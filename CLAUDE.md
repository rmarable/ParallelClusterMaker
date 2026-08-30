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
  any `.json_src`. Six managed policies now: `ClusterNode-Deny` is
  Deny-only, unconditional, and on every node; the head node role alone is
  created under the `pclustermaker-cluster-boundary` permissions boundary.
- HPC benchmark constraints live in `hpc-benchmark/CLAUDE.md` — read before
  touching `hpc-benchmark/`.
- The build summary names every filesystem's mount point on two live
  surfaces: `make_pcluster.py`'s printed summary (`_storage_summary_lines`
  in `pcluster_core.py`) and `templates/sns_build_summary_report.j2`. A
  line added to one must be added to the other. (There was a third copy in
  `src/create_pcluster.yml`; that playbook is deleted.)
  `_storage_summary_lines` is keyword-only (14 same-typed
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
  Creates a sixth managed IAM policy (`HeadNode-Monitoring`) and an SSM
  Grafana password parameter — both must be deleted on teardown.
- The cluster's CloudWatch log group is **retained** on teardown —
  PCluster's `deletion_policy` default of `"Retain"`, deliberately not
  overridden: the group is the only surviving record of a failed build,
  which is always immediately followed by a teardown. Never add a
  log-group deletion step to teardown, never set
  `DeletionPolicy: Delete`, never put a retained log group in
  `_orphaned_resources`. Its *lifetime* is a decision, not an
  inheritance: `config.pcluster.j2` sets `RetentionInDays: 30` over
  PCluster's 180. That field is `UpdatePolicy.SUPPORTED` (unlike
  `Enabled`), and 30 must stay in `CloudWatchLogsSchema`'s `OneOf` set —
  read out of the installed package by
  `TestTheLogGroupExpiresOnOurScheduleNotPClusters`, never restated.
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
  Python for one: every AWS call is stubbed in tests, and a `NameError`
  inside a broad `except Exception` prints as a warning while the build
  reports success — both failed together once, on a live build, while
  `make lint` was running ansible-lint on two playbooks and no Python at
  all. `tests/test_undefined_names.py` gates it with pyflakes, and
  `make lint` now runs that same sweep. Scoped to that one class on
  purpose; do not widen it to general linting without deciding to clear
  the backlog.
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
- **Lambda's 900s ceiling is not the binding one; API Gateway's 29s
  integration timeout is.** No tool in `TOOL_TIERS` may block on a cluster
  operation: past 900s the function is killed mid-mutation, with the fleet
  stopped, an update in flight and the S3 lock held by a dead process.
  `apply_queue_config` is local-only for this reason; remote callers drive
  `stop_fleet` → `apply_cluster_update` → `start_fleet`. Decompose such a
  tool, never delete the capability. **But a remote call has ~29s, not
  900s** — `GATEWAY_INTEGRATION_TIMEOUT_MS`, already the REST maximum;
  raising it needs a service quota increase. Past it the caller times out
  while the Lambda runs on and the mutation *succeeds*, so a retry submits
  a second update against a stack already updating. Measured:
  `apply_cluster_update` took 41,992 ms adding one queue and the caller
  failed at 29.4s while the update completed.
- **`delete_cluster` initiates a teardown; `finalize_cluster_teardown`
  finishes it.** `wait=False` returns on CloudFormation's acceptance and
  skips *every* teardown step, leaving the IAM policies, the S3 bucket,
  the credentials, the SNS topic and the store record behind. A *second*
  `delete_cluster` does finish the job — an absent stack classifies as
  `_CLUSTER_NOT_FOUND`, not `_KICKED_OFF` — but it gets there by
  **issuing another delete-cluster against the name**, which deletes the
  new stack if that name has since been rebuilt, and called too early it
  silently no-ops and reports success, indistinguishable from having
  finished. `core_delete_cluster(finalize_only=True)` is the explicit
  second half: it never calls delete-cluster and never waits, refusing
  unless the stack is confirmed gone (`_confirm_stack_is_gone` — the wait
  loop at `retries=1`, so the non-blocking guarantee is structural, not a
  promise). `DELETE_FAILED` refuses too: arriving there means an earlier
  delete failed and the answer is to re-run it, not to strip IAM and S3
  the way the waiting path deliberately does. That path exits non-zero
  either way, so only asserting no teardown step ran can see it. A failed
  describe propagates: a failed AWS call is not a deleted stack. Both
  modes fall into **one** teardown body, and their two tokens — both
  minted by `preview_cluster_delete` — are deliberately not
  interchangeable.
