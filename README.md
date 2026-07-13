# 4DGreenland Basins

Hydrological basin delineation workflow for the Greenland Ice Sheet.

The project builds ensemble drainage basins for three routing modes at two grid
resolutions:

- surface routing at 100 m and 500 m
- bed/subglacial routing at 100 m and 500 m
- hybrid surface-to-bed routing at 100 m and 500 m

For each target, the workflow runs an ensemble of 500 members, merges each
member with a basin-cleanup algorithm, and then derives final ensemble products
such as most-likely basins, basin certainty, boundary certainty, and stable
divide masks.

## Repository Contents

Core Python modules:

- `run_ensemble.py` - command-line entry point
- `ensemble_runner.py` - creates ensemble members and supports resumable runs
- `ensemble_merge.py` - merges and cleans individual ensemble basin members
- `ensemble_products.py` - orchestrates final product generation
- `ensemble_postprocess.py` - builds ensemble products from member rasters
- `basin_core.py` - GRASS GIS hydrology and basin-merging utilities

HPC/LSF scripts:

- `run_members_from_list.sh` - runs raw and merged basin members from an LSF array
- `products_script.sh` - builds final products for one target
- `submit_all_targets.sh` - submits all six target workflows

Configuration:

- `config.example.toml` - public template; copy to `config.toml` and edit paths
- `requirements.txt` - Python package requirements outside GRASS/GDAL

Large input datasets, GRASS databases, logs, and output rasters are intentionally
not tracked by git.

## Targets

The standard output layout is:

```text
/work3/ralor/output/
  surf_100m/
  bed_100m/
  hybrid_100m/
  surf_500m/
  bed_500m/
  hybrid_500m/
```

Each target contains raw ensemble rasters while the member-merged rasters and
final products live under:

```text
<target>/merged_members/
```

## Final Products

The `products` stage writes these rasters from the merged ensemble members:

- `basins_most_likely.tif` - consensus basin labels
- `basins_certainty.tif` - fraction of members supporting each most-likely label
- `basins_uncertainty.tif` - `1 - basins_certainty`
- `basin_boundary_probability.tif` - fraction of members with a basin boundary
- `basin_boundary_certainty.tif` - alias of boundary probability for downstream naming
- `basin_stable_divides.tif` - boundary pixels above the stable threshold
- `basin_uncertain_divides.tif` - boundary pixels below the stable threshold

The default product settings use merged members as input:

```toml
[postprocess]
merge_strategy = "member"
merge_output_subdir = "merged_members"
p_stable_pixel = 0.75
p_min_div = 0.00
```

## Quick Start on DTU HPC

Create your local runtime config:

```bash
cp config.example.toml config.toml
```

Edit `config.toml` so `[inputs]`, `[outputs]`, and `[grass]` match the machine
where you run the workflow.

Submit all six target workflows:

```bash
bash submit_all_targets.sh
```

Useful environment overrides:

```bash
TOTAL_MEMBERS=500 MAX_CONCURRENT_MEMBERS=30 bash submit_all_targets.sh
OUTPUT_DIR=/work3/ralor/output bash submit_all_targets.sh
CONFIG_TEMPLATE=config.toml bash submit_all_targets.sh
```

The submit script launches one LSF array per target. Each array element runs one
member, merges that member, deletes raw intermediates unless configured
otherwise, and then a dependent products job builds the final products.

## Manual Commands

Run one full target locally or inside an interactive HPC session:

```bash
python run_ensemble.py ensemble config.toml
python run_ensemble.py merge config.toml
python run_ensemble.py products config.toml
```

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

On DTU HPC, the job scripts assume a conda environment named `grisbins`. Adjust
the activation lines if your environment has another name.
