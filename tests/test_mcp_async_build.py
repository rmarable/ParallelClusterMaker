"""The build runs where nothing is waiting on it.

`create_cluster` measured 43.6s against API Gateway's 29s integration
timeout -- already the REST maximum -- and roughly 39 of those seconds are
inside pcluster.lib's own CDK synthesis, so no decomposition of this
repo's work brings the call under the ceiling. The build therefore runs in
an asynchronous invocation and the tool returns a real answer instead of a
timeout.

Two things make that safe rather than merely faster, and both are pinned
here: a retried invocation must not launch a second cluster, and a failure
nobody is waiting on must still be discoverable.
"""

import ast
import io
import json
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestTheMarkerIsExplicit:
    """A malformed event must never be mistaken for a build request and
    start creating infrastructure. Keyed on a present marker, never on the
    absence of a `method` field."""

    def test_only_the_marker_identifies_a_build(self):
        from mcp_server.build import is_build_event

        assert is_build_event({"_pcm_build": True})
        assert not is_build_event({})
        assert not is_build_event({"method": "tools/call"})
        assert not is_build_event({"_pcm_build": "yes"})
        assert not is_build_event({"_pcm_build": False})
        assert not is_build_event(None)
        assert not is_build_event("_pcm_build")

    def test_a_build_event_is_not_a_completion_event(self):
        """Both arrive on the same function. Confusing them would run a
        teardown finisher against a cluster being built."""
        from mcp_server.build import is_build_event, make_build_event
        from mcp_server.completion import is_completion_event, make_completion_event

        b = make_build_event(params={"cluster_name": "osiris"}, region="us-east-1")
        c = make_completion_event(
            cluster_name="osiris", cluster_owner="rmarable", region="us-east-1")
        assert is_build_event(b) and not is_completion_event(b)
        assert is_completion_event(c) and not is_build_event(c)

    def test_the_payload_survives_a_lambda_invoke(self):
        """It crosses the boundary as JSON, so a dataclass would not."""
        from mcp_server.build import make_build_event

        ev = make_build_event(
            params={"cluster_name": "osiris", "enable_fsx": False},
            region="us-east-1")
        assert json.loads(json.dumps(ev)) == ev

    def test_region_rides_separately_from_params(self):
        """MakeClusterParams carries no region -- the CLI resolves it from
        the AZ-verification call and passes it to core_create_cluster on
        its own, and every shim must do the same. Reading params.region
        raised AttributeError on every MCP create once already."""
        from mcp_server.build import make_build_event

        ev = make_build_event(params={"cluster_name": "x"}, region="us-west-2")
        assert ev["region"] == "us-west-2"
        assert "region" not in ev["params"]


class TestARetriedInvocationCannotLaunchTwice:
    """AWS retries a failed asynchronous invocation twice by default. The
    teardown poller tolerates that -- finalizing twice is a no-op -- but a
    retried build would attempt a second launch of the same cluster."""

    def test_the_deploy_pins_zero_retries(self):
        src = io.open(
            os.path.join(REPO_ROOT, "mcp_server", "deploy.py"), encoding="utf-8"
        ).read()
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                  and n.name == "_pin_async_retries_to_zero")
        kw = [k for n in ast.walk(fn) if isinstance(n, ast.Call)
              for k in n.keywords if k.arg == "MaximumRetryAttempts"]
        assert kw, "the config call sets no MaximumRetryAttempts"
        assert all(isinstance(k.value, ast.Constant) and k.value.value == 0
                   for k in kw), "retries must be pinned to 0, not merely set"

    def test_every_function_creation_path_pins_it(self):
        """Two paths reach a live function -- create, and update when it
        already exists. A guard on one of them leaves every redeployed
        function retrying."""
        src = io.open(
            os.path.join(REPO_ROOT, "mcp_server", "deploy.py"), encoding="utf-8"
        ).read()
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                  and n.name == "deploy_tier")
        pins = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == "_pin_async_retries_to_zero"]
        returns = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Return)
                   and n.value is not None]
        assert len(pins) >= 2, (
            f"only {len(pins)} of the function-creation paths pin async "
            f"retries; both create and update reach a live function"
        )
        # Every path that hands back a live function ARN must have pinned
        # retries on the way out. Counting the calls alone would pass with
        # both of them on one branch.
        for r in returns:
            assert any(0 < r - p <= 3 for p in pins), (
                f"the path returning at line {r} reaches a live function "
                f"without pinning its async retries"
            )

    def test_the_deployer_is_actually_granted_the_action(self):
        """The floor, not the ceiling. A guard the deployer cannot apply is
        swallowed by its own best-effort handler and silently never takes
        effect -- which is how two tiers once shipped unable to reach their
        own blast radius."""
        src = io.open(
            os.path.join(REPO_ROOT, "templates", "MCPDeployPolicy.json_src"),
            encoding="utf-8",
        ).read()
        assert "lambda:PutFunctionEventInvokeConfig" in src

    def test_setting_it_can_never_fail_a_deploy(self):
        """The lock is the guard that actually holds; this is the outer of
        two. A deploy must not die because the outer one could not be set."""
        from mcp_server.deploy import _pin_async_retries_to_zero

        class _Boom:
            def put_function_event_invoke_config(self, **kw):
                raise RuntimeError("denied")

        _pin_async_retries_to_zero(_Boom(), "pclustermaker-mcp-read-only")


