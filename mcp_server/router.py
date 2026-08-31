"""The Router Lambda: the MCP-facing endpoint behind API Gateway.

MCP's Streamable HTTP transport exposes exactly one endpoint -- the method
and tool name live in the JSON-RPC body, not the URL path -- so API Gateway
cannot route by tool the way it could for a REST-per-resource API. This
module does that fan-out instead, forwarding each `tools/call` to whichever
of the four handler Lambdas serves that tool.

NOT every JSON-RPC method is a tool call, and this is the part the
migration plan's one-line description ("parses the JSON-RPC body, reads the
tool name, and forwards") does not cover. Forwarding protocol-level methods
blindly would be wrong in ways that are quiet rather than loud:

  * `tools/list` sent to a single handler returns only that handler's
    tools, so a client would see roughly a quarter of the surface and
    conclude the rest does not exist.
  * `initialize` is the capability handshake for the *server*, not for one
    tier; answering it from a handler advertises that handler's view.
  * notifications carry no `id` and must produce no response at all --
    returning one is a protocol violation.

So the router terminates protocol methods itself and routes only
`tools/call`. `tools/list` is the one that still needs the handlers: it
fans out to all four and merges, which keeps every tool's schema owned by
the handler that implements it (one source of truth, in tools.py) rather
than duplicated into a router-side registry that could drift from it.

This module deliberately does not import pcluster_core or fastmcp. The
router executes no tool logic, and keeping its deployment package free of
that dependency chain is what makes its near-zero IAM meaningful rather
than incidental -- see templates/MCPRouterLambda.json_src.
"""

import json

from mcp_server.tiers import HANDLER_TIERS as _FANOUT_TIERS
from mcp_server.tiers import TOOL_TIERS as _TOOL_ROUTES

_PROTOCOL_VERSION = "2025-06-18"
_SERVER_INFO = {"name": "parallelclustermaker-remote", "version": "1"}

# JSON-RPC 2.0 reserved codes.
_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603


def _error(request_id, code, message):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _result(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def route_for(tool_name):
    """Tier serving `tool_name`, or None if it is not a routable tool."""
    return _TOOL_ROUTES.get(tool_name)


def unwrap_invocation(response, *, tier, request_id):
    """Turn a raw Lambda `Invoke` response into a JSON-RPC body.

    **A failing Lambda still returns StatusCode 200.** The failure is
    signalled by `FunctionError` ("Handled" or "Unhandled"), and the
    payload is then not the handler's response at all but Lambda's own
    error object: `{"errorMessage", "errorType", "stackTrace"}`. Returned
    verbatim -- which is what happened before this function existed -- that
    is two bugs at once. It is not a JSON-RPC response, so the client sees
    a protocol violation rather than an error it can act on; and
    `stackTrace` carries absolute server filesystem paths across the
    transport, which is precisely what Workstream 7 layer 5 forbids.

    The handler's own broad `except` covers a failing *tool*. It cannot
    cover a failure that happens outside it -- an import error on cold
    start, an OOM kill, or the function hitting its timeout -- and those
    are exactly the cases that produce an unhandled Lambda error.

    The stack trace is dropped, never forwarded. The type and message are
    kept: those are what a caller can act on.
    """
    try:
        payload = json.loads(response["Payload"].read())
    except (TypeError, ValueError, KeyError) as e:
        return _error(
            request_id,
            _INTERNAL_ERROR,
            f"handler {tier!r} returned an unreadable payload: {type(e).__name__}",
        )

    function_error = response.get("FunctionError")
    if not function_error:
        return payload

    if isinstance(payload, dict):
        etype = payload.get("errorType") or "error"
        message = payload.get("errorMessage") or "no message"
    else:
        etype, message = "error", str(payload)
    return _error(
        request_id,
        _INTERNAL_ERROR,
        f"handler {tier!r} failed ({function_error}): {etype}: {message}",
    )


def handle(body, *, invoke):
    """Handle one JSON-RPC request body. Returns a response dict, or None
    for a notification (which must produce no response).

    `invoke(tier, body)` is injected rather than constructed here so the
    routing logic is testable without AWS, and so the boto3 client is
    created once per container in lambda_handler rather than per call.
    """
    if not isinstance(body, dict):
        return _error(None, _INVALID_REQUEST, "request body must be a JSON object")

    method = body.get("method")
    request_id = body.get("id")

    if not isinstance(method, str):
        return _error(request_id, _INVALID_REQUEST, "missing or non-string 'method'")

    # Notifications have no id and take no response, per JSON-RPC 2.0.
    # Checked by method prefix rather than by absence of id: a client that
    # wrongly includes an id on a notification should still get silence
    # rather than a response it is not expecting.
    if method.startswith("notifications/"):
        return None

    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": _SERVER_INFO,
            },
        )

    if method == "ping":
        return _result(request_id, {})

    if method == "tools/list":
        # Fanned out concurrently. Serially this is the sum of every
        # handler's cold start, measured at **31.5s against API Gateway's
        # 29s integration ceiling** -- which is already the REST maximum,
        # so there is no timeout to raise. A cold `tools/list` therefore
        # timed out and the connector reported "Couldn't reload tools from
        # the server", while each tier individually looked healthy.
        #
        # It fit while there were three tiers; deploying the container tier
        # is what pushed it over, so nothing could have shown this until
        # the fourth tier existed. Concurrently the cost is the slowest
        # tier (~8.5s), not the sum, which restores the headroom rather
        # than spending it.
        #
        # Threads and stdlib only: the work is entirely waiting on Lambda
        # invocations, and this module must not grow a third-party
        # dependency (see the module docstring) -- that is what keeps its
        # near-zero IAM meaningful.
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=len(_FANOUT_TIERS)) as pool:
            responses = list(pool.map(lambda t: invoke(t, body), _FANOUT_TIERS))

        tools = []
        seen = set()
        # Still consumed in _FANOUT_TIERS order: `map` yields in input
        # order regardless of completion order, so the first-tier-wins
        # dedup below means the same thing it did when this was serial.
        for response in responses:
            for tool in (response or {}).get("result", {}).get("tools", []):
                name = tool.get("name")
                # A tool advertised by two tiers would otherwise appear
                # twice; first tier in _FANOUT_TIERS order wins, and the
                # duplicate is a routing-table bug the tests catch.
                if name in seen:
                    continue
                seen.add(name)
                tools.append(tool)
        return _result(request_id, {"tools": tools})

    if method == "tools/call":
        params = body.get("params")
        if not isinstance(params, dict):
            return _error(request_id, _INVALID_PARAMS, "'params' must be an object")
        name = params.get("name")
        tier = route_for(name)
        if tier is None:
            return _error(
                request_id,
                _INVALID_PARAMS,
                f"unknown tool {name!r} -- this server routes: {', '.join(sorted(_TOOL_ROUTES))}",
            )
        return invoke(tier, body)

    return _error(request_id, _METHOD_NOT_FOUND, f"unsupported method {method!r}")


