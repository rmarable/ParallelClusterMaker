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


class TestCreateFunctionWaitsOutIamPropagation:
    """`--bootstrap` is the only path that creates a role and a function in
    one process, and IAM is eventually consistent.

    Every earlier deploy ran `--setup-infra` and the tier deploys as
    separate invocations minutes apart, so the roles had always propagated
    by the time any function was created. The first live `--bootstrap` died
    on the very first `CreateFunction` -- "The role defined for the function
    cannot be assumed by Lambda" -- with the boundary, ten policies and
    seven roles already made. The trust policy was correct; the role was
    seconds old.

    Nothing stubbed could have caught it: the fake answers instantly and
    IAM's own consistency is what is being waited on.
    """

    def _lag(self, times):
        """A create_function that reports the role unassumable `times` times."""
        state = {"n": 0}

        def create(**kw):
            state["n"] += 1
            if state["n"] <= times:
                err = Exception("InvalidParameterValueException")
                err.response = {"Error": {
                    "Code": "InvalidParameterValueException",
                    "Message": ("The role defined for the function cannot be "
                                "assumed by Lambda."),
                }}
                raise err
            return {"FunctionArn": f"arn:aws:lambda:::{kw['FunctionName']}"}

        return create, state

    def test_a_propagating_role_is_waited_out(self, monkeypatch):
        slept = []
        monkeypatch.setattr("mcp_server.deploy.time.sleep", slept.append)
        lam = _FakeLambda()
        lam.create_function, state = self._lag(2)

        arn = deploy_tier(lam, "router", aws_account_id=ACCOUNT,
                          code={"S3Bucket": "b", "S3Key": "k"})

        assert arn.endswith(FUNCTION_NAMES["router"])
        assert state["n"] == 3, "the create was not retried to success"
        assert len(slept) == 2, "retried without waiting between attempts"

    def test_a_wrong_trust_policy_is_not_retried(self, monkeypatch):
        """The discrimination that matters.

        Lambda answers `InvalidParameterValueException` for a genuinely
        wrong trust policy too, so retrying on the code alone would spend
        the full backoff on every real misconfiguration. The message is
        what separates them.
        """
        slept = []
        monkeypatch.setattr("mcp_server.deploy.time.sleep", slept.append)
        lam = _FakeLambda()

        def boom(**kw):
            err = Exception("bad runtime")
            err.response = {"Error": {
                "Code": "InvalidParameterValueException",
                "Message": "Value not supported for Runtime",
            }}
            raise err

        lam.create_function = boom
        with pytest.raises(Exception, match="bad runtime"):
            deploy_tier(lam, "router", aws_account_id=ACCOUNT,
                        code={"S3Bucket": "b", "S3Key": "k"})
        assert slept == [], "a non-propagation error was retried"

    def test_the_retry_is_bounded_and_reraises(self, monkeypatch):
        """A role that never becomes assumable must still fail, with the
        service's own error rather than a timeout of our own invention."""
        from mcp_server.deploy import ROLE_PROPAGATION_ATTEMPTS

        monkeypatch.setattr("mcp_server.deploy.time.sleep", lambda _s: None)
        lam = _FakeLambda()
        lam.create_function, state = self._lag(10_000)

        with pytest.raises(Exception, match="InvalidParameterValueException"):
            deploy_tier(lam, "router", aws_account_id=ACCOUNT,
                        code={"S3Bucket": "b", "S3Key": "k"})
        assert state["n"] == ROLE_PROPAGATION_ATTEMPTS

    def test_an_existing_function_still_takes_the_update_path(self, monkeypatch):
        """Vacuity guard for the wrapper: `ResourceConflictException` must
        pass straight through to the update branch, not be retried."""
        slept = []
        monkeypatch.setattr("mcp_server.deploy.time.sleep", slept.append)
        lam = _FakeLambda(exists=True)

        deploy_tier(lam, "router", aws_account_id=ACCOUNT,
                    code={"S3Bucket": "b", "S3Key": "k"})

        ops = [name for name, _ in lam.calls]
        assert "update_function_configuration" in ops
        assert slept == []


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

    def test_the_pin_is_exact(self):
        """A bounded range is not enough, and believing it was is what let
        R4 fail against a live cluster.

        `>=3.15,<3.17` is a single string, so the agreement test above
        passed -- and pip still resolved it to 3.16.0 for an artifact built
        today while the operator's venv held 3.15.1. PCluster then refuses
        the cluster the other end created. Two identical range specifiers
        resolved at different times are not the same version, so the
        property is exactness, not boundedness.
        """
        from packaging.requirements import Requirement

        spec = Requirement(PCLUSTER_REQUIREMENT).specifier
        operators = {s.operator for s in spec}
        assert operators == {"=="}, (
            f"{PCLUSTER_REQUIREMENT!r} uses {sorted(operators)}; only '==' makes "
            f"the artifact and the operator's venv resolve the same version"
        )
        version = next(iter(spec)).version
        assert version.count(".") >= 2, (
            f"{version!r} is not a full version, so it still admits a range"
        )

    def test_a_bounded_range_would_not_satisfy_the_guard(self):
        """Vacuity guard: the shipped-and-broken spelling must fail the test
        above. Written as the inverse rather than by calling it, since the
        assertion reads PCLUSTER_REQUIREMENT directly."""
        from packaging.requirements import Requirement

        broken = Requirement("aws-parallelcluster>=3.15,<3.17").specifier
        assert {s.operator for s in broken} != {"=="}

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


