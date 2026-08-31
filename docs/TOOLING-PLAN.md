# Python tooling plan

Research only. Every number below was measured on this working tree on
2026-08-30 unless it is explicitly marked as an estimate. Two workstreams were
editing `src/pcluster_core.py`, `tests/` and the `Makefile` while this was
written, so counts drift; the *shapes* do not.

Measurement environment: `.venv/bin/python` 3.12.13, pyflakes 3.4.0, black
26.5.1. **ruff and mypy are not installed and were not installed** — every
claim about them is derived from the pyflakes data or from documented rule
semantics, and is labelled.

---

## 0. What the gates actually are right now

`make lint` no longer runs ansible-lint. `src/create_pcluster.yml` and
`src/delete_pcluster.yml` are deleted in the working tree, and the in-flight
`Makefile` change replaces the target with a pyflakes undefined-name sweep over
`git ls-files '*.py'`. That is the right call and this plan builds on it rather
than proposing an alternative.

Three things about that change are worth fixing regardless of anything else in
this document:

1. **The CI `lint` job will fail.** `.github/workflows/test.yml`'s `lint` job
   installs `ansible ansible-lint` and never creates `.venv`. The new target
   invokes `.venv/bin/python -m pyflakes`, which does not exist in that job's
   container. Measured: the `test` job is the only one with a
   `python -m venv .venv` step. The job needs the same venv step as `test`, and
   no longer needs `ansible-lint`.
2. **`make lint` is strictly weaker than `tests/test_undefined_names.py`.** The
   test's `_python_files()` walks `mcp_server/`, `tests/` and `src/` for
   untracked files as well as `git ls-files`; the Makefile target uses
   `git ls-files` alone. The three gitignored doc-hygiene tests are therefore
   outside the Makefile gate. That is acceptable for a fast pre-commit gate, but
   it means `make lint` passing is not evidence `make test` will.
3. **`ansible-lint` and `.ansible-lint` are now dead.** `ansible` itself is
   still a required test dependency — `tests/conftest.py:49` imports
   `ansible.plugins.action.template` (load-bearing, see §1) and
   `tests/test_templates.py:84` reads `ActionModule.run`'s source for Ansible's
   own `trim_blocks`/`lstrip_blocks` defaults. Only `ansible-lint` is removable.

`make lint` currently exits non-zero on 8 undefined names in
`tests/test_templates.py` (`_delete_playbook_tasks`, `_playbook_task_list`,
`_create_playbook_tasks`, `_effective_when`, `_eval_when`) — fallout from the
playbook deletion, being handled by the workstream that caused it.

---

## 1. The pyflakes backlog, characterised

`.venv/bin/python -m pyflakes $(git ls-files '*.py')` over 83 tracked files:

| kind | code | all tracked | non-test |
|---|---|---|---|
| imported but unused | F401 | 52 | 28 |
| f-string is missing placeholders | F541 | 11 | 10 |
| local variable assigned but never used | F841 | 4 | 3 |
| redefinition of unused name | F811 | 2 | 1 |
| **total** | | **69** | **42** |

The **42** in the problem statement is the non-test count. `tests/` adds 27.
There are no star-imports and no undefined names (the latter is gated).

### The 28 non-test unused imports are three different things

**18 are load-bearing re-exports and must not be removed.** The core/shim split
put the logic in `src/pcluster_core.py`; the thin entry-point scripts import the
names so that tests can reach them through the shim's module object. Verified by
locating the calls:

| file | names | proof |
|---|---|---|
| `check_pcluster.py:24` | 7 `check_*` | `tests/test_check_pcluster.py` (`import check_pcluster as chk`) calls `chk.check_cfn_status`, `chk.check_slurm`, `chk.check_s3`, … — 20+ call sites |
| `cost_pcluster.py:25` | 4 of 5 | `tests/test_cost_report.py` (`as cp`) calls `cp._safe`, `cp._date_range`, `cp._check_tag_activated`, `cp._get_cluster_cost` |
| `diagnose_pcluster.py:24` | 5 | `tests/test_diagnose.py` (`as dx`) — `dx._format_sinfo` ×6, `dx._parse_sacct` ×5, `dx._tail_lines` ×5, `dx._VALID_EC2_USERS` ×4; `_sinfo_state_is_ok` is pinned by identity in `tests/test_check_pcluster.py:276` |
| `list_pcluster.py:26` | `_age_str` | `tests/test_list_pcluster.py::TestAgeStr` loads the module by spec and calls `mod._age_str` |

