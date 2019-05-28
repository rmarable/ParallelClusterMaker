#!/usr/bin/env python3
#
################################################################################
# Name:		make-pcluster.py
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	April 20, 2019
# Last Changed:	May 26, 2019
# Purpose:	Python3 wrapper for customizing ParallelCluster stacks
################################################################################

# Load the required Python libraries.

import argparse
import boto3
import botocore
import errno
import os
import subprocess
import sys
import time
from botocore.exceptions import ClientError
from datetime import datetime as DateTime
from nested_lookup import nested_lookup
from requests.exceptions import ConnectionError
from validate_email import validate_email

# Import the list of supported EC2 instances and some external functions.
# Source: parallelparallelclustermaker_aux_data.py

from parallelclustermaker_aux_data import ctrlC_Abort
from parallelclustermaker_aux_data import default_instance_types
from parallelclustermaker_aux_data import ec2_instances_full_list
from parallelclustermaker_aux_data import illegal_az_msg
from parallelclustermaker_aux_data import is_number
from parallelclustermaker_aux_data import p_fail
from parallelclustermaker_aux_data import p_val
from parallelclustermaker_aux_data import print_TextHeader
from parallelclustermaker_aux_data import refer_to_docs_and_quit
from parallelclustermaker_aux_data import S3Prefix

# Parse input from the command line.

parser = argparse.ArgumentParser(description='make-pcluster.py: Command-line interface to build custom ParallelCluster stacks in AWS')

# Configure parser arguments for the required variables.

parser.add_argument('--az', '--AvailabilityZone', '-A', help='AWS Availability Zone (REQUIRED)', required=True)
parser.add_argument('--cluster_name', '-N', help='name of the cluster (REQUIRED)', required=True)
parser.add_argument('--cluster_owner', '-O', help='username of the cluster owner (REQUIRED)', required=True)
parser.add_argument('--cluster_owner_email', '-E', help='email address of the cluster owner (REQUIRED)', required=True)

# Configure arguments for the optional variables.
# Set reasonable defaults for anything not explicitly defined.

parser.add_argument('--ansible_verbosity', help='Set the Ansible verbosity level (default = none)', required=False, default='')
parser.add_argument('--base_os', choices=['alinux', 'centos6', 'centos7', 'ubuntu1404', 'ubuntu1604'], help='cluster base operating system (default = alinux a.k.a. Amazon Linux)', required=False, default='alinux')
parser.add_argument('--cluster_lifetime', help='automatically terminate the cluster after this time period has elapsed in days:hours:minutes format (default = 30:0:0, i.e. one month)', required=False, default='30:0:0')
parser.add_argument('--cluster_owner_department', choices=['analytics', 'clinical', 'commercial', 'compbio', 'compchem', 'datasci', 'design', 'development', 'hpc', 'imaging', 'manufacturing', 'medical', 'modeling', 'operations', 'proteomics', 'robotics', 'qa', 'research', 'scicomp'], help='department of the cluster_owner (default = hpc)', required=False, default='hpc')
parser.add_argument('--cluster_type', choices=['ondemand', 'spot'], help='build the cluster with ondemand or spot instances (default = spot)', required=False, default='spot')
parser.add_argument('--compute_instance_type', help='compute EC2 instance type (default = c5.xlarge)', required=False, default='c5.xlarge')
parser.add_argument('--compute_root_volume_size', help='compute EBS root volume size in GB (default = 250)', required=False, default=250)
parser.add_argument('--custom_ami', help='ID of a Custom AMI to use instead of default published AMIs - a valid base_os is still required.', required=False, default='NONE')
parser.add_argument('--debug_mode', '-D', choices=['true', 'false'], help='Enable debug mode (default = false)', required=False, default='false')
parser.add_argument('--desired_vcpus', help='initial number of vcpus to deploy when using Batch (default = 4)', required=False, default=4)
parser.add_argument('--ebs_encryption', choices=['true', 'false'], help='enable EBS encryption (default = false)', required=False, default='false')
parser.add_argument('--ebs_shared_dir', help='shared EBS file system path (default = /shared)', required=False, default='/shared')
parser.add_argument('--ebs_shared_volume_size', help='EBS shared volume size in GB (default = 250)', required=False, default=250)
parser.add_argument('--ebs_shared_volume_type', choices=['gp2', 'io1', 'st1'], help='EBS volume type (default = gp2)', required=False, default='gp2')
parser.add_argument('--efs_encryption', choices=['true', 'false'], help='enable EFS encryption in transit (default = false)', required=False, default='false')
parser.add_argument('--efs_performance_mode', choices=['generalPurpose', 'maxIO'], help='select the EFS performance mode (default = generalPurpose)', required=False, default='generalPurpose')
parser.add_argument('--enable_efs', help='enable Elastic File System (EFS) support (default = false)', required=False, default='false')
parser.add_argument('--enable_external_nfs', choices=['true', 'false'], help='enable support for external NFS file system mounts (default = false)', required=False, default='false')
parser.add_argument('--enable_fsx', choices=['true', 'false'], help='enable Amazon FSx for Lustre support (default = false)', required=False, default='false')
parser.add_argument('--enable_ganglia', choices=['true', 'false'], help='enable Ganglia on the master instance', required=False, default='false')
parser.add_argument('--enable_hpc_performance_tests', choices=['true', 'false'], help='enable the HPC performance tests Axb_random, hashtest, and hashtest_fibonacci under the ec2_user account on the master instance (default = true)', required=False, default='false')
parser.add_argument('--enable_sge_pe', choices=['true', 'false'], help='enable Grid Engine parallel environments (default = true)', required=False, default='true')
parser.add_argument('--external_nfs_server', help='set the hostname of the external NFS file system (default = NULL)', required=False, default='')
parser.add_argument('--enable_fsx_hydration', choices=['true', 'false'], help='enable support for hydrating FSxL from S3 (default = false)', required=False, default='false')
parser.add_argument('--fsx_s3_import_bucket', help='designate s3://fsx_s3_import_bucket as the import bucket that will hydrate the Lustre file system for this cluster (default = UNDEFINED)', required=False, default='UNDEFINED')
parser.add_argument('--fsx_s3_import_path', help='append an import path to s3_import_path (default = import)', required=False, default='import')
parser.add_argument('--fsx_s3_export_bucket', help='designate s3://fsx_s3_export_bucket as the export bucket that will dehydrate the Lustre file system for this cluster (default = UNDEFINED)', required=False, default='UNDEFINED')
parser.add_argument('--fsx_s3_export_path', help='append an export path to s3_export_bucket (default = export)', required=False, default='export')
parser.add_argument('--fsx_size', help='Lustre file system size in GB - must use multiples of 3600 (default = 3600)', required=False, default=3600)
parser.add_argument('--fsx_chunk_size', help='chunk size (MB) of S3 objects imported into Lustre (default = 1024)', required=False, type=int, default=1024)
parser.add_argument('--hyperthreading', choices=['true', 'false'], help='enable Intel Hyperthreading (default = true)', required=False, default='true')
parser.add_argument('--initial_queue_size', help='initial number of compute nodes to deploy (default = 2)', required=False, default=2)
parser.add_argument('--maintain_initial_size', help='keep initial_queue_size instances always running (default = false)', required=False, default='false')
parser.add_argument('--master_instance_type', help='master EC2 instance type (default = c5.xlarge)', required=False, default='c5.xlarge')
parser.add_argument('--master_root_volume_size', help='master EBS root volume size in GB (default = 250)', required=False, default=250)
parser.add_argument('--max_queue_size', help='maximum number of compute nodes to deploy (default = 10)', required=False, default=10)
parser.add_argument('--max_vcpus', help='maximum number of allowed vcpus when using Batch (default = 20)', required=False, default=20)
parser.add_argument('--min_vcpus', help='minimum number of vcpus to maintain when using Batch (default = 0)', required=False, default=0)
parser.add_argument('--perftest_custom_start_number', help='starting number of custom performance cluster jobs to submit (default = 10)', required=False, default=10)
parser.add_argument('--perftest_custom_step_size', help='step size of the custom performance qsub scripts (default = 10)', required=False, default=10)
parser.add_argument('--perftest_custom_total_tests', help='number of performance tests to run (default = 10)', required=False, default=10)
parser.add_argument('--placement_group', choices=['NONE', 'DYNAMIC'], help='create a dynamic placement group for this cluster, use with caution (default=NONE)', required=False, default='NONE')
parser.add_argument('--prod_level', choices=['dev', 'test', 'stage', 'prod'], help='operating stage of the cluster (default = dev)', required=False, default='dev')
parser.add_argument('--project_id', '-P', help='project name or ID number (default = UNDEFINED)', required=False, default='UNDEFINED')
parser.add_argument('--scaledown_idletime', choices=['true', 'false'], help='amount of time in minutes without a job after which the compute node will terminate (default = 5)', required=False, default=5)
parser.add_argument('--scheduler', '-S', choices=['sge', 'torque', 'slurm', 'awsbatch'], help='cluster scheduler (default = sge)', required=False, default='sge')
parser.add_argument('--sge_pe_type', choices=['make', 'mpi', 'smp'], help='select a Grid Engine parallel environment type (default = smp)', required=False, default='smp')
parser.add_argument('--turbot_account', '-T', help='Turbot account ID (default = abd).  Set to "disabled" in non-Turbot environments.', required=False, default='disabled')

