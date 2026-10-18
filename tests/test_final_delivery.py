from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

from final_delivery import build_final_delivery, extract_individual_layers


def _raster(path: Path, values: list[list[int]]) -> Path:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=4,
        height=2,
        count=1,
        dtype="int32",
        crs="EPSG:3413",
        transform=from_origin(0, 20_000, 10_000, 10_000),
        nodata=0,
    ) as dst:
        dst.write(np.asarray(values, dtype=np.int32), 1)
    return path


def test_final_delivery_uses_fixed_basin_ids(tmp_path: Path):
    output = tmp_path / "members"
    merged = output / "merged_members"
    merged.mkdir(parents=True)
    final = _raster(tmp_path / "final.tif", [[1, 1, 2, 2], [1, 1, 2, 2]])
    _raster(merged / "basins_hydro_ens_001.tif", [[10, 10, 20, 20], [10, 10, 20, 20]])
    _raster(merged / "basins_hydro_ens_002.tif", [[10, 10, 20, 20], [10, 20, 20, 20]])
    _raster(merged / "basins_hydro_ens_003.tif", [[10, 10, 20, 20], [10, 20, 20, 20]])

    vector = tmp_path / "final.gpkg"
    gpd.GeoDataFrame(
        {"basin_id": [1, 2]},
        geometry=[box(0, 0, 20_000, 20_000), box(20_000, 0, 40_000, 20_000)],
        crs="EPSG:3413",
    ).to_file(vector, driver="GPKG")
    config = tmp_path / "config.toml"
    config.write_text(
        f'[run]\ndem_mode = "surface"\ndem_res_m = 100\nn_members = 3\n'
        f'[outputs]\nsurface_dir_100m = "{output}"\n'
        '[postprocess]\nminimum_basin_km2 = 1\nchunk_rows = 1\ndelivery_chunk_rows = 1\n',
        encoding="utf-8",
    )

    delivery = tmp_path / "delivery"
    paths = build_final_delivery(config, final, vector, delivery)
    assert len(paths) == 5
    assert {path.name for path in delivery.iterdir()} == {
        "basins_surf_100m.tif",
        "basins_surf_100m.gpkg",
        "basin_certainty_surf_100m.tif",
        "boundary_probability_surf_100m.tif",
        "basin_membership_probabilities_surf_100m.tif",
    }
    with rasterio.open(paths["basins"]) as src:
        assert src.read(1)[1, 1] == 1
    with rasterio.open(paths["certainty"]) as src:
        assert np.isclose(src.read(1)[1, 1], 1 / 3)
        assert src.tags()["ensemble_members"] == "3"
    with rasterio.open(paths["boundary"]) as src:
        assert np.isclose(src.read(1)[1, 0], 2 / 3)
    with rasterio.open(paths["stack"]) as src:
        assert src.count == 2
        assert src.descriptions == ("basin_id_1", "basin_id_2")
        assert np.isclose(src.read(1)[1, 1], 1 / 3)
        assert np.isclose(src.read(2)[1, 1], 2 / 3)
        assert np.allclose(src.read(1) + src.read(2), 1)

    individual = delivery / "Individual_basin_probabilities"
    assert extract_individual_layers(paths["stack"], individual) == 2
    assert len(list(individual.glob("*.tif"))) == 2
    with rasterio.open(individual / "basin_membership_probability_basin_001_surf_100m.tif") as src:
        assert src.count == 1
        assert np.isclose(src.read(1)[1, 1], 1 / 3)
