# MCP support: build and operate clusters from a conversation

ParallelClusterMaker now exposes its toolkit over the Model Context Protocol.
An MCP client can list clusters, check health, diagnose failures, read cost
reports, start and stop compute fleets, edit queues, and build and tear down
clusters — without a terminal, a checkout, or AWS credentials on the machine
driving it.

Two ways to run it. **Locally**, a stdio server exposing all 21 tools, calling
the same code the CLI does. **Remotely**, seven Lambda functions behind API
Gateway and Cognito, so a browser-based client can reach it. The remote surface
carries 18 of the 21 tools — 14 unless the container tier is deployed.

## Getting started

```bash
# local
claude mcp add parallelclustermaker -- /path/to/.venv/bin/python /path/to/mcp_server/server.py

# remote
./deploy_mcp.py --bootstrap --create-user you@example.com
./deploy_mcp.py --tier stack-mutation-node     # adds cluster creation
```

`--bootstrap` deploys six of the seven tiers. The seventh ships as a container
image because it needs a runtime, and leaving it out means `create_cluster`,
`apply_cluster_update`, `preview_cluster_config` and `finalize_cluster_build`
are absent — you can inspect and operate clusters but not create or modify
them. `./deploy_mcp.py --teardown` removes all of it.

Check the region before deploying. `--region` reads `AWS_REGION` or defaults to
`us-east-1`; it reads neither `AWS_DEFAULT_REGION` nor your profile's region,
and one deployment serves exactly one region by design.

## A teardown finishes itself

`delete_cluster` returns as soon as CloudFormation accepts the delete, because
no tool may block for the 15–20 minutes a teardown takes. That used to leave the
IAM policies, S3 bucket, SSH key secret, SNS topic and cluster record standing,
with a second call needed to remove them — and nothing reliably made it. An
agent told to poll and come back has no timer, so it correctly says "ping me"
and stops. That was observed live from an agent that had read the instruction
and quoted it back, which is what settled that no wording closes the gap.

So the server closes it. `delete_cluster` fires an asynchronous invocation at
its own function, which polls and runs the cleanup once the stack is gone. Read
`auto_finalize_started` in the response: when true, the teardown is handled.

## A failed build says why

A remote `create_cluster` measures 43.6s against API Gateway's 29-second
integration timeout, so the caller is disconnected before the return value
exists and never sees the error it carries. Every failure after the first AWS
mutation therefore records itself in the shared state store, and
`get_build_status` reads it back — the stage that failed, the reason, and when.

Three outcomes stay distinguishable, including the awkward one: a store that
cannot be read reports `store_reachable: false` rather than "no failure found",
because absence of evidence is not evidence of success.

## The hard parts, since they shaped the design

**API Gateway's 29-second integration timeout is the binding constraint, not
Lambda's 900.** It is already the REST maximum. So no tool may block on a
cluster operation, and the ones that would are decomposed rather than dropped —
a remote caller drives `stop_fleet` → `apply_cluster_update` → `start_fleet`
instead of one blocking call. Measured: `apply_cluster_update` took 41,992 ms to
add a queue, and the caller was cut off at 29.4s while the update completed.

**`create_cluster` does not fit either**, at 43.6s measured — about 39 of those
seconds are ParallelCluster's own CDK synthesis, so no decomposition of our work
helps. The caller times out while the build succeeds. This is why the failure
record above exists, and why the tool asks whether a cluster is *already
building* before concluding that a leftover vars file means a dead run.

**Four handler tiers, split by IAM blast radius.** The internet-facing router
executes no tool logic and holds exactly one permission: invoke the four
handlers. Read-only tools cannot write anything. Fleet toggles cannot mutate a
stack. Every MCP role is created under a permissions boundary, and the deploy
tool is deliberately unable to grant itself permissions — it preflights and
names what is missing instead.

**One server manages one region.** The record store is per account and region,
and each tier's policy names exactly one bucket. Multi-region means one
deployment per region, never wider IAM.

## Known gaps

Login node root volumes cannot be sized or encrypted through this toolkit —
ParallelCluster exposes no key for it. A remote `create_cluster` still exceeds
the gateway ceiling as described above; the build succeeds and
`get_build_status` explains any failure, but the call itself returns a timeout
rather than a result.

## Verified

Four clusters built and destroyed end to end through a browser-based client,
including a teardown that removed a bucket, five IAM policies, a role, an SNS
topic, an SSH key secret, a keypair and two store objects with no second call.
The transport itself has been deployed and torn down in full. 3,386 tests, none
of which reach AWS.
