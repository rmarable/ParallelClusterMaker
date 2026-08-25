"""Tests for check_pcluster.py helper functions."""

import json
import os
import subprocess
import sys
import types

import pytest

# Import module-level — venv guard passes when running under .venv/bin/python
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import check_pcluster as chk
import pcluster_core


def _proc(rc=0, stdout="", stderr=""):
    return types.SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# check_cfn_status
# ---------------------------------------------------------------------------

class TestCheckCfnStatus:
    def test_create_complete(self, monkeypatch):
        monkeypatch.setattr(pcluster_core, "_describe_cluster_json",
                            lambda c, r: {
            "clusterStatus": "CREATE_COMPLETE",
            "cloudFormationStackStatus": "CREATE_COMPLETE",
            "headNode": {"publicIpAddress": "1.2.3.4"},
        })
        ok, msg, ip = chk.check_cfn_status("mycluster", "us-east-1", "pcluster")
        assert ok is True
        assert "CREATE_COMPLETE" in msg
        assert ip == "1.2.3.4"

    def test_non_create_complete_returns_fail(self, monkeypatch):
        monkeypatch.setattr(pcluster_core, "_describe_cluster_json",
                            lambda c, r: {
            "clusterStatus": "UPDATE_FAILED",
            "cloudFormationStackStatus": "UPDATE_ROLLBACK_COMPLETE",
            "headNode": {"publicIpAddress": "1.2.3.4"},
        })
        ok, msg, ip = chk.check_cfn_status("mycluster", "us-east-1", "pcluster")
        assert ok is False
        assert "UPDATE_FAILED" in msg

    def test_private_ip_fallback(self, monkeypatch):
        monkeypatch.setattr(pcluster_core, "_describe_cluster_json",
                            lambda c, r: {
            "clusterStatus": "CREATE_COMPLETE",
            "cloudFormationStackStatus": "CREATE_COMPLETE",
            "headNode": {"privateIpAddress": "10.0.0.5"},
        })
        ok, msg, ip = chk.check_cfn_status("mycluster", "us-east-1", "pcluster")
        assert ok is True
        assert ip == "10.0.0.5"

    def test_a_failed_describe_is_reported_not_raised(self, monkeypatch):
        """check_cfn_status is one section of a health report; a failure
        here must not abort the others. The exception type changed with
        the transport (round 48): _describe_cluster_json raises
        PClusterMakerError where the subprocess form raised
        TimeoutExpired/SystemExit."""
        def _raise(c, r):
            raise pcluster_core.PClusterMakerError("describe-cluster timed out")
        monkeypatch.setattr(pcluster_core, "_describe_cluster_json", _raise)
        ok, msg, ip = chk.check_cfn_status("mycluster", "us-east-1", "pcluster")
        assert ok is False
        assert "timed out" in msg

    def test_a_describe_failure_is_reported_with_its_cause(self, monkeypatch):
        """Renamed from test_nonzero_rc: there is no return code any more.
        The property is unchanged -- a failed describe becomes a FAIL whose
        message carries the underlying cause, so the operator is not left
        with a bare "check failed"."""
        def _raise(c, r):
            raise pcluster_core.PClusterMakerError(
                "describe-cluster failed for 'mycluster': AccessDeniedException"
            )
        monkeypatch.setattr(pcluster_core, "_describe_cluster_json", _raise)
        ok, msg, ip = chk.check_cfn_status("mycluster", "us-east-1", "pcluster")
        assert ok is False
        assert "AccessDeniedException" in msg

class TestCheckHeadIp:
    def test_present(self):
        ok, msg = chk.check_head_ip("10.0.0.1")
        assert ok is True
        assert msg == "10.0.0.1"

    def test_empty_string(self):
        ok, msg = chk.check_head_ip("")
        assert ok is False

    def test_none(self):
        ok, msg = chk.check_head_ip(None)
        assert ok is False


# ---------------------------------------------------------------------------
# check_ssh
# ---------------------------------------------------------------------------

