import copy
import os
import re
import shutil
import sys
import tempfile

from ruamel.yaml import YAML

from pcluster_aux_data import (
    ec2_instances_full_list,
    is_arm_instance,
    is_gpu_instance,
    needs_efa_gdr,
    parse_instance_type_list,
)


def _make_yaml():
    """Return a configured ruamel YAML instance."""
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    # ruamel's default line width is 80, so round-tripping reflows long
    # untouched lines (post-install script URLs, IAM policy ARNs) into
    # continuations. That is a spurious diff at best and, for a value PCluster
    # reads as a single token, a corrupted config at worst. Effectively disable
    # wrapping so only the queue we actually edited changes.
    yaml.width = 4096
    return yaml


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _config_path(cluster_name):
    from pcluster_core import _validate_cluster_name
    _validate_cluster_name(cluster_name)
    return os.path.join(
        _repo_root(), "active_clusters", cluster_name, f"config.{cluster_name}"
    )


def _load_cluster_config(cluster_name):
    path = _config_path(cluster_name)
    if not os.path.isfile(path):
        sys.exit(f"ERROR: cluster config not found: {path}")
    try:
        with open(path) as fh:
            config = _make_yaml().load(fh)
    except Exception as exc:
        sys.exit(f"ERROR: failed to parse {path}: {exc}")
    return config, path


def _write_cluster_config(config_path, config_dict):
    # Keep one generation of the previous config. The only copy of a live
    # cluster's configuration lives at this path, and a rewrite that produces a
    # structurally valid but wrong document (or a queue removal the operator
    # then wants back) is otherwise unrecoverable without rebuilding.
    if os.path.isfile(config_path):
        shutil.copy2(config_path, config_path + ".bak")
    tmp_path = None
    try:
        dir_ = os.path.dirname(config_path)
        with tempfile.NamedTemporaryFile(
            mode="w", dir=dir_, delete=False, suffix=".tmp"
        ) as tmp:
            tmp_path = tmp.name
            from io import StringIO
            buf = StringIO()
            _make_yaml().dump(config_dict, buf)
            result = buf.getvalue()
            result = re.sub(r'\n(SharedStorage:)', r'\n\n\1', result)
            result = re.sub(r'\n{3,}(SharedStorage:)', r'\n\n\1', result)
            result = re.sub(r'\n+(\n  - Name:)', r'\1', result)
            tmp.write(result)
        os.replace(tmp_path, config_path)
    except Exception:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


# PCluster's NameValidator (NAME_MAX_LENGTH = 25) is registered on both
# BaseQueue and BaseComputeResource, and _do_add derives the compute resource
# name as f"{queue_name}-resource". A 25-char queue name therefore yields a
# 34-char resource name that pcluster update-cluster rejects — after the compute
# fleet has already been stopped under -W. Bound the queue name by the suffix.
COMPUTE_RESOURCE_SUFFIX = "-resource"
PCLUSTER_NAME_MAX_LENGTH = 25
QUEUE_NAME_MAX_LENGTH = PCLUSTER_NAME_MAX_LENGTH - len(COMPUTE_RESOURCE_SUFFIX)


def _validate_queue_name(name):
    if len(name) > QUEUE_NAME_MAX_LENGTH:
        sys.exit(
            f"ERROR: queue name '{name}' is {len(name)} chars; the limit is "
            f"{QUEUE_NAME_MAX_LENGTH} because the derived compute resource name "
            f"'{name}{COMPUTE_RESOURCE_SUFFIX}' must fit PCluster's "
            f"{PCLUSTER_NAME_MAX_LENGTH}-character name limit"
        )
    pattern = r'^[a-z][a-z0-9]?$|^[a-z][a-z0-9-]{0,23}[a-z0-9]$'
    if not re.match(pattern, name):
        sys.exit(
            f"ERROR: invalid queue name '{name}'. Must start with a lowercase letter, "
            "contain only lowercase letters, digits, and hyphens, and end with a letter or digit."
        )
    if '--' in name:
        sys.exit(f"ERROR: queue name '{name}' contains consecutive hyphens")
    # PCluster's NameValidator hard-rejects this exact name on queues and
    # compute resources alike.
    if name == "default":
        sys.exit(
            "ERROR: 'default' is a reserved name in AWS ParallelCluster and cannot "
            "be used as a queue name"
        )


