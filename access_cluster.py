#!/usr/bin/env python
#
################################################################################
# Name:         access_cluster.py
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Created On:   April 20, 2019
# Last Changed: May 10, 2019
# Purpose:	Provide a mechanism for SSH-ing into pcluster master instances
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
from pcluster_core import _resolve_access_script_path, _validate_cluster_name


def main():
    parser = argparse.ArgumentParser(
        description="access_cluster.py: Provide quick SSH access to ParallelCluster head nodes"
    )
    parser.add_argument(
        "--cluster_name", "-N", help="cluster name (REQUIRED)", required=True
    )
    args = parser.parse_args()

    cluster_name = args.cluster_name
    _validate_cluster_name(cluster_name)

    _cluster_data_root = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "active_clusters"
    )
    access_script = _resolve_access_script_path(_cluster_data_root, cluster_name)

    if not os.path.isfile(access_script):
        sys.exit(
            f"ERROR: Access script not found: {access_script}\n"
            f"  Make sure the cluster was built with: ./make_pcluster.py -N {cluster_name}"
        )

    print(f"Connecting to head node of {cluster_name}...")
    result = subprocess.run(["bash", access_script])
    if result.returncode != 0:
        print(
            f"ERROR: SSH session exited with code {result.returncode}.", file=sys.stderr
        )
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