class TestAFailureNobodyWaitsOnIsStillFound:
    """An Event invocation has no caller, so silence is indistinguishable
    from success -- the same trap the teardown completion runner exists to
    avoid."""

    def _payload(self):
        from mcp_server.build import make_build_event

        return make_build_event(
            params={"cluster_name": "osiris", "cluster_owner": "rmarable"},
            region="us-east-1")

    def test_a_returned_failure_is_recorded(self, monkeypatch):
        from mcp_server import build_runner

        seen = []
        monkeypatch.setattr(build_runner, "_record_failure_if_unrecorded",
                            lambda p, m: seen.append(m) or True)

        class _R:
            success = False
            message = "AccessDenied on iam:CreatePolicy"

        out = build_runner.run_build(self._payload(), create=lambda p, r: _R())
        assert out["success"] is False
        assert seen == ["AccessDenied on iam:CreatePolicy"]

    def test_a_raised_exception_is_recorded_not_lost(self, monkeypatch):
        from mcp_server import build_runner

        seen = []
        monkeypatch.setattr(build_runner, "_record_failure_if_unrecorded",
                            lambda p, m: seen.append(m) or True)

        def _boom(p, r):
            raise RuntimeError("network discovery failed")

        out = build_runner.run_build(self._payload(), create=_boom)
        assert out["success"] is False
        assert seen and "network discovery failed" in seen[0]

    def test_a_sys_exit_is_recorded_too(self, monkeypatch):
        """The shared validation helpers still sys.exit(), and SystemExit is
        a BaseException. Synchronously that reached the caller; here it
        would vanish, which is the whole failure mode this guards."""
        from mcp_server import build_runner

        seen = []
        monkeypatch.setattr(build_runner, "_record_failure_if_unrecorded",
                            lambda p, m: seen.append(m) or True)

        def _exit(p, r):
            raise SystemExit("ERROR: that AZ does not exist")

        out = build_runner.run_build(self._payload(), create=_exit)
        assert out["success"] is False
        assert seen, "a sys.exit in the background left no record at all"

    def test_the_real_closure_runs_at_least_once(self, monkeypatch):
        """Every other test here injects `create`, so the default closure --
        the one production uses -- was never executed, and it called
        `resolve_writable_repo_root()` with no arguments against a signature
        that takes the root positionally. It raised TypeError on the first
        real remote build, after the whole async mechanism had worked.

        The repo's own rule: when a test stubs the object under test, at
        least one test must drive the real one. This stubs the *core* and
        lets the closure itself run, so a wrong call inside it fails here
        rather than in a background invocation nobody is watching.
        """
        import mcp_server.build_runner as br

        seen = {}

        class _R:
            success = True
            message = "cluster created"

        def _core(**kw):
            seen.update(kw)
            return _R()

        monkeypatch.setattr("pcluster_core.core_create_cluster", _core)
        monkeypatch.setattr("mcp_server.tools._repo_root", lambda: "/tmp/repo")
        monkeypatch.setattr("pcluster_core.MakeClusterParams", dict)

        out = br.run_build(self._payload())
        assert out["success"] is True
        assert seen["region"] == "us-east-1"
        assert seen["repo_root"] == "/tmp/repo"
        assert seen["wait"] is False, "a background build must never wait"

    @pytest.mark.parametrize("exc,expected", [
        (TypeError("f() missing 1 required positional argument"),
         "TypeError: f() missing 1 required positional argument"),
        (SystemExit("ERROR: that AZ does not exist"),
         "SystemExit: ERROR: that AZ does not exist"),
        (RuntimeError(""), "RuntimeError: no detail"),
    ])
    def test_the_recorded_message_names_the_type_exactly_once(
            self, monkeypatch, exc, expected):
        """`pcluster_exception_detail` returns "<Type>: <detail>" -- a whole
        message, not a fragment. Prefixing the type onto it again produced
        `TypeError: TypeError: ...` in the first failure this ever
        recorded. Cosmetic alone, corrosive in aggregate: the record exists
        to be believed by someone who was not there to watch the build, and
        a message that looks broken invites doubt about the rest of it.

        The third case is the one a naive fix breaks: an exception whose
        str() is empty must still say something, since that is the shape
        every pcluster.lib exception has.
        """
        from mcp_server import build_runner

        monkeypatch.setattr(build_runner, "_record_failure_if_unrecorded",
                            lambda p, m: True)

        def _boom(p, r):
            raise exc

        out = build_runner.run_build(self._payload(), create=_boom)
        assert out["message"] == expected
        assert out["message"].count(type(exc).__name__) == 1, (
            f"the type name appears more than once in {out['message']!r}"
        )

    def test_a_success_records_nothing(self, monkeypatch):
        from mcp_server import build_runner

        seen = []
        monkeypatch.setattr(build_runner, "_record_failure_if_unrecorded",
                            lambda p, m: seen.append(m) or True)

        class _R:
            success = True
            message = "cluster created"

        out = build_runner.run_build(self._payload(), create=lambda p, r: _R())
        assert out["success"] is True
        assert seen == []

    def test_it_does_not_overwrite_a_more_specific_record(self, monkeypatch):
        """core_create_cluster records post-lock failures with the stage
        that produced them. Overwriting that with a generic "validation"
        entry would discard the useful half."""
        import mcp_server.build_runner as br

        calls = {"get": 0, "put": 0}

        class _S3:
            pass

        monkeypatch.setattr("mcp_server.tools._record_store",
                            lambda region=None: (_S3(), "bucket"))
        monkeypatch.setattr(
            "pcluster_core.get_build_failure",
            lambda s3, **kw: calls.__setitem__("get", calls["get"] + 1) or
            {"stage": "IAM setup", "message": "AccessDenied"})
        monkeypatch.setattr(
            "pcluster_core._publish_build_failure",
            lambda s3, **kw: calls.__setitem__("put", calls["put"] + 1) or True)

        assert br._record_failure_if_unrecorded(self._payload(), "build failed") is False
        assert calls["put"] == 0, "it overwrote the stage-specific record"

    def test_it_writes_when_the_core_could_not(self, monkeypatch):
        """A failure *before* the lock has no record: the store is addressed
        from an account ID the build has not fetched yet."""
        import mcp_server.build_runner as br

        put = []

        class _S3:
            pass

        monkeypatch.setattr("mcp_server.tools._record_store",
                            lambda region=None: (_S3(), "bucket"))
        monkeypatch.setattr("pcluster_core.get_build_failure",
                            lambda s3, **kw: None)
        monkeypatch.setattr("pcluster_core._publish_build_failure",
                            lambda s3, **kw: put.append(kw) or True)

        assert br._record_failure_if_unrecorded(self._payload(), "bad AZ") is True
        assert put and put[0]["message"] == "bad AZ"
        assert put[0]["cluster_name"] == "osiris"

    def test_recording_never_raises(self, monkeypatch):
        """It runs after a build has already failed. A bookkeeping error
        must not become the thing the operator debugs."""
        import mcp_server.build_runner as br

        monkeypatch.setattr("mcp_server.tools._record_store",
                            lambda region=None: (_ for _ in ()).throw(
                                RuntimeError("no store")))
        assert br._record_failure_if_unrecorded(self._payload(), "x") is False


