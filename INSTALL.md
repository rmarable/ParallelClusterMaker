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
* An OCI container runtime is required **only to deploy the MCP remote transport**, and only for one of its five tiers. Nothing in normal operation needs it: building, operating and tearing down clusters from the CLI, and running the MCP server locally over stdio, all work without a container runtime. Skip this unless you are standing up the hosted (browser-reachable) MCP topology described in `docs/parallelclustermaker-mcp-plan.md`.
  * The `stack-mutation-node` tier ships as a container image rather than a zip, and the reason is Node.js again. `pcluster`'s `create_cluster()` and `update_cluster()` call `assert_valid_node_js()` as their first statement, which does `shutil.which("node")` and fails loudly without it. AWS's Python Lambda runtimes ship no Node and a zip artifact cannot add one, so that tier — which carries `create_cluster`, `apply_cluster_update` and `preview_cluster_config` — is built from `mcp_server/Dockerfile.stack-mutation-node` and pushed to ECR. The other four tiers are plain zips and need no runtime at all.
  * On macOS, [Finch](https://runfinch.com/) is the lightest option and is AWS's own: `brew install --cask finch`, then `finch vm init` once (`finch vm start` on later boots). [Docker Desktop](https://www.docker.com/products/docker-desktop/), [Podman Desktop](https://podman-desktop.io/) and [Rancher Desktop](https://rancherdesktop.io/) all work equally well; Docker Desktop requires a paid subscription for larger organizations, which the others do not.
  * On Linux, install Docker Engine or Podman from your distribution's repositories — neither needs a virtual machine, so there is nothing to start first. Podman is rootless by default and is the simpler choice if you would rather not add your user to the `docker` group.
  * On Windows, use [Docker Desktop](https://www.docker.com/products/docker-desktop/) or [Rancher Desktop](https://rancherdesktop.io/) with the WSL2 backend, and build from inside a WSL2 shell rather than from PowerShell — the repository's build tooling is POSIX shell. Finch does not support Windows.
  * **Build for the Lambda function's architecture, not your laptop's.** An image built on Apple Silicon or an ARM Linux host defaults to `linux/arm64`, and Lambda rejects a mismatch at `CreateFunction` rather than at invocation. Pass `--platform linux/amd64` unless the function is configured for `arm64`.
  * **The ECR repository must exist before the first push.** `finch push` (and `docker push`) will not create it, and the failure names the wrong cause: ECR answers an unknown repository with `repository does not exist or may require authorization`, whose second half sends you auditing IAM for a permission that was never missing. Create it once with `aws ecr create-repository --repository-name pclustermaker-mcp-stack-mutation-node --region <region>`.
  * **On Finch, `finch login` can succeed while `finch push` reports `no basic auth credentials`.** Finch runs containerd inside a Lima VM, and the push happens in the VM rather than on the host. `finch login` writes the credential to the host — by default handing it to the macOS keychain via `"credsStore": "osxkeychain"` in `~/.finch/config.json`, which leaves the `auths` entry empty — and the VM has no `~/.docker/config.json` of its own, so the pusher finds nothing. **Both halves are required, and neither alone works** — confirmed by testing each in isolation: removing `credsStore` and re-running `finch login` (so the host config carries an inline token) *and* writing the credential into the VM. With only the VM file the push still fails with the same message, so do not skip the host half on the reasoning that the push happens in the VM. Scrub both afterward (the ECR token is short-lived, but it is still a credential at rest):

    ```sh
    export LIMA_HOME=/Applications/Finch/lima/data
    LIMACTL=/Applications/Finch/lima/bin/limactl

    # host half: inline token rather than the macOS keychain
    python3 -c "import json,os;p=os.path.expanduser('~/.finch/config.json');d=json.load(open(p));d.pop('credsStore',None);json.dump(d,open(p,'w'))"
    aws ecr get-login-password --region <region> \
      | finch login --username AWS --password-stdin <acct>.dkr.ecr.<region>.amazonaws.com

    # VM half
    AUTH=$(printf 'AWS:%s' "$(aws ecr get-login-password --region <region>)" | base64 | tr -d '\n')
    printf '{"auths":{"<acct>.dkr.ecr.<region>.amazonaws.com":{"auth":"%s"}}}' "$AUTH" \
      | $LIMACTL shell finch -- sh -c 'mkdir -p $HOME/.docker && cat > $HOME/.docker/config.json'
    finch push <acct>.dkr.ecr.<region>.amazonaws.com/pclustermaker-mcp-stack-mutation-node:latest
    $LIMACTL shell finch -- sh -c 'rm -f $HOME/.docker/config.json'
    # and restore the host's keychain setting
    python3 -c "import json,os;p=os.path.expanduser('~/.finch/config.json');d=json.load(open(p));d['credsStore']='osxkeychain';d['auths']={};json.dump(d,open(p,'w'))"
    ```

    This is a Finch-specific workaround, confirmed on Finch; the other runtimes were not tested against it. Docker Desktop and Rancher Desktop are not expected to need it, since their builder reads the same host credential store the CLI writes.

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
