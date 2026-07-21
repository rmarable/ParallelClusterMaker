################################################################################
# Name:		pcluster_aux_data.py
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	April 20, 2019
# Last Changed:	July 19, 2026
# Purpose:	External data structures and functions for ParallelClusterMaker
################################################################################

########################
# Function definitions #
########################

def illegal_az_msg(az):
    import sys
    print('*** ERROR ***')
    print('"' + az + '"' + ' is not a valid Availability Zone in the selected AWS Region!')
    print('Aborting...')
    sys.exit(1)

def p_val(p, debug_mode):
    if debug_mode:
        print(p + " successfully validated")

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

def ctrlC_Abort(sleep_time, line_length, vars_file_path, cluster_serial_number_file, cluster_serial_number, enable_fsx_hydration):
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
        if (vars_file_path is None) and (cluster_serial_number_file is None):
            print('')
            print('No orphaned files or directories were found.')
            print('')
        else:
            import contextlib
            for _path in (cluster_serial_number_file, vars_file_path):
                if _path and os.path.exists(_path):
                    with contextlib.suppress(FileNotFoundError):
                        os.remove(_path)
                    print('Removed: ' + _path)
        if cluster_serial_number is None:
            print('')
            print('No IAM roles or policies exist for this cluster.')
            print('')
        else:
            import boto3
            iam = boto3.client('iam')
            ec2_iam_policy = 'pclustermaker-policy-' + str(cluster_serial_number)
            ec2_iam_role = 'pclustermaker-role-' + str(cluster_serial_number)
            fsx_hydration_iam_policy = 'pclustermaker-fsx-s3-policy-' + str(cluster_serial_number)
            def _del_inline_policy(role, policy):
                try:
                    iam.delete_role_policy(RoleName=role, PolicyName=policy)
                    print('Deleted: ' + policy)
                except Exception as _e:
                    if 'NoSuchEntity' in str(_e):
                        print(f'IAM policy not found, skipping: {policy}')
                    else:
                        print(f'WARNING: could not delete IAM policy {policy}: {_e}')

            if enable_fsx_hydration == 'true':
                _del_inline_policy(ec2_iam_role, fsx_hydration_iam_policy)
            _del_inline_policy(ec2_iam_role, ec2_iam_policy)
            try:
                iam.delete_role(RoleName=ec2_iam_role)
                print('Deleted: ' + ec2_iam_role)
            except Exception as _e:
                if 'NoSuchEntity' in str(_e):
                    print(f'IAM role not found, skipping: {ec2_iam_role}')
                else:
                    print(f'WARNING: could not delete IAM role {ec2_iam_role}: {_e}')
        print('Aborting...')
        sys.exit(1)

def print_TextHeader(cluster_name, header, line_length):
    print('')
    print(''.center(line_length, '-'))
    T2C = header + ' for ' + cluster_name
    print(T2C.center(line_length))
    print(''.center(line_length, '-'))

def base_os_instance_check(base_os, instance_type, debug_mode):
    import sys
    _arm_families = ('a1.', 'c6g', 'c7g', 'm6g', 'm7g', 'r6g', 'r7g', 'hpc7g',
                     'g5g', 'im4gn', 'is4gen', 'i4g',
                     't4g', 'x2g')
    # trn1 (Trainium 1, Intel Xeon) and inf2 (Inferentia 2, Intel Sapphire Rapids)
    # are x86_64 despite the accelerator branding — do not include in ARM families.
    _arm_oses = ('alinux2arm', 'ubuntu2204arm', 'ubuntu2404arm')
    _x86_oses = ('alinux2', 'ubuntu2204', 'ubuntu2404', 'rhel8', 'rhel9', 'centos7')
    _is_arm = any(instance_type.startswith(f) for f in _arm_families)
    if _is_arm and base_os in _x86_oses:
        if debug_mode:
            print(f'WARNING: {instance_type} is ARM/Graviton but base_os={base_os} is x86_64 — cluster will fail to boot.')
        refer_to_docs_and_quit(
            f'Instance type {instance_type} is ARM/Graviton but base_os={base_os} is x86_64.\n'
            f'  Use an ARM-compatible base OS (e.g. alinux2arm) or an x86_64 instance type.'
        )
    if debug_mode:
        p_val('base_os_instance_check', debug_mode)