class TestTheDeploymentHasAProductionCaller:
    """Every deployment through session 53 was driven from a scratchpad
    script, so `deploy.py` was exercised only by tests and by hand -- which
    is how `deploy_tier`'s update path shipped broken (it raised
    ResourceConflictException on every existing function) and stayed broken
    until a live redeploy hit it. `deploy_mcp.py` is the caller.
    """

    _PATH = os.path.join(REPO_ROOT, "deploy_mcp.py")

    def _source(self):
        return io.open(self._PATH, encoding="utf-8").read()

    def test_the_entry_point_exists_and_is_executable(self):
        assert os.path.isfile(self._PATH), "deploy_mcp.py is missing"
        assert os.access(self._PATH, os.X_OK), "deploy_mcp.py is not executable"

    def test_it_carries_the_venv_guard_every_entry_point_has(self):
        """sys.prefix, not sys.executable -- Homebrew's python symlinks
        resolve outside .venv/."""
        src = self._source()
        assert "sys.prefix" in src
        assert "sys.executable" not in src

    def test_it_actually_calls_deploy_tier(self):
        """The point of the script. An AST walk rather than a substring so
        a mention in a docstring cannot satisfy it."""
        tree = ast.parse(self._source())
        called = {
            n.func.id for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "deploy_tier" in called

    def test_it_builds_for_lambdas_platform_not_the_operators(self):
        """An arm64 laptop otherwise stages arm64 wheels into an x86_64
        function, and the failure is an ImportError at the first invocation
        rather than at build time."""
        src = self._source()
        assert "manylinux2014_x86_64" in src
        assert "--only-binary" in src

    def test_it_checks_the_unzipped_limit_before_uploading(self):
        """Learning the 250 MB ceiling from CreateFunction is the thing
        prune_for_lambda's return value exists to prevent."""
        src = self._source()
        assert "ZIP_UNZIPPED_LIMIT_BYTES" in src
        assert "prune_for_lambda" in src

    def test_the_image_tier_cannot_be_deployed_as_a_zip(self):
        """stack-mutation-node has no zip form: pcluster's create/update
        need Node on PATH and a zip cannot supply it. The script must say
        so rather than build an artifact that could never work."""
        src = self._source()
        assert "--image-uri" in src
        assert "ImageUri" in src


# ---------------------------------------------------------------------------
# Policy convergence and teardown pruning
# ---------------------------------------------------------------------------

import datetime as _datetime  # noqa: E402
import json as _json  # noqa: E402

from botocore.exceptions import ClientError as _ClientError_t  # noqa: E402

import pcluster_core as _pc  # noqa: E402


def _stamp(n):
    return _datetime.datetime(2026, 1, 1) + _datetime.timedelta(days=n)


class _FakeIam:
    """IAM modelled on botocore's own iam/service-2.json, not on what the
    caller needs.

    Three rules are quoted from that model and are what let this fake
    disagree with the code under test:

      * CreatePolicyVersion -> LimitExceeded once five versions exist
        ("A managed policy can have up to five versions").
      * DeletePolicyVersion -> DeleteConflict on the default version
        ("You cannot delete the default version ... using this operation").
      * DeletePolicy -> DeleteConflict while any version remains
        ("you must delete all the policy's versions").

    The third is the one that matters: a fake that let DeletePolicy through
    would have agreed with the shipped teardown by construction and could
    never have shown it was broken.
    """

    def __init__(self):
        self.policies = {}
        self.roles = {}
        self.calls = []

    # -- helpers ------------------------------------------------------
    @staticmethod
    def _err(code, op):
        return _ClientError_t({"Error": {"Code": code, "Message": code}}, op)

    def _p(self, arn, op):
        if arn not in self.policies:
            raise self._err("NoSuchEntity", op)
        return self.policies[arn]

    def seed(self, arn, document, extra_versions=0):
        vs = [{"VersionId": "v1", "IsDefaultVersion": extra_versions == 0,
               "CreateDate": _stamp(1), "Document": document}]
        for i in range(extra_versions):
            vs.append({"VersionId": "v%d" % (i + 2),
                       "IsDefaultVersion": i == extra_versions - 1,
                       "CreateDate": _stamp(i + 2), "Document": document})
        self.policies[arn] = {"versions": vs}

    # -- policy API ---------------------------------------------------
    def create_policy(self, PolicyName, PolicyDocument):
        arn = "arn:aws:iam::123456789012:policy/" + PolicyName
        self.calls.append(("create_policy", PolicyName))
        if arn in self.policies:
            raise self._err("EntityAlreadyExists", "CreatePolicy")
        self.seed(arn, _json.loads(PolicyDocument))
        return {"Policy": {"Arn": arn}}

    def get_policy(self, PolicyArn):
        p = self._p(PolicyArn, "GetPolicy")
        d = next(v for v in p["versions"] if v["IsDefaultVersion"])
        return {"Policy": {"DefaultVersionId": d["VersionId"]}}

    def get_policy_version(self, PolicyArn, VersionId):
        p = self._p(PolicyArn, "GetPolicyVersion")
        v = next((x for x in p["versions"] if x["VersionId"] == VersionId), None)
        if v is None:
            raise self._err("NoSuchEntity", "GetPolicyVersion")
        # botocore's after-call.iam handler hands this back already decoded.
        return {"PolicyVersion": {"Document": v["Document"]}}

    def list_policy_versions(self, PolicyArn):
        p = self._p(PolicyArn, "ListPolicyVersions")
        return {"Versions": [
            {k: v[k] for k in ("VersionId", "IsDefaultVersion", "CreateDate")}
            for v in p["versions"]
        ]}

    def create_policy_version(self, PolicyArn, PolicyDocument, SetAsDefault=False):
        p = self._p(PolicyArn, "CreatePolicyVersion")
        if len(p["versions"]) >= 5:
            raise self._err("LimitExceeded", "CreatePolicyVersion")
        nxt = max(int(v["VersionId"][1:]) for v in p["versions"]) + 1
        vid = "v%d" % nxt
        if SetAsDefault:
            for v in p["versions"]:
                v["IsDefaultVersion"] = False
        p["versions"].append({"VersionId": vid, "IsDefaultVersion": bool(SetAsDefault),
                              "CreateDate": _stamp(nxt),
                              "Document": _json.loads(PolicyDocument)})
        self.calls.append(("create_policy_version", PolicyArn, vid))
        return {"PolicyVersion": {"VersionId": vid}}

    def delete_policy_version(self, PolicyArn, VersionId):
        p = self._p(PolicyArn, "DeletePolicyVersion")
        v = next((x for x in p["versions"] if x["VersionId"] == VersionId), None)
        if v is None:
            raise self._err("NoSuchEntity", "DeletePolicyVersion")
        if v["IsDefaultVersion"]:
            raise self._err("DeleteConflict", "DeletePolicyVersion")
        p["versions"].remove(v)
        self.calls.append(("delete_policy_version", PolicyArn, VersionId))

    def delete_policy(self, PolicyArn):
        p = self._p(PolicyArn, "DeletePolicy")
        if len(p["versions"]) > 1:
            raise self._err("DeleteConflict", "DeletePolicy")
        del self.policies[PolicyArn]
        self.calls.append(("delete_policy", PolicyArn))

    # -- role API -----------------------------------------------------
    def create_role(self, RoleName, AssumeRolePolicyDocument, Description=""):
        if RoleName in self.roles:
            raise self._err("EntityAlreadyExists", "CreateRole")
        self.roles[RoleName] = []

    def attach_role_policy(self, RoleName, PolicyArn):
        self.roles.setdefault(RoleName, []).append(PolicyArn)

    def detach_role_policy(self, RoleName, PolicyArn):
        if RoleName not in self.roles:
            raise self._err("NoSuchEntity", "DetachRolePolicy")

    def delete_role(self, RoleName):
        if RoleName not in self.roles:
            raise self._err("NoSuchEntity", "DeleteRole")
        del self.roles[RoleName]


class TestTheFakeEnforcesTheServiceContract:
    """Vacuity guards. If the fake permits what IAM forbids, every test
    below passes while the code stays broken -- which is exactly how the
    shipped teardown survived until a live run."""

    ARN = "arn:aws:iam::123456789012:policy/x"

    def test_delete_policy_refuses_while_versions_remain(self):
        iam = _FakeIam()
        iam.seed(self.ARN, {"a": 1}, extra_versions=2)
        with pytest.raises(_ClientError_t) as ei:
            iam.delete_policy(PolicyArn=self.ARN)
        assert ei.value.response["Error"]["Code"] == "DeleteConflict"

    def test_delete_policy_version_refuses_the_default(self):
        iam = _FakeIam()
        iam.seed(self.ARN, {"a": 1}, extra_versions=1)
        default = next(v["VersionId"] for v in iam.policies[self.ARN]["versions"]
                       if v["IsDefaultVersion"])
        with pytest.raises(_ClientError_t):
            iam.delete_policy_version(PolicyArn=self.ARN, VersionId=default)

    def test_create_policy_version_stops_at_five(self):
        iam = _FakeIam()
        iam.seed(self.ARN, {"a": 1}, extra_versions=4)
        assert len(iam.policies[self.ARN]["versions"]) == 5
        with pytest.raises(_ClientError_t) as ei:
            iam.create_policy_version(PolicyArn=self.ARN,
                                      PolicyDocument='{"b": 2}')
        assert ei.value.response["Error"]["Code"] == "LimitExceeded"


class TestAPolicyEditReachesTheAccount:
    """`_setup_mcp_infra` reused an existing policy and never compared its
    document, so editing a templates/MCP*.json_src changed nothing in the
    account and the run still printed success. Three real IAM fixes had to
    be pushed by hand because of it."""

    ARN = "arn:aws:iam::123456789012:policy/p"
    DOC = {"Version": "2012-10-17",
           "Statement": [{"Effect": "Allow", "Action": "s3:GetObject",
                          "Resource": "*"}]}

    def test_an_unchanged_document_is_recognized_as_current(self):
        iam = _FakeIam()
        iam.seed(self.ARN, self.DOC)
        current, vid = _pc._mcp_policy_is_current(
            iam, arn=self.ARN, rendered=_json.dumps(self.DOC))
        assert current is True and vid == "v1"

    def test_a_changed_document_is_recognized_as_stale(self):
        iam = _FakeIam()
        iam.seed(self.ARN, self.DOC)
        changed = _json.loads(_json.dumps(self.DOC))
        changed["Statement"][0]["Action"] = "s3:PutObject"
        current, _ = _pc._mcp_policy_is_current(
            iam, arn=self.ARN, rendered=_json.dumps(changed))
        assert current is False

    def test_a_url_encoded_document_still_compares(self):
        """botocore normally decodes Document, but decode_quoted_jsondoc
        swallows a failure and hands back the raw string."""
        import urllib.parse

        iam = _FakeIam()
        iam.seed(self.ARN, urllib.parse.quote(_json.dumps(self.DOC)))
        current, _ = _pc._mcp_policy_is_current(
            iam, arn=self.ARN, rendered=_json.dumps(self.DOC))
        assert current is True

    def test_the_update_becomes_the_new_default(self):
        iam = _FakeIam()
        iam.seed(self.ARN, self.DOC)
        changed = {"Version": "2012-10-17", "Statement": []}
        vid = _pc._update_mcp_policy(iam, arn=self.ARN,
                                     rendered=_json.dumps(changed))
        versions = iam.policies[self.ARN]["versions"]
        assert vid == "v2"
        assert [v["VersionId"] for v in versions if v["IsDefaultVersion"]] == ["v2"]
        current, _ = _pc._mcp_policy_is_current(
            iam, arn=self.ARN, rendered=_json.dumps(changed))
        assert current is True

    def test_the_five_version_ceiling_is_made_room_for(self):
        """IAM allows five. Without pruning, the fifth edit of a policy
        fails with LimitExceeded -- on a long-lived policy that is a
        certainty, not an edge case."""
        iam = _FakeIam()
        iam.seed(self.ARN, self.DOC, extra_versions=4)
        assert len(iam.policies[self.ARN]["versions"]) == 5
        vid = _pc._update_mcp_policy(
            iam, arn=self.ARN, rendered=_json.dumps({"Version": "2012-10-17",
                                                     "Statement": []}))
        versions = iam.policies[self.ARN]["versions"]
        assert len(versions) <= 5
        assert [v["VersionId"] for v in versions if v["IsDefaultVersion"]] == [vid]
        # Oldest-first, and never the one in force.
        assert ("delete_policy_version", self.ARN, "v1") in iam.calls


class TestTeardownRemovesEveryPolicyVersion:
    """DeletePolicy refuses while a non-default version exists, so the
    moment deploy learned to add versions, teardown broke. Observed live:
    three MCP policies left in the account.

    The two halves must land together, which is why this asserts the
    policy is *gone*, not that a particular call was made.
    """

    ARN = "arn:aws:iam::123456789012:policy/p"

    def test_a_versioned_policy_is_deleted(self):
        iam = _FakeIam()
        iam.seed(self.ARN, {"a": 1}, extra_versions=3)
        _pc._delete_policy_with_versions(iam, self.ARN)
        assert self.ARN not in iam.policies

    def test_a_single_version_policy_is_deleted(self):
        iam = _FakeIam()
        iam.seed(self.ARN, {"a": 1})
        _pc._delete_policy_with_versions(iam, self.ARN)
        assert self.ARN not in iam.policies

    def test_an_absent_policy_is_not_an_error_to_the_caller(self):
        """Teardown's _try classifies NoSuchEntity as absent; this must
        raise it rather than swallow it, or a policy that was never there
        is reported as deleted."""
        iam = _FakeIam()
        with pytest.raises(_ClientError_t) as ei:
            _pc._delete_policy_with_versions(iam, self.ARN)
        assert _pc._is_missing_iam_entity(ei.value)

    def test_the_full_teardown_removes_versioned_policies(self):
        iam = _FakeIam()
        for basename in _pc._mcp_policy_templates():
            arn = ("arn:aws:iam::123456789012:policy/"
                   + _pc._mcp_policy_name(basename))
            iam.seed(arn, {"a": 1}, extra_versions=2)
        for tier in _pc._MCP_LAMBDA_TIERS:
            iam.roles[_pc._mcp_role_name(tier)] = []

        result = _pc._delete_mcp_infra(
            iam, aws_account_id="123456789012", verbose=False)

        assert not result.failed, result.failed
        assert iam.policies == {}, "policies left behind: %s" % list(iam.policies)


class TestAnInfrastructureFlagIsNotADeployment:
    """`--setup-infra` redeployed every zip tier as a side effect of
    creating IAM -- six 146 MB pip installs to attach a permissions
    boundary. `--setup-gateway` had a short-circuit with a comment arguing
    exactly this case; nothing carried it over to `--setup-infra`, and
    nothing tested either.

    The expression lived inside main(), after sts.get_caller_identity(), so
    it was unreachable by any test the no-AWS guard would allow. It is a
    module-level function now for that reason.
    """

    class _Args:
        # Mirrors what argparse actually produces. A stub that lags the real
        # namespace fails as an AttributeError inside tiers_to_deploy rather
        # than as a statement about behavior, which is how a new flag looks
        # like five broken tests.
        def __init__(self, tier=None, setup_infra=False, setup_gateway=False,
                     teardown=False, bootstrap=False):
            self.tier = tier
            self.setup_infra = setup_infra
            self.setup_gateway = setup_gateway
            self.teardown = teardown
            self.bootstrap = bootstrap

    def _zips(self):
        from mcp_server.packaging import TIER_PACKAGES

        return [t for t, s in TIER_PACKAGES.items() if s["kind"] != "image"]

    def test_setup_infra_alone_deploys_nothing(self):
        import deploy_mcp

        assert deploy_mcp.tiers_to_deploy(self._Args(setup_infra=True)) == []

    def test_setup_gateway_alone_deploys_nothing(self):
        import deploy_mcp

        assert deploy_mcp.tiers_to_deploy(self._Args(setup_gateway=True)) == []

    def test_an_explicit_tier_still_wins(self):
        """The two are combinable on purpose: setting up IAM and deploying
        the tier that needs it in one run is a real workflow."""
        import deploy_mcp

        args = self._Args(tier=["read-only"], setup_infra=True)
        assert deploy_mcp.tiers_to_deploy(args) == ["read-only"]

    def test_a_bare_run_still_deploys_every_zip_tier(self):
        """Vacuity guard: the short-circuit must not have become
        `always return []`, which would satisfy both tests above while
        deploying nothing, ever."""
        import deploy_mcp

        assert deploy_mcp.tiers_to_deploy(self._Args()) == self._zips()
        assert self._zips(), "no zip tiers at all -- the guard is vacuous"

    def test_the_image_tier_is_never_implicit(self):
        """It needs --image-uri, so including it in a bare run turns every
        such run into an error."""
        import deploy_mcp

        assert "stack-mutation-node" not in deploy_mcp.tiers_to_deploy(self._Args())


class TestTheTransportCanBeRemoved:
    """`deploy_mcp.py` could build the whole transport and remove none of it.

    `delete_mcp_functions` and `_delete_mcp_infra` existed but nothing called
    either, and the REST API and Cognito user pool had no teardown code
    anywhere -- so the internet-facing endpoint outlived every teardown. A
    live teardown had to be driven from a scratchpad script, which is where
    the ordering bug below was found.
    """

    class _Args:
        def __init__(self, **kw):
            self.tier = kw.get("tier")
            self.setup_infra = kw.get("setup_infra", False)
            self.setup_gateway = kw.get("setup_gateway", False)
            self.teardown = kw.get("teardown", False)
            self.bootstrap = kw.get("bootstrap", False)

    def test_teardown_builds_nothing(self):
        """Building a 146 MB artifact so the function it would deploy to can
        be deleted is minutes of pip for a thing about to not exist."""
        import deploy_mcp

        assert deploy_mcp.tiers_to_deploy(self._Args(teardown=True)) == []

    def test_teardown_builds_nothing_even_with_an_explicit_tier(self):
        """`--tier` wins over the other infrastructure flags on purpose;
        it must not win over this one."""
        import deploy_mcp

        args = self._Args(teardown=True, tier=["read-only"])
        assert deploy_mcp.tiers_to_deploy(args) == []

    def test_a_normal_run_still_deploys(self):
        """Vacuity guard: the short-circuit must not have become
        `return []` for every caller."""
        import deploy_mcp

        assert deploy_mcp.tiers_to_deploy(self._Args()) != []


class _CognitoWithDomain:
    """Modelled on the DeleteUserPool contract, not on the caller.

    Real Cognito refuses to delete a pool that still has a domain, and the
    error names no domain -- so a caller that guessed one from the pool name
    deletes nothing and reports success on the retry. That is what happened
    live; this fake reproduces it.
    """

    def __init__(self, pool_name, pool_id, domain):
        self._pools = {pool_id: {"Name": pool_name, "Id": pool_id,
                                 "Domain": domain}}
        self.calls = []

    def list_user_pools(self, MaxResults=60):
        return {"UserPools": [{"Name": p["Name"], "Id": pid}
                              for pid, p in self._pools.items()]}

    def describe_user_pool(self, UserPoolId):
        self.calls.append(("describe", UserPoolId))
        return {"UserPool": dict(self._pools[UserPoolId])}

    def delete_user_pool_domain(self, Domain, UserPoolId):
        pool = self._pools[UserPoolId]
        if pool.get("Domain") != Domain:
            raise RuntimeError("no such domain: %r" % Domain)
        pool["Domain"] = None
        self.calls.append(("delete_domain", Domain))

    def delete_user_pool(self, UserPoolId):
        pool = self._pools[UserPoolId]
        if pool.get("Domain"):
            raise RuntimeError(
                "User pool cannot be deleted. It has a domain configured "
                "that should be deleted first.")
        del self._pools[UserPoolId]
        self.calls.append(("delete_pool", UserPoolId))


class TestTheCognitoDomainGoesBeforeThePool:
    """The ordering bug, found live.

    `delete_user_pool` fails while a domain exists, and the domain string is
    not the pool name -- the pool was `parallelclustermaker-mcp-<acct>-<region>`
    while its domain was `pclustermaker-mcp-yqdbaeo8t`. A teardown that
    guessed removed nothing.
    """

    POOL = "parallelclustermaker-mcp-123456789012-us-east-1"
    PID = "us-east-1_AbCdEf"
    DOMAIN = "pclustermaker-mcp-abcdef"

    def test_the_pool_is_deleted_domain_first(self):
        from mcp_server.deploy import delete_cognito_pool

        cog = _CognitoWithDomain(self.POOL, self.PID, self.DOMAIN)
        removed = delete_cognito_pool(
            cog, pool_name_prefix="parallelclustermaker-mcp", suppress=False)
        assert removed == [self.POOL]
        assert cog._pools == {}, "the pool survived"
        order = [c[0] for c in cog.calls]
        assert order.index("delete_domain") < order.index("delete_pool"), (
            "the pool delete was attempted before its domain was removed"
        )

    def test_the_domain_is_read_not_guessed(self):
        """The domain is not derivable from the pool name; describe_user_pool
        is the only authority for it."""
        from mcp_server.deploy import delete_cognito_pool

        cog = _CognitoWithDomain(self.POOL, self.PID, self.DOMAIN)
        delete_cognito_pool(cog, pool_name_prefix="parallelclustermaker-mcp",
                            suppress=False)
        assert ("describe", self.PID) in cog.calls
        assert ("delete_domain", self.DOMAIN) in cog.calls

    def test_the_fake_refuses_a_pool_that_still_has_a_domain(self):
        """Vacuity guard. If the fake allowed it, the ordering test above
        would pass with the calls in either order."""
        cog = _CognitoWithDomain(self.POOL, self.PID, self.DOMAIN)
        with pytest.raises(RuntimeError, match="domain configured"):
            cog.delete_user_pool(UserPoolId=self.PID)

    def test_a_pool_without_a_domain_still_deletes(self):
        from mcp_server.deploy import delete_cognito_pool

        cog = _CognitoWithDomain(self.POOL, self.PID, None)
        removed = delete_cognito_pool(
            cog, pool_name_prefix="parallelclustermaker-mcp", suppress=False)
        assert removed == [self.POOL]
        assert "delete_domain" not in [c[0] for c in cog.calls]

    def test_an_unrelated_pool_is_left_alone(self):
        from mcp_server.deploy import delete_cognito_pool

        cog = _CognitoWithDomain("someone-elses-pool", self.PID, None)
        removed = delete_cognito_pool(
            cog, pool_name_prefix="parallelclustermaker-mcp", suppress=False)
        assert removed == []
        assert cog._pools, "an unrelated user pool was deleted"


class _Apigw:
    def __init__(self, names):
        self._apis = [{"name": n, "id": "id-%d" % i}
                      for i, n in enumerate(names)]
        self.deleted = []

    def get_rest_apis(self):
        return {"items": list(self._apis)}

    def delete_rest_api(self, restApiId):
        self._apis = [a for a in self._apis if a["id"] != restApiId]
        self.deleted.append(restApiId)


class TestTheGatewayIsRemoved:
    def test_the_mcp_api_goes(self):
        from mcp_server.deploy import delete_gateway

        apigw = _Apigw(["pclustermaker-mcp"])
        assert delete_gateway(apigw, suppress=False) == ["pclustermaker-mcp"]
        assert apigw._apis == []

    def test_someone_elses_api_stays(self):
        """The API is found by name prefix because setup_gateway records no
        id, so the match has to be narrow enough not to take an unrelated
        REST API with it."""
        from mcp_server.deploy import delete_gateway

        apigw = _Apigw(["someone-elses-api", "pclustermaker-mcp"])
        removed = delete_gateway(apigw, suppress=False)
        assert removed == ["pclustermaker-mcp"]
        assert [a["name"] for a in apigw._apis] == ["someone-elses-api"]


class TestTheGatewayTimeoutIsTheRealCeiling:
    """API Gateway REST caps an integration at 29s. That is far tighter than
    the Lambda's 900s, and it governs every remote tool call.

    CLAUDE.md described the 900s ceiling as the constraint that forces
    `apply_queue_config` to be local-only, which is true but incomplete: a
    remote call has ~29s. Past it the caller receives a timeout body while
    the Lambda keeps running and the mutation *succeeds* -- so the failure
    is not just a wrong answer, it is a wrong answer about a thing that
    happened. A client that retries submits a second update against a stack
    already updating.

    Measured live: `apply_cluster_update` adding one queue ran 41,992 ms in
    the Lambda; the caller saw failure at 29.4s; the update completed. R4's
    earlier calls were 14-20s and stayed under it, which is why nothing
    caught this.

    The number was already in `deploy.py` -- in a comment explaining why the
    authorizer's timeout is 10s -- and had never been generalized to the
    tier tools. It is a named constant now so it is visible where
    integrations are wired.
    """

    def test_the_constant_is_the_rest_maximum(self):
        from mcp_server.deploy import GATEWAY_INTEGRATION_TIMEOUT_MS

        assert GATEWAY_INTEGRATION_TIMEOUT_MS == 29_000, (
            "29s is API Gateway REST's maximum integration timeout; a higher "
            "value is not a parameter, it needs a service quota increase"
        )

    def test_every_integration_is_wired_with_it(self):
        """Explicit, not inherited from the AWS default. Inherited, the real
        ceiling on every remote call is invisible at the call site -- which
        is how a 42s tool call came to be written in the first place."""
        import ast
        import io
        import os

        path = os.path.join(REPO_ROOT, "mcp_server", "deploy.py")
        tree = ast.parse(io.open(path, encoding="utf-8").read())

        calls = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "put_integration"
        ]
        assert calls, "no put_integration call found -- the sweep is vacuous"
        for c in calls:
            names = [k.arg for k in c.keywords]
            assert "timeoutInMillis" in names, (
                "put_integration does not set timeoutInMillis; the 29s "
                "ceiling then depends on an AWS default nothing states"
            )

    def test_the_lambda_ceiling_is_still_the_larger_one(self):
        """Vacuity guard, and the point of the distinction: the Lambda may
        legitimately run far longer than the gateway will wait. Both numbers
        are real; they bound different things."""
        from mcp_server.deploy import (
            GATEWAY_INTEGRATION_TIMEOUT_MS,
            LAMBDA_MAX_TIMEOUT_SECONDS,
        )

        assert LAMBDA_MAX_TIMEOUT_SECONDS * 1000 > GATEWAY_INTEGRATION_TIMEOUT_MS
        assert LAMBDA_MAX_TIMEOUT_SECONDS == 900


