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

## Test suite

```
python -m pytest tests/ -q          # must stay green (158 tests)
make lint                           # ansible-lint — exits 0, known warnings are documented in README
make shellcheck                     # shellcheck on performance/scripts/*.sh
```

Run the test suite after any change to Python, Jinja2 templates, or conftest.py.

## Behavior

- **Don't fabricate.** If a function, file, flag, or AWS behavior is not confirmed by reading the actual code or docs, say so — do not invent plausible-sounding details.
- **Don't guess silently.** If something is uncertain, say it is uncertain.  A wrong confident answer is worse than an honest "I don't know."
- **Ask before assuming.** If a request is ambiguous — scope unclear, two reasonable interpretations exist, or a destructive action is implied — ask a clarifying question before proceeding.  One focused question is better than charging ahead and getting it wrong.

## Code style

- No comments unless the WHY is non-obvious.
- No docstrings beyond a single short line.
- No backwards-compatibility shims.
- Prefer editing existing files over creating new ones.
- No emojis.
