"""
Unit tests for the three-tier defaults resolution and --use_defaults file
loading logic extracted into src/pcluster_core.py.

Covers:
  - _resolve()      — CLI > file_defaults > hardcoded precedence, cast support
  - _resolve_bool() — string-to-bool normalisation across all tiers
  - _load_defaults_file() — missing file, toolkit-copy warning, valid YAML
"""

import os
import sys
import types

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
)

from pcluster_core import (
    _resolve as resolve,
    _resolve_bool as resolve_bool,
    _load_defaults_file,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _args(**kwargs):
    """Minimal argparse.Namespace stand-in."""
    return types.SimpleNamespace(**kwargs)


HARDCODED = {
    "base_os": "ubuntu2404",
    "cluster_type": "spot",
    "max_queue_size": 10,
    "debug_mode": "false",
    "enable_efs": "false",
}


# ---------------------------------------------------------------------------
# resolve() — precedence
# ---------------------------------------------------------------------------


class TestResolve:
    def test_cli_wins_over_file_and_hardcoded(self):
        args = _args(base_os="rhel9")
        file_d = {"base_os": "ubuntu2204"}
        assert resolve("base_os", args, file_d, HARDCODED) == "rhel9"

    def test_file_wins_over_hardcoded_when_cli_is_none(self):
        args = _args(base_os=None)
        file_d = {"base_os": "ubuntu2204"}
        assert resolve("base_os", args, file_d, HARDCODED) == "ubuntu2204"

    def test_hardcoded_used_when_cli_and_file_absent(self):
        args = _args(base_os=None)
        assert resolve("base_os", args, {}, HARDCODED) == "ubuntu2404"

    def test_returns_none_when_absent_everywhere(self):
        args = _args(unknown=None)
        assert resolve("unknown", args, {}, {}) is None

    def test_cast_applied_to_file_default(self):
        args = _args(max_queue_size=None)
        file_d = {"max_queue_size": "20"}
        result = resolve("max_queue_size", args, file_d, HARDCODED, cast=int)
        assert result == 20
        assert isinstance(result, int)

    def test_cast_applied_to_hardcoded(self):
        args = _args(max_queue_size=None)
        result = resolve("max_queue_size", args, {}, HARDCODED, cast=int)
        assert result == 10
        assert isinstance(result, int)

    def test_cast_not_applied_to_cli_arg(self):
        # argparse already coerces CLI args — we must not double-cast
        args = _args(max_queue_size=5)  # already an int
        result = resolve("max_queue_size", args, {}, HARDCODED, cast=int)
        assert result == 5

    def test_cast_skipped_when_value_is_none_in_file(self):
        args = _args(max_queue_size=None)
        file_d = {"max_queue_size": None}
        result = resolve("max_queue_size", args, file_d, HARDCODED, cast=int)
        assert result is None

    def test_empty_file_defaults_falls_through_to_hardcoded(self):
        args = _args(cluster_type=None)
        assert resolve("cluster_type", args, {}, HARDCODED) == "spot"

    def test_file_default_overrides_hardcoded_even_for_falsy_value(self):
        # '0' is falsy-ish but must still win over the hardcoded '10'
        args = _args(max_queue_size=None)
        file_d = {"max_queue_size": 0}
        result = resolve("max_queue_size", args, file_d, HARDCODED)
        assert result == 0


# ---------------------------------------------------------------------------
# resolve_bool() — string normalisation
# ---------------------------------------------------------------------------


class TestResolveBool:
    def test_true_string_from_hardcoded(self):
        args = _args(debug_mode=None)
        hc = {"debug_mode": "true"}
        assert resolve_bool("debug_mode", args, {}, hc) is True

    def test_false_string_from_hardcoded(self):
        args = _args(debug_mode=None)
        assert resolve_bool("debug_mode", args, {}, HARDCODED) is False

    def test_cli_true_string(self):
        args = _args(debug_mode="true")
        assert resolve_bool("debug_mode", args, {}, HARDCODED) is True

    def test_cli_false_string(self):
        args = _args(debug_mode="false")
        assert resolve_bool("debug_mode", args, {}, HARDCODED) is False

    def test_file_default_true(self):
        args = _args(enable_efs=None)
        file_d = {"enable_efs": "true"}
        assert resolve_bool("enable_efs", args, file_d, HARDCODED) is True

    def test_file_default_false(self):
        args = _args(enable_efs=None)
        assert resolve_bool("enable_efs", args, {}, HARDCODED) is False

    def test_uppercase_true_normalised(self):
        args = _args(debug_mode=None)
        hc = {"debug_mode": "TRUE"}
        assert resolve_bool("debug_mode", args, {}, hc) is True

    def test_mixed_case_false_normalised(self):
        args = _args(debug_mode=None)
        hc = {"debug_mode": "False"}
        assert resolve_bool("debug_mode", args, {}, hc) is False

    def test_python_bool_true_in_yaml_file(self):
        # pyyaml renders YAML `true:` as Python True; must still work
        args = _args(debug_mode=None)
        file_d = {"debug_mode": True}
        assert resolve_bool("debug_mode", args, file_d, HARDCODED) is True

    def test_python_bool_false_in_yaml_file(self):
        args = _args(debug_mode=None)
        file_d = {"debug_mode": False}
        assert resolve_bool("debug_mode", args, file_d, HARDCODED) is False


# ---------------------------------------------------------------------------
# _load_defaults_file()
# ---------------------------------------------------------------------------


class TestLoadDefaultsFile:
    def test_loads_valid_yaml(self, tmp_path):
        f = tmp_path / "my-cluster.yml"
        f.write_text("base_os: rhel9\nmax_queue_size: 20\n")
        toolkit = str(tmp_path / "pcluster_defaults.yml")
        result = _load_defaults_file(str(f), toolkit, "my-cluster")
        assert result == {"base_os": "rhel9", "max_queue_size": 20}

    def test_missing_file_raises_systemexit(self, tmp_path):
        toolkit = str(tmp_path / "pcluster_defaults.yml")
        with pytest.raises(SystemExit):
            _load_defaults_file(
                str(tmp_path / "nonexistent.yml"), toolkit, "my-cluster"
            )

    def test_empty_yaml_returns_empty_dict(self, tmp_path):
        f = tmp_path / "empty.yml"
        f.write_text("")
        toolkit = str(tmp_path / "pcluster_defaults.yml")
        result = _load_defaults_file(str(f), toolkit, "my-cluster")
        assert result == {}

    def test_toolkit_copy_prints_warning(self, tmp_path, capsys):
        toolkit = tmp_path / "pcluster_defaults.yml"
        toolkit.write_text("base_os: ubuntu2404\n")
        _load_defaults_file(str(toolkit), str(toolkit), "my-cluster")
        assert "WARNING" in capsys.readouterr().out

    def test_own_copy_no_warning(self, tmp_path, capsys):
        f = tmp_path / "my-cluster.yml"
        f.write_text("base_os: rhel9\n")
        toolkit = tmp_path / "pcluster_defaults.yml"
        toolkit.write_text("base_os: ubuntu2404\n")
        _load_defaults_file(str(f), str(toolkit), "my-cluster")
        assert "WARNING" not in capsys.readouterr().out

    def test_systemexit_message_includes_cluster_name(self, tmp_path):
        toolkit = str(tmp_path / "pcluster_defaults.yml")
        with pytest.raises(SystemExit) as exc_info:
            _load_defaults_file(str(tmp_path / "missing.yml"), toolkit, "my-cluster")
        assert "my-cluster" in str(exc_info.value)
