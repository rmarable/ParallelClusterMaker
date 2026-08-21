"""Workstream 5: deployment packaging for the Lambda topology.

The declarations in packaging.py are only worth something if they match
reality, so the tests that matter here check them *against the real import
graph and the real filesystem* rather than against each other:

  * the router's empty requirement list is verified by importing it in a
    subprocess and asserting no third-party package was loaded -- not by
    reading the list back;
  * every source path a tier declares must exist;
  * the excluded packages are the ones that would actually blow the
    250 MB unzipped limit, and no tier may require them.

`requirements.txt` is the development set: it pulls ansible (~408 MB of
collections, for playbooks nothing executes) plus the hpc-benchmark
plotting stack (~250 MB). Installed wholesale into a Lambda artifact those
alone exceed the limit, for code no tool calls.
"""

import json
import os
import subprocess
import sys
import zipfile

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from mcp_server.packaging import (  # noqa: E402
    EXCLUDED_FROM_LAMBDA,
    TIER_PACKAGES,
    ZIP_UNZIPPED_LIMIT_BYTES,
    build_source_archive,
    manifest,
    requirements_for,
    sources_for,
    validate_requirements,
)
from mcp_server.tiers import FUNCTION_NAMES  # noqa: E402


class TestTheTierSetMatchesTheTopology:
    def test_every_lambda_tier_has_a_package_spec(self):
        """A tier with a role and an ARN but no artifact is a function that
        cannot be deployed."""
        assert set(TIER_PACKAGES) == set(FUNCTION_NAMES)

    def test_each_handler_path_points_at_a_real_module(self):
        for tier, spec in TIER_PACKAGES.items():
            module, _, func = spec["handler"].rpartition(".")
            path = os.path.join(REPO_ROOT, module.replace(".", os.sep) + ".py")
            assert os.path.isfile(path), f"{tier}: no module at {path}"

    def test_each_handler_function_actually_exists(self):
        """A handler path that imports but has no such attribute fails at
        the first invocation, not at deploy time."""
        import importlib

        for tier, spec in TIER_PACKAGES.items():
            module, _, func = spec["handler"].rpartition(".")
            assert hasattr(importlib.import_module(module), func), tier

    def test_every_declared_source_path_exists(self):
        for tier in TIER_PACKAGES:
            for entry in sources_for(tier):
                assert os.path.exists(os.path.join(REPO_ROOT, entry)), f"{tier}: {entry}"

    def test_only_the_node_tier_is_a_container_image(self):
        """The split is Node.js, not size: create/update call
        assert_valid_node_js() and a zip cannot supply a Node runtime."""
        images = {t for t, s in TIER_PACKAGES.items() if s["kind"] == "image"}
        assert images == {"stack-mutation-node"}


class TestTheRouterPackageIsTiny:
    """The router's empty requirement list is the concrete payoff of
    keeping pcluster_core out of it, and it is worth verifying against the
    real import graph rather than reading the declaration back."""

    def test_importing_the_router_loads_no_third_party_package(self):
        script = (
            "import sys, os\n"
            f"sys.path.insert(0, {REPO_ROOT!r})\n"
            "base = set(sys.modules)\n"
            "import mcp_server.router\n"
            "sp = os.path.join(sys.prefix, 'lib')\n"
            "new = set(sys.modules) - base\n"
            "third = sorted({\n"
            "    m.split('.')[0] for m in new\n"
            "    if getattr(sys.modules.get(m), '__file__', None)\n"
            "    and 'site-packages' in (sys.modules[m].__file__ or '')\n"
            "})\n"
            "print(__import__('json').dumps(third))\n"
        )
        out = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=60
        )
        assert out.returncode == 0, out.stderr
        third_party = json.loads(out.stdout.strip().splitlines()[-1])
        assert third_party == [], (
            f"the router pulled in {third_party}; its near-zero IAM is only "
            f"meaningful if its package is correspondingly small"
        )

    def test_the_declared_requirements_are_empty(self):
        assert requirements_for("router") == []

    def test_its_sources_do_not_include_pcluster_core(self):
        assert not any("pcluster_core" in s for s in sources_for("router"))


class TestExcludedPackages:
    def test_no_tier_requires_an_excluded_package(self):
        for tier in TIER_PACKAGES:
            validate_requirements(tier)  # must not raise

    def test_requiring_ansible_is_rejected(self):
        """The specific hazard: installing requirements.txt wholesale.
        ansible alone is ~408 MB of collections for playbooks nothing
        executes."""
        original = TIER_PACKAGES["router"]["requirements"]
        TIER_PACKAGES["router"]["requirements"] = ["ansible"]
        try:
            with pytest.raises(ValueError, match="ansible"):
                validate_requirements("router")
        finally:
            TIER_PACKAGES["router"]["requirements"] = original

    def test_the_rejection_says_why_the_package_is_excluded(self):
        """A bare "not allowed" leaves the next person to rediscover the
        reason, and the reason differs per package."""
        original = TIER_PACKAGES["router"]["requirements"]
        TIER_PACKAGES["router"]["requirements"] = ["scipy"]
        try:
            with pytest.raises(ValueError, match="plotting"):
                validate_requirements("router")
        finally:
            TIER_PACKAGES["router"]["requirements"] = original

    def test_a_versioned_requirement_is_still_matched(self):
        """`ansible>=9,<10` must be caught as ansible, not treated as an
        unrelated name."""
        original = TIER_PACKAGES["router"]["requirements"]
        TIER_PACKAGES["router"]["requirements"] = ["ansible>=9"]
        try:
            with pytest.raises(ValueError):
                validate_requirements("router")
        finally:
            TIER_PACKAGES["router"]["requirements"] = original

    def test_the_dev_requirements_file_would_be_rejected(self):
        """Grounds the whole exclusion list: requirements.txt is the
        development set and must never be installed into an artifact."""
        with open(os.path.join(REPO_ROOT, "requirements.txt")) as fh:
            dev = [
                l.strip() for l in fh
                if l.strip() and not l.startswith("#")
            ]
        names = {r.split(">=")[0].split("==")[0].strip() for r in dev}
        assert names & set(EXCLUDED_FROM_LAMBDA), (
            "requirements.txt no longer contains anything excluded -- either "
            "the dev set changed or the exclusion list has gone stale"
        )


