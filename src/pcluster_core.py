"""
Pure-Python utility functions extracted from make_pcluster.py, kill_pcluster.py,
and access_cluster.py.

All functions are importable without AWS credentials and without the
venv guard that the main scripts enforce at import time.
"""

import contextlib
import copy
import glob
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import yaml
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, fields as _dc_fields
from datetime import datetime as DateTime, timedelta, timezone
from io import StringIO

import boto3
from botocore.exceptions import BotoCoreError, NoCredentialsError
from jinja2 import Environment, FileSystemLoader as _FSLoader, StrictUndefined
from ruamel.yaml import YAML

try:
    from botocore.exceptions import ClientError as _ClientError
except ImportError:
    _ClientError = Exception

# No ImportError fallback here, unlike _ClientError above: aws-parallelcluster
# is a hard, always-installed dependency of this repo (already invoked via
# subprocess everywhere else in this file), and a fallback that widened these
# to bare Exception would make "cluster not found" match every unrelated
# failure -- the opposite of _ClientError's fallback, which only weakens an
# attribute check that's already downstream of a real botocore exception.
from pcluster.api.errors import BadRequestException, NotFoundException


class PClusterMakerError(Exception):
    """Raised by core functions instead of sys.exit(). Message text matches
    the CLI's existing sys.exit() output exactly, so a CLI shim's
    `except PClusterMakerError as e: sys.exit(str(e))` preserves today's
    behavior unchanged."""


@dataclass(frozen=True)
class ClusterRecord:
    """Typed shape of what _read_cluster_record returns. Field set matches
    that function's dict exactly (verified 2026-08-20, not assumed) -- add a
    field here only when a migrated script actually needs it, mirroring what
    _read_cluster_record already projects from vars_file.j2."""

    cluster_name: str
    cluster_owner: str
    serial: str
    region: str
    headnode_instance_type: str
    enable_loginnode: str
    loginnode_instance_type: str
    loginnode_count: int
    cpu_instance_types: list
    gpu_instance_types: list
    enable_cpu_queue: str
    enable_gpu_queue: str
    initial_cpu_queue_size: int
    max_cpu_queue_size: int
    initial_gpu_queue_size: int
    max_gpu_queue_size: int
    cluster_type: str
    deployment_date: str
    ssh_keypair: str
    ec2_keypair: str
    ec2_user: str
    s3_bucketname: str
    enable_monitoring: str

    @classmethod
    def from_dict(cls, rec):
        """Build from the dict _read_cluster_record returns."""
        return cls(**{f: rec[f] for f in cls.__dataclass_fields__})

    @classmethod
    def unknown(cls, cluster_name):
        """Placeholder for a cluster whose vars file is missing or
        unparseable -- cluster_owner/region read "unknown", matching every
        script's existing `if rec else "unknown"` fallback."""
        defaults = {
            "cluster_name": cluster_name,
            "cluster_owner": "unknown",
            "serial": "",
            "region": "unknown",
            "headnode_instance_type": "",
            "enable_loginnode": "false",
            "loginnode_instance_type": "",
            "loginnode_count": 0,
            "cpu_instance_types": [],
            "gpu_instance_types": [],
            "enable_cpu_queue": "false",
            "enable_gpu_queue": "false",
            "initial_cpu_queue_size": 0,
            "max_cpu_queue_size": 0,
            "initial_gpu_queue_size": 0,
            "max_gpu_queue_size": 0,
            "cluster_type": "",
            "deployment_date": "",
            "ssh_keypair": "",
            "ec2_keypair": "",
            "ec2_user": "",
            "s3_bucketname": "",
            "enable_monitoring": "false",
        }
        return cls(**defaults)


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

_VALID_EC2_USERS = set(_EC2_USERS.values())


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


def _resolve_access_node_type(rec, cluster_name, *, login_node_requested, head_node_requested):
    """Return (node_type, error_message) for access_cluster.py's -L/-H/default selection.

    error_message is a complete "ERROR: ..." string ready to pass to sys.exit,
    or None on success. rec is a cluster record dict from _read_cluster_record
    (possibly {} for an unreadable/missing vars file).

    loginnode_count=0 is a schema-valid "defined but empty pool" state (AWS's
    own LoginNodesPoolSchema.count floors at 0) and gets a distinct error from
    enable_loginnode=false -- both leave zero login-node instances running, but
    "rebuild with --enable_loginnode=true" is the wrong instruction when it is
    already true.
    """
    loginnode_enabled = rec.get("enable_loginnode") == "true"
    loginnode_count = rec.get("loginnode_count") or 0
    loginnode_available = loginnode_enabled and loginnode_count > 0

    if login_node_requested and not loginnode_enabled:
        return None, (
            f"ERROR: no login node is configured for cluster '{cluster_name}'.\n"
            f"  Rebuild with --enable_loginnode=true to use -L."
        )
    if login_node_requested and not loginnode_available:
        return None, (
            f"ERROR: cluster '{cluster_name}' has --loginnode_count=0 -- the login "
            f"node pool is defined but empty.\n"
            f"  Rebuild with --loginnode_count set to at least 1 to use -L."
        )
    if login_node_requested:
        return "LoginNode", None
    if head_node_requested:
        return "HeadNode", None
    return ("LoginNode" if loginnode_available else "HeadNode"), None


# ---------------------------------------------------------------------------
# Workstream 4, async job handling, first slice: an S3-backed distributed
# cluster lock, replacing the local mkdir lock this codebase used to acquire
# via _acquire_cluster_lock/_release_cluster_lock (now deleted -- wired into
# both core_create_cluster and core_delete_cluster the same round it was
# built, and nothing else referenced the local lock once that wiring
# landed, so it was removed outright rather than left as dead code).
#
# The local mkdir lock was explicitly documented as local-machine-scoped
# only -- fine while the only caller was a CLI operator on their own
# laptop, but that stops being true the moment a remote MCP server exists
# at all: a local file lock on the server can't see a concurrent CLI
# invocation on the operator's own machine, and vice versa, regardless of
# whether the server is EC2/Fargate/Lambda. This isn't a Lambda-specific
# gap -- it's a gap in every remote hosting option, since none of them
# share a filesystem with the operator's laptop.
#
# botocore==1.43.65 (this repo's pinned version) is well past S3's August
# 2024 conditional-write release: PutObject supports IfNoneMatch='*', a
# genuine atomic create-if-absent primitive with the same guarantee mkdir
# gives locally, visible to every caller regardless of machine. Confirmed
# directly from botocore's own S3 API model (PutObjectRequest's IfMatch/
# IfNoneMatch documentation, read out of
# botocore/data/s3/2006-03-01/service-2.json.gz rather than assumed): a
# losing conditional write returns HTTP 412 ("Precondition Failed"); a
# genuinely concurrent write racing at the same instant can instead return
# HTTP 409 ("ConditionalRequestConflict"), which S3's own docs say to treat
# identically to a 412 -- fetch the current ETag and retry. Both are
# recognized here.
#
# LIVE-VERIFIED 2026-08-21 against real S3 (us-east-2), 8 concurrent
# writers each with its own boto3 client, released by a threading.Barrier:
# exactly one winner in the acquire race (IfNoneMatch='*'), exactly one in
# the reclaim race (IfMatch=<etag>), and exactly one through
# s3_acquire_cluster_lock itself. The atomicity claim holds.
#
# That run also settled the 409 question rather than leaving it defensive:
# the reclaim race produced BOTH PreconditionFailed (412) AND
# ConditionalRequestConflict (409) among its losers, in the same 8-way
# race. Handling only 412 would let a reclaim under real contention escape
# _is_conditional_write_rejection and crash a build or teardown with an
# unhandled ClientError -- and no fake-based test could ever catch it,
# since a synchronous fake cannot produce a same-instant conflict. Do not
# "simplify" that function to the 412 case alone.
# ---------------------------------------------------------------------------

_LOCKS_BUCKET_PREFIX = "parallelclustermaker-locks"

# Statuses (or the cluster being altogether absent) that make a lock's
# recorded operation safe to reclaim once it is also stale by age. Covers
# both create-side and delete-side terminal states plus manage_pcluster_
# queue's update_cluster phase -- the lock module itself has no opinion on
# which kind of operation it is guarding, by design (see
# s3_acquire_cluster_lock's own docstring).
_LOCK_RECLAIMABLE_STATUSES = frozenset({
    "CREATE_COMPLETE", "CREATE_FAILED",
    "DELETE_COMPLETE", "DELETE_FAILED",
    "UPDATE_COMPLETE", "UPDATE_FAILED",
})

_LOCK_STALENESS_CEILING_SECONDS = 7200  # 2x the 3600s default create/delete wait ceiling


class ClusterLockError(Exception):
    """Raised when s3_acquire_cluster_lock cannot obtain the lock -- either
    another operation genuinely holds it, or a reclaim attempt lost a race
    to a second caller reclaiming the same stale lock at the same time."""


def _derive_locks_bucket(*, aws_account_id, region):
    """Return the name of the long-lived, account+region-scoped cluster lock
    bucket -- the same derivation shape as _derive_results_bucket above (one
    bucket outlives every build/teardown, keyed on account+region so it
    can't collide across regions in the same account), for exactly the same
    reason the lock needs a home that exists before the cluster's own
    s3_bucketname does: in create_pcluster.yml the SNS topic is created
    before the S3 bucket, and the lock must be held before the very first
    AWS mutation of any kind."""
    name = f"{_LOCKS_BUCKET_PREFIX}-{aws_account_id}-{region}"
    if len(name) > _S3_BUCKET_NAME_MAX:
        sys.exit(
            f"*** ERROR ***\n"
            f'  Derived locks bucket name "{name}" is {len(name)} characters '
            f"(S3 limit: {_S3_BUCKET_NAME_MAX}).\n"
            f"  aws_account_id={aws_account_id}, region={region}"
        )
    return name


def _create_locks_bucket(s3, *, locks_bucketname, region):
    """Idempotently create the locks bucket -- unconditionally, unlike the
    results bucket (which is gated on enable_hpc_benchmarks): every build or
    teardown needs the lock before anything else happens, regardless of
    which features are enabled."""
    kwargs = {"Bucket": locks_bucketname}
    if region != "us-east-1":
        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
    try:
        s3.create_bucket(**kwargs)
    except _ClientError as e:
        if e.response["Error"]["Code"] != "BucketAlreadyOwnedByYou":
            raise
    s3.put_public_access_block(
        Bucket=locks_bucketname,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True, "IgnorePublicAcls": True,
            "BlockPublicPolicy": True, "RestrictPublicBuckets": True,
        },
    )


def _lock_key(cluster_name):
    return f"locks/{cluster_name}.lock"


def _lock_owner_body(*, command):
    """JSON, not the local lock's plain-text "owner" file -- the reclaim
    path needs to read the recorded command back out programmatically to
    decide staleness, not just print it for a human."""
    return json.dumps({
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "command": command,
        "started": DateTime.now(timezone.utc).isoformat(),
    }).encode("utf-8")


def _is_conditional_write_rejection(e):
    """True for both documented failure shapes of a losing conditional
    PutObject -- a clean loss (412 Precondition Failed) and a same-instant
    race that S3 reports as a conflict instead (409 ConditionalRequestConflict,
    which S3's own IfMatch/IfNoneMatch documentation says to treat
    identically: fetch the current ETag and retry). Checked by both the
    error Code and the HTTP status, since the exact Code casing has not been
    confirmed live against a real bucket yet."""
    code = e.response.get("Error", {}).get("Code", "")
    status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return code in ("PreconditionFailed", "ConditionalRequestConflict") or status in (412, 409)


def s3_acquire_cluster_lock(
    s3, *, locks_bucketname, cluster_name, command,
    describe_fn=None, region=None,
    staleness_ceiling_seconds=_LOCK_STALENESS_CEILING_SECONDS, now_fn=None,
):
    """Atomically acquire a per-cluster lock in S3, replacing the local
    mkdir lock's role but visible to every caller regardless of machine.

    describe_fn, when supplied, is called as describe_fn(cluster_name=...,
    region=...) -- the same calling convention pc.describe_cluster already
    uses elsewhere in this file -- to check whether a stale-looking lock's
    cluster has actually reached a terminal state before reclaiming it.
    Deliberately generic across create/delete/update: this module has no
    opinion on which kind of operation is being guarded, so a caller that
    cannot usefully answer "is this terminal" (e.g. no AWS credentials yet)
    may omit describe_fn entirely, in which case a stale lock is reported
    but never auto-reclaimed -- failing safe rather than guessing.

    Raises ClusterLockError, naming the current owner, when the lock is
    genuinely held by a live operation, or when a reclaim attempt loses a
    race to a second caller reclaiming the same lock at the same moment (the
    correct response then is to re-run this whole function from scratch, not
    to treat the loss as a hard failure -- left to the caller, since retry
    policy differs between the CLI's fail-fast preference and a future MCP
    caller that might want to poll).
    """
    now_fn = now_fn or (lambda: DateTime.now(timezone.utc))
    key = _lock_key(cluster_name)
    body = _lock_owner_body(command=command)
    try:
        s3.put_object(Bucket=locks_bucketname, Key=key, Body=body, IfNoneMatch="*")
        return key
    except _ClientError as e:
        if not _is_conditional_write_rejection(e):
            raise

    existing = s3.get_object(Bucket=locks_bucketname, Key=key)
    etag = existing["ETag"]
    last_modified = existing["LastModified"]
    try:
        owner_info = json.loads(existing["Body"].read())
    except (ValueError, KeyError):
        owner_info = {}
    age_seconds = (now_fn() - last_modified).total_seconds()

    if describe_fn is not None and age_seconds > staleness_ceiling_seconds:
        try:
            status = describe_fn(cluster_name=cluster_name, region=region).get("clusterStatus", "")
        except NotFoundException:
            status = "GONE"
        except Exception:
            status = ""
        if status == "GONE" or status in _LOCK_RECLAIMABLE_STATUSES:
            try:
                s3.put_object(Bucket=locks_bucketname, Key=key, Body=body, IfMatch=etag)
                print(
                    f"*** WARNING ***  Reclaimed a stale lock for cluster "
                    f"{cluster_name!r} -- previous owner: {owner_info}, "
                    f"age: {int(age_seconds)}s, cluster status: {status}"
                )
                return key
            except _ClientError as e2:
                if not _is_conditional_write_rejection(e2):
                    raise
                raise ClusterLockError(
                    f"Lock for cluster {cluster_name!r} looked stale (age "
                    f"{int(age_seconds)}s, status {status}) but another "
                    f"operation reclaimed it first -- re-run to retry."
                )

    raise ClusterLockError(
        f"Another operation is already running against cluster {cluster_name!r}: "
        f"{owner_info}\n"
        f"  If nothing is actually running, a previous one was killed before "
        f"releasing it. It will be auto-reclaimed once it is older than "
        f"{staleness_ceiling_seconds}s and the cluster has reached a terminal "
        f"state, or can be removed by hand: "
        f"aws s3 rm s3://{locks_bucketname}/{key}"
    )


def s3_release_cluster_lock(s3, *, locks_bucketname, cluster_name):
    """Release a lock acquired by s3_acquire_cluster_lock. Safe to call even
    if the lock is already gone (e.g. reclaimed out from under a caller that
    held it past the staleness ceiling) -- DeleteObject on a missing key is
    not an error in S3. Also tolerates any other ClientError (permission
    denial, transient network failure): this runs as a best-effort cleanup
    step, often the last statement before a caller returns or exits, and a
    raised exception here would mask whatever the caller was actually
    reporting. Unlike the old local lock, a release that never happens is
    not permanently stuck -- the staleness/reclaim path in
    s3_acquire_cluster_lock recovers it automatically for the next caller."""
    with contextlib.suppress(_ClientError):
        s3.delete_object(Bucket=locks_bucketname, Key=_lock_key(cluster_name))


def _acquire_distributed_cluster_lock(
    s3, *, locks_bucketname, region, cluster_name, command, describe_fn=None,
):
    """CLI-facing composing entry point: ensures the locks bucket exists,
    then acquires the lock, converting ClusterLockError into a
    sys.exit(...) -- the same shape the local mkdir lock this replaced
    always used for a held lock. Every existing CLI-behavior test (the
    "already running" substring, SystemExit rather than an uncaught
    exception propagating out of core_create_cluster/core_delete_cluster)
    depends on this exact shape, not merely on the lock being held -- so
    the conversion happens here, once, rather than being reimplemented at
    each of the two call sites."""
    _create_locks_bucket(s3, locks_bucketname=locks_bucketname, region=region)
    try:
        return s3_acquire_cluster_lock(
            s3, locks_bucketname=locks_bucketname, cluster_name=cluster_name,
            command=command, describe_fn=describe_fn, region=region,
        )
    except ClusterLockError as e:
        sys.exit(str(e))


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
    mcp_user_pool_id="",
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
            # Workstream 5's MCP Lambda policies only. Defaulted rather than
            # required so every existing cluster-policy caller is unchanged;
            # _setup_mcp_infra passes the real pool id. Kept here rather than
            # in a separate renderer so the substitution set cannot drift
            # between the cluster policies and the MCP ones -- a placeholder
            # this function does not know about renders as a literal
            # "<MCP_USER_POOL_ID>" straight into a live IAM ARN.
            .replace("<MCP_USER_POOL_ID>", mcp_user_pool_id)
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


# ---------------------------------------------------------------------------
# make_pcluster.py's hardcoded defaults, at module scope in the core layer.
#
# Moved here from inside make_pcluster.py's main() (Workstream 5): as a
# local variable it was unreachable by anything but that one function, so
# an MCP create_cluster wrapper could not build a MakeClusterParams
# without duplicating ~80 defaults into a second source that would drift
# from this one. MakeClusterParams has 84 fields and none of them carry a
# default, so this dict is the only thing that makes constructing one
# tractable from anywhere.
#
# make_pcluster.py still owns the argparse surface and still does the
# CLI > defaults-file > hardcoded precedence via _resolve_cli_value; this
# is only the bottom layer of that precedence, relocated so more than one
# caller can reach it. Two AST tests
# (test_resolve_defaults.py::test_argparse_help_defaults_match_hardcoded_defaults
# and test_make_pcluster.py's placeholder sweep) read it from here now.
# ---------------------------------------------------------------------------

MAKE_CLUSTER_DEFAULTS = {
    "ansible_verbosity": "",
    "base_os": "ubuntu2404",
    "cluster_owner_department": "hpc",
    "cluster_type": "spot",
    "compute_instance_type": "",
    "compute_root_volume_size": 250,
    "compute_root_volume_type": "gp3",
    "compute_root_volume_iops": 3000,
    "compute_root_volume_throughput": 125,
    "gpu_instance_type": "",
    "gpu_root_volume_size": 250,
    "gpu_root_volume_type": "gp3",
    "gpu_root_volume_iops": 3000,
    "gpu_root_volume_throughput": 125,
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
    "enable_loginnode": "false",
    "loginnode_subnet_id": "",
    "loginnode_count": 1,
    "head_node_bootstrap_timeout": 2100,
    "enable_fsx": "false",
    "enable_fsx_hydration": "false",
    "enable_hpc_benchmarks": "false",
    "enable_monitoring": "false",
    # These must be real digests, not placeholders. They are the only source
    # on the no-defaults-file path, and a stale <cluster>_defaults.yml that
    # predates a new key falls through to here -- which is how
    # "sha256:REPLACE_WITH_ACTUAL_SHA256" reached Ansible's get_url and
    # failed a build 18 tasks in. Keep them equal to pcluster_defaults.yml;
    # test_download_checksum_defaults_are_real_digests pins both properties.
    "monitoring_version": "v2.6",
    "monitoring_version_checksum": "sha256:4afa56a59228c1d8f4e405d07a2291f31853842128e6f7a0e52e1e2c1e262d55",
    "docker_compose_version": "v2.29.7",
    "docker_compose_checksum_x86_64": "sha256:383ce6698cd5d5bbf958d2c8489ed75094e34a77d340404d9f32c4ae9e12baf0",
    "docker_compose_checksum_aarch64": "sha256:6e9fbd5daa20dca5d7d89145081ae8155d68ef2928b497d9f85b54fe0f9dbb2c",
    "external_nfs_server": "",
    "fsx_chunk_size": 1024,
    "fsx_s3_export_bucket": "UNDEFINED",
    "fsx_s3_export_path": "export",
    "fsx_s3_import_bucket": "UNDEFINED",
    "fsx_s3_import_path": "import",
    "fsx_size": 1200,
    "hyperthreading": "true",
    "initial_cpu_queue_size": 2,
    "initial_gpu_queue_size": 2,
    "maintain_cpu_initial_size": "false",
    "maintain_gpu_initial_size": "false",
    "headnode_root_volume_size": 100,
    "headnode_root_volume_type": "gp3",
    "headnode_root_volume_iops": 3000,
    "headnode_root_volume_throughput": 125,
    "max_cpu_queue_size": 8,
    "max_gpu_queue_size": 8,
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
    "gpu_az": "",
    "gpu_subnet_ids": "",
    "use_private_gpu_subnet": "false",
}


def _arm_oses():
    from pcluster_aux_data import ARM_OSES

    return ARM_OSES


def _validate_at_least_one_queue(compute_instance_type, gpu_instance_type):
    """Reject a cluster with no compute queue at all.

    `enable_cpu_queue`/`enable_gpu_queue` are derived purely from whether
    their instance-type string is non-empty, and both default to "" -- so
    a cluster built from defaults alone has a head node and nothing to run
    jobs on. config.pcluster.j2 then renders `SlurmQueues: None`, which
    PCluster's own schema rejects.

    Caught here because of *where* it would otherwise be caught: the IAM
    policies, role, S3 bucket, keypair and Secrets Manager secret are all
    created before `pc.create_cluster` is called, so the failure is
    late-stage -- the create-side handler preserves that state and tells
    the operator to run kill_pcluster.py. A full provisioning cycle plus a
    manual teardown, for an input error visible before anything is spent.

    The invariant is not new: core_remove_queue already refuses to remove
    the last queue ("A cluster must have at least one queue"). Creation
    simply never enforced the same thing.
    """
    if compute_instance_type or gpu_instance_type:
        return
    raise PClusterMakerError(
        "this cluster would have no compute queue: both compute_instance_type "
        "and gpu_instance_type are empty, so neither a CPU nor a GPU queue is "
        "created and there is nothing to run jobs on. PCluster rejects the "
        "resulting config, but only after the IAM role, S3 bucket, keypair and "
        "SSH secret have been created. Set compute_instance_type (e.g. "
        "\"c5.xlarge\") and/or gpu_instance_type."
    )


def _validate_override_types(overrides):
    """Reject an override whose type does not match its default's.

    Guards the failure mode that motivated it, found by testing rather
    than by reading: this codebase carries booleans as the *strings*
    "true"/"false", so `{"enable_fsx": True}` -- the most natural thing to
    emit into an untyped dict -- passes a key check, is stored as the
    Python bool, and then fails every downstream `== "true"` comparison.
    The cluster comes back without FSx, and nothing errors anywhere. The
    bootstrap-timeout derivation does not fire either, so even the symptom
    is muted.

    A typed MCP tool parameter gets this free from its JSON schema; an
    `overrides` dict has no schema at all, which is exactly why it needs
    this. Types are compared exactly rather than with isinstance, because
    bool is a subclass of int in Python and `{"loginnode_count": True}`
    would otherwise sail through as the integer 1.

    Rejects rather than coerces. Coercion has to guess intent -- is the
    int 1 meant as "true", or as a count? -- and a clear error the caller
    can act on beats a silent guess that might be wrong.
    """
    problems = []
    for key, value in sorted(overrides.items()):
        if key not in MAKE_CLUSTER_DEFAULTS:
            continue  # a derived/required field; no default to compare against
        expected = MAKE_CLUSTER_DEFAULTS[key]
        if type(value) is type(expected):
            continue
        if isinstance(expected, str) and expected in ("true", "false"):
            hint = ' (this parameter takes the string "true" or "false")'
        else:
            hint = f" (expected {type(expected).__name__})"
        problems.append(f"{key}={value!r}{hint}")
    if problems:
        raise PClusterMakerError(
            "cluster parameter(s) have the wrong type: " + "; ".join(problems)
            + ". Rejected rather than converted, because a wrong-typed value is "
            "accepted silently everywhere downstream and produces a cluster that "
            "differs from what was asked for."
        )


def build_make_cluster_params(
    *, cluster_name, cluster_owner, cluster_owner_email, az,
    headnode_instance_type, overrides=None,
):
    """Build a MakeClusterParams from defaults plus a small override set.

    Exists so a caller that is not make_pcluster.py's argparse -- the MCP
    create_cluster tool, principally -- can construct one without
    reimplementing main()'s resolution. MakeClusterParams has 84 fields and
    none carry a default; MAKE_CLUSTER_DEFAULTS supplies 70, four are the
    required inputs above, and the remaining ten are derived here using the
    same core helpers make_pcluster.py itself calls.

    `overrides` is deliberately validated against the known field set
    rather than passed through: a typo'd key would otherwise be silently
    ignored and the cluster built with the default, which is the worst
    outcome -- an operator who asked for FSx and did not get it, with no
    error anywhere.

    This does NOT decide which parameters a remote caller may set. That is
    the tool wrapper's job and a policy question; this function will build
    whatever it is asked for, exactly like the CLI would.
    """
    fields = {f.name for f in _dc_fields(MakeClusterParams)}
    # Accepted override keys are the dataclass fields PLUS the input keys
    # MAKE_CLUSTER_DEFAULTS carries that get consumed into a derived field
    # rather than surviving under their own name -- compute_az becomes
    # compute_az_list, head_node_bootstrap_timeout splits into the
    # configured and derived pair, and so on. Validating against the
    # fields alone rejected "compute_az", which is exactly the knob a
    # caller reaches for.
    accepted = fields | set(MAKE_CLUSTER_DEFAULTS)
    overrides = dict(overrides or {})
    unknown = sorted(set(overrides) - accepted)
    if unknown:
        raise PClusterMakerError(
            f"unknown cluster parameter(s): {', '.join(unknown)}. "
            f"A silently-ignored typo would build a cluster that differs from "
            f"what was asked for."
        )
    _validate_override_types(overrides)

    values = dict(MAKE_CLUSTER_DEFAULTS)
    values.update(overrides)
    values.update({
        "cluster_name": cluster_name,
        "cluster_owner": cluster_owner,
        "cluster_owner_email": cluster_owner_email,
        "az": az,
    })

    _validate_at_least_one_queue(
        values.get("compute_instance_type", ""), values.get("gpu_instance_type", "")
    )

    base_os = values["base_os"]
    # Required, with no fallback, exactly as make_pcluster.py's argparse
    # has it (required=True). An earlier draft defaulted it to
    # _default_loginnode_instance_type(base_os) -- a plausible value from
    # entirely the wrong knob, which would have silently built head nodes
    # sized for a login node.
    values["headnode_instance_type"] = headnode_instance_type

    if not values.get("loginnode_instance_type"):
        values["loginnode_instance_type"] = _default_loginnode_instance_type(base_os)

    # Same derivations main() performs, via the same helpers -- not
    # reimplemented, so a change to any of them reaches both callers.
    values["compute_az_list"] = _derive_az_list(values.pop("compute_az", ""), fallback=[az])
    values["gpu_az_list"] = _derive_az_list(values.pop("gpu_az", ""), fallback=None)
    values["compute_subnet_ids_override"] = values.pop("compute_subnet_ids", "")
    values["gpu_subnet_ids_override"] = values.pop("gpu_subnet_ids", "")

    configured = values.pop("head_node_bootstrap_timeout", 2100)
    values["configured_head_node_bootstrap_timeout"] = configured
    values["head_node_bootstrap_timeout"] = _derive_head_node_bootstrap_timeout(
        configured=configured,
        enable_efs=values.get("enable_efs") == "true",
        enable_fsx=values.get("enable_fsx") == "true",
    )

    stage, arch = _derive_docker_compose_staging(
        base_os=base_os,
        arm_oses=_arm_oses(),
        enable_monitoring=values.get("enable_monitoring") == "true",
        version=values.get("docker_compose_version", ""),
    )
    values["stage_docker_compose"] = stage
    values["docker_compose_arch"] = arch
    values["docker_compose_checksum"] = values.get(f"docker_compose_checksum_{arch}", "")

    return MakeClusterParams(**{k: v for k, v in values.items() if k in fields})


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


# ---------------------------------------------------------------------------
# Workstream 5: IAM for the MCP remote transport's Lambda topology.
#
# ONE table drives creation, attachment, and teardown. _setup_iam next door
# carries three parallel suffix lists (_ALL_SUFFIXES, _suffixes, and a third
# inside _delete_managed_policies) that a test has to cross-assert against
# each other precisely because they can drift; this does not repeat that.
#
# Scope is IAM only -- roles, customer-managed policies, attachments. It
# deliberately does not create the Lambda functions, their API Gateway, or
# the Cognito pool: those need deployment artifacts (a zip, a container
# image) that do not exist at this layer, and _setup_iam's own precedent is
# role-and-policy setup, nothing else.
#
# Execution logging comes from the AWS-managed AWSLambdaBasicExecutionRole
# attached to every role here, which is why none of the nine policy
# documents grants itself logs:CreateLogStream/logs:PutLogEvents.
# ---------------------------------------------------------------------------

_MCP_BASIC_EXECUTION_ARN = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"

# tier -> (lambda function name, policy template basenames)
#
# The function names are not free-form: templates/MCPRouterLambda.json_src
# grants lambda:InvokeFunction on exactly these four handler ARNs, so a
# rename here that is not mirrored there produces a router that is denied
# at runtime. TestRouterPolicyStaysNearZero pins the same list from the
# policy side.
#
# Both stack-mutation tiers share MCPStackMutation + MCPStateAccessStackMutation:
# CloudFormation deletion needs permissions symmetric to what creation
# needed, so the "plain" tier's breadth is not meaningfully narrower than
# the Node.js one's. That split is a runtime/cold-start split, not a
# blast-radius one -- an accepted, explicit trade-off against a
# near-duplicate policy pair.
_MCP_LAMBDA_TIERS = {
    "router": (
        "pclustermaker-mcp-router",
        ["MCPRouterLambda.json_src"],
    ),
    "read-only": (
        "pclustermaker-mcp-read-only",
        ["MCPReadOnlyLambda.json_src", "MCPStateAccessReadOnly.json_src"],
    ),
    "fleet-toggle": (
        "pclustermaker-mcp-fleet-toggle",
        ["MCPFleetToggleLambda.json_src", "MCPStateAccessFleetToggle.json_src"],
    ),
    "stack-mutation": (
        "pclustermaker-mcp-stack-mutation",
        ["MCPStackMutation.json_src", "MCPStateAccessStackMutation.json_src"],
    ),
    "stack-mutation-node": (
        "pclustermaker-mcp-stack-mutation-node",
        ["MCPStackMutation.json_src", "MCPStateAccessStackMutation.json_src"],
    ),
    "register": (
        "pclustermaker-mcp-register",
        ["MCPRegisterLambda.json_src"],
    ),
    "authorizer": (
        "pclustermaker-mcp-authorizer",
        ["MCPAuthorizerLambda.json_src"],
    ),
}

_MCP_POLICY_NAME_PREFIX = "pclustermaker-mcp-policy"


def _mcp_role_name(tier):
    return f"pclustermaker-mcp-{tier}-role"


def _mcp_policy_name(template_basename):
    """One customer-managed policy per template, not per tier.

    MCPStackMutation is attached to two roles; creating it once per tier
    would collide on the second create_policy call, since IAM policy names
    are unique per account.
    """
    return f"{_MCP_POLICY_NAME_PREFIX}-{template_basename[: -len('.json_src')]}"


def _mcp_policy_templates():
    """Every distinct policy template the tier table references, in a
    stable order. Derived from the table rather than restated, so a policy
    added to a tier cannot be missed at creation or teardown."""
    seen = []
    for _fn, templates in _MCP_LAMBDA_TIERS.values():
        for t in templates:
            if t not in seen:
                seen.append(t)
    return seen


def _setup_mcp_infra(iam, *, aws_account_id, region, mcp_user_pool_id, templates_dir=None):
    """Create the MCP Lambda execution roles and their managed policies.

    Idempotent, like _setup_iam: an existing role or policy is reused
    rather than treated as an error, so a partially-completed run can be
    re-run. Returns {tier: role_name}.

    mcp_user_pool_id is required, not defaulted: it is substituted into the
    two Cognito policies' ARNs, and an empty value there renders a policy
    granting cognito-idp actions on a malformed resource -- which IAM
    accepts and which then silently fails at call time.
    """
    if not mcp_user_pool_id:
        raise PClusterMakerError(
            "mcp_user_pool_id is required: it is substituted into the "
            "MCPRegisterLambda/MCPAuthorizerLambda policy ARNs, and an empty "
            "value produces a policy that IAM accepts but that denies at call time."
        )
    if templates_dir is None:
        templates_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates"
        )

    trust = (
        '{"Version":"2012-10-17","Statement":[{"Effect":"Allow",'
        '"Principal":{"Service":["lambda.amazonaws.com"]},"Action":"sts:AssumeRole"}]}'
    )

    policy_arns = {}
    for basename in _mcp_policy_templates():
        rendered = _render_policy(
            os.path.join(templates_dir, basename),
            aws_account_id, region, "", "", "", "", "", "",
            mcp_user_pool_id,
        )
        name = _mcp_policy_name(basename)
        try:
            resp = iam.create_policy(PolicyName=name, PolicyDocument=rendered)
            policy_arns[basename] = resp["Policy"]["Arn"]
            print(f"  Created MCP policy: {name}")
        except _ClientError as e:
            if e.response["Error"]["Code"] != "EntityAlreadyExists":
                raise
            policy_arns[basename] = f"arn:aws:iam::{aws_account_id}:policy/{name}"
            print(f"  Reusing existing MCP policy: {name}")

    roles = {}
    for tier, (_fn, templates) in _MCP_LAMBDA_TIERS.items():
        role = _mcp_role_name(tier)
        try:
            iam.create_role(
                RoleName=role,
                AssumeRolePolicyDocument=trust,
                Description=f"ParallelClusterMaker MCP {tier} Lambda execution role",
            )
            print(f"  Created MCP role: {role}")
        except _ClientError as e:
            if e.response["Error"]["Code"] != "EntityAlreadyExists":
                raise
            print(f"  Reusing existing MCP role: {role}")
        for basename in templates:
            iam.attach_role_policy(RoleName=role, PolicyArn=policy_arns[basename])
        iam.attach_role_policy(RoleName=role, PolicyArn=_MCP_BASIC_EXECUTION_ARN)
        roles[tier] = role
    return roles


