"""Two IAM hardening mechanisms for the CLI cluster path.

ClusterNode-Deny is a managed policy holding nothing but Deny statements. Every
other document a cluster node carries is allow-only, so the bans this repo
relies on -- logs:DeleteLogGroup, iam:PutRolePolicy, the privilege-escalation
primitives -- are enforced by tests/test_templates.py at CI time, and only for
files somebody remembered to list. LustreS3HydrationPolicy escaped that list
once. An explicit Deny beats any Allow in any attached policy, including one
nobody added to a list, so it turns those bans into a runtime property of the
account rather than a property of the test suite.

ClusterRoleBoundary is the permissions boundary the head node's own role is
created under. It caps the head node ONLY, and that asymmetry with the MCP side
is structural rather than an oversight: templates/config.pcluster.j2 gives the
head node `InstanceRole:` -- a role _setup_iam creates -- but gives every
SlurmQueue and the LoginNodes pool `AdditionalIamPolicies:`, so PCluster's own
CDK creates those roles. There is no create_role call to pass
PermissionsBoundary= on, no role name known ahead of time to reassert it
against, and conditioning iam:CreateRole on iam:PermissionsBoundary in
OperatorPolicy (what MCPDeployPolicy does for the roles it owns) would refuse
the CDK's unbounded CreateRole and break every cluster build. Compute and login
nodes get ClusterNode-Deny instead, which they do carry.

Neither mechanism is meant to change what a cluster can do today. Both are
derived from what the shipped documents actually grant -- the deny list from
the escalation primitives none of them grants, the ceiling from the union of
services all of them do -- so a ceiling too narrow or a Deny of something in
use would fail at head node bootstrap twenty minutes into a build, where no
test can see it. The derivations are re-run as assertions here for that reason.
"""

import fnmatch
import json
import os
import sys

import pytest
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from pcluster_core import (  # noqa: E402
    _MANAGED_POLICY_SUFFIXES,
    _cluster_boundary_arn,
    _cluster_boundary_name,
    _delete_managed_policies,
    _setup_iam,
)

ACCOUNT = "123456789012"
TEMPLATES = os.path.join(REPO_ROOT, "templates")

DENY_POLICY = "ClusterNode-Deny.json_src"
BOUNDARY_POLICY = "ClusterRoleBoundary.json_src"

# The five managed documents plus the inline Lustre one. _setup_fsx_hydration_iam
# attaches the latter to the same role with put_role_policy, so a job on any node
# carries it; leaving it out is how a grant escapes an audit of "what an instance
# can do".
_INSTANCE_REACHABLE = [
    "HeadNode-Compute.json_src",
    "HeadNode-Storage.json_src",
    "HeadNode-IAM.json_src",
    "ComputeNode-Base.json_src",
    "HeadNode-Monitoring.json_src",
    "LustreS3HydrationPolicy.json_src",
]

_SUB = {
    "<AWS_ACCOUNT_ID>": ACCOUNT,
    "<AWS_REGION>": "us-east-1",
    "<VPC_ID>": "vpc-0abc123",
    "<PROD_LEVEL>": "dev",
    "<CLUSTER_NAME>": "test-cluster",
    "<CLUSTER_OWNER>": "testuser",
    "<CLUSTER_SERIAL_NUMBER>": "test-cluster-00001220260720",
    "<CLUSTER_SERIAL_DATESTAMP>": "00001220260720",
    "<FSX_S3_EXPORT_BUCKET>": "fsx-export-bucket",
    "<FSX_S3_IMPORT_BUCKET>": "fsx-import-bucket",
}


def _policy(fname):
    with open(os.path.join(TEMPLATES, fname)) as fh:
        raw = fh.read()
    for k, v in _SUB.items():
        raw = raw.replace(k, v)
    return json.loads(raw)


def _actions(stmt):
    a = stmt["Action"]
    return a if isinstance(a, list) else [a]


def _resources(stmt):
    r = stmt["Resource"]
    return r if isinstance(r, list) else [r]


def _allowed_action_patterns(fnames):
    """Every action pattern any of these documents grants, with its origin."""
    granted = {}
    for fname in fnames:
        for stmt in _policy(fname)["Statement"]:
            if stmt["Effect"] != "Allow":
                continue
            for action in _actions(stmt):
                granted.setdefault(action, []).append((fname, stmt.get("Sid")))
    return granted


def _denied_actions(fname):
    return {a for stmt in _policy(fname)["Statement"] if stmt["Effect"] == "Deny"
            for a in _actions(stmt)}


