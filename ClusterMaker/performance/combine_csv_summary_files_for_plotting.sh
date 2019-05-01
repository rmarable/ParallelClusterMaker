################################################################################
# Name:		combine_csv_summary_files_for_plotting.sh
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	January 12, 2018
# Last Changed: August 9, 2018
# Purpose:	Combine standalone CSV data files into a summary file
################################################################################

#!/bin/sh

# Get the name of the cluster.

if [ -z $1 ]
then
	echo ""
	echo "Usage: combine_csv_summary_files_for_plotting.sh [ CLUSTER_NAME ]"
	exit 1
fi
CLUSTER_NAME=$1

# Set PYTHON3.

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

# Create a TIMESTAMP and set up some critical file and directory paths.
# Combine all available CSV files from previous tests.

TIMESTAMP=`TZ=":US/Eastern" date +%d-%b-%Y-%H:%M:%S`
SCRATCH_FILE=/tmp/scratch_summary.csv
SUMMARY_DIR=`pwd`/summary
SUMMARY_FINAL_DIR=`pwd`/summary_final
if [ ! -d $SUMMARY_FINAL_DIR ]
then
	mkdir $SUMMARY_FINAL_DIR
fi
SUMMARY_DATA_FILE=$SUMMARY_FINAL_DIR/summary.$CLUSTER_NAME.$TIMESTAMP.csv
header="cluster_jobID,matrix_size,compute_time,cluster_name,raw_log_size_bytes,gzip_log_size_bytes,fileproc_time"
echo $header > $SCRATCH_FILE.header
cd $SUMMARY_DIR
for csvfile in `ls summary.$CLUSTER_NAME.*.csv`
do
	tail -n+2 $csvfile >> $SCRATCH_FILE
done
if [ -f $SUMMARY_DATA_FILE ]
then
	cat $SCRATCH_FILE >> $SUMMARY_DATA_FILE
else
	cat $SCRATCH_FILE >> $SCRATCH_FILE.header
	mv $SCRATCH_FILE.header $SUMMARY_DATA_FILE
	rm $SCRATCH_FILE
fi
cat $SUMMARY_DATA_FILE | sort -k1,1n -k 2,2n -t, | awk 'BEGIN{FS=","; OFS=",";} {print $4, $1, $2, $3, $5, $6, $7}' > $SUMMARY_DATA_FILE.sorted
mv $SUMMARY_DATA_FILE.sorted $SUMMARY_DATA_FILE
echo ""
echo "Finished creating:"
echo ""
echo "   ===>   $SUMMARY_DATA_FILE"
echo ""
echo "--------------------------------------------------------------------------------"
echo ""
echo "To save and view the plots, please run this command (or just cut/paste):"
echo ""
echo "   ===>   $PYTHON3 make_standalone_plots.py"
echo ""
echo "Finished!"
echo "Exiting..."
echo ""
exit 0
