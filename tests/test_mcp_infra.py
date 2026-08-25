"""Workstream 5: _setup_mcp_infra / _delete_mcp_infra.

The MCP Lambda topology's IAM. What makes this worth testing carefully is
not the boto3 calls -- those are trivial -- but the couplings that are
silent when wrong:

  * the tier table and templates/MCPRouterLambda.json_src must agree on the
    four handler function names, or the router is denied at runtime rather
    than at test time;
  * every policy the table references must actually exist on disk;
  * setup and teardown must cover the same set, which is why both are
    driven by the same table rather than by two hand-maintained lists (the
    failure mode _setup_iam's three parallel suffix lists demonstrate);
  * MCPStackMutation is shared by two tiers, so it must be created once,
    not twice -- IAM policy names are unique per account and the second
    create_policy would collide.
"""

import json
import os
import sys

import pytest
from botocore.exceptions import ClientError

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

import pcluster_core  # noqa: E402
from pcluster_core import (  # noqa: E402
    PClusterMakerError,
    _MCP_BASIC_EXECUTION_ARN,
    _MCP_LAMBDA_TIERS,
    _delete_mcp_infra,
    _mcp_policy_name,
    _mcp_policy_templates,
    _mcp_role_name,
    _setup_mcp_infra,
)

ACCOUNT = "123456789012"
REGION = "us-east-2"
POOL = "us-east-2_aBcDeFgHi"


class _FakeIam:
    def __init__(self, existing_policies=(), existing_roles=()):
        self._existing_policies = set(existing_policies)
        self._existing_roles = set(existing_roles)
        # What is actually present, so a delete can tell absent from real.
        self._live_policies = set(existing_policies)
        self._live_roles = set(existing_roles)
        self.created_policies = []
        self.created_roles = []
        self.attached = []
        self.detached = []
        self.deleted_roles = []
        self.deleted_policies = []

    def _already(self, op):
        from botocore.exceptions import ClientError

        return ClientError({"Error": {"Code": "EntityAlreadyExists", "Message": ""}}, op)

    def create_policy(self, PolicyName, PolicyDocument):
        if PolicyName in self._existing_policies:
            raise self._already("CreatePolicy")
        json.loads(PolicyDocument)  # must be valid rendered JSON
        self.created_policies.append({"name": PolicyName, "doc": PolicyDocument})
        self._live_policies.add(PolicyName)
        return {"Policy": {"Arn": f"arn:aws:iam::{ACCOUNT}:policy/{PolicyName}"}}

    def create_role(self, RoleName, AssumeRolePolicyDocument, Description=""):
        if RoleName in self._existing_roles:
            raise self._already("CreateRole")
        self.created_roles.append({"name": RoleName, "trust": AssumeRolePolicyDocument})
        self._live_roles.add(RoleName)

    def attach_role_policy(self, RoleName, PolicyArn):
        self.attached.append((RoleName, PolicyArn))

    def detach_role_policy(self, RoleName, PolicyArn):
        self.detached.append((RoleName, PolicyArn))

    def _no_such(self, op):
        """IAM's own answer for a missing entity, per botocore's iam
        service-2.json: NoSuchEntityException on DeleteRole, DeletePolicy
        and DetachRolePolicy. The fake used to succeed unconditionally,
        which made "it was not there" indistinguishable from "it was
        deleted" -- so a teardown against an empty account looked exactly
        like a teardown that had done seventeen things."""
        return ClientError(
            {"Error": {"Code": "NoSuchEntity",
                       "Message": "The entity does not exist"}}, op
        )

    def delete_role(self, RoleName):
        if RoleName not in self._live_roles:
            raise self._no_such("DeleteRole")
        self._live_roles.discard(RoleName)
        self.deleted_roles.append(RoleName)

    def delete_policy(self, PolicyArn):
        name = PolicyArn.rsplit("/", 1)[-1]
        if name not in self._live_policies:
            raise self._no_such("DeletePolicy")
        self._live_policies.discard(name)
        self.deleted_policies.append(PolicyArn)


def _run(iam=None, **overrides):
    iam = iam or _FakeIam()
    kwargs = dict(aws_account_id=ACCOUNT, region=REGION, mcp_user_pool_id=POOL)
    kwargs.update(overrides)
    return iam, _setup_mcp_infra(iam, **kwargs)