# ---------------------------------------------------------------------------
# A fake IAM client written from the service contract, not from what _setup_iam
# happens to call. botocore/data/iam/2010-05-08/service-2.json.gz is the source
# for every behavior below and is local and authoritative:
#
#   - EntityAlreadyExistsException carries code "EntityAlreadyExists" (409) and
#     NoSuchEntityException carries "NoSuchEntity" (404); CreatePolicy and
#     CreateRole raise the former, GetRole and PutRolePermissionsBoundary the
#     latter. _ensure_cluster_boundary swallows exactly one of those codes and
#     re-raises anything else, so a fake that never raised would agree with it
#     no matter which code it swallowed.
#   - CreateRoleRequest's PermissionsBoundary is OPTIONAL, so a role created
#     without one is a legal call this fake must accept rather than reject --
#     recording the absence is what lets a test see an unbounded role instead of
#     inferring it from a missing keyword.
#   - PutRolePermissionsBoundary has no output shape at all, so nothing may be
#     read from its return value.
#   - Role's PermissionsBoundary member is optional and IAM omits it entirely
#     for an unbounded role; GetRole's required members are Path, RoleName,
#     RoleId, Arn and CreateDate.
#   - ListAttachedRolePoliciesResponse carries AttachedPolicies (PolicyName +
#     PolicyArn) and IsTruncated.
# ---------------------------------------------------------------------------


def _client_error(code, operation):
    from botocore.exceptions import ClientError

    return ClientError({"Error": {"Code": code, "Message": ""}}, operation)


class _ContractIam:
    def __init__(self, existing_policies=(), existing_role=None,
                 existing_role_boundary=None):
        self.policies = {name: {"Version": "2012-10-17", "Statement": []}
                         for name in existing_policies}
        self.roles = {}
        if existing_role:
            self.roles[existing_role] = {"boundary": existing_role_boundary}
        self.attachments = {r: set() for r in self.roles}
        # An ordered trace of the calls that matter, so ordering is observable.
        self.calls = []
        self.created_policies = []
        self.created_roles = []
        self.boundary_puts = []
        self.deleted_policies = []
        self.detached = []

    def create_policy(self, PolicyName, PolicyDocument):
        self.calls.append(("create_policy", PolicyName))
        if PolicyName in self.policies:
            raise _client_error("EntityAlreadyExists", "CreatePolicy")
        self.policies[PolicyName] = json.loads(PolicyDocument)
        self.created_policies.append(PolicyName)
        return {"Policy": {"PolicyName": PolicyName,
                           "Arn": f"arn:aws:iam::{ACCOUNT}:policy/{PolicyName}",
                           "DefaultVersionId": "v1"}}

    def get_role(self, RoleName):
        self.calls.append(("get_role", RoleName))
        if RoleName not in self.roles:
            raise _client_error("NoSuchEntity", "GetRole")
        role = {
            "Path": "/",
            "RoleName": RoleName,
            "RoleId": "AROAEXAMPLE",
            "Arn": f"arn:aws:iam::{ACCOUNT}:role/{RoleName}",
            "CreateDate": 1,
        }
        boundary = self.roles[RoleName]["boundary"]
        if boundary is not None:
            role["PermissionsBoundary"] = {
                "PermissionsBoundaryType": "Policy",
                "PermissionsBoundaryArn": boundary,
            }
        return {"Role": role}

    def create_role(self, RoleName, AssumeRolePolicyDocument, Description="",
                    PermissionsBoundary=None):
        self.calls.append(("create_role", RoleName))
        if RoleName in self.roles:
            raise _client_error("EntityAlreadyExists", "CreateRole")
        json.loads(AssumeRolePolicyDocument)
        self.roles[RoleName] = {"boundary": PermissionsBoundary}
        self.attachments[RoleName] = set()
        self.created_roles.append({"name": RoleName,
                                   "boundary": PermissionsBoundary})
        return {"Role": {"Path": "/", "RoleName": RoleName,
                         "RoleId": "AROAEXAMPLE",
                         "Arn": f"arn:aws:iam::{ACCOUNT}:role/{RoleName}",
                         "CreateDate": 1}}

    def put_role_permissions_boundary(self, RoleName, PermissionsBoundary):
        self.calls.append(("put_role_permissions_boundary", RoleName))
        if RoleName not in self.roles:
            raise _client_error("NoSuchEntity", "PutRolePermissionsBoundary")
        self.roles[RoleName]["boundary"] = PermissionsBoundary
        self.boundary_puts.append((RoleName, PermissionsBoundary))
        return {}

    def list_attached_role_policies(self, RoleName):
        if RoleName not in self.roles:
            raise _client_error("NoSuchEntity", "ListAttachedRolePolicies")
        return {
            "AttachedPolicies": [
                {"PolicyName": n, "PolicyArn": f"arn:aws:iam::{ACCOUNT}:policy/{n}"}
                for n in sorted(self.attachments[RoleName])
            ],
            "IsTruncated": False,
        }

    def attach_role_policy(self, RoleName, PolicyArn):
        self.calls.append(("attach_role_policy", PolicyArn.split("/")[-1]))
        name = PolicyArn.split("/")[-1]
        if RoleName not in self.roles or name not in self.policies:
            raise _client_error("NoSuchEntity", "AttachRolePolicy")
        self.attachments[RoleName].add(name)

    def detach_role_policy(self, RoleName, PolicyArn):
        name = PolicyArn.split("/")[-1]
        if RoleName not in self.roles or name not in self.attachments.get(RoleName, ()):
            raise _client_error("NoSuchEntity", "DetachRolePolicy")
        self.attachments[RoleName].discard(name)
        self.detached.append(name)

    def delete_policy(self, PolicyArn):
        name = PolicyArn.split("/")[-1]
        if name not in self.policies:
            raise _client_error("NoSuchEntity", "DeletePolicy")
        del self.policies[name]
        self.deleted_policies.append(name)

    def delete_role_policy(self, RoleName, PolicyName):
        if RoleName not in self.roles:
            raise _client_error("NoSuchEntity", "DeleteRolePolicy")


