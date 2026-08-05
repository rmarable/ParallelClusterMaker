"""
Direct tests for kill_pcluster.py's main().

This is the most destructive script in the toolkit and had no direct coverage —
only the pure helpers it calls out of src/pcluster_core.py were tested, so
nothing checked the sequencing that actually makes teardown safe: that the
Ansible playbook is what deletes AWS resources, that a playbook failure leaves
the serial and vars files on disk so the operator can retry, and that state
files are removed only after the playbook succeeds.
"""

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from entrypoint_harness import REPO_ROOT, RecordingRun, load_entrypoint

AZ = "us-east-1a"
CLUSTER = "killme"
OWNER = "testuser"
SERIAL = "killme-00001220260720"


@pytest.fixture
def kp():
    return load_entrypoint("kill_pcluster.py")


@pytest.fixture
def staged(kp, tmp_path, monkeypatch):
    """A cluster whose serial and vars files exist, with AWS/Ansible stubbed."""
    serial_dir = tmp_path / "active_clusters" / CLUSTER
    serial_dir.mkdir(parents=True)
    serial_file = serial_dir / f"{CLUSTER}.serial"
    serial_file.write_text(f"{SERIAL}\n./make_pcluster.py -N {CLUSTER} -O {OWNER}\n")

    vars_dir = tmp_path / "src" / "vars_files"
    vars_dir.mkdir(parents=True)
    vars_file = vars_dir / f"{CLUSTER}.yml"
    vars_file.write_text('cluster_name: "killme"\nturbot_account: "disabled"\n')

    monkeypatch.setattr(kp, "_repo_root", str(tmp_path))
    monkeypatch.setattr(kp, "_src_dir", str(tmp_path / "src"))

    class _Ec2:
        def describe_availability_zones(self, ZoneNames):
            return {"AvailabilityZones": [{"RegionName": "us-east-1"}]}

    monkeypatch.setattr(kp.boto3, "client", lambda *a, **k: _Ec2())
    # The abort window is a 5-second sleep; tests must not pay for it.
    monkeypatch.setattr(kp, "ctrlC_Abort", lambda *a, **k: None)

    runner = RecordingRun()
    monkeypatch.setattr(kp.subprocess, "run", runner)
    monkeypatch.setattr(
        sys, "argv",
        ["kill_pcluster.py", "-A", AZ, "-N", CLUSTER, "-O", OWNER],
    )
    return {
        "mod": kp,
        "runner": runner,
        "serial_file": serial_file,
        "vars_file": vars_file,
        "root": tmp_path,
    }


def _extra_vars(runner):
    cmd = runner.command_containing("--extra-vars")
    assert cmd is not None, "ansible-playbook was never invoked"
    return json.loads(cmd[cmd.index("--extra-vars") + 1])


class TestTeardownHappyPath:
    def test_ansible_playbook_is_what_deletes_the_cluster(self, staged):
        with pytest.raises(SystemExit) as exc:
            staged["mod"].main()
        assert exc.value.code == 0
        cmd = staged["runner"].command_containing("ansible-playbook")
        assert cmd is not None
        assert cmd[-1].endswith("delete_pcluster.yml") or any(
            c.endswith("delete_pcluster.yml") for c in cmd
        )

    def test_serial_and_vars_files_removed_after_success(self, staged):
        with pytest.raises(SystemExit):
            staged["mod"].main()
        assert not staged["serial_file"].exists()
        assert not staged["vars_file"].exists()

    def test_serial_number_is_passed_to_the_playbook(self, staged):
        with pytest.raises(SystemExit):
            staged["mod"].main()
        assert _extra_vars(staged["runner"])["cluster_serial_number"] == SERIAL

    def test_delete_s3_bucketname_defaults_to_true(self, staged):
        with pytest.raises(SystemExit):
            staged["mod"].main()
        assert _extra_vars(staged["runner"])["delete_s3_bucketname"] == "true"

    def test_delete_s3_bucketname_false_reaches_the_playbook(self, staged, monkeypatch):
        monkeypatch.setattr(
            sys, "argv",
            ["kill_pcluster.py", "-A", AZ, "-N", CLUSTER, "-O", OWNER,
             "--delete_s3_bucketname=false"],
        )
        with pytest.raises(SystemExit):
            staged["mod"].main()
        assert _extra_vars(staged["runner"])["delete_s3_bucketname"] == "false"

    def test_debug_mode_raises_ansible_verbosity(self, staged, monkeypatch):
        monkeypatch.setattr(
            sys, "argv",
            ["kill_pcluster.py", "-A", AZ, "-N", CLUSTER, "-O", OWNER, "-D", "true"],
        )
        with pytest.raises(SystemExit):
            staged["mod"].main()
        cmd = staged["runner"].command_containing("ansible-playbook")
        assert "-vvv" in cmd
        assert _extra_vars(staged["runner"])["debug_mode"] == "true"


