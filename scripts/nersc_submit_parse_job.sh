#!/bin/bash
# Submit a SLURM job to parse documents on 4 GPUs.
#
# Usage: ./scripts/submit_parse_job.sh [--parser PARSER] [--source SOURCE_ID]
#                                       [--time HH:MM:SS] [--batch-size N]
#                                       [--no-images] [--no-describe]
#
# Options:
#   --parser PARSER       Parser to use: marker (default), docling, nougat
#   --source SOURCE_ID    Source ID to process (default: sld-scanned)
#   --time HH:MM:SS       Wall time (default: 04:00:00)
#   --batch-size N        Documents per batch per worker (default: 10)
#   --no-images           Disable image extraction
#   --no-describe         Disable LLM image descriptions

PARSER="marker"
SOURCE_ID="sld-scanned"
WALL_TIME="02:00:00"
BATCH_SIZE="10"
EXTRACT_IMAGES="--extract-images"
DESCRIBE_IMAGES="--describe-images"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --parser)      PARSER="$2";      shift 2 ;;
        --source)      SOURCE_ID="$2";   shift 2 ;;
        --time)        WALL_TIME="$2";   shift 2 ;;
        --batch-size)  BATCH_SIZE="$2";  shift 2 ;;
        --no-images)   EXTRACT_IMAGES=""; shift ;;
        --no-describe) DESCRIBE_IMAGES=""; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# Validate parser choice
case "$PARSER" in
    marker|docling|nougat) ;;
    *) echo "Unknown parser: $PARSER (choose: marker, docling, nougat)"; exit 1 ;;
esac

JOB_NAME="${PARSER}_parse_${SOURCE_ID}"

sbatch <<EOF
#!/bin/bash
#SBATCH --nodes=1
#SBATCH --qos=regular
#SBATCH --time=${WALL_TIME}
#SBATCH --constraint=gpu
#SBATCH --account=m5115_g
#SBATCH --job-name=${JOB_NAME}
#SBATCH --output=${JOB_NAME}_%j.out

cd \$SLURM_SUBMIT_DIR

source nersc_setup.sh

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

echo "Submitted: ${JOB_NAME} (parser=${PARSER}, source=${SOURCE_ID}, time=${WALL_TIME})"
