################################################################################
# Name:		rebuild_standalone_csv.sh
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	February 24, 2018
# Last Changed: March 3, 2018
# Purpose:	Rebuild summary CSV files generated from standalone instances
################################################################################

#!/bin/sh

# Parse the name of the cluster from the command line.
# Abort if this parameter is not provided.

if [ -z $1 ]
then
        echo ""
        echo "Usage: rebuild_Axb_random_csv.sh [ CLUSTER_NAME ]"
        echo ""
        exit 1
fi
CLUSTER_NAME=$1

# Get confirmation from the user before deleting anything.

echo ""
echo "This script parses all existing performance data generated from bang.sh"
echo "on this machine and rebuilds the CSV summary files.  This is useful for"
echo "recovering from inadvertent data deletions."
echo ""
echo "The existing 'summary' directory will be recreated."
echo ""
read -p 'Type YES to confirm for cluster $CLUSTER_NAME: ' confirm
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

# Set up some important paths.
# Delete the existing 'summary' directory if it already exists.

RAW_CSV=`pwd`/csv/summary_raw
CSV=`pwd`/csv
SUMMARY_DIR=`pwd`/summary
if [ -d $SUMMARY_DIR ]
then
	rm -rf $SUMMARY_DIR
fi

# Rebuild the CSV summary file.

for csvfile in `ls $RAW_CSV/summary.$CLUSTER_NAME.*.csv | awk -F. '{print $3}'`
do
	sh csv_summary_time_measurement.sh $CLUSTER_NAME $csvfile
done
sh combine_csv_summary_files_for_plotting.sh $CLUSTER_NAME
exit 0
