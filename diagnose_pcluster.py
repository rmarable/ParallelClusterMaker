#!/usr/bin/env python
#
################################################################################
# Name:         diagnose_pcluster.py
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Purpose:      Deep diagnostic for a running ParallelCluster stack
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
import subprocess
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import BotoCoreError, ClientError

sys.path.insert(0, _src_dir)
from pcluster_core import (
    _validate_cluster_name,
    _read_cluster_record,
    _run_pcluster_cmd,
    _clamp_int,
    _select_cw_log_group,
    _sinfo_state_is_ok,
)

_PCLUSTER_BIN = os.path.join(_repo_root, ".venv", "bin", "pcluster")
_VALID_EC2_USERS = {"ubuntu", "ec2-user"}
_MAX_CW_LINES = 500
_MAX_LOG_LINES = 200
_MIN_TIMEOUT = 1
_MAX_TIMEOUT = 300
_MIN_HOURS = 1
_MAX_HOURS = 8760  # one year — sacct retention never exceeds this

_LOCAL_LOGS = [
    "/var/log/parallelcluster/slurm_resume.log",
    "/var/log/parallelcluster/slurm_suspend.log",
    "/var/log/cinc/client.log",
    "/var/log/cloud-init-output.log",
]

_CW_STREAMS = ["cfn-init", "cloud-init-output", "cinc_client"]


def _banner(title):
    print(f"\n=== {title} ===\n")


def _sub_banner(title):
    print(f"\n  --- {title} ---")


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


def _get_head_ip(cluster_name, region):
    """Return (head_ip, error_str). Calls pcluster describe-cluster."""
    try:
        data = _run_pcluster_cmd(
            ["describe-cluster", "--cluster-name", cluster_name, "--region", region],
            _PCLUSTER_BIN,
        )
    except SystemExit as e:
        return None, str(e)
    cs = data.get("clusterStatus", "UNKNOWN")
    if cs != "CREATE_COMPLETE":
        return None, f"cluster not in CREATE_COMPLETE state (clusterStatus={cs})"
    head_node = data.get("headNode", {})
    ip = head_node.get("publicIpAddress") or head_node.get("privateIpAddress") or ""
    if not ip:
        return None, "no IP address in describe-cluster response"
    return ip, None


def _fetch_cw_logs(cluster_name, region, streams, n_lines):
    """Fetch the last n_lines events from each CW stream. Returns dict {stream: [lines]}."""
    logs = boto3.client("logs", region_name=region)
    results = {}
    try:
        group_names = []
        for page in logs.get_paginator("describe_log_groups").paginate(
            logGroupNamePrefix=f"/aws/parallelcluster/{cluster_name}-"
        ):
            for g in page.get("logGroups", []):
                group_names.append(g["logGroupName"])
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "AccessDeniedException":
            return None, f"CloudWatch unavailable: {code} — add logs:DescribeLogGroups on Resource \"*\" to the operator policy, or use --no_cw"
        return None, f"CloudWatch error: {code}"
    except BotoCoreError as e:
        return None, f"CloudWatch network error: {e}"

    log_group = _select_cw_log_group(cluster_name, group_names)
    if log_group is None:
        return None, (
            f"no CloudWatch log group for '{cluster_name}' — PCluster creates "
            f"/aws/parallelcluster/{cluster_name}-<timestamp> once the head node "
            "starts logging, which is several minutes into a build"
        )
    print(f"  log group: {log_group}")

    try:
        existing = []
        for page in logs.get_paginator("describe_log_streams").paginate(
            logGroupName=log_group, orderBy="LastEventTime", descending=True
        ):
            for s in page.get("logStreams", []):
                existing.append(s["logStreamName"])
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "AccessDeniedException":
            return None, f"CloudWatch unavailable: {code} — add logs:DescribeLogStreams to the operator policy, or use --no_cw"
        return None, f"CloudWatch error: {code}"
    except BotoCoreError as e:
        return None, f"CloudWatch network error: {e}"

    for stream in streams:
        matched = [s for s in existing if stream in s]
        if not matched:
            results[stream] = []
            continue
        target = matched[0]
        all_lines = []
        kwargs = dict(
            logGroupName=log_group,
            logStreamName=target,
            startFromHead=False,
        )
        try:
            while len(all_lines) < n_lines:
                resp = logs.get_log_events(**kwargs)
                events = resp.get("events", [])
                if not events:
                    break
                page_lines = [
                    f"  {datetime.fromtimestamp(e['timestamp'] / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}  {e['message'].rstrip()}"
                    for e in events
                ]
                all_lines = page_lines + all_lines
                next_token = resp.get("nextBackwardToken")
                if not next_token or next_token == kwargs.get("nextToken"):
                    break
                kwargs["nextToken"] = next_token
        except (ClientError, BotoCoreError):
            pass
        results[stream] = all_lines[-n_lines:]

    return results, None


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
            # Find STATE column index in the header
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