# Ddeploying compute instances into private subnets is not (yet) supported.
# Set --use_private_compute_subnet" and "--private_compute_cidr_subnet" to
# "false" and explicitly disable these parser options.
#
#parser.add_argument('--use_private_compute_subnet', help='deploy the compute nodes into a nonroutable private network - NOT TESTED (default = false)', required=False, default='false')
#parser.add_argument('--private_compute_cidr_subnet', help='designate a separate CIDR subnet for compute instances - NOT TESTED (default = false)', required=False, default='false')

use_private_compute_subnet = 'false'
private_compute_cidr_subnet = 'false'
private_compute_subnet_id = 'false'

# Parse the command used to create this cluster stack.

cluster_build_command = " ".join(sys.argv)

# Set cluster_parameters to the values provided via command line.

args = parser.parse_args()
ansible_verbosity = args.ansible_verbosity
az = args.az
base_os = args.base_os
cluster_lifetime = args.cluster_lifetime
cluster_name = args.cluster_name
cluster_owner = args.cluster_owner
cluster_owner_department = args.cluster_owner_department
cluster_owner_email = args.cluster_owner_email
cluster_type = args.cluster_type
compute_instance_type = args.compute_instance_type
compute_root_volume_size = args.compute_root_volume_size
custom_ami = args.custom_ami
desired_vcpus = args.desired_vcpus
debug_mode = args.debug_mode
ebs_encryption = args.ebs_encryption
ebs_shared_dir = args.ebs_shared_dir
ebs_shared_volume_size = args.ebs_shared_volume_size
ebs_shared_volume_type = args.ebs_shared_volume_type
efs_encryption = args.efs_encryption
efs_performance_mode = args.efs_performance_mode
enable_efs = args.enable_efs
enable_external_nfs = args.enable_external_nfs
external_nfs_server = args.external_nfs_server
enable_fsx = args.enable_fsx
enable_fsx_hydration = args.enable_fsx_hydration
fsx_s3_import_bucket = args.fsx_s3_import_bucket
fsx_s3_import_path = args.fsx_s3_import_path
fsx_s3_export_bucket = args.fsx_s3_export_bucket
fsx_s3_export_path = args.fsx_s3_export_path
fsx_chunk_size = args.fsx_chunk_size
fsx_size = args.fsx_size
enable_ganglia = args.enable_ganglia
enable_hpc_performance_tests = args.enable_hpc_performance_tests
enable_sge_pe = args.enable_sge_pe
hyperthreading = args.hyperthreading
initial_queue_size = args.initial_queue_size
maintain_initial_size = args.maintain_initial_size
master_instance_type = args.master_instance_type
master_root_volume_size = args.master_root_volume_size
max_queue_size = args.max_queue_size
max_vcpus = args.max_vcpus
min_vcpus = args.min_vcpus
placement_group = args.placement_group
prod_level = args.prod_level
project_id = args.project_id
region = az[:-1]
scaledown_idletime = args.scaledown_idletime
scheduler = args.scheduler
sge_pe_type = args.sge_pe_type
perftest_custom_start_number = args.perftest_custom_start_number
perftest_custom_step_size = args.perftest_custom_step_size
perftest_custom_total_tests = args.perftest_custom_total_tests
turbot_account = args.turbot_account
#
# NOTE: Deploying compute instances into private subnets is not currently
# supported so for now, "--use_private_subnet" and "--compute_cidr_subnet"
# are commented out.
#
#use_private_subnet = args.use_private_subnet
#compute_cidr_subnet = args.compute_cidr_subnet

# Define a dictionary of cluster_parameters that require decimal values.

decimal_vals_required = {
    'compute_root_volume_size': compute_root_volume_size,
    'desired_vcpus': desired_vcpus,
    'ebs_shared_volume_size': ebs_shared_volume_size,
    'fsx_chunk_size': fsx_chunk_size,
    'fsx_size': fsx_size,
    'master_root_volume_size': master_root_volume_size,
    'max_queue_size': max_queue_size,
    'min_vcpus': min_vcpus,
    'max_vcpus': max_vcpus,
    'perftest_custom_start_number': perftest_custom_start_number,
    'perftest_custom_step_size': perftest_custom_step_size,
    'perftest_custom_total_tests': perftest_custom_total_tests,
    'scaledown_idletime': scaledown_idletime
}

# FSxL is not currently supported when using AWS Batch as a scheduler or if
# base_os is neither Amazon Linux (alinux) or CentOS 7 (centos7):
#
# https://aws-parallelcluster.readthedocs.io/en/latest/configuration.html#fsx
# https://aws-parallelcluster.readthedocs.io/en/latest/configuration.html#fsx
#
# Lustre options should not be used without setting enable_fsx=true.
# Furthermore, Lustre-S3 hydration options should not be used without setting
# enable_fsx_hydration=true.

if enable_fsx == 'true':
    if (scheduler == 'awsbatch'):
        error_msg = 'FSxL is not currently supported when using AWS Batch as a scheduler!'
        refer_to_docs_and_quit(error_msg)
    if base_os not in ('alinux', 'centos7'):
        error_msg = 'FSxL is only supported on Amazon Linux (alinux) or CentOS 7 (centos7)!'
        refer_to_docs_and_quit(error_msg)
    if (enable_fsx_hydration == 'false') and (('UNDEFINED' not in fsx_s3_import_bucket) or ('UNDEFINED' not in fsx_s3_export_bucket)):
        error_msg = 'All Lustre-to-S3 interactions require: "enable_fsx_hydration=true"'
        refer_to_docs_and_quit(error_msg)
if enable_fsx == 'false':
    if enable_fsx_hydration == 'true': 
        error_msg = 'All Lustre-to-S3 interactions require: "enable_fsx=true"'
        refer_to_docs_and_quit(error_msg)
    if ('UNDEFINED' not in fsx_s3_import_bucket) or ('UNDEFINED' not in fsx_s3_export_bucket):
        error_msg = 'All Lustre-to-S3 interactions require: "enable_fsx=true"'
        refer_to_docs_and_quit(error_msg)
p_val('enable_fsx', debug_mode)

# Perform error checking and validation on fsx_chunk_size, which should range
# between 1,024 MB (1 GB) and 512,000 MB (500 GB).
# Furthermore, Lustre-S3 hydration options should *never* be used without
# setting enable_fsx_hydration=true.

if (int(fsx_chunk_size) > 528000) or (int(fsx_chunk_size) < 1024):
    error_msg='fsx_chunk_size must be between 1,024 MB (1 GB) and 528,000 MB (528 GB)!'
    refer_to_docs_and_quit(error_msg)
if enable_fsx_hydration == 'true':
    p_val('fsx_chunk_size', debug_mode)

# Configure a boto3 resource for communication with S3.

s3 = boto3.resource('s3')

# If an import bucket was provided but an export bucket was not, assume the
# import bucket will serve both functions.
# Future releases will support import and export path validity checks.

if fsx_s3_import_bucket != 'UNDEFINED' and fsx_s3_export_bucket == 'UNDEFINED':
    fsx_s3_export_bucket = fsx_s3_import_bucket
    print('')
    print('*** WARNING ***')
    print('fsx_s3_export_bucket was not specified!')
    print('Lustre will be hydrated *and* dehydrated from S3 using the FSx import bucket.')

# Perform error checking on the import and export S3 buckets when using Lustre.

