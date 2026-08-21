"""Workstream 7 layer 1: MCP tool-schema tests, plus the local/remote
tool-set split Workstream 5 builds.

The split test is the one the migration plan names explicitly as a
required cross-cutting regression guard: "a test asserting the remote
dispatcher's registered tool set excludes SSH-key operations while the
local stdio instance's includes them." It is a guard against a specific,
easy mistake -- registering a tool on the shared wrapper module and
forgetting it becomes remotely callable -- which is silent at runtime and
only visible as an unexpectedly-present tool in a deployed server.

Schema tests matter here beyond tidiness: FastMCP derives each tool's JSON
schema from the wrapper's own type annotations, so an annotation that
drifts from what the underlying core function accepts produces a tool the
model can call with arguments the core function will reject.
"""

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from mcp_server.server import build_local, build_remote  # noqa: E402
from mcp_server.tiers import TOOL_TIERS  # noqa: E402
from mcp_server.tools import _LOCAL_ONLY  # noqa: E402


async def _tool_names(mcp):
    return {t.name for t in await mcp.list_tools()}


class TestLocalRemoteToolSplit:
    @pytest.mark.asyncio
    async def test_local_instance_exposes_the_ssh_key_tool(self):
        assert "rotate_cluster_key" in await _tool_names(build_local())

    @pytest.mark.asyncio
    async def test_remote_instance_excludes_the_ssh_key_tool(self):
        """The plan's named regression guard. Key material never reaches
        the remote transport, and the .pem this writes has to land on the
        operator's own filesystem -- Lambda's /tmp is ephemeral and never
        reachable from a Claude web session."""
        assert "rotate_cluster_key" not in await _tool_names(build_remote())

    @pytest.mark.asyncio
    async def test_remote_instance_excludes_the_grafana_tunnel(self):
        """An SSH local port forward is only meaningful when 'local' means
        the caller's own machine, which a remote dispatcher categorically
        is not -- there is no safely-useful remote version of this tool."""
        assert "manage_grafana_tunnel" not in await _tool_names(build_remote())

    @pytest.mark.asyncio
    async def test_remote_is_a_strict_subset_of_local(self):
        """Vacuity guard in the other direction: the remote instance must
        not gain a tool the local one lacks. Without this, 'excluded from
        remote' could be satisfied by two independently-drifting tool
        sets rather than one set minus a known exclusion list."""
        local = await _tool_names(build_local())
        remote = await _tool_names(build_remote())
        assert remote < local, f"remote has tools local lacks: {sorted(remote - local)}"

    @pytest.mark.asyncio
    async def test_the_difference_is_exactly_the_declared_exclusion_list(self):
        """Pins the split to _LOCAL_ONLY rather than to whatever the two
        builders happen to produce -- so adding a local-only tool without
        declaring it fails here instead of silently shipping remotely."""
        local = await _tool_names(build_local())
        remote = await _tool_names(build_remote())
        assert local - remote == set(_LOCAL_ONLY)

    @pytest.mark.asyncio
    async def test_every_declared_exclusion_actually_exists(self):
        """A typo in _LOCAL_ONLY would silently exclude nothing at all --
        the name would simply never match a registered tool, and the
        difference-set test above would still pass if the typo'd entry
        were the only discrepancy."""
        local = await _tool_names(build_local())
        assert set(_LOCAL_ONLY) <= local, (
            f"_LOCAL_ONLY names tools that are not registered anywhere: "
            f"{sorted(set(_LOCAL_ONLY) - local)}"
        )


async def _mcp_schemas(mcp):
    """The wire-format schemas the model actually receives.

    Deliberately via to_mcp_tool() rather than FunctionTool.parameters:
    the two agree today, but .parameters is FastMCP's internal
    representation while to_mcp_tool() is the MCP-protocol shape that
    actually crosses the transport -- which is the thing whose drift
    would break a caller."""
    return {t.name: t.to_mcp_tool().inputSchema for t in await mcp.list_tools()}


