"""Workstream 7 layers 2 and 5: in-memory tool-call tests and error
translation, driven through FastMCP's own `Client`.

This is the only test file that exercises the server the way a real MCP
client does -- connect, list, call -- rather than poking at the wrappers
directly. Everything else in the MCP suite tests a piece; this tests that
the pieces are actually wired together.

MONKEYPATCH TARGET, and this repo has been bitten by it at every scale
(CLAUDE.md's "monkeypatch-isolation trap"): `mcp_server/tools.py` does
`from pcluster_core import core_list_clusters`, which binds that name into
`mcp_server.tools`' own namespace at import time. Patching
`pcluster_core.core_list_clusters` therefore does nothing here -- the tool
body resolves the name from its own module globals. Patch
`mcp_server.tools.<name>`.
"""

import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from fastmcp import Client  # noqa: E402
from fastmcp.exceptions import ToolError  # noqa: E402

import mcp_server.tools as tools_mod  # noqa: E402
from mcp_server.server import build_local, build_remote  # noqa: E402
from mcp_server.tiers import TOOL_TIERS, UNIMPLEMENTED  # noqa: E402


def _text(result):
    """The text payload of a tool result, as a client sees it."""
    return "".join(getattr(b, "text", "") for b in result.content)


class TestTheServerAnswersAClient:
    @pytest.mark.asyncio
    async def test_a_client_can_connect_and_list_tools(self):
        async with Client(build_local()) as client:
            assert await client.list_tools()

    @pytest.mark.asyncio
    async def test_the_local_server_exposes_every_implemented_tool(self):
        from mcp_server.tools import _LOCAL_ONLY

        async with Client(build_local()) as client:
            names = {t.name for t in await client.list_tools()}
        expected = (set(TOOL_TIERS) - UNIMPLEMENTED) | set(_LOCAL_ONLY)
        assert names == expected

    @pytest.mark.asyncio
    async def test_the_remote_server_omits_the_local_only_tools(self):
        """The same exclusion test_mcp_schemas pins statically, verified
        here through an actual client session instead."""
        from mcp_server.tools import _LOCAL_ONLY

        async with Client(build_remote()) as client:
            names = {t.name for t in await client.list_tools()}
        assert names.isdisjoint(_LOCAL_ONLY)

    @pytest.mark.asyncio
    async def test_every_advertised_tool_has_a_description(self):
        async with Client(build_local()) as client:
            for tool in await client.list_tools():
                assert tool.description, tool.name


