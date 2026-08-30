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

sys.path.insert(0, _src_dir)
from pcluster_core import (
    ClusterRecord,
    _clamp_int,
    _read_cluster_record,
    _validate_cluster_name,
    check_cfn_status,
    check_grafana,
    check_head_ip,
    check_postinstall,
    check_s3,
    check_slurm,
    check_ssh,
    core_check_cluster_health,
)

# Re-exported on purpose. These names are not called in this file -- the
# entry point delegates to pcluster_core -- but the test suite reaches them
# *through this module*, which is what keeps each shim honest about the
# core it fronts. Declared in __all__ rather than silenced with a noqa:
# __all__ states the intent, and both pyflakes and ruff read it, so
# `ruff check --fix` cannot quietly delete a working re-export.
__all__ = [
    "check_cfn_status",
    "check_grafana",
    "check_head_ip",
    "check_postinstall",
    "check_s3",
    "check_slurm",
    "check_ssh",
]

_PCLUSTER_BIN = os.path.join(_repo_root, ".venv", "bin", "pcluster")

_MIN_TIMEOUT = 1
_MAX_TIMEOUT = 300

_PASS = "[PASS]"
_FAIL = "[FAIL]"
_SKIP = "[SKIP]"


def _print_report(report):
    """Reconstructs today's exact CLI text from a ClusterHealthReport.
    "CloudFormation status" and "head node IP" use a ':' connector on pass
    and an em dash on fail/skip; every other check uses an em dash suffix
    only when detail is set -- both preserved from the pre-refactor script."""
    symbols = {"pass": _PASS, "fail": _FAIL, "skip": _SKIP}
    for c in report.checks:
        symbol = symbols[c.status]
        if c.name in ("CloudFormation status", "head node IP"):
            connector = ":" if c.status == "pass" else " —"
            print(f"  {symbol} {c.name}{connector} {c.detail}")
        else:
            suffix = f" — {c.detail}" if c.detail else ""
            print(f"  {symbol} {c.name}{suffix}")


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

    rec = _read_cluster_record(cluster_name, _repo_root)
    if rec is None:
        print(f"  {_FAIL} vars file — vars file missing or unreadable")
        print("\n1 check(s) failed.")
        sys.exit(1)
    print(f"  {_PASS} vars file")
    cluster_record = ClusterRecord.from_dict(rec)

    report = core_check_cluster_health(
        cluster_record=cluster_record,
        pcluster_bin=_PCLUSTER_BIN,
        timeout=timeout,
        ssh_available=True,
    )

    _print_report(report)

    print("")
    if report.healthy:
        print(f"All checks passed — {cluster_name} is healthy.")
        sys.exit(0)
    else:
        failures = sum(1 for c in report.checks if c.status == "fail")
        print(f"{failures} check(s) failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
