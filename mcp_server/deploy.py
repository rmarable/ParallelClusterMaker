"""Create/update/delete the MCP Lambda functions themselves.

`_setup_mcp_infra` in `src/pcluster_core.py` creates the execution roles and
their policies and stops there. This is the other half: the functions those
roles are for. It lives here rather than in `pcluster_core.py` because it
needs `mcp_server.packaging`, and the dependency direction is fixed --
`mcp_server/` may import `src/`, never the reverse (see `tiers.py`).

Nothing here builds an artifact. `packaging.py` stages files, this uploads a
reference to one; keeping them apart is what lets both be tested without a
network.

**Lambda's maximum function timeout is 900 seconds, and that is a hard
service limit, not a default.** It is the binding constraint on this whole
tier design, so it is expressed here as a constant every tier is checked
against rather than left implicit in five per-tier numbers. Any remote tool
whose worst case runs past it cannot be served by Lambda at all -- and the
failure is not a clean timeout but a partial mutation: the function is
killed mid-operation, having already stopped a fleet or started a stack
update, with the cluster's S3 lock still held by a process that no longer
exists. `apply_queue_config` was exactly that case, which is why it is
local-only; see `_LOCAL_ONLY` in `tools.py`.
"""

import os

from .packaging import TIER_PACKAGES, manifest
from .tiers import FUNCTION_NAMES

# AWS Lambda's hard ceiling on function timeout. Not adjustable by quota
# increase.
LAMBDA_MAX_TIMEOUT_SECONDS = 900

PYTHON_RUNTIME = "python3.12"

# Per-tier timeout and memory.
#
# The handler tiers get the full 900s not because any tool needs it -- none
# may, by the rule above -- but because the cost of an unused ceiling is
# zero (Lambda bills duration, not the limit) while the cost of a tool
# killed mid-mutation is a cluster in a partial state. The router's is
# deliberately short: it does one InvokeFunction and returns, so a router
# invocation still running after 60 seconds is a bug, and a low ceiling
# surfaces it instead of billing for it.
#
# Memory is also CPU on Lambda -- the two scale together -- so the numbers
# below are about cold-start time on the tiers that import PCluster's whole
# dependency chain, not about resident size.
TIER_RUNTIME = {
    "router": {"timeout": 60, "memory": 256},
    # 1024 is a CPU choice, not a memory one, and the numbers are why.
    # Measured on real deployed invocations, peak usage flat at ~237 MB
    # every time -- so nothing below is memory-starved -- while the cold
    # start, which is CPU-bound on importing PCluster's dependency chain,
    # moves sharply:
    #
    #     1024 MB -> 8.6s     512 MB -> 24.2s     384 MB -> 28.6s
    #
    # Lambda allocates CPU in proportion to memory, so trimming to the
    # measured peak triples first-call latency to save a fraction of a
    # cent per invocation. Do not "right-size" this against Max Memory
    # Used; that reads the wrong axis.
    "read-only": {"timeout": LAMBDA_MAX_TIMEOUT_SECONDS, "memory": 1024},
    "fleet-toggle": {"timeout": LAMBDA_MAX_TIMEOUT_SECONDS, "memory": 1024},
    "stack-mutation": {"timeout": LAMBDA_MAX_TIMEOUT_SECONDS, "memory": 2048},
    # Node.js + CDK synthesis is the slowest thing any tier does.
    "stack-mutation-node": {"timeout": LAMBDA_MAX_TIMEOUT_SECONDS, "memory": 3008},
    # Workstream 6. Both are deliberately short, and the authorizer's is
    # the shortest thing here: it runs *before every MCP request*, so its
    # latency is added to every tool call in the system. It does one JWKS
    # lookup (cached) and one DescribeUserPoolClient; anything slower is a
    # fault, not load. API Gateway's own integration timeout is 29s, so a
    # longer ceiling could not be reached anyway -- the request would have
    # been abandoned upstream first.
    "authorizer": {"timeout": 10, "memory": 512},
    # /register is called once, during connector setup.
    "register": {"timeout": 30, "memory": 512},
}


class MCPDeploymentError(Exception):
    """Raised instead of sys.exit: this is reachable from the MCP layer,
    where a SystemExit kills the server rather than failing one call."""


def validate_timeouts():
    """Refuse any tier configured past Lambda's ceiling.

    CreateFunction rejects it too, but only once the artifact is built and
    uploaded -- and the same number is what the tool wrappers' blocking
    behavior has to be reasoned against, so it is worth checking where it
    can be checked cheaply.
    """
    bad = {
        tier: cfg["timeout"]
        for tier, cfg in TIER_RUNTIME.items()
        if cfg["timeout"] > LAMBDA_MAX_TIMEOUT_SECONDS
    }
    if bad:
        raise MCPDeploymentError(
            "these tiers exceed Lambda's hard 900s function timeout: "
            + ", ".join(f"{t}={v}s" for t, v in sorted(bad.items()))
        )


def _role_arn(aws_account_id, tier):
    return f"arn:aws:iam::{aws_account_id}:role/pclustermaker-mcp-{tier}-role"