_SETUP_KWARGS = dict(
    ec2_json_policy_template=None,  # replaced per test with a tmp_path file
    aws_account_id=ACCOUNT,
    prod_level="dev",
    cluster_serial_number="test-cluster-00001220260720",
    cluster_name="test-cluster",
    cluster_owner="testuser",
    cluster_serial_datestamp="00001220260720",
    region="us-east-1",
    vpc_id="vpc-0abc123",
)


def _setup(iam, tmp_path, **overrides):
    kwargs = dict(_SETUP_KWARGS)
    kwargs["ec2_json_policy_template"] = str(tmp_path / "policy.json")
    kwargs.update(overrides)
    return _setup_iam(iam, "pclustermaker-role-s1", "pclustermaker-policy-s1", **kwargs)


class TestTheDenyPolicyDeniesOnlyThingsNothingGrants:
    """The deny list is derived, not chosen.

    Denying an action some document legitimately grants would not fail at
    CreatePolicy, at attach, or in any test that reads a policy file: it fails
    on a live node the first time the action is called, which for the head node
    is inside CloudFormation's bootstrap window and for a compute node is a
    bootstrap failure counted toward the partition's protected-mode threshold.
    So the property is not "these actions look dangerous" but "no document an
    instance carries grants any of them", asserted against the documents rather
    than against a list written down once.
    """

    def test_no_denied_action_is_granted_by_anything_an_instance_carries(self):
        granted = _allowed_action_patterns(_INSTANCE_REACHABLE)
        conflicts = {}
        for denied in _denied_actions(DENY_POLICY):
            # fnmatch in this direction: a granted "iam:Get*" would cover a
            # denied "iam:GetRole". Both sides may carry wildcards, so a
            # denied wildcard is expanded against every granted pattern too.
            hits = [
                (pattern, origin)
                for pattern, origins in granted.items()
                for origin in origins
                if fnmatch.fnmatch(pattern, denied) or fnmatch.fnmatch(denied, pattern)
            ]
            if hits:
                conflicts[denied] = hits
        assert not conflicts, (
            "ClusterNode-Deny denies actions that an instance-reachable policy "
            f"grants; attaching it would break a live cluster: {conflicts}"
        )

    def test_the_deny_policy_is_not_empty(self):
        """Vacuity guard. Every assertion above is satisfied by a document that
        denies nothing at all, and an empty Deny policy attaches, renders and
        tears down exactly like a real one."""
        denied = _denied_actions(DENY_POLICY)
        assert len(denied) >= 20, f"only {len(denied)} actions denied"
        for expected in (
            "iam:PutRolePolicy",
            "iam:CreatePolicy",
            "iam:CreateUser",
            "iam:CreateAccessKey",
            "iam:AttachUserPolicy",
            "iam:UpdateAssumeRolePolicy",
            "iam:PutRolePermissionsBoundary",
            "logs:DeleteLogGroup",
            "organizations:*",
        ):
            assert expected in denied, f"ClusterNode-Deny lost {expected}"

    def test_every_statement_is_a_deny(self):
        """The document's whole value is that it cannot grant. One Allow in it
        would also be attached to every compute and login node, where the other
        guards in this repo -- the wildcard-mutation ratchet, the required-action
        manifest -- do not read it as a grant."""
        for stmt in _policy(DENY_POLICY)["Statement"]:
            assert stmt["Effect"] == "Deny", (
                f"ClusterNode-Deny statement {stmt.get('Sid')} is an Allow"
            )

    def test_the_deny_is_unscoped(self):
        """A Deny scoped to an ARN leaves the action reachable on every other
        resource, which for escalation primitives is the whole account. The one
        statement that is legitimately scoped lives in the boundary, not here."""
        for stmt in _policy(DENY_POLICY)["Statement"]:
            assert _resources(stmt) == ["*"], (
                f"{stmt.get('Sid')} is scoped to {stmt['Resource']}"
            )

    def test_it_closes_the_escalation_chain_head_node_iam_leaves_open(self):
        """The chain templates/CLAUDE.local.md documents: HeadNode-IAM grants
        iam:CreateRole and iam:PassRole, so the missing links are authoring a
        policy (PutRolePolicy or CreatePolicy) and rewriting an existing one
        (CreatePolicyVersion/SetDefaultPolicyVersion). Those four are what the
        chain needs; a deny list that dropped them while keeping the user- and
        group-creation ones would read plausible and close nothing."""
        granted = set(_allowed_action_patterns(_INSTANCE_REACHABLE))
        assert "iam:CreateRole" in granted and "iam:PassRole" in granted, (
            "vacuity guard: the chain's own preconditions are gone from "
            "HeadNode-IAM, so this test no longer describes the cluster"
        )
        denied = _denied_actions(DENY_POLICY)
        for link in ("iam:PutRolePolicy", "iam:CreatePolicy",
                     "iam:CreatePolicyVersion", "iam:SetDefaultPolicyVersion"):
            assert link in denied


