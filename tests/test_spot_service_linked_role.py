"""EC2 Spot's service-linked role must exist before a spot cluster is built.

A service-linked role is not attached to anything and cannot be created per
cluster: there is exactly one per account, the EC2 Spot service assumes it,
and every spot request in the account goes through it. Provisioning can only
ensure it *exists*.

Nothing creates it on our behalf. AWS auto-creates it on the first spot
request made by a principal holding `iam:CreateServiceLinkedRole`, and the
principal that makes ours is the head node -- `slurm_resume` calls
`ec2:CreateFleet` with CapacityType SPOT -- whose `HeadNode-IAM` policy
deliberately grants no IAM write of that kind. So in an account that has
never launched a spot instance by another route, every compute node fails to
resume with `AuthFailure.ServiceLinkedRoleCreationNotPermitted` **while the
stack reports CREATE_COMPLETE**: a cluster that builds green and cannot run a
job. Observed exactly that way on `acctproof3` (2026-08-29), where `sinfo`
showed `down# (Code:AuthFailure.ServiceLinkedRoleCreationNotPermitted)` and a
job sat PENDING through repeated node replacement.
"""

import ast
import json
import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(REPO_ROOT, "templates")


def _policy(name):
    with open(os.path.join(TEMPLATES, name)) as fh:
        return json.loads(re.sub(r"<[A-Z_]+>", "123456789012", fh.read()))


def _actions(doc):
    out = []
    for st in doc["Statement"]:
        if st.get("Effect") != "Allow":
            continue
        a = st.get("Action")
        out.extend([a] if isinstance(a, str) else a)
    return out


class _Err(Exception):
    pass


def _client_error(code):
    from botocore.exceptions import ClientError

    return ClientError({"Error": {"Code": code, "Message": code}}, "op")


class _IAM:
    """Modelled on IAM's own contract, not on what the caller happens to need.

    `GetRole` raises `NoSuchEntity` for an absent role;
    `CreateServiceLinkedRole` raises `InvalidInput` when the role already
    exists (IAM does not return AlreadyExists for this call) and
    `AccessDenied` when the caller may not create it.
    """

    def __init__(self, *, exists=False, get_error=None, create_error=None):
        self.exists = exists
        self.get_error = get_error
        self.create_error = create_error
        self.calls = []

    def get_role(self, RoleName):
        self.calls.append(("get_role", RoleName))
        if self.get_error:
            raise _client_error(self.get_error)
        if not self.exists:
            raise _client_error("NoSuchEntity")
        return {"Role": {"RoleName": RoleName}}

    def create_service_linked_role(self, AWSServiceName):
        self.calls.append(("create", AWSServiceName))
        if self.create_error:
            raise _client_error(self.create_error)
        return {"Role": {"RoleName": "AWSServiceRoleForEC2Spot"}}


@pytest.fixture
def core():
    import sys

    sys.path.insert(0, os.path.join(REPO_ROOT, "src"))
    import pcluster_core

    return pcluster_core


class TestTheThreeOutcomesStayDistinct:
    def test_an_existing_role_is_a_no_op(self, core):
        iam = _IAM(exists=True)
        core._ensure_spot_service_linked_role(iam)
        assert [c[0] for c in iam.calls] == ["get_role"], "must not try to create it"

    def test_an_absent_role_is_created(self, core):
        iam = _IAM(exists=False)
        core._ensure_spot_service_linked_role(iam)
        assert ("create", "spot.amazonaws.com") in iam.calls

    def test_an_absent_and_unauthorized_role_stops_the_build(self, core, capsys):
        """We then know for certain the cluster cannot run work, and we know
        it before anything has been created. Warning and proceeding would
        spend ~25 minutes and a head node to reach a cluster that reports
        CREATE_COMPLETE and cannot launch a compute node."""
        iam = _IAM(exists=False, create_error="AccessDenied")
        with pytest.raises(SystemExit) as e:
            core._ensure_spot_service_linked_role(iam)
        msg = str(e.value)
        assert "AuthFailure.ServiceLinkedRoleCreationNotPermitted" in msg
        assert "create-service-linked-role" in msg
        assert "--cluster_type ondemand" in msg

    def test_an_unreadable_role_only_warns(self, core, capsys):
        """Not the same as absent: this operator cannot *see* the role, which
        is a different fact from the role not existing, and an administrator
        may well have created it. Blocking here would refuse a build that
        would have worked."""
        iam = _IAM(get_error="AccessDenied")
        core._ensure_spot_service_linked_role(iam)
        err = capsys.readouterr().err
        assert "WARNING" in err
        assert "create-service-linked-role" in err

    def test_a_concurrent_creation_is_tolerated(self, core):
        """IAM answers InvalidInput when the role already exists, which is
        what a second build racing the first sees."""
        iam = _IAM(exists=False, create_error="InvalidInput")
        core._ensure_spot_service_linked_role(iam)  # must not raise