def function_spec(tier, *, aws_account_id, code, environment=None):
    """The CreateFunction kwargs for one tier, without calling AWS.

    `code` is passed through: {"S3Bucket": ..., "S3Key": ...} for a zip
    tier, {"ImageUri": ...} for the image tier. Which shape is correct is
    the tier's own business, so it is validated here rather than left to
    an opaque AWS error.
    """
    if tier not in TIER_PACKAGES:
        raise MCPDeploymentError(
            f"unknown MCP tier {tier!r} -- known: {', '.join(sorted(TIER_PACKAGES))}"
        )
    validate_timeouts()
    spec = TIER_PACKAGES[tier]
    cfg = TIER_RUNTIME[tier]
    is_image = spec["kind"] == "image"

    if is_image and "ImageUri" not in code:
        raise MCPDeploymentError(
            f"tier {tier!r} is a container image and needs code={{'ImageUri': ...}}; "
            f"got keys {sorted(code)}"
        )
    if not is_image and "ImageUri" in code:
        raise MCPDeploymentError(
            f"tier {tier!r} is a zip artifact and cannot take an ImageUri"
        )

    kwargs = {
        "FunctionName": FUNCTION_NAMES[tier],
        "Role": _role_arn(aws_account_id, tier),
        "Timeout": cfg["timeout"],
        "MemorySize": cfg["memory"],
        "Code": dict(code),
        "Description": f"ParallelClusterMaker MCP {tier} handler",
    }
    if is_image:
        kwargs["PackageType"] = "Image"
    else:
        kwargs["PackageType"] = "Zip"
        kwargs["Runtime"] = PYTHON_RUNTIME
        kwargs["Handler"] = spec["handler"]
    if environment:
        kwargs["Environment"] = {"Variables": dict(environment)}
    return kwargs


def deploy_tier(lam, tier, *, aws_account_id, code, environment=None):
    """Create the function, or update it if it already exists.

    Idempotent in the same way `_setup_mcp_infra` is: an existing function
    is updated rather than treated as an error, so a partially-completed
    deployment can be re-run. Returns the function ARN.
    """
    kwargs = function_spec(
        tier, aws_account_id=aws_account_id, code=code, environment=environment,
    )
    try:
        resp = lam.create_function(**kwargs)
        print(f"  Created MCP function: {kwargs['FunctionName']}")
        return resp["FunctionArn"]
    except Exception as e:
        if type(e).__name__ != "ResourceConflictException" and not _already_exists(e):
            raise
    # Code and configuration are two separate API calls; there is no
    # combined update. Code first: a configuration pointing at code that
    # was never uploaded is the worse intermediate state of the two.
    lam.update_function_code(FunctionName=kwargs["FunctionName"], **_code_update(code))
    # ...and Lambda will not accept the second call until the first has
    # settled: it answers ResourceConflictException, "An update is in
    # progress for resource". So this path could never have completed a
    # redeploy. Invisible to the tests, which stub the client and answer
    # both calls instantly; it took a real second deployment to see.
    _wait_until_updated(lam, kwargs["FunctionName"])
    cfg = {
        k: kwargs[k]
        for k in ("Role", "Timeout", "MemorySize", "Description")
    }
    for k in ("Runtime", "Handler", "Environment"):
        if k in kwargs:
            cfg[k] = kwargs[k]
    resp = lam.update_function_configuration(
        FunctionName=kwargs["FunctionName"], **cfg
    )
    print(f"  Updated MCP function: {kwargs['FunctionName']}")
    return resp["FunctionArn"]


def _wait_until_updated(lam, function_name):
    """Block until a function's last update has settled.

    Lambda's own waiter, not a sleep: it polls LastUpdateStatus and knows
    the terminal values. A create also has to settle before the function
    can be invoked, but create_function is followed by no second call
    here, so only the update path needs it.
    """
    try:
        lam.get_waiter("function_updated_v2").wait(FunctionName=function_name)
    except Exception:
        # An account or botocore old enough to lack the waiter should not
        # fail the deploy outright -- the configuration call below will
        # surface any real problem with its own error.
        pass


def _already_exists(exc):
    resp = getattr(exc, "response", None)
    if not isinstance(resp, dict):
        return False
    return resp.get("Error", {}).get("Code") == "ResourceConflictException"


def _code_update(code):
    """update_function_code takes the same fields as Code= but flattened,
    not nested -- passing Code={...} to it is silently accepted by botocore
    only to be rejected by the service."""
    return dict(code)


def delete_mcp_functions(lam, *, suppress=True):
    """Delete every function this module creates, driven by the same table.

    Tolerant by default, matching `_delete_mcp_infra`: one missing function
    must not abandon the rest.
    """
    for tier in TIER_PACKAGES:
        name = FUNCTION_NAMES[tier]
        try:
            lam.delete_function(FunctionName=name)
            print(f"  Deleted MCP function: {name}")
        except Exception:
            if not suppress:
                raise


def deployment_plan(aws_account_id):
    """Everything a deployment run would do, without doing any of it."""
    validate_timeouts()
    plan = []
    for tier in TIER_PACKAGES:
        entry = manifest(tier)
        entry["function_name"] = FUNCTION_NAMES[tier]
        entry["role_arn"] = _role_arn(aws_account_id, tier)
        entry["timeout"] = TIER_RUNTIME[tier]["timeout"]
        entry["memory"] = TIER_RUNTIME[tier]["memory"]
        if entry["kind"] == "image":
            entry["dockerfile"] = os.path.join(
                "mcp_server", f"Dockerfile.{tier}"
            )
        plan.append(entry)
    return plan
