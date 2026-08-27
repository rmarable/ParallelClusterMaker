# Installing ParallelClusterMaker

## Prerequisites

These are what the CLI needs. Both MCP surfaces are installed from the same
checkout, so everything here applies to them too.

* **Python 3.12.** Not 3.13 or 3.14 — see [Python version](#python-version).
* **AWS CLI v2**, configured with credentials that have sufficient IAM
  permissions. See [Operator IAM permissions](#operator-iam-permissions).
* **`git`**.
* **System build dependencies** — the compilers and libraries needed to
  build native Python and Ansible packages.
  * macOS: `brew install autoconf automake gcc jq libtool make readline`
  * Debian/Ubuntu: `sudo apt-get install autoconf automake gcc jq libtool make libreadline-dev`
  * RPM-based: the equivalents, with `readline-devel` for `libreadline-dev`.
* **Node.js >= 10.13.0, on `PATH`.** `pcluster create-cluster`/`update-cluster`
  shell out to the AWS CDK on the machine invoking `make_pcluster.py`, so
  without it they fail immediately with `Unable to find node executable`.
  * macOS: `brew install node`
  * Debian/Ubuntu: `sudo apt-get install nodejs`, or
    [NodeSource](https://github.com/nodesource/distributions) for a newer
    version than your distribution ships.
  * RPM-based: the `nodejs` package.

Two things are needed only for specific tasks, not to install or operate the
toolkit:

* **A container runtime** (Finch, Docker, Podman or Rancher) — only to
  create clusters *through the MCP connector*. Six of the transport's seven
  tiers are plain zips and need nothing; the seventh carries
  `create_cluster`, `apply_cluster_update`, `preview_cluster_config`
  and `finalize_cluster_build`.
  See [Adding cluster creation](#adding-cluster-creation-the-container-tier).
* **A modern `bash`, GNU `coreutils` and `shellcheck` on macOS** — only to
  run `make test`, `make lint` and `make shellcheck`. See
  [Development environment (macOS)](#development-environment-macos).

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
ansible-galaxy collection install -r requirements_ansible.yml
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

## Installing the MCP server

Optional, and independent of everything above except the venv. The MCP
server exposes the same `core_*` functions the CLI drives, so an AI agent
can operate clusters directly. There are two surfaces and they do not
depend on each other — install either, both, or neither.

Both run against your own AWS account. There is no hosted service.

### Locally, for Claude Code (stdio)

Runs on this machine, deploys nothing to AWS, and is reachable only by the
agent you attach it to. Run this **from the repository root** — the shell
expands `$(pwd)` before Claude Code sees it, so what gets stored is an
absolute path:

```bash
claude mcp add parallelclustermaker \
  -e PYTHONPATH="$(pwd)" \
  -- "$(pwd)/.venv/bin/python" -m mcp_server.server
```

Then **restart Claude Code** — a newly added stdio server is not picked up
by the session that added it — and run `/mcp` to confirm the tools are
listed. That is a stronger check than `claude mcp list` showing
`Connected`: the process starting and its tools being callable are
different claims.

Three things that will waste your time:

* **Do not invoke the script directly.** `python mcp_server/server.py` fails
  with `ImportError: FastMCP server support is not installed` — misleading,
  because `fastmcp` is installed. Running the file directly puts
  `mcp_server/` at the front of `sys.path` and shadows its own imports. Use
  `-m`.
* **`PYTHONPATH` is required, not optional.** Claude Code does not run the
  server from your project directory, and `-m mcp_server.server` needs the
  repository root on `sys.path`.
* **Do not use `--scope project`.** That writes `.mcp.json` into the
  repository root, which is tracked — on a public fork you would publish
  your own absolute paths.

You do not need the venv activated, but you must use `.venv/bin/python`:
`fastmcp`, `boto3` and `aws-parallelcluster` live only there.

Remove it with `claude mcp remove parallelclustermaker`.

### Remotely, for claude.ai (API Gateway + Cognito)

Deploys seven Lambda functions, a REST API and a Cognito user pool **into
your AWS account**, so a browser session can reach the same tools.

**Once, before your first deploy**, create the deployment policy and attach
it to the identity that will run `deploy_mcp.py`:

```bash
source .venv/bin/activate
./generate_operator_policy.py --mcp --create
```

It is separate from the operator policy because the two together exceed
IAM's 6,144-byte managed policy limit, and `deploy_mcp.py` cannot create it
itself — under that policy `iam:CreatePolicy` is scoped to
`pclustermaker-mcp-policy-*`, which this name does not match, and nothing
lets it attach a policy to your identity. That is deliberate: a deploy tool
able to grant itself permissions has no ceiling. What it grants is in
[MCP deployment permissions](#mcp-deployment-permissions).

Then, one command:

```bash
./deploy_mcp.py --bootstrap --create-user you@example.com
```

That creates the IAM roles and policies (each under a permissions
boundary), the Cognito user pool, the REST API with its Lambda authorizer
and routes, the Cognito app client and Hosted UI domain, and a user to sign
in as. It prints the MCP endpoint and, unless you set `MCP_USER_PASSWORD`,
a generated password **shown once** — Cognito stores a hash, so a lost one
is re-set by re-running `--create-user`, never recovered.

> **`--bootstrap` deploys six of the seven tiers.** The seventh ships as a
> container image, so it needs a container runtime, and `--bootstrap`
> leaves it out rather than requiring one on every machine.
>
> **Without it, four tools are missing from the connector** —
> `create_cluster`, `apply_cluster_update`, `preview_cluster_config`
> and `finalize_cluster_build`.
> In other words you can **inspect and operate** clusters from the browser
> but **not create or modify** them. Everything else is present: listing,
> health, cost, diagnostics, queues, fleet start/stop, access info and
> teardown.
>
> To get those four, see
> [Adding cluster creation](#adding-cluster-creation-the-container-tier).
> It is one command once a runtime is installed.

The deploy prints the three values this needs — the **MCP endpoint**, the
**OAuth client** ID, and the Cognito username and password. Then, in
claude.ai:

1. **Customize → Connectors**, and click the **`+`** beside Connectors.
   On Team and Enterprise plans an Owner adds it under **Organization
   settings → Connectors → Add → Custom → Web** instead; members then
   connect to it from their own **Customize → Connectors**.
2. Give it a name, and paste the **MCP endpoint** — the `/mcp` URL, not
   either discovery URL.
3. Open **Advanced settings** and set **OAuth Client ID** to the `OAuth
   client` value the deploy printed. Leave **OAuth Client Secret** empty.
4. Click **Add**, then **Connect**, and sign in at the Cognito page with
   the username and password from `--create-user`. Enter the username
   exactly as created: the pool sets no `UsernameAttributes`, so an email
   address is a literal username rather than an alias.
5. Approve the consent screen. Claude asks once per connector.

The tool list should show **13** tools, or **16** once the container tier
is deployed. Asking Claude to list clusters is the quickest check — an
**empty** answer is a successful one when no cluster exists.

The secret is left empty because the connector is a public client using
PKCE; one configured with a secret fails the token exchange.

### If the connector stops working

**Reloading the tool list is not the same as reconnecting.** A reload
reuses the stored OAuth session, so it keeps failing whenever the problem
*is* that session — most often because the Cognito app client it was minted
against has been deleted or rotated, or because `--teardown` removed the
pool. The symptom is "Couldn't reload tools from the server" while every
server-side check passes, which it will, because nothing is wrong with the
server. **Disconnect the connector and add it again.**

The first call after the transport has been idle can also take several
seconds and may report that the server did not respond; that one is a
Lambda cold start and a retry answers immediately.

The ID is supplied by hand because Cognito cannot register a client on
demand. The `/register` endpoint here works when called directly, but no
client reaches it: a client finds the authorization server through the
protected-resource document, that document names Cognito, and Cognito
advertises no `registration_endpoint`. Client ID Metadata Documents, which
the MCP spec now prefers, are an authorization-server feature Cognito also
lacks — it rejects a URL-formatted `client_id` outright. `--setup-gateway`
therefore creates the app client and prints its ID; it reuses an existing
one, so re-running does not change what you pasted.

Claude also reaches the server from Anthropic's cloud rather than from the
browser's machine, so the endpoint must be reachable from the public
internet — the deployed API Gateway is.

`--bootstrap` is idempotent, so it is also the update path.

Remove the whole transport with `./deploy_mcp.py --teardown` (add
`--dry-run` to list first). The permissions boundary is left behind on
purpose — a deployer who can delete their own boundary does not have one.

### Adding cluster creation (the container tier)

Add this tier when you want to **create and modify clusters** through the
connector, rather than only inspect and operate existing ones. It carries
`create_cluster`, `apply_cluster_update`, `preview_cluster_config` and
`finalize_cluster_build`, and
nothing else needs it.

It ships as a container image because `pcluster`'s create and update paths
require Node.js on `PATH`. AWS's Python Lambda runtimes have none, and a
zip artifact cannot add one — so this tier is built from
`mcp_server/Dockerfile.stack-mutation-node`.

**Install a container runtime, then run one command.** The deploy creates
the ECR repository, logs in, builds the image for `linux/amd64` and pushes
it:

```bash
./deploy_mcp.py --tier stack-mutation-node
```

Use `--runtime` to choose among several installed, or `--image-uri` to
deploy an image built elsewhere and skip the build entirely.
`./deploy_mcp.py --teardown` removes the repository along with everything
else.

#### Choosing a runtime

* macOS: [Finch](https://runfinch.com/) is the lightest and is AWS's own —
  `brew install --cask finch`, then `finch vm init` once (`finch vm start`
  on later boots). [Docker Desktop](https://www.docker.com/products/docker-desktop/),
  [Podman Desktop](https://podman-desktop.io/) and
  [Rancher Desktop](https://rancherdesktop.io/) work equally well; Docker
  Desktop requires a paid subscription for larger organizations, which the
  others do not.
* Linux: Docker Engine or Podman from your distribution's repositories.
  Neither needs a virtual machine, so there is nothing to start first.
  Podman is rootless by default and is simpler if you would rather not add
  your user to the `docker` group.
* Windows: Docker Desktop or Rancher Desktop with the WSL2 backend, and
  build from inside a WSL2 shell rather than PowerShell — the build tooling
  is POSIX shell. Finch does not support Windows.

#### What the deploy does for you

Three things that used to be manual steps, kept here because each is a
failure someone will otherwise hit head-on:

* **The ECR repository is created if absent.** It must exist before the
  first push — `finch push` and `docker push` will not create it, and the
  failure names the wrong cause: ECR answers an unknown repository with
  `repository does not exist or may require authorization`, whose second
  half sends you auditing an IAM permission that was never missing. The
  registry URI is read back from ECR rather than assembled, since the
  hostname suffix differs in GovCloud and China.
* **The image is built for `linux/amd64`, always.** An image built on
  Apple Silicon or an ARM Linux host defaults to `linux/arm64`, and Lambda
  rejects the mismatch at `CreateFunction` rather than at invocation — long
  after the push, with nothing pointing at the cause.
* **The ECR password reaches the runtime on stdin**, never in a command
  line, where `ps` would show it to every other process on the machine.

#### If the push fails with `no basic auth credentials`

**On Finch, `finch login` can succeed and `finch push` still report this.**
Finch runs containerd inside a Lima VM, and the push happens in the VM
rather than on the host. `finch login` writes the credential to the host —
by default handing it to the macOS keychain via `"credsStore":
"osxkeychain"` in `~/.finch/config.json`, which leaves the `auths` entry
empty — and the VM has no `~/.docker/config.json` of its own, so the pusher
finds nothing.

**Three locations are required and no two of them suffice**, each tested in
isolation: (1) the host config carrying an inline token, so `credsStore` is
removed and `finch login` re-run; (2) the VM's `$HOME/.docker/config.json`;
and (3) **the VM's `/root/.docker/config.json`, because containerd inside
the VM runs as root and reads root's config, not the invoking user's**.
Omitting the third fails with exactly the same message as omitting all
three, which is indistinguishable from having done nothing — verified
twice, once against a documented procedure that listed only the first two.
Do not skip the host half on the reasoning that the push happens in the VM,
and do not skip the root half on the reasoning that you already wrote a
config into the VM.

Scrub all three afterward. The ECR token is short-lived, but it is still a
credential at rest:

```sh
export LIMA_HOME=/Applications/Finch/lima/data
LIMACTL=/Applications/Finch/lima/bin/limactl

# host half: inline token rather than the macOS keychain
python3 -c "import json,os;p=os.path.expanduser('~/.finch/config.json');d=json.load(open(p));d.pop('credsStore',None);json.dump(d,open(p,'w'))"
aws ecr get-login-password --region <region> \
  | finch login --username AWS --password-stdin <acct>.dkr.ecr.<region>.amazonaws.com

# VM half
AUTH=$(printf 'AWS:%s' "$(aws ecr get-login-password --region <region>)" | base64 | tr -d '\n')
CFG=$(printf '{"auths":{"<acct>.dkr.ecr.<region>.amazonaws.com":{"auth":"%s"}}}' "$AUTH")
printf '%s' "$CFG" \
  | $LIMACTL shell finch -- sh -c 'mkdir -p $HOME/.docker && cat > $HOME/.docker/config.json'
# containerd in the VM runs as root and reads root's config, not yours
printf '%s' "$CFG" \
  | $LIMACTL shell finch -- sudo sh -c 'mkdir -p /root/.docker && cat > /root/.docker/config.json'
finch push <acct>.dkr.ecr.<region>.amazonaws.com/pclustermaker-mcp-stack-mutation-node:latest
$LIMACTL shell finch -- sh -c 'rm -f $HOME/.docker/config.json'
$LIMACTL shell finch -- sudo sh -c 'rm -f /root/.docker/config.json'
# and restore the host's keychain setting
python3 -c "import json,os;p=os.path.expanduser('~/.finch/config.json');d=json.load(open(p));d['credsStore']='osxkeychain';d['auths']={};json.dump(d,open(p,'w'))"
```

This is a Finch-specific workaround, confirmed on Finch; the other runtimes
were not tested against it. Docker Desktop and Rancher Desktop are not
expected to need it, since their builder reads the same host credential
store the CLI writes.

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

### MCP deployment permissions

**Only if you are deploying the remote transport.** The operator policy
above does not cover it, and not by oversight: `OperatorPolicy` scopes its
IAM statements to `pclustermaker-policy-*` and `pclustermaker-role-*`,
which match no MCP resource name. Widening it is not an option either —
the full MCP grant set appended to it measures 6,358 bytes against IAM's
6,144-byte managed policy limit, so it does not fit.

That permission set is `templates/MCPDeployPolicy.json_src`. It
grants the Lambda, API Gateway, Cognito, ECR and IAM actions the deploy
needs, and grants `iam:CreateRole` **only** under a `StringEquals`
condition on `iam:PermissionsBoundary` — without that condition a deployer
could create an unbounded role and the ceiling would never apply. It also
denies deleting the boundary itself.

`./generate_operator_policy.py --mcp --create` creates it, as
`parallelcluster-mcp-deploy-pclustermaker`; `--mcp` alone prints it. Attach
it to the identity that runs `deploy_mcp.py`, alongside the operator policy
— the two are complementary, not alternatives, and an identity that both
builds clusters and deploys the transport needs both.

The name is deliberately **not** of the form `pclustermaker-mcp-policy-*`:
that is the namespace of the seven handler policies, which
`deploy_mcp.py --teardown` removes. This one has to survive a teardown,
since you need it to run one.

`deploy_mcp.py` checks for these before its first mutation and stops with
the missing action names if any are absent, rather than failing partway
through a half-built transport. The check needs
`iam:SimulatePrincipalPolicy`, which this policy does not grant — when it
cannot run, the deploy says so and continues.

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