def refer_to_docs_and_quit(error_msg):
    import sys
    print('*** ERROR ***')
    print(error_msg)
    print('')
    print('Please refer to the AWS ParallelCluster documentation for more information:')
    print('https://docs.aws.amazon.com/parallelcluster/latest/ug/what-is-aws-parallelcluster.html')
    print('')
    print('Aborting...')
    sys.exit(1)

############################
# EC2 instance definitions #
############################

default_instance_types = {
    'default_head_node_instance_type': 'c5.xlarge',
    'default_compute_instance_type': 'c5.xlarge'
}

# General Purpose

ec2_instances_general_purpose = [
    # t3/t3a
    't3.nano', 't3.micro', 't3.small', 't3.medium', 't3.large', 't3.xlarge', 't3.2xlarge',
    't3a.nano', 't3a.micro', 't3a.small', 't3a.medium', 't3a.large', 't3a.xlarge', 't3a.2xlarge',
    # m5/m5a/m5d/m5n/m5dn/m5zn
    'm5.large', 'm5.xlarge', 'm5.2xlarge', 'm5.4xlarge', 'm5.8xlarge', 'm5.12xlarge', 'm5.16xlarge', 'm5.24xlarge', 'm5.metal',
    'm5a.large', 'm5a.xlarge', 'm5a.2xlarge', 'm5a.4xlarge', 'm5a.8xlarge', 'm5a.12xlarge', 'm5a.16xlarge', 'm5a.24xlarge',
    'm5dn.large', 'm5dn.xlarge', 'm5dn.2xlarge', 'm5dn.4xlarge', 'm5dn.8xlarge', 'm5dn.12xlarge', 'm5dn.16xlarge', 'm5dn.24xlarge', 'm5dn.metal',
    'm5n.large', 'm5n.xlarge', 'm5n.2xlarge', 'm5n.4xlarge', 'm5n.8xlarge', 'm5n.12xlarge', 'm5n.16xlarge', 'm5n.24xlarge',
    'm5zn.large', 'm5zn.xlarge', 'm5zn.2xlarge', 'm5zn.3xlarge', 'm5zn.6xlarge', 'm5zn.12xlarge', 'm5zn.metal',
    # m6i/m6a/m6g/m6gd
    'm6i.large', 'm6i.xlarge', 'm6i.2xlarge', 'm6i.4xlarge', 'm6i.8xlarge', 'm6i.12xlarge', 'm6i.16xlarge', 'm6i.24xlarge', 'm6i.32xlarge', 'm6i.metal',
    'm6a.large', 'm6a.xlarge', 'm6a.2xlarge', 'm6a.4xlarge', 'm6a.8xlarge', 'm6a.12xlarge', 'm6a.16xlarge', 'm6a.24xlarge', 'm6a.32xlarge', 'm6a.48xlarge', 'm6a.metal',
    'm6g.medium', 'm6g.large', 'm6g.xlarge', 'm6g.2xlarge', 'm6g.4xlarge', 'm6g.8xlarge', 'm6g.12xlarge', 'm6g.16xlarge', 'm6g.metal',
    # m7i/m7a/m7g
    'm7i.large', 'm7i.xlarge', 'm7i.2xlarge', 'm7i.4xlarge', 'm7i.8xlarge', 'm7i.12xlarge', 'm7i.16xlarge', 'm7i.24xlarge', 'm7i.48xlarge', 'm7i.metal-24xl', 'm7i.metal-48xl',
    'm7a.medium', 'm7a.large', 'm7a.xlarge', 'm7a.2xlarge', 'm7a.4xlarge', 'm7a.8xlarge', 'm7a.12xlarge', 'm7a.16xlarge', 'm7a.24xlarge', 'm7a.32xlarge', 'm7a.48xlarge', 'm7a.metal-48xl',
    'm7g.medium', 'm7g.large', 'm7g.xlarge', 'm7g.2xlarge', 'm7g.4xlarge', 'm7g.8xlarge', 'm7g.12xlarge', 'm7g.16xlarge', 'm7g.metal',
]

# Compute Optimized

