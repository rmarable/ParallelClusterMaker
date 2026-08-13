"""
Direct tests for make_pcluster.py's main().

main() is ~1900 lines and had no direct coverage: the pure validators it calls
were tested individually, but nothing checked the sequencing that makes a
failed build recoverable. The specific invariants pinned here are the ones a
refactor would silently break — that IAM is cleaned up when the build fails so
a retry is possible, that the abort window opens before Ansible runs, that an
existing vars file blocks a rebuild before any AWS call is made, and that
derived variables (pcluster_os, enable_efa_gdr) reach the playbook correctly.

Everything past argument parsing is stubbed at the AWS and subprocess
boundaries; the point is the control flow, not the API payloads.
"""

import json
import os
import sys

import pytest
import yaml
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from entrypoint_harness import REPO_ROOT, load_entrypoint

CLUSTER = "buildme"
OWNER = "testuser"
AZ = "us-east-1a"
SERIAL = "buildme-00001220260720"
DATESTAMP = "00001220260720"


def _arch_for(instance_type):
    """Graviton families end their family name in 'g'; everything else is x86."""
    family = instance_type.split(".")[0]
    return "arm64" if family.endswith("g") or family.endswith("gd") else "x86_64"


class _FakeEc2:
    def describe_availability_zones(self, ZoneNames=None, **kw):
        return {"AvailabilityZones": [{"RegionName": "us-east-1"}]}

    def describe_instance_types(self, InstanceTypes=None, **kw):
        return {
            "InstanceTypes": [
                {"InstanceType": t,
                 "ProcessorInfo": {"SupportedArchitectures": [_arch_for(t)]}}
                for t in (InstanceTypes or [])
            ]
        }

    def describe_images(self, **kw):
        return {"Images": [{"ImageId": "ami-0abc"}]}

    def describe_spot_price_history(self, InstanceTypes=None, **kw):
        return {
            "SpotPriceHistory": [
                {"InstanceType": t, "SpotPrice": "0.05",
                 "Timestamp": "2026-07-20T00:00:00Z"}
                for t in (InstanceTypes or [])
            ]
        }


class _FakeSts:
    def get_caller_identity(self):
        return {"Account": "123456789012"}


class _FakeIam:
    def __init__(self):
        self.deleted_roles = []

    def delete_role(self, RoleName):
        self.deleted_roles.append(RoleName)


class _FakeS3Object:
    def put(self, **kw):
        return {}


class _FakeS3Resource:
    def Object(self, bucket, key):
        return _FakeS3Object()


class _FakeS3Client:
    """head_bucket must 404: a bucket that already exists aborts the build."""

    def head_bucket(self, Bucket=None, **kw):
        raise ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadBucket"
        )


@pytest.fixture
def mp():
    return load_entrypoint("make_pcluster.py")


