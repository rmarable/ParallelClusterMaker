"""Tests for fleet stop/start helpers in pcluster_core."""

import json
import os
import subprocess
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import pcluster_core
from pcluster_core import (
    ClusterRecord,
    PClusterMakerError,
    _validate_region,
    _get_fleet_status,
    _fleet_action_plan,
    _poll_fleet,
    _FLEET_POLL_INTERVAL,
    _FLEET_POLL_TIMEOUT,
    core_stop_fleet,
    core_start_fleet,
    core_apply_cluster_update,
    core_apply_queue_config,
)

_RECORD_KWARGS = {
    "cluster_name": "fleetcluster",
    "cluster_owner": "rmarable",
    "serial": "202608200001",
    "region": "us-east-1",
    "headnode_instance_type": "c5.xlarge",
    "enable_loginnode": "false",
    "loginnode_instance_type": "",
    "loginnode_count": 0,
    "cpu_instance_types": ["c5.xlarge"],
    "gpu_instance_types": [],
    "enable_cpu_queue": "true",
    "enable_gpu_queue": "false",
    "initial_cpu_queue_size": 2,
    "max_cpu_queue_size": 8,
    "initial_gpu_queue_size": 0,
    "max_gpu_queue_size": 0,
    "cluster_type": "ondemand",
    "deployment_date": "2026-08-20",
    "ssh_keypair": "/tmp/fleetcluster.pem",
    "ec2_keypair": "fleetcluster-keypair",
    "ec2_user": "ubuntu",
    "s3_bucketname": "my-bucket",
    "enable_monitoring": "false",
}


def _record(**overrides):
    return ClusterRecord(**{**_RECORD_KWARGS, **overrides})


def _proc(rc=0, stdout="", stderr=""):
    return types.SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# _validate_region
# ---------------------------------------------------------------------------

class TestValidateRegion:
    def test_valid_regions(self):
        for r in ("us-east-1", "eu-west-2", "ap-southeast-1", "us-gov-east-1", "ap-southeast-2"):
            _validate_region(r)  # must not raise

    def test_invalid_region_exits(self):
        with pytest.raises(SystemExit):
            _validate_region("not-a-region")

    def test_empty_string_exits(self):
        with pytest.raises(SystemExit):
            _validate_region("")


# ---------------------------------------------------------------------------
# _describe_cluster_json / fleet helpers
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# _get_fleet_status
# ---------------------------------------------------------------------------

class TestGetFleetStatus:
    def test_returns_status(self, monkeypatch):
        monkeypatch.setattr(pcluster_core, "_describe_cluster_json",
                            lambda c, r: {"computeFleetStatus": "STOPPED"})
        assert _get_fleet_status("mycluster", "us-east-1", "/bin/pcluster") == "STOPPED"

    def test_missing_key_returns_unknown(self, monkeypatch):
        monkeypatch.setattr(pcluster_core, "_describe_cluster_json",
                            lambda c, r: {"clusterStatus": "CREATE_COMPLETE"})
        assert _get_fleet_status("mycluster", "us-east-1", "/bin/pcluster") == "UNKNOWN"


# ---------------------------------------------------------------------------
# _poll_fleet
# ---------------------------------------------------------------------------

