from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable

import basin_core as bc
from ensemble_runner import load_config, load_runtime


ENSEMBLE_BASIN_PATTERNS = ("basins_hydro_ens_*.tif", "basins_hydro_mc_*.tif")


def _member_index_from_name(path: Path) -> int | None:
    m = re.search(r"(\d+)(?=\.[^.]+$)", path.name)
    return int(m.group(1)) if m else None


def _filter_member_files(basin_files: list[Path], start: int | None, end: int | None) -> list[Path]:
    if start is None and end is None:
        return basin_files

    lo = 1 if start is None else int(start)
    hi = len(basin_files) if end is None else int(end)
    if hi < lo:
        raise ValueError(f"Invalid member range: {lo}..{hi}")

    selected: list[Path] = []
    for pos, fpath in enumerate(basin_files, start=1):
        member_idx = _member_index_from_name(fpath) or pos
        if lo <= member_idx <= hi:
            selected.append(fpath)
    return selected


def _find_member_files(in_dir: Path) -> list[Path]:
    by_member: dict[int, Path] = {}
    for pattern in ENSEMBLE_BASIN_PATTERNS:
        for fpath in sorted(in_dir.glob(pattern)):
            member_idx = _member_index_from_name(fpath)
            if member_idx is None:
                continue
            existing = by_member.get(member_idx)
            if existing is None or "_ens_" in fpath.name:
                by_member[member_idx] = fpath
    return [by_member[idx] for idx in sorted(by_member)]


def _task_suffix(start: int | None, end: int | None) -> str:
    parts: list[str] = []
    for env_name in ("SLURM_ARRAY_JOB_ID", "SLURM_ARRAY_TASK_ID", "SLURM_JOB_ID"):
        val = os.environ.get(env_name)
        if val:
            parts.append(val)
    if start is not None or end is not None:
        parts.append(f"r{start or 1}_{end or 'all'}")
    return "_".join(parts) if parts else "local"


def _resolve_gisdbase(rt: dict) -> str | Path | None:
    return (
        os.environ.get("GRASS_GISDBASE")
        or rt.get("GRASS_GISDBASE")
        or rt.get("GISDBASE")
        or rt.get("grass_gisdbase")
    )


def _resolve_location(rt: dict) -> str:
    return str(
        os.environ.get("GRASS_LOCATION")
        or rt.get("GRASS_LOCATION")
        or rt.get("grass_location")
        or "dem_loc"
    )


def _resolve_mapset(rt: dict, *, res_m: int, start: int | None, end: int | None) -> str:
    base = str(
        os.environ.get("GRASS_MAPSET_BASE")
        or rt.get("GRASS_MAPSET_BASE")
        or rt.get("grass_mapset_base")
        or f"MC_WORK_{res_m}m"
    )
    suffix = _task_suffix(start, end)
    return f"{base}_{suffix}"


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
    start: int | None = None,
    end: int | None = None,
) -> int:
    basin_files = _find_member_files(in_dir)
    if not basin_files:
        patterns = ", ".join(ENSEMBLE_BASIN_PATTERNS)
        raise FileNotFoundError(f"No files matching {patterns} in {in_dir}")

    basin_files = _filter_member_files(basin_files, start, end)
    if not basin_files:
        raise FileNotFoundError(
            f"No ensemble member rasters matched requested range {start}..{end} in {in_dir}"
        )

    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n=== Merging {len(basin_files)} ensemble members ===")
    bc.prepare_merge_workspace(gs=gs, dem_path=str(dem_path), res_m=int(res_m), dem_map="dem")

    force_merge = os.environ.get("FORCE_MERGE", "").lower() in {"1", "true", "yes", "y"}
    n_done = 0
    for idx, fpath in enumerate(basin_files, start=1):
        member_idx = _member_index_from_name(fpath) or idx
        out_path = out_dir / f"basins_hydro_ens_{member_idx:03d}.tif"
        if out_path.exists() and not force_merge:
            print(f"[{idx}/{len(basin_files)} | member {member_idx:03d}] ✓ already merged: {out_path.name}")
            n_done += 1
            continue

        action = "re-merging" if out_path.exists() else "merging"
        print(f"[{idx}/{len(basin_files)} | member {member_idx:03d}] {action}: {fpath.name}")
        bc.merge_basins(
            gs=gs,
            basins_input=str(fpath),
            dem_path=None,
            dem_map="dem",
            member_tag=f"ens_{member_idx:03d}",
            out_dir=str(out_dir),
            out_name=out_path.name,
            min_basin_size_km2=float(min_basin_km2),
            res_m=int(res_m),
            do_exclave_cleanup=bool(do_exclaves),
            max_exclave_iters=int(max_exclave_iters),
        )
        n_done += 1

    return n_done


def merge_stage(cfg_path: str | Path, start: int | None = None, end: int | None = None) -> int:
    rt = load_runtime(load_config(cfg_path))
    post_cfg = rt["cfg"].get("postprocess", {})
    out_dir = rt["OUT"] / str(post_cfg.get("merge_output_subdir", "merged_members"))

    bc.setup_grass_env()
    mapset_name = _resolve_mapset(rt, res_m=int(rt["res"]), start=start, end=end)
    bc.start_grass_from_raster(
        str(rt["DEM"]),
        location=_resolve_location(rt),
        mapset=mapset_name,
        gisdbase=_resolve_gisdbase(rt),
    )
    gs = bc.gs

    merge_ensemble_members(
        gs=gs,
        in_dir=rt["OUT"],
        out_dir=out_dir,
        dem_path=rt["DEM"],
        res_m=rt["res"],
        min_basin_km2=float(post_cfg.get("merge_min_basin_km2", 500.0)),
        do_exclaves=bool(post_cfg.get("merge_do_exclaves", True)),
        max_exclave_iters=int(post_cfg.get("merge_max_exclave_iters", 6)),
        start=start,
        end=end,
    )

    print("\n✅ Merge stage done.")
    return 0
