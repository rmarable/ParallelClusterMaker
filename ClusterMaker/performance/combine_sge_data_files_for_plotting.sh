################################################################################
# Name:		combine_sge_data_files_for_plotting.py
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	February 16, 2018
# Last Changed:	August 9, 2018
# Purpose:	Generate summary CSV files from SGE job/task data created by
#		bang.sh	running on Grid Engine clusters suitable for plotting
################################################################################

#!/bin/sh

# Get the name of the cluster.  Abort if a valid CLUSTER_NAME is not provided.
# For Grid Engine, this parameter *must* be set to match CLUSTER_NAME as
# defined in bang.sh.

if [ -z $1 ]
then
	echo ""
	echo "Usage: combine_sge_data_files_for_plotting.sh [ CLUSTER_NAME ]"
	exit 1
fi
CLUSTER_NAME=$1

# Create a TIMESTAMP and set up some critical file and directory paths.

TIMESTAMP=`date +%d-%b-%Y-%H:%M:%S`
SCRATCH_FILE=/tmp/SCRATCH_FILE.data
SGE_JOB_DATA_DIR=`pwd`/sge_job_data
SGE_MASTER_JOB_DATA_FILE="$CLUSTER_NAME.$TIMESTAMP.csv"
if [ ! -d $SGE_JOB_DATA_DIR ]
then
	mkdir -p $SGE_JOB_DATA_DIR
fi

# Generate a list of cluster jobs to process.
# By default, poll for jobs with existing SGE log files.
#
# To generate combined log files for a specific list of jobs, uncomment this
# line and append the cluster_job_IDs of interest.
# for cluster_job_ID in 1 2 3 4 5 6 7 8 9 10
# 
# To rebuild the SGE_MASTER_JOB_DATA_FILE:
# $ ./rebuild_sge_csv.sh  [ CLUSTER_NAME ]

for cluster_job_ID in `ls job_Axb_random.o* | awk -F. '{print $2}' | uniq | tr -d 'o'`
do
	SGE_JOB_DATA_FILE="$CLUSTER_NAME.job_Axb_random.$cluster_job_ID.csv"
	echo "Now creating $SGE_JOB_DATA_FILE..."
	qacct -j $cluster_job_ID > $SCRATCH_FILE
	task_array_size=`grep taskid $SCRATCH_FILE | wc -l`
	#
	# Generate a temp file using qacct data to determine the qsub, run,
	# and total execute times for the jobs being processed.
	#
	job_qsub_time=`cat $SCRATCH_FILE | grep qsub_time | sort | head -1 | awk '{printf"%s %s %s %s", $3, $4, $5, $6}'`
	job_start_time=`cat $SCRATCH_FILE | grep start_time | sort | head -1 | awk '{printf"%s %s %s %s", $3, $4, $5, $6}'`
	job_end_time=`cat $SCRATCH_FILE | grep end_time | sort | tail -1 | awk '{printf"%s %s %s %s", $3, $4, $5, $6}'`
	rm $SCRATCH_FILE
	printf "%s\n" $(( $(date -d "$job_end_time" "+%s") - $(date -d "$job_start_time" "+%s") ))
	sge_queue_time=$(( $(date -d "$job_start_time" "+%s") - $(date -d "$job_qsub_time" "+%s") ))
	sge_run_time=$(( $(date -d "$job_end_time" "+%s") - $(date -d "$job_start_time" "+%s") ))
	total_run_time=`expr "$sge_queue_time" + "$sge_run_time"`
	#
	# Build the CSV data file.
	#
	echo "$CLUSTER_NAME,$cluster_job_ID,$task_array_size,$sge_queue_time,$sge_run_time,$total_run_time" >> $SGE_JOB_DATA_DIR/$SGE_JOB_DATA_FILE
	echo "Removing SGE output logs for cluster_job_ID $cluster_job_ID..."
	rm job_Axb_random.o$cluster_job_ID.*
	echo "Saving  ===>  $SGE_JOB_DATA_DIR/$SGE_JOB_DATA_FILE"
	echo "================================================================================="
	echo "================================================================================="
done

# Combine the data from each task into a single CSV for the whole job.
# Don't add a header if it already exists.

echo "Combining the data files into a single CSV for the cluster..."
for csvfiles in `ls $SGE_JOB_DATA_DIR/$CLUSTER_NAME.job_Axb_random*csv`
do
	tail -1 $csvfiles >>  $SGE_JOB_DATA_DIR/$SGE_MASTER_JOB_DATA_FILE
	rm $csvfiles
done
if [[ `head -1 $SGE_JOB_DATA_DIR/$SGE_MASTER_JOB_DATA_FILE` == "cluster_name,cluster_job_ID,task_array_size,sge_queue_wait_time,sge_job_execute_time,total_run_time" ]]
then
	:
else
	echo "cluster_name,cluster_job_ID,task_array_size,sge_queue_wait_time,sge_job_execute_time,total_run_time" > $SGE_JOB_DATA_DIR/$SGE_MASTER_JOB_DATA_FILE.header
fi

# Set PYTHON3.
# Sort the CSV data file by job and MATRIX_SIZES.
# Print a summary to the console.

if [ ! -f /etc/centos-release ]
then
        PYTHON3=python3
else
        if [[ `cat /etc/centos-release | awk '{print $4}' | grep -c "^7.*"` -gt 0 ]]
        then
                PYTHON3=python3.6
        elif [[ `cat /etc/centos-release | awk '{print $4}' | grep -c "^6.*"` -gt 0 ]]
        then
                PYTHON3=python3
        else
                echo "You are running an unrecognized version of centos."
                echo "This might not work."
        fi
fi
echo "Setting PYTHON3=$PYTHON3..."
cat $SGE_JOB_DATA_DIR/$SGE_MASTER_JOB_DATA_FILE | sort -k2,2n -k 3,3n -t, >> $SGE_JOB_DATA_DIR/$SGE_MASTER_JOB_DATA_FILE.header
mv $SGE_JOB_DATA_DIR/$SGE_MASTER_JOB_DATA_FILE.header $SGE_JOB_DATA_DIR/$SGE_MASTER_JOB_DATA_FILE
echo "Saved  ===>  $SGE_JOB_DATA_DIR/$SGE_MASTER_JOB_DATA_FILE"
echo "================================================================================="
echo ""
echo "To save and view the plots locally, please run this command:"
echo ""
echo "   ===>   $PYTHON3 make_sge_cluster_plots.py"
echo ""
echo "Exiting..."
echo ""
exit 0
