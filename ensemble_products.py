"""Create Version_0-based intermediate products from merged ensemble members."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import shapes
from rasterio.windows import Window
from shapely.geometry import shape

from ensemble_runner import load_config, load_target
from reference import (
    MINIMUM_BASIN_AREA_KM2,
    merged_member_files,
    resolve_version0_reference,
    validate_version0_reference,
    write_reference_validation,
)


## Match every member basin to the Version_0 basin with the largest overlap.
def build_label_maps(
    members: list[Path],
    reference: Path,
    *,
    minimum_basin_km2: float = MINIMUM_BASIN_AREA_KM2,
    chunk_rows: int = 128,
) -> tuple[list[np.ndarray], list[int], dict, dict]:
    report = validate_version0_reference(
        reference,
        minimum_basin_km2=minimum_basin_km2,
        member_path=members[0],
        chunk_rows=chunk_rows,
    )
    basin_ids = report["basin_ids"]

    with rasterio.open(reference) as ref:
        profile = ref.profile.copy()
        grid = (ref.width, ref.height, ref.transform, ref.crs)
        pixel_area_km2 = report["pixel_area_km2"]
        label_maps: list[np.ndarray] = []

        for number, member_path in enumerate(members, 1):
            print(f"Mapping member {number}/{len(members)}: {member_path.name}")
            overlaps: dict[tuple[int, int], int] = defaultdict(int)
            member_counts: dict[int, int] = defaultdict(int)

            with rasterio.open(member_path) as member:
                if (member.width, member.height, member.transform, member.crs) != grid:
                    raise ValueError(f"Member grid differs from reference: {member_path}")

                for row in range(0, ref.height, chunk_rows):
                    height = min(chunk_rows, ref.height - row)
                    window = Window(0, row, ref.width, height)
                    ref_values = ref.read(1, window=window)
                    member_values = member.read(1, window=window)

                    labels, counts = np.unique(
                        member_values[member_values > 0], return_counts=True
                    )
                    for label, count in zip(labels, counts):
                        member_counts[int(label)] += int(count)

                    valid = (ref_values > 0) & (member_values > 0)
                    pairs, counts = np.unique(
                        np.column_stack((member_values[valid], ref_values[valid])),
                        axis=0,
                        return_counts=True,
                    )
                    for pair, count in zip(pairs, counts):
                        overlaps[(int(pair[0]), int(pair[1]))] += int(count)

            undersized = {
                label: count * pixel_area_km2
                for label, count in member_counts.items()
                if count * pixel_area_km2 < minimum_basin_km2 - 1e-9
            }
            if undersized:
                raise ValueError(
                    f"{member_path.name} is not merged to "
                    f"{minimum_basin_km2:g} km²"
                )

            max_member = max(member_counts, default=0)
            label_map = np.zeros(max_member + 1, dtype=np.int32)
            best_count = np.zeros(max_member + 1, dtype=np.int64)
            for (member_label, ref_label), count in sorted(overlaps.items()):
                if count > best_count[member_label]:
                    best_count[member_label] = count
                    label_map[member_label] = ref_label
            missing = [label for label in member_counts if best_count[label] == 0]
            if missing:
                raise ValueError(
                    f"Member basins have no overlap with Version_0: "
                    f"{member_path.name}, labels={missing[:10]}"
                )
            label_maps.append(label_map)

    profile.pop("blockxsize", None)
    profile.pop("blockysize", None)
    profile.update(
        count=1,
        compress="deflate",
        tiled=profile["width"] >= 16 and profile["height"] >= 16,
    )
    return label_maps, basin_ids, profile, report


def _mapped_chunk(
    member: rasterio.DatasetReader, label_map: np.ndarray, window: Window
) -> np.ndarray:
    values = member.read(1, window=window)
    mapped = np.zeros(values.shape, dtype=np.int32)
    valid = (values >= 0) & (values < label_map.size)
    mapped[valid] = label_map[values[valid].astype(np.int64)]
    return mapped


def _majority(stack: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return the most common positive ID and its member fraction per pixel."""
    n_members, n_rows, n_cols = stack.shape
    flat = stack.reshape(n_members, -1)
    winner = np.zeros(flat.shape[1], dtype=np.int32)
    winner_count = np.zeros(flat.shape[1], dtype=np.int16)
    for basin_id in np.unique(flat):
        if basin_id <= 0:
            continue
        count = np.count_nonzero(flat == basin_id, axis=0)
        better = count > winner_count
        winner[better] = basin_id
        winner_count[better] = count[better]
    certainty = winner_count.astype(np.float32) / n_members
    return winner.reshape(n_rows, n_cols), certainty.reshape(n_rows, n_cols)


