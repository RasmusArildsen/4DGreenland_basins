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
QGIS_PREFIX_DEFAULT = "/Applications/GRASS-8.4.app/Contents/Resources"

# Filled after setup_grass_env()
gs = None
gsetup = None
find_program = None
CalledModuleError = RuntimeError


# -----------------------------------------------------------------------------
# GRASS bootstrap helpers
# -----------------------------------------------------------------------------
def _grass_subprocess_env() -> dict:
    """Subprocess env that forces GRASS PROJ/GDAL data and removes conda leakage."""
    env = os.environ.copy()
    gisbase = Path(env["GISBASE"])

    # remove conda paths
    env.pop("PROJ_LIB", None)
    env.pop("PROJ_DATA", None)
    env.pop("GDAL_DATA", None)
    env.pop("GDAL_DRIVER_PATH", None)

    # point to GRASS data dirs
    env["PROJ_LIB"] = str(gisbase / "share" / "proj")
    env["PROJ_DATA"] = str(gisbase / "share" / "proj")
    env["GDAL_DATA"] = str(gisbase / "share" / "gdal")
    env.setdefault("GTIFF_SRS_SOURCE", "EPSG")
    return env


def _grass_bin() -> str:
    grass_bin = (Path(os.environ["GISBASE"]) / "bin" / "grass").resolve()
    if not grass_bin.exists():
        raise RuntimeError(f"GRASS executable not found: {grass_bin}")
    return str(grass_bin)


def setup_grass_env(grass_gisbase: str = QGIS_PREFIX_DEFAULT):
    """
    macOS: point Python to the GRASS.app installation.
    """
    import platform

    if platform.system() != "Darwin":
        raise RuntimeError("setup_grass_env() here is macOS-only. Use your Windows version on Windows.")

    GISBASE = Path(grass_gisbase).resolve()
    grass_python = GISBASE / "etc" / "python"
    init_py = grass_python / "grass" / "__init__.py"
    if not init_py.exists():
        raise RuntimeError(f"GRASS python not found at: {init_py}")

    # Core env
    os.environ["GISBASE"] = str(GISBASE)
    os.environ["PATH"] = os.pathsep.join(
        [
            str(GISBASE / "bin"),
            str(GISBASE / "scripts"),
            os.environ.get("PATH", ""),
        ]
    )

    # Make grass.script importable
    if str(grass_python) not in sys.path:
        sys.path.insert(0, str(grass_python))

    # Make grass python visible to subprocess scripts
    os.environ["PYTHONPATH"] = str(grass_python) + os.pathsep + os.environ.get("PYTHONPATH", "")

    # Locale (avoids warnings)
    os.environ.setdefault("LANG", "en_US.UTF-8")
    os.environ.setdefault("LC_ALL", "en_US.UTF-8")

    # Avoid conda GDAL plugin interference
    os.environ.pop("GDAL_DRIVER_PATH", None)

    # Force GRASS PROJ/GDAL data (avoid conda proj.db)
    os.environ.pop("PROJ_LIB", None)
    os.environ.pop("PROJ_DATA", None)
    os.environ.pop("GDAL_DATA", None)
    os.environ["PROJ_LIB"] = str(GISBASE / "share" / "proj")
    os.environ["PROJ_DATA"] = str(GISBASE / "share" / "proj")
    os.environ["GDAL_DATA"] = str(GISBASE / "share" / "gdal")
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


def ensure_location(dem_path: str, gisdbase: Path, location: str) -> Path:
    """
    Ensure a valid GRASS Location exists (PERMANENT/DEFAULT_WIND must exist).
    Recreate if broken.
    """
    loc_path = gisdbase / location
    default_wind = loc_path / "PERMANENT" / "DEFAULT_WIND"
    if default_wind.exists():
        return loc_path

    if loc_path.exists():
        shutil.rmtree(loc_path)

    raster = str(Path(dem_path).resolve()).replace("\\", "/")
    cmd = [_grass_bin(), "--text", "-c", raster, "-e", str(loc_path)]
    print("→", " ".join(cmd))
    subprocess.run(cmd, check=True, env=_grass_subprocess_env())
    return loc_path


def ensure_mapset(gisdbase: Path, location: str, mapset: str):
    """Ensure a mapset exists inside location (creates it via g.mapset -c)."""
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
    subprocess.run(cmd, check=True, env=_grass_subprocess_env())