@pytest.fixture
def staged(mp, tmp_path, monkeypatch):
    """make_pcluster.main() with every AWS and subprocess boundary stubbed."""
    src = tmp_path / "src"
    (src / "vars_files").mkdir(parents=True)
    (tmp_path / "active_clusters").mkdir()
    # The real templates: main() renders vars_file.j2 under StrictUndefined, so
    # these runs also prove every cluster_parameters key the template needs is
    # actually present.
    (tmp_path / "templates").symlink_to(os.path.join(REPO_ROOT, "templates"))
    monkeypatch.setattr(mp, "_repo_root", str(tmp_path))
    monkeypatch.setattr(mp, "_src_dir", str(src))

    iam = _FakeIam()
    clients = {"ec2": _FakeEc2(), "sts": _FakeSts(), "iam": iam,
               "s3": _FakeS3Client(), "pricing": object()}
    monkeypatch.setattr(mp.boto3, "client",
                        lambda name, **kw: clients.get(name, object()))
    monkeypatch.setattr(mp.boto3, "resource", lambda *a, **k: _FakeS3Resource())

    record = {"calls": []}

    def _run(cmd, *args, **kwargs):
        record["calls"].append(list(cmd))
        if "--version" in cmd:
            return _Proc(0, "ansible [core 2.16.0]\n")
        if "describe-cluster" in cmd:
            # rc != 0 means "cluster does not exist yet", which is what a
            # fresh build requires.
            return _Proc(record.get("describe_rc", 1))
        if "ansible-playbook" in cmd:
            return _Proc(record.get("playbook_rc", 0))
        return _Proc(0)

    monkeypatch.setattr(mp.subprocess, "run", _run)
    monkeypatch.setattr(
        mp, "_validate_network",
        lambda *a, **k: ("vpc-0abc", "subnet-0head", ["subnet-0cpu"],
                         ["subnet-0gpu"], "10.0.0.0/16", "subnet-0login"),
    )
    monkeypatch.setattr(
        mp, "_load_or_create_serial",
        lambda data_dir, name: (
            os.path.join(str(tmp_path), "active_clusters", name,
                         name + ".serial"),
            SERIAL, DATESTAMP, True,
        ),
    )
    monkeypatch.setattr(mp, "_setup_iam", lambda *a, **k: None)
    monkeypatch.setattr(mp, "_get_od_price", lambda *a, **k: 0.34)
    monkeypatch.setattr(mp, "_get_spot_price", lambda *a, **k: 0.11)

    deleted = []
    monkeypatch.setattr(
        mp, "_delete_managed_policies",
        lambda *a, **k: deleted.append({"args": a, "kwargs": k}),
    )
    monkeypatch.setattr(mp, "_cleanup_iam_on_failure",
                        lambda *a, **k: deleted.append({"cleanup": True}))
    def _abort(timer, line_length, *args, **kwargs):
        record.setdefault("abort", {
            "timer": timer,
            "args": args,
            "kwargs": kwargs,
            "playbook_ran": any("ansible-playbook" in c
                                for c in record["calls"]),
        })

    monkeypatch.setattr(mp, "ctrlC_Abort", _abort)

    serial_dir = tmp_path / "active_clusters" / CLUSTER
    serial_dir.mkdir(parents=True, exist_ok=True)
    (serial_dir / f"{CLUSTER}.serial").write_text(SERIAL + "\n")

    return {"mod": mp, "record": record, "iam": iam, "deleted": deleted,
            "clients": clients, "root": tmp_path, "src": src}


class _Proc:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def _argv(*extra):
    argv = ["make_pcluster.py", "-N", CLUSTER, "-O", OWNER, "-A", AZ,
            "-E", OWNER + "@example.com"]
    for flag, value in (("--headnode_instance_type", "c8g.2xlarge"),
                        ("--compute_instance_type", "c8g.4xlarge"),
                        ("--base_os", "ubuntu2404arm")):
        if flag not in extra:
            argv += [flag, value]
    return argv + list(extra)


def _run_main(staged, monkeypatch, *extra):
    monkeypatch.setattr(sys, "argv", _argv(*extra))
    return staged["mod"].main()


def _playbook_vars(record):
    for cmd in record["calls"]:
        if "ansible-playbook" in cmd and "--extra-vars" in cmd:
            return json.loads(cmd[cmd.index("--extra-vars") + 1])
    return None


def _swap_ec2(staged, monkeypatch, ec2):
    """Replace only the ec2 client; every other client stays as staged."""
    clients = dict(staged["clients"], ec2=ec2)
    monkeypatch.setattr(staged["mod"].boto3, "client",
                        lambda name, **kw: clients.get(name, object()))


def _rendered_vars(staged):
    """The vars file the playbook reads. Rendered with StrictUndefined, so a
    missing cluster_parameters key fails the build rather than the assert."""
    path = staged["src"] / "vars_files" / f"{CLUSTER}.yml"
    with open(path) as fh:
        return yaml.safe_load(fh)