if enable_fsx == 'true':
    if enable_fsx_hydration == 'true':
        if (s3.Bucket(fsx_s3_import_bucket) not in s3.buckets.all()) or (s3.Bucket(fsx_s3_export_bucket) not in s3.buckets.all()):
            error_msg = 'Please create valid import and export buckets before enabling FSX-S3 hydration.'
            refer_to_docs_and_quit(error_msg)
        print('')
        if fsx_s3_import_path == 'import':
            print('Using the default S3 import path: s3://' + fsx_s3_import_bucket + '/import')
        else:
            print('Setting the S3 import path to: s3://' + fsx_s3_import_bucket + '/' + fsx_s3_import_path)
        if fsx_s3_export_path == 'export':
            print('Using the default S3 export path: s3://' + fsx_s3_import_bucket + '/export')
        else:
            print('Setting the S3 export path to: s3://' + fsx_s3_export_bucket + '/' + fsx_s3_export_path)
    print('')
    print('*** IMPORTANT ***')
    print('Please ensure these paths exist before hydrating or dehydrating an S3 bucket')
    print('from the Lustre file system associated with this cluster.')
p_val('fsx_s3_import_bucket', debug_mode)
p_val('fsx_s3_export_bucket', debug_mode)

# Check to ensure the Lustre volume size is divisible by 3600.

if enable_fsx == 'true':
    if fsx_size%3600 == 0:
        p_val('fsx_size', debug_mode)
    else:
        error_msg='fsx_size must be divisible by 3600!'
        refer_to_docs_and_quit(error_msg)

# Set the master_instance_type and compute_instance_type to default if no
# specific instance_type was provided.  Change these values by editing the
# default_instance_types dictionary defined in parallelclustermaker_aux_data.

if scheduler != 'awsbatch':
    if master_instance_type == 'default':
        master_instance_type = default_master_instance_type
    if compute_instance_type == 'default':
        compute_instance_type =  default_compute_instance_type
else:
    if compute_instance_type == 'default':
        compute_instance_type = 'optimal'
    #
    # The HPC performance tests are all written largely in Python for use with
    # traditional HPC schedulers (Grid Engine, Slurm, and Torque) and do not
    # support AWS Batch.  This will be addressed in a future release. 
    #
    if enable_hpc_performance_tests ==  'true':
        error_msg='The ParallelClusterMaker performance tests do not (yet) work with AWS Batch!'
        refer_to_docs_and_quit(error_msg)

# Set the vars_file_path.
# Combine cluster_name + cluster_owner to ensure unique cluster stack names.
# Note that cluster_name will be formally redefined a little further down.

vars_file_path = './vars_files/' + cluster_owner + '-' + cluster_name + ".yml"

# Create the vars_file directory if it does not already exist.

cwd = os.getcwd()
try:
    os.makedirs('./vars_files')
except OSError as e:
    if e.errno != errno.EEXIST:
        raise

# Print a header for cluster variable validation.

if debug_mode == 'true':
    print_TextHeader(cluster_owner + '-' + cluster_name, 'Validating cluster parameters', 80)
    print('')
    print('Performing parameter validation...')
    print('')
p_val('vars_file_path', debug_mode)

# Perform error checking on the selected AWS Region and Availability Zone. 
# Abort if a non-existent Region or Availability Zone was chosen.

try:
    ec2client = boto3.client('ec2', region_name = region)
    az_information = ec2client.describe_availability_zones()
except (ValueError):
    illegal_az_msg(az)
except (botocore.exceptions.EndpointConnectionError):
    illegal_az_msg(az)
else:
    p_val('region', debug_mode)
    p_val('az', debug_mode)

# Check for the presence of an existing cluster with the same name.
# If an existing cluster is found, abort to prevent the potential creation
# of duplicate stacks.

status_cmd_string = 'pcluster status --region ' + region + ' ' + cluster_name

with open(os.devnull, 'w') as devnull:
    p = subprocess.run(status_cmd_string, shell=True, stdout=devnull)
    if p.returncode == 0:
        error_msg='pcluster stack "' + cluster_owner + '-' + cluster_name + '" is already deployed in ' + region + '!'
        refer_to_docs_and_quit(error_msg)
    else:
        if debug_mode == 'true':
            p_val('cluster_name', debug_mode)

# Check for the presence of an existing vars_file for this cluster.
# If an existing vars_file is found, abort to prevent the potential creation
# of duplicate stacks.

if os.path.isfile(vars_file_path):
    print('')
    print('*** WARNING ***')
    print('An existing vars_file for cluster "' + cluster_owner + '-' + cluster_name + '" was found!')
    print('')
    print('Please delete this cluster properly and retry the build:')
    print('$ ./kill-pcluster.py -N ' + cluster_name + ' -O ' + cluster_owner + ' -A ' + az)
    print('$ ' + cluster_build_command)
    print('')
    print('Aborting...')
    sys.exit(1)
else:
    p_val('vars_file_path', debug_mode)

# Set some critical environment variables to support Turbot.
# https://turbot.com/about/

if turbot_account != 'disabled':
    turbot_profile = 'turbot__' + turbot_account + '__' + cluster_owner
    os.environ['AWS_PROFILE'] = turbot_profile
    os.environ['AWS_DEFAULT_REGION'] = region
    boto3.setup_default_session(profile_name=turbot_profile)
    p_val('turbot_account', debug_mode)
    p_val('turbot_profile', debug_mode)

# Perform error checking on the decimal_vals_required dictionary to ensure its
# key values are decimals.  This is also critical for ensuring that all 
# cluster stacks are unique entities.

for key in decimal_vals_required:
    if is_number(decimal_vals_required[key]):
        p_val(key, debug_mode)
    else:
        error_msg='''"' + key + '" must be a decimal!

Current parameter value:
    ' + key + ' = ' + decimal_vals_required[key])'''
        refer_to_docs_and_quit(error_msg)

# Check to ensure external NFS support has been properly enabled.

if (enable_external_nfs == 'true') and (external_nfs_server == ''):
    error_msg='Missing: valid setting for "--external_nfs_server"'
    refer_to_docs_and_quit(error_msg)
else:
    p_val('enable_external_nfs', debug_mode)
    p_val('external_nfs_server', debug_mode)

# Set external_nfs_server to a dummy value if external NFS support has not
# been enabled.

if enable_external_nfs == 'false':
    external_nfs_server = 'FEATURE_DISABLED'

# Validate the EBS configuration based on the shared volume type. 

p_val('ebs_shared_dir', debug_mode)
p_val('ebs_shared_volume_type', debug_mode)
p_val('ebs_shared_volume_size', debug_mode)
if ebs_encryption == 'true':
    p_val('ebs_encryption', debug_mode)

# Validate EFS based on the selected performance mode.

if enable_efs == 'true':
    p_val('efs_root', debug_mode)
    p_val('efs_performance_mode', debug_mode)
    p_val('efs_throughput_mode', debug_mode)
    p_val('efs_encryption', debug_mode)

# If a custom_ami was provided, perform error checking on its existence.

if custom_ami != 'NONE':
    try:
        custom_ami_information = ec2client.describe_images(ImageIds=[custom_ami])
    except (botocore.exceptions.ClientError):
        error_msg='"' + custom_ami + '" does not appear to be a valid AMI!'
        refer_to_docs_and_quit(error_msg)
    else:
        p_val('custom_ami', debug_mode)

# Todo - if enable_external_nfs=true, check to ensure external_nfs_server
# actually exists through a ping test or running showmount/rpcinfo to verify
# NFS shares are being exported.  Something like:
#
# $ showmount -e external_nfs_server (fail if empty)
# $ rpcinfo -t remote_nfs_server nfs 4 (fail if empty)
# $ ping -c 4 remote_nfs_server (fail if packet_loss > 0)

# Redefine cluster_name here to preserve compatibility with the original
# script arguments relating "-O rmarable -N dev01" ==> rmarable-dev01.

cluster_birth_name = cluster_name
cluster_name = cluster_owner + '-' + cluster_name

# Check to ensure requested EBS volume sizes are not larger than 16 TB.

if int(master_root_volume_size) > 16000 or int(compute_root_volume_size) > 16000 or int(ebs_shared_volume_size) > 16000:
    error_msg='''Maximum allowed EBS volume size is 16 TB (16000 GB)!
master_root_volume_size  = ' + str(master_root_volume_size) + ' GB
compute_root_volume_size = ' + str(compute_root_volume_size) + ' GB
ebs_shared_volume_size   = ' + str(ebs_shared_volume_size) + ' GB'''
    refer_to_docs_and_quit(error_msg)
    sys.exit(1)

# Set the state directory for this cluster.

cluster_data_dir = './cluster_data/' + prod_level + '/' + cluster_name + '/'

# Check for an existing state directory for this cluster.

try:
    os.makedirs(cluster_data_dir)
except OSError as e:
    if e.errno != errno.EEXIST:
        raise

# Generate a cluster_serial_number file to store useful state information
# about each active cluster stack.

SERIAL_DIR = './active_clusters'
try:
    os.makedirs(SERIAL_DIR)
except OSError as e:
    if e.errno != errno.EEXIST:
        raise