class TestToolSchemas:
    @pytest.mark.asyncio
    async def test_every_tool_declares_a_description(self):
        """An undescribed tool is one the model cannot choose correctly."""
        for tool in await build_local().list_tools():
            assert tool.description, f"{tool.name} has no description"

    @pytest.mark.asyncio
    async def test_every_tool_has_an_object_input_schema(self):
        for name, schema in (await _mcp_schemas(build_local())).items():
            assert schema.get("type") == "object", name

    @pytest.mark.asyncio
    async def test_cluster_name_is_required_where_it_is_taken(self):
        """A cluster-scoped tool whose cluster_name drifted to optional
        would be callable with no target at all."""
        schemas = await _mcp_schemas(build_local())
        for name in ("check_cluster_health", "list_queues", "rotate_cluster_key"):
            assert "cluster_name" in schemas[name].get("required", []), name

    @pytest.mark.asyncio
    async def test_optional_filters_are_not_required(self):
        """list_clusters must be callable with no arguments -- it is the
        natural first call for a model with no context yet."""
        schemas = await _mcp_schemas(build_local())
        assert not schemas["list_clusters"].get("required")


class TestTheSshDependentChecksDegradeOnlyOnTheRemoteTransport:
    """Workstream 7's third exclusion, and the one that is a *degradation*
    rather than an omission: check_cluster_health and diagnose_cluster are
    mostly boto3 and work identically on both transports. Only the
    sub-checks needing the cluster's private key cannot run remotely, and
    those report SKIP via the branch core_check_cluster_health already
    had, rather than a second mechanism.

    Both directions are asserted, because the shipped code got exactly one
    of them wrong: `ssh_available=False` was hardcoded in the wrapper
    shared by both server instances, so the *local* stdio server -- the
    one running on the operator's own laptop, where the .pem actually is
    -- skipped every SSH-dependent check too. That silently downgraded the
    local tool below the CLI it wraps, and `check_slurm` is precisely the
    check that separates "the cluster exists" from "the cluster can run
    work" (see the sinfo constraint in CLAUDE.local.md).

    The assertion is on the value the core function actually receives, not
    on the wrapper's source: `ssh_available=False` and
    `ssh_available=not remote` differ by a few characters and a source
    match cannot tell which instance a given spelling produces.
    """

    def _capture(self, monkeypatch, core_name):
        seen = {}

        def fake(**kwargs):
            seen.update(kwargs)
            return {"checks": [], "sections": []}

        monkeypatch.setattr(f"mcp_server.tools.{core_name}", fake)
        monkeypatch.setattr(
            "mcp_server.tools._require_record",
            lambda name: object(),
        )
        return seen

    @pytest.mark.parametrize("tool_name,core_name", [
        ("check_cluster_health", "core_check_cluster_health"),
        ("diagnose_cluster", "core_diagnose_cluster"),
    ])
    @pytest.mark.asyncio
    async def test_the_local_server_attempts_ssh(
        self, monkeypatch, tool_name, core_name
    ):
        """The key is on disk here. If SSH turns out to be unreachable the
        core function takes its own 'SSH unreachable' branch -- that is a
        different SKIP, with a different reason string, and it is the
        function's call to make, not the wrapper's."""
        seen = self._capture(monkeypatch, core_name)
        await build_local().call_tool(tool_name, {"cluster_name": "osiris"})
        assert seen["ssh_available"] is True

    @pytest.mark.parametrize("tool_name,core_name", [
        ("check_cluster_health", "core_check_cluster_health"),
        ("diagnose_cluster", "core_diagnose_cluster"),
    ])
    @pytest.mark.asyncio
    async def test_the_remote_server_does_not(
        self, monkeypatch, tool_name, core_name
    ):
        """Key material never reaches the remote transport, so attempting
        SSH there is not a degraded check -- it is a guaranteed failure
        reported as a cluster problem."""
        seen = self._capture(monkeypatch, core_name)
        await build_remote().call_tool(tool_name, {"cluster_name": "osiris"})
        assert seen["ssh_available"] is False

    @pytest.mark.asyncio
    async def test_neither_tool_is_excluded_from_the_remote_transport(self):
        """Vacuity guard against "fixing" this by moving both tools into
        _LOCAL_ONLY. The plan is explicit that neither goes dead remotely
        -- they report less there, honestly. Most of what they do is pure
        boto3 and is exactly as useful over the remote transport."""
        remote = await _tool_names(build_remote())
        assert {"check_cluster_health", "diagnose_cluster"} <= remote


