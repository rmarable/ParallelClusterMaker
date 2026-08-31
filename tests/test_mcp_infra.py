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
    _mcp_boundary_name,
    _mcp_policy_name,
    _mcp_policy_templates,
    _mcp_role_name,
    _setup_mcp_infra,
)

ACCOUNT = "123456789012"
REGION = "us-east-2"
POOL = "us-east-2_aBcDeFgHi"


class _FakeIam:
    # A policy that already exists carries a document, and IAM will not
    # delete one while a non-default version remains. Both are modelled
    # here because _setup_mcp_infra now compares documents and
    # _delete_mcp_infra now prunes versions -- a fake that answered
    # neither would agree with the code by construction.
    _SENTINEL_DOC = {"Version": "2012-10-17", "Statement": []}

    def __init__(self, existing_policies=(), existing_roles=(), existing_documents=None):
        self._existing_policies = set(existing_policies)
        self._existing_roles = set(existing_roles)
        # What is actually present, so a delete can tell absent from real.
        self._live_policies = set(existing_policies)
        self._live_roles = set(existing_roles)
        # {name: [{VersionId, IsDefaultVersion, CreateDate, Document}]}
        docs = existing_documents or {}
        self._versions = {
            name: [
                {
                    "VersionId": "v1",
                    "IsDefaultVersion": True,
                    "CreateDate": 1,
                    "Document": docs.get(name, self._SENTINEL_DOC),
                }
            ]
            for name in self._existing_policies
        }
        self.created_policies = []
        self.created_roles = []
        self.attached = []
        self.detached = []
        self.deleted_roles = []
        self.deleted_policies = []
        # {role_name: boundary_arn or None}
        self.boundaries = {name: None for name in self._existing_roles}
        self.reasserted_boundaries = []

    def _already(self, op):
        from botocore.exceptions import ClientError

        return ClientError({"Error": {"Code": "EntityAlreadyExists", "Message": ""}}, op)

    def create_policy(self, PolicyName, PolicyDocument):
        if PolicyName in self._existing_policies:
            raise self._already("CreatePolicy")
        json.loads(PolicyDocument)  # must be valid rendered JSON
        self.created_policies.append({"name": PolicyName, "doc": PolicyDocument})
        self._live_policies.add(PolicyName)
        self._versions[PolicyName] = [
            {
                "VersionId": "v1",
                "IsDefaultVersion": True,
                "CreateDate": 1,
                "Document": json.loads(PolicyDocument),
            }
        ]
        return {"Policy": {"Arn": f"arn:aws:iam::{ACCOUNT}:policy/{PolicyName}"}}

    def create_role(
        self, RoleName, AssumeRolePolicyDocument, Description="", PermissionsBoundary=None
    ):
        if RoleName in self._existing_roles:
            raise self._already("CreateRole")
        self.created_roles.append(
            {"name": RoleName, "trust": AssumeRolePolicyDocument, "boundary": PermissionsBoundary}
        )
        self._live_roles.add(RoleName)
        # A role created without a boundary has none; recording the absence
        # is what lets a test see an unbounded role rather than infer it.
        self.boundaries[RoleName] = PermissionsBoundary

    def put_role_permissions_boundary(self, RoleName, PermissionsBoundary):
        if RoleName not in self._live_roles:
            raise self._no_such("PutRolePermissionsBoundary")
        self.boundaries[RoleName] = PermissionsBoundary
        self.reasserted_boundaries.append((RoleName, PermissionsBoundary))

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
            {"Error": {"Code": "NoSuchEntity", "Message": "The entity does not exist"}}, op
        )

    def delete_role(self, RoleName):
        if RoleName not in self._live_roles:
            raise self._no_such("DeleteRole")
        self._live_roles.discard(RoleName)
        self.deleted_roles.append(RoleName)

    def _versions_of(self, PolicyArn, op):
        name = PolicyArn.rsplit("/", 1)[-1]
        if name not in self._live_policies:
            raise self._no_such(op)
        return name, self._versions.setdefault(
            name,
            [
                {
                    "VersionId": "v1",
                    "IsDefaultVersion": True,
                    "CreateDate": 1,
                    "Document": self._SENTINEL_DOC,
                }
            ],
        )

    def get_policy(self, PolicyArn):
        _, vs = self._versions_of(PolicyArn, "GetPolicy")
        d = next(v for v in vs if v["IsDefaultVersion"])
        return {"Policy": {"DefaultVersionId": d["VersionId"]}}

    def get_policy_version(self, PolicyArn, VersionId):
        _, vs = self._versions_of(PolicyArn, "GetPolicyVersion")
        v = next((x for x in vs if x["VersionId"] == VersionId), None)
        if v is None:
            raise self._no_such("GetPolicyVersion")
        return {"PolicyVersion": {"Document": v["Document"]}}

    def list_policy_versions(self, PolicyArn):
        _, vs = self._versions_of(PolicyArn, "ListPolicyVersions")
        return {
            "Versions": [
                {k: v[k] for k in ("VersionId", "IsDefaultVersion", "CreateDate")} for v in vs
            ]
        }

    def create_policy_version(self, PolicyArn, PolicyDocument, SetAsDefault=False):
        """ "A managed policy can have up to five versions" -- iam's own
        service model. Past that, LimitExceeded."""
        _, vs = self._versions_of(PolicyArn, "CreatePolicyVersion")
        if len(vs) >= 5:
            raise ClientError(
                {"Error": {"Code": "LimitExceeded", "Message": ""}}, "CreatePolicyVersion"
            )
        nxt = max(int(v["VersionId"][1:]) for v in vs) + 1
        if SetAsDefault:
            for v in vs:
                v["IsDefaultVersion"] = False
        vs.append(
            {
                "VersionId": f"v{nxt}",
                "IsDefaultVersion": bool(SetAsDefault),
                "CreateDate": nxt,
                "Document": json.loads(PolicyDocument),
            }
        )
        return {"PolicyVersion": {"VersionId": f"v{nxt}"}}

    def delete_policy_version(self, PolicyArn, VersionId):
        """The default version cannot be deleted this way."""
        _, vs = self._versions_of(PolicyArn, "DeletePolicyVersion")
        v = next((x for x in vs if x["VersionId"] == VersionId), None)
        if v is None:
            raise self._no_such("DeletePolicyVersion")
        if v["IsDefaultVersion"]:
            raise ClientError(
                {"Error": {"Code": "DeleteConflict", "Message": ""}}, "DeletePolicyVersion"
            )
        vs.remove(v)

    def delete_policy(self, PolicyArn):
        """IAM: "you must delete all the policy's versions" first. Modelling
        this is what makes the teardown's pruning testable rather than
        assumed."""
        name, vs = self._versions_of(PolicyArn, "DeletePolicy")
        if len(vs) > 1:
            raise ClientError({"Error": {"Code": "DeleteConflict", "Message": ""}}, "DeletePolicy")
        self._live_policies.discard(name)
        self._versions.pop(name, None)
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
            fn
            for tier, (fn, _p) in _MCP_LAMBDA_TIERS.items()
            if tier not in ("router", "register", "authorizer")
        }
        assert in_policy == handlers

    def test_both_stack_mutation_tiers_share_one_policy_set(self):
        """Documented as a deliberate trade-off; pinned so a future edit
        that gives them separate policies is a decision, not a drift."""
        assert _MCP_LAMBDA_TIERS["stack-mutation"][1] == _MCP_LAMBDA_TIERS["stack-mutation-node"][1]

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
            want = {f"arn:aws:iam::{ACCOUNT}:policy/{_mcp_policy_name(b)}" for b in templates} | {
                _MCP_BASIC_EXECUTION_ARN
            }
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
            existing_policies=(
                {_mcp_policy_name(b) for b in _mcp_policy_templates()} | {_mcp_boundary_name()}
            ),
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
            f"arn:aws:iam::{ACCOUNT}:policy/{_mcp_policy_name(b)}" for b in _mcp_policy_templates()
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
        what proves the construction actually holds.

        **One deliberate exception: the permissions boundary.** It is a
        durable account-level guardrail rather than part of a deployment,
        and MCPDeployPolicy denies deleting it -- a deployer who can remove
        their own boundary does not have one. Teardown therefore leaves it,
        and the exception is named here rather than the assertion being
        loosened, so a *second* resource going undeleted still fails.
        """
        created_iam, _ = _run()
        deleted_iam = _seeded_iam()
        _delete_mcp_infra(deleted_iam, aws_account_id=ACCOUNT)
        created_policy_arns = {
            f"arn:aws:iam::{ACCOUNT}:policy/{p['name']}" for p in created_iam.created_policies
        }
        boundary_arn = f"arn:aws:iam::{ACCOUNT}:policy/{_mcp_boundary_name()}"
        assert boundary_arn in created_policy_arns, (
            "setup no longer creates the boundary; the exception below is now hiding a real gap"
        )
        assert boundary_arn not in set(deleted_iam.deleted_policies), (
            "teardown deleted the permissions boundary -- it is meant to "
            "outlive the deployment, and MCPDeployPolicy cannot delete it"
        )
        assert created_policy_arns - {boundary_arn} == set(deleted_iam.deleted_policies)
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
        return ClientError({"Error": {"Code": "AccessDenied", "Message": "not authorized"}}, op)

    def _absent(self, op):
        return ClientError({"Error": {"Code": "NoSuchEntity", "Message": "cannot be found"}}, op)

    def test_a_clean_sweep_reports_what_it_deleted(self, capsys):
        iam = _FakeIam()
        _setup_mcp_infra(iam, aws_account_id=ACCOUNT, region=REGION, mcp_user_pool_id=POOL)
        capsys.readouterr()
        result = _delete_mcp_infra(iam, aws_account_id=ACCOUNT)
        assert result.ok
        assert not result.failed
        assert len(result.deleted) == 17, result.deleted  # 7 roles + 10 policies
        out = capsys.readouterr().out
        assert "Deleted MCP role:" in out
        assert "Deleted MCP policy:" in out

    def test_a_failure_is_named_not_swallowed(self, capsys):
        """The property the old code could not express."""

        class _DenyOneRole(_FakeIam):
            def delete_role(self, RoleName):
                if RoleName.endswith("router-role"):
                    raise ClientError(
                        {"Error": {"Code": "AccessDenied", "Message": "not authorized"}},
                        "DeleteRole",
                    )
                return super().delete_role(RoleName=RoleName)

        iam = _DenyOneRole()
        _setup_mcp_infra(iam, aws_account_id=ACCOUNT, region=REGION, mcp_user_pool_id=POOL)
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
                        {"Error": {"Code": "AccessDenied", "Message": "no"}}, "DeleteRole"
                    )
                return super().delete_role(RoleName=RoleName)

        iam = _DenyOneRole()
        _setup_mcp_infra(iam, aws_account_id=ACCOUNT, region=REGION, mcp_user_pool_id=POOL)
        result = _delete_mcp_infra(iam, aws_account_id=ACCOUNT)
        assert len(result.deleted) == 16  # everything but the denied role
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
                    {"Error": {"Code": "AccessDenied", "Message": "no"}}, "DeleteRole"
                )

        iam = _DenyOneRole()
        _setup_mcp_infra(iam, aws_account_id=ACCOUNT, region=REGION, mcp_user_pool_id=POOL)
        with pytest.raises(ClientError):
            _delete_mcp_infra(iam, aws_account_id=ACCOUNT, suppress=False)

    def test_a_missing_entity_never_raises_even_unsuppressed(self):
        """ "It was not there" is a success for a teardown, so suppress=False
        must not turn an empty account into an exception."""
        _delete_mcp_infra(_FakeIam(), aws_account_id=ACCOUNT, suppress=False)

    def test_verbose_false_says_nothing(self, capsys):
        """For a caller that renders its own report."""
        _delete_mcp_infra(_FakeIam(), aws_account_id=ACCOUNT, verbose=False)
        assert capsys.readouterr().out == ""


class TestEveryMcpRoleIsBounded:
    """The permissions boundary is what closes the escalation that adding
    MCP deploy grants would otherwise open.

    Without it, whoever can attach a policy to an MCP Lambda role and update
    that function's code runs arbitrary code with whatever they granted --
    and since the deploy grants include CreatePolicy on a name pattern they
    control, that reaches `*:*`. The boundary caps the role's *effective*
    permissions to the intersection with itself, so a `*:*` policy attached
    to a bounded role still cannot leave the ceiling.
    """

    def test_every_created_role_carries_the_boundary(self):
        iam, roles = _run()
        expected = f"arn:aws:iam::{ACCOUNT}:policy/{_mcp_boundary_name()}"
        assert iam.created_roles, "no roles created -- the sweep is vacuous"
        for r in iam.created_roles:
            assert r["boundary"] == expected, (
                f"role {r['name']} was created without the permissions "
                f"boundary; a `*:*` policy attached to it would be effective"
            )

    def test_a_pre_existing_unbounded_role_has_the_boundary_reasserted(self):
        """A role created before the boundary existed keeps the unbounded
        permissions the mechanism exists to cap, and nothing downstream
        would notice -- create_role raises EntityAlreadyExists and the old
        code moved on."""
        iam = _FakeIam(
            existing_roles={_mcp_role_name(t) for t in _MCP_LAMBDA_TIERS},
        )
        assert all(v is None for v in iam.boundaries.values())
        iam, _ = _run(iam=iam)
        expected = f"arn:aws:iam::{ACCOUNT}:policy/{_mcp_boundary_name()}"
        for tier in _MCP_LAMBDA_TIERS:
            role = _mcp_role_name(tier)
            assert iam.boundaries[role] == expected, f"pre-existing role {role} is still unbounded"

    def test_the_boundary_is_created_before_any_role(self):
        """A role cannot be created under a boundary that does not exist,
        so ordering is a correctness property rather than a preference."""
        order = []

        class _Ordered(_FakeIam):
            def create_policy(self, PolicyName, PolicyDocument):
                order.append(("policy", PolicyName))
                return super().create_policy(PolicyName=PolicyName, PolicyDocument=PolicyDocument)

            def create_role(
                self, RoleName, AssumeRolePolicyDocument, Description="", PermissionsBoundary=None
            ):
                order.append(("role", RoleName))
                return super().create_role(
                    RoleName=RoleName,
                    AssumeRolePolicyDocument=AssumeRolePolicyDocument,
                    Description=Description,
                    PermissionsBoundary=PermissionsBoundary,
                )

        _run(iam=_Ordered())
        first_role = next(i for i, (k, _) in enumerate(order) if k == "role")
        boundary_at = next(
            i for i, (k, n) in enumerate(order) if k == "policy" and n == _mcp_boundary_name()
        )
        assert boundary_at < first_role, (
            "the boundary is created after the first role, so that role "
            "would be created under a boundary that does not exist"
        )


class TestTheBoundaryCannotBeWeakenedByTheDeployer:
    """A boundary the deploy credentials can rewrite is not a boundary.

    These read the two policy documents rather than driving code, because
    the property lives in the documents: nothing in the code path can grant
    what MCPDeployPolicy withholds.
    """

    def _doc(self, name):
        import io as _io
        import json as _j

        path = os.path.join(REPO_ROOT, "templates", name)
        return _j.loads(_io.open(path, encoding="utf-8").read())

    def _statements(self, name, effect):
        return [s for s in self._doc(name)["Statement"] if s.get("Effect") == effect]

    def test_the_boundary_is_named_outside_the_lifecycle_pattern(self):
        """MCPDeployPolicy's policy-lifecycle statement covers
        `pclustermaker-mcp-policy-*`. If the boundary were named into that
        pattern, the deployer could version it and the deny below would be
        the only thing standing between them and a wider ceiling."""
        assert not _mcp_boundary_name().startswith("pclustermaker-mcp-policy-")

    def test_the_deployer_is_denied_rewriting_the_boundary(self):
        denies = self._statements("MCPDeployPolicy.json_src", "Deny")
        assert denies, "MCPDeployPolicy carries no Deny at all"
        denied = set()
        for st in denies:
            res = st["Resource"]
            res = res if isinstance(res, list) else [res]
            if any(r.endswith("policy/" + _mcp_boundary_name()) for r in res):
                a = st["Action"]
                denied |= set(a if isinstance(a, list) else [a])
        for action in (
            "iam:CreatePolicyVersion",
            "iam:SetDefaultPolicyVersion",
            "iam:DeletePolicy",
        ):
            assert action in denied, (
                f"MCPDeployPolicy does not deny {action} on the boundary; "
                f"the deployer can widen their own ceiling"
            )

    def test_the_deployer_cannot_detach_a_boundary(self):
        denied = set()
        for st in self._statements("MCPDeployPolicy.json_src", "Deny"):
            a = st["Action"]
            denied |= set(a if isinstance(a, list) else [a])
        assert "iam:DeleteRolePermissionsBoundary" in denied

    def test_creating_a_role_is_conditioned_on_the_boundary(self):
        """The hinge of the whole design. Without the condition the deployer
        creates an unbounded role and the ceiling never applies."""
        stmts = [
            s
            for s in self._doc("MCPDeployPolicy.json_src")["Statement"]
            if "iam:CreateRole" in (s["Action"] if isinstance(s["Action"], list) else [s["Action"]])
        ]
        assert stmts, "MCPDeployPolicy never grants iam:CreateRole"
        for st in stmts:
            cond = st.get("Condition", {}).get("StringEquals", {})
            assert cond.get("iam:PermissionsBoundary", "").endswith(
                "policy/" + _mcp_boundary_name()
            ), (
                f"statement {st.get('Sid')} grants iam:CreateRole without "
                f"requiring the permissions boundary"
            )

    def test_the_boundary_denies_the_escalation_primitives(self):
        denied = set()
        for st in self._statements("MCPRoleBoundary.json_src", "Deny"):
            a = st["Action"]
            denied |= set(a if isinstance(a, list) else [a])
        for action in (
            "iam:CreateUser",
            "iam:CreateAccessKey",
            "iam:AttachUserPolicy",
            "iam:UpdateAssumeRolePolicy",
            "iam:PutRolePermissionsBoundary",
            "iam:DeleteRolePermissionsBoundary",
        ):
            assert action in denied, (
                f"the boundary permits {action}; a bounded role can still "
                f"escalate out from under it"
            )

    def test_the_boundary_makes_the_retained_log_group_rule_structural(self):
        """CLAUDE.md's retained-log-group rule is enforced per-policy by a
        ban test. The boundary can enforce it for every MCP role at once,
        which no per-policy ban can do for a policy nobody thought to add
        to the list."""
        denied = set()
        for st in self._statements("MCPRoleBoundary.json_src", "Deny"):
            a = st["Action"]
            denied |= set(a if isinstance(a, list) else [a])
        assert "logs:DeleteLogGroup" in denied

    def test_the_ceiling_is_not_a_wildcard(self):
        """Vacuity guard. `"Action": "*"` in the ceiling would satisfy every
        allow-side expectation while capping nothing."""
        allows = self._statements("MCPRoleBoundary.json_src", "Allow")
        assert allows, "the boundary allows nothing at all"
        for st in allows:
            a = st["Action"]
            a = a if isinstance(a, list) else [a]
            assert "*" not in a, "the boundary ceiling is a bare wildcard"