class _CognitoUsers:
    """Modelled on the AdminCreateUser/AdminSetUserPassword contract.

    Read out of `botocore/data/cognito-idp/2016-04-18/service-2.json.gz`,
    not recalled: `AdminCreateUser` requires `UserPoolId` and `Username` and
    lists `UsernameExistsException` among its errors;
    `AdminSetUserPassword` requires `UserPoolId`, `Username` and `Password`.
    A fake that accepts whatever the caller happens to send agrees with the
    caller by construction and cannot fail where the caller is wrong.
    """

    class UsernameExistsException(Exception):
        pass

    class InvalidParameterException(Exception):
        pass

    def __init__(self):
        self.users = {}
        self.calls = []

    def admin_create_user(self, **kw):
        for required in ("UserPoolId", "Username"):
            if required not in kw:
                raise self.InvalidParameterException(
                    f"{required} is a required member of AdminCreateUserRequest")
        self.calls.append(("create", dict(kw)))
        name = kw["Username"]
        if name in self.users:
            raise self.UsernameExistsException("User account already exists")
        # Without MessageAction=SUPPRESS, real Cognito mails an invitation
        # and leaves the account in FORCE_CHANGE_PASSWORD.
        self.users[name] = {
            "status": ("CONFIRMED" if kw.get("MessageAction") == "SUPPRESS"
                       else "FORCE_CHANGE_PASSWORD"),
            "attrs": {a["Name"]: a["Value"] for a in kw.get("UserAttributes") or []},
            "password": None,
        }

    def admin_set_user_password(self, **kw):
        for required in ("UserPoolId", "Username", "Password"):
            if required not in kw:
                raise self.InvalidParameterException(
                    f"{required} is a required member of "
                    f"AdminSetUserPasswordRequest")
        self.calls.append(("set_password", dict(kw)))
        user = self.users[kw["Username"]]
        user["password"] = kw["Password"]
        if kw.get("Permanent"):
            user["status"] = "CONFIRMED"