class TestCheckSsh:
    def test_success(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **kw: _proc(stdout="OK\n"))
        ok, err = chk.check_ssh("1.2.3.4", "/tmp/key.pem", "ubuntu", 15)
        assert ok is True
        assert err is None

    def test_timeout(self, monkeypatch):
        def _raise(*a, **kw):
            raise subprocess.TimeoutExpired("ssh", 15)
        monkeypatch.setattr(subprocess, "run", _raise)
        ok, err = chk.check_ssh("1.2.3.4", "/tmp/key.pem", "ubuntu", 15)
        assert ok is False
        assert "timed out" in err

    def test_nonzero_rc(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **kw: _proc(rc=255, stderr="Connection refused"))
        ok, err = chk.check_ssh("1.2.3.4", "/tmp/key.pem", "ubuntu", 15)
        assert ok is False
        assert "rc=255" in err


# ---------------------------------------------------------------------------
# check_slurm
# ---------------------------------------------------------------------------


class TestCheckSlurmReadsTheNodeStates:
    """`sinfo` exiting 0 is not evidence that the cluster can run work.

    check_slurm captured stdout and never read it, so it reported PASS on a
    cluster whose entire fleet was `down`, `drained`, or `unk` -- the exact state
    the compute nodes end up in after a bootstrap failure, and the state a health
    check exists to surface. Every failure documented in the root CLAUDE.md ends
    with nodes in one of those states.

    These execute check_slurm's real body against stubbed sinfo output; the
    aggregation tests further down monkeypatch check_slurm wholesale and so never
    exercise a line of it.
    """

    def _call(self, monkeypatch, stdout, rc=0, stderr=""):
        monkeypatch.setattr(
            subprocess, "run", lambda *a, **kw: _proc(rc=rc, stdout=stdout, stderr=stderr)
        )
        return chk.check_slurm("1.2.3.4", "/tmp/key.pem", "ubuntu", 15)

    def test_a_fully_idle_fleet_passes(self, monkeypatch):
        ok, err = self._call(monkeypatch, "2 idle\n")
        assert ok is True
        assert err is None

    def test_a_mixed_and_allocated_fleet_passes(self, monkeypatch):
        ok, err = self._call(monkeypatch, "1 mixed\n3 allocated\n")
        assert ok is True
        assert err is None

    # Each of these is a fleet that cannot run a job while sinfo exits 0.
    @pytest.mark.parametrize(
        "state", ["down", "down*", "drained", "drained*", "draining", "fail",
                  "failing", "unknown", "unk*", "inval", "error", "maint"]
    )
    def test_a_fleet_in_a_bad_state_fails(self, monkeypatch, state):
        ok, err = self._call(monkeypatch, f"4 {state}\n")
        assert ok is False, f"sinfo reporting every node {state} was called healthy"
        assert "no usable nodes" in err
        assert "4" in err, "the message must say how many nodes are unusable"

    def test_a_partially_degraded_fleet_passes_with_a_note(self, monkeypatch):
        """Some capacity is still usable, so this is not a failure -- but the
        operator has to be told, which is the whole reason for reading stdout."""
        ok, err = self._call(monkeypatch, "2 idle\n3 down\n")
        assert ok is True
        assert err is not None
        assert "2" in err and "3" in err

    def test_the_degradation_note_reaches_the_operator(self, monkeypatch, capsys):
        """check_slurm returning the note is only half the fix.

        core_check_cluster_health preserving the note in CheckResult.detail
        (TestCoreCheckClusterHealth.test_slurm_pass_with_degradation_note_is_not_a_failure)
        is the other half -- but printing a bare PASS and discarding detail
        would be indistinguishable from never having read stdout at all, the
        defect this whole class exists for. That's a property of
        check_pcluster.py's own _print_report, not of the aggregation logic,
        so it's exercised here via a report main() receives.
        """
        report = pcluster_core.ClusterHealthReport(
            cluster_name="mycluster",
            checks=[
                pcluster_core.CheckResult("Slurm", "pass", "2 node(s) usable, 3 down/drained"),
            ],
            healthy=True,
        )
        _stage_main(monkeypatch, core_result=report)
        with pytest.raises(SystemExit) as exc:
            chk.main()
        out = capsys.readouterr().out
        assert exc.value.code == 0, "a partially degraded fleet is not a failure"
        assert "3 down/drained" in out

    def test_flag_characters_do_not_hide_a_usable_node(self, monkeypatch):
        """`idle~` is a powered-down but healthy dynamic node.

        Slurm appends flags (*, ~, #, $, ...) to the state name. Comparing the
        raw field would classify every flagged state as unusable and fail a
        healthy cluster whose nodes had scaled to zero.
        """
        ok, err = self._call(monkeypatch, "10 idle~\n")
        assert ok is True

    def test_empty_output_is_a_failure(self, monkeypatch):
        """No partitions at all means slurmctld answered with nothing useful."""
        ok, err = self._call(monkeypatch, "")
        assert ok is False
        assert "no partitions" in err

    def test_an_unparseable_line_is_not_treated_as_healthy(self, monkeypatch):
        ok, err = self._call(monkeypatch, "slurm_load_partitions: Unable to contact\n")
        assert ok is False

    def test_a_nonzero_rc_still_fails(self, monkeypatch):
        ok, err = self._call(monkeypatch, "", rc=1, stderr="slurmctld unreachable")
        assert ok is False
        assert "rc=1" in err
        assert "slurmctld unreachable" in err

    def test_the_command_asks_for_states_not_the_summary(self, monkeypatch):
        """`sinfo -s` cannot express this check.

        Its NODES(A/I/O/T) column is an aggregate count with no state name in it,
        so the output has to carry %T (or -N -l) for any state classification to
        be possible at all.
        """
        seen = {}

        def _run(args, **kw):
            seen["args"] = args
            return _proc(stdout="1 idle\n")

        monkeypatch.setattr(subprocess, "run", _run)
        chk.check_slurm("1.2.3.4", "/tmp/key.pem", "ubuntu", 15)

        # Asserted on the joined command, not on list membership: the probe
        # is now a single shell-quoted script (see _slurm_remote_cmd --
        # sinfo is not on a non-interactive PATH), so "sinfo" is no longer
        # an argv element of its own. The property being checked is
        # unchanged.
        joined = " ".join(seen["args"])
        assert "sinfo" in joined
        assert "%T" in joined, "sinfo is not being asked for node states"
        assert "sinfo -s" not in joined, "-s reports an aggregate, not states"


