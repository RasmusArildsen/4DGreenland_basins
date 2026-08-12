from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window


# ============================================================
# Core helpers
# ============================================================
def majority_label_and_cert(stack_2d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """
    Parameters
    ----------
    stack_2d : ndarray
        Shape (N_members, M), integer labels, with 0 meaning background.

    Returns
    -------
    majority_label_flat, certainty_flat
        Per-pixel majority label and fraction of members supporting it.
    """
    n_members, n_pixels = stack_2d.shape
    sorted_stack = np.sort(stack_2d, axis=0)

    change = np.vstack(
        [np.ones((1, n_pixels), dtype=bool), sorted_stack[1:, :] != sorted_stack[:-1, :]]
    )
    groups = np.cumsum(change, axis=0).astype(np.int32)

    offsets = (np.arange(n_pixels, dtype=np.int64) * (n_members + 1))[None, :]
    flat_group = (groups.astype(np.int64) + offsets).ravel()

    counts = np.bincount(flat_group, minlength=int(flat_group.max()) + 1)
    group_size = counts[flat_group].reshape(n_members, n_pixels).astype(np.int16)
    group_size[sorted_stack <= 0] = 0

    max_idx = group_size.argmax(axis=0)
    majority = sorted_stack[max_idx, np.arange(n_pixels)].astype(np.int32)

    max_counts = group_size.max(axis=0).astype(np.float32)
    certainty = max_counts / float(n_members)
    return majority, certainty


def boundary_mask_from_labels(arr: np.ndarray, valid_mask: np.ndarray) -> np.ndarray:
    """
    Raw boundary mask for one member:
    a pixel is boundary if it differs from at least one valid 4-neighbour.
    """
    boundaries = np.zeros(arr.shape, dtype=bool)

    diff_ud = (arr[1:, :] != arr[:-1, :]) & valid_mask[1:, :] & valid_mask[:-1, :]
    boundaries[1:, :] |= diff_ud
    boundaries[:-1, :] |= diff_ud

    diff_lr = (arr[:, 1:] != arr[:, :-1]) & valid_mask[:, 1:] & valid_mask[:, :-1]
    boundaries[:, 1:] |= diff_lr
    boundaries[:, :-1] |= diff_lr

    boundaries &= valid_mask
    return boundaries


def _scan_max_label(path: Path, *, chunk_rows: int) -> int:
    max_label = 0
    with rasterio.open(path) as src:
        width, height = src.width, src.height
        for row_start in range(0, height, chunk_rows):
            row_stop = min(row_start + chunk_rows, height)
            window = Window(0, row_start, width, row_stop - row_start)
            chunk = src.read(1, window=window)
            if chunk.size:
                max_label = max(max_label, int(chunk.max()))
    return max_label


# ============================================================
# Build mapping from each member -> external reference labels
# ============================================================
def build_label_maps(
    *,
    basin_files: list[Path],
    ref_path: Path,
    chunk_rows: int,
) -> tuple[list[np.ndarray], dict]:
    with rasterio.open(ref_path) as src_ref:
        meta = src_ref.meta.copy()
        width, height = src_ref.width, src_ref.height
        transform = src_ref.transform
        crs = src_ref.crs
        nodata_ref = src_ref.nodata if src_ref.nodata is not None else 0

    max_ref_label = _scan_max_label(ref_path, chunk_rows=chunk_rows)
    print(f"Reference: {ref_path}")
    print(f"Max reference label: {max_ref_label}")

    maps: list[np.ndarray] = []

    with rasterio.open(ref_path) as src_ref_global:
        for idx, fpath in enumerate(basin_files, start=1):
            print(f"[{idx}/{len(basin_files)}] building map for {fpath.name}")

            with rasterio.open(fpath) as src_run:
                if (
                    src_run.width != width
                    or src_run.height != height
                    or src_run.transform != transform
                    or src_run.crs != crs
                ):
                    raise ValueError(f"Grid mismatch: {fpath}")

                nodata_run = src_run.nodata if src_run.nodata is not None else 0
                mapping_counts = defaultdict(int)
                max_run_label = 0

                for row_start in range(0, height, chunk_rows):
                    row_stop = min(row_start + chunk_rows, height)
                    window = Window(0, row_start, width, row_stop - row_start)

                    ref_chunk = src_ref_global.read(1, window=window)
                    run_chunk = src_run.read(1, window=window)

                    max_run_label = max(max_run_label, int(run_chunk.max()))

                    domain = (ref_chunk != nodata_ref) & (ref_chunk > 0)
                    valid_run = (run_chunk != nodata_run) & (run_chunk > 0)
                    mask = domain & valid_run
                    if not np.any(mask):
                        continue

                    run_vals = run_chunk[mask].astype(np.int64)
                    ref_vals = ref_chunk[mask].astype(np.int64)

                    idx_comb = run_vals * (max_ref_label + 1) + ref_vals
                    uniq, counts = np.unique(idx_comb, return_counts=True)
                    for u, c in zip(uniq, counts):
                        mapping_counts[int(u)] += int(c)

            best_ref = np.zeros(max_run_label + 1, dtype=np.int32)
            best_count = np.zeros(max_run_label + 1, dtype=np.int64)

            for comb, count in mapping_counts.items():
                run_label = comb // (max_ref_label + 1)
                ref_label = comb % (max_ref_label + 1)
                if run_label == 0 or ref_label == 0:
                    continue
                if count > best_count[run_label]:
                    best_count[run_label] = count
                    best_ref[run_label] = ref_label

            maps.append(best_ref)

    return maps, {
        "meta": meta,
        "width": width,
        "height": height,
        "nodata_ref": nodata_ref,
    }


# ============================================================
# Write outputs
# ============================================================
def write_products(
    *,
    basin_files: list[Path],
    ref_path: Path,
    maps: list[np.ndarray],
    meta: dict,
    chunk_rows: int,
    out_most_likely: Path,
    out_certainty: Path,
    out_boundary_probability: Path,
) -> None:
    width = int(meta["width"])
    height = int(meta["height"])
    nodata_ref = meta["nodata_ref"]
    n_members = len(basin_files)

    meta_i32 = meta["meta"].copy()
    meta_i32.update(dtype="int32", nodata=0, count=1, compress="LZW")

    meta_f32 = meta["meta"].copy()
    meta_f32.update(dtype="float32", nodata=-9999.0, count=1, compress="LZW")

    with rasterio.open(ref_path) as src_ref, \
         rasterio.open(out_most_likely, "w", **meta_i32) as dst_seg, \
         rasterio.open(out_certainty, "w", **meta_f32) as dst_cert, \
         rasterio.open(out_boundary_probability, "w", **meta_f32) as dst_bprob:

        for row_start in range(0, height, chunk_rows):
            row_stop = min(row_start + chunk_rows, height)
            nrows = row_stop - row_start
            print(f"Rows {row_start}–{row_stop - 1}")

            window = Window(0, row_start, width, nrows)
            ref_chunk = src_ref.read(1, window=window).astype(np.int32)

            domain = (ref_chunk != nodata_ref) & (ref_chunk > 0)

            label_stack = np.zeros((n_members, nrows, width), dtype=np.int32)
            raw_boundary_stack = np.zeros((n_members, nrows, width), dtype=bool)

            for i, fpath in enumerate(basin_files):
                with rasterio.open(fpath) as src_run:
                    nodata_run = src_run.nodata if src_run.nodata is not None else 0
                    run_chunk = src_run.read(1, window=window).astype(np.int32)

                valid_run = (run_chunk != nodata_run) & (run_chunk > 0)

                # ---- remap member labels to reference labels for majority product
                map_r = maps[i]
                run_safe = run_chunk.copy()
                run_safe[run_safe < 0] = 0
                run_safe[run_safe >= len(map_r)] = 0

                labels_ref = map_r[run_safe]
                labels_ref[~domain] = 0
                label_stack[i] = labels_ref

                # ---- raw boundary probability from original member labels
                # note: this is the raw way, not remapped labels
                raw_boundary_stack[i] = boundary_mask_from_labels(run_chunk, valid_run)

            # most likely + certainty in reference space
            stack_flat = label_stack.reshape(n_members, nrows * width)
            maj_flat, cert_flat = majority_label_and_cert(stack_flat)

            seg = maj_flat.reshape(nrows, width)
            seg[~domain] = 0
            dst_seg.write(seg.astype(np.int32), 1, window=window)

            cert = cert_flat.reshape(nrows, width).astype(np.float32)
            cert[~domain] = -9999.0
            dst_cert.write(cert, 1, window=window)

            # raw boundary probability from stacked raw member boundaries
            valid_any = domain
            bprob = raw_boundary_stack.sum(axis=0).astype(np.float32) / float(n_members)
            bprob[~valid_any] = -9999.0
            dst_bprob.write(bprob.astype(np.float32), 1, window=window)


# ============================================================
# Main runner
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="Build simple ensemble products from a fixed reference raster."
    )
    parser.add_argument("ensemble_dir", help="Directory containing ensemble basin rasters.")
    parser.add_argument("reference_raster", help="Reference basin raster.")
    parser.add_argument("--basin-pattern", default="basins_hydro_ens_*.tif")
    parser.add_argument("--chunk-rows", type=int, default=32)
    args = parser.parse_args()

    ensemble_dir = Path(args.ensemble_dir)
    basin_pattern = args.basin_pattern
    reference_raster = Path(args.reference_raster)

    out_most_likely = ensemble_dir / "basins_most_likely.tif"
    out_certainty = ensemble_dir / "basins_certainty.tif"
    out_boundary_probability = ensemble_dir / "basin_boundary_probability.tif"

    chunk_rows = args.chunk_rows

    basin_files = sorted(ensemble_dir.glob(basin_pattern))
    if not basin_files:
        raise FileNotFoundError(f"No files matching {basin_pattern} in {ensemble_dir}")
    if not reference_raster.exists():
        raise FileNotFoundError(f"Reference raster not found: {reference_raster}")

    print(f"Found {len(basin_files)} ensemble members")
    print(f"Using reference raster: {reference_raster}")

    maps, meta = build_label_maps(
        basin_files=basin_files,
        ref_path=reference_raster,
        chunk_rows=chunk_rows,
    )

    write_products(
        basin_files=basin_files,
        ref_path=reference_raster,
        maps=maps,
        meta=meta,
        chunk_rows=chunk_rows,
        out_most_likely=out_most_likely,
        out_certainty=out_certainty,
        out_boundary_probability=out_boundary_probability,
    )

    print("Done.")
    print(f"Wrote: {out_most_likely}")
    print(f"Wrote: {out_certainty}")
    print(f"Wrote: {out_boundary_probability}")


if __name__ == "__main__":
    main()