class TestBuildPreflight:
    def test_existing_vars_file_blocks_a_rebuild(self, staged, monkeypatch):
        """A vars file means the cluster is already tracked; rebuilding over it
        orphans the old stack's IAM and serial state."""
        (staged["src"] / "vars_files" / f"{CLUSTER}.yml").write_text("x: 1\n")
        with pytest.raises(SystemExit) as exc:
            _run_main(staged, monkeypatch)
        assert exc.value.code == 1
        assert _playbook_vars(staged["record"]) is None

    def test_existing_cluster_stack_blocks_the_build(self, staged, monkeypatch):
        staged["record"]["describe_rc"] = 0
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch)
        assert _playbook_vars(staged["record"]) is None

    def test_unknown_az_aborts_before_any_playbook(self, staged, monkeypatch):
        class _NoAz(_FakeEc2):
            def describe_availability_zones(self, ZoneNames=None, **kw):
                return {"AvailabilityZones": []}

        _swap_ec2(staged, monkeypatch, _NoAz())
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch)
        assert _playbook_vars(staged["record"]) is None

    def test_mixed_cpu_architectures_are_rejected(self, staged, monkeypatch, capsys):
        """A Graviton head node with x86 compute nodes builds a cluster whose
        compute fleet cannot run the head node's binaries. This branch only
        fires for a family absent from the hardcoded ARM_FAMILIES prefix list,
        which is exactly why describe_instance_types is consulted — so the fake
        reports c6i as arm64, standing in for a future Graviton family."""
        class _SurpriseArm(_FakeEc2):
            def describe_instance_types(self, InstanceTypes=None, **kw):
                return {
                    "InstanceTypes": [
                        {"InstanceType": t,
                         "ProcessorInfo": {"SupportedArchitectures": [
                             "arm64" if t.startswith("c6i") else _arch_for(t)]}}
                        for t in (InstanceTypes or [])
                    ]
                }

        _swap_ec2(staged, monkeypatch, _SurpriseArm())
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch, "--base_os", "ubuntu2404",
                      "--headnode_instance_type", "c5.xlarge",
                      "--compute_instance_type", "c6i.2xlarge")
        assert "Mixed architectures detected" in capsys.readouterr().out
        assert _playbook_vars(staged["record"]) is None

    def test_base_os_architecture_must_match_the_instances(self, staged, monkeypatch, capsys):
        """An x86 base OS on Graviton hardware produces a cluster that cannot
        boot at all."""
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch, "--base_os", "ubuntu2404",
                      "--headnode_instance_type", "c8g.2xlarge",
                      "--compute_instance_type", "c8g.4xlarge")
        out = capsys.readouterr().out
        assert "is ARM/Graviton but base_os=ubuntu2404 is x86_64" in out
        assert _playbook_vars(staged["record"]) is None

    def test_api_arch_overrides_the_hardcoded_prefix_list(self, staged, monkeypatch, capsys):
        """describe_instance_types is authoritative: a uniformly-arm64 fleet on
        an x86 base OS must be rejected even when no instance family matches the
        ARM_FAMILIES prefixes that base_os_instance_check knows about."""
        class _AllArm(_FakeEc2):
            def describe_instance_types(self, InstanceTypes=None, **kw):
                return {
                    "InstanceTypes": [
                        {"InstanceType": t,
                         "ProcessorInfo": {"SupportedArchitectures": ["arm64"]}}
                        for t in (InstanceTypes or [])
                    ]
                }

        _swap_ec2(staged, monkeypatch, _AllArm())
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch, "--base_os", "ubuntu2404",
                      "--headnode_instance_type", "c5.xlarge",
                      "--compute_instance_type", "c5.2xlarge")
        assert "base_os=ubuntu2404 is x86_64" in capsys.readouterr().out
        assert _playbook_vars(staged["record"]) is None

    def test_loginnode_instance_type_must_exist(self, staged, monkeypatch, capsys):
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch, "--enable_loginnode", "true",
                      "--loginnode_instance_type", "not-a-real-instance-type")
        out = capsys.readouterr().out
        assert "not-a-real-instance-type" in out
        assert _playbook_vars(staged["record"]) is None

    def test_loginnode_architecture_must_match_base_os(self, staged, monkeypatch, capsys):
        """A Graviton login node on an x86 cluster cannot boot; this must fail
        at preflight, not 15+ minutes into a real build."""
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch, "--base_os", "ubuntu2404",
                      "--headnode_instance_type", "c5.xlarge",
                      "--compute_instance_type", "c5.2xlarge",
                      "--enable_loginnode", "true",
                      "--loginnode_instance_type", "c8g.xlarge")
        out = capsys.readouterr().out
        assert "is ARM/Graviton but base_os=ubuntu2404 is x86_64" in out
        assert _playbook_vars(staged["record"]) is None

    def test_loginnode_instance_type_falls_back_by_architecture_on_x86(self, staged, monkeypatch):
        """Regression: a flat c8g.xlarge (Graviton) fallback would silently
        fail preflight for an operator who opts into --enable_loginnode=true
        on an x86_64 cluster without also setting --loginnode_instance_type
        and without --use_defaults. The architecture-aware fallback in
        _default_loginnode_instance_type must resolve to c5.xlarge here
        instead, so preflight succeeds."""
        with pytest.raises(SystemExit) as exc:
            _run_main(staged, monkeypatch, "--base_os", "ubuntu2404",
                      "--headnode_instance_type", "c5.xlarge",
                      "--compute_instance_type", "c5.2xlarge",
                      "--enable_loginnode", "true")
        assert exc.value.code == 0
        v = _rendered_vars(staged)
        assert v["loginnode_instance_type"] == "c5.xlarge"

    def test_loginnode_count_cannot_be_negative(self, staged, monkeypatch, capsys):
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch, "--enable_loginnode", "true",
                      "--loginnode_count", "-1")
        assert "loginnode_count must be >= 0" in capsys.readouterr().out
        assert _playbook_vars(staged["record"]) is None

    def test_loginnode_count_zero_is_accepted(self, staged, monkeypatch):
        """AWS's own LoginNodesPoolSchema.count floors at 0 — a defined-but-empty
        pool is valid, the same shape as a compute queue scaled to zero."""
        with pytest.raises(SystemExit) as exc:
            _run_main(staged, monkeypatch, "--enable_loginnode", "true",
                      "--loginnode_count", "0")
        assert exc.value.code == 0
        v = _rendered_vars(staged)
        assert v["loginnode_count"] == 0

    def test_loginnode_count_validation_skipped_when_disabled(self, staged, monkeypatch):
        """A negative loginnode_count must not fail preflight when the feature
        is off entirely — the validation is gated on enable_loginnode, not on
        the count alone."""
        with pytest.raises(SystemExit) as exc:
            _run_main(staged, monkeypatch, "--loginnode_count", "-1")
        assert exc.value.code == 0


