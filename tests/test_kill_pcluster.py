"""
Direct tests for kill_pcluster.py's main(), now that core_delete_cluster tears
down clusters via boto3/pcluster.lib instead of shelling out to Ansible.

Fakes here stand in for two things: `import pcluster.lib as pc` (installed
into sys.modules, since that import happens at call time inside
core_delete_cluster -- see pcluster_core.py's own comment on why) and every
boto3 client core_delete_cluster constructs (one generic fake class good
enough for ec2/iam/s3/ssm/secretsmanager/sns, since none of their *other*
methods are shape-sensitive). The granular per-resource teardown logic is
already exhaustively unit-tested in test_teardown_steps.py; this file checks
the integration-level properties that actually make teardown safe: that
state files are removed only after a clean, confirmed, orphan-free delete,
that a DELETE_FAILED/unconfirmed/orphaned outcome preserves them and exits
non-zero, and that the abort window and cluster lock still work.
"""

import errno
import io
import os
import sys
import types
from datetime import datetime as DateTime, timezone

import pytest
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from entrypoint_harness import REPO_ROOT, load_entrypoint

sys.path.insert(0, os.path.join(REPO_ROOT, "src"))
import pcluster_core
from pcluster.api.errors import BadRequestException, NotFoundException


def _client_error(code, op, status=None):
    response = {"Error": {"Code": code, "Message": ""}}
    if status is not None:
        response["ResponseMetadata"] = {"HTTPStatusCode": status}
    return ClientError(response, op)

AZ = "us-east-1a"
CLUSTER = "killme"
OWNER = "testuser"
SERIAL = "killme-00001220260720"

_VARS_FILE_DEFAULTS = {
    "cluster_name": CLUSTER,
    "turbot_account": "disabled",
    "aws_account_id": "123456789012",
    "az": AZ,
    "ec2_iam_policy": f"pclustermaker-policy-{SERIAL}",
    "ec2_iam_role": f"pclustermaker-role-{SERIAL}",
    "ec2_keypair": f"{SERIAL}_us-east-1",
    "ec2_user": "ubuntu",
    "ec2_user_home": "/home/ubuntu",
    "enable_external_nfs": "false",
    "enable_fsx_hydration": "false",
    "enable_hpc_benchmarks": "false",
    "enable_monitoring": "false",
    "fsx_hydration_iam_policy": "UNDEFINED",
    "s3_bucketname": f"parallelclustermaker-{SERIAL}",
    "ssh_keypair": "/tmp/does-not-need-to-exist.pem",
    "ssh_secret_name": f"parallelcluster/{CLUSTER}/{SERIAL}/ssh-private-key",
}


def _vars_yaml(**overrides):
    merged = {**_VARS_FILE_DEFAULTS, **overrides}
    return "\n".join(f'{k}: "{v}"' for k, v in merged.items()) + "\n"


class _FakePcLib(types.ModuleType):
    """Stand-in for `import pcluster.lib as pc`. `describe_sequence` is a
    list of dicts (success) or exceptions, indexed by call count and
    clamped to the last entry once exhausted -- a single-element sequence
    models a persistent status, matching test_teardown_steps.py's own
    _ScriptedDescribeFn."""

    def __init__(self, describe_sequence=None, delete_raises=None):
        super().__init__("pcluster.lib")
        self._describe_sequence = list(
            describe_sequence or [{"clusterStatus": "DELETE_COMPLETE"}]
        )
        self.delete_raises = delete_raises
        self.describe_calls = []
        self.delete_calls = []

    def describe_cluster(self, cluster_name, region):
        self.describe_calls.append((cluster_name, region))
        i = min(len(self.describe_calls) - 1, len(self._describe_sequence) - 1)
        item = self._describe_sequence[i]
        if isinstance(item, Exception):
            raise item
        return item

    def delete_cluster(self, cluster_name, region):
        self.delete_calls.append((cluster_name, region))
        if self.delete_raises:
            raise self.delete_raises


class _FakeAwsClient:
    """A generic boto3-client stand-in covering every AWS call
    core_delete_cluster's teardown functions make. Methods whose return
    shape the code actually inspects get realistic empty results; every
    other call (the deletes/detaches/publishes, none of which are
    inspected) is a no-op success. `fail` names one method to raise
    RuntimeError, for testing the orphan-detection path."""

    _SHAPED = {
        "list_attached_role_policies": {"AttachedPolicies": []},
        "list_role_policies": {"PolicyNames": []},
        "list_instance_profiles_for_role": {"InstanceProfiles": []},
        "describe_security_groups": {"SecurityGroups": []},
        "describe_availability_zones": {"AvailabilityZones": [{"RegionName": "us-east-1"}]},
    }

    def __init__(self, fail=None):
        self._fail = fail
        self._objects = {}  # Workstream 4's S3 distributed lock: key -> {"body","etag","last_modified"}
        self._etag_counter = 0

    def get_paginator(self, name):
        assert name == "list_object_versions"

        class _Paginator:
            def paginate(self, Bucket):
                yield {"Versions": [], "DeleteMarkers": []}

        return _Paginator()

    def _new_etag(self):
        self._etag_counter += 1
        return f"etag-{self._etag_counter}"

    def put_object(self, Bucket, Key, Body, IfNoneMatch=None, IfMatch=None):
        """Workstream 4's S3 distributed lock uses conditional PutObject --
        modeled here the same way tests/test_s3_cluster_lock.py's own
        _FakeS3Lock models it, since this file's generic __getattr__
        fallback (always succeeds) would make a held lock unacquirable to
        simulate at all."""
        existing = self._objects.get(Key)
        if IfNoneMatch == "*" and existing is not None:
            raise _client_error("PreconditionFailed", "PutObject", status=412)
        if IfMatch is not None and (existing is None or existing["etag"] != IfMatch):
            raise _client_error("PreconditionFailed", "PutObject", status=412)
        etag = self._new_etag()
        self._objects[Key] = {"body": Body, "etag": etag, "last_modified": DateTime.now(timezone.utc)}
        return {"ETag": etag}

    def get_object(self, Bucket, Key):
        obj = self._objects[Key]
        return {"ETag": obj["etag"], "LastModified": obj["last_modified"], "Body": io.BytesIO(obj["body"])}

    def delete_object(self, Bucket, Key):
        self._objects.pop(Key, None)

    def __getattr__(self, name):
        if name == self._fail:
            def _boom(*a, **k):
                raise RuntimeError(f"{name} failed")
            return _boom
        if name in self._SHAPED:
            shape = self._SHAPED[name]
            return lambda *a, **k: shape
        return lambda *a, **k: {}


