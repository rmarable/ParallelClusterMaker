# Claude Instructions — ParallelClusterMaker

Co-authored-by: Rodney Marable <rodney.marable@gmail.com>
Co-authored-by: Claude Code <noreply@anthropic.com>

## Always do first

Read `CLAUDE-STATE.md` at the start of every session before taking any action.
It records current branch, test status, pending work, and standing constraints.

## Repository layout

```
make_pcluster.py          # create clusters
kill_pcluster.py          # destroy clusters
access_cluster.py         # SSH into head node
src/pcluster_core.py      # pure testable functions shared by all three scripts
src/pcluster_aux_data.py  # data tables and helper functions
src/create_pcluster.yml   # Ansible playbook — cluster build
src/delete_pcluster.yml   # Ansible playbook — cluster teardown
templates/                # Jinja2 templates (config, vars file, install scripts, IAM)
performance/              # HPC benchmark suite and performance analysis scripts
tests/                    # pytest suite (158 tests as of last run)
```

## Constraints

- **No commits or pushes** unless the user explicitly asks.  Do not ask about committing during work.
- Target branch is `claude-init`; main branch is `master`.
- All Python logic must live in `src/pcluster_core.py` or `src/pcluster_aux_data.py` so it is testable without AWS credentials.
- `templates/vars_file.j2` is rendered with `StrictUndefined` — `| default()` filters do not rescue from UndefinedError; every variable must be defined upstream.
- The `.venv/` virtual environment is excluded from git.  All dependencies are in `requirements.txt`.
- **Python 3.12 only.** `aws-parallelcluster` ≤ 3.15.1 does not support Python 3.13 or 3.14 — Python 3.14 breaks `asyncio.get_event_loop()` at runtime and the upstream fix (PR #7149) is unmerged. The repo is pinned via `.python-version`. Always create `.venv` with `python3.12 -m venv .venv`.

## Test suite

```
python -m pytest tests/ -q          # must stay green (158 tests)
make lint                           # ansible-lint — exits 0, profile: basic (production raises fqcn/ignore-errors warnings on legacy tasks)
make shellcheck                     # shellcheck on performance/scripts/*.sh
```

Run the test suite after any change to Python, Jinja2 templates, or conftest.py.

## Behavior

- **Don't fabricate.** If a function, file, flag, or AWS behavior is not confirmed by reading the actual code or docs, say so — do not invent plausible-sounding details.
- **Don't guess silently.** If something is uncertain, say it is uncertain.  A wrong confident answer is worse than an honest "I don't know."
- **Ask before assuming.** If a request is ambiguous — scope unclear, two reasonable interpretations exist, or a destructive action is implied — ask a clarifying question before proceeding.  One focused question is better than charging ahead and getting it wrong.
- **No inline multi-line python3 -c.** Never run a `python3 -c '...'` block that contains a newline followed by `#`. Write the script to `$CLAUDE_JOB_DIR/tmp/` first and invoke `python3 <path>` instead. This avoids Claude Code's argument-injection scanner firing on every audit.

## Code style

- No comments unless the WHY is non-obvious.
- No docstrings beyond a single short line.
- No backwards-compatibility shims.
- Prefer editing existing files over creating new ones.
- No emojis.
