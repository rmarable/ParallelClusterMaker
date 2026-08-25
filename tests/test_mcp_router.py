"""Workstream 5: the Router Lambda's JSON-RPC dispatch.

Two classes of property here, and the second is the reason this file
exists at all.

The dispatch tests are ordinary: tool calls reach the right tier, unknown
methods and tools get the right JSON-RPC error codes, notifications
produce no response.

The agreement tests pin three separately-maintained places that must name
the same Lambda functions -- mcp_server/tiers.py, _MCP_LAMBDA_TIERS in
src/pcluster_core.py, and templates/MCPRouterLambda.json_src. They cannot
be collapsed into one source (the router's package must not import
pcluster_core, and src/ should not import mcp_server), so a test is the
only thing standing between a rename and a router that is denied at
runtime on an ARN matching nothing it can reach.
"""

import io
import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from mcp_server import router  # noqa: E402
from mcp_server.tiers import FUNCTION_NAMES, function_name_for  # noqa: E402


class _Recorder:
    """Stands in for lambda:InvokeFunction. Records (tier, body) and
    returns a canned per-tier response."""

    def __init__(self, tools_by_tier=None, result=None):
        self.calls = []
        self._tools = tools_by_tier or {}
        self._result = result if result is not None else {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}

    def __call__(self, tier, body):
        self.calls.append((tier, body))
        if body.get("method") == "tools/list":
            return {
                "jsonrpc": "2.0", "id": body.get("id"),
                "result": {"tools": self._tools.get(tier, [])},
            }
        return self._result


def _call(name, **args):
    return {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {"name": name, "arguments": args}}


class TestToolCallRouting:
    @pytest.mark.parametrize("tool,tier", sorted(router._TOOL_ROUTES.items()))
    def test_each_tool_reaches_its_declared_tier(self, tool, tier):
        rec = _Recorder()
        router.handle(_call(tool), invoke=rec)
        assert rec.calls and rec.calls[0][0] == tier

    def test_the_handler_response_is_returned_unchanged(self):
        """The router is a forwarder; rewrapping a handler's response would
        be a place for the result shape to drift."""
        canned = {"jsonrpc": "2.0", "id": 7, "result": {"content": [{"type": "text", "text": "hi"}]}}
        rec = _Recorder(result=canned)
        assert router.handle(_call("list_clusters"), invoke=rec) is canned

    def test_the_whole_body_is_forwarded_not_just_the_arguments(self):
        """The handler needs the full JSON-RPC envelope to answer with a
        matching id."""
        rec = _Recorder()
        body = _call("list_clusters", region="us-east-2")
        router.handle(body, invoke=rec)
        assert rec.calls[0][1] is body

    def test_an_unknown_tool_is_invalid_params_and_never_invoked(self):
        rec = _Recorder()
        resp = router.handle(_call("rm_minus_rf"), invoke=rec)
        assert resp["error"]["code"] == router._INVALID_PARAMS
        assert rec.calls == [], "an unroutable tool must not reach any handler"

    def test_the_unknown_tool_error_lists_what_is_routable(self):
        resp = router.handle(_call("nope"), invoke=_Recorder())
        assert "list_clusters" in resp["error"]["message"]

    def test_non_object_params_are_rejected(self):
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": "oops"}
        resp = router.handle(body, invoke=_Recorder())
        assert resp["error"]["code"] == router._INVALID_PARAMS