def _delete_mcp_infra(iam, *, aws_account_id, suppress=True):
    """Tear down everything _setup_mcp_infra created, driven by the same
    table so the two cannot disagree about what exists.

    Detach before delete: IAM refuses to delete a policy that is still
    attached, and refuses to delete a role that still has attachments.
    Tolerant by default -- one missing resource must not abandon the rest,
    matching the teardown discipline the delete-side playbook already
    follows."""
    def _try(fn, *a, **kw):
        try:
            fn(*a, **kw)
            return True
        except Exception:
            if not suppress:
                raise
            return False

    policy_arns = {
        b: f"arn:aws:iam::{aws_account_id}:policy/{_mcp_policy_name(b)}"
        for b in _mcp_policy_templates()
    }
    for tier, (_fn, templates) in _MCP_LAMBDA_TIERS.items():
        role = _mcp_role_name(tier)
        for basename in templates:
            _try(iam.detach_role_policy, RoleName=role, PolicyArn=policy_arns[basename])
        _try(iam.detach_role_policy, RoleName=role, PolicyArn=_MCP_BASIC_EXECUTION_ARN)
        _try(iam.delete_role, RoleName=role)
    for arn in policy_arns.values():
        _try(iam.delete_policy, PolicyArn=arn)


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


def _default_loginnode_instance_type(base_os):
    """Return the architecture-aware hardcoded fallback for loginnode_instance_type."""
    from pcluster_aux_data import ARM_OSES

    return "c8g.xlarge" if base_os in ARM_OSES else "c5.xlarge"