- **The monitoring wrapper must not run upstream's installer on a login
  node.** `installer/install.sh`'s `case "${PLATFORM_NODE_TYPE}"` has arms
  for `HeadNode` and `ComputeFleet` only; a login node falls through
  `verify_docker`, matches nothing and fails, the wrapper exits with the
  installer's status, and the node's Auto Scaling Group replaces it —
  forever. The `LoginNode` arm exits 0 immediately: Grafana runs on the
  head node and operators reach it through `grafana_tunnel`, so there is
  no login-node half to install, and the bounded `MONITORING_HOME` poll
  that existed to let it proceed is retired. The harness stubs the
  installer, so the guard asserts on the **execution trace**, not the exit
  status. Incident: `templates/CLAUDE.local.md`.
- **Every `import pcluster.lib` must be followed by `ensure_event_loop()`.**
  PCluster's CDK layer calls `asyncio.get_event_loop()`, which returns a
  loop on the main thread and **raises** on any other; FastMCP dispatches
  sync tools on an AnyIO worker thread, so every MCP `create_cluster`
  failed with "There is no current event loop in thread 'AnyIO worker
  thread'". The CLI runs on the main thread and never hit it — a CLI build
  proves nothing about the MCP path. It fails *only* off the main thread,
  so the guard is pinned by AST at each site plus a real-thread test.
- **A pcluster.lib exception's `str()` is empty.**
  `ParallelClusterApiException.__init__` calls `super().__init__()` with
  no arguments, so formatting one with `{e}` yields a sentence with
  nothing in it — which is what hid the event-loop failure. The detail is
  on `.content`: a `message`, and `configuration_validation_errors` naming
  the validator. `pcluster_exception_detail` extracts both, skips INFO,
  and never returns an empty string — and **every `pcluster.lib` wrapper
  must use it**, not only the create paths. The three update/describe
  wrappers formatted with a bare `{e}` and reported `BadRequestException: `
  with nothing after the colon, which is what hid the R4 version skew.
- **`core_create_cluster` returns a `CreateClusterResult`; it must never
  `sys.exit`.** `SystemExit` is a `BaseException` and `create_cluster`
  cannot use `_cluster_lock`'s translation (it locks internally), so an
  exit — including the success one it used to take — kills the server. The
  CLI shim converts `exit_code`. The shared validation helpers still exit,
  so the wrapper keeps a narrow `except SystemExit` net as a backstop.
- **`finalize_cluster_build` is the create-side twin of
  `finalize_cluster_teardown`, and is reachable remotely.** `wait=False`
  returns before every step needing a live head node, so no summary is
  sent and no record published — and re-running the build refuses on the
  vars file it wrote. Gated on `CREATE_COMPLETE` (`_confirm_stack_is_built`,
  one describe, never waits), it reads context from the **rendered vars
  file**, not the build's in-memory state. `stage_dir` is the literal
  `/tmp`, never `tempfile.gettempdir()`.
- **The staging tree is pulled by the node, never pushed to it** —
  published to `s3://<s3_bucketname>/staging/` *before* the stack exists,
  fetched by `postinstall.j2` under the head-node gate, non-fatal, marker
  at `/opt/parallelcluster/shared/staging_tree_pulled`. That is what makes
  finalize remotable. Never restore the push, and never grant a tier
  `ssm:SendCommand` to do it: that is code execution on the head node from
  an internet-facing Lambda. Detail: `templates/CLAUDE.local.md`.
- **The vars file rides beside the record** at `vars/<name>.yml`, under the
  prefix `MCPStateAccess*` already grants — a new prefix is AccessDenied
  when deployed. Finalize reads **local first, store second**, like
  `_read_cluster_record`; storing is best-effort *inside* the best-effort
  publish.
- **No `aws s3 sync` subprocess on a path the container tier runs** — its
  image has no AWS CLI. Use `upload_directory_to_s3`, and keep the `*.pem`
  exclusion in the one shared `_S3_UPLOAD_NEVER`.
