# basin_core.py
from __future__ import annotations

import os
import sys
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Dict, List, Tuple

# -----------------------------------------------------------------------------
# Defaults
# -----------------------------------------------------------------------------
QGIS_PREFIX_DEFAULT = os.environ.get("GISBASE", "/usr/lib/grass84")

# Filled after setup_grass_env()
gs = None
gsetup = None
find_program = None
CalledModuleError = RuntimeError


# -----------------------------------------------------------------------------
# GRASS bootstrap helpers
# -----------------------------------------------------------------------------
def _grass_bin() -> str:
    """Return the GRASS launcher executable, honoring GRASS_BIN first."""
    env_bin = os.environ.get("GRASS_BIN")
    if env_bin:
        p = Path(env_bin).expanduser().resolve()
        if p.exists() and os.access(p, os.X_OK):
            return str(p)
        raise RuntimeError(f"GRASS_BIN is set but not executable: {p}")

    for name in ("grass", "grass84", "grass82"):
        p = shutil.which(name)
        if p:
            return p

    raise RuntimeError("No GRASS executable found. Set GRASS_BIN or put grass on PATH.")


def _guess_gisbase() -> Path:
    """Derive GISBASE from the selected GRASS executable only."""
    grass_exe = _grass_bin()
    res = subprocess.run(
        [grass_exe, "--config", "path"],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    gisbase = Path(res.stdout.strip()).expanduser().resolve()
    init_py = gisbase / "etc" / "python" / "grass" / "__init__.py"
    if not init_py.exists():
        raise RuntimeError(f"Invalid GISBASE from {grass_exe}: {gisbase}")
    return gisbase


def _conda_share_dir(name: str) -> Path | None:
    conda_prefix = os.environ.get("CONDA_PREFIX")
    if not conda_prefix:
        return None
    p = Path(conda_prefix) / "share" / name
    return p if p.exists() else None


def _grass_subprocess_env(*, for_launcher: bool = False) -> dict:
    """
    Build a subprocess environment that stays consistent with the chosen GRASS
    install and the active conda environment.

    When for_launcher=True, remove PYTHONPATH so the grass launcher resolves its
    own matching Python modules instead of inheriting possibly mixed paths.
    """
    env = os.environ.copy()
    grass_exe = _grass_bin()
    gisbase = _guess_gisbase()
    grass_python = gisbase / "etc" / "python"

    env["GRASS_BIN"] = grass_exe
    env["GISBASE"] = str(gisbase)
    env.setdefault("LANG", "en_US.UTF-8")
    env.setdefault("LC_ALL", "en_US.UTF-8")
    env.setdefault("GTIFF_SRS_SOURCE", "EPSG")

    env.pop("GDAL_DRIVER_PATH", None)
    env.pop("PYTHONHOME", None)

    proj_dir = _conda_share_dir("proj")
    if proj_dir and (proj_dir / "proj.db").exists():
        env["PROJ_LIB"] = str(proj_dir)
        env["PROJ_DATA"] = str(proj_dir)
    else:
        env.pop("PROJ_LIB", None)
        env.pop("PROJ_DATA", None)

    gdal_dir = _conda_share_dir("gdal")
    if gdal_dir:
        env["GDAL_DATA"] = str(gdal_dir)
    else:
        env.pop("GDAL_DATA", None)

    if for_launcher:
        env.pop("PYTHONPATH", None)
    else:
        env["PYTHONPATH"] = str(grass_python)

    return env


def setup_grass_env(grass_gisbase: str | None = None):
    """
    Configure GRASS Python bindings so launcher, GISBASE, and Python modules all
    come from the same installation.
    """
    global gs, gsetup, find_program, CalledModuleError

    GISBASE = Path(grass_gisbase).resolve() if grass_gisbase else _guess_gisbase()
    grass_exe = _grass_bin()
    grass_python = GISBASE / "etc" / "python"
    init_py = grass_python / "grass" / "__init__.py"
    if not init_py.exists():
        raise RuntimeError(f"GRASS python not found at: {init_py}")

    os.environ["GISBASE"] = str(GISBASE)
    os.environ["GRASS_BIN"] = grass_exe
    os.environ["PATH"] = os.pathsep.join(
        [
            str(GISBASE / "bin"),
            str(GISBASE / "scripts"),
            os.environ.get("PATH", ""),
        ]
    )
    os.environ.setdefault("LANG", "en_US.UTF-8")
    os.environ.setdefault("LC_ALL", "en_US.UTF-8")
    os.environ.setdefault("GTIFF_SRS_SOURCE", "EPSG")
    os.environ.pop("GDAL_DRIVER_PATH", None)
    os.environ.pop("PYTHONHOME", None)

    proj_dir = _conda_share_dir("proj")
    if proj_dir and (proj_dir / "proj.db").exists():
        os.environ["PROJ_LIB"] = str(proj_dir)
        os.environ["PROJ_DATA"] = str(proj_dir)
    else:
        os.environ.pop("PROJ_LIB", None)
        os.environ.pop("PROJ_DATA", None)

    gdal_dir = _conda_share_dir("gdal")
    if gdal_dir:
        os.environ["GDAL_DATA"] = str(gdal_dir)
    else:
        os.environ.pop("GDAL_DATA", None)

    sys.path = [
        p for p in sys.path
        if not ("/etc/python" in p and "grass" in p.lower())
    ]
    for name in list(sys.modules):
        if name == "grass" or name.startswith("grass."):
            del sys.modules[name]

    if str(grass_python) not in sys.path:
        sys.path.insert(0, str(grass_python))
    os.environ["PYTHONPATH"] = str(grass_python)

    import grass.script as _gs
    import grass.script.setup as _gsetup
    from grass.script.core import find_program as _find_program
    from grass.exceptions import CalledModuleError as _CalledModuleError

    gs = _gs
    gsetup = _gsetup
    find_program = _find_program
    CalledModuleError = _CalledModuleError

    print("✅ GISBASE:", GISBASE)
    print("✅ grass_python:", grass_python)
    print("✅ grass_executable:", grass_exe)
    print("✅ PROJ_LIB:", os.environ.get("PROJ_LIB"))
    print("✅ GDAL_DATA:", os.environ.get("GDAL_DATA"))


def _validate_raster_with_gdalinfo(raster_path: str) -> None:
    raster_abs = str(Path(raster_path).resolve())
    cmd = ["gdalinfo", raster_abs]
    res = subprocess.run(cmd, capture_output=True, text=True, env=_grass_subprocess_env(for_launcher=True))
    if res.returncode != 0:
        raise RuntimeError(
            "Raster failed validation with gdalinfo.\n"
            f"Command: {' '.join(cmd)}\n"
            f"stdout:\n{res.stdout}\n"
            f"stderr:\n{res.stderr}"
        )


from pathlib import Path
import subprocess

def ensure_location(raster_path, gisdbase, location, grass_executable=None):
    loc_path = Path(gisdbase) / location

    # If location already exists, reuse it
    if loc_path.exists():
        return

    cmd = [
        grass_executable or "grass",
        "--text",
        "-c",
        "EPSG:3413",
        "-e",
        str(loc_path),
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True)

    # Success
    if proc.returncode == 0:
        return

    # Race condition: another job created it between exists() and run()
    stderr = proc.stderr or ""
    stdout = proc.stdout or ""
    combined = f"{stdout}\n{stderr}"

    if "already exists" in combined or "File exists" in combined:
        return

    raise RuntimeError(
        f"Failed to create GRASS location from EPSG:3413.\n"
        f"Command: {' '.join(cmd)}\n"
        f"stdout:\n{stdout}\n"
        f"stderr:\n{stderr}"
    )

def ensure_mapset(gisdbase: Path, location: str, mapset: str) -> Path:
    """
    Ensure a mapset exists without invoking the GRASS launcher.
    This avoids launcher cleanup crashes after successful mapset creation.
    """
    loc_path = gisdbase / location
    perm_path = loc_path / "PERMANENT"
    mapset_path = loc_path / mapset

    if mapset == "PERMANENT":
        return perm_path

    if not (perm_path / "DEFAULT_WIND").exists():
        raise RuntimeError(f"PERMANENT mapset missing or invalid: {perm_path}")

    if mapset_path.exists() and (mapset_path / "WIND").exists():
        return mapset_path

    mapset_path.mkdir(parents=True, exist_ok=True)
    shutil.copy2(perm_path / "DEFAULT_WIND", mapset_path / "WIND")

    for name in ("VAR", "DB_DRIVER", "DB_DATABASE"):
        src = perm_path / name
        dst = mapset_path / name
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)

    return mapset_path


