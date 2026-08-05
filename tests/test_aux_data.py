"""
Unit tests for src/pcluster_aux_data.py pure data and logic.

Covers:
  - ARM instance detection (including the trn1/inf2 x86_64 edge case)
  - GPU instance detection and EFA-GDR detection
  - cluster_name regex boundary (27-char max, lowercase+digits+hyphens)
  - ctrlC_Abort: file cleanup, IAM cleanup, no-interrupt path, both-None path
  - illegal_az_msg, p_val, p_fail, print_TextHeader, refer_to_docs_and_quit
  - base_os_instance_check: ARM/x86 cross-check and trn1 edge case
"""

import os
import sys
import types

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
)

from pcluster_aux_data import (  # noqa: F401
    base_os_efa,
    is_arm_instance,
    is_gpu_instance,
    needs_efa_gdr,
    derive_ranks_per_node,
    nvidia_gpu_count,
    parse_instance_type_list,
    usable_vcpu_count,
)
from pcluster_core import _validate_cluster_name

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_boto3(monkeypatch):
    """Insert a fake boto3 module so ctrlC_Abort can be imported without AWS creds."""
    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.client = lambda *a, **kw: None
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)


def _reload_aux(monkeypatch):
    import importlib
    import pcluster_aux_data as aux

    importlib.reload(aux)
    return aux


# ---------------------------------------------------------------------------
# ARM instance family detection
# ---------------------------------------------------------------------------

# Test production directly. A local copy of the family tuple used to live here
# and had already drifted — it was missing c8g, the repo's own default instance
# family — so these tests passed while the real guard was untested.
_is_arm = is_arm_instance


class TestArmDetection:
    def test_graviton_instances_are_arm(self):
        for inst in [
            "c6g.large",
            "m6g.xlarge",
            "r6g.2xlarge",
            "hpc7g.4xlarge",
            "c7g.medium",
            "m7g.8xlarge",
            "r7g.16xlarge",
            "t4g.micro",
            "g5g.xlarge",
            "im4gn.large",
            "is4gen.medium",
            "a1.large",
        ]:
            assert _is_arm(inst), f"{inst} should be ARM"

    def test_repo_default_instance_families_are_arm(self):
        """c8g is the repo's default head node and compute family. Dropping it
        from ARM_FAMILIES silently disables the base_os mismatch guard for the
        default config, which is exactly what the old test-local copy missed."""
        for inst in ["c8g.xlarge", "c8g.2xlarge", "c8g.metal-24xl"]:
            assert _is_arm(inst), f"{inst} should be ARM"

    def test_no_duplicate_arm_family_definitions(self):
        """Three copies of this tuple existed and one had drifted. Production
        code must import ARM_FAMILIES, not redefine it."""
        import re as _re

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        offenders = []
        for rel in ["src/pcluster_aux_data.py", "src/pcluster_queue_editor.py",
                    "tests/test_aux_data.py", "make_pcluster.py"]:
            with open(os.path.join(repo, rel)) as fh:
                body = fh.read()
            # A literal tuple assignment containing Graviton prefixes.
            for m in _re.finditer(r"^_?ARM_FAMILIES\s*=\s*\(", body, _re.M):
                offenders.append(rel)
        assert offenders == ["src/pcluster_aux_data.py"], (
            f"ARM_FAMILIES must be defined once in src/pcluster_aux_data.py; "
            f"found definitions in: {offenders}"
        )

    def test_x86_instances_are_not_arm(self):
        for inst in [
            "c5.large",
            "m5.xlarge",
            "r5.2xlarge",
            "p3.2xlarge",
            "g4dn.xlarge",
            "hpc6a.48xlarge",
            "c5n.18xlarge",
        ]:
            assert not _is_arm(inst), f"{inst} should NOT be ARM"

    def test_trn1_is_not_arm(self):
        # trn1 is Trainium 1 on Intel Xeon — x86_64, not ARM/Graviton.
        for inst in ["trn1.2xlarge", "trn1.32xlarge", "trn1n.32xlarge"]:
            assert not _is_arm(inst), f"{inst} (Trainium) must NOT be ARM"

    def test_inf2_is_not_arm(self):
        # inf2 is Inferentia 2 on Intel Sapphire Rapids — x86_64, not ARM.
        for inst in ["inf2.xlarge", "inf2.8xlarge", "inf2.24xlarge", "inf2.48xlarge"]:
            assert not _is_arm(inst), f"{inst} (Inferentia 2) must NOT be ARM"

    def test_inf1_is_not_arm(self):
        for inst in ["inf1.xlarge", "inf1.2xlarge", "inf1.6xlarge", "inf1.24xlarge"]:
            assert not _is_arm(inst), f"{inst} (Inferentia 1) must NOT be ARM"


