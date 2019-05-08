################################################################################
# Name:		generate_custom_sbatch_templates.sh
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	May 8, 2019
# Last Changed:	May 8, 2019
# Purpose:	Generate custom sbatch job array submission scripts
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

# Generate the custom sbatch scripts.

for SBATCH_TEMPLATE_INPUT in Axb_random hashtest fibonacci_hashtest
do
	FINAL=$(( $TOTAL_TESTS * $STEP_SIZE ))
	JOBCOUNT=$STARTING_TEST_NUMBER
	while [ $JOBCOUNT -le $FINAL ]
	do
		SBATCH_TEMPLATE_OUTPUT="sbatch-${SBATCH_TEMPLATE_INPUT}.$JOBCOUNT.j2"
		if [ ! -f $PERFORMANCE_TEMPLATE_DIR/$SBATCH_TEMPLATE_OUTPUT ]
		then
			cat $PERFORMANCE_TEMPLATE_DIR/sbatch_${SBATCH_TEMPLATE_INPUT}_template.j2 | sed -e "s/JOBCOUNT/$JOBCOUNT/g" > $PERFORMANCE_TEMPLATE_DIR/$SBATCH_TEMPLATE_OUTPUT
			echo "Generating $SBATCH_TEMPLATE_OUTPUT..."
		else
			echo "Found an existing $SBATCH_TEMPLATE_OUTPUT..."
		fi
		JOBCOUNT=$(( $JOBCOUNT + $STEP_SIZE ))
	done
done

# Cleanup and exit.

echo "Finished generating the custom sbatch performance scripts!"
echo "Exiting..."
exit 0
