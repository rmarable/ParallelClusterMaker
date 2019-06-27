################################################################################
# Name:		parallelclustermaker_aux_data.py
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	April 20, 2019
# Last Changed:	June 26, 2019
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

# Function: base_os_instance_check()
# Purpose: Verify the selected EC2 instance_type is supported by base_os

def base_os_instance_check(base_os, instance_type, debug_mode):
    if base_os == 'centos6' and ('t3' or 'm5' or 'a1.' or 'c5.' or 'f1.4xlarge' or 'g3s.xlarge' or 'p3' or 'r5' or 'x1e.' or 'z1d.' or 'h1.' or 'i3.metal' or 'i3en.') in instance_type:
        error_msg = base_os + ' does not support EC2 instance type ' + instance_type + '!'
        refer_to_docs_and_quit(error_msg)
    elif base_os == 'centos7' and ('m5metal.' or 'a1.' or 'p3dn.24xlarge' or 'r5d.24xlarge' or 'r5d.metal' or 'r5.metal' or 'x1e.' or 'h1.' or 'i3en.') in instance_type:
        error_msg = base_os + ' does not support EC2 instance type ' + instance_type + '!'
        refer_to_docs_and_quit(error_msg)
    elif base_os == 'ubuntu1404' and ('t1.' or 't3a.' or 'm5a' or 'm5d.' or 'm5.metal' or 'm1.' or 'a1.' or 'c5n.' or 'c5d.' or 'c1.' or 'f1.4xlarge' or 'p3dn.24xlarge' or 'r5' or 'm2.' or 'z1d.' or 'i3.metal' or 'i3en.') in instance_type:
        error_msg = base_os + ' does not support EC2 instance type ' + instance_type + '!'
        refer_to_docs_and_quit(error_msg)
    elif base_os == 'ubuntu1604' and ('t1.' or 't3a.' or 'm5a' or 'm5d.metal' or 'm5.metal' or 'm1.' or 'a1.' or 'c1.' or 'r5ad.' or 'r5d.24xlarge' or 'r5d.metal' or 'r5.metal' or 'm2.' or 'z1d.metal' or 'i3en.') in instance_type:
        error_msg = base_os + ' does not support EC2 instance type ' + instance_type + '!'
        refer_to_docs_and_quit(error_msg)
    elif base_os == 'ubuntu1804' and ('t1.' or 't3a.' or 'm5ad' or 'm5d.metal' or 'm5.metal' or 'm1.' or 'a1.' or 'c1.' or 'cc2.8xlarge' or 'r5ad.' or 'r5d.24xlarge' or 'm2.' or 'i3en.') in instance_type:
        error_msg = base_os + ' does not support EC2 instance type ' + instance_type + '!'
        refer_to_docs_and_quit(error_msg)
    elif base_os == 'windows2019' and ('a1.' or 'f1.') in instance_type:
        error_msg = base_os + ' does not support EC2 instance type ' + instance_type + '!'
        refer_to_docs_and_quit(error_msg)
    else:
        p_val('base_os', debug_mode)
        p_val('instance_type', debug_mode)

# Function: illegal_az_msg()
# Purpose: Return an error message when an invalid AZ is provided

def illegal_az_msg(az):
    import sys
    print('*** ERROR ***')
    print('"' + az + '"' + ' is not a valid Availability Zone in the selected AWS Region!')
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
    print("*** ERROR ***")
    if r == 'missing_element':
        print('"' + p + '"' + ' seems to be missing as a valid ' + q + '!')
    else:
        print('"' + p + '"' + ' is not a valid option for ' + q + '!')
        print("Supported values:")
        r = '\t'.join(r)
        print('\n'.join(textwrap.wrap(r, 78)))
    print('')
    print("Aborting...")
    sys.exit(1)

# Function: ctrlC_Abort()
# Purpose: Print an abort header, capture CTRL-C when pressed, remove any
# orphaned state and configuration files, and delete any IAM roles and policies
# created by the make-cluster.py script.