def start_grass_from_raster(raster_path: str, *, location="dem_loc", mapset="MC_WORK", gisdbase: str | Path | None = None):
    """
    Ensure GRASS location+mapset exist, initialize a GRASS Python session,
    then import the DEM and set the computational region from it.
     """
    gisdbase = Path(gisdbase or os.environ.get("GRASS_GISDBASE") or (Path.home() / "Documents" / "grassdata"))
    gisdbase.mkdir(parents=True, exist_ok=True)

    _validate_raster_with_gdalinfo(raster_path)

    job_id = os.environ.get("LSB_JOBID", "manual")
    task_id = os.environ.get("LSB_JOBINDEX", "0")
    mapset = f"MC_WORK_{job_id}_{task_id}"

    ensure_location(raster_path, gisdbase, location)
    ensure_mapset(gisdbase, location, mapset)

    GISBASE = os.environ["GISBASE"]
    gsetup.init(str(gisdbase), location=location, mapset=mapset, grass_path=GISBASE)
    raster_abs = str(Path(raster_path).resolve())
    try:
        gs.run_command("g.remove", type="raster", name="dem", flags="f", quiet=True)
    except Exception:
        pass
    gs.run_command("r.in.gdal", input=raster_abs, output="dem", flags="o", overwrite=True)
    gs.run_command("g.region", raster="dem")

    print(f"🌿 GRASS session initialized in:\n   {gisdbase / location / mapset}\n")
    print(gs.read_command("g.gisenv"))

    return str(gisdbase), location, mapset

# -----------------------------------------------------------------------------
# Small utilities
# -----------------------------------------------------------------------------
def safe(expr: str):
    """r.mapcalc wrapper with useful error message."""
    try:
        gs.run_command("r.mapcalc", expression=expr, overwrite=True)
    except CalledModuleError as e:
        raise RuntimeError(f"Mapcalc failed: {expr}\n{e}")


