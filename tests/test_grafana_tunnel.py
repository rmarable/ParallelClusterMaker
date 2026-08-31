"""Tests for grafana_tunnel.py exit-status handling."""

import os
import sys
import types

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

import pcluster_core

_FULL_REC_DICT = {
    "cluster_name": "mycluster",
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
    "ssh_keypair": "/tmp/mycluster.pem",
    "ec2_keypair": "mycluster-keypair",
    "ec2_user": "ubuntu",
    "s3_bucketname": "my-bucket",
    "enable_monitoring": "true",
}


def _load():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "grafana_tunnel", os.path.join(REPO_ROOT, "grafana_tunnel.py")
    )
    orig = sys.prefix
    sys.prefix = os.path.join(REPO_ROOT, ".venv")
    try:
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.prefix = orig
    return mod


def _stage(tmp_path, monkeypatch, returncode):
    mod = _load()
    name = "mycluster"
    script_dir = tmp_path / "active_clusters" / name
    script_dir.mkdir(parents=True)
    (script_dir / f"grafana_tunnel.{name}.sh").write_text("#!/bin/bash\nexit 0\n")
    monkeypatch.setattr(mod, "_repo_root", str(tmp_path))
    monkeypatch.setattr(mod, "_read_cluster_record", lambda n, r: dict(_FULL_REC_DICT))
    monkeypatch.setattr(
        mod.subprocess,
        "run",
        lambda *a, **k: types.SimpleNamespace(returncode=returncode, stdout="", stderr=""),
    )
    monkeypatch.setattr(sys, "argv", ["grafana_tunnel.py", "-N", name])
    return mod


class TestTunnelExitStatus:
    """The tunnel script's exit status is the only evidence that ssh -L bound
    the local port; check=False with no returncode inspection reported success
    for a tunnel that never came up."""

    def test_nonzero_exit_is_fatal(self, tmp_path, monkeypatch):
        mod = _stage(tmp_path, monkeypatch, returncode=1)
        with pytest.raises(SystemExit) as exc:
            mod.main()
        assert exc.value.code not in (0, None)
        assert "ERROR" in str(exc.value.code)

    def test_zero_exit_is_silent_success(self, tmp_path, monkeypatch):
        mod = _stage(tmp_path, monkeypatch, returncode=0)
        mod.main()


# ---------------------------------------------------------------------------
# core_manage_grafana_tunnel -- the core function grafana_tunnel.py's main()
# now wraps, added in the Workstream 1 migration
# (docs/parallelclustermaker-mcp-plan.md). Direct tests: main()'s own two
# tests above only exercise the tunnel-script-failure path end to end.
# ---------------------------------------------------------------------------


def _record(**overrides):
    return pcluster_core.ClusterRecord(**{**_FULL_REC_DICT, **overrides})


