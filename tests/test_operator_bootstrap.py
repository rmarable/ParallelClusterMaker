"""One command must be enough to make a fresh clone able to build clusters.

The toolkit cannot grant itself permissions -- a tool that can do that has
no ceiling, which is already why `deploy_mcp.py` preflights its permissions
rather than granting them, and why no MCP tier holds `iam:AttachUserPolicy`
at all. So exactly one setup step has to be taken by a human with IAM
rights, and the whole point of `--bootstrap` is that it is *one* step,
idempotent, and safe to re-run after pulling a change that adds a grant.

It is deliberately CLI-only and identical on both MCP surfaces, because it
runs before either exists: `deploy_mcp.py` is local-only, so a tool that
sets up your permissions cannot live behind the thing those permissions
deploy.
"""

import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

DOC = {"Version": "2012-10-17", "Statement": [
    {"Sid": "S", "Effect": "Allow", "Action": "sts:GetCallerIdentity",
     "Resource": "*"}]}
RENDERED = json.dumps(DOC)
OTHER = json.dumps({"Version": "2012-10-17", "Statement": [
    {"Sid": "S", "Effect": "Allow", "Action": "iam:ListRoles",
     "Resource": "*"}]})
ACCT = "123456789012"
NAME = "parallelcluster-operator-pclustermaker"
ARN = f"arn:aws:iam::{ACCT}:policy/{NAME}"


def _err(code):
    from botocore.exceptions import ClientError
    return ClientError({"Error": {"Code": code, "Message": code}}, "op")


class _STS:
    def __init__(self, arn=f"arn:aws:iam::{ACCT}:user/alice"):
        self.arn = arn

    def get_caller_identity(self):
        return {"Account": ACCT, "Arn": self.arn, "UserId": "AIDA"}


class _IAM:
    """Modelled on IAM's contract.

    `GetPolicy` raises NoSuchEntity for an absent policy; a Document comes
    back already decoded by botocore's `after-call.iam` handler; and
    `CreatePolicyVersion` fails once five versions exist, which is why the
    version list is real rather than a counter.
    """

    def __init__(self, *, exists=False, document=None, attached=(), versions=1,
                 default_index=None):
        self.exists = exists
        self.document = document
        # default_index defaults to the newest, but must be settable: with
        # the newest version in force, deleting the in-force version and
        # deleting the oldest are the same call, and the mutation hides.
        d = versions - 1 if default_index is None else default_index
        self.versions = [
            {"VersionId": f"v{i + 1}", "IsDefaultVersion": i == d,
             "CreateDate": i}
            for i in range(versions)
        ] if exists else []
        self.attached = list(attached)
        self.calls = []

    def get_policy(self, PolicyArn):
        self.calls.append(("get_policy", PolicyArn))
        if not self.exists:
            raise _err("NoSuchEntity")
        default = [v for v in self.versions if v["IsDefaultVersion"]][0]
        return {"Policy": {"DefaultVersionId": default["VersionId"]}}

    def get_policy_version(self, PolicyArn, VersionId):
        return {"PolicyVersion": {"Document": json.loads(self.document)}}

    def list_policy_versions(self, PolicyArn):
        return {"Versions": list(self.versions)}

    def delete_policy_version(self, PolicyArn, VersionId):
        self.calls.append(("delete_policy_version", VersionId))
        self.versions = [v for v in self.versions if v["VersionId"] != VersionId]

    def create_policy_version(self, PolicyArn, PolicyDocument, SetAsDefault):
        if len(self.versions) >= 5:
            raise _err("LimitExceeded")
        self.calls.append(("create_policy_version", None))
        for v in self.versions:
            v["IsDefaultVersion"] = False
        vid = f"v{len(self.versions) + 1}"
        self.versions.append({"VersionId": vid, "IsDefaultVersion": True,
                              "CreateDate": 99})
        self.document = PolicyDocument
        return {"PolicyVersion": {"VersionId": vid}}

    def create_policy(self, PolicyName, PolicyDocument, Description):
        self.calls.append(("create_policy", PolicyName))
        self.exists = True
        self.document = PolicyDocument
        self.versions = [{"VersionId": "v1", "IsDefaultVersion": True,
                          "CreateDate": 0}]
        return {"Policy": {"Arn": ARN, "DefaultVersionId": "v1"}}

    def list_attached_user_policies(self, UserName):
        return {"AttachedPolicies": [{"PolicyArn": a} for a in self.attached]}

    def list_attached_role_policies(self, RoleName):
        return {"AttachedPolicies": [{"PolicyArn": a} for a in self.attached]}

    def attach_user_policy(self, UserName, PolicyArn):
        self.calls.append(("attach_user_policy", UserName))
        self.attached.append(PolicyArn)

    def attach_role_policy(self, RoleName, PolicyArn):
        self.calls.append(("attach_role_policy", RoleName))
        self.attached.append(PolicyArn)


