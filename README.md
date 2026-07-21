# ParallelClusterMaker

Open Source CLI toolkit for automating the creation and destruction of AWS ParallelCluster v3 stacks.  Designed to let scientists and engineers run HPC in the cloud without deep infrastructure knowledge, and to reduce the administrative burden on DevOps teams supporting HPC users.

This codebase was co-written with [Claude Code](https://claude.ai/code) (Anthropic).

---

## Prerequisites

* Python 3.10 or later
* AWS CLI v2, configured with credentials that have sufficient IAM permissions
* `git`
* `ansible` (installed via pip below)
* On macOS: Homebrew is recommended for system dependencies (`brew install ansible autoconf automake gcc jq libtool make readline`)

Apply a Name tag to any VPC in regions where you plan to deploy cluster stacks
(Console → VPC → Your VPCs → edit the Name field).  Some examples:
  * "nova" for us-east-1
  * "cleveland" for us-east-2
  * "dublin" for eu-west-1

**Important:** some environments may require assistance from your company or organizational IT/DevOps team to complete the VPC tagging step.

It runs from a Python virtual environment (`.venv/`) inside the repository.
The venv is excluded from git via `.gitignore`; its state is captured by
`requirements.txt`, which is committed and versioned.

In theory this toolkit can also be used on Windows, but that method has not been tested and will **not** be supported.

---

## Installation

Clone the repository and create the virtual environment:

```
cd ~/src
git clone https://github.com/rmarable/ParallelClusterMaker.git
cd ParallelClusterMaker
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
ansible-galaxy collection install -r requirements.ansible.yml
```

On macOS, install system dependencies via Homebrew first:

```
brew install ansible autoconf automake gcc jq libtool make readline
```

To activate the environment:

```
cd ~/src/ParallelClusterMaker
source .venv/bin/activate
```

To deactivate the environment when you are done:

```
deactivate
```

To reactivate in a future session:

```
cd ~/src/ParallelClusterMaker
source .venv/bin/activate
```

To view all available options for `make_pcluster.py`:

```
./make_pcluster.py --help
```

---

## Features

- Slurm job scheduling
- Separate instance types and EBS configurations for head node and compute fleet
- Spot instances by default with configurable pricing buffer
- Cluster lifetime management — stacks self-terminate after `--cluster_lifetime`
- dev / test / stage / prod operating levels
- Custom AMI support (`pcluster build-image` workflow)
- EFA support for HPC-optimized instance types
- Dynamic EC2 placement groups
- Selective Intel HyperThreading disable
- Multiple shared storage options
  - EFS with encryption support
  - FSx for Lustre with optional S3 hydration/dehydration
  - EBS (up to 16 TB)
- External NFS automount (Vast, NetApp, WekaIO, Qumulo, etc.)
- Spack + Lmod for HPC software module management
- SNS notifications on stack create/destroy
- Turbot environment support
- Resource tagging by owner, department, project, and operating level
- Optional HPC performance test suite: `Axb_random` smoke tests + standards-based STREAM, OSU MPI, IOR, and HPCG benchmarks

---

## Defaults

All optional parameters have hardcoded defaults and can also be persisted in a YAML defaults file.  `pcluster_defaults.yml` is the template — **copy it to your own file before use**; do not pass the toolkit's copy directly as it is shared and may be overwritten by updates.  The most commonly referenced defaults:

| Parameter | Default |
|---|---|
| `base_os` | ubuntu2404 |
| `scheduler` | slurm |
| `headnode_instance_type` | c5.xlarge |
| `compute_instance_type` | c5.2xlarge |
| `cluster_type` | spot |
| `initial_queue_size` | 2 |
| `max_queue_size` | 10 |
| `scaledown_idletime` | 5 min |
| `cluster_lifetime` | 7:0:0 (days:hours:min) |
| `ebs_shared_volume_size` | 250 GB |
| `fsx_size` | 1200 GB |

Use `--use_defaults=FILE` to load values from your own defaults file; CLI arguments always take precedence.

```
# Copy the template and customize it for your cluster
cp pcluster_defaults.yml my-cluster.yml
# Pass it at runtime
./make_pcluster.py -N my-cluster -O rmarable -E rmarable@example.com -A us-east-1a \
    --use_defaults=my-cluster.yml
```

Naming the file after the cluster (`<cluster_name>.yml`) is strongly recommended so cluster namespaces are clearly scoped.  Loading `pcluster_defaults.yml` directly is allowed but prints a warning.

---

## Building a Cluster

```
./make_pcluster.py -N CLUSTER_NAME -O OWNER -E EMAIL -A AZ [options]
```

Required arguments:

| Flag | Description |
|---|---|
| `-N` | Cluster name (must start with a letter; lowercase letters, digits, hyphens only; no consecutive or trailing hyphens; max 27 characters) |
| `-O` | Owner username |
| `-E` | Owner email |
| `-A` | Availability zone (e.g. `us-east-1a`) — pass an AZ, not a region |

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

Fixed-size autoscaling pool, with tags for a specific and internal compbio department:
```
./make_pcluster.py -A eu-central-1a -O rmarable -E rodney.marable@gmail.com -N koolkeith \
    --initial_queue_size=4 --max_queue_size=125 --maintain_initial_size=true \
    --scaledown_idletime=30 --cluster_owner_department=compbio --project_id=polaroid \
    --compute_instance_type=c5d.2xlarge
```

Self-terminating cluster (12 hours):
```
./make_pcluster.py -N pcluster-test-01 -O rmarable -E rodney.marable@gmail.com -A us-east-1a \
    --cluster_lifetime="0:12:0"
```

EFA-enabled single-node cluster with performance tests:
```
./make_pcluster.py -A us-east-1a -N rimshot -O rmarable -E rmarable@amazon.com \
    --compute_instance_type=c5n.18xlarge --initial_queue_size=1 \
    --maintain_initial_size=true --enable_efa=true --enable_hpc_performance_tests=true
```

FSx for Lustre with S3 hydration (7.2 TB, 5 GB chunk size):
```
./make_pcluster.py -A us-west-2b -O rmarable -E rodney.marable@gmail.com -N louievega \
    --enable_fsx=true --fsx_size=7200 --enable_fsx_hydration=true \
    --fsx_s3_import_bucket=s3DataImportBucket --fsx_s3_export_bucket=s3DataExportBucket \
    --fsx_chunk_size=5000
```

Large GPU cluster with 3.6 PB Lustre, tagged for production:
```
./make_pcluster.py -A us-east-1 -O rmarable -E rodney.marable@gmail.com -N gilgamesh \
    --base_os=ubuntu2204 --headnode_instance_type=r4.xlarge \
    --compute_instance_type=p3.16xlarge --enable_fsx=true --fsx_size=3600000 \
    --enable_fsx_hydration=true --fsx_s3_import_bucket=GilgameshSrcBucket \
    --fsx_s3_export_bucket=GilgameshOutputBucket --prod_level=prod --max_queue_size=256
```

Building from a custom AMI (must match base_os):
```
./make_pcluster.py -N starscream -O rmarable -E rodney.marable@gmail.com -A us-west-2a \
    --enable_fsx=true --custom_ami=ami-123456789abc --base_os=ubuntu2204
```

A new stack typically takes 30–45 minutes to build.

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

Teardown takes 5–10 minutes.  By default, associated EFS, FSx, and S3 resources are also deleted.  To preserve them:

```
./kill_pcluster.py -N pcluster-test-01 -O rmarable -A us-east-1a \
    --delete_fsx=false --delete_s3_bucketname=false
```

After a stack is deleted, it is strong recommended to run `kill_pcluster.py` to remove local artifacts even if the cluster self-terminated via `cluster_lifetime`.

---

## Storage

### EBS

All instances get `/local_scratch` backed by the root EBS volume.  Shared EBS is mounted at `/shared` (default).  Configure with `--ebs_shared_volume_size`, `--ebs_shared_volume_type`, `--ebs_encryption`.

### EFS

Enable with `--enable_efs=true`.  Mounted at `/efs` on all instances.  Adds ~5–7 minutes to build time.  Supports encryption (`--efs_encryption=true`) and performance mode (`--efs_performance_mode`).

### FSx for Lustre

Enable with `--enable_fsx=true`.  Mounted at `/fsx`.  Minimum size is 1200 GB; must be a multiple of 1200.  Maximum chunk size is 500 GB (512,000 MB).  S3 hydration/dehydration supported via `--enable_fsx_hydration=true` and the `--fsx_s3_*` parameters.

### External NFS

Enable with `--enable_external_nfs=true --external_nfs_server=storage.domain.com`.  Mount points are configured in `templates/external_nfs_mount_list.j2`.

---

## Networking and Compute

### VPC and Subnet Selection

> **Important:** The toolkit auto-discovers VPCs and subnets by convention when explicit values are not provided. Auto-discovery picks the AWS default VPC and the *first* subnet returned by EC2 in each AZ. EC2 does not guarantee subnet ordering, so results are non-deterministic in accounts with multiple subnets per AZ. **Do not rely on auto-discovery for production clusters.** Always specify networking resources explicitly.

| Parameter | Description |
|---|---|
| `--vpc_name` | VPC `Name` tag to use (default: `vpc_default` — the account's default VPC) |
| `--headnode_subnet_id` | Explicit subnet ID for the head node; overrides auto-discovery |
| `--compute_subnet_ids` | Comma-separated subnet IDs for the compute fleet; overrides auto-discovery |
| `--compute_az` | Comma-separated AZs for the compute fleet (default: same as `--az`) |
| `--use_private_compute_subnet` | Place compute nodes in private subnets (`true`/`false`, default: `false`) |

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

### EFA

Enable with `--enable_efa=true`.  Supported on `ubuntu2204`, `ubuntu2404`, `rhel8`, `rhel9`.  Requires a supported instance type (c5n.18xlarge, hpc6a.48xlarge, hpc7a.96xlarge, etc.).  A dynamic placement group is created automatically.

### Placement Groups

Enable with `--placement_group=DYNAMIC`.  If head node and compute instance types match, both are placed in the group; otherwise only compute instances are.

### HyperThreading

Disable with `--hyperthreading=false`.

---

## Software Environment

### Spack + Lmod

Every stack includes [Spack](https://spack.io/) and [Lmod](https://github.com/TACC/Lmod) for HPC software module management.

### Job Submission

A default Slurm submission script (`scripts/sbatch_default_submission_script.sh`) is copied from the toolkit's `scripts/` directory to the ec2-user home directory during cluster creation.  Copy it to any shared storage and customize as needed:

```
cp ~/sbatch_default_submission_script.sh /fsx/scratch/my_project/
sbatch /fsx/scratch/my_project/sbatch_default_submission_script.sh
```

### HPC Performance Tests

Enable with `--enable_hpc_performance_tests=true`.  Deploys the full performance toolkit to the cluster head node at `~/performance/`.

**Tier 1 — Axb_random smoke test** (no MPI required):
```bash
./hpc-perftest.sh run -n 5 -C pcluster-test-01
./hpc-perftest.sh plot --type unified
```

**Tier 2 — standards-based benchmarks** (MPI required):
```bash
./hpc-benchmark.sh install                              # build STREAM, OSU, IOR, HPCG (~5 min)
./hpc-benchmark.sh run --tests stream,osu,ior,hpcg
./hpc-benchmark.sh report
```

**Slurm job array submission:**
```bash
./hpc-perftest.sh submit --start 10 --step 10 --total 10
```

Edit `MATRIX_SIZES.conf` to control the test scope.  See `performance/README-PERFORMANCE.md` for full documentation.

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

**IAM permissions:** Check `templates/ParallelClusterInstancePolicy.json_src`.  Most build failures trace back to missing IAM permissions.

**Spot capacity:** If compute nodes fail to launch you'll see a `ComputeFleet - CREATE_FAILED` CloudFormation error.  Retry the build or switch to `--cluster_type=ondemand`.

**EBS root volume tagging:** May fail on macOS due to IAM tag permission restrictions.  Build from an EC2 instance to avoid this.

**Interrupted build recovery:** If `make_pcluster.py` is interrupted mid-run, re-run the same command with the same flags.  The tool detects the existing serial file under `active_clusters/<cluster_name>/` and resumes from that identity — all AWS resource names (S3 bucket, IAM role, IAM policy) are re-derived from the same serial number, so no orphaned resources are left behind.

---

## Development

### Running the test suite

```
make test       # pytest — template rendering + unit tests
make lint       # ansible-lint on src/create_pcluster.yml and src/delete_pcluster.yml
make shellcheck # shellcheck on performance/scripts/*.sh (hpc-benchmark.sh and hpc-perftest.sh pass but are not in this target)
```

CI runs all three automatically on every push and pull request.

### Known ansible-lint warnings

`make lint` exits 0 but emits a small number of warnings that are intentional and safe to ignore:

| Warning | Reason |
|---|---|
| `var-naming` — `HeadNodePublicIP` | ParallelCluster's own register name convention; renaming would cascade across templates |
| `yaml[line-length]` — ssh/chown/cp commands | One-liners that are 162 chars (2 over limit); splitting would harm readability |
| `no-changed-when` | `pcluster` CLI commands are inherently stateful; `changed_when` on every poll would be misleading |
| `ignore-errors` | Intentional on cleanup tasks (S3 bucket, SNS topic, IAM role) that may not exist at delete time |
| `no-handler` | Deliberate pattern; notify/handler would require restructuring without benefit |

These are all tracked in `.ansible-lint` under `warn_list` with the same rationale.

---

## Things to Do

Potential future improvements, roughly ordered by impact:

### Architecture

- **Secrets manager integration** — SSH private keys are currently written to disk under `active_clusters/` with `0600` permissions.  Storing them in AWS Secrets Manager or SSM Parameter Store would eliminate the on-disk PEM file entirely and enable key rotation without cluster rebuild.

- **Live AWS integration tests** — the current test suite covers pure Python logic and Jinja2 template rendering.  A second test tier that provisions a real (minimal) cluster in a dedicated test account and tears it down would catch IAM policy gaps, CloudFormation drift, and Ansible playbook regressions that unit tests cannot.

- **Ansible Molecule test suite** — Molecule would allow the `create_pcluster.yml` and `delete_pcluster.yml` playbooks to be tested against a mock EC2 environment (e.g. Localstack or a dedicated sandbox account) without requiring a full cluster build.

- **Dynamic EFA instance lookup** — `ec2_instances_efa` in `src/pcluster_aux_data.py` is a manually maintained allowlist that lags AWS releases.  Replacing it with a live `describe-instance-types --filters Name=network-info.efa-supported,Values=true` call at launch time would keep it perpetually accurate without maintenance.

- **`kill_pcluster.py` profile inheritance from vars file** — when `make_pcluster.py` is run with `--turbot_account`, the Turbot profile is recorded in the cluster's vars file.  `kill_pcluster.py` could auto-detect and apply it from there instead of requiring the operator to re-specify `--turbot_account` at teardown.

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
