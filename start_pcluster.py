#!/usr/bin/env python
#
################################################################################
# Name:         start_pcluster.py
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Purpose:      Start the compute fleet of a stopped ParallelCluster stack
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
    _validate_cluster_name,
    _validate_region,
    _read_cluster_record,
    _get_fleet_status,
    _fleet_action_plan,
    core_start_fleet,
)

_PCLUSTER_BIN = os.path.join(_repo_root, ".venv", "bin", "pcluster")


def main():
    parser = argparse.ArgumentParser(
        description="Start the compute fleet of a stopped ParallelCluster stack."
    )
    parser.add_argument("-N", "--cluster_name", required=True, help="Cluster name")
    parser.add_argument("-R", "--region", default=None,
                        help="AWS region (default: from cluster record)")
    parser.add_argument("-W", "--wait", action="store_true",
                        help="Wait for fleet to reach RUNNING before exiting")
    args = parser.parse_args()

    cluster_name = args.cluster_name
    _validate_cluster_name(cluster_name)

    rec = _read_cluster_record(cluster_name, _repo_root)
    if rec is None:
        sys.exit(f"ERROR: no cluster record found for {cluster_name!r}")

    region = args.region or rec["region"]
    if not region:
        sys.exit(f"ERROR: no region found for cluster {cluster_name!r} — pass -R/--region")
    _validate_region(region)

    # Preflight, purely to print the "already in progress" message at the
    # right point in the sequence -- core_start_fleet re-derives status/plan
    # itself right before acting, so a race in the small window between
    # this check and that one is safe either way.
    status = _get_fleet_status(cluster_name, region, _PCLUSTER_BIN)
    print(f"Cluster:              {cluster_name}")
    print(f"Region:               {region}")
    print(f"computeFleetStatus:   {status}")

    plan = _fleet_action_plan(status, "start")
    if plan == "abort":
        sys.exit(
            "ERROR: compute fleet is in PROTECTED state — investigate node failures before retrying."
        )
    if plan == "done":
        print(f"Fleet is already {status} — nothing to do.")
        sys.exit(0)
    if plan == "wait":
        print(f"Fleet is already {status} — a start is already in progress.")

    cluster_record = ClusterRecord.from_dict(rec)
    try:
        core_start_fleet(
            cluster_record=cluster_record, region=region, pcluster_bin=_PCLUSTER_BIN,
            wait=args.wait,
        )
    except PClusterMakerError as e:
        sys.exit(str(e))

    if not args.wait:
        print(
            f"Check status: pcluster describe-cluster --cluster-name {cluster_name} --region {region}"
        )


if __name__ == "__main__":
    main()
