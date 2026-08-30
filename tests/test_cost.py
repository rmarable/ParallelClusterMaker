"""Tests for _get_od_price, _get_spot_price, and _cost_summary_lines."""

import json
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcluster_core import _get_od_price, _get_spot_price, _cost_summary_lines

try:
    from botocore.exceptions import ClientError
    def _fake_client_error(code):
        return ClientError({"Error": {"Code": code, "Message": code}}, "op")
except ImportError:
    def _fake_client_error(code):
        e = Exception(code)
        e.response = {"Error": {"Code": code, "Message": code}}
        return e


# ---------------------------------------------------------------------------
# Fake clients
# ---------------------------------------------------------------------------

def _od_response(price_str):
    """Build a minimal realistic Pricing API response for one instance type."""
    payload = {
        "terms": {
            "OnDemand": {
                "TERM1": {
                    "priceDimensions": {
                        "DIM1": {
                            "pricePerUnit": {"USD": price_str}
                        }
                    }
                }
            }
        }
    }
    return {"PriceList": [json.dumps(payload)]}


class _FakePricingClient:
    def __init__(self, responses):
        # responses: dict of instance_type -> price_str, or Exception to raise
        self._responses = responses
        self.call_count = 0
        self.calls = []

    def get_products(self, **kwargs):
        itype = next(
            f["Value"] for f in kwargs["Filters"] if f["Field"] == "instanceType"
        )
        self.call_count += 1
        self.calls.append(itype)
        val = self._responses.get(itype)
        if val is None:
            return {"PriceList": []}
        if isinstance(val, Exception):
            raise val
        return _od_response(val)


class _FakeEC2Client:
    def __init__(self, spot_responses):
        # spot_responses: dict of instance_type -> price_str, or Exception
        self._responses = spot_responses

    def describe_spot_price_history(self, **kwargs):
        itype = kwargs["InstanceTypes"][0]
        val = self._responses.get(itype)
        if val is None:
            return {"SpotPriceHistory": []}
        if isinstance(val, Exception):
            raise val
        return {"SpotPriceHistory": [{"InstanceType": itype, "SpotPrice": val,
                                       "AvailabilityZone": "us-east-1a"}]}


# ---------------------------------------------------------------------------
# _get_od_price
# ---------------------------------------------------------------------------

class TestGetOdPrice:
    def test_success(self):
        client = _FakePricingClient({"c8g.2xlarge": "0.3190400000"})
        price, err = _get_od_price(client, "c8g.2xlarge", "us-east-1")
        assert err is None
        assert abs(price - 0.31904) < 1e-6

    def test_unknown_instance_type_returns_none(self):
        client = _FakePricingClient({})
        price, err = _get_od_price(client, "xx99.99xlarge", "us-east-1")
        assert price is None
        assert "not found in Pricing catalog" in err

    def test_unknown_region_returns_none(self):
        client = _FakePricingClient({"c8g.2xlarge": "0.31904"})
        price, err = _get_od_price(client, "c8g.2xlarge", "xx-fake-1")
        assert price is None
        assert "not in Pricing location map" in err

    def test_access_denied_returns_helpful_message(self):
        client = _FakePricingClient(
            {"c8g.2xlarge": _fake_client_error("AccessDeniedException")}
        )
        price, err = _get_od_price(client, "c8g.2xlarge", "us-east-1")
        assert price is None
        assert "pricing:GetProducts" in err

    def test_other_client_error(self):
        client = _FakePricingClient(
            {"c8g.2xlarge": _fake_client_error("ThrottlingException")}
        )
        price, err = _get_od_price(client, "c8g.2xlarge", "us-east-1")
        assert price is None
        assert "ThrottlingException" in err

    def test_generic_exception(self):
        class _BrokenClient:
            def get_products(self, **kwargs):
                raise OSError("network down")
        price, err = _get_od_price(_BrokenClient(), "c8g.2xlarge", "us-east-1")
        assert price is None
        assert "unreachable" in err