The odd one out is `cost_pcluster.py`'s `_utc_today`: no test reaches it through
`cp`, and the tests monkeypatch `pcluster_core._utc_today` instead. It is a
cohort member of four load-bearing siblings; leaving it costs nothing and
removing it invites someone to remove the other four.

**3 already carry `# noqa: F401` with a written rationale** —
`access_cluster.py:23`, `grafana_tunnel.py:22`, `kill_pcluster.py:26`, all
`import subprocess` bound so that a test patching `mod.subprocess.run` also
intercepts `pcluster_core`'s calls on the same process-wide module object.
pyflakes ignores `noqa`; ruff honours it.

**7 are genuinely dead and safe to delete:**

- `make_pcluster.py:32` — `BotoCoreError`, `ClientError`,
  `EndpointConnectionError`, `NoCredentialsError`. Zero references anywhere in
  the file, and no test patches `make_pcluster.ClientError`. Left over from the
  core/shim split, which moved the `except` clauses into `pcluster_core.py`.
- `access_cluster.py:26` — `_resolve_access_node_type`. The file uses
  `core_resolve_access_node_type` at line 80; `tests/test_kill_access.py`
  imports the private helper from `pcluster_core` directly.
- `mcp_server/completion.py:36,37` — `json`, `os`. Worth actually removing
  rather than leaving: `CLAUDE.md` justifies that module's exhaustive sweep on
  it touching "no AWS and no clock", and an unused `os` import is an invitation
  to reach for a clock.

### The 11 f-strings are all cosmetic — zero real defects

Every one is either a continuation line of an implicitly-concatenated group
whose *other* lines carry placeholders (`pcluster_core.py:3551`, `11359`), or a
literal line inside a run of `print(f"...")` calls where the `f` was applied
uniformly (`pcluster_core.py:3464-3465`, `9882`, `11387`, `11395-11397`,
`check_pcluster.py:85`). I read all 11. None is a dropped placeholder. This
category is exactly what a linter *silences* rather than fixes — the value of
enabling F541 is style consistency, not correctness.

### The 4 unused locals split 2/2

- `pcluster_core.py:7622` and `:10543` — `_lock_path = _acquire_distributed_cluster_lock(...)`.
  Deliberate: the call is made for its effect and the handle discarded, with the
  underscore prefix signalling exactly that. **Ruff would not flag these** —
  its default `dummy-variable-rgx` exempts leading-underscore names. pyflakes has
  no such setting. (Estimated from documented ruff defaults; not executed.)
- `pcluster_core.py:9759` — `ansible_verbosity = params.ansible_verbosity`, never
  read. Real dead code now that no playbook executes. The *CLI flag* must stay:
  `make_pcluster.py:116` and `kill_pcluster.py:98` still accept it and
  `kill_pcluster.py:207` prints a "has no effect" notice.
- `tests/test_mcp_tools.py:1811` — `fn = ...` assigned and discarded in a helper.

### The 2 redefinitions

- `mcp_server/tools.py:84` and `:86` import `_derive_locks_bucket` **twice in the
  same `from pcluster_core import (...)` statement**. Harmless at runtime; a
  genuine signal that the import block is unmaintained.
- `tests/test_s3_cluster_lock.py:515` re-imports `pcluster_core` inside a
  function shadowing the module-level import at line 31.

### The decision `CLAUDE.md` asks for, with numbers

Clearing the whole non-test backlog is **7 deletions, 1 duplicate-line removal,
1 dead local, 10 `f` prefixes, and 21 lines that need a `# noqa: F401` comment
they mostly already deserve.** That is a small, mechanical, one-afternoon change
with no behavioural risk — *provided* the 18 re-exports are annotated rather
than deleted. The danger is not the size of the backlog; it is that
`ruff check --fix` would delete all 18 by default and take 40+ tests with them.
The tests would catch it loudly, but a `--fix` run is not the place to discover
the architecture.

---

## 2. Ruff

**Not installed; not run.** Everything here is derived from the pyflakes census
plus ruff's documented rule semantics, and should be re-measured before adoption
with:

```
.venv/bin/python -m ruff check --statistics .
```

### What ruff would report on day one

