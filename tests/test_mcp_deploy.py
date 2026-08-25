"""Workstream 5: Lambda function deployment, and the 900s ceiling.

The interesting tests here are not about the boto3 calls. They are about
the one constraint that shapes the whole remote tier design: **Lambda's
maximum function timeout is 900 seconds.** A remote tool whose worst case
runs past it does not fail cleanly -- the function is killed mid-operation,
having already stopped a fleet or started a stack update, with the
cluster's S3 lock held by a process that no longer exists.

`apply_queue_config` was exactly that: routed to a Lambda tier while
documenting "up to ~30 minutes". It is local-only now, and
`TestNoRemoteToolCanOutliveItsLambda` is what keeps it that way.
"""

import ast
import io
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from mcp_server.deploy import (  # noqa: E402
    LAMBDA_MAX_TIMEOUT_SECONDS,
    MCPDeploymentError,
    TIER_RUNTIME,
    delete_mcp_functions,
    deploy_tier,
    deployment_plan,
    function_spec,
    validate_timeouts,
)
from mcp_server.packaging import (  # noqa: E402
    PCLUSTER_REQUIREMENT,
    TIER_PACKAGES,
    render_requirements_file,
)
from mcp_server.tiers import FUNCTION_NAMES, TOOL_TIERS  # noqa: E402

ACCOUNT = "123456789012"


class _FakeLambda:
    def __init__(self, exists=False):
        self.exists = exists
        self.calls = []

    def create_function(self, **kw):
        self.calls.append(("create_function", kw))
        if self.exists:
            err = Exception("already exists")
            err.response = {"Error": {"Code": "ResourceConflictException"}}
            raise err
        return {"FunctionArn": f"arn:aws:lambda:::{kw['FunctionName']}"}

    def update_function_code(self, **kw):
        self.calls.append(("update_function_code", kw))
        return {}

    def update_function_configuration(self, **kw):
        self.calls.append(("update_function_configuration", kw))
        return {"FunctionArn": f"arn:aws:lambda:::{kw['FunctionName']}"}

    def delete_function(self, **kw):
        self.calls.append(("delete_function", kw))
        return {}

    def names(self, op):
        return [kw["FunctionName"] for name, kw in self.calls if name == op]


class TestTheTimeoutCeiling:
    def test_the_ceiling_is_lambdas_own_hard_limit(self):
        """Not a tunable. If this is ever raised, it is because AWS raised
        it -- and the reasoning in tools.py's _LOCAL_ONLY note about
        apply_queue_config would need revisiting, not just this number."""
        assert LAMBDA_MAX_TIMEOUT_SECONDS == 900

    def test_every_tier_is_within_it(self):
        validate_timeouts()

    def test_a_tier_over_the_ceiling_is_rejected(self):
        original = TIER_RUNTIME["router"]["timeout"]
        TIER_RUNTIME["router"]["timeout"] = 901
        try:
            with pytest.raises(MCPDeploymentError, match="900"):
                validate_timeouts()
        finally:
            TIER_RUNTIME["router"]["timeout"] = original

    def test_the_router_is_not_given_the_full_ceiling(self):
        """It does one InvokeFunction. A router still running after a
        minute is a bug, and a low ceiling surfaces it rather than
        billing for it."""
        assert TIER_RUNTIME["router"]["timeout"] < LAMBDA_MAX_TIMEOUT_SECONDS

    def test_every_packaged_tier_has_a_runtime_config(self):
        assert set(TIER_RUNTIME) == set(TIER_PACKAGES)


