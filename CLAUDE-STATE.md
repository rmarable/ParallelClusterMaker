# Claude State — ParallelClusterMaker

Last updated: 2026-07-22 (session 12)

## Branch

`claude-init` (target); `master` (main)

## Test status

**263 / 263 passing** — last verified 2026-07-22, all green.

## Standing constraints

- **No commits or pushes** until the user explicitly says to.  Do not ask about it during work.
- Do not amend existing commits.

## Current grade

**A+ (97)** — All CRITICAL, HIGH, MEDIUM, and LOW findings from code review resolved. All 4 ceiling items addressed: `_validate_queue_sizes` call-order fix, `_render_policy` raises `ValueError` (not `SystemExit`), `_setup_iam` idempotency via `_role_existed` flag, `_cleanup_iam_on_failure` extracted to `pcluster_core.py`. 263 tests passing, ansible-lint production profile clean. Integration smoke test harness added.

## Commits this branch (since master)

- `1b6fd01` Remove JumphostMaker, SGE/Torque, Serverless, and v2 dead code
- `47b09a5` Migrate to PCluster v3: restructure, rewrite, harden, and audit-fix
- `ccc4ac3` Fix shell safety, IAM hardening, quoting, dead code, and formatting
- `d326c84` Fix YAML linting, botocore consolidation, documentation accuracy
- `18b5667` Ansible lint, SSH hardening, and botocore consolidation
- `a4d77b3` Fix template renames, botocore imports, and playbook copy-paste
- `65b5cf8` Performance plots, S3 results preservation, and deployment automation
- `955b002` Add integration test harness, fix IAM idempotency, and harden code quality
- `096da63` Add SSH key Secrets Manager integration, dynamic EFA lookup, Turbot auto-detect, and code review fixes
- `58ac377` Update headnode/compute subnet definition order for clarity
- `203a885` Refactor integration test: require --defaults file, fix credential/profile bugs, and add instance types to launch summary
- `cca2318` Remove Axb_random suite, rename enable_hpc_benchmarks, fix monitoring wrapper, and improve VPC error
- `a1eb2a1` Add ubuntu2204arm/ubuntu2404arm support, fix monitoring wrapper RHEL bash bug
- `4c86f56` Switch defaults to c8g/ubuntu2404arm, fix ARM OS pipeline, harden teardown
- `c153570` Add rhel8arm/rhel9arm support and update docs
- `297ea9a` Add Grafana SSH tunnel script, fix Apache port conflict, and add private IP fallback
- `cd91460` Document Apache2 port conflict fix in monitoring section

## What was fixed across all sessions

### Python
- `_resolve_bool`: handles `bool`/`int` YAML values; None → sys.exit
- `_resolve` cast error → sys.exit with message
- `_check_fsx_s3`: guards empty/null bucket; dead double-assignment removed
- `_load_or_create_serial`: TOCTOU race fixed with `O_EXCL`; exits cleanly on empty serial file
- `_validate_cluster_name`: rejects digit-first names
- `_validate_cluster_owner`: rejects trailing/consecutive hyphens
- `_validate_az_input`: validates full AZ pattern before bootstrap slice
- `access_cluster.py`: calls `_validate_cluster_name` before path resolution
- `kill_pcluster.py`: Ctrl-C abort passes None for cleanup params; dead `delete_efs` flag removed; Turbot moved after AZ verify
- `make_pcluster.py`: venv guard appends `os.sep`; `ebs_root` from `ebs_shared_dir`; `cluster_lifetime` cast to str; `stage_dir` uses `tempfile.gettempdir()`; IAM/vars_file render wrapped in cleanup-on-exception; Turbot moved after AZ verify; Ansible build failure cleans up IAM; `botocore.exceptions.ClientError` consolidated to imported `ClientError`

### Ansible playbooks
- `create_pcluster.yml`: timer forward-reference (CREATE-001) fixed; keypair existence guard (CREATE-002) added; `wait_for` port-22 task before ssh-keyscan; all SSH/SCP commands have BatchMode+ConnectTimeout; ROLLBACK states in until loop; SNS topic quoted; inline dict braces cleaned
- `delete_pcluster.yml`: DELETE_FAILED exit condition added; FSx hydration policy detached before role deletion; inline dict braces cleaned; `failed_when`/`until` conditions extended to check `stdout` as well as `stderr` (pcluster v3 writes JSON errors to stdout); `pcluster_delete_timeout` default changed from self-referencing template to literal `30`; timer stop moved to after `Delete the cluster data directory` (last real work task)

### IAM policy (`ParallelClusterInstancePolicy.json_src`)
- Trailing comma (invalid JSON) removed
- `EC2LaunchTemplate` split into `EC2LaunchTemplateCreate` (`aws:RequestTag`), `EC2LaunchTemplateModify` (`aws:ResourceTag`), `EC2LaunchTemplateDescribe` (`Resource: *`)
- `IAMCreateRole` split out with `iam:PermissionsBoundary` condition (only valid on CreateRole)
- `IAMRoleRead` carries read/delete actions without stale conditions
- `IAMAttachDetachPolicy` split out with `iam:PolicyARN` condition (only valid on Attach/Detach)
- `IAMRolePolicy` now includes `iam:PutRolePolicy`; stale conditions removed
- `iam:AddRoleToInstanceProfile`/`RemoveRoleFromInstanceProfile` deduplicated (kept in `IAMInstanceProfile`)
- `ec2:DescribeKeyPairs` deduplicated (kept in `EC2Describe`)
- `ec2:AttachVolume`, `DisassociateAddress`, `ReleaseAddress` moved to `EC2ModifyTagged`
- `ec2:DeleteTags` scoped to `EC2DeleteTagsTagged` with cluster-name tag condition
- EFS `UpdateFileSystem`/`ModifyMountTargetSecurityGroups`/`DeleteMountTarget` in `EFSDelete` (tag-conditioned)
- FSx `UpdateFileSystem`/`UntagResource` in `FSxDelete` (tag-conditioned)
- `iam:PolicyARN` condition on `IAMRolePolicy`
- Dead actions removed: Batch PassRole, `ds:DescribeDirectories`, ASG LaunchConfigs, ECS statement, serverless ARNs, `s3:PutObjectAcl`
- `PassRole` split into `IAMPassRole` (PassedToService condition) and `IAMPassRoleInstanceProfile`
- SQS and SNS split into global-read + cluster-scoped-write statements

