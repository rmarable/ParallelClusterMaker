#!/usr/bin/env python
#
################################################################################
# Name:		make_pcluster.py
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	April 20, 2019
# Last Changed:	September 22, 2019
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
import contextlib
import json
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    EndpointConnectionError,
    NoCredentialsError,
)
from datetime import datetime as DateTime
from jinja2 import Environment, FileSystemLoader as _FSLoader, StrictUndefined

# Import the list of supported EC2 instances and some external functions.
sys.path.insert(0, _src_dir)
from pcluster_core import (
    _b,
    _validate_az_input,
    _validate_cluster_name,
    _validate_cluster_owner,
    _validate_cluster_lifetime,
    _validate_fsx_size,
    _validate_ebs_config,
    _validate_ebs_shared_dir,
    _validate_queue_sizes,
    _resolve_ec2_user,
    _load_or_create_serial,
    _normalize_fsx_buckets,
    _check_fsx_s3,
    _load_defaults_file,
    _resolve as _pcore_resolve,
    _resolve_bool as _pcore_resolve_bool,
    _render_policy,
    _setup_iam,
    _cleanup_iam_on_failure,
    _delete_managed_policies,
    _setup_fsx_hydration_iam,
    _validate_network,
    _get_efa_instance_types,
    _ssh_secret_name,
)
from pcluster_aux_data import base_os_efa
from pcluster_aux_data import base_os_instance_check
from pcluster_aux_data import ctrlC_Abort
from pcluster_aux_data import default_instance_types
from pcluster_aux_data import ec2_instances_efa
from pcluster_aux_data import ec2_instances_full_list
from pcluster_aux_data import illegal_az_msg
from pcluster_aux_data import p_fail
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
        help="Set the Ansible verbosity level (default = none)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--base_os",
        choices=["ubuntu2204", "ubuntu2404", "ubuntu2204arm", "ubuntu2404arm", "rhel8", "rhel8arm", "rhel9", "rhel9arm"],
        help="cluster base operating system (default = ubuntu2404)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--cluster_lifetime",
        help="cluster lifetime in days:hours:minutes (default = 7:0:0)",
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
        help="compute EC2 instance type (default = c8g.2xlarge)",
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
        "--external_nfs_server",
        help="external NFS server hostname (default = none)",
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
        help="S3 bucket to dehydrate Lustre to (default = UNDEFINED)",
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
        "--initial_queue_size",
        help="initial compute node count (default = 2)",
        required=False,
        type=int,
        default=None,
    )
    parser.add_argument(
        "--maintain_initial_size",
        choices=["true", "false"],
        help="keep initial nodes always running (default = false)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--headnode_instance_type",
        help="head node EC2 instance type (default = c8g.xlarge)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--headnode_root_volume_size",
        help="head node EBS root volume size in GB (default = 250)",
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
        "--max_queue_size",
        help="maximum compute node count (default = 10)",
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

    # Parse the command used to create this cluster stack.

    cluster_build_command = " ".join(sys.argv)

    # Parse CLI args, then overlay pcluster_defaults.yml if --use_defaults was
    # passed, then apply hardcoded fallbacks for anything still unset.
    # Precedence: CLI arg > pcluster_defaults.yml > hardcoded default.

    args = parser.parse_args()

    _HARDCODED_DEFAULTS = {
        "ansible_verbosity": "",
        "base_os": "ubuntu2404",
        "cluster_lifetime": "0:24:0",
        "cluster_owner_department": "hpc",
        "cluster_type": "spot",
        "compute_instance_type": "c8g.2xlarge",
        "compute_root_volume_size": 250,
        "compute_root_volume_type": "gp3",
        "compute_root_volume_iops": 3000,
        "compute_root_volume_throughput": 125,
        "custom_ami": "NONE",
        "debug_mode": "false",
        "ebs_encryption": "false",
        "ebs_shared_dir": "/shared",
        "ebs_shared_volume_size": 250,
        "ebs_shared_volume_type": "gp3",
        "ebs_shared_volume_iops": 3000,
        "ebs_shared_volume_throughput": 125,
        "efs_encryption": "false",
        "efs_performance_mode": "generalPurpose",
        "efs_throughput_mode": "bursting",
        "enable_efa": "false",
        "enable_efs": "false",
        "enable_external_nfs": "false",
        "enable_fsx": "false",
        "enable_fsx_hydration": "false",
        "enable_hpc_benchmarks": "false",
        "enable_monitoring": "false",
        "monitoring_version": "v2.6",
        "monitoring_version_checksum": "sha256:REPLACE_WITH_ACTUAL_SHA256",
        "external_nfs_server": "",
        "fsx_chunk_size": 1024,
        "fsx_s3_export_bucket": "UNDEFINED",
        "fsx_s3_export_path": "export",
        "fsx_s3_import_bucket": "UNDEFINED",
        "fsx_s3_import_path": "import",
        "fsx_size": 1200,
        "hyperthreading": "true",
        "initial_queue_size": 2,
        "maintain_initial_size": "false",
        "headnode_instance_type": "c8g.xlarge",
        "headnode_root_volume_size": 250,
        "headnode_root_volume_type": "gp3",
        "headnode_root_volume_iops": 3000,
        "headnode_root_volume_throughput": 125,
        "max_queue_size": 10,
        "placement_group": "NONE",
        "pre_install_script": "scripts/pre-deployment.sh",
        "post_install_script": "scripts/post-deployment.sh",
        "prod_level": "dev",
        "project_id": "UNDEFINED",
        "pcluster_create_timeout": 60,
        "scaledown_idletime": 5,
        "scheduler": "slurm",
        "turbot_account": "disabled",
        "vpc_name": "vpc_default",
        "headnode_subnet_id": "",
        "compute_az": "",
        "compute_subnet_ids": "",
        "use_private_compute_subnet": "false",
    }

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
    cluster_lifetime = str(_resolve("cluster_lifetime"))
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
    external_nfs_server = _resolve("external_nfs_server")
    fsx_chunk_size = _resolve("fsx_chunk_size", int)
    fsx_s3_export_bucket = _resolve("fsx_s3_export_bucket")
    fsx_s3_export_path = _resolve("fsx_s3_export_path")
    fsx_s3_import_bucket = _resolve("fsx_s3_import_bucket")
    fsx_s3_import_path = _resolve("fsx_s3_import_path")
    fsx_size = _resolve("fsx_size", int)
    hyperthreading = _resolve_bool("hyperthreading")
    initial_queue_size = _resolve("initial_queue_size", int)
    maintain_initial_size = _resolve_bool("maintain_initial_size")
    headnode_instance_type = _resolve("headnode_instance_type")
    headnode_root_volume_size = _resolve("headnode_root_volume_size", int)
    headnode_root_volume_type = _resolve("headnode_root_volume_type")
    headnode_root_volume_iops = _resolve("headnode_root_volume_iops", int)
    headnode_root_volume_throughput = _resolve("headnode_root_volume_throughput", int)
    max_queue_size = _resolve("max_queue_size", int)
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
    region = az[:-1]  # bootstrap only; overwritten below from API
    pcluster_create_timeout = _resolve("pcluster_create_timeout", int)
    scaledown_idletime = _resolve("scaledown_idletime", int)
    _validate_queue_sizes(initial_queue_size, max_queue_size, scaledown_idletime)
    scheduler = _resolve("scheduler")
    turbot_account = _resolve("turbot_account")
    vpc_name = _resolve("vpc_name")
    headnode_subnet_id = _resolve("headnode_subnet_id")
    compute_az_raw = _resolve("compute_az")
    compute_subnet_ids_override = _resolve("compute_subnet_ids")
    use_private_compute_subnet = _resolve("use_private_compute_subnet")

    # Build the compute AZ list: default to the headnode AZ when not specified.
    compute_az_list = (
        [a.strip() for a in compute_az_raw.split(",") if a.strip()]
        if compute_az_raw
        else [az]
    )

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
        ec2client = boto3.client("ec2", region_name=region)
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
        # Reinitialise ec2client with the Turbot profile for VPC/subnet discovery
        # and spot price queries; the bootstrap client used for AZ verification
        # above operated on the operator's base credentials before profile switch.
        ec2client = boto3.client("ec2", region_name=region)

    vars_file_path = os.path.join(_src_dir, "vars_files", cluster_name + ".yml")
    os.makedirs(os.path.join(_src_dir, "vars_files"), exist_ok=True)

    # Check for an existing vars_file before making any API calls.
    if os.path.isfile(vars_file_path):
        print("\n*** WARNING ***")
        print('An existing vars_file for cluster "' + cluster_name + '" was found!')
        print("")
        print("Please delete this cluster properly and retry the build:")
        print(
            "$ ./kill_pcluster.py -N "
            + cluster_name
            + " -O "
            + cluster_owner
            + " -A "
            + az
        )
        print("$ " + cluster_build_command)
        print("")
        print("Aborting...")
        sys.exit(1)
    else:
        p_val("vars_file_path", debug_mode)

    # Run three independent API calls in parallel:
    #   - VPC/subnet discovery
    #   - AWS account ID from STS
    #   - Check whether this cluster already exists
    print("  Resolving network, account ID, and cluster state...")
    stsclient = boto3.client("sts", region_name=region)

    def _get_account_id():
        return stsclient.get_caller_identity()["Account"]

    def _check_cluster_exists():
        return subprocess.run(
            [
                "pcluster",
                "describe-cluster",
                "--cluster-name",
                cluster_name,
                "--region",
                region,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    with ThreadPoolExecutor(max_workers=3) as _pool:
        _fut_network = _pool.submit(
            _validate_network,
            ec2client,
            az,
            vpc_name,
            headnode_subnet_id,
            compute_az_list,
            compute_subnet_ids_override,
            use_private_compute_subnet,
        )
        _fut_account = _pool.submit(_get_account_id)
        _fut_describe = _pool.submit(_check_cluster_exists)

    try:
        vpc_id, subnet_id, compute_subnet_ids, vpc_cidr = _fut_network.result()
    except Exception as _e:
        sys.exit(f"ERROR: Network/VPC discovery failed: {_e}")
    try:
        aws_account_id = _fut_account.result()
    except Exception as _e:
        sys.exit(f"ERROR: Could not retrieve AWS account ID: {_e}")
    try:
        _describe = _fut_describe.result()
    except FileNotFoundError:
        sys.exit(
            "ERROR: 'pcluster' command not found. Install aws-parallelcluster before running this script."
        )
    except Exception as _e:
        sys.exit(f"ERROR: Could not check cluster existence: {_e}")

    if _describe.returncode == 0:
        error_msg = (
            'pcluster stack "'
            + cluster_name
            + '" is already deployed in '
            + region
            + "!"
        )
        refer_to_docs_and_quit(error_msg)
    else:
        if debug_mode:
            p_val("cluster_name", debug_mode)

    # Set the state directory for this cluster.
    # Anchored to the repo root so the script works from any CWD.

    cluster_data_dir = (
        os.path.join(_repo_root, "active_clusters", cluster_name) + os.sep
    )

    # Check for an existing state directory for this cluster.

    os.makedirs(cluster_data_dir, exist_ok=True)
    p_val("cluster_data_dir", debug_mode)

    # Generate or resume a cluster serial number.
    # If a serial file already exists this is a retry of an interrupted run.
    # We MUST reuse the same serial so that IAM roles and S3 resources created
    # during the first attempt are found (and not duplicated) on retry.
    # Generating a fresh serial on retry would orphan the first attempt's IAM
    # role and policy permanently — kill_pcluster reads the serial file to know
    # what to delete.

    _now = DateTime.now()
    DEPLOYMENT_DATE = _now.strftime("%B ") + str(_now.day) + _now.strftime(", %Y")
    DEPLOYMENT_DATE_TAG = str(_now.day) + _now.strftime("-%B-%Y")
    Deployed_On = DEPLOYMENT_DATE

    _validate_cluster_lifetime(cluster_lifetime)

    (
        cluster_serial_number_file,
        cluster_serial_number,
        cluster_serial_datestamp,
        _serial_was_created,
    ) = _load_or_create_serial(cluster_data_dir, cluster_name)

    p_val("cluster_serial_number", debug_mode)
    p_val("cluster_serial_number_file", debug_mode)

    # Validate the prod_level and cluster_owner_department.  These values are
    # limited by the command line argument parser so there is no need for futher
    # error checking.

    p_val("cluster_owner_department", debug_mode)

    # Perform a minimal check to ensure cluster_owner_email resembles a valid
    # email address.

    if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", cluster_owner_email):
        p_val("cluster_owner_email", debug_mode)
    else:
        error_msg = (
            '"'
            + cluster_owner_email
            + '"'
            + """ does not appear to be a valid email address!
    Reference: https://en.wikipedia.org/wiki/Email_address"""
        )
        refer_to_docs_and_quit(error_msg)

    # Validate the project_id if it was provided.

    if project_id != "UNDEFINED":
        p_val("project_id", debug_mode)

    # Configure the ec2_user account and home directory path to match base_os.

    ec2_user, ec2_user_home = _resolve_ec2_user(base_os)
    p_val("ec2_user_home", debug_mode)

    # Validate the production level.  Since these options are controlled by the
    # command line argument parser, no further error checking is needed.

    p_val("prod_level", debug_mode)

    # Validate the scheduler and all other associated parameters.  These values
    # are limited by the command line argument parser so there is no need for
    # additional error checking.

    p_val("scheduler", debug_mode)

    _default_head_node_instance_type = default_instance_types[
        "default_head_node_instance_type"
    ]
    _default_compute_instance_type = default_instance_types[
        "default_compute_instance_type"
    ]

    if headnode_instance_type == "default":
        headnode_instance_type = _default_head_node_instance_type
    if compute_instance_type == "default":
        compute_instance_type = _default_compute_instance_type

    # If Elastic Fabric Adapter (EFA) support is enabled, perform checks to ensure
    # the selected instance type and operating system are supported and a dynamic
    # EC2 placement group is defined in the ParallelCluster configuration.

    if enable_efa:
        _efa_types = _get_efa_instance_types(ec2client, ec2_instances_efa)
        if compute_instance_type not in _efa_types:
            print(
                f"*** WARNING ***\n"
                f"  {compute_instance_type} is not in the EFA-capable instance list."
            )
            print("  Verify EFA support at: https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/efa.html")
        if base_os not in base_os_efa:
            error_msg = base_os + " does not support Elastic Fabric Adapter (EFA)!"
            refer_to_docs_and_quit(error_msg)
        if placement_group == "NONE":
            placement_group = "DYNAMIC"
        p_val("placement_group", debug_mode)

    # Perform error checking on headnode_instance_type and compute_instance_type to
    # ensure the selections are valid EC2 instance types and are supported by the
    # selected operating system.

    if headnode_instance_type not in ec2_instances_full_list:
        p_fail(
            headnode_instance_type, "headnode_instance_type", ec2_instances_full_list
        )
    base_os_instance_check(base_os, headnode_instance_type, debug_mode)
    p_val("headnode_instance_type", debug_mode)
    p_val("headnode_root_volume_size", debug_mode)

    if compute_instance_type not in ec2_instances_full_list:
        p_fail(compute_instance_type, "compute_instance_type", ec2_instances_full_list)
    base_os_instance_check(base_os, compute_instance_type, debug_mode)
    p_val("compute_instance_type", debug_mode)
    p_val("compute_root_volume_size", debug_mode)

    # Validate the selected instances and operating system.

    p_val("base_os", debug_mode)
    print("Selected base operating system: " + base_os)
    print("Selected head node instance type: " + headnode_instance_type)
    print("Selected compute instance type: " + compute_instance_type)

    s3_bucketname = "parallelclustermaker-" + cluster_serial_number
    _resuming = not _serial_was_created

    # Lustre options should not be used without setting enable_fsx=true.
    # S3-to-Lustre and Lustre-to-S3 dehydration options should not be used
    # without setting enable_fsx_hydration=true.

    if enable_fsx:
        if (not enable_fsx_hydration) and (
            (fsx_s3_import_bucket != "UNDEFINED")
            or (fsx_s3_export_bucket != "UNDEFINED")
        ):
            error_msg = (
                'All Lustre-S3 interactions require: "enable_fsx_hydration=true"'
            )
            refer_to_docs_and_quit(error_msg)
    if not enable_fsx:
        if enable_fsx_hydration:
            error_msg = 'All Lustre-to-S3 interactions require: "enable_fsx=true"'
            refer_to_docs_and_quit(error_msg)
        if (fsx_s3_import_bucket != "UNDEFINED") or (
            fsx_s3_export_bucket != "UNDEFINED"
        ):
            error_msg = 'All Lustre-to-S3 interactions require: "enable_fsx=true"'
            refer_to_docs_and_quit(error_msg)
    p_val("enable_fsx", debug_mode)

    # Check to ensure the Lustre volume size is divisible by 1200.

    _validate_fsx_size(fsx_size, enable_fsx)
    if enable_fsx:
        p_val("fsx_size", debug_mode)

    # Perform error checking and validation on fsx_chunk_size, which should range
    # between 1,024 MB (1 GB) and 512,000 MB (500 GB).
    # Furthermore, S3-to-Lustre hydration and Lustre-to-S3 options should *never*
    # be used without setting enable_fsx_hydration=true.

    if enable_fsx and enable_fsx_hydration:
        if fsx_chunk_size > 512000 or fsx_chunk_size < 1024:
            error_msg = "fsx_chunk_size must be between 1,024 MB (1 GB) and 512,000 MB (500 GB)!"
            refer_to_docs_and_quit(error_msg)
        p_val("fsx_chunk_size", debug_mode)

    # Normalise FSx S3 bucket/path configuration (no AWS calls — pure variable logic).
    # Actual bucket/path existence checks happen after the Turbot profile switch below,
    # where s3_client is reinitialized with the correct credentials.

    if enable_fsx and enable_fsx_hydration:
        fsx_s3_export_bucket, fsx_s3_export_path = _normalize_fsx_buckets(
            fsx_s3_import_bucket,
            fsx_s3_export_bucket,
            fsx_s3_import_path,
            fsx_s3_export_path,
        )

    # Check to ensure external NFS support has been properly enabled.

    if enable_external_nfs and (external_nfs_server == ""):
        error_msg = 'Missing: valid setting for "--external_nfs_server"'
        refer_to_docs_and_quit(error_msg)
    if enable_external_nfs and not re.fullmatch(
        r"^[a-zA-Z0-9.\-]+$", external_nfs_server
    ):
        sys.exit(
            f"ERROR: external_nfs_server contains invalid characters: {external_nfs_server!r}\n"
            f"  Only letters, digits, dots, and hyphens are permitted."
        )
    else:
        p_val("enable_external_nfs", debug_mode)
        p_val("external_nfs_server", debug_mode)

    # Todo - if enable_external_nfs=true, check to ensure external_nfs_server
    # actually exists through a ping test or running showmount/rpcinfo to verify
    # NFS shares are being exported.  Something like:
    #
    # $ showmount -e external_nfs_server (fail if empty)
    # $ rpcinfo -t remote_nfs_server nfs 4 (fail if empty)
    # $ ping -c 4 remote_nfs_server (fail if packet_loss > 0)

    # Set external_nfs_server to a dummy value if external NFS support has not
    # been enabled.

    if not enable_external_nfs:
        external_nfs_server = "FEATURE_DISABLED"

    # Validate the EBS configuration based on the shared volume type.

    p_val("ebs_shared_volume_type", debug_mode)
    p_val("ebs_shared_volume_size", debug_mode)
    if ebs_shared_volume_type in ("gp3", "io1", "io2"):
        p_val("ebs_shared_volume_iops", debug_mode)
    if ebs_shared_volume_type == "gp3":
        p_val("ebs_shared_volume_throughput", debug_mode)
    if ebs_encryption:
        p_val("ebs_encryption", debug_mode)

    _validate_ebs_config(
        headnode_root_volume_size,
        compute_root_volume_size,
        ebs_shared_volume_size,
        ebs_shared_volume_type,
        ebs_shared_volume_iops,
        ebs_shared_volume_throughput,
    )
    _validate_ebs_shared_dir(ebs_shared_dir)
    p_val("ebs_shared_dir", debug_mode)

    # Validate EFS based on the selected performance mode.

    if enable_efs:
        p_val("efs_performance_mode", debug_mode)
        p_val("efs_throughput_mode", debug_mode)
        p_val("efs_encryption", debug_mode)

    # If a custom_ami was provided, perform error checking on its existence.

    if custom_ami != "NONE":
        try:
            ec2client.describe_images(ImageIds=[custom_ami])
        except ClientError:
            error_msg = '"' + custom_ami + '" does not appear to be a valid AMI!'
            refer_to_docs_and_quit(error_msg)
        else:
            p_val("custom_ami", debug_mode)

    # Compute EC2 spot prices from: https://aws.amazon.com/ec2/spot/pricing/
    # Pad the spot_price with a buffer to protect against spot price market
    # fluctuations that might cause an instance to be reclaimed in the middle
    # of a job.
    #
    # If the user selects ondemand instances, print a friendly reminder to the
    # console that spot is a more economical choice for HPC clusters.

    # 50% above current spot price — wide enough to absorb typical market swings
    # without paying close to on-demand.
    spot_buffer = 0.5
    raw_spot_price = 0.0
    spot_price = "undefined"
    if cluster_type == "ondemand":
        p_val("cluster_type", debug_mode)
        print("  On-Demand instances were selected")
        print("  *Hint* ==> spot instances are more cost-effective for HPC!!")
        print("")
    elif cluster_type == "spot":
        p_val("cluster_type", debug_mode)
        print(f"  Fetching spot price for {compute_instance_type} in {az}...")
        prices = ec2client.describe_spot_price_history(
            InstanceTypes=[compute_instance_type],
            MaxResults=1,
            ProductDescriptions=["Linux/UNIX (Amazon VPC)"],
            AvailabilityZone=az,
        )
        try:
            raw_spot_price = float(prices["SpotPriceHistory"][0]["SpotPrice"])
        except IndexError:
            error_msg = (
                f"The selected compute_instance_type ({compute_instance_type}) is "
                f"unavailable on the Spot market in {az}."
            )
            refer_to_docs_and_quit(error_msg)
        spot_price = round(raw_spot_price + (spot_buffer * raw_spot_price), 8)
        print(f"  => ${raw_spot_price:.6f}/hr (bid: ${spot_price:.6f}/hr)")
        p_val("spot_price", debug_mode)
    else:
        p_fail(cluster_type, "cluster_type", ["ondemand", "spot"])

    # Turbot profile was activated earlier (after AZ verification) so all boto3
    # clients from this point already use the correct cross-account credentials.

    # Instantiate S3 resource/client — Turbot profile already active if applicable.
    s3 = boto3.resource("s3")
    s3_client = boto3.client("s3")

    # Check s3_bucketname using the correct (post-Turbot) credentials.
    try:
        s3_client.head_bucket(Bucket=s3_bucketname)
        if _resuming:
            print(f"  Found existing S3 bucket from interrupted run: {s3_bucketname}")
            p_val("s3_bucketname", debug_mode)
        else:
            error_msg = "Found an existing S3 bucket associated with this cluster!"
            refer_to_docs_and_quit(error_msg)
    except ClientError as _e:
        if _e.response["Error"]["Code"] in ("404", "NoSuchBucket"):
            p_val("s3_bucketname", debug_mode)
        else:
            raise

    # Validate FSx S3 import/export bucket and path existence with the correct
    # (post-Turbot) credentials.
    if enable_fsx and enable_fsx_hydration:
        _check_fsx_s3(s3_client, fsx_s3_import_bucket, fsx_s3_import_path, "import")
        _check_fsx_s3(s3_client, fsx_s3_export_bucket, fsx_s3_export_path, "export")
        p_val("fsx_s3_import_bucket", debug_mode)
        p_val("fsx_s3_import_path", debug_mode)
        p_val("fsx_s3_export_bucket", debug_mode)
        p_val("fsx_s3_export_path", debug_mode)
        print(
            "Setting S3-Lustre import path to: s3://"
            + fsx_s3_import_bucket
            + "/"
            + fsx_s3_import_path
        )
        print(
            "Setting Lustre-S3 export path to: s3://"
            + fsx_s3_export_bucket
            + "/"
            + fsx_s3_export_path
        )

    print("  Setting up IAM roles and policies...")

    iam = boto3.client("iam")
    ec2_iam_policy = "pclustermaker-policy-" + cluster_serial_number
    ec2_iam_role = "pclustermaker-role-" + cluster_serial_number
    ec2_json_policy_src = os.path.join(
        _repo_root, "templates", "ParallelClusterInstancePolicy.json_src"
    )
    ec2_json_policy_template = os.path.join(
        cluster_data_dir, "ParallelClusterInstancePolicy.json"
    )

    try:
        _setup_iam(
            iam,
            ec2_iam_role,
            ec2_iam_policy,
            ec2_json_policy_template,
            aws_account_id,
            prod_level,
            cluster_serial_number,
            cluster_name,
            cluster_owner,
            cluster_serial_datestamp,
            ec2_json_policy_src,
            region=region,
            vpc_id=vpc_id,
            enable_monitoring=enable_monitoring,
        )
    except Exception as _iam_e:
        print(
            f"\n*** ERROR ***\n"
            f"  Exception during IAM role/policy setup: {_iam_e}"
        )
        print("Cleaning up any partially-created IAM resources:")
        _cleanup_iam_on_failure(
            iam, ec2_iam_role, ec2_iam_policy, aws_account_id,
            enable_monitoring=enable_monitoring,
        )
        sys.exit(1)

    try:
        if enable_fsx_hydration:
            fsx_hydration_iam_policy = (
                "pclustermaker-fsx-s3-policy-" + cluster_serial_number
            )
            fsx_hydration_json_policy_src = os.path.join(
                _repo_root, "templates", "LustreS3HydrationPolicy.json_src"
            )
            fsx_hydration_policy_template = os.path.join(
                cluster_data_dir, "LustreS3HydrationPolicy.json"
            )
            _setup_fsx_hydration_iam(
                iam,
                ec2_iam_role,
                fsx_hydration_iam_policy,
                fsx_hydration_json_policy_src,
                fsx_hydration_policy_template,
                fsx_s3_export_bucket,
                fsx_s3_import_bucket,
            )
        else:
            fsx_hydration_iam_policy = "UNDEFINED"
    except Exception as _iam_e:
        print(
            f"\n*** ERROR ***\n"
            f"  Exception during IAM/template setup after role creation: {_iam_e}"
        )
        print("Cleaning up IAM role to prevent orphan:")
        _delete_managed_policies(
            iam,
            ec2_iam_role,
            ec2_iam_policy,
            aws_account_id,
            enable_monitoring=enable_monitoring,
        )
        with contextlib.suppress(Exception):
            iam.delete_role(RoleName=ec2_iam_role)
            print(f"  Deleted IAM role: {ec2_iam_role}")
        with contextlib.suppress(FileNotFoundError):
            os.remove(cluster_serial_number_file)
        raise

    # Define the cluster_parameters dictionary.
    # This data is needed to build the vars_file.
    #
    cluster_parameters = {
        "local_workingdir": _repo_root,
        "cluster_rootdir": _repo_root,
        # Pre-computed path variables referenced by vars_file.j2 as Jinja2 expressions.
        # Plain Python Jinja2 does not evaluate YAML output lines as variables, so every
        # {{ cluster_data_dir }}, {{ stage_dir }}, etc. reference in the template must be
        # supplied explicitly in the render context.
        "cluster_data_dir": os.path.join(_repo_root, "active_clusters", cluster_name),
        "cluster_template_dir": os.path.join(_repo_root, "templates"),
        "stage_dir": os.path.join(
            tempfile.gettempdir(), "_ParallelClusterMaker_stage", cluster_serial_number
        ),
        "ec2_keypair": cluster_serial_number + "_" + region,
        "ssh_secret_name": _ssh_secret_name(cluster_name, cluster_serial_number),
        "ebs_root": ebs_shared_dir,
        "efs_root": "/efs",
        "fsx_root": "/fsx",
        "s3_script_path": "cluster_scripts/" + prod_level,
        "efs_pkg_dir": "/efs/pkg",
        "fsx_pkg_dir": "/fsx/pkg",
        "aws_account_id": aws_account_id,
        "az": az,
        "compute_az_list": compute_az_list,
        "compute_subnet_ids": compute_subnet_ids,
        "use_private_compute_subnet": use_private_compute_subnet,
        "base_os": base_os,
        "pcluster_os": base_os.removesuffix("arm"),
        "cluster_lifetime": cluster_lifetime,
        "cluster_name": cluster_name,
        "cluster_owner": cluster_owner,
        "cluster_owner_email": cluster_owner_email,
        "cluster_owner_department": cluster_owner_department,
        "cluster_serial_datestamp": cluster_serial_datestamp,
        "cluster_serial_number": cluster_serial_number,
        "cluster_serial_number_file": cluster_serial_number_file,
        "cluster_type": cluster_type,
        "compute_instance_type": compute_instance_type,
        "compute_root_volume_size": compute_root_volume_size,
        "compute_root_volume_type": compute_root_volume_type,
        "compute_root_volume_iops": compute_root_volume_iops,
        "compute_root_volume_throughput": compute_root_volume_throughput,
        "custom_ami": custom_ami,
        "debug_mode": _b(debug_mode),
        "ebs_encryption": _b(ebs_encryption),
        "ebs_shared_dir": ebs_shared_dir,
        "ebs_shared_volume_size": ebs_shared_volume_size,
        "ebs_shared_volume_type": ebs_shared_volume_type,
        "ebs_shared_volume_iops": ebs_shared_volume_iops,
        "ebs_shared_volume_throughput": ebs_shared_volume_throughput,
        "ec2_iam_policy": ec2_iam_policy,
        "ec2_iam_role": ec2_iam_role,
        "ec2_user": ec2_user,
        "ec2_user_home": ec2_user_home,
        "efs_encryption": efs_encryption,
        "efs_performance_mode": efs_performance_mode,
        "efs_throughput_mode": efs_throughput_mode,
        "enable_efa": _b(enable_efa),
        "enable_efs": _b(enable_efs),
        "enable_external_nfs": _b(enable_external_nfs),
        "enable_fsx": _b(enable_fsx),
        "enable_fsx_hydration": _b(enable_fsx_hydration),
        "enable_hpc_benchmarks": _b(enable_hpc_benchmarks),
        "enable_monitoring": _b(enable_monitoring),
        "monitoring_version": monitoring_version,
        "monitoring_version_checksum": monitoring_version_checksum,
        "monitoring_s3_dest": f"monitoring-post-install-wrapper.{cluster_name}.sh",
        "external_nfs_server": external_nfs_server,
        "fsx_chunk_size": fsx_chunk_size,
        "fsx_hydration_iam_policy": fsx_hydration_iam_policy,
        "fsx_s3_export_bucket": fsx_s3_export_bucket,
        "fsx_s3_export_path": fsx_s3_export_path,
        "fsx_s3_import_bucket": fsx_s3_import_bucket,
        "fsx_s3_import_path": fsx_s3_import_path,
        "fsx_size": fsx_size,
        "hyperthreading": _b(hyperthreading),
        "initial_queue_size": initial_queue_size,
        "maintain_initial_size": _b(maintain_initial_size),
        "max_queue_size": max_queue_size,
        "headnode_instance_type": headnode_instance_type,
        "headnode_root_volume_size": headnode_root_volume_size,
        "headnode_root_volume_type": headnode_root_volume_type,
        "headnode_root_volume_iops": headnode_root_volume_iops,
        "headnode_root_volume_throughput": headnode_root_volume_throughput,
        "placement_group": placement_group,
        "pre_install_script": pre_install_script,
        "post_install_script": post_install_script,
        "prod_level": prod_level,
        "project_id": project_id,
        "raw_spot_price": raw_spot_price if cluster_type == "spot" else "N/A",
        "region": region,
        "s3_bucketname": s3_bucketname,
        "pcluster_create_timeout": pcluster_create_timeout,
        "scaledown_idletime": scaledown_idletime,
        "scheduler": scheduler,
        "spot_price": spot_price,
        "subnet_id": subnet_id,
        "vpc_cidr": vpc_cidr,
        "vpc_id": vpc_id,
        "turbot_account": turbot_account,
        "vpc_name": vpc_name,
        "Deployed_On": Deployed_On,
        "ANSIBLE_VERSION": ANSIBLE_VERSION,
        "DEPLOYMENT_DATE": DEPLOYMENT_DATE_TAG,
    }

    # Print the current values of all validated cluster_parameters to the console
    # when debug mode is enabled.

    if debug_mode:
        print_TextHeader(cluster_name, "Displaying cluster parameter values", 80)
        print("ANSIBLE_VERSION = " + ANSIBLE_VERSION)
        print("DEPLOYMENT_DATE = " + DEPLOYMENT_DATE)
        print("aws_account_id = " + aws_account_id)
        print("base_os = " + base_os)
        print("cluster_lifetime (days:hours:minutes) = " + str(cluster_lifetime))
        print("cluster_name = " + cluster_name)
        print("cluster_owner = " + cluster_owner)
        print("cluster_owner_department = " + cluster_owner_department)
        print("cluster_owner_email = " + cluster_owner_email)
        print("cluster_serial_datestamp = " + cluster_serial_datestamp)
        print("cluster_serial_number = " + cluster_serial_number)
        print("cluster_serial_number_file = " + cluster_serial_number_file)
        print("cluster_type = " + cluster_type)
        if cluster_type == "spot":
            if "UNDEFINED" not in str(spot_price):
                print("    spot_price = $" + str(spot_price) + " per hour")
        print("compute_instance_type = " + compute_instance_type)
        print("compute_root_volume_size = " + str(compute_root_volume_size) + " GB")
        print("compute_root_volume_iops = " + str(compute_root_volume_iops))
        print(
            "compute_root_volume_throughput = "
            + str(compute_root_volume_throughput)
            + " MB/s"
        )
        if custom_ami != "NONE":
            print("custom_ami = " + custom_ami)
        print("ebs_shared_dir = " + ebs_shared_dir)
        print("ebs_shared_volume_size = " + str(ebs_shared_volume_size) + " GB")
        print("ebs_shared_volume_type = " + str(ebs_shared_volume_type))
        if ebs_shared_volume_type in ("gp3", "io1", "io2"):
            print("ebs_shared_volume_iops = " + str(ebs_shared_volume_iops))
        if ebs_shared_volume_type == "gp3":
            print(
                "ebs_shared_volume_throughput = "
                + str(ebs_shared_volume_throughput)
                + " MB/s"
            )
        print("ebs_encryption = " + str(ebs_encryption))
        print("ec2_user = " + ec2_user)
        print("ec2_user_home = " + ec2_user_home)
        print("ec2_iam_policy = " + ec2_iam_policy)
        print("ec2_iam_role = " + ec2_iam_role)
        if enable_efa:
            print(f"enable_efa = {enable_efa}")
        if enable_external_nfs:
            print(f"enable_external_nfs = {enable_external_nfs}")
            print("external_nfs_server = " + external_nfs_server)
        if enable_efs:
            print(f"enable_efs = {enable_efs}")
            print("efs_encryption = " + efs_encryption)
            print("efs_performance_mode = " + efs_performance_mode)
            print("efs_throughput_mode = " + efs_throughput_mode)
        if enable_fsx:
            print(f"enable_fsx = {enable_fsx}")
            print(f"enable_fsx_hydration = {enable_fsx_hydration}")
            print("fsx_size = " + str(fsx_size) + " GB")
            if enable_fsx_hydration:
                print("fsx_chunk_size = " + str(fsx_chunk_size))
                print("fsx_hydration_iam_policy = " + fsx_hydration_iam_policy)
                print("fsx_s3_export_bucket = " + fsx_s3_export_bucket)
                print("fsx_s3_export_path = " + fsx_s3_export_path)
                print("fsx_s3_import_bucket = " + fsx_s3_import_bucket)
                print("fsx_s3_import_path = " + fsx_s3_import_path)
        print(f"enable_hpc_benchmarks = {enable_hpc_benchmarks}")
        print(f"hyperthreading = {hyperthreading}")
        print("headnode_instance_type = " + headnode_instance_type)
        print("headnode_root_volume_size = " + str(headnode_root_volume_size) + " GB")
        print("headnode_root_volume_iops = " + str(headnode_root_volume_iops))
        print(
            "headnode_root_volume_throughput = "
            + str(headnode_root_volume_throughput)
            + " MB/s"
        )
        if placement_group != "NONE":
            print("placement_group = " + placement_group)
        print("prod_level = " + prod_level)
        if project_id != "UNDEFINED":
            print("project_id = " + project_id)
        print("region = " + region)
        print("s3_bucketname = s3://" + s3_bucketname)
        print("scheduler = " + scheduler)
        print("    initial_queue_size = " + str(initial_queue_size))
        print("    maintain_initial_size = " + str(maintain_initial_size))
        print("    max_queue_size = " + str(max_queue_size))
        print("scaledown_idletime = " + str(scaledown_idletime))
        print("subnet_id (headnode) = " + subnet_id)
        print("compute_az_list = " + ", ".join(compute_az_list))
        print("compute_subnet_ids = " + ", ".join(compute_subnet_ids))
        if use_private_compute_subnet:
            print("use_private_compute_subnet = true")
        print("vpc_id = " + vpc_id)
        print("vpc_name = " + vpc_name)

    # Generate the vars_file for this cluster.

    try:
        _jenv = Environment(
            loader=_FSLoader(os.path.join(_repo_root, "templates")),
            keep_trailing_newline=True,
            undefined=StrictUndefined,
        )
        _jtemplate = _jenv.get_template("vars_file.j2")
        print(f"  Writing vars file: {vars_file_path}")
        with open(
            os.open(vars_file_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w"
        ) as _vf:
            _vf.write(_jtemplate.render(**cluster_parameters))
    except Exception as _render_e:
        print(f"\n*** ERROR ***\n" f"  vars_file render failed: {_render_e}")
        print("Cleaning up IAM role to prevent orphan:")
        _fsx = (
            fsx_hydration_iam_policy
            if (enable_fsx_hydration and fsx_hydration_iam_policy != "UNDEFINED")
            else None
        )
        _delete_managed_policies(
            iam,
            ec2_iam_role,
            ec2_iam_policy,
            aws_account_id,
            fsx_policy=_fsx,
            enable_monitoring=enable_monitoring,
        )
        with contextlib.suppress(Exception):
            iam.delete_role(RoleName=ec2_iam_role)
            print(f"  Deleted IAM role: {ec2_iam_role}")
        with contextlib.suppress(FileNotFoundError):
            os.remove(cluster_serial_number_file)
        sys.exit(1)

    # Parse the Python3 interpreter path to ensure ParallelCluster stacks can be
    # created from either OSX or an EC2 jumphost.

    python3_path = sys.executable

    # Increase Ansible verbosity when debug_mode is enabled.

    if debug_mode:
        ansible_verbosity = "-vvv"

    # Generate the cluster build command string noting that external NFS servers
    # are not included unless that functionality is explicitly enabled by the
    # HPC operator.

    _extra_vars_dict = {
        "cluster_name": cluster_name,
        "cluster_serial_number": cluster_serial_number,
        "enable_hpc_benchmarks": _b(enable_hpc_benchmarks),
        "enable_monitoring": _b(enable_monitoring),
        "enable_efa": _b(enable_efa),
        "enable_efs": _b(enable_efs),
        "enable_fsx": _b(enable_fsx),
        "enable_fsx_hydration": _b(enable_fsx_hydration),
        "vpc_name": vpc_name,
        "debug_mode": _b(debug_mode),
        "ansible_python_interpreter": python3_path,
        "enable_external_nfs": _b(enable_external_nfs),
    }
    if enable_external_nfs:
        _extra_vars_dict["external_nfs_server"] = external_nfs_server

    _extra_vars_str = json.dumps(_extra_vars_dict)
    _create_playbook = os.path.join(_src_dir, "create_pcluster.yml")
    _ansible_cmd = [
        "ansible-playbook",
        "--extra-vars",
        _extra_vars_str,
        _create_playbook,
    ]
    if ansible_verbosity:
        _ansible_cmd.append(ansible_verbosity)

    # human-readable form for display only
    ansible_build_cmd_string = " ".join(_ansible_cmd)

    # Print the config file location and cluster build commands to the console.

    if ansible_verbosity:
        if debug_mode:
            print("debug_mode = enabled")
        print("")
        print('Setting Ansible verbosity to: "' + ansible_verbosity + '"')
    print("")
    print("View the configuration file for cluster " + cluster_name + ":")
    print("$ cat " + vars_file_path)
    print("")
    print("Ready to execute:")
    print("$ " + cluster_build_command)
    print("")
    print('Preparing to build cluster "' + cluster_name + '" using this command:')
    print("$ " + ansible_build_cmd_string)

    # Exit the script, cleanup any orphaned state files, and delete all IAM roles
    # and policies associated with this cluster if the operator types 'CTRL-C'
    # within 5 seconds after the abort header is displayed.
    # If debug_mode is invoked, set the delay interval to 15 seconds.

    line_length = 80
    if debug_mode:
        abort_timer = 15
    else:
        abort_timer = 5
    ctrlC_Abort(
        abort_timer,
        line_length,
        vars_file_path,
        cluster_serial_number_file,
        cluster_serial_number,
        _b(enable_fsx_hydration),
        enable_monitoring=enable_monitoring,
        aws_account_id=aws_account_id,
    )

    # Create the new cluster stack using the create_pcluster Ansible playbook.

    _build_result = subprocess.run(_ansible_cmd, cwd=_src_dir)

    if _build_result.returncode != 0:
        print("")
        print("*** ERROR ***")
        print(f"Ansible playbook exited with code {_build_result.returncode}.")
        print(f'Cluster "{cluster_name}" may not have been created successfully.')
        print("Cleaning up IAM resources to allow a clean retry:")
        _fsx = fsx_hydration_iam_policy if enable_fsx_hydration else None
        _delete_managed_policies(
            iam,
            ec2_iam_role,
            ec2_iam_policy,
            aws_account_id,
            suppress=False,
            fsx_policy=_fsx,
            enable_monitoring=enable_monitoring,
        )
        try:
            iam.delete_role(RoleName=ec2_iam_role)
            print(f"  Deleted IAM role: {ec2_iam_role}")
        except Exception as _e:
            print(f"  Warning: could not delete role {ec2_iam_role}: {_e}")
        print("Run kill_pcluster.py to tear down any partial stack before retrying:")
        print(f"  ./kill_pcluster.py -N {cluster_name} -O {cluster_owner} -A {az}")
        sys.exit(_build_result.returncode)

    # Append make_pcluster.py command line and the Ansible playbook command used
    # to build the stack to the cluster_serial_number file.

    with open(cluster_serial_number_file, "a") as _snf:
        print(ansible_build_cmd_string, file=_snf)
        print(cluster_build_command, file=_snf)

    cluster_serial_number_object = "cluster_serial_number/" + cluster_name + ".serial"
    try:
        with open(cluster_serial_number_file, "rb") as _snf:
            s3.Object(s3_bucketname, cluster_serial_number_object).put(Body=_snf)
    except Exception as _s3e:
        print(f"WARNING: could not upload serial number to S3: {_s3e}")

    # Fetch head node IP for the summary.
    _head_ip = ""
    try:
        import subprocess as _sp

        _desc = _sp.run(
            [
                ".venv/bin/pcluster",
                "describe-cluster",
                "--cluster-name",
                cluster_name,
                "--region",
                region,
            ],
            capture_output=True,
            text=True,
            cwd=_repo_root,
        )
        if _desc.returncode == 0:
            _info = json.loads(_desc.stdout)
            _head_ip = _info.get("headNode", {}).get("publicIpAddress") or _info.get(
                "headNode", {}
            ).get("privateIpAddress", "")
    except Exception:
        pass

    # Print a human-friendly cluster build summary.
    _enabled = [
        lbl
        for lbl, flag in [
            ("EFA", enable_efa),
            ("EFS", enable_efs),
            ("FSx/Lustre", enable_fsx),
            ("External NFS", enable_external_nfs),
            ("Monitoring", enable_monitoring),
        ]
        if str(flag).lower() == "true"
    ]
    print("")
    print("=" * 66)
    print("                   Cluster Build Summary")
    print("=" * 66)
    print(f"  Cluster Name:      {cluster_name}")
    print(f"  Cluster Type:      {cluster_type}")
    print(f"  Serial Datestamp:  {cluster_serial_datestamp}")
    print(f"  Availability Zone: {az}")
    print(f"  VPC:               {vpc_name}")
    print(f"  Head Node:         {headnode_instance_type}")
    print(f"  Compute:           {compute_instance_type}")
    print(f"  OS:                {base_os}")
    print(f"  Scheduler:         {scheduler}")
    print(f"  Lifetime:          {cluster_lifetime}")
    if _enabled:
        print(f"  Options:           {', '.join(_enabled)}")
    if _head_ip:
        print("")
        print("  Access the head node:")
        print(f"    ./access_cluster.py -N {cluster_name}")
        print(
            f"    ssh -i active_clusters/{cluster_name}/{cluster_serial_number}_{region}.pem {ec2_user}@{_head_ip}"
        )
        print("")
        print("  SSH key (Secrets Manager):")
        _secret = _ssh_secret_name(cluster_name, cluster_serial_number)
        print(f"    Secret: {_secret}")
        print(f"    Retrieve: active_clusters/{cluster_name}/retrieve_ssh_key.{cluster_name}.sh")
        print(f"    Rotate:   ./rotate_cluster_key.py -N {cluster_name}")
    if enable_monitoring and _head_ip:
        print("")
        print("  Grafana monitoring dashboard:")
        print(f"    Tunnel: active_clusters/{cluster_name}/grafana_tunnel.{cluster_name}.sh")
        print(f"    URL:    https://localhost:8443/grafana/  (after tunnel is open)")
        print(f"    Password: aws ssm get-parameter --region {region} \\")
        print(f"      --name /parallelcluster/{cluster_name}/grafana/admin-password \\")
        print("      --with-decryption --query Parameter.Value --output text")
    print("")
    print("  Delete this cluster:")
    print(f"    ./kill_pcluster.py -N {cluster_name} -O {cluster_owner} -A {az}")
    print("=" * 66)
    print("")
    print("Finished creating ParallelCluster stack " + cluster_name + "!")
    print("Exiting...")
    sys.exit(0)


if __name__ == "__main__":
    main()
