# Ensemble basin workflow

This repository provides a command-line workflow for generating basin ensembles, optionally merging basin members, and creating ensemble summary products such as most-likely basins and stable/uncertain divides.

The code is organized into six files:

- `run_ensemble.py` — thin CLI entry point
- `ensemble_runner.py` — generates ensemble members and resumes incomplete runs
- `ensemble_merge.py` — merges individual basin members
- `ensemble_products.py` — builds final products and applies the selected merge strategy
- `ensemble_postprocess.py` — stack-based ensemble logic and perturbation helpers
- `basin_core.py` - provides the GRASS helpers, hydrology pipeline, and `merge_basins()` implementation.

## Commands

Run the workflow from the terminal:

```bash
python run_ensemble.py ensemble config.toml
python run_ensemble.py merge config.toml
python run_ensemble.py products config.toml
python run_ensemble.py all config.toml
```

### `ensemble`
Runs ensemble members according to the config file.

Outputs per member:

- `basins_hydro_ens_###.tif`
- `streams_hydro_ens_###.tif`
- `flowdir_hydro_ens_###.tif`

It supports:

- `dem_mode = "surface" | "bed" | "hybrid"`
- `dem_res_m = 100 | 500`

### `merge`
Runs the merging algorithm on each ensemble member.

This step:

- fills/classifies unassigned pixels
- merges small basins
- writes merged members into `merge_output_subdir` from the config, usually `merged_members/`

### `products`
Builds ensemble summary products:

- `basins_most_likely.tif`
- `basins_certainty.tif`
- `basins_uncertainty.tif`
- `basin_boundary_probability.tif`
- `basin_stable_divides.tif`
- `basin_uncertain_divides.tif`

How merging is handled depends on `postprocess.merge_strategy` in the config.

### `all`
Runs the full workflow:

1. generate ensemble members
2. if `merge_strategy = "member"`, merge all members
3. build final products
4. if `merge_strategy = "final"`, merge only the final most-likely basin map

## Resume behavior

The `ensemble` stage is resumable.

If you set:

```toml
start_i = 1
n_members = 500
```

and members `001`–`050` already exist as `basins_hydro_ens_###.tif`, rerunning:

```bash
python run_ensemble.py ensemble config.toml
```

will skip those members and continue at `051`.

The check is based on the final basin output, not intermediate cache files.

## Merge strategies

Set this in `[postprocess]`:

```toml
merge_strategy = "none"
merge_strategy = "member"
merge_strategy = "final"
```

### `none`
No merging is applied.

- products are built directly from the raw ensemble member rasters

### `member`
Merging is done for every ensemble member before computing the final products.

Flow:

```text
basins_hydro_ens_###.tif
  -> merge each member
  -> build most-likely basins / certainty / divide products from merged members
```

Use this when you want the ensemble products to reflect the merged topology of each member.

### `final`
Products are built first, then only the final most-likely product is merged once.

Flow:

```text
basins_hydro_ens_###.tif
  -> build most-likely basins / certainty / divide products
  -> merge basins_most_likely.tif once
```

Use this when you want a faster workflow and treat merging as a final cleanup step.

## Configuration

Use `config.example.toml` as a template.

Important sections:

### `[run]`
Controls the ensemble generation.

```toml
[run]
dem_mode = "hybrid"
dem_res_m = 500
n_members = 500
start_i = 1
seed_base = 0
k_min = 0.8
k_max = 1.0
```

Notes:

- `n_members` is the preferred name
- `n_mc` is still accepted for backward compatibility

### `[postprocess]`
Controls merging and final products.

```toml
[postprocess]
merge_strategy = "final"
merge_output_subdir = "merged_members"
merge_min_basin_km2 = 500.0
merge_do_exclaves = true
merge_max_exclave_iters = 6
p_stable_pixel = 0.90
p_min_div = 0.00
```

Key settings:

- `merge_min_basin_km2` — minimum basin size threshold for merging
- `merge_do_exclaves` — enable anti-exclave cleanup
- `merge_max_exclave_iters` — maximum cleanup iterations
- `p_stable_pixel` — threshold for stable divide pixels
- `p_min_div` — lower threshold for uncertain divide pixels

## Suggested repository layout

```text
repo/
  basin_core.py
  run_ensemble.py
  ensemble_runner.py
  ensemble_merge.py
  ensemble_products.py
  ensemble_postprocess.py
  config.example.toml
  README.md
```

## Typical usage

### Full run

```bash
python run_ensemble.py all config.toml
```

### Step-by-step

```bash
python run_ensemble.py ensemble config.toml
python run_ensemble.py merge config.toml
python run_ensemble.py products config.toml
```

### Resume an interrupted ensemble

```bash
python run_ensemble.py ensemble config.toml
```

### Rebuild products only

```bash
python run_ensemble.py products config.toml
```

## Notes

- The CLI uses the term `ensemble` consistently in filenames and commands.
- Internally, some low-level raster names still use legacy `mc` naming to stay compatible with the existing GRASS workflow and `basin_core.py`. This does not affect outputs written to disk.
- The code assumes a working GRASS/QGIS installation and the paths defined in the config file.
