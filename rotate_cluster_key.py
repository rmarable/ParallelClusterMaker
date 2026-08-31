#!/usr/bin/env python
################################################################################
# Name:         rotate_cluster_key.py
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Purpose:      Rotate the SSH keypair for a running ParallelCluster without
#               rebuilding the cluster.
#
# What it does:
#   1. Generates a new ED25519 keypair locally.
#   2. Adds the new public key to ~/.ssh/authorized_keys on the head node,
#      verifies the new key authenticates, then removes the old public key
#      from authorized_keys so it can no longer log in.
#   3. Imports the new public key as a new EC2 keypair (same name + "-rotated").
#   4. Updates the Secrets Manager secret with the new private key.
#   5. Overwrites the local .pem file with the new private key.
#   6. Removes the old EC2 keypair from AWS.
#
# Prerequisites:
#   - Active .venv (source .venv/bin/activate)
#   - AWS credentials with secretsmanager:GetSecretValue, PutSecretValue,
#     ec2:ImportKeyPair, ec2:DeleteKeyPair, and ec2:DescribeInstances.
#     These are OPERATOR permissions — not granted by the cluster head node
#     managed policies.
#   - The cluster must be in CREATE_COMPLETE state (head node reachable).
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

import argparse
import boto3

sys.path.insert(0, _src_dir)
from pcluster_core import (
    ClusterRecord,
    PClusterMakerError,
    _validate_cluster_name,
    _validate_az_input,
    _read_cluster_record,
    _read_turbot_from_vars_file,
    core_rotate_cluster_key,
)


def main():
    parser = argparse.ArgumentParser(
        description="Rotate the SSH keypair for a running ParallelCluster."
    )
    parser.add_argument("--cluster_name", "-N", required=True, help="cluster name")
    parser.add_argument("--az", "-A", required=True, help="availability zone (e.g. us-east-1a)")
    parser.add_argument(
        "--turbot_account",
        "-T",
        default=None,
        help="Turbot account ID (default: auto-detect from vars file)",
    )
    parser.add_argument(
        "--dry_run",
        action="store_true",
        help="Print what would be done without making any changes.",
    )
    args = parser.parse_args()

    _validate_cluster_name(args.cluster_name)
    _validate_az_input(args.az)
    cluster_name = args.cluster_name
    region = args.az[:-1]

    vars_file_path = os.path.join(_src_dir, "vars_files", cluster_name + ".yml")
    rec = _read_cluster_record(cluster_name, _repo_root)
    if rec is None:
        sys.exit(
            f"ERROR: vars file not found: {vars_file_path}\n"
            f"  Has this cluster been created with make_pcluster.py?"
        )
    cluster_record = ClusterRecord.from_dict(rec)

    if not cluster_record.serial or not cluster_record.ec2_keypair:
        sys.exit("ERROR: vars file is missing cluster_serial_number or ec2_keypair.")

    # Turbot profile — CLI arg wins, then vars file auto-detect. This mutates
    # process-global state (AWS_PROFILE, boto3's default session), which is
    # exactly why it stays in the CLI shim rather than core_rotate_cluster_key:
    # a long-lived MCP server calling that function repeatedly for different
    # clusters must never have one call's profile choice stick for the next
    # one. The MCP tool has no turbot_account parameter at all for this reason.
    turbot_account = args.turbot_account
    if not turbot_account:
        turbot_account = _read_turbot_from_vars_file(vars_file_path)
    if turbot_account and turbot_account != "disabled":
        turbot_profile = f"turbot__{turbot_account}__{cluster_record.cluster_owner}"
        os.environ["AWS_PROFILE"] = turbot_profile
        os.environ["AWS_DEFAULT_REGION"] = region
        boto3.setup_default_session(profile_name=turbot_profile)
        print(f"  Using Turbot profile: {turbot_profile}")

    try:
        core_rotate_cluster_key(
            cluster_record=cluster_record,
            region=region,
            dry_run=args.dry_run,
        )
    except PClusterMakerError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