class TestWhetherTheBuildStartedIsAFactNotAGuess:
    """The same rule as the teardown's auto_finalize_started: what the
    caller is told about work being underway must be observed, never
    inferred."""

    def test_no_lambda_means_it_did_not_start(self, monkeypatch):
        """Locally there is no function to invoke and no gateway ceiling to
        duck under, so the build runs here and the operator watching a
        terminal gets what they asked for."""
        import mcp_server.tools as t

        monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
        assert t._start_async_build(object(), "us-east-1") is False

    def test_a_failed_invoke_reports_false(self, monkeypatch):
        import mcp_server.tools as t

        monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "pclustermaker-mcp-x")

        class _Boom:
            def invoke(self, **kw):
                raise RuntimeError("throttled")

        monkeypatch.setattr("boto3.client", lambda *a, **k: _Boom())
        assert t._start_async_build(
            type("P", (), {"__dataclass_fields__": {}})(), "us-east-1") is False

    def test_the_handler_routes_a_build_event(self):
        """A build request and a tools/call arrive on the same function."""
        src = io.open(
            os.path.join(REPO_ROOT, "mcp_server", "handlers", "base.py"),
            encoding="utf-8",
        ).read()
        assert "is_build_event" in src and "run_build" in src

    def test_the_cli_is_untouched(self):
        """make_pcluster.py has no gateway in front of it and an operator
        watching a terminal wants the progress output. It must keep calling
        core_create_cluster directly and synchronously."""
        src = io.open(
            os.path.join(REPO_ROOT, "make_pcluster.py"), encoding="utf-8"
        ).read()
        assert "_start_async_build" not in src
        assert "make_build_event" not in src
        assert "InvocationType" not in src


