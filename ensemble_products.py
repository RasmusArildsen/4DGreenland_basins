from __future__ import annotations

import inspect
from pathlib import Path

import basin_core as bc
from ensemble_merge import merge_ensemble_members
from ensemble_postprocess import build_ensemble_products
from ensemble_runner import init_grass, load_config, load_runtime


ENSEMBLE_BASIN_PATTERN = "basins_hydro_ens_*.tif"
VALID_MERGE_STRATEGIES = {"none", "member", "final"}


def _get_merge_strategy(rt) -> str:
    strategy = str(rt["cfg"].get("postprocess", {}).get("merge_strategy", "final")).lower()
    if strategy not in VALID_MERGE_STRATEGIES:
        raise ValueError("postprocess.merge_strategy must be one of: none | member | final")
    return strategy


def _final_merge_from_runtime(rt) -> Path:
    post_cfg = rt["cfg"].get("postprocess", {})
    out_most_likely = rt["OUT"] / "basins_most_likely.tif"
    out_merged = rt["OUT"] / "basins_most_likely_merged.tif"

    if not out_most_likely.exists():
        raise FileNotFoundError(
            f"Missing final product for final merge: {out_most_likely}. "
            "Run the products stage first."
        )

    gs = init_grass(rt)
    merged = bc.merge_basins(
        gs=gs,
        basins_input=str(out_most_likely),
        dem_path=str(rt["DEM"]),
        out_dir=str(rt["OUT"]),
        out_name=out_merged.name,
        min_basin_size_km2=float(post_cfg.get("merge_min_basin_km2", 500.0)),
        res_m=int(rt["res"]),
        do_exclave_cleanup=bool(post_cfg.get("merge_do_exclaves", True)),
        max_exclave_iters=int(post_cfg.get("merge_max_exclave_iters", 6)),
    )
    return Path(merged)


def products_stage(cfg_path: str | Path) -> int:
    rt = load_runtime(load_config(cfg_path))
    post_cfg = rt["cfg"].get("postprocess", {})
    merge_strategy = _get_merge_strategy(rt)
    merged_dir = rt["OUT"] / str(post_cfg.get("merge_output_subdir", "merged_members"))

    if merge_strategy == "member":
        raw_member_files = list(rt["OUT"].glob("basins_hydro_ens_*.tif")) + list(
            rt["OUT"].glob("basins_hydro_mc_*.tif")
        )
        existing_merged_files = list(merged_dir.glob(ENSEMBLE_BASIN_PATTERN))
        if raw_member_files:
            gs = init_grass(rt)
            merge_kwargs = dict(
                gs=gs,
                in_dir=rt["OUT"],
                out_dir=merged_dir,
                dem_path=rt["DEM"],
                res_m=rt["res"],
                min_basin_km2=float(post_cfg.get("merge_min_basin_km2", 500.0)),
                do_exclaves=bool(post_cfg.get("merge_do_exclaves", True)),
                max_exclave_iters=int(post_cfg.get("merge_max_exclave_iters", 6)),
            )
            if "skip_existing" in inspect.signature(merge_ensemble_members).parameters:
                merge_kwargs["skip_existing"] = True
            merge_ensemble_members(**merge_kwargs)
        elif existing_merged_files:
            print(f"✓ using existing merged members in {merged_dir}")
        else:
            raise FileNotFoundError(
                f"No raw members in {rt['OUT']} and no merged members in {merged_dir}"
            )
        source_dir = merged_dir
    else:
        source_dir = rt["OUT"]

    if not source_dir.exists():
        raise FileNotFoundError(f"Products source directory does not exist: {source_dir}")

    reference_raster = post_cfg.get("reference_raster", None) or None
    if reference_raster:
        reference_raster = Path(reference_raster)
        if not reference_raster.is_absolute():
            reference_raster = Path(cfg_path).resolve().parent / reference_raster

    build_ensemble_products(
        ensemble_dir=str(source_dir),
        basin_pattern=ENSEMBLE_BASIN_PATTERN,
        ref_index=int(post_cfg.get("reference_ref_index", 0)),
        chunk_rows=int(post_cfg.get("reference_chunk_rows", 32)),
        p_stable_pixel=float(post_cfg.get("p_stable_pixel", 0.90)),
        p_min_div=float(post_cfg.get("p_min_div", 0.00)),
        reference_mode=str(post_cfg.get("reference_mode", "iterative")),
        reference_iterations=int(post_cfg.get("reference_iterations", 2)),
        reference_raster=reference_raster,
        run_merge=False,
    )
    if merge_strategy == "final":
        merged = _final_merge_from_runtime(rt)
        print(f"✓ merged final most-likely basins: {merged}")

    print("\n✅ Products stage done.")
    return 0


def final_merge_stage(cfg_path: str | Path) -> int:
    rt = load_runtime(load_config(cfg_path))
    merged = _final_merge_from_runtime(rt)
    print(f"\n✅ Final merge stage done: {merged}")
    return 0