# ---------------------------------------------------------------------------
# cluster_name validation (via pcluster_core._validate_cluster_name)
# ---------------------------------------------------------------------------


class TestClusterNameViaCore:
    def test_valid_simple(self):
        _validate_cluster_name("mycluster")

    def test_valid_with_hyphens(self):
        _validate_cluster_name("my-cluster-01")

    def test_valid_single_char(self):
        _validate_cluster_name("a")

    def test_valid_exactly_27_chars(self):
        _validate_cluster_name("a" * 27)

    def test_digit_start_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_name("12345")

    def test_valid_mixed(self):
        _validate_cluster_name("pcluster-test-01")

    def test_invalid_28_chars_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_name("a" * 28)

    def test_invalid_uppercase_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_name("MyCluster")

    def test_invalid_leading_hyphen_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_name("-cluster")

    def test_invalid_trailing_hyphen_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_name("cluster-")

    def test_invalid_consecutive_hyphens_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_name("my--cluster")

    def test_invalid_underscore_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_name("my_cluster")

    def test_invalid_space_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_name("my cluster")

    def test_invalid_empty_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_name("")


# ---------------------------------------------------------------------------
# illegal_az_msg
# ---------------------------------------------------------------------------


class TestIllegalAzMsg:
    def test_raises_systemexit(self, monkeypatch):
        _mock_boto3(monkeypatch)
        aux = _reload_aux(monkeypatch)
        with pytest.raises(SystemExit):
            aux.illegal_az_msg("us-east-1")

    def test_output_contains_az(self, monkeypatch, capsys):
        _mock_boto3(monkeypatch)
        aux = _reload_aux(monkeypatch)
        with pytest.raises(SystemExit):
            aux.illegal_az_msg("eu-west-99z")
        assert "eu-west-99z" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# p_val
# ---------------------------------------------------------------------------


class TestPVal:
    def test_prints_when_debug_true(self, monkeypatch, capsys):
        _mock_boto3(monkeypatch)
        aux = _reload_aux(monkeypatch)
        aux.p_val("region", True)
        assert "region" in capsys.readouterr().out

    def test_silent_when_debug_false(self, monkeypatch, capsys):
        _mock_boto3(monkeypatch)
        aux = _reload_aux(monkeypatch)
        aux.p_val("region", False)
        assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# p_fail
# ---------------------------------------------------------------------------


class TestPFail:
    def test_missing_element_branch(self, monkeypatch, capsys):
        _mock_boto3(monkeypatch)
        aux = _reload_aux(monkeypatch)
        with pytest.raises(SystemExit):
            aux.p_fail("badval", "scheduler", "missing_element")
        out = capsys.readouterr().out
        assert "badval" in out
        assert "scheduler" in out

    def test_list_of_options_branch(self, monkeypatch, capsys):
        _mock_boto3(monkeypatch)
        aux = _reload_aux(monkeypatch)
        with pytest.raises(SystemExit):
            aux.p_fail("badval", "scheduler", ["slurm", "sge"])
        out = capsys.readouterr().out
        assert "badval" in out
        assert "slurm" in out


# ---------------------------------------------------------------------------
# print_TextHeader
# ---------------------------------------------------------------------------


class TestPrintTextHeader:
    def test_output_contains_cluster_name_and_header(self, monkeypatch, capsys):
        _mock_boto3(monkeypatch)
        aux = _reload_aux(monkeypatch)
        aux.print_TextHeader("mycluster", "Validating parameters", 80)
        out = capsys.readouterr().out
        assert "mycluster" in out
        assert "Validating parameters" in out


# ---------------------------------------------------------------------------
# refer_to_docs_and_quit
# ---------------------------------------------------------------------------


