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

import json
import os
import time

from .auth.discovery import CLAUDE_REDIRECT_URI, www_authenticate_header
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
        raise MCPDeploymentError(f"tier {tier!r} is a zip artifact and cannot take an ImageUri")

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
        tier,
        aws_account_id=aws_account_id,
        code=code,
        environment=environment,
    )
    try:
        resp = _create_function_once_the_role_exists(lam, kwargs)
        print(f"  Created MCP function: {kwargs['FunctionName']}")
        _pin_async_retries_to_zero(lam, kwargs["FunctionName"])
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
    cfg = {k: kwargs[k] for k in ("Role", "Timeout", "MemorySize", "Description")}
    for k in ("Runtime", "Handler", "Environment"):
        if k in kwargs:
            cfg[k] = kwargs[k]
    resp = lam.update_function_configuration(FunctionName=kwargs["FunctionName"], **cfg)
    print(f"  Updated MCP function: {kwargs['FunctionName']}")
    _pin_async_retries_to_zero(lam, kwargs["FunctionName"])
    return resp["FunctionArn"]


def _pin_async_retries_to_zero(lam, function_name):
    """Refuse AWS's default retries on asynchronous invocations.

    Lambda retries a failed `Event` invocation **twice** by default. The
    teardown poller tolerates that -- finalizing an already-finalized
    teardown is a no-op -- but a retried *build* would attempt a second
    launch of the same cluster. The cluster lock refuses the second
    attempt, so this is the outer of two guards; both are kept because
    either alone is one edit away from a double launch.

    Best-effort: a deploy must not fail because this could not be set, and
    the lock is the guard that actually holds. The warning is the record.
    """
    try:
        lam.put_function_event_invoke_config(
            FunctionName=function_name,
            MaximumRetryAttempts=0,
        )
    except Exception as e:  # noqa: BLE001 - see docstring
        # Name the cause, not the wrapper. Every boto3 failure is a
        # `ClientError`, so printing the class name says nothing an
        # operator can act on -- the first live run of this warned
        # `(ClientError)` for what was a plain AccessDenied on an action
        # the deployed policy had not been updated to grant.
        code = ""
        try:
            code = e.response["Error"]["Code"]
        except (AttributeError, KeyError, TypeError):
            code = type(e).__name__
        print(
            f"  WARNING: could not pin {function_name} to zero async "
            f"retries: {code} on lambda:PutFunctionEventInvokeConfig. "
            f"A retried async invocation may run twice; the cluster lock "
            f"still refuses a duplicate build. Re-render the deploy "
            f"policy (generate_operator_policy.py --mcp) and push it as "
            f"a new policy version."
        )


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


# IAM is eventually consistent, and `--bootstrap` is the only path that
# creates a role and then a function inside one process: every earlier
# deploy ran --setup-infra and the tier deploys as separate invocations
# minutes apart, so this could not surface until the first live bootstrap,
# where it killed the run on the very first CreateFunction with the roles,
# policies and boundary already made.
ROLE_PROPAGATION_ATTEMPTS = 6
ROLE_PROPAGATION_SLEEP = 5


def _is_role_not_yet_assumable(exc):
    """A propagation lag, not a wrong trust policy -- and the code alone
    cannot tell them apart.

    Lambda answers `InvalidParameterValueException` for both, so the
    message is matched too. Retrying a genuinely wrong trust policy is
    bounded and ends in the same error the deploy would have raised
    immediately; treating every InvalidParameterValueException as
    retryable would instead sit for half a minute on a real
    misconfiguration.
    """
    resp = getattr(exc, "response", None)
    if not isinstance(resp, dict):
        return False
    err = resp.get("Error", {})
    return err.get(
        "Code"
    ) == "InvalidParameterValueException" and "cannot be assumed by Lambda" in (
        err.get("Message") or ""
    )


