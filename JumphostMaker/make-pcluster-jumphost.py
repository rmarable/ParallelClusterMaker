#!/usr/bin/env python3
#
################################################################################
# Name:         make-pcluster-jumphost.py
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Created On:   April 18, 2019
# Last Changed: May 11, 2019
# Purpose:      Create an EC2 jumphost to run the ParallelClusterMaker toolkit
################################################################################

# Load the required Python libraries.

import argparse
import boto3
import botocore
import errno
import json
import os
import shutil
import subprocess
import sys
import time
from botocore.exceptions import ClientError
from nested_lookup import nested_lookup

# Import some external lists and functions.
# Source: jumphostmaker_aux_data.py

from jumphostmaker_aux_data import add_security_group_rule
from jumphostmaker_aux_data import get_ami_info
from jumphostmaker_aux_data import illegal_az_msg
from jumphostmaker_aux_data import p_val
from jumphostmaker_aux_data import p_fail
from jumphostmaker_aux_data import ctrlC_Abort
from jumphostmaker_aux_data import print_TextHeader

# Parse input from the command line.

parser = argparse.ArgumentParser(description='make-pcluster-jumphost.py: Command-line interface to build EC2 pcluster-jumphosts')

# Configure parser arguments for the required variables.

parser.add_argument('--az', '--AvailabilityZone', '-A', help='AWS Availability Zone (REQUIRED)', required=True)
parser.add_argument('--instance_name', '-N', help='name of the pcluster-jumphost (REQUIRED)', required=True)
parser.add_argument('--instance_owner', '-O', help='ActiveDirectory username of the pcluster-jumphost instance_owner (REQUIRED)', required=True)
parser.add_argument('--instance_owner_email', '-E', help='email address of the pcluster-jumphost instance_owner (REQUIRED)', required=True)

# Configure arguments for the optional parameters.
# Set reasonable defaults for anything that is not explicitly defined.

parser.add_argument('--instance_owner_department', choices=['analytics', 'clinical', 'commercial', 'compbio', 'compchem', 'datasci', 'design', 'development', 'hpc', 'imaging', 'manufacturing', 'medical', 'modeling', 'operations', 'proteomics', 'robotics', 'qa', 'research', 'scicomp'], help='department of the instance_owner (default = hpc)', required=False, default='hpc')
parser.add_argument('--project_id', '-P', help='project name or ID number (default = UNDEFINED)', required=False, default='UNDEFINED')
parser.add_argument('--security_group', help='primary security group for the EC2 pcluster-jumphost (default = pcluster_jumphost)', required=False, default='pcluster_jumphost')
parser.add_argument('--turbot_account', '-T', help='Turbot account ID (default = abd).  Set to "disabled" in non-Turbot environments.', required=False, default='disabled')
parser.add_argument('--ansible_verbosity', '-V', help='Set the Ansible verbosity level (default = none)', required=False, default='')
parser.add_argument('--debug_mode', '-D', choices=['true', 'false'], help='Enable debug mode (default = false)', required=False, default='false')

# Create variables from optional instance_parameters provided via command line.

args = parser.parse_args()
ansible_verbosity = args.ansible_verbosity
az = args.az
debug_mode = args.debug_mode
instance_name = args.instance_name
instance_owner = args.instance_owner
instance_owner_email = args.instance_owner_email
instance_owner_department = args.instance_owner_department
project_id = args.project_id
region = az[:-1]
security_group = args.security_group
turbot_account = args.turbot_account

# Get the version of Terraform being used to build the pcluster-jumphost.
# Abort if Terraform is not installed.

terraform_version_string = "terraform -version | head -1 | awk '{print $2}'"

TERRAFORM_VERSION = subprocess.check_output(terraform_version_string, shell=True, universal_newlines=True, stderr=subprocess.DEVNULL)

if not TERRAFORM_VERSION:
    print('')
    print('***ERROR***')
    print('Terraform is missing!')
    print('Please visit https://www.terraform.io/downloads for installation guidance.')
    print('Aborting...')
    sys.exit(1)