class TestExistingS3Bucket:
    """The bucket name is derived from the serial, so a bucket that already
    exists on a fresh build means the name collides with another cluster's
    storage — continuing would write this cluster's scripts into it."""

    def test_existing_bucket_aborts_a_fresh_build(self, staged, monkeypatch, capsys):
        class _BucketExists:
            def head_bucket(self, Bucket=None, **kw):
                return {}

        monkeypatch.setitem(staged["clients"], "s3", _BucketExists())
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch)
        assert "existing S3 bucket" in capsys.readouterr().out
        assert _playbook_vars(staged["record"]) is None

    def test_existing_bucket_is_reused_when_resuming(self, staged, monkeypatch, capsys):
        """A serial file that already existed means an earlier run was
        interrupted after creating the bucket; that build must be resumable."""
        class _BucketExists:
            def head_bucket(self, Bucket=None, **kw):
                return {}

        monkeypatch.setitem(staged["clients"], "s3", _BucketExists())
        monkeypatch.setattr(
            staged["mod"], "_load_or_create_serial",
            lambda data_dir, name: (
                os.path.join(str(staged["root"]), "active_clusters", name,
                             name + ".serial"),
                SERIAL, DATESTAMP, False,
            ),
        )
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch)
        assert "interrupted run" in capsys.readouterr().out
        assert _playbook_vars(staged["record"]) is not None


