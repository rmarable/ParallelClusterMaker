#!/bin/bash
#
################################################################################
# Name:		qsub_default_submission_script.sh
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	May 2, 2019
# Last Changed:	May 2, 2019
# Purpose:	Default qsub submission script for Grid Engine-style clusters
################################################################################

# Set the name of the cluster job.
#$ -N my_cluster_job

# Set the shell (default = bash) to use when running the job,
#$ -S /bin/bash

# Set the maximum job execution time to 8 hours.
#$ -l h_rt=08:00:00

# Request 2 GB of memory for the job (use integers).
#$ -l mem=2G

# Select a Grid Engine parallel environment and request N slots for each job
# submitted to the cluster (uncomment to enable).
# Valid options: mpi, make, smp
##$ -pe smp 4

# Use the current directory as the working directory - this is where stdout
# and stderror will be written (default).
# Uncomment the second option to use a custom working directory.
# If both options are commented out, your home directory will be used instead.
#$ -cwd
##$ -wd /fsx/scratch

# Merge standard error and output into a single file (default).
# Comment this out if using a specific location for stdout and stderr.
#$ -j yes

# Specify the standard output and error file names (uncomment to enable).
##$ -o my_cluster_job.output
##$ -e my_cluster_job.stderr

# Send an email when the job is submitted (b), completed (e), suspended (s), or
# aborted (a) to  the listed address (uncomment to enable).
##$ -m be
##$ -M your_email_address@yourdomain.com

# Submit as an array job N times (uncomment to enable).
##$ -t 1-10

##########################################################################
##########################################################################
###                Paste your code below this comment                  ###
##########################################################################
##########################################################################

echo "`hostname` ran this job on `date`."

echo "`hostname` is running `python3 --version`"

exit 0