# Get the version of Ansible being used to build the pcluster-jumphost.
# Abort if Ansible is not installed.

ansible_version_string = "ansible --version | head -1 | awk '{print $2}' | tr -d '\n'"

ANSIBLE_VERSION = subprocess.check_output(ansible_version_string, shell=True, universal_newlines=True, stderr=subprocess.DEVNULL)

if not ANSIBLE_VERSION:
    print('')
    print('***ERROR***')
    print('Ansible is missing!')
    print('Please review the Ansible documentation for installation guidance:')
    print('https://bit.ly/2KHuyY5')
    print('Aborting...')
    sys.exit(1)

# Set some critical environment variables to support Turbot.
# https://turbot.com/about/

if turbot_account != 'disabled':
    turbot_profile = 'turbot__' + turbot_account + '__' + instance_owner
    os.environ['AWS_PROFILE'] = turbot_profile
    os.environ['AWS_DEFAULT_REGION'] = region
    boto3.setup_default_session(profile_name=turbot_profile)

# Set the vars_file_path.

vars_file_path = './vars_files/' + instance_name + '.yml'

# Create the vars_file directory if it does not already exist.

cwd = os.getcwd()
try:
    os.makedirs('./vars_files')
except OSError as e:
    if e.errno != errno.EEXIST:
        raise

# Check for the presence of an existing vars_file for this pcluster-jumphost.
# If an existing vars_file exists, abort to prevent potential duplications.

if os.path.isfile(vars_file_path):
    print('')
    print('  ERROR  '.center(80, '*'))
    print(('  Found an existing ' + vars_file_path + ' ').center(80,'-'))
    print('')
    print('Please delete this file and retry the build:')
    print('')
    print('$ rm ' + vars_file_path)
    print('$ ' + ' '.join(sys.argv))
    print('')
    print('Aborting...')
    sys.exit(1)
else:
    if debug_mode == 'true':
        print_TextHeader(instance_name, 'Validating', 80)
    else:
        print('')
        print('Performing parameter validation...')
        print('')
    p_val('vars_file_path', debug_mode)

# Check for the presence of an existing instance_data directory for this
# pcluster-jumphost.

try:
    os.makedirs('./pcluster_jumphost_data/' + instance_name)
except OSError as e:
    if e.errno != errno.EEXIST:
        raise

# Set the state directory for this pcluster-jumphost.

instance_data_dir = './pcluster_jumphost_data/' + instance_name + '/'

# Generate a instance_serial_number to track individual pcluster-jumphosts.

DEPLOYMENT_DATE = time.strftime("%-d.%B.%Y")
TIMESTAMP = time.strftime("%s")
SERIAL_DIR = './active_pcluster_jumphosts'
try:
    os.makedirs(SERIAL_DIR)
except OSError as e:
    if e.errno != errno.EEXIST:
        raise
serial_datestamp = time.strftime("%S%M%H%d%m%Y")
instance_serial_number = instance_name + '-' + serial_datestamp
instance_serial_number_file = SERIAL_DIR + '/' + instance_name + '.serial'

if not os.path.isfile(instance_serial_number):
    print('%s.%s' % (instance_name, serial_datestamp), file=open(instance_serial_number_file, 'w'))
    print(' '.join(sys.argv), file=open(instance_serial_number_file, 'a'))

p_val('instance_serial_number', debug_mode)
p_val('instance_serial_number_file', debug_mode)

# Select t2.micro as the EC2 instance type.

ec2_instance_type = 't2.micro'
p_val('ec2_instance_type', debug_mode)

# Set the EBS volume type to gp2.

ebs_volume_type = 'gp2'
ebs_optimized = 'False'
instance_root_volume_size = 8
p_val('ebs_volume_type', debug_mode)

# Perform error checking on the selected AWS Region and Availability Zone.
# Abort if a non-existent Availability Zone was chosen.

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