# Every AWS EC2 instance type is exactly <family>.<size> — one dot,
# each half alphanumeric with optional internal hyphens (e.g. u-6tb1.56xlarge,
# c8g.metal-24xl). Reject anything outside that character class rather than
# whitelisting specific hyphen positions: AWS adds new naming shapes over
# time (hyphenated families, hyphenated metal sizes) and a position-specific
# pattern silently falls behind. This still rejects every shell metacharacter,
# space, colon, and newline — the actual injection surface — without needing
# to track which naming shape AWS ships next.
_INSTANCE_TYPE_RE = re.compile(r'^[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?\.[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?$')


def _validate_instance_types(raw, require_gpu):
    types = parse_instance_type_list(raw)
    if not types:
        sys.exit("ERROR: no instance types provided")
    for itype in types:
        if not _INSTANCE_TYPE_RE.match(itype):
            sys.exit(
                f"ERROR: '{itype}' is not a valid EC2 instance type "
                "(expected format: <family>.<size>, e.g. c5.2xlarge)"
            )
    for itype in types:
        if itype not in ec2_instances_full_list:
            print(f"WARNING: '{itype}' not found in known instance type list (list may be stale)")
        if require_gpu and not is_gpu_instance(itype):
            sys.exit(f"ERROR: instance type '{itype}' is not a GPU instance but --gpu was specified")
        if not require_gpu and is_gpu_instance(itype):
            sys.exit(f"ERROR: instance type '{itype}' is a GPU instance but --gpu was not specified")
    arm_types = [t for t in types if is_arm_instance(t)]
    x86_types = [t for t in types if not is_arm_instance(t)]
    if arm_types and x86_types:
        sys.exit(
            f"ERROR: mixed CPU architectures in instance list.\n"
            f"  ARM: {arm_types}\n"
            f"  x86: {x86_types}"
        )
    return types


def _gdr_capable_types(instance_types):
    """Return the subset of instance_types that support EFA GPUDirect RDMA."""
    return [t for t in instance_types if needs_efa_gdr(t)]


def _cluster_arch(config):
    """Return 'arm64', 'x86_64', or None for the head node and existing queues."""
    types = []
    head = config.get("HeadNode", {}).get("InstanceType")
    if head:
        types.append(head)
    for q in config.get("Scheduling", {}).get("SlurmQueues", []) or []:
        for cr in q.get("ComputeResources", []) or []:
            for inst in cr.get("Instances", []) or []:
                itype = inst.get("InstanceType")
                if itype:
                    types.append(itype)
    if not types:
        return None
    archs = {"arm64" if is_arm_instance(t) else "x86_64" for t in types}
    return archs.pop() if len(archs) == 1 else None


def _check_queue_arch_matches_cluster(config, instance_types):
    """Reject a new queue whose architecture differs from the running cluster.

    make_pcluster.py hard-fails on mixed head/compute architecture at build
    time, but nothing re-checked it here, so `add` could bolt a Graviton queue
    onto an x86 cluster (or vice versa). PCluster accepts that config; the
    breakage surfaces later and indirectly. Shared software built on the head
    node -- Spack packages, the benchmark suite, anything compiled with
    -march=native -- cannot execute on a foreign-architecture node, and the
    base AMI for the new queue is chosen from the cluster's single Os setting,
    which is architecture-specific.
    """
    cluster_arch = _cluster_arch(config)
    if cluster_arch is None:
        return
    new_arch = "arm64" if is_arm_instance(instance_types[0]) else "x86_64"
    if new_arch != cluster_arch:
        sys.exit(
            f"ERROR: new queue architecture does not match this cluster.\n"
            f"  Cluster (head node and existing queues): {cluster_arch}\n"
            f"  Requested instance types: {', '.join(instance_types)} ({new_arch})\n"
            f"  A cluster runs one base OS image, which is architecture-specific,\n"
            f"  and software compiled on the head node cannot run on a foreign\n"
            f"  architecture. Build a separate cluster for {new_arch} workloads."
        )


def _is_gpu_queue(q):
    for cr in q.get("ComputeResources", []):
        for inst in cr.get("Instances", []):
            if is_gpu_instance(inst.get("InstanceType", "")):
                return True
    return False


def _get_subnet_ids(queues, prefer_gpu=False):
    if not queues:
        sys.exit("ERROR: no queues found in cluster config")
    if prefer_gpu:
        for q in queues:
            if _is_gpu_queue(q):
                subnets = q.get("Networking", {}).get("SubnetIds")
                if subnets:
                    return subnets
    for q in queues:
        subnets = q.get("Networking", {}).get("SubnetIds")
        if subnets:
            return subnets
    sys.exit("ERROR: no SubnetIds found in any existing queue")


