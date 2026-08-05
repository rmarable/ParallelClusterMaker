"""Tests for grafana_tunnel.py exit-status handling."""

import os
import sys
import types

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))


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
    monkeypatch.setattr(
        mod, "_read_cluster_record", lambda n, r: {"enable_monitoring": "true"}
    )
    monkeypatch.setattr(
        mod.subprocess, "run",
        lambda *a, **k: types.SimpleNamespace(returncode=returncode),
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
