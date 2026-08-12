from __future__ import annotations

import sys
import os
from pathlib import Path
from collections import Counter

import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from rasterio import features
from shapely.geometry import shape
from shapely.ops import unary_union
import matplotlib.pyplot as plt


# ---------------------------- CONFIG ----------------------------
MC_DIR = Path(os.environ.get("MC_DIR", "outputs/hybrid_100m/merged_members"))
BASIN_GLOB = "basins_hydro_ens_*.tif"

MOST_LIKELY_NAME = "basins_most_likely_merged_final.tif"

GLACIER_SHP = Path(
    os.environ.get(
        "GLACIER_SHP",
        "data/input/glaciers/GreenlandGlacierNames_GGNv01_WGS84_updated.shp",
    )
)

OUT_DIR = MC_DIR / "glacier_basin_stats"

GLACIER_NAME_COL = "Official_n"
FOREIGN_NAME_COL = "Foreign_na"
GLACIER_ID_COL = "ID"
NODATA_VALUES = {-9999, 0}
HIST_BINS = 35

DROP_NULL_NAMES = True
DEFAULT_HIST_COLOR = "tab:blue"
HIST_TITLE_FONTSIZE = 24
HIST_LABEL_FONTSIZE = 20
HIST_TICK_FONTSIZE = 16
HIST_LEGEND_FONTSIZE = 18
HIST_STATS_FONTSIZE = 18
# ---------------------------------------------------------------


def pixel_area_km2(ds: rasterio.io.DatasetReader) -> float:
    return float(abs(ds.transform.a * ds.transform.e) / 1e6)


def basin_areas_km2(ds: rasterio.io.DatasetReader) -> dict[int, float]:
    """Compute full-raster area for every basin label in one pass."""
    arr = ds.read(1)

    if ds.nodata is not None:
        arr = np.where(arr == ds.nodata, 0, arr)

    arr = arr.astype(np.int64)
    arr[arr < 0] = 0

    labels, counts = np.unique(arr, return_counts=True)
    px_area = pixel_area_km2(ds)

    out: dict[int, float] = {}
    for lab, cnt in zip(labels, counts):
        lab = int(lab)
        if lab in NODATA_VALUES or lab <= 0:
            continue
        out[lab] = float(cnt * px_area)
    return out


def sanitize_filename(text: str) -> str:
    keep = []
    for ch in str(text):
        if ch.isalnum() or ch in ("_", "-", "."):
            keep.append(ch)
        elif ch.isspace():
            keep.append("_")
    out = "".join(keep).strip("_")
    return out or "unnamed"


