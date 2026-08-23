"""Workstream 5: the four tier handler Lambdas.

The dispatch is shared (handlers/base.py), so these test it once rather
than four times. What actually needs pinning:

  * a tier serves exactly the tools routed to it -- no more (a tool
    appearing on a tier whose IAM does not back it fails at call time
    with an opaque AccessDenied) and no fewer (a tool the router forwards
    but the handler does not serve is dead surface);
  * exceptions become shaped JSON-RPC errors, never a traceback crossing
    the transport (Workstream 7 layer 5);
  * a misrouted call is rejected by the handler too, rather than trusting
    the router to have got it right.
"""

import json
import os
import sys
import types

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from mcp_server.handlers import base  # noqa: E402
from mcp_server.handlers import (  # noqa: E402
    fleet_toggle,
    read_only,
    stack_mutation,
    stack_mutation_node,
)
from mcp_server.tiers import HANDLER_TIERS, TOOL_TIERS, UNIMPLEMENTED, tools_for  # noqa: E402

_MODULES = {
    "read-only": read_only,
    "fleet-toggle": fleet_toggle,
    "stack-mutation": stack_mutation,
    "stack-mutation-node": stack_mutation_node,
}


def _list(tier):
    return base.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, tier=tier)


def _call(tier, name, arguments=None, **kw):
    body = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}}}
    return base.handle(body, tier=tier, **kw)


class TestEveryTierHasAModule:
    def test_one_module_per_handler_tier(self):
        assert set(_MODULES) == set(HANDLER_TIERS)

    @pytest.mark.parametrize("tier", sorted(_MODULES))
    def test_each_module_declares_its_tier_and_an_entry_point(self, tier):
        mod = _MODULES[tier]
        assert mod.TIER == tier
        assert callable(mod.lambda_handler)

    @pytest.mark.parametrize("tier", sorted(_MODULES))
    def test_the_entry_point_dispatches_for_its_own_tier(self, tier):
        """A copy-paste slip that left one module's TIER pointing at
        another's would make that Lambda serve the wrong tool set with the
        wrong IAM behind it."""
        resp = _MODULES[tier].lambda_handler(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, None
        )
        served = {t["name"] for t in resp["result"]["tools"]}
        assert served <= set(tools_for(tier))


class TestTierServesExactlyItsRoutedTools:
    @pytest.mark.parametrize("tier", sorted(HANDLER_TIERS))
    def test_no_tool_leaks_onto_the_wrong_tier(self, tier):
        """The IAM-relevant direction: a tool served by a tier whose policy
        does not back it fails at call time with an opaque AccessDenied."""
        served = {t["name"] for t in _list(tier)["result"]["tools"]}
        for name in served:
            assert TOOL_TIERS.get(name) == tier, (
                f"{name!r} is served by {tier!r} but routed to {TOOL_TIERS.get(name)!r}"
            )

    @pytest.mark.parametrize("tier", sorted(HANDLER_TIERS))
    def test_served_plus_unimplemented_covers_everything_routed(self, tier):
        """The other direction: every routed tool is either served or
        explicitly declared unimplemented. Anything else is dead surface --
        the router forwards it and the handler does not have it."""
        served = {t["name"] for t in _list(tier)["result"]["tools"]}
        routed = set(tools_for(tier))
        assert routed == served | (routed & UNIMPLEMENTED)

    def test_local_only_tools_never_appear_on_any_tier(self):
        """rotate_cluster_key/manage_grafana_tunnel are local-transport
        only; a tier handler is by definition remote."""
        from mcp_server.tools import _LOCAL_ONLY

        for tier in HANDLER_TIERS:
            served = {t["name"] for t in _list(tier)["result"]["tools"]}
            assert served.isdisjoint(_LOCAL_ONLY)

    def test_the_unimplemented_list_names_only_routed_tools(self):
        """A stale entry here would silently suppress a real tool."""
        assert UNIMPLEMENTED <= set(TOOL_TIERS)


