"""
Shared fixtures for ParallelClusterMaker tests.

cluster_params() provides dummy values for every variable referenced in any
template under templates/ and performance/jinja2/.  When a new template
variable is added, add it here so test_templates.py catches the gap
immediately.
"""
import pytest


@pytest.fixture
def cluster_params():
    """
    Minimal but complete render context for all Jinja2 templates.
    Values are chosen to exercise conditionals (e.g. enable_fsx == 'true')
    so that guarded blocks are rendered and their variables are checked too.
    """
    return {
        # Identity
        'cluster_name': 'test-cluster',
        'cluster_owner': 'testuser',
        'cluster_owner_email': 'testuser@example.com',
        'cluster_owner_department': 'hpc',
        'cluster_serial_number': 'test-cluster-00001220260720',
        'cluster_serial_datestamp': '00001220260720',
        'cluster_serial_number_file': '/tmp/test-cluster.serial',
        'project_id': 'test-project',
        'prod_level': 'dev',
        'DEPLOYMENT_DATE': '2026-07-20',
        'Deployed_On': 'July 20, 2026',
        'ANSIBLE_VERSION': '2.16.0',

        # Paths
        'local_workingdir': '/home/testuser/ParallelClusterMaker',
        'cluster_rootdir': '/home/testuser/ParallelClusterMaker',
        'cluster_data_dir': '/home/testuser/ParallelClusterMaker/active_clusters/test-cluster',
        'cluster_template_dir': '/home/testuser/ParallelClusterMaker/templates',
        'stage_dir': '/tmp/_ParallelClusterMaker_stage/test-cluster-00001220260720',

        # AWS networking
        'aws_account_id': '123456789012',
        'region': 'us-east-1',
        'az': 'us-east-1a',
        'vpc_id': 'vpc-0abc123',
        'vpc_cidr': '10.0.0.0/16',
        'vpc_name': 'test-vpc',
        'subnet_id': 'subnet-0abc123',
        'compute_az_list': ['us-east-1a'],
        'compute_subnet_ids': 'subnet-0abc123',
        'use_private_compute_subnet': 'false',

        # Compute
        'base_os': 'ubuntu2404',
        'custom_ami': 'NONE',
        'hyperthreading': 'true',
        'headnode_instance_type': 'c5.xlarge',
        'headnode_root_volume_size': 50,
        'headnode_root_volume_type': 'gp3',
        'headnode_root_volume_iops': 3000,
        'headnode_root_volume_throughput': 125,
        'compute_instance_type': 'c5.2xlarge',
        'compute_root_volume_size': 50,
        'compute_root_volume_type': 'gp3',
        'compute_root_volume_iops': 3000,
        'compute_root_volume_throughput': 125,
        'placement_group': 'NONE',
        'enable_efa': 'false',

        # Scheduling
        'scheduler': 'slurm',
        'cluster_type': 'spot',
        'cluster_lifetime': '7:0:0',
        'initial_queue_size': 2,
        'max_queue_size': 10,
        'maintain_initial_size': 'false',
        'scaledown_idletime': 5,
        'raw_spot_price': '0.20',
        'spot_price': '0.20',
        'pcluster_create_timeout': 60,

        # EC2
        'ec2_user': 'ec2-user',
        'ec2_user_home': '/home/ec2-user',
        'ec2_user_src': '/home/ec2-user/src',
        'ec2_keypair': 'test-cluster-00001220260720_us-east-1',
        'ec2_iam_policy': 'pclustermaker-policy-test-cluster-00001220260720',
        'ec2_iam_role': 'pclustermaker-role-test-cluster-00001220260720',

        # S3
        's3_bucketname': 'parallelclustermaker-test-cluster-00001220260720',
        's3_script_path': 'cluster_scripts/dev',

        # EBS
        'ebs_root': '/shared',
        'ebs_shared_dir': '/shared',
        'ebs_shared_volume_size': 250,
        'ebs_shared_volume_type': 'gp3',
        'ebs_shared_volume_iops': 3000,
        'ebs_shared_volume_throughput': 125,
        'ebs_encryption': 'false',
        'ebs_performance_dir': '/shared/performance/test-cluster/testuser/slurm',

        # EFS (enabled so guarded block is rendered)
        'enable_efs': 'true',
        'efs_root': '/efs',
        'efs_encryption': 'false',
        'efs_performance_mode': 'generalPurpose',
        'efs_throughput_mode': 'bursting',
        'efs_pkg_dir': '/efs/pkg',
        'efs_hpc_performance_dir': '/efs/performance/test-cluster/testuser/slurm',

        # FSx (enabled so guarded block is rendered)
        'enable_fsx': 'true',
        'fsx_root': '/fsx',
        'fsx_size': 1200,
        'fsx_pkg_dir': '/fsx/pkg',
        'fsx_hpc_performance_dir': '/fsx/performance/test-cluster/testuser/slurm',
        'enable_fsx_hydration': 'true',
        'fsx_chunk_size': 1024,
        'fsx_hydration_iam_policy': 'pclustermaker-fsx-s3-policy-test-cluster-00001220260720',
        'fsx_s3_import_bucket': 'test-import-bucket',
        'fsx_s3_import_path': 'input/',
        'fsx_s3_export_bucket': 'test-export-bucket',
        'fsx_s3_export_path': 'output/',

        # External NFS (enabled so guarded block is rendered)
        'enable_external_nfs': 'true',
        'external_nfs_server': 'nfs.example.com',
        'external_nfs_server_root': '/nfs',
        'external_nfs_mount_list_template_dest': 'external_nfs_mount_list.test-cluster.conf',
        'external_nfs_pkg_dir': '/nfs/pkg',
        'external_nfs_hpc_performance_dir': '/nfs/performance/test-cluster/testuser/slurm',

        # Scripts
        'pre_install_script': 'templates/preinstall.j2',
        'post_install_script': 'templates/postinstall.j2',
        'preinstall_s3_dest': 'preinstall.test-cluster.sh',
        'postinstall_s3_dest': 'postinstall.test-cluster.sh',

        # Features
        'enable_ganglia': 'false',
        'enable_hpc_performance_tests': 'true',
        'matrix_sizes': '1000 2000 3000 4000 5000',
        'perftest_custom_start_number': 10,
        'perftest_custom_step_size': 10,
        'perftest_custom_total_tests': 10,

        # Spack
        'spack_user': 'spack',
        'spack_group': 'spack',
        'spack_root': '/fsx/pkg/spack',
        'pkg_dir': '/fsx/pkg',

        # Performance
        'sid': 'slurm-test-cluster',

        # Ansible registered vars (used by sns/access templates)
        'HeadNodePublicIP': '1.2.3.4',
        'start_overall_timer': type('R', (), {'stdout': '2026-07-20 10:00:00'})(),
        'start_stack_creation_timer': type('R', (), {'stdout': '2026-07-20 10:01:00'})(),
        'stop_stack_creation_timer': type('R', (), {'stdout': '2026-07-20 10:30:00'})(),
        'stop_overall_timer': type('R', (), {'stdout': '2026-07-20 10:35:00'})(),
        'start_delete_timer': type('R', (), {'stdout': '2026-07-20 11:00:00'})(),
        'stop_delete_timer': type('R', (), {'stdout': '2026-07-20 11:10:00'})(),
        'ssh_keypair': '/home/testuser/ParallelClusterMaker/active_clusters/test-cluster/test-cluster-00001220260720_us-east-1.pem',
    }


@pytest.fixture
def cluster_params_custom_ami(cluster_params):
    """cluster_params variant with custom_ami, placement_group, and ganglia enabled.

    Exercises conditional template branches that are skipped by the default fixture.
    """
    overrides = {
        'custom_ami': 'ami-0abc1234567890def',
        'placement_group': 'test-cluster-pg',
        'enable_ganglia': 'true',
        'use_private_compute_subnet': 'true',
    }
    return {**cluster_params, **overrides}