def ensure_grass_addon(module_name: str):
    """Install addon into the GRASS addon directory for the current platform.

    On some HPC/conda GRASS builds, g.extension compilation can fail because the
    build-time compiler path baked into GRASS is unavailable at runtime. In that
    case, keep going and let downstream code decide whether the addon is
    optional.
    """
    import platform

    system = platform.system()
    if system == "Darwin":
        addon_base = Path.home() / ".grass8" / "addons"
    elif system == "Linux":
        addon_base = Path(os.environ.get("GRASS_ADDON_BASE", str(Path.home() / ".grass8" / "addons"))).expanduser()
    else:
        appdata = os.environ.get("APPDATA", str(Path.home()))
        addon_base = Path(appdata) / "GRASS8" / "addons"

    addon_base.mkdir(parents=True, exist_ok=True)
    os.environ["GRASS_ADDON_BASE"] = str(addon_base)

    addon_bin = addon_base / "bin"
    addon_scripts = addon_base / "scripts"

    os.environ["PATH"] = os.pathsep.join([
        str(addon_bin),
        str(addon_scripts),
        os.environ.get("PATH", ""),
    ])
    os.environ["GRASS_ADDON_PATH"] = os.pathsep.join([
        str(addon_bin),
        str(addon_scripts),
    ])

    path = find_program(module_name)
    if path:
        print(f"✓ {module_name} at {path}")
        return path

    print(f"→ Installing GRASS addon: {module_name}")
    try:
        gs.run_command("g.extension", extension=module_name, operation="add", flags="f")
    except Exception as e:
        print(f"⚠️ Could not install optional GRASS addon {module_name}: {e}")
        return None

    path = find_program(module_name)
    if not path:
        print(f"⚠️ GRASS addon still unavailable after install attempt: {module_name}")
        return None

    print(f"✓ {module_name} at {path}")
    return path


def import_raster_native(input_path: str, out_name: str):
    """Import or clone raster to a native GRASS raster (fast + robust)."""
    raster = input_path.replace("\\", "/")
    try:
        gs.run_command("r.in.gdal", input=raster, output=out_name, flags="o", overwrite=True)
        print(f"✓ r.in.gdal → {out_name}")
    except Exception:
        gs.run_command("r.external", input=raster, output=f"{out_name}_ext", flags="o", overwrite=True)
        gs.run_command("g.region", raster=f"{out_name}_ext")
        safe(f"{out_name} = {out_name}_ext * 1.0")
        print(f"✓ r.external + clone → {out_name}")
    gs.run_command("g.region", raster=out_name)


def export_geotiff(
    map_name: str,
    out_path: str | Path,
    *,
    gdal_type: str = "Float32",
    nodata: float = -9999,
    force: bool = False,   # <-- NEW
):
    out_path = str(out_path).replace("\\", "/")
    flags = "f" if force else ""

    gs.run_command(
        "r.out.gdal",
        input=map_name,
        output=out_path,
        format="GTiff",
        type=gdal_type,
        createopt="COMPRESS=LZW,TILED=YES,BIGTIFF=YES",
        nodata=nodata,
        flags=flags,
        overwrite=True,
    )

# -----------------------------------------------------------------------------
# DEM + Mask prep
# -----------------------------------------------------------------------------
def prepare_dem_and_mask(
    dem_path: str,
    ice_mask_path: str,
    *,
    dem_name: str = "dem",
    mask_vec: str = "ice_mask_vec",
    mask_rast: str = "ice_mask_rast",
):
    """
    Import DEM + vector ice mask, rasterize mask to DEM grid.
    Leaves MASK active at the end.
    Returns: (dem_name, mask_rast)
    """
    # clear any existing MASK
    try:
        gs.run_command("r.mask", flags="r")
    except Exception:
        pass

    import_raster_native(dem_path, out_name=dem_name)
    gs.run_command("g.region", raster=dem_name, flags="p")

    ice_mask_abs = str(Path(ice_mask_path).resolve()).replace("\\", "/")
    print(f"→ Importing vector ice mask from: {ice_mask_abs}")

    try:
        gs.run_command("v.import", input=ice_mask_abs, output=mask_vec, overwrite=True)
        print(f"✓ v.import → {mask_vec}")
    except CalledModuleError:
        gs.run_command("v.in.ogr", input=ice_mask_abs, output=mask_vec, overwrite=True)
        print(f"✓ v.in.ogr → {mask_vec}")

    gs.run_command("g.region", raster=dem_name)
    gs.run_command("v.to.rast", input=mask_vec, output=mask_rast, use="val", value=1, overwrite=True)
    print(f"✓ mask rasterized → {mask_rast}")

    gs.run_command("r.mask", raster=mask_rast, overwrite=True)
    print("✓ MASK set")

    return dem_name, mask_rast


