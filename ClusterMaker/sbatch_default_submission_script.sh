#!/bin/bash
#
################################################################################
# Name:         sbatch_default_submission_script.sh
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Created On:   May 8, 2019
# Last Changed: May 8, 2019
# Deployed On:  {{ lookup('pipe','date \"+%B %-d, %Y\"') }}
# Purpose:      Default sbatch submission script for Slurm-style clusters
################################################################################

#########
# Notes #
#########
#
# Additional guidance is available from:
#
# https://slurm.schedmd.com/sbatch.html

# Set the name of the cluster job.
#SBATCH --job-name=my_default_slurm_job

# Set the maximum execution time to 2 hours.
# Format: D-HH:MM
#SBATCH --time=02:00

# Write job standard output to this file.
# Uncomment the second option to include the jobID.
# Uncomment the third option to include the jobID and taskID (default).
# To write stdout and stderr to the same file, leave the next stanza alone.
##SBATCH --output slurm_default_slurm_job.out
##SBATCH --output slurm_default_slurm_job.job%j.out
#SBATCH --output slurm_default_slurm_job.job%j.task%t.out

# Write job standard error to this file.
# Uncomment the second option to include the jobID.
# Uncomment the third option to include the jobID and taskID.
# To write stdout and stderr to the same file, leave this stanza ALONE.
##SBATCH --error slurm_default_slurm_job.err
##SBATCH --error slurm_default_slurm_job.job%j.err
#SBATCH --error slurm_default_slurm_job.job%j.task%t.err

# Select the number of compute cores.
# Todo - test this!
{% if hyperthreading %}# Hyperthreading is enabled.
{% if compute_instance_type.split('.')[-1] == "large" %}--ntasks=2
{% elif compute_instance_type.split('.')[-1] == "xlarge" %}--ntasks=4
{% elif compute_instance_type.split('.')[-1] == "2xlarge" %}--ntasks=8
{% elif compute_instance_type.split('.')[-1] == "4xlarge" %}--ntasks=16
{% elif compute_instance_type.split('.')[-1] == "9xlarge" %}--ntasks=36
{% elif compute_instance_type.split('.')[-1] == "12xlarge" %}--ntasks=48
{% elif compute_instance_type.split('.')[-1] == "18xlarge" %}--ntasks=72
{% elif compute_instance_type.split('.')[-1] == "24xlarge" %}--ntasks=96
{% else %}#--ntasks=4
{% endif %}
{% else %}# Hyperthreading is disabled.
{% if compute_instance_type.split('.')[-1] == "large" %}--ntasks=1
{% elif compute_instance_type.split('.')[-1] == "xlarge" %}--ntasks=2
{% elif compute_instance_type.split('.')[-1] == "2xlarge" %}--ntasks=4
{% elif compute_instance_type.split('.')[-1] == "4xlarge" %}--ntasks=8
{% elif compute_instance_type.split('.')[-1] == "9xlarge" %}--ntasks=18
{% elif compute_instance_type.split('.')[-1] == "12xlarge" %}--ntasks=24
{% elif compute_instance_type.split('.')[-1] == "18xlarge" %}--ntasks=36
{% elif compute_instance_type.split('.')[-1] == "24xlarge" %}--ntasks=48
{% else %}#--ntasks=2
{% endif %}
{% endif %}

# Reserve N CPUs per task.
#SBATCH --cpus-per-task=1

# Reserve 250MB for the job.
# *DON'T* use this with --mem-per-cpu!
##SBATCH --mem=250mb

# Reserve 250MB per core.
##SBATCH --mem-per-cpu=250mb

# Use N machines to tun the job.
##SBATCH --nodes=4

# Run N job per machine.
##SBATCH --ntasks-per-node=8

# Ensure that all cores are on the same machine.
##SBATCH -N 1                

# Reserve the host exclusively for this job.
## SBATCH --exclusive

# Run N tasks in total
##SBATCH --ntasks=32

# Run as an array job with SLURM_ARRAY_TASK_ID ranging from 1-24.
##SBATCH --array=1-24

# Use this email address for Slurm notifications.
##SBATCH --mail-user=rodney.marable@gmail.com

# Notify the mail-user when certain events occur.
# Valid types = NONE, BEGIN, END, FAIL, REQUEUE, ALL (equivalent to BEGIN,
# END, FAIL, REQUEUE, and STAGE_OUT), STAGE_OUT (burst buffer stage out and
# teardown completed), TIME_LIMIT, TIME_LIMIT_90 (reached 90 percent of time
# limit), TIME_LIMIT_80 (reached 80 percent of time limit), TIME_LIMIT_50
# (reached 50 percent of time limit) and ARRAY_TASKS (send emails for each
# array task).
##SBATCH --mail-type=END

##########################################################################
##########################################################################
###                      Start the script here                         ###
##########################################################################
##########################################################################

echo "`hostname` ran this job on `date`."

echo "`hostname` is running `python3 --version`"

exit 0
