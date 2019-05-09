#!/usr/bin/env python3
#
################################################################################
# Name:		make-pcluster.py
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	April 20, 2019
# Last Changed:	May 10, 2019
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
from datetime import datetime as DateTime
from nested_lookup import nested_lookup
from requests.exceptions import ConnectionError
from validate_email import validate_email

# Import the list of supported EC2 instances and some external functions.
# Source: parallelparallelclustermaker_aux_data.py

from parallelclustermaker_aux_data import default_instance_types
from parallelclustermaker_aux_data import ec2_instances_full_list
from parallelclustermaker_aux_data import illegal_az_msg
from parallelclustermaker_aux_data import is_number
from parallelclustermaker_aux_data import p_val
from parallelclustermaker_aux_data import p_fail
from parallelclustermaker_aux_data import ctrlC_Abort
from parallelclustermaker_aux_data import print_TextHeader
from parallelclustermaker_aux_data import S3Prefix

# Parse input from the command line.

parser = argparse.ArgumentParser(description='make-pcluster.py: Command-line interface to build custom ParallelCluster stacks in AWS')

# Configure parser arguments for the required variables.

parser.add_argument('--cluster_name', '-N', help='name of the cluster (REQUIRED)', required=True)
parser.add_argument('--cluster_owner', '-O', help='username of the cluster owner (REQUIRED)', required=True)
parser.add_argument('--cluster_owner_email', '-E', help='email address of the cluster owner (REQUIRED)', required=True)
parser.add_argument('--az', '--AvailabilityZone', '-A', help='AWS Availability Zone (REQUIRED)', required=True)

# Configure arguments for the optional variables.
# Set reasonable defaults for anything not explicitly defined.