class TestEveryWrapperAgreesWithTheCoreFunctionItWraps:
    """Workstream 7 layer 1, as the plan actually specifies it: "assert
    each tool's declared JSON schema matches the wrapped core function's
    actual signature, catching the case where a core function's params
    change but its tool wrapper wasn't updated to match."

    The schema tests above check properties *of* the schema -- that a
    description exists, that cluster_name is required. None of them
    compares the wrapper against the thing it calls, so a core function
    that gains a required keyword-only parameter, or renames one, leaves
    every one of them green and fails at the first real invocation with a
    TypeError that surfaces as an opaque internal error.

    Both directions matter and catch different edits:
      * a required parameter the wrapper never passes -- the core function
        grew one;
      * a keyword the wrapper passes that the core function does not
        accept -- it was renamed or removed.

    Checked against `inspect.signature` of the imported function and the
    wrapper's own AST, because there is no runtime moment where the two
    are compared: the call either happens correctly or raises.
    """

    @staticmethod
    def _call_sites():
        import ast

        import mcp_server.tools as tools_mod

        with open(os.path.join(REPO_ROOT, "mcp_server", "tools.py")) as fh:
            tree = ast.parse(fh.read())
        wrappers = set(TOOL_TIERS) | set(_LOCAL_ONLY)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name not in wrappers:
                continue
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                if not isinstance(call.func, ast.Name):
                    continue
                target = getattr(tools_mod, call.func.id, None)
                if target is None or not callable(target):
                    continue
                if not (call.func.id.startswith("core_")
                        or call.func.id == "build_make_cluster_params"):
                    continue
                yield node.name, call.func.id, target, call

    def test_the_scan_finds_every_wrapper(self):
        """Vacuity guard. A scan that matched nothing would pass both
        checks below silently, and the obvious ways to break it -- a
        renamed prefix, a wrapper that stops being a FunctionDef -- are
        invisible without this."""
        found = {w for w, _, _, _ in self._call_sites()}
        expected = (set(TOOL_TIERS) | set(_LOCAL_ONLY)) - {"preview_cluster_delete"}
        assert expected <= found, sorted(expected - found)

    def test_no_wrapper_passes_a_kwargs_splat(self):
        """A splat defeats both checks below without failing either: the
        arguments become invisible to static inspection, so drift stops
        being detectable exactly where it starts being likely."""
        offenders = [
            f"{w} -> {fn}" for w, fn, _, call in self._call_sites()
            if any(k.arg is None for k in call.keywords)
        ]
        assert offenders == [], offenders

    def test_every_required_core_parameter_is_supplied(self):
        import inspect

        missing = []
        for wrapper, fn_name, target, call in self._call_sites():
            passed = {k.arg for k in call.keywords if k.arg}
            for p in inspect.signature(target).parameters.values():
                if p.kind is p.KEYWORD_ONLY and p.default is p.empty:
                    if p.name not in passed:
                        missing.append(f"{wrapper} -> {fn_name} omits {p.name!r}")
        assert missing == [], missing

    def test_no_wrapper_passes_a_keyword_the_core_function_rejects(self):
        import inspect

        bogus = []
        for wrapper, fn_name, target, call in self._call_sites():
            sig = inspect.signature(target)
            if any(p.kind is p.VAR_KEYWORD for p in sig.parameters.values()):
                continue
            for k in call.keywords:
                if k.arg and k.arg not in sig.parameters:
                    bogus.append(f"{wrapper} -> {fn_name} passes {k.arg!r}")
        assert bogus == [], bogus

    def test_the_checks_can_see_a_drifted_signature(self):
        """Discrimination guard, driving the real assertions rather than
        re-implementing them: give a core function an extra required
        keyword-only parameter and the omission check must fail; rename
        one and the rejection check must fail."""
        import inspect

        import mcp_server.tools as tools_mod

        real = tools_mod.core_list_queues

        def grown(*, cluster_name, repo_root, new_required):
            raise AssertionError("never called")

        def renamed(*, cluster_name, repo_root_RENAMED):
            raise AssertionError("never called")

        for fake, method in (
            (grown, self.test_every_required_core_parameter_is_supplied),
            (renamed, self.test_no_wrapper_passes_a_keyword_the_core_function_rejects),
        ):
            tools_mod.core_list_queues = fake
            try:
                with pytest.raises(AssertionError):
                    method()
            finally:
                tools_mod.core_list_queues = real
        assert inspect.signature(tools_mod.core_list_queues) == inspect.signature(real)
