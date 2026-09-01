# Claude Instructions — ParallelClusterMaker MCP server

Co-authored-by: Rodney Marable <rodney.marable@gmail.com>
Co-authored-by: Claude Code <noreply@anthropic.com>

Constraints that bind **only** files under `mcp_server/`, so they load when you
work here rather than in every session.

The scoping test is the file the rule constrains, not the subject it is about.
`deploy_mcp.py` and `generate_operator_policy.py` are at the repo **root**,
`src/pcluster_core.py` is under `src/`, and the tier policies are in
`templates/` — a nested memory file does not load for any of those, so every
MCP rule touching them lives in the root `CLAUDE.md` even though it is
"about MCP". An earlier split filed thirteen such rules here and they were
pulled back on 2026-09-01: a prohibition that does not load when it binds is
not a prohibition.

Evidence behind these rules is in `mcp_server/CLAUDE.local.md`, which loads
alongside this file.

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
- **`requirements.txt` is the development set and must never be installed
  into a Lambda artifact** — `ansible` alone is ~408 MB of collections,
  and nothing on any tier imports it. `mcp_server/packaging.py` holds the
  per-tier sets and generates `requirements-lambda.txt`.
- **An MCP tool's return annotation is enforced.** FastMCP validates
  structured content against it, so `list_queues` annotated `-> dict` while
  returning a list failed *every* call — with the correct payload inside
  the error text. Tests that call a wrapper directly cannot see this; only
  a real client session can.
