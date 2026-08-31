"""Workstream 7 layer 4: the S3 distributed lock's MCP-side integration.

Deliberately not the lock mechanism itself. That lives in
tests/test_s3_cluster_lock.py (31 tests) and its conditional-write
atomicity was confirmed against real S3 in round 29. What is tested here
is whether the MCP tools that mutate a live cluster actually take the
lock -- a different question, and one the answer to was "no" when this
file was written.

The gap this file closed: the migration plan specifies that the
fleet-toggle tier "must acquire/release the distributed lock around the
call", and MCPStateAccessFleetToggle.json_src grants `locks/*` on that
basis -- but `_core_fleet_action` (behind core_stop_fleet/
core_start_fleet) and core_apply_queue_config contained no lock calls at
all. The IAM said one thing and the code did another.

Where the lock lives is the load-bearing decision. It is held at the
wrapper layer, not pushed into the core functions, because those are also
the CLI's code path: locking there would make `stop_pcluster.py` during
an in-flight build begin failing fast, and the plan's standing constraint
is that CLI behavior does not change. create/delete are the exception in
the other direction -- core_create_cluster and core_delete_cluster
already lock internally (round 27), so their tools must NOT be wrapped
again or they would deadlock against their own acquisition.
"""

import ast
import inspect
import os
import sys

from conftest import assert_source_is_real
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

import mcp_server.tools as tools_mod  # noqa: E402
import pcluster_core  # noqa: E402
from fastmcp import Client  # noqa: E402
from mcp_server.server import build_local  # noqa: E402

# Tools that mutate a live cluster via a core function that does NOT lock
# internally -- these must be wrapped by the tool layer.
_MUST_WRAP = ("stop_fleet", "start_fleet", "apply_queue_config")

# Tools whose core function already acquires the lock itself.
_LOCKS_INTERNALLY = ("delete_cluster",)


class _LockSpy:
    """Records acquire/release without touching AWS."""

    def __init__(self, held=None):
        self.acquired = []
        self.released = []
        self._held = set(held or ())

    def acquire(self, s3, *, locks_bucketname, region, cluster_name, command, describe_fn=None):
        if cluster_name in self._held:
            raise SystemExit(
                f"ERROR: another operation is already running against cluster {cluster_name!r}."
            )
        self.acquired.append(
            {"cluster": cluster_name, "bucket": locks_bucketname, "command": command}
        )
        return f"locks/{cluster_name}.lock"

    def release(self, s3, *, locks_bucketname, cluster_name):
        self.released.append(cluster_name)


@pytest.fixture
def spy(monkeypatch):
    s = _LockSpy()
    monkeypatch.setattr(tools_mod, "_acquire_distributed_cluster_lock", s.acquire)
    monkeypatch.setattr(tools_mod, "s3_release_cluster_lock", s.release)
    monkeypatch.setattr(tools_mod, "_aws_account_id", lambda: "123456789012")
    monkeypatch.setattr(
        tools_mod,
        "_require_record",
        lambda name: type(
            "R",
            (),
            {
                "region": "us-east-2",
                "cluster_owner": "o",
                "serial": "s",
                "ec2_keypair": "kp",
                "s3_bucketname": "b",
            },
        )(),
    )
    for core in ("core_stop_fleet", "core_start_fleet", "core_apply_queue_config"):
        monkeypatch.setattr(tools_mod, core, lambda **kw: {"ok": True})
    return s


async def _call(tool, args):
    async with Client(build_local()) as client:
        return await client.call_tool(tool, args, raise_on_error=False)