- **The CloudWatch-agent entry `postinstall.j2` appends must be a copy of
  one the agent already accepts**, changing only `file_path` and
  `log_stream_name`. Its schema validation is whole-config, so one unknown
  key crash-loops the agent and the node ships **nothing** — `from_beginning`
  did exactly that. **The contract rule is not just for botocore models**:
  read a third-party schema, never recall it. Detail:
  `templates/CLAUDE.local.md`.
- **A teardown finishes itself.** `delete_cluster` fires an
  `InvocationType="Event"` invoke at its own function, which polls and then
  runs `finalize_only` — an agent told to poll for 15-20 minutes has no
  timer and correctly stops, so no wording closes that gap. **The loop's
  bound lives in `mcp_server/completion.py`, which touches no AWS and no
  clock**, so it is swept exhaustively; three bounds, and an unreadable
  describe means keep waiting, never "it is gone". An Event invoke has no
  caller, so every terminal outcome must reach the retained log group and
  the cluster's SNS topic — silence would be indistinguishable from
  success. Verified live twice, most strongly on `joker` — a cluster that
  reached `CREATE_COMPLETE` and ran: `retry`×3 → `finalize success=True`,
  clearing bucket, 5 policies, 4 roles, SNS, secret, keypair and both
  store objects 31s after the stack vanished, unaided.
  **When the behavior changed, three instruction surfaces did not** — the
  preview's `next_step`, the `finalization_token` it minted for a call that
  had stopped taking one, and `delete_cluster`'s docstring, still saying
  "you must finish it". A tool's docstring is not documentation *about* the
  system, it is the input an agent decides on: as load-bearing as the
  return value, and it rots the same way. `next_step` and the docstring
  must agree, and the guard reads both. Bounds and reasoning:
  `mcp_server/completion.py`'s docstring.
- **A build failure the caller never sees must still be written down.**
  The message was never the defect — every `CreateClusterResult` carries
  one — but a 43.6s call against a 29s ceiling disconnects the caller
  before the return value exists, and no failure path recorded anything,
  so CloudWatch was the only trace and a plain AccessDenied cost two
  rounds. Every **post-lock** failure publishes via
  `_publish_build_failure`; pre-lock ones created nothing and return while
  the caller is still connected. Three rules: the key is
  `vars/<name>.build-failure.json`, since `MCPStateAccess*` grants only
  `vars/*` and `configs/*` and a new prefix is AccessDenied when deployed;
  it is **not** the cluster record, which would make `list_clusters` report
  a cluster that does not exist; and it never raises, the caller being
  already on an error path. `core_get_build_status` reads it back and keeps
  **three** outcomes distinct — an unreachable store reports
  `store_reachable: false`, never "no failure". Cleared on the next
  successful build and by teardown.
- **The build runs in an Event invocation; validation does not.** 43.6s
  against a 29s ceiling, ~39s of it CDK synthesis, so nothing of ours
  decomposes to fit — the tool validates (token, params, AZ via EC2),
  fires at its own function, returns. Measured 36,572 → 177 ms, with the
  build completing in a separate invocation 27.9s later (`joker`).
  `build_started` is a returned fact, false locally where the build runs
  inline. **`core_create_cluster` is deliberately not split** at the lock,
  the correct seam: 1,851 lines is more risk than the outcome needs, so
  pre-lock validation now fails where nobody waits and `build_runner`
  records it, reading first since a post-lock failure has already named
  its stage. **`MaximumRetryAttempts` must be 0** — AWS retries a failed
  Event invoke twice and a retried build launches a second cluster; the
  lock is the inner guard. **A tier must be granted invoke on *itself***:
  the grant named `-stack-mutation` while the build tier is
  `-stack-mutation-node`, so every remote build fell back to inline,
  visible only as one log line since the fallback is silent. The
  vars-file guard still asks whether the cluster is **already building**
  before treating a leftover file as a dead run.
- **Access must not depend on the build having finished.**
  `access_cluster.py` and `grafana_tunnel.py` render their generated
  script on demand via `core_ensure_generated_script` when one is absent —
  it is a pure function of the vars file. Rendering, never
  reimplementing — the template carries the SSM ProxyCommand, the
  plugin-absent fallback and the rc/stderr diagnosis. An existing script is
  never overwritten. With no vars file the error says this machine did not
  build the cluster; it must never say "make sure the cluster was built
  with make_pcluster.py", which blamed the operator for a supported MCP
  build and offered a rebuild.