class TestBuildSourceArchive:
    def test_it_produces_a_readable_zip(self, tmp_path):
        dest = tmp_path / "router.zip"
        staged = build_source_archive("router", REPO_ROOT, str(dest))
        assert staged
        with zipfile.ZipFile(dest) as zf:
            assert zf.testzip() is None
            assert set(zf.namelist()) == set(staged)

    def test_the_router_archive_carries_only_its_three_modules(self):
        """Anything else in there means the source list drifted from the
        leanness claim."""
        assert sorted(sources_for("router")) == [
            "mcp_server/__init__.py", "mcp_server/router.py", "mcp_server/tiers.py",
        ]

    def test_pycache_is_never_staged(self, tmp_path):
        dest = tmp_path / "handler.zip"
        staged = build_source_archive("read-only", REPO_ROOT, str(dest))
        assert not [s for s in staged if "__pycache__" in s or s.endswith(".pyc")]

    def test_a_handler_archive_carries_pcluster_core(self, tmp_path):
        dest = tmp_path / "handler.zip"
        staged = build_source_archive("read-only", REPO_ROOT, str(dest))
        assert "src/pcluster_core.py" in staged

    def test_a_handler_archive_carries_the_iam_templates(self, tmp_path):
        """core_create_cluster renders policies from templates/ at runtime;
        an artifact without them fails at the first build."""
        dest = tmp_path / "handler.zip"
        staged = build_source_archive("stack-mutation", REPO_ROOT, str(dest))
        assert any(s.startswith("templates/") for s in staged)

    def test_building_validates_requirements_first(self, tmp_path):
        original = TIER_PACKAGES["router"]["requirements"]
        TIER_PACKAGES["router"]["requirements"] = ["ansible"]
        try:
            with pytest.raises(ValueError):
                build_source_archive("router", REPO_ROOT, str(tmp_path / "x.zip"))
        finally:
            TIER_PACKAGES["router"]["requirements"] = original


class TestManifest:
    def test_the_zip_tiers_get_the_zip_limit(self):
        assert manifest("router")["size_limit_bytes"] == ZIP_UNZIPPED_LIMIT_BYTES

    def test_the_image_tier_gets_the_larger_limit(self):
        assert manifest("stack-mutation-node")["size_limit_bytes"] > ZIP_UNZIPPED_LIMIT_BYTES

    def test_it_is_json_serializable(self):
        """It is meant to be handed to a deployment step, possibly another
        process."""
        for tier in TIER_PACKAGES:
            json.loads(json.dumps(manifest(tier)))


class TestTheDockerfile:
    _PATH = os.path.join(REPO_ROOT, "mcp_server", "Dockerfile.stack-mutation-node")

    def _text(self):
        with open(self._PATH) as fh:
            return fh.read()

    def test_it_exists_for_the_image_tier(self):
        assert os.path.isfile(self._PATH)

    def test_it_installs_node(self):
        """The entire reason this tier is an image."""
        assert "nodejs" in self._text()

    def test_it_verifies_node_at_build_time(self):
        """assert_valid_node_js() would otherwise surface a missing Node
        20 minutes into an operator's first create_cluster call."""
        assert "node --version" in self._text()

    def test_it_puts_src_on_the_python_path(self):
        """pcluster_core lives under src/ and is imported as a top-level
        module; without this the handler cannot import it."""
        assert "PYTHONPATH" in self._text() and "/src" in self._text()

    def test_its_cmd_matches_the_declared_handler(self):
        assert TIER_PACKAGES["stack-mutation-node"]["handler"] in self._text()


class TestTheGeneratedRequirementsFile:
    """requirements-lambda.txt is generated from TIER_PACKAGES rather than
    maintained by hand, so the Dockerfile's `pip install -r` and the tier
    spec cannot disagree. A drifted file produces an image missing a
    package the handler imports -- found at the first invocation, not at
    build time."""

    _PATH = os.path.join(REPO_ROOT, "requirements-lambda.txt")

    def test_it_exists(self):
        assert os.path.isfile(self._PATH), "the Dockerfile installs from it"

    def test_it_matches_what_the_tier_spec_would_generate(self):
        from mcp_server.packaging import render_requirements_file

        with open(self._PATH) as fh:
            assert fh.read() == render_requirements_file("stack-mutation-node")

    def test_it_excludes_the_development_only_packages(self):
        with open(self._PATH) as fh:
            body = [l.strip() for l in fh if l.strip() and not l.startswith("#")]
        names = {r.split(">=")[0].split("==")[0].strip() for r in body}
        assert not (names & set(EXCLUDED_FROM_LAMBDA))

    def test_generation_refuses_an_excluded_package(self):
        from mcp_server.packaging import render_requirements_file

        original = TIER_PACKAGES["router"]["requirements"]
        TIER_PACKAGES["router"]["requirements"] = ["pandas"]
        try:
            with pytest.raises(ValueError):
                render_requirements_file("router")
        finally:
            TIER_PACKAGES["router"]["requirements"] = original
