#!/bin/bash
set -eo pipefail

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
: "${MEMBER_LIST:?Set MEMBER_LIST to a file with one member index per line}"

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

MEMBER_INDEX=$(sed -n "${LSB_JOBINDEX}p" "$MEMBER_LIST" | tr -d '[:space:]')
if [ -z "$MEMBER_INDEX" ]; then
    echo "No member at array index $LSB_JOBINDEX in $MEMBER_LIST" >&2
    exit 2
fi

JOB_TMP_ROOT=${JOB_TMP_ROOT:-${LSB_JOB_TMPDIR:-${TMPDIR:-/tmp}}}
if [ -z "$JOB_TMP_ROOT" ] || [ "$JOB_TMP_ROOT" = "/" ]; then
    JOB_TMP_ROOT=/tmp
fi
export TMPDIR=${JOB_TMP_ROOT%/}/ralor_${TARGET_NAME}_${LSB_JOBID}_${LSB_JOBINDEX}_${MEMBER_INDEX}
mkdir -p "$TMPDIR"
export TEMP="$TMPDIR"
export TMP="$TMPDIR"
export GRASS_TMPDIR="$TMPDIR"
trap 'rm -rf "$TMPDIR"' EXIT

export GRASS_GISDBASE="$TMPDIR/grassdata"
mkdir -p "$GRASS_GISDBASE"

echo "TARGET_NAME=$TARGET_NAME"
echo "DEM_MODE=$DEM_MODE"
echo "DEM_RES_M=$DEM_RES_M"
echo "MEMBER_INDEX=$MEMBER_INDEX"
echo "PROJECT_DIR=$PROJECT_DIR"
echo "CODE_DIR=$CODE_DIR"
echo "OUTPUT_DIR=$OUTPUT_DIR"
echo "TMPDIR=$TMPDIR"
echo "GRASS_GISDBASE=$GRASS_GISDBASE"
echo "SKIP_RAW=${SKIP_RAW:-0}"
df -h "$TMPDIR" "$OUTPUT_DIR" || true

TASK_CONFIG="$TMPDIR/config_${TARGET_NAME}_${MEMBER_INDEX}.toml"
python - <<PY
from pathlib import Path
import re

src = Path("$CONFIG_TEMPLATE")
dst = Path("$TASK_CONFIG")
gisdbase = "$GRASS_GISDBASE"
output_dir = "$OUTPUT_DIR"
text = src.read_text()
text = re.sub(r'(?m)^dem_mode\s*=\s*".*"', 'dem_mode = "$DEM_MODE"', text)
text = re.sub(r'(?m)^dem_res_m\s*=\s*\d+', 'dem_res_m = $DEM_RES_M', text)
text = re.sub(r'(?m)^gisdbase\s*=\s*".*"', f'gisdbase = "{gisdbase}"', text)
key = f'{"surface" if "$DEM_MODE" == "surface" else "$DEM_MODE"}_dir_${DEM_RES_M}m'
text = re.sub(
    rf'(?m)^{key}\s*=\s*".*"',
    f'{key} = "{output_dir}/$TARGET_NAME"',
    text,
)
dst.write_text(text)
PY

if [ "${SKIP_RAW:-0}" = "1" ]; then
    echo "=== Skipping raw ensemble member ${MEMBER_INDEX} for ${TARGET_NAME} ==="
else
    echo "=== Generating raw ensemble member ${MEMBER_INDEX} for ${TARGET_NAME} ==="
    "${RUN_ENSEMBLE_CMD[@]}" ensemble "$TASK_CONFIG" "$MEMBER_INDEX"
fi

echo "=== Merging ensemble member ${MEMBER_INDEX} for ${TARGET_NAME} ==="
"${RUN_ENSEMBLE_CMD[@]}" merge "$TASK_CONFIG" "$MEMBER_INDEX"
MEMBER_PAD=$(printf "%03d" "$MEMBER_INDEX")
MERGED_BASIN="$OUTPUT_DIR/$TARGET_NAME/merged_members/basins_hydro_ens_${MEMBER_PAD}.tif"
if [ -s "$MERGED_BASIN" ] && [ "${KEEP_RAW_AFTER_MERGE:-0}" != "1" ]; then
    rm -f "$OUTPUT_DIR/$TARGET_NAME/basins_hydro_ens_${MEMBER_PAD}.tif"
fi
echo "=== Finished member ${MEMBER_INDEX} for ${TARGET_NAME} ==="
