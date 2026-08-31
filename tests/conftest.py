"""
Shared fixtures for ParallelClusterMaker tests.

cluster_params() provides dummy values for every variable referenced in any
template under templates/ and hpc-benchmark/.  When a new template variable
is added, add it here so test_templates.py catches the gap immediately.
"""

import os
import sys

from urllib.parse import urlparse

import pytest

# `src/` on the path for every test module, not just the ones that insert
# it themselves. The autouse fixture at the bottom of this file patches a
# pcluster_core attribute, so the module has to be importable during
# collection of any test file -- running one in isolation failed with
# ModuleNotFoundError otherwise.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

# Import Ansible's collection loader before anything can import fastmcp.
#
# Not cosmetic ordering -- a hard incompatibility, reproduced and traced
# 2026-08-21. Ansible's collection loader
# (ansible/utils/collection_loader/_collection_finder.py, module scope)
# raises `Exception: need exactly one FileFinder import hook (found N)`
# unless sys.path_hooks holds exactly one FileFinder entry. Importing
# fastmcp pulls in setuptools' _distutils_hack, which inserts a second
# FileFinder hook at index 0 -- after which every later `import ansible`
# in that process fails, including the one
# TestTheTestEnvironmentMatchesAnsible uses to read Ansible's real
# trim_blocks/lstrip_blocks defaults out of its own source.
#
# The check runs once, at first import of the collection loader, so
# importing Ansible here (while exactly one hook still exists) makes the
# whole suite order-independent: it no longer matters whether pytest
# happens to collect the MCP test modules before tests/test_templates.py.
# Empirically this also stops fastmcp adding the second hook at all.
#
# Deliberately NOT fixed by loosening the Ansible-defaults assertion:
# that test exists because a mismatch between our Jinja2 env and
# Ansible's own defaults silently changed rendered output once already,
# and weakening it to accommodate an unrelated dependency would trade a
# real guard for an import-order convenience.
import ansible.plugins.action.template  # noqa: F401,E402


