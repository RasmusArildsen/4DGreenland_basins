from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window

import basin_core as bc
from basin_core import safe


# -----------------------------------------------------------------------------
# Ensemble products
# -----------------------------------------------------------------------------
def majority_label_and_cert(stack_2d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """
    Parameters
    ----------
    stack_2d : ndarray
        Shape (N_members, M), integer labels, with 0 meaning background.

    Returns
    -------
    majority_label_flat, certainty_flat : tuple[ndarray, ndarray]
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


def build_ensemble_products(
    ensemble_dir,
    basin_pattern: str = "basins_hydro_ens_*.tif",
    ref_index: int = 0,
    chunk_rows: int = 32,
    p_stable_pixel: float = 0.90,
    p_min_div: float = 0.00,
    out_most_likely: str | Path | None = None,
    out_cert: str | Path | None = None,
    out_uncert: str | Path | None = None,
    out_bound_prob: str | Path | None = None,
    out_stable_div: str | Path | None = None,
    out_uncert_div: str | Path | None = None,
    run_merge: bool = False,
    merge_dem: str | Path | None = None,
    merge_res_m: int = 500,
    merge_min_basin_km2: float = 500.0,
    merge_do_exclaves: bool = True,
    merge_max_exclave_iters: int = 6,
    out_merged: str | Path | None = None,
):
    ensemble_dir = Path(ensemble_dir)

    if out_most_likely is None:
        out_most_likely = ensemble_dir / "basins_most_likely.tif"
    if out_cert is None:
        out_cert = ensemble_dir / "basins_certainty.tif"
    if out_uncert is None:
        out_uncert = ensemble_dir / "basins_uncertainty.tif"
    if out_bound_prob is None:
        out_bound_prob = ensemble_dir / "basin_boundary_probability.tif"
    if out_stable_div is None:
        out_stable_div = ensemble_dir / "basin_stable_divides.tif"
    if out_uncert_div is None:
        out_uncert_div = ensemble_dir / "basin_uncertain_divides.tif"
    if out_merged is None:
        out_merged = ensemble_dir / "basins_most_likely_merged.tif"

    basin_files = sorted(ensemble_dir.glob(basin_pattern))
    if not basin_files:
        raise FileNotFoundError(f"No files matching {basin_pattern} in {ensemble_dir}")

    n_members = len(basin_files)
    ref_file = basin_files[ref_index]
    print(f"Found {n_members} realisations; reference = {ref_file.name}")

    with rasterio.open(ref_file) as src_ref:
        meta = src_ref.meta.copy()
        width, height = src_ref.width, src_ref.height
        nodata_ref = src_ref.nodata if src_ref.nodata is not None else 0

        max_ref_label = 0
        for row_start in range(0, height, chunk_rows):
            row_stop = min(row_start + chunk_rows, height)
            window = Window(0, row_start, width, row_stop - row_start)
            ref_chunk = src_ref.read(1, window=window)
            max_ref_label = max(max_ref_label, int(ref_chunk.max()))

    print("Max reference basin ID:", max_ref_label)

    maps: list[np.ndarray] = []
    with rasterio.open(ref_file) as src_ref_global:
        for idx, fpath in enumerate(basin_files):
            print(f"\nMapping member {idx + 1}/{n_members}: {fpath.name}")

            if idx == ref_index:
                maps.append(np.arange(max_ref_label + 1, dtype=np.int32))
                continue

            mapping_counts = defaultdict(int)
            with rasterio.open(fpath) as src_run:
                nodata_run = src_run.nodata if src_run.nodata is not None else 0
                for row_start in range(0, height, chunk_rows):
                    row_stop = min(row_start + chunk_rows, height)
                    window = Window(0, row_start, width, row_stop - row_start)

                    ref_chunk = src_ref_global.read(1, window=window)
                    run_chunk = src_run.read(1, window=window)

                    domain = (ref_chunk != 0) & (ref_chunk != nodata_ref)
                    mask = domain & (run_chunk != 0) & (run_chunk != nodata_run)
                    if not np.any(mask):
                        continue

                    run_vals = run_chunk[mask].astype(np.int64)
                    ref_vals = ref_chunk[mask].astype(np.int64)
                    idx_comb = run_vals * (max_ref_label + 1) + ref_vals
                    uniq, counts = np.unique(idx_comb, return_counts=True)
                    for u, c in zip(uniq, counts):
                        mapping_counts[int(u)] += int(c)

            if not mapping_counts:
                with rasterio.open(fpath) as src_run:
                    max_run_label = 0
                    for row_start in range(0, height, chunk_rows):
                        row_stop = min(row_start + chunk_rows, height)
                        window = Window(0, row_start, width, row_stop - row_start)
                        run_chunk = src_run.read(1, window=window)
                        max_run_label = max(max_run_label, int(run_chunk.max()))
                maps.append(np.zeros(max_run_label + 1, dtype=np.int32))
                continue

            max_run_label = max(k // (max_ref_label + 1) for k in mapping_counts.keys())
            best_ref = np.zeros(max_run_label + 1, dtype=np.int32)
            best_count = np.zeros(max_run_label + 1, dtype=np.int64)

            for comb, count in mapping_counts.items():
                run_label = comb // (max_ref_label + 1)
                if run_label == 0:
                    continue
                ref_label = comb % (max_ref_label + 1)
                if count > best_count[run_label]:
                    best_count[run_label] = count
                    best_ref[run_label] = ref_label

            best_ref[0] = 0
            maps.append(best_ref)

    print("\n✓ Finished building mappings")

    meta_i32 = meta.copy()
    meta_i32.update(dtype="int32", nodata=0, count=1, compress="LZW")
    meta_f32 = meta.copy()
    meta_f32.update(dtype="float32", nodata=0.0, count=1, compress="LZW")

    with (
        rasterio.open(ref_file) as src_ref,
        rasterio.open(out_most_likely, "w", **meta_i32) as dst_seg,
        rasterio.open(out_cert, "w", **meta_f32) as dst_cert,
        rasterio.open(out_bound_prob, "w", **meta_f32) as dst_bprob,
    ):
        for row_start in range(0, height, chunk_rows):
            row_stop = min(row_start + chunk_rows, height)
            nrows = row_stop - row_start
            print(f"[Pass] Rows {row_start}–{row_stop - 1}")

            window = Window(0, row_start, width, nrows)
            ref_chunk = src_ref.read(1, window=window).astype(np.int32)
            domain = (ref_chunk != 0) & (ref_chunk != nodata_ref)

            label_stack = np.zeros((n_members, nrows, width), dtype=np.int32)
            for i, fpath in enumerate(basin_files):
                map_r = maps[i]
                with rasterio.open(fpath) as src_run:
                    nodata_run = src_run.nodata if src_run.nodata is not None else 0
                    run_chunk = src_run.read(1, window=window).astype(np.int32)

                run_chunk[run_chunk == nodata_run] = 0
                run_chunk[run_chunk < 0] = 0
                run_chunk[run_chunk >= len(map_r)] = 0

                labels_ref = map_r[run_chunk]
                labels_ref[~domain] = 0
                label_stack[i] = labels_ref

            stack_flat = label_stack.reshape(n_members, nrows * width)
            maj_flat, cert_flat = majority_label_and_cert(stack_flat)

            seg = maj_flat.reshape(nrows, width)
            seg[~domain] = 0
            dst_seg.write(seg.astype(np.int32), 1, window=window)

            cert = cert_flat.reshape(nrows, width)
            cert[~domain] = 0.0
            dst_cert.write(cert.astype(np.float32), 1, window=window)

            valid = label_stack != 0
            boundaries = np.zeros_like(label_stack, dtype=bool)

            diff_ns = label_stack[:, 1:, :] != label_stack[:, :-1, :]
            bd_ns = diff_ns & (valid[:, 1:, :] & valid[:, :-1, :])
            boundaries[:, 1:, :] |= bd_ns
            boundaries[:, :-1, :] |= bd_ns

            diff_ew = label_stack[:, :, 1:] != label_stack[:, :, :-1]
            bd_ew = diff_ew & (valid[:, :, 1:] & valid[:, :, :-1])
            boundaries[:, :, 1:] |= bd_ew
            boundaries[:, :, :-1] |= bd_ew

            bprob = boundaries.sum(axis=0).astype(np.float32) / float(n_members)
            bprob[~domain] = 0.0
            dst_bprob.write(bprob, 1, window=window)

    print("✓ wrote most-likely, certainty, boundary-probability")

    with rasterio.open(out_cert) as src_c:
        cert = src_c.read(1).astype(np.float32)
        meta_u = src_c.meta.copy()

    uncert = 1.0 - cert
    uncert[uncert < 0] = 0.0

    meta_u.update(dtype="float32", nodata=0.0, count=1, compress="LZW")
    with rasterio.open(out_uncert, "w", **meta_u) as dst:
        dst.write(uncert, 1)

    with rasterio.open(out_bound_prob) as src_bp:
        bprob = src_bp.read(1).astype(np.float32)
        meta_div = src_bp.meta.copy()

    stable = (bprob >= p_stable_pixel).astype("uint8")
    uncertain = ((bprob >= p_min_div) & (bprob < p_stable_pixel)).astype("uint8")

    meta_div.update(dtype="uint8", nodata=0, count=1, compress="LZW")
    with rasterio.open(out_stable_div, "w", **meta_div) as dst:
        dst.write(stable, 1)
    with rasterio.open(out_uncert_div, "w", **meta_div) as dst:
        dst.write(uncertain, 1)

    print("✓ wrote uncertainty + divide masks")

    if run_merge:
        if merge_dem is None:
            raise ValueError("run_merge=True requires merge_dem=<path>")
        merged = bc.merge_basins(
            basins_input=str(Path(out_most_likely)),
            dem_path=str(Path(merge_dem)),
            out_dir=str(Path(out_merged).parent),
            out_name=str(Path(out_merged).name),
            min_basin_size_km2=float(merge_min_basin_km2),
            res_m=int(merge_res_m),
            do_exclave_cleanup=bool(merge_do_exclaves),
            max_exclave_iters=int(merge_max_exclave_iters),
        )
        print("✓ merged final most-likely basins:", merged)

    print("Done.")


# -----------------------------------------------------------------------------
# Perturbation helpers used by ensemble_runner.py
# -----------------------------------------------------------------------------
def make_perturbed_surface_member(member_idx: int, base_dem: str, var_map: str, corr_pix: int):
    try:
        bc.gs.run_command("r.mask", flags="r")
    except Exception:
        pass

    bc.gs.run_command("g.region", raster=base_dem)

    noise_raw = f"noise_raw_{member_idx:03d}"
    noise_corr = f"noise_corr_{member_idx:03d}"

    bc.gs.run_command("r.surf.gauss", output=noise_raw, mean=0.0, sigma=1.0, overwrite=True)

    reg = bc.gs.parse_command("g.region", flags="g")
    cellsize = float(reg["ewres"])
    radius1 = corr_pix * cellsize
    radius2 = 2 * corr_pix * cellsize

    bc.gs.run_command(
        "r.resamp.filter",
        input=noise_raw,
        output=noise_corr,
        filter="gauss,box",
        radius=f"{radius1},{radius2}",
        overwrite=True,
    )

    out_dem = f"dem_mc_{member_idx:03d}"
    pert_map = f"pert_mc_{member_idx:03d}"

    safe(f"{pert_map} = {noise_corr} * sqrt({var_map})")
    safe(f"{pert_map} = if(isnull({base_dem}) || isnull({var_map}), null(), {pert_map})")
    safe(f"{out_dem} = if(isnull({base_dem}), null(), {base_dem} + {pert_map})")
    return out_dem


def make_perturbed_bed_member(
    member_idx: int,
    base_bed: str,
    err_map: str,
    corr_pix: int,
    *,
    err_is_variance: bool = False,
):
    try:
        bc.gs.run_command("r.mask", flags="r")
    except Exception:
        pass

    bc.gs.run_command("g.region", raster=base_bed)

    noise_raw = f"noise_raw_bed_{member_idx:03d}"
    noise_corr = f"noise_corr_bed_{member_idx:03d}"

    bc.gs.run_command("r.surf.gauss", output=noise_raw, mean=0.0, sigma=1.0, overwrite=True)

    reg = bc.gs.parse_command("g.region", flags="g")
    cellsize = float(reg["ewres"])
    radius1 = corr_pix * cellsize
    radius2 = 2 * corr_pix * cellsize

    bc.gs.run_command(
        "r.resamp.filter",
        input=noise_raw,
        output=noise_corr,
        filter="gauss,box",
        radius=f"{radius1},{radius2}",
        overwrite=True,
    )

    out_bed = f"bed_mc_{member_idx:03d}"
    pert_map = f"pert_bed_{member_idx:03d}"

    if err_is_variance:
        safe(f"{pert_map} = {noise_corr} * sqrt({err_map})")
    else:
        safe(f"{pert_map} = {noise_corr} * ({err_map})")

    safe(f"{pert_map} = if(isnull({base_bed}) || isnull({err_map}), null(), {pert_map})")
    safe(f"{out_bed} = if(isnull({base_bed}), null(), {base_bed} + {pert_map})")
    return out_bed, pert_map