class TestADeployedTransportHasSomeoneToSignInAs:
    """Nothing in the deploy path ever created a Cognito user.

    `--setup-infra` created the pool and `--setup-gateway` the Hosted UI
    domain, and there it stopped: the connector reached a login page with no
    account behind it. R3 minted its tokens against a user made out of band,
    and that step appeared in no script and no document -- which is most of
    why the browser session stayed unrun.
    """

    POOL = "us-east-1_AbCdEf"

    def test_the_user_is_created_and_can_sign_in_immediately(self):
        from mcp_server.deploy import ensure_cognito_user

        cog = _CognitoUsers()
        created = ensure_cognito_user(
            cog, pool_id=self.POOL, username="ops@example.com", password="Aa1!aaaaaa")
        assert created is True
        assert cog.users["ops@example.com"]["status"] == "CONFIRMED"
        assert cog.users["ops@example.com"]["password"] == "Aa1!aaaaaa"

    def test_the_invitation_email_is_suppressed(self):
        """`MessageAction="SUPPRESS"` is load-bearing, not tidiness. The
        default mails through Cognito's own sender, which needs a verified
        email attribute and is capped at 50 a day -- on a pool with no SES
        configuration the create fails outright."""
        from mcp_server.deploy import ensure_cognito_user

        cog = _CognitoUsers()
        ensure_cognito_user(cog, pool_id=self.POOL, username="ops",
                            password="Aa1!aaaaaa")
        create = dict(cog.calls[0][1])
        assert create["MessageAction"] == "SUPPRESS"

    def test_the_password_is_permanent(self):
        """Without `Permanent=True` the account stays in
        FORCE_CHANGE_PASSWORD and the Hosted UI demands a reset the operator
        was never mailed -- the invitation having been suppressed above."""
        from mcp_server.deploy import ensure_cognito_user

        cog = _CognitoUsers()
        ensure_cognito_user(cog, pool_id=self.POOL, username="ops",
                            password="Aa1!aaaaaa")
        setp = dict(cog.calls[1][1])
        assert setp["Permanent"] is True

    def test_the_fake_leaves_an_unsuppressed_user_unable_to_sign_in(self):
        """Vacuity guard for the two tests above: if the fake ignored
        MessageAction and Permanent, both would pass with either value."""
        cog = _CognitoUsers()
        cog.admin_create_user(UserPoolId=self.POOL, Username="ops")
        assert cog.users["ops"]["status"] == "FORCE_CHANGE_PASSWORD"
        cog.admin_set_user_password(UserPoolId=self.POOL, Username="ops",
                                    Password="Aa1!aaaaaa")
        assert cog.users["ops"]["status"] == "FORCE_CHANGE_PASSWORD"

    def test_rerunning_resets_the_password_rather_than_failing(self):
        """The reason to re-run this is almost always that the generated
        password was lost, and Cognito stores a hash -- so the only remedy
        is to set a new one. An existing user must not be an error."""
        from mcp_server.deploy import ensure_cognito_user

        cog = _CognitoUsers()
        ensure_cognito_user(cog, pool_id=self.POOL, username="ops",
                            password="Aa1!aaaaaa")
        created = ensure_cognito_user(cog, pool_id=self.POOL, username="ops",
                                      password="Bb2@bbbbbb")
        assert created is False
        assert cog.users["ops"]["password"] == "Bb2@bbbbbb"

    def test_an_email_username_gets_the_email_attribute(self):
        from mcp_server.deploy import ensure_cognito_user

        cog = _CognitoUsers()
        ensure_cognito_user(cog, pool_id=self.POOL, username="ops@example.com",
                            password="Aa1!aaaaaa")
        attrs = cog.users["ops@example.com"]["attrs"]
        assert attrs == {"email": "ops@example.com", "email_verified": "true"}

    def test_a_plain_username_gets_no_email_attribute(self):
        """Setting `email` to something that is not an address is an
        InvalidParameterException, and the pool does not require it."""
        from mcp_server.deploy import ensure_cognito_user

        cog = _CognitoUsers()
        ensure_cognito_user(cog, pool_id=self.POOL, username="ops",
                            password="Aa1!aaaaaa")
        assert cog.users["ops"]["attrs"] == {}

    def test_an_unrelated_failure_is_not_swallowed(self):
        """The `except` catches UsernameExistsException by name. Widened to
        a bare `except Exception`, a pool that does not exist would report a
        user created."""
        from mcp_server.deploy import ensure_cognito_user

        class _Broken(_CognitoUsers):
            def admin_create_user(self, **kw):
                raise RuntimeError("ResourceNotFoundException: no such pool")

        with pytest.raises(RuntimeError, match="no such pool"):
            ensure_cognito_user(_Broken(), pool_id=self.POOL, username="ops",
                                password="Aa1!aaaaaa")


