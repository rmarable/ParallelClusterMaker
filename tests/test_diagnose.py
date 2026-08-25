"""Tests for diagnose_pcluster.py helper functions."""

import json
import os
import re
import subprocess
import sys
import types

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))
import diagnose_pcluster as dx
import pcluster_core
from pcluster_core import _clamp_int, _select_cw_log_group


def _proc(rc=0, stdout="", stderr=""):
    return types.SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# _tail_lines
# ---------------------------------------------------------------------------

class TestTailLines:
    def test_returns_last_n_lines(self):
        text = "a\nb\nc\nd\ne"
        assert dx._tail_lines(text, 3) == "c\nd\ne"

    def test_fewer_lines_than_n(self):
        text = "a\nb"
        assert dx._tail_lines(text, 10) == "a\nb"

    def test_skips_blank_lines(self):
        text = "a\n\nb\n\nc"
        result = dx._tail_lines(text, 10)
        assert "\n\n" not in result
        assert "a" in result and "b" in result and "c" in result

    def test_empty_string(self):
        assert dx._tail_lines("", 10) == ""

    def test_n_zero_returns_empty(self):
        assert dx._tail_lines("a\nb\nc", 0) == ""


# ---------------------------------------------------------------------------
# _format_sinfo
# ---------------------------------------------------------------------------

_SINFO_HEADER = "NODELIST   NODES PARTITION STATE CPUS MEMORY REASON"


class TestFormatSinfo:
    def test_idle_node_no_marker(self):
        output = f"{_SINFO_HEADER}\ncompute-1      1 cpu       idle   4  8000  none"
        result = dx._format_sinfo(output)
        assert "<-- not idle" not in result
        assert "compute-1" in result

    def test_drain_node_gets_marker(self):
        output = f"{_SINFO_HEADER}\ncompute-2      1 cpu       drain  4  8000  none"
        result = dx._format_sinfo(output)
        assert "<-- not idle" in result

    def test_down_node_gets_marker(self):
        output = f"{_SINFO_HEADER}\ncompute-3      1 cpu       down*  4  8000  none"
        result = dx._format_sinfo(output)
        assert "<-- not idle" in result

    def test_mix_node_no_marker(self):
        output = f"{_SINFO_HEADER}\ncompute-4      1 cpu       mix    4  8000  none"
        result = dx._format_sinfo(output)
        assert "<-- not idle" not in result

    def test_alloc_node_no_marker(self):
        output = f"{_SINFO_HEADER}\ncompute-5      1 cpu       alloc  4  8000  none"
        result = dx._format_sinfo(output)
        assert "<-- not idle" not in result

    def test_header_line_no_marker(self):
        result = dx._format_sinfo(_SINFO_HEADER)
        assert "<-- not idle" not in result


# ---------------------------------------------------------------------------
# _parse_sacct
# ---------------------------------------------------------------------------

class TestParseSacct:
    def test_returns_none_on_empty(self):
        assert dx._parse_sacct("") is None

    def test_returns_none_on_whitespace_only(self):
        assert dx._parse_sacct("   \n   \n") is None

    def test_returns_lines_on_real_data(self):
        output = "1042|myjob|FAILED|1:0|compute-1|2026-07-24T10:00:00|2026-07-24T10:05:00"
        result = dx._parse_sacct(output)
        assert result is not None
        assert "1042" in result
        assert "FAILED" in result

    def test_indents_output(self):
        output = "1042|myjob|FAILED|1:0|compute-1|2026-07-24T10:00:00|2026-07-24T10:05:00"
        result = dx._parse_sacct(output)
        assert result.startswith("  ")

    def test_multiple_rows(self):
        output = "1042|job1|FAILED|1:0|n1|2026-07-24T10:00:00|2026-07-24T10:05:00\n1043|job2|TIMEOUT|0:1|n2|2026-07-24T11:00:00|2026-07-24T11:30:00"
        result = dx._parse_sacct(output)
        assert "1042" in result
        assert "1043" in result


# ---------------------------------------------------------------------------
# ec2_user validation
# ---------------------------------------------------------------------------

class TestEc2UserValidation:
    def test_valid_ubuntu(self):
        assert "ubuntu" in dx._VALID_EC2_USERS

    def test_the_allowlist_covers_every_login_resolve_ec2_user_can_return(self):
        """This allowlist rejects a cluster record it does not recognize, so a
        login name that _resolve_ec2_user can produce and this set omits makes
        diagnose refuse to run against a cluster that built fine. It is derived
        from _EC2_USERS rather than restated: while rhel9 was unsupported the
        allowlist was {"ubuntu"} and ec2-user was correctly absent, and re-adding
        the OS without widening the allowlist is the mirror-image bug."""
        from pcluster_core import _EC2_USERS

        assert dx._VALID_EC2_USERS == set(_EC2_USERS.values())

    def test_invalid_user_not_in_set(self):
        assert "root" not in dx._VALID_EC2_USERS
        assert "admin" not in dx._VALID_EC2_USERS


