"""
Unit tests for kill_pcluster.py and access_cluster.py logic extracted into
src/pcluster_core.py.  No AWS credentials or venv required.
"""

import os
import sys
import types

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from pcluster_core import (
    _read_serial_first_line,
    _extract_rebuild_command,
    _resolve_access_script_path,
    _read_turbot_from_vars_file,
)

# ---------------------------------------------------------------------------
# _read_serial_first_line
# ---------------------------------------------------------------------------


class TestReadSerialFirstLine:
    def test_reads_first_line_only(self, tmp_path):
        f = tmp_path / "cluster.serial"
        f.write_text(
            "mycluster-00305910072026\nansible-playbook ...\n./make_pcluster.py -N mycluster\n"
        )
        assert _read_serial_first_line(str(f)) == "mycluster-00305910072026"

    def test_strips_trailing_newline(self, tmp_path):
        f = tmp_path / "cluster.serial"
        f.write_text("mycluster-00305910072026\n")
        result = _read_serial_first_line(str(f))
        assert "\n" not in result
        assert result == "mycluster-00305910072026"

    def test_no_trailing_newline_in_file(self, tmp_path):
        f = tmp_path / "cluster.serial"
        f.write_text("mycluster-00305910072026")
        assert _read_serial_first_line(str(f)) == "mycluster-00305910072026"

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            _read_serial_first_line(str(tmp_path / "nonexistent.serial"))


# ---------------------------------------------------------------------------
# _extract_rebuild_command
# ---------------------------------------------------------------------------


class TestExtractRebuildCommand:
    def test_returns_last_make_pcluster_line(self, tmp_path):
        f = tmp_path / "cluster.serial"
        f.write_text(
            "mycluster-00305910072026\n"
            "ansible-playbook --extra-vars ... delete_pcluster.yml\n"
            "./make_pcluster.py -N mycluster -O rodney -A us-east-1a\n"
        )
        result = _extract_rebuild_command(str(f))
        assert result == "./make_pcluster.py -N mycluster -O rodney -A us-east-1a"

    def test_returns_last_matching_line_when_multiple(self, tmp_path):
        f = tmp_path / "cluster.serial"
        f.write_text(
            "mycluster-00305910072026\n"
            "./make_pcluster.py -N mycluster -O rodney -A us-east-1a\n"
            "ansible-playbook ...\n"
            "./make_pcluster.py -N mycluster -O rodney -A us-east-1b\n"
        )
        result = _extract_rebuild_command(str(f))
        assert result == "./make_pcluster.py -N mycluster -O rodney -A us-east-1b"

    def test_returns_none_when_no_matching_lines(self, tmp_path):
        f = tmp_path / "cluster.serial"
        f.write_text("mycluster-00305910072026\nansible-playbook ...\n")
        assert _extract_rebuild_command(str(f)) is None

    def test_returns_none_for_missing_file(self, tmp_path):
        result = _extract_rebuild_command(str(tmp_path / "nonexistent.serial"))
        assert result is None

    def test_matches_absolute_path_commands(self, tmp_path):
        f = tmp_path / "cluster.serial"
        f.write_text(
            "mycluster-00305910072026\n"
            "/home/rodney/ParallelClusterMaker/make_pcluster.py -N mycluster\n"
        )
        result = _extract_rebuild_command(str(f))
        assert (
            result == "/home/rodney/ParallelClusterMaker/make_pcluster.py -N mycluster"
        )

    def test_strips_trailing_whitespace(self, tmp_path):
        f = tmp_path / "cluster.serial"
        f.write_text("./make_pcluster.py -N mycluster   \n")
        result = _extract_rebuild_command(str(f))
        assert result == "./make_pcluster.py -N mycluster"


# ---------------------------------------------------------------------------
# _resolve_access_script_path
# ---------------------------------------------------------------------------


