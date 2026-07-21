"""
Unit tests for src/pcluster_aux_data.py pure data and logic.

Covers:
  - ARM instance detection (including the trn1/inf2 x86_64 edge case)
  - cluster_name regex boundary (27-char max, lowercase+digits+hyphens)
  - ctrlC_Abort: file cleanup, IAM cleanup, no-interrupt path, both-None path
  - illegal_az_msg, p_val, p_fail, print_TextHeader, refer_to_docs_and_quit
  - base_os_instance_check: ARM/x86 cross-check and trn1 edge case
"""
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

from pcluster_aux_data import base_os_efa  # noqa: F401 — import check
from pcluster_core import _validate_cluster_name


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_boto3(monkeypatch):
    """Insert a fake boto3 module so ctrlC_Abort can be imported without AWS creds."""
    fake_boto3 = types.ModuleType('boto3')
    fake_boto3.client = lambda *a, **kw: None
    monkeypatch.setitem(sys.modules, 'boto3', fake_boto3)


def _reload_aux(monkeypatch):
    import importlib
    import pcluster_aux_data as aux
    importlib.reload(aux)
    return aux


# ---------------------------------------------------------------------------
# ARM instance family detection
# ---------------------------------------------------------------------------

_ARM_FAMILIES = (
    'a1.', 'c6g', 'c7g', 'm6g', 'm7g', 'r6g', 'r7g', 'hpc7g',
    'g5g', 'im4gn', 'is4gen', 'i4g', 't4g', 'x2g',
)


def _is_arm(instance_type):
    return any(instance_type.startswith(f) for f in _ARM_FAMILIES)


class TestArmDetection:
    def test_graviton_instances_are_arm(self):
        for inst in ['c6g.large', 'm6g.xlarge', 'r6g.2xlarge', 'hpc7g.4xlarge',
                     'c7g.medium', 'm7g.8xlarge', 'r7g.16xlarge', 't4g.micro',
                     'g5g.xlarge', 'im4gn.large', 'is4gen.medium', 'a1.large']:
            assert _is_arm(inst), f"{inst} should be ARM"

    def test_x86_instances_are_not_arm(self):
        for inst in ['c5.large', 'm5.xlarge', 'r5.2xlarge', 'p3.2xlarge',
                     'g4dn.xlarge', 'hpc6a.48xlarge', 'c5n.18xlarge']:
            assert not _is_arm(inst), f"{inst} should NOT be ARM"

    def test_trn1_is_not_arm(self):
        # trn1 is Trainium 1 on Intel Xeon — x86_64, not ARM/Graviton.
        for inst in ['trn1.2xlarge', 'trn1.32xlarge', 'trn1n.32xlarge']:
            assert not _is_arm(inst), f"{inst} (Trainium) must NOT be ARM"

    def test_inf2_is_not_arm(self):
        # inf2 is Inferentia 2 on Intel Sapphire Rapids — x86_64, not ARM.
        for inst in ['inf2.xlarge', 'inf2.8xlarge', 'inf2.24xlarge', 'inf2.48xlarge']:
            assert not _is_arm(inst), f"{inst} (Inferentia 2) must NOT be ARM"

    def test_inf1_is_not_arm(self):
        for inst in ['inf1.xlarge', 'inf1.2xlarge', 'inf1.6xlarge', 'inf1.24xlarge']:
            assert not _is_arm(inst), f"{inst} (Inferentia 1) must NOT be ARM"


# ---------------------------------------------------------------------------
# cluster_name validation (via pcluster_core._validate_cluster_name)
# ---------------------------------------------------------------------------