# ---------------------------------------------------------------------------
# cw_lines / log_lines clamping
# ---------------------------------------------------------------------------

class TestLineClamping:
    def test_cw_lines_max(self):
        assert dx._MAX_CW_LINES == 500

    def test_log_lines_max(self):
        assert dx._MAX_LOG_LINES == 200

    # These call production's _clamp_int. The previous versions re-implemented
    # `min(max(1, n), limit)` inline in the test body, so they asserted only
    # that Python's own min/max work — they would have passed with the clamp
    # deleted from diagnose_pcluster.py entirely.
    def test_clamp_above_max(self):
        assert _clamp_int(9999, 1, dx._MAX_CW_LINES, "--cw_lines") == dx._MAX_CW_LINES

    def test_clamp_below_min(self):
        assert _clamp_int(0, 1, dx._MAX_CW_LINES, "--cw_lines") == 1

    def test_clamp_passes_through_in_range(self):
        assert _clamp_int(50, 1, dx._MAX_CW_LINES, "--cw_lines") == 50

    def test_negative_is_clamped(self):
        assert _clamp_int(-5, 1, dx._MAX_CW_LINES, "--cw_lines") == 1

    def test_clamp_warns_when_adjusting(self, capsys):
        _clamp_int(9999, 1, 10, "--cw_lines")
        assert "WARNING" in capsys.readouterr().out

    def test_clamp_silent_when_in_range(self, capsys):
        _clamp_int(5, 1, 10, "--cw_lines")
        assert capsys.readouterr().out == ""


class TestArgumentBounds:
    """argparse type=int accepts negatives and unbounded values. A negative
    --hours produces a future sacct start time and silently reports zero
    failures; a negative --timeout makes ssh ConnectTimeout reject every
    connection, so every check FAILs for a healthy cluster."""

    def test_hours_bounds_defined(self):
        assert dx._MIN_HOURS == 1
        assert dx._MAX_HOURS >= 24

    def test_timeout_bounds_defined(self):
        assert dx._MIN_TIMEOUT >= 1
        assert dx._MAX_TIMEOUT >= 20

    def test_negative_hours_clamped_to_minimum(self):
        assert _clamp_int(-24, dx._MIN_HOURS, dx._MAX_HOURS, "--hours") == dx._MIN_HOURS

    def test_zero_timeout_clamped_to_minimum(self):
        assert _clamp_int(0, dx._MIN_TIMEOUT, dx._MAX_TIMEOUT, "-T") == dx._MIN_TIMEOUT

    def test_absurd_timeout_clamped_to_maximum(self):
        assert _clamp_int(10**9, dx._MIN_TIMEOUT, dx._MAX_TIMEOUT, "-T") == dx._MAX_TIMEOUT

    def test_check_pcluster_shares_the_same_bounds(self):
        import check_pcluster as cp
        assert cp._MIN_TIMEOUT >= 1
        assert cp._MAX_TIMEOUT >= 15

    def test_diagnose_clamps_all_four_numeric_args(self):
        """Every int argparse option in diagnose_pcluster.py must be routed
        through _clamp_int; an unclamped one is the bug this guards."""
        src = open(dx.__file__).read()
        clamped = set(re.findall(r'_clamp_int\([^,]+,[^,]+,[^,]+,\s*"([^"]+)"', src))
        joined = " ".join(clamped)
        for flag in ("--cw_lines", "--log_lines", "--hours", "--timeout"):
            assert flag in joined, f"{flag} is not passed through _clamp_int"


# ---------------------------------------------------------------------------
# _select_cw_log_group
# ---------------------------------------------------------------------------

