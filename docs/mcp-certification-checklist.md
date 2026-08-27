# MCP Certification Checklist

Generated 2026-08-24 by an agent surveying `mcp_server/tools.py`,
`mcp_server/tiers.py`, `CLAUDE.md` and `docs/records-store-plan.md`, then
reconciled by hand against the tree as it stands.

**Read this first.** The body was written against an earlier tree. Every
finding it raised has since been acted on, and four behaviors changed
under it. The nine notes below are current as of 2026-08-24; the
**"What is true today"** block that follows them, and every dated status
block inside a phase, carry the later corrections. Where they disagree,
the later date wins.

1. **The `SystemExit` defect (item 0.5) is fixed.** `_require_record`
   called `_validate_cluster_name` unwrapped; that function `sys.exit()`s,
   `SystemExit` is a `BaseException`, and it unwound the server process
   rather than failing one call -- with 16 of the 20 tools routing through
   it. Pinned by `TestAnInvalidClusterNameDoesNotKillTheServer`, which
   asserts on a *second* call surviving. Run 0.5 as a regression check.
2. **`add_queue`/`remove_queue` are on the `stack-mutation` tier**, not
   `read-only`, and `MCPStateAccessReadOnly` no longer grants
   `s3:PutObject` on `configs/*`. Any tier reasoning in the body predates
   that.
3. **The CLI mirrors queue edits now.** `manage_pcluster_queue.py`
   resolves the store from the cluster's own record and passes it through;
   an unreachable store degrades to a local-only edit with a warning
   rather than failing. Certified by new items 2.11 and 2.12; the section
   at the end of this file records it as resolved.
4. **Divergence direction is decided by a mirror marker**, not by
   comparing content -- content cannot tell "ahead" from "behind", and the
   first version of that check reported both as "behind". Items 2.8 and
   2.11 cover both directions.
5. **A deleted stored config is now a `ClusterConfigConflict`**, not a raw
   `ClientError`. `PutObject` with `IfMatch` against an absent key returns
   404, not 412 -- verified against real S3, not inferred. New item 2.10.
6. **Item 1.3 found a real defect, and it was not the one the item warned
   about.** `check_cluster_health` reported an entirely healthy cluster
   UNHEALTHY with `Slurm fail — sinfo returned rc=127: sinfo: command not
   found`. A non-interactive `ssh <host> sinfo` gets the bare system `PATH`;
   `/opt/slurm/bin` is appended by the login shell only, which a command-argv
   ssh never runs. The check had **never** worked against a real cluster.
   Fixed and re-verified live. 1.3 is rewritten below.
7. **`list_queues` was broken over MCP entirely.** The wrapper was annotated
   `-> dict` while returning a list; FastMCP validates structured content
   against the annotation, so every call failed with `structured_content must
   be a dict or None` *while carrying the correct payload inside the error
   text*. Now `-> list[dict]`. 1.4 carries the false-pass note.
8. **`diagnose_cluster` carried the same `PATH` defect** in 2 of its 4 probe
   types (`sinfo -N -l` and `sacct`); `tail` and `test -f` were unaffected
   because they are on the bare `PATH`. Fixed and verified live. See 1.6.
9. **ssh and the Grafana tunnel route through Session Manager now.**
   `access_cluster.j2` and `grafana_tunnel.j2` set `-o ProxyCommand` over
   `AWS-StartSSHSession`, falling back to direct ssh with a warning when
   `session-manager-plugin` is absent. New items 1.12, 1.13 and 1.14 — 1.14
   covers a real bug this transport introduced.

Item numbering is unchanged so earlier notes still line up; new items are
appended within their phase.

---

**What is true today (2026-08-26).** All 14 certification tasks are
complete and **nothing is left standing**: cluster `stageb` (with the
`r4probe` queue R4 added), the whole remote transport, the ECR repository,
all 14 CloudWatch log groups and every object in the locks bucket are gone.
The AWS account holds no ParallelClusterMaker resources; two buckets remain
by design. So every item below that reads as a live prerequisite — "the
cluster is up", "keep it alive", "still running" — describes a state that
no longer exists, and re-running any **CLUSTER** item means building one
again.

**Both transports have been exercised.** The **local stdio server**
(`build_local()`, full 20-tool set) is what Phases 0-4 were run against; on
it `remote=False`, so `_LOCAL_ONLY` excludes nothing and
`ssh_available=True`. 20 wrappers are registered; `TOOL_TIERS` names 16 —
the difference is exactly `_LOCAL_ONLY`, which holds four tools
(`apply_queue_config`, `finalize_cluster_build`, `manage_grafana_tunnel`,
`rotate_cluster_key`). The **remote** transport was deployed in full
(sessions 53 and 54), driven from a REST gateway with real Cognito tokens,
and then removed with `deploy_mcp.py --teardown`. Statements below that say
"no deployed handler was involved" are true of the **2026-08-24** run they
sit in, and false of the tree today.

**Several real clusters have been driven**, and two of them carry most of
the verdicts here. On 2026-08-24 `osiris` was built in `us-east-1` —
`--enable_loginnode true --use_defaults=osiris_defaults.yml`,
`CREATE_COMPLETE` in 17 minutes, head node plus one login node (both Graviton
`c8g.large`), an 8-node spot compute queue sitting `idle~` — **from the CLI**,
which is why 1.0 was still open after it. On 2026-08-25 `stageb` was built
**through the local MCP `create_cluster`** (task L1), which closed 1.0, and
carried Stages B and C. Between them sit `certify` (rebuilt more than once,
which is why one note says monitoring was on and another says off) and
`nodecert`, built through a deployed handler.

**Two limitations are recorded and still stand** — they are at the end of
this file and are easy to lose behind the completed tasks:

* **The CLI IAM hardening is not live-verified.** A wrong deny or a narrow
  ceiling surfaces at node bootstrap, roughly 20 minutes into a build, and
  the whole test suite is blind to it.
* **`deploy_mcp.py --teardown` has been exercised only against an empty
  account.** The delete paths have never run against live resources through
  that code.

| Marker | Meaning |
|---|---|
| **FREE** | No AWS mutation, no cluster. Some make read-only AWS calls. |
| **CLUSTER** | Needs a real running cluster (20–45 min to build, 15–20 to tear down). |
| **$$$** | Creates or destroys billable resources. |
| **SHELL** | Cannot be done from the MCP surface alone. |

**Preconditions:** local stdio server in the repo checkout; credentials with
`OperatorPolicy`; Node.js on `PATH`; `.venv` present. Note `osiris_defaults.yml`
exists at the repo root and **will be auto-applied to any cluster named
`osiris`**. Anything marked **CLUSTER** additionally needs a cluster built
first — there is none now. Anything on the remote transport needs it
redeployed first (`deploy_mcp.py --setup-infra --setup-gateway`, then the
tiers); it is torn down.

---

## Phase 0 — read-only, no cluster required

**Status 2026-08-25: ALL 18 PASSED**, against a real stdio session, with
`core_create_cluster` never reached.

**How 0.13-0.15 were run without risking a build.** Those three drive
`create_cluster` with tokens that must be refused, and a gate failure
starts a real 20-45 minute build — there is no dry-run behind it. They
were run against the shipped server with **one** attribute replaced,
`mcp_server.tools.core_create_cluster`, by a recorder that writes a
`BREACH` file and raises. Python resolves module globals at call time, so
this makes a bypass observable and free rather than expensive, and it is a
*stronger* check than the checklist originally described: "no build
started" is now an assertion rather than a hope. Nothing else in Phase 0
touches that attribute, so every other item ran unmodified. Harness:
`scratchpad/guarded_server.py`.

**Two corrections this run produced, both to the checklist rather than the
code:**

- **0.13/0.14 must assert the message wording, not the exception class.**
  FastMCP surfaces only the message text, so `"MalformedToken" in error`
  fails against a perfectly working gate. That is a false *fail*, and
  chasing it would invite "fixing" the code. Assert what
  `confirmation_token.py` actually writes — `<version>.<issued_at>.<digest>
  form`, `different build`, `expired ... TTL ... re-run the preview`,
  `does not match` — which is also the checklist's real requirement: a
  token failure must never read as a parameter problem.
- **0.15's edit must change a value, not a comment.**
  `_defaults_fingerprint` hashes `load_cluster_defaults`' *parsed
  contents*, not the file's bytes, so appending a comment legitimately
  leaves the token valid — a comment cannot change the build. Appending one
  and expecting `TokenMismatch` reports a defect that does not exist (it
  did here, and the harness is what proved the token was genuinely still
  live). Change `max_cpu_queue_size` or `base_os` instead, and revert.

**0.16 is covered without 15 minutes of wall clock.** Expiry enforcement
and its wording are proved by 0.13's third case, a synthetically aged
token, which exercises the same branch. Reusability was confirmed directly
against `mint`/`verify`: the same token verifies twice, so tokens are
**not single-use** within the window. Nothing claims they are; it is
recorded here because a reader may assume it.

**0.1's shell confirmation was run**, and matters: `[]` is also what
missing credentials or an unresolved region produce. `sts get-caller-identity`
resolved `arn:aws:iam::183295445014:user/www-admin` and `aws s3 ls` of the
`vars/` prefix returned zero objects — a genuinely empty store, not a
swallowed failure.


**0.1 Empty-store listing degrades cleanly — FREE.** `list_clusters()` → `[]`.
Claim: *"a store that cannot be reached contributes nothing and is not an
error."* **False pass:** `[]` is also what missing credentials, an unresolved
region, or an STS failure produce — all swallowed by `_record_store`'s bare
`except`. Confirm with `aws sts get-caller-identity` and a direct `aws s3 ls`
of the `vars/` prefix (**SHELL**). Objects in the bucket plus `[]` here is a
real failure reported as an empty store.

**0.2 Live listing on an empty store — FREE.** `list_clusters(live=True)` → `[]`.
Only proves `live=True` doesn't crash on zero records; proves nothing about the
live path. That is 1.2.

**0.3 Cost report with no clusters — FREE.** `get_cost_report(days=1)`. A
`ce:GetCostAndUsage` denial is *reported as a string*, not raised. Read it;
"no error" is not "Cost Explorer works".

**0.4 Untracked cluster names the region — FREE.**
`check_cluster_health(cluster_name="no-such-cluster")`. Expect the "not tracked
here" message with a **concrete region**. **False pass:** the literal
`an unresolved region` means `_store_region()` resolved nothing and the store
was never consulted — the tool answered without looking.

**0.5 An invalid name must not kill the server — FREE. (Was a real defect; now
fixed — run as a regression check.)** `check_cluster_health(cluster_name="Bad_Name")`,
then **any second call**. The second call is the assertion: `SystemExit` can
surface as an error result while the transport is already unwinding.

**0.6 A cluster with no compute queue is refused — FREE.** `preview_cluster_config`
with no `overrides` and no matching defaults file. **Check for the absence of
`confirmation_token`**, not just an error string — a token here means the gate
is bypassable and a later `create_cluster` would provision IAM, S3, a keypair
and a secret before failing.

**0.7 `<cluster>_defaults.yml` is auto-applied — FREE.** `preview_cluster_config`
for `osiris`. Expect `defaults_file == "osiris_defaults.yml"`,
`resolved_config.base_os == "ubuntu2404arm"`, `delete_s3_bucketname` **absent**
from `defaults_file_settings`. **Two false passes:** (a) `defaults_file` being
non-null only proves the file was *found* — check `resolved_config` carries its
values; (b) `notable_defaults` must **not** contain `base_os`, which is the
regression that shipped once.