### Templates
- `vars_file.j2`: 8 unused variables removed (`spack_user`, `spack_group`, `s3_object_path`, `s3_url`, `s3_read_write_resource`, `ebs_settings`, `efs_settings`, `fsx_settings`)
- `postinstall.j2`: `chown user:group` args quoted; `BASHRC=` assignment quoted; alias `cd` paths quoted; dead `SPACK_USER`/`SPACK_GROUP` removed
- `config.pcluster.j2`: `compute_subnet_ids` handles both list and string input; `ebs_shared_dir` MountDir quoted
- `sbatch_default_submission_script.sh`: backtick command substitutions replaced with `$(…)`

### Shell scripts
- `shellcheck` exits 0 at `--severity=style` across all scripts
- `hpc-benchmark.sh`: `tmpdir` promoted to `_build_tmpdir` global (SC2064 trap fix); `_fetch()` function replaces multi-word variable; `ls` glob replaced with `find`
- `hpc-perftest.sh`: `-C`/`--cluster` passed through to `make_standalone_plots.py`
- `generate_sbatch_custom_templates.sh`: SC2043 suppressed; `$JOBCOUNT` quoted; `$` removed from arithmetic
- `csv_summary_time_measurement.sh`: array glob quoted; SC2231 for-loop globs fixed; single-item loop replaced with `if`
- `combine_csv_summary_files_for_plotting.sh`: unused `PYTHON3` removed; for-loop glob quoted; timestamp format uses `-` not `:`
- `run_axb.sh`: `read -ra` array replaces bare `for N in $MATRIX_SIZES`; PYTHON3 invocations quoted
- `perf-sbatch.sh`: nullglob array captured before sort; explicit empty-check added

### YAML
- `yamllint` exits 0 across all YAML files (`.yamllint` config added, `line-length: 220`)
- `create_pcluster.yml` / `delete_pcluster.yml`: inline dict inner spaces removed
- `.github/workflows/test.yml`: `---` added; `on:` quoted as `"on":`
- `pcluster_defaults.yml`: `---` added

### Code quality
- `black` applied to all 11 Python source and test files
- `spack_user` / `spack_group` removed from `tests/conftest.py`
- `botocore.exceptions.ClientError` consolidated to imported `ClientError` in `make_pcluster.py`

### Session 3 fixes (commit 18b5667)

- `.yamllint`: added `---` document-start; fixed `braces.max-spaces-inside: 1`; added `octal-values` forbid rules
- `kill_pcluster.py`: consolidated botocore imports — `from botocore.exceptions import BotoCoreError, ClientError, EndpointConnectionError, NoCredentialsError`; removed `import botocore.exceptions`; replaced all qualified `botocore.exceptions.*` references with bare names
- `src/create_pcluster.yml`: removed dead `local_os: "{{ lookup('pipe','uname') }}"` var; renamed `HeadNodePublicIP` → `head_node_public_ip` (33 occurrences, fixes ansible-lint `var-naming`); added `-o StrictHostKeyChecking=accept-new` to all 16 SSH/SCP commands; wrapped 7 overlong lines to comply with 220-char limit
- `CLAUDE.md`: added behavior rule prohibiting inline multi-line `python3 -c` blocks with `#` comments; corrected lint note to reflect `profile: basic`

### Session 5 fixes (uncommitted)

