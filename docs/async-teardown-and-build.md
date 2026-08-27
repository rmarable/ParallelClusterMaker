# Async completion for create and delete

Status: **implemented 2026-08-27 for teardowns.** Builds still finalize
manually, by the decision below. Not yet exercised against a real teardown --
the next cluster destroyed is its first live run. Written 2026-08-27 (session 57) after two
live builds and two live teardowns through the claude.ai connector.

## The problem, stated exactly

Two operations outlive the call that starts them, and the caller is asked to
come back later:

| Operation | Duration | What the caller must do |
|---|---|---|
| `create_cluster` | 20-45 min build, **43.6s just to submit** | poll, then `finalize_cluster_build` |
| `delete_cluster` | 15-20 min | poll, then `finalize_cluster_teardown` |

For `create_cluster` there is a second, sharper problem: the *submission*
alone measured **43,615 ms** against API Gateway's **29,000 ms** integration
timeout, which is already the REST maximum. The caller is cut off while the
Lambda runs on and the build succeeds.

Neither is a wording problem, and both have now been attacked with wording
and found wanting:

- `delete_cluster`'s response was given `teardown_complete: false` and a
  `next_step` naming the finishing call. The agent read it, quoted it back,
  correctly refused to re-issue a delete -- **and still stopped**, saying
  "ping me and I'll poll".
- `finalize_cluster_teardown`'s token was removed, so the follow-up is one
  untokened call rather than a re-preview. That made the second step
  cheaper. It did not remove the second step.

**The agent stopped because it has no timer.** A conversational turn cannot
span twenty minutes, and "ping me" is the correct answer to being asked to.
No tool description fixes that, because the constraint is not comprehension.

## What "one command" requires

The server has to finish the job. Nothing in the request path can wait, so
the completion must be driven by something outside it.

## Option A -- self-scheduling Lambda (recommended)

`delete_cluster` and `create_cluster` each end by asynchronously invoking a
*completion* handler with the cluster name and a deadline. That handler
polls `describe_cluster`; if the terminal state is not reached it
re-invokes itself asynchronously after a sleep, and when it is reached it
runs the existing `finalize_only` path.

```
delete_cluster  --(Event invoke)-->  completion handler
                                       |  not terminal yet
                                       +--(Event invoke, after sleep)--> itself
                                       |  stack gone
                                       +--> core_delete_cluster(finalize_only=True)
```

Why this one:

- **Almost all the primitives exist.** Both finalize paths are already
  written, already gated on a confirmed terminal state, and already
  non-blocking; the completion handler is a loop around code that exists.
  **One IAM statement is missing, and the first draft of this document got
  it wrong.** `MCPStackMutation` does grant `lambda:InvokeFunction`, but
  scoped to `function:parallelcluster-*` and `function:pcluster-*` -- those
  are PCluster's own Lambdas, created by a cluster stack, not the
  `pclustermaker-mcp-*` tiers. So no tier can invoke itself or a sibling
  today. The addition is one statement on the tier that starts the work,
  scoped to the completion handler's own ARN; `MCPRouterLambda` already
  does exactly this for the four handlers, so the shape is settled.
- **`InvocationType="Event"` returns in milliseconds**, so the 29s ceiling
  stops being relevant to the tool that starts the work -- which also fixes
  `create_cluster`'s 43.6s submission, not just the teardown.
- **No new service.** No EventBridge rule to create and tear down per
  cluster, no Step Functions state machine to deploy, no IAM category
  beyond what the tier has.

Costs, stated rather than discovered later:

- **A self-re-invoking Lambda is a loop that must be bounded.** It needs a
  hard deadline in the payload, checked before every re-invoke, and a
  maximum iteration count, so a cluster stuck in `DELETE_IN_PROGRESS`
  cannot re-invoke forever. This is the single largest risk in the design
  and the thing to test first.
- **Sleeping in a Lambda is billed.** Poll every 60s from a fresh
  invocation rather than sleeping 60s inside one; each iteration should be
  milliseconds of work.
- **Failures become invisible.** Today a failed finalize is reported to
  whoever called it. Asynchronously there is nobody to report to, so the
  handler must write its outcome somewhere the operator will find it --
  the cluster's own retained CloudWatch log group, and the SNS topic that
  already exists for exactly this.

## Option A-lite -- notify instead of finalize

The lightest thing that removes the worst part of today's flow, which is
not the second call but *not knowing when to make it*.

The completion handler polls as in Option A, and on reaching the terminal
state publishes to `sns_alerts_<cluster>` -- a topic that already exists,
is already created per cluster, and is already deleted on teardown -- rather
than finalizing. The operator gets told the stack is gone and makes the one
untokened call themselves.

This changes the safety posture **not at all**: every destruction still
happens in a call a human makes. It needs the same bounded loop and the
same IAM addition, so it is not less work than Option A -- it is the same
work with the last step left to a person. Worth taking if the posture
question below is answered "keep the human", because then it is the whole
answer rather than a stepping stone.

## Option B -- EventBridge on the CloudFormation state change

A rule matching `CloudFormation Stack Status Change` for
`DELETE_COMPLETE` / `CREATE_COMPLETE` on stacks this toolkit owns, targeting
the finalize handler.

Better in principle: event-driven, no polling, no loop to bound. Worse in
practice here:

- It needs a rule and target created at deploy time and a new IAM category
  (`events:*`) on a tier that currently has none.