class TestNoRemoteToolCanOutliveItsLambda:
    """The defect this class exists for: apply_queue_config was registered
    on the remote transport *and* routed to a Lambda tier, while blocking
    for up to ~30 minutes. Past 900s the function is killed mid-mutation,
    with the fleet stopped, an update in flight, and the S3 lock orphaned.

    Both halves are asserted, because either one alone is satisfiable by
    the broken arrangement: it must not be routed, and it must be excluded
    from the remote tool set.
    """

    def test_apply_queue_config_is_not_routed_to_a_lambda_tier(self):
        assert "apply_queue_config" not in TOOL_TIERS

    def test_apply_queue_config_is_excluded_from_the_remote_transport(self):
        from mcp_server.tools import _LOCAL_ONLY

        assert "apply_queue_config" in _LOCAL_ONLY

    def test_the_replacement_phase_tool_is_routed(self):
        """Vacuity guard: "fix" it by deleting the capability outright and
        remote callers lose queue updates entirely. The three phases must
        still be reachable."""
        assert TOOL_TIERS["apply_cluster_update"] == "stack-mutation-node"
        assert TOOL_TIERS["stop_fleet"] == "fleet-toggle"
        assert TOOL_TIERS["start_fleet"] == "fleet-toggle"

    def test_no_routed_tool_wrapper_passes_wait_true(self):
        """The general rule behind the specific fix, checked against the
        source rather than a list someone maintains: a tool served by a
        Lambda must not block on a cluster operation. Every mutating core
        function takes wait=, and every routed wrapper must pass False.

        AST, not grep -- `wait=False` and `wait=True` differ by four
        characters and both appear in this file's prose.
        """
        path = os.path.join(REPO_ROOT, "mcp_server", "tools.py")
        with open(path) as fh:
            tree = ast.parse(fh.read())

        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name not in TOOL_TIERS:
                continue
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Call):
                    continue
                for kw in inner.keywords:
                    if kw.arg == "wait" and (
                        isinstance(kw.value, ast.Constant) and kw.value.value is True
                    ):
                        offenders.append(node.name)
        assert offenders == [], (
            f"{offenders} block on a cluster operation but are served by a "
            f"Lambda capped at {LAMBDA_MAX_TIMEOUT_SECONDS}s"
        )

    def test_the_wait_scan_can_see_a_blocking_wrapper(self):
        """Vacuity guard for the scan above: it must actually reject
        wait=True, not merely find none."""
        src = (
            "def create_cluster():\n"
            "    return core_create_cluster(wait=True)\n"
        )
        tree = ast.parse(src)
        found = [
            n.name for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name in TOOL_TIERS
            for c in ast.walk(n) if isinstance(c, ast.Call)
            for kw in c.keywords
            if kw.arg == "wait" and isinstance(kw.value, ast.Constant)
            and kw.value.value is True
        ]
        assert found == ["create_cluster"]


class TestFunctionSpec:
    def test_a_zip_tier_gets_a_runtime_and_handler(self):
        spec = function_spec(
            "read-only", aws_account_id=ACCOUNT,
            code={"S3Bucket": "b", "S3Key": "k"},
        )
        assert spec["PackageType"] == "Zip"
        assert spec["Handler"] == TIER_PACKAGES["read-only"]["handler"]
        assert spec["Runtime"].startswith("python3.")

    def test_the_image_tier_gets_neither(self):
        """Lambda rejects Runtime/Handler on a container image -- they come
        from the Dockerfile."""
        spec = function_spec(
            "stack-mutation-node", aws_account_id=ACCOUNT,
            code={"ImageUri": "acct.dkr.ecr.us-east-2.amazonaws.com/x:1"},
        )
        assert spec["PackageType"] == "Image"
        assert "Runtime" not in spec and "Handler" not in spec

    def test_a_zip_tier_refuses_an_image_uri(self):
        with pytest.raises(MCPDeploymentError, match="ImageUri"):
            function_spec("router", aws_account_id=ACCOUNT,
                          code={"ImageUri": "x:1"})

    def test_the_image_tier_refuses_a_zip_reference(self):
        """The mistake that would otherwise deploy a container function
        pointing at an S3 zip and fail at the first invocation."""
        with pytest.raises(MCPDeploymentError, match="ImageUri"):
            function_spec("stack-mutation-node", aws_account_id=ACCOUNT,
                          code={"S3Bucket": "b", "S3Key": "k"})

    def test_the_role_arn_matches_what_setup_mcp_infra_creates(self):
        """A role name agreed by two modules that never import each other.
        A mismatch is a deployment-time failure, not a test-time one."""
        import pcluster_core

        for tier in TIER_PACKAGES:
            spec = function_spec(tier, aws_account_id=ACCOUNT, code=(
                {"ImageUri": "x:1"} if TIER_PACKAGES[tier]["kind"] == "image"
                else {"S3Bucket": "b", "S3Key": "k"}
            ))
            assert spec["Role"].endswith("/" + pcluster_core._mcp_role_name(tier))

    def test_the_function_name_matches_the_router_policy_list(self):
        for tier in TIER_PACKAGES:
            spec = function_spec(tier, aws_account_id=ACCOUNT, code=(
                {"ImageUri": "x:1"} if TIER_PACKAGES[tier]["kind"] == "image"
                else {"S3Bucket": "b", "S3Key": "k"}
            ))
            assert spec["FunctionName"] == FUNCTION_NAMES[tier]

    def test_an_unknown_tier_names_the_known_ones(self):
        with pytest.raises(MCPDeploymentError, match="read-only"):
            function_spec("typo", aws_account_id=ACCOUNT, code={})

    def test_it_raises_rather_than_exiting(self):
        """Reachable from the MCP layer, where SystemExit kills the
        server rather than failing one call."""
        assert not issubclass(MCPDeploymentError, SystemExit)


