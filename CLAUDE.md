# Claude Instructions — ParallelClusterMaker

Co-authored-by: Rodney Marable <rodney.marable@gmail.com>
Co-authored-by: Claude Code <noreply@anthropic.com>

This file is the lean, public reference. Full incident history, test-name
citations, and line-by-line rationale live in `CLAUDE.local.md` (gitignored,
local development only — see the Claude Code docs on `CLAUDE.local.md` for how
it loads automatically alongside this file).

## Always do first

If `CLAUDE-STATE.md` is present (gitignored, local-only), read it before taking any action — it records current branch, test status, pending work, and
standing constraints for local development.

## Git

- **Never `git commit` or `git push` without explicit confirmation from the
  user first**, even mid-task and even if the work seems complete. Do not ask
  about committing proactively during work — wait to be asked, and when asked,
  confirm scope before running the command.
- Work lands on `main`; there is no other branch in this repo.
- Do not amend existing commits unless explicitly asked.

## AI Submissions

- Contributor-facing policy lives in `AI_POLICY.md` — read it before any
  change involving AI assistance. This section only adds what governs
  operating as an AI agent in this repo; it does not restate that file.
- **Never open a GitHub issue or PR without explicit user confirmation**,
  the same rule as `git commit`/`git push` above — proposing a change is
  fine, filing it is not.
- Commits that used AI assistance carry `Co-Authored-By: <Tool/Model>
  <email>`, matching this repo's existing convention (e.g.
  `Co-Authored-By: Claude Code <noreply@anthropic.com>`) — do not invent a
  different trailer format.

## Architecture

- All Python logic must live in `src/pcluster_core.py` or
  `src/pcluster_aux_data.py` so it is testable without AWS credentials.
- `templates/vars_file.j2` renders with `StrictUndefined` — `| default()`
  does not rescue an undefined variable. Every variable must be defined
  upstream. Any new template variable must be traced through the full
  pipeline (Python vars dict → `vars_file.j2` → template) and added to
  `tests/conftest.py`.
- `pcluster_os` is derived from `base_os` by stripping the `arm` suffix
  (`pcluster_os = base_os.removesuffix("arm")`, in `make_pcluster.py`) —
  PCluster's `Os:` field does not accept an `arm` suffix.
- Eight `base_os` values are supported, two package-manager families:
  `ubuntu2204`, `ubuntu2404`, `ubuntu2204arm`, `ubuntu2404arm`, `rhel9`,
  `rhel9arm`, `alinux2023`, `alinux2023arm`. `preinstall.j2`/`postinstall.j2`
  branch on `'ubuntu' in base_os`, then split again on `'alinux' in base_os`.
  Any new supported OS value must be threaded through: `make_pcluster.py`
  argparse choices, `_EC2_USERS`/`_resolve_ec2_user` in `pcluster_core.py`,
  `ARM_OSES`/`X86_OSES` in `pcluster_aux_data.py`, `_VALID_EC2_USERS` in
  `diagnose_pcluster.py`, and the `assert` task (must stay task index 0) in
  `src/create_pcluster.yml`.
- IAM policy constraints live in `templates/CLAUDE.md` — read before touching
  any `.json_src`.
- HPC benchmark constraints live in `hpc-benchmark/CLAUDE.md` — read before
  touching `hpc-benchmark/`.
- The build summary names every filesystem's mount point on three surfaces:
  `make_pcluster.py`'s printed summary (`_storage_summary_lines` in
  `pcluster_core.py`), `src/create_pcluster.yml`'s `_build_summary`, and
  `templates/sns_build_summary_report.j2`. A line added to one must be added
  to all three. `_storage_summary_lines` is keyword-only (14 same-typed
  params) — never call it positionally. The mount-point column width is
  derived from the longest active label (`max(len(label)) + 2`), never a
  hardcoded constant — a fixed width of 6 didn't fit the 7-character
  `/shared` default and silently dropped its padding.
- `postinstall.j2` runs on the head node **and** every compute node.
  `NODE_TYPE` must be read from `cfn_node_type` in
  `/etc/parallelcluster/cfnconfig` — there is no `PARALLELCLUSTER_NODE_TYPE`
  environment variable. Anything touching shared storage (`$HOME`, `/shared`,
  `/efs`, `/fsx`, `/opt/parallelcluster/shared`) must be gated on
  `NODE_TYPE == HeadNode`; packages needed on compute nodes must be installed
  outside that gate.
- The toolkit's own `preinstall.j2`/`postinstall.j2` and the operator's
  `--pre_install_script`/`--post_install_script` hook are two distinct
  stages with distinct vars-file variables — the toolkit pair must be
  rendered with a `template:` task, never `copy:`.
