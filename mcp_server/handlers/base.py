"""Shared dispatch for the four tier handler Lambdas.

Each handler receives the JSON-RPC body the router forwarded, answers
`tools/list` with its own tier's tools, and executes `tools/call`. One
implementation rather than four near-copies: the tiers differ only in
which tools they register and in their IAM, never in how they dispatch,
and four copies of this logic is four places for the error translation to
drift.

Error translation is the point of this module as much as dispatch.
Workstream 7's layer 5 requires that a `pcluster.lib` exception surfaces
as a shaped tool error and never as a leaked Python traceback: a traceback
crossing the transport tells the model nothing it can act on, and can
carry account identifiers, ARNs, and file paths from the server's own
filesystem into a chat transcript.
"""

import asyncio
import json

from mcp_server.tiers import TOOL_TIERS, UNIMPLEMENTED

_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603
_METHOD_NOT_FOUND = -32601


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _result(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _build_tier_server(tier):
    """A FastMCP instance carrying only this tier's implemented tools.

    Imported lazily inside the function: mcp_server.tools pulls in
    pcluster_core and, through it, boto3 and PCluster. A handler needs
    that; module-import time is simply the wrong moment for it, because it
    would run on every cold start even for a request that turns out to be
    malformed.
    """
    from fastmcp import FastMCP

    from mcp_server.server import _INSTRUCTIONS
    from mcp_server.tools import register_tools

    mcp = FastMCP(name=f"parallelclustermaker-{tier}", instructions=_INSTRUCTIONS)
    register_tools(mcp, remote=True, tier=tier)
    return mcp


def handle(body, *, tier, server=None):
    """Dispatch one forwarded JSON-RPC body for `tier`."""
    if not isinstance(body, dict):
        return _error(None, _INVALID_PARAMS, "request body must be a JSON object")
    request_id = body.get("id")
    method = body.get("method")

    if method == "tools/list":
        mcp = server if server is not None else _build_tier_server(tier)
        tools = [t.to_mcp_tool().model_dump() for t in asyncio.run(mcp.list_tools())]
        return _result(request_id, {"tools": tools})

    if method != "tools/call":
        # The router terminates initialize/ping/notifications itself, so a
        # handler seeing one means the routing contract broke. Say that
        # rather than silently returning something plausible.
        return _error(
            request_id, _METHOD_NOT_FOUND,
            f"handler for tier {tier!r} received {method!r}; only tools/list and "
            f"tools/call are forwarded",
        )

    params = body.get("params") or {}
    name = params.get("name")

    if TOOL_TIERS.get(name) != tier:
        return _error(
            request_id, _INVALID_PARAMS,
            f"tool {name!r} is not served by tier {tier!r} -- the router forwarded it "
            f"to the wrong handler",
        )
    if name in UNIMPLEMENTED:
        return _error(
            request_id, _METHOD_NOT_FOUND,
            f"tool {name!r} is routed to this tier but not implemented yet",
        )

    mcp = server if server is not None else _build_tier_server(tier)
    try:
        result = asyncio.run(mcp.call_tool(name, params.get("arguments") or {}))
    except Exception as e:
        # Deliberately broad. Anything reaching here -- a pcluster.lib
        # NotFoundException, a botocore ClientError, a PClusterMakerError,
        # a bug -- must become a shaped error rather than a traceback
        # crossing the transport. The type name is included because it is
        # the one piece a caller can actually act on; the traceback is not.
        return _error(request_id, _INTERNAL_ERROR, f"{type(e).__name__}: {e}")

    return _result(request_id, _to_content(result))


def _to_content(result):
    """MCP tool results are content blocks. FastMCP's own call path
    already produces them for a registered tool; anything else is coerced
    to JSON text rather than passed through raw, so the response shape is
    the same regardless of what a wrapper returned.

    `FastMCP.call_tool` returns a `ToolResult`, not a dict -- it carries
    pydantic content models in `.content` and the tool's own return value
    in `.structured_content`. Without this branch it falls through to the
    catch-all below and the whole result becomes the *repr* of the
    ToolResult object as a single text block: syntactically a valid MCP
    response, semantically garbage, and no test that stubs the server can
    see it.
    """
    content = getattr(result, "content", None)
    if content is not None and not isinstance(result, dict):
        body = {"content": [_dump(block) for block in content]}
        structured = getattr(result, "structured_content", None)
        if structured is not None:
            body["structuredContent"] = structured
        # No isError branch: FastMCP *raises* ToolError for a failing tool
        # rather than returning a ToolResult with is_error set (verified
        # against 3.4.7), so that flag is never true on this path and the
        # failure is shaped by the `except Exception` above instead. A
        # branch here would be unreachable, which is exactly the dead code
        # no test can distinguish -- W8 in session 51 could not.
        return body
    if isinstance(result, dict) and "content" in result:
        return result
    if isinstance(result, (list, tuple)):
        blocks = []
        for item in result:
            if isinstance(item, dict) and item.get("type"):
                blocks.append(item)
            else:
                blocks.append({"type": "text", "text": _json(item)})
        return {"content": blocks}
    return {"content": [{"type": "text", "text": _json(result)}]}


def _dump(block):
    """Content blocks arrive as pydantic models and must cross the
    transport as plain JSON-serializable dicts."""
    if hasattr(block, "model_dump"):
        return block.model_dump(mode="json", exclude_none=True)
    if isinstance(block, dict):
        return block
    return {"type": "text", "text": _json(block)}


def _json(value):
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)


def make_lambda_handler(tier):
    """Build the AWS entry point for one tier.

    The four tier modules are one line each because of this -- there is
    nothing tier-specific about a handler except its name.
    """
    def lambda_handler(event, context):
        # A teardown-completion poll arrives on the same function as a
        # tools/call and is told apart by an explicit marker, never by the
        # absence of one -- a malformed event must not be mistaken for a
        # poll and start deleting things. See mcp_server/completion.py.
        from mcp_server.completion import is_completion_event

        if is_completion_event(event):
            from mcp_server.completion_runner import run_completion_attempt

            return run_completion_attempt(event)

        # A build request arrives the same way and for the same reason: the
        # work outlives the 29s gateway ceiling, so it runs where nothing is
        # waiting on it. Told apart by its own explicit marker.
        from mcp_server.build import is_build_event

        if is_build_event(event):
            from mcp_server.build_runner import run_build

            return run_build(event)
        return handle(event, tier=tier)

    return lambda_handler