#### Python / venv
- `.python-version`: pinned to `3.12` — `aws-parallelcluster` ≤ 3.15.1 fails hard on Python 3.14 due to `asyncio.get_event_loop()` raising `RuntimeError`; upstream fix (PR #7149) unmerged
- `.venv/`: recreated with `python3.12`; 158/158 tests pass
- `tests/test_make_pcluster.py`: `_FakeClientError` now subclasses `botocore.exceptions.ClientError` (was plain `Exception`, only worked on 3.14 because botocore was absent); added `from botocore.exceptions import ClientError`
- `.github/workflows/test.yml`: CI matrix extended to include Python 3.13
- `make_pcluster.py`, `kill_pcluster.py`, `access_cluster.py`: shebang changed from `#!/usr/bin/env python3` to `#!/usr/bin/env python` so the active venv's interpreter is used
- `make_pcluster.py`, `kill_pcluster.py`, `access_cluster.py`: venv guard changed from `sys.executable` startswith to `sys.prefix` equality — Homebrew Python symlinks resolve outside `.venv/` via realpath

#### IAM policy — 3-way managed policy split
- `templates/ParallelClusterInstancePolicy.json_src` deleted — original monolithic policy exceeded 10,240-byte inline limit
- `templates/ParallelClusterInstancePolicy-A.json_src`: 22 statements (EC2Describe through S3Objects), ≤5,660 bytes minified; `S3Objects` resource broadened to `arn:aws:s3:::parallelcluster-*/*` to cover PCluster's internal system bucket
- `templates/ParallelClusterInstancePolicy-B.json_src`: 17 statements (S3Bucket through FSxDelete), ≤5,691 bytes minified; `S3Bucket` resource broadened to `arn:aws:s3:::parallelcluster-*` for same reason
- `templates/ParallelClusterInstancePolicy-C.json_src`: 1 statement (AllowAccessToSSM), ≤561 bytes minified
- `make_pcluster.py`: new `_render_policy()` helper; `_setup_iam()` uses `create_policy` + `attach_role_policy` (managed, not inline); creates/attaches all three (-A/-B/-C); new `_delete_managed_policies()` helper iterates `-A`/`-B`/`-C`; all three cleanup sites use `_delete_managed_policies()`
- `src/delete_pcluster.yml`: `amazon.aws.iam_managed_policy` module; `with_items` lists `-A`, `-B`, `-C`

#### Dead code removal — Ganglia
- `make_pcluster.py`: removed `--enable_ganglia` arg, default, resolve call, template context key, and debug print
- `templates/config.pcluster.j2`: CloudWatch Monitoring block now unconditional (was gated on `enable_ganglia`)
- `templates/sns_build_summary_report.j2`: CloudWatch link now unconditional
- `templates/vars_file.j2`: Ganglia section removed
- `src/create_pcluster.yml`: "Print CloudWatch monitoring note" task removed
- `pcluster_defaults.yml`, `osiris_defaults.yml`: `enable_ganglia` key removed
- `tests/conftest.py`: `enable_ganglia` fixture keys removed; docstrings updated

#### Defaults / warnings
- `make_pcluster.py`: added `*** WARNING ***` when `<cluster_name>_defaults.yml` exists but `--use_defaults` was not passed
- `make_pcluster.py`: all four `WARNING` messages now use `*** WARNING ***` header format
- `make_pcluster.py`: all three runtime `ERROR` print blocks now use `*** ERROR ***` header format
- `cluster_lifetime` default changed from `7:0:0` to `0:24:0` in `make_pcluster.py`, `pcluster_defaults.yml`, `osiris_defaults.yml`, and README

#### Ansible playbook fixes
- `src/create_pcluster.yml`: added `key_type: ed25519` to `amazon.aws.ec2_key` task — ubuntu2404 rejects RSA keys
- `templates/config.pcluster.j2`, `templates/vars_file.j2`: `Deployed_On` → `DEPLOYMENT_DATE` (was undefined in Ansible context)
- `src/delete_pcluster.yml`: `failed_when`/`until` extended to check `stdout` (pcluster v3 writes JSON errors to stdout, not stderr); `pcluster_delete_timeout: "{{ pcluster_delete_timeout | default(30) }}"` self-reference fixed → literal `30`; timer stop moved after `Delete the cluster data directory`

#### Documentation
- `README.md`: Python 3.12 constraint documented; installation uses `python3.12 -m venv`; defaults file naming convention updated to `<cluster_name>_defaults.yml`; monitoring integration added to Things to Do; `cluster_lifetime` default updated; stale lint warning row removed
- `CLAUDE.md`: Python 3.12 constraint, 3-way IAM managed policy split (with system bucket note), venv guard, shebang, and defaults auto-detection all documented

### Session 6 fixes (commit 65b5cf8)

#### Performance plots (`performance/scripts/make_standalone_plots.py`)
- Added `_fit_curve()` helper: power-law fit (y = a·x^b) via log-log `numpy.polyfit`; draws dashed curve in matching series color; silently skips if < 3 positive finite points
- All five plot branches (`unified`, `compute`, `fileproc`, `separated`, `cost`) switched from `plt.plot()` to `line, = ax.plot()` to capture line color, then call `_fit_curve()` after each scatter
- `np.RankWarning` replaced with `warnings.simplefilter("ignore")` inside `_fit_curve()` — `np.RankWarning` was removed in NumPy 2.x
- Axis ranges changed from zero-origin to data-driven tight bounds with 5% padding and `MaxNLocator` (no excessive whitespace)
- Plot titles updated: removed `run_axb.sh / hpc-perftest.sh and` from all five `Generated Using` lines; `separated` parenthetical changed from `and Displayed as Separate Data Sets` to `(Displayed as Separate Data Sets)`
- Matrix sizes label changed from bare `MATRIX_SIZES` value to `Matrix Sizes = [matrix.conf settings: 1000, 2000, 3000, 4000, 5000]` (space-separated conf value joined with `, `)
- `import warnings` added

#### Performance deployment
- `src/create_pcluster.yml`: new task at end of `Stage performance test scripts` block — `aws s3 sync performance/ → s3://<bucket>/performance/` excluding `*.pyc`, `__pycache__/*`, `summary/*`, `summary_final/*`, `plots/*`; gated by enclosing `when: enable_hpc_benchmarks == "true"`
- `src/delete_pcluster.yml`: new block before cluster deletion — describes cluster to get head node IP, then SSH-runs `aws s3 sync ~/performance/<cluster_name>/ → s3://<bucket>/performance-results/<cluster_name>/<cluster_serial_number>/`; gated on `enable_hpc_benchmarks == "true"`, `failed_when: false` so AccessDenied does not abort teardown; serial number subdir preserves history across rebuilds
- `templates/postinstall.j2`: inside `HeadNode` case, gated on `{% if enable_hpc_benchmarks == 'true' %}` — pulls `s3://<bucket>/performance/` to `~/performance/`, `chmod +x` the dispatcher script, `pip3 install matplotlib pandas numpy seaborn scipy` (`--break-system-packages` on ubuntu, without on dnf distros)

#### Path and array fixes
- `performance/scripts/combine_csv_summary_files_for_plotting.sh`: plot command hint fixed — uses `$(cd "$(dirname "$0")/.." && pwd)/hpc-perftest.sh` instead of wrong relative path
- `performance/jinja2/combine_csv_summary_files_for_plotting.j2`: plot command hint uses `{{ performance_rootdir }}/hpc-perftest.sh`
- `performance/hpc-perftest.sh`: empty `cluster_arg` array expansion fixed with `"${cluster_arg[@]+"${cluster_arg[@]}"}"` idiom (bash `set -u` safe)
- `tests/conftest.py`: added `performance_rootdir`, `performance_stage_dir`, `performance_template_dir` keys to fixture (required by updated `.j2` template)
- `ansible.cfg`: added `[defaults] deprecation_warnings = False` to suppress s3_sync and iam_role module deprecation noise

### Session 4 fixes (committed a4d77b3)

- `templates/access_cluster.j2`: renamed `HeadNodePublicIP` → `head_node_public_ip` (session 3 rename was not propagated here — production breakage)
- `templates/sns_build_summary_report.j2`: same stale rename fixed
- `tests/conftest.py`: fixture key renamed `HeadNodePublicIP` → `head_node_public_ip` (was masking both template breaks)
- `make_pcluster.py`: consolidated remaining `botocore.exceptions.*` qualified references to bare imports; added `BotoCoreError`, `EndpointConnectionError`, `NoCredentialsError` to `from botocore.exceptions import`
- `src/create_pcluster.yml`: collapsed three copy-paste EBS/EFS/FSxL SSH blocks (65 lines) into `set_fact` + 3 looped tasks
- `.ansible-lint`: production profile now passes (0 failures, 0 warnings)
- `CLAUDE.md`: lint note updated to accurately reflect `profile: basic` with explanation

### Session 7 fixes (uncommitted)

#### enable_monitoring feature

- `templates/ParallelClusterInstancePolicy-M.json_src` (NEW): 7 IAM statements for Grafana/Prometheus stack — MonitoringEC2Describe, MonitoringCloudFormation, MonitoringFSx, MonitoringPricing, MonitoringSSMGrafana, MonitoringKMS, MonitoringCloudWatchLogs; ~1,390 bytes minified; SSM resource scoped to `/parallelcluster/<CLUSTER_NAME>/grafana/*`
- `templates/monitoring-post-install-wrapper.j2` (NEW): wrapper script template; downloads tarball from S3 (not GitHub), extracts to `/opt/aws-parallelcluster-monitoring/`, runs upstream `post-install.sh`; logs to `/var/log/parallelcluster-monitoring-install.log`
- `templates/vars_file.j2`: added monitoring section gated on `enable_monitoring == 'true'` — defines `monitoring_version`, `monitoring_s3_dest`, `monitoring_wrapper_src`, `monitoring_wrapper_dest`
- `templates/config.pcluster.j2`: head node `OnNodeConfigured` uses `Sequence` (postinstall first, monitoring wrapper second) when `enable_monitoring == 'true'`; compute queue gets `CustomActions.OnNodeConfigured.Script` for the monitoring wrapper when `enable_monitoring == 'true'`
- `make_pcluster.py`: `--enable_monitoring` (choices true/false) and `--monitoring_version` (default v2.6) CLI args; resolve calls; `_setup_iam()` creates and attaches `-M` policy when `enable_monitoring=True`; `_delete_managed_policies()` appends `-M` suffix when `enable_monitoring=True`; all 3 call sites of `_delete_managed_policies` updated; `cluster_parameters` dict includes `enable_monitoring`, `monitoring_version`, `monitoring_s3_dest`; build summary prints Grafana URL and SSM password retrieval command when monitoring enabled and head IP is known
- `src/create_pcluster.yml`: monitoring block (gated `when: enable_monitoring == "true"`) — downloads pinned tarball from GitHub at build time, uploads to S3, templates wrapper script, uploads wrapper to S3; `"Monitoring: {{ enable_monitoring | bool | upper }}"` added to launch summary
- `src/delete_pcluster.yml`: deletes SSM parameter `/parallelcluster/<cluster_name>/grafana/admin-password` and `-M` policy when `enable_monitoring == "true"`
- `tests/conftest.py`: `monitoring_version`, `monitoring_s3_dest`, `monitoring_wrapper_src`, `monitoring_wrapper_dest` added to default fixture; new `cluster_params_monitoring_enabled` fixture variant with `enable_monitoring: "true"`
- `tests/test_templates.py`: `test_template_renders_monitoring_enabled_variant` parametrized test; JSON validity + 6,144-byte size tests for all four `-A/-B/-C/-M` policy files; `import json, re` added
- `pcluster_defaults.yml`: `enable_monitoring: "false"` and `monitoring_version: "v2.6"` added under `# --- Monitoring ---` section
- `README.md`: Monitoring bullet added to Features list; Monitoring section added after HPC Performance Tests (deploy details, Grafana access, SSM password retrieval, IAM note, S3 staging note, version pinning, custom AMI recommendation); monitoring removed from Roadmap (implemented)
- `CLAUDE.md`: `enable_monitoring` gate rule, `-M` policy naming convention, S3-staging approach, SSM parameter path and teardown requirement documented; test count updated to 182

### Session 11 fixes (uncommitted)

#### Integration test harness

- `tests/integration/run_integration_test.sh` (NEW): Bash smoke test — preflight checks, `--defaults FILE` (required, caller-supplied), creates cluster, polls for CREATE_COMPLETE (30 min limit), fetches head node IP, SSH smoke test (`ec2_user` read from defaults file, falls back to `ubuntu`), Slurm job submission + completion polling, formatted PASSED summary, `trap EXIT` cleanup with `CLUSTER_CREATED` guard; `--keep` flag skips teardown on success; cluster name is `itest-HHMMSS` (12 chars) to keep serial numbers under IAM policy size limit; `$AWS_PROFILE` inherited from environment, `--profile` overrides it; `--turbot_account` not used (not a Turbot cross-account flow)
- `tests/integration/README.md` (NEW): Usage, `--defaults` flag explanation with minimal YAML example, options table, cost estimate, log paths, exit codes
- `README.md`: Added "Integration tests" subsection under Development; removed "Live AWS integration tests" from Things to Do

#### Code review fixes (8 findings: 1 CRITICAL, 3 HIGH, 2 MEDIUM, 2 LOW)

- `make_pcluster.py` CRITICAL: `_validate_queue_sizes` call moved to after `scaledown_idletime` is resolved (was calling with undefined variable at line 738)
- `make_pcluster.py` HIGH: `--ansible_verbosity` restricted to `choices=["-v","-vv","-vvv","-vvvv",""]` (argument-injection path closed)
- `src/pcluster_aux_data.py` HIGH: `ctrlC_Abort` replaces `delete_role_policy` (inline-policy API) with `detach_role_policy` + `delete_policy` for managed policies; `aws_account_id` guard prevents ARN construction with None
- `src/pcluster_core.py` HIGH: `_setup_iam` idempotency — `_role_existed` flag; missing-policy branch calls `_delete_managed_policies(suppress=True)` before recreating; `create_role` guarded by `if not _role_existed:`
- `src/pcluster_core.py` MEDIUM: `_render_policy` raises `ValueError` instead of `sys.exit()` so caller's `except Exception` catches it
- `src/pcluster_core.py` MEDIUM: `_validate_ebs_shared_dir` regex `r"/[^...]*"` → `r"/[^...]+"` (rejects bare `/`)
- `src/pcluster_core.py` LOW: `_delete_managed_policies` splits detach and delete into two independent try blocks — delete still runs even if detach fails
- `src/pcluster_aux_data.py` LOW: `ctrlC_Abort` managed-policy loop split into independent detach + delete try blocks (matching `_delete_managed_policies`)

#### Ceiling fixes (4 items)

- `src/pcluster_core.py`: `_cleanup_iam_on_failure(iam, ec2_iam_role, ec2_iam_policy, aws_account_id, enable_monitoring=False)` extracted as named helper — calls `_delete_managed_policies(suppress=True)` then `iam.delete_role` with `contextlib.suppress`
- `make_pcluster.py`: IAM exception handler simplified to single `_cleanup_iam_on_failure(...)` call

#### Secrets Manager SSH key integration

- `rotate_cluster_key.py` (NEW, repo root): rotates SSH keypair without cluster rebuild — resolves live head IP from EC2 API, generates new ED25519 key locally, appends public key to head node `authorized_keys`, imports new EC2 keypair, updates Secrets Manager secret, overwrites local `.pem`, deletes old EC2 keypair; `--dry_run` flag; Turbot profile auto-detected from vars file
- `templates/retrieve_ssh_key.j2` (NEW): per-cluster shell script rendered into `active_clusters/{cluster_name}/`; calls `aws secretsmanager get-secret-value` and writes key to canonical path with `0600` permissions
- `src/pcluster_core.py`: `_ssh_secret_name(cluster_name, serial)` pure helper — returns `parallelcluster/{cluster_name}/{serial}/ssh-private-key`
- `make_pcluster.py`: imports `_ssh_secret_name`; `ssh_secret_name` added to `cluster_parameters`; build summary prints secret name + rotate command
- `templates/vars_file.j2`: `ssh_secret_name` added to metadata block
- `templates/access_cluster.j2`: resolves head IP live from EC2 API (no stale baked-in IP); auto-retrieves key from Secrets Manager if local `.pem` missing
- `src/create_pcluster.yml`: stores key in Secrets Manager after writing PEM; renders retrieve script to stage_dir; rescue block deletes secret on early failure
- `src/delete_pcluster.yml`: deletes Secrets Manager secret on teardown (`--force-delete-without-recovery`)
- `tests/conftest.py`: `ssh_secret_name` added to fixture
- `tests/test_pcluster_core_iam.py`: `_ssh_secret_name` imported; `TestSshSecretName` with 2 tests
- **IAM note**: `secretsmanager:GetSecretValue/CreateSecret/PutSecretValue/DeleteSecret` and `ec2:ImportKeyPair` are operator-level permissions — not in any head node policy; documented in both new scripts

#### Turbot profile auto-detection in kill_pcluster.py

- `templates/vars_file.j2`: `turbot_account: "{{ turbot_account }}"` added to metadata block
- `make_pcluster.py`: `"turbot_account": turbot_account` added to `cluster_parameters` so it is rendered into the vars file at cluster creation
- `src/pcluster_core.py`: new `_read_turbot_from_vars_file(path)` — `yaml.safe_load` with full fallback to `"disabled"` on any error, missing file, empty file, or absent key
- `kill_pcluster.py`: after resolving `turbot_account`, if still `"disabled"`, probes the cluster vars file via `_read_turbot_from_vars_file` and applies the saved profile automatically; prints a note when auto-detected
- `tests/conftest.py`: `"turbot_account": "disabled"` added to fixture (required by template render)
- `tests/test_kill_access.py`: `TestReadTurbotFromVarsFile` with 5 tests (present, disabled value, absent key, missing file, empty file)

#### Dynamic EFA instance lookup

- `src/pcluster_core.py`: new `_get_efa_instance_types(ec2client, fallback)` — paginates `describe_instance_types` with `network-info.efa-supported=true` filter; falls back to static list on any error or empty result, printing a note
- `make_pcluster.py`: EFA check calls `_get_efa_instance_types(ec2client, ec2_instances_efa)` at runtime; static import kept as offline fallback
- `templates/ParallelClusterInstancePolicy-A.json_src`: `ec2:DescribeInstanceTypes` added to `EC2Describe` statement; Policy-A now 6,107 bytes (23 actions, still under 6,144-byte limit)
- `pcluster_defaults.yml`: `monitoring_version_checksum` placeholder replaced with real SHA-256 for v2.6 tarball (`4afa56a59228c1d8f4e405d07a2291f31853842128e6f7a0e52e1e2c1e262d55`)
- `tests/conftest.py`: `monitoring_version_checksum` fixture value updated to match

#### Code review fixes (7 findings: 2 CRITICAL, 3 HIGH, 2 MEDIUM)

- `make_pcluster.py:1031` CRITICAL: `ec2` → `ec2client` — bare `ec2` was undefined; broke every `--enable_efa true` run with `NameError`
- `rotate_cluster_key.py:181/188/215` CRITICAL: removed `base64.b64encode()` wrapper on all three `import_key_pair` calls — botocore's EC2 QuerySerializer re-encodes blob fields, so passing pre-encoded bytes caused double encoding and an invalid EC2 public key; fix: pass `new_pub_key.encode()` directly
- `rotate_cluster_key.py:82` HIGH: added `_validate_az_input(args.az)` call (now imported) after `_validate_cluster_name` — passing a region string produced `region = 'us-east-'` with no diagnostic; both peer scripts (`make_pcluster.py`, `kill_pcluster.py`) already called this
- `rotate_cluster_key.py:164` HIGH: replaced `echo '...' >> authorized_keys` shell string with `cat >> ~/.ssh/authorized_keys` over SSH stdin — a single-quote in the key comment field (e.g. `user@rod's-mac`) would break the remote shell command; fix uses `subprocess.run(..., input=(new_pub_key + "\n").encode(), ...)`
- `rotate_cluster_key.py:213` HIGH: wrapped final `import_key_pair` (rename step) in `try/except ClientError` handling `InvalidKeyPair.Duplicate` — if `delete_key_pair` at line 207 only warned (old keypair still exists), the rename threw an unhandled exception leaving the cluster with an orphaned `-rotated` keypair
- `rotate_cluster_key.py:200` MEDIUM: wrapped `open(ssh_keypair, "w")` in `try/except OSError` with recovery hint pointing to Secrets Manager — a write failure after `put_secret_value` previously silently destroyed the only remaining copy of the new key when the tmpdir was cleaned up
- `templates/retrieve_ssh_key.j2:17` MEDIUM: `OUT="$2"` → `OUT="${2:-}"` with explicit empty-guard error exit — bare `$2` under `set -euo pipefail` crashed with `unbound variable` when `--out` was passed without a path argument; both manual and `access_cluster.py` auto-recovery paths were affected
- `rotate_cluster_key.py`: removed now-unused `base64` and `json` imports

#### Venv rule

- `CLAUDE.md`: Always use `.venv/bin/python` for pytest; test command updated; both test-count references updated to 255
- `memory/feedback_venv.md` (NEW): persistent memory — always use `.venv/bin/python`, never system Python 3.14

#### Tests (250 → 269)

- `tests/test_pcluster_core_iam.py`: `_FakeIAM.create_role` raises `EntityAlreadyExists` when role exists; `_FakeIAM.delete_role` added; `test_oversized_policy_raises` updated to `pytest.raises(ValueError)`; `test_recreates_when_policy_missing` updated to assert role NOT re-created and old policies deleted; `test_resume_does_not_call_create_role` added; `test_render_failure_propagates_to_caller` added; `TestCleanupIamOnFailure` with 3 tests; `TestGetEfaInstanceTypes` with 4 tests; `TestSshSecretName` with 2 tests; `retrieve_ssh_key.j2` render picked up by parametrized template test
- `tests/test_aux_data.py`: `aws_account_id="123456789012"` added to both `ctrlC_Abort` test calls
- `tests/test_kill_access.py`: `TestReadTurbotFromVarsFile` with 5 tests

### Session 10 fixes (uncommitted)

#### MEDIUM fixes (15 findings)

**Input validation (5)**
- `make_pcluster.py`: `ansible --version` empty-stdout crash — guard against empty `splitlines()` and empty token list before indexing
- `make_pcluster.py`: `scaledown_idletime < 1` → sys.exit; `initial_queue_size < 0` → sys.exit
- `make_pcluster.py`: `fsx_size` negative-multiple edge case — check `<= 0` before modulo test
- `make_pcluster.py`: EBS lower-bound: reject `< 1 GiB` volumes; IOPS `< 100`; gp3 throughput `< 125`
- `make_pcluster.py`: `ebs_shared_dir` shell metacharacters — `re.fullmatch` rejects embedded quotes/newlines/shell special chars; removed duplicate `p_val` call before the new block

**Reliability/Ansible (4)**
- `src/delete_pcluster.yml`: DELETE_FAILED `fail:` → `set_fact` warning so all cleanup (keypair, IAM, SSM, S3) still runs; `fail:` reinserted as final task so play exits non-zero
- `src/delete_pcluster.yml`: `pcluster_delete_timeout` bumped 30 → 80 retries (40 min; covers EFS/FSx worst case)
- `src/create_pcluster.yml`: S3 bucket + keypair creation wrapped in `block/rescue`; rescue cleans S3, keypair, external-NFS SG before re-raising
- `make_pcluster.py`: S3 serial-number upload wrapped in `try/except`; prints warning instead of crashing

**IAM over-scoping (6, 4 fixed + 2 noted)**
- `Policy-A`: `autoscaling:DescribeScalingActivities` merged into `AutoScalingDescribe` (all four describe actions now share one `Resource: "*"` statement with `aws:RequestedRegion` condition)
- `Policy-A`: `EC2Modify` given `aws:RequestedRegion` condition — prevents cross-region SG/EIP/ENI creation
- `Policy-B`: `elasticfilesystem:CreateFileSystem` and `fsx:CreateFileSystem` split into `EFSFSxCreate` statement with `aws:RequestTag/parallelcluster:cluster-name` condition
- `make_pcluster.py` (`_render_policy`): runtime size check — exits with clear message before IAM rejects an oversized policy
- `Policy-A S3Objects` write scope `parallelcluster-*/*` — intentional per CLAUDE.md (PCluster internal system bucket); no change
- `Policy-C SSM CreateDataChannel/OpenDataChannel` — accepted; required by PCluster's node-agent SSM channel

#### LOW fixes (3 findings)

- `LustreS3HydrationPolicy.json_src`: removed `s3:PutBucketTagging` — head node never needs to overwrite bucket-level tags
- `Policy-A`: `AutoScalingDescribe` region-wildcard — merged `DescribeScalingActivities` in and added `aws:RequestedRegion` condition to both describe statements; Policy-A now 6,015 bytes (22 statements)
- `Policy-B IAMListGlobal` enumeration — documented as intentional PCluster daemon requirement in CLAUDE.md; narrowing breaks head node startup

### Session 9 fixes (uncommitted)

#### CRITICAL fix
- `templates/ParallelClusterInstancePolicy-B.json_src`: `IAMRolePolicy` statement now has `Condition: { StringEquals: { iam:PermissionsBoundary: "..." } }` — same boundary condition as `IAMCreateRole`; closes privilege-escalation path via `iam:PutRolePolicy`

#### HIGH fixes
- `make_pcluster.py`, `kill_pcluster.py`: `--ansible_verbosity` restricted to `choices=["-v","-vv","-vvv","-vvvv",""]` — closes argument-injection path
- `src/pcluster_aux_data.py` (`ctrlC_Abort`): replaced `iam.delete_role_policy` (inline-policy API) with `iam.detach_role_policy` + `iam.delete_policy` for `-A/-B/-C/-M` managed policies; FSx hydration inline policy still uses `delete_role_policy` correctly; added `enable_monitoring` and `aws_account_id` params
- `make_pcluster.py` (`ctrlC_Abort` call site): passes `enable_monitoring=enable_monitoring, aws_account_id=aws_account_id`
- `src/create_pcluster.yml`: added unconditional `set_fact: _perf_dirs: []` before the conditional build task — prevents `AnsibleUndefinedVariable` crash when `loop:` evaluates before `when:`
- `src/create_pcluster.yml`: added `failed_when: false` and `rc == 0` guard to `until:` cluster-wait loop — transient `pcluster` CLI errors no longer abort the wait
- `make_pcluster.py`: `_setup_iam()` call wrapped in `try/except` with `_delete_managed_policies` cleanup on exception
- `make_pcluster.py` (`_setup_iam`): idempotency check now calls `list_attached_role_policies` and only returns early if all expected policies are attached
- `make_pcluster.py`: `monitoring_version` validated against `^v[0-9]+\.[0-9]+(\.[0-9]+)?$` after resolution — closes shell-injection path in `monitoring-post-install-wrapper.j2`

#### Test-coverage gaps closed (190 tests, up from 182)
- `tests/test_templates.py`: content assertions added for monitoring (`enable_monitoring: "true"`, `monitoring_version:`) and custom-AMI (`ami-...`, `PlacementGroup:`) variants; `test_template_dirs_all_exist` added
- `tests/test_make_pcluster.py`: `test_trailing_hyphen_raises`, `test_consecutive_hyphens_raises` added to `TestValidateClusterOwner`
- `tests/test_make_pcluster.py`: `test_empty_serial_file_raises_systemexit` added to `TestLoadOrCreateSerial`
- `tests/test_resolve_defaults.py`: `test_cast_valueerror_raises_systemexit`, `test_cast_typeerror_raises_systemexit` added to `TestResolve`
- `tests/test_resolve_defaults.py`: `test_absent_everywhere_raises_systemexit` added to `TestResolveBool`
- `tests/test_resolve_defaults.py`: `test_invalid_yaml_raises_systemexit` added to `TestLoadDefaultsFile`
- `tests/test_aux_data.py`: `_FakeIAM` updated with `detach_role_policy`/`delete_policy` methods; assertions updated to verify managed-policy deletion

### Session 8 fixes (uncommitted)

#### Ansible playbook security review

- `src/create_pcluster.yml`: `get_url` for monitoring tarball now includes `checksum: "{{ monitoring_version_checksum }}"` — prevents tampered or re-tagged GitHub releases from propagating to cluster nodes
- `src/create_pcluster.yml`: symlink task (`ln -sf`) now registers result and emits a `debug` warning naming the script if symlink creation fails — previously `ignore_errors: true` gave no signal
- `src/delete_pcluster.yml`: performance S3 sync block converted from `ignore_errors: true` to `block/rescue` — rescue emits an explicit warning if head node is unreachable rather than silently swallowing the failure
- `make_pcluster.py`: `--monitoring_version_checksum` CLI arg added; `monitoring_version_checksum` default `sha256:REPLACE_WITH_ACTUAL_SHA256`; threaded through `_resolve`, `cluster_parameters` dict
- `templates/vars_file.j2`: `monitoring_version_checksum` added inside `{% if enable_monitoring == 'true' %}` block
- `pcluster_defaults.yml`: `monitoring_version_checksum: "sha256:REPLACE_WITH_ACTUAL_SHA256"` added with instruction comment under `# --- Monitoring ---`
- `tests/conftest.py`: `monitoring_version_checksum` added to default fixture

No regressions: 182/182 tests passing. ansible-lint 0 failures, production profile.

## Key file locations

| File | Purpose |
|---|---|
| `src/pcluster_core.py` | Pure testable functions; `_resolve`, `_resolve_bool`, `_validate_*`, `_load_or_create_serial` |
| `src/pcluster_aux_data.py` | Data tables (`base_os_efa`, `ec2_instances_efa`), helper functions |
| `templates/vars_file.j2` | Rendered with StrictUndefined — every variable must be defined |
| `templates/ParallelClusterInstancePolicy-A.json_src` | IAM policy part A (22 statements, 23 actions in EC2Describe) — 6,107 bytes minified; covers `parallelcluster-*` S3 objects |
| `templates/ParallelClusterInstancePolicy-B.json_src` | IAM policy part B (18 statements) — 6,064 bytes minified; covers S3 buckets, Lambda, Logs, IAM, EFS, FSx |
| `templates/ParallelClusterInstancePolicy-C.json_src` | IAM policy part C (1 statement) — ≤561 bytes minified; SSM/ec2messages access |
| `templates/ParallelClusterInstancePolicy-M.json_src` | IAM policy part M (7 statements) — ~1,390 bytes minified; monitoring permissions; only created when `enable_monitoring=true` |
| `templates/monitoring-post-install-wrapper.j2` | Head/compute node monitoring installer wrapper; pulls from S3, not GitHub; stops apache2 before Docker stack |
| `templates/grafana_tunnel.j2` | Per-cluster SSH tunnel script; backgrounds itself with `-fN`, resolves IP live, supports `stop` subcommand |
| `templates/access_cluster.j2` | SSH access wrapper; falls back to PrivateIpAddress when PublicIpAddress is absent |
| `templates/LustreS3HydrationPolicy.json_src` | FSx Lustre S3 hydration IAM policy |
| `tests/conftest.py` | Shared fixtures; `cluster_params`, `cluster_params_custom_ami`, `cluster_params_monitoring_enabled` |
| `tests/test_templates.py` | Renders all Jinja2 templates with all three fixture variants; IAM policy JSON validity + size tests |
| `tests/test_make_pcluster.py` | Main test file |
| `.yamllint` | yamllint config (line-length 220, braces enforced) |

## Known remaining gaps (intentionally deferred)

- `sbatch_default_submission_script.sh` contains Jinja2 directives — cannot be shellchecked
- `sbatch_Axb_random_template.j2` double-hash `##` comments: intentional Slurm convention
- `monitoring_version_checksum` is set for v2.6; must be updated if `monitoring_version` is bumped (run: `curl -sL <tarball-url> | sha256sum`)
- Ansible Molecule test suite: explicitly deferred — these playbooks make 20+ distinct AWS API calls with real state dependencies; Molecule's delegated driver cannot mock them without replicating more complexity than the playbooks themselves; pytest + ansible-lint + integration test already cover the meaningful surface area

### Session 12 fixes (committed)

#### Grafana / monitoring

- `templates/grafana_tunnel.j2` (NEW): per-cluster SSH tunnel script — backgrounds itself with `-fN`; resolves head IP from EC2 API with PrivateIpAddress fallback; auto-retrieves key from Secrets Manager if local `.pem` missing; prints URL + SSM password retrieval command; `stop` subcommand kills the tunnel via PID file at `/tmp/grafana-tunnel-<cluster>.pid`; optional local port argument (default 8443)
- `templates/monitoring-post-install-wrapper.j2`: stop and disable `apache2` before Docker Compose starts — PCluster Ubuntu head nodes ship with apache2 holding port 80, causing the monitoring nginx container to restart-loop
- `templates/access_cluster.j2`: PrivateIpAddress fallback when PublicIpAddress is None — enables SSH to private-subnet clusters with no public IP
- `templates/vars_file.j2`: `grafana_tunnel_src`/`grafana_tunnel_dest` added inside `{% if enable_monitoring == 'true' %}` block
- `src/create_pcluster.yml`: new task renders `grafana_tunnel.j2` to `stage_dir` inside the monitoring block
- `make_pcluster.py`: build summary now shows tunnel script path + localhost URL instead of raw head IP for Grafana
- `tests/conftest.py`: `grafana_tunnel_src`/`grafana_tunnel_dest` added to default fixture; 263 tests pass (up from 260)
- `README.md`: Grafana access section rewritten around tunnel script; Apache2 port conflict noted

#### Security group

- Port 443 inbound from `10.2.0.0/16` added to osiris head node SG (`sg-053903a56957e82a6`) — required for direct browser access when on VPN; tunnel script is the preferred access method for private-subnet clusters

## TODO

No outstanding technical debt items.
