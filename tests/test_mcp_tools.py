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

from conftest import assert_source_is_real
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from fastmcp import Client  # noqa: E402
from fastmcp.exceptions import ToolError  # noqa: E402

import mcp_server.tools as tools_mod  # noqa: E402
from mcp_server.server import build_local, build_remote  # noqa: E402
from mcp_server.tiers import TOOL_TIERS, UNIMPLEMENTED  # noqa: E402
from pcluster_core import _is_writable_dir, resolve_writable_repo_root  # noqa: E402


def _text(result):
    """The text payload of a tool result, as a client sees it."""
    return "".join(getattr(b, "text", "") for b in result.content)


def _stub_az(monkeypatch, zones=({"RegionName": "us-east-2"},), seen=None):
    """Keep the AZ verification off the network.

    preview_cluster_config and create_cluster both resolve the region from
    a real describe_availability_zones call. Patch pcluster_core's boto3
    rather than the resolver: the resolver is what these tests are for, and
    stubbing it would leave the whole path unexercised. Without this the
    suite passes locally against live AWS and fails in CI, which is exactly
    how it behaved before the stub was added.
    """

    class _Client:
        def describe_availability_zones(self, ZoneNames=None, **kw):
            if seen is not None:
                seen.append(ZoneNames)
            return {"AvailabilityZones": list(zones)}

    import pcluster_core

    monkeypatch.setattr(pcluster_core.boto3, "client", lambda *a, **kw: _Client())


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
            tools_mod,
            "core_list_clusters",
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
            tools_mod,
            "core_list_clusters",
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
    async def test_check_cluster_health_never_claims_ssh_is_available_remotely(self, monkeypatch):
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
            tools_mod,
            "core_check_cluster_health",
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
            tools_mod,
            "core_diagnose_cluster",
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
        monkeypatch.setattr(tools_mod, "_acquire_distributed_cluster_lock", lambda *a, **kw: "lock")
        monkeypatch.setattr(tools_mod, "s3_release_cluster_lock", lambda *a, **kw: None)
        for tool, core in (("stop_fleet", "core_stop_fleet"), ("start_fleet", "core_start_fleet")):
            seen = {}
            monkeypatch.setattr(
                tools_mod,
                "_require_record",
                lambda name: type("R", (), {"region": "us-east-2"})(),
            )
            # noqa B023: `seen` is rebound each iteration and this lambda is
            # consumed inside the same iteration, so late binding cannot bite.
            monkeypatch.setattr(  # noqa: B023
                tools_mod,
                core,
                lambda **kw: seen.update(kw) or {"ok": True},  # noqa: B023
            )
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
        rec = type(
            "R",
            (),
            {
                "region": "us-east-2",
                "cluster_owner": "testuser",
                "serial": "osiris-1",
                "ec2_keypair": "kp",
                "s3_bucketname": "bucket",
            },
        )()
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
        rec = type(
            "R",
            (),
            {
                "region": "us-east-2",
                "cluster_owner": "testuser",
                "serial": "osiris-1",
                "ec2_keypair": "kp",
                "s3_bucketname": "bucket",
            },
        )()
        monkeypatch.setattr(tools_mod, "_require_record", lambda name: rec)
        called = []
        monkeypatch.setattr(
            tools_mod,
            "core_delete_cluster",
            lambda **kw: called.append(kw) or {"success": True},
        )
        async with Client(build_local()) as client:
            preview = json.loads(
                _text(await client.call_tool("preview_cluster_delete", {"cluster_name": "osiris"}))
            )
            await client.call_tool(
                "delete_cluster",
                {
                    "cluster_name": "osiris",
                    "confirmation_token": preview["confirmation_token"],
                },
            )
        assert len(called) == 1
        assert called[0]["wait"] is False

    @pytest.mark.asyncio
    async def test_a_token_from_a_different_cluster_is_refused(self, monkeypatch):
        """End-to-end version of the property the gate exists for."""
        rec = type(
            "R",
            (),
            {
                "region": "us-east-2",
                "cluster_owner": "testuser",
                "serial": "s",
                "ec2_keypair": "kp",
                "s3_bucketname": "bucket",
            },
        )()
        monkeypatch.setattr(tools_mod, "_require_record", lambda name: rec)
        called = []
        monkeypatch.setattr(tools_mod, "core_delete_cluster", lambda **kw: called.append(kw))
        async with Client(build_local()) as client:
            preview = json.loads(
                _text(await client.call_tool("preview_cluster_delete", {"cluster_name": "osiris"}))
            )
            result = await client.call_tool(
                "delete_cluster",
                {
                    "cluster_name": "production",
                    "confirmation_token": preview["confirmation_token"],
                },
                raise_on_error=False,
            )
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

    @pytest.fixture(autouse=True)
    def _offline_az(self, monkeypatch):
        _stub_az(monkeypatch)

    _REQ = dict(
        cluster_name="osiris",
        cluster_owner="testuser",
        cluster_owner_email="testuser@example.com",
        az="us-east-2a",
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
            return await client.call_tool("preview_cluster_config", args, raise_on_error=False)

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
            overrides={
                "enable_fsx": "true",
                "fsx_size": 2400,
                "ebs_encryption": "true",
                "placement_group": "cluster",
            }
        )
        cfg = json.loads(_text(result))["resolved_config"]
        # Coerced to a real bool before it reaches MakeClusterParams --
        # see TestEveryBoolFieldIsARealBool for why the string form was a
        # bug rather than a convention.
        assert cfg["enable_fsx"] is True
        assert cfg["fsx_size"] == 2400
        assert cfg["ebs_encryption"] is True

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
        payload = json.loads(_text(await self._preview(overrides={"ebs_encryption": "true"})))
        assert "ebs_encryption" not in payload["notable_defaults"]
        assert payload["non_default_settings"]["ebs_encryption"] == "true"


class TestTheCliOnlyParameters:
    """pre_install_script, post_install_script and custom_ami are the only
    knobs that change what code runs on the nodes; held back to the CLI at
    the operator's direction. The message has to say where to go, not just
    refuse."""

    _REQ = dict(
        cluster_name="osiris",
        cluster_owner="testuser",
        cluster_owner_email="testuser@example.com",
        az="us-east-2a",
        headnode_instance_type="c5.xlarge",
    )

    async def _preview(self, overrides):
        merged = {"compute_instance_type": "c5.2xlarge"}
        merged.update(overrides)
        args = dict(self._REQ, overrides=merged)
        async with Client(build_local()) as client:
            return await client.call_tool("preview_cluster_config", args, raise_on_error=False)

    @pytest.mark.parametrize("param", ["pre_install_script", "post_install_script", "custom_ami"])
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
            "pre_install_script",
            "post_install_script",
            "custom_ami",
        }

    @pytest.mark.asyncio
    async def test_create_cluster_also_refuses_them(self):
        """Not just the preview -- the execute path must refuse too, or a
        token minted without them could carry them in on the second call."""
        from mcp_server.confirmation_token import mint

        params = dict(self._REQ)
        token_params = dict(params, overrides={"custom_ami": "ami-0abc"}, defaults=None)
        args = dict(
            params,
            confirmation_token=mint("create_cluster", token_params),
            overrides={"custom_ami": "ami-0abc"},
        )
        async with Client(build_local()) as client:
            result = await client.call_tool("create_cluster", args, raise_on_error=False)
        assert result.is_error
        assert "custom_ami" in _text(result)


