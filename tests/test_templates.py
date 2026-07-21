"""
Template render tests.

Renders every Jinja2 template in templates/ and performance/jinja2/ using
StrictUndefined.  Any variable referenced in a template that is missing from
the fixture raises UndefinedError and fails the test immediately.

Filters used only by Ansible at playbook runtime (bool, upper, lookup) are
stubbed out so plain Python Jinja2 can render them without crashing.
"""

import os
import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined, Undefined

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TEMPLATE_DIRS = [
    os.path.join(REPO_ROOT, "templates"),
    os.path.join(REPO_ROOT, "performance", "jinja2"),
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
