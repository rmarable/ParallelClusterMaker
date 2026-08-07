"""
Pure-Python utility functions extracted from make_pcluster.py, kill_pcluster.py,
and access_cluster.py.

All functions are importable without AWS credentials and without the
venv guard that the main scripts enforce at import time.
"""

import contextlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import yaml
from datetime import datetime as DateTime, timezone

try:
    from botocore.exceptions import ClientError as _ClientError
except ImportError:
    _ClientError = Exception


def _b(v):
    """Convert Python bool to lowercase string for Ansible/Jinja2 vars."""
    return "true" if v else "false"


def _validate_az_input(az):
    """Raise SystemExit if az is not a valid AZ string."""
    # Multi-segment prefixes are real regions: us-gov-west-1a, us-iso-east-1a.
    if not az or not re.match(r"^[a-z]{2}(-[a-z]+)+-\d+[a-z]$", az):
        sys.exit(
            f"ERROR: '{az}' is not a valid Availability Zone.\n"
            f"  Pass an AZ (e.g. us-east-1a), not a region (e.g. us-east-1)."
        )


def _validate_cluster_name(name):
    """Raise SystemExit if name violates the 27-char lowercase-hyphen rule.

    Must start with a letter (PCluster v3 API rejects digit-first names).
    Disallows trailing or consecutive hyphens to prevent invalid S3 bucket names.
    """
    if not re.match(r"^[a-z]([a-z0-9\-]{0,25}[a-z0-9])?$", name) or "--" in name:
        sys.exit(
            "cluster_name must start with a lowercase letter, contain only lowercase "
            "letters, digits, and hyphens, end with a letter or digit, contain no "
            "consecutive hyphens, and be at most 27 characters."
        )


def _validate_cluster_owner(owner):
    """Raise SystemExit if owner contains characters that are unsafe in derived names.

    The owner is embedded in the Turbot profile string and IAM policy names, so
    it must be lowercase alphanumeric plus hyphens only, no trailing or consecutive hyphens.
    """
    if (
        not re.match(r"^[a-z0-9][a-z0-9\-]{0,62}$", owner)
        or owner.endswith("-")
        or "--" in owner
    ):
        sys.exit(
            "cluster_owner must contain only lowercase letters, digits, and hyphens, "
            "start with a letter or digit, and contain no trailing or consecutive hyphens."
        )


# Login names are PCluster's own (OS_MAPPING in pcluster/constants.py). Exact keys
# rather than a `"rhel" in base_os` substring test: this function is the only
# rejection on the defaults-file path, where argparse choices are bypassed, and a
# substring test accepts rhel8 and rhel10 -- neither of which any template branch,
# arch table, or playbook gate knows about.
_EC2_USERS = {
    "ubuntu2204": "ubuntu",
    "ubuntu2404": "ubuntu",
    "ubuntu2204arm": "ubuntu",
    "ubuntu2404arm": "ubuntu",
    "rhel9": "ec2-user",
    "rhel9arm": "ec2-user",
    "alinux2023": "ec2-user",
    "alinux2023arm": "ec2-user",
}


def _resolve_ec2_user(base_os):
    """Return (ec2_user, ec2_user_home) for the given base_os string."""
    ec2_user = _EC2_USERS.get(base_os)
    if ec2_user is None:
        sys.exit(
            f"ERROR: '{base_os}' is not a supported base OS. "
            f"Choose from: {', '.join(_EC2_USERS)}"
        )
    return ec2_user, "/home/" + ec2_user