class TestMutatingToolsTakeTheLock:
    @pytest.mark.parametrize("tool", _MUST_WRAP)
    @pytest.mark.asyncio
    async def test_the_lock_is_acquired(self, tool, spy):
        args = {"cluster_name": "osiris"}
        if tool == "apply_queue_config":
            args["config_path"] = "/tmp/cfg.yaml"
        await _call(tool, args)
        assert [a["cluster"] for a in spy.acquired] == ["osiris"]

    @pytest.mark.parametrize("tool", _MUST_WRAP)
    @pytest.mark.asyncio
    async def test_the_lock_is_released(self, tool, spy):
        args = {"cluster_name": "osiris"}
        if tool == "apply_queue_config":
            args["config_path"] = "/tmp/cfg.yaml"
        await _call(tool, args)
        assert spy.released == ["osiris"]

    @pytest.mark.asyncio
    async def test_the_lock_targets_the_account_and_region_scoped_bucket(self, spy):
        await _call("stop_fleet", {"cluster_name": "osiris"})
        assert spy.acquired[0]["bucket"] == ("parallelclustermaker-locks-123456789012-us-east-2")

    @pytest.mark.asyncio
    async def test_the_owner_string_names_the_mcp_tool(self, spy):
        """The lock's owner field is what a blocked caller is shown. 'mcp
        stop_fleet' tells an operator staring at a stuck cluster that a
        remote tool call holds it, not a local CLI run."""
        await _call("stop_fleet", {"cluster_name": "osiris"})
        assert "stop_fleet" in spy.acquired[0]["command"]
        assert "mcp" in spy.acquired[0]["command"]

    @pytest.mark.asyncio
    async def test_the_lock_is_released_even_when_the_core_call_fails(self, spy, monkeypatch):
        """A leaked lock blocks every later operation on that cluster. The
        S3 lock's staleness path would eventually reclaim it, but only
        after the ceiling -- an immediate release is the difference between
        a retry working now and working in two hours."""

        def _boom(**kw):
            raise RuntimeError("fleet call failed")

        monkeypatch.setattr(tools_mod, "core_stop_fleet", _boom)
        result = await _call("stop_fleet", {"cluster_name": "osiris"})
        assert result.is_error
        assert spy.released == ["osiris"], "the lock must be released on the failure path"

    @pytest.mark.asyncio
    async def test_a_held_lock_stops_the_tool_before_it_mutates(self, monkeypatch):
        """The property the lock exists for: a concurrent build or teardown
        holding the lock must stop the tool from touching the fleet."""
        s = _LockSpy(held={"osiris"})
        monkeypatch.setattr(tools_mod, "_acquire_distributed_cluster_lock", s.acquire)
        monkeypatch.setattr(tools_mod, "s3_release_cluster_lock", s.release)
        monkeypatch.setattr(tools_mod, "_aws_account_id", lambda: "123456789012")
        monkeypatch.setattr(
            tools_mod, "_require_record", lambda name: type("R", (), {"region": "us-east-2"})()
        )
        called = []
        monkeypatch.setattr(tools_mod, "core_stop_fleet", lambda **kw: called.append(kw))

        result = await _call("stop_fleet", {"cluster_name": "osiris"})
        assert result.is_error
        assert called == [], "the fleet must not be touched while another op holds the lock"


class TestReadOnlyToolsDoNotLock:
    """Nothing in the read-only tier mutates the live stack, so nothing
    there needs the lock -- which is also why
    MCPStateAccessReadOnly.json_src grants no `locks/*` access at all.
    Taking a lock here would serialize harmless polling against real
    operations, and polling is the majority of call volume under the
    async design."""

    @pytest.mark.asyncio
    async def test_list_clusters_takes_no_lock(self, spy, monkeypatch):
        monkeypatch.setattr(tools_mod, "_load_records", lambda: [])
        monkeypatch.setattr(tools_mod, "core_list_clusters", lambda **kw: [])
        await _call("list_clusters", {})
        assert spy.acquired == []

    @pytest.mark.asyncio
    async def test_preview_cluster_delete_takes_no_lock(self, spy):
        """Preview is read-only by contract; locking it would let a
        preview block a real operation."""
        await _call("preview_cluster_delete", {"cluster_name": "osiris"})
        assert spy.acquired == []

    def test_the_read_only_policy_grants_no_lock_access(self):
        import json

        doc = json.load(
            open(os.path.join(REPO_ROOT, "templates", "MCPStateAccessReadOnly.json_src"))
        )
        resources = []
        for stmt in doc["Statement"]:
            r = stmt["Resource"]
            resources += [r] if isinstance(r, str) else r
        assert not [r for r in resources if "/locks/" in r]