class TestPollFleet:
    def test_returns_when_target_reached(self, monkeypatch):
        monkeypatch.setattr(pcluster_core, "_describe_cluster_json",
                            lambda c, r: {"computeFleetStatus": "STOPPED"})
        monkeypatch.setattr("pcluster_core.time.sleep", lambda _: None)
        _poll_fleet("mycluster", "us-east-1", "STOPPED", "fleet stop", "/bin/pcluster")

    def test_protected_state_exits(self, monkeypatch):
        monkeypatch.setattr(pcluster_core, "_describe_cluster_json",
                            lambda c, r: {"computeFleetStatus": "PROTECTED"})
        monkeypatch.setattr("pcluster_core.time.sleep", lambda _: None)
        with pytest.raises(SystemExit, match="PROTECTED"):
            _poll_fleet("mycluster", "us-east-1", "STOPPED", "fleet stop", "/bin/pcluster")

    def test_timeout_exits(self, monkeypatch):
        monkeypatch.setattr(pcluster_core, "_describe_cluster_json",
                            lambda c, r: {"computeFleetStatus": "STOP_REQUESTED"})
        monkeypatch.setattr("pcluster_core.time.sleep", lambda _: None)
        with pytest.raises(SystemExit, match="timed out"):
            _poll_fleet("mycluster", "us-east-1", "STOPPED", "fleet stop", "/bin/pcluster")

    def test_keyboard_interrupt_exits(self, monkeypatch, capsys):
        call_count = [0]

        def _describe(c, r):
            call_count[0] += 1
            return {"computeFleetStatus": "STOP_REQUESTED"}

        def _sleep(_):
            raise KeyboardInterrupt

        monkeypatch.setattr(pcluster_core, "_describe_cluster_json", _describe)
        monkeypatch.setattr("pcluster_core.time.sleep", _sleep)
        with pytest.raises(SystemExit):
            _poll_fleet("mycluster", "us-east-1", "STOPPED", "fleet stop", "/bin/pcluster")
        out = capsys.readouterr().out
        assert "Interrupted" in out

    def test_keyboard_interrupt_during_describe_call_exits_cleanly(self, monkeypatch, capsys):
        """Ctrl-C most often lands in the describe-cluster call, not in
        time.sleep() — that call is where the wall clock actually goes. When
        only sleep() was guarded, an interrupt there escaped as a raw
        traceback and never told the user the fleet operation was still
        running in AWS. (The call is pcluster.lib rather than a subprocess
        since round 48; the interrupt window is the same.)"""
        def _describe(c, r):
            raise KeyboardInterrupt

        monkeypatch.setattr(pcluster_core, "_describe_cluster_json", _describe)
        monkeypatch.setattr("pcluster_core.time.sleep", lambda _: None)
        with pytest.raises(SystemExit):
            _poll_fleet("mycluster", "us-east-1", "STOPPED", "fleet stop", "/bin/pcluster")
        out = capsys.readouterr().out
        assert "Interrupted" in out
        assert "still running in AWS" in out


# ---------------------------------------------------------------------------
# _fleet_action_plan
# ---------------------------------------------------------------------------

class TestFleetActionPlan:
    @pytest.mark.parametrize("status,action,expected", [
        ("PROTECTED", "stop", "abort"),
        ("PROTECTED", "start", "abort"),
        ("STOPPED", "stop", "done"),
        ("DISABLED", "stop", "done"),
        ("RUNNING", "start", "done"),
        ("ENABLED", "start", "done"),
        ("STOP_REQUESTED", "stop", "wait"),
        ("START_REQUESTED", "start", "wait"),
        ("RUNNING", "stop", "request"),
        ("STOPPED", "start", "request"),
        ("UNKNOWN", "stop", "request"),
        ("UNKNOWN", "start", "request"),
    ])
    def test_plan(self, status, action, expected):
        assert _fleet_action_plan(status, action) == expected

    @pytest.mark.parametrize("status,action", [
        ("STOPPING", "stop"),
        ("STARTING", "start"),
    ])
    def test_transitional_states_are_not_reissued(self, status, action):
        """PCluster's ComputeFleetStatus enum includes STOPPING and STARTING.
        Both scripts previously matched only the *_REQUESTED variants, so a
        fleet caught mid-transition fell through to a second
        update-compute-fleet call against an already-transitioning fleet."""
        assert _fleet_action_plan(status, action) == "wait"

    @pytest.mark.parametrize("status,action,expected", [
        ("STOPPING", "start", "request"),
        ("STARTING", "stop", "request"),
    ])
    def test_opposite_transition_still_requests(self, status, action, expected):
        assert _fleet_action_plan(status, action) == expected

    def test_unknown_action_raises(self):
        with pytest.raises(ValueError):
            _fleet_action_plan("RUNNING", "restart")


# ---------------------------------------------------------------------------
# core_stop_fleet / core_start_fleet -- the orchestration layer added in
# Workstream 1's migration (docs/parallelclustermaker-mcp-plan.md), replacing
# what used to be duplicated inline across stop_pcluster.py/start_pcluster.py.
# Both entry points now call the same _core_fleet_action, so one shared test
# suite exercised through both public names is what actually guards against
# the copy-paste risk tests/test_fleet_entrypoints.py was written for --
# there is only one implementation left to get wrong.
# ---------------------------------------------------------------------------