class TestClusterNameViaCore:
    def test_valid_simple(self):
        _validate_cluster_name('mycluster')

    def test_valid_with_hyphens(self):
        _validate_cluster_name('my-cluster-01')

    def test_valid_single_char(self):
        _validate_cluster_name('a')

    def test_valid_exactly_27_chars(self):
        _validate_cluster_name('a' * 27)

    def test_digit_start_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_name('12345')

    def test_valid_mixed(self):
        _validate_cluster_name('pcluster-test-01')

    def test_invalid_28_chars_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_name('a' * 28)

    def test_invalid_uppercase_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_name('MyCluster')

    def test_invalid_leading_hyphen_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_name('-cluster')

    def test_invalid_trailing_hyphen_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_name('cluster-')

    def test_invalid_consecutive_hyphens_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_name('my--cluster')

    def test_invalid_underscore_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_name('my_cluster')

    def test_invalid_space_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_name('my cluster')

    def test_invalid_empty_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_name('')


# ---------------------------------------------------------------------------
# illegal_az_msg
# ---------------------------------------------------------------------------

class TestIllegalAzMsg:
    def test_raises_systemexit(self, monkeypatch):
        _mock_boto3(monkeypatch)
        aux = _reload_aux(monkeypatch)
        with pytest.raises(SystemExit):
            aux.illegal_az_msg('us-east-1')

    def test_output_contains_az(self, monkeypatch, capsys):
        _mock_boto3(monkeypatch)
        aux = _reload_aux(monkeypatch)
        with pytest.raises(SystemExit):
            aux.illegal_az_msg('eu-west-99z')
        assert 'eu-west-99z' in capsys.readouterr().out


# ---------------------------------------------------------------------------
# p_val
# ---------------------------------------------------------------------------

class TestPVal:
    def test_prints_when_debug_true(self, monkeypatch, capsys):
        _mock_boto3(monkeypatch)
        aux = _reload_aux(monkeypatch)
        aux.p_val('region', True)
        assert 'region' in capsys.readouterr().out

    def test_silent_when_debug_false(self, monkeypatch, capsys):
        _mock_boto3(monkeypatch)
        aux = _reload_aux(monkeypatch)
        aux.p_val('region', False)
        assert capsys.readouterr().out == ''


# ---------------------------------------------------------------------------
# p_fail
# ---------------------------------------------------------------------------

class TestPFail:
    def test_missing_element_branch(self, monkeypatch, capsys):
        _mock_boto3(monkeypatch)
        aux = _reload_aux(monkeypatch)
        with pytest.raises(SystemExit):
            aux.p_fail('badval', 'scheduler', 'missing_element')
        out = capsys.readouterr().out
        assert 'badval' in out
        assert 'scheduler' in out

    def test_list_of_options_branch(self, monkeypatch, capsys):
        _mock_boto3(monkeypatch)
        aux = _reload_aux(monkeypatch)
        with pytest.raises(SystemExit):
            aux.p_fail('badval', 'scheduler', ['slurm', 'sge'])
        out = capsys.readouterr().out
        assert 'badval' in out
        assert 'slurm' in out


# ---------------------------------------------------------------------------
# print_TextHeader
# ---------------------------------------------------------------------------

class TestPrintTextHeader:
    def test_output_contains_cluster_name_and_header(self, monkeypatch, capsys):
        _mock_boto3(monkeypatch)
        aux = _reload_aux(monkeypatch)
        aux.print_TextHeader('mycluster', 'Validating parameters', 80)
        out = capsys.readouterr().out
        assert 'mycluster' in out
        assert 'Validating parameters' in out


# ---------------------------------------------------------------------------
# refer_to_docs_and_quit
# ---------------------------------------------------------------------------

class TestReferToDocsAndQuit:
    def test_raises_systemexit(self, monkeypatch):
        _mock_boto3(monkeypatch)
        aux = _reload_aux(monkeypatch)
        with pytest.raises(SystemExit):
            aux.refer_to_docs_and_quit('something went wrong')

    def test_error_message_in_output(self, monkeypatch, capsys):
        _mock_boto3(monkeypatch)
        aux = _reload_aux(monkeypatch)
        with pytest.raises(SystemExit):
            aux.refer_to_docs_and_quit('unique-error-xyz')
        assert 'unique-error-xyz' in capsys.readouterr().out


# ---------------------------------------------------------------------------
# base_os_instance_check
# ---------------------------------------------------------------------------