class TestToolsCall:
    def test_a_misrouted_call_is_rejected_by_the_handler(self):
        """Defence in depth: the handler does not trust the router to have
        routed correctly."""
        resp = _call("fleet-toggle", "list_clusters")
        assert resp["error"]["code"] == base._INVALID_PARAMS
        assert "wrong handler" in resp["error"]["message"]

    def test_an_unimplemented_tool_says_so_rather_than_unknown(self):
        """'Not built yet' and 'no such tool' need different responses --
        the first is a roadmap fact, the second is a caller error.

        UNIMPLEMENTED is empty now that the creation pair landed, so this
        exercises the branch with a synthetic entry rather than deleting
        the test: the distinction still has to work the next time a tool
        is routed before it is built."""
        import mcp_server.tiers as tiers_mod

        original = base.UNIMPLEMENTED
        base.UNIMPLEMENTED = frozenset({"list_clusters"})
        try:
            resp = _call("read-only", "list_clusters")
        finally:
            base.UNIMPLEMENTED = original
        assert resp["error"]["code"] == base._METHOD_NOT_FOUND
        assert "not implemented yet" in resp["error"]["message"]

    def test_an_unknown_tool_is_invalid_params(self):
        resp = _call("read-only", "no_such_tool")
        assert resp["error"]["code"] == base._INVALID_PARAMS

    def test_a_protocol_method_reaching_a_handler_is_reported(self):
        """The router terminates these; one arriving here means the routing
        contract broke, and saying so beats returning something plausible."""
        resp = base.handle({"jsonrpc": "2.0", "id": 3, "method": "initialize"}, tier="read-only")
        assert resp["error"]["code"] == base._METHOD_NOT_FOUND
        assert "only tools/list and tools/call" in resp["error"]["message"]

    def test_a_non_object_body_is_rejected(self):
        assert base.handle("nope", tier="read-only")["error"]["code"] == base._INVALID_PARAMS

    def test_the_response_id_matches_the_request(self):
        resp = _call("read-only", "no_such_tool")
        assert resp["id"] == 2


class _Boom:
    """A stand-in FastMCP whose tool call raises."""

    def __init__(self, exc):
        self._exc = exc

    async def list_tools(self):
        return []

    async def call_tool(self, name, arguments):
        raise self._exc


class TestErrorsBecomeShapedNotTracebacks:
    """Workstream 7 layer 5. A traceback crossing the transport tells the
    model nothing actionable and can carry account ids, ARNs and server
    filesystem paths into a chat transcript."""

    def test_an_exception_becomes_a_jsonrpc_error(self):
        resp = _call("read-only", "list_clusters", server=_Boom(RuntimeError("kaboom")))
        assert "error" in resp and "result" not in resp
        assert resp["error"]["code"] == base._INTERNAL_ERROR

    def test_the_error_names_the_exception_type(self):
        """The type is the one piece a caller can act on."""
        resp = _call("read-only", "list_clusters", server=_Boom(KeyError("region")))
        assert "KeyError" in resp["error"]["message"]

    def test_no_traceback_text_crosses_the_transport(self):
        resp = _call("read-only", "list_clusters", server=_Boom(RuntimeError("kaboom")))
        blob = json.dumps(resp)
        assert "Traceback" not in blob
        assert "mcp_server/handlers" not in blob
        assert ".py\", line" not in blob

    def test_a_pcluster_lib_exception_is_translated_too(self):
        from pcluster.api.errors import NotFoundException

        resp = _call("read-only", "list_clusters", server=_Boom(NotFoundException("gone")))
        assert resp["error"]["code"] == base._INTERNAL_ERROR
        assert "NotFoundException" in resp["error"]["message"]

    def test_a_baseexception_subclass_is_not_swallowed(self):
        """KeyboardInterrupt/SystemExit are not errors to report as tool
        failures -- catching them would turn a Lambda timeout or a shutdown
        into a misleading tool result."""
        with pytest.raises(SystemExit):
            _call("read-only", "list_clusters", server=_Boom(SystemExit(1)))