@pytest.fixture
def core():
    import pcluster_core
    return pcluster_core


def _boot(core, iam, sts, **kw):
    return core.bootstrap_operator_policy(
        iam, sts, policy_name=NAME, rendered=RENDERED, description="d", **kw)


class TestAFreshCloneNeedsOneCommand:
    def test_an_absent_policy_is_created_and_attached(self, core):
        iam, sts = _IAM(exists=False), _STS()
        r = _boot(core, iam, sts)
        assert (r.policy_action, r.attach_action) == ("created", "attached")
        assert ("create_policy", NAME) in iam.calls
        assert ("attach_user_policy", "alice") in iam.calls
        assert r.ok

    def test_it_is_idempotent(self, core):
        """The second run must do nothing, not fail. An operator who reruns
        setup after pulling should not have to know whether they already
        ran it."""
        iam, sts = _IAM(exists=False), _STS()
        _boot(core, iam, sts)
        iam.calls.clear()
        r = _boot(core, iam, sts)
        assert (r.policy_action, r.attach_action) == ("current", "already")
        assert not [c for c in iam.calls if c[0].startswith(("create", "attach"))]

    def test_a_stale_policy_is_converged_not_refused(self, core):
        """The case `--create` gets wrong: an account whose policy predates a
        new grant answers EntityAlreadyExists, and the operator stops with a
        policy silently a version behind -- which is how this account ended
        up with no `elasticloadbalancing` grant while builds still worked."""
        iam, sts = _IAM(exists=True, document=OTHER), _STS()
        r = _boot(core, iam, sts)
        assert r.policy_action == "updated"
        assert ("create_policy_version", None) in iam.calls
        assert json.loads(iam.document) == DOC

    def test_convergence_compares_against_aws_own_copy(self, core):
        """Never a stored hash, tag or git ref: a marker beside the truth is
        a second source that can be wrong about it."""
        iam, sts = _IAM(exists=True, document=RENDERED), _STS()
        r = _boot(core, iam, sts)
        assert r.policy_action == "current"
        assert ("get_policy", ARN) in iam.calls


class TestItStaysUnderIamsVersionCeiling:
    def test_the_oldest_non_default_version_is_pruned(self, core):
        """IAM caps a customer-managed policy at five versions and
        CreatePolicyVersion fails with LimitExceeded at the sixth, so a
        convergent command has to make room or it works four times and then
        stops."""
        iam, sts = _IAM(exists=True, document=OTHER, versions=5), _STS()
        r = _boot(core, iam, sts)
        assert r.policy_action == "updated"
        assert ("delete_policy_version", "v1") in iam.calls
        assert len(iam.versions) <= 5

    @pytest.mark.parametrize("default_index", [4, 0])
    def test_the_version_in_force_is_never_deleted(self, core, default_index):
        """Parametrized over the default being newest and *oldest*.

        Deleting the version currently in force would leave the policy
        briefly unenforced. With the newest in force that never happens by
        accident, because oldest-first and not-the-default pick the same
        version -- so only the oldest-in-force case can see the filter."""
        iam, sts = _IAM(exists=True, document=OTHER, versions=5,
                        default_index=default_index), _STS()
        default_before = [v["VersionId"] for v in iam.versions
                          if v["IsDefaultVersion"]][0]
        _boot(core, iam, sts)
        deleted = [v for c, v in iam.calls if c == "delete_policy_version"]
        assert default_before not in deleted, (
            f"deleted the version in force ({default_before})")


