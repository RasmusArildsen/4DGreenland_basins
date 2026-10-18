"""Merge every raw ensemble member to the configured minimum basin area."""

from __future__ import annotations

import re
from pathlib import Path

import basin_core as bc
from ensemble_runner import init_grass, load_config, load_runtime
from reference import MINIMUM_BASIN_AREA_KM2


## Find raw basin rasters and keep their member numbers.
def _raw_members(directory: Path) -> list[tuple[int, Path]]:
    found: dict[int, Path] = {}
    for path in sorted(directory.glob("basins_hydro_ens_*.tif")):
        match = re.search(r"_(\d+)\.tif$", path.name)
        if match:
            found[int(match.group(1))] = path
    return sorted(found.items())


## Merge one or more members. Existing merged files make the step resumable.
def merge_ensemble_members(
    *,
    gs,
    input_dir: Path,
    output_dir: Path,
    dem_path: str,
    resolution_m: int,
    minimum_basin_km2: float,
    do_exclaves: bool,
    max_exclave_iters: int,
    start: int | None = None,
    end: int | None = None,
) -> int:
    members = [
        (index, path)
        for index, path in _raw_members(input_dir)
        if (start is None or index >= start) and (end is None or index <= end)
    ]
    if not members:
        raise FileNotFoundError(f"No raw basin members found in {input_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    bc.prepare_merge_workspace(
        gs=gs,
        dem_path=dem_path,
        res_m=resolution_m,
    )
    for position, (index, input_path) in enumerate(members, 1):
        output_path = output_dir / f"basins_hydro_ens_{index:03d}.tif"
        if output_path.exists():
            print(f"[{position}/{len(members)}] already merged: {output_path.name}")
            continue
        print(f"[{position}/{len(members)}] merging member {index:03d}")
        bc.merge_basins(
            gs=gs,
            basins_input=str(input_path),
            member_tag=f"ens_{index:03d}",
            out_dir=str(output_dir),
            out_name=output_path.name,
            min_basin_size_km2=minimum_basin_km2,
            res_m=resolution_m,
            do_exclave_cleanup=do_exclaves,
            max_exclave_iters=max_exclave_iters,
        )
    return len(members)


## Public entry point: raw members -> merged_members/ at the configured minimum.
def merge_stage(
    config_path: str | Path,
    start: int | None = None,
    end: int | None = None,
) -> int:
    runtime = load_runtime(load_config(config_path))
    settings = runtime["cfg"].get("postprocess", {})
    minimum = float(settings.get("minimum_basin_km2", MINIMUM_BASIN_AREA_KM2))
    if minimum <= 0:
        raise ValueError("minimum_basin_km2 must be positive")

    merge_ensemble_members(
        gs=init_grass(runtime),
        input_dir=runtime["OUT"],
        output_dir=runtime["OUT"] / "merged_members",
        dem_path=runtime["DEM"],
        resolution_m=runtime["res"],
        minimum_basin_km2=minimum,
        do_exclaves=bool(settings.get("merge_do_exclaves", True)),
        max_exclave_iters=int(settings.get("merge_max_exclave_iters", 12)),
        start=start,
        end=end,
    )
    print("\nMerge stage done.")
    return 0
