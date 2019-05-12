################################################################################
# Name:		perf-sbatch.sh
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	May 8, 2019
# Last Changed:	May 11, 2019
# Purpose:	Wrapper for submitting multiple sbatch-Axb_random cluster jobs
################################################################################

#!/bin/sh

for sbatchfile in `ls sbatch-Axb_random.[0-9]* | sort -k2,2n -t.`
do
	sbatch $sbatchfile
done