@pytest.fixture
def cluster_params():
    """
    Minimal but complete render context for all Jinja2 templates.
    Values are chosen to exercise conditionals (e.g. enable_fsx == 'true')
    so that guarded blocks are rendered and their variables are checked too.
    """
    return {
        # Identity
        "cluster_name": "test-cluster",
        "cluster_owner": "testuser",
        "turbot_account": "disabled",
        "ssh_secret_name": "parallelcluster/test-cluster/test-cluster-00001220260720/ssh-private-key",
        "cluster_owner_email": "testuser@example.com",
        "cluster_owner_department": "hpc",
        "cluster_serial_number": "test-cluster-00001220260720",
        "cluster_serial_datestamp": "00001220260720",
        "cluster_serial_number_file": "/tmp/test-cluster.serial",
        "project_id": "test-project",
        "prod_level": "dev",
        "DEPLOYMENT_DATE": "2026-07-20",
        "Deployed_On": "July 20, 2026",
        "ANSIBLE_VERSION": "2.16.0",
        # Paths
        "local_workingdir": "/home/testuser/ParallelClusterMaker",
        "cluster_rootdir": "/home/testuser/ParallelClusterMaker",
        "cluster_data_dir": "/home/testuser/ParallelClusterMaker/active_clusters/test-cluster",
        "cluster_template_dir": "/home/testuser/ParallelClusterMaker/templates",
        "stage_dir": "/tmp/_ParallelClusterMaker_stage/test-cluster-00001220260720",
        # AWS networking
        "aws_account_id": "123456789012",
        "region": "us-east-1",
        "az": "us-east-1a",
        "vpc_id": "vpc-0abc123",
        "vpc_cidr": "10.0.0.0/16",
        "vpc_name": "test-vpc",
        "subnet_id": "subnet-0abc123",
        "loginnode_subnet_id": "subnet-0abc123",
        "compute_az_list": ["us-east-1a"],
        "compute_subnet_ids": ["subnet-0abc123"],
        "use_private_compute_subnet": "false",
        "gpu_subnet_ids": ["subnet-0abc123"],
        "use_private_gpu_subnet": "false",
        # Compute
        "base_os": "ubuntu2404arm",
        "pcluster_os": "ubuntu2404",
        "custom_ami": "NONE",
        "hyperthreading": "true",
        "headnode_instance_type": "c8g.xlarge",
        "headnode_root_volume_size": 50,
        "headnode_root_volume_type": "gp3",
        "headnode_root_volume_iops": 3000,
        "headnode_root_volume_throughput": 125,
        "enable_loginnode": "false",
        "loginnode_instance_type": "c8g.xlarge",
        "loginnode_count": 1,
        "compute_instance_type": "c8g.2xlarge",
        "cpu_instance_types": ["c8g.2xlarge"],
        "gpu_instance_type": "",
        "gpu_instance_types": [],
        "enable_cpu_queue": "true",
        "enable_gpu_queue": "false",
        "gpu_ranks_per_node": 0,
        # Schedulable vCPUs, not GPU devices: c8g.2xlarge is 8 vCPUs at 1 thread
        # per core, so hyperthreading=true and false give the same number here.
        "cpu_ranks_per_node": 8,
        "gpu_vcpus_per_node": 0,
        "compute_root_volume_size": 50,
        "compute_root_volume_type": "gp3",
        "compute_root_volume_iops": 3000,
        "compute_root_volume_throughput": 125,
        "gpu_root_volume_size": 250,
        "gpu_root_volume_type": "gp3",
        "gpu_root_volume_iops": 3000,
        "gpu_root_volume_throughput": 125,
        "placement_group": "NONE",
        "enable_efa": "false",
        "enable_efa_gdr": "false",
        "enable_gpu": "false",
        # Scheduling
        "scheduler": "slurm",
        "pcluster_version": "3.15.1",
        "cluster_type": "spot",
        "initial_cpu_queue_size": 2,
        "max_cpu_queue_size": 8,
        "maintain_cpu_initial_size": "false",
        "initial_gpu_queue_size": 2,
        "max_gpu_queue_size": 8,
        "maintain_gpu_initial_size": "false",
        "scaledown_idletime": 5,
        "pcluster_create_timeout": 60,
        # EC2
        # Must agree with base_os above: _resolve_ec2_user derives this, and only
        # The pair used to be ubuntu2404arm with an RPM-distro login name, which
        # no cluster could ever have.
        "ec2_user": "ubuntu",
        "ec2_user_home": "/home/ubuntu",
        "ec2_user_src": "/home/ubuntu/src",
        "ec2_keypair": "test-cluster-00001220260720_us-east-1",
        "ec2_iam_policy": "pclustermaker-policy-test-cluster-00001220260720",
        "ec2_iam_role": "pclustermaker-role-test-cluster-00001220260720",
        # S3
        "s3_bucketname": "parallelclustermaker-test-cluster-00001220260720",
        "results_bucketname": "parallelclustermaker-results-123456789012-us-east-1",
        "s3_script_path": "cluster_scripts/dev",
        # EBS
        "ebs_root": "/shared",
        "ebs_shared_dir": "/shared",
        "ebs_shared_volume_size": 250,
        "ebs_shared_volume_type": "gp3",
        "ebs_shared_volume_iops": 3000,
        "ebs_shared_volume_throughput": 125,
        "ebs_encryption": "false",
        "ebs_performance_dir": "/shared/hpc-benchmark/test-cluster/testuser/slurm",
        # Both EFS and FSx are on below, so this is 2100 + the FSx allowance.
        "head_node_bootstrap_timeout": 3900,
        # EFS (enabled so guarded block is rendered)
        "enable_efs": "true",
        "efs_root": "/efs",
        "efs_encryption": "false",
        "efs_performance_mode": "generalPurpose",
        "efs_throughput_mode": "bursting",
        "efs_pkg_dir": "/efs/pkg",
        "efs_hpc_performance_dir": "/efs/hpc-benchmark/test-cluster/testuser/slurm",
        # FSx (enabled so guarded block is rendered)
        "enable_fsx": "true",
        "fsx_root": "/fsx",
        "fsx_size": 1200,
        "fsx_pkg_dir": "/fsx/pkg",
        "fsx_hpc_performance_dir": "/fsx/hpc-benchmark/test-cluster/testuser/slurm",
        "enable_fsx_hydration": "true",
        "fsx_chunk_size": 1024,
        "fsx_hydration_iam_policy": "pclustermaker-fsx-s3-policy-test-cluster-00001220260720",
        "fsx_s3_import_bucket": "test-import-bucket",
        "fsx_s3_import_path": "input/",
        "fsx_s3_export_bucket": "test-export-bucket",
        "fsx_s3_export_path": "output/",
        # External NFS (enabled so guarded block is rendered)
        "enable_external_nfs": "true",
        "external_nfs_server": "nfs.example.com",
        "external_nfs_server_root": "/nfs",
        "external_nfs_mount_list_template_dest": "external_nfs_mount_list.test-cluster.conf",
        "external_nfs_pkg_dir": "/nfs/pkg",
        "external_nfs_hpc_performance_dir": "/nfs/hpc-benchmark/test-cluster/testuser/slurm",
        # Ansible-registered result of creating the externalNfs security group;
        # only ever referenced by config.pcluster.j2 when enable_external_nfs == 'true'.
        "external_nfs_sg": {"group_id": "sg-0abc123externalnfs"},
        # Scripts. The toolkit's own rendered preinstall/postinstall and the
        # operator's hooks are separate stages; pointing pre/post_install_script at
        # the templates conflated them, which is how postinstall.j2 stayed
        # unrendered from the v3 migration to 2026-07-26 with every test green.
        "pre_install_script": "scripts/pre-deployment.sh",
        "post_install_script": "scripts/post-deployment.sh",
        "preinstall_s3_dest": "preinstall.test-cluster.sh",
        "postinstall_s3_dest": "postinstall.test-cluster.sh",
        "user_preinstall_s3_dest": "pre-deployment.sh",
        "user_postinstall_s3_dest": "post-deployment.sh",
        # Features
        "enable_hpc_benchmarks": "true",
        # Spack
        "spack_root": "/fsx/pkg/spack",
        "pkg_dir": "/fsx/pkg",
        # Monitoring
        "enable_monitoring": "false",
        "enable_slurm_accounting": "false",
        "monitoring_version": "v2.6",
        "monitoring_version_checksum": "sha256:4afa56a59228c1d8f4e405d07a2291f31853842128e6f7a0e52e1e2c1e262d55",
        "monitoring_s3_dest": "monitoring-post-install-wrapper.test-cluster.sh",
        "monitoring_wrapper_src": "/home/testuser/ParallelClusterMaker/templates/monitoring-post-install-wrapper.j2",
        "monitoring_wrapper_dest": "/home/testuser/ParallelClusterMaker/active_clusters/test-cluster/monitoring-post-install-wrapper.test-cluster.sh",
        "grafana_tunnel_src": "/home/testuser/ParallelClusterMaker/templates/grafana_tunnel.j2",
        "grafana_tunnel_dest": "/tmp/_ParallelClusterMaker_stage/test-cluster-00001220260720/grafana_tunnel.test-cluster.sh",
        # Docker Compose plugin S3 staging: alinux2023 only, since it is the one
        # base_os with no docker-compose-plugin package.  stage_docker_compose is
        # false here because the default fixture is Ubuntu; the other three are
        # still present because make_pcluster.py threads them unconditionally and
        # vars_file.j2 references them, so a missing key is an UndefinedError the
        # moment the gate flips.  cluster_params_al2023_monitoring flips it.
        "stage_docker_compose": "false",
        "docker_compose_version": "v2.29.7",
        "docker_compose_arch": "x86_64",
        "docker_compose_checksum": "sha256:383ce6698cd5d5bbf958d2c8489ed75094e34a77d340404d9f32c4ae9e12baf0",
        # Performance paths
        "performance_rootdir": "/home/testuser/ParallelClusterMaker/hpc-benchmark",
        "performance_stage_dir": "/tmp/_ParallelClusterMaker_stage/test-cluster-00001220260720/hpc-benchmark/slurm",
        # Derived unconditionally by vars_file.j2 from ec2_user_home,
        # cluster_name, cluster_owner and scheduler; postinstall.j2 pulls
        # the performance tree into it from S3.
        "headnode_performance_dir_dest": "/home/ubuntu/hpc-benchmark/test-cluster/testuser/slurm",
        # Performance
        "sid": "slurm-test-cluster",
        # Ansible registered vars (used by sns/access templates)
        "head_node_public_ip": "1.2.3.4",
        "start_overall_timer": type("R", (), {"stdout": "2026-07-20 10:00:00"})(),
        "start_stack_creation_timer": type("R", (), {"stdout": "2026-07-20 10:01:00"})(),
        "stop_stack_creation_timer": type("R", (), {"stdout": "2026-07-20 10:30:00"})(),
        "stop_overall_timer": type("R", (), {"stdout": "2026-07-20 10:35:00"})(),
        "start_delete_timer": type("R", (), {"stdout": "2026-07-20 11:00:00"})(),
        "stop_delete_timer": type("R", (), {"stdout": "2026-07-20 11:10:00"})(),
        # Set by delete_pcluster.yml's cleanup-failure collection. Empty is the
        # clean-teardown case; cluster_params_orphaned_teardown exercises the
        # branch that reports survivors.
        "_orphaned_resources": [],
        # Derived by delete_pcluster.yml from the three delete outcomes. The
        # confirmed-gone wording is the default; cluster_params_unconfirmed_delete
        # renders the case the report used to claim success on.
        "_delete_headline": "Cluster test-cluster has been deleted.",
        # Resources teardown deliberately keeps. Distinct from _orphaned_resources:
        # these are choices, not failures, so they must never drive the exit
        # status -- but they bill until removed by hand, so they are reported.
        # Empty here so the clean-teardown case renders the no-retention branch;
        # cluster_params_retained_teardown exercises the other one.
        "_retained_resources": [],
        "ssh_keypair": "/home/testuser/ParallelClusterMaker/active_clusters/test-cluster/test-cluster-00001220260720_us-east-1.pem",
        # Absolute, matching make_pcluster.py's os.path.expanduser. A literal
        # "~/.ssh/known_hosts" is not expanded by the shell tasks that quote it.
        "ssh_known_hosts": "/home/testuser/.ssh/known_hosts",
    }