def _load_or_create_serial(cluster_data_dir, cluster_name):
    """Return (serial_file_path, serial_number, serial_datestamp, was_created).

    Reads an existing serial file on retry/resume; creates a new one on
    first run.  Serial file is written with mode 0o600.
    was_created is True on first create, False on resume.
    """
    serial_file = os.path.join(cluster_data_dir, cluster_name + ".serial")
    try:
        fd = os.open(serial_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        with open(serial_file) as fh:
            serial_number = fh.readline().rstrip("\n")
        if not serial_number:
            sys.exit(
                f"ERROR: Serial file {serial_file} is empty or corrupted. Delete it and re-run to start fresh."
            )
        datestamp = serial_number.split("-")[-1]
        print(f"  Resuming interrupted run — reusing serial: {serial_number}")
        return serial_file, serial_number, datestamp, False
    datestamp = DateTime.now(timezone.utc).strftime("%S%M%H%d%m%Y")
    serial_number = cluster_name + "-" + datestamp
    with open(fd, "w") as fh:
        print(serial_number, file=fh)
    return serial_file, serial_number, datestamp, True


def _normalize_fsx_buckets(import_bucket, export_bucket, import_path, export_path):
    """Normalize FSx S3 bucket/path, rejecting anything but one bucket for both.

    FSx requires both to name the same bucket: "The Amazon S3 export bucket must be
    the same as the import bucket specified by ImportPath" (CreateFileSystem
    LustreConfiguration, botocore's own FSx model). Only the prefixes may differ, so
    a mismatch is a hard error here rather than a CloudFormation failure twenty
    minutes into the build. Callers gate on enable_fsx_hydration, so a missing import
    bucket is an error too: the config template would otherwise render a literal
    "s3://UNDEFINED/...". Returns (export_bucket, export_path). No AWS calls.
    """
    if import_bucket == "UNDEFINED":
        sys.exit(
            "ERROR: Lustre hydration: fsx_s3_import_bucket is UNDEFINED.\n"
            "  enable_fsx_hydration=true requires an S3 bucket to hydrate from. "
            "Set fsx_s3_import_bucket, or set enable_fsx_hydration=false for an "
            "empty Lustre filesystem."
        )
    if export_bucket == "UNDEFINED":
        print("*** WARNING ***")
        print(
            "fsx_s3_import bucket is defined but fsx_s3_export_bucket is unspecified!"
        )
        print("Lustre will hydrate *and* dehydrate from the S3 import bucket path.")
        print("")
        return import_bucket, import_path
    if import_bucket != export_bucket:
        sys.exit(
            "ERROR: Lustre hydration: fsx_s3_export_bucket "
            f"({export_bucket}) must name the same bucket as "
            f"fsx_s3_import_bucket ({import_bucket}) — FSx for Lustre requires "
            "the export bucket to equal the import bucket. Use distinct "
            "fsx_s3_import_path and fsx_s3_export_path values to separate them."
        )
    if import_path == export_path:
        print("*** WARNING ***")
        print("fsx_s3_import_path and fsx_s3_export_path are set to the same value!")
        print(
            "Lustre will dehydrate over the hydration source — exported files "
            "overwrite the input data."
        )
        print("")
    return export_bucket, export_path


def _check_fsx_s3(s3_client, bucket, path, label, *, require_objects=True):
    """Validate that an FSx S3 bucket and path exist. Raises SystemExit on failure."""
    if not bucket or bucket == "UNDEFINED":
        return
    try:
        s3_client.head_bucket(Bucket=bucket)
    except _ClientError as _e:
        code = (
            _e.response.get("Error", {}).get("Code", "")
            if hasattr(_e, "response") and _e.response
            else ""
        )
        if code == "403":
            sys.exit(
                f"ERROR: Lustre hydration: {label} bucket s3://{bucket} exists but access is denied — check bucket policy and IAM role."
            )
        sys.exit(f"ERROR: Lustre hydration: {label} bucket s3://{bucket} not found!")
    # An export prefix is a destination, not a source: FSx's own default is
    # s3://import-bucket/FSxLustre<creation-timestamp>, which cannot exist before
    # the filesystem does, and a prefix the operator names for a first
    # dehydration is empty by definition. Only the import path holds the data
    # Lustre reads, so only it is required to be non-empty. The head_bucket call
    # above still runs on both -- a typo'd export bucket is a real error, and
    # AWS requires it to equal the import bucket anyway (ExportPath, FSx
    # CreateFileSystemLustreConfiguration).
    if not require_objects:
        return
    try:
        result = s3_client.list_objects_v2(Bucket=bucket, Prefix=path)
    except _ClientError as _e:
        sys.exit(f"ERROR: Lustre hydration: cannot list s3://{bucket}/{path}: {_e}")
    if result.get("KeyCount", 0) == 0:
        sys.exit(f"ERROR: Please ensure s3://{bucket}/{path} exists!")


def _check_external_nfs_reachable(server, *, port_timeout=5, showmount_timeout=10):
    """Best-effort pre-flight check for an external NFS server.

    Two independent, non-authoritative signals are gathered; only one
    result is a hard failure. This runs from the operator's own machine,
    which is not guaranteed to share the target VPC's network path to the
    filer (VPC peering / Direct Connect / on-prem-only routing), so a false
    failure here would block a config that works once the head node exists.
    """
    try:
        with socket.create_connection((server, 2049), timeout=port_timeout):
            pass
    except OSError as e:
        print("*** WARNING ***")
        print(f"{server} did not answer on port 2049 (NFS) within {port_timeout}s: {e}")
        print("  This may be a real problem, or this filer may only be reachable")
        print("  from inside the cluster's VPC, not from where make_pcluster.py runs.")
        print("")
        return

    try:
        result = subprocess.run(
            ["showmount", "-e", server],
            capture_output=True,
            text=True,
            timeout=showmount_timeout,
        )
    except FileNotFoundError:
        print("*** WARNING ***")
        print(f"'showmount' is not installed -- cannot verify {server} exports NFS shares.")
        print("  Install nfs-common (Debian/Ubuntu) or nfs-utils (RHEL/Amazon Linux).")
        print("")
        return
    except subprocess.TimeoutExpired:
        print("*** WARNING ***")
        print(f"showmount -e {server} timed out after {showmount_timeout}s.")
        print("")
        return

    if result.returncode != 0:
        print("*** WARNING ***")
        print(f"showmount -e {server} failed (rc={result.returncode}): {result.stderr.strip()}")
        print("  This may be an NFSv4-only server with no mountd -- cannot verify exports.")
        print("")
        return

    export_lines = [line for line in result.stdout.splitlines()[1:] if line.strip()]
    if not export_lines:
        sys.exit(
            f"ERROR: {server} answered showmount but exports nothing -- "
            "check the NFS server's /etc/exports (or equivalent) before building."
        )


# Slurm node states that mean a node is usable. Everything else -- down, drain,
# fail, unk, inval, and their combinations -- is a node that will not run work.
# Lives here rather than in either entry point because check_pcluster.py and
# diagnose_pcluster.py both classify sinfo output and must not drift apart.
# Base state names only. A flagged spelling such as `idle~` must not be listed
# here: the entry would satisfy any test written against that state while
# _SINFO_STATE_FLAGS did nothing, which is what let an empty flag set pass the
# whole suite.
_SINFO_OK_STATES = frozenset(
    {"idle", "mix", "mixed", "alloc", "allocated", "comp", "completing",
     "future", "resv", "reserved", "power_up", "powering_up", "power_down",
     "powering_down", "powered_down", "planned"}
)

# Slurm appends flag characters to a state name (`idle*` unresponsive,
# `idle~` powered down, `drain$`, ...). The base state is what classifies the
# node, so the flags are stripped before comparison.
_SINFO_STATE_FLAGS = "*+~#!%$@^-"


def _sinfo_state_is_ok(raw_state):
    """True if a raw sinfo state field names a usable node."""
    return raw_state.strip().rstrip(_SINFO_STATE_FLAGS).lower() in _SINFO_OK_STATES


def _classify_sinfo_nodes(output):
    """Return (usable, unusable) node-state counts from `sinfo -h -o '%D %T'`.

    `-h` suppresses the header so every line is data, and `%D %T` is
    node-count-then-state, which is stable across Slurm versions -- unlike the
    column layout of `sinfo -s`, whose NODES(A/I/O/T) field is an aggregate and
    names no state at all.

    Unparseable lines are counted as unusable rather than ignored: a line this
    cannot read is not evidence that the fleet is healthy.
    """
    usable = unusable = 0
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 2:
            if line.strip():
                unusable += 1
            continue
        try:
            count = int(parts[0])
        except ValueError:
            unusable += 1
            continue
        if _sinfo_state_is_ok(parts[1]):
            usable += count
        else:
            unusable += count
    return usable, unusable


def _read_serial_first_line(serial_file_path):
    """Return the first line of a serial file with trailing newline stripped."""
    with open(serial_file_path) as fh:
        return fh.readline().rstrip("\n")


def _extract_rebuild_command(serial_file_path):
    """Return the last make_pcluster command recorded in a serial file, or None."""
    try:
        with open(serial_file_path) as fh:
            lines = [
                l.rstrip()
                for l in fh.readlines()
                if "make_pcluster" in l or l.startswith("./") or l.startswith("/")
            ]
        return lines[-1] if lines else None
    except FileNotFoundError:
        return None


def _load_defaults_file(defaults_path, toolkit_defaults_path, cluster_name):
    """Load a YAML defaults file and return its contents as a dict.

    Raises SystemExit if the file does not exist.
    Prints a warning if the caller is loading the toolkit's own template file.
    """
    if not os.path.isfile(defaults_path):
        sys.exit(
            f"ERROR: defaults file not found: {defaults_path}\n"
            f"  Copy the template first:\n"
            f"    cp pcluster_defaults.yml {cluster_name}.yml\n"
            f"  Then pass your copy:\n"
            f"    --use_defaults={cluster_name}.yml"
        )
    if os.path.abspath(defaults_path) == os.path.abspath(toolkit_defaults_path):
        print(
            f"\n  WARNING: You are loading the toolkit's own pcluster_defaults.yml directly.\n"
            f"  This file is the toolkit template and may be overwritten by future updates.\n"
            f"  Create your own copy instead:\n"
            f"\n"
            f"    cp pcluster_defaults.yml {cluster_name}.yml\n"
            f"    # Edit {cluster_name}.yml for this cluster\n"
            f"    --use_defaults={cluster_name}.yml\n"
        )
    try:
        with open(defaults_path) as fh:
            return yaml.safe_load(fh) or {}
    except yaml.YAMLError as _e:
        sys.exit(f"ERROR: defaults file is not valid YAML: {defaults_path}\n  {_e}")


def _resolve(name, args, file_defaults, hardcoded_defaults, cast=None):
    """Return the resolved value for name using three-tier precedence.

    Precedence: CLI arg (args.<name>) > file_defaults > hardcoded_defaults.
    cast is applied to non-None values from file_defaults and hardcoded_defaults
    but NOT to CLI args (argparse already coerces those).
    """
    cli_val = getattr(args, name, None)
    if cli_val is not None:
        return cli_val
    if name in file_defaults:
        val = file_defaults[name]
        if cast and val is not None:
            try:
                return cast(val)
            except (ValueError, TypeError) as _e:
                sys.exit(f"ERROR: parameter '{name}' has invalid value {val!r}: {_e}")
        return val
    val = hardcoded_defaults.get(name)
    if cast and val is not None:
        try:
            return cast(val)
        except (ValueError, TypeError) as _e:
            sys.exit(f"ERROR: parameter '{name}' has invalid value {val!r}: {_e}")
    return val


def _resolve_bool(name, args, file_defaults, hardcoded_defaults):
    """Return True/False for a three-tier string boolean parameter."""
    val = _resolve(name, args, file_defaults, hardcoded_defaults)
    if val is None:
        sys.exit(
            f"ERROR: required boolean parameter '{name}' has no value in CLI args, defaults file, or hardcoded defaults."
        )
    if isinstance(val, bool):
        return val
    if isinstance(val, int):
        return val != 0
    return str(val).lower() == "true"


def _resolve_access_script_path(cluster_data_root, cluster_name):
    """Return the absolute path to the cluster's access script.

    Raises SystemExit if cluster_name would escape cluster_data_root via
    path traversal (e.g. '../other').
    """
    root = os.path.normpath(cluster_data_root)
    path = os.path.normpath(
        os.path.join(root, cluster_name, f"access_cluster.{cluster_name}.sh")
    )
    if not path.startswith(root + os.sep):
        sys.exit(
            f"ERROR: Resolved access script path escapes active_clusters/: {path}\n"
            f"  cluster_name must not contain path traversal sequences."
        )
    return path


# ---------------------------------------------------------------------------
# Validation guards (extracted from make_pcluster.py main())
# ---------------------------------------------------------------------------


def _validate_fsx_size(fsx_size, enable_fsx):
    """Raise SystemExit if fsx_size is invalid when FSx is enabled.

    The rule is PCluster's own, not a rounder approximation of it:
    FsxStorageCapacityValidator (pcluster/validators/fsx_validators.py) accepts
    1200 or any multiple of 2400 for SCRATCH_2, PERSISTENT_1 and PERSISTENT_2.
    SCRATCH_2 is what this toolkit gets -- config.pcluster.j2's FsxLustreSettings
    sets StorageCapacity and no DeploymentType, and cluster_config.py defaults it
    to SCRATCH_2 when neither backup_id nor file_system_id is given.

    A positive-multiple-of-1200 test is looser in exactly the wrong direction:
    3600, 6000 and 8400 all passed here and are all rejected by FSx at stack
    launch, long after the five managed policies, the role, the keypair, the S3
    bucket and the Secrets Manager secret exist.
    """
    if not enable_fsx:
        return
    if fsx_size <= 0 or not (fsx_size == 1200 or fsx_size % 2400 == 0):
        sys.exit(
            f"*** ERROR ***\nfsx_size must be 1200 GB or a multiple of 2400 GB "
            f"(got {fsx_size}).\n"
            f"  FSx for Lustre SCRATCH_2 accepts 1200, 2400, 4800, 7200, ..."
        )


# Every bound below is PCluster's own, read out of
# pcluster/validators/ebs_validators.py rather than approximated. Keep them in
# sync with that file if the pinned aws-parallelcluster version changes.
_EBS_SIZE_BOUNDS = {
    "standard": (1, 1024),
    "io1": (4, 16384),
    "io2": (4, 65536),
    "gp2": (1, 16384),
    "gp3": (1, 16384),
    "st1": (500, 16384),
    "sc1": (500, 16384),
}
_EBS_IOPS_BOUNDS = {"io1": (100, 64000), "io2": (100, 256000), "gp3": (3000, 16000)}
_EBS_IOPS_TO_SIZE_RATIO = {"io1": 50, "io2": 1000, "gp3": 500}
_EBS_GP3_THROUGHPUT_BOUNDS = (125, 1000)
_EBS_THROUGHPUT_TO_IOPS_RATIO = 0.25


def _validate_ebs_volume(*, label, size, volume_type, iops, throughput):
    """Raise SystemExit if one EBS volume's parameters are out of range.

    Keyword-only: five same-typed parameters, three of them integers, so a
    transposed pair validates the wrong number against the wrong bound and
    reports a plausible error instead of raising.

    `label` is the operator-facing parameter prefix (`headnode_root_volume`,
    `gpu_root_volume`, ...) so the message names the volume that is wrong. A
    bare "EBS volume out of range" sends the operator hunting through four
    volumes.
    """
    size, iops, throughput = int(size), int(iops), int(throughput)

    min_size, max_size = _EBS_SIZE_BOUNDS.get(volume_type, (1, 16384))
    if size < min_size:
        sys.exit(
            f"*** ERROR ***\nThe size of {volume_type} volumes must be at least "
            f"{min_size:,} GiB.\n  {label}_size = {size} GB"
        )
    if size > max_size:
        sys.exit(
            f"*** ERROR ***\nThe size of {volume_type} volumes cannot exceed "
            f"{max_size:,} GiB.\n  {label}_size = {size} GB"
        )

    # gp2/st1/sc1/standard take neither Iops nor Throughput, and
    # config.pcluster.j2 renders neither for them, so whatever these hold is
    # unused -- rejecting it would fail a configuration that is perfectly valid.
    if volume_type not in _EBS_IOPS_BOUNDS:
        return

    min_iops, max_iops = _EBS_IOPS_BOUNDS[volume_type]
    if iops < min_iops or iops > max_iops:
        sys.exit(
            f"*** ERROR ***\n{label}_iops must be between {min_iops:,} and "
            f"{max_iops:,} for {volume_type} volumes (got {iops})."
        )
    max_for_size = size * _EBS_IOPS_TO_SIZE_RATIO[volume_type]
    if iops > max_for_size:
        sys.exit(
            f"*** ERROR ***\n{label}_iops of {iops} exceeds "
            f"{_EBS_IOPS_TO_SIZE_RATIO[volume_type]} IOPS per GiB, the maximum "
            f"for {volume_type}.\n"
            f"  {label}_size = {size} GB allows at most {max_for_size:,} IOPS."
        )

    if volume_type != "gp3":
        return

    min_tp, max_tp = _EBS_GP3_THROUGHPUT_BOUNDS
    if throughput < min_tp or throughput > max_tp:
        sys.exit(
            f"*** ERROR ***\n{label}_throughput must be between {min_tp} MB/s and "
            f"{max_tp:,} MB/s for gp3 volumes (got {throughput})."
        )
    max_tp_for_iops = iops * _EBS_THROUGHPUT_TO_IOPS_RATIO
    if throughput > max_tp_for_iops:
        sys.exit(
            f"*** ERROR ***\n{label}_throughput of {throughput} MB/s exceeds the "
            f"0.25 throughput-to-IOPS ratio.\n"
            f"  {label}_iops = {iops} allows at most {max_tp_for_iops:g} MB/s."
        )


def _validate_ebs_config(
    *,
    headnode_size,
    headnode_type,
    headnode_iops,
    headnode_throughput,
    compute_size,
    compute_type,
    compute_iops,
    compute_throughput,
    gpu_size,
    gpu_type,
    gpu_iops,
    gpu_throughput,
    shared_size,
    shared_type,
    shared_iops,
    shared_throughput,
    enable_cpu_queue,
    enable_gpu_queue,
):
    """Raise SystemExit if any EBS volume parameter is out of range.

    All four volumes config.pcluster.j2 renders are checked, not just the shared
    one. Upstream validates every one of them -- the head node root volume at
    HeadNode._register_validators, each queue's at SlurmQueue's, the shared one
    through the Ebs base class -- but it does so when the config is loaded to
    launch the stack, by which point the five managed policies, the IAM role, the
    keypair, the S3 bucket and the Secrets Manager secret all exist and have to
    be swept before a retry. The GPU root volume in particular reached no local
    check at all.

    A queue's volume is checked only when that queue exists: config.pcluster.j2
    renders the block under `enable_cpu_queue` / `enable_gpu_queue`, so on a
    GPU-only cluster the compute values are never written anywhere and rejecting
    them would fail a valid configuration.
    """
    _validate_ebs_volume(
        label="headnode_root_volume",
        size=headnode_size,
        volume_type=headnode_type,
        iops=headnode_iops,
        throughput=headnode_throughput,
    )
    if enable_cpu_queue:
        _validate_ebs_volume(
            label="compute_root_volume",
            size=compute_size,
            volume_type=compute_type,
            iops=compute_iops,
            throughput=compute_throughput,
        )
    if enable_gpu_queue:
        _validate_ebs_volume(
            label="gpu_root_volume",
            size=gpu_size,
            volume_type=gpu_type,
            iops=gpu_iops,
            throughput=gpu_throughput,
        )
    _validate_ebs_volume(
        label="ebs_shared_volume",
        size=shared_size,
        volume_type=shared_type,
        iops=shared_iops,
        throughput=shared_throughput,
    )


def _validate_ebs_shared_dir(path):
    """Raise SystemExit if path is not a safe absolute Unix path."""
    if not path.startswith("/"):
        sys.exit(
            f'*** ERROR ***\n"{path}" does not appear to be a Unix file path! Try "/{path}" instead.'
        )
    if not re.fullmatch(r"/[^\x00-\x1f\"\'\\;|&`$<>]+", path):
        sys.exit(
            f"*** ERROR ***\nebs_shared_dir contains invalid characters: {path!r}\n"
            f"  Only printable characters excluding quotes, backslash, and shell metacharacters are permitted."
        )


def _validate_queue_sizes(initial_queue_size, max_queue_size, scaledown_idletime):
    """Raise SystemExit if queue-size parameters are out of range."""
    if scaledown_idletime < 1:
        sys.exit(
            f"ERROR: scaledown_idletime must be >= 1 minute (got {scaledown_idletime})."
        )
    if initial_queue_size < 0:
        sys.exit(
            f"ERROR: initial_queue_size must be >= 0 (got {initial_queue_size})."
        )
    if initial_queue_size > max_queue_size:
        sys.exit(
            f"ERROR: initial_queue_size ({initial_queue_size}) must not exceed "
            f"max_queue_size ({max_queue_size})."
        )


# ---------------------------------------------------------------------------
# IAM / policy functions (moved from make_pcluster.py)
# ---------------------------------------------------------------------------


def _render_policy(
    src_path,
    aws_account_id,
    region,
    vpc_id,
    prod_level,
    cluster_serial_number,
    cluster_name,
    cluster_owner,
    cluster_serial_datestamp,
):
    """Render an IAM policy template, minify, and enforce the 6,144-byte limit."""
    _IAM_POLICY_LIMIT = 6144
    with open(src_path) as fh:
        raw = (
            fh.read()
            .replace("<AWS_ACCOUNT_ID>", aws_account_id)
            .replace("<AWS_REGION>", region)
            .replace("<VPC_ID>", vpc_id)
            .replace("<PROD_LEVEL>", prod_level)
            .replace("<CLUSTER_SERIAL_NUMBER>", cluster_serial_number)
            .replace("<CLUSTER_NAME>", cluster_name)
            .replace("<CLUSTER_OWNER>", cluster_owner)
            .replace("<CLUSTER_SERIAL_DATESTAMP>", cluster_serial_datestamp)
        )
    minified = json.dumps(json.loads(raw), separators=(",", ":"))
    size = len(minified.encode("utf-8"))
    if size > _IAM_POLICY_LIMIT:
        raise ValueError(
            f"*** ERROR ***\n"
            f"  Rendered IAM policy from {os.path.basename(src_path)} is {size} bytes "
            f"(limit: {_IAM_POLICY_LIMIT}).\n"
            f"  This usually happens when cluster_owner or cluster_serial_number is very long.\n"
            f"  cluster_owner='{cluster_owner}' ({len(cluster_owner)} chars), "
            f"cluster_serial_number='{cluster_serial_number}' ({len(cluster_serial_number)} chars)."
        )
    return minified


def _setup_iam(
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
    region="",
    vpc_id="",
    enable_monitoring=False,
):
    """Create ec2_iam_role and attach managed policies. Idempotent.

    Head node role gets: HeadNode-Compute, HeadNode-Storage, HeadNode-IAM, ComputeNode-Base
    (+ HeadNode-Monitoring when enable_monitoring=True).
    Every compute queue gets ComputeNode-Base via AdditionalIamPolicies in the cluster config.
    """
    _role_existed = False
    _template_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _tmpl = os.path.join(_template_dir, "templates")

    # All suffixes this function ever attaches, regardless of the current
    # enable_monitoring value — used to detect stale policies left over from
    # an earlier build (e.g. monitoring was enabled, then disabled on a
    # same-serial rebuild).
    _ALL_SUFFIXES = ["-HeadNode-Compute", "-HeadNode-Storage", "-HeadNode-IAM", "-ComputeNode-Base", "-HeadNode-Monitoring"]

    _suffixes = ["-HeadNode-Compute", "-HeadNode-Storage", "-HeadNode-IAM", "-ComputeNode-Base"]
    if enable_monitoring:
        _suffixes.append("-HeadNode-Monitoring")

    try:
        iam.get_role(RoleName=ec2_iam_role)
        _role_existed = True
        attached = {
            p["PolicyName"]
            for p in iam.list_attached_role_policies(RoleName=ec2_iam_role)[
                "AttachedPolicies"
            ]
        }
        expected = {ec2_iam_policy + s for s in _suffixes}
        known = {ec2_iam_policy + s for s in _ALL_SUFFIXES}
        stale = (attached & known) - expected
        if expected.issubset(attached) and not stale:
            print(f"  Found ec2_iam_role with all policies attached: {ec2_iam_role}")
            return
        if stale:
            print(
                f"  Found ec2_iam_role {ec2_iam_role} with stale policies {stale} "
                f"(no longer expected under the current flags) — cleaning up and recreating policies."
            )
        else:
            print(
                f"  Found ec2_iam_role {ec2_iam_role} but missing policies "
                f"{expected - attached} — cleaning up and recreating policies."
            )
        _delete_managed_policies(
            iam, ec2_iam_role, ec2_iam_policy, aws_account_id,
            suppress=True, enable_monitoring=True,
        )
    except _ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise

    render_args = (
        aws_account_id,
        region,
        vpc_id,
        prod_level,
        cluster_serial_number,
        cluster_name,
        cluster_owner,
        cluster_serial_datestamp,
    )

    policies = [
        ("-HeadNode-Compute",   os.path.join(_tmpl, "HeadNode-Compute.json_src")),
        ("-HeadNode-Storage",   os.path.join(_tmpl, "HeadNode-Storage.json_src")),
        ("-HeadNode-IAM",       os.path.join(_tmpl, "HeadNode-IAM.json_src")),
        ("-ComputeNode-Base",   os.path.join(_tmpl, "ComputeNode-Base.json_src")),
    ]
    if enable_monitoring:
        policies.append(("-HeadNode-Monitoring", os.path.join(_tmpl, "HeadNode-Monitoring.json_src")))

    rendered = {sfx: _render_policy(src, *render_args) for sfx, src in policies}

    with open(
        os.open(ec2_json_policy_template, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600),
        "w",
    ) as fh:
        fh.write(rendered["-HeadNode-Compute"])

    if not _role_existed:
        iam.create_role(
            RoleName=ec2_iam_role,
            AssumeRolePolicyDocument='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":["ec2.amazonaws.com"]},"Action":"sts:AssumeRole"}]}',
            Description="ParallelClusterMaker EC2 IAM instance role",
        )
    for sfx, _ in policies:
        resp = iam.create_policy(
            PolicyName=ec2_iam_policy + sfx,
            PolicyDocument=rendered[sfx],
        )
        iam.attach_role_policy(RoleName=ec2_iam_role, PolicyArn=resp["Policy"]["Arn"])
        print(f"  Created {sfx[1:]}: {ec2_iam_policy}{sfx}")
    print(f"  Created ec2_iam_role: {ec2_iam_role}")


def _cleanup_iam_on_failure(iam, ec2_iam_role, ec2_iam_policy, aws_account_id, enable_monitoring=False):
    """Delete all managed policies and the IAM role after a failed _setup_iam call."""
    _delete_managed_policies(
        iam, ec2_iam_role, ec2_iam_policy, aws_account_id,
        suppress=True, enable_monitoring=enable_monitoring,
    )
    with contextlib.suppress(Exception):
        iam.delete_role(RoleName=ec2_iam_role)


def _delete_managed_policies(
    iam,
    ec2_iam_role,
    ec2_iam_policy,
    aws_account_id,
    suppress=True,
    fsx_policy=None,
    enable_monitoring=False,
):
    """Detach and delete managed cluster policies (and optional FSx inline policy)."""
    suffixes = ["-HeadNode-Compute", "-HeadNode-Storage", "-HeadNode-IAM", "-ComputeNode-Base"]
    if enable_monitoring:
        suffixes.append("-HeadNode-Monitoring")
    for sfx in suffixes:
        name = ec2_iam_policy + sfx
        arn = f"arn:aws:iam::{aws_account_id}:policy/{name}"
        if suppress:
            with contextlib.suppress(Exception):
                iam.detach_role_policy(RoleName=ec2_iam_role, PolicyArn=arn)
            with contextlib.suppress(Exception):
                iam.delete_policy(PolicyArn=arn)
                print(f"  Deleted managed policy: {name}")
        else:
            try:
                iam.detach_role_policy(RoleName=ec2_iam_role, PolicyArn=arn)
            except Exception as _e:
                print(f"  Warning: could not detach policy {name}: {_e}")
            try:
                iam.delete_policy(PolicyArn=arn)
                print(f"  Deleted managed policy: {name}")
            except Exception as _e:
                print(f"  Warning: could not delete policy {name}: {_e}")
    if fsx_policy:
        if suppress:
            with contextlib.suppress(Exception):
                iam.delete_role_policy(RoleName=ec2_iam_role, PolicyName=fsx_policy)
                print(f"  Deleted FSx hydration policy: {fsx_policy}")
        else:
            try:
                iam.delete_role_policy(RoleName=ec2_iam_role, PolicyName=fsx_policy)
                print(f"  Deleted FSx hydration policy: {fsx_policy}")
            except Exception as _e:
                print(f"  Warning: could not delete FSx policy {fsx_policy}: {_e}")


def _setup_fsx_hydration_iam(
    iam,
    ec2_iam_role,
    fsx_hydration_iam_policy,
    fsx_hydration_json_policy_src,
    fsx_hydration_policy_template,
    fsx_s3_export_bucket,
    fsx_s3_import_bucket,
):
    """Create FSx-S3 hydration inline policy and attach to the cluster IAM role."""
    with open(fsx_hydration_json_policy_src) as fh:
        policy = (
            fh.read()
            .replace("<FSX_S3_EXPORT_BUCKET>", fsx_s3_export_bucket)
            .replace("<FSX_S3_IMPORT_BUCKET>", fsx_s3_import_bucket)
        )
    with open(
        os.open(
            fsx_hydration_policy_template, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
        ),
        "w",
    ) as fh:
        fh.write(policy)
    iam.put_role_policy(
        RoleName=ec2_iam_role,
        PolicyName=fsx_hydration_iam_policy,
        PolicyDocument=policy,
    )
    print(f"  Created fsx_hydration_iam_policy: {fsx_hydration_iam_policy}")
    print(f"  Attached to: {ec2_iam_role}")


def _ssh_secret_name(cluster_name, cluster_serial_number):
    """Return the Secrets Manager secret name for a cluster's SSH private key."""
    return f"parallelcluster/{cluster_name}/{cluster_serial_number}/ssh-private-key"


_AUTHORIZED_KEYS = "$HOME/.ssh/authorized_keys"


def _append_key_script(authorized_keys=_AUTHORIZED_KEYS):
    """Shell script run on the head node to append a public key read from stdin.

    A bare `cat >>` concatenates onto the final line when the existing
    authorized_keys does not end in a newline (hand-edited, or written by a tool
    using `content:` without a trailing \\n). That corrupts both the last
    existing key and the new one, and the caller's verify step then reports "the
    old key was left in place" — which is false, since the old key is broken
    too. Append a newline first if one is needed.
    """
    return (
        'set -eu; '
        f'AKEYS="{authorized_keys}"; AKDIR="$(dirname "$AKEYS")"; '
        'mkdir -p "$AKDIR"; chmod 700 "$AKDIR"; '
        'touch "$AKEYS"; chmod 600 "$AKEYS"; '
        'if [ -s "$AKEYS" ] && '
        '[ "$(tail -c 1 "$AKEYS" | wc -l)" -eq 0 ]; then '
        'printf "\\n" >> "$AKEYS"; fi; '
        'cat >> "$AKEYS"'
    )


def _remove_old_key_script(authorized_keys=_AUTHORIZED_KEYS):
    """Shell script run on the head node to revoke the old public key.

    Reads the old key on the first stdin line and the new key on the second.

    The live authorized_keys file is never modified in place and is only
    replaced by `mv` after the candidate replacement has been positively
    verified to contain the new key and NOT the old key.  This is deliberately
    NOT "filter, move, then check the result is correct" — that ordering lets a
    failed/short-circuited filter step (grep exiting 1 on a full match, a write
    failure, a killed process, disk pressure) silently produce empty or garbage
    content that still gets moved into place, since `mv` only cares about
    directory permissions, not the content or success of whatever produced its
    source file.  Validating first and moving only on success means a bad
    intermediate result never reaches the live file, however it was produced.
    `mktemp` gives every attempt a fresh, unpredictable filename, so a stale
    temp file left behind by an earlier interrupted rotation can never be
    mistaken for this run's output.

    Matching is on the "<type> <base64>" prefix, NOT the whole line:
    `ssh-keygen -y` emits no trailing comment, but the deployed authorized_keys
    line almost always carries one (Ansible's ec2_key and ssh-copy-id both
    append one).  An exact whole-line match therefore filters nothing, and the
    "old key absent" assertion passes vacuously against a candidate that still
    authorizes the old key — rotation reports success while revoking nothing.

    The filter and both assertions use `grep -F` defensively, not to fix a
    reachable bug: `grep` defaults to basic regular expressions, in which `+`
    and `/` — the only non-alphanumeric characters in the base64 key body — are
    literal, so for the keys this toolkit generates `-F` and no `-F` select
    exactly the same lines.  It is here so that an authorized_keys line carrying
    a genuine BRE metacharacter (`.`, `*`, `[`, `\\`, `^`, `$`) in its key-type
    or options field, whatever writes it, cannot turn the revocation filter into
    a pattern that matches more than the one key it was given.
    """
    return (
        'set -eu; '
        f'AKEYS="{authorized_keys}"; AKDIR="$(dirname "$AKEYS")"; '
        'IFS= read -r OLDKEY; '
        'IFS= read -r NEWKEY; '
        'OLDPFX="$(printf %s "$OLDKEY" | cut -d" " -f1,2)"; '
        'NEWPFX="$(printf %s "$NEWKEY" | cut -d" " -f1,2)"; '
        '[ -n "$OLDPFX" ] && [ -n "$NEWPFX" ] || { echo "could not parse key material; aborting without touching the live file" >&2; exit 1; }; '
        'TMP="$(mktemp "$AKDIR"/authorized_keys.XXXXXX)"; '
        'trap \'rm -f "$TMP"\' EXIT; '
        'grep -vF "$OLDPFX" "$AKEYS" > "$TMP" || true; '
        'grep -qF "$NEWPFX" "$TMP" || { echo "new key missing from candidate authorized_keys; aborting without touching the live file" >&2; exit 1; }; '
        'if grep -qF "$OLDPFX" "$TMP"; then echo "old key still present in candidate authorized_keys; aborting without touching the live file" >&2; exit 1; fi; '
        'chmod 600 "$TMP"; '
        'mv "$TMP" "$AKEYS"'
    )


def _read_turbot_from_vars_file(vars_file_path):
    """Return turbot_account from a rendered vars file, or 'disabled' if absent/unreadable."""
    try:
        with open(vars_file_path) as _f:
            data = yaml.safe_load(_f) or {}
        value = data.get("turbot_account", "disabled")
        return value if value else "disabled"
    except Exception:
        return "disabled"


def _get_efa_instance_types(ec2client, fallback):
    """Return the set of EFA-capable instance type strings from EC2.

    Pages through describe_instance_types with the efa-supported filter.
    Falls back to the provided static list on any error so the caller always
    gets a usable set even in restricted or offline environments.
    """
    try:
        types = []
        paginator = ec2client.get_paginator("describe_instance_types")
        for page in paginator.paginate(
            Filters=[{"Name": "network-info.efa-supported", "Values": ["true"]}]
        ):
            for it in page["InstanceTypes"]:
                types.append(it["InstanceType"])
        if types:
            return set(types)
        # Empty result is unexpected; fall through to fallback.
        print("  Note: describe_instance_types returned no EFA instances; using built-in list.")
    except Exception as _e:
        print(f"  Note: could not query EFA instance types ({_e}); using built-in list.")
    return set(fallback)


def _validate_network(
    ec2client,
    az,
    vpc_name,
    headnode_subnet_id,
    compute_az_list,
    compute_subnet_ids_override,
    use_private_compute_subnet,
    cluster_name="",
    gpu_az_list=None,
    gpu_subnet_ids_override=None,
    use_private_gpu_subnet="false",
):
    """Return (vpc_id, headnode_subnet_id, compute_subnet_ids, gpu_subnet_ids, vpc_cidr).

    Auto-discovery picks the *first* subnet returned by EC2 in each AZ.
    EC2 does not guarantee ordering, so the result is non-deterministic when
    multiple subnets exist in the same AZ. Always provide explicit subnet IDs
    (--headnode_subnet_id, --compute_subnet_ids) for production clusters.
    """
    from pcluster_aux_data import refer_to_docs_and_quit

    print(f"  Resolving VPC '{vpc_name}'...")
    if vpc_name == "vpc_default":
        vpc_info = ec2client.describe_vpcs(
            Filters=[{"Name": "isDefault", "Values": ["true"]}]
        )
    else:
        vpc_info = ec2client.describe_vpcs(
            Filters=[{"Name": "tag:Name", "Values": [vpc_name]}]
        )
    vpc_ids = [v["VpcId"] for v in vpc_info["Vpcs"]]
    if not vpc_ids:
        import sys
        print("")
        print("*** ERROR: VPC not found ***")
        print(f'  No VPC named "{vpc_name}" exists in this account and region.')
        print(f"  Fix: set vpc_name in your defaults file to the Name tag of")
        print(f"       an existing VPC, then re-run.")
        _hint = f"{cluster_name}_defaults.yml" if cluster_name else "<cluster_name>_defaults.yml"
        print(f"  Hint: you probably forgot to add --use_defaults={_hint}")
        print("")
        sys.exit(1)
    vpc_id = vpc_ids[0]
    vpc_cidr = vpc_info["Vpcs"][0].get("CidrBlock", "10.0.0.0/8")

    def _discover_subnet(target_az, private_only=False):
        filters = [
            {"Name": "availabilityZone", "Values": [target_az]},
            {"Name": "vpc-id", "Values": [vpc_id]},
        ]
        if private_only:
            filters.append({"Name": "map-public-ip-on-launch", "Values": ["false"]})
        info = ec2client.describe_subnets(Filters=filters)
        subnets = info["Subnets"]
        if not subnets:
            suffix = (
                " (private subnets only — map-public-ip-on-launch=false)"
                if private_only
                else ""
            )
            refer_to_docs_and_quit(
                f"No subnets found in AZ {target_az} within VPC {vpc_id}{suffix}."
            )
        if len(subnets) > 1:
            print(
                f"*** WARNING ***\n"
                f"  {len(subnets)} subnets found in {target_az}; using {subnets[0]['SubnetId']}.\n"
                f"  Use --headnode_subnet_id / --compute_subnet_ids to select explicitly."
            )
        return subnets[0]["SubnetId"]

    if headnode_subnet_id:
        print(f"  Using explicit head node subnet: {headnode_subnet_id}")
    else:
        print(f"  Auto-discovering head node subnet in {az}...")
        headnode_subnet_id = _discover_subnet(az)

    if compute_subnet_ids_override:
        compute_subnet_ids = [
            s.strip() for s in compute_subnet_ids_override.split(",") if s.strip()
        ]
        print(f"  Using explicit compute subnet(s): {', '.join(compute_subnet_ids)}")
    else:
        _private = use_private_compute_subnet == "true"
        _label = "private compute" if _private else "compute"
        print(
            f"  Auto-discovering {_label} subnet(s) in: {', '.join(compute_az_list)}..."
        )
        compute_subnet_ids = [
            _discover_subnet(caz, private_only=_private) for caz in compute_az_list
        ]

    if gpu_subnet_ids_override:
        gpu_subnet_ids = [
            s.strip() for s in gpu_subnet_ids_override.split(",") if s.strip()
        ]
        print(f"  Using explicit GPU subnet(s): {', '.join(gpu_subnet_ids)}")
    else:
        _gpu_private = use_private_gpu_subnet == "true"
        # Falling back to compute's AZs is what README.md documents ("--gpu_az
        # falls back to compute_az then --az"), and compute_az_list already
        # defaults to [az].
        _gpu_azs = gpu_az_list or compute_az_list
        # Copying compute's subnets is correct only when nothing distinguishes
        # the GPU queue's placement. It is *not* correct when the operator asked
        # for private GPU subnets and compute's are not known to be private:
        # --use_private_gpu_subnet=true was silently ignored on that path, and
        # the GPU fleet landed on whatever subnets compute used -- public ones if
        # --use_private_compute_subnet was left at its default, which is exactly
        # the case the flag exists for.
        _compute_is_private = (
            not compute_subnet_ids_override and use_private_compute_subnet == "true"
        )
        _reuse_compute = not gpu_az_list and not (_gpu_private and not _compute_is_private)
        if _reuse_compute:
            gpu_subnet_ids = list(compute_subnet_ids)
        else:
            _gpu_label = "private GPU" if _gpu_private else "GPU"
            if not gpu_az_list:
                print(
                    f"  --use_private_gpu_subnet=true with no --gpu_subnet_ids: "
                    f"discovering private subnets rather than reusing compute's."
                )
            print(
                f"  Auto-discovering {_gpu_label} subnet(s) in: {', '.join(_gpu_azs)}..."
            )
            gpu_subnet_ids = [
                _discover_subnet(gaz, private_only=_gpu_private) for gaz in _gpu_azs
            ]

    return vpc_id, headnode_subnet_id, compute_subnet_ids, gpu_subnet_ids, vpc_cidr


_REGION_TO_LOCATION = {
    "us-east-1":      "US East (N. Virginia)",
    "us-east-2":      "US East (Ohio)",
    "us-west-1":      "US West (N. California)",
    "us-west-2":      "US West (Oregon)",
    "ca-central-1":   "Canada (Central)",
    "ca-west-1":      "Canada West (Calgary)",
    "eu-west-1":      "Europe (Ireland)",
    "eu-west-2":      "Europe (London)",
    "eu-west-3":      "Europe (Paris)",
    "eu-central-1":   "Europe (Frankfurt)",
    "eu-central-2":   "Europe (Zurich)",
    "eu-north-1":     "Europe (Stockholm)",
    "eu-south-1":     "Europe (Milan)",
    "eu-south-2":     "Europe (Spain)",
    "ap-east-1":      "Asia Pacific (Hong Kong)",
    "ap-southeast-1": "Asia Pacific (Singapore)",
    "ap-southeast-2": "Asia Pacific (Sydney)",
    "ap-southeast-3": "Asia Pacific (Jakarta)",
    "ap-southeast-4": "Asia Pacific (Melbourne)",
    "ap-northeast-1": "Asia Pacific (Tokyo)",
    "ap-northeast-2": "Asia Pacific (Seoul)",
    "ap-northeast-3": "Asia Pacific (Osaka)",
    "ap-south-1":     "Asia Pacific (Mumbai)",
    "ap-south-2":     "Asia Pacific (Hyderabad)",
    "me-south-1":     "Middle East (Bahrain)",
    "me-central-1":   "Middle East (UAE)",
    "af-south-1":     "Africa (Cape Town)",
    "il-central-1":   "Israel (Tel Aviv)",
    "sa-east-1":      "South America (Sao Paulo)",
}


def _get_od_price(pricing_client, instance_type, region):
    """Return (price_float, None) on success or (None, reason_str) on failure."""
    location = _REGION_TO_LOCATION.get(region)
    if not location:
        return None, f"region '{region}' not in Pricing location map"
    try:
        resp = pricing_client.get_products(
            ServiceCode="AmazonEC2",
            Filters=[
                {"Type": "TERM_MATCH", "Field": "instanceType",    "Value": instance_type},
                {"Type": "TERM_MATCH", "Field": "location",        "Value": location},
                {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": "Linux"},
                {"Type": "TERM_MATCH", "Field": "preInstalledSw",  "Value": "NA"},
                {"Type": "TERM_MATCH", "Field": "tenancy",         "Value": "Shared"},
                {"Type": "TERM_MATCH", "Field": "capacitystatus",  "Value": "Used"},
            ],
            MaxResults=1,
        )
    except _ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("AccessDeniedException", "AccessDenied"):
            return None, "unavailable — add pricing:GetProducts to operator IAM policy"
        return None, f"unavailable — Pricing API error: {code}"
    except Exception as e:
        return None, f"unavailable — Pricing API unreachable: {e}"

    if not resp.get("PriceList"):
        return None, f"unavailable — '{instance_type}' not found in Pricing catalog"

    try:
        pl = json.loads(resp["PriceList"][0])
        od_terms = pl["terms"]["OnDemand"]
        price_dim = list(list(od_terms.values())[0]["priceDimensions"].values())[0]
        price = float(price_dim["pricePerUnit"]["USD"])
        return price, None
    except (KeyError, IndexError, ValueError, json.JSONDecodeError) as e:
        return None, f"unavailable — unexpected Pricing API response shape: {e}"


def _get_spot_price(ec2client, instance_type):
    """Return (price_float, None) on success or (None, reason_str) on failure."""
    try:
        resp = ec2client.describe_spot_price_history(
            InstanceTypes=[instance_type],
            ProductDescriptions=["Linux/UNIX"],
            MaxResults=1,
        )
    except _ClientError as e:
        code = e.response["Error"]["Code"]
        return None, f"unavailable — {code}"
    except Exception as e:
        return None, f"unavailable — {e}"

    history = resp.get("SpotPriceHistory", [])
    if not history:
        return None, f"unavailable — no spot price history for '{instance_type}'"
    try:
        return float(history[0]["SpotPrice"]), None
    except (KeyError, ValueError) as e:
        return None, f"unavailable — unexpected spot price response: {e}"


def _cost_summary_lines(
    *,
    pricing_client, ec2client,
    headnode_instance_type,
    cpu_instance_types, max_cpu_queue_size, enable_cpu_queue,
    gpu_instance_types, max_gpu_queue_size, enable_gpu_queue,
    region, cluster_type,
):
    """Return list of strings to inject into the build summary.

    Returns a single-element list with an error message on unexpected failure.
    Only types whose OD lookup succeeds contribute to the spot range, so the
    two ranges are always derived from the same subset of instance types.
    """
    try:
        lines = []
        is_spot = cluster_type == "spot"

        def _price_range(types):
            """Return (od_min, od_max, sp_min, sp_max, partial_note).

            Only types with a successful OD lookup are included in either range.
            partial_note is set when at least one type failed but others succeeded.
            Returns all-None when every type fails; caller checks od_min is None.
            """
            od_prices, spot_prices = [], []
            failed, succeeded = 0, 0
            for t in types:
                od, _ = _get_od_price(pricing_client, t, region)
                if od is None:
                    failed += 1
                    continue
                succeeded += 1
                od_prices.append(od)
                if is_spot:
                    sp, _ = _get_spot_price(ec2client, t)
                    if sp is not None:
                        spot_prices.append(sp)
            od_min = min(od_prices) if od_prices else None
            od_max = max(od_prices) if od_prices else None
            sp_min = min(spot_prices) if spot_prices else None
            sp_max = max(spot_prices) if spot_prices else None
            partial_note = f" ({failed} type(s) unavailable)" if failed and succeeded else ""
            return od_min, od_max, sp_min, sp_max, partial_note

        def _fmt_od(min_p, max_p, count):
            if min_p is None:
                return None
            lo, hi = min_p * count, max_p * count
            if abs(hi - lo) < 0.001:
                return f"${lo:.3f}/hr"
            return f"${lo:.3f}–${hi:.3f}/hr"

        def _fmt_spot(min_p, max_p, count):
            if min_p is None:
                return None
            lo, hi = min_p * count, max_p * count
            if abs(hi - lo) < 0.001:
                return f"~${lo:.3f}/hr spot"
            return f"~${lo:.3f}–${hi:.3f}/hr spot"

        lines.append("  Estimated hourly cost (max fleet, on-demand unless noted):")

        hn_od, hn_err = _get_od_price(pricing_client, headnode_instance_type, region)
        if hn_od is not None:
            lines.append(f"    Head node  ({headnode_instance_type} × 1):   ${hn_od:.3f}/hr")
        else:
            lines.append(f"    Head node  ({headnode_instance_type} × 1):   {hn_err}")

        if enable_cpu_queue and cpu_instance_types and max_cpu_queue_size > 0:
            label = ", ".join(cpu_instance_types)
            od_min, od_max, sp_min, sp_max, note = _price_range(cpu_instance_types)
            od_str = _fmt_od(od_min, od_max, max_cpu_queue_size)
            sp_str = _fmt_spot(sp_min, sp_max, max_cpu_queue_size) if is_spot else None
            if od_str:
                suffix = f"  [{sp_str}]" if sp_str else ""
                lines.append(f"    CPU queue  ({label} × {max_cpu_queue_size}):   {od_str}{suffix}{note}")
            else:
                lines.append(f"    CPU queue  ({label} × {max_cpu_queue_size}):   unavailable")

        if enable_gpu_queue and gpu_instance_types and max_gpu_queue_size > 0:
            label = ", ".join(gpu_instance_types)
            od_min, od_max, sp_min, sp_max, note = _price_range(gpu_instance_types)
            od_str = _fmt_od(od_min, od_max, max_gpu_queue_size)
            sp_str = _fmt_spot(sp_min, sp_max, max_gpu_queue_size) if is_spot else None
            if od_str:
                suffix = f"  [{sp_str}]" if sp_str else ""
                lines.append(f"    GPU queue  ({label} × {max_gpu_queue_size}):   {od_str}{suffix}{note}")
            else:
                lines.append(f"    GPU queue  ({label} × {max_gpu_queue_size}):   unavailable")

        if is_spot:
            lines.append("    Note: spot prices are current ask; actual cost may differ.")

        return lines
    except Exception as e:
        return [f"  Estimated hourly cost:   unavailable — {e}"]


def _storage_summary_lines(
    *,
    ebs_shared_dir, ebs_shared_volume_size, ebs_shared_volume_type,
    enable_efs, efs_throughput_mode,
    enable_fsx, fsx_size,
    enable_fsx_hydration, fsx_s3_import_bucket, fsx_s3_import_path,
    fsx_s3_export_bucket, fsx_s3_export_path,
    enable_external_nfs, external_nfs_server,
):
    """Return build-summary lines naming every shared filesystem and its mount point.

    The Options: line names FSx/Lustre but not /fsx, so an operator was told
    Lustre existed without being told where it was mounted or how large it is.
    Mount points here must track config.pcluster.j2's SharedStorage block.

    Keyword-only: with 14 same-typed parameters, a transposed pair at the call
    site renders a plausible-looking summary rather than raising. Swapping the
    import/export bucket pairs, or the EBS size and type, both passed the whole
    suite while positional.

    The mount-point column is sized from whatever labels are actually active,
    not a hardcoded constant: `/efs`, `/fsx`, and `/nfs` are always 4
    characters, but `ebs_shared_dir` is operator-configurable (`--ebs_shared_dir`)
    and defaults to `/shared` -- 7 characters, one longer than a fixed width of
    6 could hold, which silently dropped its padding entirely.
    """
    mount_labels = [ebs_shared_dir]
    if enable_efs:
        mount_labels.append("/efs")
    if enable_fsx:
        mount_labels.append("/fsx")
    if enable_external_nfs:
        mount_labels.append("/nfs")
    col = max(len(label) for label in mount_labels) + 2
    continuation_indent = " " * (4 + col)

    lines = ["  Shared storage:"]
    lines.append(
        f"    {ebs_shared_dir:<{col}}EBS ({ebs_shared_volume_type}, "
        f"{ebs_shared_volume_size} GB)"
    )
    if enable_efs:
        lines.append(f"    {'/efs':<{col}}EFS ({efs_throughput_mode} throughput)")
    if enable_fsx:
        lines.append(f"    {'/fsx':<{col}}FSx for Lustre ({fsx_size} GB)")
        if enable_fsx_hydration:
            lines.append(
                f"{continuation_indent}S3 import: s3://{fsx_s3_import_bucket}/{fsx_s3_import_path}"
            )
            lines.append(
                f"{continuation_indent}S3 export: s3://{fsx_s3_export_bucket}/{fsx_s3_export_path}"
            )
            lines.append(f"{continuation_indent}Hydrate:   /usr/local/bin/import-s3-to-lustre.sh")
            lines.append(f"{continuation_indent}Export:    /usr/local/bin/export-lustre-to-s3.sh")
            lines.append(
                f"{continuation_indent}Progress:  /usr/local/bin/check-lustre-export-progress.sh"
            )
    if enable_external_nfs:
        lines.append(f"    {'/nfs':<{col}}external NFS ({external_nfs_server})")
    # Same precedence as vars_file.j2's pkg_dir/spack_root block; tests/ renders
    # that template for each combination and asserts the two agree.
    if enable_fsx:
        pkg_dir = "/fsx/pkg"
    elif enable_efs:
        pkg_dir = "/efs/pkg"
    elif enable_external_nfs:
        pkg_dir = "/nfs/pkg"
    else:
        pkg_dir = f"{ebs_shared_dir}/pkg"
    lines.append(f"    Spack and shared packages install under {pkg_dir}")
    return lines


_NODE_BOOTSTRAP_TIMEOUT = 2100  # PCluster's own default (pcluster/constants.py)
_CFN_WAIT_CONDITION_MAX = 43200  # CloudFormation's hard ceiling: 12 hours
_FSX_PROVISION_ALLOWANCE = 1800  # measured: 17m22s for a 1200 GB filesystem
_EFS_PROVISION_ALLOWANCE = 600  # measured: 4m21s pre-instance on osiris, 2026-07-28


def _derive_head_node_bootstrap_timeout(*, configured, enable_efs, enable_fsx):
    """Return the head-node bootstrap timeout, extended for shared filesystems.

    PCluster creates the HeadNodeWaitCondition *before* the head node
    (cluster_stack.py:293 precedes _add_head_node at 295), and the filesystem
    IDs land in HeadNodeLaunchTemplate (efs_fs_ids at 1362, fsx_fs_ids at 1375),
    so filesystem provisioning runs on the head node's critical path with the
    clock already running.  On the osiris build that failed, FSx took 17m22s of
    the stock 2100s window before the instance existed, leaving preinstall ~15
    of 35 minutes.

    EFS and FSx are independent resources with no dependency between them, so
    CloudFormation provisions them concurrently and the head node waits on the
    slower one -- hence max(), not a sum.

    Both allowances are now measured, and the EFS one is dominated by the
    mount target, not the filesystem.  On the successful osiris build of
    2026-07-28 (generalPurpose/bursting, one mount target in us-east-2a) the
    filesystem itself took 4s while the mount target took 1m33s, and the head
    node instance appeared 4m24s after the wait condition -- so 600s is roughly
    2.3x headroom over the whole pre-instance window rather than a guess.  A
    multi-AZ cluster creates one mount target per subnet; they provision
    concurrently, so the shape holds, but that has not been measured.

    An operator who sets the value explicitly is never overridden, including
    downward -- only the untouched default is extended.
    """
    if configured != _NODE_BOOTSTRAP_TIMEOUT:
        return _clamp_int(
            configured, 1, _CFN_WAIT_CONDITION_MAX, "head_node_bootstrap_timeout"
        )
    allowance = max(
        _FSX_PROVISION_ALLOWANCE if enable_fsx else 0,
        _EFS_PROVISION_ALLOWANCE if enable_efs else 0,
        0,
    )
    return _NODE_BOOTSTRAP_TIMEOUT + allowance


_SHA256_CHECKSUM_RE = re.compile(r"sha256:[0-9a-fA-F]{64}")


def _validate_download_checksum(name, value):
    """Abort unless value is a well-formed sha256:<64 hex> string.

    Every checksum the build resolves is handed to Ansible's get_url, which
    splits on ':' and int()s the remainder base 16 -- so a placeholder or a
    truncated digest is not caught until the playbook is already running, after
    the IAM policies, the role, the keypair, and the S3 bucket have been created.
    That is exactly what a stale <cluster>_defaults.yml produced: the file
    predated the docker_compose_* keys, _resolve fell through to
    _HARDCODED_DEFAULTS, and its "sha256:REPLACE_WITH_ACTUAL_SHA256" reached
    get_url as "The checksum format is invalid" 18 tasks in.

    Checking here rather than trusting the defaults file also covers the
    no-defaults-file path, where _HARDCODED_DEFAULTS is the only source.
    """
    if not _SHA256_CHECKSUM_RE.fullmatch(value or ""):
        sys.exit(
            f"*** ERROR ***\n"
            f'  Invalid {name} "{value}".\n'
            f"  Must be sha256:<64 hex digits>.\n"
            f"  If this is a placeholder, set a real value in your defaults file\n"
            f"  (copy the current one from pcluster_defaults.yml), or obtain it with:\n"
            f"    curl -sL <url> | sha256sum"
        )


_DOCKER_COMPOSE_VERSION_RE = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+$")


def _derive_docker_compose_staging(*, base_os, arm_oses, enable_monitoring, version):
    """Return (stage, arch) for the Docker Compose CLI plugin S3 staging.

    Amazon Linux 2023 is the only supported base_os with no
    docker-compose-plugin package, so aws-parallelcluster-monitoring's own
    installer/os/alinux2023.sh curls the binary from github.com on every node at
    boot -- unverified, and impossible from a private subnet.  The build stages a
    checksummed copy to S3 instead, exactly as it does the monitoring tarball.

    Both halves of the gate matter.  Without enable_monitoring the S3 object is
    never uploaded, and the wrapper's `aws s3 cp` on a missing key fails the
    node; without the base_os test every other OS would fetch a plugin its
    distro already packages from a signed repository.

    The arch is the plugin binary's own suffix (docker-compose-linux-<arch>),
    which is uname -m's spelling, not PCluster's -- so aarch64/x86_64 rather
    than arm64/amd64.  It is derived for every base_os, not just AL2023: the
    checksum it selects is threaded to templates unconditionally.
    """
    if not _DOCKER_COMPOSE_VERSION_RE.fullmatch(version or ""):
        sys.exit(
            f"*** ERROR ***\n"
            f'  Invalid docker_compose_version "{version}". '
            f"Must match v<MAJOR>.<MINOR>.<PATCH> (e.g. v2.29.7)."
        )
    arch = "aarch64" if base_os in arm_oses else "x86_64"
    return bool(enable_monitoring) and "alinux" in base_os, arch


_RESULTS_BUCKET_PREFIX = "parallelclustermaker-results"
_S3_BUCKET_NAME_MAX = 63  # S3's own limit on bucket names


def _derive_results_bucket(*, aws_account_id, region):
    """Return the name of the long-lived HPC benchmark results bucket.

    This bucket is deliberately NOT the per-build bucket.  s3_bucketname is
    "parallelclustermaker-<cluster_serial_number>" and the serial carries a
    timestamp, so every build gets its own bucket -- which made the documented
    "rebuilds of the same cluster name accumulate rather than overwrite"
    impossible, and made teardown's default (delete_s3_bucketname=true) sync
    results to a bucket it then deleted with force=true.  Both tasks succeeded,
    so nothing was reported as orphaned and teardown printed "has been deleted".

    Keying on account+region rather than on the cluster gives one bucket that
    outlives every build, so results really do accumulate under
    hpc-benchmark-results/<cluster_name>/<cluster_serial_number>/.  Region is in
    the name because S3 bucket names are global while buckets are regional: a
    single name would collide across regions in the same account.

    Nothing deletes this bucket -- not teardown, not the create-side rescue.
    That is the point, and it is the one bucket in the toolkit the operator is
    expected to prune by hand.
    """
    name = f"{_RESULTS_BUCKET_PREFIX}-{aws_account_id}-{region}"
    if len(name) > _S3_BUCKET_NAME_MAX:
        sys.exit(
            f"*** ERROR ***\n"
            f'  Derived results bucket name "{name}" is {len(name)} characters '
            f"(S3 limit: {_S3_BUCKET_NAME_MAX}).\n"
            f"  aws_account_id={aws_account_id}, region={region}"
        )
    return name


def _select_cw_log_group(cluster_name, group_names):
    """Pick a cluster's CloudWatch log group from a describe_log_groups listing.

    PCluster suffixes the group with the stack's creation timestamp --
    "/aws/parallelcluster/<stack>-<YYYYmmddHHMM>" (cluster_stack.py) -- so the
    name cannot be constructed from the cluster name alone. Rebuilds leave older
    groups behind, so the highest timestamp wins. Requires an exact
    "<cluster_name>-<12 digits>" match: a prefix query for "osiris" also returns
    "osiris-test-...". Returns None when nothing matches.
    """
    prefix = f"/aws/parallelcluster/{cluster_name}-"
    matched = [
        name
        for name in group_names
        if name.startswith(prefix) and re.fullmatch(r"\d{12}", name[len(prefix) :])
    ]
    return max(matched) if matched else None


_SAFE_STR_RE = re.compile(r"[^\x20-\x7e]")  # strip non-printable / control chars


def _safe(s):
    """Strip ANSI and control characters from a string before printing."""
    return _SAFE_STR_RE.sub("", s)


def _read_cluster_record(cluster_name, repo_root):
    """Read cluster metadata from src/vars_files/<cluster_name>.yml.

    Returns a dict, or None if the vars file is missing or unparseable.
    Defense-in-depth path check protects callers that bypass _enumerate_clusters.
    """
    cluster_dir = os.path.realpath(
        os.path.join(repo_root, "active_clusters", cluster_name)
    )
    active_root = os.path.realpath(os.path.join(repo_root, "active_clusters"))
    if not cluster_dir.startswith(active_root + os.sep):
        return None
    if not os.path.isdir(cluster_dir):
        return None

    vars_path = os.path.join(repo_root, "src", "vars_files", cluster_name + ".yml")
    try:
        with open(vars_path) as fh:
            data = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None

    # Every string leaves here sanitized. The vars file is operator-authored, but
    # a hand-edit or a corrupted write can embed a newline or ANSI escape, which
    # then breaks column alignment in list_pcluster.py and injects control
    # characters into its -J JSON output. Doing it at the single read point covers
    # every consumer instead of relying on each tool to remember.
    def _str(key, default=""):
        v = data.get(key, default)
        return _safe(str(v)) if v is not None else default

    def _int(key, default=0):
        try:
            return int(data.get(key, default))
        except (TypeError, ValueError):
            return default

    def _strlist(key):
        v = data.get(key)
        if isinstance(v, list):
            return [_safe(str(x)) for x in v if x is not None]
        if isinstance(v, str) and v.strip():
            return [_safe(x.strip()) for x in v.split(",") if x.strip()]
        return []

    return {
        "cluster_name":           _str("cluster_name", cluster_name),
        "cluster_owner":          _str("cluster_owner"),
        "serial":                 _str("cluster_serial_number"),
        "region":                 _str("region"),
        "headnode_instance_type": _str("headnode_instance_type"),
        "cpu_instance_types":     _strlist("cpu_instance_types"),
        "gpu_instance_types":     _strlist("gpu_instance_types"),
        "enable_cpu_queue":       _str("enable_cpu_queue", "false"),
        "enable_gpu_queue":       _str("enable_gpu_queue", "false"),
        "initial_cpu_queue_size": _int("initial_cpu_queue_size"),
        "max_cpu_queue_size":     _int("max_cpu_queue_size"),
        "initial_gpu_queue_size": _int("initial_gpu_queue_size"),
        "max_gpu_queue_size":     _int("max_gpu_queue_size"),
        "cluster_type":           _str("cluster_type", "ondemand"),
        "deployment_date":        _str("DEPLOYMENT_DATE"),
        "ssh_keypair":            _str("ssh_keypair"),
        "ec2_user":               _str("ec2_user", "ubuntu"),
        "s3_bucketname":          _str("s3_bucketname"),
        "enable_monitoring":      _str("enable_monitoring", "false"),
    }


# ---------------------------------------------------------------------------
# Fleet stop/start helpers (shared by stop_pcluster.py and start_pcluster.py)
# ---------------------------------------------------------------------------

_REGION_RE = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d+$")
_FLEET_POLL_INTERVAL = 30
_FLEET_POLL_TIMEOUT = 90  # 90 × 30 s = 45 min


def _validate_region(region):
    if not _REGION_RE.match(region):
        sys.exit(
            f"ERROR: region {region!r} does not look like a valid AWS region "
            f"(expected e.g. us-east-1)"
        )


def _clamp_int(value, low, high, label):
    """Clamp value into [low, high], printing a warning when it was adjusted.

    argparse type=int accepts negatives and arbitrarily large values, which
    silently produce absurd behavior downstream (a negative --hours yields a
    future sacct start time and reports zero failures; a negative --timeout
    makes ssh ConnectTimeout reject every connection).
    """
    if value < low:
        print(f"*** WARNING *** {label}={value} is below the minimum; using {low}.")
        return low
    if value > high:
        print(f"*** WARNING *** {label}={value} exceeds the maximum; using {high}.")
        return high
    return value


def _fleet_ts():
    return DateTime.now().strftime("%H:%M:%S")


def _run_pcluster_cmd(subcmd_args, pcluster_bin):
    """Run pcluster <subcmd_args> and return parsed JSON.

    subcmd_args must be a list of pre-validated strings; never built from
    raw user input without prior _validate_cluster_name / _validate_region.
    """
    cmd = [pcluster_bin] + subcmd_args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        raise SystemExit(f"ERROR: pcluster binary not found: {pcluster_bin}")
    except subprocess.TimeoutExpired:
        raise SystemExit("ERROR: pcluster command timed out after 120 s")
    if result.returncode != 0:
        raise SystemExit(
            f"ERROR: pcluster exited {result.returncode}:\n"
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise SystemExit(
            f"ERROR: unexpected pcluster output:\n{result.stdout.strip()}"
        )


def _get_fleet_status(cluster_name, region, pcluster_bin):
    """Return the current computeFleetStatus string for cluster_name."""
    data = _run_pcluster_cmd(
        ["describe-cluster", "--cluster-name", cluster_name, "--region", region],
        pcluster_bin,
    )
    return data.get("computeFleetStatus", "UNKNOWN")


# Terminal states that mean the requested action has already happened, and
# in-progress states that mean AWS is already moving toward it. PCluster's
# ComputeFleetStatus enum also emits the transitional STOPPING / STARTING
# values; treating those as "not yet handled" made the scripts re-issue an
# update-compute-fleet call against a fleet that was already mid-transition.
_FLEET_DONE_STATES = {
    "stop": ("STOPPED", "DISABLED"),
    "start": ("RUNNING", "ENABLED"),
}
_FLEET_PENDING_STATES = {
    "stop": ("STOP_REQUESTED", "STOPPING"),
    "start": ("START_REQUESTED", "STARTING"),
}


def _fleet_action_plan(status, action):
    """Decide what stop_pcluster/start_pcluster should do for a fleet status.

    Returns "abort", "done", "wait", or "request".
    """
    if action not in _FLEET_DONE_STATES:
        raise ValueError(f"unknown fleet action: {action!r}")
    if status == "PROTECTED":
        return "abort"
    if status in _FLEET_DONE_STATES[action]:
        return "done"
    if status in _FLEET_PENDING_STATES[action]:
        return "wait"
    return "request"


def _poll_fleet(cluster_name, region, target, label, pcluster_bin):
    """Poll describe-cluster until computeFleetStatus == target.

    Raises SystemExit on PROTECTED state, timeout, or KeyboardInterrupt.
    """
    # Ctrl-C is caught around the whole loop, not just time.sleep(). The
    # describe-cluster subprocess and its JSON parse take real wall-clock time,
    # and an interrupt landing there escaped as a raw traceback with no hint
    # that the fleet operation was still running in AWS.
    try:
        for i in range(_FLEET_POLL_TIMEOUT):
            status = _get_fleet_status(cluster_name, region, pcluster_bin)
            print(f"  [{_fleet_ts()}] computeFleetStatus: {status}")
            if status == target:
                return
            if status == "PROTECTED":
                raise SystemExit(
                    "ERROR: compute fleet is in PROTECTED state — investigate node "
                    "failures before retrying.\n"
                    f"  pcluster describe-cluster --cluster-name {cluster_name} --region {region}"
                )
            if i == _FLEET_POLL_TIMEOUT - 1:
                raise SystemExit(
                    f"ERROR: timed out after {_FLEET_POLL_TIMEOUT * _FLEET_POLL_INTERVAL // 60} min "
                    f"waiting for {label}.\n"
                    f"  pcluster describe-cluster --cluster-name {cluster_name} --region {region}"
                )
            time.sleep(_FLEET_POLL_INTERVAL)
    except KeyboardInterrupt:
        print(
            f"\nInterrupted. Fleet operation still running in AWS.\n"
            f"  pcluster describe-cluster --cluster-name {cluster_name} --region {region}"
        )
        raise SystemExit(1)