parser.add_argument('--cluster_lifetime', help='automatically terminate the cluster after this time period has elapsed in days:hours:minutes format (default = 30:0:0, i.e. one month)', required=False, default='30:0:0')
parser.add_argument('--prod_level', choices=['dev', 'test', 'stage', 'prod'], help='operating level of the cluster (default = dev)', required=False, default='dev')
parser.add_argument('--base_os', choices=['alinux', 'centos6', 'centos7', 'ubuntu1604'], help='cluster operating system (default = alinux a.k.a. Amazon Linux)', required=False, default='alinux')
parser.add_argument('--custom_ami', help='ID of a Custom AMI to use instead of default published AMIs.  A valid base_os is still required.', required=False, default='NONE')
parser.add_argument('--master_instance_type', help='master EC2 instance type (default = c5.xlarge)', required=False, default='c5.xlarge')
parser.add_argument('--master_root_volume_size', help='master EBS root volume size in GB (default = 250)', required=False, default=250)
parser.add_argument('--compute_instance_type', help='compute EC2 instance type (default = c5.xlarge)', required=False, default='c5.xlarge')
parser.add_argument('--compute_root_volume_size', help='compute EBS root volume size in GB (default = 250)', required=False, default=250)
parser.add_argument('--cluster_type', choices=['ondemand', 'spot'], help='build the cluster with ondemand or spot instances (default = spot)', required=False, default='spot')
parser.add_argument('--placement_group', choices=['NONE', 'DYNAMIC'], help='create a dynamic placement group for this cluster, use with caution (default=NONE)', required=False, default='NONE')
parser.add_argument('--ebs_shared_dir', help='shared EBS file system path (default = /shared)', required=False, default='/shared')
parser.add_argument('--ebs_encryption', choices=['true', 'false'], help='enable EBS encryption (default = false)', required=False, default='false')
parser.add_argument('--ebs_shared_volume_type', choices=['gp2', 'io1', 'st1'], help='EBS volume type (default = gp2)', required=False, default='gp2')
parser.add_argument('--ebs_shared_volume_size', help='EBS shared volume size in GB (default = 250)', required=False, default=250)
parser.add_argument('--hyperthreading', choices=['true', 'false'], help='enable Intel Hyperthreading (default = true)', required=False, default='true')
parser.add_argument('--scheduler', '-S', choices=['sge', 'torque', 'slurm', 'awsbatch'], help='cluster scheduler (default = sge)', required=False, default='sge')
parser.add_argument('--enable_sge_pe', choices=['true', 'false'], help='enable Grid Engine parallel environments (default = true)', required=False, default='true')
parser.add_argument('--sge_pe_type', choices=['make', 'mpi', 'smp'], help='select a Grid Engine parallel environment type (default = smp)', required=False, default='smp')
parser.add_argument('--initial_queue_size', help='initial number of compute nodes to deploy (default = 2)', required=False, default=2)
parser.add_argument('--max_queue_size', help='maximum number of compute nodes to deploy (default = 10)', required=False, default=10)
parser.add_argument('--maintain_initial_size', help='keep initial_queue_size instances always running (default = false)', required=False, default='false')
parser.add_argument('--scaledown_idletime', choices=['true', 'false'], help='amount of time in minutes without a job after which the compute node will terminate (default = 5)', required=False, default=5)
parser.add_argument('--min_vcpus', help='minimum number of vcpus to maintain when using Batch (default = 0)', required=False, default=0)
parser.add_argument('--desired_vcpus', help='initial number of vcpus to deploy when using Batch (default = 4)', required=False, default=4)
parser.add_argument('--max_vcpus', help='maximum number of allowed vcpus when using Batch (default = 20)', required=False, default=20)
parser.add_argument('--enable_external_nfs', choices=['true', 'false'], help='enable support for external NFS file system mounts (default = false)', required=False, default='false')
parser.add_argument('--external_nfs_server', help='set the hostname of the external NFS file system (default = NULL)', required=False, default='')
parser.add_argument('--enable_efs', help='enable support for Elastic File System (EFS) (default = false)', required=False, default='false')
parser.add_argument('--efs_encryption', choices=['true', 'false'], help='enable EFS encryption in transit (default = false)', required=False, default='false')
parser.add_argument('--efs_performance_mode', choices=['make', 'mpi', 'smp'], help='select the EFS performance mode (default = general_purpose)', required=False, default='general_purpose')
parser.add_argument('--enable_fsx', choices=['true', 'false'], help='enable Amazon FSxL for Lustre (default = false)', required=False, default='false')
parser.add_argument('--fsx_size', help='size of the Lustre file system in GB (default = 3600)', required=False, default=3600)
parser.add_argument('--project_id', help='project name or ID number (default = UNDEFINED)', required=False, default='UNDEFINED')
parser.add_argument('--cluster_owner_department', choices=['analytics', 'clinical', 'commercial', 'compbio', 'compchem', 'datasci', 'design', 'development', 'hpc', 'imaging', 'manufacturing', 'medical', 'modeling', 'operations', 'proteomics', 'robotics', 'qa', 'research', 'scicomp'], help='department of the cluster_owner (default = hpc)', required=False, default='hpc')
parser.add_argument('--enable_hpc_performance_tests', choices=['true', 'false'], help='enable the HPC performance tests Axb_random, hashtest, and hashtest_fibonacci under the ec2_user account on the master instance (default = true)', required=False, default='false')
parser.add_argument('--enable_ganglia', choices=['true', 'false'], help='enable Ganglia on the master instance', required=False, default='false')
parser.add_argument('--perftest_custom_start_number', help='starting number of custom performance cluster jobs to submit (default = 10)', required=False, default=10)
parser.add_argument('--perftest_custom_step_size', help='step size of the custom performance qsub scripts (default = 10)', required=False, default=10)
parser.add_argument('--perftest_custom_total_tests', help='number of performance tests to run (default = 10)', required=False, default=10)
parser.add_argument('--turbot_account', '-T', help='Turbot account ID (default = abd).  Set to "disabled" in non-Turbot environments.', required=False, default='disabled')
parser.add_argument('--ansible_verbosity', '-V', help='Set the Ansible verbosity level (default = none)', required=False, default='')
parser.add_argument('--debug_mode', '-D', choices=['true', 'false'], help='Enable debug mode (default = false)', required=False, default='false')

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

# NOTE: Deploying compute instances into private subnets is not currently
# supported so for now, "--use_private_subnet" and "--compute_cidr_subnet"
# are commented out.
#
#use_private_subnet = args.use_private_subnet
#compute_cidr_subnet = args.compute_cidr_subnet