DEPLOYMENT_DATE = time.strftime("%-d.%B.%Y")
Deployed_On = time.strftime("%B %-d, %Y")

cluster_serial_datestamp = DateTime.utcnow().strftime('%S%M%H%d%m%Y')
cluster_serial_number = cluster_name + '-' + cluster_serial_datestamp
cluster_serial_number_file = SERIAL_DIR + '/' + cluster_name + '.serial'

if not os.path.isfile(cluster_serial_number):
    print('%s' % (cluster_serial_number), file=open(cluster_serial_number_file, 'w'))

p_val('prod_level', debug_mode)
p_val('cluster_owner_department', debug_mode)
p_val('cluster_serial_number', debug_mode)
p_val('cluster_serial_number_file', debug_mode)

# Validate the project_id if it was provided.

if project_id != 'UNDEFINED':
    p_val('project_id', debug_mode)

# Perform error checking on master_instance_type and compute_instance_type
# to ensure supported EC2 instance types were selected.

if master_instance_type not in ec2_instances_full_list:
    p_fail(master_instance_type, 'master_instance_type', ec2_instances_cloudhpc)
p_val('master_instance_type', debug_mode)
p_val('master_root_volume_size', debug_mode)

if compute_instance_type not in ec2_instances_full_list:
    p_fail(compute_instance_type, 'compute_instance_type', ec2_instances_full_list)
p_val('compute_instance_type', debug_mode)
p_val('compute_root_volume_size', debug_mode)

# Validate the scheduler and all other associated parameters.

p_val('scheduler', debug_mode)
if scheduler == 'sge':
    p_val('sge_pe_type', debug_mode)

# Perform a minimal check to ensure ebs_shared_dir looks like a valid path.

if ebs_shared_dir.startswith('/'):
    p_val('ebs_shared_dir', debug_mode)
else:
    print('ebs_shared_dir = ' + ebs_shared_dir)
    print('')
    error_msg='''"' + ebs_shared_dir+ '"' ' does not appear to be a Unix file path!
Try using "/' + ebs_shared_dir + '" instead.'''
    refer_to_docs_and_quit(error_msg)

# Perform a minimal check to ensure cluster_owner_email resembles a valid
# email address.

if validate_email(cluster_owner_email):
    p_val('cluster_owner_email', debug_mode)
else:
    error_msg=''''cluster_owner_email = ' + cluster_owner_email

This does not appear to be a valid email address!
Please refer to: https://en.wikipedia.org/wiki/Email_address'''
    refer_to_docs_and_quit(error_msg)

# Parse the subnet_id, vpc_id, and vpc_name from the selected AWS Region and
# Availability Zone.

subnet_information = ec2client.describe_subnets(
    Filters=[ { 'Name': 'availabilityZone', 'Values': [ az, ] }, ],
)
vpc_information = ec2client.describe_vpcs()

subnet_id = subnet_information['Subnets'][0]['SubnetId']
p_val('subnet_id', debug_mode)
for vpc in vpc_information["Vpcs"]:
    vpc_id = vpc["VpcId"]
    p_val('vpc_id', debug_mode)
    vpc_name = vpc_information['Vpcs'][0]['Tags'][0]['Value']
    p_val('vpc_name', debug_mode)

# Parse the AWS Account ID.

stsclient = boto3.client('sts', region_name=region, endpoint_url='https://sts.' + region + '.amazonaws.com')
aws_account_id = stsclient.get_caller_identity()["Account"]

# Perform error checking on the selected operating system.
# Configure the ec2_user account and home directory path to match base_os.

if base_os == 'alinux':
    ec2_user = 'ec2-user'
elif base_os == 'centos6' or base_os == 'centos7':
    ec2_user = 'centos'
elif base_os == 'ubuntu1404' or base_os == 'ubuntu1604':
    ec2_user = 'ubuntu'
else:
    p_fail(base_os, 'base_os', base_os_allowed)
ec2_user_home = '/home/' + ec2_user
p_val('base_os', debug_mode)

# Compute EC2 spot prices from: https://aws.amazon.com/ec2/spot/pricing/
# Pad the spot_price with a buffer to protect against spot price market
# fluctuations that might cause an instance to be reclaimed in the middle
# of a job.
#
# If the user selects ondemand instances, print a friendly reminder to the
# console that spot is a more economical choice for HPC clusters.

spot_buffer = 0.5
if cluster_type == 'ondemand':
    p_val('cluster_type', debug_mode)
    print('	On-Demand instances were selected')
    print('	*Hint* ==> spot instances are more cost-effective for HPC!!')
    print('')
    spot_price = 'undefined'
elif cluster_type == 'spot':
    p_val('cluster_type', debug_mode)
    if compute_instance_type != 'optimal':
        prices=ec2client.describe_spot_price_history(InstanceTypes=[compute_instance_type],MaxResults=1,ProductDescriptions=['Linux/UNIX (Amazon VPC)'],AvailabilityZone=az)
        try:
            raw_spot_price = float(prices['SpotPriceHistory'][0]['SpotPrice'])
        except IndexError:
            error_msg='''The selected compute_instance_type is unavailable for purchase on the
Spot market within the selected Availability Zone.

compute_instance_type: ' + compute_instance_type
'Availability Zone: ' + az'''
            refer_to_docs_and_quit(error_msg)
        spot_price = round(raw_spot_price + (spot_buffer * raw_spot_price), 8)
    else:
        raw_spot_price = 'UNDEFINED'
        spot_price = 'UNDEFINED'
    p_val('spot_price', debug_mode)
else:
    p_fail(cluster_type, 'cluster_type', cluster_type_allowed)

# Create ec2_iam_role, which will be attached to all cluster instances.

iam = boto3.client('iam')
ec2_iam_policy = 'pclustermaker-policy-' + cluster_serial_number
ec2_iam_role = 'pclustermaker-role-' + cluster_serial_number
ec2_json_policy_src = 'templates/ParallelClusterInstancePolicy.json_src'
ec2_json_policy_template = cluster_data_dir + 'ParallelClusterInstancePolicy.json'

print('')
try:
    check_ec2_iam_role = iam.get_role(RoleName=ec2_iam_role)
    print('Found ec2_iam_role: ' + ec2_iam_role)
except ClientError as e:
    if e.response['Error']['Code'] == 'NoSuchEntity':
        with open(ec2_json_policy_src, 'r') as ec2_iam_role_src:
            role_stage_0 = ec2_iam_role_src.read()
            ec2_iam_role_src.close()
            # Customize the IAM JSON policy template using cluster_parameters
            # provided from the command line.
            role_stage_1 = role_stage_0.replace('<AWS_ACCOUNT_ID>', aws_account_id)
            role_stage_2 = role_stage_1.replace('<PROD_LEVEL>', prod_level)
            role_stage_3 = role_stage_2.replace('<CLUSTER_SERIAL_NUMBER>', cluster_serial_number)
            role_stage_4 = role_stage_3.replace('<CLUSTER_NAME>', cluster_name)
            role_stage_5 = role_stage_4.replace('<CLUSTER_OWNER>', cluster_owner)
            role_stage_6 = role_stage_5.replace('<CLUSTER_SERIAL_DATESTAMP>', cluster_serial_datestamp)
            filedata = role_stage_6
        with open(ec2_json_policy_template, 'w') as ec2_iam_role_dest:
            ec2_iam_role_dest.write(filedata)
            ec2_iam_role_dest.close()
        pcluster_ec2_iam_role = iam.create_role(
            RoleName=ec2_iam_role,
            AssumeRolePolicyDocument='{ "Version": "2012-10-17", "Statement": [ { "Effect": "Allow", "Principal": { "Service": [ "ec2.amazonaws.com" ] }, "Action": "sts:AssumeRole" } ] }',
            Description='ParallelClusterMaker EC2 IAM instance role'
            )
        print('Created ec2_iam_role: ' + ec2_iam_role)
        with open(ec2_json_policy_template, 'r') as policy_input:
            pcluster_ec2_iam_policy = iam.put_role_policy(
                RoleName=ec2_iam_role,
                PolicyName=ec2_iam_policy,
                PolicyDocument=policy_input.read()
                )
        print('Created ec2_iam_policy: ' + ec2_iam_policy)
if debug_mode == 'true':
    p_val('ec2_iam_policy', debug_mode)
    p_val('ec2_iam_role', debug_mode)

# Create serverless_ec2_iam_role, which will be attached to the Lambda
# function that destroys the stack after cluster_lifetime has expired.

serverless_ec2_iam_policy = 'kill-pclustermaker-policy-' + cluster_serial_number
serverless_ec2_iam_role = 'kill-pclustermaker-role-' + cluster_serial_number