class TestResultShaping:
    class _Ok:
        def __init__(self, value):
            self._value = value

        async def list_tools(self):
            return []

        async def call_tool(self, name, arguments):
            return self._value

    def test_a_plain_value_becomes_a_text_content_block(self):
        resp = _call("read-only", "list_clusters", server=self._Ok({"a": 1}))
        assert resp["result"]["content"][0]["type"] == "text"
        assert json.loads(resp["result"]["content"][0]["text"]) == {"a": 1}

    def test_an_already_shaped_result_passes_through(self):
        shaped = {"content": [{"type": "text", "text": "hi"}]}
        resp = _call("read-only", "list_clusters", server=self._Ok(shaped))
        assert resp["result"] is shaped

    def test_unserializable_values_do_not_raise(self):
        """A core function returning something json cannot encode must not
        turn a successful call into a 500."""
        resp = _call("read-only", "list_clusters", server=self._Ok(object()))
        assert resp["result"]["content"][0]["type"] == "text"


class TestDeleteIsConfirmationGated:
    """delete_cluster is the first tool wired to the preview/execute gate,
    so this is where the token machinery is exercised end to end rather
    than as a unit.

    The gate is not authentication (see mcp_server/confirmation_token.py);
    these pin that what executes is what was previewed, never that only an
    authorized caller can execute.
    """

    def _tools(self):
        from fastmcp import FastMCP

        from mcp_server.server import _INSTRUCTIONS
        from mcp_server.tools import register_tools

        mcp = FastMCP(name="t", instructions=_INSTRUCTIONS)
        register_tools(mcp, remote=True)
        return mcp

    def test_preview_and_delete_land_on_different_tiers(self):
        """Deliberate, and the reason confirmation_token.py must be one
        shared module rather than duplicated: the token is minted by the
        read-only tier and verified by the stack-mutation tier, which are
        separate deployment packages."""
        assert TOOL_TIERS["preview_cluster_delete"] == "read-only"
        assert TOOL_TIERS["delete_cluster"] == "stack-mutation"

    def test_delete_requires_a_token_argument(self):
        import asyncio

        tools = {t.name: t for t in asyncio.run(self._tools().list_tools())}
        schema = tools["delete_cluster"].to_mcp_tool().inputSchema
        assert "confirmation_token" in schema.get("required", [])

    def test_preview_does_not_require_one(self):
        import asyncio

        tools = {t.name: t for t in asyncio.run(self._tools().list_tools())}
        schema = tools["preview_cluster_delete"].to_mcp_tool().inputSchema
        assert "confirmation_token" not in schema.get("properties", {})

    def test_a_token_minted_for_one_cluster_does_not_delete_another(self):
        """The property the whole gate exists for."""
        from mcp_server.confirmation_token import TokenMismatch, mint, verify

        token = mint("delete_cluster",
                     {"cluster_name": "osiris", "delete_s3_bucketname": True})
        with pytest.raises(TokenMismatch):
            verify(token, "delete_cluster",
                   {"cluster_name": "production", "delete_s3_bucketname": True})

    def test_a_token_does_not_survive_a_changed_bucket_option(self):
        """Previewing a delete that retains the S3 bucket must not
        authorize one that destroys it."""
        from mcp_server.confirmation_token import TokenMismatch, mint, verify

        token = mint("delete_cluster",
                     {"cluster_name": "osiris", "delete_s3_bucketname": False})
        with pytest.raises(TokenMismatch):
            verify(token, "delete_cluster",
                   {"cluster_name": "osiris", "delete_s3_bucketname": True})


class TestUnimplementedIsHonest:
    def test_nothing_is_unimplemented(self):
        """Every routed tool now has a wrapper. Pinned rather than
        deleted: a tool added to TOOL_TIERS without one must show up here
        as a deliberate entry, not as dead surface the router forwards
        into a handler that cannot serve it."""
        assert UNIMPLEMENTED == frozenset()

    def test_every_other_routed_tool_is_actually_served(self):
        served = set()
        for tier in HANDLER_TIERS:
            served |= {t["name"] for t in _list(tier)["result"]["tools"]}
        assert set(TOOL_TIERS) - UNIMPLEMENTED == served