class TestTheDenyPolicyReachesEveryNode:
    """Attached to the head node role by _setup_iam and to every other pool by
    AdditionalIamPolicies. Both halves are needed and neither implies the other:
    the head node is the only node whose role this toolkit creates."""

    def test_setup_iam_creates_and_attaches_it(self, tmp_path):
        iam = _ContractIam()
        _setup(iam, tmp_path)
        assert "pclustermaker-policy-s1-ClusterNode-Deny" in iam.created_policies
        assert "pclustermaker-policy-s1-ClusterNode-Deny" in (
            iam.attachments["pclustermaker-role-s1"]
        )

    @pytest.mark.parametrize("enable_monitoring", [False, True])
    def test_it_is_not_gated_on_monitoring(self, tmp_path, enable_monitoring):
        """HeadNode-Monitoring is the one conditional policy, and a new suffix
        added beside it inherits that gate by copy-paste. A deny policy that
        appears only when --enable_monitoring is passed protects the clusters
        that asked for Grafana and no others."""
        iam = _ContractIam()
        _setup(iam, tmp_path, enable_monitoring=enable_monitoring)
        assert "pclustermaker-policy-s1-ClusterNode-Deny" in iam.created_policies

    @pytest.mark.parametrize("enable_monitoring", [False, True])
    def test_teardown_deletes_it(self, enable_monitoring):
        """An undeleted policy costs nothing but blocks a same-name rebuild and
        accumulates silently."""
        iam = _ContractIam(
            existing_policies=[
                "pclustermaker-policy-s1" + s for s in _MANAGED_POLICY_SUFFIXES
            ],
            existing_role="pclustermaker-role-s1",
        )
        iam.attachments["pclustermaker-role-s1"] = set(iam.policies)
        _delete_managed_policies(
            iam, "pclustermaker-role-s1", "pclustermaker-policy-s1", ACCOUNT,
            suppress=False, enable_monitoring=enable_monitoring,
        )
        assert "pclustermaker-policy-s1-ClusterNode-Deny" in iam.deleted_policies
        assert "pclustermaker-policy-s1-ClusterNode-Deny" in iam.detached

    def test_every_node_pool_in_the_cluster_config_carries_it(
        self, rendered_cluster_config
    ):
        """Parsed out of the rendered YAML, not matched as text. The three
        AdditionalIamPolicies sites are byte-identical, so a substring check
        passes with the policy added to only one of them."""
        config = rendered_cluster_config
        arn = config["_deny_policy_arn"]

        pools = [("LoginNodes", config["LoginNodes"]["Pools"][0])]
        pools += [(q["Name"], q) for q in config["Scheduling"]["SlurmQueues"]]
        assert len(pools) == 3, f"fixture lost a pool: {[n for n, _ in pools]}"
        for name, pool in pools:
            policies = [p["Policy"] for p in pool["Iam"]["AdditionalIamPolicies"]]
            assert arn in policies, f"{name} does not carry ClusterNode-Deny"

    def test_the_head_node_gets_it_through_the_role_instead(
        self, rendered_cluster_config
    ):
        """The head node has InstanceRole, which PCluster treats as mutually
        exclusive with AdditionalIamPolicies -- so its copy can only come from
        _setup_iam's attach, and this is the fact that also makes the head node
        the only role this toolkit can put a permissions boundary on."""
        config = rendered_cluster_config
        assert "InstanceRole" in config["HeadNode"]["Iam"]
        assert "AdditionalIamPolicies" not in config["HeadNode"]["Iam"]


