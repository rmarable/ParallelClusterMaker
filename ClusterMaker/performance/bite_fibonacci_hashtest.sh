################################################################################
# Name:		bite_fibonacci_hashtest.sh
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	May 4, 2018
# Last Changed:	April 29, 2019
# Purpose:	Run multiple fibonacci_hashtest.py jobs in parallel or serial
#		mode on standalone EC2 instances 
################################################################################
#
# Usage:
# $ ./bite_fibonacci_hashtest.sh  [ JOB_TYPE ]  [ JOB_COUNT ]
#
# Example:
# $ ./bite_fibonacci_hashtest.sh parallel 4 

#!/bin/bash

# Define a function that invokes fibonacci_hashtest.py with the standard
# arguments.

compute_fibonacci() 
{

JOB_ID=$1
VALUE=$2
OUTFILE=$3

if [[ $TEST_MODE == "INDEX" ]]
then
	for i in {1..$COUNT..1}
	do
		echo "Computing $VALUE Fibonacci numbers @ `date`"
		$PYTHON3 ./fibonacci_hashtest.py --enable_index -I $VALUE -O $OUTFILE -C pigz
		echo "Finished @ `date`"
		echo ""
	done
else
	echo "Finding Fibonacci numbers of length $VALUE @ `date`"
	$PYTHON3 ./fibonacci_hashtest.py --enable_digits -D $VALUE -O $OUTFILE -C pigz
	echo "Finished @ `date`"
	echo ""
fi

}
export -f compute_fibonacci

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
	echo ""
	echo "*** ERROR ***"
	echo "$JOB_TYPE is an unsupported job type.  Aborting..."
	exit 1
fi
echo ""
echo "Selecting $JOB_TYPE test mode"

if [ -z $2 ]
then
	echo ""
	read -p "Enter the number of fibonacci_hashtest.py jobs to run in $JOB_TYPE: " JOB_COUNT
else
	JOB_COUNT=$2
fi
echo "Setting job_count = $JOB_COUNT"

# Select the TEST_MODE.

if [ -z $3 ]
then
	echo ""
	read -p 'Select INDEX or DIGITS mode: ' TEST_MODE
else
	TEST_MODE=$3
fi
if [ -z TEST_MODE ]
then
	TEST_MODE="INDEX"
fi
case $TEST_MODE in
INDEX|I|index|i )
	TEST_MODE="INDEX"
	;;
	DIGITS|D|digits|d )
		TEST_MODE="DIGITS"
	;;
	* )
		echo ""
		echo "*** ERROR ***"
		echo "test_mode \"$TEST_MODE\" is an invalid selection."
		echo "Aborting..."
		exit 1
	;;
esac
echo "Selecting $TEST_MODE test mode"
echo ""

# Set the COUNT or DIGITS value depending on the test_mode.

if [ -z $4 ]
then
	case $TEST_MODE in
	INDEX|I|index|i )
		TEST_MODE="INDEX"
		read -p 'Enter a value for INDEX: ' COUNT
		echo "Setting INDEX to $INDEX"
		echo ""
		VALUE=$COUNT
		;;
	DIGITS|D|digits|d )
		TEST_MODE="DIGITS"
		read -p 'Enter a value for DIGITS: ' DIGITS
		VALUE=$DIGITS
		echo "Setting number_of_digits to $DIGITS"
		echo ""
		;;
	* )
		echo "*** ERROR ***"
		echo "This TEST_MODE is invalid!"
		echo "Aborting..."
		exit 1
		;;
	esac
else
	VALUE=$4
fi

echo "-------------------------------------------------------------------------------"
echo "Ready to run $JOB_COUNT x fibonacci_hashtest.py jobs using ENABLE_${TEST_MODE} in $JOB_TYPE."
echo "-------------------------------------------------------------------------------"
echo ""
case $JOB_TYPE in
"serial")
	COUNTER=1
	while [[ $JOB_COUNT -ge $COUNTER ]]
	do
		echo "Spawning job #$COUNTER..."
		compute_fibonacci $COUNTER $VALUE serial_bite_fibonacci-hashtest.j${COUNTER}
		COUNTER=$[$COUNTER+1]
	done
	;;
"parallel")
	echo "System fan may engage if running on a laptop or rack server."
	echo ""
	seq $JOB_COUNT | parallel -j0 "compute_fibonacci ${JOB_COUNT} ${VALUE} p_bite-fibonacci-dump.j{}"
	;;
*)
	echo ""
	echo "*** ERROR ***" echo "$JOB_TYPE is an unsupported job type."
	echo "Aborting..."
	exit 1
	;;
esac
exit 0
