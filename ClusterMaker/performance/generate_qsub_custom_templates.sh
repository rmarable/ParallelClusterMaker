################################################################################
# Name:		generate_custom_qsub_templates.sh
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	April 21, 2018
# Last Changed:	January 15, 2019
# Purpose:	Generate custom job array submission scripts
################################################################################
#
#!/bin/bash

# Set the directory where output templates will be saved.

PERFORMANCE_TEMPLATE_DIR=`pwd`/jinja2

# If no input is provided from the command line, run a total of 10 tests with
# starting_task_number=10, ending_task_number=100, and step_size=10.

STARTING_TEST_NUMBER=$1
if [ -z $1 ]
then
	STARTING_TEST_NUMBER=10
fi
STEP_SIZE=$2
if [ -z $2 ]
then
	STEP_SIZE=10
fi
TOTAL_TESTS=$3
if [ -z $3 ]
then
	TOTAL_TESTS=10
fi

# Generate the custom qsub scripts.

for QSUB_TEMPLATE_INPUT in Axb_random hashtest fibonacci_hashtest
do
	FINAL=$(( $TOTAL_TESTS * $STEP_SIZE ))
	JOBCOUNT=$STARTING_TEST_NUMBER
	while [ $JOBCOUNT -le $FINAL ]
	do
		QSUB_TEMPLATE_OUTPUT="qsub-${QSUB_TEMPLATE_INPUT}.$JOBCOUNT.j2"
		if [ ! -f $PERFORMANCE_TEMPLATE_DIR/$QSUB_TEMPLATE_OUTPUT ]
		then
			cat $PERFORMANCE_TEMPLATE_DIR/qsub_${QSUB_TEMPLATE_INPUT}_template.j2 | sed -e "s/JOBCOUNT/$JOBCOUNT/g" > $PERFORMANCE_TEMPLATE_DIR/$QSUB_TEMPLATE_OUTPUT
			echo "Generating $QSUB_TEMPLATE_OUTPUT..."
		else
			echo "Found an existing $QSUB_TEMPLATE_OUTPUT..."
		fi
		JOBCOUNT=$(( $JOBCOUNT + $STEP_SIZE ))
	done
done

# Cleanup and exit.

echo "Finished generating the custom qsub performance scripts!"
echo "Exiting..."
exit 0
