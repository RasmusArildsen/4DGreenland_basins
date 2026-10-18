"""Resolve and validate the fixed merged Version_0 basin references."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.windows import Window

MINIMUM_BASIN_AREA_KM2 = 250.0
MEMBER_PATTERN = "basins_hydro_ens_*.tif"


## Convert a routing mode and resolution to its config key.
def target_name(dem_mode: str, resolution_m: int) -> str:
    prefix = {"surface": "surf", "bed": "bed", "hybrid": "hybrid"}.get(dem_mode)
    if prefix is None:
        raise ValueError("dem_mode must be surface, bed, or hybrid")
    if resolution_m not in {100, 500}:
        raise ValueError("resolution_m must be 100 or 500")
    return f"{prefix}_{resolution_m}m"


def _resolve_path(raw_path: str | Path, *, config_path: Path) -> Path:
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path

    # Production task configs are written to node-local temporary folders.
    # Prefer the process working directory (normally PROJECT_DIR), then the
    # directory containing a normal local config file.
    candidates = [Path.cwd() / path, config_path.resolve().parent / path]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0].resolve()


def resolve_version0_reference(
    config: dict[str, Any],
    config_path: str | Path,
    *,
    dem_mode: str,
    resolution_m: int,
) -> Path:
    """Return the target-specific, already merged Version_0 raster."""
    name = target_name(dem_mode, resolution_m)
    references = config.get("references")
    if not isinstance(references, dict) or not references.get(name):
        raise KeyError(
            f"Missing references.{name}. Set it to the matching Version_0 "
            "raster merged to the configured minimum basin size."
        )
    path = _resolve_path(references[name], config_path=Path(config_path))
    if not path.is_file():
        raise FileNotFoundError(f"Version_0 reference not found for {name}: {path}")
    return path


## Check the reference IDs, areas, projection, and member grid before processing.
def _positive_label_counts(path: Path, *, chunk_rows: int) -> tuple[dict[int, int], dict[str, Any]]:
    counts: dict[int, int] = {}
    with rasterio.open(path) as src:
        if src.crs is None or not src.crs.is_projected:
            raise ValueError(f"Reference must use a projected CRS: {path}")
        if str(src.crs.linear_units).lower() not in {"metre", "meter"}:
            raise ValueError(f"Reference CRS units must be metres: {path}")
        for row_start in range(0, src.height, chunk_rows):
            row_stop = min(row_start + chunk_rows, src.height)
            values = src.read(
                1,
                window=Window(0, row_start, src.width, row_stop - row_start),
            )
            valid = values > 0
            if src.nodata is not None:
                valid &= values != src.nodata
            labels, label_counts = np.unique(values[valid], return_counts=True)
            for label, count in zip(labels, label_counts):
                label_int = int(label)
                counts[label_int] = counts.get(label_int, 0) + int(count)
        grid = {
            "shape": [src.height, src.width],
            "transform": list(src.transform),
            "crs": src.crs.to_string(),
            "pixel_area_km2": abs(
                src.transform.a * src.transform.e - src.transform.b * src.transform.d
            )
            / 1e6,
        }
    if not counts:
        raise ValueError(f"Reference contains no positive basin IDs: {path}")
    return counts, grid


def validate_version0_reference(
    reference_path: str | Path,
    *,
    minimum_basin_km2: float = MINIMUM_BASIN_AREA_KM2,
    member_path: str | Path | None = None,
    chunk_rows: int = 128,
) -> dict[str, Any]:
    """Fail closed unless a reference is ready for fixed-reference matching."""
    reference_path = Path(reference_path)
    minimum_basin_km2 = float(minimum_basin_km2)
    if minimum_basin_km2 <= 0:
        raise ValueError("minimum_basin_km2 must be positive")
    counts, grid = _positive_label_counts(reference_path, chunk_rows=chunk_rows)
    expected_ids = set(range(1, len(counts) + 1))
    if set(counts) != expected_ids:
        raise ValueError(
            "Version_0 reference basin IDs must be contiguous 1..N and already "
            "carry the intended north-to-clockwise numbering"
        )

    areas = {label: count * grid["pixel_area_km2"] for label, count in counts.items()}
    undersized = {label: area for label, area in areas.items() if area < minimum_basin_km2 - 1e-9}
    if undersized:
        preview = ", ".join(f"{label}={area:.2f}" for label, area in list(undersized.items())[:5])
        raise ValueError(
            f"Version_0 reference has {len(undersized)} basin(s) below "
            f"{minimum_basin_km2:g} km²: {preview}"
        )

    if member_path is not None:
        with rasterio.open(reference_path) as reference, rasterio.open(member_path) as member:
            reference_grid = (
                reference.width,
                reference.height,
                reference.transform,
                reference.crs,
            )
            member_grid = (member.width, member.height, member.transform, member.crs)
        if member_grid != reference_grid:
            raise ValueError(
                f"Version_0 reference grid does not match merged members: {member_path}"
            )

    return {
        "reference_raster": str(reference_path.resolve()),
        "reference_kind": "merged_version0",
        "basin_count": len(counts),
        "basin_ids": sorted(counts),
        "minimum_basin_area_km2": min(areas.values()),
        "requested_minimum_basin_area_km2": minimum_basin_km2,
        "domain_cell_count": sum(counts.values()),
        **grid,
    }


def merged_member_files(
    directory: str | Path,
    *,
    expected_members: int,
) -> list[Path]:
    """Return exactly members 1..N, rejecting gaps and unexpected files."""
    directory = Path(directory)
    indexed: dict[int, Path] = {}
    for path in sorted(directory.glob(MEMBER_PATTERN)):
        match = re.search(r"_(\d+)\.tif$", path.name)
        if match is None:
            continue
        index = int(match.group(1))
        if index in indexed:
            raise ValueError(f"Duplicate merged member index {index}: {path}")
        indexed[index] = path
    expected = set(range(1, expected_members + 1))
    missing = sorted(expected - set(indexed))
    unexpected = sorted(set(indexed) - expected)
    if missing or unexpected:
        raise ValueError(
            f"Merged-member set is incomplete: expected 1..{expected_members}; "
            f"missing={missing[:10]}, unexpected={unexpected[:10]}"
        )
    return [indexed[index] for index in range(1, expected_members + 1)]


def write_reference_validation(path: str | Path, report: dict[str, Any]) -> Path:
    path = Path(path)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return path