def _stage_core(monkeypatch, status, run_calls=None, poll_calls=None):
    run_calls = run_calls if run_calls is not None else []
    poll_calls = poll_calls if poll_calls is not None else []
    monkeypatch.setattr(pcluster_core, "_get_fleet_status", lambda *a, **k: status)
    # The fleet path calls pcluster.lib now, not the pcluster binary. Recorded
    # in the same CLI-ish shape the assertions already expect, so they keep
    # reading as "what was requested" rather than "which kwargs were passed".
    monkeypatch.setattr(
        pcluster_core, "_update_compute_fleet_lib",
        lambda cluster, region, status: run_calls.append(
            ["update-compute-fleet", "--cluster-name", cluster,
             "--region", region, "--status", status]
        ),
    )
    monkeypatch.setattr(
        pcluster_core, "_poll_fleet",
        lambda cluster, region, target, label, binary: poll_calls.append(target),
    )
    return run_calls, poll_calls


class TestCoreStopFleet:
    def test_running_fleet_gets_stop_requested(self, monkeypatch):
        run_calls, _ = _stage_core(monkeypatch, "RUNNING")
        result = core_stop_fleet(cluster_record=_record(), region="us-east-1", pcluster_bin="pcluster")
        assert "update-compute-fleet" in run_calls[0]
        assert "STOP_REQUESTED" in run_calls[0]
        assert "START_REQUESTED" not in run_calls[0]
        assert result.plan == "request"
        assert result.status_before == "RUNNING"
        assert result.status_after is None

    def test_already_stopped_fleet_makes_no_api_call(self, monkeypatch):
        run_calls, _ = _stage_core(monkeypatch, "STOPPED")
        result = core_stop_fleet(cluster_record=_record(), region="us-east-1", pcluster_bin="pcluster")
        assert run_calls == []
        assert result.plan == "done"
        assert result.status_after == "STOPPED"

    def test_protected_fleet_raises_without_an_api_call(self, monkeypatch):
        run_calls, _ = _stage_core(monkeypatch, "PROTECTED")
        with pytest.raises(PClusterMakerError, match="PROTECTED"):
            core_stop_fleet(cluster_record=_record(), region="us-east-1", pcluster_bin="pcluster")
        assert run_calls == []

    def test_stop_in_progress_makes_no_duplicate_api_call(self, monkeypatch):
        run_calls, _ = _stage_core(monkeypatch, "STOP_REQUESTED")
        result = core_stop_fleet(cluster_record=_record(), region="us-east-1", pcluster_bin="pcluster")
        assert run_calls == []
        assert result.plan == "wait"

    def test_wait_polls_for_stopped_not_running(self, monkeypatch):
        _, poll_calls = _stage_core(monkeypatch, "RUNNING")
        result = core_stop_fleet(
            cluster_record=_record(), region="us-east-1", pcluster_bin="pcluster", wait=True,
        )
        assert poll_calls == ["STOPPED"]
        assert result.status_after == "STOPPED"

    def test_no_wait_does_not_poll(self, monkeypatch):
        _, poll_calls = _stage_core(monkeypatch, "RUNNING")
        core_stop_fleet(cluster_record=_record(), region="us-east-1", pcluster_bin="pcluster")
        assert poll_calls == []

    def test_wait_still_polls_when_a_stop_is_already_in_progress(self, monkeypatch):
        run_calls, poll_calls = _stage_core(monkeypatch, "STOPPING")
        result = core_stop_fleet(
            cluster_record=_record(), region="us-east-1", pcluster_bin="pcluster", wait=True,
        )
        assert poll_calls == ["STOPPED"]
        assert run_calls == []
        assert result.plan == "wait"

    def test_region_argument_is_what_gets_used_not_the_record(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(pcluster_core, "_get_fleet_status", lambda n, r, b: seen.setdefault("region", r) or "RUNNING")
        monkeypatch.setattr(
            pcluster_core, "_update_compute_fleet_lib", lambda c, r, status: None
        )
        core_stop_fleet(
            cluster_record=_record(region="us-east-1"), region="eu-west-1", pcluster_bin="pcluster",
        )
        assert seen["region"] == "eu-west-1"


class TestCoreStartFleet:
    def test_stopped_fleet_gets_start_requested(self, monkeypatch):
        run_calls, _ = _stage_core(monkeypatch, "STOPPED")
        result = core_start_fleet(cluster_record=_record(), region="us-east-1", pcluster_bin="pcluster")
        assert "update-compute-fleet" in run_calls[0]
        assert "START_REQUESTED" in run_calls[0]
        assert "STOP_REQUESTED" not in run_calls[0]
        assert result.plan == "request"

    def test_already_running_fleet_makes_no_api_call(self, monkeypatch):
        run_calls, _ = _stage_core(monkeypatch, "RUNNING")
        result = core_start_fleet(cluster_record=_record(), region="us-east-1", pcluster_bin="pcluster")
        assert run_calls == []
        assert result.plan == "done"

    def test_protected_fleet_raises_without_an_api_call(self, monkeypatch):
        run_calls, _ = _stage_core(monkeypatch, "PROTECTED")
        with pytest.raises(PClusterMakerError, match="PROTECTED"):
            core_start_fleet(cluster_record=_record(), region="us-east-1", pcluster_bin="pcluster")
        assert run_calls == []

    def test_wait_polls_for_running_not_stopped(self, monkeypatch):
        _, poll_calls = _stage_core(monkeypatch, "STOPPED")
        core_start_fleet(
            cluster_record=_record(), region="us-east-1", pcluster_bin="pcluster", wait=True,
        )
        assert poll_calls == ["RUNNING"]


class TestCoreApplyClusterUpdate:
    """Workstream 4 extracted phase 2 of the three-phase queue-config
    update (stop fleet -> apply config -> start fleet) into its own
    callable core function. Phases 1 and 3 already had one
    (core_stop_fleet/core_start_fleet); this is the one that did not, and
    without it the MCP tool surface could only expose the whole sequence
    as a single opaque blocking tool.

    Note this whole area had ZERO test coverage before this round --
    core_apply_queue_config and _poll_cluster_update were both entirely
    untested, despite core_apply_queue_config being what
    manage_pcluster_queue.py's -W flag runs."""

    def _stage(self, monkeypatch, run_calls=None, poll_calls=None):
        run_calls = run_calls if run_calls is not None else []
        poll_calls = poll_calls if poll_calls is not None else []
        monkeypatch.setattr(
            pcluster_core, "_update_cluster_lib",
            lambda cluster, region, config_path: (
                run_calls.append(
                    ["update-cluster", "--cluster-name", cluster,
                     "--cluster-configuration", config_path, "--region", region]
                ),
                {"ok": True},
            )[1],
        )
        monkeypatch.setattr(
            pcluster_core, "_poll_cluster_update",
            lambda cluster, region, binary: poll_calls.append(cluster),
        )
        return run_calls, poll_calls

    def test_issues_update_cluster_with_the_config_path(self, monkeypatch):
        run_calls, _ = self._stage(monkeypatch)
        core_apply_cluster_update(
            cluster_name="foo", config_path="/tmp/cfg.yaml",
            region="us-east-1", pcluster_bin="pcluster",
        )
        assert run_calls[0][0] == "update-cluster"
        assert "/tmp/cfg.yaml" in run_calls[0]
        assert "foo" in run_calls[0]

    def test_wait_true_polls_to_a_terminal_update_state(self, monkeypatch):
        _, poll_calls = self._stage(monkeypatch)
        core_apply_cluster_update(
            cluster_name="foo", config_path="/tmp/cfg.yaml",
            region="us-east-1", pcluster_bin="pcluster", wait=True,
        )
        assert poll_calls == ["foo"]

    def test_wait_false_returns_without_polling(self, monkeypatch):
        run_calls, poll_calls = self._stage(monkeypatch)
        core_apply_cluster_update(
            cluster_name="foo", config_path="/tmp/cfg.yaml",
            region="us-east-1", pcluster_bin="pcluster", wait=False,
        )
        assert run_calls, "the update must still be requested"
        assert poll_calls == [], "wait=False must not poll"

    def test_wait_defaults_to_true(self, monkeypatch):
        """The composite path depends on this: core_apply_queue_config
        cannot restart the fleet until the update has actually finished."""
        _, poll_calls = self._stage(monkeypatch)
        core_apply_cluster_update(
            cluster_name="foo", config_path="/tmp/cfg.yaml",
            region="us-east-1", pcluster_bin="pcluster",
        )
        assert poll_calls == ["foo"]


class TestApplyQueueConfigStaysBlocking:
    """The composite deliberately takes NO wait parameter, unlike every
    other core function Workstream 4 touched -- and that is a correctness
    property, not a style choice. Its three phases are causally
    dependent: update-cluster requires an already-stopped fleet, and the
    restart requires a finished update. A wait=False that fired all three
    in sequence would apply the config to a still-running fleet and fail.
    An async caller uses the three phases separately instead, polling
    between them."""

    def test_it_accepts_no_wait_parameter(self):
        import inspect

        params = inspect.signature(core_apply_queue_config).parameters
        assert "wait" not in params, (
            "core_apply_queue_config must not take a wait parameter -- its "
            "phases are causally dependent and cannot be fired without "
            "waiting between them"
        )

    def test_every_phase_is_awaited(self, monkeypatch):
        """All three phases must block. Pins the actual call kwargs, since
        a wait=False leaking into any one of them is silent at runtime and
        only shows up as a failed update against a live fleet."""
        seen = []
        monkeypatch.setattr(
            pcluster_core, "core_stop_fleet",
            lambda **kw: seen.append(("stop", kw.get("wait"))),
        )
        monkeypatch.setattr(
            pcluster_core, "core_apply_cluster_update",
            lambda **kw: seen.append(("update", kw.get("wait"))),
        )
        monkeypatch.setattr(
            pcluster_core, "core_start_fleet",
            lambda **kw: seen.append(("start", kw.get("wait"))),
        )
        core_apply_queue_config(
            cluster_record=_record(), config_path="/tmp/cfg.yaml",
            region="us-east-1", pcluster_bin="pcluster",
        )
        assert seen == [("stop", True), ("update", True), ("start", True)]


class TestTheDescribeHelperFailsCatchably:
    """The property that motivated moving off the pcluster binary at all,
    and the one a mutation showed nothing was pinning.

    `_describe_cluster_json` must raise something an `except Exception`
    can catch. The subprocess form it replaced raised SystemExit on a
    missing binary or a non-zero rc -- and SystemExit is a BaseException,
    which the MCP handler's deliberately-narrow `except Exception` does
    not catch (narrow on purpose, so a Lambda timeout is not reported as a
    tool failure). A Lambda package has no `.venv/bin/pcluster`, so every
    fleet/health/diagnose tool on the remote transport would have killed
    its own container instead of returning an error.

    Reverting the helper to `raise SystemExit` passed the entire suite
    before this class existed.
    """

    def _boom_lib(self, monkeypatch, exc):
        import types as _types

        fake = _types.ModuleType("pcluster.lib")

        def _describe(cluster_name, region):
            raise exc

        fake.describe_cluster = _describe
        monkeypatch.setitem(sys.modules, "pcluster.lib", fake)

    def test_a_library_failure_raises_a_catchable_exception(self, monkeypatch):
        self._boom_lib(monkeypatch, RuntimeError("boom"))
        with pytest.raises(Exception) as exc:
            pcluster_core._describe_cluster_json("mycluster", "us-east-1")
        assert isinstance(exc.value, Exception), "must not be a bare BaseException"

    def test_it_is_not_a_systemexit(self, monkeypatch):
        """The specific regression: SystemExit escapes `except Exception`
        and takes the whole server process with it."""
        self._boom_lib(monkeypatch, RuntimeError("boom"))
        try:
            pcluster_core._describe_cluster_json("mycluster", "us-east-1")
        except BaseException as e:
            assert not isinstance(e, SystemExit), (
                "SystemExit here kills a long-lived MCP server rather than "
                "failing one tool call"
            )
        else:
            pytest.fail("expected the failure to propagate")

    def test_it_is_a_pcluster_maker_error(self, monkeypatch):
        """Specifically PClusterMakerError, so CLI shims keep converting it
        to sys.exit and their behavior is unchanged."""
        self._boom_lib(monkeypatch, RuntimeError("boom"))
        with pytest.raises(pcluster_core.PClusterMakerError):
            pcluster_core._describe_cluster_json("mycluster", "us-east-1")

    def test_the_message_names_the_cluster_and_the_cause(self, monkeypatch):
        self._boom_lib(monkeypatch, RuntimeError("AccessDenied"))
        with pytest.raises(pcluster_core.PClusterMakerError) as exc:
            pcluster_core._describe_cluster_json("mycluster", "eu-west-1")
        text = str(exc.value)
        assert "mycluster" in text and "eu-west-1" in text
        assert "AccessDenied" in text

    def test_no_pcluster_binary_is_ever_invoked(self, monkeypatch):
        """The transport itself: a subprocess here means the `.venv` path
        dependency is back."""
        called = []
        monkeypatch.setattr(
            pcluster_core.subprocess, "run",
            lambda *a, **kw: called.append(a) or _proc(),
        )
        import types as _types

        fake = _types.ModuleType("pcluster.lib")
        fake.describe_cluster = lambda cluster_name, region: {"clusterStatus": "X"}
        monkeypatch.setitem(sys.modules, "pcluster.lib", fake)
        assert pcluster_core._describe_cluster_json("c", "r") == {"clusterStatus": "X"}
        assert called == [], "describe-cluster must not shell out"


class TestALibraryFailureNamesItsCause:
    """The three pcluster.lib wrappers formatted their exception with a bare
    `{e}`, which is empty for every ParallelClusterApiException.

    This is not a cosmetic defect. R4 drove stop_fleet ->
    apply_cluster_update -> start_fleet against a live cluster and all three
    failed with `BadRequestException: ` -- nothing after the colon. The
    actual cause was a PCluster version skew between the Lambda artifact and
    the cluster, and the blank message is what hid it: the run was recorded
    as a pass on the strength of HTTP 200 and a CloudWatch REPORT line,
    neither of which distinguishes a returned error from a completed
    operation. Same shape as the event-loop failure `pcluster_exception_detail`
    was written for, on the tiers where the operator has no other surface.

    The exceptions here are the real ones from the installed package, not
    fakes -- a fake would encode what the caller happens to need.
    """

    def _raising_lib(self, monkeypatch, name, exc):
        import types as _types

        fake = _types.ModuleType("pcluster.lib")

        def _boom(*a, **k):
            raise exc

        setattr(fake, name, _boom)
        monkeypatch.setitem(sys.modules, "pcluster.lib", fake)

    def _bad_request(self):
        from pcluster.api.errors import BadRequestException

        return BadRequestException(
            "the update can be performed only with the same ParallelCluster "
            "version (3.15.1) used to create the cluster."
        )

    def test_the_premise_holds_str_is_empty(self, monkeypatch):
        """Vacuity guard. If str() ever stops being empty the whole class is
        testing nothing, and it must fail loudly rather than pass."""
        monkeypatch.setenv("AWS_REGION", "us-east-1")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        exc = self._bad_request()
        assert str(exc) == "", (
            "ParallelClusterApiException.__str__ is no longer empty; this "
            "class's premise needs rechecking"
        )
        assert "version" in exc.content.message

    @pytest.mark.parametrize(
        "wrapper,libname,kwargs",
        [
            ("_update_compute_fleet_lib", "update_compute_fleet",
             {"cluster_name": "c", "region": "us-east-1", "status": "STOP_REQUESTED"}),
            ("_update_cluster_lib", "update_cluster",
             {"cluster_name": "c", "region": "us-east-1", "config_path": "/tmp/x.yaml"}),
        ],
    )
    def test_the_reason_reaches_the_operator(self, monkeypatch, wrapper,
                                             libname, kwargs):
        monkeypatch.setenv("AWS_REGION", "us-east-1")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        self._raising_lib(monkeypatch, libname, self._bad_request())
        with pytest.raises(pcluster_core.PClusterMakerError) as ei:
            getattr(pcluster_core, wrapper)(**kwargs)
        text = str(ei.value)
        assert "the update can be performed only" in text, (
            f"{wrapper} reported {text!r}, losing the reason -- this is the "
            f"blank message that hid the R4 version skew"
        )
        assert not text.rstrip().endswith(":"), (
            f"{wrapper} produced a message ending in a bare colon: {text!r}"
        )

    def test_no_wrapper_formats_a_library_exception_with_a_bare_e(self):
        """The three sites are one edit apart from regressing, and a
        behavioral test only covers the wrappers it drives."""
        import io as _io

        src = _io.open(pcluster_core.__file__, encoding="utf-8").read()
        assert "{type(e).__name__}: {e}" not in src, (
            "a pcluster.lib wrapper formats its exception with a bare {e}, "
            "which is empty for every ParallelClusterApiException; use "
            "pcluster_exception_detail(e)"
        )