def _member_boundary(values: np.ndarray) -> np.ndarray:
    """Mark both sides of each four-neighbour basin boundary."""
    boundary = np.zeros(values.shape, dtype=bool)
    horizontal = (values[:, 1:] != values[:, :-1]) & (values[:, 1:] > 0) & (values[:, :-1] > 0)
    vertical = (values[1:, :] != values[:-1, :]) & (values[1:, :] > 0) & (values[:-1, :] > 0)
    boundary[:, 1:] |= horizontal
    boundary[:, :-1] |= horizontal
    boundary[1:, :] |= vertical
    boundary[:-1, :] |= vertical
    return boundary


## Write the most-likely basin, certainty, and divide rasters.
def write_consensus_products(
    members: list[Path],
    label_maps: list[np.ndarray],
    reference: Path,
    output_dir: Path,
    profile: dict,
    *,
    chunk_rows: int,
    stable_divide_probability: float,
) -> dict[str, Path]:
    paths = {
        "most_likely": output_dir / "basins_most_likely.tif",
        "certainty": output_dir / "basins_certainty.tif",
        "boundary_probability": output_dir / "basin_boundary_probability.tif",
        "stable_divides": output_dir / "basin_stable_divides.tif",
        "uncertain_divides": output_dir / "basin_uncertain_divides.tif",
    }
    int_profile = profile | {"dtype": "int32", "nodata": 0}
    float_profile = profile | {"dtype": "float32", "nodata": -9999.0}
    byte_profile = profile | {"dtype": "uint8", "nodata": 0}

    datasets = [rasterio.open(path) for path in members]
    writers = {
        "most_likely": rasterio.open(paths["most_likely"], "w", **int_profile),
        "certainty": rasterio.open(paths["certainty"], "w", **float_profile),
        "boundary_probability": rasterio.open(
            paths["boundary_probability"], "w", **float_profile
        ),
        "stable_divides": rasterio.open(paths["stable_divides"], "w", **byte_profile),
        "uncertain_divides": rasterio.open(
            paths["uncertain_divides"], "w", **byte_profile
        ),
    }
    try:
        with rasterio.open(reference) as ref:
            for row in range(0, ref.height, chunk_rows):
                height = min(chunk_rows, ref.height - row)
                window = Window(0, row, ref.width, height)
                halo_row = max(0, row - 1)
                halo_end = min(ref.height, row + height + 1)
                halo = Window(0, halo_row, ref.width, halo_end - halo_row)
                crop = slice(row - halo_row, row - halo_row + height)

                stack = np.stack(
                    [_mapped_chunk(ds, mapping, window) for ds, mapping in zip(datasets, label_maps)]
                )
                majority, certainty = _majority(stack)
                domain = ref.read(1, window=window) > 0
                majority[~domain] = 0
                certainty[~domain] = -9999.0
                boundary_count = np.zeros(domain.shape, dtype=np.uint16)
                for ds, mapping in zip(datasets, label_maps):
                    boundary_count += _member_boundary(_mapped_chunk(ds, mapping, halo))[crop]
                probability = boundary_count.astype(np.float32) / len(datasets)
                probability[~domain] = -9999.0

                writers["most_likely"].write(majority, 1, window=window)
                writers["certainty"].write(certainty, 1, window=window)
                writers["boundary_probability"].write(probability, 1, window=window)
                writers["stable_divides"].write(
                    (probability >= stable_divide_probability).astype(np.uint8), 1, window=window
                )
                writers["uncertain_divides"].write(
                    (
                        (probability > 0)
                        & (probability < stable_divide_probability)
                    ).astype(np.uint8),
                    1,
                    window=window,
                )
    finally:
        for dataset in datasets:
            dataset.close()
        for writer in writers.values():
            writer.close()
    return paths


