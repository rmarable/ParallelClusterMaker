"""
Regression tests guarding secret-handling and feature-gating in the build and
teardown paths — that the SSH private key (.pem) is never swept into the
generic cluster_data_dir -> S3 upload, and that individual parameters the build
depends on (download checksums, SSM parameter paths, feature gates) cannot
silently change.

This was tests/test_playbook_secrets.py, and it read src/create_pcluster.yml
and src/delete_pcluster.yml. Both playbooks were deleted: nothing in this
toolkit executes an Ansible playbook any more, and every property they carried
is now a property of src/pcluster_core.py. The checks moved with it rather than
going away, because two of them cover code that had no other test at all.
"""

import ast
import inspect
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if os.path.join(REPO_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

import pcluster_core  # noqa: E402


def _source(name):
    return inspect.getsource(getattr(pcluster_core, name))


class TestThePrivateKeyIsNeverUploadedToS3:
    """The cluster data directory holds the .pem alongside the rendered
    scripts, and the whole directory is uploaded to the per-build bucket. One
    exclusion is what keeps the private key out of it -- there is no second
    guard, and an operator with s3:GetObject on that bucket is not the same set
    of people as those who can read the local file."""

    def test_the_exclusion_names_the_pem(self):
        assert pcluster_core._S3_UPLOAD_NEVER == ("*.pem",), (
            "the private key is no longer excluded from the cluster_data_dir "
            f"upload: {pcluster_core._S3_UPLOAD_NEVER}"
        )

    def test_the_uploader_defaults_to_it(self):
        """CLAUDE.md's rule is one shared exclusion, not one restated per call
        site: a caller that has to remember to pass it is a caller that can
        forget."""
        default = inspect.signature(
            pcluster_core.upload_directory_to_s3
        ).parameters["exclude"].default
        assert default is pcluster_core._S3_UPLOAD_NEVER, (
            "upload_directory_to_s3 no longer defaults to the shared exclusion, "
            f"so a call site can omit it and ship the key: {default!r}"
        )

    def test_no_call_site_overrides_it(self):
        """Passing a narrower exclude at a call site restores exactly the
        behavior the default exists to prevent, and reads as deliberate."""
        with open(os.path.join(REPO_ROOT, "src", "pcluster_core.py")) as fh:
            tree = ast.parse(fh.read())
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "id", "") != "upload_directory_to_s3":
                continue
            for kw in node.keywords:
                if kw.arg != "exclude":
                    continue
                if not (
                    isinstance(kw.value, ast.Name)
                    and kw.value.id == "_S3_UPLOAD_NEVER"
                ):
                    offenders.append(ast.unparse(kw))
        assert not offenders, (
            f"an upload_directory_to_s3 call passes its own exclude: {offenders}"
        )

    def test_the_exclusion_is_actually_applied(self):
        """Vacuity guard on the three above: a constant nothing reads is not a
        guard. Runs the real uploader over a directory holding a .pem."""

        class _S3:
            def __init__(self):
                self.keys = []

            def upload_file(self, Filename, Bucket, Key, **kw):  # noqa: N803
                self.keys.append(Key)

        import tempfile

        s3 = _S3()
        with tempfile.TemporaryDirectory() as td:
            open(os.path.join(td, "osiris.pem"), "w").write("PRIVATE")
            open(os.path.join(td, "vars_file.yml"), "w").write("k: v")
            pcluster_core.upload_directory_to_s3(
                s3, local_dir=td, s3_bucketname="b", prefix="p",
            )
        assert any(k.endswith("vars_file.yml") for k in s3.keys), s3.keys
        assert not any(k.endswith(".pem") for k in s3.keys), (
            f"the private key was uploaded to S3: {s3.keys}"
        )


class TestParametersTheBuildDependsOn:
    """Individual parameters, not gates -- a class the flag walk below cannot
    see, and where three separate mutations survived the whole suite."""

    def test_the_monitoring_tarball_download_is_checksum_verified(self):
        """The tarball is fetched from GitHub at build time and staged to S3, so
        the download is the one point where a moved, corrupted, or tampered
        artifact enters the cluster. Dropping the verification restores exactly
        the unverified-download behavior the S3 staging exists to avoid."""
        source = _source("_stage_monitoring_tarball")
        assert "_download_with_checksum(" in source, (
            "the monitoring tarball is no longer downloaded through the "
            "checksum-verifying helper"
        )
        assert "monitoring_version_checksum" in source, (
            "the download is not verified against monitoring_version_checksum"
        )
        assert "github.com" in source or "monitoring_url" in source, (
            "unexpected download source"
        )

    def test_the_monitoring_checksum_variable_is_threaded_end_to_end(self):
        """A checksum that references an undefined variable is worse than none —
        vars_file.j2 renders under StrictUndefined, so the build dies at
        template time. Both halves of the pair must be present."""
        with open(os.path.join(REPO_ROOT, "templates", "vars_file.j2")) as fh:
            vars_template = fh.read()
        with open(os.path.join(REPO_ROOT, "make_pcluster.py")) as fh:
            maker = fh.read()
        assert "monitoring_version_checksum" in vars_template
        assert "monitoring_version_checksum" in maker

    def test_the_grafana_ssm_parameter_path_matches_everywhere_it_is_used(self):
        """Teardown deletes the Grafana admin password by literal path. A typo
        there leaks the parameter on every teardown, and the gate assertion
        below still passes because the gate itself is untouched. Cross-check
        every place the path is written, read or printed."""
        suffix = "/grafana/admin-password"
        for relpath in (
            os.path.join("src", "pcluster_core.py"),
            os.path.join("templates", "grafana_tunnel.j2"),
        ):
            with open(os.path.join(REPO_ROOT, relpath)) as fh:
                assert suffix in fh.read(), (
                    f"{relpath} no longer references {suffix} — the teardown "
                    f"deleter and the readers have diverged"
                )
        deleter = _source("_delete_grafana_ssm_param_step")
        assert suffix in deleter, (
            "the teardown step no longer names the Grafana parameter path"
        )