Ruff's `F` rules are pyflakes. Two documented differences reduce the count:

- 6 of the 69 findings sit on lines already carrying `# noqa` (3 `subprocess`,
  `tests/conftest.py:49`, `tests/test_aux_data.py:23`,
  `tests/test_mcp_infra.py:29`). Ruff honours them; pyflakes does not.
- 2 of the 4 F841s (`_lock_path`) match ruff's default dummy-variable regex.

**Estimated day-one `F` count: ~61 tracked, ~37 non-test.**

### The repo is already written for ruff

`# noqa: BLE001` appears 7 times (`mcp_server/build_runner.py`,
`completion_runner.py`, `deploy.py`, `tools.py`, `src/pcluster_core.py:1377`).
`BLE001` is a **ruff** code (flake8-blind-except); it means nothing to pyflakes
or stock flake8. Someone already anticipated this adoption.

That does **not** mean `BLE` should be selected. Measured: **109**
`except Exception` / `except BaseException` clauses across the tree (99
non-test), of which 7 are annotated. Selecting `BLE` adds ~102 findings against
a codebase whose broad-except-with-warning pattern is deliberate and documented.

### Two rules that must be off, with measurements

- **`E402` (module import not at top of file): 142 occurrences across 51 files.**
  This is not a backlog, it is the architecture. Every entry point runs a
  `sys.prefix` venv guard and then `sys.path.insert(0, _src_dir)` *before*
  `from pcluster_core import ...`. Around 30 sites already carry
  `# noqa: E402`; the remaining ~110 would all need one. Ignore it globally.
- **`I` (isort) must not be enabled, and never with `--fix`.** 54 of 83 files
  call `sys.path.insert`. Ruff's isort sorts contiguous import blocks and does
  not lift an import above an intervening statement, so the `sys.path.insert`
  barrier should hold — but "should" is doing real work in that sentence, the
  failure mode is every entry point raising `ModuleNotFoundError`, and I could
  not run it. If import sorting is ever wanted, run `ruff check --select I --diff`
  first and read the whole diff.

### Concrete first `pyproject.toml`

Deliberately narrow. It codifies what the repo already enforces plus a small
set of "guaranteed bug" rules, and nothing aesthetic.

```toml
[project]
name = "parallelclustermaker"
version = "0.0.0"
requires-python = "==3.12.*"

[tool.ruff]
line-length = 100
target-version = "py312"
extend-exclude = [".venv", "active_clusters", "src/vars_files", "__pycache__"]

[tool.ruff.lint]
# F     = pyflakes (what the repo already runs)
# E9    = syntax / IO errors
# B     = flake8-bugbear, but only the always-a-bug members (see explicit list)
# PLE   = pylint errors only, never conventions or refactors
select = ["F", "E9", "PLE"]
extend-select = [
    "B002",   # unary prefix increment -- always a typo
    "B006",   # mutable default argument
    "B008",   # function call in default argument
    "B012",   # break/return inside finally swallows the exception
    "B017",   # assertRaises(Exception) -- too broad to prove anything
    "B023",   # loop variable captured by a closure
    "B904",   # raise-without-from inside except
]
ignore = [
    "E402",   # 142 occurrences: the venv guard + sys.path.insert prologue
]

[tool.ruff.lint.per-file-ignores]
# The shim entry points re-export pcluster_core names so tests can reach them
# through the module object (tests/test_check_pcluster.py's `chk.check_slurm`,
# tests/test_cost_report.py's `cp._safe`, ...). These are NOT unused.
"check_pcluster.py"   = ["F401"]
"cost_pcluster.py"    = ["F401"]
"diagnose_pcluster.py"= ["F401"]
"list_pcluster.py"    = ["F401"]
# Tests import for their side effects and for symbol-existence assertions.
"tests/*"             = ["F401", "F811"]
```

`line-length = 100` rather than black's 88: measured p95 of non-test Python is
**79 characters**, p99 is 93, and only 1.6% of lines exceed 88. 100 makes the
rule a genuine outlier-catcher (0.5% of lines) instead of a reformatting mandate.

**Prefer the per-file-ignores above over blanket `# noqa: F401` comments on 18
individual lines.** A per-file ignore with the comment explaining *why* survives
a `--fix` run; 18 scattered `noqa`s are 18 chances to drop one.

### Verify