- Only the head node may write `MONITORING_HOME`; the gate reads
  `cfn_node_type`, never `!= "ComputeFleet"`, and never carries a `:-`
  default.
- Monitoring tooling: gated on `enable_monitoring`; downloaded once at build
  time and staged to S3 (never fetched from GitHub by a node, to support
  private subnets); checksum-verified via `monitoring_version_checksum`.
  Creates a fifth managed IAM policy (`HeadNode-Monitoring`) and an SSM
  Grafana password parameter — both must be deleted on teardown.
- GPU support is gated on `enable_gpu`; `is_gpu_instance()` in
  `pcluster_aux_data.py` detects GPU families by instance-type prefix and
  auto-enables the flag with an `*** INFO ***` message if needed.
  EFA-GDR is derived from `enable_gpu` + `enable_efa` + instance family via
  `needs_efa_gdr(instance_type)` (single argument only).
- `job_hpc-benchmark.sh.j2`'s `--partition` and `--ntasks-per-node` are
  derived from the cluster's own shape (`enable_cpu_queue`,
  `gpu_ranks_per_node`/`cpu_ranks_per_node` in `make_pcluster.py`) — never
  hardcoded.
- FSx hydration uses one S3 bucket with two prefixes (import and export must
  name the same bucket) — enforced by `_normalize_fsx_buckets` in
  `pcluster_core.py`.
- Every download checksum (monitoring tarball, Docker Compose plugin) is
  validated in `make_pcluster.py` via `_validate_download_checksum` *before*
  the first AWS mutation (`_setup_iam`). No `_HARDCODED_DEFAULTS` entry may
  be a placeholder.
- `enable_external_nfs` gets a pre-flight reachability check
  (`_check_external_nfs_reachable`, before `_setup_iam`) — but only a
  confirmed-empty `showmount` export list is a hard failure. It runs from
  the operator's machine, not the target VPC, so an unreachable port or a
  missing `showmount` binary only warns and lets the build proceed.
- Performance/benchmark results sync to the long-lived
  `parallelclustermaker-results-<account_id>-<region>` bucket (derived by
  `_derive_results_bucket`, keyword-only on `aws_account_id`+`region` only),
  never to the per-build `s3_bucketname` that teardown deletes. All
  performance-related tasks are gated on `enable_hpc_benchmarks`.
- Secrets Manager stores the SSH private key at
  `parallelcluster/<cluster_name>/<serial>/ssh-private-key`, deleted on
  teardown. Remote shell scripts for key rotation live in `pcluster_core.py`
  (`_append_key_script`/`_remove_old_key_script`), not inline in
  `rotate_cluster_key.py`.
- `templates/access_cluster.j2` and `templates/grafana_tunnel.j2` must
  distinguish a failed AWS call from a genuinely stopped cluster (rc and
  stderr, not `2>/dev/null || true`) — `_describe_node` in the former
  (renamed once it could resolve login nodes too), `_describe_head_node`,
  untouched, in the latter.
- Login node support (`--enable_loginnode`, default `"false"`) adds a
  `LoginNodes:` pool sibling to `HeadNode:`/`Scheduling:` in
  `config.pcluster.j2`. `loginnode_instance_type`'s hardcoded fallback is
  architecture-aware (`_default_loginnode_instance_type` in
  `pcluster_core.py`: `c8g.xlarge` on Graviton `base_os`, `c5.xlarge` on
  x86_64) — a flat literal would silently fail preflight for an operator who
  opts in on an x86_64 cluster without also setting the flag.
- The login node gets `ComputeNode-Base` via `AdditionalIamPolicies`, never
  HeadNode's `InstanceRole` — head-node-level privileges there would defeat
  the feature's purpose of keeping general users off that surface.
- `postinstall.j2` treats `NODE_TYPE == "LoginNode"` like `ComputeFleet`,
  not `HeadNode` — it NFS-mounts the same shared `/home`, and running the
  Spack/Lmod build steps there would race the head node (and every other
  login node in the pool) writing the same paths concurrently at boot.
- `config.pcluster.j2`'s `LoginNodes` block gets `OnNodeConfigured` only,
  never `OnNodeStart` — that is `preinstall.j2` (full package upgrade, pip
  installs, awscli download), reserved for `HeadNode`. Giving login nodes
  `OnNodeStart` repeats all of that on every boot and every ASG replacement.