# The functions a create actually runs through. Adding a stage here is
# what keeps the flag sweeps honest after an extraction.
_BUILD_PATH = (
    "core_create_cluster",
    "_provision_pre_launch_resources",
    "_render_build_templates",
)


class TestEveryOptionalFeatureStaysBehindItsFlag:
    """enable_hpc_benchmarks=false must mean no benchmark work at all: no
    staging, no S3 upload, no remote mkdir. enable_monitoring=false is the
    mirror — a monitoring step that runs anyway fails on a cluster that has no
    monitoring stack. Dropping the gate off a single task survived the suite
    once, and one task (the head node mkdir) had in fact shipped without it.

    Asserted on the AST rather than by proximity in the text: a call beside an
    `if` reads exactly like a call inside it.
    """

    _GATED = {
        "enable_hpc_benchmarks": ("benchmark", "performance", "hpc_results"),
        "enable_monitoring": ("monitoring", "grafana"),
    }

    @staticmethod
    def _calls(node):
        return {
            getattr(n.func, "id", None) or getattr(n.func, "attr", "")
            for n in ast.walk(node)
            if isinstance(n, ast.Call)
        }

    @staticmethod
    def _flags(test_node):
        return {n.id for n in ast.walk(test_node) if isinstance(n, ast.Name)}

    @staticmethod
    def _self_gated(name, flag):
        """Some steps carry their own `if not <flag>: return` instead of being
        called under an `if`. That is the same gate one level down, and the
        caller reads more clearly for it -- but only when the callee really
        does refuse."""
        fn = getattr(pcluster_core, name, None)
        if fn is None or not callable(fn):
            return False
        try:
            source = inspect.getsource(fn)
        except (OSError, TypeError):
            return False
        return f"if not {flag}" in source

    def _check(self, func_name, flag, tokens):
        """`func_name` may name several functions -- the build path is no
        longer one.

        core_create_cluster was 1,856 lines; the stages that actually make
        the gated calls now live in _provision_pre_launch_resources and
        _render_build_templates. Sweeping only the entry point found nothing
        and tripped this test's own vacuity guard, which is the guard doing
        its job: a rule that spans functions has to be checked across them.
        """
        names = (func_name,) if isinstance(func_name, str) else tuple(func_name)
        funcs = [ast.parse(_source(n).lstrip()).body[0] for n in names]
        gated, all_calls = set(), set()
        for func in funcs:
            all_calls |= self._calls(func)
            for node in ast.walk(func):
                if isinstance(node, ast.If) and flag in self._flags(node.test):
                    gated |= self._calls(node)
        ungated = {
            name
            for name in all_calls
            if name
            and any(tok in name.lower() for tok in tokens)
            and name not in gated
            and not self._self_gated(name, flag)
        }
        assert not ungated, (
            f"{func_name} calls {sorted(ungated)} without an enclosing "
            f"`if {flag}` — that work runs on a cluster that asked for none of it"
        )
        # Vacuity guard: if nothing matched, the token list has gone stale and
        # this proves nothing. A self-gated call counts -- it is the same gate
        # one level down, which is why it is accepted above.
        matched = {
            n for n in all_calls
            if n and any(tok in n.lower() for tok in tokens)
        }
        assert matched & (gated | {n for n in matched if self._self_gated(n, flag)}), (
            f"{func_name}: no {flag}-gated call found; the token list is stale"
        )

    def test_the_build_gates_every_benchmark_call(self):
        self._check(_BUILD_PATH, "enable_hpc_benchmarks",
                    self._GATED["enable_hpc_benchmarks"])

    def test_the_build_gates_every_monitoring_call(self):
        self._check(_BUILD_PATH, "enable_monitoring",
                    self._GATED["enable_monitoring"])

    def test_the_teardown_gates_every_benchmark_call(self):
        self._check("core_delete_cluster", "enable_hpc_benchmarks",
                    self._GATED["enable_hpc_benchmarks"])