class TestSinfoClassificationIsSharedNotDuplicated:
    """check_pcluster.py and diagnose_pcluster.py must classify states alike.

    diagnose_pcluster.py had its own _SINFO_OK_STATES set and its own
    flag-stripping. Two copies of a state table drift, and then the health check
    and the diagnostic tool disagree about whether the same cluster is healthy.
    """

    def test_diagnose_uses_the_shared_predicate(self):
        import diagnose_pcluster as diag

        from pcluster_core import _sinfo_state_is_ok

        assert diag._sinfo_state_is_ok is _sinfo_state_is_ok

    def test_diagnose_no_longer_carries_its_own_state_table(self):
        import diagnose_pcluster as diag

        assert not hasattr(diag, "_SINFO_OK_STATES"), (
            "diagnose_pcluster.py has a second copy of the state table"
        )

    # Every flag Slurm appends to a state name, on a state that is otherwise
    # usable. Asserted against the predicate directly and one flag at a time:
    # while `idle~` was itself an entry in _SINFO_OK_STATES, emptying
    # _SINFO_STATE_FLAGS entirely -- so that nothing was ever stripped and every
    # other flagged spelling of a healthy node counted as unusable -- passed the
    # entire suite, because the one test covering flags used the one spelling the
    # table happened to list.
    @pytest.mark.parametrize("flag", ["*", "+", "~", "#", "!", "%", "$", "@", "^", "-"])
    def test_every_state_flag_is_stripped_before_comparison(self, flag):
        from pcluster_core import _sinfo_state_is_ok, _SINFO_OK_STATES

        assert _sinfo_state_is_ok(f"idle{flag}") is True, (
            f"a healthy node flagged {flag!r} was classified unusable"
        )
        assert f"idle{flag}" not in _SINFO_OK_STATES, (
            "the flagged spelling is in the state table, so this test would pass "
            "with no stripping at all"
        )

    def test_a_flag_does_not_rescue_an_unusable_state(self):
        """Stripping must not be so eager it makes every state look fine."""
        from pcluster_core import _sinfo_state_is_ok

        assert _sinfo_state_is_ok("down*") is False
        assert _sinfo_state_is_ok("drain$") is False

    def test_both_agree_on_a_drained_node(self):
        """Differential check on the one output that matters most.

        _format_sinfo annotates; check_slurm decides. They must not disagree
        about which states are a problem.
        """
        import diagnose_pcluster as diag

        annotated = diag._format_sinfo(
            "NODELIST NODES PARTITION STATE\n"
            "q-1 1 compute drained\n"
            "q-2 1 compute idle\n"
        )
        assert "q-1 1 compute drained   <-- not idle" in annotated
        assert "q-2 1 compute idle\n" in annotated + "\n"