class TestProtocolMethodsAreTerminatedLocally:
    """The gap the plan's one-line router description leaves open.
    Forwarding these is quietly wrong rather than loudly broken."""

    def test_initialize_is_answered_by_the_router(self):
        rec = _Recorder()
        resp = router.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"}, invoke=rec)
        assert resp["result"]["protocolVersion"]
        assert rec.calls == [], "initialize must not be answered by one tier"

    def test_ping_is_answered_by_the_router(self):
        rec = _Recorder()
        resp = router.handle({"jsonrpc": "2.0", "id": 2, "method": "ping"}, invoke=rec)
        assert resp["result"] == {}
        assert rec.calls == []

    def test_tools_list_fans_out_to_every_handler_tier(self):
        """Sent to a single handler it would return a quarter of the
        surface and the client would conclude the rest does not exist."""
        rec = _Recorder(tools_by_tier={t: [{"name": f"tool_{t}"}] for t in router._FANOUT_TIERS})
        resp = router.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, invoke=rec)
        assert {t for t, _ in rec.calls} == set(router._FANOUT_TIERS)
        assert {t["name"] for t in resp["result"]["tools"]} == {
            f"tool_{t}" for t in router._FANOUT_TIERS
        }

    def test_tools_list_does_not_fan_out_to_the_auth_tiers(self):
        """register/authorizer serve the Workstream 6 auth flow, not tools;
        invoking them here would be both wrong and an IAM grant the router
        does not have."""
        rec = _Recorder()
        router.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, invoke=rec)
        assert {t for t, _ in rec.calls}.isdisjoint({"register", "authorizer", "router"})

    def test_tools_list_deduplicates(self):
        rec = _Recorder(tools_by_tier={t: [{"name": "dup"}] for t in router._FANOUT_TIERS})
        resp = router.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, invoke=rec)
        assert [t["name"] for t in resp["result"]["tools"]] == ["dup"]

    def test_a_tier_returning_nothing_does_not_break_the_merge(self):
        """One handler erroring must not make the whole tool list
        unavailable."""
        rec = _Recorder(tools_by_tier={"read-only": [{"name": "a"}]})
        resp = router.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, invoke=rec)
        assert [t["name"] for t in resp["result"]["tools"]] == ["a"]

    def test_notifications_produce_no_response(self):
        rec = _Recorder()
        assert router.handle(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}, invoke=rec
        ) is None
        assert rec.calls == []

    def test_an_unknown_method_is_method_not_found(self):
        resp = router.handle({"jsonrpc": "2.0", "id": 4, "method": "resources/read"},
                             invoke=_Recorder())
        assert resp["error"]["code"] == router._METHOD_NOT_FOUND

    def test_a_non_object_body_is_invalid_request(self):
        resp = router.handle(["not", "an", "object"], invoke=_Recorder())
        assert resp["error"]["code"] == router._INVALID_REQUEST

    def test_a_missing_method_is_invalid_request(self):
        resp = router.handle({"jsonrpc": "2.0", "id": 5}, invoke=_Recorder())
        assert resp["error"]["code"] == router._INVALID_REQUEST

    def test_the_response_id_matches_the_request(self):
        for method in ("initialize", "ping"):
            resp = router.handle({"jsonrpc": "2.0", "id": 99, "method": method},
                                 invoke=_Recorder())
            assert resp["id"] == 99


class TestTierNamesAgreeEverywhere:
    """Three separately-maintained places, no shared import possible."""

    def _router_policy_functions(self):
        raw = open(os.path.join(REPO_ROOT, "templates", "MCPRouterLambda.json_src")).read()
        doc = json.loads(raw.replace("<AWS_REGION>", "us-east-2").replace("<AWS_ACCOUNT_ID>", "1" * 12))
        arns = []
        for stmt in doc["Statement"]:
            r = stmt["Resource"]
            arns += [r] if isinstance(r, str) else r
        return {a.rsplit(":function:", 1)[-1] for a in arns}

    def test_tiers_module_matches_pcluster_core(self):
        import pcluster_core

        from_core = {t: fn for t, (fn, _p) in pcluster_core._MCP_LAMBDA_TIERS.items()}
        assert FUNCTION_NAMES == from_core

    def test_the_router_policy_covers_every_fanout_tier(self):
        """The IAM consequence of the routing table: every tier the router
        can invoke must be in its policy, or that call is denied."""
        allowed = self._router_policy_functions()
        needed = {function_name_for(t) for t in router._FANOUT_TIERS}
        assert needed <= allowed, f"router cannot invoke: {sorted(needed - allowed)}"

    def test_the_router_policy_grants_nothing_extra(self):
        """The other direction -- a stale ARN in the policy is a grant
        pointing at a function nothing routes to."""
        allowed = self._router_policy_functions()
        needed = {function_name_for(t) for t in router._FANOUT_TIERS}
        assert allowed == needed

    def test_every_routed_tier_is_a_real_tier(self):
        assert set(router._TOOL_ROUTES.values()) <= set(FUNCTION_NAMES)

    def test_fanout_tiers_are_exactly_the_routed_tiers(self):
        """If a tool routes to a tier tools/list never queries, that tool
        is callable but never advertised -- invisible to the model."""
        assert set(router._FANOUT_TIERS) == set(router._TOOL_ROUTES.values())

    def test_an_unknown_tier_name_fails_loudly(self):
        with pytest.raises(KeyError, match="unknown MCP tier"):
            function_name_for("nope")