class TestItRunsOnlyForSpotAndOnlyBeforeAnythingIsCreated:
    @pytest.fixture
    def tree(self):
        with open(os.path.join(REPO_ROOT, "src", "pcluster_core.py")) as fh:
            return ast.parse(fh.read()), fh

    def test_it_is_gated_on_the_cluster_actually_being_spot(self):
        """An ondemand cluster needs no spot role, and calling
        CreateServiceLinkedRole unconditionally would make every ondemand
        build require a grant it has no use for."""
        with open(os.path.join(REPO_ROOT, "src", "pcluster_core.py")) as fh:
            src = fh.read()
        # rindex, not index: the `def` line contains the call string as a
        # substring, and matching it finds a window with no gate in it.
        i = src.rindex("_ensure_spot_service_linked_role(iam)")
        assert "def " not in src[src.rindex("\n", 0, i):i]
        window = src[max(0, i - 200):i]
        assert 'cluster_type == "spot"' in window

    def test_it_precedes_the_first_iam_mutation(self):
        """Same placement rule as the download-checksum validator: refusing
        after _setup_iam has run still bills the operator and leaves six
        managed policies, a role, a keypair and a bucket to sweep."""
        with open(os.path.join(REPO_ROOT, "src", "pcluster_core.py")) as fh:
            lines = fh.readlines()
        check = [n for n, l in enumerate(lines)
                 if "_ensure_spot_service_linked_role(iam)" in l and "def " not in l]
        setup = [n for n, l in enumerate(lines) if "_setup_iam(" in l and "def " not in l]
        assert check and setup
        assert max(check) < min(setup), "the check must run before _setup_iam"


class TestTheGrantIsTheOperatorsNotTheNodes:
    def test_no_instance_policy_can_create_a_service_linked_role(self):
        """A head node reachable by any Slurm job must not hold an IAM write
        action. It needs the role to exist, never to create it -- which is
        precisely why this is done operator-side."""
        for name in ("HeadNode-IAM.json_src", "HeadNode-Compute.json_src",
                     "HeadNode-Storage.json_src", "ComputeNode-Base.json_src"):
            acts = _actions(_policy(name))
            assert not any(
                a == "iam:*" or a.startswith("iam:CreateServiceLinkedRole")
                for a in acts
            ), name

    def test_the_operator_can_create_exactly_this_one_role(self):
        doc = _policy("OperatorPolicy.json_src")
        hits = [st for st in doc["Statement"]
                if "iam:CreateServiceLinkedRole" in (
                    [st["Action"]] if isinstance(st.get("Action"), str)
                    else st.get("Action", []))]
        assert len(hits) == 1, "exactly one statement should grant it"
        res = hits[0]["Resource"]
        res = [res] if isinstance(res, str) else res
        assert len(res) == 1
        assert res[0].endswith(
            ":role/aws-service-role/spot.amazonaws.com/AWSServiceRoleForEC2Spot"
        ), res
        assert "*" not in res[0].split(":role/")[1], "the role path must not be a wildcard"

    def test_the_remote_build_tier_can_do_its_job_too(self):
        """The floor, not the ceiling. `create_cluster` runs remotely on the
        container tier, so a remote spot build reaches the same code with the
        Lambda's IAM. Without this grant the check degrades to its
        cannot-see-it warning and the remote build produces exactly the dead
        cluster the check exists to prevent -- CREATE_COMPLETE, no compute
        nodes, ever. `MCPClusterBuild` carries it because `MCPStackMutation`
        has no bytes left."""
        doc = _policy("MCPClusterBuild.json_src")
        hits = [st for st in doc["Statement"]
                if "iam:CreateServiceLinkedRole" in (
                    [st["Action"]] if isinstance(st.get("Action"), str)
                    else st.get("Action", []))]
        assert len(hits) == 1, "the build tier must be able to ensure the role"
        res = hits[0]["Resource"]
        res = [res] if isinstance(res, str) else res
        assert res[0].endswith(
            ":role/aws-service-role/spot.amazonaws.com/AWSServiceRoleForEC2Spot"
        ), res

    def test_no_other_mcp_tier_gets_it(self):
        """Only the tier that builds clusters needs it. A read-only or
        fleet-toggle tier holding an IAM create action misrepresents itself."""
        import glob
        for path in glob.glob(os.path.join(TEMPLATES, "MCP*.json_src")):
            name = os.path.basename(path)
            if name in ("MCPClusterBuild.json_src", "MCPDeployPolicy.json_src",
                        "MCPRoleBoundary.json_src"):
                continue
            assert "iam:CreateServiceLinkedRole" not in _actions(_policy(name)), name

    def test_the_operator_policy_still_fits_the_managed_policy_limit(self):
        with open(os.path.join(TEMPLATES, "OperatorPolicy.json_src")) as fh:
            doc = json.loads(re.sub(r"<[A-Z_]+>", "123456789012", fh.read()))
        size = len(json.dumps(doc, separators=(",", ":")))
        assert size <= 6144, f"{size}B > 6144B"