class TestTheLockIsNotTakenTwice:
    """core_create_cluster and core_delete_cluster acquire the lock
    themselves (round 27). Wrapping their tools in _cluster_lock as well
    would deadlock against their own acquisition -- the S3 lock is not
    reentrant, and the second attempt would see the first still held."""

    @pytest.mark.parametrize("tool", _LOCKS_INTERNALLY)
    def test_the_wrapper_does_not_lock_around_a_self_locking_core(self, tool):
        src = inspect.getsource(tools_mod.register_tools)
        tree = ast.parse(src.lstrip())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == tool:
                uses = [
                    n
                    for n in ast.walk(node)
                    if isinstance(n, ast.With)
                    for item in n.items
                    if isinstance(item.context_expr, ast.Call)
                    and getattr(item.context_expr.func, "id", "") == "_cluster_lock"
                ]
                assert not uses, (
                    f"{tool} wraps a core function that already locks internally -- "
                    f"this would deadlock"
                )
                return
        pytest.fail(f"{tool} not found in register_tools")

    def test_the_self_locking_cores_really_do_lock(self):
        """Vacuity guard: the test above is only meaningful if these
        actually acquire the lock themselves."""
        for name in ("core_create_cluster", "core_delete_cluster"):
            src = inspect.getsource(getattr(pcluster_core, name))
            assert "_acquire_distributed_cluster_lock" in src, name

    def test_the_wrapped_cores_really_do_not(self):
        """The other half: if _core_fleet_action ever gains its own lock,
        the wrapper must be removed or it will deadlock."""
        for name in ("_core_fleet_action", "core_apply_queue_config"):
            src = inspect.getsource(getattr(pcluster_core, name))
            assert_source_is_real(src, "test_the_wrapped_cores_really_do_not")
            assert "_acquire_distributed_cluster_lock" not in src, (
                f"{name} now locks internally -- remove the wrapper-layer lock "
                f"from mcp_server/tools.py or the two will deadlock"
            )


class TestTheHeldLockErrorSurvivesTheTransport:
    """The lock's own failure path had a real bug when this file was
    written: _acquire_distributed_cluster_lock sys.exit()s on a held lock
    (right for the CLI), and SystemExit is a BaseException, so the
    handler's deliberately-narrow `except Exception` did not catch it.
    pcluster_core documents that hazard twice already -- 'an uncaught
    SystemExit inside a long-lived FastMCP server process kills the whole
    server, not just one tool call' -- and the lock was a live instance of
    it. Translated to PClusterMakerError at the wrapper."""

    @pytest.mark.asyncio
    async def test_a_held_lock_is_a_tool_error_not_a_server_kill(self, monkeypatch):
        s = _LockSpy(held={"osiris"})
        monkeypatch.setattr(tools_mod, "_acquire_distributed_cluster_lock", s.acquire)
        monkeypatch.setattr(tools_mod, "s3_release_cluster_lock", s.release)
        monkeypatch.setattr(tools_mod, "_aws_account_id", lambda: "123456789012")
        monkeypatch.setattr(
            tools_mod, "_require_record", lambda name: type("R", (), {"region": "us-east-2"})()
        )
        result = await _call("stop_fleet", {"cluster_name": "osiris"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_the_message_says_what_is_wrong(self, monkeypatch):
        """A model told only 'error' will retry forever; told 'another
        operation is already running' it can wait and poll instead."""
        s = _LockSpy(held={"osiris"})
        monkeypatch.setattr(tools_mod, "_acquire_distributed_cluster_lock", s.acquire)
        monkeypatch.setattr(tools_mod, "s3_release_cluster_lock", s.release)
        monkeypatch.setattr(tools_mod, "_aws_account_id", lambda: "123456789012")
        monkeypatch.setattr(
            tools_mod, "_require_record", lambda name: type("R", (), {"region": "us-east-2"})()
        )
        result = await _call("stop_fleet", {"cluster_name": "osiris"})
        text = "".join(getattr(b, "text", "") for b in result.content)
        assert "already running" in text

    def test_the_wrapper_translates_systemexit(self):
        """Pinned on the source too: the behavioural test above passes if
        the acquire call is removed entirely, since then nothing raises."""
        src = inspect.getsource(tools_mod._cluster_lock)
        code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
        assert "except SystemExit" in code
        assert "PClusterMakerError" in code

    def test_a_held_lock_never_releases_someone_elses(self, monkeypatch):
        """Failing to acquire must not run the release path -- that would
        delete the lock object the *other* operation is holding, which is
        strictly worse than not locking at all."""
        s = _LockSpy(held={"osiris"})
        monkeypatch.setattr(tools_mod, "_acquire_distributed_cluster_lock", s.acquire)
        monkeypatch.setattr(tools_mod, "s3_release_cluster_lock", s.release)
        monkeypatch.setattr(tools_mod, "_aws_account_id", lambda: "123456789012")
        with pytest.raises(pcluster_core.PClusterMakerError):
            with tools_mod._cluster_lock("osiris", "us-east-2", "mcp test"):
                pass
        assert s.released == [], "a failed acquire must not release the holder's lock"
