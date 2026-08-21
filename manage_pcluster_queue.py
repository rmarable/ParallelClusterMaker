#!/usr/bin/env python

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
    _validate_region,
    _print_update_reminder,
    core_list_queues,
    core_add_queue,
    core_remove_queue,
    core_apply_queue_config,
)

_PCLUSTER_BIN = os.path.join(_repo_root, ".venv", "bin", "pcluster")


def _run_apply_with_wait(cluster_name, region, config_path):
    _validate_region(region)
    cluster_record = ClusterRecord.unknown(cluster_name)
    try:
        core_apply_queue_config(
            cluster_record=cluster_record, config_path=config_path,
            region=region, pcluster_bin=_PCLUSTER_BIN,
        )
    except PClusterMakerError as e:
        sys.exit(str(e))


def _do_list(cluster_name):
    try:
        queues = core_list_queues(cluster_name=cluster_name, repo_root=_repo_root)
    except PClusterMakerError as e:
        sys.exit(str(e))
    if not queues:
        print("No queues found in config.")
        return

    C1, C2, C3, C4, C5 = 12, 8, 8, 3, 3
    header = (
        f"{'Queue Name':<{C1}}  {'Type':<{C2}}  {'Capacity':<{C3}}  "
        f"{'Min':^{C4}}  {'Max':^{C5}}  Instance Types"
    )
    sep = (
        f"{'':->{ C1}}  {'':->{ C2}}  {'':->{ C3}}  "
        f"{'':->{ C4}}  {'':->{ C5}}  {'':->14}"
    )
    print(header)
    print(sep)
    for q in queues:
        types_str = ", ".join(q.instance_types)
        print(
            f"{q.name:<{C1}}  {q.queue_type:<{C2}}  {q.capacity_type:<{C3}}  "
            f"{q.min_count:^{C4}}  {q.max_count:^{C5}}  {types_str}"
        )


def _do_add(cluster_name, args):
    if not args.ec2_instance_type:
        sys.exit("ERROR: -E/--ec2-type is required for add")
    if args.initial_size < 0:
        sys.exit("ERROR: --initial_size must be >= 0")
    if args.max_size < 1:
        sys.exit("ERROR: --max_size must be >= 1")
    if args.initial_size > args.max_size:
        sys.exit("ERROR: --initial_size cannot exceed --max_size")

    try:
        result = core_add_queue(
            cluster_name=cluster_name,
            repo_root=_repo_root,
            queue_type=args.queue_type,
            ec2_instance_type=args.ec2_instance_type,
            queue_name=args.queue_name,
            capacity_type=args.capacity_type,
            initial_size=args.initial_size,
            max_size=args.max_size,
            maintain_initial_size=(args.maintain_initial_size == "true"),
            root_volume_size=args.root_volume_size,
            root_volume_type=args.root_volume_type,
            root_volume_iops=args.root_volume_iops,
            root_volume_throughput=args.root_volume_throughput,
        )
    except PClusterMakerError as e:
        sys.exit(str(e))

    if args.wait:
        _run_apply_with_wait(cluster_name, result.region, result.config_path)
    else:
        _print_update_reminder(cluster_name, result.region, result.queue_name, "added to")


def _do_remove(cluster_name, args):
    if not args.queue_name:
        sys.exit("ERROR: -Q/--queue-name is required for remove")

    try:
        result = core_remove_queue(
            cluster_name=cluster_name, repo_root=_repo_root, queue_name=args.queue_name,
        )
    except PClusterMakerError as e:
        sys.exit(str(e))

    if args.wait:
        _run_apply_with_wait(cluster_name, result.region, result.config_path)
    else:
        _print_update_reminder(cluster_name, result.region, result.queue_name, "removed from")


def main():
    parser = argparse.ArgumentParser(
        description="Add, remove, or list Slurm queues in a live ParallelCluster v3 config."
    )
    parser.add_argument("-N", "--cluster_name", required=True, help="Cluster name")
    parser.add_argument(
        "-A", "--action", required=True, choices=["add", "remove", "list"],
        help="Action to perform"
    )
    parser.add_argument(
        "-T", "--type", dest="queue_type", default=None, choices=["compute", "gpu"],
        help="Queue type (required for add; ignored for remove and list)"
    )
    parser.add_argument(
        "-Q", "--queue-name", dest="queue_name", default=None,
        help="Queue name (optional for add, required for remove)"
    )
    parser.add_argument(
        "-C", "--capacity", dest="capacity_type", choices=["spot", "ondemand"], default="spot",
        help="Capacity type (default: spot)"
    )
    parser.add_argument(
        "-E", "--ec2-type", dest="ec2_instance_type", default=None,
        help="Comma-separated instance types"
    )
    parser.add_argument("-I", "--initial_size", type=int, default=2, help="Initial queue size (default: 2)")
    parser.add_argument("-M", "--max_size", type=int, default=8, help="Maximum queue size (default: 8)")
    parser.add_argument(
        "--maintain_initial_size", choices=["true", "false"], default="false",
        help="Keep MinCount equal to initial_size (default: false)"
    )
    parser.add_argument("--root_volume_size", type=int, default=250, help="Root volume size in GiB (default: 250)")
    parser.add_argument(
        "--root_volume_type", choices=["gp2", "gp3", "io1", "io2", "st1"], default="gp3",
        help="Root volume type (default: gp3)"
    )
    parser.add_argument("--root_volume_iops", type=int, default=3000, help="Root volume IOPS (default: 3000)")
    parser.add_argument("--root_volume_throughput", type=int, default=125, help="Root volume throughput MiB/s (default: 125)")
    parser.add_argument(
        "-W", "--wait", action="store_true", default=False,
        help=(
            "After writing the config, automatically stop the fleet, apply the update, and restart "
            "the fleet, polling every 30s until complete. Without this flag, the required commands "
            "are printed instead. (default: false)"
        ),
    )

    args = parser.parse_args()
    cluster_name = args.cluster_name

    # remove keys off --queue-name alone; _do_remove never reads queue_type.
    if args.action == "add" and not args.queue_type:
        parser.error("-T/--type is required for 'add'")

    if args.action == "list":
        _do_list(cluster_name)
    elif args.action == "add":
        _do_add(cluster_name, args)
    elif args.action == "remove":
        _do_remove(cluster_name, args)


if __name__ == "__main__":
    main()
