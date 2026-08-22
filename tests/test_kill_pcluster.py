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