class TestCoreManageGrafanaTunnel:
    def test_monitoring_disabled_raises(self, tmp_path):
        with pytest.raises(pcluster_core.PClusterMakerError, match="monitoring is not enabled"):
            pcluster_core.core_manage_grafana_tunnel(
                cluster_record=_record(enable_monitoring="false"),
                tunnel_script_path=str(tmp_path / "nope.sh"),
            )

    def test_missing_tunnel_script_raises(self, tmp_path):
        with pytest.raises(pcluster_core.PClusterMakerError, match="tunnel script not found"):
            pcluster_core.core_manage_grafana_tunnel(
                cluster_record=_record(),
                tunnel_script_path=str(tmp_path / "nope.sh"),
            )

    def test_missing_script_checked_even_when_monitoring_enabled(self, tmp_path):
        """Both preconditions are checked -- monitoring being enabled must not
        short-circuit the script-existence check."""
        with pytest.raises(pcluster_core.PClusterMakerError, match="tunnel script not found"):
            pcluster_core.core_manage_grafana_tunnel(
                cluster_record=_record(enable_monitoring="true"),
                tunnel_script_path=str(tmp_path / "nope.sh"),
            )

    def test_successful_start_returns_result(self, tmp_path, monkeypatch):
        script = tmp_path / "grafana_tunnel.mycluster.sh"
        script.write_text("#!/bin/bash\nexit 0\n")
        monkeypatch.setattr(
            pcluster_core.subprocess,
            "run",
            lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="", stderr=""),
        )
        result = pcluster_core.core_manage_grafana_tunnel(
            cluster_record=_record(),
            tunnel_script_path=str(script),
            port=9000,
        )
        assert result.success is True
        assert result.error is None
        assert result.action == "start"
        assert result.port == 9000
        assert result.cluster_name == "mycluster"

    def test_stop_action_is_passed_through(self, tmp_path, monkeypatch):
        script = tmp_path / "grafana_tunnel.mycluster.sh"
        script.write_text("#!/bin/bash\nexit 0\n")
        seen = {}

        def _fake_run(cmd, **k):
            seen["cmd"] = cmd
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(pcluster_core.subprocess, "run", _fake_run)
        result = pcluster_core.core_manage_grafana_tunnel(
            cluster_record=_record(),
            tunnel_script_path=str(script),
            stop=True,
        )
        assert result.action == "stop"
        assert seen["cmd"][-1] == "stop"

    def test_failed_script_returns_result_not_raise(self, tmp_path, monkeypatch):
        """A tunnel script that runs but fails to bind the port is a
        TunnelResult with success=False, not a raised exception -- it's not a
        precondition failure, it's the operation's own outcome."""
        script = tmp_path / "grafana_tunnel.mycluster.sh"
        script.write_text("#!/bin/bash\nexit 1\n")
        monkeypatch.setattr(
            pcluster_core.subprocess,
            "run",
            lambda *a, **k: types.SimpleNamespace(returncode=1, stdout="", stderr=""),
        )
        result = pcluster_core.core_manage_grafana_tunnel(
            cluster_record=_record(),
            tunnel_script_path=str(script),
        )
        assert result.success is False
        assert "exit 1" in result.error


class TestTheTunnelScriptNeverWritesToOurStdout:
    """On the stdio MCP transport this process's stdout **is** the JSON-RPC
    stream. The tunnel script prints -- "Tunnelling via SSM (i-...)" and
    "Grafana tunnel open for <cluster>." -- and those lines were inherited,
    so they landed in the protocol. Observed live: the client logged
    "Failed to parse JSONRPC message from server" once per line, corrupting
    the session for every later call, not just this one.

    The output is captured and returned rather than discarded, so a failing
    tunnel is still diagnosable, and grafana_tunnel.py prints it back on
    the CLI surface where there is no protocol to corrupt.
    """

    def test_the_child_never_inherits_stdout(self, monkeypatch, tmp_path):
        """Asserted on the call itself: a test that only checks what
        reached stdout would pass against an inherited-but-quiet script,
        and the real one is not quiet."""
        import types

        import pcluster_core

        seen = {}

        def _fake_run(cmd, **kw):
            seen.update(kw)
            return types.SimpleNamespace(returncode=0, stdout="noisy\n", stderr="")

        script = tmp_path / "tunnel.sh"
        script.write_text("#!/bin/bash\necho noisy\n")
        monkeypatch.setattr(pcluster_core.subprocess, "run", _fake_run)
        pcluster_core.core_manage_grafana_tunnel(
            cluster_record=_record(),
            tunnel_script_path=str(script),
            port=3000,
        )
        assert seen.get("capture_output") is True, (
            "the tunnel script's stdout must be captured; inheriting it "
            "corrupts the MCP stdio protocol"
        )

    def test_the_output_is_returned_not_swallowed(self, monkeypatch, tmp_path):
        """Capturing must not mean discarding -- a failing tunnel is
        diagnosed from exactly this text."""
        import types

        import pcluster_core

        monkeypatch.setattr(
            pcluster_core.subprocess,
            "run",
            lambda *a, **k: types.SimpleNamespace(
                returncode=1, stdout="Tunnelling via SSM (i-abc)\n", stderr="boom\n"
            ),
        )
        script = tmp_path / "tunnel.sh"
        script.write_text("#!/bin/bash\n")
        r = pcluster_core.core_manage_grafana_tunnel(
            cluster_record=_record(),
            tunnel_script_path=str(script),
            port=3000,
        )
        assert r.success is False
        assert "Tunnelling via SSM" in r.output and "boom" in r.output

    def test_the_cli_still_shows_it(self):
        """The CLI has no protocol to corrupt and the operator wants the
        text, so grafana_tunnel.py prints it back."""
        import os

        body = open(
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "grafana_tunnel.py"
            )
        ).read()
        assert "result.output" in body, (
            "the CLI must print the captured output or the operator loses it"
        )