# Parse the AWS Account ID.

stsclient = boto3.client('sts', region_name = region)
aws_account_id = stsclient.get_caller_identity()["Account"]

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

# Perform error checking on the selected security_group.
# If the user fails to supply a valid security_group, create a new one with
# ingress rules appropriate for accessing this pcluster-jumphost.

ec2 = boto3.resource('ec2', region_name = region)

filters = [ { 'Name': 'group-name', 'Values': [ security_group, ] }, ]
sg_id = list(ec2.security_groups.filter(Filters=filters))
if not sg_id:
    security_group_name = security_group
    security_group = ec2.create_security_group(
        GroupName=security_group_name,
        Description='pcluster-jumphost EC2 security group',
        VpcId=vpc_id
    )
    add_security_group_rule(region, security_group, "tcp", "0.0.0.0/0", 22, 22)
    sg_id = list(ec2.security_groups.filter(Filters=filters))
p_val('security_group', debug_mode)
v_sg_id = str(*sg_id).split("'")
vpc_security_group_ids = v_sg_id[1]
p_val('vpc_security_group_ids', debug_mode)

# Set base_os to alinux2 (Amazon Linux 2) and parse the appropriate aws_ami
# for the selected region.

base_os = 'alinux2'
ec2_user = 'ec2-user'
ec2_user_home = '/home/' + ec2_user
aws_ami = get_ami_info(base_os, region)
p_val('aws_ami', debug_mode)
p_val('base_os', debug_mode)

# Create a unique SNS topic name for ParallelClusterMaker jumphost events and
# subscribe instance_owner_email.

sns_client = boto3.client('sns')
sns_topic_name = 'ParallelClusterMaker_Jumphost_SNS_Alerts_' + str(instance_serial_number)
sns_topic = sns_client.create_topic(Name=sns_topic_name)
sns_topic_arn = sns_topic['TopicArn']
sns_subscription = sns_client.subscribe(
    TopicArn=sns_topic_arn,
    Protocol='email',
    Endpoint=instance_owner_email
    )
print('Subscribed ' + instance_owner_email + ' to SNS topic: ' + sns_topic_name)
p_val('sns_topic_name', debug_mode)

# Create a new EC2 key pair and PEM file for the pcluster-jumphost within
# the deployment region of choice if either doesn't already exist.

ec2_keypair = 'pcluster-jumphost_' + instance_owner + '_' + instance_name + '_' + region
secret_key_file = instance_data_dir + ec2_keypair + '.pem'

try:
    ec2_keypair_status = ec2client.describe_key_pairs(KeyNames=[ec2_keypair])
    print('Found EC2 keypair: ' + ec2_keypair)
except ClientError as e:
    if e.response['Error']['Code'] == 'InvalidKeyPair.NotFound':
        new_ec2_keypair = ec2client.create_key_pair(KeyName=ec2_keypair)
        print(new_ec2_keypair['KeyMaterial'], file=open(secret_key_file, 'w'))
        subprocess.run('chmod 0600 ' + secret_key_file, shell=True)

if not os.path.isfile(secret_key_file):
    print('')
    print('***ERROR***')
    print('Missing: ' + secret_key_file)
    print('')
    print('Please resolve this issue and retry, perhaps deleting the original keypair by')
    print('pasting this command into the shell:')
    print('')
    print('$ aws --region ' + region + ' ec2 delete-key-pair --key-name ' + ec2_keypair)
    print('')
    print('Aborting...')
    sys.exit(1)
else:
    p_val('ec2_keypair', debug_mode)

# Create an IAM EC2 instance profile for the pcluster-jumphost if it does not
# already exist.

iam = boto3.client('iam')
iam_instance_policy = 'parallelclustermaker-policy-' + instance_serial_number
iam_instance_profile = 'parallelclustermaker-profile-' + instance_serial_number
iam_instance_role = 'parallelclustermaker-role-' + instance_serial_number
iam_json_policy_src = 'templates/ParallelClusterInstancePolicy.json_src'
iam_json_policy_template = instance_data_dir + 'ParallelClusterInstancePolicy.json'