class TestReferToDocsAndQuit:
    def test_raises_systemexit(self, monkeypatch):
        _mock_boto3(monkeypatch)
        aux = _reload_aux(monkeypatch)
        with pytest.raises(SystemExit):
            aux.refer_to_docs_and_quit("something went wrong")

    def test_error_message_in_output(self, monkeypatch, capsys):
        _mock_boto3(monkeypatch)
        aux = _reload_aux(monkeypatch)
        with pytest.raises(SystemExit):
            aux.refer_to_docs_and_quit("unique-error-xyz")
        assert "unique-error-xyz" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# base_os_instance_check
# ---------------------------------------------------------------------------


class TestBaseOsInstanceCheck:
    def test_arm_instance_with_x86_os_raises(self, monkeypatch):
        _mock_boto3(monkeypatch)
        aux = _reload_aux(monkeypatch)
        with pytest.raises(SystemExit):
            aux.base_os_instance_check("ubuntu2404", "c6g.large", False)

    def test_x86_instance_with_x86_os_passes(self, monkeypatch):
        _mock_boto3(monkeypatch)
        aux = _reload_aux(monkeypatch)
        aux.base_os_instance_check("ubuntu2404", "c5.xlarge", False)

    def test_arm_instance_with_arm_os_passes(self, monkeypatch):
        _mock_boto3(monkeypatch)
        aux = _reload_aux(monkeypatch)
        aux.base_os_instance_check("ubuntu2404arm", "c6g.large", False)

    def test_trn1_with_x86_os_passes(self, monkeypatch):
        _mock_boto3(monkeypatch)
        aux = _reload_aux(monkeypatch)
        # trn1 is x86_64 despite the accelerator branding — must not raise
        aux.base_os_instance_check("ubuntu2404", "trn1.2xlarge", False)

    @pytest.mark.parametrize(
        "base_os,instance_type",
        [
            ("ubuntu2404arm", "c5.xlarge"),
            ("ubuntu2204arm", "m5.large"),
            ("ubuntu2204arm", "c6i.4xlarge"),
            ("ubuntu2404arm", "trn1.2xlarge"),
            ("ubuntu2204arm", "inf2.xlarge"),
        ],
    )
    def test_x86_instance_with_arm_os_raises(self, monkeypatch, base_os, instance_type):
        _mock_boto3(monkeypatch)
        aux = _reload_aux(monkeypatch)
        with pytest.raises(SystemExit):
            aux.base_os_instance_check(base_os, instance_type, False)

    @pytest.mark.parametrize(
        "base_os,instance_type",
        [
            ("ubuntu2404arm", "c8g.xlarge"),
            ("ubuntu2204arm", "m7g.2xlarge"),
            ("ubuntu2204arm", "hpc7g.4xlarge"),
            ("ubuntu2404", "c5.xlarge"),
            ("ubuntu2204", "inf2.xlarge"),
        ],
    )
    def test_matching_architecture_passes(self, monkeypatch, base_os, instance_type):
        _mock_boto3(monkeypatch)
        aux = _reload_aux(monkeypatch)
        aux.base_os_instance_check(base_os, instance_type, False)

    def test_arm_os_error_suggests_x86_equivalent(self, monkeypatch, capsys):
        _mock_boto3(monkeypatch)
        aux = _reload_aux(monkeypatch)
        with pytest.raises(SystemExit):
            aux.base_os_instance_check("ubuntu2404arm", "c5.xlarge", False)
        assert "ubuntu2404" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# ctrlC_Abort: file cleanup (no AWS calls, no sleep)
# ---------------------------------------------------------------------------


def test_ctrlC_abort_removes_existing_files(tmp_path, monkeypatch):
    """Files that exist are removed when CTRL-C is pressed."""
    import time

    _mock_boto3(monkeypatch)
    aux = _reload_aux(monkeypatch)

    serial_file = tmp_path / "test.serial"
    vars_file = tmp_path / "vars.yml"
    serial_file.write_text("test-serial")
    vars_file.write_text("vars: true")

    monkeypatch.setattr(
        time, "sleep", lambda _: (_ for _ in ()).throw(KeyboardInterrupt())
    )

    with pytest.raises(SystemExit):
        aux.ctrlC_Abort(
            sleep_time=1,
            line_length=80,
            vars_file_path=str(vars_file),
            cluster_serial_number_file=str(serial_file),
            cluster_serial_number=None,
            enable_fsx_hydration="false",
        )

    assert not serial_file.exists()
    assert not vars_file.exists()


