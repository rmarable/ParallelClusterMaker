#!/usr/bin/env python3
#
# NOTE - centos7 users may need to modify the shebang above to "python36"
# Todo - explore a template for this
#
################################################################################
# Name:         access_cluster.py
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Created On:   April 20, 2019
# Last Changed: April 30, 2019
# Purpose:	Provide a mechanism for SSH-ing into pcluster master instances
# Usage:	$ ./access_cluster.py --help
################################################################################

# Load some required Python libraries

import argparse
import subprocess

# Import some external lists.
# Source: clustermaker_aux_data.py

from clustermaker_aux_data import p_fail

# Parse input from the command line.

parser = argparse.ArgumentParser(description='access_cluster.py: Provide quick SSH access to CFNCluster head nodes')

# Configure arguments for the required variables.

parser.add_argument('--cluster_name', '-N', help='full name of the cluster (example: rmarable-stage02)', required=True)
parser.add_argument('--prod_level', '-P', choices=['dev', 'test', 'stage', 'prod'], help='operating level of the cluster (default = dev)', required=False, default='dev')

args = parser.parse_args()
cluster_name = args.cluster_name
prod_level = args.prod_level

cmd_string = 'python3 cluster_data/' + prod_level + '/' + cluster_name + '/access_cluster.' + cluster_name + '.py'
subprocess.run(cmd_string, shell=True)