class TestSelectCwLogGroup:
    """PCluster suffixes the log group with the stack's creation timestamp
    (cluster_stack.py: f"{CW_LOG_GROUP_NAME_PREFIX}{stack_name}-{timestamp}"), so
    diagnose_pcluster.py cannot build the name from the cluster name. It did, which
    made the CloudWatch section dead code that reported ResourceNotFoundException as
    a missing IAM permission."""

    def test_picks_the_group_for_the_cluster(self):
        assert _select_cw_log_group(
            "osiris", ["/aws/parallelcluster/osiris-202607260359"]
        ) == "/aws/parallelcluster/osiris-202607260359"

    def test_a_bare_cluster_name_is_not_a_log_group(self):
        """The name diagnose_pcluster.py used to construct. It never exists."""
        assert _select_cw_log_group("osiris", ["/aws/parallelcluster/osiris"]) is None

    def test_newest_timestamp_wins_when_rebuilds_left_groups_behind(self):
        """Teardown does not delete log groups, so a rebuilt cluster has many. The
        real osiris account had 17."""
        groups = [
            "/aws/parallelcluster/osiris-202607212352",
            "/aws/parallelcluster/osiris-202607260359",
            "/aws/parallelcluster/osiris-202607252238",
            "/aws/parallelcluster/osiris-202607260010",
        ]
        assert _select_cw_log_group("osiris", groups) == (
            "/aws/parallelcluster/osiris-202607260359"
        )

    def test_order_of_the_listing_does_not_matter(self):
        newest = "/aws/parallelcluster/osiris-202607260359"
        older = "/aws/parallelcluster/osiris-202607212352"
        assert _select_cw_log_group("osiris", [newest, older]) == newest
        assert _select_cw_log_group("osiris", [older, newest]) == newest

    def test_another_clusters_group_is_not_claimed(self):
        """A describe_log_groups prefix query for "osiris-" cannot return these, but
        a caller passing an unfiltered listing must not have them matched either."""
        assert _select_cw_log_group(
            "osiris",
            [
                "/aws/parallelcluster/anubis-202607260359",
                "/aws/parallelcluster/gilgamesh-202607260359",
            ],
        ) is None

    def test_a_longer_cluster_name_sharing_the_prefix_is_rejected(self):
        """"osiris-test" starts with "osiris-", so a startswith-only match would
        return another cluster's logs. The 12-digit suffix is what excludes it."""
        assert _select_cw_log_group(
            "osiris", ["/aws/parallelcluster/osiris-test-202607260359"]
        ) is None

    @pytest.mark.parametrize(
        "suffix", ["20260726035", "2026072603599", "20260726035z", ""]
    )
    def test_a_malformed_timestamp_is_rejected(self, suffix):
        assert _select_cw_log_group(
            "osiris", [f"/aws/parallelcluster/osiris-{suffix}"]
        ) is None

    def test_no_groups_at_all(self):
        assert _select_cw_log_group("osiris", []) is None


# ---------------------------------------------------------------------------
# _get_head_ip -- moved to pcluster_core.py in diagnose_pcluster.py's
# Workstream 1 migration (docs/parallelclustermaker-mcp-plan.md).
# ---------------------------------------------------------------------------

class TestGetHeadIp:
    def test_success_public_ip(self, monkeypatch):
        monkeypatch.setattr(pcluster_core, "_describe_cluster_json",
                            lambda c, r: {
            "clusterStatus": "CREATE_COMPLETE",
            "headNode": {"publicIpAddress": "1.2.3.4"},
        })
        ip, err = pcluster_core._get_head_ip("mycluster", "us-east-1", "pcluster")
        assert ip == "1.2.3.4"
        assert err is None

    def test_private_ip_fallback(self, monkeypatch):
        monkeypatch.setattr(pcluster_core, "_describe_cluster_json",
                            lambda c, r: {
            "clusterStatus": "CREATE_COMPLETE",
            "headNode": {"privateIpAddress": "10.0.0.5"},
        })
        ip, err = pcluster_core._get_head_ip("mycluster", "us-east-1", "pcluster")
        assert ip == "10.0.0.5"

    def test_non_create_complete_status(self, monkeypatch):
        monkeypatch.setattr(pcluster_core, "_describe_cluster_json",
                            lambda c, r: {"clusterStatus": "UPDATE_FAILED", "headNode": {}})
        ip, err = pcluster_core._get_head_ip("mycluster", "us-east-1", "pcluster")
        assert ip is None
        assert "UPDATE_FAILED" in err

    def test_no_ip_in_response(self, monkeypatch):
        monkeypatch.setattr(pcluster_core, "_describe_cluster_json",
                            lambda c, r: {"clusterStatus": "CREATE_COMPLETE", "headNode": {}})
        ip, err = pcluster_core._get_head_ip("mycluster", "us-east-1", "pcluster")
        assert ip is None
        assert "no IP address" in err

    def test_pcluster_command_failure_is_caught_not_raised(self, monkeypatch):
        """A failed describe-cluster must be caught and returned as an error
        tuple, not propagated, since core_diagnose_cluster's other sections
        (CloudWatch) still need to run.

        The exception type changed with the transport: _describe_cluster_json
        raises PClusterMakerError rather than the SystemExit the subprocess
        form used, deliberately -- SystemExit is a BaseException and would
        kill a long-lived MCP server rather than fail one call."""
        def _boom(c, r):
            raise pcluster_core.PClusterMakerError("AccessDenied")

        monkeypatch.setattr(pcluster_core, "_describe_cluster_json", _boom)
        ip, err = pcluster_core._get_head_ip("mycluster", "us-east-1", "pcluster")
        assert ip is None
        assert "AccessDenied" in err


# ---------------------------------------------------------------------------
# _fetch_cw_logs
# ---------------------------------------------------------------------------

class _Paginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **kwargs):
        return self._pages


