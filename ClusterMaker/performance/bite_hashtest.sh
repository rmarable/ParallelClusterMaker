################################################################################
# Name:		bite_hashtest.sh
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	May 4, 2018
# Last Changed:	August 8, 2018
# Purpose:	Run multiple hashtest.py jobs in parallel or serial mode on
#		standalone EC2 instances 
#
# Usage:
# $ ./bite_hashtest.sh  [ JOB_TYPE ]  [ JOB_COUNT ]
#
# Example:
# $ ./bite_hashtest.sh parallel 4 
# $ ./bite_hashtest.sh ==> follow the prompts
################################################################################
#
#!/bin/bash

# Function: compute_hash(COUNT, OUTFILE, SIZE)
# Purpose: invoke hashtest.py with appropriate arguments.

compute_hash() 
{

COUNT=$1
OUTFILE=$2
SIZE=$3

echo "Making $COUNT hashes with input_size  = $SIZE bytes @ `date`"
$PYTHON3 ./hashtest.py -c $COUNT -S $SIZE -O $OUTFILE -C pigz
echo "Finished $COUNT hashes of input_size = $SIZE bytes @ `date`"
echo ""
}
export -f compute_hash

# Set the correct version of PYTHON3.

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

# Set the JOB_TYPE.

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

# Set the JOB_COUNT.

if [ -z $2 ]
then
	echo ""
	read -p 'Enter the number of hashtest.py jobs to run: ' JOB_COUNT
else
	JOB_COUNT=$2
fi
echo "Setting job_count = $JOB_COUNT"

# Set the ARRAY_TYPE.

if [ -z $3 ]
then
	echo ""
	echo "Choose an array_type of byte sizes to test against:"
	echo "	small  ==>  { 2, 4, 8, 16, 32, 64, 128, 256, 512 }"
	echo "	large  ==>  { 1024, 2048, 4096, 8192, 16384, 32768 }"
	echo "	jumbo  ==>  { 65536, 131072, 262144, 524288, 1048576 }"
	echo ""
	read -p 'Select the byte size array_type to run: ' ARRAY_TYPE
	if [ -z $ARRAY_TYPE ]
	then
		ARRAY_TYPE="small"
	fi
	case $ARRAY_TYPE in
	"small")
		declare -a BYTE_SIZE=(2 4 8 16 32 64 128 256 512)
	;;
	"large")
		declare -a BYTE_SIZE=(1024 2048 4096 8192 16384 32768)
	;;
	"jumbo")
		declare -a BYTE_SIZE=(65536 131072 262144 524288 1048576)
	;;
	*)
		echo "*** ERROR ***"
		echo "Invalid ARRAY_TYPE selected!"
		echo "Aborting..."
		exit 1
	esac
fi
 
# Set the JOB_TYPE.

echo "-------------------------------------------------------------------------------"
echo "Ready to run $JOB_COUNT hashtest.py jobs in $JOB_TYPE using the $ARRAY_TYPE array."
echo "-------------------------------------------------------------------------------"
echo ""
COUNTER=1
while [[ $JOB_COUNT -ge $COUNTER ]]
do
	echo "Spawning job $COUNTER..."
	for ELEMENT in "${BYTE_SIZE[@]}"
	do
		if [[ $JOB_TYPE == "serial" ]]
		then
			compute_hash $JOB_COUNT serial_bite-hashtest.job${JOB_COUNT}.${ELEMENT}bytes $ELEMENT
		elif [[ $JOB_TYPE == "parallel" ]]
		then
			seq $JOB_COUNT | parallel -j0 "compute_hash $JOB_COUNT p_bite-hashtest.job{}.${ELEMENT}bytes $ELEMENT"
		else
			echo ""
			echo "*** ERROR ***"
			echo "$JOB_TYPE is an unsupported job type."
			echo "Aborting..."
			exit 1
		fi
	done
	COUNTER=$[$COUNTER+1]
done

# Cleanup and exit.

echo "Finished making $[$COUNTER-1] hashes using the $ARRAY_TYPE array_type."
echo "Exiting..."
exit 0
