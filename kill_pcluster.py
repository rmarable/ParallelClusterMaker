#!/usr/bin/env python
#
################################################################################
# Name:		kill_pcluster.py
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:   April 20, 2019
# Last Changed: May 26, 2019
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
import contextlib
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    EndpointConnectionError,
    NoCredentialsError,
)
import json
import subprocess

sys.path.insert(0, _src_dir)
from pcluster_core import (
    _read_serial_first_line,
    _extract_rebuild_command,
    _validate_az_input,
    _validate_cluster_name,
    _validate_cluster_owner,
    _load_defaults_file,
    _resolve as _pcore_resolve,
    _resolve_bool as _pcore_resolve_bool,
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
        help="Ansible verbosity level (default = none)",
        required=False,
        default=None,
    )
    parser.add_argument(
        "--delete_fsx",
        choices=["true", "false"],
        help="delete FSx on cluster teardown (default = true)",
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
        "delete_fsx": "true",
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
    delete_fsx = _resolve("delete_fsx")
    delete_s3_bucketname = _resolve("delete_s3_bucketname")
    turbot_account = _resolve("turbot_account")

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

    _describe = subprocess.run(
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
    if _describe.returncode != 0:
        print("")
        print("*** WARNING ***")
        print('Cluster stack "' + cluster_name + '" was not found in ' + region + "!")
        print("")
        print("Continuing with stack artifact destruction...")
    else:
        p_val("cluster_name", debug_mode)

    # Validate cluster_name and cluster_owner before touching any state files.
    _validate_cluster_name(cluster_name)
    _validate_cluster_owner(cluster_owner)

    _active_clusters_root = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "active_clusters"
    )
    cluster_data_dir = os.path.join(_active_clusters_root, cluster_name)
    cluster_serial_number_file = os.path.join(
        cluster_data_dir, cluster_name + ".serial"
    )
    vars_file_path = os.path.join(_src_dir, "vars_files", cluster_name + ".yml")

    if os.path.isfile(cluster_serial_number_file):
        p_val("cluster_serial_number_file", debug_mode)
    else:
        print("")
        print("*** ERROR ***")
        print("Missing cluster_serial_number_file: " + cluster_serial_number_file)
        print("Aborting...")
        sys.exit(1)

    if os.path.isfile(vars_file_path):
        p_val("vars_file_path", debug_mode)
    else:
        print("")
        print("*** ERROR ***")
        print("Missing vars_file_path: " + vars_file_path)
        print("Aborting...")
        sys.exit(1)

    # Parse cluster_serial_number from cluser_serial_number_file.
    # Strip any trailing newlines that would otherwise break the Ansible destroy
    # command string.

    cluster_serial_number = _read_serial_first_line(cluster_serial_number_file)

    # Parse the Python3 interpreter path to ensure ParallelCluster stacks can be
    # created from either OSX or an EC2 jumphost.

    python3_path = sys.executable

    # Increase Ansible verbosity when debug_mode is enabled.

    if debug_mode:
        ansible_verbosity = "-vvv"

    # Generate the command string that will delete the cluster stack.

    _destroy_extra_vars_str = json.dumps(
        {
            "cluster_name": cluster_name,
            "cluster_serial_number": cluster_serial_number,
            "delete_s3_bucketname": delete_s3_bucketname,
            "delete_fsx": delete_fsx,
            "debug_mode": "true" if debug_mode else "false",
            "ansible_python_interpreter": python3_path,
        }
    )
    _delete_playbook = os.path.join(_src_dir, "delete_pcluster.yml")
    _ansible_cmd = [
        "ansible-playbook",
        "--extra-vars",
        _destroy_extra_vars_str,
        _delete_playbook,
    ]
    if ansible_verbosity:
        _ansible_cmd.append(ansible_verbosity)

    ansible_destroy_cmd_string = " ".join(_ansible_cmd)

    # Print the cluster destroy commands to the console.

    if ansible_verbosity:
        if debug_mode:
            print("debug_mode = enabled")
            print("")
        print('Setting Ansible verbosity to "' + ansible_verbosity + '"')
    print("")
    print("Ready to execute:")
    print("$ " + cluster_destroy_command)
    print("")
    print('Preparing to delete cluster "' + cluster_name + '" using this command:')
    print("$ " + ansible_destroy_cmd_string)

    # Exit the script if the operator types 'CTRL-C' within 5 seconds after the
    # abort header is displayed.
    # If debug_mode is enabled, set the timer to 15 seconds.

    line_length = 80
    # Pass None for all cleanup params: Ctrl-C during the abort window should
    # just cancel the deletion, not destroy IAM resources or serial files before
    # Ansible has run. Actual cleanup happens after Ansible succeeds below.
    if debug_mode:
        ctrlC_Abort(15, 80, None, None, None, "false")
    else:
        ctrlC_Abort(5, 80, None, None, None, "false")

    # Delete the cluster stack using the delete_pcluster Ansible playbook.

    try:
        subprocess.run(_ansible_cmd, cwd=_src_dir, check=True, shell=False)
    except subprocess.CalledProcessError as e:
        print(
            f"\nERROR: Ansible teardown failed (exit {e.returncode}). AWS resources may not have been deleted."
        )
        print(
            "Check the output above and the CloudFormation console before assuming the cluster is gone."
        )
        sys.exit(e.returncode)

    # Print a friendly banner to the console and include the command used to
    # spawn the cluster stack.

    line_length = 80
    print("".center(line_length, "="))
    _rebuild_cmd = _extract_rebuild_command(cluster_serial_number_file)
    if _rebuild_cmd:
        print("To rebuild the cluster:")
        print("")
        print("$ " + _rebuild_cmd)

    # Delete cluster_serial_number_file and vars_file_path.

    print("".center(line_length, "="))
    print("")
    with contextlib.suppress(FileNotFoundError):
        os.remove(cluster_serial_number_file)
        print("Removed  ===> " + cluster_serial_number_file)
    with contextlib.suppress(FileNotFoundError):
        os.remove(vars_file_path)
        print("Removed  ===> " + vars_file_path)

    # Cleanup and exit.

    print("")
    print("Finished deleting cluster stack " + cluster_name + "!")
    print("Exiting...")
    sys.exit(0)


if __name__ == "__main__":
    main()
