#!/usr/bin/env python3
#
################################################################################
# Name:		kill-pcluster.py
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:   April 20, 2019
# Last Changed: May 12, 2019
# Purpose:	Python3 wrapper for deleting custom pcluster stacks
################################################################################

# Load some required Python libraries.

import argparse
import boto3
import contextlib
import errno
import os
import subprocess
import sys
import time
from nested_lookup import nested_lookup

# Import some external lists and functions.
# Source: clustermaker_aux_data.py

from parallelclustermaker_aux_data import p_val
from parallelclustermaker_aux_data import ctrlC_Abort
from parallelclustermaker_aux_data import print_TextHeader

# Parse input from the command line.

parser = argparse.ArgumentParser(description='kill-cluster.py: Command-line tool to destroy ParallelCluster stacks built in AWS')

# Configure arguments for the required variables.

parser.add_argument('--az', '-A', help='AWS Availability Zone (REQUIRED)', required=True)
parser.add_argument('--cluster_name', '-N', help='full name of the cluster (REQUIRED)', required=True)
parser.add_argument('--cluster_owner', '-O', help='username of the cluster owner (REQUIRED)', required=True)

# Configure arguments for the optional variables.
# By default, delete any storage associated with the cluster.

parser.add_argument('--delete_efs', choices=['True', 'true', 'False', 'false'], help='Delete the EFS file system associated with this cluster (default = true)', required=False, default='true')
parser.add_argument('--delete_fsx', choices=['True', 'true', 'False', 'false'], help='Delete the Lustre file system associated with this cluster (default = true)', required=False, default='true')
parser.add_argument('--delete_s3_bucketname', choices=['True', 'true', 'False', 'false'], help='Delete the S3 bucket associated with this cluster (default = true)', required=False, default='true')
parser.add_argument('--cluster_owner_email', '-E', help='email address of the cluster owner (default = UNDEFINED)', required=False, default='UNDEFINED')
parser.add_argument('--ansible_verbosity', '-V', help='Set the Ansible verbosity level (default = none)', required=False, default='')
parser.add_argument('--debug_mode', '-D', choices=['true', 'false'], help='Enable debug mode (default = false)', required=False, default='false')

# Set cluster_parameters to the values provided via command line.

args = parser.parse_args()
ansible_verbosity = args.ansible_verbosity
az = args.az
cluster_name = args.cluster_name
cluster_owner = args.cluster_owner
region = az[:-1]
delete_efs = args.delete_efs
delete_fsx = args.delete_fsx
delete_s3_bucketname = args.delete_s3_bucketname
cluster_owner_email = args.cluster_owner_email
debug_mode = args.debug_mode

# Print a header for cluster variable validation.

if debug_mode == 'true':
    print_TextHeader(cluster_owner + '-' + cluster_name, 'Validating cluster parameter values', 80)

# Perform error checking on the selected AWS Region and Availability Zone.
# Abort if a non-existent Availability Zone was chosen.

try:
    ec2client = boto3.client('ec2', region_name = region)
    az_information = ec2client.describe_availability_zones()
except ValueError:
    print('')
    print('*** ERROR ***')
    print('"' + az + '"' + ' is not a valid Availability Zone in the selected AWS Region.')
    print('Aborting...')
    sys.exit(1)
except (botocore.exceptions.EndpointConnectionError):
    print('')
    print('*** ERROR ***')
    print('"' + az + '"' + ' is not a valid Availability Zone in the selected AWS Region.')
    print('Aborting...')
    sys.exit(1)
else:
    p_val('region', debug_mode)
    p_val('az', debug_mode)

# Preserve the "birth name" of the cluster to maintain compatibility with the
# command line options provided by make-cluster.py ('-N' and '-O').
# Warn the opertor if the cluster does not exist in this region but proceed
# with removing any artifacts left over from previous stacks.

cluster_birth_name = cluster_name
cluster_name = cluster_owner + '-' + cluster_name
cluster_destroy_command = ' '.join(sys.argv)
status_cmd_string = 'pcluster status --region ' + region + ' ' + cluster_name

