################################################################################
# Name:		parallelclustermaker_aux_data.py
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	April 20, 2019
# Last Changed:	May 9, 2019
# Purpose:	External data structures and functions for ParallelClusterMaker
################################################################################

########################
# Function definitions #
########################

# Function: S3Prefix()
# Purpose: Create a prefix for S3 buckets using a random ASCII string

def S3Prefix(size):
    import random
    import string
    chars = string.ascii_uppercase + string.digits
    return str(''.join(random.choice(chars) for i in range(size)))

# Function: illegal_az_msg()
# Purpose: Return an error message when an invalid AZ is provided

def illegal_az_msg(az):
    import sys
    print('*** ERROR ***')
    print('"' + az + '"' + ' is not a valid Availability Zone in the selected AWS Region.')
    print('Aborting...')
    sys.exit(1)

# Function: is_number()
# Purpose: Check for numeric values

def is_number(s):
    try:
        float(s)
        return True
    except ValueError:
        return False

# Function: p_val()
# Purpose: Print a successful cluster_parameter validation message to stdout

def p_val(p, debug_mode):
    if debug_mode == 'True' or debug_mode == 'true':
        print(p + " successfully validated")
    else:
        pass

# Function: p_fail()
# Purpose: Print a failed cluster_parameter validation message to stdout

def p_fail(p, q, r):
    import sys
    import textwrap
    print('')
    print("*** Error ***")
    if r == 'missing_element':
        print('"' + p + '"' + ' seems to be missing as a valid ' + q + '.')
    else:
        print('"' + p + '"' + ' is not a valid option for ' + q + '.')
        print("Supported values:")
        r = '\t'.join(r)
        print('\n'.join(textwrap.wrap(r, 78)))
    print('')
    print("Aborting...")
    sys.exit(1)

# Function: ctrlC_Abort()
# Purpose: Print an abort header, capture CTRL-C when pressed, and remove any
# orphaned state and configuration files created by the make-cluster.py script.

def ctrlC_Abort(sleep_time, line_length, vars_file_path, cluster_serial_number_file):
    import os
    import sys
    import time
    center_string = '   Please type CTRL-C within ' + str(sleep_time) + ' seconds to abort   '
    print('')
    print(''.center(line_length, '#'))
    print(center_string.center(line_length, '#'))
    print(''.center(line_length, '#'))
    print('')
    try:
        time.sleep(sleep_time)
    except KeyboardInterrupt:
        if (vars_file_path == 1) and (cluster_serial_number_file == 1):
            print('')
        else:
            os.remove(cluster_serial_number_file)
            os.remove(vars_file_path)
            print('')
            print('Removed: ' + cluster_serial_number_file)
            print('Removed: ' + vars_file_path)
            print('')
        print('Aborting...')
        sys.exit(1)

# Function print_TextHeader()
# Purpose: Print a centered text header to support validation and reviewing
# of cluster_parameters.

def print_TextHeader(p, action, line_length):
    print('')
    print(''.center(line_length, '-'))
    T2C = action + ' parameter values for ' + p
    print(T2C.center(line_length))
    print(''.center(line_length, '-'))

############################
# EC2 instance definitions #
############################

# Define a dictionary of default values for the master and compute instances.

default_instance_types = {
    'default_master_instance_type': 'c5.xlarge',
    'default_compute_instance_type': 'c5.xlarge'
}

# General Purpose

ec2_instances_general_purpose = ['a1.medium', 'a1.large', 'a1.xlarge', 'a1.2xlarge', 'a1.4xlarge', 't2.nano', 't2.micro', 't2.small', 't2.medium', 't2.large', 't2.xlarge', 't2.2xlarge', 't3.nano', 't3.micro', 't3.small', 't3.medium', 't3.large', 't3.xlarge', 't3.2xlarge', 'm4.large', 'm4.xlarge', 'm4.2xlarge', 'm4.4xlarge', 'm4.10xlarge', 'm4.16xlarge', 'm5.large', 'm5.xlarge', 'm5.2xlarge', 'm5.4xlarge', 'm5.12xlarge', 'm5.24xlarge', 'm5d.large', 'm5d.xlarge', 'm5d.2xlarge', 'm5d.4xlarge', 'm5d.12xlarge', 'm5d.24xlarge', 'm5a.large', 'm5a.xlarge', 'm5a.2xlarge', 'm5a.4xlarge', 'm5a.12xlarge', 'm5a.24xlarge']