# -----------------------------------------------------------------------------
# Hydrology pipeline + exports
# -----------------------------------------------------------------------------
def run_hydro_pipeline(
    hydro_dem: str,
    out_dir: str,
    stream_threshold: int,
    *,
    tag: str = "",
    watershed_memory: int = 600,
    watershed_lowmem: bool = True,
    do_fill_dir: bool = True,
    extra_exports: List[Tuple[str, str | Path, str]] | None = None,  # (map, filename, gdal_type)
):
    """
    hydro_dem → (optional r.fill.dir) → r.watershed → r.stream.extract → r.stream.basins → exports.
    Uses ice_mask_rast if present.

    Standard exports:
      flowdir_hydro{tag}.tif (Int32)
      streams_hydro{tag}.tif (Int16)
      basins_hydro{tag}.tif  (Int32)

    extra_exports:
      additional rasters to export (e.g., dem_mc_###, bed_mc_###) into out_dir or anywhere.
    """
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    # Always set region to the incoming hydrology surface
    gs.run_command("g.region", raster=hydro_dem, flags="p")

    # Apply mask if available
    try:
        gs.run_command("r.mask", raster="ice_mask_rast", overwrite=True)
        print("✓ Mask set from ice_mask_rast")
    except Exception:
        print("⚠️ Could not set r.mask from ice_mask_rast (continuing without mask)")

    elev_used = hydro_dem
    if do_fill_dir:
        filled = f"hydro_fill{tag}"
        filled_dir = f"hydro_fill_dir{tag}"
        print(f"🔧 r.fill.dir on {hydro_dem} → {filled}")
        gs.run_command(
            "r.fill.dir",
            input=hydro_dem,
            output=filled,
            direction=filled_dir,
            overwrite=True,
        )
        elev_used = filled

    accum = f"accum{tag}"
    flow_dir = f"flow_dir{tag}"
    streams = f"streams{tag}"
    basins = f"basins{tag}"

    ws_flags = "m" if watershed_lowmem else ""

    print(f"🔧 r.watershed on {elev_used}")
    gs.run_command(
        "r.watershed",
        elevation=elev_used,
        accumulation=accum,
        drainage=flow_dir,
        memory=watershed_memory,
        flags=ws_flags,
        overwrite=True,
    )

    gs.run_command(
        "r.stream.extract",
        elevation=elev_used,
        direction=flow_dir,
        accumulation=accum,
        threshold=stream_threshold,
        stream_raster=streams,
        overwrite=True,
    )

    try:
        gs.run_command(
            "r.stream.basins",
            direction=flow_dir,
            stream_rast=streams,
            basins=basins,
            flags="l",
            overwrite=True,
        )
        print("✓ r.stream.basins completed")
    except CalledModuleError as e:
        print(f"⚠️ r.stream.basins failed; creating empty basins raster. Error: {e}")
        safe(f"{basins} = 0")

    exports: List[Tuple[str, str, str]] = [
        (flow_dir, str(Path(out_dir) / f"flowdir_hydro{tag}.tif"), "Int32"),
        (streams, str(Path(out_dir) / f"streams_hydro{tag}.tif"), "Int16"),
        (basins, str(Path(out_dir) / f"basins_hydro{tag}.tif"), "Int32"),
    ]

    if extra_exports:
        for m, fn, typ in extra_exports:
            exports.append((m, str(fn), typ))

    for name, fn, gtype in exports:
        fn = fn.replace("\\", "/")
        try:
            info = gs.read_command("r.info", map=name)
            if "min =" in info and "max =" in info:
                print(f"📤 Export {name} → {fn}")
                export_geotiff(name, fn, gdal_type=gtype, nodata=-9999, force=True)
            else:
                print(f"⚠️ Skip {name}: no data detected.")
        except Exception as e:
            print(f"❌ Export failed {name}: {e}")

    # Clear MASK at end
    try:
        gs.run_command("r.mask", flags="r")
    except Exception:
        pass

    print(f"✅ Finished hydrology for {hydro_dem} (tag='{tag}')")


def run_hydro_for_dem(
    dem_name: str,
    out_dir: str,
    stream_threshold: int,
    *,
    run_idx: int | None = None,
    watershed_memory: int = 600,
    watershed_lowmem: bool = True,
    do_fill_dir: bool = True,
    extra_exports: List[Tuple[str, str | Path, str]] | None = None,
):
    tag = f"_mc_{run_idx:03d}" if run_idx is not None else ""
    run_hydro_pipeline(
        hydro_dem=dem_name,
        out_dir=out_dir,
        stream_threshold=stream_threshold,
        tag=tag,
        watershed_memory=watershed_memory,
        watershed_lowmem=watershed_lowmem,
        do_fill_dir=do_fill_dir,
        extra_exports=extra_exports,
    )


# -----------------------------------------------------------------------------
# DEM builders for the three modes
# -----------------------------------------------------------------------------
def compute_hydraulic_potential_maps(
    *,
    bed_map: str,
    surface_map: str,
    out_map: str,
    smooth_surface: bool = True,
    smooth_size: int = 3,
    rho_w: float = 1000.0,
    rho_i: float = 917.0,
    g: float = 9.81,
    k: float = 1.0,
) -> str:
    """
    phi = rho_w*g*bed + k*rho_i*g*(surface - bed)
    stored as MPa (divide by 1e6)
    """
    gs.run_command("g.region", raster=surface_map)

    surf_eff = surface_map
    if smooth_surface and smooth_size > 1:
        surf_smooth = f"{surface_map}_sm{smooth_size}"
        gs.run_command(
            "r.neighbors",
            input=surface_map,
            output=surf_smooth,
            method="average",
            size=smooth_size,
            overwrite=True,
        )
        surf_eff = surf_smooth

    safe(f"{out_map} = (({rho_w}*{g}*{bed_map}) + ({k}*{rho_i}*{g}*({surf_eff}-{bed_map})))/1000000.0")
    return out_map


def build_bed_mode_dem(
    *,
    bed_dem: str,
    surface_dem: str,
    tag: str,
    surface_smooth_size: int = 3,
    k: float = 1.0,
) -> str:
    # clear mask for r.mapcalc / smoothing
    try:
        gs.run_command("r.mask", flags="r")
    except Exception:
        pass

    phi = f"hyd_pot{tag}"
    compute_hydraulic_potential_maps(
        bed_map=bed_dem,
        surface_map=surface_dem,
        out_map=phi,
        smooth_surface=True,
        smooth_size=surface_smooth_size,
        k=k,
    )
    return phi