class TestResolveAccessScriptPath:
    def test_valid_cluster_name_returns_expected_path(self, tmp_path):
        root = str(tmp_path / "active_clusters")
        path = _resolve_access_script_path(root, "mycluster")
        expected = os.path.join(root, "mycluster", "access_cluster.mycluster.sh")
        assert path == expected

    def test_path_contains_cluster_name_in_filename(self, tmp_path):
        root = str(tmp_path / "active_clusters")
        path = _resolve_access_script_path(root, "my-cluster-01")
        assert "access_cluster.my-cluster-01.sh" in path

    def test_traversal_with_dotdot_raises(self, tmp_path):
        root = str(tmp_path / "active_clusters")
        with pytest.raises(SystemExit):
            _resolve_access_script_path(root, "../etc/passwd")

    def test_traversal_with_nested_dotdot_raises(self, tmp_path):
        root = str(tmp_path / "active_clusters")
        with pytest.raises(SystemExit):
            _resolve_access_script_path(root, "good/../../etc/shadow")

    def test_result_is_under_root(self, tmp_path):
        root = str(tmp_path / "active_clusters")
        path = _resolve_access_script_path(root, "testcluster")
        assert path.startswith(root + os.sep)

    def test_hyphens_and_digits_in_name(self, tmp_path):
        root = str(tmp_path / "active_clusters")
        path = _resolve_access_script_path(root, "hpc-cluster-2a")
        assert path.endswith("access_cluster.hpc-cluster-2a.sh")


# ---------------------------------------------------------------------------
# _read_turbot_from_vars_file
# ---------------------------------------------------------------------------


class TestReadTurbotFromVarsFile:
    def test_returns_turbot_account_when_present(self, tmp_path):
        f = tmp_path / "mycluster.yml"
        f.write_text("cluster_name: mycluster\nturbot_account: acme-prod\n")
        assert _read_turbot_from_vars_file(str(f)) == "acme-prod"

    def test_returns_disabled_when_key_is_disabled(self, tmp_path):
        f = tmp_path / "mycluster.yml"
        f.write_text("cluster_name: mycluster\nturbot_account: disabled\n")
        assert _read_turbot_from_vars_file(str(f)) == "disabled"

    def test_returns_disabled_when_key_absent(self, tmp_path):
        f = tmp_path / "mycluster.yml"
        f.write_text("cluster_name: mycluster\n")
        assert _read_turbot_from_vars_file(str(f)) == "disabled"

    def test_returns_disabled_for_missing_file(self, tmp_path):
        assert _read_turbot_from_vars_file(str(tmp_path / "nonexistent.yml")) == "disabled"

    def test_returns_disabled_for_empty_file(self, tmp_path):
        f = tmp_path / "mycluster.yml"
        f.write_text("")
        assert _read_turbot_from_vars_file(str(f)) == "disabled"


# ---------------------------------------------------------------------------
# access_cluster.py: -L/-H node-type resolution
# ---------------------------------------------------------------------------


