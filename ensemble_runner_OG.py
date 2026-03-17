from __future__ import annotations

import random
from pathlib import Path
from typing import Any, Dict, Tuple

import basin_core as bc
from basin_core import (
    build_bed_mode_dem,
    build_hybrid_mode_dem,
    ensure_grass_addon,
    export_geotiff,
    import_raster_native,
    prepare_dem_and_mask,
    run_hydro_for_dem,
    setup_grass_env,
    start_grass_from_raster,
)
from ensemble_postprocess import make_perturbed_bed_member, make_perturbed_surface_member

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


# -----------------------------------------------------------------------------
# Config helpers
# -----------------------------------------------------------------------------
def load_config(path: str | Path) -> Dict[str, Any]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    with path.open("rb") as f:
        return tomllib.load(f)


def must(cfg: Dict[str, Any], *keys: str):
    value: Any = cfg
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            raise KeyError(f"Missing config key: {'.'.join(keys)}")
        value = value[key]
    return value


def pick_by_res(cfg: Dict[str, Any], base_key: str, res: int) -> Any:
    section, key = base_key.split(".", 1)
    return must(cfg, section, f"{key}_{res}m")


def output_dirs(cfg: Dict[str, Any], res: int) -> Tuple[Path, Path, Path]:
    out = must(cfg, "outputs")
    surface_dir = Path(out[f"surface_dir_{res}m"])
    bed_dir = Path(out[f"bed_dir_{res}m"])
    hybrid_dir = Path(out[f"hybrid_dir_{res}m"])
    for path in (surface_dir, bed_dir, hybrid_dir):
        path.mkdir(parents=True, exist_ok=True)
    return surface_dir, bed_dir, hybrid_dir


def stream_threshold(cfg: Dict[str, Any], res: int) -> int:
    return int(must(cfg, "hydrology", f"stream_threshold_{res}m"))


def corr_pix(cfg: Dict[str, Any], res: int) -> int:
    return int(must(cfg, "hydrology", f"corr_pix_{res}m"))


# -----------------------------------------------------------------------------
# Path helpers
# -----------------------------------------------------------------------------
def basin_output_path(out_dir: Path, member_idx: int) -> Path:
    return out_dir / f"basins_hydro_ens_{member_idx:03d}.tif"


def stream_output_path(out_dir: Path, member_idx: int) -> Path:
    return out_dir / f"streams_hydro_ens_{member_idx:03d}.tif"


def flowdir_output_path(out_dir: Path, member_idx: int) -> Path:
    return out_dir / f"flowdir_hydro_ens_{member_idx:03d}.tif"


def rename_member_outputs_to_ensemble(out_dir: Path, member_idx: int) -> None:
    pairs = [
        (out_dir / f"basins_hydro_mc_{member_idx:03d}.tif", basin_output_path(out_dir, member_idx)),
        (out_dir / f"streams_hydro_mc_{member_idx:03d}.tif", stream_output_path(out_dir, member_idx)),
        (out_dir / f"flowdir_hydro_mc_{member_idx:03d}.tif", flowdir_output_path(out_dir, member_idx)),
    ]
    for src, dst in pairs:
        if src.exists() and not dst.exists():
            src.rename(dst)


def normalize_existing_outputs(out_dir: Path, start_i: int, end_i: int) -> None:
    for member_idx in range(start_i, end_i + 1):
        rename_member_outputs_to_ensemble(out_dir, member_idx)


def first_missing_member(out_dir: Path, start_i: int, end_i: int) -> int | None:
    for member_idx in range(start_i, end_i + 1):
        if not basin_output_path(out_dir, member_idx).exists():
            return member_idx
    return None


# -----------------------------------------------------------------------------
# Small GRASS/session helpers
# -----------------------------------------------------------------------------
def list_maps(gs, map_type: str) -> set[str]:
    txt = gs.read_command("g.list", type=map_type, separator="newline")
    return {line.strip() for line in txt.splitlines() if line.strip()}


def remove_maps(gs, map_type: str, names: list[str]) -> None:
    if names:
        gs.run_command("g.remove", flags="f", type=map_type, name=",".join(names))