@pytest.fixture
def kp():
    return load_entrypoint("kill_pcluster.py")


@pytest.fixture
def staged(kp, tmp_path, monkeypatch):
    """A cluster whose serial and vars files exist, with pcluster.lib and
    every boto3 client stubbed to a clean, confirmed delete by default."""
    serial_dir = tmp_path / "active_clusters" / CLUSTER
    serial_dir.mkdir(parents=True)
    serial_file = serial_dir / f"{CLUSTER}.serial"
    serial_file.write_text(f"{SERIAL}\n./make_pcluster.py -N {CLUSTER} -O {OWNER}\n")

    vars_dir = tmp_path / "src" / "vars_files"
    vars_dir.mkdir(parents=True)
    vars_file = vars_dir / f"{CLUSTER}.yml"
    vars_file.write_text(_vars_yaml())

    # core_delete_cluster renders sns_destruction_summary_report.j2 out of
    # <repo_root>/templates -- the real templates, since a stub would drift
    # from what StrictUndefined actually requires (same reasoning as
    # test_make_pcluster_main.py's identical symlink).
    (tmp_path / "templates").symlink_to(os.path.join(REPO_ROOT, "templates"))

    monkeypatch.setattr(kp, "_repo_root", str(tmp_path))
    monkeypatch.setattr(kp, "_src_dir", str(tmp_path / "src"))

    pc_lib = _FakePcLib()
    monkeypatch.setitem(sys.modules, "pcluster.lib", pc_lib)
    # One fake instance per service name, not a fresh one per call: the S3
    # distributed lock acquires and releases through two separate
    # boto3.client("s3", ...) calls inside core_delete_cluster (a dedicated
    # client for the lock, then the teardown code's own later one), and
    # both need to see the same stored lock object.
    _clients = {}

    def _boto3_client(name, **kw):
        return _clients.setdefault(name, _FakeAwsClient())

    monkeypatch.setattr(kp.boto3, "client", _boto3_client)
    # The wait loop's retry delay is real time.sleep() in production; a
    # TIMED_OUT scenario iterates the full retry count, so this must be a
    # no-op for that test to run in well under a second.
    monkeypatch.setattr(pcluster_core.time, "sleep", lambda s: None)
    # The abort window is a 5-second sleep; tests must not pay for it.
    monkeypatch.setattr(kp, "ctrlC_Abort", lambda *a, **k: None)

    monkeypatch.setattr(
        sys, "argv",
        ["kill_pcluster.py", "-A", AZ, "-N", CLUSTER, "-O", OWNER],
    )
    return {
        "mod": kp,
        "pc_lib": pc_lib,
        "serial_file": serial_file,
        "vars_file": vars_file,
        "root": tmp_path,
        "clients": _clients,
    }


class TestTeardownHappyPath:
    def test_confirmed_delete_exits_zero(self, staged):
        with pytest.raises(SystemExit) as exc:
            staged["mod"].main()
        assert exc.value.code == 0
        assert staged["pc_lib"].delete_calls == [(CLUSTER, "us-east-1")]

    def test_serial_and_vars_files_removed_after_success(self, staged):
        with pytest.raises(SystemExit):
            staged["mod"].main()
        assert not staged["serial_file"].exists()
        assert not staged["vars_file"].exists()

    def test_a_missing_cluster_stack_still_cleans_up_artifacts(self, staged, monkeypatch, capsys):
        """A pre-delete describe-cluster that raises NotFoundException means
        the stack is already gone; teardown must still run to clear IAM, S3,
        and local state, and must still exit 0."""
        pc_lib = _FakePcLib(describe_sequence=[NotFoundException("gone")])
        monkeypatch.setitem(sys.modules, "pcluster.lib", pc_lib)
        with pytest.raises(SystemExit) as exc:
            staged["mod"].main()
        assert exc.value.code == 0
        assert "was not found" in capsys.readouterr().out
        assert not staged["serial_file"].exists()
        assert not staged["vars_file"].exists()

    def test_a_bad_request_on_delete_is_tolerated(self, staged, monkeypatch):
        """delete_cluster raising BadRequestException (e.g. an already
        mid-delete stack) must not abort teardown -- the wait loop is what
        actually determines the outcome."""
        pc_lib = _FakePcLib(delete_raises=BadRequestException("already deleting"))
        monkeypatch.setitem(sys.modules, "pcluster.lib", pc_lib)
        with pytest.raises(SystemExit) as exc:
            staged["mod"].main()
        assert exc.value.code == 0


