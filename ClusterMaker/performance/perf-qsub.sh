################################################################################
# Name:		perf-qsub.sh
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	February 14, 2018
# Last Changed:	May 11, 2019
# Purpose:	Wrapper for submitting multiple qsub-Axb_random cluster jobs
################################################################################

#!/bin/sh

for qsubfile in `ls qsub-Axb_random.[0-9]* | sort -k2,2n -t.`
do
	qsub $qsubfile
done