class TestTheDeleteWrapperActuallyCallsTheGate:
    """Added after mutation testing: the unit tests for verify() all passed
    with the verify() *call* deleted from the wrapper entirely, and with
    wait flipped to True. Testing a guard's implementation is not testing
    that the guard is invoked."""

    def _delete_tool(self):
        import asyncio

        from fastmcp import FastMCP

        from mcp_server.server import _INSTRUCTIONS
        from mcp_server.tools import register_tools

        mcp = FastMCP(name="t", instructions=_INSTRUCTIONS)
        register_tools(mcp, remote=True)
        tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
        return tools["delete_cluster"].fn

    def test_a_bad_token_is_rejected_by_the_wrapper(self):
        from mcp_server.confirmation_token import ConfirmationTokenError

        with pytest.raises(ConfirmationTokenError):
            self._delete_tool()(cluster_name="anything", confirmation_token="not-a-token")

    def test_the_gate_runs_before_the_cluster_lookup(self):
        """A bad token must fail as a bad token, not as 'no such cluster'.
        Otherwise the error message tells a tokenless caller which cluster
        names exist, and the gate sits behind an unrelated lookup."""
        from mcp_server.confirmation_token import ConfirmationTokenError

        with pytest.raises(ConfirmationTokenError):
            self._delete_tool()(
                cluster_name="definitely-not-a-real-cluster",
                confirmation_token="v1.1.deadbeef",
            )

    def test_an_expired_token_is_rejected_by_the_wrapper(self):
        from mcp_server.confirmation_token import ExpiredToken, mint

        stale = mint("delete_cluster",
                     {"cluster_name": "osiris", "delete_s3_bucketname": True},
                     issued_at=1)
        with pytest.raises(ExpiredToken):
            self._delete_tool()(cluster_name="osiris", confirmation_token=stale)

    def test_delete_is_kicked_off_without_waiting(self):
        """A tool call cannot block for a 5-10 minute teardown. Pinned on
        the call site because flipping wait=True is silent at test time
        otherwise -- it was a surviving mutation."""
        import ast
        import inspect

        import mcp_server.tools as tools_mod

        src = inspect.getsource(tools_mod.register_tools)
        tree = ast.parse(src.lstrip())
        found = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name != "delete_cluster":
                continue
            for call in ast.walk(node):
                if isinstance(call, ast.Call) and getattr(call.func, "id", "") == "core_delete_cluster":
                    for kw in call.keywords:
                        if kw.arg == "wait":
                            found.append(ast.literal_eval(kw.value))
        assert found == [False], f"delete_cluster must pass wait=False, got {found}"


