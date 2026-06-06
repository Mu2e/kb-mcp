#!/bin/bash
# Submit any kb command as a NERSC CPU batch job.
#
# Usage: ./scripts/nersc_submit_kb_job.sh [options] -- kb <subcommand> [args...]
#
# Options:
#   --time HH:MM:SS   Wall time (default: 04:00:00)
#   --name NAME       Job name (default: kb_job)
#   --dependency ID   SLURM dependency job ID
#
# Examples:
#   ./scripts/nersc_submit_kb_job.sh -- kb eval run --name "agentic-v1" \
#       --generation-id 09bfc266-... --search-type agentic
#
#   ./scripts/nersc_submit_kb_job.sh --time 08:00:00 --name my_eval -- \
#       kb eval run --name "rag-v1" --generation-id 09bfc266-...

WALL_TIME="04:00:00"
JOB_NAME="kb_job"
DEPENDENCY_ID=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --time)       WALL_TIME="$2";       shift 2 ;;
        --name)       JOB_NAME="$2";        shift 2 ;;
        --dependency) DEPENDENCY_ID="$2";   shift 2 ;;
        --)           shift; break ;;
        *) echo "Unknown option: $1"; echo "Use -- to separate options from the kb command."; exit 1 ;;
    esac
done

if [[ $# -eq 0 ]]; then
    echo "Error: no kb command specified after --"
    echo "Usage: $0 [--time HH:MM:SS] [--name NAME] -- kb <subcommand> [args...]"
    exit 1
fi

KB_CMD="$*"

SLURM_ARGS=()
if [[ -n "$DEPENDENCY_ID" ]]; then
    SLURM_ARGS+=(--dependency=afterany:"$DEPENDENCY_ID")
fi

JOB_ID=$(sbatch --parsable "${SLURM_ARGS[@]}" <<EOF
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

${KB_CMD}
EOF
)

echo "Submitted job ${JOB_ID}: ${JOB_NAME}"
echo "  Command: ${KB_CMD}"
echo "  Output:  ${JOB_NAME}_${JOB_ID}.out"
echo "  Monitor: squeue -j ${JOB_ID}"