def _get_custom_actions(queues):
    for q in queues:
        if "CustomActions" in q:
            return copy.deepcopy(q["CustomActions"])
    return None


def _get_additional_iam_policies(queues):
    for q in queues:
        iam = q.get("Iam", {})
        if iam and "AdditionalIamPolicies" in iam:
            return copy.deepcopy(iam["AdditionalIamPolicies"])
    return None


def _get_root_volume_encrypted(queues):
    """Inherit the Encrypted setting from an existing queue's root volume.

    Defaults to False if no existing queue has a root volume with an
    explicit Encrypted value, matching PCluster's own default.
    """
    for q in queues:
        encrypted = (
            q.get("ComputeSettings", {})
            .get("LocalStorage", {})
            .get("RootVolume", {})
            .get("Encrypted")
        )
        if encrypted is not None:
            return bool(encrypted)
    return False


def _recovery_guidance(cluster_name, region, config_path, stage):
    """Return operator recovery steps for a failed -W (wait) queue update.

    stage is one of "update", "start". A failure after the fleet has been
    stopped leaves the cluster with zero compute capacity; without these steps
    the operator is told only that a step failed, not that jobs are queued
    behind a fleet nobody restarted.
    """
    backup = config_path + ".bak"
    lines = [
        "",
        "*** RECOVERY REQUIRED ***",
        "  The compute fleet was stopped before this step and has NOT been restarted.",
        "  No jobs will run until the fleet is back in RUNNING.",
        "",
    ]
    if stage == "update":
        lines += [
            "  1. Inspect what went wrong:",
            f"       pcluster describe-cluster --cluster-name {cluster_name} --region {region}",
            "",
            "  2. To roll the configuration back to the previous generation:",
            f"       cp {backup} {config_path}",
            f"       pcluster update-cluster --cluster-name {cluster_name} \\",
            f"         --cluster-configuration {config_path} --region {region}",
            "",
            "  3. Once the cluster reaches UPDATE_COMPLETE, restart the fleet:",
            f"       pcluster update-compute-fleet --cluster-name {cluster_name} \\",
            f"         --status START_REQUESTED --region {region}",
        ]
    else:
        lines += [
            "  1. Re-issue the fleet start:",
            f"       pcluster update-compute-fleet --cluster-name {cluster_name} \\",
            f"         --status START_REQUESTED --region {region}",
            "",
            "  2. Watch for RUNNING:",
            f"       pcluster describe-cluster --cluster-name {cluster_name} --region {region}",
        ]
    lines.append("")
    return "\n".join(lines)


def _print_update_reminder(cluster_name, region, queue_name, action):
    config_rel = f"active_clusters/{cluster_name}/config.{cluster_name}"
    print(f'\nQueue "{queue_name}" {action} in {config_rel}\n')
    print("To apply this change:\n")
    print(
        f"1. Stop the compute fleet:\n"
        f"     pcluster update-compute-fleet \\\n"
        f"       --cluster-name {cluster_name} \\\n"
        f"       --status STOP_REQUESTED \\\n"
        f"       --region {region}\n"
    )
    print(
        f"2. Wait for the fleet to reach STOPPED:\n"
        f"     pcluster describe-cluster \\\n"
        f"       --cluster-name {cluster_name} \\\n"
        f"       --region {region} | grep computeFleetStatus\n"
    )
    print(
        f"3. Apply the updated configuration:\n"
        f"     pcluster update-cluster \\\n"
        f"       --cluster-name {cluster_name} \\\n"
        f"       --cluster-configuration {config_rel} \\\n"
        f"       --region {region}\n"
    )
    print(
        f"4. Wait for the cluster update to complete (clusterStatus: UPDATE_COMPLETE,\n"
        f"   cloudFormationStackStatus: UPDATE_COMPLETE):\n"
        f"     pcluster describe-cluster \\\n"
        f"       --cluster-name {cluster_name} \\\n"
        f"       --region {region} | grep -E 'clusterStatus|cloudFormationStackStatus'\n"
    )
    print(
        f"5. Restart the compute fleet:\n"
        f"     pcluster update-compute-fleet \\\n"
        f"       --cluster-name {cluster_name} \\\n"
        f"       --status START_REQUESTED \\\n"
        f"       --region {region}"
    )