# ---------------------------------------------------------------------------
# _get_spot_price
# ---------------------------------------------------------------------------

class TestGetSpotPrice:
    def test_success(self):
        client = _FakeEC2Client({"c8g.2xlarge": "0.136600"})
        price, err = _get_spot_price(client, "c8g.2xlarge")
        assert err is None
        assert abs(price - 0.1366) < 1e-6

    def test_no_history_returns_none(self):
        client = _FakeEC2Client({})
        price, err = _get_spot_price(client, "c8g.2xlarge")
        assert price is None
        assert "no spot price history" in err

    def test_client_error(self):
        client = _FakeEC2Client(
            {"c8g.2xlarge": _fake_client_error("UnsupportedOperation")}
        )
        price, err = _get_spot_price(client, "c8g.2xlarge")
        assert price is None
        assert "UnsupportedOperation" in err


# ---------------------------------------------------------------------------
# _cost_summary_lines
# ---------------------------------------------------------------------------

def _make_clients(od_prices=None, spot_prices=None):
    return (
        _FakePricingClient(od_prices or {}),
        _FakeEC2Client(spot_prices or {}),
    )


_COST_PARAMS = (
    "pricing_client", "ec2client", "headnode_instance_type",
    "cpu_instance_types", "max_cpu_queue_size", "enable_cpu_queue",
    "gpu_instance_types", "max_gpu_queue_size", "enable_gpu_queue",
    "region", "cluster_type",
    "loginnode_instance_type", "loginnode_count", "enable_loginnode",
)


class TestCostSummaryLinesTakesKeywordsOnly:
    """11 parameters holding two same-shaped triples -- (types, size, enabled) for
    the CPU queue and again for the GPU queue -- so transposing the two triples at
    the call site prices the GPU fleet as the CPU queue and reads as a plausible
    summary. The two clients are worse: `_get_od_price` and `_get_spot_price` each
    wrap their call in `except Exception` and return a `"unavailable -- ..."`
    reason, so a transposed client pair degrades every price rather than raising.
    Every test here called through keywords already and only production used the
    ordering, which is exactly the gap `_storage_summary_lines` was made
    keyword-only to close (`TestStorageSummaryLinesTakesKeywordsOnly`)."""

    def test_positional_arguments_are_rejected(self):
        import inspect

        params = inspect.signature(_cost_summary_lines).parameters
        positional = [
            name for name, p in params.items()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        assert not positional, (
            f"{positional} can be passed positionally again; transposing the CPU "
            "and GPU triples would price the wrong fleet rather than raising"
        )

    def test_calling_positionally_raises(self):
        pc, ec2 = _make_clients(od_prices={"c8g.2xlarge": "0.31904"})
        with pytest.raises(TypeError):
            _cost_summary_lines(
                pc, ec2, "c8g.2xlarge",
                ["c8g.2xlarge"], 8, True,
                [], 0, False,
                "us-east-1", "ondemand",
            )

    def test_the_signature_still_names_every_parameter_the_summary_needs(self):
        """Vacuity guard on the AST test below: it compares the call site against
        this tuple, so the tuple has to be the real parameter list."""
        import inspect

        assert tuple(inspect.signature(_cost_summary_lines).parameters) == _COST_PARAMS

    def test_the_make_pcluster_call_site_names_every_argument(self):
        """A keyword-only signature is only half of it: the call site has to pass
        names, and `f(**locals())` or a stray positional would defeat the guard."""
        import ast

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(repo_root, "src", "pcluster_core.py")) as fh:
            tree = ast.parse(fh.read())
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "_cost_summary_lines"
        ]
        assert calls, "no _cost_summary_lines call found in make_pcluster.py"
        expected = set(_COST_PARAMS)
        for call in calls:
            assert not call.args, "call site passes a positional argument"
            passed = {kw.arg for kw in call.keywords}
            assert passed, "test_the_make_pcluster_call_site_names_every_argument: nothing to assert absence against"
            assert None not in passed, "call site splats **kwargs instead of naming"
            assert passed == expected, (
                f"call site keywords {sorted(passed ^ expected)} do not match the "
                "function's parameters"
            )

    def test_the_call_site_does_not_cross_the_cpu_and_gpu_queues(self):
        """The transposition keyword-only cannot catch: both triples are the same
        shape, so `gpu_instance_types=cpu_instance_types` type-checks and renders.
        Pin each keyword to the variable name it must receive."""
        import ast

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(repo_root, "src", "pcluster_core.py")) as fh:
            tree = ast.parse(fh.read())
        call = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "_cost_summary_lines"
        )
        passed = {
            kw.arg: kw.value.id for kw in call.keywords
            if isinstance(kw.value, ast.Name)
        }
        for name in _COST_PARAMS:
            assert passed.get(name) == name, (
                f"{name}= is passed {passed.get(name)!r}; the CPU and GPU triples "
                "are the same shape, so a crossed pair prices the wrong fleet"
            )


