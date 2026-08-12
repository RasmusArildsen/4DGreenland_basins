from __future__ import annotations

import argparse
from pathlib import Path
import numpy as np
import rasterio
from rasterio.windows import Window
from rasterio.features import rasterize
import geopandas as gpd
import matplotlib.pyplot as plt


def _rasterize_polygon_mask(poly_path: str | Path, ref_raster_path: str | Path) -> tuple[np.ndarray, dict]:
    """
    Rasterizes polygon(s) onto the grid of ref_raster_path.
    Returns (mask_bool, ref_meta).
    """
    poly_path = Path(poly_path)
    ref_raster_path = Path(ref_raster_path)

    with rasterio.open(ref_raster_path) as src:
        ref_meta = src.meta.copy()
        transform = src.transform
        out_shape = (src.height, src.width)
        crs = src.crs

    gdf = gpd.read_file(poly_path)
    if gdf.empty:
        raise ValueError(f"No geometries found in {poly_path}")

    if gdf.crs is None:
        raise ValueError(f"Polygon has no CRS: {poly_path}")

    if crs is None:
        raise ValueError(f"Raster has no CRS: {ref_raster_path}")

    if gdf.crs != crs:
        gdf = gdf.to_crs(crs)

    shapes = [(geom, 1) for geom in gdf.geometry if geom is not None and not geom.is_empty]
    if not shapes:
        raise ValueError("Polygon geometries are empty after reprojection.")

    mask_u8 = rasterize(
        shapes=shapes,
        out_shape=out_shape,
        transform=transform,
        fill=0,
        dtype="uint8",
        all_touched=True,  # if you want *touching* behaviour to be generous
    )
    return (mask_u8.astype(bool), ref_meta)