@pytest.fixture
def cluster_params_orphaned_teardown(cluster_params):
    """cluster_params variant where teardown cleanup left resources behind.

    Renders the destruction report's warning branch, which is otherwise dead
    text on every clean teardown.
    """
    overrides = {
        "_orphaned_resources": [
            "IAM role and instance profile pclustermaker-role-test-cluster-00001220260720",
            "S3 bucket parallelclustermaker-test-cluster-00001220260720",
        ],
    }
    return {**cluster_params, **overrides}


@pytest.fixture
def cluster_params_retained_teardown(cluster_params):
    """cluster_params variant where teardown deliberately kept resources.

    The reported case: --delete_s3_bucketname was not true, so the per-build
    bucket survives. A skipped delete registers as `''` rather than `.failed`,
    so nothing named it and the operator had no way to know from the teardown
    output that a bucket was still there billing.
    """
    overrides = {
        "_retained_resources": [
            "S3 bucket parallelclustermaker-test-cluster-00001220260720 "
            "(--delete_s3_bucketname was not true)",
            "CloudWatch log groups /aws/parallelcluster/test-cluster-* "
            "(retained 30 days; the only record of a failed build)",
        ],
    }
    return {**cluster_params, **overrides}


@pytest.fixture
def cluster_params_unconfirmed_delete(cluster_params):
    """cluster_params variant where the delete wait timed out.

    Nothing failed and nothing was deleted, so the orphan list is empty -- which
    is exactly why every surface used to report this as a successful teardown.
    """
    overrides = {
        "_delete_headline": "Deletion of cluster test-cluster was NOT confirmed.",
    }
    return {**cluster_params, **overrides}