def _create_function_once_the_role_exists(lam, kwargs, *, sleep=None):
    """create_function, retried while its execution role is still propagating.

    `sleep` is injectable so a test can prove the retry without spending
    the wall-clock; production passes nothing.
    """
    sleep = time.sleep if sleep is None else sleep
    last = None
    for attempt in range(ROLE_PROPAGATION_ATTEMPTS):
        try:
            return lam.create_function(**kwargs)
        except Exception as e:
            if not _is_role_not_yet_assumable(e):
                raise
            last = e
            if attempt + 1 < ROLE_PROPAGATION_ATTEMPTS:
                print(
                    f"  {kwargs['FunctionName']}: execution role not assumable "
                    f"yet (IAM propagation), retrying in "
                    f"{ROLE_PROPAGATION_SLEEP}s "
                    f"[{attempt + 1}/{ROLE_PROPAGATION_ATTEMPTS - 1}]"
                )
                sleep(ROLE_PROPAGATION_SLEEP)
    raise last


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
            entry["dockerfile"] = os.path.join("mcp_server", f"Dockerfile.{tier}")
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
    return f"arn:aws:apigateway:{region}:lambda:path/2015-03-31/functions/{arn}/invocations"


def _allow_apigw_to_invoke(lam, function_name, *, region, account, api_id):
    """API Gateway cannot invoke a function it lacks permission for, and the
    failure is a 500 with nothing in the function's own log."""
    try:
        lam.add_permission(
            FunctionName=function_name,
            StatementId=f"apigw-{api_id}",
            Action="lambda:InvokeFunction",
            Principal="apigateway.amazonaws.com",
            SourceArn=f"arn:aws:execute-api:{region}:{account}:{api_id}/*",
        )
    except Exception as e:
        if type(e).__name__ != "ResourceConflictException":
            raise


def _child(apigw, api_id, parent_id, part, index):
    for item in index:
        if item.get("parentId") == parent_id and item.get("pathPart") == part:
            return item["id"]
    created = apigw.create_resource(restApiId=api_id, parentId=parent_id, pathPart=part)
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
            restApiId=api_id,
            resourceId=resource_id,
            httpMethod=method,
            patchOperations=[
                {
                    "op": "replace",
                    "path": "/authorizationType",
                    "value": "CUSTOM" if authorizer_id else "NONE",
                },
            ]
            + (
                [{"op": "replace", "path": "/authorizerId", "value": authorizer_id}]
                if authorizer_id
                else []
            ),
        )
    apigw.put_integration(
        restApiId=api_id,
        resourceId=resource_id,
        httpMethod=method,
        type="AWS_PROXY",
        integrationHttpMethod="POST",
        uri=uri,
        timeoutInMillis=GATEWAY_INTEGRATION_TIMEOUT_MS,
    )


# The app client the connector signs in with, created at deploy time
# because nothing else can create it.
#
# Cognito has no dynamic client registration and cannot be given any. The
# `/register` shim in this repo works when called directly, but no client
# ever calls it: the protected-resource document names Cognito as the
# authorization server, so a client fetches Cognito's own metadata, which
# advertises no `registration_endpoint`. Client ID Metadata Documents --
# what the MCP spec now prefers over DCR -- are no escape either, since
# they are an authorization-server feature and Cognito rejects a
# URL-formatted client_id outright with `invalid_request` (verified against
# a live pool). Implementing either would mean standing an authorization
# server in front of Cognito.
#
# So the operator pastes a client ID into the connector dialog, and the
# deploy's job is to make sure one exists and to say which.
CONNECTOR_CLIENT_NAME = "pclustermaker-mcp-connector"


def _find_user_pool_client(cog, *, user_pool_id, name):
    """The app client with this name, or None. Paginated: a pool that has
    accumulated clients would otherwise appear empty past the first page and
    a second client would be created on every deploy."""
    token = None
    while True:
        kwargs = {"UserPoolId": user_pool_id, "MaxResults": 60}
        if token:
            kwargs["NextToken"] = token
        resp = cog.list_user_pool_clients(**kwargs)
        for c in resp.get("UserPoolClients") or []:
            if c.get("ClientName") == name:
                return c["ClientId"]
        token = resp.get("NextToken")
        if not token:
            return None


def ensure_connector_client(cog, *, user_pool_id):
    """Create the connector's app client, or reuse the existing one.

    Delegates to `register()` -- the same function the `/register` endpoint
    serves -- rather than making a second `create_user_pool_client` call, so
    a client created here and one created dynamically are configured
    identically. Refresh-token rotation, revocation and the PKCE-only
    public-client shape all have one definition, and a change to any of them
    cannot reach one path and miss the other.

    Returns `(client_id, created)`.
    """
    from .auth.register_lambda import register

    existing = _find_user_pool_client(cog, user_pool_id=user_pool_id, name=CONNECTOR_CLIENT_NAME)
    if existing:
        return existing, False
    doc = register(
        {"client_name": CONNECTOR_CLIENT_NAME, "redirect_uris": [CLAUDE_REDIRECT_URI]},
        user_pool_id=user_pool_id,
        cognito=cog,
    )
    return doc["client_id"], True