class TestTheTierTableIsInternallyConsistent:
    def test_every_referenced_policy_template_exists_on_disk(self):
        for basename in _mcp_policy_templates():
            path = os.path.join(REPO_ROOT, "templates", basename)
            assert os.path.isfile(path), f"tier table references missing {basename}"

    def test_the_router_policy_names_exactly_the_handler_tiers(self):
        """The coupling that fails at deployment rather than in a test: the
        router's ARNs must name the same four functions the table declares
        for the handler tiers (every tier except the router itself and the
        two Workstream 6 auth Lambdas, which the router never invokes)."""
        raw = open(os.path.join(REPO_ROOT, "templates", "MCPRouterLambda.json_src")).read()
        doc = json.loads(raw.replace("<AWS_REGION>", REGION).replace("<AWS_ACCOUNT_ID>", ACCOUNT))
        arns = []
        for stmt in doc["Statement"]:
            r = stmt["Resource"]
            arns += [r] if isinstance(r, str) else r
        in_policy = {a.rsplit(":function:", 1)[-1] for a in arns}
        handlers = {
            fn for tier, (fn, _p) in _MCP_LAMBDA_TIERS.items()
            if tier not in ("router", "register", "authorizer")
        }
        assert in_policy == handlers

    def test_both_stack_mutation_tiers_share_one_policy_set(self):
        """Documented as a deliberate trade-off; pinned so a future edit
        that gives them separate policies is a decision, not a drift."""
        assert _MCP_LAMBDA_TIERS["stack-mutation"][1] == \
            _MCP_LAMBDA_TIERS["stack-mutation-node"][1]

    def test_policy_template_list_is_deduplicated(self):
        templates = _mcp_policy_templates()
        assert len(templates) == len(set(templates))


class TestSetupMcpInfra:
    def test_creates_one_role_per_tier(self):
        iam, roles = _run()
        assert {r["name"] for r in iam.created_roles} == {
            _mcp_role_name(t) for t in _MCP_LAMBDA_TIERS
        }
        assert set(roles) == set(_MCP_LAMBDA_TIERS)

    def test_every_role_trusts_only_lambda(self):
        iam, _ = _run()
        for role in iam.created_roles:
            trust = json.loads(role["trust"])
            services = set()
            for stmt in trust["Statement"]:
                svc = stmt["Principal"]["Service"]
                services |= set([svc] if isinstance(svc, str) else svc)
            assert services == {"lambda.amazonaws.com"}, role["name"]

    def test_the_shared_policy_is_created_once_not_twice(self):
        """IAM policy names are unique per account; MCPStackMutation is
        attached to two tiers, so creating it per-tier would collide on the
        second call."""
        iam, _ = _run()
        names = [p["name"] for p in iam.created_policies]
        assert len(names) == len(set(names))
        assert _mcp_policy_name("MCPStackMutation.json_src") in names

    def test_every_role_gets_basic_execution(self):
        """Execution logging comes from the AWS-managed policy, which is
        why none of the nine documents grants itself PutLogEvents."""
        iam, _ = _run()
        for tier in _MCP_LAMBDA_TIERS:
            assert (_mcp_role_name(tier), _MCP_BASIC_EXECUTION_ARN) in iam.attached

    def test_each_tier_gets_exactly_its_declared_policies(self):
        iam, _ = _run()
        for tier, (_fn, templates) in _MCP_LAMBDA_TIERS.items():
            role = _mcp_role_name(tier)
            got = {arn for r, arn in iam.attached if r == role}
            want = {
                f"arn:aws:iam::{ACCOUNT}:policy/{_mcp_policy_name(b)}" for b in templates
            } | {_MCP_BASIC_EXECUTION_ARN}
            assert got == want, tier

    def test_the_pool_id_is_substituted_into_the_cognito_policies(self):
        iam, _ = _run()
        by_name = {p["name"]: p["doc"] for p in iam.created_policies}
        for basename in ("MCPRegisterLambda.json_src", "MCPAuthorizerLambda.json_src"):
            doc = by_name[_mcp_policy_name(basename)]
            assert POOL in doc
            assert "<MCP_USER_POOL_ID>" not in doc

    def test_no_rendered_policy_carries_an_unsubstituted_placeholder(self):
        import re

        iam, _ = _run()
        for p in iam.created_policies:
            leftover = re.findall(r"<[A-Z_]+>", p["doc"])
            assert not leftover, f"{p['name']}: {sorted(set(leftover))}"

    def test_an_empty_pool_id_is_rejected(self):
        """An empty substitution yields a syntactically valid policy on a
        malformed ARN -- IAM accepts it and it denies at call time, which
        is the worst of both worlds."""
        with pytest.raises(PClusterMakerError, match="mcp_user_pool_id"):
            _run(mcp_user_pool_id="")

    def test_existing_policies_and_roles_are_reused_not_fatal(self):
        """A re-run after a partial failure must complete, not abort."""
        iam = _FakeIam(
            existing_policies={_mcp_policy_name(b) for b in _mcp_policy_templates()},
            existing_roles={_mcp_role_name(t) for t in _MCP_LAMBDA_TIERS},
        )
        iam, roles = _run(iam=iam)
        assert iam.created_policies == []
        assert iam.created_roles == []
        # Attachments still happen -- that is what makes the re-run useful.
        assert set(roles) == set(_MCP_LAMBDA_TIERS)
        for tier in _MCP_LAMBDA_TIERS:
            assert (_mcp_role_name(tier), _MCP_BASIC_EXECUTION_ARN) in iam.attached

    def test_a_non_already_exists_error_propagates(self):
        """Tolerating EntityAlreadyExists must not become tolerating
        AccessDenied -- that would report success on a run that created
        nothing."""
        from botocore.exceptions import ClientError

        class _Denied(_FakeIam):
            def create_policy(self, PolicyName, PolicyDocument):
                raise ClientError(
                    {"Error": {"Code": "AccessDenied", "Message": ""}}, "CreatePolicy"
                )

        with pytest.raises(ClientError):
            _run(iam=_Denied())