try:
    check_serverless_ec2_iam_role = iam.get_role(RoleName=serverless_ec2_iam_role)
    print('Found serverless_ec2_iam_role: ' + serverless_ec2_iam_role)
except ClientError as e:
    if e.response['Error']['Code'] == 'NoSuchEntity':
        pcluster_serverless_ec2_iam_role = iam.create_role(
            RoleName=serverless_ec2_iam_role,
            AssumeRolePolicyDocument='{ "Version": "2012-10-17", "Statement": [ { "Effect": "Allow", "Principal": { "Service": [ "lambda.amazonaws.com" ] }, "Action": "sts:AssumeRole" } ] }',
            Description='Serverless ParallelClusterMaker IAM role'
            )
        print('Created serverless_ec2_iam_role: ' + serverless_ec2_iam_role)
        with open(ec2_json_policy_template, 'r') as serverless_policy_input:
            pcluster_serverless_ec2_iam_policy = iam.put_role_policy(
                RoleName=serverless_ec2_iam_role,
                PolicyName=serverless_ec2_iam_policy,
                PolicyDocument=serverless_policy_input.read()
                )
        print('Created serverless_ec2_iam_policy: ' + serverless_ec2_iam_policy)
if debug_mode == 'true':
    p_val('serverless_ec2_iam_policy', debug_mode)
    p_val('serverless_ec2_iam_role', debug_mode)

# If FSxL-S3 hydration is enabled, create an inline policy permitting access
# to the S3 import and export buckets and attach it to ec2_iam_role and
# serverless_ec2_iam_role.

if enable_fsx_hydration == 'true':
    fsx_hydration_json_policy_src = 'templates/LustreS3HydrationPolicy.json_src'
    fsx_hydration_policy_template = cluster_data_dir + 'LustreS3HydrationPolicy.json_src'
    fsx_hydration_iam_policy = 'pclustermaker-fsx-s3-policy-' + cluster_serial_number
    with open(fsx_hydration_json_policy_src, 'r') as fsx_hydration_policy_src:
        policy_stage_0 = fsx_hydration_policy_src.read()
        fsx_hydration_policy_src.close()
        policy_stage_1 = policy_stage_0.replace('<FSX_S3_EXPORT_BUCKET>', fsx_s3_export_bucket)
        policy_stage_2 = policy_stage_1.replace('<FSX_S3_IMPORT_BUCKET>', fsx_s3_import_bucket)
        filedata = policy_stage_2
    print('Created fsx_hydration_iam_policy: ' + fsx_hydration_iam_policy)
    with open(fsx_hydration_policy_template, 'w') as fsx_hydration_policy_dest:
        fsx_hydration_policy_dest.write(filedata)
        fsx_hydration_policy_dest.close()
        with open(fsx_hydration_policy_template, 'r') as policy_input:
            pcluster_fsx_hydration_iam_policy = iam.put_role_policy(
                RoleName=ec2_iam_role,
                PolicyName=fsx_hydration_iam_policy,
                PolicyDocument=policy_input.read()
                )
        policy_input.close()
        with open(fsx_hydration_policy_template, 'r') as policy_input:
            pcluster_fsx_hydration_iam_policy = iam.put_role_policy(
                RoleName=serverless_ec2_iam_role,
                PolicyName=fsx_hydration_iam_policy,
                PolicyDocument=policy_input.read()
                )
        policy_input.close()
    print('')
    print('Attached ' + fsx_hydration_iam_policy + ' to the following roles:')
    print('    ' + ec2_iam_role)
    print('    ' + serverless_ec2_iam_role)
else:
    fsx_hydration_iam_policy = 'UNDEFINED'

if debug_mode == 'true':
    p_val('fsx_hydration_iam_policy', debug_mode)

# Perform error checking against the auto-generated name for s3_bucketname
# If s3_bucketname doesn't exist, create it during the cfncluster stack build.

s3_bucketname = 'parallelclustermaker-' + cluster_serial_number

if s3.Bucket(s3_bucketname) not in s3.buckets.all():
    p_val('s3_bucketname', debug_mode)
else:
    error_msg = 'Found an existing S3 bucket associated with this cluster!'
    refer_to_docs_and_quit(error_msg)

# Define the cluster_parameters dictionary.
# This data is needed to build the vars_file.
#
# Reminder: deploying compute instances into private subnets is not (yet)
# supported.  Leave these lines commented out:
#
#    'use_private_subnet': use_private_subnet,
#    'compute_cidr_subnet': compute_cidr_subnet,

cluster_parameters = {
    'aws_account_id': aws_account_id, 
    'az': az,
    'base_os': base_os,
    'cluster_birth_name': cluster_birth_name,
    'cluster_lifetime': cluster_lifetime,
    'cluster_name': cluster_name,
    'cluster_owner': cluster_owner,
    'cluster_owner_email': cluster_owner_email,
    'cluster_owner_department': cluster_owner_department,
    'cluster_serial_datestamp': cluster_serial_datestamp,
    'cluster_serial_number': cluster_serial_number,
    'cluster_serial_number_file': cluster_serial_number_file,
    'cluster_type': cluster_type,
    'desired_vcpus': desired_vcpus,
    'compute_instance_type': compute_instance_type,
    'compute_root_volume_size': compute_root_volume_size,
    'custom_ami': custom_ami,
    'debug_mode': debug_mode,
    'ebs_encryption': ebs_encryption,
    'ebs_shared_dir': ebs_shared_dir,
    'ebs_shared_volume_size': ebs_shared_volume_size,
    'ebs_shared_volume_type': ebs_shared_volume_type,
    'ec2_iam_policy': ec2_iam_policy,
    'ec2_iam_role': ec2_iam_role,
    'ec2_user': ec2_user,
    'ec2_user_home': ec2_user_home,
    'efs_encryption': efs_encryption,
    'efs_performance_mode': efs_performance_mode,
    'enable_efs': enable_efs,
    'enable_external_nfs': enable_external_nfs,
    'enable_fsx': enable_fsx,
    'enable_fsx_hydration': enable_fsx_hydration,
    'enable_ganglia': enable_ganglia,
    'enable_hpc_performance_tests': enable_hpc_performance_tests,
    'enable_sge_pe': enable_sge_pe,
    'external_nfs_server': external_nfs_server,
    'fsx_chunk_size': fsx_chunk_size,
    'fsx_hydration_iam_policy': fsx_hydration_iam_policy,
    'fsx_s3_export_bucket': fsx_s3_export_bucket,
    'fsx_s3_export_path': fsx_s3_export_path,
    'fsx_s3_import_bucket': fsx_s3_import_bucket,
    'fsx_s3_import_path': fsx_s3_import_path,
    'fsx_size': fsx_size,
    'hyperthreading': hyperthreading,
    'initial_queue_size': initial_queue_size,
    'maintain_initial_size': maintain_initial_size,
    'max_queue_size': max_queue_size,
    'max_vcpus': max_vcpus,
    'master_instance_type': master_instance_type,
    'master_root_volume_size': master_root_volume_size,
    'min_vcpus': min_vcpus,
    'perftest_custom_start_number': perftest_custom_start_number,
    'perftest_custom_step_size': perftest_custom_step_size,
    'perftest_custom_total_tests': perftest_custom_total_tests,
    'placement_group': placement_group,
    'private_compute_cidr_subnet': private_compute_cidr_subnet,
    'private_compute_subnet_id': private_compute_subnet_id,
    'prod_level': prod_level,
    'project_id': project_id,
    'raw_spot_price': raw_spot_price,
    'region': region,
    's3_bucketname': s3_bucketname,
    'scaledown_idletime': scaledown_idletime,
    'scheduler': scheduler,
    'serverless_ec2_iam_policy': serverless_ec2_iam_policy,
    'serverless_ec2_iam_role': serverless_ec2_iam_role,
    'sge_pe_type': sge_pe_type,
    'spot_price': spot_price,
    'subnet_id': subnet_id,
    'use_private_compute_subnet': use_private_compute_subnet,
    'vpc_id': vpc_id,
    'vpc_name': vpc_name,
    'Deployed_On': Deployed_On,
    'DEPLOYMENT_DATE': DEPLOYMENT_DATE
}

# Print the current values of all validated cluster_parameters to the console
# when debug mode is enabled.

