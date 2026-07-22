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
tests/                    # pytest suite (255 tests as of last run)
```

## Constraints

- **No commits or pushes** unless the user explicitly asks.  Do not ask about committing during work.
- Target branch is `claude-init`; main branch is `master`.
- All Python logic must live in `src/pcluster_core.py` or `src/pcluster_aux_data.py` so it is testable without AWS credentials.
- `templates/vars_file.j2` is rendered with `StrictUndefined` — `| default()` filters do not rescue from UndefinedError; every variable must be defined upstream.
- The `.venv/` virtual environment is excluded from git.  All dependencies are in `requirements.txt`.
- **Python 3.12 only.** `aws-parallelcluster` ≤ 3.15.1 does not support Python 3.13 or 3.14 — Python 3.14 breaks `asyncio.get_event_loop()` at runtime and the upstream fix (PR #7149) is unmerged. The repo is pinned via `.python-version`. Always create `.venv` with `python3.12 -m venv .venv`.
- **IAM policy is split into three managed policies.** `ParallelClusterInstancePolicy.json_src` no longer exists. The policy is stored as `-A.json_src` (22 statements, ≤6,015 bytes minified), `-B.json_src` (18 statements, ≤6,064 bytes minified), and `-C.json_src` (1 statement, ≤561 bytes minified) to stay under IAM's 6,144-byte managed policy limit. All three policies are named `<ec2_iam_policy>-A`, `-B`, and `-C` and must be deleted together on teardown. The `S3Objects` resource covers `arn:aws:s3:::parallelcluster-*/*` (not just `parallelcluster-<CLUSTER_NAME>-*/*`) so the head node can fetch configs from PCluster's internal system bucket.
- **`IAMListGlobal` in Policy-B is intentional.** The `iam:ListGroups`, `iam:ListRoles`, and `iam:ListUsers` actions (Sid `IAMListGlobal`) are scoped to the account but use wildcard resource paths. The PCluster head node daemon calls `iam:ListRoles` at startup to locate its own role; narrowing the resource prefix to `parallelcluster-*` breaks the daemon. This is a known PCluster requirement — do not remove or restrict it further.
- **Defaults file auto-detection.** If `<cluster_name>_defaults.yml` exists in the repo root but `--use_defaults` was not passed, `make_pcluster.py` prints a `*** WARNING ***` and suggests the flag. This is intentional — never suppress it.
- **Venv guard uses `sys.prefix`.** The three entry-point scripts check `os.path.realpath(sys.prefix)` against the repo's `.venv/` directory, not `sys.executable`. Homebrew Python symlinks resolve outside `.venv/`, so `sys.executable` was incorrect.
- **Shebangs use `#!/usr/bin/env python`.** Not `python3` — `env python3` on macOS resolves to the system Python, bypassing the active venv.
- **Ansible deprecation warnings are suppressed globally** via `ansible.cfg` (`deprecation_warnings = False`). Do not re-enable them or work around them per-task.
- **Performance results on S3 are keyed by serial number.** Results sync to `s3://<s3_bucketname>/performance-results/<cluster_name>/<cluster_serial_number>/` on teardown, so rebuilds of the same cluster name accumulate rather than overwrite. The source tree (scripts, templates) syncs to `s3://<s3_bucketname>/performance/` and is pulled back by postinstall on head node rebuild.
- **Performance deployment is gated on `enable_hpc_performance_tests`.** All S3 sync tasks (create, delete, postinstall) are wrapped in `when: enable_hpc_performance_tests == "true"` / `{% if enable_hpc_performance_tests == 'true' %}`. Never add performance tasks outside that gate.
- **Monitoring is gated on `enable_monitoring`.** All monitoring tasks in Ansible playbooks and all `{% if enable_monitoring == 'true' %}` template branches are gated on this flag. When `true`, a fourth managed policy `<ec2_iam_policy>-M` is created and attached alongside `-A/-B/-C`; it must be deleted on teardown. The SSM parameter `/parallelcluster/<cluster_name>/grafana/admin-password` is created by the monitoring installer and must also be deleted on teardown.
- **Monitoring S3 staging.** The `aws-parallelcluster-monitoring` tarball is downloaded from GitHub at cluster-build time (`create_pcluster.yml`) and staged to S3. The wrapper script (`monitoring-post-install-wrapper.j2`) pulls from S3 at node boot — never from GitHub. This keeps private-subnet nodes and air-gapped environments working. The download is integrity-checked via `checksum: "{{ monitoring_version_checksum }}"` in the `get_url` task — the checksum is threaded from `pcluster_defaults.yml` → `make_pcluster.py` → `vars_file.j2` → playbook. The checksum for v2.6 is set in `pcluster_defaults.yml`; if `monitoring_version` is bumped, update `monitoring_version_checksum` accordingly (obtain with: `curl -sL <tarball-url> | sha256sum`).
- **`-M` policy naming convention.** Monitoring IAM permissions live in `templates/ParallelClusterInstancePolicy-M.json_src` (7 statements, ~1,400 bytes minified). Named `<ec2_iam_policy>-M` at runtime. `InstanceRole` and `AdditionalIamPolicies` are mutually exclusive in PCluster v3 — monitoring permissions must be attached directly to `ec2_iam_role`, not via `AdditionalIamPolicies`.

## Test suite

**Always use the project venv.** Never invoke `python`, `pytest`, or any project tool with the system Python. Use `.venv/bin/python` explicitly, or activate the venv first (`source .venv/bin/activate`). The system Python on this machine is 3.14, which is incompatible with `aws-parallelcluster` and lacks `botocore`.

```
.venv/bin/python -m pytest tests/ -q   # must stay green (255 tests)
make lint                               # ansible-lint — exits 0, passes production profile
make shellcheck                         # shellcheck on performance/scripts/*.sh
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
