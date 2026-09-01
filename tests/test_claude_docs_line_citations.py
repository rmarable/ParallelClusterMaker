"""
Doc-hygiene: every `file:line` citation in the normative CLAUDE.md family
still points at the code it claims to.

Split out of tests/test_templates.py for modularity -- one file per doc-hygiene
concern rather than one combined file.
"""

import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestEveryLineNumberTheNormativeDocsCiteStillPointsAtItsSubject:
    """The constraint docs cite `<file>:<line>` to anchor a claim on real code,
    and a line number is the most perishable thing in a comment: every edit above
    it silently invalidates it, and nothing executes prose.  Two had already
    drifted -- `hpc-benchmark.sh:898,902,944,948` for the OSU pt2pt `-n 2` lines,
    which had moved 17 lines down to 915/919/961/965 and was wrong in *two*
    files, and `src/pcluster_core.py:527` for the `attach_role_policy` call, which
    had moved to 734 -- and in each case the cited line landed on something
    unrelated (`_info "Running ..."`, a bare `)`), so the reader following the
    citation is sent to code that does not support the claim.

    Only the *normative* docs are swept.  CLAUDE-STATE.md and docs/sessions.md
    are dated logs of what was true at each session and their line numbers are
    part of that record, so freezing them would fight the files' purpose --
    docs/sessions.md carries 11 `src/*.py:NNN` citations that were accurate when
    written and are not maintained.  That is a decision, not an oversight: a
    narrative that said `pcluster_core.py:734` in June is describing June; the two OSU citations in it were
    corrected by hand because they duplicate a live constraint.

    The manifest is a substring the cited line must contain, not the line's exact
    text: reformatting a line should not fail this, while the line ceasing to be
    the subject of the claim must.  Citations of *upstream* PCluster and
    monitoring sources (`cluster_stack.py:293`, `installer/install.sh:25-27`) are
    unresolvable here by construction -- those files are not in this repo -- and
    are the reason the sweep is a manifest rather than a blanket
    every-citation-must-resolve rule."""

    # (relpath, line) -> substring that line must contain.
    _EXPECTED = {
        ("hpc-benchmark/hpc-benchmark.sh", 1126): '-n 2 "$osu_pt2pt/osu_latency"',
        ("hpc-benchmark/hpc-benchmark.sh", 1130): '-n 2 "$osu_pt2pt/osu_bw"',
        # The d2d pair's -n 2 sits on the launcher line, one above the binary,
        # since the LD_LIBRARY_PATH prefix split the command across three lines.
        # The identical text on both is not a uniqueness problem: neither
        # neighbor of either line contains it, so a one-line shift still fails.
        ("hpc-benchmark/hpc-benchmark.sh", 1208): "-n 2 -x LD_LIBRARY_PATH",
        ("hpc-benchmark/hpc-benchmark.sh", 1214): "-n 2 -x LD_LIBRARY_PATH",
        # The line whose exported LD_LIBRARY_PATH outranks the CUDA tree's
        # RUNPATH; the whole d2d prefix exists because of it.
        ("hpc-benchmark/job_hpc-benchmark.sh.j2", 16): "module load openmpi",
        ("src/pcluster_core.py", 2584): "iam.attach_role_policy(",
    }

    # The dense files, not the lean committed ones -- the lean CLAUDE.md family
    # deliberately carries no file:line citations, so pointing this at them
    # would make the whole class vacuous.
    # DERIVED, not listed.  It was a hardcoded triple and `mcp_server/`'s dense
    # file -- created 2026-09-01 -- was simply not swept; a `file:line` citation
    # there could rot with the suite green.  The sibling class's docstring
    # already says why a hardcoded list is the wrong shape here, and this is the
    # same defect it describes.  Every dense file at any depth, so a new scoped
    # directory is covered the day it appears.
    @classmethod
    def _require_dense_docs(cls):
        """The dense files are gitignored local-only.  This module is tracked so
        the manifest itself stays reviewable; the sweeps over those files are
        skipped, not failed, when they are absent."""
        if not cls._normative_docs():
            pytest.skip("no CLAUDE.local.md present (fresh clone); nothing to sweep")

    @classmethod
    def _normative_docs(cls):
        found = []
        for root, dirs, files in os.walk(REPO_ROOT):
            dirs[:] = [d for d in dirs if d not in cls._SKIP_DIRS]
            for name in files:
                if name == "CLAUDE.local.md":
                    found.append(os.path.relpath(os.path.join(root, name), REPO_ROOT))
        return tuple(sorted(found))

    _SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules"}

    _CITE = re.compile(
        r"`([A-Za-z0-9_./-]+\.(?:sh|py|yml|j2|json_src))"
        r":(\d+(?:\s*,\s*\d+)*)`"
    )

    @classmethod
    def _citations(cls):
        """(relpath, line, citing doc) for every line citation whose file is
        present in this repo.  Basename-resolved when unambiguous, because the
        docs cite `hpc-benchmark.sh:915` without its directory.

        Walks the filesystem rather than `git ls-files`: this repo may have no
        `.git` at all yet, and the resolution set must include gitignored
        files (e.g. another `CLAUDE.local.md`) exactly as readily as tracked
        ones."""
        tracked = []
        for root, dirs, files in os.walk(REPO_ROOT):
            dirs[:] = [d for d in dirs if d not in cls._SKIP_DIRS]
            for fname in files:
                tracked.append(os.path.relpath(os.path.join(root, fname), REPO_ROOT))
        by_base = {}
        for path in tracked:
            by_base.setdefault(os.path.basename(path), []).append(path)

        out = []
        for doc in cls._normative_docs():
            text = open(os.path.join(REPO_ROOT, doc)).read()
            for match in cls._CITE.finditer(text):
                cited, numbers = match.group(1), match.group(2)
                if cited in tracked:
                    resolved = cited
                elif len(by_base.get(os.path.basename(cited), [])) == 1:
                    resolved = by_base[os.path.basename(cited)][0]
                else:
                    continue  # upstream source, not in this repo
                for number in numbers.split(","):
                    out.append((resolved, int(number.strip()), doc))
        return out

    def test_the_manifest_pins_one_line_per_citation(self):
        """A duplicate (relpath, line) key is silently dropped at dict
        construction, so the manifest shrinks and still reads as full coverage.
        Two of these were introduced at once while renumbering after an edit --
        the substrings differ, which is what makes the collision invisible."""
        source = open(__file__).read()
        body = source[source.index("_EXPECTED = {") :]
        body = body[: body.index("\n    }\n") + 1]
        keys = re.findall(r'\(\s*"([^"]+)"\s*,\s*(\d+)\s*\)\s*:', body)
        assert len(keys) >= len(self._EXPECTED), "the key extractor is not reading _EXPECTED"
        duplicates = sorted(k for k in set(keys) if keys.count(k) > 1)
        assert not duplicates, (
            f"_EXPECTED lists these (file, line) keys more than once, so only the "
            f"last substring for each is ever checked: {duplicates}"
        )
        assert len(keys) == len(self._EXPECTED), (
            f"{len(keys)} keys in the source but {len(self._EXPECTED)} in the dict"
        )

    def test_every_cited_line_holds_what_the_manifest_says(self):
        for (relpath, line), expected in sorted(self._EXPECTED.items()):
            lines = open(os.path.join(REPO_ROOT, relpath)).read().splitlines()
            assert line <= len(lines), (
                f"{relpath}:{line} is past the end of the file ({len(lines)} lines)"
            )
            assert expected in lines[line - 1], (
                f"{relpath}:{line} is cited as {expected!r} but holds "
                f"{lines[line - 1].strip()!r} -- the code moved and the citation "
                f"did not"
            )

    def test_every_checkable_citation_is_in_the_manifest(self):
        """A citation added to a normative doc must be pinned, or the manifest
        becomes a snapshot of whatever someone remembered."""
        self._require_dense_docs()
        unpinned = sorted(
            {
                (relpath, line, doc)
                for relpath, line, doc in self._citations()
                if (relpath, line) not in self._EXPECTED
            }
        )
        assert not unpinned, (
            "these line citations point at files in this repo but are not in "
            "_EXPECTED, so nothing checks them: "
            + "; ".join(f"{p}:{n} (in {d})" for p, n, d in unpinned)
        )

    def test_the_manifest_names_no_citation_the_docs_dropped(self):
        """The other direction: a manifest entry for a citation nobody makes any
        more is dead weight that reads as coverage."""
        self._require_dense_docs()
        cited = {(relpath, line) for relpath, line, _ in self._citations()}
        stale = sorted(set(self._EXPECTED) - cited)
        assert not stale, f"_EXPECTED pins citations that no normative doc makes: {stale}"

    def test_the_sweep_can_see_a_drifted_citation(self):
        """Vacuity guard.  A regex that matches nothing, or a resolver that drops
        every path, makes all three tests above pass on any documentation."""
        self._require_dense_docs()
        found = self._citations()
        assert len(found) >= 6, (
            f"only {len(found)} in-repo line citations found across "
            f"{self._normative_docs()}; the extractor is not reading what it thinks"
        )
        # The exact drift that shipped: 898 was cited for the osu_latency line,
        # which has since moved twice (915, now 957). Assert a superseded number
        # really does miss -- any of them will do, and 898 is the original.
        driver = (
            open(os.path.join(REPO_ROOT, "hpc-benchmark", "hpc-benchmark.sh")).read().splitlines()
        )
        assert '-n 2 "$osu_pt2pt/osu_latency"' not in driver[898 - 1], (
            "line 898 still holds the osu_latency launch, so the drift this class "
            "exists for is not reproducible and the manifest proves nothing"
        )

    def test_the_manifest_check_actually_discriminates(self, monkeypatch):
        """The comparison itself is the thing that can rot.  Weakening
        `expected in lines[line - 1]` to anything trivially true -- an `or True`,
        an `is not None` -- leaves the check green while checking nothing, and
        that mutation survived two batteries: the first version of this test
        re-implemented the comparison instead of running it, so neutering the real
        one changed nothing here.

        So this drives the actual test method with a manifest shifted by one line
        and requires it to raise.  Off-by-one is not an arbitrary choice of wrong
        answer -- a line inserted or removed above the citation is exactly how
        these drift, and a check that tolerates +/-1 tolerates the whole failure
        mode.  It also proves each pinned substring is unique to its line, since
        a substring the neighboring line shares cannot fail when shifted."""
        for (relpath, line), expected in sorted(self._EXPECTED.items()):
            total = len(open(os.path.join(REPO_ROOT, relpath)).read().splitlines())
            shifted = [n for n in (line - 1, line + 1) if 1 <= n <= total]
            assert shifted, f"{relpath}:{line} has no neighboring line to shift to"
            for wrong in shifted:
                monkeypatch.setattr(type(self), "_EXPECTED", {(relpath, wrong): expected})
                with pytest.raises(AssertionError):
                    self.test_every_cited_line_holds_what_the_manifest_says()
