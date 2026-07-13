from pathlib import Path
import basin_core as bc
from ensemble_runner import load_config, load_runtime

cfg_path = "config.toml"
rt = load_runtime(load_config(cfg_path))
post_cfg = rt["cfg"].get("postprocess", {})

merged_dir = rt["OUT"] / str(post_cfg.get("merge_output_subdir", "merged_members"))
infile = merged_dir / "basins_most_likely.tif"
outfile = "basins_most_likely_merged.tif"

bc.setup_grass_env()
bc.start_grass_from_raster(
    str(rt["DEM"]),
    location=str(rt["grass_cfg"].get("location", "dem_loc")),
    mapset=f"{rt['grass_cfg'].get('mapset_prefix', 'MC_WORK')}_{rt['res']}m_finalmerge",
    gisdbase=rt["grass_cfg"].get("gisdbase"),
)

bc.merge_basins(
    basins_input=str(infile),
    dem_path=str(rt["DEM"]),
    out_dir=str(merged_dir),
    out_name=outfile,
    min_basin_size_km2=float(post_cfg.get("merge_min_basin_km2", 500.0)),
    res_m=int(rt["res"]),
    do_exclave_cleanup=bool(post_cfg.get("merge_do_exclaves", True)),
    max_exclave_iters=int(post_cfg.get("merge_max_exclave_iters", 6)),
)

print(f"Done: {merged_dir / outfile}")