def build_hybrid_mode_dem(
    *,
    surface_dem: str,
    bed_dem: str,
    inside_mask_raster: str,
    tag: str,
    surface_smooth_size_for_phi: int = 3,
    k: float = 1.0,
) -> str:
    try:
        gs.run_command("r.mask", flags="r")
    except Exception:
        pass

    gs.run_command("g.region", raster=surface_dem)

    # 1) phi
    phi = f"hyd_pot{tag}"
    compute_hydraulic_potential_maps(
        bed_map=bed_dem,
        surface_map=surface_dem,
        out_map=phi,
        smooth_surface=True,
        smooth_size=surface_smooth_size_for_phi,
        k=k,
    )

    # 2) inside/outside from raster (NULL outside)
    inside = f"inside_mask{tag}"
    outside = f"outside_mask{tag}"
    safe(f"{inside}  = if(isnull({inside_mask_raster}), 0, 1)")
    safe(f"{outside} = if({inside} == 0, 1, 0)")

    # 3) masked rasters
    surf_masked = f"surface_masked{tag}"
    phi_masked = f"phi_masked{tag}"
    safe(f"{surf_masked} = if({inside} == 1, {surface_dem}, null())")
    safe(f"{phi_masked}  = if({outside} == 1, {phi}, null())")

    # 4) fill separately
    demA = f"dem_filledA{tag}"
    demB = f"dem_filledB{tag}"
    dirA = f"flow_dirA{tag}"
    dirB = f"flow_dirB{tag}"

    gs.run_command("r.fill.dir", input=surf_masked, output=demA, direction=dirA, overwrite=True)
    gs.run_command("r.fill.dir", input=phi_masked, output=demB, direction=dirB, overwrite=True)

    # 5) combine
    hybrid = f"hybrid_dem{tag}"
    safe(f"{hybrid} = if({inside} == 1, {demA}, {demB})")
    return hybrid


# -----------------------------------------------------------------------------
# Optional postprocess merge (used by mc_postprocess.py)
# -----------------------------------------------------------------------------


def prepare_merge_workspace(*, gs=None, dem_path: str, res_m: int = 500, dem_map: str = "dem") -> str:
    if gs is None:
        if globals().get("gs", None) is None:
            raise RuntimeError(
                "prepare_merge_workspace needs a live GRASS session. "
                "Call setup_grass_env() + start_grass_from_raster() first, "
                "or pass gs=bc.gs explicitly."
            )
        gs = globals()["gs"]

    gs.run_command("r.in.gdal", input=dem_path.replace(chr(92), "/"), output=dem_map, overwrite=True)
    gs.run_command("g.region", raster=dem_map, res=res_m, align=dem_map)
    return dem_map


