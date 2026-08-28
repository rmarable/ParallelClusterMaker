#!/usr/bin/env python
#
################################################################################
# Name:		kill_pcluster.py
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:   April 20, 2019
# Purpose:	Python3 wrapper for deleting custom pcluster stacks
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

# Load some required Python libraries.

import argparse
import boto3
import subprocess  # noqa: F401 -- unused directly; core_delete_cluster's
# results-sync step does the real subprocess.run call (an ssh invocation),
# but `import subprocess` binds this module's name to the same process-wide
# module object pcluster_core.py's own `subprocess` name binds to, so tests
# that patch kill_pcluster.subprocess.run still intercept it there too.
# Kept purely so that monkeypatch target exists.
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    EndpointConnectionError,
    NoCredentialsError,
)

sys.path.insert(0, _src_dir)
from pcluster_core import (
    _validate_az_input,
    _validate_cluster_name,
    _validate_cluster_owner,
    _load_defaults_file,
    _read_turbot_from_vars_file,
    _resolve as _pcore_resolve,
    _resolve_bool as _pcore_resolve_bool,
    core_delete_cluster,
)
from pcluster_aux_data import p_val
from pcluster_aux_data import ctrlC_Abort
from pcluster_aux_data import print_TextHeader
from pcluster_aux_data import refer_to_docs_and_quit