def _load_access_cluster():
    """Load access_cluster.py as a module, mirroring test_grafana_tunnel.py's
    _load() -- the venv guard at import time checks sys.prefix, not
    sys.executable, so it must be spoofed the same way here."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "access_cluster", os.path.join(REPO_ROOT, "access_cluster.py")
    )
    orig = sys.prefix
    sys.prefix = os.path.join(REPO_ROOT, ".venv")
    try:
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.prefix = orig
    return mod


def _stage_access_cluster(tmp_path, monkeypatch, cluster_name, record):
    """Stage a fake access script under tmp_path and redirect both the module's
    _repo_root (used by _read_cluster_record) and its own __file__ (used
    inline for _cluster_data_root, independently of _repo_root -- both must
    move together or the script lookup still hits the real repo)."""
    mod = _load_access_cluster()
    script_dir = tmp_path / "active_clusters" / cluster_name
    script_dir.mkdir(parents=True)
    (script_dir / f"access_cluster.{cluster_name}.sh").write_text(
        "#!/bin/bash\nexit 0\n"
    )
    monkeypatch.setattr(mod, "_repo_root", str(tmp_path))
    monkeypatch.setattr(mod, "__file__", str(tmp_path / "access_cluster.py"))
    monkeypatch.setattr(mod, "_read_cluster_record", lambda n, r: record)

    calls = []
    monkeypatch.setattr(
        mod.subprocess, "run",
        lambda cmd, env=None, **k: calls.append({"cmd": cmd, "env": env})
        or types.SimpleNamespace(returncode=0),
    )
    return mod, calls


class TestAccessClusterNodeTypeResolution:
    """-L/-H resolve to one of two fixed literals ("HeadNode"/"LoginNode")
    passed to the rendered script via ACCESS_NODE_TYPE -- chosen by Python
    control flow, never interpolated from cluster-provided text, so there is
    no injection surface at the subprocess boundary. Default selection (no
    flag) follows the cluster's own enable_loginnode, preserving today's
    behavior for clusters that never had a login node."""

    def test_login_node_flag_selects_login_node(self, tmp_path, monkeypatch):
        mod, calls = _stage_access_cluster(
            tmp_path, monkeypatch, "mycluster", {"enable_loginnode": "true"}
        )
        monkeypatch.setattr(sys, "argv", ["access_cluster.py", "-N", "mycluster", "-L"])
        with pytest.raises(SystemExit) as exc:
            mod.main()
        assert exc.value.code == 0
        assert calls[0]["env"]["ACCESS_NODE_TYPE"] == "LoginNode"

    def test_head_node_flag_selects_head_node_even_when_loginnode_enabled(
        self, tmp_path, monkeypatch
    ):
        mod, calls = _stage_access_cluster(
            tmp_path, monkeypatch, "mycluster", {"enable_loginnode": "true"}
        )
        monkeypatch.setattr(sys, "argv", ["access_cluster.py", "-N", "mycluster", "-H"])
        with pytest.raises(SystemExit):
            mod.main()
        assert calls[0]["env"]["ACCESS_NODE_TYPE"] == "HeadNode"

    def test_default_is_login_node_when_enabled(self, tmp_path, monkeypatch):
        mod, calls = _stage_access_cluster(
            tmp_path, monkeypatch, "mycluster", {"enable_loginnode": "true"}
        )
        monkeypatch.setattr(sys, "argv", ["access_cluster.py", "-N", "mycluster"])
        with pytest.raises(SystemExit):
            mod.main()
        assert calls[0]["env"]["ACCESS_NODE_TYPE"] == "LoginNode"

    def test_default_is_head_node_when_disabled(self, tmp_path, monkeypatch):
        mod, calls = _stage_access_cluster(
            tmp_path, monkeypatch, "mycluster", {"enable_loginnode": "false"}
        )
        monkeypatch.setattr(sys, "argv", ["access_cluster.py", "-N", "mycluster"])
        with pytest.raises(SystemExit):
            mod.main()
        assert calls[0]["env"]["ACCESS_NODE_TYPE"] == "HeadNode"

    def test_default_is_head_node_on_a_pre_feature_cluster_record(
        self, tmp_path, monkeypatch
    ):
        """A cluster built before this feature existed has a vars file with no
        enable_loginnode key at all -- .get() returning None must fall through
        to "not enabled", not raise and not silently enable login-node access."""
        mod, calls = _stage_access_cluster(tmp_path, monkeypatch, "mycluster", {})
        monkeypatch.setattr(sys, "argv", ["access_cluster.py", "-N", "mycluster"])
        with pytest.raises(SystemExit):
            mod.main()
        assert calls[0]["env"]["ACCESS_NODE_TYPE"] == "HeadNode"

    def test_login_node_flag_errors_out_when_disabled(self, tmp_path, monkeypatch):
        mod, calls = _stage_access_cluster(
            tmp_path, monkeypatch, "mycluster", {"enable_loginnode": "false"}
        )
        monkeypatch.setattr(sys, "argv", ["access_cluster.py", "-N", "mycluster", "-L"])
        with pytest.raises(SystemExit) as exc:
            mod.main()
        assert "no login node is configured" in str(exc.value.code)
        assert "--enable_loginnode=true" in str(exc.value.code)
        assert not calls, "the rendered script must not run when -L is rejected"

    def test_login_node_flag_errors_out_on_a_pre_feature_cluster_record(
        self, tmp_path, monkeypatch
    ):
        mod, calls = _stage_access_cluster(tmp_path, monkeypatch, "mycluster", {})
        monkeypatch.setattr(sys, "argv", ["access_cluster.py", "-N", "mycluster", "-L"])
        with pytest.raises(SystemExit) as exc:
            mod.main()
        assert "no login node is configured" in str(exc.value.code)
        assert not calls

    def test_login_node_and_head_node_flags_are_mutually_exclusive(
        self, tmp_path, monkeypatch
    ):
        mod, _ = _stage_access_cluster(
            tmp_path, monkeypatch, "mycluster", {"enable_loginnode": "true"}
        )
        monkeypatch.setattr(
            sys, "argv", ["access_cluster.py", "-N", "mycluster", "-L", "-H"]
        )
        with pytest.raises(SystemExit) as exc:
            mod.main()
        assert exc.value.code == 2, "argparse's own mutually-exclusive-group exit code"