```
.venv/bin/python -m ruff check .          # expect 0 after the config lands
.venv/bin/python -m ruff check --statistics .   # before, to confirm ~37/~61
make test                                 # unchanged
```

---

## 3. Formatting

This is the section with the most measurement behind it and the clearest
recommendation.

### What black would do

`black --check --diff` over the 83 tracked files (read-only, default settings):

- **74 of 83 files would change**
- **17,850 changed lines** (40,622 lines of diff)
- `src/pcluster_core.py` grows **12,364 → 13,605 lines (+10%)**
- Top churn: `pcluster_core.py` 2,945 lines, `test_templates.py` 1,741,
  `test_shell_surfaces.py` 1,458, `test_make_pcluster.py` 1,456

The churn is **not** line length — see the p95=79 measurement above. It is
black exploding compact multi-value lines one-item-per-line and destroying the
deliberate column alignment in this repo's region and instance-type tables:

```
-    "sa-east-1":      "South America (Sao Paulo)",
-        {"Type": "TERM_MATCH", "Field": "instanceType",    "Value": instance_type},
```

### What that actually breaks — measured, not estimated

I copied the tree to a scratch directory, symlinked `.venv`, ran the suite,
then ran black over all 86 `.py` files and ran the suite again.

| | failed | passed |
|---|---|---|
| baseline | 143 | 3493 |
| after black | 147 | 3489 |

The 143 baseline failures are pre-existing fallout from the in-flight playbook
deletion (125 in `test_templates.py`, 5 in `test_build_secrets.py`) and are
not related to formatting. Diffing the failure *sets*:

**Exactly 4 new failures. 0 tests flipped fail → pass.**

1. `test_claude_docs_line_citations.py::…::test_every_cited_line_holds_what_the_manifest_says`
   — `src/pcluster_core.py:2505` (`iam.attach_role_policy(`) moved to **2628**.
2. `test_make_pcluster.py::TestPclusterCallsWorkOffTheMainThread::test_every_pcluster_lib_import_installs_a_loop`
   — broken at **all 7 sites**.
3. `test_make_pcluster.py::TestAFailedBuildLeavesARecordTheCallerCanRead::test_every_post_lock_failure_path_records_itself`
4. `test_templates.py::…::test_the_cloudformation_status_is_shown_only_when_it_differs`

### The line-citation risk is an order of magnitude smaller than assumed

`tests/test_claude_docs_line_citations.py`'s `_EXPECTED` manifest holds **7
entries. Exactly one points at a `.py` file.** The other six are
`hpc-benchmark/hpc-benchmark.sh` (×4) and `.j2` files, which no Python formatter
touches. The manifest is also deliberately a *substring* check, not an exact-line
check — its own docstring says "reformatting a line should not fail this". It
fails only when the line **number** shifts.

So the cost is: one manifest entry, and the matching `src/pcluster_core.py:2505`
citation, which lives in `templates/CLAUDE.local.md:31` (the `attach_role_policy`
bullet), not in the root `CLAUDE.local.md`. Two edits. The failure message names the new
content, and `test_every_checkable_citation_is_in_the_manifest` forces the doc
side to be updated too. This is not the blocker.

### The real breakages are line-*adjacency* guards

**#2 is the important one.** `tests/test_make_pcluster.py` asserts that the line
immediately following `import pcluster.lib as pc` is `ensure_event_loop()`:

```python
missing = [i + 1 for i in imports if src[i + 1].strip() != "ensure_event_loop()"]
```

Black inserts a blank line after a function-body import block, so
`ensure_event_loop()` moves from `i+1` to `i+2` at all 7 sites and the guard
fails everywhere. `CLAUDE.md` describes this guard as "pinned by AST at each
site plus a real-thread test" — **the site-pinning half is text line adjacency,
not AST.** That is a doc-accuracy defect independent of any formatting decision,
and it is worth fixing on its own merits: the guard would also break if anyone
put a comment between the two lines.

**#3 is a line-*distance* guard.** It walks the AST for
`CreateClusterResult(success=False)` returns and requires a
`_publish_build_failure` call within `0 < line - p <= 12`. Black's one-arg-per-line
expansion pushes the pair past 12. Note this guard is fragile regardless: any
edit inserting 12 lines between them breaks it too.