class TestItAttachesToWhoeverIsActuallyRunningIt:
    @pytest.mark.parametrize("arn,ptype,pname", [
        (f"arn:aws:iam::{ACCT}:user/alice", "user", "alice"),
        (f"arn:aws:iam::{ACCT}:user/eng/bob", "user", "bob"),
        (f"arn:aws:sts::{ACCT}:assumed-role/AdminRole/sess", "role", "AdminRole"),
        (f"arn:aws:iam::{ACCT}:role/BuildRole", "role", "BuildRole"),
    ])
    def test_each_caller_shape_resolves(self, core, arn, ptype, pname):
        """The assumed-role shape is the one that bites: it is neither an IAM
        role ARN nor something attach-role-policy accepts -- that wants the
        bare RoleName, and the session name is not it."""
        iam, sts = _IAM(exists=False), _STS(arn)
        r = _boot(core, iam, sts)
        assert (r.principal_type, r.principal_name) == (ptype, pname)
        assert (f"attach_{ptype}_policy", pname) in iam.calls

    def test_the_root_user_is_named_not_guessed_at(self, core):
        """A managed policy cannot be attached to the account root at all, so
        this must say so rather than fail with an opaque IAM error."""
        iam, sts = _IAM(exists=False), _STS(f"arn:aws:iam::{ACCT}:root")
        r = _boot(core, iam, sts)
        assert r.attach_action == "refused"
        # Not just "root": that word also appears in the ARN echoed by the
        # unresolvable-identity branch, so a substring check there passes
        # even when the root branch has been removed entirely.
        assert "cannot have a managed policy attached" in r.reason
        assert not r.ok

    def test_an_unresolvable_identity_refuses_rather_than_guessing(self, core):
        iam, sts = _IAM(exists=False), _STS("arn:aws:sts::1:federated-user/x")
        r = _boot(core, iam, sts)
        assert r.attach_action == "refused"
        assert not r.ok


class TestDryRunChangesNothing:
    @pytest.mark.parametrize("exists,doc", [(False, None), (True, OTHER)])
    def test_no_mutating_call_is_made(self, core, exists, doc):
        iam, sts = _IAM(exists=exists, document=doc), _STS()
        r = _boot(core, iam, sts, dry_run=True)
        mutations = [c for c, _ in iam.calls
                     if c in ("create_policy", "create_policy_version",
                              "attach_user_policy", "attach_role_policy",
                              "delete_policy_version")]
        assert mutations == [], mutations
        assert r.dry_run

    def test_it_still_reports_what_would_happen(self, core):
        iam, sts = _IAM(exists=False), _STS()
        r = _boot(core, iam, sts, dry_run=True)
        assert (r.policy_action, r.attach_action) == ("created", "attached")

    def test_no_attach_converges_the_policy_but_stops_there(self, core):
        iam, sts = _IAM(exists=False), _STS()
        r = _boot(core, iam, sts, attach=False)
        assert r.policy_action == "created"
        assert r.attach_action == "skipped"
        assert not [c for c, _ in iam.calls if c.startswith("attach")]
        assert r.ok


class TestTheOneLinerReadsAsASetupCommand:
    """`--bootstrap` is documented as the one command a fresh clone runs.

    It printed the rendered policy to stdout as well as the summary, so its
    own result arrived under ~330 lines of JSON -- noticed while certifying
    the create/attach path against real IAM, where the summary had to be
    hunted for. The document is still available: `-o FILE`, or no flags at
    all."""

    def _main_src(self):
        with open(os.path.join(REPO_ROOT, "generate_operator_policy.py")) as fh:
            return fh.read()

    def test_bootstrap_does_not_dump_the_document(self):
        src = self._main_src()
        i = src.index("elif not args.bootstrap:")
        assert "print(rendered)" in src[i:i + 500]

    def test_the_document_is_still_reachable_without_bootstrap(self):
        """Vacuity guard: the fix is a condition, not deleting the output."""
        src = self._main_src()
        assert src.count("print(rendered)") == 1
        assert "if args.output:" in src


class TestItIsNotReachableFromAnyMcpSurface:
    def test_no_tier_can_attach_a_policy_to_a_human_identity(self):
        """The reason this is a CLI command and not a tool. A tool that
        grants IAM to its own caller has no ceiling."""
        import glob
        import re
        for path in glob.glob(os.path.join(REPO_ROOT, "templates", "MCP*.json_src")):
            doc = json.loads(re.sub(r"<[A-Z_]+>", ACCT, open(path).read()))
            acts = [a for st in doc["Statement"] if st.get("Effect") == "Allow"
                    for a in ([st["Action"]] if isinstance(st.get("Action"), str)
                              else st.get("Action", []))]
            assert "iam:AttachUserPolicy" not in acts, os.path.basename(path)
            assert "iam:PutUserPolicy" not in acts, os.path.basename(path)

    def test_no_mcp_tool_exposes_the_bootstrap(self):
        import glob
        for path in glob.glob(os.path.join(REPO_ROOT, "mcp_server", "*.py")):
            assert "bootstrap_operator_policy" not in open(path).read(), path
