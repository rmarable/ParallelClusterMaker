"""
Direct tests for stop_pcluster.py and start_pcluster.py main() -- CLI glue
only: arg parsing, region resolution/override, the vars-file precondition,
the preflight status/plan print sequence, and (stop only) the destructive-
action confirmation gate firing at the right point. The actual fleet-action
orchestration (STOP_REQUESTED/START_REQUESTED, done/wait/request/abort,
polling) now lives in pcluster_core.core_stop_fleet/core_start_fleet and is
tested directly in tests/test_fleet.py -- mocking it here as one unit is
required, not a style choice: core_stop_fleet resolves _get_fleet_status/
_describe_cluster_json/_poll_fleet in pcluster_core's own module globals, so
patching stop_mod._get_fleet_status etc. (as this file used to do) would
silently stop affecting anything core_stop_fleet actually calls -- the same
class of trap already caught in check_pcluster.py's migration.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from entrypoint_harness import load_entrypoint
import pcluster_core

CLUSTER = "fleetcluster"
REGION = "us-east-1"

_FULL_REC_DICT = {
    "cluster_name": CLUSTER,
    "cluster_owner": "rmarable",
    "serial": "202608200001",
    "region": REGION,
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


def _stage(mod, monkeypatch, status, argv_extra=(), core_result=None, core_raises=None, rec=None):
    """Patch the shim's own direct calls (_get_fleet_status for the
    preflight check, ctrlC_Abort) and mock core_stop_fleet/core_start_fleet
    as a single unit -- these are the only two boundaries stop_pcluster.py/
    start_pcluster.py's main() actually crosses now."""
    full_rec = dict(_FULL_REC_DICT)
    full_rec.update(rec or {})
    monkeypatch.setattr(mod, "_read_cluster_record", lambda n, r: full_rec)
    monkeypatch.setattr(mod, "_get_fleet_status", lambda *a, **k: status)

    calls = []
    core_name = "core_stop_fleet" if hasattr(mod, "core_stop_fleet") else "core_start_fleet"

    def _core(**kwargs):
        calls.append(kwargs)
        if core_raises is not None:
            raise core_raises
        return core_result or pcluster_core.FleetActionResult(
            CLUSTER, "stop" if core_name == "core_stop_fleet" else "start", status, None, "request",
        )

    monkeypatch.setattr(mod, core_name, _core)

    if hasattr(mod, "ctrlC_Abort"):
        monkeypatch.setattr(
            mod, "ctrlC_Abort",
            lambda *a, **k: calls.append("abort_before_core"),
        )
    monkeypatch.setattr(sys, "argv", [mod.__name__, "-N", CLUSTER] + list(argv_extra))
    return calls


@pytest.fixture
def stop_mod():
    return load_entrypoint("stop_pcluster.py")


@pytest.fixture
def start_mod():
    return load_entrypoint("start_pcluster.py")