if debug_mode == 'true':
    print_TextHeader(cluster_name, 'Displaying cluster parameter values', 80)
    print('aws_account_id = ' + aws_account_id)
    print('base_os = ' + base_os)
    print('cluster_birth_name = ' + cluster_birth_name)
    print('cluster_lifetime (days:hours:minutes) = ' + str(cluster_lifetime))
    print('cluster_name = ' + cluster_name)
    print('cluster_owner = ' + cluster_owner)
    print('cluster_owner_department = ' + cluster_owner_department)
    print('cluster_owner_email = ' + cluster_owner_email)
    print('cluster_serial_datestamp = ' + cluster_serial_datestamp)
    print('cluster_serial_number = ' + cluster_serial_number)
    print('cluster_serial_number_file = ' + cluster_serial_number_file)
    print('cluster_type = ' + cluster_type)
    if cluster_type == 'spot':
        if 'UNDEFINED' not in str(spot_price):
            print('spot_price = $' + str(spot_price) + ' per hour')
    print('compute_instance_type = ' + compute_instance_type)
    print('compute_root_volume_size = ' + str(compute_root_volume_size) + ' GB')
    if custom_ami != 'NONE':
        print('custom_ami = ' + custom_ami)
    print('ebs_shared_dir = ' + ebs_shared_dir)
    print('ebs_shared_volume_size = ' + str(ebs_shared_volume_size) + ' GB')
    print('ebs_shared_volume_type = ' + str(ebs_shared_volume_type))
    print('ebs_encryption = ' + str(ebs_encryption))
    print('ec2_user = ' + ec2_user)
    print('ec2_user_home = ' + ec2_user_home)
    print('ec2_iam_policy = ' + ec2_iam_policy)
    print('ec2_iam_role = ' + ec2_iam_role)
    if enable_external_nfs == 'true':
        print('enable_external_nfs = ' + enable_external_nfs)
        print('external_nfs_server = ' + external_nfs_server)
    if enable_efs == 'true':
        print('enable_efs = ' + enable_efs)
        print('efs_encryption = ' + efs_encryption)
        print('efs_performance_mode = ' + efs_performance_mode)
    if enable_fsx == 'true':
        print('enable_fsx = ' + enable_fsx)
        print('enable_fsx_hydration = ' + enable_fsx_hydration)
        print('fsx_size = ' + str(fsx_size) + ' GB')
        if enable_fsx_hydration == 'true':
            print('fsx_chunk_size = ' + enable_fsx_chunk_size)
            print('fsx_hydration_iam_policy = ' + fsx_hydration_iam_policy)
            print('fsx_s3_export_bucket = ' + enable_fsx_s3_export_bucket)
            print('fsx_s3_export_path = ' + fsx_s3_export_path)
            print('fsx_s3_import_bucket = ' + enable_fsx_s3_import_bucket)
            print('fsx_s3_import_path = ' + fsx_s3_import_path)
    if enable_ganglia:
        print('enable_ganglia = ' + enable_ganglia)
    if enable_hpc_performance_tests:
        print('enable_hpc_performance_tests = ' + enable_hpc_performance_tests)
        print('perftest_custom_start_number = ' + str(perftest_custom_start_number))
        print('perftest_custom_step_size = ' + str(perftest_custom_step_size))
        print('perftest_custom_total_tests = ' + str(perftest_custom_total_tests))
    print('hyperthreading = ' + hyperthreading)
    print('initial_queue_size = ' + str(initial_queue_size))
    print('master_instance_type = ' + master_instance_type)
    print('master_root_volume_size = ' + str(master_root_volume_size) + ' GB')
    print('max_queue_size = ' + str(max_queue_size))
    if placement_group != 'NONE':
        print('placement_group = ' + placement_group)
    print('prod_level = ' + prod_level)
    if project_id != 'UNDEFINED':
        print('project_id = ' + project_id)
    print('region = ' + region)
    print('serverless_ec2_iam_policy = ' + serverless_ec2_iam_policy)
    print('serverless_ec2_iam_role = ' + serverless_ec2_iam_role)
    print('subnet_id = ' + subnet_id)
    if use_private_compute_subnet == 'true':
        print('use_private_compute_subnet = ' + use_private_compute_subnet)
        print('private_compute_cidr_subnet = ' + private_compute_cidr_subnet)
        print('private_compute_subnet_id = ' + private_compute_subnet_id)
    print('vpc_id = ' + vpc_id)
    print('vpc_name = ' + vpc_name)
    print('scheduler = ' + scheduler)
    if scheduler == 'batch':
        print('desired_vcpus = ' + desired_vcpus)
        print('min_vcpus = ' + min_vcpus)
        print('max_vcpus = ' + max_vcpus)
    if scheduler == 'sge':
        print('enable_sge_pe = ' + enable_sge_pe)
        print('sge_pe_type = ' + sge_pe_type)
    print('scaledown_idletime = ' + str(scaledown_idletime))
    print('s3_bucketname = s3://' + s3_bucketname)

# Generate the vars_file for this cluster.

vars_file_part_1 = '''\
################################################################################
# Name:         {cluster_name}.yml
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Created On:   April 20, 2019
# Last Changed: May 27, 2019
# Deployed On:  {Deployed_On}
# Purpose:      ParallelCluster configuration for cluster "{cluster_name}"
# Notes:	Automatically generated by ParallelClusterMaker
################################################################################

# Metadata tags for cluster identification

cluster_birth_name: {cluster_birth_name}
cluster_name: {cluster_name}
cluster_owner: {cluster_owner}
cluster_owner_department: {cluster_owner_department}
cluster_owner_email: {cluster_owner_email}
cluster_serial_number: {cluster_serial_number}
cluster_serial_number_file: {cluster_serial_number_file}
cluster_serial_datestamp: {cluster_serial_datestamp}
project_id: {project_id}
prod_level: {prod_level}
DEPLOYMENT_DATE: {DEPLOYMENT_DATE}

# Master and Compute instance definitions

base_os: {base_os}
custom_ami: {custom_ami}
hyperthreading: {hyperthreading}
master_instance_type: {master_instance_type}
master_root_volume_size: {master_root_volume_size}
compute_instance_type: {compute_instance_type}
compute_root_volume_size: {compute_root_volume_size}

# AWS networking parameters

aws_account_id: {aws_account_id}
region: {region}
az: {az}
vpc_id: {vpc_id}
vpc_name: {vpc_name}
subnet_id: {subnet_id}
use_private_compute_subnet: {use_private_compute_subnet}
private_compute_cidr_subnet: {private_compute_cidr_subnet}
private_compute_subnet_id: {private_compute_subnet_id}

# EC2 instance parameters

ec2_keypair: "{{{{ cluster_name }}}}"
ec2_user: {ec2_user}
ec2_user_home: {ec2_user_home}
ec2_user_src: "{{{{ ec2_user_home }}}}/src"
ec2_iam_policy: {ec2_iam_policy}
ec2_iam_role: {ec2_iam_role}
'''

vars_file_part_2 = '''\

# Critical directory paths

cluster_rootdir: "{{{{ local_workingdir }}}}"
cluster_data_dir: "{{{{ cluster_rootdir }}}}/cluster_data/{{{{ prod_level }}}}/{{{{ cluster_name }}}}"
cluster_template_dir: "{{{{ cluster_rootdir }}}}/templates"
stage_dir_parent: /tmp/_stagedir_make-cluster.py
stage_dir: "{{{{ stage_dir_parent }}}}/{{{{ cluster_name }}}}"
performance_rootdir: "{{{{ local_workingdir }}}}/performance"
performance_stage_dir: "{{{{ stage_dir_parent }}}}/{{{{ cluster_name }}}}/performance"
performance_template_dir: "{{{{ local_workingdir }}}}/performance/jinja2"

# Serverless parameters

serverless_ec2_iam_policy: {serverless_ec2_iam_policy}
serverless_ec2_iam_role: {serverless_ec2_iam_role}
s3_serverless_bucket: serverless-pcluster-{{{{ cluster_serial_number }}}}
serverless_function_name: terminate-{{{{ cluster_name }}}}-{{{{ prod_level }}}}-{{{{ cluster_serial_datestamp }}}}
serverless_resource_role: TerminateParallelClusterMakerStack{{{{ cluster_owner }}}}{{{{ cluster_serial_datestamp }}}}
serverless_stack_name: terminate-pcluster-{{{{ cluster_serial_number }}}}
serverless_handler: terminate-pcluster-{{{{ cluster_name }}}}-{{{{ prod_level }}}}-{{{{ cluster_serial_datestamp }}}}
serverless_handler_dest: terminate-pcluster-{{{{ cluster_name }}}}-{{{{ prod_level }}}}-{{{{ cluster_serial_datestamp }}}}.py
serverless_stage_dir: "{{{{ cluster_data_dir }}}}/serverless"
serverless_template_dir: "{{{{ local_workingdir }}}}/serverless/terminate_pcluster"

# SNS templates

sns_build_summary_report_src: "{{{{ cluster_template_dir }}}}/sns_build_summary_report.j2"
sns_build_summary_report_dest: "{{{{ cluster_data_dir }}}}/sns_build_summary.{{{{ cluster_name }}}}.txt"
sns_efs_build_summary_report_src: "{{{{ cluster_template_dir }}}}/sns_efs_build_summary_report.j2"
sns_efs_build_summary_report_dest: "{{{{ cluster_data_dir }}}}/sns_efs_build_summary.{{{{ cluster_name }}}}.txt"
sns_fsx_build_summary_report_src: "{{{{ cluster_template_dir }}}}/sns_fsx_build_summary_report.j2"
sns_fsx_build_summary_report_dest: "{{{{ cluster_data_dir }}}}/sns_fsx_build_summary.{{{{ cluster_name }}}}.txt"
sns_destruction_summary_report_src: "{{{{ cluster_template_dir }}}}/sns_destruction_summary_report.j2"
sns_destruction_summary_report_dest: "{{{{ cluster_data_dir }}}}/sns_destruction_summary.{{{{ cluster_name }}}}.txt"

# User and environment configuration

spack_user: spack
spack_group: spack
ssh_keypair: "{{{{ cluster_data_dir }}}}/{{{{ ec2_keypair }}}}.pem"
ssh_known_hosts: ~/.ssh/known_hosts

# Cluster stack and autoscaling configuration

cluster_config_dest: config.{{{{ cluster_name }}}}
cluster_config_template: "{{{{ cluster_data_dir }}}}/config.{{{{ cluster_name }}}}"
cluster_config_template_orig: "{{{{ cluster_template_dir }}}}/config.pcluster.j2"
cluster_lifetime: "{cluster_lifetime}"
cluster_type: {cluster_type}
placement_group: {placement_group}
scaledown_idletime: {scaledown_idletime}
scaling_settings: custom
'''

