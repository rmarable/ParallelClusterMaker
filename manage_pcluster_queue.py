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
import json
import subprocess
import time
from datetime import datetime

sys.path.insert(0, _src_dir)
from pcluster_queue_editor import (
    _load_cluster_config,
    _write_cluster_config,
    _validate_queue_name,
    _validate_instance_types,
    _get_subnet_ids,
    _get_custom_actions,
    _get_additional_iam_policies,
    _get_root_volume_encrypted,
    _gdr_capable_types,
    _check_queue_arch_matches_cluster,
    _print_update_reminder,
    _recovery_guidance,
    _make_yaml,
    COMPUTE_RESOURCE_SUFFIX,
)
from pcluster_aux_data import is_gpu_instance
from pcluster_core import _validate_region


_PCLUSTER_BIN = os.path.join(_repo_root, ".venv", "bin", "pcluster")
_POLL_INTERVAL = 30
_POLL_TIMEOUT = 90  # 90 × 30s = 45 min


def _ts():
    return datetime.now().strftime("%H:%M:%S")


def _check_pcluster():
    if not os.path.isfile(_PCLUSTER_BIN):
        sys.exit(f"ERROR: 'pcluster' not found at {_PCLUSTER_BIN} — ensure aws-parallelcluster is installed in .venv")