# ---------------------------------------------------------------------------
# check_postinstall
# ---------------------------------------------------------------------------

class TestCheckPostinstall:
    def test_marker_present(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _proc(rc=0))
        ok, err = chk.check_postinstall("1.2.3.4", "/tmp/key.pem", "ubuntu", 15)
        assert ok is True
        assert err is None

    def test_marker_absent(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _proc(rc=1))
        ok, err = chk.check_postinstall("1.2.3.4", "/tmp/key.pem", "ubuntu", 15)
        assert ok is False
        assert "marker file absent" in err


# ---------------------------------------------------------------------------
# check_grafana
# ---------------------------------------------------------------------------

class TestCheckGrafana:
    def test_healthy(self, monkeypatch):
        body = '{"commit": "abc", "database": "ok", "version": "9.x"}'
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **kw: _proc(stdout=body))
        ok, err = chk.check_grafana("1.2.3.4", "/tmp/key.pem", "ubuntu", 15)
        assert ok is True
        assert err is None

    def test_database_not_ok(self, monkeypatch):
        body = '{"database": "failing"}'
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **kw: _proc(stdout=body))
        ok, err = chk.check_grafana("1.2.3.4", "/tmp/key.pem", "ubuntu", 15)
        assert ok is False
        assert "unexpected" in err

    def test_curl_fails(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **kw: _proc(rc=7))
        ok, err = chk.check_grafana("1.2.3.4", "/tmp/key.pem", "ubuntu", 15)
        assert ok is False
        assert "rc=7" in err


# ---------------------------------------------------------------------------
# check_s3
# ---------------------------------------------------------------------------

class TestCheckS3:
    def test_success(self, monkeypatch):
        class _FakeS3:
            def head_bucket(self, **kwargs):
                pass

        import boto3
        monkeypatch.setattr(boto3, "client", lambda *a, **kw: _FakeS3())
        ok, err = chk.check_s3("my-bucket", "us-east-1")
        assert ok is True
        assert err is None

    def test_client_error(self, monkeypatch):
        from botocore.exceptions import ClientError

        class _FakeS3:
            def head_bucket(self, **kwargs):
                raise ClientError(
                    {"Error": {"Code": "NoSuchBucket", "Message": "nope"}}, "HeadBucket"
                )

        import boto3
        monkeypatch.setattr(boto3, "client", lambda *a, **kw: _FakeS3())
        ok, err = chk.check_s3("missing-bucket", "us-east-1")
        assert ok is False
        assert "NoSuchBucket" in err


# ---------------------------------------------------------------------------
# core_check_cluster_health -- the aggregation logic itself.
#
# This logic moved from check_pcluster.py's main() into pcluster_core.py as
# part of Workstream 1's core/shim split (docs/parallelclustermaker-mcp-plan.md),
# so it's tested against pcluster_core directly now, not through chk.main().
# Monkeypatching chk.check_cfn_status etc. would silently stop affecting
# anything: core_check_cluster_health resolves those names in pcluster_core's
# own module globals, not check_pcluster's -- the same class of trap already
# hit and fixed for _utc_today/_date_range in cost_pcluster.py's migration.
# ---------------------------------------------------------------------------