with open(os.devnull, 'w') as devnull:
    p = subprocess.run(status_cmd_string, shell=True, stdout=devnull)
    if p.returncode == 1:
        print('')
        print('*** WARNING ***')
        print('Cluster stack "' + cluster_name + '" was not found in ' + region + '!')
        print('Continuing with stack artifact destruction...')
    else:
        p_val(cluster_name, debug_mode)

# Define vars_file and cluster_serial_number_file.
# Abort if either of these files are missing.

SERIAL_DIR = './active_pclusters'
VARS_FILE_DIR = './vars_files'
cluster_serial_number_file = SERIAL_DIR + '/' + cluster_name + '.serial'
vars_file_path = VARS_FILE_DIR + '/' + cluster_name + '.yml'

if os.path.isfile(cluster_serial_number_file):
    p_val('cluster_serial_number_file', debug_mode)
else:
    print('')
    print('*** ERROR ***')
    print('Missing cluster_serial_number_file: ' + cluster_serial_number_file)
    print('Aborting...')
    sys.exit(1)

if os.path.isfile(vars_file_path):
    p_val('vars_file_path', debug_mode)
else:
    print('')
    print('*** ERROR ***')
    print('Missing vars_file_path: ' + vars_file_path)
    print('Aborting...')
    sys.exit(1)

# Parse cluster_serial_number from cluser_serial_number_file.
# Strip any trailing newlines that would otherwise break destory_cmd_string.

cluster_serial_number = open(cluster_serial_number_file).readline().rstrip("\n")

# Parse the Python3 interpreter path to ensure ParallelCluster stacks can be
# created from either OSX or an EC2 jumphost.

python3_path = subprocess.run(['which','python3'], stdout=subprocess.PIPE).stdout.decode('utf8').rstrip()

# Increase Ansible verbosity when debug_mode is enabled.

if debug_mode == 'true':
    ansible_verbosity = '-vvv'

# Generate the command string that will delete the cluster stack.

destroy_cmd_string = 'ansible-playbook --extra-vars ' + '"' + 'cluster_name=' + cluster_name + ' cluster_birth_name=' + cluster_birth_name + ' cluster_serial_number=' + cluster_serial_number + ' delete_s3_bucketname=' + delete_s3_bucketname + ' delete_efs=' + delete_efs + ' delete_fsx=' + delete_fsx + ' ansible_python_interpreter=' + python3_path + '"' + ' delete_pcluster.yml ' + ansible_verbosity

# Print the cluster destroy commands to the console.

print('')
if ansible_verbosity:
    print('Setting Ansible verbosity to "' + ansible_verbosity + '"')
    print('')
print('Ready to execute:')
print('$ ' + cluster_destroy_command)
print('')
print('Preparing to delete cluster "' + cluster_name + '" using this command:')
print('$ ' + destroy_cmd_string)

# Exit the script if the operator types 'CTRL-C' within 5 seconds after the
# abort header is displayed.

ctrlC_Abort(5, 80, 1, 1)

# Delete the cluster stack using the delete_pcluster Ansible playbook.

subprocess.run(destroy_cmd_string, shell=True)

# Delete cluster_serial_number_file and vars_file_path.

line_length = 80
print(''.center(line_length, '='))
with contextlib.suppress(FileNotFoundError):
    os.remove(cluster_serial_number_file)
    print('Removed  ===> ' + cluster_serial_number_file)
    os.remove(vars_file_path)
    print('Removed  ===> ' + vars_file_path)

# Print a friendly banner to the console and include the command used to
# spawn the cluster stack.

line_length = 80
print(''.center(line_length, '='))
with contextlib.suppress(FileNotFoundError):
    with open(cluster_serial_number_file, 'r') as cli_input:
        print('To rebuild the cluster:')
        print('$ ' + cli_input.readlines()[-1])
        print(''.center(line_length, '='))

# Messaging to cluster_owner_email announcing the destruction of cluster_name
# is currently handled by delete_pcluster.yml but this may be revisited in a
# future release.

# Cleanup and exit.

print('')
print('Finished deleting cluster stack ' + cluster_name + '!')
print('Exiting...')
sys.exit(0)
