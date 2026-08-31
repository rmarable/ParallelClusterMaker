"""One attempt of the teardown-completion poll: the AWS half.

Everything that decides anything lives in `mcp_server.completion`, which
touches no AWS and no clock, so the loop's bounds are testable
exhaustively. This module is the part that cannot be: describe the cluster,
act on the decision, and re-invoke.

**Failures here reach nobody by return value.** The invocation is
`InvocationType="Event"`; there is no caller waiting on it. So every
terminal outcome is written where an operator will actually find it: the
function's own CloudWatch log group, and the cluster's SNS topic, which
already exists per cluster and is already deleted on teardown. Silence from
an automatic teardown is indistinguishable from success, and that is the
failure mode this module has to avoid being.
"""

import json
import os

from mcp_server.completion import (
    POLL_SECONDS,
    decide,
    next_payload,
)


def _log(payload, outcome, extra=""):
    """One structured line per attempt. The log group is retained, so this
    is the record that survives the cluster."""
    print(
        json.dumps(
            {
                "pcm_completion": True,
                "cluster": payload.get("cluster_name"),
                "attempt": payload.get("attempt"),
                "action": outcome.action,
                "reason": outcome.reason,
                "extra": extra,
            },
            default=str,
        ),
        flush=True,
    )


def _notify(payload, subject, message):
    """Best-effort SNS. Never raises: a teardown must not be reported as
    failed because the notification failed, and the log line above is the
    record of record."""
    try:
        import boto3

        region = payload["region"]
        account = boto3.client("sts", region_name=region).get_caller_identity()["Account"]
        arn = f"arn:aws:sns:{region}:{account}:sns_alerts_{payload['cluster_name']}"
        boto3.client("sns", region_name=region).publish(
            TopicArn=arn, Subject=subject[:100], Message=message
        )
    except Exception as e:  # noqa: BLE001 - see docstring
        print(f"pcm_completion: SNS notify failed: {type(e).__name__}: {e}", flush=True)


def _describe_status(cluster_name, region):
    """The cluster's status, "" when it is confirmed gone, None when the
    question could not be answered.

    The three-way return is the point: None must never be read as absence,
    because a describe that failed is not evidence a stack is gone -- the
    same rule `_confirm_stack_is_gone` is built on.
    """
    try:
        from pcluster_core import ensure_event_loop
        import pcluster.lib as pc

        ensure_event_loop()

        return pc.describe_cluster(cluster_name=cluster_name, region=region).get(
            "clusterStatus", ""
        )
    except Exception as e:  # noqa: BLE001
        from pcluster_core import _describe_says_cluster_is_absent

        if _describe_says_cluster_is_absent(e):
            return ""
        return None


def _reinvoke(payload):
    import boto3

    from mcp_server.tiers import FUNCTION_NAMES

    boto3.client("lambda", region_name=payload["region"]).invoke(
        FunctionName=os.environ.get("AWS_LAMBDA_FUNCTION_NAME", FUNCTION_NAMES["stack-mutation"]),
        InvocationType="Event",
        Payload=json.dumps(next_payload(payload)).encode(),
    )


def run_completion_attempt(
    payload, *, now=None, describe=None, finalize=None, reinvoke=None, sleeper=None
):
    """Poll once and act. Injectable seams so a test drives the real
    control flow without AWS."""
    import time

    now = time.time() if now is None else now
    describe = describe or _describe_status
    reinvoke = reinvoke or _reinvoke

    status = describe(payload["cluster_name"], payload["region"])
    outcome = decide(
        status=status,
        attempt=int(payload.get("attempt", 0)),
        started_at=float(payload.get("started_at", now)),
        now=now,
    )

    if outcome.action == "retry":
        _log(payload, outcome)
        # The delay is the gap between invocations, not a sleep inside one:
        # Lambda bills wall-clock, so waiting in-process costs the same as
        # working and buys nothing.
        (sleeper or time.sleep)(POLL_SECONDS)
        reinvoke(payload)
        return {"action": "retry", "attempt": payload.get("attempt")}

    if outcome.action == "give_up":
        _log(payload, outcome)
        _notify(
            payload,
            f"ParallelClusterMaker: teardown of {payload['cluster_name']} needs attention",
            f"Automatic teardown completion stopped: {outcome.reason}.\n\n"
            f"The CloudFormation stack delete was started but this could not "
            f"confirm it finished, so the IAM policies, S3 bucket, SSH key "
            f"secret, SNS topic and cluster record may still exist.\n\n"
            f"Run finalize_cluster_teardown('{payload['cluster_name']}') once "
            f"the stack is gone.",
        )
        return {"action": "give_up", "reason": outcome.reason}

    _log(payload, outcome)
    if finalize is None:
        from pcluster_core import core_delete_cluster
        from mcp_server.tools import _repo_root

        def finalize(**kw):
            return core_delete_cluster(**kw)

        result = finalize(
            cluster_name=payload["cluster_name"],
            cluster_owner=payload["cluster_owner"],
            region=payload["region"],
            repo_root=_repo_root(),
            delete_s3_bucketname=("true" if payload.get("delete_s3_bucketname") else "false"),
            debug_mode=False,
            finalize_only=True,
        )
    else:
        result = finalize(payload)

    ok = getattr(result, "success", None)
    _log(payload, outcome, extra=f"finalize success={ok}")
    if ok is False:
        _notify(
            payload,
            f"ParallelClusterMaker: teardown of {payload['cluster_name']} failed to finish",
            f"The stack was deleted but cleanup failed: "
            f"{getattr(result, 'message', '')}\n\n"
            f"Run finalize_cluster_teardown('{payload['cluster_name']}').",
        )
    return {"action": "finalize", "success": ok}
