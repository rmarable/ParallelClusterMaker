"""The marker and payload for an asynchronous cluster build.

`create_cluster` measured **43.6 seconds** against API Gateway's 29-second
integration timeout, which is already the REST maximum. Roughly 39 of
those seconds are inside `pcluster.lib`'s own CDK synthesis of the
CloudFormation template, so no decomposition of *our* work brings the call
under the ceiling. The caller was cut off while the build proceeded
normally, and a retry then had to guess whether the first attempt had
taken.

So the build runs where nothing is waiting on it. The tool validates what
it can, fires an `InvocationType="Event"` invoke at its own function, and
returns in about a second with a real answer instead of a timeout.

**This module is pure**, for the reason `completion.py` is: the decisions
worth testing exhaustively must not need AWS to exercise. It holds the
marker, the payload shape, and nothing else.

Two properties that are not obvious:

  * The marker is an explicit key, never the *absence* of a `method`
    field. A malformed event must not be mistaken for a build request and
    start creating infrastructure.
  * `MaximumRetryAttempts` on the function's async invoke config must be
    **0**. AWS retries a failed asynchronous invocation twice by default,
    and unlike a teardown poll -- which is idempotent, since finalizing an
    already-finalized teardown is a no-op -- a retried build would attempt
    a second launch. The cluster lock refuses the second attempt, so this
    is belt and braces; both are kept because either alone is one edit
    away from a double launch.
"""


def is_build_event(event):
    """True when this invocation is a build request rather than a
    `tools/call`. Keyed on an explicit marker, never on the absence of
    something else.

    The marker is not sufficient on its own. A handler serves gateway
    traffic and self-invokes on the same entry point, and the router
    forwards a `tools/call` body *verbatim* -- so a caller who adds
    `_pcm_build` to a request would otherwise reach `run_build` ahead of
    every wrapper-level gate (the confirmation token, `_REMOTE_DENIED_PARAMS`).
    A legitimate self-invoke is `make_build_event`'s payload and carries no
    JSON-RPC fields; the router refuses to forward a body without a
    `method`, so its presence proves the event is forwarded gateway input,
    not a self-invoke. Reject those here rather than trusting the marker."""
    return (
        isinstance(event, dict)
        and event.get("_pcm_build") is True
        and event.get("method") is None
        and event.get("jsonrpc") is None
    )


def make_build_event(*, params, region, repo_root=None):
    """The payload the background invocation receives.

    `params` is a MakeClusterParams as a plain dict -- the dataclass does
    not survive JSON, and the boundary is a Lambda invoke payload. `region`
    rides separately because MakeClusterParams carries none: the CLI
    resolves it from the AZ-verification call and passes it to
    core_create_cluster on its own, and every shim must do the same.
    """
    return {
        "_pcm_build": True,
        "params": params,
        "region": region,
        "repo_root": repo_root,
    }
