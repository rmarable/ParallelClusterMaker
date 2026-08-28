# The MCP server: what shaped it

Installing, connecting and removing the MCP server is in
[README.md](../README.md#mcp-server). This file is the other half: why the
transport is built the way it is, what it refuses to do and why, and the
measured numbers behind those decisions.

You do not need any of this to use it. Read it when something surprises
you — a call that times out while the work succeeds, a tool that exists
locally but not remotely, a permission that is missing on purpose.

## Two servers, one codebase

**Locally**, a stdio server exposing all 21 tools, calling the same
functions the CLI does.

**Remotely**, seven Lambda functions behind API Gateway and Cognito,
carrying 18 of the 21 — 14 unless the container tier is deployed.

The three that never leave the local server are `rotate_cluster_key`,
`manage_grafana_tunnel` and `apply_queue_config`. The first two need the
cluster's SSH private key, which deliberately never reaches the remote
transport. The third is excluded for a different reason, below.

## The 29-second ceiling is the binding constraint

Not Lambda's 900 seconds. API Gateway's REST integration timeout is 29
seconds and that is already the maximum the service allows — raising it
needs a service quota increase, not a configuration change.

Everything about the tool surface follows from this.

**No tool may block on a cluster operation.** Past 900 seconds a function
is killed mid-mutation, with the fleet stopped, an update in flight and
the cluster lock held by a dead process. But the caller is gone long
before that, at 29 seconds, and a caller that gave up is not the same as
an operation that stopped.

**So operations that would block are decomposed, never dropped.**
`apply_queue_config` is local-only because it performs stop → update →
start as one call; a remote caller drives `stop_fleet`,
`apply_cluster_update` and `start_fleet` separately. The capability
survives; the blocking call does not.

**Measured, not estimated.** `apply_cluster_update` took **41,992 ms** to
add one queue. The caller failed at 29.4 seconds while the update
completed — so a naive retry would have submitted a second update against
a stack already updating.

**`create_cluster` does not fit and cannot be made to.** It measured
**43.6 seconds**, of which roughly 39 are ParallelCluster's own CDK
synthesis of the CloudFormation template. That is not our work to
decompose. The call returns a timeout while the build proceeds normally.

Two consequences worth knowing:

* The vars-file guard asks whether a cluster is **already building** before
  concluding that a leftover vars file means a dead run. Without that
  question, "a vars file already exists" reads as wreckage to clean up —
  and that inference was once drawn against a live build.
* Because the caller may never see the result, every build failure after
  the first AWS resource is created records itself. See below.

## Long operations finish themselves

A teardown takes 15–20 minutes and a build 20–45, so neither can be one
call. The build and the teardown solve that differently, on purpose.

**A teardown completes without you.** `delete_cluster` fires an
asynchronous invocation at its own function, which polls the stack and
runs the cleanup once it is gone.

That design exists because the obvious alternative does not work. Telling
the caller to poll and come back assumes the caller has a timer, and a
conversational agent does not — told to wait fifteen minutes it correctly
says "ping me and I'll check" and stops. That was observed live, from an
agent that had read the instruction, quoted it back, and refused the
unsafe shortcut. The gap was never comprehension, so no wording could
close it.

**The poll loop is the risk, and it is bounded three ways**: a wall-clock
deadline checked before every re-invocation, a maximum attempt count so a
misbehaving clock cannot defeat the deadline, and a terminal-state check.
An unreadable describe means *keep waiting*, never *it is gone* —
finalizing on a failed API call would destroy the credentials of a cluster
that may still be running. `DELETE_FAILED` stops rather than finalizing:
the stack is still there, someone has to look at it, and stripping the IAM
strips what they would look with.

The decision logic lives in `mcp_server/completion.py`, which touches no
AWS and no clock, so every CloudFormation status can be tested
exhaustively. A live teardown is the worst possible test of a bound: it
reaches a terminal state in fifteen minutes and never visits the cases
that matter.

**An asynchronous invocation has no caller**, so silence would be
indistinguishable from success. Every terminal outcome is written to the
function's retained log group and published to the cluster's SNS topic.

**A build does not auto-complete, and that is deliberate.** Its remaining
steps stage files and send a summary; the cluster is usable without them,
so there is nothing to gain by taking the human out of it.
`finalize_cluster_build` does them on demand.

## A failure the caller never sees is still written down

The message was never the problem. Every failure path builds a real one,
and it names the exception *type* as well as its text — because a
ParallelCluster API exception's string representation is empty, so a
message built only from the text is a sentence with nothing in it.

The problem was that nobody received it. The caller is disconnected
roughly fourteen seconds before the return value exists, and no failure
path recorded anything, so CloudWatch was the only trace — and diagnosing
a plain `AccessDenied` that way cost two rounds.

Every failure after the first AWS mutation now publishes to
`vars/<cluster>.build-failure.json`, and `get_build_status` reads it back.
Four properties, each of which a plausible edit would break:

* **The key is under `vars/`.** The tier IAM policies grant `vars/*` and
  `configs/*` and nothing else, so a `builds/` prefix reads perfectly in a
  checkout and is `AccessDenied` on every deployed call.
* **It is not the cluster record.** A failed build leaves no cluster;
  writing the record would make `list_clusters` report one that does not
  exist.
* **Recording the failure never raises.** The caller is already on an
  error path, and swapping a real diagnosis for a bookkeeping error is
  strictly worse.
* **An unreachable store is not "no failure".** It reports
  `store_reachable: false`. Absence of evidence is not evidence of
  success, which is the whole thing this replaces.

Failures *before* the first AWS mutation are deliberately not recorded:
nothing was created, and the call returns while the caller still holds the
connection.

## Four handler tiers, split by blast radius

The internet-facing router executes no tool logic and holds exactly one
permission — invoke the four handlers. Everything else is behind it:

| Tier | Holds |
|---|---|
| `read-only` | Reads state. Writes nothing, anywhere. |
| `fleet-toggle` | Starts and stops compute fleets. Cannot mutate a stack. |
| `stack-mutation` | Queue edits, cluster updates, teardown. |
| `stack-mutation-node` | Cluster creation. Ships as a container image. |

**A tier's policy has a floor as well as a ceiling.** Every guard here
originally asked whether a tier could exceed its blast radius; none asked
whether it could reach its own, and two could not. `fleet-toggle` had no
grant for the S3 and DynamoDB reads that `update-compute-fleet` performs,
so it failed against every real cluster for its entire life. And no tier
granted any `elasticloadbalancing` action, so `describe-cluster` failed
against every cluster with a login node pool, which sits behind a network
load balancer. Both directions are now pinned by tests.

**`read-only` really is read-only.** `add_queue` and `remove_queue` write
a config object, so they moved to `stack-mutation` — they mutate no
CloudFormation stack, which is true and beside the point. A tier named
read-only whose policy carries `s3:PutObject` misrepresents itself to
whoever reads it next. The cost is accepted: a queue edit now carries more
privilege than the edit needs.

**Every MCP role is created under a permissions boundary**, and the deploy
tool is deliberately unable to grant itself permissions — a deploy tool
that can widen its own access has no ceiling. It preflights instead,
naming missing actions before the first mutation.

## One deployment serves one region

The shared state store is per account and region, and each tier's policy
names exactly one bucket. So a server answers only for its own region.

This is a design choice, not an oversight. Cross-region discovery would
mean scanning every region's bucket on every listing, and supporting it
properly would mean widening every tier's IAM to match. Multiple regions
means one deployment per region.

The consequence to watch for: `deploy_mcp.py --region` reads `AWS_REGION`
or defaults to `us-east-1`. It reads neither `AWS_DEFAULT_REGION` nor your
profile's configured region, so a transport can land somewhere your
clusters are not.

## Known limits

**A remote `create_cluster` returns a timeout, not a result.** The build
proceeds and succeeds; `list_clusters` shows it and `get_build_status`
explains any failure. But the call itself cannot report either.

**Login node root volumes cannot be sized or encrypted.** ParallelCluster
exposes no configuration key for it, so they always use the AMI default.
This is an upstream limit, not a toolkit one.

## What has actually been exercised

Four clusters built and destroyed end to end through a browser-based
client, including a teardown that removed an S3 bucket, five IAM policies,
an IAM role, an SNS topic, an SSH key secret, an EC2 keypair and two state
objects with no second call from anyone. The transport itself has been
deployed and completely torn down, which exercised the forced ECR
repository delete, the Cognito domain-before-pool ordering, and policy
version pruning.

Not exercised: the poll loop's bounds against a genuinely slow teardown.
The stacks torn down so far reached `DELETE_COMPLETE` in about three
minutes, four attempts into a sixty-attempt ceiling. Those bounds are
covered exhaustively by tests instead, which is the better evidence for
that particular half.

3,386 tests, none of which reach AWS.
