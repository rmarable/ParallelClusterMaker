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
            f"Choose from: ubuntu2204, ubuntu2404, ubuntu2204arm, ubuntu2404arm, rhel8, rhel8arm, rhel9, rhel9arm"
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


# ---------------------------------------------------------------------------
# Validation guards (extracted from make_pcluster.py main())
# ---------------------------------------------------------------------------


def _validate_cluster_lifetime(lifetime):
    """Raise SystemExit if lifetime is not D:HH:MM format."""
    if not re.fullmatch(r"^\d+:\d+:\d+$", lifetime):
        sys.exit(
            f"ERROR: cluster_lifetime must be in D:HH:MM format (e.g. 7:0:0). Got: {lifetime!r}"
        )


def _validate_fsx_size(fsx_size, enable_fsx):
    """Raise SystemExit if fsx_size is invalid when FSx is enabled."""
    if not enable_fsx:
        return
    if fsx_size <= 0 or fsx_size % 1200 != 0:
        sys.exit("*** ERROR ***\nfsx_size must be a positive multiple of 1200!")


def _validate_ebs_config(
    headnode_size, compute_size, shared_size, shared_type, shared_iops, shared_throughput
):
    """Raise SystemExit if any EBS volume parameter is out of range."""
    for label, val in (
        ("headnode_root_volume_size", headnode_size),
        ("compute_root_volume_size", compute_size),
        ("ebs_shared_volume_size", shared_size),
    ):
        if int(val) < 1:
            sys.exit(
                f"*** ERROR ***\nEBS volume size must be >= 1 GiB!\n  {label} = {val} GB"
            )
        if int(val) > 16384:
            sys.exit(
                f"*** ERROR ***\nMaximum allowed EBS volume size is 16,384 GiB!\n  {label} = {val} GB"
            )
    if shared_type in ("gp3", "io1", "io2") and int(shared_iops) < 100:
        sys.exit(
            f"*** ERROR ***\nebs_shared_volume_iops must be >= 100 (got {shared_iops})."
        )
    if shared_type == "gp3" and int(shared_throughput) < 125:
        sys.exit(
            f"*** ERROR ***\nebs_shared_volume_throughput must be >= 125 MiB/s for gp3 (got {shared_throughput})."
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
    ec2_json_policy_src,
    region="",
    vpc_id="",
    enable_monitoring=False,
):
    """Create ec2_iam_role and attach managed policies (-A/-B/-C, optionally -M). Idempotent."""
    _role_existed = False
    try:
        iam.get_role(RoleName=ec2_iam_role)
        _role_existed = True
        attached = {
            p["PolicyName"]
            for p in iam.list_attached_role_policies(RoleName=ec2_iam_role)[
                "AttachedPolicies"
            ]
        }
        expected = {ec2_iam_policy + s for s in ["-A", "-B", "-C"]}
        if enable_monitoring:
            expected.add(ec2_iam_policy + "-M")
        if expected.issubset(attached):
            print(f"  Found ec2_iam_role with all policies attached: {ec2_iam_role}")
            return
        print(
            f"  Found ec2_iam_role {ec2_iam_role} but missing policies "
            f"{expected - attached} — cleaning up and recreating policies."
        )
        _delete_managed_policies(
            iam, ec2_iam_role, ec2_iam_policy, aws_account_id,
            suppress=True, enable_monitoring=enable_monitoring,
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

    src_a = ec2_json_policy_src.replace(".json_src", "-A.json_src")
    src_b = ec2_json_policy_src.replace(".json_src", "-B.json_src")
    src_c = ec2_json_policy_src.replace(".json_src", "-C.json_src")
    policy_a = _render_policy(src_a, *render_args)
    policy_b = _render_policy(src_b, *render_args)
    policy_c = _render_policy(src_c, *render_args)

    with open(
        os.open(ec2_json_policy_template, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600),
        "w",
    ) as fh:
        fh.write(policy_a)

    if not _role_existed:
        iam.create_role(
            RoleName=ec2_iam_role,
            AssumeRolePolicyDocument='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":["ec2.amazonaws.com"]},"Action":"sts:AssumeRole"}]}',
            Description="ParallelClusterMaker EC2 IAM instance role",
        )
    resp_a = iam.create_policy(PolicyName=ec2_iam_policy + "-A", PolicyDocument=policy_a)
    resp_b = iam.create_policy(PolicyName=ec2_iam_policy + "-B", PolicyDocument=policy_b)
    resp_c = iam.create_policy(PolicyName=ec2_iam_policy + "-C", PolicyDocument=policy_c)
    iam.attach_role_policy(RoleName=ec2_iam_role, PolicyArn=resp_a["Policy"]["Arn"])
    iam.attach_role_policy(RoleName=ec2_iam_role, PolicyArn=resp_b["Policy"]["Arn"])
    iam.attach_role_policy(RoleName=ec2_iam_role, PolicyArn=resp_c["Policy"]["Arn"])
    print(f"  Created ec2_iam_role:     {ec2_iam_role}")
    print(f"  Created ec2_iam_policy-A: {ec2_iam_policy}-A")
    print(f"  Created ec2_iam_policy-B: {ec2_iam_policy}-B")
    print(f"  Created ec2_iam_policy-C: {ec2_iam_policy}-C")

    if enable_monitoring:
        src_m = ec2_json_policy_src.replace(".json_src", "-M.json_src")
        policy_m = _render_policy(src_m, *render_args)
        resp_m = iam.create_policy(PolicyName=ec2_iam_policy + "-M", PolicyDocument=policy_m)
        iam.attach_role_policy(RoleName=ec2_iam_role, PolicyArn=resp_m["Policy"]["Arn"])
        print(f"  Created ec2_iam_policy-M: {ec2_iam_policy}-M")


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
    suffixes = ["-A", "-B", "-C"]
    if enable_monitoring:
        suffixes.append("-M")
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
):
    """Return (vpc_id, headnode_subnet_id, compute_subnet_ids, vpc_cidr).

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

    return vpc_id, headnode_subnet_id, compute_subnet_ids, vpc_cidr
