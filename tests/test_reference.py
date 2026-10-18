from __future__ import annotations

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from reference import (
    merged_member_files,
    resolve_version0_reference,
    target_name,
    validate_version0_reference,
)


def _write(path, values, *, pixel_m=10_000):
    values = np.asarray(values, dtype="int32")
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=values.shape[0],
        width=values.shape[1],
        count=1,
        dtype="int32",
        nodata=0,
        crs="EPSG:3413",
        transform=from_origin(0, values.shape[0] * pixel_m, pixel_m, pixel_m),
    ) as dst:
        dst.write(values, 1)


def test_target_specific_reference_is_required(tmp_path, monkeypatch):
    reference = tmp_path / "hybrid_100m_v0.tif"
    _write(reference, [[1, 1, 1], [2, 2, 2]])
    monkeypatch.chdir(tmp_path)
    config = {"references": {"hybrid_100m": reference.name}}
    assert target_name("hybrid", 100) == "hybrid_100m"
    assert resolve_version0_reference(
        config,
        tmp_path / "config.toml",
        dem_mode="hybrid",
        resolution_m=100,
    ) == reference.resolve()
    with pytest.raises(KeyError, match="references.bed_100m"):
        resolve_version0_reference(
            config,
            tmp_path / "config.toml",
            dem_mode="bed",
            resolution_m=100,
        )


def test_reference_must_be_contiguous_and_at_least_250_km2(tmp_path):
    valid = tmp_path / "valid.tif"
    _write(valid, [[1, 1, 1, 2, 2, 2]])  # 3 * 100 km² per basin
    report = validate_version0_reference(valid)
    assert report["basin_count"] == 2
    assert report["minimum_basin_area_km2"] == 300.0

    undersized = tmp_path / "undersized.tif"
    _write(undersized, [[1, 1, 2, 2, 2]])
    with pytest.raises(ValueError, match="below 250"):
        validate_version0_reference(undersized)

    non_contiguous = tmp_path / "non_contiguous.tif"
    _write(non_contiguous, [[1, 1, 1, 3, 3, 3]])
    with pytest.raises(ValueError, match="contiguous"):
        validate_version0_reference(non_contiguous)


def test_reference_minimum_is_configurable(tmp_path):
    reference = tmp_path / "reference.tif"
    _write(reference, [[1, 1, 2, 2]])  # 200 km² per basin

    report = validate_version0_reference(reference, minimum_basin_km2=200)
    assert report["requested_minimum_basin_area_km2"] == 200
    with pytest.raises(ValueError, match="must be positive"):
        validate_version0_reference(reference, minimum_basin_km2=0)


def test_merged_members_must_be_exactly_one_through_n(tmp_path):
    for index in (1, 3):
        _write(tmp_path / f"basins_hydro_ens_{index:03d}.tif", [[1, 1, 1]])
    with pytest.raises(ValueError, match=r"missing=\[2\]"):
        merged_member_files(tmp_path, expected_members=3)
    _write(tmp_path / "basins_hydro_ens_002.tif", [[1, 1, 1]])
    assert [path.name for path in merged_member_files(tmp_path, expected_members=3)] == [
        "basins_hydro_ens_001.tif",
        "basins_hydro_ens_002.tif",
        "basins_hydro_ens_003.tif",
    ]