def build_disambiguated_glacier_names(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Create a unique glacier_name.

    Base case:
        glacier_name = Official_n

    If same Official_n has multiple distinct Foreign_na:
        glacier_name = Official_n__Foreign_na

    If that is still not unique:
        glacier_name = <current_name>__ID<glacier_id>
    """
    gdf = gdf.copy()

    gdf["official_name"] = gdf[GLACIER_NAME_COL].fillna("").astype(str).str.strip()

    if FOREIGN_NAME_COL in gdf.columns:
        gdf["foreign_name"] = gdf[FOREIGN_NAME_COL].fillna("").astype(str).str.strip()
    else:
        gdf["foreign_name"] = ""

    foreign_counts = (
        gdf.groupby("official_name")["foreign_name"]
        .apply(lambda s: len({x for x in s if x}))
        .to_dict()
    )

    def build_display_name(row) -> str:
        official = row["official_name"]
        foreign = row["foreign_name"]

        if foreign_counts.get(official, 0) > 1 and foreign:
            return f"{official} ({foreign})"
        return official

    gdf["glacier_name"] = gdf.apply(build_display_name, axis=1)

    # Final uniqueness safeguard
    name_counts = gdf["glacier_name"].value_counts().to_dict()

    def ensure_unique_name(row) -> str:
        name = row["glacier_name"]
        glacier_id = str(row["glacier_id"])
        if name_counts.get(name, 0) > 1:
            return f"{name}__ID{glacier_id}"
        return name

    gdf["glacier_name"] = gdf.apply(ensure_unique_name, axis=1)
    return gdf


def load_glacier_points(shp_path: Path, raster_crs) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(shp_path)

    if GLACIER_NAME_COL not in gdf.columns:
        raise ValueError(
            f"Column '{GLACIER_NAME_COL}' not found. Available columns: {list(gdf.columns)}"
        )

    if gdf.empty:
        raise ValueError("Glacier shapefile is empty.")

    if DROP_NULL_NAMES:
        gdf = gdf[gdf[GLACIER_NAME_COL].notna()].copy()

    if gdf.crs is None:
        raise ValueError("Glacier shapefile has no CRS.")

    if gdf.crs != raster_crs:
        gdf = gdf.to_crs(raster_crs)

    if not all(gdf.geometry.geom_type == "Point"):
        gdf = gdf.copy()
        gdf["geometry"] = gdf.geometry.representative_point()

    gdf = gdf.reset_index(drop=True)

    if GLACIER_ID_COL in gdf.columns:
        gdf["glacier_id"] = gdf[GLACIER_ID_COL].astype(str)
    else:
        gdf["glacier_id"] = np.arange(1, len(gdf) + 1).astype(str)

    gdf = build_disambiguated_glacier_names(gdf)
    return gdf


def glacier_names_from_shapefile(
    shp_path: Path, name_col: str = GLACIER_NAME_COL
) -> list[str]:
    gdf = gpd.read_file(shp_path)

    if name_col not in gdf.columns:
        raise ValueError(
            f"Column '{name_col}' not found. Available columns: {list(gdf.columns)}"
        )

    if DROP_NULL_NAMES:
        gdf = gdf[gdf[name_col].notna()].copy()

    if GLACIER_ID_COL in gdf.columns:
        gdf["glacier_id"] = gdf[GLACIER_ID_COL].astype(str)
    else:
        gdf["glacier_id"] = np.arange(1, len(gdf) + 1).astype(str)

    gdf = build_disambiguated_glacier_names(gdf)
    return sorted(gdf["glacier_name"].dropna().astype(str).unique().tolist())


def sample_basin_ids(
    ds: rasterio.io.DatasetReader, gdf_points: gpd.GeoDataFrame
) -> np.ndarray:
    coords = [(geom.x, geom.y) for geom in gdf_points.geometry]
    vals = list(ds.sample(coords))
    basin_ids = np.array(
        [int(v[0]) if v[0] is not None else 0 for v in vals], dtype=np.int64
    )

    if ds.nodata is not None:
        basin_ids[basin_ids == int(ds.nodata)] = 0

    basin_ids[np.isin(basin_ids, list(NODATA_VALUES))] = 0
    basin_ids[basin_ids < 0] = 0
    return basin_ids


def make_histogram(
    values,
    glacier_name: str,
    out_png: Path,
    most_likely_area_km2: float | None = None,
    hist_color: str = DEFAULT_HIST_COLOR,
) -> None:
    vals = np.asarray(values, dtype=float)
    vals = vals[np.isfinite(vals)]

    if vals.size == 0:
        return

    vmin = float(np.min(vals))
    vmax = float(np.max(vals))
    vmed = float(np.median(vals))
    vmean = float(np.mean(vals))

    fig, ax = plt.subplots(figsize=(11, 6))
    ax.hist(vals, bins=HIST_BINS, color=hist_color)

    legend = None
    if most_likely_area_km2 is not None and np.isfinite(most_likely_area_km2):
        ax.axvline(
            x=most_likely_area_km2,
            color="orange",
            linestyle="--",
            linewidth=2.5,
            zorder=10,
            label=f"Most likely basin ({most_likely_area_km2:.1f} km²)",
        )
        legend = ax.legend(
            loc="upper left",
            bbox_to_anchor=(0.02, 0.98),
            borderaxespad=0.0,
            fontsize=HIST_LEGEND_FONTSIZE,
            frameon=True,
        )

    ax.set_xlabel("Basin area (km²)", fontsize=HIST_LABEL_FONTSIZE)
    ax.set_ylabel("Count (ensemble runs)", fontsize=HIST_LABEL_FONTSIZE)
    ax.set_title(
        f"{glacier_name}: Basin area across ensemble runs",
        fontsize=HIST_TITLE_FONTSIZE,
    )
    ax.tick_params(axis="both", labelsize=HIST_TICK_FONTSIZE)
    ax.grid(True, alpha=0.25)

    stats_txt = (
        f"Runs: {len(vals)}\n"
        f"Min: {vmin:.1f} km²\n"
        f"Median: {vmed:.1f} km²\n"
        f"Mean: {vmean:.1f} km²\n"
        f"Max: {vmax:.1f} km²"
    )

    stats_y = 0.98
    if legend is not None:
        fig.canvas.draw()
        legend_bbox = legend.get_window_extent(fig.canvas.get_renderer())
        legend_bbox_axes = legend_bbox.transformed(ax.transAxes.inverted())
        stats_y = legend_bbox_axes.y0 - 0.04

    ax.text(
        0.02,
        stats_y,
        stats_txt,
        transform=ax.transAxes,
        va="top",
        ha="left",
        fontsize=HIST_STATS_FONTSIZE,
        bbox=dict(boxstyle="round", facecolor="white", alpha=0.85),
    )

    fig.tight_layout()
    fig.savefig(out_png, dpi=200)
    plt.close(fig)


def write_basin_certainty_tif(
    glacier_point_row: gpd.GeoDataFrame,
    run_files: list[Path],
    out_tif: Path,
) -> None:
    """
    For each ensemble raster:
      - sample basin ID at glacier point
      - mark all pixels belonging to that basin
    Output pixel values in [0,1] = fraction of runs where that pixel belongs
    to the glacier's basin.
    """
    if len(run_files) == 0:
        raise ValueError("run_files is empty")

    with rasterio.open(run_files[0]) as ref:
        meta = ref.meta.copy()
        h, w = ref.height, ref.width
        count_hits = np.zeros((h, w), dtype=np.uint16)
        x = glacier_point_row.geometry.iloc[0].x
        y = glacier_point_row.geometry.iloc[0].y

    n_runs = 0

    for fp in run_files:
        with rasterio.open(fp) as ds:
            basin_id = int(next(ds.sample([(x, y)]))[0])

            if ds.nodata is not None and basin_id == int(ds.nodata):
                basin_id = 0
            if basin_id in NODATA_VALUES or basin_id <= 0:
                n_runs += 1
                continue

            arr = ds.read(1)

            if ds.nodata is not None:
                arr = np.where(arr == ds.nodata, 0, arr)

            arr = arr.astype(np.int64)
            arr[arr < 0] = 0

            mask = arr == basin_id
            count_hits += mask.astype(np.uint16)
            n_runs += 1

    prob = count_hits.astype(np.float32) / float(n_runs)

    meta.update(
        dtype="float32",
        count=1,
        nodata=0.0,
        compress="LZW",
    )

    out_tif.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_tif, "w", **meta) as dst:
        dst.write(prob, 1)


def write_most_likely_basin_outline(
    glacier_point_row: gpd.GeoDataFrame,
    most_likely_tif: Path,
    out_shp: Path,
) -> int | None:
    """
    Find the basin containing the glacier point in most_likely_tif,
    polygonize that basin, dissolve to one geometry, and save shapefile.
    Returns basin ID.
    """
    out_shp.parent.mkdir(parents=True, exist_ok=True)

    with rasterio.open(most_likely_tif) as ds:
        x = glacier_point_row.geometry.iloc[0].x
        y = glacier_point_row.geometry.iloc[0].y

        basin_id = int(next(ds.sample([(x, y)]))[0])

        if ds.nodata is not None and basin_id == int(ds.nodata):
            basin_id = 0
        if basin_id in NODATA_VALUES or basin_id <= 0:
            return None

        arr = ds.read(1)
        if ds.nodata is not None:
            arr = np.where(arr == ds.nodata, 0, arr)

        mask = arr == basin_id
        if not np.any(mask):
            return None

        geoms = []
        for geom, val in features.shapes(
            mask.astype(np.uint8),
            mask=mask,
            transform=ds.transform,
        ):
            if int(val) == 1:
                geoms.append(shape(geom))

        if not geoms:
            return None

        dissolved = unary_union(geoms)

        gdf = gpd.GeoDataFrame(
            {
                "glacier": [str(glacier_point_row["glacier_name"].iloc[0])],
                "basin_id": [basin_id],
            },
            geometry=[dissolved],
            crs=ds.crs,
        )
        gdf.to_file(out_shp, driver="ESRI Shapefile")

    return basin_id


def process_one_glacier_feature(
    target: gpd.GeoDataFrame,
    all_gdf: gpd.GeoDataFrame,
    run_files: list[Path],
    most_likely_tif: Path,
    hist_color: str = DEFAULT_HIST_COLOR,
) -> None:
    glacier_name = str(target["glacier_name"].iloc[0])
    target_idx = target.index[0]
    target_id = str(target["glacier_id"].iloc[0])

    per_run_rows = []
    partner_counts = Counter()

    print(f"[{glacier_name}] ensemble runs: {len(run_files)}")

    for run_idx, fp in enumerate(run_files, start=1):
        with rasterio.open(fp) as ds:
            basin_ids_all = sample_basin_ids(ds, all_gdf)
            area_map = basin_areas_km2(ds)

        target_basin_id = int(basin_ids_all[target_idx])

        if target_basin_id <= 0:
            row = {
                "run_index": run_idx,
                "raster": str(fp),
                "glacier_id": target_id,
                "glacier_name": glacier_name,
                "basin_id": 0,
                "basin_area_km2": np.nan,
                "n_glaciers_same_basin": 0,
                "other_glaciers_same_basin": "",
                "all_glaciers_same_basin": "",
            }
            per_run_rows.append(row)
            continue

        same_idx = np.where(basin_ids_all == target_basin_id)[0].tolist()
        same_names = sorted(all_gdf.iloc[same_idx]["glacier_name"].astype(str).tolist())
        other_names = sorted([n for n in same_names if n != glacier_name])

        for other in other_names:
            partner_counts[other] += 1

        row = {
            "run_index": run_idx,
            "raster": str(fp),
            "glacier_id": target_id,
            "glacier_name": glacier_name,
            "basin_id": target_basin_id,
            "basin_area_km2": float(area_map.get(target_basin_id, np.nan)),
            "n_glaciers_same_basin": len(same_names),
            "other_glaciers_same_basin": "; ".join(other_names),
            "all_glaciers_same_basin": "; ".join(same_names),
        }
        per_run_rows.append(row)

        if run_idx % 25 == 0 or run_idx == len(run_files):
            print(f"[{glacier_name}] processed {run_idx}/{len(run_files)}")

    df = pd.DataFrame(per_run_rows)

    # Basin area from basins_most_likely_merged.tif for this glacier point
    with rasterio.open(most_likely_tif) as ds_ml:
        x = target.geometry.iloc[0].x
        y = target.geometry.iloc[0].y

        ml_basin_id = int(next(ds_ml.sample([(x, y)]))[0])

        if ds_ml.nodata is not None and ml_basin_id == int(ds_ml.nodata):
            ml_basin_id = 0

        if ml_basin_id in NODATA_VALUES or ml_basin_id <= 0:
            most_likely_area_km2 = np.nan
        else:
            ml_area_map = basin_areas_km2(ds_ml)
            most_likely_area_km2 = float(ml_area_map.get(ml_basin_id, np.nan))

    print(f"[{glacier_name}] most likely basin id: {ml_basin_id}")
    print(f"[{glacier_name}] most likely basin area: {most_likely_area_km2}")

    safe_name = sanitize_filename(glacier_name)
    glacier_dir = OUT_DIR / safe_name
    glacier_dir.mkdir(parents=True, exist_ok=True)

    out_csv = glacier_dir / f"ensemble_basin_runs_{safe_name}.csv"
    df.to_csv(out_csv, index=False)

    out_png = glacier_dir / f"ensemble_basin_area_hist_{safe_name}.png"
    make_histogram(
        df["basin_area_km2"].values,
        glacier_name,
        out_png,
        most_likely_area_km2=most_likely_area_km2,
        hist_color=hist_color,
    )

    co_df = pd.DataFrame(
        {
            "other_glacier": list(partner_counts.keys()),
            "same_basin_n_runs": list(partner_counts.values()),
        }
    )

    if len(co_df):
        co_df = co_df.sort_values(
            ["same_basin_n_runs", "other_glacier"],
            ascending=[False, True],
        )
        co_df["same_basin_fraction"] = co_df["same_basin_n_runs"] / len(run_files)
    else:
        co_df = pd.DataFrame(
            columns=["other_glacier", "same_basin_n_runs", "same_basin_fraction"]
        )

    out_co_csv = glacier_dir / f"same_basin_partners_{safe_name}.csv"
    co_df.to_csv(out_co_csv, index=False)

    area_vals = df["basin_area_km2"].dropna().values
    summary = pd.DataFrame(
        [
            {
                "glacier_id": target_id,
                "glacier_name": glacier_name,
                "n_runs_total": len(df),
                "n_runs_in_basin": int(df["basin_id"].gt(0).sum()),
                "n_runs_outside_basin": int(df["basin_id"].le(0).sum()),
                "median_basin_area_km2": float(np.median(area_vals)) if len(area_vals) else np.nan,
                "mean_basin_area_km2": float(np.mean(area_vals)) if len(area_vals) else np.nan,
                "min_basin_area_km2": float(np.min(area_vals)) if len(area_vals) else np.nan,
                "max_basin_area_km2": float(np.max(area_vals)) if len(area_vals) else np.nan,
                "n_unique_basin_ids": int(df.loc[df["basin_id"] > 0, "basin_id"].nunique()),
                "n_unique_partner_glaciers": int(len(co_df)),
                "top_partner": co_df.iloc[0]["other_glacier"] if len(co_df) else "",
                "top_partner_n_runs": int(co_df.iloc[0]["same_basin_n_runs"]) if len(co_df) else 0,
                "most_likely_basin_id": int(ml_basin_id) if ml_basin_id > 0 else 0,
                "most_likely_basin_area_km2": float(most_likely_area_km2)
                if np.isfinite(most_likely_area_km2)
                else np.nan,
            }
        ]
    )
    out_summary_csv = glacier_dir / f"summary_{safe_name}.csv"
    summary.to_csv(out_summary_csv, index=False)

    touch_dir = glacier_dir / "touchpoly_products"
    touch_dir.mkdir(parents=True, exist_ok=True)

    out_certainty_tif = touch_dir / f"basin_certainty_{safe_name}.tif"
    write_basin_certainty_tif(
        glacier_point_row=target,
        run_files=run_files,
        out_tif=out_certainty_tif,
    )

    out_outline_shp = touch_dir / f"basin_outline_most_likely_{safe_name}.shp"
    outline_basin_id = write_most_likely_basin_outline(
        glacier_point_row=target,
        most_likely_tif=most_likely_tif,
        out_shp=out_outline_shp,
    )

    print(f"[{glacier_name}] wrote: {out_csv}")
    print(f"[{glacier_name}] wrote: {out_png}")
    print(f"[{glacier_name}] wrote: {out_co_csv}")
    print(f"[{glacier_name}] wrote: {out_summary_csv}")
    print(f"[{glacier_name}] wrote: {out_certainty_tif}")
    if outline_basin_id is not None:
        print(f"[{glacier_name}] wrote: {out_outline_shp} (basin_id={outline_basin_id})")
    else:
        print(f"[{glacier_name}] no valid basin found in {most_likely_tif.name}")


def process_glacier(glacier_name: str, hist_color: str = DEFAULT_HIST_COLOR) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    run_files = sorted(MC_DIR.glob(BASIN_GLOB))
    if not run_files:
        raise FileNotFoundError(f"No ensemble rasters found: {MC_DIR}/{BASIN_GLOB}")

    most_likely_tif = MC_DIR / MOST_LIKELY_NAME
    if not most_likely_tif.exists():
        raise FileNotFoundError(f"Missing {MOST_LIKELY_NAME} in {MC_DIR}")

    with rasterio.open(run_files[0]) as ds0:
        all_gdf = load_glacier_points(GLACIER_SHP, ds0.crs)

    # First try exact match on disambiguated glacier_name
    target_gdf = all_gdf[all_gdf["glacier_name"] == glacier_name].copy()

    # If no exact glacier_name match, fall back to Official_n and process all matches
    if target_gdf.empty:
        target_gdf = all_gdf[all_gdf["official_name"] == glacier_name].copy()

    if target_gdf.empty:
        print(f"[{glacier_name}] SKIP: not found in shapefile")
        return

    print(f"[{glacier_name}] matched {len(target_gdf)} feature(s)")

    for _, row in target_gdf.iterrows():
        target = all_gdf.loc[[row.name]].copy()
        process_one_glacier_feature(
            target=target,
            all_gdf=all_gdf,
            run_files=run_files,
            most_likely_tif=most_likely_tif,
            hist_color=hist_color,
        )


def main():
    if len(sys.argv) == 2 and sys.argv[1] == "--list":
        for n in glacier_names_from_shapefile(GLACIER_SHP):
            print(n)
        return

    if len(sys.argv) not in (2, 3):
        print("Usage:")
        print("  python batch_glacier_basin_ensemble_points.py '<glacier name>'")
        print("  python batch_glacier_basin_ensemble_points.py '<glacier name>' '<hist color>'")
        print("  python batch_glacier_basin_ensemble_points.py --list")
        sys.exit(1)

    glacier_name = sys.argv[1]
    hist_color = sys.argv[2] if len(sys.argv) == 3 else DEFAULT_HIST_COLOR

    process_glacier(glacier_name, hist_color=hist_color)


if __name__ == "__main__":
    main()
