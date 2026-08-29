# ParallelClusterMaker

This Open Source CLI toolkit automates creation and destruction of [AWS ParallelCluster v3](https://github.com/aws/aws-parallelcluster) stacks.  It lets researchers and engineers stand up a working HPC cluster on AWS without deep infrastructure expertise.

This codebase was co-written with [Claude Code](https://claude.ai/code) (Anthropic). AI-assisted contributions are accepted under a public policy ([AI_POLICY.md](AI_POLICY.md)): the tool or model must be disclosed, and a human remains responsible for every line submitted.

---

## Use Cases

- **Training ML models at scale** — multi-node distributed training on GPU queues (`g4dn`, `g4ad`, `g5`, `g5g`, `g6`, `p3`, `p3dn`, `p4d`, `p4de`, `p5`), with EFA and EFA-GDR for inter-node collective communication and FSx for Lustre for high-throughput data loading.
- **Traditional HPC and scientific computing** — CFD, molecular dynamics, genomics, and other tightly-coupled MPI workloads, using EFA and dynamic placement groups for a low-latency interconnect.
- **Cluster and instance-type validation** — the built-in STREAM, OSU MPI, IOR, and HPCG benchmark suite measures memory bandwidth, MPI latency/bandwidth, filesystem I/O, and floating-point performance before committing a production workload to a given cluster shape.
- **Cost-sensitive batch and embarrassingly parallel work** — parameter sweeps, Monte Carlo simulation, rendering — using spot capacity, the default, where jobs can tolerate interruption.
- **On-demand research computing** — teams without dedicated infrastructure or a standing DevOps function can stand up a cluster for a research sprint and tear it down when finished, rather than carrying always-on cost.

---

## Installation

There are three ways to install this toolkit. **Start with the CLI** —
both MCP surfaces are deployed from that checkout and need it first. The
two MCP options are independent of each other: add either, both, or
neither.

| | What it is | Who reaches it |
|---|---|---|
| **1. [The CLI](#1-the-cli-local-use)** | `make_pcluster.py` and the other entry points, run from your terminal | You, on this machine |
| **2. [The MCP server in Claude Code](#2-the-mcp-server-in-claude-code)** | The same tools over stdio, on your machine | An agent in your terminal |
| **3. [The MCP server in claude.ai](#3-the-mcp-server-in-claudeai)** | The same tools behind API Gateway and Cognito, in your AWS account | A browser session |

**All three run against your own AWS account.** There is no hosted service,
and nothing is shared with anyone — option 3 deploys the endpoint into your
account, where you own it and tear it down.

### 1. The CLI (local use)

The base install, and a prerequisite for the other two.

```bash
git clone https://github.com/rmarable/ParallelClusterMaker.git
cd ParallelClusterMaker
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Python 3.12 specifically** — `aws-parallelcluster` does not support 3.13
or 3.14. You will also need **Node.js** on `PATH`, which ParallelCluster
shells out to for CloudFormation synthesis.

Then set up the AWS account, once:

```bash
./generate_operator_policy.py --bootstrap
```

This is the only setup step the toolkit cannot do for itself — a tool able
to grant itself IAM permissions has no ceiling — and everything else it
creates as it goes. It is idempotent, so run it again after a `git pull`
that adds a grant. `--dry-run` shows what it would change; `--no-attach`
creates the policy without attaching it.

See [INSTALL.md](INSTALL.md) for the full prerequisites, what that policy
grants, and the VPC tagging step. Then go to
[Building a Cluster](#building-a-cluster).

### 2. The MCP server in Claude Code

Runs on your machine over stdio and answers only to the agent you attach it
to. Nothing is deployed to AWS and nothing is reachable from the network.

```bash
claude mcp add parallelclustermaker \
  -e PYTHONPATH="$(pwd)" \
  -- "$(pwd)/.venv/bin/python" -m mcp_server.server
```

Run it **from the repo root** — the shell expands `$(pwd)` before Claude
Code sees it, so what gets stored is an absolute path. Then restart Claude
Code and run `/mcp` to confirm the tools are listed.

Full detail, including the three things that will waste your time:
[MCP Server](#mcp-server).

### 3. The MCP server in claude.ai

Puts the same tools behind API Gateway and Cognito **in your AWS account**,
so a browser session can reach them. One command:

```bash
./deploy_mcp.py --bootstrap --create-user you@example.com
```

Then paste the MCP endpoint it prints into claude.ai under **Customize →
Connectors → `+`**, and sign in with the user it created.

Full detail, including the container tier and teardown:
[Deploying the remote transport](#deploying-the-remote-transport).

---

## Features

**Scheduling and compute** — see [Networking and Compute](#networking-and-compute)

- Slurm job scheduling
- Separate CPU and GPU queues — the GPU queue exists only when `--gpu_instance_type` is set
- Multi-instance-type queues via `--compute_instance_type` and `--gpu_instance_type`, each accepting a comma-separated list
- Separate instance types and EBS configurations for the head node, CPU queue, and GPU queue
- Optional login node pool, keeping general users off the head node (`--enable_loginnode`); see [Login Nodes](#login-nodes)
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
- Slurm job accounting **on by default** (`--enable_slurm_accounting`) — a local MariaDB on the head node so `sacct` reports job history; see [Job Accounting](#job-accounting)
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

Most optional parameters have hardcoded defaults, and all of them can be persisted in a YAML defaults file.  Where the two differ, the table below says so — the hardcoded default is what you get with no defaults file present.  `pcluster_defaults.yml` is the template — **copy the file before use**; the tracked version is shared and may be overwritten by updates, so never pass that one directly.  The most commonly referenced defaults:

| Parameter | Default |
|---|---|
| `base_os` | ubuntu2404 (hardcoded); `pcluster_defaults.yml` ships ubuntu2404arm to match the Graviton head node.  Valid: `ubuntu2204`, `ubuntu2404`, `ubuntu2204arm`, `ubuntu2404arm`, `rhel9`, `rhel9arm`, `alinux2023`, `alinux2023arm` |
| `scheduler` | slurm |
| `headnode_instance_type` | **no hardcoded default — required unless a defaults file supplies one**; `pcluster_defaults.yml` ships c8g.xlarge |
| `compute_instance_type` | hardcoded default is empty (no CPU queue); `pcluster_defaults.yml` ships c8g.2xlarge, c7g.2xlarge, c6g.2xlarge (comma list) |
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
| `enable_slurm_accounting` | **true** |
| `enable_hpc_benchmarks` | false |
| `enable_efa` | false |
| `enable_gpu` | derived from `gpu_instance_type` — not user-settable |
| `head_node_bootstrap_timeout` | 2100 s, raised automatically when `enable_efs` or `enable_fsx` is true |

Name a file `<cluster_name>_defaults.yml` and it is loaded automatically — no flag needed, by `make_pcluster.py` and by the MCP server alike. Use `--use_defaults=FILE` to load a differently-named file instead. CLI arguments always take precedence over either.

```
# Copy the template and customize it for your cluster
cp pcluster_defaults.yml my-cluster_defaults.yml
# Pass it at runtime
./make_pcluster.py -N my-cluster -O rmarable -E rmarable@example.com -A us-east-1a \
    --use_defaults=my-cluster_defaults.yml
```

Naming the file `<cluster_name>_defaults.yml` keeps cluster namespaces scoped and is what makes the automatic load work — the example above only needs `--use_defaults` because the file is named for a different cluster.  Keys the build does not use (`delete_s3_bucketname`, which `kill_pcluster.py` reads) are ignored rather than rejected, so one file can serve both.  Loading `pcluster_defaults.yml` directly is allowed but warns.

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
./make_pcluster.py -A us-east-1a -O rmarable -E rodney.marable@gmail.com -N enkidu \
    --base_os=rhel9 --headnode_instance_type=c5.xlarge \
    --compute_instance_type=c5.2xlarge
```

Amazon Linux 2023 cluster on Graviton (login user is `ec2-user`):
```
./make_pcluster.py -A us-east-1a -O rmarable -E rodney.marable@gmail.com -N ninlil \
    --base_os=alinux2023arm --headnode_instance_type=c8g.xlarge \
    --compute_instance_type=c8g.2xlarge
```

Building from a custom AMI (must match base_os):
```
./make_pcluster.py -N starscream -O rmarable -E rodney.marable@gmail.com -A us-west-2a \
    --enable_fsx=true --custom_ami=ami-123456789abc --base_os=ubuntu2204
```

A new stack typically takes approximately 30–35 minutes to build.  Two measured `us-east-1` builds of the same cluster shape (`c5.xlarge` head node, two queues, `ubuntu2404`): **34m 24s** with a 1200 GB FSx for Lustre filesystem, and **21m 14s** with EFS instead.  Actual time depends on region, instance type availability, and which shared filesystems are enabled — FSx dominates when it is on, because its provisioning sits on the head node's critical path (see Troubleshooting).

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

If the cluster has a [login node](#login-nodes) (`--enable_loginnode=true`), `access_cluster.py` connects there by default instead of the head node.  Pass `-L`/`--login_node` or `-H`/`--head_node` to choose explicitly:

```
./access_cluster.py -N pcluster-test-01 -L    # login node
./access_cluster.py -N pcluster-test-01 -H    # head node, even if a login node is enabled
```

`-L` on a cluster built with `--enable_loginnode=false` (or an older cluster from before this feature existed) fails with a clear error rather than connecting to the head node silently.

---

## Deleting a Cluster

```
./kill_pcluster.py -N CLUSTER_NAME -O OWNER -A AZ
```

Teardown takes 15–20 minutes.  The cluster's EFS and FSx filesystems are deleted by CloudFormation, which ParallelCluster configures with a `Delete` deletion policy at *creation* time — teardown cannot preserve them.  The cluster's own S3 bucket (`parallelclustermaker-<serial>`) is the one resource teardown controls, and it holds the rendered cluster config and the bootstrap scripts.  Benchmark results do **not** live there — they are synced to the long-lived `parallelclustermaker-results-<account-id>-<region>` bucket, which teardown never touches — so keeping the per-cluster bucket is only useful for inspecting what a build actually deployed:

```
./kill_pcluster.py -N pcluster-test-01 -O rmarable -A us-east-1a \
    --delete_s3_bucketname=false
```

Teardown is always manual and at your discretion — nothing in this toolkit schedules a cluster's destruction.  Idle compute cost is bounded instead by `scaledown_idletime`: ParallelCluster terminates compute nodes that sit idle longer than that, scaling the fleet to zero on its own.  A head node left running still bills, so run `kill_pcluster.py` when you are finished with a cluster.

### When Cleanup Leaves Something Behind

Once the CloudFormation stack is gone, teardown removes everything the build created outside it.  Some steps always run — the five managed IAM policies, the cluster IAM role and its instance profile, and the SNS topic — while the rest are gated on the features the cluster actually used: the S3 bucket, the FSx hydration policy, the Grafana SSM parameter and the monitoring IAM policy, and the external NFS security group.

A second group is gated on positive confirmation that the stack is really gone, because these are the only remaining ways into a running head node: the EC2 keypair, the local `.pem`, the Secrets Manager secret holding the SSH key, the `active_clusters/<cluster>/` directory, and the cluster's record and configuration in the shared store.  A wait that merely timed out does not satisfy that gate, so a still-running cluster keeps its credentials.

Each step tolerates its own failure so that one AWS error cannot abandon the others — but every ignored failure is collected and reported, and teardown then exits non-zero:

```
================================================================================

Initiated shutdown: 2026-08-03 @ 01:33:12
Completed shutdown: 2026-08-03 @ 01:41:48

Cluster osiris has been deleted.
2 cleanup step(s) FAILED.
The following resources are still in the account and must be
removed by hand -- re-running kill_pcluster.py will not retry them
once osiris.serial has been deleted:

  - Detach and delete managed IAM policies associated with the cluster stack -- AccessDenied
  - Delete the cluster IAM role and its instance profile -- AccessDenied

Retained in the account on purpose (not failures, still billing):

  - CloudWatch log group /aws/parallelcluster/osiris-202608030133 (expires after 30 days)
================================================================================
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
5. **Slurm** — `sinfo -h -o '%D %T'` reports at least one usable node.  Exit status alone is not the criterion: a cluster whose entire fleet is `down`, `drained` or `unknown` answers successfully, and that is exactly the state a bootstrap failure leaves behind.  Empty or unparseable output fails.  A partly degraded fleet passes with a note naming the counts
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
  [PASS] Slurm
  [PASS] postinstall complete
  [PASS] S3 bucket: parallelclustermaker-my-cluster-00000000000000

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

1. **CloudWatch: head node bootstrap logs** — last N lines from `cfn-init`, `cloud-init-output`, and `cinc_client` streams.  PCluster appends the stack's creation timestamp to the log group name (`/aws/parallelcluster/<cluster_name>-<YYYYmmddHHMM>`), so the group is discovered by prefix rather than constructed; the selected group name is printed above the streams.  Rebuilds of the same cluster name leave older groups behind — the group survives `delete-cluster` by design, since it is the only surviving record of a failed build — and the newest is used.  The toolkit sets `RetentionInDays: 30` in the cluster config, so a group's events age out after 30 days rather than PCluster's default 180.  Requires `logs:DescribeLogGroups`, `logs:DescribeLogStreams`, `logs:FilterLogEvents`, and `logs:GetLogEvents` on the operator identity (all included in the operator policy).  Pass `--no_cw` to skip this section if permissions are unavailable.
2. **Slurm node states** — `sinfo -N -l` output; nodes not in `idle`/`mix`/`alloc` are annotated with `<-- not idle`.
3. **Recent Slurm job failures** — `sacct` filtered to `FAILED`, `CANCELLED`, `TIMEOUT`, `NODE_FAIL` states.  Prints a note if no results.  ParallelCluster itself does not enable Slurm accounting storage, so `sacct` has nothing to return on a cluster built with `--enable_slurm_accounting=false`, or on one built before this toolkit enabled it by default — see [Job Accounting](#job-accounting).
4. **Local log tails** — last N lines of `/var/log/parallelcluster/slurm_resume.log`, `slurm_suspend.log`, `/var/log/cinc/client.log`, `/var/log/cloud-init-output.log`.
5. **Postinstall marker** — confirms `/opt/parallelcluster/shared/custom_action_done` is present; prints the cluster serial number for cross-referencing S3 benchmark results.

Example output:

```
Diagnosing cluster: my-cluster  (us-east-1)
  serial: 20260804-abc123

=== CloudWatch: head node bootstrap logs ===

  log group: /aws/parallelcluster/my-cluster-202608041130

  --- cfn-init ---
  2026-08-04 17:24:45  ConfigSet: default
  2026-08-04 17:24:49  Install packages: ok
  ...

=== Slurm node states (sinfo -N -l) ===

  NODELIST  NODES PARTITION    STATE CPUS MEMORY REASON
  compute-1     1 cpu          idle     4   8000 none
  compute-2     1 cpu          drain    4   8000 maintenance   <-- not idle

=== Recent Slurm job failures (last 24h) ===

  No failed jobs in the last 24h
  (If this is unexpected, the cluster was built with
  `--enable_slurm_accounting=false`, or predates it being the default — see
  [Job Accounting](#job-accounting).)

=== Local log tails (last 30 lines each) ===

  --- /var/log/parallelcluster/slurm_resume.log ---
  ...

=== Postinstall marker ===

  [PASS] /opt/parallelcluster/shared/custom_action_done  (serial: 20260803-abc123)
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

### Node-Local Scratch (`/local_scratch`)

Local to a single instance, not shared.  `templates/postinstall.j2` creates `/local_scratch` as a sticky-bit directory on the root EBS volume and symlinks `/scratch` to it.  When `enable_gpu` is `true` and NVMe instance store devices are present, `/local_scratch` is backed by those instead — one device is formatted XFS, several are assembled into a RAID0 array.  See [GPU](#gpu) for the device detection logic.

Postinstall is registered as an `OnNodeConfigured` custom action on the head node and on every compute queue (`templates/config.pcluster.j2`), so `/local_scratch` is created on every instance.  This matters for the instance-store path: NVMe instance store exists only on compute instances, so a head-node-only registration leaves the RAID0 block unreachable in practice.  See [Node Bootstrap Scripts](#node-bootstrap-scripts) for how the script gets to the node.

Data in `/local_scratch` does not survive instance termination, and compute nodes terminate on scale-down.

### EFS (`/efs`)

Enable with `--enable_efs=true`.  Mounted at `/efs` on all instances.  Costs almost nothing in build time: measured on a `generalPurpose`/`bursting` filesystem with one mount target, the filesystem completed in 4 seconds and the mount target in 1m 33s, both finishing well before the Route53 zone and the compute-fleet nested stack that actually gate the head node's launch.  A multi-AZ cluster creates one mount target per subnet and has not been timed.  Configure with `--efs_encryption`, `--efs_performance_mode` (`generalPurpose` or `maxIO`), and `--efs_throughput_mode` (`bursting`, `provisioned`, or `elastic`).

### FSx for Lustre (`/fsx`)

Enable with `--enable_fsx=true`.  Mounted at `/fsx`.  `--fsx_size` must be a positive multiple of 1200 GB; the default and minimum is 1200.  `--fsx_chunk_size` (the S3 imported-file chunk size, default 1024 MB) must fall between 1,024 MB (1 GB) and 512,000 MB (500 GB).

#### S3 Hydration and Dehydration

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

Both buckets are validated before the build starts with `head_bucket`, which reports a 403 distinctly so that an access-denied bucket policy is not mistaken for a missing bucket.  The **import** prefix must also contain at least one object, so an empty or misspelled hydration source fails immediately rather than at FSx creation time.  The **export** prefix is deliberately not checked and is never even listed: it is a destination, and on a first dehydration it is empty by definition — AWS's own default export path does not exist until the filesystem does.

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
- **ParallelCluster usually claims these devices first.**  The `aws-parallelcluster-environment::ephemeral_drives` cookbook runs *before* `OnNodeConfigured` and, on any instance type with instance store, puts every such device into an LVM physical volume, formatted `ext4` and mounted on `/scratch`.  On that common case the toolkit's block correctly does nothing and `/local_scratch` is a symlink to PCluster's `/scratch` — verified on a live `g4dn.xlarge`.  Without the holders/`blkid` filters, `mkfs.xfs` on a claimed device fails with `Device or resource busy`, which fails the node
- Single unclaimed device: formatted XFS, mounted at `/local_scratch` with `noatime,nodiratime,nofail`
- Multiple unclaimed devices (`p4d.24xlarge` has 8×1000 GB, `p5.48xlarge` has 8×3800 GB, per `aws ec2 describe-instance-types`): RAID0 via `mdadm`, mounted at `/local_scratch`
- No instance store present (e.g. `p3.2xlarge`): `/local_scratch` remains a sticky-bit directory on the root EBS volume
- `htop` installed by the GPU block itself, since the main package block is head-node-only and does not run on compute nodes; `nvtop` is head-node-only because it lives outside the default repositories (`multiverse` on Ubuntu, EPEL on RHEL 9) and a compute node's package index is whatever the AMI shipped.  A compute node refreshes its index first (`apt-get update` / `dnf makecache`) because `OnNodeStart` — and therefore preinstall's refresh — never runs there.  Both installs are non-fatal (`|| echo "WARNING: ..."`), the only ones in the file: they are diagnostics nothing in the job path imports, and one transient mirror outage would otherwise count toward the 10-failure protected-mode threshold and cost the entire stack

**EFA GPUDirect RDMA (GDR):**  When `--enable_efa=true` and any GPU queue instance type is in the `p4d`, `p4de` or `p5` family, `GdrSupport: true` is added to the GPU queue EFA config automatically.  The test is on the family prefix, not on a specific size, so every member of those families qualifies.

**GPU volume settings:**  The GPU queue uses its own root volume parameters (`--gpu_root_volume_size`, `--gpu_root_volume_type`, `--gpu_root_volume_iops`, `--gpu_root_volume_throughput`) independent of the CPU queue.

**CUDA / drivers:**  PCluster's official deep learning AMIs include NVIDIA drivers.  Pass `--custom_ami=<ami-id>` to use a pre-built DLAMI or a custom AMI with pinned driver versions.

### EFA

Enable with `--enable_efa=true`.  Supported on every `base_os` value the toolkit accepts.  Requires a supported instance type (c5n.18xlarge, hpc6a.48xlarge, hpc7a.96xlarge, hpc7g.16xlarge, etc.).  A dynamic placement group is created automatically.

EFA (Elastic Fabric Adapter) is an OS-bypass network interface that gives tightly-coupled, latency-sensitive MPI jobs a much faster interconnect than standard TCP/IP networking — closer to InfiniBand than to a regular NIC.  EFA matters most for multi-node jobs with heavy collective communication (large all-reduce, halo exchanges, etc.); a single-node job, or one bound by disk or ordinary network I/O, sees little benefit.  On p4d, p4de, and p5 instances, EFA-GDR (GPUDirect RDMA) is enabled automatically, letting the network adapter read and write GPU memory directly instead of staging through host memory — which matters for multi-node GPU jobs.

**Not yet verified on hardware.**  EFA-enabled builds have not been run against real instances; the config generation and instance-type gating are unit-tested, but the actual interconnect behavior is unconfirmed.

### Placement Groups

Enable with `--placement_group=DYNAMIC`.  PCluster creates one managed cluster placement group per queue, applied to the CPU and GPU compute queues only.  The head node is never placed in a placement group.  `--enable_efa=true` sets this to `DYNAMIC` automatically when the setting is still `NONE`.

### HyperThreading

Disable with `--hyperthreading=false`.

HyperThreading (Intel) / SMT (AMD) exposes each physical CPU core as two logical vCPUs sharing the same execution resources.  Many HPC workloads — anything compute- or memory-bandwidth-bound rather than I/O-bound — run faster with HyperThreading disabled, since two threads contending for one core's resources often costs more than the extra thread gains.  Disabling HyperThreading also changes the per-node rank count Slurm submits with: `cpu_ranks_per_node` divides vCPUs by `DefaultThreadsPerCore` instead of always halving (see [Job Submission](#job-submission)).

### Login Nodes

Enable with `--enable_loginnode=true` (default: `false`).  AWS ParallelCluster's `LoginNodes` feature adds a separate, right-sized instance pool for interactive logins and job submission, keeping general users off the head node — which carries elevated IAM permissions and runs cluster orchestration.

```
./make_pcluster.py -N prod01 -O rmarable -E rmarable@example.com -A us-east-1a \
    ... \
    --enable_loginnode=true \
    --loginnode_instance_type=c5.xlarge \
    --loginnode_count=2
```

- **`--loginnode_instance_type`** falls back to an architecture-aware default when unset (`c8g.xlarge` on Graviton `base_os` values, `c5.xlarge` on x86_64) — matching whatever `base_os` you built with is required, same as every other node type.
- **`--loginnode_count`** (default `1`) sizes a static, always-on pool — there is no autoscaling to zero, so this is a real ongoing cost distinct from the CPU/GPU compute queues, which do scale down when idle.
- **The root volume can be neither sized nor encrypted through this toolkit.**  AWS ParallelCluster exposes no `RootVolume`/`LocalStorage` key for `LoginNodes/Pools` — it always uses the AMI default.
- **IAM is `ComputeNode-Base`**, the same least-privilege policy the compute/GPU queues get — never the head node's `InstanceRole`.
- **A login-node bootstrap failure does not fail the cluster build.**  Login nodes run behind their own Auto Scaling Group and load balancer health check, architecturally separate from the head node's `CreationPolicy`/wait condition and from Slurm's compute-fleet protected mode — a broken postinstall script produces a running cluster with an unhealthy, continuously-replaced login node pool instead of a failed `pcluster create-cluster` call.
- **Enabling this only makes a more-secure *option* available.**  Nothing prevents an operator from still using `-H`/direct SSH to the head node — the actual security benefit depends on people using `-L`.

Connect with `access_cluster.py -L` (or `-H` for the head node explicitly — see [Accessing a Cluster](#accessing-a-cluster)).  When `--loginnode_count` is greater than 1, `-L` connects to an unspecified member of the pool, not a chosen one; per-node targeting is not yet implemented.

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

### Listing Queues

```
./manage_pcluster_queue.py -N <cluster_name> -A list
```

### Adding Queues

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

### Removing a Queue

```
./manage_pcluster_queue.py -N osiris -A remove -Q compute-spot-overflow
```

### Applying the Change

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

### Operating Systems

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
- `nvtop` (GPU clusters) is installed on the head node only, on Ubuntu and RHEL 9 — the package sits outside the default repositories in each, and the operator logs into the head node rather than a compute node.  Amazon Linux 2023 skips `nvtop` entirely, since the package isn't available there.
- On RHEL 9, `bc` is installed explicitly because Lmod's `./configure` hard-quits without `bc` (`You must have bc in your path. Quitting!`) rather than degrading.  `bc` is on the Ubuntu and Amazon Linux 2023 package lines too, deliberately: those AMIs happen to ship `bc` incidentally, and depending on what a base image carries by accident is how the RHEL gap stayed hidden.  (Upstream's own monitoring installer claims `bc` is absent from the default al2023 repos; the repo metadata says otherwise on both architectures.)
- The dnf kernel exclusions cover both Lustre spellings — `kmod-lustre*` and `lustre-client*` — because the two distros name the client differently.  Amazon Linux 2023 has no `kmod-lustre*` package at all, so the RHEL glob alone would silently protect nothing there.

**Both Amazon Linux 2023 arms are validated on live cluster builds.**  `alinux2023` reached `CREATE_COMPLETE` on a `c5.xlarge` head node with EFS, a `c5.2xlarge` CPU queue, two `g4dn.xlarge` GPU nodes, benchmarks, and monitoring; `alinux2023arm` reached `CREATE_COMPLETE` on a `c8g.xlarge` head node with EFS, a 1200 GB FSx for Lustre filesystem, a `c8g.2xlarge` CPU queue, benchmarks, and monitoring.  Every package claim above was confirmed against the head node's and every compute node's own bootstrap logs on both architectures, not merely against the stack's exit status: `luarocks`, `epel`, `tcllib`, and `nvtop` appear nowhere; the three Lua rocks install as core-repo RPMs; `bc` is already present and Lmod's `./configure` finds it; and `dnf update` upgrades a single package with no kernel, dracut, or initramfs activity.  The Docker Compose CLI plugin is staged and checksum-verified per architecture — `docker-compose-linux-x86_64-v2.29.7` and `docker-compose-linux-aarch64-v2.29.7` respectively — and upstream's `github.com` download is removed from the extracted tree on both.

**Both RHEL 9 arms are validated on live cluster builds.**  `rhel9` reached `CREATE_COMPLETE` on a `c5.xlarge` head node with EFS, a CPU queue, a `g4dn.xlarge` GPU queue, benchmarks, and monitoring; `rhel9arm` reached `CREATE_COMPLETE` on a `c8g.xlarge` head node with EFS, a 1200 GB FSx for Lustre filesystem, benchmarks, and monitoring.  The whole RPM bootstrap path is confirmed on both architectures: EPEL by release-RPM URL, the CodeReady Builder repository id, all three luarocks rocks compiling against `lua-devel` with no separate header package, and `dnf update` upgrading `dracut` itself while installing zero `kernel*` packages and regenerating no initramfs.  All eight pip pins resolved from `manylinux_2_17_aarch64` wheels on Graviton.

### Node Bootstrap Scripts

Two stages run on every node, in this order:

1. **The toolkit's own scripts** — `templates/preinstall.j2` and `templates/postinstall.j2`, rendered per cluster with that cluster's variables (OS, storage layout, `pkg_dir`, GPU flags) and uploaded to the cluster's S3 bucket as `preinstall.<cluster>.sh` and `postinstall.<cluster>.sh`.  These are the toolkit's own work: base packages, Spack, Lmod, `/local_scratch`, the benchmark suite, and the GPU block.
2. **Your hook** — the script named by `--pre_install_script` / `--post_install_script`, copied verbatim and uploaded under its own basename.  Defaults are `scripts/pre-deployment.sh` and `scripts/post-deployment.sh`, both no-op placeholders.  Put site-specific customization here; do not edit the toolkit templates to add it.

The stages are wired as a PCluster `Sequence`, so stage 2 runs only if stage 1 succeeded.  `OnNodeStart` (preinstall) runs on the head node only — repeating the Python/pip/AWS CLI install on every scale-up event would add boot latency to every compute node for no benefit.  `OnNodeConfigured` (postinstall) runs on the head node and on every compute queue, since that is where node-local work like `/local_scratch` belongs.  When `--enable_monitoring=true` the monitoring installer is appended as a third stage.

Paths passed to `--pre_install_script` / `--post_install_script` are relative to the repository root.

#### The Kernel Is Never Upgraded

`preinstall.j2` upgrades the AMI's packages — `apt-get dist-upgrade` on Ubuntu, `dnf update` on RHEL 9 — but holds back the running kernel along with the out-of-tree modules built against it.  Ubuntu does this with `apt-mark hold` on the installed `linux-*` packages; RHEL 9 with `--exclude='kernel*' --exclude='kmod-lustre*' --exclude='efa*'`.  Two independent reasons:

- **A kernel replacement triggers an initramfs rebuild whose runtime is unbounded**, and it runs inside the window CloudFormation gives the head node to signal success.  Real builds failed this way on both families: on the PCluster AMI of the day a full upgrade crossed a kernel boundary and was still rebuilding when the wait condition expired, and on the RHEL 9 AMI a full update crossed `5.14.0-611.55.1.el9_7` → `5.14.0-687.30.1.el9_8` with dracut still running when CloudFormation gave up.
- **PCluster's AMI ships EFA and Lustre kernel modules built against the kernel it boots.**  Replacing that kernel without rebuilding them risks losing the interconnect or the Lustre client on the next boot.

The two mechanisms are equivalent in effect but not in shape: `apt-mark` pins packages by name, so the Ubuntu path first enumerates the installed `linux-*` packages and filters them to the ones dpkg reports as actually installed.  `--exclude` takes a glob resolved when dnf builds the transaction, so the RHEL 9 path needs no enumeration and holds on every AMI revision regardless of which updates happen to be pending — every RHEL 9 kernel subpackage (`kernel`, `-core`, `-modules`, `-modules-core`, `-modules-extra`, `-headers`, `-devel`, `-tools`) matches `kernel*`.

The upgrade itself is deliberately kept, because `preinstall.j2` installs the Python development headers and `numpy`/`scipy`/`pandas`/`matplotlib` compile from source wherever pip finds no wheel — which every `*arm` value of `base_os` can hit.  If you add a package that needs a newer kernel, pin it in your own `--pre_install_script` hook and reboot deliberately outside the bootstrap window; do not remove the exclusions.

### Spack + Lmod

Every stack includes [Spack](https://spack.io/) and [Lmod](https://github.com/TACC/Lmod) for HPC software module management.  Spack is cloned into `<shared>/pkg/spack`, where `<shared>` is the first available of `/fsx`, `/efs`, `/nfs`, or the shared EBS mount.

Both are built on the head node only.  Compute nodes mount the same shared storage and inherit the installation, so having every scaling node repeat the clone and `chown -R` would be wasted boot time on a tree that is already populated.

**`MODULEPATH` comes from Spack, not from Lmod's compiled-in root.**  Lmod is configured with `--with-module-root-path=<shared>/pkg/modulefiles`, but that setting is only ever read by Lmod's `init/profile` script, and postinstall installs `init/sh` instead — which defines the `module`, `ml`, and `clearMT` shell functions and sets `MODULESHOME`, with no reference to `MODULEPATH` at all.  What populates `MODULEPATH` on a login shell is `/etc/profile.d/lmod_spack.sh` sourcing Spack's `setup-env.sh`, which appends Spack's own module roots.  Consequently `<shared>/pkg/modulefiles` is not created by the install and will not exist on a fresh cluster — nothing reads that path, so this is expected rather than a broken installation.  If you want to hand-place modulefiles outside Spack, create that directory yourself and add its path to `MODULEPATH` (or to `LMOD_SITE_MODULEPATH`) from your own `--post_install_script` hook.

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

### Job Accounting

**On by default.**  Disable with `--enable_slurm_accounting=false`.
Installs MariaDB and `slurmdbd` on the head node and points Slurm's
accounting storage at them, so `sacct` returns real job history:

```
$ sacct --starttime=now-1hour
JobID           JobName  Partition    Account  AllocCPUS      State ExitCode
------------ ---------- ---------- ---------- ---------- ---------- --------
1             acctproof    compute acctproof3          1  COMPLETED      0:0
1.batch           batch            acctproof3          1  COMPLETED      0:0
```

Without it, ParallelCluster leaves accounting storage disabled and `sacct`
answers `Slurm accounting storage is disabled`.  That also affects
`diagnose_pcluster.py`, whose job-failure check has nothing to read, and
the `run_readonly_slurm_command` MCP tool, which deliberately does not
offer `sacct` for the same reason.

**The accounting data dies with the cluster.**  The database lives on the
head node's root volume and teardown destroys it; there is no export step
and nothing is synced to S3.  If you need job records to outlive a cluster,
copy them off before deleting it.  (Benchmark results are different — those
go to a long-lived bucket; see [HPC Benchmarks](#hpc-benchmarks).)

**What it costs, and why it is on anyway.**  It puts a database on a node
that already runs `slurmctld`, so it is not free.  Measured on a
`c5.xlarge` head node: `mariadbd` holds 100–120 MB RSS and `slurmdbd`
13 MB — less than the CloudWatch agent already running there — and the
install added to a postinstall phase that took 86 s in total, against a
head node bootstrap of 619 s and ParallelCluster's 2100 s bootstrap
budget.  Turn it off with `--enable_slurm_accounting=false` if you want
the head node doing nothing but scheduling.

#### Sizing

MariaDB is tuned for a database co-resident with the scheduler rather than
a dedicated server, and both values scale with the head node:

| Setting | Value |
|---|---|
| `innodb_buffer_pool_size` | 10% of RAM, floored at 128M, capped at 4096M |
| `innodb_log_file_size` | 25% of the buffer pool, capped at 256M |

Upstream Slurm recommends 5–50% of memory and *at least* 4 GiB for the
buffer pool, with the redo log at 25% of it.  That is sizing advice for a
dedicated SQL server.  The 4096M ceiling is Slurm's own 4 GiB minimum; the
256M log cap is this toolkit's deviation, because the uncapped ratio gives
a 1024M redo log — preallocated on disk and replayed on crash recovery —
for a database measured at roughly 97 MB.  Raising either currently means
editing `templates/postinstall.j2`.

#### Retention

Slurm purges nothing by default, so a database with no policy grows without
bound on a root volume sized for an operating system.  The defaults here:

| Records | Kept |
|---|---|
| Jobs, steps, events, reservations, suspends | 1 month |
| Transactions and the rolled-up usage reports are built from | 12 months |

Nothing is archived.  Slurm's `ArchiveDir` defaults to `/tmp`, and an
archive nobody collects before the head node is torn down is not a backup.

#### What happens if it fails

Every step is non-fatal by design, and that is worth understanding rather
than trusting.  Once `slurm.conf` names an accounting host, `slurmctld`
blocks indefinitely when that host does not answer — so a half-configured
cluster is worse than one with no accounting at all.  Any failure in the
install leaves `slurm.conf` untouched and the cluster builds normally,
with a warning in the bootstrap log.

`slurm.conf` is edited by a deferred one-shot unit that waits for
ParallelCluster's own bootstrap to finish, not by the post-install script
itself, so **accounting comes up shortly after the stack reaches
`CREATE_COMPLETE` rather than at the same moment**.  If `slurmctld` does
not come back answering after the edit, the unit restores the previous
`slurm.conf` and restarts it, leaving you a working cluster without
accounting.  Check `journalctl -u pcm-slurm-acct` on the head node.

The cluster registers itself with the accounting database when `slurmctld`
starts; the login user and, if it exists as an account on the node, the
`--cluster_owner` are added with `AdminLevel=Admin` so they can see every
user's jobs rather than only their own.

#### Verified on

Proven end to end on **every supported OS family**: a submitted job ran on
a compute node and came back through `sacct` with its `.batch` step,
against a cluster that registered itself with populated TRES.

| `base_os` | package manager | MariaDB package installed |
|---|---|---|
| `ubuntu2404` | apt | `mariadb-server` 10.11 |
| `alinux2023` | dnf | `mariadb105-server` 10.5 (Amazon Linux 2023.11) |
| `rhel9` | dnf | `mariadb-server` 10.5 (RHEL 9.8) |

The two dnf distributions disagree about the package name — RHEL 9 does
not ship `mariadb105-server` at all — so the install tries the versioned
name first and falls back.  That fallback is load-bearing: collapsing it
to one name silently disables accounting on one of the two.

Building on AL2023 is what found the defect this section would otherwise
still be hiding.  The tuned config was written to
`/etc/mysql/mariadb.conf.d/`, Debian's location, while both dnf
distributions read `/etc/my.cnf.d/`.  Every value was ignored and the
server ran on stock defaults — including `innodb_lock_wait_timeout=50`,
where Slurm requires 900 — while the cluster built green and `sacct`
worked.  The directory is now read out of the server's own `!includedir`
rather than guessed, and the install checks the setting actually took by
asking the running server.

One detail worth knowing if you compare the numbers yourself: MariaDB
rounds `innodb_buffer_pool_size` up to a multiple of its 128 MB chunk
size, so the effective pool is usually a little larger than the derived
value (742 MB became 768 MB on RHEL 9).  That is the server behaving
normally, not the tuning failing.

### HPC Benchmarks

Enable with `--enable_hpc_benchmarks=true`.  Cluster creation deploys the benchmark suite to a personalized working directory on the head node — `~/hpc-benchmark/<cluster_name>/<cluster_owner>/slurm/` — holding the driver, a `job_hpc-benchmark.sh` rendered for this cluster's queue layout, and a `README-PERFORMANCE.md` naming this cluster's own paths.  Postinstall also installs the Python plotting dependencies (`matplotlib`, `numpy`, `pandas`, `scipy`, `seaborn`) and drops a second copy of the driver at `~/hpc-benchmark/hpc-benchmark.sh`; that copy is restored from S3 on every head node boot, so a replaced EBS root never loses the driver — the only file staged there.  Work out of the personalized directory, since that is the one with the rendered job script.

**These commands run on the cluster head node** (SSH in via `./access_cluster.py` first):

```bash
cd ~/hpc-benchmark/<cluster_name>/<cluster_owner>/slurm
module load openmpi
./hpc-benchmark.sh install                              # build STREAM, OSU, IOR, HPCG (~5 min)
./hpc-benchmark.sh run --tests stream,osu,ior,hpcg
./hpc-benchmark.sh report
```

**Results are preserved on teardown.**  `kill_pcluster.py` syncs benchmark results from the head node to `s3://parallelclustermaker-results-<account-id>-<region>/hpc-benchmark-results/<cluster_name>/<cluster_serial_number>/` before deleting the cluster.  That bucket is **not** the per-cluster bucket — keyed on your account and region, created on the first build that enables benchmarks, and never deleted by this toolkit — so results from multiple builds of the same cluster name land in separate serial-number subdirectories and accumulate rather than overwriting each other.  This is the one bucket you are expected to prune by hand.

**STREAM is rebuilt per node.**  STREAM is compiled `-march=native`, which binds the binary to the *microarchitecture* rather than the architecture — a `c5.xlarge` head node is Intel Skylake and a `g5.xlarge` GPU node is AMD Zen 3, and `uname -m` calls both `x86_64`.  `install` caches the source and `run` compiles `bin/stream-<march>` locally, so a job on a GPU partition measures that node's real bandwidth with no manual step.  OSU, IOR, and HPCG are built by `configure`/`make` without `-march=native` and are portable across microarchitectures, so those stay in `bin/` guarded by an architecture stamp.

**OSU builds itself on the GPU node when the head node cannot.**  `install` enables CUDA only when the node running `install` has both an NVIDIA device and a CUDA toolkit, because OSU's `configure` aborts outright on a missing `-lcuda`, `-lcudart`, or `cuda.h` rather than degrading — deriving that from a cluster-level flag would fail the whole install and take STREAM, IOR, and HPCG down with OSU.  A CPU head node therefore produces a host-to-host OSU, and the first GPU-partition job builds a CUDA-enabled tree under `bin/osu-cuda` on the GPU node itself and writes `osu/latency_cuda.txt` and `osu/bandwidth_cuda.txt` alongside the host-to-host results.  `bin/` is shared storage, so later GPU jobs reuse that build.  This step can never fail the run: a node that can't build CUDA support still writes the host-to-host results, with the reason printed.

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

**Monitoring is verified on RHEL 9, on both architectures.**  Upstream's `detect_platform` resolves `PLATFORM_ID=platform:el9` on x86_64 and Graviton alike, and the container stack reached `Started` on both — so the v2.6 installer is arch-agnostic on el9, not x86-only.

**IAM:** Monitoring permissions are granted via a separate managed policy `<ec2_iam_policy>-HeadNode-Monitoring` (8 statements, ~1,550 bytes minified).  It is created and attached during `make_pcluster.py` and deleted during `kill_pcluster.py`.

**Supply chain:** The `aws-parallelcluster-monitoring` tarball is downloaded from GitHub at cluster-build time, checksum-verified, and staged in the cluster's S3 bucket.  Head nodes pull from S3, not GitHub, so private-subnet nodes and air-gapped environments work without internet access.

On Amazon Linux 2023 that applies to the Docker Compose CLI plugin as well.  AL2023 does not package `docker-compose-plugin`, so upstream's installer downloads the binary from `github.com` on every node at boot, with no integrity check — which fails outright on a private subnet.  The toolkit instead downloads the binary once at build time, verifies the checksum against `--docker_compose_checksum_x86_64` / `--docker_compose_checksum_aarch64` (both defaulted in `pcluster_defaults.yml` and matching Docker's own published sums for `v2.29.7`), stages the verified binary to S3, and installs from there on every node before the monitoring installer runs.  The wrapper also deletes upstream's download from the extracted tree so that download can never overwrite the verified copy, and fails the build by name if that edit stops matching in a future monitoring release.  Because the download is removed rather than reused, `--docker_compose_version` is the toolkit's own pin and need not match upstream's.  The Ubuntu and RHEL 9 arms install the plugin from a signed distro repository and use none of this.

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

## MCP Server

`mcp_server/` exposes this toolkit over the [Model Context
Protocol](https://modelcontextprotocol.io), so an AI agent can list,
inspect, cost, build and tear down clusters through the same
`core_*` functions the CLI drives. There is no second implementation: the
MCP tools are thin wrappers, so anything the CLI can do the agent does
identically, and a fix lands in both at once.

A remote transport also exists in `mcp_server/` — a router plus tiered
Lambda handlers behind API Gateway and Cognito, which is what makes the
toolkit reachable from a browser rather than only from a local agent. It
has been deployed and driven end to end against AWS: every tier, the REST
API, the Lambda authorizer, and real Cognito tokens. Everything in
"Installing into Claude Code" below is the **local stdio server**, which
needs none of it; see [Deploying the remote transport](#deploying-the-remote-transport)
for the hosted one.

**One of its seven tiers has prerequisites the other six do not.**
`stack-mutation-node` ships as a container image rather than a zip, because
`pcluster`'s `create_cluster()` and `update_cluster()` call
`assert_valid_node_js()` as their first statement and AWS's Python Lambda
runtimes bundle no Node.js a zip could supply. That tier needs an **OCI
container runtime** (Finch, Docker, Podman or Rancher) and an **ECR
repository**, both handled by the deploy itself — see
[Adding cluster creation](INSTALL.md#adding-cluster-creation-the-container-tier)
for the per-platform runtime choices. The other six are plain zips and need
neither.

You do not need that tier to stand the transport up. Without it every tool
works except the four that reach Node — `create_cluster`,
`apply_cluster_update`, `preview_cluster_config` and
`finalize_cluster_build`.

The rest of this section is how to install, connect, operate and remove
the server.  The constraints that shaped it — why no tool may block, why
there are four Lambda tiers with different permissions, why one deployment
serves exactly one region, and what has actually been exercised against
AWS — are in **[docs/README-MCP-SERVER.md](docs/README-MCP-SERVER.md)**.

### Installing into Claude Code

Run this **from the repo root** — the shell expands `$(pwd)` before Claude
Code ever sees it, so what gets stored is a fixed absolute path:

```bash
claude mcp add parallelclustermaker \
  -e PYTHONPATH="$(pwd)" \
  -- "$(pwd)/.venv/bin/python" -m mcp_server.server
```

Verify with `claude mcp list` (look for `✔ Connected`) or `/mcp` inside a
session to see the tool list.

#### Adding it from inside a running session

`/mcp` cannot add a server. It manages ones that already exist: the status
panel, `reconnect <server>`, `enable`, and `disable`.

You can still configure it without leaving the session — the `!` prefix
runs a shell command and puts the output in the transcript:

```
! claude mcp add parallelclustermaker -e PYTHONPATH="$(pwd)" -- "$(pwd)/.venv/bin/python" -m mcp_server.server
```

**Then restart Claude Code.** A newly added stdio server is not picked up
by the session that added it, and there is no reload for one:
`/mcp reconnect` applies to HTTP/SSE servers, and only those configured
before the session started. The same holds for editing `.mcp.json` or
`~/.claude.json` by hand — both are read at startup.

After restarting, run `/mcp` and confirm the tools are listed. That is a
stronger check than `claude mcp list` showing `✔ Connected`: the process
starting and its tools being callable are different claims.

Both paths must end up absolute — which is why they are expanded at the
shell rather than written as `./.venv/bin/python`, a relative path Claude
Code would resolve against its own working directory, not the repo. And
**`PYTHONPATH` is required, not optional**. Claude Code does not run the
server from your project directory, and `-m mcp_server.server` needs the
repo root on `sys.path`; without it the server fails with `No module named
'mcp_server'`.

You do **not** need the venv activated — `mcp_server/` carries no venv
guard and puts `src/` on the path itself — but you must use
`.venv/bin/python`, since `fastmcp`, `boto3` and `aws-parallelcluster`
live only there.

### Three things that will waste your time

- **Do not invoke the script directly.** `python mcp_server/server.py`
  fails with `ImportError: FastMCP server support is not installed`, which
  is misleading — `fastmcp` is installed. Running the file directly puts
  `mcp_server/` at the front of `sys.path` and shadows its own imports.
  Use `-m`.
- **Do not use `--scope project`.** That writes `.mcp.json` into the repo
  root, which is committed. On a public fork you would publish your own
  absolute paths. The default `local` scope is correct; use `user` if you
  want the server in every project.
- **AWS credentials are not inherited from your shell.** Profiles
  configured in `~/.aws/credentials` and `~/.aws/config` work normally
  because they are files. But if you rely on exported variables, pass them
  explicitly:

  ```bash
    -e AWS_PROFILE=your-profile -e AWS_REGION=us-east-1
  ```

### What this grants

The local server registers the full tool set, including `create_cluster`
and `delete_cluster`. **These make real AWS changes and cost real money.**

**Both are gated by a confirmation token**, so neither can happen without
the agent first showing you what it is about to do: `delete_cluster`
requires a token minted by `preview_cluster_delete`, and `create_cluster`
one minted by `preview_cluster_config`. The token binds the *parameters*,
not just the operation, so a preview of a small cluster cannot authorize a
large one, and the tokens expire.

Read-only tools (`list_clusters`, `check_cluster_health`,
`get_cost_report`, `diagnose_cluster`, `list_queues`,
`resolve_access_info`) are safe to hand to an agent freely.

### Finishing a build

`create_cluster` returns as soon as CloudFormation accepts the stack. It
has to: a build takes 20–45 minutes and no single call can block that long.
So the last steps — the build summary, and publishing the cluster record
other machines discover it by — are left undone.

**`finalize_cluster_build` does them**, once the stack reaches
`CREATE_COMPLETE`. It refuses before that and never waits. It takes no
confirmation token: it destroys nothing and only completes work you
authorized by building. Re-running the build cannot substitute — that
refuses on the vars file it wrote itself.

It works from the connector as well as locally. The staging tree the head
node needs is published to S3 *before* the stack is created and pulled by
the node during its own bootstrap, so finishing a build reaches nothing and
needs no SSH key — which is what lets a cluster built in the browser be
finished there. The generated access scripts are still written on your own
machine, because that is where you run them; `access_cluster.py` renders
them on demand from the vars file, so they are never missing.

If the pull fails, the node warns rather than dying, and
`/opt/parallelcluster/shared/staging_tree_pulled` is absent — the cluster
runs fine, but the convenience tree on the head node is not there.

### Finishing a teardown

You don't.  `delete_cluster` returns as soon as CloudFormation accepts the
delete — it has to, for the same reason `create_cluster` does — but unlike
a build, nothing is left for you to do.

The server fires an asynchronous poller at itself, which watches the stack
and runs the rest of the cleanup once it is gone: the IAM policies, the S3
bucket, the SSH key secret, the SNS topic, the EC2 keypair and the cluster
record.  **Read `auto_finalize_started` in the response.**  When it is
true the teardown is handled and there is nothing to poll for.

When it is false — the poller could not be started, or you are on the
local stdio server where there is no Lambda to invoke —
`finalize_cluster_teardown` does the same work on demand.  That is the
only case where it is yours to call.

**Never call `delete_cluster` a second time to finish the job.**  It
appears to work, because an absent stack reports already-gone and the
cleanup runs, but it gets there by issuing another delete against the
*name* — which destroys a different cluster if that name has since been
rebuilt.

### When a build fails

A build takes longer than any single call can wait, so `create_cluster`
starts it in the background and returns. Read `build_started`: when it is
true the build is underway, and `list_clusters(live=True)` will show it
shortly. Nothing is waiting on the build, so a failure has no call to
report itself to.

**`get_build_status` is where the reason is.**  Every failure after the
first AWS resource is created records the stage, the reason and the time,
and this reads it back.  Call it whenever a build did not obviously
succeed; the answer is there, not in the response to the call that failed.

One field to read carefully: `failed: false` is only meaningful alongside
`store_reachable: true`.  If the shared state store could not be read, the
tool says so rather than reporting no failure — it will not tell you a
build succeeded when it does not know.

A failure that got far enough to create anything cleans up after itself,
so retrying once you have addressed the cause is safe.  The record is
cleared by the next successful build of the same name, and by teardown.

### Node.js

`create_cluster` needs Node.js on `PATH` for the same reason the CLI does
— ParallelCluster shells out to the AWS CDK to synthesize CloudFormation.
If it is missing you will see `Unable to find node executable`. See
[INSTALL.md](INSTALL.md).

### Removing it

```bash
claude mcp remove parallelclustermaker
```

---

## Deploying the remote transport

The local stdio server above runs on your machine and answers only to the
agent you attached it to. The **remote transport** puts the same tools
behind API Gateway and Cognito, so a browser session can reach them. It is
seven Lambda functions: a router, four handler tiers split by IAM blast
radius, and two that serve the OAuth flow.

Everything here runs from your own machine against your own AWS account.
There is no hosted service and nothing is shared with anyone.

### One command

```bash
./deploy_mcp.py --bootstrap --create-user you@example.com
```

That creates the IAM roles and policies (each under a permissions
boundary), the Cognito user pool, all six zip tiers, the REST API with its
Lambda authorizer and routes, the Cognito app client and Hosted UI domain,
and a user to sign in as. It prints the MCP endpoint and, if it generated
one, the password:

```
  MCP endpoint: https://<id>.execute-api.<region>.amazonaws.com/prod/mcp
  Discovery:    https://<id>.execute-api.<region>.amazonaws.com/prod/.well-known/oauth-protected-resource

  Cognito user: you@example.com (created)
  Password:     <generated>
  ^ generated, shown once, not recoverable -- save it now
```

**The password is shown once.** Cognito stores a hash, so a lost one is
re-set by re-running `--create-user`, never recovered. To choose your own
instead, export `MCP_USER_PASSWORD` before running.

`--bootstrap` is idempotent, so it is also the update path: run it again
after pulling changes and it redeploys the functions and reconciles the
gateway. It is a spelling of `--setup-infra --setup-gateway` plus every zip
tier — those flags still exist and still compose if you want one step at a
time.

### Connecting it to claude.ai

1. **Customize → Connectors**, then the **`+`** beside Connectors. On Team
   and Enterprise plans it is **Organization settings → Connectors → Add →
   Custom → Web**, and only an Owner can add it; members then connect to it
   from their own **Customize → Connectors**.
2. Paste the **MCP endpoint** the deploy printed — the `/mcp` URL, not the
   discovery one.
3. In the advanced settings, set **OAuth Client ID** to the `OAuth client`
   value the deploy printed, and leave **OAuth Client Secret** empty.
4. Sign in at the Cognito Hosted UI with the user you created.

The client ID has to be supplied by hand because **Cognito cannot register
a client on demand.** This server does serve a `/register` endpoint, and it
works when called directly, but no client reaches it: a client discovers
the authorization server from the protected-resource document, that
document names Cognito, and Cognito's own metadata advertises no
`registration_endpoint`. Client ID Metadata Documents — which the MCP spec
now prefers over dynamic registration — are no escape either: they are an
authorization-server feature, and Cognito rejects a URL-formatted
`client_id` outright. Supporting either would mean running an
authorization server in front of Cognito.

So `--setup-gateway` creates the app client itself and prints its ID. The
secret is empty because the connector is a public client using PKCE; a
client with a secret fails the token exchange. Consent is requested once,
per connector — that is Anthropic's requirement, not something this deploy
can skip.

**Deleting or rotating the app client invalidates every session minted
against it.** The connector then fails to reload its tools while every
server-side check passes, because nothing is wrong with the server — the
stored token refers to a client that no longer exists. **Disconnect and
re-add the connector**; reloading reuses the dead token and keeps failing.
The same applies after `--teardown`, which removes the pool entirely.

**The first call after the transport has been idle can take ~9 seconds**,
and claude.ai may give up on it with "your account was authorized, but
ParallelClusterMaker didn't respond". That is a Lambda cold start, not a
broken deployment: retry and it answers in about 20ms. Measured on the
read-only tier at 8.6s cold against 15-23ms warm. Note that the reported
`Init Duration` is only ~150ms — the tier imports ParallelCluster lazily
inside the handler, so the init phase does not show this cost.

Claude reaches the server from Anthropic's cloud, not from the machine
running the browser, so the endpoint has to be reachable from the public
internet. The deployed API Gateway is; a server behind a VPN or bound to
localhost is not.

### Adding cluster creation (the container tier)

**`--bootstrap` deploys six of the seven tiers.** The seventh ships as a
container image, so it needs a container runtime, and `--bootstrap` leaves
it out rather than requiring one on every machine.

**Without it, four tools are missing from the connector** —
`create_cluster`, `apply_cluster_update`, `preview_cluster_config` and
`finalize_cluster_build`. You can **inspect and operate** clusters from the
browser but **not create or modify** them. Everything else is present:
listing, health, cost, diagnostics, queues, fleet start/stop, access info
and teardown.

Install a container runtime, then one command — the deploy creates the ECR
repository, logs in, builds for `linux/amd64` and pushes:

```bash
./deploy_mcp.py --tier stack-mutation-node
```

`--runtime` chooses among several installed; `--image-uri` deploys an image
built elsewhere and skips the build. Runtime choices per platform, and the
Finch push failure worth knowing about, are in
[INSTALL.md](INSTALL.md#adding-cluster-creation-the-container-tier).

### Removing the MCP service

```bash
./deploy_mcp.py --teardown              # add --dry-run to list first
```

The API Gateway REST API is removed first, because it is the
internet-facing surface and deleting it stops requests arriving while the
rest of the transport is only half torn down. The Lambda functions go next,
followed by the ECR repository that holds the container tier's image, then
the IAM roles and policies the functions ran under, and finally the Cognito
user pool that authenticated callers. The repository has to be deleted
after the functions that reference its image but before the IAM that grants
the deletion, and it is removed together with the image inside it, because
a repository this tool created always holds one.

Deleting the user pool invalidates every session that was issued against
it, so the claude.ai connector stops working as soon as the pool is gone.
To recover, remove the connector and add it again. Reloading the page will
not do it.

The permissions boundary is left in place on purpose. A deployer who is
able to delete their own boundary does not really have one, so
`MCPDeployPolicy` denies that action outright and the teardown says so
rather than attempting it and reporting the refusal as a failure. If you
want the account completely empty, delete the boundary by hand using
credentials that are permitted to.

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

## For Account Owners and Cluster Superusers

Most of what this toolkit creates belongs to one cluster and is destroyed
with it.  A few things are **account-scoped**: they are shared by every
cluster in the account, they outlive any individual teardown, and some of
them keep costing money.  If you own the AWS account — or administer
clusters on behalf of other people — these are the ones to know about,
because they are the ones where a reasonable-looking cleanup breaks
something you did not expect.

### One IAM step is yours, and it cannot be automated

The toolkit creates its own roles, policies and instance profiles per
cluster.  It cannot create *your* permissions, and deliberately does not
try: a tool that can grant itself IAM has no ceiling, so
`generate_operator_policy.py` renders and installs the operator policy but
nothing in the build path can widen it.  Run this once per account:

```bash
./generate_operator_policy.py --bootstrap
```

Two consequences worth knowing before you attach it:

* **Its one `Deny` binds you too.**  `IAMDenyWeakeningTheClusterBoundary`
  refuses `DeletePolicy`, the policy-version actions, and
  `DeleteRolePermissionsBoundary` on `pclustermaker-cluster-boundary` and
  `pclustermaker-role-*`.  That is the guardrail working — an explicit
  `Deny` beats any `Allow`, including an administrator's — but it means
  attaching this policy to your own admin identity genuinely restricts it,
  on those two resources.
* **It is scoped to this toolkit's own names.**  It does not grant AWS
  ParallelCluster's own required permissions; those are documented
  upstream and are a separate set.

### The EC2 Spot service-linked role is account-wide

Spot is the default capacity type.  Every spot request in an AWS account —
not just this toolkit's — goes through a single role,
`AWSServiceRoleForEC2Spot`, which the EC2 Spot service assumes.  There is
one per account, no cluster owns it, and no cluster can create it.

The first spot build creates it for you if your identity has the grant.
**Deleting it breaks spot for everything in the account**, not just for
ParallelClusterMaker, and the failure is opaque: instances fail to launch
with `AuthFailure.ServiceLinkedRoleCreationNotPermitted` while stacks
report success.  Leave it alone.

### Two permissions boundaries are never deleted, on purpose

`pclustermaker-cluster-boundary` caps what a head node role can ever be
granted; `pclustermaker-mcp-boundary` does the same for the MCP transport's
roles.  Teardown deliberately leaves both, and that is not an oversight to
clean up: they are account-level, so **every other live cluster's role is
bounded by the same document**, and deleting one on a single teardown
uncaps all of them at once.

They are also never updated by the toolkit.  Changing a boundary is an
administrator's action, taken out of band, precisely so a build cannot
widen its own ceiling.

**Stated rather than hidden:** only the *head node* role carries a
boundary.  Compute and login node roles are created by ParallelCluster's
own CDK, which the toolkit cannot pass a boundary to, so they are capped by
the `ClusterNode-Deny` policy instead — a Deny-only document attached to
every node.  If your security review needs boundaries everywhere, this is
the gap to raise.

### Three things keep costing money after a cluster is gone

| What | Why it survives |
|---|---|
| CloudWatch log groups `/aws/parallelcluster/<cluster>-<timestamp>` | Retained 30 days.  They are the only surviving record of a failed build, and teardown always follows a failure — deleting them destroys the evidence exactly when it is needed |
| `parallelclustermaker-results-<account>-<region>` | Benchmark results are meant to outlive the clusters that produced them |
| `parallelclustermaker-locks-<account>-<region>` | The cluster lock and record store, shared across machines and clusters |

Teardown prints the retained log groups by name every time, so the running
total is never a surprise.  All three are safe to prune on your own
schedule; nothing in the toolkit depends on old log groups or old results.

### Job accounting is not an audit trail

Slurm accounting is on by default, so every cluster runs a small MariaDB on
its head node and `sacct` returns real job history.  **That database lives
on the head node's root volume and teardown destroys it.**  There is no
export step and nothing is synced to S3.

If you are relying on job records for chargeback, compliance or capacity
review, copy them off before deleting a cluster — or use Cost Explorer,
which the toolkit tags per cluster and which does survive.  See
[Job Accounting](#job-accounting).

## Note to DevOps Teams

ParallelClusterMaker does **not** create or modify VPCs, subnets, gateways, routes, or Transit Gateways.  It creates IAM roles, policies, and instance profiles scoped to each individual cluster stack — with a small number of deliberate exceptions that are account-scoped and outlive any one cluster, listed under [For Account Owners and Cluster Superusers](#for-account-owners-and-cluster-superusers).  Templates are in `templates/` and can be customized.  If you hit permissions errors, the IAM policy template is the right starting point for working with your security team.

---

## Troubleshooting

**IAM permissions:** Check `templates/HeadNode-Compute.json_src`, `HeadNode-Storage.json_src`, `HeadNode-IAM.json_src`, `ComputeNode-Base.json_src`, `ClusterNode-Deny.json_src`, and (when `enable_monitoring=true`) `HeadNode-Monitoring.json_src`.  The instance policy is split by role into five managed policies — six with monitoring — to stay under the IAM managed policy size limit.  `ClusterNode-Deny.json_src` is worth reading first when a permission is refused for no visible reason: it contains only `Deny` statements, it is attached to every node, and an explicit `Deny` overrides any `Allow` in the other policies.  The head node's role is additionally created under the `pclustermaker-cluster-boundary` permissions boundary, which caps what that role can ever be granted.  IAM role and instance-profile resources use both flat-name ARNs (`parallelcluster-<CLUSTER_NAME>-*`) and path-based ARNs (`parallelcluster/<CLUSTER_NAME>/*`) — PCluster v3 uses the latter for compute fleet roles.  Most build failures trace back to missing IAM permissions.

**Spot capacity:** Compute nodes that fail to launch surface as a `ComputeFleet - CREATE_FAILED` CloudFormation error.  Retry the build or switch to `--cluster_type=ondemand`.

**Spot nodes never launch and the cluster still reports `CREATE_COMPLETE`:** Check `sinfo -R` on the head node.  If the reason reads `(Code:AuthFailure.ServiceLinkedRoleCreationNotPermitted)Failure when resuming nodes`, the account is missing the EC2 Spot service-linked role, `AWSServiceRoleForEC2Spot`.

This is an *account-level* prerequisite, not a per-cluster one.  There is exactly one such role per AWS account, the EC2 Spot service assumes it, and every spot request in the account goes through it — so it is not attached to a cluster and cannot be created by one.  AWS creates it automatically on the first spot request made by a principal holding `iam:CreateServiceLinkedRole`, but the principal that makes ours is the **head node** (`slurm_resume` calls `ec2:CreateFleet` with `CapacityType: SPOT`), and the head node deliberately holds no IAM write permissions — a shell there, including one obtained by submitting a Slurm job, must not be able to create roles.

In an account that has never launched a spot instance by some other route, the result is a cluster that builds green and cannot run a single job: the stack reaches `CREATE_COMPLETE`, `sinfo` shows nodes cycling through `down#`, and jobs sit `PENDING` forever while `clustermgtd` replaces nodes that can never come up.

Since spot is the **default** (`--cluster_type=spot`), this affects most first builds in a fresh account.  `make_pcluster.py` now creates the role for you before it creates anything else, so a normal build handles it.  If the operator identity lacks the grant, the build stops immediately — before any billable resource exists — and names the fix:

```bash
aws iam create-service-linked-role --aws-service-name spot.amazonaws.com
```

Ask an administrator to run that once per account, or build with `--cluster_type=ondemand`, which needs no such role.

**Build fails with `HeadNodeWaitCondition` timing out (`CREATE_FAILED`, 0 of 1 signals):** The head node did not finish bootstrapping inside the window CloudFormation allows.  Note that this clock starts when CloudFormation *begins creating the wait condition* — before the head node instance exists — and shared filesystem provisioning sits on the head node's critical path.  A 1200 GB FSx for Lustre filesystem measured 17m 22s, over half of PCluster's stock 2100 s budget, before the instance had even launched.

The toolkit raises `head_node_bootstrap_timeout` automatically for this: +1800 s when `enable_fsx` is true, +600 s when `enable_efs` is true, whichever is larger (the two provision concurrently, so the head node waits on the slower one, not the sum).  A `*** INFO ***` line names the filesystem that drove the increase.

Both allowances are measured against live builds rather than estimated.  FSx: a second 1200 GB filesystem took 19m 20s before its instance existed, and the build completed in 34m 24s of the 3900 s granted.  EFS (`generalPurpose`/`bursting`, one mount target): the filesystem itself completed in 4 s and its mount target in 1m 33s, with the instance appearing 4m 24s in and the wait condition satisfied in 20m 52s of the 2700 s granted — so the EFS allowance carries roughly 2.3x headroom.  Two caveats worth knowing before you rely on them: a multi-AZ EFS cluster creates one mount target per subnet and has not been timed, and on the EFS build the mount target was not in fact what gated the instance (the head node launch template holds no reference to it), so 600 s covers the observed pre-instance window rather than a proven dependency.

If it still times out, set `head_node_bootstrap_timeout` explicitly in your defaults file — any value other than 2100 is used verbatim and disables the automatic increase, so set the *total* you want, not an increment:

```yaml
head_node_bootstrap_timeout: 5400
```

The ceiling is 43200 (12 hours), CloudFormation's own limit; larger values are clamped with a warning.  The value cannot be changed on a running cluster — PCluster marks that setting `UpdatePolicy.UNSUPPORTED`, so changing the timeout means a full rebuild.  To find where the time actually went, compare the `CREATE_IN_PROGRESS`/`CREATE_COMPLETE` timestamps per resource:

```
aws cloudformation describe-stack-events --stack-name <cluster_name> \
    --query 'StackEvents[].[Timestamp,LogicalResourceId,ResourceStatus]' --output text | sort
```

The `preinstall`/`postinstall` scripts exclude the kernel from their package upgrades precisely because a kernel bump added an unbounded initramfs rebuild to this window — see [The Kernel Is Never Upgraded](#the-kernel-is-never-upgraded).

**Compute nodes fail to bootstrap after editing `postinstall.j2`:** Postinstall runs on the head node *and* on every compute node, and it runs under `set -euo pipefail` — a non-zero exit fails the node's bootstrap.

A compute-node failure does not stop the way a head-node failure does, which costs more, not less.  `clustermgtd` marks the node `DOWN`, relaunches the node, and repeats until the queue's bootstrap-failure count reaches **10**, at which point `clusterstatusmgtd` puts the cluster in `PROTECTED` state and the stack fails — after ten instance launches and, in one measured case, 82 minutes.  So a two-line mistake in a block that runs on compute nodes costs the whole build.  Check the *compute* node's log stream, not just the head node's: the head node can finish cleanly while the rest of the fleet fails.

Anything added there must declare where it belongs:

- Work on shared storage (`/shared`, `/efs`, `/fsx`, `$HOME`, `/opt/parallelcluster/shared`) belongs inside a `[ "$NODE_TYPE" == "HeadNode" ]` guard.  These paths are NFS-exported from the head node, so N compute nodes writing to one file is a concurrent read-modify-write, and a write that root-squash denies aborts the node's own bootstrap.
- Work on node-local state (instance store, local packages, sysctls) belongs outside the guard.
- Packages needed on compute nodes must be installed outside the guard.  The main `apt-get` block is head-node-only.
- `NODE_TYPE` is read from `cfn_node_type` in `/etc/parallelcluster/cfnconfig`, which ParallelCluster writes before any custom action runs.  There is no `PARALLELCLUSTER_NODE_TYPE` environment variable — reading one silently makes every compute node take the head-node path.  `HeadNode` is the default only when the cfnconfig file is absent, which means the script is being re-run by hand off-cluster; a cfnconfig with no `cfn_node_type`, or an unrecognized value, exits 1 rather than skipping every guard.

`tests/test_templates.py::TestPostinstallNodeTypeGating` executes the rendered script for each node type with all external commands stubbed, so a block placed on the wrong side of a guard fails the suite.

**A bootstrap failure whose log ends on a cheerful-looking line:** `cfn-init` captures **stdout only** — a failing command's `stderr` is written nowhere.  So the last line of `cfn-init-cmd.log` is routinely the successful-looking start of the step that failed: `Attempting uninstall: requests` for a pip failure, a list of successful `set on hold` lines for an `apt-mark` exit 100, the `luarocks` download banner for a compiler error.  Read the last line as *where* execution stopped, never as *why*.  To get the reason, re-run the same command by hand on the node (`aws ssm start-session --target <instance-id>`, then execute the rendered `/opt/parallelcluster/scripts/...` step or the individual command) and read its stderr directly.  A related consequence: any block in the toolkit's own scripts that runs without `set -x` — the monitoring wrapper, for instance — leaves no trace in the log at all, so absence of a command from `cfn-init-cmd.log` is not evidence it did not run.

**Postinstall appears to do nothing:** Check that `postinstall.<cluster>.sh` in the cluster's S3 bucket is rendered shell and not raw Jinja2.  The toolkit's templates are rendered by a `template:` task in `src/create_pcluster.yml`; only your own `--post_install_script` hook is copied verbatim.  If the two are conflated, nodes run the hook and skip everything the toolkit's script does — Spack, Lmod, the package installs, `/local_scratch`, and the GPU block.  See [Node Bootstrap Scripts](#node-bootstrap-scripts).

**EBS root volume tagging:** May fail on macOS due to IAM tag permission restrictions.  Build from an EC2 instance to avoid this.

**Interrupted build recovery:** If `make_pcluster.py` is interrupted mid-run, re-run the same command with the same flags.  The tool detects the existing serial file under `active_clusters/<cluster_name>/` and resumes from that identity — all AWS resource names (S3 bucket, IAM role, IAM policy) are re-derived from the same serial number, so no orphaned resources are left behind.

---

## Development

### Running the Test Suite

```
make test       # pytest — template rendering + unit tests
make lint       # ansible-lint on src/create_pcluster.yml and src/delete_pcluster.yml
make shellcheck # shellcheck on every tracked *.sh file
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

### Integration Tests

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

### Known ansible-lint Warnings

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

AI-assisted contributions are welcome — see [AI_POLICY.md](AI_POLICY.md) for submission guidance.