class TestTeardownFailureLeavesStateForRetry:
    def test_delete_failed_preserves_state_and_exits_nonzero(self, staged, monkeypatch, capsys):
        pc_lib = _FakePcLib(describe_sequence=[{"clusterStatus": "DELETE_FAILED"}])
        monkeypatch.setitem(sys.modules, "pcluster.lib", pc_lib)
        with pytest.raises(SystemExit) as exc:
            staged["mod"].main()
        assert exc.value.code == 1
        assert staged["serial_file"].exists()
        assert staged["vars_file"].exists()
        out = capsys.readouterr().out
        assert "DELETE_FAILED" in out

    def test_unconfirmed_delete_preserves_state_and_exits_nonzero(self, staged, monkeypatch, capsys):
        """Every describe-cluster call answers DELETE_IN_PROGRESS forever --
        the wait loop exhausts its retries without a terminal state, exactly
        the wait-timeout case CLAUDE.md documents. Runs in well under a
        second because time.sleep is stubbed in the staged fixture."""
        pc_lib = _FakePcLib(describe_sequence=[{"clusterStatus": "DELETE_IN_PROGRESS"}])
        monkeypatch.setitem(sys.modules, "pcluster.lib", pc_lib)
        with pytest.raises(SystemExit) as exc:
            staged["mod"].main()
        assert exc.value.code == 1
        assert staged["serial_file"].exists()
        assert staged["vars_file"].exists()
        out = capsys.readouterr().out
        assert "was never confirmed" in out

    def test_orphaned_resources_exit_nonzero(self, staged, monkeypatch, capsys):
        """The stack itself deletes cleanly (cf_delete_confirmed=True, so
        the credential steps -- including removing cluster_data_dir, which
        holds the serial file -- do run), but one cleanup step fails. The
        vars file lives outside cluster_data_dir and survives; the serial
        file does not, since it was inside the directory the credential
        steps already removed by the time the orphan is detected."""
        monkeypatch.setattr(
            staged["mod"].boto3, "client",
            lambda *a, **k: _FakeAwsClient(fail="delete_role"),
        )
        with pytest.raises(SystemExit) as exc:
            staged["mod"].main()
        assert exc.value.code == 1
        assert not staged["serial_file"].exists()
        assert staged["vars_file"].exists()
        out = capsys.readouterr().out
        assert "resource(s)" in out


class TestTeardownPreflight:
    def test_missing_serial_file_aborts_before_any_aws_call(self, staged):
        staged["serial_file"].unlink()
        with pytest.raises(SystemExit) as exc:
            staged["mod"].main()
        assert exc.value.code == 1
        assert staged["pc_lib"].delete_calls == []

    def test_missing_vars_file_aborts_before_any_aws_call(self, staged):
        staged["vars_file"].unlink()
        with pytest.raises(SystemExit) as exc:
            staged["mod"].main()
        assert exc.value.code == 1
        assert staged["pc_lib"].delete_calls == []

    def test_invalid_cluster_name_aborts_before_any_aws_call(self, staged, monkeypatch):
        evil = "../../etc/passwd"
        serial_dir = staged["root"] / "active_clusters" / evil
        serial_dir.mkdir(parents=True, exist_ok=True)
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
        assert staged["pc_lib"].delete_calls == []

    def test_invalid_cluster_owner_aborts_before_any_aws_call(self, staged, monkeypatch):
        monkeypatch.setattr(
            sys, "argv",
            ["kill_pcluster.py", "-A", AZ, "-N", CLUSTER, "-O", "Bad Owner!"],
        )
        with pytest.raises(SystemExit) as exc:
            staged["mod"].main()
        assert "cluster_owner must contain only lowercase" in str(exc.value.code)
        assert staged["pc_lib"].delete_calls == []

    def test_unknown_az_aborts_before_any_aws_call(self, staged, monkeypatch):
        monkeypatch.setattr(
            staged["mod"].boto3, "client",
            lambda *a, **k: types.SimpleNamespace(
                describe_availability_zones=lambda ZoneNames: {"AvailabilityZones": []}
            ),
        )
        with pytest.raises(SystemExit) as exc:
            staged["mod"].main()
        assert exc.value.code == 1
        assert staged["pc_lib"].delete_calls == []


class TestTeardownAbortWindow:
    def test_operator_is_given_an_abort_window_before_deletion(self, staged, monkeypatch):
        seen = {}

        def _abort(sleep_time, *args, **kwargs):
            seen["sleep_time"] = sleep_time
            seen["delete_ran"] = staged["pc_lib"].delete_calls != []

        monkeypatch.setattr(staged["mod"], "ctrlC_Abort", _abort)
        with pytest.raises(SystemExit):
            staged["mod"].main()
        assert seen["delete_ran"] is False
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


class TestClusterLockDuringTeardown:
    """Since Workstream 4's first slice, core_delete_cluster locks via the
    S3-backed distributed lock (pcluster_core.s3_acquire_cluster_lock), not
    the old local mkdir lock -- the .build.lock path this class used to
    check no longer exists at all. staged["clients"]["s3"] is the single
    fake S3 client every boto3.client("s3", ...) call in main() shares (see
    the staged fixture's own comment on why), so it is both where
    core_delete_cluster's own lock lands and the handle these tests use to
    pre-populate or inspect it."""

    def _locks_bucket(self):
        return pcluster_core._derive_locks_bucket(aws_account_id="123456789012", region="us-east-1")

    def test_lock_is_released_after_a_successful_teardown(self, staged):
        with pytest.raises(SystemExit) as exc:
            staged["mod"].main()
        assert exc.value.code == 0
        s3 = staged["clients"]["s3"]
        assert pcluster_core._lock_key(CLUSTER) not in s3._objects

    def test_lock_is_released_after_a_failed_delete(self, staged, monkeypatch):
        pc_lib = _FakePcLib(describe_sequence=[{"clusterStatus": "DELETE_FAILED"}])
        monkeypatch.setitem(sys.modules, "pcluster.lib", pc_lib)
        with pytest.raises(SystemExit):
            staged["mod"].main()
        s3 = staged["clients"]["s3"]
        assert pcluster_core._lock_key(CLUSTER) not in s3._objects

    def test_a_lock_already_held_by_a_build_aborts_before_any_aws_call(self, staged):
        s3 = staged["mod"].boto3.client("s3", region_name="us-east-1")
        pcluster_core.s3_acquire_cluster_lock(
            s3, locks_bucketname=self._locks_bucket(), cluster_name=CLUSTER,
            command="make_pcluster.py -N killme (other process)",
        )
        with pytest.raises(SystemExit) as exc:
            staged["mod"].main()
        assert "already running" in str(exc.value.code)
        assert staged["pc_lib"].delete_calls == []
        assert staged["serial_file"].exists(), "must not delete state while another process holds the lock"
        assert staged["vars_file"].exists()