**#4 is an exact source-substring count** —
`body.count("if cfn_status and cfn_status != status else") == 2` against the raw
text of `pcluster_core.py`. Black rewraps that conditional expression, so the
count drops to 0.

### The biggest risk is the one the pass/fail diff cannot see

#4's companion assertion is `assert unconditional not in body` — a **negative**
substring check on Python source. Under reformatting a negative check does not
fail; it **passes vacuously**. The test stays green while checking nothing.

Measured: **46 negative substring assertions** (`not in src|body|source|code`)
across the test suite. Classified by what feeds the variable:

- **22 read Python source only** — at risk
- 8 read shell/Jinja2 templates only — immune, formatters do not touch `.j2`/`.sh`
- 16 ambiguous or both

So **up to 38 assertions could silently stop guarding anything**, and by
construction no test run can tell you which. #4 is proof the mechanism is real:
it was caught only because the *positive* assertion in the same test also broke.
Had that test carried the negative half alone, formatting would have disarmed it
silently and the suite would have been green.

I could not measure how many of the 38 actually go vacuous — it is a
counterfactual (does the forbidden string, if reintroduced, still appear as one
contiguous run after formatting?), not something a test run reveals.

For contrast, the format-*insensitive* population is large: **154 of 172**
source-reading occurrences are `ast.parse` / `ast.walk`, and all **19** `.lineno`
uses compare line numbers *within a single fresh parse*, so statement ordering —
which black never changes — is all they depend on. Only **18** occurrences use
`inspect.getsource` for text matching, and 3 of those read *Ansible's* installed
source, not ours.

### Recommendation: do not adopt a formatter yet. If you do, sequence it.

The honest cost/benefit: **17,850 changed lines and one unquantifiable
test-weakening risk, in exchange for style consistency in a codebase whose p95
line is 79 characters and whose alignment is often deliberate.** That trade is
not obviously worth taking, and it is the one recommendation here I would push
back on rather than sequence.

If it is taken anyway, the only safe order is:

1. **Harden the four guards first, before any formatting**, and merge that
   separately so the fix is reviewable on an unchanged tree:
   - rewrite the `ensure_event_loop` guard to walk the AST for the statement
     *following* the import in the function body, not `src[i+1]`;
   - replace the `<= 12` line-distance window with an AST containment check
     (same enclosing block / same `except` handler);
   - replace `body.count("...")` with an AST match on the conditional expression;
   - convert every negative source-substring assertion that reads Python to an
     AST check, or at minimum add a positive companion assertion so vacuity is
     detectable.
2. **Then convert the 22–38 negative assertions**, or accept in writing that they
   become advisory.
3. **Then format, in one commit that touches nothing else**, with
   `--skip-magic-trailing-comma` if the alignment loss matters, and add the
   commit to `.git-blame-ignore-revs`.
4. **Then regenerate the citation manifest** — one entry, plus the
   `templates/CLAUDE.local.md:31` citation.
   `test_every_checkable_citation_is_in_the_manifest` fails until both sides
   agree, so it self-verifies.
5. Verify with `make test` and a failure-set diff against the pre-format run,
   not a pass count.

`ruff format` is documented as black-compatible; I could not run it, so I cannot
say the four failures would be identical. Assume they are, verify before relying
on it.

---

## 4. Typing

### What exists today, measured

AST census over the 31 non-test modules:

| | functions | with `-> ann` | params | annotated |
|---|---|---|---|---|
| `src/pcluster_core.py` | 317 | **0** | 1044 | **0** |
| `mcp_server/tools.py` | 39 | **23** | 81 | **65** |
| `mcp_server/deploy.py` | 32 | 0 | 95 | 0 |
| `src/pcluster_aux_data.py` | 14 | 0 | 30 | 0 |
| everything else (27 files) | 120 | 0 | 205 | 0 |
| **total** | **522** | **23** | **1455** | **65** |

`mcp_server/tools.py` is the only annotated module, and it is annotated because
FastMCP forces it — all 23 return annotations are on `@tool`-registered
functions, and 22 of them are `-> dict` with one `-> list[dict]`. Per `CLAUDE.md`
these are a **runtime contract**, not documentation: `list_queues` annotated
`-> dict` while returning a list failed every call. Nothing else in
`mcp_server/` carries a single annotation — `deploy.py` has 32 functions and
95 parameters, all bare.

### The codebase is not untyped — the *data* is fully typed