- **Cognito access tokens carry no `aud` claim** — they carry `client_id`;
  only ID tokens have `aud`. `mcp_server/auth/authorizer_lambda.py`
  validates `client_id` and pins `token_use == "access"` so an ID token
  cannot authorize a tool call. Never add an `aud` fallback, and never
  return a Deny policy (403, which does not prompt re-auth).
- **The 401 needs a REST API *and* the exact message `Unauthorized`** —
  measured: REST maps the bare word to 401 and a sentence to 500; HTTP gives
  500 for either. The authorizer logs the reason and re-raises the bare
  word. An *unexpected* exception is not converted: a bug there is a server
  fault, and dressing it as 401 loops a valid client.
- The remote transport is a router plus four handler Lambdas split by IAM
  blast radius, one policy per tier in `templates/MCP*.json_src` — a third
  policy category, neither instance-reachable nor the operator's own. The
  router must import no third-party package.
- **A tier's policy has a floor as well as a ceiling.** Every MCP IAM
  guard asked whether a tier could exceed its blast radius; none asked
  whether it could reach its own, and two tiers could not —
  `fleet-toggle` had no S3/DynamoDB grant for `update-compute-fleet`, and
  **no tier granted any `elasticloadbalancing` action**, so every remote
  tier failed `describe-cluster` against any `--enable_loginnode`
  cluster. `TestEachTierCanActuallyDoItsJob` pins both directions for
  both gaps. `MCPStackMutation.json_src` is now 5,935 bytes of the 6,144
  limit. Which grants, and why each stays narrow: `templates/CLAUDE.md`.
- **Reuse is not convergence, and the difference is a naming asymmetry.**
  `_setup_iam`'s policies carry the cluster serial, so every build makes
  fresh ones and a stale document is unreachable; the MCP policy names are
  fixed and long-lived, so the first-ever create won forever — a
  `templates/MCP*.json_src` edit changed nothing in the account and printed
  "Reusing existing MCP policy". `_setup_mcp_infra` compares the rendered
  document against **AWS's own copy** (never a stored hash, tag or git ref —
  a marker beside the truth is a second source that can be wrong about it);
  `--update-policies` pushes it as a new default version, pruning
  oldest-first under IAM's five-version ceiling, never the version in force.
- **`deploy_mcp.py --teardown` removes the transport; nothing did before.**
  `delete_mcp_functions` and `_delete_mcp_infra` were called by no entry
  point, and the REST API and Cognito pool had no teardown code at all, so
  the internet-facing endpoint outlived every teardown. Order is gateway,
  functions, IAM, pool. **The Cognito domain must be deleted before the
  pool**: `DeleteUserPool` fails while one exists, names no domain in the
  error, and the domain string is not the pool name — read it off
  `describe_user_pool`, never guess. Teardown deliberately leaves the
  permissions boundary; `MCPDeployPolicy` denies deleting it. `--dry-run`
  lists without removing.
- **The deploy builds and pushes the container tier itself**
  (`--tier stack-mutation-node`, no `--image-uri`), and `--teardown`
  deletes the repository with **`force=True`** — one we created always
  holds an image. Create and delete land together; creating without
  deleting leaks one per account. Only `ecr:GetAuthorizationToken` may
  keep `Resource: "*"` (it acts on the registry, not a repository); every
  other ECR action is confined to `repository/pclustermaker-mcp-*`. The
  registry URI is **read off `repositoryUri`**, never assembled — the
  suffix differs in GovCloud and China. Password on **stdin**, never argv.
  Finch's credential failure is named, never auto-applied. Detail:
  `docs/sessions.md`, session 56.