ec2_instances_compute_optimized = [
    # c5/c5a/c5n/c5d
    'c5.large', 'c5.xlarge', 'c5.2xlarge', 'c5.4xlarge', 'c5.9xlarge', 'c5.12xlarge', 'c5.18xlarge', 'c5.24xlarge', 'c5.metal',
    'c5a.large', 'c5a.xlarge', 'c5a.2xlarge', 'c5a.4xlarge', 'c5a.8xlarge', 'c5a.12xlarge', 'c5a.16xlarge', 'c5a.24xlarge',
    'c5n.large', 'c5n.xlarge', 'c5n.2xlarge', 'c5n.4xlarge', 'c5n.9xlarge', 'c5n.18xlarge', 'c5n.metal',
    # c6i/c6a/c6g/c6gn
    'c6i.large', 'c6i.xlarge', 'c6i.2xlarge', 'c6i.4xlarge', 'c6i.8xlarge', 'c6i.12xlarge', 'c6i.16xlarge', 'c6i.24xlarge', 'c6i.32xlarge', 'c6i.metal',
    'c6a.large', 'c6a.xlarge', 'c6a.2xlarge', 'c6a.4xlarge', 'c6a.8xlarge', 'c6a.12xlarge', 'c6a.16xlarge', 'c6a.24xlarge', 'c6a.32xlarge', 'c6a.48xlarge', 'c6a.metal',
    'c6g.medium', 'c6g.large', 'c6g.xlarge', 'c6g.2xlarge', 'c6g.4xlarge', 'c6g.8xlarge', 'c6g.12xlarge', 'c6g.16xlarge', 'c6g.metal',
    'c6gn.medium', 'c6gn.large', 'c6gn.xlarge', 'c6gn.2xlarge', 'c6gn.4xlarge', 'c6gn.8xlarge', 'c6gn.12xlarge', 'c6gn.16xlarge',
    # c7i/c7a/c7g
    'c7i.large', 'c7i.xlarge', 'c7i.2xlarge', 'c7i.4xlarge', 'c7i.8xlarge', 'c7i.12xlarge', 'c7i.16xlarge', 'c7i.24xlarge', 'c7i.48xlarge', 'c7i.metal-24xl', 'c7i.metal-48xl',
    'c7a.medium', 'c7a.large', 'c7a.xlarge', 'c7a.2xlarge', 'c7a.4xlarge', 'c7a.8xlarge', 'c7a.12xlarge', 'c7a.16xlarge', 'c7a.24xlarge', 'c7a.32xlarge', 'c7a.48xlarge', 'c7a.metal-48xl',
    'c7g.medium', 'c7g.large', 'c7g.xlarge', 'c7g.2xlarge', 'c7g.4xlarge', 'c7g.8xlarge', 'c7g.12xlarge', 'c7g.16xlarge', 'c7g.metal',
]

# Memory Optimized