@pytest.fixture
def cluster_params_slurm_accounting(cluster_params):
    """Accounting on, Ubuntu. Opt-in, so no other fixture enables it."""
    p = dict(cluster_params)
    p["enable_slurm_accounting"] = "true"
    return p


@pytest.fixture
def cluster_params_slurm_accounting_rhel(cluster_params_rhel):
    """The dnf family packages MariaDB under a different name."""
    p = dict(cluster_params_rhel)
    p["enable_slurm_accounting"] = "true"
    return p


@pytest.fixture
def cluster_params_rhel(cluster_params):
    """cluster_params variant on rhel9arm.

    Every other fixture is Ubuntu, so the `{% if 'ubuntu' in base_os %}` else-arm
    in preinstall.j2 and postinstall.j2 never expands without this one -- which is
    how a dnf call in a never-rendered branch used to pass a rendered-text
    assertion.  ec2_user must agree with _resolve_ec2_user's rhel arm.
    """
    overrides = {
        "base_os": "rhel9arm",
        "pcluster_os": "rhel9",
        "ec2_user": "ec2-user",
        "ec2_user_home": "/home/ec2-user",
        "headnode_performance_dir_dest": "/home/ec2-user/hpc-benchmark/test-cluster/testuser/slurm",
        "ec2_user_src": "/home/ec2-user/src",
    }
    return {**cluster_params, **overrides}


