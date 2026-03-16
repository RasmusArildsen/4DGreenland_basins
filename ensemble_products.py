from __future__ import annotations

from pathlib import Path

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


def products_stage(cfg_path: str | Path) -> int:
    rt = load_runtime(load_config(cfg_path))
    post_cfg = rt["cfg"].get("postprocess", {})
    merge_strategy = _get_merge_strategy(rt)
    merged_dir = rt["OUT"] / str(post_cfg.get("merge_output_subdir", "merged_members"))

    if merge_strategy == "member":
        gs = init_grass(rt)
        merge_ensemble_members(
            gs=gs,
            in_dir=rt["OUT"],
            out_dir=merged_dir,
            dem_path=rt["DEM"],
            res_m=rt["res"],
            min_basin_km2=float(post_cfg.get("merge_min_basin_km2", 500.0)),
            do_exclaves=bool(post_cfg.get("merge_do_exclaves", True)),
            max_exclave_iters=int(post_cfg.get("merge_max_exclave_iters", 6)),
        )
        source_dir = merged_dir
        run_merge = False
    elif merge_strategy == "final":
        source_dir = rt["OUT"]
        run_merge = True
    else:
        source_dir = rt["OUT"]
        run_merge = False

    if not source_dir.exists():
        raise FileNotFoundError(f"Products source directory does not exist: {source_dir}")

    build_ensemble_products(
        ensemble_dir=str(source_dir),
        basin_pattern=ENSEMBLE_BASIN_PATTERN,
        p_stable_pixel=float(post_cfg.get("p_stable_pixel", 0.90)),
        p_min_div=float(post_cfg.get("p_min_div", 0.00)),
        run_merge=run_merge,
        merge_dem=rt["DEM"] if run_merge else None,
        merge_res_m=rt["res"],
        merge_min_basin_km2=float(post_cfg.get("merge_min_basin_km2", 500.0)),
        merge_do_exclaves=bool(post_cfg.get("merge_do_exclaves", True)),
        merge_max_exclave_iters=int(post_cfg.get("merge_max_exclave_iters", 6)),
    )

    print("\n✅ Products stage done.")
    return 0