ec2_instances_memory_optimized = [
    # r5/r5a/r5b/r5n/r5d
    'r5.large', 'r5.xlarge', 'r5.2xlarge', 'r5.4xlarge', 'r5.8xlarge', 'r5.12xlarge', 'r5.16xlarge', 'r5.24xlarge', 'r5.metal',
    'r5a.large', 'r5a.xlarge', 'r5a.2xlarge', 'r5a.4xlarge', 'r5a.8xlarge', 'r5a.12xlarge', 'r5a.16xlarge', 'r5a.24xlarge',
    'r5dn.large', 'r5dn.xlarge', 'r5dn.2xlarge', 'r5dn.4xlarge', 'r5dn.8xlarge', 'r5dn.12xlarge', 'r5dn.16xlarge', 'r5dn.24xlarge', 'r5dn.metal',
    'r5n.large', 'r5n.xlarge', 'r5n.2xlarge', 'r5n.4xlarge', 'r5n.8xlarge', 'r5n.12xlarge', 'r5n.16xlarge', 'r5n.24xlarge',
    # r6i/r6a/r6g
    'r6i.large', 'r6i.xlarge', 'r6i.2xlarge', 'r6i.4xlarge', 'r6i.8xlarge', 'r6i.12xlarge', 'r6i.16xlarge', 'r6i.24xlarge', 'r6i.32xlarge', 'r6i.metal',
    'r6a.large', 'r6a.xlarge', 'r6a.2xlarge', 'r6a.4xlarge', 'r6a.8xlarge', 'r6a.12xlarge', 'r6a.16xlarge', 'r6a.24xlarge', 'r6a.32xlarge', 'r6a.48xlarge', 'r6a.metal',
    'r6g.medium', 'r6g.large', 'r6g.xlarge', 'r6g.2xlarge', 'r6g.4xlarge', 'r6g.8xlarge', 'r6g.12xlarge', 'r6g.16xlarge', 'r6g.metal',
    # r7i/r7a/r7g
    'r7i.large', 'r7i.xlarge', 'r7i.2xlarge', 'r7i.4xlarge', 'r7i.8xlarge', 'r7i.12xlarge', 'r7i.16xlarge', 'r7i.24xlarge', 'r7i.48xlarge', 'r7i.metal-24xl', 'r7i.metal-48xl',
    'r7a.medium', 'r7a.large', 'r7a.xlarge', 'r7a.2xlarge', 'r7a.4xlarge', 'r7a.8xlarge', 'r7a.12xlarge', 'r7a.16xlarge', 'r7a.24xlarge', 'r7a.32xlarge', 'r7a.48xlarge', 'r7a.metal-48xl',
    'r7g.medium', 'r7g.large', 'r7g.xlarge', 'r7g.2xlarge', 'r7g.4xlarge', 'r7g.8xlarge', 'r7g.12xlarge', 'r7g.16xlarge', 'r7g.metal',
    # High memory
    'x2idn.16xlarge', 'x2idn.24xlarge', 'x2idn.32xlarge',
    'x2iedn.xlarge', 'x2iedn.2xlarge', 'x2iedn.4xlarge', 'x2iedn.8xlarge', 'x2iedn.16xlarge', 'x2iedn.24xlarge', 'x2iedn.32xlarge',
    'x2iezn.2xlarge', 'x2iezn.4xlarge', 'x2iezn.6xlarge', 'x2iezn.8xlarge', 'x2iezn.12xlarge',
    'u-6tb1.56xlarge', 'u-9tb1.112xlarge', 'u-12tb1.112xlarge', 'u-18tb1.112xlarge', 'u-24tb1.112xlarge',
    # z1d
    'z1d.large', 'z1d.xlarge', 'z1d.2xlarge', 'z1d.3xlarge', 'z1d.6xlarge', 'z1d.12xlarge', 'z1d.metal',
]

# Storage Optimized

ec2_instances_storage_optimized = [
    # i3/i3en
    'i3.large', 'i3.xlarge', 'i3.2xlarge', 'i3.4xlarge', 'i3.8xlarge', 'i3.16xlarge', 'i3.metal',
    'i3en.large', 'i3en.xlarge', 'i3en.2xlarge', 'i3en.3xlarge', 'i3en.6xlarge', 'i3en.12xlarge', 'i3en.24xlarge', 'i3en.metal',
    # i4i/i4g
    'i4i.large', 'i4i.xlarge', 'i4i.2xlarge', 'i4i.4xlarge', 'i4i.8xlarge', 'i4i.16xlarge', 'i4i.32xlarge', 'i4i.metal',
    'i4g.large', 'i4g.xlarge', 'i4g.2xlarge', 'i4g.4xlarge', 'i4g.8xlarge', 'i4g.16xlarge',
    # d3/d3en
    'd3.xlarge', 'd3.2xlarge', 'd3.4xlarge', 'd3.8xlarge',
    'd3en.xlarge', 'd3en.2xlarge', 'd3en.4xlarge', 'd3en.6xlarge', 'd3en.8xlarge', 'd3en.12xlarge',
    # is4gen/im4gn
    'is4gen.medium', 'is4gen.large', 'is4gen.xlarge', 'is4gen.2xlarge', 'is4gen.4xlarge', 'is4gen.8xlarge',
    'im4gn.large', 'im4gn.xlarge', 'im4gn.2xlarge', 'im4gn.4xlarge', 'im4gn.8xlarge', 'im4gn.16xlarge',
]

# Accelerated Computing

