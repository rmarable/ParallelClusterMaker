"""Tests for fleet stop/start helpers in pcluster_core."""

import json
import os
import subprocess
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcluster_core import (
    _validate_region,
    _run_pcluster_cmd,
    _get_fleet_status,
    _fleet_action_plan,
    _poll_fleet,
    _FLEET_POLL_INTERVAL,
    _FLEET_POLL_TIMEOUT,
)


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
# _run_pcluster_cmd
# ---------------------------------------------------------------------------

class TestRunPclusterCmd:
    def test_success_returns_parsed_json(self, monkeypatch):
        payload = json.dumps({"computeFleetStatus": "RUNNING"})
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **kw: _proc(stdout=payload))
        result = _run_pcluster_cmd(["describe-cluster", "--cluster-name", "x"], "/bin/pcluster")
        assert result["computeFleetStatus"] == "RUNNING"

    def test_binary_not_found_exits(self, monkeypatch):
        def _raise(*a, **kw):
            raise FileNotFoundError
        monkeypatch.setattr(subprocess, "run", _raise)
        with pytest.raises(SystemExit, match="not found"):
            _run_pcluster_cmd([], "/bin/pcluster")

    def test_timeout_exits(self, monkeypatch):
        def _raise(*a, **kw):
            raise subprocess.TimeoutExpired("pcluster", 120)
        monkeypatch.setattr(subprocess, "run", _raise)
        with pytest.raises(SystemExit, match="timed out"):
            _run_pcluster_cmd([], "/bin/pcluster")

    def test_nonzero_rc_exits(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **kw: _proc(rc=1, stderr="bad"))
        with pytest.raises(SystemExit, match="rc=1|exited 1"):
            _run_pcluster_cmd([], "/bin/pcluster")

    def test_invalid_json_exits(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **kw: _proc(stdout="not json"))
        with pytest.raises(SystemExit, match="unexpected"):
            _run_pcluster_cmd([], "/bin/pcluster")


# ---------------------------------------------------------------------------
# _get_fleet_status
# ---------------------------------------------------------------------------

class TestGetFleetStatus:
    def test_returns_status(self, monkeypatch):
        payload = json.dumps({"computeFleetStatus": "STOPPED"})
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **kw: _proc(stdout=payload))
        assert _get_fleet_status("mycluster", "us-east-1", "/bin/pcluster") == "STOPPED"

    def test_missing_key_returns_unknown(self, monkeypatch):
        payload = json.dumps({"clusterStatus": "CREATE_COMPLETE"})
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **kw: _proc(stdout=payload))
        assert _get_fleet_status("mycluster", "us-east-1", "/bin/pcluster") == "UNKNOWN"


# ---------------------------------------------------------------------------
# _poll_fleet
# ---------------------------------------------------------------------------

class TestPollFleet:
    def test_returns_when_target_reached(self, monkeypatch):
        payload = json.dumps({"computeFleetStatus": "STOPPED"})
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **kw: _proc(stdout=payload))
        monkeypatch.setattr("pcluster_core.time.sleep", lambda _: None)
        _poll_fleet("mycluster", "us-east-1", "STOPPED", "fleet stop", "/bin/pcluster")

    def test_protected_state_exits(self, monkeypatch):
        payload = json.dumps({"computeFleetStatus": "PROTECTED"})
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **kw: _proc(stdout=payload))
        monkeypatch.setattr("pcluster_core.time.sleep", lambda _: None)
        with pytest.raises(SystemExit, match="PROTECTED"):
            _poll_fleet("mycluster", "us-east-1", "STOPPED", "fleet stop", "/bin/pcluster")

    def test_timeout_exits(self, monkeypatch):
        payload = json.dumps({"computeFleetStatus": "STOP_REQUESTED"})
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **kw: _proc(stdout=payload))
        monkeypatch.setattr("pcluster_core.time.sleep", lambda _: None)
        with pytest.raises(SystemExit, match="timed out"):
            _poll_fleet("mycluster", "us-east-1", "STOPPED", "fleet stop", "/bin/pcluster")

    def test_keyboard_interrupt_exits(self, monkeypatch, capsys):
        call_count = [0]

        def _run(*a, **kw):
            call_count[0] += 1
            return _proc(stdout=json.dumps({"computeFleetStatus": "STOP_REQUESTED"}))

        def _sleep(_):
            raise KeyboardInterrupt

        monkeypatch.setattr(subprocess, "run", _run)
        monkeypatch.setattr("pcluster_core.time.sleep", _sleep)
        with pytest.raises(SystemExit):
            _poll_fleet("mycluster", "us-east-1", "STOPPED", "fleet stop", "/bin/pcluster")
        out = capsys.readouterr().out
        assert "Interrupted" in out

    def test_keyboard_interrupt_during_describe_call_exits_cleanly(self, monkeypatch, capsys):
        """Ctrl-C most often lands in the describe-cluster subprocess, not in
        time.sleep() — that call plus its JSON parse is where the wall clock
        actually goes. When only sleep() was guarded, an interrupt there escaped
        as a raw traceback and never told the user the fleet operation was still
        running in AWS."""
        def _run(*a, **kw):
            raise KeyboardInterrupt

        monkeypatch.setattr(subprocess, "run", _run)
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
