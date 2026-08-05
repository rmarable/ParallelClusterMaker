#!/usr/bin/env python
#
################################################################################
# Name:         check_pcluster.py
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Purpose:      Health check for a running ParallelCluster stack
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
import json
import subprocess

import boto3
from botocore.exceptions import ClientError, BotoCoreError

sys.path.insert(0, _src_dir)
from pcluster_core import (
    _validate_cluster_name,
    _read_cluster_record,
    _clamp_int,
    _classify_sinfo_nodes,
)

_PCLUSTER_BIN = os.path.join(_repo_root, ".venv", "bin", "pcluster")

_MIN_TIMEOUT = 1
_MAX_TIMEOUT = 300

_PASS = "[PASS]"
_FAIL = "[FAIL]"
_SKIP = "[SKIP]"


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


def check_vars_file(cluster_name):
    rec = _read_cluster_record(cluster_name, _repo_root)
    if rec is None:
        return False, "vars file missing or unreadable", None
    return True, None, rec


def check_cfn_status(cluster_name, region):
    try:
        result = subprocess.run(
            [_PCLUSTER_BIN, "describe-cluster",
             "--cluster-name", cluster_name,
             "--region", region],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return False, f"pcluster describe-cluster failed (rc={result.returncode})", None
        data = json.loads(result.stdout)
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
    except subprocess.TimeoutExpired:
        return False, "pcluster describe-cluster timed out", None
    except (json.JSONDecodeError, KeyError) as e:
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
    except (ClientError, BotoCoreError) as e:
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


def main():
    parser = argparse.ArgumentParser(
        description="Health check for a running ParallelCluster stack."
    )
    parser.add_argument("-N", "--cluster_name", required=True, help="Cluster name")
    parser.add_argument(
        "-T", "--timeout", type=int, default=15,
        help=f"SSH timeout in seconds (default: 15, min: {_MIN_TIMEOUT}, max: {_MAX_TIMEOUT})"
    )
    args = parser.parse_args()

    cluster_name = args.cluster_name
    _validate_cluster_name(cluster_name)
    timeout = _clamp_int(args.timeout, _MIN_TIMEOUT, _MAX_TIMEOUT, "-T/--timeout")

    print(f"Checking cluster: {cluster_name}")

    failures = 0

    ok, err, rec = check_vars_file(cluster_name)
    if ok:
        print(f"  {_PASS} vars file")
    else:
        print(f"  {_FAIL} vars file — {err}")
        print(f"\n1 check(s) failed.")
        sys.exit(1)

    region            = rec["region"]
    ssh_keypair       = rec["ssh_keypair"]
    ec2_user          = rec["ec2_user"]
    s3_bucketname     = rec["s3_bucketname"]
    enable_monitoring = rec["enable_monitoring"]

    ok, msg, head_ip = check_cfn_status(cluster_name, region)
    if ok:
        print(f"  {_PASS} CloudFormation status: {msg.split('=')[1]}")
    else:
        print(f"  {_FAIL} CloudFormation status — {msg}")
        failures += 1

    if head_ip is not None:
        ok, msg = check_head_ip(head_ip)
        if ok:
            print(f"  {_PASS} head node IP: {head_ip}")
        else:
            print(f"  {_FAIL} head node IP — {msg}")
            head_ip = None
            failures += 1
    else:
        print(f"  {_SKIP} head node IP — CloudFormation check failed")

    ssh_ok = False
    if head_ip:
        ok, err = check_ssh(head_ip, ssh_keypair, ec2_user, timeout)
        if ok:
            print(f"  {_PASS} SSH reachability")
            ssh_ok = True
        else:
            print(f"  {_FAIL} SSH reachability — {err}")
            failures += 1
    else:
        print(f"  {_SKIP} SSH reachability — head node IP unavailable")

    if ssh_ok:
        ok, err = check_slurm(head_ip, ssh_keypair, ec2_user, timeout)
        if ok:
            # err is a partial-degradation note here, not a failure: some nodes
            # are usable but not all. Printing it is the whole point of reading
            # sinfo's output rather than only its exit status.
            print(f"  {_PASS} Slurm" + (f" — {err}" if err else ""))
        else:
            print(f"  {_FAIL} Slurm — {err}")
            failures += 1

        ok, err = check_postinstall(head_ip, ssh_keypair, ec2_user, timeout)
        if ok:
            print(f"  {_PASS} postinstall complete")
        else:
            print(f"  {_FAIL} postinstall complete — {err}")
            failures += 1

        if enable_monitoring == "true":
            ok, err = check_grafana(head_ip, ssh_keypair, ec2_user, timeout)
            if ok:
                print(f"  {_PASS} Grafana health")
            else:
                print(f"  {_FAIL} Grafana health — {err}")
                failures += 1
    else:
        skips = ["Slurm (sinfo -s)", "postinstall complete"]
        if enable_monitoring == "true":
            skips.append("Grafana health")
        for s in skips:
            print(f"  {_SKIP} {s} — SSH unreachable")

    ok, err = check_s3(s3_bucketname, region)
    if ok:
        print(f"  {_PASS} S3 bucket: {s3_bucketname}")
    else:
        print(f"  {_FAIL} S3 bucket: {s3_bucketname} — {err}")
        failures += 1

    print("")
    if failures == 0:
        print(f"All checks passed — {cluster_name} is healthy.")
        sys.exit(0)
    else:
        print(f"{failures} check(s) failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
