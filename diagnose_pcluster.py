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

sys.path.insert(0, _src_dir)
from pcluster_core import (
    ClusterRecord,
    PClusterMakerError,
    _VALID_EC2_USERS,
    _clamp_int,
    _format_sinfo,
    _parse_sacct,
    _read_cluster_record,
    _sinfo_state_is_ok,
    _tail_lines,
    _validate_cluster_name,
    core_diagnose_cluster,
)

# Re-exported on purpose. These names are not called in this file -- the
# entry point delegates to pcluster_core -- but the test suite reaches them
# *through this module*, which is what keeps each shim honest about the
# core it fronts. Declared in __all__ rather than silenced with a noqa:
# __all__ states the intent, and both pyflakes and ruff read it, so
# `ruff check --fix` cannot quietly delete a working re-export.
__all__ = [
    "_VALID_EC2_USERS",
    "_format_sinfo",
    "_parse_sacct",
    "_sinfo_state_is_ok",
    "_tail_lines",
]

_PCLUSTER_BIN = os.path.join(_repo_root, ".venv", "bin", "pcluster")
_MAX_CW_LINES = 500
_MAX_LOG_LINES = 200
_MIN_TIMEOUT = 1
_MAX_TIMEOUT = 300
_MIN_HOURS = 1
_MAX_HOURS = 8760  # one year — sacct retention never exceeds this


def _banner(title):
    print(f"\n=== {title} ===\n")


def _sub_banner(title):
    print(f"\n  --- {title} ---")


def _print_report(report, hours, log_lines):
    print(f"Diagnosing cluster: {report.cluster_name}  ({report.region})")
    print(f"  serial: {report.serial}")

    if report.head_ip_error:
        print(f"\nERROR: cannot reach cluster — {report.head_ip_error}")
        print("CloudWatch logs may still be available; omit --no_cw to enable them.")
    else:
        print(f"  head node: {report.head_ip}")

    if report.cloudwatch is not None:
        _banner("CloudWatch: head node bootstrap logs")
        cw = report.cloudwatch
        if cw.log_group:
            print(f"  log group: {cw.log_group}")
        if cw.error:
            print(f"  {cw.error}")
        else:
            for stream, lines in cw.streams.items():
                _sub_banner(stream)
                if lines:
                    print("\n".join(lines))
                else:
                    print("  (no events found)")

    if report.sinfo is None:
        reason = (
            "head node unreachable"
            if report.head_ip is None
            else "SSH unavailable on this transport"
        )
        print(f"\nSkipping SSH-dependent sections — {reason}.")
        sys.exit(0)

    _banner("Slurm node states (sinfo -N -l)")
    if report.sinfo.error:
        print(f"  {report.sinfo.error}")
    elif report.sinfo.formatted_output:
        print(report.sinfo.formatted_output)
    else:
        print("  (no nodes reported)")

    _banner(f"Recent Slurm job failures (last {hours}h)")
    if report.sacct.error:
        print(f"  {report.sacct.error}")
    elif report.sacct.formatted_output:
        print(report.sacct.formatted_output)
    else:
        print(f"  No failed jobs in the last {hours}h")
        print("  (If this is unexpected, Slurm accounting may not be enabled.)")

    _banner(f"Local log tails (last {log_lines} lines each)")
    for tail in report.local_logs:
        _sub_banner(tail.path)
        if tail.error:
            print(f"  ({tail.error})")
        elif tail.content:
            print(tail.content)
        else:
            print("  (empty)")

    _banner("Postinstall marker")
    p = report.postinstall
    if p.error:
        print(f"  ({p.error})")
    else:
        status = "[PASS]" if p.marker_present else "[FAIL]"
        marker = "/opt/parallelcluster/shared/custom_action_done"
        print(f"  {status} {marker}  (serial: {report.serial})")

    print("")


def main():
    parser = argparse.ArgumentParser(
        description="Deep diagnostic for a running ParallelCluster stack."
    )
    parser.add_argument("-N", "--cluster_name", required=True, help="Cluster name")
    parser.add_argument("-R", "--region", default=None, help="Override AWS region")
    parser.add_argument(
        "-T",
        "--timeout",
        type=int,
        default=20,
        help=f"SSH timeout in seconds (default: 20, min: {_MIN_TIMEOUT}, max: {_MAX_TIMEOUT})",
    )
    parser.add_argument(
        "--cw_lines",
        type=int,
        default=50,
        help=f"CloudWatch log lines per stream (default: 50, max: {_MAX_CW_LINES})",
    )
    parser.add_argument(
        "--log_lines",
        type=int,
        default=30,
        help=f"Local log file tail lines (default: 30, max: {_MAX_LOG_LINES})",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help=f"sacct lookback window in hours (default: 24, min: {_MIN_HOURS}, max: {_MAX_HOURS})",
    )
    parser.add_argument(
        "--no_cw",
        action="store_true",
        help="Skip CloudWatch log section",
    )
    args = parser.parse_args()

    cluster_name = args.cluster_name
    _validate_cluster_name(cluster_name)

    cw_lines = _clamp_int(args.cw_lines, 1, _MAX_CW_LINES, "--cw_lines")
    log_lines = _clamp_int(args.log_lines, 1, _MAX_LOG_LINES, "--log_lines")
    timeout = _clamp_int(args.timeout, _MIN_TIMEOUT, _MAX_TIMEOUT, "-T/--timeout")
    hours = _clamp_int(args.hours, _MIN_HOURS, _MAX_HOURS, "--hours")

    rec = _read_cluster_record(cluster_name, _repo_root)
    if rec is None:
        sys.exit(f"ERROR: no vars file found for cluster {cluster_name!r}")
    cluster_record = ClusterRecord.from_dict(rec)

    try:
        report = core_diagnose_cluster(
            cluster_record=cluster_record,
            pcluster_bin=_PCLUSTER_BIN,
            region_override=args.region,
            timeout=timeout,
            cw_lines=cw_lines,
            log_lines=log_lines,
            hours=hours,
            include_cloudwatch=not args.no_cw,
            ssh_available=True,
        )
    except PClusterMakerError as e:
        sys.exit(str(e))

    _print_report(report, hours, log_lines)


if __name__ == "__main__":
    main()