class TestTheGeneratedPasswordSatisfiesCognito:
    """A generated password missing one character class fails at
    `AdminSetUserPassword` with InvalidPasswordException -- after the user
    already exists, so the run is half done and the remedy is not obvious.
    """

    def test_every_required_class_is_present(self):
        import string

        from mcp_server.deploy import _PASSWORD_SYMBOLS, generate_user_password

        for _ in range(200):
            pw = generate_user_password()
            assert any(c in string.ascii_uppercase for c in pw)
            assert any(c in string.ascii_lowercase for c in pw)
            assert any(c in string.digits for c in pw)
            assert any(c in _PASSWORD_SYMBOLS for c in pw)

    def test_it_meets_the_minimum_length(self):
        from mcp_server.deploy import generate_user_password

        assert len(generate_user_password()) >= 8

    def test_a_length_below_the_policy_minimum_is_refused(self):
        """Rather than returning something Cognito will reject."""
        from mcp_server.deploy import generate_user_password

        with pytest.raises(ValueError, match="8 characters"):
            generate_user_password(length=4)

    def test_two_calls_do_not_agree(self):
        """Vacuity guard: a constant would satisfy every assertion above."""
        from mcp_server.deploy import generate_user_password

        assert len({generate_user_password() for _ in range(50)}) == 50