- CloudFormation's stack-status events are account-wide; filtering to this
  toolkit's stacks means matching on tags the event does not carry, so the
  handler must re-check anyway.
- `DELETE_COMPLETE` for a stack that no longer exists is delivered on a
  best-effort basis, and a missed event leaves a half-torn-down cluster
  with nothing watching. Polling degrades more honestly.

Worth revisiting if the self-invoke loop proves hard to bound.

## Option C -- Step Functions

Correct, and disproportionate. It introduces a service, a state machine
definition, its own IAM and its own teardown, for a wait loop with two
states.

## The decision that is not mine

**A self-finalizing teardown destroys IAM roles, the S3 bucket and the SSH
key with no human in the loop at the moment it happens.** Today those
deletions occur in a call the operator makes. That is a real change in
posture, not just plumbing, and it should be an explicit choice:

- The operator already confirmed the teardown via `delete_cluster`'s token,
  and the resources destroyed are exactly those that call promised to
  destroy. Nothing new is authorized.
- But the window between "I asked for this" and "it happened" grows from
  seconds to twenty minutes, and during it the operator cannot easily
  intervene.

**Decided: auto-finalize teardowns, not builds.** Chosen for the operator's
sake -- one command is what a person asking to destroy a cluster expects,
and the second call was friction with no decision in it. The window between
asking and happening grows to twenty minutes; that is accepted, because
`delete_cluster`'s token already authorized exactly these deletions and
nothing new becomes reachable.

The reasoning for the split: A teardown's remaining steps are cleanup of
things already doomed; a build's remaining steps stage files and send a
summary, and leaving those to an explicit call costs nothing because the
cluster is already usable.

## Sequencing

1. Bound the loop and prove it: a completion handler that polls, re-invokes,
   and stops -- against a fake describe, then against a real teardown.
2. Wire `delete_cluster` to start it. Teardown first: it is the one that
   leaves billable resources behind, and its failure mode is a leak rather
   than a broken cluster.
3. Only then consider `create_cluster`, where the async invoke also solves
   the 29s submission problem but where a lost failure is worse -- an IAM
   error during setup currently reaches the caller and would stop doing so.
4. Leave `finalize_cluster_teardown` and `finalize_cluster_build` in place
   as manual tools regardless. They are how an operator recovers when the
   automatic path does not run, and they cost nothing to keep.

## What this does not change

`create_cluster` will still take 20-45 minutes and `delete_cluster` 15-20.
Async completion removes the *second command*, not the wait. A caller asking
"is it done yet" still gets an honest "no".


## As built (2026-08-27)

Option A, teardowns only.

| Piece | Where |
|---|---|
| The decision, pure | `mcp_server/completion.py` -- no AWS, no clock |
| The AWS half | `mcp_server/completion_runner.py` |
| Dispatch | `handlers/base.py`, on an explicit `_pcm_completion` marker |
| Kickoff | `_start_teardown_completion` in `tools.py` |
| IAM | `MCPStackMutation`'s `InvokeItselfToFinishATeardown` |

**The split is the point.** `decide()` takes a status, an attempt count and
two timestamps and returns finalize/retry/give_up. It is the part that can
run up a bill or never stop, and it is the part a live test exercises worst:
a real teardown reaches a terminal state in fifteen minutes and never visits
the cases that matter. Being pure, it is swept exhaustively over every
CloudFormation status.

Three bounds, none load-bearing alone:

* a wall-clock deadline, checked before every re-invoke;
* a maximum attempt count, so a misbehaving clock cannot defeat the
  deadline;
* a terminal-state check, where an unreadable describe means *keep waiting*
  and never *it is gone* -- `_confirm_stack_is_gone`'s rule, because
  finalizing on a failed describe destroys the credentials of a cluster
  that may still be running.

`DELETE_FAILED` gives up rather than finalizing: the stack is still there
and an operator has to look at it, and stripping the IAM is stripping what
they would look with.

**Failure reaches somebody.** An Event invocation has no caller, so silence
is indistinguishable from success -- the same trap `create_cluster`'s
swallowed error was. Every terminal outcome writes a structured line to the
function's retained log group and publishes to `sns_alerts_<cluster>`,
naming `finalize_cluster_teardown` as the manual recovery.

**Falling back is explicit, not silent.** If the Event invoke cannot be
started -- or the server is the local stdio one, where there is no Lambda to
invoke -- `delete_cluster` reports `auto_finalize_started: false` and its
`next_step` reverts to telling the caller to finish the job. The difference
between "handled" and "you must act" is the one thing that must never be
guessed.

### Two mistakes worth recording

The IAM edit split the existing `Lambda` statement *before* its `Effect`,
which sits after `Resource` in that file, producing one statement without an
`Effect` and one with it twice. Caught by the `logs:DeleteLogGroup` ban
walking every statement -- a guard for something else entirely.

`completion.py` states the 15-20 minute teardown duration, because the poll
bounds are chosen against it, and the multi-surface duration sweep failed
the moment it appeared. That is the "a new surface appears" case
`CLAUDE.md` describes, working.

### Headroom

`MCPStackMutation.json_src` is **6,118 of 6,144 bytes** minified. The new
statement is separate rather than an ARN appended to the existing `Lambda`
one, which would have been smaller but would have granted
`lambda:DeleteFunction` on the tier itself.
