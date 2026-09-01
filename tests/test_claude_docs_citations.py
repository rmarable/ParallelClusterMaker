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
                        text,
                        re.M,
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
            name: sorted(files) for name, files in self._citations().items() if name not in defined
        }
        assert not dangling, (
            "the docs cite test names that no longer exist -- rename the citation "
            "or restore the test: "
            + "; ".join(f"{n} (cited in {', '.join(f)})" for n, f in sorted(dangling.items()))
        )

    def test_the_python_sources_cite_no_dangling_test_name_either(self):
        """A stale citation in a comment or docstring misleads exactly as much,
        and one of the three renames above was hiding in a comment in this file
        rather than in any .md.

        Docstrings, not just hash-comment lines.  This read only lines whose
        lstrip() began with a hash, and this codebase is docstring-heavy: two
        dangling names sat in docstrings for months, cited in the present tense
        -- TestMonitoringWrapperLoginNodeBootRace as a forward reference to a
        class deleted eleven lines below it, and the retired Ansible-side
        ssh-secret guard -- both invisible to a check whose whole purpose is
        stopping exactly that.  Docstrings are read with ast rather than by
        guessing at quote characters, which cannot tell a docstring from any
        other multi-line string.
        """
        defined = self._defined_names()
        dangling = {}
        py_files = []
        for root, dirs, files in os.walk(REPO_ROOT):
            dirs[:] = [d for d in dirs if d not in self._SKIP_DIRS]
            for fname in files:
                if fname.endswith(".py"):
                    py_files.append(os.path.relpath(os.path.join(root, fname), REPO_ROOT))
        import ast

        for relpath in py_files:
            # This module's own docstrings NAME dangling citations as worked
            # examples -- that is what they are for -- so sweeping itself
            # reports its own illustrations as defects.
            if os.path.basename(relpath) == os.path.basename(__file__):
                continue
            source = open(os.path.join(REPO_ROOT, relpath)).read()
            texts = [ln.lstrip() for ln in source.splitlines() if ln.lstrip().startswith("#")]
            try:
                tree = ast.parse(source)
            except SyntaxError:
                tree = None
            if tree is not None:
                doc_nodes = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
                for node in ast.walk(tree):
                    if isinstance(node, doc_nodes):
                        doc = ast.get_docstring(node)
                        if doc:
                            texts.append(doc)
            for text in texts:
                for name in self._NAME.findall(text):
                    if name in defined:
                        continue
                    # A filename is not a test-name citation.  `This was
                    # tests/test_playbook_secrets.py` names a renamed FILE and
                    # is correct prose; only a bare identifier is a citation.
                    if re.search(re.escape(name) + r"\.py\b", text):
                        continue
                    # A wrapped identifier, in either direction.  A docstring
                    # breaking `TestEveryNegativeSourceAssertion` before
                    # `ProvesItsHaystack` yields a truncated prefix; joining
                    # lines to repair that instead glues the name to the next
                    # word (`...ceiling` + `bites`). Accepting a match that is
                    # a prefix of a real name, or has one as its prefix, covers
                    # both without rewriting the text.
                    if any(d.startswith(name) or name.startswith(d) for d in defined):
                        continue
                    dangling.setdefault(name, set()).add(relpath)
        assert not dangling, (
            "a comment or docstring cites a test name that no longer exists: "
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
        required = {p for p in tracked if os.path.basename(p) in ("CLAUDE.md", "CLAUDE.local.md")}
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
        # Counted PER KIND.  A single total is a bare count: deleting
        # mcp_server/CLAUDE.local.md and adding newscope/CLAUDE.md nets to the
        # same number and passes, so the floor said nothing about whether the
        # constraint docs are still there.
        dense_present = os.path.exists(os.path.join(REPO_ROOT, "CLAUDE.local.md"))
        lean = {p for p in required if os.path.basename(p) == "CLAUDE.md"}
        dense = {p for p in required if os.path.basename(p) == "CLAUDE.local.md"}
        lean_floor, dense_floor = (4, 4) if dense_present else (4, 0)
        assert len(lean) >= lean_floor and len(dense) >= dense_floor, (
            f"derived {len(lean)} lean and {len(dense)} dense CLAUDE.md surfaces "
            f"(floors {lean_floor}/{dense_floor}); a scoped pair has gone missing "
            "from the scan"
        )
        # README.md always; CLAUDE-STATE.md only where it exists, which is
        # what the required set above already does -- a clone has 5, not 6.
        floor = lean_floor + dense_floor + 1 + (1 if dense_present else 0)
        assert len(required) >= floor, (
            f"derived only {len(required)} required doc surfaces ({sorted(required)}); "
            "the tracked-file scan is not seeing the constraint docs"
        )
        missing = sorted(required - tracked)
        assert not missing, f"not being swept: {missing}"

    def test_the_dangling_name_assertion_actually_discriminates(self, monkeypatch):
        """`assert not dangling` could be neutered to `assert True` and stay
        green: the existing vacuity guard checks the extractor, never the
        assertion.  Drive the real method with a citation that cannot resolve.
        """
        monkeypatch.setattr(
            type(self),
            "_citations",
            classmethod(lambda cls: {"TestThisNameWasNeverDefinedAnywhere": {"CLAUDE.md"}}),
        )
        with pytest.raises(AssertionError):
            self.test_every_cited_test_name_resolves()

    def test_the_coverage_assertion_actually_discriminates(self, monkeypatch):
        """Same shape for the surface sweep: drop a required doc from the
        scanned set and require the missing-surface assertion to raise."""
        real = type(self)._tracked_markdown.__func__(type(self))
        thinned = [p for p in real if os.path.basename(p) != "README.md"]
        monkeypatch.setattr(type(self), "_tracked_markdown", classmethod(lambda cls: thinned))
        with pytest.raises(AssertionError):
            self.test_every_documentation_surface_is_swept()