def test_ctrlC_abort_skips_missing_files(tmp_path, monkeypatch):
    """Missing file paths do not raise FileNotFoundError."""
    import time

    _mock_boto3(monkeypatch)
    aux = _reload_aux(monkeypatch)

    monkeypatch.setattr(
        time, "sleep", lambda _: (_ for _ in ()).throw(KeyboardInterrupt())
    )

    with pytest.raises(SystemExit):
        aux.ctrlC_Abort(
            sleep_time=1,
            line_length=80,
            vars_file_path=str(tmp_path / "nonexistent.yml"),
            cluster_serial_number_file=str(tmp_path / "nonexistent.serial"),
            cluster_serial_number=None,
            enable_fsx_hydration="false",
        )


def test_ctrlC_abort_both_paths_none(monkeypatch, capsys):
    """Both paths None + serial None → no-orphan message, no IAM calls, exits 1."""
    import time

    _mock_boto3(monkeypatch)
    aux = _reload_aux(monkeypatch)

    monkeypatch.setattr(
        time, "sleep", lambda _: (_ for _ in ()).throw(KeyboardInterrupt())
    )

    with pytest.raises(SystemExit):
        aux.ctrlC_Abort(
            sleep_time=1,
            line_length=80,
            vars_file_path=None,
            cluster_serial_number_file=None,
            cluster_serial_number=None,
            enable_fsx_hydration="false",
        )
    out = capsys.readouterr().out
    assert "No orphaned files" in out
    assert "No IAM roles" in out


def test_ctrlC_abort_no_interrupt_returns(monkeypatch):
    """When sleep completes without interrupt the function returns (no SystemExit)."""
    import time

    _mock_boto3(monkeypatch)
    aux = _reload_aux(monkeypatch)

    monkeypatch.setattr(time, "sleep", lambda _: None)

    # No exception expected
    aux.ctrlC_Abort(
        sleep_time=1,
        line_length=80,
        vars_file_path=None,
        cluster_serial_number_file=None,
        cluster_serial_number=None,
        enable_fsx_hydration="false",
    )


# ---------------------------------------------------------------------------
# ctrlC_Abort: IAM cleanup (mocked boto3 client)
# ---------------------------------------------------------------------------


class _FakeIAM:
    def __init__(self):
        self.detached_policies = []
        self.deleted_policies = []
        self.deleted_role_policies = []
        self.deleted_roles = []

    def detach_role_policy(self, RoleName, PolicyArn):
        self.detached_policies.append((RoleName, PolicyArn))

    def delete_policy(self, PolicyArn):
        self.deleted_policies.append(PolicyArn)

    def delete_role_policy(self, RoleName, PolicyName):
        self.deleted_role_policies.append((RoleName, PolicyName))

    def delete_role(self, RoleName):
        self.deleted_roles.append(RoleName)


def _make_boto3_with_iam(iam_client, monkeypatch):
    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.client = lambda service, **kw: iam_client if service == "iam" else None
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)


def test_ctrlC_abort_iam_cleanup_no_fsx(monkeypatch):
    """With a serial number and no FSx, the current managed policies and role are deleted."""
    import time

    iam = _FakeIAM()
    _make_boto3_with_iam(iam, monkeypatch)
    aux = _reload_aux(monkeypatch)

    monkeypatch.setattr(
        time, "sleep", lambda _: (_ for _ in ()).throw(KeyboardInterrupt())
    )

    with pytest.raises(SystemExit):
        aux.ctrlC_Abort(
            sleep_time=1,
            line_length=80,
            vars_file_path=None,
            cluster_serial_number_file=None,
            cluster_serial_number="abc123",
            enable_fsx_hydration="false",
            aws_account_id="123456789012",
        )

    deleted_arns = iam.deleted_policies
    assert any("pclustermaker-policy-abc123-HeadNode-Compute" in a for a in deleted_arns)
    assert any("pclustermaker-policy-abc123-HeadNode-Storage" in a for a in deleted_arns)
    assert any("pclustermaker-policy-abc123-HeadNode-IAM" in a for a in deleted_arns)
    assert any("pclustermaker-policy-abc123-ComputeNode-Base" in a for a in deleted_arns)
    assert not any("HeadNode-Monitoring" in a for a in deleted_arns)
    assert "pclustermaker-role-abc123" in iam.deleted_roles
    assert not any("fsx" in p for _, p in iam.deleted_role_policies)


