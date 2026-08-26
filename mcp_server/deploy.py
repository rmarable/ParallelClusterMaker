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

from .auth.discovery import www_authenticate_header
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


# ---------------------------------------------------------------- gateway
#
# A **REST** API, not an HTTP API, and the reason is measured rather than
# stylistic. The Lambda authorizer denies by raising, and only a REST API
# maps an authorizer error to 401 -- an HTTP API turns the same exception
# into a 500, which a client reads as a server fault and never
# re-authenticates over. Probed both ways before choosing:
#
#     REST, authorizer raises "Unauthorized"    -> 401
#     REST, authorizer raises a sentence        -> 500
#     HTTP, authorizer raises anything          -> 500
#
# The second line is why authorizer_lambda logs the descriptive message and
# raises the bare word: the mapping is on the *message*, not the class name.
# A REST API also supports gateway responses, which is where a
# WWW-Authenticate header can be attached to the 401.

_API_NAME = "pclustermaker-mcp"
_STAGE = "prod"

_PUBLIC_PATHS = (
    # Discovery and registration must be reachable *without* a token --
    # they are how a client learns where to get one. An authorizer on them
    # makes the flow unenterable.
    (".well-known/oauth-authorization-server", "GET"),
    (".well-known/oauth-protected-resource", "GET"),
    ("register", "POST"),
)


def _lambda_uri(region, account, function_name):
    arn = f"arn:aws:lambda:{region}:{account}:function:{function_name}"
    return (f"arn:aws:apigateway:{region}:lambda:path/2015-03-31/functions/"
            f"{arn}/invocations")


def _allow_apigw_to_invoke(lam, function_name, *, region, account, api_id):
    """API Gateway cannot invoke a function it lacks permission for, and the
    failure is a 500 with nothing in the function's own log."""
    try:
        lam.add_permission(
            FunctionName=function_name, StatementId=f"apigw-{api_id}",
            Action="lambda:InvokeFunction",
            Principal="apigateway.amazonaws.com",
            SourceArn=f"arn:aws:execute-api:{region}:{account}:{api_id}/*")
    except Exception as e:
        if type(e).__name__ != "ResourceConflictException":
            raise


def _child(apigw, api_id, parent_id, part, index):
    for item in index:
        if item.get("parentId") == parent_id and item.get("pathPart") == part:
            return item["id"]
    created = apigw.create_resource(
        restApiId=api_id, parentId=parent_id, pathPart=part)
    index.append(created)
    return created["id"]


def _resource_for(apigw, api_id, path, root_id, index):
    node = root_id
    for part in path.split("/"):
        node = _child(apigw, api_id, node, part, index)
    return node


def _wire(apigw, api_id, resource_id, method, uri, *, authorizer_id=None):
    kwargs = dict(restApiId=api_id, resourceId=resource_id, httpMethod=method)
    if authorizer_id:
        kwargs.update(authorizationType="CUSTOM", authorizerId=authorizer_id)
    else:
        kwargs.update(authorizationType="NONE")
    try:
        apigw.put_method(**kwargs)
    except Exception as e:
        if type(e).__name__ != "ConflictException":
            raise
        apigw.update_method(
            restApiId=api_id, resourceId=resource_id, httpMethod=method,
            patchOperations=[
                {"op": "replace", "path": "/authorizationType",
                 "value": "CUSTOM" if authorizer_id else "NONE"},
            ] + ([{"op": "replace", "path": "/authorizerId",
                   "value": authorizer_id}] if authorizer_id else []))
    apigw.put_integration(
        restApiId=api_id, resourceId=resource_id, httpMethod=method,
        type="AWS_PROXY", integrationHttpMethod="POST", uri=uri)


