################################################################################
# Name:		csv_summary_time_measurement.sh
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	February 11, 2018
# Last Changed:	March 10, 2018
# Purpose:	Generate summary CSV input files from the output of bang.sh
################################################################################
#
#!/bin/sh

# Parse CLUSTER_NAME and JOBID from the command line.
# Abort if CLUSTER_NAME was not provided as input.

if [ -z $1 ]
then
	echo ""
	echo "Usage: csv_summary_time_measurement.sh [ CLUSTER_NAME ] [ JOBID ]"
	echo "Clusters populate JOBID automatically from qsub-Axb_random.sh."
	echo "Please use reasonable integer values for JOBID on standalone instances."
	echo ""
	exit 1
fi

# Periods in CLUSTER_NAME are replaced with dashes to prevent issues with
# the downstream data parsers.  This is now handled by bang.sh but this 
# check is being left intact just to be on the safe side.

CLUSTER_NAME=`echo $1 | tr '.' '-'`

# If JOBID is not provided as an input, use "job_Axb_random" as the default.
# This preserves compatibility with qsub-Axb_random.sh and ensures that we 
# don't have to do extra work if bang.sh is running in standalone mode.

if [ -z $2 ]
then
	JOBID=job_Axb_random
	standalone=no
else
	JOBID=$2
	standalone=yes
fi

# Set up some critical file and directory paths.

RAW_SUMMARY_DIR=`pwd`/csv/summary_raw
SUMMARY_DATA_DIR=`pwd`/summary
SCRATCH_FILE=/tmp/SCRATCH_FILE.data
for dir in $RAW_SUMMARY_DIR $SUMMARY_DATA_DIR
do
	if [ ! -d $dir ]
	then
		mkdir -p $dir
	fi
done

# Generate a list of previously processed cluster jobs which will be used to
# build the master CSV summary data file. These loops can handle input from
# Grid Engine clusters and standalone instances.

cd $RAW_SUMMARY_DIR
# for cluster_job_ID in 9 10 11 12
for cluster_job_ID in `ls summary.$CLUSTER_NAME.$JOBID.*csv | awk -F. '{print $4}' | uniq | sort -n`
do
	if [[ $standalone = "no" ]]
	then
		summary_data_file=summary.$CLUSTER_NAME.$cluster_job_ID.csv
		for cluster_job_files in `ls summary.$CLUSTER_NAME.$JOBID.$cluster_job_ID*.csv`
		do
			cat $cluster_job_files | awk 'FNR > 1' | awk -F, '{printf"%s,%s,%s,%s\n", $2, $3, $4, $5}' | sed -e "s/$JOBID.//g" |  awk -F. '{printf"%s,%s.%s\n", $1, $2, $3}' >> $SCRATCH_FILE.data
		done
	echo "cluster_jobID,task_id,matrix_size,compute_time,cluster_name" > $SCRATCH_FILE.header
	cat $SCRATCH_FILE.data | sort -k1,1n -k2,2n -k3,3n -t, > $SCRATCH_FILE.sorted
	else
		summary_data_file=summary.$CLUSTER_NAME.$JOBID.csv
		for cluster_job_files in `ls summary.$CLUSTER_NAME.$JOBID.csv`
		do
			cat $cluster_job_files | awk 'FNR > 1' | awk -F, '{printf"%s,%s,%s,%s,%s,%s,%s\n", $2, $3, $4, $5, $6, $7, $8}' >> $SCRATCH_FILE.data
		done
	echo "cluster_jobID,matrix_size,compute_time,cluster_name,raw_log_size_bytes,gzip_log_size_bytes,fileproc_time" > $SCRATCH_FILE.header
	cat $SCRATCH_FILE.data | sort -k1,1n -k2,2n -t, > $SCRATCH_FILE.sorted
	fi
	cat $SCRATCH_FILE.sorted >> $SCRATCH_FILE.header
	mv $SCRATCH_FILE.header $SUMMARY_DATA_DIR/$summary_data_file
	rm $SCRATCH_FILE.*
	echo "Saved  ==>  $SUMMARY_DATA_DIR/$summary_data_file"
	echo ""
done

# Cleanup and exit.

exit 0