class _FakeLogsClient:
    def __init__(self, groups=None, streams=None, events_by_stream=None,
                 groups_error=None, streams_error=None):
        self._groups = groups or []
        self._streams = streams or []
        self._events_by_stream = events_by_stream or {}
        self._groups_error = groups_error
        self._streams_error = streams_error

    def get_paginator(self, op):
        if op == "describe_log_groups":
            if self._groups_error:
                raise self._groups_error
            return _Paginator([{"logGroups": [{"logGroupName": g} for g in self._groups]}])
        if op == "describe_log_streams":
            if self._streams_error:
                raise self._streams_error
            return _Paginator([{"logStreams": [{"logStreamName": s} for s in self._streams]}])
        raise AssertionError(f"unexpected paginator op: {op}")

    def get_log_events(self, logStreamName, **kwargs):
        events = self._events_by_stream.get(logStreamName, [])
        return {"events": events}


def _cw_error(code):
    from botocore.exceptions import ClientError
    return ClientError({"Error": {"Code": code, "Message": code}}, "op")


class TestFetchCwLogs:
    def test_success_returns_log_group_and_streams(self, monkeypatch):
        client = _FakeLogsClient(
            groups=["/aws/parallelcluster/mycluster-202608200001"],
            streams=["cfn-init.log", "cloud-init-output.log"],
            events_by_stream={
                "cfn-init.log": [{"timestamp": 1755000000000, "message": "hello"}],
            },
        )
        monkeypatch.setattr(pcluster_core.boto3, "client", lambda *a, **kw: client)
        section = pcluster_core._fetch_cw_logs(
            "mycluster", "us-east-1", ["cfn-init", "cloud-init-output"], 50
        )
        assert section.error is None
        assert section.log_group == "/aws/parallelcluster/mycluster-202608200001"
        assert "hello" in section.streams["cfn-init"][0]
        assert section.streams["cloud-init-output"] == []

    def test_access_denied_on_describe_log_groups(self, monkeypatch):
        client = _FakeLogsClient(groups_error=_cw_error("AccessDeniedException"))
        monkeypatch.setattr(pcluster_core.boto3, "client", lambda *a, **kw: client)
        section = pcluster_core._fetch_cw_logs("mycluster", "us-east-1", ["cfn-init"], 50)
        assert section.log_group is None
        assert "logs:DescribeLogGroups" in section.error

    def test_no_log_group_found(self, monkeypatch):
        client = _FakeLogsClient(groups=[])
        monkeypatch.setattr(pcluster_core.boto3, "client", lambda *a, **kw: client)
        section = pcluster_core._fetch_cw_logs("mycluster", "us-east-1", ["cfn-init"], 50)
        assert section.log_group is None
        assert "no CloudWatch log group" in section.error

    def test_log_group_is_populated_even_when_describe_streams_fails(self, monkeypatch):
        """Today's script prints the log-group line before the streams call
        can fail -- the CLI shim reproduces that ordering by printing
        log_group unconditionally, so it must survive this failure mode."""
        client = _FakeLogsClient(
            groups=["/aws/parallelcluster/mycluster-202608200001"],
            streams_error=_cw_error("AccessDeniedException"),
        )
        monkeypatch.setattr(pcluster_core.boto3, "client", lambda *a, **kw: client)
        section = pcluster_core._fetch_cw_logs("mycluster", "us-east-1", ["cfn-init"], 50)
        assert section.log_group == "/aws/parallelcluster/mycluster-202608200001"
        assert "logs:DescribeLogStreams" in section.error


# ---------------------------------------------------------------------------
# _diagnose_sinfo / _diagnose_sacct / _diagnose_local_logs / _diagnose_postinstall
# ---------------------------------------------------------------------------

class TestDiagnoseSinfo:
    def test_nodes_reported(self, monkeypatch):
        monkeypatch.setattr(
            pcluster_core.subprocess, "run",
            lambda *a, **kw: _proc(stdout=f"{_SINFO_HEADER}\ncompute-1 1 cpu idle 4 8000 none"),
        )
        section = pcluster_core._diagnose_sinfo("1.2.3.4", "/tmp/key.pem", "ubuntu", 15)
        assert section.error is None
        assert "compute-1" in section.formatted_output

    def test_no_nodes_reported(self, monkeypatch):
        monkeypatch.setattr(pcluster_core.subprocess, "run", lambda *a, **kw: _proc(stdout=""))
        section = pcluster_core._diagnose_sinfo("1.2.3.4", "/tmp/key.pem", "ubuntu", 15)
        assert section.error is None
        assert section.formatted_output == ""

    def test_nonzero_rc(self, monkeypatch):
        monkeypatch.setattr(
            pcluster_core.subprocess, "run", lambda *a, **kw: _proc(rc=1, stderr="boom")
        )
        section = pcluster_core._diagnose_sinfo("1.2.3.4", "/tmp/key.pem", "ubuntu", 15)
        assert "sinfo failed (rc=1)" in section.error

    def test_timeout(self, monkeypatch):
        def _raise(*a, **kw):
            raise subprocess.TimeoutExpired("ssh", 15)
        monkeypatch.setattr(pcluster_core.subprocess, "run", _raise)
        section = pcluster_core._diagnose_sinfo("1.2.3.4", "/tmp/key.pem", "ubuntu", 15)
        assert section.error == "sinfo timed out"


