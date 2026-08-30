"""Tests for cost_pcluster.py helper functions."""

import json
import os
import sys
import types
from datetime import date, timezone

import dataclasses

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import cost_pcluster as cp
import pcluster_core

try:
    from botocore.exceptions import ClientError, BotoCoreError
    def _fake_ce_error(code):
        return ClientError({"Error": {"Code": code, "Message": code}}, "op")
    class _FakeBotoCoreError(BotoCoreError):
        msg = "fake"
except ImportError:
    def _fake_ce_error(code):
        e = Exception(code)
        e.response = {"Error": {"Code": code, "Message": code}}
        return e
    class _FakeBotoCoreError(Exception):
        pass


# ---------------------------------------------------------------------------
# _safe
# ---------------------------------------------------------------------------

class TestSafe:
    def test_clean_string_unchanged(self):
        assert cp._safe("rmarable") == "rmarable"

    def test_strips_ansi_escape(self):
        result = cp._safe("\x1b[2Jhello")
        assert "\x1b" not in result
        assert "hello" in result

    def test_strips_newline(self):
        assert cp._safe("foo\nbar") == "foobar"

    def test_strips_null(self):
        assert cp._safe("foo\x00bar") == "foobar"


# ---------------------------------------------------------------------------
# _date_range
# ---------------------------------------------------------------------------

class TestDateRange:
    def test_end_is_utc_today(self, monkeypatch):
        fixed = date(2026, 7, 24)
        monkeypatch.setattr(pcluster_core, "_utc_today", lambda: fixed)
        start, end = cp._date_range(30)
        assert end == "2026-07-24"
        assert start == "2026-06-24"

    def test_days_1(self, monkeypatch):
        fixed = date(2026, 7, 24)
        monkeypatch.setattr(pcluster_core, "_utc_today", lambda: fixed)
        start, end = cp._date_range(1)
        assert start == "2026-07-23"
        assert end == "2026-07-24"


# ---------------------------------------------------------------------------
# _check_tag_activated
# ---------------------------------------------------------------------------

class TestCheckTagActivated:
    def _client(self, tags=None, exc=None):
        class _CE:
            def list_cost_allocation_tags(self, **kwargs):
                if exc:
                    raise exc
                return {"CostAllocationTags": tags or []}
        return _CE()

    def test_active_tag_returns_true(self):
        client = self._client([{"TagKey": "ClusterID", "Status": "Active"}])
        assert cp._check_tag_activated(client) is True

    def test_inactive_tag_returns_false(self):
        client = self._client([{"TagKey": "ClusterID", "Status": "Inactive"}])
        assert cp._check_tag_activated(client) is False

    def test_missing_tag_returns_false(self):
        client = self._client([])
        assert cp._check_tag_activated(client) is False

    def test_access_denied_returns_none(self):
        client = self._client(exc=_fake_ce_error("AccessDeniedException"))
        assert cp._check_tag_activated(client) is None

    def test_network_error_returns_none(self):
        client = self._client(exc=_FakeBotoCoreError())
        assert cp._check_tag_activated(client) is None


# ---------------------------------------------------------------------------
# _get_cluster_cost
# ---------------------------------------------------------------------------

def _ce_response(amounts, next_token=None):
    periods = [
        {"Total": {"UnblendedCost": {"Amount": str(a), "Unit": "USD"}}}
        for a in amounts
    ]
    resp = {"ResultsByTime": periods}
    if next_token:
        resp["NextPageToken"] = next_token
    return resp


class _FakeCE:
    def __init__(self, pages):
        # pages: list of (amounts, next_token|None) tuples
        self._pages = list(pages)
        self._idx = 0

    def get_cost_and_usage(self, **kwargs):
        amounts, token = self._pages[self._idx]
        self._idx += 1
        return _ce_response(amounts, token)