class TestBaseOsInstanceCheck:
    def test_arm_instance_with_x86_os_raises(self, monkeypatch):
        _mock_boto3(monkeypatch)
        aux = _reload_aux(monkeypatch)
        with pytest.raises(SystemExit):
            aux.base_os_instance_check('ubuntu2404', 'c6g.large', False)

    def test_x86_instance_with_x86_os_passes(self, monkeypatch):
        _mock_boto3(monkeypatch)
        aux = _reload_aux(monkeypatch)
        aux.base_os_instance_check('ubuntu2404', 'c5.xlarge', False)

    def test_arm_instance_with_arm_os_passes(self, monkeypatch):
        _mock_boto3(monkeypatch)
        aux = _reload_aux(monkeypatch)
        aux.base_os_instance_check('ubuntu2404arm', 'c6g.large', False)

    def test_trn1_with_x86_os_passes(self, monkeypatch):
        _mock_boto3(monkeypatch)
        aux = _reload_aux(monkeypatch)
        # trn1 is x86_64 despite the accelerator branding — must not raise
        aux.base_os_instance_check('ubuntu2404', 'trn1.2xlarge', False)


# ---------------------------------------------------------------------------
# ctrlC_Abort: file cleanup (no AWS calls, no sleep)
# ---------------------------------------------------------------------------

def test_ctrlC_abort_removes_existing_files(tmp_path, monkeypatch):
    """Files that exist are removed when CTRL-C is pressed."""
    import time
    _mock_boto3(monkeypatch)
    aux = _reload_aux(monkeypatch)

    serial_file = tmp_path / 'test.serial'
    vars_file = tmp_path / 'vars.yml'
    serial_file.write_text('test-serial')
    vars_file.write_text('vars: true')

    monkeypatch.setattr(time, 'sleep', lambda _: (_ for _ in ()).throw(KeyboardInterrupt()))

    with pytest.raises(SystemExit):
        aux.ctrlC_Abort(
            sleep_time=1,
            line_length=80,
            vars_file_path=str(vars_file),
            cluster_serial_number_file=str(serial_file),
            cluster_serial_number=None,
            enable_fsx_hydration='false',
        )

    assert not serial_file.exists()
    assert not vars_file.exists()


def test_ctrlC_abort_skips_missing_files(tmp_path, monkeypatch):
    """Missing file paths do not raise FileNotFoundError."""
    import time
    _mock_boto3(monkeypatch)
    aux = _reload_aux(monkeypatch)

    monkeypatch.setattr(time, 'sleep', lambda _: (_ for _ in ()).throw(KeyboardInterrupt()))

    with pytest.raises(SystemExit):
        aux.ctrlC_Abort(
            sleep_time=1,
            line_length=80,
            vars_file_path=str(tmp_path / 'nonexistent.yml'),
            cluster_serial_number_file=str(tmp_path / 'nonexistent.serial'),
            cluster_serial_number=None,
            enable_fsx_hydration='false',
        )


def test_ctrlC_abort_both_paths_none(monkeypatch, capsys):
    """Both paths None + serial None → no-orphan message, no IAM calls, exits 1."""
    import time
    _mock_boto3(monkeypatch)
    aux = _reload_aux(monkeypatch)

    monkeypatch.setattr(time, 'sleep', lambda _: (_ for _ in ()).throw(KeyboardInterrupt()))

    with pytest.raises(SystemExit):
        aux.ctrlC_Abort(
            sleep_time=1,
            line_length=80,
            vars_file_path=None,
            cluster_serial_number_file=None,
            cluster_serial_number=None,
            enable_fsx_hydration='false',
        )
    out = capsys.readouterr().out
    assert 'No orphaned files' in out
    assert 'No IAM roles' in out


def test_ctrlC_abort_no_interrupt_returns(monkeypatch):
    """When sleep completes without interrupt the function returns (no SystemExit)."""
    import time
    _mock_boto3(monkeypatch)
    aux = _reload_aux(monkeypatch)

    monkeypatch.setattr(time, 'sleep', lambda _: None)

    # No exception expected
    aux.ctrlC_Abort(
        sleep_time=1,
        line_length=80,
        vars_file_path=None,
        cluster_serial_number_file=None,
        cluster_serial_number=None,
        enable_fsx_hydration='false',
    )


