# Claude Instructions — ParallelClusterMaker MCP server

Co-authored-by: Rodney Marable <rodney.marable@gmail.com>
Co-authored-by: Claude Code <noreply@anthropic.com>

Constraints scoped to the remote transport — `mcp_server/`, `deploy_mcp.py`,
and `templates/MCP*.json_src`. Split out of the root `CLAUDE.md` so they load
when you work here rather than in every session. Cross-cutting MCP rules that
also bind `pcluster_core.py`, the CLI, or the node templates stayed in the root
file: the `ensure_event_loop()` rule, the staging tree, the vars file beside the
record, on-demand access-script rendering, the cluster record store,
`MakeClusterParams` carrying no `region`, and the defaults file.

## Constraints

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
- **No `aws s3 sync` subprocess on a path the container tier runs** — its
  image has no AWS CLI. Use `upload_directory_to_s3`, and keep the `*.pem`
  exclusion in the one shared `_S3_UPLOAD_NEVER`.
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
  both gaps. `MCPStackMutation.json_src` is now 5,979 bytes of the 6,144
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
- **A read-only deployment and a least-privilege role break three
  assumptions a checkout never tests.** `repo_root` is both the source root
  and the state root, so `resolve_writable_repo_root` overlays a read-only
  one under `/tmp` — detection is a write probe, never `os.access(W_OK)`.
  `_create_locks_bucket` returns early on `head_bucket`; no handler tier
  may be granted `s3:CreateBucket`. `MCPClusterBuild.json_src` carries the
  build grants `MCPStackMutation` had no room for, on both tiers.
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
- **An MCP tool's return annotation is enforced.** FastMCP validates
  structured content against it, so `list_queues` annotated `-> dict` while
  returning a list failed *every* call — with the correct payload inside
  the error text. Tests that call a wrapper directly cannot see this; only
  a real client session can.
- **When a test stubs the object under test, at least one test must drive
  the real one.** `handlers/base.py` called a FastMCP method that does not
  exist; every stub defined the same wrong name, so the whole remote
  `tools/call` path was broken and every test passed.
