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


class TestTheMcpDeployPolicyIsReachableFromACommand:
    """`MCPDeployPolicy.json_src` was normative in `CLAUDE.md`, pinned by
    tests in three files, and rendered by nothing.

    `generate_operator_policy.py` hardcoded `_TEMPLATE =
    OperatorPolicy.json_src`, and `_setup_mcp_infra` creates the seven
    *handler* roles plus `MCPRoleBoundary` -- not this. So the
    least-privilege deploy path was written down and unreachable from any
    command, and every deployment ran as `AdministratorAccess`.
    """

    def _run(self, monkeypatch, argv):
        mod = _load_generator()
        monkeypatch.setattr(mod, "_get_account_id", lambda: ACCOUNT_ID)
        monkeypatch.setattr(mod.boto3, "client", lambda *a, **k: object())
        seen = {}

        def fake_create(iam, rendered, policy_name, description):
            seen["name"] = policy_name
            seen["description"] = description
            seen["rendered"] = rendered
            return f"arn:aws:iam::{ACCOUNT_ID}:policy/{policy_name}"

        monkeypatch.setattr(mod, "_create_policy", fake_create)
        monkeypatch.setattr(sys, "argv", ["generate_operator_policy.py"] + argv)
        mod.main()
        return seen

    def test_mcp_renders_the_mcp_document(self, monkeypatch, capsys):
        """Asserted on a Sid, not a substring: both documents mention IAM
        actions and account ARNs, so a text match cannot tell them apart."""
        self._run(monkeypatch, ["--mcp"])
        doc = json.loads(capsys.readouterr().out)
        sids = {s.get("Sid") for s in doc["Statement"]}
        assert "MCPCreateBoundedRoleOnly" in sids
        assert "IAMManagedPolicyLifecycle" not in sids, "rendered the operator policy"

    def test_the_default_is_still_the_operator_policy(self, monkeypatch, capsys):
        """Vacuity guard: the flag must select, not replace."""
        self._run(monkeypatch, [])
        doc = json.loads(capsys.readouterr().out)
        sids = {s.get("Sid") for s in doc["Statement"]}
        assert "IAMManagedPolicyLifecycle" in sids
        assert "MCPCreateBoundedRoleOnly" not in sids

    def test_mcp_creates_under_the_mcp_name(self, monkeypatch):
        """The defect this shape invites: `--policy-name` defaulting to the
        operator name at parse time cannot be told apart from an operator
        name passed explicitly, so --mcp would write the MCP document under
        the operator policy's name -- silently replacing the meaning of an
        existing policy. Both defaults resolve after parsing for that
        reason."""
        seen = self._run(monkeypatch, ["--mcp", "--create"])
        assert seen["name"] == "parallelcluster-mcp-deploy-pclustermaker"
        assert "MCP" in seen["description"]

    def test_the_operator_name_is_unchanged(self, monkeypatch):
        seen = self._run(monkeypatch, ["--create"])
        assert seen["name"] == "parallelcluster-operator-pclustermaker"

    @pytest.mark.parametrize("argv", [["--create"], ["--mcp", "--create"]])
    def test_an_explicit_policy_name_still_wins(self, monkeypatch, argv):
        seen = self._run(monkeypatch, argv + ["--policy-name", "chosen"])
        assert seen["name"] == "chosen"

    def test_the_deployer_policy_is_not_in_the_handler_namespace(self):
        """It must outlive `deploy_mcp.py --teardown` -- you need it to run
        the teardown. Teardown enumerates `_mcp_policy_templates()` rather
        than sweeping a prefix, so the two do not collide today either way;
        this pins that the *name* also stays outside the namespace a future
        sweep would target."""
        import pcluster_core as pc

        mod = _load_generator()
        name = mod._MCP_POLICY_NAME
        assert not name.startswith(pc._MCP_POLICY_NAME_PREFIX)
        handler_names = {pc._mcp_policy_name(b) for b in pc._mcp_policy_templates()}
        assert name not in handler_names
        assert "MCPDeployPolicy.json_src" not in pc._mcp_policy_templates(), (
            "teardown would delete the policy the deployer runs under"
        )

    @pytest.mark.parametrize("mode", ["operator", "mcp"])
    def test_each_document_fits_iams_managed_policy_limit(self, mode):
        """6,144 bytes, measured the way IAM measures it -- whitespace
        excluded. This is the constraint that makes them two policies
        rather than one: appending the MCP grants to OperatorPolicy came to
        6,358 bytes."""
        mod = _load_generator()
        template, _name, _desc = mod._MODES[mode]
        doc = json.loads(mod._render(ACCOUNT_ID, template))
        compact = json.dumps(doc, separators=(",", ":"))
        assert len(compact) <= 6144, f"{mode}: {len(compact)} bytes"

    @pytest.mark.parametrize("mode", ["operator", "mcp"])
    def test_no_placeholder_survives_rendering(self, mode):
        """An unrendered `<AWS_ACCOUNT_ID>` is accepted by json.loads and
        rejected by IAM, so JSON validity is not the check."""
        mod = _load_generator()
        template, _name, _desc = mod._MODES[mode]
        assert "<AWS_ACCOUNT_ID>" not in mod._render(ACCOUNT_ID, template)