## Write one probability layer per Version_0 basin for direct use in QGIS.
def write_probability_stack(
    members: list[Path],
    label_maps: list[np.ndarray],
    basin_ids: list[int],
    reference: Path,
    output_dir: Path,
    profile: dict,
    *,
    chunk_rows: int,
) -> Path:
    path = output_dir / "basin_membership_probabilities.tif"
    stack_profile = profile | {
        "count": len(basin_ids),
        "dtype": "float32",
        "nodata": -9999.0,
        "interleave": "band",
    }
    datasets = [rasterio.open(path) for path in members]
    try:
        with rasterio.open(reference) as ref, rasterio.open(path, "w", **stack_profile) as dst:
            for band, basin_id in enumerate(basin_ids, 1):
                dst.set_band_description(band, f"basin_{basin_id}_probability")
            for row in range(0, ref.height, chunk_rows):
                height = min(chunk_rows, ref.height - row)
                window = Window(0, row, ref.width, height)
                domain = ref.read(1, window=window) > 0
                n_pixels = height * ref.width
                pixel_index = np.arange(n_pixels)
                counts = np.zeros((len(basin_ids), n_pixels), dtype=np.uint16)
                for dataset, mapping in zip(datasets, label_maps):
                    mapped = _mapped_chunk(dataset, mapping, window).ravel()
                    valid = mapped > 0
                    np.add.at(counts, (mapped[valid] - 1, pixel_index[valid]), 1)
                probabilities = (
                    counts.reshape(len(basin_ids), height, ref.width).astype(np.float32)
                    / len(datasets)
                )
                probabilities[:, ~domain] = -9999.0
                dst.write(probabilities, window=window)
    finally:
        for dataset in datasets:
            dataset.close()

    with (output_dir / "basin_membership_probability_bands.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(["band", "basin_id"])
        writer.writerows(enumerate(basin_ids, 1))
    return path


## Convert the most-likely raster to a basin vector and dissolve by basin ID.
def write_most_likely_vector(raster_path: Path, output_dir: Path) -> Path:
    path = output_dir / "basins_most_likely.gpkg"
    with rasterio.open(raster_path) as src:
        records = [
            {"basin_id": int(value), "geometry": shape(geometry)}
            for geometry, value in shapes(rasterio.band(src, 1), transform=src.transform)
            if value > 0
        ]
        basins = gpd.GeoDataFrame(records, crs=src.crs).dissolve(by="basin_id").reset_index()
    basins.to_file(path, layer="basins_most_likely", driver="GPKG")
    return path


## Intermediate products: merged members matched to the fixed Version_0 reference.
def products_stage(config_path: str | Path) -> int:
    config_path = Path(config_path)
    config = load_config(config_path)
    runtime = load_target(config)
    settings = config.get("postprocess", {})
    minimum = float(settings.get("minimum_basin_km2", MINIMUM_BASIN_AREA_KM2))
    if minimum <= 0:
        raise ValueError("minimum_basin_km2 must be positive")

    output_dir = runtime["OUT"]
    output_dir.mkdir(parents=True, exist_ok=True)
    merged_dir = output_dir / "merged_members"
    members = merged_member_files(merged_dir, expected_members=runtime["n_members"])
    reference = resolve_version0_reference(
        config,
        config_path,
        dem_mode=runtime["dem_mode"],
        resolution_m=runtime["res"],
    )
    chunk_rows = int(settings.get("chunk_rows", 128))
    label_maps, basin_ids, profile, report = build_label_maps(
        members,
        reference,
        minimum_basin_km2=minimum,
        chunk_rows=chunk_rows,
    )
    write_reference_validation(output_dir / "reference_validation.json", report)

    products = write_consensus_products(
        members,
        label_maps,
        reference,
        output_dir,
        profile,
        chunk_rows=chunk_rows,
        stable_divide_probability=float(settings.get("stable_divide_probability", 0.80)),
    )
    write_probability_stack(
        members,
        label_maps,
        basin_ids,
        reference,
        output_dir,
        profile,
        chunk_rows=int(settings.get("probability_chunk_rows", 4)),
    )
    write_most_likely_vector(products["most_likely"], output_dir)

    provenance = {
        "method": "merge_each_member_then_match_once_to_fixed_merged_version0",
        "minimum_basin_area_km2": minimum,
        "reference": str(reference),
        "member_count": len(members),
    }
    (output_dir / "product_provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\nProducts written to {output_dir}")
    return 0
