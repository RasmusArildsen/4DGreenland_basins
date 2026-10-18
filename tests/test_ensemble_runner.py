from pathlib import Path

from ensemble_runner import load_runtime


def _config(tmp_path: Path, mode: str) -> dict:
    return {
        "run": {"dem_mode": mode, "dem_res_m": 100, "n_members": 3},
        "grass": {},
        "hydrology": {"stream_threshold_100m": 10, "corr_pix_100m": 5},
        "inputs": {
            "dem_100m": "surface.tif",
            "var_100m": "variance.tif",
            "ice_mask": "mask.gpkg",
        },
        "outputs": {f"{mode}_dir_100m": str(tmp_path / mode)},
    }


def test_surface_runtime_needs_only_surface_inputs(tmp_path: Path):
    runtime = load_runtime(_config(tmp_path, "surface"))
    assert runtime["dem_mode"] == "surface"
    assert runtime["OUT"] == tmp_path / "surface"
    assert "BED" not in runtime


def test_hybrid_runtime_adds_bed_and_transition_inputs(tmp_path: Path):
    config = _config(tmp_path, "hybrid")
    config["inputs"].update(
        bed_100m="bed.tif",
        bed_err_100m="bed_error.tif",
        surf_mask_2000="transition.tif",
    )
    runtime = load_runtime(config)
    assert runtime["BED"] == "bed.tif"
    assert runtime["SURF_2000"] == "transition.tif"
