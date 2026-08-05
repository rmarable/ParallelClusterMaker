# ParallelClusterMaker

This Open Source CLI toolkit automates creation and destruction of [AWS ParallelCluster v3](https://github.com/aws/aws-parallelcluster) stacks.  It lets researchers and engineers stand up a working HPC cluster on AWS without deep infrastructure expertise, and it cuts the support load on the DevOps teams backing them.

This codebase was co-written with [Claude Code](https://claude.ai/code) (Anthropic).

---

## Installation

See [INSTALL.md](INSTALL.md) for prerequisites, AWS account setup (VPC tagging, IAM
permissions), and installation steps.

---

## Features

**Scheduling and compute** — see [Networking and Compute](#networking-and-compute)

- Slurm job scheduling
- Separate CPU and GPU queues — the GPU queue exists only when `--gpu_instance_type` is set
- Multi-instance-type queues via `--compute_instance_type` and `--gpu_instance_type`, each accepting a comma-separated list
- Separate instance types and EBS configurations for the head node, CPU queue, and GPU queue
- Spot capacity by default (`--cluster_type=spot`), with current market rates printed at build time
- EFA on supported instance types in both queues (`--enable_efa`), with EFA-GDR enabled automatically on p4d/p4de/p5
- Dynamic EFA instance type lookup at launch time, with a static fallback list
- Dynamic EC2 placement groups (`--placement_group`), applied to compute queues only
- Selective HyperThreading disable (`--hyperthreading=false`)
- Custom AMI support (`--custom_ami`)
- Eight base operating systems across x86_64 and Graviton (`--base_os`): `ubuntu2204`, `ubuntu2404`, `ubuntu2204arm`, `ubuntu2404arm`, `rhel9`, `rhel9arm`, `alinux2023`, `alinux2023arm`
- Dev / test / stage / prod operating levels (`--prod_level`)

**Shared storage** — see [Storage](#storage)

- Shared EBS at `/shared`, created unconditionally on every cluster
- EFS at `/efs` (`--enable_efs`), with optional encryption (`--efs_encryption`)
- FSx for Lustre at `/fsx` (`--enable_fsx`), with optional S3 hydration/dehydration (`--enable_fsx_hydration`)
- External NFS automount from a site filer (`--enable_external_nfs`) — Vast, NetApp, WekaIO, Qumulo, etc.
- Every filesystem, its mount point, and the resulting Spack install path named in the build summary and the SNS report

**Software environment** — see [Software Environment](#software-environment)

- Spack + Lmod for HPC software module management
- Optional benchmark suite (`--enable_hpc_benchmarks`): STREAM, OSU MPI, IOR, and HPCG — STREAM recompiles per microarchitecture, so a GPU-partition job measures the GPU node
- Optional Grafana/Prometheus monitoring stack (`--enable_monitoring`) via `aws-parallelcluster-monitoring` — Grafana dashboards, Prometheus, Slurm exporter, CloudWatch exporter

**Operations tooling**

- `list_pcluster.py` — table of every cluster tracked by this repo, with optional live CloudFormation status; see [Listing Clusters](#listing-clusters)
- `stop_pcluster.py` / `start_pcluster.py` — stop or start the compute fleet without touching the head node; see [Stopping and Starting the Compute Fleet](#stopping-and-starting-the-compute-fleet)
- `check_pcluster.py` — pass/fail health check; exits 0 only when every check passes; see [Checking Cluster Health](#checking-cluster-health)
- `diagnose_pcluster.py` — raw diagnostic dump: CloudWatch bootstrap logs, node states, job failures, log tails; see [Diagnosing a Cluster](#diagnosing-a-cluster)
- `cost_pcluster.py` — actual spend per cluster from AWS Cost Explorer; see [Cost Reporting](#cost-reporting)
- `grafana_tunnel.py` — open or close the Grafana SSH tunnel for a monitoring-enabled cluster; see [Monitoring](#monitoring)
- `rotate_cluster_key.py` — rotate the cluster SSH keypair without a rebuild; see [SSH Key Management](#ssh-key-management)
- `manage_pcluster_queue.py` — add, remove, or list Slurm queues on a live cluster; see [Managing Queues on a Running Cluster](#managing-queues-on-a-running-cluster)

**Security and lifecycle**

- SSH private key stored in AWS Secrets Manager at cluster creation, recoverable via `retrieve_ssh_key.<cluster>.sh`; see [SSH Key Management](#ssh-key-management)
- Resource tagging by owner, department, project, and operating level; see [Tagging](#tagging)
- SNS notifications on stack create and destroy
- Turbot environment support (`--turbot_account`)
- Hourly cost estimate in the build summary — on-demand and spot price ranges per queue from the AWS Pricing API, degrading to `unavailable` with a reason rather than crashing

---

## Defaults

All optional parameters have hardcoded defaults and can also be persisted in a YAML defaults file.  `pcluster_defaults.yml` is the template — **copy it to your own file before use**; do not pass the toolkit's copy directly as it is shared and may be overwritten by updates.  The most commonly referenced defaults:

| Parameter | Default |
|---|---|
| `base_os` | ubuntu2404 (hardcoded); `pcluster_defaults.yml` ships ubuntu2404arm to match the Graviton head node.  Valid: `ubuntu2204`, `ubuntu2404`, `ubuntu2204arm`, `ubuntu2404arm`, `rhel9`, `rhel9arm`, `alinux2023`, `alinux2023arm` |
| `scheduler` | slurm |
| `headnode_instance_type` | c8g.xlarge (required — no default fallback) |
| `compute_instance_type` | c8g.2xlarge, c7g.2xlarge, c6g.2xlarge (comma list; empty = no CPU queue) |
| `gpu_instance_type` | _(empty)_ — set to create a GPU queue (e.g. `p3.2xlarge,g5.2xlarge`) |
| `gpu_root_volume_size` | 250 GB (gp3) |
| `cluster_type` | spot |
| `initial_cpu_queue_size` | 2 |
| `max_cpu_queue_size` | 8 |
| `initial_gpu_queue_size` | 2 |
| `max_gpu_queue_size` | 8 |
| `maintain_cpu_initial_size` | false |
| `maintain_gpu_initial_size` | false |
| `scaledown_idletime` | 5 min (global — applies to both queues) |
| `headnode_root_volume_size` | 100 GB (gp3) |
| `compute_root_volume_size` | 250 GB (gp3) |
| `ebs_shared_volume_size` | 250 GB (gp3) |
| `fsx_size` | 1200 GB |
| `placement_group` | NONE |
| `hyperthreading` | true |
| `enable_monitoring` | false |
| `enable_hpc_benchmarks` | false |
| `enable_efa` | false |
| `enable_gpu` | derived from `gpu_instance_type` — not user-settable |
| `head_node_bootstrap_timeout` | 2100 s, raised automatically when `enable_efs` or `enable_fsx` is true |

Use `--use_defaults=FILE` to load values from your own defaults file; CLI arguments always take precedence.

```
# Copy the template and customize it for your cluster
cp pcluster_defaults.yml my-cluster_defaults.yml
# Pass it at runtime
./make_pcluster.py -N my-cluster -O rmarable -E rmarable@example.com -A us-east-1a \
    --use_defaults=my-cluster_defaults.yml
```

Name the file `<cluster_name>_defaults.yml` to keep cluster namespaces scoped.  `make_pcluster.py` detects a matching file that was not loaded and prints a `*** WARNING ***` suggesting the flag.  Loading `pcluster_defaults.yml` directly is allowed but also warns.

---

## Building a Cluster

```
./make_pcluster.py -N CLUSTER_NAME -O OWNER -E EMAIL -A AZ [options]
```

Required arguments:

| Flag | Description |
|---|---|
| `-N` | Cluster name (must start with a lowercase letter; lowercase letters, digits, hyphens only; no consecutive or trailing hyphens; max 27 characters) |
| `-O` | Owner username |
| `-E` | Owner email |
| `-A` | Availability zone (e.g. `us-east-1a`) — pass an AZ, not a region |

After a cluster finishes building, the summary includes an estimated hourly cost for the head node and each queue at maximum fleet size:

```
  Estimated hourly cost (max fleet, on-demand unless noted):
    Head node  (c8g.2xlarge × 1):          $0.319/hr
    CPU queue  (c8g.2xlarge × 8):          $2.552/hr  [~$1.093/hr spot]
    GPU queue  (p3.2xlarge × 4):           $12.240/hr
    Note: spot prices are current ask; actual cost may differ.
```

For multi-instance-type queues, a price range is shown (cheapest to most expensive type, all nodes at max count).  If the AWS Pricing API is unreachable or the operator policy lacks `pricing:GetProducts`, the affected lines report `unavailable` — the head node line carries the reason — instead of crashing the build.  Spot prices come from `ec2:DescribeSpotPriceHistory` using the most recent ask and appear only when `--cluster_type=spot`.

The summary also names every shared filesystem, where it is mounted, and where Spack and the shared package tree install.  Only the filesystems the cluster actually has appear — this example has all of them:

```
  Shared storage:
    /shared  EBS (gp3, 250 GB)
    /efs     EFS (bursting throughput)
    /fsx     FSx for Lustre (1200 GB)
             S3 import: s3://myImportBucket/data/in
             S3 export: s3://myExportBucket/data/out
             Hydrate:   /usr/local/bin/import-s3-to-lustre.sh
             Export:    /usr/local/bin/export-lustre-to-s3.sh
             Progress:  /usr/local/bin/check-lustre-export-progress.sh
    /nfs     external NFS (storage.domain.com)
    Spack and shared packages install under /fsx/pkg
```

The `pkg_dir` on the last line follows the storage precedence `fsx > efs > external NFS > shared EBS`, so it moves when a faster filesystem is present — see [Spack + Lmod](#spack--lmod).  The same block appears in the emailed SNS build report, which is the copy that outlives the terminal scrollback.

### Examples

Basic cluster in us-east-1a using all defaults:
```
./make_pcluster.py -N pcluster-test-01 -O rmarable -E rodney.marable@gmail.com -A us-east-1a
```

EFS with encryption:
```
./make_pcluster.py -A us-east-1a -O rmarable -E rodney.marable@gmail.com -N morpheus \
    --enable_efs=true --efs_encryption=true
```

Fixed-size compute pool tagged to the compbio department:
```
./make_pcluster.py -A eu-central-1a -O rmarable -E rodney.marable@gmail.com -N koolkeith \
    --initial_cpu_queue_size=4 --max_cpu_queue_size=125 --maintain_cpu_initial_size=true \
    --scaledown_idletime=30 --cluster_owner_department=compbio --project_id=polaroid \
    --compute_instance_type=c5.2xlarge
```

EFA-enabled single-node cluster with performance tests:
```
./make_pcluster.py -A us-east-1a -N rimshot -O rmarable -E rmarable@amazon.com \
    --compute_instance_type=c5n.18xlarge --initial_cpu_queue_size=1 \
    --maintain_cpu_initial_size=true --enable_efa=true --enable_hpc_benchmarks=true
```

FSx for Lustre with S3 hydration (7.2 TB, 5 GB chunk size).  Import and export must name the **same** bucket; the default `import` and `export` prefixes keep the two sides apart:
```
./make_pcluster.py -A us-west-2b -O rmarable -E rodney.marable@gmail.com -N louievega \
    --enable_fsx=true --fsx_size=7200 --enable_fsx_hydration=true \
    --fsx_s3_import_bucket=LouieVegaData --fsx_s3_export_bucket=LouieVegaData \
    --fsx_chunk_size=5000
```

Large GPU cluster with 3.6 PB Lustre, tagged for production:
```
./make_pcluster.py -A us-east-1a -O rmarable -E rodney.marable@gmail.com -N gilgamesh \
    --base_os=ubuntu2204 --headnode_instance_type=r5.xlarge \
    --gpu_instance_type=p3.16xlarge --enable_fsx=true --fsx_size=3600000 \
    --enable_fsx_hydration=true --fsx_s3_import_bucket=GilgameshData \
    --fsx_s3_import_path=src/ --fsx_s3_export_bucket=GilgameshData \
    --fsx_s3_export_path=output/ --prod_level=prod --max_gpu_queue_size=256
```

Mixed CPU + GPU cluster (separate queues):
```
./make_pcluster.py -A us-east-1a -O rmarable -E rodney.marable@gmail.com -N bifrost \
    --base_os=ubuntu2404 --headnode_instance_type=c5.xlarge \
    --compute_instance_type=c5.2xlarge,c5.4xlarge \
    --gpu_instance_type=g5.2xlarge,g5.4xlarge
```

RHEL 9 cluster (login user is `ec2-user`, not `ubuntu`):
```
./make_pcluster.py -A us-east-2a -O rmarable -E rodney.marable@gmail.com -N enkidu \
    --base_os=rhel9 --headnode_instance_type=c5.xlarge \
    --compute_instance_type=c5.2xlarge
```

Amazon Linux 2023 cluster on Graviton (login user is `ec2-user`):
```
./make_pcluster.py -A us-east-2a -O rmarable -E rodney.marable@gmail.com -N ninlil \
    --base_os=alinux2023arm --headnode_instance_type=c8g.xlarge \
    --compute_instance_type=c8g.2xlarge
```

Building from a custom AMI (must match base_os):
```
./make_pcluster.py -N starscream -O rmarable -E rodney.marable@gmail.com -A us-west-2a \
    --enable_fsx=true --custom_ami=ami-123456789abc --base_os=ubuntu2204
```

A new stack typically takes approximately 30–35 minutes to build.  Two measured `us-east-2` builds of the same cluster shape (`c5.xlarge` head node, two queues, `ubuntu2404`): **34m 24s** with a 1200 GB FSx for Lustre filesystem, and **21m 14s** with EFS instead.  Actual time depends on region, instance type availability, and which shared filesystems are enabled — FSx dominates when it is on, because its provisioning sits on the head node's critical path (see Troubleshooting).

---

## Accessing a Cluster

```
./access_cluster.py -N CLUSTER_NAME
```

Example:
```
./access_cluster.py -N pcluster-test-01
Connecting to head node of pcluster-test-01...
```

---

## Deleting a Cluster

```
./kill_pcluster.py -N CLUSTER_NAME -O OWNER -A AZ
```

Teardown takes 5–10 minutes.  The cluster's EFS and FSx filesystems are deleted by CloudFormation, which ParallelCluster configures with a `Delete` deletion policy at *creation* time — teardown cannot preserve them.  The cluster's own S3 bucket (`parallelclustermaker-<serial>`) is the one resource teardown controls, and it holds the rendered cluster config and the bootstrap scripts.  Benchmark results do **not** live there — they are synced to the long-lived `parallelclustermaker-results-<account-id>-<region>` bucket, which teardown never touches — so keeping the per-cluster bucket is only useful for inspecting what a build actually deployed:

```
./kill_pcluster.py -N pcluster-test-01 -O rmarable -A us-east-1a \
    --delete_s3_bucketname=false
```

Teardown is always manual and at your discretion — nothing in this toolkit schedules a cluster's destruction.  Idle compute cost is bounded instead by `scaledown_idletime`: ParallelCluster terminates compute nodes that sit idle longer than that, scaling the fleet to zero on its own.  A head node left running still bills, so run `kill_pcluster.py` when you are finished with a cluster.

### When cleanup leaves something behind

Teardown deletes ten kinds of resource after the CloudFormation stack is gone: the S3 bucket, the FSx hydration policy, the Grafana SSM parameter, the Secrets Manager secret, four managed IAM policies, the monitoring policy, the IAM role and its instance profile, the external NFS security group, and the SNS topic.  Each step tolerates its own failure so that one AWS error cannot abandon the other nine — but every ignored failure is collected and reported, and teardown then exits non-zero:

```
=================================================================

Initiated shutdown: 2026-07-26 @ 01:33:12
Completed shutdown: 2026-07-26 @ 01:41:48

Cluster osiris has been deleted, but 2 cleanup step(s) FAILED.
The following resources are still in the account and must be
removed by hand -- re-running kill_pcluster.py will not retry them
once osiris.serial has been deleted:

  - IAM managed policies pclustermaker-policy-osiris-00000000000000-{HeadNode-Compute,HeadNode-Storage,HeadNode-IAM,ComputeNode-Base}
  - IAM role and instance profile pclustermaker-role-osiris-00000000000000

Serial number: osiris-00000000000000
=================================================================
```

The same list is included in the SNS destruction report.  Act on it before the run's serial file is gone: `kill_pcluster.py` reads the cluster's serial number to build these resource names, so once `active_clusters/<cluster>/` is removed there is nothing left to retry with and the leftovers have to be found by hand.  The usual cause is a missing operator IAM permission — see [Operator IAM permissions](INSTALL.md#operator-iam-permissions).

---

## Stopping and Starting the Compute Fleet

`stop_pcluster.py` and `start_pcluster.py` stop or start the compute fleet while leaving the head node running.  Use them to pause a cluster between job batches without paying for idle compute nodes.

```
./stop_pcluster.py -N CLUSTER_NAME [--wait]
./start_pcluster.py -N CLUSTER_NAME [--wait]
```

| Flag | Short | Description |
|---|---|---|
| `--cluster_name NAME` | `-N` | Cluster name (required) |
| `--region REGION` | `-R` | AWS region (default: from cluster record) |
| `--wait` | `-W` | Poll until the fleet reaches the target state before exiting |

Without `--wait` the request is submitted and the script exits immediately.  With `--wait`, timestamped status lines are printed every 30 seconds until the fleet is `STOPPED` or `RUNNING` (up to 45 minutes).

**Note:** stopping the fleet terminates all compute nodes immediately — in-flight Slurm jobs will be killed.  Drain the queue first if needed.

---

## Listing Clusters

`list_pcluster.py` enumerates all clusters tracked by this repo (anything under `active_clusters/`) and prints a summary table from the local vars file — no AWS credentials required by default.

```
./list_pcluster.py [options]
```

| Flag | Short | Description |
|---|---|---|
| `--live` | `-L` | Call `pcluster describe-cluster` for real-time status (one API call per cluster) |
| `--region REGION` | `-R` | Filter output to a single region |
| `--owner OWNER` | `-O` | Filter output to a single owner |
| `--wide` | `-W` | Disable column truncation |
| `--json` | `-J` | Emit a JSON array instead of a table |

Example output:

```
Cluster  Owner     Region     Head Node    CPU Types           GPU Types  Min/Max CPU  Min/Max GPU  Type      Age  Status
-------  --------  ---------  -----------  ------------------  ---------  -----------  -----------  --------  ---  ------
osiris   rmarable  us-east-1  c8g.2xlarge  c8g.2xlarge, c7g.…  -          0/8          -/-          ondemand  4d   LOCAL
```

With `--live`, the `Status` column shows `clusterStatus / cloudFormationStackStatus` (e.g. `CREATE_COMPLETE / CREATE_COMPLETE`).  The two values diverge when a cluster update partially fails.

---

## Checking Cluster Health

`check_pcluster.py` runs a sequence of health checks against a named cluster and exits 0 only if every check passes.

```
./check_pcluster.py -N CLUSTER_NAME [--timeout SECONDS]
```

| Flag | Short | Description |
|---|---|---|
| `--cluster_name NAME` | `-N` | Cluster name (required) |
| `--timeout SECONDS` | `-T` | SSH timeout in seconds (default: 15, clamped to 1–300).  The S3 check uses the boto3 default timeout and is unaffected. |

Checks performed in order:

1. **Vars file** — cluster record exists in `src/vars_files/<name>.yml`
2. **CloudFormation status** — `pcluster describe-cluster` returns `clusterStatus=CREATE_COMPLETE`
3. **Head node IP** — public or private IP present in the describe-cluster response
4. **SSH reachability** — `ssh … echo OK` succeeds
5. **Slurm** — `sinfo -s` exits 0
6. **Postinstall complete** — custom action marker file `/opt/parallelcluster/shared/custom_action_done` is present on the head node
7. **Grafana health** — `curl -sk https://localhost:443/grafana/api/health` returns `"database":"ok"` (checked only when `enable_monitoring=true`)
8. **S3 bucket** — `s3.head_bucket` succeeds (always run, independent of SSH)

SSH-dependent checks (4–7) are `[SKIP]`ped rather than `[FAIL]`ed when SSH is unreachable, so a single SSH failure does not obscure the S3 result.

Example output (all passing):

```
Checking cluster: my-cluster
  [PASS] vars file
  [PASS] CloudFormation status: CREATE_COMPLETE
  [PASS] head node IP: 54.1.2.3
  [PASS] SSH reachability
  [PASS] Slurm (sinfo -s)
  [PASS] postinstall complete
  [PASS] S3 bucket: my-cluster-parallelcluster-bucket

All checks passed — my-cluster is healthy.
```

---

## Diagnosing a Cluster

`diagnose_pcluster.py` goes deeper than `check_pcluster.py` — it collects raw diagnostic data rather than pass/fail checks, and always exits 0 so output is never suppressed by an early failure.

```
./diagnose_pcluster.py -N CLUSTER_NAME [options]
```

| Flag | Default | Description |
|---|---|---|
| `--cluster_name NAME` / `-N` | required | Cluster name |
| `--region REGION` / `-R` | from vars file | Override AWS region |
| `--timeout SECONDS` / `-T` | 20 | SSH timeout |
| `--cw_lines N` | 50 | CloudWatch log lines per stream (max 500) |
| `--log_lines N` | 30 | Local log file tail lines (max 200) |
| `--hours N` | 24 | `sacct` lookback window in hours |
| `--no_cw` | off | Skip CloudWatch section (omit the flag to include CW output) |

Sections produced:

1. **CloudWatch: head node bootstrap logs** — last N lines from `cfn-init`, `cloud-init-output`, and `cinc_client` streams.  PCluster appends the stack's creation timestamp to the log group name (`/aws/parallelcluster/<cluster_name>-<YYYYmmddHHMM>`), so the group is discovered by prefix rather than constructed; the selected group name is printed above the streams.  Rebuilds of the same cluster name leave older groups behind — PCluster retains them by design — and the newest is used.  Requires `logs:DescribeLogGroups`, `logs:DescribeLogStreams`, `logs:FilterLogEvents`, and `logs:GetLogEvents` on the operator identity (all included in the operator policy).  Pass `--no_cw` to skip this section if permissions are unavailable.
2. **Slurm node states** — `sinfo -N -l` output; nodes not in `idle`/`mix`/`alloc` are annotated with `<-- not idle`.
3. **Recent Slurm job failures** — `sacct` filtered to `FAILED`, `CANCELLED`, `TIMEOUT`, `NODE_FAIL` states.  Prints a note if no results (Slurm accounting is not enabled by default in PCluster v3).
4. **Local log tails** — last N lines of `/var/log/parallelcluster/slurm_resume.log`, `slurm_suspend.log`, `/var/log/cinc/client.log`, `/var/log/cloud-init-output.log`.
5. **Postinstall marker** — confirms `/opt/parallelcluster/shared/custom_action_done` is present; prints the cluster serial number for cross-referencing S3 benchmark results.

Example output:

```
Diagnosing cluster: my-cluster  (us-east-1)
  serial: 20260724-abc123

=== CloudWatch: head node bootstrap logs ===

  log group: /aws/parallelcluster/my-cluster-202607241130

  --- cfn-init ---
  2026-07-24 11:32:01  ConfigSet: default
  2026-07-24 11:32:05  Install packages: ok
  ...

=== Slurm node states (sinfo -N -l) ===

  NODELIST  NODES PARTITION    STATE CPUS MEMORY REASON
  compute-1     1 cpu          idle     4   8000 none
  compute-2     1 cpu          drain    4   8000 maintenance   <-- not idle

=== Recent Slurm job failures (last 24h) ===

  No failed jobs in the last 24h
  (If this is unexpected, Slurm accounting may not be enabled.)

=== Local log tails (last 30 lines each) ===

  --- /var/log/parallelcluster/slurm_resume.log ---
  ...

=== Postinstall marker ===

  [PASS] /opt/parallelcluster/shared/custom_action_done  (serial: 20260724-abc123)
```

---

## Cost Reporting

`cost_pcluster.py` queries AWS Cost Explorer by the `ClusterID` resource tag to show actual spend per cluster.  Results reflect billing data with a 24-hour lag.

```
./cost_pcluster.py [options]
```

| Flag | Short | Description |
|---|---|---|
| `--cluster_name NAME` | `-N` | Single cluster (default: all in `active_clusters/`) |
| `--owner OWNER` | `-O` | Filter to clusters owned by this user |
| `--days N` | `-D` | Lookback window in days (default: 30, max: 365) |
| `--json` | `-J` | Emit JSON array instead of a table |

**Prerequisites:** the operator's IAM user/role needs `ce:GetCostAndUsage` and `ce:ListCostAllocationTags`.  The `ClusterID` tag key must also be activated as a cost allocation tag in the AWS Billing console (Console → Billing → Cost allocation tags → User-defined tags).  If the tag is not activated, all results show `$0.00` — the script detects this and prints a warning before running queries.

Example output:

```
AWS Cost Explorer — last 30 days  (24-hour data lag applies)

Period: 2026-06-24 – 2026-07-24

Cluster   Owner     Region     Cost ($)
-------   -----     ------     --------
osiris    rmarable  us-east-1  $47.82
bifrost   rmarable  us-west-2  $12.10
```

---

## Storage

With no storage flags at all, a cluster gets one shared filesystem — EBS at `/shared` — plus a node-local scratch directory on every instance.  Everything else is opt-in and additive; enabling EFS or FSx does not replace shared EBS.

| Mount point | Type | Scope | Default state |
|---|---|---|---|
| `/shared` | EBS volume | Shared across head node and all compute nodes | Always created |
| `/local_scratch` | Root EBS volume, or NVMe instance store | Local to one instance; not shared | Always created on every instance |
| `/efs` | EFS | Shared | Opt-in (`--enable_efs=true`) |
| `/fsx` | FSx for Lustre | Shared | Opt-in (`--enable_fsx=true`) |
| `/nfs/<export>` | External NFS | Shared, filer-provided | Opt-in (`--enable_external_nfs=true`) |

EFS and FSx filesystems are destroyed with the cluster and cannot be preserved by teardown — see [Deleting a Cluster](#deleting-a-cluster).  Move anything durable to S3 or to an external NFS filer before tearing down.

Whichever of these a cluster ends up with, the build summary names each one and its mount point — see [Building a Cluster](#building-a-cluster) for the block.

### Shared EBS (`/shared`)

Not optional.  The `SharedStorage:` block in `templates/config.pcluster.j2` emits its `Ebs` entry unconditionally, so every cluster gets one shared EBS volume mounted on the head node and every compute node.  There is no enable flag.

| Parameter | Default |
|---|---|
| `--ebs_shared_dir` | `/shared` |
| `--ebs_shared_volume_size` | 250 (GB; max 16,384) |
| `--ebs_shared_volume_type` | `gp3` (`gp2`, `gp3`, `io1`, `io2`, `st1`) |
| `--ebs_shared_volume_iops` | 3000 (emitted for `gp3`, `io1`, `io2` only) |
| `--ebs_shared_volume_throughput` | 125 (MB/s; emitted for `gp3` only) |
| `--ebs_encryption` | `false` |

`--ebs_encryption` also governs the head node, CPU queue, and GPU queue root volumes.

### Node-local scratch (`/local_scratch`)

Local to a single instance, not shared.  `templates/postinstall.j2` creates `/local_scratch` as a sticky-bit directory on the root EBS volume and symlinks `/scratch` to it.  When `enable_gpu` is `true` and NVMe instance store devices are present, `/local_scratch` is backed by those instead — one device is formatted XFS, several are assembled into a RAID0 array.  See [GPU](#gpu) for the device detection logic.

Postinstall is registered as an `OnNodeConfigured` custom action on the head node and on every compute queue (`templates/config.pcluster.j2`), so `/local_scratch` is created on every instance.  This matters for the instance-store path: NVMe instance store exists only on compute instances, so a head-node-only registration leaves the RAID0 block unreachable in practice.  See [Node bootstrap scripts](#node-bootstrap-scripts) for how the script gets to the node.

Data in `/local_scratch` does not survive instance termination, and compute nodes terminate on scale-down.

### EFS (`/efs`)

Enable with `--enable_efs=true`.  Mounted at `/efs` on all instances.  Costs almost nothing in build time: measured on a `generalPurpose`/`bursting` filesystem with one mount target, the filesystem completed in 4 seconds and the mount target in 1m 33s, both finishing well before the Route53 zone and the compute-fleet nested stack that actually gate the head node's launch.  A multi-AZ cluster creates one mount target per subnet and has not been timed.  Configure with `--efs_encryption`, `--efs_performance_mode` (`generalPurpose` or `maxIO`), and `--efs_throughput_mode` (`bursting`, `provisioned`, or `elastic`).

### FSx for Lustre (`/fsx`)

Enable with `--enable_fsx=true`.  Mounted at `/fsx`.  `--fsx_size` must be a positive multiple of 1200 GB; the default and minimum is 1200.  `--fsx_chunk_size` (the S3 imported-file chunk size, default 1024 MB) must fall between 1,024 MB (1 GB) and 512,000 MB (500 GB).

#### S3 hydration and dehydration

Requires `--enable_fsx_hydration=true`.  Setting any `--fsx_s3_*` value without that flag is an error, as is setting the flag without `--enable_fsx=true`.

**One bucket, two prefixes.**  FSx for Lustre requires the export bucket to be the same bucket as the import bucket — from AWS's own API model for `CreateFileSystem`: *"The Amazon S3 export bucket must be the same as the import bucket specified by `ImportPath`."*  Only the prefixes may differ.  `--fsx_s3_import_bucket` and `--fsx_s3_export_bucket` exist as separate parameters because `ImportPath` and `ExportPath` are separate FSx concepts — `ImportPath` is where the filesystem is seeded from at creation, `ExportPath` is where `lfs hsm_archive` writes changed files back — but both must name the same bucket.  A mismatch is rejected before the build starts rather than twenty minutes in, at FSx creation:

```
ERROR: Lustre hydration: fsx_s3_export_bucket (out-bucket) must name the same
bucket as fsx_s3_import_bucket (in-bucket) ...
```

The supported shape:

```bash
--enable_fsx_hydration=true --fsx_s3_import_bucket=ResearchData \
--fsx_s3_import_path=input/ --fsx_s3_export_bucket=ResearchData \
--fsx_s3_export_path=output/
```

The three accepted cases:

| Import | Export | Result |
|---|---|---|
| `--fsx_s3_import_bucket=data --fsx_s3_import_path=in/` | `--fsx_s3_export_bucket=data --fsx_s3_export_path=out/` | The supported shape.  No warning. |
| `--fsx_s3_import_bucket=data --fsx_s3_import_path=in/` | unset | Warns; the export side falls back to the import bucket *and* path, so hydration and dehydration both use `s3://data/in/` |
| `...=data --fsx_s3_import_path=shared/` | `...=data --fsx_s3_export_path=shared/` | Warns: dehydrated files overwrite the hydration source |

An unset `--fsx_s3_import_bucket` with `--enable_fsx_hydration=true` is an error — there is nothing to hydrate from.  Use `--enable_fsx=true` on its own for an empty Lustre filesystem.

Both sides are validated before the build starts: the bucket must exist (`head_bucket`, with a distinct error for a 403 so an access-denied bucket policy is not reported as a missing bucket), and the prefix must contain at least one object.  An empty or misspelled path fails immediately rather than at FSx creation time.

Note that the export fallback overwrites `--fsx_s3_export_path` with the import path.  To keep the two prefixes separate, name the bucket in both parameters rather than relying on the fallback.

**Helper scripts on the head node.**  When hydration is enabled, postinstall writes three one-line wrappers around Lustre's HSM commands into `/usr/local/bin` (mode 755, root-owned).  The build summary prints their paths.  They are not run automatically — hydration itself happens at FSx creation from `ImportPath`; these are for pulling and pushing on demand afterward:

| Script | What it does |
|---|---|
| `import-s3-to-lustre.sh` | `lfs hsm_restore` over every file under `/fsx` — fetches file *contents* for entries FSx has listed but not yet copied down |
| `export-lustre-to-s3.sh` | `lfs hsm_archive` over every file under `/fsx` — writes changed files back to the export prefix |
| `check-lustre-export-progress.sh` | Counts outstanding `ARCHIVE` actions, so an export in flight can be polled |

All three walk the whole filesystem with `find`, so on a large tree they take a while and are best run under `nohup` (the import and export scripts already background themselves).  Verified on a live `rhel9arm` head node with a 1200 GB Lustre filesystem and `lfs 2.15.6`.

### External NFS

Enable with `--enable_external_nfs=true --external_nfs_server=storage.domain.com`.  Exports are mounted under `/nfs/` and the export list lives in `templates/external_nfs_mount_list.j2` — uncomment the lines matching the paths your filer serves.  A security group permitting NFS traffic is attached to the head node and compute queues automatically.

---

## Networking and Compute

### VPC and Subnet Selection

> **Important:** The toolkit auto-discovers VPCs and subnets by convention when explicit values are not provided.  Auto-discovery picks the AWS default VPC and the *first* subnet returned by EC2 in each AZ.  EC2 does not guarantee subnet ordering, so results are non-deterministic in accounts with multiple subnets per AZ.  **Do not rely on auto-discovery for production clusters.**  Always specify networking resources explicitly.

| Parameter | Description |
|---|---|
| `--vpc_name` | VPC `Name` tag to use (default: `vpc_default` — the account's default VPC) |
| `--headnode_subnet_id` | Explicit subnet ID for the head node; overrides auto-discovery |
| `--compute_subnet_ids` | Comma-separated subnet IDs for the CPU compute fleet; overrides auto-discovery |
| `--compute_az` | Comma-separated AZs for the CPU compute fleet (default: same as `--az`) |
| `--use_private_compute_subnet` | Only auto-discover private subnets for CPU nodes (`true`/`false`, default: `false`) |
| `--gpu_subnet_ids` | Comma-separated subnet IDs for the GPU queue; falls back to `compute_subnet_ids` if empty |
| `--gpu_az` | Comma-separated AZs for the GPU queue; falls back to `compute_az` then `--az` if empty |
| `--use_private_gpu_subnet` | Only auto-discover private subnets for GPU nodes (`true`/`false`, default: `false`) |

Subnets and security groups are generated as part of the CloudFormation stack — the toolkit does not manage them independently outside of the stack lifecycle.

**Single-AZ cluster (explicit subnets — recommended):**
```
./make_pcluster.py -N prod01 -O rmarable -E rmarable@example.com -A us-east-1a \
    --vpc_name=my-hpc-vpc \
    --headnode_subnet_id=subnet-0abc123 \
    --compute_subnet_ids=subnet-0abc123
```

**Multi-AZ compute fleet spanning three AZs:**
```
./make_pcluster.py -N bigcluster -O rmarable -E rmarable@example.com -A us-east-1a \
    --vpc_name=my-hpc-vpc \
    --headnode_subnet_id=subnet-0abc123 \
    --compute_az=us-east-1a,us-east-1b,us-east-1c \
    --compute_subnet_ids=subnet-0abc123,subnet-0def456,subnet-0ghi789
```

**Private compute subnet (head node public, compute private):**
```
./make_pcluster.py -N private01 -O rmarable -E rmarable@example.com -A us-east-1a \
    --vpc_name=my-hpc-vpc \
    --headnode_subnet_id=subnet-0abc123 \
    --compute_subnet_ids=subnet-0private1 \
    --use_private_compute_subnet=true
```

### GPU

Set `--gpu_instance_type` to create a dedicated GPU queue alongside (or instead of) the CPU queue.  `enable_gpu` is derived — it is `true` whenever `gpu_instance_type` is non-empty, and cannot be set by the user.  GPU families (`g4dn`, `g4ad`, `g5`, `g5g`, `g6`, `p3`, `p3dn`, `p4d`, `p4de`, `p5`) are rejected from `compute_instance_type`, and non-GPU types are rejected from `gpu_instance_type`.

Both `compute_instance_type` and `gpu_instance_type` accept comma-separated lists for multi-instance-type queues:

```
# GPU-only cluster
./make_pcluster.py ... --gpu_instance_type=p3.2xlarge,g5.2xlarge

# CPU + GPU queues
./make_pcluster.py ... --compute_instance_type=c5.2xlarge --gpu_instance_type=g5.2xlarge
```

**The head node's instance type is independent of the queues.**  A CPU head node fronting both a CPU queue and a GPU queue — `c5.xlarge` head, `c5` `compute` partition, `g5` `gpu` partition — is the common layout and is fully supported, including for the benchmark suite.  `enable_cpu_queue` and `enable_gpu_queue` are each derived from whether the corresponding instance-type flag is non-empty, and the two `SlurmQueues` entries are emitted independently; nothing about the head node's own family gates either one.  There is no need for a GPU head node to run GPU jobs, and paying for an idle GPU on the head node buys nothing.

**The one real constraint is architecture, not CPU-vs-GPU.**  The head node and every queue instance type must share one CPU architecture, because a cluster runs one architecture-specific base OS image.  `make_pcluster.py` checks this at creation time against `ec2:DescribeInstanceTypes` — which is authoritative, and covers families the hardcoded ARM prefix list does not yet name — and refuses the build with the offending types named.  So an x86_64 head node with a Graviton `g5g` GPU queue is rejected up front rather than failing twenty minutes into the build.  Mixing *microarchitectures* within an architecture (Intel head node, AMD GPU nodes) is fine and is handled automatically by the benchmark suite.

**What the GPU postinstall block does:**  `templates/postinstall.j2` gates a GPU block on `enable_gpu == 'true'`.  Postinstall runs as an `OnNodeConfigured` custom action on the head node and on every compute queue, so this block runs on the GPU compute nodes — which are the only instances that have NVMe instance store to configure.

- NVMe instance store detection — scans `/sys/block/nvme*` and accepts a device only if it passes three independent filters: the model string matches `AmazonEC2NVMeInstanceStorage` (whitespace stripped), `holders/` is empty, and `blkid` finds no filesystem signature.  The model check keeps EBS volumes, which also appear as `/dev/nvme*`, from being formatted.  The other two keep the toolkit off devices ParallelCluster already claimed, and neither subsumes the other — a device inside an LVM volume has holders but no signature of its own, while a formatted-but-unmounted device has a signature and no holders
- **ParallelCluster usually claims these devices first.**  Its `aws-parallelcluster-environment::ephemeral_drives` cookbook runs *before* `OnNodeConfigured` and, on any instance type with instance store, puts every such device into an LVM physical volume, formats it `ext4`, and mounts it on `/scratch`.  On that common case the toolkit's block correctly does nothing and `/local_scratch` is a symlink to PCluster's `/scratch` — verified on a live `g4dn.xlarge`.  Without the holders/`blkid` filters, `mkfs.xfs` on a claimed device fails with `Device or resource busy`, which fails the node
- Single unclaimed device: formatted XFS, mounted at `/local_scratch` with `noatime,nodiratime,nofail`
- Multiple unclaimed devices (`p4d.24xlarge` has 8×1000 GB, `p5.48xlarge` has 8×3800 GB, per `aws ec2 describe-instance-types`): RAID0 via `mdadm`, mounted at `/local_scratch`
- No instance store present (e.g. `p3.2xlarge`): `/local_scratch` remains a sticky-bit directory on the root EBS volume
- `htop` installed by the GPU block itself, since the main package block is head-node-only and does not run on compute nodes; `nvtop` is head-node-only because it lives outside the default repositories (`multiverse` on Ubuntu, EPEL on RHEL 9) and a compute node's package index is whatever the AMI shipped.  A compute node refreshes its index first (`apt-get update` / `dnf makecache`) because `OnNodeStart` — and therefore preinstall's refresh — never runs there.  Both installs are non-fatal (`|| echo "WARNING: ..."`), the only ones in the file: they are diagnostics nothing in the job path imports, and one transient mirror outage would otherwise count toward the 10-failure protected-mode threshold and cost the entire stack

**EFA GPUDirect RDMA (GDR):**  When `--enable_efa=true` and any GPU queue instance type is `p4d.24xlarge`, `p4de.24xlarge`, or `p5.48xlarge`, `GdrSupport: true` is added to the GPU queue EFA config automatically.

**GPU volume settings:**  The GPU queue uses its own root volume parameters (`--gpu_root_volume_size`, `--gpu_root_volume_type`, `--gpu_root_volume_iops`, `--gpu_root_volume_throughput`) independent of the CPU queue.

**CUDA / drivers:**  PCluster's official deep learning AMIs include NVIDIA drivers.  Pass `--custom_ami=<ami-id>` to use a pre-built DLAMI or a custom AMI with pinned driver versions.

### EFA

Enable with `--enable_efa=true`.  Supported on every `base_os` value the toolkit accepts.  Requires a supported instance type (c5n.18xlarge, hpc6a.48xlarge, hpc7a.96xlarge, hpc7g.16xlarge, etc.).  A dynamic placement group is created automatically.

### Placement Groups

Enable with `--placement_group=DYNAMIC`.  PCluster creates one managed cluster placement group per queue; it is applied to the CPU and GPU compute queues only.  The head node is never placed in a placement group.  `--enable_efa=true` sets this to `DYNAMIC` automatically if it is still `NONE`.

### HyperThreading

Disable with `--hyperthreading=false`.

---

## Managing Queues on a Running Cluster

`manage_pcluster_queue.py` edits a live cluster's config file to add, remove, or list Slurm queue stanzas without rebuilding the cluster.  Changes take effect after a `pcluster update-cluster`; the compute fleet must be stopped before updating.  Pass `-W` to have the script handle the full stop/update/restart cycle automatically.

```
./manage_pcluster_queue.py -N <cluster_name> -A <action> -T <queue_type> [options]
```

Required arguments:

| Flag | Description |
|---|---|
| `-N` | Cluster name |
| `-A` | Action: `add`, `remove`, or `list` |
| `-T` | Queue type: `compute` (CPU) or `gpu`.  Required for `add`; ignored by `remove` and `list`. |

### Listing queues

```
./manage_pcluster_queue.py -N <cluster_name> -A list
```

### Adding queues

```
# Add a spot CPU queue
./manage_pcluster_queue.py -N osiris -A add -T compute \
    -E c5.2xlarge,c5.4xlarge -C spot \
    -Q compute-spot-overflow

# Add an on-demand GPU queue
./manage_pcluster_queue.py -N osiris -A add -T gpu \
    -E p3.2xlarge -C ondemand \
    -Q gpu-ondemand

# Add a GPU queue with custom scaling
./manage_pcluster_queue.py -N osiris -A add -T gpu \
    -E g5.2xlarge,g5.4xlarge \
    --initial_size 1 --max_size 4 --maintain_initial_size true
```

### Removing a queue

```
./manage_pcluster_queue.py -N osiris -A remove -Q compute-spot-overflow
```

### Applying the change

**Automated (`-W`/`--wait`):** the script stops the fleet, applies the config, and restarts the fleet, printing status every 30 seconds.  It warns that the operation can take up to 30 minutes; each individual poll loop times out at 45 minutes.  Run inside `screen` or `tmux` to avoid losing the session mid-update.

```
./manage_pcluster_queue.py -N osiris -A add -T compute -E c5.xlarge --wait
```

**Manual (default):** after `add` or `remove`, the script prints the exact commands needed:

1. Stop the compute fleet
2. Wait for STOPPED status
3. Run `pcluster update-cluster` with the updated config
4. Wait for UPDATE_COMPLETE
5. Restart the fleet

### Constraints

- Compute queues reject GPU instance types and vice versa
- Mixed x86/Graviton instance types in a single queue are rejected
- A new queue whose architecture differs from the running cluster's is rejected — a cluster runs one architecture-specific base OS image
- The last remaining queue on a cluster cannot be removed
- `p4d`/`p4de`/`p5` GPU instances support EFA-GDR; the script prints a reminder to enable it manually if needed

---

## Software Environment

### Operating systems

`--base_os` accepts eight values, in two package-manager families:

| `base_os` | Architecture | Package manager | Login user |
|---|---|---|---|
| `ubuntu2204` | x86_64 | apt | `ubuntu` |
| `ubuntu2404` | x86_64 | apt | `ubuntu` |
| `ubuntu2204arm` | Graviton (aarch64) | apt | `ubuntu` |
| `ubuntu2404arm` | Graviton (aarch64) | apt | `ubuntu` |
| `rhel9` | x86_64 | dnf | `ec2-user` |
| `rhel9arm` | Graviton (aarch64) | dnf | `ec2-user` |
| `alinux2023` | x86_64 | dnf | `ec2-user` |
| `alinux2023arm` | Graviton (aarch64) | dnf | `ec2-user` |

The `arm` suffix is the toolkit's, not ParallelCluster's — it drives instance-architecture validation and is stripped before the value reaches PCluster's `Os:` field, so `ubuntu2404arm`, `rhel9arm`, and `alinux2023arm` become `ubuntu2404`, `rhel9`, and `alinux2023` there.  Mixing an ARM `base_os` with an x86_64 instance type (or vice versa) is rejected before anything is created; do not mix architectures across node types either.

`preinstall.j2` and `postinstall.j2` branch on the OS family and install the equivalent package sets; the dnf side branches again where Amazon Linux 2023 and RHEL 9 differ.  A few differences are worth knowing about if you write a `--post_install_script` hook:

- On RHEL 9, EPEL and CodeReady Builder are enabled during postinstall because `lua-devel`, `lua-posix`, `lua-filesystem`, and `tcllib` are in neither baseos nor appstream.  EPEL is installed from its release RPM URL, since `epel-release` is not packaged in RHEL itself.
- On Amazon Linux 2023, neither EPEL nor CRB is used, because `epel-release` is not packaged for al2023 at all and everything the toolkit needs is already in the core repo.  Four packages the RHEL arm installs are absent from al2023 on both architectures, so that arm differs accordingly: `luarocks` (the three Lua rocks come from the core repo as RPMs instead, and the luarocks build step is a no-op), `tcllib` (unused by the toolkit — Lmod uses `tcl` itself), and `nvtop` (so GPU clusters get `htop` only, on both node types).  Do not add any of them back for symmetry with RHEL; each one would fail the node.
- `pip3` is called with `--break-system-packages` on Ubuntu and without it on RHEL 9 and Amazon Linux 2023, both of which ship a pip that predates PEP 668 and rejects the flag.
- Every `pip3 install` on a node carries `--ignore-installed`, on both families.  pip cannot uninstall a distribution whose `dist-info` has no `RECORD` file, and distro-packaged Python modules routinely ship exactly that — Ubuntu's `python3-pip` and RHEL 9's `python3-requests` are both confirmed cases.  Any install that resolves to replacing one dies at `Attempting uninstall:`, which under `set -euo pipefail` fails the node's bootstrap.  `--ignore-installed` skips the uninstall and installs over the top; nothing in the toolkit needs the distro package removed.  This is a different problem from `--break-system-packages`, which only permits writing into the system tree.
- `nvtop` (GPU clusters) is installed on the head node only, on Ubuntu and RHEL 9 — it is outside the default repositories in each, and the operator logs into the head node rather than a compute node.  It is not installed at all on Amazon Linux 2023, which does not package it.
- On RHEL 9, `bc` is installed explicitly because Lmod's `./configure` hard-quits without it (`You must have bc in your path. Quitting!`) rather than degrading.  It is on the Ubuntu and Amazon Linux 2023 package lines too, deliberately: those AMIs happen to ship `bc` incidentally, and depending on what a base image carries by accident is how the RHEL gap stayed hidden.  (Upstream's own monitoring installer claims `bc` is absent from the default al2023 repos; the repo metadata says otherwise on both architectures.)
- The dnf kernel exclusions cover both Lustre spellings — `kmod-lustre*` and `lustre-client*` — because the two distros name the client differently.  Amazon Linux 2023 has no `kmod-lustre*` package at all, so the RHEL glob alone would silently protect nothing there.

**Both Amazon Linux 2023 arms are validated on live cluster builds as of 2026-07-28.**  `alinux2023` reached `CREATE_COMPLETE` on a `c5.xlarge` head node with EFS, a `c5.2xlarge` CPU queue, two `g4dn.xlarge` GPU nodes, benchmarks, and monitoring; `alinux2023arm` reached `CREATE_COMPLETE` on a `c8g.xlarge` head node with EFS, a 1200 GB FSx for Lustre filesystem, a `c8g.2xlarge` CPU queue, benchmarks, and monitoring.  Every package claim above was confirmed against the head node's and every compute node's own bootstrap logs on both architectures, not merely against the stack's exit status: `luarocks`, `epel`, `tcllib`, and `nvtop` appear nowhere; the three Lua rocks install as core-repo RPMs; `bc` is already present and Lmod's `./configure` finds it; and `dnf update` upgrades a single package with no kernel, dracut, or initramfs activity.  The Docker Compose CLI plugin is staged and checksum-verified per architecture — `docker-compose-linux-x86_64-v2.29.7` and `docker-compose-linux-aarch64-v2.29.7` respectively — and upstream's `github.com` download is removed from the extracted tree on both.

**Both RHEL 9 arms are validated on live cluster builds as of 2026-07-28.**  `rhel9` reached `CREATE_COMPLETE` on a `c5.xlarge` head node with EFS, a CPU queue, a `g4dn.xlarge` GPU queue, benchmarks, and monitoring; `rhel9arm` reached `CREATE_COMPLETE` on a `c8g.xlarge` head node with EFS, a 1200 GB FSx for Lustre filesystem, benchmarks, and monitoring.  The whole RPM bootstrap path is confirmed on both architectures: EPEL by release-RPM URL, the CodeReady Builder repository id, all three luarocks rocks compiling against `lua-devel` with no separate header package, and `dnf update` upgrading `dracut` itself while installing zero `kernel*` packages and regenerating no initramfs.  All eight pip pins resolved from `manylinux_2_17_aarch64` wheels on Graviton.

### Node bootstrap scripts

Two stages run on every node, in this order:

1. **The toolkit's own scripts** — `templates/preinstall.j2` and `templates/postinstall.j2`, rendered per cluster with that cluster's variables (OS, storage layout, `pkg_dir`, GPU flags) and uploaded to the cluster's S3 bucket as `preinstall.<cluster>.sh` and `postinstall.<cluster>.sh`.  These are the toolkit's own work: base packages, Spack, Lmod, `/local_scratch`, the benchmark suite, and the GPU block.
2. **Your hook** — the script named by `--pre_install_script` / `--post_install_script`, copied verbatim and uploaded under its own basename.  Defaults are `scripts/pre-deployment.sh` and `scripts/post-deployment.sh`, both no-op placeholders.  Put site-specific customization here; do not edit the toolkit templates to add it.

The stages are wired as a PCluster `Sequence`, so stage 2 runs only if stage 1 succeeded.  `OnNodeStart` (preinstall) runs on the head node only — repeating the Python/pip/AWS CLI install on every scale-up event would add boot latency to every compute node for no benefit.  `OnNodeConfigured` (postinstall) runs on the head node and on every compute queue, since that is where node-local work like `/local_scratch` belongs.  When `--enable_monitoring=true` the monitoring installer is appended as a third stage.

Paths passed to `--pre_install_script` / `--post_install_script` are relative to the repository root.

#### The kernel is never upgraded

`preinstall.j2` upgrades the AMI's packages — `apt-get dist-upgrade` on Ubuntu, `dnf update` on RHEL 9 — but holds back the running kernel along with the out-of-tree modules built against it.  Ubuntu does this with `apt-mark hold` on the installed `linux-*` packages; RHEL 9 with `--exclude='kernel*' --exclude='kmod-lustre*' --exclude='efa*'`.  Two independent reasons:

- **A kernel replacement triggers an initramfs rebuild whose runtime is unbounded**, and it runs inside the window CloudFormation gives the head node to signal success.  Real builds failed this way on both families: on the PCluster AMI of the day a full upgrade crossed a kernel boundary and was still rebuilding when the wait condition expired, and on the RHEL 9 AMI a full update crossed `5.14.0-611.55.1.el9_7` → `5.14.0-687.30.1.el9_8` with dracut still running when CloudFormation gave up.
- **PCluster's AMI ships EFA and Lustre kernel modules built against the kernel it boots.**  Replacing that kernel without rebuilding them risks losing the interconnect or the Lustre client on the next boot.

The two mechanisms are equivalent in effect but not in shape: `apt-mark` pins packages by name, so the Ubuntu path first enumerates the installed `linux-*` packages and filters them to the ones dpkg reports as actually installed.  `--exclude` takes a glob resolved when dnf builds the transaction, so the RHEL 9 path needs no enumeration and holds on every AMI revision regardless of which updates happen to be pending — every RHEL 9 kernel subpackage (`kernel`, `-core`, `-modules`, `-modules-core`, `-modules-extra`, `-headers`, `-devel`, `-tools`) matches `kernel*`.

The upgrade itself is deliberately kept, because `preinstall.j2` installs the Python development headers and `numpy`/`scipy`/`pandas`/`matplotlib` compile from source wherever pip finds no wheel — which every `*arm` value of `base_os` can hit.  If you add a package that needs a newer kernel, pin it in your own `--pre_install_script` hook and reboot deliberately outside the bootstrap window; do not remove the exclusions.

### Spack + Lmod

Every stack includes [Spack](https://spack.io/) and [Lmod](https://github.com/TACC/Lmod) for HPC software module management.  Spack is cloned into `<shared>/pkg/spack`, where `<shared>` is the first available of `/fsx`, `/efs`, `/nfs`, or the shared EBS mount.

Both are built on the head node only.  Compute nodes mount the same shared storage and inherit the installation, so having every scaling node repeat the clone and `chown -R` would be wasted boot time on a tree that is already populated.

**`MODULEPATH` comes from Spack, not from Lmod's compiled-in root.**  Lmod is configured with `--with-module-root-path=<shared>/pkg/modulefiles`, but that setting is only ever read by Lmod's `init/profile` script, and postinstall installs `init/sh` instead — which defines the `module`, `ml`, and `clearMT` shell functions and sets `MODULESHOME`, with no reference to `MODULEPATH` at all.  What populates `MODULEPATH` on a login shell is `/etc/profile.d/lmod_spack.sh` sourcing Spack's `setup-env.sh`, which appends Spack's own module roots.  Consequently `<shared>/pkg/modulefiles` is not created by the install and will not exist on a fresh cluster — nothing reads it, so this is expected rather than a broken installation.  If you want to hand-place modulefiles outside Spack, create that directory yourself and add it to `MODULEPATH` (or to `LMOD_SITE_MODULEPATH`) from your own `--post_install_script` hook.

### Job Submission

A default Slurm submission script (`scripts/sbatch_default_submission_script.sh`) is rendered from the toolkit's `scripts/` directory into the login user's home directory during cluster creation (`ubuntu` or `ec2-user`, depending on `base_os`).  Copy it to shared storage and customize:

```
cp ~/sbatch_default_submission_script.sh /fsx/scratch/my_project/
sbatch /fsx/scratch/my_project/sbatch_default_submission_script.sh
```

**`--partition` and `--ntasks` are derived from this cluster's own shape, not hardcoded.**  A cluster with a CPU queue gets `--partition=compute` and `--ntasks=<cpu_ranks_per_node>`; a GPU-only cluster gets `--partition=gpu` and `--ntasks=<gpu_vcpus_per_node>`.  Both matter:

- **A GPU-only cluster has no `compute` partition**, and `sbatch` rejects an invalid partition outright before the job ever runs.  `enable_cpu_queue` is derived from `--compute_instance_type`, so the script follows whichever queues the cluster actually has.
- **The rank count is vCPUs, divided by `DefaultThreadsPerCore` when `--hyperthreading=false`** — never halved unconditionally.  Graviton reports one thread per core, so halving there would request half the cores every ARM node has.  Where a queue holds several instance types, the count is the smallest one's, since that is the only value every node in the queue can satisfy.

Note this is a *core* count, which is not the same as the GPU benchmark's `--ntasks-per-node`: `job_hpc-benchmark.sh` matches its rank count to the number of NVIDIA devices per node, while this script asks for cores.  A `p3.2xlarge` has 1 GPU and 8 vCPUs, and a general-purpose job wants the 8.

### HPC Benchmarks

Enable with `--enable_hpc_benchmarks=true`.  Cluster creation deploys the benchmark suite to a personalized working directory on the head node — `~/hpc-benchmark/<cluster_name>/<cluster_owner>/slurm/` — holding the driver, a `job_hpc-benchmark.sh` rendered for this cluster's queue layout, and a `README-PERFORMANCE.md` naming this cluster's own paths.  Postinstall also installs the Python plotting dependencies (`matplotlib`, `numpy`, `pandas`, `scipy`, `seaborn`) and drops a second copy of the driver at `~/hpc-benchmark/hpc-benchmark.sh`; that copy is restored from S3 on every head node boot so a replaced EBS root does not lose it, and it is the only file staged there.  Work out of the personalized directory — it is the one with the rendered job script.

**These commands run on the cluster head node** (SSH in via `./access_cluster.py` first):

```bash
cd ~/hpc-benchmark/<cluster_name>/<cluster_owner>/slurm
module load openmpi
./hpc-benchmark.sh install                              # build STREAM, OSU, IOR, HPCG (~5 min)
./hpc-benchmark.sh run --tests stream,osu,ior,hpcg
./hpc-benchmark.sh report
```

**Results are preserved on teardown.**  `kill_pcluster.py` syncs benchmark results from the head node to `s3://parallelclustermaker-results-<account-id>-<region>/hpc-benchmark-results/<cluster_name>/<cluster_serial_number>/` before deleting the cluster.  That bucket is **not** the per-cluster bucket: it is keyed on your account and region, it is created on the first build that enables benchmarks, and nothing in this toolkit ever deletes it — so results from multiple builds of the same cluster name land in separate serial-number subdirectories and accumulate rather than overwriting each other.  It is the one bucket you are expected to prune by hand.

**STREAM follows whatever node it lands on.**  STREAM is compiled `-march=native`, which binds the binary to the *microarchitecture* rather than the architecture — a `c5.xlarge` head node is Intel Skylake and a `g5.xlarge` GPU node is AMD Zen 3, and `uname -m` calls both `x86_64`.  `install` caches the source and `run` compiles `bin/stream-<march>` on the node it is executing on, so a job on a GPU partition measures that node's real bandwidth with no manual step.  OSU, IOR, and HPCG are built by `configure`/`make` without `-march=native` and are portable across microarchitectures, so those stay in `bin/` guarded by an architecture stamp.

**OSU builds itself on the GPU node when the head node cannot.**  `install` enables CUDA only when the node running it has both an NVIDIA device and a CUDA toolkit, because OSU's `configure` aborts outright on a missing `-lcuda`, `-lcudart`, or `cuda.h` rather than degrading — deriving that from a cluster-level flag would fail the whole install and take STREAM, IOR, and HPCG down with OSU.  On a CPU head node it therefore produces a host-to-host OSU, and the first GPU-partition job builds a CUDA-enabled tree under `bin/osu-cuda` on the GPU node itself and writes `osu/latency_cuda.txt` and `osu/bandwidth_cuda.txt` alongside the host-to-host results.  `bin/` is shared storage, so later GPU jobs reuse it.  That build can never fail the run: if it cannot be done on the node, the host-to-host results are still written and the reason is printed.

**Both partitions are benchmarkable from a CPU head node.**  The shipped `job_hpc-benchmark.sh` is submittable as-is: its `#SBATCH --partition=` and `--ntasks-per-node=` directives are rendered from this cluster's queue layout.  A GPU-only cluster has no `compute` partition, so a hardcoded partition would be rejected by `sbatch` before anything ran; on such a cluster the rank count is the NVIDIA GPU count reported for the queue's instance types, so one rank lands per GPU.  On a cluster with both queues the script targets `compute`, and the GPU run is the same script with two directives overridden:

```bash
sbatch job_hpc-benchmark.sh                                        # compute partition
sbatch --partition=gpu --ntasks-per-node=<gpu_count> job_hpc-benchmark.sh
```

The exact second command, with this cluster's GPU count already substituted, is in the commented GPU section at the bottom of the rendered script.

See `hpc-benchmark/README-PERFORMANCE.md` for full documentation.  The copy in the personalized working directory on the head node is the same document with this cluster's name, owner, and paths substituted in; the copy in this repo spells them `<cluster_name>` and `<cluster_owner>` and is not deployed.

---

### Monitoring

Enable with `--enable_monitoring=true` (default: `false`).  Deploys the [aws-parallelcluster-monitoring](https://github.com/aws-samples/aws-parallelcluster-monitoring) Grafana/Prometheus stack.  The same install script runs on every node; it branches on node type internally, so the head node gets the full stack and compute nodes get only a metrics exporter.

**On the head node:**

- Grafana (port 443, self-signed TLS)
- Prometheus, pushgateway, cloudwatch-exporter, nginx, node_exporter (Docker Compose)
- prometheus-slurm-exporter (systemd, scrapes Slurm metrics every 30 s)

**On each compute node:**

- node_exporter (Docker Compose)
- NVIDIA DCGM exporter on GPU instances, from `compose/compute.gpu.yml` upstream — requires the `nvidia-container-toolkit`, which the upstream installer provides

**Access Grafana:**

`grafana_tunnel.py` opens or closes the tunnel without locating the per-cluster script manually:

```bash
# Open tunnel (background SSH, prints URL and password command)
./grafana_tunnel.py -N CLUSTER_NAME

# Use a different local port
./grafana_tunnel.py -N CLUSTER_NAME -P 9443

# Close the tunnel
./grafana_tunnel.py -N CLUSTER_NAME --stop
```

The script verifies that monitoring is enabled for the cluster and exits with a message if it is not.  The generated per-cluster script also works directly:

```bash
# Open
./active_clusters/<cluster_name>/grafana_tunnel.<cluster_name>.sh

# Close
./active_clusters/<cluster_name>/grafana_tunnel.<cluster_name>.sh 8443 stop
```

Then open `https://localhost:8443/grafana/` in your browser and accept the self-signed certificate warning.  Pass a different local port if 8443 is in use.

If the head node has a public IP you can also open `https://<head-node-public-ip>/grafana/` directly (requires port 443 open in the security group).

**Retrieve the Grafana admin password:**
```bash
aws ssm get-parameter \
  --name "/parallelcluster/<cluster_name>/grafana/admin-password" \
  --with-decryption \
  --query "Parameter.Value" --output text
```

**Note:** PCluster head nodes ship a web server running on port 80 — `apache2` on Ubuntu, `httpd` on RHEL 9.  The monitoring installer stops and disables whichever is present so the nginx container can bind ports 80 and 443.

**Monitoring is verified on RHEL 9, on both architectures** (2026-07-28).  Upstream's `detect_platform` resolves `PLATFORM_ID=platform:el9` on x86_64 and Graviton alike, and the container stack reached `Started` on both — so the v2.6 installer is arch-agnostic on el9, not x86-only.

**IAM:** Monitoring permissions are granted via a separate managed policy `<ec2_iam_policy>-HeadNode-Monitoring` (8 statements, ~1,550 bytes minified).  It is created and attached during `make_pcluster.py` and deleted during `kill_pcluster.py`.

**Supply chain:** The `aws-parallelcluster-monitoring` tarball is downloaded from GitHub at cluster-build time, checksum-verified, and staged in the cluster's S3 bucket.  Head nodes pull from S3, not GitHub, so private-subnet nodes and air-gapped environments work without internet access.

On Amazon Linux 2023 that applies to the Docker Compose CLI plugin as well.  AL2023 does not package `docker-compose-plugin`, so upstream's installer downloads the binary from `github.com` on every node at boot, with no integrity check — which fails outright on a private subnet.  The toolkit instead downloads it once at build time, verifies it against `--docker_compose_checksum_x86_64` / `--docker_compose_checksum_aarch64` (both defaulted in `pcluster_defaults.yml` and matching Docker's own published sums for `v2.29.7`), stages it to S3, and installs it from there on every node before the monitoring installer runs.  The wrapper also deletes upstream's download from the extracted tree so it cannot overwrite the verified copy, and fails the build by name if that edit stops matching in a future monitoring release.  Because the download is removed rather than reused, `--docker_compose_version` is the toolkit's own pin and need not match upstream's.  The Ubuntu and RHEL 9 arms install the plugin from a signed distro repository and use none of this.

**Version:** Pin a specific release tag with `--monitoring_version=v2.6` (default).

**Custom AMI:** The Docker Compose installation adds several minutes to head node boot time.  For production clusters or fast iteration, build a custom AMI with the monitoring stack pre-installed.

---

## SSH Key Management

At cluster creation, the SSH private key is stored in AWS Secrets Manager at:

```
parallelcluster/<cluster_name>/<cluster_serial_number>/ssh-private-key
```

The secret is deleted automatically when `kill_pcluster.py` runs, along with the EC2 keypair and the local `.pem` file.  If the CloudFormation stack deletion itself reaches `DELETE_FAILED` (e.g. a dangling ENI, security group, or EFA interface), all three are deliberately preserved instead — this keeps the head node reachable for manual troubleshooting until the stack is fully torn down.  Re-run `kill_pcluster.py` after resolving the CloudFormation dependency to complete cleanup.

**If your local `.pem` file is lost**, retrieve it from Secrets Manager:

```bash
active_clusters/<cluster_name>/retrieve_ssh_key.<cluster_name>.sh
# optionally specify a destination:
active_clusters/<cluster_name>/retrieve_ssh_key.<cluster_name>.sh --out /tmp/mykey.pem
```

`access_cluster.py` calls the retrieve script automatically if the local key is missing.

**Rotating the SSH keypair** without rebuilding the cluster:

```bash
./rotate_cluster_key.py -N <cluster_name> -A <az>
# preview what will change:
./rotate_cluster_key.py -N <cluster_name> -A <az> --dry_run
```

Rotation: generates a new ED25519 keypair locally, appends the public key to `~/.ssh/authorized_keys` on the head node, imports it as the new EC2 keypair, updates the Secrets Manager secret, overwrites the local `.pem`, and deletes the old EC2 keypair.

**IAM requirements** (operator's user/role — not the cluster head node role):

| Permission | Purpose |
|---|---|
| `secretsmanager:CreateSecret` | Store key at cluster creation |
| `secretsmanager:PutSecretValue` | Update key on rotation |
| `secretsmanager:GetSecretValue` | Retrieve key via retrieve script |
| `secretsmanager:DeleteSecret` | Remove key on teardown |
| `ec2:ImportKeyPair` | Register new public key during rotation |
| `ec2:DeleteKeyPair` | Remove old keypair after rotation |

---

## Tagging

All resources are tagged automatically:

| Tag | Source |
|---|---|
| `ClusterID` | `--cluster_name` |
| `ClusterOwner` | `--cluster_owner` |
| `ClusterOwnerEmail` | `--cluster_owner_email` |
| `ClusterOwnerDepartment` | `--cluster_owner_department` |
| `ClusterStackType` | ParallelCluster |
| `ClusterOSType` | `--base_os` |
| `ClusterScheduler` | `--scheduler` |
| `ClusterSerialNumber` | generated |
| `ProdLevel` | `--prod_level` |
| `ProjectID` | `--project_id` (if set) |
| `DEPLOYMENT_DATE` | generated |

Supported departments: `analytics`, `clinical`, `commercial`, `compbio`, `compchem`, `datasci`, `design`, `development`, `hpc`, `imaging`, `manufacturing`, `medical`, `modeling`, `operations`, `proteomics`, `qa`, `research`, `robotics`, `scicomp`.

---

## Note to DevOps Teams

ParallelClusterMaker does **not** create or modify VPCs, subnets, gateways, routes, or Transit Gateways.  It creates IAM roles, policies, and instance profiles scoped to each individual cluster stack.  Templates are in `templates/` and can be customized.  If you hit permissions errors, the IAM policy template is the right starting point for working with your security team.

---

## Troubleshooting

**IAM permissions:** Check `templates/HeadNode-Compute.json_src`, `HeadNode-Storage.json_src`, `HeadNode-IAM.json_src`, `ComputeNode-Base.json_src`, and (when `enable_monitoring=true`) `HeadNode-Monitoring.json_src`.  The instance policy is split by role into five managed policies to stay under the IAM managed policy size limit.  IAM role and instance-profile resources use both flat-name ARNs (`parallelcluster-<CLUSTER_NAME>-*`) and path-based ARNs (`parallelcluster/<CLUSTER_NAME>/*`) — PCluster v3 uses the latter for compute fleet roles.  Most build failures trace back to missing IAM permissions.

**Spot capacity:** Compute nodes that fail to launch surface as a `ComputeFleet - CREATE_FAILED` CloudFormation error.  Retry the build or switch to `--cluster_type=ondemand`.

**Build fails with `HeadNodeWaitCondition` timing out (`CREATE_FAILED`, 0 of 1 signals):** The head node did not finish bootstrapping inside the window CloudFormation allows.  Note that this clock starts when CloudFormation *begins creating the wait condition* — before the head node instance exists — and shared filesystem provisioning sits on the head node's critical path.  A 1200 GB FSx for Lustre filesystem measured 17m 22s, over half of PCluster's stock 2100 s budget, before the instance had even launched.

The toolkit raises `head_node_bootstrap_timeout` automatically for this: +1800 s when `enable_fsx` is true, +600 s when `enable_efs` is true, whichever is larger (the two provision concurrently, so the head node waits on the slower one, not the sum).  A `*** INFO ***` line names the filesystem that drove the increase.

Both allowances are measured against live builds rather than estimated.  FSx: a second 1200 GB filesystem took 19m 20s before its instance existed, and the build completed in 34m 24s of the 3900 s granted.  EFS (`generalPurpose`/`bursting`, one mount target): the filesystem itself completed in 4 s and its mount target in 1m 33s, with the instance appearing 4m 24s in and the wait condition satisfied in 20m 52s of the 2700 s granted — so the EFS allowance carries roughly 2.3x headroom.  Two caveats worth knowing before you rely on them: a multi-AZ EFS cluster creates one mount target per subnet and has not been timed, and on the EFS build the mount target was not in fact what gated the instance (the head node launch template holds no reference to it), so 600 s covers the observed pre-instance window rather than a proven dependency.

If it still times out, set `head_node_bootstrap_timeout` explicitly in your defaults file — any value other than 2100 is used verbatim and disables the automatic increase, so set the *total* you want, not an increment:

```yaml
head_node_bootstrap_timeout: 5400
```

The ceiling is 43200 (12 hours), CloudFormation's own limit; larger values are clamped with a warning.  The value cannot be changed on a running cluster — PCluster marks it `UpdatePolicy.UNSUPPORTED`, so it takes a rebuild.  To find where the time actually went, compare the `CREATE_IN_PROGRESS`/`CREATE_COMPLETE` timestamps per resource:

```
aws cloudformation describe-stack-events --stack-name <cluster_name> \
    --query 'StackEvents[].[Timestamp,LogicalResourceId,ResourceStatus]' --output text | sort
```

The `preinstall`/`postinstall` scripts exclude the kernel from their package upgrades precisely because a kernel bump added an unbounded initramfs rebuild to this window — see [The kernel is never upgraded](#the-kernel-is-never-upgraded).

**Compute nodes fail to bootstrap after editing `postinstall.j2`:** Postinstall runs on the head node *and* on every compute node, and it runs under `set -euo pipefail` — a non-zero exit fails the node's bootstrap.

A compute-node failure does not stop the way a head-node failure does, which makes it more expensive rather than less.  `clustermgtd` marks the node `DOWN`, relaunches it, and repeats until the queue's bootstrap-failure count reaches **10**, at which point `clusterstatusmgtd` puts the cluster in `PROTECTED` state and the stack fails — after ten instance launches and, in one measured case, 82 minutes.  So a two-line mistake in a block that runs on compute nodes costs the whole build.  Check the *compute* node's log stream, not just the head node's: the head node can finish cleanly while the fleet fails behind it.

Anything added there must declare where it belongs:

- Work on shared storage (`/shared`, `/efs`, `/fsx`, `$HOME`, `/opt/parallelcluster/shared`) belongs inside a `[ "$NODE_TYPE" == "HeadNode" ]` guard.  These paths are NFS-exported from the head node, so N compute nodes writing to one file is a concurrent read-modify-write, and a write that root-squash denies aborts the node's own bootstrap.
- Work on node-local state (instance store, local packages, sysctls) belongs outside the guard.
- Packages needed on compute nodes must be installed outside the guard.  The main `apt-get` block is head-node-only.
- `NODE_TYPE` is read from `cfn_node_type` in `/etc/parallelcluster/cfnconfig`, which ParallelCluster writes before any custom action runs.  There is no `PARALLELCLUSTER_NODE_TYPE` environment variable — reading one silently makes every compute node take the head-node path.  `HeadNode` is the default only when the cfnconfig file is absent, which means the script is being re-run by hand off-cluster; a cfnconfig with no `cfn_node_type`, or an unrecognized value, exits 1 rather than skipping every guard.

`tests/test_templates.py::TestPostinstallNodeTypeGating` executes the rendered script for each node type with all external commands stubbed, so a block placed on the wrong side of a guard fails the suite.

**A bootstrap failure whose log ends on a cheerful-looking line:** `cfn-init` captures **stdout only** — a failing command's `stderr` is written nowhere.  So the last line of `cfn-init-cmd.log` is routinely the successful-looking start of the step that failed: `Attempting uninstall: requests` for a pip failure, a list of successful `set on hold` lines for an `apt-mark` exit 100, the `luarocks` download banner for a compiler error.  Read the last line as *where* execution stopped, never as *why*.  To get the reason, re-run the same command by hand on the node (`aws ssm start-session --target <instance-id>`, then execute the rendered `/opt/parallelcluster/scripts/...` step or the individual command) and read its stderr directly.  A related consequence: any block in the toolkit's own scripts that runs without `set -x` — the monitoring wrapper, for instance — leaves no trace in the log at all, so absence of a command from `cfn-init-cmd.log` is not evidence it did not run.

**Postinstall appears to do nothing:** Check that `postinstall.<cluster>.sh` in the cluster's S3 bucket is rendered shell and not raw Jinja2.  The toolkit's templates are rendered by a `template:` task in `src/create_pcluster.yml`; only your own `--post_install_script` hook is copied verbatim.  If the two are conflated, nodes run the hook and skip everything the toolkit's script does — Spack, Lmod, the package installs, `/local_scratch`, and the GPU block.  See [Node bootstrap scripts](#node-bootstrap-scripts).

**EBS root volume tagging:** May fail on macOS due to IAM tag permission restrictions.  Build from an EC2 instance to avoid this.

**Interrupted build recovery:** If `make_pcluster.py` is interrupted mid-run, re-run the same command with the same flags.  The tool detects the existing serial file under `active_clusters/<cluster_name>/` and resumes from that identity — all AWS resource names (S3 bucket, IAM role, IAM policy) are re-derived from the same serial number, so no orphaned resources are left behind.

---

## Development

### Running the test suite

```
make test       # pytest — template rendering + unit tests
make lint       # ansible-lint on src/create_pcluster.yml and src/delete_pcluster.yml
make shellcheck # shellcheck on hpc-benchmark/hpc-benchmark.sh
```

`make test` invokes `.venv/bin/python -m pytest` directly, so no manual venv activation is
needed.  The venv must exist (`python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt`)
before running it.  The top-level scripts fire a venv guard at import time; pytest must run
inside `.venv/` or collection fails with `INTERNALERROR: SystemExit`.

macOS needs additional tools on `PATH` to run this suite (a modern `bash`, GNU `coreutils`,
`shellcheck`) — see [Development environment (macOS)](INSTALL.md#development-environment-macos)
in INSTALL.md. Without them, dozens of tests fail with messages like `nproc: command not found`
or a silently wrong result from a bash-version mismatch — none indicate an actual defect.

CI runs all three automatically on every push and pull request.  The `test` job creates `.venv/`
explicitly and invokes `.venv/bin/python -m pytest` directly for the same reason.

Template tests render with the same Jinja settings `ansible.builtin.template` uses —
`trim_blocks=True`, `lstrip_blocks=False` — so what a test asserts on is the file the node
actually receives.  `tests/test_templates.py::TestTheTestEnvironmentMatchesAnsible` reads both
defaults back out of the installed Ansible instead of restating them, and fails if a playbook
task overrides either.

Every OS arm is rendered and executed.  `tests/conftest.py` ships Ubuntu, RHEL 9, and Amazon
Linux 2023 fixtures (plus GPU variants of each), and `TestPackageManagersMatchTheRenderedOs` runs the
rendered `preinstall.j2` and `postinstall.j2` under real `bash` with the package managers
stubbed, asserting on the resulting command trace: no arm may execute the other family's
package manager, and each arm must actually install its own sentinel packages rather than
rendering to nothing.  Adding a `base_os` value means adding a fixture — an unexercised
Jinja2 arm passes every text assertion written against it.

### Integration tests

A live end-to-end smoke test is available at `tests/integration/run_integration_test.sh`.
It provisions a real cluster using your own defaults file, submits a Slurm job, verifies
the output, and tears everything down.

**Integration tests are NOT run by `make test`, `pytest`, or CI.**  They build real
infrastructure and must be invoked manually:

```bash
source .venv/bin/activate

./tests/integration/run_integration_test.sh \
    --az us-east-1a \
    --owner test \
    --email test@example.com \
    --defaults tests/integration/itest_defaults.yml \
    [--profile my-aws-profile] \
    [--keep]
```

The first four flags are all required; the script has no `--help`, and any
unrecognized argument exits 1.  Start from
`tests/integration/itest_defaults.yml.example` — the copy at
`tests/integration/itest_defaults.yml` is gitignored, so account-specific values
are never committed.  A run costs roughly $0.21-$0.34 in EC2 charges.

See [`tests/integration/README.md`](tests/integration/README.md) for the flag
reference, prerequisites, cost derivation, log paths, and exit codes.

### Known ansible-lint warnings

`make lint` exits 0 but emits a small number of warnings that are intentional and safe to ignore:

| Warning | Reason |
|---|---|
| `yaml[line-length]` — ssh/chown/cp commands | One-liners that are 162 chars (2 over limit); splitting would harm readability |
| `no-changed-when` | `pcluster` CLI commands are inherently stateful; `changed_when` on every poll would be misleading |
| `ignore-errors` | Intentional on cleanup tasks (S3 bucket, SNS topic, IAM role) that may not exist at delete time |
| `no-handler` | Deliberate pattern; notify/handler would require restructuring without benefit |

These are all tracked in `.ansible-lint` under `warn_list` with the same rationale.

---

## Things to Do

Potential future improvements, roughly ordered by impact:

### Modules / Software

- **EasyBuild easyconfig workflow** — accept a user-supplied list of EasyBuild module specs, download matching easyconfigs from the EasyBuild repository, build and install the modules on the head node, and run a smoke-test job for each one via Slurm.  Useful for validating that a new OS or instance type can successfully build a site's standard software stack.

### Architecture

- **Terraform / CDK parity** — the toolkit is Ansible-native.  A Terraform or AWS CDK implementation of the same lifecycle (`make` / `kill` / `access`) would fit more naturally into infrastructure-as-code pipelines that already use those tools.

---

## Disclaimer

This software is licensed under the Apache License, Version 2.0 with the Commons Clause restriction.  You may use, modify, and distribute it freely, but you may not sell it or offer it as a commercial product or service without the explicit written consent of Rodney Marable.  See `LICENSE` for full terms.

By using this software:

- You accept all potential risks involved with your use of this Open Source software.
- You agree that the author shall have no responsibility or liability for any losses or damages incurred in conjunction with your use of this Open Source software.
- You acknowledge that bugs may still be present, unexpected behavior might be observed, and some features may not be completely documented.

**This software is authored by Rodney Marable in his individual capacity and is neither endorsed nor supported by Amazon Web Services.**  You cannot create cases with AWS Technical Support or engage AWS support engineers in public forums if you have any questions, problems, or issues using this software.

> "Play at your own risk!" — Planet Patrol

---

## Reporting Bugs & Requesting Features

https://github.com/rmarable/ParallelClusterMaker/issues

Pull requests welcome: https://github.com/rmarable/ParallelClusterMaker/pulls