class TestToolCallsReturnStructuredResults:
    @pytest.mark.asyncio
    async def test_list_clusters_returns_the_core_functions_records(self, monkeypatch):
        import dataclasses

        @dataclasses.dataclass(frozen=True)
        class _Entry:
            cluster_name: str
            region: str

        monkeypatch.setattr(tools_mod, "_load_records", lambda: [])
        monkeypatch.setattr(
            tools_mod, "core_list_clusters",
            lambda **kw: [_Entry("osiris", "us-east-2")],
        )
        async with Client(build_local()) as client:
            result = await client.call_tool("list_clusters", {})
        assert json.loads(_text(result)) == [{"cluster_name": "osiris", "region": "us-east-2"}]

    @pytest.mark.asyncio
    async def test_filters_reach_the_core_function(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(tools_mod, "_load_records", lambda: [])
        monkeypatch.setattr(
            tools_mod, "core_list_clusters",
            lambda **kw: seen.update(kw) or [],
        )
        async with Client(build_local()) as client:
            await client.call_tool("list_clusters", {"region": "us-east-2", "live": True})
        assert seen["region_filter"] == "us-east-2"
        assert seen["live"] is True

    @pytest.mark.asyncio
    async def test_a_dataclass_result_is_flattened_for_the_client(self, monkeypatch):
        import dataclasses

        @dataclasses.dataclass(frozen=True)
        class _Report:
            total: float

        monkeypatch.setattr(tools_mod, "_load_records", lambda: [])
        monkeypatch.setattr(tools_mod, "core_get_cost_report", lambda **kw: _Report(12.5))
        async with Client(build_local()) as client:
            result = await client.call_tool("get_cost_report", {})
        assert json.loads(_text(result)) == {"total": 12.5}

    @pytest.mark.asyncio
    async def test_check_cluster_health_never_claims_ssh_is_available_remotely(
        self, monkeypatch
    ):
        """Key material never reaches the *remote* transport, so there the
        SSH-dependent sub-checks must take core_check_cluster_health's
        existing SKIP branch rather than being attempted and failing.

        This drove `build_local()` while asserting the remote behavior,
        which is how the shipped `ssh_available=False` hardcode read as
        correct: the assertion matched, on the one transport where it
        should not have. The local half lives in
        TestTheSshDependentChecksDegradeOnlyOnTheRemoteTransport
        (tests/test_mcp_schemas.py), which pins both directions.
        """
        seen = {}
        monkeypatch.setattr(tools_mod, "_require_record", lambda name: object())
        monkeypatch.setattr(
            tools_mod, "core_check_cluster_health",
            lambda **kw: seen.update(kw) or {"ok": True},
        )
        async with Client(build_remote()) as client:
            await client.call_tool("check_cluster_health", {"cluster_name": "osiris"})
        assert seen["ssh_available"] is False

    @pytest.mark.asyncio
    async def test_diagnose_cluster_does_not_either_remotely(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(tools_mod, "_require_record", lambda name: object())
        monkeypatch.setattr(
            tools_mod, "core_diagnose_cluster",
            lambda **kw: seen.update(kw) or {"ok": True},
        )
        async with Client(build_remote()) as client:
            await client.call_tool("diagnose_cluster", {"cluster_name": "osiris"})
        assert seen["ssh_available"] is False

    @pytest.mark.asyncio
    async def test_fleet_tools_default_to_not_waiting(self, monkeypatch):
        """A single tool call cannot block on a fleet transition.

        The lock has to be stubbed here too: since the MCP-side lock
        integration landed, these tools acquire the S3 distributed lock
        around the core call, and an unstubbed one reaches for real STS
        and S3. tests/test_mcp_lock.py is where the locking itself is
        tested; here it is only noise.
        """
        monkeypatch.setattr(tools_mod, "_aws_account_id", lambda: "123456789012")
        monkeypatch.setattr(
            tools_mod, "_acquire_distributed_cluster_lock", lambda *a, **kw: "lock"
        )
        monkeypatch.setattr(tools_mod, "s3_release_cluster_lock", lambda *a, **kw: None)
        for tool, core in (("stop_fleet", "core_stop_fleet"),
                           ("start_fleet", "core_start_fleet")):
            seen = {}
            monkeypatch.setattr(
                tools_mod, "_require_record",
                lambda name: type("R", (), {"region": "us-east-2"})(),
            )
            monkeypatch.setattr(tools_mod, core, lambda **kw: seen.update(kw) or {"ok": True})
            async with Client(build_local()) as client:
                await client.call_tool(tool, {"cluster_name": "osiris"})
            assert seen["wait"] is False, tool


class TestErrorsReachTheClientShaped:
    """Workstream 7 layer 5, verified through a real client session: a
    failure must arrive as a tool error, never as a traceback."""

    @pytest.mark.asyncio
    async def test_a_core_exception_becomes_a_tool_error(self, monkeypatch):
        def _boom(**kw):
            raise RuntimeError("core exploded")

        monkeypatch.setattr(tools_mod, "_load_records", lambda: [])
        monkeypatch.setattr(tools_mod, "core_list_clusters", _boom)
        async with Client(build_local()) as client:
            with pytest.raises(ToolError):
                await client.call_tool("list_clusters", {})

    @pytest.mark.asyncio
    async def test_no_traceback_reaches_the_client(self, monkeypatch):
        def _boom(**kw):
            raise RuntimeError("core exploded")

        monkeypatch.setattr(tools_mod, "_load_records", lambda: [])
        monkeypatch.setattr(tools_mod, "core_list_clusters", _boom)
        async with Client(build_local()) as client:
            result = await client.call_tool("list_clusters", {}, raise_on_error=False)
        blob = _text(result) + json.dumps(str(result.content))
        assert result.is_error
        assert "Traceback" not in blob
        assert 'File "' not in blob

    @pytest.mark.asyncio
    async def test_a_pcluster_maker_error_reaches_the_client_as_text(self, monkeypatch):
        """PClusterMakerError carries an operator-facing message; that
        message is the useful part and must survive."""
        from pcluster_core import PClusterMakerError

        def _boom(name):
            raise PClusterMakerError("no cluster named 'ghost' is tracked")

        monkeypatch.setattr(tools_mod, "_require_record", _boom)
        async with Client(build_local()) as client:
            result = await client.call_tool(
                "check_cluster_health", {"cluster_name": "ghost"}, raise_on_error=False
            )
        assert result.is_error
        assert "ghost" in _text(result)

    @pytest.mark.asyncio
    async def test_an_unknown_tool_is_an_error_not_a_crash(self):
        async with Client(build_local()) as client:
            with pytest.raises(ToolError):
                await client.call_tool("definitely_not_a_tool", {})

    @pytest.mark.asyncio
    async def test_a_missing_required_argument_is_rejected(self):
        """Schema validation happens before the wrapper runs, so a
        cluster-scoped tool cannot be invoked with no target."""
        async with Client(build_local()) as client:
            with pytest.raises(ToolError):
                await client.call_tool("check_cluster_health", {})


class TestTheDeleteGateThroughAClient:
    @pytest.mark.asyncio
    async def test_preview_returns_a_token_and_mutates_nothing(self, monkeypatch):
        rec = type("R", (), {
            "region": "us-east-2", "cluster_owner": "testuser", "serial": "osiris-1",
            "ec2_keypair": "kp", "s3_bucketname": "bucket",
        })()
        monkeypatch.setattr(tools_mod, "_require_record", lambda name: rec)
        called = []
        monkeypatch.setattr(tools_mod, "core_delete_cluster", lambda **kw: called.append(kw))
        async with Client(build_local()) as client:
            result = await client.call_tool("preview_cluster_delete", {"cluster_name": "osiris"})
        payload = json.loads(_text(result))
        assert payload["confirmation_token"]
        assert called == [], "preview must not delete anything"

    @pytest.mark.asyncio
    async def test_the_previewed_token_lets_the_delete_through(self, monkeypatch):
        rec = type("R", (), {
            "region": "us-east-2", "cluster_owner": "testuser", "serial": "osiris-1",
            "ec2_keypair": "kp", "s3_bucketname": "bucket",
        })()
        monkeypatch.setattr(tools_mod, "_require_record", lambda name: rec)
        called = []
        monkeypatch.setattr(
            tools_mod, "core_delete_cluster",
            lambda **kw: called.append(kw) or {"success": True},
        )
        async with Client(build_local()) as client:
            preview = json.loads(_text(
                await client.call_tool("preview_cluster_delete", {"cluster_name": "osiris"})
            ))
            await client.call_tool("delete_cluster", {
                "cluster_name": "osiris",
                "confirmation_token": preview["confirmation_token"],
            })
        assert len(called) == 1
        assert called[0]["wait"] is False

    @pytest.mark.asyncio
    async def test_a_token_from_a_different_cluster_is_refused(self, monkeypatch):
        """End-to-end version of the property the gate exists for."""
        rec = type("R", (), {
            "region": "us-east-2", "cluster_owner": "testuser", "serial": "s",
            "ec2_keypair": "kp", "s3_bucketname": "bucket",
        })()
        monkeypatch.setattr(tools_mod, "_require_record", lambda name: rec)
        called = []
        monkeypatch.setattr(tools_mod, "core_delete_cluster", lambda **kw: called.append(kw))
        async with Client(build_local()) as client:
            preview = json.loads(_text(
                await client.call_tool("preview_cluster_delete", {"cluster_name": "osiris"})
            ))
            result = await client.call_tool("delete_cluster", {
                "cluster_name": "production",
                "confirmation_token": preview["confirmation_token"],
            }, raise_on_error=False)
        assert result.is_error
        assert called == [], "a mismatched token must not reach the core function"

    @pytest.mark.asyncio
    async def test_delete_without_a_token_is_rejected_by_the_schema(self):
        async with Client(build_local()) as client:
            with pytest.raises(ToolError):
                await client.call_tool("delete_cluster", {"cluster_name": "osiris"})


class TestCreateClusterOverrides:
    """Option B: the remote tool accepts any parameter the CLI does, with
    two guards the CLI gets free from argparse and an untyped `overrides`
    dict does not -- unknown keys and wrong-typed values -- plus three
    parameters held back to the CLI at the operator's direction.
    """

    _REQ = dict(
        cluster_name="osiris", cluster_owner="testuser",
        cluster_owner_email="testuser@example.com", az="us-east-2a",
        headnode_instance_type="c5.xlarge",
    )
    # A compute queue is required -- both instance-type defaults are ""
    # and a cluster with neither queue has nothing to run jobs on, which
    # build_make_cluster_params now rejects. These tests previously
    # previewed exactly that cluster and "passed".
    _QUEUE = {"compute_instance_type": "c5.2xlarge"}

    async def _preview(self, **kw):
        args = dict(self._REQ)
        overrides = dict(self._QUEUE)
        overrides.update(kw.pop("overrides", None) or {})
        args.update(kw)
        args["overrides"] = overrides
        async with Client(build_local()) as client:
            return await client.call_tool(
                "preview_cluster_config", args, raise_on_error=False
            )

    @pytest.mark.asyncio
    async def test_a_plain_preview_resolves_the_whole_config(self):
        result = await self._preview()
        payload = json.loads(_text(result))
        assert payload["resolved_config"]["cluster_name"] == "osiris"
        assert payload["confirmation_token"]

    @pytest.mark.asyncio
    async def test_arbitrary_cli_parameters_are_accepted(self):
        """The point of option B -- parity with the CLI, not a curated
        subset."""
        result = await self._preview(
            overrides={"enable_fsx": "true", "fsx_size": 2400,
                       "ebs_encryption": "true", "placement_group": "cluster"}
        )
        cfg = json.loads(_text(result))["resolved_config"]
        assert cfg["enable_fsx"] == "true"
        assert cfg["fsx_size"] == 2400
        assert cfg["ebs_encryption"] == "true"

    @pytest.mark.asyncio
    async def test_an_unknown_parameter_is_rejected(self):
        result = await self._preview(overrides={"enable_fsxx": "true"})
        assert result.is_error
        assert "unknown cluster parameter" in _text(result)

    @pytest.mark.asyncio
    async def test_a_wrong_typed_value_is_rejected(self):
        """The gap that made an untyped dict worse than typed parameters:
        booleans are carried as the strings "true"/"false" here, so a real
        bool passes a key check and then silently does nothing."""
        result = await self._preview(overrides={"enable_fsx": True})
        assert result.is_error
        text = _text(result)
        assert "wrong type" in text
        assert "true" in text  # the message names the expected form

    @pytest.mark.asyncio
    async def test_a_bool_is_not_accepted_where_an_int_is_expected(self):
        """bool subclasses int in Python, so an isinstance check would let
        loginnode_count=True through as 1."""
        result = await self._preview(overrides={"loginnode_count": True})
        assert result.is_error
        assert "wrong type" in _text(result)

    @pytest.mark.asyncio
    async def test_the_preview_surfaces_consequential_defaults(self):
        """Encryption is off by default. A caller who never mentions it
        should still see that in the preview rather than discovering it at
        an audit."""
        payload = json.loads(_text(await self._preview()))
        assert payload["notable_defaults"]["ebs_encryption"] == "false"

    @pytest.mark.asyncio
    async def test_an_overridden_default_drops_out_of_notable_defaults(self):
        payload = json.loads(_text(
            await self._preview(overrides={"ebs_encryption": "true"})
        ))
        assert "ebs_encryption" not in payload["notable_defaults"]
        assert payload["non_default_settings"]["ebs_encryption"] == "true"


class TestTheCliOnlyParameters:
    """pre_install_script, post_install_script and custom_ami are the only
    knobs that change what code runs on the nodes; held back to the CLI at
    the operator's direction. The message has to say where to go, not just
    refuse."""

    _REQ = dict(
        cluster_name="osiris", cluster_owner="testuser",
        cluster_owner_email="testuser@example.com", az="us-east-2a",
        headnode_instance_type="c5.xlarge",
    )

    async def _preview(self, overrides):
        merged = {"compute_instance_type": "c5.2xlarge"}
        merged.update(overrides)
        args = dict(self._REQ, overrides=merged)
        async with Client(build_local()) as client:
            return await client.call_tool(
                "preview_cluster_config", args, raise_on_error=False
            )

    @pytest.mark.parametrize(
        "param", ["pre_install_script", "post_install_script", "custom_ami"]
    )
    @pytest.mark.asyncio
    async def test_each_denied_parameter_is_refused(self, param):
        result = await self._preview({param: "something"})
        assert result.is_error
        assert param in _text(result)

    @pytest.mark.asyncio
    async def test_the_refusal_points_at_the_cli(self):
        """A bare refusal leaves the model with nowhere to go; it will
        retry or give up. Naming the command lets it tell the operator
        exactly what to run."""
        text = _text(await self._preview({"custom_ami": "ami-0abc"}))
        assert "make_pcluster.py" in text
        assert "--custom_ami" in text

    @pytest.mark.asyncio
    async def test_the_refusal_says_why(self):
        text = _text(await self._preview({"pre_install_script": "x.sh"}))
        assert "root on every node" in text

    @pytest.mark.asyncio
    async def test_everything_else_still_works_alongside(self):
        """The denial must be specific, not a blanket rejection of any
        request that happens to mention one."""
        text = _text(await self._preview({"custom_ami": "ami-0abc"}))
        assert "Every other parameter is available here" in text

    @pytest.mark.asyncio
    async def test_the_denied_set_is_exactly_three(self):
        from mcp_server.tools import _REMOTE_DENIED_PARAMS

        assert set(_REMOTE_DENIED_PARAMS) == {
            "pre_install_script", "post_install_script", "custom_ami"
        }

    @pytest.mark.asyncio
    async def test_create_cluster_also_refuses_them(self):
        """Not just the preview -- the execute path must refuse too, or a
        token minted without them could carry them in on the second call."""
        from mcp_server.confirmation_token import mint

        params = dict(self._REQ)
        token_params = dict(params, overrides={"custom_ami": "ami-0abc"})
        args = dict(params, confirmation_token=mint("create_cluster", token_params),
                    overrides={"custom_ami": "ami-0abc"})
        async with Client(build_local()) as client:
            result = await client.call_tool("create_cluster", args, raise_on_error=False)
        assert result.is_error
        assert "custom_ami" in _text(result)


class TestTheNoQueueClusterIsRefusedRemotely:
    """The defaults-only cluster has no compute queue at all, and the
    remote path is where that is easiest to reach by accident -- a model
    asked for "a cluster" supplies the five required parameters and
    nothing else. Refused at preview, before a token is even minted."""

    _REQ = dict(
        cluster_name="osiris", cluster_owner="testuser",
        cluster_owner_email="testuser@example.com", az="us-east-2a",
        headnode_instance_type="c5.xlarge",
    )

    @pytest.mark.asyncio
    async def test_preview_refuses_a_cluster_with_no_queue(self):
        async with Client(build_local()) as client:
            result = await client.call_tool(
                "preview_cluster_config", dict(self._REQ), raise_on_error=False
            )
        assert result.is_error
        assert "no compute queue" in _text(result)

    @pytest.mark.asyncio
    async def test_no_token_is_minted_for_a_cluster_that_cannot_build(self):
        """A token for an unbuildable cluster would let create_cluster get
        all the way to provisioning before PCluster rejected the config."""
        async with Client(build_local()) as client:
            result = await client.call_tool(
                "preview_cluster_config", dict(self._REQ), raise_on_error=False
            )
        assert "confirmation_token" not in _text(result)

    @pytest.mark.asyncio
    async def test_a_gpu_only_cluster_is_allowed(self):
        """GPU-only is a supported shape, not an accident -- refusing it
        would be the obvious over-correction here."""
        args = dict(self._REQ, overrides={"gpu_instance_type": "g5.xlarge"})
        async with Client(build_local()) as client:
            result = await client.call_tool(
                "preview_cluster_config", args, raise_on_error=False
            )
        assert not result.is_error