class TestGetClusterCost:
    def test_single_page_success(self):
        client = _FakeCE([([12.34], None)])
        total, err = cp._get_cluster_cost(client, "mycluster", "2026-06-24", "2026-07-24")
        assert err is None
        assert abs(total - 12.34) < 1e-6

    def test_multi_page_aggregated(self):
        # Simulates >12-month range requiring pagination
        client = _FakeCE([([10.00, 20.00], "tok1"), ([5.00], None)])
        total, err = cp._get_cluster_cost(client, "mycluster", "2025-01-01", "2026-07-24")
        assert err is None
        assert abs(total - 35.00) < 1e-6

    def test_access_denied(self):
        class _CE:
            def get_cost_and_usage(self, **kwargs):
                raise _fake_ce_error("AccessDeniedException")
        total, err = cp._get_cluster_cost(_CE(), "mycluster", "2026-06-24", "2026-07-24")
        assert total is None
        assert "ce:GetCostAndUsage" in err

    def test_other_client_error(self):
        class _CE:
            def get_cost_and_usage(self, **kwargs):
                raise _fake_ce_error("DataUnavailableException")
        total, err = cp._get_cluster_cost(_CE(), "mycluster", "2026-06-24", "2026-07-24")
        assert total is None
        assert "DataUnavailableException" in err

    def test_network_error(self):
        class _CE:
            def get_cost_and_usage(self, **kwargs):
                raise _FakeBotoCoreError()
        total, err = cp._get_cluster_cost(_CE(), "mycluster", "2026-06-24", "2026-07-24")
        assert total is None
        assert "network" in err or "credential" in err

    def test_zero_spend(self):
        client = _FakeCE([([0.0], None)])
        total, err = cp._get_cluster_cost(client, "mycluster", "2026-06-24", "2026-07-24")
        assert err is None
        assert total == 0.0

    def test_malformed_amount_skipped(self):
        class _CE:
            def get_cost_and_usage(self, **kwargs):
                return {"ResultsByTime": [
                    {"Total": {"UnblendedCost": {"Amount": "not-a-number"}}},
                    {"Total": {"UnblendedCost": {"Amount": "5.00"}}},
                ]}
        total, err = cp._get_cluster_cost(_CE(), "mycluster", "2026-06-24", "2026-07-24")
        assert err is None
        assert abs(total - 5.00) < 1e-6


# ---------------------------------------------------------------------------
# _format_table
# ---------------------------------------------------------------------------

class TestFormatTable:
    def test_empty_rows_prints_message(self, capsys):
        cp._format_table([], "2026-06-24 – 2026-07-24")
        out = capsys.readouterr().out
        assert "No clusters" in out

    def test_rows_printed(self, capsys):
        rows = [("mycluster", "rmarable", "us-east-1", "$12.34")]
        cp._format_table(rows, "2026-06-24 – 2026-07-24")
        out = capsys.readouterr().out
        assert "mycluster" in out
        assert "$12.34" in out
        assert "Period" in out

    def test_long_cost_str_does_not_crash(self, capsys):
        rows = [("c", "o", "r", "unavailable — CE error: DataUnavailableException")]
        cp._format_table(rows, "2026-06-24 – 2026-07-24")
        out = capsys.readouterr().out
        assert "unavailable" in out


# ---------------------------------------------------------------------------
# ClusterRecord.from_dict / .unknown
# ---------------------------------------------------------------------------

_FULL_REC_DICT = {
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
    "ssh_keypair": "/home/rmarable/.ssh/mycluster.pem",
    "ec2_keypair": "mycluster-keypair",
    "ec2_user": "ubuntu",
    "s3_bucketname": "parallelclustermaker-202608200001",
    "enable_monitoring": "false",
}