class TestBootstrapStandsUpTheWholeTransport:
    """`--setup-infra --setup-gateway` deployed *no functions*.

    An infrastructure flag suppresses the default tier list on purpose --
    rebuilding six 146 MB artifacts to attach a route is minutes of pip for
    nothing. But that makes the obvious one-command spelling of "stand it
    all up" build a REST API routing to functions that do not exist, and
    fail at the first call rather than at deploy. Every deploy so far was
    driven by someone who already knew to name all six tiers.
    """

    class _Args:
        def __init__(self, **kw):
            self.tier = kw.get("tier")
            self.setup_infra = kw.get("setup_infra", False)
            self.setup_gateway = kw.get("setup_gateway", False)
            self.teardown = kw.get("teardown", False)
            self.bootstrap = kw.get("bootstrap", False)
            self.create_user = kw.get("create_user")

    def _zips(self):
        from mcp_server.packaging import TIER_PACKAGES

        return [t for t, s in TIER_PACKAGES.items() if s["kind"] != "image"]

    def _fail(self, message):
        raise AssertionError(message)

    def test_bootstrap_deploys_every_zip_tier(self):
        import deploy_mcp

        args = deploy_mcp.normalize_bootstrap(
            self._Args(bootstrap=True), self._fail)
        assert deploy_mcp.tiers_to_deploy(args) == self._zips()

    def test_bootstrap_survives_its_own_implied_setup_flags(self):
        """The regression this flag is one line away from: normalization
        sets `setup_infra`, and the short-circuit keyed on it then returns
        [] unless `bootstrap` is checked first."""
        import deploy_mcp

        args = deploy_mcp.normalize_bootstrap(
            self._Args(bootstrap=True), self._fail)
        assert args.setup_infra is True and args.setup_gateway is True
        assert deploy_mcp.tiers_to_deploy(args) != []

    def test_the_short_circuit_is_still_there_without_bootstrap(self):
        """Vacuity guard: the fix must not have been "stop short-circuiting",
        which would restore the six-artifact rebuild on every --setup-infra."""
        import deploy_mcp

        assert deploy_mcp.tiers_to_deploy(self._Args(setup_infra=True)) == []
        assert deploy_mcp.tiers_to_deploy(self._Args(setup_gateway=True)) == []

    def test_bootstrap_never_pulls_in_the_container_tier(self):
        """It needs --image-uri and a container runtime, so including it
        would make the one-command path an error on every machine without
        one -- and six of the seven tiers are zips, so the transport is
        useful without it."""
        import deploy_mcp

        args = deploy_mcp.normalize_bootstrap(
            self._Args(bootstrap=True), self._fail)
        assert "stack-mutation-node" not in deploy_mcp.tiers_to_deploy(args)

    def test_an_explicit_tier_still_wins(self):
        import deploy_mcp

        args = deploy_mcp.normalize_bootstrap(
            self._Args(bootstrap=True, tier=["read-only"]), self._fail)
        assert deploy_mcp.tiers_to_deploy(args) == ["read-only"]

    def test_bootstrap_and_teardown_are_refused(self):
        import deploy_mcp

        with pytest.raises(AssertionError, match="opposites"):
            deploy_mcp.normalize_bootstrap(
                self._Args(bootstrap=True, teardown=True), self._fail)

    def test_create_user_and_teardown_are_refused(self):
        """Creating a user in the pool the same run deletes."""
        import deploy_mcp

        with pytest.raises(AssertionError, match="deletes"):
            deploy_mcp.normalize_bootstrap(
                self._Args(create_user="ops", teardown=True), self._fail)

    def test_a_plain_run_is_not_rejected(self):
        """Vacuity guard for the two refusals: `fail` must not fire on
        every call."""
        import deploy_mcp

        deploy_mcp.normalize_bootstrap(self._Args(), self._fail)
        deploy_mcp.normalize_bootstrap(self._Args(bootstrap=True), self._fail)
        deploy_mcp.normalize_bootstrap(self._Args(teardown=True), self._fail)


class _SimulatingIam:
    """Modelled on SimulatePrincipalPolicy's contract, not on the caller.

    Read out of botocore's iam service model: PolicySourceArn and
    ActionNames are required, EvaluationResults carries EvalActionName and
    EvalDecision (one of allowed/explicitDeny/implicitDeny), and
    MissingContextValues names condition keys the simulation was not given
    -- which is how a *conditional* grant reports, and is not a denial.
    """

    def __init__(self, allowed=(), missing_context=(), raises=None):
        self.allowed = set(allowed)
        self.missing_context = set(missing_context)
        self.raises = raises
        self.source_arns = []

    def simulate_principal_policy(self, **kw):
        if self.raises:
            raise self.raises
        for required in ("PolicySourceArn", "ActionNames"):
            if required not in kw:
                raise AssertionError(f"{required} is required")
        self.source_arns.append(kw["PolicySourceArn"])
        results = []
        for a in kw["ActionNames"]:
            r = {"EvalActionName": a,
                 "EvalDecision": "allowed" if a in self.allowed else "implicitDeny"}
            if a in self.missing_context:
                r["MissingContextValues"] = ["iam:PermissionsBoundary"]
            results.append(r)
        return {"EvaluationResults": results}


