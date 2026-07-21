#!/bin/bash
set -euo pipefail
################################################################################
# Name:		csv_summary_time_measurement.sh
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	February 11, 2018
# Last Changed:	March 10, 2018
# Purpose:	Generate summary CSV input files from the output of run_axb.sh
################################################################################

# Parse CLUSTER_NAME and JOBID from the command line.
# Abort if CLUSTER_NAME was not provided as input.

if [ -z "$1" ]
then
	echo ""
	echo "Usage: csv_summary_time_measurement.sh [ CLUSTER_NAME ] [ JOBID ]"
	echo "Clusters populate JOBID automatically from sbatch-Axb_random.sh."
	echo "Please use reasonable integer values for JOBID on standalone instances."
	echo ""
	exit 1
fi

# Periods in CLUSTER_NAME are replaced with dashes to prevent issues with
# the downstream data parsers.  run_axb.sh handles this, but the check
# is left here as a safety net.

CLUSTER_NAME=$(echo "$1" | tr '.' '-')

# If JOBID is not provided as an input, use "job_Axb_random" as the default.
# This preserves compatibility with sbatch-Axb_random.sh and run_axb.sh.

if [ -z "$2" ]
then
	JOBID=job_Axb_random
	standalone=no
else
	JOBID=$2
	standalone=yes
fi

if [[ ! "$JOBID" =~ ^[a-zA-Z0-9._-]+$ ]]; then
	echo "ERROR: JOBID contains invalid characters: $JOBID" >&2
	echo "  Only letters, digits, dots, underscores, and hyphens are permitted." >&2
	exit 1
fi

# Set up some critical file and directory paths.

RAW_SUMMARY_DIR=$(pwd)/csv/summary_raw
SUMMARY_DATA_DIR=$(pwd)/summary
SCRATCH_FILE=$(mktemp /tmp/pcluster_scratch.XXXXXX)
trap 'rm -f "${SCRATCH_FILE}" "${SCRATCH_FILE}.data" "${SCRATCH_FILE}.header" "${SCRATCH_FILE}.sorted"' EXIT
for dir in "$RAW_SUMMARY_DIR" "$SUMMARY_DATA_DIR"
do
	if [ ! -d "$dir" ]
	then
		mkdir -p "$dir"
	fi
done

# Generate a list of previously processed cluster jobs which will be used to
# build the master CSV summary data file.

pushd "$RAW_SUMMARY_DIR" >/dev/null
shopt -s nullglob
_csv_files=("summary.$CLUSTER_NAME.$JOBID."*csv)
shopt -u nullglob
for _f in "${_csv_files[@]}"; do
	cluster_job_ID=$(echo "$_f" | awk -F. '{print $4}')
	if [[ $standalone = "no" ]]
	then
		summary_data_file=summary.$CLUSTER_NAME.$cluster_job_ID.csv
		shopt -s nullglob
		for cluster_job_files in "summary.$CLUSTER_NAME.$JOBID.$cluster_job_ID"*.csv
		do
			awk 'FNR > 1' "$cluster_job_files" | awk -F, '{printf"%s,%s,%s,%s\n", $2, $3, $4, $5}' | sed -e "s|${JOBID}\\.||g" | awk -F. '{printf"%s,%s.%s\n", $1, $2, $3}' >> "$SCRATCH_FILE.data"
		done
		shopt -u nullglob
		echo "cluster_jobID,task_id,matrix_size,compute_time" > "$SCRATCH_FILE.header"
		sort -k1,1 -k2,2n -k3,3n -t, "$SCRATCH_FILE.data" > "$SCRATCH_FILE.sorted"
	else
		summary_data_file=summary.$CLUSTER_NAME.$JOBID.csv
		cluster_job_files="summary.$CLUSTER_NAME.$JOBID.csv"
		if [[ -f "$cluster_job_files" ]]; then
			awk 'FNR > 1' "$cluster_job_files" | awk -F, '{printf"%s,%s,%s,%s,%s,%s,%s\n", $2, $3, $4, $5, $6, $7, $8}' >> "$SCRATCH_FILE.data"
		fi
		echo "cluster_jobID,matrix_size,compute_time,cluster_name,raw_log_size_bytes,gzip_log_size_bytes,fileproc_time" > "$SCRATCH_FILE.header"
		sort -k1,1n -k2,2n -t, "$SCRATCH_FILE.data" > "$SCRATCH_FILE.sorted"
	fi
	cat "$SCRATCH_FILE.sorted" >> "$SCRATCH_FILE.header"
	mv "$SCRATCH_FILE.header" "$SUMMARY_DATA_DIR/$summary_data_file"
	rm -f "$SCRATCH_FILE.data" "$SCRATCH_FILE.sorted"
	echo "Saved  ==>  $SUMMARY_DATA_DIR/$summary_data_file"
	echo ""
done
popd >/dev/null

# Cleanup and exit.

exit 0