class TestTheHeadNodeRoleIsCreatedUnderTheBoundary:
    def test_a_new_role_carries_the_boundary(self, tmp_path):
        iam = _ContractIam()
        _setup(iam, tmp_path)
        assert iam.created_roles == [
            {"name": "pclustermaker-role-s1",
             "boundary": _cluster_boundary_arn(ACCOUNT)}
        ]

    def test_the_boundary_exists_before_the_role_does(self, tmp_path):
        """IAM rejects create_role with a PermissionsBoundary ARN that does not
        resolve, so ordering is not cosmetic. Read off the call trace because
        both calls succeed against the fake in either order."""
        iam = _ContractIam()
        _setup(iam, tmp_path)
        created = [c for c in iam.calls if c[0] == "create_policy"
                   and c[1] == _cluster_boundary_name()]
        roles = [i for i, c in enumerate(iam.calls) if c[0] == "create_role"]
        assert created, "the boundary was never created"
        assert iam.calls.index(("create_policy", _cluster_boundary_name())) < roles[0]

    def test_an_existing_role_has_the_boundary_reasserted(self, tmp_path):
        iam = _ContractIam(existing_role="pclustermaker-role-s1")
        _setup(iam, tmp_path)
        assert iam.boundary_puts == [
            ("pclustermaker-role-s1", _cluster_boundary_arn(ACCOUNT))
        ]

    def test_the_reassert_happens_before_the_already_satisfied_early_return(
        self, tmp_path, capsys
    ):
        """The case the whole ordering exists for. _setup_iam returns early when
        the role is present with every expected policy attached -- which is what
        a role built by a toolkit predating the boundary looks like on every
        subsequent build. Reasserting after that check would leave such a role
        uncapped forever while each build reported success.
        """
        role = "pclustermaker-role-s1"
        iam = _ContractIam(
            existing_policies=[
                "pclustermaker-policy-s1" + s for s in _MANAGED_POLICY_SUFFIXES
            ],
            existing_role=role,
        )
        iam.attachments[role] = set(iam.policies)
        _setup(iam, tmp_path)
        assert "Found ec2_iam_role with all policies attached" in capsys.readouterr().out
        assert iam.roles[role]["boundary"] == _cluster_boundary_arn(ACCOUNT)

    def test_an_existing_boundary_is_reused_rather_than_recreated(self, tmp_path):
        """Account-level and shared by every cluster in the account, so the
        second build onward always finds it. EntityAlreadyExists is the normal
        path, not an error, and the boundary is deliberately never updated --
        changing it is an administrator's action, out of band."""
        iam = _ContractIam(existing_policies=[_cluster_boundary_name()])
        before = json.loads(json.dumps(iam.policies[_cluster_boundary_name()]))
        _setup(iam, tmp_path)
        assert _cluster_boundary_name() not in iam.created_policies
        assert iam.policies[_cluster_boundary_name()] == before
        assert iam.created_roles[0]["boundary"] == _cluster_boundary_arn(ACCOUNT)

    def test_a_create_policy_failure_that_is_not_already_exists_propagates(
        self, tmp_path
    ):
        """An AccessDenied on the boundary must not be swallowed as "it already
        exists": the role would then be created against an ARN that resolves to
        nothing, or -- worse, if create_role somehow succeeded -- uncapped. Only
        EntityAlreadyExists is the reuse path."""
        class _Denied(_ContractIam):
            def create_policy(self, PolicyName, PolicyDocument):
                if PolicyName == _cluster_boundary_name():
                    raise _client_error("AccessDenied", "CreatePolicy")
                return super().create_policy(PolicyName, PolicyDocument)

        iam = _Denied()
        with pytest.raises(Exception) as excinfo:
            _setup(iam, tmp_path)
        assert excinfo.value.response["Error"]["Code"] == "AccessDenied"
        assert iam.created_roles == []

    def test_teardown_leaves_the_boundary_in_place(self):
        """Deliberate, and for a stronger reason than the MCP boundary's: this
        one is account-level, so every other live cluster's role is bounded by
        the same document and deleting it on one teardown uncaps all of them.
        It also cannot be reached by suffix -- it carries none -- so the guard
        is that no teardown path names it at all."""
        role = "pclustermaker-role-s1"
        iam = _ContractIam(
            existing_policies=[_cluster_boundary_name()]
            + ["pclustermaker-policy-s1" + s for s in _MANAGED_POLICY_SUFFIXES],
            existing_role=role,
        )
        iam.attachments[role] = set(iam.policies) - {_cluster_boundary_name()}
        _delete_managed_policies(
            iam, role, "pclustermaker-policy-s1", ACCOUNT,
            suppress=False, enable_monitoring=True,
        )
        assert _cluster_boundary_name() in iam.policies
        assert _cluster_boundary_name() not in iam.deleted_policies

    def test_no_teardown_surface_names_the_boundary(self):
        """kill_pcluster.py deletes policies by name, so a literal added there
        would delete the shared boundary out from under every other cluster
        without going through _delete_managed_policies. src/delete_pcluster.yml
        was the other surface swept here until that playbook was deleted;
        src/pcluster_core.py is deliberately not swept in its place, since the
        boundary is created there and naming it is correct."""
        for relpath in ("kill_pcluster.py",):
            with open(os.path.join(REPO_ROOT, relpath)) as fh:
                assert _cluster_boundary_name() not in fh.read(), (
                    f"{relpath} names the shared cluster boundary"
                )