class TestBuildAbortWindow:
    def test_abort_window_opens_before_ansible_runs(self, staged, monkeypatch):
        """Ctrl-C in the window is supposed to cancel the build and clean up
        IAM. If the window opened after ansible-playbook, the cluster would
        already exist by the time the operator could abort."""
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch)
        assert staged["record"]["abort"]["playbook_ran"] is False

    def test_debug_mode_lengthens_the_abort_window(self, staged, monkeypatch):
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch, "-D", "true")
        assert staged["record"]["abort"]["timer"] == 15

    def test_default_abort_window_is_five_seconds(self, staged, monkeypatch):
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch)
        assert staged["record"]["abort"]["timer"] == 5

    def test_abort_window_gets_the_real_cleanup_targets(self, staged, monkeypatch):
        """Unlike kill_pcluster.py, which passes None because nothing exists yet,
        a build has already created the IAM role, the serial file, and the vars
        file by this point — Ctrl-C has to be able to remove all three or the
        next build collides on the same serial-derived names."""
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch)
        abort = staged["record"]["abort"]
        vars_file_path, serial_file, serial = abort["args"][0:3]
        assert vars_file_path.endswith(f"vars_files/{CLUSTER}.yml")
        assert serial_file.endswith(f"{CLUSTER}.serial")
        assert serial == SERIAL
        assert abort["kwargs"]["aws_account_id"] == "123456789012"