def test_ctrlC_abort_iam_cleanup_with_fsx_and_monitoring(monkeypatch):
    """With FSx hydration and monitoring enabled, the FSx inline policy, the monitoring
    managed policy, and the base managed policies are all deleted."""
    import time

    iam = _FakeIAM()
    _make_boto3_with_iam(iam, monkeypatch)
    aux = _reload_aux(monkeypatch)

    monkeypatch.setattr(
        time, "sleep", lambda _: (_ for _ in ()).throw(KeyboardInterrupt())
    )

    with pytest.raises(SystemExit):
        aux.ctrlC_Abort(
            sleep_time=1,
            line_length=80,
            vars_file_path=None,
            cluster_serial_number_file=None,
            cluster_serial_number="abc123",
            enable_fsx_hydration="true",
            enable_monitoring=True,
            aws_account_id="123456789012",
        )

    deleted_arns = iam.deleted_policies
    assert any("pclustermaker-policy-abc123-HeadNode-Compute" in a for a in deleted_arns)
    assert any("pclustermaker-policy-abc123-HeadNode-Storage" in a for a in deleted_arns)
    assert any("pclustermaker-policy-abc123-HeadNode-IAM" in a for a in deleted_arns)
    assert any("pclustermaker-policy-abc123-ComputeNode-Base" in a for a in deleted_arns)
    assert any("pclustermaker-policy-abc123-HeadNode-Monitoring" in a for a in deleted_arns)
    fsx_policy_names = [p for _, p in iam.deleted_role_policies]
    assert "pclustermaker-fsx-s3-policy-abc123" in fsx_policy_names
    assert "pclustermaker-role-abc123" in iam.deleted_roles


def test_ctrlC_abort_iam_no_account_id_skips_policy_cleanup_but_deletes_role(monkeypatch):
    """Without aws_account_id, managed-policy cleanup is skipped but role deletion still runs."""
    import time

    iam = _FakeIAM()
    _make_boto3_with_iam(iam, monkeypatch)
    aux = _reload_aux(monkeypatch)

    monkeypatch.setattr(
        time, "sleep", lambda _: (_ for _ in ()).throw(KeyboardInterrupt())
    )

    with pytest.raises(SystemExit):
        aux.ctrlC_Abort(
            sleep_time=1,
            line_length=80,
            vars_file_path=None,
            cluster_serial_number_file=None,
            cluster_serial_number="abc123",
            enable_fsx_hydration="false",
        )

    assert iam.deleted_policies == []
    assert "pclustermaker-role-abc123" in iam.deleted_roles


def test_ctrlC_abort_iam_no_such_entity_is_graceful(monkeypatch, capsys):
    """NoSuchEntity IAM error prints a warning and still exits 1."""
    import time

    class _BrokenIAM:
        def detach_role_policy(self, **kw):
            raise Exception("NoSuchEntityException: NoSuchEntity")

        def delete_policy(self, **kw):
            raise Exception("NoSuchEntityException: NoSuchEntity")

        def delete_role_policy(self, **kw):
            raise Exception("NoSuchEntityException: NoSuchEntity")

        def delete_role(self, **kw):
            raise Exception("NoSuchEntityException: NoSuchEntity")

    _make_boto3_with_iam(_BrokenIAM(), monkeypatch)
    aux = _reload_aux(monkeypatch)

    monkeypatch.setattr(
        time, "sleep", lambda _: (_ for _ in ()).throw(KeyboardInterrupt())
    )

    with pytest.raises(SystemExit):
        aux.ctrlC_Abort(
            sleep_time=1,
            line_length=80,
            vars_file_path=None,
            cluster_serial_number_file=None,
            cluster_serial_number="abc123",
            enable_fsx_hydration="false",
        )
    out = capsys.readouterr().out
    assert "not found" in out or "skipping" in out