class TestTheDeploySaysWhatPermissionItIsMissing:
    """deploy_mcp.py cannot create MCPDeployPolicy -- under that policy
    iam:CreatePolicy is scoped to pclustermaker-mcp-policy-*, which the
    deploy policy's own name does not match, and nothing lets it attach a
    policy to the caller's identity. That is deliberate: a deploy tool able
    to grant itself permissions has no ceiling.

    What it can do is stop before the first mutation and name the fix,
    rather than failing six tiers in with an AccessDenied whose cause is a
    missing policy on the operator's own identity.
    """

    ACCT = "123456789012"

    def _all_probe_actions(self):
        from mcp_server.deploy import _DEPLOY_PROBES

        return [a for a, _r in _DEPLOY_PROBES]

    def _call(self, iam, arn=None):
        from mcp_server.deploy import preflight_deploy_permissions

        return preflight_deploy_permissions(
            iam, caller_arn=arn or f"arn:aws:iam::{self.ACCT}:user/deployer",
            aws_account_id=self.ACCT, region="us-east-1",
        )

    def test_a_fully_permitted_identity_reports_nothing_missing(self):
        iam = _SimulatingIam(allowed=self._all_probe_actions())
        assert self._call(iam) == []

    def test_a_denied_action_is_named(self):
        actions = self._all_probe_actions()
        iam = _SimulatingIam(allowed=[a for a in actions if a != "lambda:CreateFunction"])
        assert self._call(iam) == ["lambda:CreateFunction"]

    def test_an_unanswerable_check_is_none_not_a_denial(self):
        """iam:SimulatePrincipalPolicy is itself a permission and
        MCPDeployPolicy does not grant it, so an identity holding exactly
        the right policy cannot run this check. None means "could not
        tell"; the caller warns and proceeds. `_check_external_nfs_reachable`
        set that precedent -- only a confirmed failure is fatal."""
        iam = _SimulatingIam(raises=RuntimeError("AccessDenied: iam:SimulatePrincipalPolicy"))
        assert self._call(iam) is None

    def test_none_and_empty_are_distinguishable(self):
        """Vacuity guard, and the bug this shape invites: `if not missing`
        treats "could not tell" and "nothing missing" identically, while
        `if missing` treats "could not tell" as fine. They are different
        answers and the caller branches on both."""
        assert self._call(_SimulatingIam(raises=RuntimeError("x"))) is None
        assert self._call(_SimulatingIam(allowed=self._all_probe_actions())) == []

    def test_a_conditional_grant_is_not_reported_as_denied(self):
        """A grant conditional on iam:PermissionsBoundary simulated without
        that context key comes back implicitDeny with the key in
        MissingContextValues. Counting it as a denial would tell an
        operator with the correct policy to go install the policy they
        already have."""
        actions = self._all_probe_actions()
        iam = _SimulatingIam(allowed=[], missing_context=actions)
        assert self._call(iam) == []

    def test_no_probe_is_a_conditional_grant(self):
        """The guard above handles it, but the probe set should not rely on
        that: every probed action must come from a MCPDeployPolicy
        statement carrying no Condition."""
        import json
        import os

        from mcp_server.deploy import _DEPLOY_PROBES

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        doc = json.load(open(os.path.join(root, "templates", "MCPDeployPolicy.json_src")))
        conditional = set()
        for st in doc["Statement"]:
            if not st.get("Condition"):
                continue
            acts = st["Action"] if isinstance(st["Action"], list) else [st["Action"]]
            conditional.update(acts)
        for action, _r in _DEPLOY_PROBES:
            assert action not in conditional, f"{action} is a conditional grant"

    def test_every_probe_is_actually_granted_by_the_policy(self):
        """Vacuity guard the other way: a probe for an action the policy
        never grants would fail every correctly-configured deploy."""
        import json
        import os

        from mcp_server.deploy import _DEPLOY_PROBES

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        doc = json.load(open(os.path.join(root, "templates", "MCPDeployPolicy.json_src")))
        granted = set()
        for st in doc["Statement"]:
            if st.get("Effect") != "Allow":
                continue
            acts = st["Action"] if isinstance(st["Action"], list) else [st["Action"]]
            granted.update(acts)
        for action, _r in _DEPLOY_PROBES:
            assert action in granted, f"{action} is probed but never granted"

    def test_an_assumed_role_arn_is_rewritten_to_its_role(self):
        """SimulatePrincipalPolicy takes a role ARN, not a session ARN --
        an SSO or assumed-role caller is the common case and would
        otherwise fail with NoSuchEntity, which this check would then
        report as "could not tell" on every such identity."""
        iam = _SimulatingIam(allowed=self._all_probe_actions())
        self._call(iam, arn=f"arn:aws:sts::{self.ACCT}:assumed-role/AdminRole/session-name")
        assert iam.source_arns
        for a in iam.source_arns:
            assert a == f"arn:aws:iam::{self.ACCT}:role/AdminRole", a

    def test_a_plain_user_arn_is_passed_through(self):
        iam = _SimulatingIam(allowed=self._all_probe_actions())
        arn = f"arn:aws:iam::{self.ACCT}:user/deployer"
        self._call(iam, arn=arn)
        assert set(iam.source_arns) == {arn}


class _Ecr:
    """Modelled on ECR's contract, read from botocore's service model.

    What the contract forced, each because production depends on it:
    CreateRepository raises **RepositoryAlreadyExistsException** (the
    idempotency signal); the Repository shape carries **repositoryUri**, so
    the caller never assembles a registry host; DeleteRepository raises
    **RepositoryNotEmptyException** unless `force` is set, which is the
    case that matters since a repository this deploy created always holds
    an image; and `authorizationToken` is **base64 of `user:password`**.
    """

    class RepositoryAlreadyExistsException(Exception):
        pass

    class RepositoryNotFoundException(Exception):
        pass

    class RepositoryNotEmptyException(Exception):
        pass

    # A deliberately non-standard suffix: the point is that the caller must
    # use what AWS returns rather than building <acct>.dkr.ecr.<region>.
    HOST = "111122223333.dkr.ecr.us-gov-west-1.amazonaws.com"

    def __init__(self, existing=None, images=0):
        self.repos = dict(existing or {})
        self.images = images
        self.deleted = []

    def create_repository(self, repositoryName, **kw):
        if repositoryName in self.repos:
            raise self.RepositoryAlreadyExistsException("already exists")
        uri = f"{self.HOST}/{repositoryName}"
        self.repos[repositoryName] = uri
        return {"repository": {"repositoryName": repositoryName, "repositoryUri": uri}}

    def describe_repositories(self, repositoryNames=None, **kw):
        names = repositoryNames or list(self.repos)
        missing = [n for n in names if n not in self.repos]
        if missing:
            raise self.RepositoryNotFoundException(str(missing))
        return {"repositories": [{"repositoryName": n, "repositoryUri": self.repos[n]}
                                 for n in names]}

    def delete_repository(self, repositoryName, force=False, **kw):
        if repositoryName not in self.repos:
            raise self.RepositoryNotFoundException(repositoryName)
        if self.images and not force:
            raise self.RepositoryNotEmptyException("images still present")
        del self.repos[repositoryName]
        self.deleted.append(repositoryName)

    def get_authorization_token(self, **kw):
        import base64
        tok = base64.b64encode(b"AWS:sekrit").decode()
        return {"authorizationData": [
            {"authorizationToken": tok, "proxyEndpoint": f"https://{self.HOST}"}]}


class _Runner:
    """Records subprocess invocations; scripted return codes by argv[1]."""

    def __init__(self, fail_on=None, stderr=""):
        self.calls = []
        self.fail_on = fail_on
        self.stderr = stderr

    def __call__(self, argv, **kw):
        self.calls.append((list(argv), kw))

        class R:
            pass
        r = R()
        r.returncode = 1 if (self.fail_on and argv[1] == self.fail_on) else 0
        r.stdout = ""
        r.stderr = self.stderr if r.returncode else ""
        return r