def setup_gateway(
    *, account, region, user_pool_id, cognito_domain=None, apigw=None, lam=None, cog=None
):
    """Create (or reuse) the REST API, its authorizer, and its routes.

    Idempotent in the same way _setup_mcp_infra is: an existing API,
    resource or method is reused rather than treated as an error.
    """
    import boto3

    apigw = apigw or boto3.client("apigateway", region_name=region)
    lam = lam or boto3.client("lambda", region_name=region)
    cog = cog or boto3.client("cognito-idp", region_name=region)

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
    existing = {a["name"]: a for a in apigw.get_authorizers(restApiId=api_id, limit=500)["items"]}
    if "cognito-jwt" in existing:
        authorizer_id = existing["cognito-jwt"]["id"]
    else:
        authorizer_id = apigw.create_authorizer(
            restApiId=api_id,
            name="cognito-jwt",
            type="TOKEN",
            authorizerUri=_lambda_uri(region, account, auth_fn),
            identitySource="method.request.header.Authorization",
            # No caching: a cached decision would outlive a client deleted
            # to revoke access, which is the revocation mechanism.
            authorizerResultTtlInSeconds=0,
        )["id"]
    print(f"  authorizer cognito-jwt ({authorizer_id})")
    _allow_apigw_to_invoke(lam, auth_fn, region=region, account=account, api_id=api_id)

    public_uri = _lambda_uri(region, account, FUNCTION_NAMES["register"])
    router_uri = _lambda_uri(region, account, FUNCTION_NAMES["router"])
    _allow_apigw_to_invoke(
        lam, FUNCTION_NAMES["register"], region=region, account=account, api_id=api_id
    )
    _allow_apigw_to_invoke(
        lam, FUNCTION_NAMES["router"], region=region, account=account, api_id=api_id
    )

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
                restApiId=api_id,
                responseType="UNAUTHORIZED",
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
                    "gatewayresponse.header.WWW-Authenticate": "'"
                    + www_authenticate_header(api_base_url=base_url)
                    + "'",
                },
            )
            print("  gateway response UNAUTHORIZED -> 401 + WWW-Authenticate")
        except Exception as e:
            print(f"  WARNING: could not set the 401 gateway response: {type(e).__name__}")

    apigw.create_deployment(restApiId=api_id, stageName=_STAGE)
    print(f"  deployed to stage {_STAGE}")

    # Deliberately not fatal. Every route above is already live, and a
    # deploy that fails here has produced a working transport that the
    # operator merely has to find a client ID for -- aborting would throw
    # that away over the last, least step.
    connector_client_id = None
    try:
        connector_client_id, made = ensure_connector_client(cog, user_pool_id=user_pool_id)
        print(
            f"  connector app client {CONNECTOR_CLIENT_NAME} ({'created' if made else 'reusing'})"
        )
    except Exception as e:
        print(f"  WARNING: could not create the connector app client: {type(e).__name__}: {e}")
        print(
            "  The transport is up; create one with "
            "cognito-idp create-user-pool-client, or POST to /register."
        )

    hosted_ui = (
        f"https://{cognito_domain}.auth.{region}.amazoncognito.com" if cognito_domain else ""
    )
    for fn in (FUNCTION_NAMES["register"],):
        cfg = lam.get_function_configuration(FunctionName=fn)
        env = dict((cfg.get("Environment") or {}).get("Variables") or {})
        env.update(MCP_USER_POOL_ID=user_pool_id, MCP_API_BASE_URL=base_url)
        if hosted_ui:
            env["MCP_COGNITO_DOMAIN"] = hosted_ui
        lam.update_function_configuration(FunctionName=fn, Environment={"Variables": env})
    cfg = lam.get_function_configuration(FunctionName=auth_fn)
    env = dict((cfg.get("Environment") or {}).get("Variables") or {})
    env.update(MCP_USER_POOL_ID=user_pool_id)
    lam.update_function_configuration(FunctionName=auth_fn, Environment={"Variables": env})

    return {
        "api_id": api_id,
        "base_url": base_url,
        "authorizer_id": authorizer_id,
        "connector_client_id": connector_client_id,
    }