class TestCostSummaryLines:
    def test_ondemand_single_type(self):
        pc, ec2 = _make_clients(
            od_prices={"c8g.2xlarge": "0.31904"},
        )
        lines = _cost_summary_lines(
            pricing_client=pc, ec2client=ec2,
            headnode_instance_type="c8g.2xlarge",
            cpu_instance_types=["c8g.2xlarge"], max_cpu_queue_size=8,
            enable_cpu_queue=True,
            gpu_instance_types=[], max_gpu_queue_size=0,
            enable_gpu_queue=False,
            region="us-east-1", cluster_type="ondemand",
        )
        assert any("Head node" in l for l in lines)
        assert any("CPU queue" in l for l in lines)
        cpu_line = next(l for l in lines if "CPU queue" in l)
        # 8 × 0.31904 = 2.552
        assert "2.552" in cpu_line
        assert "spot" not in cpu_line

    def test_spot_shows_spot_price(self):
        pc, ec2 = _make_clients(
            od_prices={"c8g.2xlarge": "0.31904"},
            spot_prices={"c8g.2xlarge": "0.1366"},
        )
        lines = _cost_summary_lines(
            pricing_client=pc, ec2client=ec2,
            headnode_instance_type="c8g.2xlarge",
            cpu_instance_types=["c8g.2xlarge"], max_cpu_queue_size=4,
            enable_cpu_queue=True,
            gpu_instance_types=[], max_gpu_queue_size=0,
            enable_gpu_queue=False,
            region="us-east-1", cluster_type="spot",
        )
        cpu_line = next(l for l in lines if "CPU queue" in l)
        assert "spot" in cpu_line
        assert "Note:" in "\n".join(lines)

    def test_range_for_multi_type(self):
        pc, ec2 = _make_clients(
            od_prices={"c8g.2xlarge": "0.31904", "c7g.2xlarge": "0.29000"},
        )
        lines = _cost_summary_lines(
            pricing_client=pc, ec2client=ec2,
            headnode_instance_type="c8g.2xlarge",
            cpu_instance_types=["c8g.2xlarge", "c7g.2xlarge"],
            max_cpu_queue_size=8, enable_cpu_queue=True,
            gpu_instance_types=[], max_gpu_queue_size=0, enable_gpu_queue=False,
            region="us-east-1", cluster_type="ondemand",
        )
        cpu_line = next(l for l in lines if "CPU queue" in l)
        assert "–" in cpu_line  # range separator

    def test_no_range_when_same_price(self):
        pc, ec2 = _make_clients(
            od_prices={"c8g.2xlarge": "0.31904", "c8g.2xlarge-twin": "0.31904"},
        )
        lines = _cost_summary_lines(
            pricing_client=pc, ec2client=ec2,
            headnode_instance_type="c8g.2xlarge",
            cpu_instance_types=["c8g.2xlarge"], max_cpu_queue_size=2,
            enable_cpu_queue=True,
            gpu_instance_types=[], max_gpu_queue_size=0, enable_gpu_queue=False,
            region="us-east-1", cluster_type="ondemand",
        )
        cpu_line = next(l for l in lines if "CPU queue" in l)
        assert "–" not in cpu_line

    def test_all_types_unavailable_shows_unavailable(self):
        pc, ec2 = _make_clients(od_prices={})
        lines = _cost_summary_lines(
            pricing_client=pc, ec2client=ec2,
            headnode_instance_type="xx.fake",
            cpu_instance_types=["xx.fake"], max_cpu_queue_size=4,
            enable_cpu_queue=True,
            gpu_instance_types=[], max_gpu_queue_size=0, enable_gpu_queue=False,
            region="us-east-1", cluster_type="ondemand",
        )
        cpu_line = next(l for l in lines if "CPU queue" in l)
        assert "unavailable" in cpu_line

    def test_partial_type_failure_annotated(self):
        pc, ec2 = _make_clients(
            od_prices={"c8g.2xlarge": "0.31904"},  # c7g.2xlarge missing
        )
        lines = _cost_summary_lines(
            pricing_client=pc, ec2client=ec2,
            headnode_instance_type="c8g.2xlarge",
            cpu_instance_types=["c8g.2xlarge", "c7g.2xlarge"],
            max_cpu_queue_size=4, enable_cpu_queue=True,
            gpu_instance_types=[], max_gpu_queue_size=0, enable_gpu_queue=False,
            region="us-east-1", cluster_type="ondemand",
        )
        cpu_line = next(l for l in lines if "CPU queue" in l)
        assert "unavailable" in cpu_line  # partial note present
        assert "$" in cpu_line            # price still shown for the successful type

    def test_gpu_queue_shown_when_enabled(self):
        pc, ec2 = _make_clients(
            od_prices={"c8g.2xlarge": "0.31904", "p3.2xlarge": "3.06000"},
        )
        lines = _cost_summary_lines(
            pricing_client=pc, ec2client=ec2,
            headnode_instance_type="c8g.2xlarge",
            cpu_instance_types=["c8g.2xlarge"], max_cpu_queue_size=8,
            enable_cpu_queue=True,
            gpu_instance_types=["p3.2xlarge"], max_gpu_queue_size=4,
            enable_gpu_queue=True,
            region="us-east-1", cluster_type="ondemand",
        )
        assert any("GPU queue" in l for l in lines)

    def test_gpu_queue_absent_when_disabled(self):
        pc, ec2 = _make_clients(
            od_prices={"c8g.2xlarge": "0.31904"},
        )
        lines = _cost_summary_lines(
            pricing_client=pc, ec2client=ec2,
            headnode_instance_type="c8g.2xlarge",
            cpu_instance_types=["c8g.2xlarge"], max_cpu_queue_size=8,
            enable_cpu_queue=True,
            gpu_instance_types=[], max_gpu_queue_size=0, enable_gpu_queue=False,
            region="us-east-1", cluster_type="ondemand",
        )
        assert not any("GPU queue" in l for l in lines)

    def test_pricing_exception_shows_unavailable_per_line(self):
        # _get_od_price catches RuntimeError internally and returns a reason string;
        # _cost_summary_lines still returns all lines with unavailable messages.
        class _BrokenPricing:
            def get_products(self, **kwargs):
                raise RuntimeError("boom")

        lines = _cost_summary_lines(
            pricing_client=_BrokenPricing(), ec2client=_FakeEC2Client({}),
            headnode_instance_type="c8g.2xlarge",
            cpu_instance_types=["c8g.2xlarge"], max_cpu_queue_size=4,
            enable_cpu_queue=True,
            gpu_instance_types=[], max_gpu_queue_size=0, enable_gpu_queue=False,
            region="us-east-1", cluster_type="ondemand",
        )
        assert all("unavailable" in l or "Estimated" in l for l in lines)

    def test_outer_exception_returns_single_error_line(self):
        # A failure outside the helper calls (e.g. broken str formatting) returns
        # a single-element list with an error message.
        class _BrokenArg:
            def __format__(self, spec):
                raise RuntimeError("format bomb")

        # Inject a broken object as max_cpu_queue_size to trigger the outer except
        lines = _cost_summary_lines(
            pricing_client=_FakePricingClient({"c8g.2xlarge": "0.31904"}),
            ec2client=_FakeEC2Client({}),
            headnode_instance_type="c8g.2xlarge",
            cpu_instance_types=["c8g.2xlarge"], max_cpu_queue_size=_BrokenArg(),
            enable_cpu_queue=True,
            gpu_instance_types=[], max_gpu_queue_size=0, enable_gpu_queue=False,
            region="us-east-1", cluster_type="ondemand",
        )
        assert len(lines) == 1
        assert "unavailable" in lines[0]