def _validate_network(
    *,
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
    enable_loginnode="false",
    loginnode_subnet_id="",
):
    """Return (vpc_id, headnode_subnet_id, compute_subnet_ids, gpu_subnet_ids, vpc_cidr, loginnode_subnet_id).

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

    def _resolve_single_subnet(explicit_id, label):
        if explicit_id:
            print(f"  Using explicit {label} subnet: {explicit_id}")
            return explicit_id
        print(f"  Auto-discovering {label} subnet in {az}...")
        return _discover_subnet(az)

    headnode_subnet_id = _resolve_single_subnet(headnode_subnet_id, "head node")

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

    if enable_loginnode == "true":
        loginnode_subnet_id = _resolve_single_subnet(loginnode_subnet_id, "login node")

    return (
        vpc_id,
        headnode_subnet_id,
        compute_subnet_ids,
        gpu_subnet_ids,
        vpc_cidr,
        loginnode_subnet_id,
    )


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
    loginnode_instance_type="", loginnode_count=0, enable_loginnode=False,
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

        if enable_loginnode and loginnode_instance_type and loginnode_count > 0:
            # pcluster_defaults.yml's own shipped example sets both instance
            # types to c8g.xlarge -- reuse the head node's lookup rather than
            # making an identical live Pricing API call a second time.
            if loginnode_instance_type == headnode_instance_type:
                ln_od, ln_err = hn_od, hn_err
            else:
                ln_od, ln_err = _get_od_price(pricing_client, loginnode_instance_type, region)
            _spot_note = "  (on-demand only)" if is_spot else ""
            if ln_od is not None:
                lines.append(
                    f"    Login node ({loginnode_instance_type} × {loginnode_count}):   "
                    f"${ln_od * loginnode_count:.3f}/hr{_spot_note}"
                )
            else:
                lines.append(
                    f"    Login node ({loginnode_instance_type} × {loginnode_count}):   {ln_err}"
                )

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


def _derive_az_list(raw, *, fallback):
    """Split a comma-separated AZ override into a list, or return fallback.

    Two callers with a deliberately different fallback, which is the whole
    reason this takes one rather than defaulting internally: the compute
    queue falls back to [<headnode az>] (a queue must live somewhere, and
    the head node's AZ is the sensible default), while the GPU queue falls
    back to None, meaning "no GPU AZ override" -- distinct from "an empty
    list of AZs", and read downstream as absence rather than emptiness.
    Collapsing them to one default would silently give a GPU-less cluster
    a GPU AZ list.

    Blank entries are dropped and surrounding whitespace stripped, so
    "us-east-1a, us-east-1b," parses the way an operator expects rather
    than yielding an empty-string AZ that fails much later.
    """
    if not raw:
        return fallback
    parsed = [a.strip() for a in raw.split(",") if a.strip()]
    return parsed if parsed else fallback


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
        "enable_loginnode":       _str("enable_loginnode", "false"),
        "loginnode_instance_type": _str("loginnode_instance_type"),
        "loginnode_count":        _int("loginnode_count"),
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
        "ec2_keypair":            _str("ec2_keypair"),
        "ec2_user":               _str("ec2_user", "ubuntu"),
        "s3_bucketname":          _str("s3_bucketname"),
        "enable_monitoring":      _str("enable_monitoring", "false"),
    }


# ---------------------------------------------------------------------------
# Cost reporting helpers (shared by cost_pcluster.py)
# ---------------------------------------------------------------------------


def _utc_today():
    return DateTime.now(timezone.utc).date()


def _date_range(days):
    """Return (start, end) ISO date strings; end is today UTC (exclusive in CE)."""
    end = _utc_today()
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat()


def _check_tag_activated(ce_client):
    """Return True if ClusterID is an active cost allocation tag, False if not,
    None if the check could not be performed (permissions or network error)."""
    try:
        resp = ce_client.list_cost_allocation_tags(
            TagKeys=["ClusterID"], Type="UserDefined", MaxResults=1
        )
        tags = resp.get("CostAllocationTags", [])
        return any(
            t.get("TagKey") == "ClusterID" and t.get("Status") == "Active"
            for t in tags
        )
    except _ClientError:
        return None
    except BotoCoreError:
        return None


def _get_cluster_cost(ce_client, cluster_name, start, end):
    """Query CE for total UnblendedCost tagged ClusterID=cluster_name.

    Follows NextPageToken to handle ranges spanning >12 months.
    Returns (total_usd: float, error: str|None).
    """
    total = 0.0
    next_token = None
    while True:
        kwargs = dict(
            TimePeriod={"Start": start, "End": end},
            Granularity="MONTHLY",
            Filter={"Tags": {"Key": "ClusterID", "Values": [cluster_name]}},
            Metrics=["UnblendedCost"],
        )
        if next_token:
            kwargs["NextPageToken"] = next_token
        try:
            resp = ce_client.get_cost_and_usage(**kwargs)
        except _ClientError as e:
            code = e.response["Error"]["Code"]
            if code in ("AccessDeniedException", "AuthFailure"):
                return None, "unavailable (needs ce:GetCostAndUsage)"
            return None, f"CE error: {code}"
        except BotoCoreError as e:
            return None, f"network/credential error: {e}"

        for period in resp.get("ResultsByTime", []):
            amount = (
                period.get("Total", {})
                .get("UnblendedCost", {})
                .get("Amount", "0")
            )
            try:
                total += float(amount)
            except ValueError:
                pass

        next_token = resp.get("NextPageToken")
        if not next_token:
            break

    return total, None


@dataclass(frozen=True)
class ClusterCostRecord:
    cluster_name: str
    owner: str
    region: str
    cost_usd: float | None
    error: str | None


@dataclass(frozen=True)
class CostReportResult:
    period_start: str
    period_end: str
    tag_activated: bool  # None = could not verify (permissions)
    records: list


def core_get_cost_report(*, cluster_records, owner_filter=None, days=30):
    """Report actual AWS spend per cluster via Cost Explorer.

    cluster_records: list[ClusterRecord], already resolved by the caller
    (Workstream 1's backend-agnostic principle -- this function never reads
    cluster state itself). Raises PClusterMakerError if days not in [1, 365].
    """
    if days < 1 or days > 365:
        raise PClusterMakerError("ERROR: --days must be between 1 and 365")

    start, end = _date_range(days)
    ce_client = boto3.client("ce", region_name="us-east-1")
    tag_activated = _check_tag_activated(ce_client)

    records = []
    for rec in cluster_records:
        if owner_filter and rec.cluster_owner != owner_filter:
            continue
        total, err = _get_cluster_cost(ce_client, rec.cluster_name, start, end)
        records.append(
            ClusterCostRecord(
                cluster_name=rec.cluster_name,
                owner=rec.cluster_owner,
                region=rec.region,
                cost_usd=total,
                error=err,
            )
        )

    return CostReportResult(
        period_start=start, period_end=end, tag_activated=tag_activated, records=records
    )


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


def _update_compute_fleet_lib(cluster_name, region, status):
    """update-compute-fleet via pcluster.lib. Same transport reasoning as
    _describe_cluster_json: no `.venv/bin/pcluster` on a Lambda, and a
    PClusterMakerError rather than a server-killing SystemExit.

    Confirmed against the installed package rather than assumed: this
    operation takes no `wait` kwarg -- it is absent from lib.py's own
    wait_ops list -- which is why the fleet paths poll manually and always
    have."""
    import pcluster.lib as pc

    try:
        return pc.update_compute_fleet(
            cluster_name=cluster_name, region=region, status=status
        )
    except Exception as e:
        raise PClusterMakerError(
            f"update-compute-fleet failed for {cluster_name!r} in {region}: "
            f"{type(e).__name__}: {e}"
        )


def _update_cluster_lib(cluster_name, region, config_path):
    """update-cluster via pcluster.lib.

    `cluster_configuration` must be a filesystem PATH, not YAML content --
    the CLI model tags it "type": "file", so the dispatcher hands a string
    to read_file(). Same subtlety the create side hit in round 23.

    Deliberately does not pass wait=True even though this operation does
    accept it: the local _poll_cluster_update prints per-attempt progress
    across what can be a 30-minute wait, and the library's own polling is
    opaque. Same precedent as create/delete."""
    import pcluster.lib as pc

    try:
        return pc.update_cluster(
            cluster_name=cluster_name, region=region,
            cluster_configuration=config_path,
        )
    except Exception as e:
        raise PClusterMakerError(
            f"update-cluster failed for {cluster_name!r} in {region}: "
            f"{type(e).__name__}: {e}"
        )


def _describe_cluster_json(cluster_name, region):
    """describe-cluster via pcluster.lib, not the pcluster binary.

    Replaces three call sites that each issued the identical
    `pcluster describe-cluster` subprocess and parsed a different field
    out of it -- the duplication session 48 flagged.

    The transport change is the more important half, and it is a bug fix
    rather than tidying. The subprocess form needs `.venv/bin/pcluster` to
    exist on disk; a Lambda deployment package has no virtualenv, so every
    fleet/health/diagnose tool on the remote transport would have hit
    "pcluster binary not found" -- raised as SystemExit, which is a
    BaseException, which the handler's deliberately-narrow `except
    Exception` does not catch, killing the whole Lambda rather than
    failing one call. The same hazard the S3 lock hit in round 41.

    Raises PClusterMakerError rather than SystemExit for exactly that
    reason; CLI shims already convert PClusterMakerError to sys.exit, so
    their behavior is unchanged.
    """
    import pcluster.lib as pc

    try:
        return pc.describe_cluster(cluster_name=cluster_name, region=region)
    except Exception as e:
        raise PClusterMakerError(
            f"describe-cluster failed for {cluster_name!r} in {region}: "
            f"{type(e).__name__}: {e}"
        )


def _get_fleet_status(cluster_name, region, pcluster_bin):
    """Return the current computeFleetStatus string for cluster_name."""
    return _describe_cluster_json(cluster_name, region).get(
        "computeFleetStatus", "UNKNOWN"
    )


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


# ---------------------------------------------------------------------------
# Health check helpers (shared by check_pcluster.py)
# ---------------------------------------------------------------------------


def _ssh_args(head_ip, ssh_keypair, ec2_user, timeout):
    return [
        "ssh",
        "-i", ssh_keypair,
        "-o", f"ConnectTimeout={timeout}",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "BatchMode=yes",
        f"{ec2_user}@{head_ip}",
    ]


def _run_ssh(head_ip, ssh_keypair, ec2_user, timeout, remote_cmd):
    args = _ssh_args(head_ip, ssh_keypair, ec2_user, timeout) + remote_cmd
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout + 5)
    return result.returncode, result.stdout, result.stderr


def check_cfn_status(cluster_name, region, pcluster_bin):
    try:
        data = _describe_cluster_json(cluster_name, region)
        cs = data.get("clusterStatus", "UNKNOWN")
        cfs = data.get("cloudFormationStackStatus", "UNKNOWN")
        head_node = data.get("headNode", {})
        head_ip = (
            head_node.get("publicIpAddress") or
            head_node.get("privateIpAddress") or ""
        )
        if cs != "CREATE_COMPLETE":
            return False, f"clusterStatus={cs} cloudFormationStackStatus={cfs}", head_ip
        return True, f"clusterStatus={cs}", head_ip
    except PClusterMakerError as e:
        return False, str(e), None
    except KeyError as e:
        return False, f"unexpected describe-cluster response: {e}", None


def check_head_ip(head_ip):
    if not head_ip:
        return False, "no IP address in describe-cluster response"
    return True, head_ip


def check_ssh(head_ip, ssh_keypair, ec2_user, timeout):
    try:
        rc, stdout, _ = _run_ssh(head_ip, ssh_keypair, ec2_user, timeout, ["echo", "OK"])
        if rc == 0 and "OK" in stdout:
            return True, None
        return False, f"SSH returned rc={rc}"
    except subprocess.TimeoutExpired:
        return False, "SSH connection timed out"
    except OSError as e:
        return False, f"SSH failed: {e}"


def check_slurm(head_ip, ssh_keypair, ec2_user, timeout):
    """Report Slurm healthy only if it answers AND has a usable node.

    `rc == 0` alone is not health: sinfo exits 0 while reporting every partition
    down or drained, so a cluster whose entire fleet had failed to bootstrap
    passed this check. The stdout was captured and never read.
    """
    try:
        rc, stdout, stderr = _run_ssh(
            head_ip, ssh_keypair, ec2_user, timeout,
            ["sinfo", "-h", "-o", "%D %T"],
        )
        if rc != 0:
            return False, f"sinfo returned rc={rc}: {stderr.strip()[:120]}"
        if not stdout.strip():
            return False, "sinfo reported no partitions"
        usable, unusable = _classify_sinfo_nodes(stdout)
        if usable == 0:
            return False, (
                f"no usable nodes: {unusable} node(s) down/drained/unknown "
                f"(sinfo -N -l on the head node has the detail)"
            )
        if unusable:
            return True, f"{usable} node(s) usable, {unusable} down/drained"
        return True, None
    except subprocess.TimeoutExpired:
        return False, "sinfo timed out"
    except OSError as e:
        return False, f"sinfo failed: {e}"


def check_s3(s3_bucketname, region):
    try:
        s3 = boto3.client("s3", region_name=region)
        s3.head_bucket(Bucket=s3_bucketname)
        return True, None
    except (_ClientError, BotoCoreError) as e:
        return False, str(e)
    except OSError as e:
        return False, f"S3 check failed: {e}"


def check_postinstall(head_ip, ssh_keypair, ec2_user, timeout):
    marker = "/opt/parallelcluster/shared/custom_action_done"
    try:
        rc, _, _ = _run_ssh(
            head_ip, ssh_keypair, ec2_user, timeout, ["test", "-f", marker]
        )
        if rc == 0:
            return True, None
        return False, f"marker file absent: {marker}"
    except subprocess.TimeoutExpired:
        return False, "postinstall check timed out"
    except OSError as e:
        return False, f"postinstall check failed: {e}"


def check_grafana(head_ip, ssh_keypair, ec2_user, timeout):
    cmd = [
        "curl", "-sk", "--max-time", "10",
        "https://localhost:443/grafana/api/health",
    ]
    try:
        rc, stdout, _ = _run_ssh(head_ip, ssh_keypair, ec2_user, timeout, cmd)
        if rc != 0:
            return False, f"curl returned rc={rc}"
        if '"database":"ok"' in stdout.replace(" ", ""):
            return True, None
        return False, f"unexpected Grafana response: {stdout.strip()[:120]}"
    except subprocess.TimeoutExpired:
        return False, "Grafana check timed out"
    except OSError as e:
        return False, f"Grafana check failed: {e}"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str  # "pass" | "fail" | "skip"
    detail: str


@dataclass(frozen=True)
class ClusterHealthReport:
    cluster_name: str
    checks: list
    healthy: bool


def core_check_cluster_health(*, cluster_record, pcluster_bin, timeout=15, ssh_available=True):
    """Run the full health-check sequence for one cluster.

    timeout is trusted as already validated -- the caller (CLI shim today;
    the MCP wrapper once built) clamps/validates it before calling, the same
    way cluster_name validation and ClusterRecord resolution are caller
    responsibilities (Workstream 1's backend-agnostic principle). This
    function never raises PClusterMakerError; the plan's original draft
    signature assumed it should, which didn't match the CLI's actual
    behavior of clamping with a warning rather than exiting.

    ssh_available=False (the remote transport, once built) degrades every
    SSH-dependent check to "skip" instead of attempting a connection with no
    key material -- see Workstream 7.
    """
    cluster_name = cluster_record.cluster_name
    region = cluster_record.region
    ssh_keypair = cluster_record.ssh_keypair
    ec2_user = cluster_record.ec2_user
    s3_bucketname = cluster_record.s3_bucketname
    enable_monitoring = cluster_record.enable_monitoring

    checks = []

    ok, msg, head_ip = check_cfn_status(cluster_name, region, pcluster_bin)
    if ok:
        checks.append(CheckResult("CloudFormation status", "pass", msg.split("=", 1)[1]))
    else:
        checks.append(CheckResult("CloudFormation status", "fail", msg))

    if head_ip is not None:
        ok, msg = check_head_ip(head_ip)
        if ok:
            checks.append(CheckResult("head node IP", "pass", head_ip))
        else:
            checks.append(CheckResult("head node IP", "fail", msg))
            head_ip = None
    else:
        checks.append(CheckResult("head node IP", "skip", "CloudFormation check failed"))

    ssh_ok = False
    if not ssh_available:
        checks.append(CheckResult("SSH reachability", "skip", "SSH unavailable on this transport"))
    elif head_ip:
        ok, err = check_ssh(head_ip, ssh_keypair, ec2_user, timeout)
        if ok:
            checks.append(CheckResult("SSH reachability", "pass", None))
            ssh_ok = True
        else:
            checks.append(CheckResult("SSH reachability", "fail", err))
    else:
        checks.append(CheckResult("SSH reachability", "skip", "head node IP unavailable"))

    if ssh_ok:
        ok, err = check_slurm(head_ip, ssh_keypair, ec2_user, timeout)
        checks.append(CheckResult("Slurm", "pass" if ok else "fail", err))

        ok, err = check_postinstall(head_ip, ssh_keypair, ec2_user, timeout)
        checks.append(CheckResult("postinstall complete", "pass" if ok else "fail", err))

        if enable_monitoring == "true":
            ok, err = check_grafana(head_ip, ssh_keypair, ec2_user, timeout)
            checks.append(CheckResult("Grafana health", "pass" if ok else "fail", err))
    else:
        reason = "SSH unreachable" if ssh_available else "SSH unavailable on this transport"
        checks.append(CheckResult("Slurm", "skip", reason))
        checks.append(CheckResult("postinstall complete", "skip", reason))
        if enable_monitoring == "true":
            checks.append(CheckResult("Grafana health", "skip", reason))

    ok, err = check_s3(s3_bucketname, region)
    checks.append(CheckResult(f"S3 bucket: {s3_bucketname}", "pass" if ok else "fail", err))

    healthy = not any(c.status == "fail" for c in checks)
    return ClusterHealthReport(cluster_name=cluster_name, checks=checks, healthy=healthy)


# ---------------------------------------------------------------------------
# Cluster listing helpers (shared by list_pcluster.py)
# ---------------------------------------------------------------------------


def _age_str(deployment_date):
    # DEPLOYMENT_DATE_TAG format: D-Month-YYYY e.g. "24-July-2026"
    try:
        dt = DateTime.strptime(deployment_date, "%d-%B-%Y").replace(tzinfo=timezone.utc)
        delta = DateTime.now(timezone.utc) - dt
        total_minutes = int(delta.total_seconds() // 60)
        if total_minutes < 60:
            return f"{total_minutes}m"
        total_hours = total_minutes // 60
        if total_hours < 48:
            return f"{total_hours}h"
        return f"{total_hours // 24}d"
    except (ValueError, TypeError):
        return "?"


def _live_status(cluster_name, region, pcluster_bin):
    try:
        data = _describe_cluster_json(cluster_name, region)
        cs = data.get("clusterStatus", "?")
        cfs = data.get("cloudFormationStackStatus", "?")
        return f"{cs} / {cfs}"
    except (PClusterMakerError, OSError):
        # "ERR" for this one cluster only -- core_list_clusters must never
        # let one unreachable cluster abort a listing of all the others.
        return "ERR"


@dataclass(frozen=True)
class ClusterListEntry:
    """Deliberately carries every ClusterRecord field, not just what
    _print_table's columns show -- today's list_pcluster.py -J output is the
    full record plus age/status, and narrowing this to only the displayed
    columns would silently drop ssh_keypair/s3_bucketname/etc. from JSON
    output any existing scripting against it relies on. Field order matches
    ClusterRecord's exactly, so dataclasses.asdict() reproduces today's JSON
    key order without dict.update() tricks."""

    cluster_name: str
    cluster_owner: str
    serial: str
    region: str
    headnode_instance_type: str
    enable_loginnode: str
    loginnode_instance_type: str
    loginnode_count: int
    cpu_instance_types: list
    gpu_instance_types: list
    enable_cpu_queue: str
    enable_gpu_queue: str
    initial_cpu_queue_size: int
    max_cpu_queue_size: int
    initial_gpu_queue_size: int
    max_gpu_queue_size: int
    cluster_type: str
    deployment_date: str
    ssh_keypair: str
    ec2_user: str
    s3_bucketname: str
    enable_monitoring: str
    age: str
    status: str


def core_list_clusters(*, cluster_records, pcluster_bin, region_filter=None,
                        owner_filter=None, live=False):
    """List already-resolved clusters, optionally filtered by region/owner,
    with live CloudFormation status. Never raises for one cluster's
    live-status failure -- mirrors today's tolerance (status="ERR" for that
    entry only, not an aborted listing).
    """
    entries = []
    for rec in cluster_records:
        if region_filter and rec.region != region_filter:
            continue
        if owner_filter and rec.cluster_owner != owner_filter:
            continue
        age = _age_str(rec.deployment_date)
        status = _live_status(rec.cluster_name, rec.region, pcluster_bin) if live else "LOCAL"
        entries.append(ClusterListEntry(
            cluster_name=rec.cluster_name,
            cluster_owner=rec.cluster_owner,
            serial=rec.serial,
            region=rec.region,
            headnode_instance_type=rec.headnode_instance_type,
            enable_loginnode=rec.enable_loginnode,
            loginnode_instance_type=rec.loginnode_instance_type,
            loginnode_count=rec.loginnode_count,
            cpu_instance_types=rec.cpu_instance_types,
            gpu_instance_types=rec.gpu_instance_types,
            enable_cpu_queue=rec.enable_cpu_queue,
            enable_gpu_queue=rec.enable_gpu_queue,
            initial_cpu_queue_size=rec.initial_cpu_queue_size,
            max_cpu_queue_size=rec.max_cpu_queue_size,
            initial_gpu_queue_size=rec.initial_gpu_queue_size,
            max_gpu_queue_size=rec.max_gpu_queue_size,
            cluster_type=rec.cluster_type,
            deployment_date=rec.deployment_date,
            ssh_keypair=rec.ssh_keypair,
            ec2_user=rec.ec2_user,
            s3_bucketname=rec.s3_bucketname,
            enable_monitoring=rec.enable_monitoring,
            age=age,
            status=status,
        ))
    return entries


# ---------------------------------------------------------------------------
# Deep diagnostic helpers (shared by diagnose_pcluster.py)
# ---------------------------------------------------------------------------

_LOCAL_LOGS = [
    "/var/log/parallelcluster/slurm_resume.log",
    "/var/log/parallelcluster/slurm_suspend.log",
    "/var/log/cinc/client.log",
    "/var/log/cloud-init-output.log",
]

_CW_STREAMS = ["cfn-init", "cloud-init-output", "cinc_client"]


def _get_head_ip(cluster_name, region, pcluster_bin):
    """Return (head_ip, error_str). Calls pcluster describe-cluster."""
    try:
        data = _describe_cluster_json(cluster_name, region)
    except PClusterMakerError as e:
        return None, str(e)
    cs = data.get("clusterStatus", "UNKNOWN")
    if cs != "CREATE_COMPLETE":
        return None, f"cluster not in CREATE_COMPLETE state (clusterStatus={cs})"
    head_node = data.get("headNode", {})
    ip = head_node.get("publicIpAddress") or head_node.get("privateIpAddress") or ""
    if not ip:
        return None, "no IP address in describe-cluster response"
    return ip, None


@dataclass(frozen=True)
class CloudWatchLogSection:
    log_group: str | None
    streams: dict
    error: str | None


def _fetch_cw_logs(cluster_name, region, streams, n_lines):
    """Fetch the last n_lines events from each CW stream. log_group is
    populated as soon as it's resolved, even if a later step fails -- the CLI
    shim prints it unconditionally once set, matching today's ordering
    (the log group line was printed before the stream-fetch could fail)."""
    logs = boto3.client("logs", region_name=region)
    try:
        group_names = []
        for page in logs.get_paginator("describe_log_groups").paginate(
            logGroupNamePrefix=f"/aws/parallelcluster/{cluster_name}-"
        ):
            for g in page.get("logGroups", []):
                group_names.append(g["logGroupName"])
    except _ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "AccessDeniedException":
            return CloudWatchLogSection(None, {}, (
                f"CloudWatch unavailable: {code} — add logs:DescribeLogGroups "
                f"on Resource \"*\" to the operator policy, or use --no_cw"
            ))
        return CloudWatchLogSection(None, {}, f"CloudWatch error: {code}")
    except BotoCoreError as e:
        return CloudWatchLogSection(None, {}, f"CloudWatch network error: {e}")

    log_group = _select_cw_log_group(cluster_name, group_names)
    if log_group is None:
        return CloudWatchLogSection(None, {}, (
            f"no CloudWatch log group for '{cluster_name}' — PCluster creates "
            f"/aws/parallelcluster/{cluster_name}-<timestamp> once the head node "
            "starts logging, which is several minutes into a build"
        ))

    try:
        existing = []
        for page in logs.get_paginator("describe_log_streams").paginate(
            logGroupName=log_group, orderBy="LastEventTime", descending=True
        ):
            for s in page.get("logStreams", []):
                existing.append(s["logStreamName"])
    except _ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "AccessDeniedException":
            return CloudWatchLogSection(log_group, {}, (
                f"CloudWatch unavailable: {code} — add logs:DescribeLogStreams "
                f"to the operator policy, or use --no_cw"
            ))
        return CloudWatchLogSection(log_group, {}, f"CloudWatch error: {code}")
    except BotoCoreError as e:
        return CloudWatchLogSection(log_group, {}, f"CloudWatch network error: {e}")

    results = {}
    for stream in streams:
        matched = [s for s in existing if stream in s]
        if not matched:
            results[stream] = []
            continue
        target = matched[0]
        all_lines = []
        kwargs = dict(logGroupName=log_group, logStreamName=target, startFromHead=False)
        try:
            while len(all_lines) < n_lines:
                resp = logs.get_log_events(**kwargs)
                events = resp.get("events", [])
                if not events:
                    break
                page_lines = [
                    f"  {DateTime.fromtimestamp(e['timestamp'] / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}  {e['message'].rstrip()}"
                    for e in events
                ]
                all_lines = page_lines + all_lines
                next_token = resp.get("nextBackwardToken")
                if not next_token or next_token == kwargs.get("nextToken"):
                    break
                kwargs["nextToken"] = next_token
        except (_ClientError, BotoCoreError):
            pass
        results[stream] = all_lines[-n_lines:]

    return CloudWatchLogSection(log_group, results, None)


def _tail_lines(text, n):
    """Return the last n non-empty lines of text as a single string."""
    if n <= 0:
        return ""
    lines = [l for l in text.splitlines() if l.strip()]
    return "\n".join(lines[-n:])


def _format_sinfo(output):
    """Return sinfo -N -l output with non-idle nodes annotated.

    Identifies the STATE column index from the header line, then checks only
    that column on data rows to avoid false positives from other column names.
    """
    lines = output.splitlines()
    if not lines:
        return ""
    out = []
    state_col = None
    for i, line in enumerate(lines):
        parts = line.split()
        if state_col is None:
            headers = [h.upper() for h in parts]
            if "STATE" in headers:
                state_col = headers.index("STATE")
            out.append(f"  {line}")
            continue
        marker = ""
        if len(parts) > state_col:
            if not _sinfo_state_is_ok(parts[state_col]):
                marker = "   <-- not idle"
        out.append(f"  {line}{marker}")
    return "\n".join(out)


def _parse_sacct(output):
    """Return sacct output lines, or None if there are no data rows."""
    lines = [l for l in output.splitlines() if l.strip()]
    if not lines:
        return None
    return "\n".join(f"  {l}" for l in lines)


@dataclass(frozen=True)
class SinfoSection:
    formatted_output: str
    error: str | None


def _diagnose_sinfo(head_ip, ssh_keypair, ec2_user, timeout):
    try:
        rc, stdout, stderr = _run_ssh(head_ip, ssh_keypair, ec2_user, timeout, ["sinfo", "-N", "-l"])
        if rc == 0:
            return SinfoSection(_format_sinfo(stdout) if stdout.strip() else "", None)
        return SinfoSection("", f"sinfo failed (rc={rc}): {stderr.strip()[:200]}")
    except subprocess.TimeoutExpired:
        return SinfoSection("", "sinfo timed out")
    except OSError as e:
        return SinfoSection("", f"sinfo failed: {e}")


@dataclass(frozen=True)
class SacctSection:
    formatted_output: str | None
    error: str | None


def _diagnose_sacct(head_ip, ssh_keypair, ec2_user, timeout, hours):
    since = (DateTime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    sacct_cmd = [
        "sacct", "-X",
        "--state=FAILED,CANCELLED,TIMEOUT,NODE_FAIL",
        f"--starttime={since}",
        "--format=JobID,JobName,State,ExitCode,NodeList,Start,End",
        "--noheader",
    ]
    try:
        rc, stdout, stderr = _run_ssh(head_ip, ssh_keypair, ec2_user, timeout, sacct_cmd)
        if rc != 0:
            stderr_short = stderr.strip()[:200]
            if "command not found" in stderr_short or rc == 127:
                return SacctSection(None, "sacct not available — Slurm accounting is not enabled on this cluster")
            return SacctSection(None, f"sacct failed (rc={rc}): {stderr_short}")
        return SacctSection(_parse_sacct(stdout), None)
    except subprocess.TimeoutExpired:
        return SacctSection(None, "sacct timed out")
    except OSError as e:
        return SacctSection(None, f"sacct failed: {e}")


@dataclass(frozen=True)
class LocalLogTail:
    path: str
    content: str | None
    error: str | None


def _diagnose_local_logs(head_ip, ssh_keypair, ec2_user, timeout, log_lines):
    tails = []
    for log_path in _LOCAL_LOGS:
        try:
            rc, stdout, stderr = _run_ssh(
                head_ip, ssh_keypair, ec2_user, timeout, ["tail", "-n", str(log_lines), log_path]
            )
            if rc == 0:
                tails.append(LocalLogTail(log_path, _tail_lines(stdout, log_lines), None))
            else:
                tails.append(LocalLogTail(log_path, None, f"unavailable — {stderr.strip()[:120]}"))
        except subprocess.TimeoutExpired:
            tails.append(LocalLogTail(log_path, None, "timed out"))
        except OSError as e:
            tails.append(LocalLogTail(log_path, None, f"error: {e}"))
    return tails


@dataclass(frozen=True)
class PostinstallSection:
    marker_present: bool | None
    error: str | None


def _diagnose_postinstall(head_ip, ssh_keypair, ec2_user, timeout):
    marker = "/opt/parallelcluster/shared/custom_action_done"
    try:
        rc, _, _ = _run_ssh(head_ip, ssh_keypair, ec2_user, timeout, ["test", "-f", marker])
        return PostinstallSection(rc == 0, None)
    except subprocess.TimeoutExpired:
        return PostinstallSection(None, "timed out")
    except OSError as e:
        return PostinstallSection(None, f"error: {e}")


@dataclass(frozen=True)
class DiagnosticReport:
    cluster_name: str
    region: str
    serial: str
    head_ip: str | None
    head_ip_error: str | None
    cloudwatch: CloudWatchLogSection | None   # None if include_cloudwatch=False
    sinfo: SinfoSection | None                 # None if SSH unavailable/unreachable
    sacct: SacctSection | None
    local_logs: list
    postinstall: PostinstallSection | None


def core_diagnose_cluster(*, cluster_record, pcluster_bin, region_override=None,
                           timeout=20, cw_lines=50, log_lines=30, hours=24,
                           include_cloudwatch=True, ssh_available=True):
    """Deep diagnostic for one cluster. Numeric args are trusted as already
    validated -- the CLI shim clamps them before calling, same as
    core_check_cluster_health (timeout/cw_lines/log_lines/hours must stay
    textually inside diagnose_pcluster.py itself for
    TestArgumentBounds::test_diagnose_clamps_all_four_numeric_args to keep
    seeing them, and clamping here would also reorder the CLI's print output
    relative to today's script, the same class of bug already caught and
    fixed in check_pcluster.py's migration).

    Raises PClusterMakerError if cluster_record.ec2_user isn't recognized --
    this validates the resolved record's own integrity, not caller input, so
    it belongs here rather than in the CLI shim (unlike the numeric args
    above). The CLI shim defers printing anything about the cluster until
    after this call succeeds, so the exception still surfaces before any
    output, matching today's script exactly.
    """
    if cluster_record.ec2_user not in _VALID_EC2_USERS:
        raise PClusterMakerError(
            f"ERROR: unrecognized ec2_user {cluster_record.ec2_user!r} in vars file. "
            f"Expected one of: {sorted(_VALID_EC2_USERS)}"
        )

    cluster_name = cluster_record.cluster_name
    region = region_override or cluster_record.region
    ssh_keypair = cluster_record.ssh_keypair
    ec2_user = cluster_record.ec2_user
    serial = cluster_record.serial or "unknown"

    head_ip, head_ip_error = _get_head_ip(cluster_name, region, pcluster_bin)

    cloudwatch = None
    if include_cloudwatch:
        streams = _CW_STREAMS
        if cluster_record.enable_monitoring == "true":
            streams = streams + ["grafana", "prometheus"]
        cloudwatch = _fetch_cw_logs(cluster_name, region, streams, cw_lines)

    ssh_ok = ssh_available and head_ip is not None
    sinfo = sacct = postinstall = None
    local_logs = []
    if ssh_ok:
        sinfo = _diagnose_sinfo(head_ip, ssh_keypair, ec2_user, timeout)
        sacct = _diagnose_sacct(head_ip, ssh_keypair, ec2_user, timeout, hours)
        local_logs = _diagnose_local_logs(head_ip, ssh_keypair, ec2_user, timeout, log_lines)
        postinstall = _diagnose_postinstall(head_ip, ssh_keypair, ec2_user, timeout)

    return DiagnosticReport(
        cluster_name=cluster_name,
        region=region,
        serial=serial,
        head_ip=head_ip,
        head_ip_error=head_ip_error,
        cloudwatch=cloudwatch,
        sinfo=sinfo,
        sacct=sacct,
        local_logs=local_logs,
        postinstall=postinstall,
    )


# ---------------------------------------------------------------------------
# Node-access helpers (shared by access_cluster.py)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AccessInfo:
    cluster_name: str
    node_type: str    # "HeadNode" | "LoginNode"
    node_label: str   # "head node" | "login node"


def core_resolve_access_node_type(rec, cluster_name, *, login_node_requested=False,
                                    head_node_requested=False):
    """Decide which node type to connect to. rec is a dict from
    _read_cluster_record, or {} for a missing/unreadable vars file --
    deliberately NOT a ClusterRecord: this reuses _resolve_access_node_type,
    which only reads two keys and must tolerate a sparse or empty dict, the
    same tolerance access_cluster.py has always had for a cluster with no
    vars file at all. Raises PClusterMakerError if both flags are set
    (argparse's own mutually-exclusive group already prevents this on the
    CLI path, but a future MCP caller has no such guard), if a login node
    was requested but none is configured, or if one is configured with
    count zero.
    """
    if login_node_requested and head_node_requested:
        raise PClusterMakerError("ERROR: --login_node and --head_node are mutually exclusive.")

    node_type, error = _resolve_access_node_type(
        rec, cluster_name,
        login_node_requested=login_node_requested,
        head_node_requested=head_node_requested,
    )
    if error:
        raise PClusterMakerError(error)

    node_label = "login node" if node_type == "LoginNode" else "head node"
    return AccessInfo(cluster_name=cluster_name, node_type=node_type, node_label=node_label)


def core_exec_access_script(*, cluster_data_root, cluster_name, node_type):
    """Run the cluster's access script with ACCESS_NODE_TYPE set and return
    its exit code. Interactive by design (inherits stdin/stdout/stderr for
    the SSH session itself) -- CLI shim only, never registered as an MCP
    tool on either transport; not expressible as a tool-call result."""
    access_script = _resolve_access_script_path(cluster_data_root, cluster_name)
    env = dict(os.environ, ACCESS_NODE_TYPE=node_type)
    result = subprocess.run(["bash", access_script], env=env)
    return result.returncode


# ---------------------------------------------------------------------------
# Grafana tunnel helpers (shared by grafana_tunnel.py)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TunnelResult:
    cluster_name: str
    action: str   # "start" | "stop"
    port: int
    success: bool
    error: str | None


def core_manage_grafana_tunnel(*, cluster_record, tunnel_script_path, port=8443, stop=False):
    """Start or stop the Grafana SSH tunnel for a cluster. Raises
    PClusterMakerError if monitoring isn't enabled, or the tunnel script
    doesn't exist -- both are precondition failures, not tunnel-execution
    failures, so they raise rather than surface in TunnelResult.error, which
    is reserved for the tunnel script itself running and reporting failure.
    Registered as an MCP tool on the local stdio FastMCP instance only,
    never remote (Workstream 7) -- SSH key material lives only on the
    operator's machine.
    """
    cluster_name = cluster_record.cluster_name
    if cluster_record.enable_monitoring != "true":
        raise PClusterMakerError(
            f"ERROR: monitoring is not enabled for cluster {cluster_name!r}.\n"
            f"  Rebuild with --enable_monitoring=true to use Grafana."
        )
    if not os.path.isfile(tunnel_script_path):
        raise PClusterMakerError(
            f"ERROR: tunnel script not found: {tunnel_script_path}\n"
            f"  Make sure the cluster was built with monitoring enabled."
        )

    action = "stop" if stop else "start"
    # The tunnel script's exit status is the only signal that ssh -L actually
    # bound the port; swallowing it made a dead tunnel look like a live one.
    result = subprocess.run(["bash", tunnel_script_path, str(port), action], check=False)
    if result.returncode != 0:
        return TunnelResult(
            cluster_name=cluster_name, action=action, port=port, success=False,
            error=f"tunnel script failed to {action} the tunnel (exit {result.returncode})",
        )
    return TunnelResult(cluster_name=cluster_name, action=action, port=port, success=True, error=None)


# ---------------------------------------------------------------------------
# Fleet action helpers (shared by stop_pcluster.py and start_pcluster.py)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FleetActionResult:
    cluster_name: str
    action: str            # "stop" | "start"
    status_before: str
    status_after: str | None   # None unless wait=True
    plan: str               # "done" | "wait" | "request" -- never "abort", which raises instead


def _core_fleet_action(*, cluster_record, action, target_status, pcluster_bin, region, wait=False):
    """Shared orchestration for core_stop_fleet/core_start_fleet: check
    status, decide the plan, act if needed, optionally wait. Raises
    PClusterMakerError for PROTECTED (the plan's draft listed "abort" as a
    possible FleetActionResult.plan value too, alongside "raises
    PClusterMakerError if fleet is PROTECTED" in the same docstring --
    those two are inconsistent, and raising is what every other migrated
    script does for a hard failure, so that's what actually happens here).

    The three prints inside this function (before update-compute-fleet,
    right after it succeeds, and bracketing an internal wait/poll) exist
    because they're interleaved with describe-cluster/_poll_fleet calls
    that themselves have unavoidable side effects (_poll_fleet already
    prints its own progress) -- reproducing this ordering from the CLI shim
    alone isn't possible without either duplicating this whole function's
    control flow in the shim or calling it twice. Both are print()s that
    only fire on paths (plan=="request", wait=True) an MCP caller's
    wait=False call never takes, so this doesn't compromise MCP callability
    in practice, matching the same reasoning already applied to
    _poll_fleet's own prints and _clamp_int's warning prints elsewhere in
    this file.
    """
    cluster_name = cluster_record.cluster_name

    status = _get_fleet_status(cluster_name, region, pcluster_bin)
    plan = _fleet_action_plan(status, action)

    if plan == "abort":
        raise PClusterMakerError(
            "ERROR: compute fleet is in PROTECTED state — investigate node failures before retrying."
        )

    if plan == "done":
        return FleetActionResult(cluster_name, action, status, status, "done")

    if plan == "request":
        print(f"Requesting fleet {action}...")
        requested_status = "STOP_REQUESTED" if action == "stop" else "START_REQUESTED"
        _update_compute_fleet_lib(cluster_name, region, requested_status)
        print(f"{action.capitalize()} requested.")

    status_after = None
    if wait:
        print(f"Waiting for fleet to reach {target_status}...")
        _poll_fleet(cluster_name, region, target_status, f"fleet {action}", pcluster_bin)
        print(f"Fleet is {target_status}.")
        status_after = target_status

    return FleetActionResult(cluster_name, action, status, status_after, plan)


def core_stop_fleet(*, cluster_record, region, pcluster_bin, wait=False):
    """Stop a cluster's compute fleet. Reused as-is for
    manage_pcluster_queue's phase 1 (not yet migrated)."""
    return _core_fleet_action(
        cluster_record=cluster_record, action="stop", target_status="STOPPED",
        pcluster_bin=pcluster_bin, region=region, wait=wait,
    )


def core_start_fleet(*, cluster_record, region, pcluster_bin, wait=False):
    """Start a cluster's compute fleet. Reused as-is for
    manage_pcluster_queue's phase 3 (not yet migrated)."""
    return _core_fleet_action(
        cluster_record=cluster_record, action="start", target_status="RUNNING",
        pcluster_bin=pcluster_bin, region=region, wait=wait,
    )


# ---------------------------------------------------------------------------
# SSH key rotation (shared by rotate_cluster_key.py)
# ---------------------------------------------------------------------------


def _require_on_path(cmd):
    if subprocess.run(["which", cmd], capture_output=True).returncode != 0:
        raise PClusterMakerError(f"ERROR: '{cmd}' not found on PATH.")


def _import_ec2_keypair(ec2, keypair_name, pub_key):
    """Import a public key as an EC2 keypair, replacing any existing keypair
    of the same name. Shared by both the "-rotated" staging import and the
    final rename-to-canonical import in core_rotate_cluster_key -- those two
    steps were identical duplicate-handling logic in the original script."""
    try:
        ec2.import_key_pair(KeyName=keypair_name, PublicKeyMaterial=pub_key.encode())
    except _ClientError as e:
        if "InvalidKeyPair.Duplicate" in str(e):
            ec2.delete_key_pair(KeyName=keypair_name)
            ec2.import_key_pair(KeyName=keypair_name, PublicKeyMaterial=pub_key.encode())
        else:
            raise


@dataclass(frozen=True)
class KeyRotationResult:
    cluster_name: str
    secret_name: str
    head_ip: str
    local_key_path_updated: bool
    old_keypair_deleted: bool
    dry_run: bool


def core_rotate_cluster_key(*, cluster_record, region, secret_name=None, dry_run=False):
    """Rotate the SSH keypair for a running cluster. Never returns key
    material -- status fields only, mirroring today's stdout (which also
    never prints the key). Local stdio FastMCP instance only (Workstream 7):
    SSH key material and local .pem writes only make sense on the
    operator's own machine.

    Unlike every other core function in this file, this one keeps its
    print()s rather than returning everything as structured data. That's
    deliberate, not an oversight: every other core function is also
    remote-callable, where stdout is silently discarded (a Lambda's stdout
    never reaches the MCP response), so print()s there would be dead
    weight at best. This function is local-only, so its prints are exactly
    what both the CLI operator and a local Claude Code MCP session actually
    see -- there's no remote caller to strand.

    Still raises PClusterMakerError rather than calling sys.exit(), same as
    every other core function, and for a reason that matters even more
    here: an uncaught SystemExit inside a long-lived FastMCP server process
    would kill the whole server, not just fail this one tool call.
    """
    cluster_name = cluster_record.cluster_name
    serial = cluster_record.serial
    ec2_keypair = cluster_record.ec2_keypair
    ec2_user = cluster_record.ec2_user
    ssh_keypair = cluster_record.ssh_keypair
    secret_name = secret_name or _ssh_secret_name(cluster_name, serial)

    if not serial or not ec2_keypair:
        # Also checked by the CLI shim before this function is even called
        # (Turbot profile switching happens between that check and this
        # call, and needs to stay after it to match today's print order) --
        # kept here too as real defense-in-depth: an empty serial would
        # otherwise flow straight into a malformed Secrets Manager name via
        # _ssh_secret_name, for an MCP caller with no equivalent preflight.
        raise PClusterMakerError("ERROR: vars file is missing cluster_serial_number or ec2_keypair.")

    _require_on_path("ssh")
    _require_on_path("ssh-keygen")
    _require_on_path("aws")

    ec2 = boto3.client("ec2", region_name=region)
    sm = boto3.client("secretsmanager", region_name=region)

    try:
        resp = ec2.describe_instances(
            Filters=[
                {"Name": "tag:parallelcluster:cluster-name", "Values": [cluster_name]},
                {"Name": "tag:parallelcluster:node-type", "Values": ["HeadNode"]},
                {"Name": "instance-state-name", "Values": ["running"]},
            ]
        )
        reservations = resp.get("Reservations", [])
        instance = reservations[0]["Instances"][0] if reservations else {}
        head_ip = instance.get("PublicIpAddress") or instance.get("PrivateIpAddress", "")
    except (BotoCoreError, _ClientError, NoCredentialsError) as e:
        raise PClusterMakerError(f"ERROR: Could not describe EC2 instances: {e}")

    if not head_ip:
        raise PClusterMakerError(f"ERROR: No running head node found for cluster '{cluster_name}'.")

    print(f"  Cluster:   {cluster_name}  ({serial})")
    print(f"  Head node: {head_ip}")
    print(f"  Secret:    {secret_name}")

    if dry_run:
        print("\n[dry-run] No changes made.")
        return KeyRotationResult(cluster_name, secret_name, head_ip, False, False, True)

    with tempfile.TemporaryDirectory() as tmpdir:
        new_key_path = os.path.join(tmpdir, "id_ed25519_new")
        new_pub_path = new_key_path + ".pub"

        # 0. Capture the current public key before any key material changes,
        #    so it can be revoked from authorized_keys after the new key is
        #    verified to work.
        old_pub_key = None
        if ssh_keypair and os.path.isfile(ssh_keypair):
            try:
                old_pub_key = subprocess.run(
                    ["ssh-keygen", "-y", "-f", ssh_keypair],
                    check=True, capture_output=True, text=True,
                ).stdout.strip()
            except subprocess.CalledProcessError as e:
                print(f"  Warning: could not derive the current public key: {e}")
                print("  The old key will not be removed from authorized_keys.")

        # 1. Generate new ED25519 keypair.
        print("\nGenerating new ED25519 keypair...")
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", new_key_path], check=True)
        with open(new_pub_path) as f:
            new_pub_key = f.read().strip()
        with open(new_key_path) as f:
            new_priv_key = f.read()

        # 2. Add new public key to head node authorized_keys via SSH.
        print("Adding new public key to head node authorized_keys...")
        subprocess.run(
            [
                "ssh", "-i", ssh_keypair,
                "-o", "StrictHostKeyChecking=accept-new",
                "-o", "ConnectTimeout=30",
                "-o", "BatchMode=yes",
                f"{ec2_user}@{head_ip}",
                _append_key_script(),
            ],
            input=(new_pub_key + "\n").encode(),
            check=True,
        )

        # 2a. Verify the new key logs in successfully, then remove the old
        #     public key from authorized_keys. This order avoids locking
        #     ourselves out if the new key doesn't work, and ensures rotation
        #     actually revokes the old key rather than merely adding a new one.
        print("Verifying the new key can authenticate...")
        try:
            subprocess.run(
                [
                    "ssh", "-i", new_key_path,
                    "-o", "StrictHostKeyChecking=accept-new",
                    "-o", "ConnectTimeout=30",
                    "-o", "BatchMode=yes",
                    f"{ec2_user}@{head_ip}",
                    "true",
                ],
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise PClusterMakerError(
                f"ERROR: new key failed to authenticate: {e}\n"
                f"  The old key was left in place in authorized_keys. No AWS resources were changed."
            )

        if old_pub_key:
            print("Removing old public key from head node authorized_keys...")
            try:
                subprocess.run(
                    [
                        "ssh", "-i", new_key_path,
                        "-o", "StrictHostKeyChecking=accept-new",
                        "-o", "ConnectTimeout=30",
                        "-o", "BatchMode=yes",
                        f"{ec2_user}@{head_ip}",
                        _remove_old_key_script(),
                    ],
                    input=(old_pub_key + "\n" + new_pub_key + "\n").encode(),
                    check=True,
                )
            except subprocess.CalledProcessError as e:
                raise PClusterMakerError(
                    f"ERROR: could not safely remove the old key from authorized_keys: {e}\n"
                    f"  The live authorized_keys file was left untouched — either the old key\n"
                    f"  is still present, or the candidate replacement did not contain the new\n"
                    f"  key, so nothing was moved into place.\n"
                    f"  No AWS resources were changed. Inspect ~/.ssh/authorized_keys on the\n"
                    f"  head node manually, then re-run this script."
                )

        # 3. Import new public key as EC2 keypair (rotated name).
        new_keypair_name = ec2_keypair + "-rotated"
        print(f"Importing new EC2 keypair: {new_keypair_name}...")
        _import_ec2_keypair(ec2, new_keypair_name, new_pub_key)

        # 4. Update Secrets Manager secret.
        print("Updating Secrets Manager secret...")
        sm.put_secret_value(SecretId=secret_name, SecretString=new_priv_key)

        # 5. Overwrite local .pem file.
        local_key_path_updated = False
        if ssh_keypair:
            print(f"Updating local key file: {ssh_keypair}...")
            try:
                fd = os.open(ssh_keypair, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as f:
                    f.write(new_priv_key)
                local_key_path_updated = True
            except OSError as e:
                print(f"  Warning: could not write local key file: {e}")
                print(f"  The new key is safe in Secrets Manager: {secret_name}")
                print(f"  Retrieve it with: active_clusters/{cluster_name}/retrieve_ssh_key.{cluster_name}.sh")

        # 6. Delete old EC2 keypair.
        print(f"Deleting old EC2 keypair: {ec2_keypair}...")
        old_keypair_deleted = True
        try:
            ec2.delete_key_pair(KeyName=ec2_keypair)
        except _ClientError as e:
            print(f"  Warning: could not delete old keypair: {e}")
            old_keypair_deleted = False

        # Rename the rotated keypair to the canonical name. The final
        # delete_key_pair below has no try/except in the original script
        # either -- preserved exactly rather than silently hardened, since
        # this is a high-consequence security operation and adding new
        # error-handling behavior here wasn't asked for.
        print(f"Renaming {new_keypair_name} → {ec2_keypair}...")
        _import_ec2_keypair(ec2, ec2_keypair, new_pub_key)
        ec2.delete_key_pair(KeyName=new_keypair_name)

    print("")
    print("=" * 66)
    print("  SSH key rotation complete.")
    print(f"  New key stored in Secrets Manager: {secret_name}")
    if ssh_keypair:
        print(f"  Local .pem updated: {ssh_keypair}")
    print(f"  Verify access: ssh -i {ssh_keypair} {ec2_user}@{head_ip}")
    print("=" * 66)

    return KeyRotationResult(
        cluster_name, secret_name, head_ip, local_key_path_updated, old_keypair_deleted, False,
    )


# ---------------------------------------------------------------------------
# Slurm queue management (shared by manage_pcluster_queue.py)
#
# Merged in from the former src/pcluster_queue_editor.py, which predated the
# "all Python logic lives in pcluster_core.py/pcluster_aux_data.py" rule and
# was never brought into line with it. Functions that can fail on ordinary
# user input (bad queue name, missing config, arch mismatch) now raise
# PClusterMakerError instead of sys.exit() -- they are reachable from the
# core_* functions below, and an uncaught SystemExit inside a future MCP
# server process kills the whole server, not just one tool call.
# ---------------------------------------------------------------------------


def _make_yaml():
    """Return a configured ruamel YAML instance."""
    yaml_rt = YAML()
    yaml_rt.preserve_quotes = True
    yaml_rt.indent(mapping=2, sequence=4, offset=2)
    # ruamel's default line width is 80, so round-tripping reflows long
    # untouched lines (post-install script URLs, IAM policy ARNs) into
    # continuations. That is a spurious diff at best and, for a value PCluster
    # reads as a single token, a corrupted config at worst. Effectively disable
    # wrapping so only the queue we actually edited changes.
    yaml_rt.width = 4096
    return yaml_rt


def _queue_config_path(cluster_name, repo_root):
    """Path to a live cluster's editable config.<cluster_name> file.

    Wraps _validate_cluster_name's SystemExit as PClusterMakerError -- this
    is the only gate on cluster_name-derived path safety for the queue-edit
    path, so it must never raise something an MCP server call can't catch.
    """
    try:
        _validate_cluster_name(cluster_name)
    except SystemExit as e:
        raise PClusterMakerError(str(e))
    return os.path.join(
        repo_root, "active_clusters", cluster_name, f"config.{cluster_name}"
    )


def _load_cluster_config(cluster_name, repo_root):
    path = _queue_config_path(cluster_name, repo_root)
    if not os.path.isfile(path):
        raise PClusterMakerError(f"ERROR: cluster config not found: {path}")
    try:
        with open(path) as fh:
            config = _make_yaml().load(fh)
    except Exception as exc:
        raise PClusterMakerError(f"ERROR: failed to parse {path}: {exc}")
    return config, path


def _write_cluster_config(config_path, config_dict):
    # Keep one generation of the previous config. The only copy of a live
    # cluster's configuration lives at this path, and a rewrite that produces a
    # structurally valid but wrong document (or a queue removal the operator
    # then wants back) is otherwise unrecoverable without rebuilding.
    if os.path.isfile(config_path):
        shutil.copy2(config_path, config_path + ".bak")
    tmp_path = None
    try:
        dir_ = os.path.dirname(config_path)
        with tempfile.NamedTemporaryFile(
            mode="w", dir=dir_, delete=False, suffix=".tmp"
        ) as tmp:
            tmp_path = tmp.name
            buf = StringIO()
            _make_yaml().dump(config_dict, buf)
            result = buf.getvalue()
            result = re.sub(r'\n(SharedStorage:)', r'\n\n\1', result)
            result = re.sub(r'\n{3,}(SharedStorage:)', r'\n\n\1', result)
            result = re.sub(r'\n+(\n  - Name:)', r'\1', result)
            tmp.write(result)
        os.replace(tmp_path, config_path)
    except Exception:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


# PCluster's NameValidator (NAME_MAX_LENGTH = 25) is registered on both
# BaseQueue and BaseComputeResource, and core_add_queue derives the compute
# resource name as f"{queue_name}-resource". A 25-char queue name therefore
# yields a 34-char resource name that pcluster update-cluster rejects -- after
# the compute fleet has already been stopped under -W. Bound the queue name
# by the suffix.
COMPUTE_RESOURCE_SUFFIX = "-resource"
PCLUSTER_NAME_MAX_LENGTH = 25
QUEUE_NAME_MAX_LENGTH = PCLUSTER_NAME_MAX_LENGTH - len(COMPUTE_RESOURCE_SUFFIX)


def _validate_queue_name(name):
    if len(name) > QUEUE_NAME_MAX_LENGTH:
        raise PClusterMakerError(
            f"ERROR: queue name '{name}' is {len(name)} chars; the limit is "
            f"{QUEUE_NAME_MAX_LENGTH} because the derived compute resource name "
            f"'{name}{COMPUTE_RESOURCE_SUFFIX}' must fit PCluster's "
            f"{PCLUSTER_NAME_MAX_LENGTH}-character name limit"
        )
    pattern = r'^[a-z][a-z0-9]?$|^[a-z][a-z0-9-]{0,23}[a-z0-9]$'
    if not re.match(pattern, name):
        raise PClusterMakerError(
            f"ERROR: invalid queue name '{name}'. Must start with a lowercase letter, "
            "contain only lowercase letters, digits, and hyphens, and end with a letter or digit."
        )
    if '--' in name:
        raise PClusterMakerError(f"ERROR: queue name '{name}' contains consecutive hyphens")
    # PCluster's NameValidator hard-rejects this exact name on queues and
    # compute resources alike.
    if name == "default":
        raise PClusterMakerError(
            "ERROR: 'default' is a reserved name in AWS ParallelCluster and cannot "
            "be used as a queue name"
        )


# Every AWS EC2 instance type is exactly <family>.<size> -- one dot,
# each half alphanumeric with optional internal hyphens (e.g. u-6tb1.56xlarge,
# c8g.metal-24xl). Reject anything outside that character class rather than
# whitelisting specific hyphen positions: AWS adds new naming shapes over
# time (hyphenated families, hyphenated metal sizes) and a position-specific
# pattern silently falls behind. This still rejects every shell metacharacter,
# space, colon, and newline -- the actual injection surface -- without needing
# to track which naming shape AWS ships next.
_INSTANCE_TYPE_RE = re.compile(r'^[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?\.[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?$')


def _validate_instance_types(raw, require_gpu):
    from pcluster_aux_data import ec2_instances_full_list, is_arm_instance, is_gpu_instance, parse_instance_type_list

    types = parse_instance_type_list(raw)
    if not types:
        raise PClusterMakerError("ERROR: no instance types provided")
    for itype in types:
        if not _INSTANCE_TYPE_RE.match(itype):
            raise PClusterMakerError(
                f"ERROR: '{itype}' is not a valid EC2 instance type "
                "(expected format: <family>.<size>, e.g. c5.2xlarge)"
            )
    for itype in types:
        if itype not in ec2_instances_full_list:
            print(f"WARNING: '{itype}' not found in known instance type list (list may be stale)")
        if require_gpu and not is_gpu_instance(itype):
            raise PClusterMakerError(f"ERROR: instance type '{itype}' is not a GPU instance but --gpu was specified")
        if not require_gpu and is_gpu_instance(itype):
            raise PClusterMakerError(f"ERROR: instance type '{itype}' is a GPU instance but --gpu was not specified")
    arm_types = [t for t in types if is_arm_instance(t)]
    x86_types = [t for t in types if not is_arm_instance(t)]
    if arm_types and x86_types:
        raise PClusterMakerError(
            f"ERROR: mixed CPU architectures in instance list.\n"
            f"  ARM: {arm_types}\n"
            f"  x86: {x86_types}"
        )
    return types


def _gdr_capable_types(instance_types):
    """Return the subset of instance_types that support EFA GPUDirect RDMA."""
    from pcluster_aux_data import needs_efa_gdr

    return [t for t in instance_types if needs_efa_gdr(t)]


def _cluster_arch(config):
    """Return 'arm64', 'x86_64', or None for the head node and existing queues."""
    from pcluster_aux_data import is_arm_instance

    types = []
    head = config.get("HeadNode", {}).get("InstanceType")
    if head:
        types.append(head)
    for q in config.get("Scheduling", {}).get("SlurmQueues", []) or []:
        for cr in q.get("ComputeResources", []) or []:
            for inst in cr.get("Instances", []) or []:
                itype = inst.get("InstanceType")
                if itype:
                    types.append(itype)
    if not types:
        return None
    archs = {"arm64" if is_arm_instance(t) else "x86_64" for t in types}
    return archs.pop() if len(archs) == 1 else None


def _check_queue_arch_matches_cluster(config, instance_types):
    """Reject a new queue whose architecture differs from the running cluster.

    make_pcluster.py hard-fails on mixed head/compute architecture at build
    time, but nothing re-checked it here, so `add` could bolt a Graviton queue
    onto an x86 cluster (or vice versa). PCluster accepts that config; the
    breakage surfaces later and indirectly. Shared software built on the head
    node -- Spack packages, the benchmark suite, anything compiled with
    -march=native -- cannot execute on a foreign-architecture node, and the
    base AMI for the new queue is chosen from the cluster's single Os setting,
    which is architecture-specific.
    """
    from pcluster_aux_data import is_arm_instance

    cluster_arch = _cluster_arch(config)
    if cluster_arch is None:
        return
    new_arch = "arm64" if is_arm_instance(instance_types[0]) else "x86_64"
    if new_arch != cluster_arch:
        raise PClusterMakerError(
            f"ERROR: new queue architecture does not match this cluster.\n"
            f"  Cluster (head node and existing queues): {cluster_arch}\n"
            f"  Requested instance types: {', '.join(instance_types)} ({new_arch})\n"
            f"  A cluster runs one base OS image, which is architecture-specific,\n"
            f"  and software compiled on the head node cannot run on a foreign\n"
            f"  architecture. Build a separate cluster for {new_arch} workloads."
        )


def _is_gpu_queue(q):
    from pcluster_aux_data import is_gpu_instance

    for cr in q.get("ComputeResources", []):
        for inst in cr.get("Instances", []):
            if is_gpu_instance(inst.get("InstanceType", "")):
                return True
    return False


def _get_subnet_ids(queues, prefer_gpu=False):
    if not queues:
        raise PClusterMakerError("ERROR: no queues found in cluster config")
    if prefer_gpu:
        for q in queues:
            if _is_gpu_queue(q):
                subnets = q.get("Networking", {}).get("SubnetIds")
                if subnets:
                    return subnets
    for q in queues:
        subnets = q.get("Networking", {}).get("SubnetIds")
        if subnets:
            return subnets
    raise PClusterMakerError("ERROR: no SubnetIds found in any existing queue")


def _get_custom_actions(queues):
    for q in queues:
        if "CustomActions" in q:
            return copy.deepcopy(q["CustomActions"])
    return None


def _get_additional_iam_policies(queues):
    for q in queues:
        iam = q.get("Iam", {})
        if iam and "AdditionalIamPolicies" in iam:
            return copy.deepcopy(iam["AdditionalIamPolicies"])
    return None


def _get_root_volume_encrypted(queues):
    """Inherit the Encrypted setting from an existing queue's root volume.

    Defaults to False if no existing queue has a root volume with an
    explicit Encrypted value, matching PCluster's own default.
    """
    for q in queues:
        encrypted = (
            q.get("ComputeSettings", {})
            .get("LocalStorage", {})
            .get("RootVolume", {})
            .get("Encrypted")
        )
        if encrypted is not None:
            return bool(encrypted)
    return False


def _recovery_guidance(cluster_name, region, config_path, stage):
    """Return operator recovery steps for a failed -W (wait) queue update.

    stage is one of "update", "start". A failure after the fleet has been
    stopped leaves the cluster with zero compute capacity; without these steps
    the operator is told only that a step failed, not that jobs are queued
    behind a fleet nobody restarted.
    """
    backup = config_path + ".bak"
    lines = [
        "",
        "*** RECOVERY REQUIRED ***",
        "  The compute fleet was stopped before this step and has NOT been restarted.",
        "  No jobs will run until the fleet is back in RUNNING.",
        "",
    ]
    if stage == "update":
        lines += [
            "  1. Inspect what went wrong:",
            f"       pcluster describe-cluster --cluster-name {cluster_name} --region {region}",
            "",
            "  2. To roll the configuration back to the previous generation:",
            f"       cp {backup} {config_path}",
            f"       pcluster update-cluster --cluster-name {cluster_name} \\",
            f"         --cluster-configuration {config_path} --region {region}",
            "",
            "  3. Once the cluster reaches UPDATE_COMPLETE, restart the fleet:",
            f"       pcluster update-compute-fleet --cluster-name {cluster_name} \\",
            f"         --status START_REQUESTED --region {region}",
        ]
    else:
        lines += [
            "  1. Re-issue the fleet start:",
            f"       pcluster update-compute-fleet --cluster-name {cluster_name} \\",
            f"         --status START_REQUESTED --region {region}",
            "",
            "  2. Watch for RUNNING:",
            f"       pcluster describe-cluster --cluster-name {cluster_name} --region {region}",
        ]
    lines.append("")
    return "\n".join(lines)


def _print_update_reminder(cluster_name, region, queue_name, action):
    config_rel = f"active_clusters/{cluster_name}/config.{cluster_name}"
    print(f'\nQueue "{queue_name}" {action} in {config_rel}\n')
    print("To apply this change:\n")
    print(
        f"1. Stop the compute fleet:\n"
        f"     pcluster update-compute-fleet \\\n"
        f"       --cluster-name {cluster_name} \\\n"
        f"       --status STOP_REQUESTED \\\n"
        f"       --region {region}\n"
    )
    print(
        f"2. Wait for the fleet to reach STOPPED:\n"
        f"     pcluster describe-cluster \\\n"
        f"       --cluster-name {cluster_name} \\\n"
        f"       --region {region} | grep computeFleetStatus\n"
    )
    print(
        f"3. Apply the updated configuration:\n"
        f"     pcluster update-cluster \\\n"
        f"       --cluster-name {cluster_name} \\\n"
        f"       --cluster-configuration {config_rel} \\\n"
        f"       --region {region}\n"
    )
    print(
        f"4. Wait for the cluster update to complete (clusterStatus: UPDATE_COMPLETE,\n"
        f"   cloudFormationStackStatus: UPDATE_COMPLETE):\n"
        f"     pcluster describe-cluster \\\n"
        f"       --cluster-name {cluster_name} \\\n"
        f"       --region {region} | grep -E 'clusterStatus|cloudFormationStackStatus'\n"
    )
    print(
        f"5. Restart the compute fleet:\n"
        f"     pcluster update-compute-fleet \\\n"
        f"       --cluster-name {cluster_name} \\\n"
        f"       --status START_REQUESTED \\\n"
        f"       --region {region}"
    )


@dataclass(frozen=True)
class QueueSummary:
    name: str
    queue_type: str  # "gpu" | "compute"
    capacity_type: str
    min_count: int
    max_count: int
    instance_types: list


def core_list_queues(*, cluster_name, repo_root):
    from pcluster_aux_data import is_gpu_instance

    config, _ = _load_cluster_config(cluster_name, repo_root)
    queues = config.get("Scheduling", {}).get("SlurmQueues", [])
    out = []
    for q in queues:
        cr_list = q.get("ComputeResources", [])
        all_types = []
        min_count = 0
        max_count = 0
        for cr in cr_list:
            instances = cr.get("Instances", [])
            all_types.extend(i.get("InstanceType", "") for i in instances)
            min_count += cr.get("MinCount", 0)
            max_count += cr.get("MaxCount", 0)
        queue_type = "gpu" if any(is_gpu_instance(t) for t in all_types) else "compute"
        out.append(QueueSummary(
            name=q.get("Name", ""), queue_type=queue_type,
            capacity_type=q.get("CapacityType", ""),
            min_count=min_count, max_count=max_count, instance_types=all_types,
        ))
    return out


@dataclass(frozen=True)
class QueueAddResult:
    cluster_name: str
    queue_name: str
    region: str
    config_path: str
    gdr_capable_types: list


def core_add_queue(
    *, cluster_name, repo_root, queue_type, ec2_instance_type, queue_name=None,
    capacity_type="spot", initial_size=2, max_size=8, maintain_initial_size=False,
    root_volume_size=250, root_volume_type="gp3", root_volume_iops=3000,
    root_volume_throughput=125,
):
    # Month-day-hour-minute, not %Y%m%d-%H%M: "compute-20260725-1430" is 21
    # chars, so the derived "-resource" name overflowed PCluster's 25-char
    # limit and every auto-named queue failed at update-cluster time.
    ts = DateTime.now().strftime("%m%d%H%M")
    queue_name = queue_name or f"{queue_type}-{ts}"
    _validate_queue_name(queue_name)

    require_gpu = queue_type == "gpu"
    instance_types = _validate_instance_types(ec2_instance_type, require_gpu)

    gdr_capable_types = _gdr_capable_types(instance_types) if require_gpu else []
    if gdr_capable_types:
        print("*** INFO ***")
        print("  One or more instance types (p4d/p4de/p5) support EFA GPUDirect RDMA (GDR).")
        print("  GDR is not enabled automatically. To enable, add to the queue stanza manually:")
        print("    Efa:")
        print("      Enabled: true")
        print("      GdrSupport: true")

    config, config_path = _load_cluster_config(cluster_name, repo_root)
    queues = config["Scheduling"]["SlurmQueues"]

    _check_queue_arch_matches_cluster(config, instance_types)

    existing_names = [q.get("Name") for q in queues]
    if queue_name in existing_names:
        raise PClusterMakerError(f"ERROR: queue '{queue_name}' already exists in this cluster config")

    subnet_ids = _get_subnet_ids(queues, prefer_gpu=require_gpu)
    custom_actions = _get_custom_actions(queues)
    additional_iam = _get_additional_iam_policies(queues)
    root_volume_encrypted = _get_root_volume_encrypted(queues)
    region = config["Region"]

    capacity_type_yaml = "SPOT" if capacity_type == "spot" else "ONDEMAND"
    min_count = initial_size if maintain_initial_size else 0

    root_vol_lines = [
        f"        VolumeType: {root_volume_type}",
        f"        Size: {root_volume_size}",
        f"        Encrypted: {str(root_volume_encrypted).lower()}",
    ]
    if root_volume_type in ("gp3", "io1", "io2"):
        root_vol_lines.append(f"        Iops: {root_volume_iops}")
    if root_volume_type == "gp3":
        root_vol_lines.append(f"        Throughput: {root_volume_throughput}")
    root_vol_block = "\n".join(root_vol_lines)

    instances_block = "\n".join(
        f"        - InstanceType: {t}" for t in instance_types
    )

    subnet_ids_block = "\n".join(f"    - {s}" for s in subnet_ids)

    stanza_yaml = f"""\
Name: {queue_name}
CapacityType: {capacity_type_yaml}
Networking:
  SubnetIds:
{subnet_ids_block}
ComputeSettings:
  LocalStorage:
    RootVolume:
{root_vol_block}
ComputeResources:
  - Name: {queue_name}{COMPUTE_RESOURCE_SUFFIX}
    Instances:
{instances_block}
    MinCount: {min_count}
    MaxCount: {max_size}
    DisableSimultaneousMultithreading: false
"""
    if additional_iam is not None:
        stanza_yaml += "Iam:\n  AdditionalIamPolicies:\n"
        buf_iam = StringIO()
        _make_yaml().dump(additional_iam, buf_iam)
        for line in buf_iam.getvalue().splitlines():
            stanza_yaml += f"    {line}\n"
    if custom_actions is not None:
        stanza_yaml += "CustomActions:\n"
        buf2 = StringIO()
        _make_yaml().dump(custom_actions, buf2)
        for line in buf2.getvalue().splitlines():
            stanza_yaml += f"  {line}\n"

    new_queue = _make_yaml().load(stanza_yaml)
    queues.append(new_queue)
    _write_cluster_config(config_path, config)

    return QueueAddResult(
        cluster_name=cluster_name, queue_name=queue_name, region=region,
        config_path=config_path, gdr_capable_types=gdr_capable_types,
    )


@dataclass(frozen=True)
class QueueRemoveResult:
    cluster_name: str
    queue_name: str
    region: str
    config_path: str


def core_remove_queue(*, cluster_name, repo_root, queue_name):
    config, config_path = _load_cluster_config(cluster_name, repo_root)
    queues = config["Scheduling"]["SlurmQueues"]
    region = config["Region"]

    existing_names = [q.get("Name") for q in queues]
    if queue_name not in existing_names:
        raise PClusterMakerError(
            f"ERROR: queue '{queue_name}' not found.\n"
            f"Available queues: {', '.join(existing_names)}"
        )

    if len(queues) == 1:
        raise PClusterMakerError("ERROR: Cannot remove the last queue. A cluster must have at least one queue.")

    filtered = [q for q in queues if q.get("Name") != queue_name]
    config["Scheduling"]["SlurmQueues"] = filtered
    _write_cluster_config(config_path, config)

    return QueueRemoveResult(
        cluster_name=cluster_name, queue_name=queue_name, region=region, config_path=config_path,
    )


_UPDATE_FAIL_STATES = {
    "UPDATE_FAILED", "UPDATE_ROLLBACK_IN_PROGRESS",
    "UPDATE_ROLLBACK_COMPLETE", "UPDATE_ROLLBACK_FAILED",
}


def _poll_cluster_update(cluster_name, region, pcluster_bin):
    """Poll describe-cluster until clusterStatus and cloudFormationStackStatus
    both reach UPDATE_COMPLETE. Raises SystemExit on a failure state, timeout,
    or KeyboardInterrupt -- same shape and same timeout constants as
    _poll_fleet, which this function is deliberately kept a near-twin of
    rather than parameterized into a single generic poller: the two watch
    different field pairs with different terminal-state vocabularies, and a
    shared abstraction over that would obscure both.
    """
    try:
        for i in range(_FLEET_POLL_TIMEOUT):
            data = _describe_cluster_json(cluster_name, region)
            cs = data.get("clusterStatus", "UNKNOWN")
            cfs = data.get("cloudFormationStackStatus", "UNKNOWN")
            print(f"  [{_fleet_ts()}] clusterStatus: {cs}  cloudFormationStackStatus: {cfs}")
            if cs == "UPDATE_COMPLETE" and cfs == "UPDATE_COMPLETE":
                return
            if cs in _UPDATE_FAIL_STATES or cfs in _UPDATE_FAIL_STATES:
                raise SystemExit(
                    f"ERROR: cluster update failed (clusterStatus={cs}, cloudFormationStackStatus={cfs}).\n"
                    f"Check CloudFormation events for details."
                )
            if i == _FLEET_POLL_TIMEOUT - 1:
                raise SystemExit(
                    f"ERROR: timed out after {_FLEET_POLL_TIMEOUT * _FLEET_POLL_INTERVAL // 60} min waiting for UPDATE_COMPLETE.\n"
                    f"Check status with: pcluster describe-cluster --cluster-name {cluster_name} --region {region}"
                )
            time.sleep(_FLEET_POLL_INTERVAL)
    except KeyboardInterrupt:
        print(
            f"\nInterrupted. The cluster update is still running in AWS.\n"
            f"Check status with: pcluster describe-cluster --cluster-name {cluster_name} --region {region}"
        )
        raise SystemExit(1)


@dataclass(frozen=True)
class QueueApplyResult:
    cluster_name: str
    config_path: str


def core_apply_cluster_update(*, cluster_name, config_path, region, pcluster_bin, wait=True):
    """Phase 2 of the three-phase queue-config update: apply an updated
    cluster configuration to an existing cluster.

    Split out of core_apply_queue_config as its own callable unit for
    Workstream 4: the MCP tool surface deliberately exposes the three
    phases (stop fleet / apply config / start fleet) as three separate
    tools rather than one opaque multi-phase tool, so the calling model
    can see which phase is running and react between them -- e.g. skip
    the restart when the config apply failed. Phases 1 and 3 already had
    independently callable core functions (core_stop_fleet/
    core_start_fleet); this is the one that did not.

    Calls pcluster.lib's update_cluster via _update_cluster_lib (round
    48). It was the last mutating cluster operation still shelling out to
    the pcluster binary.

    wait=True polls to a terminal update state via _poll_cluster_update
    (kept local rather than forwarding wait= to the library, matching the
    create/delete precedent -- the library's own polling is opaque, with
    no progress output). wait=False returns straight after the update is
    accepted, for a caller that will poll itself."""
    result = _update_cluster_lib(cluster_name, region, config_path)
    print(json.dumps(result, indent=2))
    if not wait:
        return result
    print(f"[{_fleet_ts()}] Waiting for cluster update to complete...")
    _poll_cluster_update(cluster_name, region, pcluster_bin)
    return result


def core_apply_queue_config(*, cluster_record, config_path, region, pcluster_bin):
    """Stop the fleet, apply an updated cluster config, and restart the fleet
    -- the -W/--wait path of manage_pcluster_queue.py's add/remove actions.

    Reuses core_stop_fleet/core_start_fleet (wait=True) for phases 1 and 3
    rather than reimplementing fleet-status polling a third time (session 48
    finding: this script used to carry its own private, near-identical copy
    of _get_fleet_status/_poll_fleet). That reuse changes the one-time
    phase-transition status lines' exact wording from the original script's
    own [HH:MM:SS]-prefixed lines to core_stop_fleet/core_start_fleet's own
    -- the recurring per-poll progress lines an operator actually watches
    across a 30-45 minute wait are unchanged, since both paths call the same
    _poll_fleet.

    Steps 3-6 (config apply onward) run with the fleet already stopped, so
    (matching the original script) only failures from this point on print
    recovery guidance before re-raising -- a failure to stop the fleet in the
    first place leaves nothing to recover.

    Deliberately takes no wait parameter, unlike every other core function
    Workstream 4 touched. Its three phases are causally dependent --
    update-cluster requires an already-stopped fleet, and the restart
    requires a finished update -- so a wait=False that fired all three in
    sequence would apply the config to a still-running fleet and fail.
    This function IS the blocking sequence, and is what the CLI's -W flag
    calls. An async caller uses the three phases separately instead
    (core_stop_fleet, core_apply_cluster_update, core_start_fleet, each
    with its own wait), polling between them -- which the migration plan
    prefers on its own merits regardless of async: it lets the caller see
    which phase is running and react between them, such as skipping the
    restart when the config apply failed.
    """
    cluster_name = cluster_record.cluster_name

    print(
        "\n*** WARNING: this operation can take up to 30 minutes and should not be interrupted.\n"
        "    Run inside screen or tmux if there is any risk of losing your terminal session. ***\n"
    )

    print(f"[{_fleet_ts()}] Stopping compute fleet...")
    core_stop_fleet(cluster_record=cluster_record, region=region, pcluster_bin=pcluster_bin, wait=True)

    try:
        print(f"[{_fleet_ts()}] Applying updated cluster configuration...")
        core_apply_cluster_update(
            cluster_name=cluster_name, config_path=config_path,
            region=region, pcluster_bin=pcluster_bin, wait=True,
        )
    except SystemExit as e:
        if e.code not in (0, None):
            print(_recovery_guidance(cluster_name, region, config_path, "update"))
        raise

    try:
        print(f"[{_fleet_ts()}] Restarting compute fleet...")
        core_start_fleet(cluster_record=cluster_record, region=region, pcluster_bin=pcluster_bin, wait=True)
    except PClusterMakerError:
        print(_recovery_guidance(cluster_name, region, config_path, "start"))
        raise
    except SystemExit as e:
        if e.code not in (0, None):
            print(_recovery_guidance(cluster_name, region, config_path, "start"))
        raise

    print(f"[{_fleet_ts()}] Compute fleet is RUNNING. Done.")
    return QueueApplyResult(cluster_name=cluster_name, config_path=config_path)


# ---------------------------------------------------------------------------
# Cluster teardown (shared by kill_pcluster.py)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeleteClusterResult:
    cluster_name: str
    success: bool
    exit_code: int
    rebuild_command: str | None = None


def _pclib_head_ip(describe_fn, cluster_name, region):
    """boto3/pcluster.lib twin of delete_pcluster.yml's "Get head node IP
    for performance results sync" + "Set head node IP fact" task pair --
    called BEFORE delete-cluster, since the head node still exists at this
    point. Never raises: a cluster that's already gone, or that answers
    with no IP either way, just means the results sync below is skipped
    (matching the playbook's own failed_when: false + empty-string
    fallback), not that teardown aborts."""
    try:
        resp = describe_fn(cluster_name=cluster_name, region=region)
    except Exception:
        return ""
    head_node = resp.get("headNode") or {}
    return head_node.get("publicIpAddress") or head_node.get("privateIpAddress") or ""


def _sync_performance_results_to_s3(
    *, head_ip, ssh_keypair, ec2_user, ec2_user_home, cluster_name,
    cluster_serial_number, results_bucketname, region,
):
    """subprocess twin of "Push performance results from head node to S3"
    -- best-effort, matching the playbook's own rescue: block (any failure
    prints a warning and teardown proceeds; the results may be lost, but
    that must never block deleting the cluster). Never raises."""
    if not head_ip:
        return False, "no head node IP available (cluster may already be gone)"
    cmd = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=30",
        "-o", "StrictHostKeyChecking=accept-new", "-i", ssh_keypair,
        f"{ec2_user}@{head_ip}",
        "aws", "s3", "sync",
        f"{ec2_user_home}/hpc-benchmark/{cluster_name}/",
        f"s3://{results_bucketname}/hpc-benchmark-results/{cluster_name}/{cluster_serial_number}/",
        "--exclude", "*.pyc", "--exclude", "__pycache__/*",
        "--region", region,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except Exception as e:
        return False, str(e)
    if result.returncode != 0:
        return False, f"rc={result.returncode}: {result.stderr.strip()}"
    return True, ""


def _collect_orphaned_resources(step_results):
    """boto3 twin of "Collect cleanup failures that ignore_errors
    swallowed" -- every step's own .name already names its resource (it's
    the playbook's own task name), so a failed step's name plus its detail
    is the orphan entry. A skipped-but-not-attempted credential step
    (succeeded=True with a "skipped: ..." detail, from an unconfirmed
    delete) is correctly excluded, since .succeeded is True there."""
    return [
        f"{r.name} -- {r.detail}" if r.detail else r.name
        for r in step_results
        if not r.succeeded
    ]


def _collect_retained_resources(
    *, s3_bucketname, delete_s3_bucketname, enable_hpc_benchmarks,
    results_bucketname, cluster_name,
):
    """boto3 twin of "Collect the resources this teardown deliberately
    retained" -- these are choices, not failures, and must never reach
    _orphaned_resources."""
    retained = []
    if not delete_s3_bucketname:
        retained.append(
            f"S3 bucket {s3_bucketname} (--delete_s3_bucketname was not true)"
        )
    if enable_hpc_benchmarks:
        retained.append(
            f"S3 bucket {results_bucketname} (benchmark results, shared by "
            f"every cluster in this account and region)"
        )
    retained.append(
        f"CloudWatch log groups /aws/parallelcluster/{cluster_name}-* "
        f"(retained 180 days; the only record of a failed build)"
    )
    return retained


def _publish_destruction_report(sns, topic_arn, message, subject):
    """boto3 twin of "Distribute the cluster destruction summary report
    via SNS" -- best-effort (ignore_errors: true in the playbook); a
    publish failure is warned, never fails teardown and never appears in
    the orphan list (only the topic *deletion* below does)."""
    try:
        sns.publish(TopicArn=topic_arn, Message=message, Subject=subject)
    except Exception as e:
        return False, str(e)
    return True, ""


def _delete_sns_topic_step(sns, topic_arn):
    """boto3 twin of "Delete the SNS topic associated with this cluster"
    -- unlike every other AWS delete in this module, SNS's own DeleteTopic
    is documented idempotent (no error deleting an ARN that was never
    created), so this needs no NotFound special-casing."""
    step_name = "Delete the SNS topic associated with this cluster"
    try:
        sns.delete_topic(TopicArn=topic_arn)
    except Exception as e:
        return TeardownStepResult(step_name, False, str(e))
    return TeardownStepResult(step_name, True)


def _format_destruction_summary(
    *, cluster_name, start_ts, stop_ts, delete_headline,
    orphaned_resources, retained_resources,
):
    """boto3 twin of "Print cluster deletion summary" / "Print cluster
    deletion summary with the resources that survived cleanup" -- one
    function, not two, since the only difference between the playbook's
    pair is whether _orphaned_resources is empty."""
    sep = "=" * 80
    lines = [sep, "", f"Initiated shutdown: {start_ts}", f"Completed shutdown: {stop_ts}", "", delete_headline]
    if orphaned_resources:
        lines += [
            f"{len(orphaned_resources)} cleanup step(s) FAILED.",
            "The following resources are still in the account and must be",
            "removed by hand -- re-running kill_pcluster.py will not retry them",
            f"once {cluster_name}.serial has been deleted:",
            "",
        ]
        lines += [f"  - {r}" for r in orphaned_resources]
    if retained_resources:
        lines += [
            "",
            "Retained in the account on purpose (not failures, still billing):"
            if orphaned_resources
            else "Retained in the account (not deleted, still billing):",
            "",
        ]
        lines += [f"  - {r}" for r in retained_resources]
    lines.append(sep)
    return lines


def core_delete_cluster(
    *, cluster_name, cluster_owner, region, repo_root,
    delete_s3_bucketname, debug_mode, wait=True,
):
    """Tear down a cluster via boto3/pcluster.lib, replacing the
    ansible-playbook subprocess call to delete_pcluster.yml -- which stays
    in the repo as the reference spec every function this calls (and every
    docstring above) was built to replicate; its task names and positions
    are cited throughout for exactly that reason.

    Every failure this function itself detects (missing serial/vars file,
    DELETE_FAILED, an unconfirmed delete, resources left behind after
    cleanup) is reported by *returning* DeleteClusterResult(success=False,
    exit_code=1) rather than raising, matching the original script's own
    bare-integer sys.exit() codes -- there is no PClusterMakerError message
    to preserve. The AZ-verification and Turbot-profile-switch that must
    precede this call, and the operator's Ctrl-C abort window, stay in the
    CLI shim -- the former because it must run before the account/region
    this function operates in is even decided, the latter matching every
    other migrated script's confirmation gate. The CLI shim also duplicates
    the validate/file-existence/serial-read steps below, purely to display
    what's about to happen before that abort window -- this function
    independently re-derives and re-runs all of it afterward, the same
    "preflight for display, core re-derives before acting" tradeoff already
    used for stop_pcluster.py/start_pcluster.py and manage_pcluster_
    queue.py's -W flow.
    """
    from pcluster_aux_data import p_val
    import pcluster.lib as pc

    _validate_cluster_name(cluster_name)
    _validate_cluster_owner(cluster_owner)

    src_dir = os.path.join(repo_root, "src")

    try:
        pc.describe_cluster(cluster_name=cluster_name, region=region)
        p_val("cluster_name", debug_mode)
    except Exception:
        print("")
        print("*** WARNING ***")
        print(f'Cluster stack "{cluster_name}" was not found in {region}!')
        print("")
        print("Continuing with stack artifact destruction...")

    active_clusters_root = os.path.join(repo_root, "active_clusters")
    cluster_data_dir = os.path.join(active_clusters_root, cluster_name)
    cluster_serial_number_file = os.path.join(cluster_data_dir, cluster_name + ".serial")
    vars_file_path = os.path.join(src_dir, "vars_files", cluster_name + ".yml")

    if os.path.isfile(cluster_serial_number_file):
        p_val("cluster_serial_number_file", debug_mode)
    else:
        print("")
        print("*** ERROR ***")
        print("Missing cluster_serial_number_file: " + cluster_serial_number_file)
        print("Aborting...")
        return DeleteClusterResult(cluster_name=cluster_name, success=False, exit_code=1)

    if os.path.isfile(vars_file_path):
        p_val("vars_file_path", debug_mode)
    else:
        print("")
        print("*** ERROR ***")
        print("Missing vars_file_path: " + vars_file_path)
        print("Aborting...")
        return DeleteClusterResult(cluster_name=cluster_name, success=False, exit_code=1)

    # A throwaway peek at aws_account_id, read before the lock, purely to
    # derive the locks bucket's name -- the canonical, lock-protected read
    # of cluster_vars below is what everything else in this function acts
    # on, preserving the original concurrency-safety property the local
    # lock existed for (a concurrent kill_pcluster.py must not delete this
    # file out from under an in-flight reader).
    with open(vars_file_path) as fh:
        aws_account_id = (yaml.safe_load(fh) or {}).get("aws_account_id", "")
    _lock_s3 = boto3.client("s3", region_name=region)
    locks_bucketname = _derive_locks_bucket(aws_account_id=aws_account_id, region=region)
    _lock_path = _acquire_distributed_cluster_lock(
        _lock_s3, locks_bucketname=locks_bucketname, region=region,
        cluster_name=cluster_name, command="kill_pcluster.py",
        describe_fn=pc.describe_cluster,
    )
    try:
        cluster_serial_number = _read_serial_first_line(cluster_serial_number_file)
        with open(vars_file_path) as fh:
            cluster_vars = yaml.safe_load(fh) or {}

        def _v(key, default=""):
            return cluster_vars.get(key, default)

        def _vbool(key):
            return _v(key) == "true"

        aws_account_id = _v("aws_account_id")
        az = _v("az")
        ec2_iam_policy = _v("ec2_iam_policy")
        ec2_iam_role = _v("ec2_iam_role")
        ec2_keypair = _v("ec2_keypair")
        ec2_user = _v("ec2_user")
        ec2_user_home = _v("ec2_user_home")
        enable_external_nfs = _vbool("enable_external_nfs")
        enable_fsx_hydration = _vbool("enable_fsx_hydration")
        enable_hpc_benchmarks = _vbool("enable_hpc_benchmarks")
        enable_monitoring = _vbool("enable_monitoring")
        fsx_hydration_iam_policy = _v("fsx_hydration_iam_policy")
        s3_bucketname = _v("s3_bucketname")
        ssh_keypair = _v("ssh_keypair")
        ssh_secret_name = _v("ssh_secret_name")
        _delete_s3_bucketname_bool = delete_s3_bucketname == "true"
        # results_bucketname is newer than enable_hpc_benchmarks -- an older
        # vars file can carry the gate without the key. Same derivation the
        # playbook falls back to, not a restated literal (see CLAUDE.md).
        results_bucketname = _v("results_bucketname") or _derive_results_bucket(
            aws_account_id=aws_account_id, region=region,
        )

        _rebuild_cmd = _extract_rebuild_command(cluster_serial_number_file)
        _isdir_cdd = os.path.isdir(cluster_data_dir)

        start_ts = teardown_timestamp()
        print("")
        print("=" * 65)
        print(f"Destroying: {cluster_name} in {az}")
        print(f"Start Time: {start_ts}")
        print("")
        print("This process will take approximately 5-10 minutes to complete.")
        print("=" * 65)
        print("")

        if enable_hpc_benchmarks:
            _head_ip = _pclib_head_ip(pc.describe_cluster, cluster_name, region)
            _sync_ok, _sync_detail = _sync_performance_results_to_s3(
                head_ip=_head_ip, ssh_keypair=ssh_keypair, ec2_user=ec2_user,
                ec2_user_home=ec2_user_home, cluster_name=cluster_name,
                cluster_serial_number=cluster_serial_number,
                results_bucketname=results_bucketname, region=region,
            )
            if not _sync_ok:
                print("")
                print("*** WARNING ***")
                print(f"Could not sync performance results to S3 ({_sync_detail}).")
                print(
                    f"Results under {ec2_user_home}/hpc-benchmark/{cluster_name}/ "
                    f"on the head node may be lost."
                )
                print("Proceeding with cluster deletion.")

        def _print_teardown_progress(attempt, status, cfn_status):
            """Same scoped, disclosed exception as the create side's own
            progress printer: this wait was previously entirely silent."""
            detail = f" (CloudFormation: {cfn_status})" if cfn_status else ""
            print(
                f"  [{(attempt + 1) * 30 // 60:>3d}m] {cluster_name}: "
                f"{status or 'status unavailable'}{detail}"
            )

        outcome = run_cluster_delete_and_classify(
            pc.delete_cluster, pc.describe_cluster, cluster_name, region,
            progress_fn=_print_teardown_progress, wait=wait,
        )

        if outcome.terminal_state == _KICKED_OFF:
            # Workstream 4: the caller asked to initiate the delete without
            # waiting, so the CloudFormation stack is still going away and
            # nothing about it is confirmed. Every teardown step below --
            # not just the credential-destroying ones -- is deliberately
            # skipped rather than run against a live stack: deleting the
            # IAM role or the S3 bucket out from under a still-deleting
            # stack is exactly how a DELETE_FAILED gets manufactured. The
            # local serial/vars files are preserved too, since they are
            # what a follow-up run needs to finish the job.
            print("")
            print(f"Deletion of cluster '{cluster_name}' was initiated and NOT waited on.")
            print("Nothing has been cleaned up yet -- the stack is still deleting.")
            print("Poll until the stack is gone, then re-run to finish teardown:")
            print(f"  pcluster describe-cluster --cluster-name {cluster_name} --region {region}")
            print(f"  ./kill_pcluster.py -N {cluster_name} -O {cluster_owner} -A {az}")
            return DeleteClusterResult(
                cluster_name=cluster_name, success=True, exit_code=0,
                rebuild_command=_rebuild_cmd,
            )

        if outcome.terminal_state == _TIMED_OUT:
            print("")
            print("*** WARNING ***")
            print("Cluster deletion wait timed out -- cluster may still be deleting.")
            print("Check the CloudFormation console before assuming the cluster is gone:")
            print(f"  aws cloudformation describe-stacks --stack-name parallelcluster-{cluster_name}")
            print("Re-run kill_pcluster.py once the stack reaches DELETE_COMPLETE.")
            print("")
            print("*** WARNING ***")
            print(f"Deletion of cluster '{cluster_name}' was never confirmed, so the")
            print("SSH keypair, the local .pem file, and the Secrets Manager secret are PRESERVED.")
            print("Every other resource is still cleaned up below.")
        if outcome.cf_delete_failed:
            print("")
            print("*** WARNING ***")
            print(f"CloudFormation stack deletion reached DELETE_FAILED for cluster '{cluster_name}'.")
            print("This usually means a resource (ENI, security group, EFA interface) still has dependencies.")
            print("IAM roles, SSM parameters, and S3 resources will still be cleaned up below.")
            print("The SSH keypair, local .pem file, and Secrets Manager secret are preserved so the")
            print("head node stays reachable for manual troubleshooting until the stack is fully deleted.")

        ec2 = boto3.client("ec2", region_name=region)
        iam = boto3.client("iam")
        s3 = boto3.client("s3", region_name=region)
        ssm = boto3.client("ssm", region_name=region)
        secretsmanager = boto3.client("secretsmanager", region_name=region)
        sns = boto3.client("sns", region_name=region)

        credential_results = run_credential_teardown_steps(
            cf_delete_confirmed=outcome.cf_delete_confirmed,
            ec2=ec2, secretsmanager=secretsmanager,
            ec2_keypair=ec2_keypair, ssh_keypair=ssh_keypair,
            ssh_secret_name=ssh_secret_name, cluster_data_dir=cluster_data_dir,
        )
        resource_results = run_resource_teardown_steps(
            s3=s3, iam=iam, ssm=ssm, ec2=ec2, cluster_name=cluster_name,
            ec2_iam_role=ec2_iam_role, ec2_iam_policy=ec2_iam_policy,
            aws_account_id=aws_account_id, s3_bucketname=s3_bucketname,
            delete_s3_bucketname=_delete_s3_bucketname_bool,
            enable_fsx_hydration=enable_fsx_hydration,
            fsx_hydration_iam_policy=fsx_hydration_iam_policy,
            enable_monitoring=enable_monitoring,
            enable_external_nfs=enable_external_nfs,
        )

        _retained_resources = _collect_retained_resources(
            s3_bucketname=s3_bucketname, delete_s3_bucketname=_delete_s3_bucketname_bool,
            enable_hpc_benchmarks=enable_hpc_benchmarks,
            results_bucketname=results_bucketname, cluster_name=cluster_name,
        )
        stop_ts = teardown_timestamp()

        # The SNS topic is deleted after the report is sent, so its own
        # failure cannot appear in that report -- the report below is built
        # from credential+resource results only, matching that ordering.
        sns_topic_arn = f"arn:aws:sns:{region}:{aws_account_id}:sns_alerts_{cluster_name}"
        if _isdir_cdd:
            _orphaned_for_report = _collect_orphaned_resources(credential_results + resource_results)
            report = render_template(
                os.path.join(repo_root, "templates"), "sns_destruction_summary_report.j2",
                cluster_name=cluster_name,
                _delete_headline=outcome.delete_headline,
                start_delete_timer={"stdout": start_ts},
                stop_delete_timer={"stdout": stop_ts},
                az=az, cluster_owner=cluster_owner,
                cluster_serial_number=cluster_serial_number,
                _orphaned_resources=_orphaned_for_report,
                _retained_resources=_retained_resources,
            )
            _report_path = f"/tmp/sns_destruction_summary.{cluster_name}.txt"
            with open(
                os.open(_report_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644), "w"
            ) as fh:
                fh.write(report)
            _publish_destruction_report(
                sns, sns_topic_arn, report, f"Cluster Destruction Notice: {cluster_name}",
            )

        sns_topic_result = _delete_sns_topic_step(sns, sns_topic_arn)
        orphaned_resources = _collect_orphaned_resources(
            credential_results + resource_results + [sns_topic_result]
        )

        print("")
        for line in _format_destruction_summary(
            cluster_name=cluster_name, start_ts=start_ts, stop_ts=stop_ts,
            delete_headline=outcome.delete_headline,
            orphaned_resources=orphaned_resources,
            retained_resources=_retained_resources,
        ):
            print(line)

        if outcome.cf_delete_failed:
            print("")
            print("*** ERROR ***")
            print(f"CloudFormation stack deletion reached DELETE_FAILED for cluster '{cluster_name}'.")
            print("IAM, SSM, and S3 resources have been cleaned up.")
            print("The SSH keypair, local .pem file, and Secrets Manager secret were preserved so the")
            print("head node remains reachable for troubleshooting.")
            print("Resolve the remaining CloudFormation dependency and re-run kill_pcluster.py if needed.")
            return DeleteClusterResult(cluster_name=cluster_name, success=False, exit_code=1)

        if not outcome.cf_delete_confirmed:
            print("")
            print("*** ERROR ***")
            print(f"Deletion of cluster '{cluster_name}' was never confirmed.")
            print("The stack reached neither DELETE_COMPLETE nor DELETE_FAILED, so it may")
            print("still be deleting -- and it may still be billing.")
            print("Every other resource was cleaned up; the SSH keypair, the local .pem")
            print("file, and the Secrets Manager secret were PRESERVED so the head node")
            print("stays reachable.")
            print("Confirm the stack is gone, then re-run to remove the credentials:")
            print(f"  pcluster describe-cluster --cluster-name {cluster_name} --region {region}")
            print(f"  ./kill_pcluster.py -N {cluster_name}")
            return DeleteClusterResult(cluster_name=cluster_name, success=False, exit_code=1)

        if orphaned_resources:
            print("")
            print("*** ERROR ***")
            print(
                f"Teardown of '{cluster_name}' left {len(orphaned_resources)} resource(s) "
                f"in the account. See the summary above for the list. These were reported "
                f"as ignored failures, not skipped steps -- the account is not clean."
            )
            return DeleteClusterResult(cluster_name=cluster_name, success=False, exit_code=1)

        line_length = 80
        print("".center(line_length, "="))
        if _rebuild_cmd:
            print("To rebuild the cluster:")
            print("")
            print(_rebuild_cmd)
        print("".center(line_length, "="))
        print("")
        with contextlib.suppress(FileNotFoundError):
            os.remove(cluster_serial_number_file)
            print("Removed  ===> " + cluster_serial_number_file)
        with contextlib.suppress(FileNotFoundError):
            os.remove(vars_file_path)
            print("Removed  ===> " + vars_file_path)

        print("")
        print("Finished deleting cluster stack " + cluster_name + "!")
        print("Exiting...")
        return DeleteClusterResult(
            cluster_name=cluster_name, success=True, exit_code=0,
            rebuild_command=_rebuild_cmd,
        )
    finally:
        s3_release_cluster_lock(_lock_s3, locks_bucketname=locks_bucketname, cluster_name=cluster_name)


# ---------------------------------------------------------------------------
# Cluster build (shared by make_pcluster.py)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MakeClusterParams:
    """Every make_pcluster.py CLI flag, fully resolved (CLI > defaults file >
    hardcoded default) and, where the original script derives a value during
    resolution -- loginnode_instance_type's architecture-aware fallback,
    stage_docker_compose/docker_compose_arch/docker_compose_checksum,
    head_node_bootstrap_timeout -- already in its final form. Built by the
    CLI shim, which also owns cluster_name/cluster_owner/cluster_owner_email
    validation, the Ansible version check, AZ verification, and the Turbot
    profile switch: all of that must run (in that order) before region is
    known and before any AWS call in this function's namespace uses the
    right credentials, so it cannot be folded into this dataclass or into
    core_create_cluster itself. az itself is included for downstream network
    discovery; the region core_create_cluster actually operates in is a
    separate parameter, resolved by the shim from the AZ-verification call.
    """

    ansible_verbosity: str
    az: str
    base_os: str
    cluster_name: str
    cluster_owner: str
    cluster_owner_department: str
    cluster_owner_email: str
    cluster_type: str
    compute_instance_type: str
    compute_root_volume_size: int
    compute_root_volume_type: str
    compute_root_volume_iops: int
    compute_root_volume_throughput: int
    custom_ami: str
    debug_mode: bool
    ebs_encryption: bool
    ebs_shared_dir: str
    ebs_shared_volume_size: int
    ebs_shared_volume_type: str
    ebs_shared_volume_iops: int
    ebs_shared_volume_throughput: int
    efs_encryption: str
    efs_performance_mode: str
    efs_throughput_mode: str
    enable_efa: bool
    enable_efs: bool
    enable_external_nfs: bool
    enable_loginnode: bool
    loginnode_instance_type: str
    loginnode_count: int
    gpu_instance_type: str
    gpu_root_volume_size: int
    gpu_root_volume_type: str
    gpu_root_volume_iops: int
    gpu_root_volume_throughput: int
    enable_fsx: bool
    enable_fsx_hydration: bool
    enable_hpc_benchmarks: bool
    enable_monitoring: bool
    monitoring_version: str
    monitoring_version_checksum: str
    docker_compose_version: str
    stage_docker_compose: bool
    docker_compose_arch: str
    docker_compose_checksum: str
    external_nfs_server: str
    fsx_chunk_size: int
    fsx_s3_export_bucket: str
    fsx_s3_export_path: str
    fsx_s3_import_bucket: str
    fsx_s3_import_path: str
    fsx_size: int
    head_node_bootstrap_timeout: int
    configured_head_node_bootstrap_timeout: int
    hyperthreading: bool
    initial_cpu_queue_size: int
    initial_gpu_queue_size: int
    maintain_cpu_initial_size: bool
    maintain_gpu_initial_size: bool
    headnode_instance_type: str
    headnode_root_volume_size: int
    headnode_root_volume_type: str
    headnode_root_volume_iops: int
    headnode_root_volume_throughput: int
    max_cpu_queue_size: int
    max_gpu_queue_size: int
    placement_group: str
    pre_install_script: str
    post_install_script: str
    prod_level: str
    project_id: str
    pcluster_create_timeout: int
    scaledown_idletime: int
    scheduler: str
    turbot_account: str
    vpc_name: str
    headnode_subnet_id: str
    loginnode_subnet_id: str
    compute_az_list: list
    compute_subnet_ids_override: str
    use_private_compute_subnet: str
    gpu_az_list: list
    gpu_subnet_ids_override: str
    use_private_gpu_subnet: str


# ---------------------------------------------------------------------------
# Workstream 3, create-side migration: the first slice, matching the plan's
# own risk-ordered task table for create_pcluster.yml's 80 tasks. This round
# covers only the task the plan calls "Trivial": the OS assert (task index
# 0), the one piece of create_pcluster.yml with no Python equivalent at all
# today. The timer/local-state-dir tasks that follow it in the playbook
# already have a Python equivalent (core_create_cluster's own existing
# os.makedirs(cluster_data_dir, ...) call, fixed in place below to also
# chmod -- see that call site's own comment) rather than needing a new
# function. Everything else in that file -- the S3/keypair/secret block, the
# create-cluster launch+wait+classify, the SSH/SCP orchestration to the head
# node -- is real implementation work per the plan's own table and is
# deliberately left for later rounds.
# ---------------------------------------------------------------------------


def _assert_supported_os(base_os):
    """boto3/Python twin of create_pcluster.yml's "Assert that base_os and
    pcluster_os are supported" task -- must stay the first statement
    core_create_cluster executes, exactly mirroring the playbook's own
    "must stay task index 0" invariant (CLAUDE.md), just structural (first
    line of code) instead of needing a dedicated task-index test.

    This closes a real gap, not just a lateral port: make_pcluster.py's
    argparse `choices=` already gates the CLI path, but nothing in Python
    rejects an unsupported base_os if core_create_cluster is ever called
    directly (a future MCP tool, a test, a differently-argparse'd caller)
    -- exactly the argparse bypass CLAUDE.md documents this assert exists
    to catch on the Ansible side, and the reason the playbook kept its own
    copy of this check rather than treating argparse as sufficient.
    `ARM_OSES`/`X86_OSES` (pcluster_aux_data.py) are the single source of
    truth for the eight supported base_os values, not a third copy of the
    literal list; pcluster_os is checked too, even though today's
    `removesuffix("arm")` derivation makes it mathematically implied by the
    base_os check, so a future change to that derivation still gets caught
    here rather than only in the playbook.

    Raises via refer_to_docs_and_quit (a printed message + bare
    sys.exit(1)), not PClusterMakerError -- core_create_cluster's own
    docstring documents that it deliberately keeps every validation error
    in this raw sys.exit() shape rather than a typed exception nothing in
    make_pcluster.py catches; a new PClusterMakerError here would surface
    as an uncaught traceback instead of the clean error every other
    validation failure in this function already produces."""
    from pcluster_aux_data import ARM_OSES, X86_OSES, refer_to_docs_and_quit

    pcluster_os = base_os.removesuffix("arm")
    if base_os not in (ARM_OSES + X86_OSES) or pcluster_os not in X86_OSES:
        refer_to_docs_and_quit(
            f"Unsupported OS: base_os={base_os}, pcluster_os={pcluster_os}.\n"
            f"preinstall.j2 and postinstall.j2 branch on 'ubuntu' in base_os "
            f"and select a package manager accordingly; any other value "
            f"renders a script with the wrong one and fails the node "
            f"bootstrap.\n"
            f"Valid base_os values are {', '.join(ARM_OSES + X86_OSES)}."
        )


# ---------------------------------------------------------------------------
# Workstream 3, create-side migration, second slice: create_pcluster.yml's
# "Create S3 bucket and EC2 keypair, clean up on failure" block, with its
# rescue:. The plan calls this the best-case mapping in the whole file --
# Ansible's block:/rescue: is structurally try:/except: in Python -- but
# flags ec2_private_key.changed as real translation work, since boto3's
# create_key_pair has no equivalent existing-vs-created flag; two downstream
# steps (the orphaned-keypair abort, and whether to save the returned key
# material at all) depend on that fact precisely.
#
# Built and tested standalone this round, matching the same "build first,
# wire later" split used for the teardown migration (rounds 16-18 built,
# round 19 wired): create_pcluster.yml still runs this block via Ansible
# today, and several existing tests in tests/test_templates.py pin
# hard-won safety properties of that Ansible implementation directly
# (no_log on every task touching key material, the Secrets Manager write
# staying ungated on ec2_private_key.changed, task ordering). Wiring this
# in means removing that block from create_pcluster.yml and updating or
# replacing those tests to pin the same properties against this code
# instead -- deliberately a separate, later round, not bundled with
# writing the functions themselves.
# ---------------------------------------------------------------------------


def _create_s3_bucket_for_cluster(s3, *, s3_bucketname, region, tags):
    """boto3 twin of create_pcluster.yml's "Create s3_bucketname..." task
    pair (project_id == "UNDEFINED" vs not) -- one function taking a
    caller-built tag dict instead of two near-duplicate Ansible tasks,
    closing exactly the duplication pattern CLAUDE.md documents elsewhere
    as a past bug source (transposed pairs surviving in an untested twin
    of a real code path). Idempotent: a bucket this account already owns
    (a resumed, interrupted build reusing the same serial-derived name) is
    not an error, matching amazon.aws.s3_bucket's own state: present
    semantics. CreateBucketConfiguration must be omitted for us-east-1 --
    S3 rejects an explicit LocationConstraint naming the default region."""
    kwargs = {"Bucket": s3_bucketname}
    if region != "us-east-1":
        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
    try:
        s3.create_bucket(**kwargs)
    except _ClientError as e:
        if e.response["Error"]["Code"] != "BucketAlreadyOwnedByYou":
            raise
    s3.put_bucket_tagging(
        Bucket=s3_bucketname,
        Tagging={"TagSet": [{"Key": k, "Value": v} for k, v in tags.items()]},
    )


_EXTERNAL_NFS_PORTS = (111, 2049, 4045, 4046, 4047)


def _create_external_nfs_sg(ec2, *, cluster_name, vpc_id, vpc_cidr):
    """boto3 twin of "Create a new security group for mounting external
    NFS file systems". Idempotent by name within the VPC, matching
    amazon.aws.ec2_security_group: an InvalidGroup.Duplicate error means a
    previous run already created it, so this looks the existing group up
    instead of failing. Returns the group_id (create_pcluster.yml's own
    register: external_nfs_sg.group_id) for the caller to thread into
    config.pcluster.j2's render context once that template moves off
    Ansible-side rendering -- not this round's scope."""
    group_name = f"pcluster-{cluster_name}-externalNfs"
    try:
        resp = ec2.create_security_group(
            GroupName=group_name,
            Description="Permit NFS traffic to/from external NFS file systems",
            VpcId=vpc_id,
        )
        group_id = resp["GroupId"]
    except _ClientError as e:
        if e.response["Error"]["Code"] != "InvalidGroup.Duplicate":
            raise
        existing = ec2.describe_security_groups(
            Filters=[
                {"Name": "group-name", "Values": [group_name]},
                {"Name": "vpc-id", "Values": [vpc_id]},
            ]
        )
        return existing["SecurityGroups"][0]["GroupId"]

    cidr = vpc_cidr or "10.0.0.0/8"
    permissions = [
        {"IpProtocol": proto, "FromPort": port, "ToPort": port, "IpRanges": [{"CidrIp": cidr}]}
        for proto in ("tcp", "udp")
        for port in _EXTERNAL_NFS_PORTS
    ]
    ec2.authorize_security_group_ingress(GroupId=group_id, IpPermissions=permissions)
    return group_id


def _generate_ec2_keypair(ec2, *, ec2_keypair):
    """boto3 twin of "Generate a new EC2 keypair for this cluster".
    Returns (changed, private_key_pem) -- changed mirrors Ansible's
    ec2_private_key.changed fact, which two downstream steps depend on
    precisely: a keypair AWS already had returns (False, None), since AWS
    never returns key material for an existing keypair; a freshly minted
    one returns (True, <pem>). Never logs or otherwise surfaces
    private_key_pem -- the Python discipline standing in for no_log."""
    try:
        resp = ec2.create_key_pair(KeyName=ec2_keypair, KeyType="ed25519")
    except _ClientError as e:
        if e.response["Error"]["Code"] != "InvalidKeyPair.Duplicate":
            raise
        return False, None
    return True, resp["KeyMaterial"]


class _ClusterProvisioningError(Exception):
    """Raised within provision_s3_keypair_and_secret's own try: block;
    caught there (along with any other exception the block raises) to run
    rescue-style cleanup before re-raising, exactly like the playbook's
    block:/rescue:."""


def _abort_if_keypair_orphaned(*, changed, local_pem_exists, ec2_keypair, region):
    """boto3 twin of "Abort if keypair exists in AWS but local key file is
    missing". AWS never returns key material for an existing keypair, so
    this combination means the .pem is unrecoverable from AWS -- the
    operator must delete the keypair and rebuild, or restore the file from
    elsewhere."""
    if not changed and not local_pem_exists:
        raise _ClusterProvisioningError(
            f"EC2 keypair '{ec2_keypair}' already exists in AWS but the local "
            f"private key file is absent. AWS never returns key material for "
            f"an existing keypair. Delete the keypair from EC2 and re-run, or "
            f"restore the .pem file:\n"
            f"  aws ec2 delete-key-pair --key-name {ec2_keypair} --region {region}"
        )


def _save_private_key_locally(ssh_keypair, private_key_pem):
    """boto3 twin of "Save the private key" (copy, mode: "0600"). Only
    called when _generate_ec2_keypair reported changed=True -- AWS
    populates key material only when it actually minted a new keypair;
    calling this when it did not would overwrite a good .pem with
    nothing."""
    with open(
        os.open(ssh_keypair, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w"
    ) as fh:
        fh.write(private_key_pem)


def _store_ssh_secret(secretsmanager, *, ssh_secret_name, cluster_name, ssh_keypair):
    """boto3 twin of "Store SSH private key in Secrets Manager". Must run
    unconditionally -- not gated on whether this run minted a new keypair
    -- matching the fix TestTheSshSecretIsWrittenOnEveryRun
    (tests/test_templates.py) pins on the Ansible side: gating this on
    "changed" left a cluster whose first build failed after the keypair
    existed but before the secret was written with no secret at all, and
    no way back into the head node on retrieve_ssh_key.<cluster>.sh's
    documented recovery path. Reads the *local* .pem rather than key
    material passed in, since on an ungated run there may be none for this
    call (a resumed build's keypair already existed). Tolerates
    ResourceExistsException as success, the same tolerance that makes
    running this unconditionally safe on a rebuild."""
    with open(ssh_keypair) as fh:
        private_key_pem = fh.read()
    try:
        secretsmanager.create_secret(
            Name=ssh_secret_name,
            Description=f"SSH private key for ParallelCluster {cluster_name}",
            SecretString=private_key_pem,
            Tags=[{"Key": "parallelcluster:cluster-name", "Value": cluster_name}],
        )
    except _ClientError as e:
        if e.response["Error"]["Code"] != "ResourceExistsException":
            raise


def _cleanup_after_provisioning_failure(
    *, s3, ec2, secretsmanager, s3_bucketname, ec2_keypair, ssh_secret_name,
    cluster_name, external_nfs_sg_enabled,
):
    """boto3 twin of the block's rescue: -- reuses the teardown-side step
    functions (_delete_s3_bucket_step, _delete_ec2_keypair_step,
    _delete_secrets_manager_secret_step, _delete_external_nfs_sg_step)
    rather than re-implementing the same four AWS deletes a second time;
    each already tolerates the resource being absent and never raises.
    Matches ignore_errors: true on all four Ansible rescue tasks: one
    cleanup failure must not prevent the others from being attempted.
    external_nfs_sg_enabled, not "was one created this run": the playbook's
    own rescue task gates purely on enable_external_nfs, so a failure on
    (say) the keypair step still deletes a security group a *previous*
    interrupted attempt already created -- the whole block is meant to
    roll back atomically, all four resource types or none, not only
    whichever this particular attempt happened to reach."""
    _delete_s3_bucket_step(s3, s3_bucketname)
    _delete_ec2_keypair_step(ec2, ec2_keypair)
    _delete_secrets_manager_secret_step(secretsmanager, ssh_secret_name)
    if external_nfs_sg_enabled:
        _delete_external_nfs_sg_step(ec2, cluster_name)


def provision_s3_keypair_and_secret(
    *, s3, ec2, secretsmanager, s3_bucketname, region, tags,
    enable_external_nfs, cluster_name, vpc_id, vpc_cidr,
    ec2_keypair, ssh_keypair, ssh_secret_name,
):
    """The single entry point a future core_create_cluster wiring calls:
    create_pcluster.yml's "Create S3 bucket and EC2 keypair, clean up on
    failure" block, translated structurally into try:/except: -- the
    "best-case mapping in the whole file" per the migration plan. Returns
    external_nfs_sg_id (None if enable_external_nfs is false) for the
    caller to thread into config.pcluster.j2's render context, matching
    what the playbook's own register: external_nfs_sg supplies today. On
    any failure, cleans up everything the block is responsible for, then
    re-raises the original exception unchanged -- matching the playbook's
    "Re-raise the failure after cleanup" task, which does not swallow or
    reword the underlying error."""
    external_nfs_sg_id = None
    try:
        _create_s3_bucket_for_cluster(
            s3, s3_bucketname=s3_bucketname, region=region, tags=tags,
        )
        if enable_external_nfs:
            external_nfs_sg_id = _create_external_nfs_sg(
                ec2, cluster_name=cluster_name, vpc_id=vpc_id, vpc_cidr=vpc_cidr,
            )

        changed, private_key_pem = _generate_ec2_keypair(ec2, ec2_keypair=ec2_keypair)
        local_pem_exists = os.path.isfile(ssh_keypair)
        _abort_if_keypair_orphaned(
            changed=changed, local_pem_exists=local_pem_exists,
            ec2_keypair=ec2_keypair, region=region,
        )
        if changed:
            _save_private_key_locally(ssh_keypair, private_key_pem)

        _store_ssh_secret(
            secretsmanager, ssh_secret_name=ssh_secret_name,
            cluster_name=cluster_name, ssh_keypair=ssh_keypair,
        )
    except Exception:
        _cleanup_after_provisioning_failure(
            s3=s3, ec2=ec2, secretsmanager=secretsmanager,
            s3_bucketname=s3_bucketname, ec2_keypair=ec2_keypair,
            ssh_secret_name=ssh_secret_name, cluster_name=cluster_name,
            external_nfs_sg_enabled=enable_external_nfs,
        )
        raise

    return external_nfs_sg_id


# ---------------------------------------------------------------------------
# Workstream 3, create-side migration, third slice: create_pcluster.yml's
# "Stage and upload monitoring post-install wrapper" block -- the monitoring
# tarball and Docker Compose CLI plugin checksum-verified downloads, plus
# their (and the already-rendered wrapper script's) S3 uploads. Built and
# tested standalone this round, matching the "build first, wire later" split
# from the two prior slices.
#
# The download half is not a bare swap, per the plan's own table: Ansible's
# get_url computes the checksum *during* download and fails the task on
# mismatch. This is a genuinely different check from _validate_download_
# checksum above -- that function only validates the checksum *string's*
# format (sha256:<64 hex>) before any AWS mutation; it has no way to know
# whether the bytes actually on GitHub match it. Both must exist:
# conflating them would silently drop the real download-integrity check
# this whole staging step exists to enforce (CLAUDE.md's monitoring-tarball
# and Docker-Compose-plugin bullets document exactly what an unverified
# download risks on a private-subnet AL2023 cluster).
# ---------------------------------------------------------------------------


def _download_with_checksum(url, dest, checksum, *, mode):
    """boto3/Python twin of get_url's checksum-verified download. Streams
    to a temp file in dest's own directory, hashes the temp file, and only
    os.replace()s it into dest once the digest matches -- os.replace is
    atomic on the same filesystem, so a mismatch (or any other failure)
    leaves no partial or wrong file at dest at all, rather than trusting a
    caller to clean up after a raised exception. Only the sha256 algorithm
    is supported, matching every checksum this codebase resolves
    (_SHA256_CHECKSUM_RE); _validate_download_checksum already rejected
    anything else long before this function is ever reached in practice,
    but this still checks its own input rather than assuming that."""
    algo, _, expected_hex = checksum.partition(":")
    if algo != "sha256":
        raise ValueError(f"unsupported checksum algorithm: {checksum!r}")
    expected_hex = expected_hex.lower()

    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(dest) or ".")
    try:
        with os.fdopen(fd, "wb") as fh, urllib.request.urlopen(url, timeout=120) as resp:
            shutil.copyfileobj(resp, fh)
        digest = hashlib.sha256()
        with open(tmp_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        actual_hex = digest.hexdigest()
        if actual_hex != expected_hex:
            raise ValueError(
                f"checksum mismatch downloading {url}: expected sha256:{expected_hex}, "
                f"got sha256:{actual_hex}"
            )
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, dest)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.remove(tmp_path)
        raise


def _upload_file_to_s3(s3, *, bucket, key, src):
    """boto3 twin of amazon.aws.s3_object's mode: put (permission: private,
    encrypt: true). Deliberately does not pass ACL="private": S3 has
    defaulted every new bucket to "bucket owner enforced" (ACLs disabled)
    since April 2023, and _create_s3_bucket_for_cluster does not override
    that default -- passing any ACL on such a bucket raises
    AccessControlListNotSupported. Omitting the ACL parameter entirely
    produces the same effective permission (owner-only access) under that
    default and is the strictly safer choice, regardless of what the
    Ansible module's own permission: private resolves to on whatever this
    account's buckets are configured with today."""
    s3.upload_file(src, bucket, key, ExtraArgs={"ServerSideEncryption": "AES256"})


def _stage_monitoring_tarball(
    s3, *, cluster_data_dir, s3_bucketname, s3_script_path,
    monitoring_version, monitoring_version_checksum,
):
    """boto3 twin of "Download aws-parallelcluster-monitoring tarball from
    GitHub" + "Upload monitoring tarball to S3"."""
    filename = f"aws-parallelcluster-monitoring-{monitoring_version}.tar.gz"
    dest = os.path.join(cluster_data_dir, filename)
    url = (
        "https://github.com/aws-samples/aws-parallelcluster-monitoring/"
        f"archive/refs/tags/{monitoring_version}.tar.gz"
    )
    _download_with_checksum(url, dest, monitoring_version_checksum, mode=0o644)
    _upload_file_to_s3(s3, bucket=s3_bucketname, key=f"{s3_script_path}/{filename}", src=dest)


def _stage_docker_compose_plugin(
    s3, *, s3_bucketname, s3_script_path, docker_compose_version,
    docker_compose_arch, docker_compose_checksum, docker_compose_local_dest,
    docker_compose_s3_dest,
):
    """boto3 twin of "Download the Docker Compose CLI plugin for Amazon
    Linux 2023" + "Upload the Docker Compose CLI plugin to S3", both
    already gated on stage_docker_compose by the caller (matching the
    playbook's own per-task when:, not a block-level one)."""
    url = (
        "https://github.com/docker/compose/releases/download/"
        f"{docker_compose_version}/docker-compose-linux-{docker_compose_arch}"
    )
    _download_with_checksum(
        url, docker_compose_local_dest, docker_compose_checksum, mode=0o755,
    )
    _upload_file_to_s3(
        s3, bucket=s3_bucketname,
        key=f"{s3_script_path}/{docker_compose_s3_dest}", src=docker_compose_local_dest,
    )


def stage_and_upload_monitoring_wrapper(
    s3, *, enable_monitoring, cluster_data_dir, s3_bucketname, s3_script_path,
    monitoring_version, monitoring_version_checksum, stage_docker_compose,
    docker_compose_version, docker_compose_arch, docker_compose_checksum,
    docker_compose_local_dest, docker_compose_s3_dest,
    monitoring_wrapper_dest, monitoring_s3_dest,
):
    """The single entry point matching create_pcluster.yml's "Stage and
    upload monitoring post-install wrapper" block, gated entirely on
    enable_monitoring exactly like the playbook's own block-level when:.
    monitoring_wrapper_dest is already-rendered by Workstream 2's Tier 2
    cutover (render_template, not this function's job) -- this only
    uploads it, the one task in the original block that was never a
    download."""
    if not enable_monitoring:
        return
    _stage_monitoring_tarball(
        s3, cluster_data_dir=cluster_data_dir, s3_bucketname=s3_bucketname,
        s3_script_path=s3_script_path, monitoring_version=monitoring_version,
        monitoring_version_checksum=monitoring_version_checksum,
    )
    if stage_docker_compose:
        _stage_docker_compose_plugin(
            s3, s3_bucketname=s3_bucketname, s3_script_path=s3_script_path,
            docker_compose_version=docker_compose_version,
            docker_compose_arch=docker_compose_arch,
            docker_compose_checksum=docker_compose_checksum,
            docker_compose_local_dest=docker_compose_local_dest,
            docker_compose_s3_dest=docker_compose_s3_dest,
        )
    _upload_file_to_s3(
        s3, bucket=s3_bucketname, key=f"{s3_script_path}/{monitoring_s3_dest}",
        src=monitoring_wrapper_dest,
    )


# ---------------------------------------------------------------------------
# Workstream 3, create-side migration, fourth slice: create_pcluster.yml's
# "Launch the new ParallelCluster v3 stack" + "Wait for the cluster head
# node to reach running state" + its three abort/fail tasks + "Get the head
# node IP address" -- directly parallel to the delete side's
# run_cluster_delete_and_classify (round 18), and built the same way: a
# wait loop, a classify step, and one composing entry point, tested standalone
# and not yet wired in.
# ---------------------------------------------------------------------------

_CREATE_COMPLETE = "CREATE_COMPLETE"
_CREATE_FAILED = "CREATE_FAILED"
# Workstream 4's async-handling state: the operation was successfully
# kicked off and deliberately not waited on (wait=False). Distinct from
# _TIMED_OUT, which means a wait *was* performed and ran out of retries --
# conflating the two would make a caller that never intended to wait look
# like a failed build.
_KICKED_OFF = "KICKED_OFF"


@dataclass(frozen=True)
class ClusterCreateOutcome:
    terminal_state: str
    create_confirmed: bool
    create_failed: bool
    create_headline: str
    head_node_public_ip: str


def _wait_for_cluster_create(
    describe_fn, cluster_name, region, *, retries=60, delay_seconds=60, sleep_fn=None,
    progress_fn=None,
):
    """boto3/pcluster.lib twin of "Wait for the cluster head node to reach
    running state" (until:/retries:/delay: pcluster_create_timeout|
    default(60)/60 -- a retries*delay_seconds=3600s/60min default ceiling
    matching --pcluster_create_timeout's own default). Returns
    (terminal_state, last_response): _CREATE_COMPLETE or _CREATE_FAILED
    with the describe-cluster response that decided it (so a caller can
    read the head node IP out of the *same* poll, not a fresh API call --
    see run_cluster_create_and_classify), or _TIMED_OUT (shared with the
    delete side's identical concept) with None.

    Only two outcomes are checked against clusterStatus, not the four
    literal substrings ("CREATE_COMPLETE"/"CREATE_FAILED"/
    "ROLLBACK_COMPLETE"/"ROLLBACK_FAILED") the playbook's own until: greps
    for across the raw JSON text. This is not a simplification that
    changes behavior -- it is what the structured field can ever actually
    contain. Read directly from the installed pcluster package rather
    than assumed: cloud_formation_status_to_cluster_status
    (pcluster/api/converters.py) maps CloudFormation's ROLLBACK_IN_
    PROGRESS, ROLLBACK_FAILED, and ROLLBACK_COMPLETE *all* to
    ClusterStatus.CREATE_FAILED, so describe_cluster's clusterStatus field
    can only ever show CREATE_COMPLETE or CREATE_FAILED for a cluster in
    this state machine -- the literal ROLLBACK_* strings only ever appear
    in the separate, unmapped cloudFormationStackStatus field, which is
    what the playbook's whole-blob substring search was really matching
    against. A real, previously-undocumented consequence, verified from
    that mapping rather than assumed: CREATE_FAILED is already present
    (via clusterStatus) the moment CloudFormation *enters* rollback
    (ROLLBACK_IN_PROGRESS), a full state earlier than rollback actually
    finishing -- so the playbook's own until: loop, and the "Abort if
    stack creation failed" task right after it, already stop waiting and
    abort the play as soon as rollback *begins*, not once CloudFormation
    finishes cleaning up. This function reproduces that exact behavior
    faithfully rather than "fixing" it to wait for the rollback's own
    terminal CFN state, which would be a real, unrequested behavior
    change to a system this migration's whole job is to leave working
    unchanged. Any non-terminal describe-cluster failure is retried
    exactly like a still-building cluster, matching the delete side's
    identical reasoning (Ansible's until:/retries: does not distinguish
    the reason for a non-terminal result mid-retry); the last exception is
    re-raised if the final attempt is still unrecognized, rather than
    folded into TIMED_OUT."""
    sleep_fn = sleep_fn or time.sleep
    last_exc = None
    for attempt in range(retries):
        last_exc = None
        try:
            resp = describe_fn(cluster_name=cluster_name, region=region)
        except Exception as e:
            last_exc = e
        else:
            status = resp.get("clusterStatus", "")
            if status == _CREATE_COMPLETE:
                return _CREATE_COMPLETE, resp
            if status == _CREATE_FAILED:
                return _CREATE_FAILED, resp
            if progress_fn is not None:
                progress_fn(attempt, status, resp.get("cloudFormationStackStatus", ""))
        if attempt < retries - 1:
            sleep_fn(delay_seconds)
    if last_exc is not None:
        raise last_exc
    return _TIMED_OUT, None


def _extract_head_node_ip(describe_response):
    """boto3 twin of "Get the head node IP address" -- re-parses the SAME
    describe-cluster response that already determined CREATE_COMPLETE,
    matching the playbook's own set_fact, which reads cluster_status.stdout
    (the wait loop's already-captured last response) rather than issuing a
    fresh describe-cluster call. Making a new call here instead would not
    just be wasteful -- it would risk reading a different snapshot than
    the one that actually decided the cluster was ready."""
    if not describe_response:
        return ""
    head_node = describe_response.get("headNode") or {}
    return head_node.get("publicIpAddress") or head_node.get("privateIpAddress") or ""


def _classify_cluster_create_outcome(terminal_state, cluster_name, describe_response):
    """boto3 twin of create_pcluster.yml's "Abort if the stack never
    reached a terminal state" (TIMED_OUT) and "Abort if stack creation
    failed" (CREATE_FAILED) -- derived once, the same reasoning as the
    delete side's _classify_cluster_delete_outcome, so no caller can see
    the three facts disagree with each other. The failure headline
    surfaces failureCode/failureReason when available (describe_cluster's
    own failures field, populated only when clusterStatus is
    CREATE_FAILED) -- a genuine improvement over the playbook's own fail
    message, which only ever repeats the literal string "CREATE_FAILED"
    with no further detail, matching this migration's precedent of
    picking up small real improvements where the Python side can do
    strictly better with no extra cost (see the delete-side classification
    logic and the cluster-launch-summary print in the plan's own table)."""
    create_confirmed = terminal_state == _CREATE_COMPLETE
    create_failed = terminal_state == _CREATE_FAILED
    if create_confirmed:
        headline = f"Cluster {cluster_name} was created successfully."
    elif create_failed:
        failures = (describe_response or {}).get("failures") or []
        if failures:
            detail = "; ".join(
                f"{f.get('failureCode', 'UNKNOWN')}: {f.get('failureReason', '')}"
                for f in failures
            )
            headline = f"Cluster {cluster_name} creation failed: {detail}"
        else:
            headline = f"Cluster {cluster_name} creation failed (CREATE_FAILED)."
    else:
        headline = f"Creation of cluster {cluster_name} was NOT confirmed."
    return create_confirmed, create_failed, headline


def run_cluster_create_and_classify(
    create_fn, describe_fn, cluster_name, region, *,
    cluster_configuration_path, rollback_on_failure=False,
    retries=60, delay_seconds=60, sleep_fn=None,
    wait=True, progress_fn=None,
):
    """The single entry point a future core_create_cluster wiring calls:
    "Launch the new ParallelCluster v3 stack" + the wait + its abort/fail
    tasks + "Get the head node IP address", as one coherent unit -- the
    same grouping run_cluster_delete_and_classify uses on the delete side.

    cluster_configuration_path must be a filesystem path, not raw YAML
    content. Confirmed by reading pcluster/cli/model.py directly:
    create-cluster's clusterConfiguration parameter is tagged "type":
    "file" in the CLI model, so pcluster.lib's dispatcher hands a string
    value to read_file(), which open()s it as a path -- exactly like the
    CLI's own --cluster-configuration <path> flag. Passing raw config text
    here would make it try to open() the YAML itself as a path and fail
    with FileNotFoundError. rollback_on_failure defaults to False,
    matching the playbook's own hardcoded --rollback-on-failure false --
    automatic rollback would delete every resource before an operator can
    diagnose the failure, defeating the entire point of a failed-build
    postmortem.

    create_fn itself is called with no tolerance for failure -- unlike the
    delete side, which tolerates NotFoundException/BadRequestException on
    an already-gone cluster, any exception here propagates immediately,
    matching the playbook's own failed_when: create_cluster_result.rc != 0
    with no exceptions. A preflight check earlier in core_create_cluster's
    pipeline (Workstream 1) already rules out "cluster already exists"
    before this is ever reached, so there is no equivalent tolerance case
    to reproduce here.

    wait (Workstream 4): True -- the CLI shim's setting, and the default --
    polls describe_cluster internally until a terminal state, exactly the
    blocking behavior the Ansible until:/retries: loop always had, so CLI
    behavior is preserved by construction. False -- the setting a future
    MCP tool wrapper uses -- returns immediately after the kickoff call
    with terminal_state=_KICKED_OFF, since a single MCP tool call cannot
    block for the 20-45 minutes a real build takes. Deliberately NOT
    implemented by forwarding wait= to pcluster.lib: that kwarg does exist
    for create-cluster/delete-cluster/update-cluster (confirmed from
    pcluster/lib/lib.py's own wait_ops list, which excludes
    update-compute-fleet), but its polling is opaque -- no progress
    output, no access to the intermediate describe responses, and no
    shared shape with update_compute_fleet, which has to poll manually
    regardless. One polling implementation here serves every operation
    uniformly.

    progress_fn, when supplied, is called as progress_fn(attempt, status,
    cfn_status) once per non-terminal poll -- the hook the CLI uses to
    print a status line per interval during what was previously a
    completely silent 20-45 minute wait. A deliberate, scoped exception to
    "CLI behavior unchanged," called out rather than bundled silently in:
    the final build summary it precedes is still byte-identical."""
    create_fn(
        cluster_name=cluster_name,
        cluster_configuration=cluster_configuration_path,
        region=region,
        rollback_on_failure=rollback_on_failure,
    )
    if not wait:
        return ClusterCreateOutcome(
            _KICKED_OFF, False, False,
            f"Creation of cluster {cluster_name} was started; not waiting for it "
            f"to finish. Poll with: pcluster describe-cluster --cluster-name "
            f"{cluster_name} --region {region}",
            "",
        )
    terminal_state, last_response = _wait_for_cluster_create(
        describe_fn, cluster_name, region,
        retries=retries, delay_seconds=delay_seconds, sleep_fn=sleep_fn,
        progress_fn=progress_fn,
    )
    create_confirmed, create_failed, headline = _classify_cluster_create_outcome(
        terminal_state, cluster_name, last_response,
    )
    head_node_public_ip = _extract_head_node_ip(last_response) if create_confirmed else ""
    return ClusterCreateOutcome(
        terminal_state, create_confirmed, create_failed, headline, head_node_public_ip,
    )


# ---------------------------------------------------------------------------
# Workstream 3, create-side migration, fifth slice: create_pcluster.yml's
# SSH/SCP orchestration to the head node -- "Wait for SSH port to be
# reachable", known_hosts management (ssh-keygen -R / ssh-keyscan), and the
# staging/performance-directory transfer and cleanup. The plan's own table
# is explicit that this category is not a boto3 swap at all: none of it
# touches an AWS API, it is entirely subprocess.run([...]) in place of
# Ansible's shell:/command: wrapper around the exact same ssh/scp
# invocations. Built and tested standalone this round, matching the
# "build first, wire later" split used for every prior slice.
# ---------------------------------------------------------------------------

_SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=30", "-o", "StrictHostKeyChecking=accept-new"]


def _ssh_argv(ssh_keypair, ec2_user, head_node_ip, remote_command):
    """One argv list for `ssh <opts> -i <keypair> user@host <remote_command>`.
    remote_command is a single string, matching ssh's own calling
    convention -- it hands that one string to the remote user's shell,
    it is not a list of remote argv items."""
    return ["ssh", *_SSH_OPTS, "-i", ssh_keypair, f"{ec2_user}@{head_node_ip}", remote_command]


def _scp_argv(ssh_keypair, *args):
    return ["scp", *_SSH_OPTS, "-i", ssh_keypair, *args]


def _wait_for_ssh_port(
    host, *, port=22, delay=5, timeout=300, poll_interval=1, sleep_fn=None, time_fn=None,
):
    """boto3/Python twin of "Wait for SSH port to be reachable on the head
    node" (wait_for: port 22, delay: 5, timeout: 300, state: started).
    Reuses the exact socket.create_connection probe shape
    _check_external_nfs_reachable already uses for the same class of
    check (CLAUDE.md documents that function as the precedent). delay is
    a fixed wait before the first probe (matching wait_for's own delay:
    semantics -- the head node has just been reported running, but sshd
    is not instantly ready), timeout is the *total* budget including that
    delay. time_fn is injectable (defaults to time.monotonic) so a test
    can supply a deterministic fake clock rather than depending on real
    wall-clock time to exercise the timeout path."""
    sleep_fn = sleep_fn or time.sleep
    time_fn = time_fn or time.monotonic
    sleep_fn(delay)
    deadline = time_fn() + max(timeout - delay, 0)
    while True:
        try:
            with socket.create_connection((host, port), timeout=poll_interval):
                return
        except OSError:
            if time_fn() >= deadline:
                raise TimeoutError(
                    f"SSH port {port} on {host} did not become reachable within {timeout}s"
                )
            sleep_fn(poll_interval)


def _ensure_local_ssh_dir(ssh_known_hosts):
    """boto3/Python twin of "Ensure the local SSH directory exists" (file,
    state: directory, mode: "0700")."""
    os.makedirs(os.path.dirname(ssh_known_hosts), mode=0o700, exist_ok=True)
    os.chmod(os.path.dirname(ssh_known_hosts), 0o700)


def _remove_stale_known_hosts_entry(head_node_ip, ssh_known_hosts):
    """boto3/Python twin of "Remove any stale known_hosts entry for the
    head node" (`ssh-keygen -R ... 2>/dev/null || true`) -- the `|| true`
    means this never fails regardless of ssh-keygen's exit code (a
    missing known_hosts file, or no matching entry, are both fine), so
    this function swallows everything rather than propagating."""
    with contextlib.suppress(Exception):
        subprocess.run(
            ["ssh-keygen", "-R", head_node_ip, "-f", ssh_known_hosts],
            capture_output=True,
        )


def _accept_ssh_fingerprint(head_node_ip, ssh_known_hosts, *, timeout=30):
    """boto3/Python twin of "Accept the SSH fingerprint of the head node"
    -- ssh-keyscan, then append only genuinely new lines to known_hosts
    (exact-match dedup, matching the shell script's own `grep -qxF`).
    Raises RuntimeError if ssh-keyscan returns no host key at all
    (matching the shell script's own fatal exit 2 there -- every later
    ssh/scp call in this whole group fails host-key verification without
    a recorded fingerprint, so this must not be tolerated). Returns True
    if any line was newly added, False if every key was already present
    -- the Python analog of changed_when: rc == 1."""
    result = subprocess.run(
        ["ssh-keyscan", "-T", str(timeout), "-H", head_node_ip],
        capture_output=True, text=True,
    )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(f"ssh-keyscan returned no host key for {head_node_ip}")

    existing = set()
    if os.path.isfile(ssh_known_hosts):
        with open(ssh_known_hosts) as fh:
            existing = set(fh.read().splitlines())
    else:
        open(ssh_known_hosts, "a").close()

    added = False
    with open(ssh_known_hosts, "a") as fh:
        for line in lines:
            if line not in existing:
                fh.write(line + "\n")
                existing.add(line)
                added = True
    return added


def _create_performance_dir_on_head_node(*, ssh_keypair, ec2_user, head_node_ip, headnode_performance_dir_dest):
    """boto3/Python twin of "Create performance directory on the head
    node" (ssh mkdir -p)."""
    subprocess.run(
        _ssh_argv(
            ssh_keypair, ec2_user, head_node_ip,
            f"mkdir -p {shlex.quote(headnode_performance_dir_dest)}",
        ),
        check=True,
    )


def _transfer_staging_dir(*, ssh_keypair, ec2_user, head_node_ip, stage_dir):
    """boto3/Python twin of "Transfer the local staging directory to the
    head node" (ssh mkdir -p "$(dirname stage_dir)" && scp -r stage_dir
    user@host:"$(dirname stage_dir)"/). `dirname` is pure string
    manipulation with no filesystem access, so os.path.dirname() produces
    the identical result the shell's own $(dirname ...) would -- no need
    to shell out for it. The `&&` short-circuit is reproduced by simply
    not calling scp when the ssh mkdir raises (check=True)."""
    parent = os.path.dirname(stage_dir.rstrip("/"))
    subprocess.run(
        _ssh_argv(ssh_keypair, ec2_user, head_node_ip, f"mkdir -p {shlex.quote(parent)}"),
        check=True,
    )
    subprocess.run(
        _scp_argv(ssh_keypair, "-r", stage_dir, f"{ec2_user}@{head_node_ip}:{parent}/"),
        check=True,
    )


def _transfer_sbatch_script(*, ssh_keypair, ec2_user, ec2_user_home, head_node_ip, stage_dir):
    """boto3/Python twin of "Transfer the default Slurm submission script
    to the head node" (scp)."""
    subprocess.run(
        _scp_argv(
            ssh_keypair,
            os.path.join(stage_dir, "sbatch_default_submission_script.sh"),
            f"{ec2_user}@{head_node_ip}:{ec2_user_home}",
        ),
        check=True,
    )


def _copy_performance_source_tree(*, ssh_keypair, ec2_user, head_node_ip, performance_stage_dir, headnode_performance_dir_dest):
    """boto3/Python twin of "Copy the performance source tree to its
    final destination on the head node" (scp -r
    "{{ performance_stage_dir }}"/* ...). The playbook's `shell:` module
    lets the *local* shell expand that glob before scp ever runs; Python
    has no shell here (subprocess.run's list form, deliberately, to avoid
    shell=True), so this expands it explicitly with glob.glob instead. An
    empty performance_stage_dir therefore correctly copies nothing rather
    than passing a literal unmatched "*" to scp and failing -- a small,
    disclosed improvement over the original, not a behavior this function
    was asked to preserve."""
    sources = sorted(glob.glob(os.path.join(performance_stage_dir, "*")))
    if not sources:
        return
    subprocess.run(
        _scp_argv(
            ssh_keypair, "-r", *sources,
            f"{ec2_user}@{head_node_ip}:{headnode_performance_dir_dest}",
        ),
        check=True,
    )


def _build_active_perf_dirs(
    *, ebs_hpc_performance_dir, enable_efs, efs_hpc_performance_dir,
    enable_fsx, fsx_hpc_performance_dir,
):
    """boto3/Python twin of "Build list of active HPC performance test
    directories" -- pure function, the ebs directory is unconditional
    (EBS-backed /shared always exists), EFS/FSx are appended only when
    enabled, matching the playbook's own set_fact exactly."""
    dirs = [ebs_hpc_performance_dir]
    if enable_efs:
        dirs.append(efs_hpc_performance_dir)
    if enable_fsx:
        dirs.append(fsx_hpc_performance_dir)
    return dirs


def _create_and_own_perf_dirs(*, ssh_keypair, ec2_user, head_node_ip, perf_dirs, headnode_performance_dir_dest):
    """boto3/Python twin of the three looped tasks -- "Create HPC
    performance test directories", "Set ownership of HPC performance test
    directories", "Copy performance source tree to HPC performance test
    directories" -- all on the head node. Three separate phases over
    every directory, in that order, not one interleaved pass: Ansible's
    `loop:` on three separate tasks runs each task to completion over
    every item before the next task starts (mkdir dir1, mkdir dir2, ...,
    then chown dir1, chown dir2, ..., then cp dir1, cp dir2, ...), and
    this reproduces that exact phase ordering rather than the arguably
    more natural per-directory grouping."""
    for d in perf_dirs:
        subprocess.run(
            _ssh_argv(ssh_keypair, ec2_user, head_node_ip, f"sudo mkdir -p {shlex.quote(d)}"),
            check=True,
        )
    for d in perf_dirs:
        subprocess.run(
            _ssh_argv(
                ssh_keypair, ec2_user, head_node_ip,
                f"sudo chown -R {shlex.quote(ec2_user)}:{shlex.quote(ec2_user)} {shlex.quote(d)}",
            ),
            check=True,
        )
    for d in perf_dirs:
        subprocess.run(
            _ssh_argv(
                ssh_keypair, ec2_user, head_node_ip,
                f"cp -a {shlex.quote(headnode_performance_dir_dest)}/* {shlex.quote(d)}",
            ),
            check=True,
        )


def _remove_head_node_staging_dir(*, ssh_keypair, ec2_user, head_node_ip, stage_dir):
    """boto3/Python twin of "Remove the staging directory on the head
    node" (ssh rm -rf)."""
    subprocess.run(
        _ssh_argv(ssh_keypair, ec2_user, head_node_ip, f"rm -rf {shlex.quote(stage_dir)}"),
        check=True,
    )


def deploy_staging_and_performance_tree_to_head_node(
    *, head_node_public_ip, ssh_keypair, ssh_known_hosts, ec2_user, ec2_user_home,
    stage_dir, enable_hpc_benchmarks, performance_stage_dir,
    headnode_performance_dir_dest, ebs_hpc_performance_dir, enable_efs,
    efs_hpc_performance_dir, enable_fsx, fsx_hpc_performance_dir,
):
    """The single entry point a future core_create_cluster wiring calls:
    every SSH/SCP task from "Wait for SSH port to be reachable" through
    "Remove the staging directory on the head node", in the playbook's own
    order. A no-op when head_node_public_ip is empty, matching every one
    of those tasks sharing the identical `when: head_node_public_ip != ''`
    gate -- there is nothing to reach without an IP, and every individual
    function above assumes it already has one rather than re-checking."""
    if not head_node_public_ip:
        return

    _wait_for_ssh_port(head_node_public_ip)
    _ensure_local_ssh_dir(ssh_known_hosts)
    _remove_stale_known_hosts_entry(head_node_public_ip, ssh_known_hosts)
    _accept_ssh_fingerprint(head_node_public_ip, ssh_known_hosts)

    if enable_hpc_benchmarks:
        _create_performance_dir_on_head_node(
            ssh_keypair=ssh_keypair, ec2_user=ec2_user, head_node_ip=head_node_public_ip,
            headnode_performance_dir_dest=headnode_performance_dir_dest,
        )

    _transfer_staging_dir(
        ssh_keypair=ssh_keypair, ec2_user=ec2_user, head_node_ip=head_node_public_ip,
        stage_dir=stage_dir,
    )
    _transfer_sbatch_script(
        ssh_keypair=ssh_keypair, ec2_user=ec2_user, ec2_user_home=ec2_user_home,
        head_node_ip=head_node_public_ip, stage_dir=stage_dir,
    )

    if enable_hpc_benchmarks:
        _copy_performance_source_tree(
            ssh_keypair=ssh_keypair, ec2_user=ec2_user, head_node_ip=head_node_public_ip,
            performance_stage_dir=performance_stage_dir,
            headnode_performance_dir_dest=headnode_performance_dir_dest,
        )
        perf_dirs = _build_active_perf_dirs(
            ebs_hpc_performance_dir=ebs_hpc_performance_dir, enable_efs=enable_efs,
            efs_hpc_performance_dir=efs_hpc_performance_dir, enable_fsx=enable_fsx,
            fsx_hpc_performance_dir=fsx_hpc_performance_dir,
        )
        _create_and_own_perf_dirs(
            ssh_keypair=ssh_keypair, ec2_user=ec2_user, head_node_ip=head_node_public_ip,
            perf_dirs=perf_dirs, headnode_performance_dir_dest=headnode_performance_dir_dest,
        )

    _remove_head_node_staging_dir(
        ssh_keypair=ssh_keypair, ec2_user=ec2_user, head_node_ip=head_node_public_ip,
        stage_dir=stage_dir,
    )


# ---------------------------------------------------------------------------
# Workstream 3, create-side migration, final slice: every remaining
# create_pcluster.yml task the five prior slices (rounds 20-24) don't cover
# -- SNS topic + notify, config.pcluster.j2's render (finally unblocked now
# that _create_external_nfs_sg returns a real group_id -- see
# core_create_cluster's own comment on why it was deferred) plus the
# config/script S3 uploads, the external NFS mount list upload, the HPC
# benchmark driver upload + results bucket, the pre-launch summary print,
# the staging-dir copy/sync/cleanup, and the final SNS build-summary report.
# Wiring this and the five prior slices into core_create_cluster (below)
# retires create_pcluster.yml from execution entirely -- the mirror of what
# round 19 did for delete_pcluster.yml. Most of these functions take `ctx`
# (the same {**cluster_parameters, **rendered vars_file} dict every
# template render in this file already uses) rather than a long list of
# individual keyword parameters, for the same reason render_template(...,
# **ctx) does: it is already the single source of truth for this data, and
# restating two dozen names as separate parameters here would just be
# another place for two of them to get transposed.
# ---------------------------------------------------------------------------


def _create_sns_topic_and_notify(sns, *, cluster_name, cluster_owner_email, start_timestamp):
    """boto3 twin of "Create an SNS topic to send notifications to
    cluster_owner_email" + "Send an SNS notification announcing the
    cluster build initiation". create_topic is idempotent by name.
    Returns the topic ARN, needed later to publish the build summary and
    by teardown to delete the same topic."""
    topic_arn = sns.create_topic(Name=f"sns_alerts_{cluster_name}")["TopicArn"]
    sns.set_topic_attributes(
        TopicArn=topic_arn, AttributeName="DisplayName",
        AttributeValue=f"SNS Alerts for Cluster {cluster_name}",
    )
    sns.subscribe(TopicArn=topic_arn, Protocol="email", Endpoint=cluster_owner_email)
    sns.publish(
        TopicArn=topic_arn,
        Message=f"Started building {cluster_name} at {start_timestamp}",
        Subject=f"Cluster Deployment Update: {cluster_name}",
    )
    return topic_arn


def render_and_upload_cluster_config_and_scripts(s3, *, ctx, external_nfs_sg_id, templates_dir):
    """boto3/Python twin of "Template the cluster config" + "Copy the
    operator pre/post-deployment hooks..." + "PUT the pre/post-deployment
    scripts and cluster config into s3_bucketname". config.pcluster.j2
    could not render in Python during Workstream 2's Tier 1 cutover --
    when enable_external_nfs is true it needs {{ external_nfs_sg.group_id
    }}, an Ansible register: result from a security-group task that did
    not have a Python equivalent yet. _create_external_nfs_sg (round 21)
    is that equivalent now, so this finally closes that gap rather than
    leaving config.pcluster.j2 as the one template still Ansible-rendered.
    external_nfs_sg_id is None when the feature is disabled; config.
    pcluster.j2 only ever reads it inside an {% if enable_external_nfs ==
    'true' %} guard, so passing None there is never actually evaluated."""
    # A merged dict, not **ctx, external_nfs_sg=... directly: if ctx ever
    # already carries an external_nfs_sg key (as it legitimately does in
    # tests/conftest.py's fixture, built for other templates that need
    # one), the two would collide with a TypeError instead of the
    # explicit value correctly winning.
    render_kwargs = {**ctx, "external_nfs_sg": {"group_id": external_nfs_sg_id}}
    rendered_config = render_template(templates_dir, "config.pcluster.j2", **render_kwargs)
    with open(
        os.open(ctx["cluster_config_template"], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o755), "w"
    ) as fh:
        fh.write(rendered_config)

    user_preinstall_dest = os.path.join(ctx["cluster_data_dir"], ctx["user_preinstall_s3_dest"])
    user_postinstall_dest = os.path.join(ctx["cluster_data_dir"], ctx["user_postinstall_s3_dest"])
    for src, dest in (
        (ctx["user_preinstall_src"], user_preinstall_dest),
        (ctx["user_postinstall_src"], user_postinstall_dest),
    ):
        shutil.copyfile(src, dest)
        os.chmod(dest, 0o755)

    for src, dest_name in (
        (ctx["cluster_config_template"], ctx["cluster_config_dest"]),
        (ctx["preinstall_rendered"], ctx["preinstall_s3_dest"]),
        (ctx["postinstall_rendered"], ctx["postinstall_s3_dest"]),
        (user_preinstall_dest, ctx["user_preinstall_s3_dest"]),
        (user_postinstall_dest, ctx["user_postinstall_s3_dest"]),
    ):
        _upload_file_to_s3(
            s3, bucket=ctx["s3_bucketname"], key=f"{ctx['s3_script_path']}/{dest_name}", src=src,
        )


def _upload_external_nfs_mount_list(s3, *, ctx):
    """boto3 twin of "PUT the external NFS mount list into
    s3_bucketname"."""
    _upload_file_to_s3(
        s3, bucket=ctx["s3_bucketname"],
        key=f"{ctx['s3_script_path']}/{ctx['external_nfs_mount_list_template_dest']}",
        src=ctx["external_nfs_mount_list_template_src"],
    )


def _create_hpc_results_bucket(s3, *, results_bucketname, region):
    """boto3 twin of "Create the long-lived HPC benchmark results bucket"
    -- idempotent (`state: present`), matching that every build in an
    account+region shares this one bucket (CLAUDE.md's results-bucket
    bullet); no cluster-specific tags, just the three fixed ones the
    playbook itself sets."""
    kwargs = {"Bucket": results_bucketname}
    if region != "us-east-1":
        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
    try:
        s3.create_bucket(**kwargs)
    except _ClientError as e:
        if e.response["Error"]["Code"] != "BucketAlreadyOwnedByYou":
            raise
    s3.put_public_access_block(
        Bucket=results_bucketname,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True, "IgnorePublicAcls": True,
            "BlockPublicPolicy": True, "RestrictPublicBuckets": True,
        },
    )
    s3.put_bucket_tagging(
        Bucket=results_bucketname,
        Tagging={"TagSet": [
            {"Key": "Name", "Value": "ParallelClusterMaker HPC benchmark results"},
            {"Key": "ClusterStackType", "Value": "ParallelCluster"},
            {"Key": "DoNotDelete", "Value": "results are retained across cluster rebuilds"},
        ]},
    )


def stage_and_upload_hpc_benchmark_driver(s3, *, ctx, region):
    """boto3/Python twin of "Copy top-level performance dispatcher
    scripts to stage_dir" + "Upload the performance driver to S3 for head
    node self-repair" + "Create the long-lived HPC benchmark results
    bucket". The S3 sync is an ALLOWLIST on both ends (--exclude "*"
    --include "hpc-benchmark.sh"), matching CLAUDE.md's own documented
    incident (a blocklist once leaked internal docs and raw Jinja2
    templates into the operator's working tree) -- kept as a real `aws s3
    sync` subprocess call rather than hand-rolled in boto3, per the
    migration plan's own recommendation (community.aws.s3_sync has no
    boto3-native equivalent worth reimplementing)."""
    src = os.path.join(ctx["performance_rootdir"], "hpc-benchmark.sh")
    dest = os.path.join(ctx["performance_stage_dir"], "hpc-benchmark.sh")
    shutil.copyfile(src, dest)
    os.chmod(dest, 0o755)
    subprocess.run(
        [
            "aws", "s3", "sync", f"{ctx['performance_rootdir']}/",
            f"s3://{ctx['s3_bucketname']}/hpc-benchmark/",
            "--exclude", "*", "--include", "hpc-benchmark.sh",
            "--region", region,
        ],
        check=True,
    )
    _create_hpc_results_bucket(s3, results_bucketname=ctx["results_bucketname"], region=region)


def print_cluster_launch_summary(ctx, *, launch_timestamp):
    """boto3/Python twin of "Print cluster launch summary" -- the
    pre-launch informational print, distinct from the comprehensive
    post-creation summary core_create_cluster already builds independently
    (cost/storage breakdown, access instructions, etc.) further down its
    own pipeline; this one exists so the operator sees what is about to be
    built before the 30-45 minute wait starts, matching the original's
    timing exactly."""
    lines = [
        "", "=" * 66, "                   Cluster Launch Summary", "=" * 66, "",
        f"Cluster Name:      {ctx['cluster_name']}",
        f"SerialDateStamp:   {ctx['cluster_serial_datestamp']}",
        f"Launch Timestamp:  {launch_timestamp}",
        f"Operating System:  {ctx['base_os']}",
        f"HPC Scheduler:     {ctx['scheduler']}",
        f"Head Node:         {ctx['headnode_instance_type']}",
        f"VPC Name:          {ctx['vpc_name']}",
        f"Availability Zone: {ctx['az']}",
    ]
    if ctx.get("enable_loginnode") == "true":
        lines.append(
            f"Login Node:        {ctx['loginnode_instance_type']} (x{ctx['loginnode_count']})"
        )
    if ctx.get("enable_cpu_queue") == "true":
        lines.append(
            f"CPU Queue:         {', '.join(ctx['cpu_instance_types'])}  "
            f"(min: {ctx['initial_cpu_queue_size']}  max: {ctx['max_cpu_queue_size']})"
        )
    if ctx.get("enable_gpu_queue") == "true":
        lines.append(
            f"GPU Queue:         {', '.join(ctx['gpu_instance_types'])}  "
            f"(min: {ctx['initial_gpu_queue_size']}  max: {ctx['max_gpu_queue_size']})"
        )
    for flag, label in (
        ("enable_efa", "EFA Enabled:       TRUE"),
        ("enable_efs", "EFS Enabled:       TRUE"),
        ("enable_fsx", "FSxL Enabled:      TRUE"),
        ("enable_external_nfs", "External NFS:      TRUE"),
        ("enable_monitoring", "Monitoring:        TRUE"),
        ("enable_hpc_benchmarks", "HPC Benchmarks:    TRUE"),
    ):
        if ctx.get(flag) == "true":
            lines.append(label)
    lines += [
        "", "Stack build takes 30-45 minutes. Monitor progress with:",
        f"  pcluster describe-cluster --cluster-name {ctx['cluster_name']} --region {ctx['region']}",
        "",
    ]
    for line in lines:
        print(line)


def finalize_staging_directory(*, stage_dir, cluster_data_dir, s3_bucketname, region):
    """boto3/Python twin of "Copy the custom scripts from stage_dir to
    the cluster_data directory" + "Sync the cluster_data directory to
    s3_bucketname" + "Remove the local staging directory". The S3 sync
    stays a real `aws s3 sync` subprocess call excluding "*.pem" -- same
    reasoning as the HPC driver upload above, and the one place this
    toolkit must never let a private key reach S3
    (TestCreatePlaybookExcludesPrivateKeyFromS3Sync, tests/test_templates.py,
    pins the *.pem exclusion on the Ansible side; this is its Python
    twin)."""
    shutil.copytree(stage_dir, cluster_data_dir, dirs_exist_ok=True)
    subprocess.run(
        [
            "aws", "s3", "sync", cluster_data_dir, f"s3://{s3_bucketname}/",
            "--exclude", "*.pem", "--region", region,
        ],
        check=True,
    )
    shutil.rmtree(stage_dir, ignore_errors=True)


def render_and_publish_build_summary_report(
    sns, *, ctx, sns_topic_arn, templates_dir, head_node_public_ip,
    start_overall_timestamp, start_stack_timestamp, stop_stack_timestamp, stop_overall_timestamp,
):
    """boto3/Python twin of "Template the cluster build summary report" +
    "Publish the cluster build summary report via SNS". Renders
    sns_build_summary_report.j2 (Workstream 2's own Tier 4, deferred to
    Workstream 3 for exactly this reason: it needs mid-build timers and
    head_node_public_ip, facts that did not exist as Python values until
    this round) and publishes it to the topic _create_sns_topic_and_notify
    already created -- the same "report first, topic outlives it" shape
    round 19 used on the delete side, except create's topic is not deleted
    afterward (it stays for the lifetime of the cluster, deleted only on
    teardown)."""
    # A merged dict, not **ctx plus explicit kwargs directly -- same
    # collision risk (and same fix) as render_and_upload_cluster_config_
    # and_scripts above.
    render_kwargs = {
        **ctx,
        "head_node_public_ip": head_node_public_ip,
        "start_overall_timer": {"stdout": start_overall_timestamp},
        "start_stack_creation_timer": {"stdout": start_stack_timestamp},
        "stop_stack_creation_timer": {"stdout": stop_stack_timestamp},
        "stop_overall_timer": {"stdout": stop_overall_timestamp},
    }
    report = render_template(templates_dir, "sns_build_summary_report.j2", **render_kwargs)
    with open(
        os.open(ctx["sns_build_summary_report_dest"], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644), "w"
    ) as fh:
        fh.write(report)
    with contextlib.suppress(Exception):
        sns.publish(
            TopicArn=sns_topic_arn, Message=report,
            Subject=f"Cluster Deployment Update: {ctx['cluster_name']}",
        )


def print_fsx_hydration_helper_locations(ctx):
    """boto3/Python twin of "Print FSx hydration helper script
    locations" -- gated on enable_fsx_hydration, unlike the Ansible task
    it replaces, which prints unconditionally and would show
    "s3://UNDEFINED/UNDEFINED" paths on a cluster where the feature is
    disabled (fsx_hydration_iam_policy's own "UNDEFINED" sentinel, used
    deliberately elsewhere in this file, flows into these same variables
    when the feature is off). A disclosed, deliberate fix, not a silent
    behavior change: printing paths that reference a feature the operator
    never enabled has no legitimate reading as intentional."""
    if ctx.get("enable_fsx_hydration") != "true":
        return
    lines = [
        "",
        f"S3→Lustre import path: s3://{ctx['fsx_s3_import_bucket']}/{ctx['fsx_s3_import_path']}",
        f"Lustre→S3 export path: s3://{ctx['fsx_s3_export_bucket']}/{ctx['fsx_s3_export_path']}",
        "",
        "Import S3 to Lustre:     /usr/local/bin/import-s3-to-lustre.sh",
        "Export Lustre to S3:     /usr/local/bin/export-lustre-to-s3.sh",
        "Check export status:     /usr/local/bin/check-lustre-export-progress.sh",
        "",
    ]
    for line in lines:
        print(line)


def core_create_cluster(*, params, repo_root, region, cluster_build_command, ansible_version, wait=True):
    """Validate, provision IAM, render the vars file, and build a cluster.

    A near-verbatim port of make_pcluster.py's own main() body from just
    after the Turbot profile switch through its final sys.exit(0) -- kept as
    one large function, in the core_rotate_cluster_key/core_apply_queue_config
    mold, rather than split into many small ones: the original body is one
    continuous, tightly sequenced pipeline (IAM setup must precede the vars
    file render; the vars file must exist before the Ansible invocation is
    built; cleanup on failure differs by how far the pipeline got), and
    splitting it would either duplicate that sequencing logic at the call
    site or require passing partial state between functions that has no
    other reason to exist.

    Every sys.exit() below -- bare int, an f-string message, or (via
    p_fail/refer_to_docs_and_quit/illegal_az_msg) always a bare
    sys.exit(1) with the message already printed -- is left exactly as it
    was in the original script, unconverted to PClusterMakerError. None of
    them need a numeric-vs-string exit-code translation the way
    kill_pcluster.py's core_delete_cluster did: every one of these already
    IS the final observable behavior (message printed, then a bare
    sys.exit()), and letting it propagate straight out of this function
    reproduces that exactly, with no wrapping needed at the CLI shim at all.
    This intentionally leaves the same disclosed limitation already accepted
    for core_stop_fleet/core_start_fleet: an MCP caller of a hypothetical
    create_cluster tool gets a raw, uncaught SystemExit on any of these
    paths, not a catchable error. Full MCP-safety hardening of this
    validation surface is out of scope for Workstream 1's job, which is to
    make the orchestration callable and testable outside of a CLI main() at
    all -- something this function achieves even with that limitation, since
    nothing about it depends on argparse or sys.argv.

    The operator's Ctrl-C abort window (ctrlC_Abort) is also kept inside
    this function, breaking the "gate always stays in the CLI shim" pattern
    every other migrated script follows. That pattern was chosen because the
    gate is normally the *first* real action, cheap for the shim to
    reproduce ahead of a lightweight preflight check. Here the gate sits
    after IAM roles/policies already exist and the vars file has already
    been rendered -- by the time an operator could Ctrl-C, most of the
    build's real state already exists, and the command being confirmed
    (build_cmd, with the real cluster_serial_number embedded) depends on
    values only available after that work completes. Splitting the abort
    window out to the shim would mean either running IAM setup and the vars
    file render twice (the first attempt being pure waste, and a real AWS
    mutation) or passing an awkward amount of half-built state back across
    the boundary. A future MCP create_cluster tool will need its own answer
    for this -- almost certainly no interactive gate at all, matching
    Workstream 4's async job design -- but that is that workstream's
    decision to make, not a blocker for this one.
    """
    _assert_supported_os(params.base_os)

    from pcluster_aux_data import (
        base_os_efa,
        base_os_instance_check,
        ctrlC_Abort,
        derive_ranks_per_node,
        ec2_instances_efa,
        ec2_instances_full_list,
        is_gpu_instance,
        needs_efa_gdr,
        nvidia_gpu_count,
        p_fail,
        p_val,
        parse_instance_type_list,
        refer_to_docs_and_quit,
        usable_vcpu_count,
    )

    src_dir = os.path.join(repo_root, "src")

    ansible_verbosity = params.ansible_verbosity
    az = params.az
    base_os = params.base_os
    cluster_name = params.cluster_name
    cluster_owner = params.cluster_owner
    cluster_owner_department = params.cluster_owner_department
    cluster_owner_email = params.cluster_owner_email
    cluster_type = params.cluster_type
    compute_instance_type = params.compute_instance_type
    compute_root_volume_size = params.compute_root_volume_size
    compute_root_volume_type = params.compute_root_volume_type
    compute_root_volume_iops = params.compute_root_volume_iops
    compute_root_volume_throughput = params.compute_root_volume_throughput
    custom_ami = params.custom_ami
    debug_mode = params.debug_mode
    ebs_encryption = params.ebs_encryption
    ebs_shared_dir = params.ebs_shared_dir
    ebs_shared_volume_size = params.ebs_shared_volume_size
    ebs_shared_volume_type = params.ebs_shared_volume_type
    ebs_shared_volume_iops = params.ebs_shared_volume_iops
    ebs_shared_volume_throughput = params.ebs_shared_volume_throughput
    efs_encryption = params.efs_encryption
    efs_performance_mode = params.efs_performance_mode
    efs_throughput_mode = params.efs_throughput_mode
    enable_efa = params.enable_efa
    enable_efs = params.enable_efs
    enable_external_nfs = params.enable_external_nfs
    enable_loginnode = params.enable_loginnode
    loginnode_instance_type = params.loginnode_instance_type
    loginnode_count = params.loginnode_count
    gpu_instance_type = params.gpu_instance_type
    gpu_root_volume_size = params.gpu_root_volume_size
    gpu_root_volume_type = params.gpu_root_volume_type
    gpu_root_volume_iops = params.gpu_root_volume_iops
    gpu_root_volume_throughput = params.gpu_root_volume_throughput
    enable_fsx = params.enable_fsx
    enable_fsx_hydration = params.enable_fsx_hydration
    enable_hpc_benchmarks = params.enable_hpc_benchmarks
    enable_monitoring = params.enable_monitoring
    monitoring_version = params.monitoring_version
    monitoring_version_checksum = params.monitoring_version_checksum
    docker_compose_version = params.docker_compose_version
    stage_docker_compose = params.stage_docker_compose
    docker_compose_arch = params.docker_compose_arch
    docker_compose_checksum = params.docker_compose_checksum
    external_nfs_server = params.external_nfs_server
    fsx_chunk_size = params.fsx_chunk_size
    fsx_s3_export_bucket = params.fsx_s3_export_bucket
    fsx_s3_export_path = params.fsx_s3_export_path
    fsx_s3_import_bucket = params.fsx_s3_import_bucket
    fsx_s3_import_path = params.fsx_s3_import_path
    fsx_size = params.fsx_size
    head_node_bootstrap_timeout = params.head_node_bootstrap_timeout
    _configured_bootstrap_timeout = params.configured_head_node_bootstrap_timeout
    hyperthreading = params.hyperthreading
    initial_cpu_queue_size = params.initial_cpu_queue_size
    initial_gpu_queue_size = params.initial_gpu_queue_size
    maintain_cpu_initial_size = params.maintain_cpu_initial_size
    maintain_gpu_initial_size = params.maintain_gpu_initial_size
    headnode_instance_type = params.headnode_instance_type
    headnode_root_volume_size = params.headnode_root_volume_size
    headnode_root_volume_type = params.headnode_root_volume_type
    headnode_root_volume_iops = params.headnode_root_volume_iops
    headnode_root_volume_throughput = params.headnode_root_volume_throughput
    max_cpu_queue_size = params.max_cpu_queue_size
    max_gpu_queue_size = params.max_gpu_queue_size
    placement_group = params.placement_group
    pre_install_script = params.pre_install_script
    post_install_script = params.post_install_script
    prod_level = params.prod_level
    project_id = params.project_id
    pcluster_create_timeout = params.pcluster_create_timeout
    scaledown_idletime = params.scaledown_idletime
    scheduler = params.scheduler
    turbot_account = params.turbot_account
    vpc_name = params.vpc_name
    headnode_subnet_id = params.headnode_subnet_id
    loginnode_subnet_id = params.loginnode_subnet_id
    compute_az_list = params.compute_az_list
    compute_subnet_ids_override = params.compute_subnet_ids_override
    use_private_compute_subnet = params.use_private_compute_subnet
    gpu_az_list = params.gpu_az_list
    gpu_subnet_ids_override = params.gpu_subnet_ids_override
    use_private_gpu_subnet = params.use_private_gpu_subnet

    vars_file_path = os.path.join(src_dir, "vars_files", cluster_name + ".yml")
    os.makedirs(os.path.join(src_dir, "vars_files"), exist_ok=True)

    # Check for an existing vars_file before making any API calls.
    if os.path.isfile(vars_file_path):
        print("\n*** WARNING ***")
        print('An existing vars_file for cluster "' + cluster_name + '" was found!')
        print("")
        print("Please delete this cluster properly and retry the build:")
        print(
            "./kill_pcluster.py -N "
            + cluster_name
            + " -O "
            + cluster_owner
            + " -A "
            + az
        )
        print(cluster_build_command)
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
    ec2client = boto3.client("ec2", region_name=region)
    stsclient = boto3.client("sts", region_name=region)
    pricing_client = boto3.client("pricing", region_name="us-east-1")

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
            ec2client=ec2client,
            az=az,
            vpc_name=vpc_name,
            headnode_subnet_id=headnode_subnet_id,
            compute_az_list=compute_az_list,
            compute_subnet_ids_override=compute_subnet_ids_override,
            use_private_compute_subnet=use_private_compute_subnet,
            cluster_name=cluster_name,
            gpu_az_list=gpu_az_list,
            gpu_subnet_ids_override=gpu_subnet_ids_override,
            use_private_gpu_subnet=use_private_gpu_subnet,
            enable_loginnode=_b(enable_loginnode),
            loginnode_subnet_id=loginnode_subnet_id,
        )
        _fut_account = _pool.submit(_get_account_id)
        _fut_describe = _pool.submit(_check_cluster_exists)

    try:
        (
            vpc_id,
            subnet_id,
            compute_subnet_ids,
            gpu_subnet_ids,
            vpc_cidr,
            loginnode_subnet_id,
        ) = _fut_network.result()
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
        os.path.join(repo_root, "active_clusters", cluster_name) + os.sep
    )

    # Check for an existing state directory for this cluster.

    # boto3/Python twin of create_pcluster.yml's "Create a local state
    # directory for this cluster" (file, state: directory, mode: "0755").
    # Ansible's file: module chmods *after* creation, bypassing umask;
    # os.makedirs(path, mode=0o755) alone does not -- the requested mode is
    # ANDed with the process umask, so a restrictive umask (e.g. 077) can
    # silently produce a narrower directory mode (0o700) than requested.
    # The explicit os.chmod() after makedirs matches Ansible's own two-step
    # behavior regardless of the caller's umask.
    os.makedirs(cluster_data_dir, exist_ok=True)
    os.chmod(cluster_data_dir, 0o755)
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

    # headnode_instance_type is required — no hardcoded default.
    if not headnode_instance_type or headnode_instance_type == "default":
        refer_to_docs_and_quit(
            "headnode_instance_type is required.\n"
            "  Set it in your defaults file or pass --headnode_instance_type."
        )

    # At least one of compute_instance_type or gpu_instance_type must be non-empty.
    if not (compute_instance_type or "").strip() and not (gpu_instance_type or "").strip():
        refer_to_docs_and_quit(
            "At least one queue must be defined.\n"
            "  Set compute_instance_type, gpu_instance_type, or both."
        )

    cpu_instance_types = parse_instance_type_list(compute_instance_type)
    gpu_instance_types = parse_instance_type_list(gpu_instance_type)

    enable_cpu_queue = bool(cpu_instance_types)
    enable_gpu_queue = bool(gpu_instance_types)
    enable_gpu = enable_gpu_queue  # derived; not user-settable

    # Validate CPU types: must not be GPU families.
    for _t in cpu_instance_types:
        if _t not in ec2_instances_full_list:
            p_fail(_t, "compute_instance_type", ec2_instances_full_list)
        if is_gpu_instance(_t):
            refer_to_docs_and_quit(
                f"compute_instance_type contains a GPU instance family: {_t}\n"
                f"  GPU instances belong in gpu_instance_type, not compute_instance_type."
            )
        base_os_instance_check(base_os, _t, debug_mode)

    # Validate GPU types: must be GPU families.
    for _t in gpu_instance_types:
        if _t not in ec2_instances_full_list:
            p_fail(_t, "gpu_instance_type", ec2_instances_full_list)
        if not is_gpu_instance(_t):
            refer_to_docs_and_quit(
                f"gpu_instance_type contains a non-GPU instance type: {_t}\n"
                f"  Non-GPU instances belong in compute_instance_type, not gpu_instance_type."
            )
        base_os_instance_check(base_os, _t, debug_mode)

    # Validate headnode.
    if headnode_instance_type not in ec2_instances_full_list:
        p_fail(headnode_instance_type, "headnode_instance_type", ec2_instances_full_list)
    base_os_instance_check(base_os, headnode_instance_type, debug_mode)

    # Validate login node, gated on enable_loginnode — mirrors how GPU-queue
    # instance types are only checked when that queue is enabled.
    if enable_loginnode:
        if loginnode_instance_type not in ec2_instances_full_list:
            p_fail(loginnode_instance_type, "loginnode_instance_type", ec2_instances_full_list)
        base_os_instance_check(base_os, loginnode_instance_type, debug_mode)
        # AWS's own LoginNodesPoolSchema.count floors at 0 (a defined-but-empty
        # pool); no upper bound is enforced there either.
        if loginnode_count < 0:
            refer_to_docs_and_quit(
                f"loginnode_count must be >= 0, got {loginnode_count}."
            )

    # Architecture check — the head node and every queue type must share one
    # architecture. The head node is included here because base_os_instance_check
    # only knows the hardcoded ARM family prefixes above; describe_instance_types
    # is authoritative and covers families that list does not yet name.
    # describe_instance_types accepts max 100 per call; paginate in chunks.
    _all_instance_types = [headnode_instance_type] + cpu_instance_types + gpu_instance_types
    if enable_loginnode:
        _all_instance_types = _all_instance_types + [loginnode_instance_type]
    _arch_map = {}
    _gpu_count_map = {}
    _vcpu_map = {}
    _chunk_size = 100
    for _i in range(0, len(_all_instance_types), _chunk_size):
        _chunk = _all_instance_types[_i:_i + _chunk_size]
        _resp = ec2client.describe_instance_types(InstanceTypes=_chunk)
        for _info in _resp["InstanceTypes"]:
            _archs = _info["ProcessorInfo"]["SupportedArchitectures"]
            _arch_map[_info["InstanceType"]] = "arm64" if "arm64" in _archs else "x86_64"
            _gpu_count_map[_info["InstanceType"]] = nvidia_gpu_count(_info)
            _vcpu_map[_info["InstanceType"]] = usable_vcpu_count(
                _info, hyperthreading=hyperthreading
            )
    _unique_archs = set(_arch_map.values())
    if len(_unique_archs) > 1:
        _arch_detail = ", ".join(f"{t}={a}" for t, a in _arch_map.items())
        refer_to_docs_and_quit(
            f"The head node and all compute instance types must share the same CPU architecture.\n"
            f"  Mixed architectures detected: {_arch_detail}\n"
            f"  Use only x86_64 or only arm64/Graviton types."
        )

    # The base_os architecture must match the instance architecture. This catches
    # families absent from the ARM prefix list in base_os_instance_check.
    _cluster_arch = next(iter(_unique_archs), None)
    if _cluster_arch:
        _base_os_arch = "arm64" if base_os.endswith("arm") else "x86_64"
        if _cluster_arch != _base_os_arch:
            refer_to_docs_and_quit(
                f"base_os={base_os} is {_base_os_arch} but the selected instance types "
                f"are {_cluster_arch}.\n"
                f"  Mixed: {', '.join(f'{t}={a}' for t, a in _arch_map.items())}"
            )

    p_val("headnode_instance_type", debug_mode)
    p_val("headnode_root_volume_size", debug_mode)

    # EFA: all CPU queue types must be EFA-capable (hard fail).
    if enable_efa:
        _efa_types = _get_efa_instance_types(ec2client, ec2_instances_efa)
        _non_efa = [t for t in cpu_instance_types if t not in _efa_types]
        if _non_efa:
            refer_to_docs_and_quit(
                f"EFA is enabled but the following CPU compute instance type(s) do not support EFA:\n"
                f"  {', '.join(_non_efa)}\n"
                f"  All CPU queue types must be EFA-capable when --enable_efa=true.\n"
                f"  See: https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/efa.html"
            )
        if base_os not in base_os_efa:
            error_msg = base_os + " does not support Elastic Fabric Adapter (EFA)!"
            refer_to_docs_and_quit(error_msg)
        if placement_group == "NONE":
            placement_group = "DYNAMIC"
        p_val("placement_group", debug_mode)

    p_val("compute_root_volume_size", debug_mode)
    p_val("base_os", debug_mode)
    print("Selected base operating system: " + base_os)
    print("Selected head node instance type: " + headnode_instance_type)
    if enable_cpu_queue:
        print(f"  CPU queue: {', '.join(cpu_instance_types)}")
    if enable_gpu_queue:
        print(f"  GPU queue: {', '.join(gpu_instance_types)}")

    # Ranks-per-node for the benchmark job script's GPU queue. A queue may hold
    # several instance types with different GPU counts, so take the minimum: a
    # value every node in the queue can satisfy. Zero means the GPU types report
    # no NVIDIA devices, and the job script falls back to CPU-shaped ranks.
    gpu_ranks_per_node = (
        min(_gpu_count_map.get(t, 0) for t in gpu_instance_types)
        if gpu_instance_types
        else 0
    )

    # Schedulable vCPUs per node in each queue, taken the same way: the minimum
    # every node in the queue can satisfy. These replaced two hardcoded
    # instance-size ladders in the default Slurm submission script that named
    # eleven suffixes each and emitted no --ntasks at all for anything else --
    # c8g.medium, c7i.16xlarge, c6a.metal and every other unlisted size fell
    # through to a commented-out fallback, so the job silently ran on one task.
    # These count cores, not GPUs: gpu_ranks_per_node above is NVIDIA device
    # count, which is the right rank shape for a GPU benchmark and the wrong one
    # for --ntasks on a general-purpose job. Zero means the queue does not exist.
    cpu_ranks_per_node = derive_ranks_per_node(
        instance_types=cpu_instance_types, vcpu_map=_vcpu_map
    )
    gpu_vcpus_per_node = derive_ranks_per_node(
        instance_types=gpu_instance_types, vcpu_map=_vcpu_map
    )

    s3_bucketname = "parallelclustermaker-" + cluster_serial_number
    # Long-lived and account+region-scoped, so benchmark results survive the
    # teardown of the per-build bucket above.  See _derive_results_bucket.
    results_bucketname = _derive_results_bucket(
        aws_account_id=aws_account_id, region=region
    )
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

    # Normalize FSx S3 bucket/path configuration (no AWS calls — pure variable logic).
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

    if enable_external_nfs:
        _check_external_nfs_reachable(external_nfs_server)

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
        headnode_size=headnode_root_volume_size,
        headnode_type=headnode_root_volume_type,
        headnode_iops=headnode_root_volume_iops,
        headnode_throughput=headnode_root_volume_throughput,
        compute_size=compute_root_volume_size,
        compute_type=compute_root_volume_type,
        compute_iops=compute_root_volume_iops,
        compute_throughput=compute_root_volume_throughput,
        gpu_size=gpu_root_volume_size,
        gpu_type=gpu_root_volume_type,
        gpu_iops=gpu_root_volume_iops,
        gpu_throughput=gpu_root_volume_throughput,
        shared_size=ebs_shared_volume_size,
        shared_type=ebs_shared_volume_type,
        shared_iops=ebs_shared_volume_iops,
        shared_throughput=ebs_shared_volume_throughput,
        enable_cpu_queue=enable_cpu_queue,
        enable_gpu_queue=enable_gpu_queue,
    )
    _validate_ebs_shared_dir(ebs_shared_dir)
    p_val("ebs_shared_dir", debug_mode)

    # Validate EFS based on the selected performance mode.

    if enable_efs:
        p_val("efs_performance_mode", debug_mode)
        p_val("efs_throughput_mode", debug_mode)
        p_val("efs_encryption", debug_mode)

    # PCluster starts the head node's wait condition before the head node
    # exists, so filesystem provisioning eats into the bootstrap budget.
    if head_node_bootstrap_timeout != _configured_bootstrap_timeout:
        _fs_driver = "FSx for Lustre" if enable_fsx else "EFS"
        print(
            f"  *** INFO *** {_fs_driver} provisioning runs on the head node's\n"
            f"  critical path while CloudFormation is already timing the bootstrap,\n"
            f"  so head_node_bootstrap_timeout was raised to "
            f"{head_node_bootstrap_timeout}s (default 2100s).\n"
            f"  Set head_node_bootstrap_timeout in the defaults file to override."
        )

    # If a custom_ami was provided, perform error checking on its existence.

    if custom_ami != "NONE":
        try:
            ec2client.describe_images(ImageIds=[custom_ami])
        except _ClientError:
            error_msg = '"' + custom_ami + '" does not appear to be a valid AMI!'
            refer_to_docs_and_quit(error_msg)
        else:
            p_val("custom_ami", debug_mode)

    # Fetch current EC2 spot prices for display only.
    # PCluster v3 uses CapacityType: SPOT and manages bid pricing automatically
    # at the fleet level — no SpotPrice field exists in the v3 config.
    #
    # If the user selects ondemand instances, print a friendly reminder to the
    # console that spot is a more economical choice for HPC clusters.

    spot_prices_by_type = {}
    if cluster_type == "ondemand":
        p_val("cluster_type", debug_mode)
        print("  On-Demand instances were selected")
        print("  *Hint* ==> spot instances are more cost-effective for HPC!!")
        print("")
    elif cluster_type == "spot":
        p_val("cluster_type", debug_mode)
        _pricing_types = cpu_instance_types + gpu_instance_types
        if _pricing_types:
            # One batched call for all types; take the most recent price per type.
            # MaxResults=1000 is the hard API limit; each type returns multiple AZ
            # entries so paginate with a generous cap.
            _spot_resp = ec2client.describe_spot_price_history(
                InstanceTypes=_pricing_types,
                MaxResults=min(len(_pricing_types) * 20, 1000),
                ProductDescriptions=["Linux/UNIX (Amazon VPC)"],
                AvailabilityZone=az,
            )
            _latest = {}
            for _entry in _spot_resp["SpotPriceHistory"]:
                _it = _entry["InstanceType"]
                if _it not in _latest:
                    _latest[_it] = float(_entry["SpotPrice"])
            for _itype in _pricing_types:
                if _itype not in _latest:
                    refer_to_docs_and_quit(
                        f"Instance type {_itype} is unavailable on the Spot market in {az}."
                    )
                spot_prices_by_type[_itype] = _latest[_itype]
            print("  *** INFO *** PCluster v3 manages Spot bid pricing automatically at the")
            print("  fleet level. The prices below are current market rates for reference only.")
            print("  No bid price is set in the cluster config.")
            if cpu_instance_types:
                print("  CPU queue current spot prices:")
                for _t in cpu_instance_types:
                    print(f"    {_t}: ${spot_prices_by_type[_t]:.6f}/hr")
            if gpu_instance_types:
                print("  GPU queue current spot prices:")
                for _t in gpu_instance_types:
                    print(f"    {_t}: ${spot_prices_by_type[_t]:.6f}/hr")
            print("")
            print("  *** INFO *** To improve Spot acquisition rates:")
            print("    - Add subnets from multiple AZs to --compute_subnet_ids")
            print("      (Spot capacity is AZ-specific; one subnet = one pool)")
            print("    - Specify multiple instance types of similar size in")
            print("      --compute_instance_type / --gpu_instance_type")
            print("      (e.g. c6i.2xlarge,c7i.2xlarge,c5.2xlarge)")
            print("")
        p_val("cluster_type", debug_mode)
    else:
        p_fail(cluster_type, "cluster_type", ["ondemand", "spot"])

    # Turbot profile was activated earlier (after AZ verification) so all boto3
    # clients from this point already use the correct cross-account credentials.

    # Instantiate the S3 client — Turbot profile already active if applicable.
    #
    # region_name is load-bearing, not tidiness: S3 is regional, and an
    # unbound client resolves its endpoint from the ambient environment
    # (AWS_DEFAULT_REGION/AWS_REGION/profile), which need not be the region
    # this build targets. CreateBucket then correctly omits
    # LocationConstraint for a us-east-1 target while sending the request to
    # some other region's endpoint, and S3 rejects it with
    # IllegalLocationConstraintException -- after the IAM roles exist.
    # Every regional client here must be bound the same way; iam and sts are
    # global and deliberately are not.
    s3_client = boto3.client("s3", region_name=region)

    # Check s3_bucketname using the correct (post-Turbot) credentials.
    try:
        s3_client.head_bucket(Bucket=s3_bucketname)
        if _resuming:
            print(f"  Found existing S3 bucket from interrupted run: {s3_bucketname}")
            p_val("s3_bucketname", debug_mode)
        else:
            error_msg = "Found an existing S3 bucket associated with this cluster!"
            refer_to_docs_and_quit(error_msg)
    except _ClientError as _e:
        if _e.response["Error"]["Code"] in ("404", "NoSuchBucket"):
            p_val("s3_bucketname", debug_mode)
        else:
            raise

    # Validate FSx S3 import/export bucket and path existence with the correct
    # (post-Turbot) credentials.
    if enable_fsx and enable_fsx_hydration:
        _check_fsx_s3(s3_client, fsx_s3_import_bucket, fsx_s3_import_path, "import")
        _check_fsx_s3(
            s3_client,
            fsx_s3_export_bucket,
            fsx_s3_export_path,
            "export",
            require_objects=False,
        )
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

    # Acquired here, right before the first AWS mutation, and held through
    # the rest of the build -- same "before spending anything" placement as
    # the checksum/external-NFS pre-flight checks. Guards against a second
    # process (e.g. a concurrent kill_pcluster.py) touching this cluster
    # name's state mid-build, now visible to any caller regardless of
    # machine (see s3_acquire_cluster_lock's own docstring for why the
    # local mkdir lock this replaced could not do that).
    import pcluster.lib as pc
    _lock_s3 = boto3.client("s3", region_name=region)
    locks_bucketname = _derive_locks_bucket(aws_account_id=aws_account_id, region=region)
    _lock_path = _acquire_distributed_cluster_lock(
        _lock_s3, locks_bucketname=locks_bucketname, region=region,
        cluster_name=cluster_name,
        command="make_pcluster.py " + " ".join(sys.argv[1:]),
        describe_fn=pc.describe_cluster,
    )

    print("  Setting up IAM roles and policies...")

    iam = boto3.client("iam")
    ec2_iam_policy = "pclustermaker-policy-" + cluster_serial_number
    ec2_iam_role = "pclustermaker-role-" + cluster_serial_number
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
        s3_release_cluster_lock(_lock_s3, locks_bucketname=locks_bucketname, cluster_name=cluster_name)
        sys.exit(1)

    try:
        if enable_fsx_hydration:
            fsx_hydration_iam_policy = (
                "pclustermaker-fsx-s3-policy-" + cluster_serial_number
            )
            fsx_hydration_json_policy_src = os.path.join(
                repo_root, "templates", "LustreS3HydrationPolicy.json_src"
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
        "local_workingdir": repo_root,
        "cluster_rootdir": repo_root,
        # Absolute, because the shell tasks that use it quote it: an unexpanded
        # "~/.ssh/known_hosts" makes ssh-keygen -R a no-op and sends the keyscan
        # append into a nonexistent ./~/.ssh directory.
        "ssh_known_hosts": os.path.join(
            os.path.expanduser("~"), ".ssh", "known_hosts"
        ),
        # Pre-computed path variables referenced by vars_file.j2 as Jinja2 expressions.
        # Plain Python Jinja2 does not evaluate YAML output lines as variables, so every
        # {{ cluster_data_dir }}, {{ stage_dir }}, etc. reference in the template must be
        # supplied explicitly in the render context.
        "cluster_data_dir": os.path.join(repo_root, "active_clusters", cluster_name),
        "cluster_template_dir": os.path.join(repo_root, "templates"),
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
        "gpu_subnet_ids": gpu_subnet_ids,
        "use_private_gpu_subnet": use_private_gpu_subnet,
        "base_os": base_os,
        "pcluster_os": base_os.removesuffix("arm"),
        "cluster_name": cluster_name,
        "cluster_owner": cluster_owner,
        "cluster_owner_email": cluster_owner_email,
        "cluster_owner_department": cluster_owner_department,
        "cluster_serial_datestamp": cluster_serial_datestamp,
        "cluster_serial_number": cluster_serial_number,
        "cluster_serial_number_file": cluster_serial_number_file,
        "cluster_type": cluster_type,
        "compute_instance_type": compute_instance_type,
        "cpu_instance_types": cpu_instance_types,
        "gpu_instance_type": gpu_instance_type,
        "gpu_instance_types": gpu_instance_types,
        "compute_root_volume_size": compute_root_volume_size,
        "compute_root_volume_type": compute_root_volume_type,
        "compute_root_volume_iops": compute_root_volume_iops,
        "compute_root_volume_throughput": compute_root_volume_throughput,
        "gpu_root_volume_size": gpu_root_volume_size,
        "gpu_root_volume_type": gpu_root_volume_type,
        "gpu_root_volume_iops": gpu_root_volume_iops,
        "gpu_root_volume_throughput": gpu_root_volume_throughput,
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
        "enable_gpu": _b(enable_gpu),
        "enable_cpu_queue": _b(enable_cpu_queue),
        "enable_gpu_queue": _b(enable_gpu_queue),
        "gpu_ranks_per_node": gpu_ranks_per_node,
        "cpu_ranks_per_node": cpu_ranks_per_node,
        "gpu_vcpus_per_node": gpu_vcpus_per_node,
        "enable_efa_gdr": _b(enable_efa and any(needs_efa_gdr(t) for t in cpu_instance_types + gpu_instance_types)),
        "enable_external_nfs": _b(enable_external_nfs),
        "enable_loginnode": _b(enable_loginnode),
        "loginnode_instance_type": loginnode_instance_type,
        "loginnode_count": loginnode_count,
        "loginnode_subnet_id": loginnode_subnet_id,
        "enable_fsx": _b(enable_fsx),
        "enable_fsx_hydration": _b(enable_fsx_hydration),
        "enable_hpc_benchmarks": _b(enable_hpc_benchmarks),
        "enable_monitoring": _b(enable_monitoring),
        "monitoring_version": monitoring_version,
        "monitoring_version_checksum": monitoring_version_checksum,
        "monitoring_s3_dest": f"monitoring-post-install-wrapper.{cluster_name}.sh",
        "docker_compose_version": docker_compose_version,
        "docker_compose_arch": docker_compose_arch,
        "docker_compose_checksum": docker_compose_checksum,
        "stage_docker_compose": _b(stage_docker_compose),
        "external_nfs_server": external_nfs_server,
        "head_node_bootstrap_timeout": head_node_bootstrap_timeout,
        "fsx_chunk_size": fsx_chunk_size,
        "fsx_hydration_iam_policy": fsx_hydration_iam_policy,
        "fsx_s3_export_bucket": fsx_s3_export_bucket,
        "fsx_s3_export_path": fsx_s3_export_path,
        "fsx_s3_import_bucket": fsx_s3_import_bucket,
        "fsx_s3_import_path": fsx_s3_import_path,
        "fsx_size": fsx_size,
        "hyperthreading": _b(hyperthreading),
        "initial_cpu_queue_size": initial_cpu_queue_size,
        "initial_gpu_queue_size": initial_gpu_queue_size,
        "maintain_cpu_initial_size": _b(maintain_cpu_initial_size),
        "maintain_gpu_initial_size": _b(maintain_gpu_initial_size),
        "max_cpu_queue_size": max_cpu_queue_size,
        "max_gpu_queue_size": max_gpu_queue_size,
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
        "region": region,
        "s3_bucketname": s3_bucketname,
        "results_bucketname": results_bucketname,
        "pcluster_create_timeout": pcluster_create_timeout,
        "scaledown_idletime": scaledown_idletime,
        "scheduler": scheduler,
        "subnet_id": subnet_id,
        "vpc_cidr": vpc_cidr,
        "vpc_id": vpc_id,
        "turbot_account": turbot_account,
        "vpc_name": vpc_name,
        "Deployed_On": Deployed_On,
        "ANSIBLE_VERSION": ansible_version,
        "DEPLOYMENT_DATE": DEPLOYMENT_DATE_TAG,
    }

    # Print the current values of all validated cluster_parameters to the console
    # when debug mode is enabled.

    if debug_mode:
        from pcluster_aux_data import print_TextHeader

        print_TextHeader(cluster_name, "Displaying cluster parameter values", 80)
        print("ANSIBLE_VERSION = " + ansible_version)
        print("DEPLOYMENT_DATE = " + DEPLOYMENT_DATE)
        print("aws_account_id = " + aws_account_id)
        print("base_os = " + base_os)
        print("cluster_name = " + cluster_name)
        print("cluster_owner = " + cluster_owner)
        print("cluster_owner_department = " + cluster_owner_department)
        print("cluster_owner_email = " + cluster_owner_email)
        print("cluster_serial_datestamp = " + cluster_serial_datestamp)
        print("cluster_serial_number = " + cluster_serial_number)
        print("cluster_serial_number_file = " + cluster_serial_number_file)
        print("cluster_type = " + cluster_type)
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
        print("    initial_cpu_queue_size = " + str(initial_cpu_queue_size))
        print("    maintain_cpu_initial_size = " + str(maintain_cpu_initial_size))
        print("    max_cpu_queue_size = " + str(max_cpu_queue_size))
        print("    initial_gpu_queue_size = " + str(initial_gpu_queue_size))
        print("    maintain_gpu_initial_size = " + str(maintain_gpu_initial_size))
        print("    max_gpu_queue_size = " + str(max_gpu_queue_size))
        print("scaledown_idletime = " + str(scaledown_idletime))
        print("subnet_id (headnode) = " + subnet_id)
        print("compute_az_list = " + ", ".join(compute_az_list))
        print("compute_subnet_ids = " + ", ".join(compute_subnet_ids))
        if use_private_compute_subnet == "true":
            print("use_private_compute_subnet = true")
        print("gpu_subnet_ids = " + ", ".join(gpu_subnet_ids))
        if use_private_gpu_subnet == "true":
            print("use_private_gpu_subnet = true")
        print("vpc_id = " + vpc_id)
        print("vpc_name = " + vpc_name)

    # Generate the vars_file and the Tier 1 static templates (Workstream 2):
    # kill_pcluster.j2, access_cluster.j2, and retrieve_ssh_key.j2 render
    # from cluster_parameters alone, with no dependency on anything
    # create_pcluster.yml's own AWS calls produce, so they render here in
    # Python instead of via an Ansible `template:` task. create_pcluster.yml's
    # downstream tasks (the scp of stage_dir to the head node, the cp -a into
    # cluster_data_dir) read whichever files already exist at these paths
    # regardless of which system wrote them.
    #
    # config.pcluster.j2 does NOT belong in this set, despite the plan
    # originally calling it Tier 1 (purely static): when enable_external_nfs
    # is true it references {{ external_nfs_sg.group_id }}, an Ansible
    # `register:` result from a security group create_pcluster.yml's own
    # `amazon.aws.ec2_security_group` task creates -- a real runtime AWS
    # value that does not exist until that task runs, which is well after
    # this function would need it. Caught empirically, not by re-reading the
    # template harder: a smoke test with --enable_external_nfs=true failed
    # with "'external_nfs_sg' is undefined" against Tier 1's original
    # all-four-templates version of this function. Moving the security
    # group's creation to Python is a Workstream 3 (boto3 swap) change, not
    # a Workstream 2 (templating) one, so config.pcluster.j2 stays an
    # Ansible `template:` task until that lands.

    _templates_dir = os.path.join(repo_root, "templates")
    stage_dir = cluster_parameters["stage_dir"]

    try:
        _rendered_vars_file = render_template(
            _templates_dir, "vars_file.j2", **cluster_parameters
        )
        print(f"  Writing vars file: {vars_file_path}")
        with open(
            os.open(vars_file_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w"
        ) as _vf:
            _vf.write(_rendered_vars_file)

        # The templates rendered below reference names (preinstall_s3_dest,
        # ssh_keypair, ...) that vars_file.j2 itself derives with its own
        # Jinja2 expressions -- they are not in cluster_parameters directly,
        # only in what vars_file.j2 renders from it, which is what Ansible's
        # own vars_files: load gave these templates before Workstream 2. But
        # the reverse gap is real too: two cluster_parameters keys
        # (debug_mode, Deployed_On) are never re-emitted by vars_file.j2 at
        # all, confirmed by checking every key against the template's own
        # text rather than assumed -- Deployed_On is what several of these
        # templates' "Deployed On:" comments need once their Ansible-only
        # `lookup('pipe', 'date ...')` calls are replaced with it (see the
        # template-level fix in this same round). Merge both so every
        # template gets the union, with vars_file.j2's own rendering winning
        # on the (identical-value) keys both carry.
        _vars_file_context = {**cluster_parameters, **yaml.safe_load(_rendered_vars_file)}

        # Tier 3: the toolkit's own pre/postinstall pair. 28KB, the heaviest
        # branching of any template in the repo, and confirmed to carry no
        # runtime-AWS-call dependency (unlike config.pcluster.j2) by the same
        # variable-by-variable check Tier 1/2 already applied, plus a
        # byte-for-byte parity check against every tests/conftest.py fixture
        # combination before this task was deleted from create_pcluster.yml
        # (TestPreinstallPostinstallByteForByteParity, tests/test_templates.py).
        for _tmpl_name, _dest_name in (
            ("preinstall.j2", f"preinstall.{cluster_name}.sh"),
            ("postinstall.j2", f"postinstall.{cluster_name}.sh"),
        ):
            _rendered_install = render_template(_templates_dir, _tmpl_name, **_vars_file_context)
            _install_dest = os.path.join(cluster_data_dir, _dest_name)
            with open(
                os.open(_install_dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o755), "w"
            ) as _if:
                _if.write(_rendered_install)

        os.makedirs(stage_dir, exist_ok=True)
        for _tmpl_name, _dest_name in (
            ("kill_pcluster.j2", f"kill_pcluster.{cluster_name}.sh"),
            ("access_cluster.j2", f"access_cluster.{cluster_name}.sh"),
            ("retrieve_ssh_key.j2", f"retrieve_ssh_key.{cluster_name}.sh"),
        ):
            _rendered_script = render_template(_templates_dir, _tmpl_name, **_vars_file_context)
            _dest_path = os.path.join(stage_dir, _dest_name)
            with open(
                os.open(_dest_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o755), "w"
            ) as _tf:
                _tf.write(_rendered_script)

        # Tier 2: gated templates whose upload/publish step stays an Ansible
        # task pointed at the pre-rendered file (Workstream 2's own
        # instruction, to decouple this rendering change from Workstream 3's
        # eventual boto3 swap of those uploads).
        if enable_monitoring:
            _rendered_wrapper = render_template(
                _templates_dir, "monitoring-post-install-wrapper.j2", **_vars_file_context
            )
            with open(
                os.open(
                    _vars_file_context["monitoring_wrapper_dest"],
                    os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o755,
                ),
                "w",
            ) as _mf:
                _mf.write(_rendered_wrapper)

            _rendered_grafana = render_template(
                _templates_dir, "grafana_tunnel.j2", **_vars_file_context
            )
            with open(
                os.open(
                    _vars_file_context["grafana_tunnel_dest"],
                    os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o755,
                ),
                "w",
            ) as _gf:
                _gf.write(_rendered_grafana)

        if enable_external_nfs:
            # Same template, two destinations, two modes -- matches the two
            # Ansible tasks this replaces exactly rather than writing once
            # and copying, since the original never assumed the two stayed
            # byte-identical either.
            _rendered_nfs = render_template(
                _templates_dir, "external_nfs_mount_list.j2", **_vars_file_context
            )
            with open(
                os.open(
                    _vars_file_context["external_nfs_mount_list_template_src"],
                    os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644,
                ),
                "w",
            ) as _nf1:
                _nf1.write(_rendered_nfs)
            _nfs_stage_dest = os.path.join(
                stage_dir, _vars_file_context["external_nfs_mount_list_template_dest"]
            )
            with open(
                os.open(_nfs_stage_dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o755), "w"
            ) as _nf2:
                _nf2.write(_rendered_nfs)

        if enable_hpc_benchmarks:
            _performance_stage_dir = _vars_file_context["performance_stage_dir"]
            os.makedirs(_performance_stage_dir, exist_ok=True)
            _hpc_benchmark_dir = os.path.join(repo_root, "hpc-benchmark")
            for _tmpl_name, _dest_name in (
                ("job_hpc-benchmark.sh.j2", "job_hpc-benchmark.sh"),
                ("README-PERFORMANCE.md.j2", "README-PERFORMANCE.md"),
            ):
                _rendered_perf = render_template(
                    _hpc_benchmark_dir, _tmpl_name, **_vars_file_context
                )
                _perf_dest = os.path.join(_performance_stage_dir, _dest_name)
                with open(
                    os.open(_perf_dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o755), "w"
                ) as _pf:
                    _pf.write(_rendered_perf)

        # Unconditional -- every build gets the default Slurm submission
        # script, regardless of enable_hpc_benchmarks.
        _rendered_sbatch = render_template(
            os.path.join(repo_root, "scripts"),
            "sbatch_default_submission_script.sh",
            **_vars_file_context,
        )
        _sbatch_dest = os.path.join(stage_dir, "sbatch_default_submission_script.sh")
        with open(
            os.open(_sbatch_dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o755), "w"
        ) as _sf:
            _sf.write(_rendered_sbatch)
    except Exception as _render_e:
        print(f"\n*** ERROR ***\n" f"  Template render failed: {_render_e}")
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
        s3_release_cluster_lock(_lock_s3, locks_bucketname=locks_bucketname, cluster_name=cluster_name)
        sys.exit(1)

    # Every remaining create_pcluster.yml task, wired straight to Python --
    # this retires the playbook from execution entirely, matching what
    # round 19 did for delete_pcluster.yml on the teardown side.
    # create_pcluster.yml itself stays in the repo, unexecuted, as the
    # reference spec every function below cites.
    #
    # Two failure-handling shapes, matching the Ansible original's own two
    # shapes exactly (both collapsed into a single ansible-playbook exit
    # code there, but distinguishable here since each stage is its own
    # Python call): everything up through the last upload happens *before*
    # CloudFormation has created anything, so a failure there rolls back
    # everything this build has created so far (S3 bucket, keypair, secret,
    # external NFS SG, SNS topic, IAM) -- nothing to retry, matching the
    # original block's own rescue:. Once pc.create_cluster is called,
    # CloudFormation may already own real resources; a failure from that
    # point on preserves the S3 bucket/keypair/secret/SNS topic/serial
    # file/vars file exactly like the original's post-launch failure path
    # did, and tells the operator to run kill_pcluster.py, which needs all
    # of that state to actually tear the partial stack down.

    def _rollback_pre_launch_resources():
        with contextlib.suppress(Exception):
            secretsmanager = boto3.client("secretsmanager", region_name=region)
            _delete_s3_bucket_step(s3_client, s3_bucketname)
            _delete_ec2_keypair_step(ec2client, _vars_file_context["ec2_keypair"])
            _delete_secrets_manager_secret_step(secretsmanager, _vars_file_context["ssh_secret_name"])
            if enable_external_nfs:
                _delete_external_nfs_sg_step(ec2client, cluster_name)
        if sns_topic_arn:
            with contextlib.suppress(Exception):
                sns.delete_topic(TopicArn=sns_topic_arn)
        _fsx = fsx_hydration_iam_policy if fsx_hydration_iam_policy != "UNDEFINED" else None
        _delete_managed_policies(
            iam, ec2_iam_role, ec2_iam_policy, aws_account_id,
            fsx_policy=_fsx, enable_monitoring=enable_monitoring,
        )
        with contextlib.suppress(Exception):
            iam.delete_role(RoleName=ec2_iam_role)

    def _fail_after_launch(reason):
        print("")
        print("*** ERROR ***")
        print(reason)
        print(f'Cluster "{cluster_name}" may not have been created successfully.')
        print("Cleaning up IAM resources to allow a clean retry:")
        _fsx = fsx_hydration_iam_policy if fsx_hydration_iam_policy != "UNDEFINED" else None
        _delete_managed_policies(
            iam, ec2_iam_role, ec2_iam_policy, aws_account_id,
            suppress=False, fsx_policy=_fsx, enable_monitoring=enable_monitoring,
        )
        try:
            iam.delete_role(RoleName=ec2_iam_role)
            print(f"  Deleted IAM role: {ec2_iam_role}")
        except Exception as _e:
            print(f"  Warning: could not delete role {ec2_iam_role}: {_e}")
        print("Run kill_pcluster.py to tear down any partial stack before retrying:")
        print(f"  ./kill_pcluster.py -N {cluster_name} -O {cluster_owner} -A {az}")
        s3_release_cluster_lock(_lock_s3, locks_bucketname=locks_bucketname, cluster_name=cluster_name)
        sys.exit(1)

    sns = boto3.client("sns", region_name=region)
    sns_topic_arn = None
    start_overall_timestamp = teardown_timestamp()
    try:
        sns_topic_arn = _create_sns_topic_and_notify(
            sns, cluster_name=cluster_name, cluster_owner_email=cluster_owner_email,
            start_timestamp=start_overall_timestamp,
        )

        _bucket_tags = {
            "ClusterID": cluster_name,
            "ClusterStackType": "ParallelCluster",
            "ClusterOSType": base_os,
            "ClusterScheduler": scheduler,
            "ClusterSerialNumber": cluster_serial_number,
            "ClusterOwner": cluster_owner,
            "ClusterOwnerEmail": cluster_owner_email,
            "ClusterOwnerDepartment": cluster_owner_department,
            "ProdLevel": prod_level,
            "DEPLOYMENT_DATE": DEPLOYMENT_DATE_TAG,
        }
        if project_id != "UNDEFINED":
            _bucket_tags["ProjectID"] = project_id

        secretsmanager = boto3.client("secretsmanager", region_name=region)
        external_nfs_sg_id = provision_s3_keypair_and_secret(
            s3=s3_client, ec2=ec2client, secretsmanager=secretsmanager,
            s3_bucketname=s3_bucketname, region=region, tags=_bucket_tags,
            enable_external_nfs=enable_external_nfs, cluster_name=cluster_name,
            vpc_id=vpc_id, vpc_cidr=vpc_cidr,
            ec2_keypair=_vars_file_context["ec2_keypair"],
            ssh_keypair=_vars_file_context["ssh_keypair"],
            ssh_secret_name=_vars_file_context["ssh_secret_name"],
        )

        render_and_upload_cluster_config_and_scripts(
            s3_client, ctx=_vars_file_context, external_nfs_sg_id=external_nfs_sg_id,
            templates_dir=_templates_dir,
        )

        stage_and_upload_monitoring_wrapper(
            s3_client, enable_monitoring=enable_monitoring,
            cluster_data_dir=cluster_data_dir, s3_bucketname=s3_bucketname,
            s3_script_path=_vars_file_context["s3_script_path"],
            monitoring_version=monitoring_version,
            monitoring_version_checksum=monitoring_version_checksum,
            stage_docker_compose=stage_docker_compose,
            docker_compose_version=docker_compose_version,
            docker_compose_arch=docker_compose_arch,
            docker_compose_checksum=docker_compose_checksum,
            docker_compose_local_dest=_vars_file_context.get("docker_compose_local_dest"),
            docker_compose_s3_dest=_vars_file_context.get("docker_compose_s3_dest"),
            monitoring_wrapper_dest=_vars_file_context.get("monitoring_wrapper_dest"),
            monitoring_s3_dest=_vars_file_context["monitoring_s3_dest"],
        )

        if enable_external_nfs:
            _upload_external_nfs_mount_list(s3_client, ctx=_vars_file_context)

        if enable_hpc_benchmarks:
            stage_and_upload_hpc_benchmark_driver(s3_client, ctx=_vars_file_context, region=region)
    except Exception as _early_e:
        print(f"\n*** ERROR ***\n  Exception before cluster launch: {_early_e}")
        print("Cleaning up everything this build has created so far:")
        _rollback_pre_launch_resources()
        with contextlib.suppress(FileNotFoundError):
            os.remove(cluster_serial_number_file)
        s3_release_cluster_lock(_lock_s3, locks_bucketname=locks_bucketname, cluster_name=cluster_name)
        sys.exit(1)

    # Print the pre-launch summary and open the Ctrl-C abort window right
    # before actually launching the stack -- matching the playbook's own
    # task order (launch summary, then "Launch the new ParallelCluster v3
    # stack" immediately after).

    start_stack_creation_timestamp = teardown_timestamp()
    print_cluster_launch_summary(_vars_file_context, launch_timestamp=start_stack_creation_timestamp)

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

    # pc (pcluster.lib) was already imported above, right before the lock
    # acquisition, and stays bound for the rest of this function.
    def _print_build_progress(attempt, status, cfn_status):
        """Workstream 4's scoped, disclosed exception to "CLI behavior
        unchanged": this wait was previously entirely silent for 20-45
        minutes (the Ansible until: loop printed nothing per attempt), so
        an operator had no way to tell a healthy slow build from a hung
        one without opening the CloudFormation console. The final build
        summary after it is still byte-identical."""
        elapsed = (attempt + 1) * 60
        detail = f" (CloudFormation: {cfn_status})" if cfn_status else ""
        print(
            f"  [{elapsed // 60:>3d}m] {cluster_name}: {status or 'status unavailable'}{detail}"
        )

    try:
        outcome = run_cluster_create_and_classify(
            pc.create_cluster, pc.describe_cluster, cluster_name, region,
            cluster_configuration_path=_vars_file_context["cluster_config_template"],
            progress_fn=_print_build_progress, wait=wait,
        )
    except Exception as _launch_e:
        _fail_after_launch(f"Exception launching cluster: {_launch_e}")

    if outcome.terminal_state == _KICKED_OFF:
        # Workstream 4: launched, deliberately not waited on. Everything
        # below this point needs a head node that exists and is reachable
        # -- the SSH/SCP staging transfer, the performance tree, the
        # build-summary report -- so none of it can run yet. The IAM
        # role, S3 bucket, keypair, secret, serial file and vars file are
        # all deliberately left in place: they are what the cluster is
        # being built with, and what a later completion step needs. The
        # lock is released, since this process is done with the cluster.
        print("")
        print(outcome.create_headline)
        print("")
        print("Staging files were NOT transferred to the head node and the build")
        print("summary was NOT sent -- both require a running head node. Re-run")
        print("once the cluster reaches CREATE_COMPLETE to finish those steps.")
        s3_release_cluster_lock(_lock_s3, locks_bucketname=locks_bucketname, cluster_name=cluster_name)
        sys.exit(0)

    if not outcome.create_confirmed:
        _fail_after_launch(outcome.create_headline)

    stop_stack_creation_timestamp = teardown_timestamp()

    try:
        deploy_staging_and_performance_tree_to_head_node(
            head_node_public_ip=outcome.head_node_public_ip,
            ssh_keypair=_vars_file_context["ssh_keypair"],
            ssh_known_hosts=_vars_file_context["ssh_known_hosts"],
            ec2_user=ec2_user, ec2_user_home=ec2_user_home, stage_dir=stage_dir,
            enable_hpc_benchmarks=enable_hpc_benchmarks,
            performance_stage_dir=_vars_file_context.get("performance_stage_dir"),
            headnode_performance_dir_dest=_vars_file_context.get("headnode_performance_dir_dest"),
            ebs_hpc_performance_dir=_vars_file_context.get("ebs_hpc_performance_dir"),
            enable_efs=enable_efs,
            efs_hpc_performance_dir=_vars_file_context.get("efs_hpc_performance_dir"),
            enable_fsx=enable_fsx,
            fsx_hpc_performance_dir=_vars_file_context.get("fsx_hpc_performance_dir"),
        )

        finalize_staging_directory(
            stage_dir=stage_dir, cluster_data_dir=cluster_data_dir,
            s3_bucketname=s3_bucketname, region=region,
        )

        stop_overall_timestamp = teardown_timestamp()

        render_and_publish_build_summary_report(
            sns, ctx=_vars_file_context, sns_topic_arn=sns_topic_arn,
            templates_dir=_templates_dir, head_node_public_ip=outcome.head_node_public_ip,
            start_overall_timestamp=start_overall_timestamp,
            start_stack_timestamp=start_stack_creation_timestamp,
            stop_stack_timestamp=stop_stack_creation_timestamp,
            stop_overall_timestamp=stop_overall_timestamp,
        )
    except Exception as _post_launch_e:
        _fail_after_launch(f"Exception after cluster launch: {_post_launch_e}")

    # Append make_pcluster.py's own command line to the cluster_serial_number
    # file, and upload the serial number to S3 for durability.

    with open(cluster_serial_number_file, "a") as _snf:
        print(cluster_build_command, file=_snf)

    cluster_serial_number_object = "cluster_serial_number/" + cluster_name + ".serial"
    try:
        with open(cluster_serial_number_file, "rb") as _snf:
            s3.Object(s3_bucketname, cluster_serial_number_object).put(Body=_snf)
    except Exception as _s3e:
        print(f"WARNING: could not upload serial number to S3: {_s3e}")

    _head_ip = outcome.head_node_public_ip

    # Print a human-friendly cluster build summary.
    _min_count_pinned = (enable_cpu_queue and maintain_cpu_initial_size) or (
        enable_gpu_queue and maintain_gpu_initial_size
    )
    _enabled = [
        lbl
        for lbl, flag in [
            ("EFA", enable_efa),
            ("EFS", enable_efs),
            ("FSx/Lustre", enable_fsx),
            ("External NFS", enable_external_nfs),
            ("GPU queue", enable_gpu),
            ("HPC Benchmarks", enable_hpc_benchmarks),
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
    if enable_loginnode:
        print(f"  Login Node:        {loginnode_instance_type} (x{loginnode_count})")
    if enable_cpu_queue:
        print(f"  CPU Queue:         {', '.join(cpu_instance_types)}")
    if enable_gpu_queue:
        print(f"  GPU Queue:         {', '.join(gpu_instance_types)}")
    print(f"  OS:                {base_os}")
    print(f"  Scheduler:         {scheduler}")
    if _enabled:
        print(f"  Options:           {', '.join(_enabled)}")
    for _cost_line in _cost_summary_lines(
        pricing_client=pricing_client,
        ec2client=ec2client,
        headnode_instance_type=headnode_instance_type,
        cpu_instance_types=cpu_instance_types,
        max_cpu_queue_size=max_cpu_queue_size,
        enable_cpu_queue=enable_cpu_queue,
        gpu_instance_types=gpu_instance_types,
        max_gpu_queue_size=max_gpu_queue_size,
        enable_gpu_queue=enable_gpu_queue,
        region=region,
        cluster_type=cluster_type,
        loginnode_instance_type=loginnode_instance_type,
        loginnode_count=loginnode_count,
        enable_loginnode=enable_loginnode,
    ):
        print(_cost_line)
    print("")
    for _storage_line in _storage_summary_lines(
        ebs_shared_dir=ebs_shared_dir,
        ebs_shared_volume_size=ebs_shared_volume_size,
        ebs_shared_volume_type=ebs_shared_volume_type,
        enable_efs=enable_efs,
        efs_throughput_mode=efs_throughput_mode,
        enable_fsx=enable_fsx,
        fsx_size=fsx_size,
        enable_fsx_hydration=enable_fsx_hydration,
        fsx_s3_import_bucket=fsx_s3_import_bucket,
        fsx_s3_import_path=fsx_s3_import_path,
        fsx_s3_export_bucket=fsx_s3_export_bucket,
        fsx_s3_export_path=fsx_s3_export_path,
        enable_external_nfs=enable_external_nfs,
        external_nfs_server=external_nfs_server,
    ):
        print(_storage_line)
    print("")
    print("  Idle compute:")
    print(
        f"    Compute nodes idle for more than {scaledown_idletime} minutes are "
        f"terminated automatically."
    )
    if _min_count_pinned:
        print(
            f"    NOTE: maintain_*_initial_size is set, so the queue floor stays "
            f"above zero and those nodes bill continuously."
        )
    else:
        print("    An idle cluster scales its compute fleet to zero on its own.")
    print("    The head node keeps running and billing until you tear the cluster down:")
    print(f"      ./kill_pcluster.py -N {cluster_name} -O {cluster_owner} -A {az}")
    if _head_ip:
        print("")
        if enable_loginnode:
            print("  Access the cluster (login node by default; -H for the head node):")
        else:
            print("  Access the head node:")
        print(f"    ./access_cluster.py -N {cluster_name}")
        print("  Access the head node directly:")
        print(
            f"    ssh -i active_clusters/{cluster_name}/{cluster_serial_number}_{region}.pem {ec2_user}@{_head_ip}"
        )
        print("")
        print("  SSH key (Secrets Manager):")
        _secret = _ssh_secret_name(cluster_name, cluster_serial_number)
        print(f"    Secret: {_secret}")
        print(f"    Retrieve: active_clusters/{cluster_name}/retrieve_ssh_key.{cluster_name}.sh")
        print(f"    Rotate:   ./rotate_cluster_key.py -N {cluster_name} -A {az}")
    if enable_monitoring and _head_ip:
        print("")
        print("  Grafana monitoring dashboard:")
        print(f"    Tunnel: active_clusters/{cluster_name}/grafana_tunnel.{cluster_name}.sh")
        print(f"    URL:    https://localhost:8443/grafana/  (after tunnel is open)")
        print(f"    Password: aws ssm get-parameter --region {region} \\")
        print(f"      --name /parallelcluster/{cluster_name}/grafana/admin-password \\")
        print("      --with-decryption --query Parameter.Value --output text")
    if enable_hpc_benchmarks and _head_ip:
        print("")
        print("  HPC benchmarks (run these commands on the head node):")
        print(f"    cd ~/hpc-benchmark/{cluster_name}/{cluster_owner}/{scheduler}")
        print(f"    ./hpc-benchmark.sh install")
        print(f"    ./hpc-benchmark.sh run --tests stream,osu,ior,hpcg")
        print(f"    Note: multi-node tests require a Slurm allocation (srun/sbatch)")
        print(f"    Results sync to s3://{s3_bucketname}/hpc-benchmark-results/{cluster_name}/ on teardown")
    print("")
    print("  Delete this cluster:")
    print(f"    ./kill_pcluster.py -N {cluster_name} -O {cluster_owner} -A {az}")
    print("=" * 66)
    print("")
    print("Finished creating ParallelCluster stack " + cluster_name + "!")
    print("Exiting...")
    s3_release_cluster_lock(_lock_s3, locks_bucketname=locks_bucketname, cluster_name=cluster_name)
    sys.exit(0)


# ---------------------------------------------------------------------------
# Shared Jinja2 renderer (Workstream 2: Ansible-driven templating -> pure
# Python rendering)
#
# Phase 0 of that workstream: one correctly-configured Environment every
# Python-side template render must use, so a template rendered by this
# toolkit's own Python code and one rendered by Ansible's `template:` module
# for the exact same file never silently diverge. `tests/test_templates.py`'s
# `_make_env` is this function's test-side twin, already pinned against
# Ansible's installed source by `TestTheTestEnvironmentMatchesAnsible`;
# `test_the_production_env_matches_ansible_too` extends that same class to
# pin this one the identical way.
# ---------------------------------------------------------------------------


def _template_env(templates_dir):
    """The one Jinja2 Environment every production template render must use.

    trim_blocks=True/lstrip_blocks=False matches ansible.builtin.template's
    own defaults exactly (ansible/plugins/action/template.py) -- not the
    vars_file.j2 precedent this replaces, which set neither and got away
    with it only because vars_file.j2 renders YAML, where a stray blank
    line is cosmetic. A whitespace-sensitive shell script would not get
    away with it, which is why every future Python-side template render
    must go through this one function rather than a fresh ad hoc
    `Environment(...)` at each call site.
    """
    return Environment(
        loader=_FSLoader(templates_dir),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=False,
    )


def render_template(templates_dir, template_name, **context):
    """Render template_name out of templates_dir with the shared, Ansible-
    matched Environment. context is passed straight through as render kwargs,
    same calling convention as the vars_file.j2 call site this replaces."""
    return _template_env(templates_dir).get_template(template_name).render(**context)


# ---------------------------------------------------------------------------
# Workstream 3: replace delete_pcluster.yml's Ansible tasks with direct
# boto3/os calls. This round covers only the plan's "trivial" first slice --
# the timer helper and the four credential-destroying tasks -- not yet wired
# into kill_pcluster.py/core_delete_cluster, since the gate these four steps
# share (a positively-confirmed CloudFormation delete) depends on the
# pcluster.lib delete+wait+classify logic that is a separate, larger piece of
# this same workstream, not yet built.
#
# pcluster.lib's exception shape (NotFoundException, .content['message'],
# .code) that a later round of this workstream depends on was live-verified
# against a real AWS account before any of this landed, per the plan's own
# gate: pc.describe_cluster/pc.delete_cluster against a genuinely nonexistent
# cluster both raised NotFoundException (code 404), exactly as the plan's
# source-reading-only research predicted.
#
# Each step function below never raises -- it catches everything and returns
# a TeardownStepResult, mirroring delete_pcluster.yml's own
# ignore_errors: true + register: pattern: one AWS failure must not abandon
# the rest of teardown, and the caller decides what to do with a failure
# (report it as an orphan) rather than the step deciding to abort.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TeardownStepResult:
    name: str
    succeeded: bool
    detail: str = ""


def teardown_timestamp():
    """Match delete_pcluster.yml's `date +%Y-%m-%d\\ \\@\\ %H:%M:%S` output
    exactly (verified against a real `date` invocation, not assumed from the
    strftime directives alone: `%e`-style space-padding pitfalls elsewhere in
    this codebase are exactly why bare strftime reasoning isn't trusted on
    its own -- see CLAUDE.local.md's Deployed_On bullet)."""
    return DateTime.now().strftime("%Y-%m-%d @ %H:%M:%S")


def _delete_ec2_keypair_step(ec2, ec2_keypair):
    """boto3 twin of "Delete the EC2 keypair associated with this cluster"
    (amazon.aws.ec2_key, state: absent). EC2's DeleteKeyPair is itself
    idempotent -- AWS does not error when the keypair is already gone -- so
    this only ever reports failure for a genuine AWS-side problem (e.g.
    denied access), matching the Ansible module's own idempotent shape."""
    try:
        ec2.delete_key_pair(KeyName=ec2_keypair)
    except Exception as e:
        return TeardownStepResult(
            "Delete the EC2 keypair associated with this cluster", False, str(e)
        )
    return TeardownStepResult("Delete the EC2 keypair associated with this cluster", True)


def _delete_local_ssh_key_step(ssh_keypair):
    """os twin of "Delete the SSH private key associated with this cluster"
    (file, state: absent) -- purely local, so unlike the other three steps a
    failure here can never leave an AWS-side orphan; kept as its own step
    anyway to match delete_pcluster.yml's own task-per-target shape, and its
    "ignore_errors but no register" choice there (nothing for an orphan
    report to name)."""
    try:
        os.remove(ssh_keypair)
    except FileNotFoundError:
        pass
    except Exception as e:
        return TeardownStepResult(
            "Delete the SSH private key associated with this cluster", False, str(e)
        )
    return TeardownStepResult("Delete the SSH private key associated with this cluster", True)


def _delete_secrets_manager_secret_step(secretsmanager, ssh_secret_name):
    """boto3 twin of "Delete SSH private key from Secrets Manager" (today a
    raw `aws secretsmanager delete-secret` CLI subprocess call).
    ForceDeleteWithoutRecovery=True matches --force-delete-without-recovery
    exactly -- immediate deletion, no 7-30 day recovery window, consistent
    with this being a secret scoped to one cluster's own lifetime."""
    try:
        secretsmanager.delete_secret(
            SecretId=ssh_secret_name, ForceDeleteWithoutRecovery=True
        )
    except Exception as e:
        return TeardownStepResult(
            "Delete SSH private key from Secrets Manager", False, str(e)
        )
    return TeardownStepResult("Delete SSH private key from Secrets Manager", True)


def _delete_cluster_data_dir_step(cluster_data_dir):
    """os twin of "Delete the cluster data directory" (file, state: absent)
    -- holds the .pem, retrieve_ssh_key.<cluster>.sh, and <cluster>.serial,
    the only record of the serial number a retry needs."""
    try:
        shutil.rmtree(cluster_data_dir)
    except FileNotFoundError:
        pass
    except Exception as e:
        return TeardownStepResult("Delete the cluster data directory", False, str(e))
    return TeardownStepResult("Delete the cluster data directory", True)


def run_credential_teardown_steps(
    *, cf_delete_confirmed, ec2, secretsmanager, ec2_keypair, ssh_keypair,
    ssh_secret_name, cluster_data_dir,
):
    """The four credential-destroying steps, gated together exactly as
    delete_pcluster.yml gates them: only when the CloudFormation stack is
    positively confirmed gone (_cf_delete_confirmed), never on a blocklist of
    "not failed" -- a wait timeout is neither confirmed nor DELETE_FAILED,
    and treating it as "safe to delete credentials" would destroy the only
    way back into a head node that might still be running and billing (the
    exact bug CLAUDE.md documents this gate exists to prevent). Returns all
    four results in delete_pcluster.yml's own task order whether or not the
    gate fired -- an unfired gate reports success with an explanatory
    detail, never silently omits the step from the result list."""
    if not cf_delete_confirmed:
        detail = "skipped: cluster deletion not confirmed"
        return [
            TeardownStepResult(
                "Delete the EC2 keypair associated with this cluster", True, detail
            ),
            TeardownStepResult(
                "Delete the SSH private key associated with this cluster", True, detail
            ),
            TeardownStepResult(
                "Delete SSH private key from Secrets Manager", True, detail
            ),
            TeardownStepResult("Delete the cluster data directory", True, detail),
        ]
    return [
        _delete_ec2_keypair_step(ec2, ec2_keypair),
        _delete_local_ssh_key_step(ssh_keypair),
        _delete_secrets_manager_secret_step(secretsmanager, ssh_secret_name),
        _delete_cluster_data_dir_step(cluster_data_dir),
    ]


# ---------------------------------------------------------------------------
# Workstream 3, second increment: the rest of delete_pcluster.yml's cleanup
# steps -- the S3 bucket, the FSx hydration inline policy, the Grafana SSM
# parameter, the managed IAM policies (base four plus monitoring), the IAM
# role/instance profile, and the external NFS security group. None of these
# are gated on _cf_delete_confirmed in the playbook -- only the four
# credential-destroying steps above are, since only those grant a way back
# into a (possibly still-running) head node. These are gated on their own
# creation flags instead, or run unconditionally where the playbook's own
# task carries no `when:` at all (the managed policies and the IAM role).
# ---------------------------------------------------------------------------

_MANAGED_POLICY_SUFFIXES = ["-HeadNode-Compute", "-HeadNode-Storage", "-HeadNode-IAM", "-ComputeNode-Base"]


def _delete_s3_bucket_step(s3, s3_bucketname):
    """boto3 twin of "Delete the S3 bucket associated with this cluster"
    (amazon.aws.s3_bucket, state: absent, force: true) -- force means empty
    the bucket first, since S3 refuses to delete a non-empty one.
    list_object_versions (rather than list_objects_v2) is used so this
    empties a bucket correctly whether or not versioning was ever enabled:
    an unversioned object still has a VersionId of "null" and deletes the
    same way."""
    step_name = "Delete the S3 bucket associated with this cluster"
    try:
        paginator = s3.get_paginator("list_object_versions")
        for page in paginator.paginate(Bucket=s3_bucketname):
            objects = [
                {"Key": v["Key"], "VersionId": v["VersionId"]}
                for v in page.get("Versions", []) + page.get("DeleteMarkers", [])
            ]
            if objects:
                s3.delete_objects(Bucket=s3_bucketname, Delete={"Objects": objects})
        s3.delete_bucket(Bucket=s3_bucketname)
    except _ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchBucket":
            return TeardownStepResult(step_name, False, str(e))
    except Exception as e:
        return TeardownStepResult(step_name, False, str(e))
    return TeardownStepResult(step_name, True)


def _detach_fsx_hydration_policy_step(iam, ec2_iam_role, fsx_hydration_iam_policy):
    """boto3 twin of "Detach the FSx hydration IAM policy from the role"
    (amazon.aws.iam_policy, iam_type: role, state: absent) -- this is an
    inline role policy created by _setup_fsx_hydration_iam's
    put_role_policy, not a managed policy, so its twin is delete_role_policy,
    not detach_role_policy/delete_policy."""
    step_name = "Detach the FSx hydration IAM policy from the role"
    try:
        iam.delete_role_policy(RoleName=ec2_iam_role, PolicyName=fsx_hydration_iam_policy)
    except _ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            return TeardownStepResult(step_name, False, str(e))
    except Exception as e:
        return TeardownStepResult(step_name, False, str(e))
    return TeardownStepResult(step_name, True)


def _delete_grafana_ssm_param_step(ssm, cluster_name):
    """boto3 twin of "Delete Grafana SSM password parameter"
    (community.aws.ssm_parameter, state: absent)."""
    step_name = "Delete Grafana SSM password parameter"
    try:
        ssm.delete_parameter(Name=f"/parallelcluster/{cluster_name}/grafana/admin-password")
    except _ClientError as e:
        if e.response["Error"]["Code"] != "ParameterNotFound":
            return TeardownStepResult(step_name, False, str(e))
    except Exception as e:
        return TeardownStepResult(step_name, False, str(e))
    return TeardownStepResult(step_name, True)


def _delete_managed_iam_policies_step(iam, ec2_iam_role, ec2_iam_policy, aws_account_id):
    """boto3 twin of "Detach and delete managed IAM policies associated
    with the cluster stack" -- one combined result for all four policies,
    matching the playbook's single with_items task and its single register
    (whose top-level .failed is True if any item failed, per
    CLAUDE.local.md's orphan-collection bullet). Deliberately not routed
    through _delete_managed_policies, which suppresses every error for the
    create-side rollback path (_cleanup_iam_on_failure) -- a teardown
    failure here has to reach the orphan list, not be swallowed."""
    step_name = "Detach and delete managed IAM policies associated with the cluster stack"
    errors = []
    for sfx in _MANAGED_POLICY_SUFFIXES:
        name = ec2_iam_policy + sfx
        arn = f"arn:aws:iam::{aws_account_id}:policy/{name}"
        try:
            iam.detach_role_policy(RoleName=ec2_iam_role, PolicyArn=arn)
        except _ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchEntity":
                errors.append(f"{name} (detach): {e}")
                continue
        except Exception as e:
            errors.append(f"{name} (detach): {e}")
            continue
        try:
            iam.delete_policy(PolicyArn=arn)
        except _ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchEntity":
                errors.append(f"{name} (delete): {e}")
        except Exception as e:
            errors.append(f"{name} (delete): {e}")
    if errors:
        return TeardownStepResult(step_name, False, "; ".join(errors))
    return TeardownStepResult(step_name, True)


def _delete_monitoring_iam_policy_step(iam, ec2_iam_role, ec2_iam_policy, aws_account_id):
    """boto3 twin of "Detach and delete monitoring IAM policy"."""
    step_name = "Detach and delete monitoring IAM policy"
    name = ec2_iam_policy + "-HeadNode-Monitoring"
    arn = f"arn:aws:iam::{aws_account_id}:policy/{name}"
    try:
        iam.detach_role_policy(RoleName=ec2_iam_role, PolicyArn=arn)
    except _ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            return TeardownStepResult(step_name, False, str(e))
    except Exception as e:
        return TeardownStepResult(step_name, False, str(e))
    try:
        iam.delete_policy(PolicyArn=arn)
    except _ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            return TeardownStepResult(step_name, False, str(e))
    except Exception as e:
        return TeardownStepResult(step_name, False, str(e))
    return TeardownStepResult(step_name, True)


def _delete_iam_role_step(iam, ec2_iam_role):
    """boto3 twin of "Delete the IAM roles associated with the cluster
    stack" (amazon.aws.iam_role, state: absent, delete_instance_profile:
    true) -- that module detaches every attached managed policy, deletes
    every inline policy, removes the role from its instance profile, and
    deletes the instance profile before deleting the role; AWS refuses
    DeleteRole otherwise. The four managed policies and the monitoring
    policy are already detached by the steps above, but this enumerates
    and detaches whatever is actually attached rather than assuming that's
    the only path here -- a stale rebuild artifact or a manual attach must
    not block role deletion."""
    step_name = "Delete the IAM roles associated with the cluster stack"
    try:
        for p in iam.list_attached_role_policies(RoleName=ec2_iam_role)["AttachedPolicies"]:
            iam.detach_role_policy(RoleName=ec2_iam_role, PolicyArn=p["PolicyArn"])
        for pname in iam.list_role_policies(RoleName=ec2_iam_role)["PolicyNames"]:
            iam.delete_role_policy(RoleName=ec2_iam_role, PolicyName=pname)
        for prof in iam.list_instance_profiles_for_role(RoleName=ec2_iam_role)["InstanceProfiles"]:
            iam.remove_role_from_instance_profile(
                InstanceProfileName=prof["InstanceProfileName"], RoleName=ec2_iam_role
            )
            iam.delete_instance_profile(InstanceProfileName=prof["InstanceProfileName"])
        iam.delete_role(RoleName=ec2_iam_role)
    except _ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            return TeardownStepResult(step_name, False, str(e))
    except Exception as e:
        return TeardownStepResult(step_name, False, str(e))
    return TeardownStepResult(step_name, True)


def _delete_external_nfs_sg_step(ec2, cluster_name):
    """boto3 twin of "Delete the external NFS security group associated
    with this cluster" (amazon.aws.ec2_security_group, state: absent).
    describe_security_groups on a name that doesn't exist returns an empty
    list rather than raising, so this is idempotent with no special-case
    exception handling needed, matching the Ansible module's own shape."""
    step_name = "Delete the external NFS security group associated with this cluster"
    group_name = f"pcluster-{cluster_name}-externalNfs"
    try:
        resp = ec2.describe_security_groups(
            Filters=[{"Name": "group-name", "Values": [group_name]}]
        )
        for sg in resp["SecurityGroups"]:
            ec2.delete_security_group(GroupId=sg["GroupId"])
    except Exception as e:
        return TeardownStepResult(step_name, False, str(e))
    return TeardownStepResult(step_name, True)


def run_resource_teardown_steps(
    *, s3, iam, ssm, ec2, cluster_name, ec2_iam_role, ec2_iam_policy,
    aws_account_id, s3_bucketname, delete_s3_bucketname, enable_fsx_hydration,
    fsx_hydration_iam_policy, enable_monitoring, enable_external_nfs,
):
    """The rest of delete_pcluster.yml's cleanup steps, in the playbook's own
    task order. Unlike run_credential_teardown_steps, none of these wait on
    cf_delete_confirmed -- each is gated on its own creation flag (or runs
    unconditionally, for the managed policies and the IAM role, matching
    the playbook tasks that carry no `when:` at all)."""
    results = []

    if delete_s3_bucketname:
        results.append(_delete_s3_bucket_step(s3, s3_bucketname))

    if enable_fsx_hydration:
        results.append(
            _detach_fsx_hydration_policy_step(iam, ec2_iam_role, fsx_hydration_iam_policy)
        )

    if enable_monitoring:
        results.append(_delete_grafana_ssm_param_step(ssm, cluster_name))

    results.append(
        _delete_managed_iam_policies_step(iam, ec2_iam_role, ec2_iam_policy, aws_account_id)
    )

    if enable_monitoring:
        results.append(
            _delete_monitoring_iam_policy_step(iam, ec2_iam_role, ec2_iam_policy, aws_account_id)
        )

    results.append(_delete_iam_role_step(iam, ec2_iam_role))

    if enable_external_nfs:
        results.append(_delete_external_nfs_sg_step(ec2, cluster_name))

    return results


# ---------------------------------------------------------------------------
# Workstream 3, third increment: the delete+wait+classify logic that produces
# _cf_delete_confirmed / _cf_delete_failed / _delete_headline -- the gate
# run_credential_teardown_steps and run_resource_teardown_steps are already
# built to receive but that nothing in Python has produced until now.
#
# pcluster.lib's return shape was read from the installed package source
# rather than assumed: pc.describe_cluster/pc.delete_cluster dispatch through
# pcluster.cli.model.call, which json-round-trips the controller's response
# the same way the CLI itself does before printing it -- so the dict this
# code reads (clusterStatus, etc.) is exactly the same shape the rest of this
# file already parses from `pcluster describe-cluster`'s JSON output via
# subprocess. NotFoundException/BadRequestException propagate through
# unwrapped (dispatch() calls the controller directly, bypassing the
# CLI-only exception-wrapping in _run_operation) -- confirmed live against a
# genuinely nonexistent cluster before this landed (see the comment above
# TeardownStepResult). CloudFormationStackStatus.DELETE_COMPLETE/
# DELETE_FAILED are plain string constants ("DELETE_COMPLETE"/
# "DELETE_FAILED") that cloud_formation_status_to_cluster_status passes
# through unchanged, matching CloudFormation's own stack-status strings
# exactly -- both confirmed by reading pcluster/api/converters.py and
# pcluster/api/models/cloud_formation_stack_status.py directly rather than
# inferred from the CLI's printed output alone.
# ---------------------------------------------------------------------------

_CLUSTER_NOT_FOUND = "CLUSTER_NOT_FOUND"
_DELETE_COMPLETE = "DELETE_COMPLETE"
_DELETE_FAILED = "DELETE_FAILED"
_TIMED_OUT = "TIMED_OUT"


@dataclass(frozen=True)
class ClusterDeleteOutcome:
    terminal_state: str
    cf_delete_confirmed: bool
    cf_delete_failed: bool
    delete_headline: str


def _initiate_cluster_delete(delete_fn, cluster_name, region):
    """boto3/pcluster.lib twin of "Delete the ParallelCluster v3 stack"
    (pcluster delete-cluster). Tolerates the same conditions the playbook's
    failed_when does -- a cluster that's already gone (NotFoundException) or
    version-mismatched/already mid-delete (BadRequestException) don't abort
    here either; the wait loop is what actually determines the outcome. Any
    other exception propagates -- that's a genuine delete-cluster failure
    the caller must not paper over. Returns True when the cluster is
    already confirmed gone (NotFoundException), letting the caller skip
    the wait loop entirely rather than polling for a state that will never
    change."""
    try:
        delete_fn(cluster_name=cluster_name, region=region)
    except NotFoundException:
        return True
    except BadRequestException:
        return False
    return False


def _wait_for_cluster_delete(
    describe_fn, cluster_name, region, *, retries=80, delay_seconds=30, sleep_fn=None,
    progress_fn=None,
):
    """boto3/pcluster.lib twin of "Wait for the cluster to finish deleting"
    (until:/retries:/delay: 80/30, an 80*30=2400s/40min ceiling matching
    delete_pcluster.yml's pcluster_delete_timeout default). Returns one of
    the four module-level terminal-state constants above. TIMED_OUT (the
    cluster is still in some other state, e.g. DELETE_IN_PROGRESS, after
    every retry) is deliberately not an exception and not a failure by
    itself -- delete_pcluster.yml only WARNs on a wait timeout, it never
    fails the play there, matching CLAUDE.md's own gate: a timeout is
    neither confirmed-gone nor DELETE_FAILED, and the credential-destroying
    steps must preserve access either way. A describe-cluster call that
    fails for some *other* reason on every attempt (throttling, a
    transient network error, BadRequestException) is retried exactly like
    a still-building cluster would be -- Ansible's own until:/retries: loop
    does not distinguish the reason for a non-terminal result mid-retry,
    only on the final one -- but if the very last attempt still raised,
    that exception is re-raised rather than folded into TIMED_OUT: the
    playbook's failed_when aborts the whole play in that case, and nothing
    after it runs, so the caller here must not silently treat an
    describe-cluster call that never resolved to any recognized state as
    safe to proceed past."""
    sleep_fn = sleep_fn or time.sleep
    last_exc = None
    for attempt in range(retries):
        last_exc = None
        try:
            resp = describe_fn(cluster_name=cluster_name, region=region)
        except NotFoundException:
            return _CLUSTER_NOT_FOUND
        except Exception as e:
            last_exc = e
        else:
            status = resp.get("clusterStatus", "")
            if status == _DELETE_COMPLETE:
                return _DELETE_COMPLETE
            if status == _DELETE_FAILED:
                return _DELETE_FAILED
            if progress_fn is not None:
                progress_fn(attempt, status, resp.get("cloudFormationStackStatus", ""))
        if attempt < retries - 1:
            sleep_fn(delay_seconds)
    if last_exc is not None:
        raise last_exc
    return _TIMED_OUT


def _classify_cluster_delete_outcome(terminal_state, cluster_name):
    """boto3 twin of delete_pcluster.yml's three set_fact tasks
    (_cf_delete_confirmed, _cf_delete_failed, _delete_headline), derived
    from one terminal state so no caller can see the three disagree with
    each other -- the same reason the playbook derives _delete_headline
    once rather than letting each reporting surface restate the success
    claim independently. Positive confirmation only: TIMED_OUT is neither
    confirmed nor failed, and must preserve credentials exactly like a
    genuine DELETE_FAILED would (CLAUDE.md's teardown-gate bullet)."""
    cf_delete_confirmed = terminal_state in (_DELETE_COMPLETE, _CLUSTER_NOT_FOUND)
    cf_delete_failed = terminal_state == _DELETE_FAILED
    if cf_delete_confirmed:
        headline = f"Cluster {cluster_name} has been deleted."
    elif cf_delete_failed:
        headline = f"Cluster {cluster_name} reached DELETE_FAILED and was NOT deleted."
    else:
        headline = f"Deletion of cluster {cluster_name} was NOT confirmed."
    return cf_delete_confirmed, cf_delete_failed, headline


def run_cluster_delete_and_classify(
    delete_fn, describe_fn, cluster_name, region, *, retries=80, delay_seconds=30, sleep_fn=None,
    wait=True, progress_fn=None,
):
    """The single entry point a future core_delete_cluster rewrite calls:
    initiate the delete, wait for a terminal state, and classify it into
    the three facts run_credential_teardown_steps/run_resource_teardown_
    steps and the eventual destruction-summary report all need. Mirrors
    delete_pcluster.yml's "Delete the ParallelCluster v3 stack" + "Wait for
    the cluster to finish deleting" + its three set_fact tasks as one
    coherent unit, the same grouping a caller actually needs them in."""
    already_gone = _initiate_cluster_delete(delete_fn, cluster_name, region)
    if already_gone:
        terminal_state = _CLUSTER_NOT_FOUND
    elif not wait:
        # Workstream 4: kicked off, deliberately not waited on. Classified
        # as NOT confirmed, which is correct and load-bearing -- every
        # credential-destroying teardown step is gated on positive
        # confirmation the stack is gone (CLAUDE.md's teardown-gate
        # bullet), and "we did not look" is not confirmation. An MCP
        # caller polls describe_cluster itself and re-runs the teardown
        # once the stack is genuinely gone.
        terminal_state = _KICKED_OFF
    else:
        terminal_state = _wait_for_cluster_delete(
            describe_fn, cluster_name, region,
            retries=retries, delay_seconds=delay_seconds, sleep_fn=sleep_fn,
            progress_fn=progress_fn,
        )
    cf_delete_confirmed, cf_delete_failed, headline = _classify_cluster_delete_outcome(
        terminal_state, cluster_name
    )
    return ClusterDeleteOutcome(terminal_state, cf_delete_confirmed, cf_delete_failed, headline)
