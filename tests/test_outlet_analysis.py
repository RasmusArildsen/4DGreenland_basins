from __future__ import annotations

import csv
import json

import numpy as np
import rasterio
from rasterio.transform import from_origin

from outlet_analysis import analyse_outlet


def _write_raster(path, values):
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=4,
        width=4,
        count=1,
        dtype="int32",
        crs="EPSG:3413",
        transform=from_origin(0, 4000, 1000, 1000),
        nodata=0,
    ) as dataset:
        dataset.write(np.asarray(values, dtype="int32"), 1)


def test_analyse_outlet_writes_distribution_and_spatial_products(tmp_path):
    ensemble = tmp_path / "members"
    ensemble.mkdir()
    _write_raster(
        ensemble / "basins_hydro_ens_001.tif",
        [[1, 1, 0, 0], [1, 1, 0, 0], [0, 0, 2, 2], [0, 0, 2, 2]],
    )
    _write_raster(
        ensemble / "basins_hydro_ens_002.tif",
        [[1, 1, 1, 0], [1, 1, 1, 0], [0, 0, 2, 2], [0, 0, 2, 2]],
    )
    _write_raster(
        ensemble / "basins_hydro_ens_003.tif",
        [[1, 1, 1, 1], [1, 1, 1, 1], [0, 0, 2, 2], [0, 0, 2, 2]],
    )
    most_likely = tmp_path / "basins_most_likely.tif"
    _write_raster(
        most_likely,
        [[7, 7, 7, 0], [7, 7, 7, 0], [0, 0, 8, 8], [0, 0, 8, 8]],
    )

    output = tmp_path / "analysis"
    summary = analyse_outlet(
        ensemble,
        x=500,
        y=3500,
        outlet_name="Test outlet",
        most_likely=most_likely,
        output_dir=output,
        chunk_rows=2,
    )

    assert summary["minimum_area_km2"] == 4.0
    assert summary["median_area_km2"] == 6.0
    assert summary["maximum_area_km2"] == 8.0
    assert summary["most_likely_basin_area_km2"] == 6.0

    with (output / "ensemble_member_areas.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert [float(row["area_km2"]) for row in rows] == [4.0, 6.0, 8.0]

    with rasterio.open(output / "basin_membership_probability.tif") as dataset:
        probability = dataset.read(1)
    np.testing.assert_allclose(probability[0], [1, 1, 2 / 3, 1 / 3])

    with rasterio.open(output / "minimum_area_basin.tif") as dataset:
        assert int(dataset.read(1).sum()) == 4
    with rasterio.open(output / "maximum_area_basin.tif") as dataset:
        assert int(dataset.read(1).sum()) == 8
    with rasterio.open(output / "most_likely_basin.tif") as dataset:
        assert int(dataset.read(1).sum()) == 6

    saved_summary = json.loads((output / "summary.json").read_text())
    assert saved_summary["ensemble_members"] == 3
    assert (output / "basin_area_distribution.png").stat().st_size > 0
