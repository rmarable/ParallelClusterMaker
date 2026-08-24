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
  and `diagnose_pcluster.py`.
- IAM policy constraints live in `templates/CLAUDE.md` — read before touching
  any `.json_src`.
- HPC benchmark constraints live in `hpc-benchmark/CLAUDE.md` — read before
  touching `hpc-benchmark/`.
- The build summary names every filesystem's mount point on two live
  surfaces: `make_pcluster.py`'s printed summary (`_storage_summary_lines`
  in `pcluster_core.py`) and `templates/sns_build_summary_report.j2`. A
  line added to one must be added to the other.
  `src/create_pcluster.yml`'s `_build_summary` carries a third copy but no
  longer executes — update it only to keep the reference spec honest. `_storage_summary_lines` is keyword-only (14 same-typed
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
- **An undefined name must never reach a commit.** Nothing else checks
  Python for one: `make lint` runs ansible-lint on the two playbooks only,
  every AWS call is stubbed in tests, and a `NameError` inside a broad
  `except Exception` prints as a warning while the build reports success —
  all three failed together once, on a live build. `tests/test_undefined_names.py`
  gates it with pyflakes. Scoped to that one class on purpose; do not widen
  it to general linting without deciding to clear the backlog.
- **A duration, path or label stated on more than one surface needs a
  guard that they agree.** The teardown estimate lived in six places and
  drifted; `stage_dir` had two definitions that disagreed. The pattern is
  the same as the build summary's three surfaces: when a value cannot be
  generated from one source, pin the copies by test — and include a sweep
  that fails when a *new* surface appears, since the copy nobody knows
  about is the one that rots.
- **Every regional boto3 client must be built with
  `region_name=region`.** An unbound client resolves its endpoint from
  `AWS_DEFAULT_REGION`/`AWS_REGION`/the active profile, which need not be
  the region the build targets — so resources land in the wrong region
  while the build reports success. `iam` and `sts` are global and
  deliberately exempt.
  `TestEveryRegionalBotoClientIsBoundToTheTargetRegion` enforces this by
  AST; a stubbed client has no endpoint to be wrong about, so no
  behavioral test can see it.
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
- Cluster create and delete hold a per-cluster **S3** lock
  (`s3_acquire_cluster_lock`, `PutObject` with `IfNoneMatch`/`IfMatch`)
  from before the first AWS mutation through the whole operation. A second
  process fails fast naming the lock's owner, never waits. It replaced an
  earlier local mkdir lock, which could not see a second *machine* driving
  the same cluster. `_is_conditional_write_rejection` must keep treating
  **both** 412 and 409 as "someone else holds it" — a live 8-writer run
  proved the 409 path is reachable, and handling only 412 crashes a build
  under contention.
- The MCP server (`mcp_server/`) exposes `pcluster_core`'s `core_*`
  functions as tools on two FastMCP instances: `build_local()` (stdio,
  full set) and `build_remote()` (restricted). The exclusions are data in
  `_LOCAL_ONLY`, never a second registration list.
- **Lambda's function timeout is a hard 900s ceiling**, so no tool in
  `TOOL_TIERS` may block on a cluster operation — past it the function is
  killed mid-mutation, with the fleet stopped, an update in flight and the
  S3 lock held by a dead process. `apply_queue_config` is local-only for
  this reason; remote callers drive `stop_fleet` → `apply_cluster_update`
  → `start_fleet`. Decompose such a tool, never delete the capability.
- **Cognito access tokens carry no `aud` claim** — they carry `client_id`;
  only ID tokens have `aud`. `mcp_server/auth/authorizer_lambda.py`
  validates `client_id` and pins `token_use == "access"` so an ID token
  cannot authorize a tool call. Never add an `aud` fallback. Every auth
  failure raises `Unauthorized` (401, which prompts re-auth), never a Deny
  policy (403, which does not).
- The remote transport is a router plus four handler Lambdas split by IAM
  blast radius, one policy per tier in `templates/MCP*.json_src` — a third
  policy category, neither instance-reachable nor the operator's own. The
  router must import no third-party package.
- **No tool on the `read-only` tier may write anything.** `add_queue`/
  `remove_queue` write `configs/<name>.yaml` and moved to `stack-mutation`,
  where the rest of the config's lifecycle already lives (apply reads it,
  teardown deletes it). A tier named read-only carrying `s3:PutObject`
  misrepresents itself to whoever reads the policy next; mutating no
  CloudFormation stack is not the same as being read-only. The cost is
  accepted: a queue edit now carries that tier's blast radius.
- **Booleans are strings in the defaults, `bool` on `MakeClusterParams`, and
  truthiness-tested in `core_create_cluster`** — so `"false"` is truthy.
  `build_make_cluster_params` coerces via `_coerce_bool` over
  `typing.get_type_hints`; without it every feature flag reads as enabled.
- `build_make_cluster_params` validates `cluster_name`/`cluster_owner` — in
  the core, not the shims, which never did. `discover_defaults_file`
  excludes the tracked `pcluster_defaults.yml`: it sets all three
  `_REMOTE_DENIED_PARAMS`, so `cluster_name="pcluster"` defeated the denial.
