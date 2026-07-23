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
rotate_cluster_key.py     # rotate SSH keypair without cluster rebuild
src/pcluster_core.py      # pure testable functions shared by all three scripts
src/pcluster_aux_data.py  # data tables and helper functions
src/create_pcluster.yml   # Ansible playbook — cluster build
src/delete_pcluster.yml   # Ansible playbook — cluster teardown
templates/                # Jinja2 templates (config, vars file, install scripts, IAM)
hpc-benchmark/            # HPC benchmark suite and performance analysis scripts
tests/                    # pytest suite (320 tests as of last run)
```

## Constraints

- **No commits or pushes** unless the user explicitly asks.  Do not ask about committing during work.
- Target branch is `claude-init`; main branch is `main`.
- All Python logic must live in `src/pcluster_core.py` or `src/pcluster_aux_data.py` so it is testable without AWS credentials.
- `templates/vars_file.j2` is rendered with `StrictUndefined` — `| default()` filters do not rescue from UndefinedError; every variable must be defined upstream.
- **`pcluster_os` is derived from `base_os`.** PCluster's `Os:` field does not accept the `arm` suffix (e.g. `ubuntu2404arm` → `ubuntu2404`, `rhel9arm` → `rhel9`). `pcluster_os = base_os.removesuffix("arm")` is set in `make_pcluster.py`, written to `vars_file.j2`, and used in `config.pcluster.j2`. Supported ARM OS values: `ubuntu2204arm`, `ubuntu2404arm`, `rhel8arm`, `rhel9arm`, `alinux2arm` (future). Any new template variable must be traced through the full pipeline: Python vars dict → `vars_file.j2` → template — and added to `tests/conftest.py` so the test suite catches it.
- The `.venv/` virtual environment is excluded from git.  All dependencies are in `requirements.txt`.
- **Python 3.12 only.** `aws-parallelcluster` ≤ 3.15.1 does not support Python 3.13 or 3.14 — Python 3.14 breaks `asyncio.get_event_loop()` at runtime and the upstream fix (PR #7149) is unmerged. The repo is pinned via `.python-version`. Always create `.venv` with `python3.12 -m venv .venv`.
- **IAM policy is split into three managed policies.** `ParallelClusterInstancePolicy.json_src` no longer exists. The policy is stored as `-A.json_src` (22 statements, ≤6,124 bytes minified), `-B.json_src` (16 statements, ≤5,911 bytes minified), and `-C.json_src` (5 statements, ≤1,822 bytes minified) to stay under IAM's 6,144-byte managed policy limit. All three policies are named `<ec2_iam_policy>-A`, `-B`, and `-C` and must be deleted together on teardown. The `S3Objects` resource covers `arn:aws:s3:::parallelcluster-*/*` (not just `parallelcluster-<CLUSTER_NAME>-*/*`) so the head node can fetch configs from PCluster's internal system bucket. IAM role/instance-profile resources are covered by both flat-name ARNs (`parallelcluster-<CLUSTER_NAME>-*`) and path-based ARNs (`parallelcluster/<CLUSTER_NAME>/*`) — PCluster v3 uses the latter for compute fleet roles.
- **`IAMListGlobal` in Policy-B is intentional.** The `iam:ListGroups`, `iam:ListRoles`, and `iam:ListUsers` actions (Sid `IAMListGlobal`) are scoped to the account but use wildcard resource paths. The PCluster head node daemon calls `iam:ListRoles` at startup to locate its own role; narrowing the resource prefix to `parallelcluster-*` breaks the daemon. This is a known PCluster requirement — do not remove or restrict it further.
- **Defaults file auto-detection.** If `<cluster_name>_defaults.yml` exists in the repo root but `--use_defaults` was not passed, `make_pcluster.py` prints a `*** WARNING ***` and suggests the flag. This is intentional — never suppress it.
- **Venv guard uses `sys.prefix`.** The three entry-point scripts check `os.path.realpath(sys.prefix)` against the repo's `.venv/` directory, not `sys.executable`. Homebrew Python symlinks resolve outside `.venv/`, so `sys.executable` was incorrect.
- **Shebangs use `#!/usr/bin/env python`.** Not `python3` — `env python3` on macOS resolves to the system Python, bypassing the active venv.
- **Ansible deprecation warnings are suppressed globally** via `ansible.cfg` (`deprecation_warnings = False`). Do not re-enable them or work around them per-task.
- **Performance results on S3 are keyed by serial number.** Results sync to `s3://<s3_bucketname>/hpc-benchmark-results/<cluster_name>/<cluster_serial_number>/` on teardown, so rebuilds of the same cluster name accumulate rather than overwrite. The source tree (scripts, templates) syncs to `s3://<s3_bucketname>/hpc-benchmark/` and is pulled back by postinstall on head node rebuild.
- **Performance deployment is gated on `enable_hpc_benchmarks`.** All S3 sync tasks (create, delete, postinstall) are wrapped in `when: enable_hpc_benchmarks == "true"` / `{% if enable_hpc_benchmarks == 'true' %}`. Never add performance tasks outside that gate. `hpc-benchmark.sh install` writes compiled binaries to `hpc-benchmark/bin/` and results to `hpc-benchmark/benchmark_results/` — both are gitignored.
- **Monitoring is gated on `enable_monitoring`.** All monitoring tasks in Ansible playbooks and all `{% if enable_monitoring == 'true' %}` template branches are gated on this flag. When `true`, a fourth managed policy `<ec2_iam_policy>-M` is created and attached alongside `-A/-B/-C`; it must be deleted on teardown. The SSM parameter `/parallelcluster/<cluster_name>/grafana/admin-password` is created by the monitoring installer and must also be deleted on teardown.
- **Monitoring S3 staging.** The `aws-parallelcluster-monitoring` tarball is downloaded from GitHub at cluster-build time (`create_pcluster.yml`) and staged to S3. The wrapper script (`monitoring-post-install-wrapper.j2`) pulls from S3 at node boot — never from GitHub. This keeps private-subnet nodes and air-gapped environments working. The download is integrity-checked via `checksum: "{{ monitoring_version_checksum }}"` in the `get_url` task — the checksum is threaded from `pcluster_defaults.yml` → `make_pcluster.py` → `vars_file.j2` → playbook. The checksum for v2.6 is set in `pcluster_defaults.yml`; if `monitoring_version` is bumped, update `monitoring_version_checksum` accordingly (obtain with: `curl -sL <tarball-url> | sha256sum`).
- **Monitoring wrapper bypasses upstream `post-install.sh`.** The upstream script always re-downloads the tarball from GitHub, defeating the S3 staging. The wrapper extracts the S3-staged tarball directly to `MONITORING_HOME` and calls `installer/install.sh` directly. It also suspends `set -u` around `source /etc/profile` because RHEL 7/8/9, CentOS, and Amazon Linux all ship an `/etc/profile` that references `$HISTCONTROL` unconditionally on an `export` line — this crashes under `set -u` if `HISTCONTROL` is not already in the environment. Ubuntu's `/etc/profile` does not have this issue. Do not remove the `set +u / source /etc/profile / set -u` guard.
- **`-M` policy naming convention.** Monitoring IAM permissions live in `templates/ParallelClusterInstancePolicy-M.json_src` (7 statements, ~1,400 bytes minified). Named `<ec2_iam_policy>-M` at runtime. `InstanceRole` and `AdditionalIamPolicies` are mutually exclusive in PCluster v3 — monitoring permissions must be attached directly to `ec2_iam_role`, not via `AdditionalIamPolicies`.
- **Secrets Manager SSH key storage.** At cluster creation, the SSH private key is stored in Secrets Manager at `parallelcluster/<cluster_name>/<serial>/ssh-private-key` and deleted on teardown (`--force-delete-without-recovery`). The secret name is threaded from `pcluster_core._ssh_secret_name` → `make_pcluster.py` → `vars_file.j2` → playbooks. The operator's IAM user/role needs `secretsmanager:CreateSecret`, `PutSecretValue`, `GetSecretValue`, `DeleteSecret`, and `ec2:ImportKeyPair` — these are **not** in any head node managed policy. `rotate_cluster_key.py` rotates the keypair without a cluster rebuild; `active_clusters/<cluster_name>/retrieve_ssh_key.<cluster_name>.sh` recovers the key from Secrets Manager if the local `.pem` is lost.
- **GPU support is gated on `enable_gpu`.** `is_gpu_instance(instance_type)` in `pcluster_aux_data.py` detects GPU families by prefix (g4dn, g4ad, g5, g5g, g6, gr6, p3, p3dn, p4d, p4de, p5). If `enable_gpu == "false"` but the compute instance is a GPU family, `make_pcluster.py` auto-enables it and prints `*** INFO ***`. GPU block in `postinstall.j2` installs `nvtop`/`htop` and mounts NVMe instance store at `/local_scratch` (single device: XFS; multiple devices: RAID0 via `mdadm`). NVMe device detection uses `/sys/block/nvme*/device/model` filtered for "Instance Storage". **Jinja2 constraint:** `${#arr[@]}` triggers the Jinja2 `{#` comment tag parser — use `$(echo "${arr[@]}" | wc -w)` instead.
- **EFA-GDR is derived from `enable_gpu` + `enable_efa` + instance family.** `needs_efa_gdr(instance_type, enable_efa)` returns `True` only for p4d/p4de/p5 with EFA enabled. The derived `enable_efa_gdr` variable is set in `make_pcluster.py` and controls the `GdrSupport: true` line in `config.pcluster.j2` under the `Efa:` block. Do not set `GdrSupport` unconditionally — non-GDR instances reject it at cluster creation.

## Test suite

**Always use the project venv.** Never invoke `python`, `pytest`, or any project tool with the system Python. Use `.venv/bin/python` explicitly, or activate the venv first (`source .venv/bin/activate`). The system Python on this machine is 3.14, which is incompatible with `aws-parallelcluster` and lacks `botocore`.

```
.venv/bin/python -m pytest tests/ -q   # must stay green (320 tests)
make lint                               # ansible-lint — exits 0, passes production profile
make shellcheck                         # shellcheck on hpc-benchmark/hpc-benchmark.sh
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