class TestTheDeployBuildsItsOwnImage:
    """`--tier stack-mutation-node` used to require --image-uri and told the
    operator to run finch build, aws ecr create-repository and finch push by
    hand -- while MCPDeployPolicy already granted ecr:CreateRepository and
    nothing ever called it.
    """

    def test_the_repository_uri_comes_from_aws(self):
        """Never assembled as <acct>.dkr.ecr.<region>.amazonaws.com: that is
        right in the standard partition and wrong in GovCloud and China,
        where a subtly wrong registry host fails at push as an
        authentication error rather than a name error."""
        from mcp_server.deploy import IMAGE_REPOSITORY, ensure_ecr_repository

        ecr = _Ecr()
        uri, created = ensure_ecr_repository(ecr)
        assert created is True
        assert uri == f"{_Ecr.HOST}/{IMAGE_REPOSITORY}"

    def test_an_existing_repository_is_reused_not_an_error(self):
        from mcp_server.deploy import IMAGE_REPOSITORY, ensure_ecr_repository

        ecr = _Ecr(existing={IMAGE_REPOSITORY: f"{_Ecr.HOST}/{IMAGE_REPOSITORY}"})
        uri, created = ensure_ecr_repository(ecr)
        assert created is False
        assert uri == f"{_Ecr.HOST}/{IMAGE_REPOSITORY}"

    def test_teardown_forces_the_delete(self):
        """A repository this deploy created always holds the image it
        pushed, and DeleteRepository raises RepositoryNotEmptyException
        without force -- so an unforced delete fails on exactly the
        transports that deployed this tier."""
        from mcp_server.deploy import IMAGE_REPOSITORY, delete_ecr_repository

        ecr = _Ecr(existing={IMAGE_REPOSITORY: "u"}, images=1)
        assert delete_ecr_repository(ecr, suppress=False) is True
        assert ecr.deleted == [IMAGE_REPOSITORY]

    def test_the_fake_refuses_an_unforced_delete_of_a_nonempty_repo(self):
        """Vacuity guard: without this the force assertion above passes
        either way."""
        ecr = _Ecr(existing={"r": "u"}, images=1)
        with pytest.raises(_Ecr.RepositoryNotEmptyException):
            ecr.delete_repository(repositoryName="r")

    def test_deleting_an_absent_repository_is_not_a_failure(self):
        from mcp_server.deploy import delete_ecr_repository

        assert delete_ecr_repository(_Ecr(), suppress=False) is False

    def test_the_password_never_reaches_argv(self):
        """A credential in a command line is visible to every process on
        the machine through `ps`. ECR's token decodes to user:password and
        the password goes on stdin."""
        from mcp_server.deploy import ecr_login

        run = _Runner()
        registry = ecr_login(_Ecr(), "finch", run=run)
        assert registry == _Ecr.HOST, "the scheme must be stripped"
        argv, kw = run.calls[0]
        assert "sekrit" not in " ".join(argv)
        assert kw["input"] == "sekrit"
        assert "--password-stdin" in argv

    def test_the_build_always_pins_the_platform(self):
        """An image built on Apple Silicon defaults to linux/arm64 and
        Lambda rejects the mismatch at CreateFunction, long after the
        push."""
        from mcp_server.deploy import IMAGE_PLATFORM, build_and_push_image

        run = _Runner()
        build_and_push_image("finch", image_uri="u:latest", repo_root="/r",
                             dockerfile="/r/Dockerfile", run=run)
        argv = run.calls[0][0]
        assert argv[:2] == ["finch", "build"]
        assert argv[argv.index("--platform") + 1] == IMAGE_PLATFORM

    def test_it_pushes_what_it_built(self):
        from mcp_server.deploy import build_and_push_image

        run = _Runner()
        build_and_push_image("finch", image_uri="u:latest", repo_root="/r",
                             dockerfile="/r/Dockerfile", run=run)
        assert run.calls[1][0] == ["finch", "push", "u:latest"]

    def test_a_failed_build_does_not_push(self):
        from mcp_server.deploy import ImageBuildError, build_and_push_image

        run = _Runner(fail_on="build", stderr="compile error")
        with pytest.raises(ImageBuildError, match="build failed"):
            build_and_push_image("finch", image_uri="u:latest", repo_root="/r",
                                 dockerfile="/r/Dockerfile", run=run)
        assert [c[0][1] for c in run.calls] == ["build"], "pushed after a failed build"

    def test_the_finch_credential_failure_names_the_remedy(self):
        """`finch login` writes to the host and the push runs inside a Lima
        VM. Deliberately not automated -- the fix writes an ECR token into
        two paths inside the VM, both of which must be scrubbed, so a
        process dying between them leaves a credential at rest."""
        from mcp_server.deploy import ImageBuildError, build_and_push_image

        run = _Runner(fail_on="push", stderr="failed: no basic auth credentials")
        with pytest.raises(ImageBuildError, match="INSTALL.md"):
            build_and_push_image("finch", image_uri="u:latest", repo_root="/r",
                                 dockerfile="/r/Dockerfile", run=run)

    def test_an_unrelated_push_failure_is_not_dressed_up_as_that_one(self):
        from mcp_server.deploy import ImageBuildError, build_and_push_image

        run = _Runner(fail_on="push", stderr="network unreachable")
        with pytest.raises(ImageBuildError) as e:
            build_and_push_image("finch", image_uri="u:latest", repo_root="/r",
                                 dockerfile="/r/Dockerfile", run=run)
        assert "network unreachable" in str(e.value)
        assert "INSTALL.md" not in str(e.value)

    def test_runtime_detection_prefers_the_first_present(self):
        from mcp_server.deploy import detect_container_runtime

        assert detect_container_runtime(candidates=("definitely-not-installed",)) is None
        assert detect_container_runtime(candidates=("sh",)) == "sh"
        assert detect_container_runtime(candidates=("definitely-not-installed", "sh")) == "sh"


class TestTheEcrGrantMatchesWhatTheDeployDoes:
    """Creating the repository and never deleting it leaks one per account;
    the two halves have to land together. ecr:DeleteRepository was granted
    nowhere before this."""

    def _statements(self):
        import json
        import os

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        doc = json.load(open(os.path.join(root, "templates", "MCPDeployPolicy.json_src")))
        return doc["Statement"]

    def _granted(self):
        out = {}
        for st in self._statements():
            if st.get("Effect") != "Allow":
                continue
            acts = st["Action"] if isinstance(st["Action"], list) else [st["Action"]]
            res = st["Resource"] if isinstance(st["Resource"], list) else [st["Resource"]]
            for a in acts:
                out.setdefault(a, set()).update(res)
        return out

    def test_teardown_can_delete_the_repository_it_created(self):
        assert "ecr:DeleteRepository" in self._granted()

    def test_only_the_registry_wide_action_uses_a_wildcard_resource(self):
        """GetAuthorizationToken operates on the registry and IAM rejects it
        against a repository ARN, so it genuinely needs "*". Every other ECR
        action is confined to this deploy's own repositories -- a blanket
        wildcard would let the deployer delete any repository in the
        account."""
        granted = self._granted()
        for action, resources in granted.items():
            if not action.startswith("ecr:"):
                continue
            if action == "ecr:GetAuthorizationToken":
                assert resources == {"*"}
                continue
            assert resources != {"*"}, f"{action} is granted on every repository"
            assert all("repository/pclustermaker-mcp-" in r for r in resources), action