class TestTheBoundaryCeilingCoversWhatTheClusterPoliciesGrant:
    """A permissions boundary is an intersection: the role's effective
    permissions are its identity policies AND the boundary. A service missing
    from the ceiling therefore silently removes every grant in that service --
    and not at deploy time. CreatePolicy accepts it, create_role accepts it, the
    stack builds, and the head node fails partway through bootstrap with an
    AccessDenied on a call that worked yesterday. So the ceiling is derived from
    the documents rather than written down.
    """

    # Services the ceiling carries beyond what the shipped documents grant.
    # Pinned by equality so the margin cannot quietly become a wildcard, and
    # each is a service a near-term policy edit would plausibly reach for:
    #   elasticloadbalancing -- login node pools sit behind an NLB, and its
    #     absence from an MCP tier is what made describe-cluster fail against
    #     every --enable_loginnode cluster (CLAUDE-STATE.md, R4).
    #   secretsmanager -- the SSH key secret; operator-side today, head-node
    #     retrieval is the obvious next move.
    #   resource-groups, tag -- PCluster tags what it creates.
    _DOCUMENTED_MARGIN = {
        "elasticloadbalancing",
        "resource-groups",
        "secretsmanager",
        "tag",
    }

    def _ceiling_services(self):
        services = set()
        for stmt in _policy(BOUNDARY_POLICY)["Statement"]:
            if stmt["Effect"] != "Allow":
                continue
            for action in _actions(stmt):
                services.add(action.split(":")[0])
        return services

    def test_every_service_an_instance_policy_grants_is_in_the_ceiling(self):
        granted = set()
        for fname in _INSTANCE_REACHABLE:
            for stmt in _policy(fname)["Statement"]:
                if stmt["Effect"] != "Allow":
                    continue
                for action in _actions(stmt):
                    granted.add(action.split(":")[0])
        missing = granted - self._ceiling_services()
        assert not missing, (
            "the boundary would cap away every grant in these services, and the "
            f"failure lands on a live head node mid-bootstrap: {sorted(missing)}"
        )

    def test_the_margin_beyond_that_union_is_the_documented_one(self):
        """Pinned by equality in both directions. Without it the safe fix for
        any narrow-ceiling failure is another service wildcard, and the ceiling
        stops being derived from anything."""
        granted = set()
        for fname in _INSTANCE_REACHABLE:
            for stmt in _policy(fname)["Statement"]:
                if stmt["Effect"] == "Allow":
                    for action in _actions(stmt):
                        granted.add(action.split(":")[0])
        assert self._ceiling_services() - granted == self._DOCUMENTED_MARGIN

    def test_the_ceiling_is_not_a_bare_wildcard(self):
        """Vacuity guard: `"Action": "*"` satisfies every coverage assertion
        above while capping nothing, and reads as a boundary to anyone
        skimming the attached-policy list."""
        for stmt in _policy(BOUNDARY_POLICY)["Statement"]:
            if stmt["Effect"] != "Allow":
                continue
            for action in _actions(stmt):
                assert action != "*", "the ceiling is a bare wildcard"
                assert action.endswith(":*"), (
                    f"{action} is narrower than a service wildcard; a boundary is "
                    f"a cap, and per-action precision here breaks a build the "
                    f"first time an identity policy grants a sibling action"
                )
        assert len(self._ceiling_services()) < 40, (
            "the ceiling has grown past a derived union into an allowlist of "
            "most of AWS"
        )

    def test_the_boundary_is_at_least_as_strong_as_the_deny_policy(self):
        """The two are independent mechanisms and a node carries both, but the
        boundary is the one that survives a detached policy. Anything
        ClusterNode-Deny refuses must be refused here too, or the pair disagrees
        about what a cluster role may do."""
        missing = _denied_actions(DENY_POLICY) - _denied_actions(BOUNDARY_POLICY)
        assert not missing, f"the boundary permits what ClusterNode-Deny denies: {missing}"

    def test_the_boundary_denies_edits_to_itself(self):
        """A boundary the bounded principal can version is not a boundary. The
        head node role holds no policy-versioning grant today, so this is the
        cap that keeps it that way if one is ever added."""
        arn = _cluster_boundary_arn(ACCOUNT)
        covered = set()
        for stmt in _policy(BOUNDARY_POLICY)["Statement"]:
            if stmt["Effect"] != "Deny" or arn not in _resources(stmt):
                continue
            covered |= set(_actions(stmt))
        for action in ("iam:CreatePolicyVersion", "iam:SetDefaultPolicyVersion",
                       "iam:DeletePolicyVersion", "iam:DeletePolicy"):
            assert action in covered, f"the boundary does not deny {action} on itself"

    def test_the_arn_the_document_names_is_the_one_the_code_creates(self):
        """Two sources for one name. The document hardcodes the boundary's own
        ARN in BoundaryDenyEditingItself and _cluster_boundary_name() supplies
        it everywhere else; a rename in one is a Deny that protects a policy
        nobody has."""
        rendered = json.dumps(_policy(BOUNDARY_POLICY))
        assert _cluster_boundary_arn(ACCOUNT) in rendered
        assert _cluster_boundary_name() == "pclustermaker-cluster-boundary"


