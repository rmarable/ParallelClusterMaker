# Installing ParallelClusterMaker

## Prerequisites

* Python 3.12 is required. See [Python version](#python-version) for why.
* AWS CLI v2 must be installed and configured with credentials that have sufficient IAM permissions.
* `git` must be installed.
* System build dependencies must be installed. These are the compilers and libraries needed to build native Python and Ansible packages.
  * On macOS, run `brew install autoconf automake gcc jq libtool make readline`.
  * On Debian/Ubuntu (`apt`), run `sudo apt-get install autoconf automake gcc jq libtool make libreadline-dev`.
  * On RPM-based distributions, install the equivalents with your package manager; use `readline-devel` instead of `libreadline-dev`.
* macOS also needs a modern `bash`, GNU `coreutils`, and `shellcheck` to run the test suite. See [Development environment (macOS)](#development-environment-macos). These are not required to operate the toolkit itself — only `make test`, `make lint`, and `make shellcheck` need them.
* Node.js >= 10.13.0 must be installed and on `PATH`. `pcluster create-cluster`/`update-cluster` shell out locally to the AWS CDK library ParallelCluster uses to synthesize the CloudFormation stack — this runs on the machine invoking `make_pcluster.py`, not on any cluster node, so it is not listed in `pcluster_defaults.yml`'s `base_os` package sets. Without it, `create-cluster` fails immediately with `Unable to find node executable`.
  * On macOS, run `brew install node`.
  * On Debian/Ubuntu (`apt`), run `sudo apt-get install nodejs`, or follow [NodeSource's install instructions](https://github.com/nodesource/distributions) for a newer version than your distribution ships.
  * On RPM-based distributions, install the `nodejs` package with your package manager.

## Python version

`aws-parallelcluster` does not support Python 3.13 or 3.14. Python 3.14 changed `asyncio.get_event_loop()` to raise `RuntimeError` when no event loop is running, so `pcluster create-cluster` fails with `"There is no current event loop in thread 'MainThread'"` even though `pip install` succeeds without warning. The upstream fix (PR [#7149](https://github.com/aws/aws-parallelcluster/pull/7149)) is unmerged as of v3.15.1. Use Python 3.12. The `.python-version` file in the repository root pins the venv to it.

The toolkit runs from a virtual environment (`.venv/`) inside the repository. The venv is gitignored, and its state is captured by `requirements.txt`, which is tracked.

This toolkit has not been tested on Windows.

## Installing the toolkit

Clone the repository and create the virtual environment:

```bash
cd ~/src
git clone https://github.com/rmarable/ParallelClusterMaker.git
cd ParallelClusterMaker
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
ansible-galaxy collection install -r requirements.ansible.yml
```

Deactivate the environment when you are done:

```bash
deactivate
```

Reactivate it in a later session:

```bash
cd ~/src/ParallelClusterMaker
source .venv/bin/activate
```

Run this to see all available options for `make_pcluster.py`:

```bash
./make_pcluster.py --help
```

## VPC tagging

Apply a Name tag to any VPC in regions where you plan to deploy cluster stacks. In the AWS Console, go to VPC → Your VPCs and edit the Name field. For example:

* Tag the VPC "nova" in us-east-1.
* Tag the VPC "cleveland" in us-east-2.
* Tag the VPC "dublin" in eu-west-1.

This step may require assistance from your organization's IT or DevOps team.

## Operator IAM permissions

The IAM identity that runs this toolkit — a user, role, or SSO permission set — needs two layers of permissions.

**1. AWS ParallelCluster base permissions.** AWS documents the minimum permissions required to create and delete PCluster stacks. Attach the AWS-managed policy `arn:aws:iam::aws:policy/AdministratorAccess` in development or sandbox environments. For production, follow the [PCluster IAM permissions documentation](https://docs.aws.amazon.com/parallelcluster/latest/ug/iam-roles-in-parallelcluster-v3.html) to build a scoped policy instead.

**2. ParallelClusterMaker operator policy.** This toolkit requires additional permissions that PCluster's base policy does not cover. Generate and optionally create this managed policy as follows:

```bash
source .venv/bin/activate

# Print the rendered policy JSON (account ID resolved automatically):
./generate_operator_policy.py

# Write it to a file:
./generate_operator_policy.py --output operator-policy.json

# Create the managed policy in IAM and print the attachment commands:
./generate_operator_policy.py --create
```

The generated policy (`parallelcluster-operator-pclustermaker`) covers the following statements:

| Statement | Purpose |
|---|---|
| `IAMManagedPolicyLifecycle` | Create/delete the 4–5 cluster managed policies (head node + compute) |
| `IAMListEntitiesForPolicy` | Enumerate what a `pclustermaker-policy-*` policy is attached to |
| `IAMAttachDetachClusterPolicies` | Attach/detach those policies to the cluster IAM role; scoped to `pclustermaker-policy-*` policy ARNs only |
| `IAMRoleLifecycle` | Create, delete, and configure the cluster EC2 IAM role; `PassRole` to EC2 |
| `IAMInstanceProfile` | Create, populate, and clean up instance profiles |
| `EC2Keypair` | Create, import, and delete SSH keypairs |
| `SNSClusterAlerts` | Manage the per-cluster `sns_alerts_*` topic and publish build/teardown summaries |
| `SNSListTopics` | Locate an existing cluster topic |
| `S3ClusterBucketLifecycle` | Create, configure, and delete the cluster's `parallelclustermaker-*` bucket |
| `S3ClusterObjects` | Read/write/delete objects in that bucket (scripts, benchmark results) |
| `S3ListAllBuckets` | Bucket-name collision check |
| `SecretsManagerSSHKey` | Store and retrieve SSH private keys under `parallelcluster/*` |
| `SSMGrafanaPassword` | Manage the Grafana admin password parameter under `/parallelcluster/*` (monitoring only) |
| `CloudWatchLogsDescribeGlobal` | `logs:DescribeLogGroups` — requires `Resource: "*"`; the API call carries no log group ARN |
| `CloudWatchLogsDiagnose` | `diagnose_pcluster.py` — read bootstrap log streams under `/aws/parallelcluster/*` |
| `PricingReadOnly` | Build summary hourly cost estimates |
| `CostExplorer` | `cost_pcluster.py` spend reporting |
| `Route53ClusterHostedZone` | Private hosted zone lifecycle for cluster DNS; zone IDs are AWS-generated, so this cannot be scoped per cluster |
| `STSCallerIdentity` | Account ID resolution |

After generating the policy, attach it to your identity:

```bash
# For an IAM user:
aws iam attach-user-policy \
  --user-name <YOUR_USERNAME> \
  --policy-arn arn:aws:iam::<ACCOUNT_ID>:policy/parallelcluster-operator-pclustermaker

# For an IAM role:
aws iam attach-role-policy \
  --role-name <YOUR_ROLE_NAME> \
  --policy-arn arn:aws:iam::<ACCOUNT_ID>:policy/parallelcluster-operator-pclustermaker
```

## Development environment (macOS)

Running `make test`, `make lint`, and `make shellcheck` on macOS requires a modern `bash`, GNU `coreutils`, and `shellcheck`. None of these ship with the OS.

* macOS's `/bin/bash` is frozen at version 3.2, from 2007, for licensing reasons, and the shell-surface tests require bash 4+/5 features such as `mapfile`.
* `nproc` is a GNU coreutils command, not part of the BSD userland macOS ships, and `hpc-benchmark.sh` calls it directly.
* `make shellcheck` requires the `shellcheck` binary itself.

Without these tools, tests fail with messages like `nproc: command not found`, or they produce a silently wrong result from a bash-version mismatch. Neither failure mode indicates an actual defect.

Install the missing tools with Homebrew:

```bash
brew install bash shellcheck coreutils
```

`bash` and `shellcheck` resolve automatically once installed, because Homebrew's `/opt/homebrew/bin` precedes `/bin` on `PATH` by default. `coreutils` installs its GNU tools with a `g` prefix, for example `gnproc`, to avoid shadowing the BSD versions. Prepend its `gnubin` directory to `PATH` so the unprefixed names resolve as well:

```bash
export PATH="/opt/homebrew/opt/coreutils/libexec/gnubin:$PATH"
```

This step has no effect on Linux, where CI runs, because a modern bash, coreutils, and `shellcheck` are already standard there.
