# Integration Tests

This directory contains a live integration smoke test that provisions a real
AWS ParallelCluster cluster, runs a Slurm job, and tears it down.

**These tests are NOT run by `make test`, `pytest`, or CI.**
You must invoke them manually when you want to validate end-to-end behavior
against a real AWS environment.

## What it tests

`run_integration_test.sh` exercises the full cluster lifecycle:

1. Pre-flight: AWS credentials, `pcluster` CLI, `jq`, active venv
2. Create: `make_pcluster.py` with a minimal defaults file
3. Assert `CREATE_COMPLETE` (CloudFormation polling, 30-minute limit)
4. SSH smoke test: hostname contains cluster name, `sinfo` returns output
5. Slurm job: `sbatch` a 5-second job, poll for `COMPLETED`, verify output
6. Teardown: `kill_pcluster.py` via `trap EXIT` (runs on success AND failure)

## Prerequisites

- Python 3.12 venv activated: `source .venv/bin/activate`
- AWS credentials with sufficient IAM permissions (EC2, CloudFormation, IAM, S3)
- `jq` installed: `brew install jq` or `apt install jq`
- The target AZ must have a default VPC with at least one public subnet

## Usage

```bash
source .venv/bin/activate

./tests/integration/run_integration_test.sh \
    --az us-east-1a \
    --owner yourusername \
    --email you@example.com

# With a named AWS profile:
./tests/integration/run_integration_test.sh \
    --az us-east-1a \
    --owner yourusername \
    --email you@example.com \
    --profile my-aws-profile

# Leave cluster running after success (for inspection):
./tests/integration/run_integration_test.sh \
    --az us-east-1a \
    --owner yourusername \
    --email you@example.com \
    --keep
```

## Cluster configuration

| Parameter | Value |
|---|---|
| Head node | c5.xlarge |
| Compute nodes | 2 x c5.xlarge (maintained) |
| Scheduler | Slurm |
| OS | ubuntu2404 |
| Purchasing | On-demand (no spot flakiness) |
| EFS / FSx | disabled |
| Monitoring | disabled |
| Performance tests | disabled |

## Cost estimate

~$0.50 per run at us-east-1 on-demand pricing (3 x c5.xlarge @ $0.17/hr for
~25-40 minutes, plus negligible EBS and data transfer charges).

## Logs

Full create and teardown output is written to:

- `/tmp/itest-create-<cluster-name>.log`
- `/tmp/itest-kill-<cluster-name>.log`

The defaults file written to the repo root (`<cluster-name>_defaults.yml`) is
deleted automatically by the `trap EXIT` handler.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | All assertions passed; cluster torn down (unless `--keep`) |
| 1 | A pre-flight check, AWS operation, or assertion failed; teardown attempted |

The teardown step in `cleanup()` runs on both success and failure, so resources
are cleaned up even when the test fails mid-run.
