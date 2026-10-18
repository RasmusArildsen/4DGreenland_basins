"""Calculate probabilities for the IDs of an already approved final basin map.

The final geometry is an input. Each merged member basin receives the final
basin ID with which it has the greatest pixel overlap. No geometry is edited.
"""

from __future__ import annotations

import shutil
from contextlib import ExitStack
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.windows import Window

from ensemble_products import _member_boundary, build_label_maps
from ensemble_runner import load_config, load_target
from reference import MINIMUM_BASIN_AREA_KM2, merged_member_files

NODATA = -9999.0


def _check_vector(vector: Path, basin_ids: list[int]) -> None:
    basins = gpd.read_file(vector)
    if "basin_id" not in basins:
        raise ValueError("Final vector needs a basin_id column")
    if sorted(int(value) for value in basins["basin_id"]) != basin_ids:
        raise ValueError("Final raster and vector basin IDs differ")


def build_final_delivery(
    config_path: str | Path,
    final_raster: str | Path,
    final_vector: str | Path,
    destination: str | Path,
) -> dict[str, Path]:
    """Write the five core products using the fixed final basin identities."""
    config = load_config(config_path)
    target = load_target(config)
    settings = config.get("postprocess", {})
    minimum = float(settings.get("minimum_basin_km2", MINIMUM_BASIN_AREA_KM2))
    rows_per_chunk = int(settings.get("delivery_chunk_rows", 32))
    if minimum <= 0 or rows_per_chunk < 1:
        raise ValueError("Minimum basin area and delivery_chunk_rows must be positive")

    final_raster = Path(final_raster)
    final_vector = Path(final_vector)
    destination = Path(destination)
    tag = f"{'surf' if target['dem_mode'] == 'surface' else target['dem_mode']}_{target['res']}m"
    members = merged_member_files(
        target["OUT"] / "merged_members", expected_members=target["n_members"]
    )
    maps, basin_ids, _, _ = build_label_maps(
        members,
        final_raster,
        minimum_basin_km2=minimum,
        chunk_rows=int(settings.get("chunk_rows", 128)),
    )
    _check_vector(final_vector, basin_ids)
    destination.mkdir(parents=True, exist_ok=False)

    paths = {
        "basins": destination / f"basins_{tag}.tif",
        "vector": destination / f"basins_{tag}.gpkg",
        "certainty": destination / f"basin_certainty_{tag}.tif",
        "boundary": destination / f"boundary_probability_{tag}.tif",
        "stack": destination / f"basin_membership_probabilities_{tag}.tif",
    }
    shutil.copyfile(final_raster, paths["basins"])
    shutil.copyfile(final_vector, paths["vector"])

    with ExitStack() as handles:
        reference = handles.enter_context(rasterio.open(final_raster))
        datasets = [handles.enter_context(rasterio.open(path)) for path in members]
        scalar_profile = reference.profile.copy()
        scalar_profile.pop("blockxsize", None)
        scalar_profile.pop("blockysize", None)
        scalar_profile.update(
            dtype="float32",
            nodata=NODATA,
            count=1,
            compress="deflate",
            tiled=reference.width >= 16 and reference.height >= 16,
        )
        # The uncompressed 100 m probability stacks can exceed classic TIFF limits.
        stack_profile = scalar_profile | {
            "count": len(basin_ids),
            "interleave": "band",
            "BIGTIFF": "YES",
        }
        certainty = handles.enter_context(rasterio.open(paths["certainty"], "w", **scalar_profile))
        boundary = handles.enter_context(rasterio.open(paths["boundary"], "w", **scalar_profile))
        stack = handles.enter_context(rasterio.open(paths["stack"], "w", **stack_profile))
        metadata = {
            "ensemble_members": str(len(members)),
            "identity": "final basin IDs 1..N",
            "harmonization": "whole merged member basin dominant area overlap with final raster",
        }
        for writer in (certainty, boundary, stack):
            writer.update_tags(**metadata)
        certainty.update_tags(
            certainty_definition="fraction supporting assigned final basin ID"
        )
        for basin_id in basin_ids:
            stack.set_band_description(basin_id, f"basin_id_{basin_id}")

        for row in range(0, reference.height, rows_per_chunk):
            height = min(rows_per_chunk, reference.height - row)
            window = Window(0, row, reference.width, height)
            halo_start = max(0, row - 1)
            halo_stop = min(reference.height, row + height + 1)
            halo = Window(0, halo_start, reference.width, halo_stop - halo_start)
            crop = slice(row - halo_start, row - halo_start + height)
            halo_domain = reference.read(1, window=halo) > 0
            labels = reference.read(1, window=window)
            domain = labels > 0
            pixels = np.flatnonzero(domain.ravel())
            counts = np.zeros((len(basin_ids), labels.size), dtype=np.uint16)
            boundaries = np.zeros(labels.shape, dtype=np.uint16)

            for dataset, mapping in zip(datasets, maps):
                raw = dataset.read(1, window=halo)
                if not np.array_equal(raw > 0, halo_domain):
                    raise ValueError(f"Member domain differs from final raster: {dataset.name}")
                if raw[halo_domain].size and int(raw[halo_domain].max()) >= mapping.size:
                    raise ValueError(f"Member basin outside its label map: {dataset.name}")
                mapped = np.zeros(raw.shape, dtype=np.int32)
                mapped[halo_domain] = mapping[raw[halo_domain]]
                core = mapped[crop].ravel()
                if np.any(core[pixels] <= 0):
                    raise ValueError(f"Unmapped member basin: {dataset.name}")
                counts[core[pixels] - 1, pixels] += 1
                boundaries += _member_boundary(mapped)[crop]

            if np.any(counts[:, pixels].sum(axis=0) != len(members)):
                raise ValueError("Member votes do not sum to the ensemble size")
            own = np.full(labels.shape, NODATA, dtype=np.float32)
            own.ravel()[pixels] = (
                counts[labels.ravel()[pixels] - 1, pixels].astype(np.float32) / len(members)
            )
            certainty.write(own, 1, window=window)
            frequency = boundaries.astype(np.float32) / len(members)
            frequency[~domain] = NODATA
            boundary.write(frequency, 1, window=window)
            for basin_id in basin_ids:
                frequency = (counts[basin_id - 1] / len(members)).reshape(labels.shape).astype(np.float32)
                frequency[~domain] = NODATA
                stack.write(frequency, basin_id, window=window)

    return paths


def extract_individual_layers(stack_path: str | Path, destination: str | Path) -> int:
    """Export each band as a separate single-basin GeoTIFF for QGIS."""
    stack_path = Path(stack_path)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    tag = stack_path.stem.removeprefix("basin_membership_probabilities_")
    with rasterio.open(stack_path) as stack:
        profile = stack.profile.copy()
        profile.update(count=1)
        for basin_id in range(1, stack.count + 1):
            if stack.descriptions[basin_id - 1] != f"basin_id_{basin_id}":
                raise ValueError(f"Unexpected band description for basin {basin_id}")
            path = destination / f"basin_membership_probability_basin_{basin_id:03d}_{tag}.tif"
            with rasterio.open(path, "w", **profile) as dst:
                dst.set_band_description(1, f"basin_id_{basin_id}")
                for _, window in stack.block_windows(basin_id):
                    dst.write(stack.read(basin_id, window=window), 1, window=window)
    return stack.count
