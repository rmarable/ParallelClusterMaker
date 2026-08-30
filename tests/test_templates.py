"""
Template render tests.

Renders every Jinja2 template in templates/ and hpc-benchmark/ using
StrictUndefined.  Any variable referenced in a template that is missing from
the fixture raises UndefinedError and fails the test immediately.

Filters used only by Ansible at playbook runtime (bool, upper, lookup) are
stubbed out so plain Python Jinja2 can render them without crashing.
"""

import fnmatch
import json
import os
from conftest import assert_source_is_real
import pytest
import re
import sys
import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# hpc-benchmark/*.j2 are rendered by core_create_cluster from the same vars file
# as templates/*.j2, so an undefined variable there fails at cluster build time.
TEMPLATE_DIRS = [
    os.path.join(REPO_ROOT, "templates"),
    os.path.join(REPO_ROOT, "hpc-benchmark"),
]

# Templates that are not rendered by Python at all — skip them.
# (JSON policy templates are not Jinja2 text, they're shell-substituted separately.)
SKIP_TEMPLATES = {
    "LustreS3HydrationPolicy.json_src",
}

# Files core_create_cluster renders despite not carrying a .j2 suffix. The
# suffix filter in _collect_templates() is what kept
# scripts/sbatch_default_submission_script.sh out of every render test for the
# life of the repo, so an unlisted extra here is a file no test renders.
# test_collect_templates_covers_every_template_the_toolkit_renders is the guard.
EXTRA_TEMPLATES = [
    (os.path.join(REPO_ROOT, "scripts"), "sbatch_default_submission_script.sh"),
]


def _make_env(template_dir):
    # trim_blocks/lstrip_blocks match ansible.builtin.template's own defaults
    # (True/False respectively, ansible/plugins/action/template.py) so these
    # tests see the file the node actually receives. While the env left both off,
    # every {% if %} in a rendered file left a blank line here that production
    # does not have -- enough to report a cosmetic defect in the SNS report that
    # never existed, and enough to hide a real one in a whitespace-sensitive
    # template. Do not "fix" lstrip_blocks to True to match trim_blocks; that
    # reintroduces the mismatch in the other direction.
    env = Environment(
        loader=FileSystemLoader(template_dir),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=False,
    )
    # Stub Ansible-only filters so they pass through without error.
    env.filters["bool"] = lambda v: str(v).lower() in ("true", "1", "yes")
    env.filters["upper"] = lambda v: str(v).upper()
    # Stub lookup() global — returns a placeholder string.
    env.globals["lookup"] = lambda *args, **kwargs: "<lookup-stub>"
    return env


class TestTheTestEnvironmentMatchesAnsible:
    """Every render assertion in this file is only as good as the env producing it.
    While _make_env left trim_blocks off, each {% if %} in a rendered file left a
    blank line the node never sees — enough to report a cosmetic defect in the SNS
    report that did not exist, and enough to hide a real one in a template where
    whitespace is load-bearing. These read the defaults out of the installed
    Ansible rather than restating them, so an upstream change fails here instead
    of silently invalidating the suite."""

    @staticmethod
    def _ansible_defaults():
        import inspect
        import re

        from ansible.plugins.action.template import ActionModule

        source = inspect.getsource(ActionModule.run)
        found = {}
        for opt in ("trim_blocks", "lstrip_blocks"):
            m = re.search(
                rf"{opt} = boolean\(self\._task\.args\.get\("
                rf"'{opt}', (True|False)\)",
                source,
            )
            assert m, f"cannot read ansible's {opt} default — upstream changed shape"
            found[opt] = m.group(1) == "True"
        return found

    @pytest.mark.parametrize("option", ["trim_blocks", "lstrip_blocks"])
    def test_the_env_uses_ansibles_own_default(self, option):
        env = _make_env(os.path.join(REPO_ROOT, "templates"))
        assert getattr(env, option) is self._ansible_defaults()[option]

    @pytest.mark.parametrize("option", ["trim_blocks", "lstrip_blocks"])
    def test_the_production_env_matches_ansible_too(self, option):
        """Workstream 2 Phase 0: pcluster_core._template_env is the production
        twin of this file's own _make_env -- the one every Python-side
        template render (vars_file.j2 today, more as Workstream 2's tiers
        land) must go through. Pinned the same way, against Ansible's real
        source, so the two configs cannot drift apart from each other or
        from what Ansible itself does."""
        src = os.path.join(REPO_ROOT, "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        import pcluster_core

        env = pcluster_core._template_env(os.path.join(REPO_ROOT, "templates"))
        assert getattr(env, option) is self._ansible_defaults()[option]

    # A test banning a per-task trim_blocks/lstrip_blocks override lived here.
    # It swept
    # src/create_pcluster.yml and src/delete_pcluster.yml for a per-task
    # trim_blocks/lstrip_blocks override on Ansible's `template:` module. Both
    # playbooks are gone -- nothing in this toolkit executes one -- and there is
    # no `template:` task left anywhere to override anything. The two tests
    # above are what the rule reduces to: the test env and the production env
    # both read their values out of the installed Ansible.


class TestRenderTemplate:
    """Direct unit tests for pcluster_core.render_template/_template_env,
    independent of any specific template's content."""

    @staticmethod
    def _pcluster_core():
        src = os.path.join(REPO_ROOT, "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        import pcluster_core

        return pcluster_core

    def test_renders_a_template_with_the_given_context(self, tmp_path):
        (tmp_path / "greeting.j2").write_text("Hello, {{ name }}!\n")
        out = self._pcluster_core().render_template(
            str(tmp_path), "greeting.j2", name="world"
        )
        assert out == "Hello, world!\n"

    def test_strict_undefined_raises_on_a_missing_variable(self, tmp_path):
        from jinja2 import UndefinedError

        (tmp_path / "greeting.j2").write_text("Hello, {{ name }}!\n")
        with pytest.raises(UndefinedError):
            self._pcluster_core().render_template(str(tmp_path), "greeting.j2")

    def test_trim_blocks_matches_ansible_shape(self, tmp_path):
        """The exact whitespace bug Phase 0 exists to fix: without
        trim_blocks=True, the newline after a block tag survives into the
        rendered file, which Ansible's own template: module never emits."""
        (tmp_path / "cond.j2").write_text(
            "before\n{% if true %}\nmiddle\n{% endif %}\nafter\n"
        )
        out = self._pcluster_core().render_template(str(tmp_path), "cond.j2")
        assert out == "before\nmiddle\nafter\n"

    def test_keeps_the_trailing_newline(self, tmp_path):
        (tmp_path / "plain.j2").write_text("one line\n")
        out = self._pcluster_core().render_template(str(tmp_path), "plain.j2")
        assert out.endswith("\n")


def _collect_templates():
    cases = []
    for tdir in TEMPLATE_DIRS:
        if not os.path.isdir(tdir):
            continue
        for fname in sorted(os.listdir(tdir)):
            if fname in SKIP_TEMPLATES:
                continue
            if fname.endswith((".j2", ".jinja2", ".jinja")):
                cases.append((tdir, fname))
    cases.extend(EXTRA_TEMPLATES)
    assert len(cases) > 0, "No templates discovered — check TEMPLATE_DIRS"
    return cases


@pytest.mark.parametrize("tdir,fname", _collect_templates())
def test_template_renders_without_undefined(tdir, fname, cluster_params):
    """Every template must render cleanly given the full fixture context."""
    env = _make_env(tdir)
    template = env.get_template(fname)
    # Should not raise UndefinedError, TemplateSyntaxError, or any other exception.
    rendered = template.render(**cluster_params)
    assert isinstance(rendered, str)
    assert len(rendered) > 0, f"{fname} rendered to an empty string"


@pytest.mark.parametrize("tdir,fname", _collect_templates())
def test_template_renders_custom_ami_variant(tdir, fname, cluster_params_custom_ami):
    """Templates must also render when custom_ami and placement_group are set."""
    env = _make_env(tdir)
    template = env.get_template(fname)
    rendered = template.render(**cluster_params_custom_ami)
    assert isinstance(rendered, str)
    assert (
        len(rendered) > 0
    ), f"{fname} rendered to an empty string (custom_ami variant)"
    if fname == "config.pcluster.j2":
        assert "ami-0abc1234567890def" in rendered, "custom_ami value not in config"
        assert "PlacementGroup:" in rendered, "PlacementGroup block absent from config"


@pytest.mark.parametrize("tdir,fname", _collect_templates())
def test_template_renders_monitoring_enabled_variant(
    tdir, fname, cluster_params_monitoring_enabled
):
    """Templates must render when enable_monitoring=true.

    Exercises the Sequence CustomActions block in config.pcluster.j2, the
    compute queue monitoring hook, and the vars_file monitoring section.
    """
    env = _make_env(tdir)
    template = env.get_template(fname)
    rendered = template.render(**cluster_params_monitoring_enabled)
    assert isinstance(rendered, str)
    assert (
        len(rendered) > 0
    ), f"{fname} rendered to an empty string (monitoring_enabled variant)"
    if fname == "vars_file.j2":
        assert 'enable_monitoring: "true"' in rendered
        assert "monitoring_version:" in rendered
        assert "monitoring_version_checksum:" in rendered
    if fname == "config.pcluster.j2":
        assert "monitoring" in rendered.lower(), "monitoring block absent from config"
    if fname == "monitoring-post-install-wrapper.j2":
        assert "monitoring" in rendered.lower(), "monitoring block absent from wrapper"


@pytest.mark.parametrize("tdir,fname", _collect_templates())
def test_template_renders_hpc_benchmarks_enabled(tdir, fname, cluster_params):
    """postinstall must contain the benchmark sync block when enable_hpc_benchmarks=true."""
    env = _make_env(tdir)
    rendered = env.get_template(fname).render(**cluster_params)
    assert isinstance(rendered, str)
    if fname == "postinstall.j2":
        assert "hpc-benchmark/" in rendered, "hpc-benchmark S3 sync block absent"
        assert "hpc-benchmark.sh" in rendered, "hpc-benchmark.sh chmod absent"


@pytest.mark.parametrize("tdir,fname", _collect_templates())
def test_template_renders_hpc_benchmarks_disabled(tdir, fname, cluster_params_hpc_benchmarks_disabled):
    """postinstall must NOT contain the benchmark block when enable_hpc_benchmarks=false."""
    env = _make_env(tdir)
    rendered = env.get_template(fname).render(**cluster_params_hpc_benchmarks_disabled)
    assert isinstance(rendered, str)
    if fname == "postinstall.j2":
        # Scoped to the sync *commands*, not to `aws s3 sync` as such and
        # not to the substrings either. The staging tree is pulled with the
        # same command and is unconditional -- it is how the head node gets
        # the tree at all now that nothing scp's it in -- so a bare command
        # ban forbids the thing it was never about; and "/hpc-benchmark/"
        # appears in a .bashrc alias that renders either way, so a
        # substring ban fails on content the gate does not govern.
        syncs = [ln for ln in rendered.splitlines() if "aws s3 sync" in ln]
        for ln in syncs:
            for gated in ("/hpc-benchmark/", "/performance/"):
                assert gated not in ln, (
                    f"benchmark S3 sync present when "
                    f"enable_hpc_benchmarks=false: {ln.strip()}"
                )
        assert any("/staging/" in ln for ln in syncs), (
            "the staging tree pull is not gated on benchmarks and must "
            "still be here"
        )


@pytest.mark.parametrize("tdir,fname", _collect_templates())
def test_template_renders_efa_enabled(tdir, fname, cluster_params_efa_enabled):
    """config.pcluster.j2 must contain the Efa block when enable_efa=true."""
    env = _make_env(tdir)
    rendered = env.get_template(fname).render(**cluster_params_efa_enabled)
    assert isinstance(rendered, str)
    if fname == "config.pcluster.j2":
        assert "Efa:" in rendered, "Efa block absent when enable_efa=true"


@pytest.mark.parametrize("tdir,fname", _collect_templates())
def test_template_renders_efs_enabled(tdir, fname, cluster_params):
    """config.pcluster.j2 must contain the EFS SharedStorage block when enable_efs=true."""
    env = _make_env(tdir)
    rendered = env.get_template(fname).render(**cluster_params)
    assert isinstance(rendered, str)
    if fname == "config.pcluster.j2":
        assert "StorageType: Efs" in rendered, \
            "EFS SharedStorage block absent when enable_efs=true"


@pytest.mark.parametrize("tdir,fname", _collect_templates())
def test_template_renders_fsx_enabled(tdir, fname, cluster_params):
    """config.pcluster.j2 must contain the FSx SharedStorage block when enable_fsx=true."""
    env = _make_env(tdir)
    rendered = env.get_template(fname).render(**cluster_params)
    assert isinstance(rendered, str)
    if fname == "config.pcluster.j2":
        assert "FsxLustre" in rendered or "LustreFileSystemId" in rendered, \
            "FSx SharedStorage block absent when enable_fsx=true"


@pytest.mark.parametrize("tdir,fname", _collect_templates())
def test_template_renders_spot_cluster(tdir, fname, cluster_params):
    """Templates must render without error when cluster_type=spot."""
    env = _make_env(tdir)
    rendered = env.get_template(fname).render(**cluster_params)
    assert isinstance(rendered, str)


@pytest.mark.parametrize("tdir,fname", _collect_templates())
def test_template_renders_gpu_enabled_variant(tdir, fname, cluster_params_gpu_enabled):
    """Templates must render when enable_gpu=true (p3.2xlarge, no NVMe store)."""
    env = _make_env(tdir)
    rendered = env.get_template(fname).render(**cluster_params_gpu_enabled)
    assert isinstance(rendered, str)
    assert len(rendered) > 0, f"{fname} rendered empty (gpu_enabled variant)"
    if fname == "vars_file.j2":
        assert 'enable_gpu: "true"' in rendered
    if fname == "postinstall.j2":
        assert "nvtop" in rendered
        assert "_NVME_DEVS" in rendered


@pytest.mark.parametrize("tdir,fname", _collect_templates())
def test_template_renders_gpu_gdr_enabled_variant(tdir, fname, cluster_params_gpu_gdr_enabled):
    """Templates must render with GdrSupport: true in the GPU queue when p4d + EFA-GDR enabled."""
    env = _make_env(tdir)
    rendered = env.get_template(fname).render(**cluster_params_gpu_gdr_enabled)
    assert isinstance(rendered, str)
    assert len(rendered) > 0, f"{fname} rendered empty (gpu_gdr_enabled variant)"
    if fname == "config.pcluster.j2":
        assert "- Name: gpu" in rendered, "GPU queue absent with enable_gpu_queue=true"
        assert "GdrSupport: true" in rendered, "GdrSupport absent in GPU queue with p4d + EFA-GDR"


@pytest.mark.parametrize("tdir,fname", _collect_templates())
def test_cpu_queue_present_when_enabled(tdir, fname, cluster_params):
    """config.pcluster.j2 must contain a compute queue with Instances: list when enable_cpu_queue=true."""
    if fname != "config.pcluster.j2":
        return
    env = _make_env(tdir)
    rendered = env.get_template(fname).render(**cluster_params)
    assert "- Name: compute" in rendered, "CPU queue absent when enable_cpu_queue=true"
    assert "Instances:" in rendered, "Instances: list absent in CPU queue"
    assert "c8g.2xlarge" in rendered, "CPU instance type absent from Instances: list"


@pytest.mark.parametrize("tdir,fname", _collect_templates())
def test_cpu_queue_absent_when_disabled(tdir, fname, cluster_params_gpu_enabled):
    """config.pcluster.j2 must not contain a compute queue when enable_cpu_queue=false."""
    if fname != "config.pcluster.j2":
        return
    env = _make_env(tdir)
    rendered = env.get_template(fname).render(**cluster_params_gpu_enabled)
    assert "- Name: compute" not in rendered, "CPU queue present when enable_cpu_queue=false"


@pytest.mark.parametrize("tdir,fname", _collect_templates())
def test_gpu_queue_present_when_enabled(tdir, fname, cluster_params_gpu_queue_enabled):
    """config.pcluster.j2 must contain a gpu queue with Instances: list when enable_gpu_queue=true."""
    if fname != "config.pcluster.j2":
        return
    env = _make_env(tdir)
    rendered = env.get_template(fname).render(**cluster_params_gpu_queue_enabled)
    assert "- Name: gpu" in rendered, "GPU queue absent when enable_gpu_queue=true"
    assert "p3.2xlarge" in rendered, "GPU instance type absent from GPU queue"


@pytest.mark.parametrize("tdir,fname", _collect_templates())
def test_gpu_queue_absent_when_disabled(tdir, fname, cluster_params):
    """config.pcluster.j2 must not contain a gpu queue when enable_gpu_queue=false."""
    if fname != "config.pcluster.j2":
        return
    env = _make_env(tdir)
    rendered = env.get_template(fname).render(**cluster_params)
    assert "- Name: gpu" not in rendered, "GPU queue present when enable_gpu_queue=false"


@pytest.mark.parametrize("tdir,fname", _collect_templates())
def test_multi_instance_cpu_all_types_rendered(tdir, fname, cluster_params_multi_instance_cpu):
    """config.pcluster.j2 must list all CPU types in Instances: when given a multi-type list."""
    if fname != "config.pcluster.j2":
        return
    env = _make_env(tdir)
    rendered = env.get_template(fname).render(**cluster_params_multi_instance_cpu)
    for itype in ["c8g.2xlarge", "c7g.2xlarge", "c6g.2xlarge"]:
        assert itype in rendered, f"{itype} absent from Instances: list"


@pytest.mark.parametrize("tdir,fname", _collect_templates())
def test_vars_file_derived_queue_flags(tdir, fname, cluster_params_gpu_queue_enabled):
    """vars_file.j2 must contain enable_cpu_queue and enable_gpu_queue when both queues active."""
    if fname != "vars_file.j2":
        return
    env = _make_env(tdir)
    rendered = env.get_template(fname).render(**cluster_params_gpu_queue_enabled)
    assert 'enable_cpu_queue: "true"' in rendered, "enable_cpu_queue missing from vars_file"
    assert 'enable_gpu_queue: "true"' in rendered, "enable_gpu_queue missing from vars_file"
    assert 'enable_gpu: "true"' in rendered, "enable_gpu missing from vars_file"


@pytest.mark.parametrize("tdir,fname", _collect_templates())
def test_gpu_only_cluster_renders(tdir, fname, cluster_params_gpu_enabled):
    """All templates must render when only a GPU queue is defined (no CPU queue)."""
    env = _make_env(tdir)
    rendered = env.get_template(fname).render(**cluster_params_gpu_enabled)
    assert isinstance(rendered, str)
    assert len(rendered) > 0, f"{fname} rendered empty (gpu-only cluster)"


# ---------------------------------------------------------------------------
# Template directory integrity
# ---------------------------------------------------------------------------


def test_template_dirs_all_exist():
    """Every directory in TEMPLATE_DIRS must exist and contain at least one template."""
    for tdir in TEMPLATE_DIRS:
        assert os.path.isdir(tdir), f"Template directory missing: {tdir}"
        templates = [
            f for f in os.listdir(tdir) if f.endswith((".j2", ".jinja2", ".jinja"))
        ]
        assert len(templates) > 0, f"No templates found in {tdir}"


# ---------------------------------------------------------------------------
# IAM managed policy JSON validity and size tests
# ---------------------------------------------------------------------------

_IAM_POLICY_LIMIT = 6144
_PLACEHOLDER_SUB = {
    "<AWS_REGION>": "us-east-1",
    "<AWS_ACCOUNT_ID>": "123456789012",
    "<CLUSTER_NAME>": "test-cluster",
    "<CLUSTER_OWNER>": "testuser",
    "<CLUSTER_SERIAL_NUMBER>": "test-cluster-00001220260720",
    "<CLUSTER_SERIAL_DATESTAMP>": "00001220260720",
    "<VPC_ID>": "vpc-0abc123",
    # Workstream 5's MCP Lambda policies only. Must stay in lockstep with
    # _render_policy's own substitution chain -- a placeholder present in
    # one and not the other renders a literal "<MCP_USER_POOL_ID>" into a
    # live IAM ARN, which test_iam_policy_no_unsubstituted_placeholders
    # exists to catch.
    "<MCP_USER_POOL_ID>": "us-east-1_aBcDeFgHi",
}
_POLICY_FILES = [
    "HeadNode-Compute.json_src",
    "HeadNode-Storage.json_src",
    "HeadNode-IAM.json_src",
    "ComputeNode-Base.json_src",
    "ClusterNode-Deny.json_src",
    "HeadNode-Monitoring.json_src",
]

# Policies attached to ec2_iam_role *inline* rather than as managed policies, so
# equally reachable from a shell on any node -- _setup_fsx_hydration_iam calls
# put_role_policy(RoleName=ec2_iam_role, ...). _POLICY_FILES is pinned by equality
# to the five managed policies (test_every_policy_template_is_created_and_deleted),
# so the inline one needs its own list rather than being appended there. Any grant
# that must not be reachable from an instance has to sweep both.
_INLINE_INSTANCE_POLICY_FILES = ["LustreS3HydrationPolicy.json_src"]
_INSTANCE_REACHABLE_POLICY_FILES = _POLICY_FILES + _INLINE_INSTANCE_POLICY_FILES

# Workstream 5's MCP Lambda execution-role policies -- a third category,
# neither instance-reachable nor the operator's own credentials. They need
# their own list because _POLICY_FILES is pinned by equality to the five
# managed cluster policies (test_every_policy_template_is_created_and_deleted)
# and _INSTANCE_REACHABLE_POLICY_FILES drives guards whose reasoning is
# specific to a shell on a cluster node.
#
# They are NOT exempt from the logs:DeleteLogGroup ban, and that is the
# deliberate call rather than the convenient one. The ban's rationale --
# a retained log group is the only surviving record of a failed build --
# depends on nothing about the principal being an EC2 instance; the
# instance framing is just where the original incident happened. The
# stack-mutation draft arrived granting logs:DeleteLogGroup on
# Resource: "*", which would have let the delete_cluster Lambda erase any
# cluster's logs account-wide. Removed before the file was moved into
# templates/ rather than categorized around: the toolkit never calls
# delete_log_group, upstream's only caller is the build-image path this
# toolkit does not use, and PCluster's own CloudWatch log group defaults
# to Retain -- so nothing needs it. See _BAN_APPLIES_TO below.
_MCP_LAMBDA_POLICY_FILES = [
    "MCPRouterLambda.json_src",
    "MCPReadOnlyLambda.json_src",
    "MCPFleetToggleLambda.json_src",
    "MCPStackMutation.json_src",
    "MCPClusterBuild.json_src",
    "MCPStateAccessReadOnly.json_src",
    "MCPStateAccessFleetToggle.json_src",
    "MCPStateAccessStackMutation.json_src",
    "MCPRegisterLambda.json_src",
    "MCPAuthorizerLambda.json_src",
]

# A fourth category: neither instance-reachable, nor a Lambda execution
# role, nor the operator's own. These are what an administrator grants to
# whoever deploys the transport, plus the permissions boundary every MCP
# role is created under. They are deliberately outside _BAN_APPLIES_TO --
# the boundary *denies* logs:DeleteLogGroup, and that ban reads every
# statement without looking at Effect, so a Deny would trip it. Denying an
# action is the opposite of the thing the ban exists to catch.
_MCP_DEPLOY_POLICY_FILES = [
    "MCPDeployPolicy.json_src",
    "MCPRoleBoundary.json_src",
]

# Structural guards (valid JSON, the size limit, unique Sids, placeholder
# substitution) apply to every MCP document regardless of category.
_MCP_ALL_POLICY_FILES = _MCP_LAMBDA_POLICY_FILES + _MCP_DEPLOY_POLICY_FILES

# A fifth category: the permissions boundary the head node's own role is
# created under. It is neither a grant nor a document _setup_iam deletes,
# so it cannot join _POLICY_FILES -- that list is pinned by equality to the
# managed policies and cross-asserted against pcluster_core.py's suffix
# lists, and the boundary has no suffix and outlives every cluster. It is
# instance-reachable in the sense that matters here (it caps the head node
# role), so it does sit inside _BAN_APPLIES_TO.
_CLUSTER_BOUNDARY_POLICY_FILES = ["ClusterRoleBoundary.json_src"]

# Everything the cluster-side structural guards (valid JSON, the 6,144-byte
# minified limit, statement keys, unique Sids, placeholder substitution)
# must read. The boundary is not a managed policy but IAM validates it the
# same way and _render_policy enforces the same ceiling on it.
_CLUSTER_STRUCTURAL_POLICY_FILES = _POLICY_FILES + _CLUSTER_BOUNDARY_POLICY_FILES

# The two permissions boundaries, cluster-side and MCP-side. A boundary is a
# ceiling rather than a grant, so its Allow statements are service wildcards
# that would trip any ban written against grants; what makes it safe is the
# unscoped Deny in the same document. Listed together so a guard can assert
# that pairing rather than exempt the files.
_BOUNDARY_POLICY_FILES = _CLUSTER_BOUNDARY_POLICY_FILES + ["MCPRoleBoundary.json_src"]


def _load_policy(fname):
    path = os.path.join(REPO_ROOT, "templates", fname)
    with open(path) as f:
        raw = f.read()
    for placeholder, value in _PLACEHOLDER_SUB.items():
        raw = raw.replace(placeholder, value)
    return json.loads(raw)


def _arn_matches(pattern, arn):
    """True if an IAM Resource pattern matches a concrete ARN.

    IAM wildcards are `*` (any run of characters, including `/`) and `?`, which
    is not fnmatch semantics -- fnmatch's `*` also spans separators but its
    character-class handling differs, so translate explicitly.
    """
    import re

    regex = "".join(
        ".*" if c == "*" else "." if c == "?" else re.escape(c) for c in pattern
    )
    return re.fullmatch(regex, arn) is not None


@pytest.mark.parametrize("fname", _CLUSTER_STRUCTURAL_POLICY_FILES)
def test_iam_policy_valid_json(fname):
    """Each IAM policy template must parse as valid JSON after placeholder substitution."""
    data = _load_policy(fname)
    assert isinstance(data, dict), f"{fname}: top-level must be a JSON object"
    assert "Statement" in data, f"{fname}: missing Statement key"
    assert isinstance(data["Statement"], list), f"{fname}: Statement must be a list"
    assert len(data["Statement"]) > 0, f"{fname}: Statement list is empty"


@pytest.mark.parametrize("fname", _CLUSTER_STRUCTURAL_POLICY_FILES)
def test_iam_policy_under_size_limit(fname):
    """Each IAM managed policy must stay under the 6,144-byte IAM limit when minified."""
    data = _load_policy(fname)
    minified = json.dumps(data, separators=(",", ":"))
    size = len(minified.encode("utf-8"))
    assert (
        size <= _IAM_POLICY_LIMIT
    ), f"{fname}: minified size {size} bytes exceeds IAM limit of {_IAM_POLICY_LIMIT}"


@pytest.mark.parametrize("fname", _CLUSTER_STRUCTURAL_POLICY_FILES + ["OperatorPolicy.json_src"])
def test_iam_policy_statement_keys_are_valid(fname):
    """Only real IAM statement keys are allowed. A stray key (e.g. "Comment")
    makes AWS reject the whole policy at CreatePolicy time."""
    _VALID = {"Sid", "Effect", "Action", "NotAction", "Resource", "NotResource", "Condition", "Principal"}
    for stmt in _load_policy(fname)["Statement"]:
        bad = set(stmt) - _VALID
        assert not bad, f"{fname}: statement {stmt.get('Sid')} has invalid keys: {sorted(bad)}"


@pytest.mark.parametrize("fname", _CLUSTER_STRUCTURAL_POLICY_FILES + ["OperatorPolicy.json_src"])
def test_iam_policy_sids_unique(fname):
    """Duplicate Sids within one policy are rejected by IAM."""
    sids = [s["Sid"] for s in _load_policy(fname)["Statement"] if "Sid" in s]
    dupes = {s for s in sids if sids.count(s) > 1}
    assert not dupes, f"{fname}: duplicate Sids: {sorted(dupes)}"


def test_head_node_iam_policy_omits_put_role_policy():
    """iam:PutRolePolicy on the head node completes a privilege-escalation chain
    to account admin (create role -> inline Action:* -> pass to EC2 -> read IMDS
    credentials). The only put_role_policy call in the toolkit runs under
    operator credentials. See the standing constraint in CLAUDE.md."""
    data = _load_policy("HeadNode-IAM.json_src")
    actions = [
        a
        for stmt in data["Statement"]
        for a in (stmt["Action"] if isinstance(stmt["Action"], list) else [stmt["Action"]])
    ]
    assert "iam:PutRolePolicy" not in actions
    assert "iam:CreatePolicyVersion" not in actions


def test_operator_policy_omits_policy_version_management():
    """templates/CLAUDE.md states these are intentionally omitted from
    OperatorPolicy, and until now only HeadNode-IAM was checked.

    The reason is a privilege-escalation path: `iam:CreatePolicyVersion` on
    `pclustermaker-policy-*` lets the holder rewrite any cluster's head-node
    policy in place to `Action:"*" Resource:"*"`. Nothing in the toolkit
    needs it there -- `_setup_iam` only ever calls create_policy and
    delete_policy.

    MCPDeployPolicy *does* grant these, and that is not a contradiction:
    they are scoped to `pclustermaker-mcp-policy-*`, they are what makes
    `--setup-infra` converge on a changed document, and every role those
    policies attach to is created under a permissions boundary that caps
    what they can confer. templates/CLAUDE.local.md names that exact
    mitigation -- "gate it behind an iam:PermissionsBoundary condition".
    The head node role now has such a boundary, but the compute and login
    node roles are PCluster's own and cannot, so the omission stands.

    Only Allow statements are read. OperatorPolicy gained a Deny of these
    same three actions on pclustermaker-cluster-boundary -- an operator who
    can version the boundary they are bounded by does not have one -- and a
    flat action set cannot tell that apart from the grant this bans. The
    Deny is asserted for separately, below.
    """
    data = _load_policy("OperatorPolicy.json_src")
    actions = {
        a
        for stmt in data["Statement"]
        if stmt["Effect"] == "Allow"
        for a in (stmt["Action"] if isinstance(stmt["Action"], list)
                  else [stmt["Action"]])
    }
    for banned in ("iam:CreatePolicyVersion", "iam:DeletePolicyVersion",
                   "iam:SetDefaultPolicyVersion"):
        assert banned not in actions, (
            f"OperatorPolicy grants {banned}; it can now rewrite any "
            f"cluster's head-node policy in place"
        )


class TestNoInstancePolicyCanDeleteALogGroup:
    """logs:DeleteLogGroup must not be reachable from any instance in the cluster.

    ComputeNode-Base shipped it in LogsWrite on /aws/parallelcluster/* and
    /parallelcluster/* -- account-wide, not <CLUSTER_NAME>-scoped like the
    LogsRead statement directly below it -- and that policy is attached to the
    head node role *and* to every compute queue via AdditionalIamPolicies, so
    any Slurm job could erase any cluster's log group. A cluster's log group is
    the only surviving record of a failed build: cfn-init captures stdout only,
    node stderr reaches no stream at all, and every bootstrap failure documented
    in CLAUDE.md was diagnosed from those logs or off the live node. The
    retained-log-group bullet in CLAUDE.md exists to keep them, which this
    grant contradicted.

    Nothing needs it. The toolkit never calls delete_log_group; upstream's only
    caller is imagebuilder (`pcluster/models/imagebuilder.py`), a build-image
    code path this toolkit does not use, and upstream's own node policy grants
    logs:CreateLogStream and logs:PutLogEvents and nothing else
    (`cdk_builder_utils.py`). The operator purges groups by hand under their own
    credentials; OperatorPolicy is therefore not covered here.
    """

    _BANNED = "logs:DeleteLogGroup"

    # Every policy whose principal must never be able to erase a log group.
    # Instance-reachable ones plus Workstream 5's MCP Lambda execution
    # roles: the rationale (a retained log group is the only surviving
    # record of a failed build) turns on what the log group is worth, not
    # on whether the principal is an EC2 instance. OperatorPolicy stays
    # out -- purging by hand under operator credentials is exactly what
    # the retained-log-group bullet expects.
    _BAN_APPLIES_TO = (
        _INSTANCE_REACHABLE_POLICY_FILES
        + _MCP_LAMBDA_POLICY_FILES
        + _MCP_DEPLOY_POLICY_FILES
        + _CLUSTER_BOUNDARY_POLICY_FILES
    )

    @staticmethod
    def _denied_outright(policy, banned):
        """True if the document itself denies `banned` on every resource.

        IAM resolves an explicit Deny ahead of any Allow, so a permissions
        boundary whose ceiling is `logs:*` and which then denies
        logs:DeleteLogGroup on Resource "*" cannot confer the action -- and a
        ban that read the ceiling alone would have to exclude every boundary
        file, which is how MCPRoleBoundary escaped this check in the first
        place. The Deny must be unscoped: one naming a single log group leaves
        the wildcard Allow standing everywhere else.
        """
        for stmt in policy["Statement"]:
            if stmt["Effect"] != "Deny":
                continue
            actions = (
                stmt["Action"] if isinstance(stmt["Action"], list) else [stmt["Action"]]
            )
            if not any(fnmatch.fnmatch(banned, a) for a in actions):
                continue
            resources = (
                stmt["Resource"] if isinstance(stmt["Resource"], list)
                else [stmt["Resource"]]
            )
            if "*" in resources:
                return True
        return False

    @pytest.mark.parametrize("fname", _BAN_APPLIES_TO)
    def test_no_policy_on_an_instance_grants_it(self, fname):
        """Only Allow statements are read, and that is what widened the list.

        The ban used to read every statement regardless of Effect, which is
        why _MCP_DEPLOY_POLICY_FILES had to be excluded from it: MCPRoleBoundary
        *denies* logs:DeleteLogGroup, and a Deny of the banned action tripped a
        check written to catch a grant of it. Excluding whole files to work
        around that is the wrong lever -- it also stops the ban seeing an Allow
        that later lands in the same file, which is the thing it exists for.
        ClusterNode-Deny and ClusterRoleBoundary forced the question again, and
        both are documents an instance carries, so exclusion was not available:
        a cluster policy outside this ban is exactly the hole
        test_every_policy_template_is_covered_by_this_ban was written after.
        Reading Effect instead lets every policy document in the repo sit
        inside the ban, MCPDeployPolicy included, which nothing checked before.
        """
        policy = _load_policy(fname)
        if self._denied_outright(policy, self._BANNED):
            return
        for stmt in policy["Statement"]:
            if stmt["Effect"] != "Allow":
                continue
            actions = (
                stmt["Action"] if isinstance(stmt["Action"], list) else [stmt["Action"]]
            )
            # fnmatch, not equality: "logs:*" and "logs:Delete*" both grant the
            # action while an exact-string ban reads clean.
            matched = [a for a in actions if fnmatch.fnmatch(self._BANNED, a)]
            assert not matched, (
                f"{fname}: statement {stmt.get('Sid')} grants {self._BANNED} "
                f"via {matched} on {stmt['Resource']}"
            )

    # (document, why it must still fail). Each is a shape one of the two new
    # filters would wave through if it were slightly wrong.
    _MUST_STILL_FAIL = {
        "a plain Allow with no Deny anywhere": {
            "Version": "2012-10-17",
            "Statement": [
                {"Sid": "Grant", "Action": ["logs:DeleteLogGroup"],
                 "Effect": "Allow", "Resource": "*"},
            ],
        },
        "an Allow reached only through a wildcard": {
            "Version": "2012-10-17",
            "Statement": [
                {"Sid": "Grant", "Action": ["logs:*"],
                 "Effect": "Allow", "Resource": "*"},
            ],
        },
        "a Deny scoped to one log group, so the wildcard Allow stands elsewhere": {
            "Version": "2012-10-17",
            "Statement": [
                {"Sid": "Grant", "Action": ["logs:*"],
                 "Effect": "Allow", "Resource": "*"},
                {"Sid": "NarrowDeny", "Action": ["logs:DeleteLogGroup"],
                 "Effect": "Deny",
                 "Resource": ["arn:aws:logs:*:123456789012:log-group:/x:*"]},
            ],
        },
        "a Deny of a neighboring action only": {
            "Version": "2012-10-17",
            "Statement": [
                {"Sid": "Grant", "Action": ["logs:*"],
                 "Effect": "Allow", "Resource": "*"},
                {"Sid": "WrongDeny", "Action": ["logs:DeleteLogStream"],
                 "Effect": "Deny", "Resource": "*"},
            ],
        },
    }

    @pytest.mark.parametrize("case", sorted(_MUST_STILL_FAIL))
    def test_the_ban_still_fails_a_real_grant(self, monkeypatch, case):
        """Discrimination guard, driving the real assertion rather than a copy.

        Both filters this class gained are ways for the ban to read nothing and
        pass: `Effect != "Allow"` compared against a string no statement carries
        skips every statement, and a _denied_outright that returns True too
        eagerly returns before reading any of them. Neither shows up in any
        other test here -- every real document passes the ban, so a ban that
        checks nothing passes it too. Recomputing the comparison in this test
        would have the same blind spot, so the real method is called with
        _load_policy monkeypatched to hand it the document instead.
        """
        monkeypatch.setattr(
            sys.modules[__name__], "_load_policy",
            lambda _fname: self._MUST_STILL_FAIL[case],
        )
        with pytest.raises(AssertionError):
            self.test_no_policy_on_an_instance_grants_it("synthetic.json_src")

    def test_the_ban_passes_a_document_the_deny_actually_covers(self):
        """The other direction: the filters must not have been made so strict
        that a legitimately capped boundary now fails. Without this, deleting
        _denied_outright entirely reads as a tightening rather than the change
        that breaks every real boundary file."""
        assert self._denied_outright(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {"Sid": "Ceiling", "Action": ["logs:*"],
                     "Effect": "Allow", "Resource": "*"},
                    {"Sid": "Deny", "Action": ["logs:DeleteLogGroup"],
                     "Effect": "Deny", "Resource": "*"},
                ],
            },
            self._BANNED,
        )
        # A wildcard Deny covers the action too, which is why the Deny side is
        # matched with fnmatch and not equality. Equality here reads clean and
        # then reports a correctly capped boundary as granting the action.
        assert self._denied_outright(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {"Sid": "Ceiling", "Action": ["logs:*"],
                     "Effect": "Allow", "Resource": "*"},
                    {"Sid": "Deny", "Action": ["logs:Delete*"],
                     "Effect": "Deny", "Resource": "*"},
                ],
            },
            self._BANNED,
        )

    @pytest.mark.parametrize("fname", _BOUNDARY_POLICY_FILES)
    def test_the_deny_is_what_lets_a_boundary_carry_a_logs_wildcard(self, fname):
        """Vacuity guard for _denied_outright.

        Both boundaries have `logs:*` in their ceiling, so the only reason
        either passes the ban is the unscoped Deny beside it. Dropping that
        Deny leaves a document whose ceiling permits erasing any log group in
        the account, and the early return above would hide it -- so assert the
        Deny directly rather than trusting the ban to notice its absence.
        """
        policy = _load_policy(fname)
        assert self._denied_outright(policy, self._BANNED), (
            f"{fname}: no unscoped Deny covers {self._BANNED}"
        )
        ceiling = [
            stmt.get("Sid")
            for stmt in policy["Statement"]
            if stmt["Effect"] == "Allow"
            and any(
                fnmatch.fnmatch(self._BANNED, a)
                for a in (
                    stmt["Action"] if isinstance(stmt["Action"], list)
                    else [stmt["Action"]]
                )
            )
        ]
        assert ceiling, (
            f"{fname}: nothing in the ceiling matches {self._BANNED} any more, so "
            f"this file no longer exercises the Deny-beats-Allow path and belongs "
            f"in the plain ban instead"
        )

    def test_every_policy_template_is_covered_by_this_ban(self):
        """A policy file in neither list is a file this ban never reads.

        LustreS3HydrationPolicy.json_src was exactly that: it is attached to
        ec2_iam_role by put_role_policy, so a job on any node carries it, but it
        is not one of the five managed policies and _POLICY_FILES is pinned to
        those by equality. Adding logs:DeleteLogGroup to it passed the whole
        suite. OperatorPolicy is deliberately excluded -- it is the operator's
        own credentials, not an instance's, and purging log groups by hand is
        what the retained-log-group bullet in CLAUDE.md expects of it.
        """
        on_disk = {
            f for f in os.listdir(os.path.join(REPO_ROOT, "templates"))
            if f.endswith(".json_src")
        }
        classified = (
            set(_INSTANCE_REACHABLE_POLICY_FILES)
            | set(_MCP_LAMBDA_POLICY_FILES)
            | set(_MCP_DEPLOY_POLICY_FILES)
            | set(_CLUSTER_BOUNDARY_POLICY_FILES)
            | {"OperatorPolicy.json_src"}
        )
        assert on_disk == classified, (
            f"unclassified policy templates: {sorted(on_disk - classified)}; "
            f"listed but absent: {sorted(classified - on_disk)}"
        )

    def test_the_log_shipping_actions_are_still_granted(self):
        """Vacuity guard: the fix is removing one action, not the statement."""
        actions = {
            a
            for stmt in _load_policy("ComputeNode-Base.json_src")["Statement"]
            for a in (
                stmt["Action"] if isinstance(stmt["Action"], list) else [stmt["Action"]]
            )
        }
        for needed in (
            "logs:CreateLogGroup",
            "logs:CreateLogStream",
            "logs:PutLogEvents",
            "logs:PutRetentionPolicy",
        ):
            assert needed in actions, f"ComputeNode-Base.json_src: lost {needed}"


class TestAttachRolePolicyCannotReachTheOperatorPolicy:
    """The head node may attach only this cluster's own managed policies.

    The condition shipped as `policy/parallelcluster-*`, which matches
    `parallelcluster-operator-pclustermaker` (`_POLICY_NAME` in
    generate_operator_policy.py). Since IAMAttachDetachPolicy's Resource list
    includes the head node's own role, anyone with a shell on the head node
    (including via Slurm job submission) could attach the operator policy to
    that role and gain iam:PutRolePolicy on pclustermaker-role-* plus
    secretsmanager:GetSecretValue on parallelcluster/* -- every cluster's SSH
    key in the account. The operator-side twin (OperatorPolicy's
    IAMAttachDetachClusterPolicies) is scoped correctly and pinned by
    tests/test_operator_policy.py; this statement was pinned by nothing.

    Upstream PCluster grants its own head node exactly one IAM action,
    iam:PassRole (cdk_builder_utils.py), so scoping this to the toolkit's own
    per-serial policies cannot regress a stock cluster.
    """

    _SID = "IAMAttachDetachPolicy"
    _ACCT = _PLACEHOLDER_SUB["<AWS_ACCOUNT_ID>"]
    _SERIAL = _PLACEHOLDER_SUB["<CLUSTER_SERIAL_NUMBER>"]
    _SUFFIXES = [
        "-HeadNode-Compute",
        "-HeadNode-Storage",
        "-HeadNode-IAM",
        "-ComputeNode-Base",
        "-HeadNode-Monitoring",
    ]

    def _patterns(self):
        stmt = [
            s
            for s in _load_policy("HeadNode-IAM.json_src")["Statement"]
            if s.get("Sid") == self._SID
        ]
        assert len(stmt) == 1, f"expected exactly one {self._SID} statement"
        cond = stmt[0]["Condition"]["StringLike"]["iam:PolicyARN"]
        return [cond] if isinstance(cond, str) else cond

    def _allows(self, arn):
        return any(_arn_matches(p, arn) for p in self._patterns())

    def _toolkit_arn(self, suffix):
        return f"arn:aws:iam::{self._ACCT}:policy/pclustermaker-policy-{self._SERIAL}{suffix}"

    @pytest.mark.parametrize("suffix", _SUFFIXES)
    def test_every_toolkit_policy_is_still_attachable(self, suffix):
        """_setup_iam attaches all five by ARN; over-narrowing breaks every build."""
        assert self._allows(self._toolkit_arn(suffix))

    def test_the_operator_policy_is_not_attachable(self):
        arn = f"arn:aws:iam::{self._ACCT}:policy/parallelcluster-operator-pclustermaker"
        assert not self._allows(arn)

    @pytest.mark.parametrize(
        "arn",
        [
            "arn:aws:iam::aws:policy/AdministratorAccess",
            "arn:aws:iam::aws:policy/PowerUserAccess",
            "arn:aws:iam::aws:policy/IAMFullAccess",
        ],
    )
    def test_no_aws_managed_admin_policy_is_attachable(self, arn):
        assert not self._allows(arn)

    def test_another_clusters_policy_is_not_attachable(self):
        """Serial-scoped, so cluster A cannot touch cluster B's policies."""
        other = self._toolkit_arn("-HeadNode-IAM").replace(self._SERIAL, "other-99999920260101")
        assert not self._allows(other)

    def test_a_cluster_named_operator_cannot_reach_the_operator_policy(self):
        """A `parallelcluster-<CLUSTER_NAME>-*` pattern would readmit the operator
        policy for a cluster named `operator`; _validate_cluster_name allows that
        name. The condition must not be scoped on the cluster name."""
        for pattern in self._patterns():
            assert "parallelcluster-operator" not in pattern.replace(
                "<CLUSTER_NAME>", "operator"
            )

    def test_the_condition_is_not_an_unscoped_wildcard(self):
        """Guards the exact string that shipped, and any equivalent widening."""
        for pattern in self._patterns():
            assert self._SERIAL in pattern, (
                f"{self._SID} iam:PolicyARN must be scoped to the cluster serial; "
                f"got {pattern!r}"
            )


@pytest.mark.parametrize(
    "fname",
    ["HeadNode-Storage.json_src", "HeadNode-Monitoring.json_src", "ComputeNode-Base.json_src"],
)
def test_cfn_resource_arns_use_bare_cluster_name(fname):
    """PCluster's CloudFormation stack name IS the cluster name (Cluster.stack_name
    returns self.name) — not parallelcluster-<cluster_name>. Every statement that
    scopes a CloudFormation ARN must include the bare-cluster-name form, or the
    call is silently denied."""
    cluster = _PLACEHOLDER_SUB["<CLUSTER_NAME>"]
    for stmt in _load_policy(fname)["Statement"]:
        resources = stmt["Resource"]
        if isinstance(resources, str):
            resources = [resources]
        cfn = [r for r in resources if ":cloudformation:" in r]
        if not cfn:
            continue
        assert any(f":stack/{cluster}/" in r or f":stack/{cluster}-" in r for r in cfn), (
            f"{fname}: {stmt.get('Sid')} scopes CloudFormation ARNs but none match the "
            f"real stack name {cluster!r}: {cfn}"
        )


def test_compute_node_can_read_regional_pcluster_bucket():
    """PCluster's node bootstrap fetches artifacts from <region>-aws-parallelcluster.
    Without this grant the compute fleet fails to bootstrap."""
    resources = [
        r
        for stmt in _load_policy("ComputeNode-Base.json_src")["Statement"]
        for r in (stmt["Resource"] if isinstance(stmt["Resource"], list) else [stmt["Resource"]])
    ]
    assert any("aws-parallelcluster" in r and r.startswith("arn:aws:s3:::") for r in resources)


def _actions(fname):
    return {
        a
        for stmt in _load_policy(fname)["Statement"]
        for a in (stmt["Action"] if isinstance(stmt["Action"], list) else [stmt["Action"]])
    }


def test_compute_node_can_query_slurm_table_indexes():
    """dynamodb:Query against a GSI is authorized on the index ARN, not the table
    ARN. Without table/.../index/* the slurm resume/suspend daemons get
    AccessDenied and the compute fleet never scales."""
    resources = [
        r
        for stmt in _load_policy("ComputeNode-Base.json_src")["Statement"]
        for r in (stmt["Resource"] if isinstance(stmt["Resource"], list) else [stmt["Resource"]])
    ]
    assert "dynamodb:Query" in _actions("ComputeNode-Base.json_src")
    assert any(r.endswith("/index/*") and ":dynamodb:" in r for r in resources)


@pytest.mark.parametrize(
    "fname,action",
    [
        ("ComputeNode-Base.json_src", "cloudformation:DescribeStackResource"),
        ("ComputeNode-Base.json_src", "ec2:DescribeInstanceAttribute"),
        ("HeadNode-Storage.json_src", "cloudformation:SignalResource"),
        ("HeadNode-Compute.json_src", "ec2:DetachVolume"),
        ("HeadNode-Compute.json_src", "ec2:GetConsoleOutput"),
        ("OperatorPolicy.json_src", "iam:ListEntitiesForPolicy"),
        ("OperatorPolicy.json_src", "iam:CreateInstanceProfile"),
        ("OperatorPolicy.json_src", "iam:AddRoleToInstanceProfile"),
    ],
)
def test_required_upstream_action_present(fname, action):
    """Each of these is exercised by upstream PCluster or by the Ansible modules
    this toolkit drives; dropping one produces a runtime AccessDenied that no
    other test would catch."""
    assert action in _actions(fname), f"{fname}: missing {action}"


# The load-bearing actions of each policy, grouped by the capability they buy.
# The wildcard-mutation ratchet only fires on grants *added* at Resource: "*",
# never on one taken away, and the size/JSON/ARN-scoping tests all pass just as
# happily with an action deleted or misspelled. This manifest is the mirror
# image: every capability the cluster cannot boot or scale without.
#
# Keep it to actions whose absence is a hard failure, not a degraded mode. Add a
# group when a new capability becomes load-bearing; do not add an action here
# just because it happens to be present.
_REQUIRED_ACTIONS = {
    "HeadNode-IAM.json_src": {
        # Without PassRole the head node cannot hand the compute-node role to
        # RunInstances/CreateFleet and the fleet never launches.
        "pass the compute fleet its instance role": ["iam:PassRole"],
        # The PCluster head node daemon calls ListRoles at startup to locate
        # its own role. See the IAMListGlobal standing constraint.
        "daemon startup role lookup": ["iam:ListRoles"],
        "create and attach compute fleet roles": [
            "iam:CreateRole",
            "iam:DeleteRole",
            "iam:AttachRolePolicy",
            "iam:DetachRolePolicy",
        ],
        "instance profile lifecycle": [
            "iam:CreateInstanceProfile",
            "iam:DeleteInstanceProfile",
            "iam:AddRoleToInstanceProfile",
            "iam:RemoveRoleFromInstanceProfile",
        ],
    },
    "HeadNode-Compute.json_src": {
        "launch and terminate compute nodes": [
            "ec2:RunInstances",
            "ec2:TerminateInstances",
            "ec2:CreateFleet",
        ],
        "launch template lifecycle": [
            "ec2:CreateLaunchTemplate",
            "ec2:ModifyLaunchTemplate",
            "ec2:DeleteLaunchTemplate",
        ],
        # The slurm resume/suspend daemons key node state off these tables.
        "slurm node state tables": [
            "dynamodb:CreateTable",
            "dynamodb:DescribeTable",
            "dynamodb:DeleteTable",
        ],
    },
    "ComputeNode-Base.json_src": {
        # Node bootstrap pulls cookbook and node-package artifacts from S3.
        "bootstrap artifact download": ["s3:GetObject"],
        "cloudwatch log shipping": [
            "logs:CreateLogStream",
            "logs:PutLogEvents",
            "logs:DescribeLogGroups",
        ],
        "slurm node state read/write": [
            "dynamodb:GetItem",
            "dynamodb:PutItem",
            "dynamodb:UpdateItem",
            "dynamodb:Query",
        ],
        "cluster bootstrap signaling": [
            "cloudformation:DescribeStackResource",
            "sqs:SendMessage",
        ],
        "ssm session manager agent": [
            "ssm:UpdateInstanceInformation",
            "ssmmessages:CreateControlChannel",
        ],
    },
    "HeadNode-Storage.json_src": {
        "cluster stack lifecycle": [
            "cloudformation:CreateStack",
            "cloudformation:DeleteStack",
            "cloudformation:DescribeStacks",
            "cloudformation:SignalResource",
        ],
        "cluster bucket read/write": [
            "s3:GetObject",
            "s3:PutObject",
            "s3:ListBucket",
        ],
        # Slurm resolves compute node hostnames through the cluster's private
        # hosted zone; without ChangeResourceRecordSets nodes never register.
        "compute node dns registration": ["route53:ChangeResourceRecordSets"],
        "shared filesystem lifecycle": [
            "elasticfilesystem:CreateMountTarget",
            "fsx:CreateFileSystem",
            "fsx:DescribeFileSystems",
        ],
    },
    "HeadNode-Monitoring.json_src": {
        # The Grafana admin password is read back from SSM by the installer.
        "grafana admin password": ["ssm:PutParameter", "ssm:GetParameter"],
        "dashboard data sources": [
            "cloudformation:DescribeStacks",
            "ec2:DescribeInstances",
            "logs:FilterLogEvents",
        ],
    },
    "OperatorPolicy.json_src": {
        "managed policy lifecycle": ["iam:CreatePolicy", "iam:DeletePolicy"],
        "cluster role lifecycle": [
            "iam:CreateRole",
            "iam:DeleteRole",
            "iam:PassRole",
            "iam:AttachRolePolicy",
        ],
        # rotate_cluster_key.py and the create/teardown SSH key paths.
        # PutSecretValue is the write half of rotation (rotate_cluster_key.py
        # calls it to store the new private key); without it a rotation imports
        # the new keypair into EC2 and then cannot record the matching private
        # key, leaving the secret holding a key that no longer opens the cluster.
        "ssh key storage": [
            "ec2:ImportKeyPair",
            "ec2:DeleteKeyPair",
            "secretsmanager:CreateSecret",
            "secretsmanager:PutSecretValue",
            "secretsmanager:GetSecretValue",
            "secretsmanager:DeleteSecret",
        ],
        # create_pcluster.yml writes the Grafana admin password here and
        # delete_pcluster.yml deletes it; the operator prints a get-parameter
        # command for the user.
        "grafana admin password lifecycle": [
            "ssm:PutParameter",
            "ssm:GetParameter",
            "ssm:DeleteParameter",
        ],
        # Zone lifecycle belongs to the operator, not the head node.
        "hosted zone lifecycle": [
            "route53:CreateHostedZone",
            "route53:DeleteHostedZone",
        ],
        "cost reporting": ["ce:GetCostAndUsage", "pricing:GetProducts"],
    },
}


@pytest.mark.parametrize(
    "fname,capability,action",
    [
        (fname, capability, action)
        for fname, groups in _REQUIRED_ACTIONS.items()
        for capability, actions in groups.items()
        for action in actions
    ],
)
def test_required_action_manifest(fname, capability, action):
    """A policy that loses or misspells a required action fails at runtime with
    an opaque AccessDenied, while every other IAM test in this file stays green:
    the JSON is still valid, the size is still under the limit, the ARNs are
    still scoped, and the wildcard ratchet only watches for grants being added.
    Dropping iam:PassRole from HeadNode-IAM broke cluster creation and survived
    the whole suite."""
    assert action in _actions(fname), (
        f"{fname}: missing {action} — the policy can no longer {capability}"
    )


# Actions that must be granted by a *specific* statement, not merely present
# somewhere in the policy. HeadNode-IAM grants iam:PassRole twice — once for
# roles (IAMPassRole) and once for instance profiles
# (IAMPassRoleInstanceProfile) — under different resources and conditions, so
# the set-based manifest above still passes when either one loses the action:
# the other keeps it in the set. Dropping IAMPassRole breaks the compute fleet
# role handoff; dropping IAMPassRoleInstanceProfile breaks the instance profile
# handoff. Each has to be pinned to its Sid to be checked at all.
_REQUIRED_SID_ACTIONS = [
    ("HeadNode-IAM.json_src", "IAMPassRole", "iam:PassRole"),
    ("HeadNode-IAM.json_src", "IAMPassRoleInstanceProfile", "iam:PassRole"),
]


@pytest.mark.parametrize("fname,sid,action", _REQUIRED_SID_ACTIONS)
def test_required_action_is_granted_by_its_own_statement(fname, sid, action):
    statements = [s for s in _load_policy(fname)["Statement"] if s.get("Sid") == sid]
    assert statements, f"{fname}: statement {sid} is gone"
    for stmt in statements:
        actions = stmt["Action"] if isinstance(stmt["Action"], list) else [stmt["Action"]]
        assert action in actions, (
            f"{fname}: {sid} no longer grants {action}; another statement may "
            f"still grant it under different resources or conditions, which is "
            f"not the same permission"
        )


def test_required_actions_are_not_stale():
    """A manifest entry for an action a policy no longer needs is worse than no
    entry: it forces the grant to stay. Every action listed above must still be
    present, so an intentional removal fails here and has to be de-listed
    deliberately rather than silently re-added to satisfy the test."""
    for fname in _REQUIRED_ACTIONS:
        assert fname in _POLICY_FILES + ["OperatorPolicy.json_src"], (
            f"{fname} is in the required-action manifest but is not a policy template"
        )


def test_describe_log_groups_is_granted_on_wildcard_resource():
    """logs:DescribeLogGroups carries no log group ARN at the IAM level, so any
    scoped resource silently denies every call and the compute fleet loses log
    discovery. Re-scoping it to a real-looking log-group ARN survived the whole
    suite — the wildcard ratchet only fires on grants being *added* at
    Resource: "*", never on one being taken away. See the standing constraint in
    CLAUDE.md: do not narrow it."""
    for fname in (
        "ComputeNode-Base.json_src",
        "HeadNode-Monitoring.json_src",
        "OperatorPolicy.json_src",
    ):
        granting = [
            stmt
            for stmt in _load_policy(fname)["Statement"]
            if "logs:DescribeLogGroups"
            in (stmt["Action"] if isinstance(stmt["Action"], list) else [stmt["Action"]])
        ]
        assert granting, f"{fname}: logs:DescribeLogGroups is not granted at all"
        assert any(stmt["Resource"] == "*" for stmt in granting), (
            f"{fname}: logs:DescribeLogGroups is granted only on scoped ARNs "
            f"({[s['Resource'] for s in granting]}) — the API call carries no log "
            f"group ARN, so every call is denied"
        )


def test_postinstall_writes_completion_marker(cluster_params):
    """check_pcluster.py and diagnose_pcluster.py both gate PASS on this marker.
    Without a writer the health check can never succeed on a good cluster."""
    env = _make_env(os.path.join(REPO_ROOT, "templates"))
    rendered = env.get_template("postinstall.j2").render(**cluster_params)
    marker = "/opt/parallelcluster/shared/custom_action_done"
    assert f"touch {marker}" in rendered
    assert "set -euo pipefail" in rendered.splitlines()[1]
    # The marker must be the very last action before `exit 0`. Anything executed
    # after it can fail while the marker already claims full success, so the
    # health check would report PASS on a broken node. Block terminators are not
    # commands, so they may sit between the marker and exit — the marker write is
    # gated to the head node and therefore lives inside an `if`.
    body = [l.strip() for l in rendered.splitlines() if l.strip() and not l.strip().startswith("#")]
    assert body[-1] == "exit 0", f"postinstall must end with exit 0, got {body[-1]!r}"
    commands = [l for l in body[:-1] if l.split("#")[0].strip() not in ("fi", "esac", "done", "}")]
    assert marker in commands[-1], (
        f"marker must be the last statement before exit 0, but that slot holds "
        f"{commands[-1]!r} — a post-marker command can fail after success was claimed"
    )
    # The marker means "the head node finished". /opt/parallelcluster/shared is
    # NFS-exported from the head node, so a compute node touching it would either
    # be denied by root-squash — aborting its own bootstrap under `set -e` — or
    # re-touch the head node's marker and turn it into "some node finished".
    gate = rendered.rsplit(f"touch {marker}", 1)[0].rsplit("\n\n", 1)[-1]
    assert 'NODE_TYPE" == "HeadNode"' in gate, (
        "the marker write must be gated to the head node; postinstall now runs on "
        "compute nodes too"
    )


def _postinstall_queue_hooks(rendered_config):
    """Return {queue_name: [script basenames in its OnNodeConfigured]}.

    Parsed out of the rendered cluster config rather than asserted as substrings,
    so a hook attached to the wrong queue cannot pass.
    """
    import yaml

    parsed = yaml.safe_load(rendered_config)
    hooks = {}
    for queue in parsed["Scheduling"]["SlurmQueues"]:
        action = queue.get("CustomActions", {}).get("OnNodeConfigured", {})
        scripts = (
            [s["Script"] for s in action["Sequence"]]
            if "Sequence" in action
            else [action["Script"]] if "Script" in action else []
        )
        hooks[queue["Name"]] = [s.rsplit("/", 1)[-1] for s in scripts]
    return hooks


class TestPostinstallRunsOnComputeNodes:
    """postinstall was registered only as a HeadNode OnNodeConfigured action, so
    its `ComputeFleet)` case arm was unreachable and the GPU NVMe instance-store
    mount at /local_scratch never ran on the GPU compute nodes that are the only
    instances with instance store. It is now attached to every queue."""

    def _config(self, params):
        env = _make_env(os.path.join(REPO_ROOT, "templates"))
        return env.get_template("config.pcluster.j2").render(**params)

    def test_the_cpu_queue_runs_postinstall(self, cluster_params):
        hooks = _postinstall_queue_hooks(self._config(cluster_params))
        assert cluster_params["postinstall_s3_dest"] in hooks["compute"], (
            f"the compute queue does not run postinstall: {hooks}"
        )

    def test_the_gpu_queue_runs_postinstall(self, cluster_params_gpu_queue_enabled):
        """The GPU NVMe/RAID0 /local_scratch block in postinstall.j2 is gated on
        enable_gpu and is only meaningful on these nodes."""
        hooks = _postinstall_queue_hooks(self._config(cluster_params_gpu_queue_enabled))
        assert cluster_params_gpu_queue_enabled["postinstall_s3_dest"] in hooks["gpu"], (
            f"the gpu queue does not run postinstall: {hooks}"
        )

    def test_monitoring_runs_after_postinstall_on_every_queue(
        self, cluster_params_monitoring_enabled
    ):
        """All three hooks must fire in order: the toolkit's postinstall, then the
        operator's hook, then monitoring -- the monitoring installer expects a
        configured node, and the operator's hook expects the toolkit's package and
        storage setup to have happened. A single Script key silently drops whichever
        one is not named."""
        params = dict(cluster_params_monitoring_enabled)
        params.update(
            {"enable_cpu_queue": "true", "enable_gpu_queue": "true", "enable_gpu": "true"}
        )
        hooks = _postinstall_queue_hooks(self._config(params))
        assert set(hooks) == {"compute", "gpu"}, hooks
        for queue, scripts in hooks.items():
            assert scripts == [
                params["postinstall_s3_dest"],
                params["user_postinstall_s3_dest"],
                params["monitoring_s3_dest"],
            ], f"{queue} queue hook order is wrong: {scripts}"

    def test_compute_nodes_can_read_the_postinstall_script_from_s3(self, cluster_params):
        """Registering the hook is useless if the node cannot fetch the object.
        Compute nodes carry only ComputeNode-Base, so its S3 grant must cover the
        rendered postinstall path -- otherwise every node fails to bootstrap with
        an opaque 403."""
        policy = _load_policy("ComputeNode-Base.json_src")
        key = f"{cluster_params['s3_script_path']}/{cluster_params['postinstall_s3_dest']}"
        target = f"arn:aws:s3:::{cluster_params['s3_bucketname']}/{key}"
        granting = [
            s
            for s in policy["Statement"]
            if "s3:GetObject" in s["Action"]
            and any(
                _arn_matches(pattern, target)
                for pattern in (
                    s["Resource"] if isinstance(s["Resource"], list) else [s["Resource"]]
                )
            )
        ]
        assert granting, (
            f"ComputeNode-Base does not grant s3:GetObject on {target}; compute "
            f"nodes now run postinstall and cannot fetch it"
        )

    def test_the_head_node_still_runs_postinstall(self, cluster_params):
        import yaml

        parsed = yaml.safe_load(self._config(cluster_params))
        action = parsed["HeadNode"]["CustomActions"]["OnNodeConfigured"]
        script = action["Script"] if "Script" in action else action["Sequence"][0]["Script"]
        assert script.endswith(cluster_params["postinstall_s3_dest"])


class TestPostinstallTemplateIsActuallyRendered:
    """postinstall.j2 and preinstall.j2 were never rendered or uploaded by anything
    from the v3 migration (c2673ae) until 2026-07-26. v2 templated them to
    postinstall_src; v3 redefined postinstall_src as the *operator's* hook
    (cluster_rootdir/post_install_script) and downgraded the template: task to a
    copy:, so what every node ran was the user's 5-line scripts/post-deployment.sh.
    Spack, Lmod, the package installs, and the GPU NVMe /local_scratch mount were
    all dead template. Confirmed on a live cluster: no /local_scratch and no nvtop
    on a booted g5.xlarge.

    Nothing caught it because tests/conftest.py set post_install_script to
    "templates/postinstall.j2", so postinstall_s3_dest rendered as the template's
    own basename and every hook assertion passed."""

    @pytest.mark.parametrize("template", ["preinstall.j2", "postinstall.j2"])
    def test_the_template_is_rendered_by_a_template_task(self, template):
        """Since Workstream 2 Tier 3, neither template is rendered by an
        Ansible `template:` task at all -- core_create_cluster
        (src/pcluster_core.py) renders both directly via render_template,
        parity-tested against every tests/conftest.py fixture
        (TestPreinstallPostinstallByteForByteParity) before the Ansible task
        was deleted. The property this test guards -- that the file actually
        gets rendered by *something*, not left as dead {% %} source no node
        ever runs -- now means: render_template is really called on this
        template name inside core_create_cluster, checked on the AST so a
        renamed/removed call site fails this rather than a stale string
        match, the same discipline a `copy:` task substitution first hid."""
        import ast

        with open(os.path.join(REPO_ROOT, "src", "pcluster_core.py")) as fh:
            tree = ast.parse(fh.read())

        def _names_a_literal_call(call_node):
            return any(
                isinstance(arg, ast.Constant) and arg.value == template
                for arg in call_node.args
            )

        def _contains_the_template_name(node):
            return any(
                isinstance(n, ast.Constant) and n.value == template
                for n in ast.walk(node)
            )

        found = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "id", None) != "render_template":
                continue
            # Either the template name is passed as a literal directly...
            if _names_a_literal_call(node):
                found = True
                break
        if not found:
            # ...or (this codebase's actual shape) the call is inside a `for`
            # loop whose iterable lists the template name alongside it, e.g.
            # `for _tmpl_name, _dest in (("preinstall.j2", ...), ...):
            #      render_template(dir, _tmpl_name, **ctx)`.
            for node in ast.walk(tree):
                if not isinstance(node, ast.For):
                    continue
                if not _contains_the_template_name(node.iter):
                    continue
                if any(
                    isinstance(n, ast.Call) and getattr(n.func, "id", None) == "render_template"
                    for n in ast.walk(node)
                ):
                    found = True
                    break

        assert found, (
            f"{template} is not rendered by any render_template(...) call in "
            f"src/pcluster_core.py -- it is dead template and no node ever runs it"
        )

    @pytest.mark.parametrize("stage", ["preinstall", "postinstall"])
    def test_the_rendered_output_is_what_gets_uploaded(self, stage):
        """The S3 object the cluster config points at must be the rendered file, not
        the operator's hook. This is the exact substitution the v3 migration made.

        render_and_upload_cluster_config_and_scripts pairs each local source with
        the S3 destination name it is uploaded under, so the pairing is what has
        to be read -- a check that `<stage>_s3_dest` is uploaded at all passes
        just as happily when its source is the operator's hook.
        """
        import ast
        import inspect

        src = os.path.join(REPO_ROOT, "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        import pcluster_core

        tree = ast.parse(
            inspect.getsource(
                pcluster_core.render_and_upload_cluster_config_and_scripts
            ).lstrip()
        )
        pairs = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Tuple) or len(node.elts) != 2:
                continue
            keys = [
                n.slice.value
                for n in node.elts
                if isinstance(n, ast.Subscript)
                and isinstance(n.slice, ast.Constant)
            ]
            if len(keys) == 2:
                pairs.append(tuple(keys))
        uploaded = [
            source for source, dest in pairs if dest == f"{stage}_s3_dest"
        ]
        assert uploaded, (
            f"nothing uploads {stage}_s3_dest to S3: {pairs}"
        )
        assert uploaded == [f"{stage}_rendered"], (
            f"{stage}_s3_dest is uploaded from {uploaded}, not from the rendered "
            f"template -- the nodes would run the operator's hook instead"
        )

    @pytest.mark.parametrize("stage", ["preinstall", "postinstall"])
    def test_the_toolkit_stage_is_not_the_operator_hook(self, stage):
        """vars_file.j2 must derive <stage>_s3_dest from the cluster name, not from
        pre/post_install_script. Deriving it from the operator's flag is what made
        the two indistinguishable."""
        with open(os.path.join(REPO_ROOT, "templates", "vars_file.j2")) as fh:
            body = fh.read()
        line = next(
            l for l in body.splitlines() if l.startswith(f"{stage}_s3_dest:")
        )
        assert "install_script" not in line, (
            f"{stage}_s3_dest is derived from the operator's hook flag: {line}"
        )

    def test_both_stages_run_on_every_node_type(self, cluster_params):
        """The operator's hook must not displace the toolkit's postinstall on any
        node type. Both, in that order, everywhere."""
        params = dict(cluster_params)
        params.update({"enable_cpu_queue": "true", "enable_gpu_queue": "true"})
        parsed = yaml.safe_load(
            _make_env(os.path.join(REPO_ROOT, "templates"))
            .get_template("config.pcluster.j2")
            .render(**params)
        )
        surfaces = {"HeadNode": parsed["HeadNode"]["CustomActions"]}
        for queue in parsed["Scheduling"]["SlurmQueues"]:
            surfaces[queue["Name"]] = queue["CustomActions"]
        for name, actions in surfaces.items():
            action = actions["OnNodeConfigured"]
            scripts = (
                [action["Script"]]
                if "Script" in action
                else [s["Script"] for s in action["Sequence"]]
            )
            assert any(
                s.endswith(params["postinstall_s3_dest"]) for s in scripts
            ), f"{name} does not run the toolkit postinstall: {scripts}"
            assert any(
                s.endswith(params["user_postinstall_s3_dest"]) for s in scripts
            ), f"{name} does not run the operator hook: {scripts}"


_INSTANCE_STORE_MODEL = "Amazon EC2 NVMe Instance Storage"


def _run_postinstall(
    cluster_params,
    node_type,
    nvme_devices=(),
    model=_INSTANCE_STORE_MODEL,
    profile="",
    claimed_devices=(),
    held_devices=(),
    formatted_devices=(),
):
    """Execute the rendered postinstall with every external command stubbed.

    Node-type gating is a runtime property, so it is checked by running the
    script and recording what it actually did rather than by matching text.
    Stubs log to $TRACE; sudo/apt-get/dnf/git/etc. never touch the host. Both
    package managers are stubbed because both arms of the `'ubuntu' in base_os`
    branch have to be executable; which one appears in the trace is the property
    TestPackageManagersMatchTheRenderedOs asserts on, and cluster_params selects
    which arm rendered. The sudo stub logs and then swallows, so a cross-family
    call is caught by the trace, not by the exit status.
    Returns (CompletedProcess, trace, bashrc) -- bashrc is the file the alias
    block appends to, which no stub intercepts.

    nvme_devices fakes the sysfs entries an instance-store node would expose.
    node_type is written into a fake cfnconfig as cfn_node_type, which is where
    ParallelCluster actually publishes it -- there is no environment variable.
    node_type=None omits the file entirely, as a manual re-run off a
    ParallelCluster node would; node_type="" writes a cfnconfig with no
    cfn_node_type, which is a changed upstream contract.
    profile is substituted for /etc/profile; the default is an empty file.

    claimed_devices are the ones ParallelCluster's ephemeral_drives recipe
    already took: each gets a sysfs holders/ entry and a blkid signature, which
    is how a real LVM-claimed device presents. held_devices and formatted_devices
    set one side each, so the two halves of the check can be pinned
    independently -- a device inside an LVM physical volume has holders but no
    signature of its own, and a formatted-but-unmounted device has a signature
    and no holders. The sudo stub must forward the wrapped command's exit status
    for blkid to mean anything -- a stub that always returns 0 makes every
    device look formatted, so it cannot see either direction of the bug.
    """
    import subprocess
    import tempfile

    env = _make_env(os.path.join(REPO_ROOT, "templates"))
    rendered = env.get_template("postinstall.j2").render(**cluster_params)
    with tempfile.TemporaryDirectory() as tmp:
        home = os.path.join(tmp, "home")
        os.makedirs(home)
        bashrc = os.path.join(home, ".bashrc")
        with open(bashrc, "w"):
            pass
        rendered = rendered.replace(cluster_params["ec2_user_home"], home)

        profile_path = os.path.join(tmp, "profile")
        with open(profile_path, "w") as fh:
            fh.write(profile)
        assert "source /etc/profile" in rendered, "the /etc/profile source is gone"
        rendered = rendered.replace("source /etc/profile", f"source {profile_path}")

        # The guard restores all three options after the profile, which would turn
        # `set -e` on for the rest of this harness run -- and it cannot carry that,
        # for the same reason line 2 is discarded below: mkdir is stubbed, so the
        # head-node path's cd targets never exist. Narrow the restore to the one
        # option that is harmless here. Nothing is lost: the guard itself is
        # executed for real by TestTheProfileGuardSuspendsEveryOption, which is
        # also the only place either hazard is visible.
        guard_restore = f"source {profile_path}\nset -euo pipefail\n"
        assert guard_restore in rendered, (
            "the profile guard no longer restores the options on the line after "
            "the source"
        )
        rendered = rendered.replace(guard_restore, f"source {profile_path}\nset -u\n", 1)

        # A real instance-store device advertises this exact model string; the
        # driver filters on it so EBS-backed nvme volumes are never reformatted.
        block = os.path.join(tmp, "sys", "block")
        for dev in nvme_devices:
            os.makedirs(os.path.join(block, dev, "device"))
            with open(os.path.join(block, dev, "device", "model"), "w") as fh:
                fh.write(model + "\n")
            # An unclaimed device still has the directory, empty. Absent vs empty
            # must both read as free, since the kernel creates it either way.
            holders = os.path.join(block, dev, "holders")
            os.makedirs(holders)
            if dev in claimed_devices or dev in held_devices:
                with open(os.path.join(holders, "dm-0"), "w"):
                    pass
        find_stub = (
            f'find() {{ for _d in {" ".join(nvme_devices) or "\'\'"}; do '
            f'[[ -n "$_d" ]] && echo "{block}/$_d"; done; return 0; }}'
        )
        # The real node type arrives in /etc/parallelcluster/cfnconfig, not in the
        # environment. Substituting the path is what makes this harness able to
        # see the difference; an env-var stub passed while the template read a
        # variable that has never existed on any node.
        cfnconfig = os.path.join(tmp, "cfnconfig")
        if node_type is None:
            node_type_stub = f"rm -f {cfnconfig}"
        else:
            with open(cfnconfig, "w") as fh:
                fh.write("cfn_cluster_user=ubuntu\n")
                if node_type != "":
                    fh.write(f"cfn_node_type={node_type}\n")
            node_type_stub = ":"
        assert "/etc/parallelcluster/cfnconfig" in rendered, (
            "postinstall no longer reads the node type from cfnconfig"
        )
        rendered = rendered.replace("/etc/parallelcluster/cfnconfig", cfnconfig)

        # ParallelCluster's ephemeral_drives recipe runs before OnNodeConfigured
        # and puts every instance-store device into an LVM volume, so blkid
        # reports a signature on it. Anything not in that list is genuinely free.
        blkid_claimed = (
            " ".join(
                f"/dev/{d}" for d in list(claimed_devices) + list(formatted_devices)
            )
            or "''"
        )
        # This harness deliberately does NOT restore the rendered script's own
        # `set -euo pipefail` (line 2, discarded by the split below) the way
        # _run_preinstall does. It cannot: mkdir is stubbed, so the directories the
        # head-node path cds into never exist, and every head-node test would abort
        # at `cd "$SRC"` on what is the happy path on a real node. The cost is that
        # no test using this harness can see a `set -e` hazard -- which is why
        # TestNvmeDetectionSurvivesSetE executes the device-detection loop on its
        # own, under a real `set -e`.
        harness = f"""
        export TRACE={tmp}/trace
        : > "$TRACE"
        _log() {{ echo "$*" >> "$TRACE"; }}
        blkid() {{
            _log "blkid $*"
            for _c in {blkid_claimed}; do [[ "$_c" == "$1" ]] && return 0; done
            return 2
        }}
        # Real sudo exits with the wrapped command's status. The blkid guard in
        # the GPU block is `! sudo blkid ...`, so a stub that always returned 0
        # would report every device as already claimed and silently skip the
        # whole block -- passing the no-op tests and hiding a broken filter.
        # Forwarded for blkid only: the template also runs `sudo -E make install`
        # and `sudo su -c`, and a blanket "$@" would try to execute `-E` and the
        # unstubbed `su`.
        sudo() {{
            _log "sudo $*"
            [[ "$1" == "blkid" ]] && {{ shift; blkid "$@"; return $?; }}
            return 0
        }}
        for _c in apt-get dnf pip3 luarocks git make ln aws mdadm mkfs.xfs \
                  mount nproc chown chmod mkdir touch tee cp; do
            eval "$_c() {{ _log \\"$_c \\$*\\"; return 0; }}"
        done
        {find_stub}
        {node_type_stub}
        cd {tmp}
        """
        script = os.path.join(tmp, "postinstall.sh")
        with open(script, "w") as fh:
            fh.write(harness + "\n" + rendered.split("\n", 2)[2])
        r = subprocess.run(["bash", script], capture_output=True, cwd=tmp)
        trace = open(os.path.join(tmp, "trace")).read()
        aliases = open(bashrc).read()
    return r, trace, aliases


class TestPostinstallNodeTypeGating:
    """Running on compute nodes means every block must say where it belongs.
    /home and /opt/parallelcluster/shared are both NFS-exported from the head
    node, so a compute node writing there is a concurrent write to one file --
    or a root-squash denial that aborts the node's own bootstrap under `set -e`."""

    def test_the_script_runs_to_completion_on_a_compute_node(self, cluster_params):
        r, _, _ = _run_postinstall(cluster_params, "ComputeFleet")
        assert r.returncode == 0, (
            f"postinstall aborts on a compute node.\nstdout: {r.stdout.decode()}\n"
            f"stderr: {r.stderr.decode()}"
        )

    def test_the_script_runs_to_completion_on_the_head_node(self, cluster_params):
        r, _, _ = _run_postinstall(cluster_params, "HeadNode")
        assert r.returncode == 0, (
            f"postinstall aborts on the head node.\nstdout: {r.stdout.decode()}\n"
            f"stderr: {r.stderr.decode()}"
        )

    def test_a_compute_node_does_not_write_the_completion_marker(self, cluster_params):
        _, trace, _ = _run_postinstall(cluster_params, "ComputeFleet")
        assert "custom_action_done" not in trace, (
            "a compute node touched the head node's NFS-exported marker; it would "
            "then mean 'some node finished' instead of 'the head node finished'"
        )

    def test_the_head_node_still_writes_the_completion_marker(self, cluster_params):
        _, trace, _ = _run_postinstall(cluster_params, "HeadNode")
        assert "custom_action_done" in trace, (
            "the head node no longer writes the marker; check_pcluster.py and "
            "diagnose_pcluster.py gate PASS on it"
        )

    def test_a_compute_node_does_not_clone_lmod_or_spack(self, cluster_params):
        """Both build into NFS-exported shared storage that the head node already
        populated. Every scaling node repeating the clone is wasted boot time at
        best and a corrupted tree at worst."""
        _, trace, _ = _run_postinstall(cluster_params, "ComputeFleet")
        assert "Lmod" not in trace, f"compute node builds Lmod: {trace}"
        assert "spack.git" not in trace, f"compute node clones Spack: {trace}"

    def test_a_compute_node_does_not_append_shell_aliases(self, cluster_params):
        """$HOME is NFS-exported from the head node, so every compute node
        appending to one .bashrc is a concurrent read-modify-write on one file;
        the grep guard does not make it safe. The appends are plain `echo >>`
        with no command to stub, so this reads the resulting file."""
        _, _, aliases = _run_postinstall(cluster_params, "ComputeFleet")
        assert aliases == "", f"a compute node wrote to the shared .bashrc: {aliases!r}"

    def test_the_head_node_still_appends_shell_aliases(self, cluster_params):
        r, _, aliases = _run_postinstall(cluster_params, "HeadNode")
        assert r.returncode == 0, r.stderr.decode()
        assert "alias src=" in aliases, f"the head node wrote no aliases: {aliases!r}"

    def test_the_alias_appends_are_still_idempotent(self, cluster_params):
        """The grep guard exists so a head node rebuild does not accumulate
        duplicate aliases in a .bashrc that survives on shared storage."""
        _, _, once = _run_postinstall(cluster_params, "HeadNode")
        assert once.count("alias src=") == 1, once

    def test_an_unknown_node_type_is_a_hard_failure(self, cluster_params):
        """cfn_node_type is HeadNode, ComputeFleet, or LoginNode. A fourth value
        means the contract changed and every gate above silently stopped
        applying, which must not look like success."""
        r, _, _ = _run_postinstall(cluster_params, "SomeOtherNodeType")
        assert r.returncode != 0, "an unrecognized node type must not exit 0"
        # stdout, not stderr: cfn-init captures stdout only, so a diagnosis
        # written to stderr is invisible to whoever has to act on it.
        assert b"SomeOtherNodeType" in r.stdout

    def test_a_manual_run_outside_a_custom_action_does_not_abort(self, cluster_params):
        """No cfnconfig means this is not a ParallelCluster node, so HeadNode is
        the right default. `set -u` is on, so an unguarded expansion would abort
        on its first use. The operator re-running postinstall by hand is the case
        that matters."""
        r, trace, _ = _run_postinstall(cluster_params, None)
        assert r.returncode == 0, (
            f"postinstall aborts when cfnconfig is absent: {r.stderr.decode()}"
        )
        assert "custom_action_done" in trace, "a manual run must default to HeadNode"

    def test_the_node_type_is_read_from_cfnconfig_not_the_environment(
        self, cluster_params
    ):
        """The bug this pins cost a whole build. There is no
        PARALLELCLUSTER_NODE_TYPE on any ParallelCluster node: the node type is
        published as cfn_node_type in /etc/parallelcluster/cfnconfig.  While the
        template read the phantom variable through `${...:-HeadNode}`, every one of
        the ten compute nodes on cluster osiris took the head-node path -- cloning
        Lmod into shared NFS and running `aws s3 sync` -- until the gpu queue hit
        the 10-failure protected-mode threshold and failed the stack.

        A source assertion is what is needed here rather than a trace assertion:
        the failure mode is reading the *wrong source*, and a run whose cfnconfig
        and environment agree cannot tell the two apart."""
        env = _make_env(os.path.join(REPO_ROOT, "templates"))
        rendered = env.get_template("postinstall.j2").render(**cluster_params)
        assert "cfn_node_type" in rendered, (
            "postinstall does not read cfn_node_type; the node type must come "
            "from /etc/parallelcluster/cfnconfig"
        )
        body = rendered.split("\n", 2)[2]
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert "PARALLELCLUSTER_NODE_TYPE" not in stripped, (
                "postinstall reads PARALLELCLUSTER_NODE_TYPE, which no "
                f"ParallelCluster node ever sets: {line!r}"
            )

    def test_a_cfnconfig_without_a_node_type_is_a_hard_failure(self, cluster_params):
        """A cfnconfig that exists but defines no cfn_node_type is a changed
        upstream contract. Defaulting there is exactly the mistake that shipped:
        it turns "the source of truth is gone" into "be a head node", and every
        gate in the script silently stops applying on compute nodes."""
        r, trace, _ = _run_postinstall(cluster_params, "")
        assert r.returncode != 0, (
            "a cfnconfig with no cfn_node_type must abort, not default to HeadNode"
        )
        assert "custom_action_done" not in trace, (
            "the script ran the head-node path with no node type to justify it"
        )

    def test_sourcing_a_profile_with_an_unset_variable_does_not_abort(self, cluster_params):
        """A profile that references an unset variable on an export line is fatal
        under the `set -u` on line 2. Ubuntu's own /etc/profile is clean, so
        dropping the guard passes every test here — but a site can drop anything
        into /etc/profile.d. Originally an RPM-distro bug ($HISTCONTROL exported
        unconditionally); that base_os is gone, the hazard is not.
        Same failure the monitoring wrapper carries the same guard for.

        This harness discards the script's own `set -euo pipefail`, so it can only
        see the -u half. TestTheProfileGuardSuspendsEveryOption is what covers the
        rest, and it is the one that catches the failure that shipped."""
        r, _, _ = _run_postinstall(
            cluster_params, "HeadNode", profile='export HISTCONTROL="$HISTCONTROL"\n'
        )
        assert r.returncode == 0, (
            f"postinstall aborts when /etc/profile references an unset variable; "
            f"the profile guard is missing or no longer encloses the "
            f"source. stderr: {r.stderr.decode()}"
        )

    def test_a_gpu_compute_node_mounts_the_nvme_instance_store(
        self, cluster_params_gpu_queue_enabled
    ):
        """This block is the reason postinstall had to reach compute nodes at all:
        instance-store NVMe only exists there, and it was unreachable."""
        r, trace, _ = _run_postinstall(
            cluster_params_gpu_queue_enabled, "ComputeFleet", nvme_devices=["nvme1n1"]
        )
        assert r.returncode == 0, r.stderr.decode()
        assert "mkfs.xfs -f /dev/nvme1n1" in trace, (
            f"the GPU NVMe instance store is not formatted on a compute node: {trace}"
        )
        assert "/local_scratch" in trace, f"instance store not mounted: {trace}"
        assert "mdadm" not in trace, "a single device must not be assembled into RAID0"

    def test_a_login_node_never_claims_the_instance_store(
        self, cluster_params_gpu_queue_enabled
    ):
        """A login node's local NVMe storage has nothing to do with the GPU
        queue -- this block must not claim it just because enable_gpu is true
        cluster-wide. Same fixture and device as the ComputeFleet-formats-it
        test above; only NODE_TYPE differs."""
        r, trace, _ = _run_postinstall(
            cluster_params_gpu_queue_enabled, "LoginNode", nvme_devices=["nvme1n1"]
        )
        assert r.returncode == 0, r.stderr.decode()
        assert "mkfs" not in trace, (
            f"a login node's instance store was formatted: {trace}"
        )
        assert "mdadm" not in trace, trace

    def test_multiple_instance_store_devices_are_striped(
        self, cluster_params_gpu_queue_enabled
    ):
        """p4d/p5 expose several devices; the whole point of the block is to give
        the job one large scratch filesystem rather than N small ones."""
        r, trace, _ = _run_postinstall(
            cluster_params_gpu_queue_enabled,
            "ComputeFleet",
            nvme_devices=["nvme1n1", "nvme2n1"],
        )
        assert r.returncode == 0, r.stderr.decode()
        assert "--level=0 --raid-devices=2" in trace, f"devices not striped: {trace}"
        assert "mkfs.xfs -f /dev/md0" in trace, f"RAID array not formatted: {trace}"

    def test_a_node_with_no_instance_store_is_left_alone(
        self, cluster_params_gpu_queue_enabled
    ):
        """The sticky-bit /local_scratch directory created earlier is the fallback.
        Formatting anything here would destroy the EBS root volume."""
        r, trace, _ = _run_postinstall(
            cluster_params_gpu_queue_enabled, "ComputeFleet", nvme_devices=[]
        )
        assert r.returncode == 0, r.stderr.decode()
        assert "mkfs" not in trace, f"a node with no instance store was formatted: {trace}"
        assert "mdadm" not in trace, trace

    def test_an_ebs_backed_nvme_volume_is_never_formatted(
        self, cluster_params_gpu_queue_enabled
    ):
        """EBS volumes also appear as /dev/nvme*. The model-string filter is the
        only thing standing between this block and mkfs on the root volume."""
        r, trace, _ = _run_postinstall(
            cluster_params_gpu_queue_enabled,
            "ComputeFleet",
            nvme_devices=["nvme0n1"],
            model="Amazon Elastic Block Store",
        )
        assert r.returncode == 0, r.stderr.decode()
        assert "mkfs" not in trace, (
            f"an EBS-backed nvme device was formatted as instance store: {trace}"
        )

    def test_a_device_parallelcluster_already_claimed_is_not_reformatted(
        self, cluster_params_gpu_queue_enabled
    ):
        """This is the osiris gpu-queue failure of 2026-07-27, verbatim.

        aws-parallelcluster-environment::ephemeral_drives runs BEFORE
        OnNodeConfigured and puts every instance-store device into an LVM
        physical volume (/dev/vg.01/lv_ephemeral), formats it ext4, and mounts it
        on /scratch. mkfs.xfs on the same device then fails with "cannot open
        /dev/nvme1n1: Device or resource busy", and under `set -euo pipefail`
        that is a dead compute node -- which clustermgtd relaunches until the
        partition trips protected mode and the whole stack fails. It reached 8 of
        10 on a g4dn.xlarge before teardown.

        Asserted on the exit status AND the trace: the node must survive, and it
        must not have attempted the mkfs. Exit status alone is not enough,
        because mkfs.xfs is stubbed to return 0 here -- only the trace shows the
        call that a real kernel refuses."""
        r, trace, _ = _run_postinstall(
            cluster_params_gpu_queue_enabled,
            "ComputeFleet",
            nvme_devices=["nvme1n1"],
            claimed_devices=["nvme1n1"],
        )
        assert r.returncode == 0, (
            f"postinstall aborts on a GPU node whose instance store "
            f"ParallelCluster already mounted.\nstderr: {r.stderr.decode()}"
        )
        assert "mkfs" not in trace, (
            f"a device ParallelCluster already claimed was reformatted; on a real "
            f"node mkfs.xfs fails with EBUSY and the node dies: {trace}"
        )
        assert "mdadm" not in trace, f"a claimed device was pulled into RAID0: {trace}"

    def test_a_device_held_by_lvm_with_no_signature_of_its_own_is_skipped(
        self, cluster_params_gpu_queue_enabled
    ):
        """The holders half of the check, in isolation. A device pulled into an LVM
        physical volume reports no filesystem of its own -- blkid on the raw device
        returns 2 -- while the kernel still lists dm-0 under holders/ and mkfs
        still fails EBUSY. blkid alone cannot see this case, so this is what makes
        dropping the holders test a detectable change rather than a style edit."""
        r, trace, _ = _run_postinstall(
            cluster_params_gpu_queue_enabled,
            "ComputeFleet",
            nvme_devices=["nvme1n1"],
            held_devices=["nvme1n1"],
        )
        assert r.returncode == 0, r.stderr.decode()
        assert "mkfs" not in trace, (
            f"a device held by LVM but carrying no filesystem signature was "
            f"reformatted; only the holders check sees this: {trace}"
        )

    def test_a_formatted_but_unmounted_device_is_skipped(
        self, cluster_params_gpu_queue_enabled
    ):
        """The blkid half, in isolation. A device carrying a filesystem nobody has
        mounted has an empty holders/ directory, so the holders test passes it
        through -- but it holds data, and this block would silently destroy it.
        This is what makes dropping the blkid test detectable."""
        r, trace, _ = _run_postinstall(
            cluster_params_gpu_queue_enabled,
            "ComputeFleet",
            nvme_devices=["nvme1n1"],
            formatted_devices=["nvme1n1"],
        )
        assert r.returncode == 0, r.stderr.decode()
        assert "mkfs" not in trace, (
            f"a device with an existing filesystem was reformatted; it has no "
            f"holders, so only the blkid check sees it: {trace}"
        )

    def test_a_free_device_is_still_formatted_when_another_is_claimed(
        self, cluster_params_gpu_queue_enabled
    ):
        """The filter must exclude claimed devices, not disable the block. p4d/p5
        expose several; if ParallelCluster consumed only the first, the rest are
        still ours to use, and a single survivor takes the single-device path
        rather than a one-disk RAID0."""
        r, trace, _ = _run_postinstall(
            cluster_params_gpu_queue_enabled,
            "ComputeFleet",
            nvme_devices=["nvme1n1", "nvme2n1"],
            claimed_devices=["nvme1n1"],
        )
        assert r.returncode == 0, r.stderr.decode()
        assert "mkfs.xfs -f /dev/nvme2n1" in trace, (
            f"the free device was skipped along with the claimed one: {trace}"
        )
        assert "/dev/nvme1n1" not in trace.split("blkid /dev/nvme1n1")[-1], (
            f"the claimed device was used after being probed: {trace}"
        )
        assert "mdadm" not in trace, f"one surviving device must not be RAID0: {trace}"

    def test_a_compute_node_refreshes_the_apt_index_before_installing(
        self, cluster_params_gpu_queue_enabled
    ):
        """OnNodeStart -- and so preinstall.j2's `apt-get -y update` -- is
        head-node-only, which leaves a compute node's /var/lib/apt/lists at
        whatever the AMI shipped. `apt-get -y install htop` against a stale or
        empty index exits 100 with "Unable to locate package"; that is what
        failed osiris's gpu queue on 2026-07-27, one line past the NVMe fix.

        Order is the property, not presence: an update after the install
        refreshes nothing in time. Asserted on the execution trace by index,
        because both commands are `sudo apt-get` and a source-level grep cannot
        tell which one ran first on which node type."""
        _, trace, _ = _run_postinstall(
            cluster_params_gpu_queue_enabled, "ComputeFleet"
        )
        lines = trace.splitlines()
        updates = [i for i, ln in enumerate(lines) if "apt-get -y update" in ln]
        installs = [i for i, ln in enumerate(lines) if "install" in ln and "htop" in ln]
        assert updates, f"the compute node never refreshes the apt index: {trace}"
        assert installs, f"htop is not installed on the compute node at all: {trace}"
        assert updates[0] < installs[0], (
            f"the apt index is refreshed after the install it exists for: {trace}"
        )

    def test_nvtop_is_never_installed_on_a_compute_node(
        self, cluster_params_gpu_queue_enabled
    ):
        """nvtop is in multiverse and the operator logs into the head node, not a
        compute node. Keeping it off the compute path removes the only
        non-main package from a node that must never fail to bootstrap."""
        _, trace, _ = _run_postinstall(
            cluster_params_gpu_queue_enabled, "ComputeFleet"
        )
        assert "nvtop" not in trace, (
            f"nvtop is back on the compute node, where multiverse may be absent: {trace}"
        )
        assert "htop" in trace, f"htop is not installed at all: {trace}"

    def test_the_head_node_still_gets_nvtop_and_needs_no_refresh(
        self, cluster_params_gpu_queue_enabled
    ):
        """preinstall.j2 already refreshed the index on this node and the
        head-node package block below refreshes it again, so a third update here
        is pure bootstrap latency. nvtop belongs here and nowhere else."""
        _, trace, _ = _run_postinstall(cluster_params_gpu_queue_enabled, "HeadNode")
        lines = trace.splitlines()
        gpu_install = [
            i for i, ln in enumerate(lines) if "install" in ln and "nvtop" in ln
        ]
        assert gpu_install, f"the head node lost nvtop: {trace}"
        assert "htop" in lines[gpu_install[0]], (
            f"htop must install alongside nvtop on the head node: {trace}"
        )
        updates_before = [
            i
            for i, ln in enumerate(lines[: gpu_install[0]])
            if "apt-get -y update" in ln
        ]
        assert not updates_before, (
            f"the head node refreshes the apt index a third time: {trace}"
        )


class TestMonitoringToolsCannotFailTheNode:
    """Every other install in postinstall.j2 is fatal on purpose. The two
    monitoring installs are not, and that is the whole point of the block: a
    compute node exiting non-zero is not simply lost -- clustermgtd relaunches it
    and counts it toward the partition's 10-failure protected-mode threshold, so
    one transient mirror outage would cost the entire stack over a diagnostic
    tool that nothing in the job path imports.

    _run_postinstall cannot host this. It discards the rendered script's line 2,
    so it runs with no `set -e` at all -- it has to, because mkdir is stubbed and
    the head-node path's `cd` targets never exist -- which means a test using it
    passes whether the `|| echo` guards are there or not. Same reason
    TestNvmeDetectionSurvivesSetE exists. This class extracts the block from the
    rendered template and runs it alone under a real `set -euo pipefail` with the
    package manager failing, where the guards are the only thing between a failed
    install and a failed node.

    There are THREE arms -- apt, dnf/alinux, and dnf/rhel -- and this class saw
    only the first one for its whole life: `_block` anchored its index() on
    `sudo apt-get` and `_run` stubbed `apt-get` alone, so the two dnf arms'
    `|| echo` guards were never executed by any test and deleting them passed the
    entire suite. The arms are not copies of each other (the RHEL head node also
    installs EPEL by URL, and only apt installs nvtop), so a rendered-text
    assertion cannot stand in for running each one."""

    # (fixture name, package manager, refresh subcommand) per arm. Selecting the
    # arm by base_os rather than by text is what keeps a new OS family from
    # silently reusing another family's coverage.
    _ARMS = {
        "apt": ("cluster_params_gpu_queue_enabled", "apt-get", "apt-get -y update"),
        "dnf_alinux": ("cluster_params_al2023_gpu_queue", "dnf", "dnf -y makecache"),
        "dnf_rhel": ("cluster_params_rhel_gpu_queue", "dnf", "dnf -y makecache"),
    }

    @staticmethod
    def _block(cluster_params, refresh):
        """Extract the monitoring block for whichever arm this base_os renders.

        Anchored on the refresh command -- which is unique to this block on every
        arm -- then walked back to the enclosing `if`, rather than on a package
        manager name that only matches one family.
        """
        env = _make_env(os.path.join(REPO_ROOT, "templates"))
        rendered = env.get_template("postinstall.j2").render(**cluster_params)
        refresh_at = rendered.index(refresh)
        start = rendered.rindex('if [ "$NODE_TYPE" == "HeadNode" ]', 0, refresh_at)
        end = rendered.index("\nfi", refresh_at) + 3
        block = rendered[start:end]
        assert refresh in block and "htop" in block, (
            f"extracted the wrong region of the template:\n{block}"
        )
        return block

    def _run(self, cluster_params, node_type, manager, refresh):
        import subprocess
        import tempfile

        block = self._block(cluster_params, refresh)
        with tempfile.TemporaryDirectory() as tmp:
            trace = os.path.join(tmp, "trace")
            script = os.path.join(tmp, "block.sh")
            with open(script, "w") as fh:
                fh.write(
                    "set -euo pipefail\n"
                    f'NODE_TYPE="{node_type}"\n'
                    f'_log() {{ echo "$*" >> "{trace}"; }}\n'
                    # The package manager fails the way an unreachable mirror or a
                    # missing package does: non-zero, with the message on stderr.
                    f'{manager}() {{ _log "{manager} $*"; echo "E: failed" >&2; return 100; }}\n'
                    'sudo() { _log "sudo $*"; "$@"; }\n'
                    + block
                    + "\n"
                )
            result = subprocess.run(
                ["bash", script], capture_output=True, text=True, timeout=60
            )
            with open(trace) as fh:
                return result, fh.read()

    def _params(self, request, arm):
        fixture, manager, refresh = self._ARMS[arm]
        return request.getfixturevalue(fixture), manager, refresh

    @pytest.mark.parametrize("arm", sorted(_ARMS))
    def test_a_compute_node_survives_a_failed_index_refresh(self, request, arm):
        params, manager, refresh = self._params(request, arm)
        result, trace = self._run(params, "ComputeFleet", manager, refresh)
        assert result.returncode == 0, (
            f"[{arm}] a failed {manager} took the compute node down: "
            f"rc={result.returncode} stderr={result.stderr} trace={trace}"
        )
        assert refresh in trace, f"[{arm}] the refresh never ran: {trace}"
        assert "htop" in trace, (
            f"[{arm}] the install was skipped rather than "
            f"attempted-and-tolerated: {trace}"
        )

    @pytest.mark.parametrize("arm", sorted(_ARMS))
    def test_the_head_node_survives_a_failed_monitoring_install(self, request, arm):
        params, manager, refresh = self._params(request, arm)
        result, trace = self._run(params, "HeadNode", manager, refresh)
        assert result.returncode == 0, (
            f"[{arm}] a failed {manager} took the head node down: "
            f"rc={result.returncode} stderr={result.stderr} trace={trace}"
        )
        assert "htop" in trace, f"[{arm}] the head node install never ran: {trace}"

    def test_the_rhel_head_node_survives_a_failed_epel_install(
        self, cluster_params_rhel_gpu_queue
    ):
        """RHEL's arm has a command the other two do not: EPEL by URL, because
        nvtop is not in RHEL's own repositories. It is the first command in the
        block, so an unguarded failure there takes the node down before either
        install is even attempted -- and no apt-only test could see it."""
        _, manager, refresh = self._ARMS["dnf_rhel"]
        block = self._block(cluster_params_rhel_gpu_queue, refresh)
        assert "epel-release-latest-9.noarch.rpm" in block, (
            f"the EPEL install is gone from the RHEL arm:\n{block}"
        )
        result, trace = self._run(
            cluster_params_rhel_gpu_queue, "HeadNode", manager, refresh
        )
        assert result.returncode == 0, (
            f"a failed EPEL install took the RHEL head node down: "
            f"rc={result.returncode} stderr={result.stderr} trace={trace}"
        )
        assert "epel-release" in trace, f"EPEL was never attempted: {trace}"

    @pytest.mark.parametrize(
        "arm,wants_nvtop",
        [("apt", True), ("dnf_alinux", False), ("dnf_rhel", True)],
    )
    def test_nvtop_is_installed_on_exactly_the_arms_that_package_it(
        self, request, arm, wants_nvtop
    ):
        """Pins which arm each fixture actually renders, so the parametrization
        cannot silently collapse back onto one family. nvtop is in Ubuntu's
        multiverse and in EPEL, and absent from the al2023 core repo entirely --
        installing it there fails the node.

        Asserted per install line in the execution trace, not as `"nvtop" in
        block`: every arm's guard message names nvtop (`|| echo "WARNING:
        nvtop/htop unavailable..."`), so a whole-block substring test passes with
        the package dropped from the install line entirely. The trace also proves
        the line is reached on the head node rather than merely present."""
        params, manager, refresh = self._params(request, arm)
        result, trace = self._run(params, "HeadNode", manager, refresh)
        installs = [
            ln
            for ln in trace.splitlines()
            if ln.startswith(manager) and " install " in f"{ln} "
        ]
        assert installs, f"[{arm}] the head node installed nothing: {trace}"
        with_nvtop = [ln for ln in installs if "nvtop" in ln.split()]
        assert bool(with_nvtop) is wants_nvtop, (
            f"[{arm}] nvtop {'missing from' if wants_nvtop else 'present in'} the "
            f"head node's install: {installs}"
        )

    @pytest.mark.parametrize("arm", sorted(_ARMS))
    def test_the_harness_actually_fails_the_package_manager(self, request, arm):
        """Guards every test above against passing vacuously. If the stub ever
        returns 0 -- or stubs the wrong binary, which is how the dnf arms went
        unexercised -- they prove nothing about the guards."""
        params, manager, refresh = self._params(request, arm)
        block = self._block(params, refresh)
        assert "|| echo" in block, (
            f"[{arm}] the non-fatal guards are gone from the template:\n{block}"
        )
        result, trace = self._run(params, "ComputeFleet", manager, refresh)
        assert "E: failed" in result.stderr, (
            f"[{arm}] the {manager} stub did not fail, so the guards were never "
            "exercised"
        )
        assert manager in trace, (
            f"[{arm}] the harness stubbed {manager} but the block never called "
            f"it, so this arm was never executed: {trace}"
        )


class TestTheProfileGuardSuspendsEveryOption:
    """`set -e`, `set -u` and `pipefail` are three independent ways for a profile
    fragment to kill the node, and the guard has to suspend all three.

    It shipped as `set +u` / `set -u`, which left `pipefail` in force.  AL2023's
    /etc/profile.d/debuginfod.sh runs

        DEBUGINFOD_URLS=$(cat /dev/null "/etc/debuginfod"/*.urls 2>/dev/null | tr '\\n' ' ')

    and /etc/debuginfod/ does not exist on that image, so `cat` exits 1 while `tr`
    exits 0.  `2>/dev/null` hides the message but not the status, `pipefail`
    promotes it to the pipeline's status, and the assignment propagates it.  That
    failed the first alinux2023 build of osiris 690 ms into runpostinstall with
    nothing on stdout -- cfn-init captures stdout only, so the diagnosis came off
    the live node.  RHEL 9 passing the same line was never evidence for AL2023:
    different profile trees.

    Neither _run_postinstall nor a source-text assertion can see this.  The former
    discards the rendered script's line 2 and so runs with none of the three
    options set (it has to -- mkdir is stubbed, so the head-node path's `cd`
    targets never exist); the latter cannot tell a guard that encloses the source
    from one that no longer does.  So this extracts the real prologue from each
    rendered template and executes it under real bash against a profile carrying
    both hazards.  Same reason TestNvmeDetectionSurvivesSetE exists."""

    _TEMPLATES = ("postinstall.j2", "monitoring-post-install-wrapper.j2")

    @staticmethod
    def _prologue(template, cluster_params):
        """Everything from the shebang through the option-restore line."""
        env = _make_env(os.path.join(REPO_ROOT, "templates"))
        lines = env.get_template(template).render(**cluster_params).splitlines()

        def executable(i):
            stripped = lines[i].strip()
            return bool(stripped) and not stripped.startswith("#")

        src = next(
            (i for i, l in enumerate(lines) if l.strip() == "source /etc/profile"), None
        )
        assert src is not None, f"{template} no longer sources /etc/profile"
        restore = next(
            (i for i in range(src + 1, len(lines)) if executable(i)), None
        )
        assert restore is not None, (
            f"{template} sources /etc/profile and never runs anything else; the "
            f"option restore is gone"
        )
        prologue = lines[: restore + 1]

        # Pin the extraction: the prologue must be the guard and nothing else, in
        # this order.  A `source` that has drifted outside the suspend/restore pair
        # shows up here rather than as a confusing runtime failure.
        got = [l.strip() for l in prologue if l.strip() and not l.strip().startswith("#")]
        assert got[0] == "set -euo pipefail", (
            f"{template} does not set the options on its first executable line: {got}"
        )
        assert len(got) == 4, (
            f"{template}'s profile guard has extra executable lines in it, or the "
            f"source is no longer enclosed by it: {got}"
        )
        assert got[2] == "source /etc/profile", (
            f"{template}'s guard does not enclose the source: {got}"
        )
        return "\n".join(prologue)

    # AL2023's debuginfod.sh shape (pipefail) plus the RPM $HISTCONTROL export
    # (set -u), in one file.  The directory is deliberately absent, which is the
    # whole mechanism: `cat` fails, `tr` succeeds.
    _HOSTILE_PROFILE = (
        'DEBUGINFOD_URLS=$(cat /dev/null "{missing}"/*.urls 2>/dev/null'
        " | tr '\\n' ' ')\n"
        "export DEBUGINFOD_URLS\n"
        'export HISTCONTROL="$HISTCONTROL"\n'
    )

    def _run(self, prologue, profile_body):
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            profile = os.path.join(tmp, "profile")
            with open(profile, "w") as fh:
                fh.write(profile_body.format(missing=os.path.join(tmp, "absent")))
            script = prologue.replace("source /etc/profile", f"source {profile}")
            # $- carries e and u; pipefail is only visible in SHELLOPTS.
            script += '\necho "DOLLARDASH=$-"\necho "SHELLOPTS=$SHELLOPTS"\n'
            return subprocess.run(
                ["bash", "-c", script],
                capture_output=True,
                text=True,
                env={"PATH": os.environ["PATH"]},
            )

    @pytest.mark.parametrize("template", _TEMPLATES)
    def test_a_profile_that_fails_under_pipefail_does_not_kill_the_node(
        self, template, cluster_params
    ):
        r = self._run(
            self._prologue(template, cluster_params),
            'DEBUGINFOD_URLS=$(cat /dev/null "{missing}"/*.urls 2>/dev/null'
            " | tr '\\n' ' ')\nexport DEBUGINFOD_URLS\n",
        )
        assert r.returncode == 0, (
            f"{template} aborts on AL2023's own /etc/profile.d/debuginfod.sh; the "
            f"guard suspends set -u but leaves pipefail in force, which is the "
            f"exact failure that shipped. stderr: {r.stderr!r}"
        )

    @pytest.mark.parametrize("template", _TEMPLATES)
    def test_a_profile_that_fails_under_set_u_does_not_kill_the_node(
        self, template, cluster_params
    ):
        r = self._run(
            self._prologue(template, cluster_params),
            'export HISTCONTROL="$HISTCONTROL"\n',
        )
        assert r.returncode == 0, (
            f"{template} aborts when a profile fragment exports an unset variable. "
            f"stderr: {r.stderr!r}"
        )

    @pytest.mark.parametrize("template", _TEMPLATES)
    def test_every_option_is_restored_after_the_profile_is_sourced(
        self, template, cluster_params
    ):
        """A guard that suspends three options and restores one silently disables
        the other two for the entire rest of the script -- which on these two
        files is the whole node bootstrap."""
        r = self._run(self._prologue(template, cluster_params), self._HOSTILE_PROFILE)
        assert r.returncode == 0, f"the prologue aborted: {r.stderr!r}"
        flags = next(
            l.split("=", 1)[1] for l in r.stdout.splitlines() if l.startswith("DOLLARDASH=")
        )
        shellopts = next(
            l.split("=", 1)[1] for l in r.stdout.splitlines() if l.startswith("SHELLOPTS=")
        )
        assert "e" in flags, f"{template} never restores set -e: $- is {flags!r}"
        assert "u" in flags, f"{template} never restores set -u: $- is {flags!r}"
        assert "pipefail" in shellopts.split(":"), (
            f"{template} never restores pipefail: SHELLOPTS is {shellopts!r}"
        )

    @pytest.mark.parametrize("template", _TEMPLATES)
    def test_the_harness_fails_a_guard_that_only_suspends_set_u(
        self, template, cluster_params
    ):
        """Vacuity guard.  If the hostile profile did not actually fail under
        pipefail, every test above would pass against the broken guard.

        The narrowed prologue is rebuilt from the guard's position rather than by
        replacing a literal, so it reproduces the shipped shape no matter what the
        current suspend line says -- a literal replace fails here for bookkeeping
        reasons on any variant spelling, which is not a signal about anything."""
        lines = self._prologue(template, cluster_params).splitlines()
        src = lines.index("source /etc/profile")
        suspend = max(i for i in range(src) if lines[i].strip().startswith("set +"))
        lines[suspend] = "set +u"
        narrowed = "\n".join(lines)
        r = self._run(narrowed, self._HOSTILE_PROFILE)
        assert r.returncode != 0, (
            "a guard that suspends only set -u survived the hostile profile, so "
            "these tests prove nothing; the profile no longer reproduces AL2023's "
            "debuginfod.sh failure"
        )


class TestNvmeDetectionSurvivesSetE:
    """The NVMe device filter runs inside a `while read` subshell under
    `set -euo pipefail`, and which devices survive must not depend on their
    position in the list -- `find` does not sort, so position is not controlled.
    Both the skipped-first and skipped-last cases are asserted, along with the
    loop's own exit status, which the process substitution otherwise hides.

    _run_postinstall cannot host this: it discards the rendered script's line 2
    and so runs without `set -e` at all (it has to -- mkdir is stubbed, so the
    head-node path's `cd` targets never exist). This class therefore extracts the
    real loop from the rendered template and executes it alone, under a real
    `set -e`. Extracted rather than restated so it cannot drift from what ships.

    Note what this does NOT show: rewriting the `if` as a chain of
    `[[ ... ]] && continue` guards passes every test here, and that is correct --
    both forms were run under bash 5.3 and behave identically, because `continue`
    is exempt from `set -e` inside a loop body. The `if` is a style choice."""

    def _loop(self, cluster_params_gpu_queue_enabled):
        env = _make_env(os.path.join(REPO_ROOT, "templates"))
        rendered = env.get_template("postinstall.j2").render(
            **cluster_params_gpu_queue_enabled
        )
        start = rendered.index("mapfile -t _NVME_DEVS")
        end = rendered.index("_NVME_COUNT=")
        loop = rendered[start:end]
        assert "while read" in loop and "basename" in loop, (
            f"the NVMe detection loop no longer looks like a loop; this test "
            f"extracts it by position and would silently stop testing it:\n{loop}"
        )
        return loop

    def _run(self, loop, devices, claimed, ebs_devices=()):
        """Execute the extracted loop under `set -e` against a fake sysfs."""
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            block = os.path.join(tmp, "sys", "block")
            for dev in devices:
                os.makedirs(os.path.join(block, dev, "device"))
                with open(os.path.join(block, dev, "device", "model"), "w") as fh:
                    model = (
                        "Amazon Elastic Block Store"
                        if dev in ebs_devices
                        else _INSTANCE_STORE_MODEL
                    )
                    fh.write(model + "\n")
                holders = os.path.join(block, dev, "holders")
                os.makedirs(holders)
                if dev in claimed:
                    with open(os.path.join(holders, "dm-0"), "w"):
                        pass
            claimed_devs = " ".join(f"/dev/{d}" for d in claimed) or "''"
            devs = " ".join(devices) or "''"
            script = (
                "set -euo pipefail\n"
                f'find() {{ for _d in {devs}; do [[ -n "$_d" ]] && '
                f'echo "{block}/$_d"; done; return 0; }}\n'
                "blkid() {\n"
                f"    for _c in {claimed_devs}; do "
                '[[ "$_c" == "$1" ]] && return 0; done\n'
                "    return 2\n"
                "}\n"
                'sudo() { [[ "$1" == "blkid" ]] && { shift; blkid "$@"; '
                "return $?; }; return 0; }\n"
                + loop
                + '\nprintf "%s\\n" "${_NVME_DEVS[@]:-}"\n'
                # The process substitution hides the loop's own exit status, which
                # is the whole failure mode. Run the same pipeline again where it
                # is observable, so a form that exits 1 on a skipped device is
                # visible rather than silently swallowed.
                + "\n_inner_status=0\n"
                + loop.replace("mapfile -t _NVME_DEVS < <(", "(").rstrip().rstrip(")")
                + ") > /dev/null || _inner_status=$?\n"
                + 'printf "STATUS %s\\n" "$_inner_status"\n'
            )
            path = os.path.join(tmp, "loop.sh")
            with open(path, "w") as fh:
                fh.write(script)
            r = subprocess.run(["bash", path], capture_output=True, text=True)
        lines = [l for l in r.stdout.split("\n") if l]
        status = next(
            (int(l.split()[1]) for l in lines if l.startswith("STATUS ")), None
        )
        assert status is not None, f"harness lost the inner status: {r.stdout!r}"
        return r, [l for l in lines if not l.startswith("STATUS ")], status

    def test_a_claimed_device_last_in_the_list_does_not_fail_the_loop(
        self, cluster_params_gpu_queue_enabled
    ):
        """Skipped device last: the position where a filter's own non-zero status
        would become the loop's, since nothing follows it in the body."""
        loop = self._loop(cluster_params_gpu_queue_enabled)
        r, devs, status = self._run(loop, ["nvme1n1", "nvme2n1"], ["nvme2n1"])
        assert r.returncode == 0, f"the detection loop aborts: {r.stderr}"
        assert status == 0, (
            f"the device-detection loop exits {status} when the last device is "
            f"skipped; the process substitution hides it here, but under `set -e` "
            f"that status is real.\nstderr: {r.stderr}"
        )
        assert devs == ["nvme1n1"], (
            f"wrong devices survived -- got {devs}, expected only the free one."
            f"\nstderr: {r.stderr}"
        )

    def test_a_claimed_device_first_does_not_lose_the_free_devices_behind_it(
        self, cluster_params_gpu_queue_enabled
    ):
        """The complementary position: two free devices behind a claimed one, which
        must both survive and take the RAID0 path downstream."""
        loop = self._loop(cluster_params_gpu_queue_enabled)
        r, devs, status = self._run(
            loop, ["nvme1n1", "nvme2n1", "nvme3n1"], ["nvme1n1"]
        )
        assert r.returncode == 0, f"the detection loop aborts: {r.stderr}"
        assert status == 0, f"the detection loop exits {status}: {r.stderr}"
        assert devs == ["nvme2n1", "nvme3n1"], (
            f"free devices behind a claimed one were lost -- got {devs}."
            f"\nstderr: {r.stderr}"
        )

    def test_an_ebs_device_last_in_the_list_does_not_fail_the_loop(
        self, cluster_params_gpu_queue_enabled
    ):
        """The model filter is subject to the same rule, and an EBS volume is what
        trips it in practice -- every instance has one. Placed last, it is the
        device whose false test would end the loop body."""
        loop = self._loop(cluster_params_gpu_queue_enabled)
        r, devs, status = self._run(
            loop, ["nvme1n1", "nvme2n1"], [], ebs_devices=["nvme2n1"]
        )
        assert r.returncode == 0, f"the detection loop aborts: {r.stderr}"
        assert status == 0, (
            f"the detection loop exits {status} when the last device is an EBS "
            f"volume, which every instance has.\nstderr: {r.stderr}"
        )
        assert devs == ["nvme1n1"], (
            f"wrong devices survived -- got {devs}, expected only the instance "
            f"store.\nstderr: {r.stderr}"
        )


class TestLmodConfigureGetsEveryToolItHardQuitsOn:
    """Lmod's ./configure does not degrade when a helper tool is missing -- it
    prints "You must have <tool> in your path. Quitting!" and exits non-zero, which
    under `set -euo pipefail` is OnNodeConfiguredExecutionFailure.

    `bc` is not on the RHEL 9 PCluster AMI (verified absent on head node
    i-0000000000000015; it lives in rhel-9-baseos, so it needs neither EPEL nor
    CRB), and neither package line installed it. That failed osiris at 17:26 on
    2026-07-28, one stage after the pip fix let the node reach Lmod at all --
    configure got through pkg-config, tcl.h, ps, expr and basename and quit on bc.

    Both arms are checked even though the Ubuntu builds have been passing: `bc` is
    on that AMI incidentally, and depending on what a base image happens to carry
    is how this failure hid in the first place. Reading Lmod 8.7.55's own configure
    for every "You must have" gate gives six: pkg-config, ps, expr|gexpr,
    basename|gbasename, bc, and sha1sum|shasum|md5sum|md5. Only bc was missing --
    the rest are coreutils or already on the package lines -- so `bc` is the whole
    fix, not the first of a series.

    Asserted on the execution trace rather than the source, for the same reason as
    TestLuarocksGetsTheLuaHeadersItCompilesAgainst: the package line sits inside the
    HeadNode-only gate, and a source grep passes with it in a block that never runs."""

    _CONFIGURE_REQUIRES = ("bc",)

    # Both dnf distros, because postinstall's critical-package block splits between
    # them: bc dropped from the AL2023 line alone is invisible to a RHEL-only run,
    # and unlike Ubuntu there is no reason to expect that AMI to carry it anyway.
    @pytest.mark.parametrize(
        "fixture,manager",
        [
            ("cluster_params", "apt-get"),
            ("cluster_params_rhel", "dnf"),
            ("cluster_params_al2023", "dnf"),
        ],
    )
    def test_lmod_configure_prerequisites_are_installed_on_the_head_node(
        self, request, fixture, manager
    ):
        params = request.getfixturevalue(fixture)
        _, trace, _ = _run_postinstall(params, "HeadNode")
        installed = TestPackageManagersMatchTheRenderedOs._installed_packages(
            trace, manager
        )
        for tool in self._CONFIGURE_REQUIRES:
            assert tool in installed, (
                f"{manager} never installs {tool!r}, which Lmod's ./configure "
                f"hard-quits on.\nInstalled: {sorted(installed)}\n"
                "This is the failure that took down osiris on 2026-07-28: "
                'configure prints "You must have bc in your path. Quitting!" and '
                "set -euo pipefail turns that into a failed node bootstrap."
            )

    @pytest.mark.parametrize(
        "fixture,manager",
        [
            ("cluster_params", "apt-get"),
            ("cluster_params_rhel", "dnf"),
            ("cluster_params_al2023", "dnf"),
        ],
    )
    def test_the_prerequisites_land_before_configure_runs(
        self, request, fixture, manager
    ):
        """Installing bc after ./configure would be useless, so the order matters.

        This one is asserted on the rendered source, not the trace: ./configure is
        deliberately not among _run_postinstall's stubs, so it never reaches the
        trace at all and a trace-based ordering check is silently vacuous -- which
        is exactly how the first version of this test passed with bc moved to the
        line below `sudo -E make install`."""
        env = _make_env(os.path.join(REPO_ROOT, "templates"))
        rendered = env.get_template("postinstall.j2").render(
            **request.getfixturevalue(fixture)
        )
        # Comments are stripped: the block explaining *why* bc is on the package
        # line names ./configure itself, and matching that comment put the
        # "configure" anchor 80 lines above the command it describes.
        lines = [
            "" if ln.lstrip().startswith("#") else ln
            for ln in rendered.splitlines()
        ]
        installs = [
            i for i, ln in enumerate(lines)
            if manager in ln and " install" in ln and re.search(r"\bbc\b", ln)
        ]
        configures = [i for i, ln in enumerate(lines) if "./configure" in ln]
        assert installs, f"no rendered {manager} line installs bc"
        assert configures, "postinstall no longer runs Lmod's ./configure"
        assert min(installs) < min(configures), (
            f"bc is installed at rendered line {min(installs)} but ./configure "
            f"runs at {min(configures)} -- configure quits before bc exists"
        )


class TestLuarocksGetsTheLuaHeadersItCompilesAgainst:
    """luaposix, luafilesystem, and lua-term are C extensions. Ubuntu's luarocks
    package declares liblua5.1-dev|liblua5.2-dev|liblua5.3-dev as an alternative
    group and apt picks 5.3, while luarocks itself runs lua_version 5.1 -- so a
    stock ubuntu2404 AMI has /usr/include/lua5.3 and lua5.4 and no 5.1 header,
    every rock fails to compile, and `set -euo pipefail` takes the node down with
    OnNodeConfiguredExecutionFailure. That is what failed osiris at 15:24 on
    2026-07-27.

    Asserted on the execution trace, not the source: the rocks are installed
    inside the HeadNode-only gate, so the trace also proves the apt line is
    reached on the node that needs it. A source-level grep would pass with the
    install sitting in a block that never runs."""

    _ROCKS = ("luaposix", "luafilesystem", "lua-term")

    def _apt_packages(self, trace):
        """Every package name handed to any apt-get install in the trace."""
        pkgs = set()
        for line in trace.splitlines():
            if "apt-get" not in line or " install" not in line:
                continue
            tail = line.split(" install", 1)[1]
            pkgs.update(w for w in tail.split() if not w.startswith("-"))
        return pkgs

    def test_the_lua_dev_headers_are_installed_before_any_rock_is_built(
        self, cluster_params
    ):
        _, trace, _ = _run_postinstall(cluster_params, "HeadNode")

        # The sudo stub logs "sudo luarocks ...", so anchor on the word, not the
        # start of the line.
        rock_lines = [
            i for i, ln in enumerate(trace.splitlines())
            if re.search(r"\bluarocks\b", ln) and " install" in ln
        ]
        assert rock_lines, f"postinstall no longer installs any luarocks rock: {trace}"

        assert "liblua5.1-0-dev" in self._apt_packages(trace), (
            "postinstall builds Lua C extensions without installing the 5.1 dev "
            "headers. apt will NOT pull them in on its own -- luarocks' dependency "
            "on liblua5.1-dev is one arm of an alternative group that apt satisfies "
            "with liblua5.3-dev, so lua.h for 5.1 is absent and every rock below "
            f"fails to compile.\ntrace:\n{trace}"
        )

        header_line = min(
            i for i, ln in enumerate(trace.splitlines())
            if "apt-get" in ln and "liblua5.1-0-dev" in ln
        )
        # <= because the headers ship on the same apt-get line as luarocks itself;
        # the property is that no rock is built before them, not that they occupy
        # an earlier line.
        assert header_line <= min(rock_lines), (
            "the Lua dev headers are installed after the first rock is built, so "
            f"that rock still has no lua.h to compile against.\ntrace:\n{trace}"
        )

    def test_the_rocks_and_the_headers_target_the_same_lua_version(
        self, cluster_params
    ):
        """The block reads LUA_VER off the `lua` interpreter and builds LUA_CPATH
        from it, so headers, rocks, and Lmod's module path must be one version.
        Installing 5.1 headers while pinning the rocks to 5.3 compiles them into
        a directory Lmod never searches -- which fails at module load, long after
        the build looks clean."""
        env = _make_env(os.path.join(REPO_ROOT, "templates"))
        rendered = env.get_template("postinstall.j2").render(**cluster_params)
        body = "\n".join(
            ln for ln in rendered.splitlines() if not ln.lstrip().startswith("#")
        )

        # Scoped to the luarocks install line. The general dev-package line above
        # installs lua5.4/liblua5.4-dev for the user environment, which is
        # unrelated to what the rocks compile against and must not be dragged in.
        luarocks_line = [
            ln for ln in body.splitlines()
            if re.search(r"\bluarocks\b", ln) and "apt-get" in ln
        ]
        assert luarocks_line, "postinstall no longer apt-installs luarocks"
        dev_versions = set(
            re.findall(r"liblua(\d\.\d)-\d*-?dev", " ".join(luarocks_line))
        )
        assert dev_versions == {"5.1"}, (
            f"the luarocks apt line installs Lua dev headers for "
            f"{dev_versions or 'nothing'}; the luarocks/Lmod block is 5.1 throughout"
        )

        for rock in self._ROCKS:
            for line in body.splitlines():
                if f"luarocks" in line and rock in line:
                    pinned = re.search(r"--lua-version[= ](\d\.\d)", line)
                    assert pinned is None or pinned.group(1) == "5.1", (
                        f"{rock} is pinned to Lua {pinned.group(1)} while the dev "
                        "headers and LUA_CPATH are 5.1"
                    )


class TestAmazonLinux2023InstallsOnlyWhatItPackages:
    """AL2023 shares the dnf family with RHEL 9 but not its package set, and every
    difference is a package that does not exist rather than one that is merely
    named differently -- so `dnf install` exits non-zero and `set -euo pipefail`
    turns each one into a dead node. All four gaps were checked against the al2023
    core repo's own metadata (primary.xml on x86_64 and aarch64), not assumed:

      luarocks     absent. RHEL builds three rocks with it; AL2023 packages all
                   three as RPMs (lua-posix, lua-filesystem, lua-term) instead.
      tcllib       absent. Nothing in the toolkit references it -- Lmod uses tcl
                   itself -- so it is dropped rather than replaced.
      nvtop        absent, and there is no EPEL to fall back on.
      epel-release absent as a package, and the EPEL 9 release RPM the RHEL arm
                   installs by URL is built for el9. Nothing on the AL2023 line
                   needs a second repository: lua-devel, lua-posix, bc and the
                   rest are all in core.

    `bc` is the opposite case and is covered by
    TestLmodConfigureGetsEveryToolItHardQuitsOn: it IS in core, upstream
    aws-parallelcluster-monitoring's own alinux2023.sh claims otherwise
    ("bc is not in the default AL2023 repos"), and Lmod's ./configure hard-quits
    without it."""

    _ABSENT = ("luarocks", "tcllib", "nvtop", "epel-release")

    @staticmethod
    def _dnf_installs(text):
        """Package names handed to a dnf install in the rendered text."""
        names = set()
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "dnf" not in stripped:
                continue
            if " install" not in stripped:
                continue
            tail = stripped.split(" install", 1)[1]
            names.update(w.strip('"\'') for w in tail.split() if not w.startswith("-"))
        return names

    @pytest.mark.parametrize("template", ("preinstall.j2", "postinstall.j2"))
    @pytest.mark.parametrize(
        "fixture", ("cluster_params_al2023", "cluster_params_al2023_gpu_queue")
    )
    def test_no_package_absent_from_the_al2023_repo_is_installed(
        self, request, fixture, template
    ):
        """Asserted on the rendered text of both fixtures: the GPU variant is
        required because the nvtop/htop install sits inside
        `{% if enable_gpu == 'true' %}`, which the plain fixture leaves false."""
        params = request.getfixturevalue(fixture)
        env = _make_env(os.path.join(REPO_ROOT, "templates"))
        rendered = env.get_template(template).render(**params)
        installed = self._dnf_installs(rendered)
        for pkg in self._ABSENT:
            assert pkg not in installed, (
                f"{template} installs {pkg!r} on base_os={params['base_os']}, "
                f"which is absent from the al2023 core repo on both arches. dnf "
                f"exits non-zero and `set -euo pipefail` fails the node."
            )

    def test_no_epel_release_rpm_is_fetched_by_url(self, cluster_params_al2023):
        """The RHEL arm installs EPEL by URL because epel-release is not packaged
        in RHEL. Copying that line here is the mutation that looks like symmetry:
        the RPM is built for el9, and nothing on the AL2023 package line needs a
        second repository at all."""
        env = _make_env(os.path.join(REPO_ROOT, "templates"))
        for template in ("preinstall.j2", "postinstall.j2"):
            rendered = env.get_template(template).render(**cluster_params_al2023)
            for line in rendered.splitlines():
                if line.strip().startswith("#"):
                    continue
                assert "epel-release" not in line, (
                    f"{template} pulls in EPEL on "
                    f"base_os={cluster_params_al2023['base_os']}: {line.strip()!r}"
                )

    @pytest.mark.parametrize("node_type", ("HeadNode", "ComputeFleet"))
    def test_luarocks_is_never_invoked_on_this_arm(
        self, cluster_params_al2023, node_type
    ):
        """The three `sudo luarocks install` lines sat below the whole
        `{% if 'ubuntu' in base_os %}` chain, so an arm that skips luarocks still
        reached them -- `luarocks: command not found` under `set -euo pipefail` is
        a dead head node. They are duplicated into the Ubuntu and RHEL arms for
        that reason. This asserts on the execution trace: luarocks is stubbed in
        the harness, so a source-level check cannot tell a reached line from an
        unreached one, and the stub returns 0 where a real node returns 127."""
        _, trace, _ = _run_postinstall(cluster_params_al2023, node_type)
        assert "luarocks" not in trace, (
            f"postinstall ran luarocks on base_os="
            f"{cluster_params_al2023['base_os']} ({node_type}), where the binary "
            f"does not exist: rc=127 fails the node.\ntrace:\n{trace}"
        )

    @pytest.mark.parametrize(
        "fixture", ("cluster_params", "cluster_params_rhel")
    )
    def test_the_other_two_arms_still_build_the_rocks(self, request, fixture):
        """Guards the test above against being satisfied by deleting the rocks
        everywhere. Ubuntu and RHEL have no RPM for luaposix or luafilesystem, so
        without luarocks they get no Lua C extensions and Lmod fails at module
        load -- long after the build looks clean."""
        params = request.getfixturevalue(fixture)
        _, trace, _ = _run_postinstall(params, "HeadNode")
        for rock in ("luaposix", "luafilesystem", "lua-term"):
            assert rock in trace, (
                f"postinstall no longer builds {rock} on "
                f"base_os={params['base_os']}: {trace}"
            )


@pytest.mark.parametrize("fname", _CLUSTER_STRUCTURAL_POLICY_FILES)
def test_iam_policy_no_unsubstituted_placeholders(fname):
    """No <PLACEHOLDER> tokens must remain after substitution — catches missing entries in _PLACEHOLDER_SUB."""
    import re
    path = os.path.join(REPO_ROOT, "templates", fname)
    with open(path) as f:
        raw = f.read()
    for placeholder, value in _PLACEHOLDER_SUB.items():
        raw = raw.replace(placeholder, value)
    remaining = re.findall(r"<[A-Z_]+>", raw)
    assert not remaining, (
        f"{fname}: unsubstituted placeholders after substitution: {remaining}\n"
        f"  Add them to _PLACEHOLDER_SUB in this test file."
    )


def test_no_operator_side_scheduled_teardown_is_reintroduced():
    """The toolkit schedules nothing on the operator's workstation. The old
    --cluster_lifetime feature fired `at` locally to run kill_pcluster.py later,
    which meant a teardown could run hours after the operator walked away, from
    a machine that may have slept, moved networks, or rebuilt the same cluster
    name in the meantime. Teardown is now manual and at the operator's
    discretion. Compute cost is bounded by ScaledownIdletime scaling idle nodes
    to zero, which needs no scheduler at all."""
    for relpath in ("src/pcluster_core.py", "make_pcluster.py", "kill_pcluster.py"):
        with open(os.path.join(REPO_ROOT, relpath)) as fh:
            body = fh.read()
        for token in ("at_job_id", "atrm", "atq", "cluster_lifetime"):
            assert_source_is_real(body, 'test_no_operator_side_scheduled_teardown_is_reintroduced')
            assert token not in body, (
                f"{relpath} references {token!r} — an operator-side scheduled "
                f"teardown appears to have been reintroduced"
            )

    assert not os.path.exists(
        os.path.join(REPO_ROOT, "templates", "generate_cron_lifetime_string.j2")
    ), "the operator-side lifetime scheduler template is back"


def _policy_suffixes_in_core():
    """Every policy-name suffix pcluster_core.py knows about, per literal list."""
    import ast
    path = os.path.join(REPO_ROOT, "src", "pcluster_core.py")
    with open(path) as f:
        tree = ast.parse(f.read())
    lists = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        try:
            vals = ast.literal_eval(node)
        except (ValueError, SyntaxError):
            continue
        items = [v for v in vals if isinstance(v, str)]
        # "-ClusterNode-" is not decoration: _policy_suffixes_in_core finds a
        # list only when *every* string in it looks like a policy suffix, so a
        # prefix missing here silently drops the whole list from the sweep
        # rather than failing -- and the three lists cross-assert each other,
        # so dropping them all leaves nothing to disagree.
        if items and all(
            s.startswith(("-HeadNode-", "-ComputeNode-", "-ClusterNode-"))
            for s in items
        ):
            lists.append(set(items))
    return lists


def test_every_policy_template_is_created_and_deleted():
    """The four base policies plus HeadNode-Monitoring exist as templates, are
    created by _setup_iam, and are deleted by _delete_managed_policies. A policy
    created but not deleted is an orphan the operator pays no money for but
    which blocks a same-name rebuild and silently accumulates in the account."""
    expected = {"-" + f[: -len(".json_src")] for f in _POLICY_FILES}
    assert expected == {
        "-HeadNode-Compute", "-HeadNode-Storage", "-HeadNode-IAM",
        "-ComputeNode-Base", "-ClusterNode-Deny", "-HeadNode-Monitoring",
    }, f"policy template set changed: {sorted(expected)}"

    # Every suffix list in pcluster_core.py must be a subset of the template set:
    # a suffix with no template renders nothing; a template in no list is never
    # created. The union across lists must equal the template set exactly.
    lists = _policy_suffixes_in_core()
    assert lists, "no policy-suffix lists found in pcluster_core.py"
    union = set().union(*lists)
    assert union == expected, (
        f"pcluster_core.py suffix lists do not match templates/\n"
        f"  in code but no template: {sorted(union - expected)}\n"
        f"  template but never created/deleted: {sorted(expected - union)}"
    )
    # A union check alone is too weak: dropping a base suffix from one list
    # (say _delete_managed_policies) leaves the union intact via the others,
    # while that policy is created and never deleted. Only -HeadNode-Monitoring
    # is conditional, so every list must carry all four base suffixes.
    base = expected - {"-HeadNode-Monitoring"}
    for lst in lists:
        assert base <= lst, (
            f"a policy-suffix list in pcluster_core.py is missing base suffixes "
            f"{sorted(base - lst)}: {sorted(lst)}"
        )


class TestTeardownFailuresReachTheOperator:
    """Every ignored cleanup failure must be collected, named in the summary,
    carried in the SNS report, and turned into a non-zero exit -- a failed IAM
    detach once printed "Cluster <name> has been deleted" and exited 0, and the
    leftovers were found by hand days later.

    The step functions that replaced the teardown playbook's ignore_errors +
    register: pattern are pinned in tests/test_teardown_steps.py, which drives
    the real code rather than reading a task list. What is left here is the half
    that is still a template: the SNS report, which is what whoever is on the
    topic sees instead of the terminal.
    """

    def test_the_sns_report_carries_the_orphan_list(self, cluster_params,
                                                   cluster_params_orphaned_teardown):
        """The operator who ran the teardown sees the terminal; whoever is on the
        SNS topic sees only the report. Both need the list."""
        env = _make_env(os.path.join(REPO_ROOT, "templates"))
        tmpl = env.get_template("sns_destruction_summary_report.j2")

        clean = tmpl.render(**cluster_params)
        assert "CLEANUP STEP(S) FAILED" not in clean, (
            "clean teardown report warns about failures that did not happen"
        )

        dirty = tmpl.render(**cluster_params_orphaned_teardown)
        assert "2 CLEANUP STEP(S) FAILED" in dirty
        for resource in cluster_params_orphaned_teardown["_orphaned_resources"]:
            assert f"- {resource}" in dirty, (
                f"SNS destruction report never names orphaned resource {resource!r}"
            )


def _pcluster_core():
    src = os.path.join(REPO_ROOT, "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    import pcluster_core

    return pcluster_core


def _core_function_source(name):
    """One function's own source out of src/pcluster_core.py."""
    import inspect

    return inspect.getsource(getattr(_pcluster_core(), name))


class TestAnUnconfirmedDeleteIsNotReportedAsSuccess:
    """Every reporting surface claimed "Cluster <name> has been deleted"
    whenever no cleanup step *failed* -- and on a wait timeout nothing failed
    and nothing was deleted either. There are three delete outcomes, so the
    claim is derived once (_classify_cluster_delete_outcome, pinned outcome by
    outcome in tests/test_teardown_steps.py) and every surface interpolates it.

    What is checked here is that no surface restates the literal instead: the
    SNS report, and the terminal summary _format_destruction_summary prints.
    """

    def test_no_reporting_surface_hardcodes_the_success_claim(self):
        """Two surfaces have to agree. Either one restating the claim as a
        literal goes back to asserting it unconditionally, which is the defect.

        _classify_cluster_delete_outcome is where the literal legitimately
        lives, so it is not one of the surfaces swept -- and the summary's
        ".serial has been deleted" is about the local serial file, not the
        cluster.
        """
        surfaces = {
            os.path.join("templates", "sns_destruction_summary_report.j2"): None,
            "src/pcluster_core.py:_format_destruction_summary": _core_function_source(
                "_format_destruction_summary"
            ),
        }
        for label, text in surfaces.items():
            if text is None:
                with open(os.path.join(REPO_ROOT, label)) as fh:
                    text = fh.read()
            offenders = [
                line
                for line in text.splitlines()
                if "has been deleted" in line
                and not line.lstrip().startswith("#")
                and ".serial" not in line
            ]
            assert not offenders, (
                f"{label} states the deletion claim as a literal rather than "
                f"interpolating the derived headline: {offenders}"
            )

    def test_the_terminal_summary_interpolates_the_derived_headline(self):
        """Vacuity guard for the sweep above: a summary that printed no
        headline at all would satisfy it while telling the operator nothing."""
        source = _core_function_source("_format_destruction_summary")
        assert "delete_headline" in source, (
            "_format_destruction_summary no longer carries the derived headline"
        )

    @pytest.mark.parametrize(
        "fixture_name,expected",
        [
            ("cluster_params", "has been deleted"),
            ("cluster_params_unconfirmed_delete", "was NOT confirmed"),
        ],
    )
    def test_the_sns_report_carries_the_same_claim(self, request, fixture_name, expected):
        """Whoever is on the SNS topic never sees the terminal. The report shows
        "Completed destruction: <time>", which reads as success on its own."""
        params = request.getfixturevalue(fixture_name)
        env = _make_env(os.path.join(REPO_ROOT, "templates"))
        rendered = env.get_template("sns_destruction_summary_report.j2").render(**params)
        assert expected in rendered, (
            f"destruction report does not carry {expected!r} for {fixture_name}"
        )


class TestARetainedResourceIsReportedWithoutBeingCalledAnOrphan:
    """`--delete_s3_bucketname=false` skips the bucket delete, so nothing
    reached the orphan list and teardown printed "has been deleted" over a
    bucket that was still there billing. The benchmark results bucket and the
    30-day log groups are the same shape: kept on purpose, invisible in the
    output, not free.

    The distinction is the whole fix. The orphan list drives the non-zero exit,
    and a deliberate retention is not a failure -- putting these there would
    fail every `--delete_s3_bucketname=false` teardown. So they go through
    _collect_retained_resources, which is reported on both surfaces and drives
    nothing.

    These drove the teardown playbook's set_fact expression until that playbook
    was deleted; they drive _collect_retained_resources itself now, which is the
    code an operator's teardown actually runs.
    """

    _BASE = {
        "s3_bucketname": "parallelclustermaker-osiris-00001220260720",
        "results_bucketname": "parallelclustermaker-results-123456789012-us-east-2",
        "cluster_name": "osiris",
        "delete_s3_bucketname": True,
        "enable_hpc_benchmarks": False,
    }

    def _retained(self, **facts):
        return _pcluster_core()._collect_retained_resources(**{**self._BASE, **facts})

    def test_a_skipped_bucket_delete_is_reported(self):
        """The reported bug: the bucket survives and nothing says so."""
        retained = self._retained(delete_s3_bucketname=False)
        assert any("parallelclustermaker-osiris-00001220260720" in r for r in retained), (
            "a retained per-build bucket is not named anywhere in the teardown "
            f"output: {retained}"
        )

    def test_a_deleted_bucket_is_not_reported_as_retained(self):
        """Vacuity guard on the test above: on the default path the bucket really
        is gone, and reporting it as retained sends the operator hunting."""
        retained = self._retained(delete_s3_bucketname=True)
        assert not any(
            "parallelclustermaker-osiris-00001220260720" in r for r in retained
        ), f"the per-build bucket is reported as retained after being deleted: {retained}"

    def test_the_results_bucket_is_reported_only_when_it_exists(self):
        """It is created under enable_hpc_benchmarks and deleted by nothing, so
        naming it on a cluster that never had one is a false alarm."""
        with_bench = self._retained(enable_hpc_benchmarks=True)
        assert any("results-123456789012-us-east-2" in r for r in with_bench), with_bench
        without = self._retained(enable_hpc_benchmarks=False)
        assert not any("results-123456789012-us-east-2" in r for r in without), without

    def test_the_log_groups_are_always_reported(self):
        """They are retained by PCluster\'s own default on every teardown, clean
        or not -- 33 osiris groups and ~55 GB accumulated unremarked."""
        retained = self._retained()
        assert any("/aws/parallelcluster/osiris-" in r for r in retained), retained

    def test_the_log_groups_are_named_by_prefix_not_in_full(self):
        """The group name carries a %Y%m%d%H%M creation timestamp, so an exact
        name cannot be constructed here. Printing one would be a fabrication the
        operator would then fail to find."""
        retained = self._retained()
        line = next(r for r in retained if "/aws/parallelcluster/" in r)
        assert "osiris-*" in line, (
            "the log group is named without a wildcard, which implies a "
            f"constructible name that does not exist: {line}"
        )

    def test_nothing_retained_is_also_counted_as_an_orphan(self):
        """The property that keeps a --delete_s3_bucketname=false teardown from
        exiting non-zero. _collect_orphaned_resources reads step *results* and
        nothing else, so a deliberate retention cannot reach it -- pinned on the
        signature, since a widened one is exactly how it would."""
        import inspect

        core = _pcluster_core()
        params = list(inspect.signature(core._collect_orphaned_resources).parameters)
        assert params == ["step_results"], (
            "_collect_orphaned_resources took on another input; a retention "
            f"condition could now reach the orphan list: {params}"
        )
        source = inspect.getsource(core._collect_orphaned_resources)
        for token in ("delete_s3_bucketname", "results_bucketname"):
            assert_source_is_real(source, 'test_nothing_retained_is_also_counted_as_an_orphan')
            assert token not in source, (
                f"the orphan list reads {token}, so a deliberately retained "
                f"resource would fail the teardown"
            )

    def test_the_retained_list_never_reaches_the_exit_status(self):
        """core_delete_cluster's non-zero exits must stay keyed on the orphan
        list and the delete outcome, never on a deliberate retention."""
        import ast

        core_src = _core_function_source("core_delete_cluster")
        tree = ast.parse(core_src.lstrip())
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            names = {
                n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)
            }
            if "_retained_resources" not in names:
                continue
            returns = [
                n for n in ast.walk(node)
                if isinstance(n, ast.Return)
            ]
            assert not returns, (
                "a deliberate retention decides core_delete_cluster's return "
                "value; retention is not a failure"
            )

    def test_both_summary_surfaces_report_it(self):
        """A retention on a clean teardown and a retention alongside orphans are
        two different renderings of the summary, and the bug is invisible on
        whichever one is left out."""
        core = _pcluster_core()
        retained = ["S3 bucket parallelclustermaker-osiris-00001220260720 (kept)"]
        for orphans in ([], ["Delete the SNS topic -- AccessDenied"]):
            text = "\n".join(core._format_destruction_summary(
                cluster_name="osiris", start_ts="t0", stop_ts="t1",
                delete_headline="Cluster osiris has been deleted.",
                orphaned_resources=orphans, retained_resources=retained,
            ))
            assert retained[0] in text, (
                f"the summary with orphans={orphans!r} does not report retained "
                f"resources"
            )

    def test_it_is_collected_before_the_sns_report_is_templated(self):
        """Same ordering rule the orphan collection follows: the report cannot
        carry a fact derived after it is rendered."""
        source = _core_function_source("core_delete_cluster")
        collect = source.index("_collect_retained_resources(")
        report = source.index("sns_destruction_summary_report.j2")
        assert collect < report, (
            "retained resources are collected after the SNS report is rendered, "
            "so the report cannot name them"
        )

    def test_the_sns_report_carries_it(self, cluster_params_retained_teardown):
        env = _make_env(os.path.join(REPO_ROOT, "templates"))
        text = env.get_template("sns_destruction_summary_report.j2").render(
            **cluster_params_retained_teardown
        )
        for resource in cluster_params_retained_teardown["_retained_resources"]:
            assert resource in text, (
                f"the SNS destruction report does not name {resource!r}"
            )

    def test_the_sns_report_omits_the_section_when_nothing_was_retained(
        self, cluster_params
    ):
        """Vacuity guard on the render test: an unconditional section would
        satisfy it while telling every operator something was left behind."""
        env = _make_env(os.path.join(REPO_ROOT, "templates"))
        text = env.get_template("sns_destruction_summary_report.j2").render(
            **cluster_params
        )
        assert "Retained in the account" not in text, text

    def test_a_retention_is_never_worded_as_a_failure(self):
        """The two lists are reported next to each other, so the wording is the
        only thing telling the operator which one needs action."""
        core = _pcluster_core()
        lines = core._format_destruction_summary(
            cluster_name="osiris", start_ts="t0", stop_ts="t1",
            delete_headline="Cluster osiris has been deleted.",
            orphaned_resources=["Delete the SNS topic -- AccessDenied"],
            retained_resources=["S3 bucket parallelclustermaker-osiris (kept)"],
        )
        retained_lines = [l for l in lines if "Retained" in l]
        assert retained_lines, "the summary has no retention heading"
        for line in retained_lines:
            assert "FAILED" not in line, (
                f"a deliberate retention is worded as a failure: {line.strip()}"
            )


class TestTheResultsSyncSurvivesAnOlderVarsFile:
    """results_bucketname is newer than enable_hpc_benchmarks, so a vars file
    from an older toolkit satisfies the gate but leaves the sync's bucket
    undefined -- and teardown then destroys the head node holding the only copy
    of the results. The gain is a named cause, not recovered results: that
    bucket was never created, so the sync gets NoSuchBucket and the existing
    warning names it.
    """

    def _teardown_source(self):
        return _core_function_source("core_delete_cluster")

    def test_it_only_fires_when_the_vars_file_lacks_the_name(self):
        """A vars file that does define it must keep its own value; the
        fallback is an `or`, never an unconditional overwrite."""
        import re

        source = self._teardown_source()
        m = re.search(
            r"""results_bucketname\s*=\s*_v\(["']results_bucketname["']\)"""
            r"\s*or\s*_derive_results_bucket\(",
            source,
        )
        assert m, (
            "core_delete_cluster no longer falls back to _derive_results_bucket "
            "only when the vars file omits results_bucketname"
        )

    def test_the_derived_name_matches_the_python_derivation(self):
        """Pinned against _derive_results_bucket, never a restated literal --
        two sources that disagree make the target depend on which toolkit built
        the cluster."""
        source = self._teardown_source()
        assert "parallelclustermaker-results-" not in source, (
            "the teardown restates the results bucket name as a literal instead "
            "of calling _derive_results_bucket"
        )

    def test_the_inputs_are_ones_every_vars_file_has(self):
        """Referencing the cluster name or the serial would risk the very
        missing-key failure this exists to prevent, and would restore a
        per-build bucket."""
        import inspect

        params = set(
            inspect.signature(_pcluster_core()._derive_results_bucket).parameters
        )
        assert params == {"aws_account_id", "region"}, params

    def test_it_is_derived_before_the_sync_interpolates_it(self):
        source = self._teardown_source()
        assert source.index("_derive_results_bucket(") < source.index(
            "_sync_performance_results_to_s3("
        ), "the fallback is derived after the sync that needs it"

    def test_it_stays_inside_the_benchmark_gate(self):
        """The sync itself must not run on a cluster that never benchmarked."""
        source = self._teardown_source()
        assert source.index("if enable_hpc_benchmarks") < source.index(
            "_sync_performance_results_to_s3("
        ), "the results sync is not gated on enable_hpc_benchmarks"



# A class asserting that a failed describe is not a failed cluster lived
# here. It evaluated
# create_pcluster.yml's three abort tasks (a failed describe, a stack that never
# reached a terminal state, a genuine failure) against four real describe-cluster
# outcomes. The playbook is gone; the same three-way discrimination is now
# _classify_cluster_create_outcome / _wait_for_cluster_create in
# src/pcluster_core.py, driven outcome by outcome in
# tests/test_create_pcluster_migration.py (TestWaitForClusterCreate,
# TestClassifyClusterCreateOutcome, TestRunClusterCreateAndClassify).



def test_head_node_cannot_manage_hosted_zone_lifecycle():
    """CreateHostedZone / DeleteHostedZone cannot be scoped to a cluster (zone
    IDs are random), so granting them on Resource "*" lets anyone with a shell
    on the head node — including via Slurm job submission — delete any hosted
    zone in the account. Upstream PCluster grants the head node
    ChangeResourceRecordSets only; the zone itself is created and destroyed by
    CloudFormation under operator credentials."""
    forbidden = {
        "route53:CreateHostedZone",
        "route53:DeleteHostedZone",
        "route53:AssociateVPCWithHostedZone",
    }
    for fname in ("HeadNode-Storage.json_src", "HeadNode-Compute.json_src",
                  "HeadNode-IAM.json_src", "ComputeNode-Base.json_src"):
        granted = _actions(fname) & forbidden
        assert not granted, f"{fname} grants hosted-zone lifecycle: {sorted(granted)}"


def test_operator_policy_covers_hosted_zone_lifecycle():
    """The permissions removed from the head node have to live somewhere —
    cluster creation fails at the Route53HostedZone resource without them."""
    actions = _actions("OperatorPolicy.json_src")
    for action in ("route53:CreateHostedZone", "route53:DeleteHostedZone",
                   "route53:AssociateVPCWithHostedZone"):
        assert action in actions, f"OperatorPolicy is missing {action}"


def test_head_node_can_still_write_its_own_dns_records():
    """Slurm's node DNS registration needs this; removing it breaks cluster DNS."""
    assert "route53:ChangeResourceRecordSets" in _actions("HeadNode-Storage.json_src")


def test_instance_profile_arns_are_pinned_to_this_cluster():
    """A bare pclustermaker-role-* instance-profile wildcard lets cluster A's
    head node delete cluster B's instance profile, while every role ARN in the
    same policy is pinned to <CLUSTER_SERIAL_NUMBER>. Match the role scoping."""
    with open(os.path.join(REPO_ROOT, "templates", "HeadNode-IAM.json_src")) as f:
        raw = f.read()
    bad = [
        line.strip()
        for line in raw.splitlines()
        if "instance-profile/pclustermaker" in line and "<CLUSTER_SERIAL_NUMBER>" not in line
    ]
    assert not bad, f"unpinned instance-profile ARNs: {bad}"


# Sids that legitimately grant a mutating action on Resource "*" with no
# Condition. Each entry is a deliberate exception, not an oversight:
#   CloudFormationExtras  ValidateTemplate takes a template body, not an ARN.
#   EFSDescribe           CreateMountTarget/CreateTags run against a filesystem
#                         CloudFormation has not created yet, so no ARN exists
#                         at policy-render time.
#   FSxDescribe           Same for fsx:TagResource on a not-yet-created FSx fs.
#   CloudWatchMetrics     PutMetricData has no resource-level permissions in IAM.
#   AllowAccessToSSM      Verbatim from AWS's own AmazonSSMManagedInstanceCore;
#                         the SSM agent channel actions are not ARN-scopable.
#   EC2Keypair            Keypairs are named, not ARN-addressed, at create time.
#   Route53ClusterHostedZone  Zone IDs are AWS-generated and random; see the
#                         standing constraint in CLAUDE.md. Operator-only.
_WILDCARD_MUTATION_ALLOWLIST = {
    ("HeadNode-Storage.json_src", "CloudFormationExtras"),
    ("HeadNode-Storage.json_src", "EFSDescribe"),
    ("HeadNode-Storage.json_src", "FSxDescribe"),
    ("ComputeNode-Base.json_src", "CloudWatchMetrics"),
    ("ComputeNode-Base.json_src", "AllowAccessToSSM"),
    ("OperatorPolicy.json_src", "EC2Keypair"),
    ("OperatorPolicy.json_src", "Route53ClusterHostedZone"),
}

_READ_ONLY_VERBS = (
    "Describe", "List", "Get", "Head", "Check", "Lookup", "Query", "Scan",
    "BatchGet", "Filter", "Search", "Estimate", "Simulate",
)


@pytest.mark.parametrize("fname", _POLICY_FILES + ["OperatorPolicy.json_src"])
def test_no_new_unconditioned_wildcard_mutation_grants(fname):
    """A mutating action on Resource "*" with no Condition is reachable by anyone
    with a shell on the node — including via Slurm job submission. That is exactly
    how the Route53 zone-deletion hole got in. Read-only wildcards are fine;
    every mutating one needs either an ARN, a Condition, or an explicit entry in
    _WILDCARD_MUTATION_ALLOWLIST justifying why neither is possible.

    Only Allow statements are read. ClusterNode-Deny is nothing but mutating
    actions on Resource "*" -- that is the whole document -- and a ratchet that
    cannot tell a Deny from a grant would demand an allowlist entry for every
    escalation primitive the file exists to refuse, turning the allowlist into
    a list of things that are fine because they are forbidden."""
    offenders = []
    for stmt in _load_policy(fname)["Statement"]:
        if stmt["Effect"] != "Allow":
            continue
        resources = stmt["Resource"] if isinstance(stmt["Resource"], list) else [stmt["Resource"]]
        if "*" not in resources or "Condition" in stmt:
            continue
        sid = stmt.get("Sid", "<no Sid>")
        if (fname, sid) in _WILDCARD_MUTATION_ALLOWLIST:
            continue
        actions = stmt["Action"] if isinstance(stmt["Action"], list) else [stmt["Action"]]
        mutating = [
            a
            for a in actions
            if not any(a.split(":", 1)[-1].startswith(v) for v in _READ_ONLY_VERBS)
        ]
        if mutating:
            offenders.append((sid, sorted(mutating)))
    assert not offenders, (
        f"{fname}: unconditioned Resource \"*\" grants for mutating actions: {offenders}\n"
        f"  Scope them to an ARN, add a Condition, or add (file, Sid) to "
        f"_WILDCARD_MUTATION_ALLOWLIST with a comment explaining why neither works."
    )


def test_wildcard_mutation_allowlist_has_no_stale_entries():
    """A stale allowlist entry silently pre-approves a future wildcard grant
    under a Sid that no longer means what it did when the exception was written."""
    live = {
        (fname, stmt.get("Sid"))
        for fname in _POLICY_FILES + ["OperatorPolicy.json_src"]
        for stmt in _load_policy(fname)["Statement"]
    }
    stale = _WILDCARD_MUTATION_ALLOWLIST - live
    assert not stale, f"allowlist references Sids that no longer exist: {sorted(stale)}"


def test_compute_node_policy_grants_no_iam_or_privilege_escalation_actions():
    """ComputeNode-Base is attached to every queue via AdditionalIamPolicies, so
    any job submitted to Slurm runs with it. No IAM write, no PassRole, no
    role-assumption beyond GetCallerIdentity."""
    forbidden_prefixes = ("iam:",)
    forbidden_exact = {"sts:AssumeRole", "sts:AssumeRoleWithWebIdentity"}
    granted = _actions("ComputeNode-Base.json_src")
    bad = sorted(
        a for a in granted
        if a.startswith(forbidden_prefixes) or a in forbidden_exact
    )
    assert not bad, f"ComputeNode-Base grants privilege-escalation actions: {bad}"


def _vars_file_variables():
    """Every variable vars_file.j2 dereferences."""
    from jinja2 import meta

    path = os.path.join(REPO_ROOT, "templates", "vars_file.j2")
    with open(path) as fh:
        source = fh.read()
    return meta.find_undeclared_variables(_make_env(os.path.dirname(path)).parse(source))


def _cluster_parameters_keys():
    """Static keys of core_create_cluster's cluster_parameters dict (moved
    from make_pcluster.py to src/pcluster_core.py in the core/shim split)."""
    import ast

    with open(os.path.join(REPO_ROOT, "src", "pcluster_core.py")) as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and getattr(node.targets[0], "id", "") == "cluster_parameters"
        ):
            return {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    raise AssertionError("cluster_parameters dict not found in src/pcluster_core.py")


def test_vars_file_variables_are_all_supplied_by_make_pcluster():
    """vars_file.j2 renders with StrictUndefined, so any variable it dereferences
    that make_pcluster.py does not put in cluster_parameters aborts the build with
    UndefinedError. This is the first half of the CLAUDE.md tracing rule:
    Python dict -> vars_file.j2 -> template -> conftest."""
    missing = _vars_file_variables() - _cluster_parameters_keys()
    assert not missing, (
        "vars_file.j2 references variables absent from cluster_parameters "
        f"(build aborts with UndefinedError): {sorted(missing)}"
    )


def test_conftest_supplies_every_variable_make_pcluster_threads_to_templates():
    """Second half of the tracing rule: a variable added to cluster_parameters and
    used by a template but forgotten in conftest means the test suite renders a
    different template than production does, and the gap ships silently."""
    import ast

    with open(os.path.join(REPO_ROOT, "tests", "conftest.py")) as fh:
        tree = ast.parse(fh.read())

    fixture_keys = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "cluster_params":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Dict):
                    fixture_keys |= {
                        k.value for k in sub.keys if isinstance(k, ast.Constant)
                    }
    assert fixture_keys, "cluster_params fixture dict not found in conftest.py"

    # Only variables that a template actually dereferences matter; inert
    # cluster_parameters entries (e.g. Deployed_On) are not render inputs.
    referenced = set()
    for tdir, fname in _collect_templates():
        with open(os.path.join(tdir, fname)) as fh:
            source = fh.read()
        from jinja2 import meta

        referenced |= meta.find_undeclared_variables(_make_env(tdir).parse(source))

    missing = (_cluster_parameters_keys() & referenced) - fixture_keys
    assert not missing, (
        "conftest cluster_params is missing template variables that make_pcluster "
        f"supplies in production: {sorted(missing)}"
    )


def _render_benchmark_job(params):
    """Render job_hpc-benchmark.sh.j2 as the head node receives it."""
    return _make_env(os.path.join(REPO_ROOT, "hpc-benchmark")).get_template(
        "job_hpc-benchmark.sh.j2"
    ).render(**params)


def _sbatch_directives(rendered_job):
    """{long option: value} from the leading #SBATCH block of a job script.

    Stops at the first non-comment line so the explanatory #SBATCH examples in
    the GPU section cannot be mistaken for active directives.
    """
    out = {}
    for line in rendered_job.splitlines():
        if line.startswith("#SBATCH "):
            body = line[len("#SBATCH ") :].strip()
            key, _, value = body.partition("=")
            out[key.lstrip("-")] = value
        elif line and not line.startswith("#"):
            break
    return out


def _defined_partitions(params):
    import yaml

    env = _make_env(os.path.join(REPO_ROOT, "templates"))
    parsed = yaml.safe_load(env.get_template("config.pcluster.j2").render(**params))
    return [q["Name"] for q in parsed["Scheduling"]["SlurmQueues"]]


class TestBenchmarkJobScriptTargetsAPartitionThatExists:
    """The job script shipped `#SBATCH --partition=compute` unconditionally.

    enable_cpu_queue is derived from compute_instance_type (make_pcluster.py), so
    a GPU-only cluster is a supported configuration in which `compute` is never
    defined in config.pcluster.j2 — sbatch rejects the job outright with an
    invalid partition error. These tests read the partition out of the rendered
    job script and check it against the queue names in the cluster config
    rendered from the same variables, so the two cannot drift apart.
    """

    def test_a_cpu_only_cluster_targets_the_compute_partition(self, cluster_params):
        directives = _sbatch_directives(_render_benchmark_job(cluster_params))
        assert directives["partition"] == "compute"
        assert directives["partition"] in _defined_partitions(cluster_params)

    def test_a_gpu_only_cluster_targets_the_gpu_partition(self, cluster_params_gpu_enabled):
        directives = _sbatch_directives(_render_benchmark_job(cluster_params_gpu_enabled))
        assert directives["partition"] == "gpu", (
            "a GPU-only cluster has no compute partition; sbatch would reject the job"
        )
        assert directives["partition"] in _defined_partitions(cluster_params_gpu_enabled)

    def test_a_mixed_cluster_targets_compute_and_documents_the_gpu_override(
        self, cluster_params_gpu_queue_enabled
    ):
        rendered = _render_benchmark_job(cluster_params_gpu_queue_enabled)
        directives = _sbatch_directives(rendered)
        assert directives["partition"] == "compute"
        assert set(_defined_partitions(cluster_params_gpu_queue_enabled)) == {
            "compute",
            "gpu",
        }
        assert "sbatch --partition=gpu --ntasks-per-node=1 job_hpc-benchmark.sh" in rendered

    def test_every_queue_configuration_emits_a_partition_the_config_defines(
        self,
        cluster_params,
        cluster_params_gpu_enabled,
        cluster_params_gpu_queue_enabled,
        cluster_params_multi_instance_cpu,
        cluster_params_gpu_gdr_enabled,
        cluster_params_gpu_no_nvidia,
    ):
        for params in (
            cluster_params,
            cluster_params_gpu_enabled,
            cluster_params_gpu_queue_enabled,
            cluster_params_multi_instance_cpu,
            cluster_params_gpu_gdr_enabled,
            cluster_params_gpu_no_nvidia,
        ):
            partition = _sbatch_directives(_render_benchmark_job(params))["partition"]
            defined = _defined_partitions(params)
            assert partition in defined, (
                f"job script targets partition {partition!r} but the cluster config "
                f"defines {defined} for cpu={params['cpu_instance_types']} "
                f"gpu={params['gpu_instance_types']}"
            )


class TestBenchmarkJobScriptRankCountMatchesGpuCount:
    """On a GPU-only cluster the rank count must equal the NVIDIA GPU count.

    PCluster's vendored CLI does not generate Slurm GRES — gpu_count() in
    pcluster/aws/aws_resources.py feeds validators only, and gres is on the
    SLURM_SETTINGS_DENY_LIST — so --gres=gpu:N cannot be relied on. Matching
    --ntasks-per-node to the GPU count places one rank per device either way.
    """

    def test_a_gpu_only_cluster_gets_one_rank_per_gpu(self, cluster_params_gpu_enabled):
        directives = _sbatch_directives(_render_benchmark_job(cluster_params_gpu_enabled))
        assert directives["ntasks-per-node"] == str(
            cluster_params_gpu_enabled["gpu_ranks_per_node"]
        )

    def test_a_multi_gpu_instance_gets_all_of_its_gpus(self, cluster_params_gpu_gdr_enabled):
        """p4d.24xlarge carries 8 A100s; a hardcoded 4 would leave half idle."""
        directives = _sbatch_directives(
            _render_benchmark_job(cluster_params_gpu_gdr_enabled)
        )
        assert directives["ntasks-per-node"] == "8"

    def test_zero_gpus_falls_back_to_a_cpu_shaped_rank_count(
        self, cluster_params_gpu_no_nvidia
    ):
        """g4ad is AMD, so nvidia_gpu_count() is 0. --ntasks-per-node=0 is
        rejected by sbatch, which would make the job unsubmittable."""
        directives = _sbatch_directives(_render_benchmark_job(cluster_params_gpu_no_nvidia))
        assert directives["ntasks-per-node"] == "4"

    def test_no_configuration_emits_a_zero_or_empty_rank_count(
        self,
        cluster_params,
        cluster_params_gpu_enabled,
        cluster_params_gpu_queue_enabled,
        cluster_params_gpu_gdr_enabled,
        cluster_params_gpu_no_nvidia,
    ):
        for params in (
            cluster_params,
            cluster_params_gpu_enabled,
            cluster_params_gpu_queue_enabled,
            cluster_params_gpu_gdr_enabled,
            cluster_params_gpu_no_nvidia,
        ):
            value = _sbatch_directives(_render_benchmark_job(params))["ntasks-per-node"]
            assert value.isdigit() and int(value) > 0, (
                f"--ntasks-per-node={value!r} is not a positive integer"
            )

    def test_the_gpu_section_is_absent_without_a_gpu_queue(self, cluster_params):
        rendered = _render_benchmark_job(cluster_params)
        assert "--partition=gpu" not in rendered
        assert "GPU queue" not in rendered

    def test_the_rendered_job_script_is_valid_bash(
        self,
        cluster_params,
        cluster_params_gpu_enabled,
        cluster_params_gpu_queue_enabled,
        cluster_params_gpu_no_nvidia,
    ):
        import subprocess

        for params in (
            cluster_params,
            cluster_params_gpu_enabled,
            cluster_params_gpu_queue_enabled,
            cluster_params_gpu_no_nvidia,
        ):
            r = subprocess.run(
                ["bash", "-n"],
                input=_render_benchmark_job(params).encode(),
                capture_output=True,
            )
            assert r.returncode == 0, r.stderr


def _render_sbatch_default(params):
    """Render scripts/sbatch_default_submission_script.sh as the head node gets it."""
    return _make_env(os.path.join(REPO_ROOT, "scripts")).get_template(
        "sbatch_default_submission_script.sh"
    ).render(**params)


def test_collect_templates_covers_every_template_the_toolkit_renders(cluster_params):
    """Every template src/pcluster_core.py names must be a file
    _collect_templates discovers.

    The suffix filter is what hid scripts/sbatch_default_submission_script.sh:
    it is rendered from a hardcoded path, it carries Jinja2 in its #SBATCH
    block, and because the name ends in .sh no render test ever touched it. Its
    two --ntasks ladders were consequently the only Jinja2 in the repo with no
    coverage at all.

    This walked the two playbooks' `template:` tasks until those were deleted.
    core_create_cluster renders every one of them in Python now, so the names
    are string literals in that module -- swept by name rather than by call
    site, since several are supplied through a loop variable and an
    argument-position check would not see them.
    """
    import ast

    discovered = {fname for _tdir, fname in _collect_templates()}
    extra = {fname for _tdir, fname in EXTRA_TEMPLATES}
    with open(os.path.join(REPO_ROOT, "src", "pcluster_core.py")) as fh:
        tree = ast.parse(fh.read())
    named = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and (node.value.endswith(".j2") or node.value in extra)
    }
    assert named, "no template names found in pcluster_core.py -- sweep is blind"
    missing = sorted(named - discovered)
    assert not missing, (
        f"src/pcluster_core.py renders {missing} but no test renders them -- add "
        f"them to EXTRA_TEMPLATES (or to a directory in TEMPLATE_DIRS)"
    )
    # Vacuity guard on the sweep, not a target: the .sh entry is the reason
    # EXTRA_TEMPLATES exists, so losing it silently is the failure mode.
    assert extra <= named, (
        f"EXTRA_TEMPLATES lists {sorted(extra - named)}, which pcluster_core.py "
        f"never renders -- the entry is stale"
    )
    assert cluster_params  # the fixture is the render tests' own precondition


class TestTheDefaultSbatchScriptIsShapedByTheCluster:
    """Both --ntasks values in the default submission script were hardcoded
    ladders over eleven instance-size suffixes each.

    An unlisted size fell through to a `{% else %}` whose body was itself
    commented out (`##SBATCH --ntasks=4`), so c8g.medium, every 16xlarge, every
    metal variant other than three, and every future size emitted no --ntasks at
    all -- Slurm then defaults to one task and the operator's job silently runs
    on a single core of a machine they are paying for by the hour. The
    replacement reads EC2's own DefaultVCpus out of the describe_instance_types
    response make_pcluster.py already fetches for the architecture check.
    """

    def test_a_cpu_cluster_gets_its_own_core_count(self, cluster_params):
        directives = _sbatch_directives(_render_sbatch_default(cluster_params))
        assert directives["partition"] == "compute"
        assert directives["ntasks"] == str(cluster_params["cpu_ranks_per_node"])

    def test_the_partition_exists_in_the_cluster_config(
        self,
        cluster_params,
        cluster_params_gpu_enabled,
        cluster_params_gpu_queue_enabled,
        cluster_params_multi_instance_cpu,
        cluster_params_gpu_gdr_enabled,
        cluster_params_gpu_no_nvidia,
    ):
        """The script shipped no --partition at all, so it inherited Slurm's
        default. A GPU-only cluster has no `compute` queue, and the ladder keyed
        on compute_instance_type -- which is empty there -- so the fallback fired
        and there was no rank count either."""
        for params in (
            cluster_params,
            cluster_params_gpu_enabled,
            cluster_params_gpu_queue_enabled,
            cluster_params_multi_instance_cpu,
            cluster_params_gpu_gdr_enabled,
            cluster_params_gpu_no_nvidia,
        ):
            partition = _sbatch_directives(_render_sbatch_default(params))["partition"]
            defined = _defined_partitions(params)
            assert partition in defined, (
                f"script targets {partition!r} but the config defines {defined}"
            )

    def test_a_gpu_only_cluster_gets_a_core_count_not_a_gpu_count(
        self, cluster_params_gpu_enabled
    ):
        """This is a general-purpose job script, not the GPU benchmark driver.
        p3.2xlarge has 1 GPU and 8 vCPUs; using gpu_ranks_per_node here would
        request one task on an 8-core machine."""
        directives = _sbatch_directives(_render_sbatch_default(cluster_params_gpu_enabled))
        assert directives["partition"] == "gpu"
        assert directives["ntasks"] == "8", (
            "GPU-only clusters must get gpu_vcpus_per_node, not gpu_ranks_per_node"
        )

    def test_an_amd_gpu_cluster_still_gets_its_cores(self, cluster_params_gpu_no_nvidia):
        """g4ad reports zero NVIDIA devices, so gpu_ranks_per_node is 0 -- but the
        instance still has 16 vCPUs and `sbatch --ntasks=0` is rejected."""
        directives = _sbatch_directives(_render_sbatch_default(cluster_params_gpu_no_nvidia))
        assert directives["ntasks"] == "16"

    @pytest.mark.parametrize(
        "fixture_name",
        [
            "cluster_params",
            "cluster_params_gpu_enabled",
            "cluster_params_gpu_queue_enabled",
            "cluster_params_multi_instance_cpu",
            "cluster_params_gpu_gdr_enabled",
            "cluster_params_gpu_no_nvidia",
            "cluster_params_efa_enabled",
        ],
    )
    def test_every_configuration_emits_exactly_one_positive_ntasks(
        self, fixture_name, request
    ):
        """The bug was an absent directive, not a wrong one, so the property is
        that --ntasks is present, positive, and singular on every path."""
        params = request.getfixturevalue(fixture_name)
        rendered = _render_sbatch_default(params)
        active = [
            line
            for line in rendered.splitlines()
            if line.startswith("#SBATCH ") and "--ntasks=" in line
        ]
        assert len(active) == 1, f"expected one active --ntasks, found {active}"
        value = active[0].split("--ntasks=")[1].strip()
        assert value.isdigit() and int(value) > 0, f"--ntasks={value!r}"

    def test_no_instance_size_suffix_is_hardcoded(self):
        """The ladders are the defect. A new one for a size the old ones missed
        would reintroduce it one suffix at a time."""
        with open(
            os.path.join(REPO_ROOT, "scripts", "sbatch_default_submission_script.sh")
        ) as fh:
            source = fh.read()
        assert "split('.')" not in source and 'split(".")' not in source, (
            "the script derives a core count from the instance-type suffix again"
        )
        for suffix in ("2xlarge", "9xlarge", "18xlarge", "48xlarge", "metal-24xl"):
            assert f'"{suffix}"' not in source, (
                f"instance size {suffix!r} is hardcoded in the submission script"
            )

    def test_hyperthreading_halves_a_multithreaded_instance(self, cluster_params_efa_enabled):
        """c5n.18xlarge is 72 vCPUs at 2 threads per core. With
        DisableSimultaneousMultithreading the node presents 36, and requesting 72
        would leave the job pending forever."""
        ht = _sbatch_directives(_render_sbatch_default(cluster_params_efa_enabled))
        assert ht["ntasks"] == "72"
        no_ht = _sbatch_directives(
            _render_sbatch_default(
                {**cluster_params_efa_enabled, "hyperthreading": "false", "cpu_ranks_per_node": 36}
            )
        )
        assert no_ht["ntasks"] == "36"

    def test_the_rendered_script_is_valid_bash(
        self,
        cluster_params,
        cluster_params_gpu_enabled,
        cluster_params_gpu_queue_enabled,
    ):
        import subprocess

        for params in (
            cluster_params,
            cluster_params_gpu_enabled,
            cluster_params_gpu_queue_enabled,
        ):
            r = subprocess.run(
                ["bash", "-n"],
                input=_render_sbatch_default(params).encode(),
                capture_output=True,
            )
            assert r.returncode == 0, r.stderr

    def test_the_readme_documents_the_derivation_rather_than_a_fixed_partition(self):
        """README.md described this script for the life of the repo without ever
        mentioning either derived value, which is how two dead eleven-branch
        ladders and a missing --partition went unreported: an operator reading the
        docs had no reason to expect the script to know anything about their
        cluster, so a job silently running on one core looked like the default.

        Both variable names are asserted, because the GPU arm's is the one that is
        easy to get wrong -- gpu_ranks_per_node is an NVIDIA *device* count and
        belongs to the benchmark driver, not here -- and a README naming only the
        CPU side would read as complete.

        Every assertion is scoped to the Job Submission section rather than to the
        whole file.  README.md documents job_hpc-benchmark.sh's own derived
        directives a few sections down in nearly the same words, so a whole-file
        match is satisfied by the *other* section: deleting the reason the
        partition is derived at all ("no `compute` partition") from this section
        survived the first battery on the strength of the benchmark section's
        copy."""
        with open(os.path.join(REPO_ROOT, "README.md")) as fh:
            readme = fh.read()
        parts = readme.split("### Job Submission", 1)
        assert len(parts) == 2, "the Job Submission section was renamed"
        section = parts[1].split("\n### ", 1)[0]

        # The scoping is itself load-bearing, and widening it back to the whole
        # file is a mutation that passes on today's tree while re-permitting
        # every mutation the scoping exists to catch. So assert the extraction
        # actually narrowed: the benchmark section documents its own derived
        # directives in nearly these words and must not be inside this window.
        assert len(section) < len(readme) / 4, (
            f"the extracted section is {len(section)} of {len(readme)} characters; "
            f"the scope is not narrowed to Job Submission"
        )
        assert "### HPC Benchmarks" not in section, (
            "the extracted section runs into the HPC Benchmarks section, whose "
            "wording satisfies these assertions on its own"
        )
        assert "hpc-benchmark.sh install" not in section, (
            "the extracted section reaches the benchmark driver's own docs"
        )

        for expected in (
            "`--partition` and `--ntasks` are derived",
            "cpu_ranks_per_node",
            "gpu_vcpus_per_node",
            "DefaultThreadsPerCore",
            # Why the partition is derived at all, rather than merely that it is.
            "no `compute` partition",
            # This script asks for cores; the benchmark driver asks for NVIDIA
            # devices. Documenting them as the same thing is the exact bug
            # test_a_gpu_only_cluster_gets_a_core_count_not_a_gpu_count prevents
            # in the template.
            "not the same as the GPU benchmark",
        ):
            assert expected in section, (
                f"README.md's Job Submission section does not document "
                f"{expected!r}; the derivation is invisible to whoever reads the "
                f"docs for this script"
            )


# ---------------------------------------------------------------------------
# The build summary
#
# Two surfaces carry the storage topology: pcluster_core's printed summary
# (_storage_summary_lines, driven in tests/test_make_pcluster.py) and
# templates/sns_build_summary_report.j2, which is what the SNS audience sees
# and what these render. A third copy lived in create_pcluster.yml's
# _build_summary set_fact until that playbook was deleted.
# ---------------------------------------------------------------------------

# Runtime facts the report interpolates; the vars file knows nothing about them.
_RUNTIME_FACTS = {
    "head_node_public_ip": "1.2.3.4",
    "start_overall_timer": {"stdout": "2026-07-25 @ 20:09:46"},
    "start_stack_creation_timer": {"stdout": "2026-07-25 @ 20:10:20"},
    "stop_stack_creation_timer": {"stdout": "2026-07-25 @ 20:33:56"},
    "stop_overall_timer": {"stdout": "2026-07-25 @ 20:34:33"},
}

_STORAGE_COMBOS = [
    ({}, ["/shared"], ["/efs", "/fsx", "/nfs"]),
    ({"enable_fsx": "true"}, ["/shared", "/fsx"], ["/efs", "/nfs"]),
    ({"enable_efs": "true"}, ["/shared", "/efs"], ["/fsx", "/nfs"]),
    ({"enable_external_nfs": "true"}, ["/shared", "/nfs"], ["/efs", "/fsx"]),
    (
        {"enable_efs": "true", "enable_fsx": "true", "enable_external_nfs": "true"},
        ["/shared", "/efs", "/fsx", "/nfs"],
        [],
    ),
]


def _vars_for(overrides, cluster_params):
    """Render vars_file.j2 for a storage combination and load what it defines.

    Rendering rather than hand-writing the dict is the point: the summary
    expression is evaluated by Ansible against these variables, and EFS, FSx, and
    NFS variables exist in the file only inside their enable_* gates. Ansible
    evaluates the whole expression, so a reference to a gated variable on a
    cluster without that filesystem is an undefined-variable failure at the end
    of an otherwise successful 25-minute build.
    """
    params = dict(cluster_params)
    for key in ("enable_efs", "enable_fsx", "enable_external_nfs"):
        params[key] = overrides.get(key, "false")
    if params["enable_fsx"] != "true":
        params["enable_fsx_hydration"] = "false"
    rendered = _make_env(os.path.join(REPO_ROOT, "templates")).get_template(
        "vars_file.j2"
    ).render(**params)
    return yaml.safe_load(rendered)


def _rendered_sns(overrides, cluster_params):
    variables = {**cluster_params, **_vars_for(overrides, cluster_params),
                 **_RUNTIME_FACTS}
    # The timers are Ansible-registered objects in this template's context.
    variables.update(
        {k: type("R", (), {"stdout": v["stdout"]})()
         for k, v in _RUNTIME_FACTS.items() if isinstance(v, dict)}
    )
    return _make_env(os.path.join(REPO_ROOT, "templates")).get_template(
        "sns_build_summary_report.j2"
    ).render(**variables)


# The mount point alone is not enough to prove the filesystem was reported: on an
# FSx cluster pkg_dir is /fsx/pkg, so a bare "/fsx" check still passes after the
# mount line is deleted. Match the label the line carries, anchored from the
# mount point rightwards so the surface's own indentation does not matter.
_MOUNT_LINES = {
    "/shared": "/shared  EBS (",
    "/efs": "/efs     EFS (",
    "/fsx": "/fsx     FSx for Lustre (",
    "/nfs": "/nfs     external NFS (",
}


def _assert_storage(text, overrides, present, absent):
    label = overrides or "EBS only"
    assert "Shared storage:" in text
    for mount in present:
        assert _MOUNT_LINES[mount] in text, f"{label}: {mount} not reported"
    for mount in absent:
        assert mount not in text, (
            f"{label}: {mount} named on a cluster that has no such filesystem"
        )


# A class covering the playbook's own build-summary storage block lived here.
# It evaluated create_pcluster.yml's
# _build_summary expression over the same _STORAGE_COMBOS the SNS class below
# uses, and was also the only check that the expression never reached for a
# variable vars_file.j2 leaves undefined under its gate. The playbook is gone;
# the printed summary an operator actually reads is _storage_summary_lines in
# src/pcluster_core.py, driven over the same combinations in
# tests/test_make_pcluster.py (TestStorageSummaryLines,
# TestStorageSummaryLinesTakesKeywordsOnly).



class TestSnsBuildSummaryStorage:
    """The emailed report is the copy that outlives the terminal scrollback, so it
    carries the same storage topology as the printed summary."""

    @pytest.mark.parametrize("overrides,present,absent", _STORAGE_COMBOS)
    def test_the_report_names_every_filesystem_and_only_those(
        self, overrides, present, absent, cluster_params
    ):
        _assert_storage(
            _rendered_sns(overrides, cluster_params), overrides, present, absent
        )

    @pytest.mark.parametrize("overrides,present,absent", _STORAGE_COMBOS)
    def test_the_report_reports_the_vars_file_pkg_dir(
        self, overrides, present, absent, cluster_params
    ):
        pkg_dir = _vars_for(overrides, cluster_params)["pkg_dir"]
        assert f"install under {pkg_dir}" in _rendered_sns(overrides, cluster_params)

    def test_the_report_gives_lustre_its_size(self, cluster_params):
        overrides = {"enable_fsx": "true"}
        fsx_size = _vars_for(overrides, cluster_params)["fsx_size"]
        assert f"FSx for Lustre ({fsx_size} GB)" in _rendered_sns(
            overrides, cluster_params
        )

    def test_the_report_names_the_hydration_buckets_only_when_hydrating(
        self, cluster_params
    ):
        """The import/export S3 paths are the operator's only record of what the
        filesystem was seeded from; naming buckets on a cluster that never
        configured any would be worse than saying nothing."""
        hydrated = dict(cluster_params, enable_fsx_hydration="true")
        text = _rendered_sns({"enable_fsx": "true"}, hydrated)
        assert "S3 import: s3://" in text
        assert "S3 export: s3://" in text
        # osiris ran with FSx on and hydration off.
        plain = dict(cluster_params, enable_fsx_hydration="false")
        assert "S3 import:" not in _rendered_sns({"enable_fsx": "true"}, plain)


def _config_mount_dirs(overrides, cluster_params):
    """MountDir values out of the rendered cluster config's SharedStorage block."""
    variables = {**cluster_params, **_vars_for(overrides, cluster_params)}
    rendered = _make_env(os.path.join(REPO_ROOT, "templates")).get_template(
        "config.pcluster.j2"
    ).render(**variables)
    return [
        entry["MountDir"] for entry in yaml.safe_load(rendered)["SharedStorage"]
    ]


class TestSummaryMountPointsMatchTheClusterConfig:
    """/efs and /fsx are hardcoded in three places — config.pcluster.j2's
    SharedStorage block, _storage_summary_lines, and the SNS template — and
    vars_file.j2's efs_root/fsx_root variables are referenced by none of them.
    Nothing but this stops a mount point changed in the config from leaving both
    summaries pointing at a directory that does not exist. (There were four
    places until create_pcluster.yml's own copy of the summary was deleted with
    the playbook.) External NFS is absent from SharedStorage by design: PCluster
    does not manage it, postinstall.j2 mounts it at external_nfs_server_root, so
    that one is checked against the vars file instead."""

    @pytest.mark.parametrize("overrides,present,absent", _STORAGE_COMBOS)
    def test_every_pcluster_managed_mount_is_named_in_the_report(
        self, overrides, present, absent, cluster_params
    ):
        mounts = _config_mount_dirs(overrides, cluster_params)
        assert mounts, "SharedStorage parsed empty — the config template changed shape"
        sns = _rendered_sns(overrides, cluster_params)
        for mount in mounts:
            assert mount in _MOUNT_LINES, (
                f"config mounts {mount}, which no summary knows how to report"
            )
            assert _MOUNT_LINES[mount] in sns, f"{mount} missing from SNS report"

    @pytest.mark.parametrize("overrides,present,absent", _STORAGE_COMBOS)
    def test_the_report_claims_no_mount_the_config_does_not_create(
        self, overrides, present, absent, cluster_params
    ):
        managed = set(_config_mount_dirs(overrides, cluster_params))
        for mount, line in _MOUNT_LINES.items():
            if mount in managed or mount == "/nfs":
                continue
            assert line not in _rendered_sns(overrides, cluster_params)

    def test_the_external_nfs_mount_tracks_the_vars_file(self, cluster_params):
        overrides = {"enable_external_nfs": "true"}
        root = _vars_for(overrides, cluster_params)["external_nfs_server_root"]
        assert _MOUNT_LINES["/nfs"].startswith(root)
        assert root not in _config_mount_dirs(overrides, cluster_params)


class TestReportingSurfacesNameTheLoginNode:
    """Login node appears on several independently-maintained literals -- the
    pre-build launch summary and the post-build summary
    (print_cluster_launch_summary and _print_build_summary in
    src/pcluster_core.py, driven in tests/test_create_pcluster_migration.py and
    by TestTheNodeLinesReadAsOneGroup below), the SNS report template itself,
    plus list_pcluster.py's table and the cost estimate, covered elsewhere. A
    line added to one and missed on another is worse than not adding it
    anywhere, so each is checked independently. The two that read
    create_pcluster.yml's own literals went with that playbook.

    What is left here is the SNS report, which is the copy that outlives the
    terminal scrollback."""

    def test_the_sns_report_names_the_login_node_only_when_enabled(
        self, cluster_params, cluster_params_loginnode_enabled
    ):
        assert "Login Node Instance Type:" not in _rendered_sns({}, cluster_params)
        text = _rendered_sns({}, cluster_params_loginnode_enabled)
        assert (
            f"Login Node Instance Type: "
            f"{cluster_params_loginnode_enabled['loginnode_instance_type']} "
            f"(x{cluster_params_loginnode_enabled['loginnode_count']})"
        ) in text

    def test_the_sns_report_reports_the_pool_size(self, cluster_params_loginnode_pool):
        text = _rendered_sns({}, cluster_params_loginnode_pool)
        assert "(x3)" in text

    def test_the_sns_report_header_fields_share_one_column(
        self, cluster_params_loginnode_enabled
    ):
        """Login Node Instance Type: is one character longer than every other
        label in this block (Head Node Instance Type: included) -- a hardcoded
        per-line space count, the exact defect class CLAUDE.md documents as
        previously fixed for the storage-summary block a few lines below in
        this same file, put its value one column later than the rest. The
        column width must now be derived from the longest active label."""
        text = _rendered_sns({}, cluster_params_loginnode_enabled)
        lines = [
            ln for ln in text.splitlines()
            if ln.startswith((
                "Cluster Stack Name:",
                "AWS Availability Zone:",
                "HPC Scheduler Type:",
                "Head Node Instance Type:",
                "Login Node Instance Type:",
                "Compute Instance Type:",
            ))
        ]
        assert len(lines) == 6, f"expected all six header lines, got: {lines}"

        def _value_column(line):
            label, _, rest = line.partition(":")
            padding = len(rest) - len(rest.lstrip(" "))
            return len(label) + 1 + padding

        value_columns = {_value_column(ln) for ln in lines}
        assert len(value_columns) == 1, (
            f"header fields do not share one column: {lines}"
        )

    def test_the_sns_report_access_instructions_reflect_the_login_node_default(
        self, cluster_params, cluster_params_loginnode_enabled
    ):
        """access_cluster.py connects to the login node by default whenever
        enable_loginnode=true, so a report captioning ./access_cluster.py -N
        as unconditionally reaching the head node is misleading -- the
        ParallelCluster-CLI and raw-ssh methods next to it are still
        guaranteed head-node, so only the heading/caption around method (1)
        needs to change."""
        disabled_text = _rendered_sns({}, cluster_params)
        assert "Access the head node by any of the following:" in disabled_text

        enabled_text = _rendered_sns({}, cluster_params_loginnode_enabled)
        assert "Access the head node by any of the following:" not in enabled_text
        assert "-H" in enabled_text.split("Access the cluster", 1)[1][:120]
        assert "pcluster ssh --cluster-name" in enabled_text
        assert "(head node)" in enabled_text


class TestOnNodeConfiguredIsAMacroNotFourCopies:
    """config.pcluster.j2's OnNodeConfigured block (postinstall + user postinstall
    + conditional monitoring script) is identical in HeadNode, LoginNodes, and
    both SlurmQueues. It used to be four independent copies, and that is exactly
    how LoginNodes silently inherited HeadNode's OnNodeStart by copy-paste rather
    than the compute queues' narrower shape (see the adversarial-review bugfix).
    A shared Jinja2 macro makes that class of drift structurally impossible
    instead of merely fixed for now -- this class guards that the macro is
    actually used at all four sites, not just that the rendered output happens
    to look right today."""

    def test_the_macro_is_called_at_all_four_sites(self):
        with open(os.path.join(REPO_ROOT, "templates", "config.pcluster.j2")) as fh:
            source = fh.read()
        assert source.count("{% macro on_node_configured(") == 1, (
            "expected exactly one macro definition"
        )
        assert source.count("on_node_configured() | indent(") == 4, (
            "expected the macro to be called at all four CustomActions sites "
            "(HeadNode, LoginNodes, compute queue, GPU queue) -- a reintroduced "
            "literal OnNodeConfigured block would not show up here even if it "
            "rendered correctly"
        )

    def test_headnode_and_login_node_render_identical_on_node_configured_content(
        self, cluster_params_loginnode_enabled
    ):
        params = dict(
            cluster_params_loginnode_enabled,
            enable_gpu_queue="true",
            enable_gpu="true",
            gpu_instance_type="p3.2xlarge",
            gpu_instance_types=["p3.2xlarge"],
        )
        env = _make_env(os.path.join(REPO_ROOT, "templates"))
        rendered = env.get_template("config.pcluster.j2").render(**params)
        parsed = yaml.safe_load(rendered)
        head = parsed["HeadNode"]["CustomActions"]["OnNodeConfigured"]["Sequence"]
        login = parsed["LoginNodes"]["Pools"][0]["CustomActions"]["OnNodeConfigured"]["Sequence"]
        compute = parsed["Scheduling"]["SlurmQueues"][0]["CustomActions"]["OnNodeConfigured"]["Sequence"]
        gpu = parsed["Scheduling"]["SlurmQueues"][1]["CustomActions"]["OnNodeConfigured"]["Sequence"]
        assert head == login == compute == gpu


class TestLoginNodesConfigBlock:
    """LoginNodes is an optional top-level block, sibling to HeadNode/Scheduling,
    gated on enable_loginnode. It gets ComputeNode-Base IAM (never HeadNode-level
    privileges) and the compute-queue CustomActions shape -- OnNodeConfigured
    only, no OnNodeStart -- not HeadNode's. HeadNode is the only node type that
    runs preinstall.j2 (kernel-held apt/dnf upgrade, pip installs, a fresh
    awscli download); registering it as LoginNodes' OnNodeStart too would make
    every login node repeat all of that on every boot and every ASG
    replacement, exactly the "latency regression, not a fix" reasoning that
    already keeps it off the compute/GPU queues (root CLAUDE.md)."""

    def _config(self, params):
        env = _make_env(os.path.join(REPO_ROOT, "templates"))
        return env.get_template("config.pcluster.j2").render(**params)

    def test_disabled_by_default_renders_no_loginnodes_key(self, cluster_params):
        parsed = yaml.safe_load(self._config(cluster_params))
        assert "LoginNodes" not in parsed

    @pytest.mark.parametrize(
        "fixture_name", ["cluster_params_loginnode_enabled", "cluster_params_loginnode_pool"]
    )
    def test_the_pool_block_carries_every_required_field(self, fixture_name, request):
        params = request.getfixturevalue(fixture_name)
        parsed = yaml.safe_load(self._config(params))
        pool = parsed["LoginNodes"]["Pools"][0]

        assert pool["Name"] == params["cluster_name"]
        # Asserted against the fixture's own value, not a hardcoded 1, so this
        # still means something once loginnode_count=3 is exercised -- proves
        # the value threads through rather than the template happening to
        # always render 1.
        assert pool["Count"] == params["loginnode_count"]
        assert pool["InstanceType"] == params["loginnode_instance_type"]
        assert pool["Networking"]["SubnetIds"] == [params["loginnode_subnet_id"]]

        policies = pool["Iam"]["AdditionalIamPolicies"]
        assert any(
            p["Policy"].endswith("-ComputeNode-Base") for p in policies
        ), f"login node does not get ComputeNode-Base: {policies}"
        assert not any(
            "InstanceRole" in str(p) for p in policies
        ), "login node must not carry head-node-level IAM"

        assert "OnNodeStart" not in pool["CustomActions"], (
            "login nodes must not run preinstall.j2 -- that's HeadNode-only, "
            "same as the compute/GPU queues"
        )
        head_configured = parsed["HeadNode"]["CustomActions"]["OnNodeConfigured"]["Sequence"]
        login_configured = pool["CustomActions"]["OnNodeConfigured"]["Sequence"]
        assert [s["Script"] for s in login_configured] == [
            s["Script"] for s in head_configured
        ]

    def test_pool_count_actually_threads_through_not_just_defaults_to_one(
        self, cluster_params_loginnode_pool
    ):
        parsed = yaml.safe_load(self._config(cluster_params_loginnode_pool))
        assert parsed["LoginNodes"]["Pools"][0]["Count"] == 3

    def test_external_nfs_combined_with_loginnode_grants_the_security_group(
        self, cluster_params_loginnode_enabled
    ):
        """postinstall.j2's external-NFS mount block is gated only on
        enable_external_nfs, not on node type, so it also runs on the login
        node -- without this grant, sudo mount fails outright under
        set -euo pipefail because the login node's auto-created security
        group has no network path to the filer."""
        params = dict(cluster_params_loginnode_enabled, enable_external_nfs="true")
        parsed = yaml.safe_load(self._config(params))
        pool = parsed["LoginNodes"]["Pools"][0]
        assert pool["Networking"]["AdditionalSecurityGroups"] == [
            params["external_nfs_sg"]["group_id"]
        ]

    def test_pcluster_own_schema_accepts_the_rendered_pool(
        self, cluster_params_loginnode_enabled, monkeypatch
    ):
        """The authoritative check: load the rendered config through PCluster's
        own ClusterSchema. A key-casing typo is silently ignored by marshmallow
        rather than rejected, so a substring/yaml check alone would miss it."""
        from pcluster.schemas.cluster_schema import ClusterSchema

        monkeypatch.setenv("AWS_REGION", "us-east-1")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")

        params = dict(cluster_params_loginnode_enabled)
        params["subnet_id"] = "subnet-0abc1234"
        params["compute_subnet_ids"] = ["subnet-0abc1234"]
        params["gpu_subnet_ids"] = ["subnet-0abc1234"]
        params["loginnode_subnet_id"] = "subnet-0abc1234"
        params["external_nfs_sg"] = {"group_id": "sg-0abc1234"}
        parsed = yaml.safe_load(self._config(params))
        config = ClusterSchema(cluster_name="test-cluster").load(parsed)
        pool = config.login_nodes.pools[0]
        assert pool.name == params["cluster_name"]
        assert pool.count == params["loginnode_count"]
        assert pool.instance_type == params["loginnode_instance_type"]


class TestHeadNodeBootstrapTimeoutReachesTheClusterConfig:
    """PCluster starts the HeadNodeWaitCondition before the head node instance
    exists, and EFS/FSx provisioning sits on the head node's critical path, so a
    filesystem spends the bootstrap budget before preinstall runs. The osiris
    build failed exactly this way: FSx took 17m22s of the stock 2100s window.

    The only knob is DevSettings.Timeouts.HeadNodeBootstrapTimeout
    (cluster_stack.py:1249-1262 feeds it straight to CfnWaitCondition(timeout=)),
    so the value has to survive the render into a key PCluster actually reads --
    a substring match on the number cannot tell a correctly-nested block from one
    at the wrong depth or under a misspelled parent."""

    def _config(self, params):
        env = _make_env(os.path.join(REPO_ROOT, "templates"))
        return env.get_template("config.pcluster.j2").render(**params)

    def test_the_config_nests_the_timeout_where_pcluster_reads_it(
        self, cluster_params
    ):
        parsed = yaml.safe_load(self._config(cluster_params))
        assert "DevSettings" in parsed, "config renders no DevSettings section"
        timeouts = parsed["DevSettings"]["Timeouts"]
        assert timeouts["HeadNodeBootstrapTimeout"] == int(
            cluster_params["head_node_bootstrap_timeout"]
        )

    def test_the_rendered_value_is_an_integer_not_a_quoted_string(
        self, cluster_params
    ):
        """The schema field is fields.Int; a quoted value is a load-time failure."""
        parsed = yaml.safe_load(self._config(cluster_params))
        value = parsed["DevSettings"]["Timeouts"]["HeadNodeBootstrapTimeout"]
        assert isinstance(value, int), f"rendered as {type(value).__name__}"

    def test_pcluster_own_schema_accepts_the_rendered_block(
        self, cluster_params, monkeypatch
    ):
        """The authoritative check: load the rendered config through PCluster's own
        ClusterSchema and read the timeout back off the config object. This is what
        catches a wrong key casing -- BaseSchema.on_bind_field derives the YAML key
        with to_pascal_case(), so HeadNodeBootstrapTimeout is not a guess, and a
        typo here would be silently ignored by marshmallow rather than rejected."""
        from pcluster.schemas.cluster_schema import ClusterSchema

        # RootVolume.__init__ calls get_region().startswith("us-iso"), and
        # get_region() raises AWSClientError when boto3 resolves no region. Set
        # it explicitly rather than inheriting the operator's environment: a
        # developer with AWS_DEFAULT_REGION exported passes without this, which
        # is why CI was the first place it failed. Both names are set because
        # AWS_REGION takes precedence over AWS_DEFAULT_REGION in botocore, so
        # setting only the latter leaves an inherited AWS_REGION in charge.
        monkeypatch.setenv("AWS_REGION", "us-east-1")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")

        params = dict(cluster_params)
        # The fixture's IDs are 7 hex chars; PCluster's own patterns require 8 or
        # 17 (common_schema.py:36). Widen them so the schema rejects only what
        # this test is about.
        params["subnet_id"] = "subnet-0abc1234"
        params["compute_subnet_ids"] = ["subnet-0abc1234"]
        params["gpu_subnet_ids"] = ["subnet-0abc1234"]
        params["external_nfs_sg"] = {"group_id": "sg-0abc1234"}
        parsed = yaml.safe_load(self._config(params))
        config = ClusterSchema(cluster_name="test-cluster").load(parsed)
        assert config.dev_settings.timeouts.head_node_bootstrap_timeout == int(
            cluster_params["head_node_bootstrap_timeout"]
        )

    def test_the_vars_file_carries_the_timeout_for_the_playbook(
        self, cluster_params
    ):
        env = _make_env(os.path.join(REPO_ROOT, "templates"))
        rendered = env.get_template("vars_file.j2").render(**cluster_params)
        parsed = yaml.safe_load(rendered)
        assert parsed["head_node_bootstrap_timeout"] == int(
            cluster_params["head_node_bootstrap_timeout"]
        )

    def test_the_defaults_file_ships_pclusters_own_default(self):
        """Left at 2100, make_pcluster.py extends it for EFS/FSx. Shipping any other
        value in the defaults file disables the auto-bump for every cluster, since
        _derive_head_node_bootstrap_timeout treats a non-default as operator intent."""
        with open(os.path.join(REPO_ROOT, "pcluster_defaults.yml")) as fh:
            defaults = yaml.safe_load(fh)
        assert defaults["head_node_bootstrap_timeout"] == 2100


class TestTheLogGroupExpiresOnOurScheduleNotPClusters:
    """The cluster's CloudWatch log group is still retained on teardown -- that is
    CloudWatchLogs.__init__'s deletion_policy default of "Retain", which the
    toolkit deliberately does not override, because the group is the only
    surviving record of a failed build and a failed build is immediately followed
    by a teardown.

    What is no longer inherited is how long it lives. The block set `Enabled: true`
    and nothing else, so retention fell through to PCluster's
    CW_LOGS_RETENTION_DAYS_DEFAULT of 180 days; diagnosing a failed build is a
    short-horizon activity, so `RetentionInDays: 30` covers it and cuts the
    accumulation the operator has to purge by hand.

    A substring match on "30" cannot tell a correctly-nested key from one at the
    wrong depth or under a misspelled parent, and marshmallow ignores an unknown
    key rather than rejecting it -- so the value is read out of the parsed YAML,
    and again off PCluster's own loaded config object.
    """

    _OURS = 30

    def _cloudwatch_logs(self, params):
        env = _make_env(os.path.join(REPO_ROOT, "templates"))
        parsed = yaml.safe_load(
            env.get_template("config.pcluster.j2").render(**params)
        )
        return parsed["Monitoring"]["Logs"]["CloudWatch"]

    def test_the_rendered_config_sets_a_thirty_day_retention(self, cluster_params):
        block = self._cloudwatch_logs(cluster_params)
        assert block["RetentionInDays"] == self._OURS, (
            f"Monitoring/Logs/CloudWatch renders {block!r}"
        )

    def test_the_key_is_present_so_pclusters_default_cannot_apply(
        self, cluster_params
    ):
        """Vacuity guard on the test above. A retention that silently reverts is
        not a wrong number in the config -- it is no key at all, and the rendered
        file then looks exactly as it did before this was a decision."""
        from pcluster.constants import CW_LOGS_RETENTION_DAYS_DEFAULT

        block = self._cloudwatch_logs(cluster_params)
        assert "RetentionInDays" in block, (
            "Monitoring/Logs/CloudWatch sets no RetentionInDays, so the group "
            f"silently expires at PCluster's default of "
            f"{CW_LOGS_RETENTION_DAYS_DEFAULT} days"
        )
        assert block["RetentionInDays"] != CW_LOGS_RETENTION_DAYS_DEFAULT, (
            "the rendered retention is PCluster's own default, which is what "
            "this block exists to override"
        )

    def test_the_rendered_value_is_an_integer_not_a_quoted_string(
        self, cluster_params
    ):
        """The schema field is fields.Int; a quoted value is a load-time failure."""
        value = self._cloudwatch_logs(cluster_params)["RetentionInDays"]
        assert isinstance(value, int), f"rendered as {type(value).__name__}"

    def test_the_deletion_policy_is_left_at_pclusters_retain_default(
        self, cluster_params
    ):
        """Shortening the lifetime must not turn into deleting the group. The
        toolkit sets no DeletionPolicy, so CloudWatchLogs.__init__'s "Retain"
        applies -- read back off PCluster rather than restated here."""
        from pcluster.config.cluster_config import CloudWatchLogs

        block = self._cloudwatch_logs(cluster_params)
        assert "DeletionPolicy" not in block, (
            f"the config sets DeletionPolicy: {block.get('DeletionPolicy')!r}; "
            "the retained-log-group rule expects PCluster's own default"
        )
        assert CloudWatchLogs().deletion_policy == "Retain"

    def test_thirty_is_a_value_pcluster_accepts(self):
        """Read out of the installed schema, never restated: retention_in_days is
        a validate.OneOf, so a PCluster that drops 30 from the set has to fail
        here rather than at cluster creation, twenty minutes into a build."""
        from marshmallow import validate
        from pcluster.schemas.cluster_schema import CloudWatchLogsSchema

        field = CloudWatchLogsSchema._declared_fields["retention_in_days"]
        choices = [
            v.choices for v in field.validators if isinstance(v, validate.OneOf)
        ]
        assert len(choices) == 1, (
            f"retention_in_days no longer carries exactly one OneOf: "
            f"{field.validators}"
        )
        assert self._OURS in list(choices[0]), (
            f"PCluster no longer accepts RetentionInDays: {self._OURS} -- "
            f"allowed values are {list(choices[0])}"
        )

    def test_the_retention_can_be_changed_on_a_running_cluster(self):
        """retention_in_days is UpdatePolicy.SUPPORTED while enabled is
        UNSUPPORTED, so this is a knob an operator can turn on a live cluster
        rather than a rebuild. The `enabled` half is the vacuity guard: it proves
        the two policies are actually distinguishable through this reading."""
        from pcluster.config.update_policy import UpdatePolicy
        from pcluster.schemas.cluster_schema import CloudWatchLogsSchema

        fields = CloudWatchLogsSchema._declared_fields
        assert (
            fields["retention_in_days"].metadata["update_policy"]
            is UpdatePolicy.SUPPORTED
        )
        assert (
            fields["enabled"].metadata["update_policy"] is UpdatePolicy.UNSUPPORTED
        )

    def test_pcluster_own_schema_reads_the_retention_back(
        self, cluster_params, monkeypatch
    ):
        """The authoritative check: load the rendered config through PCluster's own
        ClusterSchema. BaseSchema.on_bind_field derives the YAML key with
        to_pascal_case(), so a casing typo is silently ignored by marshmallow and
        the group quietly keeps the 180-day default."""
        from pcluster.schemas.cluster_schema import ClusterSchema

        # RootVolume.__init__ calls get_region(), which raises when boto3
        # resolves no region; AWS_REGION outranks AWS_DEFAULT_REGION in botocore,
        # so both are set rather than inherited from the operator's environment.
        monkeypatch.setenv("AWS_REGION", "us-east-1")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")

        params = dict(cluster_params)
        params["subnet_id"] = "subnet-0abc1234"
        params["compute_subnet_ids"] = ["subnet-0abc1234"]
        params["gpu_subnet_ids"] = ["subnet-0abc1234"]
        params["external_nfs_sg"] = {"group_id": "sg-0abc1234"}
        env = _make_env(os.path.join(REPO_ROOT, "templates"))
        parsed = yaml.safe_load(
            env.get_template("config.pcluster.j2").render(**params)
        )
        config = ClusterSchema(cluster_name="test-cluster").load(parsed)
        assert config.monitoring.logs.cloud_watch.retention_in_days == self._OURS


def _run_preinstall(cluster_params, base_os, kernel_pkgs=("linux-image-6.8.0-1021-aws",),
                    dpkg_rc=0, phantom_pkgs=(), phantom_dnf_pkgs=()):
    """Execute the rendered preinstall with every external command stubbed.

    Whether the kernel is actually held back is a runtime property of shell
    word-splitting and `set -e`, not a text property, so it is checked by running
    the script and recording the argv the package manager was handed.

    dpkg_rc fakes dpkg-query's exit status: it returns non-zero whenever ANY of
    its four patterns matches nothing, which is the common case (no AMI carries
    linux-image*, linux-headers*, linux-modules* AND linux-aws*), while still
    printing the packages that did match.

    phantom_pkgs are names dpkg knows but has not installed -- `dpkg-query -W`
    reports them with the same exit status as the real ones, and handing one to
    `apt-mark hold` exits 100. The stub emits the `${db:Status-Status}` column so
    the awk filter that drops them is the code under test, not the stub. awk is
    deliberately left unstubbed for the same reason.

    phantom_dnf_pkgs is the same idea on the rhel9 arm: the dnf stub exits 1 with
    "Unable to find a match" for any of these, so the RHEL package lines are
    assertable rather than trivially green. base_os selects which arm renders at
    all, so a caller passing an Ubuntu base_os never reaches the dnf stub.
    """
    import subprocess
    import tempfile

    params = dict(cluster_params)
    params["base_os"] = base_os
    env = _make_env(os.path.join(REPO_ROOT, "templates"))
    rendered = env.get_template("preinstall.j2").render(**params)
    with tempfile.TemporaryDirectory() as tmp:
        # Honor dpkg-query's -f format string rather than assuming it. Real
        # dpkg-query prints only the fields asked for, so a template that drops
        # ${db:Status-Status} gets bare names back and the awk filter then matches
        # a package name against "installed" and emits nothing -- apt-mark is
        # handed an empty list. A stub that prints the status column unconditionally
        # cannot tell that mutation from the fix.
        with_status = "".join(
            f'echo "installed {p}"\n' for p in kernel_pkgs
        ) + "".join(f'echo "not-installed {p}"\n' for p in phantom_pkgs)
        names_only = "".join(
            f'echo "{p}"\n' for p in tuple(kernel_pkgs) + tuple(phantom_pkgs)
        )
        listing = (
            f'case "$*" in\n'
            f'    *db:Status-Status*) {with_status or ":"} ;;\n'
            f'    *) {names_only or ":"} ;;\n'
            f'esac\n'
        )
        # The rendered script's own `set -euo pipefail` is on line 2, which the
        # split below discards along with the shebang. It has to be restored
        # explicitly: without it the `|| true` guard on the dpkg-query assignment
        # has nothing to guard against, and removing that guard is a mutation the
        # suite would pass. Assert it is still there rather than assuming.
        assert rendered.splitlines()[1] == "set -euo pipefail", (
            "preinstall.j2 no longer sets -euo pipefail on line 2; the harness "
            "restores it by position and would silently stop matching the node"
        )
        harness = f"""
        set -euo pipefail
        export TRACE={tmp}/trace
        : > "$TRACE"
        _log() {{ echo "$*" >> "$TRACE"; }}
        sudo() {{ _log "sudo $*"; "$@" >/dev/null 2>&1 || return 0; }}
        dpkg-query() {{ _log "dpkg-query $*"; {listing}return {dpkg_rc}; }}
        # Faithful to dnf: exit 1 with the package named when it cannot be found.
        # A stub that always returned 0 would make an rhel9 render look healthy no
        # matter what it asked for, and every RHEL assertion here vacuous.
        _DNF_PHANTOMS="{' '.join(phantom_dnf_pkgs)}"
        dnf() {{
            _log "dnf $*"
            local _a _p
            for _a in "$@"; do
                for _p in $_DNF_PHANTOMS; do
                    if [ "$_a" = "$_p" ]; then
                        echo "Error: Unable to find a match: $_a" >&2
                        return 1
                    fi
                done
            done
            return 0
        }}
        for _c in apt-get pip3 pip curl unzip rm uname tee; do
            eval "$_c() {{ _log \\"$_c \\$*\\"; return 0; }}"
        done
        # Faithful to the real apt-mark: exit 100 if any name is not installed.
        # A stub that always returns 0 cannot see the bug this models.
        _PHANTOMS="{' '.join(phantom_pkgs)}"
        apt-mark() {{
            _log "apt-mark $*"
            local _a _p
            for _a in "$@"; do
                for _p in $_PHANTOMS; do
                    if [ "$_a" = "$_p" ]; then
                        echo "E: Can't select installed nor candidate version" \\
                             "from package '$_a' as it has neither of them" >&2
                        return 100
                    fi
                done
            done
            return 0
        }}
        uname() {{ echo x86_64; }}
        cd {tmp}
        """
        script = os.path.join(tmp, "preinstall.sh")
        with open(script, "w") as fh:
            fh.write(harness + "\n" + rendered.split("\n", 2)[2])
        r = subprocess.run(["bash", script], capture_output=True, cwd=tmp)
        trace = open(os.path.join(tmp, "trace")).read()
    return r, trace


class TestPreinstallNeverReplacesTheKernel:
    """The osiris build timed out because a full package upgrade on the PCluster
    AMI of the day crossed a kernel boundary and the initramfs rebuild had not
    finished when CloudFormation gave up. That base_os is gone, but apt has the
    same hazard and the same fix.

    The upgrade itself is kept deliberately: preinstall installs python3-dev, and
    numpy/scipy/pandas compile from source wherever pip finds no aarch64 wheel,
    which the two ARM base_os values hit. What is excluded is the kernel -- both
    because the rebuild's runtime is unbounded inside the bootstrap window and
    because PCluster's AMI ships EFA and Lustre modules built against the kernel
    it boots, so replacing it without rebuilding them loses the interconnect."""

    _UBUNTU = "ubuntu2404"

    def test_ubuntu_holds_the_kernel_before_dist_upgrade(self, cluster_params):
        r, trace = _run_preinstall(cluster_params, self._UBUNTU)
        assert r.returncode == 0, f"preinstall aborts on Ubuntu: {r.stderr.decode()}"
        lines = trace.splitlines()
        held = [i for i, ln in enumerate(lines) if "apt-mark hold" in ln]
        upgraded = [i for i, ln in enumerate(lines) if "dist-upgrade" in ln]
        assert held, "no apt-mark hold ran; dist-upgrade would replace the kernel"
        assert upgraded, "dist-upgrade is gone; option (b) keeps the upgrade"
        assert held[0] < upgraded[0], (
            "apt-mark hold runs AFTER dist-upgrade, so the kernel is already "
            "replaced by the time it is held"
        )

    def test_the_hold_names_the_kernel_packages_it_found(self, cluster_params):
        """An unquoted $_kernel_pkgs is what makes the multi-package case work; a
        quoted one passes all of them to apt-mark as a single argument."""
        pkgs = ("linux-image-6.8.0-1021-aws", "linux-headers-6.8.0-1021-aws")
        _, trace = _run_preinstall(cluster_params, self._UBUNTU, kernel_pkgs=pkgs)
        hold = [ln for ln in trace.splitlines() if ln.startswith("sudo apt-mark hold")]
        assert len(hold) == 1, f"expected one apt-mark hold, got {hold}"
        for pkg in pkgs:
            assert pkg in hold[0], f"{pkg} was not held: {hold[0]!r}"
        # Separate argv entries, not one concatenated string: proves $_kernel_pkgs
        # is unquoted at the call site.
        assert hold[0].split()[3:] == list(pkgs), (
            f"packages did not arrive as separate arguments: {hold[0]!r}"
        )

    def test_a_partial_dpkg_query_match_still_holds_what_it_found(
        self, cluster_params
    ):
        """dpkg-query exits non-zero when ANY pattern matches nothing -- the normal
        case, since no AMI carries all four -- but still prints the real matches.
        Without `|| true` the script aborts under `set -e` and the node fails
        bootstrap; without the -n guard it would hand apt-mark an empty list."""
        r, trace = _run_preinstall(
            cluster_params, self._UBUNTU,
            kernel_pkgs=("linux-image-6.8.0-1021-aws",), dpkg_rc=1,
        )
        assert r.returncode == 0, (
            "preinstall aborts when dpkg-query exits non-zero on a partial match; "
            f"the `|| true` guard is gone.\nstderr: {r.stderr.decode()}"
        )
        hold = [ln for ln in trace.splitlines() if "apt-mark hold" in ln]
        assert hold and "linux-image-6.8.0-1021-aws" in hold[0], (
            f"the packages dpkg-query did find were not held: {trace!r}"
        )

    def test_no_kernel_packages_found_is_not_an_error(self, cluster_params):
        """A minimal or container-built image may list none. apt-mark with an empty
        argument list is a usage error, so the -n guard has to skip it."""
        r, trace = _run_preinstall(
            cluster_params, self._UBUNTU, kernel_pkgs=(), dpkg_rc=1
        )
        assert r.returncode == 0, (
            f"preinstall aborts when no kernel packages exist: {r.stderr.decode()}"
        )
        assert not [ln for ln in trace.splitlines() if "apt-mark hold" in ln], (
            "apt-mark hold ran with an empty package list"
        )

    # Exactly what `dpkg-query -W 'linux-image*' 'linux-headers*' 'linux-modules*'
    # 'linux-aws*'` returned on osiris's ubuntu2404 head node (i-0000000000000007,
    # 2026-07-27): 20 names, 11 of them not-installed.
    _AMI_INSTALLED = (
        "linux-aws",
        "linux-aws-6.17-headers-6.17.0-1015",
        "linux-aws-6.17-tools-6.17.0-1015",
        "linux-headers-6.17.0-1015-aws",
        "linux-headers-aws",
        "linux-image-6.17.0-1015-aws",
        "linux-image-aws",
        "linux-modules-6.17.0-1015-aws",
        "linux-modules-extra-6.17.0-1015-aws",
    )
    _AMI_PHANTOMS = (
        "linux-aws-6.17-doc-6.17.0",
        "linux-aws-6.17-source-6.17.0",
        "linux-aws-6.17-tools",
        "linux-headers",
        "linux-headers-3.0",
        "linux-headers-686-pae",
        "linux-headers-amd64",
        "linux-headers-generic",
        "linux-image",
        "linux-image-unsigned-6.17.0-1015-aws",
        "linux-modules-extra-aws",
    )

    def test_uninstalled_kernel_packages_are_never_handed_to_apt_mark(
        self, cluster_params
    ):
        """`dpkg-query -W` reports names dpkg merely knows about, and `apt-mark
        hold` exits 100 on any of them ("E: Can't select installed nor candidate
        version"). The `|| true` guard covers dpkg-query, not apt-mark, so that 100
        propagates under `set -e` and fails the node's bootstrap -- which is what
        killed osiris with OnNodeStartExecutionFailure, return code 100, on
        2026-07-27. Only the installed rows may reach apt-mark."""
        r, trace = _run_preinstall(
            cluster_params,
            self._UBUNTU,
            kernel_pkgs=self._AMI_INSTALLED,
            phantom_pkgs=self._AMI_PHANTOMS,
            dpkg_rc=1,
        )
        assert r.returncode == 0, (
            "preinstall aborts when dpkg-query lists not-installed kernel "
            "packages -- apt-mark hold exits 100 and set -e fails the node "
            f"bootstrap.\nstderr: {r.stderr.decode()}"
        )
        hold = [ln for ln in trace.splitlines() if ln.startswith("sudo apt-mark hold")]
        assert len(hold) == 1, f"expected one apt-mark hold, got {hold}"
        argv = hold[0].split()[3:]
        for phantom in self._AMI_PHANTOMS:
            assert phantom not in argv, (
                f"not-installed package {phantom!r} was handed to apt-mark hold; "
                "the ${db:Status-Status} filter is gone"
            )
        assert argv == list(self._AMI_INSTALLED), (
            f"the installed kernel packages were not held verbatim: {argv}"
        )

    def test_the_harness_apt_mark_stub_actually_rejects_a_phantom(
        self, cluster_params
    ):
        """Guards the test above from passing vacuously. If the stub returned 0
        regardless, removing the status filter would still look green -- so prove
        the stub fails when a phantom does reach it."""
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "s.sh")
            with open(script, "w") as fh:
                fh.write(
                    'set -euo pipefail\n'
                    'TRACE=%s/t\n: > "$TRACE"\n'
                    '_log() { echo "$*" >> "$TRACE"; }\n'
                    '_PHANTOMS="linux-headers-3.0"\n'
                    'apt-mark() {\n'
                    '    _log "apt-mark $*"\n'
                    '    local _a _p\n'
                    '    for _a in "$@"; do\n'
                    '        for _p in $_PHANTOMS; do\n'
                    '            if [ "$_a" = "$_p" ]; then return 100; fi\n'
                    '        done\n'
                    '    done\n'
                    '    return 0\n'
                    '}\n'
                    'apt-mark hold linux-image-real linux-headers-3.0\n'
                    % tmp
                )
            r = subprocess.run(["bash", script], capture_output=True, cwd=tmp)
        assert r.returncode == 100, (
            f"the apt-mark stub does not model exit 100; got {r.returncode}"
        )

    def test_pip_is_never_upgraded_over_the_distro_pip(self, cluster_params):
        """Debian's python3-pip ships a dist-info with no RECORD file, so pip cannot
        uninstall it -- `pip3 install --upgrade pip` dies at "Attempting uninstall".
        That failed osiris's head node on 2026-07-27 (return code 1), and it was
        only reachable at all because the apt-mark fix let execution get that far.
        The harness stubs pip3, so no runtime assertion can see this; it has to be
        checked against the source. `--ignore-installed` is the supported way to
        install over a dpkg-owned distribution."""
        with open(os.path.join(REPO_ROOT, "templates", "preinstall.j2")) as fh:
            body = "\n".join(
                ln for ln in fh.read().splitlines() if not ln.lstrip().startswith("#")
            )
        pip_lines = [
            ln for ln in body.splitlines() if re.search(r"\bpip3?\s+install\b", ln)
        ]
        assert pip_lines, "preinstall.j2 no longer installs anything with pip"
        for line in pip_lines:
            if not re.search(r"(^|\s)pip(\s|$|')", line.split("install", 1)[1]):
                continue
            assert "--upgrade" not in line, (
                "preinstall.j2 upgrades pip in place: "
                f"{line.strip()!r}\nDebian's pip has no RECORD file and cannot be "
                "uninstalled; use --ignore-installed instead."
            )
            assert "--ignore-installed" in line, (
                "the pip self-install must pass --ignore-installed so it does not "
                f"attempt to uninstall the dpkg-owned pip: {line.strip()!r}"
            )

    def test_postinstall_never_dist_upgrades_without_a_hold(self, cluster_params):
        """postinstall.j2 runs its own `apt-get update` on the head-node package
        path. Fixing only preinstall leaves the same initramfs rebuild one stage
        later, so postinstall must not upgrade the kernel at all: it installs
        named packages and never dist-upgrades."""
        env = _make_env(os.path.join(REPO_ROOT, "templates"))
        params = dict(cluster_params)
        params["base_os"] = self._UBUNTU
        rendered = env.get_template("postinstall.j2").render(**params)
        for line in rendered.splitlines():
            stripped = line.strip()
            if "dist-upgrade" in stripped or "full-upgrade" in stripped:
                assert "apt-mark hold" in rendered, (
                    f"postinstall upgrades the kernel with no hold in place: "
                    f"{stripped!r} -- this is the initramfs rebuild that timed "
                    f"out cluster osiris, one stage later"
                )

    # Both Lustre spellings, on both dnf distros. RHEL packages the kernel module
    # as kmod-lustre* and AL2023 packages the client as lustre-client with no
    # kmod-lustre* in its repo at all, so a single-spelling list is inert on one of
    # the two -- a flag that is present and protects nothing, which is exactly the
    # failure mode this class exists to prevent.
    _DNF_EXCLUDES = ("--exclude='kernel*'", "--exclude='kmod-lustre*'",
                     "--exclude='lustre-client*'", "--exclude='efa*'")

    @pytest.mark.parametrize("fixture", ["cluster_params_rhel", "cluster_params_al2023"])
    @pytest.mark.parametrize("template", ["preinstall.j2", "postinstall.j2"])
    def test_dnf_arms_exclude_the_kernel_from_every_update(
        self, request, fixture, template
    ):
        """The dnf arms carry the same hazard as the apt arm and the same measured
        failure behind it: a full `dnf update` on the RHEL 9 PCluster AMI crossed
        5.14.0-611.55.1.el9_7 -> 5.14.0-687.30.1.el9_8 and the dracut rebuild was
        still running when CloudFormation gave up.

        `--exclude` is a name glob resolved at depsolve time, so unlike the apt
        path it needs no package enumeration -- there is no dpkg-query status
        filter, no phantom-package problem, and no `|| true` to guard. That also
        means nothing about it is observable in the execution trace: `dnf` is
        stubbed, so a trace assertion cannot tell a flag that was passed from one
        that was honored. The rendered text is what is asserted on, and it must be
        the *rendered* text -- a line inside an unexpanded `{% if %}` is not a line
        any node runs. Both templates update, so both are covered; fixing only
        preinstall leaves the same rebuild one stage later.

        Parametrized over both dnf distros because they do not share every update
        line: preinstall's arm is shared, but postinstall's critical-package block
        splits, so an exclude dropped from the AL2023 half alone is invisible to a
        RHEL-only run."""
        params = request.getfixturevalue(fixture)
        rendered = _make_env(
            os.path.join(REPO_ROOT, "templates")
        ).get_template(template).render(**params)
        updates = [
            ln.strip()
            for ln in rendered.splitlines()
            if not ln.strip().startswith("#")
            and re.search(r"\bdnf\b", ln)
            and re.search(r"\bupdate\b", ln)
        ]
        assert updates, (
            f"{template} renders no `dnf ... update` line on the "
            f"{params['base_os']} arm -- this test has gone vacuous; if the "
            "upgrade was deliberately removed, delete this test rather than "
            "leaving it asserting over nothing"
        )
        for line in updates:
            for flag in self._DNF_EXCLUDES:
                assert flag in line, (
                    f"{template}'s dnf update on {params['base_os']} is missing "
                    f"{flag}: {line!r} -- a kernel bump here rebuilds the "
                    "initramfs inside CloudFormation's bootstrap window, and "
                    "PCluster's EFA and Lustre modules are built against the "
                    "kernel the AMI boots"
                )


class TestNoPipInstallEverUninstallsADistroPackage:
    """pip cannot uninstall a distribution whose dist-info carries no RECORD file,
    and distro-packaged Python modules routinely ship exactly that. Any `pip3
    install` that resolves to replacing one reaches "Attempting uninstall: <name>"
    and exits 1, which under `set -euo pipefail` fails the node's bootstrap.

    This has now shipped twice, on two different lines of the same file. Session 26
    fixed the pip self-install (`--upgrade` over Debian's dpkg-owned pip, osiris
    head node, 2026-07-27). The *dependency* line two lines below it was left
    without the flag, and on 2026-07-28 the first live RHEL 9 build died there:
    'requests>=2.31,<3' against the RPM-owned python3-requests-2.25.1, whose
    dist-info was confirmed on head node i-0000000000000020 to hold only
    INSTALLER, LICENSE, METADATA, WHEEL and top_level.txt. numpy had already been
    uninstalled by the time it aborted, so the node was left with less than the
    AMI shipped.

    So the guard is deliberately not "the pip line is safe" -- that predicate was
    true the whole time the cluster was failing. It is "no pip3 install anywhere on
    a node path omits --ignore-installed", asserted over the *rendered* text of
    both templates on both OS arms, since a line inside an unexpanded {% if %} is
    not a line any node runs and a raw-source scan cannot tell the difference.

    --break-system-packages is a separate flag solving a separate problem (PEP 668
    write permission) and does not make a distro-owned distribution uninstallable;
    it must not be accepted as a substitute. It also must never appear on the RHEL
    arm, whose pip predates PEP 668 and rejects it outright."""

    _ARMS = ("ubuntu2404", "rhel9")
    _TEMPLATES = ("preinstall.j2", "postinstall.j2")

    def _pip_lines(self, cluster_params, template, base_os):
        env = _make_env(os.path.join(REPO_ROOT, "templates"))
        params = dict(cluster_params)
        params["base_os"] = base_os
        params["pcluster_os"] = base_os.removesuffix("arm")
        rendered = env.get_template(template).render(**params)
        return [
            ln.strip()
            for ln in rendered.splitlines()
            if not ln.lstrip().startswith("#")
            and re.search(r"\bpip3?\s+install\b", ln)
        ]

    def test_every_rendered_pip_install_ignores_installed(self, cluster_params):
        seen = 0
        for template in self._TEMPLATES:
            for base_os in self._ARMS:
                for line in self._pip_lines(cluster_params, template, base_os):
                    seen += 1
                    assert "--ignore-installed" in line, (
                        f"{template} on {base_os} runs a pip install without "
                        f"--ignore-installed: {line!r}\npip will try to uninstall "
                        "any distro-owned package it decides to replace, and a "
                        "distro dist-info has no RECORD for it to uninstall "
                        "against -- that is the requests failure that took down "
                        "osiris's head node on 2026-07-28."
                    )
        assert seen >= 6, (
            f"expected at least six rendered pip install lines across "
            f"{self._TEMPLATES} x {self._ARMS}, found {seen} -- the templates "
            "stopped installing with pip, or a branch stopped expanding, and this "
            "test is no longer checking anything"
        )

    def test_no_pip_install_is_ever_an_in_place_upgrade(self, cluster_params):
        """--upgrade re-introduces the uninstall step that --ignore-installed
        exists to skip, so the two must never appear on the same line."""
        for template in self._TEMPLATES:
            for base_os in self._ARMS:
                for line in self._pip_lines(cluster_params, template, base_os):
                    assert "--upgrade" not in line and " -U" not in line, (
                        f"{template} on {base_os} upgrades in place: {line!r}\n"
                        "--upgrade uninstalls the existing distribution first, "
                        "which fails on any distro-owned dist-info."
                    )

    def test_break_system_packages_is_not_treated_as_the_fix(self, cluster_params):
        """The two flags are unrelated, and the RHEL arm cannot even accept the
        PEP 668 one. Pinning this keeps a future fix from reaching for the wrong
        flag and keeps --break-system-packages off the arm that rejects it."""
        for template in self._TEMPLATES:
            for line in self._pip_lines(cluster_params, template, "rhel9"):
                assert "--break-system-packages" not in line, (
                    f"{template}'s RHEL arm passes --break-system-packages: "
                    f"{line!r}\nRHEL 9's pip predates PEP 668 and rejects the flag."
                )
            for line in self._pip_lines(cluster_params, template, "ubuntu2404"):
                if "--break-system-packages" in line:
                    assert "--ignore-installed" in line, (
                        f"{template}'s Ubuntu arm relies on "
                        f"--break-system-packages alone: {line!r}\nThat grants "
                        "write access to the system tree; it does not make a "
                        "dpkg-owned distribution uninstallable."
                    )


class TestPackageManagersMatchTheRenderedOs:
    """Ubuntu and rhel9 are both supported, so preinstall.j2 and postinstall.j2
    branch on `'ubuntu' in base_os` and reach for apt or dnf accordingly. The
    hazard is no longer "any dnf at all" but a *mismatch*: an apt call that
    survives on the RHEL arm, or a dnf call on the Ubuntu arm, is a node that dies
    at the first package install ninety seconds in, and under `set -euo pipefail`
    on a compute node that costs ten relaunches and a PROTECTED cluster.

    RHEL was removed once and re-added: the removal cause was `ansible>=9,<10` in
    preinstall's pip list, unsatisfiable on that AMI's Python 3.9 via
    ansible-core 2.16's requires_python >= 3.10. Nothing on a node imports ansible,
    so the pin was deleted from both arms rather than branched around --
    test_no_arm_installs_ansible is what keeps it gone.

    Every check asserts the *supported set* by equality, not the absence of
    remembered spellings. A blocklist is exactly as wide as whoever wrote it
    remembered: four other distro names passed the first version of these tests,
    as did a single-quoted entry in the argparse list, since the assertion was on
    the double-quoted spelling."""

    _SUPPORTED_OSES = {
        "ubuntu2204",
        "ubuntu2404",
        "ubuntu2204arm",
        "ubuntu2404arm",
        "rhel9",
        "rhel9arm",
        "alinux2023",
        "alinux2023arm",
    }

    # Sampled rejects for the paths where equality cannot be asserted directly
    # (a function that takes a string can only be probed with strings). Spread
    # across families and release-numbering styles on purpose; the equality
    # assertions above are what actually close the set.
    #
    # alinux2, alinux2arm, and amzn2 are the sharpest entries here now that AL2023
    # is supported: postinstall.j2 branches on `'alinux' in base_os`, which matches
    # alinux2 as readily as alinux2023, so a template renders the AL2023 package
    # line for a distro whose repos do not carry it.  Nothing downstream of
    # _resolve_ec2_user and the playbook's index-0 assert would refuse the value.
    _UNSUPPORTED_OSES = (
        "rhel8",
        "rhel8arm",
        "rhel10",
        "centos7",
        "alinux2",
        "alinux2arm",
        "alinux2023arm2",
        "amzn2",
        "amzn2023",
        "rocky9",
    )

    # Two templates was too narrow a scope: a package-manager branch added to
    # monitoring-post-install-wrapper.j2 reaches the node just as directly, and
    # it passed. The two playbooks were on this list until they were deleted;
    # nothing in the toolkit runs an Ansible task on a node any more.
    _NODE_SURFACES = (
        os.path.join("templates", "preinstall.j2"),
        os.path.join("templates", "postinstall.j2"),
        os.path.join("templates", "monitoring-post-install-wrapper.j2"),
    )

    # Word-boundary regexes, not `"dnf "`. The space-suffixed literal missed
    # `sudo dnf\t-y install`, a trailing `dnf\` line continuation, and `dnf` at
    # end of line; `microdnf`, `rpm -i`, `zypper`, and `apk` matched nothing at all.
    _RPM_MANAGERS = re.compile(
        r"\b(dnf|microdnf|yum|yum-config-manager|zypper|apk|rpm|rpmbuild|"
        r"subscription-manager)\b"
    )
    _DEB_MANAGERS = re.compile(
        r"\b(apt|apt-get|apt-cache|apt-mark|aptitude|dpkg|dpkg-query|"
        r"add-apt-repository)\b"
    )

    def _rendered(self, name, params):
        return _make_env(os.path.join(REPO_ROOT, "templates")).get_template(
            name
        ).render(**params)

    def test_no_unbranched_surface_invokes_either_package_manager(self):
        """monitoring-post-install-wrapper.j2 and the two playbooks have no
        `'ubuntu' in base_os` branch at all, so any package-manager call in them
        runs on whichever OS the operator chose and is wrong on one of them. The
        raw source is what is asserted on: those files carry no OS conditional, so
        there is nothing a render could reveal that the text does not."""
        for rel in self._NODE_SURFACES:
            if rel.endswith(("preinstall.j2", "postinstall.j2")):
                continue
            with open(os.path.join(REPO_ROOT, rel)) as fh:
                source = fh.read()
            assert "base_os" not in source or rel.endswith(".yml"), (
                f"{rel} now references base_os; if it branches on the OS it "
                f"belongs in the rendered-arm checks below instead"
            )
            for line in source.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                for rx, family in ((self._RPM_MANAGERS, "RPM"), (self._DEB_MANAGERS, "deb")):
                    hit = rx.search(line)
                    assert not hit, (
                        f"{rel} invokes the {family} package manager "
                        f"{hit.group(1)!r}: {stripped!r}. This file has no "
                        f"`'ubuntu' in base_os` branch, so that call reaches every "
                        f"node regardless of base_os and is wrong on one family"
                    )

    @pytest.mark.parametrize(
        "fixture,wrong_family,wrong_rx",
        [
            ("cluster_params", "RPM", _RPM_MANAGERS),
            ("cluster_params_gpu_queue_enabled", "RPM", _RPM_MANAGERS),
            ("cluster_params_rhel", "deb", _DEB_MANAGERS),
            ("cluster_params_rhel_gpu_queue", "deb", _DEB_MANAGERS),
            ("cluster_params_al2023", "deb", _DEB_MANAGERS),
            ("cluster_params_al2023_gpu_queue", "deb", _DEB_MANAGERS),
        ],
    )
    def test_neither_arm_renders_the_other_familys_package_manager(
        self, request, fixture, wrong_family, wrong_rx
    ):
        """The rendered script is what the node receives, so this is the check that
        an `{% else %}` boundary is in the right place. Both a RHEL and an Ubuntu
        fixture are required: while every fixture was Ubuntu, a `{% if 'ubuntu' not
        in base_os %}` branch never expanded and a rendered-text assertion passed
        with the wrong-family call still sitting in the file. The GPU variants are
        required for the same reason at one level down -- the nvtop/htop install
        sits inside `{% if enable_gpu == 'true' %}`, which the plain fixtures leave
        false."""
        params = request.getfixturevalue(fixture)
        for name in ("preinstall.j2", "postinstall.j2"):
            for line in self._rendered(name, params).splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                hit = wrong_rx.search(line)
                assert not hit, (
                    f"{name} rendered for base_os={params['base_os']} invokes the "
                    f"{wrong_family} package manager {hit.group(1)!r}: {stripped!r}"
                )

    # Packages only the arm's *own main package block* installs, per template.
    # A bare command name is too coarse: postinstall's GPU block runs
    # `dnf -y install nvtop htop`, which puts dnf in the trace, so deleting the
    # entire critical-packages block -- leaving an rhel9 head node with no gcc,
    # git, lua, lua-devel, nfs-utils or EPEL, and therefore no Lmod -- passed
    # the whole suite. Each name below appears on exactly one apt/dnf line.
    #
    # Keyed by (manager, template), which still holds now that two distros share
    # the dnf arm: the four dnf sentinels are on the AL2023 lines as well as the
    # RHEL ones. That is not an accident to preserve blindly -- if a future arm
    # drops one of them, this dict needs a third key rather than a looser check,
    # since a sentinel absent from one distro's line makes that arm's positive
    # half unprovable.
    _SENTINEL_PACKAGES = {
        "apt-get": {
            "preinstall.j2": ("python3-dev", "unzip"),
            "postinstall.j2": ("liblua5.4-dev", "nfs-common"),
        },
        "dnf": {
            "preinstall.j2": ("python3-devel", "unzip"),
            "postinstall.j2": ("lua-devel", "nfs-utils"),
        },
    }

    @staticmethod
    def _installed_packages(trace, manager):
        """Every package name handed to `<manager> ... install` in the trace."""
        pkgs = set()
        for line in trace.splitlines():
            if manager not in line or " install" not in line:
                continue
            tail = line.split(" install", 1)[1]
            pkgs.update(w for w in tail.split() if not w.startswith("-"))
        return pkgs

    @pytest.mark.parametrize(
        "fixture,gpu_fixture,expected,forbidden",
        [
            ("cluster_params", "cluster_params_gpu_queue_enabled", "apt-get", _RPM_MANAGERS),
            ("cluster_params_rhel", "cluster_params_rhel_gpu_queue", "dnf", _DEB_MANAGERS),
            ("cluster_params_al2023", "cluster_params_al2023_gpu_queue", "dnf", _DEB_MANAGERS),
        ],
    )
    def test_no_node_type_executes_the_wrong_package_manager(
        self, request, fixture, gpu_fixture, expected, forbidden
    ):
        """What the two harnesses actually ran, not what the files say. The render
        check above reads text and so cannot distinguish a call inside a branch
        that never executes from one on the main path, and it is blind to an
        indirect `_pm=dnf; sudo $_pm ...` entirely; this reads the command trace.

        Both package managers are stubbed in both harnesses -- they have to be, or
        the RHEL arm could not run at all -- so what carries the fact is which name
        appears in the trace for which fixture, not the exit status. The trace is
        also the only check that sees the *positive* half: that the arm selected by
        base_os actually reached a package install rather than rendering to nothing.
        """
        params = request.getfixturevalue(fixture)
        gpu_params = request.getfixturevalue(gpu_fixture)
        traces = {"preinstall.j2": [_run_preinstall(params, params["base_os"])[1]]}
        traces["postinstall.j2"] = []
        for node_type in ("HeadNode", "ComputeFleet"):
            traces["postinstall.j2"].append(_run_postinstall(params, node_type)[1])
            traces["postinstall.j2"].append(
                _run_postinstall(gpu_params, node_type, nvme_devices=("nvme1n1",))[1]
            )
        for name, group in traces.items():
            for trace in group:
                for line in trace.splitlines():
                    # The trace records argv, so `sudo dnf -y install` logs twice:
                    # once as "sudo dnf ..." and once as the stubbed command itself.
                    words = line.replace("sudo ", "", 1).split()
                    hit = forbidden.match(words[0]) if words else None
                    assert not hit, (
                        f"{name} on base_os={params['base_os']} executed "
                        f"{hit.group(1)!r}: {line!r}"
                    )
            # Per template, not aggregated across both, and per *package*, not per
            # command name. An aggregate `any` is satisfied by postinstall alone, so
            # deleting preinstall's entire {% else %} body -- which renders an rhel9
            # node a script that installs no python3 and no awscli -- passed the
            # whole suite; and within postinstall, `expected in trace` is satisfied
            # by the GPU block's nvtop/htop line, so deleting the critical-packages
            # block (no gcc, git, lua, lua-devel, nfs-utils, EPEL -> no Lmod) also
            # passed. The sentinels below sit only on the main block's own line.
            installed = set()
            for trace in group:
                installed |= self._installed_packages(trace, expected)
            missing = [
                pkg for pkg in self._SENTINEL_PACKAGES[expected][name]
                if pkg not in installed
            ]
            assert not missing, (
                f"no node installed {missing} via {expected!r} from {name} on "
                f"base_os={params['base_os']}; the arm selected by base_os "
                f"rendered to nothing or lost its main package block, so the "
                f"negative checks above are vacuous"
            )

    def test_no_arm_installs_ansible(
        self, cluster_params, cluster_params_rhel
    ):
        """`pip3 install 'ansible>=9,<10'` is what failed the RHEL 9 node: every
        non-prerelease 9.x needs ansible-core 2.16, whose requires_python is
        >= 3.10, while that AMI ships Python 3.9, and `set -euo pipefail` turned an
        unsatisfiable pin into OnNodeStartExecutionFailure. It was deleted rather
        than branched around because nothing on a node imports ansible -- only
        src/create_pcluster.yml does, and that runs on the operator's workstation.
        Re-adding it to either arm reintroduces the exact failure."""
        for params in (cluster_params, cluster_params_rhel):
            for name in ("preinstall.j2", "postinstall.j2"):
                for line in self._rendered(name, params).splitlines():
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue
                    assert "ansible" not in line, (
                        f"{name} on base_os={params['base_os']} installs ansible: "
                        f"{stripped!r} -- nothing on a node imports it, and the pin "
                        f"is unsatisfiable on the rhel9 AMI's Python 3.9"
                    )

    def test_the_cli_accepts_exactly_the_supported_oses(self):
        """argparse choices is the outermost gate; if it accepts an unsupported value
        that value reaches _resolve_ec2_user and the templates regardless of
        anything else."""
        with open(os.path.join(REPO_ROOT, "make_pcluster.py")) as fh:
            body = fh.read()
        m = re.search(r'"--base_os",\s*\n\s*choices=\[([^\]]*)\]', body)
        assert m, "could not find the --base_os choices list in make_pcluster.py"
        listed = set(re.findall(r"""['"]([^'"]+)['"]""", m.group(1)))
        assert listed == self._SUPPORTED_OSES, (
            f"--base_os choices are {sorted(listed)}, expected "
            f"{sorted(self._SUPPORTED_OSES)}"
        )

    def test_the_arch_and_efa_tables_list_only_supported_oses(self):
        # src/ is not on sys.path when this module runs alone; relying on another
        # test module having imported it first makes this pass only in full-suite
        # order.
        import sys
        src = os.path.join(REPO_ROOT, "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        from pcluster_aux_data import ARM_OSES, X86_OSES, base_os_efa
        for table, label in (
            (ARM_OSES, "ARM_OSES"),
            (X86_OSES, "X86_OSES"),
            (base_os_efa, "base_os_efa"),
        ):
            extra = set(table) - self._SUPPORTED_OSES
            assert not extra, (
                f"{label} lists {sorted(extra)}, which --base_os does not accept"
            )
        assert set(ARM_OSES) | set(X86_OSES) == self._SUPPORTED_OSES, (
            "ARM_OSES + X86_OSES must cover every supported base_os or "
            "base_os_instance_check silently skips the arch mismatch check"
        )

    def test_resolve_ec2_user_accepts_exactly_the_supported_oses(self):
        """_resolve_ec2_user is the only rejection on the defaults-file path --
        argparse choices are bypassed entirely by a <cluster>_defaults.yml value.
        This must key on exact names, not substrings. While the rhel arm was
        `elif "rhel" in base_os`, rhel8 and rhel10 were both accepted and returned
        a login name -- and no template branch, arch table, or playbook gate knows
        either value, so the build proceeded to a node that could not be reached.
        rhel8 and rhel10 are in _UNSUPPORTED_OSES for exactly that reason.

        Nothing pinned that this accepts *only* the supported values, so an
        `elif "<other-distro>" in base_os: ec2_user = "someone-else"` arm passed
        the suite. The login names are PCluster's own: OS_MAPPING in
        pcluster/constants.py gives ubuntu for the ubuntu* values and ec2-user for
        rhel9, and a mismatch here means every ssh and every chown targets a user
        that does not exist on the node."""
        import sys
        src = os.path.join(REPO_ROOT, "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        from pcluster_core import _resolve_ec2_user

        for base_os in sorted(self._SUPPORTED_OSES):
            expected = "ubuntu" if base_os.startswith("ubuntu") else "ec2-user"
            assert _resolve_ec2_user(base_os) == (expected, f"/home/{expected}")

        for base_os in self._UNSUPPORTED_OSES:
            with pytest.raises(SystemExit):
                _resolve_ec2_user(base_os)

    def test_no_defaults_file_ships_an_unsupported_base_os(self):
        """A tracked defaults file naming an unsupported base_os hands the operator a build that
        fails at preinstall, which is exactly how this was found. The example
        file under tests/integration/ is included: it is tracked, operators copy
        it verbatim, and the repo-root glob did not reach it."""
        import glob
        paths = glob.glob(os.path.join(REPO_ROOT, "*_defaults.yml")) + glob.glob(
            os.path.join(REPO_ROOT, "tests", "integration", "*_defaults.yml*")
        )
        # Two, not four: osiris_defaults.yml and isis_defaults.yml were purged and
        # gitignored (both held a real VPC name and subnet IDs). What remains
        # tracked is pcluster_defaults.yml plus the integration
        # itest_defaults.yml.example, which the glob's trailing * picks up.
        assert len(paths) >= 2, f"expected the tracked defaults files, found {paths}"
        for path in paths:
            with open(path) as fh:
                loaded = yaml.safe_load(fh) or {}
            base_os = loaded.get("base_os")
            assert base_os in self._SUPPORTED_OSES, (
                f"{os.path.basename(path)} ships base_os={base_os}"
            )

    def test_defaults_files_document_only_supported_oses(self):
        """The `# ubuntu2204 | ubuntu2404 | ...` comment above base_os is what an
        operator actually reads before editing the value. Rewriting it to list
        unsupported values passed the entire suite -- the same gap
        test_defaults_files_document_the_real_gpu_families already closes for
        GPU families."""
        import glob
        checked = 0
        paths = glob.glob(os.path.join(REPO_ROOT, "*_defaults.yml")) + glob.glob(
            os.path.join(REPO_ROOT, "tests", "integration", "*_defaults.yml*")
        )
        for path in paths:
            with open(path) as fh:
                for line in fh:
                    # The enumeration is the trailing comment on the base_os line
                    # itself. Matching any commented line containing "ubuntu2404"
                    # also swallowed unrelated prose in the integration example.
                    if not line.startswith("base_os:") or "#" not in line:
                        continue
                    listed = {
                        tok.strip()
                        for tok in line.split("#", 1)[1].split("|")
                        if tok.strip()
                    }
                    checked += 1
                    assert listed == self._SUPPORTED_OSES, (
                        f"{os.path.basename(path)} documents {sorted(listed)}, "
                        f"expected {sorted(self._SUPPORTED_OSES)}"
                    )
        # One, not three: osiris_defaults.yml and isis_defaults.yml were purged and
        # gitignored (both held a real VPC name and subnet IDs), leaving
        # pcluster_defaults.yml as the only tracked file with this comment.
        # tests/integration/itest_defaults.yml.example has a base_os: line but no
        # trailing comment, so it never counted. The glob still picks up an
        # operator's own untracked *_defaults.yml when present and holds it to the
        # same equality check -- that is deliberate, since a stale comment there is
        # what an operator reads before editing the value.
        assert checked >= 1, (
            f"no defaults file documents the valid base_os values (found {checked})"
        )

    # A test requiring the playbook to reject an unsupported OS before spending
    # anything lived here. It pinned create_pcluster.yml's `assert` task at index 0 and checked
    # that its own vars listed exactly this set for both base_os and pcluster_os.
    # The playbook is gone; _assert_supported_os in src/pcluster_core.py is the
    # last gate now, and tests/test_create_pcluster_migration.py pins both halves
    # it used to cover -- the valid set (TestAssertSupportedOs, including that
    # the set is ARM_OSES/X86_OSES rather than a restated literal, and that
    # pcluster_os is checked as well as base_os) and the position
    # (TestAssertRunsBeforeAnythingElse, which asserts on the AST that the call
    # is core_create_cluster's first statement, before anything is spent).



class TestOnlyTheDriverIsStagedToS3:
    """The S3 hpc-benchmark prefix exists for exactly one reason: a head node whose
    EBS root was replaced needs hpc-benchmark.sh back.  It was a blocklist sync of
    the whole source tree, which shipped three files nobody wanted to the operator's
    home directory -- hpc-benchmark/CLAUDE.md (internal development notes) and the
    raw README-PERFORMANCE.md.j2 and job_hpc-benchmark.sh.j2.  The README case was
    the damaging one: the tracked README-PERFORMANCE.md is a de-Jinja'd copy of the
    template that tells the reader to cd into a literal <cluster_name>/<cluster_owner>
    path, so an operator following the top-level copy never finds the personalized
    one that create_pcluster.yml scps into headnode_performance_dir_dest.

    An allowlist is the property under test, not the absence of those three names:
    a blocklist is exactly as wide as whoever wrote it remembered, and every file
    added to hpc-benchmark/ from now on would ride along again.  Both ends of the
    sync are pinned -- upload and pull -- because either alone leaves the other
    free to widen.
    """

    # Anything the sync must never place in the operator's home directory. Guard
    # data: the point is that these are in the source tree and stay out of ~.
    _NOT_FOR_THE_OPERATOR = (
        "CLAUDE.md",
        "README-PERFORMANCE.md.j2",
        "job_hpc-benchmark.sh.j2",
    )

    @staticmethod
    def _upload_argv():
        """The `aws s3 sync` argv stage_and_upload_hpc_benchmark_driver runs.

        Read off the AST rather than as text: the arguments are a real Python
        list, so `--exclude "*"` is two separate elements and a substring match
        on the source would happily accept a blocklist written across them.
        """
        import ast
        import inspect

        core = _pcluster_core()
        tree = ast.parse(
            inspect.getsource(core.stage_and_upload_hpc_benchmark_driver).lstrip()
        )
        for node in ast.walk(tree):
            if not isinstance(node, ast.List):
                continue
            literals = [
                e.value for e in node.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            ]
            if literals[:3] == ["aws", "s3", "sync"]:
                return literals
        raise AssertionError(
            "stage_and_upload_hpc_benchmark_driver no longer runs an aws s3 sync"
        )

    @classmethod
    def _upload_includes(cls):
        argv = cls._upload_argv()
        return [argv[i + 1] for i, a in enumerate(argv) if a == "--include"]

    @staticmethod
    def _pull_command(rendered):
        """The rendered sync's own logical command, continuations joined."""
        lines = rendered.splitlines()
        start = next(
            i for i, line in enumerate(lines)
            if "aws s3 sync" in line and "/hpc-benchmark/" in line
        )
        body = []
        for line in lines[start:]:
            body.append(line.rstrip().removesuffix("\\"))
            if not line.rstrip().endswith("\\"):
                break
        return " ".join(" ".join(body).split())

    def test_the_upload_is_an_allowlist(self):
        argv = self._upload_argv()
        excludes = [argv[i + 1] for i, a in enumerate(argv) if a == "--exclude"]
        assert "*" in excludes, (
            'the upload is not an allowlist -- without --exclude "*" every file '
            "added to hpc-benchmark/ ships to the operator's home directory"
        )
        assert "hpc-benchmark.sh" in self._upload_includes(), \
            "the upload excludes everything and includes nothing; self-repair is dead"

    def test_the_upload_allowlists_nothing_but_the_driver(self):
        assert self._upload_includes() == ["hpc-benchmark.sh"], \
            "only the driver is needed for self-repair"

    def test_the_upload_is_still_gated_on_benchmarks(self):
        """An ungated upload creates the S3 prefix on every cluster, and the pull
        side is gated, so nothing would ever read it.

        Asserted on core_create_cluster's AST: the call has to sit inside an
        `if` whose test names the flag, which a text search for the two tokens
        near each other cannot tell from a call beside the gate.
        """
        import ast

        source = _core_function_source("core_create_cluster")
        tree = ast.parse(source.lstrip())

        def calls_the_stager(node):
            return any(
                isinstance(n, ast.Call)
                and getattr(n.func, "id", None) == "stage_and_upload_hpc_benchmark_driver"
                for n in ast.walk(node)
            )

        assert calls_the_stager(tree), (
            "core_create_cluster never stages the benchmark driver"
        )
        gated = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.If)
            and "enable_hpc_benchmarks" in {
                n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)
            }
            and calls_the_stager(node)
        ]
        assert gated, (
            "the benchmark driver upload is not inside an `if enable_hpc_benchmarks`"
        )

    @pytest.mark.parametrize("fixture", ["cluster_params", "cluster_params_rhel"])
    def test_the_pull_is_the_same_allowlist(self, fixture, request):
        params = request.getfixturevalue(fixture)
        rendered = _make_env(os.path.join(REPO_ROOT, "templates")).get_template(
            "postinstall.j2"
        ).render(**params)
        cmd = self._pull_command(rendered)
        assert '--exclude "*"' in cmd, "the pull is not an allowlist"
        assert re.findall(r'--include\s+"([^"]+)"', cmd) == ["hpc-benchmark.sh"], \
            "the pull and the upload must allowlist the same one file"

    @pytest.mark.parametrize("name", _NOT_FOR_THE_OPERATOR)
    def test_no_internal_file_is_named_on_either_end(self, name, cluster_params):
        """A --include naming any of these would defeat the allowlist by hand."""
        rendered = _make_env(os.path.join(REPO_ROOT, "templates")).get_template(
            "postinstall.j2"
        ).render(**cluster_params)
        pull = self._pull_command(rendered)
        for where, included in (
            ("upload", self._upload_includes()),
            ("pull", re.findall(r'--include\s+"([^"]+)"', pull)),
        ):
            assert name not in included, f"{name} is allowlisted on the {where}"

    def test_the_internal_files_are_really_in_the_source_tree(self):
        """Vacuity guard: the test above proves nothing if these do not exist.

        Each is tracked in hpc-benchmark/, so a plain blocklist sync would have
        shipped it -- which is what it did.
        """
        for name in self._NOT_FOR_THE_OPERATOR:
            path = os.path.join(REPO_ROOT, "hpc-benchmark", name)
            assert os.path.exists(path), \
                f"{name} is gone from hpc-benchmark/; this guard is now vacuous"

    def test_the_chmod_names_only_a_file_the_sync_delivers(self, cluster_params):
        """The chmod carried a second target, ~/hpc-benchmark/job_hpc-benchmark.sh,
        that has never existed at that path -- only the .j2 was ever synced, and the
        rendered job script arrives by scp two directories down.  `|| true` hid it.
        A chmod that cannot fail needs no guard, and a guard on a chmod hides the
        driver failing to arrive.
        """
        rendered = _make_env(os.path.join(REPO_ROOT, "templates")).get_template(
            "postinstall.j2"
        ).render(**cluster_params)
        home = cluster_params["ec2_user_home"]
        chmods = [
            line.strip() for line in rendered.splitlines()
            if line.strip().startswith("chmod +x") and "/hpc-benchmark/" in line
        ]
        assert len(chmods) == 1, f"expected one hpc-benchmark chmod, got {chmods}"
        line = chmods[0]
        assert line == f'chmod +x "{home}/hpc-benchmark/hpc-benchmark.sh"', (
            "the chmod must name exactly the one file the sync delivers, with no "
            f"|| true masking its failure: {line}"
        )


# A class asserting the Secrets Manager write happens on every run lived here.
# It pinned four properties of
# create_pcluster.yml's keypair/secret tasks: that the Secrets Manager write is
# not gated on whether AWS minted a new keypair, that an existing secret is
# tolerated, that the local .pem is guaranteed to exist first, and that the
# tasks handling real key material keep no_log. The playbook is gone, and so is
# no_log -- there is no Ansible task to censor. The other three are properties
# of src/pcluster_core.py's _store_ssh_secret / _save_private_key_locally /
# _abort_if_keypair_orphaned, driven directly in
# tests/test_create_pcluster_migration.py (TestStoreSshSecret,
# TestSavePrivateKeyLocally, TestAbortIfKeypairOrphaned,
# TestProvisionS3KeypairAndSecret).



class TestTheKnownHostsPathIsExpanded:
    """`ssh_known_hosts` must be absolute, because every use of it is quoted.

    It shipped as the literal `~/.ssh/known_hosts` in vars_file.j2, and both
    consumers wrap it in double quotes -- which the shell does not tilde-expand.
    Two consequences, verified under real bash rather than assumed:
    `ssh-keygen -R "~/.ssh/known_hosts"` cannot find the file (its `|| true`
    swallowed that), and the keyscan append redirected into a nonexistent
    `./~/.ssh/` directory. The append's failure was invisible: the `_added=1`
    that followed it inside the `{ ... }` group always succeeded, so the group
    returned 0 and the write was reported as having happened. Every later ssh
    and scp against the head node then depends on a fingerprint that was never
    recorded.
    """

    def test_the_vars_file_does_not_ship_a_literal_tilde(self):
        with open(os.path.join(REPO_ROOT, "templates", "vars_file.j2")) as fh:
            for line in fh:
                if line.strip().startswith("ssh_known_hosts:"):
                    value = line.split(":", 1)[1].strip().strip("\"'")
                    assert not value.startswith("~"), (
                        "ssh_known_hosts is a literal tilde path; the shell tasks "
                        "that use it quote it, so it is never expanded"
                    )
                    return
        raise AssertionError("ssh_known_hosts not found in vars_file.j2")

    def test_make_pcluster_supplies_an_absolute_path(self, cluster_params):
        """Derived with expanduser in Python, since vars_file.j2 is rendered by
        plain Jinja2 -- Ansible's lookup('env', 'HOME') is not available there."""
        rendered = _make_env(os.path.join(REPO_ROOT, "templates")).get_template(
            "vars_file.j2"
        ).render(**cluster_params)
        value = yaml.safe_load(rendered)["ssh_known_hosts"]
        assert os.path.isabs(value), f"ssh_known_hosts is not absolute: {value!r}"

    def test_the_derivation_uses_expanduser_not_a_literal(self):
        """An AST walk over src/pcluster_core.py's core_create_cluster (where
        cluster_parameters has lived since the core/shim split), so the
        fixture cannot carry it alone.

        conftest supplies an absolute value regardless of what production does, so
        the test above passes with the literal tilde restored in production.
        """
        import ast

        with open(os.path.join(REPO_ROOT, "src", "pcluster_core.py")) as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and getattr(node.targets[0], "id", "") == "cluster_parameters"
            ):
                for key, value in zip(node.value.keys, node.value.values):
                    if getattr(key, "value", None) == "ssh_known_hosts":
                        src = ast.dump(value)
                        assert "expanduser" in src, (
                            "ssh_known_hosts must be expanded in Python; a literal "
                            "'~' is not expanded by the quoted shell tasks"
                        )
                        return
        raise AssertionError("cluster_parameters['ssh_known_hosts'] not found")

    def test_the_quoted_tilde_really_is_not_expanded(self):
        """Vacuity guard for the two tests above, under the real shell.

        If bash expanded a quoted tilde, none of this would be a bug and the
        guard would be pinning a preference rather than a defect.
        """
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            rc = subprocess.run(
                ['echo test >> "~/.ssh/known_hosts"'],
                shell=True, cwd=td, capture_output=True,
            )
            assert rc.returncode != 0, (
                "a quoted tilde was expanded by this shell; the premise of this "
                "class no longer holds"
            )

    # Four tests that executed create_pcluster.yml's own keyscan shell body
    # under real bash lived here -- that a failed known_hosts append fails the
    # task, that a successful one reports changed, that an empty ssh-keyscan is
    # not a success, and that the .ssh directory is created before either
    # consumer. The playbook is gone; those are properties of
    # _accept_ssh_fingerprint / _remove_stale_known_hosts_entry /
    # _ensure_local_ssh_dir in src/pcluster_core.py, driven in
    # tests/test_create_pcluster_migration.py. What stays here is the property
    # the class is named for: ssh_known_hosts must be an absolute path, because
    # every consumer of it quotes it.

class TestBenchmarkResultsOutliveTheCluster:
    """Teardown synced the head node's benchmark results to s3_bucketname and then,
    a few steps later, deleted that same bucket with force=true -- which purges
    objects before removing it. delete_s3_bucketname defaults to "true", so the
    default teardown path uploaded possibly gigabytes and immediately destroyed
    them. Both steps succeed, so nothing was reported as orphaned and teardown
    still printed "has been deleted": the loss was silent by construction.

    Results now go to results_bucketname, which is keyed on account+region rather
    than on the cluster or its serial, so it outlives every build. Two properties
    carry this, and each fails on its own:

      - the sync must name results_bucketname, not s3_bucketname
      - nothing in the toolkit may delete results_bucketname

    These drove the two playbooks' tasks until those were deleted; they drive
    src/pcluster_core.py's own teardown and build functions now.
    """

    _RESULTS_VAR = "results_bucketname"
    _PER_BUILD_VAR = "s3_bucketname"

    @staticmethod
    def _sync_argv():
        """The argv _sync_performance_results_to_s3 runs on the head node."""
        import ast
        import inspect

        core = _pcluster_core()
        tree = ast.parse(
            inspect.getsource(core._sync_performance_results_to_s3).lstrip()
        )
        for node in ast.walk(tree):
            if not isinstance(node, ast.List):
                continue
            if any(
                isinstance(e, ast.Constant) and e.value == "ssh" for e in node.elts
            ):
                return node
        raise AssertionError(
            "no results sync command in _sync_performance_results_to_s3; if it "
            "was removed, the accumulation claim in README.md must go with it"
        )

    @classmethod
    def _sync_text(cls):
        import ast

        return ast.unparse(cls._sync_argv())

    def test_the_results_sync_does_not_target_the_bucket_teardown_deletes(self):
        cmd = self._sync_text()
        assert self._RESULTS_VAR in cmd, (
            f"results sync does not name {self._RESULTS_VAR}: {cmd}"
        )
        # The per-build bucket is deleted later in the same teardown, by default.
        assert f"s3://{{{self._PER_BUILD_VAR}}}" not in cmd, (
            f"results are synced to {self._PER_BUILD_VAR}, which teardown deletes "
            f"with force=true when delete_s3_bucketname is true (the default): {cmd}"
        )

    def test_the_results_prefix_still_separates_clusters_and_builds(self):
        """One bucket for every build means the prefix is the only thing keeping two
        clusters' results apart, so it carries both the name and the serial."""
        cmd = self._sync_text()
        assert (
            "hpc-benchmark-results/{cluster_name}/{cluster_serial_number}/" in cmd
        ), cmd

    def test_no_surface_ever_deletes_the_results_bucket(self):
        """The whole point of the bucket. The build side is included because its
        provisioning rollback deletes s3_bucketname on any failure, and the
        obvious wrong move is to add the results bucket beside it for symmetry."""
        import ast

        core = _pcluster_core()
        with open(os.path.join(REPO_ROOT, "src", "pcluster_core.py")) as fh:
            tree = ast.parse(fh.read())
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            attr = getattr(node.func, "attr", "")
            if attr not in ("delete_bucket", "delete_objects", "delete_object"):
                continue
            names = {
                n.id for n in ast.walk(node) if isinstance(n, ast.Name)
            } | {
                n.value for n in ast.walk(node)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            }
            if any(self._RESULTS_VAR in str(n) for n in names):
                offenders.append(attr)
        assert not offenders, (
            f"src/pcluster_core.py deletes the results bucket: {offenders}. It "
            f"holds every past build's results and nothing in this toolkit may "
            f"remove it."
        )
        assert core is not None

    def test_the_per_build_bucket_is_still_deleted_on_teardown(self):
        """Vacuity guard: if the S3 deletion were simply gone, the test above would
        pass while teardown leaked a bucket per build."""
        import inspect

        source = inspect.getsource(_pcluster_core()._delete_s3_bucket_step)
        assert "delete_bucket" in source, (
            "teardown no longer deletes the per-build bucket"
        )

    def test_the_results_bucket_is_created_before_it_is_synced_to(self):
        """Teardown's sync is the first write on a first-ever build, and `aws s3 sync`
        to a nonexistent bucket fails -- so the build side must create it."""
        import ast
        import inspect

        core = _pcluster_core()
        creator = inspect.getsource(core._create_hpc_results_bucket)
        assert "create_bucket" in creator and self._RESULTS_VAR in creator, creator

        # It is only reached under the benchmark gate: its one caller is the
        # driver staging, which core_create_cluster calls inside
        # `if enable_hpc_benchmarks`.
        stager = inspect.getsource(core.stage_and_upload_hpc_benchmark_driver)
        assert "_create_hpc_results_bucket(" in stager, (
            "the results bucket is no longer created alongside the driver upload"
        )
        tree = ast.parse(_core_function_source("core_create_cluster").lstrip())
        gated = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.If)
            and "enable_hpc_benchmarks" in {
                n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)
            }
            and any(
                isinstance(n, ast.Call)
                and getattr(n.func, "id", None)
                == "stage_and_upload_hpc_benchmark_driver"
                for n in ast.walk(node)
            )
        ]
        assert gated, (
            "the results bucket is created on clusters that run no benchmarks"
        )

    def test_the_results_bucket_blocks_public_access(self):
        """It is the one bucket that outlives every cluster, so it is also the one
        whose misconfiguration persists."""
        import inspect

        source = inspect.getsource(_pcluster_core()._create_hpc_results_bucket)
        assert "put_public_access_block" in source, (
            "the results bucket does not block public access"
        )
        for key in (
            "BlockPublicAcls",
            "BlockPublicPolicy",
            "IgnorePublicAcls",
            "RestrictPublicBuckets",
        ):
            assert f'"{key}": True' in source, f"{key} is not true"


    def test_the_rendered_vars_file_actually_defines_the_bucket(self, cluster_params):
        """The teardown sync interpolates results_bucketname out of
        vars_files/<cluster>.yml, which is vars_file.j2's rendered output. Deleting
        the line from that template leaves the sync interpolating an undefined
        variable at teardown -- the last chance to save the results, on a cluster
        that is about to be destroyed. The other half of the tracing rule
        (cluster_parameters -> vars_file.j2) is covered by
        test_vars_file_variables_are_all_supplied_by_make_pcluster, which only
        checks the direction that fails at build time rather than at teardown."""
        defined = _vars_for({}, cluster_params)
        assert self._RESULTS_VAR in defined, (
            f"vars_file.j2 does not define {self._RESULTS_VAR}; the teardown sync "
            f"would interpolate an undefined variable"
        )
        assert defined[self._RESULTS_VAR], f"{self._RESULTS_VAR} renders empty"
        assert defined[self._RESULTS_VAR] != defined.get("s3_bucketname"), (
            "results_bucketname renders to the per-build bucket teardown deletes"
        )

    def test_the_iam_arn_matches_the_name_the_toolkit_actually_derives(self):
        """The bucket name is spelled twice and independently: once by
        _derive_results_bucket, once as a literal ARN in HeadNode-Storage.json_src.
        Nothing connects them, so a change to either alone leaves the head node
        with a grant on a bucket that does not exist and the teardown sync failing
        with an opaque 403 -- after the results have already been written to the
        head node and are about to be terminated with it.

        Asserted by rendering the policy through the same placeholder values the
        derivation is given, so the two must agree character for character."""
        sys.path.insert(0, os.path.join(REPO_ROOT, "src"))
        from pcluster_core import _derive_results_bucket

        derived = _derive_results_bucket(
            aws_account_id=_PLACEHOLDER_SUB["<AWS_ACCOUNT_ID>"],
            region=_PLACEHOLDER_SUB["<AWS_REGION>"],
        )
        arns = [
            r
            for stmt in _load_policy("HeadNode-Storage.json_src")["Statement"]
            for r in (
                stmt["Resource"]
                if isinstance(stmt["Resource"], list)
                else [stmt["Resource"]]
            )
            if "hpc-benchmark-results" in r or r.endswith(derived)
        ]
        assert arns, (
            f"no HeadNode-Storage ARN names the derived results bucket {derived!r}; "
            f"the head node's teardown sync would get an opaque 403"
        )
        for arn in arns:
            bucket = arn.split(":::", 1)[1].split("/", 1)[0]
            assert bucket == derived, (
                f"HeadNode-Storage grants on bucket {bucket!r} but the toolkit "
                f"derives {derived!r}; the two spellings have drifted"
            )

    def test_the_head_node_can_write_results_but_not_delete_them(self):
        """The head node runs the sync itself over ssh, so it needs PutObject. It must
        not get DeleteObject or DeleteBucket: anyone with a shell on the head node
        (including via Slurm job submission) could otherwise erase every past build's
        results, and the bucket is the only copy once the cluster is gone."""
        def _listify(value):
            return value if isinstance(value, list) else [value]

        results_statements = [
            stmt
            for stmt in _load_policy("HeadNode-Storage.json_src")["Statement"]
            if any(
                "parallelclustermaker-results" in r for r in _listify(stmt["Resource"])
            )
        ]
        assert results_statements, (
            "HeadNode-Storage grants nothing on the results bucket; the teardown "
            "sync runs on the head node and would fail with an opaque 403"
        )
        granted = {a for stmt in results_statements for a in _listify(stmt["Action"])}
        assert "s3:PutObject" in granted, granted
        for forbidden in ("s3:DeleteObject", "s3:DeleteObjectVersion", "s3:DeleteBucket"):
            assert forbidden not in granted, (
                f"HeadNode-Storage grants {forbidden} on the results bucket; a shell "
                f"on the head node could erase every past build's results"
            )

        # Withholding DeleteObject is only half of it. PutObject on the whole
        # bucket lets a shell on the head node overwrite any past build's results
        # in place, which destroys them just as thoroughly as deleting them --
        # and read is not harmless either, since the bucket is shared by every
        # cluster in the account and region. Widening the object Resource to
        # <bucket>/* passed every assertion above.
        for stmt in results_statements:
            objectish = {
                a for a in _listify(stmt["Action"]) if a != "s3:ListBucket"
            }
            if not objectish:
                continue
            for resource in _listify(stmt["Resource"]):
                assert resource.endswith("/hpc-benchmark-results/*"), (
                    f"HeadNode-Storage grants {sorted(objectish)} on {resource}; "
                    f"object access to the results bucket must be confined to the "
                    f"hpc-benchmark-results/ prefix"
                )


class TestPreinstallPostinstallByteForByteParity:
    """Workstream 2 Tier 3's own mandated check, from the plan itself:
    preinstall.j2/postinstall.j2 are 28KB, the heaviest branching of any
    template in the repo, and the single largest concentration of
    CLAUDE.md-pinned bootstrap incidents tied to exact rendered output.
    Before their Ansible `template:` task is deleted, prove -- across every
    fixture combination in conftest.py, not just the default -- that
    core_create_cluster's real context-construction pipeline
    (cluster_parameters -> render vars_file.j2 -> yaml.safe_load -> merge
    with cluster_parameters) produces byte-identical output to _make_env's
    direct fixture render, which the rest of this file's extensive
    preinstall/postinstall coverage already trusts as correct. A mismatch
    here would mean core_create_cluster's Python render diverges from what
    Ansible's template: task actually produced -- exactly the class of bug
    Tier 1 (missing preinstall_s3_dest) and Tier 2 (missing Deployed_On,
    the config.pcluster.j2 runtime-value gap) each found once already."""

    _FIXTURES = [
        "cluster_params",
        "cluster_params_orphaned_teardown",
        "cluster_params_retained_teardown",
        "cluster_params_unconfirmed_delete",
        "cluster_params_rhel",
        "cluster_params_rhel_gpu_queue",
        "cluster_params_al2023",
        "cluster_params_al2023_gpu_queue",
        "cluster_params_al2023_monitoring",
        "cluster_params_custom_ami",
        "cluster_params_monitoring_enabled",
        "cluster_params_loginnode_enabled",
        "cluster_params_loginnode_pool",
        "cluster_params_gpu_enabled",
        "cluster_params_gpu_gdr_enabled",
        "cluster_params_hpc_benchmarks_disabled",
        "cluster_params_efa_enabled",
        "cluster_params_multi_instance_cpu",
        "cluster_params_gpu_queue_enabled",
        "cluster_params_gpu_no_nvidia",
    ]

    @staticmethod
    def _pcluster_core():
        src = os.path.join(REPO_ROOT, "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        import pcluster_core

        return pcluster_core

    @classmethod
    def _pipeline_context(cls, params):
        """The exact context core_create_cluster builds: render vars_file.j2
        from the raw params, reparse it, and merge with the raw params so
        neither vars_file.j2-derived names (preinstall_s3_dest, ...) nor
        cluster_parameters-only names (Deployed_On, debug_mode) are lost."""
        pcluster_core = cls._pcluster_core()
        templates_dir = os.path.join(REPO_ROOT, "templates")
        rendered_vars_file = pcluster_core.render_template(
            templates_dir, "vars_file.j2", **params
        )
        return {**params, **yaml.safe_load(rendered_vars_file)}

    @pytest.mark.parametrize("fixture", _FIXTURES)
    def test_preinstall_matches_the_established_render(self, fixture, request):
        params = request.getfixturevalue(fixture)
        templates_dir = os.path.join(REPO_ROOT, "templates")
        expected = _make_env(templates_dir).get_template("preinstall.j2").render(**params)
        context = self._pipeline_context(params)
        actual = self._pcluster_core().render_template(
            templates_dir, "preinstall.j2", **context
        )
        assert actual == expected, (
            f"preinstall.j2 diverges from the established render for {fixture}"
        )

    @pytest.mark.parametrize("fixture", _FIXTURES)
    def test_postinstall_matches_the_established_render(self, fixture, request):
        params = request.getfixturevalue(fixture)
        templates_dir = os.path.join(REPO_ROOT, "templates")
        expected = _make_env(templates_dir).get_template("postinstall.j2").render(**params)
        context = self._pipeline_context(params)
        actual = self._pcluster_core().render_template(
            templates_dir, "postinstall.j2", **context
        )
        assert actual == expected, (
            f"postinstall.j2 diverges from the established render for {fixture}"
        )



class TestMcpLambdaPolicies:
    """Workstream 5's MCP Lambda execution-role policies get the same
    structural guards the cluster policies already have.

    They are deliberately NOT added to _POLICY_FILES -- that list is pinned
    by equality to the five managed cluster policies, and _setup_iam's
    suffix lists are asserted against it -- so without this class they
    would sit in templates/ classified but otherwise unchecked: no JSON
    validity, no size limit, no placeholder sweep. Being newer and
    undeployed makes that more dangerous, not less; nothing has exercised
    them yet.
    """

    def test_the_file_list_matches_what_is_on_disk(self):
        """Pinned by equality in both directions: a new MCP policy file
        that nobody adds here would be classified (the ban's
        directory-equality check passes) yet never validated by anything
        in this class."""
        on_disk = {
            f for f in os.listdir(os.path.join(REPO_ROOT, "templates"))
            if f.startswith("MCP") and f.endswith(".json_src")
        }
        assert on_disk == set(_MCP_ALL_POLICY_FILES)

    @pytest.mark.parametrize("fname", _MCP_ALL_POLICY_FILES)
    def test_valid_json(self, fname):
        _load_policy(fname)

    @pytest.mark.parametrize("fname", _MCP_ALL_POLICY_FILES)
    def test_under_the_managed_policy_size_limit(self, fname):
        """Measured minified, which is what _render_policy enforces. Worth
        stating because raw file size misleads here: MCPStackMutation is
        ~8.7 KB on disk but ~4.8 KB minified, and a reviewer reading the
        raw size concluded it needed splitting."""
        minified = json.dumps(_load_policy(fname), separators=(",", ":"))
        size = len(minified.encode("utf-8"))
        assert size <= _IAM_POLICY_LIMIT, f"{fname}: {size} > {_IAM_POLICY_LIMIT}"

    @pytest.mark.parametrize("fname", _MCP_ALL_POLICY_FILES)
    def test_statement_keys_are_valid(self, fname):
        valid = {
            "Sid", "Effect", "Action", "NotAction",
            "Resource", "NotResource", "Condition", "Principal",
        }
        for stmt in _load_policy(fname)["Statement"]:
            unknown = set(stmt) - valid
            assert not unknown, f"{fname}: statement {stmt.get('Sid')} has {unknown}"

    @pytest.mark.parametrize("fname", _MCP_ALL_POLICY_FILES)
    def test_sids_are_unique(self, fname):
        sids = [s.get("Sid") for s in _load_policy(fname)["Statement"]]
        assert len(sids) == len(set(sids)), f"{fname}: duplicate Sids"

    @pytest.mark.parametrize("fname", _MCP_ALL_POLICY_FILES)
    def test_no_unsubstituted_placeholders(self, fname):
        """Catches a placeholder _PLACEHOLDER_SUB does not know about --
        which is how <MCP_USER_POOL_ID> would otherwise have rendered
        literally into a live IAM ARN, since it existed in neither the test
        substitution map nor _render_policy's chain before this round."""
        rendered = json.dumps(_load_policy(fname))
        leftover = re.findall(r"<[A-Z_]+>", rendered)
        assert not leftover, f"{fname}: unsubstituted {sorted(set(leftover))}"

    def test_render_policy_substitutes_every_placeholder_these_files_use(self):
        """The two substitution sources must agree. _PLACEHOLDER_SUB is the
        tests' map and _render_policy is production's; a placeholder in one
        and not the other is exactly the drift that makes a policy pass its
        tests and then render a literal token into a real ARN."""
        import inspect

        src = os.path.join(REPO_ROOT, "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        import pcluster_core

        # Comment lines stripped before matching. The comment explaining
        # this substitution names the placeholder itself, so a whole-source
        # search matches the prose and passes with the .replace() call
        # deleted -- verified: that mutation survived the first version of
        # this test.
        source = "\n".join(
            line for line in inspect.getsource(pcluster_core._render_policy).splitlines()
            if not line.lstrip().startswith("#")
        )
        used = set()
        for fname in _MCP_LAMBDA_POLICY_FILES:
            raw = open(os.path.join(REPO_ROOT, "templates", fname)).read()
            used |= set(re.findall(r"<[A-Z_]+>", raw))
        for token in sorted(used):
            assert f'"{token}"' in source, (
                f"_render_policy does not substitute {token}, used by an MCP "
                f"policy template -- it would render literally into an IAM ARN"
            )


class TestEachTierCanActuallyDoItsJob:
    """A tier's policy must grant what its own tools call, and nothing had
    checked that for `fleet-toggle`.

    `update-compute-fleet` does two things before it can toggle anything: it
    parses the cluster configuration out of PCluster's own per-cluster S3
    bucket, and it reads/updates the fleet status item in the DynamoDB table
    `parallelcluster-<cluster>` (`ComputeFleetStatusManager`). The tier
    granted neither, so `stop_fleet` and `start_fleet` failed against every
    real cluster for the tier's whole life -- "Unable to access bucket
    associated to the cluster", observed live in R4.

    It went unseen because every guard here asked whether a tier could
    exceed its blast radius, never whether it could reach its own floor, and
    because the error that surfaced it was blank until
    `pcluster_exception_detail` was wired into the wrappers.
    """

    def _actions(self, fname):
        pol = _load_policy(fname)
        out = []
        for st in pol["Statement"]:
            if st.get("Effect") != "Allow":
                continue
            acts = st.get("Action", [])
            out += acts if isinstance(acts, list) else [acts]
        return out

    def _grants(self, fname, action):
        return any(fnmatch.fnmatch(action, granted)
                   for granted in self._actions(fname))

    def test_fleet_toggle_can_read_the_cluster_configuration(self):
        for action in ("s3:GetObject", "s3:ListBucket"):
            assert self._grants("MCPFleetToggleLambda.json_src", action), (
                f"fleet-toggle cannot {action}; update-compute-fleet parses "
                f"the cluster config from PCluster's per-cluster bucket and "
                f"fails with 'Unable to access bucket associated to the cluster'"
            )

    def test_fleet_toggle_can_read_and_update_the_fleet_status_item(self):
        for action in ("dynamodb:GetItem", "dynamodb:UpdateItem"):
            assert self._grants("MCPFleetToggleLambda.json_src", action), (
                f"fleet-toggle cannot {action}; the compute fleet status "
                f"lives in the parallelcluster-<cluster> DynamoDB table"
            )

    def test_fleet_toggle_still_cannot_write_a_cluster_configuration(self):
        """The floor is not a licence to raise the ceiling: this tier
        toggles a fleet, so its S3 grant stays read-only. `s3:*` would have
        satisfied the two tests above while handing a fleet toggle the
        ability to rewrite any cluster's configuration."""
        for action in ("s3:PutObject", "s3:DeleteObject", "s3:DeleteBucket"):
            assert not self._grants("MCPFleetToggleLambda.json_src", action), (
                f"fleet-toggle grants {action}; it needs only to read the "
                f"cluster config"
            )

    # Every tier serving a tool that calls describe_cluster. A login-node
    # pool sits behind an NLB, and pcluster/aws/elb.py is reached during an
    # ordinary describe -- so this is not a login-node-only tier's problem.
    _DESCRIBE_CLUSTER_TIERS = [
        "MCPReadOnlyLambda.json_src",
        "MCPFleetToggleLambda.json_src",
        "MCPStackMutation.json_src",
    ]

    @pytest.mark.parametrize("fname", _DESCRIBE_CLUSTER_TIERS)
    def test_describe_cluster_works_on_a_login_node_cluster(self, fname):
        """`--enable_loginnode true` puts the pool behind a load balancer,
        and describe-cluster then reads it. No tier granted any
        elasticloadbalancing action, so describe-cluster failed from every
        remote tier against any login-node cluster -- observed live in R4 as
        "not authorized to perform: elasticloadbalancing:DescribeLoadBalancers".

        All four calls pcluster's elb.py makes are required; granting only
        DescribeLoadBalancers moves the failure to the next one.
        """
        for action in ("elasticloadbalancing:DescribeLoadBalancers",
                       "elasticloadbalancing:DescribeTags",
                       "elasticloadbalancing:DescribeTargetGroups",
                       "elasticloadbalancing:DescribeTargetHealth"):
            assert self._grants(fname, action), (
                f"{fname} cannot {action}; describe-cluster fails on any "
                f"cluster built with --enable_loginnode true"
            )

    @pytest.mark.parametrize("fname", _DESCRIBE_CLUSTER_TIERS)
    def test_the_load_balancer_grant_stays_read_only(self, fname):
        """Describe actions take no resource-level permission, so this one
        is necessarily `Resource: "*"` -- which makes keeping it read-only
        the only bound left on it."""
        for action in ("elasticloadbalancing:CreateLoadBalancer",
                       "elasticloadbalancing:DeleteLoadBalancer",
                       "elasticloadbalancing:ModifyTargetGroup",
                       "elasticloadbalancing:RegisterTargets"):
            assert not self._grants(fname, action), (
                f"{fname} grants {action}; the tiers only ever read the "
                f"login-node load balancer"
            )

    def test_the_dynamodb_grant_is_scoped_to_pclusters_own_tables(self):
        pol = _load_policy("MCPFleetToggleLambda.json_src")
        for st in pol["Statement"]:
            acts = st.get("Action", [])
            acts = acts if isinstance(acts, list) else [acts]
            if not any(a.startswith("dynamodb:") for a in acts):
                continue
            res = st["Resource"]
            for r in (res if isinstance(res, list) else [res]):
                assert r != "*", "the DynamoDB grant is account-wide"
                assert "table/parallelcluster-" in r, (
                    f"DynamoDB grant on {r!r} reaches tables PCluster does "
                    f"not own"
                )


class TestTheClusterBuildTierCanActuallyBuild:
    """`MCPClusterBuild` granted IAM on PCluster's own CDK roles and nothing
    for `_setup_iam`'s.

    The first remote `create_cluster` got through VPC discovery, spot
    pricing and OS resolution, then failed with `AccessDenied` on
    `iam:CreatePolicy` for `pclustermaker-cluster-boundary`. The gap was not
    one action: the whole toolkit-side grant set was absent -- the six
    managed policies, the head node role, its instance profile, the
    boundary, and the SSH key secret beyond `TagResource`.

    Derived against `OperatorPolicy`, which does the same job from the CLI,
    rather than listed here: a restated list is a second source, and the CLI
    path is the one that has been exercised on live builds.
    """

    # What the CLI holds for its own resources. Not the whole operator
    # policy -- the tier legitimately lacks Cost Explorer, pricing and the
    # Route53 zone lifecycle, none of which _setup_iam touches.
    _SHARED_SIDS = [
        "IAMAttachDetachClusterPolicies",
        "IAMRoleLifecycle",
        "IAMClusterBoundaryBootstrapReadAndCreate",
        "IAMBoundClusterRoleOnly",
        "IAMDenyWeakeningTheClusterBoundary",
        "IAMInstanceProfile",
    ]

    def _stmts(self, fname):
        return {s.get("Sid"): s for s in _load_policy(fname)["Statement"]}

    def _actions(self, stmt):
        a = stmt.get("Action", [])
        return set(a if isinstance(a, list) else [a])

    @pytest.mark.parametrize("sid", _SHARED_SIDS)
    def test_it_carries_the_operators_own_build_grant(self, sid):
        tier = self._stmts("MCPClusterBuild.json_src")
        operator = self._stmts("OperatorPolicy.json_src")
        assert sid in tier, (
            f"MCPClusterBuild has no {sid}; the CLI needs it to build a "
            f"cluster, so a remote build fails partway through _setup_iam"
        )
        missing = self._actions(operator[sid]) - self._actions(tier[sid])
        assert not missing, f"{sid} is missing {sorted(missing)}"

    def test_the_boundary_bootstrap_can_create_not_only_read(self):
        """`iam:GetPolicy` alone passes on any account that already has the
        boundary and fails on a fresh one. The account this was found on had
        none -- both durable boundaries had been removed by hand once
        nothing was bound by them -- which is why the log named CreatePolicy
        rather than GetPolicy."""
        st = self._stmts("MCPClusterBuild.json_src")[
            "IAMClusterBoundaryBootstrapReadAndCreate"]
        assert "iam:CreatePolicy" in self._actions(st)

    def test_it_can_manage_the_ssh_key_secret_not_only_tag_it(self):
        acts = self._actions(
            self._stmts("MCPClusterBuild.json_src")["TheSshKeySecret"])
        for a in ("secretsmanager:CreateSecret", "secretsmanager:PutSecretValue",
                  "secretsmanager:DeleteSecret"):
            assert a in acts, f"the build creates the SSH key secret; {a} missing"

    def test_role_creation_stays_bounded(self):
        """The floor is not a licence to raise the ceiling. `iam:CreateRole`
        reaches an internet-facing tier here, so the boundary condition is
        what keeps it from creating an unbounded role."""
        st = self._stmts("MCPClusterBuild.json_src")["IAMBoundClusterRoleOnly"]
        cond = st["Condition"]["StringEquals"]["iam:PermissionsBoundary"]
        assert cond.endswith("policy/pclustermaker-cluster-boundary")
        for sid in ("IAMRoleLifecycle", "IAMAttachDetachClusterPolicies"):
            for r in self._stmts("MCPClusterBuild.json_src")[sid]["Resource"]:
                assert "role/pclustermaker-role-" in r, (
                    f"{sid} reaches roles outside this toolkit's own: {r}")

    def test_the_mcp_boundary_permits_exactly_the_one_rebind(self):
        """A grant the boundary denies is a grant that does nothing.

        `BoundaryDenyBoundaryTampering` denied `iam:PutRolePermissionsBoundary`
        outright, so `_setup_iam`'s reassert -- the path that re-caps a head
        node role built before the boundary existed -- would have failed
        even with the grant above. It is now denied except when attaching
        the cluster boundary, and always on an MCP role, so the transport
        can never re-bound itself.
        """
        st = self._stmts("MCPRoleBoundary.json_src")
        assert "iam:PutRolePermissionsBoundary" not in self._actions(
            st["BoundaryDenyBoundaryTampering"]), (
            "the blanket deny is back; the grant on the tier is inert")
        gate = st["BoundaryOnlyEverAttachTheClusterBoundary"]
        assert gate["Effect"] == "Deny" and gate["Resource"] == "*"
        assert gate["Condition"]["StringNotEquals"][
            "iam:PermissionsBoundary"].endswith(
            "policy/pclustermaker-cluster-boundary")
        mcp = st["BoundaryNeverRebindAnMcpRole"]
        assert mcp["Effect"] == "Deny"
        assert any("role/pclustermaker-mcp-" in r for r in mcp["Resource"])

    def test_the_user_boundary_denials_are_untouched(self):
        """Vacuity guard: the fix narrows one action, not the statement."""
        acts = self._actions(
            self._stmts("MCPRoleBoundary.json_src")["BoundaryDenyBoundaryTampering"])
        for a in ("iam:DeleteRolePermissionsBoundary",
                  "iam:PutUserPermissionsBoundary",
                  "iam:DeleteUserPermissionsBoundary"):
            assert a in acts


class TestTheDeployPolicyCanActuallyDeploy:
    """`MCPDeployPolicy` has a floor too, and nothing was checking it.

    `TestEachTierCanActuallyDoItsJob` asks whether each *tier* can reach its
    own floor. Nobody asked it of the policy an administrator hands the
    deployer -- so `--create-user` shipped granting the Cognito pool, client
    and domain lifecycle but not `AdminCreateUser`, and every deploy under
    the intended policy died after standing the whole transport up.

    An admin identity cannot see this: it satisfies every call. It surfaced
    the first time a deploy ran under a non-admin role, which is exactly why
    the runbook insists on one.

    The preflight cannot see it either, and that is structural, not a bug in
    the probes: `_DEPLOY_PROBES` carries one representative action per
    *statement*, to prove the statement is reachable. An action missing from
    a statement entirely leaves the representative intact, so the probe
    passes and the deploy still fails.

    The grants are therefore derived from the calls, not restated. Scope is
    the two modules that are purely MCP-deploy code; `pcluster_core.py` is
    excluded because `_setup_iam` and `_setup_mcp_infra` share it and the
    cluster half calls actions this policy rightly withholds.
    """

    # boto3 client variable -> IAM service prefix. apigateway is absent on
    # purpose: its IAM actions are HTTP verbs (`apigateway:POST`), not
    # method names, so the snake_case-to-action mapping does not hold. s3 is
    # absent for a related reason -- `upload_file` is a transfer-manager
    # helper, not an API call, and `head_bucket` authorizes as
    # `s3:ListBucket`.
    _CLIENTS = {
        "cog": "cognito-idp",
        "lam": "lambda",
        "ecr": "ecr",
        "iam": "iam",
        "sts": "sts",
    }

    _SOURCES = ("deploy_mcp.py", os.path.join("mcp_server", "deploy.py"))

    # Not API calls: botocore helpers that reach the service, if at all,
    # through some other operation.
    _NOT_AN_API_CALL = {"get_waiter", "get_paginator"}

    # Called, deliberately ungranted. `preflight_deploy_permissions` is
    # written to return None rather than fail when it cannot simulate, so an
    # identity holding exactly this policy runs the deploy with the check
    # degraded -- which is the documented behavior, not an oversight.
    _DELIBERATELY_UNGRANTED = {"iam:SimulatePrincipalPolicy"}

    def _called_actions(self):
        pat = re.compile(r"\b(" + "|".join(self._CLIENTS) + r")\.([a-z_]+)\(")
        found = set()
        for rel in self._SOURCES:
            with open(os.path.join(REPO_ROOT, rel)) as fh:
                src = fh.read()
            for var, method in pat.findall(src):
                if method in self._NOT_AN_API_CALL:
                    continue
                action = "".join(p.title() for p in method.split("_"))
                found.add(f"{self._CLIENTS[var]}:{action}")
        return found

    def _granted(self):
        out = []
        for st in _load_policy("MCPDeployPolicy.json_src")["Statement"]:
            if st.get("Effect") != "Allow":
                continue
            acts = st.get("Action", [])
            out += acts if isinstance(acts, list) else [acts]
        return out

    def test_every_action_the_deploy_calls_is_granted(self):
        granted = self._granted()
        missing = sorted(
            a for a in self._called_actions()
            if a not in self._DELIBERATELY_UNGRANTED
            and not any(fnmatch.fnmatch(a, g) for g in granted)
        )
        assert not missing, (
            f"MCPDeployPolicy does not grant {missing}; deploy_mcp.py calls "
            f"them, so a deploy under this policy fails partway through with "
            f"AccessDenied after creating real resources"
        )

    def test_the_create_user_path_is_granted(self):
        """The two that shipped missing, pinned by name.

        The derivation above would catch them again only while the calls stay
        in a file it reads; these are the ones a live deploy actually died
        on, so they are named as well as derived.
        """
        granted = self._granted()
        for action in ("cognito-idp:AdminCreateUser",
                       "cognito-idp:AdminSetUserPassword"):
            assert any(fnmatch.fnmatch(action, g) for g in granted), (
                f"MCPDeployPolicy does not grant {action}; --create-user "
                f"fails after the whole transport is already deployed"
            )

    def test_the_derivation_reads_something(self):
        """Vacuity guard: both filters above are ways to read nothing."""
        called = self._called_actions()
        assert "cognito-idp:AdminCreateUser" in called
        assert "lambda:CreateFunction" in called
        assert len(called) > 15, f"derivation found only {len(called)} calls"

    def test_the_deploy_policy_still_cannot_manage_arbitrary_users(self):
        """The floor is not a licence to raise the ceiling.

        `cognito-idp:*` would satisfy both tests above while handing the
        deployer every Cognito action in the account. The pool the grant
        reaches is unscoped already (`Resource: "*"`, forced -- the pool ID
        is not known until it is created), so the action list is the only
        bound left.
        """
        for action in ("cognito-idp:AdminDeleteUser",
                       "cognito-idp:AdminAddUserToGroup",
                       "cognito-idp:AdminUpdateUserAttributes"):
            assert not any(fnmatch.fnmatch(action, g) for g in self._granted()), (
                f"MCPDeployPolicy grants {action}; the deploy only ever "
                f"creates a user and sets its password"
            )


class TestRouterPolicyStaysNearZero:
    """The Router Lambda is the internet-facing endpoint behind API Gateway.
    The entire 5-Lambda split exists to keep blast radius off it: it parses
    a JSON-RPC body, picks a handler, and forwards. It executes no tool
    logic, so it must carry none of the handlers' permission breadth --
    if it ever gains PCluster IAM, the split has bought nothing and the
    most exposed component is also the most privileged.

    The handler function names are pinned here rather than only inside the
    policy JSON because _setup_mcp_infra has to create functions with
    exactly these names for the ARNs to match; a rename in one place and
    not the other produces a router that is denied at runtime, which is a
    deployment-time failure rather than a test-time one.
    """

    _FILE = "MCPRouterLambda.json_src"
    _HANDLERS = [
        "pclustermaker-mcp-read-only",
        "pclustermaker-mcp-fleet-toggle",
        "pclustermaker-mcp-stack-mutation",
        "pclustermaker-mcp-stack-mutation-node",
    ]

    def _statements(self):
        return _load_policy(self._FILE)["Statement"]

    def test_it_grants_exactly_one_action(self):
        actions = set()
        for stmt in self._statements():
            a = stmt["Action"]
            actions |= set([a] if isinstance(a, str) else a)
        assert actions == {"lambda:InvokeFunction"}, (
            f"the router must carry only lambda:InvokeFunction, got {sorted(actions)}"
        )

    def test_it_names_every_handler_and_nothing_else(self):
        resources = []
        for stmt in self._statements():
            r = stmt["Resource"]
            resources += [r] if isinstance(r, str) else r
        suffixes = sorted(r.rsplit(":function:", 1)[-1] for r in resources)
        assert suffixes == sorted(self._HANDLERS)

    def test_no_resource_is_a_wildcard(self):
        """A `function:*` (or bare `*`) would let the router invoke any
        Lambda in the account, which is the one thing scoping it to four
        ARNs is for."""
        for stmt in self._statements():
            r = stmt["Resource"]
            for res in [r] if isinstance(r, str) else r:
                assert "*" not in res, f"wildcard resource in router policy: {res}"

    def test_it_carries_no_pcluster_permissions(self):
        """Vacuity guard against the failure mode this class exists for:
        someone adding 'just one' EC2 or CloudFormation grant so the router
        can answer a status question itself instead of forwarding."""
        services = set()
        for stmt in self._statements():
            a = stmt["Action"]
            for act in [a] if isinstance(a, str) else a:
                services.add(act.split(":", 1)[0])
        assert services == {"lambda"}, f"router reaches beyond lambda: {sorted(services)}"


class TestTheNodeLinesReadAsOneGroup:
    """Head Node, Login Node and the queues are one logical group -- what
    the cluster is made of -- and the launch summary split the head node
    away from the rest with VPC Name and Availability Zone in between,
    while the build summary already grouped them. An operator comparing the
    two saw the same facts in two orders.

    Order is asserted by line index, not by presence: the existing login
    node tests check only that the line appears, so nothing pinned where.
    """

    def _index(self, text, label):
        lines = [l.strip() for l in text.splitlines()]
        for i, line in enumerate(lines):
            if line.startswith(label):
                return i
        raise AssertionError(f"{label!r} not in:\n{text}")

    def test_the_build_summary_does_too(self):
        """The other surface, and the one that was already right; pinned so it
        stays that way and so the two cannot drift apart again.

        The build summary reads dozens of inputs and calls out to the pricing
        API, so its order is asserted on its own source rather than by driving
        it -- the lines are prints in a fixed sequence, which is exactly what an
        ordering assertion needs to see. Read out of whichever function carries
        them, so extracting the block into a helper does not silently stop this
        from covering anything."""
        core = _pcluster_core()
        for candidate in ("_print_build_summary", "core_create_cluster"):
            if hasattr(core, candidate):
                source = _core_function_source(candidate)
                if "Cluster Build Summary" in source:
                    break
        else:
            raise AssertionError("no function prints the cluster build summary")
        printed = [
            line.strip() for line in source.splitlines()
            if line.strip().startswith(("print(", 'print(f"'))
        ]

        def _index(label):
            for i, line in enumerate(printed):
                if label in line:
                    return i
            raise AssertionError(f"{label!r} not printed by _print_build_summary")

        head = _index("Head Node:")
        login = _index("Login Node:")
        assert login == head + 1, (
            "the node lines must be adjacent, head first, in the build summary "
            f"too:\n" + "\n".join(printed[head - 1:login + 2])
        )
        assert _index("Availability Zone:") < head
        assert _index("CPU Queue:") > login

    def test_the_launch_summary_is_the_one_the_operator_sees(self, capsys):
        """What actually prints during a build is
        `print_cluster_launch_summary` in `pcluster_core.py`. The four tests
        that used to sit above this one evaluated create_pcluster.yml's own
        launch-summary literal instead, and reverting *this* function's order
        left every one of them green -- which is why they were not what the
        rule needed even before the playbook was deleted.

        The network lines coming before the nodes is the vacuity guard on the
        fix's direction: moving Login Node *up* would also make the pair
        adjacent, but would strand the network context below the hardware.
        """
        src = os.path.join(REPO_ROOT, "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        import pcluster_core

        ctx = {
            "cluster_name": "osiris", "cluster_serial_datestamp": "202608212212",
            "region": "us-east-1",
            "base_os": "ubuntu2404arm", "scheduler": "slurm",
            "headnode_instance_type": "c8g.large",
            "vpc_name": "vpc_default", "az": "us-east-1a",
            "enable_loginnode": "true", "loginnode_instance_type": "c8g.large",
            "loginnode_count": 1,
            "enable_cpu_queue": "true",
            "cpu_instance_types": ["c8g.xlarge"],
            "initial_cpu_queue_size": 1, "max_cpu_queue_size": 8,
        }
        pcluster_core.print_cluster_launch_summary(ctx, launch_timestamp="now")
        text = capsys.readouterr().out

        head = self._index(text, "Head Node:")
        login = self._index(text, "Login Node:")
        assert login == head + 1, (
            "Head Node must sit directly above Login Node in the summary an "
            f"operator actually reads:\n{text}"
        )
        assert self._index(text, "Availability Zone:") < head
        assert self._index(text, "VPC Name:") < head
        assert self._index(text, "CPU Queue:") > login


class TestTheSummariesReportThePClusterVersion:
    """Which aws-parallelcluster drove a build is the first thing anyone
    asks when a cluster behaves differently from the last one, and it was
    on none of the summaries. Threaded through the full pipeline the repo
    requires: Python vars dict -> vars_file.j2 -> template, plus conftest.
    """

    def _index(self, text, label):
        for i, line in enumerate(l.strip() for l in text.splitlines()):
            if line.startswith(label):
                return i
        raise AssertionError(f"{label!r} not in:\n{text}")

    def test_the_version_is_read_from_pclusters_own_accessor(self):
        """Not a `pcluster version` subprocess -- that binary is absent on
        Lambda and every shell-out to it was deliberately removed."""
        src = os.path.join(REPO_ROOT, "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        import pcluster_core

        assert pcluster_core.installed_pcluster_version() != "unknown"
        from pcluster.utils import get_installed_version

        assert pcluster_core.installed_pcluster_version() == get_installed_version()

    def test_a_lookup_failure_does_not_abort_the_build(self, monkeypatch):
        """A summary line must never be the thing that fails a build."""
        src = os.path.join(REPO_ROOT, "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        import pcluster_core

        import builtins

        real_import = builtins.__import__

        def boom(name, *a, **kw):
            if name == "pcluster.utils":
                raise ImportError("simulated")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", boom)
        assert pcluster_core.installed_pcluster_version() == "unknown"

    def test_the_launch_summary_reports_it_under_the_scheduler(self, capsys):
        src = os.path.join(REPO_ROOT, "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        import pcluster_core

        ctx = {
            "cluster_name": "osiris", "cluster_serial_datestamp": "202608212212",
            "region": "us-east-1", "base_os": "ubuntu2404arm", "scheduler": "slurm",
            "pcluster_version": "3.15.1",
            "headnode_instance_type": "c8g.large", "vpc_name": "vpc_default",
            "az": "us-east-1a",
        }
        pcluster_core.print_cluster_launch_summary(ctx, launch_timestamp="now")
        text = capsys.readouterr().out
        assert "PCluster Version:  3.15.1" in text
        assert self._index(text, "PCluster Version:") == (
            self._index(text, "HPC Scheduler:") + 1
        )

    def test_the_vars_file_carries_it(self, cluster_params):
        """vars_file.j2 renders under StrictUndefined, so a template
        referencing it without the Python side defining it raises."""
        assert _vars_for({}, cluster_params).get("pcluster_version")

    def test_conftest_supplies_it(self, cluster_params):
        """The repo's rule: a new template variable must reach
        tests/conftest.py or every template test renders without it."""
        assert "pcluster_version" in cluster_params


class TestTheWaitProgressLineSpacing:
    """Both long waits print a per-attempt progress line, and they must
    agree on its shape -- an operator watching a build then a teardown sees
    the same format, not two.

    The time element carries exactly one space on each side. The previous
    form right-aligned the number inside the brackets (`[  1m]`), which put
    two spaces after the opening bracket and none before the closing one.
    """

    def _printers(self):
        import ast

        with open(os.path.join(REPO_ROOT, "src", "pcluster_core.py")) as fh:
            tree = ast.parse(fh.read())
        found = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            # The ordered constant parts, NOT their concatenation: joining
            # them turns "  [ " + <elapsed> + " ] " into "  [  ] ", which
            # cannot be told apart from two literal spaces.
            parts = [p.value for p in node.values if isinstance(p, ast.Constant)]
            if any(p.endswith("[ ") for p in parts) and any(
                p.startswith(" ] ") for p in parts
            ):
                found.append(parts)
        return found

    def test_both_printers_were_found(self):
        """Vacuity guard: a scan matching nothing passes the checks below
        in silence. There are two -- create and teardown."""
        assert len(self._printers()) == 2, self._printers()

    def test_the_time_element_has_one_space_on_each_side(self):
        """Exactly one space inside each bracket, checked on the format
        string's parts so an interpolation is not mistaken for padding."""
        for parts in self._printers():
            opening = [p for p in parts if p.endswith("[ ")]
            closing = [p for p in parts if p.startswith(" ] ")]
            assert opening, parts
            assert closing, parts
            assert not any(p.endswith("[  ") for p in parts), parts
            assert not any(p.startswith("  ] ") for p in parts), parts

    def test_no_printer_pads_the_number_inside_the_brackets(self):
        """`:>3d` is what produced `[  1m]`; the padding now lives inside
        _elapsed_str's zero-filled fields, not in the bracket format."""
        with open(os.path.join(REPO_ROOT, "src", "pcluster_core.py")) as fh:
            body = fh.read()
        assert_source_is_real(body, 'test_no_printer_pads_the_number_inside_the_brackets')
        assert ":>3d}m]" not in body
        assert_source_is_real(body, 'test_no_printer_pads_the_number_inside_the_brackets')
        assert ":>3d}m ]" not in body

    def test_minutes_are_not_zero_padded(self):
        """`01m00s` reads as a timestamp; it is a duration. Minutes are
        space-padded so the leading zero is gone and the column still does
        not move -- reverting to `{minutes:02d}` satisfies the fixed-width
        test above and reintroduces exactly what was reported.

        Seconds keep their zero deliberately: `1m5s` and `1m50s` are
        different quantities and dropping it there is ambiguous.
        """
        src = os.path.join(REPO_ROOT, "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        import pcluster_core

        assert pcluster_core._elapsed_str(90) == " 1m30s"
        assert pcluster_core._elapsed_str(630) == "10m30s"
        # A leading zero *followed by a digit* is padding; "0m" on its own
        # is the real value at zero minutes, not a padded one.
        import re as _re

        for t in (0, 60, 300, 540, 3540, 90, 630):
            label = pcluster_core._elapsed_str(t)
            assert not _re.match(r"^0\d", label), (t, label)

    def test_the_cloudformation_status_is_shown_only_when_it_differs(self):
        """Both printers appended "(CloudFormation: X)" unconditionally,
        and the two statuses agree for the whole of a healthy build -- so
        every line carried the same value twice. They diverge exactly when
        it matters: a failed create reads CREATE_FAILED beside
        CloudFormation's ROLLBACK_IN_PROGRESS.

        Asserted on the source of both printers rather than by driving a
        wait, since reaching them needs a live cluster. The guard is that
        neither builds the detail without comparing.
        """
        with open(os.path.join(REPO_ROOT, "src", "pcluster_core.py")) as fh:
            body = fh.read()

        unconditional = 'f" (CloudFormation: {cfn_status})" if cfn_status else ""'
        assert_source_is_real(body, 'test_the_cloudformation_status_is_shown_only_when_it_differs')
        assert unconditional not in body, (
            "a printer appends the CloudFormation status without comparing it"
        )
        guarded = body.count("if cfn_status and cfn_status != status else")
        assert guarded == 2, (
            f"expected both the create and delete printers to compare, "
            f"found {guarded}"
        )

    def test_a_whole_minute_drops_its_seconds(self):
        """The create wait polls exactly once a minute, so every label
        carried a redundant "00s". A whole minute prints as "1m"/"13m";
        anything else keeps its seconds.

        This gives up fixed width on purpose -- an earlier version padded
        both fields so the column after the bracket never moved, and the
        delete wait now alternates "0m30s"/"1m", shifting it by three. The
        property that actually mattered in the original report is the one
        below: every poll gets a *distinct* label.
        """
        src = os.path.join(REPO_ROOT, "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        import pcluster_core

        assert pcluster_core._elapsed_str(60) == " 1m"
        assert pcluster_core._elapsed_str(780) == "13m"
        assert pcluster_core._elapsed_str(0) == " 0m"
        assert pcluster_core._elapsed_str(90) == " 1m30s"
        assert pcluster_core._elapsed_str(65) == " 1m05s", (
            "seconds keep their zero: 1m5s and 1m50s are different"
        )

        # The minutes column does not move for any run either wait can
        # reach, which is what the space padding buys.
        minute_fields = {
            pcluster_core._elapsed_str(t).split("m")[0]
            for t in (60, 540, 600, 780, 3540)
        }
        assert {len(f) for f in minute_fields} == {2}, minute_fields

    def test_every_poll_of_a_delete_gets_a_distinct_label(self):
        """The reported symptom, stated directly."""
        src = os.path.join(REPO_ROOT, "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        import pcluster_core

        interval = pcluster_core._DELETE_POLL_SECONDS
        labels = [pcluster_core._elapsed_str((a + 1) * interval) for a in range(12)]
        assert len(set(labels)) == len(labels), labels

    def test_the_printers_cannot_disagree_with_their_own_wait(self):
        """Both printers used to hardcode an interval (30 and 60) beside a
        delay_seconds they did not read, so changing either default made
        the elapsed time silently wrong. One constant now drives both the
        wait's default and the label."""
        import inspect

        src = os.path.join(REPO_ROOT, "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        import pcluster_core

        for fn, const in (
            (pcluster_core.run_cluster_create_and_classify, "_CREATE_POLL_SECONDS"),
            (pcluster_core.run_cluster_delete_and_classify, "_DELETE_POLL_SECONDS"),
        ):
            default = inspect.signature(fn).parameters["delay_seconds"].default
            assert default == getattr(pcluster_core, const), fn.__name__

    def test_no_printer_reintroduces_a_literal_interval(self):
        with open(os.path.join(REPO_ROOT, "src", "pcluster_core.py")) as fh:
            body = fh.read()
        assert_source_is_real(body, 'test_no_printer_reintroduces_a_literal_interval')
        assert "(attempt + 1) * 30" not in body
        assert_source_is_real(body, 'test_no_printer_reintroduces_a_literal_interval')
        assert "(attempt + 1) * 60" not in body


class TestTheStagingDirectoryIsValidOnBothMachines:
    """stage_dir is created and written on the operator's box *and* on the
    head node: _transfer_staging_dir ssh's `mkdir -p` for its parent and
    scp's the tree across. So it has to be a path that exists on a Linux
    node, not merely one that exists locally.

    The migration replaced the playbook's `/tmp/...` literal with
    `tempfile.gettempdir()`, which is correct for a local scratch directory
    and wrong for this one: on macOS it is /var/folders/<...>/T, which the
    head node cannot create (Permission denied) and which means nothing
    there. It failed a live build 15 minutes in, after the stack was
    already up -- the same shape as the S3 region bug, a local-context
    value used in a remote context.
    """

    def test_the_staging_root_is_not_the_local_temp_directory(self):
        """The specific regression. On Linux gettempdir() *is* /tmp, so a
        developer on Linux would never see this -- which is why it is
        asserted against the source rather than against a rendered value."""
        with open(os.path.join(REPO_ROOT, "src", "pcluster_core.py")) as fh:
            body = fh.read()
        assert "tempfile.gettempdir(), \"_ParallelClusterMaker_stage\"" not in body
        assert 'os.path.join(\n            "/tmp", "_ParallelClusterMaker_stage"' in body

    def test_the_rendered_value_is_an_absolute_posix_path(self, cluster_params):
        stage = _vars_for({}, cluster_params)["stage_dir"]
        assert stage.startswith("/tmp/"), stage
        assert "\\" not in stage

    def test_the_vars_file_does_not_restate_the_literal(self):
        """Two sources for one path is how they came to disagree: the
        template still said /tmp while Python had moved to gettempdir(),
        so the rendered vars file and the running code named different
        directories."""
        with open(os.path.join(REPO_ROOT, "templates", "vars_file.j2")) as fh:
            body = fh.read()
        assert 'stage_dir: "{{ stage_dir }}"' in body
        assert "/tmp/_ParallelClusterMaker_stage/{{ cluster_serial_number }}" not in body

    def test_the_transfer_uses_the_parent_of_that_path(self):
        """Guards the shape the fix depends on: the remote mkdir targets
        dirname(stage_dir), so a relative or bare-name stage_dir would
        mkdir something unexpected on the head node."""
        src = os.path.join(REPO_ROOT, "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        import inspect

        import pcluster_core

        body = inspect.getsource(pcluster_core._transfer_staging_dir)
        assert "os.path.dirname(stage_dir" in body
        assert "mkdir -p" in body


class TestTheAccessScriptsPreferSSM:
    """access_cluster.j2 and grafana_tunnel.j2 route ssh through Session
    Manager when they can, so neither needs an inbound port 22 or a
    reachable address.

    Deliberately still `ssh`, not `aws ssm start-session`: the operator
    keeps landing as ec2_user with their key, and scp, rsync, agent
    forwarding and -L forwards keep working -- none of which a bare
    start-session provides, and which would instead land them as ssm-user
    with the wrong $HOME, PATH and Slurm environment.
    """

    _CTX = dict(cluster_name="osiris", region="us-east-1",
                ssh_keypair="/k.pem", ec2_user="ubuntu")

    def _render(self, name):
        src = os.path.join(REPO_ROOT, "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        import pcluster_core

        return pcluster_core.render_template(
            os.path.join(REPO_ROOT, "templates"), name, **self._CTX
        )

    @pytest.mark.parametrize("template", [
        "access_cluster.j2", "grafana_tunnel.j2",
    ])
    def test_it_resolves_an_instance_id(self, template):
        """SSM addresses an instance, ssh an address. Without this lookup
        there is nothing to target."""
        body = self._render(template)
        assert "InstanceId" in body, template

    @pytest.mark.parametrize("template", [
        "access_cluster.j2", "grafana_tunnel.j2",
    ])
    def test_it_uses_the_ssm_proxycommand(self, template):
        body = self._render(template)
        assert "AWS-StartSSHSession" in body, template
        assert "ProxyCommand" in body, template
        assert "portNumber=%p" in body, template

    @pytest.mark.parametrize("template", [
        "access_cluster.j2", "grafana_tunnel.j2",
    ])
    def test_it_falls_back_rather_than_failing(self, template):
        """An operator without the plugin still gets a shell, and is told
        why. Chosen over hard-failing so the scripts keep working for
        anyone who has not installed it."""
        body = self._render(template)
        assert "session-manager-plugin" in body, template
        assert "WARNING" in body, template

    def test_the_tunnel_pid_matches_whichever_target_was_used(self):
        """The pgrep pattern matched the IP. Over SSM the command line
        carries `ubuntu@i-0abc...` instead, so the PID would never have
        been captured and `stop` would have silently done nothing -- the
        script would report success and leave the tunnel running."""
        body = self._render("grafana_tunnel.j2")
        pgrep_line = next(
            l for l in body.splitlines() if "pgrep" in l and "SSH_PID" in l
        )
        assert "SSH_TARGET" in pgrep_line, pgrep_line
        assert "HEAD_NODE_IP" not in pgrep_line, (
            "pgrep still matches the address, which is wrong over SSM"
        )

    def test_the_empty_proxy_array_is_guarded(self):
        """`"${PROXY_ARGS[@]}"` on an empty array is an unbound-variable
        error under `set -u` on bash before 4.4, and the script has no
        control over which bash an operator runs."""
        body = self._render("grafana_tunnel.j2")
        assert 'PROXY_ARGS[@]+"${PROXY_ARGS[@]}"' in body

    @pytest.mark.parametrize("template", [
        "access_cluster.j2", "grafana_tunnel.j2",
    ])
    def test_the_direct_path_still_exists(self, template):
        """Vacuity guard: removing the fallback entirely would satisfy the
        SSM assertions and strand anyone without the plugin."""
        body = self._render(template)
        assert "HEAD_NODE_IP" in body, template


class TestNoAptFetchOnTheHeadNodeIsUnbounded:
    """apt's network defaults are effectively unbounded, and preinstall.j2
    runs as OnNodeStart on the *head node* -- the critical path for the
    whole cluster, since nothing the head node exports exists until it
    finishes.

    Observed on cluster stageb: `apt-get -y update` ran for 17+ minutes
    while both mirrors answered in under 90 ms. apt's http/https fetch
    methods sat in CLOSE-WAIT -- the remotes had closed the connections and
    apt never noticed -- so `/opt/parallelcluster/shared` was never
    exported, the login node's ASG abandoned instance after instance on
    Heartbeat Timeout unable to NFS-mount it, and the stack never left
    CREATE_IN_PROGRESS. A hang on the head node is not a slow build; it is
    every other node starved behind it.
    """

    _NEEDS_BOUND = ("update", "dist-upgrade", "install")

    def _render_preinstall(self, params):
        env = _make_env(os.path.join(REPO_ROOT, "templates"))
        return env.get_template("preinstall.j2").render(**params)

    def _apt_lines(self, rendered):
        return [
            ln.strip() for ln in rendered.splitlines()
            if "apt-get" in ln and not ln.strip().startswith("#")
        ]

    def test_every_apt_get_carries_the_timeouts(self, cluster_params):
        rendered = self._render_preinstall(cluster_params)
        lines = self._apt_lines(rendered)
        assert lines, "no apt-get call rendered -- the ubuntu arm did not render"
        for ln in lines:
            assert "$APT_TIMEOUTS" in ln, f"unbounded apt fetch: {ln}"

    def test_the_timeouts_actually_bound_something(self, cluster_params):
        """A variable that expands to nothing would satisfy the test above
        while changing no behavior."""
        rendered = self._render_preinstall(cluster_params)
        assign = [
            ln for ln in rendered.splitlines()
            if ln.strip().startswith("APT_TIMEOUTS=")
        ]
        assert len(assign) == 1, f"expected one assignment, got {assign}"
        body = assign[0]
        assert "Acquire::http::Timeout" in body
        assert "Acquire::https::Timeout" in body
        assert "Acquire::Retries" in body

    def test_a_retry_count_alone_is_not_enough(self, cluster_params):
        """Retries without a timeout retries nothing: the first attempt
        never returns, which is the exact failure. The timeout is the
        load-bearing half."""
        rendered = self._render_preinstall(cluster_params)
        assign = [ln for ln in rendered.splitlines()
                  if ln.strip().startswith("APT_TIMEOUTS=")][0]
        timeouts = [t for t in assign.split() if "Timeout=" in t]
        assert timeouts, "retries are set but nothing bounds a single attempt"

    def test_the_dnf_arm_needs_no_equivalent(self, cluster_params_rhel):
        """dnf carries its own timeouts and this arm has never hung; the
        test exists to record that the asymmetry is deliberate rather than
        an oversight, so nobody 'restores symmetry' with an untested
        change."""
        rendered = self._render_preinstall(cluster_params_rhel)
        assert "apt-get" not in rendered
        assert "dnf" in rendered


class TestATransientMirrorCannotFailTheCluster:
    """`apt-get update` and `dist-upgrade` are opportunistic; the `install`
    of python3/python3-dev is not. Cluster stageb died twice on the EC2
    regional Ubuntu ARM mirror -- once hanging in CLOSE-WAIT, then, with
    timeouts added, failing fast on `503 Service Unavailable` for
    mutter-common-bin, network-manager, gnome-control-center and snapd:
    330 MB of desktop packages nothing on an HPC head node imports.

    Losing that upgrade should cost nothing. On the *head node* it cost the
    whole cluster, because every other node waits on exports that never
    happen. Same guard, same reason, as the GPU monitoring-tool installs in
    postinstall.j2.
    """

    def _render_preinstall(self, params):
        env = _make_env(os.path.join(REPO_ROOT, "templates"))
        return env.get_template("preinstall.j2").render(**params)

    def _line_for(self, rendered, needle):
        for ln in rendered.splitlines():
            st = ln.strip()
            if st.startswith("#") or "apt-get" not in st:
                continue
            if needle in st:
                return st
        raise AssertionError(f"no apt-get line containing {needle!r}")

    def test_update_is_not_fatal(self, cluster_params):
        ln = self._line_for(self._render_preinstall(cluster_params), " update")
        assert "||" in ln, f"a failed index refresh would fail the node: {ln}"

    def test_dist_upgrade_is_not_fatal(self, cluster_params):
        ln = self._line_for(self._render_preinstall(cluster_params), "dist-upgrade")
        assert "||" in ln, f"a transient 503 would fail the node: {ln}"

    def test_the_required_install_is_still_fatal(self, cluster_params):
        """The vacuity guard. python3 and python3-dev are genuinely
        required -- a cluster without them is not one -- so making
        *everything* non-fatal would turn a broken node into a silently
        broken one."""
        ln = self._line_for(self._render_preinstall(cluster_params), " install ")
        assert "python3" in ln
        assert "||" not in ln, f"a required install must still fail loudly: {ln}"

    def test_the_warning_names_what_was_skipped(self, cluster_params):
        """A silent `|| true` is worse than the failure: the operator has no
        way to know the node is running the AMI's package set.

        The warning must go to **stdout**, not stderr. This test used to
        require `>&2` and was therefore enforcing the defect: cfn-init
        captures stdout only, so a warning on stderr reaches no stream at
        all and the operator it is written for cannot see it. Proven on
        `acctproof4`, where a guard fired and its output is absent from
        CloudWatch while an `echo` from the same script is present."""
        rendered = self._render_preinstall(cluster_params)
        for needle in (" update", "dist-upgrade"):
            ln = self._line_for(rendered, needle)
            assert "WARNING" in ln, f"failure is swallowed silently: {ln}"
            assert ">&2" not in ln, (
                f"the warning goes to stderr, which cfn-init discards: {ln}"
            )


class TestPostinstallsRequiredInstallsRetry:
    """postinstall.j2's package installs are *required* -- Lmod will not
    build without lua and bc -- so unlike preinstall's opportunistic
    upgrade they cannot be made non-fatal. Retrying is the only correct
    answer to a transient mirror error, and they had neither retries nor
    timeouts.

    Cluster stageb's third build died here on `503 Service Unavailable`
    from us-east-1.ec2.ports.ubuntu.com for liblua5.1-0, lua5.1,
    lua-socket, lua-sec and luarocks -- the same mirror that had already
    taken the two builds before it down, once by hanging and once by
    failing the opportunistic upgrade.
    """

    def _render_postinstall(self, params):
        env = _make_env(os.path.join(REPO_ROOT, "templates"))
        return env.get_template("postinstall.j2").render(**params)

    def _required_installs(self, rendered):
        """The fatal apt lines: an install with no `||` guard."""
        out = []
        for ln in rendered.splitlines():
            st = ln.strip()
            if st.startswith("#") or "apt-get" not in st or " install" not in st:
                continue
            if "||" in st:          # the guarded, opportunistic ones
                continue
            out.append(st)
        return out

    def test_every_required_apt_install_retries(self, cluster_params):
        lines = self._required_installs(self._render_postinstall(cluster_params))
        assert lines, "no fatal apt install rendered -- the sweep is vacuous"
        for ln in lines:
            assert "$APT_TIMEOUTS" in ln, f"a required install with no retries: {ln}"

    def test_the_retry_budget_is_defined_before_any_use(self, cluster_params):
        """postinstall.j2 runs under `set -euo pipefail`. A reference on a
        path where the variable was never assigned is an unbound-variable
        error, which kills the node -- so the assignment must not sit
        inside a node-type gate that its uses are outside of."""
        rendered = self._render_postinstall(cluster_params)
        lines = rendered.splitlines()
        assign = next(i for i, ln in enumerate(lines)
                      if ln.strip().startswith("APT_TIMEOUTS="))
        uses = [i for i, ln in enumerate(lines)
                if "$APT_TIMEOUTS" in ln and not ln.strip().startswith("#")]
        assert uses, "the variable is assigned but never used"
        assert min(uses) > assign, "used before assignment"

    def test_the_budget_actually_bounds_and_retries(self, cluster_params):
        rendered = self._render_postinstall(cluster_params)
        assign = [ln for ln in rendered.splitlines()
                  if ln.strip().startswith("APT_TIMEOUTS=")]
        assert len(assign) == 1, f"expected one assignment, got {len(assign)}"
        body = assign[0]
        assert "Acquire::Retries" in body
        assert "Acquire::http::Timeout" in body

    def test_the_opportunistic_installs_stay_guarded(self, cluster_params_gpu_queue_enabled):
        """The vacuity guard in the other direction: nvtop/htop are
        diagnostics nothing in the job path imports, and they must stay
        non-fatal. Making everything retry-and-fail would cost a compute
        node its bootstrap over a missing diagnostic."""
        rendered = self._render_postinstall(cluster_params_gpu_queue_enabled)
        guarded = [ln.strip() for ln in rendered.splitlines()
                   if "htop" in ln and "install" in ln and not ln.strip().startswith("#")]
        assert guarded, "the GPU diagnostics block did not render"
        for ln in guarded:
            assert "||" in ln, f"a diagnostic install became fatal: {ln}"


class TestEveryPackageManagerCallIsWallClockBounded:
    """apt's own Acquire::* timeouts bound individual socket operations, not
    the command, and that is not enough. Cluster stageb's fourth build ran
    `apt-get update` for **30+ minutes with those options set** -- a mirror
    that dribbles rather than stalling outright never trips them -- and
    `|| echo WARNING` cannot help a process that never exits.

    The head node is the critical path for the whole cluster, so a hang
    there starves every login and compute node behind it. timeout(1) is the
    guarantee; APT_TIMEOUTS is best-effort on top of it.

    Semantics verified empirically before this was written, rather than
    assumed: `timeout N cmd` exits 124 and kills the child; `|| echo` on a
    timeout emits the warning; an unguarded timeout under `set -e` aborts;
    `timeout N env VAR=x cmd` propagates the variable; and an unquoted
    `$APT_TIMEOUTS` still word-splits into separate argv entries.
    """

    _TEMPLATES = ("preinstall.j2", "postinstall.j2")

    def _render(self, name, params):
        env = _make_env(os.path.join(REPO_ROOT, "templates"))
        return env.get_template(name).render(**params)

    def _pm_lines(self, rendered):
        """Package-manager invocations that reach the network."""
        out = []
        for ln in rendered.splitlines():
            st = ln.strip()
            if st.startswith("#") or not st.startswith("sudo "):
                continue
            if "apt-get" not in st:
                continue
            if any(w in st for w in (" update", " install", "dist-upgrade")):
                out.append(st)
        return out

    @pytest.mark.parametrize("template", _TEMPLATES)
    def test_every_apt_call_carries_a_wall_clock_bound(self, template, cluster_params):
        lines = self._pm_lines(self._render(template, cluster_params))
        assert lines, f"{template} rendered no apt call -- the sweep is vacuous"
        for ln in lines:
            assert "timeout " in ln, f"{template}: unbounded wall clock: {ln}"

    @pytest.mark.parametrize("template", _TEMPLATES)
    def test_the_bound_precedes_the_command(self, template, cluster_params):
        """`sudo timeout N apt-get ...`, not `sudo apt-get ... timeout`.
        timeout must be the parent or it bounds nothing."""
        for ln in self._pm_lines(self._render(template, cluster_params)):
            before = ln.split("apt-get")[0]
            assert "timeout " in before, f"{template}: timeout is not the parent: {ln}"

    @pytest.mark.parametrize("template", _TEMPLATES)
    def test_each_bound_is_a_positive_number_of_seconds(self, template, cluster_params):
        import re

        for ln in self._pm_lines(self._render(template, cluster_params)):
            m = re.search(r"timeout (\d+)", ln)
            assert m, f"{template}: no numeric bound: {ln}"
            assert int(m.group(1)) > 0

    def test_dist_upgrade_uses_env_not_a_bare_assignment(self, cluster_params):
        """`timeout N DEBIAN_FRONTEND=x apt-get` is not valid: timeout execs
        its argument directly and a VAR=VAL prefix is shell syntax, so the
        variable would be treated as the program name. `env` is required."""
        for ln in self._pm_lines(self._render("preinstall.j2", cluster_params)):
            if "dist-upgrade" not in ln:
                continue
            assert "DEBIAN_FRONTEND" in ln
            i_to, i_env = ln.index("timeout "), ln.index("DEBIAN_FRONTEND")
            assert "env " in ln[i_to:i_env], f"needs env(1) after timeout: {ln}"

    def test_the_required_installs_are_still_fatal(self, cluster_params):
        """timeout(1) exits 124, which `set -e` treats as failure -- so a
        bounded required install still fails the node rather than silently
        continuing without python3 or lua."""
        for template in self._TEMPLATES:
            for ln in self._pm_lines(self._render(template, cluster_params)):
                if " install " not in ln:
                    continue
                if any(p in ln for p in ("nvtop", "htop")):
                    continue        # diagnostics, deliberately non-fatal
                assert "||" not in ln, f"{template}: required install is guarded: {ln}"


class TestTheLogThatSaysWhyABootstrapFailedIsShipped:
    """PCluster's CloudWatch agent ships `/var/log/cfn-init.log` and not
    `/var/log/cfn-init-cmd.log`.

    The first records *that* a custom action ran and its return code; the
    second holds everything it printed -- postinstall, preinstall, the
    operator's own hook scripts and chef. Read on a live node to confirm it,
    rather than inferred: the shipped log had "Running command
    runpostinstall" and a status, and the unshipped one had the awscli
    unzip output and the operator hook's own echo.

    So a failed node left a CloudWatch record saying a command exited
    non-zero and nothing about why, and the evidence died with the
    instance. `postinstall.j2` adds the file to the agent's collect_list.
    """

    def _rendered(self, cluster_params):
        env = _make_env(os.path.join(REPO_ROOT, "templates"))
        return env.get_template("postinstall.j2").render(**cluster_params)

    def test_the_unshipped_log_is_added_to_the_agent(self, cluster_params):
        r = self._rendered(cluster_params)
        assert "/var/log/cfn-init-cmd.log" in r
        assert "amazon-cloudwatch-agent.d" in r, (
            "the agent config path is gone; nothing ships the file")

    def _code(self, cluster_params):
        """The block with comment lines stripped.

        The prose above it names the same identifiers the code sets, so a
        plain substring check passes with the code deleted -- which is
        exactly what a mutation run showed before this existed.
        """
        r = self._rendered(cluster_params)
        block = r[r.index("PCM_CW_AGENT_CFG="):]
        block = block[:block.index("# Set values for some important")]
        return "\n".join(
            ln for ln in block.splitlines() if not ln.lstrip().startswith("#"))

    def test_it_adds_no_key_the_agent_does_not_already_accept(self, cluster_params):
        """The entry is a copy of one the agent already validates, with two
        fields changed. Anything else is a guess about a third-party schema.

        `from_beginning` was such a guess -- asserted from memory, not read
        from the contract, and rejected with `Additional property
        from_beginning is not allowed`. Schema validation is whole-config,
        so the agent crash-looped and shipped *nothing*: worse than the one
        missing file this exists to add. A new file is tailed from the start
        by default, so it bought nothing even had it been valid.
        """
        code = self._code(cluster_params)
        assert "from_beginning" not in code, (
            "from_beginning is not in the agent's schema; it fails "
            "validation for the whole config"
        )
        assigned = [ln.split("]")[0].split("[")[-1].strip().strip('"\'')
                    for ln in code.splitlines()
                    if ln.strip().startswith("entry[")]
        assert set(assigned) == {"file_path", "log_stream_name"}, (
            f"the copied entry sets {sorted(assigned)}; only file_path and "
            f"log_stream_name may differ from the entry it was modelled on"
        )

    def test_it_appends_rather_than_replacing_the_agent_config(self, cluster_params):
        """That file is PCluster's. Rewriting it drops whatever the next
        release ships, silently, on every node."""
        code = self._code(cluster_params)
        assert "cl.append(entry)" in code, "no append; the config is being rebuilt"
        assert 'entry = dict(cl[0])' in code, (
            "the new entry is not modelled on an existing one, so it may "
            "omit fields the agent requires")

    def test_nothing_here_can_fail_the_node(self, cluster_params):
        """The whole point is to preserve evidence of a failure. Becoming a
        *cause* of one inverts that -- and on a compute node a non-zero exit
        feeds clustermgtd's relaunch loop toward protected mode."""
        r = self._rendered(cluster_params)
        block = r[r.index("PCM_CW_AGENT_CFG="):]
        block = block[:block.index("# Set values for some important")]
        assert "|| echo" in block, "the python edit is not guarded"
        assert block.count("|| echo") >= 2, (
            "both the config edit and the agent restart must be non-fatal")
        assert "2>/dev/null" in block
        # Skipped whole when the agent is not where it was, rather than
        # assuming the path.
        assert 'if [ -f "$PCM_CW_AGENT_CFG" ]' in block

    def test_it_is_not_gated_on_node_type(self, cluster_params):
        """A compute node is the case that needs this most: it fails, is
        relaunched, and takes its logs with it every time."""
        r = self._rendered(cluster_params)
        head = r.index('NODE_TYPE="HeadNode"')
        blk = r.index("PCM_CW_AGENT_CFG=")
        between = r[head:blk]
        assert '== "HeadNode"' not in between.split("PCM_CW_AGENT_CFG")[0][-400:], (
            "the block sits inside a head-node gate")
