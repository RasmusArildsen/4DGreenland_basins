# 4DGreenland Basins

Hydrological basin delineation workflow for the Greenland Ice Sheet.

The project builds ensemble drainage basins for three routing modes at two grid
resolutions:

- surface routing at 100 m and 500 m
- bed/subglacial routing at 100 m and 500 m
- hybrid surface-to-bed routing at 100 m and 500 m

For each target, the workflow can run an ensemble of basin members, merge each
member with a basin-cleanup algorithm, and derive final ensemble products such
as most-likely basins, basin certainty, boundary certainty, and stable divide
masks.

The repository is designed as a general Python/GRASS GIS workflow. It can be
run on a laptop, workstation, server, container, or scheduler-managed cluster as
long as the required geospatial dependencies and input rasters are available.

## Repository Contents

Core workflow:

- `run_ensemble.py` - command-line entry point for one target
- `run_all_targets.py` - convenience runner for the six standard targets
- `ensemble_runner.py` - creates ensemble members and supports resumable runs
- `ensemble_merge.py` - merges and cleans individual ensemble basin members
- `ensemble_products.py` - orchestrates final product generation
- `ensemble_postprocess.py` - builds ensemble products from member rasters
- `basin_core.py` - GRASS GIS hydrology and basin-merging utilities

Configuration:

- `config.example.toml` - portable template; copy to `config.toml` and edit paths
- `requirements.txt` - Python package requirements outside GRASS/GDAL

Optional scheduler examples:

- `scripts/hpc/` - LSF submission helpers for cluster deployments

Large input datasets, GRASS databases, logs, and output rasters are intentionally
not tracked by git.

## Targets

The standard target names are:

```text
surf_100m
bed_100m
hybrid_100m
surf_500m
bed_500m
hybrid_500m
```

The default output layout is relative to the repository:

```text
outputs/
  surf_100m/
  bed_100m/
  hybrid_100m/
  surf_500m/
  bed_500m/
  hybrid_500m/
```

Each target contains raw ensemble rasters. Member-merged rasters and final
products live under:

```text
<target>/merged_members/
```

## Final Products

The `products` stage writes these rasters from the ensemble members, or from
the merged members when `postprocess.merge_strategy = "member"`:

- `basins_most_likely.tif` - consensus basin labels
- `basins_certainty.tif` - fraction of members supporting each most-likely label
- `basins_uncertainty.tif` - `1 - basins_certainty`
- `basin_boundary_probability.tif` - fraction of members with a basin boundary
- `basin_boundary_certainty.tif` - alias of boundary probability for downstream naming
- `basin_stable_divides.tif` - boundary pixels above the stable threshold
- `basin_uncertain_divides.tif` - boundary pixels below the stable threshold

## Quick Start

Create a runtime config:

```bash
cp config.example.toml config.toml
```

Edit `config.toml` so the `[inputs]`, `[outputs]`, and `[grass]` sections match
your machine. The example config uses relative paths such as `data/input/...`,
`outputs/...`, and `grassdata`.

Install Python dependencies into your environment:

```bash
python -m pip install -r requirements.txt
```

GRASS GIS, GDAL, and the GRASS addon `r.stream.extract` must also be available
in the runtime environment.

## Run One Target

Choose one routing mode and resolution in `[run]`:

```toml
[run]
dem_mode = "hybrid"
dem_res_m = 500
n_members = 500
```

Run the stages:

```bash
python run_ensemble.py ensemble config.toml
python run_ensemble.py merge config.toml
python run_ensemble.py products config.toml
```

Or run the full workflow for the selected target:

```bash
python run_ensemble.py all config.toml
```

## Run All Six Targets

`run_all_targets.py` creates temporary per-target configs from `config.toml` and
runs each selected target:

```bash
python run_all_targets.py --config config.toml --stage all
```

Run only the products stage for selected targets:

```bash
python run_all_targets.py --config config.toml --stage products --targets surf_100m bed_100m
```

For a short test run:

```bash
python run_all_targets.py --config config.toml --stage all --members 5
```

To preview commands without running them:

```bash
python run_all_targets.py --config config.toml --dry-run
```

## Manual Member Commands

Run one member:

```bash
python run_ensemble.py ensemble-single config.toml 42
python run_ensemble.py single config.toml 42
```

Run a member range:

```bash
python run_ensemble.py ensemble-range config.toml 1 50
python run_ensemble.py range config.toml 1 50
```

## Resume Behavior

The ensemble stage skips members whose final basin raster already exists:

```text
basins_hydro_ens_###.tif
```

The merge stage skips merged members unless `FORCE_MERGE=1` is set. This makes
failed or interrupted runs safe to resubmit.

## Dependencies

The workflow expects:

- Python 3.10+
- NumPy
- Rasterio
- GRASS GIS 8
- GDAL
- GRASS addon `r.stream.extract`

Optional analysis utilities use Pandas, GeoPandas, Shapely, and Matplotlib.

## Scheduler Examples

The repository does not require a specific HPC system. The `scripts/hpc/` folder
contains optional LSF examples for running large 500-member ensembles on a
cluster. Adapt paths, environment activation, queues, memory, walltime, and
concurrency limits to your own system before submitting jobs.
