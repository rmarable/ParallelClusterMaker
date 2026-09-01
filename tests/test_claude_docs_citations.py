"""
Doc-hygiene: every test/class name the CLAUDE.md family cites still exists.

Split out of tests/test_templates.py for modularity -- one file per doc-hygiene
concern rather than one combined file.
"""

import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestEveryTestNameTheDocsCiteStillExists:
    """The Markdown in this repo argues for its own constraints by naming the test
    that enforces each one, which is what makes a constraint checkable rather than
    folklore.  A citation that no longer resolves is worse than no citation: it
    reads as proof the property is pinned, and the reader has no reason to look.

    Renaming a test is the whole failure mode, and it had already happened three
    times unnoticed -- `test_managed_policy_suffixes_are_consistent` (now
    `test_every_policy_template_is_created_and_deleted`),
    `test_rhel_excludes_the_kernel_from_every_dnf_update` (now
    `test_dnf_arms_exclude_the_kernel_from_every_update`), and
    `test_the_harness_actually_fails_apt_get` (now
    `test_the_harness_actually_fails_the_package_manager`) -- across CLAUDE.md,
    CLAUDE-STATE.md and a comment in this file.  Nothing failed, because a name in
    prose is not executed by anything.

    Module basenames (`tests/test_templates.py`, cited constantly) are legitimate
    citations too, so they are collected alongside the def/class names rather than
    being excluded by pattern -- excluding them by shape would also excuse a
    genuinely dead one."""

    _NAME = re.compile(r"\b(test_[a-z0-9_]+|Test[A-Z][A-Za-z0-9]*)\b")

    # A filesystem walk, not `git ls-files`: CLAUDE.local.md (and its nested
    # siblings) are gitignored by design -- the citations that matter most live
    # there, and a git-tracked-only sweep would silently stop checking them the
    # moment they were split out of the committed CLAUDE.md.
    _SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules"}

    @classmethod
    def _tracked_markdown(cls):
        out = []
        for root, dirs, files in os.walk(REPO_ROOT):
            dirs[:] = [d for d in dirs if d not in cls._SKIP_DIRS]
            for fname in files:
                if fname.endswith(".md") or fname.endswith(".md.j2"):
                    out.append(os.path.relpath(os.path.join(root, fname), REPO_ROOT))
        assert out, "no Markdown found on disk; the sweep would pass vacuously"
        return out

    @classmethod
    def _defined_names(cls):
        """Every test callable, class, and module basename under tests/."""
        names = set()
        for root, dirs, files in os.walk(os.path.join(REPO_ROOT, "tests")):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                names.add(fname[:-3])
                text = open(os.path.join(root, fname)).read()
                # `async def` must match too. Without the optional
                # `async`, every asynchronous test in the suite is
                # invisible to this sweep -- so a doc citing one reads as
                # dangling, and (worse) a genuinely deleted async test
                # would not be caught either. Found when the first async
                # tests landed (the FastMCP client suite): a correct
                # citation of test_fleet_tools_default_to_not_waiting
                # failed here while the test existed and passed.
                names.update(
                    re.findall(
                        r"^\s*(?:async\s+)?(?:def|class)\s+((?:test_|Test)[A-Za-z0-9_]+)",
                        text, re.M,
                    )
                )
        return names

    @classmethod
    def _citations(cls):
        """name -> set of files citing it, over every tracked Markdown file."""
        cited = {}
        for relpath in cls._tracked_markdown():
            text = open(os.path.join(REPO_ROOT, relpath)).read()
            for name in cls._NAME.findall(text):
                cited.setdefault(name, set()).add(relpath)
        return cited

    def test_every_cited_test_name_resolves(self):
        defined = self._defined_names()
        dangling = {
            name: sorted(files)
            for name, files in self._citations().items()
            if name not in defined
        }
        assert not dangling, (
            "the docs cite test names that no longer exist -- rename the citation "
            "or restore the test: "
            + "; ".join(f"{n} (cited in {', '.join(f)})" for n, f in sorted(dangling.items()))
        )

    def test_the_python_sources_cite_no_dangling_test_name_either(self):
        """A stale citation in a comment misleads exactly as much, and one of the
        three renames above was hiding in a comment in this file rather than in
        any .md."""
        defined = self._defined_names()
        dangling = {}
        py_files = []
        for root, dirs, files in os.walk(REPO_ROOT):
            dirs[:] = [d for d in dirs if d not in self._SKIP_DIRS]
            for fname in files:
                if fname.endswith(".py"):
                    py_files.append(os.path.relpath(os.path.join(root, fname), REPO_ROOT))
        for relpath in py_files:
            for line in open(os.path.join(REPO_ROOT, relpath)):
                stripped = line.lstrip()
                if not stripped.startswith("#"):
                    continue
                for name in self._NAME.findall(stripped):
                    if name not in defined:
                        dangling.setdefault(name, set()).add(relpath)
        assert not dangling, (
            "a comment cites a test name that no longer exists: "
            + "; ".join(f"{n} ({', '.join(sorted(f))})" for n, f in sorted(dangling.items()))
        )

    def test_the_sweep_can_see_a_dangling_citation(self, tmp_path, monkeypatch):
        """Vacuity guard.  If the citation extractor matched nothing -- a broken
        regex, or a git invocation returning no files -- both tests above would
        pass on any documentation at all."""
        cited = self._citations()
        floor = 50 if os.path.exists(os.path.join(REPO_ROOT, "CLAUDE.local.md")) else 5
        assert len(cited) > floor, (
            f"the extractor found only {len(cited)} citations (floor {floor}) across the repo's "
            "Markdown; it is not reading what it thinks it is"
        )
        defined = self._defined_names()
        assert "test_the_sweep_can_see_a_dangling_citation" in defined, (
            "the definition scan cannot even find this test"
        )
        # A name of the right shape that nothing defines must be reported.
        invented = "test_this_name_is_not_defined_anywhere_in_the_suite"
        assert invented not in defined
        assert self._NAME.findall(f"see `{invented}` for the guard") == [invented]

    def test_every_documentation_surface_is_swept(self):
        """The sweep is driven by a filesystem walk, so a newly added Markdown
        file is covered without anyone remembering to list it -- and the files
        that carry the standing constraints must actually be in that set.

        The required set is *derived* rather than listed: every tracked file named
        CLAUDE.md at any depth, plus the two top-level docs.  A hardcoded list can
        be emptied to nothing and this test still passes, which is exactly what
        happened on the first mutation battery -- a five-name list cut down to one
        was green.  Deriving it means the assertion is about coverage rather than
        about someone's memory."""
        tracked = set(self._tracked_markdown())
        required = {
            p for p in tracked
            if os.path.basename(p) in ("CLAUDE.md", "CLAUDE.local.md")
        }
        # CLAUDE-STATE.md is gitignored local-only; require it only where it
        # exists, so this module can be tracked and still pass a fresh clone.
        required |= {"README.md"}
        if os.path.exists(os.path.join(REPO_ROOT, "CLAUDE-STATE.md")):
            required |= {"CLAUDE-STATE.md"}
        # The constraint docs are the point of the sweep; if the derivation ever
        # finds fewer than the four lean + four dense CLAUDE.md files that exist
        # today plus the two top-level docs, it is not reading the repo.  Was 8
        # and stayed 8 when `mcp_server/` added two surfaces on 2026-09-01, so
        # both could be deleted outright with this green -- proven by mutation.
        # The floor applies only with the local-only dense files present; a
        # fresh clone legitimately has fewer surfaces.
        floor = 10 if os.path.exists(os.path.join(REPO_ROOT, "CLAUDE.local.md")) else 5
        assert len(required) >= floor, (
            f"derived only {len(required)} required doc surfaces ({sorted(required)}); "
            "the tracked-file scan is not seeing the constraint docs"
        )
        missing = sorted(required - tracked)
        assert not missing, f"not being swept: {missing}"


