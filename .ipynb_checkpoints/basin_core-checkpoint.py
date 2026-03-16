# basin_core.py
from __future__ import annotations
from typing import Dict, List, Tuple, Optional
import os
import sys
from pathlib import Path
import shutil
import subprocess

# =============================================================================
# SHARED DEFAULTS
# =============================================================================
#QGIS_PREFIX_DEFAULT = r"C:\Program Files\QGIS 3.40.11"
#QGIS_PREFIX_DEFAULT = "/Applications/QGIS-LTR.app/Contents/MacOS"
QGIS_PREFIX_DEFAULT = "/Applications/GRASS-8.4.app/Contents/Resources"



# Filled after setup_grass_env() is called
gs = None
gsetup = None
find_program = None
CalledModuleError = RuntimeError


# =============================================================================
# GRASS ENVIRONMENT INIT (EXACTLY LIKE YOUR WORKING SCRIPT)
# =============================================================================
def _grass_subprocess_env() -> dict:
    env = os.environ.copy()
    gisbase = Path(env["GISBASE"])

    # remove Conda paths
    env.pop("PROJ_LIB", None)
    env.pop("PROJ_DATA", None)
    env.pop("GDAL_DATA", None)
    env.pop("GDAL_DRIVER_PATH", None)

    # point to GRASS data dirs
    env["PROJ_LIB"]  = str(gisbase / "share" / "proj")
    env["PROJ_DATA"] = str(gisbase / "share" / "proj")
    env["GDAL_DATA"] = str(gisbase / "share" / "gdal")
    env.setdefault("GTIFF_SRS_SOURCE", "EPSG")
    return env


def _grass_bin() -> str:
    grass_bin = (Path(os.environ["GISBASE"]) / "bin" / "grass").resolve()
    if not grass_bin.exists():
        raise RuntimeError(f"GRASS executable not found: {grass_bin}")
    return str(grass_bin)

def ensure_location(dem_path: str, gisdbase: Path, location: str) -> Path:
    """
    Ensure a valid GRASS Location exists (PERMANENT/DEFAULT_WIND must exist).
    If location exists but is invalid (e.g. empty PERMANENT), recreate it.
    """
    loc_path = gisdbase / location
    default_wind = loc_path / "PERMANENT" / "DEFAULT_WIND"

    if default_wind.exists():
        return loc_path

    # Location exists but broken, or doesn't exist at all -> recreate
    if loc_path.exists():
        shutil.rmtree(loc_path)

    raster = str(Path(dem_path).resolve()).replace("\\", "/")
    cmd = [_grass_bin(), "--text", "-c", raster, "-e", str(loc_path)]
    print("→", " ".join(cmd))
    #subprocess.run(cmd, check=True, env=os.environ.copy())
    subprocess.run(cmd, check=True, env=_grass_subprocess_env())


    return loc_path

def ensure_mapset(gisdbase: Path, location: str, mapset: str):
    """
    Ensure mapset exists inside location (creates it via g.mapset -c).
    """
    if mapset == "PERMANENT":
        return

    loc_path = gisdbase / location
    mapset_path = loc_path / mapset
    if mapset_path.exists():
        return

    cmd = [
        _grass_bin(),
        str(loc_path / "PERMANENT"),
        "--exec",
        "g.mapset",
        "-c",
        f"mapset={mapset}",
    ]
    print("→", " ".join(cmd))
    subprocess.run(cmd, check=True, env=os.environ.copy())

def setup_grass_env2(qgis_prefix="/Applications/QGIS-LTR.app/Contents/MacOS"):
    import os, sys, platform
    from pathlib import Path
    
    if platform.system() != "Darwin":
        raise RuntimeError("This setup_grass_env() is for macOS. Use your Windows version on Windows.")
    
    p = Path(qgis_prefix).resolve()
    
    # Accept passing:
    #   /Applications/QGIS-LTR.app
    #   /Applications/QGIS-LTR.app/Contents
    #   /Applications/QGIS-LTR.app/Contents/MacOS
    if p.suffix == ".app":
        contents = p / "Contents"
    elif p.name == "Contents":
        contents = p
    elif p.name == "MacOS":
        contents = p.parent
    else:
    # try to find a parent named "Contents"
        contents = next((pp for pp in [p, *p.parents] if pp.name == "Contents"), None)
    if contents is None:
        raise RuntimeError(f"Could not locate QGIS app 'Contents' from: {p}")
    
    resources = contents / "Resources"
    if not resources.exists():
        raise RuntimeError(f"QGIS Resources folder not found at: {resources}")
    
    # Pick the GRASS folder that actually exists (grass78, grass82, grass84, ...)
    grass_candidates = sorted([d for d in resources.glob("grass*") if d.is_dir()])
    if not grass_candidates:
        raise RuntimeError(f"No GRASS folder found in: {resources}\nFound: {list(resources.iterdir())[:20]}")
    
    GISBASE = grass_candidates[-1]  # choose highest (last lexicographically)
    
    grass_python = GISBASE / "etc" / "python"
    init_py = grass_python / "grass" / "__init__.py"
    if not init_py.exists():
        raise RuntimeError(
        f"GRASS python package not found at: {init_py}\n"
        f"GISBASE={GISBASE}\n"
        f"Contents of {grass_python}: {list(grass_python.glob('*'))[:20]}"
    )
    
    os.environ["GISBASE"] = str(GISBASE)
    os.environ["PATH"] = os.pathsep.join([
    str(GISBASE / "bin"),
    str(GISBASE / "scripts"),
    os.environ.get("PATH", ""),
    ])
    
    if str(grass_python) not in sys.path:
        sys.path.insert(0, str(grass_python))
    
    # Make GRASS python visible to subprocess scripts (e.g. r.mask)
    pp = os.environ.get("PYTHONPATH", "")
    os.environ["PYTHONPATH"] = str(grass_python) + (os.pathsep + pp if pp else "")
    
    # Prevent conda GDAL plugin path from leaking into GRASS
    os.environ.pop("GDAL_DRIVER_PATH", None)
    
    
    import grass.script as _gs
    import grass.script.setup as _gsetup
    from grass.script.core import find_program as _find_program
    from grass.script.core import CalledModuleError as _CalledModuleError
    
    global gs, gsetup, find_program, CalledModuleError
    gs = _gs
    gsetup = _gsetup
    find_program = _find_program
    CalledModuleError = _CalledModuleError
    
    print("✅ QGIS Contents:", contents)
    print("✅ GISBASE:", GISBASE)
    print("✅ grass_python:", grass_python)