def _run_pcluster(subcmd_args, region):
    cmd = [_PCLUSTER_BIN] + subcmd_args + ["--region", region]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        sys.exit(f"ERROR: 'pcluster' not found at {_PCLUSTER_BIN}")
    except subprocess.TimeoutExpired:
        sys.exit("ERROR: pcluster command timed out after 120 s")
    if result.returncode != 0:
        sys.exit(f"ERROR: pcluster exited {result.returncode}:\n{result.stderr.strip() or result.stdout.strip()}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        sys.exit(f"ERROR: unexpected pcluster output:\n{result.stdout.strip()}")


def _get_fleet_status(cluster_name, region):
    data = _run_pcluster(["describe-cluster", "--cluster-name", cluster_name], region)
    return data.get("computeFleetStatus", "UNKNOWN")


def _poll_fleet(cluster_name, region, target, label):
    # Ctrl-C is caught around the whole loop: the describe-cluster subprocess is
    # where most of the wall clock goes, and an interrupt landing there used to
    # escape as a bare traceback with no note that AWS was still working.
    try:
        for i in range(_POLL_TIMEOUT):
            status = _get_fleet_status(cluster_name, region)
            print(f"  [{_ts()}] computeFleetStatus: {status}")
            if status == target:
                return
            if status == "PROTECTED":
                sys.exit(
                    "ERROR: compute fleet is in PROTECTED state — Slurm has locked the fleet due to repeated failures.\n"
                    "Investigate node failures before retrying."
                )
            if i == _POLL_TIMEOUT - 1:
                sys.exit(
                    f"ERROR: timed out after {_POLL_TIMEOUT * _POLL_INTERVAL // 60} min waiting for {label}.\n"
                    f"Check status with: pcluster describe-cluster --cluster-name {cluster_name} --region {region}"
                )
            time.sleep(_POLL_INTERVAL)
    except KeyboardInterrupt:
        print(
            f"\nInterrupted. The fleet operation is still running in AWS.\n"
            f"Check status with: pcluster describe-cluster --cluster-name {cluster_name} --region {region}"
        )
        sys.exit(1)


def _poll_cluster_update(cluster_name, region):
    _FAIL_STATES = {
        "UPDATE_FAILED", "UPDATE_ROLLBACK_IN_PROGRESS",
        "UPDATE_ROLLBACK_COMPLETE", "UPDATE_ROLLBACK_FAILED",
    }
    try:
        for i in range(_POLL_TIMEOUT):
            data = _run_pcluster(["describe-cluster", "--cluster-name", cluster_name], region)
            cs = data.get("clusterStatus", "UNKNOWN")
            cfs = data.get("cloudFormationStackStatus", "UNKNOWN")
            print(f"  [{_ts()}] clusterStatus: {cs}  cloudFormationStackStatus: {cfs}")
            if cs == "UPDATE_COMPLETE" and cfs == "UPDATE_COMPLETE":
                return
            if cs in _FAIL_STATES or cfs in _FAIL_STATES:
                sys.exit(
                    f"ERROR: cluster update failed (clusterStatus={cs}, cloudFormationStackStatus={cfs}).\n"
                    f"Check CloudFormation events for details."
                )
            if i == _POLL_TIMEOUT - 1:
                sys.exit(
                    f"ERROR: timed out after {_POLL_TIMEOUT * _POLL_INTERVAL // 60} min waiting for UPDATE_COMPLETE.\n"
                    f"Check status with: pcluster describe-cluster --cluster-name {cluster_name} --region {region}"
                )
            time.sleep(_POLL_INTERVAL)
    except KeyboardInterrupt:
        print(
            f"\nInterrupted. The cluster update is still running in AWS.\n"
            f"Check status with: pcluster describe-cluster --cluster-name {cluster_name} --region {region}"
        )
        sys.exit(1)


def _apply_with_wait(cluster_name, region, config_path):
    _check_pcluster()
    _validate_region(region)
    print(
        "\n*** WARNING: this operation can take up to 30 minutes and should not be interrupted.\n"
        "    Run inside screen or tmux if there is any risk of losing your terminal session. ***\n"
    )

    # Step 1 — stop the fleet unless already stopped/stopping
    status = _get_fleet_status(cluster_name, region)
    print(f"[{_ts()}] Current computeFleetStatus: {status}")
    if status == "PROTECTED":
        sys.exit(
            "ERROR: compute fleet is in PROTECTED state — investigate node failures before retrying."
        )
    if status not in ("STOPPED", "STOP_REQUESTED", "STOPPING", "DISABLED"):
        print(f"[{_ts()}] Requesting fleet stop...")
        _run_pcluster(
            ["update-compute-fleet", "--cluster-name", cluster_name, "--status", "STOP_REQUESTED"],
            region,
        )
    else:
        print(f"[{_ts()}] Fleet already stopping/stopped — skipping stop request.")

    # Step 2 — poll until STOPPED
    print(f"[{_ts()}] Waiting for fleet to reach STOPPED...")
    _poll_fleet(cluster_name, region, "STOPPED", "fleet stop")

    # Steps 3-6 run with the fleet already stopped. Any failure from here on
    # leaves the cluster with no compute capacity, so every exit path has to
    # tell the operator how to get back to RUNNING.
    try:
        # Step 3 — apply config
        print(f"[{_ts()}] Applying updated cluster configuration...")
        result = _run_pcluster(
            ["update-cluster", "--cluster-name", cluster_name,
             "--cluster-configuration", config_path],
            region,
        )
        print(json.dumps(result, indent=2))

        # Step 4 — poll until UPDATE_COMPLETE
        print(f"[{_ts()}] Waiting for cluster update to complete...")
        _poll_cluster_update(cluster_name, region)
    except SystemExit as _e:
        if _e.code not in (0, None):
            print(_recovery_guidance(cluster_name, region, config_path, "update"))
        raise

    try:
        # Step 5 — restart fleet
        print(f"[{_ts()}] Restarting compute fleet...")
        _run_pcluster(
            ["update-compute-fleet", "--cluster-name", cluster_name, "--status", "START_REQUESTED"],
            region,
        )

        # Step 6 — poll until RUNNING
        print(f"[{_ts()}] Waiting for fleet to reach RUNNING...")
        _poll_fleet(cluster_name, region, "RUNNING", "fleet start")
    except SystemExit as _e:
        if _e.code not in (0, None):
            print(_recovery_guidance(cluster_name, region, config_path, "start"))
        raise
    print(f"[{_ts()}] Compute fleet is RUNNING. Done.")


def _do_list(cluster_name):
    config, _ = _load_cluster_config(cluster_name)
    queues = config.get("Scheduling", {}).get("SlurmQueues", [])
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
        name = q.get("Name", "")
        capacity = q.get("CapacityType", "")
        cr_list = q.get("ComputeResources", [])
        all_types = []
        min_count = 0
        max_count = 0
        for cr in cr_list:
            instances = cr.get("Instances", [])
            all_types.extend(i.get("InstanceType", "") for i in instances)
            min_count += cr.get("MinCount", 0)
            max_count += cr.get("MaxCount", 0)
        queue_type = "gpu" if any(is_gpu_instance(t) for t in all_types) else "compute"
        types_str = ", ".join(all_types)
        print(
            f"{name:<{C1}}  {queue_type:<{C2}}  {capacity:<{C3}}  "
            f"{min_count:^{C4}}  {max_count:^{C5}}  {types_str}"
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

    # Month-day-hour-minute, not %Y%m%d-%H%M: "compute-20260725-1430" is 21
    # chars, so the derived "-resource" name overflowed PCluster's 25-char
    # limit and every auto-named queue failed at update-cluster time.
    ts = datetime.now().strftime("%m%d%H%M")
    queue_name = args.queue_name or f"{args.queue_type}-{ts}"
    _validate_queue_name(queue_name)

    require_gpu = args.queue_type == "gpu"
    instance_types = _validate_instance_types(args.ec2_instance_type, require_gpu)

    if require_gpu and _gdr_capable_types(instance_types):
        print("*** INFO ***")
        print("  One or more instance types (p4d/p4de/p5) support EFA GPUDirect RDMA (GDR).")
        print("  GDR is not enabled automatically. To enable, add to the queue stanza manually:")
        print("    Efa:")
        print("      Enabled: true")
        print("      GdrSupport: true")

    config, config_path = _load_cluster_config(cluster_name)
    queues = config["Scheduling"]["SlurmQueues"]

    _check_queue_arch_matches_cluster(config, instance_types)

    existing_names = [q.get("Name") for q in queues]
    if queue_name in existing_names:
        sys.exit(f"ERROR: queue '{queue_name}' already exists in this cluster config")

    subnet_ids = _get_subnet_ids(queues, prefer_gpu=require_gpu)
    custom_actions = _get_custom_actions(queues)
    additional_iam = _get_additional_iam_policies(queues)
    root_volume_encrypted = _get_root_volume_encrypted(queues)
    region = config["Region"]

    capacity_type = "SPOT" if args.capacity_type == "spot" else "ONDEMAND"
    min_count = args.initial_size if args.maintain_initial_size == "true" else 0

    root_vol_lines = [
        f"        VolumeType: {args.root_volume_type}",
        f"        Size: {args.root_volume_size}",
        f"        Encrypted: {str(root_volume_encrypted).lower()}",
    ]
    if args.root_volume_type in ("gp3", "io1", "io2"):
        root_vol_lines.append(f"        Iops: {args.root_volume_iops}")
    if args.root_volume_type == "gp3":
        root_vol_lines.append(f"        Throughput: {args.root_volume_throughput}")
    root_vol_block = "\n".join(root_vol_lines)

    instances_block = "\n".join(
        f"        - InstanceType: {t}" for t in instance_types
    )

    subnet_ids_block = "\n".join(f"    - {s}" for s in subnet_ids)

    stanza_yaml = f"""\
Name: {queue_name}
CapacityType: {capacity_type}
Networking:
  SubnetIds:
{subnet_ids_block}
ComputeSettings:
  LocalStorage:
    RootVolume:
{root_vol_block}
ComputeResources:
  - Name: {queue_name}{COMPUTE_RESOURCE_SUFFIX}
    Instances:
{instances_block}
    MinCount: {min_count}
    MaxCount: {args.max_size}
    DisableSimultaneousMultithreading: false
"""
    if additional_iam is not None:
        stanza_yaml += "Iam:\n  AdditionalIamPolicies:\n"
        yaml_obj_iam = _make_yaml()
        from io import StringIO as _StringIO
        buf_iam = _StringIO()
        yaml_obj_iam.dump(additional_iam, buf_iam)
        for line in buf_iam.getvalue().splitlines():
            stanza_yaml += f"    {line}\n"
    if custom_actions is not None:
        stanza_yaml += "CustomActions:\n"
        yaml_obj2 = _make_yaml()
        from io import StringIO as _StringIO
        buf2 = _StringIO()
        yaml_obj2.dump(custom_actions, buf2)
        for line in buf2.getvalue().splitlines():
            stanza_yaml += f"  {line}\n"

    new_queue = _make_yaml().load(stanza_yaml)
    queues.append(new_queue)
    _write_cluster_config(config_path, config)
    if args.wait:
        _apply_with_wait(cluster_name, region, config_path)
    else:
        _print_update_reminder(cluster_name, region, queue_name, "added to")


def _do_remove(cluster_name, args):
    if not args.queue_name:
        sys.exit("ERROR: -Q/--queue-name is required for remove")

    config, config_path = _load_cluster_config(cluster_name)
    queues = config["Scheduling"]["SlurmQueues"]
    region = config["Region"]

    existing_names = [q.get("Name") for q in queues]
    if args.queue_name not in existing_names:
        sys.exit(
            f"ERROR: queue '{args.queue_name}' not found.\n"
            f"Available queues: {', '.join(existing_names)}"
        )

    if len(queues) == 1:
        sys.exit("ERROR: Cannot remove the last queue. A cluster must have at least one queue.")

    filtered = [q for q in queues if q.get("Name") != args.queue_name]
    config["Scheduling"]["SlurmQueues"] = filtered
    _write_cluster_config(config_path, config)
    if args.wait:
        _apply_with_wait(cluster_name, region, config_path)
    else:
        _print_update_reminder(cluster_name, region, args.queue_name, "removed from")


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