class TestBuildFailureCleansUpIam:
    """A failed playbook leaves the IAM role and policies behind. The next
    build derives the same names from the same serial, so CreatePolicy fails
    with EntityAlreadyExists and the retry is blocked until an operator
    deletes them by hand."""

    def test_failed_playbook_deletes_the_managed_policies(self, staged, monkeypatch):
        staged["record"]["playbook_rc"] = 2
        with pytest.raises(SystemExit) as exc:
            _run_main(staged, monkeypatch)
        assert exc.value.code == 2
        assert any("args" in d for d in staged["deleted"])

    def test_failed_playbook_deletes_the_iam_role(self, staged, monkeypatch):
        staged["record"]["playbook_rc"] = 2
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch)
        assert staged["iam"].deleted_roles == ["pclustermaker-role-" + SERIAL]

    def test_failure_exit_code_is_the_playbook_exit_code(self, staged, monkeypatch):
        staged["record"]["playbook_rc"] = 7
        with pytest.raises(SystemExit) as exc:
            _run_main(staged, monkeypatch)
        assert exc.value.code == 7

    def test_failure_tells_the_operator_how_to_tear_down(self, staged, monkeypatch, capsys):
        staged["record"]["playbook_rc"] = 1
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch)
        out = capsys.readouterr().out
        assert "kill_pcluster.py" in out
        assert f"-N {CLUSTER}" in out

    def test_iam_setup_failure_triggers_cleanup_and_aborts(self, staged, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("AccessDenied")

        monkeypatch.setattr(staged["mod"], "_setup_iam", _boom)
        with pytest.raises(SystemExit) as exc:
            _run_main(staged, monkeypatch)
        assert exc.value.code == 1
        assert any(d.get("cleanup") for d in staged["deleted"])
        assert _playbook_vars(staged["record"]) is None


class TestDerivedVariablesReachTheVarsFile:
    def test_pcluster_os_strips_the_arm_suffix(self, staged, monkeypatch):
        """PCluster's Os: field rejects the arm suffix, so pcluster_os is
        base_os minus 'arm'. Passing base_os through unchanged fails at
        cluster-create time with a schema error."""
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch, "--base_os", "ubuntu2404arm")
        v = _rendered_vars(staged)
        assert v["base_os"] == "ubuntu2404arm"
        assert v["pcluster_os"] == "ubuntu2404"

    def test_non_arm_os_is_unchanged(self, staged, monkeypatch):
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch, "--base_os", "ubuntu2404",
                      "--headnode_instance_type", "c5.xlarge",
                      "--compute_instance_type", "c5.xlarge")
        v = _rendered_vars(staged)
        assert v["pcluster_os"] == v["base_os"] == "ubuntu2404"

    def test_iam_names_are_derived_from_the_serial_number(self, staged, monkeypatch):
        """kill_pcluster.py and the teardown playbook rebuild these names from
        the serial alone; a different derivation here orphans them."""
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch)
        v = _rendered_vars(staged)
        assert v["ec2_iam_role"] == "pclustermaker-role-" + SERIAL
        assert v["ec2_iam_policy"] == "pclustermaker-policy-" + SERIAL

    def test_efa_gdr_stays_off_for_non_gdr_instances(self, staged, monkeypatch):
        """GdrSupport is rejected at cluster-create time by any instance family
        that does not support it, so this must not be set unconditionally."""
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch, "--enable_efa", "true",
                      "--headnode_instance_type", "c8g.2xlarge",
                      "--compute_instance_type", "hpc7g.16xlarge")
        v = _rendered_vars(staged)
        assert v["enable_efa"] == "true"
        assert v["enable_efa_gdr"] == "false"

    def test_serial_number_reaches_the_playbook(self, staged, monkeypatch):
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch)
        assert _playbook_vars(staged["record"])["cluster_serial_number"] == SERIAL

    def test_debug_mode_raises_ansible_verbosity(self, staged, monkeypatch):
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch, "-D", "true")
        cmd = next(c for c in staged["record"]["calls"] if "ansible-playbook" in c)
        assert "-vvv" in cmd

    def test_loginnode_instance_type_reaches_the_vars_file(self, staged, monkeypatch):
        with pytest.raises(SystemExit) as exc:
            _run_main(staged, monkeypatch, "--enable_loginnode", "true",
                      "--loginnode_instance_type", "c8g.2xlarge",
                      "--loginnode_count", "2")
        assert exc.value.code == 0
        v = _rendered_vars(staged)
        assert v["enable_loginnode"] == "true"
        assert v["loginnode_instance_type"] == "c8g.2xlarge"
        assert v["loginnode_count"] == 2


class TestIdleComputeNotice:
    """The toolkit schedules no teardown of any kind, so the build summary is the
    only place the operator learns what an idle cluster costs. Removing
    --cluster_lifetime removed a self-termination promise from the summary; this
    replaces it with an accurate statement of what actually bounds the bill."""

    def test_summary_states_the_idle_scaledown_window(self, staged, monkeypatch, capsys):
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch, "--scaledown_idletime", "17")
        out = capsys.readouterr().out
        assert "Idle compute:" in out
        assert "17 minutes" in out, (
            "the summary must quote the cluster's real scaledown_idletime, not a "
            "hardcoded default"
        )
        assert "scales its compute fleet to zero" in out

    def test_summary_warns_when_the_queue_floor_is_pinned_above_zero(
        self, staged, monkeypatch, capsys
    ):
        """maintain_cpu_initial_size pins MinCount above zero, so the fleet never
        scales to zero and those nodes bill continuously. Telling that operator
        their cluster costs nothing when idle would be false."""
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch, "--maintain_cpu_initial_size", "true")
        out = capsys.readouterr().out
        assert "maintain_" in out and "bill continuously" in out
        assert "scales its compute fleet to zero" not in out

    def test_summary_points_at_manual_teardown(self, staged, monkeypatch, capsys):
        """Nothing tears the cluster down on a timer any more; the head node bills
        until the operator acts."""
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch)
        out = capsys.readouterr().out
        assert "head node keeps running and billing" in out
        assert f"./kill_pcluster.py -N {CLUSTER}" in out