def setup_grass_env(grass_gisbase="/Applications/GRASS-8.4.app/Contents/Resources"):
    import os, sys, platform
    from pathlib import Path

    if platform.system() != "Darwin":
        raise RuntimeError("macOS only here; keep Windows code for Windows.")

    GISBASE = Path(grass_gisbase).resolve()
    grass_python = GISBASE / "etc" / "python"
    init_py = grass_python / "grass" / "__init__.py"
    if not init_py.exists():
        raise RuntimeError(f"GRASS python not found at: {init_py}")

    # Core env
    os.environ["GISBASE"] = str(GISBASE)
    os.environ["PATH"] = os.pathsep.join([
        str(GISBASE / "bin"),
        str(GISBASE / "scripts"),
        os.environ.get("PATH", ""),
    ])

    # Make grass.script importable both in this python AND in GRASS python scripts (r.mask, v.import, ...)
    if str(grass_python) not in sys.path:
        sys.path.insert(0, str(grass_python))
    os.environ["PYTHONPATH"] = str(grass_python) + os.pathsep + os.environ.get("PYTHONPATH", "")

    # Locale warning fix
    os.environ.setdefault("LANG", "en_US.UTF-8")
    os.environ.setdefault("LC_ALL", "en_US.UTF-8")

    # Avoid conda GDAL plugin interference
    os.environ.pop("GDAL_DRIVER_PATH", None)

    # ---- Force GRASS PROJ/GDAL data (avoid Conda proj.db) ----
    os.environ.pop("PROJ_LIB", None)
    os.environ.pop("PROJ_DATA", None)
    os.environ.pop("GDAL_DATA", None)

    os.environ["PROJ_LIB"]  = str(GISBASE / "share" / "proj")
    os.environ["PROJ_DATA"] = str(GISBASE / "share" / "proj")   # some builds use PROJ_DATA
    os.environ["GDAL_DATA"] = str(GISBASE / "share" / "gdal")

    # Optional: helps EPSG:3413 warnings with GeoTIFFs sometimes
    os.environ.setdefault("GTIFF_SRS_SOURCE", "EPSG")


    import grass.script as _gs
    import grass.script.setup as _gsetup
    from grass.script.core import find_program as _find_program
    from grass.script.core import CalledModuleError as _CalledModuleError

    global gs, gsetup, find_program, CalledModuleError
    gs = _gs
    gsetup = _gsetup
    find_program = _find_program
    CalledModuleError = _CalledModuleError

    print("✅ GISBASE:", GISBASE)
    print("✅ grass_python:", grass_python)


def safe(expr: str):
    """
    Convenience wrapper for r.mapcalc with better error messages.
    Uses the global 'gs' that setup_grass_env() initialises.
    """
    try:
        gs.run_command("r.mapcalc", expression=expr, overwrite=True)
    except CalledModuleError as e:
        raise RuntimeError(f"Mapcalc failed: {expr}\n{e}")