# API Gateway REST caps an integration at 29s and this is already the
# maximum -- raising it needs a service quota increase, not a parameter.
# Set explicitly rather than inherited, so the number is visible at the call
# site: it is the real ceiling on every remote tool call, far tighter than
# the Lambda's 900s, and it was invisible here until a 42s
# apply_cluster_update returned a timeout to the caller while the update it
# had already submitted ran to completion.
GATEWAY_INTEGRATION_TIMEOUT_MS = 29_000

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


# Cognito's default password policy: >= 8 characters, with an uppercase, a
# lowercase, a number and a symbol. Read off CreateUserPool's own
# PasswordPolicyType defaults rather than guessed -- a generated password
# that misses one class fails at AdminSetUserPassword with
# InvalidPasswordException, after the user already exists.
_PASSWORD_SYMBOLS = "!@#$%^&*()-_=+[]{}"


def generate_user_password(*, length=20):
    """A password satisfying Cognito's default policy, from `secrets`.

    One character drawn from each required class first, the rest from the
    union, then shuffled -- rejection sampling on a random string would
    loop, and taking the classes in a fixed order would put them in a fixed
    position.
    """
    import secrets
    import string

    if length < 8:
        raise ValueError("Cognito's default policy requires at least 8 characters")
    classes = (string.ascii_uppercase, string.ascii_lowercase, string.digits, _PASSWORD_SYMBOLS)
    chars = [secrets.choice(c) for c in classes]
    pool = "".join(classes)
    chars += [secrets.choice(pool) for _ in range(length - len(classes))]
    # SystemRandom.shuffle, not random.shuffle: the whole point is that the
    # ordering is not predictable from a seed either.
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def ensure_cognito_user(cog, *, pool_id, username, password):
    """Create a Cognito user with a permanent password; idempotent.

    Nothing in the deploy path ever created a user, so a freshly stood-up
    transport reached the Hosted UI with nobody to log in as -- the one
    manual step between `--setup-gateway` and a working browser connector.

    **`MessageAction="SUPPRESS"` is required, not tidiness.** The default
    sends an invitation through Cognito's own mailer, which needs a verified
    `email` attribute and is capped at 50 messages a day; without SUPPRESS a
    pool with no SES configuration fails the create outright. The password
    is then set `Permanent=True`, which moves the user straight to
    CONFIRMED -- the invitation flow would leave them in
    FORCE_CHANGE_PASSWORD, where the Hosted UI demands a reset the operator
    was never mailed.

    Returns True if the user was created, False if it already existed. An
    existing user still has its password set: the reason to re-run this is
    usually that the password was lost.
    """
    created = True
    attrs = []
    if "@" in username:
        # Only when it really is an address. Setting email to a non-address
        # is an InvalidParameterException, and the pool does not require
        # the attribute at all.
        attrs = [{"Name": "email", "Value": username}, {"Name": "email_verified", "Value": "true"}]
    try:
        cog.admin_create_user(
            UserPoolId=pool_id,
            Username=username,
            UserAttributes=attrs,
            MessageAction="SUPPRESS",
        )
    except Exception as e:
        if type(e).__name__ != "UsernameExistsException":
            raise
        created = False

    cog.admin_set_user_password(
        UserPoolId=pool_id,
        Username=username,
        Password=password,
        Permanent=True,
    )
    return created


# Probes for the deploy preflight: one representative action per
# MCPDeployPolicy statement, chosen from the grants that carry **no
# Condition**. iam:CreateRole is deliberately absent -- its grant is
# conditional on iam:PermissionsBoundary, and a simulation that does not
# supply that context key comes back implicitDeny with the key listed in
# MissingContextValues, which is indistinguishable from a real denial
# unless you look. Probing only unconditional grants keeps the check from
# inventing failures.
_DEPLOY_PROBES = (
    ("iam:CreatePolicy", "arn:aws:iam::{acct}:policy/pclustermaker-mcp-policy-probe"),
    ("iam:AttachRolePolicy", "arn:aws:iam::{acct}:role/pclustermaker-mcp-probe"),
    ("lambda:CreateFunction", "arn:aws:lambda:{region}:{acct}:function:pclustermaker-mcp-probe"),
    # Probed because its absence is otherwise silent: the pin is
    # best-effort by design, so a missing grant warns after the fact
    # instead of stopping anything, and the async-retry guard is simply
    # never applied. That is how it shipped inert on its first deploy.
    (
        "lambda:PutFunctionEventInvokeConfig",
        "arn:aws:lambda:{region}:{acct}:function:pclustermaker-mcp-probe",
    ),
    ("cognito-idp:CreateUserPool", "*"),
)


