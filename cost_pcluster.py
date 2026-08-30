#!/usr/bin/env python
#
################################################################################
# Name:         cost_pcluster.py
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Purpose:      Report actual AWS spend per cluster via Cost Explorer
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
import json

sys.path.insert(0, _src_dir)
from pcluster_core import (
    ClusterRecord,
    PClusterMakerError,
    _check_tag_activated,
    _date_range,
    _get_cluster_cost,
    _read_cluster_record,
    _safe,
    _validate_cluster_name,
    core_get_cost_report,
)

# Re-exported on purpose. These names are not called in this file -- the
# entry point delegates to pcluster_core -- but the test suite reaches them
# *through this module*, which is what keeps each shim honest about the
# core it fronts. Declared in __all__ rather than silenced with a noqa:
# __all__ states the intent, and both pyflakes and ruff read it, so
# `ruff check --fix` cannot quietly delete a working re-export.
__all__ = [
    "_check_tag_activated",
    "_date_range",
    "_get_cluster_cost",
    "_safe",
]

_ACTIVE_CLUSTERS_DIR = os.path.join(_repo_root, "active_clusters")


def _enumerate_cluster_names():
    names = []
    try:
        with os.scandir(_ACTIVE_CLUSTERS_DIR) as it:
            for entry in it:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                try:
                    _validate_cluster_name(entry.name)
                    names.append(entry.name)
                except SystemExit:
                    pass
    except OSError:
        pass
    return sorted(names)


def _load_cluster_records(cluster_names):
    """Read each cluster's local vars file and build a ClusterRecord. A
    missing/unparseable vars file still gets a row (owner/region "unknown"),
    matching this script's existing behavior."""
    records = []
    for name in cluster_names:
        rec = _read_cluster_record(name, _repo_root)
        records.append(
            ClusterRecord.from_dict(rec) if rec is not None else ClusterRecord.unknown(name)
        )
    return records


def _format_table(rows, period):
    """rows: list of (cluster, owner, region, cost_str).
    period is printed once in the header rather than repeated per row.
    """
    if not rows:
        print("No clusters found.")
        return
    H = ("Cluster", "Owner", "Region", "Cost ($)")
    widths = [max(len(H[i]), max(len(r[i]) for r in rows)) for i in range(4)]
    sep = "  "
    print(f"Period: {period}\n")
    print(sep.join(H[i].ljust(widths[i]) for i in range(4)))
    print(sep.join("-" * widths[i] for i in range(4)))
    for r in rows:
        print(sep.join(r[i].ljust(widths[i]) for i in range(4)))


def main():
    parser = argparse.ArgumentParser(
        description="Report actual AWS spend per cluster via Cost Explorer."
    )
    parser.add_argument("-N", "--cluster_name", default=None,
                        help="Single cluster name (default: all in active_clusters/)")
    parser.add_argument("-O", "--owner", default=None,
                        help="Filter to clusters owned by this user")
    parser.add_argument("-D", "--days", type=int, default=30,
                        help="Lookback window in days (default: 30, max: 365)")
    parser.add_argument("-J", "--json", action="store_true",
                        help="Emit JSON array instead of table")
    args = parser.parse_args()

    if args.cluster_name:
        _validate_cluster_name(args.cluster_name)
        cluster_names = [args.cluster_name]
    else:
        cluster_names = _enumerate_cluster_names()
        if not cluster_names:
            sys.exit("No clusters found in active_clusters/")

    cluster_records = _load_cluster_records(cluster_names)

    try:
        result = core_get_cost_report(
            cluster_records=cluster_records, owner_filter=args.owner, days=args.days
        )
    except PClusterMakerError as e:
        sys.exit(str(e))

    if result.tag_activated is False:
        print(
            "WARNING: 'ClusterID' is not an active cost allocation tag — "
            "all results will show $0.00.\n"
            "Activate it at: AWS Console → Billing → Cost allocation tags "
            "→ User-defined tags.\n"
        )
    elif result.tag_activated is None:
        print(
            "NOTE: could not verify 'ClusterID' tag activation "
            "(needs ce:ListCostAllocationTags). Results may show $0.00 "
            "if the tag is not active.\n"
        )

    rows = []
    tag_warning_shown = False

    for r in result.records:
        if r.error:
            cost_str = f"unavailable — {r.error}"
        elif r.cost_usd == 0.0:
            cost_str = "$0.00 *"
            tag_warning_shown = True
        else:
            cost_str = f"${r.cost_usd:.2f}"

        rows.append((r.cluster_name, r.owner, r.region, cost_str))

    period = f"{result.period_start} – {result.period_end}"

    if args.json:
        out = [
            {"cluster": r[0], "owner": r[1], "region": r[2],
             "period": period, "cost_usd": r[3]}
            for r in rows
        ]
        print(json.dumps(out, indent=2))
        return

    print(f"AWS Cost Explorer — last {args.days} days  (24-hour data lag applies)\n")
    _format_table(rows, period)

    if tag_warning_shown:
        print(
            "\n* $0.00 may mean no spend, or that 'ClusterID' is not activated as a\n"
            "  cost allocation tag. Activate it at: AWS Console → Billing → "
            "Cost allocation tags."
        )


if __name__ == "__main__":
    main()