ec2_instances_accelerated_computing = [
    # G4 - NVIDIA T4
    'g4dn.xlarge', 'g4dn.2xlarge', 'g4dn.4xlarge', 'g4dn.8xlarge', 'g4dn.12xlarge', 'g4dn.16xlarge', 'g4dn.metal',
    'g4ad.xlarge', 'g4ad.2xlarge', 'g4ad.4xlarge', 'g4ad.8xlarge', 'g4ad.16xlarge',
    # G5 - NVIDIA A10G
    'g5.xlarge', 'g5.2xlarge', 'g5.4xlarge', 'g5.8xlarge', 'g5.12xlarge', 'g5.16xlarge', 'g5.24xlarge', 'g5.48xlarge',
    'g5g.xlarge', 'g5g.2xlarge', 'g5g.4xlarge', 'g5g.8xlarge', 'g5g.16xlarge', 'g5g.metal',
    # G6 - NVIDIA L4
    'g6.xlarge', 'g6.2xlarge', 'g6.4xlarge', 'g6.8xlarge', 'g6.12xlarge', 'g6.16xlarge', 'g6.24xlarge', 'g6.48xlarge',
    # P3 - NVIDIA V100
    'p3.2xlarge', 'p3.8xlarge', 'p3.16xlarge', 'p3dn.24xlarge',
    # P4 - NVIDIA A100
    'p4d.24xlarge', 'p4de.24xlarge',
    # P5 - NVIDIA H100
    'p5.48xlarge',
    # Trainium
    'trn1.2xlarge', 'trn1.32xlarge', 'trn1n.32xlarge',
    # Inferentia
    'inf1.xlarge', 'inf1.2xlarge', 'inf1.6xlarge', 'inf1.24xlarge',
    'inf2.xlarge', 'inf2.8xlarge', 'inf2.24xlarge', 'inf2.48xlarge',
    # F1 - FPGA
    'f1.2xlarge', 'f1.4xlarge', 'f1.16xlarge',
    # VT1 - video transcoding
    'vt1.3xlarge', 'vt1.6xlarge', 'vt1.24xlarge',
]

# HPC Optimized

ec2_instances_hpc_optimized = [
    'hpc6a.48xlarge',
    'hpc6id.32xlarge',
    'hpc7a.12xlarge', 'hpc7a.24xlarge', 'hpc7a.48xlarge', 'hpc7a.96xlarge',
    'hpc7g.4xlarge', 'hpc7g.8xlarge', 'hpc7g.16xlarge',
]

# Full EC2 instance list

ec2_instances_full_list = (
    ec2_instances_general_purpose +
    ec2_instances_compute_optimized +
    ec2_instances_memory_optimized +
    ec2_instances_storage_optimized +
    ec2_instances_accelerated_computing +
    ec2_instances_hpc_optimized
)

# EFA-capable instances — intentionally incomplete allowlist (AWS adds new types frequently).
# This list is used only for a warning, not a hard block.  To check authoritatively, use:
#   aws ec2 describe-instance-types --filters Name=network-info.efa-supported,Values=true

ec2_instances_efa = [
    'c5n.18xlarge', 'c5n.metal',
    'c6gn.16xlarge',
    'g4dn.8xlarge', 'g4dn.12xlarge', 'g4dn.metal',
    'g5.8xlarge', 'g5.12xlarge', 'g5.16xlarge', 'g5.24xlarge', 'g5.48xlarge',
    'hpc6a.48xlarge', 'hpc6id.32xlarge',
    'hpc7a.12xlarge', 'hpc7a.24xlarge', 'hpc7a.48xlarge', 'hpc7a.96xlarge',
    'hpc7g.4xlarge', 'hpc7g.8xlarge', 'hpc7g.16xlarge',
    'i4i.32xlarge',
    'm5dn.24xlarge', 'm5n.24xlarge',
    'p3dn.24xlarge',
    'p4d.24xlarge', 'p4de.24xlarge',
    'p5.48xlarge',
    'r5dn.24xlarge', 'r5n.24xlarge',
    'trn1.2xlarge', 'trn1.32xlarge', 'trn1n.32xlarge',
    'inf2.8xlarge', 'inf2.24xlarge', 'inf2.48xlarge',
]

base_os_efa = ['ubuntu2204', 'ubuntu2404', 'rhel8', 'rhel9']