class TestDiagnoseSacct:
    def test_failed_jobs_present(self, monkeypatch):
        output = "1042|myjob|FAILED|1:0|compute-1|2026-07-24T10:00:00|2026-07-24T10:05:00"
        monkeypatch.setattr(pcluster_core.subprocess, "run", lambda *a, **kw: _proc(stdout=output))
        section = pcluster_core._diagnose_sacct("1.2.3.4", "/tmp/key.pem", "ubuntu", 15, 24)
        assert section.error is None
        assert "1042" in section.formatted_output

    def test_no_failed_jobs(self, monkeypatch):
        monkeypatch.setattr(pcluster_core.subprocess, "run", lambda *a, **kw: _proc(stdout=""))
        section = pcluster_core._diagnose_sacct("1.2.3.4", "/tmp/key.pem", "ubuntu", 15, 24)
        assert section.error is None
        assert section.formatted_output is None

    def test_sacct_not_available(self, monkeypatch):
        monkeypatch.setattr(
            pcluster_core.subprocess, "run",
            lambda *a, **kw: _proc(rc=127, stderr="sacct: command not found"),
        )
        section = pcluster_core._diagnose_sacct("1.2.3.4", "/tmp/key.pem", "ubuntu", 15, 24)
        assert "not available" in section.error

    def test_other_failure(self, monkeypatch):
        monkeypatch.setattr(
            pcluster_core.subprocess, "run", lambda *a, **kw: _proc(rc=1, stderr="boom")
        )
        section = pcluster_core._diagnose_sacct("1.2.3.4", "/tmp/key.pem", "ubuntu", 15, 24)
        assert "sacct failed (rc=1)" in section.error


class TestDiagnoseLocalLogs:
    def test_all_four_logs_checked_in_order(self, monkeypatch):
        monkeypatch.setattr(pcluster_core.subprocess, "run", lambda *a, **kw: _proc(stdout="line1\nline2"))
        tails = pcluster_core._diagnose_local_logs("1.2.3.4", "/tmp/key.pem", "ubuntu", 15, 30)
        assert [t.path for t in tails] == pcluster_core._LOCAL_LOGS
        assert all(t.error is None for t in tails)
        assert all("line1" in t.content for t in tails)

    def test_empty_file(self, monkeypatch):
        monkeypatch.setattr(pcluster_core.subprocess, "run", lambda *a, **kw: _proc(stdout=""))
        tails = pcluster_core._diagnose_local_logs("1.2.3.4", "/tmp/key.pem", "ubuntu", 15, 30)
        assert all(t.content == "" and t.error is None for t in tails)

    def test_unavailable_log_carries_full_message_text(self, monkeypatch):
        """The error string must carry the exact phrasing the CLI shim prints
        verbatim -- 'unavailable — {stderr}', matching today's script."""
        monkeypatch.setattr(
            pcluster_core.subprocess, "run", lambda *a, **kw: _proc(rc=1, stderr="No such file")
        )
        tails = pcluster_core._diagnose_local_logs("1.2.3.4", "/tmp/key.pem", "ubuntu", 15, 30)
        assert all(t.error == "unavailable — No such file" for t in tails)

    def test_timeout_error_text(self, monkeypatch):
        def _raise(*a, **kw):
            raise subprocess.TimeoutExpired("ssh", 15)
        monkeypatch.setattr(pcluster_core.subprocess, "run", _raise)
        tails = pcluster_core._diagnose_local_logs("1.2.3.4", "/tmp/key.pem", "ubuntu", 15, 30)
        assert all(t.error == "timed out" for t in tails)

    def test_os_error_text_carries_error_prefix(self, monkeypatch):
        def _raise(*a, **kw):
            raise OSError("no route to host")
        monkeypatch.setattr(pcluster_core.subprocess, "run", _raise)
        tails = pcluster_core._diagnose_local_logs("1.2.3.4", "/tmp/key.pem", "ubuntu", 15, 30)
        assert all(t.error == "error: no route to host" for t in tails)


class TestDiagnosePostinstall:
    def test_marker_present(self, monkeypatch):
        monkeypatch.setattr(pcluster_core.subprocess, "run", lambda *a, **kw: _proc(rc=0))
        section = pcluster_core._diagnose_postinstall("1.2.3.4", "/tmp/key.pem", "ubuntu", 15)
        assert section.marker_present is True
        assert section.error is None

    def test_marker_absent(self, monkeypatch):
        monkeypatch.setattr(pcluster_core.subprocess, "run", lambda *a, **kw: _proc(rc=1))
        section = pcluster_core._diagnose_postinstall("1.2.3.4", "/tmp/key.pem", "ubuntu", 15)
        assert section.marker_present is False
        assert section.error is None

    def test_timeout_error_text(self, monkeypatch):
        def _raise(*a, **kw):
            raise subprocess.TimeoutExpired("ssh", 15)
        monkeypatch.setattr(pcluster_core.subprocess, "run", _raise)
        section = pcluster_core._diagnose_postinstall("1.2.3.4", "/tmp/key.pem", "ubuntu", 15)
        assert section.error == "timed out"

    def test_os_error_text_carries_error_prefix(self, monkeypatch):
        def _raise(*a, **kw):
            raise OSError("connection reset")
        monkeypatch.setattr(pcluster_core.subprocess, "run", _raise)
        section = pcluster_core._diagnose_postinstall("1.2.3.4", "/tmp/key.pem", "ubuntu", 15)
        assert section.error == "error: connection reset"


