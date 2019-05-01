################################################################################
# Name:		bite_Axb_random.sh
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	February 27, 2018
# Last Changed:	August 8, 2018
# Purpose:	Run multiple invocations of Axb_random.py in serial or parallel
#		on standalone EC2 instances
################################################################################
#
# Usage:
# $ ./bite_Axb_random.sh  [ JOB_TYPE ]  [ JOB_COUNT ]
#
# Example:
# $ ./bite_Axb_random.sh parallel 4 

#!/bin/bash

if [ -z $1 ]
then
	echo ""
	read -p 'Enter the job type ==> [s]erial or [p]arallel: ' JOB_TYPE
else
	JOB_TYPE=$1
fi
if [[ $JOB_TYPE == "s" || $JOB_TYPE == "serial" ]]
then
	JOB_TYPE="serial"
	
elif [[ $JOB_TYPE == "p" || $JOB_TYPE == "parallel" ]]
then
	JOB_TYPE="parallel"
else
	echo "$JOB_TYPE is an unsupported job type.  Aborting..."
	exit 1
fi
echo ""
echo "Selecting $JOB_TYPE testing mode."
if [ -z $2 ]
then
	echo ""
	read -p "Enter the number of bang.sh jobs to run in $JOB_TYPE: " JOB_COUNT
else
	JOB_COUNT=$2
fi
case $JOB_TYPE in
"serial")
	echo ""
	echo "Running $JOB_COUNT serial bang.sh jobs."
	COUNTER=1
	while [[ $JOB_COUNT -ge $COUNTER ]]
	do
		echo ""
		echo "################################################################################"
		echo "##                                 Running job #$COUNTER...                          ##"
		echo "################################################################################"
		./bang.sh $COUNTER
		COUNTER=$[$COUNTER+1]
	done
	;;
"parallel")
	echo ""
	echo "##############################################################"
	echo "Now running $JOB_COUNT x bang.sh jobs in parallel..."
	echo "System fan may engage when running on laptops or rack servers."
	echo "##############################################################"
	echo ""
	seq $JOB_COUNT | parallel -j0 ./bang.sh {}
	;;
*)
	echo "$JOB_TYPE is an unsupported job type.  Aborting..."
	exit 1
	;;
esac
exit 0