def setup_gateway(*, account, region, user_pool_id, cognito_domain=None,
                  apigw=None, lam=None, cog=None):
    """Create (or reuse) the REST API, its authorizer, and its routes.

    Idempotent in the same way _setup_mcp_infra is: an existing API,
    resource or method is reused rather than treated as an error.
    """
    import boto3

    apigw = apigw or boto3.client("apigateway", region_name=region)
    lam = lam or boto3.client("lambda", region_name=region)

    api_id = None
    for page in apigw.get_paginator("get_rest_apis").paginate():
        for api in page["items"]:
            if api["name"] == _API_NAME:
                api_id = api["id"]
    if api_id is None:
        api_id = apigw.create_rest_api(name=_API_NAME)["id"]
    base_url = f"https://{api_id}.execute-api.{region}.amazonaws.com/{_STAGE}"
    print(f"  REST API {_API_NAME} ({api_id})")

    index = apigw.get_resources(restApiId=api_id, limit=500)["items"]
    root_id = next(r["id"] for r in index if r["path"] == "/")

    auth_fn = FUNCTION_NAMES["authorizer"]
    existing = {a["name"]: a for a in
                apigw.get_authorizers(restApiId=api_id, limit=500)["items"]}
    if "cognito-jwt" in existing:
        authorizer_id = existing["cognito-jwt"]["id"]
    else:
        authorizer_id = apigw.create_authorizer(
            restApiId=api_id, name="cognito-jwt", type="TOKEN",
            authorizerUri=_lambda_uri(region, account, auth_fn),
            identitySource="method.request.header.Authorization",
            # No caching: a cached decision would outlive a client deleted
            # to revoke access, which is the revocation mechanism.
            authorizerResultTtlInSeconds=0)["id"]
    print(f"  authorizer cognito-jwt ({authorizer_id})")
    _allow_apigw_to_invoke(lam, auth_fn, region=region, account=account, api_id=api_id)

    public_uri = _lambda_uri(region, account, FUNCTION_NAMES["register"])
    router_uri = _lambda_uri(region, account, FUNCTION_NAMES["router"])
    _allow_apigw_to_invoke(lam, FUNCTION_NAMES["register"],
                           region=region, account=account, api_id=api_id)
    _allow_apigw_to_invoke(lam, FUNCTION_NAMES["router"],
                           region=region, account=account, api_id=api_id)

    for path, method in _PUBLIC_PATHS:
        rid = _resource_for(apigw, api_id, path, root_id, index)
        _wire(apigw, api_id, rid, method, public_uri)
        print(f"  {method:<5} /{path:<44} public")

    rid = _resource_for(apigw, api_id, "mcp", root_id, index)
    _wire(apigw, api_id, rid, "POST", router_uri, authorizer_id=authorizer_id)
    print(f"  POST  /mcp{'':<44} auth")

    # The 401 body and its WWW-Authenticate header. A REST API lets an
    # authorizer failure be shaped; an HTTP API does not, which is the other
    # half of why this is a REST API.
    if cognito_domain or base_url:
        try:
            apigw.put_gateway_response(
                restApiId=api_id, responseType="UNAUTHORIZED",
                statusCode="401",
                responseParameters={
                    # One source for the wire format. This was built inline
                    # here while www_authenticate_header() -- whose docstring
                    # says it is "configured on API Gateway's UNAUTHORIZED
                    # gateway response" -- had no caller at all, and the two
                    # had already diverged: the helper rstrips a trailing
                    # slash off the base URL and this did not, so a base_url
                    # ending in "/" produced a doubled slash in the only
                    # header that tells a client where to authenticate.
                    # API Gateway wants a static mapping value quoted.
                    "gatewayresponse.header.WWW-Authenticate":
                        "'" + www_authenticate_header(
                            api_base_url=base_url) + "'",
                })
            print("  gateway response UNAUTHORIZED -> 401 + WWW-Authenticate")
        except Exception as e:
            print(f"  WARNING: could not set the 401 gateway response: "
                  f"{type(e).__name__}")

    apigw.create_deployment(restApiId=api_id, stageName=_STAGE)
    print(f"  deployed to stage {_STAGE}")

    hosted_ui = (
        f"https://{cognito_domain}.auth.{region}.amazoncognito.com"
        if cognito_domain else ""
    )
    for fn in (FUNCTION_NAMES["register"],):
        cfg = lam.get_function_configuration(FunctionName=fn)
        env = dict((cfg.get("Environment") or {}).get("Variables") or {})
        env.update(MCP_USER_POOL_ID=user_pool_id, MCP_API_BASE_URL=base_url)
        if hosted_ui:
            env["MCP_COGNITO_DOMAIN"] = hosted_ui
        lam.update_function_configuration(
            FunctionName=fn, Environment={"Variables": env})
    cfg = lam.get_function_configuration(FunctionName=auth_fn)
    env = dict((cfg.get("Environment") or {}).get("Variables") or {})
    env.update(MCP_USER_POOL_ID=user_pool_id)
    lam.update_function_configuration(
        FunctionName=auth_fn, Environment={"Variables": env})

    return {"api_id": api_id, "base_url": base_url, "authorizer_id": authorizer_id}


_GATEWAY_NAME_PREFIX = "pclustermaker-mcp"


def delete_gateway(apigw, *, suppress=True):
    """Delete the REST API setup_gateway creates.

    Nothing owned this before: the transport could be built and not removed,
    so the internet-facing endpoint outlived every teardown. Named by prefix
    because setup_gateway names the API rather than recording its id.
    """
    removed = []
    for api in apigw.get_rest_apis().get("items", []):
        if not api["name"].startswith(_GATEWAY_NAME_PREFIX):
            continue
        try:
            apigw.delete_rest_api(restApiId=api["id"])
            removed.append(api["name"])
            print(f"  Deleted MCP REST API: {api['name']} ({api['id']})")
        except Exception:
            if not suppress:
                raise
    if not removed:
        print("  No MCP REST API present")
    return removed


def delete_cognito_pool(cog, *, pool_name_prefix, suppress=True):
    """Delete the user pool, its domain first.

    **The domain must go before the pool.** DeleteUserPool fails with
    InvalidParameterException -- "User pool cannot be deleted. It has a
    domain configured that should be deleted first." -- and the message names
    no domain, so a caller that guessed one from the pool name (they are not
    the same string) deletes nothing and reports success on the retry.  The
    domain is read off `describe_user_pool`, which is authoritative.
    """
    removed = []
    for pool in cog.list_user_pools(MaxResults=60)["UserPools"]:
        if not pool["Name"].startswith(pool_name_prefix):
            continue
        pid = pool["Id"]
        try:
            described = cog.describe_user_pool(UserPoolId=pid)["UserPool"]
            domain = described.get("Domain")
            if domain:
                cog.delete_user_pool_domain(Domain=domain, UserPoolId=pid)
                print(f"  Deleted Cognito domain: {domain}")
            cog.delete_user_pool(UserPoolId=pid)
            removed.append(pool["Name"])
            print(f"  Deleted Cognito user pool: {pool['Name']} ({pid})")
        except Exception:
            if not suppress:
                raise
    if not removed:
        print("  No MCP Cognito user pool present")
    return removed