class TestStopFleetCliShim:
    def test_running_fleet_calls_core_stop_fleet_with_wait_false_by_default(self, stop_mod, monkeypatch):
        calls = _stage(stop_mod, monkeypatch, "RUNNING")
        stop_mod.main()
        core_calls = [c for c in calls if isinstance(c, dict)]
        assert core_calls[0]["wait"] is False
        assert core_calls[0]["region"] == REGION

    def test_operator_gets_an_abort_window_before_core_stop_fleet_is_called(self, stop_mod, monkeypatch):
        """Stopping the fleet kills in-flight Slurm jobs, so the window has
        to open before the actual update-compute-fleet call, which now
        happens inside core_stop_fleet."""
        calls = _stage(stop_mod, monkeypatch, "RUNNING")
        stop_mod.main()
        assert calls[0] == "abort_before_core"

    def test_already_stopped_fleet_never_calls_core_stop_fleet(self, stop_mod, monkeypatch):
        calls = _stage(stop_mod, monkeypatch, "STOPPED")
        with pytest.raises(SystemExit) as exc:
            stop_mod.main()
        assert exc.value.code == 0
        assert calls == []

    def test_protected_fleet_aborts_without_calling_core_stop_fleet(self, stop_mod, monkeypatch):
        """The shim's own preflight check already knows about PROTECTED --
        core_stop_fleet is never reached, so its own PClusterMakerError path
        for this same state is redundant defense, not the only guard."""
        calls = _stage(stop_mod, monkeypatch, "PROTECTED")
        with pytest.raises(SystemExit) as exc:
            stop_mod.main()
        assert "PROTECTED" in str(exc.value.code)
        assert calls == []

    def test_stop_in_progress_prints_before_calling_core_stop_fleet_no_abort_window(
        self, stop_mod, monkeypatch, capsys
    ):
        calls = _stage(stop_mod, monkeypatch, "STOP_REQUESTED")
        stop_mod.main()
        out = capsys.readouterr().out
        assert "a stop is already in progress" in out
        assert "abort_before_core" not in calls, (
            "the confirmation gate must only fire on a genuinely new request"
        )

    def test_missing_cluster_record_aborts(self, stop_mod, monkeypatch):
        calls = _stage(stop_mod, monkeypatch, "RUNNING")
        monkeypatch.setattr(stop_mod, "_read_cluster_record", lambda n, r: None)
        with pytest.raises(SystemExit) as exc:
            stop_mod.main()
        assert "no cluster record found" in str(exc.value.code)
        assert calls == []

    def test_region_flag_overrides_the_cluster_record(self, stop_mod, monkeypatch):
        calls = _stage(stop_mod, monkeypatch, "RUNNING", argv_extra=["-R", "eu-west-1"])
        stop_mod.main()
        core_calls = [c for c in calls if isinstance(c, dict)]
        assert core_calls[0]["region"] == "eu-west-1"

    def test_blank_region_in_record_aborts(self, stop_mod, monkeypatch):
        calls = _stage(stop_mod, monkeypatch, "RUNNING")
        monkeypatch.setattr(stop_mod, "_read_cluster_record", lambda n, r: {"region": ""})
        with pytest.raises(SystemExit) as exc:
            stop_mod.main()
        assert "-R/--region" in str(exc.value.code)
        assert calls == []

    def test_core_pcluster_maker_error_becomes_a_clean_exit(self, stop_mod, monkeypatch):
        _stage(
            stop_mod, monkeypatch, "RUNNING",
            core_raises=pcluster_core.PClusterMakerError("ERROR: something went wrong"),
        )
        with pytest.raises(SystemExit) as exc:
            stop_mod.main()
        assert exc.value.code == "ERROR: something went wrong"

    def test_wait_flag_is_passed_through(self, stop_mod, monkeypatch):
        calls = _stage(stop_mod, monkeypatch, "RUNNING", argv_extra=["-W"])
        stop_mod.main()
        core_calls = [c for c in calls if isinstance(c, dict)]
        assert core_calls[0]["wait"] is True

    def test_wait_suppresses_the_check_status_line(self, stop_mod, monkeypatch, capsys):
        _stage(stop_mod, monkeypatch, "RUNNING", argv_extra=["-W"])
        stop_mod.main()
        assert "Check status:" not in capsys.readouterr().out

    def test_no_wait_prints_the_check_status_line(self, stop_mod, monkeypatch, capsys):
        _stage(stop_mod, monkeypatch, "RUNNING")
        stop_mod.main()
        assert "Check status:" in capsys.readouterr().out


class TestStartFleetCliShim:
    def test_stopped_fleet_calls_core_start_fleet(self, start_mod, monkeypatch):
        calls = _stage(start_mod, monkeypatch, "STOPPED")
        start_mod.main()
        core_calls = [c for c in calls if isinstance(c, dict)]
        assert core_calls[0]["region"] == REGION
        assert core_calls[0]["wait"] is False

    def test_already_running_fleet_never_calls_core_start_fleet(self, start_mod, monkeypatch):
        calls = _stage(start_mod, monkeypatch, "RUNNING")
        with pytest.raises(SystemExit) as exc:
            start_mod.main()
        assert exc.value.code == 0
        assert calls == []

    def test_protected_fleet_aborts_without_calling_core_start_fleet(self, start_mod, monkeypatch):
        calls = _stage(start_mod, monkeypatch, "PROTECTED")
        with pytest.raises(SystemExit) as exc:
            start_mod.main()
        assert "PROTECTED" in str(exc.value.code)
        assert calls == []

    def test_start_has_no_abort_window(self, start_mod):
        """Starting a fleet destroys nothing, so it deliberately does not
        prompt. Pinning this keeps a copy-paste from stop_pcluster.py from
        adding a pointless 5-second delay to every start."""
        assert not hasattr(start_mod, "ctrlC_Abort")

    def test_wait_flag_is_passed_through(self, start_mod, monkeypatch):
        calls = _stage(start_mod, monkeypatch, "STOPPED", argv_extra=["-W"])
        start_mod.main()
        core_calls = [c for c in calls if isinstance(c, dict)]
        assert core_calls[0]["wait"] is True

    def test_core_pcluster_maker_error_becomes_a_clean_exit(self, start_mod, monkeypatch):
        _stage(
            start_mod, monkeypatch, "STOPPED",
            core_raises=pcluster_core.PClusterMakerError("ERROR: something went wrong"),
        )
        with pytest.raises(SystemExit) as exc:
            start_mod.main()
        assert exc.value.code == "ERROR: something went wrong"
