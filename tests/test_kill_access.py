"""
Unit tests for kill_pcluster.py and access_cluster.py logic extracted into
src/pcluster_core.py.  No AWS credentials or venv required.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

from pcluster_core import (
    _read_serial_first_line,
    _extract_rebuild_command,
    _resolve_access_script_path,
)


# ---------------------------------------------------------------------------
# _read_serial_first_line
# ---------------------------------------------------------------------------

class TestReadSerialFirstLine:
    def test_reads_first_line_only(self, tmp_path):
        f = tmp_path / 'cluster.serial'
        f.write_text('mycluster-00305910072026\nansible-playbook ...\n./make_pcluster.py -N mycluster\n')
        assert _read_serial_first_line(str(f)) == 'mycluster-00305910072026'

    def test_strips_trailing_newline(self, tmp_path):
        f = tmp_path / 'cluster.serial'
        f.write_text('mycluster-00305910072026\n')
        result = _read_serial_first_line(str(f))
        assert '\n' not in result
        assert result == 'mycluster-00305910072026'

    def test_no_trailing_newline_in_file(self, tmp_path):
        f = tmp_path / 'cluster.serial'
        f.write_text('mycluster-00305910072026')
        assert _read_serial_first_line(str(f)) == 'mycluster-00305910072026'

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            _read_serial_first_line(str(tmp_path / 'nonexistent.serial'))


# ---------------------------------------------------------------------------
# _extract_rebuild_command
# ---------------------------------------------------------------------------

class TestExtractRebuildCommand:
    def test_returns_last_make_pcluster_line(self, tmp_path):
        f = tmp_path / 'cluster.serial'
        f.write_text(
            'mycluster-00305910072026\n'
            'ansible-playbook --extra-vars ... delete_pcluster.yml\n'
            './make_pcluster.py -N mycluster -O rodney -A us-east-1a\n'
        )
        result = _extract_rebuild_command(str(f))
        assert result == './make_pcluster.py -N mycluster -O rodney -A us-east-1a'

    def test_returns_last_matching_line_when_multiple(self, tmp_path):
        f = tmp_path / 'cluster.serial'
        f.write_text(
            'mycluster-00305910072026\n'
            './make_pcluster.py -N mycluster -O rodney -A us-east-1a\n'
            'ansible-playbook ...\n'
            './make_pcluster.py -N mycluster -O rodney -A us-east-1b\n'
        )
        result = _extract_rebuild_command(str(f))
        assert result == './make_pcluster.py -N mycluster -O rodney -A us-east-1b'

    def test_returns_none_when_no_matching_lines(self, tmp_path):
        f = tmp_path / 'cluster.serial'
        f.write_text('mycluster-00305910072026\nansible-playbook ...\n')
        assert _extract_rebuild_command(str(f)) is None

    def test_returns_none_for_missing_file(self, tmp_path):
        result = _extract_rebuild_command(str(tmp_path / 'nonexistent.serial'))
        assert result is None

    def test_matches_absolute_path_commands(self, tmp_path):
        f = tmp_path / 'cluster.serial'
        f.write_text(
            'mycluster-00305910072026\n'
            '/home/rodney/ParallelClusterMaker/make_pcluster.py -N mycluster\n'
        )
        result = _extract_rebuild_command(str(f))
        assert result == '/home/rodney/ParallelClusterMaker/make_pcluster.py -N mycluster'

    def test_strips_trailing_whitespace(self, tmp_path):
        f = tmp_path / 'cluster.serial'
        f.write_text('./make_pcluster.py -N mycluster   \n')
        result = _extract_rebuild_command(str(f))
        assert result == './make_pcluster.py -N mycluster'


# ---------------------------------------------------------------------------
# _resolve_access_script_path
# ---------------------------------------------------------------------------

class TestResolveAccessScriptPath:
    def test_valid_cluster_name_returns_expected_path(self, tmp_path):
        root = str(tmp_path / 'active_clusters')
        path = _resolve_access_script_path(root, 'mycluster')
        expected = os.path.join(root, 'mycluster', 'access_cluster.mycluster.sh')
        assert path == expected

    def test_path_contains_cluster_name_in_filename(self, tmp_path):
        root = str(tmp_path / 'active_clusters')
        path = _resolve_access_script_path(root, 'my-cluster-01')
        assert 'access_cluster.my-cluster-01.sh' in path

    def test_traversal_with_dotdot_raises(self, tmp_path):
        root = str(tmp_path / 'active_clusters')
        with pytest.raises(SystemExit):
            _resolve_access_script_path(root, '../etc/passwd')

    def test_traversal_with_nested_dotdot_raises(self, tmp_path):
        root = str(tmp_path / 'active_clusters')
        with pytest.raises(SystemExit):
            _resolve_access_script_path(root, 'good/../../etc/shadow')

    def test_result_is_under_root(self, tmp_path):
        root = str(tmp_path / 'active_clusters')
        path = _resolve_access_script_path(root, 'testcluster')
        assert path.startswith(root + os.sep)

    def test_hyphens_and_digits_in_name(self, tmp_path):
        root = str(tmp_path / 'active_clusters')
        path = _resolve_access_script_path(root, 'hpc-cluster-2a')
        assert path.endswith('access_cluster.hpc-cluster-2a.sh')