class TestOnlyTheHeadNodeRoleCanBeBounded:
    """The documented scope limit, asserted rather than trusted.

    If a future edit gave the queues an InstanceRole, the boundary would become
    applicable to them and this asymmetry would be worth revisiting -- so the
    reason for the limit is pinned, not just the limit.
    """

    def test_the_queues_and_login_pool_use_policies_pclusters_cdk_attaches(
        self, rendered_cluster_config
    ):
        config = rendered_cluster_config
        pools = [config["LoginNodes"]["Pools"][0]] + config["Scheduling"]["SlurmQueues"]
        for pool in pools:
            assert "InstanceRole" not in pool["Iam"], (
                "a pool now names a role this toolkit could create and bound; "
                "the head-node-only scope limit no longer describes the config"
            )
            assert pool["Iam"]["AdditionalIamPolicies"]

    def test_operator_policy_does_not_condition_create_role_on_a_boundary(self):
        """MCPDeployPolicy grants iam:CreateRole only under a StringEquals on
        iam:PermissionsBoundary, which is what makes every MCP role bounded by
        construction. The same condition here would refuse PCluster's CDK its
        own unbounded CreateRole and break every cluster build, so the cluster
        side deliberately does not have it. Residual risk, stated so it is not
        mistaken for coverage: a role matching pclustermaker-role-* can still be
        created unbounded by whoever holds these credentials.
        """
        for stmt in _policy("OperatorPolicy.json_src")["Statement"]:
            if "iam:CreateRole" not in _actions(stmt):
                continue
            assert "Condition" not in stmt, (
                "iam:CreateRole is now conditioned; PCluster's CDK creates the "
                "compute and login node roles and would be refused"
            )


