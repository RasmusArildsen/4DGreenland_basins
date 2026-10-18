from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.transform import from_origin

from ensemble_products import (
    build_label_maps,
    write_consensus_products,
    write_most_likely_vector,
    write_probability_stack,
)


def _write_raster(path: Path, values: np.ndarray) -> Path:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=values.shape[1],
        height=values.shape[0],
        count=1,
        dtype="int32",
        crs="EPSG:3413",
        transform=from_origin(0, 20_000, 10_000, 10_000),
        nodata=0,
    ) as dst:
        dst.write(values.astype(np.int32), 1)
    return path


def test_products_use_fixed_reference_ids(tmp_path: Path):
    reference = _write_raster(
        tmp_path / "version0.tif",
        np.array([[1, 1, 2, 2], [1, 1, 2, 2]]),
    )
    members = [
        _write_raster(
            tmp_path / "member1.tif",
            np.array([[10, 10, 20, 20], [10, 10, 20, 20]]),
        ),
        _write_raster(
            tmp_path / "member2.tif",
            np.array([[10, 10, 20, 20], [10, 20, 20, 20]]),
        ),
        _write_raster(
            tmp_path / "member3.tif",
            np.array([[7, 7, 8, 8], [7, 7, 8, 8]]),
        ),
    ]

    label_maps, basin_ids, profile, report = build_label_maps(
        members, reference, chunk_rows=1
    )
    assert report["basin_count"] == 2
    products = write_consensus_products(
        members,
        label_maps,
        reference,
        tmp_path,
        profile,
        chunk_rows=1,
        stable_divide_probability=0.8,
    )
    probability_path = write_probability_stack(
        members,
        label_maps,
        basin_ids,
        reference,
        tmp_path,
        profile,
        chunk_rows=1,
    )
    vector_path = write_most_likely_vector(products["most_likely"], tmp_path)

    with rasterio.open(products["most_likely"]) as src:
        assert src.read(1).tolist() == [[1, 1, 2, 2], [1, 1, 2, 2]]
    with rasterio.open(products["certainty"]) as src:
        certainty = src.read(1)
        assert np.isclose(certainty[1, 1], 2 / 3)
        assert np.isclose(certainty[0, 0], 1.0)
    assert "uncertainty" not in products
    assert not (tmp_path / "basins_uncertainty.tif").exists()
    with rasterio.open(probability_path) as src:
        assert src.count == 2
        assert src.descriptions == ("basin_1_probability", "basin_2_probability")
        assert np.isclose(src.read(1)[1, 1], 2 / 3)
        assert np.isclose(src.read(2)[1, 1], 1 / 3)
    basins = gpd.read_file(vector_path)
    assert sorted(basins["basin_id"].tolist()) == [1, 2]


def test_unmerged_member_is_rejected(tmp_path: Path):
    reference = _write_raster(
        tmp_path / "version0.tif",
        np.array([[1, 1, 2, 2], [1, 1, 2, 2]]),
    )
    too_small = _write_raster(
        tmp_path / "member.tif",
        np.array([[10, 10, 20, 20], [10, 30, 20, 20]]),
    )

    try:
        build_label_maps([too_small], reference, chunk_rows=1)
    except ValueError as error:
        assert "not merged to 250" in str(error)
    else:
        raise AssertionError("Expected the 100 km² basin to be rejected")