def load_runtime(cfg: Dict[str, Any]) -> Dict[str, Any]:
    run_cfg = must(cfg, "run")
    grass_cfg = must(cfg, "grass")
    hyd_cfg = must(cfg, "hydrology")
    in_cfg = must(cfg, "inputs")
    out_cfg = must(cfg, "outputs")

    dem_mode = str(run_cfg["dem_mode"]).lower()
    res = int(run_cfg["dem_res_m"])
    n_members = int(run_cfg.get("n_members", run_cfg.get("n_mc", 0)))
    start_i = int(run_cfg.get("start_i", 1))
    seed_base = int(run_cfg.get("seed_base", 0))
    k_min = float(run_cfg.get("k_min", 1.0))
    k_max = float(run_cfg.get("k_max", 1.0))

    if dem_mode not in ("surface", "bed", "hybrid"):
        raise ValueError("run.dem_mode must be one of: surface | bed | hybrid")
    if res not in (100, 500):
        raise ValueError("run.dem_res_m must be 100 or 500")
    if n_members < 1:
        raise ValueError("run.n_members must be >= 1")
    if start_i < 1 or start_i > n_members:
        raise ValueError("run.start_i must be between 1 and n_members")

    dem = str(pick_by_res(cfg, "inputs.dem", res))
    var = str(pick_by_res(cfg, "inputs.var", res))
    bed = str(pick_by_res(cfg, "inputs.bed", res))
    bed_err = str(pick_by_res(cfg, "inputs.bed_err", res))
    bed_err_is_var = bool(in_cfg.get(f"bed_err_is_variance_{res}m", False))
    ice_mask = str(in_cfg["ice_mask"])
    surf_2000 = str(in_cfg["surf_mask_2000"])

    out_surf, out_bed, out_hyb = output_dirs(cfg, res)
    out_dir = out_surf if dem_mode == "surface" else out_bed if dem_mode == "bed" else out_hyb

    return {
        "cfg": cfg,
        "dem_mode": dem_mode,
        "res": res,
        "n_members": n_members,
        "start_i": start_i,
        "seed_base": seed_base,
        "k_min": k_min,
        "k_max": k_max,
        "DEM": dem,
        "VAR": var,
        "BED": bed,
        "BED_ERR": bed_err,
        "BED_ERR_IS_VAR": bed_err_is_var,
        "ICE_MASK": ice_mask,
        "SURF_2000": surf_2000,
        "OUT_SURF": out_surf,
        "OUT_BED": out_bed,
        "OUT_HYB": out_hyb,
        "OUT": out_dir,
        "STREAM_THRESH": stream_threshold(cfg, res),
        "CORR_PIX": corr_pix(cfg, res),
        "DO_FILL_DIR": bool(hyd_cfg.get("do_fill_dir", True)),
        "WS_MEM": int(hyd_cfg.get("watershed_memory", 600)),
        "WS_LOWMEM": bool(hyd_cfg.get("watershed_lowmem", True)),
        "SAVE_SURF": bool(out_cfg.get("save_surface_dem", True)),
        "SAVE_BED": bool(out_cfg.get("save_bed_dem", True)),
        "SAVE_PHI_OR_HYBRID": bool(out_cfg.get("save_phi_or_hybrid_dem", True)),
        "grass_cfg": grass_cfg,
    }


def init_grass(rt: Dict[str, Any]):
    setup_grass_env(str(rt["grass_cfg"]["gisbase"]))
    gs = bc.gs
    mapset_name = f"{rt['grass_cfg'].get('mapset_prefix', 'MC_WORK')}_{rt['res']}m"
    start_grass_from_raster(
        rt["DEM"],
        location=str(rt["grass_cfg"].get("location", "dem_loc")),
        mapset=mapset_name,
    )
    for mod in rt["grass_cfg"].get("ensure_addons", []):
        ensure_grass_addon(mod)
    gs.run_command("g.mapset", flags="c", mapset=mapset_name)
    return gs