- Cluster records are published to `s3://parallelclustermaker-locks-<acct>-<region>/vars/<name>.json`
  so a machine that did not build a cluster can still see it. Local files
  win: `_read_cluster_record` reads `active_clusters/` + `src/vars_files/`
  first and only falls back to the store. The `vars/` prefix is fixed by
  the `MCPStateAccess*` IAM policies, which granted it long before any code
  used it. Published after a successful build, deleted on teardown under
  the same `cf_delete_confirmed` gate as the credentials. The cluster
  config rides the same bucket under `configs/`, where writes are
  conditional on the ETag the read returned — `add_queue`/`remove_queue`
  take no cluster lock, so concurrent edits are an ordinary lost-update
  race and `ClusterConfigConflict` is what makes one visible. A *local*
  edit is conditional too. Which copy is stale is decided by the mirror
  marker (the ETag this machine last pushed), not by comparing content —
  content cannot tell ahead from behind. The CLI mirrors as well, and a
  store it cannot read degrades to a local-only edit with a warning. The bucket is
  addressed in the **cluster's** region, not the process's — everything
  that writes it already was. `add_queue`/`remove_queue` also refuse while
  the stack is `UPDATE_IN_PROGRESS`: `apply_cluster_update` returns on
  CloudFormation's acceptance, so the lock is released mid-update.
- **`MakeClusterParams` carries no `region`** — the CLI resolves it from
  the AZ-verification call and passes it to `core_create_cluster`
  separately, so every shim must too. MCP's `create_cluster` read
  `params.region` and raised `AttributeError` on every call; the one test
  reaching it asserted an error and got one from the earlier denied-param
  check. `resolve_region_from_az` (`pcluster_core.py`) is the shared
  resolution: it asks EC2 rather than trimming `az[:-1]`, which also proves
  the AZ exists.
- **A Lambda artifact must be pruned of bytecode.** `pip install --target`
  of the read-only tier is 241 MB against the 250 MB unzipped limit;
  removing `__pycache__`/`.pyc` takes it to 139 MB. `prune_for_lambda`
  returns the total so a build checks before uploading. The 55 MB zip
  exceeds the 50 MB direct-upload limit, so handler tiers go via S3.
  `aws-parallelcluster` *declares* 17 `aws-cdk.*` packages — the lazy
  import does not keep them out of the artifact.
- **`requirements.txt` is the development set and must never be installed
  into a Lambda artifact** — `ansible` alone is ~408 MB of collections for
  playbooks nothing executes. `mcp_server/packaging.py` holds the per-tier
  sets and generates `requirements-lambda.txt`.
- **A fake is written from the API's contract, never from memory of it.**
  For AWS that means `botocore/data/<service>/*/service-2.json` in `.venv`,
  which is authoritative and local. A fake built from recall encodes what
  the caller happens to need and therefore agrees with the code by
  construction — including where the code is wrong. The `_S3` fake hid
  three defects that way: it discarded `Bucket`, its `put_object` returned
  no `ETag` (silently disabling the config store's direction detection),
  and it always returned `Contents` with `IsTruncated: False`, so the
  hand-rolled pagination loop had never run a second iteration.
- **When a test stubs the object under test, at least one test must drive
  the real one.** `handlers/base.py` called a FastMCP method that does not
  exist; every stub defined the same wrong name, so the whole remote
  `tools/call` path was broken and every test passed.
- `config.pcluster.j2`'s `OnNodeConfigured` block is a shared Jinja2 macro
  called at all four `CustomActions` sites (`HeadNode`, `LoginNodes`, both
  `SlurmQueues`), not four independent copies — that duplication is what
  let `LoginNodes` silently inherit `HeadNode`'s `OnNodeStart` by
  copy-paste in the first place.

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
- `<cluster_name>_defaults.yml` is applied automatically when it exists,
  by the CLI and the MCP server both — `load_cluster_defaults`
  (`pcluster_core.py`) is the one loader. Precedence: explicit input >
  file > `MAKE_CLUSTER_DEFAULTS`. `--use_defaults` overrides it with a
  differently-named file. Non-build keys (`delete_s3_bucketname`) are
  ignored, not rejected. It is also the one path that may set the three
  `_REMOTE_DENIED_PARAMS` — that denial is scoped to `overrides`, since it
  exists to stop a network caller choosing what runs on the nodes, and the
  file is the operator's own.
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
- **Ask before assuming.** If a request is ambiguous — scope unclear, two
  readings exist, or a destructive action is implied — ask a focused
  clarifying question before proceeding.
- **No inline multi-line `python3 -c`.** Never run a `python3 -c '...'`
  block containing a newline followed by `#`. Write the script to
  `$CLAUDE_JOB_DIR/tmp/` first and invoke `python3 <path>` instead — this
  keeps Claude Code's argument-injection scanner from firing on every audit.

## Code style

- No comments unless the WHY is non-obvious.
- No docstrings beyond a single short line.
- No backwards-compatibility shims.
- Prefer editing existing files over creating new ones.
- No emojis.
- **American English everywhere** — docs, comments, docstrings, CLI help,
  error strings, Ansible `name:` fields ("personalize", "normalize",
  "analog", "behavior", "honor", "signaling", "neighboring", "defense",
  "unlabeled"). Exception: never "correct" a spelling that is part of an
  external contract — Slurm's `CANCELLED` job state is passed verbatim to
  `sacct --state=` in `diagnose_pcluster.py` and
  `tests/integration/run_integration_test.sh`, and Americanizing it breaks
  the query.
