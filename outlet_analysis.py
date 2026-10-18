"""Analyse drainage-basin uncertainty for a selected outlet.

The outlet is sampled in every ensemble basin raster.  The basin containing
the outlet is then measured and accumulated into a pixel-wise membership
probability surface.  Results are deliberately written in simple, portable
formats (CSV, JSON, GeoTIFF, and PNG).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from rasterio.warp import transform as transform_coordinates
from rasterio.windows import Window

DEFAULT_PATTERN = "basins_hydro_ens_*.tif"
MOST_LIKELY_CANDIDATES = (
    "basins_most_likely_merged_final.tif",
    "basins_most_likely_merged.tif",
    "basins_most_likely.tif",
)


@dataclass(frozen=True)
class MemberResult:
    member: int
    raster: str
    basin_id: int
    pixel_count: int
    area_km2: float | None


def _windows(dataset: rasterio.io.DatasetReader, chunk_rows: int) -> Iterable[Window]:
    for row_off in range(0, dataset.height, chunk_rows):
        yield Window(0, row_off, dataset.width, min(chunk_rows, dataset.height - row_off))


def _valid_basin_id(value: object, nodata: float | None) -> int:
    try:
        basin_id = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    if basin_id <= 0 or (nodata is not None and basin_id == int(nodata)):
        return 0
    return basin_id


def _sample_basin_id(dataset: rasterio.io.DatasetReader, x: float, y: float) -> int:
    bounds = dataset.bounds
    if not (bounds.left <= x <= bounds.right and bounds.bottom <= y <= bounds.top):
        return 0
    value = next(dataset.sample([(x, y)], masked=True))[0]
    if np.ma.is_masked(value):
        return 0
    return _valid_basin_id(value, dataset.nodata)


def _pixel_area_km2(dataset: rasterio.io.DatasetReader) -> float:
    if dataset.crs is not None and dataset.crs.is_geographic:
        raise ValueError(
            "Basin rasters use a geographic CRS. Reproject them to a metric CRS "
            "before calculating basin area."
        )
    return abs(dataset.transform.a * dataset.transform.e) / 1_000_000.0


def _assert_same_grid(
    dataset: rasterio.io.DatasetReader,
    *,
    shape: tuple[int, int],
    transform: rasterio.Affine,
    crs: rasterio.crs.CRS | None,
    path: Path,
) -> None:
    if (dataset.height, dataset.width) != shape:
        raise ValueError(f"Grid shape differs from the first member: {path}")
    if not dataset.transform.almost_equals(transform):
        raise ValueError(f"Grid transform differs from the first member: {path}")
    if dataset.crs != crs:
        raise ValueError(f"CRS differs from the first member: {path}")


def _write_selected_basin(
    source: Path,
    basin_id: int,
    destination: Path,
    *,
    chunk_rows: int,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(source) as src:
        profile = src.profile.copy()
        profile.update(dtype="uint8", nodata=0, count=1, compress="deflate")
        with rasterio.open(destination, "w", **profile) as dst:
            for window in _windows(src, chunk_rows):
                values = src.read(1, window=window)
                dst.write((values == basin_id).astype("uint8"), 1, window=window)


def _measure_selected_basin(
    source: Path,
    *,
    x: float,
    y: float,
    chunk_rows: int,
) -> tuple[int, int, float | None]:
    with rasterio.open(source) as src:
        basin_id = _sample_basin_id(src, x, y)
        if basin_id == 0:
            return 0, 0, None
        pixels = sum(
            int(np.count_nonzero(src.read(1, window=window) == basin_id))
            for window in _windows(src, chunk_rows)
        )
        return basin_id, pixels, pixels * _pixel_area_km2(src)


def _find_most_likely(ensemble_dir: Path) -> Path | None:
    search_dirs = (ensemble_dir, ensemble_dir.parent)
    for directory in search_dirs:
        for name in MOST_LIKELY_CANDIDATES:
            candidate = directory / name
            if candidate.exists():
                return candidate
    return None


def _write_histogram(
    areas: np.ndarray,
    destination: Path,
    *,
    outlet_name: str,
    most_likely_area: float | None,
) -> None:
    fig, ax = plt.subplots(figsize=(9, 5.5))
    bins = min(35, max(5, int(math.sqrt(len(areas)))))
    ax.hist(areas, bins=bins, color="#2878B5", edgecolor="white", linewidth=0.7)
    ax.axvline(np.median(areas), color="#333333", linestyle=":", linewidth=2, label="Median")
    if most_likely_area is not None:
        ax.axvline(
            most_likely_area,
            color="#E07A1F",
            linestyle="--",
            linewidth=2,
            label="Most-likely basin",
        )
    ax.set(
        title=f"{outlet_name}: basin area across ensemble members",
        xlabel="Basin area (km²)",
        ylabel="Members",
    )
    ax.grid(axis="y", alpha=0.2)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(destination, dpi=200)
    plt.close(fig)


def _write_csv(results: Sequence[MemberResult], destination: Path) -> None:
    with destination.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(asdict(results[0]).keys()))
        writer.writeheader()
        writer.writerows(asdict(row) for row in results)


def analyse_outlet(
    ensemble_dir: str | Path,
    *,
    x: float,
    y: float,
    outlet_name: str = "selected_outlet",
    point_crs: str | None = None,
    basin_pattern: str = DEFAULT_PATTERN,
    most_likely: str | Path | None = None,
    output_dir: str | Path | None = None,
    percentiles: Sequence[float] = (5, 25, 75, 95),
    chunk_rows: int = 256,
) -> dict[str, object]:
    """Create area-distribution and spatial uncertainty products for one outlet."""
    ensemble_dir = Path(ensemble_dir)
    member_paths = sorted(ensemble_dir.glob(basin_pattern))
    if not member_paths:
        raise FileNotFoundError(f"No rasters match {basin_pattern!r} in {ensemble_dir}")
    if chunk_rows < 1:
        raise ValueError("chunk_rows must be positive")
    if any(p < 0 or p > 100 for p in percentiles):
        raise ValueError("percentiles must be between 0 and 100")

    output_dir = Path(output_dir) if output_dir else ensemble_dir / "outlet_analysis" / outlet_name
    output_dir.mkdir(parents=True, exist_ok=True)

    with rasterio.open(member_paths[0]) as reference:
        shape = (reference.height, reference.width)
        grid_transform = reference.transform
        grid_crs = reference.crs
        profile = reference.profile.copy()
        pixel_area = _pixel_area_km2(reference)
    if grid_crs is None:
        raise ValueError("Ensemble rasters must define a CRS")

    if point_crs and rasterio.crs.CRS.from_user_input(point_crs) != grid_crs:
        xs, ys = transform_coordinates(point_crs, grid_crs, [x], [y])
        raster_x, raster_y = float(xs[0]), float(ys[0])
    else:
        raster_x, raster_y = float(x), float(y)

    counter_dtype = np.uint16 if len(member_paths) <= np.iinfo(np.uint16).max else np.uint32
    results: list[MemberResult] = []
    min_selection: tuple[float, Path, int] | None = None
    max_selection: tuple[float, Path, int] | None = None

    with tempfile.TemporaryDirectory(prefix="greenland_outlet_") as temp_dir:
        counter_path = Path(temp_dir) / "membership_counts.dat"
        counts = np.memmap(counter_path, mode="w+", dtype=counter_dtype, shape=shape)
        counts[:] = 0

        for member_number, path in enumerate(member_paths, start=1):
            with rasterio.open(path) as src:
                _assert_same_grid(src, shape=shape, transform=grid_transform, crs=grid_crs, path=path)
                basin_id = _sample_basin_id(src, raster_x, raster_y)
                pixel_count = 0
                if basin_id:
                    for window in _windows(src, chunk_rows):
                        values = src.read(1, window=window)
                        selected = values == basin_id
                        pixel_count += int(np.count_nonzero(selected))
                        row_start = int(window.row_off)
                        row_stop = row_start + int(window.height)
                        counts[row_start:row_stop, :][selected] += 1

            area = pixel_count * pixel_area if basin_id else None
            results.append(MemberResult(member_number, path.name, basin_id, pixel_count, area))
            if area is not None and (min_selection is None or area < min_selection[0]):
                min_selection = (area, path, basin_id)
            if area is not None and (max_selection is None or area > max_selection[0]):
                max_selection = (area, path, basin_id)

        probability_path = output_dir / "basin_membership_probability.tif"
        probability_profile = profile.copy()
        probability_profile.update(dtype="float32", nodata=0.0, count=1, compress="deflate")
        with rasterio.open(probability_path, "w", **probability_profile) as dst:
            for row_start in range(0, shape[0], chunk_rows):
                row_stop = min(row_start + chunk_rows, shape[0])
                window = Window(0, row_start, shape[1], row_stop - row_start)
                probability = counts[row_start:row_stop, :].astype("float32") / len(member_paths)
                dst.write(probability, 1, window=window)
        del counts

    valid_areas = np.asarray([r.area_km2 for r in results if r.area_km2 is not None], dtype=float)
    if valid_areas.size == 0:
        raise ValueError("The outlet does not fall in a valid basin in any ensemble member")

    most_likely_path = Path(most_likely) if most_likely else _find_most_likely(ensemble_dir)
    most_likely_id = 0
    most_likely_pixels = 0
    most_likely_area: float | None = None
    if most_likely_path:
        if not most_likely_path.exists():
            raise FileNotFoundError(f"Most-likely raster not found: {most_likely_path}")
        with rasterio.open(most_likely_path) as most_likely_dataset:
            _assert_same_grid(
                most_likely_dataset,
                shape=shape,
                transform=grid_transform,
                crs=grid_crs,
                path=most_likely_path,
            )
        most_likely_id, most_likely_pixels, most_likely_area = _measure_selected_basin(
            most_likely_path, x=raster_x, y=raster_y, chunk_rows=chunk_rows
        )
        if most_likely_id:
            _write_selected_basin(
                most_likely_path,
                most_likely_id,
                output_dir / "most_likely_basin.tif",
                chunk_rows=chunk_rows,
            )

    if min_selection:
        _write_selected_basin(
            min_selection[1], min_selection[2], output_dir / "minimum_area_basin.tif", chunk_rows=chunk_rows
        )
    if max_selection:
        _write_selected_basin(
            max_selection[1], max_selection[2], output_dir / "maximum_area_basin.tif", chunk_rows=chunk_rows
        )

    percentile_values = np.percentile(valid_areas, percentiles)
    summary: dict[str, object] = {
        "outlet_name": outlet_name,
        "outlet_x_input": x,
        "outlet_y_input": y,
        "outlet_crs_input": point_crs or str(grid_crs),
        "outlet_x_raster": raster_x,
        "outlet_y_raster": raster_y,
        "raster_crs": str(grid_crs),
        "ensemble_members": len(results),
        "valid_members": int(valid_areas.size),
        "invalid_members": len(results) - int(valid_areas.size),
        "minimum_area_km2": float(np.min(valid_areas)),
        "mean_area_km2": float(np.mean(valid_areas)),
        "median_area_km2": float(np.median(valid_areas)),
        "maximum_area_km2": float(np.max(valid_areas)),
        "standard_deviation_km2": float(np.std(valid_areas)),
        "most_likely_basin_id": most_likely_id,
        "most_likely_basin_pixels": most_likely_pixels,
        "most_likely_basin_area_km2": most_likely_area,
        "percentiles_km2": {
            str(float(percentile)): float(value) for percentile, value in zip(percentiles, percentile_values)
        },
        "outputs": {
            "member_areas_csv": "ensemble_member_areas.csv",
            "area_distribution_png": "basin_area_distribution.png",
            "membership_probability_tif": probability_path.name,
            "minimum_area_basin_tif": "minimum_area_basin.tif",
            "maximum_area_basin_tif": "maximum_area_basin.tif",
            "most_likely_basin_tif": "most_likely_basin.tif" if most_likely_id else None,
        },
    }

    _write_csv(results, output_dir / "ensemble_member_areas.csv")
    _write_histogram(
        valid_areas,
        output_dir / "basin_area_distribution.png",
        outlet_name=outlet_name,
        most_likely_area=most_likely_area,
    )
    with (output_dir / "summary.json").open("w", encoding="utf-8") as stream:
        json.dump(summary, stream, indent=2, allow_nan=False)
        stream.write("\n")
    return summary


def _parse_percentiles(value: str) -> list[float]:
    try:
        return [float(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("percentiles must be comma-separated numbers") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure the basin connected to one outlet across an ensemble of basin rasters."
    )
    parser.add_argument("ensemble_dir", type=Path, help="Directory containing ensemble basin GeoTIFFs")
    parser.add_argument(
        "--x", type=float, required=True, help="Outlet x coordinate (longitude for EPSG:4326)"
    )
    parser.add_argument("--y", type=float, required=True, help="Outlet y coordinate (latitude for EPSG:4326)")
    parser.add_argument("--point-crs", help="CRS of --x/--y, for example EPSG:4326; defaults to raster CRS")
    parser.add_argument(
        "--name", default="selected_outlet", help="Name used in the plot and output directory"
    )
    parser.add_argument("--pattern", default=DEFAULT_PATTERN, help="Glob pattern for ensemble basin rasters")
    parser.add_argument("--most-likely", type=Path, help="Optional final most-likely basin raster")
    parser.add_argument("--output-dir", type=Path, help="Output directory")
    parser.add_argument("--percentiles", type=_parse_percentiles, default=[5, 25, 75, 95])
    parser.add_argument("--chunk-rows", type=int, default=256, help="Rows processed in memory at once")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = analyse_outlet(
        args.ensemble_dir,
        x=args.x,
        y=args.y,
        outlet_name=args.name,
        point_crs=args.point_crs,
        basin_pattern=args.pattern,
        most_likely=args.most_likely,
        output_dir=args.output_dir,
        percentiles=args.percentiles,
        chunk_rows=args.chunk_rows,
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
