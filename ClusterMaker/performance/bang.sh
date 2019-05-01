#!/bin/bash
#
################################################################################
# Name:		bang.sh
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	January 12, 2018
# Last Changed: April 28, 2019
# Purpose:	Wrapper script for running bang.sh and Axb_random.py
# Notes:	This is *NOT* the cluster-friendly version!
################################################################################
#
# Usage: ./bang.sh JOBID
#
# To run on a standalone instance:
#	- Edit MATRIX_SIZES to set the sizes of the matrices to compute.
# 	- Update CLUSTER_NAME wth the instance and data source being tested.
#	- Provide a reasonable integer value for JOBID as an argument on the
#	  command line invoking bang.sh.
#	- Modify the log and data file arguments if needed.
#
# Examples:
# $ ./bang.sh t2.xlarge
# $ ./bang.sh rmarable_mbp_24Feb2018
#
# To run on a Grid Engine-based cluster:
#	- Edit MATRIX_SIZES to set the sizes of the matrices to compute.
#	- Update CLUSTER_NAME to match the name of the cluster stack.
#	- Modify the log and data file arguments if needed.
# 	- Edit qsub-Axb_random.sh to adjust the task array size or load any
#	  local HPC modules of interest.
#	- Submit qsub-Axb_random.sh to the cluster.
#	- See the README for help on submitting multiple jobs with perf-qsub.sh
#	  or using the qsub-wrapper scripts.
#
# Examples:
# $ qsub qsub-Axb_random.sh
# $ qsub qsub-Axb_random.50.sh
# $ ./perf-qsub.sh
#
###############################################################################
###############################################################################
#                                                                             #
#                   User-configurable options begin here.                     #
#        Please follow the guidelines provided to set each parameter.         #
#                                                                             #
###############################################################################
###############################################################################
#
# Change MATRIX_SIZES.conf to reflect the matrices to be solved.  This array is
# also used in the make_plots scripts.
#
# You have lots of freedom here but make sure to keep MATRIX_SIZES consistent
# when running against multiple compute entities and note any other relevant
# data like instance type, network configuration, EBS volume size/type, etc.
# that might affect the test results.  Be mindful of local disk space and
# cost constraints when solving very large matrices.

source MATRIX_SIZES.conf

# Set the CLUSTER_NAME.
#
# When running on a Grid Engine cluster, use the official cluster name to
# ensure data is compareable against other clusters. For ParallelCluster,
# this means CLUSTER_NAME should appear in the output of 'pcluster list.'
# For standalone instances, use something reasonable and distinctive like the
# examples offered below.
#
# Note: If you use periods in CLUSTER_NAME, please be advised they will be
# replaced with dashes to prevent issues with the downstream data parsers.

#CLUSTER_NAME="rmarable_mbp_sierra"	# rmarable's Macbook Pro
CLUSTER_NAME="rmarable-dev01"		# ParallelCluster stack rmarable-dev01

################################################################################
################################################################################
################################################################################
##                                                                            ##
##   WARNING: There are no more user-configurable options beyond this point.  ##
##  Don't change anything below this line unless you know what you are doing! ##
##                                                                            ##
################################################################################
################################################################################
################################################################################

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
echo "Setting PYTHON3=$PYTHON3..."

# Parse JOBID from the command line.  The script will fail if this is empty.

JOBID=$1
if [[ -z $JOBID ]]
then
	echo ""
	echo "Usage: bang.sh [ JOBID ]"
	echo "Please use reasonable integer values for \$JOBID on standalone instances!"
	echo ""
	exit 1
fi

# Set the compression_type.
# Supported options are pigz and gzip.
# Default to gzip if pigz isn't found.

if test -e /usr/bin/pigz || test -e /usr/local/bin/pigz
then
	compression_type=pigz
else
	compression_type=gzip
fi

# Modify the log and data file arguments.
# These settings should *NEVER* be changed.
# Please consult the README for additional guidance.
#
# Enabling CONSOLE_DUMP=yes doubles the amount of time required to complete
# the test.  This feature is only useful for debugging and should *not* be
# normally be turned on.  
#
# Disabling CREATE_LOGS permits computation of much bigger matrices at the
# cost of reducing I/O activity.  Since this is intended to be a generic
# apples-to-apples comparison of instances or HPC clusters, the operator 
# should never change this value.

CONSOLE_DUMP=no
CREATE_CSV=yes
CREATE_LOGS=yes

# Replace periods with dashes in CLUSTER_NAME to prevent issues with the
# downstream data parsers.

CLUSTER_NAME=`echo $CLUSTER_NAME | tr '.' '-'`

# Set paths for the Axb_random log, Axb_random CSV, and job summary data
# files.  Create the directories if they are missing.

LOG_DIR="./logs"
RAW_CSV_DIR="./csv"
RAW_SUMMARY_DIR="./csv/summary_raw"
for dir in $LOG_DIR $RAW_CSV_DIR $RAW_SUMMARY_DIR
do
	if [ ! -d $dir ]
	then
		mkdir -p $dir
	fi
done

# Create the CSV file header.

echo "execute_node,cluster_jobid,matrix_size,time_elapsed_sec,cluster_name" > $RAW_SUMMARY_DIR/summary.$CLUSTER_NAME.$JOBID.csv

# Invoke Axb_random.py with the arguments provided to bang.sh.

echo ""
for N in $MATRIX_SIZES
do
	if [[ -z $CLUSTER_NAME ]]
	then
		$PYTHON3 Axb_random.py --jobid $JOBID --matrix-size $N --console-dump $CONSOLE_DUMP --create-csv $CREATE_CSV --create-logs $CREATE_LOGS
	else
		$PYTHON3 Axb_random.py --jobid $JOBID --matrix-size $N --console-dump $CONSOLE_DUMP --create-csv $CREATE_CSV --create-logs $CREATE_LOGS --note "$CLUSTER_NAME"
	fi
	case $compression_type in
	pigz)
		$PYTHON3 compress_logfiles.py --jobid $JOBID --matrix-size $N --compression_type pigz
		;;
	gzip)
		$PYTHON3 compress_logfiles.py --jobid $JOBID --matrix-size $N --compression_type gzip
		;;
	*)
		"Invalid compression type selected, defaulting to gzip."
		compression_type=gzip
		;;
	esac
	if [ -f $JOBID.csv ]
	then
		paste -d, $JOBID.csv $JOBID.time.csv > $JOBID.csv.scratch
		rm $JOBID.csv $JOBID.time.csv
		cat $JOBID.csv.scratch >> $RAW_SUMMARY_DIR/summary.$CLUSTER_NAME.$JOBID.csv
		echo "+ Printing CSV data file to stdout:"
		cat $JOBID.csv.scratch
		echo "--------------------------------------------------------------------------------"
		echo ""
		mv $JOBID.csv.scratch $RAW_CSV_DIR/$JOBID.$CLUSTER_NAME.$N.csv
	fi
done

# Generate the summary CSV files.

sh csv_summary_time_measurement.sh $CLUSTER_NAME $JOBID

echo "================================================================================"
echo "  Please run this script to create summary CSV files for $CLUSTER_NAME:"
echo "================================================================================"
echo ""
echo "        ==>  ./combine_csv_summary_files_for_plotting.sh $CLUSTER_NAME"
echo ""

# Cleanup and exit.

echo "********************************************************************************"
echo "Finished!"
echo "Exiting..."
exit 0