def import_base_layers(rt: Dict[str, Any]):
    dem_map, ice_mask_rast = prepare_dem_and_mask(rt["DEM"], rt["ICE_MASK"])
    import_raster_native(rt["VAR"], out_name="dem_var")
    import_raster_native(rt["BED"], out_name="bed_raster")
    import_raster_native(rt["BED_ERR"], out_name="bed_err")
    import_raster_native(rt["SURF_2000"], out_name="surf_mask_2000")
    keep_rasters = {dem_map, "dem_var", ice_mask_rast, "MASK", "bed_raster", "bed_err", "surf_mask_2000"}
    keep_vectors = {"ice_mask_vec"}
    return dem_map, keep_rasters, keep_vectors


# -----------------------------------------------------------------------------
# Caching helpers
# -----------------------------------------------------------------------------
def ensure_cached_surface(
    *, member_idx: int, base_dem: str, var_map: str, corr_pix_val: int, cache_dir: Path, save: bool = True
) -> str:
    name = f"dem_mc_{member_idx:03d}"
    tif = cache_dir / f"{name}.tif"
    if tif.exists():
        print(f"✓ Using cached surface DEM: {tif.name}")
        import_raster_native(str(tif), out_name=name)
        return name

    print(f"→ Creating surface DEM perturbation: {name}")
    dem_member = make_perturbed_surface_member(
        member_idx=member_idx,
        base_dem=base_dem,
        var_map=var_map,
        corr_pix=corr_pix_val,
    )
    if save:
        print(f"💾 Caching surface DEM → {tif}")
        export_geotiff(dem_member, tif, gdal_type="Float64", force=True)
    return dem_member


def ensure_cached_bed(
    *,
    member_idx: int,
    base_bed: str,
    err_map: str,
    corr_pix_val: int,
    err_is_variance: bool,
    cache_dir: Path,
    save: bool = True,
) -> str:
    name = f"bed_mc_{member_idx:03d}"
    tif = cache_dir / f"{name}.tif"
    if tif.exists():
        print(f"✓ Using cached bed DEM: {tif.name}")
        import_raster_native(str(tif), out_name=name)
        return name

    print(f"→ Creating bed DEM perturbation: {name}")
    bed_member, _ = make_perturbed_bed_member(
        member_idx=member_idx,
        base_bed=base_bed,
        err_map=err_map,
        corr_pix=corr_pix_val,
        err_is_variance=err_is_variance,
    )
    if save:
        print(f"💾 Caching bed DEM → {tif}")
        export_geotiff(bed_member, tif, gdal_type="Float64", force=True)
    return bed_member


def ensure_cached_phi(
    *, member_idx: int, bed_member: str, surface_member: str, k_i: float, cache_dir: Path, save: bool = True
) -> str:
    tag = f"_{member_idx:03d}"
    name = f"hyd_pot{tag}"
    tif = cache_dir / f"phi_ens_{member_idx:03d}.tif"
    if tif.exists():
        print(f"✓ Using cached phi: {tif.name}")
        import_raster_native(str(tif), out_name=name)
        return name

    print(f"→ Creating phi: {name}")
    phi = build_bed_mode_dem(
        bed_dem=bed_member,
        surface_dem=surface_member,
        tag=tag,
        surface_smooth_size=3,
        k=k_i,
    )
    if save:
        print(f"💾 Caching phi → {tif}")
        export_geotiff(phi, tif, gdal_type="Float64", force=True)
    return phi


def ensure_cached_hybrid(
    *,
    member_idx: int,
    bed_member: str,
    surface_member: str,
    k_i: float,
    inside_mask_raster: str,
    cache_dir: Path,
    save: bool = True,
) -> str:
    tag = f"_{member_idx:03d}"
    name = f"hybrid_dem{tag}"
    tif = cache_dir / f"hybrid_ens_{member_idx:03d}.tif"
    if tif.exists():
        print(f"✓ Using cached hybrid: {tif.name}")
        import_raster_native(str(tif), out_name=name)
        return name

    print(f"→ Creating hybrid: {name}")
    hyb = build_hybrid_mode_dem(
        surface_dem=surface_member,
        bed_dem=bed_member,
        inside_mask_raster=inside_mask_raster,
        tag=tag,
        surface_smooth_size_for_phi=3,
        k=k_i,
    )
    if save:
        print(f"💾 Caching hybrid → {tif}")
        export_geotiff(hyb, tif, gdal_type="Float64", force=True)
    return hyb