# ---------------------------------------------------------------------------
# GPU instance detection
# ---------------------------------------------------------------------------


class TestIsGpuInstance:
    @pytest.mark.parametrize("itype", [
        "g4dn.xlarge", "g4dn.12xlarge", "g4dn.metal",
        "g4ad.xlarge", "g4ad.16xlarge",
        "g5.xlarge", "g5.48xlarge",
        "g5g.xlarge", "g5g.metal",
        "g6.xlarge", "g6.48xlarge",
        "p3.2xlarge", "p3.16xlarge", "p3dn.24xlarge",
        "p4d.24xlarge", "p4de.24xlarge",
        "p5.48xlarge",
    ])
    def test_gpu_instances_detected(self, itype):
        assert is_gpu_instance(itype)

    @pytest.mark.parametrize("itype", [
        "c8g.xlarge", "m7i.large", "r6i.2xlarge",
        "trn1.32xlarge", "inf2.48xlarge",
        "hpc7g.16xlarge", "i4i.32xlarge",
    ])
    def test_non_gpu_instances_not_detected(self, itype):
        assert not is_gpu_instance(itype)


class TestNeedsEfaGdr:
    @pytest.mark.parametrize("itype", [
        "p4d.24xlarge", "p4de.24xlarge", "p5.48xlarge",
    ])
    def test_gdr_instances_detected(self, itype):
        assert needs_efa_gdr(itype)

    @pytest.mark.parametrize("itype", [
        "g4dn.12xlarge", "g5.48xlarge", "p3.16xlarge", "c8g.xlarge",
    ])
    def test_non_gdr_instances_not_detected(self, itype):
        assert not needs_efa_gdr(itype)


class TestNvidiaGpuCount:
    """Shapes are taken verbatim from ec2:DescribeInstanceTypes responses.

    The count feeds --ntasks-per-node in the rendered benchmark job script, so an
    overcount oversubscribes ranks against devices that do not exist and an
    undercount leaves GPUs idle.
    """

    def test_a_single_nvidia_gpu(self):
        info = {
            "InstanceType": "p3.2xlarge",
            "GpuInfo": {"Gpus": [{"Name": "V100", "Manufacturer": "NVIDIA", "Count": 1}]},
        }
        assert nvidia_gpu_count(info) == 1

    def test_all_gpus_on_a_multi_gpu_instance(self):
        info = {
            "InstanceType": "p4d.24xlarge",
            "GpuInfo": {"Gpus": [{"Name": "A100", "Manufacturer": "NVIDIA", "Count": 8}]},
        }
        assert nvidia_gpu_count(info) == 8

    def test_amd_gpus_are_not_counted(self):
        """g4ad carries Radeon Pro V520. Neither Slurm GRES nor the CUDA runtime
        sees them, so counting them would shape ranks against unaddressable
        devices."""
        info = {
            "InstanceType": "g4ad.4xlarge",
            "GpuInfo": {"Gpus": [{"Name": "Radeon Pro V520", "Manufacturer": "AMD", "Count": 1}]},
        }
        assert nvidia_gpu_count(info) == 0

    def test_mixed_manufacturers_count_only_nvidia(self):
        info = {
            "GpuInfo": {
                "Gpus": [
                    {"Manufacturer": "AMD", "Count": 2},
                    {"Manufacturer": "NVIDIA", "Count": 4},
                ]
            }
        }
        assert nvidia_gpu_count(info) == 4

    def test_manufacturer_casing_does_not_matter(self):
        info = {"GpuInfo": {"Gpus": [{"Manufacturer": "Nvidia", "Count": 2}]}}
        assert nvidia_gpu_count(info) == 2

    def test_a_cpu_instance_has_no_gpuinfo_key(self):
        assert nvidia_gpu_count({"InstanceType": "c8g.2xlarge"}) == 0

    def test_a_null_gpuinfo_value_is_zero(self):
        assert nvidia_gpu_count({"GpuInfo": None}) == 0

    def test_an_empty_gpu_list_is_zero(self):
        assert nvidia_gpu_count({"GpuInfo": {"Gpus": []}}) == 0

    def test_a_gpu_entry_without_a_count_is_zero(self):
        assert nvidia_gpu_count({"GpuInfo": {"Gpus": [{"Manufacturer": "NVIDIA"}]}}) == 0