def main():
    parser = argparse.ArgumentParser(
        description="Deep diagnostic for a running ParallelCluster stack."
    )
    parser.add_argument("-N", "--cluster_name", required=True, help="Cluster name")
    parser.add_argument("-R", "--region", default=None, help="Override AWS region")
    parser.add_argument(
        "-T", "--timeout", type=int, default=20,
        help=f"SSH timeout in seconds (default: 20, min: {_MIN_TIMEOUT}, max: {_MAX_TIMEOUT})",
    )
    parser.add_argument(
        "--cw_lines", type=int, default=50,
        help=f"CloudWatch log lines per stream (default: 50, max: {_MAX_CW_LINES})",
    )
    parser.add_argument(
        "--log_lines", type=int, default=30,
        help=f"Local log file tail lines (default: 30, max: {_MAX_LOG_LINES})",
    )
    parser.add_argument(
        "--hours", type=int, default=24,
        help=f"sacct lookback window in hours (default: 24, min: {_MIN_HOURS}, max: {_MAX_HOURS})",
    )
    parser.add_argument(
        "--no_cw", action="store_true",
        help="Skip CloudWatch log section",
    )
    args = parser.parse_args()

    cluster_name = args.cluster_name
    _validate_cluster_name(cluster_name)

    cw_lines = _clamp_int(args.cw_lines, 1, _MAX_CW_LINES, "--cw_lines")
    log_lines = _clamp_int(args.log_lines, 1, _MAX_LOG_LINES, "--log_lines")
    args.timeout = _clamp_int(args.timeout, _MIN_TIMEOUT, _MAX_TIMEOUT, "-T/--timeout")
    args.hours = _clamp_int(args.hours, _MIN_HOURS, _MAX_HOURS, "--hours")

    rec = _read_cluster_record(cluster_name, _repo_root)
    if rec is None:
        sys.exit(f"ERROR: no vars file found for cluster {cluster_name!r}")

    region = args.region or rec["region"]
    ssh_keypair = rec["ssh_keypair"]
    enable_monitoring = rec["enable_monitoring"]
    serial = rec.get("serial", "unknown")

    raw_ec2_user = rec["ec2_user"]
    if raw_ec2_user not in _VALID_EC2_USERS:
        sys.exit(
            f"ERROR: unrecognized ec2_user {raw_ec2_user!r} in vars file. "
            f"Expected one of: {sorted(_VALID_EC2_USERS)}"
        )
    ec2_user = raw_ec2_user

    print(f"Diagnosing cluster: {cluster_name}  ({region})")
    print(f"  serial: {serial}")

    # --- Head node IP ---
    head_ip, err = _get_head_ip(cluster_name, region)
    if err:
        print(f"\nERROR: cannot reach cluster — {err}")
        print("CloudWatch logs may still be available; omit --no_cw to enable them.")
        ssh_ok = False
    else:
        print(f"  head node: {head_ip}")
        ssh_ok = True

    # -------------------------------------------------------------------------
    # Section 1: CloudWatch bootstrap logs
    # -------------------------------------------------------------------------
    if not args.no_cw:
        _banner("CloudWatch: head node bootstrap logs")
        streams = _CW_STREAMS
        if enable_monitoring == "true":
            streams = streams + ["grafana", "prometheus"]
        cw_results, cw_err = _fetch_cw_logs(cluster_name, region, streams, cw_lines)
        if cw_err:
            print(f"  {cw_err}")
        else:
            for stream, lines in cw_results.items():
                _sub_banner(stream)
                if lines:
                    print("\n".join(lines))
                else:
                    print("  (no events found)")

    # -------------------------------------------------------------------------
    # Remaining sections require SSH
    # -------------------------------------------------------------------------
    if not ssh_ok:
        print("\nSkipping SSH-dependent sections — head node unreachable.")
        sys.exit(0)

    timeout = args.timeout

    # -------------------------------------------------------------------------
    # Section 2: Slurm node states
    # -------------------------------------------------------------------------
    _banner("Slurm node states (sinfo -N -l)")
    try:
        rc, stdout, stderr = _run_ssh(
            head_ip, ssh_keypair, ec2_user, timeout,
            ["sinfo", "-N", "-l"],
        )
        if rc == 0 and stdout.strip():
            print(_format_sinfo(stdout))
        elif rc == 0:
            print("  (no nodes reported)")
        else:
            print(f"  sinfo failed (rc={rc}): {stderr.strip()[:200]}")
    except subprocess.TimeoutExpired:
        print("  sinfo timed out")
    except OSError as e:
        print(f"  sinfo failed: {e}")

    # -------------------------------------------------------------------------
    # Section 3: Recent Slurm job failures
    # -------------------------------------------------------------------------
    since = (datetime.now(timezone.utc) - timedelta(hours=args.hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _banner(f"Recent Slurm job failures (last {args.hours}h)")
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
                print("  sacct not available — Slurm accounting is not enabled on this cluster")
            else:
                print(f"  sacct failed (rc={rc}): {stderr_short}")
        else:
            parsed = _parse_sacct(stdout)
            if parsed:
                print(parsed)
            else:
                print(f"  No failed jobs in the last {args.hours}h")
                print("  (If this is unexpected, Slurm accounting may not be enabled.)")
    except subprocess.TimeoutExpired:
        print("  sacct timed out")
    except OSError as e:
        print(f"  sacct failed: {e}")

    # -------------------------------------------------------------------------
    # Section 4: Local log tails
    # -------------------------------------------------------------------------
    _banner(f"Local log tails (last {log_lines} lines each)")
    for log_path in _LOCAL_LOGS:
        _sub_banner(log_path)
        try:
            rc, stdout, stderr = _run_ssh(
                head_ip, ssh_keypair, ec2_user, timeout,
                ["tail", "-n", str(log_lines), log_path],
            )
            if rc == 0:
                trimmed = _tail_lines(stdout, log_lines)
                if trimmed:
                    print(trimmed)
                else:
                    print("  (empty)")
            else:
                print(f"  (unavailable — {stderr.strip()[:120]})")
        except subprocess.TimeoutExpired:
            print("  (timed out)")
        except OSError as e:
            print(f"  (error: {e})")

    # -------------------------------------------------------------------------
    # Section 5: Postinstall marker
    # -------------------------------------------------------------------------
    _banner("Postinstall marker")
    marker = "/opt/parallelcluster/shared/custom_action_done"
    try:
        rc, _, _ = _run_ssh(
            head_ip, ssh_keypair, ec2_user, timeout, ["test", "-f", marker]
        )
        status = "[PASS]" if rc == 0 else "[FAIL]"
        print(f"  {status} {marker}  (serial: {serial})")
    except subprocess.TimeoutExpired:
        print("  (timed out)")
    except OSError as e:
        print(f"  (error: {e})")

    print("")


if __name__ == "__main__":
    main()