- **`--bootstrap` must be checked *before* the setup-flag short-circuit.**
  An infrastructure flag alone deploys no tiers on purpose, but
  `--bootstrap` normalizes to `--setup-infra --setup-gateway`, so checked
  second it builds a gateway routing to functions that do not exist. It is
  a *spelling* of those flags, never a second deploy path;
  `normalize_bootstrap` is module-level for the reason `tiers_to_deploy`
  is. It excludes `stack-mutation-node` (needs a runtime; six of seven are
  zips). `--create-user` is the other half — nothing created a Cognito
  user, so a deployed transport had nobody to sign in as. Both
  `MessageAction="SUPPRESS"` and `Permanent=True` are load-bearing, and
  the second *because* of the first (suppressing the invitation without a
  permanent password leaves the account in `FORCE_CHANGE_PASSWORD`).
  Detail: `docs/sessions.md`, session 56.
- **MCP deployment is its own permission set, not the operator's.**
  `OperatorPolicy` scopes its IAM to `pclustermaker-policy-*` and
  `pclustermaker-role-*`, which match no MCP name, and the full MCP grant
  set appended to it measures 6,358 bytes against the 6,144 limit — so
  widening it does not merely read badly, it does not fit.
  `MCPDeployPolicy.json_src` carries it — rendered by
  `generate_operator_policy.py --mcp` as
  `parallelcluster-mcp-deploy-pclustermaker`, a name kept outside
  `_MCP_POLICY_NAME_PREFIX` because the deployer's own policy must survive
  `--teardown`. Every MCP role is created
  under the `MCPRoleBoundary.json_src` permissions boundary, with
  `iam:CreateRole` granted **only** under a `StringEquals` condition on
  `iam:PermissionsBoundary` — without it a deployer creates an unbounded
  role and the ceiling never applies. **`deploy_mcp.py` cannot create or
  attach that policy and must not try** — a deploy tool able to grant
  itself permissions has no ceiling. It preflights instead, naming missing
  actions before the first mutation: probes are **unconditional grants
  only** (a conditional one simulated without its context key reports
  `implicitDeny` + `MissingContextValues`, which is not a denial), and an
  unanswerable check warns rather than blocks, since
  `iam:SimulatePrincipalPolicy` is itself ungranted. Details in
  `templates/CLAUDE.md`.
