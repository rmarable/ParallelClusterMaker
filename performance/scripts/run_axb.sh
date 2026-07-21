#!/bin/bash
set -euo pipefail
################################################################################
# Name:         run_axb.sh
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Created On:   January 12, 2018
# Last Changed: July 19, 2026
# Purpose:      Core Axb_random.py worker — solve random matrices, compress
#               logs, and aggregate CSV data for one JOBID
################################################################################
#
# Usage:
#   ./run_axb.sh --jobid N [--cluster NAME]
#   ./run_axb.sh -J N [-C NAME]
#
# Called directly or via hpc-perftest.sh run -t axb.
################################################################################

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON3=python3

# ---------- defaults --------------------------------------------------------
_CONF="$SCRIPT_DIR/../MATRIX_SIZES.conf"
[[ -f "$_CONF" ]] || { echo "ERROR: MATRIX_SIZES.conf not found at $_CONF" >&2; exit 1; }
MATRIX_SIZES=$(grep -m1 '^MATRIX_SIZES=' "$_CONF" | sed 's/^MATRIX_SIZES=//; s/^"//; s/"$//')
CLUSTER_NAME="$(hostname -s)"
JOBID=""

# ---------- argument parsing -------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --jobid|-J)   JOBID="$2";        shift 2 ;;
        --cluster|-C) CLUSTER_NAME="$2"; shift 2 ;;
        *)
            echo "ERROR: unknown argument: $1" >&2
            echo "Usage: $0 --jobid N [--cluster NAME]" >&2
            exit 1 ;;
    esac
done

if [[ -z "$JOBID" ]]; then
    echo "ERROR: --jobid is required." >&2
    echo "Usage: $0 --jobid N [--cluster NAME]" >&2
    exit 1
fi

trap 'rm -f "$JOBID.csv.scratch" "$JOBID.csv" "$JOBID.time.csv"' EXIT

# Replace periods with dashes to avoid breaking downstream CSV parsers.
CLUSTER_NAME=$(echo "$CLUSTER_NAME" | tr '.' '-')

# ---------- compression ------------------------------------------------------
if command -v pigz >/dev/null 2>&1; then
    compression_type=pigz
else
    compression_type=gzip
fi

# ---------- output settings (rarely need changing) ---------------------------
CONSOLE_DUMP=no
CREATE_CSV=yes
CREATE_LOGS=yes

# ---------- directory setup --------------------------------------------------
LOG_DIR="./logs"
RAW_CSV_DIR="./csv"
RAW_SUMMARY_DIR="./csv/summary_raw"
for dir in "$LOG_DIR" "$RAW_CSV_DIR" "$RAW_SUMMARY_DIR"; do
    mkdir -p "$dir"
done

# ---------- CSV header -------------------------------------------------------
echo "exec_node,cluster_jobID,matrix_size,compute_time,note,raw_log_size_bytes,compressed_log_size_bytes,fileproc_time" \
    > "$RAW_SUMMARY_DIR/summary.$CLUSTER_NAME.$JOBID.csv"

# ---------- main loop --------------------------------------------------------
echo ""
for N in $MATRIX_SIZES; do
    $PYTHON3 "$SCRIPT_DIR/Axb_random.py" \
        --jobid "$JOBID" --matrix-size "$N" \
        --console-dump "$CONSOLE_DUMP" --create-csv "$CREATE_CSV" \
        --create-logs "$CREATE_LOGS" --note "$CLUSTER_NAME"

    $PYTHON3 "$SCRIPT_DIR/compress_logfiles.py" \
        --jobid "$JOBID" --matrix-size "$N" \
        --compression_type "$compression_type"

    if [[ -f "$JOBID.csv" ]]; then
        paste -d, "$JOBID.csv" "$JOBID.time.csv" > "$JOBID.csv.scratch"
        rm "$JOBID.csv" "$JOBID.time.csv"
        cat "$JOBID.csv.scratch" >> "$RAW_SUMMARY_DIR/summary.$CLUSTER_NAME.$JOBID.csv"
        echo "+ CSV data:"
        cat "$JOBID.csv.scratch"
        echo "--------------------------------------------------------------------------------"
        echo ""
        mv "$JOBID.csv.scratch" "$RAW_CSV_DIR/$JOBID.$CLUSTER_NAME.$N.csv"
    else
        echo "WARNING: $JOBID.csv not found for matrix_size=$N — Axb_random.py may have failed." >&2
    fi
done

# ---------- summarise --------------------------------------------------------
bash "$SCRIPT_DIR/csv_summary_time_measurement.sh" "$CLUSTER_NAME" "$JOBID"

echo ""
echo "Run combine to finalise:"
echo "  ./hpc-perftest.sh plot --cluster $CLUSTER_NAME"
echo ""
echo "Finished!"
exit 0
