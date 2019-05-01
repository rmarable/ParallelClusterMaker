################################################################################
# Name:		perf-standalone-test.sh
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	February 14, 2018
# Last Changed:	August 8, 2018
# Deployed On:  {{ lookup('pipe','date \"+%B %-d, %Y\"') }}
# Purpose:	Wrapper for invoking the performance suite multiple times on
#		standalone EC2 instances
################################################################################
# 
# Invoke perf-standalone-test JOB_RUNS times starting with STARTING_JOBID:
# $ ./perf-standalone-test.sh  [ JOB_RUNS ]  [ STARTING_JOBID ]
#
#!/bin/sh

echo ""
echo "Welcome to perf-standalone-test.sh!"
if [ -z $1 ]
then
	read -p 'Please enter the number of times to invoke bang.sh in serial: ' JOB_RUNS
else
	JOB_RUNS=$1
fi
if [ -z $2 ]
then
	COUNTER=1
else
	COUNTER=$2
	JOB_RUNS=$[$COUNTER+$JOB_RUNS-1]
fi
while [ $JOB_RUNS -ge $COUNTER ]
do
	./bang.sh $COUNTER
	COUNTER=$[$COUNTER+1]
done
