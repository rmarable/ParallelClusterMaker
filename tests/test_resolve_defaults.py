"""
Unit tests for the three-tier defaults resolution and --use_defaults file
loading logic extracted into src/pcluster_core.py.

Covers:
  - _resolve()      — CLI > file_defaults > hardcoded precedence, cast support
  - _resolve_bool() — string-to-bool normalization across all tiers
  - _load_defaults_file() — missing file, toolkit-copy warning, valid YAML
"""

import os
import re
import sys
import types

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
)

from pcluster_core import (
    _resolve as resolve,
    _resolve_bool as resolve_bool,
    _load_defaults_file,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _args(**kwargs):
    """Minimal argparse.Namespace stand-in."""
    return types.SimpleNamespace(**kwargs)


HARDCODED = {
    "base_os": "ubuntu2404",
    "cluster_type": "spot",
    "max_cpu_queue_size": 10,
    "debug_mode": "false",
    "enable_efs": "false",
}


# ---------------------------------------------------------------------------
# resolve() — precedence
# ---------------------------------------------------------------------------


class TestResolve:
    def test_cli_wins_over_file_and_hardcoded(self):
        args = _args(base_os="ubuntu2404arm")
        file_d = {"base_os": "ubuntu2204"}
        assert resolve("base_os", args, file_d, HARDCODED) == "ubuntu2404arm"

    def test_file_wins_over_hardcoded_when_cli_is_none(self):
        args = _args(base_os=None)
        file_d = {"base_os": "ubuntu2204"}
        assert resolve("base_os", args, file_d, HARDCODED) == "ubuntu2204"

    def test_hardcoded_used_when_cli_and_file_absent(self):
        args = _args(base_os=None)
        assert resolve("base_os", args, {}, HARDCODED) == "ubuntu2404"

    def test_returns_none_when_absent_everywhere(self):
        args = _args(unknown=None)
        assert resolve("unknown", args, {}, {}) is None

    def test_cast_applied_to_file_default(self):
        args = _args(max_cpu_queue_size=None)
        file_d = {"max_cpu_queue_size": "20"}
        result = resolve("max_cpu_queue_size", args, file_d, HARDCODED, cast=int)
        assert result == 20
        assert isinstance(result, int)

    def test_cast_applied_to_hardcoded(self):
        args = _args(max_cpu_queue_size=None)
        result = resolve("max_cpu_queue_size", args, {}, HARDCODED, cast=int)
        assert result == 10
        assert isinstance(result, int)

    def test_cast_not_applied_to_cli_arg(self):
        # argparse already coerces CLI args — we must not double-cast
        args = _args(max_cpu_queue_size=5)  # already an int
        result = resolve("max_cpu_queue_size", args, {}, HARDCODED, cast=int)
        assert result == 5

    def test_cast_skipped_when_value_is_none_in_file(self):
        args = _args(max_cpu_queue_size=None)
        file_d = {"max_cpu_queue_size": None}
        result = resolve("max_cpu_queue_size", args, file_d, HARDCODED, cast=int)
        assert result is None

    def test_empty_file_defaults_falls_through_to_hardcoded(self):
        args = _args(cluster_type=None)
        assert resolve("cluster_type", args, {}, HARDCODED) == "spot"

    def test_file_default_overrides_hardcoded_even_for_falsy_value(self):
        # '0' is falsy-ish but must still win over the hardcoded '10'
        args = _args(max_cpu_queue_size=None)
        file_d = {"max_cpu_queue_size": 0}
        result = resolve("max_cpu_queue_size", args, file_d, HARDCODED)
        assert result == 0

    def test_cast_valueerror_raises_systemexit(self):
        args = _args(max_cpu_queue_size=None)
        with pytest.raises(SystemExit):
            resolve("max_cpu_queue_size", args, {"max_cpu_queue_size": "not-a-number"}, {}, cast=int)

    def test_cast_typeerror_raises_systemexit(self):
        args = _args(max_cpu_queue_size=None)
        with pytest.raises(SystemExit):
            resolve("max_cpu_queue_size", args, {"max_cpu_queue_size": ["a", "b"]}, {}, cast=int)


# ---------------------------------------------------------------------------
# resolve_bool() — string normalization
# ---------------------------------------------------------------------------


class TestResolveBool:
    def test_true_string_from_hardcoded(self):
        args = _args(debug_mode=None)
        hc = {"debug_mode": "true"}
        assert resolve_bool("debug_mode", args, {}, hc) is True

    def test_false_string_from_hardcoded(self):
        args = _args(debug_mode=None)
        assert resolve_bool("debug_mode", args, {}, HARDCODED) is False

    def test_cli_true_string(self):
        args = _args(debug_mode="true")
        assert resolve_bool("debug_mode", args, {}, HARDCODED) is True

    def test_cli_false_string(self):
        args = _args(debug_mode="false")
        assert resolve_bool("debug_mode", args, {}, HARDCODED) is False

    def test_file_default_true(self):
        args = _args(enable_efs=None)
        file_d = {"enable_efs": "true"}
        assert resolve_bool("enable_efs", args, file_d, HARDCODED) is True

    def test_file_default_false(self):
        args = _args(enable_efs=None)
        assert resolve_bool("enable_efs", args, {}, HARDCODED) is False

    def test_uppercase_true_normalised(self):
        args = _args(debug_mode=None)
        hc = {"debug_mode": "TRUE"}
        assert resolve_bool("debug_mode", args, {}, hc) is True

    def test_mixed_case_false_normalised(self):
        args = _args(debug_mode=None)
        hc = {"debug_mode": "False"}
        assert resolve_bool("debug_mode", args, {}, hc) is False

    def test_python_bool_true_in_yaml_file(self):
        # pyyaml renders YAML `true:` as Python True; must still work
        args = _args(debug_mode=None)
        file_d = {"debug_mode": True}
        assert resolve_bool("debug_mode", args, file_d, HARDCODED) is True

    def test_python_bool_false_in_yaml_file(self):
        args = _args(debug_mode=None)
        file_d = {"debug_mode": False}
        assert resolve_bool("debug_mode", args, file_d, HARDCODED) is False

    def test_absent_everywhere_raises_systemexit(self):
        args = _args(unknown_flag=None)
        with pytest.raises(SystemExit):
            resolve_bool("unknown_flag", args, {}, {})


# ---------------------------------------------------------------------------
# _load_defaults_file()
# ---------------------------------------------------------------------------


class TestLoadDefaultsFile:
    def test_loads_valid_yaml(self, tmp_path):
        f = tmp_path / "my-cluster.yml"
        f.write_text("base_os: ubuntu2404arm\nmax_cpu_queue_size: 20\n")
        toolkit = str(tmp_path / "pcluster_defaults.yml")
        result = _load_defaults_file(str(f), toolkit, "my-cluster")
        assert result == {"base_os": "ubuntu2404arm", "max_cpu_queue_size": 20}

    def test_missing_file_raises_systemexit(self, tmp_path):
        toolkit = str(tmp_path / "pcluster_defaults.yml")
        with pytest.raises(SystemExit):
            _load_defaults_file(
                str(tmp_path / "nonexistent.yml"), toolkit, "my-cluster"
            )

    def test_empty_yaml_returns_empty_dict(self, tmp_path):
        f = tmp_path / "empty.yml"
        f.write_text("")
        toolkit = str(tmp_path / "pcluster_defaults.yml")
        result = _load_defaults_file(str(f), toolkit, "my-cluster")
        assert result == {}

    def test_toolkit_copy_prints_warning(self, tmp_path, capsys):
        toolkit = tmp_path / "pcluster_defaults.yml"
        toolkit.write_text("base_os: ubuntu2404\n")
        _load_defaults_file(str(toolkit), str(toolkit), "my-cluster")
        assert "WARNING" in capsys.readouterr().out

    def test_own_copy_no_warning(self, tmp_path, capsys):
        f = tmp_path / "my-cluster.yml"
        f.write_text("base_os: ubuntu2404arm\n")
        toolkit = tmp_path / "pcluster_defaults.yml"
        toolkit.write_text("base_os: ubuntu2404\n")
        _load_defaults_file(str(f), str(toolkit), "my-cluster")
        assert "WARNING" not in capsys.readouterr().out

    def test_systemexit_message_includes_cluster_name(self, tmp_path):
        toolkit = str(tmp_path / "pcluster_defaults.yml")
        with pytest.raises(SystemExit) as exc_info:
            _load_defaults_file(str(tmp_path / "missing.yml"), toolkit, "my-cluster")
        assert "my-cluster" in str(exc_info.value)

    def test_invalid_yaml_raises_systemexit(self, tmp_path):
        bad = tmp_path / "bad.yml"
        bad.write_text("key: [unclosed\n")
        toolkit = str(tmp_path / "pcluster_defaults.yml")
        with pytest.raises(SystemExit) as exc_info:
            _load_defaults_file(str(bad), toolkit, "my-cluster")
        assert "not valid YAML" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Defaults-file key liveness
# ---------------------------------------------------------------------------

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_every_defaults_key_has_a_consumer():
    """A key in pcluster_defaults.yml that nothing reads is a silent trap: the
    user sets it, nothing happens, and no error is raised. delete_efs shipped
    that way. Every key must appear somewhere in the code or templates."""
    import yaml as _yaml

    with open(os.path.join(REPO_ROOT, "pcluster_defaults.yml")) as fh:
        keys = _yaml.safe_load(fh)

    searched = []
    for rel in ["make_pcluster.py", "src", "templates"]:
        path = os.path.join(REPO_ROOT, rel)
        if os.path.isfile(path):
            searched.append(open(path).read())
            continue
        for root, _dirs, files in os.walk(path):
            for f in files:
                if f.endswith((".py", ".j2", ".yml", ".json_src")):
                    with open(os.path.join(root, f)) as fh:
                        searched.append(fh.read())
    haystack = "\n".join(searched)

    dead = [k for k in keys if k not in haystack]
    assert not dead, f"defaults keys with no consumer: {dead}"


def test_requirements_declares_every_third_party_import():
    """ruamel.yaml is imported by src/pcluster_core.py (queue-config editing)
    but was once undeclared — a fresh `pip install -r requirements.txt`
    produced an ImportError at runtime."""
    import re as _re

    with open(os.path.join(REPO_ROOT, "requirements.txt")) as fh:
        declared = fh.read().lower()

    src_dir = os.path.join(REPO_ROOT, "src")
    third_party = {"yaml", "ruamel", "botocore", "boto3", "jinja2", "requests"}
    found = set()
    for f in os.listdir(src_dir):
        if not f.endswith(".py"):
            continue
        with open(os.path.join(src_dir, f)) as fh:
            text = fh.read()
        for m in _re.finditer(r"^\s*(?:import|from)\s+([a-zA-Z0-9_.]+)", text, _re.M):
            root = m.group(1).split(".")[0]
            if root in third_party:
                found.add(root)

    reverse = {"yaml": "pyyaml", "ruamel": "ruamel.yaml"}
    for mod in sorted(found):
        dist = reverse.get(mod, mod)
        assert dist in declared, f"{mod} imported in src/ but {dist} not in requirements.txt"


def test_requirements_declares_ansible_filter_dependencies():
    """Ansible's json_query filter is a thin wrapper around jmespath, which was
    undeclared — it only arrived transitively via botocore. A pin that drops it
    turns the cluster-failure message task into an unrelated filter error."""
    import re as _re

    with open(os.path.join(REPO_ROOT, "requirements.txt")) as fh:
        declared = fh.read().lower()

    filter_deps = {"json_query": "jmespath"}
    src_dir = os.path.join(REPO_ROOT, "src")
    for f in sorted(os.listdir(src_dir)):
        if not f.endswith((".yml", ".yaml")):
            continue
        with open(os.path.join(src_dir, f)) as fh:
            text = fh.read()
        for filt, dist in filter_deps.items():
            if _re.search(r"\|\s*" + filt + r"\b", text):
                assert dist in declared, (
                    f"src/{f} uses the {filt} filter but {dist} is not in requirements.txt"
                )


def test_argparse_help_defaults_match_hardcoded_defaults():
    """Every "(default = X)" in make_pcluster.py's help text is a promise about
    _HARDCODED_DEFAULTS. Six had drifted (headnode_root_volume_size said 250
    when the code used 100) and
    headnode_instance_type advertised a default it does not have — it is
    required and aborts with an error when unset."""
    import ast as _ast
    import re as _re

    src_path = os.path.join(REPO_ROOT, "make_pcluster.py")
    with open(src_path) as fh:
        src = fh.read()

    # The argparse help text is still in make_pcluster.py (above), but the
    # defaults it is checked against moved to pcluster_core so an MCP
    # wrapper could reach them -- they were a local inside main() before.
    core_path = os.path.join(REPO_ROOT, "src", "pcluster_core.py")
    with open(core_path) as fh:
        core_src = fh.read()

    hardcoded = None
    for node in _ast.walk(_ast.parse(core_src)):
        if (
            isinstance(node, _ast.Assign)
            and getattr(node.targets[0], "id", "") == "MAKE_CLUSTER_DEFAULTS"
        ):
            hardcoded = _ast.literal_eval(node.value)
    assert hardcoded, "MAKE_CLUSTER_DEFAULTS not found in src/pcluster_core.py"

    # Options that legitimately have no _HARDCODED_DEFAULTS entry, so their help
    # text is free-form. headnode_instance_type is required, not defaulted.
    exempt = {"headnode_instance_type", "use_defaults"}

    mismatches = []
    for block in _re.findall(r"add_argument\((.*?)\n    \)", src, _re.S):
        name_match = _re.search(r'"--([a-z0-9_]+)"', block)
        help_match = _re.search(r"default = ([^)\"\n]+)\)", block)
        if not name_match or not help_match:
            continue
        name = name_match.group(1)
        if name in exempt:
            continue
        claimed = help_match.group(1).strip()
        assert name in hardcoded, f"--{name} advertises a default but has no _HARDCODED_DEFAULTS entry"
        actual = str(hardcoded[name])
        # An empty-string default is described in prose ("unset", '""'), not literally.
        if actual == "":
            if "unset" not in claimed and '""' not in claimed:
                mismatches.append((name, actual, claimed))
        elif claimed.split()[0].rstrip(",") != actual:
            mismatches.append((name, actual, claimed))

    assert not mismatches, "help text contradicts _HARDCODED_DEFAULTS: " + "; ".join(
        f"--{n}: code={a!r} help={c!r}" for n, a, c in mismatches
    )


def test_required_options_do_not_advertise_a_default():
    """headnode_instance_type's help said "default = c8g.xlarge" but the code
    aborts when it is unset — the help promised a build that cannot happen."""
    import re as _re

    with open(os.path.join(REPO_ROOT, "make_pcluster.py")) as fh:
        src = fh.read()

    for block in _re.findall(r"add_argument\((.*?)\n    \)", src, _re.S):
        if '"--headnode_instance_type"' not in block:
            continue
        assert block, "test_required_options_do_not_advertise_a_default: nothing to assert absence against"
        assert "default = " not in block, (
            "headnode_instance_type is required (make_pcluster.py aborts when unset); "
            "its help must not advertise a default"
        )
        return
    raise AssertionError("--headnode_instance_type add_argument block not found")


def test_documented_instance_types_are_selectable():
    """README examples are copy-pasted verbatim. c5d.2xlarge and r4.xlarge were
    not in ec2_instances_full_list, so two documented commands aborted with a
    validation error before reaching AWS."""
    import glob as _glob
    import re as _re

    sys.path.insert(0, os.path.join(REPO_ROOT, "src"))
    from pcluster_aux_data import ec2_instances_full_list

    known = set(ec2_instances_full_list)
    pattern = _re.compile(
        r"--(compute_instance_type|gpu_instance_type|headnode_instance_type)=([A-Za-z0-9_.,\-]+)"
    )

    bad = []
    docs = [os.path.join(REPO_ROOT, "README.md")] + _glob.glob(
        os.path.join(REPO_ROOT, "pr", "*.md")
    )
    for path in docs:
        with open(path) as fh:
            text = fh.read()
        for match in pattern.finditer(text):
            for itype in match.group(2).split(","):
                itype = itype.strip()
                if itype and itype not in known:
                    line = text[: match.start()].count("\n") + 1
                    bad.append(f"{os.path.basename(path)}:{line} --{match.group(1)}={itype}")

    assert not bad, "documented instance types absent from ec2_instances_full_list: " + "; ".join(bad)


def test_documented_gpu_families_match_the_detector():
    """README enumerates the GPU families. `gr6` was listed but absent from
    _GPU_PREFIXES and from every instance table, so the docs advertised
    detection that does not exist.

    `pr/` (a press-release doc that also enumerated the families) was
    deliberately removed; if it comes back, add it to `docs` below and raise
    the floor back to 2."""
    import re as _re

    sys.path.insert(0, os.path.join(REPO_ROOT, "src"))
    import pcluster_aux_data as _aux

    actual = {p.rstrip(".") for p in _aux._GPU_PREFIXES}

    docs = [os.path.join(REPO_ROOT, "README.md")]
    checked = 0
    for path in docs:
        with open(path) as fh:
            text = fh.read()
        # Match the backticked family enumerations that both docs use.
        for run in _re.findall(r"\(`g4dn`[^)]*\)", text):
            checked += 1
            listed = set(_re.findall(r"`([a-z0-9]+)`", run))
            assert listed == actual, (
                f"{os.path.basename(path)} lists GPU families {sorted(listed)} "
                f"but _GPU_PREFIXES is {sorted(actual)}"
            )
    assert checked >= 1, f"expected a GPU family enumeration in README, found {checked}"


def test_defaults_files_document_the_real_gpu_families():
    """The "Valid GPU instance families" comment listed 7 of the 10 families in
    _GPU_PREFIXES — g4ad, g5g, and p3dn were omitted, so users had no way to know
    those were supported."""
    import glob as _glob
    import re as _re

    sys.path.insert(0, os.path.join(REPO_ROOT, "src"))
    import pcluster_aux_data as _aux

    actual = {p.rstrip(".") for p in _aux._GPU_PREFIXES}

    checked = 0
    for path in _glob.glob(os.path.join(REPO_ROOT, "*_defaults.yml")):
        with open(path) as fh:
            for line in fh:
                if "Valid GPU instance families" not in line:
                    continue
                checked += 1
                listed = {f.strip() for f in line.split(":", 1)[1].split(",") if f.strip()}
                assert listed == actual, (
                    f"{os.path.basename(path)} lists {sorted(listed)} "
                    f"but _GPU_PREFIXES is {sorted(actual)}"
                )
    assert checked, "no defaults file documents the GPU instance families"


def _makefile_recipes():
    """Map target -> list of recipe command lines.

    A `VAR := value` assignment also contains a colon, and reading it as a target
    made the caller demand `make SHELLCHECK_EXCLUDE` in CI. Make's own rule is that
    an assignment operator (`:=`, `::=`, `?=`, `+=`, `!=`, or a bare `=`) before any
    colon means assignment, not a rule.
    """
    assignment = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*(::?=|\?=|\+=|!=|=)")
    recipes = {}
    target = None
    with open(os.path.join(REPO_ROOT, "Makefile")) as fh:
        for line in fh:
            if line.startswith("\t"):
                if target:
                    recipes[target].append(line.strip())
            elif assignment.match(line):
                target = None
            elif ":" in line and not line.startswith(("#", ".PHONY")):
                target = line.split(":", 1)[0].strip()
                recipes[target] = []
            elif not line.strip():
                continue
    return recipes


def test_ci_invokes_make_targets_instead_of_duplicating_them():
    """CI used to re-spell `.venv/bin/python -m pytest tests/ -q` inline instead of
    calling `make test`. Adding a flag or path to the Makefile then silently left
    CI running the old command. Every Makefile target must be reached through
    `make`, and no target's recipe may be duplicated verbatim in the workflow."""
    with open(os.path.join(REPO_ROOT, ".github", "workflows", "test.yml")) as fh:
        workflow = fh.read()

    recipes = _makefile_recipes()
    assert set(recipes) >= {"test", "lint", "shellcheck"}, (
        f"Makefile targets changed; guard needs updating: {sorted(recipes)}"
    )

    for target, commands in recipes.items():
        assert f"make {target}" in workflow, (
            f"Makefile target '{target}' is never invoked as `make {target}` in CI"
        )
        for command in commands:
            assert command not in workflow, (
                f"CI duplicates the recipe for `make {target}` inline "
                f"({command!r}); call `make {target}` instead"
            )


def test_defaults_file_banners_name_their_own_file():
    """isis_defaults.yml and osiris_defaults.yml were copied from
    pcluster_defaults.yml and kept its banner, so the header identified the wrong
    file. An operator reading the top of the file cannot tell which cluster's
    defaults they are editing."""
    import glob as _glob

    checked = 0
    for path in _glob.glob(os.path.join(REPO_ROOT, "*_defaults.yml")):
        basename = os.path.basename(path)
        with open(path) as fh:
            head = [next(fh, "") for _ in range(6)]
        banner = [ln for ln in head if ln.strip().startswith("#") and ".yml" in ln]
        assert banner, f"{basename} has no filename in its header banner"
        checked += 1
        for line in banner:
            assert basename in line, (
                f"{basename} banner names a different file: {line.strip()!r}"
            )
    # One, not two: isis_defaults.yml and osiris_defaults.yml were purged and
    # gitignored (both held a real VPC name and subnet IDs), leaving
    # pcluster_defaults.yml as the only tracked defaults file. The glob still
    # reaches an operator's own untracked copies and holds them to the same banner
    # check, which is the case this test was written for.
    assert checked >= 1, f"expected at least 1 defaults file, found {checked}"


def test_gpu_subnet_fallback_comments_describe_the_real_behavior():
    """gpu_az falls back to compute_az before --az, and gpu_subnet_ids falls back
    to compute_subnet_ids (pcluster_core._validate_network). osiris_defaults.yml
    had copied compute's comments verbatim, documenting the wrong fallback."""
    import glob as _glob

    for path in _glob.glob(os.path.join(REPO_ROOT, "*_defaults.yml")):
        with open(path) as fh:
            for lineno, line in enumerate(fh, 1):
                if "#" not in line:
                    continue
                key, comment = line.split("#", 1)[0].strip(), line.split("#", 1)[1]
                where = f"{os.path.basename(path)}:{lineno}"
                if key.startswith("gpu_az:"):
                    assert "compute_az" in comment, (
                        f"{where} gpu_az comment must state the compute_az fallback: {comment.strip()!r}"
                    )
                elif key.startswith("gpu_subnet_ids:"):
                    assert "compute_subnet_ids" in comment, (
                        f"{where} gpu_subnet_ids comment must state the "
                        f"compute_subnet_ids fallback: {comment.strip()!r}"
                    )


def test_readme_tag_table_matches_the_config_template():
    """The README tagging table is the only place users learn what tags exist for
    cost allocation. ClusterOSType and ClusterScheduler were applied by
    config.pcluster.j2 but absent from the table."""
    import re as _re

    with open(os.path.join(REPO_ROOT, "templates", "config.pcluster.j2")) as fh:
        template = fh.read()

    tags_block = template.split("\nTags:\n", 1)[1].split("\n\n", 1)[0]
    emitted = set(_re.findall(r"^\s*-\s*Key:\s*(\S+)", tags_block, _re.M))
    assert emitted, "no tags parsed out of config.pcluster.j2"

    with open(os.path.join(REPO_ROOT, "README.md")) as fh:
        readme = fh.read()

    table = readme.split("## Tagging", 1)[1].split("\n---\n", 1)[0]
    documented = set(_re.findall(r"^\| `([A-Za-z_]+)` \|", table, _re.M))

    assert documented == emitted, (
        f"README tagging table drift — undocumented: {sorted(emitted - documented)}, "
        f"documented but not emitted: {sorted(documented - emitted)}"
    )


def test_loginnode_instance_type_help_documents_both_architectures():
    """loginnode_instance_type's hardcoded fallback is architecture-conditional
    (c8g.xlarge on Graviton, c5.xlarge on x86_64), so it cannot be expressed as
    a single "(default = X)" claim the way every other flag's help text is —
    this is the documented exception test_argparse_help_defaults_match_hardcoded_defaults
    needs; both branch values must appear in the help string instead."""
    import re as _re

    with open(os.path.join(REPO_ROOT, "make_pcluster.py")) as fh:
        src = fh.read()

    for block in _re.findall(r"add_argument\((.*?)\n    \)", src, _re.S):
        if '"--loginnode_instance_type"' not in block:
            continue
        assert "c8g.xlarge" in block and "c5.xlarge" in block, (
            "--loginnode_instance_type help text must document both the "
            "Graviton and x86_64 fallback values"
        )
        return
    raise AssertionError("--loginnode_instance_type add_argument block not found")


def test_default_loginnode_instance_type_covers_every_base_os():
    """_default_loginnode_instance_type must return an architecture-correct
    fallback for every supported base_os, not just the ones it was written
    against — mirrors the ARM_OSES | X86_OSES exhaustiveness discipline used
    elsewhere for architecture checks, so a 9th base_os added later can't
    silently fall through un-covered."""
    from pcluster_core import _default_loginnode_instance_type
    from pcluster_aux_data import ARM_OSES, X86_OSES

    for base_os in ARM_OSES:
        assert _default_loginnode_instance_type(base_os) == "c8g.xlarge", base_os
    for base_os in X86_OSES:
        assert _default_loginnode_instance_type(base_os) == "c5.xlarge", base_os

    assert set(ARM_OSES) | set(X86_OSES), "ARM_OSES/X86_OSES must not be empty"