class TestUsableVcpuCount:
    """The default Slurm submission script's --ntasks came from two hardcoded
    ladders over eleven instance-size suffixes each, whose {% else %} bodies were
    themselves commented out -- so every unlisted size (c8g.medium, any 16xlarge,
    most metal variants) emitted no --ntasks at all and Slurm ran the job on one
    task. This reads EC2's own DefaultVCpus instead."""

    def test_hyperthreaded_returns_every_vcpu(self):
        info = {"VCpuInfo": {"DefaultVCpus": 72, "DefaultThreadsPerCore": 2}}
        assert usable_vcpu_count(info, hyperthreading=True) == 72

    def test_disabling_smt_halves_a_two_thread_instance(self):
        """DisableSimultaneousMultithreading is what config.pcluster.j2 sets when
        hyperthreading is false; the node then presents one thread per core, and
        requesting the full vCPU count leaves the job pending forever."""
        info = {"VCpuInfo": {"DefaultVCpus": 72, "DefaultThreadsPerCore": 2}}
        assert usable_vcpu_count(info, hyperthreading=False) == 36

    def test_graviton_is_not_halved(self):
        """Graviton reports one thread per core, and upstream only acts on the
        SMT flag when default_threads_per_core() > 1 (cluster_config.py:1523).
        Halving unconditionally would request half the cores of every ARM node."""
        info = {"VCpuInfo": {"DefaultVCpus": 8, "DefaultThreadsPerCore": 1}}
        assert usable_vcpu_count(info, hyperthreading=False) == 8
        assert usable_vcpu_count(info, hyperthreading=True) == 8

    def test_a_four_thread_instance_divides_by_four(self):
        info = {"VCpuInfo": {"DefaultVCpus": 128, "DefaultThreadsPerCore": 4}}
        assert usable_vcpu_count(info, hyperthreading=False) == 32

    def test_a_missing_threads_field_is_not_a_division_by_zero(self):
        info = {"VCpuInfo": {"DefaultVCpus": 16}}
        assert usable_vcpu_count(info, hyperthreading=False) == 16

    def test_a_missing_vcpuinfo_is_zero(self):
        assert usable_vcpu_count({"InstanceType": "c8g.2xlarge"}, hyperthreading=True) == 0

    def test_a_null_vcpuinfo_is_zero(self):
        assert usable_vcpu_count({"VCpuInfo": None}, hyperthreading=False) == 0

    def test_hyperthreading_is_keyword_only(self):
        """Two arguments and the second is a bool: a positional call reads as
        `usable_vcpu_count(info, True)`, which says nothing about which way."""
        import inspect

        params = inspect.signature(usable_vcpu_count).parameters
        assert params["hyperthreading"].kind is inspect.Parameter.KEYWORD_ONLY