- `monitoring-post-install-wrapper.j2`'s `LoginNode` arm polls (bounded,
  `MONITORING_LOGIN_WAIT_SECONDS`/`_POLL_SECONDS`) for the head node's
  `MONITORING_HOME` instead of failing immediately — PCluster's CDK gates
  the login-node pool only on the head node's EC2 instance existing, not its
  bootstrap completing, unlike `ComputeFleet`'s `clustermgtd` ordering.
- `access_cluster.py`'s default node selection also requires
  `loginnode_count > 0` — `enable_loginnode=true` alone does not guarantee
  any login-node instance is actually running.
- AWS ParallelCluster exposes no `RootVolume`/`LocalStorage` key for
  `LoginNodes/Pools` — the login node's root volume can be neither sized
  nor encrypted through this toolkit; it always uses the AMI default.
- Teardown's credential-destroying tasks (EC2 keypair, local `.pem`,
  Secrets Manager secret, `active_clusters/<cluster>/`) must be gated on
  positive confirmation the CloudFormation stack is gone — never on
  `not (_cf_delete_failed | bool)`, which also fires on a wait timeout.
- The `results_bucketname` derivation must be safe to run against an older
  vars file that predates the key — teardown derives it via
  `_derive_results_bucket` (same function, not a restated literal) when the
  vars file doesn't define it.

## Environment

- `.venv/` is excluded from git; all dependencies are in `requirements.txt`.
- **Python 3.12 only.** `aws-parallelcluster` does not support 3.13/3.14.
  Always create `.venv` with `python3.12 -m venv .venv`.
- Venv guard uses `sys.prefix` (not `sys.executable`) — Homebrew Python
  symlinks resolve outside `.venv/`.
- Shebangs use `#!/usr/bin/env python`, not `python3` — `env python3` on
  macOS bypasses the active venv.
- Ansible deprecation warnings are suppressed globally via `ansible.cfg`
  (`deprecation_warnings = False`). Do not re-enable or work around per-task.
- If `<cluster_name>_defaults.yml` exists but `--use_defaults` was not
  passed, `make_pcluster.py` prints a `*** WARNING ***` — never suppress it.
- **Node.js (>= 10.13.0) must be on `PATH` locally.** `pcluster
  create-cluster`/`update-cluster` shell out to the AWS CDK library
  ParallelCluster uses to synthesize CloudFormation — on the operator's
  machine, never a cluster node — and fail immediately with `Unable to find
  node executable` without it. Not a toolkit dependency to work around;
  install it (`INSTALL.md`).

## Test suite

**Always use the project venv** — `.venv/bin/python`/`.venv/bin/pytest`, never
the system Python. `make test`, `make lint`, and `make shellcheck` are the
three gates; see the `Makefile`.

- The suite runs on macOS locally and Linux in CI. A green local run is not
  evidence CI is green: `tests/test_shell_surfaces.py`'s stub `PATH` must
  carry `gzip` (GNU `tar -xzf` forks it; macOS bsdtar doesn't need it), and
  any test touching PCluster's config objects needs `AWS_REGION`/
  `AWS_DEFAULT_REGION` set via `monkeypatch.setenv` (botocore prefers
  `AWS_REGION`).
- CI creates `.venv/` explicitly via `python -m venv .venv && .venv/bin/pip
  install -r requirements.txt` — the top-level scripts fire a `sys.exit()`
  venv guard at import time, so running pytest outside `.venv/` fails
  collection.
- Run the test suite after any change to Python, Jinja2 templates, or
  `conftest.py`.
- The CLAUDE.md-family doc-hygiene tests live in three files under `tests/`
  (citation sweep, line-citation sweep, preamble byte budget), gitignored
  alongside `CLAUDE.local.md`/`CLAUDE-STATE.md` — they test properties of
  files that aren't part of the public repo.

## Behavior

- **Don't fabricate.** If a function, file, flag, or AWS behavior is not
  confirmed by reading the actual code or docs, say so — do not invent
  plausible-sounding details.
- **Don't guess silently.** If something is uncertain, say it is uncertain.
  A wrong confident answer is worse than an honest "I don't know."
- **Ask before assuming.** If a request is ambiguous, ask a focused
  clarifying question before proceeding.
- **No inline multi-line `python3 -c`.** Never run a `python3 -c '...'`
  block containing a newline followed by `#`. Write the script to
  `$CLAUDE_JOB_DIR/tmp/` first and invoke `python3 <path>` instead.

## Code style

- No comments unless the WHY is non-obvious.
- No docstrings beyond a single short line.
- No backwards-compatibility shims.
- Prefer editing existing files over creating new ones.
- No emojis.
- **American English everywhere**, except where a spelling is part of an
  external contract (e.g. Slurm's `CANCELLED` job state, passed verbatim to
  `sacct --state=`).
