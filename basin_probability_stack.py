"""Build a per-basin, per-pixel membership probability stack.

Each output band corresponds to one basin ID in a reference basin raster. The
value of a pixel is the fraction of ensemble members in which that pixel was
assigned to the reference basin after label alignment.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window

from ensemble_postprocess import _build_label_maps

OUTPUT_NODATA = -9999.0


def _validate_member_grids(member_paths: list[Path], reference_path: Path) -> None:
    with rasterio.open(reference_path) as reference:
        expected = (reference.width, reference.height, reference.transform, reference.crs)
    for member_path in member_paths:
        with rasterio.open(member_path) as member:
            actual = (member.width, member.height, member.transform, member.crs)
        if actual != expected:
            raise ValueError(f"Ensemble member grid differs from the reference: {member_path}")


def _positive_labels(path: Path, *, chunk_rows: int) -> np.ndarray:
    labels: set[int] = set()
    with rasterio.open(path) as src:
        for row_start in range(0, src.height, chunk_rows):
            row_stop = min(row_start + chunk_rows, src.height)
            window = Window(0, row_start, src.width, row_stop - row_start)
            values = np.unique(src.read(1, window=window))
            labels.update(int(value) for value in values if value > 0)
    if not labels:
        raise ValueError(f"Reference raster contains no positive basin IDs: {path}")
    return np.asarray(sorted(labels), dtype=np.int32)


def label_count_cube(stack_2d: np.ndarray, basin_ids: np.ndarray) -> np.ndarray:
    """Count every reference label at every pixel without a basin-by-basin scan.

    Parameters
    ----------
    stack_2d
        Integer array shaped ``(members, pixels)`` with zero as background.
    basin_ids
        Sorted positive basin IDs defining the output-band order.

    Returns
    -------
    ndarray
        Unsigned integer counts shaped ``(basins, pixels)``.
    """
    if stack_2d.ndim != 2:
        raise ValueError("stack_2d must have shape (members, pixels)")
    if len(basin_ids) == 0:
        raise ValueError("basin_ids must not be empty")

    n_members, n_pixels = stack_2d.shape
    count_dtype = np.uint16 if n_members <= np.iinfo(np.uint16).max else np.uint32

    # Transposing after the sort makes all member labels for one pixel
    # contiguous. Run lengths can then be found in one flattened pass.
    labels_by_pixel = np.ascontiguousarray(np.sort(stack_2d, axis=0).T)
    flat = labels_by_pixel.reshape(-1)
    changes = np.empty(flat.size, dtype=bool)
    changes[0] = True
    changes[1:] = flat[1:] != flat[:-1]
    changes[::n_members] = True

    starts = np.flatnonzero(changes)
    lengths = np.diff(np.append(starts, flat.size))
    labels = flat[starts]
    pixels = starts // n_members

    positive = labels > 0
    labels = labels[positive]
    lengths = lengths[positive]
    pixels = pixels[positive]

    band_indices = np.searchsorted(basin_ids, labels)
    in_range = band_indices < len(basin_ids)
    matched = np.zeros_like(in_range)
    matched[in_range] = basin_ids[band_indices[in_range]] == labels[in_range]

    counts = np.zeros((len(basin_ids), n_pixels), dtype=count_dtype)
    counts[band_indices[matched], pixels[matched]] = lengths[matched].astype(count_dtype)
    return counts


def _write_band_index(path: Path, basin_ids: np.ndarray) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["band", "basin_id"])
        writer.writeheader()
        writer.writerows(
            {"band": band_number, "basin_id": int(basin_id)}
            for band_number, basin_id in enumerate(basin_ids, start=1)
        )


def build_basin_probability_stack(
    ensemble_dir: str | Path,
    reference_raster: str | Path,
    *,
    basin_pattern: str = "basins_hydro_ens_*.tif",
    output_path: str | Path | None = None,
    mapping_chunk_rows: int = 32,
    probability_chunk_rows: int = 4,
) -> dict[str, object]:
    """Write a multiband probability GeoTIFF aligned to ``reference_raster``."""
    ensemble_dir = Path(ensemble_dir)
    reference_raster = Path(reference_raster)
    if not reference_raster.exists():
        raise FileNotFoundError(f"Reference basin raster not found: {reference_raster}")
    if mapping_chunk_rows < 1 or probability_chunk_rows < 1:
        raise ValueError("chunk-row settings must be positive")

    basin_files = sorted(ensemble_dir.glob(basin_pattern))
    if not basin_files:
        raise FileNotFoundError(f"No files matching {basin_pattern!r} in {ensemble_dir}")
    _validate_member_grids(basin_files, reference_raster)

    if output_path is None:
        output_path = ensemble_dir / "basin_membership_probabilities.tif"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    maps, grid = _build_label_maps(
        basin_files=basin_files,
        ref_path=reference_raster,
        ref_index=None,
        chunk_rows=mapping_chunk_rows,
    )
    basin_ids = _positive_labels(reference_raster, chunk_rows=mapping_chunk_rows)
    n_members = len(basin_files)
    width = int(grid["width"])
    height = int(grid["height"])
    nodata_ref = grid["nodata_ref"]

    uncompressed_gib = len(basin_ids) * width * height * np.dtype("float32").itemsize / 1024**3
    working_gib = (
        (2 * n_members * probability_chunk_rows * width * np.dtype("int32").itemsize)
        + (len(basin_ids) * probability_chunk_rows * width * np.dtype("uint16").itemsize)
    ) / 1024**3
    print(f"Probability bands: {len(basin_ids)}")
    print(f"Ensemble members: {n_members}")
    print(f"Uncompressed stack size: {uncompressed_gib:.2f} GiB")
    print(f"Approximate working memory per chunk: {working_gib:.2f} GiB")

    profile = grid["meta"].copy()
    profile.update(
        driver="GTiff",
        count=len(basin_ids),
        dtype="float32",
        nodata=OUTPUT_NODATA,
        compress="DEFLATE",
        predictor=3,
        interleave="band",
        BIGTIFF="YES",
        SPARSE_OK="TRUE",
    )

    with rasterio.open(reference_raster) as src_ref, rasterio.open(output_path, "w", **profile) as dst:
        dst.update_tags(
            title="Per-basin pixel membership probabilities",
            reference_raster=reference_raster.name,
            ensemble_members=str(n_members),
            probability_definition="members_assigned_to_basin / all_members",
            valid_min="0.0",
            valid_max="1.0",
            units="probability",
        )
        for band_number, basin_id in enumerate(basin_ids, start=1):
            dst.set_band_description(band_number, f"basin_id_{int(basin_id)}")
            dst.update_tags(
                band_number,
                basin_id=str(int(basin_id)),
                long_name=f"Probability of membership in basin {int(basin_id)}",
                units="probability",
            )

        for row_start in range(0, height, probability_chunk_rows):
            row_stop = min(row_start + probability_chunk_rows, height)
            nrows = row_stop - row_start
            window = Window(0, row_start, width, nrows)
            print(f"[Probability stack] Rows {row_start}–{row_stop - 1}")

            ref_chunk = src_ref.read(1, window=window)
            domain = (ref_chunk > 0) & (ref_chunk != nodata_ref)
            label_stack = np.zeros((n_members, nrows, width), dtype=np.int32)

            for member_index, member_path in enumerate(basin_files):
                map_for_member = maps[member_index]
                with rasterio.open(member_path) as src_member:
                    nodata_member = src_member.nodata if src_member.nodata is not None else 0
                    labels = src_member.read(1, window=window).astype(np.int32)
                labels[labels == nodata_member] = 0
                labels[labels < 0] = 0
                labels[labels >= len(map_for_member)] = 0
                aligned = map_for_member[labels]
                aligned[~domain] = 0
                label_stack[member_index] = aligned

            counts = label_count_cube(label_stack.reshape(n_members, -1), basin_ids)
            for band_index in range(len(basin_ids)):
                probability = counts[band_index].reshape(nrows, width).astype(np.float32)
                probability /= float(n_members)
                probability[~domain] = OUTPUT_NODATA
                dst.write(probability, band_index + 1, window=window)

    band_index_path = output_path.with_suffix(".bands.csv")
    _write_band_index(band_index_path, basin_ids)

    metadata = {
        "probability_stack": str(output_path),
        "band_index": str(band_index_path),
        "reference_raster": str(reference_raster),
        "ensemble_members": n_members,
        "basin_count": len(basin_ids),
        "basin_ids": [int(value) for value in basin_ids],
        "nodata": OUTPUT_NODATA,
        "uncompressed_size_gib": uncompressed_gib,
    }
    metadata_path = output_path.with_suffix(".json")
    with metadata_path.open("w", encoding="utf-8") as stream:
        json.dump(metadata, stream, indent=2)
        stream.write("\n")

    print(f"✓ wrote probability stack: {output_path}")
    print(f"✓ wrote band index: {band_index_path}")
    return metadata


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build one per-pixel probability layer for every reference drainage basin."
    )
    parser.add_argument("ensemble_dir", type=Path, help="Directory containing ensemble basin rasters")
    parser.add_argument("reference_raster", type=Path, help="Final basin raster defining basin IDs")
    parser.add_argument("--pattern", default="basins_hydro_ens_*.tif")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--mapping-chunk-rows", type=int, default=32)
    parser.add_argument("--probability-chunk-rows", type=int, default=4)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    build_basin_probability_stack(
        args.ensemble_dir,
        args.reference_raster,
        basin_pattern=args.pattern,
        output_path=args.output,
        mapping_chunk_rows=args.mapping_chunk_rows,
        probability_chunk_rows=args.probability_chunk_rows,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
