#!/bin/bash
# Submit a SLURM job to parse documents in parallel.
#
# GPU parsers (marker, docling, nougat) run on 4 GPU workers.
# CPU parsers (unstructured) run on N CPU workers with a container per worker.
# Cloud parsers (azure) run on a single CPU node with no container setup.
#
# Usage: ./scripts/nersc_submit_parse_job.sh [--parser PARSER] [--source SOURCE_ID]
#                                             [--time HH:MM:SS] [--batch-size N]
#                                             [--workers N] [--no-images] [--no-describe]
#
# Options:
#   --parser PARSER       Parser: marker (default), docling, nougat, unstructured, azure
#   --source SOURCE_ID    Source ID to process (default: sld-scanned)
#   --time HH:MM:SS       Wall time (default: 02:00:00)
#   --batch-size N        Documents per batch per worker (default: 10)
#   --workers N           Number of parallel workers for CPU parsers (default: 4)
#   --no-images           Disable image extraction
#   --no-describe         Disable LLM image descriptions
#   --dependency

PARSER="marker"
SOURCE_ID="sld-scanned"
WALL_TIME="02:00:00"
BATCH_SIZE="10"
NUM_WORKERS="4"
EXTRACT_IMAGES="--extract-images"
DESCRIBE_IMAGES="--describe-images"
DEPENDENCY_ID=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --parser)      PARSER="$2";       shift 2 ;;
        --source)      SOURCE_ID="$2";    shift 2 ;;
        --time)        WALL_TIME="$2";    shift 2 ;;
        --batch-size)  BATCH_SIZE="$2";   shift 2 ;;
        --workers)     NUM_WORKERS="$2";  shift 2 ;;
        --dependency)  DEPENDENCY_ID="$2";  shift 2 ;;
        --no-images)   EXTRACT_IMAGES=""; shift ;;
        --no-describe) DESCRIBE_IMAGES=""; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

case "$PARSER" in
    marker|docling|nougat|unstructured|azure) ;;
    *) echo "Unknown parser: $PARSER (choose: marker, docling, nougat, unstructured, azure)"; exit 1 ;;
esac

JOB_NAME="${PARSER}_parse_${SOURCE_ID}"

SLURM_ARGS=()
if [[ -n "$DEPENDENCY_ID" ]]; then
    SLURM_ARGS+=(--dependency=afterany:"$DEPENDENCY_ID")
fi


# GPU parsers use the GPU queue and run_on_4gpus.sh
# CPU parsers use the CPU queue, start containers, and use run_on_n_workers.sh
# Cloud parsers use a single CPU node with no container setup
if [[ "$PARSER" == "azure" ]]; then
    sbatch "${SLURM_ARGS[@]}" <<EOF
#!/bin/bash
#SBATCH --nodes=1
#SBATCH --qos=regular
#SBATCH --time=${WALL_TIME}
#SBATCH --constraint=cpu
#SBATCH --account=m5115
#SBATCH --job-name=${JOB_NAME}
#SBATCH --output=${JOB_NAME}_%j.out

cd \$SLURM_SUBMIT_DIR

source /pscratch/sd/s/scorrodi/kb-mcp/scripts/nersc_setup.sh

kb tools parse-all ${SOURCE_ID} \\
    ${EXTRACT_IMAGES} \\
    ${DESCRIBE_IMAGES} \\
    --parser-name ${PARSER} \\
    --batch-size ${BATCH_SIZE}
EOF
elif [[ "$PARSER" == "unstructured" ]]; then
    sbatch "${SLURM_ARGS[@]}" <<EOF
#!/bin/bash
#SBATCH --nodes=1
#SBATCH --qos=regular
#SBATCH --time=${WALL_TIME}
#SBATCH --constraint=cpu
#SBATCH --account=m5115
#SBATCH --job-name=${JOB_NAME}
#SBATCH --output=${JOB_NAME}_%j.out

cd \$SLURM_SUBMIT_DIR

source /pscratch/sd/s/scorrodi/kb-mcp/scripts/nersc_setup.sh
source scripts/nersc_setup_unstructured.sh
source scripts/nersc_launch_unstructured_api.sh ${NUM_WORKERS}

./scripts/run_on_n_workers.sh ${NUM_WORKERS} kb tools parse-all ${SOURCE_ID} \\
    ${EXTRACT_IMAGES} \\
    ${DESCRIBE_IMAGES} \\
    --parser-name ${PARSER} \\
    --batch-size ${BATCH_SIZE}
EOF
else
    sbatch "${SLURM_ARGS[@]}" <<EOF
#!/bin/bash
#SBATCH --nodes=1
#SBATCH --qos=regular
#SBATCH --time=${WALL_TIME}
#SBATCH --constraint=gpu
#SBATCH --account=m5115_g
#SBATCH --job-name=${JOB_NAME}
#SBATCH --output=${JOB_NAME}_%j.out

cd \$SLURM_SUBMIT_DIR


source /pscratch/sd/s/scorrodi/kb-mcp/scripts/nersc_setup.sh
case "${PARSER}" in
    marker)  source scripts/nersc_setup_marker.sh ;;
    docling) source scripts/nersc_setup_docling.sh ;;
    nougat)  source scripts/nersc_setup_nougat.sh ;;
esac

./scripts/run_on_4gpus.sh kb tools parse-all ${SOURCE_ID} \\
    ${EXTRACT_IMAGES} \\
    ${DESCRIBE_IMAGES} \\
    --parser-name ${PARSER} \\
    --batch-size ${BATCH_SIZE}
EOF
fi

echo "Submitted: ${JOB_NAME} (parser=${PARSER}, source=${SOURCE_ID}, time=${WALL_TIME})"