class TestTheHandlerDrivesTheRealFastmcpApi:
    """The bug this class exists for, and the reason the rest of this file
    could not see it.

    `base.handle` called `mcp._call_tool(...)`. FastMCP 3.4.7 has no such
    method -- it has `call_tool`. Every `tools/call` test above passes
    `server=<stub>`, and the stubs define `_call_tool` because that is
    what the production code called, so the stub and the code agreed with
    each other and *both* disagreed with FastMCP. On a real Lambda every
    single tool call would have raised AttributeError, been caught by the
    broad `except Exception`, and returned a shaped internal error -- so
    even the failure would have looked like a well-handled tool fault
    rather than a broken dispatch path.

    Two things follow, and both are asserted here rather than in a stubbed
    test: the method must exist on the real object, and the object's
    return value must survive translation. `call_tool` returns a
    `ToolResult` carrying pydantic content models, not a dict -- fed to
    the old `_to_content` it fell through to the catch-all and the entire
    response became the repr of the ToolResult as one text block.

    Workstream 7 layer 2 names FastMCP's in-memory Client for exactly this
    reason: a stub proves the code calls *something*, only the real object
    proves it calls the right thing.
    """

    def _server(self):
        from fastmcp import FastMCP

        mcp = FastMCP(name="real")

        @mcp.tool
        def list_clusters(region: str | None = None) -> dict:
            return {"clusters": ["osiris"], "region": region}

        return mcp

    def test_a_tool_call_against_a_real_server_succeeds(self):
        resp = base.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "list_clusters", "arguments": {}}},
            tier="read-only", server=self._server(),
        )
        assert "error" not in resp, resp

    def test_the_tool_result_is_translated_not_stringified(self):
        """The half a corrected method name alone would not fix.

        The assertion is that the text block *parses as JSON equal to the
        tool's own return value*, not that it mentions something the tool
        returned. The first version checked `"osiris" in text` and
        `"ToolResult" not in text`, and both passed against the broken
        output -- because falling through to the catch-all yields
        pydantic's `__str__`, which embeds the real payload verbatim
        (`content=[TextContent(text='{"clusters":["osiris"]...')]...`) and,
        being `__str__` rather than `__repr__`, never names the class. A
        substring check cannot tell that apart from a correct result; a
        round-trip through `json.loads` can.
        """
        resp = base.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "list_clusters", "arguments": {}}},
            tier="read-only", server=self._server(),
        )
        blocks = resp["result"]["content"]
        assert [b["type"] for b in blocks] == ["text"]
        assert json.loads(blocks[0]["text"]) == {
            "clusters": ["osiris"], "region": None,
        }

    def test_the_structured_content_survives(self):
        """MCP carries the tool's typed return alongside the text block,
        and it is what a caller should read rather than re-parsing text."""
        resp = base.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "list_clusters", "arguments": {}}},
            tier="read-only", server=self._server(),
        )
        assert resp["result"]["structuredContent"] == {
            "clusters": ["osiris"], "region": None,
        }

    def test_the_content_blocks_are_json_serializable(self):
        """They arrive as pydantic models and have to cross the transport
        as plain dicts -- json.dumps on a model raises."""
        resp = base.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "list_clusters", "arguments": {}}},
            tier="read-only", server=self._server(),
        )
        json.loads(json.dumps(resp))

    def test_arguments_actually_reach_the_tool(self):
        """Guards the argument path independently: a dispatch that calls
        the right method with the wrong payload returns a plausible result
        for the wrong input."""
        resp = base.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "list_clusters",
                        "arguments": {"region": "us-east-2"}}},
            tier="read-only", server=self._server(),
        )
        assert "us-east-2" in resp["result"]["content"][0]["text"]

    def test_the_method_the_handler_calls_exists_on_fastmcp(self):
        """Stated directly, so the next rename is a failing test rather
        than a Lambda that returns a shaped error for every call."""
        from fastmcp import FastMCP

        assert hasattr(FastMCP, "call_tool")
        assert not hasattr(FastMCP, "_call_tool"), (
            "if FastMCP grows a _call_tool again, re-check which one "
            "base.handle should be using"
        )

    def test_a_raising_tool_becomes_a_shaped_error_not_a_traceback(self):
        """Layer 5 against the real object rather than a _Boom stub.

        The stubbed error tests above raise from the stub's own
        `call_tool`. The real object wraps the tool's exception in
        FastMCP's own `ToolError` first (verified against 3.4.7 -- it
        raises rather than returning a ToolResult with is_error set), so
        what base.handle actually catches, and what a deployed Lambda
        actually produces, is a different exception type carrying a
        rewritten message. Only this test sees that.
        """
        from fastmcp import FastMCP

        mcp = FastMCP(name="real")

        @mcp.tool
        def list_clusters() -> dict:
            raise RuntimeError("cluster vanished")

        resp = base.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "list_clusters", "arguments": {}}},
            tier="read-only", server=mcp,
        )
        blob = json.dumps(resp)
        assert "cluster vanished" in blob
        assert "Traceback" not in blob and "File \"" not in blob, (
            "a traceback crossing the transport tells the model nothing "
            "actionable and can carry ARNs and server paths into a chat"
        )
        json.loads(blob)
        # An error response, not a result carrying an error-shaped
        # payload: a client distinguishes the two structurally, and a
        # failing tool that returns `result` reads as a success whose
        # output happens to mention a problem.
        assert "error" in resp and "result" not in resp
        assert resp["error"]["code"] == base._INTERNAL_ERROR
        assert "ToolError" in resp["error"]["message"], (
            "the type is the one piece a caller can act on, and on this "
            "path it is FastMCP's wrapper type rather than the tool's own"
        )


