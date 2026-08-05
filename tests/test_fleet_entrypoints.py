"""
Direct tests for stop_pcluster.py and start_pcluster.py main().

`_fleet_action_plan` and `_poll_fleet` were already tested in isolation
(tests/test_fleet.py); what had no coverage was the wiring in the CLI scripts —
whether the plan is actually obeyed, whether STOP_REQUESTED/START_REQUESTED is
the status sent, and whether stop offers the abort window that start does not
need. The two scripts are near-identical, which is exactly the shape where a
copy-paste error survives review.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from entrypoint_harness import load_entrypoint

CLUSTER = "fleetcluster"
REGION = "us-east-1"


def _stage(mod, monkeypatch, status, argv_extra=(), record=None):
    record = record if record is not None else {}
    monkeypatch.setattr(
        mod, "_read_cluster_record", lambda n, r: {"region": REGION}
    )
    monkeypatch.setattr(mod, "_get_fleet_status", lambda *a, **k: status)

    def _run(cmd, binary):
        record.setdefault("run_calls", []).append(list(cmd))

    def _poll(cluster, region, target, label, binary):
        record.setdefault("polls", []).append(target)

    monkeypatch.setattr(mod, "_run_pcluster_cmd", _run)
    monkeypatch.setattr(mod, "_poll_fleet", _poll)
    if hasattr(mod, "ctrlC_Abort"):
        monkeypatch.setattr(
            mod, "ctrlC_Abort",
            lambda *a, **k: record.setdefault("abort_before_run",
                                             "run_calls" not in record),
        )
    monkeypatch.setattr(
        sys, "argv",
        [mod.__name__, "-N", CLUSTER] + list(argv_extra),
    )
    return record


@pytest.fixture
def stop_mod():
    return load_entrypoint("stop_pcluster.py")


@pytest.fixture
def start_mod():
    return load_entrypoint("start_pcluster.py")


class TestStopFleet:
    def test_running_fleet_gets_stop_requested(self, stop_mod, monkeypatch):
        rec = _stage(stop_mod, monkeypatch, "RUNNING")
        stop_mod.main()
        cmd = rec["run_calls"][0]
        assert "update-compute-fleet" in cmd
        assert "STOP_REQUESTED" in cmd
        assert "START_REQUESTED" not in cmd

    def test_operator_gets_an_abort_window_before_the_api_call(self, stop_mod, monkeypatch):
        """Stopping the fleet kills in-flight Slurm jobs, so the window has to
        open before update-compute-fleet is sent, not after."""
        rec = _stage(stop_mod, monkeypatch, "RUNNING")
        stop_mod.main()
        assert rec["abort_before_run"] is True

    def test_already_stopped_fleet_makes_no_api_call(self, stop_mod, monkeypatch):
        rec = _stage(stop_mod, monkeypatch, "STOPPED")
        with pytest.raises(SystemExit) as exc:
            stop_mod.main()
        assert exc.value.code == 0
        assert "run_calls" not in rec

    def test_protected_fleet_aborts_without_an_api_call(self, stop_mod, monkeypatch):
        rec = _stage(stop_mod, monkeypatch, "PROTECTED")
        with pytest.raises(SystemExit) as exc:
            stop_mod.main()
        assert "PROTECTED" in str(exc.value.code)
        assert "run_calls" not in rec

    def test_stop_in_progress_makes_no_duplicate_api_call(self, stop_mod, monkeypatch):
        rec = _stage(stop_mod, monkeypatch, "STOP_REQUESTED")
        with pytest.raises(SystemExit) as exc:
            stop_mod.main()
        assert exc.value.code == 0
        assert "run_calls" not in rec

    def test_wait_polls_for_stopped_not_running(self, stop_mod, monkeypatch):
        rec = _stage(stop_mod, monkeypatch, "RUNNING", argv_extra=["-W"])
        stop_mod.main()
        assert rec["polls"] == ["STOPPED"]

    def test_no_wait_does_not_poll(self, stop_mod, monkeypatch):
        rec = _stage(stop_mod, monkeypatch, "RUNNING")
        stop_mod.main()
        assert "polls" not in rec

    def test_wait_still_polls_when_a_stop_is_already_in_progress(self, stop_mod, monkeypatch):
        rec = _stage(stop_mod, monkeypatch, "STOPPING", argv_extra=["-W"])
        with pytest.raises(SystemExit) as exc:
            stop_mod.main()
        assert exc.value.code == 0
        assert rec["polls"] == ["STOPPED"]
        assert "run_calls" not in rec

    def test_missing_cluster_record_aborts(self, stop_mod, monkeypatch):
        rec = _stage(stop_mod, monkeypatch, "RUNNING")
        monkeypatch.setattr(stop_mod, "_read_cluster_record", lambda n, r: None)
        with pytest.raises(SystemExit) as exc:
            stop_mod.main()
        assert "no cluster record found" in str(exc.value.code)
        assert "run_calls" not in rec

    def test_region_flag_overrides_the_cluster_record(self, stop_mod, monkeypatch):
        rec = _stage(stop_mod, monkeypatch, "RUNNING", argv_extra=["-R", "eu-west-1"])
        stop_mod.main()
        cmd = rec["run_calls"][0]
        assert cmd[cmd.index("--region") + 1] == "eu-west-1"

    def test_blank_region_in_record_aborts(self, stop_mod, monkeypatch):
        rec = _stage(stop_mod, monkeypatch, "RUNNING")
        monkeypatch.setattr(stop_mod, "_read_cluster_record", lambda n, r: {"region": ""})
        with pytest.raises(SystemExit) as exc:
            stop_mod.main()
        assert "-R/--region" in str(exc.value.code)
        assert "run_calls" not in rec


class TestStartFleet:
    def test_stopped_fleet_gets_start_requested(self, start_mod, monkeypatch):
        rec = _stage(start_mod, monkeypatch, "STOPPED")
        start_mod.main()
        cmd = rec["run_calls"][0]
        assert "update-compute-fleet" in cmd
        assert "START_REQUESTED" in cmd
        assert "STOP_REQUESTED" not in cmd

    def test_already_running_fleet_makes_no_api_call(self, start_mod, monkeypatch):
        rec = _stage(start_mod, monkeypatch, "RUNNING")
        with pytest.raises(SystemExit) as exc:
            start_mod.main()
        assert exc.value.code == 0
        assert "run_calls" not in rec

    def test_protected_fleet_aborts_without_an_api_call(self, start_mod, monkeypatch):
        rec = _stage(start_mod, monkeypatch, "PROTECTED")
        with pytest.raises(SystemExit) as exc:
            start_mod.main()
        assert "PROTECTED" in str(exc.value.code)
        assert "run_calls" not in rec

    def test_wait_polls_for_running_not_stopped(self, start_mod, monkeypatch):
        rec = _stage(start_mod, monkeypatch, "STOPPED", argv_extra=["-W"])
        start_mod.main()
        assert rec["polls"] == ["RUNNING"]

    def test_start_has_no_abort_window(self, start_mod):
        """Starting a fleet destroys nothing, so it deliberately does not prompt.
        Pinning this keeps a copy-paste from stop_pcluster.py from adding a
        pointless 5-second delay to every start."""
        assert not hasattr(start_mod, "ctrlC_Abort")