# -----------------------------------------------------------------------------
# Main ensemble stage
# -----------------------------------------------------------------------------
def run_ensemble_stage(cfg_path: str | Path) -> int:
    rt = load_runtime(load_config(cfg_path))

    normalize_existing_outputs(rt["OUT"], rt["start_i"], rt["n_members"])
    first_missing = first_missing_member(rt["OUT"], rt["start_i"], rt["n_members"])
    if first_missing is None:
        print(f"✅ All ensemble members {rt['start_i']:03d}–{rt['n_members']:03d} already exist in {rt['OUT']}")
        return 0

    print(f"→ Resuming from ensemble member {first_missing:03d}")

    gs = init_grass(rt)
    dem_map, keep_rasters, keep_vectors = import_base_layers(rt)

    for member_idx in range(rt["start_i"], rt["n_members"] + 1):
        basin_tif = basin_output_path(rt["OUT"], member_idx)
        if basin_tif.exists():
            print(f"✓ Skipping ensemble member {member_idx:03d}: {basin_tif.name} already exists")
            continue

        print(
            f"\n==================== ENS {member_idx:03d}/{rt['n_members']} "
            f"({rt['dem_mode']}, {rt['res']}m) ===================="
        )
        rng = random.Random(rt["seed_base"] + member_idx)
        k_i = rng.uniform(rt["k_min"], rt["k_max"])
        print(f"→ k (sliding factor) = {k_i:.3f}")

        ras_before = list_maps(gs, "raster")
        vec_before = list_maps(gs, "vector")

        surface_member = ensure_cached_surface(
            member_idx=member_idx,
            base_dem=dem_map,
            var_map="dem_var",
            corr_pix_val=rt["CORR_PIX"],
            cache_dir=rt["OUT_SURF"],
            save=rt["SAVE_SURF"],
        )

        bed_member = None
        if rt["dem_mode"] in ("bed", "hybrid"):
            bed_member = ensure_cached_bed(
                member_idx=member_idx,
                base_bed="bed_raster",
                err_map="bed_err",
                corr_pix_val=rt["CORR_PIX"],
                err_is_variance=rt["BED_ERR_IS_VAR"],
                cache_dir=rt["OUT_BED"],
                save=rt["SAVE_BED"],
            )

        if rt["dem_mode"] == "surface":
            hydro_dem = surface_member
        elif rt["dem_mode"] == "bed":
            assert bed_member is not None
            hydro_dem = ensure_cached_phi(
                member_idx=member_idx,
                bed_member=bed_member,
                surface_member=surface_member,
                k_i=k_i,
                cache_dir=rt["OUT_BED"],
                save=rt["SAVE_PHI_OR_HYBRID"],
            )
        else:
            assert bed_member is not None
            hydro_dem = ensure_cached_hybrid(
                member_idx=member_idx,
                bed_member=bed_member,
                surface_member=surface_member,
                k_i=k_i,
                inside_mask_raster="surf_mask_2000",
                cache_dir=rt["OUT_HYB"],
                save=rt["SAVE_PHI_OR_HYBRID"],
            )

        run_hydro_for_dem(
            dem_name=hydro_dem,
            out_dir=str(rt["OUT"]),
            stream_threshold=rt["STREAM_THRESH"],
            run_idx=member_idx,
            watershed_memory=rt["WS_MEM"],
            watershed_lowmem=rt["WS_LOWMEM"],
            do_fill_dir=rt["DO_FILL_DIR"],
            extra_exports=None,
        )
        rename_member_outputs_to_ensemble(rt["OUT"], member_idx)

        ras_after = list_maps(gs, "raster")
        vec_after = list_maps(gs, "vector")
        remove_maps(gs, "raster", sorted((ras_after - ras_before) - keep_rasters))
        remove_maps(gs, "vector", sorted((vec_after - vec_before) - keep_vectors))

    print("\n✅ Ensemble stage done.")
    return 0