class TestTeardownWaitFalse:
    """Workstream 4: core_delete_cluster gained a `wait` parameter
    (default True, so the CLI shim is unchanged). wait=False initiates
    the stack delete and returns without waiting -- for a future MCP tool
    wrapper, since a single tool call cannot block for a 5-10 minute
    teardown."""

    def _run(self, staged):
        return pcluster_core.core_delete_cluster(
            cluster_name=CLUSTER, cluster_owner=OWNER, region="us-east-1",
            repo_root=str(staged["root"]), delete_s3_bucketname="true",
            debug_mode=False, wait=False,
        )

    def test_delete_is_still_initiated(self, staged):
        self._run(staged)
        assert staged["pc_lib"].delete_calls == [(CLUSTER, "us-east-1")]

    def test_local_state_is_preserved(self, staged):
        """The serial and vars files are what a follow-up run needs to
        finish teardown -- destroying them here would strand a
        half-deleted cluster with no way to complete the job."""
        self._run(staged)
        assert staged["serial_file"].exists()
        assert staged["vars_file"].exists()

    def test_it_reports_success_because_the_requested_action_succeeded(self, staged):
        """The caller asked to initiate a delete without waiting, and that
        is what happened. Reporting failure would make an MCP caller retry
        a delete that is already correctly in flight."""
        result = self._run(staged)
        assert result.success is True
        assert result.exit_code == 0

    def test_the_operator_is_told_nothing_was_cleaned_up(self, staged, capsys):
        """The dangerous misreading of a success here is 'teardown done'.
        The message has to say plainly that it is not."""
        self._run(staged)
        out = capsys.readouterr().out
        assert "NOT waited on" in out
        assert "Nothing has been cleaned up yet" in out
        assert "kill_pcluster.py" in out

    def test_wait_defaults_to_true(self, staged):
        """The CLI shim passes no wait at all, so the default is what
        preserves today's blocking teardown."""
        import inspect

        sig = inspect.signature(pcluster_core.core_delete_cluster)
        assert sig.parameters["wait"].default is True


class TestTheDeleteWaitDropsOneKnownBenignUpstreamError:
    """PCluster logs every AWSClientError at ERROR before raising it, even
    the ones its own caller handles. During a teardown, describe_cluster
    fetches the cluster config from S3 by object version; once the bucket's
    objects are going away that get_object fails, so a *working* delete
    printed "Encountered error when performing boto3 call in get_object:
    The specified version does not exist." on every poll and looked broken.

    Silencing someone else's error stream is how a real failure gets
    hidden, so the suppression is narrow on three axes and each is pinned
    below: one logger, one message, the wait's duration only.
    """

    _NOISE = "The specified version does not exist"

    def _logger(self):
        import logging

        return logging.getLogger("pcluster.aws.common")

    def _records(self, caplog, fn):
        import logging

        with caplog.at_level(logging.ERROR, logger="pcluster.aws.common"):
            fn()
        return [r.getMessage() for r in caplog.records]

    def test_the_benign_message_is_dropped_inside_the_wait(self, caplog):
        import pcluster_core

        def emit():
            with pcluster_core.quiet_missing_config_version_noise():
                self._logger().error(
                    "Encountered error when performing boto3 call in %s: %s",
                    "get_object", self._NOISE + ".",
                )

        assert self._records(caplog, emit) == []

    def test_every_other_error_from_that_logger_still_prints(self, caplog):
        """The axis that matters most: a different get_object failure, or a
        denied delete_stack, must not be swallowed with it."""
        import pcluster_core

        def emit():
            with pcluster_core.quiet_missing_config_version_noise():
                self._logger().error(
                    "Encountered error when performing boto3 call in %s: %s",
                    "delete_stack", "AccessDenied",
                )

        assert any("AccessDenied" in m for m in self._records(caplog, emit))

    def test_the_filter_is_removed_when_the_wait_ends(self, caplog):
        import pcluster_core

        def emit():
            with pcluster_core.quiet_missing_config_version_noise():
                pass
            self._logger().error(
                "Encountered error when performing boto3 call in %s: %s",
                "get_object", self._NOISE + ".",
            )

        assert any(self._NOISE in m for m in self._records(caplog, emit))

    def test_the_filter_is_removed_even_when_the_wait_raises(self):
        """A delete that fails mid-wait must not leave the operator's
        process permanently deaf to this message."""
        import pcluster_core

        before = list(self._logger().filters)
        with pytest.raises(RuntimeError):
            with pcluster_core.quiet_missing_config_version_noise():
                raise RuntimeError("delete blew up")
        assert list(self._logger().filters) == before

    def test_no_other_logger_is_touched(self, caplog):
        """Scoped to pcluster.aws.common, not the root -- a root filter
        would silence this string from anywhere, including our own code."""
        import logging

        import pcluster_core

        other = logging.getLogger("pcluster.api.something_else")
        with caplog.at_level(logging.ERROR):
            with pcluster_core.quiet_missing_config_version_noise():
                other.error("boto3 call failed: %s", self._NOISE + ".")
        assert any(self._NOISE in r.getMessage() for r in caplog.records)

    def test_the_delete_wait_is_actually_wrapped(self):
        """The filter is worthless if the wait does not use it, and that is
        invisible at runtime -- the noise simply keeps printing."""
        import ast
        import inspect

        import pcluster_core

        src = inspect.getsource(pcluster_core.core_delete_cluster)
        tree = ast.parse(src.lstrip())
        wrapped = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.With):
                continue
            if "quiet_missing_config_version_noise" not in ast.dump(node.items[0]):
                continue
            if "run_cluster_delete_and_classify" in ast.dump(node):
                wrapped = True
        assert wrapped, (
            "run_cluster_delete_and_classify must run inside "
            "quiet_missing_config_version_noise()"
        )

    def test_the_live_listing_is_covered_too(self):
        """Scoping the filter to the delete wait alone was narrower than
        the problem: `list_pcluster.py --live` describes a cluster that is
        mid-delete and hits the same missing config version, printing the
        error above the table. Observed."""
        import ast
        import inspect

        import pcluster_core

        src = inspect.getsource(pcluster_core._live_status)
        tree = ast.parse(src.lstrip())
        wrapped = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.With):
                continue
            if "quiet_missing_config_version_noise" not in ast.dump(node.items[0]):
                continue
            if "_describe_cluster_json" in ast.dump(node):
                wrapped = True
        assert wrapped, (
            "_live_status must describe inside quiet_missing_config_version_noise()"
        )