if scheduler == "awsbatch":
    vars_file_scheduler = '''\

# Scheduler parameters for AWS Batch

scheduler: {scheduler}
min_vcpus: {min_vcpus}
desired_vcpus: {desired_vcpus}
max_vcpus: {max_vcpus}
'''
else:
    vars_file_scheduler = '''\

# Scheduler parameters for SGE, Slurm, and Torque

scheduler: {scheduler}
initial_queue_size: {initial_queue_size}
maintain_initial_size: {maintain_initial_size}
max_queue_size: {max_queue_size}
'''

if scheduler == "sge":
    vars_file_sge = '''\

# SGE parallel environment parameters

enable_sge_pe: {enable_sge_pe}
sge_pe_type: {sge_pe_type}
'''
    vars_file_scheduler = vars_file_scheduler + vars_file_sge

if cluster_type == "spot":
    if scheduler == "awsbatch":
        vars_file_spot_price = '''\

# Spot instance definitions

raw_spot_price: {raw_spot_price}
spot_bid_percentage: 80
'''
    else:
        vars_file_spot_price = '''\

# Spot instance definitions

raw_spot_price: {raw_spot_price}
spot_price: {spot_price}
'''

vars_file_part_3 = '''\

# S3 bucket-related configuration

s3_bucketname: {s3_bucketname}
s3_script_path: cluster_scripts/{{{{ prod_level }}}}
s3_object_path: s3://{{{{ s3_bucketname }}}}/{{{{ s3_script_path }}}}
s3_cluster_data_dir: "cluster_data/{{{{ prod_level }}}}"
s3_url: https://s3.amazonaws.com/{{{{ s3_bucketname }}}}/cluster_scripts/{{{{ prod_level }}}}
s3_read_write_resource: arn:aws:s3:::{{{{ s3_bucketname }}}}

# *********************************** WARNING **********************************
#                Custom Chef recipes are currently unsupported!
#            Do *NOT* enable these parameters in PROD environments!
# ******************************************************************************
#
# Custom Chef recipe configuration
#
#custom_cookbook_src: "{{{{ cluster_name }}}}-custom-pcluster-cookbook.tgz"
#custom_cookbook_s3_dest: "{{{{ custom_cookbook_src }}}}"
#custom_cookbook_url: "{{{{ s3_url }}}}/{{{{ custom_cookbook_src }}}}"

# Lambda stack termination function configuration

generate_cron_lifetime_string_src: "{{{{ cluster_template_dir }}}}/generate_cron_lifetime_string.j2"
generate_cron_lifetime_string_dest: "{{{{ cluster_data_dir }}}}/generate_cron_lifetime_string.{{{{ cluster_name }}}}.py"

# ParallelCluster postinstall script configuration

preinstall_template_orig: "{{{{ cluster_template_dir }}}}/preinstall.j2"
preinstall_src: "{{{{ cluster_data_dir }}}}/preinstall.{{{{ cluster_name }}}}.sh"
preinstall_s3_dest: "preinstall.{{{{ cluster_name }}}}.sh"
postinstall_template_orig: "{{{{ cluster_template_dir }}}}/postinstall.j2"
postinstall_src: "{{{{ cluster_data_dir }}}}/postinstall.{{{{ cluster_name }}}}.sh"
postinstall_s3_dest: "postinstall.{{{{ cluster_name }}}}.sh"

# HPC performance test configuration

enable_hpc_performance_tests: {enable_hpc_performance_tests}
perftest_custom_start_number: {perftest_custom_start_number}
perftest_custom_step_size: {perftest_custom_step_size}
perftest_custom_total_tests: {perftest_custom_total_tests}
master_performance_dir_dest: "{{{{ ec2_user_home }}}}/performance/{{{{ cluster_name }}}}/{{{{ cluster_owner }}}}"

# Ganglia support

enable_ganglia: {enable_ganglia}
'''

ebs_encryption = ebs_encryption.lower()

vars_file_part_4 = '''\

# EBS mount definitions

ebs_root: /shared
ebs_settings: custom
ebs_encryption: {ebs_encryption}
ebs_performance_dir: "{{{{ ebs_root }}}}/performance/{{{{ cluster_name }}}}/{{{{ cluster_owner }}}}"
ebs_shared_dir: {ebs_shared_dir}
ebs_shared_volume_size: {ebs_shared_volume_size}
ebs_shared_volume_type: {ebs_shared_volume_type}

# Supported shared storage options:
#   "enable_efs" ==> Elastic File System a.k.a. EFS
#   "enable_external_nfs  ==> External NFS support: onprem NFS
#   "enable_fsx ==> FSx for Lustre

enable_efs: {enable_efs}
enable_external_nfs: {enable_external_nfs}
enable_fsx: {enable_fsx}
'''

vars_file_efs = '''\

# EFS definitions

efs_root: /efs
efs_hpc_performance_dir: "{{{{ efs_root }}}}/performance/{{{{ cluster_name }}}}/{{{{ cluster_owner }}}}"
efs_pkg_dir: "{{{{ efs_root }}}}/pkg"
efs_settings: customfs
efs_encryption: {efs_encryption}
efs_performance_mode: {efs_performance_mode}
efs_throughput_mode: bursting
'''

vars_file_external_nfs = '''\

# External NFS definitions

external_nfs_server: {external_nfs_server}
external_nfs_server_root: /nfs
external_nfs_mount_list_template_orig: "{{{{ cluster_template_dir }}}}/external_nfs_mount_list.j2"
external_nfs_mount_list_template_src: "{{{{ cluster_data_dir }}}}/external_nfs_mount_list.{{ cluster_name }}.conf"
external_nfs_mount_list_template_dest: "external_nfs_mount_list.{{ cluster_name }}.conf"
'''

vars_file_fsx_defs = '''\

# FSx for Lustre (FSxL) definitions

fsx_root: /fsx
fsx_settings: customfs
fsx_size: {fsx_size}
fsx_hpc_performance_dir: "{{{{ fsx_root }}}}/performance/{{{{ cluster_name }}}}/{{{{ cluster_owner }}}}"
fsx_pkg_dir: "{{{{ fsx_root }}}}/pkg"
'''

vars_file_fsx_hydration = '''\
enable_fsx_hydration: {enable_fsx_hydration}
fsx_chunk_size: {fsx_chunk_size}
fsx_hydration_iam_policy: {fsx_hydration_iam_policy}
fsx_s3_import_bucket: {fsx_s3_import_bucket}
fsx_s3_import_path: {fsx_s3_import_path}
fsx_s3_export_bucket: {fsx_s3_export_bucket}
fsx_s3_export_path: {fsx_s3_export_path}
'''

if enable_fsx_hydration == 'true':
    vars_file_fsx = vars_file_fsx_defs + vars_file_fsx_hydration
else:
    vars_file_fsx = vars_file_fsx_defs