class TestTeardownFailureLeavesStateForRetry:
    """A failed playbook means AWS resources may still exist. Deleting the
    serial and vars files anyway would strand them: kill_pcluster.py requires
    both to run at all, so the operator would have no way to retry."""

    def test_state_files_survive_a_failed_playbook(self, staged, monkeypatch):
        runner = RecordingRun(rc_by_command={"ansible-playbook": 2})
        monkeypatch.setattr(staged["mod"].subprocess, "run", runner)
        with pytest.raises(SystemExit) as exc:
            staged["mod"].main()
        assert exc.value.code == 2
        assert staged["serial_file"].exists()
        assert staged["vars_file"].exists()

    def test_failure_exit_code_is_the_playbook_exit_code(self, staged, monkeypatch):
        runner = RecordingRun(rc_by_command={"ansible-playbook": 13})
        monkeypatch.setattr(staged["mod"].subprocess, "run", runner)
        with pytest.raises(SystemExit) as exc:
            staged["mod"].main()
        assert exc.value.code == 13

    def test_failure_warns_that_aws_resources_may_remain(self, staged, monkeypatch, capsys):
        runner = RecordingRun(rc_by_command={"ansible-playbook": 1})
        monkeypatch.setattr(staged["mod"].subprocess, "run", runner)
        with pytest.raises(SystemExit):
            staged["mod"].main()
        out = capsys.readouterr().out
        assert "may not have been deleted" in out
        assert "CloudFormation" in out


