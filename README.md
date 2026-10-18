# 4DGreenland Basins

Hydrological basin delineation workflow for the Greenland Ice Sheet.

The project builds ensemble drainage basins for three routing modes at two grid
resolutions:

- surface routing at 100 m and 500 m
- bed/subglacial routing at 100 m and 500 m
- hybrid surface-to-bed routing at 100 m and 500 m

For each target, the workflow creates an ensemble of basin members, merges small
basins in each member, and calculates basin membership probabilities. The
delivered basin geometry is reviewed separately and remains fixed when its
probabilities are calculated.

The repository is designed as a general Python/GRASS GIS workflow. It can be
run on a laptop, workstation, server, container, or scheduler-managed cluster as
long as the required geospatial dependencies and input rasters are available.

## Repository Contents

Core workflow:

- `run_ensemble.py` - command-line entry point for one target
- `run_all_targets.py` - convenience runner for the six standard targets
- `ensemble_runner.py` - creates ensemble members and supports resumable runs
- `ensemble_merge.py` - merges and cleans individual ensemble basin members
- `reference.py` - validates the merged Version 0 reference and member rasters
- `ensemble_products.py` - creates Version 0-based intermediate products
- `final_delivery.py` - calculates products for an approved final basin map
- `outlet_analysis.py` - analyses basin-area variation at a selected outlet
- `basin_core.py` - GRASS GIS hydrology and basin-merging utilities

Configuration:

- `config.example.toml` - portable template; copy to `config.toml` and edit paths
- `pyproject.toml` - Python dependencies, package metadata, and test settings

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

Each target contains raw ensemble rasters. The member-merged rasters live under:

```text
<target>/merged_members/
```

The approved final delivery is kept in a separate folder, for example
`Ensemble_products/100m/surf/`.

## Final Products

The `products` stage writes intermediate, Version 0-based results. Once a final
basin raster and vector have been approved, `final-delivery` uses all merged
members (500 for the completed products) to write the delivered files. For a
target named `surf_100m`, these are:

- `basins_surf_100m.tif` and `.gpkg` - the approved raster and vector basin IDs
- `basin_certainty_surf_100m.tif` - fraction of members supporting each pixel's assigned final basin ID
- `boundary_probability_surf_100m.tif` - fraction of members with a basin divide at each pixel
- `basin_membership_probabilities_surf_100m.tif` - one probability band per final basin ID
- `Individual_basin_probabilities/*.tif` - single-band copies of those layers

Each merged member basin is assigned as a whole to the final basin with which it
has the largest area overlap. Final-basin certainty can therefore be lower than
another basin's probability at a pixel. No uncertainty-complement raster or
stable/uncertain divide masks are included in the final delivery. The multiband
GeoTIFF and GeoPackage can be opened in QGIS.

Use the final geometry for the selected target; the destination must not exist:

```bash
python run_ensemble.py final-delivery config.toml \
  /path/to/approved/basins_surf_100m.tif \
  /path/to/approved/basins_surf_100m.gpkg \
  /path/to/delivery/Ensemble_products/100m/surf

python run_ensemble.py extract-individual \
  /path/to/delivery/Ensemble_products/100m/surf/basin_membership_probabilities_surf_100m.tif \
  /path/to/delivery/Ensemble_products/100m/surf/Individual_basin_probabilities
```

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
python -m pip install -e .
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

Or run those three stages for the selected target:

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
python run_ensemble.py ensemble config.toml 42
python run_ensemble.py merge config.toml 42
```

Run a member range:

```bash
python run_ensemble.py ensemble config.toml 1 50
python run_ensemble.py merge config.toml 1 50
```

## Resume Behavior

The ensemble stage skips members whose final basin raster already exists:

```text
basins_hydro_ens_###.tif
```

The merge stage also skips existing merged members. This makes failed or
interrupted runs safe to resubmit.

## Dependencies

The workflow expects:

- Python 3.10+
- NumPy
- Rasterio
- GeoPandas and Shapely
- Matplotlib (for outlet analysis)
- GRASS GIS 8
- GDAL
- GRASS addon `r.stream.extract`

The Python dependency versions are specified in `pyproject.toml`. Run the
synthetic regression tests with `python -m pip install -e ".[dev]"` followed by
`python -m pytest`.

## Scheduler Examples

The repository does not require a specific HPC system. The `scripts/hpc/` folder
contains optional LSF examples for running large 500-member ensembles on a
cluster. Adapt paths, environment activation, queues, memory, walltime, and
concurrency limits to your own system before submitting jobs.