class TestTheNoQueueClusterIsRefusedRemotely:
    """The defaults-only cluster has no compute queue at all, and the
    remote path is where that is easiest to reach by accident -- a model
    asked for "a cluster" supplies the five required parameters and
    nothing else. Refused at preview, before a token is even minted."""

    @pytest.fixture(autouse=True)
    def _offline_az(self, monkeypatch):
        _stub_az(monkeypatch)

    _REQ = dict(
        cluster_name="osiris",
        cluster_owner="testuser",
        cluster_owner_email="testuser@example.com",
        az="us-east-2a",
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
            result = await client.call_tool("preview_cluster_config", args, raise_on_error=False)
        assert not result.is_error


class TestCreateClusterResolvesTheRegionItBuildsIn:
    """MakeClusterParams carries no region field -- deliberately, per its
    own docstring: the CLI resolves region from the AZ-verification call
    and hands it to core_create_cluster separately. The MCP tool is a
    second shim and skipped that step, reading `params.region` off an
    object that has no such attribute. Every create_cluster call raised
    AttributeError after the token verified and before any AWS mutation.

    Nothing caught it because the only test that called create_cluster
    asserted an *error*, and got one from the denied-parameter check --
    which returns before the region line is ever reached. CLAUDE.md's
    "when a test stubs the object under test, at least one test must drive
    the real one" again: these drive the real tool and the real resolver,
    stubbing only the EC2 client and the core function.
    """

    _REQ = dict(
        cluster_name="osiris",
        cluster_owner="testuser",
        cluster_owner_email="testuser@example.com",
        az="us-east-2a",
        headnode_instance_type="c5.xlarge",
    )
    _QUEUE = {"compute_instance_type": "c5.2xlarge"}

    def _stub_ec2(self, monkeypatch, zones, seen=None):
        _stub_az(monkeypatch, zones=zones, seen=seen)

    def _stub_core(self, monkeypatch, calls):
        def _fake(**kwargs):
            calls.append(kwargs)
            return {"cluster_name": kwargs["params"].cluster_name}

        monkeypatch.setattr(tools_mod, "core_create_cluster", _fake)

    def _args(self):
        from mcp_server.confirmation_token import mint

        overrides = dict(self._QUEUE)
        # `defaults` is part of what the token binds now: the auto-applied
        # <cluster>_defaults.yml has to be covered, or a file edited inside
        # the 15-minute window builds something else. None here because the
        # conftest isolation fixture guarantees no such file.
        token_params = dict(self._REQ, overrides=dict(sorted(overrides.items())), defaults=None)
        return dict(
            self._REQ,
            overrides=overrides,
            confirmation_token=mint("create_cluster", token_params),
        )

    @pytest.mark.asyncio
    async def test_a_real_region_reaches_the_core_function(self, monkeypatch):
        """The regression: this raised AttributeError before the fix."""
        calls, seen = [], []
        self._stub_ec2(monkeypatch, [{"RegionName": "us-east-2"}], seen)
        self._stub_core(monkeypatch, calls)

        async with Client(build_local()) as client:
            result = await client.call_tool("create_cluster", self._args(), raise_on_error=False)

        assert not result.is_error, _text(result)
        assert len(calls) == 1
        assert calls[0]["region"] == "us-east-2"
        assert seen == [["us-east-2a"]], "the AZ actually asked about"

    @pytest.mark.asyncio
    async def test_the_region_comes_from_ec2_not_from_trimming_the_az(self, monkeypatch):
        """az[:-1] is right for every AZ _validate_az_input accepts, so a
        string trim passes the test above and still skips the call that
        proves the AZ exists. Only a divergent answer separates them."""
        calls = []
        self._stub_ec2(monkeypatch, [{"RegionName": "eu-west-1"}])
        self._stub_core(monkeypatch, calls)

        async with Client(build_local()) as client:
            result = await client.call_tool("create_cluster", self._args(), raise_on_error=False)

        assert not result.is_error, _text(result)
        assert calls[0]["region"] == "eu-west-1" != "us-east-2"

    @pytest.mark.asyncio
    async def test_an_unknown_az_stops_the_build(self, monkeypatch):
        """A well-formed typo passes _validate_az_input's regex. If it also
        passes here, every regional client binds to a region the operator
        never named."""
        calls = []
        self._stub_ec2(monkeypatch, [])
        self._stub_core(monkeypatch, calls)

        async with Client(build_local()) as client:
            result = await client.call_tool("create_cluster", self._args(), raise_on_error=False)

        assert result.is_error
        assert "us-east-2a" in _text(result)
        assert calls == [], "the build must not start on an unverified AZ"

    def test_the_params_object_still_carries_no_region(self):
        """The wrong repair. MakeClusterParams' docstring explains why the
        region cannot be folded into it -- the AZ check, the Ansible check
        and the Turbot profile switch all have to run first, in order.
        Adding the field would make `params.region` compile and put the
        resolution back where it cannot happen."""
        import dataclasses as _dc

        from pcluster_core import MakeClusterParams

        assert "region" not in {f.name for f in _dc.fields(MakeClusterParams)}


class TestThePreviewVerifiesTheAzBeforeMintingAToken:
    """A token asserts that this configuration was previewed. A preview
    that never checked whether the AZ exists cannot make that assertion --
    it mints a token for a cluster that create_cluster will refuse, or
    worse, for one that builds in a region nobody named.

    This is the same argument TestTheNoQueueClusterIsRefusedRemotely makes
    about a cluster with no compute queue, applied to the other input that
    is well-formed by regex and still wrong.

    The ordering matters as much as the check: parameter resolution stays
    offline, so an unbuildable config still fails without an AWS call at
    all, and only a config worth building costs a round trip.
    """

    _REQ = dict(
        cluster_name="osiris",
        cluster_owner="testuser",
        cluster_owner_email="testuser@example.com",
        az="us-east-2a",
        headnode_instance_type="c5.xlarge",
    )
    _QUEUE = {"compute_instance_type": "c5.2xlarge"}

    async def _preview(self, overrides=None):
        # `or` would swallow the empty dict, which is the whole input of
        # the unbuildable case below.
        if overrides is None:
            overrides = self._QUEUE
        args = dict(self._REQ, overrides=dict(overrides))
        async with Client(build_local()) as client:
            return await client.call_tool("preview_cluster_config", args, raise_on_error=False)

    @pytest.mark.asyncio
    async def test_the_preview_reports_the_region_it_verified(self, monkeypatch):
        """The key shipped as `resolved.get("region", "")` against a
        dataclass with no region field, so it was always the empty string.
        A caller cannot tell an unknown region from a missing one."""
        seen = []
        _stub_az(monkeypatch, zones=[{"RegionName": "eu-west-1"}], seen=seen)

        payload = json.loads(_text(await self._preview()))

        assert payload["region"] == "eu-west-1"
        assert seen == [["us-east-2a"]], "the AZ actually asked about"

    @pytest.mark.asyncio
    async def test_an_unknown_az_mints_no_token(self, monkeypatch):
        """`us-east-2z` passes _validate_az_input's regex. Before this the
        preview handed back a token for it and create_cluster was the first
        thing to notice."""
        _stub_az(monkeypatch, zones=[])

        result = await self._preview()

        assert result.is_error
        assert "confirmation_token" not in _text(result)

    @pytest.mark.asyncio
    async def test_an_unbuildable_config_never_reaches_aws(self, monkeypatch):
        """Ordering guard. Resolving parameters first keeps the offline
        failure offline -- moving the AZ check above it would bill a round
        trip on every malformed request, and would make the no-queue
        refusal depend on having credentials."""
        import pcluster_core

        def _explode(*a, **kw):
            raise AssertionError("no AWS call may precede parameter resolution")

        monkeypatch.setattr(pcluster_core.boto3, "client", _explode)

        result = await self._preview(overrides={})

        assert result.is_error
        assert "no compute queue" in _text(result)


class TestTheDefaultsFileReachesTheMcpSurface:
    """The operator writes `<cluster>_defaults.yml` and expects that
    cluster, whichever surface asks for it. The MCP server has no flags, so
    the file is applied automatically -- which makes it an input to the
    build that nobody typed, and the token has to cover it.
    """

    _REQ = dict(
        cluster_name="osiris",
        cluster_owner="testuser",
        cluster_owner_email="testuser@example.com",
        az="us-east-2a",
        headnode_instance_type="c5.xlarge",
    )

    @pytest.fixture(autouse=True)
    def _offline_az(self, monkeypatch):
        _stub_az(monkeypatch)

    def _write(self, tmp_path, monkeypatch, contents):
        import yaml as _yaml

        (tmp_path / "osiris_defaults.yml").write_text(_yaml.safe_dump(contents))
        monkeypatch.setattr("pcluster_core._default_repo_root", lambda: str(tmp_path))

    async def _preview(self):
        async with Client(build_local()) as client:
            return await client.call_tool(
                "preview_cluster_config", dict(self._REQ), raise_on_error=False
            )

    async def _create(self, token):
        args = dict(self._REQ, confirmation_token=token)
        async with Client(build_local()) as client:
            return await client.call_tool("create_cluster", args, raise_on_error=False)

    @pytest.mark.asyncio
    async def test_the_preview_applies_the_file_and_names_it(self, tmp_path, monkeypatch):
        """No overrides are passed at all here -- the compute queue comes
        from the file. Before this, the same call was refused for having no
        queue, with the operator's own file sitting unread beside it."""
        self._write(
            tmp_path,
            monkeypatch,
            {
                "compute_instance_type": "c6g.8xlarge",
                "cluster_type": "spot",
            },
        )

        payload = json.loads(_text(await self._preview()))

        assert payload["resolved_config"]["compute_instance_type"] == "c6g.8xlarge"
        assert payload["defaults_file"] == "osiris_defaults.yml"

    @pytest.mark.asyncio
    async def test_a_file_edited_after_the_preview_invalidates_the_token(
        self, tmp_path, monkeypatch
    ):
        """The reason the token binds the file. A token asserts that this
        configuration was previewed; an input that can change underneath it
        in the 15-minute window would make that assertion false while the
        token still verified."""
        calls = []
        monkeypatch.setattr(
            tools_mod,
            "core_create_cluster",
            lambda **kw: calls.append(kw) or {"status": "kicked off"},
        )
        self._write(tmp_path, monkeypatch, {"compute_instance_type": "c6g.8xlarge"})
        token = json.loads(_text(await self._preview()))["confirmation_token"]

        self._write(tmp_path, monkeypatch, {"compute_instance_type": "c5.24xlarge"})
        result = await self._create(token)

        assert result.is_error
        assert calls == [], "a build must not start on a stale preview"

    @pytest.mark.asyncio
    async def test_an_untouched_file_still_authorizes_the_build(self, tmp_path, monkeypatch):
        """Vacuity guard. A binding that rejected every token would satisfy
        the test above and break the tool."""
        calls = []
        monkeypatch.setattr(
            tools_mod,
            "core_create_cluster",
            lambda **kw: calls.append(kw) or {"status": "kicked off"},
        )
        self._write(tmp_path, monkeypatch, {"compute_instance_type": "c6g.8xlarge"})
        token = json.loads(_text(await self._preview()))["confirmation_token"]

        result = await self._create(token)

        assert not result.is_error, _text(result)
        assert calls[0]["params"].compute_instance_type == "c6g.8xlarge"

    @pytest.mark.asyncio
    async def test_the_preview_does_not_call_a_file_value_a_default(self, tmp_path, monkeypatch):
        """notable_defaults filtered on `overrides` alone, so a file that
        set base_os=ubuntu2404arm still had the preview reporting
        base_os=ubuntu2404 -- the wrong OS, stated to the operator who is
        about to approve the build."""
        from pcluster_core import MAKE_CLUSTER_DEFAULTS

        assert MAKE_CLUSTER_DEFAULTS["base_os"] != "ubuntu2404arm"
        self._write(
            tmp_path,
            monkeypatch,
            {
                "compute_instance_type": "c6g.8xlarge",
                "base_os": "ubuntu2404arm",
            },
        )

        payload = json.loads(_text(await self._preview()))

        assert payload["resolved_config"]["base_os"] == "ubuntu2404arm"
        assert "base_os" not in payload["notable_defaults"]
        assert payload["defaults_file_settings"]["base_os"] == "ubuntu2404arm"

    @pytest.mark.asyncio
    async def test_a_default_the_file_leaves_alone_is_still_reported(self, tmp_path, monkeypatch):
        """Vacuity guard: emptying notable_defaults would satisfy the test
        above and destroy the block's purpose."""
        self._write(tmp_path, monkeypatch, {"compute_instance_type": "c6g.8xlarge"})

        payload = json.loads(_text(await self._preview()))

        assert "base_os" in payload["notable_defaults"]
        assert "compute_instance_type" not in payload["notable_defaults"]

    @pytest.mark.asyncio
    async def test_a_non_build_key_is_not_reported_as_a_setting(self, tmp_path, monkeypatch):
        """delete_s3_bucketname is in the file for kill_pcluster.py. The
        build ignores it, so reporting it as something this cluster was
        configured with would be a third wrong claim in the same block."""
        self._write(
            tmp_path,
            monkeypatch,
            {
                "compute_instance_type": "c6g.8xlarge",
                "delete_s3_bucketname": "true",
            },
        )

        payload = json.loads(_text(await self._preview()))

        assert "delete_s3_bucketname" not in payload["defaults_file_settings"]
        assert payload["defaults_file_settings"]["compute_instance_type"] == "c6g.8xlarge"

    @pytest.mark.asyncio
    async def test_a_defaults_file_may_set_what_an_override_may_not(self, tmp_path, monkeypatch):
        """A decision, not an oversight, so it is pinned rather than left
        to be rediscovered as a bug.

        `_reject_denied` inspects `overrides` only. The denial stops a
        caller -- a model, over the network -- from choosing what code runs
        on the nodes; a defaults file is the operator's own artifact on
        their own disk, the same trust level as the CLI, where these three
        are allowed. Extending the check to the file would refuse every
        real operator's file: `pcluster_defaults.yml` itself sets
        pre_install_script and post_install_script.
        """
        self._write(
            tmp_path,
            monkeypatch,
            {
                "compute_instance_type": "c6g.8xlarge",
                "post_install_script": "scripts/post-deployment.sh",
            },
        )

        payload = json.loads(_text(await self._preview()))
        assert payload["resolved_config"]["post_install_script"] == ("scripts/post-deployment.sh")

        args = dict(
            self._REQ,
            overrides={
                "compute_instance_type": "c6g.8xlarge",
                "post_install_script": "scripts/post-deployment.sh",
            },
        )
        async with Client(build_local()) as client:
            refused = await client.call_tool("preview_cluster_config", args, raise_on_error=False)
        assert refused.is_error, "the same value as an override is still refused"
        assert "post_install_script" in _text(refused)


class TestTheStoreIsAddressedInTheClustersRegion:
    """The bucket is per account+region. Everything that *writes* it --
    core_create_cluster publishing, core_delete_cluster removing, the S3
    lock -- derives it from the cluster's region. The MCP read path derived
    it from the process environment instead, so a laptop with
    AWS_DEFAULT_REGION=us-east-1 building into us-west-2 wrote the record
    to one bucket and looked for it in another, and teardown could never
    reach whatever the MCP path had written to the wrong one.
    """

    def _seen_regions(self, monkeypatch, rec_region):
        """Record which region every store client is built for."""
        import boto3

        import mcp_server.tools as t

        seen = {"clients": [], "buckets": []}
        monkeypatch.setattr(t, "_aws_account_id", lambda: "123456789012")
        monkeypatch.setattr(
            t,
            "_store_region",
            lambda: "us-east-1",  # the ambient region
        )

        real_client = boto3.client

        def _fake_client(name, region_name=None, **kw):
            seen["clients"].append(region_name)
            return object()

        monkeypatch.setattr(boto3, "client", _fake_client)
        s3, bucket = t._record_store(rec_region)
        seen["buckets"].append(bucket)
        monkeypatch.setattr(boto3, "client", real_client)
        return seen

    def test_a_cluster_region_wins_over_the_ambient_one(self, monkeypatch):
        seen = self._seen_regions(monkeypatch, "us-west-2")
        assert seen["clients"] == ["us-west-2"]
        assert seen["buckets"] == ["parallelclustermaker-locks-123456789012-us-west-2"]

    def test_the_ambient_region_is_the_fallback(self, monkeypatch):
        """Only for the two cases with no cluster to ask: enumerating the
        store, and a cluster this machine has no local record for."""
        seen = self._seen_regions(monkeypatch, None)
        assert seen["clients"] == ["us-east-1"]
        assert seen["buckets"] == ["parallelclustermaker-locks-123456789012-us-east-1"]

    def test_every_tool_with_a_record_passes_its_region(self):
        """Asserted over the source: a call site that drops the argument
        silently addresses the wrong bucket, and no stub can see that
        because the stub is handed whichever client the caller built."""
        import ast
        import os

        import mcp_server.tools as t

        src = open(t.__file__).read()
        tree = ast.parse(src)
        bare, passed = [], []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_record_store"
            ):
                (passed if node.args else bare).append(node.lineno)

        assert passed, "no call site passes a region"
        # The two legitimate bare calls: _load_records (enumeration) and
        # _require_record's fallback for a cluster with no local record.
        # Three, and the third is the same documented exemption as the
        # other two: there is no cluster to ask for a region. get_build_status
        # answers for a build that *failed*, so no cluster record exists to
        # carry one -- requiring the caller to supply a region would mean
        # demanding a detail from someone whose whole problem is not knowing
        # what happened. _store_region() is right here for the reason one
        # server manages one region.
        assert len(bare) == 3, (
            f"expected exactly 3 region-less _record_store() calls, found "
            f"{len(bare)} at lines {bare}"
        )
        assert os.path.basename(t.__file__) == "tools.py"


