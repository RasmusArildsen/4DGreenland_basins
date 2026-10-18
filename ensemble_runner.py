"""Generate raw basin members for one routing mode and resolution."""

from __future__ import annotations

import os
import random
from pathlib import Path
from typing import Any

import basin_core as bc

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


## Read and validate the small TOML configuration.
def load_config(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Config not found: {path}")
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _required(config: dict[str, Any], section: str, key: str) -> Any:
    try:
        return config[section][key]
    except KeyError as error:
        raise KeyError(f"Missing config key: {section}.{key}") from error


## Read only the target settings needed by postprocessing.
def load_target(config: dict[str, Any]) -> dict[str, Any]:
    run = config["run"]
    mode = str(run["dem_mode"]).lower()
    resolution = int(run["dem_res_m"])
    n_members = int(run["n_members"])
    if mode not in {"surface", "bed", "hybrid"}:
        raise ValueError("run.dem_mode must be surface, bed, or hybrid")
    if resolution not in {100, 500}:
        raise ValueError("run.dem_res_m must be 100 or 500")
    if n_members < 1:
        raise ValueError("run.n_members must be positive")

    output = Path(_required(config, "outputs", f"{mode}_dir_{resolution}m"))
    return {
        "dem_mode": mode,
        "res": resolution,
        "n_members": n_members,
        "OUT": output,
    }


def load_runtime(config: dict[str, Any]) -> dict[str, Any]:
    target = load_target(config)
    run = config["run"]
    mode = target["dem_mode"]
    resolution = target["res"]
    output = target["OUT"]
    output.mkdir(parents=True, exist_ok=True)
    inputs = config["inputs"]
    hydrology = config["hydrology"]

    runtime = {
        **target,
        "cfg": config,
        "seed_base": int(run.get("seed_base", 0)),
        "k_min": float(run.get("k_min", 1.0)),
        "k_max": float(run.get("k_max", 1.0)),
        "DEM": str(inputs[f"dem_{resolution}m"]),
        "VAR": str(inputs[f"var_{resolution}m"]),
        "ICE_MASK": str(inputs["ice_mask"]),
        "OUT": output,
        "STREAM_THRESH": int(hydrology[f"stream_threshold_{resolution}m"]),
        "CORR_PIX": int(hydrology[f"corr_pix_{resolution}m"]),
        "DO_FILL_DIR": bool(hydrology.get("do_fill_dir", True)),
        "WS_MEM": int(hydrology.get("watershed_memory", 600)),
        "WS_LOWMEM": bool(hydrology.get("watershed_lowmem", True)),
        "SAVE_MEMBER_DIAGNOSTICS": bool(
            config["outputs"].get("save_member_diagnostics", False)
        ),
        "grass_cfg": config["grass"],
    }
    if mode in {"bed", "hybrid"}:
        runtime.update(
            BED=str(inputs[f"bed_{resolution}m"]),
            BED_ERR=str(inputs[f"bed_err_{resolution}m"]),
            BED_ERR_IS_VAR=bool(inputs.get(f"bed_err_is_variance_{resolution}m", False)),
        )
    if mode == "hybrid":
        runtime["SURF_2000"] = str(inputs["surf_mask_2000"])
    return runtime


## Start one isolated GRASS mapset for the local process or HPC array task.
def init_grass(runtime: dict[str, Any]):
    bc.setup_grass_env()
    job_suffix = "_".join(
        value
        for value in (os.environ.get("LSB_JOBID"), os.environ.get("LSB_JOBINDEX"))
        if value
    )
    mapset = f"{runtime['grass_cfg'].get('mapset_prefix', 'MC_WORK')}_{runtime['res']}m"
    if job_suffix:
        mapset = f"{mapset}_{job_suffix}"
    bc.start_grass_from_raster(
        runtime["DEM"],
        location=str(runtime["grass_cfg"].get("location", "dem_loc")),
        mapset=mapset,
        gisdbase=runtime["grass_cfg"].get("gisdbase"),
    )
    for module in runtime["grass_cfg"].get("ensure_addons", []):
        bc.ensure_grass_addon(module)
    return bc.gs


## Import only the inputs required by the selected routing mode.
def _import_inputs(runtime: dict[str, Any]) -> tuple[str, set[str], set[str]]:
    dem, mask = bc.prepare_dem_and_mask(runtime["DEM"], runtime["ICE_MASK"])
    bc.import_raster_native(runtime["VAR"], "dem_var")
    rasters = {dem, "dem_var", mask, "MASK"}
    if runtime["dem_mode"] in {"bed", "hybrid"}:
        bc.import_raster_native(runtime["BED"], "bed_raster")
        bc.import_raster_native(runtime["BED_ERR"], "bed_err")
        rasters |= {"bed_raster", "bed_err"}
    if runtime["dem_mode"] == "hybrid":
        bc.import_raster_native(runtime["SURF_2000"], "surf_mask_2000")
        rasters.add("surf_mask_2000")
    return dem, rasters, {"ice_mask_vec"}


## Make a spatially correlated random-error field.
def _noise(
    member: int,
    base: str,
    correlation_pixels: int,
    name: str,
    seed: int,
) -> str:
    bc.gs.run_command("r.mask", flags="r", errors="status")
    bc.gs.run_command("g.region", raster=base)
    raw = f"noise_raw_{name}_{member:03d}"
    output = f"noise_{name}_{member:03d}"
    bc.gs.run_command(
        "r.surf.gauss",
        output=raw,
        mean=0.0,
        sigma=1.0,
        seed=seed,
        overwrite=True,
    )
    cellsize = float(bc.gs.parse_command("g.region", flags="g")["ewres"])
    bc.gs.run_command(
        "r.resamp.filter",
        input=raw,
        output=output,
        filter="gauss,box",
        radius=f"{correlation_pixels * cellsize},{2 * correlation_pixels * cellsize}",
        overwrite=True,
    )
    return output


def _surface_member(
    member: int,
    base: str,
    variance: str,
    correlation: int,
    seed: int,
) -> str:
    noise = _noise(member, base, correlation, "surface", seed)
    output = f"surface_{member:03d}"
    bc.safe(
        f"{output} = if(isnull({base}) || isnull({variance}), null(), "
        f"{base} + {noise} * sqrt({variance}))"
    )
    return output


def _bed_member(
    member: int,
    base: str,
    error: str,
    correlation: int,
    error_is_variance: bool,
    seed: int,
) -> str:
    noise = _noise(member, base, correlation, "bed", seed)
    scale = f"sqrt({error})" if error_is_variance else error
    output = f"bed_{member:03d}"
    bc.safe(
        f"{output} = if(isnull({base}) || isnull({error}), null(), "
        f"{base} + {noise} * ({scale}))"
    )
    return output


## Build the surface, hydraulic-potential, or hybrid routing raster.
def _routing_raster(runtime: dict[str, Any], member: int, k: float, base_dem: str) -> str:
    seed = runtime["seed_base"] + 2 * member
    surface = _surface_member(member, base_dem, "dem_var", runtime["CORR_PIX"], seed)
    if runtime["dem_mode"] == "surface":
        return surface

    bed = _bed_member(
        member,
        "bed_raster",
        "bed_err",
        runtime["CORR_PIX"],
        runtime["BED_ERR_IS_VAR"],
        seed + 1,
    )
    tag = f"_{member:03d}"
    if runtime["dem_mode"] == "bed":
        return bc.build_bed_mode_dem(bed_dem=bed, surface_dem=surface, tag=tag, k=k)
    return bc.build_hybrid_mode_dem(
        surface_dem=surface,
        bed_dem=bed,
        inside_mask_raster="surf_mask_2000",
        tag=tag,
        k=k,
    )


def _maps(map_type: str) -> set[str]:
    listing = bc.gs.read_command("g.list", type=map_type, separator="newline")
    return {name.strip() for name in listing.splitlines() if name.strip()}


def _remove_new_maps(before: set[str], keep: set[str], map_type: str) -> None:
    names = sorted((_maps(map_type) - before) - keep)
    if names:
        bc.gs.run_command("g.remove", flags="f", type=map_type, name=",".join(names))


## Public entry point: create every missing raw member in the requested range.
def run_ensemble_stage(
    config_path: str | Path,
    start: int | None = None,
    end: int | None = None,
) -> int:
    runtime = load_runtime(load_config(config_path))
    first = 1 if start is None else int(start)
    last = runtime["n_members"] if end is None else int(end)
    if not 1 <= first <= last <= runtime["n_members"]:
        raise ValueError(f"Member range must be within 1..{runtime['n_members']}")

    wanted = range(first, last + 1)
    missing = [
        member
        for member in wanted
        if not (runtime["OUT"] / f"basins_hydro_ens_{member:03d}.tif").exists()
    ]
    if not missing:
        print(f"INFO: Members {first:03d}-{last:03d} already exist in {runtime['OUT']}")
        return 0

    init_grass(runtime)
    base_dem, keep_rasters, keep_vectors = _import_inputs(runtime)
    for member in missing:
        print(f"\nMember {member:03d}/{runtime['n_members']} ({runtime['dem_mode']}, {runtime['res']}m)")
        k = random.Random(runtime["seed_base"] + member).uniform(
            runtime["k_min"], runtime["k_max"]
        )
        print(f"INFO: Sliding factor k = {k:.3f}")
        raster_before = _maps("raster")
        vector_before = _maps("vector")
        routing = _routing_raster(runtime, member, k, base_dem)
        bc.run_hydro_for_dem(
            hydro_dem=routing,
            out_dir=str(runtime["OUT"]),
            stream_threshold=runtime["STREAM_THRESH"],
            run_idx=member,
            watershed_memory=runtime["WS_MEM"],
            watershed_lowmem=runtime["WS_LOWMEM"],
            do_fill_dir=runtime["DO_FILL_DIR"],
            export_diagnostics=runtime["SAVE_MEMBER_DIAGNOSTICS"],
        )
        _remove_new_maps(raster_before, keep_rasters, "raster")
        _remove_new_maps(vector_before, keep_vectors, "vector")

    print("\nEnsemble stage complete.")
    return 0
