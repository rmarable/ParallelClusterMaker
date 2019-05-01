################################################################################
# Name:		rebuild_sge_csv.sh
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	February 24, 2018
# Last Changed: February 26, 2018
# Purpose:	Rebuild summary CSV files generated from qsub-Axb_random.sh
################################################################################
#
#!/bin/sh

# Parse the name of the cluster from the command line.
# Abort if this parameter is not provided.

CLUSTER_NAME=$1
if [ -z $CLUSTER_NAME ]
then
        echo ""
        echo "Usage: rebuild_sge_csv.sh [ CLUSTER_NAME ]"
        exit 1
fi

# Get confirmation from the user before deleting anything.

echo ""
echo "rebuild_sge_csv.sh parses all existing performance data generated using"
echo "bang.sh for cluster $CLUSTER_NAME and rebuilds the summary CSV files."
echo "This is very useful for recovering from inadvertent data deletions and"
echo "generating new plots with data collected in subsequent runs."
echo ""
echo "NOTE: The existing 'sge_job_data' and 'summary' directories will be deleted."
echo ""
read -p 'Type YES to confirm for cluster '"$CLUSTER_NAME:"' ' confirm
case $confirm in
"YES"|"yes")
	echo ""
	echo "Confirmed! Rebuilding the summary CSV file for $CLUSTER_NAME..."
	;;
*)
	echo ""
        echo "*** CONFIRMATION ERROR ***"
        echo "Aborting..."
        exit 1
	;;
esac

# Set up some important file and directory paths.
# Delete the existing sge_job_data and summary directores.

RAW_CSV_DIR=`pwd`/csv/summary_raw
SGE_JOB_DATA_DIR=`pwd`/sge_job_data
SGE_DATA_FILE=$SGE_JOB_DATA_DIR/$CLUSTER_NAME.csv
SUMMARY_DATA_DIR=`pwd`/summary
for dir in $SGE_JOB_DATA_DIR $SUMMARY_DATA_DIR
do
	if [ -d $dir ]
	then
		rm -rf $dir
		mkdir -p $dir
	else
		mkdir -p $dir
	fi
done

# Rebuild the CSV summary files for this cluster using previously collected
# data and store the results in SGE_JOB_DATA_DIR.

for cluster_job_ID in `ls $RAW_CSV_DIR/summary.$CLUSTER_NAME.job_Axb_random.*.csv | awk -F. '{print $4}' | uniq`
do
	sh csv_summary_time_measurement.sh $CLUSTER_NAME
done
for file in `ls $SUMMARY_DATA_DIR/summary.$CLUSTER_NAME.*.csv | sort -t. -k5,5n`
do
	cat $file | awk 'FNR > 1' >> $SGE_DATA_FILE.data
done
echo "cluster_jobID,task_id,matrix_size,compute_time,cluster_name" > $SGE_DATA_FILE
cat $SGE_DATA_FILE.data >> $SGE_DATA_FILE

# Cleanup and exit.

rm $SGE_DATA_FILE.data
exit 0
