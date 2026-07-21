"""
Pure-Python utility functions extracted from make_pcluster.py, kill_pcluster.py,
and access_cluster.py.

All functions are importable without AWS credentials and without the
venv guard that the main scripts enforce at import time.
"""

import os
import re
import sys
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
    if not az or not re.match(r"^[a-z]{2}-[a-z]+-\d+[a-z]$", az):
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


def _resolve_ec2_user(base_os):
    """Return (ec2_user, ec2_user_home) for the given base_os string."""
    if "ubuntu" in base_os:
        ec2_user = "ubuntu"
    elif "rhel" in base_os:
        ec2_user = "ec2-user"
    else:
        sys.exit(
            f"ERROR: '{base_os}' is not a supported base OS. "
            f"Choose from: ubuntu2204, ubuntu2404, rhel8, rhel9"
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
    """Normalise FSx S3 bucket/path when export is undefined or mirrors import.

    Returns (export_bucket, export_path) after applying defaults and warnings.
    No AWS calls — pure variable logic.
    """
    if import_bucket != "UNDEFINED" and export_bucket == "UNDEFINED":
        print("*** WARNING ***")
        print(
            "fsx_s3_import bucket is defined but fsx_s3_export_bucket is unspecified!"
        )
        print("Lustre will hydrate *and* dehydrate from the S3 import bucket path.")
        print("")
        export_bucket = import_bucket
        export_path = import_path
    elif import_bucket != "UNDEFINED" and import_bucket == export_bucket:
        print("*** WARNING ***")
        print(
            "fsx_s3_import bucket and fsx_s3_export_bucket are set to the same value!"
        )
        print("Lustre will hydrate *and* dehydrate from the S3 import bucket.")
        print("")
        if import_path == export_path:
            print("*** WARNING ***")
            print(
                "fsx_s3_import path and fsx_s3_export_path are set to the same value!"
            )
            print("Lustre will hydrate *and* dehydrate from the S3 import path.")
            print("")
    return export_bucket, export_path


def _check_fsx_s3(s3_client, bucket, path, label):
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
    try:
        result = s3_client.list_objects_v2(Bucket=bucket, Prefix=path)
    except _ClientError as _e:
        sys.exit(f"ERROR: Lustre hydration: cannot list s3://{bucket}/{path}: {_e}")
    if result.get("KeyCount", 0) == 0:
        sys.exit(f"ERROR: Please ensure s3://{bucket}/{path} exists!")


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