# =============================================================================
# GRASS SESSION HELPERS
# =============================================================================
def start_grass_from_raster2(raster_path, location="dem_loc", mapset="PERMANENT"):
    """
    Start GRASS in ~/Documents/grassdata based on a DEM (macOS / QGIS-bundled GRASS).
    Creates the location using the GRASS launcher (NOT gs.core.create_location),
    then initialises the Python session with gsetup.init().
    """
    import os
    import subprocess
    import shutil
    from pathlib import Path

    gisdbase = Path.home() / "Documents" / "grassdata"
    gisdbase.mkdir(parents=True, exist_ok=True)

    loc_path = gisdbase / location
    mapset_path = loc_path / mapset

    raster = str(Path(raster_path)).replace("\\", "/")

    # --- create location if needed (via GRASS launcher) ---
    if not loc_path.exists():
        #GISBASE = Path(os.environ["GISBASE"])  # set in setup_grass_env()

        GISBASE = os.environ["GISBASE"]
        gsetup.init(GISBASE, str(gisdbase), location, mapset)


        #grass_launcher = _find_qgis_grass_launcher()
        #cmd = [grass_launcher, "--text", "-c", raster, "-e", str(loc_path)]
        #print("→", " ".join(cmd))
        #subprocess.run(cmd, check=True, env=os.environ.copy())

        grass_launcher = "/Applications/GRASS-8.4.app/Contents/Resources/bin/grass"
        cmd = [grass_launcher, "--text", "-c", raster, "-e", str(loc_path)]
        subprocess.run(cmd, check=True, env=os.environ.copy())




    else:
        print(f"✅ Using existing GRASS location: {loc_path}")

    # --- now init GRASS Python session ---
    GISBASE = os.environ["GISBASE"]
    gsetup.init(GISBASE, str(gisdbase), location, mapset)

    print(f"🌿 GRASS session initialized in:\n   {mapset_path}\n")
    print(gs.read_command("g.gisenv"))

    return str(gisdbase), location, mapset

def start_grass_from_raster(raster_path, location="dem_loc", mapset="MC_WORK"):
    """
    Ensure GRASS location+mapset exist (creating/repairing if needed),
    then init a GRASS Python session.
    """
    gisdbase = Path.home() / "Documents" / "grassdata"
    gisdbase.mkdir(parents=True, exist_ok=True)

    # Create/repair location (fixes your empty PERMANENT issue)
    ensure_location(raster_path, gisdbase, location)

    # Create work mapset if needed
    ensure_mapset(gisdbase, location, mapset)

    # Init Python session
    GISBASE = os.environ["GISBASE"]
    gsetup.init(str(gisdbase), location=location, mapset=mapset, grass_path=GISBASE)

    print(f"🌿 GRASS session initialized in:\n   {gisdbase / location / mapset}\n")
    print(gs.read_command("g.gisenv"))

    return str(gisdbase), location, mapset



def _find_qgis_grass_launcher():
    import os, subprocess
    from pathlib import Path

    GISBASE = Path(os.environ["GISBASE"]).resolve()
    contents = GISBASE.parent.parent  # .../QGIS-LTR.app/Contents

    # Only search inside the QGIS bundle / GISBASE (avoid Conda's "grass")
    roots = [
        GISBASE,
        contents / "MacOS",
        contents / "Resources",
    ]

    candidates = []
    for root in roots:
        if not root.exists():
            continue
        # Look for possible launchers
        for p in root.rglob("grass*"):
            # Must be a file (not a dir/symlink-to-dir) and executable
            if p.is_file() and os.access(p, os.X_OK):
                candidates.append(p)

    # Prefer shorter paths first (often the real launcher is nearer the root)
    for p in sorted(set(candidates), key=lambda x: len(str(x))):
        try:
            r = subprocess.run([str(p), "--help"], capture_output=True, text=True)
            helptext = (r.stdout or "") + (r.stderr or "")
            # Real GRASS startup help contains "-c" and mentions GISDBASE/PROJECT/MAPSET
            if ("-c" in helptext) and ("GISDBASE" in helptext or "PROJECT" in helptext):
                print("✅ Using GRASS launcher:", p)
                return str(p)
        except Exception:
            pass

    raise RuntimeError(
        "Could not find a real GRASS startup program inside the QGIS bundle/GISBASE.\n"
        "The paths in Contents/MacOS/grass* are often symlinks to directories (not launchers).\n"
        f"GISBASE={GISBASE}"
    )


def import_dem_native(input_path, out_name="dem"):
    """Import or clone DEM to a native GRASS raster."""
    raster = input_path.replace("\\", "/")
    try:
        gs.run_command(
            "r.in.gdal",
            input=raster,
            output=out_name,
            flags="o",
            overwrite=True
        )
        print(f"✓ r.in.gdal → {out_name}")
    except Exception:
        gs.run_command(
            "r.external",
            input=raster,
            output=f"{out_name}_ext",
            flags="o",
            overwrite=True
        )
        gs.run_command("g.region", raster=f"{out_name}_ext")
        gs.mapcalc(f"{out_name} = {out_name}_ext * 1.0", overwrite=True)
        print(f"✓ r.external + clone → {out_name}")
    gs.run_command("g.region", raster=out_name)