`src/pcluster_core.py` contains **29 `@dataclass` declarations with 260
annotated fields**: `ClusterRecord`, `MakeClusterParams`, `CreateClusterResult`,
`DeleteClusterResult`, `ClusterHealthReport`, `DiagnosticReport`, `AccessInfo`,
`SlurmCommandResult`, `QueueApplyResult`, and 20 more. `build_make_cluster_params`
already calls `typing.get_type_hints(MakeClusterParams)` at runtime to drive
boolean coercion — the annotations are load-bearing today.

What is missing is annotations on the *functions*, and for the highest-value
subset that is mechanical rather than a judgement call.

### The highest-value annotations, identified

Of 22 module-level `core_*` functions, **14 return exactly one known dataclass**
and the mapping is derivable from the AST with no human decision:

```
core_get_cost_report        -> CostReportResult
core_check_cluster_health   -> ClusterHealthReport
core_diagnose_cluster       -> DiagnosticReport
core_resolve_access_node_type -> AccessInfo
core_run_slurm_command      -> SlurmCommandResult
core_run_slurm_command_via_ssm -> SlurmCommandResult
core_manage_grafana_tunnel  -> TunnelResult
core_rotate_cluster_key     -> KeyRotationResult
core_add_queue              -> QueueAddResult
core_remove_queue           -> QueueRemoveResult
core_apply_queue_config     -> QueueApplyResult
core_delete_cluster         -> DeleteClusterResult
core_create_cluster         -> CreateClusterResult
core_finalize_cluster_build -> CreateClusterResult
```

These 14 annotations are worth more than the other 508 combined, because
`core_*` is the seam every shim and every MCP tool crosses. `core_create_cluster`
in particular is the one `CLAUDE.md` says "returns a `CreateClusterResult`; it
must never `sys.exit`" — an annotation makes that statement checkable instead of
merely written down.

### Recommended path

**Do not run `mypy --strict` on `src/pcluster_core.py`.** 12,364 lines, 317
unannotated functions, boto3 and `pcluster.lib` both untyped in this venv. The
output would be thousands of lines and would be turned off. (Not measured —
mypy is not installed. This is a judgement based on the file's size and the
zero-annotation baseline; re-measure before committing to it.)

Instead:

1. **Annotate the 14 `core_*` returns above.** Pure addition, no config, no new
   dependency, verified by `make test`. Do this whether or not mypy is ever
   adopted.
2. **Add `mypy` and a `[tool.mypy]` section that checks a narrow surface
   strictly and nothing else loosely.** The right first surface is
   `mcp_server/` — it is already 80% annotated on parameters, it is the module
   where a type error is a *runtime* failure (FastMCP validates against the
   annotation), and it is 1/8th the size of `pcluster_core.py`:

   ```toml
   [tool.mypy]
   python_version = "3.12"
   files = ["mcp_server"]
   ignore_missing_imports = true      # boto3, pcluster.lib, fastmcp
   warn_unused_ignores = true

   [[tool.mypy.overrides]]
   module = "mcp_server.tools"
   disallow_untyped_defs = true       # the tier that FastMCP already enforces
   ```

   Expected first-run volume: unmeasured. Run `mypy mcp_server` once before
   deciding whether `disallow_untyped_defs` is affordable on `tools.py` alone or
   needs to start narrower still.
3. **`src/pcluster_aux_data.py` is the natural second module** — 14 functions,
   30 parameters, no AWS calls, pure data. Small enough to annotate completely
   in one sitting and to gate strictly thereafter.
4. **`src/pcluster_core.py` should be annotated opportunistically**, function by
   function as each is edited, and should not be gated. A `# type: ignore`
   backlog on a 12,000-line file is the same trap as a lint backlog nobody can
   clear.

Do not add mypy to `make lint` until step 2's real output is known.

---

## 5. What `make lint` should become

The in-flight target is the right shape: fast, exit-code-clean, one gate the
repo genuinely commits to. Three increments, cheapest first.

**Step A — fix CI (do this now, independent of everything else).** Give the
`lint` job the venv step the `test` job has and drop `ansible-lint`:

```yaml
      - name: Install dependencies
        run: python -m venv .venv && .venv/bin/pip install -r requirements.txt
      - name: Run lint
        run: make lint
```