try:
    check_role = iam.get_role(RoleName=iam_instance_role)
    print('Found IAM EC2 instance role: ' + iam_instance_role)
except ClientError as e:
    if e.response['Error']['Code'] == 'NoSuchEntity':
        with open(iam_json_policy_src, 'r') as ec2_instance_role_src:
            filedata = ec2_instance_role_src.read()
            ec2_instance_role_src.close()
        with open(iam_json_policy_template, 'w') as ec2_instance_role_dest:
            ec2_instance_role_dest.write(filedata)
            ec2_instance_role_dest.close()
        pcluster_jumphost_ec2_instance_role = iam.create_role(
            RoleName=iam_instance_role,
            AssumeRolePolicyDocument='{ "Version": "2012-10-17", "Statement": [ { "Effect": "Allow", "Principal": { "Service": [ "batch.amazonaws.com", "ec2.amazonaws.com", "ecs-tasks.amazonaws.com", "spotfleet.amazonaws.com" ] }, "Action": "sts:AssumeRole" } ] }',
            Description='ParallelClusterMaker EC2 instance role'
            )
        with open(iam_json_policy_template, 'r') as policy_input:
            pcluster_jumphost_ec2_policy = iam.put_role_policy(
                RoleName=iam_instance_role,
                PolicyName=iam_instance_policy,
                PolicyDocument=policy_input.read()
                )
        print('Created EC2 instance role: ' + iam_instance_role)

try:
    check_profile = iam.get_instance_profile(InstanceProfileName=iam_instance_profile)
    print('Found IAM EC2 instance profile: ' + iam_instance_profile)
except ClientError as e:
    if e.response['Error']['Code'] == 'NoSuchEntity':
        pcluster_jumphost_ec2_instance_profile = iam.create_instance_profile(InstanceProfileName=iam_instance_profile)
        print('Created EC2 instance profile: ' + iam_instance_profile)
        pcluster_jumphost_add_ec2_role_to_instance_profile = iam.add_role_to_instance_profile(
            InstanceProfileName=iam_instance_profile,
            RoleName=iam_instance_role
            )
        print('Added: ' + iam_instance_role + ' to ' + iam_instance_profile)

if debug_mode == 'true':
    p_val('iam_instance_role', debug_mode)
    p_val('iam_instance_profile', debug_mode)

# Define the instance_parameters dictionary for populating the vars_file.

instance_parameters = {
    'az': az,
    'aws_ami': aws_ami,
    'aws_account_id': aws_account_id,
    'base_os': base_os,
    'ebs_optimized': ebs_optimized,
    'ebs_volume_type': ebs_volume_type,
    'ec2_instance_type': ec2_instance_type,
    'ec2_keypair': ec2_keypair,
    'ec2_user': ec2_user,
    'ec2_user_home': ec2_user_home,
    'iam_instance_policy': iam_instance_policy,
    'iam_instance_profile': iam_instance_profile,
    'iam_instance_role': iam_instance_role,
    'instance_owner': instance_owner,
    'instance_owner_email': instance_owner_email,
    'instance_owner_department': instance_owner_department,
    'instance_name': instance_name,
    'instance_root_volume_size': instance_root_volume_size,
    'instance_serial_number': instance_serial_number,
    'instance_serial_number_file': instance_serial_number_file,
    'project_id': project_id,
    'region': region,
    'security_group': security_group,
    'vpc_security_group_ids': vpc_security_group_ids,
    'sns_topic_arn': sns_topic_arn,
    'subnet_id': subnet_id,
    'turbot_account': turbot_account,
    'vars_file_path': vars_file_path,
    'vpc_id': vpc_id,
    'vpc_name': vpc_name,
    'DEPLOYMENT_DATE': DEPLOYMENT_DATE,
    'ANSIBLE_VERSION': ANSIBLE_VERSION,
    'TERRAFORM_VERSION': TERRAFORM_VERSION
}