class TestTeardownFinalizeOnly:
    """finalize_only=True is the other half of wait=False.

    wait=False returns before every teardown step, so on its own it leaves
    the IAM policies, the S3 bucket, the credentials and the shared-store
    record behind with nothing able to remove them -- a truncated
    capability, where CLAUDE.md's 900s-ceiling bullet calls for a
    decomposed one. This half finishes the job, and the property that
    makes it safe for a caller that cannot block is that it is structurally
    incapable of waiting, not that it promises not to.
    """

    def _finalize(self, staged, **kw):
        return pcluster_core.core_delete_cluster(
            cluster_name=CLUSTER, cluster_owner=OWNER, region="us-east-1",
            repo_root=str(staged["root"]), delete_s3_bucketname="true",
            debug_mode=False, finalize_only=True, **kw
        )

    def test_it_never_initiates_a_delete(self, staged):
        """The stack is already gone; calling delete-cluster again would at
        best be a no-op and at worst re-enter a delete on a rebuilt name."""
        self._finalize(staged)
        assert staged["pc_lib"].delete_calls == []

    def test_it_reaches_the_teardown_and_removes_local_state(self, staged):
        """The whole point: what wait=False left behind is now gone."""
        result = self._finalize(staged)
        assert result.success is True
        assert result.exit_code == 0
        assert not staged["serial_file"].exists()
        assert not staged["vars_file"].exists()

    def test_it_cannot_wait(self, staged, monkeypatch):
        """The non-blocking guarantee is structural. A stack still in
        DELETE_IN_PROGRESS must cost exactly one describe -- if this path
        could ever reach the retry loop it would block for up to 40
        minutes, which under a 900s function timeout is a kill mid-teardown
        with the cluster lock held by a dead process.

        A slept-through retry loop is invisible in the return value (the
        refusal below is identical either way), so this asserts on the call
        count, which is the only thing that can see it.
        """
        pc_lib = _FakePcLib(describe_sequence=[{"clusterStatus": "DELETE_IN_PROGRESS"}])
        monkeypatch.setitem(sys.modules, "pcluster.lib", pc_lib)
        slept = []
        monkeypatch.setattr(pcluster_core.time, "sleep", lambda s: slept.append(s))
        self._finalize(staged)
        # One for core_delete_cluster's own opening describe, one for the gate.
        assert len(pc_lib.describe_calls) == 2
        assert slept == []

    @pytest.mark.parametrize("status", ["DELETE_IN_PROGRESS", "DELETE_FAILED", "CREATE_COMPLETE"])
    def test_it_refuses_unless_the_stack_is_confirmed_gone(self, staged, monkeypatch, status):
        monkeypatch.setitem(
            sys.modules, "pcluster.lib",
            _FakePcLib(describe_sequence=[{"clusterStatus": status}]),
        )
        result = self._finalize(staged)
        assert result.success is False
        assert result.exit_code == 1

    @pytest.mark.parametrize("status", ["DELETE_IN_PROGRESS", "DELETE_FAILED", "CREATE_COMPLETE"])
    def test_a_refusal_destroys_nothing(self, staged, monkeypatch, status):
        """Refusing has to mean refusing. Tearing IAM or S3 out from under
        a stack that still exists is how a DELETE_FAILED is manufactured --
        the same reasoning the wait=False path already documents -- and the
        local files are what a later finalize needs to try again."""
        monkeypatch.setitem(
            sys.modules, "pcluster.lib",
            _FakePcLib(describe_sequence=[{"clusterStatus": status}]),
        )
        self._finalize(staged)
        assert staged["serial_file"].exists()
        assert staged["vars_file"].exists()

    def test_the_refusal_explains_the_state_rather_than_naming_a_constant(
        self, staged, monkeypatch, capsys,
    ):
        """The gate reuses the wait loop, so a stack that merely still
        exists comes back as `TIMED_OUT` — accurate inside that loop, and
        meaningless to whoever reads the refusal. Printing the raw constant
        told a live operator nothing; accepting either spelling is what let
        that through the first time.
        """
        monkeypatch.setitem(
            sys.modules, "pcluster.lib",
            _FakePcLib(describe_sequence=[{"clusterStatus": "DELETE_IN_PROGRESS"}]),
        )
        self._finalize(staged)
        out = capsys.readouterr().out
        assert "confirmed gone" in out
        assert "the stack still exists" in out
        assert "TIMED_OUT" not in out, "internal constant leaked to the operator"

    def test_delete_failed_is_named_verbatim(self, staged, monkeypatch, capsys):
        """The opposite of the rule above: DELETE_FAILED is the string the
        operator greps the CloudFormation console for, so it is passed
        through rather than translated."""
        monkeypatch.setitem(
            sys.modules, "pcluster.lib",
            _FakePcLib(describe_sequence=[{"clusterStatus": "DELETE_FAILED"}]),
        )
        self._finalize(staged)
        out = capsys.readouterr().out
        assert "DELETE_FAILED" in out
        assert "re-run the delete" in out

    def test_no_banner_claims_the_stack_is_gone_before_the_gate_looks(
        self, staged, monkeypatch, capsys,
    ):
        """The banner printed "The stack is already gone" ahead of the only
        call that could know, and against a live DELETE_IN_PROGRESS that
        was simply false — the operator saw it asserted and then denied
        four lines later."""
        monkeypatch.setitem(
            sys.modules, "pcluster.lib",
            _FakePcLib(describe_sequence=[{"clusterStatus": "DELETE_IN_PROGRESS"}]),
        )
        self._finalize(staged)
        out = capsys.readouterr().out
        assert "gone" not in out.split("*** ERROR ***")[0], (
            "a refused finalize must not announce the teardown it refused"
        )

    def test_the_banner_appears_once_the_gate_passes(self, staged, capsys):
        """Vacuity guard: 'no banner' must not be satisfied by deleting it."""
        self._finalize(staged)
        out = capsys.readouterr().out
        assert "Finalizing teardown: killme" in out
        assert "The stack is confirmed gone" in out

    def test_a_failed_describe_is_not_a_deleted_stack(self, staged, monkeypatch):
        """This repo's standing rule, and the one an over-eager gate gets
        wrong: an expired token or a throttle must not read as 'gone' and
        authorize the destruction of the credentials. The waiting path
        re-raises here; so must this one."""
        boom = RuntimeError("ExpiredToken")
        monkeypatch.setitem(
            sys.modules, "pcluster.lib",
            _FakePcLib(describe_sequence=[boom]),
        )
        with pytest.raises(RuntimeError):
            self._finalize(staged)
        assert staged["serial_file"].exists()

    def test_a_cluster_already_absent_finalizes(self, staged, monkeypatch):
        """NotFoundException is the ordinary case -- the stack is gone, which
        is exactly the precondition -- not an error."""
        monkeypatch.setitem(
            sys.modules, "pcluster.lib",
            _FakePcLib(describe_sequence=[NotFoundException("gone")]),
        )
        result = self._finalize(staged)
        assert result.success is True
        assert not staged["vars_file"].exists()

    def test_the_results_sync_is_skipped(self, staged, monkeypatch):
        """The sync reads off the head node over ssh and runs before the
        delete on the waiting path. On this path the stack that owned that
        node is gone by definition, so attempting it can only produce a
        warning about results it was never going to reach."""
        staged["vars_file"].write_text(_vars_yaml(enable_hpc_benchmarks="true"))
        called = []
        monkeypatch.setattr(
            pcluster_core, "_sync_performance_results_to_s3",
            lambda **kw: called.append(kw) or (True, ""),
        )
        self._finalize(staged)
        assert called == []

    def test_the_waiting_path_still_syncs(self, staged, monkeypatch):
        """Vacuity guard for the test above: 'skipped on finalize' must not
        be satisfied by the sync having been deleted outright."""
        staged["vars_file"].write_text(_vars_yaml(enable_hpc_benchmarks="true"))
        called = []
        monkeypatch.setattr(
            pcluster_core, "_sync_performance_results_to_s3",
            lambda **kw: called.append(kw) or (True, ""),
        )
        pcluster_core.core_delete_cluster(
            cluster_name=CLUSTER, cluster_owner=OWNER, region="us-east-1",
            repo_root=str(staged["root"]), delete_s3_bucketname="true",
            debug_mode=False, wait=False,
        )
        assert len(called) == 1

    def test_there_is_exactly_one_teardown_body(self):
        """What makes the decomposition a decomposition rather than a second
        implementation: both modes fall into the *same* steps.

        Two copies would drift -- a step added to the waiting path only
        would leave a resource that finalize can never clean up, which is
        precisely the class of defect this whole change exists to fix, and
        no behavioral test comparing the two modes would catch it if the
        new step were simply absent from both fixtures.
        """
        import ast
        import inspect

        src = inspect.getsource(pcluster_core.core_delete_cluster)
        tree = ast.parse(src.lstrip())
        counts = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                counts[node.func.id] = counts.get(node.func.id, 0) + 1
        for step in (
            "run_credential_teardown_steps",
            "run_resource_teardown_steps",
            "delete_cluster_record_step",
            "delete_cluster_config_step",
            "_delete_sns_topic_step",
        ):
            assert counts.get(step) == 1, (
                f"{step} is called {counts.get(step)} times; the teardown body "
                f"must be shared by the waiting and finalize paths, not duplicated"
            )

    def test_finalize_only_defaults_to_false(self, staged):
        """The CLI shim passes neither wait nor finalize_only, so the
        defaults are what preserve today's blocking teardown."""
        import inspect

        sig = inspect.signature(pcluster_core.core_delete_cluster)
        assert sig.parameters["finalize_only"].default is False

    @pytest.mark.parametrize("status", ["DELETE_IN_PROGRESS", "DELETE_FAILED", "CREATE_COMPLETE"])
    def test_a_refusal_runs_no_teardown_steps(self, staged, monkeypatch, status):
        """The return value cannot see this, and DELETE_FAILED is why.

        Letting DELETE_FAILED past the gate still exits non-zero -- the
        waiting path's own cf_delete_failed branch produces exactly that --
        so every assertion on success/exit_code passes while the IAM role
        and the S3 bucket are stripped from a stack that still exists. The
        waiting path does that on purpose, having just tried the delete
        itself; arriving here means an earlier delete_cluster failed, and
        the answer to that is to re-run the delete, not to scavenge the
        resources it still depends on.
        """
        monkeypatch.setitem(
            sys.modules, "pcluster.lib",
            _FakePcLib(describe_sequence=[{"clusterStatus": status}]),
        )
        ran = []
        for step in ("run_credential_teardown_steps", "run_resource_teardown_steps"):
            monkeypatch.setattr(
                pcluster_core, step,
                lambda _s=step, **kw: ran.append(_s) or [],
            )
        self._finalize(staged)
        assert ran == []