# Compute Optimized

ec2_instances_compute_optimized = ['c4.large', 'c4.xlarge', 'c4.2xlarge', 'c4.4xlarge', 'c4.8xlarge', 'c5.large', 'c5.xlarge', 'c5.2xlarge', 'c5.4xlarge', 'c5.9xlarge', 'c5.18xlarge', 'c5d.large', 'c5d.xlarge', 'c5d.2xlarge', 'c5d.4xlarge', 'c5d.9xlarge', 'c5d.18xlarge', 'c5a.large', 'c5a.xlarge', 'c5a.2xlarge', 'c5a.4xlarge', 'c5a.9xlarge', 'c5a.18xlarge', 'c5n.large', 'c5n.xlarge', 'c5n.2xlarge', 'c5n.4xlarge', 'c5n.9xlarge', 'c5n.18xlarge']

# Memory Optimized

ec2_instances_memory_optimized = ['r4.large', 'r4.xlarge', 'r4.2xlarge', 'r4.4xlarge', 'r4.8xlarge', 'r4.16xlarge', 'r5.large', 'r5.xlarge', 'r5.2xlarge', 'r5.4xlarge', 'r5.12xlarge', 'r5.24xlarge', 'r5d.large', 'r5d.xlarge', 'r5d.2xlarge', 'r5d.4xlarge', 'r5d.12xlarge', 'r5d.24xlarge', 'r5a.large', 'r5a.xlarge', 'r5a.2xlarge', 'r5a.4xlarge', 'r5a.12xlarge', 'r5a.24xlarge', 'x1.16xlarge', 'x1.32xlarge', 'x1e.xlarge', 'x1e.2xlarge', 'x1e.4xlarge', 'x1e.8xlarge', 'x1e.16xlarge', 'x1e.32xlarge', 'u-6tb1.metal', 'u-9tb1.metal', 'u-12tb1.metal', 'z1d.large', 'z1d.xlarge', 'z1d.2xlarge', 'z1d.3xlarge', 'z1d.6xlarge', 'z1d.12xlarge']

# Storage Optimized

ec2_instances_storage_optimized = ['h1.2xlarge', 'h1.4xlarge', 'h1.8xlarge', 'h1.16xlarge', 'd2.xlarge', 'd2.2xlarge', 'd2.4xlarge', 'd2.8xlarge', 'h1.2xlarge', 'h1.4xlarge', 'h1.8xlarge', 'h1.16xlarge', 'i3.large', 'i3.xlarge', 'i3.2xlarge', 'i3.4xlarge', 'i3.8xlarge', 'i3.16xlarge', 'i3.metal']

# Accelerated Computing

ec2_instances_accelerated_computing = ['f1.2xlarge', 'f1.4xlarge', 'f1.16xlarge', 'g3s.xlarge', 'g3.4xlarge', 'g3.8xlarge', 'g3.16xlarge', 'p2.xlarge', 'p2.8xlarge', 'p2.16xlarge', 'p3.2xlarge', 'p3.8xlarge', 'p3.16xlarge', 'p3dn.24xlarge']

# AWS Batch

ec2_instances_batch = ['optimal']

# Full EC2 instance definitions

ec2_instances_full_list = ec2_instances_general_purpose + ec2_instances_compute_optimized + ec2_instances_memory_optimized + ec2_instances_storage_optimized + ec2_instances_accelerated_computing + ec2_instances_batch
