"""
Template render tests.

Renders every Jinja2 template in templates/ and performance/jinja2/ using
StrictUndefined.  Any variable referenced in a template that is missing from
the fixture raises UndefinedError and fails the test immediately.

Filters used only by Ansible at playbook runtime (bool, upper, lookup) are
stubbed out so plain Python Jinja2 can render them without crashing.
"""

import json
import os
import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TEMPLATE_DIRS = [
    os.path.join(REPO_ROOT, "templates"),
]

# Templates that are not rendered by Python at all — skip them.
# (JSON policy templates are not Jinja2 text, they're shell-substituted separately.)
SKIP_TEMPLATES = {
    "LustreS3HydrationPolicy.json_src",
    "ParallelClusterInstancePolicy.json_src",
}


def _make_env(template_dir):
    env = Environment(
        loader=FileSystemLoader(template_dir),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    # Stub Ansible-only filters so they pass through without error.
    env.filters["bool"] = lambda v: str(v).lower() in ("true", "1", "yes")
    env.filters["upper"] = lambda v: str(v).upper()
    # Stub lookup() global — returns a placeholder string.
    env.globals["lookup"] = lambda *args, **kwargs: "<lookup-stub>"
    return env


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
    """Templates must render when enable_gpu=true with EFA-GDR (p4d.24xlarge)."""
    env = _make_env(tdir)
    rendered = env.get_template(fname).render(**cluster_params_gpu_gdr_enabled)
    assert isinstance(rendered, str)
    assert len(rendered) > 0, f"{fname} rendered empty (gpu_gdr_enabled variant)"
    if fname == "config.pcluster.j2":
        assert "GdrSupport: true" in rendered


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
}
_POLICY_FILES = [
    "ParallelClusterInstancePolicy-A.json_src",
    "ParallelClusterInstancePolicy-B.json_src",
    "ParallelClusterInstancePolicy-C.json_src",
    "ParallelClusterInstancePolicy-M.json_src",
]


def _load_policy(fname):
    path = os.path.join(REPO_ROOT, "templates", fname)
    with open(path) as f:
        raw = f.read()
    for placeholder, value in _PLACEHOLDER_SUB.items():
        raw = raw.replace(placeholder, value)
    return json.loads(raw)


@pytest.mark.parametrize("fname", _POLICY_FILES)
def test_iam_policy_valid_json(fname):
    """Each IAM policy template must parse as valid JSON after placeholder substitution."""
    data = _load_policy(fname)
    assert isinstance(data, dict), f"{fname}: top-level must be a JSON object"
    assert "Statement" in data, f"{fname}: missing Statement key"
    assert isinstance(data["Statement"], list), f"{fname}: Statement must be a list"
    assert len(data["Statement"]) > 0, f"{fname}: Statement list is empty"


@pytest.mark.parametrize("fname", _POLICY_FILES)
def test_iam_policy_under_size_limit(fname):
    """Each IAM managed policy must stay under the 6,144-byte IAM limit when minified."""
    data = _load_policy(fname)
    minified = json.dumps(data, separators=(",", ":"))
    size = len(minified.encode("utf-8"))
    assert (
        size <= _IAM_POLICY_LIMIT
    ), f"{fname}: minified size {size} bytes exceeds IAM limit of {_IAM_POLICY_LIMIT}"