class TestTheStoreSuppliesWhatOnlyTheBuilderHad:
    """The serial file and the vars file both live only on the machine that
    built the cluster, so core_delete_cluster aborted on each in turn and a
    remote teardown could never run -- on values the published record has
    carried all along. Local still wins, the same order as
    _read_cluster_record and for the same reason.

    Found live: MCP delete_cluster against a Lambda returned
    "Missing cluster_serial_number_file: /var/task/active_clusters/..."
    while s3://<locks-bucket>/vars/<name>.json held the record.
    """

    def test_the_store_record_is_returned_whole(self, monkeypatch):
        """One read serves both the serial and the vars, rather than two
        round trips for one object."""
        record = {"serial": SERIAL, "aws_account_id": "1234", "az": "us-east-1a"}
        monkeypatch.setattr(pcluster_core, "get_cluster_record", lambda s3, **kw: record)
        monkeypatch.setattr(pcluster_core.boto3, "client", lambda *a, **k: _StsOnly())
        assert pcluster_core._cluster_record_from_store(
            CLUSTER, region="us-east-1"
        ) == record

    def test_an_absent_record_is_none_not_an_exception(self, monkeypatch):
        monkeypatch.setattr(pcluster_core, "get_cluster_record", lambda s3, **kw: None)
        monkeypatch.setattr(pcluster_core.boto3, "client", lambda *a, **k: _StsOnly())
        assert pcluster_core._cluster_record_from_store(
            "nope", region="us-east-1"
        ) is None

    def test_an_unreachable_store_is_none_not_a_traceback(self, monkeypatch):
        """Teardown's own error path says what is missing; a store that
        cannot be reached must not surface as a traceback in front of it."""
        def _boom(*a, **k):
            raise RuntimeError("no credentials")

        monkeypatch.setattr(pcluster_core.boto3, "client", _boom)
        assert pcluster_core._cluster_record_from_store(
            "x", region="us-east-1"
        ) is None

    def test_it_does_not_consult_local_state(self, monkeypatch):
        """Store-only by design: the caller reaches this exactly when the
        local files are absent, so re-reading local state here would only
        re-answer a question already asked."""
        def _should_not_run(*a, **k):
            raise AssertionError("_read_local_vars_file must not be consulted here")

        monkeypatch.setattr(pcluster_core, "_read_local_vars_file", _should_not_run)
        monkeypatch.setattr(
            pcluster_core, "get_cluster_record", lambda s3, **kw: {"serial": "s-1"},
        )
        monkeypatch.setattr(pcluster_core.boto3, "client", lambda *a, **k: _StsOnly())
        assert pcluster_core._cluster_record_from_store(
            "x", region="us-east-1"
        ) == {"serial": "s-1"}


