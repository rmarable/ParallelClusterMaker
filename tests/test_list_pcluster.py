"""
Tests for _read_cluster_record (pcluster_core) and list_pcluster helpers.
"""

import os
import sys
import pytest

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_repo_root, "src"))

from pcluster_core import _read_cluster_record

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MINIMAL_VARS = """\
cluster_name: "mycluster"
cluster_owner: "alice"
cluster_serial_number: "mycluster-00001220260720"
region: "us-east-1"
az: "us-east-1a"
DEPLOYMENT_DATE: "20-July-2026"
headnode_instance_type: "c8g.2xlarge"
compute_instance_type: "c8g.2xlarge"
cpu_instance_types:
  - "c8g.2xlarge"
gpu_instance_types: []
enable_cpu_queue: "true"
enable_gpu_queue: "false"
initial_cpu_queue_size: 0
max_cpu_queue_size: 8
initial_gpu_queue_size: 0
max_gpu_queue_size: 4
cluster_type: "ondemand"
"""


def _make_cluster_tree(tmp_path, cluster_name, vars_content=None):
    """Create a minimal active_clusters/<name>/ dir and optional vars file."""
    active = tmp_path / "active_clusters" / cluster_name
    active.mkdir(parents=True)
    (active / f"{cluster_name}.serial").write_text(f"{cluster_name}-serial")
    src_vars = tmp_path / "src" / "vars_files"
    src_vars.mkdir(parents=True)
    if vars_content is not None:
        (src_vars / f"{cluster_name}.yml").write_text(vars_content)
    return tmp_path


# ---------------------------------------------------------------------------
# _read_cluster_record
# ---------------------------------------------------------------------------


