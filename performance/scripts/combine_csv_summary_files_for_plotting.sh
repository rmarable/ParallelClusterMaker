#!/bin/bash
set -euo pipefail
################################################################################
# Name:		combine_csv_summary_files_for_plotting.sh
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	January 12, 2018
# Last Changed: August 9, 2018
# Purpose:	Combine standalone CSV data files into a summary file
################################################################################

# Get the name of the cluster.

if [ -z "$1" ]
then
	echo ""
	echo "Usage: combine_csv_summary_files_for_plotting.sh [ CLUSTER_NAME ]"
	exit 1
fi
CLUSTER_NAME=$1

# Set PYTHON3.

PYTHON3=python3

# Create a TIMESTAMP and set up some critical file and directory paths.
# Combine all available CSV files from previous tests.

TIMESTAMP=$(TZ=":US/Eastern" date +%d-%b-%Y-%H-%M-%S)
SCRATCH_FILE=$(mktemp /tmp/pcluster_scratch.XXXXXX)
[[ -n "$SCRATCH_FILE" ]] || { echo "ERROR: mktemp failed" >&2; exit 1; }
trap 'rm -f "${SCRATCH_FILE}" "${SCRATCH_FILE}.header"' EXIT
SUMMARY_DIR=$(pwd)/summary
SUMMARY_FINAL_DIR=$(pwd)/summary_final
if [ ! -d "$SUMMARY_FINAL_DIR" ]
then
	mkdir "$SUMMARY_FINAL_DIR"
fi
SUMMARY_DATA_FILE=$SUMMARY_FINAL_DIR/summary.$CLUSTER_NAME.$TIMESTAMP.csv
header="cluster_name,cluster_jobID,matrix_size,compute_time,raw_log_size_bytes,gzip_log_size_bytes,fileproc_time"
echo "$header" > "$SCRATCH_FILE.header"
if [[ ! -d "$SUMMARY_DIR" ]]; then
	echo "ERROR: $SUMMARY_DIR not found. Run hpc-perftest.sh run first." >&2
	exit 1
fi
cd "$SUMMARY_DIR"
shopt -s nullglob
for csvfile in summary.$CLUSTER_NAME.*.csv
do
	tail -n+2 "$csvfile" >> "$SCRATCH_FILE"
done
shopt -u nullglob
if [ -f "$SUMMARY_DATA_FILE" ]
then
	cat "$SCRATCH_FILE" >> "$SUMMARY_DATA_FILE"
else
	cat "$SCRATCH_FILE" >> "$SCRATCH_FILE.header"
	mv "$SCRATCH_FILE.header" "$SUMMARY_DATA_FILE"
	rm "$SCRATCH_FILE"
fi
_csv_header=$(head -1 "$SUMMARY_DATA_FILE")
tail -n+2 "$SUMMARY_DATA_FILE" \
    | sort -k1,1n -k2,2n -t, \
    | awk 'BEGIN{FS=","; OFS=",";} {print $4, $1, $2, $3, $5, $6, $7}' \
    > "$SUMMARY_DATA_FILE.sorted"
{ echo "$_csv_header"; cat "$SUMMARY_DATA_FILE.sorted"; } > "$SUMMARY_DATA_FILE"
rm -f "$SUMMARY_DATA_FILE.sorted"
echo ""
echo "Finished creating:"
echo ""
echo "   ===>   $SUMMARY_DATA_FILE"
echo ""
echo "--------------------------------------------------------------------------------"
echo ""
echo "To save and view the plots, please run this command (or just cut/paste):"
echo ""
echo "   ===>   ../hpc-perftest.sh plot --type unified"
echo ""
echo "Finished!"
echo "Exiting..."
echo ""
exit 0