def main():
    # Parse input from the command line.

    parser = argparse.ArgumentParser(
        description="kill-cluster.py: Command-line tool to destroy ParallelCluster stacks built in AWS"
    )

    # Configure arguments for the required variables.

    parser.add_argument(
        "--az", "-A", help="AWS Availability Zone (REQUIRED)", required=True
    )
    parser.add_argument(
        "--cluster_name", "-N", help="cluster name (REQUIRED)", required=True
    )
    parser.add_argument(
        "--cluster_owner",
        "-O",
        help="username of the cluster owner (REQUIRED)",
        required=True,
    )

    # Configure arguments for the optional variables.
    # By default, delete any storage associated with the cluster.

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
        "--turbot_account",
        "-T",
        help='Turbot account ID, set to "disabled" if not used (default = disabled)',
        required=False,
        default=None,
    )
    parser.add_argument(
        "--ansible_verbosity",
        "-V",
        choices=["-v", "-vv", "-vvv", "-vvvv", ""],
        help="Ansible verbosity level (default = none)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--delete_s3_bucketname",
        choices=["true", "false"],
        help="delete S3 bucket on cluster teardown (default = true)",
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

    # Parse CLI args, overlay pcluster_defaults.yml if --use_defaults, then apply
    # hardcoded fallbacks. Precedence: CLI arg > pcluster_defaults.yml > hardcoded.

    args = parser.parse_args()

    _HARDCODED_DEFAULTS = {
        "ansible_verbosity": "",
        "debug_mode": "false",
        "delete_s3_bucketname": "true",
        "turbot_account": "disabled",
    }

    _file_defaults = {}
    if args.use_defaults:
        _toolkit_defaults = os.path.join(_repo_root, "pcluster_defaults.yml")
        _file_defaults = _load_defaults_file(
            os.path.abspath(args.use_defaults), _toolkit_defaults, args.cluster_name
        )
        print(f"Defaults: loaded from {args.use_defaults}")

    def _resolve(name):
        return _pcore_resolve(name, args, _file_defaults, _HARDCODED_DEFAULTS)

    def _resolve_bool(name):
        return _pcore_resolve_bool(name, args, _file_defaults, _HARDCODED_DEFAULTS)

    az = args.az
    _validate_az_input(az)
    cluster_name = args.cluster_name
    cluster_owner = args.cluster_owner
    region = az[:-1]  # bootstrap only; overwritten below from API
    ansible_verbosity = _resolve("ansible_verbosity")
    debug_mode = _resolve_bool("debug_mode")
    delete_s3_bucketname = _resolve("delete_s3_bucketname")
    turbot_account = _resolve("turbot_account")

    # Auto-detect turbot_account from the cluster vars file when not supplied on
    # the CLI or in a defaults file.  The vars file is written by make_pcluster.py
    # and records the profile used at creation time, so teardown uses the same one
    # without the operator having to remember it.
    if turbot_account == "disabled":
        _vars_file_probe = os.path.join(_src_dir, "vars_files", cluster_name + ".yml")
        _saved_turbot = _read_turbot_from_vars_file(_vars_file_probe)
        if _saved_turbot != "disabled":
            turbot_account = _saved_turbot
            print(f"  Note: turbot_account auto-detected from vars file: {turbot_account}")

    # Print a header for cluster variable validation.

    if debug_mode:
        print_TextHeader(cluster_name, "Validating cluster parameter values", 80)

    # Verify AZ with operator's base credentials before any profile switch.

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
        refer_to_docs_and_quit(
            f'"{az}" is not a valid Availability Zone in the selected AWS Region.'
        )
    region = _az_info["AvailabilityZones"][0]["RegionName"]
    p_val("region", debug_mode)
    p_val("az", debug_mode)

    # Activate Turbot profile now that region is confirmed from the API.
    if turbot_account != "disabled":
        turbot_profile = "turbot__" + turbot_account + "__" + cluster_owner
        os.environ["AWS_PROFILE"] = turbot_profile
        os.environ["AWS_DEFAULT_REGION"] = region
        boto3.setup_default_session(profile_name=turbot_profile)
        p_val("turbot_account", debug_mode)

    cluster_destroy_command = " ".join(sys.argv)

    if ansible_verbosity:
        print(
            "Note: --ansible_verbosity/-V has no effect -- teardown no longer "
            "shells out to Ansible."
        )

    # Preflight, purely to display what's about to happen before the Ctrl-C
    # abort window opens -- core_delete_cluster independently re-derives and
    # re-runs all of this after the window closes, so a race in the few-
    # second gap is safe either way (same tradeoff already accepted for
    # stop_pcluster.py/start_pcluster.py's preflight checks).

    _validate_cluster_name(cluster_name)
    _validate_cluster_owner(cluster_owner)

    # The serial file and the vars file are deliberately *not* checked here.
    # Both exist only on the machine that built the cluster, and
    # core_delete_cluster already falls back to the shared record store when
    # either is absent -- that fallback was written for exactly this case and
    # carries a comment saying so. Checking them here made it unreachable
    # from the CLI: a cluster built through the MCP server could not be torn
    # down from any other machine, on values the published record has
    # carried all along. Two statements of one rule, and this was the copy
    # that was wrong.

    if debug_mode:
        print("debug_mode = enabled")
        print("")
    print("")
    print("Ready to execute:")
    print(cluster_destroy_command)
    print("")
    print(
        f'Preparing to delete cluster "{cluster_name}" in {region} '
        f"(delete_s3_bucketname={delete_s3_bucketname})."
    )

    # Exit the script if the operator types 'CTRL-C' within 5 seconds after the
    # abort header is displayed.
    # If debug_mode is enabled, set the timer to 15 seconds.

    # Pass None for all cleanup params: Ctrl-C during the abort window should
    # just cancel the deletion, not destroy IAM resources or serial files
    # before core_delete_cluster has run. Actual cleanup happens after it
    # succeeds below.
    if debug_mode:
        ctrlC_Abort(15, 80, None, None, None, "false")
    else:
        ctrlC_Abort(5, 80, None, None, None, "false")

    # Delete the cluster stack via boto3/pcluster.lib.

    result = core_delete_cluster(
        cluster_name=cluster_name,
        cluster_owner=cluster_owner,
        region=region,
        repo_root=_repo_root,
        delete_s3_bucketname=delete_s3_bucketname,
        debug_mode=debug_mode,
    )
    sys.exit(result.exit_code)


if __name__ == "__main__":
    main()