class TestTheRouterPackageStaysLean:
    def test_it_imports_neither_pcluster_core_nor_fastmcp(self):
        """Its near-zero IAM is only meaningful if its deployment package
        is correspondingly small; pulling in pcluster_core would drag boto3,
        PCluster and Jinja2 behind it."""
        import ast

        # Parsed, not grepped. A text search matches this module's own
        # docstring -- which says it does not import these -- and fails on
        # the prose. The first version of this test did exactly that.
        tree = ast.parse(open(os.path.join(REPO_ROOT, "mcp_server", "router.py")).read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert not ({"pcluster_core", "fastmcp"} & imported), (
            f"router.py imports {sorted({'pcluster_core', 'fastmcp'} & imported)}"
        )

    def test_the_import_detector_would_catch_a_real_import(self):
        """Vacuity guard: prove the AST walk sees a banned import rather
        than passing because it inspects the wrong nodes."""
        import ast

        tree = ast.parse("def f():\n    import pcluster_core\n")
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
        assert "pcluster_core" in imported


class TestAFailingHandlerLambdaDoesNotLeakItsStackTrace:
    """Workstream 7 layer 5, at the router boundary rather than inside a
    handler.

    A failing Lambda still returns **StatusCode 200**. The failure shows up
    as `FunctionError` ("Handled"/"Unhandled") with Lambda's own error
    object as the payload -- `{"errorMessage", "errorType", "stackTrace"}`
    -- not the handler's response. The router forwarded that verbatim,
    which put absolute server filesystem paths into the MCP response *and*
    returned something that is not a JSON-RPC message at all.

    The handler's own broad `except` cannot cover this: it only sees a
    failing tool. An import error on cold start, an OOM kill, or the
    function hitting its timeout all happen outside it, and those are
    exactly what produce an unhandled Lambda error.
    """

    _TRACE = [
        '  File "/var/task/mcp_server/handlers/base.py", line 94, in handle\n',
        '  File "/var/task/src/pcluster_core.py", line 2201, in core_list_clusters\n',
    ]

    def _response(self, payload, function_error=None):
        import io

        body = {"Payload": io.BytesIO(json.dumps(payload).encode())}
        if function_error:
            body["FunctionError"] = function_error
        return body

    def _unhandled(self):
        return self._response({
            "errorMessage": "Unable to import module 'mcp_server.handlers.read_only'",
            "errorType": "Runtime.ImportModuleError",
            "stackTrace": self._TRACE,
        }, function_error="Unhandled")

    def test_the_stack_trace_never_reaches_the_client(self):
        out = router.unwrap_invocation(self._unhandled(), tier="read-only", request_id=7)
        blob = json.dumps(out)
        assert "/var/task/" not in blob
        assert "stackTrace" not in blob
        assert "File \"" not in blob

    def test_it_becomes_a_wellformed_jsonrpc_error(self):
        """The other half: Lambda's error object has no `jsonrpc` or `id`,
        so forwarding it is a protocol violation the client cannot even
        parse as a failure."""
        out = router.unwrap_invocation(self._unhandled(), tier="read-only", request_id=7)
        assert out["jsonrpc"] == "2.0"
        assert out["id"] == 7
        assert out["error"]["code"] == router._INTERNAL_ERROR

    def test_the_cause_is_still_named(self):
        """Dropping the trace must not mean dropping the diagnosis -- the
        type and message are what a caller can act on, and an import error
        on cold start is a deployment bug that has to be findable."""
        out = router.unwrap_invocation(self._unhandled(), tier="read-only", request_id=7)
        message = out["error"]["message"]
        assert "Runtime.ImportModuleError" in message
        assert "read-only" in message
        assert "Unhandled" in message

    def test_a_handled_error_is_translated_too(self):
        """`FunctionError` is "Handled" when the function caught and
        re-raised; still not a JSON-RPC response."""
        resp = self._response(
            {"errorMessage": "boom", "errorType": "RuntimeError",
             "stackTrace": self._TRACE},
            function_error="Handled",
        )
        out = router.unwrap_invocation(resp, tier="fleet-toggle", request_id=1)
        assert "error" in out and "stackTrace" not in json.dumps(out)

    def test_a_successful_response_passes_through_untouched(self):
        """Vacuity guard: the translation must not fire on the happy path,
        or every successful call becomes an error."""
        good = {"jsonrpc": "2.0", "id": 3, "result": {"tools": []}}
        assert router.unwrap_invocation(
            self._response(good), tier="read-only", request_id=3
        ) == good

    def test_an_unreadable_payload_is_reported_not_raised(self):
        """A truncated or non-JSON payload must not take the router down
        with an uncaught exception -- that becomes an unhandled error in
        the *router*, and the client sees nothing at all."""
        import io

        out = router.unwrap_invocation(
            {"Payload": io.BytesIO(b"not json")}, tier="read-only", request_id=2,
        )
        assert out["error"]["code"] == router._INTERNAL_ERROR

    def test_the_lambda_entry_point_uses_the_translation(self):
        """The logic is worthless if the real invoke path bypasses it.
        Asserted on the source, since _invoke is defined inside
        lambda_handler and cannot be reached any other way."""
        import inspect

        source = inspect.getsource(router.lambda_handler)
        assert "unwrap_invocation(" in source
        assert "json.loads(response[\"Payload\"].read())" not in source


class TestAnInvokeThatFailsOutrightIsNotLeaked:
    """`unwrap_invocation` covers a handler that *ran* and failed: Lambda
    answers 200 with `FunctionError` and its own error object. `invoke`
    itself can fail before any response exists -- a tier that is not
    deployed, an AccessDenied on the router's role, throttling -- and that
    path was uncaught, so the exception propagated out of `lambda_handler`
    and Lambda serialized it verbatim.

    Observed against a partially deployed topology: `FunctionError:
    Unhandled`, with `"/var/task/mcp_server/router.py"` in the payload.
    That is the same pair of bugs `unwrap_invocation` exists to prevent --
    an internal path disclosed, and a body that is not a JSON-RPC response
    -- arriving through the one door it does not watch.
    """

    def _handler_with_failing_invoke(self, monkeypatch, exc):
        import mcp_server.router as router

        class _Client:
            def invoke(self, **kw):
                raise exc

        class _Boto:
            @staticmethod
            def client(name, *a, **k):
                return _Client()

        monkeypatch.setitem(sys.modules, "boto3", _Boto)
        return router

    def test_a_missing_tier_is_a_json_rpc_error(self, monkeypatch):
        router = self._handler_with_failing_invoke(
            monkeypatch, RuntimeError("Function not found: ...:pclustermaker-mcp-stack-mutation"),
        )
        event = {"body": json.dumps({
            "jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {"name": "delete_cluster", "arguments": {}}})}
        out = router.lambda_handler(event, None)
        body = json.loads(out["body"])
        assert out["statusCode"] == 200
        assert body["jsonrpc"] == "2.0" and body["id"] == 7
        assert body["error"]["code"] == -32603

    def test_no_internal_path_reaches_the_client(self, monkeypatch):
        """The disclosure half. A traceback naming /var/task tells a caller
        the runtime layout; the tier name alone is what they can act on."""
        router = self._handler_with_failing_invoke(
            monkeypatch, RuntimeError('File "/var/task/mcp_server/router.py", line 210'),
        )
        event = {"body": json.dumps({
            "jsonrpc": "2.0", "id": 8, "method": "tools/call",
            "params": {"name": "delete_cluster", "arguments": {}}})}
        out = router.lambda_handler(event, None)
        assert "/var/task" not in out["body"], out["body"]
        assert "stack-mutation" in out["body"], "the tier has to be named"

    def test_the_exception_type_is_reported_not_its_message(self, monkeypatch):
        """Enough to diagnose (ResourceNotFound vs AccessDenied vs throttle)
        without echoing an arbitrary AWS string back to a caller."""
        router = self._handler_with_failing_invoke(
            monkeypatch, PermissionError("arn:aws:iam::123456789012:role/secret-role denied"),
        )
        event = {"body": json.dumps({
            "jsonrpc": "2.0", "id": 9, "method": "tools/call",
            "params": {"name": "stop_fleet", "arguments": {}}})}
        out = router.lambda_handler(event, None)
        assert "PermissionError" in out["body"]
        assert "secret-role" not in out["body"]

    def test_a_working_invoke_is_untouched(self, monkeypatch):
        """Vacuity guard: the net must not swallow successful responses."""
        import mcp_server.router as router

        class _Client:
            def invoke(self, **kw):
                return {"StatusCode": 200,
                        "Payload": io.BytesIO(json.dumps(
                            {"jsonrpc": "2.0", "id": 10, "result": {"ok": True}}
                        ).encode())}

        class _Boto:
            @staticmethod
            def client(name, *a, **k):
                return _Client()

        monkeypatch.setitem(sys.modules, "boto3", _Boto)
        event = {"body": json.dumps({
            "jsonrpc": "2.0", "id": 10, "method": "tools/call",
            "params": {"name": "list_clusters", "arguments": {}}})}
        out = router.lambda_handler(event, None)
        assert json.loads(out["body"])["result"] == {"ok": True}
