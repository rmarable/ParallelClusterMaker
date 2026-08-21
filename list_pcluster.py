#!/usr/bin/env python
#
################################################################################
# Name:         list_pcluster.py
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Purpose:      List ParallelCluster stacks managed by this repo
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
import dataclasses
import json

sys.path.insert(0, _src_dir)
from pcluster_core import (
    ClusterRecord,
    _age_str,
    _read_cluster_record,
    _validate_cluster_name,
    core_list_clusters,
)

_PCLUSTER_BIN = os.path.join(_repo_root, ".venv", "bin", "pcluster")
_ACTIVE_CLUSTERS_DIR = os.path.join(_repo_root, "active_clusters")
_TRUNC = 20


def _truncate(s, width):
    if len(s) <= width:
        return s
    return s[: width - 1] + "…"


def _enumerate_clusters():
    if not os.path.isdir(_ACTIVE_CLUSTERS_DIR):
        return []
    names = []
    for entry in os.scandir(_ACTIVE_CLUSTERS_DIR):
        if not entry.is_dir(follow_symlinks=False):
            continue
        name = entry.name
        try:
            _validate_cluster_name(name)
        except SystemExit:
            continue
        names.append(name)
    return sorted(names)


def _print_table(records, wide):
    if not records:
        print("No clusters found.")
        return
    trunc = None if wide else _TRUNC

    def fmt_types(types):
        s = ", ".join(types) if types else "-"
        return s if trunc is None else _truncate(s, trunc)

    def fmt_loginnode(r):
        if r.get("enable_loginnode") != "true":
            return "-"
        s = f"{r.get('loginnode_instance_type', '')} (x{r.get('loginnode_count', 0)})"
        return s if trunc is None else _truncate(s, trunc)

    rows = []
    for r in records:
        cpu_t = fmt_types(r.get("cpu_instance_types") or [])
        gpu_t = fmt_types(r.get("gpu_instance_types") or [])
        login_t = fmt_loginnode(r)
        min_max_cpu = (
            f"{r['initial_cpu_queue_size']}/{r['max_cpu_queue_size']}"
            if r.get("enable_cpu_queue") == "true" else "-/-"
        )
        min_max_gpu = (
            f"{r['initial_gpu_queue_size']}/{r['max_gpu_queue_size']}"
            if r.get("enable_gpu_queue") == "true" else "-/-"
        )
        rows.append([
            r["cluster_name"],
            r["cluster_owner"],
            r["region"],
            r["headnode_instance_type"],
            login_t,
            cpu_t,
            gpu_t,
            min_max_cpu,
            min_max_gpu,
            r["cluster_type"],
            r["age"],
            r["status"],
        ])

    headers = [
        "Cluster", "Owner", "Region", "Head Node", "Login Node",
        "CPU Types", "GPU Types", "Min/Max CPU", "Min/Max GPU",
        "Type", "Age", "Status",
    ]
    widths = [
        max(len(h), max(len(row[i]) for row in rows))
        for i, h in enumerate(headers)
    ]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(fmt.format(*row))


def main():
    parser = argparse.ArgumentParser(
        description="List ParallelCluster stacks managed by this repo."
    )
    parser.add_argument(
        "-L", "--live", action="store_true",
        help="Query pcluster describe-cluster for live status (one API call per cluster).",
    )
    parser.add_argument("-R", "--region", help="Filter by AWS region.")
    parser.add_argument("-O", "--owner", help="Filter by cluster owner.")
    parser.add_argument(
        "-W", "--wide", action="store_true",
        help="Disable column truncation.",
    )
    parser.add_argument(
        "-J", "--json", action="store_true",
        help="Emit JSON array instead of table.",
    )
    args = parser.parse_args()

    names = _enumerate_clusters()
    cluster_records = []
    for name in names:
        rec = _read_cluster_record(name, _repo_root)
        if rec is None:
            print(
                f"WARNING: skipping {name} (vars file missing or unreadable)",
                file=sys.stderr,
            )
            continue
        cluster_records.append(ClusterRecord.from_dict(rec))

    entries = core_list_clusters(
        cluster_records=cluster_records,
        pcluster_bin=_PCLUSTER_BIN,
        region_filter=args.region,
        owner_filter=args.owner,
        live=args.live,
    )
    records = [dataclasses.asdict(e) for e in entries]

    if args.json:
        print(json.dumps(records, indent=2))
    else:
        _print_table(records, args.wide)


if __name__ == "__main__":
    main()
