# Integration Tests

This directory contains a live integration smoke test that provisions a real
AWS ParallelCluster cluster, runs a Slurm job, and tears it down.

**These tests are NOT run by `make test`, `pytest`, or CI.**
You must invoke them manually when you want to validate end-to-end behavior
against a real AWS environment.

## What it tests

`run_integration_test.sh` exercises the full cluster lifecycle:

1. Pre-flight: AWS credentials, `pcluster` CLI, `jq`, active venv
2. Create: `make_pcluster.py` with your defaults file
3. Assert `CREATE_COMPLETE` (CloudFormation polling, 30-minute limit)
4. SSH smoke test: hostname contains cluster name, `sinfo` returns output
5. Slurm job: `sbatch` a 5-second job, poll for `COMPLETED`, verify output
6. Teardown: `kill_pcluster.py` via `trap EXIT` (runs on success AND failure)

## Prerequisites

- Python 3.12 venv activated: `source .venv/bin/activate`
- AWS credentials with sufficient IAM permissions (EC2, CloudFormation, IAM, S3)
- `jq` installed: `brew install jq` or `apt install jq`
- A defaults YAML file for your target environment (VPC, subnet, instance types, etc.)

## Defaults file

The script requires a `--defaults` file — it does **not** generate one.  This
is intentional: network topology (VPC name, subnets, AZ) and instance choices
vary per account and environment, and a generated file would hardcode wrong
values.

A known-good minimal template is provided at
`tests/integration/itest_defaults.yml.example`.  Copy it, fill in your VPC
name (and optionally explicit subnet IDs), then pass it with `--defaults`:

```bash
cp tests/integration/itest_defaults.yml.example tests/integration/itest_defaults.yml
# Edit itest_defaults.yml: set vpc_name and optionally headnode_subnet_id / compute_subnet_ids
```

`tests/integration/itest_defaults.yml` is gitignored so your account-specific
network values are never committed.  The `.example` file is committed as a
reference baseline.

The example disables monitoring, EFS, FSx, EFA, and performance tests — the
minimum surface needed to verify the cluster lifecycle.  Add optional features
back selectively once the baseline smoke test passes.

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
| `--az` | yes | Availability zone (e.g. `us-east-1a`) |
| `--owner` | yes | Cluster owner username |
| `--email` | yes | Cluster owner email |
| `--defaults` | yes | Path to a pcluster defaults YAML file |
| `--profile` | no | AWS CLI profile name (inherits `$AWS_PROFILE` if omitted) |
| `--keep` | no | Skip teardown on success; leave cluster running for inspection |

## Cost estimate

Varies by instance type and region. As a reference: 3 x c5.xlarge on-demand
in us-east-1 for ~25-40 minutes costs approximately $0.50, plus negligible EBS
and data transfer charges.

## Logs

Full create and teardown output is written to:

- `/tmp/itest-create-<cluster-name>.log`
- `/tmp/itest-kill-<cluster-name>.log`

## Exit codes

| Code | Meaning |
|---|---|
| 0 | All assertions passed; cluster torn down (unless `--keep`) |
| 1 | A pre-flight check, AWS operation, or assertion failed; teardown attempted |

The teardown step in `cleanup()` runs on both success and failure, so resources
are cleaned up even when the test fails mid-run.