class TestDeriveRanksPerNode:
    def test_an_empty_queue_is_zero(self):
        assert derive_ranks_per_node(instance_types=[], vcpu_map={}) == 0

    def test_a_single_type_gets_its_own_count(self):
        assert derive_ranks_per_node(
            instance_types=["c8g.2xlarge"], vcpu_map={"c8g.2xlarge": 8}
        ) == 8

    def test_a_mixed_queue_takes_the_minimum(self):
        """A queue may hold several instance types, and only the smallest one's
        count fits on every node -- a job shaped to the largest goes pending when
        the fleet hands it a smaller one."""
        assert derive_ranks_per_node(
            instance_types=["c8g.8xlarge", "c8g.2xlarge", "c8g.4xlarge"],
            vcpu_map={"c8g.8xlarge": 32, "c8g.2xlarge": 8, "c8g.4xlarge": 16},
        ) == 8

    def test_a_type_missing_from_the_map_floors_at_one_not_zero(self):
        """`sbatch --ntasks=0` is rejected outright, so an instance type EC2
        answered without VCpuInfo must not render an unsubmittable script."""
        assert derive_ranks_per_node(
            instance_types=["c8g.2xlarge", "future.type"],
            vcpu_map={"c8g.2xlarge": 8},
        ) == 1

    def test_the_signature_is_keyword_only(self):
        import inspect

        params = inspect.signature(derive_ranks_per_node).parameters
        positional = [
            name
            for name, p in params.items()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        assert not positional, f"{positional} can be passed positionally"

    def test_make_pcluster_derives_both_queues_from_the_same_response(self):
        """Both call sites must read the map the architecture check already built.
        A second describe_instance_types sweep would double the API cost of every
        build for data already in hand."""
        import ast
        import os

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(repo_root, "make_pcluster.py")) as fh:
            tree = ast.parse(fh.read())
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "derive_ranks_per_node"
        ]
        assert len(calls) == 2, f"expected two queue derivations, found {len(calls)}"
        for call in calls:
            assert not call.args, "call site passes a positional argument"
            passed = {kw.arg for kw in call.keywords}
            assert passed == {"instance_types", "vcpu_map"}, sorted(passed)

        # Keyword-only defends against transposing instance_types and vcpu_map,
        # which are different shapes. It cannot defend against handing the CPU
        # queue's derivation the GPU list: both are lists of instance types, so
        # the swap renders a plausible script that puts a GPU-shaped rank count
        # on the compute partition. Pin each assignment to its own queue.
        assigned = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or node.value not in calls:
                continue
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            queues = [
                kw.value.id
                for kw in node.value.keywords
                if kw.arg == "instance_types" and isinstance(kw.value, ast.Name)
            ]
            assert len(targets) == 1 and len(queues) == 1, ast.dump(node)
            assigned[targets[0]] = queues[0]
        assert assigned == {
            "cpu_ranks_per_node": "cpu_instance_types",
            "gpu_vcpus_per_node": "gpu_instance_types",
        }, assigned
        described = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "attr", None) == "describe_instance_types"
        ]
        assert len(described) == 1, (
            f"{len(described)} describe_instance_types calls -- the vCPU counts must "
            f"come out of the response the architecture check already fetches"
        )


class TestParseInstanceTypeList:
    def test_single_type(self):
        assert parse_instance_type_list("c8g.2xlarge") == ["c8g.2xlarge"]

    def test_multiple_types_with_spaces(self):
        assert parse_instance_type_list("c8g.2xlarge, c7g.2xlarge, c6g.2xlarge") == [
            "c8g.2xlarge", "c7g.2xlarge", "c6g.2xlarge"
        ]

    def test_deduplication_preserves_order(self):
        assert parse_instance_type_list("c5.xlarge,c5.xlarge,c6i.xlarge") == [
            "c5.xlarge", "c6i.xlarge"
        ]

    def test_empty_string_returns_empty_list(self):
        assert parse_instance_type_list("") == []

    def test_none_returns_empty_list(self):
        assert parse_instance_type_list(None) == []

    def test_whitespace_only_returns_empty_list(self):
        assert parse_instance_type_list("   ") == []

    def test_trailing_comma_ignored(self):
        assert parse_instance_type_list("c5.xlarge,") == ["c5.xlarge"]

    def test_mixed_whitespace(self):
        assert parse_instance_type_list("  c5.xlarge ,  c6i.xlarge  ") == [
            "c5.xlarge", "c6i.xlarge"
        ]


class TestQueueTypeValidation:
    """Verify that is_gpu_instance correctly separates CPU and GPU queue types."""

    @pytest.mark.parametrize("itype", [
        "g4dn.xlarge", "g5.2xlarge", "p3.8xlarge", "p4d.24xlarge", "p5.48xlarge",
    ])
    def test_gpu_types_rejected_from_cpu_queue(self, itype):
        assert is_gpu_instance(itype), (
            f"{itype} should be detected as GPU and rejected from compute_instance_type"
        )

    @pytest.mark.parametrize("itype", [
        "c8g.2xlarge", "c7g.2xlarge", "c6g.2xlarge",
        "m7i.large", "r6i.2xlarge", "hpc7g.16xlarge",
        "trn1.32xlarge", "inf2.48xlarge",
    ])
    def test_non_gpu_types_allowed_in_cpu_queue(self, itype):
        assert not is_gpu_instance(itype), (
            f"{itype} incorrectly detected as GPU — should be allowed in compute_instance_type"
        )