@pytest.fixture
def cluster_params_rhel_gpu_queue(cluster_params_gpu_queue_enabled):
    """rhel9 with CPU and GPU queues.

    The nvtop/htop install sits inside `{% if enable_gpu == 'true' %}`, so the
    default RHEL fixture leaves it unrendered -- a dnf call mutated into the GPU
    block survives a run using cluster_params_rhel alone.
    """
    overrides = {
        "base_os": "rhel9",
        "pcluster_os": "rhel9",
        "ec2_user": "ec2-user",
        "ec2_user_home": "/home/ec2-user",
        "headnode_performance_dir_dest": "/home/ec2-user/hpc-benchmark/test-cluster/testuser/slurm",
        "ec2_user_src": "/home/ec2-user/src",
    }
    return {**cluster_params_gpu_queue_enabled, **overrides}


@pytest.fixture
def cluster_params_al2023(cluster_params):
    """cluster_params variant on alinux2023arm.

    AL2023 shares the dnf family with RHEL but not its package set: no luarocks,
    no tcllib, no nvtop, no EPEL.  Without a fixture on this arm the
    `{% elif 'alinux' in base_os %}` branches never expand, and a wrong package
    name there is invisible to every rendered-text assertion.  ec2_user must agree
    with _resolve_ec2_user's alinux2023 entry.
    """
    overrides = {
        "base_os": "alinux2023arm",
        "pcluster_os": "alinux2023",
        "ec2_user": "ec2-user",
        "ec2_user_home": "/home/ec2-user",
        "headnode_performance_dir_dest": "/home/ec2-user/hpc-benchmark/test-cluster/testuser/slurm",
        "ec2_user_src": "/home/ec2-user/src",
    }
    return {**cluster_params, **overrides}


@pytest.fixture
def cluster_params_al2023_gpu_queue(cluster_params_gpu_queue_enabled):
    """alinux2023 (x86_64) with CPU and GPU queues.

    The htop install sits inside `{% if enable_gpu == 'true' %}`, so
    cluster_params_al2023 alone leaves that block unrendered -- the same gap the
    RHEL GPU fixture exists to close.  x86_64 here so the pair covers both arch
    values.
    """
    overrides = {
        "base_os": "alinux2023",
        "pcluster_os": "alinux2023",
        "ec2_user": "ec2-user",
        "ec2_user_home": "/home/ec2-user",
        "headnode_performance_dir_dest": "/home/ec2-user/hpc-benchmark/test-cluster/testuser/slurm",
        "ec2_user_src": "/home/ec2-user/src",
    }
    return {**cluster_params_gpu_queue_enabled, **overrides}


@pytest.fixture
def cluster_params_al2023_monitoring(cluster_params_al2023):
    """alinux2023arm with monitoring on, so the compose-staging block renders.

    The wrapper's Docker Compose block is gated on stage_docker_compose, which
    make_pcluster.py sets only for `enable_monitoring and 'alinux' in base_os`.
    Both halves have to be true in one fixture or the block is dead text.
    """
    overrides = {
        "enable_monitoring": "true",
        "enable_slurm_accounting": "false",
        "stage_docker_compose": "true",
        "docker_compose_version": "v2.29.7",
        "docker_compose_arch": "aarch64",
        "docker_compose_checksum": "sha256:6e9fbd5daa20dca5d7d89145081ae8155d68ef2928b497d9f85b54fe0f9dbb2c",
        "docker_compose_s3_dest": "docker-compose-linux-aarch64-v2.29.7",
    }
    return {**cluster_params_al2023, **overrides}