class TestNoReadOnlyToolWritesTheStore:
    """`add_queue`/`remove_queue` write `configs/<name>.yaml`. They sat on
    the read-only tier because they mutate no CloudFormation stack -- true,
    and beside the point: a tier named read-only whose IAM carries
    s3:PutObject misrepresents itself to whoever reads the policy next.

    The cost of the move is real and accepted: a queue edit now carries the
    stack-mutation tier's blast radius, which is more than the edit needs.
    The alternative is a fifth tier for config writes, i.e. a fifth Lambda,
    role, policy and cold start.
    """

    _WRITERS = {"add_queue", "remove_queue"}

    def test_the_config_writers_are_not_on_the_read_only_tier(self):
        from mcp_server.tiers import TOOL_TIERS

        for name in self._WRITERS:
            assert TOOL_TIERS[name] != "read-only", (
                f"{name} writes S3 objects and cannot be read-only"
            )

    def test_they_are_where_the_rest_of_the_config_lifecycle_lives(self):
        """apply reads the config, teardown deletes it. Editing belongs
        with them, not scattered across tiers."""
        from mcp_server.tiers import TOOL_TIERS

        for name in self._WRITERS:
            assert TOOL_TIERS[name] == "stack-mutation"
        assert TOOL_TIERS["delete_cluster"] == "stack-mutation"

    def test_the_read_only_tier_still_reads_configs(self):
        """Vacuity guard: list_queues stays, so the tier keeps GetObject.
        Moving the readers too would be over-correction."""
        from mcp_server.tiers import TOOL_TIERS

        assert TOOL_TIERS["list_queues"] == "read-only"