class TestClusterRecord:
    def test_from_dict_matches_read_cluster_record_shape(self):
        rec = pcluster_core.ClusterRecord.from_dict(_FULL_REC_DICT)
        assert rec.cluster_name == "mycluster"
        assert rec.cluster_owner == "rmarable"
        assert rec.region == "us-east-1"
        assert rec.cpu_instance_types == ["c5.xlarge"]

    def test_from_dict_is_frozen(self):
        rec = pcluster_core.ClusterRecord.from_dict(_FULL_REC_DICT)
        with pytest.raises(dataclasses.FrozenInstanceError):
            rec.cluster_owner = "someone-else"

    def test_unknown_sets_owner_and_region_unknown(self):
        rec = pcluster_core.ClusterRecord.unknown("orphaned-cluster")
        assert rec.cluster_name == "orphaned-cluster"
        assert rec.cluster_owner == "unknown"
        assert rec.region == "unknown"


# ---------------------------------------------------------------------------
# core_get_cost_report
# ---------------------------------------------------------------------------

def _records(*names_owners):
    return [
        pcluster_core.ClusterRecord.from_dict(
            {**_FULL_REC_DICT, "cluster_name": name, "cluster_owner": owner}
        )
        for name, owner in names_owners
    ]


class _FakeCEClient:
    """Fake Cost Explorer client: always reports the tag active, and answers
    get_cost_and_usage from a fixed list of (amounts, next_token) pages."""

    def __init__(self, pages=()):
        self._pages = list(pages)
        self._idx = 0

    def list_cost_allocation_tags(self, **kwargs):
        return {"CostAllocationTags": [{"TagKey": "ClusterID", "Status": "Active"}]}

    def get_cost_and_usage(self, **kwargs):
        amounts, token = self._pages[self._idx]
        self._idx += 1
        resp = {
            "ResultsByTime": [
                {"Total": {"UnblendedCost": {"Amount": str(a)}}} for a in amounts
            ]
        }
        if token:
            resp["NextPageToken"] = token
        return resp


class TestCoreGetCostReport:
    def test_days_out_of_range_raises(self):
        with pytest.raises(pcluster_core.PClusterMakerError, match="--days"):
            pcluster_core.core_get_cost_report(cluster_records=[], days=0)
        with pytest.raises(pcluster_core.PClusterMakerError, match="--days"):
            pcluster_core.core_get_cost_report(cluster_records=[], days=366)

    def test_days_boundaries_accepted(self, monkeypatch):
        monkeypatch.setattr(pcluster_core.boto3, "client", lambda *a, **k: _FakeCEClient())
        # boundary values must not raise
        pcluster_core.core_get_cost_report(cluster_records=[], days=1)
        pcluster_core.core_get_cost_report(cluster_records=[], days=365)

    def test_returns_one_record_per_cluster(self, monkeypatch):
        monkeypatch.setattr(
            pcluster_core.boto3,
            "client",
            lambda *a, **k: _FakeCEClient([([12.34], None)]),
        )
        records = _records(("mycluster", "rmarable"))
        result = pcluster_core.core_get_cost_report(cluster_records=records, days=30)
        assert result.tag_activated is True
        assert len(result.records) == 1
        assert result.records[0].cluster_name == "mycluster"
        assert result.records[0].owner == "rmarable"
        assert abs(result.records[0].cost_usd - 12.34) < 1e-6
        assert result.records[0].error is None

    def test_owner_filter_excludes_non_matching_clusters(self, monkeypatch):
        monkeypatch.setattr(
            pcluster_core.boto3,
            "client",
            lambda *a, **k: _FakeCEClient([([1.00], None), ([2.00], None)]),
        )
        records = _records(("a", "rmarable"), ("b", "someone-else"))
        result = pcluster_core.core_get_cost_report(
            cluster_records=records, owner_filter="rmarable", days=30
        )
        assert len(result.records) == 1
        assert result.records[0].cluster_name == "a"

    def test_default_days_is_30(self, monkeypatch):
        captured = {}

        def _fake_date_range(days):
            captured["days"] = days
            return "2026-07-21", "2026-08-20"

        monkeypatch.setattr(pcluster_core, "_date_range", _fake_date_range)
        monkeypatch.setattr(pcluster_core.boto3, "client", lambda *a, **k: _FakeCEClient())
        pcluster_core.core_get_cost_report(cluster_records=[])
        assert captured["days"] == 30