class TestTheTierCanInvokeItselfToBuild:
    """The floor again, and the third time this shape has bitten.

    `_start_async_build` returns False on any failure and the tool then
    builds synchronously -- correct as a fallback, and it makes a missing
    grant look like nothing at all: the build still happens, the caller
    still times out, and the only trace is one log line. That is exactly
    how this shipped: the grant named `function:pclustermaker-mcp-stack-
    mutation` while the tier that runs `create_cluster` is
    `-stack-mutation-node`, a different function, so every remote build
    fell back to synchronous and timed out as before.
    """

    @staticmethod
    def _resource():
        import json

        src = io.open(
            os.path.join(REPO_ROOT, "templates", "MCPStackMutation.json_src"),
            encoding="utf-8",
        ).read()
        doc = json.loads(src.replace("<AWS_ACCOUNT_ID>", "123456789012")
                            .replace("<AWS_REGION>", "us-east-1"))
        # By Sid, not by action: several statements grant
        # lambda:InvokeFunction and the first of them is PCluster's own
        # function namespace, which has nothing to do with self-invocation.
        st = next(x for x in doc["Statement"]
                  if x.get("Sid") == "InvokeItselfForAsyncWork")
        r = st["Resource"]
        return [r] if isinstance(r, str) else r

    @pytest.mark.parametrize("function_name", [
        "pclustermaker-mcp-stack-mutation",       # the teardown poller
        "pclustermaker-mcp-stack-mutation-node",  # the build, and create_cluster
    ])
    def test_each_self_invoking_tier_is_granted_its_own_arn(self, function_name):
        import fnmatch

        arn = f"arn:aws:lambda:us-east-1:123456789012:function:{function_name}"
        assert any(fnmatch.fnmatch(arn, r) for r in self._resource()), (
            f"{function_name} cannot invoke itself, so its async work "
            f"silently falls back to running inline and timing out"
        )

    @pytest.mark.parametrize("function_name", [
        "pclustermaker-mcp-read-only",
        "pclustermaker-mcp-router",
        "pclustermaker-mcp-authorizer",
    ])
    def test_it_reaches_no_further_than_that(self, function_name):
        """The vacuity guard. `function:*` would satisfy the test above and
        hand the build tier invoke on every function in the account."""
        import fnmatch

        arn = f"arn:aws:lambda:us-east-1:123456789012:function:{function_name}"
        assert not any(fnmatch.fnmatch(arn, r) for r in self._resource()), (
            f"the grant reaches {function_name}, which no async path invokes"
        )