# ---------------------------------------------------------------------------
# core_diagnose_cluster -- the orchestration layer.
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


def _stage_diagnose(monkeypatch, head_ip=("1.2.3.4", None), cw=None, sinfo=None,
                     sacct=None, local_logs=None, postinstall=None):
    monkeypatch.setattr(pcluster_core, "_get_head_ip", lambda *a, **kw: head_ip)
    monkeypatch.setattr(
        pcluster_core, "_fetch_cw_logs",
        lambda *a, **kw: cw or pcluster_core.CloudWatchLogSection("/aws/parallelcluster/x", {}, None),
    )
    monkeypatch.setattr(
        pcluster_core, "_diagnose_sinfo",
        lambda *a, **kw: sinfo or pcluster_core.SinfoSection("", None),
    )
    monkeypatch.setattr(
        pcluster_core, "_diagnose_sacct",
        lambda *a, **kw: sacct or pcluster_core.SacctSection(None, None),
    )
    monkeypatch.setattr(pcluster_core, "_diagnose_local_logs", lambda *a, **kw: local_logs or [])
    monkeypatch.setattr(
        pcluster_core, "_diagnose_postinstall",
        lambda *a, **kw: postinstall or pcluster_core.PostinstallSection(True, None),
    )


class TestCoreDiagnoseCluster:
    def test_invalid_ec2_user_raises(self):
        with pytest.raises(pcluster_core.PClusterMakerError, match="unrecognized ec2_user"):
            pcluster_core.core_diagnose_cluster(
                cluster_record=_record(ec2_user="root"), pcluster_bin="pcluster"
            )

    def test_cloudwatch_runs_even_when_head_ip_resolution_fails(self, monkeypatch):
        """CloudWatch logs don't need SSH or a resolved head IP -- today's
        script fetches them unconditionally when --no_cw isn't passed."""
        cw_called = []
        _stage_diagnose(monkeypatch, head_ip=(None, "cluster not in CREATE_COMPLETE state"))
        monkeypatch.setattr(
            pcluster_core, "_fetch_cw_logs",
            lambda *a, **kw: cw_called.append(1) or pcluster_core.CloudWatchLogSection(None, {}, None),
        )
        report = pcluster_core.core_diagnose_cluster(cluster_record=_record(), pcluster_bin="pcluster")
        assert cw_called == [1]
        assert report.head_ip is None
        assert report.head_ip_error == "cluster not in CREATE_COMPLETE state"

    def test_ssh_sections_are_none_when_head_ip_unresolved(self, monkeypatch):
        _stage_diagnose(monkeypatch, head_ip=(None, "unreachable"))
        report = pcluster_core.core_diagnose_cluster(cluster_record=_record(), pcluster_bin="pcluster")
        assert report.sinfo is None
        assert report.sacct is None
        assert report.postinstall is None
        assert report.local_logs == []

    def test_ssh_sections_populated_when_head_ip_resolves(self, monkeypatch):
        _stage_diagnose(monkeypatch)
        report = pcluster_core.core_diagnose_cluster(cluster_record=_record(), pcluster_bin="pcluster")
        assert report.sinfo is not None
        assert report.sacct is not None
        assert report.postinstall is not None

    def test_ssh_available_false_skips_ssh_sections_without_calling_them(self, monkeypatch):
        """The remote-transport path (Workstream 7): no key material exists
        there, so no SSH-dependent section may even be attempted, regardless
        of whether the head IP resolved."""
        sinfo_called = []
        _stage_diagnose(monkeypatch)
        monkeypatch.setattr(
            pcluster_core, "_diagnose_sinfo",
            lambda *a, **kw: sinfo_called.append(1) or pcluster_core.SinfoSection("", None),
        )
        report = pcluster_core.core_diagnose_cluster(
            cluster_record=_record(), pcluster_bin="pcluster", ssh_available=False
        )
        assert sinfo_called == []
        assert report.sinfo is None

    def test_cloudwatch_skipped_when_include_cloudwatch_false(self, monkeypatch):
        cw_called = []
        _stage_diagnose(monkeypatch)
        monkeypatch.setattr(
            pcluster_core, "_fetch_cw_logs",
            lambda *a, **kw: cw_called.append(1) or pcluster_core.CloudWatchLogSection(None, {}, None),
        )
        report = pcluster_core.core_diagnose_cluster(
            cluster_record=_record(), pcluster_bin="pcluster", include_cloudwatch=False
        )
        assert cw_called == []
        assert report.cloudwatch is None

    def test_monitoring_enabled_adds_grafana_and_prometheus_streams(self, monkeypatch):
        seen = {}

        def _fake_fetch(cluster_name, region, streams, n_lines):
            seen["streams"] = streams
            return pcluster_core.CloudWatchLogSection(None, {}, None)

        _stage_diagnose(monkeypatch)
        monkeypatch.setattr(pcluster_core, "_fetch_cw_logs", _fake_fetch)
        pcluster_core.core_diagnose_cluster(
            cluster_record=_record(enable_monitoring="true"), pcluster_bin="pcluster"
        )
        assert "grafana" in seen["streams"]
        assert "prometheus" in seen["streams"]

    def test_monitoring_disabled_does_not_add_streams(self, monkeypatch):
        seen = {}

        def _fake_fetch(cluster_name, region, streams, n_lines):
            seen["streams"] = streams
            return pcluster_core.CloudWatchLogSection(None, {}, None)

        _stage_diagnose(monkeypatch)
        monkeypatch.setattr(pcluster_core, "_fetch_cw_logs", _fake_fetch)
        pcluster_core.core_diagnose_cluster(
            cluster_record=_record(enable_monitoring="false"), pcluster_bin="pcluster"
        )
        assert "grafana" not in seen["streams"]

    def test_region_override_takes_precedence_over_the_record(self, monkeypatch):
        seen = {}

        def _fake_get_head_ip(cluster_name, region, pcluster_bin):
            seen["region"] = region
            return "1.2.3.4", None

        _stage_diagnose(monkeypatch)
        monkeypatch.setattr(pcluster_core, "_get_head_ip", _fake_get_head_ip)
        report = pcluster_core.core_diagnose_cluster(
            cluster_record=_record(region="us-east-1"),
            pcluster_bin="pcluster",
            region_override="eu-west-1",
        )
        assert seen["region"] == "eu-west-1"
        assert report.region == "eu-west-1"

    def test_serial_falls_back_to_unknown_when_blank(self, monkeypatch):
        _stage_diagnose(monkeypatch)
        report = pcluster_core.core_diagnose_cluster(
            cluster_record=_record(serial=""), pcluster_bin="pcluster"
        )
        assert report.serial == "unknown"