_GPU_COUNTS = {
    "p3.2xlarge": ("NVIDIA", 1),
    "p4d.24xlarge": ("NVIDIA", 8),
    "g5.xlarge": ("NVIDIA", 1),
    "g4ad.4xlarge": ("AMD", 1),
}


class _GpuAwareEc2(_FakeEc2):
    """describe_instance_types with the GpuInfo shape the real API returns."""

    def describe_instance_types(self, InstanceTypes=None, **kw):
        out = []
        for t in InstanceTypes or []:
            entry = {"InstanceType": t,
                     "ProcessorInfo": {"SupportedArchitectures": [_arch_for(t)]}}
            if t in _GPU_COUNTS:
                mfr, count = _GPU_COUNTS[t]
                entry["GpuInfo"] = {"Gpus": [{"Manufacturer": mfr, "Count": count}]}
            out.append(entry)
        return {"InstanceTypes": out}


class TestGpuRanksPerNodeReachTheVarsFile:
    """gpu_ranks_per_node shapes --ntasks-per-node in the rendered benchmark job
    script. vars_file.j2 renders under StrictUndefined, so a break in this thread
    fails the cluster build, not just the benchmark."""

    def _run(self, staged, monkeypatch, *extra):
        _swap_ec2(staged, monkeypatch, _GpuAwareEc2())
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch, "--base_os", "ubuntu2404",
                      "--headnode_instance_type", "c5.xlarge", *extra)
        return _rendered_vars(staged)

    def test_a_cpu_only_cluster_reports_zero(self, staged, monkeypatch):
        v = self._run(staged, monkeypatch, "--compute_instance_type", "c5.2xlarge")
        assert v["enable_gpu_queue"] == "false"
        assert v["gpu_ranks_per_node"] == 0

    def test_a_single_gpu_instance_reports_one(self, staged, monkeypatch):
        v = self._run(staged, monkeypatch, "--compute_instance_type", "c5.2xlarge",
                      "--gpu_instance_type", "p3.2xlarge")
        assert v["gpu_ranks_per_node"] == 1

    def test_a_multi_gpu_instance_reports_every_device(self, staged, monkeypatch):
        v = self._run(staged, monkeypatch, "--compute_instance_type", "",
                      "--gpu_instance_type", "p4d.24xlarge")
        assert v["enable_cpu_queue"] == "false"
        assert v["gpu_ranks_per_node"] == 8

    def test_a_mixed_gpu_queue_reports_the_minimum(self, staged, monkeypatch):
        """Only the smallest count is satisfiable by every node in the queue;
        the maximum would oversubscribe the g5 nodes 8:1."""
        v = self._run(staged, monkeypatch, "--compute_instance_type", "",
                      "--gpu_instance_type", "p4d.24xlarge,g5.xlarge")
        assert v["gpu_ranks_per_node"] == 1

    def test_an_amd_gpu_queue_reports_zero(self, staged, monkeypatch):
        """g4ad is Radeon, invisible to CUDA and GRES. The job template falls
        back to a CPU-shaped rank count on this value."""
        v = self._run(staged, monkeypatch, "--compute_instance_type", "",
                      "--gpu_instance_type", "g4ad.4xlarge")
        assert v["enable_gpu_queue"] == "true"
        assert v["gpu_ranks_per_node"] == 0

    def test_the_playbook_gets_the_same_value(self, staged, monkeypatch):
        """create_pcluster.yml renders the job script from --extra-vars merged
        over the vars file; a value present in only one of them is drift."""
        v = self._run(staged, monkeypatch, "--compute_instance_type", "",
                      "--gpu_instance_type", "p4d.24xlarge")
        assert _playbook_vars(staged["record"])["gpu_ranks_per_node"] == v["gpu_ranks_per_node"]

    def test_no_extra_api_calls_are_made_for_the_gpu_count(self, staged, monkeypatch):
        """The count is read out of the describe_instance_types response the
        architecture check already fetches. A second sweep would double the API
        calls on every build."""
        class _Counting(_GpuAwareEc2):
            calls = 0

            def describe_instance_types(self, InstanceTypes=None, **kw):
                type(self).calls += 1
                return super().describe_instance_types(InstanceTypes=InstanceTypes, **kw)

        ec2 = _Counting()
        _swap_ec2(staged, monkeypatch, ec2)
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch, "--base_os", "ubuntu2404",
                      "--headnode_instance_type", "c5.xlarge",
                      "--compute_instance_type", "c5.2xlarge",
                      "--gpu_instance_type", "p4d.24xlarge")
        assert _Counting.calls == 1, (
            f"describe_instance_types called {_Counting.calls} times; the GPU count "
            "should come from the architecture check's existing response"
        )


