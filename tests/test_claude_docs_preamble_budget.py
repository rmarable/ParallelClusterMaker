"""
Doc-hygiene: the always-loaded preamble (CLAUDE.md + CLAUDE.local.md +
CLAUDE-STATE.md) stays under its byte budget, and the dated session archive
does not creep back in.

CLAUDE.md is the lean, public, committed file; CLAUDE.local.md is the dense
rationale/incident-history companion Claude Code auto-loads alongside it
(gitignored, local-only, per the CLAUDE.local.md convention documented at
https://code.claude.com/docs/en/memory.md). Both count toward the budget
because both load every session regardless of which one is tracked by git.

Split out of tests/test_templates.py for modularity -- one file per doc-hygiene
concern rather than one combined file.
"""

import io
import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestTheAlwaysLoadedPreambleStaysAffordable:
    """`CLAUDE.md` and `CLAUDE-STATE.md` are read in full at the start of every
    session, before any code, so their size is a tax on every task in the repo.
    Together they had reached ~73k tokens -- 37% of a 200k window -- because the
    growth mechanism was one appended section per session and the only brake was
    someone remembering.  Splitting the dated record out to `docs/sessions.md`
    brought it to ~45k; nothing stops it climbing back.

    The budget is in **bytes, not lines**, and that is the whole design.  These
    two files differ by 7.4x in bytes per line (86 vs 640: one is wrapped prose,
    the other is one dense paragraph per bullet), so a line cap is a byte cap
    whose size depends on wrap width -- reflowing `CLAUDE-STATE.md` at width 100
    changes nothing and adds 207 lines.  Worse, the cheapest way to satisfy a
    line cap is longer lines, so it would reward the least readable shape in the
    file (`## Test status` is already 927 bytes/line) and punish wrapped prose.
    Bytes are ungameable by formatting.

    The ceiling is **combined**, so the two files are priced against each other:
    growing one means shrinking the other, and there is no budget to be found by
    shuffling content between them.  A per-file cap would also put the pressure
    on the wrong file -- `CLAUDE.md` is 2.3x larger and is where the remaining
    reduction is.

    This is a ratchet, not a target.  After any real reduction, lower `_CEILING`
    to just above the new size in the same commit; the headroom exists to absorb
    one substantive constraint bullet, not a session log."""

    _PREAMBLE = ("CLAUDE.md", "CLAUDE.local.md", "CLAUDE-STATE.md")

    @classmethod
    def _require_full_preamble(cls):
        """Two of the three preamble files are gitignored local-only files, so a
        fresh clone (and CI) has only CLAUDE.md.  This module is tracked so the
        checks that DO work on tracked files run there; the byte budget cannot,
        and is skipped rather than failed."""
        absent = [f for f in cls._PREAMBLE
                  if not os.path.exists(os.path.join(REPO_ROOT, f))]
        if absent:
            pytest.skip("local-only preamble files not present: %s" % ", ".join(absent))

    _SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules"}

    @classmethod
    def _every_memory_file(cls):
        """Every file Claude Code loads as memory, at any depth -- the always-
        loaded preamble plus every scoped `<dir>/CLAUDE*.md`.  Derived, because
        the two guards that used a hardcoded list both silently stopped covering
        `mcp_server/` the day it was created."""
        found = set(cls._PREAMBLE)
        for root, dirs, files in os.walk(REPO_ROOT):
            dirs[:] = [d for d in dirs if d not in cls._SKIP_DIRS]
            for name in files:
                if name in ("CLAUDE.md", "CLAUDE.local.md"):
                    found.add(os.path.relpath(os.path.join(root, name), REPO_ROOT))
        return tuple(sorted(found))

    def test_every_scoped_memory_file_is_reachable_from_the_preamble(self):
        """A scoped doc nobody is told about is a doc nobody reads, and under
        the byte ceiling above, *deleting the pointer to it is a pure win* --
        the preamble shrinks and every guard stays green.  That was measured on
        2026-09-01: removing `CLAUDE.md`'s five-line `mcp_server/` pointer left
        17 doc tests passing and the budget reading better, with a 15KB
        constraint file orphaned.

        The requirement is only that the scoped file's directory is named
        somewhere in the always-loaded preamble.  Deliberately not a prose
        pattern -- an assertion about how the pointer is phrased would rot as
        the wording changes, and reachability is the property that matters."""
        preamble = "\n".join(
            io.open(os.path.join(REPO_ROOT, p), encoding="utf-8").read()
            for p in self._PREAMBLE
            if os.path.exists(os.path.join(REPO_ROOT, p))
        )
        scoped = [p for p in self._every_memory_file() if p not in self._PREAMBLE]
        assert scoped, "no scoped memory files found; the walk is broken"
        unreachable = [p for p in scoped if os.path.dirname(p) + "/" not in preamble]
        assert not unreachable, (
            "these scoped memory files are named nowhere in the always-loaded "
            f"preamble, so nothing directs a reader to them: {unreachable}.  "
            "Add a pointer in CLAUDE.md naming the directory."
        )

    # Headroom had drifted to 9 bytes against the old 150,000 ceiling --
    # several rounds of small, individually-justified additions each
    # squeezed in under a shrinking margin rather than triggering a real
    # reduction, exactly the "slow relapse" this file's own docstring warns
    # a byte cap cannot see on its own. Condensed the single largest bullet
    # in CLAUDE.local.md in place (the kernel-exclusion one: 12,949 bytes ->
    # ~4,300 bytes; rule + reasoning + test names kept, exact dates/instance
    # IDs/error codes moved to docs/sessions.md verbatim under "Trimmed
    # CLAUDE.local.md bullets (moved 2026-08-13)"), bringing the combined
    # size to 13,263 (CLAUDE.md) + 117,961 (CLAUDE.local.md) + 10,102
    # (CLAUDE-STATE.md) = 141,326 bytes. Ceiling lowered accordingly rather
    # than left at 150,000 to bank the new slack.
    #
    # 2026-08-24: went 1,672B *over* before anyone looked, and was brought
    # back to 143,410 (headroom 2,090) by condensing the shared-store
    # bullet in CLAUDE.local.md and archiving CLAUDE-STATE.md's completed
    # Workstream 5/6/7 detail to docs/sessions.md verbatim. _CEILING is
    # deliberately NOT lowered this time: the same commit adds the
    # finalize-teardown constraint, so the reduction left no slack to bank
    # -- lowering it further would put the working margin below its own
    # floor, which is the guard firing, not the guard being satisfied.
    #
    # 2026-08-25: raised to 150,000 at the operator's direction. This is a
    # deliberate loosening, not a ratchet step, and it is recorded as such:
    # the session that asked for it spent most of a day shaving bytes to
    # fit genuine new constraints (the event-loop fix, the two finalize
    # twins, the one-region guard), and every trim went straight back out
    # as new content. The cap exists to stop *unnoticed* growth, not to
    # price out constraints that earn their place. The ratchet rule still
    # holds from here: after any real reduction, lower this again.
    #
    # 2026-08-26: ratcheted 150,000 -> 146,000 after a real reduction of
    # 11,217 bytes (147,511 -> 136,294). Three mechanisms, no rule weakened:
    # per-arm node-bootstrap evidence (AL2023 packages, the compute-node
    # package index, the kernel excludes, liblua, Lmod's `bc`, the NVMe
    # block, the /etc/profile guard, the monitoring wrapper) moved verbatim
    # to templates/CLAUDE.local.md, which loads whenever the templates it
    # describes are touched, with the rule and its test names kept in the
    # always-loaded root file; and three IAM bullets that were already
    # stated in full in templates/CLAUDE.md / templates/CLAUDE.local.md
    # collapsed to one pointer. CLAUDE-STATE.md was condensed in place and
    # CLAUDE.md was left alone -- its bullets are normative and carry
    # measured numbers.
    #
    # 2026-08-26 (seventh pass): ratcheted 146,000 -> 142,000 after a real
    # reduction of 10,147 bytes (142,728 -> 132,581). Three mechanisms, no
    # rule weakened: CLAUDE-STATE.md was rewritten to current state only
    # (14,092 -> 8,833) with the session 52-54 narrative it carried
    # archived verbatim in docs/sessions.md, since nothing in it was
    # pending any more; the nine CLAUDE.md bullets written this session
    # while the work was in flight were tightened, with their evidence
    # moved to docs/sessions.md and their IAM detail to
    # templates/CLAUDE.md; and two purely template-scoped CLAUDE.local.md
    # bullets (the access-script rc/stderr diagnosis, the two install
    # stages) moved to templates/CLAUDE.local.md behind pointers. One
    # genuinely dead bullet was deleted rather than condensed: the
    # login-node MONITORING_HOME poll, which a later bullet in the same
    # file already recorded as retired and which names a variable no file
    # in the repo still contains. The largest CLAUDE.local.md bullet (the
    # shared cluster store, 6,813B) was deliberately left alone for the
    # third pass running -- shrinking it lowers the derived allowance in
    # test_the_ceiling_is_not_slack faster than it raises headroom.
    # 2026-08-27, eighth pass: 142,000 -> 136,000 after 134,877 -> 130,797.
    # Three bullets left the preamble for files that are not always loaded --
    # the postinstall.j2 node-type bullet to templates/CLAUDE.local.md, the
    # job_hpc-benchmark.sh.j2 derivation to hpc-benchmark/CLAUDE.local.md,
    # and the sbatch script bullet condensed in place because scripts/ has no
    # scoped CLAUDE.md.  Separately, CLAUDE-STATE.md's "Rules for writing
    # tests" block moved into CLAUDE.local.md: it is normative, and that file
    # is current state.  That move buys nothing here -- both are in the
    # preamble -- and is not counted as a reduction.  The shared cluster
    # store bullet was left alone for the fourth pass running, for the reason
    # in the note above.
    #
    # Ninth pass (2026-08-27, session 57): the results_bucketname fallback
    # bullet condensed 2,465B -> 1,774B, keeping every normative rule and all
    # four test names -- its four sub-bullets restated their own reasoning at
    # length.  That paid for the async-teardown bullet's live verification and
    # the instruction-surface rule in CLAUDE.md, and the ceiling drops by the
    # ambient slack the ratchet exists to reclaim.
    #
    # 2026-09-01: 127,696 -> 110,442, the largest single reduction so far and
    # the first that moved content out of the preamble rather than condensing
    # it in place.  Two halves.  (1) CLAUDE.local.md lost 2,611B of guidance
    # the committed CLAUDE.md already carried -- six bullets deleted outright
    # (Python-logic location, StrictUndefined, .venv/, shebangs, the
    # hpc-benchmark pointer, "no Ansible playbooks") and six condensed to the
    # half CLAUDE.md does not state.  The dense file is the detail behind the
    # lean one, so a bullet that restates it is pure duplication; that was the
    # split's premise and it had drifted.  (2) 15,001B of CLAUDE.md -- 23
    # bullets, 41% of the file -- moved verbatim to mcp_server/CLAUDE.md, which
    # loads only when that directory is in play.  Scope was the test: a bullet
    # moved only if its subject lives entirely in mcp_server/, deploy_mcp.py or
    # templates/MCP*.json_src.  The seven cross-cutting MCP rules that also
    # bind pcluster_core.py, the CLI or the node templates stayed (the
    # ensure_event_loop rule, the staging tree, the vars file beside the
    # record, on-demand access-script rendering, the record store,
    # MakeClusterParams carrying no region, the defaults file), and a pointer
    # bullet replaced them.  CLAUDE.md now sits under Claude Code's own
    # large-memory warning threshold (~40,000 chars) for the first time.
    # Ceiling ratcheted to 114,000: 3,558B of headroom, above the 2,760 floor
    # and inside the 4,062 allowance -- the reduction is banked, not spent.
    #
    # 2026-09-01 (second pass, same day): 110,442 -> 95,600.  The first pass
    # scoped CLAUDE.md; this one scoped CLAUDE.local.md, and found the dense
    # file was carrying a *third* copy of thirteen bullets whose full versions
    # already sat in templates/CLAUDE.local.md and hpc-benchmark/CLAUDE.local.md
    # -- the nested files even label their sections by the root line number they
    # were moved from, so the summary left behind in the root was the accident,
    # not the plan.  Five were deleted outright because the committed CLAUDE.md
    # still states their rule; eight became one-line pointers that keep the rule
    # resident while the evidence loads with the directory (the kernel holds,
    # luarocks/liblua, Lmod's bc, the GPU NVMe skip, the /etc/profile guard, the
    # compute-node package index, AL2023's absent packages, the sbatch script).
    # Six more with no nested counterpart moved to templates/CLAUDE.local.md and
    # a new mcp_server/CLAUDE.local.md, leaving two consolidated pointers.  The
    # two nested dense files were checked against their own lean siblings and
    # left alone: their entries run 2-18x the rule they carry, which is the
    # lean/dense split working, not duplication.  Ceiling ratcheted to 98,500:
    # 2,900B of headroom, above the 2,760 floor and inside the 4,062 allowance.
    #
    # 2026-09-01 (third pass): 95,600 -> 91,169.  This one emptied
    # CLAUDE-STATE.md rather than either CLAUDE file.  That file is current
    # state by its own first line, and 46% of it had become a record of
    # finished work: a session-summary bullet whose last clause pointed at
    # docs/sessions.md 64-77, and a `## Deferred work` section opening "All
    # six are done ... this list is empty", kept for what a rerun would
    # rediscover.  Both moved verbatim to docs/sessions.md under "Trimmed
    # CLAUDE-STATE.md sections (moved 2026-09-01)"; the two blocks that exist
    # to stop a future session redoing settled work -- the re-open guard and
    # the redeploy recipe -- survive as one-line pointers.  CLAUDE.local.md
    # gave up only 130B (the `python -u` incident), which is the honest
    # result: the 2026-08-07 and 2026-08-13 passes already took its narrative,
    # and what still matches an archive signal is a clause of evidence inside
    # a normative rule, not history.  The stated figure in CLAUDE-STATE.md's
    # own Doc structure section was stale by two passes and is restated here
    # to a fixed point (restating it changes the total it states).  Ceiling
    # 94,200: 3,031B of headroom, above the 2,760 floor, inside the 4,062
    # allowance.
    #
    # 2026-09-01 (fourth pass): 91,351 -> 90,162, reclaiming what the third
    # pass had spent.  Restating the git state cost 182B and left headroom at
    # 2,849 against a 2,760 floor -- 89B of slack, which is the shrinking
    # margin this class exists to stop.  Nothing was archived this time; it
    # was all duplication the earlier passes had not looked for, because they
    # compared each file against its sibling and never CLAUDE-STATE.md against
    # the two CLAUDE files.  Seven claims stated twice: the no-commit rule and
    # the pyflakes sweep (CLAUDE.md's), the doc-hygiene test files (CLAUDE.md's),
    # the credential-free pytest invocation (CLAUDE.local.md pointed at it while
    # restating it), `Not bash -lc` and `discover_defaults_file` (CLAUDE.md
    # states both in full), plus force-pushes and the current-state framing
    # said twice inside CLAUDE-STATE.md itself.  A sentence-level scan across
    # all three files finds no internal duplication left in either CLAUDE file.
    # Ceiling 93,600: 3,438B of headroom, a working margin again.
    #
    # 2026-09-01 (fifth pass): RAISED 93,600 -> 103,200. This is a correction,
    # not a loosening, and it is the one entry in this list that undoes an
    # earlier step rather than tightening it.
    #
    # An adversarial review of the tenth-through-thirteenth passes found that
    # the mcp_server/ split had applied the wrong scoping test: it asked what a
    # bullet was *about* instead of which file it *constrains*. Thirteen of the
    # twenty-three moved rules constrain `src/pcluster_core.py`, the root-level
    # `deploy_mcp.py`/`generate_operator_policy.py`, `templates/MCP*.json_src`
    # or `tests/` -- none of which load `mcp_server/CLAUDE.md`. Among them were
    # prohibitions (`--bootstrap` ordering, ECR force-delete pairing, "deploy
    # cannot grant itself permissions", "no `aws s3 sync` on the container
    # tier"), i.e. exactly the class that must not be absent at the moment it
    # binds. All thirteen are back in the always-loaded file, +9,326B.
    #
    # There is no scoping fix for the root-level scripts: a root file's memory
    # file IS the always-loaded one. And `src/CLAUDE.md` was rejected on
    # measurement rather than taste -- 48 of the last 100 commits touch
    # `src/pcluster_core.py` and 28 of the 43 that touch `mcp_server/` touch
    # `src/` or a root script in the same commit, so it would load in most
    # sessions that matter while adding a fifth surface no guard prices.
    #
    # The lesson for this ceiling specifically: 93,600 was never earned. Moving
    # a rule to a file that does not load where the rule applies reduces the
    # measured number without reducing anything real, and this guard cannot see
    # the difference -- it prices three paths and nothing else. Ratchet from
    # 103,200 (headroom 3,109) going forward; do not treat 93,600 as a target
    # to get back to.
    # 2026-09-01: raised 103,200 -> 123,840 (+20%) at the operator's explicit
    # direction, like the 2026-08-25 step. Recorded as a deliberate loosening.
    #
    # It does not fit the derivation: at a 100,093B preamble it leaves 23,747B
    # of headroom against a 4,260B allowance, and the guards as written permit
    # at most +1.1%. Rather than inflate the allowance (which would corrupt the
    # "two of the largest bullet" derivation) or widen the `total // 5` bound
    # (the anti-gaming cap this file warns against widening), the excess is
    # carried as ONE named number, _DELIBERATE_SLACK. The derivation and the
    # anti-gaming bound are untouched and still mean what they say; the
    # loosening is visible, attributable, and can be ratcheted to zero on its
    # own without touching anything else.
    _CEILING = 123_840

    # One pattern, one copy.  It is used by the relapse detector and by that
    # detector's own vacuity guard, and while each carried its own literal, a
    # mutation narrowing the detector's copy to `#{5,6}` left the guard matching
    # correctly and passed -- two copies of one pattern is the `pkg_dir` hazard.
    _SESSION_HEADING = re.compile(r"^#{2,4}\s+Session\s+\d+\b", re.M)

    # Indirection so the discrimination test can retarget the relapse detector at
    # the archive itself; production value is the preamble.
    _SCAN_FOR_SESSIONS = _PREAMBLE

    @classmethod
    def _sizes(cls):
        sizes = {}
        for relpath in cls._PREAMBLE:
            path = os.path.join(REPO_ROOT, relpath)
            assert os.path.exists(path), (
                f"{relpath} is gone; this guard is measuring nothing.  If it was "
                "renamed, update _PREAMBLE -- do not delete the check."
            )
            sizes[relpath] = len(open(path, "rb").read())
        assert len(sizes) == len(cls._PREAMBLE), "a preamble file was skipped"
        return sizes

    def test_the_preamble_stays_under_the_ceiling(self):
        self._require_full_preamble()
        sizes = self._sizes()
        total = sum(sizes.values())
        assert total <= self._CEILING, (
            "the always-loaded preamble has grown past its budget: "
            + ", ".join(f"{k} {v:,}B" for k, v in sorted(sizes.items()))
            + f" = {total:,}B > {self._CEILING:,}B.\n"
            "Every session pays this before reading any code.  Move the evidence, "
            "not the rule: dated per-session narrative belongs in "
            "`docs/sessions.md`, and a closed hazard whose guard is equality-pinned "
            "can go there too.  Raising _CEILING is the wrong fix -- it is a "
            "ratchet, and the headroom is for one constraint bullet, not a log."
        )

    def test_the_dated_archive_is_not_in_the_preamble(self):
        """The reduction that bought the current headroom was moving the session
        log out.  Appending sessions to either preamble file again is how it comes
        back, and it would come back one small section at a time -- under the
        ceiling each time, which is precisely why the ceiling alone cannot see it.

        `## Session history 17-24` in the archive is a summary note, not a
        section, so `_SESSION_HEADING` deliberately matches only the numbered
        heading form that the per-session record used.

        The scanned set is `_SCAN_FOR_SESSIONS` rather than `_PREAMBLE` inline so
        that the discrimination test below can point this method at the archive --
        a file that is full of the thing being banned -- and require it to raise.
        Without that, neutering the assertion to `assert True` passed."""
        self._require_full_preamble()
        offenders = {}
        for relpath in self._SCAN_FOR_SESSIONS:
            text = open(os.path.join(REPO_ROOT, relpath)).read()
            found = self._SESSION_HEADING.findall(text)
            if found:
                offenders[relpath] = found
        assert not offenders, (
            "a dated session section is back in the always-loaded preamble: "
            + "; ".join(f"{k}: {v}" for k, v in sorted(offenders.items()))
            + ".  Per-session narrative goes in `docs/sessions.md`; the preamble "
            "carries current state and standing constraints only."
        )

    # An instruction to load something unconditionally.  Root CLAUDE.md uses the
    # first of these verbatim about CLAUDE-STATE.md, which is what makes it an
    # always-loaded file in the first place -- see the positive control below.
    #
    # Every alternative is a directive to the reader.  There is deliberately no
    # `read ... in full` variant: it earns nothing (the repo's real directive is
    # caught by the first two) and it fired on "...were read in full" describing a
    # node's own logs -- prose about evidence, 90 characters from an unrelated
    # archive citation on the same 5KB line.  A pattern that cannot separate "load
    # this every time" from "we read this once, during verification" pressures
    # correct documentation into being reworded, so the pattern is what gives.
    _ALWAYS_LOAD = re.compile(
        r"at the start of every session|before taking any action|"
        r"read (?:all|the whole|the entire)\b|"
        r"always read\b|must (?:be )?read\b",
        re.I,
    )
    _ARCHIVE_MENTION = re.compile(r"docs/sessions\.md")

    def test_the_archive_is_not_loaded_by_any_claude_md(self):
        """Moving 29k tokens out of the preamble buys nothing if a constraint file
        tells the reader to go read all of it anyway -- that re-imports the cost
        through the back door while the byte ceiling above still reads green,
        since the archive is not in `_PREAMBLE`.

        What is banned is the *unconditional* form, not the verb.  A scoped
        pointer is the entire point of the split and must keep working:
        `CLAUDE-STATE.md` says "Read `docs/sessions.md` when you need the evidence
        behind a `CLAUDE.md` bullet", and an earlier version of this guard matched
        on `read` alone and failed that line -- which would have pressured the doc
        into being less useful to satisfy a test.  The distinction is whether the
        reader is told to load it every time or only on a stated condition.

        Scoping is per *sentence*, not per line.  A constraint bullet in
        `CLAUDE.md` is one 5KB line, so a line-scoped match pairs an archive
        citation with any always-load phrasing anywhere in the same bullet -- which
        it did, on a sentence about node logs being "read in full" that sat 90
        characters from an unrelated `docs/sessions.md` reference."""
        splitter = re.compile(r"(?<=[.!?])\s+(?=[A-Z`*\[])")
        offenders = {}
        # DERIVED: this was a hardcoded list and `mcp_server/`'s two files were
        # not in it, so the back door this guard exists to close stood open in
        # the files the 2026-09-01 split created -- proven by mutation.
        for relpath in self._every_memory_file():
            path = os.path.join(REPO_ROOT, relpath)
            if not os.path.exists(path):
                continue
            for n, line in enumerate(open(path), 1):
                if not self._ARCHIVE_MENTION.search(line):
                    continue
                for sentence in splitter.split(line):
                    if self._ARCHIVE_MENTION.search(sentence) and self._ALWAYS_LOAD.search(sentence):
                        offenders.setdefault(relpath, []).append(n)
        assert not offenders, (
            "a constraint doc directs the reader to load `docs/sessions.md` "
            "unconditionally, which undoes the split: "
            + "; ".join(f"{k}:{v}" for k, v in sorted(offenders.items()))
            + ".  Cite the specific session or item, or state the condition."
        )

    def test_the_always_load_pattern_matches_a_real_always_load_instruction(self):
        """Vacuity guard, with a positive control taken from the repo rather than
        invented: root `CLAUDE.md`'s own directive about `CLAUDE-STATE.md` is an
        always-load instruction, and it is *why* that file is in `_PREAMBLE`.  The
        pattern must match it with the filename swapped -- and must not match the
        scoped pointer that `CLAUDE-STATE.md` legitimately carries, since a
        pattern that flags both cannot tell the two apart and would just force the
        pointer to be deleted."""
        claude_md = open(os.path.join(REPO_ROOT, "CLAUDE.md")).read()
        directive = [
            line for line in claude_md.splitlines()
            if "CLAUDE-STATE.md" in line and self._ALWAYS_LOAD.search(line)
        ]
        assert directive, (
            "root CLAUDE.md no longer carries an always-load directive for "
            "CLAUDE-STATE.md that this pattern recognizes; either the directive "
            "changed wording (widen _ALWAYS_LOAD) or the file is no longer "
            "always loaded (revisit _PREAMBLE)"
        )
        swapped = directive[0].replace("CLAUDE-STATE.md", "docs/sessions.md")
        assert self._ARCHIVE_MENTION.search(swapped)
        assert self._ALWAYS_LOAD.search(swapped), (
            "the pattern cannot recognize the repo's own always-load phrasing"
        )
        scoped = (
            "Read `docs/sessions.md` when you need the evidence behind a "
            "`CLAUDE.md` bullet, the build a claim was verified on, or why an "
            "approach was rejected."
        )
        assert not self._ALWAYS_LOAD.search(scoped), (
            "the pattern flags a conditional pointer as an always-load "
            "instruction; it cannot distinguish the two"
        )
        # Regression: prose describing evidence, not directing the reader.  This
        # exact sentence fired the guard while `in (?:its|full)` was unanchored.
        evidence = (
            "the head node's cfn-init and all four compute nodes' "
            "`cloud-init-output` were read in full."
        )
        assert not self._ALWAYS_LOAD.search(evidence), (
            "the pattern flags prose about what was read during verification as an "
            "instruction to the reader; anchor the alternative on a directive"
        )

    def test_the_ceiling_is_not_slack(self, monkeypatch):
        """Vacuity guard, in both directions.

        A ceiling far above the current size is decoration -- it would never fire,
        and the first real relapse would still be green.  The bound on headroom is
        *derived* from the largest single bullet in `CLAUDE.local.md` rather than
        being a round number: the budget exists to absorb one substantive constraint, and
        that is what one costs.  A hand-picked constant is a second thing to keep
        true, and widening it is the obvious way to make a failing ceiling pass.

        The second half drives the real assertion with a ceiling below the current
        size and requires it to raise, because the comparison itself is what can
        rot -- an `or True`, or a `<` on the wrong operand, leaves this green while
        checking nothing."""
        self._require_full_preamble()
        total = sum(self._sizes().values())
        headroom = self._CEILING - total
        allowance = self._allowance()
        # The allowance is itself bounded, because inflating it is the obvious way
        # to make a failing ceiling pass -- either by hardcoding a wide number in
        # place of the derivation, or by breaking the derivation so it returns one.
        # Room for two large bullets cannot be a large fraction of the whole
        # preamble. The bound was 1/6 while CLAUDE-STATE.md was itself a bulky
        # session log and contributed most of `total`; now that it has been
        # rewritten into a lean current-state file, CLAUDE.local.md's own
        # bullets make up nearly all of `total`, so the same largest bullet is
        # a bigger fraction of a smaller whole -- a real composition shift, not
        # the allowance being gamed. 1/5 still catches a runaway allowance.
        assert 0 < allowance <= total // 5, (
            f"the derived allowance is {allowance:,}B against a {total:,}B "
            f"preamble; two constraint bullets do not cost that much, so either "
            "the derivation is broken or it was replaced by a constant"
        )
        assert 0 <= headroom <= allowance + self._DELIBERATE_SLACK, (
            f"the ceiling has {headroom:,}B of headroom over the current "
            f"{total:,}B, against an allowance of {allowance:,}B + {self._DELIBERATE_SLACK:,}B authorized slack (two of the "
            "largest bullet in CLAUDE.local.md).  Too much slack and the guard never "
            "fires; if the preamble really did shrink, lower _CEILING in the "
            "same commit rather than banking the room."
        )
        monkeypatch.setattr(type(self), "_CEILING", total - 1)
        with pytest.raises(AssertionError):
            self.test_the_preamble_stays_under_the_ceiling()

    # Headroom fell to 9 bytes before anyone (a Claude session, not the
    # operator) noticed -- the hard ceiling only fires at zero, so several
    # rounds of individually-justified additions each squeezed in under a
    # shrinking margin with nothing forcing a check in between. This is
    # deliberately *not* derived from the ceiling's own allowance the way
    # _largest_bullet_bytes is: it exists to fire well before that one does,
    # as an early warning rather than a second copy of the same check.
    #
    # 2026-08-25: raised 2,000 -> 2,400 alongside the ceiling. A 2,000-byte
    # margin turned out to be too tight to work under: a single substantive
    # constraint bullet runs 700-1,500 bytes, so adding one routinely
    # crossed the floor and forced a trim in the same breath -- which is
    # how a whole session ends up shaving prose instead of writing rules.
    # The floor should fire while there is still room to think, not at the
    # moment of writing.
    # 2026-08-29: raised 2,400 -> 2,760 (+15%). The 2,400 margin was still
    # being consumed faster than it was being reclaimed -- three separate
    # condense passes landed in a single session, each triggered by the floor
    # rather than by anyone choosing to tidy up. A wider margin buys the
    # reclaiming pass some distance from the writing pass, which is the whole
    # point of having a floor above the ceiling.
    _WARNING_FLOOR = 2_760

    # The allowance has a FLOOR as well as a derivation, and the floor is what
    # keeps the two guards jointly satisfiable.  They require
    #     total + _WARNING_FLOOR <= _CEILING <= total + allowance
    # which has no solution at all once `allowance < _WARNING_FLOOR`.  Because
    # the allowance is 2x the largest CLAUDE.local.md bullet, condensing that
    # bullet shrinks the allowance twice as fast as it grows headroom -- so a
    # successful condense pass can make the suite unfixable at ANY value of
    # _CEILING.  That was not hypothetical: the largest bullet fell 6,813B ->
    # 2,031B over four passes on 2026-09-01, leaving the band 1,303B wide and
    # 651B from empty.  The floor is _WARNING_FLOOR plus 1,500 -- the top of
    # the 700-1,500B range this file's own comments give for one substantive
    # bullet -- so the band is never narrower than the thing it is sized to
    # absorb.  It is stated relative to _WARNING_FLOOR, not as a literal, so
    # the relationship cannot drift if that floor is retuned.
    _ALLOWANCE_FLOOR = _WARNING_FLOOR + 1_500

    # Headroom the operator has authorized ON TOP of the derived allowance.
    # This is not a budget to spend: it exists so an operator-directed ceiling
    # can be honored without silently breaking the anti-slack guard, and the
    # correct direction for it is down. Ratchet this to 0 before ratcheting
    # _CEILING, so the derivation resumes governing.
    _DELIBERATE_SLACK = 20_640

    @classmethod
    def _allowance(cls):
        return max(2 * cls._largest_bullet_bytes(), cls._ALLOWANCE_FLOOR)

    def test_the_two_guards_are_jointly_satisfiable(self):
        """The band `[total + _WARNING_FLOOR, total + allowance]` must be
        non-empty, or no _CEILING passes both guards and the only way to a
        green suite is to weaken one of them.

        This fires on the *derivation*, before anyone hits it as an
        unexplainable failure while trying to ratchet after a real reduction."""
        self._require_full_preamble()
        allowance = self._allowance()
        assert allowance >= self._WARNING_FLOOR, (
            f"the allowance is {allowance:,}B against a {self._WARNING_FLOOR:,}B "
            "working margin, so no _CEILING satisfies both guards at once; "
            "raise _ALLOWANCE_FLOOR or lower _WARNING_FLOOR deliberately"
        )
        total = sum(self._sizes().values())
        lo = total + self._WARNING_FLOOR
        hi = total + min(allowance, total // 5) + self._DELIBERATE_SLACK
        assert lo <= self._CEILING <= hi, (
            f"_CEILING {self._CEILING:,} is outside the only band that passes "
            f"both guards, [{lo:,}, {hi:,}]"
        )

    def _assert_headroom_has_a_working_margin(self):
        total = sum(self._sizes().values())
        headroom = self._CEILING - total
        assert headroom >= self._WARNING_FLOOR, (
            f"headroom is {headroom:,}B, under the {self._WARNING_FLOOR:,}B "
            f"working margin. Condense a CLAUDE.local.md bullet and lower "
            f"_CEILING now -- do not keep squeezing new content in under a "
            f"shrinking margin, that is exactly how it reached 9 bytes."
        )

    def test_headroom_has_a_working_margin(self, monkeypatch):
        """Fires long before the hard ceiling, forcing a condense-and-ratchet
        cycle while there is still room to do it calmly instead of after
        the fact. A substantive CLAUDE.md bullet costs 700-1,500 bytes today
        (see the largest current ones); the floor is sized to absorb one or
        two before the real ceiling in test_the_preamble_stays_under_the_ceiling
        bites, so a rule can be written without shaving prose in the same
        breath.

        Vacuity guard in the same shape as test_the_ceiling_is_not_slack:
        a ceiling set to leave less than the floor must fail this check."""
        self._require_full_preamble()
        self._assert_headroom_has_a_working_margin()
        total = sum(self._sizes().values())
        monkeypatch.setattr(type(self), "_CEILING", total + self._WARNING_FLOOR - 1)
        with pytest.raises(AssertionError):
            self._assert_headroom_has_a_working_margin()

    @staticmethod
    def _largest_bullet_bytes():
        """Size of the biggest top-level bullet in CLAUDE.local.md, sub-bullets
        included. The dense constraint bullets live there now, not in the lean
        CLAUDE.md -- reading the lean file here would derive a tiny allowance
        from a file that no longer holds the bloat this budget guards against.

        Top-level constraints start at column 0 with `- `; their continuations are
        indented or blank."""
        lines = open(os.path.join(REPO_ROOT, "CLAUDE.local.md")).read().split("\n")
        sizes, cur = [], None
        for line in lines:
            if line.startswith("- "):
                if cur is not None:
                    sizes.append(cur)
                cur = [line]
            elif cur is not None and (line.startswith(" ") or not line.strip()):
                cur.append(line)
            elif cur is not None:
                sizes.append(cur)
                cur = None
        if cur is not None:
            sizes.append(cur)
        widths = [len("\n".join(b).encode()) for b in sizes]
        assert len(widths) > 20, (
            f"only found {len(widths)} top-level bullets in CLAUDE.local.md; the "
            "bullet parser is not reading the file's structure"
        )
        return max(widths)

    def test_the_relapse_detector_actually_discriminates(self, monkeypatch):
        """`test_the_dated_archive_is_not_in_the_preamble` can rot in two ways: the
        pattern stops matching the heading form the record uses, or the assertion
        stops asserting.  Both survived a battery that only checked the pattern
        against the archive out of band -- narrowing the detector's own copy to
        `#{5,6}` was green, and so was replacing its `assert not offenders` with
        `assert True`.

        So this points the real method at `docs/sessions.md`, which holds 13
        numbered session sections, and requires it to raise.  That exercises the
        production pattern and the production assertion together; neither can be
        neutered without failing here."""
        self._require_full_preamble()
        # The indirection that lets this test retarget the detector is also a way
        # to disable it: pointing _SCAN_FOR_SESSIONS at some other file leaves the
        # pattern and the assertion intact while scanning nothing that matters.
        assert type(self)._SCAN_FOR_SESSIONS == self._PREAMBLE, (
            "the relapse detector is no longer scanning the preamble; "
            "_SCAN_FOR_SESSIONS exists for this test to override per-call, not to "
            "be repointed in production"
        )
        archive = os.path.join("docs", "sessions.md")
        assert os.path.exists(os.path.join(REPO_ROOT, archive)), (
            "docs/sessions.md is gone -- either the split was reverted or the "
            "archive moved; the preamble budget assumes the record lives there"
        )
        found = self._SESSION_HEADING.findall(
            open(os.path.join(REPO_ROOT, archive)).read()
        )
        assert len(found) >= 10, (
            f"_SESSION_HEADING found only {len(found)} session headings in the "
            "archive; it is not matching the form the record actually uses"
        )
        monkeypatch.setattr(type(self), "_SCAN_FOR_SESSIONS", (archive,))
        with pytest.raises(AssertionError):
            self.test_the_dated_archive_is_not_in_the_preamble()