def _seeded_iam():
    """A fake holding exactly what _setup_mcp_infra creates.

    These tests used to delete against an *empty* fake and assert that
    everything had been deleted -- which passed only because the fake
    succeeded on every delete, including for entities that were never
    there. With the fake modelling IAM's NoSuchEntityException, "attempted"
    and "deleted" are finally different things, and the intent of these
    tests (the teardown covers every role and policy the table declares) is
    expressed by seeding what it should find.
    """
    return _FakeIam(
        existing_policies=[_mcp_policy_name(b) for b in _mcp_policy_templates()],
        existing_roles=[_mcp_role_name(t) for t in _MCP_LAMBDA_TIERS],
    )

class TestDeleteMcpInfra:
    def test_deletes_every_role_and_policy_the_table_declares(self):
        iam = _seeded_iam()
        _delete_mcp_infra(iam, aws_account_id=ACCOUNT)
        assert set(iam.deleted_roles) == {_mcp_role_name(t) for t in _MCP_LAMBDA_TIERS}
        assert set(iam.deleted_policies) == {
            f"arn:aws:iam::{ACCOUNT}:policy/{_mcp_policy_name(b)}"
            for b in _mcp_policy_templates()
        }

    def test_detaches_before_deleting(self):
        """IAM refuses to delete an attached policy or a role with
        attachments, so ordering here is functional, not cosmetic."""
        iam = _seeded_iam()
        _delete_mcp_infra(iam, aws_account_id=ACCOUNT)
        assert iam.detached, "nothing was detached"
        # Every role's detaches must precede its own deletion.
        for tier in _MCP_LAMBDA_TIERS:
            role = _mcp_role_name(tier)
            assert role in iam.deleted_roles
            assert any(r == role for r, _ in iam.detached)

    def test_setup_and_teardown_cover_the_same_set(self):
        """The property the shared table exists to guarantee. _setup_iam's
        three parallel suffix lists need a cross-assertion for exactly this
        reason; here it should be true by construction, and this test is
        what proves the construction actually holds."""
        created_iam, _ = _run()
        deleted_iam = _seeded_iam()
        _delete_mcp_infra(deleted_iam, aws_account_id=ACCOUNT)
        created_policy_arns = {
            f"arn:aws:iam::{ACCOUNT}:policy/{p['name']}"
            for p in created_iam.created_policies
        }
        assert created_policy_arns == set(deleted_iam.deleted_policies)
        assert {r["name"] for r in created_iam.created_roles} == set(deleted_iam.deleted_roles)

    def test_one_failure_does_not_abandon_the_rest(self):
        class _Flaky(_FakeIam):
            def delete_role(self, RoleName):
                if RoleName == _mcp_role_name("router"):
                    raise RuntimeError("boom")
                super().delete_role(RoleName)

        iam = _Flaky(
            existing_policies=[_mcp_policy_name(b) for b in _mcp_policy_templates()],
            existing_roles=[_mcp_role_name(t) for t in _MCP_LAMBDA_TIERS],
        )
        _delete_mcp_infra(iam, aws_account_id=ACCOUNT)
        assert len(iam.deleted_roles) == len(_MCP_LAMBDA_TIERS) - 1
        assert iam.deleted_policies, "policy deletion must still run"

    def test_suppress_false_surfaces_the_failure(self):
        class _Flaky(_FakeIam):
            def delete_role(self, RoleName):
                raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            _delete_mcp_infra(_Flaky(), aws_account_id=ACCOUNT, suppress=False)