class TestStorageReachesTheBuildSummary:
    """A cluster built with --enable_fsx=true printed "Options: FSx/Lustre" and
    nothing else: no /fsx, no size, and the only task naming the hydration
    helpers is skipped when hydration is off. The summary is where the operator
    learns what to cd into, so every filesystem has to name its mount point."""

    def _out(self, staged, monkeypatch, capsys, *extra):
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch, *extra)
        return capsys.readouterr().out

    def test_a_default_cluster_reports_the_shared_ebs_volume(
        self, staged, monkeypatch, capsys
    ):
        out = self._out(staged, monkeypatch, capsys)
        assert "Shared storage:" in out
        assert "/shared" in out
        assert "EBS" in out

    def test_an_fsx_cluster_reports_the_lustre_mount_point(
        self, staged, monkeypatch, capsys
    ):
        """The reported bug, end to end through main()."""
        out = self._out(staged, monkeypatch, capsys, "--enable_fsx", "true",
                        "--fsx_size", "2400")
        assert "/fsx" in out, (
            "the build summary of an FSx cluster still does not mention /fsx"
        )
        assert "FSx for Lustre (2400 GB)" in out

    def test_a_non_fsx_cluster_does_not_mention_lustre(
        self, staged, monkeypatch, capsys
    ):
        out = self._out(staged, monkeypatch, capsys)
        assert "/fsx" not in out
        assert "Lustre" not in out

    def test_an_efs_cluster_reports_the_efs_mount_point(
        self, staged, monkeypatch, capsys
    ):
        out = self._out(staged, monkeypatch, capsys, "--enable_efs", "true")
        assert "/efs" in out

    def test_the_summary_reports_where_spack_installs(
        self, staged, monkeypatch, capsys
    ):
        """pkg_dir follows the storage precedence, so it moves when FSx is added.
        An operator told /shared/pkg on an FSx cluster looks in the wrong place."""
        out = self._out(staged, monkeypatch, capsys)
        assert "install under /shared/pkg" in out

    def test_spack_moves_to_lustre_on_an_fsx_cluster(
        self, staged, monkeypatch, capsys
    ):
        """An operator told /shared/pkg on an FSx cluster looks in the wrong
        place. One main() call per test: the vars file it writes blocks a second
        build of the same cluster name."""
        out = self._out(staged, monkeypatch, capsys, "--enable_fsx", "true")
        assert "install under /fsx/pkg" in out

    def test_the_summary_agrees_with_the_rendered_vars_file(
        self, staged, monkeypatch, capsys
    ):
        """The vars file is what the playbook and postinstall actually use. If the
        summary and the vars file disagree, one of them is lying to the operator."""
        out = self._out(staged, monkeypatch, capsys, "--enable_fsx", "true")
        assert f"install under {_rendered_vars(staged)['pkg_dir']}" in out