def preflight_deploy_permissions(iam, *, caller_arn, aws_account_id, region):
    """Which of the deploy's own permissions the caller is missing.

    Returns a list of denied action names -- empty when everything needed
    is allowed -- or **None** when the question could not be answered.

    None is not a failure. `iam:SimulatePrincipalPolicy` is itself a
    permission, and MCPDeployPolicy does not grant it, so an identity
    holding exactly the right policy cannot run this check. Following
    `_check_external_nfs_reachable`: only a *confirmed* denial is fatal,
    and everything else warns and lets the operator proceed. A preflight
    that blocks the deploy because it could not see is worse than no
    preflight.

    An assumed-role ARN has to be rewritten to its role ARN --
    SimulatePrincipalPolicy takes the role, not the session.
    """
    arn = caller_arn
    if ":assumed-role/" in arn:
        _, _, tail = arn.partition(":assumed-role/")
        role = tail.split("/")[0]
        arn = f"arn:aws:iam::{aws_account_id}:role/{role}"

    denied = []
    try:
        for action, resource in _DEPLOY_PROBES:
            res = resource.format(acct=aws_account_id, region=region)
            resp = iam.simulate_principal_policy(
                PolicySourceArn=arn,
                ActionNames=[action],
                ResourceArns=[res],
            )
            for r in resp.get("EvaluationResults") or []:
                # A conditional grant simulated without its context key is
                # not evidence of a denial.
                if r.get("MissingContextValues"):
                    continue
                if r.get("EvalDecision") != "allowed":
                    denied.append(r["EvalActionName"])
    except Exception:
        return None
    return denied


IMAGE_REPOSITORY = "pclustermaker-mcp-stack-mutation-node"
IMAGE_PLATFORM = "linux/amd64"

# In preference order. Finch first because it is AWS's own and the one the
# Finch-specific push failure below was diagnosed against; nerdctl last
# because a machine with it usually has one of the others too.
CONTAINER_RUNTIMES = ("finch", "docker", "podman", "nerdctl")


class ImageBuildError(RuntimeError):
    """A build or push failed. Its own type so the caller can print the
    remedy rather than a CalledProcessError's repr."""


def detect_container_runtime(candidates=CONTAINER_RUNTIMES):
    """The first container runtime on PATH, or None."""
    import shutil as _shutil

    for name in candidates:
        if _shutil.which(name):
            return name
    return None


def ensure_ecr_repository(ecr, *, repository=IMAGE_REPOSITORY):
    """Create the image repository if absent. Returns (uri, created).

    **The URI comes from AWS, never from f-string assembly.** The obvious
    `{acct}.dkr.ecr.{region}.amazonaws.com/{name}` is right in the standard
    partition and wrong in GovCloud and China, where the suffix differs --
    and a registry host that is subtly wrong fails at `push` with an
    authentication error rather than a name error. `repositoryUri` is on
    both CreateRepository's and DescribeRepositories' Repository shape;
    same reasoning as reading the Cognito domain off `describe_user_pool`.
    """
    try:
        repo = ecr.create_repository(repositoryName=repository)["repository"]
        return repo["repositoryUri"], True
    except Exception as e:
        if type(e).__name__ != "RepositoryAlreadyExistsException":
            raise
    repos = ecr.describe_repositories(repositoryNames=[repository])["repositories"]
    return repos[0]["repositoryUri"], False


