"""Finish a teardown without the caller having to come back.

`delete_cluster` returns on CloudFormation's acceptance -- it must, because
a teardown runs 15-20 minutes and no MCP call can block for it -- so the IAM
policies, the S3 bucket, the SSH key secret, the SNS topic and the store
record are all still standing when it returns. Something has to make the
second call.

Nobody could. A conversational agent has no timer: told to poll and come
back, it correctly says "ping me and I'll poll" and stops. That was observed
live, by an agent that had read the instruction, quoted it back, and refused
the unsafe shortcut -- so the gap is not comprehension and no wording closes
it.

So the server finishes the job. `delete_cluster` fires this handler with
`InvocationType="Event"`, which returns in milliseconds; the handler polls
`describe_cluster`, re-invokes itself when the stack is still going, and
runs the existing `finalize_only` path once it is gone.

**The loop is the risk, and it is bounded three ways**, because a Lambda
that re-invokes itself has no natural end and a stack wedged in
`DELETE_IN_PROGRESS` would otherwise re-invoke forever:

  * a wall-clock deadline carried in the payload, checked before every
    re-invoke rather than only on entry;
  * a maximum attempt count, so a clock that misbehaves cannot defeat the
    deadline; and
  * a terminal state check that treats an unreadable describe as "keep
    waiting" for a bounded number of tries but never as "it is gone".

Nothing here waits inside an invocation. Each attempt is milliseconds of
work and the delay is the gap between invocations, because sleeping in a
Lambda is billed at the same rate as working in one.
"""

import time

# Poll cadence and ceiling. A teardown is 15-20 minutes; the deadline is
# generous enough to cover a slow one and short enough that a wedged stack
# stops costing invocations within the hour.
POLL_SECONDS = 60
MAX_ATTEMPTS = 60
DEADLINE_SECONDS = 3600

# The states that end the wait. Anything else means keep waiting.
_GONE = ("DELETE_COMPLETE",)
_FAILED = ("DELETE_FAILED",)


class CompletionOutcome:
    """What one attempt decided. Deliberately a plain object: this module
    is imported by a Lambda handler and must not drag in a dependency."""

    def __init__(self, action, reason, attempt=0):
        self.action = action  # "finalize" | "retry" | "give_up"
        self.reason = reason
        self.attempt = attempt

    def __repr__(self):
        return f"CompletionOutcome({self.action!r}, {self.reason!r}, {self.attempt})"

    def __eq__(self, other):
        return isinstance(other, CompletionOutcome) and (
            self.action,
            self.reason,
            self.attempt,
        ) == (other.action, other.reason, other.attempt)


def decide(
    *,
    status,
    attempt,
    started_at,
    now,
    max_attempts=MAX_ATTEMPTS,
    deadline_seconds=DEADLINE_SECONDS,
):
    """Whether to finalize, poll again, or stop. No AWS, no clock, no I/O.

    Separated from everything that touches AWS so the bound can be tested
    exhaustively -- the loop is the part of this design that can run up a
    bill or never stop, and it is the part a live test is worst at
    exercising.

    `status` is the cluster's status string, or None when the describe
    could not be answered. None is *not* absence: a describe that failed is
    not evidence a stack is gone, the same rule `_confirm_stack_is_gone` is
    built on. It means keep waiting, and the bounds below are what stop
    that being forever.
    """
    elapsed = now - started_at
    if status in _GONE or status == "":
        return CompletionOutcome("finalize", f"stack is {status or 'absent'}", attempt)
    if status in _FAILED:
        # Nothing to finalize: the stack is still there and an operator has
        # to look. Finalizing here would strip the IAM and the credentials
        # needed to investigate, which is the opposite of useful.
        return CompletionOutcome("give_up", f"stack is {status}", attempt)
    if attempt >= max_attempts:
        return CompletionOutcome("give_up", f"gave up after {attempt} attempts", attempt)
    if elapsed >= deadline_seconds:
        return CompletionOutcome("give_up", f"deadline passed ({int(elapsed)}s)", attempt)
    return CompletionOutcome("retry", f"status={status or 'unreadable'}", attempt)


def next_payload(payload):
    """The payload for the next attempt: the same one, one attempt on."""
    out = dict(payload)
    out["attempt"] = int(payload.get("attempt", 0)) + 1
    return out


def is_completion_event(event):
    """True when this invocation is a completion poll rather than a
    `tools/call`. Keyed on an explicit marker rather than on the absence of
    something, so an unrelated malformed event is never mistaken for one.

    The marker alone is not enough. The router forwards a `tools/call` body
    verbatim to a handler, so a caller who adds `_pcm_completion` would
    otherwise reach `run_completion_attempt` -- an unpreviewed, untokened
    teardown of any named cluster -- ahead of every wrapper-level gate. A
    legitimate self-invoke is `make_completion_event`'s payload and carries
    no JSON-RPC fields; the router will not forward a body without a
    `method`, so its presence marks the event as forwarded gateway input.
    Reject those here rather than trusting the marker."""
    return (
        isinstance(event, dict)
        and event.get("_pcm_completion") is True
        and event.get("method") is None
        and event.get("jsonrpc") is None
    )


def make_completion_event(
    *, cluster_name, cluster_owner, region, delete_s3_bucketname=True, started_at=None
):
    return {
        "_pcm_completion": True,
        "cluster_name": cluster_name,
        "cluster_owner": cluster_owner,
        "region": region,
        "delete_s3_bucketname": bool(delete_s3_bucketname),
        "attempt": 0,
        "started_at": started_at if started_at is not None else time.time(),
    }