**0.8 Boolean flags are actually OFF — FREE.** Same call; inspect
`resolved_config`. Every `enable_*`, `debug_mode`, `stage_docker_compose` must
be the JSON literal **`false`**, not the string `"false"`. **This is the
important false pass:** `"enable_fsx": "false"` reads correct to a human and
*is* the bug — the string is truthy, and the build turns the feature on.
Distinguish the quotes.

**0.9 A real bool in `overrides` is rejected — FREE.**
`overrides={"enable_fsx": true}` → error naming the string form. Also
`{"loginnode_count": true}` → `(expected int)`. Acceptance means the type check
was loosened to `isinstance` and `True` passed as 1.

**0.10 An unknown override key is rejected — FREE.** `{"enable_fxs": "true"}`.
**False pass:** a successful preview echoing `enable_fxs` in
`non_default_settings` — that echo is the raw input and will faithfully display
a key that was never applied.

**0.11 The three CLI-only parameters are refused — FREE.** `custom_ami`,
`pre_install_script`, `post_install_script` on `preview_cluster_config`. Expect
the parameter, the reason, and a concrete `make_pcluster.py` command.
**Note:** the execute-side check is unreachable from MCP (preview denies first,
and `create_cluster` verifies the token before the denylist). Certify the
preview side; record the other as defense-in-depth only.

**0.12 The AZ is verified before a token is minted — FREE.** `az="us-east-1z"`
→ `AvailabilityZoneNotFound`, **no token**. A token with `region: "us-east-1"`
means the region was trimmed off the string rather than resolved. Also try
`az="us-east-1"` — expect the "pass an AZ, not a region" message, not a raw
botocore error.

**0.13 The token gate cannot be walked around — FREE.** Three `create_cluster`
calls: garbage token → `MalformedToken`; `v2.…` → `MalformedToken` naming the
version; `v1.1.<64 hex>` → `ExpiredToken` naming the age. Verify the wording
matches `confirmation_token.py` — a token failure must never be phrased as a
parameter problem.

**0.14 A token cannot authorize different parameters — FREE.** Preview, then
`create_cluster` with one field changed → `TokenMismatch`. Repeat per bound
field: `cluster_owner_email` alone, `overrides` alone, and
`max_cpu_queue_size: 10` vs `"10"` (types are preserved in the hash).
**Any of these succeeding starts a $$$ 20–45 minute build** — there is no
dry-run behind `create_cluster`.

**0.15 The token covers the defaults file — FREE (edits your file; revert).**
Preview `osiris`, **SHELL:** edit `osiris_defaults.yml`, then `create_cluster`
with the old token → `TokenMismatch`. If the build starts, tear it down
immediately: the fingerprint is not covering the file.

**0.16 Token TTL — FREE (15 min of wall clock).** `preview_cluster_delete`,
wait 15 minutes, `delete_cluster` → `ExpiredToken`. **Record, don't certify:**
tokens are **not single-use** — within the window one authorizes the same
operation repeatedly. Nothing claims otherwise, but a reader may assume it.

**0.17 `preview_cluster_delete` on an untracked cluster mints nothing — FREE.**
Expect the "not tracked" error, no `will_delete`, no token. **False pass:** a
`will_delete` list built from placeholders while still returning a token.

**0.18 `list_queues` with no config anywhere — FREE.** Expect the "not tracked"
error, **not** `ERROR: cluster config not found:` — the latter means
`_require_record` answered for a cluster it shouldn't have.

---

## Phase 1 — read-only against a cluster that exists

All **CLUSTER**. Only 1.0 is **$$$**. Build via **MCP**, not the CLI: a CLI
build certifies nothing about the MCP create path or the `_KICKED_OFF`
publish. That was the specifically-unverified claim this phase existed to
close, and 1.0 closed it on 2026-08-25; the instruction stands for any
re-run.

**Status, 2026-08-25** (cluster `certify`, built through
`core_create_cluster(wait=False)` -- the MCP path -- in us-east-1 while the
ambient region was us-east-2):

- **PASS: 1.1, 1.2, 1.3, 1.5, 1.6, 1.7, 1.8, 1.10, 1.11.** All six health
  checks pass including **Slurm** and **postinstall complete**, which
  re-confirms the `_slurm_remote_cmd` PATH fix on a cluster built from
  scratch. **1.10 is the one that mattered**: with the vars file and
  `active_clusters/` moved aside, health, listing and queues all resolved
  from S3. Nothing had ever exercised that path. **1.11** passed in the
  same state: invisible from a server in the wrong region, degrading to a
  refusal that names the region rather than a traceback.