def merge_basins(
    *,
    gs=None,
    basins_input: str,
    dem_path: Optional[str] = None,
    out_dir: str,
    min_basin_size_km2: float = 500.0,
    res_m: int = 500,
    do_exclave_cleanup: bool = True,
    max_exclave_iters: int = 6,
    out_name: str = "basins_merged_no_small_fullcover.tif",
    dem_map: str = "dem",
    member_tag: Optional[str] = None,
):
    if gs is None:
        if globals().get("gs", None) is None:
            raise RuntimeError(
                "merge_basins needs a live GRASS session. "
                "Call setup_grass_env() + start_grass_from_raster() first, "
                "or pass gs=bc.gs explicitly."
            )
        gs = globals()["gs"]

    out_dir_p = Path(out_dir)
    out_dir_p.mkdir(parents=True, exist_ok=True)
    rule_dir = Path(os.environ.get("TMPDIR") or out_dir_p)
    rule_dir.mkdir(parents=True, exist_ok=True)

    tag = member_tag or Path(basins_input).stem.replace("-", "_").replace(".", "_")
    basins_in = f"basins_in_{tag}"
    basins0 = f"basins0_{tag}"
    basins_coverage = f"basins_coverage_{tag}"
    basins_after_merge = f"basins_after_merge_{tag}"
    fill_from = f"fill_from_{tag}"
    basins_filled = f"basins_filled_{tag}"
    big_only = f"big_only_{tag}"
    nearest_big_id = f"nearest_big_id_{tag}"
    clumps = f"clumps_{tag}"
    clump_basin = f"clump_basin_{tag}"
    dropmask = f"dropmask_{tag}"
    refill = f"refill_{tag}"
    final_big_only = f"final_big_only_{tag}"
    final_nearest_big_id = f"final_nearest_big_id_{tag}"
    final_refill = f"final_refill_{tag}"
    basins_final_sized = f"basins_final_sized_{tag}"
    basins_merged_final = f"basins_merged_final_{tag}"

    def _parse_stat_value(tok: str) -> Optional[int]:
        tok = tok.strip()
        if tok in ("", "*"):
            return None
        if "-" in tok:
            tok = tok.split("-", 1)[0]
        try:
            return int(round(float(tok)))
        except Exception:
            return None

    def _read_rstats_cn(mapname: str) -> Dict[int, int]:
        lines = gs.read_command("r.stats", input=mapname, flags="cn", separator=",").strip().splitlines()
        d: Dict[int, int] = {}
        for ln in lines:
            if not ln.strip():
                continue
            a, b = ln.split(",", 1)
            cat = _parse_stat_value(a)
            if cat is None:
                continue
            try:
                d[cat] = int(b)
            except Exception:
                pass
        return d

    def _pairs(lines: List[str]) -> List[Tuple[int, int]]:
        out: List[Tuple[int, int]] = []
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            a, b = ln.split(",", 1)
            try:
                out.append((int(float(a)), int(float(b))))
            except Exception:
                continue
        return out

    def _nearest_big_counts(label_map: str, big_map: str, small_set: set[int], nearest_map: str) -> Dict[int, Dict[int, int]]:
        """Fallback for isolated small basins that do not touch any large basin."""
        gs.run_command("r.grow.distance", input=big_map, value=nearest_map, flags="m", overwrite=True)
        counts_by_small: Dict[int, Dict[int, int]] = {}
        lines = gs.read_command(
            "r.stats",
            input=f"{label_map},{nearest_map}",
            flags="cnN",
            separator=",",
        ).strip().splitlines()
        for ln in lines:
            parts = ln.split(",")
            if len(parts) < 3:
                continue
            src = _parse_stat_value(parts[0])
            dst = _parse_stat_value(parts[1])
            if src is None or dst is None or src not in small_set:
                continue
            try:
                cnt = int(parts[2])
            except Exception:
                continue
            counts_for_src = counts_by_small.setdefault(src, {})
            counts_for_src[dst] = counts_for_src.get(dst, 0) + cnt
        return counts_by_small

    def _force_single_component_per_label(label_map: str, stage: str) -> Tuple[str, int]:
        """
        Keep one connected component per basin label.

        Pixelwise ensemble voting can assign the same reference basin ID to
        multiple disconnected islands. A basin ID must represent one connected
        object, so keep the largest component for each label and refill the
        detached components from the nearest retained basin.
        """
        comp_map = f"{stage}_clumps_{tag}"
        comp_label = f"{stage}_clump_label_{tag}"
        drop_map = f"{stage}_dropmask_{tag}"
        assign_map = f"{stage}_assign_{tag}"
        coverage_next = f"{stage}_coverage_{tag}"
        out_map = f"{stage}_connected_{tag}"

        gs.run_command("r.clump", input=label_map, output=comp_map, overwrite=True)
        gs.mapcalc(f"{comp_label} = if(!isnull({comp_map}), int({label_map}), null())", overwrite=True)
        lines = gs.read_command(
            "r.stats",
            input=f"{comp_map},{comp_label}",
            flags="cn",
            separator=",",
        ).strip().splitlines()

        best_by_label: Dict[int, Tuple[int, int]] = {}
        n_components_by_label: Dict[int, int] = {}
        label_by_component: Dict[int, int] = {}
        count_by_component: Dict[int, int] = {}
        all_components: set[int] = set()

        for ln in lines:
            parts = ln.split(",")
            if len(parts) < 3:
                continue
            cid = _parse_stat_value(parts[0])
            bid = _parse_stat_value(parts[1])
            if cid is None or bid is None:
                continue
            try:
                count = int(parts[2])
            except Exception:
                continue

            all_components.add(cid)
            label_by_component[cid] = bid
            count_by_component[cid] = count
            n_components_by_label[bid] = n_components_by_label.get(bid, 0) + 1
            best = best_by_label.get(bid)
            if best is None or count > best[1]:
                best_by_label[bid] = (cid, count)

        keep_components = {cid for cid, _ in best_by_label.values()}
        drop_components = sorted(all_components - keep_components)
        split_labels = sum(1 for n in n_components_by_label.values() if n > 1)

        print(
            f"  connected-label cleanup: {split_labels} split label(s), "
            f"{len(drop_components)} detached component(s)"
        )

        if not drop_components:
            return label_map, 0

        rules_dropmask = rule_dir / f"{stage}_dropmask_reclass_{tag}.txt"
        with open(rules_dropmask, "w", encoding="utf-8") as f:
            for cid in drop_components:
                f.write(f"{cid} = {cid}\n")
            f.write("* = NULL\n")

        gs.run_command("r.reclass", input=comp_map, output=drop_map, rules=str(rules_dropmask), overwrite=True)

        adjacency_counts: Dict[int, Dict[int, int]] = {}

        # Use the same 4-neighbor topology as r.clump. A distance ring can miss
        # or mis-rank one-cell islands near diagonal same-label cells.
        for direction, dr, dc in (
            ("n", -1, 0),
            ("s", 1, 0),
            ("w", 0, -1),
            ("e", 0, 1),
        ):
            neighbor_label = f"{stage}_neighbor_{direction}_{tag}"
            gs.mapcalc(
                f"{neighbor_label} = if(!isnull({drop_map}) && "
                f"isnull({drop_map}[{dr},{dc}]) && !isnull({label_map}[{dr},{dc}]) && "
                f"int({label_map}[{dr},{dc}]) != int({label_map}), "
                f"int({label_map}[{dr},{dc}]), null())",
                overwrite=True,
            )
            adj_lines = gs.read_command(
                "r.stats",
                input=f"{drop_map},{neighbor_label}",
                flags="cn",
                separator=",",
            ).strip().splitlines()
            for ln in adj_lines:
                parts = ln.split(",")
                if len(parts) < 3:
                    continue
                cid = _parse_stat_value(parts[0])
                bid = _parse_stat_value(parts[1])
                if cid is None or bid is None or cid not in label_by_component:
                    continue
                try:
                    count = int(parts[2])
                except Exception:
                    continue
                counts = adjacency_counts.setdefault(cid, {})
                counts[bid] = counts.get(bid, 0) + count

        assignments = {
            cid: max(counts.items(), key=lambda kv: kv[1])[0]
            for cid, counts in adjacency_counts.items()
            if counts
        }
        missing_assignments = sorted(set(drop_components) - set(assignments))

        if missing_assignments:
            missing_cells = sum(count_by_component.get(cid, 0) for cid in missing_assignments)
            print(
                f"  dropping {len(missing_assignments)} isolated detached component(s) "
                f"({missing_cells} cell(s)) to nodata"
            )

        print(f"  reassigning detached components to {len(set(assignments.values()))} adjacent label(s)")

        rules_assign = rule_dir / f"{stage}_assign_reclass_{tag}.txt"
        with open(rules_assign, "w", encoding="utf-8") as f:
            for cid in sorted(assignments):
                f.write(f"{cid} = {assignments[cid]}\n")
            f.write("* = NULL\n")

        gs.run_command("r.reclass", input=comp_map, output=assign_map, rules=str(rules_assign), overwrite=True)
        gs.mapcalc(
            f"{out_map} = if(!isnull({drop_map}), "
            f"if(!isnull({assign_map}), {assign_map}, null()), {label_map})",
            overwrite=True,
        )
        if missing_assignments:
            gs.mapcalc(f"{coverage_next} = if(!isnull({out_map}), {basins_coverage}, null())", overwrite=True)
            gs.run_command("g.rename", raster=f"{coverage_next},{basins_coverage}", overwrite=True)
        return out_map, len(drop_components)

    if dem_path:
        prepare_merge_workspace(gs=gs, dem_path=dem_path, res_m=res_m, dem_map=dem_map)
    else:
        gs.run_command("g.region", raster=dem_map, res=res_m, align=dem_map)

    gs.run_command("r.in.gdal", input=basins_input.replace(chr(92), "/"), output=basins_in, overwrite=True)
    gs.mapcalc(f"{basins0} = int({basins_in})", overwrite=True)
    gs.mapcalc(f"{basins0} = if(isnull({dem_map}), null(), {basins0})", overwrite=True)
    gs.mapcalc(f"{basins_coverage} = if(!isnull({dem_map}), 1, null())", overwrite=True)

    cell_area_km2 = (res_m * res_m) / 1e6
    min_cells = int(round(min_basin_size_km2 / cell_area_km2))
    print(f"→ Merge threshold: {min_basin_size_km2} km² ≈ {min_cells} cells at {res_m} m")

    sizes = _read_rstats_cn(basins0)
    if not sizes:
        raise RuntimeError("No basins found within DEM extent.")

    big_ids = {cat for cat, n in sizes.items() if n >= min_cells}
    small_ids = sorted(set(sizes) - big_ids)
    small_id_set = set(small_ids)

    if not big_ids:
        largest = max(sizes.items(), key=lambda kv: kv[1])[0]
        big_ids = {largest}
        small_ids = sorted(set(sizes) - big_ids)
        small_id_set = set(small_ids)
        print(f"ℹ️ All basins < threshold; seeding with largest basin {largest}")

    print(f"→ Big basins: {len(big_ids)}  |  Small basins: {len(small_ids)}")

    rules_big = rule_dir / f"big_reclass_{tag}.txt"
    with open(rules_big, "w", encoding="utf-8") as f:
        for cat in sizes:
            if cat in big_ids:
                f.write(f"{cat} = {cat}\n")
            else:
                f.write(f"{cat} = NULL\n")
    gs.run_command("r.reclass", input=basins0, output=big_only, rules=str(rules_big), overwrite=True)

    if not small_ids:
        gs.mapcalc(f"{basins_after_merge} = {basins0}", overwrite=True)
    else:
        small_to_big = _nearest_big_counts(basins0, big_only, small_id_set, nearest_big_id)

        rules_path = rule_dir / f"whole_basin_reclass_{tag}.txt"
        with open(rules_path, "w", encoding="utf-8") as f:
            for bid in sorted(big_ids):
                f.write(f"{bid} = {bid}\n")
            for sid in small_ids:
                counts = small_to_big.get(sid)
                if not counts:
                    f.write(f"{sid} = {sid}\n")
                    continue
                chosen = max(counts.items(), key=lambda kv: kv[1])[0]
                f.write(f"{sid} = {chosen}\n")

        gs.run_command("r.reclass", input=basins0, output=basins_after_merge, rules=str(rules_path), overwrite=True)

    gs.mapcalc(f"{basins_after_merge} = if(isnull({basins_coverage}), null(), {basins_after_merge})", overwrite=True)
    gs.run_command("r.grow.distance", input=basins_after_merge, value=fill_from, flags="m", overwrite=True)
    gs.mapcalc(
        f"{basins_filled} = if(isnull({basins_after_merge}) && !isnull({basins_coverage}), {fill_from}, {basins_after_merge})",
        overwrite=True,
    )
    current = basins_filled

    if do_exclave_cleanup:
        for it in range(1, max_exclave_iters + 1):
            print(f"\n→ Anti-exclave pass {it}")

            gs.run_command("r.clump", input=current, output=clumps, overwrite=True)
            cstats = gs.read_command("r.stats", input=clumps, flags="cn", separator=",").strip().splitlines()
            if not cstats:
                print("  (no clumps?)")
                break

            gs.mapcalc(f"{clump_basin} = if(!isnull({clumps}), int({current}), null())", overwrite=True)
            anchor_lines = gs.read_command(
                "r.stats",
                input=f"{clumps},{clump_basin},{big_only}",
                flags="cnN",
                separator=",",
            ).strip().splitlines()

            anchored_clumps: set[int] = set()
            for ln in anchor_lines:
                parts = ln.split(",")
                if len(parts) < 4:
                    continue
                cid = _parse_stat_value(parts[0])
                bid = _parse_stat_value(parts[1])
                anchor = _parse_stat_value(parts[2])
                if cid is not None and bid is not None and anchor == bid:
                    anchored_clumps.add(cid)

            all_clumps = {cid for cid, _ in _pairs(cstats)}
            drop_clumps = sorted(all_clumps - anchored_clumps)

            print(f"  anchored clumps: {len(anchored_clumps)}")
            print(f"  detached clumps to reassign: {len(drop_clumps)}")

            if not drop_clumps:
                print("  ✓ no detached merged/fill clumps remain; stopping")
                break

            drop_basin_ids: set[int] = set()
            if drop_clumps:
                drop_id_set = set(drop_clumps)
                cb_lines = gs.read_command(
                    "r.stats", input=f"{clumps},{clump_basin}", flags="cn", separator=","
                ).strip().splitlines()
                for ln in cb_lines:
                    parts = ln.split(",")
                    if len(parts) < 3:
                        continue
                    cid = _parse_stat_value(parts[0])
                    bid = _parse_stat_value(parts[1])
                    if cid in drop_id_set and bid is not None:
                        drop_basin_ids.add(bid)
            if drop_basin_ids:
                try:
                    print(f"  affected basin labels: {len(drop_basin_ids)}")
                except Exception:
                    pass

            rules_dropmask = rule_dir / f"dropmask_reclass_{tag}_iter{it}.txt"
            with open(rules_dropmask, "w", encoding="utf-8") as f:
                for cid in drop_clumps:
                    f.write(f"{cid} = 1\n")
                f.write("* = NULL\n")
            gs.run_command("r.reclass", input=clumps, output=dropmask, rules=str(rules_dropmask), overwrite=True)

            current_nulled = f"{current}_nulled"
            gs.mapcalc(f"{current_nulled} = if(!isnull({dropmask}), null(), {current})", overwrite=True)
            gs.run_command("r.grow.distance", input=current_nulled, value=refill, flags="m", overwrite=True)
            gs.mapcalc(
                f"{current} = if(!isnull({basins_coverage}), if(isnull({current_nulled}), {refill}, {current_nulled}), null())",
                overwrite=True,
            )

    def _run_connected_label_passes(label_map: str, prefix: str, title: str) -> str:
        current_map = label_map
        last_dropped = 0
        for it in range(1, max_exclave_iters + 1):
            print(f"\n→ {title} {it}")
            current_map, last_dropped = _force_single_component_per_label(current_map, f"{prefix}{it}")
            if not last_dropped:
                print("  ✓ every basin label is a single connected component")
                return current_map
        raise RuntimeError(
            f"{title} did not converge after {max_exclave_iters} pass(es); "
            f"last pass still reassigned {last_dropped} detached component(s)."
        )

    if do_exclave_cleanup:
        current = _run_connected_label_passes(current, "conn", "Connected-label pass")

    final_sizes = _read_rstats_cn(current)
    final_big_ids = {cat for cat, n in final_sizes.items() if n >= min_cells}
    final_small_ids = sorted(set(final_sizes) - final_big_ids)

    if final_small_ids:
        if not final_big_ids:
            largest = max(final_sizes.items(), key=lambda kv: kv[1])[0]
            final_big_ids = {largest}
            final_small_ids = sorted(set(final_sizes) - final_big_ids)
            print(f"ℹ️ Final size pass: all basins < threshold; keeping largest basin {largest}")

        print(f"\n→ Final size pass: merging {len(final_small_ids)} basin(s) still below threshold")

        rules_big_final = rule_dir / f"final_big_reclass_{tag}.txt"
        with open(rules_big_final, "w", encoding="utf-8") as f:
            for cat in final_sizes:
                if cat in final_big_ids:
                    f.write(f"{cat} = {cat}\n")
                else:
                    f.write(f"{cat} = NULL\n")

        gs.run_command("r.reclass", input=current, output=final_big_only, rules=str(rules_big_final), overwrite=True)

        final_small_set = set(final_small_ids)
        final_small_to_big = _nearest_big_counts(current, final_big_only, final_small_set, final_nearest_big_id)

        rules_final_path = rule_dir / f"final_size_reclass_{tag}.txt"
        with open(rules_final_path, "w", encoding="utf-8") as f:
            for bid in sorted(final_big_ids):
                f.write(f"{bid} = {bid}\n")
            for sid in final_small_ids:
                counts = final_small_to_big.get(sid)
                if not counts:
                    f.write(f"{sid} = {sid}\n")
                    continue
                chosen = max(counts.items(), key=lambda kv: kv[1])[0]
                f.write(f"{sid} = {chosen}\n")

        gs.run_command("r.reclass", input=current, output=basins_final_sized, rules=str(rules_final_path), overwrite=True)
        current = basins_final_sized
    else:
        print("\n✓ Final size pass: no basins below threshold")

    if do_exclave_cleanup:
        current = _run_connected_label_passes(current, "finalconn", "Final connected-label pass")

    gs.run_command("r.grow.distance", input=current, value=final_refill, flags="m", overwrite=True)
    gs.mapcalc(
        f"{current} = if(!isnull({basins_coverage}), if(isnull({current}), {final_refill}, {current}), null())",
        overwrite=True,
    )
    gs.mapcalc(f"{basins_merged_final} = int({current})", overwrite=True)

    out_path = out_dir_p / out_name
    export_geotiff(basins_merged_final, out_path, gdal_type="Int32", nodata=-9999, force=True)
    print("✅ Done →", out_path)
    return out_path