class TestDeployTier:
    def test_a_new_function_is_created(self):
        lam = _FakeLambda(exists=False)
        deploy_tier(lam, "router", aws_account_id=ACCOUNT,
                    code={"S3Bucket": "b", "S3Key": "k"})
        assert lam.names("create_function") == [FUNCTION_NAMES["router"]]
        assert not lam.names("update_function_code")

    def test_an_existing_function_is_updated_not_an_error(self):
        """Idempotent, like _setup_mcp_infra: a partially-completed
        deployment must be re-runnable."""
        lam = _FakeLambda(exists=True)
        deploy_tier(lam, "router", aws_account_id=ACCOUNT,
                    code={"S3Bucket": "b", "S3Key": "k"})
        assert lam.names("update_function_code") == [FUNCTION_NAMES["router"]]
        assert lam.names("update_function_configuration")

    def test_code_is_updated_before_configuration(self):
        """A configuration pointing at code that was never uploaded is the
        worse of the two intermediate states."""
        lam = _FakeLambda(exists=True)
        deploy_tier(lam, "router", aws_account_id=ACCOUNT,
                    code={"S3Bucket": "b", "S3Key": "k"})
        ops = [n for n, _ in lam.calls]
        assert ops.index("update_function_code") < ops.index(
            "update_function_configuration"
        )

    def test_a_real_error_is_not_swallowed_as_already_exists(self):
        lam = _FakeLambda()

        def boom(**kw):
            err = Exception("denied")
            err.response = {"Error": {"Code": "AccessDeniedException"}}
            raise err

        lam.create_function = boom
        with pytest.raises(Exception, match="denied"):
            deploy_tier(lam, "router", aws_account_id=ACCOUNT,
                        code={"S3Bucket": "b", "S3Key": "k"})

    def test_the_configured_timeout_reaches_the_api(self):
        lam = _FakeLambda()
        deploy_tier(lam, "stack-mutation", aws_account_id=ACCOUNT,
                    code={"S3Bucket": "b", "S3Key": "k"})
        _, kw = lam.calls[0]
        assert kw["Timeout"] == TIER_RUNTIME["stack-mutation"]["timeout"]


class TestDeleteMcpFunctions:
    def test_it_deletes_every_tier(self):
        lam = _FakeLambda()
        delete_mcp_functions(lam)
        assert set(lam.names("delete_function")) == {
            FUNCTION_NAMES[t] for t in TIER_PACKAGES
        }

    def test_one_missing_function_does_not_abandon_the_rest(self):
        lam = _FakeLambda()
        calls = []

        def flaky(FunctionName):
            calls.append(FunctionName)
            if len(calls) == 1:
                raise Exception("ResourceNotFoundException")

        lam.delete_function = flaky
        delete_mcp_functions(lam)
        assert len(calls) == len(TIER_PACKAGES)

    def test_suppress_false_propagates(self):
        lam = _FakeLambda()

        def boom(**kw):
            raise Exception("denied")

        lam.delete_function = boom
        with pytest.raises(Exception, match="denied"):
            delete_mcp_functions(lam, suppress=False)


class TestDeploymentPlan:
    def test_it_covers_every_tier_without_calling_aws(self):
        plan = deployment_plan(ACCOUNT)
        assert {e["tier"] for e in plan} == set(TIER_PACKAGES)

    def test_the_image_tier_names_its_dockerfile(self):
        plan = {e["tier"]: e for e in deployment_plan(ACCOUNT)}
        entry = plan["stack-mutation-node"]
        assert os.path.isfile(os.path.join(REPO_ROOT, entry["dockerfile"]))

    def test_it_is_json_serializable(self):
        import json

        json.loads(json.dumps(deployment_plan(ACCOUNT)))