_RECORD_KWARGS = {
    "cluster_name": "mycluster",
    "cluster_owner": "rmarable",
    "serial": "202608200001",
    "region": "us-east-1",
    "headnode_instance_type": "c5.xlarge",
    "enable_loginnode": "false",
    "loginnode_instance_type": "",
    "loginnode_count": 0,
    "cpu_instance_types": ["c5.xlarge"],
    "gpu_instance_types": [],
    "enable_cpu_queue": "true",
    "enable_gpu_queue": "false",
    "initial_cpu_queue_size": 2,
    "max_cpu_queue_size": 8,
    "initial_gpu_queue_size": 0,
    "max_gpu_queue_size": 0,
    "cluster_type": "ondemand",
    "deployment_date": "2026-08-20",
    "ssh_keypair": "/tmp/mycluster.pem",
    "ec2_keypair": "mycluster-keypair",
    "ec2_user": "ubuntu",
    "s3_bucketname": "my-bucket",
    "enable_monitoring": "false",
}


def _record(**overrides):
    return pcluster_core.ClusterRecord(**{**_RECORD_KWARGS, **overrides})


def _stage_checks(monkeypatch, **overrides):
    """Stub every leaf check to pass, then let each test fail exactly one.
    Patches pcluster_core's own names -- the module core_check_cluster_health
    actually resolves them against."""
    monkeypatch.setattr(pcluster_core, "check_cfn_status",
                        lambda n, r, b: (True, "status=CREATE_COMPLETE", "1.2.3.4"))
    monkeypatch.setattr(pcluster_core, "check_head_ip", lambda ip: (True, None))
    monkeypatch.setattr(pcluster_core, "check_ssh", lambda *a: (True, None))
    monkeypatch.setattr(pcluster_core, "check_slurm", lambda *a: (True, None))
    monkeypatch.setattr(pcluster_core, "check_postinstall", lambda *a: (True, None))
    monkeypatch.setattr(pcluster_core, "check_grafana", lambda *a: (True, None))
    monkeypatch.setattr(pcluster_core, "check_s3", lambda b, r: (True, None))
    for name, value in overrides.items():
        monkeypatch.setattr(pcluster_core, name, value)


def _names(report):
    return [c.name for c in report.checks]


def _statuses(report):
    return {c.name: c.status for c in report.checks}