class TestTheOperatorCanActuallyCreateAndBindTheBoundary:
    """OperatorPolicy is the least-privilege credential set _setup_iam runs
    under. Neither iam:CreatePolicy on the boundary's own name nor
    iam:PutRolePermissionsBoundary was in it, and the boundary name is outside
    the pclustermaker-policy-* wildcard the existing lifecycle statement covers
    -- deliberately, since a principal who can version its own boundary does not
    have one. Without both grants the first build under these credentials fails
    at _setup_iam with AccessDenied.
    """

    def setup_method(self):
        self.statements = {s.get("Sid"): s
                           for s in _policy("OperatorPolicy.json_src")["Statement"]}

    def test_the_boundary_is_outside_the_policy_lifecycle_wildcard(self):
        """The premise the two new statements exist for. If the boundary name
        ever matched pclustermaker-policy-*, the operator would hold
        CreatePolicy on it through the general grant -- and the Deny below would
        be the only thing keeping them from versioning it."""
        for resource in _resources(self.statements["IAMManagedPolicyLifecycle"]):
            assert not fnmatch.fnmatch(_cluster_boundary_arn(ACCOUNT), resource)

    def test_the_operator_may_create_and_read_the_boundary(self):
        stmt = self.statements["IAMClusterBoundaryBootstrapReadAndCreate"]
        assert stmt["Effect"] == "Allow"
        assert _resources(stmt) == [_cluster_boundary_arn(ACCOUNT)]
        for action in ("iam:CreatePolicy", "iam:GetPolicy", "iam:GetPolicyVersion"):
            assert action in _actions(stmt)

    def test_the_operator_may_bind_a_cluster_role_only_to_this_boundary(self):
        """_setup_iam calls put_role_permissions_boundary on a role that already
        existed. Granting the action unconditioned would let the same credentials
        move a cluster role onto a boundary of their choosing, which is a cap the
        operator writes rather than one an administrator does."""
        stmt = self.statements["IAMBoundClusterRoleOnly"]
        assert _actions(stmt) == ["iam:PutRolePermissionsBoundary"]
        assert stmt["Condition"]["StringEquals"]["iam:PermissionsBoundary"] == (
            _cluster_boundary_arn(ACCOUNT)
        )
        assert _resources(stmt) == [f"arn:aws:iam::{ACCOUNT}:role/pclustermaker-role-*"]

    def test_the_operator_cannot_weaken_the_boundary(self):
        stmt = self.statements["IAMDenyWeakeningTheClusterBoundary"]
        assert stmt["Effect"] == "Deny"
        for action in ("iam:CreatePolicyVersion", "iam:SetDefaultPolicyVersion",
                       "iam:DeletePolicyVersion", "iam:DeletePolicy",
                       "iam:DeleteRolePermissionsBoundary"):
            assert action in _actions(stmt)
        assert _cluster_boundary_arn(ACCOUNT) in _resources(stmt)
        assert f"arn:aws:iam::{ACCOUNT}:role/pclustermaker-role-*" in _resources(stmt)


@pytest.fixture
def rendered_cluster_config(cluster_params_gpu_queue_enabled):
    """config.pcluster.j2 rendered with the login node pool and both queues on,
    so all three AdditionalIamPolicies sites appear at once. Built on the shared
    conftest fixture rather than a local vars dict: config.pcluster.j2 renders
    with StrictUndefined, so a variable added upstream fails here immediately
    instead of drifting out of a private copy. Jinja2 settings match
    ansible.builtin.template's own defaults (trim_blocks on, lstrip_blocks off).
    """
    from jinja2 import Environment, FileSystemLoader, StrictUndefined

    params = {
        **cluster_params_gpu_queue_enabled,
        "enable_loginnode": "true",
        "loginnode_instance_type": "c8g.xlarge",
        "loginnode_count": 1,
        "loginnode_subnet_id": "subnet-0abc123",
    }
    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        trim_blocks=True,
        lstrip_blocks=False,
        undefined=StrictUndefined,
    )
    rendered = env.get_template("config.pcluster.j2").render(**params)
    config = yaml.safe_load(rendered)
    config["_deny_policy_arn"] = (
        f"arn:aws:iam::{params['aws_account_id']}:policy/"
        f"{params['ec2_iam_policy']}-ClusterNode-Deny"
    )
    return config
