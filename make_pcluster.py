#!/usr/bin/env python
#
################################################################################
# Name:		make_pcluster.py
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	April 20, 2019
# Purpose:	Python3 wrapper for customizing ParallelCluster stacks
################################################################################

import os
import sys

_repo_root = os.path.dirname(os.path.abspath(__file__))
_src_dir = os.path.join(_repo_root, "src")
if os.path.realpath(sys.prefix) != os.path.realpath(os.path.join(_repo_root, ".venv")):
    sys.exit(
        f"ERROR: Run this script inside the repo virtual environment.\n"
        f"  $ source {os.path.join(_repo_root, '.venv', 'bin', 'activate')}\n"
        f"  $ {sys.argv[0]} ..."
    )

# Load the required Python libraries.

import argparse
import boto3
import re
import subprocess  # noqa: F401 -- also used directly below (ansible --version),
# but kept importing the bare module (not just the one function) so tests that
# patch make_pcluster.subprocess.run also reach core_create_cluster's calls,
# which resolve subprocess.run in pcluster_core's namespace -- same shared
# process-wide module object either way (see kill_pcluster.py's own note).
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    EndpointConnectionError,
    NoCredentialsError,
)

# Import the list of supported EC2 instances and some external functions.
sys.path.insert(0, _src_dir)
from pcluster_core import (
    MAKE_CLUSTER_DEFAULTS,
    PClusterMakerError,
    _validate_at_least_one_queue,
    _derive_az_list,
    MakeClusterParams,
    _validate_az_input,
    _validate_cluster_name,
    _validate_cluster_owner,
    _validate_queue_sizes,
    _load_defaults_file,
    _resolve as _pcore_resolve,
    _resolve_bool as _pcore_resolve_bool,
    _default_loginnode_instance_type,
    _derive_head_node_bootstrap_timeout,
    _derive_docker_compose_staging,
    _validate_download_checksum,
    core_create_cluster,
)
from pcluster_aux_data import ARM_OSES
from pcluster_aux_data import illegal_az_msg
from pcluster_aux_data import p_val
from pcluster_aux_data import print_TextHeader
from pcluster_aux_data import refer_to_docs_and_quit