class TestCoreCheckClusterHealth:
    """healthy/checks is the whole contract: both the CLI shim and the future
    MCP wrapper derive everything else from it, so an off-by-one or a
    swallowed failure would report a broken cluster as healthy."""

    def test_all_checks_passing_is_healthy(self, monkeypatch):
        _stage_checks(monkeypatch)
        report = pcluster_core.core_check_cluster_health(
            cluster_record=_record(), pcluster_bin="pcluster"
        )
        assert report.healthy is True
        assert report.cluster_name == "mycluster"
        assert all(c.status != "fail" for c in report.checks)

    def test_one_failed_check_is_unhealthy(self, monkeypatch):
        _stage_checks(monkeypatch, check_s3=lambda b, r: (False, "NoSuchBucket"))
        report = pcluster_core.core_check_cluster_health(
            cluster_record=_record(), pcluster_bin="pcluster"
        )
        assert report.healthy is False
        statuses = _statuses(report)
        assert statuses["S3 bucket: my-bucket"] == "fail"

    def test_failure_count_accumulates(self, monkeypatch):
        _stage_checks(
            monkeypatch,
            check_s3=lambda b, r: (False, "NoSuchBucket"),
            check_slurm=lambda *a: (False, "sinfo failed"),
            check_postinstall=lambda *a: (False, "marker missing"),
        )
        report = pcluster_core.core_check_cluster_health(
            cluster_record=_record(), pcluster_bin="pcluster"
        )
        failures = [c for c in report.checks if c.status == "fail"]
        assert len(failures) == 3

    def test_ssh_failure_skips_dependent_checks_without_double_counting(self, monkeypatch):
        """Slurm, postinstall, and Grafana all require SSH. They must be
        reported skip, not fail — otherwise one unreachable head node inflates
        the failure count to four and buries the actual cause."""
        _stage_checks(monkeypatch, check_ssh=lambda *a: (False, "timed out"))
        report = pcluster_core.core_check_cluster_health(
            cluster_record=_record(), pcluster_bin="pcluster"
        )
        statuses = _statuses(report)
        assert statuses["SSH reachability"] == "fail"
        assert statuses["Slurm"] == "skip"
        assert statuses["postinstall complete"] == "skip"
        assert sum(1 for c in report.checks if c.status == "fail") == 1

    def test_cfn_failure_skips_the_entire_ssh_chain(self, monkeypatch):
        _stage_checks(monkeypatch, check_cfn_status=lambda n, r, b: (False, "rc=1", None))
        report = pcluster_core.core_check_cluster_health(
            cluster_record=_record(), pcluster_bin="pcluster"
        )
        statuses = _statuses(report)
        assert statuses["CloudFormation status"] == "fail"
        assert statuses["head node IP"] == "skip"
        assert statuses["SSH reachability"] == "skip"
        assert sum(1 for c in report.checks if c.status == "fail") == 1

    def test_grafana_is_checked_only_when_monitoring_is_enabled(self, monkeypatch):
        calls = []
        _stage_checks(monkeypatch, check_grafana=lambda *a: calls.append("grafana") or (True, None))
        pcluster_core.core_check_cluster_health(
            cluster_record=_record(enable_monitoring="false"), pcluster_bin="pcluster"
        )
        assert calls == []

    def test_grafana_is_checked_when_monitoring_is_enabled(self, monkeypatch):
        calls = []
        _stage_checks(monkeypatch, check_grafana=lambda *a: calls.append("grafana") or (True, None))
        pcluster_core.core_check_cluster_health(
            cluster_record=_record(enable_monitoring="true"), pcluster_bin="pcluster"
        )
        assert calls == ["grafana"]

    def test_grafana_still_skipped_when_monitoring_enabled_but_ssh_unreachable(self, monkeypatch):
        """Monitoring gating and the SSH-availability gate are independent --
        Grafana must appear as skip, not silently vanish from the report,
        when both conditions withhold it."""
        _stage_checks(monkeypatch, check_ssh=lambda *a: (False, "timed out"))
        report = pcluster_core.core_check_cluster_health(
            cluster_record=_record(enable_monitoring="true"), pcluster_bin="pcluster"
        )
        assert _statuses(report)["Grafana health"] == "skip"

    def test_ssh_unavailable_skips_every_ssh_dependent_check_without_connecting(self, monkeypatch):
        """ssh_available=False is the remote-transport path (Workstream 7) --
        no key material exists there, so no SSH attempt may be made at all."""
        ssh_called = []
        _stage_checks(monkeypatch, check_ssh=lambda *a: ssh_called.append(1) or (True, None))
        report = pcluster_core.core_check_cluster_health(
            cluster_record=_record(enable_monitoring="true"),
            pcluster_bin="pcluster",
            ssh_available=False,
        )
        assert ssh_called == []
        statuses = _statuses(report)
        assert statuses["SSH reachability"] == "skip"
        assert statuses["Slurm"] == "skip"
        assert statuses["postinstall complete"] == "skip"
        assert statuses["Grafana health"] == "skip"
        assert report.healthy is True

    def test_slurm_pass_with_degradation_note_is_not_a_failure(self, monkeypatch):
        """Some nodes usable, some not: check_slurm reports pass with a note,
        and that note must survive into the report, not just the boolean."""
        _stage_checks(
            monkeypatch,
            check_slurm=lambda *a: (True, "2 node(s) usable, 3 down/drained"),
        )
        report = pcluster_core.core_check_cluster_health(
            cluster_record=_record(), pcluster_bin="pcluster"
        )
        slurm = next(c for c in report.checks if c.name == "Slurm")
        assert slurm.status == "pass"
        assert slurm.detail == "2 node(s) usable, 3 down/drained"
        assert report.healthy is True

    def test_timeout_is_passed_through_to_the_ssh_checks_unmodified(self, monkeypatch):
        """core_check_cluster_health trusts its caller to have already
        clamped timeout -- it does not reclamp or reject it."""
        seen = {}

        def _ssh(ip, keypair, user, timeout):
            seen["timeout"] = timeout
            return True, None

        _stage_checks(monkeypatch, check_ssh=_ssh)
        pcluster_core.core_check_cluster_health(
            cluster_record=_record(), pcluster_bin="pcluster", timeout=42
        )
        assert seen["timeout"] == 42