class TestReadClusterRecord:
    def test_returns_dict_for_valid_cluster(self, tmp_path):
        root = _make_cluster_tree(tmp_path, "mycluster", _MINIMAL_VARS)
        rec = _read_cluster_record("mycluster", str(root))
        assert isinstance(rec, dict)
        assert rec["cluster_name"] == "mycluster"
        assert rec["cluster_owner"] == "alice"
        assert rec["region"] == "us-east-1"
        assert rec["serial"] == "mycluster-00001220260720"
        assert rec["headnode_instance_type"] == "c8g.2xlarge"
        assert rec["cpu_instance_types"] == ["c8g.2xlarge"]
        assert rec["gpu_instance_types"] == []
        assert rec["enable_cpu_queue"] == "true"
        assert rec["enable_gpu_queue"] == "false"
        assert rec["initial_cpu_queue_size"] == 0
        assert rec["max_cpu_queue_size"] == 8
        assert rec["cluster_type"] == "ondemand"
        assert rec["deployment_date"] == "20-July-2026"

    def test_returns_none_when_vars_file_missing(self, tmp_path):
        root = _make_cluster_tree(tmp_path, "mycluster", vars_content=None)
        rec = _read_cluster_record("mycluster", str(root))
        assert rec is None

    def test_returns_none_when_active_cluster_dir_missing(self, tmp_path):
        # vars file exists but active_clusters dir does not
        src_vars = tmp_path / "src" / "vars_files"
        src_vars.mkdir(parents=True)
        (src_vars / "mycluster.yml").write_text(_MINIMAL_VARS)
        rec = _read_cluster_record("mycluster", str(tmp_path))
        assert rec is None

    def test_returns_none_for_invalid_yaml(self, tmp_path):
        root = _make_cluster_tree(tmp_path, "mycluster", vars_content=": : invalid: {yaml")
        rec = _read_cluster_record("mycluster", str(root))
        assert rec is None

    def test_returns_none_for_yaml_not_a_dict(self, tmp_path):
        root = _make_cluster_tree(tmp_path, "mycluster", vars_content="- just\n- a\n- list\n")
        rec = _read_cluster_record("mycluster", str(root))
        assert rec is None

    def test_path_traversal_rejected(self, tmp_path):
        # Manually create a dir whose realpath escapes active_clusters/
        (tmp_path / "active_clusters").mkdir()
        # Pass a name that resolves outside active_clusters/ via os.path.join
        # _validate_cluster_name would normally block this, but test the
        # defense-in-depth check directly
        rec = _read_cluster_record("../escape", str(tmp_path))
        assert rec is None

    def test_int_fields_default_on_missing_keys(self, tmp_path):
        sparse = "cluster_name: sparse\ncluster_owner: bob\nregion: eu-west-1\n"
        root = _make_cluster_tree(tmp_path, "sparse", sparse)
        rec = _read_cluster_record("sparse", str(root))
        assert rec is not None
        assert rec["initial_cpu_queue_size"] == 0
        assert rec["max_cpu_queue_size"] == 0

    def test_cpu_instance_types_as_comma_string(self, tmp_path):
        content = _MINIMAL_VARS.replace(
            "cpu_instance_types:\n  - \"c8g.2xlarge\"",
            'cpu_instance_types: "c8g.2xlarge, c7g.2xlarge"',
        )
        root = _make_cluster_tree(tmp_path, "mycluster", content)
        rec = _read_cluster_record("mycluster", str(root))
        assert rec["cpu_instance_types"] == ["c8g.2xlarge", "c7g.2xlarge"]

    def test_deployment_date_single_digit_day(self, tmp_path):
        content = _MINIMAL_VARS.replace('DEPLOYMENT_DATE: "20-July-2026"',
                                        'DEPLOYMENT_DATE: "4-July-2026"')
        root = _make_cluster_tree(tmp_path, "mycluster", content)
        rec = _read_cluster_record("mycluster", str(root))
        assert rec["deployment_date"] == "4-July-2026"

    def test_control_characters_are_stripped_from_strings(self, tmp_path):
        """cost_pcluster.py sanitized these on the way out; list_pcluster.py did
        not, so an embedded newline or ANSI escape in a hand-edited vars file
        broke its column alignment and reached -J JSON consumers verbatim.
        Sanitizing inside _read_cluster_record covers both tools."""
        content = _MINIMAL_VARS.replace(
            'cluster_owner: "alice"',
            'cluster_owner: "ali\\u001b[31mce\\nEVIL"',
        )
        root = _make_cluster_tree(tmp_path, "mycluster", content)
        rec = _read_cluster_record("mycluster", str(root))
        assert rec["cluster_owner"] == "ali[31mceEVIL"
        assert "\n" not in rec["cluster_owner"]
        assert "\x1b" not in rec["cluster_owner"]

    def test_control_characters_are_stripped_from_instance_type_lists(self, tmp_path):
        content = _MINIMAL_VARS.replace(
            'cpu_instance_types:\n  - "c8g.2xlarge"',
            'cpu_instance_types: "c8g.2xlarge, c7g\\u001b[0m.2xlarge"',
        )
        root = _make_cluster_tree(tmp_path, "mycluster", content)
        rec = _read_cluster_record("mycluster", str(root))
        assert rec["cpu_instance_types"] == ["c8g.2xlarge", "c7g[0m.2xlarge"]


# ---------------------------------------------------------------------------
# _age_str
# ---------------------------------------------------------------------------


class TestAgeStr:
    def _import(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "list_pcluster",
            os.path.join(_repo_root, "list_pcluster.py"),
        )
        # list_pcluster has a venv guard at module level — bypass by patching
        # sys.prefix before loading
        orig = sys.prefix
        sys.prefix = os.path.join(_repo_root, ".venv")
        try:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        finally:
            sys.prefix = orig
        return mod

    def test_today_is_not_question_mark(self):
        # _age_str has day precision — a same-day cluster shows hours or minutes
        mod = self._import()
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%-d-%B-%Y")
        result = mod._age_str(today)
        assert result != "?"
        assert result[-1] in ("m", "h")

    def test_old_cluster_shows_days(self):
        mod = self._import()
        result = mod._age_str("1-January-2025")
        assert result.endswith("d")

    def test_invalid_returns_question_mark(self):
        mod = self._import()
        assert mod._age_str("not-a-date") == "?"
        assert mod._age_str("") == "?"
        assert mod._age_str(None) == "?"
