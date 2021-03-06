#!/usr/bin/env python3
#
################################################################################
# Name:         access_jumphost.py
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Created On:   April 14, 2019
# Last Changed: May 31, 2019
# Purpose:	Quick way to SSH into Terraform-built pcluster-jumphosts
################################################################################

# Load some required Python libraries

import argparse
import os
import subprocess
import sys

# Import some external lists and functions.
# Source: jumphostmaker_aux_data.py

from jumphostmaker_aux_data import refer_to_docs_and_quit

# Parse input from the command line.

parser = argparse.ArgumentParser(description='access_jumphost.py: Provide quick SSH access to pcluster-jumphost EC2 instances')

# Configure arguments for the required variables.

parser.add_argument('--instance_name', '-N', help='name of the pcluster-jumphost EC2 instance', required=True)

args = parser.parse_args()
instance_name = args.instance_name

# Perform error checking for the command line arguments.
# If successful, execute the custom SSH access script for this instance family.

if os.path.exists('pcluster_jumphost_data/' + instance_name + '/access_jumphost.' + instance_name + '.sh'):
    cmd_string = 'cd pcluster_jumphost_data/' + instance_name + '/ &&' + 'sh access_jumphost.' + instance_name + '.sh'
    subprocess.run(cmd_string, shell=True)
else:
    error_msg='pcluster-jumphost "' + instance_name + '" does not appear to exist!'
    refer_to_docs_and_quit(error_msg)
    sys.exit(1)