def lambda_pull_policy(*, aws_account_id, region):
    """The repository policy letting Lambda pull this tier's image.

    Pushing an image is not enough to deploy it. Lambda fetches the image
    as **the Lambda service, not as the deployer**, so the deployer's own
    ECR grants are irrelevant to it and `CreateFunction` fails with
    `AccessDeniedException: Lambda does not have permission to access the
    ECR image` -- an identity-policy-shaped message for a resource-policy
    problem, which is what makes it slow to place.

    The `aws:SourceArn` condition is scoped to this toolkit's own function
    names rather than left off. Without a condition any Lambda function in
    any account could pull the image; AWS's own documented snippet uses
    `function:*`, which is every function in this account.
    """
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "LambdaECRImageRetrievalPolicy",
                    "Effect": "Allow",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
                    "Condition": {
                        "StringLike": {
                            "aws:SourceArn": f"arn:aws:lambda:{region}:{aws_account_id}:function:"
                            f"{_GATEWAY_NAME_PREFIX}-*"
                        }
                    },
                }
            ],
        }
    )


def ensure_lambda_can_pull(ecr, *, aws_account_id, region, repository=IMAGE_REPOSITORY):
    """Put the pull policy on the repository. Idempotent -- SetRepositoryPolicy
    replaces wholesale, so re-running converges rather than accumulating."""
    ecr.set_repository_policy(
        repositoryName=repository,
        policyText=lambda_pull_policy(aws_account_id=aws_account_id, region=region),
    )


def delete_ecr_repository(ecr, *, repository=IMAGE_REPOSITORY, suppress=True):
    """Remove the image repository. Returns True if it was there.

    **`force=True` is required, not convenience.** DeleteRepository raises
    RepositoryNotEmptyException while any image remains, and a repository
    this deploy created always holds at least the image it pushed -- so
    without it teardown fails on every transport that ever deployed the
    container tier, which is precisely the case it exists for.
    """
    try:
        ecr.delete_repository(repositoryName=repository, force=True)
        return True
    except Exception as e:
        if type(e).__name__ == "RepositoryNotFoundException":
            return False
        if not suppress:
            raise
        return False


def ecr_login(ecr, runtime, *, run=None):
    """Log the container runtime in to ECR. Returns the registry host.

    The token is base64 of `user:password` per ECR's own contract, and the
    password reaches the runtime on **stdin** rather than argv -- a
    credential in a command line is visible to every other process on the
    machine via `ps`.
    """
    import base64
    import subprocess

    run = run or subprocess.run
    data = ecr.get_authorization_token()["authorizationData"][0]
    user, _, password = base64.b64decode(data["authorizationToken"]).decode().partition(":")
    registry = data["proxyEndpoint"].removeprefix("https://")
    proc = run(
        [runtime, "login", "--username", user, "--password-stdin", registry],
        input=password,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise ImageBuildError(
            f"{runtime} login to {registry} failed:\n{(proc.stderr or '').strip()}"
        )
    return registry


_NO_AUTH = "no basic auth credentials"


def build_and_push_image(
    runtime, *, image_uri, repo_root, dockerfile, platform=IMAGE_PLATFORM, run=None
):
    """Build the container tier's image and push it. Returns image_uri.

    `--platform` is passed always, never left to the host's default: an
    image built on Apple Silicon or an ARM Linux host is linux/arm64, and
    Lambda rejects the mismatch at CreateFunction rather than at
    invocation, long after the push.
    """
    import subprocess

    run = run or subprocess.run
    build = run(
        [runtime, "build", "--platform", platform, "-f", dockerfile, "-t", image_uri, repo_root],
        text=True,
        capture_output=True,
    )
    if build.returncode != 0:
        raise ImageBuildError(f"{runtime} build failed:\n{(build.stderr or '').strip()[-2000:]}")

    push = run([runtime, "push", image_uri], text=True, capture_output=True)
    if push.returncode != 0:
        err = (push.stderr or "").strip()
        # Finch's three-location credential problem: `finch login` writes to
        # the host while the push runs inside its Lima VM. Deliberately not
        # automated -- the fix writes an ECR token into two paths inside the
        # VM and both must be scrubbed afterward, so a process that dies
        # between the two leaves a credential at rest. Name the remedy
        # instead of half-applying it.
        if _NO_AUTH in err:
            raise ImageBuildError(
                f"{runtime} push failed: {_NO_AUTH}.\n"
                f"  On Finch the push happens inside its Lima VM, which does "
                f"not see the\n"
                f"  credential `finch login` wrote to the host. See "
                f'"If the push fails with\n'
                f"  'no basic auth credentials'\" in INSTALL.md -- three "
                f"locations are needed\n"
                f"  and no two of them suffice."
            )
        raise ImageBuildError(f"{runtime} push failed:\n{err[-2000:]}")
    return image_uri
