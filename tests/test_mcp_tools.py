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

    @pytest.fixture(autouse=True)
    def _offline_az(self, monkeypatch):
        _stub_az(monkeypatch)

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
        token_params = dict(
            params, overrides={"custom_ami": "ami-0abc"}, defaults=None
        )
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

    @pytest.fixture(autouse=True)
    def _offline_az(self, monkeypatch):
        _stub_az(monkeypatch)

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
        cluster_name="osiris", cluster_owner="testuser",
        cluster_owner_email="testuser@example.com", az="us-east-2a",
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
        token_params = dict(
            self._REQ, overrides=dict(sorted(overrides.items())), defaults=None
        )
        return dict(
            self._REQ, overrides=overrides,
            confirmation_token=mint("create_cluster", token_params),
        )

    @pytest.mark.asyncio
    async def test_a_real_region_reaches_the_core_function(self, monkeypatch):
        """The regression: this raised AttributeError before the fix."""
        calls, seen = [], []
        self._stub_ec2(monkeypatch, [{"RegionName": "us-east-2"}], seen)
        self._stub_core(monkeypatch, calls)

        async with Client(build_local()) as client:
            result = await client.call_tool(
                "create_cluster", self._args(), raise_on_error=False
            )

        assert not result.is_error, _text(result)
        assert len(calls) == 1
        assert calls[0]["region"] == "us-east-2"
        assert seen == [["us-east-2a"]], "the AZ actually asked about"

    @pytest.mark.asyncio
    async def test_the_region_comes_from_ec2_not_from_trimming_the_az(
        self, monkeypatch
    ):
        """az[:-1] is right for every AZ _validate_az_input accepts, so a
        string trim passes the test above and still skips the call that
        proves the AZ exists. Only a divergent answer separates them."""
        calls = []
        self._stub_ec2(monkeypatch, [{"RegionName": "eu-west-1"}])
        self._stub_core(monkeypatch, calls)

        async with Client(build_local()) as client:
            result = await client.call_tool(
                "create_cluster", self._args(), raise_on_error=False
            )

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
            result = await client.call_tool(
                "create_cluster", self._args(), raise_on_error=False
            )

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
        cluster_name="osiris", cluster_owner="testuser",
        cluster_owner_email="testuser@example.com", az="us-east-2a",
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
            return await client.call_tool(
                "preview_cluster_config", args, raise_on_error=False
            )

    @pytest.mark.asyncio
    async def test_the_preview_reports_the_region_it_verified(
        self, monkeypatch
    ):
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
        cluster_name="osiris", cluster_owner="testuser",
        cluster_owner_email="testuser@example.com", az="us-east-2a",
        headnode_instance_type="c5.xlarge",
    )

    @pytest.fixture(autouse=True)
    def _offline_az(self, monkeypatch):
        _stub_az(monkeypatch)

    def _write(self, tmp_path, monkeypatch, contents):
        import yaml as _yaml

        (tmp_path / "osiris_defaults.yml").write_text(_yaml.safe_dump(contents))
        monkeypatch.setattr(
            "pcluster_core._default_repo_root", lambda: str(tmp_path)
        )

    async def _preview(self):
        async with Client(build_local()) as client:
            return await client.call_tool(
                "preview_cluster_config", dict(self._REQ), raise_on_error=False
            )

    async def _create(self, token):
        args = dict(self._REQ, confirmation_token=token)
        async with Client(build_local()) as client:
            return await client.call_tool(
                "create_cluster", args, raise_on_error=False
            )

    @pytest.mark.asyncio
    async def test_the_preview_applies_the_file_and_names_it(
        self, tmp_path, monkeypatch
    ):
        """No overrides are passed at all here -- the compute queue comes
        from the file. Before this, the same call was refused for having no
        queue, with the operator's own file sitting unread beside it."""
        self._write(tmp_path, monkeypatch, {
            "compute_instance_type": "c6g.8xlarge", "cluster_type": "spot",
        })

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
            tools_mod, "core_create_cluster",
            lambda **kw: calls.append(kw) or {"status": "kicked off"},
        )
        self._write(tmp_path, monkeypatch, {"compute_instance_type": "c6g.8xlarge"})
        token = json.loads(_text(await self._preview()))["confirmation_token"]

        self._write(tmp_path, monkeypatch, {"compute_instance_type": "c5.24xlarge"})
        result = await self._create(token)

        assert result.is_error
        assert calls == [], "a build must not start on a stale preview"

    @pytest.mark.asyncio
    async def test_an_untouched_file_still_authorizes_the_build(
        self, tmp_path, monkeypatch
    ):
        """Vacuity guard. A binding that rejected every token would satisfy
        the test above and break the tool."""
        calls = []
        monkeypatch.setattr(
            tools_mod, "core_create_cluster",
            lambda **kw: calls.append(kw) or {"status": "kicked off"},
        )
        self._write(tmp_path, monkeypatch, {"compute_instance_type": "c6g.8xlarge"})
        token = json.loads(_text(await self._preview()))["confirmation_token"]

        result = await self._create(token)

        assert not result.is_error, _text(result)
        assert calls[0]["params"].compute_instance_type == "c6g.8xlarge"

    @pytest.mark.asyncio
    async def test_the_preview_does_not_call_a_file_value_a_default(
        self, tmp_path, monkeypatch
    ):
        """notable_defaults filtered on `overrides` alone, so a file that
        set base_os=ubuntu2404arm still had the preview reporting
        base_os=ubuntu2404 -- the wrong OS, stated to the operator who is
        about to approve the build."""
        from pcluster_core import MAKE_CLUSTER_DEFAULTS

        assert MAKE_CLUSTER_DEFAULTS["base_os"] != "ubuntu2404arm"
        self._write(tmp_path, monkeypatch, {
            "compute_instance_type": "c6g.8xlarge", "base_os": "ubuntu2404arm",
        })

        payload = json.loads(_text(await self._preview()))

        assert payload["resolved_config"]["base_os"] == "ubuntu2404arm"
        assert "base_os" not in payload["notable_defaults"]
        assert payload["defaults_file_settings"]["base_os"] == "ubuntu2404arm"

    @pytest.mark.asyncio
    async def test_a_default_the_file_leaves_alone_is_still_reported(
        self, tmp_path, monkeypatch
    ):
        """Vacuity guard: emptying notable_defaults would satisfy the test
        above and destroy the block's purpose."""
        self._write(tmp_path, monkeypatch, {"compute_instance_type": "c6g.8xlarge"})

        payload = json.loads(_text(await self._preview()))

        assert "base_os" in payload["notable_defaults"]
        assert "compute_instance_type" not in payload["notable_defaults"]

    @pytest.mark.asyncio
    async def test_a_non_build_key_is_not_reported_as_a_setting(
        self, tmp_path, monkeypatch
    ):
        """delete_s3_bucketname is in the file for kill_pcluster.py. The
        build ignores it, so reporting it as something this cluster was
        configured with would be a third wrong claim in the same block."""
        self._write(tmp_path, monkeypatch, {
            "compute_instance_type": "c6g.8xlarge", "delete_s3_bucketname": "true",
        })

        payload = json.loads(_text(await self._preview()))

        assert "delete_s3_bucketname" not in payload["defaults_file_settings"]
        assert payload["defaults_file_settings"]["compute_instance_type"] == "c6g.8xlarge"

    @pytest.mark.asyncio
    async def test_a_defaults_file_may_set_what_an_override_may_not(
        self, tmp_path, monkeypatch
    ):
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
        self._write(tmp_path, monkeypatch, {
            "compute_instance_type": "c6g.8xlarge",
            "post_install_script": "scripts/post-deployment.sh",
        })

        payload = json.loads(_text(await self._preview()))
        assert payload["resolved_config"]["post_install_script"] == (
            "scripts/post-deployment.sh"
        )

        args = dict(self._REQ, overrides={
            "compute_instance_type": "c6g.8xlarge",
            "post_install_script": "scripts/post-deployment.sh",
        })
        async with Client(build_local()) as client:
            refused = await client.call_tool(
                "preview_cluster_config", args, raise_on_error=False
            )
        assert refused.is_error, "the same value as an override is still refused"
        assert "post_install_script" in _text(refused)
