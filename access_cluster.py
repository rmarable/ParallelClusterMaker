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
import subprocess  # noqa: F401 -- tests/test_kill_access.py patches mod.subprocess.run

sys.path.insert(0, _src_dir)
from pcluster_core import (
    PClusterMakerError,
    core_ensure_generated_script,
    _resolve_access_node_type,
    _validate_cluster_name,
    _read_cluster_record,
    core_exec_access_script,
    core_resolve_access_node_type,
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
    # Rendered on demand when the build never wrote one -- an MCP build
    # returns before the scripts are copied out of stage_dir, and telling
    # the operator to rebuild (as this did) was wrong: the cluster is fine.
    try:
        core_ensure_generated_script(
            cluster_data_root=_cluster_data_root, cluster_name=cluster_name,
            repo_root=_repo_root, template="access_cluster.j2",
            dest_name=f"access_cluster.{cluster_name}.sh",
        )
    except PClusterMakerError as e:
        sys.exit(f"ERROR: {e}")

    rec = _read_cluster_record(cluster_name, _repo_root) or {}
    try:
        info = core_resolve_access_node_type(
            rec, cluster_name,
            login_node_requested=args.login_node,
            head_node_requested=args.head_node,
        )
    except PClusterMakerError as e:
        sys.exit(str(e))

    print(f"Connecting to {info.node_label} of {cluster_name}...")
    returncode = core_exec_access_script(
        cluster_data_root=_cluster_data_root,
        cluster_name=cluster_name,
        node_type=info.node_type,
    )
    if returncode != 0:
        print(f"ERROR: SSH session exited with code {returncode}.", file=sys.stderr)
    sys.exit(returncode)


if __name__ == "__main__":
    main()