# ---------------------------------------------------------------------------
# check_pcluster.py's main() -- CLI glue only: arg parsing, vars-file
# resolution, print formatting, exit codes. core_check_cluster_health is
# mocked as a single unit here, since its own behavior is covered above.
# ---------------------------------------------------------------------------

_REC = dict(_RECORD_KWARGS)


def _stage_main(monkeypatch, argv=("check_pcluster.py", "-N", "mycluster"), rec=None,
                 core_result=None):
    full_rec = dict(_REC)
    full_rec.update(rec or {})
    monkeypatch.setattr(chk, "_read_cluster_record", lambda n, r: full_rec)
    if core_result is None:
        core_result = pcluster_core.ClusterHealthReport(
            cluster_name="mycluster", checks=[], healthy=True
        )
    monkeypatch.setattr(chk, "core_check_cluster_health", lambda **kw: core_result)
    monkeypatch.setattr(sys, "argv", list(argv))


class TestCheckPclusterMainCliShim:
    def test_all_checks_passing_exits_zero(self, monkeypatch, capsys):
        _stage_main(monkeypatch)
        with pytest.raises(SystemExit) as exc:
            chk.main()
        assert exc.value.code == 0
        assert "All checks passed" in capsys.readouterr().out

    def test_unhealthy_report_exits_one_with_correct_count(self, monkeypatch, capsys):
        report = pcluster_core.ClusterHealthReport(
            cluster_name="mycluster",
            checks=[
                pcluster_core.CheckResult("SSH reachability", "fail", "timed out"),
                pcluster_core.CheckResult("S3 bucket: my-bucket", "fail", "NoSuchBucket"),
                pcluster_core.CheckResult("Slurm", "skip", "SSH unreachable"),
            ],
            healthy=False,
        )
        _stage_main(monkeypatch, core_result=report)
        with pytest.raises(SystemExit) as exc:
            chk.main()
        out = capsys.readouterr().out
        assert exc.value.code == 1
        assert "2 check(s) failed." in out

    def test_missing_vars_file_exits_immediately(self, monkeypatch, capsys):
        """No region or keypair is known without the vars file, so
        core_check_cluster_health would be meaningless to call at all."""
        monkeypatch.setattr(chk, "_read_cluster_record", lambda n, r: None)
        called = []
        monkeypatch.setattr(
            chk, "core_check_cluster_health", lambda **kw: called.append(1),
        )
        monkeypatch.setattr(sys, "argv", ["check_pcluster.py", "-N", "mycluster"])
        with pytest.raises(SystemExit) as exc:
            chk.main()
        assert exc.value.code == 1
        assert called == []
        assert "1 check(s) failed." in capsys.readouterr().out

    def test_timeout_is_clamped_before_being_passed_to_the_core_function(self, monkeypatch):
        seen = {}
        report = pcluster_core.ClusterHealthReport(cluster_name="mycluster", checks=[], healthy=True)

        def _core(**kwargs):
            seen.update(kwargs)
            return report

        _stage_main(
            monkeypatch,
            argv=("check_pcluster.py", "-N", "mycluster", "-T", "9999"),
        )
        monkeypatch.setattr(chk, "core_check_cluster_health", _core)
        with pytest.raises(SystemExit):
            chk.main()
        assert seen["timeout"] == chk._MAX_TIMEOUT

    def test_report_is_printed_with_correct_symbols_and_connectors(self, monkeypatch, capsys):
        report = pcluster_core.ClusterHealthReport(
            cluster_name="mycluster",
            checks=[
                pcluster_core.CheckResult("CloudFormation status", "pass", "CREATE_COMPLETE"),
                pcluster_core.CheckResult("head node IP", "pass", "1.2.3.4"),
                pcluster_core.CheckResult("SSH reachability", "pass", None),
                pcluster_core.CheckResult("Slurm", "skip", "SSH unreachable"),
            ],
            healthy=True,
        )
        _stage_main(monkeypatch, core_result=report)
        with pytest.raises(SystemExit):
            chk.main()
        out = capsys.readouterr().out
        assert "CloudFormation status: CREATE_COMPLETE" in out
        assert "head node IP: 1.2.3.4" in out
        assert f"{chk._PASS} SSH reachability\n" in out
        assert f"{chk._SKIP} Slurm — SSH unreachable" in out