class TestCostSummaryLinesLoginNode:
    """A login-node pool is always billed on-demand -- LoginNodesPoolSchema has
    no capacity_type field at all, unlike HeadNode/SlurmQueues -- so this line
    must never carry a spot-price bracket, unlike the CPU/GPU queue lines in
    the same output. The easiest way to break this is copy-pasting the
    CPU-queue-line test, which would silently expect a spot branch that
    should not exist for this node type -- that case gets its own test,
    called out explicitly rather than folded in with the others."""

    def test_absent_when_disabled(self):
        pc, ec2 = _make_clients(od_prices={"c8g.2xlarge": "0.31904"})
        lines = _cost_summary_lines(
            pricing_client=pc, ec2client=ec2,
            headnode_instance_type="c8g.2xlarge",
            cpu_instance_types=["c8g.2xlarge"], max_cpu_queue_size=8,
            enable_cpu_queue=True,
            gpu_instance_types=[], max_gpu_queue_size=0, enable_gpu_queue=False,
            region="us-east-1", cluster_type="ondemand",
            loginnode_instance_type="c8g.xlarge", loginnode_count=1,
            enable_loginnode=False,
        )
        assert not any("Login node" in l for l in lines)

    def test_present_when_enabled(self):
        pc, ec2 = _make_clients(
            od_prices={"c8g.2xlarge": "0.31904", "c8g.xlarge": "0.15952"}
        )
        lines = _cost_summary_lines(
            pricing_client=pc, ec2client=ec2,
            headnode_instance_type="c8g.2xlarge",
            cpu_instance_types=["c8g.2xlarge"], max_cpu_queue_size=8,
            enable_cpu_queue=True,
            gpu_instance_types=[], max_gpu_queue_size=0, enable_gpu_queue=False,
            region="us-east-1", cluster_type="ondemand",
            loginnode_instance_type="c8g.xlarge", loginnode_count=1,
            enable_loginnode=True,
        )
        login_line = next(l for l in lines if "Login node" in l)
        # 1 x 0.15952
        assert "0.160" in login_line

    def test_matching_instance_type_reuses_the_head_node_price_lookup(self):
        """pcluster_defaults.yml's own shipped example sets both instance
        types to c8g.xlarge -- must not cost a second, identical live
        Pricing API call."""
        pc, ec2 = _make_clients(od_prices={"c8g.xlarge": "0.15952"})
        lines = _cost_summary_lines(
            pricing_client=pc, ec2client=ec2,
            headnode_instance_type="c8g.xlarge",
            cpu_instance_types=[], max_cpu_queue_size=0, enable_cpu_queue=False,
            gpu_instance_types=[], max_gpu_queue_size=0, enable_gpu_queue=False,
            region="us-east-1", cluster_type="ondemand",
            loginnode_instance_type="c8g.xlarge", loginnode_count=1,
            enable_loginnode=True,
        )
        assert pc.calls == ["c8g.xlarge"], (
            f"expected exactly one Pricing API call, got: {pc.calls}"
        )
        login_line = next(l for l in lines if "Login node" in l)
        assert "0.160" in login_line

    def test_different_instance_types_each_get_their_own_lookup(self):
        pc, ec2 = _make_clients(
            od_prices={"c8g.2xlarge": "0.31904", "c8g.xlarge": "0.15952"}
        )
        _cost_summary_lines(
            pricing_client=pc, ec2client=ec2,
            headnode_instance_type="c8g.2xlarge",
            cpu_instance_types=[], max_cpu_queue_size=0, enable_cpu_queue=False,
            gpu_instance_types=[], max_gpu_queue_size=0, enable_gpu_queue=False,
            region="us-east-1", cluster_type="ondemand",
            loginnode_instance_type="c8g.xlarge", loginnode_count=1,
            enable_loginnode=True,
        )
        assert sorted(pc.calls) == ["c8g.2xlarge", "c8g.xlarge"]

    def test_count_multiplier_is_applied(self):
        pc, ec2 = _make_clients(od_prices={"c8g.xlarge": "0.15952"})
        lines = _cost_summary_lines(
            pricing_client=pc, ec2client=ec2,
            headnode_instance_type="c8g.xlarge",
            cpu_instance_types=[], max_cpu_queue_size=0, enable_cpu_queue=False,
            gpu_instance_types=[], max_gpu_queue_size=0, enable_gpu_queue=False,
            region="us-east-1", cluster_type="ondemand",
            loginnode_instance_type="c8g.xlarge", loginnode_count=3,
            enable_loginnode=True,
        )
        login_line = next(l for l in lines if "Login node" in l)
        # 3 x 0.15952 = 0.47856
        assert "0.479" in login_line

    def test_no_spot_bracket_in_spot_mode(self):
        """Unlike the CPU/GPU queue lines, this one carries no [~$X/hr spot]
        bracket at all in spot mode -- a login-node pool cannot be spot."""
        pc, ec2 = _make_clients(
            od_prices={"c8g.xlarge": "0.15952", "c8g.2xlarge": "0.31904"},
            spot_prices={"c8g.2xlarge": "0.1366"},
        )
        lines = _cost_summary_lines(
            pricing_client=pc, ec2client=ec2,
            headnode_instance_type="c8g.xlarge",
            cpu_instance_types=["c8g.2xlarge"], max_cpu_queue_size=4,
            enable_cpu_queue=True,
            gpu_instance_types=[], max_gpu_queue_size=0, enable_gpu_queue=False,
            region="us-east-1", cluster_type="spot",
            loginnode_instance_type="c8g.xlarge", loginnode_count=1,
            enable_loginnode=True,
        )
        login_line = next(l for l in lines if "Login node" in l)
        assert "spot" not in login_line
        cpu_line = next(l for l in lines if "CPU queue" in l)
        assert "spot" in cpu_line, "the CPU line should still show spot pricing"

    def test_spot_mode_annotates_on_demand_only(self):
        """The missing bracket must read as intentional, not a rendering bug --
        the login-node line gets a fixed annotation in spot mode instead."""
        pc, ec2 = _make_clients(od_prices={"c8g.xlarge": "0.15952"})
        lines = _cost_summary_lines(
            pricing_client=pc, ec2client=ec2,
            headnode_instance_type="c8g.xlarge",
            cpu_instance_types=[], max_cpu_queue_size=0, enable_cpu_queue=False,
            gpu_instance_types=[], max_gpu_queue_size=0, enable_gpu_queue=False,
            region="us-east-1", cluster_type="spot",
            loginnode_instance_type="c8g.xlarge", loginnode_count=1,
            enable_loginnode=True,
        )
        login_line = next(l for l in lines if "Login node" in l)
        assert "on-demand only" in login_line

    def test_failure_path_shows_unavailable(self):
        pc, ec2 = _make_clients(od_prices={})
        lines = _cost_summary_lines(
            pricing_client=pc, ec2client=ec2,
            headnode_instance_type="c8g.xlarge",
            cpu_instance_types=[], max_cpu_queue_size=0, enable_cpu_queue=False,
            gpu_instance_types=[], max_gpu_queue_size=0, enable_gpu_queue=False,
            region="us-east-1", cluster_type="ondemand",
            loginnode_instance_type="xx.fake", loginnode_count=1,
            enable_loginnode=True,
        )
        login_line = next(l for l in lines if "Login node" in l)
        assert "unavailable" in login_line