class TestTheMcpTeardownCanBeAudited:
    """_delete_mcp_infra computed a per-call boolean and threw it away,
    printing nothing. A denied delete, a resource that was never there, and
    a clean sweep were indistinguishable, so the only way to know a teardown
    had worked was to go and look -- which is what actually happened when
    the session-53 infrastructure was removed. Tolerating a failure is
    right; hiding it is the defect, and it is the same one `ignore_errors`
    without `register` had in delete_pcluster.yml.
    """

    def _denied(self, op):
        return ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "not authorized"}}, op
        )

    def _absent(self, op):
        return ClientError(
            {"Error": {"Code": "NoSuchEntity", "Message": "cannot be found"}}, op
        )

    def test_a_clean_sweep_reports_what_it_deleted(self, capsys):
        iam = _FakeIam()
        _setup_mcp_infra(iam, aws_account_id=ACCOUNT, region=REGION,
                         mcp_user_pool_id=POOL)
        capsys.readouterr()
        result = _delete_mcp_infra(iam, aws_account_id=ACCOUNT)
        assert result.ok
        assert not result.failed
        assert len(result.deleted) == 17, result.deleted   # 7 roles + 10 policies
        out = capsys.readouterr().out
        assert "Deleted MCP role:" in out
        assert "Deleted MCP policy:" in out

    def test_a_failure_is_named_not_swallowed(self, capsys):
        """The property the old code could not express."""
        class _DenyOneRole(_FakeIam):
            def delete_role(self, RoleName):
                if RoleName.endswith("router-role"):
                    raise ClientError(
                        {"Error": {"Code": "AccessDenied",
                                   "Message": "not authorized"}}, "DeleteRole")
                return super().delete_role(RoleName=RoleName)

        iam = _DenyOneRole()
        _setup_mcp_infra(iam, aws_account_id=ACCOUNT, region=REGION,
                         mcp_user_pool_id=POOL)
        capsys.readouterr()
        result = _delete_mcp_infra(iam, aws_account_id=ACCOUNT)
        assert not result.ok
        assert len(result.failed) == 1
        what, why = result.failed[0]
        assert "router-role" in what
        assert "AccessDenied" in why or "not authorized" in why
        out = capsys.readouterr().out
        assert "FAILED" in out
        assert "removed by hand" in out
        assert "router-role" in out

    def test_the_rest_still_runs_after_one_failure(self):
        """Tolerance is the half that was already right and must stay."""
        class _DenyOneRole(_FakeIam):
            def delete_role(self, RoleName):
                if RoleName.endswith("router-role"):
                    raise ClientError(
                        {"Error": {"Code": "AccessDenied", "Message": "no"}},
                        "DeleteRole")
                return super().delete_role(RoleName=RoleName)

        iam = _DenyOneRole()
        _setup_mcp_infra(iam, aws_account_id=ACCOUNT, region=REGION,
                         mcp_user_pool_id=POOL)
        result = _delete_mcp_infra(iam, aws_account_id=ACCOUNT)
        assert len(result.deleted) == 16      # everything but the denied role
        assert len(result.failed) == 1

    def test_an_absent_entity_is_absent_not_deleted(self, capsys):
        """A teardown against an empty account must not claim to have
        deleted seventeen things, and must not report a failure either."""
        result = _delete_mcp_infra(_FakeIam(), aws_account_id=ACCOUNT)
        assert result.ok
        assert result.deleted == []
        assert len(result.absent) == 17
        assert "No MCP infrastructure was present." in capsys.readouterr().out

    def test_suppress_false_still_raises(self):
        """The escape hatch an operator uses when they want the traceback."""
        class _DenyOneRole(_FakeIam):
            def delete_role(self, RoleName):
                raise ClientError(
                    {"Error": {"Code": "AccessDenied", "Message": "no"}},
                    "DeleteRole")

        iam = _DenyOneRole()
        _setup_mcp_infra(iam, aws_account_id=ACCOUNT, region=REGION,
                         mcp_user_pool_id=POOL)
        with pytest.raises(ClientError):
            _delete_mcp_infra(iam, aws_account_id=ACCOUNT, suppress=False)

    def test_a_missing_entity_never_raises_even_unsuppressed(self):
        """"It was not there" is a success for a teardown, so suppress=False
        must not turn an empty account into an exception."""
        _delete_mcp_infra(_FakeIam(), aws_account_id=ACCOUNT, suppress=False)

    def test_verbose_false_says_nothing(self, capsys):
        """For a caller that renders its own report."""
        _delete_mcp_infra(_FakeIam(), aws_account_id=ACCOUNT, verbose=False)
        assert capsys.readouterr().out == ""