vars_file_ebs = '''\

# Vanilla shared EBS HPC performance definitions

ebs_hpc_performance_dir: "{{{{ ebs_shared_dir }}}}/performance/{{{{ cluster_name }}}}/{{{{ cluster_owner }}}}"
'''

# Determine where Spack packages are installed based on the selected shared
# storage option: FSxL > EFS > external NFS > EBS

if (enable_fsx == 'true'):
    vars_file_spack_fsx = '''\

# Spack configuration for FSxL

pkg_dir: "{{{{ fsx_pkg_dir }}}}"
spack_root: "{{{{ fsx_pkg_dir }}}}/spack"
'''

elif (enable_efs == 'true'):
    vars_file_spack_efs = '''\

# Spack configuration for EFS

pkg_dir: "{{{{ efs_pkg_dir }}}}"
spack_root: "{{{{ efs_pkg_dir }}}}/spack"
'''

elif (enable_external_nfs == 'true'):
    vars_file_spack_external_nfs = '''\

# Spack configuration for external NFS

pkg_dir: /nfs/pkg
spack_root: /nfs/pkg/spack
'''

else:
    vars_file_spack_ebs = '''\

# Spack configuration for vanilla shared EBS

pkg_dir: "{{{{ ebs_shared_dir }}}}/pkg"
spack_root: "{{{{ ebs_shared_dir }}}}/pkg/spack"
'''

# Assemble cluster_vars_file based on parameters selected by the user.
# To support maximum flexibility, create sections for cluster_type and
# the various supported shared storage options before writing the final
# version to disk.

# Build vars_file_cluster_type based on cluster_type and scheduler.
# This also controls autoscaling parameters for the cluster.

if cluster_type == 'ondemand':
    vars_file_cluster_type = vars_file_part_1 + vars_file_scheduler + vars_file_part_2 + vars_file_part_3 + vars_file_part_4
if cluster_type == 'spot':
    vars_file_cluster_type = vars_file_part_1 + vars_file_scheduler + vars_file_spot_price + vars_file_part_2 + vars_file_part_3 + vars_file_part_4

# Build vars_file_shared_storage based on whether EFS, Lustre, an external
# NFS server, vanilla shared EBS, or multiple combinations were selected.
# Install Spack based on the selected shared storage options.

# Shared storage = EFS only

if (enable_efs == 'true' and
    enable_external_nfs == 'false' and
    enable_fsx == 'false'):
    vars_file_shared_storage = vars_file_cluster_type + vars_file_efs + vars_file_ebs + vars_file_spack_efs

# Shared storage = External NFS only

if (enable_efs == 'false' and
    enable_external_nfs == 'true' and
    enable_fsx == 'false'):
    vars_file_shared_storage = vars_file_cluster_type + vars_file_external_nfs + vars_file_ebs + vars_file_spack_external_nfs

# Shared storage = FSxL only

if (enable_efs == 'false' and
    enable_external_nfs == 'false' and
    enable_fsx == 'true'):
    vars_file_shared_storage = vars_file_cluster_type + vars_file_fsx + vars_file_ebs + vars_file_spack_fsx

# Shared storage = EFS + External NFS

if (enable_efs == 'true' and
    enable_external_nfs == 'true' and
    enable_fsx == 'false'):
    vars_file_shared_storage = vars_file_cluster_type + vars_file_efs + vars_file_external_nfs + vars_file_ebs + vars_file_spack_efs

# Shared storage = EFS + FSxL

if (enable_efs == 'true' and
    enable_external_nfs == 'false' and
    enable_fsx == 'true'):
    vars_file_shared_storage = vars_file_cluster_type + vars_file_efs + vars_file_fsx + vars_file_ebs + vars_file_spack_fsx

# Shared storage = External NFS + FSxL

if (enable_efs == 'false' and
    enable_external_nfs == 'true' and
    enable_fsx == 'true'):
    vars_file_shared_storage = vars_file_cluster_type + vars_file_external_nfs + vars_file_fsx + vars_file_ebs + vars_file_spack_fsx

# Enable all shared storage options: EFS + External NFS + FSxL

if (enable_efs == 'true' and
    enable_external_nfs == 'true' and
    enable_fsx == 'true'):
    vars_file_shared_storage = vars_file_cluster_type + vars_file_efs + vars_file_external_nfs + vars_file_fsx + vars_file_ebs + vars_file_spack_fsx

# Nothing (use EBS shared storage)

if (enable_efs == 'false' and
    enable_external_nfs == 'false' and
    enable_fsx == 'false'):
    vars_file_shared_storage = vars_file_cluster_type + vars_file_ebs + vars_file_spack_ebs

# Unify all relevant parameters into a single grand final vars_file.
# This is holdover from legacy code and will be deprecated in a future release.

vars_file_grand_final = vars_file_shared_storage
print(vars_file_grand_final.format(**cluster_parameters), file = open(vars_file_path, 'w'))

# Parse the Python3 interpreter path to ensure ParallelCluster stacks can be
# created from either OSX or an EC2 jumphost.

python3_path = subprocess.run(['which','python3'], stdout=subprocess.PIPE).stdout.decode('utf8').rstrip()

# Increase Ansible verbosity when debug_mode is enabled.

if debug_mode == 'true':
    ansible_verbosity = '-vvv'

# Generate the cluster build command string noting that external NFS servers
# are not included unless that functionality is explicitly enabled by the
# HPC operator.

if enable_external_nfs == 'false':
    ansible_build_cmd_string = 'ansible-playbook --extra-vars ' + '"' + 'cluster_name=' + cluster_name + ' cluster_birth_name=' + cluster_birth_name + ' cluster_serial_number=' + cluster_serial_number + ' enable_hpc_performance_tests=' + enable_hpc_performance_tests + ' enable_efs=' + enable_efs + ' enable_external_nfs=false' + ' enable_fsx=' + enable_fsx + ' enable_fsx_hydration=' + enable_fsx_hydration + ' debug_mode=' + debug_mode + ' ansible_python_interpreter=' + python3_path + '"' + ' create_pcluster.yml ' + ansible_verbosity
else:
    ansible_build_cmd_string = 'ansible-playbook --extra-vars ' + '"' + 'cluster_name=' + cluster_name + ' cluster_birth_name=' + cluster_birth_name + ' cluster_serial_number=' + cluster_serial_number + ' enable_hpc_performance_tests=' + enable_hpc_performance_tests + ' enable_efs=' + enable_efs + ' enable_external_nfs=true' + ' external_nfs_server=' + external_nfs_server + ' enable_fsx=' + enable_fsx + ' enable_fsx_hydration=' + enable_fsx_hydration + ' debug_mode=' + debug_mode + ' ansible_python_interpreter=' + python3_path + '"' + ' create_pcluster.yml ' + ansible_verbosity

# Print the config file location and cluster build commands to the console.

if ansible_verbosity:
    if debug_mode == 'true':
        print('debug_mode = enabled')
    print('')
    print('Setting Ansible verbosity to: "' + ansible_verbosity + '"')
print('')
print('View the configuration file for cluster ' + cluster_name + ':')
print('$ cat ' + vars_file_path)
print('')
print('Ready to execute:')
print('$ ' + cluster_build_command)
print('')
print('Preparing to build cluster "' + cluster_name + '" using this command:')
print('$ ' + ansible_build_cmd_string)

# Exit the script, cleanup any orphaned state files, and delete all IAM roles
# and policies associated with this cluster if the operator types 'CTRL-C'
# within 5 seconds after the abort header is displayed.
# If debug_mode is invoked, set the delay interval to 15 seconds.

line_length = 80
if debug_mode == 'true':
    abort_timer = 15
else:
    abort_timer = 5
ctrlC_Abort(abort_timer, line_length, vars_file_path, cluster_serial_number_file, cluster_serial_number, enable_fsx_hydration)

# Create the new cluster stack using the create_pcluster Ansible playbook.

subprocess.run(ansible_build_cmd_string, shell=True)

# Append make-pcluster.py command line and the Ansible playbook command used
# to build the stack to the cluster_serial_number file.

print(ansible_build_cmd_string, file=open(cluster_serial_number_file, 'a'))
print(cluster_build_command, file=open(cluster_serial_number_file, 'a'))

# PUT the cluster_serial_file into s3_bucketname.

cluster_serial_number_object = 'cluster_serial_number' + '/' + cluster_name + '.serial'
s3.Object(s3_bucketname, cluster_serial_number_object).put(Body=open(cluster_serial_number_file, 'rb'))

# Cleanup and exit.

print('Finished creating ParallelCluster stack ' + cluster_name + '!')
print('Exiting...')
sys.exit(0)