# Print the current values of all defined instance_parameters to the console
# when debug_mode is enabled.

if debug_mode == 'true':
    print_TextHeader(instance_name, 'Printing', 80)
    print('aws_account_id = ' + aws_account_id)
    if turbot_account != 'disabled':
        print('turbot_account = ' + turbot_account)
    print('aws_ami = ' + str(aws_ami))
    print('base_os = ' + base_os)
    print('ebs_optimized = ' + str(ebs_optimized))
    print('ebs_volume_type = ' + ebs_volume_type)
    print('ec2_instance_type = ' + ec2_instance_type)
    print('ec2_keypair = ' + ec2_keypair)
    print('ec2_user = ' + ec2_user)
    print('ec2_user_home = ' + ec2_user_home)
    print('instance_name = ' + instance_name)
    print('instance_owner = ' + instance_owner)
    print('instance_owner_email = ' + instance_owner_email)
    print('instance_owner_department = ' + instance_owner_department)
    if project_id != 'UNDEFINED':
        print('project_id = ' + project_id)
    print('instance_root_volume_size = ' + str(instance_root_volume_size) + ' GB')
    print('region = ' + region)
    print('az = ' + az)
    print('security_group = ' + str(security_group))
    if iam_instance_profile:
        print('iam_instance_policy = ' + iam_instance_policy)
        print('iam_instance_profile = ' + iam_instance_profile)
        print('iam_instance_role = ' + iam_instance_role)
    print('vpc_security_group_ids = ' + vpc_security_group_ids)
    print('subnet_id = ' + subnet_id)
    print('sns_topic_arn = ' + sns_topic_arn)
    print('vars_file_path = ' + vars_file_path)
    print('vpc_id = ' + vpc_id)
    print('vpc_name = ' + vpc_name)
    print('instance_serial_number = ' + instance_serial_number)
    print('instance_serial_number_file = ' + instance_serial_number_file)
    print('DEPLOYMENT_DATE = ' + DEPLOYMENT_DATE)
    print('ANSIBLE_VERSION = ' + ANSIBLE_VERSION)
    print('TERRAFORM_VERSION = ' + TERRAFORM_VERSION)

# Generate the vars_file for this pcluster-jumphost.