# ---------------------------------------------------------------------------
# diagnose_pcluster.py's main()/_print_report -- CLI glue only.
# ---------------------------------------------------------------------------

def _base_report(**overrides):
    defaults = dict(
        cluster_name="mycluster",
        region="us-east-1",
        serial="202608200001",
        head_ip="1.2.3.4",
        head_ip_error=None,
        cloudwatch=None,
        sinfo=pcluster_core.SinfoSection("", None),
        sacct=pcluster_core.SacctSection(None, None),
        local_logs=[],
        postinstall=pcluster_core.PostinstallSection(True, None),
    )
    defaults.update(overrides)
    return pcluster_core.DiagnosticReport(**defaults)


class TestDiagnosePclusterMainCliShim:
    def _stage_main(self, monkeypatch, rec=None, report=None, argv=("diagnose_pcluster.py", "-N", "mycluster")):
        full_rec = dict(_RECORD_KWARGS)
        full_rec.update(rec or {})
        monkeypatch.setattr(dx, "_read_cluster_record", lambda n, r: full_rec)
        monkeypatch.setattr(
            dx, "core_diagnose_cluster", lambda **kw: report or _base_report()
        )
        monkeypatch.setattr(sys, "argv", list(argv))

    def test_missing_vars_file_exits_immediately(self, monkeypatch, capsys):
        monkeypatch.setattr(dx, "_read_cluster_record", lambda n, r: None)
        called = []
        monkeypatch.setattr(dx, "core_diagnose_cluster", lambda **kw: called.append(1))
        monkeypatch.setattr(sys, "argv", ["diagnose_pcluster.py", "-N", "mycluster"])
        with pytest.raises(SystemExit) as exc:
            dx.main()
        assert "no vars file found" in str(exc.value)
        assert called == []

    def test_invalid_ec2_user_error_reaches_the_operator(self, monkeypatch, capsys):
        self._stage_main(monkeypatch)
        monkeypatch.setattr(
            dx, "core_diagnose_cluster",
            lambda **kw: (_ for _ in ()).throw(
                pcluster_core.PClusterMakerError("ERROR: unrecognized ec2_user 'root'")
            ),
        )
        with pytest.raises(SystemExit) as exc:
            dx.main()
        assert "unrecognized ec2_user" in str(exc.value)
        assert "Diagnosing cluster" not in capsys.readouterr().out, (
            "nothing should print before a raised PClusterMakerError, matching "
            "today's script where the ec2_user exit happens before any banner"
        )

    def test_head_ip_failure_still_shows_cloudwatch_then_exits_zero(self, monkeypatch, capsys):
        report = _base_report(
            head_ip=None,
            head_ip_error="cluster not in CREATE_COMPLETE state",
            cloudwatch=pcluster_core.CloudWatchLogSection("/aws/parallelcluster/x", {"cfn-init": ["line"]}, None),
            sinfo=None, sacct=None, postinstall=None,
        )
        self._stage_main(monkeypatch, report=report)
        with pytest.raises(SystemExit) as exc:
            dx.main()
        out = capsys.readouterr().out
        assert exc.value.code == 0
        assert "cannot reach cluster" in out
        assert "CloudWatch logs may still be available" in out
        assert "log group: /aws/parallelcluster/x" in out
        assert "Skipping SSH-dependent sections" in out

    def test_full_report_prints_all_five_sections(self, monkeypatch, capsys):
        report = _base_report(
            cloudwatch=pcluster_core.CloudWatchLogSection("/aws/parallelcluster/x", {"cfn-init": ["boot ok"]}, None),
            sinfo=pcluster_core.SinfoSection("  compute-1 idle", None),
            sacct=pcluster_core.SacctSection("  1042 myjob FAILED", None),
            local_logs=[pcluster_core.LocalLogTail("/var/log/x.log", "tail content", None)],
            postinstall=pcluster_core.PostinstallSection(True, None),
        )
        self._stage_main(monkeypatch, report=report)
        dx.main()  # a fully successful report never calls sys.exit(), matching today's script
        out = capsys.readouterr().out
        assert "boot ok" in out
        assert "compute-1 idle" in out
        assert "1042 myjob FAILED" in out
        assert "tail content" in out
        assert "[PASS]" in out and "custom_action_done" in out

    def test_postinstall_error_prints_parenthesized_message(self, monkeypatch, capsys):
        report = _base_report(postinstall=pcluster_core.PostinstallSection(None, "timed out"))
        self._stage_main(monkeypatch, report=report)
        dx.main()
        assert "(timed out)" in capsys.readouterr().out

    def test_local_log_error_prints_parenthesized_message(self, monkeypatch, capsys):
        report = _base_report(
            local_logs=[pcluster_core.LocalLogTail("/var/log/x.log", None, "unavailable — No such file")]
        )
        self._stage_main(monkeypatch, report=report)
        dx.main()
        assert "(unavailable — No such file)" in capsys.readouterr().out