Buys: a lint job that runs at all. Risks: none. Verify: push, watch the job.
Also remove `ansible-lint` from `requirements.txt` and delete `.ansible-lint`
— but **keep `ansible`**, `tests/conftest.py` and `tests/test_templates.py`
both require it.

**Step B — make `make lint` the ruff invocation, keeping the same guarantee.**
After the `pyproject.toml` in §2 lands:

```make
lint:
	.venv/bin/python -m ruff check .
```

Buys: the undefined-name gate (ruff's `F821` is the same check) plus the 7 real
dead imports, the duplicate import, and the `B0xx` always-a-bug rules — with
`# noqa` finally honoured, and per-file-ignores documenting the re-export
architecture in one reviewable place. Risks: `ruff check --fix` deleting the 18
load-bearing re-exports if someone runs it before the per-file-ignores land —
so **land the config first, in its own commit, and never put `--fix` in the
Makefile.** Verify: `.venv/bin/python -m ruff check .` exits 0, and `make test`
is unchanged.

`tests/test_undefined_names.py` should stay exactly as it is. It covers the
untracked files the Makefile's `git ls-files` misses, it carries the incident
narrative, and a gate worth having is worth having in the suite that CI runs.

**Step C — add `make format-check` only if §3 is accepted**, and only as a
separate non-blocking target until the four guards are hardened:

```make
format-check:
	.venv/bin/python -m ruff format --check .
```

Do not fold it into `make lint` until it is green.

---

## Sequenced summary

| # | step | cost | risk | verify |
|---|---|---|---|---|
| 1 | Fix the CI `lint` job's missing venv | 3 lines of YAML | none | CI goes green |
| 2 | Drop `ansible-lint` + `.ansible-lint`; keep `ansible` | 2 deletions | none | `make test` |
| 3 | Delete the 7 dead imports, the duplicate, the dead local | 10 lines | none | `make test` |
| 4 | Fix the `ensure_event_loop` guard to be AST-based | ~10 lines | none | it still catches a removed call |
| 5 | Add `pyproject.toml` with the §2 ruff config | 1 file | `--fix` deleting re-exports | `ruff check .` = 0 |
| 6 | `make lint` → `ruff check .` | 1 line | none | CI |
| 7 | Annotate the 14 `core_*` returns | 14 lines | none | `make test` |
| 8 | `mypy` on `mcp_server/` only | 1 config block | unmeasured volume | `mypy mcp_server` |
| 9 | Formatting — **only after** the negative-assertion audit | 17,850 lines | vacuous tests | failure-set diff |

Steps 1–7 are safe, cheap, and independently valuable. Step 8 needs one
measurement first. Step 9 is the only one I would argue against on its merits.

## What I could not measure

- **Ruff's actual output.** Not installed, not installed by choice. All ruff
  counts are derived from pyflakes plus documented rule semantics.
- **Mypy's actual output.** Not installed. The "thousands of errors" claim for
  `pcluster_core.py` is a judgement, not a measurement.
- **`ruff format`'s diff vs black's.** Documented as black-compatible; unverified
  here.
- **How many of the 22–38 negative source assertions actually go vacuous after
  formatting.** This is a counterfactual, not observable from a test run. It is
  the single largest unquantified risk in this document.
- **Whether ruff's isort would survive the 54 `sys.path.insert` files.** The
  rule's documented behaviour says yes; the failure mode if it does not is every
  entry point failing to import.

---

## DECIDED, 2026-08-30: no black

Not adopted, and not on grounds of risk -- the risk was measured and then
removed. Of 22 negative assertions over Python source carrying a literal
needle, 4 contained parentheses or spaces a formatter could move; those 4
now compare with layout stripped (`assert_absent_ignoring_formatting`),
verified by reintroducing a banned expression wrapped across lines and
watching the test fail. The `ensure_event_loop` guard, which black broke at
all 7 sites, walks the AST now and does not care about blank lines.

So the objection that remains is not correctness. It is that black rewrites
**76 of 85 files**, which buries recent work in `git blame` and invalidates
the `file:line` citations the docs carry. That is a judgement about history,
and the answer was no.

**What that leaves behind is worth keeping**: the four layout-proof
assertions and the AST-based guard are better tests whether or not a
formatter ever runs. They were written to unblock a decision that went the
other way, and they stand on their own.

Do not re-open this by citing the vacuity risk -- that part is fixed. Any
future case for black has to argue the diff.