@pytest.fixture
def cluster_params_custom_ami(cluster_params):
    """cluster_params variant with custom_ami and placement_group set.

    Exercises conditional template branches that are skipped by the default fixture.
    """
    overrides = {
        "custom_ami": "ami-0abc1234567890def",
        "placement_group": "test-cluster-pg",
        "use_private_compute_subnet": "true",
    }
    return {**cluster_params, **overrides}


@pytest.fixture
def cluster_params_monitoring_enabled(cluster_params):
    """cluster_params variant with enable_monitoring=true.

    Exercises the Sequence CustomActions block in config.pcluster.j2,
    the compute queue monitoring hook, and the vars_file monitoring section.
    """
    overrides = {
        "enable_monitoring": "true",
        "enable_slurm_accounting": "false",
    }
    return {**cluster_params, **overrides}


@pytest.fixture
def cluster_params_loginnode_enabled(cluster_params):
    """cluster_params variant with enable_loginnode=true, a pool of 1.

    Exercises the LoginNodes block in config.pcluster.j2, the postinstall
    LoginNode case arm, and the login-node lines in the reporting surfaces.
    """
    overrides = {
        "enable_loginnode": "true",
        "loginnode_instance_type": "c8g.xlarge",
        "loginnode_count": 1,
        "loginnode_subnet_id": "subnet-0abc123",
    }
    return {**cluster_params, **overrides}


@pytest.fixture
def cluster_params_loginnode_pool(cluster_params_loginnode_enabled):
    """cluster_params_loginnode_enabled variant with a pool of 3.

    Distinguishes "loginnode_count threads through" from "the template
    happens to always render 1".
    """
    overrides = {
        "loginnode_count": 3,
    }
    return {**cluster_params_loginnode_enabled, **overrides}


@pytest.fixture
def cluster_params_gpu_enabled(cluster_params):
    """cluster_params variant with a GPU-only queue (p3.2xlarge).

    No CPU queue — exercises the GPU-only cluster path and postinstall GPU block.
    """
    overrides = {
        "compute_instance_type": "",
        "cpu_instance_types": [],
        "gpu_instance_type": "p3.2xlarge",
        "gpu_instance_types": ["p3.2xlarge"],
        "enable_cpu_queue": "false",
        "enable_gpu_queue": "true",
        "enable_gpu": "true",
        "gpu_ranks_per_node": 1,
        # p3.2xlarge: 8 vCPUs, 2 threads per core, so 8 hyperthreaded and 4 not.
        # The fixture leaves hyperthreading true.
        "cpu_ranks_per_node": 0,
        "gpu_vcpus_per_node": 8,
        "base_os": "ubuntu2404",
        "pcluster_os": "ubuntu2404",
    }
    return {**cluster_params, **overrides}


@pytest.fixture
def cluster_params_gpu_gdr_enabled(cluster_params):
    """cluster_params with p4d.24xlarge in the GPU queue + EFA-GDR enabled.

    p4d is a GPU family so it belongs in gpu_instance_types.  With enable_efa=true
    and enable_efa_gdr=true the GPU queue ComputeResources block must emit
    GdrSupport: true.
    """
    overrides = {
        "compute_instance_type": "",
        "cpu_instance_types": [],
        "gpu_instance_type": "p4d.24xlarge",
        "gpu_instance_types": ["p4d.24xlarge"],
        "enable_cpu_queue": "false",
        "enable_gpu_queue": "true",
        "enable_gpu": "true",
        "gpu_ranks_per_node": 8,
        "cpu_ranks_per_node": 0,
        "gpu_vcpus_per_node": 96,
        "enable_efa": "true",
        "enable_efa_gdr": "true",
        "base_os": "ubuntu2404",
        "pcluster_os": "ubuntu2404",
    }
    return {**cluster_params, **overrides}


@pytest.fixture
def cluster_params_hpc_benchmarks_disabled(cluster_params):
    """cluster_params variant with enable_hpc_benchmarks=false.

    Verifies that the benchmark sync block is absent from postinstall
    when the feature flag is off.
    """
    return {**cluster_params, "enable_hpc_benchmarks": "false"}


