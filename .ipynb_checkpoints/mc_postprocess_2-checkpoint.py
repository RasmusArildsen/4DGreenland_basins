import numpy as np
import rasterio
from rasterio.windows import Window
from pathlib import Path
from collections import defaultdict
import basin_core as bc
from basin_core import safe


def max_count_nonzero(stack_2d: np.ndarray) -> np.ndarray:
    """
    stack_2d: (N_runs, M) int labels (0 = background).
    Returns max_counts (M,) = largest count of any non-zero label per pixel.
    Memory-safe: avoids allocating group_size_pos.
    """
    N, M = stack_2d.shape
    s = np.sort(stack_2d, axis=0)  # (N, M) int32

    change = np.vstack([
        np.ones((1, M), dtype=bool),
        s[1:, :] != s[:-1, :]
    ])
    g = np.cumsum(change, axis=0).astype(np.int32)

    Np1 = N + 1
    col_offsets = (np.arange(M, dtype=np.int64) * Np1)[None, :]
    global_group = g.astype(np.int64) + col_offsets
    flat_group = global_group.ravel()

    max_gid = int(flat_group.max())
    counts = np.bincount(flat_group, minlength=max_gid + 1)  # int64

    # group_size is int64 -> cast down (max is N<=~1000 typically)
    group_size = counts[flat_group].reshape(N, M).astype(np.int16)

    # ignore zeros in-place (no extra array)
    group_size[s <= 0] = 0

    max_counts = group_size.max(axis=0).astype(np.int32)
    return max_counts