def ctrlC_Abort(sleep_time, line_length, vars_file_path, cluster_serial_number_file, cluster_serial_number, enable_fsx_hydration):
    import boto3
    import os
    import sys
    import time
    center_string = '   Please type CTRL-C within ' + str(sleep_time) + ' seconds to abort   '
    iam = boto3.client('iam')
    ec2_iam_policy = 'pclustermaker-policy-' + str(cluster_serial_number)
    ec2_iam_role = 'pclustermaker-role-' + str(cluster_serial_number)
    serverless_ec2_iam_policy = 'kill-pclustermaker-policy-' + str(cluster_serial_number)
    serverless_ec2_iam_role = 'kill-pclustermaker-role-' + str(cluster_serial_number)
    if enable_fsx_hydration == 'true':
        fsx_hydration_iam_policy = 'pclustermaker-fsx-s3-policy-' + str(cluster_serial_number)
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
            print('No orphaned files or directories were found.')
            print('')
        else:
            os.remove(cluster_serial_number_file)
            os.remove(vars_file_path)
            print('')
            print('Removed: ' + cluster_serial_number_file)
            print('Removed: ' + vars_file_path)
        if (cluster_serial_number == 1):
            print('')
            print('No IAM roles or policies exist for this cluster.')
            print('')
        else:
            if enable_fsx_hydration == 'true':
                iam.delete_role_policy(RoleName=ec2_iam_role, PolicyName=fsx_hydration_iam_policy)
                iam.delete_role_policy(RoleName=serverless_ec2_iam_role, PolicyName=fsx_hydration_iam_policy)
                print('Deleted: ' + fsx_hydration_iam_policy)
            iam.delete_role_policy(RoleName=ec2_iam_role, PolicyName=ec2_iam_policy)
            iam.delete_role(RoleName=ec2_iam_role)
            iam.delete_role_policy(RoleName=serverless_ec2_iam_role, PolicyName=serverless_ec2_iam_policy)
            iam.delete_role(RoleName=serverless_ec2_iam_role)
            print('')
            print('Deleted: ' + ec2_iam_policy)
            print('Deleted: ' + ec2_iam_role)
            print('Deleted: ' + serverless_ec2_iam_policy)
            print('Deleted: ' + serverless_ec2_iam_role)
            print('')
        print('Aborting...')
        sys.exit(1)

# Function: print_TextHeader()
# Purpose: Print a centered text header to support validation and reviewing
# of cluster_parameters.

def print_TextHeader(cluster_name, header, line_length):
    print('')
    print(''.center(line_length, '-'))
    T2C = header +' for ' + cluster_name
    print(T2C.center(line_length))
    print(''.center(line_length, '-'))

# Function: refer_to_docs_and_quit()
# Purpose: Print an error message, refer to the AWS ParallelCluster public
# documentation, and quit with a non-successful error code.

def refer_to_docs_and_quit(error_msg):
    import sys
    print('*** ERROR ***')
    print(error_msg)
    print('')
    print('Please refer to the ParallelCluster documentation for more information:')
    print('https://aws-parallelcluster.readthedocs.io/en/latest/index.html')
    print('')
    print('Aborting...')
    sys.exit(1)

############################
# EC2 instance definitions #
############################

# Define a dictionary of default values for the master and compute instances.

default_instance_types = {
    'default_master_instance_type': 'c5.xlarge',
    'default_compute_instance_type': 'c5.xlarge'
}

# General Purpose

ec2_instances_general_purpose = ['a1.medium', 'a1.large', 'a1.xlarge', 'a1.2xlarge', 'a1.4xlarge', 't2.nano', 't2.micro', 't2.small', 't2.medium', 't2.large', 't2.xlarge', 't2.2xlarge', 't3.nano', 't3.micro', 't3.small', 't3.medium', 't3.large', 't3.xlarge', 't3.2xlarge', 't3a.nano', 't3a.micro', 't3a.small', 't3a.medium', 't3a.large', 't3a.xlarge', 't3a.2xlarge', 'm4.large', 'm4.xlarge', 'm4.2xlarge', 'm4.4xlarge', 'm4.10xlarge', 'm4.16xlarge', 'm5.large', 'm5.xlarge', 'm5.2xlarge', 'm5.4xlarge', 'm5.12xlarge', 'm5.24xlarge', 'm5d.large', 'm5d.xlarge', 'm5d.2xlarge', 'm5d.4xlarge', 'm5d.12xlarge', 'm5d.24xlarge', 'm5a.large', 'm5a.xlarge', 'm5a.2xlarge', 'm5a.4xlarge', 'm5a.12xlarge', 'm5a.24xlarge', 'm5ad.large', 'm5ad.xlarge', 'm5ad.2xlarge', 'm5ad.4xlarge', 'm5ad.12xlarge', 'm5ad.24xlarge']

