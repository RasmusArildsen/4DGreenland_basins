from __future__ import annotations

from pathlib import Path

import basin_core as bc
from ensemble_runner import init_grass, load_config, load_runtime


ENSEMBLE_BASIN_PATTERN = "basins_hydro_ens_*.tif"


def merge_ensemble_members(
    *,
    gs,
    in_dir: Path,
    out_dir: Path,
    dem_path: str,
    res_m: int,
    min_basin_km2: float,
    do_exclaves: bool,
    max_exclave_iters: int,
) -> int:
    basin_files = sorted(in_dir.glob(ENSEMBLE_BASIN_PATTERN))
    if not basin_files:
        raise FileNotFoundError(f"No files matching {ENSEMBLE_BASIN_PATTERN} in {in_dir}")

    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n=== Merging {len(basin_files)} ensemble members ===")
    n_done = 0
    for idx, fpath in enumerate(basin_files, start=1):
        out_path = out_dir / fpath.name
        if out_path.exists():
            print(f"[{idx}/{len(basin_files)}] ✓ already merged: {out_path.name}")
            n_done += 1
            continue

        print(f"[{idx}/{len(basin_files)}] merging: {fpath.name}")
        bc.merge_basins(
            gs=gs,
            basins_input=str(fpath),
            dem_path=str(dem_path),
            out_dir=str(out_dir),
            out_name=out_path.name,
            min_basin_size_km2=float(min_basin_km2),
            res_m=int(res_m),
            do_exclave_cleanup=bool(do_exclaves),
            max_exclave_iters=int(max_exclave_iters),
        )
        n_done += 1

    return n_done


def merge_stage(cfg_path: str | Path) -> int:
    rt = load_runtime(load_config(cfg_path))
    gs = init_grass(rt)
    post_cfg = rt["cfg"].get("postprocess", {})
    out_dir = rt["OUT"] / str(post_cfg.get("merge_output_subdir", "merged_members"))

    merge_ensemble_members(
        gs=gs,
        in_dir=rt["OUT"],
        out_dir=out_dir,
        dem_path=rt["DEM"],
        res_m=rt["res"],
        min_basin_km2=float(post_cfg.get("merge_min_basin_km2", 500.0)),
        do_exclaves=bool(post_cfg.get("merge_do_exclaves", True)),
        max_exclave_iters=int(post_cfg.get("merge_max_exclave_iters", 6)),
    )

    print("\n✅ Merge stage done.")
    return 0
