#!/usr/bin/env python
#
################################################################################
# Name:         access_cluster.py
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Created On:   April 20, 2019
# Purpose:	Provide a mechanism for SSH-ing into pcluster head nodes
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
from pcluster_core import (
    _resolve_access_script_path,
    _validate_cluster_name,
    _read_cluster_record,
)


def main():
    parser = argparse.ArgumentParser(
        description="access_cluster.py: Provide quick SSH access to ParallelCluster head/login nodes"
    )
    parser.add_argument(
        "--cluster_name", "-N", help="cluster name (REQUIRED)", required=True
    )
    _node_group = parser.add_mutually_exclusive_group()
    _node_group.add_argument(
        "--login_node", "-L", action="store_true",
        help=(
            "connect to the login node pool instead of the head node "
            "(requires --enable_loginnode=true at build time; when "
            "loginnode_count > 1, connects to an unspecified member of the "
            "pool, not a chosen one)"
        ),
    )
    _node_group.add_argument(
        "--head_node", "-H", action="store_true",
        help="connect to the head node (default, unless a login node is enabled)",
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

    rec = _read_cluster_record(cluster_name, _repo_root) or {}
    loginnode_enabled = rec.get("enable_loginnode") == "true"

    if args.login_node and not loginnode_enabled:
        sys.exit(
            f"ERROR: no login node is configured for cluster '{cluster_name}'.\n"
            f"  Rebuild with --enable_loginnode=true to use -L."
        )

    if args.login_node:
        node_type = "LoginNode"
    elif args.head_node:
        node_type = "HeadNode"
    else:
        node_type = "LoginNode" if loginnode_enabled else "HeadNode"

    node_label = "login node" if node_type == "LoginNode" else "head node"
    print(f"Connecting to {node_label} of {cluster_name}...")
    env = dict(os.environ, ACCESS_NODE_TYPE=node_type)
    result = subprocess.run(["bash", access_script], env=env)
    if result.returncode != 0:
        print(
            f"ERROR: SSH session exited with code {result.returncode}.", file=sys.stderr
        )
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