def main():
    # Parse input from the command line.

    parser = argparse.ArgumentParser(
        description="make_pcluster.py: Command-line interface to build custom ParallelCluster stacks in AWS"
    )

    # Configure parser arguments for the required variables.

    parser.add_argument(
        "--az",
        "--AvailabilityZone",
        "-A",
        help="AWS Availability Zone (REQUIRED)",
        required=True,
    )
    parser.add_argument(
        "--cluster_name", "-N", help="name of the cluster (REQUIRED)", required=True
    )
    parser.add_argument(
        "--cluster_owner",
        "-O",
        help="username of the cluster owner (REQUIRED)",
        required=True,
    )
    parser.add_argument(
        "--cluster_owner_email",
        "-E",
        help="email address of the cluster owner (REQUIRED)",
        required=True,
    )

    # Configure arguments for the optional variables.
    # Defaults are None here; hardcoded fallbacks applied after optional
    # pcluster_defaults.yml loading so CLI args always take precedence.

    parser.add_argument(
        "--use_defaults",
        metavar="DEFAULTS_FILE",
        help="path to a YAML defaults file (example: --use_defaults=myteam-prod.yml). "
        "Copy pcluster_defaults.yml to your own file first — do not load the "
        "toolkit's own copy directly.",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--ansible_verbosity",
        choices=["-v", "-vv", "-vvv", "-vvvv", ""],
        help="Set the Ansible verbosity level (default = \"\", no extra verbosity)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--base_os",
        choices=[
            "ubuntu2204",
            "ubuntu2404",
            "ubuntu2204arm",
            "ubuntu2404arm",
            "rhel9",
            "rhel9arm",
            "alinux2023",
            "alinux2023arm",
        ],
        help="cluster base operating system (default = ubuntu2404)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--cluster_owner_department",
        choices=[
            "analytics",
            "clinical",
            "commercial",
            "compbio",
            "compchem",
            "datasci",
            "design",
            "development",
            "hpc",
            "imaging",
            "manufacturing",
            "medical",
            "modeling",
            "operations",
            "proteomics",
            "robotics",
            "qa",
            "research",
            "scicomp",
        ],
        help="department of the cluster_owner (default = hpc)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--cluster_type",
        choices=["ondemand", "spot"],
        help="ondemand or spot instances (default = spot)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--compute_instance_type",
        help="compute EC2 instance type(s); comma-separated list for multiple types (default = unset; set this, --gpu_instance_type, or both)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--compute_root_volume_size",
        help="compute EBS root volume size in GB (default = 250)",
        required=False,
        type=int,
        default=None,
    )
    parser.add_argument(
        "--compute_root_volume_type",
        choices=["gp2", "gp3", "io1", "io2", "st1"],
        help="compute root EBS volume type (default = gp3)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--compute_root_volume_iops",
        help="compute root volume IOPS for gp3/io1/io2 (default = 3000)",
        required=False,
        type=int,
        default=None,
    )
    parser.add_argument(
        "--compute_root_volume_throughput",
        help="compute root volume throughput in MB/s for gp3 (default = 125)",
        required=False,
        type=int,
        default=None,
    )
    parser.add_argument(
        "--custom_ami",
        help="custom AMI ID (default = NONE)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--debug_mode",
        "-D",
        choices=["true", "false"],
        help="enable debug mode (default = false)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--ebs_encryption",
        choices=["true", "false"],
        help="enable EBS encryption (default = false)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--ebs_shared_dir",
        help="shared EBS mount path (default = /shared)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--ebs_shared_volume_size",
        help="EBS shared volume size in GB (default = 250)",
        required=False,
        type=int,
        default=None,
    )
    parser.add_argument(
        "--ebs_shared_volume_type",
        choices=["gp2", "gp3", "io1", "io2", "st1"],
        help="EBS volume type (default = gp3)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--ebs_shared_volume_iops",
        help="EBS shared volume IOPS, applies to gp3/io1/io2 (default = 3000)",
        required=False,
        type=int,
        default=None,
    )
    parser.add_argument(
        "--ebs_shared_volume_throughput",
        help="EBS shared volume throughput in MB/s, applies to gp3 (default = 125)",
        required=False,
        type=int,
        default=None,
    )
    parser.add_argument(
        "--efs_encryption",
        choices=["true", "false"],
        help="enable EFS encryption in transit (default = false)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--efs_performance_mode",
        choices=["generalPurpose", "maxIO"],
        help="EFS performance mode (default = generalPurpose)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--efs_throughput_mode",
        choices=["bursting", "provisioned", "elastic"],
        help="EFS throughput mode (default = bursting)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--enable_efs",
        choices=["true", "false"],
        help="enable EFS (default = false)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--enable_efa",
        choices=["true", "false"],
        help="enable EFA (default = false)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--enable_external_nfs",
        choices=["true", "false"],
        help="enable external NFS mounts (default = false)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--enable_loginnode",
        choices=["true", "false"],
        help="enable a login node pool separate from the head node (default = false)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--loginnode_instance_type",
        help=(
            "login node EC2 instance type "
            "(falls back to c8g.xlarge on Graviton base_os, c5.xlarge on "
            "x86_64, when unset and no --use_defaults file sets one)"
        ),
        required=False,
        default=None,
    )
    parser.add_argument(
        "--loginnode_count",
        help="number of login nodes in the pool (default = 1)",
        required=False,
        type=int,
        default=None,
    )
    parser.add_argument(
        "--enable_fsx",
        choices=["true", "false"],
        help="enable FSx for Lustre (default = false)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--enable_hpc_benchmarks",
        choices=["true", "false"],
        help="deploy HPC performance test suite (default = false)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--enable_monitoring",
        choices=["true", "false"],
        help="deploy Grafana/Prometheus monitoring dashboard (default = false)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--gpu_instance_type",
        help="GPU compute EC2 instance type(s); comma-separated list (leave empty for no GPU queue)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--gpu_root_volume_size",
        help="GPU compute node EBS root volume size in GB (default = 250)",
        required=False,
        type=int,
        default=None,
    )
    parser.add_argument(
        "--gpu_root_volume_type",
        choices=["gp2", "gp3", "io1", "io2", "st1"],
        help="GPU compute node EBS root volume type (default = gp3)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--gpu_root_volume_iops",
        help="GPU compute node EBS root volume IOPS (default = 3000)",
        required=False,
        type=int,
        default=None,
    )
    parser.add_argument(
        "--gpu_root_volume_throughput",
        help="GPU compute node EBS root volume throughput in MB/s (default = 125)",
        required=False,
        type=int,
        default=None,
    )
    parser.add_argument(
        "--monitoring_version",
        help="aws-parallelcluster-monitoring release tag (default = v2.6)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--monitoring_version_checksum",
        help="SHA-256 checksum of the monitoring tarball (format: sha256:<hex>)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--docker_compose_version",
        help="Docker Compose CLI plugin release tag staged to S3 for alinux2023 (default = v2.29.7)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--docker_compose_checksum_x86_64",
        help="SHA-256 checksum of the x86_64 Docker Compose plugin (format: sha256:<hex>)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--docker_compose_checksum_aarch64",
        help="SHA-256 checksum of the aarch64 Docker Compose plugin (format: sha256:<hex>)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--external_nfs_server",
        help="external NFS server hostname (default = unset; required when --enable_external_nfs=true)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--enable_fsx_hydration",
        choices=["true", "false"],
        help="enable FSxL hydration from S3 (default = false)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--fsx_s3_import_bucket",
        help="S3 bucket to hydrate Lustre from (default = UNDEFINED)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--fsx_s3_import_path",
        help="S3 import path (default = import)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--fsx_s3_export_bucket",
        help="S3 bucket to dehydrate Lustre to - AWS requires this to be the same bucket as fsx_s3_import_bucket, with a different path (default = UNDEFINED, which follows the import bucket and path)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--fsx_s3_export_path",
        help="S3 export path (default = export)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--fsx_size",
        help="Lustre file system size in GB, multiples of 1200 (default = 1200)",
        required=False,
        type=int,
        default=None,
    )
    parser.add_argument(
        "--fsx_chunk_size",
        help="S3 import chunk size in MB (default = 1024)",
        required=False,
        type=int,
        default=None,
    )
    parser.add_argument(
        "--hyperthreading",
        choices=["true", "false"],
        help="enable Intel HyperThreading (default = true)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--initial_cpu_queue_size",
        help="initial CPU compute node count (default = 2)",
        required=False,
        type=int,
        default=None,
    )
    parser.add_argument(
        "--initial_gpu_queue_size",
        help="initial GPU compute node count (default = 2)",
        required=False,
        type=int,
        default=None,
    )
    parser.add_argument(
        "--maintain_cpu_initial_size",
        choices=["true", "false"],
        help="keep initial CPU nodes always running (default = false)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--maintain_gpu_initial_size",
        choices=["true", "false"],
        help="keep initial GPU nodes always running (default = false)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--headnode_instance_type",
        help="head node EC2 instance type (required — no default)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--headnode_root_volume_size",
        help="head node EBS root volume size in GB (default = 100)",
        required=False,
        type=int,
        default=None,
    )
    parser.add_argument(
        "--headnode_root_volume_type",
        choices=["gp2", "gp3", "io1", "io2", "st1"],
        help="head node root EBS volume type (default = gp3)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--headnode_root_volume_iops",
        help="head node root volume IOPS for gp3/io1/io2 (default = 3000)",
        required=False,
        type=int,
        default=None,
    )
    parser.add_argument(
        "--headnode_root_volume_throughput",
        help="head node root volume throughput in MB/s for gp3 (default = 125)",
        required=False,
        type=int,
        default=None,
    )
    parser.add_argument(
        "--max_cpu_queue_size",
        help="maximum CPU compute node count (default = 8)",
        required=False,
        type=int,
        default=None,
    )
    parser.add_argument(
        "--max_gpu_queue_size",
        help="maximum GPU compute node count (default = 8)",
        required=False,
        type=int,
        default=None,
    )
    parser.add_argument(
        "--placement_group",
        choices=["NONE", "DYNAMIC"],
        help="EC2 placement group (default = NONE)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--pre_install_script",
        help="pre-installation script path relative to repo root (default = scripts/pre-deployment.sh)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--post_install_script",
        help="post-installation script path relative to repo root (default = scripts/post-deployment.sh)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--prod_level",
        choices=["dev", "test", "stage", "prod"],
        help="operating level (default = dev)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--project_id",
        "-P",
        help="project name or ID (default = UNDEFINED)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--pcluster_create_timeout",
        help="stack creation poll retries, each 60 s (default = 60 → 60 min)",
        required=False,
        type=int,
        default=None,
    )
    parser.add_argument(
        "--scaledown_idletime",
        help="idle minutes before compute node terminates (default = 5)",
        required=False,
        type=int,
        default=None,
    )
    parser.add_argument(
        "--scheduler",
        "-S",
        choices=["slurm"],
        help="cluster scheduler (default = slurm)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--turbot_account",
        "-T",
        help='Turbot account ID, set to "disabled" if not used (default = disabled)',
        required=False,
        default=None,
    )
    parser.add_argument(
        "--vpc_name",
        help="VPC Name tag (default = vpc_default)",
        required=False,
        default=None,
    )
    # WARNING: The toolkit auto-discovers VPCs and subnets by convention (default VPC, first
    # subnet per AZ). This is convenient for quick tests but not reliable in accounts with
    # multiple subnets or complex VPC layouts. For production clusters, explicitly specify
    # --headnode_subnet_id, --compute_subnet_ids, and --vpc_name to ensure the correct
    # network resources are used. Do not rely on auto-discovery for production workloads.
    parser.add_argument(
        "--headnode_subnet_id",
        help="explicit subnet ID for the head node; overrides auto-discovery",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--loginnode_subnet_id",
        help="explicit subnet ID for the login node pool; overrides auto-discovery",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--compute_az",
        help="comma-separated AZs for the compute fleet (default: same as --az)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--compute_subnet_ids",
        help="comma-separated subnet IDs for compute nodes; overrides auto-discovery",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--use_private_compute_subnet",
        choices=["true", "false"],
        help="deploy compute nodes into private subnets (default = false)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--gpu_az",
        help="comma-separated AZs for the GPU queue; defaults to compute_az then --az",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--gpu_subnet_ids",
        help="comma-separated subnet IDs for GPU nodes; defaults to compute_subnet_ids",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--use_private_gpu_subnet",
        choices=["true", "false"],
        help="deploy GPU nodes into private subnets (default = false)",
        required=False,
        default=None,
    )

    # Parse the command used to create this cluster stack.

    cluster_build_command = " ".join(sys.argv)

    # Parse CLI args, then overlay pcluster_defaults.yml if --use_defaults was
    # passed, then apply hardcoded fallbacks for anything still unset.
    # Precedence: CLI arg > pcluster_defaults.yml > hardcoded default.

    args = parser.parse_args()

    # Moved to pcluster_core so an MCP wrapper can reach it too; the
    # local name is kept because _resolve/_resolve_bool below and the
    # rest of main() refer to it.
    _HARDCODED_DEFAULTS = MAKE_CLUSTER_DEFAULTS

    _file_defaults = {}
    if args.use_defaults:
        _toolkit_defaults = os.path.join(_repo_root, "pcluster_defaults.yml")
        _file_defaults = _load_defaults_file(
            os.path.abspath(args.use_defaults), _toolkit_defaults, args.cluster_name
        )
        print(f"Defaults: loaded from {args.use_defaults}")
    else:
        _candidate = os.path.join(_repo_root, f"{args.cluster_name}_defaults.yml")
        if os.path.exists(_candidate):
            print(
                f"*** WARNING ***\n"
                f"  '{args.cluster_name}_defaults.yml' exists but was not loaded.\n"
                f"  If you meant to use it, re-run with: --use_defaults={args.cluster_name}_defaults.yml"
            )

    def _resolve(name, cast=None):
        return _pcore_resolve(name, args, _file_defaults, _HARDCODED_DEFAULTS, cast)

    def _resolve_bool(name):
        return _pcore_resolve_bool(name, args, _file_defaults, _HARDCODED_DEFAULTS)

    ansible_verbosity = _resolve("ansible_verbosity")
    az = args.az
    _validate_az_input(az)
    base_os = _resolve("base_os")
    cluster_name = args.cluster_name
    cluster_owner = args.cluster_owner
    cluster_owner_department = _resolve("cluster_owner_department")
    cluster_owner_email = args.cluster_owner_email
    cluster_type = _resolve("cluster_type")
    compute_instance_type = _resolve("compute_instance_type")
    compute_root_volume_size = _resolve("compute_root_volume_size", int)
    compute_root_volume_type = _resolve("compute_root_volume_type")
    compute_root_volume_iops = _resolve("compute_root_volume_iops", int)
    compute_root_volume_throughput = _resolve("compute_root_volume_throughput", int)
    custom_ami = _resolve("custom_ami")
    debug_mode = _resolve_bool("debug_mode")
    ebs_encryption = _resolve_bool("ebs_encryption")
    ebs_shared_dir = _resolve("ebs_shared_dir")
    ebs_shared_volume_size = _resolve("ebs_shared_volume_size", int)
    ebs_shared_volume_type = _resolve("ebs_shared_volume_type")
    ebs_shared_volume_iops = _resolve("ebs_shared_volume_iops", int)
    ebs_shared_volume_throughput = _resolve("ebs_shared_volume_throughput", int)
    efs_encryption = _resolve("efs_encryption")
    efs_performance_mode = _resolve("efs_performance_mode")
    efs_throughput_mode = _resolve("efs_throughput_mode")
    enable_efa = _resolve_bool("enable_efa")
    enable_efs = _resolve_bool("enable_efs")
    enable_external_nfs = _resolve_bool("enable_external_nfs")
    enable_loginnode = _resolve_bool("enable_loginnode")
    loginnode_instance_type = _resolve("loginnode_instance_type")
    if loginnode_instance_type is None:
        loginnode_instance_type = _default_loginnode_instance_type(base_os)
    loginnode_count = _resolve("loginnode_count", int)
    gpu_instance_type = _resolve("gpu_instance_type")
    gpu_root_volume_size = _resolve("gpu_root_volume_size", int)
    gpu_root_volume_type = _resolve("gpu_root_volume_type")
    gpu_root_volume_iops = _resolve("gpu_root_volume_iops", int)
    gpu_root_volume_throughput = _resolve("gpu_root_volume_throughput", int)
    enable_fsx = _resolve_bool("enable_fsx")
    enable_fsx_hydration = _resolve_bool("enable_fsx_hydration")
    enable_hpc_benchmarks = _resolve_bool("enable_hpc_benchmarks")
    enable_monitoring = _resolve_bool("enable_monitoring")
    monitoring_version = _resolve("monitoring_version")
    if not re.fullmatch(r"v[0-9]+\.[0-9]+(\.[0-9]+)?", monitoring_version):
        sys.exit(
            f"*** ERROR ***\n"
            f'  Invalid monitoring_version "{monitoring_version}". '
            f"Must match v<MAJOR>.<MINOR>[.<PATCH>] (e.g. v2.6 or v2.6.1)."
        )
    monitoring_version_checksum = _resolve("monitoring_version_checksum")
    docker_compose_version = _resolve("docker_compose_version")
    stage_docker_compose, docker_compose_arch = _derive_docker_compose_staging(
        base_os=base_os,
        arm_oses=ARM_OSES,
        enable_monitoring=enable_monitoring,
        version=docker_compose_version,
    )
    docker_compose_checksum = _resolve(f"docker_compose_checksum_{docker_compose_arch}")
    # Validated here, not at the get_url task: a bad checksum otherwise surfaces
    # 18 tasks into the playbook, after the IAM policies, role, keypair, and S3
    # bucket already exist and have to be cleaned up before a retry.  Only the
    # checksums this build will actually download are checked.
    if enable_monitoring:
        _validate_download_checksum("monitoring_version_checksum", monitoring_version_checksum)
        if stage_docker_compose:
            _validate_download_checksum(
                f"docker_compose_checksum_{docker_compose_arch}", docker_compose_checksum
            )
    external_nfs_server = _resolve("external_nfs_server")
    fsx_chunk_size = _resolve("fsx_chunk_size", int)
    fsx_s3_export_bucket = _resolve("fsx_s3_export_bucket")
    fsx_s3_export_path = _resolve("fsx_s3_export_path")
    fsx_s3_import_bucket = _resolve("fsx_s3_import_bucket")
    fsx_s3_import_path = _resolve("fsx_s3_import_path")
    fsx_size = _resolve("fsx_size", int)
    _configured_bootstrap_timeout = _resolve("head_node_bootstrap_timeout", int)
    head_node_bootstrap_timeout = _derive_head_node_bootstrap_timeout(
        configured=_configured_bootstrap_timeout,
        enable_efs=enable_efs,
        enable_fsx=enable_fsx,
    )
    hyperthreading = _resolve_bool("hyperthreading")
    initial_cpu_queue_size = _resolve("initial_cpu_queue_size", int)
    initial_gpu_queue_size = _resolve("initial_gpu_queue_size", int)
    maintain_cpu_initial_size = _resolve_bool("maintain_cpu_initial_size")
    maintain_gpu_initial_size = _resolve_bool("maintain_gpu_initial_size")
    headnode_instance_type = _resolve("headnode_instance_type")
    headnode_root_volume_size = _resolve("headnode_root_volume_size", int)
    headnode_root_volume_type = _resolve("headnode_root_volume_type")
    headnode_root_volume_iops = _resolve("headnode_root_volume_iops", int)
    headnode_root_volume_throughput = _resolve("headnode_root_volume_throughput", int)
    max_cpu_queue_size = _resolve("max_cpu_queue_size", int)
    max_gpu_queue_size = _resolve("max_gpu_queue_size", int)
    placement_group = _resolve("placement_group")
    pre_install_script = _resolve("pre_install_script")
    post_install_script = _resolve("post_install_script")
    for _script_name, _script_val in (
        ("pre_install_script", pre_install_script),
        ("post_install_script", post_install_script),
    ):
        _resolved = os.path.realpath(os.path.join(_repo_root, _script_val))
        if not _resolved.startswith(os.path.realpath(_repo_root) + os.sep):
            sys.exit(
                f"ERROR: {_script_name} path escapes the repo root: {_script_val}\n"
                f"  Paths must be relative to the project directory."
            )
    prod_level = _resolve("prod_level")
    project_id = _resolve("project_id")
    pcluster_create_timeout = _resolve("pcluster_create_timeout", int)
    scaledown_idletime = _resolve("scaledown_idletime", int)
    _validate_queue_sizes(initial_cpu_queue_size, max_cpu_queue_size, scaledown_idletime)
    _validate_queue_sizes(initial_gpu_queue_size, max_gpu_queue_size, scaledown_idletime)
    # A cluster with neither instance type set has no queue at all, and
    # PCluster only rejects the resulting config after the IAM role, S3
    # bucket, keypair and SSH secret exist -- see the function's own
    # docstring. Checked here, beside the other queue validation and well
    # before the first AWS call. Converted to sys.exit because the core
    # function raises: it is shared with the MCP tool layer, where an
    # uncaught SystemExit would kill a long-lived server process rather
    # than fail one call.
    try:
        _validate_at_least_one_queue(compute_instance_type, gpu_instance_type)
    except PClusterMakerError as _no_queue:
        sys.exit(f"ERROR: {_no_queue}")
    scheduler = _resolve("scheduler")
    turbot_account = _resolve("turbot_account")
    vpc_name = _resolve("vpc_name")
    headnode_subnet_id = _resolve("headnode_subnet_id")
    loginnode_subnet_id = _resolve("loginnode_subnet_id")
    compute_az_raw = _resolve("compute_az")
    compute_subnet_ids_override = _resolve("compute_subnet_ids")
    use_private_compute_subnet = _resolve("use_private_compute_subnet")
    gpu_az_raw = _resolve("gpu_az")
    gpu_subnet_ids_override = _resolve("gpu_subnet_ids")
    use_private_gpu_subnet = _resolve("use_private_gpu_subnet")

    # Compute falls back to the headnode AZ; GPU falls back to None, which
    # downstream reads as "no override" rather than "no AZs". See
    # _derive_az_list on why the fallback is a parameter.
    compute_az_list = _derive_az_list(compute_az_raw, fallback=[az])
    gpu_az_list = _derive_az_list(gpu_az_raw, fallback=None)

    # Print a header for cluster variable validation.

    if debug_mode:
        print_TextHeader(cluster_name, "Validating cluster parameters", 80)
        print("")
    else:
        print("")
        print("Performing parameter validation...")
        print("")

    # Validate cluster_name and cluster_owner format.
    # cluster_name: lowercase, digits, hyphens, max 27 chars (S3 bucket length limit).
    # cluster_owner: lowercase, digits, hyphens (embedded in Turbot profile and IAM names).
    _validate_cluster_name(cluster_name)
    _validate_cluster_owner(cluster_owner)

    # Get the version of Ansible being used to build the instance.

    try:
        _av = subprocess.run(["ansible", "--version"], capture_output=True, text=True)
        _lines = _av.stdout.splitlines() if _av.returncode == 0 else []
        ANSIBLE_VERSION = _lines[0].split()[-1].rstrip("]") if _lines and _lines[0].split() else ""
    except FileNotFoundError:
        ANSIBLE_VERSION = ""

    if not ANSIBLE_VERSION:
        error_msg = "Ansible is missing! Install it: pip install ansible"
        refer_to_docs_and_quit(error_msg)

    # Perform error checking on the selected AWS Region and Availability Zone.
    # Abort if a non-existent Region or Availability Zone was chosen.

    print(f"  Verifying region/AZ: {az}...")
    try:
        ec2client = boto3.client("ec2", region_name=az[:-1])
        _az_info = ec2client.describe_availability_zones(ZoneNames=[az])
    except (
        ValueError,
        EndpointConnectionError,
        NoCredentialsError,
        BotoCoreError,
        ClientError,
    ) as _e:
        sys.exit(f"ERROR: Could not verify availability zone '{az}': {_e}")

    if not _az_info.get("AvailabilityZones"):
        illegal_az_msg(az)
    region = _az_info["AvailabilityZones"][0]["RegionName"]

    # Activate Turbot cross-account profile now that region is confirmed and
    # before any VPC/STS/spot API calls so all downstream boto3 calls use the
    # correct cross-account credentials.
    if turbot_account != "disabled":
        turbot_profile = "turbot__" + turbot_account + "__" + cluster_owner
        os.environ["AWS_PROFILE"] = turbot_profile
        os.environ["AWS_DEFAULT_REGION"] = region
        boto3.setup_default_session(profile_name=turbot_profile)
        p_val("turbot_account", debug_mode)
        p_val("turbot_profile", debug_mode)

    params = MakeClusterParams(
        ansible_verbosity=ansible_verbosity,
        az=az,
        base_os=base_os,
        cluster_name=cluster_name,
        cluster_owner=cluster_owner,
        cluster_owner_department=cluster_owner_department,
        cluster_owner_email=cluster_owner_email,
        cluster_type=cluster_type,
        compute_instance_type=compute_instance_type,
        compute_root_volume_size=compute_root_volume_size,
        compute_root_volume_type=compute_root_volume_type,
        compute_root_volume_iops=compute_root_volume_iops,
        compute_root_volume_throughput=compute_root_volume_throughput,
        custom_ami=custom_ami,
        debug_mode=debug_mode,
        ebs_encryption=ebs_encryption,
        ebs_shared_dir=ebs_shared_dir,
        ebs_shared_volume_size=ebs_shared_volume_size,
        ebs_shared_volume_type=ebs_shared_volume_type,
        ebs_shared_volume_iops=ebs_shared_volume_iops,
        ebs_shared_volume_throughput=ebs_shared_volume_throughput,
        efs_encryption=efs_encryption,
        efs_performance_mode=efs_performance_mode,
        efs_throughput_mode=efs_throughput_mode,
        enable_efa=enable_efa,
        enable_efs=enable_efs,
        enable_external_nfs=enable_external_nfs,
        enable_loginnode=enable_loginnode,
        loginnode_instance_type=loginnode_instance_type,
        loginnode_count=loginnode_count,
        gpu_instance_type=gpu_instance_type,
        gpu_root_volume_size=gpu_root_volume_size,
        gpu_root_volume_type=gpu_root_volume_type,
        gpu_root_volume_iops=gpu_root_volume_iops,
        gpu_root_volume_throughput=gpu_root_volume_throughput,
        enable_fsx=enable_fsx,
        enable_fsx_hydration=enable_fsx_hydration,
        enable_hpc_benchmarks=enable_hpc_benchmarks,
        enable_monitoring=enable_monitoring,
        monitoring_version=monitoring_version,
        monitoring_version_checksum=monitoring_version_checksum,
        docker_compose_version=docker_compose_version,
        stage_docker_compose=stage_docker_compose,
        docker_compose_arch=docker_compose_arch,
        docker_compose_checksum=docker_compose_checksum,
        external_nfs_server=external_nfs_server,
        fsx_chunk_size=fsx_chunk_size,
        fsx_s3_export_bucket=fsx_s3_export_bucket,
        fsx_s3_export_path=fsx_s3_export_path,
        fsx_s3_import_bucket=fsx_s3_import_bucket,
        fsx_s3_import_path=fsx_s3_import_path,
        fsx_size=fsx_size,
        head_node_bootstrap_timeout=head_node_bootstrap_timeout,
        configured_head_node_bootstrap_timeout=_configured_bootstrap_timeout,
        hyperthreading=hyperthreading,
        initial_cpu_queue_size=initial_cpu_queue_size,
        initial_gpu_queue_size=initial_gpu_queue_size,
        maintain_cpu_initial_size=maintain_cpu_initial_size,
        maintain_gpu_initial_size=maintain_gpu_initial_size,
        headnode_instance_type=headnode_instance_type,
        headnode_root_volume_size=headnode_root_volume_size,
        headnode_root_volume_type=headnode_root_volume_type,
        headnode_root_volume_iops=headnode_root_volume_iops,
        headnode_root_volume_throughput=headnode_root_volume_throughput,
        max_cpu_queue_size=max_cpu_queue_size,
        max_gpu_queue_size=max_gpu_queue_size,
        placement_group=placement_group,
        pre_install_script=pre_install_script,
        post_install_script=post_install_script,
        prod_level=prod_level,
        project_id=project_id,
        pcluster_create_timeout=pcluster_create_timeout,
        scaledown_idletime=scaledown_idletime,
        scheduler=scheduler,
        turbot_account=turbot_account,
        vpc_name=vpc_name,
        headnode_subnet_id=headnode_subnet_id,
        loginnode_subnet_id=loginnode_subnet_id,
        compute_az_list=compute_az_list,
        compute_subnet_ids_override=compute_subnet_ids_override,
        use_private_compute_subnet=use_private_compute_subnet,
        gpu_az_list=gpu_az_list,
        gpu_subnet_ids_override=gpu_subnet_ids_override,
        use_private_gpu_subnet=use_private_gpu_subnet,
    )

    core_create_cluster(
        params=params,
        repo_root=_repo_root,
        region=region,
        cluster_build_command=cluster_build_command,
        ansible_version=ANSIBLE_VERSION,
    )


if __name__ == "__main__":
    main()