class TestTheCliCanTearDownWhatItDidNotBuild:
    """A cluster built through the MCP server could not be torn down from
    any other machine.

    `core_delete_cluster` reads the serial and the vars file from the
    shared record store when neither is on disk -- written for exactly this
    case, and carrying a comment that says so. `kill_pcluster.py` then
    restated the same check on the same two files and `sys.exit(1)`d
    *before* the core was ever called, which made that fallback unreachable
    from the CLI. Two statements of one rule; this was the copy that was
    wrong.

    Found by hitting it: a cluster built in the browser, torn down from a
    laptop that had never seen it.
    """

    _CLI = os.path.join(REPO_ROOT, "kill_pcluster.py")

    def _tree(self):
        return ast.parse(io.open(self._CLI, encoding="utf-8").read())

    def test_the_cli_does_not_refuse_on_a_missing_serial_or_vars_file(self):
        """An `os.path.isfile` on either path, anywhere in this file, is the
        defect returning: both live only on the building machine."""
        src = io.open(self._CLI, encoding="utf-8").read()
        for name in ("cluster_serial_number_file", "vars_file_path"):
            assert name not in src, (
                f"kill_pcluster.py references {name} again; the core reads "
                f"both from the record store, and a second check here is "
                f"what made a remotely-built cluster untearable"
            )

    def test_it_still_hands_the_core_what_the_core_needs(self):
        """The vacuity guard. Deleting the checks must not have deleted the
        call, and the core resolves everything else itself."""
        fn = next(n for n in ast.walk(self._tree())
                  if isinstance(n, ast.FunctionDef) and n.name == "main")
        call = next((n for n in ast.walk(fn) if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Name)
                     and n.func.id == "core_delete_cluster"), None)
        assert call is not None, "the CLI no longer calls core_delete_cluster"
        passed = {k.arg for k in call.keywords}
        assert {"cluster_name", "cluster_owner", "region", "repo_root"} <= passed

    def test_the_core_still_carries_the_fallback(self):
        """The other half of the pair. Removing the CLI's check is only
        correct while the core actually reads the store."""
        src = io.open(
            os.path.join(REPO_ROOT, "src", "pcluster_core.py"), encoding="utf-8"
        ).read()
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "core_delete_cluster")
        called = {n.func.id for n in ast.walk(fn) if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Name)}
        assert "_cluster_record_from_store" in called, (
            "core_delete_cluster no longer reads the store, so the CLI's "
            "check was load-bearing after all"
        )