class TestTheCreateWrapperActuallyCallsTheGate:
    """The delete wrapper got this guard after a mutation showed its
    verify() call could be deleted silently. create_cluster carried the
    same gate and none of the guard -- deleting its verify() call passed
    all 2,777 tests.

    The asymmetry mattered more than it looks. A create is the one
    operation that commits ongoing spend: it is kicked off without waiting,
    so an ungated build starts, the call returns, and nothing surfaces
    until someone reads a bill. Destruction is at least a discrete act on a
    named cluster.
    """

    def _create_tool(self):
        import asyncio

        from fastmcp import FastMCP

        from mcp_server.server import _INSTRUCTIONS
        from mcp_server.tools import register_tools

        mcp = FastMCP(name="t", instructions=_INSTRUCTIONS)
        register_tools(mcp, remote=True)
        tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
        return tools["create_cluster"].fn

    _ARGS = dict(
        cluster_name="osiris", cluster_owner="rmarable",
        cluster_owner_email="rmarable@gmail.com", az="us-east-1a",
        headnode_instance_type="c8g.large",
    )

    def test_a_bad_token_is_rejected_by_the_wrapper(self):
        from mcp_server.confirmation_token import ConfirmationTokenError

        with pytest.raises(ConfirmationTokenError):
            self._create_tool()(confirmation_token="not-a-token", **self._ARGS)

    def test_the_gate_runs_before_anything_that_could_build(self):
        """verify() sits above _reject_denied and build_make_cluster_params
        on purpose: a tokenless caller must be refused as tokenless, not be
        told which parameter it got wrong -- otherwise the gate is
        reachable-around by fixing whatever the later error names."""
        from mcp_server.confirmation_token import ConfirmationTokenError

        with pytest.raises(ConfirmationTokenError):
            self._create_tool()(
                confirmation_token="v1.1.deadbeef",
                overrides={"pre_install_script": "/tmp/evil.sh"},
                **self._ARGS,
            )

    def test_an_expired_token_is_rejected_by_the_wrapper(self):
        from mcp_server.confirmation_token import ExpiredToken, mint

        params = dict(self._ARGS)
        params["overrides"] = {}
        stale = mint("create_cluster", params, issued_at=1)
        with pytest.raises(ExpiredToken):
            self._create_tool()(confirmation_token=stale, **self._ARGS)

    def test_a_token_minted_for_a_different_cluster_is_rejected(self):
        """The token binds the parameters, not just the operation -- a
        preview of a small cluster must not authorize a large one."""
        from mcp_server.confirmation_token import ConfirmationTokenError, mint

        other = dict(self._ARGS)
        other["headnode_instance_type"] = "c8g.24xlarge"
        other["overrides"] = {}
        token = mint("create_cluster", other)
        with pytest.raises(ConfirmationTokenError):
            self._create_tool()(confirmation_token=token, **self._ARGS)

    def test_a_delete_token_cannot_authorize_a_create(self):
        """The operation name is part of what is signed."""
        from mcp_server.confirmation_token import ConfirmationTokenError, mint

        params = dict(self._ARGS)
        params["overrides"] = {}
        token = mint("delete_cluster", params)
        with pytest.raises(ConfirmationTokenError):
            self._create_tool()(confirmation_token=token, **self._ARGS)

    def test_a_correctly_minted_token_is_accepted(self, monkeypatch):
        """Vacuity guard, and it caught a real gap: the five rejection
        tests above all passed with `token_params` replaced by `{}`, which
        unbinds the token from the parameters entirely. Only asserting that
        the *right* token still works can see that -- a gate that rejects
        everything satisfies every negative test ever written.
        """
        import mcp_server.tools as tools_mod
        from mcp_server.confirmation_token import mint

        seen = {}
        monkeypatch.setattr(
            tools_mod, "core_create_cluster",
            lambda **kw: seen.update(kw) or {"status": "kicked off"},
        )
        # This stub used to fabricate `region` on the params object, which
        # is exactly what hid the AttributeError create_cluster raised on
        # every real call -- MakeClusterParams has no such field. It builds
        # a bare namespace now, and the region comes from the resolver,
        # stubbed here because this test is about the token gate;
        # TestCreateClusterResolvesTheRegionItBuildsIn drives the real one.
        monkeypatch.setattr(
            tools_mod, "build_make_cluster_params",
            lambda **kw: types.SimpleNamespace(),
        )
        monkeypatch.setattr(
            tools_mod, "resolve_region_from_az", lambda az: "us-east-1"
        )

        params = dict(self._ARGS)
        params["overrides"] = {}
        params["defaults"] = None
        token = mint("create_cluster", params)
        result = self._create_tool()(confirmation_token=token, **self._ARGS)

        assert seen, "the build was never reached with a valid token"
        assert result["status"] == "kicked off"