@pytest.fixture
def cluster_params_efa_enabled(cluster_params):
    """cluster_params variant with enable_efa=true (c5n instance).

    Verifies the Efa: block appears in config.pcluster.j2.
    """
    overrides = {
        "enable_efa": "true",
        "enable_efa_gdr": "false",
        "compute_instance_type": "c5n.18xlarge",
        "cpu_instance_types": ["c5n.18xlarge"],
        # c5n.18xlarge: 72 vCPUs, 2 threads per core.  hyperthreading is true here.
        "cpu_ranks_per_node": 72,
    }
    return {**cluster_params, **overrides}


@pytest.fixture
def cluster_params_multi_instance_cpu(cluster_params):
    """cluster_params with multiple CPU instance types in the CPU queue."""
    overrides = {
        "compute_instance_type": "c8g.2xlarge,c7g.2xlarge,c6g.2xlarge",
        "cpu_instance_types": ["c8g.2xlarge", "c7g.2xlarge", "c6g.2xlarge"],
        # All three are 8 vCPUs at 1 thread per core; the min is 8.
        "cpu_ranks_per_node": 8,
        "gpu_instance_type": "",
        "gpu_instance_types": [],
        "enable_cpu_queue": "true",
        "enable_gpu_queue": "false",
    }
    return {**cluster_params, **overrides}


@pytest.fixture
def cluster_params_gpu_queue_enabled(cluster_params):
    """cluster_params with separate CPU and GPU queues."""
    overrides = {
        "compute_instance_type": "c8g.2xlarge",
        "cpu_instance_types": ["c8g.2xlarge"],
        "gpu_instance_type": "p3.2xlarge",
        "gpu_instance_types": ["p3.2xlarge"],
        "enable_cpu_queue": "true",
        "enable_gpu_queue": "true",
        "enable_gpu": "true",
        "gpu_ranks_per_node": 1,
        "cpu_ranks_per_node": 8,
        "gpu_vcpus_per_node": 8,
        "base_os": "ubuntu2404",
        "pcluster_os": "ubuntu2404",
    }
    return {**cluster_params, **overrides}


@pytest.fixture
def cluster_params_gpu_no_nvidia(cluster_params_gpu_enabled):
    """GPU-only queue whose instance types report no NVIDIA devices (g4ad is AMD).

    nvidia_gpu_count() returns 0 for these, so gpu_ranks_per_node is 0 and the
    benchmark job script must fall back to a CPU-shaped rank count rather than
    emitting --ntasks-per-node=0, which Slurm rejects.
    """
    overrides = {
        "gpu_instance_type": "g4ad.4xlarge",
        "gpu_instance_types": ["g4ad.4xlarge"],
        "gpu_ranks_per_node": 0,
        # g4ad.4xlarge is 16 vCPUs. gpu_ranks_per_node is 0 because none of them
        # are NVIDIA, but the core count is unaffected -- which is exactly why
        # --ntasks must not be derived from the GPU count.
        "gpu_vcpus_per_node": 16,
    }
    return {**cluster_params_gpu_enabled, **overrides}


@pytest.fixture(autouse=True)
def _no_operator_defaults_file(monkeypatch, tmp_path_factory):
    """Keep the developer's own `<cluster>_defaults.yml` out of the suite.

    Those files are auto-applied now, by the CLI and the MCP server both,
    and they are gitignored -- so a test that builds a cluster named
    `osiris` resolves against the operator's real osiris_defaults.yml on
    their laptop and against nothing at all in CI. That is a green local
    run that means nothing, the same trap as the AZ verification reaching
    live AWS.

    Discovery is pointed at an empty directory for every test. A test that
    wants a defaults file writes one and repoints this seam itself; see
    TestTheDefaultsFileIsAppliedWhenItExists.
    """
    import pcluster_core

    empty = tmp_path_factory.mktemp("no_defaults")
    monkeypatch.setattr(pcluster_core, "_default_repo_root", lambda: str(empty))

    # The sibling seam. mcp_server.tools._repo_root() is what every MCP
    # tool resolves active_clusters/ and src/vars_files/ against, and it
    # was never repointed -- so a test driving list_queues or add_queue
    # would read (and core_add_queue would *write*) the developer's live
    # cluster state, and see nothing at all in CI. Latent only because no
    # test drove those tools through the client yet.
    try:
        import mcp_server.tools as _mcp_tools
    except ImportError:
        return
    monkeypatch.setattr(_mcp_tools, "_repo_root", lambda: str(empty))


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "allow_aws: this test may put a real request on the wire",
    )