def majority_label_and_cert(stack_2d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """
    stack_2d: (N_runs, M) with integer labels (0 = background).

    Returns
    -------
    majority_label_flat : (M,) int32
        Most frequent non-zero label per pixel (0 if all runs are 0).
    certainty_flat : (M,) float32
        Frequency of that label, n_max / N.
    """
    N, M = stack_2d.shape

    # Sort labels so equal labels are contiguous per column
    s = np.sort(stack_2d, axis=0)  # (N, M), same dtype as input (int32)

    # Run-length encoding indices per column
    change = np.vstack([
        np.ones((1, M), dtype=bool),
        s[1:, :] != s[:-1, :],
    ])
    g = np.cumsum(change, axis=0).astype(np.int32)  # group index per element

    # Global group id across all columns
    Np1 = N + 1
    col_offsets = (np.arange(M, dtype=np.int64) * Np1)[None, :]
    global_group = g.astype(np.int64) + col_offsets
    flat_group = global_group.ravel()

    max_gid = int(flat_group.max())
    counts = np.bincount(flat_group, minlength=max_gid + 1)

    # Group size for each element -> cast down to int16 to save memory
    group_size = counts[flat_group].reshape(N, M).astype(np.int16)

    # Zero out groups where label == 0 (background)
    group_size[s <= 0] = 0

    # For each column, find index of largest group size
    max_idx = group_size.argmax(axis=0)              # (M,)
    majority_label_flat = s[max_idx, np.arange(M)]   # labels of those groups

    # Corresponding counts and certainty
    max_counts = group_size.max(axis=0).astype(np.float32)  # (M,)
    certainty_flat = max_counts / float(N)

    return majority_label_flat.astype(np.int32), certainty_flat



def build_mc_products(
    mc_dir,
    basin_pattern: str = "basins_mc_*.tif",
    ref_index: int = 0,
    chunk_rows: int = 32,
    p_stable_pixel: float = 0.90,
    p_stable_edge: float = 0.70,  # NOTE: currently unused (no global merging)
    p_min_div: float = 0.00,
    out_most_likely: str | Path | None = None,
    out_cert: str | Path | None = None,
    out_uncert: str | Path | None = None,
    out_bound_prob: str | Path | None = None,
    out_stable_div: str | Path | None = None,
    out_uncert_div: str | Path | None = None,
    # ---------------- NEW: optional merge step ----------------
    run_merge: bool = False,
    merge_dem: str | Path | None = None,          # required if run_merge=True
    merge_res_m: int = 500,
    merge_min_basin_km2: float = 500.0,
    merge_do_exclaves: bool = True,
    merge_max_exclave_iters: int = 6,
    out_merged: str | Path | None = None,         # default: mc_dir/basins_most_likely_merged.tif
):
    """
    Post-process Monte Carlo basin realisations into:

    - most-likely basins (per-pixel majority label across runs)
    - pixel-wise certainty (fraction of runs agreeing with that label)
    - basin-boundary probability (probability a pixel sits on a basin boundary)
    - uncertainty (1 - certainty)
    - stable / uncertain divide masks

    Optionally also runs your GRASS-based basin merging algorithm on the final
    basins_most_likely.tif (via basin_core.merge_basins()).

    Parameters
    ----------
    mc_dir : str or Path
        Directory containing basin realisations.
    basin_pattern : str
        Glob pattern for basin rasters (default 'basins_mc_*.tif').
    ref_index : int
        Index (0-based) of the reference realisation in the sorted file list.
    chunk_rows : int
        Number of rows per chunk for block processing.
    p_stable_pixel : float
        Threshold for pixel-wise boundary probability for "stable divides".
    p_stable_edge : float
        Currently unused (no global basin merging); kept for API compatibility.
    p_min_div : float
        Minimum boundary probability to be considered any divide.
    out_* : str or Path or None
        Output filenames. If None, defaults are created inside mc_dir.

    run_merge : bool
        If True, run basin_core.merge_basins() on out_most_likely.
    merge_dem : str or Path
        DEM used to define GRASS location/region and DEM extent for filling.
        REQUIRED if run_merge=True.
    merge_res_m : int
        Working resolution (m) for merging.
    merge_min_basin_km2 : float
        Minimum basin size threshold for merging small basins into big ones.
    merge_do_exclaves : bool
        If True, run the iterative anti-exclave cleanup (no area threshold needed
        if your basin_core implementation ignores it).
    merge_max_exclave_iters : int
        Safety cap on exclave iterations.
    out_merged : str or Path or None
        Output filename for merged basins (GeoTIFF). Default in mc_dir.
    """

    mc_dir = Path(mc_dir)

    # ----- Outputs (defaults if not provided) -----
    if out_most_likely is None:
        out_most_likely = mc_dir / "basins_most_likely.tif"
    if out_cert is None:
        out_cert = mc_dir / "basins_certainty.tif"
    if out_uncert is None:
        out_uncert = mc_dir / "basins_uncertainty.tif"
    if out_bound_prob is None:
        out_bound_prob = mc_dir / "basin_boundary_probability.tif"
    if out_stable_div is None:
        out_stable_div = mc_dir / "basin_stable_divides.tif"
    if out_uncert_div is None:
        out_uncert_div = mc_dir / "basin_uncertain_divides.tif"

    if out_merged is None:
        out_merged = mc_dir / "basins_most_likely_merged.tif"

    # -------------------------------------------------------------------------
    # 0) COLLECT FILES & BASIC INFO
    # -------------------------------------------------------------------------
    basin_files = sorted(mc_dir.glob(basin_pattern))
    if not basin_files:
        raise FileNotFoundError(f"No files matching {basin_pattern} in {mc_dir}")

    N_runs = len(basin_files)
    print(f"Found {N_runs} basin realisations")

    ref_file = basin_files[ref_index]
    print(f"Using {ref_file.name} as label grid / domain")

    with rasterio.open(ref_file) as src_ref:
        meta = src_ref.meta.copy()
        width, height = src_ref.width, src_ref.height
        transform = src_ref.transform
        crs = src_ref.crs
        nodata_ref = src_ref.nodata if src_ref.nodata is not None else 0

        max_ref_label = 0
        for row_start in range(0, height, chunk_rows):
            row_stop = min(row_start + chunk_rows, height)
            window = Window(0, row_start, width, row_stop - row_start)
            ref_chunk = src_ref.read(1, window=window)
            max_ref_label = max(max_ref_label, int(ref_chunk.max()))

    print("Max reference basin ID:", max_ref_label)

    # -------------------------------------------------------------------------
    # 1) BUILD PER-RUN MAPPINGS → REFERENCE BASINS
    # -------------------------------------------------------------------------
    maps = []  # map_r[label_in_run] = label_in_ref

    src_ref_global = rasterio.open(ref_file)

    for idx, fpath in enumerate(basin_files):
        print(f"\nMapping run {idx+1}/{N_runs}: {fpath.name}")

        if idx == ref_index:
            # identity mapping for reference
            map_r = np.arange(max_ref_label + 1, dtype=np.int32)
            maps.append(map_r)
            continue

        mapping_counts = defaultdict(int)

        with rasterio.open(fpath) as src_run:
            nodata_run = src_run.nodata if src_run.nodata is not None else 0

            for row_start in range(0, height, chunk_rows):
                row_stop = min(row_start + chunk_rows, height)
                window = Window(0, row_start, width, row_stop - row_start)

                ref_chunk = src_ref_global.read(1, window=window)
                run_chunk = src_run.read(1, window=window)

                domain_chunk = (ref_chunk != 0) & (ref_chunk != nodata_ref)
                mask = domain_chunk & (run_chunk != 0) & (run_chunk != nodata_run)
                if not np.any(mask):
                    continue

                rvals = run_chunk[mask].astype(np.int64)
                kvals = ref_chunk[mask].astype(np.int64)

                idx_comb = rvals * (max_ref_label + 1) + kvals
                uniq, counts = np.unique(idx_comb, return_counts=True)
                for u, c in zip(uniq, counts):
                    mapping_counts[int(u)] += int(c)

        if not mapping_counts:
            print("  Warning: no overlaps; mapping everything to 0")
            # auto-size mapping
            with rasterio.open(fpath) as src_run:
                max_run_label = 0
                for row_start in range(0, height, chunk_rows):
                    row_stop = min(row_start + chunk_rows, height)
                    window = Window(0, row_start, width, row_stop - row_start)
                    run_chunk = src_run.read(1, window=window)
                    max_run_label = max(max_run_label, int(run_chunk.max()))
            map_r = np.zeros(max_run_label + 1, dtype=np.int32)
            maps.append(map_r)
            continue

        max_run_label = max(k // (max_ref_label + 1) for k in mapping_counts.keys())
        best_k = np.zeros(max_run_label + 1, dtype=np.int32)
        best_n = np.zeros(max_run_label + 1, dtype=np.int64)

        for idx_comb, c in mapping_counts.items():
            j = idx_comb // (max_ref_label + 1)  # label in run
            if j == 0:
                continue
            k = idx_comb % (max_ref_label + 1)   # label in ref
            if c > best_n[j]:
                best_n[j] = c
                best_k[j] = k

        map_r = best_k
        map_r[0] = 0
        maps.append(map_r)

    src_ref_global.close()
    print("\n✓ Finished building label mappings to reference basins")

    # -------------------------------------------------------------------------
    # 2) SINGLE PASS: MOST-LIKELY BASINS + CERTAINTY + BOUNDARY PROBABILITY
    # -------------------------------------------------------------------------
    meta_i32 = meta.copy()
    meta_i32.update(dtype="int32", nodata=0, count=1, compress="LZW")
    meta_f32 = meta.copy()
    meta_f32.update(dtype="float32", nodata=0.0, count=1, compress="LZW")

    with rasterio.open(ref_file) as src_ref, \
         rasterio.open(out_most_likely, "w", **meta_i32) as dst_seg, \
         rasterio.open(out_cert, "w", **meta_f32) as dst_cert, \
         rasterio.open(out_bound_prob, "w", **meta_f32) as dst_bprob:

        for row_start in range(0, height, chunk_rows):
            row_stop = min(row_start + chunk_rows, height)
            nrows_chunk = row_stop - row_start
            print(f"[Pass] Rows {row_start}–{row_stop-1}")

            window = Window(0, row_start, width, nrows_chunk)
            ref_chunk = src_ref.read(1, window=window).astype(np.int32)

            # Domain where reference has a basin
            domain_chunk = (ref_chunk != 0) & (ref_chunk != nodata_ref)

            # stack of mapped REF labels (N_runs, nrows_chunk, width)
            stack_final = np.zeros((N_runs, nrows_chunk, width), dtype=np.int32)

            for i, fpath in enumerate(basin_files):
                map_r = maps[i]
                with rasterio.open(fpath) as src_run:
                    nodata_run = src_run.nodata if src_run.nodata is not None else 0
                    run_chunk = src_run.read(1, window=window)

                run_chunk = run_chunk.astype(np.int32)
                run_chunk[run_chunk == nodata_run] = 0
                run_chunk[run_chunk < 0] = 0
                mask_big = run_chunk >= len(map_r)
                run_chunk[mask_big] = 0

                labels_ref = map_r[run_chunk]
                labels_ref[~domain_chunk] = 0
                stack_final[i, :, :] = labels_ref

            # ---- pixel-wise majority label + certainty ----
            N = N_runs
            M = nrows_chunk * width
            stack_flat = stack_final.reshape(N, M)

            maj_labels_flat, certainty_flat = majority_label_and_cert(stack_flat)

            # basins_most_likely (segmentation)
            seg_chunk = maj_labels_flat.reshape(nrows_chunk, width)
            seg_chunk[~domain_chunk] = 0
            dst_seg.write(seg_chunk.astype(np.int32), 1, window=window)

            # certainty
            cert_chunk = certainty_flat.reshape(nrows_chunk, width)
            cert_chunk[~domain_chunk] = 0.0
            dst_cert.write(cert_chunk, 1, window=window)

            # ---- boundary probability w.r.t run labels (reference IDs) ----
            labels = stack_final
            valid = labels != 0
            B = np.zeros_like(labels, dtype=bool)

            # N-S neighbours
            diff_ns = labels[:, 1:, :] != labels[:, :-1, :]
            valid_ns = valid[:, 1:, :] & valid[:, :-1, :]
            bd_ns = diff_ns & valid_ns
            B[:, 1:, :] |= bd_ns
            B[:, :-1, :] |= bd_ns

            # E-W neighbours
            diff_ew = labels[:, :, 1:] != labels[:, :, :-1]
            valid_ew = valid[:, :, 1:] & valid[:, :, :-1]
            bd_ew = diff_ew & valid_ew
            B[:, :, 1:] |= bd_ew
            B[:, :, :-1] |= bd_ew

            bprob_chunk = B.sum(axis=0).astype(np.float32) / float(N)
            bprob_chunk[~domain_chunk] = 0.0

            dst_bprob.write(bprob_chunk, 1, window=window)

    print("✓ Wrote basins_most_likely, basins_certainty, basin_boundary_probability")

    # -------------------------------------------------------------------------
    # 3) DERIVED PRODUCTS: UNCERTAINTY + DIVIDE MASKS
    # -------------------------------------------------------------------------
    with rasterio.open(out_cert) as src_c:
        cert = src_c.read(1).astype(np.float32)
        meta_u = src_c.meta.copy()

    uncert = 1.0 - cert
    uncert[uncert < 0] = 0.0

    meta_u.update(dtype="float32", nodata=0.0, count=1, compress="LZW")
    with rasterio.open(out_uncert, "w", **meta_u) as dst:
        dst.write(uncert, 1)

    print("✓ Wrote basins_uncertainty:", Path(out_uncert).name)

    with rasterio.open(out_bound_prob) as src_bp:
        bprob = src_bp.read(1).astype(np.float32)
        meta_div = src_bp.meta.copy()

    stable_divides    = (bprob >= p_stable_pixel).astype("uint8")
    uncertain_divides = ((bprob >= p_min_div) & (bprob < p_stable_pixel)).astype("uint8")

    meta_div.update(dtype="uint8", nodata=0, count=1, compress="LZW")

    with rasterio.open(out_stable_div, "w", **meta_div) as dst:
        dst.write(stable_divides, 1)

    with rasterio.open(out_uncert_div, "w", **meta_div) as dst:
        dst.write(uncertain_divides, 1)

    print("✓ Wrote stable and uncertain divide masks")

    # -------------------------------------------------------------------------
    # 4) OPTIONAL: RUN YOUR GRASS MERGE ON basins_most_likely2.tif
    # -------------------------------------------------------------------------
    if run_merge:
        if merge_dem is None:
            raise ValueError("run_merge=True requires merge_dem=<path to DEM>")

        # Defer all GRASS details to basin_core.merge_basins()
        # (You said it's already in basin_core.py)
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
        print("✓ Wrote merged basins:", merged)

    print("Done.")


def make_perturbed_dem(
    run_idx: int,
    base_dem: str,
    var_map: str,
    corr_pix: int,
):
    """
    Create a spatially correlated random perturbation and add it to the DEM.

    - base_dem: GRASS raster name (ideally already hole-filled, e.g. "dem_filled_500m")
    - var_map:  GRASS raster with variance (σ²) for each pixel
    """
    # ensure no mask is active while generating noise / perturbation
    try:
        bc.gs.run_command("r.mask", flags="r")
    except Exception:
        pass

    # region aligned to DEM
    bc.gs.run_command("g.region", raster=base_dem)

    # 1) Gaussian white noise N(0,1)
    noise_raw = "noise_raw"
    bc.gs.run_command(
        "r.surf.gauss",
        output=noise_raw,
        mean=0.0,
        sigma=1.0,
        overwrite=True,
    )

    # 2) Impose spatial correlation with Gaussian kernel (corr_pix in cells)
    reg = bc.gs.parse_command("g.region", flags="g")
    cellsize = float(reg["ewres"])  # assume square pixels

    radius1 = corr_pix * cellsize          # in map units
    radius2 = 2 * corr_pix * cellsize      # in map units

    noise_corr = "noise_corr"
    bc.gs.run_command(
        "r.resamp.filter",
        input=noise_raw,
        output=noise_corr,
        filter="gauss,box",
        radius=f"{radius1},{radius2}",
        overwrite=True,
    )

    # 3) Build perturbation and perturbed DEM
    out_dem  = f"dem_mc_{run_idx:03d}"
    pert_map = f"pert_mc_{run_idx:03d}"

    # raw perturbation (assumes var_map = σ²)
    safe(f"{pert_map} = noise_corr * sqrt({var_map})")

    # mask invalid
    safe(
        f"{pert_map} = if(isnull({base_dem}) || isnull({var_map}), "
        f"null(), {pert_map})"
    )

    # add to base DEM
    safe(
        f"{out_dem} = if(isnull({base_dem}), null(), {base_dem} + {pert_map})"
    )

    print(f"✓ Created perturbation: {pert_map}")
    print(f"✓ Created perturbed DEM: {out_dem}")

    return out_dem

def make_perturbed_bed(
    run_idx: int,
    base_bed: str,
    err_map: str,
    corr_pix: int,
    *,
    err_is_variance: bool = False,   # set True if err_map is σ²
):
    """
    Create spatially correlated perturbation for bed and add it to the bed raster.

    base_bed : GRASS raster name (bed elevations)
    err_map  : GRASS raster of bed uncertainty (σ or σ² depending on err_is_variance)
    corr_pix : correlation length in pixels (cells)
    """
    # ensure no mask is active while generating noise / perturbation
    try:
        bc.gs.run_command("r.mask", flags="r")
    except Exception:
        pass

    bc.gs.run_command("g.region", raster=base_bed)

    noise_raw = f"noise_raw_bed_{run_idx:03d}"
    bc.gs.run_command(
        "r.surf.gauss",
        output=noise_raw,
        mean=0.0,
        sigma=1.0,
        overwrite=True,
    )

    reg = bc.gs.parse_command("g.region", flags="g")
    cellsize = float(reg["ewres"])

    radius1 = corr_pix * cellsize
    radius2 = 2 * corr_pix * cellsize

    noise_corr = f"noise_corr_bed_{run_idx:03d}"
    bc.gs.run_command(
        "r.resamp.filter",
        input=noise_raw,
        output=noise_corr,
        filter="gauss,box",
        radius=f"{radius1},{radius2}",
        overwrite=True,
    )

    out_bed  = f"bed_mc_{run_idx:03d}"
    pert_map = f"pert_bed_{run_idx:03d}"

    if err_is_variance:
        # err_map = σ²
        safe(f"{pert_map} = {noise_corr} * sqrt({err_map})")
    else:
        # err_map = σ
        safe(f"{pert_map} = {noise_corr} * ({err_map})")

    # mask invalid
    safe(
        f"{pert_map} = if(isnull({base_bed}) || isnull({err_map}), "
        f"null(), {pert_map})"
    )

    safe(f"{out_bed} = if(isnull({base_bed}), null(), {base_bed} + {pert_map})")

    return out_bed, pert_map