class TestClusterAgeComesFromTheSerialNotTheCalendarDate:
    """DEPLOYMENT_DATE is a *local* calendar date with no time, parsed as
    midnight UTC. A cluster built at 22:12 EDT on the 21st exists at 02:12
    UTC on the 22nd, so the date "21-August-2026" makes it read as 26 hours
    old the moment it is created -- observed on a live 38-minute-old
    cluster.

    That is not cosmetic: age is what an operator uses to decide whether a
    cluster is stale enough to tear down, and this overstates it by up to a
    full day for any evening build west of UTC.

    The serial carries the real thing: a 14-digit `%S%M%H%d%m%Y` stamp
    generated with DateTime.now(timezone.utc) -- already UTC, precise to
    the second.
    """

    _SERIAL = "osiris-10120222082026"  # 2026-08-22T02:12:10Z

    def test_the_serial_decodes_to_the_launch_instant(self):
        from datetime import timezone

        import pcluster_core

        dt = pcluster_core._serial_launch_time(self._SERIAL)
        assert dt is not None
        assert dt.tzinfo is timezone.utc
        assert (dt.year, dt.month, dt.day) == (2026, 8, 22)
        assert (dt.hour, dt.minute, dt.second) == (2, 12, 10)

    def test_a_fresh_cluster_does_not_read_as_a_day_old(self, monkeypatch):
        """The regression, stated as the operator saw it."""
        from datetime import datetime, timedelta, timezone

        import pcluster_core

        launched = pcluster_core._serial_launch_time(self._SERIAL)
        fixed_now = launched + timedelta(minutes=38)

        class _Now(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_now

        monkeypatch.setattr(pcluster_core, "DateTime", _Now)
        assert pcluster_core._age_str("21-August-2026", serial=self._SERIAL) == "38m"

    def test_the_date_only_fallback_still_works(self, monkeypatch):
        """Kept, not replaced: a cluster from an older toolkit may have no
        parseable stamp, and an approximate age beats "?".

        Time is frozen. The first version asserted the result ended in "h"
        against a hardcoded date, which was true when written and became
        false two days later when the real clock crossed _age_str's own
        48-hour switch to days -- a test that passes today and fails
        tomorrow is worse than no test, because it fails on unrelated work.
        """
        from datetime import datetime, timezone

        import pcluster_core

        # 6 hours after midnight UTC on the date below.
        fixed_now = datetime(2026, 8, 21, 6, 0, tzinfo=timezone.utc)

        class _Now(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_now

        monkeypatch.setattr(pcluster_core, "DateTime", _Now)
        assert pcluster_core._age_str("21-August-2026", serial=None) == "6h"
        assert pcluster_core._age_str("21-August-2026", serial="osiris-nope") == "6h"

    def test_an_unparseable_pair_still_degrades_to_a_question_mark(self):
        import pcluster_core

        assert pcluster_core._age_str("garbage", serial=None) == "?"

    def test_a_serial_without_fourteen_digits_is_not_misread(self):
        """A shorter or longer run of digits must not be parsed as a
        timestamp -- that would produce a confidently wrong age."""
        import pcluster_core

        assert pcluster_core._serial_launch_time("osiris-1234567890") is None
        assert pcluster_core._serial_launch_time("") is None
        assert pcluster_core._serial_launch_time(None) is None

    def test_the_listing_passes_the_serial_through(self):
        """The fix is inert unless the caller supplies it, and the old call
        site read fine without it."""
        import ast
        import inspect

        import pcluster_core

        src = inspect.getsource(pcluster_core.core_list_clusters)
        tree = ast.parse(src.lstrip())
        calls = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_age_str"
        ]
        assert calls, "core_list_clusters no longer computes an age"
        for call in calls:
            assert any(k.arg == "serial" for k in call.keywords), (
                "_age_str must be given the serial, or it falls back to the "
                "calendar date for every cluster"
            )