class TestTeardownPreflight:
    def test_missing_serial_file_aborts_before_ansible(self, staged):
        staged["serial_file"].unlink()
        with pytest.raises(SystemExit) as exc:
            staged["mod"].main()
        assert exc.value.code == 1
        assert staged["runner"].command_containing("ansible-playbook") is None

    def test_missing_vars_file_aborts_before_ansible(self, staged):
        staged["vars_file"].unlink()
        with pytest.raises(SystemExit) as exc:
            staged["mod"].main()
        assert exc.value.code == 1
        assert staged["runner"].command_containing("ansible-playbook") is None

    def test_invalid_cluster_name_aborts_before_ansible(self, staged, monkeypatch):
        """The serial/vars files are staged under the traversal name too, so the
        preflight file check cannot stand in for validation — without
        _validate_cluster_name this run would reach ansible-playbook with a
        path-traversal cluster name. Asserting on the validator's own message is
        what pins it; a bare SystemExit passes either way."""
        evil = "../../etc/passwd"
        serial_dir = staged["root"] / "active_clusters" / evil
        serial_dir.mkdir(parents=True, exist_ok=True)
        (serial_dir / f"{evil}.serial").parent.mkdir(parents=True, exist_ok=True)
        (staged["root"] / "active_clusters" / f"{evil}.serial").write_text(SERIAL)
        vars_dir = staged["root"] / "src" / "vars_files"
        (vars_dir / f"{evil}.yml").parent.mkdir(parents=True, exist_ok=True)
        (vars_dir / f"{evil}.yml").write_text("cluster_name: evil\n")

        monkeypatch.setattr(
            sys, "argv",
            ["kill_pcluster.py", "-A", AZ, "-N", evil, "-O", OWNER],
        )
        with pytest.raises(SystemExit) as exc:
            staged["mod"].main()
        assert "cluster_name must start with a lowercase letter" in str(exc.value.code)
        assert staged["runner"].command_containing("ansible-playbook") is None

    def test_invalid_cluster_owner_aborts_before_ansible(self, staged, monkeypatch):
        monkeypatch.setattr(
            sys, "argv",
            ["kill_pcluster.py", "-A", AZ, "-N", CLUSTER, "-O", "Bad Owner!"],
        )
        with pytest.raises(SystemExit) as exc:
            staged["mod"].main()
        assert "cluster_owner must contain only lowercase" in str(exc.value.code)
        assert staged["runner"].command_containing("ansible-playbook") is None

    def test_unknown_az_aborts_before_ansible(self, staged, monkeypatch):
        class _Empty:
            def describe_availability_zones(self, ZoneNames):
                return {"AvailabilityZones": []}

        monkeypatch.setattr(staged["mod"].boto3, "client", lambda *a, **k: _Empty())
        with pytest.raises(SystemExit) as exc:
            staged["mod"].main()
        assert exc.value.code == 1
        assert staged["runner"].command_containing("ansible-playbook") is None

    def test_a_missing_cluster_stack_still_cleans_up_artifacts(self, staged, monkeypatch, capsys):
        """describe-cluster returning nonzero means the stack is already gone;
        teardown must still run to clear IAM, S3, and local state."""
        runner = RecordingRun(rc_by_command={"describe-cluster": 1})
        monkeypatch.setattr(staged["mod"].subprocess, "run", runner)
        with pytest.raises(SystemExit) as exc:
            staged["mod"].main()
        assert exc.value.code == 0
        assert "was not found" in capsys.readouterr().out
        assert runner.command_containing("ansible-playbook") is not None


class TestTeardownAbortWindow:
    def test_operator_is_given_an_abort_window_before_deletion(self, staged, monkeypatch):
        """Ctrl-C during the window must cancel the deletion, so the window has
        to open before subprocess.run reaches ansible-playbook, not after."""
        seen = {}

        def _abort(sleep_time, *args, **kwargs):
            seen["sleep_time"] = sleep_time
            seen["ansible_ran"] = (
                staged["runner"].command_containing("ansible-playbook") is not None
            )

        monkeypatch.setattr(staged["mod"], "ctrlC_Abort", _abort)
        with pytest.raises(SystemExit):
            staged["mod"].main()
        assert seen["ansible_ran"] is False
        assert seen["sleep_time"] == 5

    def test_debug_mode_lengthens_the_abort_window(self, staged, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            staged["mod"], "ctrlC_Abort",
            lambda sleep_time, *a, **k: seen.setdefault("sleep_time", sleep_time),
        )
        monkeypatch.setattr(
            sys, "argv",
            ["kill_pcluster.py", "-A", AZ, "-N", CLUSTER, "-O", OWNER, "-D", "true"],
        )
        with pytest.raises(SystemExit):
            staged["mod"].main()
        assert seen["sleep_time"] == 15

    def test_abort_window_is_passed_no_cleanup_targets(self, staged, monkeypatch):
        """Ctrl-C in the abort window must not destroy IAM resources or state
        files — Ansible has not run yet, so there is nothing to clean up."""
        seen = {}
        monkeypatch.setattr(
            staged["mod"], "ctrlC_Abort",
            lambda *args, **kwargs: seen.setdefault("args", args),
        )
        with pytest.raises(SystemExit):
            staged["mod"].main()
        vars_file_path, serial_file, serial = seen["args"][2:5]
        assert vars_file_path is None
        assert serial_file is None
        assert serial is None