- **Adding policy versions breaks teardown unless teardown prunes them.**
  `DeletePolicy` refuses while any non-default version exists
  (`DeleteConflictException`), so the two halves must land together:
  `_delete_policy_with_versions` deletes non-default versions first. It
  was unreachable until deploy could version a policy, and was reached the
  same day — three MCP policies left undeletable in the account.
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
  first and only falls back to the store. **Teardown follows the same
  order**, via `_cluster_record_from_store` — one read for both the serial
  and the vars, since both files exist only on the building machine. The
  record therefore projects teardown's own 11 inputs, defaulted so an older
  record still loads, and a record missing them is **refused** rather than
  run against blank names. The `vars/` prefix is fixed by
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
- **One server manages one region, by design, and the IAM matches.** The
  record store is per account+region, so a server answers only for its own
  region — cross-region discovery would mean scanning every region's
  bucket on every listing — and each tier's `MCPStateAccess*` policy names
  exactly one bucket, so multi-region means one topology per region, never
  wider IAM. On a Lambda the mismatch is unreachable (no local files, so a
  record can only come from that region's own store), but `_require_record`
  still refuses a *stored* record whose `region` disagrees with the bucket
  it came out of, naming both — acting on it sends every later store call
  to a bucket the record was not in, which under one-region IAM surfaces as
  an opaque `AccessDenied`. Scoped to the store branch: a *local* vars file
  may name any region, which is why it is read first. The not-found
  message names the limit too — "not tracked here" otherwise reads as
  "does not exist".
- **`MakeClusterParams` carries no `region`** — the CLI resolves it from
  the AZ-verification call and passes it to `core_create_cluster`
  separately, so every shim must too. MCP's `create_cluster` read
  `params.region` and raised `AttributeError` on every call; the one test
  reaching it asserted an error and got one from the earlier denied-param
  check. `resolve_region_from_az` (`pcluster_core.py`) is the shared
  resolution: it asks EC2 rather than trimming `az[:-1]`, which also proves
  the AZ exists.
- **A read-only deployment and a least-privilege role break three
  assumptions a checkout never tests.** `repo_root` is both the source root
  and the state root, so `resolve_writable_repo_root` overlays a read-only
  one under `/tmp` — detection is a write probe, never `os.access(W_OK)`.
  `_create_locks_bucket` returns early on `head_bucket`; no handler tier
  may be granted `s3:CreateBucket`. `MCPClusterBuild.json_src` carries the
  build grants `MCPStackMutation` had no room for, on both tiers.
- **The PCluster version is pinned **exactly** at every end** —
  `PCLUSTER_REQUIREMENT`, `requirements.txt` and the generated
  `requirements-lambda.txt`. A version skew between an artifact and the
  operator's venv builds clusters neither can manage. **A bounded range is
  not a pin and was the fix that failed**: `>=3.15,<3.17` is one string on
  every surface, so an agreement test passes, while pip resolves it to
  3.16.0 for an artifact built today against a venv holding 3.15.1 —
  identical specifiers resolved at different times are not the same
  version. Every remote tool then fails with "the update can be performed
  only with the same ParallelCluster version". `test_the_pin_is_exact`
  requires the operator set to be exactly `{"=="}`.
- **A Lambda artifact must be pruned of bytecode.** `pip install --target`
  of the read-only tier is 241 MB against the 250 MB unzipped limit;
  removing `__pycache__`/`.pyc` takes it to 139 MB. `prune_for_lambda`
  returns the total so a build checks before uploading. The 55 MB zip
  exceeds the 50 MB direct-upload limit, so handler tiers go via S3.
  `aws-parallelcluster` *declares* 17 `aws-cdk.*` packages — the lazy
  import does not keep them out of the artifact.
- **`requirements.txt` is the development set and must never be installed
  into a Lambda artifact** — `ansible` alone is ~408 MB of collections,
  and nothing on any tier imports it. `mcp_server/packaging.py` holds the
  per-tier sets and generates `requirements-lambda.txt`.
- **Slurm is not on a non-interactive PATH, and a remote command is
  re-parsed by the remote shell.** `ssh host sinfo` gets the bare system
  PATH — `/opt/slurm/bin` is appended only by a login shell — so
  `check_slurm` and two of `diagnose`'s four probes returned rc=127
  against every real cluster. `_slurm_remote_cmd` prepends the directory
  and `shlex.quote`s the whole script, because `_ssh_args` does no quoting
  and ssh joins argv with spaces: `["sinfo","-o","%D %T"]` arrived as five
  words. Not `bash -lc` — a login shell sources `/etc/profile.d`, and a
  banner from any fragment lands in the text `_classify_sinfo_nodes`
  parses, where an unreadable line counts as an unusable node.
- **`access_cluster.j2` and `grafana_tunnel.j2` route ssh through SSM**
  (`-o ProxyCommand` + `AWS-StartSSHSession`), falling back to direct ssh
  with a warning when `session-manager-plugin` is absent. Still ssh, not
  `aws ssm start-session`: that lands as `ssm-user`, whose `$HOME`, PATH
  and Slurm environment are not the ones a login node exists to provide,
  and loses scp/rsync/agent-forwarding/`-L`. The tunnel's `pgrep` must
  match the *target actually used* — it matched the IP, and over SSM the
  command line carries the instance ID, so `stop` reported success and
  left the tunnel running.
- **An MCP tool's return annotation is enforced.** FastMCP validates
  structured content against it, so `list_queues` annotated `-> dict` while
  returning a list failed *every* call — with the correct payload inside
  the error text. Tests that call a wrapper directly cannot see this; only
  a real client session can.
- **A fake is written from the API's contract, never from memory of it.**
  For AWS that means `botocore/data/<service>/*/service-2.json` in `.venv`,
  which is authoritative and local. A fake built from recall encodes what
  the caller happens to need and therefore agrees with the code by
  construction — including where the code is wrong. The `_S3` fake hid
  three defects that way: it discarded `Bucket`, its `put_object` returned
  no `ETag` (silently disabling the config store's direction detection),
  and it always returned `Contents` with `IsTruncated: False`, so the
  hand-rolled pagination loop had never run a second iteration. Modelling
  it faithfully also closed a real gap: `PutObject` with `IfMatch` against
  an absent key returns **404**, not 412 (verified live), so that case
  escaped `_is_conditional_write_rejection` — it is handled separately,
  never by widening the predicate the cluster lock shares.
- **When a test stubs the object under test, at least one test must drive
  the real one.** `handlers/base.py` called a FastMCP method that does not
  exist; every stub defined the same wrong name, so the whole remote
  `tools/call` path was broken and every test passed.
- `config.pcluster.j2`'s `OnNodeConfigured` block is a shared Jinja2 macro
  called at all four `CustomActions` sites (`HeadNode`, `LoginNodes`, both
  `SlurmQueues`), not four independent copies — that duplication is what
  let `LoginNodes` silently inherit `HeadNode`'s `OnNodeStart` by
  copy-paste in the first place.
- **Slurm accounting (`--enable_slurm_accounting`, default true) is a
  local MariaDB on the head node, and every step is non-fatal** — once
  `slurm.conf` names an accounting host, `slurmctld` blocks forever when
  it does not answer, so any failure sets `_acct_ok=0` and leaves
  `slurm.conf` untouched. Three halves shipped wrong once each:
  **MariaDB is `restart`ed, never `enable --now`n** — the package's own
  postinst already started it, so a config written afterward is never
  read, and the effective value is read back off the server;
  **`SlurmUser` comes from `slurm.conf`'s `SlurmUser=` line**, never
  `stat -c %U`, which returns root and built a green cluster with a dead
  scheduler; and **`slurm.conf` is edited by a deferred unit** after
  `clustermgtd`'s heartbeat, rolling back unless `slurmctld` returns
  *answering*, not merely active. Also load-bearing: the `loose-` prefix
  on `innodb_snapshot_isolation`, never `sacctmgr add cluster`, and a
  purge policy. **Default-on is safe only because of that latch**; the
  two defaults surfaces must agree. Detail: `templates/CLAUDE.local.md`.
- **A warning a node script writes to stderr reaches nobody.** A script's
  own stderr reaches no CloudWatch stream, so `|| echo "WARNING: ..." >&2`
  is invisible. Before/after on one guard: `acctproof4` wrote it to stderr
  and it is **absent** while an `echo` from the same script is present;
  `acctproof5`, on stdout, has it. Scope it that way — dpkg relays
  `depmod`'s warnings, so "stderr is never captured" is too strong. Only a
  systemd unit keeps `>&2`, the journal capturing it. Pinned by
  `TestNoNodeScriptWarningGoesToStderr`. **A clean build cannot verify
  this**: no guard fires, so evidence needs a failed build or a retained
  log group.

## Environment

- `.venv/` is excluded from git; all dependencies are in `requirements.txt`.
- **Python 3.12 only.** `aws-parallelcluster` does not support 3.13/3.14.
  Always create `.venv` with `python3.12 -m venv .venv`.
- **Nothing executes an Ansible playbook, and `ansible` is still required.**
  `src/create_pcluster.yml`/`delete_pcluster.yml` are deleted; every task
  they held is a function in `pcluster_core.py`. The dependency stays for
  two live reasons: `_template_env` renders every template with
  `ansible.builtin.template`'s own `trim_blocks`/`lstrip_blocks` defaults,
  read out of the installed package by
  `TestTheTestEnvironmentMatchesAnsible`, and `make_pcluster.py` aborts a
  build when `ansible --version` does not run. Removing either is what has
  to come first.
- Venv guard uses `sys.prefix` (not `sys.executable`) — Homebrew Python
  symlinks resolve outside `.venv/`.
- Shebangs use `#!/usr/bin/env python`, not `python3` — `env python3` on
  macOS bypasses the active venv.
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
- **No test may reach AWS**, enforced by `_no_test_reaches_aws` in
  `tests/conftest.py` — patched at botocore's HTTP layer, since a test may
  construct a client but not put a request on the wire (`@pytest.mark.allow_aws`
  opts out). An unstubbed call is invisible where it is written: it passes
  wherever there are credentials and fails in CI, far from its cause.
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
  error strings ("personalize", "normalize", "analog", "behavior",
  "honor", "signaling", "neighboring", "defense", "unlabeled"). Exception: never "correct" a spelling that is part of an
  external contract — Slurm's `CANCELLED` job state is passed verbatim to
  `sacct --state=` in `diagnose_pcluster.py` and
  `tests/integration/run_integration_test.sh`, and Americanizing it breaks
  the query.