class TestTierMemoryTracksTierWork:
    """The per-tier memory sizes encode how much work each tier does, and
    an inversion is the mistake worth catching -- a literal-value test
    would just be a second copy of the constant.

    read-only was provisioned at 1024 MB and measured at 238 MB peak on the
    first deployed invocation (tools/list, list_clusters and
    check_cluster_health); it is 384 now. Lambda scales CPU with memory, so
    the reduction lengthens the ~8.6s cold start -- accepted for a tier
    where no call is latency-critical.
    """

    def test_memory_never_decreases_as_the_tier_does_more(self):
        from mcp_server.deploy import TIER_RUNTIME

        order = ["router", "read-only", "fleet-toggle",
                 "stack-mutation", "stack-mutation-node"]
        sizes = [TIER_RUNTIME[t]["memory"] for t in order]
        assert sizes == sorted(sizes), (
            f"memory must not invert across tiers doing progressively more "
            f"work: {dict(zip(order, sizes))}"
        )

    def test_every_tier_clears_the_measured_floor(self):
        """238 MB was the observed peak on the lightest tier that loads
        PCluster's dependency chain; nothing that loads it can be sized
        below that and still run."""
        from mcp_server.deploy import TIER_RUNTIME

        MEASURED_PEAK_MB = 238
        for tier in ("read-only", "fleet-toggle", "stack-mutation",
                     "stack-mutation-node"):
            assert TIER_RUNTIME[tier]["memory"] > MEASURED_PEAK_MB, (
                f"{tier} is sized below the {MEASURED_PEAK_MB} MB peak "
                f"measured on a real invocation"
            )

    def test_lambda_accepts_every_size(self):
        """Lambda takes memory in 1 MB steps between 128 and 10240; a value
        outside that is rejected at CreateFunction, which is the slowest
        possible place to find out."""
        from mcp_server.deploy import TIER_RUNTIME

        for tier, cfg in TIER_RUNTIME.items():
            assert 128 <= cfg["memory"] <= 10240, (tier, cfg["memory"])


class TestBothSurfacesPinTheSamePclusterVersion:
    """requirements.txt and the Lambda tier specs each name a PCluster
    version, and they drifted in effect rather than in text: both said
    ">=3.15", so the operator's venv resolved 3.15.1 while a Lambda artifact
    built months later resolved 3.16.0. PCluster refuses to manage a cluster
    created by a version it does not recognize, so the remote transport built
    a real cluster the operator's own CLI could neither describe nor tear
    down -- reported as "belongs to an incompatible ParallelCluster major
    version". An unpinned upper bound is what makes that reachable.
    """

    def _requirements_line(self):
        path = os.path.join(REPO_ROOT, "requirements.txt")
        for raw in io.open(path, encoding="utf-8").read().splitlines():
            if raw.strip().lower().startswith("aws-parallelcluster"):
                return raw.strip()
        raise AssertionError("requirements.txt names no aws-parallelcluster")

    def test_the_two_surfaces_agree(self):
        assert PCLUSTER_REQUIREMENT == self._requirements_line()

    def test_the_pin_carries_an_upper_bound(self):
        """The half that actually prevents the drift. A lower bound alone is
        satisfied by every future release."""
        assert "<" in PCLUSTER_REQUIREMENT, (
            f"{PCLUSTER_REQUIREMENT!r} has no upper bound, so an artifact built "
            f"later can resolve a PCluster the operator's CLI cannot manage"
        )

    def test_every_tier_that_ships_pcluster_uses_the_constant(self):
        """Four tiers named the version as a literal; one edited copy is the
        whole failure mode again."""
        shipping = [
            t for t, spec in TIER_PACKAGES.items()
            if any("parallelcluster" in r for r in spec["requirements"])
        ]
        assert shipping, "no tier ships PCluster -- the sweep has gone vacuous"
        for tier in shipping:
            named = [
                r for r in TIER_PACKAGES[tier]["requirements"]
                if "parallelcluster" in r
            ]
            assert named == [PCLUSTER_REQUIREMENT], (
                f"tier {tier!r} names {named} rather than the shared constant"
            )

    def test_the_generated_file_carries_the_pin(self):
        for tier in ("read-only", "stack-mutation-node"):
            assert PCLUSTER_REQUIREMENT in render_requirements_file(tier)
