# Integration Tests

This directory contains a live integration smoke test that provisions a real
AWS ParallelCluster cluster, runs a Slurm job, and tears it down.

**These tests are NOT run by `make test`, `pytest`, or CI.**
Invoke them manually to validate end-to-end behavior against a real AWS
account.

The cluster name is generated per run as `itest-<HHMMSS>`.

## What it tests

`run_integration_test.sh` exercises the full cluster lifecycle:

1. Pre-flight: `aws sts get-caller-identity`, `pcluster` on `PATH`, `jq` on
   `PATH`, and `sys.prefix` resolving inside the repo `.venv`
2. Create: `make_pcluster.py --use_defaults <your defaults file>`
3. Poll `pcluster describe-cluster` for `CREATE_COMPLETE` — 60 attempts at 30 s
   apart, so a 30-minute limit; aborts early on `CREATE_FAILED` or
   `ROLLBACK_COMPLETE`
4. Resolve the head node IP and locate the `.pem` under
   `active_clusters/<cluster-name>/`
5. Wait for SSH (10 attempts at 30 s, 5-minute limit), then assert `hostname`
   contains the cluster name and `sinfo --noheader` returns output
6. Slurm: `sbatch` a 5-second job to the `compute` partition, poll `sacct` for
   `COMPLETED` (5-minute limit), assert `INTEGRATION_TEST_OK` in the output
7. Teardown: `kill_pcluster.py` from the `trap EXIT` handler

Step 6 submits to the `compute` partition, so the defaults file must define a
CPU queue. A GPU-only defaults file fails at `sbatch`.

## Prerequisites

- Python 3.12 venv activated: `source .venv/bin/activate`
- AWS credentials with sufficient IAM permissions (EC2, CloudFormation, IAM, S3, Secrets Manager)
- `jq` installed: `brew install jq` or `apt install jq`
- A defaults YAML file for your target environment (VPC, subnet, instance types, etc.)

## Defaults file

The script requires a `--defaults` file — it does **not** generate one.  This
is intentional: network topology (VPC name, subnets, AZ) and instance choices
vary per account and environment, and a generated file would hardcode wrong
values.

A known-good minimal template is at
`tests/integration/itest_defaults.yml.example`.  Copy it, fill in your VPC
name (and optionally explicit subnet IDs), then pass it with `--defaults`:

```bash
cp tests/integration/itest_defaults.yml.example tests/integration/itest_defaults.yml
# Edit itest_defaults.yml: set vpc_name and optionally headnode_subnet_id / compute_subnet_ids
```

`tests/integration/itest_defaults.yml` is gitignored so your account-specific
network values are never committed.  The `.example` file is committed as a
reference baseline.

The example disables monitoring, EFS, FSx, EFA, external NFS, and HPC
benchmarks — the minimum surface needed to verify the cluster lifecycle.  Add
optional features back selectively once the baseline smoke test passes.

The example provisions one `c5.xlarge` head node and a CPU queue of
`initial_cpu_queue_size: 2` / `max_cpu_queue_size: 2` `c5.xlarge` compute nodes
with `maintain_cpu_initial_size: "true"`, which pins `MinCount` at 2 so the
fleet does not scale to zero mid-run.  Three instances total.

## Usage

```bash
source .venv/bin/activate

./tests/integration/run_integration_test.sh \
    --az us-east-1a \
    --owner test \
    --email test@example.com \
    --defaults /path/to/my-itest_defaults.yml

# With a named AWS profile:
./tests/integration/run_integration_test.sh \
    --az us-east-1a \
    --owner test \
    --email test@example.com \
    --defaults /path/to/my-itest_defaults.yml \
    --profile my-aws-profile

# Leave cluster running after success (for inspection):
./tests/integration/run_integration_test.sh \
    --az us-east-1a \
    --owner test \
    --email test@example.com \
    --defaults /path/to/my-itest_defaults.yml \
    --keep
```

## Options

| Flag | Required | Description |
|---|---|---|
| `--az` | yes | Availability zone (e.g. `us-east-1a`); region is derived by stripping the last character |
| `--owner` | yes | Cluster owner; lowercase letters, digits, and hyphens only |
| `--email` | yes | Cluster owner email |
| `--defaults` | yes | Path to a pcluster defaults YAML file; must exist |
| `--profile` | no | AWS CLI profile name (inherits `$AWS_PROFILE` if omitted) |
| `--keep` | no | Skip teardown on success; leave cluster running for inspection |

Any unrecognized argument is a hard error.

## Cost estimate

`itest_defaults.yml.example` provisions three instances: one `c5.xlarge` head
node and two `c5.xlarge` compute nodes. At the us-east-1 on-demand rate of
$0.17/hr per instance, three instances cost $0.51/hr, so a 25-minute run is
about $0.21 and a 40-minute run about $0.34. EBS (three 50 GB gp3 root volumes
plus a 50 GB gp3 shared volume) and data transfer add a few cents.

Substitute the rate for your own region and instance types before quoting a
number. Compute nodes do not exist for the whole run — the fleet launches after
the head node — so treat these figures as an upper bound.

## Logs

Cluster name is `itest-<HHMMSS>`, so the log paths carry that name:

- `/tmp/itest-create-itest-<HHMMSS>.log` — full `make_pcluster.py` output,
  truncated per run
- `/tmp/itest-kill-itest-<HHMMSS>.log` — full `kill_pcluster.py` output,
  appended to

Slurm job output lands at `/tmp/itest-job.out` on the head node, which is gone
after teardown. Use `--keep` if you need to read it.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | All assertions passed; cluster torn down (unless `--keep`) |
| 1 | Bad or missing arguments, a failed pre-flight check, or a failed assertion |
| other | `set -euo pipefail` propagated a failed command's exit code — most often `make_pcluster.py`, which exits with the Ansible return code |

`cleanup()` runs from `trap EXIT` on both success and failure. `--keep` is
honored only when the test passed; a failing run is always torn down.

Two caveats on relying on the trap:

- The trap tears down only when `CLUSTER_CREATED=true`, which is set *after*
  `make_pcluster.py` returns. If `make_pcluster.py` itself fails, `pipefail`
  aborts the script before that assignment and no teardown runs. Partial
  CloudFormation stacks from a failed create must be cleaned up by hand.
- Teardown runs synchronously and `delete_pcluster.yml` waits up to 40 minutes
  (80 retries at 30 s) for `DELETE_COMPLETE`, but the trap wraps it in
  `set +e`, so a failed teardown does not change the script's exit code.

Either way, check the CloudFormation console and `/tmp/itest-kill-*.log` after
any run that did not report a clean teardown.
