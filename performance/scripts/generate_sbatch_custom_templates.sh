#!/bin/bash
set -euo pipefail
################################################################################
# Name:		generate_custom_sbatch_templates.sh
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	May 8, 2019
# Last Changed:	May 8, 2019
# Purpose:	Generate custom sbatch job array submission scripts
################################################################################

# Set the directory where output templates will be saved.

PERFORMANCE_TEMPLATE_DIR="$(cd "$(dirname "$0")/.." && pwd)/jinja2"
PERF_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# If no input is provided from the command line, run a total of 10 tests with
# starting_task_number=10, ending_task_number=100, and step_size=10.

STARTING_TEST_NUMBER=${1:-10}
STEP_SIZE=${2:-10}
TOTAL_TESTS=${3:-10}

_int_check() {
    [[ "$1" =~ ^[0-9]+$ ]] || { echo "ERROR: '$1' is not a positive integer (arg: $2)" >&2; exit 1; }
    [[ "$1" -gt 0 ]] || { echo "ERROR: $2 must be > 0 (got $1)" >&2; exit 1; }
}
_int_check "$STARTING_TEST_NUMBER" STARTING_TEST_NUMBER
_int_check "$STEP_SIZE"            STEP_SIZE
_int_check "$TOTAL_TESTS"          TOTAL_TESTS

# Generate the custom sbatch scripts.

for SBATCH_TEMPLATE_INPUT in Axb_random
do
	FINAL=$(( $STARTING_TEST_NUMBER + ($TOTAL_TESTS - 1) * $STEP_SIZE ))
	JOBCOUNT=$STARTING_TEST_NUMBER
	while [ $JOBCOUNT -le $FINAL ]
	do
		SBATCH_TEMPLATE_OUTPUT="sbatch-${SBATCH_TEMPLATE_INPUT}.$JOBCOUNT.sh.j2"
		if [ ! -f "$PERFORMANCE_TEMPLATE_DIR/$SBATCH_TEMPLATE_OUTPUT" ]
		then
			sed -e "s/JOBCOUNT/$JOBCOUNT/g" "$PERFORMANCE_TEMPLATE_DIR/sbatch_${SBATCH_TEMPLATE_INPUT}_template.j2" > "$PERFORMANCE_TEMPLATE_DIR/$SBATCH_TEMPLATE_OUTPUT"
			echo "Generating $SBATCH_TEMPLATE_OUTPUT..."
		else
			echo "Found an existing $SBATCH_TEMPLATE_OUTPUT..."
		fi
		sed -e "s/JOBCOUNT/$JOBCOUNT/g" "$PERFORMANCE_TEMPLATE_DIR/sbatch_${SBATCH_TEMPLATE_INPUT}_template.j2" > "$PERF_DIR/sbatch-${SBATCH_TEMPLATE_INPUT}.${JOBCOUNT}.sh"
		chmod +x "$PERF_DIR/sbatch-${SBATCH_TEMPLATE_INPUT}.${JOBCOUNT}.sh"
		JOBCOUNT=$(( $JOBCOUNT + $STEP_SIZE ))
	done
done

# Cleanup and exit.

echo "Finished generating the custom sbatch performance scripts!"
echo "Exiting..."
exit 0
