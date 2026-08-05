"""Tests for check_pcluster.py helper functions."""

import json
import os
import subprocess
import sys
import types

import pytest

# Import module-level — venv guard passes when running under .venv/bin/python
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import check_pcluster as chk


def _proc(rc=0, stdout="", stderr=""):
    return types.SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# check_cfn_status
# ---------------------------------------------------------------------------

class TestCheckCfnStatus:
    def test_create_complete(self, monkeypatch):
        payload = json.dumps({
            "clusterStatus": "CREATE_COMPLETE",
            "cloudFormationStackStatus": "CREATE_COMPLETE",
            "headNode": {"publicIpAddress": "1.2.3.4"},
        })
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _proc(stdout=payload))
        ok, msg, ip = chk.check_cfn_status("mycluster", "us-east-1")
        assert ok is True
        assert "CREATE_COMPLETE" in msg
        assert ip == "1.2.3.4"

    def test_non_create_complete_returns_fail(self, monkeypatch):
        payload = json.dumps({
            "clusterStatus": "UPDATE_FAILED",
            "cloudFormationStackStatus": "UPDATE_ROLLBACK_COMPLETE",
            "headNode": {"publicIpAddress": "1.2.3.4"},
        })
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _proc(stdout=payload))
        ok, msg, ip = chk.check_cfn_status("mycluster", "us-east-1")
        assert ok is False
        assert "UPDATE_FAILED" in msg

    def test_private_ip_fallback(self, monkeypatch):
        payload = json.dumps({
            "clusterStatus": "CREATE_COMPLETE",
            "cloudFormationStackStatus": "CREATE_COMPLETE",
            "headNode": {"privateIpAddress": "10.0.0.5"},
        })
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _proc(stdout=payload))
        ok, msg, ip = chk.check_cfn_status("mycluster", "us-east-1")
        assert ok is True
        assert ip == "10.0.0.5"

    def test_timeout(self, monkeypatch):
        def _raise(*a, **kw):
            raise subprocess.TimeoutExpired("pcluster", 30)
        monkeypatch.setattr(subprocess, "run", _raise)
        ok, msg, ip = chk.check_cfn_status("mycluster", "us-east-1")
        assert ok is False
        assert "timed out" in msg

    def test_nonzero_rc(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **kw: _proc(rc=1, stderr="err"))
        ok, msg, ip = chk.check_cfn_status("mycluster", "us-east-1")
        assert ok is False
        assert "rc=1" in msg