def lambda_handler(event, context):
    """API Gateway proxy integration entry point.

    Kept thin on purpose: everything decision-shaped lives in handle(), so
    it can be tested without an AWS SDK, an event fixture, or a network.
    """
    import os

    import boto3

    # In-Lambda: AWS_REGION is this router's own region, and it invokes
    # handler functions in that same region. Bound explicitly so the
    # region-binding sweep holds across mcp_server too. os is stdlib -- the
    # router's no-third-party-import rule is unaffected.
    client = boto3.client(
        "lambda",
        region_name=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"),
    )

    def _invoke(tier, payload):
        from mcp_server.tiers import function_name_for

        request_id = (payload or {}).get("id") if isinstance(payload, dict) else None
        try:
            response = client.invoke(
                FunctionName=function_name_for(tier),
                InvocationType="RequestResponse",
                Payload=json.dumps(payload).encode("utf-8"),
            )
        except Exception as e:
            # unwrap_invocation covers a handler that *ran* and failed --
            # Lambda answers 200 with FunctionError and an error object.
            # Invoke itself can fail before any response exists at all: a
            # tier that is not deployed, an AccessDenied on the router's
            # role, throttling. Left uncaught, that propagates out of
            # lambda_handler and Lambda serializes it verbatim, stack trace
            # included -- observed against a partially deployed topology:
            # `FunctionError: Unhandled` with "/var/task/mcp_server/router.py"
            # in the payload. Same two bugs unwrap_invocation exists to
            # prevent (an internal path disclosed, and a body that is not a
            # JSON-RPC response), reached by the one door it does not watch.
            return _error(
                request_id,
                _INTERNAL_ERROR,
                f"tier {tier!r} could not be invoked: {type(e).__name__}",
            )
        return unwrap_invocation(response, tier=tier, request_id=request_id)

    try:
        body = json.loads(event.get("body") or "null")
    except (TypeError, ValueError):
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(_error(None, _PARSE_ERROR, "request body is not valid JSON")),
        }

    response = handle(body, invoke=_invoke)
    if response is None:
        # Notification: 202 with no body, per Streamable HTTP.
        return {"statusCode": 202, "body": ""}
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(response),
    }