class TestDiagnoseFindsSlurmOnTheHeadNode:
    """`sinfo` and `sacct` are not on a non-interactive PATH -- verified on
    a live us-east-1 head node, 2026-08-24, where `ssh host sinfo` answers
    `command not found` while /opt/slurm/bin/sinfo works. So two of
    diagnose's four probe types returned rc=127 against every real
    cluster, and the sections rendered as unavailable.

    check_slurm had the identical bug and was fixed first; nothing pinned
    the diagnose half, so reverting it passed this whole file.
    """

    def _captured(self, monkeypatch, fn, *args):
        import pcluster_core

        seen = []

        def _fake(head_ip, keypair, user, timeout, remote_cmd):
            seen.append(" ".join(remote_cmd))
            return 0, "", ""

        monkeypatch.setattr(pcluster_core, "_run_ssh", _fake)
        fn(*args)
        return seen

    def test_sinfo_gets_slurm_on_path(self, monkeypatch):
        import pcluster_core

        cmds = self._captured(
            monkeypatch, pcluster_core._diagnose_sinfo,
            "1.2.3.4", "/k.pem", "ubuntu", 15,
        )
        assert cmds and "/opt/slurm/bin" in cmds[0], cmds
        assert "sinfo" in cmds[0], cmds

    def test_sacct_gets_slurm_on_path(self, monkeypatch):
        import pcluster_core

        cmds = self._captured(
            monkeypatch, pcluster_core._diagnose_sacct,
            "1.2.3.4", "/k.pem", "ubuntu", 15, 24,
        )
        assert cmds and "/opt/slurm/bin" in cmds[0], cmds
        assert "sacct" in cmds[0], cmds

    def test_the_sacct_arguments_survive_the_remote_shell(self, monkeypatch):
        """ssh joins argv with spaces and the remote shell re-parses it, so
        every argument has to come through quoting intact. The same class
        of bug split check_slurm's `%D %T` format into two words."""
        import pcluster_core

        cmds = self._captured(
            monkeypatch, pcluster_core._diagnose_sacct,
            "1.2.3.4", "/k.pem", "ubuntu", 15, 24,
        )
        for flag in ("--noheader", "--state=", "--format=", "--starttime="):
            assert flag in cmds[0], (flag, cmds[0])

    def test_the_probes_that_do_not_need_slurm_are_left_alone(self, monkeypatch):
        """Vacuity guard: `tail` and `test -f` are on the default PATH, so
        wrapping them too would be noise rather than a fix."""
        import pcluster_core

        cmds = self._captured(
            monkeypatch, pcluster_core._diagnose_postinstall,
            "1.2.3.4", "/k.pem", "ubuntu", 15,
        )
        assert cmds and "/opt/slurm/bin" not in cmds[0], cmds
