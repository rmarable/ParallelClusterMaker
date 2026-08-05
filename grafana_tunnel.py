#!/usr/bin/env python
#
################################################################################
# Name:         grafana_tunnel.py
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Purpose:      Open or close the Grafana SSH tunnel for a running cluster
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

sys.path.insert(0, _src_dir)
from pcluster_core import _validate_cluster_name, _read_cluster_record


def main():
    parser = argparse.ArgumentParser(
        description="Open or close the Grafana SSH tunnel for a ParallelCluster stack."
    )
    parser.add_argument("-N", "--cluster_name", required=True, help="Cluster name")
    parser.add_argument("-P", "--port", type=int, default=8443,
                        help="Local port for the tunnel (default: 8443)")
    parser.add_argument("-S", "--stop", action="store_true",
                        help="Stop a running tunnel instead of starting one")
    args = parser.parse_args()

    cluster_name = args.cluster_name
    _validate_cluster_name(cluster_name)

    rec = _read_cluster_record(cluster_name, _repo_root)
    if rec is None:
        sys.exit(f"ERROR: no cluster record found for {cluster_name!r}")

    if rec.get("enable_monitoring") != "true":
        sys.exit(
            f"ERROR: monitoring is not enabled for cluster {cluster_name!r}.\n"
            f"  Rebuild with --enable_monitoring=true to use Grafana."
        )

    tunnel_script = os.path.join(
        _repo_root, "active_clusters", cluster_name,
        f"grafana_tunnel.{cluster_name}.sh",
    )
    if not os.path.isfile(tunnel_script):
        sys.exit(
            f"ERROR: tunnel script not found: {tunnel_script}\n"
            f"  Make sure the cluster was built with monitoring enabled."
        )

    port = str(args.port)
    action = "stop" if args.stop else "start"
    # The tunnel script's exit status is the only signal that ssh -L actually
    # bound the port; swallowing it made a dead tunnel look like a live one.
    result = subprocess.run(["bash", tunnel_script, port, action], check=False)
    if result.returncode != 0:
        sys.exit(
            f"ERROR: tunnel script failed to {action} the tunnel "
            f"(exit {result.returncode})."
        )


if __name__ == "__main__":
    main()