class TestAnInvalidClusterNameDoesNotKillTheServer:
    """`_require_record` called `_validate_cluster_name` unwrapped, and that
    function `sys.exit()`s. SystemExit is a BaseException, so it is not
    turned into a tool error -- it unwinds the server process. 13 of the 18
    tools reach `_require_record`, so any of them with a malformed
    cluster_name took the whole session down.

    `tools.py` already documents this hazard for the distributed lock
    ("an uncaught SystemExit inside a long-lived FastMCP process kills the
    whole server rather than failing one call"); this was the same hazard
    one function over, and every other caller of that validator wraps it.
    """

    _BAD = ["Bad_Name", "UPPER", "9start", "trailing-", "has--double", "", "x" * 40]

    @pytest.mark.parametrize("name", _BAD)
    @pytest.mark.asyncio
    async def test_the_session_survives_a_malformed_name(self, name):
        """The second call is the assertion. A first call that returns an
        error proves nothing on its own -- SystemExit can surface as an
        error result while the transport is already unwinding."""
        async with Client(build_local()) as client:
            first = await client.call_tool(
                "check_cluster_health",
                {"cluster_name": name},
                raise_on_error=False,
            )
            assert first.is_error

            survived = await client.call_tool("list_clusters", {}, raise_on_error=False)
            assert not survived.is_error, f"the server did not survive cluster_name={name!r}"

    @pytest.mark.asyncio
    async def test_the_error_still_says_what_is_wrong(self):
        """Vacuity guard: swallowing the exception into a bare 'not
        tracked' would satisfy the test above and lose the naming rule."""
        async with Client(build_local()) as client:
            result = await client.call_tool(
                "check_cluster_health",
                {"cluster_name": "Bad_Name"},
                raise_on_error=False,
            )
        text = _text(result)
        assert "lowercase" in text and "27 characters" in text

    @pytest.mark.asyncio
    async def test_every_tool_that_takes_a_cluster_name_is_covered(self):
        """_require_record is the shared path, so covering one tool covers
        the rest -- but only while they all go through it. This pins that
        they do."""
        import ast
        import os

        import mcp_server.tools as t

        tree = ast.parse(open(t.__file__).read())
        reaches = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            args = [a.arg for a in node.args.args]
            if "cluster_name" not in args:
                continue
            calls = {
                n.func.id
                for n in ast.walk(node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            }
            if "_require_record" in calls:
                reaches.add(node.name)
        assert len(reaches) >= 8, (
            f"only {len(reaches)} tools route through _require_record: {reaches}"
        )
        assert os.path.basename(t.__file__) == "tools.py"


class TestListQueuesReturnsWhatItsAnnotationPromises:
    """`list_queues` was annotated `-> dict` and returned a list. FastMCP
    validates structured content against the annotation, so *every* call
    failed with "structured_content must be a dict or None" while carrying
    the correct payload inside the error text.

    Invisible to every existing test because they call the wrapper
    function directly, where the annotation is inert -- it only bites
    through a real client session. Found by calling the tool against a
    live cluster.
    """

    def _stub(self, monkeypatch, queues):
        import types

        monkeypatch.setattr(
            tools_mod,
            "_require_record",
            lambda name: types.SimpleNamespace(region="us-east-1"),
        )
        monkeypatch.setattr(tools_mod, "core_list_queues", lambda **kw: queues)

    @pytest.mark.asyncio
    async def test_a_queue_listing_survives_the_transport(self, monkeypatch):
        self._stub(
            monkeypatch,
            [
                {
                    "name": "compute",
                    "queue_type": "compute",
                    "capacity_type": "SPOT",
                    "min_count": 0,
                    "max_count": 8,
                    "instance_types": ["c8g.xlarge"],
                },
            ],
        )
        async with Client(build_local()) as client:
            result = await client.call_tool(
                "list_queues",
                {"cluster_name": "osiris"},
                raise_on_error=False,
            )
        assert not result.is_error, _text(result)
        assert "compute" in _text(result)

    @pytest.mark.asyncio
    async def test_an_empty_listing_survives_too(self, monkeypatch):
        """The shape has to hold when there is nothing to report -- an
        empty list is still a list."""
        self._stub(monkeypatch, [])
        async with Client(build_local()) as client:
            result = await client.call_tool(
                "list_queues",
                {"cluster_name": "osiris"},
                raise_on_error=False,
            )
        assert not result.is_error, _text(result)

    def test_the_annotation_matches_what_the_core_returns(self):
        """The root cause, pinned directly: a wrapper whose annotation
        disagrees with its return value fails only over the transport, so
        nothing that calls the function catches it."""
        import ast

        tree = ast.parse(open(tools_mod.__file__).read())
        fn = next(
            n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "list_queues"
        )
        assert ast.unparse(fn.returns) == "list[dict]", ast.unparse(fn.returns)


class TestFinalizeCompletesWhatDeleteStarted:
    """delete_cluster returns on CloudFormation's acceptance, so by itself
    it cleans up nothing. finalize_cluster_teardown is the other half --
    the decomposition CLAUDE.md's 900s-ceiling bullet calls for, rather
    than a capability a remote caller simply does not have.
    """

    def _rec(self):
        return type(
            "R",
            (),
            {
                "region": "us-east-2",
                "cluster_owner": "testuser",
                "serial": "osiris-1",
                "ec2_keypair": "kp",
                "s3_bucketname": "bucket",
            },
        )()

    @pytest.mark.asyncio
    async def test_it_finalizes_without_a_token(self, monkeypatch):
        """It used to require one, and that token could not exist: a 900s
        TTL against a 15-20 minute teardown, so it had always expired by
        the time the stack was gone (observed at 984s). See
        TestFinalizeTeardownNeedsNoTokenItCouldNeverHold."""
        monkeypatch.setattr(tools_mod, "_require_record", lambda name: self._rec())
        called = []
        monkeypatch.setattr(
            tools_mod,
            "core_delete_cluster",
            lambda **kw: called.append(kw) or {"success": True},
        )
        async with Client(build_local()) as client:
            await client.call_tool("finalize_cluster_teardown", {"cluster_name": "osiris"})
        assert len(called) == 1
        assert called[0]["finalize_only"] is True

    @pytest.mark.asyncio
    async def test_it_never_asks_the_core_to_wait(self, monkeypatch):
        """finalize_only is what makes this non-blocking; passing wait as
        well would be a second, contradictory answer to the same question."""
        monkeypatch.setattr(tools_mod, "_require_record", lambda name: self._rec())
        called = []
        monkeypatch.setattr(
            tools_mod,
            "core_delete_cluster",
            lambda **kw: called.append(kw) or {"success": True},
        )
        async with Client(build_local()) as client:
            await client.call_tool("finalize_cluster_teardown", {"cluster_name": "osiris"})
        assert "wait" not in called[0]

    @pytest.mark.asyncio
    async def test_a_token_minted_for_another_action_does_not_authorize_a_delete(self, monkeypatch):
        """A token binds an action as well as parameters. delete_cluster is
        the only tool left that takes one, so the interchangeability that
        used to be tested between the delete and finalize tokens is now
        tested against any other action's token."""
        from mcp_server.confirmation_token import mint

        monkeypatch.setattr(tools_mod, "_require_record", lambda name: self._rec())
        called = []
        monkeypatch.setattr(tools_mod, "core_delete_cluster", lambda **kw: called.append(kw))
        params = {"cluster_name": "osiris", "delete_s3_bucketname": True}
        async with Client(build_local()) as client:
            result = await client.call_tool(
                "delete_cluster",
                {
                    "cluster_name": "osiris",
                    "confirmation_token": mint("finalize_cluster_teardown", params),
                },
                raise_on_error=False,
            )
        assert result.is_error
        assert called == []

    @pytest.mark.asyncio
    async def test_a_token_from_a_different_cluster_is_refused(self, monkeypatch):
        monkeypatch.setattr(tools_mod, "_require_record", lambda name: self._rec())
        called = []
        monkeypatch.setattr(tools_mod, "core_delete_cluster", lambda **kw: called.append(kw))
        async with Client(build_local()) as client:
            preview = json.loads(
                _text(await client.call_tool("preview_cluster_delete", {"cluster_name": "osiris"}))
            )
            result = await client.call_tool(
                "delete_cluster",
                {
                    "cluster_name": "production",
                    "confirmation_token": preview["confirmation_token"],
                },
                raise_on_error=False,
            )
        assert result.is_error
        assert called == []

    @pytest.mark.asyncio
    async def test_the_preview_mints_no_token_for_a_call_that_takes_none(self, monkeypatch):
        """It minted a `finalization_token` and its next_step told the
        caller to pass it to finalize_cluster_teardown, which stopped
        accepting a token when auto-finalize landed -- so an agent that
        followed the instructions got a schema error. Wrong instructions on
        the surface an agent reads to decide what to do next are worse than
        no instructions."""
        monkeypatch.setattr(tools_mod, "_require_record", lambda name: self._rec())
        async with Client(build_local()) as client:
            preview = json.loads(
                _text(await client.call_tool("preview_cluster_delete", {"cluster_name": "osiris"}))
            )
        assert "finalization_token" not in preview
        assert "finalization_token" not in preview["next_step"]

    @pytest.mark.asyncio
    async def test_finalize_takes_no_token_at_all(self, monkeypatch):
        """The inverse of the requirement this class used to encode: a
        token here could not be satisfied on its intended path, because a
        teardown outlives the 900s TTL of the token minted before it."""
        monkeypatch.setattr(tools_mod, "_require_record", lambda name: self._rec())
        monkeypatch.setattr(tools_mod, "core_delete_cluster", lambda **kw: {"success": True})
        async with Client(build_local()) as client:
            tool = {t.name: t for t in await client.list_tools()}["finalize_cluster_teardown"]
            assert "confirmation_token" not in tool.inputSchema.get("properties", {})
            await client.call_tool("finalize_cluster_teardown", {"cluster_name": "osiris"})

    @pytest.mark.asyncio
    async def test_the_remote_transport_gets_it_too(self):
        """The gap this closes is specifically the remote one: locally an
        operator can always fall back to kill_pcluster.py, but a network
        caller has no filesystem and no CLI, so without this tool a remote
        teardown could never be completed at all."""
        async with Client(build_remote()) as client:
            names = {t.name for t in await client.list_tools()}
        assert "finalize_cluster_teardown" in names

    def test_it_carries_the_same_blast_radius_as_the_delete(self):
        """It destroys IAM policies, an S3 bucket and credentials, so it
        belongs on the tier that already covers that -- not read-only,
        where its name might suggest a mere bookkeeping step."""
        from mcp_server.tiers import TOOL_TIERS

        assert TOOL_TIERS["finalize_cluster_teardown"] == TOOL_TIERS["delete_cluster"]
        assert TOOL_TIERS["finalize_cluster_teardown"] == "stack-mutation"


class TestOneServerManagesOneRegion:
    """The record store is per account+region, so a server answers for the
    region it runs in and no other. That is a design limit, not an
    oversight -- cross-region discovery would mean scanning every region's
    bucket on every listing -- and the deployed IAM matches it: each tier's
    MCPStateAccess policy names one bucket, `-locks-<acct>-<region>`.

    Two things follow, and both are pinned here: the operator must be told
    *why* a cluster is invisible rather than that it does not exist, and a
    record that contradicts the bucket it was found in must be refused
    rather than acted on.
    """

    def _stub(self, monkeypatch, *, local=None, stored=None, store_region="us-east-1"):
        import mcp_server.tools as t

        def _read(cluster_name, repo_root, s3=None, locks_bucketname=None):
            return stored if s3 is not None else local

        monkeypatch.setattr(t, "_read_cluster_record", _read)
        monkeypatch.setattr(t, "_record_store", lambda region=None: ("s3", "bucket"))
        monkeypatch.setattr(t, "_store_region", lambda: store_region)
        return t

    def _rec(self, region):
        """A full ClusterRecord projection -- from_dict wants all 22 fields,
        and a thin dict would fail for a reason unrelated to the guard."""
        return {
            "cluster_name": "osiris",
            "cluster_owner": "rmarable",
            "serial": "osiris-1",
            "region": region,
            "headnode_instance_type": "c8g.large",
            "enable_loginnode": "false",
            "loginnode_instance_type": "",
            "loginnode_count": 0,
            "cpu_instance_types": ["c8g.xlarge"],
            "gpu_instance_types": [],
            "enable_cpu_queue": "true",
            "enable_gpu_queue": "false",
            "initial_cpu_queue_size": 0,
            "max_cpu_queue_size": 8,
            "initial_gpu_queue_size": 0,
            "max_gpu_queue_size": 0,
            "cluster_type": "spot",
            "deployment_date": "2026-08-24",
            "ssh_keypair": "/dev/null/x.pem",
            "ec2_keypair": "kp",
            "ec2_user": "ubuntu",
            "s3_bucketname": "bucket",
            "enable_monitoring": "false",
        }

    def test_a_store_record_naming_another_region_is_refused(self, monkeypatch):
        """The bucket is derived from the cluster's own region when the
        record is published, so these agree by construction. Disagreeing
        means something published to the wrong bucket — and acting on it
        would send every later store call in the request to a bucket this
        record was not found in, which on a Lambda granted one region is an
        opaque AccessDenied rather than anything diagnosable."""
        from pcluster_core import PClusterMakerError

        t = self._stub(monkeypatch, stored=self._rec("us-west-2"), store_region="us-east-1")
        with pytest.raises(PClusterMakerError) as exc:
            t._require_record("osiris")
        msg = str(exc.value)
        assert "us-east-1" in msg and "us-west-2" in msg, (
            "the refusal must name both regions — which store it came from "
            "and which region it claims — or it cannot be acted on"
        )
        assert "vars/osiris.json" in msg

    def test_a_local_record_may_name_any_region(self, monkeypatch):
        """The case the guard must not break, and the reason it is scoped to
        the store branch: an operator's own checkout legitimately holds a
        vars file for a cluster in any region, and reading it first is what
        lets that cluster's region address its own bucket afterward."""
        t = self._stub(monkeypatch, local=self._rec("us-west-2"), store_region="us-east-1")
        rec = t._require_record("osiris")
        assert rec.region == "us-west-2"

    def test_a_store_record_in_its_own_region_passes(self, monkeypatch):
        """Vacuity guard: refusing everything would satisfy the test above."""
        t = self._stub(monkeypatch, stored=self._rec("us-east-1"), store_region="us-east-1")
        assert t._require_record("osiris").region == "us-east-1"

    def test_an_unresolvable_store_region_does_not_false_refuse(self, monkeypatch):
        """With no region resolvable there is nothing to compare against, so
        the guard has no opinion. Treating "" as a mismatch would refuse
        every call on a server whose region cannot be determined — failing
        closed on the wrong axis, since the record itself is fine."""
        t = self._stub(monkeypatch, stored=self._rec("us-west-2"), store_region="")
        assert t._require_record("osiris").region == "us-west-2"

    def test_a_record_carrying_no_region_is_not_called_a_mismatch(self, monkeypatch):
        """A record with no region is missing data, not contradictory data,
        so the guard must abstain rather than invent a disagreement with the
        store's region. Such a record fails anyway — ClusterRecord requires
        the field — but it must fail as malformed, which is what an operator
        can act on, and not as a region conflict that does not exist."""
        rec = self._rec("us-east-1")
        del rec["region"]
        t = self._stub(monkeypatch, stored=rec, store_region="us-east-1")
        with pytest.raises(Exception) as exc:
            t._require_record("osiris")
        assert "record store but its record says" not in str(exc.value)

    def test_the_not_found_message_explains_the_single_region_limit(self, monkeypatch):
        """'No cluster named X is tracked here' reads as 'it does not exist'.
        For a cluster that is up and healthy in another region that is
        actively misleading, and the remedy — use that region's endpoint —
        is not guessable from the old wording."""
        from pcluster_core import PClusterMakerError

        t = self._stub(monkeypatch, store_region="us-east-1")
        with pytest.raises(PClusterMakerError) as exc:
            t._require_record("osiris")
        msg = str(exc.value)
        assert "us-east-1" in msg
        assert "one region" in msg
        assert "endpoint" in msg, "the message must name the remedy, not just the fact"


class TestTheSanitizerIsWhatGuaranteesTheRecordShape:
    """`ClusterRecord.from_dict` indexes every field with `rec[f]` and has no
    guard, which reads like a latent `KeyError` on a truncated record. It is
    not, and the reason is worth pinning: `_sanitize_record` is a *total*
    projection — it builds the dict key by key with a default for each — and
    it sits at the single read point every caller goes through. A record
    that reaches `from_dict` therefore always has every field, whatever the
    stored object looked like.

    Written after building a guard for the missing-field case and then
    discovering, only by seeding a real two-field object into a real bucket,
    that the case cannot occur. The unit tests that made the guard look
    necessary stubbed `_read_cluster_record` — the very function whose
    behavior the question turned on. Hence the last test here, which drives
    the real one.

    If the sanitizer ever stops being total, this fires and the guard
    becomes worth having again.
    """

    def test_the_sanitizer_supplies_every_field_from_nothing(self):
        import dataclasses

        from pcluster_core import ClusterRecord, _sanitize_record

        out = _sanitize_record({}, "osiris")
        expected = {f.name for f in dataclasses.fields(ClusterRecord)}
        assert expected - set(out) == set(), (
            "a field ClusterRecord requires is not defaulted by the sanitizer"
        )

    def test_from_dict_accepts_what_the_sanitizer_produces(self):
        from pcluster_core import ClusterRecord, _sanitize_record

        rec = ClusterRecord.from_dict(_sanitize_record({}, "osiris"))
        assert rec.cluster_name == "osiris"

    def test_a_truncated_store_object_still_yields_a_whole_record(
        self,
        monkeypatch,
        tmp_path,
    ):
        """The real `_read_cluster_record`, not a stub of it.

        A two-field object in the store — the shape a hand-edit or a
        truncated write leaves behind — comes back complete, with blanks
        where the data was missing. That is deliberate: `list_clusters`
        showing a row with empty columns is more use than a listing that
        omits the cluster or refuses to render at all.
        """
        import pcluster_core

        monkeypatch.setattr(
            pcluster_core,
            "get_cluster_record",
            lambda s3, **kw: {"cluster_name": "probe", "region": "us-east-1"},
        )
        rec = pcluster_core._read_cluster_record(
            "probe",
            str(tmp_path),
            s3=object(),
            locks_bucketname="bucket",
        )
        assert rec is not None
        built = pcluster_core.ClusterRecord.from_dict(rec)
        assert built.cluster_name == "probe"
        assert built.region == "us-east-1"
        assert built.cluster_owner == ""
        assert built.cpu_instance_types == []


class TestCreateClusterCannotKillTheServer:
    """`core_create_cluster` used to `sys.exit()` on every path, success
    included. `SystemExit` is a `BaseException`, and this tool cannot use
    `_cluster_lock`'s SystemExit translation because the core locks
    internally and wrapping would deadlock — so a *successful* MCP build
    killed the server instead of returning. Confirmed live on 2026-08-25:
    the call never returned and the transport disconnected.

    Every test here drives the tool through a real client session, because
    that is the only place the failure was visible — a direct call to the
    wrapper would raise SystemExit into the test runner and look like a
    plain error.
    """

    @pytest.fixture(autouse=True)
    def _no_real_aws(self, monkeypatch):
        """Minting the token runs the real preview_cluster_config, which
        resolves the region from EC2 (`resolve_region_from_az` asks AWS
        rather than trimming the AZ name, deliberately). Unstubbed that is
        a live API call: green on a developer machine that has credentials,
        `NoCredentialsError` on a CI runner that does not. This class is
        about what create_cluster returns, not about region resolution.
        """
        monkeypatch.setattr(
            tools_mod,
            "resolve_region_from_az",
            lambda az, **kw: "us-east-1",
        )

    # conftest points defaults-file discovery at an empty directory, so a
    # compute type has to be supplied explicitly or the preview is refused
    # for having no queue (checklist 0.6) before it can mint a token.
    _ARGS = dict(
        cluster_name="osiris",
        cluster_owner="rmarable",
        cluster_owner_email="rmarable@gmail.com",
        az="us-east-1a",
        headnode_instance_type="c8g.large",
        overrides={"compute_instance_type": "c7g.large"},
    )

    async def _token(self, c):
        r = await c.call_tool("preview_cluster_config", dict(self._ARGS), raise_on_error=False)
        return json.loads(r.content[0].text)["confirmation_token"]

    @pytest.mark.asyncio
    async def test_a_kicked_off_build_returns_a_result(self, monkeypatch):
        from pcluster_core import CreateClusterResult

        monkeypatch.setattr(
            tools_mod,
            "core_create_cluster",
            lambda **kw: CreateClusterResult(
                cluster_name="osiris", success=True, exit_code=0, kicked_off=True, message="started"
            ),
        )
        async with Client(build_local()) as c:
            tok = await self._token(c)
            r = await c.call_tool(
                "create_cluster", {**self._ARGS, "confirmation_token": tok}, raise_on_error=False
            )
        assert not r.is_error, r.content[0].text if r.content else ""
        payload = json.loads(r.content[0].text)
        assert payload["kicked_off"] is True
        assert payload["success"] is True

    @pytest.mark.asyncio
    async def test_a_validation_sys_exit_becomes_a_tool_error(self, monkeypatch):
        """The shared validation helpers (p_fail/refer_to_docs_and_quit)
        still sys.exit(1) with the message already printed. Those are not
        converted, so the wrapper's narrow SystemExit net is what keeps one
        bad input from taking the whole server down."""

        def _boom(**kw):
            raise SystemExit(1)

        monkeypatch.setattr(tools_mod, "core_create_cluster", _boom)
        async with Client(build_local()) as c:
            tok = await self._token(c)
            r = await c.call_tool(
                "create_cluster", {**self._ARGS, "confirmation_token": tok}, raise_on_error=False
            )
            assert r.is_error
            # The session must still be usable -- that is the whole point.
            alive = await c.call_tool("list_clusters", {}, raise_on_error=False)
        assert not alive.is_error, "the server did not survive the SystemExit"

    @pytest.mark.asyncio
    async def test_a_failed_build_is_reported_not_raised(self, monkeypatch):
        from pcluster_core import CreateClusterResult

        monkeypatch.setattr(
            tools_mod,
            "core_create_cluster",
            lambda **kw: CreateClusterResult(
                cluster_name="osiris", success=False, exit_code=1, message="build failed"
            ),
        )
        async with Client(build_local()) as c:
            tok = await self._token(c)
            r = await c.call_tool(
                "create_cluster", {**self._ARGS, "confirmation_token": tok}, raise_on_error=False
            )
        payload = json.loads(r.content[0].text)
        assert payload["success"] is False and payload["exit_code"] == 1


class TestFinalizeClusterBuildIsReachableRemotely:
    """It was local-only, and the reason stopped being true.

    What made it local was the staging tree being scp'd to the head node:
    that needed the private key and a route, neither of which a Lambda has.
    The tree is now published to S3 before the stack is created and pulled
    by the head node during its own bootstrap, so finalizing reaches
    nothing -- and the vars file it reads rides beside the record in the
    store rather than existing only on the building machine.

    Without this, a cluster built from the browser could not be finished
    from the browser, which is most of what building it there was for.
    """

    @pytest.mark.asyncio
    async def test_both_transports_have_it(self):
        for build in (build_local, build_remote):
            async with Client(build()) as c:
                names = {t.name for t in await c.list_tools()}
            assert "finalize_cluster_build" in names, f"missing from {build.__name__}"

    def test_it_is_no_longer_declared_local_only(self):
        """The split is data in _LOCAL_ONLY, never a second registration
        list -- so the two transports cannot disagree."""
        assert "finalize_cluster_build" not in tools_mod._LOCAL_ONLY

    def test_it_runs_on_the_tier_that_can_build(self):
        """Same tier as create_cluster. It renders the same templates and
        needs the same PCluster import, so a lighter tier would be a second
        answer to the question create_cluster already settled."""
        from mcp_server.tiers import TOOL_TIERS

        assert TOOL_TIERS["finalize_cluster_build"] == "stack-mutation-node"
        assert TOOL_TIERS["finalize_cluster_build"] == TOOL_TIERS["create_cluster"]

    def test_the_local_only_set_is_still_only_what_cannot_work_remotely(self):
        """Vacuity guard: the fix is removing one name, not emptying the
        set. Each of these is useless remotely rather than merely awkward
        -- two write files the operator later needs, one blocks past
        Lambda's ceiling, and `run_readwrite_slurm_command` submits
        arbitrary code, which must never be reachable from an
        internet-facing Lambda.

        `run_readonly_slurm_command` was here and is deliberately no
        longer: it reaches nodes through an SSM document whose
        allowedValues admit three read commands, so the grant that carries
        it cannot express anything else. That distinction -- bounded by
        the document rather than by our own code -- is what let the read
        half port while the write half stayed.

        An equality pin rather than a subset check, because adding a name
        here has to be argued: `check_cluster_health` and
        `diagnose_cluster` are SSH-dependent too and are deliberately
        absent, being mostly boto3 with an SSH tail where a partial answer
        is still useful.
        """
        assert tools_mod._LOCAL_ONLY == frozenset(
            {
                "rotate_cluster_key",
                "manage_grafana_tunnel",
                "apply_queue_config",
                "run_readwrite_slurm_command",
            }
        )

    @pytest.mark.asyncio
    async def test_it_takes_no_confirmation_token(self, monkeypatch):
        """It destroys nothing and only completes work the operator already
        authorized by building. A token here would be friction with no
        matching risk -- and would need a preview tool that has nothing to
        preview."""
        # The record's region must differ from the ambient one, or a tool
        # that wrongly used _store_region() would look correct: the test
        # environment sets AWS_REGION=us-east-2, so a fixture using that
        # cannot tell the two apart.
        rec = type(
            "R",
            (),
            {
                "region": "us-west-2",
                "cluster_owner": "testuser",
                "serial": "s",
                "ec2_keypair": "kp",
                "s3_bucketname": "b",
            },
        )()
        monkeypatch.setattr(tools_mod, "_require_record", lambda name: rec)
        monkeypatch.setattr(tools_mod, "_store_region", lambda: "us-east-2")
        called = []
        monkeypatch.setattr(
            tools_mod,
            "core_finalize_cluster_build",
            lambda **kw: called.append(kw) or {"success": True},
        )
        async with Client(build_local()) as c:
            tool = next(t for t in await c.list_tools() if t.name == "finalize_cluster_build")
            assert tool.inputSchema.get("required") == ["cluster_name"]
            r = await c.call_tool(
                "finalize_cluster_build", {"cluster_name": "certify"}, raise_on_error=False
            )
        assert not r.is_error, r.content[0].text if r.content else ""
        assert len(called) == 1
        assert called[0]["region"] == "us-west-2", "the cluster's region must be used"


class TestABuildCanWriteOnAReadOnlyDeployment:
    """Lambda mounts the package at /var/task read-only, and a build writes
    src/vars_files/<name>.yml and active_clusters/<name>/.  The first live
    create_cluster through the deployed image tier died on exactly that:
    OSError(30) 'Read-only file system'.  Nothing local could see it -- a
    developer checkout is writable, so resolve_writable_repo_root returns it
    untouched and every existing test exercises that branch.
    """

    def _read_only_tree(self, tmp_path):
        root = tmp_path / "task"
        (root / "templates").mkdir(parents=True)
        (root / "templates" / "vars_file.j2").write_text("{{ cluster_name }}")
        (root / "src").mkdir()
        (root / "src" / "pcluster_core.py").write_text("# module")
        (root / "scripts").mkdir()
        os.chmod(root / "src", 0o555)
        os.chmod(root, 0o555)
        return root

    def test_a_writable_root_is_returned_untouched(self, tmp_path):
        root = tmp_path / "checkout"
        root.mkdir()
        assert resolve_writable_repo_root(str(root)) == str(root)
        # and nothing was created beside it
        assert list(tmp_path.iterdir()) == [root]

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the mode bits")
    def test_a_read_only_root_yields_a_writable_overlay(self, tmp_path):
        root = self._read_only_tree(tmp_path)
        overlay = resolve_writable_repo_root(str(root), overlay_root=str(tmp_path / "overlay"))
        assert overlay != str(root)

        # the two written paths are real directories, and really writable
        for rel in ("active_clusters", os.path.join("src", "vars_files")):
            target = os.path.join(overlay, rel)
            assert os.path.isdir(target) and not os.path.islink(target)
            probe = os.path.join(target, "probe")
            with open(probe, "w") as fh:
                fh.write("ok")
            assert open(probe).read() == "ok"

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the mode bits")
    def test_the_source_tree_is_still_readable_through_the_overlay(self, tmp_path):
        root = self._read_only_tree(tmp_path)
        overlay = resolve_writable_repo_root(str(root), overlay_root=str(tmp_path / "overlay"))
        # a template the build renders, and a module under src/, both reachable
        assert (
            open(os.path.join(overlay, "templates", "vars_file.j2")).read() == "{{ cluster_name }}"
        )
        assert open(os.path.join(overlay, "src", "pcluster_core.py")).read() == "# module"

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the mode bits")
    def test_resolving_twice_is_not_an_error(self, tmp_path):
        """A warm container calls this again; the overlay already exists."""
        root = self._read_only_tree(tmp_path)
        first = resolve_writable_repo_root(str(root), overlay_root=str(tmp_path / "overlay"))
        second = resolve_writable_repo_root(str(root), overlay_root=str(tmp_path / "overlay"))
        assert first == second

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the mode bits")
    def test_the_probe_is_what_detects_it_not_the_permission_bits(self, tmp_path):
        """os.access(W_OK) answers from the mode bits and reports a read-only
        *filesystem* as writable, which is the case that shipped.  The
        detection has to be an actual write."""
        root = self._read_only_tree(tmp_path)
        assert _is_writable_dir(str(root)) is False
        writable = tmp_path / "rw"
        writable.mkdir()
        assert _is_writable_dir(str(writable)) is True

    def test_the_probe_leaves_nothing_behind(self, tmp_path):
        root = tmp_path / "rw"
        root.mkdir()
        assert _is_writable_dir(str(root)) is True
        assert list(root.iterdir()) == []


class TestDeleteClusterSendsTheCallerToFinalize:
    """`delete_cluster` removes the stack and nothing else, and used to tell
    the caller to "re-run" it.

    That is the one recovery this repo documents as dangerous: a second
    `delete_cluster` does finish the job, but by issuing another
    delete-cluster against the *name* -- which destroys a different cluster
    if that name has been rebuilt since, and silently no-ops while the
    stack is still deleting, reporting success either way.
    `finalize_cluster_teardown` is that second call made explicit and safe,
    and the tool never mentioned it.

    So an operator who said "tear the cluster down" got a stack deleted and
    a bill for the IAM policies, bucket, secret, topic and record -- not
    because the agent went off-script, but because the script said to.
    """

    def _doc(self):
        import mcp_server.tools as t

        import inspect

        src = inspect.getsource(t)
        start = src.index("def delete_cluster(")
        return src[start : src.index("@tool", start + 10)]

    def test_it_names_the_tool_that_finishes_the_job(self):
        assert "finalize_cluster_teardown" in self._doc(), (
            "delete_cluster never mentions the tool that completes a teardown"
        )

    def test_it_does_not_tell_the_caller_to_re_run_itself(self):
        """The wording that shipped. Banned by shape, not by that exact
        phrase: any instruction to repeat this call is the unsafe path."""
        doc = self._doc()
        head = doc[: doc.index('"""', doc.index('"""') + 3)]
        assert "re-run" not in head.lower(), (
            "the docstring still tells the caller to re-run delete_cluster"
        )

    def test_it_says_a_second_delete_is_unsafe(self):
        doc = self._doc()
        assert "rebuilt" in doc, (
            "the docstring does not say why a second delete_cluster is "
            "dangerous, so the reasoning cannot survive an edit"
        )

    def test_the_response_carries_the_next_step_not_just_the_docstring(self):
        """A tool description is read once when the tool list loads. The
        response is read at the moment the caller decides what to do next,
        which is when it matters -- the same reason
        preview_cluster_config carries a next_step."""
        doc = self._doc()
        assert 'out["next_step"]' in doc
        assert 'out["teardown_complete"] = False' in doc, (
            "nothing in the response says the teardown is incomplete"
        )


class TestFinalizeTeardownNeedsNoTokenItCouldNeverHold:
    """The finalization token expired before it could ever be used.

    `preview_cluster_delete` minted it with a 900s TTL and a teardown takes
    15-20 minutes, so by the time the stack was gone the token had always
    expired -- observed live at 984s against a 900s TTL. Every finalize
    therefore required a fresh preview first: a confirmation prompt for a
    destruction the operator had already confirmed, on a call that can only
    run after the destructive part is over.

    A gate that cannot be satisfied on its intended path is not a safety
    measure. `finalize_cluster_build` already takes no token for completing
    authorized work; its teardown twin now matches.
    """

    def test_it_takes_no_confirmation_token(self):
        import inspect

        import mcp_server.tools as t

        src = inspect.getsource(t)
        start = src.index("def finalize_cluster_teardown(")
        sig = src[start : src.index(")", start)]
        assert_source_is_real(sig, "test_it_takes_no_confirmation_token")
        assert "confirmation_token" not in sig, (
            "finalize_cluster_teardown still takes a token it cannot be "
            "given: the TTL is shorter than the teardown it follows"
        )

    def test_delete_cluster_still_requires_one(self):
        """The gate moves, it does not disappear. `delete_cluster` is the
        call that decides anything; this one only finishes it."""
        import inspect

        import mcp_server.tools as t

        src = inspect.getsource(t)
        start = src.index("def delete_cluster(")
        sig = src[start : src.index(")", start)]
        assert "confirmation_token" in sig

    def test_the_reason_is_recorded_so_it_is_not_added_back(self):
        import inspect

        import mcp_server.tools as t

        src = inspect.getsource(t)
        start = src.index("def finalize_cluster_teardown(")
        doc = src[start : src.index("rec = _require_record", start)]
        assert "TTL" in doc and "900" in doc, (
            "the docstring does not say why the token was removed, so the "
            "next reader will restore it"
        )


class TestADeletedClusterIsNotReportedAsAnError:
    """`list_clusters(live=True)` returned `ERR` for a cluster whose stack
    was gone -- the *expected* end state of a delete.

    A caller reasoned past it correctly ("expected end state for a completed
    delete, not a failure"), but only because it knew to. Reporting the
    normal outcome as an error trains the next reader to discount the
    column, which is the one that matters during a teardown.
    """

    def test_an_exception_whose_str_is_empty_is_still_recognized(self):
        """The failure this actually had. A pcluster.lib exception's `str()`
        is empty -- `ParallelClusterApiException.__init__` calls
        `super().__init__()` with no arguments -- so a predicate built on
        `str(exc)` sees "NotFoundException: " and matches nothing.

        It cost a live teardown: every describe of an already-deleted stack
        read as "unreadable", so the poller never finalized and ran to its
        bound instead. CLAUDE.md names this trap; this is the guard that
        makes it fail here rather than in production.
        """
        from pcluster_core import _describe_says_cluster_is_absent

        class NotFoundException(Exception):
            def __init__(self):
                super().__init__()

        e = NotFoundException()
        assert str(e) == "", "the fixture is not reproducing the trap"
        assert _describe_says_cluster_is_absent(e), (
            "absence is decided from str(exc), which is empty for exactly "
            "the exception that means absence"
        )

    def test_a_confirmed_absence_reads_as_deleted(self):
        from pcluster_core import PClusterMakerError, _describe_says_cluster_is_absent

        for msg in ("ClusterNotFound: no such cluster", "Cluster 'osiris' does not exist"):
            assert _describe_says_cluster_is_absent(PClusterMakerError(msg)), msg

    def test_any_other_failure_is_still_an_error(self):
        """The narrowness is the point. A describe that could not be
        answered is not evidence a cluster is gone -- the same rule
        `_confirm_stack_is_gone` is built on. Widening this would report
        every cluster as DELETED the moment a token expired."""
        from pcluster_core import PClusterMakerError, _describe_says_cluster_is_absent

        for msg in (
            "ExpiredToken: credentials expired",
            "Throttling: rate exceeded",
            "EndpointConnectionError: could not connect",
            "AccessDenied: not authorized",
        ):
            assert not _describe_says_cluster_is_absent(PClusterMakerError(msg)), msg

    def test_live_status_returns_deleted_for_an_absent_cluster(self, monkeypatch):
        import pcluster_core as pc

        def gone(*a, **kw):
            raise pc.PClusterMakerError("ClusterNotFound: it is gone")

        monkeypatch.setattr(pc, "_describe_cluster_json", gone)
        assert pc._live_status("osiris", "us-east-1", "/bin/pcluster") == "DELETED"

    def test_live_status_still_returns_err_for_an_unreadable_one(self, monkeypatch):
        import pcluster_core as pc

        def broke(*a, **kw):
            raise pc.PClusterMakerError("ExpiredToken: credentials expired")

        monkeypatch.setattr(pc, "_describe_cluster_json", broke)
        assert pc._live_status("osiris", "us-east-1", "/bin/pcluster") == "ERR"