@pytest.fixture(autouse=True)
def _no_test_reaches_aws(request, monkeypatch):
    """No test may make a real AWS API call.

    CLAUDE.md states that every AWS call in this suite is stubbed, but
    nothing enforced it -- and the failure is invisible where it is
    written: a developer machine has credentials, so an unstubbed call
    succeeds and the test passes. It surfaces only on a runner without
    them, as a NoCredentialsError raised far from the test that caused it.
    That has now happened twice, most recently in
    TestCreateClusterCannotKillTheServer, whose token minting ran the real
    preview_cluster_config -- which resolves the region by asking EC2
    rather than trimming the AZ name, deliberately.

    Patched at botocore's HTTP layer rather than at boto3.client, because a
    test is entitled to *construct* a client: many stub one method on a
    real client object, and several assert on region binding. What no test
    may do is put a request on the wire. The message names the URL, so the
    offending call is identifiable without reading a stack trace.
    """
    if request.node.get_closest_marker("allow_aws"):
        return

    try:
        from botocore.httpsession import URLLib3Session
    except ImportError:
        return

    real_send = URLLib3Session.send

    def _blocked(self, http_request, *args, **kwargs):
        url = getattr(http_request, "url", "") or ""
        host = urlparse(url).hostname or ""
        # Only AWS *service* endpoints are the concern. botocore's
        # credential chain probes the instance metadata service at
        # 169.254.169.254 during client construction, which is not a test
        # calling AWS -- it is link-local, unroutable on a CI runner, and
        # fails fast on its own. Blocking it here failed 13 tests that
        # reach no service at all.
        if not host.endswith("amazonaws.com"):
            return real_send(self, http_request, *args, **kwargs)
        raise AssertionError(
            "this test tried to reach AWS: "
            f"{getattr(http_request, 'method', '?')} {url} -- every AWS call "
            "in this suite must be stubbed, since unstubbed it passes "
            "wherever there are credentials and fails in CI."
        )

    monkeypatch.setattr(URLLib3Session, "send", _blocked)


def assert_source_is_real(source, label=""):
    """Prove a haystack is the source you think it is, before asserting absence.

    `assert needle not in source` is satisfied by an empty string, a
    truncated read, a renamed file, and a path typo. It fails loudly when
    the *code* regresses and silently when the *test* does, which is the
    wrong way round: a negative assertion that has stopped reading anything
    still passes, forever, while the rule it names goes unguarded.

    Seventeen negative assertions over Python source had no such control.
    They were found by asking which tests read source and assert absence
    without also asserting presence -- and `TestEveryNegativeSourceAssertion
    ProvesItsHaystack` in tests/test_negative_assertions.py keeps the answer
    at zero, so a new one cannot be added quietly.
    """
    assert source and source.strip(), f"{label or 'source'} is empty"
    assert "def " in source or "import " in source, (
        f"{label or 'source'} does not look like Python source -- a negative "
        f"assertion against it would pass without reading anything"
    )


def assert_absent_ignoring_formatting(needle, source, label=""):
    """Assert `needle` is absent, comparing with all whitespace removed.

    A literal needle like `"(attempt + 1) * 30"` stops matching the moment a
    formatter wraps that expression across lines -- and a *negative*
    assertion that stops matching passes. The rule it names would then be
    unguarded with the suite green, which is the failure mode
    `assert_source_is_real` covers for an empty haystack and this one covers
    for a reshaped needle.

    Measured before writing: 22 negative assertions over Python source carry
    a literal needle, and 4 of them contain parentheses, commas or runs of
    spaces that `black` could move. Those 4 use this; the other 18 cannot be
    reshaped by any formatter.
    """
    import re

    assert_source_is_real(source, label)
    squeezed_needle = re.sub(r"\s+", "", needle)
    assert squeezed_needle, f"{label or 'needle'} is empty after squeezing"
    assert squeezed_needle not in re.sub(r"\s+", "", source), (
        f"{label or 'source'} contains {needle!r} (matched ignoring layout)"
    )