def start_grass_from_raster(raster_path: str, *, location="dem_loc", mapset="MC_WORK"):
    """
    Ensure GRASS location+mapset exist (creating/repairing if needed),
    then init a GRASS Python session.
    """
    gisdbase = Path.home() / "Documents" / "grassdata"
    gisdbase.mkdir(parents=True, exist_ok=True)

    ensure_location(raster_path, gisdbase, location)
    ensure_mapset(gisdbase, location, mapset)

    GISBASE = os.environ["GISBASE"]
    gsetup.init(str(gisdbase), location=location, mapset=mapset, grass_path=GISBASE)

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
    """Install addon into ~/.grass8/addons on macOS (or APPDATA/GRASS8/addons on Windows)."""
    import platform

    if platform.system() == "Darwin":
        addon_base = Path.home() / ".grass8" / "addons"
    else:
        APPDATA = os.environ.get("APPDATA", str(Path.home()))
        addon_base = Path(APPDATA) / "GRASS8" / "addons"

    addon_base.mkdir(parents=True, exist_ok=True)
    os.environ["GRASS_ADDON_BASE"] = str(addon_base)

    addon_bin = addon_base / "bin"
    addon_scripts = addon_base / "scripts"

    os.environ["PATH"] = os.pathsep.join([str(addon_bin), str(addon_scripts), os.environ.get("PATH", "")])
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
def merge_basins(
    *,
    gs=None,
    basins_input: str,
    dem_path: str,
    out_dir: str,
    min_basin_size_km2: float = 500.0,
    res_m: int = 500,
    do_exclave_cleanup: bool = True,
    max_exclave_iters: int = 6,
    out_name: str = "basins_merged_no_small_fullcover.tif",
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

    # Import + region
    gs.run_command("r.in.gdal", input=dem_path.replace("\\", "/"), output="dem", overwrite=True)
    gs.run_command("r.in.gdal", input=basins_input.replace("\\", "/"), output="basins_in", overwrite=True)
    gs.run_command("g.region", raster="dem", res=res_m, align="dem")

    # int labels + mask to DEM extent
    gs.mapcalc("basins0 = int(basins_in)", overwrite=True)
    gs.mapcalc("basins0 = if(isnull(dem), null(), basins0)", overwrite=True)

    # Determine big vs small
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

    # Merge small -> big (whole-basin reassignment)
    if not small_ids:
        gs.mapcalc("basins_after_merge = basins0", overwrite=True)
    else:
        rules_big = out_dir_p / "big_reclass.txt"
        with open(rules_big, "w", encoding="utf-8") as f:
            for cat in sizes:
                f.write(f"{cat} = {cat}\n" if cat in big_ids else f"{cat} = NULL\n")
        gs.run_command("r.reclass", input="basins0", output="big_only", rules=str(rules_big), overwrite=True)

        gs.run_command("r.grow.distance", input="big_only", value="nearest_big_id", flags="m", overwrite=True)

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

    # Fill NULLs inside DEM extent
    gs.mapcalc("basins_after_merge = if(isnull(dem), null(), basins_after_merge)", overwrite=True)
    gs.run_command("r.grow.distance", input="basins_after_merge", value="fill_from", flags="m", overwrite=True)
    gs.mapcalc(
        "basins_filled = if(isnull(basins_after_merge) && !isnull(dem), fill_from, basins_after_merge)",
        overwrite=True,
    )
    current = "basins_filled"

    # Anti-exclave cleanup
    if do_exclave_cleanup:
        for it in range(1, max_exclave_iters + 1):
            print(f"\n→ Anti-exclave pass {it}")

            gs.run_command("r.clump", input=current, output="clumps", overwrite=True)

            cstats = gs.read_command("r.stats", input="clumps", flags="cn", separator=",").strip().splitlines()
            if not cstats:
                print("  (no clumps?)")
                break
            clump_sizes = dict(_pairs(cstats))

            gs.mapcalc(f"clump_basin = if(!isnull(clumps), int({current}), null())", overwrite=True)
            cb_lines = gs.read_command(
                "r.stats", input="clumps,clump_basin", flags="cn", separator=","
            ).strip().splitlines()

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

            drop_clumps: List[int] = []
            for bid, cids in fragmented.items():
                keep = max(cids, key=lambda cid: clump_sizes.get(cid, 0))
                drop_clumps.extend([cid for cid in cids if cid != keep])

            print(f"  clumps to reassign: {len(drop_clumps)}")

            gs.mapcalc("dropmask = null()", overwrite=True)
            chunk = 300
            for k in range(0, len(drop_clumps), chunk):
                part = drop_clumps[k : k + chunk]
                expr = " || ".join([f"clumps == {cid}" for cid in part])
                gs.mapcalc(f"dropmask = if(!isnull(dropmask) || ({expr}), 1, dropmask)", overwrite=True)

            gs.mapcalc(f"{current}_nulled = if(!isnull(dropmask), null(), {current})", overwrite=True)
            gs.run_command("r.grow.distance", input=f"{current}_nulled", value="refill", flags="m", overwrite=True)
            gs.mapcalc(
                f"{current} = if(!isnull(dem), if(isnull({current}_nulled), refill, {current}_nulled), null())",
                overwrite=True,
            )

    # Export
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