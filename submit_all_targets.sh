#!/bin/bash
# Submit the full 4DGreenland basin production workflow to LSF.
#
# The script launches one member array for each target and then submits a
# dependent products job for that target.

set -euo pipefail

PROJECT_DIR=${PROJECT_DIR:-$(pwd)}
CODE_DIR=${CODE_DIR:-$PROJECT_DIR}
CONFIG_TEMPLATE=${CONFIG_TEMPLATE:-config.toml}
OUTPUT_DIR=${OUTPUT_DIR:-/work3/ralor/output}
TOTAL_MEMBERS=${TOTAL_MEMBERS:-500}
MAX_CONCURRENT_MEMBERS=${MAX_CONCURRENT_MEMBERS:-30}
MEMBER_WALLTIME=${MEMBER_WALLTIME:-48:00}
PRODUCT_WALLTIME=${PRODUCT_WALLTIME:-48:00}
MEMBER_MEM_MB=${MEMBER_MEM_MB:-8000}
PRODUCT_MEM_MB=${PRODUCT_MEM_MB:-32000}

cd "$PROJECT_DIR"
mkdir -p logs runs "$OUTPUT_DIR"

member_list_for_target() {
    local target=$1
    local member_list="runs/${target}_members_1_${TOTAL_MEMBERS}.txt"
    seq 1 "$TOTAL_MEMBERS" > "$member_list"
    printf '%s\n' "$member_list"
}

submit_target() {
    local target=$1
    local mode=$2
    local res=$3
    local member_list
    member_list=$(member_list_for_target "$target")

    local member_job_name="${target}_members"
    local products_job_name="${target}_products"

    echo "Submitting $target ($mode, ${res} m)"
    local submission
    submission=$(
        env \
            PROJECT_DIR="$PROJECT_DIR" \
            CODE_DIR="$CODE_DIR" \
            CONFIG_TEMPLATE="$CONFIG_TEMPLATE" \
            OUTPUT_DIR="$OUTPUT_DIR" \
            TARGET_NAME="$target" \
            DEM_MODE="$mode" \
            DEM_RES_M="$res" \
            MEMBER_LIST="$member_list" \
            TOTAL_MEMBERS="$TOTAL_MEMBERS" \
            bsub \
                -J "${member_job_name}[1-${TOTAL_MEMBERS}]%${MAX_CONCURRENT_MEMBERS}" \
                -oo "logs/${target}_members_%J_%I.out" \
                -eo "logs/${target}_members_%J_%I.err" \
                -W "$MEMBER_WALLTIME" \
                -n 1 \
                -R "rusage[mem=${MEMBER_MEM_MB}]" \
                < run_members_from_list.sh
    )
    echo "$submission"

    local member_job_id
    member_job_id=$(printf '%s\n' "$submission" | sed -n 's/.*Job <\([0-9][0-9]*\)>.*/\1/p')
    if [ -z "$member_job_id" ]; then
        echo "Could not parse LSF job id for $target" >&2
        exit 1
    fi

    env \
        PROJECT_DIR="$PROJECT_DIR" \
        CODE_DIR="$CODE_DIR" \
        CONFIG_TEMPLATE="$CONFIG_TEMPLATE" \
        OUTPUT_DIR="$OUTPUT_DIR" \
        TARGET_NAME="$target" \
        DEM_MODE="$mode" \
        DEM_RES_M="$res" \
        bsub \
            -J "$products_job_name" \
            -w "done(${member_job_id})" \
            -oo "logs/${target}_products_%J.out" \
            -eo "logs/${target}_products_%J.err" \
            -W "$PRODUCT_WALLTIME" \
            -n 1 \
            -R "rusage[mem=${PRODUCT_MEM_MB}]" \
            < products_script.sh
}

submit_target surf_100m surface 100
submit_target bed_100m bed 100
submit_target hybrid_100m hybrid 100
submit_target surf_500m surface 500
submit_target bed_500m bed 500
submit_target hybrid_500m hybrid 500