class TestTagGetResourcesReachesTheTiersThatNeedIt:
    """ParallelCluster 3.16.0 makes `tag:GetResources` a required CLI
    permission -- it resolves login node load balancer ARNs by tag, which
    is what the `elasticloadbalancing` grant already exists for. Added
    ahead of the version bump, since it costs nothing on 3.15.1 and is a
    floor gap the moment anyone upgrades.

    The boundary half is the part that fails silently: a permissions
    boundary is an intersection, so a service missing from the ceiling
    removes every grant in it while `CreatePolicy` and `create_role` both
    succeed. The grant would read perfectly and do nothing.
    """

    @staticmethod
    def _doc(name):
        import json

        src = io.open(os.path.join(REPO_ROOT, "templates", name),
                      encoding="utf-8").read()
        for k, v in (("<AWS_ACCOUNT_ID>", "123456789012"),
                     ("<AWS_REGION>", "us-east-1"),
                     ("<MCP_USER_POOL_ID>", "us-east-1_XXXXXXXXX")):
            src = src.replace(k, v)
        return json.loads(src)

    @staticmethod
    def _granted(doc, action):
        import fnmatch

        for st in doc["Statement"]:
            if st.get("Effect") != "Allow":
                continue
            acts = st["Action"]
            acts = [acts] if isinstance(acts, str) else acts
            if any(fnmatch.fnmatch(action, a) for a in acts):
                return True
        return False

    @pytest.mark.parametrize("policy", [
        "MCPStackMutation.json_src",     # stack-mutation and -node
        "MCPReadOnlyLambda.json_src",    # describe-cluster
        "MCPFleetToggleLambda.json_src",  # update-compute-fleet
        "OperatorPolicy.json_src",       # the CLI itself
    ])
    def test_every_policy_that_reads_a_cluster_can_resolve_its_tags(self, policy):
        assert self._granted(self._doc(policy), "tag:GetResources"), (
            f"{policy} cannot call tag:GetResources, which 3.16.0 requires "
            f"to resolve login node load balancers"
        )

    @pytest.mark.parametrize("action", [
        "elasticloadbalancing:DescribeLoadBalancers",
        "elasticloadbalancing:DescribeTags",
        "elasticloadbalancing:DescribeTargetGroups",
        "elasticloadbalancing:DescribeTargetHealth",
    ])
    def test_the_operator_can_read_a_login_node_load_balancer(self, action):
        """`pcluster/aws/elb.py` makes exactly these four calls, and
        `describe-cluster` makes them against any cluster with a login node
        pool. The MCP tiers were granted them when that floor gap was
        found; OperatorPolicy -- which the CLI runs under -- was not, so
        the CLI had the same gap the whole time.

        Granting only the first moves the failure to the next call, which
        is why all four are pinned rather than the statement's presence.
        """
        assert self._granted(self._doc("OperatorPolicy.json_src"), action), (
            f"the CLI cannot call {action}, so describe-cluster fails "
            f"against any --enable_loginnode cluster"
        )

    def test_the_operator_grant_stays_read_only(self):
        """Describes take no resource-level permission, so Resource must be
        `*` and read-only is the only bound left. A mutating
        elasticloadbalancing action here would let anyone holding operator
        credentials reconfigure a load balancer."""
        doc = self._doc("OperatorPolicy.json_src")
        st = next(x for x in doc["Statement"]
                  if x.get("Sid") == "LoginNodeLoadBalancerRead")
        for a in st["Action"]:
            assert a.split(":")[1].startswith(("Describe", "Get", "List")), (
                f"{a} is not a read"
            )

    def test_the_mcp_boundary_ceiling_permits_it(self):
        """Without this the grant above is nullified, and nothing fails at
        deploy time to say so."""
        assert self._granted(self._doc("MCPRoleBoundary.json_src"), "tag:GetResources")

    def test_the_cluster_boundary_permits_it_too(self):
        assert self._granted(self._doc("ClusterRoleBoundary.json_src"), "tag:GetResources")

    @pytest.mark.parametrize("policy", [
        "MCPRouterLambda.json_src",
        "MCPAuthorizerLambda.json_src",
        "MCPRegisterLambda.json_src",
    ])
    def test_it_reaches_no_tier_that_reads_no_cluster(self, policy):
        """The vacuity guard. The router executes no tool logic and the
        authorizer validates a token; neither describes a cluster, so
        neither should have grown this."""
        assert not self._granted(self._doc(policy), "tag:GetResources")

    @pytest.mark.parametrize("policy", [
        "MCPStackMutation.json_src",
        "MCPReadOnlyLambda.json_src",
        "MCPFleetToggleLambda.json_src",
        "OperatorPolicy.json_src",
        "MCPRoleBoundary.json_src",
    ])
    def test_each_still_fits_the_managed_policy_limit(self, policy):
        """MCPStackMutation is the one to watch: it was 6,054 of 6,144
        bytes before this grant."""
        import json

        b = len(json.dumps(self._doc(policy), separators=(",", ":")))
        assert b <= 6144, f"{policy} is {b} bytes, over IAM's 6,144 limit"
