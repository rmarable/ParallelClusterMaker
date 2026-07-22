#!/usr/bin/env python
################################################################################
# Name:         rotate_cluster_key.py
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Purpose:      Rotate the SSH keypair for a running ParallelCluster without
#               rebuilding the cluster.
#
# What it does:
#   1. Generates a new ED25519 keypair locally.
#   2. Adds the new public key to ~/.ssh/authorized_keys on the head node.
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
import subprocess
import tempfile
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

sys.path.insert(0, _src_dir)
from pcluster_core import (
    _validate_cluster_name,
    _validate_az_input,
    _ssh_secret_name,
    _read_turbot_from_vars_file,
)


def _require(cmd):
    """Exit if a CLI tool is not on PATH."""
    if subprocess.run(["which", cmd], capture_output=True).returncode != 0:
        sys.exit(f"ERROR: '{cmd}' not found on PATH.")


def _run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


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
        help='Turbot account ID (default: auto-detect from vars file)',
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

    _require("ssh")
    _require("ssh-keygen")
    _require("aws")

    # Load vars file to get serial, keypair name, ec2_user, and secret name.
    vars_file_path = os.path.join(_src_dir, "vars_files", cluster_name + ".yml")
    if not os.path.isfile(vars_file_path):
        sys.exit(f"ERROR: vars file not found: {vars_file_path}\n"
                 f"  Has this cluster been created with make_pcluster.py?")

    import yaml
    with open(vars_file_path) as _f:
        vf = yaml.safe_load(_f) or {}

    serial = vf.get("cluster_serial_number", "")
    ec2_keypair = vf.get("ec2_keypair", "")
    ec2_user = vf.get("ec2_user", "ubuntu")
    ssh_keypair = vf.get("ssh_keypair", "")
    secret_name = vf.get("ssh_secret_name") or _ssh_secret_name(cluster_name, serial)

    if not serial or not ec2_keypair:
        sys.exit("ERROR: vars file is missing cluster_serial_number or ec2_keypair.")

    # Turbot profile — CLI arg wins, then vars file auto-detect.
    turbot_account = args.turbot_account
    if not turbot_account:
        turbot_account = _read_turbot_from_vars_file(vars_file_path)
    if turbot_account and turbot_account != "disabled":
        cluster_owner = vf.get("cluster_owner", "")
        turbot_profile = f"turbot__{turbot_account}__{cluster_owner}"
        os.environ["AWS_PROFILE"] = turbot_profile
        os.environ["AWS_DEFAULT_REGION"] = region
        boto3.setup_default_session(profile_name=turbot_profile)
        print(f"  Using Turbot profile: {turbot_profile}")

    ec2 = boto3.client("ec2", region_name=region)
    sm = boto3.client("secretsmanager", region_name=region)

    # Resolve live head node IP.
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
    except (BotoCoreError, ClientError, NoCredentialsError) as _e:
        sys.exit(f"ERROR: Could not describe EC2 instances: {_e}")

    if not head_ip:
        sys.exit(f"ERROR: No running head node found for cluster '{cluster_name}'.")

    print(f"  Cluster:   {cluster_name}  ({serial})")
    print(f"  Head node: {head_ip}")
    print(f"  Secret:    {secret_name}")

    if args.dry_run:
        print("\n[dry-run] No changes made.")
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        new_key_path = os.path.join(tmpdir, "id_ed25519_new")
        new_pub_path = new_key_path + ".pub"

        # 1. Generate new ED25519 keypair.
        print("\nGenerating new ED25519 keypair...")
        _run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", new_key_path])
        with open(new_pub_path) as _f:
            new_pub_key = _f.read().strip()
        with open(new_key_path) as _f:
            new_priv_key = _f.read()

        # 2. Add new public key to head node authorized_keys via SSH.
        print("Adding new public key to head node authorized_keys...")
        subprocess.run(
            [
                "ssh",
                "-i", ssh_keypair,
                "-o", "StrictHostKeyChecking=accept-new",
                "-o", "ConnectTimeout=30",
                "-o", "BatchMode=yes",
                f"{ec2_user}@{head_ip}",
                "cat >> ~/.ssh/authorized_keys",
            ],
            input=(new_pub_key + "\n").encode(),
            check=True,
        )

        # 3. Import new public key as EC2 keypair (rotated name).
        new_keypair_name = ec2_keypair + "-rotated"
        print(f"Importing new EC2 keypair: {new_keypair_name}...")
        try:
            ec2.import_key_pair(
                KeyName=new_keypair_name,
                PublicKeyMaterial=new_pub_key.encode(),
            )
        except ClientError as _e:
            if "InvalidKeyPair.Duplicate" in str(_e):
                ec2.delete_key_pair(KeyName=new_keypair_name)
                ec2.import_key_pair(
                    KeyName=new_keypair_name,
                    PublicKeyMaterial=new_pub_key.encode(),
                )
            else:
                raise

        # 4. Update Secrets Manager secret.
        print("Updating Secrets Manager secret...")
        sm.put_secret_value(SecretId=secret_name, SecretString=new_priv_key)

        # 5. Overwrite local .pem file.
        if ssh_keypair:
            print(f"Updating local key file: {ssh_keypair}...")
            try:
                with open(ssh_keypair, "w") as _f:
                    _f.write(new_priv_key)
                os.chmod(ssh_keypair, 0o600)
            except OSError as _e:
                print(f"  Warning: could not write local key file: {_e}")
                print(f"  The new key is safe in Secrets Manager: {secret_name}")
                print(f"  Retrieve it with: active_clusters/{cluster_name}/retrieve_ssh_key.{cluster_name}.sh")

        # 6. Delete old EC2 keypair.
        print(f"Deleting old EC2 keypair: {ec2_keypair}...")
        try:
            ec2.delete_key_pair(KeyName=ec2_keypair)
        except ClientError as _e:
            print(f"  Warning: could not delete old keypair: {_e}")

        # Rename the rotated keypair to the canonical name.
        print(f"Renaming {new_keypair_name} → {ec2_keypair}...")
        try:
            ec2.import_key_pair(
                KeyName=ec2_keypair,
                PublicKeyMaterial=new_pub_key.encode(),
            )
        except ClientError as _e:
            if "InvalidKeyPair.Duplicate" in str(_e):
                ec2.delete_key_pair(KeyName=ec2_keypair)
                ec2.import_key_pair(
                    KeyName=ec2_keypair,
                    PublicKeyMaterial=new_pub_key.encode(),
                )
            else:
                raise
        ec2.delete_key_pair(KeyName=new_keypair_name)

    print("")
    print("=" * 66)
    print("  SSH key rotation complete.")
    print(f"  New key stored in Secrets Manager: {secret_name}")
    if ssh_keypair:
        print(f"  Local .pem updated: {ssh_keypair}")
    print(f"  Verify access: ssh -i {ssh_keypair} {ec2_user}@{head_ip}")
    print("=" * 66)


if __name__ == "__main__":
    main()