class TestTeardownRunsOffTheStoreAlone:
    """The wiring, not the helper. Tests covering _cluster_record_from_store
    in isolation all passed with the fallback reverted -- nothing drove
    core_delete_cluster down the no-local-file path, which is the only path
    that was broken. These assert on the real function.
    """

    def _record(self):
        return {
            "serial": SERIAL, "cluster_name": CLUSTER, "cluster_owner": OWNER,
            "region": "us-east-1", "aws_account_id": "183295445014",
            "az": "us-east-1a", "ec2_iam_policy": "pclustermaker-policy-x",
            "ec2_iam_role": "pclustermaker-role-x", "ec2_keypair": "kp",
            "ec2_user": "ubuntu", "ec2_user_home": "/home/ubuntu",
            "ssh_keypair": "kp.pem", "ssh_secret_name": "parallelcluster/x",
            "s3_bucketname": "parallelclustermaker-x",
            "fsx_hydration_iam_policy": "", "results_bucketname": "",
            "enable_monitoring": "false", "enable_external_nfs": "false",
            "enable_fsx_hydration": "false", "enable_hpc_benchmarks": "false",
        }

    def _run(self, staged):
        return pcluster_core.core_delete_cluster(
            cluster_name=CLUSTER, cluster_owner=OWNER, region="us-east-1",
            repo_root=str(staged["root"]), delete_s3_bucketname="true",
            debug_mode=False, wait=False,
        )

    def test_a_teardown_with_neither_local_file_still_deletes_the_stack(
        self, staged, monkeypatch,
    ):
        """The whole point: a machine that did not build the cluster has
        neither file, and that is exactly when teardown must still work."""
        staged["serial_file"].unlink()
        staged["vars_file"].unlink()
        rec = self._record()
        monkeypatch.setattr(
            pcluster_core, "_cluster_record_from_store", lambda name, **kw: rec,
        )
        self._run(staged)
        assert staged["pc_lib"].delete_calls == [(CLUSTER, "us-east-1")]

    def test_the_store_values_are_what_teardown_acts_on(
        self, staged, monkeypatch, capsys,
    ):
        """Not merely "it did not abort": every resource name is built from
        the serial, so the store's value has to be the one in play."""
        staged["serial_file"].unlink()
        staged["vars_file"].unlink()
        rec = self._record()
        monkeypatch.setattr(
            pcluster_core, "_cluster_record_from_store", lambda name, **kw: rec,
        )
        self._run(staged)
        assert SERIAL in capsys.readouterr().out

    def test_one_store_read_serves_both_files(self, staged, monkeypatch):
        """Two lookups for one object is a second round trip and a second
        chance for the two halves to disagree."""
        calls = []
        rec = self._record()

        def _counted(name, **kw):
            calls.append(name)
            return rec

        staged["serial_file"].unlink()
        staged["vars_file"].unlink()
        monkeypatch.setattr(pcluster_core, "_cluster_record_from_store", _counted)
        self._run(staged)
        assert calls == [CLUSTER]

    def test_no_local_files_and_no_record_still_aborts(self, staged, monkeypatch):
        """The vacuity guard: the fix must not become "proceed regardless".
        A teardown that cannot name the serial must not run against a blank
        one."""
        staged["serial_file"].unlink()
        staged["vars_file"].unlink()
        monkeypatch.setattr(
            pcluster_core, "_cluster_record_from_store", lambda name, **kw: None,
        )
        result = self._run(staged)
        assert result.success is False
        assert result.exit_code == 1
        assert staged["pc_lib"].delete_calls == []

    def test_a_record_without_a_serial_aborts(self, staged, monkeypatch):
        """A record whose serial is blank must not resolve to "" and let
        teardown proceed -- every resource name is built from it."""
        staged["serial_file"].unlink()
        rec = dict(self._record(), serial="")
        monkeypatch.setattr(
            pcluster_core, "_cluster_record_from_store", lambda name, **kw: rec,
        )
        result = self._run(staged)
        assert result.success is False
        assert staged["pc_lib"].delete_calls == []

    def test_a_record_predating_the_teardown_fields_is_refused(
        self, staged, monkeypatch, capsys,
    ):
        """Every teardown field defaults to "", so an older record loads
        cleanly and would run cleanup against blank policy and role names --
        skipping steps silently and leaving orphans while reporting success.
        Refusing names the cause and the remedy instead."""
        staged["serial_file"].unlink()
        staged["vars_file"].unlink()
        legacy = {
            "serial": SERIAL, "cluster_name": CLUSTER, "cluster_owner": OWNER,
            "region": "us-east-1", "ec2_keypair": "kp", "ec2_user": "ubuntu",
            "s3_bucketname": "parallelclustermaker-x", "enable_monitoring": "false",
        }
        monkeypatch.setattr(
            pcluster_core, "_cluster_record_from_store", lambda name, **kw: legacy,
        )
        result = self._run(staged)
        assert result.success is False
        assert staged["pc_lib"].delete_calls == []
        out = capsys.readouterr().out
        assert "predates teardown" in out
        assert "ec2_iam_policy" in out

    def test_the_refusal_does_not_fire_on_a_current_record(self, staged, monkeypatch):
        """The vacuity guard: the check must not refuse every store-driven
        teardown, which would undo the whole extension."""
        staged["serial_file"].unlink()
        staged["vars_file"].unlink()
        rec = self._record()
        monkeypatch.setattr(
            pcluster_core, "_cluster_record_from_store", lambda name, **kw: rec,
        )
        self._run(staged)
        assert staged["pc_lib"].delete_calls == [(CLUSTER, "us-east-1")]

    def test_local_files_are_still_preferred(self, staged, monkeypatch):
        """Local wins, the same order as _read_cluster_record. The store is
        not consulted at all when both files are there."""
        def _should_not_run(*a, **k):
            raise AssertionError("the store must not be consulted when local files exist")

        monkeypatch.setattr(
            pcluster_core, "_cluster_record_from_store", _should_not_run,
        )
        self._run(staged)
        assert staged["pc_lib"].delete_calls == [(CLUSTER, "us-east-1")]