- **1.0 FAILED, was fixed, and PASSED on the re-run.** See the item below:
  `core_create_cluster` sys.exit()ed on every path including success,
  killing the server. It now returns a `CreateClusterResult`. The
  `_KICKED_OFF` publish half was already verified on `certify` (both
  objects landed, in the cluster's region); the kick-off half was certified
  on **2026-08-25 by task L1**, where `create_cluster` through the local
  stdio server returned `success=true, exit_code=0, kicked_off=true` for
  `stageb` and published both objects. It has also since run on a
  **deployed handler**. **1.0 is PASS.**
- **1.12 and 1.13 PASS as of 2026-08-25**, once `finalize_cluster_build`
  existed to render the scripts (see the finding below). 1.12 returned
  `user=ubuntu conn=127.0.0.1 43780 127.0.0.1 22` -- loopback, so the
  session traversed SSM; 1.13 with the plugin off `PATH` warned by name
  and fell back to direct ssh reporting the operator's real address,
  which is the discriminator the item specifies.
- **The blocking finding, now fixed.** A
  `wait=False` build -- every MCP build -- returns before the post-launch
  pipeline, so `access_cluster.<name>.sh`, `grafana_tunnel.<name>.sh` and
  `retrieve_ssh_key.<name>.sh` are **never generated**. The function's own
  message says "Re-run once the cluster reaches CREATE_COMPLETE to finish
  those steps", and re-running is **refused**: the vars file exists, so it
  aborts with "delete it properly, then retry the build". Verified. This
  is the create-side twin of the teardown gap already closed by
  `finalize_cluster_teardown`. **`core_finalize_cluster_build` and the
  `finalize_cluster_build` tool now close it**, verified live against the
  cluster in exactly that state: the three access scripts rendered into
  `active_clusters/`, the staging tree reached the head node, the summary
  published, and **zero `.pem` objects** reached S3 (that exclusion is the
  one thing this path must never get wrong).

  Two design points worth carrying: it reads its context from the
  **rendered vars file** rather than reconstructing the build's in-memory
  state -- 124 keys, sufficient for every template, and the same artifact
  every other surface consumes -- and it is **local-only**, in
  `_LOCAL_ONLY` beside `rotate_cluster_key`, because it writes the
  operator's own `active_clusters/` and scp's with the local `.pem`. So
  the *remote* create path still ends at a running cluster with no access
  scripts and no summary; that is a smaller gap (those artifacts are only
  useful on the machine that runs them) but it is a real one and is not
  claimed as closed.
- **1.9 skipped** by choice (monitoring off on *this* build). There were
  several clusters named `certify`; an earlier one did carry monitoring,
  which is where the audit's 1.9 PASS comes from, and `stageb` carried it
  too — so 1.9 is PASS overall and was skipped only here. If the two
  statements read as a contradiction, that is why.
- 1.4 was **broken and is now fixed**, verified live on `osiris`.

**1.0 `create_cluster` kicks off and publishes to the store — $$$ CLUSTER,
20–45 min. PASS 2026-08-25 (task L1), after failing earlier the same day.
The failure and its cause are kept below because they are the record of how
the defect was found; the tool succeeds now.**

**As it stood when this item first failed: `core_create_cluster` never
returned a value.** Every terminal path in it
is `sys.exit(...)` — including the `wait=False` success path, which ends
`sys.exit(0)` at `pcluster_core.py:9264` after publishing the record and
releasing the lock. It is CLI-shaped, and unlike `core_delete_cluster`
(which returns a `DeleteClusterResult`) it was never migrated to a return.
So `mcp_server.tools.create_cluster`'s `_plain(core_create_cluster(...))`
can never receive anything: on success it gets `SystemExit(0)`, on failure
`SystemExit(1)`.

`SystemExit` is a `BaseException`, and `create_cluster` is deliberately
**not** wrapped in `_cluster_lock`'s `SystemExit` → `PClusterMakerError`
translation, because it locks internally and wrapping would deadlock. So a
*successful* MCP `create_cluster` **kills the server**. Observed exactly
that: the call never returned, was still "running" after fifteen minutes,
and the MCP server disconnected. This is the same defect class as
`_require_record`'s unwrapped `sys.exit` fixed earlier in the session, on
the one path no unit test reaches — every test stubs `core_create_cluster`.

**What does work, and was verified in the same run:** the `_KICKED_OFF`
publish. `vars/certify.json` (822 B) and `configs/certify.yaml` (3,836 B)
both landed, in the **cluster's** region, before the exit. That was the
original fix this item existed to check, and it is correct. The defect is
purely that the caller can never be told.

**The fix, applied:** `core_create_cluster` returns a
`CreateClusterResult` and the CLI shim converts it to `sys.exit`, mirroring
what the delete side already does. **Certified 2026-08-25 by task L1** —
`stageb`, local stdio server, `success=true, exit_code=0, kicked_off=true`,
both store objects in the cluster's own region. The osiris CLI build that
preceded it exercised none of this item (`create_cluster`'s token check,
its kick-off return, `_KICKED_OFF`'s publish), and that distinction still
holds for any future run: **do not read "a cluster exists" as this item
passing.** To re-certify, preview then create with e.g.
`overrides={"compute_instance_type": "c5.xlarge", "base_os": "ubuntu2404",
"initial_cpu_queue_size": 0, "max_cpu_queue_size": 2}`.

**"Expect a return in seconds" is wrong, and was corrected on 2026-08-25.**
`wait=False` governs only the CloudFormation wait; everything before the
kick-off still runs inline in the call -- AZ verification, the download
checksums, the external-NFS probe, the EC2 keypair, the Secrets Manager
secret, the S3 bucket, five IAM policies and the role, the rendered
templates and their staging. Observed: past **120 seconds** with the
keypair and eight local files written but no IAM policies and no stack
yet, so the call had not even reached `pcluster create-cluster`. Once it
did, the dominant cost was **the AWS CDK**: `jsii-runtime.js` ran for
minutes synthesizing the CloudFormation template in Node before the stack
appeared at all. That is the same Node dependency `INSTALL.md` requires
locally, and it lands squarely on the create path -- so a remote
`create_cluster` needs both a Node runtime and several minutes of the 900s
budget before CloudFormation has been asked for anything. This
matters beyond wording: the item is the model for what a *remote* caller
would experience, and this pre-kickoff work is what a 900s Lambda budget
would actually be spent on. Expect minutes, and treat "returns in seconds"
as a claim this item disproves rather than one it checks. **The return value cannot show the record was published** —
`_publish_cluster_record` never raises and prints its warning to the server
process's stdout, which the caller never sees. **SHELL:** confirm both
`vars/<name>.json` and `configs/<name>.yaml` exist. This is the fix for the
`_KICKED_OFF` exit that published nothing.

**1.1 The cluster appears — CLUSTER. VERIFIED LIVE 2026-08-24.**
`list_clusters()` → one entry, `status: "LOCAL"`. Sourced from
`active_clusters/`, so it proves the local path only. 1.10 is what separates
local from store.

**1.2 Live status is authoritative — CLUSTER. VERIFIED LIVE 2026-08-24.**
`list_clusters(live=True)` → a real CloudFormation state, never `"LOCAL"`.
**False pass:** `status: "ERR"` is a *swallowed* error, not a cluster problem.

**1.3 SSH-dependent checks actually run — CLUSTER. FOUND AND FIXED
2026-08-24; re-verified live, all checks pass.** `check_cluster_health`.
`SSH reachability` must be **pass, not skip**; Slurm pass, with a degradation
note if the fleet scaled to zero.

What this item warned about was a `skip: SSH unavailable on this transport`
result. **What it actually caught was worse.** Against a completely healthy
cluster — head node up, login node up, 8 compute nodes `idle~` — the tool
reported the cluster **UNHEALTHY** with `Slurm fail — sinfo returned rc=127:
sinfo: command not found`. Cause: a command-argv `ssh <host> sinfo` runs a
non-interactive, non-login shell and gets the bare system `PATH`;
`/opt/slurm/bin` is appended by `/etc/profile.d` on a **login** shell only.
So the check had never worked against any real cluster, and could not have —
this was not a regression, it was a defect that shipped and was never
exercised. The fix wraps the probe in a shell-quoted script so a login shell
sources the profile before `sinfo` is resolved.

**Three false passes now, not one.** (a) `skip: SSH unavailable on this
transport` — still the original one; grep for `"skip"` explicitly. (b) A
`fail` naming `rc=127` is **not** a cluster problem and must never be read as
one; it is this bug back. (c) The fix is a shell-quoted script, not an argv
list, and it looks like needless indirection: **anyone "simplifying" it back
to a bare argv list reintroduces the defect**, and the resulting failure
accuses a healthy cluster.

**1.4 Queue listing matches — CLUSTER. WAS BROKEN OVER MCP; fixed and
verified live 2026-08-24.** `list_queues`. Certifies the local read only; the
config comes from `active_clusters/` first.

The wrapper was annotated `-> dict` while returning a list. FastMCP validates
structured content against the annotation, so **every call over MCP failed**
with `structured_content must be a dict or None`. Now `-> list[dict]`.
**False pass, and it is a nasty one:** the failure is not empty — FastMCP's
error text *carries the correct queue payload inside it*, so a hurried
operator reads the queues they expected, sees their data, and marks the item
passed. Certify on the call **succeeding**, not on the queue names appearing
somewhere in the response. Any other tool whose return type is a list is
worth checking against its annotation for the same reason.

**1.5 Access resolution — CLUSTER. VERIFIED LIVE 2026-08-24** on a cluster
built *with* `--enable_loginnode true`: no flags correctly resolved to the
**LoginNode**, and `head_node=True`/`login_node=True` each resolved to the
right distinct host. The without-`enable_loginnode` half below is **not**
certified — that cluster had a login node. `resolve_access_info` with no flags,
`head_node=True`, `login_node=True`. On a cluster built without
`enable_loginnode`, `login_node=True` must **error**. Returning `HeadNode` is
the documented failure mode. It routes through `_require_record` now (it
used to do `_read_cluster_record(...) or {}` and answer confidently from an
empty record), so also call it for an **untracked** name and expect the 0.4
"not tracked" message rather than a resolved `HeadNode`.

**1.6 Diagnostics — CLUSTER. SAME `PATH` DEFECT AS 1.3; fixed and verified
live 2026-08-24.** `diagnose_cluster(hours=1, include_cloudwatch=True)`.
Sections 2–5 populated, not SKIP. `ResourceNotFoundException` (group absent)
and `AccessDeniedException` (IAM) must stay on **separate** messages.

Two of its four probe types carried 1.3's bug — `sinfo -N -l` and `sacct`,
both of which live in `/opt/slurm/bin`. `tail` and `test -f` were unaffected
because they are on the bare non-interactive `PATH`, which is exactly why the
defect was partial and looked like a Slurm problem rather than a `PATH` one.
**False pass:** a report in which the `tail`/`test -f` sections are populated
and only the Slurm sections are empty or `rc=127` reads as "this cluster's
Slurm is sick" — it is this bug. Both are fixed by the same shell-quoted
probe as 1.3 and carry the same "do not simplify to an argv list" warning.

**1.7 `preview_cluster_delete` mutates nothing — CLUSTER. PASS 2026-08-25.** Re-run
`check_cluster_health` after; unchanged. Also run with
`delete_s3_bucketname=False` and confirm the list flips **and** the token
differs — that option is bound into the token.

**1.8 `rotate_cluster_key(dry_run=True)` returns no key material — CLUSTER.
PASS 2026-08-25.**
Scan the *serialized* response for `BEGIN OPENSSH PRIVATE KEY` / any base64
block. A path naming the `.pem` is correct; its contents are not.

**1.9 `manage_grafana_tunnel` — CLUSTER, only with `enable_monitoring`.
PASS.** Open then `stop=True`. Skip otherwise; the script won't exist. It
was **not** run during the 2026-08-24 session — the note saying so stood
here for a while and was wrong by the time it was read — but it was run on
the monitoring-enabled `certify`: tunnel opened and port bound, stopped and
port released. `stop=True` returning success is not evidence the tunnel
stopped: see 1.14, which drives the same script and where that exact false
pass was a shipped bug.

**1.10 Store-only resolution — CLUSTER, SHELL. PASS 2026-08-25. The single
highest-value item here.** Move `src/vars_files/<name>.yml` and `active_clusters/<name>/` aside,
then `check_cluster_health` and `list_clusters`. Both must still work, sourced
from S3. SSH checks will now fail (the `.pem` moved) — that is correct; the
CloudFormation and head-node-IP checks are the ones that must pass.
`list_queues` should read from `configs/` and match 1.4. **This is the only
item that exercises the store read path end to end**, and until this run
nothing ever had. Restore both paths afterward.

**1.11 The store is addressed in the cluster's region — CLUSTER, SHELL.
PASS 2026-08-25.**
Build into a region *different* from `AWS_DEFAULT_REGION`. **SHELL:** the
record is in the **cluster's** region bucket and **not** the process's. With
local files present, MCP reads work (region comes from the record). With them
moved aside, `list_clusters` will **not** show it — that is stated behavior,
not a bug; certify that it degrades to "not tracked" **with the region named**,
rather than to a traceback.

**1.12 ssh reaches the node through Session Manager — CLUSTER, SHELL.
VERIFIED LIVE 2026-08-24.** `resolve_access_info`, then run the generated
`access_cluster.<name>.sh`. `access_cluster.j2` now sets
`-o ProxyCommand` over `AWS-StartSSHSession`, so the session traverses SSM
rather than opening port 22 from the operator's address. Claim: *"the SSM
path lands as the right user with the right environment, not as a degraded
or differently-privileged session."* Expect the login user for the `base_os`
(`ubuntu` on `ubuntu2404arm`) and `SSH_CONNECTION` beginning `127.0.0.1` —
both observed. **The `127.0.0.1` is the assertion**, not decoration: a direct
ssh reports the operator's real source address there, so it is the only
in-band proof the ProxyCommand was actually used. **False pass:** a shell
that opens and works. It works either way — a silent fall through to direct
ssh (1.13) gives an identical prompt, and only `SSH_CONNECTION` separates
them. Confirm the user too: landing as `root` or as the wrong distro's
default user means `_resolve_ec2_user` and the transport disagree.

**1.13 The plugin-absent fallback is a warning, not a failure — CLUSTER,
SHELL. VERIFIED LIVE 2026-08-25.** Run the same script with
`session-manager-plugin` off `PATH`
(rename it, or invoke with a stripped `PATH`). Claim: *"an operator without
the plugin installed is not locked out."* Expect a **warning naming the
plugin** and a direct ssh that succeeds — `SSH_CONNECTION` then carries the
operator's real address, which is how you tell this branch from 1.12. **Two
false passes:** (a) a silent fallback — with no warning the operator never
learns why their session bypassed SSM, and a security posture that depends
on the ProxyCommand quietly stops holding; (b) a hard failure, which makes
the plugin a new hard dependency of the toolkit rather than a preferred
transport. Observed: it warned by name and fell back to a direct ssh
reporting the operator's real address, which is the discriminator against
1.12.

**1.14 The tunnel's PID file names the process that must be killed —
CLUSTER, SHELL, only with `enable_monitoring`. FOUND AND FIXED 2026-08-24;
PASS 2026-08-26 as task L2, which is the first time the item itself was
run.** Open `grafana_tunnel.<name>.sh`, then `stop`.
Claim: *"`stop` stops the tunnel it opened."* **This was a real bug, and it
is the whole reason the item exists.** The PID was recovered by `pgrep`ing
for the head node's **IP address** in the command line — true of a direct
ssh, false over SSM, where the ProxyCommand's command line carries the
**instance ID** instead. So `pgrep` matched nothing, `stop` reported success,
and the tunnel kept running and kept the local port bound. **False pass, and
it is the default one:** `stop` printing success proves nothing here — it
printed success while doing nothing. Certify it the way it was certified
live: forward to the head node's `:22`, read sshd's banner back through the
local port (proof the tunnel carries traffic, not merely that a process
exists), then `stop`, then read the port again and require the connection to
be **refused**. A banner after `stop` is the bug. Forwarding to `:22` rather
than to Grafana's own port is deliberate: it needs no working Grafana behind
the tunnel, and the Grafana port proves less, since a dead backend and a dead
tunnel look the same from the client.

---

## Measured facts from the 2026-08-24 live run

Not certification items — measurements an operator will otherwise have to
take again, recorded here because each one contradicts a plausible
assumption.

* **SSM is not the slow path.** `ssm send-command` round-tripped in ~950 ms
  against ~1000 ms for the equivalent ssh. Do not restructure a probe to
  "avoid the SSM penalty"; on this cluster there was none.
* **`ssm get-command-invocation` truncates at exactly 24000 bytes**, with an
  in-band `--output truncated--` marker. Bracketed live: 23 KB came back
  intact, 24 KB came back truncated. The marker is *inside* the payload, so
  anything parsing that output must look for it — a truncated result is
  well-formed and silently short.
* **Headroom is real but not generous.** `diagnose_cluster`'s largest actual
  output was 5,846 bytes, about a quarter of the ceiling. A cluster with a
  larger fleet or a longer `hours` window is the case to watch; the ceiling
  is a fixed byte count, not a per-node budget.

---

## Phase 2 — config edits

All **CLUSTER**. None mutate the running cluster.

**Status: 14 of 14 sub-cases PASS** (2026-08-25, with 2.12b re-run
2026-08-26 as task L3). The item bodies below carry no per-item verdict;
they are in the item-by-item audit near the end of this file.

**2.1 Baseline — CLUSTER.** `list_queues`; record it exactly.

**2.2 `add_queue` writes locally and mirrors — CLUSTER.** Expect `config_path`
to be the **local filesystem path**, not an `s3://` URI. **SHELL:** confirm
the queue is in the store copy. The mirror failure is a printed warning that
**never reaches the caller** — a successful-looking `add_queue` whose store
copy is stale is the false pass, and there is no MCP-visible signal for it.
Also confirm a `.bak` was written, and that a hidden
`.config.<name>.mirror-etag` now sits beside the config: that marker is what
later distinguishes "local is ahead" from "local is behind", and its absence
means every future divergence is refused without a direction.

**2.3 The edit is visible — CLUSTER.** `list_queues` shows the new queue.

**2.4 Duplicate queue name refused — CLUSTER.** Re-run 2.2's call verbatim.

**2.5 Architecture mismatch refused — CLUSTER.** Add a Graviton type to an
x86_64 cluster.

**2.6 Removing a nonexistent queue lists what exists — CLUSTER.** The
available-queue list is the point; an error without it forces a second call.

**2.7 The last queue cannot be removed — CLUSTER.** Restore afterward, or
`apply_cluster_update` will reject the config.

**2.8 A second machine's write refuses the edit — CLUSTER, SHELL.** Edit
`configs/<name>.yaml` out of band, then `add_queue`. Expect
`ClusterConfigConflict` — and specifically the wording *"changed by another
machine since this one last mirrored ... the local copy is behind"*, which
is only reachable when a mirror marker exists (2.2). **If `add_queue`
succeeds, your out-of-band queue is gone** — the regression the fix was
written for. Two further directions to certify:
  * **No marker at all** (a machine that has never mirrored): the refusal
    must say there is *no record of which is newer* and must **not** claim
    the local copy is behind. Content alone cannot tell the two apart, and
    the first version of this check asserted "behind" unconditionally.
  * **Whitespace only:** re-upload a semantically identical but reformatted
    config. **This DOES conflict, and the expectation above was wrong** --
    measured 2026-08-25. The comparison *is* structural, but the mirror
    marker is an ETag and is checked first, so any store write moves it and
    the edit is refused before `yaml.safe_load` is consulted. Certify the
    behavior as it is: a reformat-only push from another machine blocks the
    next local edit. Conservative and safe; just not what the old wording
    promised.

**2.9 A genuine ETag conflict — CLUSTER, SHELL, two writers.** Only reachable
with local files moved aside (otherwise you take 2.8's branch). Realistic
route: move them aside, read the ETag out of band, overwrite the object, then
`add_queue` — which now holds a stale ETag and must be rejected. **The 409
half is not reproducible on demand;** treat it as unit-test-only.

**2.10 A deleted stored config is a conflict, not a boto error — CLUSTER,
SHELL.** Read the ETag out of band, **delete** `configs/<name>.yaml`, then
make an edit that writes back conditionally (from a machine with no local
config, per 2.9's setup). Expect `ClusterConfigConflict` saying the config
*was deleted* — a different remedy from *changed*: re-publish rather than
re-read. **False pass:** a raw botocore `ClientError` reaching the caller.
S3 answers a conditional write against a missing key with
`NoSuchKey`/**404**, not 412 (verified against real S3, us-east-2), so it
escapes the 412/409 predicate unless handled separately. **Also certify the
discrimination:** an unrelated failure — an IAM denial on the same call —
must **not** be reported as a deleted config. A predicate that says yes to
everything passes this item while turning a permissions problem into "your
config was deleted".

**2.11 A CLI edit is recognized as ahead, not refused — CLUSTER.** The
common single-machine sequence: build (publishes the config and writes the
mirror marker), then `./manage_pcluster_queue.py -N <name> -A ...`, then
`add_queue` over MCP. Expect the MCP edit to **succeed**, with an
`*** INFO ***` line saying the local config is ahead and is being mirrored
up. **False pass — this is the one that was broken:** a
`ClusterConfigConflict` telling you the local copy is behind, when it is the
newer one. **Then repeat both edits a second time** — the marker must be
rewritten on every mirror, and leaving it at the publish value makes the
*second* round misdiagnose this machine's own change as another machine's.

**2.12 A CLI queue edit reaches the store at all — CLUSTER, SHELL.**
`./manage_pcluster_queue.py -N <name> -A compute -E c5.large -Q clitest`,
then **SHELL:** confirm `clitest` is in `configs/<name>.yaml`. The CLI
passed no store at all until 2026-08-24, so every CLI edit silently diverged
it. **Also certify the degradation:** run the same command with credentials
that cannot read the store and confirm it still edits the local file and
prints `Shared store unreachable` — editing a file on your own disk must not
depend on S3.

---

## Phase 3 — fleet and update operations

All **CLUSTER**. These hold the per-cluster S3 lock.

**Status: 8 of 8 PASS** (2026-08-25; 3.8's blocking path ran 2026-08-26
as task L4, which is what took it from scoped-only to full). Per-item
verdicts are in the item-by-item audit near the end of this file.

**3.1 `stop_fleet` returns without blocking — CLUSTER.** Seconds, not minutes;
poll `check_cluster_health`. **SHELL:** `locks/<name>.lock` must be absent
afterward — a leaked lock blocks every later operation.

**3.2 A held lock is a tool error, not a dead server — CLUSTER, two processes.**
Hold the lock from a shell, then call `start_fleet`. Expect a prompt error
naming the lock's **owner**. **False pass:** the call hanging (it must never
wait), or the session dying. Issue another call afterward to confirm the server
is alive.

**3.3 `apply_cluster_update` fetches from the store — CLUSTER.** Fleet stopped;
call with **`config_path` omitted**. **SHELL:** confirm no temp config survives
in `$TMPDIR` — a reused container would apply one caller's config for the next.
Also certify the ordering claim cheaply: call it with the fleet **running** and
confirm PCluster's own rejection.

**3.4 A queue edit is refused while `UPDATE_IN_PROGRESS` — CLUSTER, time-boxed.**
`add_queue` while 3.3's update is in flight → `ClusterConfigConflict` naming the
status. **Two false passes:** (a) the edit succeeding is the race the guard
closes; (b) the guard is **fail-open** by design — a failed describe lets the
edit through, so confirm independently via `list_clusters(live=True)` that the
status really was `UPDATE_IN_PROGRESS`. **Also certify the mirror image:** once
the update completes, the same call must succeed.

**3.5 An `s3://` config path is rejected with a pointer — CLUSTER.** This is
what `add_queue` returns on a machine with no local config, so handing it
straight to `apply_cluster_update` is the obvious next move.

**3.6 An explicit path is used exactly as given — CLUSTER.** Pass a
nonexistent path; expect PCluster's own file error, **not** a silent fallback
to the store. **The update succeeding is a dangerous false pass** — it applies
*something*.

**3.7 `start_fleet` — CLUSTER.** Returns immediately; poll until Slurm reports
usable nodes. **False pass:** `[PASS] Slurm` with a fleet entirely
`down`/`drained`. Zero usable nodes must **fail**; partial capacity passes
*with a note*. Read the note.

**3.8 `apply_queue_config` — CLUSTER, ~30 min, local only. PASS: the cheaper
partial 2026-08-25, the blocking path itself 2026-08-26 as task L4, measured
at 30.5 minutes.** The one blocking tool. Largely redundant with 3.1/3.3/3.7
— run only to certify the blocking path itself. Cheaper partial: confirm it
is registered locally and **absent from `TOOL_TIERS`**. **SHELL:** lock
released afterward. Note the local MCP client aborts a tool call after 1800s
of silence, so even locally this tool can outlive its caller — see L4.

---

## Phase 4 — teardown

All **$$$ CLUSTER**, destructive.

**Status 2026-08-24 (`osiris`, us-east-1): 4.1-4.5 and 4.8 PASSED** on a
real cluster via the two-phase path. Stack gone at 18m26s; finalize
completed in 5.7s with no orphans. 4.5 additionally found two defects in
the finalize messaging (a banner asserting the stack was gone before the
gate looked; a raw `TIMED_OUT` constant in the refusal) — both fixed.

**Status 2026-08-26: 4.6 PASSED** at `stageb`'s teardown, as task L5 — the
only time it can be run, since spending a token consumes what it
authorizes. Both tokens were spent on the wrong tool and both refused;
the correct token then succeeded, which is the vacuity guard. Detail is in
L5 below. **4.7 still needs CloudTrail** in its own terms: R5 proved by
`iam:SimulatePrincipalPolicy` and an ephemeral trail that a tier cannot
exceed its IAM, but nothing has yet confirmed that a *finalize* call emits
no `DeleteCluster`/`DeleteStack` event.

**4.1 Preview mints a token bound to the options — CLUSTER.** Fresh token
inside the 15-minute window.

**4.2 `delete_cluster` kicks off and cleans up nothing — $$$ CLUSTER.**
Returns in seconds. Then verify the **negative**: `list_clusters()` still shows
it; **SHELL:** `vars/` and `configs/` objects still exist; keypair, `.pem` and
secret all still exist. **False pass:** store objects *gone* here means the
`cf_delete_confirmed` gate was bypassed — that would hide a cluster still
running and billing from every other machine.

**4.3 Poll to stack gone — CLUSTER.** `list_clusters(live=True)` until the
cluster no longer resolves.

**4.4 `finalize_cluster_teardown` completes teardown — $$$ CLUSTER.** New
preview; use its **`finalization_token`**, not `confirmation_token` (the two
are deliberately not interchangeable — check that the wrong one is refused,
which is item 4.6). Expect the record and config steps **succeeded**, not
`"skipped: cluster deletion not confirmed"`. **SHELL:** `vars/`, `configs/`,
`locks/` objects gone; keypair, `.pem`, `active_clusters/<name>/`, secret gone.
**Two false passes:** (a) a step result reads `success=True` *with* a
`"skipped"` detail — read the detail on every step; (b) the CloudWatch log
groups must **survive** — deleting them destroys the only record of the build.

**4.5 Finalizing too early is refused, not silently ignored — CLUSTER.** Run
4.4's call *before* the stack is gone (i.e. between 4.2 and 4.3). Expect
`success=False`, `exit_code=1`, and a message naming the state it saw.
**SHELL:** every object from 4.2 still present. **False pass:** a second
`delete_cluster` in this position returns `success=True` having cleaned
nothing — that is the behavior this tool exists to replace, so a green result
here means the wrong call was made.

**4.6 The two tokens are not interchangeable — CLUSTER. PASS 2026-08-26
(task L5).** `delete_cluster`
with the `finalization_token`, and `finalize_cluster_teardown` with the
`confirmation_token`. Both must be refused. One authorizes starting a stack
delete; the other authorizes destroying the credentials.

**4.7 `finalize_cluster_teardown` never issues a delete — CLUSTER.** The
reason it exists rather than a second `delete_cluster`: that second call
re-issues `delete-cluster` against the *name*, so if the name has been rebuilt
since, it deletes the new cluster's stack. Not safely testable against a real
rebuild; verify from CloudTrail that the finalize call produced **no**
`DeleteCluster`/`DeleteStack` event.

**4.8 Post-teardown the cluster is genuinely untracked — CLUSTER.**
`check_cluster_health` → the 0.4 message, naming the region.

---

## What cannot be certified from the MCP surface alone

**A. Everything about the remote transport** — per-tier routing, `_LOCAL_ONLY`
actually excluding tools, the 900s ceiling, `ssh_available=False` SKIP
branches, the per-tier IAM prefix grants, Cognito auth, the router's
`FunctionError` handling. Needs a deployed topology, API Gateway and a Cognito
pool. **Superseded 2026-08-25/26**: all of it except the `FunctionError`
handler-ran-and-failed half and a Claude web session has now been driven on
deployed handlers (R1-R6). It stands as a *category* statement — none of it
is reachable from the local stdio server, so certifying it again means
redeploying the transport, which is torn down.

**B. Store writes landing in the right bucket** — 1.0, 1.11, 2.2, 4.2 and 4.4
all need direct S3 inspection. **No MCP tool reports where a record was
written, or whether one exists.** Both publishers never raise and warn only to
the server's stdout, so a build that silently failed to publish looks identical
to one that succeeded.

**C. A genuine two-writer conflict** — needs a second machine or an out-of-band
write; the 409 half is not reproducible on demand.

**D. Lock contention** — needs a second process.

**E. CLI/MCP defaults parity** — `make_pcluster.py` has no dry-run, so parity
is only observable by building the same cluster twice and diffing the vars
files (~90 min, real money). Both paths call the same `load_cluster_defaults`,
but `_resolve` and `build_make_cluster_params` implement the precedence
*separately* — that separateness is the risk, and a single-surface
certification cannot see it.

**F. `access_cluster`** — `tools.py` documents an exclusion for a tool that was
never written. Nothing to certify.

---

## Resolved since this was written

**The CLI queue editor now mirrors.** It called the core editors with no
`s3=`/`locks_bucketname=`, so a CLI queue edit wrote the local file, never
mirrored, and never took the `_same_config`/`IfMatch` checks — which, once
the staleness check landed, meant a CLI edit silently diverged the store and
the next MCP edit was refused against a copy left behind on purpose.
`manage_pcluster_queue.py` resolves the store from the cluster's own record
(same region rule as `_record_store`) and passes it through; an unreachable
store degrades to a local-only edit with a warning, following
`_publish_cluster_record`'s precedent that publishing never fails an
operation the operator actually asked for. Certified by 2.11 and 2.12.

**Every ssh probe now runs under a login shell.** `check_cluster_health`'s
`sinfo` and two of `diagnose_cluster`'s four probes ran as a command-argv
ssh, which is non-interactive and non-login, so `/opt/slurm/bin` was never on
`PATH` and both returned `rc=127: command not found`. The health check
therefore reported a fully healthy cluster as UNHEALTHY, and had done so
against every cluster it had ever been pointed at — it was simply never
pointed at one until 2026-08-24. Both now wrap the probe in a shell-quoted
script. Certified by 1.3 and 1.6, both re-verified live.

**`list_queues` returns what it says it returns.** Annotated `-> dict` while
returning a list, so FastMCP's structured-content validation rejected every
call — with the correct payload embedded in the error text, which is what
made it look like a formatting complaint rather than a total failure. Now
`-> list[dict]`. Certified by 1.4.

**The Grafana tunnel's `stop` stops the tunnel.** The PID was found by
`pgrep`ing the head node's IP, which the command line no longer contains once
the session runs through Session Manager's ProxyCommand — it carries the
instance ID. `stop` reported success while the tunnel stayed up and the local
port stayed bound. Certified by 1.14.

## Work plan (2026-08-25) — completed 2026-08-26

**All 14 tasks are done and the plan is a record, not a queue.** It is kept
in its original imperative voice because the sequencing is the useful part
if this is ever re-run; the *Status* column and the per-task bodies carry
what actually happened. The plan's own premise — one cluster (`stageb`)
built through local MCP and kept alive from Stage B through Stage C — held,
and that cluster and the transport are both gone now.

**Record each verdict in this file as it is produced.** That rule exists
because Phases 2 and 3 were run and then lost to a conversation.

| # | Task | Status | Cluster? | Transport | Certifies |
|---|---|---|---|---|---|
| **A1** | `deploy.py` production caller | **DONE** 08-25 | no | either | — (removes the scratchpad-only path) |
| **A2** | Move image off Node 18 (EOL) | **DONE** 08-25 | no | remote build | — (removes an EOL runtime) |
| **A3** | `_setup_mcp_infra` from scratch | **DONE** 08-25 | no | remote | the IAM set creates cleanly in one go |
| **L1** | Build via local MCP `create_cluster` | **PASS** | **builds it** | **local only** | 1.0 kick-off + publish |
| **L2** | Tunnel PID file | **PASS** | **yes** | **local only** (`_LOCAL_ONLY`) | 1.14 |
| **L3** | CLI edit warns on unreachable store | **PASS** | **yes** | **local only** (CLI) | 2.12b (was the last outstanding FAIL) |
| **L4** | `apply_queue_config` blocking path | **PASS** | **yes** | **local only** (`_LOCAL_ONLY`) | 3.8 + a real duration vs the 900s ceiling |
| **L5** | Tokens not interchangeable | **PASS** | **yes** | either (do at teardown) | 4.6 |
| **R1** | Redeploy five tiers | **PASS** | no | **remote only** | the deployment path end to end |
| **R2** | API Gateway + Cognito | **BUILT + VERIFIED** | no | **remote only** | the transport is reachable over HTTPS (a Claude web session is still unrun) |
| **R3** | Auth: `token_use`, `client_id`, 401-not-403 | **PASS** | no | **remote only** | the whole of Workstream 6 |
| **R4** | 900s ceiling observed | **PASS** (4 defects found) | **yes** | **remote only** | the constraint behind the tier split |
| **R5** | A tier cannot exceed its IAM (policy simulation) | **PASS** | no | **remote only** | 4.7 in part — the ceiling, not the finalize-emits-no-delete half |
| **R6** | Every read-only tool driven remotely | **PASS** | **yes** | **remote only** | the tier's IAM floor, not just its ceiling |

**14 of 14 done.** L5 closed at teardown, the only time it could be. **R6 was
added mid-stage**, after the row count above was first written: R5 simulated
each tier's *ceiling* and passed 31/31 while two tiers could not reach their
own **floor**, so R6 drives every read-only tool for real. Any "13 of 14" or
"N tasks" count elsewhere in this file predates R6.

The single cluster from L1 carried through R6, so it was built once — and it
is now torn down, along with the `r4probe` queue R4 left on it, the transport,
the ECR repository, the log groups and every locks-bucket object. Auth (R2,
R3) needs no cluster and can be done before or after.

### Stage A — code, no cluster, no AWS

- [x] **A1. DONE 2026-08-25 — `deploy_mcp.py`.** Every deployment so far
      ran from a scratchpad script, which is how the update path's
      `ResourceConflictException` survived undetected. A real entry point
      (or a documented `make` target) that builds, prunes, uploads and
      deploys a tier.
      *Done when:* one command deploys a named tier end to end.
- [x] **A2. DONE 2026-08-25 — Node 22.23.1.** It is EOL (2025-04-30) and the
      CDK says so in its own banner on every invocation. Move
      `Dockerfile.stack-mutation-node` to Node 20 or 22 and rebuild.
      *Done when:* the build-time guard passes on the new major and a
      `preview_cluster_config` still answers.
- [x] **A3. DONE 2026-08-25 — 7 roles, 10 policies, clean.** The IAM fixes landed
      incrementally, patched as live failures surfaced; creating all seven
      roles and ten policies from nothing is a path never run in one go.
      *Done when:* a fresh create produces the same policy set the working
      deployment ended with.

**Stage A results (2026-08-25).**

* **A1** — `deploy_mcp.py`, following the repo's entry-point conventions
  (`sys.prefix` venv guard, `#!/usr/bin/env python`). Builds a tier, prunes,
  checks the 250 MB unzipped ceiling *before* uploading, routes anything over
  the 50 MB direct-upload limit through S3, and refuses the image tier
  without `--image-uri` rather than building a zip that could never work.
  Verified `--dry-run` on `router`. Pinned by
  `TestTheDeploymentHasAProductionCaller` (6 tests, including that it builds
  for `manylinux2014_x86_64` rather than the operator's arch).
* **A2** — the base image's bare `nodejs` resolves to **Node 18**, EOL
  2025-04-30, which is why the CDK printed an EOL banner on every
  invocation. AL2023 offers `nodejs20`/`nodejs22`/`nodejs24`; pinned to
  **nodejs22** (LTS), now **v22.23.1**. The build-time guard checks the
  *major* (>= 20), not just that `node` exists — presence was already
  checked and the image still shipped an EOL runtime for its whole life.
* **A3** — `_setup_mcp_infra` run against an empty account: **7 roles and 10
  policies created in one pass**, matching what the incrementally-patched
  deployment ended with, and every per-tier attachment verified equal to
  `_MCP_LAMBDA_TIERS`. `MCPClusterBuild.json_src` is correctly wired in.
  A Cognito pool was created to satisfy the `mcp_user_pool_id`
  requirement (the function refuses an empty one) and **the whole set was
  then torn down again** at the operator's request, via `_delete_mcp_infra`
  -- which is driven by the same `_MCP_LAMBDA_TIERS` table as the setup, so
  the two cannot disagree about what exists. Verified empty afterward.
* **The pool name is now derived, not chosen.** The hand-made pool was
  `pclustermaker-mcp-certify` -- a *cluster's* name on an account-wide
  resource, which went stale the moment that cluster was torn down and the
  pool was not. `_derive_mcp_user_pool_name(*, aws_account_id, region)`
  gives `parallelclustermaker-mcp-<acct>-<region>`, one family with the
  locks and results buckets, keyword-only, signature-pinned so it cannot
  see a cluster or a serial, and length-checked against Cognito's 128.
  `deploy_mcp.py --setup-infra` creates it under that name. Note the region
  buys **legibility, not uniqueness**: a pool is regional and its name is
  already unique within an account and region.

### Stage B — one cluster, local MCP (Claude Code)

Build with `--enable_monitoring true` and `--enable_loginnode true`: L2
needs Grafana, and the login node is what makes `resolve_access_info`
interesting.

- [x] **L1. PASS 2026-08-25 — item 1.0 on the local path.** `create_cluster`
      through the local stdio server returned
      `success=true, exit_code=0, kicked_off=true` for `stageb`
      (serial `stageb-20250026082026`, us-east-1), and both objects landed
      in the cluster's own region: `vars/stageb.json` and
      `configs/stageb.yaml`.
      **The record carries 34 fields with all 11 teardown inputs present**,
      which is the half worth stating: it proves the server was running
      current code rather than the 23-field projection, and that Stage C's
      store-driven teardown has what it needs.
      *Caught before spending anything:* the stdio server had been running
      since 11:28 against a `pcluster_core.py` last modified at 19:15 --
      eight hours and five commits stale, and a stdio server cannot
      hot-reload. Comparing the process start time to the source mtime is
      the check; do it before any MCP certification item, since a stale
      server certifies code that no longer exists.
- [x] **L2. PASS 2026-08-26 — item 1.14, the tunnel's PID file names the
      process that must be killed.** On `stageb`: start bound port 8443 and
      wrote PID 20045 to `/tmp/grafana-tunnel-stageb.pid`; `lsof` confirmed
      20045 was the process holding the port, so the file names the right
      one. Stop removed the file, killed 20045 and released the port, with
      no stray forward left behind.
      **The discriminator held**: the ssh target was
      `ubuntu@i-020c840b950b97317` -- the *instance ID*, because the session
      goes through SSM's ProxyCommand -- which is exactly the case where the
      old `pgrep` matching `${HEAD_NODE_IP}` captured nothing, leaving stop
      to report success over a tunnel still running.
      Note the item needed `finalize_cluster_build` first: a `wait=False`
      MCP build renders the access scripts to a temp directory and discards
      them, so `manage_grafana_tunnel` refused until finalize wrote
      `grafana_tunnel.stageb.sh`. That is the designed two-phase flow, not a
      defect, and finalize also put zero `.pem` objects in S3. `manage_grafana_tunnel` is `_LOCAL_ONLY`; this can never
      be done from the browser. It is also the item whose defect (the
      `pgrep` matching the IP while SSM puts the instance ID on the command
      line) was found by other means.
      *Done when:* start, confirm the PID in the file is the live tunnel,
      stop, confirm the port is released and no process survives.
- [x] **L3. PASS 2026-08-26 — item 2.12b, a CLI queue edit warns when the
      store is unreachable.** This was the checklist's only outstanding
      FAIL. With credentials pointed at /dev/null, `manage_pcluster_queue.py`
      printed:

          *** WARNING ***
            Shared store unreachable (NoCredentialsError: Unable to locate
            credentials).
            Editing locally only -- other machines will not see this change.

      Both halves verified: the edit applied to the local config, and the
      store object's ETag was byte-identical before and after
      (`b369a62e9133d743af041db387eb2278`), so the mirror genuinely did not
      happen -- which is what the warning claims. Reverted afterward.
      **A real defect fell out of running it**: `_print_update_reminder`
      appends "in" while both call sites pass a preposition, so every queue
      edit has printed `added to in <path>` / `removed from in <path>` for
      the life of the script. Fixed, three tests, mutation-verified.
      *Done when:* the edit applies locally *and* names the cause — both
      observed, so 2.12b is no longer "fixed but unverified".
- [x] **L4. PASS 2026-08-26 — item 3.8, `apply_queue_config`'s blocking
      path, and the number R4 turns on.** Ran on `stageb` against a config
      carrying a new `l4q` queue. Cluster reached UPDATE_COMPLETE with the
      fleet RUNNING, and `sinfo` on the head node showed the partition live:
      `compute* 2 idle~` / `l4q 2 idle~`.

      **Wall clock: ~30.5 minutes** (00:09:27 -> 00:40:00), against
      **Lambda's hard 900s (15 min) ceiling**. So a remote
      `apply_queue_config` would be killed at roughly the halfway point --
      mid-update, fleet stopped, S3 lock held by a dead process, which is
      precisely the state `CLAUDE.md` says must never be reachable. **The
      900s ceiling is a measured constraint, not a theoretical one**, and
      this retroactively justifies both the `_LOCAL_ONLY` placement and the
      decomposed remote path (stop_fleet -> apply_cluster_update ->
      start_fleet) that R4 exercises.

      **Second finding, about the local transport itself**: the MCP client
      aborts a tool call after 1800s of silence, and this tool is
      *documented* as taking up to ~30 minutes -- so it can outlive the
      caller's patience even locally. The work completed correctly on the
      cluster; only the client stopped waiting. An operator running this
      through MCP needs `CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT` raised, or
      should drive the three phases separately. ~30 minutes,
      `_LOCAL_ONLY`, three causally dependent phases. This is the closest a
      local run gets to the 900s ceiling that shapes the whole tier split.
      *Done when:* it completes and the fleet comes back with the new queue,
      **and the wall-clock time is recorded here** -- that number is the
      evidence for whether the ceiling is a real risk or a theoretical one.
- [x] **L5. PASS 2026-08-26 — item 4.6, the two tokens are not
      interchangeable.** Done at teardown, which is the only time it can be:
      spending a token consumes the thing it authorizes.

      `preview_cluster_delete` minted both in one call:

          confirmation_token  v1.1787771299.fddcbdc5...
          finalization_token  v1.1787771299.d9b62d73...

      Each was then spent on the *other* tool, and both refused with
      "confirmation token does not match the action and parameters being
      executed -- what is about to run is not what was previewed":

          delete_cluster(finalization_token)            REFUSED
          finalize_cluster_teardown(confirmation_token) REFUSED

      Then the real teardown with the correct token succeeded
      (`exit_code 0`, stack `DELETE_IN_PROGRESS`), which is the vacuity
      guard: a token check that refused everything would have passed both
      tests above while making the tool unusable.

      **Why this matters beyond the guard working.** The two tools sit at
      opposite ends of one teardown and share a single body, so the risk is
      not a forged token -- both are minted by the same server from the same
      secret -- but a *transposed* one. `finalize_cluster_teardown` refuses
      unless the stack is confirmed gone; `delete_cluster` issues a stack
      delete against the name. Handing the second the first's token, on a
      name that had since been rebuilt, is how a live cluster gets deleted
      by a call that meant to tidy up after a dead one.

### Stage C — deployed tiers and the browser

- [x] **R1. PASS 2026-08-26 — the five tiers, plus register and authorizer.**
      Deployed through `deploy_mcp.py`, which is task A1's entry point
      getting its **first production use** -- so A1 is now certified by use
      and not only by test. `--setup-infra` created 7 roles, 10 policies and
      the Cognito pool under the derived name
      `parallelclustermaker-mcp-183295445014-us-east-1` (the naming fix, in
      production), then built, pruned, uploaded and deployed six zip tiers;
      the image tier went via a recreated ECR repository.
      Measured again: each PCluster-carrying zip is 146 MB unzipped / 57 MB
      zipped, so all three route through S3 rather than direct upload.
      Verified: `read-only` 7 tools, `fleet-toggle` 2, `stack-mutation` 4,
      `stack-mutation-node` 3. The **router** answers nothing on a raw
      `tools/list` by design -- it terminates protocol methods and routes
      only `tools/call`, and it expects an API Gateway proxy event -- so it
      was driven with a real proxy event and returned HTTP 200 carrying
      `stageb`'s record, forwarded to the read-only tier and back. Image to ECR, four zips via S3, all
      IAM from `templates/` (including `MCPClusterBuild.json_src`).
      *Done when:* each tier answers `tools/list` with its own tools.
- [x] **R2. BUILT AND VERIFIED 2026-08-26 — API Gateway + Cognito.**
      This was not a certification step: **no API Gateway provisioning
      existed anywhere in the repo**. `deploy.py` made zero `apigateway`
      calls, and `discovery.py`'s two metadata builders were called only by
      tests -- so the authorizer and register handlers had been deployable,
      and unreachable, the whole time. Built as `--setup-gateway` in
      `deploy_mcp.py` (option 1 of three, chosen for the same reason A1 was
      worth doing).

      Live at the time (`https://6g5kyrua2i.execute-api.us-east-1.amazonaws.com`
      — that HTTP API was deleted later the same day when R3 replaced it
      with a REST one, and the REST one is torn down too, so the URL
      resolves to nothing now):
      - `GET /.well-known/oauth-authorization-server` -> 200 (RFC 8414)
      - `GET /.well-known/oauth-protected-resource` -> 200 (RFC 9728)
      - `POST /register` -> **201** with a real `client_id`, PKCE public
        client, Claude's callback URI accepted (RFC 7591)
      - `POST /mcp` -> **401** unauthenticated, routed to the router

      An HTTP API, not REST: cheaper per request, and the one REST feature
      needed (a Lambda authorizer) exists at payload format 1.0 -- which is
      required here, since the authorizer returns an IAM policy keyed on
      `methodArn`. Simple responses stay **off** for the same reason: they
      replace the policy with `{"isAuthorized": bool}`, and the policy is
      what carries the api-wide `Resource` that stops a cached decision
      denying every path but the first one called.

      **That choice was reversed within hours, and the reasoning is why.**
      R3 measured that an HTTP API cannot map an authorizer exception to a
      401 at all, and cannot shape the 401's headers either. `setup_gateway`
      builds a **REST** API today; the payload-format-1.0 and
      simple-responses reasoning above still applies to the authorizer
      contract, only the API type changed. Read the paragraph as the
      decision that was made here, not as what ships.

      **Two defects found by running it.** The discovery routes returned
      registration errors until the `register` tier was redeployed -- the
      handler was edited locally and the deployed zip was stale, which is
      the deploy-vs-source gap in miniature. And `authorization_endpoint`
      and `token_endpoint` rendered as **relative paths** (`/oauth2/authorize`)
      because `MCP_COGNITO_DOMAIN` was never set; RFC 8414 requires absolute
      URLs, and the Hosted UI domain is a *different host* from the
      `cognito-idp` one that serves the issuer and JWKS. Both fixed; all
      four endpoints are absolute now.

      **Gap recorded here, closed by R3 an hour later**: the 401 carried no
      `WWW-Authenticate` header, because HTTP APIs do not let an authorizer
      denial customize response headers. The REST API R3 moved to serves it
      from a gateway response, so the header is present. One detail
      survives: `discovery.www_authenticate_header()` still has no
      production caller — the deployed header is a literal built in
      `setup_gateway`, so the helper and the wire format are two sources for
      one string.

      **Remaining for this item**: connecting an actual Claude web session,
      which is the operator's step, not one this process can drive. **This
      is still unrun** — and the transport has since been torn down, so it
      needs a redeploy first. The one wholly unexercised surface, and the
      reason the transport exists.
      *Done when:* a Claude web session lists tools through the gateway.
- [x] **R3. PASS 2026-08-26 — but only after a rebuild: two of three passed
      on the first measurement, and the third found a transport-level defect
      that took a REST API *and* a message change to fix.** Driven with
      **real Cognito tokens** minted
      through `ADMIN_USER_PASSWORD_AUTH`, because a fabricated token dies at
      the signature check and proves nothing about the two claim rules this
      item exists to certify.

      **The claim shapes, confirmed empirically rather than from the docs:**

          access token: token_use='access'  client_id=<set>  aud=None
          id     token: token_use='id'      client_id=None   aud=<set>

      That is exactly why the authorizer validates `client_id` and never
      `aud`: an access token has **no `aud`**, so API Gateway's native JWT
      authorizer -- which validates audience via `aud` -- would reject every
      valid token. The plan's decision to use a Lambda authorizer instead is
      now evidence-backed.

      - **PASS -- a valid access token authenticates end to end.**
        `POST /mcp` with a real bearer token returned **200** carrying
        `stageb`'s record: gateway -> authorizer -> router -> read-only tier
        -> back. The entire authenticated path works.
      - **PASS -- an unauthenticated call is 401, not 403.** API Gateway
        rejects a missing Authorization header before the authorizer runs.
      - **FAIL -- an ID token yields HTTP 500, not 401.** The authorizer
        behaved correctly and its log says so exactly:
        `Unauthorized: token_use is 'id', expected 'access' -- an ID token
        must never authorize a tool call`. **The code is right and the
        transport mistranslates it**: on an *HTTP* API a Lambda authorizer
        that raises produces a 500, where the REST API contract maps an
        `Unauthorized` exception to 401. 500 is worse than either outcome --
        a client reads a server error and never re-authenticates, so the
        whole point of preferring 401 over 403 is lost.

      **Consequence for the design**, and it is not a small one: the
      401-not-403 rule in `CLAUDE.md` is implemented in the handler but is
      **not achievable on an HTTP API**. Options are (a) a REST API, whose
      authorizer contract supports both the 401 mapping and gateway
      responses that could also supply the missing `WWW-Authenticate`
      header, (b) return a Deny policy and accept 403, contradicting the
      rule, or (c) move token validation into the router, which can return a
      real 401 with headers but defeats the tier split that keeps auth off
      the router.

      **(a) tested 2026-08-26, and the answer is narrower than the option as
      stated.** A throwaway REST API with two authorizers, one raising the
      bare word and one raising a sentence:

          message is exactly 'Unauthorized'   -> HTTP 401
          descriptive message                 -> HTTP 500

      So **a REST API alone does not fix this.** AWS maps an authorizer
      error to 401 only when the message is *exactly* `Unauthorized`, and
      every message this authorizer raises is descriptive -- so it would
      return 500 on a REST API too. The defect is two-part: the HTTP API
      cannot map the exception at all, **and** the message would defeat the
      mapping even where it can.

      The fix is therefore a REST API **and** raising the literal
      `Unauthorized` to the transport, keeping the diagnostic in the log --
      which is where it already goes. The CloudWatch entry
      (`token_use is 'id', expected 'access' ...`) is what diagnosed this
      and survives the change untouched, so the operator keeps the detail
      while the client gets the status code that makes it re-authenticate.
      Whether REST gateway responses also supply the missing
      `WWW-Authenticate` header is **not** yet measured.

      The probe was deleted after measuring: one throwaway API, two
      five-line Lambdas and a role, all removed.

      **The two-part fix was implemented and R3 now passes in full.**

      1. `authorizer_lambda` logs the descriptive message and re-raises the
         bare word `Unauthorized` to the transport. Its class docstring had
         asserted the *name* was what API Gateway maps; the probe showed it
         is the **message**, so that comment was wrong and is corrected. An
         *unexpected* exception is deliberately left unconverted -- a bug in
         the authorizer is a server fault, and dressing it as a 401 would
         send a correctly-credentialled client into a re-auth loop over a
         defect it cannot fix.
      2. `setup_gateway` builds a **REST** API. The HTTP API it replaced was
         deleted.

      Re-measured against the live REST gateway, all four behaviors:

          valid access token              -> 200, real cluster data
          ID token where access required  -> 401   (was 500)
          no token                        -> 401
          revoked client, still-live token-> 401   (was 500)

      **The `WWW-Authenticate` gap closed with it**, which the HTTP API
      could not express: the 401 now carries
      `Bearer resource_metadata="<base>/.well-known/oauth-protected-resource"`,
      served by a REST gateway response. That was recorded as a known gap an
      hour earlier and is no longer one.

      Nothing was traded away for the status code. CloudWatch still shows
      `Unauthorized: token_use is 'id', expected 'access' -- an ID token
      must never authorize a tool call`, so the operator keeps the reason
      while the client gets the code that makes it re-authenticate.

      **Revocation is confirmed as a mechanism**: deleting the app client
      refused a token that was still signed, unexpired and otherwise valid,
      with the authorizer naming it -- `client_id ... does not exist in this
      user pool -- it was never registered here, or it has been deleted to
      revoke access`. `authorizerResultTtlInSeconds=0` is what makes that
      immediate; a cached decision would outlive the deletion. An **ID** token must be refused where an access
      token is required (`token_use` pinned); a bad `client_id` refused; every
      failure a **401**, never a 403 -- a Deny policy does not prompt re-auth.
      *Done when:* all three observed against the deployed authorizer.
- [x] **R6 (added 2026-08-26). PASS -- every read-only tool driven through
      the gateway against a live login-node cluster, zero IAM denials.**

      Added because R5's simulation and R4's three-call path between them
      still left most tools untouched, and the two IAM gaps R4 found were
      both *floors* -- a tier unable to make a call its own tools need.
      Speculating about which action is missing is how those were found, one
      failed run at a time; driving every tool finds them in one pass.

      All eight read-only cases returned a real result over the REST
      gateway with a Cognito access token:

          list_clusters            OK      list_clusters(live)   OK
          list_queues              OK      check_cluster_health  OK
          resolve_access_info      OK      get_cost_report       OK
          preview_cluster_delete   OK      diagnose_cluster      OK

      **IAM denials: 0.** `stageb` carries a login-node pool, so this is
      also the regression test for the `elasticloadbalancing` gap -- before
      that fix, every one of these that describes a cluster failed.

      One case failed on the first pass and the fault was the harness, not
      the tool: `get_cost_report` takes `owner`/`days`, not `cluster_name`,
      and rejected the unexpected keyword. Worth keeping in the record --
      a tool whose arguments are guessed rather than read from
      `tools/list` produces a failure that looks like a defect.

- [x] **R4. PASS 2026-08-26 (fourth method; the first three each measured
      something that was not the thing).** Drove
      `add_queue` -> `stop_fleet` -> `apply_cluster_update` -> `start_fleet`
      on `stageb` through the REST gateway with a Cognito access token.

          add_queue              2.4 s
          stop_fleet             1.3 s   (see caveat)
          apply_cluster_update  20.0 s
          start_fleet            2.0 s
          ---------------------------------------------
          slowest timed call    20.0 s of a 900 s ceiling
          headroom             880 s   (45x)
          L4, blocking, local 1830 s   (91x the slowest phase)

      **Verified as an outcome, not a status.** `apply_cluster_update`
      returned `UPDATE_IN_PROGRESS`; the stack's `LastUpdatedTime` advanced
      04:10:46 -> 17:10:43; the stack reached `UPDATE_COMPLETE`; and the
      launch template **`stageb-r4probe-r4probe-resource`** exists in EC2 --
      the update materialized resources, it was not merely accepted. Fleet
      `RUNNING` at the end.

      **This is what the 900s ceiling buys.** The operation still costs
      ~20 minutes of wall clock; what the decomposition changes is *where*
      the waiting happens. Remotely it happens in the caller's polling
      loop and the slowest Lambda holds for 20s. Blocking, it happens
      inside a function that would be killed near the halfway point with
      the fleet stopped, an update in flight and the S3 lock held by a dead
      process. `apply_queue_config`'s place in `_LOCAL_ONLY`, and the
      three-tool remote path replacing it, are now measured in both
      directions.

      **Caveat, recorded rather than smoothed over:** this run's
      `stop_fleet` reported `status_before: STOPPED`, `plan: done` -- the
      fleet was already stopped by the prior aborted attempt, so its 1.3s
      times a no-op. A real stop from `RUNNING` was measured at **2.0s**
      (`plan: request`) in the preceding run. Neither is near the ceiling
      and the conclusion is unchanged, but the 1.3s figure alone would
      overstate what was tested.

      **Four defects had to be fixed to get here, and each hid the next.**
      The first three runs are in `docs/sessions.md`; in order:
      (1) the three `pcluster.lib` wrappers formatted with a bare `{e}`, so
      every failure read `BadRequestException: ` with nothing after the
      colon; (2) `PCLUSTER_REQUIREMENT` was a *range*, so artifacts resolved
      3.16.0 against a 3.15.1 cluster; (3) `fleet-toggle` had no S3 or
      DynamoDB grant and so could never toggle a fleet; (4) **no tier
      granted any `elasticloadbalancing` action**, so `describe-cluster`
      failed from every tier against any `--enable_loginnode` cluster.

      **And three of my own measurement errors, which is the durable
      lesson.** HTTP 200 was read as success (a JSON-RPC error is a 200
      carrying an `error` member). A CloudWatch `REPORT` line was read as
      success (it records duration, not outcome). Then `"error" not in
      payload` was read as success, while the payload was `""` -- no error
      *and no result* -- as the Lambda logged `No changes found in your
      cluster configuration`, because R4 was applying an **unchanged
      config**. A pass needs a positive result and an observed state
      *transition*; every proxy for those was wrong at least once.

      One incidental positive: `add_queue` refused `c5.large` on this
      all-Graviton cluster -- *"new queue architecture does not match this
      cluster ... Cluster: arm64, Requested: c5.large (x86_64)"* -- so the
      architecture guard is confirmed through the remote transport.

      **Leftover state, since resolved:** the `r4probe` queue remained on
      `stageb` and in `configs/stageb.yaml`. It went with the cluster at
      teardown; nothing carries it now.

- [x] **R5. PASS 2026-08-26 -- the tier separation, evidenced against the
      shipped policies.** The question is whether a tier can exceed its own
      IAM, and the instrument matters more than the answer.

      **CloudTrail was the wrong instrument, and reaching for it first cost
      time.** A denial only appears in a trail if something attempts it, and
      nothing does: each tier's roles trust only `lambda.amazonaws.com`, and
      each tier's tools make only the calls its own policy grants. So there
      is no natural denial to catch. The two options that suggested
      themselves -- wait for a denial during some future build, or narrow a
      policy to provoke one and restore it -- were both bad. The first
      defers the evidence indefinitely; the second tests a configuration
      that is **not the one deployed**, which is the same class of error as
      a fake written from the caller instead of the contract.

      `iam:SimulatePrincipalPolicy` is the right instrument and was
      available the whole time: it runs IAM's own evaluator over the **real
      attached policies of the real roles**, mutating nothing. 17 actions x
      5 tiers, spanning read / build / destroy / self-modify / privilege
      escalation:

          action                       read-only  fleet  stack-mut  node  router
          cloudformation:DescribeStacks    ALLOW   ALLOW     ALLOW  ALLOW      .
          cloudformation:CreateStack           .       .     ALLOW  ALLOW      .
          cloudformation:DeleteStack           .       .     ALLOW  ALLOW      .
          ec2:RunInstances                     .   ALLOW     ALLOW  ALLOW      .
          ec2:TerminateInstances               .   ALLOW     ALLOW  ALLOW      .
          iam:CreateRole / PutRolePolicy       .       .         .      .      .
          iam:CreateUser / CreateAccessKey     .       .         .      .      .
          iam:AttachUserPolicy                 .       .         .      .      .
          sts:AssumeRole                       .       .         .      .      .
          lambda:UpdateFunctionCode            .       .         .      .      .
          s3:DeleteBucket                      .       .         .      .      .
          organizations:LeaveOrganization      .       .         .      .      .

      **31 of 31 assertions pass.** The escalation ladder holds in the
      intended shape -- read-only reads and cannot build or destroy;
      fleet-toggle moves instances but cannot touch a stack; only the two
      stack-mutation tiers reach CloudFormation -- and **no tier can create
      a user, mint an access key, attach a user policy, assume a role, or
      rewrite its own code**, which is the property that actually bounds the
      blast radius.

      **The router's row is the interesting one, and reading it carelessly
      would invert its meaning.** `lambda:InvokeFunction` simulates as
      *denied* for the router against `*` -- which is the router's entire
      job. The grant is ARN-scoped, so a wildcard simulation is the wrong
      question. Resolved per resource:

          -> read-only / fleet-toggle / stack-mutation / -node   allowed
          -> authorizer                                     implicitDeny
          -> register                                       implicitDeny
          -> an unrelated function                          implicitDeny

      So the router invokes exactly the four handler tiers and nothing else.
      **A wildcard grant would have simulated ALLOW against `*` and read as
      a cleaner pass while being strictly worse** -- the denial in the first
      table is the evidence of scoping, not a gap in it.

      **An ephemeral CloudTrail corroborated it at runtime**, which the
      simulation alone cannot: a policy that permits an action is not proof
      the action is the one actually taken. Lambda data events scoped to
      the five function ARNs (they are invisible in Event History, which
      carries management events only) captured the full chain:

          API Gateway (AWSService)          -> router
          assumed-role/...-mcp-router-role  -> fleet-toggle
          assumed-role/...-mcp-router-role  -> stack-mutation-node
          errorCode on all four records     : none (allowed)

      So the caller reaching each handler is the **router's own role**, not
      the gateway and not a handler reaching sideways -- the topology the
      IAM describes is the topology that runs. Trail and bucket deleted
      afterward; cost was a fraction of a cent (data events $0.000001 each,
      management events free).

      **What this does not settle: item 4.7.** The summary table lists R5 as
      certifying it, and that holds only for the first half — a tier cannot
      exceed its own IAM. 4.7's specific assertion, that a
      `finalize_cluster_teardown` call emits **no**
      `DeleteCluster`/`DeleteStack` event, was never measured: this trail
      was scoped to Lambda data events on the five function ARNs, and the
      finalize that ran at teardown was not watched. Still open.

### Notes carried from session 53

* The per-tier IAM is certified by having failed loudly four times; a
  stubbed test cannot produce that evidence.
* Deploying does not re-certify `_LOCAL_ONLY` tools -- L2 and L4 are
  local-only forever.
* Teardown from the store alone works (`exit_code 0`, no orphans) and needs
  no local files, so Stage C can tear the cluster down even if Stage B's
  machine is gone.

## Item-by-item audit (2026-08-25, corrected again 2026-08-26)

Results below are **recovered from the session transcript**, where the
harness printed a `PASS`/`FAIL` line per item. An earlier version of this
audit claimed Phases 2 and 3 were never run; that was wrong. They were run,
the verdicts were reported in conversation, and nothing was written here --
which is a documentation failure, not a testing one. The verdicts are now
recorded, with the transcript line they came from.

### Phase 0 — 18/18

`0.1`-`0.15`, `0.17`, `0.18` each have a recorded `PASS`. `0.16` (token TTL)
has no harness line by design: expiry enforcement is covered by `0.13`,
which asserts the message names both age and TTL, without 15 minutes of
wall clock.

### Phase 1 — 15 of 15 (was 14 of 15 until L1 and L2 landed)

| Item | Verdict | Evidence |
|---|---|---|
| 1.0 | PASS | publish half verified on `certify`; kick-off blocked by the `sys.exit` defect, then driven on a **deployed handler** in session 53 (`nodecert` launched a real stack and published its record) and on the local path by **task L1** (`stageb`, `kicked_off=true`) |
| 1.1, 1.2 | PASS | recorded VERIFIED LIVE 2026-08-24 |
| 1.3 | PASS | all six health checks pass, Slurm included |
| 1.4 | PASS | was broken over MCP, fixed, verified live on `osiris` |
| 1.5 | PASS | default resolves to LoginNode when one exists |
| 1.6 | PASS | diagnose returned the head IP |
| 1.7 | PASS | list flips, token differs, health unchanged |
| 1.8 | PASS | no key material in the response |
| 1.9 | PASS | tunnel opened and port bound; stopped and port released |
| 1.10 | PASS | health, listing and queues all resolved from S3 |
| 1.11 | PASS | invisible from the wrong region, degrades naming it |
| 1.12 | PASS | `SSH_CONNECTION` loopback -- traversed SSM |
| 1.13 | PASS | warns by name, falls back to direct ssh |
| 1.14 | PASS | task L2 on `stageb` (2026-08-26): the PID in the file held the port, stop killed it and released the port |

**1.9 was run** on the monitoring-enabled `certify`, contrary to the older
"skipped" note above.

**1.14 was the notable gap and is now closed.** The defect it exists to
catch was found by other means (the `pgrep` matched the IP, while over SSM
the command line carries the instance ID), so the item itself went unrun
until task L2 — which is worth keeping in view, because a fix landing
before its own test is exactly how a fix goes unverified.

### Phase 2 — 14 of 14 sub-cases (was 13 of 14 until L3 landed)

`2.1`-`2.7` PASS. `2.8` and `2.8b` PASS after a fix -- the first run
recorded `FAIL 2.8 ... IT SUCCEEDED`, which is the case where the harness
was asserting on a comment rather than a value. `2.9` PASS (stale ETag
rejected). `2.10` PASS (says *deleted*, not merely changed) and `2.10b`
PASS (a denial surfaces as itself). **`2.11` PASS, both rounds**
(`2.11-r1`, `2.11-r2`: a CLI edit is recognized as ahead and the MCP edit
after it succeeds) -- it had genuinely been absent from the runner until
that was challenged, then it was added and run. `2.12` PASS.

**`2.12b` was the one outstanding FAIL, and is now PASS.** The original
verdict was *"local edit applied=True, warned about the store=False"*. The
fix landed afterward (`manage_pcluster_queue.py`'s `_cluster_store()` now
warns, naming the cause, on both the missing-region and exception paths,
pinned by `TestAnUnreachableStoreIsAnnounced`) and sat fixed-but-unverified
until **task L3 re-ran it live on 2026-08-26**: credentials pointed at
/dev/null, the warning named `NoCredentialsError`, the edit applied
locally, and the store object's ETag was byte-identical either side —
which is what proves the mirror genuinely did not happen.

### Phase 3 — 8 of 8 (3.8 was scoped-only until L4 ran its blocking path)

`3.1` PASS (returned in 3.7s, lock released). `3.2` PASS (refused in 0.7s
naming the owner, server alive). `3.3` PASS (7.1s, no temp configs left).
`3.4` PASS (edit refused during `UPDATE_IN_PROGRESS`, naming the status).
`3.5` PASS (`s3://` rejected with a pointer). `3.6` PASS (explicit path
errors, no silent fallback). `3.7` PASS (2.3s, Slurm check passes).

**`3.8` was PASS as scoped only, and is now PASS in full.** The original
verdict was *"registered locally and absent from `TOOL_TIERS` (blocking
path not run)"* — the local-only placement certified, the ~30-minute
blocking execution not. **Task L4 ran it on 2026-08-26**: `stageb` reached
`UPDATE_COMPLETE` with the fleet `RUNNING` and the new `l4q` partition live
in `sinfo`, at **~30.5 minutes** of wall clock against Lambda's 900s
ceiling. That number is also what closes the 900s gap this bullet used to
point at; R4 measured the other direction (20.0s for the slowest remote
call).

### Phase 4 — 7 of 8

`4.1`-`4.5`, `4.8` PASS on `osiris` (stack gone at 18m26s, finalize 5.7s,
no orphans; 4.5 found two messaging defects, both fixed). **`4.6` PASS**
live on `stageb`'s teardown, 2026-08-26 (task L5). **`4.7` still needs
CloudTrail** — see the note under R5 for why R5's trail does not cover it.

### What actually remains

Everything below was open on 2026-08-25; all but 4.7 and the browser
session closed on 2026-08-26. Kept with its outcome rather than deleted,
because the list is the record of what the certification campaign was for.

1. ~~**Auth** -- API Gateway + Cognito, never stood up.~~ **Closed** by R2
   and R3: the gateway had to be *written* (none existed), then rebuilt as
   REST. **A Claude web session driving it is still unrun** — the one
   wholly unexercised surface.
2. ~~**1.14** -- never run.~~ **Closed** by L2.
3. ~~**2.12b** -- fixed, not re-verified.~~ **Closed** by L3.
4. ~~**3.8's blocking path** and the **900s ceiling** as observed
   behavior.~~ **Closed** by L4 (30.5 min blocking) and R4 (20.0s slowest
   remote call).
5. ~~**4.6** live~~ **closed** by L5; **4.7 via CloudTrail is still open.**
6. ~~`deploy.py` has no production caller.~~ **Closed 2026-08-25** by
   `deploy_mcp.py` (task A1), which also gained `--teardown` on 2026-08-26,
   so the transport now has a removal path as well as a deployment one.
   `--teardown` has been exercised **only against an empty account**: it
   reports absence correctly and is idempotent, but the delete paths have
   never run against live resources through that code.

Both survivors need infrastructure that no longer exists: 4.7 needs a
cluster and a trail, the browser session needs the transport redeployed.

**Rule: a verdict is written into this file in the session it is produced.**
Everything above existed only in a transcript, and recovering it took
mining 15 MB of JSONL. The next one may not be recoverable at all.

## Still genuinely open (rewritten 2026-08-25 after session 53; re-marked 2026-08-26 after session 54)

**Superseded: handlers have now run.** All five tiers were deployed and
driven against AWS, a real cluster was built through the image tier, and a
teardown ran entirely off the shared store. What that settled, and what it
did not, is below. The pre-session text is kept after it for the record.

**Certified by the deployment:**

- **Per-tier routing.** A `preview_cluster_delete` sent to `stack-mutation`
  was refused with "not served by tier 'stack-mutation' -- the router
  forwarded it to the wrong handler", which is the handler's own guard
  rather than a stub's.
- **`_LOCAL_ONLY`.** The excluded tools are absent from a deployed tier's
  `tools/list`.
- **`ssh_available=False`.** `check_cluster_health` on the read-only tier
  returned `skip` with "SSH unavailable on this transport" for the four
  ssh-dependent checks, and real results for the two that do not need it.
- **The per-tier IAM grants.** Certified the hard way: every gap surfaced
  as a real `AccessDenied` naming the assumed role, and each was closed and
  re-driven. A stubbed test cannot produce that evidence.
- **1.0 / the create path.** `create_cluster` on a deployed handler kicked
  off a real stack and published its record; the CDK bridge ran under Node
  inside the Lambda (its EOL banner is in the log, which is what proves the
  container's whole reason for existing).
- **Phase 4 off the store.** `delete_cluster` completed with `exit_code 0`
  and no orphans for a cluster whose local files did not exist on the
  machine running it.

**The 2026-08-25 list, with what session 54 did to each of them:**

1. ~~**Auth — the only untouched surface.**~~ **Mostly closed 2026-08-26.**
   API Gateway and Cognito were never stood up — R2 discovered there was no
   provisioning code to stand them up *with*, wrote it, and R3 then replaced
   the HTTP API with a REST one. Both rules are now live-verified with real
   Cognito tokens: an access token carries `client_id` and **no `aud`**, an
   ID token is refused, and every failure is a 401 carrying
   `WWW-Authenticate`. **What remains is a Claude web session** driving the
   transport, which is still unrun and is the entire point of it.
2. ~~**The 900s ceiling as observed behavior.**~~ **Closed** in both
   directions: L4 measured the blocking path at ~30.5 min (1830s) and R4
   drove `stop_fleet` -> `apply_cluster_update` -> `start_fleet` end to end
   on deployed handlers, slowest call **20.0s of 900s**.
3. ~~**4.6 -- token non-interchangeability, live.**~~ **Closed** by L5 at
   `stageb`'s teardown, with both tokens spent on the wrong tool.
4. **4.7 -- CloudTrail proof that a finalize issues no delete. Still open.**
   Partly superseded twice over: the AccessDenied failures above, and R5's
   `iam:SimulatePrincipalPolicy` sweep plus its ephemeral trail, are the
   ceiling evidence. Nothing has confirmed the *absence* of a
   `DeleteCluster`/`DeleteStack` event from a finalize call.
5. **`FunctionError`'s handler-ran-and-failed half. Still open.** The
   router's could-not-invoke path is covered; a handler that runs and raises
   is not.
6. ~~**The deployment machinery has no production caller.**~~ **Closed
   2026-08-25**: `deploy_mcp.py` is that caller, pinned by
   `TestTheDeploymentHasAProductionCaller`, and it got its first production
   use in R1. It was true that every session-53 deploy ran from a scratchpad
   script, which is how the update path's `ResourceConflictException`
   survived as long as it did. **Removal is no longer a scratchpad job
   either** — `--teardown` takes the gateway, the functions, the IAM and the
   pool, in that order, and deliberately leaves the permissions boundary
   (`MCPDeployPolicy` denies deleting it). It has been exercised **only
   against an empty account**.

**Two recorded limitations that are not on this list and must not be read
as closed:**

- **The CLI IAM hardening is not live-verified.** `ClusterNode-Deny` and
  the `ClusterRoleBoundary` were derived from what the shipped documents
  grant, so neither *should* change behavior — but a wrong deny or a narrow
  ceiling surfaces at node bootstrap, roughly 20 minutes into a build, and
  the whole test suite is blind to it.
- **`deploy_mcp.py --teardown` has been run only against an empty account.**
  It reports absence correctly and is idempotent; the delete paths have not
  run against live resources through that code. Only the
  domain-before-pool ordering is backed by a live failure.

### Pre-session text, kept for the record

*Everything in this subsection was true on 2026-08-24 and is false now — it
is kept because it is the state each finding above was made against. Do not
quote it as current. The two claims it opens with, "nothing has run on a
deployed handler" and "the MCP create path is still unrun", were both
retired the following day.*

**Nothing has run on a deployed handler.** Section A above is the ceiling on
everything here: the local stdio server is the only thing these items can
drive, so per-tier routing, `_LOCAL_ONLY`, the 900s ceiling,
`ssh_available=False`, the per-tier IAM grants and Cognito auth all remain
uncertified. A deployment was attempted on 2026-08-24 and abandoned at
`CreateFunction`; the artifact does build and fit (139 MB pruned against a
250 MB ceiling) and the operator's credentials do allow `CreateFunction` and
`PassRole`. **The 2026-08-24 live run does not change this.** A real cluster
was built and Phase 1 was executed against it, but every call was made by the
**local stdio server** — `remote=False`, `ssh_available=True`, no tier
routing, no API Gateway, no Cognito. Three of those items found defects, all
of them in tool bodies that a deployed handler would run identically; none of
them touched the transport. Nothing has run on a deployed handler.

**The MCP create path is still unrun.** `osiris` was built from the CLI, so
1.0 — `create_cluster`'s kick-off and `_KICKED_OFF`'s publish to the store —
remains uncertified, along with everything in Phase 1 that depends on the
store having been written by that path (1.10, 1.11) and all of Phase 3.
Phase 4 was certified on `osiris`'s own teardown (4.1-4.5, 4.8) and Phase 2
only incidentally (2.5, 2.12).

**Phase 0 is done — all 18 passed on 2026-08-25**, with
`core_create_cluster` never reached. See that section for the guarded
harness and for the two checklist corrections the run produced.