# Compute Optimized

ec2_instances_compute_optimized = ['c4.large', 'c4.xlarge', 'c4.2xlarge', 'c4.4xlarge', 'c4.8xlarge', 'c5.large', 'c5.xlarge', 'c5.2xlarge', 'c5.4xlarge', 'c5.9xlarge', 'c5.18xlarge', 'c5d.large', 'c5d.xlarge', 'c5d.2xlarge', 'c5d.4xlarge', 'c5d.9xlarge', 'c5d.18xlarge', 'c5n.large', 'c5n.xlarge', 'c5n.2xlarge', 'c5n.4xlarge', 'c5n.9xlarge', 'c5n.18xlarge']

# Memory Optimized

ec2_instances_memory_optimized = ['r4.large', 'r4.xlarge', 'r4.2xlarge', 'r4.4xlarge', 'r4.8xlarge', 'r4.16xlarge', 'r5.large', 'r5.xlarge', 'r5.2xlarge', 'r5.4xlarge', 'r5.12xlarge', 'r5.24xlarge', 'r5d.large', 'r5d.xlarge', 'r5d.2xlarge', 'r5d.4xlarge', 'r5d.12xlarge', 'r5d.24xlarge', 'r5a.large', 'r5a.xlarge', 'r5a.2xlarge', 'r5a.4xlarge', 'r5a.12xlarge', 'r5a.24xlarge', 'r5ad.large', 'r5ad.xlarge', 'r5ad.2xlarge', 'r5ad.4xlarge', 'r5ad.12xlarge', 'r5ad.24xlarge', 'x1.16xlarge', 'x1.32xlarge', 'x1e.xlarge', 'x1e.2xlarge', 'x1e.4xlarge', 'x1e.8xlarge', 'x1e.16xlarge', 'x1e.32xlarge', 'u-6tb1.metal', 'u-9tb1.metal', 'u-12tb1.metal', 'z1d.large', 'z1d.xlarge', 'z1d.2xlarge', 'z1d.3xlarge', 'z1d.6xlarge', 'z1d.12xlarge']

# Storage Optimized

ec2_instances_storage_optimized = ['h1.2xlarge', 'h1.4xlarge', 'h1.8xlarge', 'h1.16xlarge', 'd2.xlarge', 'd2.2xlarge', 'd2.4xlarge', 'd2.8xlarge', 'h1.2xlarge', 'h1.4xlarge', 'h1.8xlarge', 'h1.16xlarge', 'i3.large', 'i3.xlarge', 'i3.2xlarge', 'i3.4xlarge', 'i3.8xlarge', 'i3.16xlarge', 'i3.metal', 'i3en.large', 'i3en.xlarge', 'i3en.2xlarge', 'i3en.3xlarge', 'i3en.6xlarge', 'i3en.12xlarge', 'i3en.24xlarge']

# Accelerated Computing - FPGA Instances and GPU Instances

ec2_instances_accelerated_computing = ['f1.2xlarge', 'f1.4xlarge', 'f1.16xlarge', 'g3s.xlarge', 'g3.4xlarge', 'g3.8xlarge', 'g3.16xlarge', 'p2.xlarge', 'p2.8xlarge', 'p2.16xlarge', 'p3.2xlarge', 'p3.8xlarge', 'p3.16xlarge', 'p3dn.24xlarge']

# AWS Batch

ec2_instances_batch = ['optimal']

# Full EC2 instance definitions

ec2_instances_full_list = ec2_instances_general_purpose + ec2_instances_compute_optimized + ec2_instances_memory_optimized + ec2_instances_storage_optimized + ec2_instances_accelerated_computing + ec2_instances_batch

# Elastic File Adapter (EFA)

ec2_instances_efa = ['c5n.18xlarge', 'i3en.24xlarge', 'p3dn.24xlarge']
base_os_efa = ['alinux', 'alinux2', 'centos7', 'ubuntu1604', 'ubuntu1804']
