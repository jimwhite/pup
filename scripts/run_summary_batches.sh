#!/usr/bin/env bash
#
# Run summarization batches from the priority report.
# Each batch uses an exact notebook list (scripts/batch-lists/batch-N.txt).
#
# Usage:
#   ./scripts/run_summary_batches.sh [BATCH_NUMBER...]
#
# Examples:
#   ./scripts/run_summary_batches.sh          # Run all 5 batches
#   ./scripts/run_summary_batches.sh 1        # Run only batch 1
#   ./scripts/run_summary_batches.sh 2 3      # Run batches 2 and 3
#   ./scripts/run_summary_batches.sh --dry-run 1  # Dry-run batch 1
#   ./scripts/run_summary_batches.sh -v 1        # Verbose (LLM message log)
#   ./scripts/run_summary_batches.sh --overwrite 1 # Re-run batch 1 from scratch
#   ./scripts/run_summary_batches.sh --version=v2-groq 1  # Use version label
#   SUMMARY_VERSION=v2-groq ./scripts/run_summary_batches.sh 1  # Via env var
#
# Logs are written to logs/batch-N-YYYYMMDD-HHMMSS.log
#
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-.venv/bin/python}"
SCRIPT="scripts/summarize_kg.py"
LISTDIR="scripts/batch-lists"
LOGDIR="logs"
JOBS="${JOBS:-4}"
VERSION="${SUMMARY_VERSION:-}"

# ── Parse flags vs batch numbers ────────────────────────────────────
EXTRA_FLAGS=""
BATCHES=()

for arg in "$@"; do
    case "$arg" in
        --dry-run)   EXTRA_FLAGS="$EXTRA_FLAGS --dry-run" ;;
        --overwrite) EXTRA_FLAGS="$EXTRA_FLAGS --overwrite" ;;
        -v|--verbose) EXTRA_FLAGS="$EXTRA_FLAGS -v" ;;
        --version=*) VERSION="${arg#--version=}" ;;
        [1-5])       BATCHES+=("$arg") ;;
        *)           echo "Unknown argument: $arg (expected 1-5, --dry-run, --overwrite, --version=LABEL, or -v)" >&2; exit 1 ;;
    esac
done

# Add --version flag if set
if [[ -n "$VERSION" ]]; then
    EXTRA_FLAGS="$EXTRA_FLAGS --version $VERSION"
fi

# Default: all batches
if [[ ${#BATCHES[@]} -eq 0 ]]; then
    BATCHES=(1 2 3 4 5)
fi

mkdir -p "$LOGDIR"

# ── Batch labels ────────────────────────────────────────────────────

batch_label() {
    case "$1" in
        1) echo "Foundation: FTY, Basic Types, Osets, Ordinals" ;;
        2) echo "Teaching: Textbook, Proofstyles, Demos" ;;
        3) echo "Hints, Clause Processors, Utilities, Arithmetic" ;;
        4) echo "Standard Libraries: Lists, Alists, IO, Stobjs, Bitsets" ;;
        5) echo "Advanced: APT, Codewalker, ABNF, Projects" ;;
    esac
}

# ── Run a batch ─────────────────────────────────────────────────────

run_batch() {
    local batch_num="$1"
    local label
    label=$(batch_label "$batch_num")
    local listfile="$LISTDIR/batch-${batch_num}.txt"

    if [[ ! -f "$listfile" ]]; then
        echo "ERROR: $listfile not found" >&2
        return 1
    fi

    local nb_count
    nb_count=$(grep -c '^books/' "$listfile")

    local timestamp
    timestamp=$(date +%Y%m%d-%H%M%S)
    local logfile="$LOGDIR/batch-${batch_num}-${timestamp}.log"

    echo ""
    echo "═══════════════════════════════════════════════════════════════"
    echo "  BATCH $batch_num: $label"
    echo "  Notebooks: $nb_count (from $listfile)"
    echo "  Log: $logfile"
    echo "═══════════════════════════════════════════════════════════════"
    echo ""

    local start_time
    start_time=$(date +%s)

    {
        echo "=== Batch $batch_num: $label ==="
        echo "Started: $(date)"
        echo "Notebooks: $nb_count from $listfile"
        echo ""
    } >> "$logfile"

    "$PYTHON" "$SCRIPT" \
        --notebook-list "$listfile" \
        --jobs "$JOBS" \
        $EXTRA_FLAGS \
        2>&1 | tee -a "$logfile"

    local end_time elapsed_min
    end_time=$(date +%s)
    elapsed_min=$(( (end_time - start_time) / 60 ))

    {
        echo ""
        echo "=== Batch $batch_num complete ==="
        echo "Finished: $(date)"
        echo "Elapsed: ${elapsed_min} minutes"
    } >> "$logfile"

    echo ""
    echo "  Batch $batch_num complete (${elapsed_min} min). Log: $logfile"
    echo ""
}

# ── Main ────────────────────────────────────────────────────────────

echo ""
echo "ACL2 KG Summarization — Batch Runner"
echo "Batches to run: ${BATCHES[*]}"
if [[ -n "$VERSION" ]]; then
    echo "Version: $VERSION"
fi
if [[ -n "$EXTRA_FLAGS" ]]; then
    echo "Flags:$EXTRA_FLAGS"
fi
echo ""

total_start=$(date +%s)

for b in "${BATCHES[@]}"; do
    run_batch "$b"
done

total_end=$(date +%s)
total_min=$(( (total_end - total_start) / 60 ))
echo "═══════════════════════════════════════════════════════════════"
echo "  All requested batches complete. Total: ${total_min} minutes."
echo "═══════════════════════════════════════════════════════════════"