# =============================================================================
# ADDONS + HELPERS
# =============================================================================
def ensure_grass_addon(module_name: str):
    from pathlib import Path
    import os, platform

    if platform.system() == "Darwin":
        #addon_base = Path.home() / ".grass7" / "addons"
        addon_base = Path.home() / ".grass8" / "addons"

    else:
        APPDATA = os.environ.get("APPDATA", str(Path.home()))
        addon_base = Path(APPDATA) / "GRASS8" / "addons"

    addon_base.mkdir(parents=True, exist_ok=True)
    os.environ["GRASS_ADDON_BASE"] = str(addon_base)

    addon_bin = addon_base / "bin"
    addon_scripts = addon_base / "scripts"

    os.environ["PATH"] = os.pathsep.join([
        str(addon_bin),
        str(addon_scripts),
        os.environ.get("PATH", "")
    ])
    os.environ["GRASS_ADDON_PATH"] = os.pathsep.join([str(addon_bin), str(addon_scripts)])

    if find_program(module_name) is None:
        gs.run_command("g.extension", extension=module_name, operation="add", flags="f")

    path = find_program(module_name)
    if path is None:
        inst = gs.read_command("g.extension", flags="l")
        raise RuntimeError(
            f"{module_name} not found after install.\n"
            f"GRASS_ADDON_BASE={addon_base}\n"
            f"Installed addons (truncated):\n{inst[:600]}"
        )
    print(f"✓ {module_name} at {path}")



# =============================================================================
# HYDRAULIC POTENTIAL
# =============================================================================
def compute_hydraulic_potential(
    ice_surface: str,
    bed: str,
    *,
    surface_is_file: bool = True,
    bed_is_file: bool = True,
    out_map: str = "hydraulic_potential",
    out_tif: str | None = None,
    smooth_surface: bool = False,
    smooth_size: int = 15,
    rho_w: float = 1000.0,
    rho_i: float = 917.0,
    g: float = 9.81,
):
    """
    Compute hydraulic potential:
        phi = ρ_w g z_b + ρ_i g (z_s - z_b)
    """
    # Import or use existing rasters
    if surface_is_file:
        gs.run_command(
            "r.import",
            input=ice_surface,
            output="ice_surface",
            overwrite=True,
        )
        surf_map = "ice_surface"
    else:
        surf_map = ice_surface

    if bed_is_file:
        gs.run_command(
            "r.import",
            input=bed,
            output="bed_raster",
            overwrite=True,
        )
        bed_map = "bed_raster"
    else:
        bed_map = bed

    # Optional smoothing of surface
    if smooth_surface:
        gs.run_command(
            "r.neighbors",
            input=surf_map,
            output="surface_smoothed",
            method="average",
            size=smooth_size,
            overwrite=True,
        )
        surf_eff = "surface_smoothed"
    else:
        surf_eff = surf_map

    expr = (
        f"{out_map} = "
        f"(({rho_w} * {g} * {bed_map}) + "
        f"({rho_i} * {g} * ({surf_eff} - {bed_map}))) / 1000000.0"
    )
    safe(expr)
    print(f"✓ Computed hydraulic potential → {out_map}")

    if out_tif is not None:
        out_tif_norm = out_tif.replace("\\", "/")
        gs.run_command(
            "r.out.gdal",
            input=out_map,
            output=out_tif_norm,
            format="GTiff",
            type="Float64",
            createopt="COMPRESS=LZW,TILED=YES,BIGTIFF=YES",
            overwrite=True,
        )
        print(f"📤 Exported hydraulic potential to {out_tif_norm}")

    return out_map


def fill_dem_holes(input_map: str,
                   output_map: str = "dem_filled",
                   method: str = "bilinear") -> str:
    """
    Fill internal NoData (NULL) holes in `input_map` using r.fillnulls.
    Returns the name of the DEM to use.
    """
    try:
        gs.run_command("r.mask", flags="r")
    except Exception:
        pass

    gs.run_command("g.region", raster=input_map)

    null_cells = None
    try:
        stats = gs.read_command("r.univar", map=input_map, flags="g")
        for line in stats.splitlines():
            if line.startswith("null_cells=") or line.startswith("null="):
                null_cells = int(float(line.split("=")[1]))
                break
    except Exception:
        null_cells = None

    if null_cells is not None and null_cells == 0:
        print(f"🔧 No NULL cells in {input_map} – skipping r.fillnulls")
        return input_map

    print(f"🔧 Filling DEM holes in {input_map} with r.fillnulls (method={method})")
    try:
        gs.run_command(
            "r.fillnulls",
            input=input_map,
            output=output_map,
            method=method,
            overwrite=True,
        )
        print(f"✓ {output_map} created and used as hole-free DEM")
        return output_map
    except CalledModuleError as e:
        print(f"⚠️ r.fillnulls failed on {input_map}, using raw DEM instead.\n   {e}")
        return input_map


def smooth_dem(input_map: str,
               output_map: str = "dem_smoothed",
               size: int = 3,
               method: str = "average",
               memory: int = 300) -> str:
    """
    Smooth DEM with r.neighbors. Returns `output_map`.
    """
    if size <= 1:
        print(f"🔧 Smoothing disabled (size={size}), using {input_map} unchanged")
        return input_map

    print(f"🔧 Smoothing {input_map} with r.neighbors (size={size}, method={method})")
    gs.run_command(
        "r.neighbors",
        input=input_map,
        output=output_map,
        method=method,
        size=size,
        memory=memory,
        overwrite=True,
    )
    print(f"✓ Smoothed DEM → {output_map}")
    return output_map


