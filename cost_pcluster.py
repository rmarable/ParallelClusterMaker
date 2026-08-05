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
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import BotoCoreError, ClientError

sys.path.insert(0, _src_dir)
from pcluster_core import _validate_cluster_name, _read_cluster_record, _safe

_ACTIVE_CLUSTERS_DIR = os.path.join(_repo_root, "active_clusters")


def _utc_today():
    return datetime.now(timezone.utc).date()


def _date_range(days):
    """Return (start, end) ISO date strings; end is today UTC (exclusive in CE)."""
    end = _utc_today()
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat()


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


def _check_tag_activated(ce_client):
    """Return True if ClusterID is an active cost allocation tag, False if not,
    None if the check could not be performed (permissions or network error)."""
    try:
        resp = ce_client.list_cost_allocation_tags(
            TagKeys=["ClusterID"], Type="UserDefined", MaxResults=1
        )
        tags = resp.get("CostAllocationTags", [])
        return any(
            t.get("TagKey") == "ClusterID" and t.get("Status") == "Active"
            for t in tags
        )
    except ClientError as e:
        if e.response["Error"]["Code"] in ("AccessDeniedException", "AuthFailure"):
            return None
        return None
    except BotoCoreError:
        return None


def _get_cluster_cost(ce_client, cluster_name, start, end):
    """Query CE for total UnblendedCost tagged ClusterID=cluster_name.

    Follows NextPageToken to handle ranges spanning >12 months.
    Returns (total_usd: float, error: str|None).
    """
    total = 0.0
    next_token = None
    while True:
        kwargs = dict(
            TimePeriod={"Start": start, "End": end},
            Granularity="MONTHLY",
            Filter={"Tags": {"Key": "ClusterID", "Values": [cluster_name]}},
            Metrics=["UnblendedCost"],
        )
        if next_token:
            kwargs["NextPageToken"] = next_token
        try:
            resp = ce_client.get_cost_and_usage(**kwargs)
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code in ("AccessDeniedException", "AuthFailure"):
                return None, "unavailable (needs ce:GetCostAndUsage)"
            return None, f"CE error: {code}"
        except BotoCoreError as e:
            return None, f"network/credential error: {e}"

        for period in resp.get("ResultsByTime", []):
            amount = (
                period.get("Total", {})
                .get("UnblendedCost", {})
                .get("Amount", "0")
            )
            try:
                total += float(amount)
            except ValueError:
                pass

        next_token = resp.get("NextPageToken")
        if not next_token:
            break

    return total, None


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

    if args.days < 1 or args.days > 365:
        sys.exit("ERROR: --days must be between 1 and 365")

    if args.cluster_name:
        _validate_cluster_name(args.cluster_name)
        cluster_names = [args.cluster_name]
    else:
        cluster_names = _enumerate_cluster_names()
        if not cluster_names:
            sys.exit("No clusters found in active_clusters/")

    start, end = _date_range(args.days)
    ce_client = boto3.client("ce", region_name="us-east-1")

    # Pre-flight: verify ClusterID tag is activated as a cost allocation tag.
    tag_active = _check_tag_activated(ce_client)
    if tag_active is False:
        print(
            "WARNING: 'ClusterID' is not an active cost allocation tag — "
            "all results will show $0.00.\n"
            "Activate it at: AWS Console → Billing → Cost allocation tags "
            "→ User-defined tags.\n"
        )
    elif tag_active is None:
        print(
            "NOTE: could not verify 'ClusterID' tag activation "
            "(needs ce:ListCostAllocationTags). Results may show $0.00 "
            "if the tag is not active.\n"
        )

    rows = []
    tag_warning_shown = False

    for name in cluster_names:
        rec = _read_cluster_record(name, _repo_root)
        owner = _safe(rec["cluster_owner"]) if rec else "unknown"
        region = _safe(rec["region"]) if rec else "unknown"

        if args.owner and owner != args.owner:
            continue

        total, err = _get_cluster_cost(ce_client, name, start, end)

        if err:
            cost_str = f"unavailable — {err}"
        elif total == 0.0:
            cost_str = "$0.00 *"
            tag_warning_shown = True
        else:
            cost_str = f"${total:.2f}"

        rows.append((name, owner, region, cost_str))

    period = f"{start} – {end}"

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