class TestSlurmIsFoundOnANonInteractiveShell:
    """`ssh host sinfo` does not get Slurm on PATH. Verified on a live
    us-east-1 head node, 2026-08-24: the non-login PATH is the bare system
    default, while a login shell appends /opt/slurm/bin. So the check ran
    `sinfo`, got rc=127 "command not found", and reported a healthy
    cluster as failed -- the one check that separates "the cluster exists"
    from "the cluster can run work".

    No local test could see it: every one stubs _run_ssh, so the remote
    command was never executed anywhere.
    """

    def _captured(self, monkeypatch, rc=0, stdout="8 idle\n"):
        import pcluster_core

        seen = {}

        def _fake(head_ip, keypair, user, timeout, remote_cmd):
            seen["cmd"] = remote_cmd
            return rc, stdout, ""

        monkeypatch.setattr(pcluster_core, "_run_ssh", _fake)
        pcluster_core.check_slurm("1.2.3.4", "/k.pem", "ubuntu", 15)
        return seen["cmd"]

    def test_the_remote_command_puts_slurm_on_path(self, monkeypatch):
        cmd = self._captured(monkeypatch)
        joined = " ".join(cmd)
        assert "/opt/slurm/bin" in joined, cmd
        assert "sinfo" in joined, cmd

    def test_it_does_not_use_a_login_shell(self, monkeypatch):
        """`bash -lc` would also work and is the tempting one-liner. It
        sources /etc/profile.d, which this repo documents as hazardous, and
        a banner printed by any fragment lands in the output
        _classify_sinfo_nodes parses -- where an unreadable line counts as
        an unusable node, which can flip the verdict."""
        cmd = self._captured(monkeypatch)
        assert "-lc" not in cmd, cmd
        assert not any(a.startswith("-l") for a in cmd if a != "-c"), cmd

    def test_a_healthy_fleet_still_passes(self, monkeypatch):
        """Vacuity guard: the command change must not break parsing."""
        import pcluster_core

        monkeypatch.setattr(
            pcluster_core, "_run_ssh",
            lambda *a, **k: (0, "8 idle\n", ""),
        )
        ok, detail = pcluster_core.check_slurm("1.2.3.4", "/k.pem", "ubuntu", 15)
        assert ok, detail

    def test_the_powered_down_state_counts_as_usable(self, monkeypatch):
        """A scale-from-zero spot queue reports `idle~` -- idle with the
        power-saving flag. The live cluster reported exactly that for all
        eight nodes, so treating the flag as unusable would call every
        idle cluster broken."""
        import pcluster_core

        monkeypatch.setattr(
            pcluster_core, "_run_ssh",
            lambda *a, **k: (0, "8 idle~\n", ""),
        )
        ok, detail = pcluster_core.check_slurm("1.2.3.4", "/k.pem", "ubuntu", 15)
        assert ok, detail