def fill_sinks_hydrodem(input_map: str,
                        output_map: str = "dem_hydro") -> str:
    """
    Fill sinks for hydrology using r.hydrodem.
    """
    try:
        gs.run_command("r.mask", flags="r")
    except Exception:
        pass

    gs.run_command("g.region", raster=input_map)

    print(f"🔧 Running r.hydrodem on {input_map} → {output_map}")
    try:
        gs.run_command(
            "r.hydrodem",
            input=input_map,
            output=output_map,
            overwrite=True,
        )
        print(f"✓ r.hydrodem succeeded → {output_map}")
        return output_map
    except CalledModuleError as e:
        print("⚠️ r.hydrodem failed or unavailable – using input DEM instead.")
        print("   Error:", e)
        return input_map


def run_hydro_pipeline(
    hydro_dem: str,
    out_dir: str,
    stream_threshold: int,
    tag: str = "",
    run_idx: int | None = None,
    perturbation_map: str | None = None,
    watershed_memory: int = 600,
    watershed_lowmem: bool = True,
):
    """
    Generic hydrology pipeline:

      hydro_dem  → r.watershed → r.stream.extract → r.stream.basins
                  → export (dem_hydro{tag}, accum_hydro{tag}, ...)
    """
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    gs.run_command("g.region", raster=hydro_dem, flags="p")

    try:
        gs.run_command("r.mask", raster="ice_mask_rast", overwrite=True)
        print("✓ Mask set from ice_mask_rast")
    except Exception:
        print("⚠️ Could not set r.mask from ice_mask_rast (continuing without mask)")

    accum    = f"accum{tag}"
    flow_dir = f"flow_dir{tag}"
    streams  = f"streams{tag}"
    basins   = f"basins{tag}"

    ws_flags = "m" if watershed_lowmem else ""

    print(f"🔧 Running r.watershed on {hydro_dem}")
    gs.run_command(
        "r.watershed",
        elevation=hydro_dem,
        accumulation=accum,
        drainage=flow_dir,
        memory=watershed_memory,
        flags=ws_flags,
        overwrite=True,
    )
    print("✓ r.watershed completed")

    gs.run_command(
        "r.stream.extract",
        elevation=hydro_dem,
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
        print(f"⚠️ r.stream.basins failed for {basins}; creating empty basins raster.")
        print("   Error:", e)
        safe(f"{basins} = 0")

    dtype = {
        hydro_dem: "Float32",
        accum:     "Float64",
        flow_dir:  "Int32",
        streams:   "Int16",
        basins:    "Int32",
    }

    exports = [
        (hydro_dem, f"{out_dir}/dem_hydro{tag}.tif"),
        #(accum,     f"{out_dir}/accum_hydro{tag}.tif"),
        (flow_dir,  f"{out_dir}/flowdir_hydro{tag}.tif"),
        (streams,   f"{out_dir}/streams_hydro{tag}.tif"),
        (basins,    f"{out_dir}/basins_hydro{tag}.tif"),
    ]

    for name, fn in exports:
        fn_norm = fn.replace("\\", "/")
        try:
            info = gs.read_command("r.info", map=name)
            if "min =" in info and "max =" in info:
                print(f"📤 Exporting {name} → {fn_norm}")
                gs.run_command(
                    "r.out.gdal",
                    input=name,
                    output=fn_norm,
                    format="GTiff",
                    type=dtype.get(name, "Float32"),
                    createopt="COMPRESS=LZW,TILED=YES,BIGTIFF=YES",
                    nodata=-9999,
                    overwrite=True,
                )
            else:
                print(f"⚠️ Skipping {name}: no data detected.")
        except Exception as e:
            print(f"❌ Failed to export {name}: {e}")

    if perturbation_map is not None:
        pert_out = f"{out_dir}/perturbation{tag}.tif"
        pert_out_norm = pert_out.replace("\\", "/")
        try:
            info = gs.read_command("r.info", map=perturbation_map)
            if "min =" in info and "max =" in info:
                print(f"📤 Exporting {perturbation_map} → {pert_out_norm}")
                gs.run_command(
                    "r.out.gdal",
                    input=perturbation_map,
                    output=pert_out_norm,
                    format="GTiff",
                    createopt="COMPRESS=LZW,TILED=YES,BIGTIFF=YES",
                    overwrite=True,
                )
        except Exception as e:
            print(f"❌ Failed to export perturbation {perturbation_map}: {e}")

    try:
        gs.run_command("r.mask", flags="r")
    except Exception:
        pass

    print(f"✅ Finished hydrology for DEM '{hydro_dem}' (tag='{tag}')")


def run_hydro_for_dem(
    dem_name: str,
    out_dir: str,
    stream_threshold: int,
    run_idx: int | None = None,
    smooth: bool = False,
    smooth_size: int = 3,
    perturbation_map: str | None = None,
    watershed_memory: int = 600,
    watershed_lowmem: bool = True,
):
    """
    Convenience wrapper: optionally smooth DEM, then run hydrology pipeline.

    - dem_name: input DEM (e.g. dem_mc_001)
    - smooth: if True and smooth_size > 1, apply r.neighbors before hydrology
    - smooth_size: smoothing window size in cells
    """
    hydro_dem = dem_name
    if smooth and (smooth_size is not None) and (smooth_size > 1):
        hydro_dem = smooth_dem(
            input_map=dem_name,
            output_map=f"{dem_name}_smoothed",
            size=smooth_size,
        )

    tag = f"_mc_{run_idx:03d}" if run_idx is not None else ""

    run_hydro_pipeline(
        hydro_dem=hydro_dem,
        out_dir=out_dir,
        stream_threshold=stream_threshold,
        tag=tag,
        run_idx=run_idx,
        perturbation_map=perturbation_map,
        watershed_memory=watershed_memory,
        watershed_lowmem=watershed_lowmem,
    )


def prepare_dem_and_mask(
    dem_path: str,
    ice_mask_path: str,
    *,
    fill_holes: bool = False,
    fill_method: str = "bilinear",
    filled_name: str = "dem_filled",
):
    """
    Prepare DEM + ice mask in GRASS using a vector GPKG mask.

    Steps (mirrors your stable script):
      1) Clear any existing MASK
      2) Import DEM as 'dem'
      3) Optionally fill internal holes with r.fillnulls → 'filled_name'
      4) Set region to the DEM used for hydrology
      5) Import ice mask vector (GPKG) → ice_mask_vec
      6) v.to.rast → ice_mask_rast on the DEM grid
      7) r.mask raster=ice_mask_rast

    Returns
    -------
    dem_for_hydro : str
        GRASS raster name of the DEM to use ('dem' or 'filled_name').
    ice_mask_rast : str
        GRASS raster name of the mask ('ice_mask_rast').
    """
    # 0) Clear any existing MASK
    try:
        gs.run_command("r.mask", flags="r")
    except Exception:
        pass

    # 1) DEM → 'dem'
    import_dem_native(dem_path, out_name="dem")

    # 2) Optional hole filling ONCE, without mask
    dem_for_hydro = "dem"
    if fill_holes:
        dem_for_hydro = fill_dem_holes(
            input_map="dem",
            output_map=filled_name,
            method=fill_method,
        )

    # 3) Set region to the DEM we'll actually use
    gs.run_command("g.region", raster=dem_for_hydro, flags="p")

    # 4) Import ice mask vector (GPKG) → ice_mask_vec
    ice_mask_abs = str(Path(ice_mask_path).resolve()).replace("\\", "/")
    print(f"→ Importing vector ice mask from: {ice_mask_abs}")

    try:
        gs.run_command(
            "v.import",
            input=ice_mask_abs,
            output="ice_mask_vec",
            overwrite=True,
        )
        print("✓ Ice mask imported with v.import → ice_mask_vec")
    except CalledModuleError as e:
        print("⚠️ v.import failed, falling back to v.in.ogr")
        print(f"   v.import error: {e}")
        gs.run_command(
            "v.in.ogr",
            input=ice_mask_abs,
            output="ice_mask_vec",
            overwrite=True,
        )
        print("✓ Ice mask imported with v.in.ogr → ice_mask_vec")

    # 5) Ensure region is still aligned to DEM grid before rasterising
    gs.run_command("g.region", raster=dem_for_hydro)

    # 6) Rasterise mask → ice_mask_rast
    gs.run_command(
        "v.to.rast",
        input="ice_mask_vec",
        output="ice_mask_rast",
        use="val",
        value=1,
        overwrite=True,
    )
    print("✓ Ice mask rasterized → ice_mask_rast")

    # 7) Apply mask
    gs.run_command("r.mask", raster="ice_mask_rast", overwrite=True)
    print("✓ Mask set from ice mask")

    return dem_for_hydro, "ice_mask_rast"

def build_hybrid_dem_for_run_mask(
    surface_dem: str,
    bed_raster: str,
    inside_mask_raster: str,
    *,
    tag: str,
    smooth_size: int = 3,
    rho_w: float = 1000.0,
    rho_i: float = 917.0,
    g: float = 9.81,
):
    """
    Hybrid DEM using a RASTER mask (not vector):
      inside_mask_raster has data inside (>=2000m), NULL outside.

    Inside (mask==1): smoothed surface DEM (filled)
    Outside           : hydraulic potential from bed + smoothed surface (filled)
    """
    # Clear MASK while constructing hybrid surfaces
    try:
        gs.run_command("r.mask", flags="r")
    except Exception:
        pass

    gs.run_command("g.region", raster=surface_dem)

    # 1) Smooth surface
    surface_smooth = f"surface_dem_smooth{tag}"
    gs.run_command(
        "r.neighbors",
        input=surface_dem,
        output=surface_smooth,
        method="average",
        size=smooth_size,
        overwrite=True,
    )

    # 2) Hydraulic potential
    bed_dem = f"bed_dem{tag}"
    safe(
        f"{bed_dem} = (({rho_w} * {g} * {bed_raster}) + "
        f"({rho_i} * {g} * ({surface_smooth} - {bed_raster})))/1000000.0"
    )

    # 3) Build inside/outside masks from raster (NULL outside)
    inside_mask = f"inside_mask{tag}"
    safe(f"{inside_mask} = if(isnull({inside_mask_raster}), 0, 1)")

    outside_mask = f"outside_mask{tag}"
    safe(f"{outside_mask} = if({inside_mask} == 0, 1, 0)")

    # 4) Mask surfaces
    surface_masked = f"surface_masked{tag}"
    bed_masked     = f"bed_masked{tag}"
    safe(f"{surface_masked} = if({inside_mask} == 1, {surface_smooth}, null())")
    safe(f"{bed_masked}     = if({outside_mask} == 1, {bed_dem}, null())")

    # 5) Fill separately
    dem_filledA = f"dem_filledA{tag}"
    dem_filledB = f"dem_filledB{tag}"
    flow_dirA   = f"flow_dirA{tag}"
    flow_dirB   = f"flow_dirB{tag}"

    gs.run_command("r.fill.dir", input=surface_masked, output=dem_filledA,
                   direction=flow_dirA, overwrite=True)
    gs.run_command("r.fill.dir", input=bed_masked, output=dem_filledB,
                   direction=flow_dirB, overwrite=True)

    # 6) Combine
    hybrid_dem = f"hybrid_dem{tag}"
    safe(f"{hybrid_dem} = if({inside_mask} == 1, {dem_filledA}, {dem_filledB})")

    return hybrid_dem


def merge_basins(
    *,
    gs=None,                  # <- changed
    basins_input: str,
    dem_path: str,
    out_dir: str,
    min_basin_size_km2: float = 500.0,
    res_m: int = 500,
    do_exclave_cleanup: bool = True,
    max_exclave_iters: int = 6,
    out_name: str = "basins_merged_no_small_fullcover.tif",
):
    # --- add this block ---
    if gs is None:
        if globals().get("gs", None) is None:
            raise RuntimeError(
                "merge_basins needs a live GRASS session. "
                "Call setup_grass_env() + start_grass_from_raster() first, "
                "or pass gs=bc.gs explicitly."
            )
        gs = globals()["gs"]
    """
    Workflow:
      1) Import DEM + basins, set region to DEM @ res_m
      2) Merge all basins smaller than min_basin_size_km2 into nearest *big* basin,
         using whole-basin reassignment (no splitting)
      3) Fill NULL pixels inside DEM extent with nearest neighbor basin ID
      4) Threshold-free anti-exclave cleanup (iterative):
         - r.clump contiguous patches
         - for each basin ID that appears in >1 clump:
             keep largest clump, null the others
             refill nulled pixels from nearest neighbor basin
         - repeat until stable or max_exclave_iters
      5) Export GeoTIFF

    Assumes:
      - `gs` is a live GRASS session (e.g., gs = bc.gs) and projection matches DEM.
      - DEM defines your domain; outside DEM -> NULL.
    """
    out_dir_p = Path(out_dir)
    out_dir_p.mkdir(parents=True, exist_ok=True)

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

    # --------------------------
    # Import + region
    # --------------------------
    gs.run_command("r.in.gdal", input=dem_path.replace("\\", "/"), output="dem", overwrite=True)
    gs.run_command("r.in.gdal", input=basins_input.replace("\\", "/"), output="basins_in", overwrite=True)
    gs.run_command("g.region", raster="dem", res=res_m, align="dem")

    # int labels + mask to DEM extent
    gs.mapcalc("basins0 = int(basins_in)", overwrite=True)
    gs.mapcalc("basins0 = if(isnull(dem), null(), basins0)", overwrite=True)

    # --------------------------
    # Determine big vs small
    # --------------------------
    cell_area_km2 = (res_m * res_m) / 1e6
    min_cells = int(round(min_basin_size_km2 / cell_area_km2))
    print(f"→ Merge threshold: {min_basin_size_km2} km² ≈ {min_cells} cells at {res_m} m")

    sizes = _read_rstats_cn("basins0")
    if not sizes:
        raise RuntimeError("No basins found within DEM extent.")

    big_ids = {cat for cat, n in sizes.items() if n >= min_cells}
    small_ids = sorted(set(sizes) - big_ids)

    if not big_ids:
        largest = max(sizes.items(), key=lambda kv: kv[1])[0]
        big_ids = {largest}
        small_ids = sorted(set(sizes) - big_ids)
        print(f"ℹ️ All basins < threshold; seeding with largest basin {largest}")

    print(f"→ Big basins: {len(big_ids)}  |  Small basins: {len(small_ids)}")

    # --------------------------
    # Merge small -> big (whole-basin reassignment)
    # --------------------------
    if not small_ids:
        gs.mapcalc("basins_after_merge = basins0", overwrite=True)
    else:
        # big-only raster
        rules_big = out_dir_p / "big_reclass.txt"
        with open(rules_big, "w", encoding="utf-8") as f:
            for cat in sizes:
                f.write(f"{cat} = {cat}\n" if cat in big_ids else f"{cat} = NULL\n")
        gs.run_command("r.reclass", input="basins0", output="big_only", rules=str(rules_big), overwrite=True)

        # nearest big ID for each cell
        gs.run_command("r.grow.distance", input="big_only", value="nearest_big_id", flags="m", overwrite=True)

        # choose ONE big neighbour per small basin by majority vote
        rules_path = out_dir_p / "whole_basin_reclass.txt"
        with open(rules_path, "w", encoding="utf-8") as f:
            for bid in sorted(big_ids):
                f.write(f"{bid} = {bid}\n")

            for sid in small_ids:
                tmp = f"nbid_{sid}"
                gs.mapcalc(f"{tmp} = if(basins0 == {sid}, nearest_big_id, null())", overwrite=True)

                lines = gs.read_command("r.stats", input=tmp, flags="cnN", separator=",").strip().splitlines()
                if not lines:
                    f.write(f"{sid} = {sid}\n")
                    continue

                counts: List[Tuple[int, int]] = []
                for ln in lines:
                    val_s, cnt_s = ln.split(",", 1)
                    val = _parse_stat_value(val_s)
                    if val is None:
                        continue
                    try:
                        cnt = int(cnt_s)
                    except Exception:
                        continue
                    counts.append((val, cnt))

                if not counts:
                    f.write(f"{sid} = {sid}\n")
                    continue

                chosen = max(counts, key=lambda vc: vc[1])[0]
                f.write(f"{sid} = {chosen}\n")

        gs.run_command("r.reclass", input="basins0", output="basins_after_merge", rules=str(rules_path), overwrite=True)

    # --------------------------
    # Fill NULLs inside DEM extent
    # --------------------------
    gs.mapcalc("basins_after_merge = if(isnull(dem), null(), basins_after_merge)", overwrite=True)
    gs.run_command("r.grow.distance", input="basins_after_merge", value="fill_from", flags="m", overwrite=True)
    gs.mapcalc(
        "basins_filled = if(isnull(basins_after_merge) && !isnull(dem), fill_from, basins_after_merge)",
        overwrite=True,
    )

    current = "basins_filled"

    # --------------------------
    # Threshold-free anti-exclave cleanup
    # --------------------------
    if do_exclave_cleanup:
        for it in range(1, max_exclave_iters + 1):
            print(f"\n→ Anti-exclave pass {it}")

            gs.run_command("r.clump", input=current, output="clumps", overwrite=True)

            cstats = gs.read_command("r.stats", input="clumps", flags="cn", separator=",").strip().splitlines()
            if not cstats:
                print("  (no clumps?)")
                break
            clump_sizes = dict(_pairs(cstats))  # clump_id -> n_cells

            # clump -> basin mapping
            gs.mapcalc(f"clump_basin = if(!isnull(clumps), int({current}), null())", overwrite=True)
            cb_lines = gs.read_command("r.stats", input="clumps,clump_basin", flags="cn", separator=",").strip().splitlines()

            basin_to_clumps: Dict[int, List[int]] = {}
            for ln in cb_lines:
                parts = ln.split(",")
                if len(parts) < 3:
                    continue
                try:
                    cid = int(float(parts[0]))
                    bid = int(float(parts[1]))
                except Exception:
                    continue
                basin_to_clumps.setdefault(bid, []).append(cid)

            fragmented = {bid: sorted(set(cids)) for bid, cids in basin_to_clumps.items() if len(set(cids)) > 1}
            print(f"  fragmented basins: {len(fragmented)}")

            if not fragmented:
                print("  ✓ no fragmented basins remain; stopping")
                break

            # loser clumps = all but largest per basin
            drop_clumps: List[int] = []
            for bid, cids in fragmented.items():
                keep = max(cids, key=lambda cid: clump_sizes.get(cid, 0))
                drop_clumps.extend([cid for cid in cids if cid != keep])

            print(f"  clumps to reassign: {len(drop_clumps)}")

            # build dropmask in chunks (avoid giant expression)
            gs.mapcalc("dropmask = null()", overwrite=True)
            chunk = 300
            for k in range(0, len(drop_clumps), chunk):
                part = drop_clumps[k:k + chunk]
                expr = " || ".join([f"clumps == {cid}" for cid in part])
                gs.mapcalc(f"dropmask = if(!isnull(dropmask) || ({expr}), 1, dropmask)", overwrite=True)

            # null losers and refill from nearest neighbor basin
            gs.mapcalc(f"{current}_nulled = if(!isnull(dropmask), null(), {current})", overwrite=True)
            gs.run_command("r.grow.distance", input=f"{current}_nulled", value="refill", flags="m", overwrite=True)
            gs.mapcalc(
                f"{current} = if(!isnull(dem), if(isnull({current}_nulled), refill, {current}_nulled), null())",
                overwrite=True,
            )

    # --------------------------
    # Export
    # --------------------------
    gs.mapcalc(f"basins_merged_final = int({current})", overwrite=True)

    out_path = out_dir_p / out_name
    gs.run_command(
        "r.out.gdal",
        input="basins_merged_final",
        output=str(out_path),
        format="GTiff",
        createopt="COMPRESS=LZW,TILED=YES,BIGTIFF=YES",
        overwrite=True,
    )
    print("✅ Done →", out_path)
    return out_path