class _StsOnly:
    """Stands in for both the s3 and sts clients boto3.client would return."""

    def get_caller_identity(self):
        return {"Account": "183295445014"}


class TestAnAbsentLocalKeyIsNotAnOrphan:
    """A remote teardown has no local .pem by construction. The step caught
    FileNotFoundError, but on a read-only filesystem the kernel rejects the
    write before discovering the file is not there, so it raises EROFS
    instead -- and a purely local step then appeared in the orphan report,
    telling the operator to remove by hand a file that was never on that
    machine. Observed live on the deployed stack-mutation Lambda:
    "[Errno 30] Read-only file system: 'storecert-...pem'".

    EROFS is raised directly rather than simulated with chmod: a read-only
    *directory* is a different thing from a read-only *filesystem*, and on
    macOS unlinking an absent file inside one raises FileNotFoundError,
    which the step already handled. A chmod-based test passes with the fix
    reverted and proves nothing.
    """

    def test_a_read_only_filesystem_is_not_a_failure(self, tmp_path, monkeypatch):
        def _erofs(path):
            raise OSError(errno.EROFS, "Read-only file system", path)

        monkeypatch.setattr(os, "remove", _erofs)
        step = pcluster_core._delete_local_ssh_key_step(str(tmp_path / "absent.pem"))
        assert step.succeeded is True

    def test_an_existing_key_is_still_removed(self, tmp_path):
        """The vacuity guard: the fix must not become "never delete it"."""
        pem = tmp_path / "real.pem"
        pem.write_text("KEY")
        step = pcluster_core._delete_local_ssh_key_step(str(pem))
        assert step.succeeded is True
        assert not pem.exists()

    def test_a_genuine_failure_on_a_real_key_is_still_reported(
        self, tmp_path, monkeypatch,
    ):
        """The absence check must not swallow a failure to remove a key that
        is actually there."""
        pem = tmp_path / "stuck.pem"
        pem.write_text("KEY")

        def _denied(path):
            raise PermissionError("denied")

        monkeypatch.setattr(os, "remove", _denied)
        step = pcluster_core._delete_local_ssh_key_step(str(pem))
        assert step.succeeded is False
        assert "denied" in step.detail