# ---------------------------------------------------------------------------
# ctrlC_Abort: IAM cleanup (mocked boto3 client)
# ---------------------------------------------------------------------------

class _FakeIAM:
    def __init__(self):
        self.deleted_role_policies = []
        self.deleted_roles = []

    def delete_role_policy(self, RoleName, PolicyName):
        self.deleted_role_policies.append((RoleName, PolicyName))

    def delete_role(self, RoleName):
        self.deleted_roles.append(RoleName)


def _make_boto3_with_iam(iam_client, monkeypatch):
    fake_boto3 = types.ModuleType('boto3')
    fake_boto3.client = lambda service, **kw: iam_client if service == 'iam' else None
    monkeypatch.setitem(sys.modules, 'boto3', fake_boto3)


def test_ctrlC_abort_iam_cleanup_no_fsx(monkeypatch):
    """With a serial number and no FSx, role and ec2 policy are deleted."""
    import time
    iam = _FakeIAM()
    _make_boto3_with_iam(iam, monkeypatch)
    aux = _reload_aux(monkeypatch)

    monkeypatch.setattr(time, 'sleep', lambda _: (_ for _ in ()).throw(KeyboardInterrupt()))

    with pytest.raises(SystemExit):
        aux.ctrlC_Abort(
            sleep_time=1,
            line_length=80,
            vars_file_path=None,
            cluster_serial_number_file=None,
            cluster_serial_number='abc123',
            enable_fsx_hydration='false',
        )

    assert ('pclustermaker-role-abc123', 'pclustermaker-policy-abc123') in iam.deleted_role_policies
    assert 'pclustermaker-role-abc123' in iam.deleted_roles
    assert not any('fsx' in p for _, p in iam.deleted_role_policies)


def test_ctrlC_abort_iam_cleanup_with_fsx(monkeypatch):
    """With FSx hydration enabled, the FSx policy is deleted first."""
    import time
    iam = _FakeIAM()
    _make_boto3_with_iam(iam, monkeypatch)
    aux = _reload_aux(monkeypatch)

    monkeypatch.setattr(time, 'sleep', lambda _: (_ for _ in ()).throw(KeyboardInterrupt()))

    with pytest.raises(SystemExit):
        aux.ctrlC_Abort(
            sleep_time=1,
            line_length=80,
            vars_file_path=None,
            cluster_serial_number_file=None,
            cluster_serial_number='abc123',
            enable_fsx_hydration='true',
        )

    policy_names = [p for _, p in iam.deleted_role_policies]
    assert 'pclustermaker-fsx-s3-policy-abc123' in policy_names
    assert 'pclustermaker-policy-abc123' in policy_names
    assert 'pclustermaker-role-abc123' in iam.deleted_roles


def test_ctrlC_abort_iam_no_such_entity_is_graceful(monkeypatch, capsys):
    """NoSuchEntity IAM error prints a warning and still exits 1."""
    import time

    class _BrokenIAM:
        def delete_role_policy(self, **kw):
            raise Exception('NoSuchEntityException: NoSuchEntity')
        def delete_role(self, **kw):
            raise Exception('NoSuchEntityException: NoSuchEntity')

    _make_boto3_with_iam(_BrokenIAM(), monkeypatch)
    aux = _reload_aux(monkeypatch)

    monkeypatch.setattr(time, 'sleep', lambda _: (_ for _ in ()).throw(KeyboardInterrupt()))

    with pytest.raises(SystemExit):
        aux.ctrlC_Abort(
            sleep_time=1,
            line_length=80,
            vars_file_path=None,
            cluster_serial_number_file=None,
            cluster_serial_number='abc123',
            enable_fsx_hydration='false',
        )
    out = capsys.readouterr().out
    assert 'not found' in out or 'skipping' in out