def polygon_conditioned_basins(
    mc_dir: str | Path,
    basin_pattern: str,
    polygon_path: str | Path,
    out_dir: str | Path | None = None,
    *,
    chunk_rows: int = 256,
    write_filtered_basins: bool = True,
    filtered_prefix: str = "basins_touchpoly_",
    prob_name: str = "probability_touching_polygon.tif",
    area_hist_name: str = "area_hist_touching_polygon.png",
) -> dict:
    """
    For each basin raster in mc_dir matching basin_pattern:
      - find basin IDs present under polygon mask (touch/intersect)
      - write filtered basin raster (only those IDs)
      - accumulate pixel-wise frequency (how often pixels belong to those basins)
      - compute total area per run and produce histogram

    Returns dict with paths + arrays summary.
    """
    mc_dir = Path(mc_dir)
    if out_dir is None:
        out_dir = mc_dir
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    basin_files = sorted(mc_dir.glob(basin_pattern))
    if not basin_files:
        raise FileNotFoundError(f"No files match {basin_pattern} in {mc_dir}")

    ref = basin_files[0]
    poly_mask, meta = _rasterize_polygon_mask(polygon_path, ref)

    height, width = meta["height"], meta["width"]
    transform = meta["transform"]

    # pixel area in map units (assumes projected meters if you want m²)
    # Note: if your CRS is geographic degrees, you should not do this directly.
    pixel_area = abs(transform.a * transform.e)  # a=ewres, e=nsres (negative usually)
    # If it's in meters, pixel_area is m²; convert to km²:
    pixel_area_km2 = pixel_area / 1e6

    # frequency accumulator: counts how many runs mark each pixel as "selected"
    counts = np.zeros((height, width), dtype=np.uint16)  # 500 fits in uint16

    # per-run areas
    areas_km2 = []

    # output metadata for filtered basins
    out_meta_i32 = meta.copy()
    out_meta_i32.update(dtype="int32", nodata=0, compress="LZW", count=1)

    # iterate runs
    for run_idx, tif in enumerate(basin_files, start=1):
        print(f"[{run_idx}/{len(basin_files)}] Processing {tif.name}")

        with rasterio.open(tif) as src:
            nodata = src.nodata
            if nodata is None:
                nodata = 0

            # ---- 1) find touched basin IDs by looking only where polygon_mask==True ----
            touched_ids = set()

            for row0 in range(0, height, chunk_rows):
                nrows = min(chunk_rows, height - row0)
                window = Window(0, row0, width, nrows)

                bas = src.read(1, window=window)
                pm = poly_mask[row0:row0 + nrows, :]

                # valid inside polygon and nonzero basins
                sel = pm & (bas != 0) & (bas != nodata)
                if not np.any(sel):
                    continue

                ids = np.unique(bas[sel])
                # remove nodata/0 just in case
                ids = ids[(ids != 0) & (ids != nodata)]
                touched_ids.update(ids.tolist())

            touched_ids = np.array(sorted(touched_ids), dtype=np.int32)

            # If nothing touches polygon: still need to produce empty outputs
            if touched_ids.size == 0:
                print("  ↳ No basins touch polygon in this run.")
                areas_km2.append(0.0)
                if write_filtered_basins:
                    out_path = out_dir / f"{filtered_prefix}{tif.stem}.tif"
                    with rasterio.open(out_path, "w", **out_meta_i32) as dst:
                        dst.write(np.zeros((height, width), dtype=np.int32), 1)
                continue

            # ---- 2) second pass: build selected mask + optional filtered basin tif ----
            run_selected_pixels = 0

            if write_filtered_basins:
                out_path = out_dir / f"{filtered_prefix}{tif.stem}.tif"
                dst = rasterio.open(out_path, "w", **out_meta_i32)
            else:
                dst = None

            # For fast membership test in chunks:
            # np.isin is ok for moderate ID counts; for huge counts, consider sorting + searchsorted
            for row0 in range(0, height, chunk_rows):
                nrows = min(chunk_rows, height - row0)
                window = Window(0, row0, width, nrows)

                bas = src.read(1, window=window).astype(np.int32)
                valid = (bas != 0) & (bas != nodata)

                # selected basins = those IDs anywhere that touch polygon
                sel = valid & np.isin(bas, touched_ids)

                # accumulate frequency
                counts[row0:row0 + nrows, :][sel] += 1
                run_selected_pixels += int(sel.sum())

                # write filtered basins (keep original IDs, else 0)
                if dst is not None:
                    out_chunk = np.where(sel, bas, 0).astype(np.int32)
                    dst.write(out_chunk, 1, window=window)

            if dst is not None:
                dst.close()

            area_km2 = run_selected_pixels * pixel_area_km2
            areas_km2.append(area_km2)
            print(f"  ↳ touched IDs: {touched_ids.size}, selected area: {area_km2:.3f} km²")

    # ---- 3) probability map (0..1) ----
    N = len(basin_files)
    prob = counts.astype(np.float32) / float(N)

    prob_meta = meta.copy()
    prob_meta.update(dtype="float32", nodata=0.0, compress="LZW", count=1)

    prob_path = out_dir / prob_name
    with rasterio.open(prob_path, "w", **prob_meta) as dst:
        dst.write(prob.astype(np.float32), 1)

    # ---- 4) histogram of total area per run ----
    areas_km2 = np.array(areas_km2, dtype=float)

    plt.figure()
    plt.hist(areas_km2, bins=30)
    plt.xlabel("Total selected area per run (km²)")
    plt.ylabel("Count (runs)")
    plt.title("Distribution of basin area touching polygon")
    hist_path = out_dir / area_hist_name
    plt.savefig(hist_path, dpi=150, bbox_inches="tight")
    plt.close()

    print("\n✅ Done")
    print("  Probability map:", prob_path)
    print("  Histogram:", hist_path)

    return {
        "probability_tif": prob_path,
        "hist_png": hist_path,
        "areas_km2": areas_km2,
        "counts": counts,  # uint16
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build probability and area products for basins touching a polygon."
    )
    parser.add_argument("mc_dir", help="Directory containing ensemble basin rasters.")
    parser.add_argument("polygon_path", help="Polygon file used to select touching basins.")
    parser.add_argument("--basin-pattern", default="basins_hydro_ens*.tif")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--chunk-rows", type=int, default=256)
    parser.add_argument("--no-filtered-basins", action="store_true")
    args = parser.parse_args()

    polygon_conditioned_basins(
        mc_dir=args.mc_dir,
        basin_pattern=args.basin_pattern,
        polygon_path=args.polygon_path,
        out_dir=args.out_dir or Path(args.mc_dir) / "touchpoly_products",
        chunk_rows=args.chunk_rows,
        write_filtered_basins=not args.no_filtered_basins,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
