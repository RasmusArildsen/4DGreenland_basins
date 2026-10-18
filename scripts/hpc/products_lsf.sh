#!/bin/bash
# Build Version_0-based intermediate products for one target.
#
# Required environment:
#   TARGET_NAME=surf_100m | bed_100m | hybrid_100m | surf_500m | bed_500m | hybrid_500m
#   DEM_MODE=surface | bed | hybrid
#   DEM_RES_M=100 | 500

set -euo pipefail

if [ -n "${CONDA_ENV:-}" ]; then
    source "${CONDA_SH:-$HOME/miniconda3/etc/profile.d/conda.sh}"
    conda activate "$CONDA_ENV"
fi

PROJECT_DIR=${PROJECT_DIR:-${LSB_SUBCWD:-$(pwd)}}
CODE_DIR=${CODE_DIR:-$PROJECT_DIR}
CONFIG_TEMPLATE=${CONFIG_TEMPLATE:-config.toml}
OUTPUT_DIR=${OUTPUT_DIR:-outputs}

: "${TARGET_NAME:?Set TARGET_NAME, e.g. surf_100m}"
: "${DEM_MODE:?Set DEM_MODE: surface | bed | hybrid}"
: "${DEM_RES_M:?Set DEM_RES_M: 100 | 500}"

cd "$PROJECT_DIR"
mkdir -p logs "$OUTPUT_DIR"

if [ ! -f "$CODE_DIR/run_ensemble.py" ] || [ ! -f "$CODE_DIR/ensemble_runner.py" ]; then
    echo "Cannot find the ensemble Python code." >&2
    echo "PROJECT_DIR is: $PROJECT_DIR" >&2
    echo "CODE_DIR is: $CODE_DIR" >&2
    exit 1
fi
export PYTHONPATH="$CODE_DIR:${PYTHONPATH:-}"
RUN_ENSEMBLE_CMD=(python -u "$CODE_DIR/run_ensemble.py")

JOB_TMP_ROOT=${JOB_TMP_ROOT:-${LSB_JOB_TMPDIR:-${TMPDIR:-/tmp}}}
if [ -z "$JOB_TMP_ROOT" ] || [ "$JOB_TMP_ROOT" = "/" ]; then
    JOB_TMP_ROOT=/tmp
fi

JOB_ID=${LSB_JOBID:-local}
export TMPDIR=${JOB_TMP_ROOT%/}/ralor_products_${TARGET_NAME}_${JOB_ID}
mkdir -p "$TMPDIR"
export TEMP="$TMPDIR"
export TMP="$TMPDIR"
trap 'rm -rf "$TMPDIR"' EXIT

echo "TARGET_NAME=$TARGET_NAME"
echo "DEM_MODE=$DEM_MODE"
echo "DEM_RES_M=$DEM_RES_M"
echo "PROJECT_DIR=$PROJECT_DIR"
echo "CODE_DIR=$CODE_DIR"
echo "OUTPUT_DIR=$OUTPUT_DIR"
echo "TMPDIR=$TMPDIR"

TASK_CONFIG="$TMPDIR/config_${TARGET_NAME}_products.toml"
python - <<PY
from pathlib import Path
import re

src = Path("$CONFIG_TEMPLATE")
dst = Path("$TASK_CONFIG")
output_dir = "$OUTPUT_DIR"
text = src.read_text()
text = re.sub(r'(?m)^dem_mode\s*=\s*".*"', 'dem_mode = "$DEM_MODE"', text)
text = re.sub(r'(?m)^dem_res_m\s*=\s*\d+', 'dem_res_m = $DEM_RES_M', text)
key = f'{"surface" if "$DEM_MODE" == "surface" else "$DEM_MODE"}_dir_${DEM_RES_M}m'
text = re.sub(
    rf'(?m)^{key}\s*=\s*".*"',
    f'{key} = "{output_dir}/$TARGET_NAME"',
    text,
)
dst.write_text(text)
PY

"${RUN_ENSEMBLE_CMD[@]}" products "$TASK_CONFIG"
