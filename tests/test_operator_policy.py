"""
Tests that the operator IAM policy template's ARN patterns actually match
the resource names the toolkit creates (pclustermaker-role-*,
pclustermaker-policy-*), not AWS ParallelCluster's own parallelcluster-*
naming convention.
"""

import json
import os
import re
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from pcluster_core import _ssh_secret_name

TEMPLATE_PATH = os.path.join(REPO_ROOT, "templates", "OperatorPolicy.json_src")

ACCOUNT_ID = "123456789012"
SERIAL = "1234567890"
EC2_IAM_ROLE = "pclustermaker-role-" + SERIAL
EC2_IAM_POLICY = "pclustermaker-policy-" + SERIAL


def _rendered():
    with open(TEMPLATE_PATH) as fh:
        raw = fh.read()
    return raw.replace("<AWS_ACCOUNT_ID>", ACCOUNT_ID)


def _statements_by_sid(policy):
    return {s["Sid"]: s for s in policy["Statement"]}


class TestOperatorPolicyRendersValidJson:
    def test_renders_valid_json(self):
        json.loads(_rendered())


class TestOperatorPolicyIamArnsMatchToolkitNaming:
    """The toolkit's own IAM role/policy names are pclustermaker-role-<serial>
    and pclustermaker-policy-<serial> (make_pcluster.py), not
    parallelcluster-* (that's AWS ParallelCluster's own internal naming).
    An operator policy scoped to parallelcluster-* cannot create, attach to,
    or delete the resources this toolkit actually creates.
    """

    def setup_method(self):
        self.policy = json.loads(_rendered())
        self.statements = _statements_by_sid(self.policy)

    def _all_resource_strings(self, sid):
        resource = self.statements[sid]["Resource"]
        return resource if isinstance(resource, list) else [resource]

    def test_no_parallelcluster_role_or_policy_arns(self):
        for sid, stmt in self.statements.items():
            resources = stmt["Resource"]
            resources = resources if isinstance(resources, list) else [resources]
            for r in resources:
                assert not re.search(r":(role|policy|instance-profile)/parallelcluster", r), (
                    f"Sid '{sid}' resource '{r}' scopes IAM role/policy/instance-profile "
                    "to AWS ParallelCluster's own naming instead of this toolkit's "
                    "pclustermaker-role-*/pclustermaker-policy-* resources"
                )

    def test_managed_policy_lifecycle_matches_ec2_iam_policy_naming(self):
        resources = self._all_resource_strings("IAMManagedPolicyLifecycle")
        pattern = re.compile(r"arn:aws:iam::" + ACCOUNT_ID + r":policy/pclustermaker-policy-\*")
        assert any(pattern.fullmatch(r) for r in resources)
        real_policy_arn = f"arn:aws:iam::{ACCOUNT_ID}:policy/{EC2_IAM_POLICY}-HeadNode-Compute"
        assert any(re.fullmatch(r.replace("*", ".*"), real_policy_arn) for r in resources)

    def test_role_lifecycle_matches_ec2_iam_role_naming(self):
        resources = self._all_resource_strings("IAMRoleLifecycle")
        pattern = re.compile(r"arn:aws:iam::" + ACCOUNT_ID + r":role/pclustermaker-role-\*")
        assert any(pattern.fullmatch(r) for r in resources)
        role_arn = f"arn:aws:iam::{ACCOUNT_ID}:role/{EC2_IAM_ROLE}"
        assert any(re.fullmatch(r.replace("*", ".*"), role_arn) for r in resources)

    def test_attach_detach_condition_matches_ec2_iam_policy_naming(self):
        stmt = self.statements["IAMAttachDetachClusterPolicies"]
        cond = stmt["Condition"]["StringLike"]["iam:PolicyARN"]
        assert cond == f"arn:aws:iam::{ACCOUNT_ID}:policy/pclustermaker-policy-*"

    def test_instance_profile_matches_ec2_iam_role_naming(self):
        resources = self._all_resource_strings("IAMInstanceProfile")
        pattern = re.compile(r"arn:aws:iam::" + ACCOUNT_ID + r":instance-profile/pclustermaker-role-\*")
        assert any(pattern.fullmatch(r) for r in resources)


def _load_generator():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "generate_operator_policy",
        os.path.join(REPO_ROOT, "generate_operator_policy.py"),
    )
    orig = sys.prefix
    sys.prefix = os.path.join(REPO_ROOT, ".venv")
    try:
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.prefix = orig
    return mod


class TestGeneratorWritesNothingWhenCreateFails:
    """--output wrote the rendered policy before CreatePolicy was attempted, so
    a denied or already-existing policy still left a file on disk that looked
    like a successful run."""

    def _run(self, tmp_path, monkeypatch, create_ok):
        mod = _load_generator()
        out = tmp_path / "operator-policy.json"
        monkeypatch.setattr(mod, "_get_account_id", lambda: ACCOUNT_ID)
        monkeypatch.setattr(mod.boto3, "client", lambda *a, **k: object())

        def fake_create(iam, rendered, policy_name, description):
            if create_ok:
                return f"arn:aws:iam::{ACCOUNT_ID}:policy/{policy_name}"
            sys.exit("ERROR: policy already exists")

        monkeypatch.setattr(mod, "_create_policy", fake_create)
        monkeypatch.setattr(
            sys, "argv",
            ["generate_operator_policy.py", "--create", "-o", str(out)],
        )
        return mod, out

    def test_no_output_file_when_create_fails(self, tmp_path, monkeypatch):
        mod, out = self._run(tmp_path, monkeypatch, create_ok=False)
        with pytest.raises(SystemExit):
            mod.main()
        assert not out.exists()

    def test_output_file_written_when_create_succeeds(self, tmp_path, monkeypatch):
        mod, out = self._run(tmp_path, monkeypatch, create_ok=True)
        mod.main()
        assert out.exists()
        json.loads(out.read_text())


class TestOperatorPolicySecretsManagerMatchesSshSecretName:
    def test_secrets_manager_resource_matches_ssh_secret_name_prefix(self):
        rendered = json.loads(_rendered())
        stmt = _statements_by_sid(rendered)["SecretsManagerSSHKey"]
        resources = stmt["Resource"]
        secret_name = _ssh_secret_name("mycluster", SERIAL)
        prefix = secret_name.split("/")[0]
        assert any(prefix in r for r in resources)