vars_file_main_part = '''\
# Name:    	{instance_name}.yml
# Author:  	Rodney Marable <rodney.marable@gmail.com>
# Created On:   April 20, 2019
# Last Changed: April 24, 2019
# Deployed On:  {DEPLOYMENT_DATE}
# Purpose: 	Build template for pcluster-jumphost

# Build tool information

ansible_version: {ANSIBLE_VERSION}
terraform_version: {TERRAFORM_VERSION}
vars_file_path: {vars_file_path}

# SNS topic

sns_arn: {sns_topic_arn}

# IAM parameters

iam_instance_policy: {iam_instance_policy}
iam_instance_profile: {iam_instance_profile}
iam_instance_role: {iam_instance_role}

# EC2 keypair management

ec2_keypair: {ec2_keypair}
ssh_keypair_file: "{{{{ ec2_keypair }}}}.pem"
#preserve_ec2_keypair: true
remove_pcluster_jumphost_data_dir: true
ssh_known_hosts: ~/.ssh/known_hosts

# EC2 instance parameters

aws_ami: {aws_ami}
base_os: {base_os}
ec2_instance_type: {ec2_instance_type}
ec2_user: {ec2_user}
ec2_user_home: /home/{ec2_user}
ec2_user_src: "{{{{ ec2_user_home }}}}/src"
instance_owner: {instance_owner}
instance_owner_email: {instance_owner_email}
instance_owner_department: {instance_owner_department}
project_id: {project_id}
instance_serial_number: {instance_serial_number}
instance_serial_number_file: {instance_serial_number_file}
DEPLOYMENT_DATE: {DEPLOYMENT_DATE}

# EBS

ebs_optimized: {ebs_optimized}
instance_root_volume_size: {instance_root_volume_size}
ebs_volume_type: {ebs_volume_type}

# AWS networking

az: {az}
region: {region}
security_group: {security_group}
vpc_security_group_ids: {vpc_security_group_ids}
subnet_id: {subnet_id}
vpc_id: {vpc_id}
vpc_name: {vpc_name}
provider: aws.{{{{ vpc_name }}}}
provider_tf_src: "{{{{ local_workingdir }}}}/templates/provider_aws.j2"
provider_tf_dest: provider_aws.tf

# Template paths

access_pcluster_jumphost_src: "{{{{ local_workingdir }}}}/templates/access_jumphost.j2"
access_pcluster_jumphost_dest: access_jumphost.{{{{ instance_name }}}}.sh
build_pcluster_jumphost_src: "{{{{ local_workingdir }}}}/templates/build_pcluster_jumphost.j2"
build_pcluster_jumphost_script: build_pcluster_jumphost.{{{{ instance_name }}}}.sh
instance_data_dir: "{{{{ local_workingdir }}}}/pcluster_jumphost_data/{{{{ instance_name }}}}"
instance_userdata_src: "{{{{ local_workingdir }}}}/templates/instance_userdata.j2"
instance_userdata_script: instance_userdata.{{{{ instance_name }}}}.sh
kill_pcluster_jumphost_src: "{{{{ local_workingdir }}}}/templates/kill_pcluster_jumphost.j2"
kill_pcluster_jumphost_script: kill_pcluster_jumphost.{{{{ instance_name }}}}.sh
remove_pcluster_jumphost_data_dir_src: "{{{{ local_workingdir }}}}/templates/remove_pcluster_jumphost_data_dir.j2"
remove_pcluster_jumphost_data_dir_dest: remove_pcluster_jumphost_data_dir.{{{{ instance_name }}}}.sh
stage_dir_parent: /tmp/_stagedir_Rmarable_InstanceMaker
stage_dir: "{{{{ stage_dir_parent }}}}/{{{{ instance_name }}}}"
tf_ec2_instance_src: "{{{{ local_workingdir }}}}/templates/DEFAULT_EC2_TEMPLATE.j2"
tf_ec2_instance_dest: "{{{{ instance_name }}}}.tf"

'''

# Write the cluster vars_file to disk.

print(vars_file_main_part.format(**instance_parameters), file = open(vars_file_path, 'w'))

print('Saved ' + instance_name + ' build template: ' + vars_file_path)
print('')

# Increase Ansible verbosity when debug_mode is enabled.

if debug_mode == 'true':
    ansible_verbosity = '-vvv'

# Generate the EC2 instance and security group templates using Ansible.
# Abort if CTRL-C is typed within 5 seconds.

print('Generating templates for pcluster-jumphost...')

cmd_string = 'ansible-playbook --extra-vars \"instance_name=' + instance_name + ' instance_serial_number=' + instance_serial_number + ' turbot_account=' + turbot_account + '"' + ' create_pcluster_jumphost_terraform_templates.yml ' + ansible_verbosity

print(cmd_string, file=open(instance_serial_number_file, "a"))

subprocess.run(cmd_string, shell=True)

# Create the new EC2 pcluster jumphost and security group with Terraform.
# Abort if CTRL-C is typed within 5 seconds.

ctrlC_Abort(5, 80, vars_file_path, instance_serial_number_file)
print('Invoking Terraform to build ' + instance_name + '...')

subprocess.run('terraform init -input=false', shell=True, cwd=instance_data_dir)
subprocess.run('terraform plan -out terraform_environment', shell=True, cwd=instance_data_dir)
subprocess.run('terraform apply \"terraform_environment\"', shell=True, cwd=instance_data_dir)

# Cleanup and exit.

print('')
print('To access via SSH:')
print('$ ./access_jumphost.py -N ' + instance_name)
print('Exiting...')
sys.exit(0)
