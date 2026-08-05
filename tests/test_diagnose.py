"""Tests for diagnose_pcluster.py helper functions."""

import os
import re
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))
import diagnose_pcluster as dx
from pcluster_core import _clamp_int, _select_cw_log_group


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
