#!/usr/bin/env python
#
################################################################################
# Name:         stop_pcluster.py
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Purpose:      Stop the compute fleet of a running ParallelCluster stack
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
    core_stop_fleet,
)
from pcluster_aux_data import ctrlC_Abort

_PCLUSTER_BIN = os.path.join(_repo_root, ".venv", "bin", "pcluster")


def main():
    parser = argparse.ArgumentParser(
        description="Stop the compute fleet of a ParallelCluster stack."
    )
    parser.add_argument("-N", "--cluster_name", required=True, help="Cluster name")
    parser.add_argument(
        "-R", "--region", default=None, help="AWS region (default: from cluster record)"
    )
    parser.add_argument(
        "-W", "--wait", action="store_true", help="Wait for fleet to reach STOPPED before exiting"
    )
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

    # Preflight, purely to decide whether the destructive-action confirmation
    # gate below fires and to print the "already in progress" message at the
    # right point in the sequence -- core_stop_fleet re-derives status/plan
    # itself right before acting, so a race in the few-second window between
    # this check and that one is safe either way (today's script has the
    # same latent window during the 5-second ctrlC_Abort wait, with no
    # recheck at all).
    status = _get_fleet_status(cluster_name, region, _PCLUSTER_BIN)
    print(f"Cluster:              {cluster_name}")
    print(f"Region:               {region}")
    print(f"computeFleetStatus:   {status}")

    plan = _fleet_action_plan(status, "stop")
    if plan == "abort":
        sys.exit(
            "ERROR: compute fleet is in PROTECTED state — investigate node failures before retrying."
        )
    if plan == "done":
        print(f"Fleet is already {status} — nothing to do.")
        sys.exit(0)
    if plan == "wait":
        print(f"Fleet is already {status} — a stop is already in progress.")
    if plan == "request":
        print(
            "\n*** WARNING: stopping the fleet terminates all compute nodes immediately.\n"
            "    In-flight Slurm jobs will be killed. ***\n"
        )
        ctrlC_Abort(5, 80, None, None, None, "false")

    cluster_record = ClusterRecord.from_dict(rec)
    try:
        core_stop_fleet(
            cluster_record=cluster_record,
            region=region,
            pcluster_bin=_PCLUSTER_BIN,
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