# Set master_instance_type and compute_instance_type to the default values if
# no specific instance_type was provided.  Change these values by editing the
# default_instance_types dictionary defined in parallelclustermaker_aux_data.

if scheduler != 'awsbatch':
    if master_instance_type == 'default':
        master_instance_type = default_master_instance_type
    if compute_instance_type == 'default':
        compute_instance_type =  default_compute_instance_type
else:
    compute_instance_type = 'optimal'

# Define a dictionary of cluster_parameters that require decimal values.

decimal_vals_required = {
    'compute_root_volume_size': compute_root_volume_size,
    'desired_vcpus': desired_vcpus,
    'ebs_shared_volume_size': ebs_shared_volume_size,
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
        print('')
        print('*** ERROR ***')
        print('pcluster stack "' + cluster_owner + '-' + cluster_name + '" is already deployed in ' + region + '!')
        print('')
        print('Please delete this cluster properly and retry the build:')
        print('$ ./kill-pcluster.py -N ' + cluster_name + ' -O ' + cluster_owner + ' -A ' + az)
        print('$ ' + cluster_build_command)
        print('')
        print('Aborting...')
        sys.exit(1)
    else:
        if debug_mode == 'true':
            print('cluster_name successfully validated')

# Check for the presence of an existing vars_file for this cluster.
# If an existing vars_file is found, abort to prevent the potential creation
# of duplicate stacks.

if os.path.isfile(vars_file_path):
    print('')
    print('*** WARNING ***')
    print('As existing vars_file for cluster "' + cluster_owner + '-' + cluster_name + '" was found!')
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
    turbot_profile = 'turbot__' + turbot_account + '__' + instance_owner
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
        print('')
        print('*** ERROR ****')
        print('"' + key + '"' + " must be decimal!")
        print('Current parameter value:')
        print('\t' + key + ' = ' + decimal_vals_required[key])
        print('Aborting...')
        sys.exit(1)

# Perform error checking to ensure external NFS support has been properly
# enabled.

if (enable_external_nfs == 'true') and (external_nfs_server == ''):
    print('')
    print('*** ERROR ***')
    print('Missing: valid setting for "--external_nfs_server"')
    print('Aborting...')
    sys.exit(1)
else:
    p_val('enable_external_nfs', debug_mode)
    p_val('external_nfs_server', debug_mode)

# Set external_nfs_server to a dummy value if external NFS support has not
# been enabled.

if enable_external_nfs == 'false':
    external_nfs_server = 'FEATURE_DISABLED'

# Provide validation for EBS, EFS (performance mode), and FSxL (size).

p_val('ebs_shared_volume_type', debug_mode)
if enable_efs == 'true':
    p_val('efs_performance_mode', debug_mode)
if enable_fsx == 'true':
    p_val('fsx_size', debug_mode)

# FSxL on Ubuntu is not supported by ParallelClusterMaker because the client
# installation process requires rebooting the instance, which breaks the
# ParallelCluster installation process.
#
# This may be addressed in a future release.  For now, perform error checking
# to ensure the operator doesn't try to build an Ubuntu ParallelCluster with
# FSxL support.  If a custom_ami is supplied, however, let the cluster build
# continue.  The operator is responisble for ensuring the correct kernel
# drivers and Lustre client are built into the AMI.

if (enable_fsx == 'true') and (base_os == 'ubuntu1604') and (custom_ami == 'NONE'):
    print('')
    print('*** ERROR ****')
    print('FSxL on Ubuntu is not currently supported by ParallelClusterMaker.')
    print('Please set "--enable_fsx=false" and retry the cluster build or use')
    print('a custom AMI that already includes the correct kernel drivers and')
    print('Lustre client.')
    print('Aborting...')
    sys.exit(1)

# If a custom_ami was provided, perform error checking on its validity.

if custom_ami != 'NONE':
    try:
        custom_ami_information = ec2client.describe_images(ImageIds=[custom_ami])
    except (botocore.exceptions.ClientError):
        print('')
        print('*** ERROR ****')
        print('"' + custom_ami + '" does not appear to be a valid AMI!')
        print('Aborting...')
        sys.exit(1)
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
    print('')
    print('*** ERROR ****')
    print('master_root_volume_size = ' + master_root_volume_size)
    print('compute_root_volume_size = ' + compute_root_volume_size)
    print('ebs_shared_volume_size = ' + ebs_shared_volume_size)
    print('Maximum allowed EBS volume size is 16 TB!')
    print('Aborting...')
    sys.exit(1)

# Generate a cluster_serial_number file to store useful state information
# for each active cluster stack.

SERIAL_DIR = './active_pclusters'
try:
    os.makedirs(SERIAL_DIR)
except OSError as e:
    if e.errno != errno.EEXIST:
        raise

DEPLOYMENT_DATE = time.strftime("%-d.%B.%Y")
Deployed_On = time.strftime("%B %-d, %Y")
serial_datestamp = DateTime.utcnow().strftime('%f%S%M%H%d%m%Y')

cluster_serial_number = cluster_name + '-' + serial_datestamp
cluster_serial_number_file = SERIAL_DIR + '/' + cluster_name + '.serial'

if not os.path.isfile(cluster_serial_number):
    print('%s.%s' % (cluster_name, serial_datestamp), file=open(cluster_serial_number_file, 'w'))
    print(' '.join(sys.argv), file=open(cluster_serial_number_file, 'a'))

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

# Perform error checking against the auto-generated name for s3_bucketname.
# If the bucket doesn't exist, create it during the cfncluster stack build.

s3_bucketname = 'parallelclustermaker-' + cluster_name + '-' + serial_datestamp

s3 = boto3.resource('s3')
if s3.Bucket(s3_bucketname) not in s3.buckets.all():
    p_val('s3_bucketname', debug_mode)
else:
    print('')
    print('*** ERROR***')
    print('Found a duplicate S3 bucket that should be associated with this')
    print('cfncluster stack.  Normally, this would be treated like a violation')
    print('of the laws of physics but if everything looks good, proceed ahead')
    print ('with the build.')

# Perform a minimal check to ensure ebs_shared_dir looks like a valid path.

if ebs_shared_dir.startswith('/'):
    p_val('ebs_shared_dir', debug_mode)
else:
    print('ebs_shared_dir = ' + ebs_shared_dir)
    print('')
    print('*** ERROR ****')
    print('"' + ebs_shared_dir+ '"' ' does not appear to be a Unix file path!')
    print('Try using "/' + ebs_shared_dir + '" instead.')
    print('Aborting...')
    sys.exit(1)

# Perform a minimal check to ensure cluster_owner_email resembles a valid
# email address.

if validate_email(cluster_owner_email):
    p_val('cluster_owner_email', debug_mode)
else:
    print('')
    print('cluster_owner_email = ' + cluster_owner_email)
    print('*** ERROR ****')
    print('This does not appear to be a valid email address!')
    print('Please refer to: https://en.wikipedia.org/wiki/Email_address')
    print('Aborting...')
    sys.exit(1)

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

stsclient = boto3.client('sts', region_name = region)
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

# Validate cluster_type.  Abort if an unsupported option is chosen.
# Compute EC2 spot prices from: https://aws.amazon.com/ec2/spot/pricing/
# Add 33% to raw_spot_price to protect against marketplace fluctuations.

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
        raw_spot_price = float(prices['SpotPriceHistory'][0]['SpotPrice'])
        spot_price = round(raw_spot_price + ((1 / 3) * raw_spot_price), 8)
    else:
        raw_spot_price = 'UNDEFINED'
        spot_price = 'UNDEFINED'
    p_val('spot_price', debug_mode)
else:
    p_fail(cluster_type, 'cluster_type', cluster_type_allowed)

# Define the cluster_parameters dictionary.
# This data is needed to build the vars_file.
#
# Reminder: deploying compute instances into private subnets is not (yet)
# supported.  Leave these lines commented out:
#
#    'use_private_subnet': use_private_subnet,
#    'compute_cidr_subnet': compute_cidr_subnet,

cluster_parameters = {
    'cluster_name': cluster_name,
    'DEPLOYMENT_DATE': DEPLOYMENT_DATE,
    'Deployed_On': Deployed_On,
    'serial_datestamp': serial_datestamp,
    'cluster_serial_number_file': cluster_serial_number_file,
    'cluster_lifetime': cluster_lifetime,
    'prod_level': prod_level,
    'cluster_type': cluster_type,
    'placement_group': placement_group,
    'raw_spot_price': raw_spot_price,
    'spot_price': spot_price,
    'master_instance_type': master_instance_type,
    'master_root_volume_size': master_root_volume_size,
    'compute_instance_type': compute_instance_type,
    'compute_root_volume_size': compute_root_volume_size,
    'ebs_shared_dir': ebs_shared_dir,
    'ebs_shared_volume_size': ebs_shared_volume_size,
    'ebs_shared_volume_type': ebs_shared_volume_type,
    'ebs_encryption': ebs_encryption,
    's3_bucketname': s3_bucketname,
    'hyperthreading': hyperthreading,
    'scheduler': scheduler,
    'enable_sge_pe': enable_sge_pe,
    'sge_pe_type': sge_pe_type,
    'initial_queue_size': initial_queue_size,
    'maintain_initial_size': maintain_initial_size,
    'max_queue_size': max_queue_size,
    'min_vcpus': min_vcpus,
    'desired_vcpus': desired_vcpus,
    'max_vcpus': max_vcpus,
    'scaledown_idletime': scaledown_idletime,
    'base_os': base_os,
    'custom_ami': custom_ami,
    'ec2_user': ec2_user,
    'ec2_user_home': ec2_user_home,
    'enable_efs': enable_efs,
    'efs_encryption': efs_encryption,
    'efs_performance_mode': efs_performance_mode,
    'enable_external_nfs': enable_external_nfs,
    'external_nfs_server': external_nfs_server,
    'enable_fsx': enable_fsx,
    'fsx_size': fsx_size,
    'aws_account_id': aws_account_id, 
    'region': region,
    'az': az,
    'vpc_id': vpc_id,
    'vpc_name': vpc_name,
    'subnet_id': subnet_id,
    'use_private_compute_subnet': use_private_compute_subnet,
    'private_compute_cidr_subnet': private_compute_cidr_subnet,
    'private_compute_subnet_id': private_compute_subnet_id,
    'cluster_owner': cluster_owner,
    'cluster_owner_email': cluster_owner_email,
    'cluster_owner_department': cluster_owner_department,
    'project_id': project_id,
    'perftest_custom_start_number': perftest_custom_start_number,
    'perftest_custom_step_size': perftest_custom_step_size,
    'perftest_custom_total_tests': perftest_custom_total_tests,
    'enable_hpc_performance_tests': enable_hpc_performance_tests,
    'enable_ganglia': enable_ganglia
}

# Print the current values of all validated cluster_parameters to the console.

if debug_mode == 'true':
    print_TextHeader(cluster_name, 'Displaying cluster parameter values', 80)
    print('cluster_name = ' + cluster_name)
    if cluster_lifetime:
        print('cluster_lifetime (days:hours:minutes) = ' + str(cluster_lifetime))
    print('cluster_serial_number = ' + cluster_serial_number)
    print('cluster_type = ' + cluster_type)
    if cluster_type == 'spot':
        if 'UNDEFINED' not in spot_price:
            print('spot_price = $' + str(spot_price) + ' per hour')
    if placement_group != 'NONE':
        print('placement_group = ' + placement_group)
    print('prod_level = ' + prod_level)
    print('base_os = ' + base_os)
    print('ec2_user = ' + ec2_user)
    print('ec2_user_home = ' + ec2_user_home)
    if custom_ami != 'NONE':
        print('custom_ami = ' + custom_ami)
    print('aws_account_id = ' + aws_account_id)
    print('region = ' + region)
    print('vpc_id = ' + vpc_id)
    print('vpc_name = ' + vpc_name)
    print('subnet_id = ' + subnet_id)
    if use_private_compute_subnet == 'true':
        print('use_private_compute_subnet = ' + use_private_compute_subnet)
        print('private_compute_cidr_subnet = ' + private_compute_cidr_subnet)
        print('private_compute_subnet_id = ' + private_compute_subnet_id)
    print('scheduler = ' + scheduler)
    if scheduler == 'sge':
        print('enable_sge_pe = ' + enable_sge_pe)
        print('sge_pe_type = ' + sge_pe_type)
    if scheduler == 'batch':
        print('min_vcpus = ' + min_vcpus)
        print('desired_vcpus = ' + desired_vcpus)
        print('max_vcpus = ' + max_vcpus)
    print('master_instance_type = ' + master_instance_type)
    print('master_root_volume_size = ' + str(master_root_volume_size) + ' GB')
    print('compute_instance_type = ' + compute_instance_type)
    print('compute_root_volume_size = ' + str(compute_root_volume_size) + ' GB')
    print('hyperthreading = ' + hyperthreading)
    print('initial_queue_size = ' + str(initial_queue_size))
    print('max_queue_size = ' + str(max_queue_size))
    print('scaledown_idletime = ' + str(scaledown_idletime))
    print('ebs_shared_dir = ' + ebs_shared_dir)
    print('ebs_shared_volume_size = ' + str(ebs_shared_volume_size) + ' GB')
    print('ebs_shared_volume_type = ' + str(ebs_shared_volume_type))
    print('ebs_encryption = ' + str(ebs_encryption))
    print('s3_bucketname = s3://' + s3_bucketname)
    if enable_external_nfs == 'true':
        print('enable_external_nfs = ' + enable_external_nfs)
        print('external_nfs_server = ' + external_nfs_server)
    if enable_efs == 'true':
        print('enable_efs = ' + enable_efs)
        print('efs_encryption = ' + efs_encryption)
        print('efs_performance_mode = ' + efs_performance_mode)
    if enable_fsx == 'true':
        print('enable_fsx = ' + enable_fsx)
        print('fsx_size = ' + str(fsx_size) + ' GB')
    print('cluster_owner = ' + cluster_owner)
    print('cluster_owner_email = ' + cluster_owner_email)
    print('cluster_owner_department = ' + cluster_owner_department)
    if project_id != 'UNDEFINED':
        print('project_id = ' + project_id)
    if enable_hpc_performance_tests:
        print('enable_hpc_performance_tests = ' + enable_hpc_performance_tests)
        print('perftest_custom_start_number = ' + str(perftest_custom_start_number))
        print('perftest_custom_step_size = ' + str(perftest_custom_step_size))
        print('perftest_custom_total_tests = ' + str(perftest_custom_total_tests))
    if enable_ganglia:
        print('enable_ganglia = ' + enable_ganglia)

# Generate the vars_file for this cluster.

vars_file_part_1 = '''\
################################################################################
# Name:         {cluster_name}.yml
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Created On:   April 20, 2019
# Last Changed: April 30, 2019
# Deployed On:  {Deployed_On}
# Purpose:      ParallelCluster configuration for cluster "{cluster_name}"
# Notes:	Automatically generated by ParallelClusterMaker
################################################################################

# Metadata tags for cluster identification

cluster_owner: {cluster_owner}
cluster_owner_department: {cluster_owner_department}
cluster_owner_email: {cluster_owner_email}
project_id: {project_id}
prod_level: {prod_level}
serial_datestamp: {serial_datestamp}
cluster_serial_number_file: {cluster_serial_number_file}
DEPLOYMENT_DATE: {DEPLOYMENT_DATE}

# Master and Compute instance definitions

base_os: {base_os}
custom_ami: {custom_ami}
master_instance_type: {master_instance_type}
master_root_volume_size: {master_root_volume_size}
compute_instance_type: {compute_instance_type}
compute_root_volume_size: {compute_root_volume_size}
hyperthreading: {hyperthreading}

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
serverless_stage_dir: "{{{{ cluster_data_dir }}}}/serverless"
serverless_template_dir: "{{{{ local_workingdir }}}}/serverless/kill_pcluster"

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

cluster_config_template: "{{{{ cluster_data_dir }}}}/config.{{{{ cluster_name }}}}"
cluster_config_dest: config.{{{{ cluster_name }}}}
cluster_config_template_orig: "{{{{ cluster_template_dir }}}}/config.pcluster.j2"
cluster_lifetime: "{cluster_lifetime}"
cluster_type: {cluster_type}
placement_group: {placement_group}
scaling_settings: custom
scaledown_idletime: {scaledown_idletime}
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
preinstall_s3_dest: "{{{{ cluster_name }}}}-preinstall.sh"
postinstall_template_orig: "{{{{ cluster_template_dir }}}}/postinstall.j2"
postinstall_src: "{{{{ cluster_data_dir }}}}/postinstall.{{{{ cluster_name }}}}.sh"
postinstall_s3_dest: "{{{{ cluster_name }}}}-postinstall.sh"

# HPC performance test configuration

enable_hpc_performance_tests: {enable_hpc_performance_tests}
perftest_custom_start_number: {perftest_custom_start_number}
perftest_custom_step_size: {perftest_custom_step_size}
perftest_custom_total_tests: {perftest_custom_total_tests}
Axb_random_src: "{{{{ performance_rootdir }}}}"
Axb_random_dest: "{{{{ ec2_user_home }}}}/performance/{{{{ cluster_owner }}}}/{{{{ cluster_name }}}}"

# Ganglia support

enable_ganglia: {enable_ganglia}
'''

ebs_encryption = ebs_encryption.lower()

vars_file_part_4 = '''\

# EBS mount definitions

ebs_root: /shared
ebs_settings: custom
ebs_encryption: {ebs_encryption}
ebs_shared_dir: {ebs_shared_dir}
ebs_shared_volume_size: {ebs_shared_volume_size}
ebs_shared_volume_type: {ebs_shared_volume_type}
ebs_performance_dir: "{{{{ ebs_root }}}}/performance/{{{{ cluster_owner }}}}/{{{{ cluster_name }}}}"

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
# Please see the documentation to understand why selecting efs_throughput_mode
# is disabled.

efs_root: /efs
efs_fs_pcluster: efs_pcluster_{{{{ cluster_name }}}}
efs_temp_dir: /tmp/efs/{{{{ cluster_name }}}}
efs_temp_file: "{{{{ efs_temp_dir }}}}/{{{{ efs_fs_pcluster }}}}.fsid"
efs_pkg_dir: "{{{{ efs_root }}}}/pkg"
efs_encryption: {efs_encryption}
efs_performance_mode: {efs_performance_mode}
efs_settings: customfs
efs_fs_performance: efs_performance_{{{{ cluster_name }}}}
efs_hpc_performance_dir: "{{{{ efs_root }}}}/performance/{{{{ cluster_owner }}}}/{{{{ cluster_name }}}}"
'''

vars_file_external_nfs = '''\

# External NFS definitions

external_nfs_server_root: /nfshpc
#external_nfs_pkg: nfshpc
external_nfs_server: {external_nfs_server}
external_nfs_pkg_dir: "{{{{ external_nfs_server_root }}}}/pkg"
external_nfs_hpc_performance_dir: "{{{{ external_nfs_performance }}}}/{{{{ cluster_owner }}}}/{{{{ cluster_name }}}}"
'''

vars_file_fsx = '''\

# FSx for Lustre (FSxL) definitions

fsx_size: {fsx_size}
fsx_root: /fsx
fsx_pkg_dir: "{{{{ fsx_root }}}}/pkg"
fsx_temp_dir: /tmp/fsx/{{{{ cluster_name }}}}
fsx_dns_name_file: fsx_dns_name_{{{{ cluster_name }}}}.file
fsx_dns_name_object: fsx_dns_name_{{{{ cluster_name }}}}.s3
fsx_fsid_file: fsx_fsid_{{{{ cluster_name }}}}.file
fsx_fsid_object: fsx_fsid_{{{{ cluster_name }}}}.s3
fsx_create_fs_src: "{{{{ cluster_template_dir }}}}/create_fsx_fs.sh.j2"
fsx_create_fs_script: "{{{{ cluster_data_dir }}}}/create_fsx_fs.{{{{ cluster_name }}}}.sh"
fsx_create_fs_object: create_fsx_fs.{{{{ cluster_name }}}}.sh
fsx_delete_fs_src: "{{{{ cluster_template_dir }}}}/delete_fsx_fs.sh.j2"
fsx_delete_fs_script: "{{{{ cluster_data_dir }}}}/delete_fsx_fs.{{{{ cluster_name }}}}.sh"
fsx_delete_fs_object: delete_fsx_fs.{{{{ cluster_name }}}}.sh
fsx_hpc_performance_dir: "{{{{ fsx_root }}}}/performance/{{{{ cluster_owner }}}}/{{{{ cluster_name }}}}"
'''

vars_file_ebs = '''\

# Vanilla shared EBS HPC performance definitions

ebs_hpc_performance_dir: "{{{{ ebs_shared_dir }}}}/performance/{{{{ cluster_owner }}}}/{{{{ cluster_name }}}}"
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

pkg_dir: /nfshpc/pkg
spack_root: /nfshpc/pkg/spack
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

# This value became redundant when ec2_iam_role functionality was removed.
# For now, leave vars_file_grand_final alone.

vars_file_grand_final = vars_file_shared_storage

print(vars_file_grand_final.format(**cluster_parameters), file = open(vars_file_path, 'w'))

# Parse the Python3 interpreter path to ensure ParallelCluster stacks can be
# created from either OSX or an EC2 jumphost.

python3_path = subprocess.run(['which','python3'], stdout=subprocess.PIPE).stdout.decode('utf8').rstrip()

# Increase Ansible verbosity when debug_mode is enabled.

if debug_mode == 'true':
    ansible_verbosity = '-vvv'

# Generate the cluster build command string.  Don't include the external NFS
# server if that functionality is not explicitly enabled by the operator.

if enable_external_nfs == 'false':
    build_cmd_string = 'ansible-playbook --extra-vars ' + '"' + 'cluster_name=' + cluster_name + ' cluster_birth_name=' + cluster_birth_name + ' cluster_serial_number=' + cluster_serial_number + ' enable_hpc_performance_tests=' + enable_hpc_performance_tests + ' enable_efs=' + enable_efs + ' enable_external_nfs=false' + ' enable_fsx=' + enable_fsx + ' ansible_python_interpreter=' + python3_path + '"' + ' create_pcluster.yml ' + ansible_verbosity
else:
    build_cmd_string = 'ansible-playbook --extra-vars ' + '"' + 'cluster_name=' + cluster_name + ' cluster_birth_name=' + cluster_birth_name + ' cluster_serial_number=' + cluster_serial_number + ' enable_hpc_performance_tests=' + enable_hpc_performance_tests + ' enable_efs=' + enable_efs + ' enable_external_nfs=true' + ' external_nfs_server=' + external_nfs_server + ' enable_fsx=' + enable_fsx + ' ansible_python_interpreter=' + python3_path + '"' + ' create_pcluster.yml ' + ansible_verbosity

# Print the config file location and cluster build commands to the console.

print('')
print('View the configuration file for cluster ' + cluster_name + ':')
print('$ cat ' + vars_file_path)
print('')
print('Ready to execute:')
print('$ ' + cluster_build_command)
print('')
print('Preparing to build cluster "' + cluster_name + '" using this command:')
print('$ ' + build_cmd_string)

# Exit the script and cleanup any orphaned state files if the operator types
# 'CTRL-C' within 5 seconds after the abort header is displayed.

ctrlC_Abort(5, 80, vars_file_path, cluster_serial_number_file)

# Create the new cluster stack using the create_pcluster Ansible playbook.

subprocess.run(build_cmd_string, shell=True)

# Append make-pcluster.py command line and the Ansible playbook command used
# to build the stack to the cluster_serial_number file.

print(build_cmd_string, file=open(cluster_serial_number_file, 'a'))
print(cluster_build_command, file=open(cluster_serial_number_file, 'a'))

# PUT the cluster_serial_file into s3_bucketname.

cluster_serial_number_object = 'cluster_serial_number' + '/' + cluster_name + '.serial'
s3.Object(s3_bucketname, cluster_serial_number_object).put(Body=open(cluster_serial_number_file, 'rb'))

# Cleanup and exit.

print('Finished creating ParallelCluster stack ' + cluster_name + '!')
print('Exiting...')
sys.exit(0)