# ---------------------------------------------------------------------------
# check_head_ip
# ---------------------------------------------------------------------------

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

        On the pass path main() has an `err` that is a degradation note rather
        than a failure, and printing a bare PASS discards it -- which is
        indistinguishable from never having read stdout at all, the defect this
        whole class exists for. A unit test on check_slurm cannot see that: it
        asserts on a return value main() is free to throw away.
        """
        _stage_main(
            monkeypatch,
            check_slurm=lambda *a: (True, "2 node(s) usable, 3 down/drained"),
        )
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
        assert "sinfo" in seen["args"]
        assert "%T" in " ".join(seen["args"]), (
            "sinfo is not being asked for node states"
        )
        assert "-s" not in seen["args"], "-s reports an aggregate, not states"


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
# main() aggregation
# ---------------------------------------------------------------------------

_REC = {
    "region": "us-east-1",
    "ssh_keypair": "/tmp/mycluster.pem",
    "ec2_user": "ubuntu",
    "s3_bucketname": "my-bucket",
    "enable_monitoring": "false",
}


def _stage_main(monkeypatch, argv=("check_pcluster.py", "-N", "mycluster"), **overrides):
    """Stub every check to pass, then let each test fail exactly one."""
    rec = dict(_REC)
    rec.update(overrides.pop("rec", {}))
    monkeypatch.setattr(chk, "check_vars_file", lambda n: (True, None, rec))
    monkeypatch.setattr(chk, "check_cfn_status",
                        lambda n, r: (True, "status=CREATE_COMPLETE", "1.2.3.4"))
    monkeypatch.setattr(chk, "check_head_ip", lambda ip: (True, None))
    monkeypatch.setattr(chk, "check_ssh", lambda *a: (True, None))
    monkeypatch.setattr(chk, "check_slurm", lambda *a: (True, None))
    monkeypatch.setattr(chk, "check_postinstall", lambda *a: (True, None))
    monkeypatch.setattr(chk, "check_grafana", lambda *a: (True, None))
    monkeypatch.setattr(chk, "check_s3", lambda b, r: (True, None))
    for name, value in overrides.items():
        monkeypatch.setattr(chk, name, value)
    monkeypatch.setattr(sys, "argv", list(argv))


class TestCheckPclusterMainAggregation:
    """The exit code is the whole contract: CI and scripts branch on it. main()
    counts failures across eight checks and exits 1 if any failed, so an
    off-by-one or a swallowed failure would report a broken cluster as healthy."""

    def test_all_checks_passing_exits_zero(self, monkeypatch, capsys):
        _stage_main(monkeypatch)
        with pytest.raises(SystemExit) as exc:
            chk.main()
        assert exc.value.code == 0
        assert "All checks passed" in capsys.readouterr().out

    def test_one_failed_check_exits_one(self, monkeypatch, capsys):
        _stage_main(monkeypatch, check_s3=lambda b, r: (False, "NoSuchBucket"))
        with pytest.raises(SystemExit) as exc:
            chk.main()
        assert exc.value.code == 1
        assert "1 check(s) failed." in capsys.readouterr().out

    def test_failure_count_accumulates(self, monkeypatch, capsys):
        _stage_main(
            monkeypatch,
            check_s3=lambda b, r: (False, "NoSuchBucket"),
            check_slurm=lambda *a: (False, "sinfo failed"),
            check_postinstall=lambda *a: (False, "marker missing"),
        )
        with pytest.raises(SystemExit) as exc:
            chk.main()
        assert exc.value.code == 1
        assert "3 check(s) failed." in capsys.readouterr().out

    def test_missing_vars_file_exits_immediately(self, monkeypatch, capsys):
        """No region or keypair is known without the vars file, so every
        downstream check would be meaningless — main() must stop, not skip."""
        _stage_main(monkeypatch, check_vars_file=lambda n: (False, "missing", None))
        called = []
        monkeypatch.setattr(chk, "check_cfn_status",
                            lambda *a: called.append("cfn") or (True, "x=y", "1.2.3.4"))
        with pytest.raises(SystemExit) as exc:
            chk.main()
        assert exc.value.code == 1
        assert called == []

    def test_ssh_failure_skips_dependent_checks_without_double_counting(self, monkeypatch, capsys):
        """Slurm, postinstall, and Grafana all require SSH. They must be
        reported SKIP, not FAIL — otherwise one unreachable head node inflates
        the failure count to four and buries the actual cause."""
        _stage_main(monkeypatch, check_ssh=lambda *a: (False, "timed out"))
        with pytest.raises(SystemExit) as exc:
            chk.main()
        out = capsys.readouterr().out
        assert exc.value.code == 1
        assert "1 check(s) failed." in out
        assert out.count(chk._SKIP) == 2

    def test_cfn_failure_skips_the_entire_ssh_chain(self, monkeypatch, capsys):
        _stage_main(
            monkeypatch,
            check_cfn_status=lambda n, r: (False, "rc=1", None),
        )
        with pytest.raises(SystemExit) as exc:
            chk.main()
        out = capsys.readouterr().out
        assert exc.value.code == 1
        assert "1 check(s) failed." in out
        assert "head node IP" in out and chk._SKIP in out

    def test_grafana_is_checked_only_when_monitoring_is_enabled(self, monkeypatch):
        calls = []
        _stage_main(
            monkeypatch,
            rec={"enable_monitoring": "false"},
            check_grafana=lambda *a: calls.append("grafana") or (True, None),
        )
        with pytest.raises(SystemExit):
            chk.main()
        assert calls == []

    def test_grafana_is_checked_when_monitoring_is_enabled(self, monkeypatch):
        calls = []
        _stage_main(
            monkeypatch,
            rec={"enable_monitoring": "true"},
            check_grafana=lambda *a: calls.append("grafana") or (True, None),
        )
        with pytest.raises(SystemExit):
            chk.main()
        assert calls == ["grafana"]

    def test_timeout_is_clamped_before_reaching_the_ssh_checks(self, monkeypatch):
        seen = {}

        def _ssh(ip, keypair, user, timeout):
            seen["timeout"] = timeout
            return True, None

        _stage_main(
            monkeypatch,
            argv=("check_pcluster.py", "-N", "mycluster", "-T", "9999"),
            check_ssh=_ssh,
        )
        with pytest.raises(SystemExit):
            chk.main()
        assert seen["timeout"] == chk._MAX_TIMEOUT
