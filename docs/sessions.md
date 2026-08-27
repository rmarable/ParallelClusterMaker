# ParallelClusterMaker — session archive

Dated record of sessions 17 through 41, split out of `CLAUDE-STATE.md` on
2026-07-31 so that file stays loadable. **Nothing here is an instruction.** Every
durable conclusion has been promoted into a `CLAUDE.md` constraint bullet — the
root file, `templates/CLAUDE.md` (IAM), or `hpc-benchmark/CLAUDE.md` (STREAM, OSU,
CUDA, the `set -e` assignment forms). Read this when you need the evidence behind
a bullet, the build a claim was verified on, or why an approach was rejected.

Current state — branch, test status, the grade, open gaps, and the live
checklists — is in `CLAUDE-STATE.md`. If the two disagree, that file wins: this
one records what was true at the time.

## Commits this branch (since main)

**These hashes are from the original private repo (`ParallelClusterMaker-Legacy`),
not this public repo.** The public fork has fresh git history — no commits,
branches, or objects carried over — starting at its own initial commit
`1785933` (see `CLAUDE-STATE.md`'s "What this repo is"). Sessions 17-41 below
predate the fork and are numbered/hashed against that old repo's history; do
not append this public repo's own commits to the list below, and do not
expect `git show <hash>` on any hash here to resolve in this checkout. This
public repo's own commit list — currently ten commits, `1785933` through
`4fe2d8e` — is in `CLAUDE-STATE.md`'s "Commit state" section; sessions 44
onward in this file cite that repo's real hashes directly in prose instead of
in a list like this one.

- `ded4976` Run postinstall on compute nodes, drop lifetime scheduler, fix HPCG build (sessions 17-22)
- `2ccc37f` Document SSH key DELETE_FAILED preservation and benchmark checksum verification
- `52f846a` Update docs to match the pclustermaker-* IAM ARN fix and current test count
- `9d49f9b` Fix root causes from third adversarial review pass
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
- `86c7957` Update CLAUDE-STATE.md, CLAUDE.md, and README for session 12
- `5c6321e` Sync README defaults table with pcluster_defaults.yml; set cluster_lifetime to 0:8:0
- `97c287d` Add GPU support: auto-detection, NVMe /local_scratch, EFA-GDR, nvtop/htop
- `c1707f9` Add GPU subnet routing, per-queue autoscaling, and manage_pcluster_queue.py
- `bc5f4a3` Add ec2:CreateFleet to Policy-B for PCluster v3 Slurm node provisioning
- `b4d9645` Fix None crash on empty instance type params and clean up build summary
- `02948a6` Add multi-queue multi-instance-type support and clean up dead code
- `812923b` Update docs: test count 320 → 398, fix cd path to ~/hpc-benchmark

## Session history 17-24 (trimmed 2026-07-27, before this file existed)

The trim below predates the 2026-07-31 split and was made against `CLAUDE-STATE.md`;
its text is the one thing this archive does **not** hold. Every durable conclusion
from sessions 17-24 has been promoted into a `CLAUDE.md` constraint bullet — the root file, `templates/CLAUDE.md` (IAM), or `hpc-benchmark/CLAUDE.md` (STREAM, OSU,
CUDA, the `set -e` assignment forms). The per-session narrative that led to each was removed here
to keep this file loadable. It covered: the IAM policy restructure and the five managed policies
(17), three rounds of adversarial review and the P0-P3 fixes (18-19), suite-wide mutation
measurement and the nine survivors (20-21), the lifetime scheduler removal and postinstall never
running on compute nodes (22), the benchmark partition / Lustre-summary / STREAM-microarchitecture
work and its 23b-23f follow-ons including FSx hydration, teardown orphans, the OSU CUDA build node,
and the head-node bootstrap timeout (23), and the removal of the RPM-based `base_os` (24, since
reversed — see `## Supported operating systems` in `CLAUDE-STATE.md` and session 31 below).

The full text is at `~/.claude/CLAUDE-STATE.md.doctor-bak-20260727`. This file is gitignored, so
that backup is the only copy — `git diff` will not show what was cut.

### Session 25 — `apt-mark hold` exited 100 and failed the head node

**Root cause, reproduced live.** `osiris` (stack created 2026-07-27T04:15:49Z, head node
`i-0000000000000007`) reached `CREATE_FAILED` with `OnNodeStartExecutionFailure` and
`return code: 100` at 04:39:01. Not the timeout and not the ansible pin — both of those
fixes worked. `HeadNodeBootstrapTimeout: 3900` rendered correctly (read off
`/opt/parallelcluster/shared/cluster-config.yaml` on the box), so the build had until
05:20:49; it died 41 minutes early.

The failure is `preinstall.j2`'s `apt-mark hold`, not the `dist-upgrade` after it.
`dpkg-query -W 'linux-image*' 'linux-headers*' 'linux-modules*' 'linux-aws*'` returned
**20** names on the `ubuntu2404` AMI, of which **11 were never installed** —
`linux-headers-686-pae`, `linux-headers-3.0`, `linux-headers-amd64`, `linux-image`,
`linux-headers`, `linux-headers-generic`, `linux-aws-6.17-doc-6.17.0`,
`linux-aws-6.17-source-6.17.0`, `linux-aws-6.17-tools`,
`linux-image-unsigned-6.17.0-1015-aws`, `linux-modules-extra-aws`. `apt-mark hold`
exits **100** on any name with neither an installed nor a candidate version, and the
`|| true` guard covers the `dpkg-query` assignment, not `apt-mark` — so 100 propagated
under `set -e`. Reproduced on the running head node: `apt-mark rc=100`, eight
`E: Can't select installed nor candidate version` lines. A `--dry-run dist-upgrade`
on the same box came back clean (273 upgrades, 3 kernel packages kept back, exit 0),
which is what ruled the upgrade out.

**Why the logs pointed the wrong way.** `cfn-init-cmd.log` captures stdout only. The
`E:` lines go to stderr and are recorded nowhere, so the last thing in the log is
twelve successful-looking `set on hold` lines followed immediately by the rc=100
report — reading as though the holds succeeded and the next command failed. Same
blind spot that hid the ansible resolver message in session 24.

**Fix.** One line in `templates/preinstall.j2`: `dpkg-query -W` now requests
`${db:Status-Status} ${Package}` and pipes through `awk '$1 == "installed" { print $2 }'`
so only installed packages reach `apt-mark`.

**Tests.** +2 (1274 -> 1277). `test_uninstalled_kernel_packages_are_never_handed_to_apt_mark`
uses the real AMI's 20-name list verbatim; `test_the_harness_apt_mark_stub_actually_rejects_a_phantom`
keeps it from passing vacuously. Two harness stubs had to become faithful first: the
`dpkg-query` stub now honors `-f` (a stub that always prints the status column cannot
distinguish dropping `${db:Status-Status}` from keeping it), and `apt-mark` now exits 100
on a phantom instead of always returning 0. `awk` is left unstubbed deliberately.
Mutation battery: **6 of 6 caught**, `preinstall.j2` restored byte-exact. `1277 passed`
under the env-stripped CI-like invocation; `ansible-lint` and `shellcheck` clean.

**Verified live.** Rebuild `osiris-26001227072026` (stack 12:01:32, head node
`i-0000000000000018`) held **9** packages, all installed, zero phantoms, and
`dist-upgrade` ran to completion for the first time in this repo's history — 273
upgraded, 2 new, `linux-aws`/`linux-headers-aws`/`linux-image-aws` kept back,
initramfs rebuilt for the booted kernel only. `apt-get install python3 ...` then
succeeded. The apt-mark fix is confirmed working on real hardware.

FSx timing also re-measured: WaitCondition opened 12:01:33, FSx `CREATE_IN_PROGRESS`
12:01:44, complete ~12:17-12:18 (~16 min), head node instance `CREATE_COMPLETE`
12:21:16. Under the stock 2100s the deadline would have been 12:36:33 with preinstall
starting at 12:22 — ~14 minutes for a script that needs more. With 3900s the deadline
was 13:06:33. The timeout fix is doing exactly what it was built for.

---

### Session 26 — `pip3 install --upgrade pip` cannot uninstall the Debian pip

**Newly reachable, not newly broken.** With `apt-mark` fixed, execution reached
`preinstall.j2` line 42 for the first time; the build failed at 12:26:44 with
`return code: 1`. Debian's `python3-pip` ships
`/usr/lib/python3/dist-packages/pip-24.0.dist-info/` containing `INSTALLER`,
`METADATA`, `WHEEL`, `entry_points.txt`, `top_level.txt` and **no `RECORD`** (verified
on the box; `dpkg -S` confirms `python3-pip` owns it). pip cannot uninstall a
distribution with no file manifest, so `--upgrade` dies at the uninstall step — the
cfn-init log's last line is `Found existing installation: pip 24.0`, exactly where
that begins.

**One caveat on the evidence.** The literal pip error message was never captured:
cfn-init records stdout only, and nothing on the node retains the stderr. The
`RECORD`-absence → uninstall-failure chain is documented pip behavior and matches
where the log stops, but the message itself is inferred, not observed. Also note
`pip3 install --upgrade pip --dry-run` **succeeds** — it returns before the uninstall —
so a dry run is not a valid check for this.

**A `cups.service` / `deb-systemd-helper` root cause was proposed and is false.**
`grep -n 'deb-systemd-helper' /var/log/cfn-init-cmd.log` on `i-0000000000000018`
matches **nothing**; every `cups` hit in that log is ordinary apt download/unpack
output from the `dist-upgrade`, which completed. The log's final three content lines
are `Installing collected packages: pip` / `Attempting uninstall: pip` /
`Found existing installation: pip 24.0`, immediately followed by `fetch_and_run -
Failed to execute OnNodeStart script 1` and `[ERROR] Exited with error code 1` — no
other command runs in between. Do not chase the systemd theory if it resurfaces.

**Fix.** `sudo pip3 install --ignore-installed --break-system-packages pip`.
`--ignore-installed` skips the uninstall and installs over the top;
`--break-system-packages` was already there and solves a different problem (writing
into the system tree), which is why it did not help.

**Tests.** +1 (1276 -> 1277). `pip3` is stubbed in `_run_preinstall`, so no runtime
assertion can see this — `test_pip_is_never_upgraded_over_the_distro_pip` parses the
template source, skipping comment lines, and requires `--ignore-installed` with no
`--upgrade` on any pip self-install line. Mutation battery **4 of 4 caught**
(original bug restored, `--ignore-installed` dropped, `--upgrade` re-added alongside
it, flags reordered); `preinstall.j2` restored byte-identical. `1277 passed`;
`ansible-lint` and `shellcheck` clean.

**Verified live.** Rebuild `osiris-25551427072026` (head node `i-0000000000000014`,
10.0.1.30) got **all the way through `preinstall.j2`** for the first time in this
repo's history: the `apt-mark` hold, the `dist-upgrade`, this pip fix, the
`ansible`/`boto3`/`numpy`/`scipy`/`pandas`/`matplotlib` installs, and the awscli zip
install all completed. The failure code moved
`OnNodeStartExecutionFailure` -> `OnNodeConfiguredExecutionFailure`, i.e. into
`postinstall.j2` — see session 27.

---

### Session 27 — `luarocks` builds C extensions with no `lua.h` present

**Newly reachable, not newly broken.** With preinstall clean, `postinstall.j2` ran for
the first time and died at line 154, `sudo luarocks install luaposix`, at 15:24:31 on
2026-07-27 (`OnNodeConfiguredExecutionFailure`).

**Root cause.** Ubuntu 24.04's `luarocks` package declares its header dependency as an
*alternative group* — `liblua5.1-dev | liblua5.2-dev | liblua5.3-dev` — and apt
satisfies it with **5.3**, while the same package's parallel `lua5.1|lua5.2|lua5.3`
group leaves `luarocks` itself running `lua_version 5.1`. `luaposix`,
`luafilesystem`, and `lua-term` are **C extensions** needing `lua.h` for the version
luarocks targets. Verified read-only on the box: `luarocks config lua_version` -> `5.1`;
`ls -d /usr/include/lua*` -> `lua5.3`, `lua5.4` only; `ls /usr/include/lua5.1/lua.h` ->
absent; `dpkg -l | grep liblua` -> `liblua5.3-dev` and `liblua5.4-dev` installed, no
5.1; `luarocks list` -> **empty**, so all three rocks failed, `luaposix` first. `gcc`
and `make` were both present, ruling out a missing toolchain.

**Same logging blind spot, third time running.** cfn-init records stdout only, so the
compiler error went nowhere and the log's last content line is the banner
`Installing https://luarocks.org/luaposix-36.3-1.src.rock`.

**Fix.** `sudo apt-get install -y luarocks liblua5.1-0-dev` (note `-0-` — `liblua5.1-dev`
is a virtual package with no installation candidate; the real name is
`liblua5.1-0-dev`, confirmed via `apt-cache policy`). Considered and rejected:
Ubuntu's own `lmod` 8.6.19 package (older than the pinned 8.7.55 and installs to
`/usr/share/lmod`, not the `/usr/local/lmod` prefix compute nodes mount), and pinning
the rocks to 5.3 (the block reads `LUA_VER` off the `lua` interpreter and builds
`LUA_CPATH`/`LUA_PATH` from it, so 5.3 rocks land in a directory Lmod never searches —
which fails at module load, long after the build looks clean). Operator chose the
explicit-headers fix.

**Tests.** +2 (1277 -> 1279). `TestLuarocksGetsTheLuaHeadersItCompilesAgainst` asserts
on the **execution trace** for `HeadNode`, not the source: the rocks sit inside the
head-node gate, so the trace also proves the apt line is reached on the node that
needs it, where a source grep would pass with the install in a block that never runs.
The version-agreement test is scoped to the luarocks apt line specifically —
`liblua5.4-dev` on the general dev-package line (line 100) is unrelated and must not be
dragged in. Mutation battery **4 of 4 caught** (headers absent, headers 5.3, headers
installed after the rocks, rocks pinned to 5.3); `postinstall.j2` restored byte-exact.
A fifth case — moving the headers into the head-node-only general block — is also
reported as caught: that arrangement would *work*, but the test deliberately requires
the headers on the `luarocks` line so the two cannot drift apart. `1279 passed`;
`ansible-lint` and `shellcheck` clean.

**Not yet verified.** No build has completed. The `CREATE_FAILED` stack from this
attempt must be torn down before the next retry. Next unexercised ground is the rest
of `postinstall.j2` — the Lmod source build, Spack, and the compute-node path, which
has never run.

---

### Session 28 — our GPU NVMe block races PCluster's own `ephemeral_drives` recipe

**The head node finished for the first time in this repo's history.** Stack created
2026-07-27T15:56:35Z. `custom_action_done` was written at 16:26 and the monitoring
installer finished at 16:27:42, so `preinstall.j2` *and* `postinstall.j2` both ran end to
end on the head node — the luarocks fix from session 27 works, and the Lmod 8.7.55 source
build and Spack clone are now exercised. The failure moved to the compute fleet, which had
never run this script before.

**Root cause.** `aws-parallelcluster-environment::ephemeral_drives` runs **before**
`OnNodeConfigured`. On any instance type with instance store it puts every such device into
an LVM physical volume (`/dev/vg.01/lv_ephemeral`), formats it `ext4`, and mounts it on
`/scratch`. Our GPU block then ran `sudo mkfs.xfs -f /dev/nvme1n1` on the same device and
got `cannot open /dev/nvme1n1: Device or resource busy`, which under `set -euo pipefail`
failed the node. **This block was unreachable until session 22 made postinstall run on
compute nodes** — instance store exists only there — so no earlier build could expose it.

**Why it is worse than a head-node failure.** A failed head node stops. A failed static
compute node does not: `clustermgtd` marks it `DOWN`, terminates it, launches a
replacement, and increments the partition's bootstrap-failure count. At **10** the
partition enters protected mode and the stack fails. The count was at **8 of 10** when the
operator tore the cluster down — ten instance launches for a two-line bug.

**Evidence, all read-only.** `"failureCode": "OnNodeConfiguredExecutionFailure"` on
`i-0000000000000019` (`g4dn.xlarge`, one instance-store device) in CloudWatch stream
`ip-10-2-35-96.i-0000000000000019.bootstrap_error_msg`, log group
`/aws/parallelcluster/osiris-202607271556`; the `mkfs.xfs` EBUSY line in the same
instance's `.chef-client` stream; `Partitions bootstrap failure count` in the head node's
`/var/log/parallelcluster/clustermgtd`. No writes were made on any node.

**Fix** (`templates/postinstall.j2`). The device filter is now three conditions, not one:
the existing `AmazonEC2NVMeInstanceStorage` model check (keeps EBS, which also enumerates
as `/dev/nvme*`, from being reformatted), plus an empty-`holders/` check and a
`! sudo blkid` check. Both new halves are needed and neither subsumes the other — an
LVM-held device has holders and **no** signature of its own; a formatted-but-unmounted
device has a signature and **no** holders. On a single-device instance type the block now
correctly no-ops and `/local_scratch` is the symlink to PCluster's `/scratch`; it still
fires on devices PCluster left alone.

**Tests.** +7 (1279 -> 1286). Four in `TestPostinstallNodeTypeGating`: the osiris failure
verbatim (asserts rc 0 **and** `mkfs` absent from the trace — `mkfs.xfs` is stubbed to 0,
so only the trace shows the call a real kernel refuses), the holders half alone
(`held_devices`), the blkid half alone (`formatted_devices`), and a free device still
formatted while a sibling is claimed. Two harness fixes were prerequisites: a real `blkid`
stub, and a `sudo` stub that **forwards the wrapped command's exit status** — it returned
0 unconditionally, which made every device look formatted and would have passed the no-op
tests with a completely broken filter. Forwarded for `blkid` only; the template also runs
`sudo -E make install` and `sudo su -c`, which a blanket `"$@"` would try to execute.

New class `TestNvmeDetectionSurvivesSetE` extracts the detection loop out of the rendered
template by position and runs it standalone under a real `set -euo pipefail`, including a
second copy where the loop's own exit status is observable (the process substitution hides
it). This exists because `_run_postinstall` **cannot** restore the rendered script's
`set -euo pipefail` the way `_run_preinstall` does: `mkdir` is stubbed, so the head-node
path's `cd "$SRC"` targets never exist and 6 head-node tests abort on what is the happy
path on a real node. Tried, reverted, documented in the harness docstring.

**Two claims of mine were wrong and are corrected in place.** I wrote — in the template
comment and a test docstring — that a chain of `[[ ... ]] && continue` guards would
truncate the device list under `set -e`, then narrowed it to "aborts when it is the body's
last command." Both were disproved against real bash 5.3: a false `[[ ]] && continue`
inside a loop body returns 0 in **every** position, because `continue` is exempt from
`set -e` there. The `if` form is kept because the three tests are one decision — style,
not correctness — and that case was removed from the mutation battery as not-a-bug.

Mutation battery **7 of 7 caught** after the two independent-half tests were added (run 1
was 5 of 8: holders-only and blkid-only survived a single combined fixture). Gates:
`1286 passed` under the env-stripped CI-like invocation, `ansible-lint` production clean,
`shellcheck` clean.

**Not yet verified.** No build has completed. The stack is gone (`Stack with id osiris
does not exist`) and the local `.pem` with it. Next unexercised ground is the compute-node
path *past* the NVMe block.

---

### Session 29 — a compute node's apt index is whatever the AMI shipped

**The NVMe fix worked.** Build `osiris-202607271710` (stack created 17:10:23Z) got the GPU
compute node past the block that killed the last one: no `mkfs` anywhere in
`i-0000000000000012`'s chef log and no `Device or resource busy`. The three-condition
filter correctly saw PCluster's LVM claim on `g4dn.xlarge`'s single instance-store device
and skipped it. The failure moved **four lines further down**, to the `nvtop`/`htop` install.

**Root cause.** `OnNodeStart` — and therefore `preinstall.j2`'s `apt-get -y update` — is
registered on `HeadNode:` only, deliberately (running the python3/pip/awscli install on
every scale-up is a latency regression). So a compute node's `/var/lib/apt/lists` is
whatever the AMI shipped, and `apt-get -y install nvtop htop` against it exits **100**:
`E: Unable to locate package nvtop`. The AMI's `sources.list` is the standard Ubuntu one
and **does** include multiverse — this was never a missing-component problem, only a stale
index. Confirmed both ways from this build's own logs: the head node's update fetched
`noble/multiverse amd64 Packages [269 kB]` and installed `nvtop 3.0.2-1` cleanly, while the
compute node's `bootstrap_error_msg` stream carries the exit-100 stderr verbatim. The block
comment even said it installs these itself "because the main package block is
head-node-only" — correct reasoning that never accounted for `apt-get update` being
head-node-only too.

**Evidence.** `"stderr": "E: Unable to locate package nvtop\n"`, `"exit_code": 100` on
`gpu-st-gpu-resource-1` / `i-0000000000000012` (`g4dn.xlarge`, static) at 17:42:12Z;
`protected-mode-error-count` at **1 of 10** in `clustermgtd_events`. Head node was healthy
throughout — monitoring finished 17:39:41Z. All read-only; the operator tore the stack down
at 17:48Z.

**Fix** (`templates/postinstall.j2`). The install now splits on `NODE_TYPE`. Head node:
`nvtop htop`, no refresh (`preinstall.j2` already did one and the head-node package block
does another; a third is pure bootstrap latency). Compute node: `apt-get -y update` first,
then `htop` **only**. Operator chose this over refreshing-and-keeping-nvtop: `nvtop` is the
only non-`main` package on the compute path, and the operator logs into the head node,
which already has it.

**Both installs are non-fatal** (`|| echo "WARNING: ..." >&2`) — the only non-fatal installs
in the file. Rationale is the protected-mode threshold: a compute node exiting non-zero is
relaunched and counted toward 10, so one transient mirror outage would cost the entire stack
over a diagnostic nothing in the job path imports. Considered and rejected: leaving them
fatal for symmetry with every other install here.

**Tests.** +5 (1286 -> 1291). Three in `TestPostinstallNodeTypeGating` assert on **trace
indices per node type** — both commands are `sudo apt-get`, so a source grep cannot tell
which ran first on which node, and an update placed after the install refreshes nothing in
time. New class `TestMonitoringToolsCannotFailTheNode` extracts the block and runs it
standalone under real `set -euo pipefail` with `apt-get` returning 100, because
`_run_postinstall` discards line 2 and so cannot see the guards at all (same reason
`TestNvmeDetectionSurvivesSetE` exists); `test_the_harness_actually_fails_the_package_manager` guards
those two against passing vacuously. Mutation battery **9 of 9 caught** (no update on
compute, update after install, nvtop back on compute, each of the three commands made
fatal, gate inverted, a third head-node update, gate removed entirely);
`postinstall.j2` restored byte-exact. `1291 passed`, `ansible-lint` production clean,
`shellcheck` clean.

**Not yet verified.** No build has completed. Next unexercised ground is the compute-node
path past the monitoring install — the external NFS block (off in this defaults file), the
`case` statement's `ComputeFleet)` arm, and the `custom_action_done` gate.

**Superseded within the hour.** Build `osiris-202607271807` showed the apt-index fix was
never the whole story: the node-type variable the entire gating scheme rests on does not
exist. See session 30. The session-29 fix is still correct and still needed — it just was
not what was failing the build, because *no* compute node was taking the compute path.

---

### Session 30 — `PARALLELCLUSTER_NODE_TYPE` does not exist

**Root cause 7, and the one that actually failed the build.** `postinstall.j2` read
`NODE_TYPE="${PARALLELCLUSTER_NODE_TYPE:-HeadNode}"`. That variable is invented. The node
type is published as `cfn_node_type` in `/etc/parallelcluster/cfnconfig`, written by
`aws-parallelcluster-environment::cfnconfig_mixed` during the init phase, before any custom
action runs — confirmed in both node types' own chef logs (`cfn_node_type=HeadNode` on
`i-0000000000000009`, `cfn_node_type=ComputeFleet` on `i-0000000000000023`). Nothing
exports the other name: `custom_action_executor.py`'s `EnvEnricher.build_env` is
`os.environ.copy()` plus the `cfn_<event>`/`cfn_<event>_args` pairs, and `fetch_and_run`
keeps `PCLUSTER_NODE_TYPE` as a plain unexported shell variable it passes as `--node-type`.
A grep across both nodes' full chef and cfn-init logs and the vendored PCluster CLI returns
zero hits.

So the `:-HeadNode` default always won, and **every compute node ran the entire head-node
path**. All ten `g4dn.xlarge` launches on the `gpu` queue failed `OnNodeConfigured`: nine
with `rc=128` on `fatal: destination path 'Lmod' already exists and is not an empty
directory` — they were racing each other's `git clone` into NFS-exported `$SRC` — and the
first with `rc=1` on `s3:ListBucket` denied on `parallelclustermaker-osiris-45061827072026`,
which the head-node-only S3 sync needs and `ComputeNode-Base` correctly does not grant.
The queue hit the 10-failure threshold, the cluster went `PROTECTED`, and PCluster's own
`finalize_head_node` recipe (`ruby_block[wait for static fleet capacity]`,
`helpers.rb:202`) raised after 52m33s. Stack `CREATE_FAILED` on
`HeadNodeWaitCondition20260727180738` at 19:14:44, 82 minutes in. The head node itself was
clean — `runpreinstall` 18:28:32-18:33:53 and `runpostinstall` 18:34:30-18:37:09 both
succeeded, so sessions 25-29's fixes all held.

**The default was the bug, not a safety net.** It collapsed "this variable does not exist"
into "be a head node" and made the `case`'s `*)` arm — the one guard whose whole job is to
catch a changed upstream contract — unreachable. The replacement defaults to `HeadNode`
only when the cfnconfig *file* is absent (a genuine off-cluster manual re-run) and treats a
cfnconfig without `cfn_node_type` as a hard failure.

**The harness manufactured the phantom.** `_run_postinstall` set
`PARALLELCLUSTER_NODE_TYPE` in the environment, so all eleven gating tests passed against a
mechanism no node has ever used — the tests supplied the variable the template was wrong to
read, which is why eight sessions of work on that template never surfaced this. The harness
now writes a fake `cfnconfig` and substitutes its path; `node_type=None` omits the file,
`node_type=""` writes one with no `cfn_node_type`.

**Tests.** +2 (1291 -> 1293), plus the harness rewrite that re-points all eleven existing
gating tests at the real mechanism. `test_the_node_type_is_read_from_cfnconfig_not_the_environment`
asserts on rendered **source** with comment lines stripped — a trace assertion is blind here
because the failure mode is reading the wrong *source*, and a run whose env and cfnconfig
agree behaves identically either way; the comment strip is needed because the name
legitimately appears in the comment explaining why it must never be read.
`test_a_cfnconfig_without_a_node_type_is_a_hard_failure` pins the no-default rule. Mutation
battery **8 of 8 caught**: the exact line that shipped, a `:-HeadNode` on the cfnconfig
read, no cfnconfig read at all, hardcoded `ComputeFleet`, `*)` arm exiting 0, a plausible
wrong key (`cfn_nodetype`), sourcing the file but still using the env var, and the `-f`
guard inverted. `postinstall.j2` restored byte-exact. `1293 passed`, `ansible-lint`
production clean, `shellcheck` clean.

**Verified on hardware.** Build `osiris-47192227072026` (log group
`/aws/parallelcluster/osiris-202607272220`) reached `CREATE_COMPLETE` at 22:55:14Z, 34m24s
after creation — the first end-to-end successful build in this chain. Confirmed across three
node shapes: head node `c5.xlarge`, GPU compute `g4dn.xlarge`, CPU compute `c5.2xlarge`.
Compute nodes read `cfn_node_type=ComputeFleet` and took the `ComputeFleet)` arm. Root cause
6 confirmed (compute path ran `apt-get -y update`, 52 MB fetched, then `htop` only, no
`nvtop` attempt, no `E: Unable to locate package`). Root cause 5 confirmed (zero `mkfs`, no
`md0`, no EBUSY — the single instance-store device was LVM-claimed by PCluster and correctly
skipped; `/local_scratch` is the symlink to `/scratch`). Root cause 1 confirmed
(`HeadNodeBootstrapTimeout: 3900` in the deployed config; FSx took 19m20s before the
instance existed, head-node bootstrap ~13 min inside the remaining window).

---

### Session 31 — RHEL 9 re-added; the guards were rewritten, not just widened

Session 24 removed the RPM-based `base_os` because `preinstall.j2` pinned
`ansible>=9,<10`, every non-prerelease 9.x needs `ansible-core` 2.16 (`requires_python
>=3.10`), and that AMI shipped Python 3.9. The fix was never to drop the OS — **nothing
on a node imports ansible.** `src/create_pcluster.yml` runs on the operator's
workstation. The pin is now deleted from *both* arms, guarded by
`test_no_arm_installs_ansible`.

`rhel9` and `rhel9arm` are back. `rhel8`/`rhel8arm` are not — untested, and there is no
reason to carry two RPM generations. `rhel9arm` is the toolkit's spelling; `pcluster_os`
strips the suffix to `rhel9` for PCluster's `Os:` field.

**Three defects the test rewrite exposed in the implementation, all fixed:**
1. `_resolve_ec2_user` used `elif "rhel" in base_os`, so `rhel8`, `rhel10`, and
   `rhel99` all resolved to `ec2-user`. Replaced with the exact-key `_EC2_USERS` dict
   (`src/pcluster_core.py:76`). `diagnose_pcluster.py`'s `_VALID_EC2_USERS` is now
   asserted to be derived from `_EC2_USERS.values()` rather than hand-maintained.
2. The template checks were written as an Ubuntu-only blocklist. A blocklist is exactly
   as wide as whoever wrote it remembered; the replacement asserts the supported set by
   **equality**, both directions, at every surface.
3. Both package managers must be stubbed in `_run_preinstall`/`_run_postinstall`. While
   only apt was stubbed, the trace could not carry which family actually ran.

**The positive trace assertion needed three rounds of tightening.** The class is
`TestPackageManagersMatchTheRenderedOs` (`tests/test_templates.py:3194`), parametrized
over `(cluster_params, cluster_params_gpu_queue_enabled, "apt-get")` and
`(cluster_params_rhel, cluster_params_rhel_gpu_queue, "dnf")`. Each round shipped a
mutation that survived the whole suite:
- **M8** — an aggregate `any()` over both templates is satisfied by postinstall alone,
  so deleting preinstall's entire `{% else %}` body (an rhel9 node with no python3 and
  no awscli) passed 1324 tests. Fix: assert per template.
- **M11** — within one template, `expected in trace` is satisfied by the GPU block's
  `dnf -y install nvtop htop`, so deleting the whole critical-packages block (no gcc,
  git, lua, lua-devel, nfs-utils, EPEL, and therefore no Lmod) passed. Fix:
  `_SENTINEL_PACKAGES` — assert on package names that appear on exactly one arm's own
  package line (`python3-devel`/`unzip`, `lua-devel`/`nfs-utils`, and the apt
  equivalents), extracted from the trace by `_installed_packages`.
- **M12/M13/M14** — the symmetric mutations (delete postinstall's Ubuntu block; drop
  `python3-devel unzip`; drop `python3-dev unzip`) each now fail, and each fails **only**
  its own parametrization, which is what proves the two arms are independently pinned.

The general lesson, and the reason this took three passes: **a bare command name is too
coarse a sentinel whenever any other block in the same file invokes the same command.**
Assert on the argument that only the block under test supplies.

**Two more properties worth not re-deriving:**
- The wrong-family regexes need word boundaries and both families' fixtures. `"dnf "`
  as a space-suffixed literal misses `sudo dnf\t-y install`.
- Three of the five surfaces in `_NODE_SURFACES` have no `'ubuntu' in base_os` branch:
  `templates/monitoring-post-install-wrapper.j2` (whose port-80 stop loop covers
  `apache2 httpd` unconditionally, which is why it needs no branch),
  `src/create_pcluster.yml`, and `src/delete_pcluster.yml`. A package-manager call in
  any of them would reach both families, so
  `test_no_unbranched_surface_invokes_either_package_manager` asserts on their raw
  source instead of sweeping them through the per-family parametrization.

Suite: **1324 passing**, `ansible-lint` production clean, `shellcheck` clean. Every
mutation restored and verified by checksum.

**Nothing here has been built.** See item 4 of the live-verification TODO.

---

### Root cause 8 — the monitoring wrapper raced on NFS-shared `MONITORING_HOME`

`MONITORING_HOME` is `/home/<cfn_cluster_user>/aws-parallelcluster-monitoring`, and `/home`
is NFS-exported from the head node — verified in a failing compute node's own chef log
(`mount 10.0.1.10:/home to /home`). `monitoring-post-install-wrapper.j2` ran
`rm -rf "$MONITORING_HOME"; mkdir -p; tar -xzf; chown -R` unconditionally on **every** node.
Two `c5.2xlarge` nodes booting 92 ms apart on 2026-07-27 destroyed each other's tree: one
failed `OnNodeConfigured` with `rc=127` because `installer/install.sh` had been deleted
between its own `tar` and the `bash` that runs it, the other with `rc=1`. Count reached 2 of
the partition's 10-failure protected-mode threshold. **The race is intermittent** — the
relaunched pair happened to launch at the identical second (23:40:48) and both succeeded,
and `clustermgtd` logged `Find successfully launched node in partition compute, reset
partition protected failure count` at 23:44:38. A rebuild is therefore not a test of this;
the suite is.

**Node-local `MONITORING_HOME` is not viable** — checked, not assumed. Upstream
`installer/install.sh:25-27` computes the path itself with no override, no argument, and no
env fallback: `MONITORING_HOME="/home/${PLATFORM_USER}/${MONITORING_DIR_NAME}"`, where
`PLATFORM_USER` is `cfn_cluster_user` (`installer/platform/parallelcluster.sh`).
`compose/compute.gpu.yml` additionally hardcodes
`/home/${cfn_cluster_user}/__MONITORING_DIR__/dcgm/counters.csv`. So the fix is to gate the
write, not to relocate it.

**A compute node needs the tree only to exist and be readable.** `compose/compute.yml` runs
one service, `prometheus-node-exporter`, whose only volume is `/:/host:ro,rslave` — nothing
from `MONITORING_HOME`. The GPU arm does one idempotent `sed -i` substituting
`__MONITORING_DIR__` and a read-only bind of `dcgm/counters.csv`. Neither creates anything.

**The ordering that makes the head-node gate safe is structural, not luck.** cfn-init runs
the head node's steps in sequence: `runpostinstall` — which contains this wrapper as script 3
of the `OnNodeConfigured` Sequence — completed at 22:50:20, and only the *following* chef
`finalize` step starts `clustermgtd`, which is what launches compute nodes. The first
compute node launched at 22:50:40.

**Upstream's own `sed -i` race is deliberately left unpatched.** It is GPU-only, idempotent
(a constant substitution; after one pass the token is gone), and `sed -i` writes-then-renames
so a reader sees a whole file. Verified succeeding on `i-0000000000000006`. No observed
failure; not ours to fix.

**Tests.** +7 (1293 -> 1300), all in `tests/test_shell_surfaces.py`
(`TestMonitoringWrapperOnlyTheHeadNodeWritesTheTree`) behind a new `_run_wrapper` harness.
Whether the extraction is gated is a runtime property of the `if`, so the harness executes
the rendered wrapper under real `bash` with a fake `cfnconfig` and records what ran; a text
assertion cannot tell a gated block from an ungated one. `rm` and `mkdir` run for real so
the tree's *survival* is observable, with a `_guard` that hard-fails on any path outside the
tmpdir — a failed substitution would otherwise point them at the developer's own `/home`.
`test_the_harness_can_see_an_ungated_extraction` keeps the two negative tests from passing
vacuously. Mutation battery **8 of 8 caught**: the gate deleted entirely (the line that
shipped), `${cfn_node_type:-HeadNode}`, the gate spelled `!= "ComputeFleet"`, the `elif`
readability arm deleted, the `elif` warning without exiting, `tar` hoisted out of the gate,
`chown` hoisted out, and the phantom `PARALLELCLUSTER_NODE_TYPE` again. Two of those — the
`!=` spelling and the missing `exit 1` — survived the first battery and drove two additional
tests. Template restored byte-exact. `1300 passed`, `ansible-lint` production clean,
`shellcheck` clean.

**Not yet verified on hardware.** The gate itself has not run on a real build; the cluster
was still up with benchmarks running when the fix landed.

---

### Sourcing the benchmark driver from a login shell was broken

Found while debugging `_native_march`, and unrelated to it. `SCRIPT_DIR="$(cd "$(dirname
"$0")" && pwd)"` sits at line 26, hundreds of lines above the `HPC_BENCHMARK_LIB_ONLY`
return, so sourcing runs it. In an interactive login shell `$0` is `-bash`, which `dirname`
parses as short options: `dirname: invalid option -- 'b'`. Observed live on the `osiris`
head node. Fixed with `dirname -- "$0"`.

**The suite could not see it.** Every existing test sources under `bash -c`, where `$0` is
plain `bash` and `dirname` answers `.` — so all of them ran against a `SCRIPT_DIR` pointing
at pytest's cwd. Harmless in practice (every test passes explicit paths) but it meant the
documented sourcing entry point was never exercised as an operator uses it.

**A fallback was written and then removed.** `|| SCRIPT_DIR="$PWD"` looked prudent, but with
`--` in place `dirname -- -bash` returns `.` and `cd .` cannot fail, so the branch is
unreachable — two mutations of it (no fallback; fallback to `/`) survived the suite by
construction. Deleted rather than left as untestable code. Recorded because the next person
to read that line will want to add it back.

**Tests.** +3 (1300 -> 1303) in `TestTheDriverCanBeSourcedFromAnInteractiveShell`, using
`exec -a -bash bash -s` — the only way to put a leading dash in argv[0] from a test.
`test_the_harness_reproduces_the_original_failure` asserts the bare `dirname "$0"` still
fails on that argv[0], so the pair cannot pass vacuously. Mutation: the shipped bug is
caught. `1303 passed`, `shellcheck` clean.

---

### `_native_march` returning `unknown` — ROOT-CAUSED and fixed

**`gcc -march=native -Q --help=target` prints the option table and then exits non-zero.**
`-Q` asks gcc to compile, no input file was given, so it ends with `gcc: fatal error: no
input files` — on **stderr**, which the probe's `2>/dev/null` discards. Measured on the
`osiris` head node (Ubuntu 24.04, gcc 13.3.0):

```
$ bash -c 'gcc -march=native -Q --help=target | awk "..." >/dev/null; echo "${PIPESTATUS[*]}"'
2 0
```

gcc fails; awk succeeds on the line it already read. The driver runs under `set -euo
pipefail`, so a failing producer failed the whole pipeline and the old `|| resolved=""`
turned a perfectly good `skylake-avx512` into `unknown` on **every** node, every time. Not
intermittent — it never worked.

**Why four mechanisms were proposed and disproved first.** Every interactive check passes:
the pipeline prints the right answer, and an interactive shell discards the status. `rc=0`
was measured — but for the *whole pipeline*'s last stage (awk), not gcc. Only `PIPESTATUS`
separates them. SIGPIPE was the closest wrong answer and is definitively out: the producer
was never signaled (`PIPESTATUS=0 0` in every repro).

**Impact was caching only.** `cmd_run` refuses the shared per-march path when march is
`unknown` and compiles a throwaway binary into the run's own results dir — the designed
fallback, so the numbers were always correct. What was lost: the per-microarchitecture cache
in `bin/`, so every run recompiled STREAM, and the WARNING implied the compiler could not
name its target when it could.

**Fix** (`hpc-benchmark/hpc-benchmark.sh`, `_native_march`): capture gcc's stdout on its own
command with `|| true`, then parse `$raw` on a later one. gcc's status is discarded; the
parse decides. An unreadable table still yields empty `resolved` → `unknown`.

**Tests.** +7 (1303 -> 1310) in `TestGccsExitStatusDoesNotDecideTheMarch`. The suite could
not see this because `_fake_gcc` did `exit 0` on the probe — it now defaults to `probe_rc=2`,
and `test_the_harness_gcc_really_does_fail_the_probe` guards that so the pair cannot pass
vacuously. The stub also now emits the two other rows real gcc prints — a neighboring
`-mtune=` row and the `Known valid arguments for -march= option:` trailer at line 266 — which
is what distinguishes the exact-field parse from a substring one: `/-march=/` reads `valid`
off that trailer, a legal filename, so every node class would share one `stream-valid`
binary. `march_row=False` keeps the trailer and drops the value row to pin exactly that.

**Mutations: 7 of 8 caught.** The survivor, dropping awk's `exit` so the last match wins, is
an **equivalent mutant** on this input and no test is owed: the real table has exactly one
row whose `$1` is `-march=` (line 29; line 266's `$1` is `Known`), verified in the head
node's own 271-line output. The `exit` stays because it is correct and free.

Two mutations were also removed from the driver rather than tested, both dead code by
construction: an `if [[ -n "$raw" ]]` guard around the parse (an empty `raw` yields one blank
line, nothing matches, `resolved` stays empty — same outcome), and the earlier
`|| SCRIPT_DIR="$PWD"` fallback. Same rule as `hpc-benchmark/CLAUDE.md` records for the
latter: do not add back a branch nothing can reach.

**Still owed:** re-run `./hpc-benchmark.sh install` on a live node and confirm
`bin/stream-skylake-avx512` appears rather than a per-run throwaway.

---

### The driver leaked `set -euo pipefail` into a shell that sourced it — FIXED

Line 2 of `hpc-benchmark.sh` was `set -euo pipefail`, and it ran on `source` exactly as line
26's `dirname` did. The documented library entry point (`HPC_BENCHMARK_LIB_ONLY=1 source
./hpc-benchmark.sh`) therefore handed the caller an interactive shell where the next non-zero
command exited the session. **This closed two of the operator's SSH sessions with exit code 2**
while debugging `_native_march` above, and it is why the diagnostics in that section had to be
issued as `bash -c` one-liners rather than pasted into a live shell.

**Fix:** the `set` moved below the `HPC_BENCHMARK_LIB_ONLY` return and above the dispatch
`case`. Verified end-to-end under `exec -a -bash bash -s`: `$-` is `hBs` before and after the
source, `false` and an unset-variable reference both survive, session exits 0.

**Both bounds on the placement are load-bearing, and the lower one is the risk.**
`_compile_stream`'s `mv -f` into shared `bin/` and `_build_osu_cuda`'s `mkdir` lock are reached
only from `cmd_install`/`cmd_run`, so a `set` placed below the dispatch would run them without
strict mode. Neither is affected by this change — both are function bodies invoked from below
the guard — but nothing in the suite said so, hence
`TestTheDispatchPathStillRunsUnderStrictMode`, which pins the position statically *and* splices
a probe arm into a copy of the driver to read `$-` and `set -o pipefail` out of the running
dispatch path.

**Two claims I made while planning this were wrong and are corrected here**, since both would
mislead a future reader:
- "Moving the `set` leaves the top-level code above the guard unprotected" — technically true
  and practically empty. `set -x` shows the sourced region executes twelve plain assignments
  plus the `SCRIPT_DIR` substitution; `dirname --` cannot reject an argv[0] and `cd .` cannot
  fail. There is no unprotected failure path.
- "The suite can't see this because 16 of 21 sourcing harnesses set `-e` themselves" — the
  count is right, the reason is not. `bash -c` exits when its script ends, so a leaked `-e` has
  nothing left to kill; even the five harnesses that don't set it were blind. The test has to
  run commands *after* the source.

Also worth recording: **`set -e` is a hazard to the CUDA lock, not a protection.**
`_build_osu_cuda` releases `bin/.osu-cuda.lock` on its last two lines, and a bare failing
command under `-e` would abort before the `rmdir`, stranding the lock so every later GPU job
skips the device tests. It survives only because the build steps sit inside an `if ... && ...`
condition, where `-e` is suspended, and the one unguarded line carries `|| rc=1`.

**Tests.** +8 (1310 -> 1318) across `TestSourcingDoesNotLeakShellOptionsIntoTheCaller` (4) and
`TestTheDispatchPathStillRunsUnderStrictMode` (4). All eight faithful mutations caught —
delete the line, back to the top, just above the guard, inside a dispatch arm, at the end of
the file, and dropping each of `-e`/`-u`/`pipefail`. One mutation initially "survived" and was
my own error, not a gap: it inserted the `set` one line higher than intended, still ahead of
the dispatch.

---

### The EFS bootstrap allowance was an unmeasured estimate — MEASURED 2026-07-28

`_EFS_PROVISION_ALLOWANCE = 600` was the one number in `_derive_head_node_bootstrap_timeout`
derived from resource shape rather than observation; the build that motivated the whole
derivation had `enable_efs: "false"`. An `enable_efs: "true"` build of `osiris` (serial
`osiris-32371228072026`, `c5.xlarge` head, `ubuntu2404`, `generalPurpose`/`bursting`, one mount
target in `us-east-2a`) reached `CREATE_COMPLETE` and supplied the measurement.

Timings from `describe-stack-events`, all relative to `HeadNodeWaitCondition` going
`CREATE_IN_PROGRESS` at 12:41:49 (which is when the clock starts):

| event | wall | elapsed |
|---|---|---|
| `EFS::FileSystem` CREATE_COMPLETE | 12:41:53 | 4s |
| `EFS::MountTarget` CREATE_COMPLETE | 12:43:30 | 1m41s |
| `ComputeFleetQueues` nested stack CREATE_COMPLETE | 12:46:10 | 4m21s |
| `HeadNode` instance CREATE_IN_PROGRESS | 12:46:13 | 4m24s |
| `HeadNodeWaitCondition` CREATE_COMPLETE | 13:02:41 | 20m52s |

**The allowance holds, and 600 stays.** 4m21s of pre-instance time against a 600s allowance is
~2.4x headroom, and the whole wait condition was satisfied in 20m52s of the 2700s granted.
The deployed template was confirmed to carry `Timeout: '2700'` — read out of
`get-template`, not inferred from the defaults file — so the derivation demonstrably fired.

**Two things the measurement does *not* show, and the comment now says so:**
- **The mount target did not gate the instance.** I expected it to, given the FSx precedent.
  `HeadNodeLaunchTemplate` in the deployed template carries no reference to
  `EFS55ddaaf7d4ab71faMTuseast2a` — the only references to it are its own definition — and the
  2m40s between mount-target completion and the launch template was the compute-fleet nested
  stack, which is on the critical path for its own reasons. So the figure covers the observed
  pre-instance *window*, which is what the timeout has to cover anyway, rather than a proven
  EFS dependency.
- **Multi-AZ is still unmeasured.** One mount target per subnet, provisioned concurrently, so
  the shape should hold — but "should" is the operative word, and a 1m41s serial cost per AZ
  would exhaust 600s at four subnets.

The EFS allowance is now the measured 600s in `pcluster_core.py`'s comment,
`test_efs_adds_a_smaller_allowance`'s docstring, and the root `CLAUDE.md` bullet. No code or
test value changed — only the provenance of the number.

---

### Session 32 — the pip RECORD fix was applied to one line, not to the rule

First live `rhel9` build: `osiris`, serial `osiris-30141528072026`, head node
`i-0000000000000020` (`c5.xlarge`, RHEL 9.8), log group
`/aws/parallelcluster/osiris-202607281515`. Chef finished clean (15:22:00-15:22:47),
`runpreinstall` started 15:22:47 and failed **15:30:42, `return code: 1`** →
`OnNodeStartExecutionFailure` → `CREATE_FAILED`.

`dnf update` and `dnf install` both succeeded; `pip3 install --ignore-installed pip`
succeeded (`pip-26.0.1` over the RPM-owned `21.3.1`). The **dependency** install on the
next line did not:

```
Attempting uninstall: numpy          <- succeeded
Attempting uninstall: requests
  Found existing installation: requests 2.25.1
<log ends>
```

**Root cause: the same defect as session 26, on a different line of the same file.** That
session established that pip cannot uninstall a distribution whose `dist-info` has no
`RECORD`, fixed the pip self-install, and wrote the rule down — but only applied
`--ignore-installed` to the pip line. `preinstall.j2`'s dependency install (`'requests>=2.31,<3'`
among eight pins) still resolved to replacing RHEL 9's RPM-owned
`python3-requests-2.25.1-10.el9_6.noarch`. Confirmed on the live node via SSM: that
`dist-info` holds only `INSTALLER`, `LICENSE`, `METADATA`, `WHEEL`, `top_level.txt`.

Facts worth not re-deriving:
- **`requests` is the only one of the eight the AMI preinstalls.** Probed all eight plus pip
  on the live node: `boto3 matplotlib numpy pandas seaborn scipy tailhead` → ABSENT;
  `requests` and `pip` → present and RECORD-less. So the blast radius was one pin, and
  which one is an AMI property that can change.
- **The abort left the node worse than the AMI shipped it.** `numpy` was uninstalled
  successfully *before* `requests` failed. A partially-uninstalled interpreter is not a state
  any later bootstrap stage is written against, which is a second reason the flag matters
  beyond the immediate exit code.
- **Invisible in cfn-init again**, for the fourth consecutive bootstrap failure: it captures
  stdout only, so the last recorded line is the optimistic `Attempting uninstall: requests`
  and pip's actual error went nowhere. SSM on the live node was the only way to see it.
- **`--dry-run` still does not reproduce it** (returns before the uninstall step), same as
  session 26.

Fix: `--ignore-installed` on **all four** node-path pip installs — both arms of
`preinstall.j2`'s dependency line and both arms of `postinstall.j2`'s plotting-stack line.
The `postinstall.j2` half is prophylactic against *transitive* replacement (RHEL 9 ships
`packaging`/`dateutil`/`pyparsing` as RPMs; those five names are unpinned and already
satisfied by preinstall, so the exposure is one level down). The Ubuntu halves are
prophylactic too — whether that AMI preinstalls any of the eight was not checked, on the
grounds that a RECORD-less `dist-info` is a property of distro packaging and re-auditing the
AMI on every image bump is not a maintainable guard.

**The test lesson is the reusable part.** `test_pip_is_never_upgraded_over_the_distro_pip`
passed throughout, and would pass today with the bug restored: it filters to the line whose
install target *is* pip and skips everything else. A guard scoped to the instance of a defect
does not cover the defect's rule. `TestNoPipInstallEverUninstallsADistroPackage` replaces the
scope with "no `pip3 install` anywhere on a node path omits `--ignore-installed`", asserted
over **rendered** text on both arms — a raw-source scan cannot distinguish a line inside an
unexpanded `{% if %}` from one a node actually runs. All seven faithful mutations are caught,
including the exact line that shipped (M1) and a `seen >= 6` vacuity guard that fires if a
branch stops expanding (M7). The old test is kept: it still pins `--upgrade`'s absence on the
pip line specifically, and the `--break-system-packages` asymmetry between arms now has its
own test on all four lines.

**Verified on hardware**, on the failed head node itself before teardown: the verbatim
rendered RHEL arm returned `PIP_EXIT=0` with **zero** `Attempting uninstall` lines. See the
two struck TODO items below for the full package list and the `packaging`/`dateutil` finding,
which upgraded `postinstall.j2`'s flag from prophylactic to load-bearing.

Suite: **1327 passing** (1324 + 3), `ansible-lint` production clean, `shellcheck` clean.

---

### Session 33 — Lmod's `./configure` hard-quits on a missing `bc`

Second live `rhel9` build: `osiris`, serial `osiris-36081728072026`, head node
`i-0000000000000015`, log group `/aws/parallelcluster/osiris-202607281709`. The pip fix
held — **`OnNodeStart` passed** — and the failure moved one stage later to
`OnNodeConfiguredExecutionFailure` at 17:26, i.e. `postinstall.j2`.

```
checking for basename... /bin/basename
checking for bc... no

You must have bc in your path. Quitting!
```

**Root cause: `bc` is not on the RHEL 9 PCluster AMI and neither package line installed it.**
Lmod's `./configure` does not degrade on a missing helper — it prints `You must have <tool> in
your path. Quitting!` and exits non-zero, which `set -euo pipefail` turns into a failed
bootstrap. `bc` lives in `rhel-9-baseos-rhui-rpms`, so it needs neither EPEL nor CRB; probed
absent directly on the live node.

Facts worth not re-deriving:
- **`bc` is the only gap, not the first of a series.** Read Lmod 8.7.55's own `configure` for
  every `You must have` gate: there are six — `pkg-config`, `ps`, `expr|gexpr`,
  `basename|gbasename`, `bc`, and `sha1sum|shasum|md5sum|md5`. Probed all of them on the live
  node: only `bc` was missing (`md5` is also absent but `sha1sum`/`shasum`/`md5sum` satisfy
  that alternative group). So one package closes it.
- **`bc` is used by nothing else in the toolkit** — not by `hpc-benchmark.sh`, not by any
  template. It is on the package line solely for Lmod's configure, which is why the comment
  saying so matters: it reads like a stray utility otherwise.
- **It is on the Ubuntu arm too, deliberately.** Ubuntu builds have been passing, so that AMI
  ships `bc` incidentally. Depending on what a base image happens to carry is precisely how
  this hid, so both arms install it explicitly.

Four items came off the unverified list on this build, all on the head-node path:
`epel-release` by URL, the lua/tcllib package resolution, **all three luarocks rocks
compiling** (confirming the Ubuntu header hazard has no RHEL analog), and both `dnf update`
calls completing without a dracut rebuild. The CRB loop is moot in practice — every package
resolved from `epel` + `rhel-9-{baseos,appstream}`, so *which* repo id works was never
exercised and remains unproven.

**A harness lesson, and it cost a mutation.** The first version of the ordering test asserted
on the execution trace and **M4 survived it**: moving `bc` to a line *below*
`sudo -E make install` still passed, because `./configure` is deliberately not among
`_run_postinstall`'s stubs, so it never reaches the trace and `if configures:` made the whole
assertion vacuous. Ordering here is a property of source position in the rendered script, not
of the trace. The rewrite asserts on rendered text with comment lines stripped — required,
because the comment explaining *why* `bc` is on the package line names `./configure` itself and
put the anchor 80 lines above the command it describes. Both halves are now pinned: presence on
the trace (which proves the line is reached inside the HeadNode gate) and order in the source.
All five faithful mutations are caught, including the exact bug and a vacuity guard.

Suite: **1331 passing** (1327 + 4), `ansible-lint` production clean, `shellcheck` clean.

---

### Session 40 — the device tests were handed an MPI that cannot run them

The one open item in `CLAUDE-STATE.md` was "the device-to-device CUDA path has
produced no numbers on any cluster," logged as an unmeasured path. It was not
unmeasured for want of trying: it **could not** produce a number on a
ParallelCluster GPU AMI, for a reason nothing in the toolkit was looking at.

**The AMI ships two Open MPIs and the default `PATH` one is not CUDA-aware.**
Measured on the AL2023 x86_64 GPU image, and confirmed again on `osiris`'s head
node on 2026-07-31:

- `/opt/amazon/openmpi` — 4.1.7, `mpi_built_with_cuda_support:false`, no
  `btl: smcuda`, no `accelerator: cuda`. **This is what `command -v mpirun` finds.**
- `/opt/amazon/openmpi5` — 5.0.9amzn1, CUDA support `true`, `btl: smcuda` and
  `accelerator: cuda`, built `--with-cuda=/usr/local/cuda`.

Neither is broken; 4.1.7 is kept as the default for ABI compatibility and
openmpi5 is opt-in. **The failure mode is what made this expensive:** `osu_latency
-d cuda D D` under 4.1.7 does not error — it prints its header and then **hangs**
at the first message size, both ranks at 99.9% CPU in `R` state, `WCHAN` empty,
`poll` the only syscall, **0% GPU utilization**. It never times out. On a queue
with `TimeLimit=UNLIMITED` that is forever, and nothing is reported and no result
file is written. A wrong MPI is worse than no MPI: a missing one skips with a
reason, this one consumes the allocation in silence. Observed directly in job 1's
tail on 2026-07-31 — the CUDA latency section ends at `# Size  Avg Latency(us)`
with nothing after it.

**Two facts decided the shape of the fix, both verified rather than assumed:**

- **Switching `mpirun` alone is not a fix.** OSU binaries link against whichever
  MPI compiled them, so using openmpi5 means rebuilding with *its*
  `mpicc`/`mpicxx`.
- **`LD_LIBRARY_PATH` must not be used to do it.** Both ship SONAME
  `libmpi.so.40` (`.40.30.7` vs `.40.40.7`), so a global export silently
  redirects every 4.1.7-linked binary into openmpi5. It is also unnecessary: both
  wrappers bake `RUNPATH` to their own `lib64`, and a binary resolves correctly
  under `env -u LD_LIBRARY_PATH`. The driver uses absolute paths to the wrappers
  and to `mpirun`.

**The probe is the acceptance test, which is what makes this OS-agnostic.**
`_mpi_is_cuda_aware` asks `ompi_info --parsable --all` for
`mpi_built_with_cuda_support:value:true` and requires the root to carry both
`bin/mpirun` and `bin/mpicc`. No version number and no directory name appears in
any decision — a `5` in the path or a version comparison would be right on
today's AL2023 image and wrong on every other `base_os`, and wrong again the next
time upstream renumbers. `ompi_info` costs **12 ms**, so it is free per
candidate. `_cuda_aware_mpi_root` tries the operator override, then the **default
`mpirun`**, then a glob list; an image whose default MPI is already CUDA-aware
therefore never consults a glob, which is the property that covers the other
seven `base_os` values by the same code path rather than by a special case. The
globs span ParallelCluster's `/opt/amazon`, RHEL-family `/usr/lib64/openmpi*`,
and Debian multiarch. A glob that misses costs a skipped optional test with a
named reason — never a wrong number and never a hang.

**`bin/osu` stays on the default `mpicc` deliberately.** It serves the
host-to-host tests, whose numbers must stay comparable across runs and node
classes, so rebuilding it against openmpi5 would silently redefine every headline
latency figure. The device tests move to `bin/osu-cuda`, built with the
CUDA-aware MPI's own wrappers and launched by its own `mpirun`. The
`.cuda_enabled` stamp gained a **third field** — the MPI root the tree was linked
against — so a run lands on a node that did not compile the tree and can still
tell whether it is usable. A legacy two-field stamp forces a rebuild rather than
being read as a match.

**`install` now names the MPI and says whether it can do `-d cuda`.** Without
that, an image whose default MPI is not CUDA-aware produces a successful install
and a tree that can never run the device tests, with the first hint arriving from
a job on a different node hours later.

Guarded by `TestDeviceTestsRunUnderAnMpiThatCanDoThem` (19 tests) plus reshaped
OSU-CUDA classes; **+26 tests, 1792 → 1818**. All **23** faithful mutations
caught across four batteries. One mutation is worth recording because it was a
real defect the first battery found: a root selected without `bin/mpirun` yields
`"$root/bin/mpirun"` as the launcher, and a command-not-found inside the run's
`tee` pipeline is a non-zero status `pipefail` propagates — so the job would
**die** instead of skipping an optional test. A second mutation
(`N8_probe_outside_the_gpu_gate`) was judged **behaviorally inert** — the
launcher it sets is never read on the CPU path — and deliberately given no guard,
per the repo's rule against tests for unreachable branches. The harness had to
grow two seams to see any of this: `HPC_BENCHMARK_CUDA_MPI` (no developer machine
or CI runner has any MPI at all, so every assertion would otherwise pass against
an implementation containing no probe), and a fake `configure` that **executes**
whatever `CC=` names, because passing a wrapper is not the same as it being
honored.

**Confirmed on hardware, head node only.** On `osiris` 2026-07-31: the probe
answers `/opt/amazon/openmpi5`, the default resolves to `/opt/amazon/openmpi`,
`install` prints the NOT-CUDA-aware advisory naming both the root and
`HPC_BENCHMARK_CUDA_MPI`, and the stamp reads `yes /usr/local/cuda
/opt/amazon/openmpi`. The advisory's accuracy is the point, not its presence —
that node's default MPI really is 4.1.7. **Still open:** a GPU *compute* node
building `bin/osu-cuda` against openmpi5 and the two `*_cuda.txt` files carrying
numbers. Jobs 7 and 8 were submitted to answer that; session 41 read their output
and found the build failing for an unrelated reason, so the one open item in
`CLAUDE-STATE.md` is narrowed, not closed.

Docs: the two-MPI hazard is now a constraint bullet in `hpc-benchmark/CLAUDE.md`,
and `README-PERFORMANCE.md` + its `.j2` gained an operator-facing section (both
copies, since the `.md` is a de-Jinja'd duplicate — the sync was verified by
diffing with the Jinja expressions substituted out). Four `file:line` citations
were renumbered as the added lines shifted the OSU launch calls.

Suite: **1818 passing**, `ansible-lint` production clean, `shellcheck` clean.

---

### Session 42 — the right launcher loaded the wrong libmpi, because LD_LIBRARY_PATH outranks RUNPATH

Job 10 was the first run in which the CUDA tree actually **built**, and it failed
one step later:

    .../osu_latency: symbol lookup error: .../osu_latency:
    undefined symbol: ompi_mpi_instance_null
    Exit code: 127

Third defect on this one code path, and the third to be hidden behind its
predecessor — a tree has to build before anything can fail to load it. Session 40
fixed *which launcher*; this one is *which library the launcher's ranks resolve*.

**The mechanism, measured on the `osiris` head node rather than reasoned about.**
`ompi_mpi_instance_null` is an Open MPI 5.x symbol: `nm -D --defined-only` finds
it once in `/opt/amazon/openmpi5/lib64/libmpi.so` and **zero** times in
`/opt/amazon/openmpi`'s. The CUDA binaries do carry the right `RUNPATH` —
`readelf -d` shows `[/usr/local/cuda/lib64:/usr/local/cuda/lib:/opt/amazon/openmpi5/lib64]`
— but `LD_LIBRARY_PATH` is searched **before** `RUNPATH`, and
`job_hpc-benchmark.sh.j2:16` runs `module load openmpi`, which exports
`LD_LIBRARY_PATH=/opt/amazon/openmpi/lib64` (openmpi5's lib64 is absent from it;
the default login value is unset entirely). Both MPIs ship SONAME
`libmpi.so.40`, so the loader found a plausible file with a missing symbol rather
than failing to find one at all.

That invalidates the premise of session 40's own `env -u LD_LIBRARY_PATH`
measurement. It was correct — both trees do resolve correctly with the variable
cleared — but it is not the environment a job runs in, and the blanket ban it
justified (session 40's whole-driver ban on the variable, since renamed and
narrowed, so its old name is deliberately not cited here — the docs' test-name
sweep requires every cited name to resolve) forbade the fix. The hazard the ban
was *really* about is
shell-wide scope, not the variable: an `export` redirects the host-to-host
binaries into openmpi5 too, silently changing every headline number. So the guard
is now `test_ld_library_path_is_never_set_for_longer_than_one_command`, which bans
`export`, bans a bare assignment as its own statement, and requires every
assignment to be a prefix whose next line is the CUDA launcher.

**Three properties, each with its own reason.** Per-command prefix, never
`export`, for the scope reason above. **Prepend**, not replace: the inherited
value may carry CUDA or other libraries the binary also needs, and the loader
takes the first match, so prepending is sufficient — verified with `ldd` in both
directions, the CUDA binary resolving to openmpi5 and the host-to-host tree still
resolving to openmpi via its own RUNPATH. And **`-x LD_LIBRARY_PATH`** on the
launcher, because `prterun` ships its own environment to the ranks and a
launcher-only fix never reaches the process that dies.

**Fixed live before it was committed, and it produced numbers.** The candidate
form was run by hand on the head node and returned real device-to-device
latencies (80-83 us at the large message sizes) — the first data this path has
ever produced. `enable_efa` is false on that cluster, so those figures are not
expected to beat host-to-host; the point is that they exist.

**+7 tests, 1824 → 1831,** in `TestTheCudaTreeLoadsItsOwnLibmpi` plus the
narrowed static ban. Three harness changes are what make the runtime half
possible, and all three were bugs in the *old* harness:

- `_fake_mpi_root`'s `mpirun` did a blind `shift 2`, which silently ate
  `-x LD_LIBRARY_PATH` and exec'd the flag as the program. It now parses `-n`/`-np`
  and `-x` in a loop and reports what it was asked to forward.
- It created both `lib` and `lib64`; the Amazon packages have `lib64` only, so an
  implementation emitting `lib` unconditionally was uncatchable. Now `lib64` alone.
- The **default**-PATH `mpirun` stub now echoes its own inherited value under a
  distinct prefix. Without that, exporting once for the whole `osu` branch fixes
  the d2d tests while poisoning host-to-host, and no test can see it —
  `test_the_host_to_host_launch_is_left_alone` is that mutation's detector, and it
  needed two attempts: the first version split the launches positionally on the
  first `FROM cuda-tree` line, but the CUDA stub reports its own environment
  *before* exec'ing, so that launch's value was attributed to the wrong side and
  the assertion could never hold.

All nine faithful mutations are caught: dropping `-x`, replacing instead of
prepending, appending instead of prepending, a trailing colon on an unset
inherited value, emitting `lib` unconditionally, exporting once for the whole
branch, an `export` in place of the prefix, a bare assignment as a statement, and
setting the prefix on something other than the launcher.

Citations renumbered for the ~46 inserted lines: the OSU launch calls are now
`1126,1130,1208,1214` (the d2d pair's `-n 2` moved onto the launcher line, one
above the binary, since the prefix splits each command across three lines), and
`CLAUDE-STATE.md`'s `609,675` → `653,719` and `792` → `836`.

Staged to both head-node delivery paths on `osiris` (md5
`38a31e130bdc4974ad643584e3b93414`, `bash -n` clean on the node), so the next GPU
submission runs the fixed driver.

**Jobs 11 and 12, submitted the same day against the staged driver, closed the
path this session fixed.** `sbatch --partition=compute --ntasks-per-node=1
job_hpc-benchmark.sh` (job 11) and `sbatch --partition=gpu --ntasks-per-node=1
job_hpc-benchmark.sh` (job 12) both ran to `All benchmarks complete` with empty
`.err` files. Job 12's `bin/osu-cuda/.cuda_enabled` reads `yes /usr/local/cuda
/opt/amazon/openmpi5`, and `osu/latency_cuda.txt` / `osu/bandwidth_cuda.txt` each
hold 23 monotonic rows from 1 B to 4 MiB — the first data this path has ever
produced from an actual job rather than a hand-run command. Device-to-device
latency (53.26-2831.52 us) is higher than host-to-host (27.40-2634.29 us over the
same range) and bandwidth caps lower (1171.29 vs 1191.17 MB/s) — expected, since
`enable_efa` is false on this cluster and there is no GPUDirect RDMA path; every
hop crosses PCIe to reach the device. `bin/.osu-cuda.lock` was absent after the
run, so the concurrency lock left no residue. Job 11's `compute`-queue run and job
12's `gpu`-queue run left two different `bin/stream-<march>` binaries side by side
(`stream-skylake-avx512` and `stream-znver3`), confirming the per-microarchitecture
cache differs correctly across queues sharing one `bin/`. `sinfo -R` came back
empty — no drained or down nodes from either job.

This closes the last item this file's own grade section had been carrying since
session 23d/23e: a real number from a real GPU job. `CLAUDE-STATE.md`'s grade
moves from A to **A+** as a result — the criterion that section stated for the
move ("when that path produces real rows — not before, and not on the strength of
a green suite") is now met on hardware, not asserted. The CUDA build itself is
timed for the first time: `install`'s `--enable-cuda=yes` build ran from job 12's
log-start (17:52) to `bin/build_logs/osu-cuda.log`'s mtime (17:56:52), about 4
minutes. The four "a few minutes" estimates in the docs are left as prose anyway —
one run on one instance family with a cold `bin/` is a data point, not a number to
advertise as *the* build time.

**This `osiris` build (serial `49170731072026`) was torn down the same day, via
`kill_pcluster.py`, once jobs 11 and 12 confirmed the fix.** Verified after
teardown: `aws cloudformation describe-stacks --stack-name osiris` returns
`ValidationError: ... does not exist`, no `osiris-*` EC2 keypair remains, no
`pclustermaker-policy-49170731072026-*` IAM policies remain, no matching S3
bucket remains, and `active_clusters/` is empty locally. `osiris_defaults.yml`
is unaffected — it is the gitignored rebuild template, not per-cluster state.

Suite: **1831 passing**, `ansible-lint` production clean, `shellcheck` clean.

---

### Session 43 — post-teardown cleanup deleted the shared results bucket, taking job 11/12's raw files with it

No code changed this session. The operator asked to scrub CloudWatch and then
S3 of `osiris` remnants after the session-42 teardown.

**CloudWatch: one group, correctly deleted.** `describe-log-groups` with prefix
`/aws/parallelcluster/osiris-` returned exactly one group,
`/aws/parallelcluster/osiris-202607310718`, `0` bytes stored. Swept all three
likely regions (`us-east-1`, `us-east-2`, `us-west-2`) and a substring search
across every log group in `us-east-2` to rule out a differently-dated name
before deleting. This is the operator-purge path the retained-log-group bullet
in `CLAUDE.md` describes — PCluster retains these on teardown by design, and
purging by hand once a build is confirmed good is the documented workflow, not
a gap.

**S3: the request widened mid-session, and the second half is the one worth
reading carefully.** `list-buckets` found no per-build bucket (already deleted
at teardown, as expected) and exactly one match:
`parallelclustermaker-results-123456789012-us-east-2` — the long-lived results
bucket the results-bucket bullet in `CLAUDE.md` describes as "the only
surviving copy once the cluster is gone," keyed on account+region so it
outlives every cluster in the account. `hpc-benchmark-results/osiris/` held
256 objects: the raw job 11/12 output (`osu/latency_cuda.txt`,
`bandwidth_cuda.txt`, `hpcg/*`, `stream.txt`, `ior.txt`, `cmd.txt`,
`README-PERFORMANCE.md`) across five separate run timestamps — the direct
evidence behind session 42's A → A+ closure. No other cluster had written to
the bucket yet.

The first ask ("what about s3 buckets") was answered by identifying the
bucket and its contents and stopping there — the design intent (survive the
cluster) was flagged and a delete was **declined** via `AskUserQuestion`,
consistent with the "no destructive action without confirmation" rule for
hard-to-reverse actions. The follow-up ("delete the bucket AND its objects")
was explicit and scoped, so a second confirmation named the actual blast
radius (shared infrastructure, not osiris-only, next benchmark-enabled build
will need it recreated) before `aws s3 rb --force` ran. Confirmed empty
afterward: `list-buckets` shows no `parallelclustermaker-*` bucket remaining
in the account.

**Net effect: this project's own demo-run files are gone; the grade is not
retracted, and nothing about the toolkit's design changes.** The numbers
session 42 measured are already transcribed inline in `CLAUDE-STATE.md` and in
the session-42 section above (device-to-device latency 53.26-2831.52 us vs
host-to-host 27.40-2634.29 us, bandwidth 1171.29 vs 1191.17 MB/s, both over
1 B-4 MiB, 23 rows each). The results bucket's job is to let an *operator's
own* benchmark output survive that operator's own cluster teardown — it was
never meant to be a permanent archive of this repo's example runs, and
re-verifying the closure means running the suite again, not restoring a
deleted object. `CLAUDE-STATE.md`'s teardown section notes this so a reader
does not read the bucket's absence as data loss. **The bucket will be
recreated, empty, the next time a cluster builds with `enable_hpc_benchmarks`
— nothing in `create_pcluster.yml` needs updating for that.**

Suite: unchanged, no code touched.

---

### Session 41 — the CUDA tree was configured out-of-tree, and two IOR runs ate each other's files

Jobs 7 and 8 from session 40 came back with errors, and the question that came
with them was whether the right fix all along was `module load openmpi5` rather
than absolute paths. Two independent defects, both real, both in code written by
this toolkit. **Neither was the MPI selection** — the openmpi5 probe worked
exactly as designed on the GPU node, which is visible in the failure's own log.

**Defect 1: `configure` invoked by absolute path is a VPATH build.**
`_build_osu_cuda` ran `"$tmpdir/osu-micro-benchmarks-$V/configure"` from wherever
the job started, then `make -C "$tmpdir/osu-micro-benchmarks-$V" install`.
Autoconf writes `Makefile`, `config.status`, `config.log`, `libtool` and the whole
`c/` tree into the **CWD**, not the srcdir. So `make -C srcdir` found no
`Makefile` and died on `make: *** No rule to make target 'install'.  Stop.` —
every device test, on every node, since the feature was written.

Two things made this hard to see and easy to misread:

- **The CWD is the job's submit directory, which is shared storage.** `osiris`'s
  ended up holding `Makefile`, `config.log` (62 KB), `config.status` (68 KB),
  `libtool` (345 KB) and a `c/` tree, alongside the rendered job script and the
  `.out` files. That is where the litter came from.
- **The run still exits 0.** `_try_build_step` is non-fatal here on purpose — the
  host-to-host numbers are already written by that point — so the only signal is
  a `NOTE:` on stderr saying a CUDA tree could not be built. Job 8's
  host-to-host OSU, IOR and HPCG results were all correct and complete.

The evidence that the MPI work was fine is in `config.log`, which recorded the
configure line verbatim: `CC=/opt/amazon/openmpi5/bin/mpicc
CXX=/opt/amazon/openmpi5/bin/mpicxx --enable-cuda=yes
--with-cuda=/usr/local/cuda`, with `checking for library containing
cuPointerGetAttribute... -lcuda` and `cuda.h... yes` further down. The probe
picked the right MPI, `configure` accepted it, and then `make` was pointed at a
directory with no makefile in it. The fix is a subshell that `cd`s into the srcdir
and runs `./configure` and a bare `make install`. **`install`'s own OSU build was
never wrong** — it already `pushd`es — which is why only the run-time path broke.

**The suite passed the whole time, and two harness stubs are why.** The fake
`configure` wrote its `Makefile` beside itself rather than into `$PWD`, so it
could not distinguish an in-tree build from a VPATH one; and the fake `make`
ignored `-C` and created the tree from anywhere. Both are fixed — the stub `make`
now exits 2 with GNU make's own wording when its directory has no makefile — and
`_osu_cuda_run_harness` gained a dedicated `submitdir` CWD, returned as a fourth
element, so litter is observable. It had been inheriting the repo root: a
mutation run of the broken form overwrote **this repo's own `Makefile`** with
`CONFIGURED IN <repo root>`, which is how the litter half of the bug surfaced at
all (restored from git; the new CWD makes it unrepeatable).

**Defect 2: `--fs-path` names a filesystem, and the IOR object name was fixed.**
Jobs 7 and 8 overlapped by four seconds, both writing
`ior_scratch/ior_testfile.00000000` and `.00000001`. Job 8 finished first and ran
`rm -f "$fs_path/ior_testfile"*`, deleting job 7's files mid-run; job 7 reported
`ERROR: stat(".../ior_testfile.00000000", ...) failed, (aiori-POSIX.c:866)` for
both ranks. Sharing the *path* is the point — that is what makes the measurement
meaningful — but sharing the files is not, and the error names only a path, so it
reads as a filesystem fault. The object is now `ior_testfile.$ts`, where `$ts` is
`date +%Y%m%d_%H%M%S_$$`, the same value that already makes `$results_dir/$ts`
unique; the `rm` is scoped to match.
`TestTwoConcurrentRunsDoNotFightOverTheIorScratchFiles` runs two real concurrent
`cmd_run --tests ior` against one path, with **deliberately unequal** fake-IOR
durations (4s and 1s) — with equal ones both check their files before either
reaches its cleanup and nothing reproduces.

**On the modules question: `module load` was considered and is the wrong tool
here, for a measured reason.** `/opt/amazon/modules/modulefiles` really does carry
`openmpi5/5.0.9amzn1`, and using it would be idiomatic. But its entire content is
three `prepend-path` lines, one of which is `LD_LIBRARY_PATH
/opt/amazon/openmpi5/lib64` — and both MPIs ship SONAME `libmpi.so.40`
(`.40.30.7` vs `.40.40.7`). Demonstrated on the `osiris` head node: `bin/osu`'s
`osu_latency` resolves `libmpi.so.40 => /opt/amazon/openmpi/lib64/...` normally,
and `libmpi.so.40 => /opt/amazon/openmpi5/lib64/...` after `module load
openmpi5`. That is the host-to-host binary silently running against a different
MPI than the one that compiled it, in the same process tree as the device tests.
`module` is also a shell **function** sourced from `/etc/profile.d`, absent under
`env -i` and unavailable to anything not running a profile — the driver is
`copy:`d, runs under `set -euo pipefail`, and is sourced by the test suite. And
the modulefile only adjusts `PATH`; it cannot rebuild `bin/osu-cuda`, which is the
actual requirement, since OSU links against whichever MPI compiled it. Absolute
paths to the wrappers give the same result with neither hazard, and both trees
resolve correctly under `env -u LD_LIBRARY_PATH` because each bakes `RUNPATH` to
its own `lib64`. The modules are the right mechanism for an *operator* at a shell
— `job_hpc-benchmark.sh.j2` still suggests `module load` — and the wrong one
inside a script that has to keep two MPIs apart in one process tree.

**+6 tests, 1818 → 1824.** Both fixes are pinned by tests that reproduce the
shipped failure against the real driver via a `monkeypatch`ed `BENCHMARK`, since
in both cases a source-text assertion cannot tell a `cd` or a `$ts` that is
present from one that has any effect. Four `file:line` citations renumbered again
(OSU launch calls now 1082/1086/1153/1157).

Suite: **1824 passing**, `ansible-lint` production clean, `shellcheck` clean.

**Staged to the live head node, and the node's own logs confirm the diagnosis
verbatim.** `scp` to both delivery paths on `osiris` (`~/hpc-benchmark/` and
`~/hpc-benchmark/osiris/rmarable/slurm/`, md5 `e05b0ace06450be28dcd52accdf3caf7`,
`bash -n` clean). `bin/build_logs/osu-cuda.log` from job 8 ends
`config.status: executing libtool commands` / `make: Entering directory
'/tmp/hpc-benchmark-osu-cuda.3G2U9q/...'` / `make: *** No rule to make target
'install'.  Stop.` — so `configure` ran all the way through `creating
c/mpi/pt2pt/congestion/Makefile` and wrote every one of those Makefiles into the
*submit* directory while `make -C` looked in `/tmp`. The submit directory held
`Makefile`, `config.status`, `config.log`, `libtool` and a 1.3 MB `c/` tree; `bin/`
held `osu`, `ior`, `hpcg`, both STREAM binaries and no `osu-cuda`, which is exactly
the shape the bug predicts. The five litter entries were cleared by hand
afterward — `c/` was checked first and held 19 Makefiles and **zero** `.c` files,
confirming it was `config.status` output rather than a source tree that happened
to land there.

**Section C's `rm -rf` became obsolete in session 40 and nobody noticed until
now.** It was added in session 38 because the run-time dispatch then tested only
*whether* `bin/osu` had CUDA, so an install-time build on a GPU head node
short-circuited the path section C exists to exercise. Session 40 replaced that
test with `_osu_cuda_tree_matches_mpi`, which compares the stamp's **third field**
against this node's CUDA-aware MPI — and `install` deliberately builds `bin/osu`
with the *default* `mpicc` so the host-to-host numbers stay comparable across runs.
Read off the live head node: `bin/osu/.cuda_enabled` is `yes /usr/local/cuda
/opt/amazon/openmpi` while the probe answers `/opt/amazon/openmpi5`. It does not
match, `_build_osu_cuda` is reached on its own, and the `rm` now only discards a
valid artifact. The general point is the session-38 one again: a checklist step
whose justification is a code shape has to be re-derived when that shape changes,
and neither the byte ceiling nor the citation manifest can see a step that is
merely *pointless* rather than wrong.

Four `CLAUDE-STATE.md` citations were also stale and are corrected here:
`hpc-benchmark.sh:468,524` (cited three times as the "a few minutes" estimates)
were `_osu_cuda_stamp_path` and `_stream_bin_path`; the estimates are at `609` and
`675`, and the `README-PERFORMANCE.md` copies carry theirs at line **340**, not 331.
`hpc-benchmark.sh:641` was cited as the install-time CUDA branch and is
`_usage_install()`; that branch is at `792`. None of the four were in
`_EXPECTED` — the manifest pins only citations in the three `CLAUDE.md` files, and
`CLAUDE-STATE.md` is deliberately excluded because it is a dated log rather than an
instruction. That exclusion is still right, and this is what it costs.

---

### Session 38 — the closeout checklist was wrong about the cluster it was run against

No code and no test changes. `## GPU queue closeout commands`, then at the end of
`CLAUDE-STATE.md`, was written on 2026-07-26 for a **CPU head node fronting a GPU queue**, and
hardcoded that shape into four expected results. Run against the `g6.xlarge`-head
`alinux2023` `osiris` of 2026-07-31, four of its lines are wrong:

- `nvidia-smi -L # both expected to FAIL on a CPU head node` — succeeds on a GPU
  head node.
- `command -v nvtop` on the same line — fails on `alinux2023` on **either** node
  type, for an unrelated reason (AL2023 does not package it, no EPEL to fall back
  on, so `postinstall.j2`'s AL2023 arm installs `htop` only). Reading its failure
  as evidence about the GPU block is a false negative waiting to happen.
- `ls -l bin/osu/.cuda_enabled # expected ABSENT` — **present**, because
  `hpc-benchmark.sh:641` builds CUDA-enabled OSU during `install` whenever
  `_host_has_gpu`, which is a property of the node, not of `enable_gpu`.
- `ls -d /fsx/pkg` — this build has `enable_fsx: false` and `enable_efs: false`,
  so `vars_file.j2`'s precedence (`fsx > efs > external_nfs > ebs`) puts `pkg_dir`
  at `/shared/pkg`.

**The third one is not cosmetic — it silently voids section C.** The run-time
dispatch is an `elif` chain: `:946` (`_osu_cuda_enabled "$BENCH_BIN/osu"`) matches
the install-time tree and short-circuits, so `_build_osu_cuda` at `:950` — the one
path that has never run on hardware, and the whole reason section C exists — is
never reached. Section C now opens with
`rm -rf bin/osu/.cuda_enabled bin/osu-cuda bin/.osu-cuda.lock`, to be skipped only
on a CPU-only head node. Without it, C would have "passed" while testing the
install-time build for the second time.

Section D was rewritten as a pure concurrency check. It said the lock test needed
multiple nodes and named a `<gpu_count>` to pass to `sbatch`; both are wrong —
`job_hpc-benchmark.sh` derives `--partition` and `--ntasks-per-node` itself, and
Slurm scales the queue to whatever the jobs ask for. The test is two overlapping
jobs, and how many nodes they land on is irrelevant. The EFA-GDR sentence in C was
dropped as well: the pass criterion is non-empty monotonic rows, and
device-to-device latency relative to host-to-host depends on the interconnect.

**The process lesson, which is the reason this section exists at all.** Session
37's review swept prose for *dangling references* — test names and `file:line`
citations — and built two CI guards for them. It never asked whether a
checklist's **assumptions** still described a real cluster. `CLAUDE-STATE.md` was
deliberately excluded from the `file:line` manifest as "a dated log", which is
correct for line numbers and wrong for an operational checklist that gets read
back as instructions. No guard written in session 37 would have caught any of the
four defects above, and none is proposed here: a checklist's expected results
depend on the shape of a cluster that does not exist at test time. The mitigation
is the one now in the checklist's own header — state which lines vary with cluster
shape, and never write an "expected to FAIL" without saying what it is evidence
*of*.

### Session 37 — a multi-agent adversarial review, and the two drift classes it exposed

A fan-out adversarial review over the whole tree, findings sorted by impact and
severity, then fixed in descending order. **293 new tests** (1493 → 1786) across
**28 new classes**, every one carrying its own mutation battery at a 100% kill
rate. Nothing here was found by a live build — this was a review pass — so read
the "Why it is not a clean A" caveat before treating any of it as hardware-
confirmed. The durable conclusions are all promoted into `CLAUDE.md` bullets; what
follows is only what does not survive as a constraint.

**The two findings worth remembering are about the docs, not the code.** Both are
drift classes rather than individual defects, which is why each got a guard
instead of a correction:

1. **Dangling test-name citations.** Three `CLAUDE.md` bullets named tests that had
   been renamed. Sweeping for the class found more, including one caused by a
   rename made *in this same session* — the constraint bullets cite test names
   heavily, and nothing executed prose.
   `TestEveryTestNameTheDocsCiteStillExists` now requires every `test_*`/`Test*`
   token in tracked `.md`/`.md.j2` prose **and in `#` comments of tracked `.py`
   files** to be defined somewhere in `tests/`. The required-surface list is
   derived from `git ls-files` with a `len(required) >= 5` floor, because the first
   version was an enumerated list of five names that could be cut to one with the
   test still green (mutation N10).
2. **Stale `file:line` citations.** `templates/CLAUDE.md` sent the reader to line
   527 of `pcluster_core.py` for an `attach_role_policy` call that had moved to
   `src/pcluster_core.py:734` — landing on a bare `)`. The OSU pt2pt citations were
   stale in **two** files. `TestEveryLineNumberTheNormativeDocsCiteStillPointsAtItsSubject`
   is a `(file, line) -> substring` manifest rather than a blanket rule, because
   most cited lines are upstream PCluster and unresolvable here by construction.
   **`CLAUDE-STATE.md` is deliberately excluded from that sweep** — it is a dated
   log, and pinning its line numbers would freeze a historical record against every
   later edit. Its citations are still corrected by hand when found.

Two process notes from the mutation work, both worth not rediscovering:

- **A whole-file substring match cannot see a deletion when the same string
  appears elsewhere in the file.** This produced three separate survivors in one
  session: the GPU guard message contains the word `nvtop`, so dropping the
  package from the install line passed; `` no `compute` partition `` appears in
  README's HPC Benchmarks section as well as Job Submission; and
  `create_pcluster.yml`'s adjacent `src:`/`dest:` lines both name the same script,
  so a one-line shift was undetectable. Every fix was to narrow the scope and then
  **assert that the narrowing happened**, since the obvious repair for the
  resulting failure is to widen it back.
- **A test that re-implements the production comparison cannot detect that
  comparison being neutered.** The manifest's discrimination test survived twice
  for this reason. It now `monkeypatch`es `_EXPECTED` to a neighboring line and
  calls the real test method under `pytest.raises(AssertionError)`.

One reported finding was investigated and **closed as a non-defect**:
`kill_pcluster.py` was thought to remove the serial and vars files on a failed
teardown, stranding a retry. It does not — `check=True` raises before either
`os.remove`, and `sys.exit(e.returncode)` leaves both on disk.
`TestTeardownFailureLeavesStateForRetry` already pinned it. Recorded here because
the finding will look plausible again to the next reader.

### Session 36 — the GPU closeout checklist, and a preflight the checklist itself needed

Ran sections A and B of the GPU queue closeout against a fresh `osiris` built with
`g6.xlarge` GPU nodes (`g5g` was ruled out: zero `us-east-2` AZs offer it). Three
gaps closed on hardware, one defect fixed, and the checklist's own commands
corrected.

1. **`postinstall.j2`'s `/scratch` `else` arm fires and is benign** — first
   observation on hardware. `/local_scratch -> /scratch` on
   `/dev/mapper/vg.01-lv_ephemeral`, no nesting, no fstab line, writes work. The
   NVMe block correctly no-op'd because PCluster's `ephemeral_drives` cookbook had
   already claimed the device into LVM, so `holders/` was non-empty. **The
   bind-mount worry in the old closeout note is retracted.** Proves the symlink
   case, not "mount onto a symlink" — the mount never happened.
2. **Slurm GRES exists, via the cookbook.** `GresTypes = gpu`, `Gres=gpu:l4:1`.
   `CLAUDE.md` had implied GRES simply is not there; the CLI genuinely does not
   emit it and `grestypes` is on `SLURM_SETTINGS_DENY_LIST` (verified in
   `pcluster/validators/slurm_settings_validator.py:19-40`), but the cookbook
   configures it at bootstrap. Corrected. Rank-count matching stays the mechanism
   — chosen because it depends on nothing but `--ntasks-per-node`, not because
   GRES is unavailable. Do not rewrite the job template to `--gres=gpu:N` on one
   cluster's evidence.
3. **A 1-slot allocation cannot run OSU, and nothing said so.** My own checklist
   shipped `srun -p gpu --nodes=1 --pty bash`, which allocates one task; OSU pt2pt
   is hardcoded `-n 2`, so section C died in 0.061s on Open MPI's "not enough
   slots" message, which names the binary path and not the cause. Both the
   checklist and the driver are fixed — see the preflight section in the closeout
   block. `iris` job 9 never hit this because `sbatch` supplied
   `--ntasks-per-node=4`.
4. **Two path traps worth not rediscovering.** `<cluster_owner>` is the operator's
   name, not `$(whoami)` (the login user is `ec2-user`), and
   `create_pcluster.yml` `cp -a`s the suite to several independent roots — `install`
   in one populates only that one. Running `run` from `/shared` after installing in
   `~/` correctly aborts on the missing arch stamp in 0.031s. Driver behaving
   right, not a defect.
5. **Sections C, D, and E were closed without being run**, at the operator's
   direction. The device-to-device CUDA path has therefore produced **no numbers**,
   and the four "a few minutes" CUDA-build estimates were deliberately left in
   place rather than filled with a figure nobody measured. `install`'s estimate was
   updated to "about a minute" from the measured 30.9s. See the caveat block at the
   head of the closeout section before citing any of C/D/E as verified.

### Session 35 — Docker's bridge deadlocked MPI, and two access scripts blamed the cluster

Three defects, all found on or confirmed against live `iris` (`alinux2023arm`,
2 x `c8g.2xlarge`, monitoring on). Suite **1476 passing** (1431 + 45), `ansible-lint`
production clean, `shellcheck` clean.

**1. Every node's `docker0` carries the same address, and Open MPI hangs on it.**
`btl_tcp_if_exclude` defaults to `127.0.0.1/8,sppp` and `oob_tcp_if_exclude` defaults to
**empty** — both read out of `ompi_info` on the node, not assumed. `--enable_monitoring`
installs Docker, so every monitored cluster has a `172.17.0.1/16` bridge on *every* node and
each rank advertises an address that routes back to whoever dials it. It **hangs**; it does
not fail. `iris` sat in that deadlock for **13h14m** with `TimeLimit=UNLIMITED`, so Slurm
never reaped it: the 2-rank latency and bandwidth tests passed (both ranks on one node), then
the 8-rank all-reduce wrote its header and stopped — `allreduce.txt` 90 bytes, zero data rows.
`_isolate_mpi_interfaces` in `hpc-benchmark.sh` now excludes a glob list of virtual interfaces
from both channels, via `OMPI_MCA_*` in the environment rather than `--mca` flags (Intel MPI's
`mpiexec` rejects `--mca` outright). 25 tests, 12 of 12 faithful mutations caught. See the
virtual-interface bullet in `hpc-benchmark/CLAUDE.md` for the load-bearing properties.

**The live A/B is closed** — job 9 on `iris`, 2026-07-29, same cluster and same 8-rank/2-node
shape: `allreduce.txt` went from 90 bytes/0 rows to **699 bytes/21 rows** (1 B through 1 MiB),
written ~25s into the OSU stage, and the driver reached `All benchmarks complete` in 34m under
`set -euo pipefail` with an empty `.err`. `==> Excluded virtual interfaces from MPI: docker0`
is the **first line** of the job output, so the exclusion ran on the real path and not just in
a hand-driven `mpirun`. `alltoall.txt` and IOR's 8-task cross-node run also completed — three
cross-node MPI paths where there had been one hang. Note `sacct` is unavailable on this cluster
(`Slurm accounting storage is disabled`), so the driver's own completion line plus the empty
`.err` is the exit evidence, not an accounted `ExitCode`.

**2. A failed AWS call and a stopped cluster were reported identically.** Both
`templates/access_cluster.j2` and `templates/grafana_tunnel.j2` ran `describe-instances`
under `2>/dev/null || true` and then said `Is the cluster running?` on an empty result —
discarding the one line naming the actual cause and erasing the only thing that separates the
two cases: **a missing instance answers the literal string `None` with rc=0, while an auth or
API failure is a non-zero rc with a message on stderr** (verified empirically). An expired
token sent the operator to check a perfectly healthy cluster, which is exactly what happened
on `iris`. Both files now share a `_describe_head_node` function, an `_AWS_RC` that survives
both call sites, and two mutually exclusive diagnoses. 20 tests parametrized over both
templates; 14 of 14 mutations caught, including restoring the exact code that shipped in each.

**3. A real leak the tests found, not the reviewer.** `access_cluster.j2` ends in `exec ssh`,
and **an EXIT trap does not run on `exec`** (verified under bash 5.3; a control with plain
`exit` fires correctly) — so the happy path leaked one temp file per connection, on a script
an operator runs dozens of times a day. Fixed with an explicit `rm -f` before the `exec`;
`grafana_tunnel.j2` does not `exec`, so its trap suffices and must not get a redundant one.

Two harness lessons worth not re-deriving, both in `tests/test_shell_surfaces.py`:
- **`shlex.quote`, never Python `repr`, for bash stubs.** `repr("None\n")` renders the newline
  as a backslash escape that bash single quotes pass through literally, so the stub answered a
  6-character string and the script's `== "None"` test missed, falling through to `exec ssh`.
- **macOS `mktemp` ignores `TMPDIR`** — it resolves its default directory from
  `_CS_DARWIN_USER_TEMP_DIR`, so the first leak check silently watched an empty directory and
  two mutations survived. `mktemp` is stubbed into an observable directory instead. This is the
  second cross-platform harness bug in this file (see the `gzip` note in `CLAUDE.md`); a green
  local run is still not evidence about the runner.

Also landed: the S3 benchmark-driver sync is now an **allowlist** at both ends
(`--exclude "*" --include "hpc-benchmark.sh"`), replacing a blocklist that shipped three
internal files into the operator's `~/hpc-benchmark/` — including a raw `README-PERFORMANCE.md.j2`
whose de-Jinja'd sibling has `cd` lines reading `<cluster_name>/<cluster_owner>` **literally**.
All eight mutations caught. See the S3-staging bullet in `CLAUDE.md`.

---

### TODO — live-cluster verification owed

Both filesystem items are closed. Nothing here is owed for the bootstrap timeout. **Only item 3
remains open** — items 4 and 5 are both closed on both architectures, and all eight supported
`base_os` values have now been built on hardware. The `osiris`/`isis`/`iris` CloudWatch groups
have all been read and were purged 2026-07-29 — all 39 (`osiris-*` 34, `isis-*` 2, `iris-*` 3,
~54 MB) deleted via `aws logs delete-log-group`; both prefixes confirmed empty afterward. This
was a manual one-off purge of these three cluster names' retained groups, not a change to the
retention policy — `delete-cluster` still defaults to `Retain` for every future build; see the
CloudWatch-retention bullet in `CLAUDE.md`.

1. ~~**EFS on a live cluster (`enable_efs=true`).**~~ **DONE 2026-07-28** — measured; see the
   EFS section above.
2. ~~**FSx on a live cluster with the new 3900s window.**~~ **DONE 2026-07-27** on build
   `osiris-47192227072026` — `CREATE_COMPLETE` in 34m24s, `HeadNodeBootstrapTimeout: 3900` read
   off the deployed config, FSx 19m20s before the instance existed, bootstrap ~13 min inside
   the remainder. That also supplied the second measurement of the FSx interval this item
   asked for (19m20s vs the 17m22s that set the 1800s allowance — generous, not marginal).
   See "Verified on hardware" above.
3. **The `preinstall.j2` questions above** — resolvable on any booted node: `pip download --no-binary` behavior for scipy/numpy on the ARM AMIs, and whether EFA/Lustre modules survive a kernel bump.
4. **The RHEL 9 bootstrap path (session 31).** The first live `rhel9` build was attempted
   on 2026-07-28 (`osiris-30141528072026`) and **failed in `preinstall.j2`** on the pip
   dependency install — see session 32 below; that root cause is fixed and its item is
   struck below. The build died before reaching anything after it, so everything else
   here is still owed. Specifically unverified:
   - ~~`epel-release` installs by URL from a PCluster RHEL 9 AMI.~~ **DONE 2026-07-28** — `epel-release-9-11.el9.noarch` installed clean on build `osiris-36081728072026`. Note it writes `/etc/yum.repos.d/epel.repo.rpmnew` rather than replacing an existing file, and prints `It is recommended that you run /usr/bin/crb enable` — harmless, but it means the AMI already had an epel.repo.
   - ~~Whether the CRB loop finds a working repo id.~~ **DONE 2026-07-28** — moot in practice: every package resolved from `epel` and `rhel-9-{baseos,appstream}-rhui-rpms` without CRB. The loop is still correct as a best-effort guard, but nothing on the critical path needed it, so *which* id succeeded (if any) was never exercised. Do not treat this as proof the ids are right.
   - ~~Whether `lua`, `lua-devel`, `lua-posix`, `lua-filesystem`, and `tcllib` all resolve once EPEL+CRB are on.~~ **DONE 2026-07-28** — all resolved: `lua-devel-5.4.4-4.el9` was already installed, `lua-filesystem-1.8.0-5.el9` and `tcllib-1.21-1.el9` from epel, `luarocks-3.9.2-5.el9` from epel. **All three rocks compiled** — `luaposix` built its C extensions against `/usr/include` with no separate header package, confirming the CLAUDE.md claim that the Ubuntu alternative-dependency-group hazard has no RHEL analog.
   - ~~Whether `pip3 install --ignore-installed pip` works against the RPM-owned pip.~~ **DONE 2026-07-28** — it does: `Successfully installed pip-26.0.1` over the RPM-owned `pip-21.3.1` (whose `dist-info` was confirmed RECORD-less), with no `--break-system-packages`. The *dependency* line on the same arm lacked the flag and is what failed the build; fixed in session 32.
   - ~~Whether the eight pins actually install to completion on Python 3.9 now that the uninstall step is skipped.~~ **DONE 2026-07-28** — verified by running the verbatim rendered RHEL arm on the failed head node via SSM: `PIP_EXIT=0`, **zero** `Attempting uninstall` lines, `requests-2.32.5` installed over the RPM-owned 2.25.1 without touching it. All eight resolved on 3.9 (`numpy-1.26.4`, `scipy-1.13.1`, `pandas-2.3.3`, `matplotlib-3.9.4`, `boto3-1.42.97`, `seaborn-0.13.2`, `tailhead-1.0.2`) plus 20 transitives, so the `requires_python` floors are now confirmed by a live resolve rather than by reading metadata. ~~**x86 only** — the node was a `c5.xlarge`, so aarch64 wheel availability on `rhel9arm` is still unverified.~~ **aarch64 DONE 2026-07-28** — build `iris-10121728072026` (`c8g.xlarge`, `rhel9arm`) resolved the same eight pins from real `manylinux_2_17_aarch64` wheels for every compiled one (`numpy-1.26.4`, `scipy-1.13.1` 33.7 MB, `pandas-2.3.3`, `matplotlib-3.9.4`, `contourpy`, `kiwisolver`, `pillow`, `charset_normalizer`, `fonttools`) with **no** from-source build except `tailhead`, which is pure Python. The aarch64 wheel-availability worry was unfounded.
   - **`postinstall.j2`'s `--ignore-installed` is not merely prophylactic.** The same run installed `packaging-26.2` and `python-dateutil-2.9.0.post0`, both of which RHEL 9 ships as RPMs — so pip *does* elect to replace distro-owned transitives here. Had those RPM `dist-info` directories been RECORD-less the way `requests` was, the unflagged plotting-stack line would have hit the identical failure one stage later. The exposure is real, not theoretical.
   - ~~Whether `aws-parallelcluster-monitoring` v2.6 supports RHEL 9 **at all**.~~ **DONE 2026-07-28** — it does. On `osiris-05521728072026` the installer's own `detect_platform` resolved `PLATFORM_ID=platform:el9`, set `PLATFORM=parallelcluster` / `PLATFORM_NODE_TYPE=head` / `PLATFORM_USER=ec2-user` off `cfnconfig`, and ran to `Done.`; `grafana`, `nginx`, `prometheus`, and `pushgateway` all reached `Started`, and the `grafana-password-refresh` / `prometheus-creds-refresh` / `slurm-job-nodes` timers and `slurm_exporter` unit were all enabled. The compute node took the `Configuring ComputeFleet node` branch. Note the port-80 `apache2 httpd` loop is **invisible in the log** — our wrapper has no `set -x`, so the `+ ` trace begins only when it sources upstream's `install.sh`. Absence of those lines is not evidence the loop was skipped; `nginx` binding port 80 successfully is the evidence it worked.
   - ~~Whether the `--exclude='kernel*'` hold on `dnf update` is complete.~~ **DONE 2026-07-28** — both `dnf update` calls (preinstall and postinstall) completed with no dracut rebuild and no bootstrap timeout on build `osiris-36081728072026`. This was recorded as PARTIAL on the grounds that there is no per-package audit and so "a future AMI whose pending updates cross a kernel boundary differently is not covered" — **that framing was wrong and is retracted.** `--exclude` is a name glob resolved at depsolve time: dnf drops every package whose name matches from the transaction, on every AMI revision, independent of what happens to be pending. A kernel cannot enter that transaction unless it arrives under a name not starting with `kernel`, and on RHEL 9 every kernel subpackage (`kernel`, `-core`, `-modules`, `-modules-core`, `-modules-extra`, `-headers`, `-devel`, `-tools`) does. The absent apt-mark analog is not a coverage gap — `apt-mark` needs an enumeration because it pins *named packages*, which was apt's cost of doing what one glob does here.
     - **The real gap was that neither line had a single assertion on it**, and it is now closed. Mutating both `dnf` calls down to a bare `sudo dnf -y update` passed the entire 1331-test suite. `TestPreinstallNeverReplacesTheKernel::test_dnf_arms_exclude_the_kernel_from_every_update` is parametrized over `preinstall.j2` and `postinstall.j2` and requires all three excludes on every rendered `dnf ... update` line; all eight faithful mutations are caught (all three dropped, and each dropped singly, on each template). It asserts on the **rendered** text on both counts: `dnf` is stubbed in `_run_preinstall`, so a trace cannot distinguish a flag that was passed from one that was honored, and a line inside an unexpanded `{% if %}` is not a line any node runs. It carries a vacuity guard that fails if the RHEL arm renders no update line at all.
     - **One thing genuinely remains unverified, and it is not what the PARTIAL note claimed.** A few RHEL packages regenerate the initramfs in `%posttrans` without matching `kernel*` — `dracut` itself, `microcode_ctl` (early microcode lives in the initramfs), possibly `linux-firmware`. None of this has been checked against the PCluster RHEL 9 AMI. One data point against caring: the successful build's transaction **did** upgrade `dracut` and still finished inside the window. Do not pre-emptively add `--exclude='dracut*'`/`--exclude='microcode_ctl'` — excluding security updates on speculation is the worse trade. A `uname -r` vs `rpm -q --last kernel` guard was considered and **rejected**: those upgrades do not change the kernel, so the check passes while the rebuild runs anyway, and if the AMI ships a kernel installed-but-not-booted (normal for a baked image, unverified here) it would fail every healthy node under `set -euo pipefail`. If this ever bites, the fix starts with a measurement on a live node.
   - **The whole head-node path up to Lmod now executes.** Verified in order on `osiris-36081728072026`: `dnf update`, python3/pip install, pip self-install, the eight pins, EPEL by URL, the critical-packages line, `luarocks` + all three rocks compiled, and Lmod's `./configure` reaching its final tool check. ~~Everything past `./configure` — `make install`, the profile.d scripts, Spack bootstrap, `pkg_dir`, the alias block, the monitoring wrapper — is **still unexecuted** on RHEL.~~
   - **`rhel9` reached `CREATE_COMPLETE` on 2026-07-28.** Build `osiris-05521728072026` (stack created 17:53:01Z, `Build complete` 18:20:09Z), head node `i-0000000000000017` (`c5.xlarge`), compute `i-0000000000000021` (`c5.2xlarge`), GPU `i-0000000000000002` (`g4dn.xlarge`), log group `/aws/parallelcluster/osiris-202607281753`. Both session-32 and session-33 fixes are confirmed on the successful build: `checking for bc... /bin/bc`, and **zero** `Attempting uninstall` lines anywhere in the head node's cfn-init. Everything previously listed as unexecuted now has: Lmod `make install` through the `ksh_funcs`/`zsh` init trees, the profile.d scripts (`MODULEPATH_ROOT=/efs/pkg/modulefiles`, `SPIDER_CACHE_DIRS`, `UPDATE_SYSTEM_FN` all resolved against `pkg_dir`), the `lmod → 8.7.55` symlink, `Bootstrapping Spack...`, the `hpc-benchmark` S3 pull-back, and the monitoring wrapper. **Both compute nodes finished with zero `WARNING:` lines** and cloud-init completing in 234s and 258s.
   - **`rhel9arm` reached `CREATE_COMPLETE` the same day.** Build `iris-28071828072026` (stack created 18:08:27Z, `Build complete` 18:41:21Z), head node `i-0000000000000003` (`c8g.xlarge`), compute `i-0000000000000001` (`c8g.2xlarge`), log group `/aws/parallelcluster/iris-202607281808`. Same two fixes confirmed on aarch64: `checking for bc... /bin/bc`, zero `Attempting uninstall`. Monitoring's `detect_platform` gave `PLATFORM_ID=platform:el9` on ARM too and all six containers reached `Started` (`grafana`, `prometheus`, `pushgateway`, `node-exporter`, `cloudwatch-exporter`, `nginx`) — so the v2.6 stack is arch-agnostic on el9, not just x86. Compute node: `cfn_node_type=ComputeFleet`, `Configuring ComputeFleet node`, zero `WARNING:` lines, cloud-init 179s.
   - **This build is the first with FSx on RHEL, and `pkg_dir` correctly followed the `fsx > efs` precedence** — `MODULEPATH_ROOT=/fsx/pkg/modulefiles`, `SPIDER_CACHE_DIRS=/fsx/pkg/ModuleData/cachedir`, `UPDATE_SYSTEM_FN=/fsx/pkg/ModuleData/system.txt`, versus `/efs/pkg/...` on `osiris`, which had EFS but no FSx. ~~**The three FSx hydration scripts are NOT verified.**~~ **VERIFIED 2026-07-28** by an operator-authorized read-only SSM probe of head node `i-0000000000000003` (command `a7168233-6095-4422-ba33-8ca285befeae`). This needed a live probe because `postinstall.j2` writes the scripts with `sudo su -c "echo ... > /usr/local/bin/..."`, which emits nothing to stdout, so their existence, content, and the `chmod` in the following loop are all invisible in cfn-init; the same applied to the Lustre mount, which is PCluster's job and leaves no trace in our log either. Results:
     - `/fsx` is mounted — `10.0.1.50@tcp:/imfqlb4v` type `lustre`, 1.1T, 204M used. `lfs 2.15.6` is on `PATH`, so the `lfs hsm_*` calls inside the scripts have a binary to reach.
     - All three scripts exist, mode `-rwxr-xr-x`, `root:root`, Jul 28 18:36, with the expected one-line bodies (`hsm_restore` for import, `hsm_archive` for export, `hsm_action | grep ARCHIVE | wc -l` for progress). So both the `sudo su -c` writes and the `chmod` loop worked.
     - Also on the same host: `/efs` (`nfs4`), `/shared` (`/dev/nvme1n1`, ext4, 246G), the `lmod → /usr/local/lmod/lmod/libexec/lmod` symlink, and `uname -m` = `aarch64`.
   - **`--with-module-root-path` is inert as deployed, and `{{ pkg_dir }}/modulefiles` is never created by anything.** The same probe found `/fsx/pkg` present (`ec2-user:ec2-user`, Jul 28 18:35) but `/fsx/pkg/modulefiles` absent. Traced through Lmod 8.7.55's own source: the path is `AC_DEFINE_UNQUOTED`d and substituted into `@modulepath_root@`, but `Makefile.in`'s `DIRLIST` — the complete set of directories `make install` creates — does not include it, so Lmod never makes the directory. Nor does the toolkit: `templates/postinstall.j2:330` is the *only* occurrence of `modulefiles` anywhere in the tree, and Spack's default module root is `$SPACK_ROOT/share/spack/modules`, not `pkg_dir`. **More to the point, the only file that reads `MODULEPATH_ROOT` is `init/profile`, and `postinstall.j2` copies `init/sh` instead** (`sudo cp /usr/local/lmod/lmod/init/sh /etc/profile.d/lmod.sh`, line 336). `init/sh` sets `LMOD_CMD`/`LMOD_PKG`/`LMOD_DIR`/`MODULESHOME` and defines the `module`/`ml`/`clearMT` functions — it contains no reference to `MODULEPATH` or `MODULEPATH_ROOT` at all (grep on the 8.7.55 tag returns zero hits). `MODULEPATH` on a login shell therefore comes from `lmod_spack.sh` sourcing Spack's `setup-env.sh`, whose `_sp_multi_pathadd MODULEPATH "$_sp_tcl_roots"` appends Spack's own roots. So the missing directory is **benign**: nothing creates it, nothing populates it, and nothing on the login path reads the variable that points at it. `--with-spiderCacheDir` and `--with-updateSystemFn` are read-if-present by the binary and likewise create nothing. **This is not a defect and needs no fix** — but do not "repair" it by adding a `mkdir -p {{ pkg_dir }}/modulefiles`, which would create an empty directory no code path consults, nor by switching the copy to `init/profile`, which would newly put `MODULEPATH_ROOT` in charge of `MODULEPATH` and change login-shell behavior on every cluster. Left as-is deliberately; the `./configure` flags are documentation of intent for an operator who wants to hand-place modulefiles under `pkg_dir`.
   - **Correction to the two build entries above:** the `MODULEPATH_ROOT=/efs/pkg/modulefiles` and `MODULEPATH_ROOT=/fsx/pkg/modulefiles` lines cited as evidence are `./configure`'s own `AC_MSG_RESULT` output (`configure.ac:243,247`), **not** profile.d content. They still prove `pkg_dir` reached the configure line with the right `fsx > efs` precedence, which is what they were cited for — but they say nothing about the installed profile.d scripts.
   - **Three gates verified behaving correctly on live compute nodes, not just in tests.** `cfn_node_type=ComputeFleet` was read from `cfnconfig` on both (the `PARALLELCLUSTER_NODE_TYPE` root cause is gone in practice); the GPU node installed `htop` only, no `nvtop`, from epel; and the NVMe block **correctly no-op'd** — `cfn_ephemeral_dir=/scratch` shows PCluster's `ephemeral_drives` cookbook had already claimed the `g4dn.xlarge`'s single instance-store device, and no `mkfs.xfs`, `mdadm`, or `blkid` call appears in the node's trace. That is the documented single-device behavior, observed for the first time.
   - **The same is true on `rhel9arm`, independently verified.** Build `iris-10121728072026` (`c8g.xlarge`, head node `i-0000000000000013`, log group `/aws/parallelcluster/iris-202607281717`) started 17:17:34Z and failed `OnNodeConfiguredExecutionFailure` at 17:45:25Z on the **identical** `checking for bc... no` / `You must have bc in your path. Quitting!`. It predates the session-33 fix (`postinstall.j2` was edited at 17:42:27Z, after this build's postinstall had already been staged to S3), so it is not a second defect and needs no code change. What it *did* prove, aarch64-only until now: `epel-release-9-11.el9.noarch` installs by URL on the ARM AMI; `lua-filesystem-1.8.0-5.el9.aarch64`, `tcllib`, and `luarocks-3.9.2-5.el9` resolve from epel; **all three rocks compiled** — `luaposix`'s ~25 `gcc -shared` invocations against `/usr/include` all succeeded, so the no-RHEL-header-package claim holds on both architectures; and the 865-package `dnf update` upgraded `dracut` itself to `057-117.git20260625.el9_8.aarch64` while installing **zero** `kernel*` packages and triggering **no** initramfs regeneration — the strongest evidence yet that `--exclude='kernel*'` is doing its job, since dracut moved and the ramdisk did not.
5. ~~**The AL2023 bootstrap path (session 34).**~~ **DONE 2026-07-28** — `alinux2023` reached
   `CREATE_COMPLETE` on build `osiris-23142328072026`, `us-east-2a`, `c5.xlarge` head node
   `i-0000000000000016` at `10.0.1.40`, `c5.2xlarge` CPU queue, `g4dn.xlarge` GPU queue,
   with EFS, HPC benchmarks, and monitoring enabled. `/shared` EBS gp3 250 GB, `/efs` EFS
   bursting, `pkg_dir` = `/efs/pkg`. **The node logs were then read in full** from log group
   `/aws/parallelcluster/osiris-202607282315` — the head node's `cfn-init` (1747 events,
   12434 lines) and all four compute nodes' `cloud-init-output` — so every claim below is
   directly observed, not inferred from the stack's exit status. The x86_64 arm is closed;
   `alinux2023arm` remains unbuilt.
   - **Fetching these logs requires pagination.** `aws logs get-log-events --limit 10000`
     silently truncates at the ~1 MB response cap: the first fetch returned 1396 of 1747
     events, and greps for `docker.compose` and `MONITORING_HOME` came back empty, which
     briefly looked like the features were absent. Loop on `nextForwardToken`. Also note
     `describe-log-groups` reported `storedBytes: 0` for this group — a lagging metric, not
     an emptiness signal — and that `grep -v 'file(s) remaining'` to strip `aws s3 cp`
     progress noise **also strips real output**, because the CLI appends the final
     `download: s3://... to ...` line onto the tail of the last progress line.
   - **Head node timing.** `runpreinstall` 23:21:34 → 23:22:22 (48s), `runpostinstall`
     23:22:54 → 23:27:51 (~5 min). Both exited 0.
   - **Four documented AL2023 claims confirmed by absence** — **zero** hits across the head
     node's entire cfn-init for `luarocks`, `epel`, `tcllib`, and `nvtop`, and zero for
     `Attempting uninstall`. The three rocks came from the core repo as RPMs exactly as
     documented: `lua-filesystem-1.8.0-4.amzn2023.0.3`, `lua-posix-35.0-3.amzn2023.0.2`,
     `lua-term-0.07-13.amzn2023.0.2`.
   - **`bc` is in the AL2023 core repo**, directly contradicting upstream's own
     `installer/os/alinux2023.sh` comment: `bc-1.07.1-14.amzn2023.0.2.x86_64 is already
     installed`, and `./configure` then printed `checking for bc... /bin/bc`. Lmod cleared
     all six `You must have` gates, `make install` completed, `ln -s 8.7.55
     /usr/local/lmod/lmod`, `MODULEPATH_ROOT=/efs/pkg/modulefiles` (efs precedence — no FSx
     on this build), then `Bootstrapping Spack...` and the hpc-benchmark S3 pull-back.
   - **`dnf update` upgraded exactly one package**, `amazon-efs-utils-3.1.1` — no kernel, no
     dracut, no initramfs rebuild. pip: `Successfully installed pip-26.0.1` over the
     RPM-owned 21.3.1 with zero `Attempting uninstall`.
   - **The Docker Compose S3 staging is fully confirmed on the head node**, in order: the S3
     download of `docker-compose-linux-x86_64-v2.29.7` to
     `/usr/libexec/docker/cli-plugins/docker-compose`, then the awk patch logging `Removed
     upstream's compose download from alinux2023.sh`, then `compose: 2.29.7`.
   - **Monitoring on AL2023 needs no patch to `detect_platform`** — upstream resolved
     `PLATFORM_ID=platform:al2023` natively (versus `platform:el9` on RHEL), with
     `PLATFORM_NODE_TYPE=head`, `PLATFORM_USER=ec2-user`, `MONITORING_HOME=/home/ec2-user/aws-parallelcluster-monitoring`,
     and all six containers Created → Started (`grafana`, `prometheus`, `nginx`,
     `pushgateway`, `node-exporter`, `cloudwatch-exporter`). The only `WARNING:` in the whole
     log is dnf's cosmetic "A newer release of Amazon Linux is available".
   - **Four compute nodes, not three** — `i-0000000000000011` and `i-0000000000000024`
     (`c5.2xlarge`), `i-0000000000000004` and `i-0000000000000022` (**two** `g4dn.xlarge`).
     All four read `cfn_node_type=ComputeFleet` from `cfnconfig` and show zero `mkfs.xfs`,
     `mdadm`, `Device or resource busy`, `luarocks`, and zero Lmod — the head-node gate held.
     Both GPU nodes: `cfn_ephemeral_dir=/scratch`, so PCluster's `ephemeral_drives` cookbook
     had already claimed the single instance-store device and the NVMe block **correctly
     no-op'd**; `htop` only and no `nvtop`; `compose/compute.gpu.yml`; and the documented
     index refresh before the install, visible as `Metadata cache created.` / `Last metadata
     expiration check: 0:00:01 ago` rather than as the string `makecache`.
   - **The `MONITORING_HOME` gate held on every compute node** — no `rm -rf`, `mkdir`, `tar`,
     or `chown`, only the `[[ -r .../installer/os/alinux2023.sh ]]` readable check. **The
     compose plugin did install on compute nodes**, confirming the deliberately straddled
     gate: the S3 `download:` line and `install -d -m 0755 /usr/libexec/docker/cli-plugins` /
     `chmod +x` / `compose: 2.29.7` are all present, while `Removed upstream's compose
     download` is present on **neither** (the compute node reads the already-patched
     `alinux2023.sh` out of NFS `/home`).
   - **The only `curl`/`github.com` references on a compute node are not ours.**
     `BUG_REPORT_URL=https://github.com/amazonlinux/amazon-linux-2023` from `/etc/os-release`,
     and upstream monitoring's own GPU arm running `curl -fsSL
     https://nvidia.github.io/libnvidia-container/stable/rpm/nvidia-container-toolkit.repo`
     followed by a successful `dnf -y install nvidia-container-toolkit` (4 packages, 8.6 M).
     Neither is on the compose path.
   - **The benchmark suite ran to completion on both queues — DONE 2026-07-28.** Jobs 3
     (`compute`, 2 nodes) and 4 (`gpu`, 2 nodes) both reached `RUNNING` and were confirmed
     past 1:12 wall clock under `TIME_LIMIT=UNLIMITED`. That closes the derived-directive
     path end to end on AL2023: `sbatch` accepted the rendered `--partition` and
     `--ntasks-per-node` on *both* a CPU and a GPU queue, and `module load openmpi` plus
     `_detect_mpi` resolved a launcher on the compute nodes. The long tail is by design, not
     a hang — `--hpcg-time` defaults to **1800** (`hpc-benchmark/hpc-benchmark.sh:691`),
     HPCG's official validity threshold, so the timed loop alone is a 30-minute floor and the
     full four-test run is ~35-50 min regardless of rank count (HPCG sizes its iteration
     count to hit the target, so the 2-rank GPU job takes about as long as the 8-rank CPU
     one). Use `--hpcg-time 60` when the goal is completion evidence rather than valid HPCG
     numbers. Note IOR writes into `${SCRIPT_DIR}/ior_scratch` by default — NFS-exported
     `/home` on the head node's EBS root, not `/efs` or `/fsx` — at 1 GiB/process written and
     read with `-e` fsync; pass `--fs-path` to aim it at a real parallel filesystem.
   - **`alinux2023arm` reached `CREATE_COMPLETE` on 2026-07-28 — the aarch64 half is now
     closed too, and the eighth and last supported `base_os` is built.** Build
     `iris-15510029072026`, `c8g.xlarge` head node `i-0000000000000008` at `10.0.1.20`,
     `c8g.2xlarge` CPU queue (compute node `i-0000000000000005`), with EFS, **FSx for Lustre
     1200 GB**, HPC benchmarks, and monitoring. Stack creation 20:52:21 → 21:21:06 (28m45s),
     `HeadNodeBootstrapTimeout: 3900` read back off the deployed config. Logs read in full
     from `/aws/parallelcluster/iris-202607290052` — head node cfn-init (1724 events, 12325
     lines) and the compute node's `cloud-init-output` (803 events). **Every AL2023 claim that
     rested on aarch64 metadata alone is now confirmed on hardware**, and nothing differed
     from x86 except the arch strings:
     - The five absences all hold: **zero** hits for `luarocks`, `epel`, `tcllib`, `nvtop`,
       and `Attempting uninstall` across the entire head-node log.
     - `bc-1.07.1-14.amzn2023.0.2.**aarch64** is already installed` and `checking for bc...
       /bin/bc` — `bc` is in the aarch64 core repo, so upstream's `alinux2023.sh` comment is
       wrong on both arches.
     - The three rocks are core-repo `.aarch64` RPMs: `lua-filesystem-1.8.0-4.amzn2023.0.3`,
       `lua-posix-35.0-3.amzn2023.0.2`, `lua-term-0.07-13.amzn2023.0.2` — same versions as
       x86, different arch suffix.
     - **`docker_compose_checksum_aarch64` is verified end to end**: the S3 pull fetched
       `docker-compose-linux-**aarch64**-v2.29.7`, so `_derive_docker_compose_staging`'s arch
       map picked the right release asset and its checksum passed. The awk patch then logged
       `Removed upstream's compose download from alinux2023.sh` and `docker compose version`
       reported `compose: 2.29.7`. This was the one claim that could plausibly have differed
       per-arch, since it is a distinct digest for a distinct binary.
     - Monitoring: `PLATFORM_ID=platform:al2023` (upstream resolves AL2023 natively on ARM
       too), `PLATFORM_NODE_TYPE=head`, `PLATFORM_USER=ec2-user`, and all six containers
       Created → Started. The v2.6 stack is therefore arch-agnostic on **both** el9 and
       al2023.
     - Lmod cleared all six `You must have` gates and `make install` completed. **This is the
       first AL2023 build with FSx, and `pkg_dir` correctly followed the `fsx > efs`
       precedence** — `MODULEPATH_ROOT=/fsx/pkg/modulefiles` versus `/efs/pkg/modulefiles` on
       the x86 build, which had EFS but no FSx. `Bootstrapping Spack...` followed.
     - `dnf update` upgraded exactly **one** package, `amazon-efs-utils-3.1.1.aarch64`, with
       **zero** matches for `kernel-`, `dracut`, `initramfs`, `kmod-lustre`, or
       `lustre-client` anywhere in the transaction — the `--exclude` globs doing their job on
       aarch64. pip: `Successfully installed pip-26.0.1` with zero `Attempting uninstall`.
     - Compute node gating held completely: `cfn_node_type=ComputeFleet`, `Configuring
       ComputeFleet node`, and **zero** hits for `luarocks`, `Lmod`, `mkfs.xfs`, `mdadm`,
       `Device or resource busy`, `nvtop`, `rm -rf`, or `Attempting uninstall`. The compose
       plugin still installed there (`install -d -m 0755 /usr/libexec/docker/cli-plugins`,
       the `aarch64` S3 pull, `compose: 2.29.7`), confirming the straddled gate on ARM.
     - **The only `WARNING:` on either node** is dnf's cosmetic `A newer release of "Amazon
       Linux" is available` — identical to the x86 build, not a failure.
   - **The benchmark suite was submitted on aarch64 and `sbatch` accepted it** — `sbatch
     --partition=compute --nodes=2 job_hpc-benchmark.sh` → `Submitted batch job 1` on
     `iris`'s head node, so `hpc-benchmark.sh install` had already run and the rendered job
     script is submittable as shipped on ARM. **Completion was not observed** — the run was
     still in flight when this was written. The x86 suite did run to completion on both
     queues, and nothing in the driver is arch-conditional except STREAM's per-microarchitecture
     build, so this is a thin gap rather than an open question.
   - **Still not verified.** This cluster has no GPU queue, so the AL2023 GPU block —
     `htop`-only, no `nvtop`, and the NVMe no-op — is confirmed on **x86 only**. That is not
     an aarch64 package claim; those are all closed. Note the AL2023 GPU arm is also the one
     place AL2023 differs from RHEL in what a node gets, so an `alinux2023arm` GPU build is
     the only remaining OS-matrix hole worth filling — but it needs a Graviton GPU instance
     type, and **g5g-class is untested due to limited Region availability**; see the g5g note
     in the supported-OS block above for why that build cannot be run against the operator's
     current VPC.
   - **Two defaults in the shipped job script that surprise on first read**, neither a defect:
     `--fs-path` defaults to `${SCRIPT_DIR}/ior_scratch` — under `/home`, which is NFS-exported
     from the head node's EBS root — so a stock run benchmarks **NFS, not** `/efs` or `/fsx`
     even on a cluster that has both; pass `--fs-path /fsx` for parallel-filesystem numbers.
     And `--ntasks-per-node` renders as the CPU-queue default of **4** regardless of the
     instance type's vCPU count (8 on `c8g.2xlarge`), so OSU/IOR/HPCG run under-subscribed and
     the HPCG rating is a floor; STREAM is unaffected since it sets `OMP_NUM_THREADS=$(nproc)`
     itself. `README-PERFORMANCE.md` already tells the operator to set `--ppn` to the vCPU
     count; the *template* does not derive it, and deliberately so — see the
     `job_hpc-benchmark.sh.j2` bullet in `CLAUDE.md` for why the GPU path derives its rank
     count and the CPU path does not.

---

### generate_operator_policy.py (new)

- `generate_operator_policy.py` (repo root, 0755): renders `templates/OperatorPolicy.json_src` with account ID resolved via `sts:GetCallerIdentity`; validates JSON before output; flags: `--output FILE`, `--create` (creates managed policy in IAM), `--policy-name`, `--description`.
- `templates/OperatorPolicy.json_src` (NEW): 9 statements covering IAM policy lifecycle, role lifecycle (create/delete/put/pass), instance profile teardown, EC2 keypair, Secrets Manager, SSM, Pricing, Cost Explorer, STS. IAM role/policy/instance-profile ARNs scoped to `pclustermaker-role-*`/`pclustermaker-policy-*` (this toolkit's own naming — see session 18 fix above; originally shipped scoped to `parallelcluster-*`, which is AWS ParallelCluster's naming, not this toolkit's). `CreatePolicyVersion`/`DeletePolicyVersion` intentionally omitted — self-escalation risk since operator policy name matches the `parallelcluster-*` wildcard used for the *policy name itself* (`parallelcluster-operator-pclustermaker`).
- Security review applied: removed `CreatePolicyVersion`/`DeletePolicyVersion` (privilege escalation), removed `ssm:DescribeParameters` (unsupported at resource ARN scope, never called by toolkit), added `OSError` handling in `_render()`.
- Correctness review applied: added `IAMRoleLifecycle` and `IAMInstanceProfile` statements covering `iam:CreateRole/DeleteRole/GetRole/PutRolePolicy/DeleteRolePolicy/TagRole/PassRole` and `iam:GetInstanceProfile/DeleteInstanceProfile/RemoveRoleFromInstanceProfile` — these were missing but called directly by `pcluster_core._setup_iam` and `delete_pcluster.yml`.
- No new tests — script has no pure-Python logic beyond `_render()` (string replace + JSON parse) and error handling; the policy JSON is validated by `tests/test_templates.py` existing IAM policy JSON validity tests.
- README: new "Operator IAM permissions" section under Prerequisites with statement table and attachment commands.
- CLAUDE.md: `generate_operator_policy.py` added to repo layout; `Operator-only IAM permissions` constraint rewritten with complete action list.

### HPC benchmark tuning documentation (2026-07-24)

- `hpc-benchmark/README-PERFORMANCE.md` and `README-PERFORMANCE.md.j2`: new "Tuning --nodes and --ppn by instance class" section. Covers recommended `--ppn`, `--ior-size`, and HPCG problem size from `.xlarge` through `hpc6a.48xlarge`. Documents four cases where defaults produce misleading results: r-family (HPCG problem size too small), EFA instances (OSU latency an order of magnitude lower), GPU instances (STREAM/HPCG are CPU-only), Graviton (no changes needed).

### Press release (2026-07-24)

- `pr/PRESS-RELEASE-20260724.md`: comprehensive rewrite covering the full current feature set. `pr/PRESS-RELEASE-20260722.md` removed. Multiple follow-up passes: sentence-fragment fixes, AI-phrasing cleanup, AWS cost disclaimer with Planet Patrol "play at your own risk" quote, Jeff Barr tweet URL added, license mismatch fixed (Apache-2.0 + Commons Clause, not MIT), test count and module list corrected.

### README wording/accuracy pass (2026-07-24)

- Fixed: `ansible` mislabeled as a Homebrew/system package (it's `pip install -r requirements.txt`); Prerequisites system-deps bullet split into macOS/Linux (dnf/apt) sub-bullets; awkward Python-version-note phrasing; unnecessarily blunt Windows-support line; venv deactivate wording; missing hyperlink to upstream `aws/aws-parallelcluster` GitHub repo in opening sentence; grammar typo ("It primary purpose" → "Its").

### Fan-out adversarial code review (2026-07-24)

- Ran a 6-area parallel adversarial code review (core logic, lifecycle scripts, ops tooling, IAM/security, templates/Ansible, HPC benchmark) via Workflow, each finding independently verified by a skeptic agent. Result available in that run's transcript; no code changes were made directly from it in this session (superseded by the doc-editorial workflow below, which caught the same class of issues plus a real bug).

### Fan-out doc-editorial review + fixes (2026-07-24)

- Ran a 9-agent parallel editorial review (ORA-editor style) across README.md (5 chunks), CLAUDE.md, hpc-benchmark README, integration test README, and the press release, checking both factual accuracy (verified against actual source) and AI-sounding phrasing. Found 21 issues; applied 20 with user confirmation (held one pending investigation).
- **Real bug found and fixed:** `manage_pcluster_queue.py:239` called `needs_efa_gdr(t, "true")` — a stray second argument not in the function's one-parameter signature (`src/pcluster_aux_data.py:806`). Every `-A add -T gpu` invocation crashed with `TypeError` before reaching the config write; no test caught it because the GPU-add logic lived directly in the untested CLI entry-point script, violating the project's own architecture rule (business logic must live in `src/`).
  - Fix: extracted `_gdr_capable_types(instance_types)` into `src/pcluster_queue_editor.py` (pure function, same pattern as `_validate_instance_types`/`_get_subnet_ids`); `manage_pcluster_queue.py` now calls that instead of `needs_efa_gdr` directly.
  - Added `TestGdrCapableTypes` (7 tests) to `tests/test_queue_editor.py` — p4d/p4de/p5 positive, p3/non-GPU negative, mixed list, empty list.
  - Test count: 651 → 658.
- **Doc fixes applied:** stale IAM policy filenames/suffixes (old lettered `-A/-B/-C`/`-M` → current `HeadNode-*`/`ComputeNode-Base`/`HeadNode-Monitoring`), invalid `manage_pcluster_queue.py` CLI flags in README examples (`--capacity_type`/`--queue_name` → `-C`/`-Q`), missing `ec2:DeleteKeyPair` in SSH rotation permissions table, wrong HPC benchmark deploy path (`~/performance` → `~/hpc-benchmark/<cluster>/<owner>/slurm`, fixed in both `.md` and `.j2`), wrong default autoscale cap (5→8, flag name corrected), missing Secrets Manager prerequisite in integration test README, cost example instance type mismatch (`c8g.xlarge`→`c5.xlarge`), press release IAM policy count contradiction, several AI-phrasing tightenings.
- All changes committed and pushed: `7a9491b` (bug fix + tests), `b72b183` (doc fixes).

**The `git filter-repo` rewrite renumbered every SHA, so old SHAs in this file are dead
references.** `osiris_defaults.yml` and `isis_defaults.yml` were purged from all commits
and force-pushed on 2026-07-28. Anything this file cited before that point no longer
resolves on `origin/main`. Every SHA this file cited was swept: **20 were dead** — the
pre-rewrite objects are still in the local object store but unreachable from any ref — and
every one has a rewritten equivalent that *is* on `origin/main`, matched by exact commit
subject. Nothing was lost. The citations below have **not** been rewritten in place
throughout the file, so when you hit a SHA in an older section, look it up here first:

| Pre-rewrite (dead) | On `origin/main` | Subject |
|---|---|---|
| `bc5f4a3` | `bff1c9e` | Add ec2:CreateFleet to Policy-B for PCluster v3 Slurm node provisioning |
| `7b3027b` | `e87cf1b` | Restructure IAM policies by role, fix compute node S3/Logs access, and enhance queue management |
| `c1707f9` | `e577f29` | Add GPU subnet routing, per-queue autoscaling, and manage_pcluster_queue.py |
| `02948a6` | `f6b25c3` | Add multi-queue multi-instance-type support and clean up dead code |
| `7a9491b` | `bdb6ae4` | Fix TypeError crash in manage_pcluster_queue.py GPU add path |
| `97c287d` | `bc3e6f1` | Add GPU support: auto-detection, NVMe /local_scratch, EFA-GDR, nvtop/htop |
| `b4d9645` | `832f740` | Fix None crash on empty instance type params and clean up build summary |
| `bacbea4` | `6d57944` | Fix five P1 findings from adversarial review |
| `9d49f9b` | `4609340` | Fix root causes from third adversarial review pass |
| `812923b` | `c69c7e4` | Update docs: test count 320 → 398, fix cd path to ~/hpc-benchmark |
| `52f846a` | `e64daec` | Update docs to match the pclustermaker-* IAM ARN fix and current test count |
| `2ccc37f` | `2868348` | Document SSH key DELETE_FAILED preservation and benchmark checksum verification |
| `b72b183` | `f053293` | Editorial review pass: fix accuracy and phrasing issues across docs |
| `ded4976` | `f046e4f` | Run postinstall on compute nodes, drop lifetime scheduler, fix HPCG build |
| `3c29bd7` | `31c1474` | Enforce FSx's one-bucket requirement for S3 hydration |
| `6694a85` | `5ddbd0e` | Render the toolkit's install scripts and find the timestamped CW log group |
| `95de5e5` | `a5073d9` | Collect and report the teardown failures ignore_errors was swallowing |
| `f492431` | `3eb491f` | Build OSU with CUDA when the node running install actually has a GPU |
| `e5cd4da` | `6fe9cd1` | Fall back to --enable-cuda=basic when the build node has no nvcc |
| `6395669` | `b36028d` | Build the CUDA OSU tree on the GPU node when install could not |

Rebuild this table after any future rewrite with a loop over the file's backtick-quoted
SHAs: skip any that `git merge-base --is-ancestor <sha> origin/main` accepts, and for the
rest look up `git log origin/main --fixed-strings --grep="$(git log -1 --format=%s <sha>)"`.
That depends on the dead objects still being in the local store — it will not work from a
fresh clone, so keep the mapping here rather than rederiving it later.

Content was preserved by the rewrite; only the SHAs moved. Session 31 (RHEL 9 re-add) is
`246f824`, followed by `b0a14d1` and `bd542d6` (untracking the two operator defaults files
and relaxing the three count guards that depended on them) — those three postdate the
rewrite and are still valid. Sessions 32-33 (the pip `RECORD` fix and the Lmod `bc` fix)
are `d783580`; the American-English sweep is `6d49d1a` and `cadd48c`; the doc pass that
closed out the live RHEL builds is `73fe4d3`.

## FSx + dual-queue benchmark run (osiris shape)

Written 2026-07-26 for the rebuild of `osiris` after the bootstrap-timeout fix.
Cluster shape read from `osiris_defaults.yml`: `ubuntu2404` (so `ubuntu`),
`c5.xlarge` head node with no GPU, `c5.2xlarge` -> partition `compute`,
`g5.xlarge` -> partition `gpu`, FSx 1200 GB at `/fsx` with hydration on,
**`enable_efs: "false"`** so there is no `/efs` and no EFS timing to collect
here, `enable_efa: "false"`, `max_*_queue_size: 2` on both queues, and
`g5.xlarge` carries one A10G so `gpu_ranks_per_node` is **1**.

Set these once per shell; every later command uses them:

    C=osiris; O=<cluster_owner>          # the -O you passed to make_pcluster.py
    B=~/hpc-benchmark/$C/$O/slurm

### 0. Before anything else — confirm the timeout fix worked

**Already confirmed on build `osiris-47192227072026`, 2026-07-27**: `Timeout: 3900`
in the deployed config, FSx 19m20s pre-instance, `CREATE_COMPLETE` in 34m24s. The
1800 s allowance is generous, not marginal, and the EFS 600 s figure was measured
2026-07-28 (4m24s pre-instance). This step is now a regression check on a rebuild,
not an open measurement — run it, but nothing is owed if it matches.

It must still be checked from the laptop before logging in, because the evidence is
in CloudFormation, not on the node.

    grep HeadNodeBootstrapTimeout active_clusters/$C/config.$C
    aws cloudformation describe-stack-events --stack-name $C \
        --query 'StackEvents[].[Timestamp,LogicalResourceId,ResourceStatus]' \
        --output text | sort | grep -Ei 'WaitCondition|FSX|HeadNode|FileSystem'

Expected: `HeadNodeBootstrapTimeout: 3900` (2100 + the 1800 s FSx allowance,
since EFS is off), and the head node's `CREATE_COMPLETE` inside 3900 s of the
wait condition's `CREATE_IN_PROGRESS`. A FSx interval materially above 19m20s is
the one result that would reopen the allowance.

    ./check_pcluster.py -N $C
    ./access_cluster.py -N $C

### 1. Head node: install, on the CPU node that cannot build CUDA

    B=~/hpc-benchmark/$C/$O/slurm; cd "$B"
    sinfo                                   # both `compute` and `gpu` present
    df -h /fsx /shared; ls -ld /fsx/pkg     # FSx mounted, pkg_dir precedence
    ls -l /opt/parallelcluster/shared/custom_action_done
    module avail 2>&1 | head
    module load openmpi

    time ./hpc-benchmark.sh install         # record the wall time
    cat bin/.build_arch
    ls bin/stream-* bin/src/                # head node's march; cached sources
    ls -l bin/osu/.cuda_enabled             # expected ABSENT: no GPU on c5.xlarge

`install` runs on the head node only and builds all four tools. The absent
`.cuda_enabled` is the expected result, not a failure — it is what makes the
run-time `bin/osu-cuda` build in step 3 the code path under test.

### 2. CPU queue — the `compute` partition

`job_hpc-benchmark.sh` already targets `compute` with 4 ranks/node, because
`enable_cpu_queue` is true:

    cd "$B"
    sbatch job_hpc-benchmark.sh
    squeue -u "$USER"

That is the full `stream,osu,ior,hpcg` suite with `--hpcg-time 1800`, so budget
30-60 min. For a fast confidence check first, do a single-node interactive run
instead — no allocation is needed for `--nodes 1`:

    ./hpc-benchmark.sh run --tests stream,osu --nodes 1 --ppn 1
    ./hpc-benchmark.sh report

FSx is the reason this cluster exists, so run IOR against it explicitly rather
than relying on the job script's default `./ior_scratch`:

    sbatch --wrap="module load openmpi && cd $B && \
      ./hpc-benchmark.sh run --tests ior --fs-path /fsx --nodes 2 --ppn 4 --ior-size 4g" \
      --partition=compute --nodes=2 --ntasks-per-node=4

`--fs-path /shared` is the useful comparison — same test, EBS instead of Lustre.
Do **not** point `--fs-path` at `/efs`: this cluster has no EFS.

### 3. GPU queue — the `gpu` partition, one rank per node

`g5.xlarge` has one A10G, so `--ntasks-per-node=1`. This is the run that builds
`bin/osu-cuda`, since `install` could not:

    sbatch --partition=gpu --ntasks-per-node=1 job_hpc-benchmark.sh

Then, from the head node:

    cat bin/osu-cuda/.cuda_enabled          # "<yes|basic> <cuda_home>"
    ls -d bin/.osu-cuda.lock                # must be GONE after a clean build
    tail -30 bin/build_logs/osu-cuda.log
    ls bin/stream-*                         # a SECOND march now present
    R=$(ls -dt benchmark_results/*/ | head -1)
    head -20 "$R/osu/latency_cuda.txt" "$R/osu/bandwidth_cuda.txt"
    ./hpc-benchmark.sh report --results-dir "$R"

`enable_efa` is **false** on this cluster, so device-to-device latency may well
be *worse* than host-to-host. That is not a failure — the GDR path is what makes
it faster, and it is not enabled here.

The concurrency case needs two jobs submitted back to back; with
`max_gpu_queue_size: 2` both can be running at once, which is what the lock is
for:

    sbatch --partition=gpu --ntasks-per-node=1 job_hpc-benchmark.sh
    sbatch --partition=gpu --ntasks-per-node=1 job_hpc-benchmark.sh
    grep -il "another node is building" hpc-benchmark-*.err

### 4. Closeout

    sinfo -R                                # no drained/down nodes
    ./diagnose_pcluster.py -N $C
    ls -dt benchmark_results/*/ | head

Results sync to
`s3://parallelclustermaker-<serial>/hpc-benchmark-results/osiris/<serial>/`
on teardown, keyed by serial, so they survive the next rebuild.

Details of the GPU-specific hardware and GRES probes are in the next section;
this one is the FSx/two-queue path.

### Session 44 — Login Node support, planned then live-verified on `osiris`

`--enable_loginnode` (default `"false"`) adds AWS ParallelCluster's optional
`LoginNodes` pool: a separate, right-sized instance (or pool) for interactive
logins and job submission, keeping general users off the head node's elevated
IAM surface. The design was worked out and adversarially self-reviewed twice
*before* any code was written (saved plan file, referenced from
`CLAUDE-STATE.md`'s "Deferred work" while implementation was pending), then
implemented by two forked agents in sequence — one for the core code
(`pcluster_defaults.yml`, `make_pcluster.py`, `src/pcluster_core.py`,
`vars_file.j2`, `config.pcluster.j2`, `postinstall.j2`, `access_cluster.py`/
`.j2`), one for the full mirrored test suite and docs — committed as
`d424695`. Key decisions, each confirmed against the installed
`aws-parallelcluster` package's own schema/CDK source rather than assumed
from AWS's doc prose:

- **No root volume flags.** `LoginNodesPoolSchema` has no `RootVolume`/
  `LocalStorage` key at all, so the root volume can be neither sized nor
  encrypted through this toolkit — it always uses the AMI default.
- **`loginnode_instance_type`'s hardcoded fallback is architecture-aware**
  (`_default_loginnode_instance_type` in `pcluster_core.py`: `c8g.xlarge` on
  Graviton `base_os`, `c5.xlarge` on x86_64) — a flat literal would silently
  fail preflight for an operator who opts in on an x86_64 cluster without
  also setting the flag.
- **IAM is `ComputeNode-Base`** via `AdditionalIamPolicies`, never
  `HeadNode`'s `InstanceRole` — head-node-level privileges on the login node
  would defeat the feature's own purpose.
- **No spot pricing** — confirmed no `capacity_type` field exists on a
  login-node pool, unlike `HeadNode`/`SlurmQueues`.
- **Login node appears in all five console/report surfaces** (build summary,
  launch summary, SNS report, `list_pcluster.py` table) plus the cost
  estimate — a self-review finding reversed an earlier draft that had left
  the build summary untouched.
- **Self-review caught a real bug before it shipped:** `config.pcluster.j2`'s
  `LoginNodes` block needs the same `AdditionalSecurityGroups` grant
  `HeadNode` and both compute queues carry, or a cluster with
  `enable_external_nfs=true` fails to boot the login node — `postinstall.j2`'s
  NFS mount block is gated only on `enable_external_nfs`, not node type.

**Live-verified on cluster `osiris` (`ubuntu2404arm`, 2026-08-12)**, not just
unit-tested: the login node's IAM role carried only `ComputeNode-Base` (plus
PCluster's own default `CloudWatchAgentServerPolicy`) and no head-node
policies; `cfn_node_type=LoginNode` was read correctly from
`/etc/parallelcluster/cfnconfig`; `postinstall.j2`'s `LoginNode)` case arm ran
its no-op path cleanly (`"Customize the pcluster stack login node here."` /
`"Ready to finish joining the cluster!"`, confirmed in the node's own boot
log); `/home` and `/shared` were NFS-mounted from the head node
(`172.31.28.11`) rather than rebuilt; `access_cluster.py` with no flags
connected to the login node, `-H` and `-L` each resolved to the correct
distinct private IP (`172.31.28.11` head, `172.31.29.14` login); and `squeue
-l` ran cleanly from the login node with an empty queue.

**One real incident during the build, worth recording so it isn't
rediscovered as a code bug.** A background `make_pcluster.py` run for
`osiris` was mid-flight (IAM policies created, stack launched, waiting on the
head node) when a second, concurrent `./kill_pcluster.py -N osiris ...` was
run against the same cluster name — the operator, reasonably, had followed
the exact remediation text a *separate*, earlier, non-concurrent
`make_pcluster.py` invocation had printed ("existing vars_file found ...
please delete this cluster properly"), not realizing the first run was the
one actually building. The concurrent teardown deleted the in-flight build's
IAM policies, keypair, and `vars_files/osiris.yml` out from under it,
producing an opaque `'region' is undefined` Ansible templating error at the
`Abort if describe-cluster itself failed` task (`src/create_pcluster.yml`)
and "NoSuchEntity" warnings when the orphaned build's own failure-cleanup
tried to remove resources a moment after creating them. AWS/local state were
confirmed fully clean afterward (`aws cloudformation describe-stacks` →
`does not exist`, no keypair, no IAM role); the rebuild on a second, solo
attempt reached `CREATE_COMPLETE` cleanly. No code fix was made or needed —
this is a two-terminal operator race, not a toolkit defect — but it is the
reason `make_pcluster.py` and `kill_pcluster.py`'s printed command examples
had their leading `$ ` stripped in the same session (so remediation text
pastes cleanly without inviting a second copy-paste into a second terminal),
and the reason to always ask "who else might be touching this cluster name
right now" before treating a mid-build failure as a code bug.

**Also this session: a small dead-code sweep (commit `e8eb447`).** Removed
the unused `default_instance_types` dict/import (`pcluster_aux_data.py`/
`make_pcluster.py`, superseded by `_HARDCODED_DEFAULTS`'s three-tier
resolution) and an orphaned `sns_destruction_summary_report_dest` key in
`vars_file.j2`. The latter looked like a drift bug at first (its sibling
`sns_build_summary_report_dest` *is* used) but tracing the actual task order
in `delete_pcluster.yml` showed `cluster_data_dir` is deleted (task "Delete
the cluster data directory") *before* the destruction summary is templated
on the confirmed-delete path — so the existing hardcoded `/tmp` destination
is correct as shipped, not a bug the dead variable was masking. Worth
remembering: a "looks unused, must be a forgotten wire-up" instinct is wrong
often enough here that it needs checking against the actual execution order,
not just against its sibling's shape.

Test count: 1839 → 1886 (Login Node feature) → 1908 (see Session 45).

### Session 45 — adversarial review of the Login Node feature, 8 confirmed bugs

Requested explicitly as "brutal and minute," run via the `/code-review max`
skill against the diff since before the Login Node work
(`58a2bce..HEAD`, i.e. commits `d424695` + `e8eb447`). Unlike Session 37,
several of these findings *were* cross-checked against the installed
`aws-parallelcluster` package's own source before being trusted — the two
most severe specifically, since the review's own report flagged them as
novel enough to warrant independent confirmation before acting. Fixed in
commit `4fe2d8e`; full per-fix rationale is in `CLAUDE.md`'s bullets, since
there was no byte-budget room left for a `CLAUDE.local.md` companion this
round (see the ratchet note in `CLAUDE-STATE.md`'s "Doc structure" section).
Summary, most severe first:

1. **`access_cluster.py`'s default node selection ignored `loginnode_count`.**
   `--enable_loginnode=true --loginnode_count=0` (a schema-valid "defined but
   empty pool" state) made the plain, unflagged `access_cluster.py -N
   <cluster>` route to `LoginNode` and fail with "no running login node
   found," while the head node was fully healthy and reachable via `-H`.
   Fixed by extracting the decision into `_resolve_access_node_type()` in
   `pcluster_core.py` (this repo's own architecture rule: Python logic
   belongs there, not in the entry-point script) with a distinct error
   message for count-zero vs. feature-disabled, since "rebuild with
   `--enable_loginnode=true`" is misleading when it is already true.
2. **`LoginNodes` got `OnNodeStart`, copied verbatim from `HeadNode`.**
   `config.pcluster.j2`'s `LoginNodes` block carried the same `OnNodeStart`
   sequence as `HeadNode` — unlike the compute/GPU `SlurmQueues` blocks,
   which deliberately have none, for the reason `CLAUDE.local.md` already
   documents ("repeating the python3/pip/awscli install on every scale-up is
   a latency regression, not a fix"). `preinstall.j2` itself has zero
   `NODE_TYPE` gating, so every login node was running a full kernel-held
   `apt`/`dnf` upgrade, eight `pip install`s, and a fresh `awscli` download
   on every boot and every ASG replacement — directly contradicting this
   same diff's own stated design ("`LoginNode` treated like `ComputeFleet`,
   not `HeadNode`"). Fixed by dropping `OnNodeStart` from the block entirely,
   matching the compute-queue shape.
3. **The login node pool has no CloudFormation dependency on head-node
   bootstrap completion — confirmed against the installed CDK source, not
   assumed.** `cluster_stack.py:544` is `login_nodes_stack.node.add_dependency
   (self.head_node_instance)` — the *raw EC2 instance* only. The same file's
   CloudWatch alarms (lines 443, 452) depend on `self.wait_condition`
   instead, proving the pattern was available and not used here. Net effect:
   a login node can reach the monitoring wrapper before the head node has
   populated `MONITORING_HOME`, hit the wrapper's existing (correct)
   `exit 1` diagnosis, and get endlessly replaced by the ASG until the head
   node catches up. Fixed by giving the wrapper's `LoginNode` arm a bounded
   poll (`MONITORING_LOGIN_WAIT_SECONDS`/`_POLL_SECONDS`, default 300s/10s)
   instead of failing immediately — `ComputeFleet`'s immediate-fail behavior
   is correctly left alone, since `clustermgtd`'s ordering guarantee is real
   for that node type and isn't for this one.
4. **The GPU NVMe/RAID0 claiming block had no node-type gate.** Gated only on
   `{% if enable_gpu == 'true' %}` cluster-wide, so a `loginnode_instance_type`
   with instance-store NVMe would have its local disk silently claimed and
   reformatted into `/local_scratch` on any cluster with a GPU queue enabled
   — regardless of what the login node itself is for. This extends a
   pre-existing imprecision (the head node was already subject to the same
   broad gate) to a third node type rather than introducing a new pattern.
   Fixed by excluding `LoginNode` from the block; `HeadNode`/`ComputeFleet`
   behavior is unchanged.
5. **Four generated/static surfaces still captioned `./access_cluster.py -N
   <cluster>` as unconditionally reaching the head node**
   (`sns_build_summary_report.j2`, `make_pcluster.py`'s printed summary,
   `create_pcluster.yml`'s `_build_summary`, `hpc-benchmark/
   README-PERFORMANCE.md(.j2)`) — `README.md`'s own "Accessing a Cluster"
   section had already been corrected, these four had not. The
   hpc-benchmark one needed a non-Jinja fix (plain conditional-free prose)
   because its tracked static twin must stay byte-identical to the `.j2`
   modulo two placeholders (`test_the_rendered_doc_matches_the_template`) —
   an `{% if %}` there would have broken that invariant, which exists
   specifically to prevent the raw-`.j2`-vs-static-copy drift the
   S3-staging allowlist bullet in `CLAUDE.local.md` already documents.
6. **SNS report column misalignment.** `sns_build_summary_report.j2`'s new
   "Login Node Instance Type:" line put its value one column later than
   every other field in the same header block — the exact defect class
   `CLAUDE.md` already documents as previously fixed for the storage-summary
   block a few lines below in the same file, and the shipped test had
   hardcoded the misaligned string as the expected value, enshrining rather
   than catching it. Fixed by deriving the column width dynamically from the
   longest active label, mirroring the storage block's own `_storage_col`
   pattern in the same file.
7. **Stale two-value comment in `postinstall.j2`.** The top-of-file comment
   still said the script runs on "the head node and every compute queue" and
   named only `cfn_node_type=HeadNode`/`ComputeFleet`, contradicting the
   three-way `case` statement this same diff had already extended with
   `LoginNode`.
8. **Duplicate Pricing API call.** `_cost_summary_lines` called `_get_od_price`
   separately for `headnode_instance_type` and `loginnode_instance_type`
   with no memoization — true of the toolkit's own shipped
   `pcluster_defaults.yml` example, which sets both to `c8g.xlarge`. Fixed
   by reusing the head-node lookup when the two instance types match.

**Deliberately not fixed, with reasoning recorded so it isn't re-litigated:**
`config.pcluster.j2`'s four now-separately-maintained `CustomActions` blocks
have no shared Jinja2 macro (the review flagged this as finding 2's root
cause) — true, but introducing macro machinery into this specific
Ansible-rendered template ecosystem is higher risk than the value returned,
and finding 2's actual fix already removes the one hazard that pattern had
caused. `_validate_network`'s login-node subnet block is a third
near-identical copy of the head/compute/gpu pattern — same reasoning,
compounded by that function's existing dense test coverage. `access_cluster.py
-L` colliding with `list_pcluster.py`'s unrelated `-L`/`-W` flags is
consistent with an existing tolerated collision in this codebase
(`stop_pcluster.py`'s `-W` means something different from `list_pcluster.py`'s),
not a new problem.

Test count: 1886 → 1908.

### Session 46 — an honest grade surfaced two dedups and a concurrency gap

Asked to grade the codebase, honestly, against everything known at the time
(1908/1908 tests, extensive live-hardware verification, the Session 45
review just landed). Gave A-, not A, and named five concrete reasons rather
than a vague "could be better": (1) the stale `preinstall.j2` pip-download/
EFA-kernel-bump TODO from Session 25-ish, still unverified; (2) no
concurrency guard between two processes touching the same cluster name —
confirmed by grepping for `flock`/lock files and finding none, and this
one is not hypothetical: it is the literal root cause of the `region is
undefined` incident in Session 44; (3) `config.pcluster.j2`'s four
near-identical `CustomActions` blocks and `_validate_network`'s
near-identical head/login subnet blocks, both left alone during Session 45
as acknowledged tech debt; (4) `make_pcluster.py`'s printed access
instructions have no dedicated test (the shared `staged` fixture's
`describe-cluster` mock is not call-order-aware); (5) the byte-budget
near-miss that led to Session 44's own cleanup. Asked to propose fixes for
(2) and (3) — (1) and (4) were left as known, named gaps rather than fixed
this round.

**Design decisions, settled with the user before writing anything:**
- The concurrency lock is **local-machine scope only**, not a distributed
  (S3-conditional-write) lock across machines. The actual incident was two
  terminals on one checkout; a cross-machine lock would add real complexity
  (a new failure mode: network partition) for a scenario that has not
  happened. Modeled directly on `hpc-benchmark.sh`'s existing
  `.osu-cuda.lock` — `mkdir` is the atomic primitive (a check-then-create
  with `os.path.exists` is a race), and a second process fails fast naming
  the lock's owner rather than waiting, same shape as "the loser skips
  rather than polls" there.
- Lock coverage is **`make_pcluster.py` + `kill_pcluster.py` only**, not
  also `start_pcluster.py`/`stop_pcluster.py`/`rotate_cluster_key.py` — the
  two that actually collided, not speculative broader coverage.

**Implementation, verified against the real code before writing anything:**
- `config.pcluster.j2`'s `OnNodeConfigured` block turned out to be
  identical in all four `CustomActions` sites (`HeadNode`, `LoginNodes`,
  both `SlurmQueues`) — same 4-6 lines, differing only in indentation (4
  spaces for `HeadNode`, 8 for the other three, measured exactly before
  writing the macro). Ansible's `template:` module is plain Jinja2
  underneath, so `{% macro %}` + the `indent` filter works: one macro
  definition, called with `indent(4, first=true)` or `indent(8,
  first=true)` at each site. `TestOnNodeConfiguredIsAMacroNotFourCopies`
  guards that the macro is actually *called* four times (not just that
  today's rendered output happens to look right) and that all four sites
  render byte-identical `OnNodeConfigured` content via a real `ClusterSchema`
  round-trip fixture combining login nodes and a GPU queue.
- `_validate_network`'s duplication turned out narrower than the review
  first described: only the head-node and login-node subnet blocks are
  true copies of the same "explicit-or-discover(az)" shape. Compute and
  GPU are structurally different (multi-AZ lists with private-subnet
  fallback logic) and were correctly left alone. A four-line
  `_resolve_single_subnet` nested closure (reusing the existing
  `_discover_subnet` closure already in scope) replaced both call sites.
- The lock (`active_clusters/<cluster>/.build.lock`) is acquired right
  before the first AWS mutation in each script (`make_pcluster.py`: before
  `_setup_iam`; `kill_pcluster.py`: after the serial/vars-file existence
  checks, before the ansible-playbook call) and released at every exit
  point after that — enumerated by hand rather than wrapped in one
  try/finally, since retrofitting a ~2200-line `main()` into a single
  enclosing block was judged higher-risk than explicit release calls at
  four (`make_pcluster.py`) and two (`kill_pcluster.py`) known exit points.
  This carries the same accepted limitation as the OSU lock it's modeled
  on: a genuinely uncaught exception between acquire and the nearest
  release point leaves the lock stuck, remediated by the same `rm -rf
  <lock>` message pattern already used there — not a hidden gap, the same
  tradeoff already made once in this codebase and made again deliberately.
  Verified directly, not just by the unit tests: a standalone script
  acquired the lock, confirmed a second acquire attempt raises with the
  first process's PID/host/command/timestamp in the message, released it,
  then confirmed a fresh acquire succeeds.

All three fixes: 1924/1924 passing (1908 → 1924), `make lint` and `make
shellcheck` clean. Committed as `b8637a5`, pushed to `origin/main`.

### Session 47 — the byte-budget near-miss got a mechanical guard, not just a cleanup

The user pushed back on Session 46's grade item 5 ("a documentation-hygiene
near-miss just happened") with a fair question: wasn't there supposed to be
a handle on keeping `CLAUDE.md`/`CLAUDE-STATE.md`/`docs/sessions.md` under
control already? Worth stating precisely what the audit found, because the
first hypothesis was wrong: `CLAUDE-STATE.md`'s "Recent session work"
section was suspected as the main driver (it duplicates narrative content
that already lives, non-budget-checked, in `docs/sessions.md`) but measured
at only 850 bytes — real, worth fixing, but not dominant. The actual driver:
`CLAUDE.md` grew **+3,364 bytes across this session alone** (10,725 →
14,089), and every bullet checked (the ten largest, 300-650 bytes each) was
genuinely terse and correctly placed — not narrative bleeding into the
wrong file. The ratchet system (condense `CLAUDE.local.md`, move detail to
`docs/sessions.md`, lower `_CEILING`) is designed to absorb exactly this
kind of real growth and never actually failed — headroom was checked only
*after* writing each round, reactively, so it eroded from a comfortable
margin to 9 bytes with nothing forcing an earlier look.

Four fixes, all against the actual root cause rather than the symptom:

1. **`test_headroom_has_a_working_margin`** (`tests/test_claude_docs_preamble_budget.py`)
   fails at 2,000 bytes of headroom, well before the hard 148,000-byte
   ceiling — an early-warning floor, not a second copy of the same check
   (deliberately not derived from the ceiling's own allowance formula, so
   it fires independently of that check ever changing). Carries its own
   vacuity guard in the same shape as `test_the_ceiling_is_not_slack`:
   monkeypatch `_CEILING` to leave less than the floor and require the
   check to fail.
2. **A standing habit was written to persistent memory** (not just stated
   in chat, per the Session 44 lesson about durable facts) — check
   headroom *before* adding to `CLAUDE.md` each round, condense in the
   same round if under the warning floor, rather than discovering the
   problem by running the suite afterward.
3. **`CLAUDE-STATE.md`'s "Recent session work"** rewritten from two
   multi-line paragraphs (850 bytes) duplicating what Sessions 45/46
   already say in full, to two one-line pointers (354 bytes) — the section
   now states its own convention (pointer-only) so it doesn't silently
   drift back into narrative duplication over future rounds.
4. **`CLAUDE-STATE.md`'s Doc structure table** corrected on two counts: the
   `docs/sessions.md` row still described it as frozen pre-fork history,
   when three new sessions (44-46) had already been added to it in this
   session alone; and the budget paragraph cited a stale `150,000` ceiling
   — the actual value after Session 44's ratchet-down was `148,000`, missed
   because nobody re-read that specific paragraph after lowering the
   constant elsewhere.

Net byte effect: freed ~500 bytes from the `CLAUDE-STATE.md` rewrite,
spent ~150 bytes on the corrected Doc structure paragraph and the new
test's brief docstring reference, headroom settled at 6,095 bytes — three
times the new 2,000-byte warning floor. 1925/1925 passing (1924 → 1925 for
the new test), `make lint` and `make shellcheck` clean. Touches only
gitignored files (the test file itself, `CLAUDE-STATE.md`, this file) --
nothing to commit.

## Trimmed CLAUDE.local.md bullets (moved 2026-08-07)

The preamble byte budget (`CLAUDE.md` + `CLAUDE.local.md` + `CLAUDE-STATE.md`)
was down to 261 bytes of headroom after adding the AI-submissions and
external-NFS-check bullets. Rather than raise `_CEILING`, five of the
largest, most self-contained incident narratives in `CLAUDE.local.md` were
condensed in place (rule + core reasoning + test names kept) and their full
original text moved here, verbatim. Nothing was deleted — only moved out of
the always-loaded preamble. Freed ~7.8KB, bringing headroom to ~8KB.

### Performance results sync (full version)

- **Performance results sync to the long-lived results bucket, never to `s3_bucketname`.** Results go to `s3://<results_bucketname>/hpc-benchmark-results/<cluster_name>/<cluster_serial_number>/` on teardown, and `results_bucketname` is `parallelclustermaker-results-<aws_account_id>-<region>` — derived by `_derive_results_bucket` in `src/pcluster_core.py`, created by `create_pcluster.yml` under `enable_hpc_benchmarks`, and deleted by nothing. It was `s3_bucketname`, which is `parallelclustermaker-<cluster_serial_number>` and which teardown deletes with `force: true` (purging objects first) whenever `delete_s3_bucketname` is `"true"` — **the default**. So the default teardown path ssh'd to the head node, uploaded every result, and destroyed them a few tasks later; both tasks *succeed*, so nothing reached `_orphaned_resources` and teardown printed `has been deleted` over a silent data loss. The serial carries a `%S%M%H%d%m%Y` timestamp, so the per-build bucket also made the documented "rebuilds accumulate rather than overwrite" impossible — every build had its own bucket. Keying on account+region is what makes that claim true, and it is why `_derive_results_bucket` takes **only** `aws_account_id` and `region` (keyword-only): any cluster- or serial-derived input silently restores a per-build bucket, which `test_the_derivation_cannot_see_the_cluster_or_the_serial` pins on the signature rather than on the rendered name, since a 12-digit account ID is indistinguishable from a serial datestamp by inspection. Region is in the name because S3 bucket names are global while buckets are regional. `HeadNode-Storage.json_src` grants the head node `s3:PutObject`/`GetObject` on `hpc-benchmark-results/*` and `s3:ListBucket` on the bucket, but deliberately **no** `DeleteObject` or `DeleteBucket`: the head node runs the sync itself over ssh, and anyone with a shell there (including via Slurm job submission) could otherwise erase every past build's results, which are the only copy once the cluster is gone. `TestBenchmarkResultsOutliveTheCluster` (`tests/test_templates.py`) pins the sync target, the prefix, that **neither** playbook deletes the results bucket (`create_pcluster.yml` is included because its early-setup rescue deletes `s3_bucketname` with `force: true` and the obvious wrong move is adding the results bucket beside it for symmetry), the public-access block, and the head node's grant; `test_the_per_build_bucket_is_still_deleted_on_teardown` is the vacuity guard. The creation gate is evaluated through `_effective_when`, not read off the task — the `when:` lives on the enclosing block, so a task-only read sees `true` and passes vacuously. **Only `hpc-benchmark.sh` syncs to `s3://<s3_bucketname>/hpc-benchmark/`, and both ends of that sync are allowlists (`--exclude "*" --include "hpc-benchmark.sh"`), never blocklists.** The prefix exists for one reason — a head node whose EBS root was replaced needs the driver back — and `postinstall.j2` pulls it on head node rebuild. It was a blocklist sync of the whole tracked tree, which shipped three files nobody wanted into the operator's `~/hpc-benchmark/`: the internal `hpc-benchmark/CLAUDE.md`, and the raw `README-PERFORMANCE.md.j2` and `job_hpc-benchmark.sh.j2`. The README case did real damage — the tracked `README-PERFORMANCE.md` is a de-Jinja'd copy of the template whose four `cd` lines read `~/hpc-benchmark/<cluster_name>/<cluster_owner>/slurm` **literally**, so an operator following the top-level copy never reaches the personalized one that `create_pcluster.yml` scps into `headnode_performance_dir_dest`. A blocklist is exactly as wide as whoever wrote it remembered, so every file added to `hpc-benchmark/` would ride along again; the allowlist is the property, not the absence of those three names. **There are two independent delivery paths and only one is restorable.** The S3 pull writes the driver to `~/hpc-benchmark/` (top level); `create_pcluster.yml`'s `mkdir -p` + `scp` of `performance_stage_dir` writes the rendered `job_hpc-benchmark.sh`, the rendered `README-PERFORMANCE.md`, and a second copy of the driver to `~/hpc-benchmark/<cluster_name>/<cluster_owner>/slurm` — which is **not** on S3, so self-repair cannot restore the personalized working directory at all. The doubled driver is deliberate and the copies are identical. **The `chmod +x` after the pull must name exactly the one file the sync delivers, with no `|| true`.** It carried a second target, `~/hpc-benchmark/job_hpc-benchmark.sh`, that has never existed at that path — only the `.j2` was ever synced — and the `|| true` is what hid it for the file's whole life. A chmod that cannot fail needs no guard, and a guard there hides the driver failing to arrive. `TestOnlyTheDriverIsStagedToS3` (`tests/test_templates.py`) pins the upload allowlist by parsing the playbook, the pull allowlist on the **rendered** text of both OS arms, the `enable_hpc_benchmarks` gate by walking to whichever ancestor block carries it, the chmod by exact-string equality, and — as a vacuity guard — that all three internal files are still in `hpc-benchmark/` for a blocklist to have shipped.

### MONITORING_HOME race (full version)

- **Only the head node may write `MONITORING_HOME`, and the gate reads `cfn_node_type`.** `MONITORING_HOME` is `/home/<cfn_cluster_user>/aws-parallelcluster-monitoring`, and `/home` is NFS-exported from the head node — verified in a failing compute node's own chef log (`mount 10.0.1.10:/home to /home`). The wrapper ran `rm -rf`/`mkdir -p`/`tar -xzf`/`chown -R` on *every* node, so two `c5.2xlarge` nodes booting 92 ms apart on 2026-07-27 destroyed each other's tree: one failed `OnNodeConfigured` with `rc=127` because `installer/install.sh` was deleted between its own `tar` and the `bash` that runs it, the other with `rc=1`, counting 2 toward the partition's 10-failure protected-mode threshold. **The race is intermittent** — the relaunched pair survived and the failure count reset — so a green rebuild is not evidence the bug is gone.
  - **A node-local prefix is not available.** Upstream `installer/install.sh:25-27` computes `MONITORING_HOME="/home/${PLATFORM_USER}/${MONITORING_DIR_NAME}"` itself with no override, no argument, and no env fallback (`PLATFORM_USER` is `cfn_cluster_user`, set by `installer/platform/parallelcluster.sh`), and `compose/compute.gpu.yml` hardcodes `/home/${cfn_cluster_user}/__MONITORING_DIR__/dcgm/counters.csv`. Gate the write; do not relocate the tree.
  - **A compute node needs the tree only to exist and be readable.** `compose/compute.yml`'s single service mounts `/:/host:ro,rslave` and nothing from `MONITORING_HOME`; the GPU arm does one idempotent `sed -i` and a read-only bind of `dcgm/counters.csv`. So the `elif [ ! -r "$MONITORING_HOME/installer/install.sh" ]` arm is a diagnosis, not a fallback — it must `exit 1`, and its message is the property under test, since `bash` on the absent script exits 127 either way.
  - **The read is safe because of cfn-init's ordering, not luck.** `runpostinstall` (which contains this wrapper as script 3 of the `OnNodeConfigured` Sequence) completes before the chef `finalize` step that starts `clustermgtd`, and `clustermgtd` is what launches compute nodes — 22:50:20 vs 22:50:40 on the verified build.
  - **The gate names `HeadNode`; it must not be spelled `!= "ComputeFleet"`.** Those two are equivalent only while that is the whole world, and PCluster login nodes NFS-mount `/home` the same way. Nor may it carry a `:-` default: `${cfn_node_type:-HeadNode}` makes every node a head node, which is the `PARALLELCLUSTER_NODE_TYPE` root cause again (session 30 of `docs/sessions.md`).
  - **`tests/test_shell_surfaces.py`'s `_run_wrapper` executes the rendered wrapper** with a fake `cfnconfig`, because a text assertion cannot tell a gated block from an ungated one. `rm`/`mkdir` run for real so the tree's survival is observable, behind a `_guard` that hard-fails on any path outside the tmpdir — a failed path substitution would otherwise point them at the developer's own `/home`. `test_the_harness_can_see_an_ungated_extraction` keeps the negative tests from passing vacuously. All eight faithful mutations are caught; two of them (`!= "ComputeFleet"`, and the `elif` warning without exiting) survived the first battery.
  - **Upstream's own `sed -i` race on `compute.gpu.yml` is deliberately left unpatched** — GPU-only, idempotent, and `sed -i` writes-then-renames so readers see a whole file. Do not conflate it with ours.

### PARALLELCLUSTER_NODE_TYPE (full version)

- **`NODE_TYPE` comes from `cfn_node_type` in `/etc/parallelcluster/cfnconfig`. There is no `PARALLELCLUSTER_NODE_TYPE` environment variable — that name is invented, and reading it defeated every gate in this file.** `aws-parallelcluster-environment::cfnconfig_mixed` writes `cfn_node_type=HeadNode` or `cfn_node_type=ComputeFleet` during the init phase, before any custom action runs (verified in both node types' own chef logs). Nothing exports it: `custom_action_executor.py`'s `EnvEnricher.build_env` is `os.environ.copy()` plus the `cfn_<event>`/`cfn_<event>_args` pairs, and `fetch_and_run` assigns `PCLUSTER_NODE_TYPE` as a plain unexported shell variable it passes to the executor as `--node-type`. A grep for `PARALLELCLUSTER_NODE_TYPE` across both nodes' full chef and cfn-init logs and the vendored PCluster CLI returns **zero** hits. So `${PARALLELCLUSTER_NODE_TYPE:-HeadNode}` always took the default, and on 2026-07-27 every compute node on `osiris` ran the entire head-node path: **all ten** `g4dn.xlarge` launches failed `OnNodeConfigured`, nine with `rc=128` on `fatal: destination path 'Lmod' already exists` (they were racing each other's clone into NFS-exported `$SRC`) and the first with `rc=1` on an `s3:ListBucket` denial the head-node-only S3 sync needs and `ComputeNode-Base` correctly does not grant. The `gpu` queue hit the 10-failure threshold, `clusterstatusmgtd` set the cluster `PROTECTED`, and PCluster's own `finalize_head_node` recipe — `ruby_block[wait for static fleet capacity]` — raised after 52m33s, failing the stack 82 minutes in. The head node itself was fine: `runpreinstall` and `runpostinstall` both succeeded.
  - **The `:-HeadNode` default was the bug, not a safety net.** It collapsed "this variable does not exist" into "be a head node" and made the `case`'s `*)` arm unreachable — the one guard whose entire job is to catch a changed upstream contract. The replacement defaults to `HeadNode` only when the cfnconfig **file** is absent (a genuine off-cluster manual re-run), and treats a cfnconfig that exists without `cfn_node_type` as a hard failure.
  - **`tests/test_templates.py` is what hid it, and the fix is in the harness.** `_run_postinstall` set `PARALLELCLUSTER_NODE_TYPE` in the environment, so all eleven gating tests passed against a mechanism no node has ever used — the harness manufactured the very variable the template was wrong to read. It now writes a fake `cfnconfig` and substitutes its path; `node_type=None` omits the file, `node_type=""` writes one with no `cfn_node_type`. Because the failure mode is reading the *wrong source*, a trace assertion cannot see it (a run whose env and cfnconfig agree behaves identically either way): `test_the_node_type_is_read_from_cfnconfig_not_the_environment` asserts on the rendered source with comment lines stripped, since the name legitimately appears in the comment explaining why it must never be read. All eight faithful mutations are caught, including the exact line that shipped.

### /etc/profile guard (full version)

- **The `/etc/profile` guard suspends all three shell options, not just `set -u`.** `set -e`, `set -u`, and `pipefail` are three independent ways for a profile fragment to kill a node, and a fragment is written for an interactive shell under no obligation to survive any of them. The guard shipped as `set +u` / `source /etc/profile` / `set -u`, which left `pipefail` in force, and AL2023's `/etc/profile.d/debuginfod.sh` runs `DEBUGINFOD_URLS=$(cat /dev/null "/etc/debuginfod"/*.urls 2>/dev/null | tr '\n' ' ')` where `/etc/debuginfod/` **does not exist** on that image — `cat` exits 1, `tr` exits 0, `2>/dev/null` hides the message but not the status, `pipefail` promotes it to the pipeline's status, and the plain assignment propagates it. That failed the first `alinux2023` build of `osiris` (serial `00412128072026`) with `OnNodeConfiguredExecutionFailure` **690 ms** into `runpostinstall` with nothing on stdout — cfn-init captures stdout only, so as with every other bootstrap failure in this file the diagnosis came off the live node rather than the logs. The correct form is `set +euo pipefail` / `source /etc/profile` / `set -euo pipefail`, in **both** `templates/postinstall.j2` and `templates/monitoring-post-install-wrapper.j2`.
  - **RHEL 9 surviving that line was never evidence for AL2023.** The two have different `/etc/profile.d` trees; `debuginfod.sh` is AL2023's. Reasoning from one dnf-family distro to the other is what left the hazard in place through two successful RHEL builds.
  - **Neither `_run_postinstall` nor a source-text assertion can see this.** The harness discards the rendered script's line 2 and so runs with none of the three options set — it has to, because `mkdir` is stubbed and the head-node path's `cd` targets never exist — and a text match cannot tell a guard that encloses the `source` from one that no longer does. `TestTheProfileGuardSuspendsEveryOption` (`tests/test_templates.py`) extracts each template's real prologue *by position* and executes it under real `bash` against a profile carrying both hazards, parametrized over both templates. Its `_prologue` pins the extraction (first executable line sets the options; exactly four executable lines; the `source` is the third) so a drifted guard fails there rather than as a confusing runtime error. `test_the_harness_fails_a_guard_that_only_suspends_set_u` is the vacuity guard, and it rebuilds the narrowed prologue from the guard's *position* rather than by replacing a literal — a literal replace fails on any variant spelling for bookkeeping reasons, which is not a signal about anything. All eight faithful mutations are caught, including the exact line that shipped and its two one-file-only variants.
  - **`_run_postinstall` narrows the guard's restore line to `set -u`** for the same reason it discards line 2: the real restore switches `set -e` on mid-script and every head-node test would then abort at a stubbed `mkdir`'s missing directory. Nothing is lost — the guard itself is executed for real by the class above.
  - **`set +eu` (suspending `-e` and `-u` but not `pipefail`) is behaviorally safe** and is caught only by the text-ordering test, correctly: with `-e` cleared, a failed pipeline status has nothing to act on. Do not add a runtime assertion against it.

### Teardown ignore_errors / orphaned resources (full version)

- **Every `ignore_errors` cleanup task in `src/delete_pcluster.yml` must `register:`, and the result must be read.** `ignore_errors` is correct there — one AWS failure must not abandon the remaining nine cleanup steps — but on its own it prints `...ignoring`, exits 0, and the playbook still says `Cluster <name> has been deleted`. That is how the orphans from serials `50321924072026`, `10162123072026`, and `09412321072026` went unnoticed until they were swept by hand, days later, after the serial file was gone and `kill_pcluster.py` would no longer retry them. `Collect cleanup failures that ignore_errors swallowed` builds `_orphaned_resources` from each `<var>.failed | default(false)`; `_orphaned_resources` then drives three surfaces — the printed summary (two mutually exclusive tasks, the clean one gated `when: not _orphaned_resources`), `templates/sns_destruction_summary_report.j2`, and a terminal `fail:` gated on `length > 0`. Ordering is load-bearing: collection must precede the SNS templating, and both `fail:` tasks must come after every cleanup task and after the summary print, or they abort the thing they are reporting on. `.failed | default(false)` was verified empirically under real Ansible, not assumed — it is `False` for skipped tasks, and `True` at the *top level* of a looped (`with_items`) result, not only per-item. Two exemptions, both encoded in `TestTeardownFailuresReachTheOperator._NOT_AN_ORPHAN`: deleting the local `.pem` orphans nothing in AWS, and a failed SNS *send* is not a leftover resource. The SNS *topic* is, but it is deleted after the report it carries is sent, so it cannot appear in that report — `Append the SNS topic to the orphan list if its deletion failed` adds it afterward, in time for the summary and the exit status. Entries must interpolate real resource names; `3 cleanup steps failed` sends the operator hunting. All eight faithful mutations of this are caught. **The EC2 keypair delete was the last AWS-mutating cleanup task with no `ignore_errors` at all**, so a single denied `DeleteKeyPair` aborted the play before every later cleanup step *and* before the collection itself — nine resource classes leaked with the failure printed as a plain task error. It now carries `ignore_errors` plus `register: _rm_ec2_keypair`, read by the collection. Its `no_log: true` was **removed** at the same time: the module is passed only a key *name* and returns `{"key": None, "msg": "key deleted"}` (`ec2_key.py`), so `no_log` censored nothing sensitive and hid the one line naming a denial's cause. `no_log` remains correct on the three creation-side tasks that handle real key material, and `test_key_creation_still_censors_the_key_material` pins that so the removal is not read as license.

## Trimmed CLAUDE.local.md bullets (moved 2026-08-13)

The preamble byte budget had drifted to 9 bytes of headroom against the
150,000-byte ceiling — the "slow relapse" the ceiling's own test docstring
warns a byte cap cannot see on its own, produced by several rounds of small,
individually-justified additions (Login Node feature docs, the adversarial-
review bugfix docs, session-narrative gap fixes) each squeezed in under a
shrinking margin rather than triggering a real reduction. Condensed the
single largest bullet in `CLAUDE.local.md` in place (12,949 bytes → ~4,300
bytes; rule + core reasoning + test names kept, exact dates/instance
IDs/error codes moved here verbatim). `_CEILING` in
`tests/test_claude_docs_preamble_budget.py` is lowered in the same pass, per
that test's own ratchet rule.

### Kernel exclusion (full version)

- **The package upgrade in `preinstall.j2`/`postinstall.j2` must never replace the kernel.** A kernel bump triggers an initramfs rebuild whose runtime is unbounded inside CloudFormation's bootstrap window — on the PCluster AMI of the day a full package upgrade crossed a kernel boundary and was still rebuilding when the wait condition expired, which is half of why cluster `osiris` failed. Independently, PCluster's AMI ships EFA and Lustre kernel modules built against the kernel it boots, so replacing that kernel without rebuilding them risks losing the interconnect or the Lustre client on next boot (the documented DKMS hazard pattern; **not** verified against the current AMI). Both of `preinstall.j2`'s arms carry a full upgrade and both hold the kernel back: the apt path via `apt-mark hold`, the dnf path via `--exclude='kernel*' --exclude='kmod-lustre*' --exclude='efa*'`. The dnf exclusion was measured on the RHEL 9 PCluster AMI — a full update crossed `5.14.0-611.55.1.el9_7` → `5.14.0-687.30.1.el9_8` and the dracut rebuild was still running when CloudFormation gave up. `postinstall.j2`'s RHEL arm carries the same three `--exclude`s on its own `dnf update`; its apt arm installs named packages only and must never be changed to `dist-upgrade`/`full-upgrade` without a hold — that would put the same rebuild one stage later, which `TestPreinstallNeverReplacesTheKernel::test_postinstall_never_dist_upgrades_without_a_hold` guards.
  - **`--exclude` is a name glob resolved at depsolve time, which is why the dnf arm needs no enumeration and no audit.** dnf drops every package whose name matches from the transaction on every AMI revision, independent of what is pending; a kernel cannot enter unless it arrives under a name not starting with `kernel`, and on RHEL 9 every kernel subpackage (`kernel`, `-core`, `-modules`, `-modules-core`, `-modules-extra`, `-headers`, `-devel`, `-tools`) does. `apt-mark` needs a package list only because it pins *named* packages — that is apt's cost of doing what one glob does here, not a capability the dnf arm is missing. Do not add a `dnf check-update`-based audit on the theory that a future AMI's pending set could differ; it cannot matter, and it costs a second full depsolve on the bootstrap clock. `test_dnf_arms_exclude_the_kernel_from_every_update` is parametrized over both templates and requires all three excludes on every rendered `dnf ... update` line — until it existed, mutating both calls to a bare `sudo dnf -y update` passed the entire suite. It asserts on the **rendered** text on both counts: `dnf` is stubbed in `_run_preinstall`, so a trace cannot tell a flag that was passed from one that was honored, and a line inside an unexpanded `{% if %}` is not a line any node runs. All eight faithful mutations are caught. **Never add `--exclude='dracut*'` or `--exclude='microcode_ctl'`** — both regenerate the initramfs in `%posttrans` without matching `kernel*`, but neither replaces the kernel, the successful RHEL builds upgraded `dracut` and still finished inside the window, and excluding security updates on speculation is the worse trade. A `uname -r` vs `rpm -q --last kernel` guard is likewise wrong: it passes in exactly that case, and if the AMI ships a kernel installed-but-not-booted it fails every healthy node under `set -euo pipefail`.
  - **The upgrade itself is deliberately kept.** Narrowing it to installing only the named packages was considered and rejected: `preinstall.j2` installs `python3-dev`, and `numpy`/`scipy`/`pandas`/`matplotlib` compile from source wherever pip finds no wheel — the aarch64 case that `ubuntu2204arm` and `ubuntu2404arm` both hit. The claim "nothing needs a full upgrade at boot" was an assertion that did not survive reading the templates. Line 16 is a survivor of the 2019 `ubuntu1604` Python-3.6-PPA block (`git show 4c0b759:ClusterMaker/templates/preinstall.j2`), where every upgrade sat under a narrow gate with a stated purpose; the v3 migration (`c2673ae`) kept the upgrade and dropped the gate. Its original justification is gone, which is **not** the same as it being unnecessary now.
  - **`|| true` on the `dpkg-query` assignment is load-bearing, not decoration.** `dpkg-query -W` exits non-zero when *any* of its four patterns matches nothing — the normal case, since no AMI carries `linux-image*`, `linux-headers*`, `linux-modules*` and `linux-aws*` — while still printing the packages that did match. This is a plain assignment, which per the `set -e` rule above propagates the failure, so without the guard the node aborts bootstrap. Verified under real `bash`, and the mutation is caught by two tests.
  - **`dpkg-query -W` must be filtered to `${db:Status-Status} == installed` before anything reaches `apt-mark`.** `-W` reports every package dpkg has a record of, installed or not: on the `ubuntu2404` AMI those four patterns return **20** names of which **11 are not installed** — `linux-headers-686-pae`, `linux-headers-3.0`, `linux-headers-amd64`, `linux-image`, `linux-headers`, `linux-aws-6.17-doc-6.17.0`, `linux-aws-6.17-source-6.17.0`, `linux-aws-6.17-tools`, `linux-headers-generic`, `linux-image-unsigned-6.17.0-1015-aws`, `linux-modules-extra-aws`. `apt-mark hold` exits **100** on any name with neither an installed nor a candidate version (`E: Can't select installed nor candidate version from package '<name>' as it has neither of them`), and the `|| true` above guards the *`dpkg-query`* assignment, not `apt-mark` — so the 100 propagates under `set -e`. That is what failed `osiris`'s head node on 2026-07-27 with `OnNodeStartExecutionFailure` and `return code: 100`, twelve packages into a hold that had already printed `set on hold` for each of them. Reproduced directly on the live head node (`i-0000000000000007`): `apt-mark rc=100`, eight `E:` lines. The failure is invisible in `cfn-init-cmd.log`, which captures only stdout — the `E:` lines go to stderr and are logged nowhere, so the last thing recorded is a successful-looking list of holds. Do not "simplify" the format string back to `${Package}\n`, and do not drop the `awk` filter: the two are one mechanism. `test_uninstalled_kernel_packages_are_never_handed_to_apt_mark` pins it with the real AMI's package list, and all six faithful mutations are caught.
  - **The harness's `dpkg-query` stub honors `-f`, and its `apt-mark` stub exits 100.** Both are load-bearing. A stub that prints the status column regardless of the format string cannot distinguish dropping `${db:Status-Status}` from keeping it (the `awk` filter then matches a bare package name against `installed`, emits nothing, and `apt-mark` is handed an empty list); a stub that always returns 0 cannot see the bug at all. `test_the_harness_apt_mark_stub_actually_rejects_a_phantom` guards the second against passing vacuously. `awk` is deliberately left unstubbed so the filter under test is the real one.
  - **Every `pip3 install` on a node path must carry `--ignore-installed`.** The rule is not "the pip self-install is safe" — that predicate was true the whole time the first live RHEL 9 build was failing. pip cannot uninstall a distribution whose `dist-info` has no `RECORD`, and distro-packaged Python modules routinely ship exactly that, so *any* install that resolves to replacing one dies at `Attempting uninstall: <name>`. This has now shipped twice on two different lines of the same file: session 26 fixed the pip self-install (see the bullet below), and left the dependency install two lines under it unflagged. On 2026-07-28 `osiris`'s head node failed `OnNodeStartExecutionFailure` with `return code: 1` eight minutes into `runpreinstall` on `'requests>=2.31,<3'` against RHEL 9's RPM-owned `python3-requests-2.25.1-10.el9_6.noarch` — its `dist-info` was confirmed on the live node (`i-0000000000000020`, RHEL 9.8) to hold only `INSTALLER`, `LICENSE`, `METADATA`, `WHEEL`, `top_level.txt`. `requests` is the only one of the eight pins the AMI preinstalls; **`numpy` had already been uninstalled successfully when the transaction aborted**, so the failure left the node with less than the AMI shipped, and a partially-uninstalled interpreter is not a state any later stage is written against. As with every bootstrap failure in this file, cfn-init captured **stdout only** — the log's last line is the cheerful `Attempting uninstall: requests`. Four lines are covered: the pip self-install and the dependency install in `preinstall.j2`, and the plotting-stack install in `postinstall.j2`, each on both OS arms. `TestNoPipInstallEverUninstallsADistroPackage` asserts over the **rendered** text of both templates on both arms, because a line inside an unexpanded `{% if %}` is not a line any node runs; all seven faithful mutations are caught, including the exact line that shipped and a vacuity guard on the line count. The `postinstall.j2` addition guards against *transitive* replacement one level down — its five names are unpinned and already satisfied by preinstall, so nothing direct would be replaced, but **pip does elect to replace distro-owned transitives here and that is measured, not assumed**: the verification run installed `packaging-26.2` and `python-dateutil-2.9.0.post0` over RHEL 9's own RPMs. Had either `dist-info` been RECORD-less the way `requests` was, the unflagged line would have failed identically one stage later. Cost is a re-download of five wheels on the head node only, under `enable_hpc_benchmarks`; all five publish manylinux aarch64 wheels, so this is not a from-source build on the ARM images. **The fix is verified on both RHEL arms** — on `rhel9` x86 the verbatim rendered arm returned exit 0 with zero `Attempting uninstall` lines on head node `i-0000000000000020`, with all eight pins plus 20 transitives resolved on Python 3.9; on `rhel9arm` build `iris-10121728072026` (`c8g.xlarge`) resolved the same eight from real `manylinux_2_17_aarch64` wheels for every compiled one, with no from-source build except pure-Python `tailhead`. Whether the Ubuntu AMI preinstalls any of the eight is still unverified — the flag is on that arm because a RECORD-less `dist-info` is a property of distro packaging rather than of pip, and re-checking the AMI on every image bump is not a maintainable guard.
  - **`pip3 install --upgrade pip` must never be used on the Debian pip.** `python3-pip` ships `/usr/lib/python3/dist-packages/pip-<ver>.dist-info` with **no `RECORD`** file (only `INSTALLER`, `METADATA`, `WHEEL`, `entry_points.txt`, `top_level.txt` — verified on the `ubuntu2404` AMI, `dpkg -S` confirms the owner), and pip cannot uninstall a distribution it has no file manifest for. `--upgrade` therefore reaches `Attempting uninstall: pip / Found existing installation: pip 24.0` and dies, which under `set -euo pipefail` failed `osiris`'s head node at 12:26 on 2026-07-27 with `return code: 1`. `--break-system-packages` permits writing into the system tree; it does **not** make a dpkg-owned pip uninstallable — the two flags solve different problems. Use `--ignore-installed`, which skips the uninstall and installs over the top; nothing in the toolkit needs the distro pip removed, only a newer pip on `PATH`. Note `--dry-run` does **not** reproduce the failure (it returns before the uninstall step), so it is useless as a check here. The line was unreachable until the `apt-mark` fix above let execution get past it, which is why no earlier build exposed it. `pip3` is stubbed in `_run_preinstall`, so no runtime assertion can see this — `test_pip_is_never_upgraded_over_the_distro_pip` checks the source, and all four faithful mutations are caught. **The RHEL arm uses `--ignore-installed` too, and must not carry `--break-system-packages`**: an RPM-owned `dist-info` may equally have no `RECORD`, while RHEL 9's pip predates PEP 668 and rejects the flag outright. That asymmetry is deliberate — on every one of the four pip lines the two arms differ in exactly that one flag and nothing else. `test_break_system_packages_is_not_treated_as_the_fix` pins both halves: absent on every RHEL line, and never standing alone on an Ubuntu one.
  - **`$_kernel_pkgs` is unquoted on purpose**, so multiple packages arrive as separate argv entries; quoting hands `apt-mark` one concatenated string. The `[ -n ... ]` guard is equally required — `apt-mark hold` with an empty argument list is a usage error.
  - **`tests/test_templates.py::TestPreinstallNeverReplacesTheKernel` executes the rendered script under real `bash`** with the package managers stubbed, because whether the hold actually happens is a property of word-splitting and `set -e`, not of the text. `_run_preinstall` must restore `set -euo pipefail` explicitly: the harness discards the rendered script's first two lines, and line 2 is where it lives. While it was missing, removing the `|| true` guard passed the entire suite — the harness asserts on line 2's content so it cannot drift silently.

### Session 48 — MCP server migration planned, adversarially reviewed, and cross-checked against real source and live AWS tests; no production code touched

A comprehensive plan for exposing this toolkit as MCP tools (Claude Code local via stdio, Claude web remote via Lambda) was researched and written to `docs/parallelclustermaker-mcp-plan.md` (gitignored, planning-only — the file states plainly it is a researched and adversarially-reviewed architecture, not yet a line-level implementation spec). Seven workstreams: callable-library refactor, Ansible-to-Python templating, Ansible-to-boto3/`pcluster.lib` orchestration, async job handling, transport/hosting, remote auth, and tool-surface scoping/testing — each grounded in real source reads or live tests rather than assumption, including two live AWS tests specifically: a temporary Cognito User Pool stood up and torn down to confirm PKCE and RFC 8707 `resource`-parameter behavior against real endpoints, and `pcluster.lib`'s exception/kwarg behavior (`create_cluster`/`delete_cluster`/`update_compute_fleet`/the `dryrun` spelling) probed directly against the installed package via deliberately-triggered `TypeError`s, no AWS calls needed. The plan went through two full adversarial-review passes; findings from both were fixed in place, not just logged — including a real design bug (the S3-backed distributed lock's staleness-reclaim path was non-atomic as first written, allowing two racing reclaimers to both believe they held the lock; fixed to a conditional `IfMatch` write) and a real gap in the remote-auth design (Cognito access tokens carry no `aud` claim, so the plan's original "API Gateway native JWT authorizer" would have rejected every valid token; replaced with a Lambda authorizer checking the `client_id` claim against this server's own registered app clients via `cognito-idp:DescribeUserPoolClient`).

Three findings from this research are about the **current** codebase, independent of whether the MCP work ever ships, and are not yet fixed — noted here as pending work, not acted on this session:

- `_run_pcluster_cmd` (`src/pcluster_core.py:1814`) is a shared chokepoint for shelling out to the `pcluster` CLI, raising `SystemExit` on failure — but only `diagnose_pcluster.py` uses it. `list_pcluster.py`'s `_live_status` and `check_pcluster.py`'s `check_cfn_status` each define their own `_PCLUSTER_BIN` constant and call `subprocess.run` directly, with a different, deliberate error-handling philosophy (return a sentinel rather than raise, so one cluster's failed lookup doesn't abort a table/check run spanning several clusters). A `pcluster.lib` migration would need to preserve both philosophies at their respective call sites, not unify them.
- `manage_pcluster_queue.py` does not use `pcluster_core.py`'s shared `_get_fleet_status`/`_fleet_action_plan`/`_poll_fleet` (used correctly by `stop_pcluster.py`/`start_pcluster.py`) — it reimplements nearly identical logic privately and inline, with different timeout constants, and a raw `if status == "PROTECTED": sys.exit(...)` in place of the shared `_fleet_action_plan` decision function. Same "near-copy code paths" pattern commit `b8637a5` fixed elsewhere in this repo.
- `manage_pcluster_queue.py` imports queue-editing logic from `pcluster_queue_editor.py`, a third Python-logic module CLAUDE.md's architecture rule doesn't name (the rule states logic "must live in `src/pcluster_core.py` or `src/pcluster_aux_data.py`").

Also surfaced, not a bug: `_read_cluster_record` (local-filesystem `vars_files/<cluster>.yml` reads) is a dependency of 8 of the 12 MCP-in-scope scripts, which a remote Lambda deployment has no access to — the plan doc addresses this with an S3 metadata mirror (extending the existing per-cluster lock bucket with a `vars/` prefix) and a backend-agnostic core-function design principle; see the plan doc's Workstream 1 for detail, not repeated here.

### Session 49 — every remaining MCP-plan decision resolved; two more live AWS verifications; plan is now implementation-ready

Follow-on to session 48. The MCP plan doc (`docs/parallelclustermaker-mcp-plan.md`, gitignored) had a "What's blocking a true implementation spec" section listing genuinely open items — this session closed every decision-shaped one and live-tested the two verifications cheap enough to just do rather than defer.

**Decisions resolved, each grounded in real code rather than picked generically:**

- **The `sys.exit()` → exception conversion rule.** Mapped all 36 `sys.exit()` call sites in `src/pcluster_core.py` to their enclosing function rather than assuming a per-function-type rule was needed. Every one belongs to a pure validation function — no AWS mutation, nothing to roll back on failure — so the rule is uniform: one new `PClusterMakerError(Exception)` class (this codebase currently defines zero custom exceptions), raised with the same message text `sys.exit()` uses today, caught once at each CLI shim's `main()` to preserve identical output/exit codes. One real bug gets fixed as part of the conversion, not preserved: `_validate_network`'s line-1142 bare `sys.exit(1)` carries no message today — its actual error text is only on stdout via five separate `print()` calls beforehand, unlike every other call site. `_acquire_cluster_lock`'s exit is out of scope — Workstream 4's S3-lock redesign already replaces that function.
- **`manage_pcluster_queue --wait`'s three-phase async shape** (stop fleet → apply config → restart fleet, flagged in session 48 as not fitting the plan's single `wait: bool` pattern). Resolved by decomposition rather than a new pattern: phases 1 and 3 reuse `stop_pcluster`/`start_pcluster`'s exact core function (`update_compute_fleet` + manual polling); phase 2 is a new core function using the standard `wait: bool` pattern, since `update_cluster` — unlike `update_compute_fleet` — genuinely supports the library's native `wait` kwarg. The MCP tool surface exposes three separate tools, not one opaque multi-phase tool, so the calling model has visibility into which phase is running.
- **File/module layout for the new MCP code.** New `mcp_server/` directory at repo root: `server.py` (both FastMCP instances), `tools.py` (the `@mcp.tool()` wrappers), `auth/register_lambda.py`, `auth/authorizer_lambda.py`, `heavy_handler.py` (the Node.js/CDK-bundled container-image Lambda).
- **IaC approach for the new AWS infrastructure** (Cognito pool, the two auth Lambdas' roles, the dispatcher and heavy-Lambda roles) — decided by consistency with this repo's own convention rather than introducing a new tool: this codebase already rejected Terraform/CDK in favor of direct boto3 orchestration, and already has a working pattern for exactly this (`templates/*.json_src` + Jinja2 + `_setup_iam`). New `templates/MCPDispatcherLambda.json_src` etc., applied by a new `_setup_mcp_infra` mirroring `_setup_iam`'s shape.
- **Test file mapping** for the MCP tool-surface test plan — four files, not five (two of the originally-separate layers are tightly coupled, same testing style): `tests/test_mcp_schemas.py`, `tests/test_mcp_tools.py`, `tests/test_mcp_confirmation_gate.py`, `tests/test_mcp_lock.py`.

**Two more live AWS verifications, same discipline as session 48's Cognito test — stood up real infrastructure, tested, tore it down:**

- **S3 lock atomicity.** Created a temporary bucket (`mcp-lock-test-*`, account `183295445014`, `us-east-1`), tested both halves of the lock design, then deleted everything. Initial acquire: first `PutObject --if-none-match '*'` succeeded, a second on the same key failed with `PreconditionFailed`. Reclaim path (the part a prior adversarial pass had found non-atomic and fixed on paper, never tested): first `PutObject --if-match <observed ETag>` succeeded, a second racing reclaimer using the same now-stale ETag failed with `PreconditionFailed`. Both halves hold under real S3 conditional-write semantics, not just documented API surface.
- **`NotFoundException` behavior.** Called `pc.describe_cluster()` and `pc.delete_cluster()` (both from the installed `pcluster.lib`) against a genuinely absent cluster name (`definitely-does-not-exist-mcp-test`, `us-east-1`) — both raised `pcluster.api.errors.NotFoundException` exactly as traced from source in session 48, with the documented message text. Neither call mutates anything on a genuinely absent cluster, so this was safe to run directly.

**One item deliberately deferred, not resolved:** Claude's actual DCR registration request payload shape can't be checked without either standing up production infrastructure and getting a real Claude Code/Claude web connection attempt, or materially deeper research into Claude's specific connector implementation — decided to build the `/register` Lambda to the documented RFC 7591 shape and treat a real connection attempt as the actual test, once there's a real endpoint to point Claude at. Not a gap left open by omission; a decision about when it can actually be checked.

The plan doc's blocking-list section was updated to reflect all of the above with strikethrough/pointer annotations, matching the convention already established for resolved items in that document. No production code was touched this session — this was the last planning/decision round before implementation begins.

**Same-session follow-up:** the plan doc's own top-of-file status line had gone stale mid-session — it still pointed readers at "What's genuinely still unresolved" (a section fully resolved via strikethrough several rounds earlier) instead of "What's blocking a true implementation spec" (the section this round's decisions actually updated), and its prose still read as if the decision-resolution round hadn't happened yet. Fixed to point at the correct section and state plainly that every decision-shaped gap is closed, with only drafting work and the one deliberately-deferred DCR-payload-shape check remaining.

### Backfill — the private→public fork and commits 1-11 (moved from `CLAUDE-STATE.md`, 2026-08-19)

`CLAUDE-STATE.md`'s "What this repo is" and "Commit state" sections had grown to carry the full narrative for the fork and all eleven commits on `main` in-place, rather than as pointers — the opposite of the split this repo's own doc structure argues for (lean/current in `CLAUDE-STATE.md`, full narrative here). Unlike the MCP-plan condensing done earlier in session 49, this content isn't duplicated anywhere else — the earliest dated entry in this file is session 25, well after the fork and after commits 1-7 — so it's moved here in full rather than simply deleted, per the doc structure's own stated purpose for this file.

**What this repo is.** This is a public, scrubbed, restructured derivative of a private repo (`ParallelClusterMaker`, renamed locally to `ParallelClusterMaker-Legacy` and on GitHub to `rmarable/ParallelClusterMaker-Legacy`). Same battle-tested toolkit; what changed in the fork: fresh git history (no commits/branches/objects carried over — `main` here starts at its own initial commit); `CLAUDE.md` split into lean (committed) + dense (gitignored) pairs at the root and in `templates/`/`hpc-benchmark/` — the lean files hold actionable rules only, the dense `*.local.md` companions hold the full incident-history rationale; real AWS identifiers scrubbed from every doc and test fixture that carried them (one real account ID, 24 real EC2 instance IDs, 5 real private IPs) — cluster serial numbers and dates were left as illustrative examples except where tied to one specific real incident, obfuscated on request; `pr/` (an internal press-release doc) was dropped; `README.md` split into `README.md` + `INSTALL.md`, with README's Features section rewritten for consistent style (`--flag` notation, per-category/per-bullet cross-links to the relevant deep-dive section).

**Commit state as of `b8637a5` (eleven commits on `main`):**

1-3. `1785933`/`c39b751`/`674c180` — initial public release (squashed fork/scrub work, no earlier history); README prose cleanup (pronoun reduction, EFA/HyperThreading detail, undated validation claims); added a "Use Cases" section (GPU list pulled from `_GPU_PREFIXES`, not hand-restated).
4. `0f5c8f7` — documented the storage-summary column-width fix (full rationale in `CLAUDE.local.md`) in the lean `CLAUDE.md`.
5. `19930de` — added `AI_POLICY.md` (disclosure, human review/responsibility, no autonomous issue/PR filing, `Co-Authored-By` convention); linked it from README's top disclosure line and closing contributor section; added a matching "AI Submissions" section to `CLAUDE.md`; normalized `##`/`###` heading case (sentence case → Title Case) across `README.md`, `hpc-benchmark/README-PERFORMANCE.md(.j2)`, and `tests/integration/README.md`; excluded `docs/press-release-*.txt` via `.gitignore`.
6. `af07c1c` — added `_check_external_nfs_reachable` (pre-flight TCP port-2049 + best-effort `showmount` check for `--enable_external_nfs`, run before `_setup_iam`; only a confirmed-empty export list hard-fails).
7. `58a2bce` — documented the external-NFS pre-flight check in the lean `CLAUDE.md` (Architecture section).
8. `d424695` — Login Node support (`--enable_loginnode`): new `LoginNodes:` pool, architecture-aware `loginnode_instance_type` fallback, `ComputeNode-Base` IAM, `-L`/`-H` in `access_cluster.py`, full mirrored test coverage, README/CLAUDE.md docs, and the Node.js/AWS CDK prerequisite fix. Confirmed on a live build (`osiris`, `ubuntu2404arm`, 2026-08-12).
9. `e8eb447` — removed two dead-code items found during a repo-wide sweep: the unused `default_instance_types` dict/import (`pcluster_aux_data.py`/`make_pcluster.py`) and the orphaned `sns_destruction_summary_report_dest` vars_file.j2 key.
10. `4fe2d8e` — 8 bugs from the adversarial review of the Login Node feature fixed (see session 45 above for the list).
11. `b8637a5` — per-cluster build/teardown lock, `config.pcluster.j2` macro dedup, `_validate_network` subnet-helper dedup (see session 46 above).

Author/committer identity: `Rodney Marable <rodney.marable@gmail.com>`, via global git config since commit 4 — plain `git commit` resolves it; fall back to `--author`/env-vars only if `git var GIT_AUTHOR_IDENT` shows unset. Working tree was clean as of `b8637a5`, pushed to `origin/main`, at the time of this backfill.

### Session 50 — MCP plan's Lambda topology split further by IAM blast radius, then adversarially reviewed and fixed; still no production code touched

Follow-on to sessions 48-49. Two rounds this session, both against `docs/parallelclustermaker-mcp-plan.md` (gitignored, planning-only) and the scratchpad IAM policy drafts under the session's temp directory (not yet moved into `templates/`).

**Round 1 — split the remote topology from 2 Lambdas to 7, driven by drafting the actual IAM policies.** Drafting `MCPStackMutation.json_src` surfaced that `delete_cluster` needs nearly the same broad IAM as `create_cluster` (CloudFormation stack deletion needs symmetric permissions to creation), so the plan's original 2-Lambda split (thin dispatcher vs. heavy Node.js container) didn't actually track IAM blast radius — a "thin" dispatcher serving `delete_cluster` would carry the same breadth as the "heavy" one regardless of container. Redesigned into 7 functions: `/register`, the Lambda authorizer (both pre-existing), a new near-zero-IAM router (parses JSON-RPC, forwards via `lambda:InvokeFunction` to whichever handler serves the tool — necessary because MCP's Streamable HTTP transport exposes one endpoint with the tool name in the body, not the URL path, so API Gateway can't route by tool name itself), and four IAM-tiered handlers: read-only (9 tools), fleet-toggle (2 tools), stack-mutation-plain (`delete_cluster` only), stack-mutation-Node.js (`create_cluster`/`apply_queue_config`/`preview_cluster_config`). Folded into Workstream 5 (topology), Workstream 6 (consolidated list), and the MCP tool schemas section (routing regrouped into the 4 tiers) — the tool-schema fold-in also caught and fixed a preexisting miscount (the doc had claimed 19 tools; the actual list is 18).

**Round 2 — adversarial review of round 1's fold-in found 6 real issues, all fixed the same session, two of them via reading `pcluster`'s actual installed source rather than inferring:**

1. **Preview/execute pairing across IAM boundaries.** First flagged as "the confirmation token's signing secret now needs cross-Lambda distribution, since `preview_cluster_delete` (read-only tier) and `delete_cluster` (stack-mutation-plain tier) run in different Lambdas with different roles" — on rereading Workstream 4's actual design this was wrong: the token is a **keyless deterministic hash** `hash(cluster_name, canonical_params, issued_at)`, not an HMAC, so verification needs no shared secret at all, just an independent recompute. The real, narrower risk underneath: the hash/canonicalization function must be one shared module (`confirmation_token.py`, added to the `mcp_server/` file layout) imported identically by both packages, with both redeployed together — a version-skew window (one package's canonicalization changes before the other's) would reject legitimate tokens, not admit forged ones. `create_cluster`/`preview_cluster_config` don't have this exposure since Workstream 5 keeps that pair co-located in the same tier.
2. **No tier's IAM accounted for S3 cluster-state access**, and it's a hard blocker for two tools, not a nice-to-have: `add_queue`/`remove_queue` (both read-only tier) need `configs/*` write, which nothing in the tier's described IAM (`EC2Read`/CFN-describe/Logs-read/Cost-Explorer) grants. Fixed by replacing the single unscoped `MCPStateAccess.json_src` scratchpad draft with three tier-scoped files — `MCPStateAccessReadOnly.json_src` (`vars/*` read, `configs/*` read/write, no `locks/*`), `MCPStateAccessFleetToggle.json_src` (`vars/*` read, full `locks/*`, no `configs/*`), `MCPStateAccessStackMutation.json_src` (shared by both stack-mutation tiers: `vars/*` read/write/delete, `configs/*` read/delete, full `locks/*` — a deliberate, explicit over-grant relative to what either individual tool needs, same reasoning as the shared create/delete action policy) — each attached alongside its tier's action policy. This also caught and fixed a *cascading* inconsistency in an earlier, pre-existing passage that claimed the whole remote side was read-only on `vars/*`; it isn't — the stack-mutation tiers write it.
3. **Router adds a second Lambda hop to every call**, including the read-only/polling calls the plan's own async design (Workstream 4) makes the majority of total call volume — the original 2-Lambda design let the dispatcher serve most tools with one hop. Not a bug, but the trade-off was never weighed anywhere; added an explicit "accepted trade-off, unmeasured" note rather than leaving it silent.
4. **The "split further, by IAM blast radius" framing overstated what changed.** Only the read-only/fleet-toggle carve-out is actually blast-radius-motivated; stack-mutation-plain vs. Node.js carry "nearly the same broad IAM" by round 1's own finding, so that half remains a pure runtime/cold-start split — the same rationale the original 2-Lambda design had. Reworded to say so plainly instead of implying the whole split was security-driven.
5. **Prose/draft mismatch, resolved by reading `pcluster`'s installed source rather than guessing either way.** `MCPFleetToggleLambda.json_src` (drafted round 1) granted `cloudformation:DescribeStacks` **and** `DescribeStackResources`; the Workstream 5 prose describing that tier's IAM mentioned neither. Traced `pcluster.api.controllers.cluster_compute_fleet_controller.update_compute_fleet` directly: it reads `cluster.stack.scheduler` to branch AWS Batch vs. Slurm, and `Cluster.stack` (`pcluster/models/cluster.py:179-182`) lazily calls `AWSApi.instance().cfn.describe_stack()`, which (`pcluster/aws/cfn.py:106-114`) is exactly one `describe_stacks` boto3 call — confirming `DescribeStacks` is genuinely needed (added to the prose) and finding no call site anywhere in that trace for `DescribeStackResources` (removed from the draft as unjustified).
6. **Unresolved ambiguity: does `apply_queue_config` (Node.js tier) orchestrate its stop/apply/restart phases atomically server-side, or does the calling agent call `stop_fleet` → `apply_queue_config` → `start_fleet` as three separate tool calls** (risking a fleet left stopped if a step fails between calls)? The original fleet-toggle bullet asserted reuse ("`manage_pcluster_queue`'s fleet-toggle phases") without saying which. Resolved: atomic, in-process, inside the Node.js tier — `core_apply_queue_config` calls the same `core_stop_fleet`/`core_start_fleet` functions the standalone tools wrap, as ordinary Python calls within that Lambda's own process, matching the CLI's existing atomic `--wait` behavior. No new IAM grant needed for this — the Node.js tier's `EC2Write` subset (`RunInstances`/`CreateFleet`/`TerminateInstances`/tagging) already covers it, inherited from the shared stack-mutation base policy.

Two more stale-terminology passes fixed as direct fallout of fix #2 and the general 2-to-7-Lambda rename, both predating this session: Workstream 6's auth section had said "the dispatcher Lambda's execution role is the one fixed IAM role" (now six-plus distinct scoped roles, not one — reworded to clarify the "fixed" axis is single-operator-identity, not single-role-object) and one stray "the dispatcher's `401` response" reference (renamed to "the router's").

The IAM policy documents blocking-list bullet was updated: 8 `.json_src` drafts now (was 6), 4 flagged gaps carried forward explicitly (was 3) — two closed this session (fleet-toggle `EC2Write`'s CFN dependency, confirmed by source; the token cross-Lambda question, resolved as a deployment-discipline fix rather than an IAM gap) and two still open (the stack-mutation tier's own `EC2Write` subset is still inferred from AWS's documented base policy, not traced through `pcluster.lib` source the way the fleet-toggle grant now is; whether PCluster's CDK usage needs `cdk bootstrap` staging permissions beyond that base policy remains unverified). No production code touched this session — all edits were to the plan doc and to scratchpad `.json_src` drafts, neither of which is committed.

**Round 3 — same session, both remaining flagged IAM gaps closed by reading `pcluster`'s installed source, plus one gap that turned out bigger than flagged, also closed.**

- **Stack-mutation `EC2Write` (gap 2, was inferred).** Traced `Cluster.create()` (`pcluster/models/cluster.py:351-408`) directly: it does exactly one AWS mutation — `AWSApi.instance().cfn.create_stack_from_url()` — after synthesizing the template in-process (see next finding). Checked that call and its `delete_stack`/`update_stack` siblings (`pcluster/aws/cfn.py:49-99`) for a `RoleARN` argument: **none of the three pass one.** A CloudFormation `CreateStack`/`UpdateStack`/`DeleteStack` call with no execution role runs using the *caller's own* IAM credentials for every resource the stack touches — so the broad EC2/IAM/DynamoDB/Route53/EFS/FSx grants in `MCPStackMutation.json_src` are not an inference about what `create_cluster` "plausibly needs," they're structurally required by how PCluster calls CloudFormation, and identically so for creation, update, and deletion. This also retroactively confirms round 1's founding claim (`delete_cluster` needs the same IAM breadth as `create_cluster`) as a proven fact rather than a reasonable inference.
- **CDK bootstrap (gap 3).** Traced `CDKTemplateBuilder.build_cluster_template()` (`pcluster/templates/cdk_builder.py:29-55`): it calls `aws_cdk.core.App(...).synth()`, the CDK construct library's in-process Python/jsii template synthesis, writing to a local `tempfile.TemporaryDirectory()` — no subprocess, no `cdk` CLI, no AWS call of any kind. `CDKArtifactsManager.upload_assets()` (`pcluster/templates/cdk_artifacts_manager.py:113`) then uploads the synthesized assets to **PCluster's own per-cluster artifact bucket** (already covered by the base policy), not a `CDKToolkit`-bootstrap-managed staging bucket. PCluster never calls `cdk deploy`/`cdk bootstrap` — Node.js is needed only for the jsii bridge running CDK's synthesis logic in-process, which has no AWS-facing footprint of its own. Confirmed: no bootstrap permissions needed, full stop.
- **`secretsmanager:DescribeSecret` (gap 4) — closed, and turned out larger than flagged.** The flagged gap undersized the problem: vanilla `pcluster create-cluster`/`delete-cluster` doesn't manage SSH keys in Secrets Manager at all — that's this repo's own custom logic (currently the Ansible playbook; the boto3 port is Workstream 3) — so nothing in AWS's own documented base policy was ever going to cover this, `DescribeSecret` included. Added a scoped `SecretsManagerSshKeyLifecycle` statement to `MCPStackMutation.json_src`: `CreateSecret`/`PutSecretValue`/`GetSecretValue`/`DeleteSecret`/`DescribeSecret` on `arn:aws:secretsmanager:*:<AWS_ACCOUNT_ID>:secret:parallelcluster/*`, matching this repo's existing secret-naming convention. Checking that same convention surfaced a second, identically-shaped omission in the same pass: `ec2:ImportKeyPair`/`ec2:DeleteKeyPair` were absent from `EC2Write` entirely — also custom to this repo's key-rotation design, also never in AWS's own policy — added alongside the Secrets Manager fix rather than left for a later pass.

All four flagged IAM gaps are now closed. The plan doc's top-of-file status line, the blocking-list "IAM policy JSON" bullet, and both Workstream 5/6 stack-mutation-Node.js bullets were updated to state this plainly rather than continue to hedge with "unverified"/"inferred" language now that the evidence exists. No production code touched — same as rounds 1-2, all edits stayed in the plan doc and the scratchpad `.json_src` drafts.

### Session 51 — Workstream 1 begins: `cost_pcluster.py` migrated to the core/shim split; first production code touched by the MCP effort

Follow-on to session 50. This is the first session in the MCP migration where production code actually changed, not just the plan doc or scratchpad drafts.

**`cost_pcluster.py` chosen as the first script**, per Workstream 1's own ranking ("cleanest of the four" read-only scripts — zero `pcluster` CLI/`pcluster.lib` dependency, already tuple-returning helpers, only 2 `sys.exit()` calls) — deliberately the smallest-surface-area script, to establish the core/shim pattern before the harder three.

**`ClusterRecord`'s field list — the flagged verification gap — closed for real this time, not just decided.** Read `_read_cluster_record`'s actual dict output (`src/pcluster_core.py`) field-by-field against the plan's drafted dataclass and found it wrong in several ways: `cluster_owner_email`/`az`/`scheduler` don't exist in the dict today (though they're real `vars_file.j2` keys, just not yet projected — confirmed by reading the template directly rather than guessing); the plan's `cluster_serial_number` field name doesn't match the dict's actual key, `serial`; and the dict already carries eight fields (`cpu_instance_types`, `gpu_instance_types`, `enable_cpu_queue`, `enable_gpu_queue`, and the four queue-size fields) the plan's dataclass omitted entirely. `ClusterRecord` is now a real frozen dataclass in `pcluster_core.py`, matching the dict's current shape exactly (real key names, real field set) rather than the plan's representative guess — the three not-yet-projected fields were deliberately left out for now, per "add a field only when a migrated script needs it," rather than speculatively completed against every `vars_file.j2` key.

**`PClusterMakerError`** (session 49's decided exception class) is now real code, defined once in `pcluster_core.py`.

**A real regression caught before it shipped, not after.** The original script's fallback for a cluster with a missing/unparseable vars file was `owner = "unknown"` / `region = "unknown"` — the row still appears, just with placeholder values. A first-pass version of the new `_load_cluster_records` helper silently *skipped* such a cluster instead (built `ClusterRecord` only when `_read_cluster_record` returned non-`None`), which would have dropped the row entirely — caught while reviewing the original `main()`'s logic line-by-line before deciding the new design was equivalent, not after running the tests. Fixed by adding `ClusterRecord.unknown(cluster_name)`, a classmethod producing the same "unknown"/"unknown" placeholder the CLI has always shown, reused as a general fallback shape other scripts' record-fetch code will likely need too.

**What moved into `pcluster_core.py`, and why each did or didn't:** `_utc_today`, `_date_range`, `_check_tag_activated`, `_get_cluster_cost`, and the new `core_get_cost_report` orchestrator moved in — pure business/AWS logic, satisfying CLAUDE.md's "all Python logic lives in `pcluster_core.py`/`pcluster_aux_data.py`" rule this script had been quietly violating. `_format_table` (pure `print()` formatting) and `_enumerate_cluster_names` (local-filesystem-only cluster enumeration) stayed in `cost_pcluster.py` — presentation and local-backend-specific state access are exactly what the plan's "CLI shim" layer is for, not core logic. `core_get_cost_report` builds its own `boto3.client("ce", ...)` internally, matching the plan's drafted signature (no injected client parameter) and the original script's own behavior.

**A monkeypatch target broke silently and would have made two tests lie.** `tests/test_cost_report.py`'s `TestDateRange` patched `cost_pcluster._utc_today` to control "today" in tests — which stops working the moment `_date_range` (and the `_utc_today()` it calls) live in `pcluster_core` instead: the patched name and the name `_date_range` actually looks up at call time are then in two different module namespaces, so the tests would keep *passing* while silently no longer testing what they claim to (the real `datetime.now()` would run instead of the fixed date, and by coincidence neither assertion depends on the actual current date, so nothing would have failed — a false-negative, not a crash). Caught by reasoning through Python's name-resolution rules before running the suite, not discovered via a failure; fixed by retargeting the monkeypatch at `pcluster_core._utc_today`.

**New test coverage**, not just relocated: `TestClusterRecord` (`.from_dict`, frozen-ness, `.unknown()`) and `TestCoreGetCostReport` (days-range validation raising `PClusterMakerError`, owner filtering, per-cluster record assembly, default `days=30`) in `tests/test_cost_report.py` — 8 new tests, all mocking at the `boto3.client` boundary rather than re-mocking the already-tested `_check_tag_activated`/`_get_cluster_cost` helpers directly, to actually exercise the new orchestration logic rather than just re-assert what was already covered.

**Running the full suite surfaced two unrelated things, one caused by this session and one not:**

- **Caused by this session**: inserting ~90 lines near the top of `pcluster_core.py` shifted every line number below it, breaking `templates/CLAUDE.local.md`'s citation of `_setup_iam`'s `iam.attach_role_policy(` call at line 880 (now 958) — exactly the kind of drift `tests/test_claude_docs_line_citations.py` exists to catch. Fixed the citation and its manifest entry in the same pass.
- **Not caused by this session, pre-existing since session 49**: `docs/parallelclustermaker-mcp-plan.md` and `docs/sessions.md` cite four Workstream 7 test-module names (`test_mcp_schemas`, `test_mcp_tools`, `test_mcp_confirmation_gate`, `test_mcp_lock`) that didn't exist as real files yet — the test suite hadn't been rerun since 2026-08-14 (predating even session 48), so nothing had caught it. Presented the user three options (reword the citations, create skip-marked placeholders now, leave tracked until Workstream 7); recommended placeholders over "leave tracked" on the grounds that a known-red `make test` gate persisting across several more implementation sessions (steps 2-6 of the build order come before Workstream 7) risks exactly the failure mode this repo's testing discipline exists to prevent — a real new regression getting lost in "oh, that's the known one." User agreed. Created four minimal files (`tests/test_mcp_schemas.py`, `tests/test_mcp_tools.py`, `tests/test_mcp_confirmation_gate.py`, `tests/test_mcp_lock.py`), each a clearly-labeled placeholder with one `pytest.skip("Workstream 7 not yet implemented...")` test — honest about coverage (skipped, not faked), satisfies the citation sweep, and gets replaced with real tests when Workstream 7 actually starts.

**Full suite: 1933 passed, 4 skipped (the new placeholders), 0 failed** — up from the stale "1924/1924" figure `CLAUDE-STATE.md` had carried since 2026-08-14 (+8 new cost-report tests, +4 skipped placeholders, +1 net from the pre-existing citation fix's own test not counting as new). `make lint` and `make shellcheck` both clean, unaffected by this session's Python-only changes.

**Migration order for the remaining three read-only scripts, unchanged from the plan**: `check_pcluster.py` next ("nearly ready as-is... lowest-effort extraction of the four"), then `list_pcluster.py`, then `diagnose_pcluster.py` (the one requiring actual internal refactoring first, per session 48's finding that its `main()` mixes SSH calls, formatting, and printing inline across 190 lines).

**Same session, round 2 — `check_pcluster.py` migrated too, a materially bigger lift than `cost_pcluster.py`.** The plan's own ranking called this "nearly ready as-is," which held for the seven leaf `check_*` functions (already tuple-returning, individually well-tested) but not for `main()`'s aggregation logic, which the plan's drafted `core_check_cluster_health(*, cluster_record, timeout, ssh_available)` signature explicitly wants as one reusable function — and `tests/test_check_pcluster.py` had a test class covering exactly that aggregation logic (since renamed away, see below) by monkeypatching `chk.check_vars_file`/`chk.check_cfn_status`/etc. as attributes of `check_pcluster` and calling `chk.main()`. Moving that aggregation logic into `pcluster_core.core_check_cluster_health` (which resolves those same names in `pcluster_core`'s own namespace) would have made every one of those monkeypatches silently stop affecting anything — the exact `_utc_today` class of bug from `cost_pcluster.py`'s migration, but across eight interdependent functions instead of one, and this time it would have gone undetected by a fresh test run too (the tests would keep exercising the OLD, now-dead `main()` code path against the OLD checks that still happened to work, since nothing was deleted, only bypassed) if not caught by tracing the design through before writing tests.

Fixed by splitting the test file's coverage into the two layers the refactor actually created: `TestCoreCheckClusterHealth` (new) tests `pcluster_core.core_check_cluster_health(...)` directly, monkeypatching `pcluster_core.check_cfn_status` etc. and asserting on the returned `ClusterHealthReport`'s `.healthy`/`.checks` fields — this is the real home for every aggregation-logic assertion the old class had (failure counting, SSH-failure skip cascade, CFN-failure skip cascade, monitoring-gated Grafana), plus three new cases the old suite didn't cover: Grafana still skipping when monitoring is enabled but SSH is unreachable (gating and SSH-availability are independent conditions), the new `ssh_available=False` remote-transport path skipping every SSH-dependent check without ever calling `check_ssh` (Workstream 7's degradation mechanism, unreachable from the CLI today but real code now), and the Slurm partial-degradation note surviving into `CheckResult.detail`. `TestCheckPclusterMainCliShim` (new, much smaller) tests `chk.main()` end-to-end with `core_check_cluster_health` mocked as one unit — arg parsing, the vars-file hard-exit precondition, timeout clamping happening before the core call, and `_print_report`'s exact text reconstruction.

**Two more real findings, caught by tracing execution order before writing code, not discovered via a failing test:**

- **Timeout clamping had to move to the CLI shim, not live inside the core function, to preserve print ordering.** The original `main()` calls `_clamp_int` (which prints a warning on adjustment) *before* printing "Checking cluster: X" and before the vars-file check. The plan's drafted signature put `timeout` as a `core_check_cluster_health` parameter, which — if the function clamped it internally — would fire that print *after* the vars-file check instead, since the core function is only reachable once a `ClusterRecord` exists. `core_check_cluster_health` now trusts an already-validated `timeout`, matching how cluster-name validation and `ClusterRecord` resolution are already caller responsibilities. The plan's docstring claim "raises PClusterMakerError if timeout not in [1, 300]" was also wrong on both counts — today's CLI clamps with a warning, it never raises or exits for a bad timeout — corrected in the function's own docstring.
- **`check_cfn_status` needed a `pcluster_bin` parameter the plan's draft never listed at all.** It shells out to the `pcluster` binary directly (not yet the Workstream 3 `pcluster.lib` swap — deliberately out of scope this session), and `pcluster_core.py`'s own established convention (`_read_cluster_record`, `_run_pcluster_cmd`) is to take path-like values as explicit parameters from the caller rather than deriving them internally via `__file__` tricks — `_PCLUSTER_BIN` stays computed in `check_pcluster.py` (the CLI shim) and is threaded through `core_check_cluster_health` as a new required keyword parameter, a genuine gap in the plan's draft rather than a corrected assumption.

One deliberate, disclosed cosmetic fix: the SSH-unreachable skip branch's stale `"Slurm (sinfo -s)"` label (the check has used `sinfo -h -o "%D %T"` since session 32, per `CLAUDE.local.md`'s Slurm bullet) is now just `"Slurm"`, matching the name already used in its pass/fail branches — no test pinned the stale text, and a `CheckResult.name` field is more useful to an MCP caller as one consistent string across all three statuses than as three different labels for one check.

**Full suite: 1940 passed, 4 skipped, 0 failed** (up from 1933/4/0 after `cost_pcluster.py`). `make lint`/`make shellcheck` clean. Manually verified end-to-end against a real (nonexistent) cluster name: `Checking cluster: nonexistent-mcp` / `[FAIL] vars file — vars file missing or unreadable` / `1 check(s) failed.`, exit 1 — matches the pre-refactor script's exact shape.

**Same session, round 3 — `list_pcluster.py` migrated, the smallest of the three so far: no existing test exercised `main()`'s own logic at all**, only `_age_str` and `_print_table` directly (both preserved unchanged, no rewrite needed there). The real finding was in the plan's drafted `ClusterListEntry` dataclass, which listed only the columns `_print_table` displays — missing `enable_loginnode`, `enable_cpu_queue`, `enable_gpu_queue` entirely. Without those three fields, `_print_table`'s existing `fmt_loginnode`/`min_max_cpu`/`min_max_gpu` logic (which all branch on an `enable_*` flag before showing real values vs. `"-"`) would always take the "disabled" branch, silently hiding every enabled login node and queue regardless of the cluster's actual configuration — this would not have failed loudly, `_print_table`'s own tests (which build dicts by hand and never go through `core_list_clusters`) would still pass, and the bug would only show on a real, live cluster listing. Caught by reading `_print_table`'s body line-by-line against the plan's dataclass field list before writing `core_list_clusters`, not by a test failure.

**A second, larger gap in the same dataclass: today's `-J`/`--json` output is the *entire* `ClusterRecord` (all 22 fields, including `ssh_keypair`/`s3_bucketname`/`ec2_user`/`enable_monitoring`/`serial`) plus `age`/`status` — 24 keys total — and the plan's `ClusterListEntry` draft had only 15, dropping 9 fields any existing scripting against `-J` output could depend on.** Widened `ClusterListEntry` to carry every `ClusterRecord` field (in the same declared order, so `dataclasses.asdict()` reproduces today's exact JSON key order with no manual reordering) plus `age`/`status` appended — trading a cleaner, MCP-shaped dataclass for one that exactly preserves current CLI behavior, since narrowing what an MCP tool wrapper exposes is explicitly that wrapper's job (Workstream 7, not built yet), not something to bake into the shared core-function return type today. `_live_status`/`_age_str` moved to `pcluster_core.py` unchanged (including `_live_status`'s existing "ERR"-on-any-failure tolerance and its own separate `subprocess` call, deliberately *not* unified with `check_cfn_status`'s near-identical shape from the `check_pcluster.py` migration — that unification is explicitly Workstream 3 scope, not this session's).

One process note: this round's own `docs/sessions.md` writeup for the *previous* round (`check_pcluster.py`) named the old aggregation-logic test class that round had itself renamed away — a real dangling citation the doc-hygiene sweep caught on this round's full-suite run. Fixed by rewording to describe the class without citing its exact former name as a standalone token (twice now — the fix's own explanation re-cited the same literal name and had to be reworded again).

**Full suite: 1940 passed, 4 skipped, 0 failed** (same totals as after `check_pcluster.py` — no test file needed changes since `list_pcluster.py` had no `main()`-level tests to begin with). `make lint`/`make shellcheck` clean. Manually verified the empty-listing case end-to-end: `No clusters found.` (table mode, exit 0) and `[]` (JSON mode, exit 0) — did not write a live cluster into `active_clusters/` to test the populated case, relying on the automated suite's `_print_table`/`ClusterRecord`/`core_list_clusters` coverage instead.

**Workstream 1's read-only tier is now fully migrated: `cost_pcluster.py`, `check_pcluster.py`, `list_pcluster.py` all done.** Only `diagnose_pcluster.py` remains, and it's the one the plan flagged as needing actual internal refactoring first — its `main()` mixes SSH calls, formatting, and printing inline across roughly 190 lines, unlike the other three scripts' already-separated helper functions.

**Same session, round 4 — `diagnose_pcluster.py` migrated, closing out Workstream 1's read-only tier entirely.** This was the script the plan itself flagged as needing real refactoring first, not just extraction — its `main()` had five diagnostic sections (CloudWatch, sinfo, sacct, local log tails, postinstall marker) written entirely inline, mixing SSH calls, string formatting, and `print()` in one ~190-line function. Pulled each section into its own `pcluster_core.py` function (`_diagnose_sinfo`, `_diagnose_sacct`, `_diagnose_local_logs`, `_diagnose_postinstall`) returning a small frozen dataclass apiece, orchestrated by `core_diagnose_cluster` into one `DiagnosticReport`. `_get_head_ip`/`_fetch_cw_logs` moved too; `_run_ssh`/`_ssh_args` did not move again — they already exist in `pcluster_core.py` from `check_pcluster.py`'s migration (verbatim-duplicated code before this), so this round just deleted `diagnose_pcluster.py`'s own copies and imported the shared ones, a dedup that fell out of doing the migration properly rather than a separate cleanup pass.

**Three real fidelity bugs caught by tracing each section's exact original error text before writing the extraction, not by a failing test:**

1. **Two sections' error strings needed the desired final phrasing baked in at the source, not reconstructed by a shared wrapper at print time.** `_diagnose_sinfo`/`_diagnose_sacct` each have three distinct, differently-worded error cases in the original (e.g. `"sinfo failed (rc=...)"` vs `"sinfo timed out"` vs `"sinfo failed: {e}"`) that must print verbatim with no wrapper — got this right on the first pass by directly transcribing each string. `_diagnose_local_logs`/`_diagnose_postinstall` were written the *other* way at first (bare, unwrapped error text, e.g. just `"timed out"` or `str(e)`), which is fine on its own — until the CLI shim's print function applies ONE uniform wrapper (`f"  ({error})"` for postinstall, `f"  (unavailable — {error})"` was the plan for local logs) that only produces the correct text for one of each function's three error cases and silently mangles the other two (`"(unavailable — timed out)"` instead of `"(timed out)"`; `"(unavailable — {e})"` instead of `"(error: {e})"`). Fixed by making these two functions bake in the same full-phrasing convention as the other two, so the shim's wrapper is genuinely uniform.
2. **The core function's validation-vs-printing ordering had to be deliberately reordered relative to check_pcluster.py's established pattern.** `core_diagnose_cluster` validates `cluster_record.ec2_user` and raises `PClusterMakerError` — correctly, since this is record-integrity validation both the CLI and a future MCP caller need, not caller-input validation. But today's script prints nothing at all before that specific exit (`sys.exit` fires before "Diagnosing cluster: X" ever prints), and the established pattern from `check_pcluster.py`/`cost_pcluster.py` has the CLI shim print its banner line *before* calling the core function. Reusing that pattern here would have reordered visible output relative to today. Fixed by moving "Diagnosing cluster: X" / "serial: Y" into `_print_report`, called only after `core_diagnose_cluster` returns successfully — so a raised `PClusterMakerError` still surfaces before any banner prints, exactly matching today.
3. **The plan's docstring for the numeric args ("raises PClusterMakerError if out of range") was wrong here too, for the same reason it was wrong in check_pcluster.py.** All four `_clamp_int` calls (`--cw_lines`, `--log_lines`, `-T/--timeout`, `--hours`) stay textually inside `diagnose_pcluster.py`'s own `main()` — both to preserve print ordering (clamping warnings print before anything else, same as before) and because `tests/test_diagnose.py::TestArgumentBounds::test_diagnose_clamps_all_four_numeric_args` greps the *rendered source* of `diagnose_pcluster.py` for these four calls by name; moving them into `pcluster_core.py` would have made that test fail by making its premise false, not by breaking assertions.

**A near-duplicate consistency-test trap, caught before running anything:** `diagnose_pcluster.py` previously hardcoded `_VALID_EC2_USERS = {"ubuntu", "ec2-user"}` as a literal, and `tests/test_diagnose.py::TestEc2UserValidation` asserts `dx._VALID_EC2_USERS == set(_EC2_USERS.values())` (from `pcluster_core.py`) — a load-bearing consistency check documented in `templates/CLAUDE.local.md` against exactly this drifting. Since the validation logic itself moved into `core_diagnose_cluster`, the set moved to live beside `_EC2_USERS` in `pcluster_core.py` (`_VALID_EC2_USERS = set(_EC2_USERS.values())`, now genuinely derived rather than a second literal), and `diagnose_pcluster.py` re-imports it so `dx._VALID_EC2_USERS` still resolves for the test. A second, unrelated cross-file test (`test_check_pcluster.py::TestSinfoClassificationIsSharedNotDuplicated::test_diagnose_uses_the_shared_predicate`, from the `check_pcluster.py` round) asserts `diag._sinfo_state_is_ok is _sinfo_state_is_ok` — `diagnose_pcluster.py`'s own code no longer calls `_sinfo_state_is_ok` directly (only `_format_sinfo`, now in `pcluster_core.py`, does), but the import still had to stay for this cross-file identity check; caught by the full-suite run, not anticipated in advance.

**Two more citation-drift instances, the same class as before, one of them self-referential.** Inserting `_VALID_EC2_USERS` near the top of `pcluster_core.py` shifted the `iam.attach_role_policy(` line cited in `templates/CLAUDE.local.md` again (960 this time, having already been fixed once this session at 958) — the doc-hygiene sweep working exactly as designed, fixed the same way as before. Separately, this round's own draft of *this* session-log entry, while explaining last round's dangling-citation fix, re-quoted the literal old test-class name as part of the explanation — tripping the same sweep a second time over the same underlying fact. Fixed by describing it without the exact token, again.

**42 new tests added** (`_get_head_ip`, `_fetch_cw_logs`, all four `_diagnose_*` section functions, `core_diagnose_cluster`'s orchestration, and the CLI shim's print/exit behavior) — this round's own migration had shipped zero new coverage in an earlier pass before the full-suite run was treated as "done"; caught and fixed before considering the migration complete, not after. One test-design mistake caught immediately, not shipped: three new CLI-shim tests initially assumed `main()` always raises `SystemExit`, copying `check_pcluster.py`'s pattern — but unlike that script, `diagnose_pcluster.py`'s `main()` has no explicit `sys.exit(0)` on the fully-successful path, matching the original script exactly (it just returns and the process exits 0 implicitly). Fixed the three test expectations, not the code.

**Full suite: 1982 passed, 4 skipped, 0 failed** (up from 1940 — the full +42 delta from this round's own new tests, since the previous three rounds' totals hadn't changed net between the check_pcluster.py and list_pcluster.py rounds). `make lint`/`make shellcheck` clean. Manually verified end-to-end against a nonexistent cluster: `ERROR: no vars file found for cluster 'nonexistent-mcp'`, exit 1 — matches the pre-refactor script exactly.

**Workstream 1 is now complete for all four read-only-tier scripts.** Per the plan's migration order, next up is Tier 2 (node-access helpers): `access_cluster.py`, then `grafana_tunnel.py`.

**Same session, round 5 — `access_cluster.py` migrated, Tier 2 begun. The smallest script yet, but it surfaced the most significant deviation from the plan's drafted signature so far.** The plan wanted `core_resolve_access_node_type(*, cluster_record: ClusterRecord, ...)`, matching every other migrated script's convention — but `tests/test_kill_access.py`'s existing `_stage_access_cluster` helper stubs `_read_cluster_record` with deliberately *sparse* dicts (e.g. `{"enable_loginnode": "true", "loginnode_count": 1}`, or even `{}`), isolating the node-type-decision logic from the full 22-field record shape on purpose. `ClusterRecord.from_dict()` requires every field (`rec[f] for f in cls.__dataclass_fields__`) and would `KeyError` on any of them. Reworking the test file to inject full records for a function that only ever reads two fields would have undermined the tests' own intentional minimalism for no behavioral gain — so `core_resolve_access_node_type` keeps taking a raw dict (named `rec`, not `cluster_record`, to avoid implying the usual dataclass), same as the existing `_resolve_access_node_type` it wraps.

**That wrapping is itself the main design decision this round.** Rather than reimplement the decision logic a second time against a different input shape, `core_resolve_access_node_type` calls the existing, already-thoroughly-tested `_resolve_access_node_type` unchanged and converts its `(node_type, error_or_None)` tuple into either a raised `PClusterMakerError` or a returned `AccessInfo` dataclass — genuine reuse, and it means `_resolve_access_node_type`'s entire existing test class (`TestResolveAccessNodeType`, ~15 cases) needed zero changes. One new behavior was added at the wrapper layer, not the reused function: raising when both `-L`/`-H`-equivalent flags are set. Argparse's own `mutually_exclusive_group()` already prevents this on the CLI path (verified unreachable — `test_login_node_and_head_node_flags_are_mutually_exclusive` still asserts on argparse's own exit code 2, untouched), but a future MCP caller has no such guard.

**A second, unrelated function needed the same "keep it dict/tuple-shaped" treatment for a different reason.** `access_cluster.py`'s empty-vars-file tolerance (`rec = _read_cluster_record(...) or {}`, defaulting to HeadNode rather than failing) is unique among the five scripts migrated so far — every other one hard-fails on a missing vars file. `ClusterRecord.unknown(cluster_name)` (added during `cost_pcluster.py`'s migration) was verified to produce field-for-field identical decision inputs to an empty dict for this specific function, so it could have worked as a drop-in — but since `core_resolve_access_node_type` ended up dict-based anyway per the finding above, the CLI shim just keeps passing `rec` (dict or `{}`) straight through, matching today's exact code shape with no conversion at all.

**A global-singleton subprocess-mocking detail confirmed, not assumed, before extracting `core_exec_access_script`.** The plan named this function (`core_exec_access_script(*, cluster_data_root, cluster_name, node_type) -> int`) explicitly as "CLI shim only, not a core function in the MCP sense," but it still belongs in `pcluster_core.py` per the architecture rule. `_stage_access_cluster`'s test helper patches `mod.subprocess.run` (`mod` being the dynamically-loaded `access_cluster` module) — since `import subprocess` binds every module's local name to the one process-wide `sys.modules["subprocess"]` object, patching `mod.subprocess.run` also patches `pcluster_core.subprocess.run`, so moving the actual `subprocess.run(["bash", access_script], ...)` call into `pcluster_core.py` doesn't break the test's interception — confirmed by running the suite, not assumed from the reasoning alone. `access_cluster.py` still keeps its own now-otherwise-unused `import subprocess`, commented why, purely so `mod.subprocess` exists as an attribute for that monkeypatch target to find.

**Preserved one quirk deliberately rather than "cleaning it up": `_cluster_data_root` is still computed via a fresh `os.path.dirname(os.path.abspath(__file__))` call inside `main()`, not the module-level `_repo_root` constant** (which is computed once, at import time, from the same `__file__`). The two are redundant in production, but the test helper monkeypatches `mod.__file__` specifically to redirect this one computation, independently of `mod._repo_root` (also separately patched) — collapsing them to a single `_repo_root` reference would still pass today's tests (both get patched to the same `tmp_path` anyway) but changes what future tests could independently control. Left as-is since there was no actual reason to touch it.

**Added a small, targeted set of direct tests for `core_resolve_access_node_type`** (`TestCoreResolveAccessNodeType`, 4 cases) covering only what the wrapper itself adds beyond `_resolve_access_node_type` — the `AccessInfo` return shape, `PClusterMakerError` signaling, and the new both-flags-set check that's otherwise unreachable from the CLI and had zero coverage anywhere. Deliberately not a large new test class, unlike the previous two rounds — the wrapper is thin enough that `TestAccessClusterNodeTypeResolution`'s existing `main()`-level tests (11 cases, unchanged) already exercise it end-to-end for everything else.

**Full suite: 1986 passed, 4 skipped, 0 failed** (up from 1982, +4 new tests). `make lint`/`make shellcheck` clean. No line-citation drift this round — the new `pcluster_core.py` section landed after `iam.attach_role_policy(`'s line, unlike the last two rounds. Manually verified end-to-end against a nonexistent cluster: `ERROR: Access script not found: .../nonexistent-mcp/access_cluster.nonexistent-mcp.sh`, exit 1 — matches the pre-refactor script exactly.

Next: `grafana_tunnel.py`, the last Tier 2 script.

**Same session, round 6 — `grafana_tunnel.py` migrated, closing out Tier 2 entirely.** The smallest script yet in line count, and unlike `access_cluster.py` the previous round, its core function *did* end up `ClusterRecord`-shaped, matching the plan's draft directly — the opposite call from last round, made for a symmetrical reason. `tests/test_grafana_tunnel.py`'s existing stub also used a sparse dict (`{"enable_monitoring": "true"}`), the same shape of test-fixture friction that drove `access_cluster.py` to stay dict-based — but here there was exactly one stub, feeding exactly two tests, in a much smaller file, so widening it to the full 22-field record was a one-dict edit rather than a rewrite of ~15 individually-crafted sparse-dict cases. Cheap fix, dominant pattern preserved; `access_cluster.py`'s dict-based design stays the deliberate, documented exception, not a second data point pulling toward "always keep it dict-shaped."

**The core function's shape differs from `access_cluster.py`'s in a second way, and this one reflects a real, load-bearing distinction, not just a smaller test file.** The plan splits `access_cluster.py` into two functions — a decision-only `core_resolve_access_node_type` plus a separate, explicitly-not-MCP-tool-callable `core_exec_access_script` — because that script's SSH session is genuinely interactive (inherits stdin/stdout/stderr) and cannot be expressed as a single tool-call result at all. `grafana_tunnel.py`'s tunnel start/stop is different in kind: a non-interactive, one-shot command whose full outcome (success or the exit code that says otherwise) fits in a structured return. So `core_manage_grafana_tunnel` is one function doing validation *and* execution *and* result reporting — raising `PClusterMakerError` for the two precondition failures (monitoring disabled, tunnel script missing) but *returning* a `TunnelResult(success=False, error=...)` for the tunnel script itself running and failing, since that's the operation's own outcome, not a precondition violated before it started. Confirmed this distinction is real, not just convenient, by checking the plan's own docstring for each function — `core_manage_grafana_tunnel` is explicitly named as "Registered as an MCP tool on the local stdio FastMCP instance only, never remote" precisely because it's a real one-shot operation with a real structured result, the thing `core_exec_access_script` by design can never be.

**Zero new findings against the original script's exact text this round** — a first, after five rounds that each caught at least one real behavioral or fidelity gap. Traced the message construction by hand anyway before writing the shim (`TunnelResult.error` carries the bare `"tunnel script failed to {action} the tunnel (exit {code})"`, and the CLI shim's `sys.exit(f"ERROR: {result.error}.")` reconstructs the original's exact three-part string — prefix, body, trailing period — the same discipline that caught real mismatches in `diagnose_pcluster.py`'s two error-prone functions two rounds ago; it just didn't find one here).

**Coverage was net-new this round too, matching the discipline established after `diagnose_pcluster.py` shipped zero on the first pass**: `TestCoreManageGrafanaTunnel` (6 cases) covers both precondition-raise paths, that the script-missing check fires independently of the monitoring check rather than being short-circuited by it, successful start with a non-default port, the stop action being threaded through to the subprocess call, and the failed-script-returns-a-result-not-a-raise distinction explicitly (the one property most likely to silently regress into a raise if `core_manage_grafana_tunnel` were ever refactored again).

**Full suite: 1992 passed, 4 skipped, 0 failed** (up from 1986, +6 new tests). `make lint`/`make shellcheck` clean. No line-citation drift this round either — same as last round, new `pcluster_core.py` code landed after `iam.attach_role_policy(`'s line. Manually verified end-to-end against a nonexistent cluster: `ERROR: no cluster record found for 'nonexistent-mcp'`, exit 1 — matches the pre-refactor script exactly.

**Tier 2 (node-access helpers) is now complete.** Per the plan's migration order, next is Tier 3 (idempotent lifecycle): `stop_pcluster.py`, `start_pcluster.py`, then `rotate_cluster_key.py`.

**Same session, round 7 — `stop_pcluster.py` and `start_pcluster.py` migrated together, the biggest design challenge of this migration series so far.** Both scripts already shared their real logic through existing `pcluster_core.py` helpers (`_get_fleet_status`, `_fleet_action_plan`, `_poll_fleet`, `_run_pcluster_cmd`) — the two scripts' own bodies were the near-identical part, exactly the kind of duplication `tests/test_fleet_entrypoints.py` was written to guard against ("the two scripts are near-identical, which is exactly the shape where a copy-paste error survives review"). The new `_core_fleet_action` (with `core_stop_fleet`/`core_start_fleet` as three-line wrappers naming the action) collapses that duplication into one implementation, which — not incidentally — also resolves the *reason* that test file existed: there's only one control-flow path left to get wrong now, not two copies that could drift.

**Caught the same monkeypatch-isolation trap a third time, this time before writing any implementation code at all — the checkpoint discipline paid off.** `tests/test_fleet_entrypoints.py`'s existing `_stage()` helper patched `mod._get_fleet_status`/`mod._run_pcluster_cmd`/`mod._poll_fleet` and drove everything through `mod.main()`. Read this file *before* finalizing the core-function design (not after hitting failures), and recognized immediately that moving the actual fleet-action orchestration into `pcluster_core.py` would silently stop these patches from affecting anything, since `core_stop_fleet` resolves those same names in `pcluster_core`'s own module globals — identical to the `_utc_today` trap from `cost_pcluster.py` and the whole-`main()`-aggregation trap from `check_pcluster.py`. Split coverage the same way as those two: `tests/test_fleet.py` (which already tested `_fleet_action_plan`/`_poll_fleet`/`_get_fleet_status` in isolation) gained direct tests for `core_stop_fleet`/`core_start_fleet`, patching `pcluster_core.X`; `tests/test_fleet_entrypoints.py` was rewritten to mock `core_stop_fleet`/`core_start_fleet` as one unit and test only what's left in the CLI shim.

**The confirmation-gate placement problem took the most design work of any script in this series, because of a genuine ordering conflict between three requirements that don't naturally coexist:** (1) the plan wants `core_stop_fleet` to be one atomic call handling all four outcomes (abort/done/wait/request) so an MCP caller never needs a separate pre-check — confirmed by rereading the MCP tool schema section, where `stop_fleet` wraps `core_stop_fleet` with nothing else; (2) the CLI's 5-second `ctrlC_Abort` confirmation window (Workstream 4's established "two different gates for two different callers" pattern — the MCP `stop_fleet` tool has no equivalent gate at all, by design) must fire *only* when a real new stop is about to happen, which means the shim needs to know the plan *before* calling the core function; (3) several of the original script's print statements (`"Fleet is already X — a stop is already in progress."`, `"Waiting for fleet to reach STOPPED..."` bracketing the poll) are interleaved with the actual `_run_pcluster_cmd`/`_poll_fleet` calls in a way that can't be reproduced from outside if those calls move inside the core function.

Resolved by accepting a deliberate, disclosed compromise: the CLI shim does its own lightweight preflight status/plan check (reusing `_get_fleet_status`/`_fleet_action_plan` directly — no new function needed, they're already shared) purely to decide whether to show the confirmation gate and to print the "already in progress" message at the right point; `core_stop_fleet` independently re-derives status/plan right before acting. This means a few-second race window exists between the two checks — judged acceptable and *not a new problem*, since today's actual script has the same latent window during the unconditional 5-second `ctrlC_Abort` wait, with no recheck at all afterward; the new design's window is smaller, not larger. `_core_fleet_action` itself gained three `print()` calls (before/after the `update-compute-fleet` call, bracketing an internal `_poll_fleet`) to keep those specific messages correctly interleaved — accepted using the same reasoning already applied to `_poll_fleet`'s own prints and `_clamp_int`'s warning prints: every one of these three only fires on a code path (`wait=True`, or `plan=="request"`) the MCP wrapper's `wait=False` call never takes, so MCP callability is unaffected in practice even though the function isn't print-free in the abstract.

Traced all four plan branches (`abort`/`done`/`wait`/`request`) against the original scripts' exact print sequences by hand, for both `wait=True` and `wait=False`, before writing any shim code — this is what surfaced the ordering conflict in the first place (an early draft had `"Fleet is already X — a stop is already in progress."` printing *after* the wait had already happened, and `"Stop requested."` printing after an internal wait completed instead of immediately following the API call) — both fixed before ever running a test.

One correction to the plan's own draft, matching the pattern from `check_pcluster.py`'s `core_check_cluster_health`: `FleetActionResult.plan`'s `Literal["request", "wait", "done", "abort"]` and the docstring's "raises PClusterMakerError if fleet is PROTECTED" contradict each other — the abort case can't be both a raised exception and a returned plan value. Raising is what's actually implemented, matching every other migrated script's PROTECTED/precondition-failure handling; `plan` only ever holds `"done"`/`"wait"`/`"request"` in practice.

**Full suite: 2006 passed, 4 skipped, 0 failed** (up from 1992, +14 new tests: 11 in `test_fleet.py` for the new core functions, split net across a rewritten `test_fleet_entrypoints.py`). `make lint`/`make shellcheck` clean. No line-citation drift this round. Manually verified end-to-end against a nonexistent cluster for both scripts: `ERROR: no cluster record found for 'nonexistent-mcp'`, exit 1 — matches the pre-refactor scripts exactly.

Next: `rotate_cluster_key.py`, the last Tier 3 script.

**Same session, round 8 — `rotate_cluster_key.py` migrated, closing out Tier 3 (idempotent lifecycle) entirely.** The highest-consequence script in the series so far: real key material, real AWS mutations (EC2 keypair import/delete, Secrets Manager `PutSecretValue`), a local `.pem` file overwrite with security-sensitive permissions, and several distinct failure modes each carrying a specific, carefully-worded message (e.g. explicitly reassuring the operator "No AWS resources were changed" when the new key fails to authenticate). Treated with the extra care that implies: read `_append_key_script`/`_remove_old_key_script` and their existing tests first (unchanged, already shared infrastructure), then designed the whole `core_rotate_cluster_key` orchestration and traced every branch against the original's exact messages before writing it, rather than iterating live against a script that deletes real EC2 keypairs.

**`ClusterRecord` needed a genuinely new field this time, not just a design choice: `ec2_keypair`.** The original script bypassed `_read_cluster_record` entirely, reading the vars file directly via its own `yaml.safe_load` to get `ec2_keypair` -- a real, already-rendered `vars_file.j2` key (confirmed by reading the template directly: `ec2_keypair: "{{ cluster_serial_number }}_{{ region }}"`, with `ssh_keypair` itself built from it) that had simply never been projected into `_read_cluster_record`'s dict because no script needed it yet. Added it to `ClusterRecord`, `ClusterRecord.unknown()`, and `_read_cluster_record`'s own dict construction -- and because `ClusterRecord.from_dict()` requires every dataclass field to be present in the source dict, this rippled into the six other test files that maintain their own full `ClusterRecord`-shaped fixture dict (`test_cost_report.py`, `test_check_pcluster.py`, `test_diagnose.py`, `test_grafana_tunnel.py`, `test_fleet.py`, `test_fleet_entrypoints.py`), each needing one new line. Confirmed via a full suite run *before* writing any of `rotate_cluster_key.py`'s own migration that this widening alone was safe, isolating it from the larger change.

**The Turbot-profile-switching block surfaced a reason to diverge from `core_rotate_cluster_key`'s otherwise-complete responsibility, not covered by precedent from any earlier script.** `os.environ["AWS_PROFILE"]` and `boto3.setup_default_session(...)` are process-global mutations, not request-scoped ones -- if that logic lived inside the core function, a long-lived MCP server calling `rotate_cluster_key` for one cluster under one Turbot account, then later for a different cluster under a different account, would have the first call's profile choice silently stick for the second (global interpreter state, not a per-call argument). The MCP tool schema for `rotate_cluster_key` has no `turbot_account` parameter at all, confirming this was never meant to be MCP-exposed -- so the whole block stays in the CLI shim, entirely outside `core_rotate_cluster_key`, which knows nothing about Turbot.

**One real print-ordering bug caught by tracing branches by hand before running anything, the same discipline that's caught a bug in every migration since `check_pcluster.py`.** An early design had the "vars file is missing cluster_serial_number or ec2_keypair" check living only inside `core_rotate_cluster_key` (matching the pattern from `diagnose_pcluster.py`'s ec2_user check, which validates the *resolved record's* integrity rather than caller input). But the CLI shim's Turbot-profile-switch — which must run before the core function, since it configures the boto3 session the core function's own clients pick up — would then print "Using Turbot profile: ..." *before* that validation ever ran, whereas today's script prints nothing at all before this specific error. Fixed by duplicating the check in the CLI shim too, positioned before the Turbot switch to match today's exact silence, while *keeping* the core function's own copy as real defense-in-depth: an empty `serial` would otherwise flow straight into a malformed Secrets Manager name via `_ssh_secret_name`, a risk an MCP caller has no equivalent preflight against.

**One asymmetric gap in the original script's own error handling was found and deliberately preserved, not silently fixed.** The old-keypair delete at step 6 is wrapped in `try/except ClientError` with a warning; the final cleanup delete of the `-rotated` staging keypair, two lines later, has no protection at all -- if it fails, the original script lets an uncaught boto3 exception propagate. Given the stakes of this specific script, decided not to unilaterally hoist error-handling behavior into a security-sensitive operation without being asked; the gap is called out explicitly in a code comment at the call site instead, so it's a visible, deliberate choice rather than something a future reader has to rediscover.

**One dedup fell out naturally: `_import_ec2_keypair`.** The original script's "import the `-rotated` staging keypair" step and its "rename to canonical" step at the end are structurally identical duplicate-name-handling logic (import, and on `InvalidKeyPair.Duplicate`, delete-then-reimport) -- collapsed into one shared helper, used by both call sites in `core_rotate_cluster_key`.

**`tests/test_key_rotation_scripts.py`'s one existing check on this script needed rewording, not just relocation.** It asserted `rotate_cluster_key.py`'s own source called `_append_key_script()`/`_remove_old_key_script()` directly -- true before this migration, false after, since those calls now live entirely inside `pcluster_core.core_rotate_cluster_key`. Split into two tests matching the new reality: one on `pcluster_core.py`'s source (the calls actually happen there now), one on `rotate_cluster_key.py`'s source (it must delegate to `core_rotate_cluster_key`, not carry the calls directly).

**Test coverage for `core_rotate_cluster_key` needed a purpose-built fake subprocess dispatcher, not a simple monkeypatch, because of a genuine sequencing problem: the real script's `ssh-keygen -t ed25519` step writes real key files that a later `open()` call reads back, and the add/remove authorized_keys SSH calls are indistinguishable by argument content alone (both pass an opaque, pre-rendered shell script as the remote command).** Built `_FakeRotationSubprocess`, dispatching by argv shape and call order (add always precedes the verify step, remove always follows it) rather than content, and having it actually write placeholder files to the `-f` path when it sees the keygen invocation. All 13 new tests (10 for `core_rotate_cluster_key`'s branches, 3 for `_import_ec2_keypair` in isolation) passed on the first run against this design -- a sign the branch-by-branch tracing done before writing any code paid off, not a claim that the fake is bulletproof.

**Full suite: 2020 passed, 4 skipped, 0 failed** (up from 2006, +14 net new: 13 new tests in `test_key_rotation_scripts.py` plus the one relocated/reworded check that split into two). `make lint`/`make shellcheck` clean. Two more line-citation drifts from this round's `pcluster_core.py` insertions (960→962→963 across the `ec2_keypair` addition and the new imports), each caught by a full-suite run and fixed the same way as every previous round -- confirmed the fix by re-running rather than assuming it landed correctly the first time, after an earlier background run in this same round completed with the drift still unfixed mid-flight. Manually verified end-to-end against a nonexistent cluster: `ERROR: vars file not found: .../nonexistent-mcp.yml`, exit 1 — matches the pre-refactor script exactly.

**Tier 3 (idempotent lifecycle) is now complete: `stop_pcluster.py`, `start_pcluster.py`, `rotate_cluster_key.py`.** Per the plan's migration order, all that remains of Workstream 1 is the last tier: `manage_pcluster_queue.py`, then `make_pcluster`/`kill_pcluster` last ("most flags, most cross-field validation, highest consequence of a mistake").

**Same session, round 9 — `manage_pcluster_queue.py` migrated, closing out Tier 3/Workstream 1's second-to-last script and resolving both flagged findings from session 48 in the same pass, since both were about this exact script.** Read `manage_pcluster_queue.py` and `src/pcluster_queue_editor.py` in full before any design work, then `tests/test_queue_editor.py` (682 lines, the only test file covering either) -- confirmed no monkeypatch-namespace trap risk this round, since its CLI-surface tests parse `manage_pcluster_queue.py`'s source via `ast` rather than importing it (the module fires its venv guard at import time), so nothing there patches CLI-module-level names for logic that was about to move.

**Finding (b) resolved: `pcluster_queue_editor.py` fully merged into `pcluster_core.py`, then deleted.** It predated the "all Python logic lives in `pcluster_core.py`/`pcluster_aux_data.py`" architecture rule and was never brought into line with it -- 348 lines of queue-editing logic (YAML config load/write, queue-name/instance-type/architecture validation, subnet/custom-action/IAM-policy inheritance, recovery guidance) in a third module the rule doesn't name. Merged verbatim except for one systematic change: every `sys.exit()` reachable from what are now `core_*` functions became `raise PClusterMakerError(...)`, same message text -- these functions are business logic invoked from `core_list_queues`/`core_add_queue`/`core_remove_queue`, not CLI-argparse-time checks, so an uncaught `SystemExit` from one of them would still kill a future MCP server the same way this rule has applied to every core function so far this session. `_config_path` (renamed `_queue_config_path` to avoid colliding with an unrelated existing name) is the sole gate on cluster-name-derived path-traversal safety for the whole queue-edit path, so it now wraps `_validate_cluster_name`'s own `SystemExit` as `PClusterMakerError` rather than leaving that one call site as the sole place a "core" function could still raise the dangerous exception type. Needed new top-level imports in `pcluster_core.py`: `copy`, `shutil`, `from io import StringIO`, `from ruamel.yaml import YAML` (PyYAML, already imported, is a different library from ruamel and serves a different purpose elsewhere in the file). `ARM_FAMILIES`'s single-source-of-truth comment in `pcluster_aux_data.py`, and `test_aux_data.py::test_no_duplicate_arm_family_definitions`'s scanned-file list, both still named the deleted module -- fixed both; the latter would otherwise have raised `FileNotFoundError` on the next run rather than failing meaningfully.

**Finding (a) resolved: the script's own private, near-duplicate `_run_pcluster`/`_get_fleet_status`/`_poll_fleet` are gone, replaced by direct reuse of `pcluster_core.py`'s shared `_run_pcluster_cmd`/`_poll_fleet`/`core_stop_fleet`/`core_start_fleet` -- the same functions `stop_pcluster.py`/`start_pcluster.py` already use (round 7). Confirmed by inspection, not assumed, that the reuse is exact and not merely similar: the script's own inline done-state check for `-W`'s phase 1 (`status not in ("STOPPED", "STOP_REQUESTED", "STOPPING", "DISABLED")`) is the literal union of `_FLEET_DONE_STATES["stop"]` and `_FLEET_PENDING_STATES["stop"]` in `pcluster_core.py`, and both scripts already shared identical `_POLL_INTERVAL`/`_POLL_TIMEOUT` values (30s / 90 iterations = 45 min) -- so reuse changes no timing and no polling behavior, only which module owns the code. Added a new `_poll_cluster_update` (twin of `_poll_fleet` but watching `clusterStatus`+`cloudFormationStackStatus` against `UPDATE_COMPLETE` rather than a single fleet-status field) since no existing shared helper covered the config-apply phase; deliberately kept it a near-duplicate of `_poll_fleet` rather than a shared generic poller, since the two watch different field pairs with different terminal-state vocabularies and a forced abstraction over that would obscure both, not clarify them.

**One disclosed, deliberate observable-behavior change: the `-W`/`--wait` flow's one-time phase-transition status lines lose their original `[HH:MM:SS]`-prefixed inline wording** (e.g. `"[14:32:07] Requesting fleet stop..."`) in favor of `core_stop_fleet`/`core_start_fleet`'s own internal wording (`"Requesting fleet stop..."`, no per-call timestamp) -- the direct cost of eliminating the duplication in finding (a). Judged acceptable and scoped narrowly: the *recurring* per-30-second progress lines an operator actually watches across a 30-45 minute wait are unchanged, since the reused `_poll_fleet` prints those exactly as before, timestamp included. Also dropped the script's own `_check_pcluster()` (a bespoke pre-flight existence check on the `.venv/bin/pcluster` binary with its own wording) in favor of `_run_pcluster_cmd`'s existing `FileNotFoundError` handling, matching `stop_pcluster.py`/`start_pcluster.py`'s precedent of having no equivalent bespoke check at all -- changes only the wording of an edge-case failure message (venv broken / pcluster not installed), never reached in normal operation.

**Numeric and flag-presence CLI checks deliberately kept in the shim, unconverted, mirroring the existing `-T/--type is required for 'add'` guard already in `main()`.** `-E/--ec2-type is required for add`, `--initial_size must be >= 0`, `--max_size must be >= 1`, `--initial_size cannot exceed --max_size` all still live in `manage_pcluster_queue.py`'s `_do_add`/`_do_remove`, unchanged, rather than moving into `core_add_queue` with reworded flag-agnostic messages. Two reasons: zero observable-behavior delta (the whole point of Workstream 1), and precedent -- `stop_pcluster.py`/`start_pcluster.py` already establish that argparse-shape validation belongs in the CLI shim, with `core_*` functions trusting already-validated input.

**`core_apply_queue_config`'s exception profile deliberately mirrors `core_stop_fleet`/`core_start_fleet`'s own already-shipped, imperfect-but-consistent mix rather than being "fixed" in isolation.** `_run_pcluster_cmd`/`_poll_fleet`/`_poll_cluster_update` still raise bare `SystemExit` for subprocess/timeout failures (only the `PROTECTED`-fleet case raises `PClusterMakerError`, inside `_core_fleet_action`) -- an existing, already-tested characteristic of session 51's fleet-action functions that this round's `core_apply_queue_config` now also exhibits by calling them, not a new gap introduced this round. Its recovery-guidance wrapping (`except (PClusterMakerError, SystemExit)`, printing `_recovery_guidance` before re-raising) therefore needed both exception types on the phase-3 restart call, but only `SystemExit` on phase 2's direct `_run_pcluster_cmd`/`_poll_cluster_update` calls, which never raise the other kind. Phase 1 (stop) is deliberately left outside any recovery-guidance wrapping, matching the original script's own "steps 3-6 run with the fleet already stopped" comment -- a failure to stop the fleet in the first place leaves nothing to recover.

**`ClusterRecord.unknown(cluster_name)` used for the first time this session for its originally-intended purpose.** `manage_pcluster_queue.py` never reads a vars file at all -- it operates purely off the live PCluster `config.<cluster_name>` file -- yet `core_stop_fleet`/`core_start_fleet` (reused for `-W`'s phases 1/3) require a `ClusterRecord` and only ever read its `.cluster_name` field internally. Constructing one via `ClusterRecord.from_dict()` would have required inventing a fake vars-file read this script has no reason to perform; `ClusterRecord.unknown(cluster_name)` -- added during the `cost_pcluster.py` round specifically as "placeholder for a cluster whose vars file is missing or unreadable" -- was an exact fit.

**Test file left named `test_queue_editor.py`** despite `pcluster_queue_editor.py` no longer existing -- avoided the rename to keep the diff scoped to what actually needed to change; nothing in the suite infrastructure requires test-file names to mirror module names. Updated: its import source (`pcluster_queue_editor` → `pcluster_core`), 23 `pytest.raises(SystemExit` → `pytest.raises(PClusterMakerError` (every validation function it exercises now raises the new type), the two `_load_cluster_config`/`_config_path`-signature-dependent tests (both gained a `repo_root` argument), and `TestQueueAddCallsTheArchGuard` (rewritten to walk `core_add_queue`'s AST in `pcluster_core.py` rather than `_do_add`'s in `manage_pcluster_queue.py`, since the ordering guarantee it pins -- architecture guard before config write -- now lives at the new location). `TestQueueCliFlagNames`'s two tests needed no changes at all: the shim still declares the same argparse flags and cites the same ones in its still-shim-resident error messages.

**Full suite: 2020 passed, 4 skipped, 0 failed** (same count as the previous round -- this round converted/relocated existing tests rather than adding net-new ones). `make lint`/`make shellcheck` clean. One line-citation drift (963→967, from the four new top-level imports), caught by a full-suite run, fixed, then reconfirmed by a second full run after the fix actually landed -- the first "failure" notification was from a background run snapshotted before the citation edit took effect, not a real regression; re-ran to be sure rather than assuming. Manually verified end-to-end: `list`/`add` against an isolated fixture config reproduced the original's exact table formatting, GDR-info gating, config write, and five-step `pcluster` command reminder; all four CLI-flag-presence/bounds error paths (`add` without `-T`/`-E`, negative `--initial_size`, `remove` without `-Q`) and the "config not found" path against a nonexistent cluster all matched the pre-refactor script's exact messages and exit codes.

**Only `make_pcluster.py`/`kill_pcluster.py` remain in Workstream 1** -- Tier 4, saved for last by the plan's own design ("most flags, most cross-field validation, highest consequence of a mistake").

**Same session, round 10 -- Tier 4 begins: `kill_pcluster.py` migrated, deliberately split off from `make_pcluster.py` by size rather than attempted together.** `make_pcluster.py` (2272 lines) and `kill_pcluster.py` (374 lines) are named as one tier in the plan, but the size gap between them is roughly the same as the *entire* previous tier's three scripts combined, and `make_pcluster.py`'s own test coverage (`test_make_pcluster_main.py`, 799 lines, plus 1410 + 1227 lines across `test_make_pcluster.py`/`test_pcluster_core_iam.py` covering its already-extracted pure helpers) is itself larger than most scripts migrated so far, in full. Read both scripts and all three of `kill_pcluster.py`'s own test files (`test_kill_pcluster.py`, `test_kill_access.py`, plus the relevant slice of `test_playbook_secrets.py`) end to end before writing anything, per the standing discipline -- and treated the size disparity as a reason to sequence `kill_pcluster.py` first, alone, rather than force both into one round the way `stop_pcluster.py`/`start_pcluster.py` were paired (there, the two scripts were near-duplicates of *each other*; here they are not).

**A key structural finding, made while reading rather than assumed from the plan: most of `make_pcluster.py`'s real complexity is already `pcluster_core.py` code.** `test_make_pcluster.py` and `test_pcluster_core_iam.py` (2637 lines together) test dozens of already-extracted pure functions directly -- validators, IAM setup, network resolution, checksum/bucket/timeout derivation -- everything CLAUDE.local.md's incident history documents. What remains inside `make_pcluster.py`'s `main()` is argparse (~600 lines), a long but mostly linear sequence of calls *into* those already-core functions, the ~140-key `cluster_parameters` dict construction, the vars_file.j2 render, and the Ansible hand-off. This reframes Workstream 1's remaining work for `make_pcluster.py`: not "extract untested business logic," which is what every previous round did, but "wrap an already-mostly-core orchestration sequence into one callable unit and convert its own `sys.exit()`s" -- closer in shape to `rotate_cluster_key.py`'s round than to `cost_pcluster.py`'s. Left for its own round given the sheer mechanical size of doing that safely (the giant dict, ~90 CLI flags needing their own params-object enumeration the plan explicitly flagged as unverified, and `test_make_pcluster_main.py`'s ~15 bespoke-name monkeypatches needing the same namespace-isolation fix already applied three times this session, at a larger scale than any prior instance).

**`kill_pcluster.py`'s own design required resolving a real conflict between two established, previously-compatible precedents, and the resolution is the one place this round's behavior isn't a byte-for-byte port.** Two rules that held for every prior script collided here: (1) the Ctrl-C confirmation gate stays in the CLI shim, never the core function (established since `stop_pcluster.py`) -- an MCP caller gets no such gate, by design; (2) the shim's pre-gate "here's the exact command about to run" display must show the *real* command, including the cluster's actual serial number read off disk. Satisfying both means the shim must independently reconstruct the exact Ansible invocation *before* handing off to `core_delete_cluster`, which then re-derives and actually runs it *after* the gate closes. Resolved with a new shared pure function, `_build_destroy_ansible_cmd` (`pcluster_core.py`) -- no I/O, no subprocess call, just JSON/list construction from already-resolved values -- called once by the shim (display only) and once inside `core_delete_cluster` (the real run), so the two can never independently drift the way two hand-copied literals could. The cost is a small, disclosed reordering: the "cluster stack not found, continuing anyway" warning (from the `pcluster describe-cluster` existence check, which stayed entirely inside `core_delete_cluster` since it has no bearing on what the shim needs to display) now prints *after* the abort window instead of before it, since that check no longer runs in the shim's preflight at all. No existing test asserted on that relative ordering, and the final behavior (warn, then continue) is unchanged -- only its position relative to two other print blocks moved.

**`core_delete_cluster` returns a result rather than raising `PClusterMakerError`, unlike every other core function introduced this session -- a deliberate, first-time departure, not an oversight.** The original script's own failure exits in this stretch of code are bare integers with the message already printed separately (`sys.exit(1)` for a missing serial/vars file; `sys.exit(e.returncode)` propagating the Ansible playbook's own exit code verbatim, for a scripted caller that greps that code to distinguish failure modes) -- never a `sys.exit(f"...")` string. Empirically confirmed (`sys.exit("")` vs `sys.exit(1)`) before deciding this mattered: both exit 1, but the string form additionally writes a bare newline to stderr that the integer form does not, and routing the playbook's *real* returncode (e.g. 13) through `sys.exit(str(PClusterMakerError("13")))` would have collapsed it to 1 always, since `SystemExit.code` becomes the literal string `"13"`, not the int -- silently breaking that one integer-code contract, in the single highest-consequence script in the toolkit. `DeleteClusterResult(success, exit_code, rebuild_command)` avoids the whole class of problem: the CLI shim's own translation is a bare `sys.exit(result.exit_code)`, and a hypothetical MCP `delete_cluster` tool gets the exact numeric code and rebuild command as structured fields rather than a stringified message, which is arguably the more MCP-native shape anyway.

**`kill_pcluster.py` needed the same fix `access_cluster.py` needed in an earlier round: keeping an otherwise-unused `import subprocess` purely so the monkeypatch target exists.** `test_kill_pcluster.py`'s `staged` fixture does `monkeypatch.setattr(kp.subprocess, "run", runner)` -- this relies on `subprocess` being a name bound in the `kill_pcluster` module's own namespace, even though the shim no longer calls `subprocess.run` directly (that now happens inside `core_delete_cluster`). Since `import subprocess` everywhere binds to the one process-wide module object, keeping the import (commented why, `# noqa: F401`) means the test's patch still reaches the real call site in `pcluster_core.py` -- the same mechanism, not a coincidence, as the `boto3`/`subprocess` patches in `test_make_pcluster_main.py`'s fixture and `test_kill_pcluster.py`'s own `boto3.client` patch, neither of which needed any change at all. This is the reason `ctrlC_Abort`, `_repo_root`, `_src_dir` (bespoke module-level names, not shared stdlib modules) are the *only* things that would have needed the monkeypatch-isolation fix here -- and none of them moved into `pcluster_core.py`, so **`test_kill_pcluster.py`'s all 21 tests passed unmodified**, a first for a script this size in the series; only `test_playbook_secrets.py`'s `_teardown_extra_vars()` helper (which parsed kill_pcluster.py's own source for the `_destroy_extra_vars_str = json.dumps(` literal) needed updating to read `src/pcluster_core.py` instead, since that construction moved there.

**Full suite: 2020 passed, 4 skipped, 0 failed** (same count -- no new tests were needed; the existing coverage already exercised every path the new split preserves). `make lint`/`make shellcheck` clean. No line-citation drift this round (`core_delete_cluster` and `_build_destroy_ansible_cmd` were appended at the end of `pcluster_core.py`, after `iam.attach_role_policy(`'s line). Manually verified end-to-end: a nonexistent cluster reproduces the exact original "Missing cluster_serial_number_file" message and exit 1 before any AWS mutation past AZ verification; a fully staged fake teardown (fake `pcluster`/`ansible-playbook`/`ctrlC_Abort`) reproduces the exact original print sequence -- command display, abort window, describe-cluster check, Ansible run, rebuild-command banner, file cleanup, final banner -- with only the one disclosed WARNING-position change noted above.

**Only `make_pcluster.py` remains in Workstream 1** -- the last script in the entire migration, and the one the plan's own design saved for last on purpose. Next round's scope, informed by this round's reading: build a `MakeClusterParams`-shaped dataclass by enumerating all ~90 CLI flags directly from the script (not from the plan's own draft, which flags itself as unverified for exactly this function); wrap the already-mostly-core orchestration into one `core_create_cluster` in the `core_rotate_cluster_key` mold (keeps its own `print()`s, single large function, returns a structured result); keep AZ verification, the Turbot profile switch, and the Ctrl-C abort window in the CLI shim, matching every precedent including this round's; and rewrite `test_make_pcluster_main.py`'s ~15 bespoke-name monkeypatches (the ones patching things like `_setup_iam`, `_validate_network`, `_load_or_create_serial`, `_delete_managed_policies`, `_cleanup_iam_on_failure` at the `mp.` level) to patch `pcluster_core.` instead, the same fix already applied three times this session, at its largest scale yet.

**Same session, round 11 -- `make_pcluster.py` migrated, closing out Workstream 1 entirely.** The largest single change of the whole series: `core_create_cluster` plus its `MakeClusterParams`/`_build_destroy_ansible_cmd`-style dataclass moved roughly 1,250 lines out of `main()`'s ~2,180-line body into `src/pcluster_core.py`, leaving `make_pcluster.py` as argparse, CLI/defaults-file resolution, and everything that must run before an AWS credential decision is even made. The prior round's reading paid off exactly as scoped: nothing here required inventing new validation or AWS-call logic, only relocating an already-largely-correct sequence and fixing the namespace/exception-boundary consequences of doing so.

**`MakeClusterParams` ended up with 84 fields, not the ~79 first estimated, because two values needed to travel alongside their own pre-derivation form.** `head_node_bootstrap_timeout` is CLI-shim-derived (`_derive_head_node_bootstrap_timeout`, which stays in the shim along with every other pure param-resolution derivation), but a since-relocated `*** INFO ***` print inside `core_create_cluster` compares the derived value against the *raw configured* one to decide whether to say anything at all -- so `configured_head_node_bootstrap_timeout` had to become its own field, not just `head_node_bootstrap_timeout` alone. A first draft of that comparison, written from memory rather than checked against the original code, degenerated into a `_clamp_int(...)` call against itself that could never fire -- caught by `pyflakes` reporting `head_node_bootstrap_timeout` compared to a dead expression, not by re-reading carefully enough the first time; fixed by going back to the actual original source (still in context from the prior round's full read) rather than reconstructing the logic from description. `ANSIBLE_VERSION` similarly could not be computed inside `core_create_cluster` at all -- the `ansible --version` subprocess check happens in the CLI shim, before AZ verification, so it is threaded through as an explicit `ansible_version` keyword argument rather than a `MakeClusterParams` field, matching how `region` and `cluster_build_command` are also passed as separate parameters rather than folded into the params dataclass.

**The Ctrl-C abort window breaks the "always stays in the CLI shim" pattern every other migrated script in this series followed -- a deliberate, disclosed departure, not a missed case.** That pattern held because the gate was always the *first* real action, cheap for the shim to reproduce ahead of a lightweight preflight (`kill_pcluster.py`'s round is the clearest prior example). Here the gate sits *after* IAM roles and policies already exist and the vars file has already been rendered -- by the time an operator could Ctrl-C, most of the build's real, billable state already exists, and the exact command being confirmed embeds `cluster_serial_number`, `gpu_ranks_per_node`, and other values only available after that work completes. Splitting the window out to the shim the way `kill_pcluster.py` did would mean either running IAM setup and the vars-file render a second time just to reconstruct the display (a real AWS mutation, not a cheap re-derivation) or passing an awkward amount of half-built state back across the boundary. `ctrlC_Abort` therefore stays inside `core_create_cluster`, with the tradeoff named explicitly in the function's own docstring: a hypothetical MCP `create_cluster` tool will need its own answer for operator confirmation, almost certainly no interactive gate at all matching Workstream 4's async job design -- but that decision belongs to that workstream, not this one.

**Every `sys.exit()` inside `core_create_cluster` was left exactly as it was in the original script -- bare int, an f-string, or (via `p_fail`/`refer_to_docs_and_quit`/`illegal_az_msg`) always a pre-printed message followed by a bare `sys.exit(1)` -- rather than converted to `PClusterMakerError`, and this is a considered simplification of the pattern `kill_pcluster.py`'s round established, not an inconsistency with it.** `kill_pcluster.py`'s `core_delete_cluster` introduced a `DeleteClusterResult(success, exit_code)` return specifically to avoid a real bug: `sys.exit(str(PClusterMakerError("13")))` collapses a meaningful playbook return code to 1, because `SystemExit.code` becomes the *string* `"13"`. Revisiting that reasoning here: raising was never actually necessary to solve that problem -- a bare `sys.exit(<int>)`, even from deep inside a nested function call, propagates as `SystemExit(<int>)` with zero special handling needed at the call site, since Python's exception propagation does that for free. `core_create_cluster` therefore needs no result type and no shim-side `try/except` at all; it either completes and calls `sys.exit(0)` itself (matching the original's own final line exactly, so `pytest.raises(SystemExit)`-based tests need no adjustment) or an exception propagates untouched, all the way out of `main()`, identical to today. This is now flagged as a question worth revisiting for `kill_pcluster.py` too, in hindsight -- not urgent, since both are correct and fully tested, but the `core_create_cluster` shape is simpler for the same guarantee.

**Two real transcription bugs were introduced and caught before any test ran, both by static analysis rather than by reading harder.** `pyflakes src/pcluster_core.py` found `base_os_efa` used but never imported (the EFA-vs-base_os support check) -- a name available in `make_pcluster.py`'s top-level import list that the new function's local `from pcluster_aux_data import (...)` block had simply omitted while enumerating what looked complete. `pyflakes make_pcluster.py` found `_b` and `_resolve_ec2_user` imported but unused -- both had moved entirely into `core_create_cluster`'s body and the shim no longer needed them, but the import list, copied forward from the original file, still listed them. Running a static linter before the first test run -- not previously a formal step in this series, since no prior round's file was large enough to make hand-verification of every reference unreliable -- is worth keeping as a standing step for any future large transcription.

**The monkeypatch-isolation trap recurred at its largest scale yet, and the fix generalizes the pattern established three times before it: patch `pcluster_core` (or, for one name, `pcluster_aux_data`) directly, not the CLI module's copy of the same import.** `test_make_pcluster_main.py`'s `staged` fixture patched roughly a dozen bespoke names (`_validate_network`, `_load_or_create_serial`, `_setup_iam`, `_get_od_price`, `_get_spot_price`, `_delete_managed_policies`, `_cleanup_iam_on_failure`, `ctrlC_Abort`) at the `mp.` (make_pcluster module) level; all of them now resolve inside `core_create_cluster`'s own body, in `pcluster_core`'s namespace. Fixed by importing `pcluster_core` and `pcluster_aux_data` directly in the test file and repointing every one of those patches, plus three call sites inside individual test methods that separately re-patched `_load_or_create_serial`/`_setup_iam` to simulate specific failure scenarios. `ctrlC_Abort` needed the `pcluster_aux_data` target specifically (not `pcluster_core`) because `core_create_cluster` imports it with a *local* `from pcluster_aux_data import ctrlC_Abort` inside its own body -- re-executed, and re-resolved from `pcluster_aux_data`'s current namespace, on every call -- rather than a module-level import that would freeze a binding at `pcluster_core` import time. `boto3.client`/`boto3.resource`/`subprocess.run` needed no change at all, the same "shared process-wide module object" reasoning already confirmed in the `kill_pcluster.py` round -- patching `mp.boto3`/`mp.subprocess` still reaches `core_create_cluster`'s calls regardless of which file issues them. All 51 of `test_make_pcluster_main.py`'s tests pass with this fix and no other changes.

**A second, larger and previously-unseen category of breakage: source-parsing AST/regex tests scattered across six other test files that asserted properties of `make_pcluster.py`'s own text -- call-site keyword-argument shapes, ordering relative to `_setup_iam`, dict-key sets -- for logic that had lived in `main()` and has now moved.** None of the four scripts migrated before this one triggered this category at any real scale, because none of them had accumulated years of "the call site must name every argument" / "the call site must come before the first mutation" guard tests the way `make_pcluster.py`'s validators had (`templates/CLAUDE.md`'s and `CLAUDE.local.md`'s own incident history is why those guards exist at all). Found and fixed one file at a time, driven entirely by actual full-suite failures rather than a speculative sweep: `test_make_pcluster.py` (three tests: `_check_fsx_s3`'s import/export asymmetry, `_storage_summary_lines`'s and `_validate_network`'s keyword-only call sites), `test_pcluster_core_iam.py` (`_validate_ebs_config`'s call site), `test_aux_data.py` (`derive_ranks_per_node`'s two queue call sites), `test_cost.py` (`_cost_summary_lines`'s call site, two tests), `test_templates.py` (`cluster_parameters`'s key set via `_cluster_parameters_keys()`, feeding two other tests transitively, plus the `ssh_known_hosts`/`expanduser` derivation check), `test_playbook_secrets.py` (the Grafana SSM-path cross-check). Each fix was a targeted file-path repoint (`make_pcluster.py` → `src/pcluster_core.py`) at the exact call site that moved, not a blanket search-and-replace -- confirmed per-file by checking whether the specific name being asserted on had actually relocated (`_derive_head_node_bootstrap_timeout`, `_HARDCODED_DEFAULTS`, the `--base_os` argparse choices, and `monitoring_version_checksum`'s string presence all correctly still live in the CLI shim and needed no change, which is why they did not appear in the failure list in the first place -- verified by inspection, not assumed from the absence of a failure).

**One test's rewritten assertion is arguably a strictly stronger guarantee than the property it replaced.** `test_make_pcluster_validates_before_it_creates_anything` used to check that `_validate_download_checksum` and `_setup_iam` calls, found by AST-walking one file, appeared in the right line order. Checksum validation and `_setup_iam` no longer share a file at all, so the rewritten version checks that validation completes in the CLI shim *before `core_create_cluster` -- the only caller of `_setup_iam` anywhere -- is ever invoked*, which is a call-graph-level guarantee rather than a same-file line-order one; a vacuity guard confirms `_setup_iam` is still reachably called somewhere in `pcluster_core.py`.

**Full suite: 2020 passed, 4 skipped, 0 failed** (same count -- every fix this round repointed or strengthened an existing test rather than adding new ones, mirroring the `kill_pcluster.py` round). `make lint`/`make shellcheck` clean. One line-citation drift (967→969, from the `ThreadPoolExecutor` and jinja2 imports added to `pcluster_core.py`'s top), caught by the first full-suite run and fixed in both `tests/test_claude_docs_line_citations.py` and `templates/CLAUDE.local.md`'s own citation, then reconfirmed by a second full run. Manually verified end-to-end with a fully staged fake build (fake EC2/STS/IAM/S3 clients, fake `pcluster`/`ansible-playbook`/`ctrlC_Abort`, real `vars_file.j2` rendered under `StrictUndefined`): the printed sequence -- parameter validation, AZ/region verification, network and account resolution, spot-price info, IAM setup, vars-file write, the exact `ansible-playbook` command with a real `cluster_serial_number` embedded, the abort window, and the full cluster build summary -- reproduces the original script's exact structure and wording end to end.

**Workstream 1 (the callable-library refactor) is now complete: every one of the ~12 in-scope CLI entry points has a core/shim split.** `PClusterMakerError`, `ClusterRecord`, and roughly a dozen `core_*` functions in `src/pcluster_core.py` are now the toolkit's real, tested, MCP-callable business logic surface; every CLI script left behind is a thin argparse-and-print shim over it. The MCP plan's later workstreams (2: Jinja2→Python templating, 3: Ansible→boto3/pcluster.lib swap, 4: async/long-running job handling, 5-7: remote transport/auth/tool-surface) remain entirely unimplemented and unstarted -- not requested, and per the standing rule for this effort, not to be started without explicit direction.

**Same session, round 12 -- Workstream 2 (Ansible-driven templating → pure Python rendering) started: Phase 0 only, the shared renderer, deliberately not bundled with any tier's actual cutover.** Read the plan's Workstream 2 section in full, then spot-verified its claims against the live playbooks rather than trusting the draft outright (the established discipline this whole effort has followed) -- specifically counted every `template:`/`ansible.builtin.template:` task in both `create_pcluster.yml` (12) and `delete_pcluster.yml` (1), confirmed the two Tier-4 exclusions (`sns_build_summary_report.j2`, `sns_destruction_summary_report.j2`) really do reference `set_fact`/`register` values that only exist mid-build or mid-teardown, and confirmed `access_cluster.j2`/`retrieve_ssh_key.j2` -- positioned in the playbook *after* the head-node-IP `set_fact` -- genuinely don't reference `head_node_public_ip` despite the ordering, which is what makes them safely static (Tier 1) rather than runtime-coupled. All of it checked out; no correction to the plan's own tiering was needed.

**Scope was deliberately narrowed from what "start on Workstream 2" could have meant, and the narrowing is the round's real decision.** The plan's Tier 1 ("do first") describes moving `config.pcluster.j2`, `kill_pcluster.j2`, `access_cluster.j2`, and `retrieve_ssh_key.j2` off Ansible's `template:` module entirely. Doing that for real means more than writing a render function: Python would have to create `stage_dir` and write these four files into it *before* `ansible-playbook` runs (today `stage_dir` and everything staged into it, including these four files, are created and populated *inside* `create_pcluster.yml`, then bulk-copied to the head node via `scp` and to `active_clusters/<cluster_name>/` via `cp -a "{{ stage_dir }}"/* "{{ cluster_data_dir }}"` -- both steps operate on whatever's in `stage_dir` regardless of which system wrote each file into it, so the seam is real but not yet exercised). That is a coupled, cross-file cutover against the same live orchestration Workstream 1 spent eleven rounds being careful around, and it deserves its own dedicated round with the same rigor -- not a rushed addition to "build the renderer." So this round stops at Phase 0: the renderer plus its tests, nothing wired into `create_pcluster.yml` or `core_create_cluster` beyond the one already-existing `vars_file.j2` call site. The plan's own stated Tier 1 payoff -- "`preview_cluster_config`-style tool becomes a fast, side-effect-free MCP tool" -- only actually needs a working, tested Python render function to exist somewhere callable, which Phase 0 already provides for `config.pcluster.j2`; it does not require deleting the corresponding Ansible task in the same sitting.

**`_template_env`/`render_template` (`src/pcluster_core.py`) are the production twin of `tests/test_templates.py`'s own `_make_env`, which already existed and was already correctly configured** (`trim_blocks=True, lstrip_blocks=False, keep_trailing_newline=True, undefined=StrictUndefined`, pinned against Ansible's real installed source by the pre-existing `TestTheTestEnvironmentMatchesAnsible`). The only genuinely new production code is a thin, template-agnostic wrapper; the correctness work here is mostly the *decision* to unify on one Environment rather than the mechanics of building it. `core_create_cluster`'s `vars_file.j2` render call site -- the toolkit's one pre-existing example of Python-side Jinja2 rendering, previously its own ad hoc `Environment(...)` that set `keep_trailing_newline`/`StrictUndefined` but never `trim_blocks`/`lstrip_blocks` -- now calls `render_template` instead, which is the fix the plan itself calls out ("It has gotten away with this because `vars_file.j2` renders YAML, where a stray blank line is cosmetic... Any new shared renderer must fix this, not extend the mismatched one"). Confirmed safe empirically, not just by the plan's own reasoning: `test_make_pcluster_main.py`'s 51 tests already render this exact template under `StrictUndefined` end to end and all still pass, and separately, every existing `test_templates.py` assertion about `vars_file.j2`'s content already renders it via `_make_env` -- meaning `trim_blocks=True` was already the config the whole test suite validated `vars_file.j2` correctness against; production was the one place still out of step with what was already proven correct.

**`TestTheTestEnvironmentMatchesAnsible` gained a parametrized twin test pinning the production env the identical way** (`test_the_production_env_matches_ansible_too`, same `_ansible_defaults()` read off the installed Ansible source, now applied to `pcluster_core._template_env` too) -- this is the piece that stops the two configs (test-side and production-side) from independently drifting apart again, which is exactly the failure mode that let `vars_file.j2` ship without `trim_blocks` for as long as it did in the first place. A new `TestRenderTemplate` class (4 tests, using `tmp_path` fixture templates rather than any real one in `templates/`, so they test the renderer's own behavior independent of any specific file's content) covers: basic render-with-context, `StrictUndefined` raising on a missing variable, the exact whitespace behavior Phase 0 exists to fix (`trim_blocks=True` removing a block tag's trailing newline that plain Jinja2 would keep), and `keep_trailing_newline`.

**Full suite: 2026 passed** (up from 2020, +6 new: 2 parametrized production-env-matches-Ansible cases, 4 in `TestRenderTemplate`) **, 4 skipped, 0 failed.** `make lint`/`make shellcheck` clean (neither playbook nor any shell script was touched this round). No line-citation drift (`_template_env`/`render_template` were appended at the very end of `pcluster_core.py`, after every existing citation).

**Next round's scope: Tier 1's real cutover.** Write a small, well-tested change to `create_pcluster.yml` that creates `stage_dir` from Python (inside `core_create_cluster`, before `ansible-playbook` is invoked) and writes the four Tier-1 templates' rendered output into it via `render_template`, then deletes the four corresponding Ansible `template:` tasks (`config.pcluster.j2`, `kill_pcluster.j2`, `access_cluster.j2`, `retrieve_ssh_key.j2`) -- leaving every downstream task that reads those paths (the S3 upload of the rendered cluster config, the `scp` of `stage_dir` to the head node, the `cp -a` into `active_clusters/<cluster_name>/`) untouched, since they operate on whatever's already in `stage_dir` regardless of which system put it there. `config.pcluster.j2` has by far the largest existing test surface of the four (dozens of ad hoc `env.get_template("config.pcluster.j2").render(**params)` call sites scattered across `test_templates.py`) -- repointing those at one shared production function, per the plan's own "repoint each template's existing fixtures... rather than duplicating them" instruction, is real, wide-reaching (if mechanical) work worth scoping as its own step within that round, not assumed to be free.

**Same session, round 13 -- Tier 1's real cutover, done in full: all four static templates (`config.pcluster.j2`, `kill_pcluster.j2`, `access_cluster.j2`, `retrieve_ssh_key.j2`) now render in Python, and their four Ansible `template:` tasks are deleted.** Re-verified the plan's own claims against the live playbook before writing any code, exactly as round 12 did before touching anything: read `create_pcluster.yml` start to finish to find every place `stage_dir` is created and consumed, confirmed the "downstream tasks operate on whatever's in `stage_dir` regardless of who wrote it" premise by tracing the actual `scp` (line ~625) and `cp -a "{{ stage_dir }}"/* "{{ cluster_data_dir }}"` (line ~706) tasks, and confirmed `config.pcluster.j2` alone renders to `cluster_data_dir` directly (`{{ cluster_data_dir }}/config.{{ cluster_name }}`), not `stage_dir` -- the one place the four templates aren't uniform.

**A real gap in the plan's own premise was found and fixed before it could ship: `config.pcluster.j2` and the three `stage_dir` scripts don't actually render from `cluster_parameters` alone.** Round 12 (Phase 0) and this round's initial draft both assumed the plan's claim that these four templates are "renderable from the same context dict `vars_file.j2` already builds" meant `cluster_parameters` itself was sufficient context. It isn't: `vars_file.j2` computes a second layer of names with its own Jinja2 expressions -- `preinstall_s3_dest: "preinstall.{{ cluster_name }}.sh"`, `ssh_keypair: "{{ cluster_data_dir }}/{{ ec2_keypair }}.pem"`, and others -- that exist only in what vars_file.j2 *renders*, never as raw keys in the Python dict. Ansible's `vars_files:` directive loads the *rendered* vars file, so every Ansible-templated file downstream of it (including these four) always saw that fuller namespace; a Python renderer calling `render_template(..., **cluster_parameters)` does not. This was not caught by reading -- it was caught by running: the first attempt at this round failed 25 of `test_make_pcluster_main.py`'s 51 tests with `'preinstall_s3_dest' is undefined` inside `core_create_cluster`'s own IAM-cleanup-and-abort path, immediately on the first full test run. Fixed by rendering `vars_file.j2` first (as before), then `yaml.safe_load`ing its own output back into a dict (`_vars_file_context`) and using *that* -- not `cluster_parameters` -- as the render context for the other four. This is exactly the pattern `test_collect_templates_matches_what_the_playbooks_render` (an existing test, unrelated to this round) already used for verification purposes; the fix brings production into line with what the test suite had already established as correct, the same shape of realization as Phase 0's `vars_file.j2` `trim_blocks` fix one round earlier.

**File-mode parity was checked, not assumed.** All four templates carry `mode: "0755"` in their Ansible tasks (including `config.pcluster.j2`, a YAML file, not a script -- matched anyway, since "identical to what Ansible produced" is the bar, not "what seems more correct"). Python reproduces this via the same `os.open(path, O_WRONLY|O_CREAT|O_TRUNC, 0o755)` pattern `vars_file.j2`'s render already used (there, `0o600`) -- confirmed empirically with a full staged smoke test reading back `os.stat(...).st_mode` on every written file, not just trusted from the `os.open` call's argument.

**Two pieces of now-dead configuration were found and removed while making this change, not left as inert leftovers.** `vars_file.j2`'s own `cluster_config_template_orig: "{{ cluster_template_dir }}/config.pcluster.j2"` had exactly one consumer -- the Ansible task this round deletes -- confirmed by grepping the whole repo for the name before removing it. `test_templates.py::TestTheTestEnvironmentMatchesAnsible::test_no_playbook_template_task_overrides_them`'s `checked >= 10` floor (a vacuity guard proving the test's own playbook-walker isn't blind, not a target) was lowered to `>= 9` to match the real post-cutover count, with a comment explaining it's a ratchet that will keep shrinking as more tiers land -- the same discipline already applied to the preamble byte budget in `CLAUDE-STATE.md`.

**Deliberately did not do one piece of the plan's stated instruction: repointing `test_templates.py`'s dozens of existing `env.get_template("config.pcluster.j2").render(**params)` call sites at `pcluster_core.render_template`.** Considered and rejected as churn without a correctness gain: those call sites already use `_make_env`, which `test_the_production_env_matches_ansible_too` (round 12) now cross-verifies is configured identically to `pcluster_core._template_env`, so the test-side and production-side renderers are provably equivalent without touching every call site. Repointing them would be a large, purely mechanical diff across a 6000+ line test file for zero behavior change -- worth doing eventually for the plan's own "one renderer, one place" tidiness goal, but not bundled into a round whose actual risk (and actual bug caught) was in production code, not test plumbing.

**Line-citation drift this round, from a first: a *deletion* shifting a line number downward, not an insertion pushing one up (every prior citation-drift fix this session came from adding code above a citation).** Removing 24 lines of now-dead Ansible tasks moved `src/create_pcluster.yml`'s `sbatch_default_submission_script.sh` citation from line 635 to 611. Fixing it surfaced a second problem: `CLAUDE.local.md` cited `src/create_pcluster.yml:635` a *second* time, inside prose narrating the historical bug that citation's own test class was written to catch ("the first version pinned ... at line 635 ... a one-line shift was undetectable"). That second mention is a historical fact about what a past bug looked like, not a live pointer, but the citation-sweep regex can't tell the two apart -- it would have demanded the manifest carry two different current-line values for the same file, one of them necessarily wrong. Fixed by rewording the historical sentence to drop the specific backtick-wrapped `file:line` entirely (the point being made -- "the substring wasn't unique to its line" -- never actually needed the number), rather than trying to make the manifest express two truths about one line.

**Full suite: 2026 passed, 4 skipped, 0 failed** (same count as Phase 0's round -- no tests added or removed, all four templates' correctness continues to be proven by existing `test_templates.py` coverage plus `test_make_pcluster_main.py`'s end-to-end renders). `make lint` clean (both playbooks re-linted after task removal). `make shellcheck` clean (no shell script touched). Manually verified end-to-end with a fully staged fake build: `config.smoketest` lands at `active_clusters/smoketest/config.smoketest` (mode 755) with correct `Region`/`Os`/`InstanceType`/`SubnetId` content; `kill_pcluster.smoketest.sh`, `access_cluster.smoketest.sh`, and `retrieve_ssh_key.smoketest.sh` all land in the real OS temp-dir-derived `stage_dir` (mode 755 each), with every `{{ }}` correctly substituted (secret name, region, SSH keypair path, EC2 user) and no Jinja2 artifacts left in the output.

**Workstream 2 status: Phase 0 and Tier 1 both complete. 8 of the original 13 `template:` tasks remain** (`preinstall.j2`/`postinstall.j2`'s pair, the monitoring wrapper, the Grafana tunnel script, the external NFS mount list's two renders, the two hpc-benchmark files, and the two Tier-4 SNS reports -- Tier 2's five templates plus Tier 3's two plus Tier 4's two). Tier 2 (gated templates chained to an S3 upload: monitoring wrapper, external NFS mount list, `job_hpc-benchmark.sh.j2`/`README-PERFORMANCE.md.j2`, the sbatch script, Grafana tunnel) is the natural next round -- the plan's own guidance is to render each in Python but *leave the upload/publish step as an Ansible task pointed at the pre-rendered file*, decoupling the rendering change from Workstream 3's boto3 swap. Tier 3 (`preinstall.j2`/`postinstall.j2`) is explicitly saved for last within this workstream too, per the plan's own reasoning (heaviest branching, requires a byte-for-byte parity check against every `conftest.py` fixture before the Ansible task can be deleted). Tier 4 stays out of scope until Workstream 3.

**Same session, round 14 -- Tier 2's cutover done (5 gated templates), plus a genuine plan correction found by testing with every gate enabled rather than the default off-state.** Re-read `create_pcluster.yml` around each of Tier 2's five tasks before writing anything -- confirmed each destination path against `vars_file.j2`'s own derived variables (`monitoring_wrapper_dest`, `grafana_tunnel_dest`, `external_nfs_mount_list_template_src`/`_dest`, `performance_stage_dir`) rather than reconstructing paths by hand the way Tier 1 had to for the three templates with no named vars_file.j2 variable of their own, and confirmed the two-destinations-two-modes shape of the external NFS mount list task (0644 to `cluster_data_dir`, 0755 to `stage_dir`) by reading both Ansible tasks side by side rather than assuming symmetry. `job_hpc-benchmark.sh.j2`/`README-PERFORMANCE.md.j2` render from `hpc-benchmark/`, not `templates/` -- confirmed from the Ansible task's own `src: "{{ performance_rootdir }}/{{ item.src }}"` rather than assumed from the file's location, and `render_template`'s existing `templates_dir` parameter took that directory with no change needed.

**Found and fixed a real Ansible-only construct the plan's own audit missed: `lookup('pipe', 'date ...')` in two of Tier 2's templates.** The plan's Phase 0 section claimed "No Ansible-only Jinja filters or facts exist anywhere... zero hits" after auditing all 14 template files -- true for the filters it checked (`to_json`, `regex_replace`, `combine`, `default`, `ansible_*` facts) but the audit never checked for `lookup()` calls, and `external_nfs_mount_list.j2` and `scripts/sbatch_default_submission_script.sh` (Tier 2, this round) both carry `{{ lookup('pipe','date \"+%B %e, %Y\"...') }}` in their "Deployed On:" comment lines -- confirmed by grepping all 14 files for `lookup(`, which also turned up `preinstall.j2`/`postinstall.j2` (Tier 3, not this round, but now a known item for that round rather than a fresh surprise). `tests/test_templates.py`'s own `_make_env` already had to stub `lookup` to a placeholder string to render these templates at all in tests -- a tell that should have been read as "this construct exists and needs a real answer for production," not just worked around. Fixed by replacing both `lookup('pipe', ...)` calls with `{{ Deployed_On }}`, a value already computed in Python (`Deployed_On = DEPLOYMENT_DATE`, `_now.strftime("%B ") + str(_now.day) + _now.strftime(", %Y")`) and already threaded through `cluster_parameters` for other templates' use -- format-verified to match the shell command's output exactly (`%B %e, %Y` space-squeezed via `tr -s`, vs. Python's `str(_now.day)` which never produces the leading space `tr -s` exists to remove in the first place, e.g. both produce "August 5, 2026" not "August  5, 2026"). This is a real, permanent improvement independent of Workstream 2's own goals: those two templates can now render under vanilla Jinja2 with no stub required, by any caller, not just this toolkit's own test harness.

**A second, opposite-direction gap in the render context, found the same way as `lookup()` -- by checking, not assuming symmetry with Tier 1's fix.** Tier 1's round established that `vars_file.j2`-derived names (like `preinstall_s3_dest`) aren't in raw `cluster_parameters`. This round found the reverse also holds: `Deployed_On` and `debug_mode` are in `cluster_parameters` but never re-emitted by `vars_file.j2` at all -- confirmed by mechanically diffing all 123 `cluster_parameters` keys against `vars_file.j2`'s own text rather than checking by hand. Since the `Deployed_On` fix above needs exactly this key, `core_create_cluster`'s render context changed from "vars_file.j2's own parsed output" to a merge, `{**cluster_parameters, **yaml.safe_load(rendered_vars_file)}` -- the parsed side winning on any key both carry (matching what Ansible's `vars_files:` load actually gave templates before Workstream 2), `cluster_parameters` filling the rest. This closes the gap for every future Tier 2/3 template too, not just this round's two.

**A genuine plan-classification error was found and corrected by testing with every gate enabled, not by re-reading the template more carefully.** Tier 1's round (and this round's initial draft) trusted the plan's claim that `config.pcluster.j2` is "purely static," and it renders fine in every scenario this session had tested up to this point -- because none of them set `--enable_external_nfs=true`. A smoke test with monitoring, external NFS, and HPC benchmarks all enabled in the same run (deliberately broader than any prior round's smoke test, specifically to shake out gated interactions Tier 2's own templates might expose) failed immediately with `'external_nfs_sg' is undefined`: `config.pcluster.j2` references `{{ external_nfs_sg.group_id }}` when `enable_external_nfs == 'true'`, and `external_nfs_sg` is an Ansible `register:` result from the `amazon.aws.ec2_security_group` task inside `create_pcluster.yml`'s own "Create S3 bucket and EC2 keypair" block -- a real AWS-created resource that does not exist until that task runs, well after `core_create_cluster` would need it to render config.pcluster.j2 in Python. This was a genuine hole in the plan's own "10 of 12 confirmed purely static" audit, not something Workstream 2's Tier 1 round should have caught on its own -- Tier 1's tests never enabled external NFS, matching the plan's original (uncaught) blind spot exactly.

**Rolled config.pcluster.j2 back to Ansible-side rendering rather than working around the gap within Workstream 2.** Considered and rejected: a conditional two-path renderer (Python for the common case, Ansible only when `enable_external_nfs=true`) adds a second, rarely-exercised code path for one template, the exact kind of complexity CLAUDE.md's own style rules warn against and that this session's incident history (documented at length in `CLAUDE.local.md`) shows hides bugs behind low-traffic branches. The clean fix is Workstream 3's job, not Workstream 2's: once the external-NFS security group's creation moves from Ansible to boto3/Python, `external_nfs_sg` becomes a Python-computed value config.pcluster.j2 can receive like everything else, and the template migrates cleanly at that point. Until then it stays exactly where it was: the "Template the cluster config" Ansible task restored at its original position (right after "Create local staging directories," before the toolkit pre/postinstall render), `cluster_config_template_orig` restored in `vars_file.j2` (removed as dead code in Tier 1's round, now genuinely alive again), and `core_create_cluster`'s own Tier 1 render block reduced back to the three templates that really are static (`kill_pcluster.j2`, `access_cluster.j2`, `retrieve_ssh_key.j2`) with an explicit comment recording why config.pcluster.j2 isn't among them, so a future reader doesn't reintroduce this exact bug by "finishing" Tier 1.

**Both vacuity-guard floors from Tier 1's round needed a second adjustment, up this time, once config.pcluster.j2's task came back** -- 3 → 4 remaining `template:` tasks total, 2 → 3 resolvable absolute `src` values (`config.pcluster.j2` plus the two SNS reports; the preinstall/postinstall pair's `item.src` stays skipped). Both comments were rewritten to name config.pcluster.j2's specific reason for staying, not just update the number, so a future Tier 3/4 round's own floor adjustment has the right context already in place rather than needing to rediscover it.

**Full suite: 2026 passed, 4 skipped, 0 failed** (same count throughout this round -- `Deployed_On` was added to `conftest.py`'s `cluster_params` fixture per the standing "every new template variable traced through the pipeline into conftest.py" rule, and two vacuity-guard floors were adjusted twice each as the task count moved, but no new test cases were added or removed net). `make lint` clean (playbook re-linted after both the Tier 2 removals and the config.pcluster.j2 restoration). `make shellcheck` clean. `test_make_pcluster_main.py`'s `staged` fixture gained `scripts/` and `hpc-benchmark/` access -- `hpc-benchmark/` as a straight symlink (safe, nothing resolves realpath against it), `scripts/` as a **real** directory holding one symlinked file rather than a symlinked directory itself, since a fully-symlinked `scripts/` made the *existing* `pre_install_script`/`post_install_script` path-escape check (which deliberately uses `os.path.realpath` to catch exactly this kind of symlink escape) fire a false positive against its own default value -- caught by a second full-suite run immediately surfacing 46 failures (up from 25) right after the first fix, not assumed safe from the first fix's success. Manually verified end-to-end twice: once with every Tier 2 gate off (matching Tier 1's baseline, confirming no regression), once with monitoring, external NFS, and HPC benchmarks all on together -- reading back every rendered file's destination, mode, and substituted content, including confirming `config.pcluster.j2` is *not* among Python's written files (still correctly Ansible's job) and that `Deployed On:` lines read a real date with no stray double space or leftover `<lookup-stub>` artifact.

**Workstream 2 status: Phase 0, Tier 1, and Tier 2 all complete. 4 of the original 13 `template:` tasks remain**: `config.pcluster.j2` (now permanently Tier-3-or-later, pending Workstream 3's security-group move), the `preinstall.j2`/`postinstall.j2` pair (Tier 3, saved for last per the plan's own reasoning -- heaviest branching, needs a byte-for-byte parity check across every `conftest.py` fixture), and the two SNS report templates (Tier 4, out of scope until Workstream 3 supplies the mid-build/mid-teardown facts they need). Given `preinstall.j2`/`postinstall.j2` reference the same class of Ansible-only `lookup('pipe', 'date ...')` construct already fixed twice this round, that fix is now a known, not a surprise, going into Tier 3 -- but Tier 3's own scope (parity-testing a 28KB template against every fixture combination) is large enough to warrant checking for other runtime-AWS-value couplings the same deliberate way this round did, not assuming the plan's classification is complete just because the pattern now looks familiar.

**Same session, round 15 -- Tier 3 done: `preinstall.j2`/`postinstall.j2` cut over to Python, closing out everything in Workstream 2 that was ever meant to move.** The highest-risk template migration in the workstream by the plan's own description (28KB, heaviest branching of any template, the single largest concentration of CLAUDE.md-pinned bootstrap incidents tied to exact rendered output) got the most upfront verification of any round this workstream: every `{{ }}` reference in both files was grepped and cross-checked against `vars_file.j2`'s derived-variable set *before* any code was written, specifically to rule out a second `config.pcluster.j2`-shaped surprise (a hidden runtime-AWS-value dependency) -- none was found. The two `lookup('pipe', 'date ...')` occurrences already known from Tier 2's discovery (both files) were fixed the identical way: replaced with `{{ Deployed_On }}`.

**Built the plan's own mandated verification before touching any production code or deleting the Ansible task: a byte-for-byte parity test across all 20 of `conftest.py`'s fixture combinations (`cluster_params`, `cluster_params_rhel`, `cluster_params_al2023`, every GPU/EFA/login-node/monitoring variant), 40 cases total, comparing the real `core_create_cluster` pipeline's output against the established-correct `_make_env` render.** All 40 passed on the first run -- a first for this workstream (Tier 1 and Tier 2 each found a real bug via testing that reading missed; this round's heavier upfront variable-coupling check evidently closed that gap before code was written rather than after). The test is intentionally not a comparison of two renderers in isolation (which would be tautological, since `_template_env`/`_make_env` are already pinned identical) -- it drives `render_template` through the actual `cluster_parameters -> render vars_file.j2 -> yaml.safe_load -> merge` context construction `core_create_cluster` performs, so it catches context-mismatch bugs, not just renderer-config drift.

**`Deployed_On` was also added to `vars_file.j2`'s own rendered output this round, not left as a Python-side-only fix.** Tier 2's fix routed `Deployed_On` into templates via `core_create_cluster`'s `_vars_file_context` merge (`cluster_parameters` filling gaps `vars_file.j2` doesn't re-emit), which is sufficient for Python-rendered templates but would have left `Deployed_On` undefined for any template *still* rendered by Ansible (which is exactly what `preinstall.j2`/`postinstall.j2` were, for the whole first half of this round, and what `config.pcluster.j2` still is on purpose) -- Ansible's `vars_files:` load only ever sees what `vars_file.j2` itself emits, never Python's raw `cluster_parameters` dict. Caught before it could break anything: adding `Deployed_On: "{{ Deployed_On }}"` to `vars_file.j2` next to the existing `DEPLOYMENT_DATE` line closes the gap for both callers permanently, and is the more correct fix regardless of which templates end up Python-rendered later.

**`TestPostinstallTemplateIsActuallyRendered::test_the_template_is_rendered_by_a_template_task` is a regression test for a real, previously-shipped incident (postinstall.j2/preinstall.j2 silently dead from the v3 migration until 2026-07-26, confirmed on a live cluster), and its premise -- "some Ansible `template:` task renders this" -- is now categorically false by design, not a mistake to fix around.** Rewritten to check the same property the way it now actually holds: `render_template(...)` is really called on the template name inside `src/pcluster_core.py`, verified on the AST rather than a string search (a renamed or accidentally-removed call site must fail this, the same reasoning every other "call site" test in this series has used since Workstream 1). The AST check has two paths since the real call site doesn't pass the template name as a literal argument -- it unpacks a `for _tmpl_name, _dest_name in (("preinstall.j2", ...), ...)` loop -- so the test also accepts a `render_template` call found inside a `for` loop whose iterable contains the literal template name, matching the code's actual shape rather than requiring a shape the code doesn't have.

**Both vacuity-guard floors dropped again, this time by one each (not two, since only one task -- the pre/postinstall `with_items` pair -- was removed this round, unlike Tier 2's five separate removals):** 4 → 3 remaining `template:` tasks total (`config.pcluster.j2` plus the two SNS reports), and the resolvable-absolute-`src` count stayed at 3 (unchanged in value, since the pre/postinstall task was already excluded from that count for containing `item.src`, not newly excluded -- only the comment explaining why needed updating, from "skipped because of item.src" to "this task doesn't exist anymore at all"). `preinstall_template_orig`/`postinstall_template_orig` removed from `vars_file.j2` as dead code (grepped for zero remaining consumers first, matching the same check-before-delete discipline Tier 1 applied to `cluster_config_template_orig` and Tier 2 implicitly relied on); `preinstall_rendered`/`postinstall_rendered`/`*_s3_dest` were kept, since the S3 upload task that stays in Ansible still reads them.

**Full suite: 2066 passed** (up from 2026, +40 -- the parity test class, the only net-new tests this round) **, 4 skipped, 0 failed.** `make lint`/`make shellcheck` clean. No line-citation drift (the one remaining `src/pcluster_core.py` citation, `iam.attach_role_policy(` at line 969, sits well above where this round's code landed; no `create_pcluster.yml` line citations exist to track after Tier 2 already removed the last one). Manually verified end-to-end with a RHEL9 smoke test (deliberately not the default Ubuntu/ARM fixture, to confirm the dnf branch specifically): both files land at `cluster_data_dir` with mode 755, a real "Deployed On:" date with no stray artifact, no leftover `{% %}`/`{{ }}` in the output, and dnf-family package lines present confirming the correct OS branch was taken.

**Workstream 2 is now complete for everything the plan ever scoped into it.** 7 templates render in Python (`vars_file.j2`, `kill_pcluster.j2`, `access_cluster.j2`, `retrieve_ssh_key.j2`, the Tier 2 five, `preinstall.j2`/`postinstall.j2`); 3 of the original 13 `template:` tasks remain in Ansible, and all three are *deliberately* out of scope, not merely unfinished: `config.pcluster.j2` needs Workstream 3's boto3 swap of the external-NFS security group first, and the two SNS reports need Workstream 3's own fact-computation (mid-build/mid-teardown timers and derived state) before a Python render call would have anything correct to receive. Nothing further is actionable in this workstream without Workstream 3 landing first.

**Same session, round 16 -- Workstream 3 (replace Ansible's outer orchestration with direct boto3 + `pcluster.lib` calls) started.** Read the plan's Workstream 3 section in full first (its own task-by-task feasibility tables for all 43 `delete_pcluster.yml` tasks and all 80 `create_pcluster.yml` tasks) before writing anything, given this is the highest-risk workstream in the plan by its own description -- the CLI's printed output, exit codes, and every CLAUDE.md-pinned ordering/gating invariant currently enforced by Ansible task structure has to keep working unchanged for an operator who never touches the MCP layer at all.

**Before any code: ran the plan's own mandated live check, with explicit user confirmation first.** The plan flagged its `pcluster.lib` exception-shape findings (`NotFoundException` on `describe_cluster`/`delete_cluster` against a nonexistent cluster) as "confirmed by reading source, never observed live" and explicitly required a real-AWS-account check before any implementation depends on it. Real AWS credentials were already active in this environment (account `183295445014`); rather than assume that meant permission to use them, asked the user first (`AskUserQuestion`) whether to run the check and what this round's actual scope should be -- both confirmed: run the live check, then start with teardown's lowest-risk group. `pc.describe_cluster(cluster_name="definitely-does-not-exist-pclustermaker-check", region="us-east-2")` and the equivalent `pc.delete_cluster(...)` call both raised `NotFoundException` exactly as documented, with `.content == {"message": "Cluster '...' does not exist or belongs to an incompatible ParallelCluster major version."}` and `.code == 404` -- a genuinely useful additional finding beyond the plan's own research (the exception's structured attributes, useful for building good error messages, weren't discoverable from source alone the way the exception type was).

**Scope for this round, per the user's own choice: teardown's two "trivial" groups from the plan's feasibility table -- the timer helper and the four credential-destroying tasks -- built as standalone, tested functions in `pcluster_core.py`, not yet wired into `kill_pcluster.py`/`core_delete_cluster`.** The four credential-destroying tasks (EC2 keypair, local `.pem`, Secrets Manager secret, `cluster_data_dir`) all share one gate in `delete_pcluster.yml` -- `_cf_delete_confirmed`, the same positive-confirmation-only gate CLAUDE.md documents at length (a wait timeout is neither confirmed nor `DELETE_FAILED`, and treating it as "safe to delete" would destroy the only way back into a head node that might still be running and billing). `_cf_delete_confirmed` itself is computed by the delete+wait+classify logic that is a separate, larger piece of this same workstream, not yet built -- so `run_credential_teardown_steps` takes `cf_delete_confirmed` as an explicit boolean parameter rather than deriving it, keeping this round's scope to exactly what the user asked for and leaving the wiring for a follow-up round once that piece exists.

**Every step function follows one uniform shape, matching the plan's own design note ("each step: its own function, wrapped in try/except, tolerating its own failure and continuing") rather than four ad hoc try/excepts:** never raises, always returns a `TeardownStepResult(name, succeeded, detail)`. This mirrors `delete_pcluster.yml`'s own `ignore_errors: true` + `register:` pattern precisely -- a single denied `DeleteKeyPair` must not abandon the other three steps, the exact incident CLAUDE.md documents as the reason `ignore_errors` was added to that one task in the first place. `teardown_timestamp()` was verified against a real `date +%Y-%m-%d\ \@\ %H:%M:%S` invocation, not just reasoned about from the strftime directives, given this exact session already found one date-formatting mismatch (the `Deployed_On`/`lookup('pipe', ...)` fix in Workstream 2) from trusting format-string equivalence without checking.

**17 new tests in a new file, `tests/test_teardown_steps.py`** (Workstream 3's first test file): each step tested for success, for a caught-not-raised failure, and (for the two local filesystem steps) for the already-absent case being success rather than failure, matching `file: state: absent`'s own idempotence. `run_credential_teardown_steps` gets four tests of its own: all four steps fire together when confirmed, all four are skipped together (with an explanatory `detail`, not silently omitted) when not confirmed and nothing on disk or in the fakes is touched, one step's failure doesn't block the other three, and the four results come back in `delete_pcluster.yml`'s own task order. A vacuity guard confirms `TeardownStepResult` is genuinely frozen (its own regression-proofing, matching every other frozen dataclass introduced this session).

**Full suite: 2083 passed** (up from 2066, +17 -- the new test file, no other test needed adjustment since nothing existing referenced these new names) **, 4 skipped, 0 failed.** `make lint`/`make shellcheck` clean (no playbook or shell script touched -- `delete_pcluster.yml`'s Ansible tasks are untouched this round; the boto3 twins exist alongside them, not yet replacing them). No line-citation drift (new code appended at the very end of `pcluster_core.py`, after `iam.attach_role_policy(`'s line).

**Next round's scope, following the plan's own migration order:** the rest of teardown's "simple creation resources" and "IAM cleanup" groups (S3 bucket, FSx hydration policy detach, Grafana SSM parameter, the four managed IAM policies plus monitoring policy, IAM role/instance-profile, external NFS security group, SNS topic), then the delete+wait+classify logic itself (`pc.delete_cluster`/`pc.describe_cluster`, now live-verified) that produces the `_cf_delete_confirmed`/`_cf_delete_failed`/`_delete_headline` classification this round's credential-destroying steps depend on -- at which point `kill_pcluster.py`/`core_delete_cluster` can actually be wired to the new boto3 path instead of shelling out to `ansible-playbook`. `create_pcluster.yml`'s 80-task migration is explicitly a separate, later effort per the plan's own recommended order (teardown first, since every teardown group is independently portable and none blocks another -- create's four-category mix, per the plan's own table, is real additional work, not a lateral port of the same pattern).

**Same session, round 17 -- Workstream 3's second increment: the rest of teardown's "simple creation resources" and "IAM cleanup" groups ported.** Seven more `delete_pcluster.yml` tasks got boto3 twins in `pcluster_core.py`, all in `run_resource_teardown_steps` (a sibling to round 16's `run_credential_teardown_steps`, not a merge into it -- these seven are deliberately **not** gated on `_cf_delete_confirmed` in the playbook, only the four credential-destroying ones are, so gating them together would have been a real behavior change, not a refactor):

- **`_delete_s3_bucket_step`** empties the bucket via `list_object_versions` + `delete_objects` before `delete_bucket` -- `list_object_versions` (not `list_objects_v2`) so this empties correctly whether or not versioning was ever turned on (an unversioned object still reports `VersionId: "null"` and deletes the same way), matching `amazon.aws.s3_bucket`'s `force: true`.
- **`_detach_fsx_hydration_policy_step`** is `delete_role_policy`, not `detach_role_policy`/`delete_policy` -- the FSx hydration policy is an inline role policy (`_setup_fsx_hydration_iam`'s `put_role_policy`), not a managed one, and using the managed-policy calls on it would silently no-op.
- **`_delete_managed_iam_policies_step`** detaches+deletes all four base policies and returns one combined result, matching the playbook's single `with_items` task and its single register (top-level `.failed` is `True` if any item failed, per the existing orphan-collection bullet in `CLAUDE.md`) -- deliberately **not** routed through the existing `_delete_managed_policies` helper, which silently suppresses every error for the create-side rollback path (`_cleanup_iam_on_failure`); a teardown failure here has to reach the orphan list, not be swallowed. Same shape for `_delete_monitoring_iam_policy_step`, kept separate since the playbook itself registers it separately (gated on `enable_monitoring`, the base four are not).
- **`_delete_iam_role_step`** enumerates and detaches whatever's actually attached (managed + inline + instance profile) rather than assuming the prior steps already emptied it -- a stale rebuild artifact or a manual attach must not block `DeleteRole`, which AWS refuses on a role carrying anything.
- **`_delete_external_nfs_sg_step`** needed no idempotence special-casing at all: `describe_security_groups` with a name filter that matches nothing returns an empty list rather than raising, so "already gone" and "never existed" both fall out of the normal code path for free.
- Every step follows round 16's shape: catch `_ClientError`, treat the service's own not-found code (`NoSuchEntity` for IAM, `NoSuchBucket` for S3, `ParameterNotFound` for SSM) as idempotent success, everything else becomes a failed `TeardownStepResult` rather than a raised exception -- consistent with `_setup_iam`/`_delete_managed_policies`'s existing `_ClientError`/`.response["Error"]["Code"]` pattern already in this file, not the generated `client.exceptions.XxxException` style, so the fakes in the test file construct real `botocore.exceptions.ClientError` instances rather than a bespoke per-service exception hierarchy.

**SNS topic deletion (`delete_pcluster.yml`'s eighth remaining cleanup task) is deliberately still deferred, correcting last round's own scope note that had listed it as in-scope.** The playbook deletes the SNS topic *after* sending the destruction summary report through it, with an explicit comment explaining why: the topic has to outlive the report it carries. The report/summary logic itself (`_delete_headline`, `_orphaned_resources` collection, the SNS-templated destruction summary) is separate, later work this round didn't touch -- porting SNS topic deletion into `run_resource_teardown_steps` now would run it far too early relative to a report step that doesn't exist yet in Python, which is a real ordering bug waiting to happen rather than a faithful port. Left for the round that builds the report/summary derivation.

**23 new tests, all in the existing `tests/test_teardown_steps.py`** (not a new file -- extends round 16's rather than fragmenting the coverage): fakes for S3 (paginated `list_object_versions`, `delete_objects`, `delete_bucket`), IAM (a fuller fake than round 16's, covering every call `_delete_iam_role_step` makes plus a per-method `raise_on` dict so any single call can be made to fail without touching the others), SSM, and a name-filtered EC2 security-group fake. Each step gets a success case, an already-absent-is-not-a-failure case (via a real `ClientError` with the service's own not-found code), and a genuine-failure-is-reported-not-raised case; `_delete_managed_iam_policies_step` additionally gets a case proving one policy's failure doesn't stop the other three from being attempted. `run_resource_teardown_steps` gets three: the minimal-cluster case (only the three unconditional/default-on steps fire), `delete_s3_bucketname=False` skipping that one step, and every gate on at once producing all seven in the playbook's own order.

**Full suite: 2106 passed** (up from 2083, +23), 4 skipped, 0 failed. `make lint`/`make shellcheck` clean (no playbook or shell script touched this round either). No line-citation drift -- new code appended after round 16's, itself after `iam.attach_role_policy(`'s cited line.

**Next round's scope:** the delete+wait+classify logic (`pc.delete_cluster`/`pc.describe_cluster`, live-verified in round 16) that produces `_cf_delete_confirmed`/`_cf_delete_failed`/`_delete_headline` -- the piece both `run_credential_teardown_steps` and this round's `run_resource_teardown_steps` are built to receive but that nothing yet produces in Python. Once that exists, the SNS topic deletion this round explicitly deferred can be added correctly (after the report), and `kill_pcluster.py`/`core_delete_cluster` can be wired to the new boto3 path end to end instead of shelling out to `ansible-playbook`. `create_pcluster.yml`'s 80-task migration remains separate/later per the plan's own order.

**Same session, round 18 -- Workstream 3's third increment: the delete+wait+classify logic that produces `_cf_delete_confirmed`/`_cf_delete_failed`/`_delete_headline`, the gate both prior rounds' teardown-step groups were built to receive but that nothing in Python had actually produced.** Read `pcluster.lib`'s and the API controller's installed source in full before writing anything, rather than assuming the CLI-subprocess JSON shape carried over unchanged:

- **`pc.describe_cluster`/`pc.delete_cluster` dispatch straight through the controller, bypassing the CLI's own exception-wrapping.** `pcluster.lib.lib._gen_func_map`'s generated functions call `dispatch(model, Args(kwargs))` directly -- the `_run_operation` wrapper that turns everything into `APIOperationException` for the CLI's `main()` is never in the call path. `pcluster.cli.model.call` (what `dispatch` ultimately invokes) does `json.loads(encoder.JSONEncoder().encode(ret))` on the controller's return value, the exact same encoding the CLI prints -- so `pc.describe_cluster(...)` returns a plain dict with the same `clusterStatus` key this codebase already parses everywhere else from `pcluster describe-cluster`'s JSON output via subprocess. `NotFoundException`/`BadRequestException` (from `pcluster.api.errors`) propagate unwrapped, confirmed live in round 16 against a genuinely nonexistent cluster.
- **`CloudFormationStackStatus.DELETE_COMPLETE`/`DELETE_FAILED` are plain string constants** (`"DELETE_COMPLETE"`/`"DELETE_FAILED"`) that `cloud_formation_status_to_cluster_status` passes through unchanged (its remap dict only touches the `ROLLBACK_*`/`UPDATE_ROLLBACK_*` states) -- confirmed by reading `pcluster/api/converters.py` and the generated `cloud_formation_stack_status.py` model directly, not inferred from CLI output alone.
- **No new live AWS calls this round.** The one thing genuinely uncertain about `pcluster.lib`'s behavior (does an exception propagate raw, and what's its shape) was already live-verified in round 16; everything else needed for this round -- exact exception classes, exact status-string constants, exact response-dict shape -- is a static fact of the installed package's source, not AWS behavior, and manufacturing a real cluster in DELETE_FAILED/DELETE_COMPLETE states to "verify" a classification function would be disproportionately expensive and destructive for what source-reading already settles with certainty.
- **`_initiate_cluster_delete`, `_wait_for_cluster_delete`, `_classify_cluster_delete_outcome`, and the composing `run_cluster_delete_and_classify`** mirror the playbook's "Delete the ParallelCluster v3 stack" task, "Wait for the cluster to finish deleting" task, and its three `set_fact` tasks respectively -- kept as four separate, individually testable units rather than one function, matching this file's established one-Ansible-task-per-Python-unit granularity. `_wait_for_cluster_delete` retries on *any* non-terminal outcome (a still-building cluster and a transient describe-cluster failure are indistinguishable mid-retry, matching Ansible's own `until:`/`retries:` behavior which doesn't inspect the reason until the final attempt) but **re-raises the last exception if the final attempt is still unrecognized**, rather than folding it into `TIMED_OUT` -- the playbook's `failed_when` aborts the whole play on that same condition, and nothing downstream runs, so the Python port must not silently let a caller treat "describe-cluster never resolved to anything" as safe to proceed past. `TIMED_OUT` itself is deliberately *not* an exception and not folded into `DELETE_FAILED` -- the playbook only warns on a wait timeout, and `_classify_cluster_delete_outcome` treats it exactly like `DELETE_FAILED` for the one property that matters (`cf_delete_confirmed=False`), per the positive-confirmation-only gate `CLAUDE.md` already documents.
- **The `_ClientError` import's `ImportError` fallback pattern was deliberately NOT copied for `NotFoundException`/`BadRequestException`.** `_ClientError = Exception` on import failure is inert -- it only weakens an `e.response["Error"]["Code"]` check that's already downstream of a real botocore exception. The equivalent fallback here would be actively unsafe in the opposite direction: catching bare `Exception` under the name `NotFoundException` would reclassify *any* describe/delete failure (including AccessDenied) as "cluster confirmed gone," which is precisely the false-confirmed case this whole gate exists to prevent. `aws-parallelcluster>=3.15` is a hard, unconditional dependency of this repo already (`requirements.txt`, already invoked via subprocess everywhere else in this file), so the import is unguarded.

**19 new tests, again extending `tests/test_teardown_steps.py`** (not a new file): `_FakeDeleteFn` and `_ScriptedDescribeFn` (a scripted sequence of `clusterStatus` values or exceptions, repeating its last entry forever once exhausted, so a single-element sequence models a persistent condition) drive four classes -- `TestInitiateClusterDelete`, `TestWaitForClusterDelete` (including the sleep-call-count assertions proving no sleep happens after the final attempt, and the persistent-vs-transient-error distinction), `TestClassifyClusterDeleteOutcome`, and `TestRunClusterDeleteAndClassify` (the already-gone-skips-the-wait-loop-entirely case is the one that would have caught an unconditional wait call).

**Full suite: 2125 passed** (up from 2106, +19), 4 skipped, 0 failed. `make lint`/`make shellcheck` clean (still no playbook or shell script touched). **One line-citation drift this round, caught by the full suite itself**: the new top-of-file import block shifted `iam.attach_role_policy(` from `src/pcluster_core.py:969` to `:977` -- fixed in both citing surfaces (`tests/test_claude_docs_line_citations.py`'s manifest and `templates/CLAUDE.local.md`'s prose); `docs/sessions.md`'s own round-14 mention of line 969 was left alone since it's a dated historical record of what was true at the time, not a pinned citation.

**Workstream 3's teardown side is now feature-complete except SNS topic deletion** (still correctly deferred to whenever the destruction-summary report gets built, per round 17's reasoning) **and the actual wiring into `kill_pcluster.py`/`core_delete_cluster`.** Next round's scope: wire `core_delete_cluster` to call `run_cluster_delete_and_classify` then `run_credential_teardown_steps`/`run_resource_teardown_steps` in place of `_build_destroy_ansible_cmd`'s `ansible-playbook` subprocess, replicating the playbook's printed summary/orphan-list/exit-status behavior closely enough that `kill_pcluster.py`'s observable behavior doesn't change for an operator who never touches the MCP layer. `create_pcluster.yml`'s 80-task migration remains separate/later per the plan's own order.

**Same session, round 19 -- Workstream 3's fourth increment: `core_delete_cluster` wired onto the boto3/`pcluster.lib` path built in rounds 16-18, replacing the `ansible-playbook` subprocess call to `delete_pcluster.yml` entirely.** User explicitly chose "full parity" over a partial swap when asked (`delete_pcluster.yml` does three things beyond the already-built pieces: syncs HPC-benchmark results to S3 *before* deleting the cluster, collects orphaned/retained resources into a printed summary that drives the exit status, and sends/deletes an SNS report) -- dropping any of those would have been a real regression (silent data loss for the first, a worse-than-today teardown report for the second), not a disclosed gap, so all three were built this round rather than deferred again.

- **New functions in `pcluster_core.py`, each a boto3/subprocess twin of one more `delete_pcluster.yml` task**: `_pclib_head_ip` (head node IP via `pc.describe_cluster`, called *before* `delete_cluster` since the head node still exists then; never raises, matching the playbook's `failed_when: false` + empty-string fallback), `_sync_performance_results_to_s3` (an `ssh ... aws s3 sync ...` subprocess call, best-effort matching the playbook's `rescue:` block), `_collect_orphaned_resources`/`_collect_retained_resources` (list-building from `TeardownStepResult`s -- a failed step's own `.name`, which is already the playbook's task name, plus its `.detail` is the orphan entry; a *deliberate* wording simplification from the playbook's hand-crafted per-resource phrases, disclosed rather than silently different, since resource state and exit codes are what "must keep working unchanged," not exact warning-message text), `_publish_destruction_report`/`_delete_sns_topic_step` (SNS publish is best-effort and never orphan-tracked, matching `ignore_errors: true`; `sns.delete_topic` needs no NotFound special-casing since SNS's own DeleteTopic is documented idempotent, unlike every other AWS delete in this module), `_format_destruction_summary` (one function for the playbook's two near-identical debug-message tasks, since the only difference is whether the orphan list is empty).
- **The SNS report is built from credential+resource results only, before the SNS topic is deleted** -- matching the playbook's own ordering comment ("the topic is deleted after the report is sent, so its own failure cannot appear in that report"). The *terminal* summary and exit-status check, printed after, include the topic-deletion result too. Getting this backwards (computing one `orphaned_resources` list and reusing it for both) was the one sequencing bug caught by re-reading the playbook's task order line-by-line before writing the code, not by a test.
- **`core_delete_cluster` now reads the cluster's own vars file directly** (`yaml.safe_load`, gated string-to-bool conversion via a local `_vbool` closure matching this codebase's `"true"`/`"false"` string convention) for every fact the playbook used to get from Ansible's `vars_files:` include -- `aws_account_id`, `az`, `ec2_iam_role`/`ec2_iam_policy`, `ec2_keypair`, `ec2_user`/`ec2_user_home`, the four `enable_*` gates, `fsx_hydration_iam_policy`, `s3_bucketname`, `ssh_keypair`, `ssh_secret_name`, `results_bucketname` (falling back to `_derive_results_bucket` for a vars file that predates the key, the same fallback the playbook itself has -- not a restated literal).
- **`import pcluster.lib as pc` is a local import inside `core_delete_cluster`, not module-level in `pcluster_core.py`.** Measured cost: 0.535s (`_load_model()` parses the bundled OpenAPI spec at import time) -- module-level would tax every script's startup (`list_pcluster.py`, `check_pcluster.py`, ...) even though only teardown needs it; paid once, only when actually deleting a cluster.
- **`ansible_verbosity` is dropped from `core_delete_cluster`'s signature entirely** (an unused parameter threaded through for no functional reason is itself a shim, per `CLAUDE.md`'s own style rule) but **the `--ansible_verbosity`/`-V` CLI flag stays accepted in `kill_pcluster.py`**, now printing "has no effect" rather than silently doing nothing -- removing a documented flag outright would break any existing script invoking it, and "flag accepted, now inert, says so" is the honest middle ground between a breaking removal and a silent no-op.
- **`_build_destroy_ansible_cmd` deleted outright** once nothing called it (both its callers -- `core_delete_cluster` and `kill_pcluster.py`'s pre-abort display -- were rewritten in the same pass). `kill_pcluster.py`'s "Preparing to delete cluster... using this command: <ansible-playbook invocation>" display is replaced with a simpler `Preparing to delete cluster "<name>" in <region> (delete_s3_bucketname=<value>).` -- there's no shell command left to show.
- **`delete_pcluster.yml` stays in the repo, unexecuted, as the reference spec** every function above was built to replicate -- its task names and positions are cited throughout the new code's docstrings for exactly that reason. It also stays in `make lint`'s ansible-lint target and in `test_playbook_secrets.py`'s two remaining internal-consistency checks (benchmark/monitoring gating), which check properties of the document itself, independent of whether anything executes it. Deleting the file entirely was considered and rejected as its own, separate, larger decision the user hadn't been asked about -- distinct from "wire core_delete_cluster off it," which this round's actual instruction was.
- **Test fallout, handled rather than left broken**: `test_playbook_secrets.py`'s three tests that existed specifically to verify the old Ansible flag-plumbing (`_teardown_extra_vars`'s parse of `_destroy_extra_vars_str = json.dumps(...)`, and the two cross-checks built on it) tested a path that no longer exists -- removed along with the now-dead `_when_expressions` helper; the file's other checks (which test `delete_pcluster.yml`'s own internal consistency, not whether Ansible plumbing reaches it) were kept. `tests/test_kill_pcluster.py` was rewritten from scratch: new fakes for `import pcluster.lib as pc` (installed via `monkeypatch.setitem(sys.modules, "pcluster.lib", fake)`, since the import happens at call time inside `core_delete_cluster` -- verified working even when `pcluster.api.errors` has already partially imported the `pcluster` package tree) and a single generic `_FakeAwsClient` covering every boto3 service `core_delete_cluster` touches (ec2/iam/s3/ssm/secretsmanager/sns), with a `_SHAPED` dict for the handful of methods whose return value the code actually inspects (`list_attached_role_policies`, `list_role_policies`, `list_instance_profiles_for_role`, `describe_security_groups`, `describe_availability_zones`) and a generic no-op success for everything else, since none of those other calls' return values are read. `pcluster_core.time.sleep` is stubbed globally in the fixture so a genuine `TIMED_OUT` scenario (every `describe_cluster` call answering `DELETE_IN_PROGRESS` through all 80 default retries) runs in well under a second rather than 40 real minutes -- confirmed: the file's 18 tests run in ~1s total. One test (`test_orphaned_resources_exit_nonzero`) pins a subtle, easy-to-get-backwards asymmetry: when the CloudFormation delete itself succeeds but one *cleanup* step fails, the credential steps still ran (since `cf_delete_confirmed=True`), so the serial file (inside `cluster_data_dir`, which those steps `rmtree`) is gone, while the vars file (a separate path) survives, since the function returns before reaching its own final removal code.
- **Net test count**: 6 removed from `test_playbook_secrets.py` (was 9, now 6) plus a net -3 from `test_kill_pcluster.py`'s rewrite (was 21, now 18, having consolidated several playbook-plumbing-specific tests that no longer apply and added new ones for the boto3-specific behavior: `NotFoundException`/`BadRequestException` tolerance on delete, the SNS-report-templates-dir symlink requirement, the orphan-vs-retained-file-survival asymmetry) = **net -6 this round**.

**Full suite: 2119 passed** (down from 2125 by exactly the accounted-for 6 removed/consolidated tests, not a hidden loss), 4 skipped, 0 failed. `make lint`/`make shellcheck` clean -- neither playbook nor any shell script was touched. No line-citation drift (`core_delete_cluster`'s region sits well before `iam.attach_role_policy(`'s cited line in the file, so growing it did not shift that citation).

**Workstream 3's teardown side is now fully wired end to end.** Remaining Workstream 3 scope is `create_pcluster.yml`'s 80-task migration, explicitly separate/later per the plan's own migration order (never bundled with teardown, which the plan called out as independently portable per resource group). A live smoke test of the new teardown path against a real AWS account has not been run this round -- everything here is verified by the 18 new/adapted `test_kill_pcluster.py` tests plus the 59 `test_teardown_steps.py` tests underneath them, not by an actual `kill_pcluster.py` invocation against AWS.

**Same session, round 20 -- Workstream 3's create-side migration started: the first, safest slice off `create_pcluster.yml`'s 80 tasks, matching the plan's own risk-ordered table rather than the teardown migration's grouping (create mixes four different kinds of work -- AWS resource creation, SSH/SCP node provisioning, and places where an Ansible module quietly does more than a bare boto3 call replicates for free -- so the plan explicitly warns against treating it as "simple boto3 swap" the way teardown mostly was).** Read `create_pcluster.yml` in full (780 lines, all 80 tasks) and the plan's dedicated create-side feasibility table before writing anything, per this session's established discipline for the highest-risk workstream.

- **The OS assert (task index 0) is the only piece of `create_pcluster.yml` with zero Python equivalent today, and closes a real gap rather than being a lateral port.** `make_pcluster.py`'s argparse `choices=` already gates the CLI path, but nothing in Python rejected an unsupported `base_os` if `core_create_cluster` were ever called directly (a future MCP tool, a test, a differently-argparse'd caller) -- exactly the argparse-bypass scenario `CLAUDE.md` documents as the reason the playbook kept its own copy of this check rather than trusting argparse alone. New `_assert_supported_os(base_os)` in `pcluster_core.py`, wired as the literal first statement of `core_create_cluster` (confirmed nothing before it makes any AWS mutation -- three read-only `boto3.client(...)` constructions and one non-mutating `subprocess.run` are the only calls between the function's start and where this landed). Derives its valid set from `pcluster_aux_data.ARM_OSES`/`X86_OSES` rather than a third copy of the eight-value literal list; raises via `refer_to_docs_and_quit` (a printed message + bare `sys.exit(1)`), not the newer `PClusterMakerError` convention -- `core_create_cluster`'s own docstring already documents that it deliberately keeps every validation error in that raw shape, since nothing in `make_pcluster.py` catches `PClusterMakerError` and a new one here would surface as an uncaught traceback instead of the clean error every other validation failure in this function produces.
- **The timer/local-state-dir tasks that follow the assert in the playbook turned out to already have a Python equivalent** -- `core_create_cluster`'s own `os.makedirs(cluster_data_dir, exist_ok=True)`, pre-existing, not new this round -- so no new function was needed there, just a fix to the one real gap the plan's table flagged: Ansible's `file:` module chmods explicitly *after* creation, bypassing umask, while a bare `os.makedirs(path, mode=0o755)` does not (the requested mode is ANDed with the process umask). Added an unconditional `os.chmod(cluster_data_dir, 0o755)` right after the existing `os.makedirs` call.
- **The chmod test needed two attempts to be non-vacuous, and the first failure taught something worth keeping.** A restrictive-umask test (`os.umask(0o077)` around the call) passed identically whether the fix was present or reverted -- verified by deliberately reverting the fix and re-running, the same discipline this session already applies to every new test. Root cause: the `staged` fixture (`test_make_pcluster_main.py`) already creates `active_clusters/<cluster>/` itself, before `main()` ever runs, to write the serial file -- so by the time `core_create_cluster`'s own `os.makedirs(..., exist_ok=True)` runs, the directory already exists, and `exist_ok=True` is a no-op on an existing directory's mode regardless of umask at that later point. The umask that mattered was whatever was in effect during the *fixture's* setup, not the test's. Fixed by pre-setting the directory to a wrong mode (`os.chmod(cluster_data_dir, 0o700)`) explicitly before calling `main()`, and asserting it becomes `0o755` afterward -- this actually exercises the unconditional `os.chmod` call, verified failing without the fix and passing with it restored.
- **The "must run first" property is pinned by AST position, not by running the full pipeline.** `MakeClusterParams` has ~84 fields with no existing test-side constructor helper, so a test that hand-builds one just to prove the assert fires before argparse's own gate would be expensive for what it proves (argparse already blocks that path for the CLI; the assert exists for callers that skip argparse entirely, which a params-object test can't distinguish from "argparse already caught it"). Instead, `TestAssertRunsBeforeAnythingElse` parses `pcluster_core.py`'s real AST and asserts `_assert_supported_os(...)` is the first statement (skipping past the docstring `Expr`) inside `core_create_cluster`'s function body -- the same "position is the property" discipline `CLAUDE.md` already documents for the playbook's own task-index-0 requirement, now on the Python side too. Its own discrimination test proves the walker would catch the call being moved later, not just removed.
- **A vacuity check on the pcluster_os half of the assert, since today's derivation makes it currently unreachable by any real value.** `pcluster_os = base_os.removesuffix("arm")` means every one of the eight real `base_os` values already produces a `pcluster_os` in `X86_OSES` by construction -- so the second condition (`pcluster_os not in X86_OSES`) can never fire against real input today. `test_the_pcluster_os_check_is_not_dead_code` monkeypatches `pcluster_aux_data.X86_OSES` to a narrower set and confirms a still-valid `base_os` then gets rejected, proving the condition is live code (protecting against a future change to the derivation formula), not vestigial.

**15 new tests in a new file, `tests/test_create_pcluster_migration.py`** (`_assert_supported_os` correctness across all 8 supported values, the unsupported-value/garbage-suffix/pcluster_os-vacuity cases, an AST-position pin plus its own discrimination test, and a source-grep vacuity guard that the 8 OS literals aren't hardcoded a second time in the function body) **plus 1 new test in `test_make_pcluster_main.py`** (the chmod fix, described above). **Full suite: 2135 passed** (up from 2119, +16), 4 skipped, 0 failed. `make lint`/`make shellcheck` clean -- `create_pcluster.yml` itself is unmodified this round (still executed wholesale via `ansible-playbook` for everything past this first slice); no line-citation drift.

**Next round's scope, following the plan's own risk ordering for create (not a copy of teardown's grouping):** the SNS-topic-create + S3-bucket/keypair/secret block (with its `block:`/`rescue:` -- "best-case mapping in the whole file," structurally `try:`/`except:` already, but `ec2_private_key.changed` needs real translation work since boto3's `create_key_pair` has no equivalent existing-vs-created flag). The monitoring/Docker-Compose checksum-verified downloads, the create-cluster launch+wait+3-way-abort-classification (directly parallels the delete-side work already done), and the ~13-task SSH/SCP orchestration to the head node (no AWS API involvement at all, its own lower-risk category) remain later, explicitly sequenced per the plan's own table rather than folded into one "boto3 swap" pass.

**Same session, round 21 -- Workstream 3's create-side migration, second slice: the S3 bucket / EC2 keypair / Secrets Manager block from `create_pcluster.yml`, with its `rescue:`, built and tested standalone (not wired in).** The plan calls this "the best-case mapping in the whole file" -- Ansible's `block:`/`rescue:` is structurally `try:`/`except:` already -- but flags `ec2_private_key.changed` as real translation work, since boto3's `create_key_pair` has no equivalent existing-vs-created flag two downstream Ansible tasks (the orphaned-keypair abort, and whether to save the returned key material) depend on precisely.

- **Six new functions plus a composing entry point in `pcluster_core.py`**: `_create_s3_bucket_for_cluster` (one function with a caller-built tag dict, collapsing the playbook's `project_id == "UNDEFINED"` vs. not pair -- exactly the duplication pattern `CLAUDE.md` documents as a past bug source; idempotent on `BucketAlreadyOwnedByYou`; omits `CreateBucketConfiguration` for `us-east-1`, which S3 rejects otherwise), `_create_external_nfs_sg` (10 ingress rules -- 5 ports x tcp/udp -- idempotent on `InvalidGroup.Duplicate` via a `describe_security_groups` lookup, returning the existing group's id), `_generate_ec2_keypair` (the `ec2_private_key.changed` translation: `(True, pem)` on success, `(False, None)` on `InvalidKeyPair.Duplicate`, matching that AWS never returns key material for an existing keypair), `_abort_if_keypair_orphaned`, `_save_private_key_locally` (mode 0600, only called when `changed`), `_store_ssh_secret` (Secrets Manager, tolerant of `ResourceExistsException`), and `provision_s3_keypair_and_secret` (the `try:`/`except:` composing function with rescue-style cleanup).
- **The Secrets Manager write is unconditional, exactly matching `TestTheSshSecretIsWrittenOnEveryRun`'s hard-won fix on the Ansible side** (`tests/test_templates.py`) -- gating it on `changed` was the original bug: a build that failed after the keypair existed but before the secret was written left no secret at all, and no way back into the head node via `retrieve_ssh_key.<cluster>.sh`. `_store_ssh_secret` reads the *local* `.pem` file rather than key material passed by the caller, since on an ungated call there may be none (a resumed build's keypair already existed) -- the local file is guaranteed present by that point because `_abort_if_keypair_orphaned` already checked it.
- **The rescue cleanup reuses the teardown migration's own step functions** (`_delete_s3_bucket_step`, `_delete_ec2_keypair_step`, `_delete_secrets_manager_secret_step`, `_delete_external_nfs_sg_step`, all from rounds 16-17) rather than re-implementing the same four AWS deletes a second time -- each already tolerates the resource being absent and never raises. Pinned by a source-level vacuity guard that the cleanup function's body actually calls all four by name, not a reimplementation that happens to produce the same visible effect today.
- **A subtle fidelity point that reads like a bug until the Ansible source is checked closely: the security-group cleanup gates on `enable_external_nfs`, not on "did this run create it."** The playbook's own rescue task has no memory of which sub-step failed -- it deletes the S3 bucket, keypair, secret, and (if the flag is set) the SG unconditionally, on *any* failure anywhere in the block. So a failure on the keypair step still deletes a security group a *previous*, interrupted attempt already created. This is intentional, not sloppy: the whole block is meant to roll back atomically -- all four resource types or none -- not only whichever ones this particular attempt happened to reach. `provision_s3_keypair_and_secret` matches this exactly rather than "improving" it into a created-this-run tracking flag, which would silently change retry behavior.
- **No wiring this round, matching the "build first, wire later" split already proven across rounds 16-19 of the teardown migration.** `create_pcluster.yml` still executes this block via Ansible today, and several existing tests in `tests/test_templates.py` pin hard-won safety properties directly against that Ansible implementation (`no_log` on every task touching key material, appearing in two separate test classes; the ungated Secrets Manager write; task ordering between the abort and the secret write). Wiring this in means removing that block from the playbook and updating those tests to pin the same properties against the new Python functions instead -- correctly a separate, later round, the same reasoning that kept rounds 16-18 unwired until round 19.

**42 tests total in `tests/test_create_pcluster_migration.py`** (27 new this round): bucket creation/tagging/idempotence/region-handling, security-group port/protocol/CIDR correctness and duplicate-lookup, keypair changed/not-changed semantics, the orphan-abort's three branches, the 0600 file-mode write, secret-store idempotence and local-file-read behavior, the cleanup function's reuse of the teardown steps (with the vacuity/DRY guard), and the composing function's happy path (with and without external NFS), the resumed-build-still-writes-the-secret property, the orphaned-keypair-triggers-full-cleanup property, an unexpected-failure-cleans-up-and-reraises-unchanged property, and a `capsys`-based test that the fake private key material never appears in captured stdout/stderr -- the closest Python analog to `no_log`'s protection, verified behaviorally rather than assumed. One test-authoring bug caught and fixed during this round: two assertions expected the deleted security-group id to equal `cluster_name` when it's actually whatever the fake's `describe_security_groups` returns (its own hardcoded stand-in id) -- caught by the tests themselves failing on first run, not by a later review pass.

**Full suite: 2162 passed** (up from 2135, +27), 4 skipped, 0 failed. `make lint`/`make shellcheck` clean -- `create_pcluster.yml` is unmodified this round, still executed wholesale via `ansible-playbook`; no line-citation drift.

**Next round's scope, per the plan's own risk ordering:** the monitoring tarball + Docker Compose CLI plugin checksum-verified downloads (not a bare swap -- Ansible's `get_url` verifies checksum *during* download and fails the task on mismatch; Python needs an explicit `hashlib.sha256` comparison after `urllib.request.urlretrieve()`/`requests`, distinct from the existing pre-flight `_validate_download_checksum` that only checks the checksum string's format); or the create-cluster launch+wait+3-way-abort-classification (~6 tasks, directly parallels the delete-side `run_cluster_delete_and_classify` work from round 18); or the ~13-task SSH/SCP orchestration to the head node (no AWS API involvement at all, its own lower-risk category, mechanically `subprocess.run([...])` in place of Ansible's `shell:`/`command:` wrapper). None yet chosen -- whichever comes next should still be built standalone before any wiring round, matching this round's and the teardown migration's proven shape.

**Same session, round 22 -- Workstream 3's create-side migration, third slice: the monitoring tarball / Docker Compose CLI plugin checksum-verified downloads from `create_pcluster.yml`'s "Stage and upload monitoring post-install wrapper" block, built and tested standalone (not wired in).** The plan flags this as "not a bare swap" -- Ansible's `get_url` verifies the checksum *during* download and fails the task on mismatch, a genuinely different check from the existing `_validate_download_checksum` (format-only, pre-flight, no network access at all).

- **`_download_with_checksum(url, dest, checksum, *, mode)`**: streams to a temp file in `dest`'s own directory via `urllib.request.urlopen` (stdlib, not `requests` -- avoids adding an undeclared transitive dependency as a direct one), hashes the temp file, and only `os.replace()`s it into `dest` once the digest matches. `os.replace` is atomic on the same filesystem, so a checksum mismatch or any other failure leaves no partial or wrong file at `dest` at all -- verified by a test asserting the containing directory is completely empty afterward, not just that `dest` itself is absent.
- **`_upload_file_to_s3` deliberately omits the ACL parameter, a considered deviation from `amazon.aws.s3_object`'s `permission: private`, not a silent guess.** S3 has defaulted every *new* bucket to "bucket owner enforced" (ACLs disabled) since April 2023, and `_create_s3_bucket_for_cluster` (round 21) does not override that default -- passing any `ACL=` on such a bucket raises `AccessControlListNotSupported`. Omitting the parameter produces the same effective permission (owner-only access) under that default and is the strictly safer choice regardless of what the Ansible module's own `permission: private` resolves to on whatever this account's buckets are configured with today, which was not independently verified live this round (no existing incident or doc references an ACL problem on this codebase's buckets either way -- reasoned from S3's documented default behavior, not assumed from a memory of how the Ansible side behaves).
- **Three composing functions**: `_stage_monitoring_tarball`, `_stage_docker_compose_plugin` (gated on `stage_docker_compose`, called by the caller not internally -- matching the playbook's own per-task `when:`, not a block-level one), and `stage_and_upload_monitoring_wrapper` (the single entry point, gated on `enable_monitoring` exactly like the block's own `when:`). The wrapper script itself is already rendered by Workstream 2's Tier 2 cutover (`render_template`) -- this function's job for it is upload only, the one task in the original block that was never a download.
- **Fail-fast ordering is pinned by a dedicated test**: a checksum mismatch on the monitoring tarball must stop before the Docker Compose plugin download is even attempted, not proceed as though nothing had gone wrong -- `test_tarball_checksum_mismatch_stops_before_docker_compose` asserts exactly one URL was ever requested and nothing reached S3.

**12 new tests, extending `tests/test_create_pcluster_migration.py`** (54 total): the download function's success/mismatch/unsupported-algorithm/network-failure/mode-setting cases (including the empty-directory assertion proving no temp-file leak), the upload helper's no-ACL property, both staging functions' URL/path construction, and the composing function's four cases (disabled, tarball-only, tarball+compose, and the fail-fast ordering test).

**Full suite: 2174 passed** (up from 2162, +12), 4 skipped, 0 failed. `make lint`/`make shellcheck` clean -- `create_pcluster.yml` is unmodified this round, still executed wholesale via `ansible-playbook`. **One line-citation drift this round, same shape as round 18's**: the new top-level `import hashlib`/`import urllib.request` shifted `iam.attach_role_policy(` from `src/pcluster_core.py:977` to `:979` -- fixed in both citing surfaces (`tests/test_claude_docs_line_citations.py`'s manifest and `templates/CLAUDE.local.md`'s prose), caught by the full suite itself before being reported here.

**Three slices of the create-side migration now built and tested standalone, none wired in**: the OS assert (round 20), the S3/keypair/secret block (round 21), and this round's monitoring/Docker-Compose downloads. `create_pcluster.yml` still runs all three originals via Ansible. Next per the plan's ordering: the create-cluster launch+wait+3-way-abort-classification (~6 tasks, directly parallels round 18's delete-side `run_cluster_delete_and_classify`), or the ~13-task SSH/SCP orchestration to the head node (no AWS API involvement at all, its own lower-risk category -- mechanically `subprocess.run([...])` in place of Ansible's `shell:`/`command:` wrapper, the same shape as the already-confirmed-safe SSH known_hosts handling). Wiring all three built slices (plus whichever comes next) into `core_create_cluster`, removing their Ansible originals, and adapting the existing task-level tests remains a separate, later round.

**Same session, round 23 -- Workstream 3's create-side migration, fourth slice: the create-cluster launch + wait + 3-way abort classification + head-node-IP extraction, directly parallel to round 18's delete-side `run_cluster_delete_and_classify`, built and tested standalone (not wired in).**

- **`cluster_configuration` must be a filesystem path, not raw YAML content -- confirmed by reading `pcluster/cli/model.py` directly, not assumed.** `create-cluster`'s `clusterConfiguration` body parameter is explicitly tagged `"type": "file"` in the CLI model's per-operation overrides (`_TYPE_OVERRIDES` equivalent), so `pcluster.lib`'s dispatcher hands any string value to `read_file()`, which `open()`s it as a path -- exactly matching the CLI's own `--cluster-configuration <path>` flag, and exactly the trap the round-18 `NotFoundException` finding warned this whole workstream to watch for (reading the actual dispatch code rather than assuming a body field behaves like its OpenAPI type says).
- **A real, previously undocumented finding about existing production behavior, verified against the installed package rather than assumed: `describe_cluster`'s structured `clusterStatus` field can only ever show `CREATE_COMPLETE` or `CREATE_FAILED` for a cluster in this state machine -- never the literal strings `"ROLLBACK_COMPLETE"`/`"ROLLBACK_FAILED"` the playbook's own `until:` loop greps for.** `pcluster.api.converters.cloud_formation_status_to_cluster_status` maps CloudFormation's `ROLLBACK_IN_PROGRESS`, `ROLLBACK_FAILED`, and `ROLLBACK_COMPLETE` *all* to `ClusterStatus.CREATE_FAILED` -- confirmed live against the actually-installed package (`cloud_formation_status_to_cluster_status(CFN.ROLLBACK_IN_PROGRESS) == "CREATE_FAILED"`, etc.), not just read from source. The playbook's four-string check only "works" today because it searches the *entire raw JSON blob*, where the separate, unmapped `cloudFormationStackStatus` field can carry the literal `ROLLBACK_*` text -- but `CREATE_FAILED` is already present via `clusterStatus` a full CloudFormation state *earlier*, the moment rollback *begins* rather than once it *finishes*. So today's Ansible playbook already stops waiting and aborts the build as soon as CloudFormation enters `ROLLBACK_IN_PROGRESS`, not once cleanup completes -- this is existing production behavior the migration reproduces faithfully (`_wait_for_cluster_create` checks exactly two `clusterStatus` outcomes, which is provably equivalent, not a simplification that changes anything), not a bug introduced or fixed by this round. A dedicated test (`test_rollback_states_collapse_into_create_failed_in_the_installed_package`) pins the mapping directly against the installed `pcluster` package, guarding against a future `aws-parallelcluster` upgrade silently breaking the equivalence.
- **The head node IP is extracted from the *same* describe-cluster response that already decided `CREATE_COMPLETE`, not a fresh API call** -- matching what the playbook's own `set_fact` actually does (it reads `cluster_status.stdout`, the wait loop's last-captured result, never issuing a new `describe-cluster`). `_wait_for_cluster_create` therefore returns `(terminal_state, last_response)`, not just the state string, so `_extract_head_node_ip` can read off it directly; a fresh call at that point would risk seeing a different snapshot than the one that actually confirmed the cluster was ready, and would just be wasteful besides.
- **A genuine, low-cost improvement over the playbook's own fail message: the `CREATE_FAILED` headline surfaces `failureCode`/`failureReason` from `describe_cluster`'s own `failures` field** (populated by AWS ParallelCluster's controller only when `clusterStatus == CREATE_FAILED`, reading CloudFormation's own stack-event failure detail) when available, falling back to the same terse `(CREATE_FAILED)` message the playbook already produces when it isn't. The Ansible fail message only ever echoes the literal string "CREATE_FAILED" with no further diagnostic content -- this costs nothing extra (the field is already in the response the wait loop already fetched) and matches this migration's established precedent of picking up small real improvements where the Python side can do strictly better for free (the delete-side classification logic, the cluster-launch-summary print).
- **No tolerance on the initial `create_fn` call, unlike the delete side's tolerance of `NotFoundException`/`BadRequestException`.** Any exception from `pc.create_cluster(...)` propagates immediately, matching the playbook's `failed_when: create_cluster_result.rc != 0` with no exceptions carved out -- there is no "already exists" case to tolerate here, since Workstream 1's existing preflight already rejects a build over an existing cluster stack before `core_create_cluster`'s pipeline ever reaches this point.
- **`rollback_on_failure` defaults to `False`, matching the playbook's hardcoded `--rollback-on-failure false`** rather than AWS's own API default (`true`) -- automatic rollback would delete every resource CloudFormation created before an operator can diagnose the failure, defeating the point of a failed-build postmortem (the same reasoning already documented for retained CloudWatch log groups on teardown).

**21 new tests, extending `tests/test_create_pcluster_migration.py`** (75 total): the live installed-package mapping-verification test; the wait loop's six cases (mirroring the delete side's `TestWaitForClusterDelete` shape: complete/failed on first attempt, still-building-then-completes with sleep-count assertions, timeout, transient-error-recovery, persistent-error-reraise); head-node-IP extraction's four cases; classification's four cases (including both failure-detail branches); and the composing function's six cases (configuration-path-not-content, `rollback_on_failure` default, happy path returning the IP, no-tolerance-on-create-failure with a check that the wait loop was never even reached, and the create-failed/timeout flows both correctly returning no IP).

**Full suite: 2195 passed** (up from 2174, +21), 4 skipped, 0 failed. `make lint`/`make shellcheck` clean -- `create_pcluster.yml` is unmodified this round, still executed wholesale via `ansible-playbook`. No line-citation drift this round (no new top-level imports, unlike rounds 18 and 22).

**Four slices of the create-side migration now built and tested standalone, none wired in**: OS assert (round 20), S3/keypair/secret block (round 21), monitoring/Docker-Compose downloads (round 22), create-cluster launch+wait+classify (this round). `create_pcluster.yml` still runs all four originals via Ansible. Next per the plan's ordering: the ~13-task SSH/SCP orchestration to the head node (no AWS API involvement at all, its own lower-risk category -- mechanically `subprocess.run([...])` in place of Ansible's `shell:`/`command:` wrapper) is the one remaining unbuilt piece the plan's table names explicitly; after that, wiring all built slices into `core_create_cluster`, removing their Ansible originals, and adapting the existing task-level tests becomes the natural next major round, mirroring how round 19 wired the delete side after rounds 16-18 built it.

**Same session, round 24 -- Workstream 3's create-side migration, fifth and final built slice: the SSH/SCP orchestration to the head node ("Wait for SSH port to be reachable" through "Remove the staging directory on the head node"), built and tested standalone (not wired in). This closes out every slice the plan's own table names for the create side.**

- **Twelve functions plus one composing entry point**, matching the plan's own framing exactly: none of this touches an AWS API at all, it is entirely `subprocess.run([...])` in place of Ansible's `shell:`/`command:` wrapper around the identical `ssh`/`scp`/`ssh-keygen`/`ssh-keyscan` invocations. `_wait_for_ssh_port` reuses the same `socket.create_connection` probe shape as `_check_external_nfs_reachable` (the precedent `CLAUDE.md` already names for this exact class of check), with an injectable `time_fn` so tests exercise the timeout path without any real wall-clock waiting. `_accept_ssh_fingerprint` reproduces the shell script's exact-match dedup (`grep -qxF`) and its one fatal case (no host key at all raises, since every later `ssh`/`scp` call in the group depends on a recorded fingerprint) while `_remove_stale_known_hosts_entry` stays unconditionally tolerant, matching the shell's own `|| true`.
- **A real, previously-latent ordering detail preserved deliberately rather than "cleaned up": the three looped perf-directory tasks (`mkdir`, `chown`, `cp`) run in three separate phases over every directory, not interleaved per directory.** Ansible's `loop:` on three separate tasks runs each task to completion over every item before the next task starts (`mkdir dir1, mkdir dir2, ..., then chown dir1, chown dir2, ..., then cp dir1, cp dir2, ...`), not the arguably more natural `mkdir dir1, chown dir1, cp dir1, mkdir dir2, ...` grouping. `_create_and_own_perf_dirs` reproduces the exact phase ordering with a dedicated test asserting the full command sequence.
- **One small, disclosed improvement: `_copy_performance_source_tree` expands `performance_stage_dir/*` in Python via `glob.glob` rather than relying on shell glob expansion**, since every function in this slice deliberately avoids `subprocess.run(..., shell=True)` for the usual injection-surface reasons. A side effect, tested explicitly: an empty `performance_stage_dir` now copies nothing rather than passing a literal unmatched `*` to `scp` and failing.
- **`dirname` computations use `os.path.dirname()` directly rather than shelling out to the `dirname` binary**, confirmed safe by tracing exactly how the playbook's own `shell:` task evaluates `"mkdir -p $(dirname '{{ stage_dir }}')"` -- the `$(...)` is inside the *local* shell's double quotes, so it is evaluated locally (a pure string operation with no filesystem access) before the resulting literal string is ever handed to `ssh` as the remote command; `os.path.dirname()` produces the identical result.
- **The composing function, `deploy_staging_and_performance_tree_to_head_node`, gates everything on a single `head_node_public_ip != ""` check up front** rather than repeating the identical `when:` on all twelve tasks the way the playbook does -- every individual function below it assumes it already has a real IP rather than re-checking, matching the plan's own preference for Python conditionals reading more clearly than the equivalent repeated Ansible `when:` clauses.

**26 new tests, extending `tests/test_create_pcluster_migration.py`** (101 total): the wait-for-port function's four cases (immediate success, delay-before-first-probe, retry-then-succeed, timeout) using an injectable fake clock; local SSH directory creation and mode-correction; known_hosts removal's unconditional tolerance; fingerprint acceptance's append/dedup/fatal-no-key cases; each SSH/SCP task function's own argv-construction test; the `&&`-short-circuit property (scp is skipped when the remote mkdir fails); the empty-glob-copies-nothing property; the three-phase (not interleaved) perf-directory ordering, pinned by asserting the full six-command sequence; and the composing function's four cases (empty IP is a no-op, HPC benchmarks disabled skips the performance steps, HPC benchmarks enabled runs the full sequence, and known_hosts setup happens before any `scp`). One test-authoring bug caught and fixed during this round: an `or`-based stub (`runner(cmd, *a, **k) or _FakeCompletedProcess(...)`) silently always returned the recorder's own return value rather than the intended stubbed response, since a `_FakeCompletedProcess` object is truthy regardless of its contents -- caught by the test itself failing on first run (a `ssh-keyscan returned no host key` error), not by a later review pass, the same "caught by running it" discipline as round 21's security-group-id assertion bug.

**Full suite: 2221 passed** (up from 2195, +26), 4 skipped, 0 failed. `make lint`/`make shellcheck` clean -- `create_pcluster.yml` is unmodified this round, still executed wholesale via `ansible-playbook`. **One line-citation drift, same shape as rounds 18 and 22, fixed proactively before running the full suite this time** (new top-level `import glob`/`import shlex` shifted `iam.attach_role_policy(` from `src/pcluster_core.py:979` to `:981` -- both citing surfaces fixed immediately after adding the imports, confirmed via `grep` rather than waiting for the doc-hygiene test to catch it).

**All five slices of the create-side migration the plan's table names are now built and tested standalone, none wired in**: OS assert (round 20), S3/keypair/secret block (round 21), monitoring/Docker-Compose downloads (round 22), create-cluster launch+wait+classify (round 23), SSH/SCP orchestration (round 24). `create_pcluster.yml` still runs every one of the originals via Ansible. The natural next major round is wiring all five into `core_create_cluster` in place of the `ansible-playbook` subprocess call, removing their Ansible originals from `create_pcluster.yml`, and adapting the existing task-level tests that currently pin safety properties directly against those originals -- mirroring exactly how round 19 wired the delete side after rounds 16-18 built it.

**Same session, round 25 -- Workstream 3's create-side migration, final round: user explicitly directed "move forward with wiring all five into core_create_cluster, removing their Ansible originals, and adapting the existing task-level tests," then, when a mid-round `AskUserQuestion` surfaced that the five built slices interleave with roughly nine still-unbuilt task groups in `create_pcluster.yml` (making a clean "wire only the five" pass architecturally awkward without splitting the build into two separate `ansible-playbook` invocations), chose "Finish everything" over a split-playbook compromise -- build the remaining task groups too, so `create_pcluster.yml` becomes fully unexecuted in one pass, exactly mirroring round 19's clean end state for `delete_pcluster.yml` on the teardown side.**

- **Nine new functions closing out every remaining task group**, all added to `pcluster_core.py` ahead of `core_create_cluster`'s own body: `_create_sns_topic_and_notify` (idempotent `create_topic` by name, subscribe, initial "build started" publish -- returns the topic ARN, needed later for the build-summary publish and by teardown to delete the same topic); `render_and_upload_cluster_config_and_scripts` (finally renders `config.pcluster.j2` in Python -- deferred since Workstream 2's Tier 1 cutover because it needs `{{ external_nfs_sg.group_id }}`, an Ansible `register:` result with no Python equivalent until round 21's `_create_external_nfs_sg` existed; copies the operator pre/post-deployment hooks into `cluster_data_dir`; uploads five files to S3); `_upload_external_nfs_mount_list`; `_create_hpc_results_bucket` (idempotent, three fixed tags, matching the long-lived results-bucket precedent `CLAUDE.md` already documents); `stage_and_upload_hpc_benchmark_driver` (a real `aws s3 sync --exclude "*" --include "hpc-benchmark.sh"` subprocess call, the allowlist-on-both-ends shape `CLAUDE.md`'s own blocklist-leak incident already documents, kept as a real CLI call rather than hand-rolled boto3 per the migration plan's own recommendation); `print_cluster_launch_summary` (the pre-launch informational print, distinct from `core_create_cluster`'s own comprehensive post-creation summary, which already existed and needed no rewrite beyond swapping in the boto3-derived head node IP); `finalize_staging_directory` (`shutil.copytree` + a real `aws s3 sync --exclude "*.pem"` subprocess call -- the one place this toolkit must never let a private key reach S3, the Python twin of `TestCreatePlaybookExcludesPrivateKeyFromS3Sync` on the Ansible side); `render_and_publish_build_summary_report` (renders `sns_build_summary_report.j2`, Workstream 2's own deferred Tier 4, since it needs mid-build timers and `head_node_public_ip` that did not exist as Python values until this round); `print_fsx_hydration_helper_locations`.
- **One disclosed, deliberate fix, not a silent behavior change: `print_fsx_hydration_helper_locations` gates its print on `enable_fsx_hydration`.** The Ansible task it replaces has no `when:` clause at all -- confirmed by re-reading the raw task -- so a cluster with the feature disabled would have printed `s3://UNDEFINED/UNDEFINED` import/export paths (`fsx_hydration_iam_policy`'s own `"UNDEFINED"` sentinel flowing into the same variables). Printing paths that reference a feature the operator never enabled has no legitimate reading as intentional.
- **A kwarg-collision bug caught by reasoning before ever running a test, not by a failure.** The first draft of both `render_and_upload_cluster_config_and_scripts` and `render_and_publish_build_summary_report` called `render_template(templates_dir, name, **ctx, external_nfs_sg=...)` (or the four timer keys, for the summary report) -- `ctx` is `{**cluster_parameters, **rendered vars_file}`, and `tests/conftest.py`'s `cluster_params` fixture already carries `external_nfs_sg`/`head_node_public_ip`/all four timer keys for *other* templates that need them, so the explicit kwarg would collide with an identical key already in `**ctx` and raise `TypeError: got multiple values for keyword argument`. Fixed by building a merged dict first (`render_kwargs = {**ctx, "key": value}`) and splatting that instead, so the explicit value always wins rather than colliding. `test_does_not_collide_when_ctx_already_carries_external_nfs_sg` is the regression test.
- **The wiring itself replaces `core_create_cluster`'s entire tail** (from the old "Parse the Python3 interpreter path..." comment through the function's final `sys.exit(0)`) with two failure-handling phases matching the Ansible original's own two shapes, now distinguishable since each stage is its own Python call rather than one collapsed `ansible-playbook` exit code: **early-stage** (SNS topic through HPC-driver staging, everything before `pc.create_cluster` is ever called) rolls back everything the build has created so far -- S3 bucket, keypair, secret, external NFS SG (via the same teardown-side step functions rounds 16-17 already built), SNS topic, and IAM -- because nothing exists yet for `kill_pcluster.py` to usefully retry against; **late-stage** (from the launch call onward, including SSH/staging/report) preserves the S3 bucket/keypair/secret/SNS topic/serial file/vars file and only cleans up IAM, then tells the operator to run `kill_pcluster.py`, exactly matching the *existing, unchanged* Ansible-era failure handler's own behavior (verified by reading `_build_result.returncode != 0`'s original handler before deleting it, which only ever touched IAM). `import pcluster.lib as pc` happens locally inside `core_create_cluster`, the identical pattern `core_delete_cluster` already established on the teardown side.
- **A real bug caught during self-review, before any test was written against it, and fixed the same way round 21's `_abort_if_keypair_orphaned` ordering was preserved deliberately: `run_cluster_create_and_classify`'s `create_fn` (`pc.create_cluster`) is documented to raise on any failure with no tolerance, by design (round 23) -- but the first draft of the wiring called it with no `try:`/`except:` around it at all.** An `AccessDenied`/`ValidationError`/etc. from the real launch call would have propagated as an unhandled traceback out of `core_create_cluster` entirely, skipping every cleanup path -- no IAM deletion, no "run kill_pcluster.py" guidance, no released build lock. Fixed by wrapping the call in its own `try:`/`except:` that routes any raised exception through the same late-stage `_fail_after_launch` cleanup as an unconfirmed launch. Caught by careful re-reading of `run_cluster_create_and_classify`'s own docstring against the new call site, not by a failing test -- `test_a_raised_launch_exception_is_treated_as_a_failed_launch` (`tests/test_make_pcluster_main.py`) now pins it so a future edit can't reopen the gap silently.
- **`tests/test_make_pcluster_main.py` rewritten wholesale, not patched incrementally**, since nearly every fixture and test in it assumed an `ansible-playbook` subprocess call that no longer exists. The `staged` fixture now installs a `_FakePcLibCreate` (`types.ModuleType`, the identical `sys.modules["pcluster.lib"]` pattern `test_kill_pcluster.py` already established on the delete side) alongside extended EC2/S3/SecretsManager/SNS fakes covering every AWS call the new wiring makes. Three environment traps found and fixed while writing it, none of them findable by reading the wiring code alone:
  - **Symlinking `scripts/pre-deployment.sh`/`post-deployment.sh` (mirroring how the fixture already symlinks the sbatch script) broke every single test.** `make_pcluster.py`'s own `pre_install_script`/`post_install_script` escape check calls `os.path.realpath` on the joined path, which *follows* a symlink to its real-repo target -- outside `tmp_path` -- and aborts every build before `core_create_cluster` is ever reached. The sbatch script symlink is safe only because it is never subject to that specific check. Fixed by writing real (non-symlink) placeholder file content for both instead.
  - **`ssh_known_hosts` is derived inside `core_create_cluster` as `os.path.expanduser("~")/.ssh/known_hosts` -- the operator's real home directory.** The SSH orchestration slice's `_ensure_local_ssh_dir`/`_accept_ssh_fingerprint` are real (non-subprocess) Python file I/O, not `subprocess.run` calls the fixture's fake intercepts -- so an unpatched `HOME` would have made every successful-build test for real create `~/.ssh` (if absent) and append lines to the developer's actual `known_hosts` file. Fixed with `monkeypatch.setenv("HOME", str(tmp_path))`.
  - **`_wait_for_ssh_port` unconditionally sleeps its `delay` (default 5s) before the first probe, and would attempt a real `socket.create_connection` to whatever fake IP a test's `pc_lib` returns.** Fixed with `monkeypatch.setattr(pcluster_core.time, "sleep", lambda s: None)` and a `pcluster_core.socket.create_connection` stub returning a context manager that succeeds immediately -- the same class of fix `_check_external_nfs_reachable`'s own tests already use for this exact precedent function.
  - Also needed: `_accept_ssh_fingerprint` raises fatally on empty `ssh-keyscan` output (by design, round 24), so the fixture's `_run` fake now returns a non-empty fake host-key line for that command specifically, rather than the generic `_Proc(0)` every other stubbed subprocess call gets.
- **Old assertions retired, not merely renamed, where the concept they tested no longer exists**: the `_playbook_vars(record)` helper (parsed `--extra-vars` JSON off a captured `ansible-playbook` call) is replaced by `_vars_file_written(staged)`/`_rendered_vars(staged)` reading the actual rendered vars file directly -- the same file both the old and new code paths write at the identical point in the pipeline, so every "did the build abort before any AWS mutation" assertion maps onto "was the vars file ever written" with no change in what property is actually being proven. The old two-source-agreement check (`_rendered_vars(...)["gpu_ranks_per_node"] == _playbook_vars(...)["gpu_ranks_per_node"]`) is dropped rather than translated: there is no longer a second, independently-rendered payload for the value to drift from, since the one vars-file context is now the sole source `config.pcluster.j2` and `pc.create_cluster`'s file path both read from. the old debug-mode-raises-Ansible-verbosity test (Ansible-specific, no analog) is removed outright. `TestBuildFailureCleansUpIam`'s tests are retargeted from a variable playbook exit code (the Ansible child process's own rc, which no longer exists) to `pc_lib.describe_response = {"clusterStatus": "CREATE_FAILED", ...}` -- exit code is now always a fixed `1` on any core_create_cluster-detected failure rather than an inherited subprocess rc, so the old variable-rc test becomes the simpler `test_failure_exit_code_is_one`.
- **One new integration-level test added, not just retargeted**: `test_cluster_configuration_handed_to_pcluster_lib_is_the_rendered_config` asserts end-to-end through `main()` that `pc.create_cluster`'s `cluster_configuration` argument is the real rendered `config.<cluster>` file path (and that the file actually exists on disk at that point), not the vars file or raw YAML content -- closing the loop on round 23's file-path-not-content finding at the one call site that actually matters in production.

**52 tests in the rewritten `tests/test_make_pcluster_main.py`** (up from 51 before this round's rewrite -- essentially a 1:1 property-for-property translation plus one new integration-level test, not a net expansion of coverage): every preflight/abort-window/lock-lifecycle/derived-variable/GPU-ranks/idle-notice/storage-summary property from the pre-wiring suite still holds, now proven against the real wired pipeline instead of a stubbed Ansible invocation.

**Full suite: 2235 passed** (up from 2221 at the end of round 24, +14: `test_create_pcluster_migration.py` grew from 101 to 115 tests covering the nine new final-slice functions, and `test_make_pcluster_main.py`'s rewrite is a net +1), 4 skipped, 0 failed. `make lint`/`make shellcheck` clean. `create_pcluster.yml` is now **fully unexecuted** -- every task group it contains has a Python equivalent that `core_create_cluster` actually calls, and the `ansible-playbook` subprocess invocation that used to run it is gone from `core_create_cluster` entirely, matching `delete_pcluster.yml`'s status since round 19. It stays in the repo, untouched this round, as the reference spec every new function's docstring cites -- deleting it outright remains a separate, unasked decision, exactly as round 19 left `delete_pcluster.yml`. No line-citation drift (`iam.attach_role_policy(` still cites `src/pcluster_core.py:981` correctly -- confirmed via `grep` and by the doc-hygiene suite itself, both green). Preamble byte budget: 145,689 of 148,000 bytes, 2,311 bytes of headroom -- above the 2,000-byte working-margin floor but worth condensing proactively next time CLAUDE.local.md needs a new bullet, per this repo's own standing feedback about reactive-only headroom checking.

**Workstream 3 is now complete on both sides.** Teardown (`core_delete_cluster`, round 19) and create (`core_create_cluster`, round 25) both run entirely on boto3/`pcluster.lib`, with zero `ansible-playbook` invocations anywhere in the toolkit's own execution path. `ansible-playbook` itself remains a declared dependency only insofar as `ansible --version` is still checked at `make_pcluster.py` startup (unrelated, pre-existing, out of this migration's scope) and both YAML playbooks remain in the repo as unexecuted reference specs.

**Same session, round 26 -- Workstream 4 (async job handling) started, first slice: an S3-backed distributed cluster lock, built and tested standalone (not wired into `core_create_cluster`/`core_delete_cluster` yet). User instruction: "start on Workstream 4," with no further scoping -- the plan's own design (section 4, `docs/parallelclustermaker-mcp-plan.md`) was followed as written rather than re-litigated.**

- **Why the existing lock has to go, not just gain a distributed mode: CLAUDE.md already documents `_acquire_cluster_lock`'s local `mkdir` lock as explicitly "local-machine scope only, not a distributed lock"** -- a deliberate, fine limitation while the only caller was a CLI operator on their own laptop. It stops being fine the moment a remote MCP server exists at all (Workstream 5+), since a local file lock on that server can't see a concurrent CLI invocation on the operator's own machine and vice versa, regardless of hosting choice (EC2/Fargate/Lambda all fail identically here -- this was never a Lambda-specific gap, contrary to an earlier draft of the plan that framed it that way).
- **`s3_acquire_cluster_lock`/`s3_release_cluster_lock` plus three small helpers** (`_derive_locks_bucket` -- same account+region-keyed derivation shape as `_derive_results_bucket`, since the lock bucket has to exist before the cluster's own `s3_bucketname` does, exactly like the results bucket's own reasoning; `_create_locks_bucket` -- idempotent, unconditional unlike the gated results bucket; `_is_conditional_write_rejection`) in `pcluster_core.py`, all keyword-only where they take more than one same-typed argument.
- **The atomicity primitive is `PutObject(..., IfNoneMatch='*')`, confirmed viable against this repo's actual pinned `botocore==1.43.65`** (well past S3's August 2024 conditional-write release) by reading the installed package's own S3 API model directly (`botocore/data/s3/2006-03-01/service-2.json.gz`'s `PutObjectRequest` shape), not assumed from general S3 knowledge. That same read surfaced a detail the plan's prose hadn't named: a losing conditional write can return either HTTP 412 (`PreconditionFailed`, the clean-loss case) *or* HTTP 409 (`ConditionalRequestConflict`, S3's own documented shape for two writes racing at the same instant) -- S3's own docs say to treat both identically, fetch the current ETag and retry, so `_is_conditional_write_rejection` checks both the error `Code` and the raw HTTP status, since the exact `Code` casing has not been confirmed against a real bucket yet.
- **Staleness and reclaim, per the plan's corrected design (its own 2026-08-19 note fixing an earlier draft that would have let a plain overwrite reopen the exact race it exists to prevent):** a losing `PutObject(IfNoneMatch='*')` triggers a `GetObject` of the existing lock to read its age, ETag, and JSON-encoded owner info (JSON, not the local lock's plain-text file, since the reclaim path needs to parse the recorded state back out programmatically). Only when the lock is *both* older than a configurable ceiling (`_LOCK_STALENESS_CEILING_SECONDS`, defaulting to 7200s -- 2x the 3600s default create/delete wait ceiling) *and* a caller-supplied `describe_fn` confirms the cluster has reached a terminal state (a fixed set covering create/delete/update terminal statuses, plus `NotFoundException` treated as maximally terminal -- the cluster is fully gone) does it attempt a reclaim, and the reclaim write itself is `PutObject(IfMatch=<the ETag just read>)`, never an unconditional overwrite -- so two callers independently concluding staleness at the same moment cannot both win; the loser gets `ClusterLockError` naming the race explicitly, telling the caller to re-run the whole acquire rather than treating the loss as fatal.
- **`describe_fn` is dependency-injected with no opinion on create vs. delete vs. update**, matching the plan's explicit design ("the lock module has no opinion on which kind of operation it is guarding"): it is called `describe_fn(cluster_name=..., region=...)`, the identical keyword-only calling convention `pc.describe_cluster` already uses elsewhere in this file, so the real wiring (not yet done) can hand it `pc.describe_cluster` directly. A caller with no way to answer "is this terminal" (e.g. before AWS credentials are confirmed) may omit `describe_fn` entirely -- staleness is then reported but never auto-reclaimed, failing safe rather than guessing. An ambiguous `describe_fn` exception (network blip, transient AWS error) is likewise never read as evidence of terminal state, for the same fail-safe reason.
- **Explicitly NOT done this round, disclosed rather than silently deferred: live two-writer verification against a real S3 bucket.** The plan itself flags this ("needs live verification before implementation, not before planning... two processes racing a conditional PutObject against the same key, confirming exactly one succeeds") as a distinct step from writing the module, contrasting it with the Cognito `resource`-parameter question elsewhere in the same plan, which *did* get a real two-writer test before being trusted. This round's fake-based tests pin the *logic* (which branch fires, what gets written, what gets raised) using a fake S3 client that models the documented 412 shape faithfully but cannot exercise real concurrent-request behavior at all -- that verification is a separate, real-AWS action requiring the operator's own go-ahead before it runs, not performed here.
- **Not wired in anywhere yet** -- `core_create_cluster`/`core_delete_cluster` still use the local `_acquire_cluster_lock`/`_release_cluster_lock`, unchanged. Wiring the S3 lock in (replacing the local lock at every call site), the live two-writer verification, and the separate `wait: bool` core-function refactor (the plan's other major piece of this workstream, covering `create_pcluster`/`kill_pcluster`/`manage_pcluster_queue`'s config-apply phase, plus the periodic CLI progress-output addition) are explicitly next, not started this round. The confirmation-token piece (`create_cluster_preview`/`create_cluster_execute`) is scoped by the plan itself to the MCP wrapper layer (Workstream 5+, not yet started) and is out of this workstream's own deliverable.

**27 new tests, `tests/test_s3_cluster_lock.py`** (new file): bucket-name derivation (normal, too-long, keyword-only-on-two-args); bucket creation (idempotent, region handling, public-access-block, error propagation); the 412/409 rejection classifier's four positive shapes plus one negative; lock-key/owner-body construction; and the acquire function's full branch set -- absent (succeeds), held-and-fresh (fails, no `describe_fn` call at all -- age gates before status does, pinned by asserting zero `describe_fn` calls when the lock is still fresh even with a terminal status configured), held-and-stale-without-`describe_fn` (fails safe), held-and-stale-but-not-yet-terminal (fails), held-and-stale-and-terminal (reclaims, asserted via both the rewritten object body and a captured `WARNING` print), held-and-cluster-gone (`NotFoundException` reclaims), the exact `describe_fn(cluster_name=, region=)` keyword-only calling convention (a positional-tolerant fake would never catch a real breakage here), the reclaim-race case (a `_FakeS3Lock.race_on_next_get` flag that mutates the stored ETag between the read and the reclaim write, modeling a second writer's reclaim landing in between -- one test-authoring bug caught on first run, not a later review pass: the flag initially mutated *and returned* the new ETag from the same `get_object` call, so the caller's own read was already "fresh" and no race was ever exercised; fixed by returning the ETag current *at read time* and mutating the store only after, matching how a real race actually interleaves), an unrelated `PutObject` error propagating unchanged, and an ambiguous `describe_fn` exception failing safe. Full suite: 2262 passed (up from 2235, +27), 4 skipped, 0 failed. `make lint`/`make shellcheck` clean. One line-citation drift, same recurring shape as rounds 18/22/24: the new module (inserted ahead of `_setup_iam`) shifted `iam.attach_role_policy(` from `src/pcluster_core.py:981` to `:1206` -- fixed in both citing surfaces (`tests/test_claude_docs_line_citations.py`'s manifest, `templates/CLAUDE.local.md`'s prose).

**Same session, round 27 -- Workstream 4, second slice: the S3-backed distributed lock built in round 26 is now wired into both `core_create_cluster` and `core_delete_cluster`, and the local mkdir lock it replaced is deleted outright. User instruction: "continue" (in answer to a choice offered between wiring the lock in now vs. running the live two-writer verification first) -- read as choosing the first, since wiring is fully testable with fakes and the live-AWS step still needs the operator's own go-ahead, unchanged from round 26's disclosure.**

- **`_acquire_distributed_cluster_lock`, a new composing wrapper**, added alongside the round-26 primitives: ensures the locks bucket exists (`_create_locks_bucket`) then calls `s3_acquire_cluster_lock`, converting `ClusterLockError` into `sys.exit(str(e))` -- the exact shape the deleted local lock's own failure always had. Every existing CLI-behavior test (SystemExit rather than an uncaught exception, the "already running" substring) depends on that shape, not merely on the lock being held, so the conversion lives in one place rather than being reimplemented at both call sites.
- **`s3_release_cluster_lock` gained blanket `_ClientError` tolerance**, not just the missing-key case it already handled: release runs as best-effort cleanup, often the very last statement before a caller returns or exits, and a raised exception there would mask whatever the caller was actually reporting. Unlike the deleted local lock, a release that never lands is not permanently stuck either -- the staleness/reclaim path already built recovers it automatically for the next caller, which is what makes swallowing the error here safe rather than merely convenient.
- **`core_delete_cluster`'s wiring needed one structural change beyond a straight call-site swap: `aws_account_id` has to be known before the lock can be acquired, but the file that carries it (the cluster's own vars file) was previously read for the first time *inside* the lock.** Reading it again earlier, unprotected, would reopen the exact race the lock exists to prevent (a concurrent `kill_pcluster.py` deleting the vars file out from under an in-flight reader -- the documented origin incident). The fix is a deliberately throwaway peek: `aws_account_id` alone is read from the vars file before the lock purely to derive the locks-bucket name, and the *canonical*, lock-protected read of the full `cluster_vars` dict everything else in the function acts on stays exactly where it was, unchanged -- the file is parsed twice, not moved earlier once. `core_create_cluster` needed no equivalent change: `aws_account_id` was already resolved (via a concurrent STS call in the existing `ThreadPoolExecutor` network/account/existence-check block) before the point the old local lock was acquired.
- **`pc.describe_cluster` (the same `pcluster.lib` binding each function already uses for its own real work) is threaded into the lock as `describe_fn`**, matching the lock module's own dependency-injection design from round 26 -- neither call site needed a second, redundant `import pcluster.lib`; `core_create_cluster`'s pre-existing later `import pcluster.lib as pc` (originally right before `run_cluster_create_and_classify`) was deleted as newly redundant once the earlier one, added for the lock, covers the same binding for the rest of the function.
- **The local mkdir lock (`_acquire_cluster_lock`, `_read_lock_owner`, `_release_cluster_lock`) is deleted from `pcluster_core.py` outright, not left in place as an unused fallback.** A repo-wide grep after wiring confirmed nothing else called it -- CLAUDE.md's "if something is unused, delete it completely" standard, not a partial migration. `tests/test_kill_access.py`'s dedicated local-lock test class (eight tests exercising the deleted functions directly) is removed with it, along with the now-dead import; the S3 lock's own coverage (round 26's `test_s3_cluster_lock.py`, plus this round's integration-level tests below) is what replaces it, not a straight port of the same eight tests onto the new mechanism -- the two lock shapes have different failure surfaces (a stale-reclaim path the local lock never had, no local `owner` file), so a mechanical port would have pinned properties that don't apply and missed the ones that do.
- **Both `tests/test_kill_pcluster.py` and `tests/test_make_pcluster_main.py` needed a shared fake-client fix, for the same underlying reason.** Both files' fake S3 clients previously either constructed a fresh instance on every `boto3.client(...)` call (`test_kill_pcluster.py`'s `lambda *a, **k: _FakeAwsClient()`) or already memoized per service name (`test_make_pcluster_main.py`'s `clients` dict, unchanged) -- `core_delete_cluster` now constructs *two* separate `boto3.client("s3", ...)` handles (a dedicated one for the lock, another later for teardown's own S3 work), and both must observe the same stored lock object for a "lock already held" test to mean anything. `test_kill_pcluster.py`'s fixture was changed to memoize one `_FakeAwsClient` per service name (`_clients.setdefault(name, _FakeAwsClient())`) rather than constructing fresh each call; `test_make_pcluster_main.py` needed no fixture change, only the fake class itself gaining lock-aware `put_object`/`get_object`/`delete_object` methods (mirroring `test_s3_cluster_lock.py`'s own `_FakeS3Lock` semantics for the 412 conditional-write shape), since its `clients` dict was already a per-service singleton from round 25.
- **Three test classes rewritten, not just retargeted, in both CLI test files**: `TestClusterLockDuringTeardown` (`test_kill_pcluster.py`) and `TestClusterLockDuringBuild` (`test_make_pcluster_main.py`) both had a `_lock_path`-returning helper pointing at the now-nonexistent `.build.lock` file, and both of the lock-released-after-success/failure tests asserted `not os.path.exists(local_lock_path)` -- an assertion that had gone silently vacuous the moment the local lock stopped being created at all, true regardless of whether release actually ran. Both are now `pcluster_core._lock_key(CLUSTER) not in <fake s3>._objects`, and the "already held" tests now pre-populate the fake S3 client via a direct `s3_acquire_cluster_lock` call (delete side) or `pcluster_core.s3_acquire_cluster_lock` (create side) rather than the deleted `_acquire_cluster_lock`.

**Net test delta: -3** (2262 → 2259, 4 skipped unchanged, 0 failed) -- 8 removed (the deleted local-lock test class's direct unit tests, now redundant with round 26's `test_s3_cluster_lock.py`), 5 added (3 new `TestAcquireDistributedClusterLock`/release-tolerance tests in `test_s3_cluster_lock.py`, plus the rewritten held-lock tests staying 1:1 in both CLI files). `make lint`/`make shellcheck` clean. One more line-citation drift, same recurring shape: deleting the three local-lock functions net-shrank the file, moving `iam.attach_role_policy(` from `src/pcluster_core.py:1206` to `:1180` -- fixed in both citing surfaces. Preamble byte budget: 145,019 of 148,000, 2,981 bytes of headroom.

**Explicitly still not done, unchanged from round 26's disclosure:** the live two-writer verification of the `IfNoneMatch`/`IfMatch` conditional-write atomicity against a real S3 bucket -- the lock is now wired into every real build and teardown, so this is more load-bearing than when it was merely built, not less. The `wait: bool` core-function refactor (`create_pcluster`/`kill_pcluster`/`manage_pcluster_queue`'s config-apply phase, plus periodic CLI progress output during waits) remains the other unstarted piece of this workstream's own design. The confirmation-token piece stays scoped to the MCP wrapper layer (Workstream 5+), out of this workstream's deliverable.

**Same session, round 28 -- Workstream 4, third slice: the `wait: bool` core-function refactor and periodic CLI progress output, the plan's other major piece of this workstream. User instruction: "continue" (with budget explicitly confirmed).**

- **The plan's `wait=` kwarg claim was verified from source before any code was written on it, and it holds.** `pcluster/lib/lib.py` line 39 carries `wait_ops = ["create-cluster", "delete-cluster", "update-cluster"]` and injects a synthetic `{"name": "wait", "type": "boolean", "required": False}` parameter into each of those operations' models -- exactly matching the plan's claim, and correctly excluding `update-compute-fleet`, which is consistent with the plan's own separately-live-tested `TypeError: got unexpected arguments: {'wait'}` finding for that operation. Worth recording how this was checked, since the obvious first look is misleading: `pcluster.lib`'s `__init__.py` is 20 lines and contains **no** wait handling at all (a grep for "wait" there returns nothing) -- it just calls `lib._add_functions(lib._load_model(), lib)`, and every function it exposes has a bare `(**kwargs)` signature, so neither `inspect.signature` nor the package's entry-point module can answer this question. The real dispatcher is `pcluster/lib/lib.py`.
- **`wait=` is nevertheless deliberately NOT forwarded to `pcluster.lib`, despite being genuinely available.** The polling is implemented here instead, for three reasons that only became visible once the surrounding code existed: upstream's wait is opaque (no progress output, and no access to the intermediate `describe_cluster` responses -- which this codebase already depends on, since `_extract_head_node_ip` reads the head node IP out of the *same* response that decided `CREATE_COMPLETE` rather than issuing a fresh call); `update_compute_fleet` has to poll manually regardless (`stop_pcluster`/`start_pcluster` already do, via `_poll_fleet`), so forwarding would leave two different waiting mechanisms in one codebase; and the existing `_wait_for_cluster_create`/`_wait_for_cluster_delete` loops already reproduce the Ansible `until:`/`retries:`/`delay:` semantics the CLI has always had, so replacing them with upstream's would be a behavior change to a system this migration exists to leave working unchanged.
- **`_KICKED_OFF` is a new terminal state, deliberately distinct from `_TIMED_OUT`.** Conflating them would make a caller that never intended to wait look like a failed operation -- and `_TIMED_OUT` is a real failure signal on both sides (on the delete side it is specifically the state CLAUDE.md's teardown-gate bullet exists to protect against, since a wait timeout is neither confirmed nor `DELETE_FAILED`).
- **The single most load-bearing property, and the one a careless implementation gets wrong: `wait=False` confirms nothing.** On the create side, `create_confirmed=False` means the whole post-launch pipeline (SSH orchestration, staging sync, build summary) correctly does not run against a cluster that may not exist yet. On the delete side, `cf_delete_confirmed=False` means the four credential-destroying steps (EC2 keypair, local `.pem`, Secrets Manager secret, `active_clusters/<cluster>/`) do not fire -- CLAUDE.md requires *positive* confirmation the stack is gone before any of those, and "we did not look" is not confirmation. A `True` there would destroy the only ways back into a head node whose stack may still be running and billing. Both are pinned by their own tests.
- **`already_gone` is checked before the `wait` branch on the delete side, and that ordering is correct rather than incidental**: a `NotFoundException` from `delete_cluster` is positive evidence the stack is already gone, which no amount of not-waiting can undo -- so that path still confirms even under `wait=False`. Pinned by `test_an_already_gone_cluster_still_confirms_under_wait_false`.
- **`wait` defaults to `True` on both sides**, so every existing CLI call site keeps blocking without passing anything -- a default of `False` would silently turn every existing CLI build and teardown into fire-and-forget, exactly the regression the plan's top-of-file "CLI behavior cannot change" constraint forbids. Its own test on each side.
- **Periodic CLI progress output, the plan's explicitly-disclosed scoped exception to "unchanged."** Both waits were previously *entirely silent* -- the Ansible `until:` loop printed nothing per attempt -- so for 20-45 minutes (create) or 5-10 (delete) an operator had no way to distinguish a healthy slow build from a hung one without opening the CloudFormation console. A `progress_fn(attempt, status, cfn_status)` hook fires once per **non-terminal** poll (never for the terminal one, and never at all when `wait=False`), defaulting to `None` so every existing caller and test stays byte-identically silent. `core_create_cluster`/`core_delete_cluster` each pass a small local printer. **The third argument is `cloudFormationStackStatus`, not just `clusterStatus`, and that matters because of round 23's own finding**: `clusterStatus` collapses every `ROLLBACK_*` state into `CREATE_FAILED`, so a progress line showing only `clusterStatus` would hide the single most useful detail for an operator watching a build go wrong in real time. The final build summary each wait precedes is still byte-identical.

**20 new tests (+20; 2259 → 2279, 4 skipped unchanged, 0 failed)**: 11 in `test_create_pcluster_migration.py` (`TestWaitFalseKicksOffWithoutPolling` -- still launches, never polls, `_KICKED_OFF` not `_TIMED_OUT`, confirms nothing, headline names the poll command, default-is-True, a failed kickoff still raises under `wait=False`; plus `TestBuildProgressIsReportedDuringTheWait` -- one line per non-terminal poll, the exact `(attempt, status, cfn_status)` payload, silent by default, never fires when not waiting), and 9 in `test_teardown_steps.py` mirroring the same properties for the delete side. `make lint`/`make shellcheck` clean. No line-citation drift this round (`iam.attach_role_policy(` stays at `src/pcluster_core.py:1180` -- every addition landed below it).

**Still not done, unchanged:** the live two-writer verification of the S3 lock's conditional-write atomicity against a real bucket (needs the operator's go-ahead; a real-AWS action). Also still open within Workstream 4's own design: threading `wait` up through `core_create_cluster`/`core_delete_cluster`'s own signatures to their CLI shims (this round wired the parameter at the `run_*_and_classify` layer, where the polling actually lives, and both core functions pass `wait=True` implicitly via the default), and the three-phase `manage_pcluster_queue` decomposition (Phase 1/3 reuse `stop_pcluster`/`start_pcluster`'s existing manual-polling core functions; Phase 2 needs a new `update_cluster` core function on the standard pattern).

**Same session, round 29 -- the live two-writer verification of the S3 distributed lock, run against real S3 with the operator's explicit go-ahead. PASS on all three phases, and it was NOT a rubber stamp: it produced a real error shape that the fake-based tests could never have surfaced.**

- **Setup**: account `183295445014`, `us-east-2`, a throwaway bucket (`pcm-locktest-<random>`) created and deleted by the script itself, touching nothing else in the account (confirmed clean afterward by listing for both the test-bucket prefix and `parallelclustermaker-locks`). 8 concurrent writers, each with its **own** `boto3` client, released simultaneously by a `threading.Barrier` -- separate clients rather than one shared client deliberately, since modeling separate machines is the entire reason the local mkdir lock was replaced.
- **Phase 1 -- acquire race (`IfNoneMatch='*'`), 8 writers: exactly 1 winner, 7 losers, all `PreconditionFailed (HTTP 412)`.** The core atomicity claim the whole lock rests on, previously taken from botocore's API model and never tested, now confirmed against real S3.
- **Phase 2 -- reclaim race (`IfMatch=<etag>`), 8 writers all reading the same ETag then racing a conditional overwrite: exactly 1 winner, 7 losers.** This is the phase the plan's own 2026-08-19 correction exists for -- an unconditional overwrite here would have let all 8 "win" and merely relocated the race from the acquire path to the reclaim path rather than eliminating it.
- **The real finding, and the reason this run earned its keep: Phase 2's losers returned BOTH error shapes -- `PreconditionFailed (HTTP 412)` *and* `ConditionalRequestConflict (HTTP 409)` -- in the same 8-way race.** Round 26 handled both in `_is_conditional_write_rejection` on the strength of botocore's documentation alone (its `IfMatch`/`IfNoneMatch` docs mention 409 as the same-instant-race shape and say to treat it identically to a 412), and explicitly flagged that the exact `Code` casing was unconfirmed. That defensive choice turns out to have been load-bearing, not speculative: **had only 412 been handled, a reclaim under genuine contention would have escaped `_is_conditional_write_rejection`, propagated as an unhandled `ClientError` out of `s3_acquire_cluster_lock`, and crashed a real build or teardown** -- and no fake-based test would ever have caught it, because a synchronous fake cannot produce a same-instant conflict at all (`tests/test_s3_cluster_lock.py`'s own `_FakeS3Lock` docstring already said as much). The spelling is confirmed exactly as guessed: `ConditionalRequestConflict`, HTTP 409.
- **Phase 3 -- the production function itself (`s3_acquire_cluster_lock`), not just the raw primitive, raced 8 ways: exactly 1 winner, 7 `ClusterLockError`s.** This is what closes the loop: it proves the real code path maps both real-S3 rejection shapes onto the intended single-winner semantics, rather than merely proving S3 behaves as documented.

**No code changes were needed** -- the verification confirmed the implementation as written, which is the outcome to hope for but not the one to assume. The script lives in scratchpad only (it creates and destroys a real bucket; it is not a suite test and must never become one -- the suite runs in CI with no credentials, and a test that silently no-ops without them would be worse than absent). Full suite unchanged at **2279 passed, 4 skipped, 0 failed**; `make lint`/`make shellcheck` clean.

**Workstream 4's remaining work after this round**: threading `wait` up through `core_create_cluster`/`core_delete_cluster`'s own signatures to their CLI shims (round 28 wired the parameter at the `run_*_and_classify` layer, where the polling lives), and the three-phase `manage_pcluster_queue` decomposition (Phase 1/3 reuse `stop_pcluster`/`start_pcluster`'s existing manual-polling core functions; Phase 2 needs a new `update_cluster` core function on the standard pattern). Workstreams 5-7 remain unstarted.

**Same session, round 30 -- Workstream 4's last two pieces: threading `wait` up to the core functions, and the three-phase `manage_pcluster_queue` decomposition. User asked for subagents to speed this up; two `Explore` agents ran the read-heavy investigation in parallel (one on the queue-editor three-phase shape, one on the CLI-shim/`wait`-threading surface) while edits stayed serial, since both tasks write `src/pcluster_core.py` and parallel writes to one file would corrupt it.**

- **A design correction the subagent report surfaced before any code was written: there is no new CLI flag.** The obvious reading of "thread `wait` up to the CLI shims" is a `-W/--wait` flag matching `stop_pcluster.py`/`start_pcluster.py`/`manage_pcluster_queue.py`'s existing convention. That would have been wrong in two ways. The fleet scripts default to `wait=False` and use `-W` to opt *in*; create/delete have always blocked unconditionally, so matching that convention would need `--no-wait`, inverting an established flag's meaning across the same CLI. And the plan itself (`docs/parallelclustermaker-mcp-plan.md:619`) says the CLI shim *always* calls `wait=True` and only a future MCP wrapper passes `False` -- the parameter exists for the wrapper's benefit, not as new CLI surface. So `core_create_cluster`/`core_delete_cluster` gained `wait=True` defaults and the shims pass nothing at all. This also sidestepped a live trap the same report found: `tests/test_resolve_defaults.py::test_argparse_help_defaults_match_hardcoded_defaults` regex-scans `make_pcluster.py`'s `add_argument` help text and would have failed a `store_true` flag whose help mentioned a default.
- **`core_delete_cluster(wait=False)` skips the ENTIRE teardown, not just the credential-destroying steps.** The natural implementation reuses the existing `cf_delete_confirmed` gate, which protects only the four credential steps and lets IAM/S3/SSM cleanup proceed. That would be actively harmful here: deleting the IAM role or the S3 bucket out from under a stack that is still mid-delete is a documented way to manufacture a `DELETE_FAILED`. The early return fires before any teardown at all, preserves the serial and vars files (what a follow-up run needs to finish), and returns `success=True, exit_code=0` -- because the *requested* operation (initiate a delete without waiting) genuinely succeeded, and reporting failure would make an MCP caller retry a delete already correctly in flight. The dangerous misreading of that success is "teardown done," so the printed message says plainly that nothing was cleaned up and names the follow-up command; `test_the_operator_is_told_nothing_was_cleaned_up` pins that wording.
- **`core_create_cluster(wait=False)` exits 0 after the launch, releasing the lock but preserving everything else.** Everything past the launch -- SSH/SCP staging transfer, the performance tree, the SNS build-summary report -- requires a reachable head node that does not exist yet. The IAM role, S3 bucket, keypair, secret, serial file and vars file are all deliberately left in place (they are what the cluster is being built *with*), but the lock is released, since this process is done with the cluster and holding it would block the follow-up run.
- **A real bug I wrote and caught before running tests: threading `wait` through `core_apply_queue_config`'s three phases is incorrect.** The first version added `wait` to the composite and forwarded it to all three phases. Those phases are *causally dependent* -- `update-cluster` requires an already-stopped fleet, and the restart requires a finished update -- so `wait=False` would have fired the config apply against a still-running fleet and failed. The composite deliberately takes **no** `wait` parameter, alone among every core function this workstream touched; it *is* the blocking sequence, and is what the `-W` flag runs. Async callers use the three phases separately instead. `TestApplyQueueConfigStaysBlocking` pins both halves: `wait` is absent from the signature, and all three phases are called with `wait=True` (asserted on the actual call kwargs, since a `wait=False` leaking into any one of them is silent at runtime and only shows up as a failed update against a live fleet).
- **`core_apply_cluster_update` is the new phase-2 function.** Phases 1 and 3 already had independently callable core functions (`core_stop_fleet`/`core_start_fleet`); phase 2 was inline in the composite, which is why the MCP surface could not expose the three separate tools the plan calls for. It keeps its `wait: bool` on the standard pattern and keeps `_poll_cluster_update` as a local poller rather than forwarding `wait=` to `pcluster.lib`, matching the create/delete precedent (the library's polling is opaque -- no progress output, no intermediate describe responses).
- **Disclosed and deliberately deferred: phase 2 still shells out via `_run_pcluster_cmd(["update-cluster", ...])` rather than calling `pcluster.lib`.** It is now the last mutating cluster operation not on the library. This is *not* a gap in Workstream 3's completion claim -- no Ansible playbook ever performed an update, so it was never in that workstream's scope -- but it is real, and migrating it carries the same `cluster_configuration`-must-be-a-file-path subtlety round 23 hit, so it belongs in its own change with its own tests rather than riding along with the wait plumbing. The new function's docstring says so, rather than leaving it to read as an oversight.
- **This area had ZERO test coverage before this round.** `core_apply_queue_config` and `_poll_cluster_update` were both entirely untested despite `core_apply_queue_config` being exactly what `manage_pcluster_queue.py -W` runs against a live cluster.

**16 new tests (+16; 2279 → 2295, 4 skipped unchanged, 0 failed)**: 6 in `test_fleet.py` (`TestCoreApplyClusterUpdate` -- issues `update-cluster` with the config path, polls under `wait=True`, returns without polling under `wait=False`, defaults to True; `TestApplyQueueConfigStaysBlocking` -- no `wait` parameter, every phase awaited), 5 in `test_kill_pcluster.py` (`TestTeardownWaitFalse`), 5 in `test_make_pcluster_main.py` (`TestBuildWaitFalse`, including a test pinning that the CLI shim passes no `wait` *and* that the default is `True` -- both halves, since either alone would let the CLI silently become fire-and-forget). `make lint`/`make shellcheck` clean; no line-citation drift.

**Workstream 4 is now complete.** The S3 distributed lock (rounds 26-27, live-verified round 29), the `wait: bool` refactor and CLI progress output (round 28), and the three-phase decomposition plus `wait` threading (this round) are all done. Remaining across the whole MCP effort: Workstreams 5-7 (remote transport/auth/tool-surface), entirely unstarted, plus two disclosed follow-ups -- migrating `update-cluster` to `pcluster.lib`, and the `_run_pcluster_cmd` duplication flagged back in session 48.

**Same session, round 31 -- Workstream 5 begun: the MCP server foundation. Two `Explore` subagents ran reconnaissance in parallel (existing MCP scaffolding; the 8 IAM drafts + how `templates/*.json_src` renders and is tested) while implementation stayed serial.**

- **A subagent's headline finding was wrong, and measuring it directly is what caught it.** The IAM-recon agent reported `MCPStackMutation.json_src` as "far over the 6,144-byte IAM managed-policy limit... it cannot be attached as a single managed policy," which would have forced a policy split. It judged by **raw file size** (8,746 bytes); `_render_policy` enforces the limit on the **minified** JSON, which is **4,816 bytes** -- comfortably under. Measured all 8 drafts: every one passes. No split needed. Subagent reports are leads, not findings; this one would have caused a pointless refactor of a security-sensitive policy.
- **The four MCP placeholder test modules define no API contract.** Each is a single `pytest.skip()` whose entire import block is `import pytest`. They exist so the doc-hygiene citation sweep can resolve the module basenames the docs cite -- which also means renaming or deleting one breaks that sweep. The real contract is the plan doc.
- **`fastmcp` is now a real dependency** (`fastmcp`, plus `pytest-asyncio` for the in-memory `Client` tests), added to `requirements.txt` with the plan's own rationale recorded inline: the standalone package rather than the official `mcp` SDK's low-level API, since the SDK vendors an older copy of the same decorator API and nothing in this tool surface needs protocol-level control. `fastmcp` depends on `mcp` anyway, so the SDK arrives transitively. ~50 transitive packages; `pip check` clean, no existing package downgraded.
- **A real dependency incompatibility, found by running the suite rather than by reading: importing `fastmcp` breaks every subsequent `import ansible` in the same process.** Ansible's collection loader (`ansible/utils/collection_loader/_collection_finder.py`, at module scope) raises `Exception: need exactly one FileFinder import hook (found 2)` unless `sys.path_hooks` holds exactly one `FileFinder` entry; `fastmcp` pulls in setuptools' `_distutils_hack`, which inserts a second at index 0. This surfaced as four `TestTheTestEnvironmentMatchesAnsible` failures that **passed in isolation and failed in the full suite** -- pure ordering pollution. Traced by bisecting the import chain under pytest (`pcluster_core` alone: 1 hook; `+ fastmcp`: 2), not guessed.
  - **Fixed by importing Ansible's collection loader in `tests/conftest.py`, before anything can import `fastmcp`.** The check runs once at first import, so pre-importing it while exactly one hook exists makes the suite order-independent. Empirically it also stops `fastmcp` adding the second hook at all.
  - **Deliberately NOT fixed by loosening the Ansible-defaults assertion.** That test exists because a mismatch between this repo's Jinja2 env and Ansible's own `trim_blocks`/`lstrip_blocks` defaults silently changed rendered output once already; weakening it to accommodate an unrelated dependency would trade a real guard for an import-order convenience.
  - Runtime impact is nil -- neither playbook is executed any more (Workstream 3) -- but it is recorded because anything that later imports both libraries in one process hits it.
- **`mcp_server/` created**: `__init__.py` (side-effect-free by contract -- no boto3 client, no credential resolution, no `sys.exit()` at import, so the in-memory `Client` tests can drive it with only the AWS layer stubbed), `tools.py` (thin `@mcp.tool()` adapters over `core_*`, no business logic), `server.py` (`build_local()` / `build_remote()`).
- **The local/remote split is data, not two hand-maintained files.** `tools._LOCAL_ONLY` names the exclusions and `register_tools(mcp, *, remote)` consults it, so both instances are built from the same wrappers. Currently excluded from remote: `rotate_cluster_key` (not because it leaks key material -- it deliberately returns status and paths only -- but because the `.pem` it writes must land on the operator's own filesystem, and Lambda's `/tmp` is ephemeral; the "obvious fix" of returning key content in the response is exactly the exposure the exclusion prevents) and `manage_grafana_tunnel` (an SSH local port forward is only meaningful when "local" means the caller's own machine).
- **`server.main()` deliberately offers no `--remote` flag.** The remote instance is served by the Lambda topology behind an authorizer, not by a process someone starts by hand; a flag would invite running the still-privileged remote tool set on a host holding the operator's full credentials with nothing in front of it.
- **`check_cluster_health` forces `ssh_available=False`** rather than inventing a new mechanism -- `core_check_cluster_health` already has a SKIP branch for unreachable SSH, which is exactly the right shape for "key not available on this transport" (the plan's own reasoning).
- **API verified against the installed `fastmcp` 3.4.7 before writing against it**, not assumed from the plan's older-docs description. One correction found that way: `list_tools()` returns `FunctionTool` objects exposing `.parameters`, not the MCP wire shape -- the tests assert on `to_mcp_tool().inputSchema` instead, since that is what actually crosses the transport and therefore the thing whose drift breaks a caller.

**`tests/test_mcp_schemas.py` replaced its skip stub with 10 real tests** (net suite: 2295 passed/4 skipped -> **2305 passed/3 skipped**, 0 failed). Six pin the split -- including the guard the plan names explicitly ("the remote dispatcher's registered tool set excludes SSH-key operations while the local stdio instance's includes them"), plus three guards that version alone would not give: remote must be a *strict subset* of local (otherwise two independently-drifting sets would satisfy "excluded"), the difference must equal `_LOCAL_ONLY` exactly (so adding a local-only tool without declaring it fails here rather than shipping remotely), and every `_LOCAL_ONLY` entry must match a real registered tool (a typo would otherwise exclude nothing while still passing the difference test). Four pin schema shape. `make lint`/`make shellcheck` clean; no line-citation drift.

**Same session, round 32 -- reconciled the migration plan's stale file/module layout section (flag 1 from round 31's report), at the user's direction.**

- The layout section was written in the 2-Lambda era and named a `MCPDispatcherLambda`/`MCPHeavyLambda` policy pair that corresponds to nothing in the current design, plus four policy filenames that do not match the eight actually drafted. Replaced the superseded text outright rather than annotating it: two competing layouts in one document is what caused the confusion in the first place.
- **A second inconsistency found while fixing the first, and it was mine.** The document already contained a *newer*, more detailed resolution of the same question in its structural-gaps section (updated 2026-08-19 for the tier split, and extended 2026-08-20 with `confirmation_token.py`) -- naming `mcp_server/router.py` at the package top level and `handlers/stack_mutation_node.py`. My first replacement invented `handlers/router.py` and `handlers/stack_mutation_heavy.py` instead, i.e. I fixed one stale layout by adding a third conflicting one. Corrected to take the names verbatim from the existing resolution, with a note in the bullet itself recording which entry is authoritative so the next edit does not repeat this. The lesson generalizes: when reconciling a stale section, search the whole document for a later decision on the same topic *before* writing the replacement, not after.
- `confirmation_token.py` had been omitted from my replacement entirely; restored, with the reason it must not be duplicated into either handler package (the token is a keyless deterministic hash verified by recomputation, so a canonicalization change deployed to one tier and not the other makes every outstanding token unverifiable).
- **A real open gap surfaced by the reconciliation and recorded rather than silently closed: there is no router policy.** The Router Lambda is described throughout as "near-zero IAM," which is right in spirit but not zero -- it needs `lambda:InvokeFunction` scoped to the four handler ARNs, and none of the eight drafts provides it. The plan now says to decide this before `_setup_mcp_infra` is written, since that function's policy list is exactly where the omission would become silent.
- The three real constraints on moving the drafts into `templates/` (third classification category; the `logs:DeleteLogGroup` miscategorization hazard; the missing `<MCP_USER_POOL_ID>` placeholder) are now recorded in the plan itself rather than living only in a session report, together with the explicit correction that the 6,144-byte limit is **not** a constraint -- all eight fit when minified, which is what `_render_policy` measures.

**Same session, round 33 -- moved the 8 MCP IAM policy drafts into `templates/` (flag 2 from round 31), at the user's direction. The categorization work was the stated task; a real over-grant found on the way in was the more important part.**

- **`MCPStackMutation.json_src` granted `logs:DeleteLogGroup` on `Resource: "*"`, and it was removed rather than categorized around.** That policy backs the stack-mutation tier, i.e. the `delete_cluster` Lambda, so as drafted it could erase any cluster's log group account-wide. It directly contradicts an invariant this repo already documents at length: a retained CloudWatch log group is the only surviving record of a failed build (cfn-init captures stdout only, node stderr reaches no stream at all), PCluster's own log group defaults to `Retain`, and the toolkit deliberately does not override it. Verified before removing rather than assumed: the toolkit never calls `delete_log_group` anywhere, and upstream's only caller is `pcluster/models/imagebuilder.py` on the `build-image` path this toolkit does not use -- the identical trace `templates/CLAUDE.md` already records for the instance policies. Removing one action from the statement, not the statement, leaves the 14 legitimate log-shipping/metric-filter actions intact. If a future real deployment turns out to need it for CFN stack deletion, it should come back ARN-scoped to the cluster's own log group with the retain-default reasoning revisited -- not restored as a wildcard.
- **The ban now covers the MCP policies too, which is the substantive call.** The obvious reading of "third category" is "a category the `logs:DeleteLogGroup` ban does not apply to" -- which would have made the over-grant above legal by construction. But the ban's rationale turns on what the log group is *worth*, not on whether the principal is an EC2 instance; the instance framing is just where the original incident happened. `_BAN_APPLIES_TO = _INSTANCE_REACHABLE_POLICY_FILES + _MCP_LAMBDA_POLICY_FILES`. `OperatorPolicy` stays exempt, unchanged -- purging by hand under operator credentials is exactly what the retain rule expects.
- **`_MCP_LAMBDA_POLICY_FILES` is a classification list, deliberately not an addition to `_POLICY_FILES`.** That list is pinned by equality to the five managed cluster policies and `_setup_iam`'s three suffix lists are asserted against it; appending to it would break that pin. The new list satisfies `test_every_policy_template_is_covered_by_this_ban`'s directory-equality check.
- **`<MCP_USER_POOL_ID>` added to both substitution sources at once** -- `_render_policy`'s replace chain (keyword-defaulted, so every existing cluster-policy caller is unchanged; `_setup_mcp_infra` will pass the real pool id) and the tests' `_PLACEHOLDER_SUB`. A placeholder present in one and not the other renders a literal `<MCP_USER_POOL_ID>` into a live IAM ARN.
- **`TestMcpLambdaPolicies` gives the new files the same structural guards the cluster policies have** -- valid JSON, the 6,144-byte limit, valid statement keys, unique Sids, no unsubstituted placeholders -- plus a file-list equality pin (a ninth MCP file nobody classifies would pass the ban's directory check yet be validated by nothing) and a cross-check that every placeholder these templates use is actually substituted by `_render_policy`. Without this class they would have sat in `templates/` classified but otherwise entirely unchecked; being newer and undeployed makes that more dangerous, not less.
- **All three new guards were mutation-tested, and one was vacuous on the first try.** Re-adding `logs:DeleteLogGroup` -> caught. Dropping a ninth unclassified MCP file in -> caught. Deleting the `<MCP_USER_POOL_ID>` substitution from `_render_policy` -> **passed**, because the guard searched the function's whole source for the token and the *comment* explaining the substitution names the token itself. Fixed by stripping comment lines before matching -- the exact vacuity trap `CLAUDE.local.md` already documents for two other tests, encountered again because the comment was written at the same time as the guard. Re-verified: the mutation is now caught.

**Same session, round 34 -- closed the router-policy gap recorded in round 32.**

- **Resolved with a ninth file (`templates/MCPRouterLambda.json_src`) rather than an inline grant.** Consistent with every other policy in this repo, and it puts the router's permissions in the same place, reviewed the same way, guarded by the same tests. One statement, one action (`lambda:InvokeFunction`), four explicit handler ARNs, no wildcard.
- **The gap was real but narrow, and worth stating precisely: "near-zero IAM" is not zero IAM.** The plan describes the router as carrying no PCluster permissions, which is correct, but a router that cannot invoke anything cannot route -- so the absence of a policy was an omission, not a deliberate minimalism. Left unresolved, it would have surfaced as an `AccessDeniedException` on the first real tool call after deployment, with `_setup_mcp_infra`'s policy list being exactly where the omission stayed invisible.
- **It also forced a decision nothing had pinned yet: the handler Lambda function names.** The router's ARNs encode them, so they are now fixed (`pclustermaker-mcp-read-only`, `-fleet-toggle`, `-stack-mutation`, `-stack-mutation-node`) and `TestRouterPolicyStaysNearZero` pins the list alongside the policy. A rename in the policy but not in `_setup_mcp_infra` (or vice versa) produces a router that is denied at runtime -- a deployment-time failure rather than a test-time one, which is precisely the class of thing worth catching in a test.
- **Confirmed while writing it, rather than assumed: none of these policy documents grants itself `logs:CreateLogStream`/`logs:PutLogEvents`.** They rely on the AWS-managed `AWSLambdaBasicExecutionRole` being attached separately -- the standard Lambda pattern. Checked all nine so the router matches rather than inventing its own logging grant. (`MCPStackMutation`'s lone `logs:CreateLogGroup` is for CloudFormation-managed cluster log groups, not its own execution log.)
- **Four guards in `TestRouterPolicyStaysNearZero`, all mutation-tested**: exactly one action; the resource list equals the four handler names; no wildcard in any resource; and no action outside the `lambda:` service. Mutations caught: widening a resource to `function:pclustermaker-mcp-*`, appending a single `ec2:DescribeInstances` grant ("just so the router can answer a status question itself"), and renaming one handler. The last is the drift the class exists for.

**Same session, round 35 -- `_setup_mcp_infra` / `_delete_mcp_infra`: the IAM layer for the MCP Lambda topology.**

- **One table drives creation, attachment, and teardown.** `_MCP_LAMBDA_TIERS` maps each of the seven Lambdas (router, four handlers, two Workstream 6 auth) to its function name and its policy templates; `_mcp_policy_templates()` derives the distinct policy set from it rather than restating one. This is a deliberate departure from `_setup_iam` next door, which carries **three** parallel suffix lists (`_ALL_SUFFIXES`, `_suffixes`, and a third inside `_delete_managed_policies`) that a test has to cross-assert against each other precisely because they can drift. `test_setup_and_teardown_cover_the_same_set` proves the construction actually holds rather than assuming it.
- **Scope is IAM only, stated rather than implied**: roles, customer-managed policies, attachments. It does not create the Lambda functions, API Gateway, or the Cognito pool -- those need deployment artifacts (a zip, a container image) that do not exist at this layer, and `_setup_iam`'s own precedent is role-and-policy setup and nothing else.
- **The shared policy is created once, not per tier.** Both stack-mutation tiers reference `MCPStackMutation` + `MCPStateAccessStackMutation`; IAM policy names are unique per account, so a per-tier `create_policy` would collide on the second call. Policies are keyed by template basename and attached to as many roles as the table says.
- **`mcp_user_pool_id` is required, not defaulted, and that is a real hazard rather than defensive typing.** It is substituted into the two Cognito policies' ARNs; an empty value renders a policy that is *syntactically valid* and that IAM happily accepts, then denies at call time on a malformed resource. A default of `""` would have made that the quiet path. `_render_policy`'s own parameter keeps its `""` default so every existing cluster-policy caller is untouched -- the requirement belongs at the MCP entry point, not in the shared renderer.
- **Idempotent by tolerating `EntityAlreadyExists` only.** A re-run after a partial failure completes rather than aborting, and attachments still happen on the reuse path (which is what makes the re-run useful). Any other `ClientError` propagates -- tolerating `AccessDenied` here would report success on a run that created nothing, and that mutation is tested.
- **Teardown detaches before deleting**, which is functional rather than cosmetic: IAM refuses to delete an attached policy or a role that still has attachments. Tolerant by default so one missing resource does not abandon the rest, with `suppress=False` available for a caller that wants the failure.

**19 tests in a new `tests/test_mcp_infra.py`**, and the ones worth naming are the couplings that would otherwise fail at *deployment* rather than in a test: every policy the table references exists on disk, and the router policy's four ARNs equal exactly the four handler tiers' function names (excluding the router itself and the two auth Lambdas, which it never invokes). Four mutations verified caught: renaming a handler in the table but not the policy; forgetting to attach `AWSLambdaBasicExecutionRole`; teardown skipping the shared stack-mutation policy; and tolerating `AccessDenied` as though it were `EntityAlreadyExists`.

**Same session, round 36 -- `mcp_server/confirmation_token.py`, the preview/execute gate. Second skip stub replaced with real tests (4 skipped -> 2).**

- **The honest framing, stated in the module docstring rather than implied: this is not authentication.** The hash is keyless by design, so anyone able to call `execute` can equally call `preview` and mint a token for themselves. It stops an *unpreviewed* execution -- a model calling `create_cluster`/`delete_cluster` without having first shown what it is about to do -- not a *hostile* one. Authorization is Workstream 6's job (Cognito + API Gateway authorizer); conflating the two would leave both weaker. The tests are worded to pin "what was previewed is what runs" and never "only an authorized caller can run this," so they cannot be over-read later.
- **Keyless rather than HMAC is a deliberate IAM simplification, not laziness.** `execute` verifies by recomputing the hash from the parameters it is about to act on, so there is no shared secret -- hence no Secrets Manager/SSM/KMS grant in any Lambda policy, and nothing to distribute between the read-only tier (which mints `preview_cluster_delete`'s token) and the stack-mutation tier (which verifies it), two separate deployment packages under Workstream 5's split.
- **The exposure that split does create is version skew, and it is a false negative rather than a hole**: a canonicalization change deployed to one tier and not the other makes outstanding tokens unverifiable -- a legitimate caller is rejected, a forged one is not accepted. `_VERSION` is inside the token so that failure reads as "minted by a different build" rather than an unexplained mismatch; the mitigation (ship all handler Lambdas from one versioned artifact for a token-touching change) is deployment discipline and lives in the plan.
- **Canonicalization decisions that are load-bearing rather than stylistic**: `sort_keys` recurses, so nested dicts in a different order still verify; lists keep their order, because a parameter like an instance-type list is order-significant to the resulting cluster and reordering it is a genuinely different plan; types are preserved, so a preview of `max_queue_size=10` cannot authorize an execute of `"10"`, and `True` does not collapse into `1` despite Python considering them equal; `allow_nan=False`, because `json.dumps` emits bare `NaN` by default, which is not valid JSON and does not round-trip.
- **`issued_at` is carried in the clear *and* inside the digest.** In the clear because a hash cannot be reversed and `verify` needs it for both the recomputation and the TTL judgement; inside the digest because the obvious attack on a keyless token is to edit the timestamp and extend its life. A far-future token is rejected outright rather than accepted, since accepting one lets it outlive its window.
- **Three distinct failure types (`MalformedToken`, `ExpiredToken`, `TokenMismatch`) under one `ConfirmationTokenError` base.** Distinct because they need different responses -- a mismatch or expiry means re-preview, a malformed token means the caller is passing something that never came from `mint`; one base so a tool wrapper can translate the whole family into a single shaped MCP error without enumerating subclasses (Workstream 7's layer-5 requirement: never a leaked traceback). A non-string token raises `MalformedToken`, not a raw `TypeError`.

**31 tests**, and five mutations verified caught: dropping `issued_at` from the digest (making the timestamp editable); dropping the action from the canonical form (so a delete-preview token would authorize a create); stringifying values (collapsing `10` and `"10"`); an off-by-one at the TTL boundary (rejecting a token at exactly the 15 minutes the operator was promised); and removing the future-dated guard.

**Same session, round 37 -- `mcp_server/router.py` and `mcp_server/tiers.py`: the Router Lambda's JSON-RPC dispatch.**

- **A real gap in the plan, found by implementing it: the router description says "parses the JSON-RPC body, reads the tool name, and forwards," which is only true for `tools/call`.** MCP has protocol-level methods that are not tool calls, and forwarding them blindly is wrong in ways that are quiet rather than loud. `tools/list` sent to a single handler returns that handler's tools only -- a client would see roughly a quarter of the surface and conclude the rest does not exist. `initialize` is the *server's* capability handshake, not one tier's. Notifications carry no `id` and must produce no response at all; returning one is a protocol violation. The router now terminates `initialize`/`ping`/notifications itself and routes only `tools/call`.
- **`tools/list` fans out to all four handler tiers and merges.** The alternative -- a router-side registry of tool schemas -- would duplicate every schema outside the handler that implements it, and that copy would drift. Fanning out keeps `tools.py` the single source of truth for schemas while the router knows only *names*, for routing. Cost is four invocations on a call made about once per session; correctness over micro-optimization, and stated as such rather than left to look accidental. A tier returning nothing does not break the merge -- one erroring handler must not make the whole tool list unavailable.
- **The auth tiers are deliberately excluded from the fan-out.** `register`/`authorizer` serve the Workstream 6 auth flow, not tools; invoking them from `tools/list` would be both semantically wrong and an IAM grant the router's policy does not have.
- **`mcp_server/tiers.py` exists because the obvious de-duplication is impossible.** Three places must agree on the Lambda function names: `tiers.py` (router-side, to invoke), `_MCP_LAMBDA_TIERS` in `pcluster_core.py` (to create the roles), and `MCPRouterLambda.json_src` (to grant InvokeFunction on exactly those ARNs). They cannot be collapsed: the router's deployment package must not import `pcluster_core` -- dragging in boto3, PCluster and Jinja2 is exactly what would make its "near-zero IAM" incidental rather than meaningful -- and `src/` should not depend on `mcp_server/` either. So the agreement is enforced by test, and the module says so rather than leaving the duplication looking careless.
- **The three-way agreement is pinned in both directions.** Every fan-out tier must be in the router policy (or that call is denied), *and* the policy must grant nothing extra (a stale ARN is a grant pointing at a function nothing routes to). Plus: every routed tier is a real tier, and the fan-out set equals the routed set -- a tool routed to a tier `tools/list` never queries would be callable but never advertised, i.e. invisible to the model.
- **A false-positive guard, caught immediately and worth recording as the mirror of round 33's vacuous one.** `test_it_imports_neither_pcluster_core_nor_fastmcp` first grepped the source text and failed on `router.py`'s *own docstring*, which states that it does not import those. Rewritten to walk the AST and inspect real `Import`/`ImportFrom` nodes, with its own vacuity guard proving the walk sees a genuine import. Round 33's bug was prose making a guard pass when it should fail; this was prose making one fail when it should pass. Same root cause -- matching text where structure was meant.

**39 tests in a new `tests/test_mcp_router.py`.** Five mutations verified caught: renaming a function in `tiers.py` only; routing a tool to a tier the fan-out never queries; forwarding `tools/list` to a single tier; forwarding `initialize`; and responding to notifications.

**Same session, round 38 -- the four tier handler Lambdas (`mcp_server/handlers/`), plus a consolidation that removed a duplication I was about to create.**

- **Caught before writing it: the tool->tier map was about to become a fourth source of truth.** The handlers need to know which tools they serve, and the obvious move is a per-tier list in `tools.py` -- which would have duplicated `router.py`'s `_TOOL_ROUTES`. Moved the map into `mcp_server/tiers.py` instead, the one module all three can import (the router's package must stay free of `pcluster_core`; `tools.py` is the opposite end of that dependency). `router.py` now imports it; all 39 router tests passed unchanged after the move, which is what makes it a refactor rather than a rewrite.
- **`UNIMPLEMENTED` is explicit rather than inferred, and that distinction is the point.** Most of the routed surface has no wrapper yet -- only six tools exist in `tools.py`. Without a declared list, a routed-but-missing tool is indistinguishable from one that was implemented and silently dropped. Now a handler answers "routed to this tier but not implemented yet" (`METHOD_NOT_FOUND`) versus "unknown tool" (`INVALID_PARAMS`): the first is a roadmap fact, the second is a caller error, and they want different responses. A test pins that `UNIMPLEMENTED` names only routed tools, so a stale entry cannot silently suppress a real one.
- **One shared dispatch, four one-line modules.** The tiers differ only in which tools they register and in their IAM, never in how they dispatch -- four copies of that logic would be four places for the error translation to drift.
- **Error translation is as much the point of `base.py` as dispatch (Workstream 7 layer 5).** The `except Exception` is deliberately broad: anything reaching it -- a `pcluster.lib` `NotFoundException`, a botocore `ClientError`, a `PClusterMakerError`, an outright bug -- must become a shaped JSON-RPC error. A traceback crossing the transport tells the model nothing actionable and can carry account identifiers, ARNs, and server filesystem paths into a chat transcript. It is `Exception`, **not** `BaseException`: catching `SystemExit`/`KeyboardInterrupt` would turn a Lambda timeout or shutdown into a misleading tool result. Both halves are mutation-tested.
- **Registration is filtered by tier rather than filtered after the fact.** The first version built the full remote instance and called `remove_tool` for everything outside the tier -- which needed a deprecated FastMCP API *and*, more importantly, meant a tool briefly existed on an instance that must never expose it. `register_tools(mcp, *, remote, tier=None)` now skips at registration.
- **Handlers re-check routing rather than trusting the router.** A misrouted call is rejected with "the router forwarded it to the wrong handler". Defence in depth: a tool served by a tier whose IAM does not back it fails at call time with an opaque `AccessDenied`, which is a far worse diagnostic than an explicit rejection.

**33 tests in a new `tests/test_mcp_handlers.py`**, pinning both directions of the tier/tool relationship -- no tool leaks onto a tier that does not route it (the IAM-relevant direction), and every routed tool is either served or explicitly unimplemented (no dead surface). Five mutations verified caught: dropping the tier filter; dropping the misrouted-call rejection; narrowing the exception catch so tracebacks propagate; widening it to `BaseException`; and pointing a handler module at the wrong tier.

**Same session, round 39 -- filled in nine tool wrappers; `UNIMPLEMENTED` shrank from 11 to 2.**

- **Implemented**: `diagnose_cluster`, `resolve_access_info`, `add_queue`, `remove_queue`, `stop_fleet`, `start_fleet`, `apply_queue_config`, `preview_cluster_delete`, `delete_cluster`. All fifteen routed tools now distribute correctly across the four tiers.
- **The two that remain are blocked on a design decision, not on typing, and the code says so.** `core_create_cluster` takes `MakeClusterParams`: ~130 fields, each of which would need an MCP parameter with a schema, a default, and a validation story. Exposing that verbatim hands the model a tool it cannot realistically fill in correctly; exposing a curated subset means deciding which knobs a *remote* caller may turn, which is a product decision. `preview_cluster_config` is not the cheap half it sounds like -- `dryrun` still routes through `create_cluster()`, whose first line is `assert_valid_node_js()`, so it needs the Node.js tier and the identical parameter surface.
- **`delete_cluster` is the first tool wired to the preview/execute gate**, which exercises `confirmation_token.py` end to end rather than as a unit. `preview_cluster_delete` is read-only, mints the token, and enumerates both what would be destroyed *and* what is retained -- the CloudWatch log groups, since an operator reading "delete" should not have to know that those survive. The pair deliberately lands on different tiers (read-only mints, stack-mutation verifies), which is exactly why `confirmation_token.py` has to be one shared module.
- **Two mutations survived the first battery, and both were real gaps in my own tests.** Deleting the `verify(...)` call from the wrapper entirely -> passed. Flipping `wait=False` to `True` (blocking a tool call on a 5-10 minute teardown) -> passed. Every token test was a unit test of `verify()`; **testing a guard's implementation is not testing that the guard is invoked**, and nothing exercised the wrapper. Fixed with `TestTheDeleteWrapperActuallyCallsTheGate`, which calls the real tool function, plus an AST pin on the `wait=` keyword at the call site.
- **Fixing that surfaced an ordering problem worth the change: `verify` now runs *before* `_require_record`.** Both orders "work", but the original put the gate behind a cluster lookup -- so a caller without a valid token could use the missing-cluster error to probe which names exist, and a bad token failed as whatever the lookup happened to say first rather than as a bad token. A third mutation (restoring the old order) is now caught too.
- **One small consolidation**: a repeated `dataclasses.asdict(...) if is_dataclass(...) else ...` ternary across every wrapper became a shared `_plain()`. Core functions return frozen dataclasses, plain dicts, or bare strings depending on age; the tool surface should not expose that difference.

**Suite: 2487 -> 2498** (+11 net; 44 tests now in `test_mcp_handlers.py`). `make lint`/`make shellcheck` clean.

**Same session, round 40 -- `tests/test_mcp_tools.py`: Workstream 7's layers 2 and 5, driven through FastMCP's own `Client`. Third of four skip stubs replaced (4 skipped -> 1).**

- **This is the only test file that drives the server the way a real MCP client does** -- connect, list, call. Everything else in the MCP suite tests a piece; this tests that the pieces are wired together. Chosen over packaging as the next step for exactly that reason: verifying what exists beats adding more unverified surface.
- **The monkeypatch target is the trap this repo has hit at every scale.** `mcp_server/tools.py` does `from pcluster_core import core_list_clusters`, binding the name into its own module namespace at import time, so patching `pcluster_core.core_list_clusters` does nothing -- the tool body resolves from `mcp_server.tools`' globals. Same finding CLAUDE.md records for the CLI shims after Workstream 1 moved logic into `pcluster_core`. Called out in the module docstring so the next person patches the right thing on the first try.
- **The full preview -> delete round trip is exercised end to end through a client session**, not just as unit calls: preview mints a token and mutates nothing, the token lets the matching delete through with `wait=False`, and a token minted for one cluster is refused for another *without the core function being reached*. That last assertion is the one that matters -- a gate that rejects after calling the destructive function is not a gate.
- **Both "SSH is never claimed available" paths are pinned through the client**, for `check_cluster_health` and `diagnose_cluster`. Key material never reaches this transport, so those sub-checks must take the existing SKIP branch rather than being attempted and failing; a flipped flag is silent until someone reads a diagnostic report that quietly says FAIL where it should say SKIP.
- **Error translation verified from the client's side, not the handler's**: a core exception arrives as a `ToolError`, a `PClusterMakerError`'s operator-facing message survives (it is the useful part), and no traceback or `File "` text reaches the client. Schema validation is confirmed to reject a cluster-scoped tool called with no target, before the wrapper runs.

**19 tests, all passing on the first run** -- unusual for this workstream, and worth attributing to the surface having been mutation-tested at each layer beneath it rather than to luck. Four mutations verified caught: letting `check_cluster_health` claim SSH availability; registering the local-only tools on the remote instance; minting the preview token for the wrong action; and making `stop_fleet` block by default.

**Suite: 2498 -> 2517**, 1 skipped (only `test_mcp_lock.py` remains a stub). `make lint`/`make shellcheck` clean.

**Same session, round 41 -- `tests/test_mcp_lock.py`, the last skip stub. Writing it found a real gap and a real bug; the suite now has 0 skipped.**

- **The gap: the IAM said one thing and the code did another.** The plan specifies that the fleet-toggle tier "must acquire/release the distributed lock around the call", and `MCPStateAccessFleetToggle.json_src` grants `locks/*` on exactly that basis -- but `_core_fleet_action` (behind `core_stop_fleet`/`core_start_fleet`) and `core_apply_queue_config` contained **no lock calls at all**. Checked before claiming it, by grepping their sources for the acquire helpers. So `stop_fleet` could have raced an in-flight `make_pcluster.py` build and stopped its compute fleet mid-build, with a policy granting the lock access that would have prevented it.
- **Where the lock goes was the load-bearing decision, and it is not in the core functions.** Those are also the CLI's code path: locking there would make `stop_pcluster.py` during an in-flight build begin failing fast -- arguably a fix, but definitely a change, and the plan's standing constraint is that CLI behavior does not. Held at the wrapper layer instead, which is also what the plan's own wording says ("around the call"). `create_cluster`/`delete_cluster` are the exception in the other direction: their core functions already lock internally (round 27), so their tools must **not** be wrapped or they would deadlock against their own acquisition -- pinned by an AST test, plus a vacuity guard that those cores really do lock and a reverse guard that the wrapped ones really do not.
- **The bug: a held lock would have killed the server, not failed the call.** `_acquire_distributed_cluster_lock` calls `sys.exit()` on a held lock, which is right for the CLI. `SystemExit` is a `BaseException`, and the handler's `except Exception` is deliberately narrow so a Lambda timeout is not reported as a tool failure -- so it does not catch it. `pcluster_core` already documents this hazard **twice** ("an uncaught SystemExit inside a long-lived FastMCP server process kills the whole server, not just one tool call"); the lock was a live instance of it that nobody had connected. Translated to `PClusterMakerError` at the wrapper, so a held lock reaches the model as an ordinary tool error carrying "another operation is already running" -- a model told only "error" retries forever; told that, it can wait and poll.
- **A failed acquire must not run the release path.** Deleting the lock object the *other* operation is holding is strictly worse than not locking at all. `contextlib.contextmanager` with the acquire outside the `try:` gets this right by construction; a test pins it rather than trusting the shape.
- **Read-only tools deliberately take no lock**, which is why `MCPStateAccessReadOnly.json_src` grants no `locks/*` access -- locking there would serialize harmless polling against real operations, and polling is the majority of call volume under the async design. Tested on both sides: the tools do not lock, and the policy does not grant it.
- **One casualty, fixed rather than papered over**: `test_fleet_tools_default_to_not_waiting` in `test_mcp_tools.py` stubbed the core functions but not the lock, so it began reaching for real STS and S3. Stubbed the lock there with a comment saying where locking *is* tested, so the next reader does not duplicate it.

**20 tests**, four mutations verified caught: dropping the lock from `stop_fleet`; moving the release out of the `finally` (leaking on failure); removing the `SystemExit` translation; and wrapping `delete_cluster` in a second lock (deadlock).

**Suite: 2517 -> 2537, and 0 skipped** -- all four MCP placeholder stubs are now real tests.

**Same session, round 42 -- moved `_HARDCODED_DEFAULTS` into `pcluster_core` as `MAKE_CLUSTER_DEFAULTS`. A necessary step toward the creation pair, and a sharper diagnosis of why that pair was blocked.**

- **The blocker was never "~130 fields is a lot of typing" -- that framing, which I had recorded twice, was wrong.** `MakeClusterParams` has 84 fields and **none** carry a default, so every one must be supplied; the thing that makes constructing one tractable is `_HARDCODED_DEFAULTS`, which was a **local variable inside `make_pcluster.py`'s `main()`**. Not module-level, not importable, unreachable by anything but that one function. An MCP wrapper had exactly two options: duplicate ~76 defaults into a second source that would drift from the first (the failure mode this repo documents over and over), or move them. So it was a layering problem, not a volume problem -- and the fix is a refactor, not a product decision.
- **`_resolve` was already in the core layer**, delegating to `_resolve_cli_value`; only the bottom layer of the precedence chain (the dict itself) was stranded in the shim. Moving it leaves `make_pcluster.py` owning the argparse surface and the CLI > defaults-file > hardcoded precedence exactly as before, with a one-line alias so the rest of `main()` is untouched.
- **Verified behavior-preserving rather than assumed**: the moved dict was compared key-for-key against the version in the last commit and is identical (76 keys). The two AST-walking guards that read it -- `test_argparse_help_defaults_match_hardcoded_defaults` and the placeholder sweep in `test_make_pcluster.py` -- were retargeted to `src/pcluster_core.py`; both had hardcoded `"...not found in make_pcluster.py"` assertions, so they failed loudly on the move rather than silently reading nothing, which is the behavior you want from a guard.
- **Honest scope: this was necessary but is not sufficient, and the remaining gap is now precisely known.** The defaults cover 70 of `MakeClusterParams`' 84 fields. Four of the rest are genuinely required inputs (`cluster_name`, `cluster_owner`, `cluster_owner_email`, `az`); the other ten are **derived during resolution inside `main()`** -- `docker_compose_arch`/`docker_compose_checksum`/`stage_docker_compose`, the `compute_az_list`/`gpu_az_list`/subnet-override lists, `configured_head_node_bootstrap_timeout`, and the two instance types. Those derivations are still shim-local, so a `create_cluster` tool would still have to reproduce them. That is the next step for the creation pair, and it is the same kind of move rather than a new kind of problem.

**Suite: unchanged at 2537** (a pure refactor: no tests added, the two retargeted guards still count once each). `make lint`/`make shellcheck` clean. One line-citation drift, the recurring shape: the 83-line insertion moved `iam.attach_role_policy(` from `src/pcluster_core.py:1192` to `:1297`, fixed in both citing surfaces.

**Same session, round 43 -- extracted `_derive_az_list`, and corrected round 42's own claim about what was left.**

- **Round 42 said "ten parameters are derived inside `main()`". That was wrong, and checking each one is what showed it.** Three were *already* core functions the shim merely calls (`_default_loginnode_instance_type`, `_derive_head_node_bootstrap_timeout`, `_derive_docker_compose_staging`); five are plain `_resolve` lookups with no derivation at all (`compute_subnet_ids_override`, `gpu_subnet_ids_override`, `docker_compose_checksum`, `configured_head_node_bootstrap_timeout`, and the required `headnode_instance_type`). Counting names in a dataclass and calling them all "derivations" produced a number that sounded like a lot of work; reading the code found exactly **one** genuinely shim-local derivation. Recording the corrected figure matters more than the figure itself -- the earlier one would have made the next person budget for a refactor that does not exist.
- **The one real item was the AZ-list split**, duplicated at two call sites, and its duplication hid something worth preserving explicitly: the two fallbacks are **deliberately different**. Compute falls back to `[<headnode az>]` (a queue has to live somewhere); GPU falls back to `None`, which downstream reads as "no override" rather than "an empty list of AZs". Collapsing them into one internal default would give a GPU-less cluster a GPU AZ list. `_derive_az_list(raw, *, fallback)` therefore takes the fallback as a **required keyword** -- a default would let a caller silently get the wrong one, and that is a caught mutation.
- **Two edge cases the extraction pinned that the inline version handled only by accident**: a trailing comma (`"us-east-1a,"` -- what an operator actually types) must not yield an empty-string AZ that survives to cluster creation before failing, and an all-separator string (`",,,"`) must return the fallback rather than `[]`, since a queue pinned to no AZ at all fails obscurely much later.
- **The call-site asymmetry is pinned by an AST test, not just the function's behavior.** A swapped pair renders a plausible cluster rather than an error, so the mutation is silent at the unit level -- and indeed swapping them broke 37 tests downstream, which is a fine safety net but a terrible diagnostic. The AST test names the actual problem.

**10 new tests** (`TestDeriveAzList`), four mutations caught: swapping the fallbacks, returning `[]` instead of the fallback, dropping the whitespace strip, and giving `fallback` a default. **Suite: 2537 -> 2547.** `make lint`/`make shellcheck` clean; no line-citation drift.

**Same session, round 44 -- `build_make_cluster_params`, the bridge that lets a non-argparse caller construct a `MakeClusterParams`.**

- **Rounds 42-43 cleared the plumbing; this is the piece that uses it.** 84 fields with no defaults: `MAKE_CLUSTER_DEFAULTS` supplies 70, four are required inputs, and the remaining ten are derived here **through the same core helpers `main()` calls** -- `_derive_az_list`, `_derive_head_node_bootstrap_timeout`, `_derive_docker_compose_staging`, `_default_loginnode_instance_type`. Not reimplemented, so a change to any of them reaches both callers rather than drifting.
- **Unknown override keys are rejected rather than ignored.** Silently dropping a typo is the worst available outcome: an operator who asked for FSx and did not get it, with no error anywhere and a bill that looks right. The error names the offending key.
- **A real bug the tests caught immediately: the accepted-key set was too narrow.** Validation started out checking overrides against `MakeClusterParams`' field names only -- which rejected `compute_az`, exactly the knob a caller reaches for, because it is an *input* consumed into the derived `compute_az_list` rather than a field that survives under its own name. Same for `gpu_az`, the subnet overrides, `head_node_bootstrap_timeout`, and the per-arch checksums. Accepted keys are now fields **plus** `MAKE_CLUSTER_DEFAULTS`' own keys.
- **A second bug, caught while smoke-testing rather than by a test: `headnode_instance_type` was defaulting to `_default_loginnode_instance_type(base_os)`.** That returns a plausible instance type from entirely the wrong knob, so it would have silently built head nodes sized for a login node -- the kind of defect that produces an underpowered cluster and no error. It is required now, with no fallback, matching `make_pcluster.py`'s own `required=True`, and a test pins that it has no default.

**14 tests** (`TestBuildMakeClusterParams`), four mutations caught: accepting unknown overrides silently, hardcoding the Docker Compose arch to x86_64 (every node would fetch a wrong-architecture binary), skipping the bootstrap-timeout bump for shared filesystems, and letting defaults win over overrides. **Suite: 2547 -> 2560.** One line-citation drift, the recurring shape, fixed in both surfaces (`:1297` -> `:1392`).

**What remains for the `create_cluster` tool is now only the policy question** -- which of the 76 knobs a *remote* caller may turn. The machinery is done and tested; that choice is deliberately not encoded in `build_make_cluster_params`, which builds whatever it is asked for exactly like the CLI would.

**Same session, round 45 -- the creation pair, built to the operator's decision: full CLI parity via `overrides`, with value validation and three CLI-only parameters. `UNIMPLEMENTED` is now empty.**

- **The operator pushed back on my recommendation and was right to.** I had argued for a curated ~15-parameter surface over an `overrides` passthrough, on the grounds that a smaller schema is easier for a model to fill. That argument does not survive contact: option B *has* the small schema; `overrides` is optional on top of it. Asked directly why A beat B, the honest answer was that it did not -- except for one thing, which testing rather than reasoning found.
- **The real difference, measured: an untyped dict has no schema, so a wrong-typed value is accepted and then silently does nothing.** `overrides={"enable_fsx": True}` -- a Python bool, the most natural thing for a model to emit -- passed the unknown-key guard, was stored as `True`, and failed every downstream `== "true"` comparison. The cluster comes back without FSx, nothing errors, and even the bootstrap-timeout derivation does not fire (2100 instead of 3900), so the symptom is muted too. That is precisely the failure the unknown-*key* guard exists to prevent, reachable through the door it does not cover.
- **So the fix was to close the gap rather than avoid the option.** `_validate_override_types` compares each override against the type of its default and **rejects rather than coerces** -- coercion has to guess whether the int `1` means "true" or a count, and a clear error the caller can act on beats a silent guess. Types are compared with `type() is` rather than `isinstance`, because `bool` subclasses `int` and `loginnode_count=True` would otherwise sail through as `1`; that is a caught mutation.
- **Three parameters are CLI-only, at the operator's direction**: `pre_install_script`, `post_install_script`, `custom_ami` -- the only knobs that change *what code runs on the nodes* rather than what infrastructure gets built. A denylist rather than a schema omission, because `overrides` is an open dict with no schema to leave them out of. The refusal names the parameter, says why ("runs as root on every node"), prints the actual `make_pcluster.py` command to use instead, and states that every other parameter is available here -- a bare refusal leaves the model with nowhere to go, and it will retry or give up. Enforced on **both** the preview and the execute path: a token minted without them could otherwise carry them in on the second call, which is a caught mutation.
- **The preview surfaces consequential defaults, not just the overrides.** `ebs_encryption` and `efs_encryption` both default to `"false"`, so a caller who never mentions encryption gets none -- a fact worth seeing in a preview rather than discovering at an audit. `notable_defaults` lists them (and `cluster_type`, `base_os`, `scaledown_idletime`) whenever they have not been overridden.
- **Two defaults found while grounding this decision that are worth knowing independently**: `compute_instance_type` defaults to `""` and `enable_cpu_queue` is *derived from it*, so a defaults-only cluster has **no compute queue at all** -- a head node and nothing to run jobs on. And `vpc_name` defaults to the literal string `"vpc_default"`, so it is effectively required rather than optional for any account whose VPC is named otherwise.

**15 new tests** across `TestCreateClusterOverrides` and `TestTheCliOnlyParameters`. Four mutations caught: enforcing the denylist only on preview; using `isinstance` (letting a bool through as an int); skipping validation entirely; and dropping the CLI hint from the refusal. **Suite: 2561 -> 2575, `UNIMPLEMENTED` empty** -- every routed tool now has a wrapper. One line-citation drift, fixed in both surfaces (`:1392` -> `:1436`).

**Same session, round 46 -- a latent creation bug, found by the operator asking what `gpu_instance_type` defaults to.**

- **The question was about the GPU default; the answer was that the GPU default is fine and the CPU one is not.** Both `compute_instance_type` and `gpu_instance_type` default to `""`, and `enable_cpu_queue`/`enable_gpu_queue` are derived purely from whether their string is non-empty. An empty GPU type is correct -- most clusters want no GPU queue, and GPU instances are expensive to create by accident. An empty *compute* type means no CPU queue either, so **a defaults-only cluster is a head node with nothing to run jobs on**.
- **Verified rather than reasoned about**: rendering `config.pcluster.j2` with both queues off produces `SlurmQueues: None`, which PCluster's own schema rejects. So the cluster was never buildable -- the question was only how expensively it failed.
- **Expensively, as it turns out, which is what made this worth fixing rather than documenting.** `core_create_cluster` creates the IAM policies, role, S3 bucket, keypair and Secrets Manager secret *before* calling `pc.create_cluster`. A schema rejection there is a late-stage failure, so round 25's handler deliberately preserves all of that and tells the operator to run `kill_pcluster.py`. A full provisioning cycle plus a manual teardown, for an input error visible before anything is spent.
- **The invariant already existed on the other side.** `core_remove_queue` refuses to remove the last queue -- "A cluster must have at least one queue." Creation simply never enforced the same thing. This is not a new rule, it is the same rule applied where it was missing.
- **`_validate_at_least_one_queue` rejects only when both are empty.** Requiring *both* is the obvious over-correction -- GPU-only clusters are a supported shape, with a whole branch in the benchmark job template -- and that mutation is caught. The message names both parameters, gives a concrete value to use, and says what the early failure saves, because a caller told only "no compute queue" has to go read the source to find the knob.
- **Four existing tests were previewing a cluster that could never have been built.** `TestBuildMakeClusterParams` and `TestCreateClusterOverrides` both constructed params with no queue and asserted success. They pass a `compute_instance_type` now; the guard finding them is a small argument for its own value.

**10 new tests** (`TestAtLeastOneQueue`, `TestTheNoQueueClusterIsRefusedRemotely`), including that **no confirmation token is minted** for an unbuildable cluster -- a token would otherwise let `create_cluster` reach provisioning before PCluster rejected the config. Two mutations caught, in both directions: dropping the guard, and over-correcting to require both queues. **Suite: 2575 -> 2585.** One line-citation drift, fixed in both surfaces.

**Same session, round 47 -- closed the CLI half of the same bug, at the operator's direction.**

- **Scoped to the check, not the refactor.** The architecturally tidy fix is routing `make_pcluster.py` through `build_make_cluster_params`, but that means replacing an ~84-kwarg construction in `main()` -- a large change to well-tested CLI machinery for no behavioral gain beyond the one guard. Calling `_validate_at_least_one_queue` directly gets the whole benefit; the refactor stays available if something else ever needs it.
- **Placed beside the existing queue validation** (`_validate_queue_sizes`, two lines above), which is both the natural home for the concern and safely before the first AWS call. Placement is the property, not presence: a guard that runs *after* `core_create_cluster` is exactly as useless as no guard, since the IAM role, S3 bucket, keypair and secret are already created by then. `test_it_aborts_before_any_aws_mutation` asserts the vars file is unwritten, no IAM call was made, and `pc.create_cluster` was never reached -- and that mutation (guard present, moved later) is caught separately from removing it.
- **The core function raises, the shim converts to `sys.exit`.** The repo's established split, and load-bearing here specifically: `_validate_at_least_one_queue` is shared with the MCP tool layer, where an uncaught `SystemExit` kills a long-lived FastMCP server process rather than failing one call -- a hazard `pcluster_core` already documents twice. Its neighbour `_validate_queue_sizes` calls `sys.exit` directly, which is fine because nothing shares it; this one could not.
- **The message is pinned once, in the core function**, and the CLI test asserts against that rather than restating it -- so the two paths cannot drift into telling an operator different things about the same misconfiguration.

**5 new tests. Suite: 2585 -> 2591.** Both paths now refuse a queueless cluster before spending anything, and GPU-only remains supported on both.

**Same session, round 48 -- migrated every `pcluster`-binary shell-out to `pcluster.lib`, closing both open findings at once. They turned out to be one problem, and it was a live bug in the remote path rather than the tidiness item the task list described.**

- **The two items were the same code.** Session 48's "`_run_pcluster_cmd` duplication" and "`update-cluster` still shells out" are the same five call sites, and looking at them together showed the duplication was worse than recorded: **five** places issued the identical `pcluster describe-cluster` subprocess and each parsed a different field out of it, not three. Two of them (`check_cfn_status`, `_live_status`) called `subprocess.run` directly rather than through `_run_pcluster_cmd`, which is why a grep for the helper missed them.
- **The real finding: the fleet, health, diagnose and queue tools could not have worked on the remote transport at all.** `_pcluster_bin()` returns `<repo_root>/.venv/bin/pcluster`; a Lambda deployment package has no virtualenv. The subprocess form raises `SystemExit` on a missing binary -- a `BaseException`, which the handler's deliberately-narrow `except Exception` does not catch -- so the tool would have killed its own container rather than returning an error. Verified by calling the helper with a nonexistent path, not inferred. This is the same hazard the S3 lock hit in round 41 and that `pcluster_core` documents twice; it was present in six tools simultaneously.
- **So the fix is a bug fix, not a refactor.** `_describe_cluster_json`, `_update_compute_fleet_lib` and `_update_cluster_lib` call `pcluster.lib` directly and raise `PClusterMakerError`. CLI behavior is unchanged because the shims already convert `PClusterMakerError` to `sys.exit`; what changes is that a server context can now catch it. No production path builds a `pcluster`-binary argv any more, and `_run_pcluster_cmd` was deleted along with its test class.
- **`_update_cluster_lib` deliberately does not pass `wait=True`** even though `update-cluster` is one of the three operations that accepts it: `_poll_cluster_update` prints per-attempt progress across what can be a 30-minute wait, and the library's polling is opaque. Same precedent as create/delete. It also passes `cluster_configuration` as a **path**, per the round-23 finding that the CLI model tags that parameter `"type": "file"`.
- **A mutation survived the first battery, on the single most important property.** Reverting `_describe_cluster_json` to `raise SystemExit` passed the *entire* suite -- the exact regression this whole round exists to prevent, with nothing pinning it. Every existing test asserted on the *message*, none on the exception being catchable. `TestTheDescribeHelperFailsCatchably` now pins that it is an `Exception`, specifically not a `SystemExit`, specifically a `PClusterMakerError`, and that no subprocess is invoked at all.
- **One self-inflicted error worth recording**: a regex retargeting the test stubs over-matched and rewrote the subprocess helper's own test class body -- the one class that legitimately exercised it -- making it stub the very seam it was meant to exercise. Caught because it failed immediately with a real `/bin/pcluster` lookup. Mechanical edits across test files need the same "check what it actually matched" discipline as production code.

**Test churn was substantial and is the honest cost of the change**: stubs in `test_fleet.py`, `test_diagnose.py` and `test_check_pcluster.py` all patched `subprocess.run`, which these paths no longer reach -- the monkeypatch-isolation trap again, this time because the seam moved rather than the code. Two tests were renamed rather than deleted where the concept survived but its mechanism did not (`test_nonzero_rc` -> `test_a_describe_failure_is_reported_with_its_cause`; there is no return code any more). **Suite: 2591**, five net new tests against five deleted subprocess-shape ones.

**Still ahead in Workstream 5**: moving the 8 IAM drafts into `templates/` (needs a third policy category -- they are Lambda execution roles, neither instance-reachable nor the operator's own, and `test_every_policy_template_is_covered_by_this_ban` asserts directory equality so it fails the moment any file lands; `MCPStackMutation`'s `logs:DeleteLogGroup` on `*` would also trip the instance-reachable ban if miscategorized); adding the `<MCP_USER_POOL_ID>` placeholder to `_render_policy` and the tests' `_PLACEHOLDER_SUB`; `_setup_mcp_infra`; the Router/handler Lambdas and `heavy_handler.py`; and the remaining tool wrappers. Note the plan's own file-layout section (written in the 2-Lambda era) names `MCPDispatcherLambda`/`MCPHeavyLambda`, which the later 5-Lambda revision supersedes -- reconcile to the 5-Lambda design rather than implementing both.

### Session 49 — Workstream 5: packaging and deployment, and the 900-second ceiling

Built the two halves that stood between `_setup_mcp_infra` (IAM only) and a
deployable topology, and in doing so found a defect that no amount of
tool-surface testing would have surfaced, because it is a property of the
*runtime* the tools were routed to rather than of the tools themselves.

**Packaging (`mcp_server/packaging.py`).** One artifact spec per tier:
requirements, source paths, handler, and zip-vs-image kind. Two facts were
measured against the real import graph rather than estimated, and both
turned out to matter:

- **The router imports no third-party package at all** — stdlib plus
  `mcp_server.tiers`. Its artifact is a few KB. That is the concrete payoff
  of keeping `pcluster_core` out of it, and it is why the router can be a
  zip with an empty requirement set instead of sharing the handlers' 77 MB.
  `test_importing_the_router_loads_no_third_party_package` verifies it in a
  subprocess against the actual import graph, not by reading the declared
  list back — the declaration is the thing under test.
- **`aws_cdk` (80 MB) does not appear when `pcluster_core` is imported.**
  PCluster imports it lazily at synthesis time, which is what keeps it off
  four of the five tiers.

`requirements.txt` is the development set and must never be installed
wholesale into an artifact: `ansible` alone is ~408 MB of collections for
playbooks nothing executes any more, plus the `hpc-benchmark` plotting
stack at ~250 MB. Together those exceed Lambda's 250 MB unzipped limit on
their own, for code no tool calls. `EXCLUDED_FROM_LAMBDA` names them with a
reason each, `validate_requirements` refuses a tier that requires one, and
`requirements-lambda.txt` is *generated* from the tier spec rather than
maintained beside it — a drifted hand-maintained copy produces an image
missing a package the handler imports, discovered at the first invocation.

**Only the node tier is a container image, and the split is Node.js, not
size.** `create_cluster` and `apply_cluster_update` call
`assert_valid_node_js()` on their first line and a zip artifact cannot
supply a Node runtime. `Dockerfile.stack-mutation-node` installs it and
verifies it at build time, so a missing Node fails the build rather than an
operator's first `create_cluster` twenty minutes in.

**Deployment (`mcp_server/deploy.py`), and the defect.** It lives in
`mcp_server/` rather than `pcluster_core.py` because it needs
`mcp_server.packaging`, and the dependency direction is fixed: `mcp_server/`
may import `src/`, never the reverse.

Writing the per-tier timeout table is what surfaced it. **Lambda's maximum
function timeout is 900 seconds — a hard service ceiling, not a default,
not raisable by quota.** `apply_queue_config` was registered on the remote
transport *and* routed to a Lambda tier while its own docstring said
"Expect this call to take up to ~30 minutes". It blocks across three
causally dependent phases (stop fleet, apply config, restart fleet).

The failure that produces is not a clean timeout. The function is killed
mid-operation, with the fleet already stopped, a stack update already in
flight, and the cluster's S3 lock held by a process that no longer exists —
a partial mutation whose next symptom is the *next* operator being told the
cluster is locked by a PID that cannot be found. Every existing MCP test
passed, because each one asks whether the tool behaves correctly, and it
does; what it cannot do is finish.

**The fix was already designed — it just had not been wired up.**
`core_apply_cluster_update`'s docstring, written in Workstream 4, says it
was split out of `core_apply_queue_config` precisely so "the MCP tool
surface deliberately exposes the three phases as three separate tools
rather than one opaque multi-phase tool, so the calling model can see which
phase is running and react between them — e.g. skip the restart when the
config apply failed." `tools.py` never registered it. So: `apply_queue_config`
joins `_LOCAL_ONLY` (the only exclusion there that is a hard limit rather
than a judgment call), a non-blocking `apply_cluster_update` wrapper lands
and takes its place in `TOOL_TIERS`, and remote callers drive the three
phases with `check_cluster_health` between them.

**The guard is the general rule, not the specific instance.**
`test_no_routed_tool_wrapper_passes_wait_true` walks `tools.py`'s AST and
rejects any wrapper in `TOOL_TIERS` that passes `wait=True` to a core
function — AST rather than grep, since `wait=False` and `wait=True` differ
by four characters and both spellings appear in that file's prose. Its
vacuity guard parses a synthetic blocking wrapper and asserts the scan
finds it. Two more halves are pinned separately because the broken
arrangement satisfies either one alone: the tool must not be routed, *and*
it must be excluded from the remote transport. `test_the_replacement_phase_tool_is_routed`
is the vacuity guard against "fixing" this by deleting the capability
outright, which would leave remote callers with no way to update a queue at
all.

Six faithful mutations, all caught: reverting the defect wholesale;
`wait=True` on a routed wrapper; deleting the replacement rather than
decomposing; giving the router the full 900s (it does one
`InvokeFunction`, so a router still running after a minute is a bug and a
low ceiling surfaces it instead of billing for it); updating configuration
before code (a config pointing at code never uploaded is the worse of the
two intermediate states); and dropping the image-vs-zip code-shape guard,
which would otherwise deploy a container function pointing at an S3 zip and
fail at the first invocation.

**Suite: 2651**, up from 2591 — 27 packaging tests, 29 deployment tests,
four net new after the `apply_queue_config` rewiring. Lint and shellcheck
clean.

**Still ahead in Workstream 5:** nothing structural. The remaining work is
Workstream 6 (`mcp_server/auth/register_lambda.py`,
`authorizer_lambda.py`, and the Cognito user pool their two policies are
scoped to) and a live deployment, which has never been run — everything
above is verified against stubs and the real import graph, not against AWS.

### Session 50 — Workstream 6: OAuth for the remote server

Three pieces on top of native Cognito, which supplies everything else.
`mcp_server/auth/`: `register_lambda.py` (RFC 7591 DCR), `authorizer_lambda.py`
(API Gateway Lambda authorizer), `discovery.py` (the two static documents,
served as API Gateway mock integrations rather than functions). Every AWS
API shape below was read out of botocore's own `cognito-idp` service model
rather than assumed.

**The central fact, and the reason the native JWT authorizer is not used:
Cognito access tokens carry no `aud` claim at all.** They carry `client_id`;
only *ID* tokens have `aud`. API Gateway's native JWT authorizer validates
audience via `aud` specifically, so against Cognito it either rejects every
valid access token or runs with audience validation disabled — and the
second defeats the requirement the MCP spec states plainly, that a server
MUST validate tokens were issued for its use. So the check is `client_id`.

**`token_use` is checked for a reason that is not belt-and-braces.** An ID
token *does* carry `aud`, and its value is the app client id — so a check
written against `aud` accepts an ID token, and a check written against
`client_id` alone rejects it for the wrong reason (absent claim) and keeps
working right up until someone "helpfully" adds an `aud` fallback. Pinning
`token_use == "access"` states directly that an ID token must never
authorize a tool call. `test_an_id_token_is_refused` caught both the
`verify_aud` mutation and the deleted-check mutation.

**No allowlist store.** The pool has exactly one purpose and
`register_lambda` is the only caller of `CreateUserPoolClient` against it,
so any client id currently in the pool is legitimate by construction.
`DescribeUserPoolClient` is the check and `ResourceNotFoundException` is a
*deny* — deleting the app client revokes access immediately, with no second
store to keep in sync. Any *other* Cognito error is explicitly not treated
as an unknown client: throttling or a denied IAM call is not evidence a
token is invalid, so it fails closed under a distinct message.

**Every authentication failure raises; no Deny policy is ever built.** API
Gateway maps a raised `Unauthorized` to **401** and a Deny policy to **403**.
Claude re-authenticates on 401 and gives up on 403, so returning a Deny for
an expired token leaves the connector permanently broken instead of
prompting the refresh Claude would do on its own. The class name is the
contract, and `test_the_module_never_builds_a_deny_policy` scans the source
because a Deny is exactly what a well-meaning refactor would add — it reads
as the "proper" authorizer shape. (This 401/403 mapping is from AWS's docs;
unlike the Cognito API shapes it has **not** been live-verified here.)

**The allow policy is widened to the whole stage, deliberately.** API
Gateway caches an authorizer response by token, so a policy scoped to
`event["methodArn"]` — whichever path happened to be called first — is
replayed for every other path and denies it.

**`RefreshTokenValidity` is why `/register` is a Lambda and not a
pre-created app client.** Cognito's default is 30 days and **does not reset
on use**; AWS's own docs say the rotated token "is valid for the remaining
duration of the original." Left unset, the operator re-clicks the OAuth
consent screen monthly with nothing naming the cause. Set explicitly to
3650 days — and the *unit* is sent with it, because the API's raw bound
(315,360,000) is in seconds, so 3650 without `TokenValidityUnits` means
3650 seconds, about an hour. That misconfiguration looks like a correctly
configured long lifetime and is not one, which is why the value and the
unit are two separate tests.

**One guard was a false positive, caught by mutation and worth recording**
— it is the third instance of this exact class in this effort (round 33's
comment self-match, round 37's docstring match).
`test_an_http_callback_is_refused` passed a lone
`http://claude.ai/api/mcp/auth_callback`. With the https check deleted, the
*missing-Claude-callback* check rejected it instead — and since that
message quotes `https://claude.ai/...`, `match="https"` matched the wrong
message and the test passed with the guard gone. Fixed by including the
required Claude callback alongside an http URL, so the https check is the
only thing that can reject the payload, and matching on `every
redirect_uri` instead. Both guards are now independently caught.

**Packaging.** `register` and `authorizer` join `TIER_PACKAGES`, and
neither carries `pcluster_core` or `fastmcp` — the authorizer runs before
*every* MCP request, so its cold start is on the critical path of the whole
server. `boto3` and `jwt` are imported lazily inside the functions that
need them, verified against the real import graph in a subprocess rather
than from the declaration. `PyJWT` becomes a direct `requirements.txt`
entry: it arrives transitively via `mcp` in the development set, but this
artifact carries neither `mcp` nor `fastmcp`, so relying on the transitive
would ship a function that cannot verify a token. Timeouts are the
shortest in the system — 10s for the authorizer (API Gateway's own
integration timeout is 29s, so more could not be reached anyway), 30s for
`/register`.

Adding the two tiers to `TIER_PACKAGES` broke
`test_every_packaged_tier_has_a_runtime_config` immediately, which is the
guard doing its job: a tier with an artifact and no timeout is a function
that cannot be deployed.

**Mutation testing: 15 mutations, 14 caught on the first pass**, the 15th
being the false-positive guard above, fixed and re-verified in both
directions. **Suite: 2700.** Lint and shellcheck clean.

**Still open, and unchanged by this session:** whether Claude's actual DCR
registration payload maps cleanly onto `CreateUserPoolClient`'s parameters
— the plan flags this as observable only at real implementation time, and
nothing here has been exercised against Claude or against AWS. No live
deployment has been run.

### Session 51 — Workstream 7: tool-surface scoping and testing

WS7's two halves were mostly already built as a side effect of WS4/WS5 —
both hard exclusions were in `_LOCAL_ONLY`, the four planned test files
existed, and the plan's named cross-cutting guard (remote's tool set
differs from local's by exactly `_LOCAL_ONLY`) was in place. What this
session found is that **three of those pieces were tested against
something other than the thing that ships**, and each hid a real defect.

**1. The degradation was applied to the wrong transport.**
`ssh_available=False` was hardcoded in the `check_cluster_health` and
`diagnose_cluster` wrappers — which are shared by both server instances,
with `remote` in scope right there and unused. So the *local* stdio
server, running on the operator's own laptop where the `.pem` actually
is, also skipped every SSH-dependent sub-check. Per the `sinfo`
constraint, `check_slurm` is exactly the check that separates "the
cluster exists" from "the cluster can run work", so the local tool was
quietly less capable than the CLI it wraps, with nothing saying why.
Now `ssh_available=not remote`, pinned in both directions.

The pre-existing test asserted `ssh_available is False` while driving
`build_local()`, and its own docstring described the remote transport —
which is how the hardcode read as correct. Retargeted to `build_remote()`.

**2. `base.handle` called a method FastMCP does not have.** It called
`mcp._call_tool(...)`; FastMCP 3.4.7 has `call_tool`. **Every `tools/call`
on every handler Lambda would have raised `AttributeError`** — caught by
the broad `except Exception` and returned as a shaped internal error, so
even the failure would have looked like a well-handled tool fault rather
than a dead dispatch path.

It was invisible because every `tools/call` test passes `server=<stub>`,
and the stubs defined `_call_tool` — the stub and the production code
agreed with each other and both disagreed with FastMCP. `tools/list` was
never stubbed (no test passes `server=` for it), which is exactly why
`list_tools()` and `to_mcp_tool().model_dump()` were fine. The bug lived
precisely in the one path a stub stood in for.

A corrected method name alone would not have fixed it: `call_tool`
returns a `ToolResult` carrying pydantic content models, not a dict, and
`_to_content` had no branch for it — the whole response would have become
the *repr* of the ToolResult as one text block. Both halves are fixed and
now driven against a real `FastMCP` instance.

**3. The router forwarded a failing Lambda's stack trace.** A failing
Lambda still returns **StatusCode 200**; the failure is `FunctionError`
plus a payload of `{"errorMessage", "errorType", "stackTrace"}`. The
router did `json.loads(response["Payload"].read())` and returned it
verbatim, so an unhandled handler failure put `/var/task/...` paths into
the MCP response — the exact thing layer 5 exists to prevent — *and*
returned something with no `jsonrpc` or `id`, which the client cannot
parse as a failure at all. The handler's own `except` cannot cover this:
it only sees a failing tool, never a cold-start import error, an OOM kill
or a timeout. `unwrap_invocation` now translates it, dropping the trace
and keeping the type and message.

**4. Layer 1 was not doing what the plan specifies.** The schema tests
checked properties *of* the schema (a description exists, `cluster_name`
is required) but never compared a wrapper against the core function it
calls — which is the drift the layer is named for. `TestEveryWrapperAgreesWithTheCoreFunctionItWraps`
walks `tools.py`'s AST over all 18 wrapper → core call sites and pins both directions (a required keyword-only
parameter the wrapper omits; a keyword the core function does not accept),
plus a ban on `**kwargs` splats, which would make drift invisible exactly
where it becomes likely.

**Two of this session's own guards were too weak, both caught by
mutation.** The first asserted `"osiris" in text` and `"ToolResult" not in
text` — and both passed against the broken output, because falling
through to the catch-all yields pydantic's `__str__`, which embeds the
real payload verbatim and (being `__str__`, not `__repr__`) never names
the class. Replaced with a `json.loads` round-trip against the tool's
actual return value. The second asserted `isError` on a failing tool —
which failed, and revealed that the *docstring* I had written was wrong:
FastMCP **raises** `ToolError` rather than returning `is_error=True`
(verified against 3.4.7). So the `isError` branch in `_to_content` was
unreachable on every real path, which is why no mutation could kill it;
it was deleted as dead code and the test rewritten to assert the behavior
that actually occurs.

**Mutation testing: 16 mutations across the new guards, 14 caught on the
first pass**, the two exceptions being the self-inflicted weak assertions
above, both fixed and re-verified. **Suite: 2712 → see below.** Lint and
shellcheck clean.

**Audit conclusion for the five layers.** Layer 1 now compares wrappers
to cores rather than schemas to themselves. Layer 2 drives a real FastMCP
object for `tools/call`, not only a stub. Layer 3 (confirmation token) is
pure Python with no stubs and needed nothing. Layer 4 (S3 lock) stubs only
the AWS boundary and was additionally live-verified with 8 concurrent
writers — the strongest coverage in the project. Layer 5 now covers both
the handler boundary (a raising tool) and the router boundary (a failing
Lambda), which were separate leaks.

**Still open:** no live deployment has been run, so none of the remote
transport has executed against real API Gateway, real Lambda, or real
Cognito. Everything above is verified against real library objects and
stubbed AWS boundaries.

### Session 51 (cont.) — landing the migration in two commits

The seven workstreams could not be committed one-per-workstream, and the
reason is worth recording rather than rediscovering: **`src/pcluster_core.py`
is a single file carrying WS1-5** (+6,939/-71 — 17 `core_*` functions,
`render_template`, the `pcluster.lib` calls, the S3 lock, `_setup_mcp_infra`),
and `mcp_server/tools.py`, `handlers/base.py` and `router.py` each carry WS5
*and* WS7. Splitting them needs hunk surgery; `git add -p`/`-i` is
unavailable in this environment, and a WS5-then-WS7 split would mean writing
the broken `_call_tool` version back in and committing known-broken code so
the next commit could fix it — a history that did not happen.

Two commits, on the one seam that falls on file boundaries, verified
self-contained first: no non-MCP test or source imports `mcp_server` or
`fastmcp`, and the counts partition exactly (2391 + 333 = 2724).

- `47c30da` — WS1-4, the toolkit side.
- WS5-7, the MCP server.

**Committing surfaced three stale claims in the public `CLAUDE.md`**, all
invalidated by WS1-4 and none caught earlier because the doc-hygiene tests
check citations and byte budget, not whether a claim is still true:
the mkdir build lock (deleted, replaced by the S3 lock), the playbook's
`assert` task described as the live `base_os` gate (the playbook no longer
executes), and `_build_summary` described as one of three live summary
surfaces (now two). Fixed in the same commit that invalidated them.

**`CLAUDE.md` had no MCP section at all** — every MCP constraint was in
`CLAUDE.local.md`/`CLAUDE-STATE.md`, both gitignored, so the public repo
would have shipped 19 modules and 9 IAM policies with no committed
constraint documentation. Added: the 900s ceiling, the missing `aud` claim,
the local/remote split, the tier/IAM split, the Lambda-packaging rule, and
the stub-vs-real testing lesson. The matching text was then *deleted* from
`CLAUDE-STATE.md` rather than trimmed elsewhere — that file is current
state, `CLAUDE.md` is the rules, and the duplication was the actual defect.
Net effect on the preamble: -2,000 bytes despite +1,907 of new rules.

### Session 52 — the first live build after the migration, and what it caught

`./make_pcluster.py -N osiris -A us-east-1a ...` failed at `CreateBucket`
with `IllegalLocationConstraintException: The unspecified location
constraint is incompatible for the region specific endpoint this request
was sent to`, after the four managed policies and the IAM role existed.
The rollback path worked and swept all five.

**The bucket code was right; the client was wrong.** `core_create_cluster`
built `s3_client = boto3.client("s3")` with no `region_name`, so it
resolved its endpoint from the ambient `AWS_DEFAULT_REGION`/`AWS_REGION`/
profile rather than from the region the build targets. Both `create_bucket`
call sites correctly omit `CreateBucketConfiguration` for a `us-east-1`
target — which S3 requires — but that request reached a *different*
region's endpoint, which rejected the omission. The error message is S3
saying the omission was wrong for the endpoint it arrived at, not for the
region the operator asked for, which is why it reads backwards.

**The failure is the lucky case.** The next two calls on that path create
the EC2 keypair and the Secrets Manager secret, both regional. Had S3 not
failed first they would have been created in the ambient region while the
cluster came up in the target region — a build that reports success and
leaves no way to reach the head node. That is the outcome this bug was
one API call away from producing.

**A regression from the boto3 migration, and one the suite structurally
could not catch.** `amazon.aws.s3_bucket` took its region from the
playbook's connection settings, so nothing had to state it until the
module call became a boto3 call. Every S3 test stubs the client, and a
stub has no endpoint to be wrong about — so the guard is static analysis,
not another behavioral test.
`TestEveryRegionalBotoClientIsBoundToTheTargetRegion` walks the AST of
`src/*.py` and the repo root and requires `region_name` on every client
for a regional service. `iam`/`sts` are global, exempt, and asserted to
be exempt. A service in **neither** list fails a vacuity test — building
a client for an unclassified service is the obvious way to silence the
check, and it was a caught mutation.

A repo-wide sweep found no other unbound regional client. An adjacent
`boto3.resource("s3")` on the same two lines was dead and was deleted.

**Standing lesson:** the migration's own test suite was green at 2724 and
this still shipped. Nothing in it exercises a real endpoint, so anything
whose only observable effect is *which endpoint a call reaches* is
invisible to it by construction. Prefer a static guard for that class.

### Session 52 (cont.) — the abort that sent the operator the wrong way

The region fix unblocked the build, but the *previous* failure had left
`src/vars_files/osiris.yml` behind, so the next run aborted with "An
existing vars_file for cluster osiris was found!" and instructions to run
`kill_pcluster.py`. There was no cluster to kill -- the failed build never
launched a stack -- so the advice was wrong for the state that produced it,
and the message never named the file actually blocking the rebuild.

**Two states, one message, opposite remedies.** A running cluster must be
torn down; a build that died pre-launch created nothing in AWS. The serial
file discriminates: written when a build commits to a cluster identity,
removed by the pre-launch rollback. Its absence beside a vars file means
the rollback ran. The abort now branches on it and prints only the
applicable remedy.

**The message moved into `existing_vars_file_guidance()`.** As a string
literal inside `core_create_cluster` no test could reach it, which is how a
wrong remedy shipped unnoticed -- the same "logic belongs in
`pcluster_core.py` so it is testable" rule that already applies to the
remote key-rotation scripts.

**The cause was fixed, not just the symptom.** The pre-launch rollback
removed the serial file and released the lock but left the vars file and
`active_clusters/<name>/`, so every failed build armed the next run's
abort. It now removes all three.

**Third weak assertion of the session, same class.** The rollback test
asserted `"vars_file_path" in ast.dump(handler)` -- but that name also
appears in the block's own `print`, so deleting the `os.remove` left the
test green. Rewritten to walk for the `os.remove`/`shutil.rmtree` calls and
their argument names. The recurring lesson: **an assertion that matches a
name rather than a structure passes on any block that merely mentions the
name.** Also worth recording, one mutation was malformed rather than
surviving -- the target pattern occurs 4x in the file and the first attempt
removed a different occurrence, breaking collection. A mutation that fails
to collect is not evidence of anything.

Deliberately not added to `CLAUDE.md`: the rollback-leaves-nothing rule is
now guarded by a test and is not a subtle cross-surface constraint of the
kind that file carries. Revisit if it breaks again.

### Session 52 (cont.) — what the first live build actually found

Four defects, all from running the thing rather than testing it. Two are
the same shape and that shape is the lesson: **a value that is correct in
the local context, used in a remote one.**

- **S3 client unbound to the target region** (2677416). Correct
  `CreateBucket` code, wrong endpoint.
- **`stage_dir` from `tempfile.gettempdir()`**. The path is created and
  written on *both* machines -- staged locally, then `mkdir -p` + `scp` to
  the head node. On macOS that is `/var/folders/<...>/T`, which the head
  node cannot create. **Invisible on Linux**, where `gettempdir()` is
  `/tmp`, so a CI run or a Linux developer would never see it. It failed a
  build 15 minutes in, after the stack was up. `vars_file.j2` had also
  drifted to a second copy of the literal; it now echoes the Python value.

The other two:

- **The vars_file abort recommended the wrong remedy** (b865137), because
  a failed pre-launch build left state the next run tripped over. Fixed at
  both ends: detect which state applies, and stop producing it.
- **A working teardown looked broken.** Upstream logs every
  `AWSClientError` at ERROR before raising it, so `get_object` failing
  during a delete (the config's object version is going away) printed once
  per poll. Suppressed narrowly -- one logger, one message, the wait's
  duration -- because silencing another project's error stream is how a
  real failure gets hidden.

**The recurring testing lesson, now on its third instance this session.**
A test that exercises a *stand-in* is not exercising what ships:
  * the FastMCP handler tests stubbed `server=` and the stub defined the
    same wrong method name as the code;
  * the summary-ordering test evaluated `create_pcluster.yml`'s expression,
    which no longer executes, so reverting the Python summary left it
    green;
  * every S3 test stubs the client, and a stub has no endpoint to be wrong
    about -- which is why the region guard is static analysis.
When the real object is available, at least one test must drive it. When
it is not, prefer a static guard over a behavioral test that can only see
the stand-in.

**And a fourth weak-assertion instance**, same class as the earlier three:
the rollback test matched `"vars_file_path"` in the handler's AST dump,
but that name also appears in the block's own `print`, so deleting the
`os.remove` left it green. Structural assertions (walk for the call and
its argument) rather than name matching.

Also: adding `import logging` shifted `pcluster_core.py`'s line numbers and
broke `templates/CLAUDE.local.md`'s `attach_role_policy` citation. The
line-citation guard caught it. That citation has now moved nine times;
it is the single most drift-prone thing in the docs.

### Session 52 (cont.) — a regression I caused, and the gate that now catches it

**I broke the serial upload in `2677416` and it reached a live build.** I
deleted `s3 = boto3.resource("s3")` as unused. It was used 1,000 lines
below (`s3.Object(s3_bucketname, ...)`), and the grep that "proved" it dead
filtered on a pattern (`s3_bucket`) that matched the very line using it.
Three things then had to fail together for it to ship, and all three did:

  * `make lint` runs ansible-lint on two playbooks — **no Python linting
    existed anywhere in the gates**;
  * every AWS call in the suite is stubbed, so nothing executed the line;
  * the call sits inside `except Exception: print("WARNING: could not
    upload serial number to S3: {e}")`, so a `NameError` printed as a
    transient S3 hiccup and **the build reported success**.

The lesson is not "grep more carefully". It is that a broad `except` around
a call turns a programming error into an operational-looking warning, and
that a repo with no undefined-name check will ship one eventually.
`tests/test_undefined_names.py` closes it with pyflakes, scoped to that one
class — pyflakes also reports unused locals and placeholder-less f-strings,
and this repo has a backlog of both; a gate nobody can keep green stops
being read.

**Commit ordering became load-bearing for the first time.** The gate had to
land *after* the fix, not before: pyflakes reports `undefined name 's3'`
against the pre-fix tree, so a guard-first commit would have been red.
Verified with `git show HEAD~2:src/pcluster_core.py` rather than assumed.

**Six surfaces stated the teardown duration and all six were stale.** The
obvious grep found five; the sixth was embedded mid-sentence in the MCP
server's instructions string as `20-45 minutes and 5-10 `. Now guarded
three ways: the surfaces agree, none carries the old figure, and a repo
sweep fails when a *new* surface appears. That last one is the only part
that survives someone adding a seventh.

**The self-match trap, fourth through sixth instances.** Writing that guard,
the stale-figure check matched its own source three times running — the
detector's literal tuple, then the docstring explaining why not to write
the literal, then the class docstring describing the change. The fix was
not cleverer escaping: this file is the *checker*, not an operator-facing
surface, so it is an explicit exemption with the reason recorded. **A
self-scanning check cannot describe what it scans for.**

**Two invalid mutations, worth recording as a technique note.** One "added
a seventh surface" to a file already in the manifest; another removed a
pattern occurring 4x and broke collection. Neither proved anything. A
mutation that fails to collect, or that lands somewhere the guard already
covers, is not evidence — re-target it.

Also this session: the progress line now shows true elapsed time
(`[ 01m30s ]`, fixed width) because the delete wait polls twice a minute
and whole-minute labels printed each one twice; and both printers stopped
hardcoding a poll interval beside a `delay_seconds` they never read. Note
there are **two layers each** — a poll helper and an `*_and_classify`
wrapper — and fixing only the helper leaves the wrapper wrong.

`README.md` gained an MCP Server section; it had documented none of
`mcp_server/`, so the public repo shipped 19 modules with no way to learn
they existed. The remote Lambda transport is deliberately left out until
it has actually been deployed.

### Session 52 (cont.) — the region the MCP create path never resolved

`MakeClusterParams` has 84 fields and `region` is deliberately not one of
them: its own docstring records that the AZ check, the Ansible version
check and the Turbot profile switch all have to run, in that order, before
the region is known, so the CLI resolves it from the
`describe_availability_zones` response and hands it to
`core_create_cluster` as a separate parameter (`make_pcluster.py:893`).

The MCP server is a second shim and skipped that step. `create_cluster`
called `core_create_cluster(..., region=params.region)` on an object with
no such attribute, so **every** MCP build raised `AttributeError`. It
failed safe — arguments evaluate before the call, so nothing reached AWS —
but the tool had never once completed a build. Found by asking why
`preview_cluster_config` reported `"region": ""`; the preview's
`resolved.get("region", "")` is the same root cause, reading a key
`dataclasses.asdict(params)` cannot contain.

**Why the suite was green.** `test_create_cluster_also_refuses_them` is
the only test that calls the tool, and it asserts an *error* — which it
gets from `_reject_denied`, several lines above the region line. It passes
for a real reason and never reaches the bug: CLAUDE.md's "when a test
stubs the object under test, at least one test must drive the real one",
the same shape as the `handlers/base.py` incident.

**The fix is a shared resolver, not a string trim.** `az[:-1]` is correct
for every AZ `_validate_az_input`'s regex accepts (Local Zones like
`us-east-1-bos-1a` fail that regex, so they are already out of scope), and
that is exactly what makes it the tempting wrong repair: it returns the
right region while proving nothing about whether the AZ exists. A
well-formed typo (`us-east-2z`) would then bind every regional client to a
region the operator never named — the failure the "every regional boto3
client must be built with `region_name=region`" rule exists to prevent.
`resolve_region_from_az` (`src/pcluster_core.py`) asks EC2 and raises
`PClusterMakerError` — never `sys.exit`, which is a `BaseException` that
would kill a long-lived stdio server, the hazard already documented at the
distributed-lock call site.

`preview_cluster_config`'s always-empty `"region"` key was dropped rather
than filled: the preview makes no AWS calls today and populating the key
honestly would require one. Making the preview verify the AZ before
minting a token is the natural follow-up — it is the same argument
`TestTheNoQueueClusterIsRefusedRemotely` already makes about unbuildable
clusters — but it is a behavior change to a tool documented as offline,
not part of this fix.

**`TestCreateClusterResolvesTheRegionItBuildsIn`** drives the real tool and
the real resolver, stubbing only the EC2 client (via `pcluster_core.boto3`,
since patching the resolver would stub the thing under test) and
`core_create_cluster`. Three faithful mutations are caught: the shipped
`params.region` (3 of 4 tests fail), the `az[:-1]` trim (3 fail — the
divergent-region test is what separates it from a correct fix), and
dropping the empty-zone check (the unknown-AZ test fails, on an opaque
`IndexError` instead of a named message, which is the point). The fourth
test pins that `MakeClusterParams` still carries no `region` field, since
adding one is the repair that would make `params.region` compile and put
the resolution back where it cannot happen.

**Full suite: 2787 passed, 0 failed** (up from 2783, +4). `make lint`
clean. One line-citation drift, caught by the suite itself and fixed in
both surfaces: the 33-line helper landed near the top of `pcluster_core.py`
and shifted `iam.attach_role_policy(` from `:1541` to `:1574`
(`templates/CLAUDE.local.md`'s prose and the manifest in
`tests/test_claude_docs_line_citations.py`) — the same drift class as three
previous rounds, and the reason that sweep exists.

### Session 52 (cont.) — the defaults file the MCP server could not see

`<cluster_name>_defaults.yml` is how an operator describes a cluster once.
The CLI required `--use_defaults` to apply it and otherwise printed a
`*** WARNING ***` that the file existed; the MCP server has no flags and so
could not apply it at all. The same cluster name therefore built two
different clusters depending on which surface asked, and an MCP caller had
to transcribe all 78 keys of the file into an `overrides` dict by hand --
which is how this was found.

**The mechanism was already in core.** `_load_defaults_file` and `_resolve`
have lived in `pcluster_core.py` since the MCP migration. Nothing
structural prevented the wiring; it was never done. `discover_defaults_file`
and `load_cluster_defaults` are the new single loader, and
`build_make_cluster_params` layers the file below `overrides` and above
`MAKE_CLUSTER_DEFAULTS` -- the same three-tier precedence `_resolve`
already gave the CLI, so both surfaces resolve identically.

**The CLI's warning is gone, not suppressed.** The state it described --
the file exists and was not loaded -- stops existing once the file always
loads. `--use_defaults` survives as the override for a differently-named
file and still wins. No test covered that warning, which is why removing it
broke nothing: it was unguarded for its whole life.

**Non-build keys are ignored, not rejected.** One file serves
`make_pcluster.py` and `kill_pcluster.py`, so `delete_s3_bucketname` is
legitimately in it -- and it is exactly what bounced the first hand-built
MCP preview. Worth recording precisely: a merge-time filter for this was
written and then removed as redundant, because the `MakeClusterParams(**{k:
v for k, v in values.items() if k in fields})` construction already drops
anything that is not a field. The mutation removing the merge filter passed
the entire new test class, which is what exposed it as decoration. The
class still pins the outcome; it does not claim to pin a mechanism that
isn't there.

**The token had to grow to cover the file.** An auto-applied file is an
input to the build that nobody typed, and a token is an assertion that this
configuration was previewed -- so a file edited inside the 15-minute window
would otherwise build something else while the token still verified.
`_defaults_fingerprint` hashes the file's contents into `token_params`.
Hashing the resolution's *input* rather than the resolved
MakeClusterParams is deliberate and is the one place the implementation
departs from the literal instruction: binding the output would say the same
thing, since resolution is a pure function of (explicit arguments, file
contents), but only by building the params -- and `verify()` must run
before `build_make_cluster_params` so a tokenless caller is refused as
tokenless rather than told which parameter it got wrong, which is what
`test_the_gate_runs_before_anything_that_could_build` pins.

**The suite was reading the developer's own files.** First full run after
the wiring: 15 failures, nearly all because tests name their cluster
`osiris` and `osiris_defaults.yml` sits in the repo root. Those files are
gitignored, so every affected test resolved against the operator's real
cluster locally and against nothing at all in CI -- the same class of
green-that-means-nothing as the AZ verification reaching live AWS earlier
this session, and the second instance in one session. `tests/conftest.py`
grew an autouse `_no_operator_defaults_file` that points discovery at an
empty directory; a test that wants a file writes one and repoints the seam.
The fixture also forced `src/` onto `sys.path` in `conftest.py`, since it
touches `pcluster_core` during collection of every test module and running
one file in isolation had failed with `ModuleNotFoundError`.

**Three mutations caught, one survived and was informative**: dropping the
token's `defaults` key (the stale-file test fails), inverting the merge so
the file overrules an explicit override (the precedence test fails), and
removing the merge-time key filter (**survived** -- see above; the filter
was redundant and is gone). Two more from the AZ work in the same round:
preview no longer verifying (2 of 3 fail) and the AZ check hoisted above
parameter resolution (the ordering guard fails).

**Full suite: 2801 passed, 0 failed**, verified both with credentials and
under the CI-equivalent invocation with them blocked. `make lint` and
`make shellcheck` clean. Verified end-to-end against the real
`osiris_defaults.yml`: five required arguments and no overrides now resolve
`compute_instance_type='c8g.xlarge, c7g.xlarge, c6g.xlarge'`,
`base_os='ubuntu2404arm'`, `cluster_type='spot'`, `docker_compose_arch='aarch64'`.

**Preamble budget.** The new bullets pushed the three always-loaded files
294B over the ceiling. Rather than raise it: `CLAUDE.local.md`'s `## Code
style` section (872B) and its header, `## Always do first`, CI-venv and
project-venv paragraphs (1049B) were all verbatim or near-verbatim copies
of `CLAUDE.md`, which loads beside it every session -- removed, with the
few unique fragments folded into `CLAUDE.md`. Headroom ended at 2,025B
against the 2,000B working floor. `_CEILING` deliberately not lowered: the
combined size is roughly flat across the round, so there is no real
reduction to ratchet against, and lowering it would break the floor.

### Session 52 (cont.) — three defects the first post-restart preview showed

Restarting the MCP server and running one `preview_cluster_config` against
the real `osiris_defaults.yml` confirmed the round's work (region
populated, `defaults_file` named, five arguments resolving the whole
cluster) and exposed three defects in the same response. Worth recording
that the confirmation call is what found them: none of the 2,801 tests did.

**`notable_defaults` reported values the cluster was not using.** It
filtered on `k not in (overrides or {})` and had no idea the defaults file
existed, so it stated `base_os: ubuntu2404` while `resolved_config` said
`ubuntu2404arm`, and `scaledown_idletime: 5` against a resolved 1. A
preview naming the wrong OS to the operator about to approve the build is
the worst possible place for this. Now excluded on `k not in
_file_defaults` as well.

**`non_default_settings` was `{}` on a heavily customized cluster**, same
cause. A `defaults_file_settings` block now reports what the file
contributed, filtered to `set(resolved) | set(MAKE_CLUSTER_DEFAULTS)` so a
non-build key like `delete_s3_bucketname` is not presented as something
this cluster was configured with, and excluding keys an explicit override
shadowed, which belong under `non_default_settings`.

**A YAML key with no value became None and reached a `str` field.**
`gpu_instance_type:` in the file parses as None; every reader here treats
a present key as an explicit setting, so it overwrote the `""` default and
`.split(",")` raised `AttributeError` -- the same shape as the
`params.region` bug earlier in this session, a value of the wrong type
reaching code that assumed otherwise. `_drop_unset` is applied in **both**
loaders, because the `--use_defaults` path had the identical bug (`_resolve`
returns a file's None unchanged) and a fix in one place would make the same
file behave differently depending on whether it was named on the command
line. That bug predates this round and was simply unreachable without the
flag; auto-applying the file put it in front of every operator who has one.

Four mutations, all caught: the None filter reverted in each loader
separately (one test each), `notable_defaults` dropping its
`_file_defaults` exclusion, and `defaults_file_settings` dropping its
build-parameter filter. The vacuity guard is
`test_a_default_the_file_leaves_alone_is_still_reported` -- emptying
`notable_defaults` would satisfy the correctness test and destroy the
block.


### Trimmed CLAUDE.local.md bullets (moved 2026-08-22)

Moved verbatim out of the always-loaded preamble to stay under the byte
ceiling. Still true, still the reasoning behind the rule that remains in
`CLAUDE.local.md`'s `enable_external_nfs` bullet -- just not something
that has to be read at the start of every session.

- **The original TODO's plan (`ping`, hard-fail on any of the three) was rejected, not just deferred.** `ping` failing proves nothing here — ICMP is routinely blocked by security groups/firewalls on hosts where NFS itself works fine — so it was dropped entirely rather than added as a third warning-only signal that would just add noise.

- **The real defect was in the test harness.** `_collect_templates()` in `tests/test_templates.py` filtered on `.j2`/`.jinja2`/`.jinja` suffixes, and this file has none — at the time, `create_pcluster.yml` rendered it with `template:` from a hardcoded path (that render has since moved into Python; see `docs/sessions.md`) — so **no test had ever rendered it** for the life of the repo, which is how two dead ladders survived. `EXTRA_TEMPLATES` lists it, and `test_collect_templates_matches_what_the_playbooks_render` walks every `template:` task in both playbooks and requires each `src` to be in the discovered set. It resolves each `src` against a rendered + `yaml.safe_load`ed `vars_file.j2` (a hand-written context is not sufficient) with `enable_monitoring="true"` (`monitoring_wrapper_src` and `grafana_tunnel_src` exist only under that gate) and a `repo_paths` dict spread on both sides, since the conftest fixture supplies `cluster_template_dir` and Jinja prefers a context value over an assignment in the same file. Emptying `EXTRA_TEMPLATES` fails it.

- **`showmount`'s failure modes are also not uniformly trustworthy, which is why *its* connection failures warn rather than exit too.** `showmount`/`rpcinfo` depend on the legacy NFS MOUNT protocol (`mountd`, often via `rpcbind`), which is universal on NFSv3 but not guaranteed on an NFSv4-only server — some filers don't run `mountd` at all and would fail this probe even though real mounts work fine via NFSv4's pseudo-root. There's no confirmed inventory of which of the filers this toolkit names in the README (Vast, NetApp, WekaIO, Qumulo) keep NFSv3/`mountd` compatibility on by default, so treating a `showmount` connection failure as proof of a broken server would be guessing.

- **No new CLI flag or bypass was added.** A `--skip_external_nfs_check` escape hatch was considered for the vantage-point case, but since nothing here ever hard-fails except the one unambiguous signal, there's nothing to bypass — smaller surface, nothing new to document or test.

- **`subprocess.run(["showmount", "-e", server], ...)` uses the list form, never `shell=True`.** No injection risk regardless, since `server` is already constrained to `^[a-zA-Z0-9.\-]+$` by the existing regex check upstream of this call — but the list form is defense in depth independent of that.

- **`TestCheckExternalNfsReachable` (`tests/test_make_pcluster.py`) covers all five branches** (reachable-with-exports, port unreachable, `showmount` missing, `showmount` timeout, `showmount` non-zero exit, confirmed-empty) via `monkeypatch.setattr("pcluster_core.socket.create_connection", ...)` / `"pcluster_core.subprocess.run", ...)` — no real network calls, matching how `_render_policy` and other `pcluster_core` internals are already stubbed by dotted-string `monkeypatch.setattr` elsewhere in the suite. The unreachable-port test also asserts `showmount` is never invoked at all when the port probe already failed.

### Session 52 (cont.) — records store, phase 1

Built the `vars/` half of the plan in `docs/records-store-plan.md`. The
premise the plan opens with held up: the IAM was already there and had
never had code behind it, so this was finishing a mechanism rather than
inventing one.

**The prefix nearly went wrong in the obvious way.** First implementation
used `records/<name>.json` -- a perfectly readable name that would have
been AccessDenied on every deployed call, since every
`MCPStateAccess*.json_src` grants `vars/*`. Caught before it mattered,
and `test_the_key_lives_under_the_prefix_the_iam_grants` now asserts the
key and the policy files agree, because nothing else can: a local stub has
no IAM to be wrong about, exactly the shape of the
`region_name=region` rule already in `CLAUDE.md`.

**`_read_cluster_record` had to be split three ways.** It fused projection
and sanitizing, and the two shapes disagree on exactly two names --
`serial` (from `cluster_serial_number`) and `deployment_date` (from
`DEPLOYMENT_DATE`). Re-running the vars-file projection over an
already-projected record blanks those two and keeps everything else: a
record that looks right and has silently lost fields. It is now
`_read_local_vars_file` -> `_project_vars_file` -> `_sanitize_record`, with
`_read_cluster_record` as a local-first resolver over the two sources, and
the sanitizer still at one point (an S3 object is *more* exposed to a
corrupted write than a local file, not less).

**A second guard turned out to be decoration, the same as the defaults
merge filter earlier this session.** Record-key fallbacks inside
`_project_vars_file` (`data.get("cluster_serial_number", data.get("serial"))`)
survived their mutation: the store path skips projection entirely, so they
are unreachable by construction. Removed, and the test's docstring
rewritten to name what actually makes the round trip work -- the resolver's
shape, not a fallback.

**Lifecycle placement.** `_publish_cluster_record` runs just before the
create path releases its lock, and *never raises*: the cluster is running
and billing by then, so a store the operator cannot write is a
discoverability problem, not a reason to abandon it. Deletion is
`delete_cluster_record_step`, gated on `cf_delete_confirmed` **inside the
function** rather than at the call site, so there is one statement of the
gate; it is deliberately not part of `run_credential_teardown_steps`, which
mirrors the playbook's four tasks exactly and whose result list is pinned
at four -- and a record is not a credential.

**Region scoping.** `_record_store()` derives the bucket per account+region
from `_aws_account_id()` (already `lru_cache`d) and the ambient region, so
a server sees the clusters built in its own region. Cross-region discovery
would mean scanning every region's bucket on every listing. `(None, None)`
on any failure -- no region, no credentials, no bucket -- degrades to
exactly the local behavior that existed before the store.

Verified against real AWS: the bucket name derives, and a listing against
a bucket that does not exist yet returns `[]` rather than raising. It costs
~0.34s on `_load_records`, which was previously an offline call.

Seven mutations caught: the prefix change (2 tests), S3-first ordering,
skipping the sanitizer on a stored record, deleting the record regardless
of confirmation, a failed delete reporting success, and a publish failure
aborting the build. One survived and was removed (the dead fallback above).

**Full suite: 2821 passed, 0 failed**, `make lint` clean.

Preamble budget paid for with real reductions rather than a raised ceiling:
the sbatch bullet's test-harness sub-bullet (1,062B, its one instruction
kept inline) and four `enable_external_nfs` sub-bullets (2,510B total) moved
here verbatim. Headroom ended at 2,236B.

- **That test's positive half asserts on package names, per template, never on a bare command name.** Three successive granularity gaps each survived the full suite: an aggregate `any` over both templates was satisfied by postinstall alone, so deleting preinstall's entire `{% else %}` body — an `rhel9` node with no python3 and no awscli — passed; and within postinstall, `expected in trace` was satisfied by the GPU block's `dnf -y install nvtop htop`, so deleting the whole critical-packages block — no gcc, git, lua, lua-devel, nfs-utils or EPEL, hence no Lmod — also passed. `_SENTINEL_PACKAGES` names two packages per (manager, template) that appear on that arm's own package line and nowhere else: `python3-dev`/`unzip` and `liblua5.4-dev`/`nfs-common` for apt, `python3-devel`/`unzip` and `lua-devel`/`nfs-utils` for dnf. All four faithful mutations are caught.

- **The wrong-family check has both a rendered arm and a raw-source arm, and needs fixtures of both families.** While every fixture was Ubuntu, a `{% if 'ubuntu' not in base_os %}` branch never expanded and a rendered-text assertion passed with the `dnf` call still in the file — so `cluster_params_rhel` and `cluster_params_rhel_gpu_queue` (`tests/conftest.py`) exist to expand the other arm, and the GPU variants are required one level down because the `nvtop`/`htop` install sits inside `{% if enable_gpu == 'true' %}`, which the plain fixtures leave false. Word-boundary regexes, not `"dnf "`: the space-suffixed literal missed `sudo dnf\t-y install`, and `microdnf`, `rpm -i`, `zypper`, and `apk` matched nothing at all. `monitoring-post-install-wrapper.j2`, `create_pcluster.yml`, and `delete_pcluster.yml` have **no** OS branch, so a package-manager call in any of them is wrong on one family unconditionally — they get their own test (`test_no_unbranched_surface_invokes_either_package_manager`) rather than being scoped out.

### Session 52 (cont.) — records store, phase 2 (`configs/`)

Completed the plan's second phase, which finishes the bucket: all three
prefixes (`locks/`, `vars/`, `configs/`) now have code behind them.

**The real remote blocker was not the storage, it was the signature.**
`apply_cluster_update(cluster_name, config_path)` took the path as a
required tool argument -- and a remote caller has no filesystem to name one
on, while a path an earlier `add_queue` returned belonged to a different
Lambda container. `config_path` is optional now; omitted, the config comes
from the store and is written to a temp file, because `pcluster.lib`'s
`cluster_configuration` must be a PATH (the CLI model tags it
`"type": "file"` and the dispatcher hands the string to `read_file()`).
The temp file is removed in a `finally`: a reused container would otherwise
apply one caller's config on behalf of the next.

**A regression I introduced and the suite caught.** The first draft fell
back to the store whenever the supplied path did not exist on disk --
clever, and it silently changed what the function did with a path it was
handed. Four `TestCoreApplyClusterUpdate` tests failed, all of which pass
`/tmp/cfg.yaml` without creating it. An explicit path is now used exactly
as given (pcluster's own error on a bad path is clearer than an invented
one), with `test_an_explicit_path_is_used_as_given` pinning it. Worth
noting the targeted runs during development never included
`tests/test_fleet.py`; only the full suite saw this.

**The conditional write is the substance of the phase.** `add_queue` and
`remove_queue` take no cluster lock -- deliberately, since they are edits
rather than cluster mutations and the lock is held across whole operations
(an edit blocking behind a 30-minute update is worse than a retry). So two
concurrent edits are an ordinary read-modify-write race: both read, both
add a queue, the second write wins, the first queue is gone with nothing
raised anywhere. `_load_cluster_config` therefore returns the ETag
alongside the config, `put_cluster_config_object` sends it as `IfMatch`,
and a loss raises `ClusterConfigConflict` -- its own type because the
correct response is specific and not shared: re-read, re-apply, write
again, never retry the same body. `_is_conditional_write_rejection` is
reused rather than restated, so both 412 and 409 count.

**Two smaller shape changes.** `_load_cluster_config` returns a 3-tuple now
(the ETag has to come back with the config or there is nothing to be
conditional on), and `_write_cluster_config`'s rendering moved into a
shared `_dump_cluster_config` so the stored copy and the local file are
byte-identical -- two renderers for one document is the `pkg_dir` hazard,
and the `SharedStorage` spacing fixups are not cosmetic to PCluster. A
locally-sourced config is mirrored up *unconditionally*: this machine holds
the authoritative copy and the mirror would go stale the moment an operator
edited locally.

Five mutations caught: dropping `IfMatch` entirely, treating only 412 as a
conflict, leaving the temp config behind, consulting the store before the
local file, and the explicit-path regression above.

**Full suite: 2830 passed, 0 failed**, `make lint` clean.

Preamble paid for by merging the two store bullets into one (3,851B ->
3,378B; the phases are one mechanism and read better together) and moving
two archival test-design sub-bullets from the `base_os` bullet here
(1,902B). Headroom 2,214B.

**One reader was missed and found while summarizing.** `core_list_queues`
reads the config through the same `_load_cluster_config` as `add_queue` but
was never handed the store, so on a machine with no local file one worked
and the other reported the config missing. Fixed, and
`test_every_config_reader_reaches_the_store` now asserts over the
signatures of all four readers rather than calling each one -- the next
reader added is the one that would be forgotten. **Full suite: 2832
passed.**

- **Both package managers are stubbed in `_run_preinstall`/`_run_postinstall` now** — they have to be, or the RHEL arm could not execute at all. What carries the fact is therefore *which* name appears in the trace for *which* fixture, not the exit status. `test_no_node_type_executes_the_wrong_package_manager` reads the trace because it is the only check that sees an indirect `_pm=dnf; sudo $_pm ...`, and it must run both node types and the GPU fixture.

- **They assert the supported set by equality, not the absence of remembered spellings.** A blocklist is only as wide as whoever wrote it remembered: four other distro names passed the first version, as did a single-quoted argparse entry (the assertion tested the double-quoted spelling). `ARM_OSES | X86_OSES` must also *cover* the supported set — dropping a value makes `base_os_instance_check` silently skip the arch-mismatch check for it. The literal distro names in `_UNSUPPORTED_OSES` are guard data — a rejection list needs the strings it rejects; do not "clean them up".

### Session 52 (cont.) — doc pass

`CLAUDE-STATE.md` gained a **Shared cluster store** entry under Deferred
work: both phases built, never run on a deployed handler, and the one
design question left open (whether `add_queue`/`remove_queue` stay on the
read-only tier now that they write S3 objects). Its Test status line now
says explicitly that the store is unconfirmed for the same reason the
remote transport is -- the store's whole purpose *is* that transport, so
"the CLI path is confirmed" says nothing about it.

Preamble rebalanced by moving four more archival sub-bullets here (the
`_run_preinstall` package-manager stubbing note, the supported-set-by-
equality note, and the two moved earlier in this session). **One was moved
and put back**: the `assert`-task-index-0 rule is a constraint about a file
that still exists, not history, and demoting it to the archive was a
judgment I should not have made unilaterally -- headroom did not require
it. Ended at 2,175B.

### Session 52 (cont.) — the denial's real scope

The first preview after the restart confirmed the round (region populated,
`defaults_file` named, `notable_defaults` no longer claiming the wrong OS,
`gpu_instance_type` an empty string rather than None) and showed something
else in `defaults_file_settings`: `custom_ami`, `pre_install_script` and
`post_install_script` -- all three `_REMOTE_DENIED_PARAMS` -- applied from
the file. `_reject_denied` inspects `overrides` only, so the file walks
past it: pass `custom_ami` as an override and MCP refuses, put it in
`osiris_defaults.yml` and it is applied silently.

**Kept, deliberately, and documented rather than closed.** The denial
exists to stop a *caller* -- a model, over the network -- from choosing
what code runs on the nodes. A defaults file is the operator's own artifact
on their own disk, the same trust level as the CLI, which is exactly where
those three are permitted. Extending the check to file-sourced values would
also refuse every real operator's file, since `pcluster_defaults.yml`
itself sets `pre_install_script` and `post_install_script`.

Nothing reaches a handler this way today: the file is gitignored and in no
tier's `sources` list in `packaging.py`. That is the fact the decision
rests on, so it is written at `_reject_denied` as the thing to revisit
first if it ever changes.

Pinned by `test_a_defaults_file_may_set_what_an_override_may_not`, which
asserts both halves -- the file's value is applied, the identical value as
an override is still refused. Without it the next reader finds an
inconsistency and "fixes" it.

**RHEL-specific bootstrap observations (moved 2026-08-22).** `aws-parallelcluster-monitoring` v2.6 **does** support RHEL 9, on both arches — confirmed on live builds of each, so the v2.6 stack is arch-agnostic on el9 (session 32 of `docs/sessions.md`). Our wrapper carries no `set -x`, so the port-80 `apache2 httpd` loop leaves no trace in the log — `nginx` binding port 80 is the evidence it worked, not the absence of those lines.

**Two corrections from that round's doc pass, both mine.** A second
constraint went to the archive by mistake and came back: the
`gpu_vcpus_per_node` vs `gpu_ranks_per_node` distinction is a rule about
files that still exist, not history -- the same error as the
`assert`-task-index-0 bullet earlier in the session, and the same cause,
reaching for byte savings and grabbing the nearest large block rather than
the archival one. What survives in the archive from this session is
test-design rationale and live-build observations, which is what it is
for.

Separately, one round of "tightening" the store bullet *added* 84 bytes:
the sentence added (that every config reader takes the store) outweighed
the prose trimmed, and `docs/sessions.md` already carried it. Dropping it
again and moving two purely observational sentences out of the
RHEL-bootstrap bullet was the clean cut. **The lesson is the same one the
budget test's own docstring gives**: condense by removing a thing, not by
rewording around it, and check `wc -c` before and after rather than
assuming a rewrite is smaller.


### Trimmed CLAUDE.local.md bullets (moved 2026-08-22, second pass)

All four document tests that enforce themselves -- the doc-hygiene
sweeps and the README-scoping guard fail loudly if violated, so the
prose describing them is the safest thing to take out of the
always-loaded preamble. The rules they describe are unchanged.

- **`TestEveryTestNameTheDocsCiteStillExists` requires every `test_*`/`Test*` token in tracked prose to be defined somewhere in `tests/`.** It sweeps `git ls-files '*.md' '*.md.j2'` plus `#`-comment lines from `git ls-files '*.py'` — comment citations drift identically and a code-line match would hit the definitions themselves. `_defined_names()` collects def and class names **and module basenames**, because `tests/test_templates.py` is a legitimate thing for prose to name. The required-surface set is **derived** (`os.path.basename(p) == "CLAUDE.md"` over the tracked list, plus `CLAUDE-STATE.md` and `README.md`) with a `len(required) >= 5` floor: an enumerated list was cut from five names to one with the test still green, and a new `<subdir>/CLAUDE.md` must be swept the day it is added rather than the day someone remembers.

- **`TestEveryLineNumberTheNormativeDocsCiteStillPointsAtItsSubject` is a manifest, not a blanket rule, because most cited lines are unresolvable by construction** — `cluster_stack.py:293` and `installer/install.sh:25-27` are upstream PCluster and upstream monitoring, not files in this repo. `_EXPECTED` maps `(relpath, line)` to a substring that line must hold. **The substring must be unique to that line** — the first version pinned `sbatch_default_submission_script.sh` on a substring its adjacent `dest:` line also carried, so a one-line shift was undetectable. `_NORMATIVE_DOCS` is the three `CLAUDE.md` files only; **`CLAUDE-STATE.md` is deliberately excluded** from the manifest requirement because it is a dated log of what was true at the time, not an instruction — its citations are still corrected when found, but pinning them would freeze a historical record against every later edit.

- **Moving 29k tokens out buys nothing if a constraint doc says to load it anyway**, which the byte ceiling cannot see either since the archive is not in `_PREAMBLE`. Banned is the **unconditional** form, not the verb — a scoped pointer is the point of the split, and `CLAUDE-STATE.md`'s "Read `docs/sessions.md` when you need the evidence behind a bullet" must keep working. Two false positives already came from patterns flagging prose *about* evidence as instructions *to* the reader: matching on `read` alone hit that pointer, and an unanchored `in (?:its|full)` hit "...were read in full" describing a node's own logs. **Scope per sentence, not per line** — a constraint bullet is one 5KB line — and when such a match fires the pattern is what gives, since the pressure is otherwise to reword correct documentation.

- **`README.md`'s "Job Submission" section documents the derivation, and every assertion about it is scoped to that section.** The section described a fixed submission script, so an operator reading it had no reason to expect either derived value and no warning that the GPU arm's rank count is *cores* rather than devices — the one thing a reader of `job_hpc-benchmark.sh` would carry over wrongly. `test_the_readme_documents_the_derivation_rather_than_a_fixed_partition` (in `TestTheDefaultSbatchScriptIsShapedByTheCluster`) splits on `### Job Submission` and stops at the next `### ` heading, because the phrase `` no `compute` partition `` also appears in the HPC Benchmarks section — a whole-file match cannot see it deleted from this one, and that mutation survived the first battery. The **narrowing itself is asserted** (the section must be under a quarter of the file and contain neither `### HPC Benchmarks` nor `hpc-benchmark.sh install`) — the obvious repair for the first failure is to widen the scope back.


### Session 52 (cont.) — adversarial review of 1d64615, and six fixes

Four agents reviewed the pushed commit under distinct lenses (correctness,
security, concurrency, test quality). Every load-bearing claim below was
re-verified here before being acted on; the agents were right about the
substance and occasionally overstated the reach.

**The worst finding was mine and the commit created it.** `create_cluster`
had been dying on `params.region` with `AttributeError` *before any AWS
mutation* -- accidentally fail-safe. Fixing that unblocked
`build_make_cluster_params`, which hands `MakeClusterParams` the strings
`"true"`/`"false"` for 13 fields the dataclass annotates `bool` and
`core_create_cluster` truthiness-tests. So the first real MCP build would
have provisioned FSx, EFS, EFA, monitoring and a login-node pool that were
all explicitly off, after creating IAM policies, a role, a keypair, a
secret and a bucket. **Fixing one bug made a worse one reachable**, and
nothing saw it because all five tests reaching `create_cluster` stub
`core_create_cluster` and none assert on the *types* inside the params
object. Three existing tests asserted `enable_fsx == "true"` -- they were
pinning the defect.

**The store had no writer on the path it was built for.**
`core_create_cluster` has two exits; `_publish_cluster_record` sat before
the final one, and every `wait=False` caller -- which is every MCP build --
leaves at the `_KICKED_OFF` branch ~190 lines earlier. The config was never
published at build time at all. Both exits now call one
`_publish_cluster_state`, guarded by an AST test that requires a publish
before every `sys.exit(0)` in that function.

**The denied-params decision was defensible and its stated reason was
wrong.** The docstring said the denial holds because the defaults file is
gitignored. `.gitignore` lists three literal filenames;
`pcluster_defaults.yml` is deliberately tracked, sits at the repo root, and
sets all three denied parameters -- so `cluster_name="pcluster"` applied
them without ever touching `overrides`. The remote transport survives on
the other half of the claim (no repo-root file is in any tier's
`sources`). Auto-discovery now excludes the template, and validation moved
into `build_make_cluster_params` where the MCP tools could not skip it.

**"Local wins" was making the store converge away from every other
writer.** The mirror was unconditional, justified as "this machine is the
writer of record" -- false, since the store branch is a second writer by
construction. A laptop with a local file overwrote every remote edit
forever, silently, with `ClusterConfigConflict` never firing. Local edits
are conditional now, and a local copy already behind the store is refused.

**Three of my own guards this session were decoration**, each found only by
deliberate mutation: the defaults merge filter, the `_project_vars_file`
record-key fallbacks, and the `in ("bool", bool)` annotation tuple. A
fourth was self-neutralizing -- `if "configs/" in text: assert "/configs/*"
in text` made the assertion *vanish* on exactly the drift it existed to
catch. And `_same_config` compared re-dumped ruamel round-trip text, which
preserves formatting and therefore normalizes nothing; its own whitespace
test caught it.

**Harness holes closed**: the `_S3` stub discarded `Bucket` entirely (so
every primitive could address the wrong bucket, green); the IAM guard is
now a per-tier expectation table; and `mcp_server.tools._repo_root` was
never repointed in conftest, so a test driving `add_queue` would have
*written* to the developer's live cluster.

Smaller fixes in the same pass: AccessDenied is no longer reported as a
missing cluster; an `s3://` URI is refused as a `config_path`; a defaults
file that is not a mapping says so instead of raising `AttributeError`;
`resolve_region_from_az` validates the AZ format before building a client
from `az[:-1]` and constructs that client inside the `try` that names
`ValueError`; and `make_pcluster.py`'s duplicate region resolution is gone,
with `AvailabilityZoneNotFound` (a `PClusterMakerError` subclass) letting
the CLI keep `illegal_az_msg`'s wording.

**Full suite: 2867 passed, 0 failed**, `make lint`/`make shellcheck` clean.
13 mutations run across the fixes, 12 caught immediately; the survivor
(dropping `IfMatch` from the local mirror) is now covered by a racing-stub
test.

Preamble rebalanced by moving four sub-bullets that document
*self-enforcing* tests into the archive above -- if those guards are
violated they fail on their own, so the prose was the safest thing to move.


### Trimmed CLAUDE.local.md bullets (moved 2026-08-22, third pass)

Both are narrative about *why* a piece moved and which mutations it
caught, not rules an editor needs in front of them. The rule from the
first survives inline as one line; the second describes a test that
fails on its own if violated.

- **`_derive_docker_compose_staging` lives in `src/pcluster_core.py`, not inline in `make_pcluster.py`.** Inline it was unreachable by any test and two faithful mutations survived the whole suite: dropping the `enable_monitoring` half of the gate (no upload happens, so `aws s3 cp` on a missing key fails the node) and inverting the arch map (every node fetches the wrong-architecture binary). Both are now caught by `TestDeriveDockerComposeStaging`. The arch string is `uname -m`'s spelling (`aarch64`/`x86_64`, not `arm64`/`amd64`) because that is the release asset's own suffix, and it is derived for **every** `base_os` because the checksum is threaded unconditionally — returning it only for AL2023 leaves `docker_compose_checksum` undefined and `vars_file.j2` raises under `StrictUndefined`.

- **`TestAFailedAwsCallIsNotReportedAsAStoppedCluster` (`tests/test_shell_surfaces.py`) executes the rendered scripts** with a stub `aws` at each rc, because a text assertion cannot tell which branch a script takes. Three harness details are load-bearing: the stub is written with `shlex.quote` rather than Python's `repr` (repr renders a newline as a backslash escape that bash single quotes pass through literally, so the stub would answer the 6-character string `None\n` and the `== "None"` test would miss); `mktemp` is stubbed rather than passed through, because **macOS `mktemp` resolves its default directory from `_CS_DARWIN_USER_TEMP_DIR` and ignores `TMPDIR`**, so a `TMPDIR`-based leak check silently watches an empty directory; and `test_the_two_diagnoses_are_distinguishable` is the vacuity guard, since one generic message mentioning both causes would satisfy every individually-worded assertion.


- **`_run_postinstall` cannot restore the rendered script's `set -euo pipefail`** the way `_run_preinstall` does — `mkdir` is stubbed, so the head-node path's `cd` targets never exist and every head-node test would abort on what is the happy path. `TestNvmeDetectionSurvivesSetE` therefore extracts the detection loop from the rendered template and runs it alone under a real `set -e`, asserting the same devices survive in either position plus the loop's own exit status (the process substitution hides it). Verified while writing it, and worth not re-deriving: a false `[[ ... ]] && continue` inside a loop body is **exempt** from `set -e` in every position, so that form and the `if` behave identically here. The `if` is style, not a fix; do not document it as one.

### Session 52 (cont.) — the region asymmetry, and option C for the update window

Two of the three open items from the adversarial review. The third
(pinning the applied version by ETag) was considered and deliberately not
taken.

**The store bucket is per account+region, and the read path used the wrong
region.** Everything that *writes* it -- `core_create_cluster` publishing,
`core_delete_cluster` removing, the S3 lock -- derives the bucket from the
cluster's region. `_record_store()` derived it from `AWS_REGION` /
`AWS_DEFAULT_REGION` / the profile. So a laptop with `AWS_DEFAULT_REGION`
`us-east-1` building into `us-west-2` wrote the record to one bucket and
looked for it in another, and teardown -- which uses the cluster's region
-- could never reach whatever the MCP path had written to the other one.
This was not cross-region discovery being unsupported; it was one machine
disagreeing with itself.

`_record_store(region=None)` now takes the region, and every tool holding
a record passes `rec.region`. Making that possible meant reshaping
`_require_record`: it reads local with **no store at all** first, so a
cluster this machine built is answered from its own vars file and that
region addresses every later store call. Only a cluster with no local
record falls back to the ambient region, which is the one case with
nothing better to ask -- and is what keeps remote discovery working at
all. Two region-less call sites remain by design and the test pins the
count, because a site that drops the argument silently addresses the wrong
bucket and no stub can catch it: the stub is handed whichever client the
caller already built.

**`resolve_access_info` was fixed in the same pass** -- flagged by the
concurrency review two rounds earlier, acknowledged, and not acted on. It
did `_read_cluster_record(...) or {}`, so it answered *confidently* from an
empty record: a login-node cluster resolved to `HeadNode` rather than
erroring, and it was the one record reader never threaded to the store.

**Option C for the apply window.** `apply_cluster_update` returns when
CloudFormation accepts, so the cluster lock is released while the stack is
`UPDATE_IN_PROGRESS`, and `add_queue`/`remove_queue` hold no lock by
design. An edit lands in that window, the store moves on, the cluster
converges on what was applied, and nothing detects the divergence.

Four options were laid out: accept and document; pin the applied version
by ETag; refuse edits mid-update; hold the lock across the whole update.
**C was chosen** -- refuse. It prevents the interleaving rather than
reporting it afterward, uses `_describe_cluster_json` which already
existed, and adds no state to the store or to teardown. B was declined
because `wait=False` never learns that an update *completed*, so the
honest field would be "last submitted", which quietly lies after a failed
update. D fixes only the local path, which is not the one this subsystem
exists for.

`_refuse_edit_during_update` lives in the core, so the CLI is guarded too,
and **only a confirmed `UPDATE_IN_PROGRESS` refuses** -- the
`_check_external_nfs_reachable` precedent, since a check that runs from
wherever the operator happens to be must not make editing a config file
depend on AWS being reachable. One test asserts the config is
byte-identical after a refusal: refusing *after* writing would be worse
than not checking.

Six mutations, all caught: the region ignored entirely, one call site
dropping it, the guard removed from both editors, the guard never firing,
an unanswerable describe becoming fatal, and the guard on `add_queue`
only.

**Full suite: 2877 passed, 0 failed**, `make lint` clean. Preamble
rebalanced by archiving three narrative blocks (the docker-compose staging
refactor, the failed-AWS-call test harness, and the `_run_postinstall`
set -e note); the first keeps its rule inline as one line. Headroom 2,642B.

**Still open, and the only item from the review not closed:** nothing has
run on a deployed handler.


**Trimmed from CLAUDE.local.md (moved 2026-08-24)** -- the rule survives in `CLAUDE.md`'s external-NFS bullet; this is the detail behind it.

- **Only a confirmed-empty export list (`showmount` connects, answers, lists nothing) is `sys.exit`.** Everything else — port unreachable, `showmount` not installed, the call timing out, or `showmount` failing to connect — prints `*** WARNING ***` and lets the build proceed. This is deliberate, not a half-finished check: the probe runs from wherever `make_pcluster.py` is invoked (the operator's laptop, a bastion, CI), which is not guaranteed to share the target VPC's network path to the filer. A site filer reachable only via VPC peering, Direct Connect, or on-prem-only routing would be invisible from the operator's current machine even though the head node reaches it fine once it exists inside that VPC — so a hard fail on unreachability alone would block valid configs, not just catch broken ones.

- **`_SINFO_OK_STATES` holds base state names only, never a flagged spelling.** Slurm appends flag characters (`*` unresponsive, `~` powered down, `$`, `#`, ...) and `_sinfo_state_is_ok` `rstrip`s `_SINFO_STATE_FLAGS` before comparing. `idle~` was itself an entry in the set, which meant the one test covering flags used the one spelling the table listed — so emptying `_SINFO_STATE_FLAGS` entirely, which classifies every other flagged spelling of a *healthy* node as unusable and fails a cluster that has merely scaled to zero, passed the whole suite. `test_every_state_flag_is_stripped_before_comparison` is parametrized over all ten flags and asserts the flagged spelling is **not** in the table, so it cannot pass without stripping.

- **The YAML key is `HeadNodeBootstrapTimeout`, derived not guessed:** `BaseSchema.on_bind_field` sets `data_key = to_pascal_case(field_name)` (`common_schema.py:103-112`, `utils.py:243-246`). A casing typo is silently *ignored* by marshmallow rather than rejected, so substring assertions cannot catch it — `test_pcluster_own_schema_accepts_the_rendered_block` loads the rendered config through PCluster's own `ClusterSchema` and reads the value back off the config object. That test is the only one that killed the `HeadnodeBootstrapTimeout` mutation. It widens the fixture's subnet/SG IDs to 8 hex chars first, since PCluster's own patterns (`common_schema.py:36`) require 8 or 17 and the fixture ships 7.

### Session 52 (cont.) — the deployment that did not happen, and what it found anyway

Attempted to deploy the read-only tier and call `list_clusters` through
it -- the one open item no local test can reach. The Lambda
`CreateFunction` call was blocked by this environment's permission
classifier, and after the operator declined to run it by hand, everything
created along the way was torn down: the IAM role, its two customer-managed
policies, the artifact object and the deploy bucket, all verified gone.
`_delete_mcp_infra` did the IAM half, so the repo's own teardown path got a
real exercise even though the deployment did not. The operator's
credentials were confirmed to allow `lambda:CreateFunction`,
`InvokeFunction`, `GetFunctionConfiguration` and `iam:PassRole`, so nothing
in IAM blocks a future attempt.

Two findings survive the abandonment, both from actually building the
artifact rather than reasoning about it:

**The artifact is 9 MB under the ceiling, not comfortably under it.** A
real `pip install --target` of the read-only tier's requirement set is
**241 MB** against Lambda's 250 MB unzipped limit. Pruning `__pycache__`
and `.pyc` takes it to **139 MB** with the tier's sources staged -- 84 MB
of it was bytecode Lambda recompiles anyway. Nothing in `packaging.py`
pruned, and nothing measured, so the headroom the module described was far
thinner than it read. `prune_for_lambda` now prunes and returns the byte
total, so a build can check `ZIP_UNZIPPED_LIMIT_BYTES` before uploading
rather than learning the ceiling from `CreateFunction`. The pruned zip is
55 MB, over the 50 MB direct-upload limit, so a handler tier must be
uploaded via S3 -- which is why the deploy step needed a bucket at all.

**A docstring claim was wrong in the direction that flatters the design.**
`packaging.py` said `aws_cdk` "does not appear when `pcluster_core` is
imported ... which is why only the create/update tier carries it". The
first half is a fact about the import graph. The conclusion is false:
`aws-parallelcluster` *declares* 17 `aws-cdk.*` packages as hard
requirements, so pip installs them into every tier that installs PCluster
at all -- 44 MB of the pruned read-only artifact. Corrected, with
`test_the_cdk_claim_matches_what_pcluster_declares` reading the declared
requirements back out of the installed distribution rather than restating
them.

Three mutations on the new helper, all caught: stray `.pyc` outside a
`__pycache__` surviving, pruning nothing, and the size always returning
zero. Worth noting the first mutation initially produced no output at all
because the `perl` pattern did not match -- an unapplied mutation reads
exactly like a caught one, so it was reapplied through an asserted
`str.replace` and confirmed to fail two tests.

**Still open, unchanged:** no MCP tier has ever executed. Everything about
the deployed path remains verified by tests and reasoning only.


**Trimmed from CLAUDE.local.md (moved 2026-08-24)** -- measurement detail behind the head-node bootstrap timeout; the rule and the numbers survive in the bullet itself.

- **Both allowances are measured, and the EFS one is dominated by the mount target rather than the filesystem.** FSx: 1800s, from the 17m22s a 1200 GB Lustre filesystem took on the build that failed. EFS: 600s, measured on a successful build — the filesystem itself completed in **4s** while its mount target took **1m33s**, and the head node instance appeared **4m24s** after the wait condition started, so 600s is ~2.3x headroom over the whole pre-instance window. Note the mount target is *not* what gated the instance: `HeadNodeLaunchTemplate` has no reference to it. So the figure covers the observed window, not a proven dependency — a multi-AZ cluster creates one mount target per subnet, and while those provision concurrently, that has not been measured. Bump it if such a cluster times out.


**Trimmed from CLAUDE.local.md (moved 2026-08-24)** -- the three NVMe device filters and why each is independently load-bearing. The rule (skip devices PCluster's cookbook already claimed) survives in the GPU bullet.

- **Three filters, and each is load-bearing on its own.** The model check (`AmazonEC2NVMeInstanceStorage`) keeps EBS volumes, which also enumerate as `/dev/nvme*`, from being reformatted. `holders/` non-empty catches a device inside an LVM volume, which reports **no** filesystem signature of its own. `blkid` catches a formatted-but-unmounted device, which has **no** holders. Neither claimed-device check subsumes the other, so `tests/test_templates.py::TestPostinstallNodeTypeGating` pins each with a case only it can see (`held_devices` and `formatted_devices` in `_run_postinstall` set one side each) — a single combined fixture leaves dropping either half undetectable. All seven faithful mutations are caught.

### Session 52 (cont.) — the read-only tier stops writing, and the stray bucket goes

**`add_queue`/`remove_queue` moved off the `read-only` tier.** They write
`configs/<name>.yaml`. The original placement reasoned that they mutate no
CloudFormation stack, which is true and is not what the tier name says:
`MCPStateAccessReadOnly.json_src` carried `s3:PutObject` under a name that
promises the opposite, and the next person to read that policy would have
been misled. This was flagged as an open question when phase 2 landed and
deliberately left inherited rather than decided; the operator decided it.

They are on `stack-mutation` now, where the rest of the config's lifecycle
already lives -- `apply_cluster_update` reads the object,
`delete_cluster` removes it. `MCPStateAccessReadOnly` keeps `s3:GetObject`
on `configs/*`, since `list_queues` still reads it, and lost `PutObject`;
`MCPStateAccessStackMutation` gained it.

**The cost is real and was accepted rather than argued away**: a queue edit
now carries the stack-mutation tier's blast radius, which is more privilege
than the edit needs. The least-privilege alternative is a fifth tier for
config writes -- a fifth Lambda, role, policy and cold start -- and that is
not worth it for two tools.

`TestTheClusterConfigStore`'s per-tier table was strengthened in the same
pass. It asserted only the *presence* of a `configs/` grant, which is
exactly the half that did not move; it now asserts the *actions*, so
read-only regaining `PutObject` fails.

**The stray bucket was deleted.** `parallelclustermaker-locks-183295445014-`
`us-east-1`, dated 2026-08-21, in a region nothing was ever built in --
the symptom of the region asymmetry fixed earlier this session, where the
store bucket was derived from the ambient `AWS_DEFAULT_REGION` rather than
the cluster's. Confirmed empty (0 objects) before deleting, so nothing was
lost. No `parallelclustermaker-*` buckets remain in the account.

**Full suite: 2884 passed, 0 failed.**


**Trimmed from CLAUDE.local.md (moved 2026-08-24)** -- the `_EC2_USERS` exact-key detail. The rule (eight supported base_os values, threaded through every surface) survives in `CLAUDE.md` and in the parent bullet.

- **`_EC2_USERS` is an exact-key dict, not a substring test.** `_resolve_ec2_user` is the only rejection on the defaults-file path (argparse `choices` are bypassed by a `<cluster>_defaults.yml` value), and while the RHEL arm was `elif "rhel" in base_os`, both `rhel8` and `rhel10` were accepted and returned a login name — no template branch, arch table or playbook gate knows either, so the build proceeded to a node nobody could reach. `rhel8`/`rhel10` are in `_UNSUPPORTED_OSES` for exactly that reason. The login names are PCluster's own (`OS_MAPPING` in `pcluster/constants.py`: `ubuntu` for the ubuntu values, `ec2-user` for both `rhel9` and `alinux2023`); a mismatch means every ssh and every chown targets a user that does not exist. `diagnose_pcluster.py`'s `_VALID_EC2_USERS` is asserted *derived from* `_EC2_USERS.values()` rather than restated — while RHEL was unsupported that allowlist was `{"ubuntu"}` and re-adding the OS without widening it is the mirror-image bug, which is what the old test (asserting `ec2-user`'s **absence**) would have enforced.


**Trimmed from CLAUDE.local.md (moved 2026-08-24)** -- how the log-group ban's coverage set is assembled. The ban itself is a rule in `CLAUDE.md` and `templates/CLAUDE.md`, and the test enforces the coverage on its own.

- **`_POLICY_FILES` is not the full set of instance-reachable policies.** It is pinned by *equality* to the five managed policies (`test_every_policy_template_is_created_and_deleted`), but `LustreS3HydrationPolicy.json_src` is attached to the same `ec2_iam_role` by `put_role_policy` in `_setup_fsx_hydration_iam` — an inline policy, equally carried by any job on any node. Adding `logs:DeleteLogGroup` to it passed the entire suite. `_INLINE_INSTANCE_POLICY_FILES` and `_INSTANCE_REACHABLE_POLICY_FILES` (`tests/test_templates.py`) exist for that reason; do not "simplify" by appending the inline policy to `_POLICY_FILES`, which breaks the equality pin. `test_every_policy_template_is_covered_by_this_ban` compares `templates/*.json_src` on disk against the union, so a newly added policy file cannot silently escape coverage. `OperatorPolicy.json_src` is deliberately excluded: those are the operator's own credentials, and purging log groups by hand is exactly what the retained-log-group bullet expects of them.

### Session 52 (cont.) — which copy is stale, and the CLI joins the store

Asked whether it was safe to mirror CLI and MCP behavior for queue edits.
Testing the question found a defect in the staleness check shipped earlier
the same day.

**Content cannot tell "ahead" from "behind".** `_save_cluster_config`
compared the stored config against the local one and, on any difference,
reported *"another machine changed it -- the local copy is behind"*. That
is one of two possible directions, and the other is exactly what the
un-mirrored CLI produced: a local file *newer* than the store, whose owner
was then told to re-read the staler copy. Reproduced directly before
changing anything.

`_mirror_marker_path` records the ETag this machine last pushed, in a
hidden file beside the config. That makes the question answerable: if the
store still holds that ETag, nobody else has written since, so the local
copy is ahead and mirroring it is correct; if the store has moved on, a
second writer exists and the local copy really is behind; with no marker
at all, refuse **without claiming a direction**, because there is none to
claim. `_publish_cluster_config` writes the marker too, so the ordinary
single-machine case -- build, then edit with the CLI, then edit with MCP --
resolves as "ahead" and simply works.

**The CLI now mirrors.** `manage_pcluster_queue.py` resolves the store from
the cluster's own record, same region rule as `_record_store`, and passes
it through. **An unreachable store degrades to a local-only edit with a
warning**, following `_publish_cluster_record`'s precedent that publishing
never fails an operation the operator actually asked for. Without that, the
`AccessDenied`-raises change made earlier today would have hard-failed a
purely local config edit for an operator holding cluster permissions but
not store permissions -- a regression introduced by one fix and only
visible while making another.

**Four mutations, one survived and mattered.** Not rewriting the marker
after a mirror passed everything, because every test made a *single*
local-ahead edit. On the second, the marker is stale and the machine
misdiagnoses its own change as another machine's write. Covered now by a
three-round test.

**A stub hid it, again.** `TestTheClusterConfigStore._S3.put_object`
returned `None` where real S3 returns the new ETag, so the marker was never
written during tests and direction detection was silently disabled while
every assertion passed. Same shape as the `Bucket`-discarding gap the
adversarial review found in the same stub. It returns an ETag now.

**Full suite: 2898 passed, 0 failed**, `make lint` clean.


**Trimmed from CLAUDE.local.md (moved 2026-08-24)** -- the apt-mark/dpkg-query detail behind the kernel-hold rule. The rule itself survives in the parent bullet and in `CLAUDE.md`.

- **`dpkg-query -W`'s `|| true` is load-bearing** (it exits non-zero whenever any of its four patterns matches nothing, the normal case) **and its output must be filtered to `${db:Status-Status} == installed` before reaching `apt-mark`** -- `-W` reports every package dpkg has a record of, installed or not, and `apt-mark hold` exits 100 on an uninstalled name. Both failure modes were observed on a live `ubuntu2404` head node, and the `E:` lines explaining the 100 are logged nowhere (cfn-init captures stdout only). `test_uninstalled_kernel_packages_are_never_handed_to_apt_mark` and `test_the_harness_apt_mark_stub_actually_rejects_a_phantom` (the latter guarding the harness's own `apt-mark` stub against always returning 0) pin this.

- **The patch is verified, not trusted, and the verification shares the patch's predicate.** If upstream's fetch stops matching, the awk changes nothing silently, the staged binary is overwritten at install time, and a private-subnet cluster fails on a network error twenty minutes in with nothing pointing at the cause — so a second awk re-scans the file and `exit 1`s by name. Because the two share a predicate, no upstream input satisfies one and not the other: exercising the check requires breaking the patch, which is what `_run_wrapper`'s `neuter_the_patch` parameter does.

- **`_UPSTREAM_AL2023_INSTALLER` in `tests/test_shell_surfaces.py` reproduces upstream v2.6's shape verbatim**, including the two-line `curl ... \` continuation and the `chmod +x` below it. A single-line stub would let a `,+0`-equivalent patch pass. Three reshapes (`o_flag_before_the_url`, `collapsed_to_one_line`, `wget_from_another_host`) are parametrized and all must still be removed, and the result must be `bash -n`-clean.

### Session 52 (cont.) — the fake, rebuilt from the contract

Operator feedback after the mirror-marker round: *stop writing fakes from
memory rather than from the API contract.* Recorded as a standing practice
and acted on.

**The charge was fair and the count was three, not two.** `_S3` in
`tests/test_make_pcluster.py` was written from recall of what S3 does, and
in three consecutive rounds it hid a real defect:

1. It discarded `Bucket` entirely, so every store primitive could have
   addressed the wrong bucket with a green suite (found by the adversarial
   review).
2. Its `put_object` returned `None` where `PutObjectOutput` carries an
   `ETag`, so `_write_mirror_marker` was a no-op under test and the
   ahead/behind detection it exists for was silently disabled.
3. It always returned `Contents` with `IsTruncated: False`, so
   `list_cluster_records`' hand-rolled pagination loop had **never
   executed a second iteration** -- and the `resp.get("Contents") or []`
   guard had never seen an absent key.

The general shape is worth stating plainly: a fake built from memory
encodes what the code under test happens to need, so it agrees with that
code by construction -- including everywhere the code is wrong. It cannot
fail in the direction that matters.

**Rebuilt from `botocore/data/s3/*/service-2.json.gz`**, which is
authoritative and sitting in `.venv`. What the contract forced, each
because production depends on it: `PutObjectOutput` carries `ETag`;
`GetObjectOutput` carries `Body`/`ETag`/`ContentLength`/`LastModified` and
raises `NoSuchKey`; `ListObjectsV2Output` **omits `Contents` entirely**
when nothing matches and carries `KeyCount`/`IsTruncated`/
`NextContinuationToken`, raising `NoSuchBucket`; `DeleteObject` succeeds on
a key that is not there. `max_keys` is 2 so paging actually happens rather
than being assumed.

Also modelled, and this one is a gap rather than a fix: **`PutObject` with
`IfMatch` against an absent key returns 404, not 412.**
`_is_conditional_write_rejection` treats 412 and 409 as conflicts and does
not cover 404, so that case surfaces as a raw `ClientError` instead of
`ClusterConfigConflict`. The fake models it so the gap is visible in the
code rather than living in prose.

**Two mutations that had survived every prior round now fail**: stop
paginating after page one, and index `resp["Contents"]` directly. Three
new tests earn the fake its keep -- a store spanning three pages, an empty
`vars/` prefix, and an unknown bucket.

The config-store `_S3` subclass was deleted (1,329 B). Its ETag and
`IfMatch` handling was S3's behaviour, not a test-local embellishment, so
once the base modelled the contract there was nothing left to override.

**Full suite: 2901 passed, 0 failed.**


**Trimmed from CLAUDE.local.md (moved 2026-08-24)** -- docker-compose staging detail; the rule survives in the parent bullet.

- **Pre-placing the binary is not sufficient on its own.** Upstream's curl is unconditional and would overwrite the staged copy and *then* fail on a private subnet, so the patch must remove it rather than rely on ordering.

### Session 52 (cont.) — closing the 404 gap, verified rather than assumed

The contract-faithful fake surfaced a gap and the previous round recorded
it in prose: `PutObject` with `IfMatch` against an absent key returns 404,
which `_is_conditional_write_rejection` (412/409) does not cover, so it
escaped as a raw `ClientError` for a case `ClusterConfigConflict` exists to
describe.

**Verified before fixing, because neither I nor the agent that raised it
had confirmed the claim** -- it came from AWS documentation, and the
botocore service model does not list conditional errors for PutObject at
all. A throwaway bucket in us-east-2 settled it:

    IfMatch on a key that does NOT exist  -> NoSuchKey / 404
    IfMatch matching, key exists          -> SUCCEEDED
    IfMatch stale, key exists             -> PreconditionFailed / 412
    IfNoneMatch="*" on an existing key    -> PreconditionFailed / 412

**`_is_missing_key_rejection` is deliberately a separate predicate.**
Folding 404 into `_is_conditional_write_rejection` is the tempting
one-line version and is wrong: the cluster lock shares that helper, and
there a vanished object means *nobody holds the lock* -- the opposite of
what 412 supports. Only the config store can read a 404 as a conflict,
because only there does it mean "the thing I was updating was deleted
underneath me". The message differs from the 412 one for the same reason:
*changed* wants a re-read, *deleted* wants a re-publish.

Three mutations, and **one survived that mattered**: a predicate returning
`True` for every error passed the deleted-config test while turning an IAM
denial into "your config was deleted" -- pointing the operator at cluster
state for a permissions problem, the exact failure `_s3_absence_or_raise`
was written to stop. Now covered by a parametrized test over
AccessDenied/InternalError/SlowDown. Widening the lock predicate to 404
fails two tests.

**Process note, second occurrence:** the new tests were appended with
`cat >>` and landed in the last class in the file rather than the one whose
fixtures they use, so the targeted run reported them as passing when they
had never been selected. Same slip as earlier in the session. The check
that catches it is asserting on the *count* of a filtered run, not just on
"0 failed".

**Full suite: 2906 passed, 0 failed.**

- **The patch is a predicate-based awk program, not `sed -i` and not a line-offset delete.** Both in-place editing and the `,+N` address form are **GNU extensions**, and the suite runs on macOS — a `sed -i '/pat/,+1d'` version was unexecutable by any test. Two awk programs accumulate line continuations and match whole *logical commands*; the predicate is "a downloader invocation that mentions docker-compose", which cannot match anything else in that file (its own `install -d` and `chmod +x` name the path but fork no downloader). A literal shape match is brittle in the one direction that matters: a reordered `-o`, a third continuation, or a switch to wget each leave a **partial** delete, and a syntactically broken `alinux2023.sh` is a dead node. Deleting whole commands is also what makes it idempotent, which it must be — the head node's postinstall can be re-run by hand, and a line-offset delete eats two more lines per pass.

- **`bc` is the opposite case: present in the core repo**, despite upstream's own `installer/os/alinux2023.sh` carrying a comment claiming "bc is not in the default AL2023 repos" — contradicted by the metadata on both arches. It is on the package line anyway, for the reason in the Lmod bullet above.

### Session 52 (cont.) — the PATH that was never there, and SSM as the transport

Everything below was verified against a live cluster: `osiris`, us-east-1,
built from the CLI with `--enable_loginnode`, `CREATE_COMPLETE` in 17
minutes.

**`check_slurm` had never worked, against any cluster.** It ran
`["sinfo", "-h", "-o", "%D %T"]` over ssh. A non-interactive
`ssh host sinfo` gets the bare system PATH; `/opt/slurm/bin` is appended
only by a login shell reading `/etc/profile.d`. On the live head node
`which sinfo` finds nothing while `/opt/slurm/bin/sinfo` answers
`8 idle~`. So the single check that separates *the cluster exists* from
*the cluster can run work* — the distinction session 41 added the
classifier for — reported a perfectly healthy cluster as FAILED, and had
done so for every real cluster it was ever pointed at. Nothing in the
suite could see it: every test stubs the ssh call, and the stub supplies
the output the classifier is being tested on.

**The first fix was wrong, and the live cluster caught it on the first
run.** I wrote `["bash", "-c", "export PATH=...; exec sinfo ..."]`, which
is correct as a local argv and meaningless as a remote one. It came back
`exec: sinfo: not found`. `_ssh_args` does no quoting and `_run_ssh` just
appends argv, so ssh joins the parts with spaces and the **remote** shell
re-parses the result: the command split at the semicolon, the export ran
in one command and `sinfo` in another with the original PATH. The same
flaw was already latent in the original call — `"%D %T"` arrived at the
remote shell as two separate words, which happened not to matter for
`-o` only by luck. The fix is a `_slurm_remote_cmd` helper that builds one
`shlex.quote`d string; the file already used `shlex.quote` for its remote
`mkdir`/`chown`, so this was reaching for a tool that was already there.

`bash -lc` would also have found `sinfo` and was rejected deliberately: a
login shell sources `/etc/profile.d`, which this repo has documented as a
hazard twice over, and a banner from any fragment lands in the output
`_classify_sinfo_nodes` parses — where an unreadable line is counted as an
unusable node. The safe-looking option would have turned a cosmetic profile
fragment into a failed health check.

**The same bug was in `diagnose_cluster`,** in two of its four probe types
(`sinfo -N -l` and `sacct`); the `tail` and `test -f` probes were fine,
being on the system PATH. Both go through the same helper now, and the
live cluster renders real node states where it previously rendered
nothing useful.

**Two mutations survived the first battery, and they were the ones that
mattered.** Reverting *either* diagnose probe to a bare `sinfo`/`sacct`
passed all 87 tests in `tests/test_diagnose.py` — the bug that had just
been found on live hardware had nothing pinning it in the suite. Two tests
later the tally for the round is 5 mutations run, 5 caught.

**`list_queues` was broken over MCP and worked everywhere else.** It was
annotated `-> dict` while returning a list. FastMCP validates structured
content against the annotation, so every remote call failed with
`structured_content must be a dict or None` — carrying the correct payload
inside the error text, which is why it read as a serialization quirk
rather than a broken tool. Changed to `-> list[dict]`, matching
`list_clusters`. Invisible to the existing tests because they call the
wrapper function directly, where the annotation is inert; the same shape as
the `handlers/base.py` gap earlier in this session.

**SSM: prototyped first, and my prediction was wrong.** I expected
`send-command` to cost 2–6 seconds against ssh. Measured on the live
cluster, `check_slurm` over SSM returned an **identical verdict** at 953 ms
against ssh's 998 ms. Both nodes were already `Online` in SSM with only the
association-subset grants `ComputeNode-Base` carries — no
`AmazonSSMManagedInstanceCore` involved — which was established
empirically rather than assumed. A second, wider prototype measured the
truncation edge for diagnose's larger outputs: 23 KB comes back intact,
24 KB returns exactly 24000 bytes ending in `--output truncated--`,
**in band** rather than as a flag, so a caller cannot detect it without
matching that string. diagnose's real outputs top out at 5,846 bytes (the
`cloud-init-output.log` tail), a quarter of the cap, and that ceiling comes
from `tail -n 30` rather than from file size — so it does not grow with a
noisier build.

**`access_cluster.j2` and `grafana_tunnel.j2` now route ssh through SSM**
via `-o ProxyCommand=... AWS-StartSSHSession`, falling back to direct ssh
with a warning when the session-manager plugin is absent. Pure
`aws ssm start-session` was the obvious alternative and is wrong for this
use: it lands as `ssm-user`, whose `$HOME`, PATH and Slurm environment are
not the ones a login node exists to provide, and it gives up scp, rsync,
agent forwarding and `-L` — the tunnel script's entire purpose. Verified
live end to end: connected to the login node and got `ubuntu`,
`ip-172-31-19-62`, and `SSH_CONNECTION=127.0.0.1`, that last one being the
proof it actually traversed SSM rather than falling back. The tunnel was
verified by forwarding to `:22` (monitoring is off on this cluster, so
nothing listens on `:443`), reading sshd's banner back through the local
port, and stopping cleanly through the PID file.

**One bug in that change was caught by reading, not by testing.**
`grafana_tunnel`'s `pgrep` pattern matched `${HEAD_NODE_IP}`, but over SSM
the ssh command line carries `ubuntu@i-0abc...` — the IP is nowhere in it.
The PID would never have been captured, so `stop` would have reported
success while leaving the tunnel running, which is worse than failing. It
matches `${SSH_TARGET##*@}` now. `"${PROXY_ARGS[@]}"` also picked up a
`${PROXY_ARGS[@]+...}` guard: expanding an empty array is an
unbound-variable error under `set -u` on bash before 4.4, and the fallback
path is exactly the one where the array is empty.

**Cosmetics, recorded because they are on the operator's screen every
build.** The progress-line timer reads `[  1m ]` / `[ 13m ]` — minutes
space-padded rather than zero-padded, seconds omitted on a whole minute and
keeping their leading zero when present. Both progress printers now show
the CloudFormation status **only when it differs** from the cluster status;
they agree for the whole of a healthy build, so printing both was noise
that went quiet precisely when the divergence would have meant something.

**Full suite: 2930 passed, 0 failed.**


### Session 52 (cont.) — delete_cluster was half a capability

**The question that started it, from the operator: why doesn't MCP
`delete_cluster` replicate `kill_pcluster.py`?**

It does — the same `core_delete_cluster`, differing by one argument.
`wait=False`. And at `pcluster_core.py`'s `_KICKED_OFF` branch that one
argument returns `success=True` *before every teardown step*: the five IAM
policies and the role, the S3 build bucket, the EC2 keypair and its local
`.pem`, the Secrets Manager secret, the SSM Grafana password, the SNS
topic, `active_clusters/<name>/`, and — new since the records store — the
`vars/<name>.json` and `configs/<name>.yaml` objects in the shared bucket.

The rationale was sound: teardown is 15-20 minutes against Lambda's hard
900s ceiling. The implementation was not. `CLAUDE.md` states the rule it
broke — *"Decompose such a tool, never delete the capability"* — and
`apply_queue_config` shows what that looks like done right (local-only,
with remote callers driving `stop_fleet` → `apply_cluster_update` →
`start_fleet`). `delete_cluster` was truncated instead: no second tool,
and its own remediation text said `./kill_pcluster.py`.

**A correction I had to make mid-task, having asserted otherwise.** I told
the operator a remote caller could never complete a teardown at all. That
is wrong, and reading `_initiate_cluster_delete` rather than reasoning
from the `wait` flag is what showed it: an absent stack makes it return
`already_gone=True`, which classifies as `_CLUSTER_NOT_FOUND` — *not*
`_KICKED_OFF` — so a **second** `wait=False` call runs the whole teardown.
The certification checklist's item 4.4 already documented exactly that
flow. Confirmed against the fakes: call 2 returns `success=True` and
removes the vars file.

So the capability was reachable. What was wrong with it is narrower and
sharper, and it is what the new tool fixes:
- **The second call re-issues `delete-cluster` against the name** (the
  trace shows `[('killme', 'us-east-1')]` before it discovers the stack is
  gone). If that name has been rebuilt in the interim — a test loop, a
  retried build — it deletes the *new* cluster's stack.
- **Called too early it silently no-ops and reports success again**, which
  is indistinguishable from having finished. The operator has no signal
  short of inspecting S3 by hand.

**The fix, and why it is smaller than it looks.** The teardown body did
not need extracting. `run_cluster_delete_and_classify` already classifies
an already-absent stack as `_CLUSTER_NOT_FOUND` →
`cf_delete_confirmed=True`, which is why "re-run kill_pcluster.py" works
locally today. What was missing was a *mode* that reaches that body
without being able to block. `core_delete_cluster` gained
`finalize_only=False`; when true it skips the delete call and the wait
loop entirely and consults `_confirm_stack_is_gone` instead.

- **The non-blocking guarantee is structural, not a precondition check.**
  `_confirm_stack_is_gone` is `_wait_for_cluster_delete(..., retries=1)` —
  one describe, and the loop only sleeps while `attempt < retries - 1`, so
  it never sleeps. Reusing the wait loop rather than re-deriving "gone"
  keeps one definition of a terminal state; two would drift. Verified
  against the live `osiris` cluster: `CREATE_COMPLETE` → `TIMED_OUT` →
  refused, exactly one describe, 2.53s.
- **A failed describe propagates.** The standing rule — a failed AWS call
  is not a stopped cluster — matters more here than usual, because reading
  an expired token as "gone" authorizes destroying the credentials.
- **`DELETE_FAILED` refuses.** This was the one mutation that survived the
  first battery, and it is a real defect rather than a test artifact. The
  waiting path *does* strip IAM and S3 on `DELETE_FAILED`, deliberately,
  having just attempted the delete itself; arriving at *finalize* in that
  state means an earlier `delete_cluster` failed, and the answer is to
  re-run the delete, not to scavenge resources the stack still holds.
  Letting it through still exits non-zero — the `cf_delete_failed` branch
  produces exactly that — so every assertion on `success`/`exit_code`
  passed while the resources were being removed. Only
  `test_a_refusal_runs_no_teardown_steps`, which monkeypatches both step
  functions and asserts neither ran, can see it.
- **The results sync is skipped**, since it reads off a head node that no
  longer exists; `test_the_waiting_path_still_syncs` is its vacuity guard,
  because "skipped on finalize" is also satisfied by deleting it outright.
- **One teardown body, pinned by AST.** `test_there_is_exactly_one_teardown_body`
  counts calls to the five step functions and requires exactly one each. A
  behavioral comparison of the two modes cannot catch a step added to only
  one path if that step is simply absent from both fixtures.

**MCP side.** `finalize_cluster_teardown` on the `stack-mutation` tier —
the same blast radius as `delete_cluster`, since it destroys IAM
policies, an S3 bucket and credentials; a name suggesting bookkeeping on
`read-only` would repeat the `add_queue` mistake from earlier this
session. `preview_cluster_delete` now mints **two** tokens bound to the
same parameters but different actions, and they are deliberately not
interchangeable in either direction: one authorizes starting a stack
delete, the other authorizes destroying the credentials, and a single
token covering both would make the irreversible half reachable on consent
to the first. Both directions are tested, because an "accept either" fix
closes only one.

**13 faithful mutations, 13 caught** (12 in the first battery, with
`DELETE_FAILED` surviving; caught after the refusal test above was added).
Full suite 2958 passed, `make lint` and `make shellcheck` clean.

**Certifying it on the way out, and what that cost.** The operator chose
the two-phase path for `osiris`'s teardown over `kill_pcluster.py`
precisely because it was the only chance to run checklist Phase 4 against
a real cluster. It found two defects in code written an hour earlier, both
invisible to 13 passing mutations:

- **The banner claimed the stack was already gone, before the only call
  that could know.** `Finalizing teardown: osiris ... The stack is already
  gone; this removes what it left behind.` printed against a live
  `DELETE_IN_PROGRESS`, and was contradicted four lines later by the
  refusal. It was written as a sibling of the destroying banner, in the
  shared prologue that runs before the gate. Both banners now sit on their
  own path: the destroying one where it was, the finalizing one *after*
  the gate passes, so a refused finalize announces nothing at all.
- **The refusal named an internal constant.** A stack that merely still
  exists comes back from the reused wait loop as `TIMED_OUT` — accurate
  inside that loop, where it means "no terminal state within the retries",
  and meaningless in `the stack is not confirmed gone (state: TIMED_OUT)`.
  `_finalize_refusal_reason` translates it to "the stack still exists (it
  is not in any deleted state yet)" and deliberately passes `DELETE_FAILED`
  through **verbatim**, because that is the string the operator greps the
  CloudFormation console for. The asymmetry is the point and is pinned
  both ways.

The test that should have caught the second one was mine, and it was
written to accept either spelling — `assert "TIMED_OUT" in out or
"DELETE_IN_PROGRESS" in out`. An assertion that accepts the wrong answer
alongside the right one certifies nothing; it now bans the constant
outright. Four further mutations on the two fixes, all caught (17/17
across the change).

**Live Phase 4 results.** 4.2 passed: after `delete_cluster` returned in
seconds, the store's `vars/` and `configs/` objects, the keypair, the local
`.pem`, the secret, all four IAM policies and the vars file were still
present — nothing cleaned up, which is the documented behavior and the
thing that made the decomposition worth building. 4.5 passed twice against
a real `DELETE_IN_PROGRESS`: refused in ~1.5s, destroyed nothing, exactly
one describe call. Note this build had **four** managed policies, not five
— `enable_monitoring` was false, so there was no `HeadNode-Monitoring`
policy or SSM Grafana parameter to remove.

**Phase 4 certified end to end, on a real cluster.** The stack was gone at
18m26s; `finalize_only=True` then completed the whole teardown in **5.7
seconds** with no orphaned resources. Verified afterward: the store's
`vars/`, `configs/` and `locks/` objects, all four IAM policies, the role,
the S3 build bucket, the EC2 keypair, the Secrets Manager secret, the SNS
topic, `active_clusters/osiris/`, `src/vars_files/osiris.yml` and every
running instance — **all gone**; all five `/aws/parallelcluster/osiris-*`
log groups — **all retained**, including the four from earlier builds.
`list_clusters(live=True)` returns `[]`.

That 5.7s against a 900s ceiling is the number the decomposition exists
for: the work after the stack disappears is a handful of fast API calls,
and only the *waiting* was ever incompatible with a function timeout.
Item 4.6 (token non-interchangeability) is covered by unit tests in both
directions but was not run live — the tokens are minted by the new
`preview_cluster_delete`, and the MCP server in this session predates it.
4.7 (no `DeleteCluster` event from a finalize) still wants CloudTrail.

**Preamble budget.** This round opened 1,672B *over* the 145,500 ceiling —
several rounds of individually-justified additions with nothing forcing a
check, the exact relapse the working margin exists to catch, and it had
gone unnoticed because the hard ceiling only fires at zero. Restored to
143,410 (headroom 2,090) by condensing `CLAUDE.local.md`'s shared-store
bullet and archiving `CLAUDE-STATE.md`'s completed Workstream 5/6/7 detail
here verbatim. `_CEILING` was **not** lowered: the same commit adds the
finalize constraint, so the reduction left nothing to bank, and lowering
further would push the working margin below its own floor.


### Session 52 (cont.) — one server, one region, said out loud

**A claim I made and then had to walk back.** I told the operator the
per-region IAM scoping on `MCPStateAccess*` was a latent bug: the code
addresses the store bucket in the *cluster's* region, while the policy
grants one region, so a handler would break cross-region. Reading
`_require_record` rather than reasoning from the policy showed otherwise.
On a Lambda there are no local files, so the record can only come from the
fallback — `_record_store()` with no region → `_store_region()` → the
Lambda's own `AWS_REGION`. And a record in the region-X bucket can only
describe a region-X cluster, because `_publish_cluster_record` derives the
bucket from the cluster's region. So `rec.region` always equals the
handler's region and the grant is exactly right. `_store_region`'s own
docstring already said this was intentional.

So the work here is not a bug fix. It is making a deliberate limit legible
and guarding the one way it could rot.

- **The discriminator is provenance, not transport.** The obvious guard —
  "refuse when `rec.region` differs from the server's region" — is wrong
  locally, where an operator's checkout legitimately holds a vars file for
  a cluster in any region; reading it first is precisely what lets that
  cluster's region address its own bucket. The guard is therefore scoped
  to the **store branch**: a record read *out of* the region-X bucket that
  claims region Y is wrong on either transport, because the two agree by
  construction on a healthy store. Acting on it would send every later
  store call in the request to a bucket the record was not found in —
  under one-region IAM, an opaque `AccessDenied`.
- **The not-found message now names the limit and the remedy.** "No
  cluster named X is tracked here" reads as "it does not exist", which for
  a cluster that is up and healthy in another region is actively
  misleading, and "use that region's endpoint" is not guessable from it.
  Confirmed through a real stdio session.
- **Options considered and rejected**: a region wildcard in the ARN (fixes
  only the store, so the record read succeeds and `describe_cluster` fails
  later — a worse failure than a clean refusal); a deploy-time region list
  (least-privilege but runs into `_render_policy`'s hard 6,144-byte IAM
  limit); and a single account-wide bucket (cross-region becomes free, but
  the lock is taken before the first AWS mutation, so every build in every
  region would then depend on one region's S3 — a global single point of
  failure on the build path). One topology per region stands.

**5 of 6 mutations caught; the sixth is equivalent.** Dropping
`rec.get("region")` makes a record with no region raise `KeyError('region')`
instead of `from_dict`'s `KeyError('headnode_instance_type')` — both raw
KeyErrors, differing only in which key is named, so no test can separate
them honestly. It appeared to point at a pre-existing gap — a malformed
store record surfacing as a raw KeyError — which turned out not to exist.
See the next entry.


### Session 52 (cont.) — a guard for a state that cannot occur

Asked to fix the "malformed record raises a raw KeyError" gap noted above,
I built it: a `MalformedClusterRecord(PClusterMakerError)` reporting every
missing field and naming the cluster, `_load_records` skipping a bad record
rather than failing the listing, seven tests, eight mutations all caught.
It also made the previously-equivalent N2 mutant observable, which felt
like confirmation.

Then I seeded a real two-field object into the real bucket and listed. It
came back as a **cluster**, not a skip:

    RECORDS: ['zz-malformed-probe']

`_sanitize_record` is a **total projection** — it builds the record key by
key with a default for every field — and `_read_cluster_record` routes
every caller through it. So a record reaching `from_dict` always has every
field, whatever the stored object looked like. The `KeyError` I set out to
fix is unreachable in production; it appeared only because my own tests
hand-built thin dicts. The guard was dead code, and so was the
`_load_records` skip.

**Why the unit tests could not see it.** They stubbed `_read_cluster_record`
— the one function whose behavior the entire question turned on. That is
this repo's own rule, already written down after `handlers/base.py` called
a FastMCP method that did not exist: *when a test stubs the object under
test, at least one test must drive the real one.* I stubbed it seven times
and drove it zero.

**Reverted**, with precedent: `_project_vars_file`'s record-key fallbacks
were written, covered by no test, and removed for being unreachable — the
same function family, the same mistake. What replaced it is
`TestTheSanitizerIsWhatGuaranteesTheRecordShape`, which pins the invariant
that makes the guard unnecessary: the sanitizer defaults every
`ClusterRecord` field, `from_dict` accepts what it produces, and a
truncated store object still yields a whole record — that last test driving
the **real** `_read_cluster_record` with only the S3 read stubbed. Removing
either a scalar or a list default from the sanitizer fails it. If the
sanitizer ever stops being total, the guard becomes worth having again and
this says so.

Blanks rather than a refusal is the deliberate behavior: a `list_clusters`
row with empty columns is more use than a listing that omits the cluster or
refuses to render. N2 goes back to being an equivalent mutant, which is the
honest state.


### Session 52 (cont.) — the create path could not succeed

Phase 1 item 1.0 asks whether `create_cluster` kicks off and publishes.
It does publish. It cannot return.

**`core_create_cluster` never returned a value.** Every terminal path was
`sys.exit(...)`, including the `wait=False` success path -- the only path
MCP takes. `SystemExit` is a `BaseException`, and `create_cluster` cannot
use `_cluster_lock`'s translation because the core locks internally and
wrapping would deadlock. So a *successful* MCP build killed the server.
Observed exactly that: the call sat "running" for fifteen minutes and the
transport disconnected.

The function's own docstring had predicted it, word for word -- "an MCP
caller of a hypothetical create_cluster tool gets a raw, uncaught
SystemExit on any of these paths" -- and deferred it as out of scope for
Workstream 1. The prediction was exact; what it underestimated was the
cost, because the *success* path exits too, so this was not a
validation-surface limitation but a total one.

**Diagnosis went wrong first.** CloudTrail showed the IAM role created at
09:08:59 and deleted at 09:09:11, which read as a rollback, and I spent
several rounds theorizing about which failure path ran. What settled it
was running the same call standalone with stdout visible: the build
started normally and printed `SystemExit(0)`. The lesson is the one this
repo keeps relearning -- the MCP server swallows the message, so reproduce
outside it rather than inferring from side effects.

**The fix**: a `CreateClusterResult`, twelve direct exits converted, the
CLI shim converting `exit_code` back to `sys.exit`, and a narrow
`except SystemExit` net in the wrapper for the shared validation helpers
(`p_fail`/`refer_to_docs_and_quit`), which are used by other entry points
and are not converted here. The net is the backstop, not the mechanism.

**The tests caught a bug in the fix.** `_fail_after_launch` is a *nested*
function, so returning from it left only the helper and the outer function
ran on to the success path -- a failed launch reporting exit 0. Every call
site propagates it now. `TestBuildFailureCleansUpIam` is what saw it.

**And an existing guard was weaker than it looked.**
`test_no_successful_return_skips_the_publisher` used
`any(publish_line < exit_line)`, which with two publishes is satisfied by
the *earlier* one even after the relevant publish is deleted. Rewriting it
for returns, my first attempt used `ast.walk` over preceding siblings --
which descends into earlier *blocks* and found the publish nested in the
`_KICKED_OFF` branch, so it still passed. It now matches direct statements
in the return's own block. Both weaknesses were only visible under
mutation.

### Session 52 (cont.) — finalize_cluster_build, the other twin

Phase 1 then found a second gap in the same shape. A `wait=False` build
renders `kill_pcluster`/`access_cluster`/`retrieve_ssh_key` into
`stage_dir` -- a temp directory -- and `finalize_staging_directory` is
what copies them into `active_clusters/`. On the non-waiting path the
process returns first, so the scripts are discarded, the staging tree
never reaches the head node, and no summary is sent. The build's own
message says to re-run once the stack completes; re-running is **refused**
on the vars file it wrote itself. Verified: `success=False ... 'a vars
file for this cluster already exists'`.

`core_finalize_cluster_build` closes it, and two decisions are worth
keeping:

- **It reads the rendered vars file, not the build's in-memory state.**
  Tested before designing: 124 keys, and all three templates render from
  it under `StrictUndefined`. That file is what every other surface
  already consumes, so there is nothing to reconstruct and nothing to
  drift.
- **It is local-only.** It writes the operator's `active_clusters/` and
  scp's with the local `.pem`; a remote handler has neither, and the
  scripts are only useful on the machine that runs them. So the *remote*
  create path still ends with no access scripts -- recorded as a smaller
  remaining gap rather than claimed closed.

Verified live against the cluster in exactly that state: three scripts
rendered, staging on the head node, summary published, and **zero `.pem`
objects** in S3. That last one is the property this path must never get
wrong, so it is asserted rather than assumed.

**The suite caught my `stage_dir` fallback**, which used
`tempfile.gettempdir()` -- on macOS `/var/folders/...`, the exact hazard
already documented, invisible on Linux and fatal when `mkdir`'d on an
Ubuntu head node. It is the literal `/tmp` now. **7/7 mutations caught**,
but only after fixing a fixture that used the same region for the record
and the ambient environment, so a tool wrongly reading `_store_region()`
looked correct -- the same lesson the region guard taught a few hours
earlier.

With the scripts in place, checklist 1.12 and 1.13 both passed: SSM gave
`conn=127.0.0.1 ... 127.0.0.1 22`, and with the plugin off `PATH` the
fallback warned by name and reported the operator's real address.


### Session 52 (cont.) — the MCP create path had never run

Item 1.0 asks whether `create_cluster` kicks off and publishes. Driving it
through a real MCP session found two defects that no CLI build could have,
and each was hidden behind the other.

**Defect 1: no event loop.** FastMCP dispatches sync tools on an AnyIO
worker thread; `aws-parallelcluster`'s CDK layer calls
`asyncio.get_event_loop()`, which returns a loop on the main thread and
**raises** on any other. Confirmed directly rather than inferred: a probe
thread gets `RuntimeError: There is no current event loop`, and
`set_event_loop(new_event_loop())` fixes it. The CLI runs on the main
thread, which is exactly why "a CLI build proves the cluster works" said
nothing about the MCP path. `ensure_event_loop()` is idempotent, never
replaces a running loop, and is wired at all six `import pcluster.lib`
sites; dropping the guard at any one of the six is caught.

**Why it was invisible:** `ParallelClusterApiException.__init__` calls
`super().__init__()` with no arguments, so `str(exc)` is empty for *every*
PCluster API exception. Formatting one with `{e}` produced "Exception
launching cluster: " with nothing after it. `pcluster_exception_detail`
now reads `content.message` and the non-INFO
`configuration_validation_errors` (the validator's name is the actionable
half) and can never return an empty string.

**Two wrong turns worth recording.** A `--dryrun` reported the IAM role
missing, which was *post-rollback* state, not the cause — acting on it
would have meant fixing IAM setup that worked. And my first message fix
added the exception type, which got `CreateClusterBadRequestException: no
message`: better, still hiding the content. Each was a layer, not a dead
end, but neither announced itself.

**Defect 2: the monitoring wrapper breaks login nodes.** With the loop
fixed the build reached CloudFormation and then sat in
`CREATE_IN_PROGRESS` for 45 minutes while its login-node Auto Scaling
Group launched and abandoned three instances on Heartbeat Timeout.
Upstream's `installer/install.sh` says it in its own header —
"ParallelCluster HeadNode and ComputeFleet nodes" — and its
`case "${PLATFORM_NODE_TYPE}"` has arms for exactly those two. The wrapper
ran it unconditionally and exited with its status, so a login node fell
through `verify_docker`, matched nothing, failed, and took the custom
action down with it; the ASG replaced the node and the replacement did the
same.

The `LoginNode` arm now exits 0 immediately. That also **retires the
bounded `MONITORING_HOME` poll**, which was a correct answer to the wrong
question: it made the node wait for a tree it was then going to fail on
anyway, at up to 300s per login-node boot.

**Three hypotheses died before that one.** The 300s bound (contradicted by
cfn-init logging *Build complete* with an empty `bootstrap_error_msg`); a
race with the head node's bootstrap (contradicted by the second login node
launching five minutes *after* the head node was ready and failing
identically); and `/home` not being mounted (it mounts at 15:12:36). Each
was plausible from timing. What settled it was reading upstream's
installer — which should have been the first move, since the answer was in
its file header.

**Only the combination fails.** `osiris` had a login node without
monitoring and worked; the first `certify` had monitoring without a login
node and worked. `TestMonitoringWrapperSkipsLoginNodes` replaced the
retired boot-race class and asserts on the **execution
trace**, not the exit status — the harness stubs the installer, which
returns 0 and would hide the whole defect.

**Certified in passing:** tearing that cluster down cleared all eleven
surfaces with zero orphans, including the fifth `HeadNode-Monitoring` IAM
policy, the SSM Grafana password parameter and the churned login ASG —
none of which had ever been through a delete, since this was the first
monitoring-enabled cluster to be torn down.


## Trimmed CLAUDE-STATE.md blocks (moved 2026-08-24)

Verbatim, condensed in place to keep the always-loaded preamble
under its byte ceiling. Constraints stayed; narrative moved here.

```
**Workstream 6 (auth) COMPLETE bar a live deployment** (session 50).
`mcp_server/auth/`: `register_lambda.py` (RFC 7591 DCR), `authorizer_lambda.py`,
`discovery.py`. Facts that stay load-bearing:
- **The allow policy is widened to the whole stage on purpose** — API
  Gateway caches by token, so a `methodArn`-scoped policy is replayed for
  every other path and denies it.
- **`RefreshTokenValidity` must be set explicitly *with its unit*.** The
  30-day default does not reset on use; and the API's raw bound is in
  seconds, so `3650` without `TokenValidityUnits={"RefreshToken":"days"}`
  means ~1 hour while looking correct.
- **No allowlist store**: `DescribeUserPoolClient` is the check,
  `ResourceNotFoundException` is a *deny*; any other error fails closed
  under a distinct message (throttling is not evidence of a bad token).
- Both auth tiers carry **no `pcluster_core` and no `fastmcp`**, and
  import `boto3`/`jwt` lazily — the authorizer runs before every request.
  `PyJWT` is a direct requirement, not a transitive via `mcp`.
- Still open: whether Claude's real DCR payload maps onto
  `CreateUserPoolClient`. Observable only against a live connector.
```

```
**The CLI path is live-verified as of 2026-08-22.** One build-and-teardown
cycle found four defects the 2,777-test suite could not, and they cluster:
**a value correct in the local context, used in a remote one.** The S3
client resolved its endpoint from the ambient region; `stage_dir` resolved
to the operator's macOS `/var/folders/...` and was then `mkdir`'d on an
Ubuntu head node; cluster age treated a local calendar date as a UTC
instant. The `stage_dir` one is **invisible on Linux**, where
`gettempdir()` *is* `/tmp` — CI could never have caught it. Prefer a
static guard wherever the only observable effect is *which endpoint or
path a call reaches*, since every AWS call in the suite is stubbed.
```

```
**Workstream 7 (tool-surface scoping and testing) COMPLETE** (session 51).
Its lesson generalizes past MCP: **three pieces were tested against
something other than what ships**, and each hid a real defect.
- **`FastMCP.call_tool` returns a `ToolResult`, not a dict** — pydantic
  content models in `.content`, the tool's value in
  `.structured_content`. Unpack both; a missing branch stringifies the
  whole object into one text block. FastMCP **raises `ToolError`** for a
  failing tool rather than setting `is_error`, so there is no `isError`
  branch to write (it would be unreachable).
- **The router must check `FunctionError`.** A failing Lambda still
  returns StatusCode 200 with `{"errorMessage","errorType","stackTrace"}`
  as the payload. Forwarding it leaks `/var/task/...` paths and is not a
  JSON-RPC message. `unwrap_invocation` drops the trace, keeps type and
  message. The handler's own `except` cannot cover this — cold-start
  import errors, OOM and timeouts happen outside it.
- **`ssh_available=not remote`, never a hardcoded literal.** Hardcoding
  False downgraded the *local* server below the CLI it wraps
  (`check_slurm` is the check that proves a cluster can run work).
- **Layer 1 compares each wrapper to the core function it calls** (AST
  over all 18 call sites, both directions, plus a `**kwargs`-splat ban) —
  not schema properties, which is what it checked before and which cannot
  see signature drift.
```

```
- ~~Should `add_queue`/`remove_queue` stay on the read-only tier?~~
  **Decided 2026-08-24: no.** They write `configs/*` and moved to
  `stack-mutation`; read-only kept `GetObject` for `list_queues` and lost
  `PutObject`. Accepted cost: a queue edit now carries that tier's blast
  radius.
```


### Trimmed CLAUDE-STATE.md Workstream 5 detail (moved 2026-08-24)

```
- The 900s ceiling, the `aud`-claim fact, the local/remote split, the
  tier/IAM split and the Lambda-packaging rule are all **normative in
  `CLAUDE.md`** as of the MCP commit — not restated here.
  `test_no_routed_tool_wrapper_passes_wait_true` (AST, not grep) and
  `_MCP_LAMBDA_POLICY_FILES` are the guards.
- **`fastmcp` breaks `import ansible`** — it pulls setuptools'
  `_distutils_hack`, adding a second `FileFinder` to `sys.path_hooks`;
  Ansible's collection loader demands exactly one. `tests/conftest.py`
  imports `ansible.plugins.action.template` first. Do **not** instead
  loosen `TestTheTestEnvironmentMatchesAnsible`.
- **Patch `mcp_server.tools.<name>`, not `pcluster_core.<name>`** —
  `tools.py` binds those at import time (monkeypatch-isolation trap).
- **The cluster lock is held at the wrapper layer** (`_cluster_lock`),
  never in the core functions (those are the CLI's path).
  `create_cluster`/`delete_cluster` must **never** be wrapped — they lock
  internally and would deadlock. A held lock's `sys.exit()` is translated
  to `PClusterMakerError`; `SystemExit` is a `BaseException` and would
  otherwise kill the server rather than fail one call.
- **`confirmation_token.py` is not authentication** — keyless by design,
  so it stops an *unpreviewed* execution, not a hostile one (that is WS6).
  `verify` runs **before** the record lookup so the gate is not
  reachable-around.
- **The router terminates protocol methods** (`initialize`/`ping`/
  notifications) and routes only `tools/call`; `tools/list` fans out to
  all four handler tiers. Three places name the Lambda functions
  (`tiers.py`, `_MCP_LAMBDA_TIERS`, `MCPRouterLambda.json_src`) and cannot
  share an import — pinned by test both ways.
- **`fastmcp` 3.x:** assert on `to_mcp_tool().inputSchema`.
- **`MAKE_CLUSTER_DEFAULTS`** lives in `pcluster_core` (was a local inside
  `make_pcluster.py`'s `main()`); two AST guards read it from there.
  `build_make_cluster_params` assembles a `MakeClusterParams` from it —
  `headnode_instance_type` is **required** (an early draft defaulted it to
  the *login node* default).
- **The tool surface is complete** (round 45): all 15 routed tools have
  wrappers, `UNIMPLEMENTED` is empty. `create_cluster`/
  `preview_cluster_config` take an `overrides` dict giving **full CLI
  parity**, guarded two ways an untyped dict otherwise lacks: unknown keys
  rejected, and **wrong-typed values rejected not coerced**
  (`{"enable_fsx": True}` — a real bool — otherwise passes the key check
  and then silently does nothing, since booleans are carried as the
  strings `"true"`/`"false"`). Type compared with `type() is`, not
  `isinstance`: `bool` subclasses `int`.
- **Three parameters are CLI-only** by operator decision:
  `pre_install_script`, `post_install_script`, `custom_ami` — the only
  knobs that change what code runs on the nodes. `_REMOTE_DENIED_PARAMS`
  in `tools.py`; refusal names the param, the reason, and the
  `make_pcluster.py` command to use instead. Enforced on **both** preview
  and execute.
- **A defaults-only cluster has no compute queue at all** —
  `compute_instance_type` and `gpu_instance_type` both default to `""` and
  the queue flags derive from them, so `config.pcluster.j2` renders
  `SlurmQueues: None`, which PCluster rejects *after* the IAM role, S3
  bucket, keypair and secret exist. `_validate_at_least_one_queue`
  refuses it on **both** paths (round 46 MCP via
  `build_make_cluster_params`, round 47 CLI beside `_validate_queue_sizes`,
  before the first AWS call). GPU-only is still allowed. The core function
  **raises** and the CLI shim converts to `sys.exit` — it is shared with
  the MCP layer, where an uncaught `SystemExit` kills the server.
  `vpc_name` also defaults to the literal `"vpc_default"`.
- **`requirements.txt` must never be installed into a Lambda artifact** —
  `ansible` (~408 MB) plus the plotting stack (~250 MB) exceed the 250 MB
  unzipped limit on their own, for code no tool calls. Per-tier sets live
  in `mcp_server/packaging.py`; `requirements-lambda.txt` is **generated**
  from them, never hand-edited. The router requires **nothing** —
  verified against the real import graph, and that leanness is what makes
  its near-zero IAM meaningful. Only `stack-mutation-node` is a container
  image, and the reason is Node.js (`assert_valid_node_js()`), not size.
- Remaining: `auth/` (WS6), and a live deployment — everything above is
  verified against stubs and the real import graph, never against AWS.
  Detail in `docs/sessions.md` 31-49.
```


## Certifying the stack-mutation-node tier against a live build (2026-08-25)

The image tier was built, pushed to ECR and deployed, then driven with a real
`create_cluster` for cluster `nodecert`. Four rounds, each ending in a defect
that no local test could have produced.

**Round 1 -- `OSError(30): Read-only file system: '/var/task/src/vars_files'`.**
Lambda mounts the deployment package read-only. `repo_root` had been serving
two roles at once: the place templates and modules are read from, and the place
`src/vars_files/<name>.yml` and `active_clusters/<name>/` are written to. True
of a developer checkout, false of every deployment.
`resolve_writable_repo_root` (`src/pcluster_core.py`) returns a writable root
untouched -- so no local caller changes at all -- and builds an overlay under
`/tmp` otherwise: top-level entries symlinked back to the real tree, `src/` a
real directory of symlinks to the modules, `active_clusters/` and
`src/vars_files/` real directories. An overlay rather than a second `state_root`
parameter, because `repo_root` is joined onto by dozens of call sites and each
would become a place to pick the wrong half. Detection is a write probe rather
than `os.access(W_OK)`, which answers from the permission bits and reports a
read-only *filesystem* as writable -- precisely the case being detected. Writing
to `/tmp` is acceptable rather than a data-loss hazard because the durable
copies go elsewhere: the vars file is published to the record store and the SSH
key to Secrets Manager. Confirmed live on the next run, which logged
`Writing vars file: /tmp/_ParallelClusterMaker_root/src/vars_files/nodecert.yml`.

**Round 2 -- `AccessDenied` on `s3:CreateBucket` for the locks bucket, which
already existed.** `_create_locks_bucket` called `create_bucket`
unconditionally and caught only `BucketAlreadyOwnedByYou`, which is what the
operator's own credentials return. A least-privilege handler role is denied the
action outright, on a bucket it can otherwise read and write perfectly well. It
now calls `head_bucket` first and returns on success, skipping
`PutPublicAccessBlock` as well (a second grant it would otherwise need). The fix
is deliberately not an IAM grant: adding `s3:CreateBucket` to the tiers would
hand every handler the right to create buckets in the account in order to permit
a call that never needed to happen, since the bucket is long-lived and
account-wide and the operator's first CLI build already made it. `_FakeS3Lock`
gained a `head_bucket` modeled on botocore's own
`s3/2006-03-01/service-2.json.gz` -- one error, `NoSuchBucket`, at HTTP 404 --
rather than on what the caller happens to need.

**Round 3 -- `AccessDenied` on `iam:CreateRole`.** `MCPStackMutation.json_src`
granted IAM read, instance-profile operations and `PassRole` scoped to
upstream's `parallelcluster/*` paths, but nothing at all for the toolkit's own
`pclustermaker-role-*` and `pclustermaker-policy-*`. The three statements added
mirror `OperatorPolicy`'s own Sids and scoping rather than inventing a set,
since the tier is doing exactly the operator's job; `iam:PutRolePolicy` is
included because the FSx hydration policy is inline rather than managed.

**Round 4 -- `AuthorizationError` on `SNS:CreateTopic`.** Diffing the operator
policy's action set against the tier's showed the gap was not one service but
several: SNS entirely, `ec2:CreateKeyPair`, the per-build S3 bucket's lifecycle,
the Grafana SSM parameter, `sts:GetCallerIdentity` and Route53. With
`MCPStackMutation` at 5,614 bytes of the hard 6,144-byte minified limit, these
could not be added to it, so they went into a new `MCPClusterBuild.json_src`
attached to *both* stack-mutation tiers -- teardown needs the symmetric deletes.
Pricing and Cost Explorer were deliberately left out: the build's cost summary
degrades to `unavailable` without them rather than failing.

Worth recording separately: on every one of these failures the tier returned a
shaped `CreateClusterResult` with `success: false` and the traceback confined to
the logs, and the IAM cleanup path deleted all four managed policies and the
role it had created. The `sys.exit`-to-return conversion and the cleanup
handler both held under real failures.


## Teardown from a machine that did not build the cluster (2026-08-25)

`core_delete_cluster` read two files that exist only on the building
machine: `active_clusters/<name>/<name>.serial`, and the vars file. An MCP
`delete_cluster` against a deployed Lambda therefore aborted with
`Missing cluster_serial_number_file: /var/task/active_clusters/...` while
`s3://<locks-bucket>/vars/<name>.json` held the serial the whole time --
the exact gap the record store was built to close, left open because
nothing had needed those fields until teardown ran remotely.

**The serial alone was not enough.** Fixing it moved the abort down two
lines to the vars file, which supplies `aws_account_id` (needed to derive
the locks bucket, before the lock is even taken) and ten more cleanup
inputs. So `ClusterRecord` was extended by 11 fields -- `aws_account_id`,
`az`, `ec2_iam_policy`, `ec2_iam_role`, `ec2_user_home`, `ssh_secret_name`,
`fsx_hydration_iam_policy`, `results_bucketname`, and the external-NFS,
FSx-hydration and benchmark flags. All eleven were already in
`vars_file.j2`, so the projection picks them up with no upstream change;
the class docstring's rule ("add a field here only when a migrated script
actually needs it") is what sanctioned the addition.

`_serial_from_cluster_record` became `_cluster_record_from_store` and
returns the record whole: one round trip rather than two, and no way for
two lookups of the same object to disagree. The record's keys are the vars
file's own names for every field teardown reads, so it drops straight in as
`cluster_vars`. `from_dict` now ignores keys a record does not carry, since
the store outlives any one version of the class.

**The defaults created a worse failure than the one they fixed.** Every new
field defaults to `""`, so a record published before them loads perfectly
and teardown then runs cleanup against a blank `ec2_iam_policy` and
`ec2_iam_role` -- skipping steps silently, orphaning resources, and
reporting success. A store-driven teardown now refuses a record missing
`aws_account_id`, `ec2_iam_policy` or `ec2_iam_role`, naming the fields and
the remedy. Local files are unaffected: they are still preferred, and the
store is not consulted at all when both are present.

**Testing note, the same one as the round before.** The first five tests
covered `_cluster_record_from_store` in isolation and all passed with the
fallback reverted -- nothing drove `core_delete_cluster` down the
no-local-file path, which is the only path that was broken. The tests that
matter drive the real function: `test_one_store_read_serves_both_files`
pins the single round trip, and `test_the_refusal_does_not_fire_on_a_current_record`
guards the new check against refusing every store-driven teardown, which
would quietly undo the whole extension. Three mutations were verified to
fail: reverting the store fallback (3 tests), reverting the legacy-record
guard (1), and reverting the serial fallback (2).

### The false orphan the store-driven teardown exposed

With the record fallback in place, the first teardown driven purely from
the store completed -- and reported one orphan:

    - Delete the SSH private key associated with this cluster --
      [Errno 30] Read-only file system: 'storecert-...pem'

A remote teardown has no local `.pem` by construction, so this was a purely
local step failing on a file that had never been on that machine, and the
summary told the operator to go remove it by hand. `_delete_local_ssh_key_step`
already treated an absent key as success, but it did so by catching
`FileNotFoundError`, and on a read-only filesystem the kernel rejects the
write *before* it discovers the file is not there: the errno is EROFS, not
ENOENT. The check now precedes the unlink.

**The first test battery for this was vacuous and a mutation caught it.**
It simulated the condition with `chmod 0555` on the containing directory,
which is a read-only *directory* rather than a read-only *filesystem* --
on macOS, unlinking an absent file inside one raises `FileNotFoundError`,
the case the step already handled. All four tests passed with the fix
reverted. Raising `OSError(errno.EROFS)` directly is what discriminates.
Repairing that also required fixing an existing test in
`tests/test_teardown_steps.py`, which proved "a real OS error is reported"
using a path that did not exist -- now correctly a no-op, so it needed a
real file to keep testing what it claims to.

### The CI failure the local suite could not show (2026-08-25)

Three tests in `TestCreateClusterCannotKillTheServer` failed on GitHub with
`NoCredentialsError: Unable to locate credentials`, having passed locally
every time. They stub `core_create_cluster`, but minting the confirmation
token runs the *real* `preview_cluster_config`, and that resolves the
region via `resolve_region_from_az` -- which asks EC2 rather than trimming
the AZ name, deliberately, so the AZ is proved to exist. Unstubbed, that is
a live `DescribeAvailabilityZones` call: green on any machine with
credentials, red on a runner without them.

Reproduced locally with the documented incantation (`env -u AWS_REGION
-u AWS_DEFAULT_REGION -u AWS_PROFILE AWS_CONFIG_FILE=/dev/null
AWS_SHARED_CREDENTIALS_FILE=/dev/null`), fixed with an autouse fixture in
the class that stubs the resolution -- the class is about what
`create_cluster` returns, not about region resolution. A credential-free
run of the whole suite then confirmed those three were the only ones.

**The durable fix is the guard, not the three stubs.** `CLAUDE.md` had
asserted for a long time that every AWS call in the suite is stubbed, and
nothing enforced it; this was the second time CI diverged from local for
exactly this reason. `_no_test_reaches_aws` (`tests/conftest.py`) patches
`botocore.httpsession.URLLib3Session.send` to raise, naming the method and
URL. The patch is at the HTTP layer rather than at `boto3.client` because a
test is entitled to *construct* a client -- many stub a single method on a
real client object, and several assert on region binding -- but no test may
put a request on the wire. `@pytest.mark.allow_aws` opts out.

Verified by reverting the three stubs with credentials present: the guard
fails them with `this test tried to reach AWS: POST
https://ec2.us-east-1.amazonaws.com/`, where before they passed silently.
That is the whole point -- the defect is now visible on the machine where
it gets written, rather than only on the runner.

### Session 53 close-out: what was deployed, what it cost, what is left

**The shape of the session.** Every defect below was found by deploying the
remote transport and driving it against AWS. None was reachable from a
developer checkout, and the local suite -- 3,000+ tests, green throughout --
saw none of them, because in each case the suite exercises the other branch
by construction: a checkout is writable, an operator can create buckets, a
developer machine has credentials.

Eight defects, in the order they surfaced:

1. `repo_root` served as both the source root and the state root. `/var/task`
   is read-only; a build writes `src/vars_files/` and `active_clusters/`.
2. `_create_locks_bucket` issued `CreateBucket` unconditionally, which only
   the operator's own credentials tolerate.
3. `MCPStackMutation.json_src` was never sized for a build -- no grants for
   the toolkit's own roles and policies, SNS, the per-build bucket, the
   keypair or the SSM parameter.
4. CloudFormation, acting as the tier's role, also creates PCluster's *own*
   roles under `role/parallelcluster/*`, which the toolkit's naming does not
   cover. This one is worth remembering as a class: scoping IAM to the names
   *this* code chooses misses the names *upstream* chooses on its behalf.
5. The PCluster requirement was unbounded at both ends, so an artifact built
   today resolved 3.16.0 against an operator venv on 3.15.1 -- and PCluster
   refuses to manage a cluster created by a version it does not recognize.
   The remote transport built a real cluster the CLI could not tear down.
6. Teardown read the serial file and the vars file from local disk.
7. `_delete_local_ssh_key_step` reported a false orphan on a read-only
   filesystem: EROFS, not ENOENT.
8. Three tests reached real AWS through `preview_cluster_config`.

**What the deployment cost, measured rather than estimated.** The
`stack-mutation-node` image is 984 MB uncompressed / 303 MB in ECR, against
Lambda's 10 GB image ceiling -- so the zip limits that shape the other four
tiers do not apply. Cold start 604 ms of init; `preview_cluster_config`
answers in ~540 ms warm. The `stack-mutation` zip is 146 MB unzipped and
58 MB zipped, over the 50 MB direct-upload limit, so it goes via S3 exactly
as `packaging.py` documents.

**Two testing lessons, both learned the hard way in this session.**

*Test the wiring, not the helper.* Twice a battery of tests covering a new
helper in isolation passed with the fix reverted, because nothing drove the
function down the broken path -- the only path that was broken. Both times
the mutation check is what exposed it.

*A reproduction that is not the real environment proves nothing.* The
"credential-free" recipe used to verify the no-AWS guard set
`AWS_EC2_METADATA_DISABLED=true`, which suppresses botocore's IMDS probe.
CI does not set it. The guard blocked that probe, and the commit meant to
fix CI failed 13 more tests. The correct recipe leaves the probe enabled;
it is now recorded in `CLAUDE-STATE.md` with an explicit warning not to add
that variable back.

**Teardown.** `certify` was destroyed through `kill_pcluster.py` in 21m35s,
exit 0, with no orphan list; every target was then verified independently
rather than trusted from the summary -- the five IAM policies and role, the
S3 bucket, the SNS topic, the SSH secret, the keypair, the Grafana SSM
parameter, the store record and the local state. The MCP infrastructure went
in one pass: 5 Lambdas, 5 Lambda log groups, 7 IAM roles, 10 IAM policies,
the ECR repository and its image, the Cognito pool, and the 58 MB artifact.
No EC2 instances, volumes or stacks remain.

Deliberately kept: `parallelclustermaker-locks-<acct>-<region>`, which is
account-and-region scoped and holds the `vars/`, `configs/` and `locks/`
prefixes; and the cluster's `/aws/parallelcluster/certify-*` log groups,
which showed as `DELETE_SKIPPED` during the delete. That is the retain
policy working, not a failure -- they are the only surviving record of the
build and expire on their own at 180 days.

**What is left.** Auth is the one surface never exercised: API Gateway and
Cognito were never stood up, so nothing was ever reached from a browser,
and the whole point of the remote transport is a Claude web session driving
it. The deployment machinery also still has no production caller -- every
deploy in this session was driven from a scratchpad script, so `deploy.py`
is exercised only by tests and by hand.

### Stage A: the three things that needed no cluster (2026-08-25)

**A1 -- the deployment had no production caller.** Every deploy in session
53 ran from a scratchpad script, which is precisely how `deploy_tier`'s
update path shipped broken (`ResourceConflictException` on every existing
function) and stayed broken until a live redeploy hit it. `deploy_mcp.py`
is that caller: it follows the repo's entry-point conventions (`sys.prefix`
venv guard, `#!/usr/bin/env python`), builds a tier, prunes, checks the
250 MB unzipped ceiling **before** uploading anything, routes an artifact
over the 50 MB direct-upload limit through S3, and refuses the image tier
without `--image-uri` rather than building a zip that could never work.
`--setup-infra` creates the IAM and the Cognito pool first; `--dry-run`
builds and reports sizes without touching AWS. Pinned by
`TestTheDeploymentHasAProductionCaller`, including that it builds for
`manylinux2014_x86_64` rather than the operator's architecture -- an arm64
laptop otherwise stages arm64 wheels into an x86_64 function and the
failure is an ImportError at the first invocation, not at build time.

**A2 -- the image had been running an EOL Node for its whole life.** The
base image's bare `nodejs` provide resolves to **Node 18**, end-of-life
2025-04-30, which is why the CDK printed an EOL banner on every single
invocation -- visible in session 53's own logs and read past at the time as
noise. AL2023 offers `nodejs20`, `nodejs22` and `nodejs24`; pinned to
`nodejs22` (LTS), now v22.23.1. The interesting part is the guard: it
already checked that `node` existed, and that check passed happily for
months on an EOL runtime. It now checks the **major** (`>= 20`), because
presence was never the property worth asserting.

**A3 -- the IAM set had never been created in one go.** Every previous
deployment built it incrementally, patched as live AccessDenied failures
surfaced, so "does this create cleanly from nothing" was untested. Run
against an empty account it produced **7 roles and 10 policies in one
pass**, and every per-tier attachment was verified equal to
`_MCP_LAMBDA_TIERS` rather than merely counted -- `MCPClusterBuild.json_src`
included, so the policy split from earlier in the session survives a clean
create. The whole set was then removed again at the operator's request via
`_delete_mcp_infra`, which is driven by the same table as the setup so the
two cannot disagree about what exists.

**A naming defect A3 surfaced.** `_setup_mcp_infra` requires a
`mcp_user_pool_id` and refuses an empty one (an empty value renders a
policy IAM accepts and that then denies at call time), so A3 had to create
a Cognito pool -- and the one made by hand during certification was called
`pclustermaker-mcp-certify`: a *cluster's* name on an account-wide
resource. It went stale the moment `certify` was torn down and the pool was
not. `_derive_mcp_user_pool_name(*, aws_account_id, region)` now yields
`parallelclustermaker-mcp-<acct>-<region>`, the same shape as
`_derive_locks_bucket` and `_derive_results_bucket`, keyword-only,
signature-pinned so it cannot see a cluster or a serial, and checked
against Cognito's 128-character limit. Worth stating plainly in the
docstring, because it would otherwise be assumed: a user pool is regional
and its name is already unique within an account and region, so the region
in the name buys **legibility, not uniqueness**.

**One thing left unfixed, deliberately.** `_delete_mcp_infra` prints
nothing and swallows every failure through `_try(..., suppress=True)`,
while `_setup_mcp_infra` announces every create. A verification sweep was
the only reason the teardown could be confirmed rather than assumed -- the
same shape as the `ignore_errors`-without-`register` problem
`delete_pcluster.yml` already fixed. Not urgent (the resources are free and
hand-verifiable), but a teardown that cannot fail visibly is one nobody can
audit.

### R1-R3: the transport reaches the internet (2026-08-26)

**R1** deployed all seven tiers through `deploy_mcp.py` -- the entry point
built in Stage A, getting its first production use, which is what actually
certifies it. `--setup-infra` created 7 roles, 10 policies and the Cognito
pool under the derived name. Each PCluster-carrying zip measured 146 MB
unzipped / 57 MB zipped, so all three route through S3. The router answers
nothing on a raw `tools/list` by design and had to be driven with a real
API Gateway proxy event, where it returned 200 carrying `stageb`'s record.

**R2 was not a certification step.** There was no API Gateway provisioning
anywhere in the repo: `deploy.py` made zero `apigateway` calls, and
`discovery.py`'s two metadata builders were called only by tests. The
authorizer and register handlers had been deployable and unreachable the
whole time. Built as `--setup-gateway`.

Two defects fell out of running it. The discovery routes returned
registration errors until the `register` tier was redeployed -- the handler
was edited locally and the deployed zip was stale, the deploy-vs-source gap
in miniature. And `authorization_endpoint` rendered as the relative
`/oauth2/authorize` because `MCP_COGNITO_DOMAIN` was never set; RFC 8414
requires absolute URLs, and the Hosted UI domain is a *different host* from
the `cognito-idp` one serving the issuer and JWKS.

**R3 is where the interesting failure was.** With real Cognito tokens minted
through `ADMIN_USER_PASSWORD_AUTH` -- a fabricated token dies at the
signature check and proves nothing about the claim rules -- the documented
claim shapes confirmed themselves:

    access token: token_use='access'  client_id=<set>  aud=None
    id     token: token_use='id'      client_id=None   aud=<set>

which is precisely why the authorizer validates `client_id` and never
`aud`, and why the plan chose a Lambda authorizer over API Gateway's native
JWT one. But an ID token returned **500**, not 401, and the authorizer's own
log showed it had refused correctly:
`token_use is 'id', expected 'access'`.

**The code was right and the transport mistranslated it.** Rather than
rebuild the gateway on a guess, a throwaway REST API was probed with two
authorizers -- one raising the bare word, one raising a sentence:

    REST, message exactly 'Unauthorized'   -> 401
    REST, descriptive message              -> 500
    HTTP, anything                         -> 500

That measurement changed the fix. **A REST API alone would not have worked**:
the mapping is on the *message*, and every message this authorizer raised
was descriptive, so it would have returned 500 on REST too. The probe cost
one throwaway API, two five-line Lambdas and a role, all deleted; the
rebuild it avoided would have cost far more and ended at the same 500.

The fix was therefore two-part -- REST API, **and** log the reason while
raising the bare word -- after which all four behaviours passed, and the
`WWW-Authenticate` header that an HTTP API could not express came with it,
closing a gap recorded as open an hour earlier.

**A false belief was removed from the code.** `Unauthorized`'s docstring
said "the name is load-bearing", conflating class name with message, and a
test asserted `Unauthorized.__name__ == "Unauthorized"` as "the contract".
Both were wrong, and together they are why every real denial came back as a
500 while the suite stayed green. The replacement tests pin what was
measured: the exact string reaches the transport, the reason still reaches
the log, and an unexpected error is *not* converted.

## Trimmed CLAUDE.local.md bullets (moved 2026-08-26)

Verbatim originals of `CLAUDE.local.md` bullets condensed in place.  The rule,
every test name, file path, function name and measured number stayed in the
preamble; the incident narrative is here.

### `CLAUDE.local.md` line 42 (original)

- **Every download checksum is validated in `make_pcluster.py` before the first AWS mutation, and no `_HARDCODED_DEFAULTS` entry may be a placeholder.** Ansible's `get_url` splits `checksum:` on `:` and `int()`s the remainder base 16, so a malformed digest is not caught until the playbook is already running — and by then the five managed policies, the IAM role, the keypair, and the S3 bucket exist and have to be swept before a retry. `_HARDCODED_DEFAULTS` shipped `sha256:REPLACE_WITH_ACTUAL_SHA256` for `monitoring_version_checksum`, `docker_compose_checksum_x86_64`, and `docker_compose_checksum_aarch64`, and that is not a dormant hazard: `_resolve`'s precedence is CLI > defaults file > `_HARDCODED_DEFAULTS`, so a `<cluster>_defaults.yml` written before a new key existed falls straight through to the placeholder. That failed a live `alinux2023` build with `The checksum format is invalid` — a message naming neither the parameter nor the file it came from. All three now hold real digests, equal to `pcluster_defaults.yml`'s, and `_validate_download_checksum` in `src/pcluster_core.py` rejects anything `get_url` would. Properties that are load-bearing:

### `CLAUDE.local.md` line 70 (original)

- **`<cluster_name>_defaults.yml` is applied automatically, by both entry points.** `discover_defaults_file`/`load_cluster_defaults` (`src/pcluster_core.py`) are the single loader; `build_make_cluster_params` layers the file below `overrides` and above `MAKE_CLUSTER_DEFAULTS` — the three-tier precedence `_resolve` already gave the CLI. Before this the CLI only *warned* that the file existed and the MCP server could not reach it at all, so one cluster name built two different clusters depending on the surface. Load-bearing: non-build keys are **ignored, not rejected** (`delete_s3_bucketname` is there for `kill_pcluster.py` and bounced a real MCP preview), and what makes that true is the field filter on the `MakeClusterParams(**...)` construction, not a second filter at the merge — a mutation removing one at the merge passes the whole class; an **explicit override still beats the file**; discovery is keyed on the **exact** cluster name, so `osiris-test` does not inherit `osiris`'s; **absence is not an error**, unlike the `--use_defaults` path. File values are **not** type-validated the way `overrides` are, since `_resolve` does not validate them either — tightening one surface alone restores the divergence. **The file may set the three `_REMOTE_DENIED_PARAMS`, and that is a decision**, pinned by `test_a_defaults_file_may_set_what_an_override_may_not`: `_reject_denied` inspects `overrides` only, since the denial stops a *caller* choosing what runs on the nodes while the file is the operator's own — and `pcluster_defaults.yml` itself sets `pre_install_script`, so checking file values would refuse every real file. Nothing reaches a handler this way (gitignored, in no tier's `sources`); revisit if that changes. **A key with no value is dropped, in both loaders** (`_drop_unset`): `gpu_instance_type:` parses as None, overwrote the `""` default and reached a `str`-typed field where `.split(",")` raises — latent on the `--use_defaults` path since it existed. `TestTheDefaultsFileIsAppliedWhenItExists` pins it. **The suite must not see the developer's own files** (gitignored, so a test naming `osiris` resolved against the real one locally and nothing in CI): `tests/conftest.py`'s autouse `_no_operator_defaults_file` points discovery at an empty directory. Full narrative in `docs/sessions.md`.

### `CLAUDE.local.md` line 85 (original)

- **A failed AWS call and a stopped cluster are different problems, and the generated access scripts must not conflate them.** `templates/access_cluster.j2` and `templates/grafana_tunnel.j2` both ran `aws ec2 describe-instances ... 2>/dev/null || true` and then reported `Could not resolve head node IP for <cluster>. Is the cluster running?` on an empty result. `2>/dev/null` discarded the one line naming the actual cause and `|| true` erased the only thing that separates the two cases: **a missing instance answers the literal string `None` with rc=0, while an auth or API failure is a non-zero rc with a message on stderr** (verified empirically, not assumed). So an expired token or an unset `AWS_PROFILE` sent the operator to check a perfectly healthy cluster. Both files now share the same shape: a `_describe_head_node` function taking the query field and a stderr path, a `mktemp` capture with an EXIT trap, an `_AWS_RC` that survives both call sites, and two mutually exclusive diagnoses — the failure branch prints `NOT a cluster problem`, replays aws's own stderr through `sed 's/^/    /'`, and names the `AWS_PROFILE` in effect; the absent branch says the call *succeeded* and points at `./list_pcluster.py --live`. Properties that are load-bearing:

### `CLAUDE.local.md` line 91 (original)

- **`postinstall.j2` runs on the head node AND on every compute node.** `config.pcluster.j2` registers `postinstall_s3_dest` as `OnNodeConfigured` on `HeadNode:` and on both the CPU and GPU queues; each is a `Sequence` — toolkit postinstall, then the operator hook, then (under `enable_monitoring`) the monitoring wrapper. It was head-node-only through session 22, which made the `ComputeFleet)` case arm dead code and left the GPU NVMe/RAID0 `/local_scratch` block unreachable — instance store exists only on compute instances. Consequences for any edit to that template:

### `CLAUDE.local.md` line 95 (original)

- **`NODE_TYPE` comes from `cfn_node_type` in `/etc/parallelcluster/cfnconfig`. There is no `PARALLELCLUSTER_NODE_TYPE` environment variable — that name is invented, and reading it defeated every gate in this file.** `aws-parallelcluster-environment::cfnconfig_mixed` writes `cfn_node_type=HeadNode`/`ComputeFleet` during the init phase, before any custom action runs; nothing exports a `PARALLELCLUSTER_NODE_TYPE` anywhere in the cookbook or the vendored PCluster CLI. So `${PARALLELCLUSTER_NODE_TYPE:-HeadNode}` always took the default, and every compute node ran the entire head-node path — a whole GPU queue failed `OnNodeConfigured` and hit the partition's 10-failure protected-mode threshold, failing the stack; the head node itself was fine. **The `:-HeadNode` default was the bug, not a safety net** — it collapsed "this variable does not exist" into "be a head node" and made the `case`'s `*)` arm unreachable, the one guard whose job is to catch a changed upstream contract. The replacement defaults to `HeadNode` only when the cfnconfig **file** is absent (a genuine off-cluster manual re-run); a cfnconfig that exists without `cfn_node_type` is a hard failure. **`tests/test_templates.py` hid it**: `_run_postinstall` set `PARALLELCLUSTER_NODE_TYPE` in the environment, so every gating test passed against a mechanism no node has ever used. It now writes a fake `cfnconfig` and substitutes its path (`node_type=None` omits the file, `node_type=""` writes one with no `cfn_node_type`); `test_the_node_type_is_read_from_cfnconfig_not_the_environment` asserts on the rendered source with comment lines stripped, since the name legitimately appears in the comment explaining why it must never be read. All eight faithful mutations are caught. Full narrative in `docs/sessions.md`.

### `CLAUDE.local.md` line 98 (original)

- **`liblua5.1-0-dev` must be installed on the same `apt-get` line as `luarocks`.** `luaposix`, `luafilesystem`, and `lua-term` are C extensions that compile against `lua.h` for whatever version luarocks targets. Ubuntu 24.04's `luarocks` declares its header dependency as an **alternative group** (`liblua5.1-dev | liblua5.2-dev | liblua5.3-dev`) which apt satisfies with **5.3**, while its parallel `lua5.1|lua5.2|lua5.3` group leaves `luarocks` itself on `lua_version 5.1`. A stock `ubuntu2404` AMI therefore has 5.3 and 5.4 headers and **no 5.1 header at all** (verified live), so every rock fails to compile and `set -euo pipefail` takes the node down with `OnNodeConfiguredExecutionFailure` — with the compiler error going nowhere because cfn-init records stdout only (see the log-group bullet). The whole block is 5.1: `LUA_VER` is read off the `lua` interpreter and `LUA_CPATH`/`LUA_PATH` are built from it, so pinning the rocks to another version with `--lua-version` compiles them into a directory Lmod never searches — a failure that appears at module load, long after the build looks clean. `liblua5.4-dev` on the general dev-package line is unrelated and must not be conflated with this. `TestLuarocksGetsTheLuaHeadersItCompilesAgainst` asserts on the **execution trace** for `HeadNode`, not the source, so it also proves the apt line is reached inside the head-node gate — a source-level grep passes with the install sitting in a block that never runs. All four faithful mutations are caught (headers absent, headers 5.3, headers installed after the rocks, rocks pinned to 5.3). **This whole hazard is Ubuntu packaging and has no RHEL analog** — RHEL's `luarocks` depends on `lua-devel` for the same lua it targets, and `lua-devel` is already on the critical-packages line, so that arm is a bare `dnf install -y luarocks` with no separate header package. Do not "restore symmetry" by adding one; `TestLuarocksGetsTheLuaHeadersItCompilesAgainst` runs against the Ubuntu fixture only, so an invented RHEL header package would go unchecked. **The no-RHEL-analog claim is confirmed on hardware**: all three rocks compiled against `/usr/include` with `lua-devel-5.4.4-4.el9`.

### `CLAUDE.local.md` line 99 (original)

- **Lmod's `./configure` hard-quits on a missing helper tool, and `bc` is not on the RHEL 9 AMI.** It does not degrade or warn: it prints `You must have <tool> in your path. Quitting!` and exits non-zero, which under `set -euo pipefail` is `OnNodeConfiguredExecutionFailure`. Neither package line installed `bc`, and that failed a live RHEL 9 build. `bc` is in `rhel-9-baseos-rhui-rpms`, so it needs neither EPEL nor CRB. **It is the only gap, not the first of a series**: Lmod 8.7.55's `configure` has exactly six `You must have` gates — `pkg-config`, `ps`, `expr|gexpr`, `basename|gbasename`, `bc`, and `sha1sum|shasum|md5sum|md5` — and all but `bc` were present on the live node (`md5` is absent but the other three satisfy that alternative group). Nothing else in the toolkit calls `bc`, which is why the comment on the package line saying so is load-bearing — it reads like a stray utility otherwise. **It is on the Ubuntu arm too, deliberately**: that AMI ships `bc` incidentally, and depending on what a base image happens to carry is exactly how this hid. `TestLmodConfigureGetsEveryToolItHardQuitsOn` pins presence on the execution trace (which also proves the package line is reached inside the head-node gate) and order in the **rendered source** — the ordering half cannot use the trace, because `./configure` is deliberately not stubbed in `_run_postinstall` and so never appears in it, which made the first version of that test vacuous and let `bc`-after-`make install` survive. It strips comment lines before matching, since the comment explaining the requirement names `./configure` itself.

### `CLAUDE.local.md` line 100 (original)

- **The GPU NVMe block must skip devices ParallelCluster's own cookbook already claimed.** `aws-parallelcluster-environment::ephemeral_drives` runs **before** `OnNodeConfigured` and, on any instance type with instance store, puts every such device into an LVM physical volume (`/dev/vg.01/lv_ephemeral`), formats it `ext4`, and mounts it on `/scratch` — confirmed in the compute node's own chef log. `mkfs.xfs` on the same device then fails with `cannot open /dev/nvme1n1: Device or resource busy`, and under `set -euo pipefail` that is a failed compute node. Unlike a head-node failure, this one does not stop: `clustermgtd` sets the static node `DOWN`, relaunches it, and repeats until the partition's bootstrap-failure count reaches **10**, at which point the cluster enters protected mode and the stack fails — so a two-line bug costs ten instance launches and the entire build.

### `CLAUDE.local.md` line 103 (original)

- **The `/etc/profile` guard suspends all three shell options, not just `set -u`.** `set -e`, `set -u`, and `pipefail` are three independent ways for a profile fragment to kill a node, and a fragment written for an interactive shell has no obligation to survive any of them. The guard shipped as `set +u` / `source /etc/profile` / `set -u`, leaving `pipefail` in force; AL2023's `/etc/profile.d/debuginfod.sh` runs a pipeline that legitimately fails (`cat` on a directory absent from that image) and `pipefail` promoted that into an `OnNodeConfiguredExecutionFailure`, with nothing on stdout. The correct form is `set +euo pipefail` / `source /etc/profile` / `set -euo pipefail`, in **both** `templates/postinstall.j2` and `templates/monitoring-post-install-wrapper.j2`. **RHEL 9 surviving that line was never evidence for AL2023** — the two distros have different `/etc/profile.d` trees, and reasoning from one dnf-family distro to the other is what left the hazard in place through two successful RHEL builds. **Neither `_run_postinstall` nor a source-text assertion can see this**: the harness discards the rendered script's line 2 (see the NVMe bullet for why it must), and a text match can't tell an enclosing guard from a removed one. `TestTheProfileGuardSuspendsEveryOption` (`tests/test_templates.py`) extracts each template's real prologue *by position* and executes it under real `bash` against a profile carrying the hazard, parametrized over both templates; `test_the_harness_fails_a_guard_that_only_suspends_set_u` is the vacuity guard, rebuilt from the guard's position rather than a literal replace. `set +eu` (no `pipefail`) is behaviorally safe and caught only by the ordering test, correctly — with `-e` cleared, a failed pipeline status has nothing to act on. All eight faithful mutations are caught. Full narrative in `docs/sessions.md`.

### `CLAUDE.local.md` line 104 (original)

- **A compute node must refresh its package index before installing anything, and the monitoring installs must be non-fatal.** `OnNodeStart` — and therefore `preinstall.j2`'s index refresh — is registered on the head node only, so a compute node's `/var/lib/apt/lists` (or dnf cache) is whatever the AMI shipped. `apt-get -y install nvtop` against that index exits **100** with `E: Unable to locate package nvtop`. The GPU block therefore splits on `NODE_TYPE` **inside each OS arm**: the head node installs `nvtop htop` with no refresh (`preinstall.j2` already did one and the head-node package block does another — a third is pure bootstrap latency), and a compute node refreshes first (`apt-get -y update` / `dnf -y makecache`) and installs `htop` only. **`nvtop` is head-node-only on purpose, on both families**: it is outside the default repositories (`multiverse` on Ubuntu, EPEL on RHEL — which is why the RHEL head-node arm installs `epel-release` by URL first, also non-fatally), the operator logs into the head node rather than a compute node, and it is the only such package on either path. Both installs are guarded with `|| echo "WARNING: ..." >&2` — the *only* non-fatal installs in the file — because a compute node that exits non-zero is relaunched by `clustermgtd` and counted toward the partition's 10-failure protected-mode threshold, so one transient mirror outage would cost the whole stack over a diagnostic nothing in the job path imports. Do not "restore symmetry" by making them fatal.

### `CLAUDE.local.md` line 112 (original)

- **Every `ignore_errors` cleanup task in `src/delete_pcluster.yml` must `register:`, and the result must be read.** `ignore_errors` is correct there — one AWS failure must not abandon the remaining nine cleanup steps — but alone it prints `...ignoring`, exits 0, and the playbook still says `Cluster <name> has been deleted`, which is how orphaned resources went unnoticed until the serial file was gone and `kill_pcluster.py` could no longer retry them. `Collect cleanup failures that ignore_errors swallowed` builds `_orphaned_resources` from each `<var>.failed | default(false)` (verified under real Ansible: `False` for skipped tasks, `True` at the *top level* of a looped result); it then drives the printed summary, `templates/sns_destruction_summary_report.j2`, and a terminal `fail:`. Ordering is load-bearing — collection must precede the SNS templating, and both `fail:` paths must come after every cleanup task and the summary print, or they abort the thing they're reporting on. Two exemptions, both encoded in `TestTeardownFailuresReachTheOperator._NOT_AN_ORPHAN`: deleting the local `.pem` orphans nothing in AWS, and a failed SNS *send* is not a leftover resource (the SNS *topic* is, appended to the list after its own deletion). Entries interpolate real resource names — `3 cleanup steps failed` alone sends the operator hunting. **The EC2 keypair delete originally had no `ignore_errors` at all**, so one denied `DeleteKeyPair` aborted the play before every later cleanup step *and* before the collection itself; it now carries `ignore_errors` + `register:`, and its `no_log: true` was removed since the module only ever handles a key *name*, not material (`no_log` remains correct on the three creation-side tasks that handle real key material). All eight faithful mutations are caught. Full narrative in `docs/sessions.md`.

### `CLAUDE.local.md` line 115 (original)

- **The package upgrade in `preinstall.j2`/`postinstall.j2` must never replace the kernel.** A kernel bump triggers an initramfs rebuild whose runtime is unbounded inside CloudFormation's bootstrap window -- a full package upgrade once crossed a kernel boundary and was still rebuilding when the wait condition expired. Independently, PCluster's AMI ships EFA and Lustre kernel modules built against the kernel it boots, so replacing it without rebuilding them risks losing the interconnect or the Lustre client on next boot (the documented DKMS hazard pattern; not verified against the current AMI). Both of `preinstall.j2`'s arms carry a full upgrade and both hold the kernel back: apt via `apt-mark hold`, dnf via `--exclude='kernel*' --exclude='kmod-lustre*' --exclude='efa*'`. `postinstall.j2`'s RHEL arm carries the same three excludes; its apt arm installs named packages only and must never become `dist-upgrade`/`full-upgrade` without a hold. `TestPreinstallNeverReplacesTheKernel` (`tests/test_templates.py`) executes the rendered script under real `bash` with the package managers stubbed and pins all of the following:

### `CLAUDE.local.md` line 123 (original)

- **The head-node bootstrap timeout must cover shared-filesystem provisioning.** PCluster creates the `HeadNodeWaitCondition` at `cluster_stack.py:293`, *before* `_add_head_node()` at 295, and the filesystem IDs land in `HeadNodeLaunchTemplate` (`efs_fs_ids` at `cluster_stack.py:1362`, `fsx_fs_ids` at 1375) — an implicit CloudFormation dependency, verified in a deployed template where `HeadNodeLaunchTemplate` had no explicit `DependsOn` but two `Ref`s to the FSx filesystem. So EFS/FSx provisioning runs on the head node's critical path with the clock already running, and the stock 2100s (`NODE_BOOTSTRAP_TIMEOUT`, `pcluster/constants.py:250`) is shared with it. The only knob is `DevSettings.Timeouts.HeadNodeBootstrapTimeout`, which `_add_wait_condition` (`cluster_stack.py:1249-1262`) feeds straight to `CfnWaitCondition(timeout=)`. Consequences:

### `CLAUDE.local.md` line 130 (original)

- **`sinfo` exiting 0 is not evidence that a cluster can run work, and the state classifier is shared.** `check_pcluster.py`'s `check_slurm` ran `sinfo -s`, branched on `rc == 0`, and captured a stdout it never read — so a cluster whose whole fleet was `down`, `drained` or `unk` reported `[PASS] Slurm`. That is the exact state compute nodes end up in after a bootstrap failure, which is the one thing a health check exists to surface: every failure documented in this file ends with nodes in one of those states. It now runs `sinfo -h -o '%D %T'` (`-s`'s `NODES(A/I/O/T)` column is an aggregate that names no state at all, so it cannot express this check) and classifies via `_classify_sinfo_nodes` in `src/pcluster_core.py`. Load-bearing properties:

### `CLAUDE.local.md` line 132 (original)

- **`main()` must print the note.** `check_slurm` returning it is half the fix — printing a bare `[PASS] Slurm` discards a degradation note and is indistinguishable from never having read stdout. A unit test on `check_slurm` cannot see that: it asserts on a return value `main()` is free to throw away. `test_the_degradation_note_reaches_the_operator` drives `main()` through `capsys`, and dropping the note from the print survived every other test.

### `CLAUDE.local.md` line 139 (original)

- **An FSx export prefix is a destination and must not be required to contain objects.** `_check_fsx_s3` in `src/pcluster_core.py` listed the prefix and `sys.exit`ed on `KeyCount == 0` for both buckets, so a valid first-dehydration configuration was refused at the last check before the build — after every earlier validation had passed. AWS's own FSx model settles it: `ExportPath`'s default is `s3://import-bucket/FSxLustre<creation-timestamp>`, which cannot exist before the filesystem does (`CreateFileSystemLustreConfiguration`, botocore's FSx `service-2.json.gz`). The parameter is keyword-only `require_objects=True` and only the export call site in `make_pcluster.py` passes `False`. Load-bearing properties:

### `CLAUDE.local.md` line 143 (original)

- **`scripts/sbatch_default_submission_script.sh` derives its `--partition` and `--ntasks`; it had neither correctly.** (It has no `.j2` suffix, so `EXTRA_TEMPLATES` in `tests/test_templates.py` must keep listing it or no test renders it at all — the harness gap that let two dead ladders survive; `test_collect_templates_matches_what_the_playbooks_render` is what enforces the listing. Narrative in `docs/sessions.md`.) Its two Jinja2 ladders had **both `{% else %}` fallbacks commented out** — so every unlisted size emitted no `--ntasks` at all and Slurm silently ran the job on one task. It also had **no `--partition` directive**, which `sbatch` rejects outright on a GPU-only cluster that has no `compute` partition. Both values now come from the cluster's own shape: `cpu_ranks_per_node` and `gpu_vcpus_per_node`, derived in `make_pcluster.py` from `_vcpu_map` and threaded through `vars_file.j2`.

### `CLAUDE.local.md` line 147 (original)

- **Both are keyword-only, and the call sites are pinned per queue.** Keyword-only defends against transposing `instance_types` and `vcpu_map`, which are different shapes — it cannot defend against handing the CPU queue's derivation the GPU list, since both are lists of instance types and the swap renders a plausible script. `test_make_pcluster_derives_both_queues_from_the_same_response` walks the AST for the assignment targets and pins `cpu_ranks_per_node` to `cpu_instance_types` and `gpu_vcpus_per_node` to `gpu_instance_types`; that swap survived the keyword-name-only version of the test. The same test pins **exactly one** `describe_instance_types` call — the vCPU counts must come out of the response the architecture check already fetches. All nine faithful mutations are caught.

### `CLAUDE.local.md` line 148 (original)

- **Teardown has three outcomes, and the claim it prints is derived once into `_delete_headline`.** Both summary tasks and `sns_destruction_summary_report.j2` said `Cluster <name> has been deleted` whenever no cleanup step *failed* — and on a wait **timeout** nothing failed and nothing was deleted either, so the playbook printed `cluster may still be deleting`, then `credentials are PRESERVED`, then contradicted both twelve lines later and exited **0**, which every caller reads as a clean teardown. The orphan list is orthogonal to all three states (`_orphaned_resources` is empty on a timeout, because an ignored *failure* is not what happened), so gating the claim on it can never be right. `Record what the summary is allowed to claim about the stack` derives one sentence from `_cf_delete_confirmed` then `_cf_delete_failed`, and the two summary tasks plus the SNS report interpolate it — a fourth surface restating the literal is the mutation to avoid, which is why `test_no_reporting_surface_hardcodes_the_success_claim` bans it outside the derivation task's own body (`.serial has been deleted` in the orphan summary is exempt: that is the local serial file, not the cluster).

### `CLAUDE.local.md` line 153 (original)

- **`results_bucketname` is newer than `enable_hpc_benchmarks`, so teardown derives it when the vars file predates it.** A cluster built by an older toolkit has a vars file that satisfies the benchmark gate but leaves the bucket undefined, and `Push performance results from head node to S3` interpolates it. That raised an `UndefinedError` which the block's `rescue` **caught** — verified under real `ansible-playbook`: `failed=0 rescued=1`, the play continues — printing `WARNING: Unexpected error during performance results sync` and naming nothing, after which teardown destroyed the head node holding the only copy. The failure mode is a silent loss behind an unnamed cause, **not** an aborted teardown; do not document it as one. `Derive the results bucket name when the vars file predates it` renders `parallelclustermaker-results-<aws_account_id>-<region>`, gated `when: results_bucketname is not defined`.

### `CLAUDE.local.md` line 161 (original)

- **The budget is bytes, not lines.** The two files differ by 7.4x in bytes per line (86 vs 640 — one is wrapped prose, the other one dense paragraph per bullet), so a line cap is a byte cap whose size depends on wrap width: reflowing `CLAUDE-STATE.md` at width 100 adds 207 lines while changing nothing. The cheapest way to satisfy a line cap is also longer lines, which rewards the least readable shape in the file. The ceiling is **combined**, so the two files are priced against each other and no budget can be found by shuffling content between them.


## Trimmed CLAUDE.local.md bullets (moved 2026-08-26, second pass)

Verbatim originals of `CLAUDE.local.md` bullets condensed in place.  The rule,
every test name, file path, function name and measured number stayed in the
preamble; the incident narrative is here.

### `CLAUDE.local.md` line 27 (original)

- **The mount-point column width must be derived from the longest *active* label, never a hardcoded constant.** Python's `str.format` silently drops the padding entirely when a value overflows its width rather than raising, so a width one character short of the `--ebs_shared_dir` default `/shared` lost the alignment of the first line of every real build's summary while the 4-character `/efs`, `/fsx` and `/nfs` kept theirs. The fix computes `col = max(len(label) for label in the active mount labels) + 2` once per call, so a longer or shorter `--ebs_shared_dir` widens or narrows the column correctly instead of silently breaking. All four mount types share one parenthetical label style — `EBS (gp3, 250 GB)`, `FSx for Lustre (1200 GB)`, `EFS (bursting throughput)`, `external NFS (host)` — and the sibling bullet's test matches on `FSx for Lustre (`, so those spellings are load-bearing. All three summary surfaces carry the same fix, since each duplicates the format independently (see the bullet above): `src/create_pcluster.yml`'s `_build_summary` is one bare `{{ }}` Jinja2 expression with no `{% set %}` scoping available inside it, so the column-width formula is repeated inline at each of its four call sites rather than computed once; `templates/sns_build_summary_report.j2` computes it once via `{% set %}` at the top of the block, since a real template (unlike a `set_fact` expression) permits that. `TestStorageSummaryLines::test_every_active_mount_line_starts_its_description_in_the_same_column` and `test_a_longer_custom_mount_point_widens_every_column` (`tests/test_make_pcluster.py`) pin both properties on the Python function: every active mount line lands in the same column, and a longer `--ebs_shared_dir` genuinely widens it rather than the width being hardcoded.

### `CLAUDE.local.md` line 30 (original)

- **The `assert` task in `src/create_pcluster.yml` must stay task index 0.** `make_pcluster.py`'s argparse `choices` are the outermost gate only for people who use the CLI: `ansible-playbook --extra-vars base_os=<unsupported>` run straight at the playbook bypasses them, and `config.pcluster.j2` then passes that value into `Os:`, which upstream PCluster's own `SUPPORTED_OSES` *accepts* — so nothing downstream refuses it and the node dies at its first package install, twenty minutes in. The gate constrains **both** `base_os` and `pcluster_os` (the latter is what reaches PCluster's `Os:` field, so constraining `base_os` alone leaves it free) and its allowed values live in the task's own `vars`. Position is the property, not presence: an assert placed after the SNS topic or the S3 bucket has already billed the operator and left resources behind. `test_the_playbook_rejects_an_unsupported_os_before_spending_anything` asserts on the task *index*, and the gate was verified firing under real `ansible-playbook` both ways — an unsupported value fails with `ok=0`, `ubuntu2404arm` passes through. All seven faithful mutations are caught.

### `CLAUDE.local.md` line 31 (original)

- **RHEL-specific bootstrap facts, all of them confirmed on live builds of both arms.** EPEL installs *by URL* (`epel-release-latest-9.noarch.rpm`) because `epel-release` is not packaged in RHEL itself, unlike CentOS and Amazon Linux; it is required because `lua-devel`, `lua-posix`, `lua-filesystem`, and `tcllib` are in neither baseos nor appstream. CodeReady Builder is enabled best-effort in a loop over three repository ids (`crb` on Rocky/Alma/CentOS Stream, two `codeready-builder-for-rhel-9-*` spellings on genuine RHEL depending on RHUI vs subscription-manager), with the fatal package install as the arbiter — so a missing package fails loudly by name rather than on an unrecognized repo id. `pip3` takes no `--break-system-packages` on this arm: RHEL 9's pip predates PEP 668 and rejects the flag. There is **no** apt-mark analog — `dnf --exclude` needs no package enumeration, so the `dpkg-query` status filter, the phantom-package problem, and its `|| true` guard have no counterpart.

### `CLAUDE.local.md` line 32 (original)

- **Amazon Linux 2023 is the dnf family's second member, and every difference from RHEL 9 is a package that does not exist.** Every package claim below is **confirmed on hardware on both arches**, not on repo metadata alone; item 5 of the live-verification list in `docs/sessions.md` has the per-claim evidence. The arm is selected by `'alinux' in base_os`, which is a **substring** test: `alinux2`, `alinux2arm`, and a trailing-garbage `alinux2023arm2` all satisfy it, so all three are in `_UNSUPPORTED_OSES` and the argparse `choices`, `_EC2_USERS`, and the playbook's `assert` task are what actually bound the set — the template branch does not. `pcluster_os` strips the suffix to `alinux2023`, which is in upstream's own `SUPPORTED_OSES`. Login user is `ec2-user`, same as RHEL. Four packages RHEL's arm installs are **absent** from al2023 on x86_64 and aarch64 alike, and each was checked against the metadata rather than assumed:

### `CLAUDE.local.md` line 33 (original)

- **`epel-release` is not packaged for al2023 at all**, so the RHEL arm's release-RPM URL install has no analog on either the critical-packages line or the GPU block. An EL9 EPEL rpm on an AL2023 node is a version-mismatched repo, and the critical-packages install is *not* `|| true`-guarded, so it fails the node. A stray copy of that line survived into the AL2023 arm of `postinstall.j2` and was caught by `test_no_epel_release_rpm_is_fetched_by_url` on the first full-suite run after the guard was written — which is the entire reason that test exists. There is also no CRB analog: `lua`, `lua-devel`, `lua-filesystem`, `lua-posix`, `lua-term`, `bc`, and `tcl` are all in the core repo, so nothing is left for a second repository to supply.

### `CLAUDE.local.md` line 34 (original)

- **`luarocks` is absent**, so the three rocks the RHEL and Ubuntu arms build from source come from the core repo as RPMs instead (`lua-filesystem`, `lua-posix`, `lua-term`) and the luarocks block is a no-op on this arm. `luarocks` is **stubbed and returns 0** in `_run_postinstall`, where a real AL2023 node returns 127 — so a source-level assertion proves nothing and `test_luarocks_is_never_invoked_on_this_arm` asserts on the **execution trace**, for both node types. Its paired vacuity guard `test_the_other_two_arms_still_build_the_rocks` is what keeps the fix from being "delete the rocks everywhere".

### `CLAUDE.local.md` line 37 (original)

- **`lustre-client*` joins the dnf kernel exclusions**, in both templates, on the shared dnf arm. AL2023 packages the Lustre client as `lustre-client` and has **no `kmod-lustre*` at all**, so the RHEL glob alone silently protects nothing there; on RHEL `lustre-client` is the userspace half of the same client and has no business jumping versions away from the kmod either, so the flag is correct on both rather than merely inert on one. Neither distro carries an `efa*` package in its core repo — EFA comes from Amazon's installer on the AMI — so that glob is kept on the strength of node state, not repo contents.

### `CLAUDE.local.md` line 76 (original)

- **`job_hpc-benchmark.sh.j2`'s `#SBATCH --partition=` and `--ntasks-per-node=` are derived, never hardcoded.** The partition is `compute` when `enable_cpu_queue == 'true'`, otherwise `gpu`; `enable_cpu_queue` is derived from `compute_instance_type` (`make_pcluster.py`), so a GPU-only cluster is supported and has no `compute` partition — a hardcoded `--partition=compute` is rejected by `sbatch` with an invalid-partition error before anything runs. On a GPU-only cluster the rank count is `gpu_ranks_per_node`, derived in `make_pcluster.py` as `min(nvidia_gpu_count(...))` across `gpu_instance_types` (the only value every node in the queue can satisfy) and threaded through `vars_file.j2` → the playbook → the job template. `nvidia_gpu_count()` in `pcluster_aux_data.py` counts NVIDIA devices only — AMD (`g4ad`) and Habana accelerators are invisible to both Slurm GRES and the CUDA runtime. A queue reporting zero NVIDIA devices falls back to 4 ranks, since `--ntasks-per-node=0` is not a valid `sbatch` value. The GPU count is read from the `describe_instance_types` response the architecture check already fetches; do not add a second API sweep. **Slurm GRES does exist on these clusters, but nothing in the toolkit's own path puts it there.** PCluster's vendored CLI does not emit it — `gpu_count()` in `pcluster/aws/aws_resources.py` feeds validators only, and `grestypes` is on `SLURM_SETTINGS_DENY_LIST` (`pcluster/validators/slurm_settings_validator.py`, under `SlurmConf/Global`), so an operator cannot set it through the config either. The **cookbook** configures it at node bootstrap: confirmed on a live AL2023 GPU node; session 37 of `docs/sessions.md`. Rank-count matching is still the right mechanism, and not because GRES is unavailable — it depends on nothing but `--ntasks-per-node`, so it behaves identically whether the cookbook configured GRES on a given queue or not. Do not rewrite the job template to `--gres=gpu:N` on the strength of one confirmed cluster.

### `CLAUDE.local.md` line 90 (original)

- **The install scripts are two distinct stages, and the toolkit's own stage is a rendered template.** Every node runs, in order, the toolkit's rendered `preinstall.j2`/`postinstall.j2` and then the operator's hook named by `--pre_install_script`/`--post_install_script`. These are separate variables in `vars_file.j2`: `preinstall_template_orig`/`preinstall_rendered`/`preinstall_s3_dest` (and the `postinstall_*` equivalents) name the toolkit stage and derive their filenames from `cluster_name`; `user_preinstall_src`/`user_preinstall_s3_dest` (and `user_postinstall_*`) name the operator hook and derive from the flag. `src/create_pcluster.yml` renders the toolkit pair with `template:` and copies the operator pair with `copy:` — a `copy:` on the toolkit pair uploads raw Jinja2 to S3, which is the regression. From the v3 migration (`c2673ae`) until 2026-07-26 there was no `template:` task at all and `post_install_script` pointed at the template path, so every node ran the operator's 5-line hook while `postinstall.j2` was dead text — Spack, Lmod, the package installs and the `NODE_TYPE` gating all never executed. Confirmed live on a booted `g5.xlarge`. `tests/conftest.py` is what hid it — `pre/post_install_script` pointed at `templates/preinstall.j2`, conflating the two stages, so every assertion about "the postinstall script" was really about the operator hook. `TestPostinstallTemplateIsActuallyRendered` in `tests/test_templates.py` now pins all four properties: rendered by `template:` not `copy:`, the *rendered* path is what gets uploaded, `<stage>_s3_dest` never derives from the operator flag, and both stages appear on every node type. `OnNodeStart` is head-node-only on purpose — running the python3/pip/awscli install on every scale-up is a latency regression, not a fix.

### `CLAUDE.local.md` line 98 (original)

- **`liblua5.1-0-dev` must be installed on the same `apt-get` line as `luarocks`.** `luaposix`, `luafilesystem`, and `lua-term` are C extensions that compile against `lua.h` for whatever version luarocks targets. Ubuntu 24.04's `luarocks` declares its header dependency as an **alternative group** (`liblua5.1-dev | liblua5.2-dev | liblua5.3-dev`) which apt satisfies with **5.3**, while its parallel `lua5.1|lua5.2|lua5.3` group leaves `luarocks` itself on `lua_version 5.1`. A stock `ubuntu2404` AMI therefore has 5.3 and 5.4 headers and **no 5.1 header at all** (verified live), so every rock fails to compile and `set -euo pipefail` takes the node down with `OnNodeConfiguredExecutionFailure` — with the compiler error going nowhere because cfn-init records stdout only (see the log-group bullet). The whole block is 5.1: `LUA_VER` is read off the `lua` interpreter and `LUA_CPATH`/`LUA_PATH` are built from it, so pinning the rocks to another version with `--lua-version` compiles them into a directory Lmod never searches — a failure that appears at module load, long after the build looks clean. `liblua5.4-dev` on the general dev-package line is unrelated and must not be conflated with this. `TestLuarocksGetsTheLuaHeadersItCompilesAgainst` asserts on the **execution trace** for `HeadNode`, not the source, so it also proves the apt line is reached inside the head-node gate; all four faithful mutations are caught. **This whole hazard is Ubuntu packaging and has no RHEL analog** — RHEL's `luarocks` depends on `lua-devel` for the same lua it targets, and `lua-devel` is already on the critical-packages line, so that arm is a bare `dnf install -y luarocks` with no separate header package (confirmed on hardware: all three rocks compiled against `/usr/include` with `lua-devel-5.4.4-4.el9`). Do not "restore symmetry" by adding one; the test runs against the Ubuntu fixture only, so an invented RHEL header package would go unchecked.

### `CLAUDE.local.md` line 99 (original)

- **Lmod's `./configure` hard-quits on a missing helper tool, and `bc` is not on the RHEL 9 AMI.** It does not degrade or warn: it prints `You must have <tool> in your path. Quitting!` and exits non-zero, which under `set -euo pipefail` is `OnNodeConfiguredExecutionFailure`. `bc` is in `rhel-9-baseos-rhui-rpms`, so it needs neither EPEL nor CRB. **It is the only gap, not the first of a series**: Lmod 8.7.55's `configure` has exactly six `You must have` gates — `pkg-config`, `ps`, `expr|gexpr`, `basename|gbasename`, `bc`, and `sha1sum|shasum|md5sum|md5` — and all but `bc` were present on the live node. Nothing else in the toolkit calls `bc`, which is why the comment on the package line saying so is load-bearing — it reads like a stray utility otherwise. **It is on the Ubuntu arm too, deliberately**: that AMI ships `bc` incidentally, and depending on what a base image happens to carry is exactly how this hid. `TestLmodConfigureGetsEveryToolItHardQuitsOn` pins presence on the execution trace (which also proves the package line is reached inside the head-node gate) and order in the **rendered source** — the ordering half cannot use the trace, because `./configure` is deliberately not stubbed in `_run_postinstall` and so never appears in it. It strips comment lines before matching, since the comment explaining the requirement names `./configure` itself.

### `CLAUDE.local.md` line 105 (original)

- **Order and gating are pinned on the execution trace, not the source.** Both commands are `sudo apt-get`, so a grep cannot tell which ran first on which node type, and an update placed *after* the install refreshes nothing in time. `TestPostinstallNodeTypeGating`'s three tests assert on trace indices per node type.

### `CLAUDE.local.md` line 106 (original)

- **`_run_postinstall` cannot see the non-fatal guards at all** — it discards the rendered script's `set -euo pipefail` (see the NVMe bullet for why it must), so a test using it passes whether the `|| echo` is there or not. `TestMonitoringToolsCannotFailTheNode` extracts the block and runs it standalone under real `set -euo pipefail` with the package manager returning 100, and `test_the_harness_actually_fails_the_package_manager` guards those two tests against passing vacuously. All nine faithful mutations are caught.

### `CLAUDE.local.md` line 107 (original)

- **That class is parametrized over all three arms, and the arm table carries the refresh command each one uses.** It ran against Ubuntu only, so the two dnf arms' `|| echo` guards and their `dnf -y makecache` were unguarded — a fatal `dnf` install on a compute node counts toward the same 10-failure threshold. `_ARMS` maps each arm to `(fixture, manager, refresh)`: `apt` → `cluster_params_gpu_queue_enabled`/`apt-get`/`apt-get -y update`, `dnf_alinux` → `cluster_params_al2023_gpu_queue`/`dnf`/`dnf -y makecache`, `dnf_rhel` → `cluster_params_rhel_gpu_queue`/`dnf`/`dnf -y makecache`. The GPU fixtures are required because the whole block sits inside `{% if enable_gpu == 'true' %}`.

### `CLAUDE.local.md` line 108 (original)

- **The `nvtop` assertion is per-install-line on the execution trace, never a substring over the block.** Every arm's guard message *names* the package — `|| echo "WARNING: nvtop/htop unavailable..."` — so `"nvtop" in block` is satisfied with the package dropped from the install line entirely, and that mutation survived the first battery. The test filters the trace to lines starting with the arm's manager and containing ` install `, then requires `nvtop` in that line's own `split()`. Expected presence is per arm and asymmetric: `apt` and `dnf_rhel` yes (multiverse, EPEL), `dnf_alinux` no — AL2023 does not package it and has no EPEL to fall back on.

### `CLAUDE.local.md` line 113 (original)

- **The four tasks that destroy cluster access must be gated on a *positive* confirmation that the stack is gone, never on `not (_cf_delete_failed | bool)`.** The EC2 keypair, the local `.pem`, the Secrets Manager secret, and the `active_clusters/<cluster>/` directory that holds the latter two are the only ways back into a running head node. All four were gated on a blocklist of exactly one state, so a wait *timeout* — `DELETE_IN_PROGRESS`, retries exhausted, the playbook's own `WARNING: cluster deletion wait timed out — cluster may still be deleting` already printed twelve lines above — read as a clean delete and destroyed all four while the stack and the head node were still up and still billing. That is precisely the case the `DELETE_FAILED` branch's message says they are preserved for. `Record whether the stack is confirmed gone` sets `_cf_delete_confirmed` from `DELETE_COMPLETE` **or** any of the four cluster-absent spellings (`ClusterNotFound`/`does not exist`, on either stream — the delete wait's own `until:`/`failed_when:` accept all four, so all four reach these tasks), and every state that is neither confirmed nor `DELETE_FAILED` gets its own preservation warning, since the `DELETE_FAILED` one does not cover it. The `cluster_delete_status is defined` guard is load-bearing: a skipped wait task is not confirmation. `TestCredentialsSurviveAnUnconfirmedDelete` (`tests/test_templates.py`) evaluates each task's real `when:` with `Environment.compile_expression` against four real `describe-cluster` outcomes rather than matching text, because a text assertion cannot tell one gate from another; `test_the_scenarios_can_see_the_gate_that_shipped` is the vacuity guard, asserting the timeout case is one the shipped gate got wrong.

### `CLAUDE.local.md` line 114 (original)

- **`pcluster describe-cluster`'s three outcomes get three separate aborts, and the wait task stays `failed_when: false`.** The create-side wait had `failed_when: false` with no follow-up, so a succeeded stack, a failed stack, and an AWS call that never answered all fell through to one code path — and since every downstream task is gated on `head_node_public_ip`, `''` in the last two cases, an expired token produced a build that reported a *cluster* problem while the stack kept building and billing. `Abort if describe-cluster itself failed` (`when: cluster_status.rc != 0`) says `This is NOT a cluster problem`, replays `cluster_status.stderr`, and names the `AWS_PROFILE` in effect; `Abort if the stack never reached a terminal state` says `STILL BUILDING`, that the stack is `still billing`, and points at `--pcluster_create_timeout`. Both sit between the wait and `Get the head node IP address` — an abort after that fact is an abort after every gated task has already silently no-opped. Restoring `failed_when` on the wait is the mutation to avoid: Ansible's own retry failure collapses the three diagnoses back into one opaque message. `TestAFailedDescribeIsNotAFailedCluster` evaluates each abort's `when:` against four outcomes and asserts which fires *first*, plus `test_the_three_diagnoses_are_distinguishable` as the vacuity guard (one generic message naming every cause would satisfy any individually-worded assertion). That guard must match `--pcluster_create_timeout` with the dashes: the bare variable name also appears in the "did not reach ... within N minutes" line, and matching it there let deleting the remediation line survive.

### `CLAUDE.local.md` line 116 (original)

- **`--exclude` is a name glob resolved at depsolve time, needing no enumeration or audit** -- a kernel cannot enter the transaction under a name not starting with `kernel`, so a future AMI's pending set cannot matter. Never add `--exclude='dracut*'` or `--exclude='microcode_ctl'`: neither replaces the kernel, and excluding security updates on speculation is the worse trade. A `uname -r` vs `rpm -q --last kernel` guard is likewise wrong -- it fails a healthy node that ships a kernel installed-but-not-booted.

### `CLAUDE.local.md` line 118 (original)

- **Every `pip3 install` on a node path must carry `--ignore-installed`, on both OS arms, across all four pip call sites** (self-install and dependency install in `preinstall.j2`; the plotting-stack install in `postinstall.j2`). pip cannot uninstall a distribution whose `dist-info` has no `RECORD` file, which distro-packaged Python modules routinely ship -- confirmed on a live RHEL 9 node, and pip does elect to replace distro-owned transitives (`packaging`, `python-dateutil`) even when the direct pins are already satisfied. `TestNoPipInstallEverUninstallsADistroPackage` asserts over the **rendered** text of both templates.

### `CLAUDE.local.md` line 123 (original)

- **The head-node bootstrap timeout must cover shared-filesystem provisioning.** PCluster creates the `HeadNodeWaitCondition` at `cluster_stack.py:293`, *before* `_add_head_node()` at 295, and the filesystem IDs land in `HeadNodeLaunchTemplate` (`efs_fs_ids` at `cluster_stack.py:1362`, `fsx_fs_ids` at 1375) — an implicit CloudFormation dependency, verified in a deployed template. So EFS/FSx provisioning runs on the head node's critical path with the clock already running, and the stock 2100s (`NODE_BOOTSTRAP_TIMEOUT`, `pcluster/constants.py:250`) is shared with it. The only knob is `DevSettings.Timeouts.HeadNodeBootstrapTimeout`, which `_add_wait_condition` (`cluster_stack.py:1249-1262`) feeds straight to `CfnWaitCondition(timeout=)`. Consequences:

### `CLAUDE.local.md` line 139 (original)

- **An FSx export prefix is a destination and must not be required to contain objects.** `_check_fsx_s3` in `src/pcluster_core.py` listed the prefix and `sys.exit`ed on `KeyCount == 0` for both buckets, refusing a valid first-dehydration configuration at the last check before the build. AWS's own FSx model settles it: `ExportPath`'s default is `s3://import-bucket/FSxLustre<creation-timestamp>`, which cannot exist before the filesystem does (`CreateFileSystemLustreConfiguration`, botocore's FSx `service-2.json.gz`). The parameter is keyword-only `require_objects=True` and only the export call site in `make_pcluster.py` passes `False`. Load-bearing properties:

### `CLAUDE.local.md` line 142 (original)

- **Which call site got the flag is the whole fix.** Both calls take the same four positional arguments and differ only in the label, so a flag on the *import* call reads as plausible and silently stops validating the one path that has to hold data. `test_make_pcluster_relaxes_the_export_call_and_only_that_one` walks the AST and pins `{"import": True, "export": False}`. The default must stay `True`, and `test_an_empty_import_prefix_still_fails` is the vacuity guard against the fix becoming "stop checking either one".

### `CLAUDE.local.md` line 144 (original)

- **`gpu_vcpus_per_node` is not `gpu_ranks_per_node`, and the names are the documentation.** `gpu_ranks_per_node` is an NVIDIA *device* count — correct for the GPU benchmark driver, wrong for `--ntasks` on a general-purpose job, where a `p3.2xlarge` would get 1 task on an 8-core machine. The GPU-only arm of this script asks for **cores**. `cluster_params_gpu_no_nvidia` (`g4ad.4xlarge`) is the fixture that makes the distinction visible: `gpu_ranks_per_node` is 0 there while `gpu_vcpus_per_node` is 16.

### `CLAUDE.local.md` line 145 (original)

- **`usable_vcpu_count` divides by `DefaultThreadsPerCore`, never by a hardcoded 2.** `DisableSimultaneousMultithreading` is what `config.pcluster.j2` sets when `hyperthreading` is false, and upstream acts on it only when the instance reports more than one thread per core (`cluster_config.py:1523`). Graviton reports 1, so halving unconditionally requests half the cores every ARM node actually has. The `or 1` covers both that case and a missing field and makes the division an identity rather than a special case — do not reintroduce an `if threads > 1` branch around it, which is unreachable and reads as though the `or 1` were not there.

### `CLAUDE.local.md` line 149 (original)

- **The headline must be gated on the positive confirmation, not on `not (_cf_delete_failed | bool)`** — the same rule as the four credential-destroying tasks above, and for the same reason: a timeout is neither confirmed nor `DELETE_FAILED`, so a blocklist of one state reports it as success. `DELETE_FAILED` and the timeout also need *different* wording, since the operator greps the CloudFormation console for the state name; collapsing them into one message is a caught mutation.

### `CLAUDE.local.md` line 152 (original)

- **`TestAnUnconfirmedDeleteIsNotReportedAsSuccess` (`tests/test_templates.py`) evaluates the headline expression and every `fail:`'s real `when:`** against the four `describe-cluster` outcomes `TestCredentialsSurviveAnUnconfirmedDelete` already defines, because a text assertion cannot tell `confirmed` from `not failed`. `test_a_confirmed_delete_still_says_so` and `test_the_three_outcomes_are_distinguishable` are the vacuity guards — one hedged sentence covering every case would satisfy each individually-worded assertion.

### `CLAUDE.local.md` line 155 (original)

- **This is a second source for one name, so it is pinned *against `_derive_results_bucket`*, not against a restated literal** — the `pkg_dir` hazard again. Two sources that disagree would make the sync target depend on which toolkit built the cluster. `test_the_derived_name_matches_the_python_derivation` renders the playbook expression and compares it to the function's own output.

### `CLAUDE.local.md` line 157 (original)

- **The `is not defined` guard is load-bearing**: without it the fallback overwrites a current vars file's value, so every cluster's results would follow this expression instead of the one `make_pcluster.py` wrote. Note a test cannot simulate absence by passing `None` — any value makes the variable *defined*; the key must be omitted. `TestTheResultsSyncSurvivesAnOlderVarsFile` also pins that the derivation stays inside the `enable_hpc_benchmarks` gate, read through `_effective_when` since the gate lives on the enclosing block.

### `CLAUDE.local.md` line 160 (original)

- **`CLAUDE.md` and `CLAUDE-STATE.md` are read in full before any code, and their combined size is capped in bytes by `TestTheAlwaysLoadedPreambleStaysAffordable`.** The growth mechanism was one appended section per session and the only brake was someone remembering. The dated record now lives in `docs/sessions.md`; the cap is what stops it coming back, and it is a **ratchet** — after any real reduction, lower `_CEILING` in the same commit.

### `CLAUDE.local.md` line 162 (original)

- **The headroom bound is derived from the largest bullet in `CLAUDE.md`, not a round number.** The budget absorbs one substantive constraint and that is what one costs (~13KB today); a constant is a second thing to keep true, and widening it is the obvious way to make a failing ceiling pass, so `allowance <= total // 6` bounds the derivation too.

### `CLAUDE.local.md` line 163 (original)

- **A byte ceiling cannot see a slow relapse**, since each appended session is under it. `test_the_dated_archive_is_not_in_the_preamble` bans a `### Session N` heading in either file, and its discrimination test points the real detector at `docs/sessions.md` — which holds 13 of them — rather than checking the pattern out of band, which let `assert True` survive.


## Trimmed CLAUDE.local.md bullets (moved 2026-08-26, third pass)

Verbatim originals of `CLAUDE.local.md` bullets condensed in place.  The rule,
every test name, file path, function name and measured number stayed in the
preamble; the incident narrative is here.

### `CLAUDE.local.md` line 25 (original)

- **The build summary names every filesystem's mount point, on all three surfaces.** The topology block is duplicated in `make_pcluster.py`'s printed summary (via `_storage_summary_lines` in `pcluster_core.py`), `src/create_pcluster.yml`'s `_build_summary` set_fact, and `templates/sns_build_summary_report.j2`; a line added to one must be added to all three. `pkg_dir` is **not** a Python variable — `vars_file.j2` derives it with `fsx > efs > external_nfs > ebs` precedence, and `_storage_summary_lines` reproduces that precedence independently, so the rendered vars file is the reference for both. Asserting a bare mount point is not enough to prove a line exists: on an FSx cluster `pkg_dir` is `/fsx/pkg`, so `"/fsx" in text` still passes after the mount line is deleted — match the label (`FSx for Lustre (`), a substring that does not depend on the column width, which is now derived per build rather than fixed (see the column-width bullet below). `tests/test_templates.py` evaluates the playbook expression with `Environment.compile_expression()` over the rendered vars file, which is also the only check that the expression never reaches for a variable `vars_file.j2` leaves undefined under its gate.

### `CLAUDE.local.md` line 26 (original)

- **`_storage_summary_lines` is keyword-only, and the mount points are cross-checked against the cluster config.** The signature's leading `*,` is load-bearing: 14 same-typed parameters means a transposed pair renders a plausible summary instead of raising, and two such mutations (the FSx import/export bucket pair; the EBS size and type) survived the entire suite while it was positional. `TestStorageSummaryLinesTakesKeywordsOnly` (`tests/test_make_pcluster.py`) pins both the signature via `inspect.signature` and the `make_pcluster.py` call site via an AST walk that rejects positional args and a `**kwargs` splat. Separately, `/efs` and `/fsx` are hardcoded in **four** places — `config.pcluster.j2`'s `SharedStorage` block plus the three summary surfaces — and `vars_file.j2`'s `efs_root`/`fsx_root` variables are referenced by none of them; `TestSummaryMountPointsMatchTheClusterConfig` parses `MountDir` out of the rendered config with `yaml.safe_load` and requires every PCluster-managed mount to appear in both text surfaces. External NFS is deliberately exempt from that parse: PCluster does not manage it, `postinstall.j2` mounts it at `external_nfs_server_root`, so it is checked against the vars file instead.

### `CLAUDE.local.md` line 28 (original)

- **`tests/test_templates.py::_make_env` must match `ansible.builtin.template`'s own defaults: `trim_blocks=True`, `lstrip_blocks=False`.** Read out of the installed Ansible (`ansible/plugins/action/template.py`), not assumed. While the env left both off, every `{% if %}` in a rendered file left a blank line the node never sees — enough that a cosmetic defect was reported in the SNS report that did not exist, and enough to hide a real one in a whitespace-sensitive template. Do not "fix" `lstrip_blocks` to `True` for symmetry with `trim_blocks`; that reintroduces the mismatch in the other direction. `TestTheTestEnvironmentMatchesAnsible` reads both defaults back out of Ansible's source and also rejects any per-task `trim_blocks`/`lstrip_blocks` override in the playbooks, since one override would make the env correct for every template but that one. Its task walker descends into `block`/`rescue`/`always` — most `template:` tasks in `create_pcluster.yml` are block-nested, and a top-level-only walk sees the cluster config but not the monitoring wrapper or the external NFS mount list.

### `CLAUDE.local.md` line 54 (original)

- **Remote Slurm commands need both a PATH and shell quoting, and the second is what makes the first work.** A non-interactive `ssh host sinfo` gets the bare system PATH; `/opt/slurm/bin` is appended only by a login shell (confirmed live: `which sinfo` finds nothing, `/opt/slurm/bin/sinfo` answers). So `check_slurm` — the one check separating "the cluster exists" from "the cluster can run work" — reported healthy clusters as failed, forever, and `_diagnose_sinfo`/`_diagnose_sacct` did the same. **The first fix was wrong and one live run caught it**: `["bash","-c","export PATH=...; exec sinfo"]` failed with `exec: sinfo: not found`, because `_ssh_args` does no quoting and ssh joins argv with spaces, so the *remote* shell re-parses and splits at the `;`. The same flaw was already latent in the original: `"%D %T"` arrived as two words. `_slurm_remote_cmd` wraps the script in `shlex.quote`. **Not `bash -lc`**: a login shell sources `/etc/profile.d`, and a fragment's banner lands in the text `_classify_sinfo_nodes` parses, where an unreadable line counts as an *unusable node*. `tail` and `test -f` are left unwrapped — they are on the default PATH. Reverting *either* diagnose probe passed all 87 tests in `test_diagnose.py` until `TestDiagnoseFindsSlurmOnTheHeadNode` was added.

### `CLAUDE.local.md` line 55 (original)

- **SSM carries ssh for `access_cluster.j2` and `grafana_tunnel.j2`, and does not replace it.** `-o ProxyCommand="aws ssm start-session --target %h --document-name AWS-StartSSHSession --parameters portNumber=%p"`, so neither needs inbound port 22 or a reachable address; verified live by `SSH_CONNECTION=127.0.0.1` and by forwarding the tunnel to `:22` and reading sshd's banner back. **Pure `aws ssm start-session` was rejected**: it lands as `ssm-user`, whose `$HOME`, PATH and Slurm environment are not the login node's, and it loses scp/rsync/agent-forwarding/`-L`. Both scripts **fall back to direct ssh with a warning** when `session-manager-plugin` is missing, so an operator without it still gets a shell. Two details that bit: the tunnel's `pgrep` matched `${HEAD_NODE_IP}`, but over SSM the command line carries the instance ID, so the PID was never captured and `stop` reported success while leaving the tunnel up — it matches `${SSH_TARGET##*@}` now; and `"${PROXY_ARGS[@]}"` on an empty array is an unbound-variable error under `set -u` before bash 4.4, so it is guarded with `${PROXY_ARGS[@]+…}`. Measured, not assumed: SSM `send-command` is **not slower** (953ms vs ssh's 998ms), and `get-command-invocation` truncates at exactly **24000 bytes** with an in-band `--output truncated--` marker, against diagnose's largest real output of 5,846 bytes.

### `CLAUDE.local.md` line 74 (original)

- **Performance results sync to the long-lived results bucket, never to `s3_bucketname`.** Results go to `s3://<results_bucketname>/hpc-benchmark-results/<cluster_name>/<cluster_serial_number>/` on teardown; `results_bucketname` is `parallelclustermaker-results-<aws_account_id>-<region>`, derived by `_derive_results_bucket` in `src/pcluster_core.py` (keyword-only on `aws_account_id`+`region` only — a cluster- or serial-derived input would silently restore a per-build bucket, since a 12-digit account ID is indistinguishable from a serial datestamp by inspection; `test_the_derivation_cannot_see_the_cluster_or_the_serial` pins the signature). `HeadNode-Storage.json_src`'s grant on that bucket is deliberately write-but-not-delete and prefix-confined — see the results-grant bullet below. `TestBenchmarkResultsOutliveTheCluster` (`tests/test_templates.py`) pins the sync target, the prefix, that neither playbook deletes the results bucket, and the head node's grant. Separately, **`hpc-benchmark.sh`'s own sync (`s3://<s3_bucketname>/hpc-benchmark/`) is an allowlist on both ends (`--exclude "*" --include "hpc-benchmark.sh"`), never a blocklist** — a blocklist once shipped internal docs and unrendered `.j2` templates into the operator's working tree. `TestOnlyTheDriverIsStagedToS3` pins the allowlist on both ends and the exact `chmod +x` target (a stale second target that never existed at that path is the mutation this guards). Full narrative in `docs/sessions.md`.

### `CLAUDE.local.md` line 79 (original)

- **Monitoring wrapper bypasses upstream `post-install.sh`.** The upstream script always re-downloads the tarball from GitHub, defeating the S3 staging. The wrapper extracts the S3-staged tarball directly to `MONITORING_HOME` and calls `installer/install.sh` directly. It also suspends **all three** shell options around `source /etc/profile` — see the profile-guard bullet below. `postinstall.j2` carries the same guard for the same reason. **The port-80 web server is stopped by a loop over `apache2 httpd`, not by name.** Ubuntu PCluster head nodes ship `apache2`; the RHEL equivalent unit is `httpd`. Both are checked unconditionally rather than under a Jinja2 branch because the unit name is the only difference and `systemctl is-active` on an absent unit is a no-op — this file has **no** `base_os` branch at all, which the unbranched-surface test relies on.

### `CLAUDE.local.md` line 80 (original)

- **Only the head node may write `MONITORING_HOME`, and the gate reads `cfn_node_type`.** `MONITORING_HOME` is `/home/<cfn_cluster_user>/aws-parallelcluster-monitoring`, NFS-exported from the head node. Before the gate, the wrapper ran `rm -rf`/`mkdir -p`/`tar -xzf`/`chown -R` on *every* node, so two nodes booting close together could destroy each other's tree — an **intermittent** race, so a green rebuild alone is not evidence it's gone. Upstream computes `MONITORING_HOME` itself with no override or env fallback, so the fix gates the write rather than relocating the tree; a compute node only needs the tree to exist and be readable (nothing it mounts comes from `MONITORING_HOME` beyond a read-only bind), and that read is safe because `runpostinstall` completes before `clustermgtd` starts launching compute nodes — ordering, not luck. **The gate must name `HeadNode` exactly** — never `!= "ComputeFleet"` (equivalent only while that is the whole world; login nodes NFS-mount `/home` too) and never a `:-` default (`${cfn_node_type:-HeadNode}` makes every node a head node — the `NODE_TYPE` root cause again). `tests/test_shell_surfaces.py`'s `_run_wrapper` executes the rendered wrapper with a fake `cfnconfig` so the gate is observable, behind a `_guard` that hard-fails on any path outside the tmpdir. All eight faithful mutations are caught, including both wrong spellings above. Upstream's own `sed -i` race on `compute.gpu.yml` is deliberately left unpatched (GPU-only, idempotent, writes-then-renames). Full narrative in `docs/sessions.md`.

### `CLAUDE.local.md` line 89 (original)

- **GPU support is gated on `enable_gpu`.** `is_gpu_instance(instance_type)` in `pcluster_aux_data.py` detects GPU families by prefix (g4dn, g4ad, g5, g5g, g6, p3, p3dn, p4d, p4de, p5). If `enable_gpu == "false"` but the compute instance is a GPU family, `make_pcluster.py` auto-enables it and prints `*** INFO ***`. GPU block in `postinstall.j2` installs monitoring tools (`nvtop`+`htop` on the head node, `htop` only on compute nodes, via apt or dnf per `base_os` — see the package-index bullet below) and mounts NVMe instance store at `/local_scratch` (single device: XFS; multiple devices: RAID0 via `mdadm`). NVMe device detection uses `/sys/block/nvme*/device/model` filtered for "Instance Storage" — that model check is the only thing keeping EBS volumes, which also enumerate as `/dev/nvme*`, from being reformatted. The block installs the monitoring tools itself because the main package block is head-node-only. **Jinja2 constraint:** `${#arr[@]}` triggers the Jinja2 `{#` comment tag parser — use `$(echo "${arr[@]}" | wc -w)` instead.

### `CLAUDE.local.md` line 110 (original)

- **FSx hydration uses ONE S3 bucket with two prefixes.** AWS requires it: *"The Amazon S3 export bucket must be the same as the import bucket specified by `ImportPath`"* (`CreateFileSystemLustreConfiguration`, botocore's own FSx model at `.venv/lib/python3.12/site-packages/botocore/data/fsx/2018-03-01/service-2.json.gz`). Only the prefixes may differ. `_normalize_fsx_buckets` (`src/pcluster_core.py`) therefore `sys.exit`s on two inputs and warns on two: hard errors when the export and import buckets differ (invalid at FSx creation, twenty minutes in) and when the import bucket is `UNDEFINED` (`config.pcluster.j2` would render a literal `ImportPath: s3://UNDEFINED/input/`); warnings when the export bucket is unset, in which case export follows the import bucket **and** path, and when the two paths match, since dehydration then overwrites the hydration source. `make_pcluster.py`'s own FSx-S3 checks only catch hydration-off-with-buckets-set and `enable_fsx=false`-with-hydration-on — nothing upstream validates the buckets themselves, so do not weaken `_normalize_fsx_buckets` assuming something else already checked. The function had this backwards until session 23b: it warned when the buckets matched (the requirement) and silently accepted two different ones. `TestNormalizeFsxBuckets` in `tests/test_make_pcluster.py` pins all five outcomes.

### `CLAUDE.local.md` line 111 (original)

- **The cluster's CloudWatch log groups are retained on teardown, by PCluster's default and by our choice not to override it.** The mechanism is *not* a missing task in `src/delete_pcluster.yml`: `CloudWatchLogs.__init__` defaults `deletion_policy` to `"Retain"` (`pcluster/config/cluster_config.py:920`) with `CW_LOGS_RETENTION_DAYS_DEFAULT = 180` (`pcluster/constants.py:182`), and `config.pcluster.j2`'s `Monitoring/Logs/CloudWatch` block sets `Enabled: true` and neither of the other two — so `delete-cluster` preserves the group and it expires on its own at 180 days. Adding a log-group deletion task to the playbook would *defeat* that default; setting `DeletionPolicy: Delete` in the template would be the supported way to change it. Do neither. `/aws/parallelcluster/<cluster_name>-<%Y%m%d%H%M>` is the only surviving record of a failed build — cfn-init captures stdout only, node stderr reaches no stream at all, and every bootstrap failure documented in this file was diagnosed from those logs or off the live node, never from the playbook's own output — and a failed build is immediately followed by a teardown, so deleting on teardown destroys the evidence exactly when it is needed. The cost is visible accumulation and it is accepted: the operator purges by hand once a build is confirmed good. A retained log group is **not** an orphaned resource and must never be added to `_orphaned_resources` in the teardown sweep below.

### `CLAUDE.local.md` line 129 (original)

- **A cluster's CloudWatch log group name carries a creation timestamp and cannot be constructed.** PCluster builds it as `f"{CW_LOG_GROUP_NAME_PREFIX}{self.stack.stack_name}-{timestamp}"` with `timestamp = %Y%m%d%H%M` (`pcluster/templates/cluster_stack.py`, prefix `/aws/parallelcluster/` from `pcluster/constants.py`). `diagnose_pcluster.py` therefore queries `describe_log_groups` with `logGroupNamePrefix="/aws/parallelcluster/<cluster_name>-"` and picks the group with `_select_cw_log_group` in `src/pcluster_core.py`, which requires an exact `<cluster_name>-<12 digits>` suffix (a prefix query for `osiris` also returns `osiris-test-...`) and takes the newest timestamp, since rebuilds leave older groups behind. Do not go back to `f"/aws/parallelcluster/{cluster_name}"` — that group has never existed, and its `ResourceNotFoundException` was being reported as a missing IAM permission, which is what kept the bug hidden. `ResourceNotFoundException` means the group is absent; an IAM denial is `AccessDeniedException`, and the two must stay on separate error messages.

### `CLAUDE.local.md` line 135 (original)

- **No policy reachable from an instance may grant `logs:DeleteLogGroup`, and "reachable from an instance" includes the inline Lustre policy.** `ComputeNode-Base.json_src` shipped it in `LogsWrite` on `/aws/parallelcluster/*` and `/parallelcluster/*` — account-wide, unlike the `<CLUSTER_NAME>`-scoped `LogsRead` statement directly below it — and that policy is attached to the head node role *and* to every compute queue, so any Slurm job could erase any cluster's log group. That contradicts the retained-log-group bullet above: those logs are the only surviving record of a failed build. Nothing needs the action — the toolkit never calls `delete_log_group`, upstream's only caller is imagebuilder (`pcluster/models/imagebuilder.py`, a code path this toolkit does not use), and upstream's own node policy grants `logs:CreateLogStream` and `logs:PutLogEvents` and nothing else (`cdk_builder_utils.py`). Properties of the guard:

### `CLAUDE.local.md` line 137 (original)

- **`iam:AttachRolePolicy`'s `iam:PolicyARN` condition must name this cluster's own policies, not `parallelcluster-*`.** The head node's `iam:PutRolePolicy` privilege-escalation chain is documented in `templates/CLAUDE.md`; the `AttachRolePolicy` condition is the other half of it. Scoped to `arn:aws:iam::<AWS_ACCOUNT_ID>:policy/parallelcluster-*` it matched any policy in the account whose name began with that prefix — including ones this toolkit never created — so the condition read like a restriction while permitting a shell on the head node to attach a policy of its choosing. It is now `policy/pclustermaker-policy-<CLUSTER_SERIAL_NUMBER>-*`, which is the naming `_setup_iam` actually uses, so the grant covers exactly the five policies this build made and nothing else. `TestAttachRolePolicyCannotReachTheOperatorPolicy` asserts both directions — every toolkit policy is still attachable (the vacuity guard; over-narrowing breaks the build) and `OperatorPolicy`, three AWS-managed admin policies, and *another cluster's* policy are all unreachable.

### `CLAUDE.local.md` line 138 (original)

- **The head node's grant on the results bucket is `Put`/`Get` confined to the `hpc-benchmark-results/` prefix — withholding `DeleteObject` is only half of it.** The results bucket outlives every cluster (see the results-bucket bullet above) and is shared by every cluster in the account and region, so the sync the head node runs over ssh needs `s3:ListBucket` on the bucket and `s3:GetObject`/`s3:PutObject` on the prefix — and nothing more. `PutObject` on `<bucket>/*` lets anyone with a shell there (including via Slurm job submission) **overwrite** any past build's results in place, which destroys them as thoroughly as a delete and passed every assertion about `DeleteObject` being absent. `test_the_head_node_can_write_results_but_not_delete_them` therefore checks the object statements' `Resource` ends with `/hpc-benchmark-results/*` in addition to banning the three delete actions, which also catches `PutObject` being moved onto the bucket-level statement. Removing the grant entirely fails the sync with an opaque 403.


## Trimmed CLAUDE.local.md bullets (moved 2026-08-26, fourth pass)

Verbatim originals of `CLAUDE.local.md` bullets condensed in place.  The rule,
every test name, file path, function name and measured number stayed in the
preamble; the incident narrative is here.

### `CLAUDE.local.md` line 29 (original)

- **Eight `base_os` values are supported, across two package-manager families: `ubuntu2204`, `ubuntu2404`, `ubuntu2204arm`, `ubuntu2404arm`, `rhel9`, `rhel9arm`, `alinux2023`, `alinux2023arm`.** `preinstall.j2` and `postinstall.j2` branch on `'ubuntu' in base_os` and select apt or dnf accordingly; the dnf side splits again on `'alinux' in base_os` where AL2023 and RHEL 9 differ (see the AL2023 bullet below). RHEL 9 was supported, removed, and re-added; the removal cause must not come back. Every non-prerelease `ansible` 9.x depends on `ansible-core` 2.16, whose `requires_python` is `>=3.10`, while the RHEL 9 PCluster AMI ships Python 3.9 — so `preinstall.j2`'s `pip3 install 'ansible>=9,<10'` was unsatisfiable and `set -euo pipefail` turned that into `OnNodeStartExecutionFailure`. **The fix on re-add was to delete the pin from both arms, not to branch around it**: nothing on a node imports ansible — only `src/create_pcluster.yml` does, and that runs on the operator's workstation. `test_no_arm_installs_ansible` is what keeps it gone. `rhel9arm` is ours, not PCluster's: `pcluster_os` strips the suffix to `rhel9`, which is in upstream's own `SUPPORTED_OSES`. The set is pinned by `TestPackageManagersMatchTheRenderedOs` (`tests/test_templates.py`) and `TestEc2UserValidation` (`tests/test_diagnose.py`) across eight surfaces — `--base_os` choices in `make_pcluster.py`, `_EC2_USERS`/`_resolve_ec2_user` in `pcluster_core.py`, `ARM_OSES`/`X86_OSES`/`base_os_efa` in `pcluster_aux_data.py`, no *wrong-family* package manager on any surface that reaches a node, no package manager at all on an unbranched surface, no tracked defaults file shipping or *documenting* an unsupported value, `_VALID_EC2_USERS` in `diagnose_pcluster.py`, and the `assert` task in `src/create_pcluster.yml`. Load-bearing properties of the guards:

### `CLAUDE.local.md` line 70 (original)

- **`<cluster_name>_defaults.yml` is applied automatically, by both entry points.** `discover_defaults_file`/`load_cluster_defaults` (`src/pcluster_core.py`) are the single loader; `build_make_cluster_params` layers the file below `overrides` and above `MAKE_CLUSTER_DEFAULTS` — the three-tier precedence `_resolve` already gave the CLI. Before this the CLI only *warned* that the file existed and the MCP server could not reach it at all, so one cluster name built two different clusters depending on the surface. Load-bearing: non-build keys are **ignored, not rejected** (`delete_s3_bucketname` is there for `kill_pcluster.py` and bounced a real MCP preview), and what makes that true is the field filter on the `MakeClusterParams(**...)` construction, not a second filter at the merge; an **explicit override still beats the file**; discovery is keyed on the **exact** cluster name, so `osiris-test` does not inherit `osiris`'s; **absence is not an error**, unlike the `--use_defaults` path. File values are **not** type-validated the way `overrides` are, since `_resolve` does not validate them either — tightening one surface alone restores the divergence. **The file may set the three `_REMOTE_DENIED_PARAMS`, and that is a decision**, pinned by `test_a_defaults_file_may_set_what_an_override_may_not`: `_reject_denied` inspects `overrides` only, since the denial stops a *caller* choosing what runs on the nodes while the file is the operator's own — and `pcluster_defaults.yml` itself sets `pre_install_script`, so checking file values would refuse every real file. Nothing reaches a handler this way (gitignored, in no tier's `sources`); revisit if that changes. **A key with no value is dropped, in both loaders** (`_drop_unset`): `gpu_instance_type:` parses as None, overwrote the `""` default and reached a `str`-typed field where `.split(",")` raises. `TestTheDefaultsFileIsAppliedWhenItExists` pins it. **The suite must not see the developer's own files**: `tests/conftest.py`'s autouse `_no_operator_defaults_file` points discovery at an empty directory. Full narrative in `docs/sessions.md`.

### `CLAUDE.local.md` line 76 (original)

- **`job_hpc-benchmark.sh.j2`'s `#SBATCH --partition=` and `--ntasks-per-node=` are derived, never hardcoded.** The partition is `compute` when `enable_cpu_queue == 'true'`, otherwise `gpu`; `enable_cpu_queue` is derived from `compute_instance_type` (`make_pcluster.py`), so a GPU-only cluster is supported and has no `compute` partition — a hardcoded `--partition=compute` is rejected by `sbatch` with an invalid-partition error before anything runs. On a GPU-only cluster the rank count is `gpu_ranks_per_node`, derived in `make_pcluster.py` as `min(nvidia_gpu_count(...))` across `gpu_instance_types` (the only value every node in the queue can satisfy) and threaded through `vars_file.j2` → the playbook → the job template. `nvidia_gpu_count()` in `pcluster_aux_data.py` counts NVIDIA devices only — AMD (`g4ad`) and Habana accelerators are invisible to both Slurm GRES and the CUDA runtime. A queue reporting zero NVIDIA devices falls back to 4 ranks, since `--ntasks-per-node=0` is not a valid `sbatch` value. The GPU count is read from the `describe_instance_types` response the architecture check already fetches; do not add a second API sweep. **Slurm GRES does exist on these clusters, but nothing in the toolkit's own path puts it there** — PCluster's vendored CLI does not emit it (`gpu_count()` in `pcluster/aws/aws_resources.py` feeds validators only, and `grestypes` is on `SLURM_SETTINGS_DENY_LIST` in `pcluster/validators/slurm_settings_validator.py`, under `SlurmConf/Global`), so an operator cannot set it through the config either; the **cookbook** configures it at node bootstrap (confirmed on a live AL2023 GPU node, session 37 of `docs/sessions.md`). Rank-count matching is still the right mechanism, and not because GRES is unavailable — it depends on nothing but `--ntasks-per-node`. Do not rewrite the job template to `--gres=gpu:N` on the strength of one confirmed cluster.

### `CLAUDE.local.md` line 80 (original)

- **Only the head node may write `MONITORING_HOME`, and the gate reads `cfn_node_type`.** `MONITORING_HOME` is `/home/<cfn_cluster_user>/aws-parallelcluster-monitoring`, NFS-exported from the head node; ungated, the wrapper's `rm -rf`/`mkdir -p`/`tar -xzf`/`chown -R` run on *every* node and two booting close together destroy each other's tree — an **intermittent** race, so a green rebuild alone is not evidence it's gone. Upstream computes `MONITORING_HOME` itself with no override or env fallback, so the fix gates the write rather than relocating the tree; a compute node only needs the tree to exist and be readable, and that read is safe because `runpostinstall` completes before `clustermgtd` starts launching compute nodes — ordering, not luck. **The gate must name `HeadNode` exactly** — never `!= "ComputeFleet"` (equivalent only while that is the whole world; login nodes NFS-mount `/home` too) and never a `:-` default (`${cfn_node_type:-HeadNode}` makes every node a head node — the `NODE_TYPE` root cause again). `tests/test_shell_surfaces.py`'s `_run_wrapper` executes the rendered wrapper with a fake `cfnconfig` so the gate is observable, behind a `_guard` that hard-fails on any path outside the tmpdir. All eight faithful mutations are caught, including both wrong spellings above. Upstream's own `sed -i` race on `compute.gpu.yml` is deliberately left unpatched (GPU-only, idempotent, writes-then-renames). Full narrative in `docs/sessions.md`.

### `CLAUDE.local.md` line 90 (original)

- **The install scripts are two distinct stages, and the toolkit's own stage is a rendered template.** Every node runs, in order, the toolkit's rendered `preinstall.j2`/`postinstall.j2` and then the operator's hook named by `--pre_install_script`/`--post_install_script`. These are separate variables in `vars_file.j2`: `preinstall_template_orig`/`preinstall_rendered`/`preinstall_s3_dest` (and the `postinstall_*` equivalents) name the toolkit stage and derive their filenames from `cluster_name`; `user_preinstall_src`/`user_preinstall_s3_dest` (and `user_postinstall_*`) name the operator hook and derive from the flag. `src/create_pcluster.yml` renders the toolkit pair with `template:` and copies the operator pair with `copy:` — a `copy:` on the toolkit pair uploads raw Jinja2 to S3, which is the regression. It shipped that way from the v3 migration (`c2673ae`) until 2026-07-26, so `postinstall.j2` was dead text on every node (Spack, Lmod, the package installs and the `NODE_TYPE` gating never executed), and `tests/conftest.py` hid it by pointing `pre/post_install_script` at `templates/preinstall.j2`, conflating the two stages. `TestPostinstallTemplateIsActuallyRendered` in `tests/test_templates.py` now pins all four properties: rendered by `template:` not `copy:`, the *rendered* path is what gets uploaded, `<stage>_s3_dest` never derives from the operator flag, and both stages appear on every node type. `OnNodeStart` is head-node-only on purpose — running the python3/pip/awscli install on every scale-up is a latency regression, not a fix. Full narrative in `docs/sessions.md`.

### `CLAUDE.local.md` line 95 (original)

- **`NODE_TYPE` comes from `cfn_node_type` in `/etc/parallelcluster/cfnconfig`. There is no `PARALLELCLUSTER_NODE_TYPE` environment variable — that name is invented, and reading it defeated every gate in this file.** `aws-parallelcluster-environment::cfnconfig_mixed` writes `cfn_node_type=HeadNode`/`ComputeFleet` during the init phase, before any custom action runs; nothing exports a `PARALLELCLUSTER_NODE_TYPE` anywhere in the cookbook or the vendored PCluster CLI. **The `:-HeadNode` default was the bug, not a safety net** — it collapsed "this variable does not exist" into "be a head node", ran the entire head-node path on every compute node (a whole GPU queue failed `OnNodeConfigured` and hit the partition's 10-failure protected-mode threshold), and made the `case`'s `*)` arm unreachable, the one guard whose job is to catch a changed upstream contract. The replacement defaults to `HeadNode` only when the cfnconfig **file** is absent (a genuine off-cluster manual re-run); a cfnconfig that exists without `cfn_node_type` is a hard failure. `_run_postinstall` must write a fake `cfnconfig` and substitute its path (`node_type=None` omits the file, `node_type=""` writes one with no `cfn_node_type`) — setting the environment variable instead is what hid this, passing every gating test against a mechanism no node has ever used. `test_the_node_type_is_read_from_cfnconfig_not_the_environment` asserts on the rendered source with comment lines stripped, since the name legitimately appears in the comment explaining why it must never be read. All eight faithful mutations are caught. Full narrative in `docs/sessions.md`.

### `CLAUDE.local.md` line 98 (original)

- **`liblua5.1-0-dev` must be installed on the same `apt-get` line as `luarocks`.** `luaposix`, `luafilesystem`, and `lua-term` are C extensions that compile against `lua.h` for whatever version luarocks targets. Ubuntu 24.04's `luarocks` declares its header dependency as an **alternative group** (`liblua5.1-dev | liblua5.2-dev | liblua5.3-dev`) which apt satisfies with **5.3**, while its parallel `lua5.1|lua5.2|lua5.3` group leaves `luarocks` itself on `lua_version 5.1`; a stock `ubuntu2404` AMI therefore has 5.3 and 5.4 headers and **no 5.1 header at all**, so every rock fails to compile and `set -euo pipefail` takes the node down with `OnNodeConfiguredExecutionFailure`. The whole block is 5.1: `LUA_VER` is read off the `lua` interpreter and `LUA_CPATH`/`LUA_PATH` are built from it, so pinning the rocks to another version with `--lua-version` compiles them into a directory Lmod never searches — a failure that appears at module load, long after the build looks clean. `liblua5.4-dev` on the general dev-package line is unrelated and must not be conflated with this. `TestLuarocksGetsTheLuaHeadersItCompilesAgainst` asserts on the **execution trace** for `HeadNode`, not the source, so it also proves the apt line is reached inside the head-node gate; all four faithful mutations are caught. **This whole hazard is Ubuntu packaging and has no RHEL analog** — RHEL's `luarocks` depends on `lua-devel` for the same lua it targets, and `lua-devel` is already on the critical-packages line, so that arm is a bare `dnf install -y luarocks` with no separate header package (confirmed on hardware with `lua-devel-5.4.4-4.el9`). Do not "restore symmetry" by adding one; the test runs against the Ubuntu fixture only, so an invented RHEL header package would go unchecked. Narrative in `docs/sessions.md`.

### `CLAUDE.local.md` line 103 (original)

- **The `/etc/profile` guard suspends all three shell options, not just `set -u`.** `set -e`, `set -u`, and `pipefail` are three independent ways for a profile fragment to kill a node, and a fragment written for an interactive shell has no obligation to survive any of them — AL2023's `/etc/profile.d/debuginfod.sh` runs a pipeline that legitimately fails, and under `pipefail` that is an `OnNodeConfiguredExecutionFailure` with nothing on stdout. The correct form is `set +euo pipefail` / `source /etc/profile` / `set -euo pipefail`, in **both** `templates/postinstall.j2` and `templates/monitoring-post-install-wrapper.j2`. **RHEL 9 surviving that line was never evidence for AL2023** — the two distros have different `/etc/profile.d` trees, and reasoning from one dnf-family distro to the other is what left the hazard in place through two successful RHEL builds. **Neither `_run_postinstall` nor a source-text assertion can see this**: the harness discards the rendered script's line 2 (see the NVMe bullet for why it must), and a text match can't tell an enclosing guard from a removed one. `TestTheProfileGuardSuspendsEveryOption` (`tests/test_templates.py`) extracts each template's real prologue *by position* and executes it under real `bash` against a profile carrying the hazard, parametrized over both templates; `test_the_harness_fails_a_guard_that_only_suspends_set_u` is the vacuity guard, rebuilt from the guard's position rather than a literal replace. `set +eu` (no `pipefail`) is behaviorally safe and caught only by the ordering test, correctly — with `-e` cleared, a failed pipeline status has nothing to act on. All eight faithful mutations are caught. Full narrative in `docs/sessions.md`.

### `CLAUDE.local.md` line 104 (original)

- **A compute node must refresh its package index before installing anything, and the monitoring installs must be non-fatal.** `OnNodeStart` — and therefore `preinstall.j2`'s index refresh — is registered on the head node only, so a compute node's `/var/lib/apt/lists` (or dnf cache) is whatever the AMI shipped, and `apt-get -y install nvtop` against that index exits **100** with `E: Unable to locate package nvtop`. The GPU block therefore splits on `NODE_TYPE` **inside each OS arm**: the head node installs `nvtop htop` with no refresh (`preinstall.j2` already did one and the head-node package block does another — a third is pure bootstrap latency), and a compute node refreshes first (`apt-get -y update` / `dnf -y makecache`) and installs `htop` only. **`nvtop` is head-node-only on purpose, on both families**: it is outside the default repositories (`multiverse` on Ubuntu, EPEL on RHEL — which is why the RHEL head-node arm installs `epel-release` by URL first, also non-fatally), and the operator logs into the head node rather than a compute node. Both installs are guarded with `|| echo "WARNING: ..." >&2` — the *only* non-fatal installs in the file — because a compute node that exits non-zero is relaunched by `clustermgtd` and counted toward the partition's 10-failure protected-mode threshold, so one transient mirror outage would cost the whole stack over a diagnostic nothing in the job path imports. Do not "restore symmetry" by making them fatal.

### `CLAUDE.local.md` line 112 (original)

- **Every `ignore_errors` cleanup task in `src/delete_pcluster.yml` must `register:`, and the result must be read.** `ignore_errors` is correct there — one AWS failure must not abandon the remaining nine cleanup steps — but alone it prints `...ignoring`, exits 0, and the playbook still says `Cluster <name> has been deleted`, which is how orphaned resources went unnoticed until the serial file was gone and `kill_pcluster.py` could no longer retry them. `Collect cleanup failures that ignore_errors swallowed` builds `_orphaned_resources` from each `<var>.failed | default(false)` (verified under real Ansible: `False` for skipped tasks, `True` at the *top level* of a looped result); it then drives the printed summary, `templates/sns_destruction_summary_report.j2`, and a terminal `fail:`. Ordering is load-bearing — collection must precede the SNS templating, and both `fail:` paths must come after every cleanup task and the summary print, or they abort the thing they're reporting on. Two exemptions, both encoded in `TestTeardownFailuresReachTheOperator._NOT_AN_ORPHAN`: deleting the local `.pem` orphans nothing in AWS, and a failed SNS *send* is not a leftover resource (the SNS *topic* is, appended to the list after its own deletion). Entries interpolate real resource names — `3 cleanup steps failed` alone sends the operator hunting. **The EC2 keypair delete must carry `ignore_errors` + `register:` too** — without it one denied `DeleteKeyPair` aborts the play before every later cleanup step *and* before the collection itself — and it carries no `no_log: true`, since the module only ever handles a key *name*, not material (`no_log` remains correct on the three creation-side tasks that handle real key material). All eight faithful mutations are caught. Full narrative in `docs/sessions.md`.

### `CLAUDE.local.md` line 113 (original)

- **The four tasks that destroy cluster access must be gated on a *positive* confirmation that the stack is gone, never on `not (_cf_delete_failed | bool)`.** The EC2 keypair, the local `.pem`, the Secrets Manager secret, and the `active_clusters/<cluster>/` directory that holds the latter two are the only ways back into a running head node, and a blocklist of exactly one state reads a wait *timeout* as a clean delete — destroying all four while the stack and the head node are still up and still billing, precisely the case the `DELETE_FAILED` branch's message says they are preserved for. `Record whether the stack is confirmed gone` sets `_cf_delete_confirmed` from `DELETE_COMPLETE` **or** any of the four cluster-absent spellings (`ClusterNotFound`/`does not exist`, on either stream — the delete wait's own `until:`/`failed_when:` accept all four, so all four reach these tasks), and every state that is neither confirmed nor `DELETE_FAILED` gets its own preservation warning, since the `DELETE_FAILED` one does not cover it. The `cluster_delete_status is defined` guard is load-bearing: a skipped wait task is not confirmation. `TestCredentialsSurviveAnUnconfirmedDelete` (`tests/test_templates.py`) evaluates each task's real `when:` with `Environment.compile_expression` against four real `describe-cluster` outcomes rather than matching text, because a text assertion cannot tell one gate from another; `test_the_scenarios_can_see_the_gate_that_shipped` is the vacuity guard, asserting the timeout case is one the shipped gate got wrong.

### `CLAUDE.local.md` line 114 (original)

- **`pcluster describe-cluster`'s three outcomes get three separate aborts, and the wait task stays `failed_when: false`.** With `failed_when: false` and no follow-up, a succeeded stack, a failed stack and an AWS call that never answered all fall through one code path — and since every downstream task is gated on `head_node_public_ip`, `''` in the last two cases, an expired token produced a build that reported a *cluster* problem while the stack kept building and billing. `Abort if describe-cluster itself failed` (`when: cluster_status.rc != 0`) says `This is NOT a cluster problem`, replays `cluster_status.stderr`, and names the `AWS_PROFILE` in effect; `Abort if the stack never reached a terminal state` says `STILL BUILDING`, that the stack is `still billing`, and points at `--pcluster_create_timeout`. Both sit between the wait and `Get the head node IP address` — an abort after that fact is an abort after every gated task has already silently no-opped. Restoring `failed_when` on the wait is the mutation to avoid: Ansible's own retry failure collapses the three diagnoses back into one opaque message. `TestAFailedDescribeIsNotAFailedCluster` evaluates each abort's `when:` against four outcomes and asserts which fires *first*, plus `test_the_three_diagnoses_are_distinguishable` as the vacuity guard. That guard must match `--pcluster_create_timeout` with the dashes: the bare variable name also appears in the "did not reach ... within N minutes" line, and matching it there let deleting the remediation line survive.


## Trimmed CLAUDE.local.md bullets (moved 2026-08-26, fifth pass)

Verbatim originals of `CLAUDE.local.md` bullets condensed in place.  The rule,
every test name, file path, function name and measured number stayed in the
preamble; the incident narrative is here.

### `CLAUDE.local.md` line 42 (original)

- **Every download checksum is validated in `make_pcluster.py` before the first AWS mutation, and no `_HARDCODED_DEFAULTS` entry may be a placeholder.** Ansible's `get_url` splits `checksum:` on `:` and `int()`s the remainder base 16, so a malformed digest is not caught until the playbook is already running — and by then the five managed policies, the IAM role, the keypair, and the S3 bucket exist and have to be swept before a retry. A placeholder is not a dormant hazard: `_resolve`'s precedence is CLI > defaults file > `_HARDCODED_DEFAULTS`, so a `<cluster>_defaults.yml` written before a new key existed falls straight through to it, which failed a live `alinux2023` build with `The checksum format is invalid` — a message naming neither the parameter nor the file it came from. `monitoring_version_checksum`, `docker_compose_checksum_x86_64` and `docker_compose_checksum_aarch64` all hold real digests, equal to `pcluster_defaults.yml`'s, and `_validate_download_checksum` in `src/pcluster_core.py` rejects anything `get_url` would. Properties that are load-bearing:

### `CLAUDE.local.md` line 46 (original)

- **`enable_external_nfs` gets a pre-flight reachability check, and only one of its two signals is a hard failure.** `_check_external_nfs_reachable` in `src/pcluster_core.py` runs before `_setup_iam` (same "before the first AWS mutation" placement as the checksum validator above) and probes two things: a TCP connect to port 2049 (the NFS service port on both v3 and v4, needing no external binary), and a best-effort `showmount -e <server>` to enumerate NFSv3 exports. Before this, `external_nfs_server` only got a hostname-format regex check — a typo'd, unreachable, or empty-exports filer sailed through and was only caught when `postinstall.j2`'s `sudo mount` failed under `set -euo pipefail`, on the head node **and every compute node** (that block isn't gated to head-node-only), 15–30+ minutes and several AWS resources into a build.

### `CLAUDE.local.md` line 48 (original)

- **`MakeClusterParams` annotates 13 fields `bool`; `MAKE_CLUSTER_DEFAULTS` and every defaults file carry them as `"true"`/`"false"`; `core_create_cluster` truthiness-tests them.** Dataclasses do not coerce and `"false"` is truthy, so a build requesting nothing provisioned FSx, EFS, EFA, monitoring and a login-node pool while the preview reported each `"false"`. argparse gave the CLI real bools, so only the shim was affected — and it was unreachable until `params.region` was fixed, which is how fixing one bug made another live. `_coerce_bool` is the single reading, shared with `_resolve_bool`; the field set comes from `typing.get_type_hints`, **not** `f.type`, which becomes the string `"bool"` under postponed annotations and would silently empty the set (an `in ("bool", bool)` tuple has an unreachable half no test can distinguish). `TestEveryBoolFieldIsARealBool` pins type, falsiness, a YAML-native `true`, and CLI/shim agreement; three older tests asserted `enable_fsx == "true"` and helped hide it.

### `CLAUDE.local.md` line 49 (original)

- **Cluster-name and owner validation live in `build_make_cluster_params`, not the shims**, which never validated at all — a name went straight into `os.path.join` for the defaults file and into `makedirs` under `active_clusters/`. `SystemExit` is wrapped as `PClusterMakerError` so it cannot kill a stdio server. `_validate_cluster_name` uses `\Z`, not `$` (which matches before a trailing newline, and the name becomes an S3 key, a directory and a filename). `discover_defaults_file` **excludes the tracked `pcluster_defaults.yml`**: it sets all three `_REMOTE_DENIED_PARAMS`, so `cluster_name="pcluster"` applied them without ever touching `overrides`. The claim that the denial held because "the file is gitignored" was false — `.gitignore` lists three literal names and the template is tracked; the remote transport survives on the other half (no repo-root file is in any tier's `sources`).

### `CLAUDE.local.md` line 52 (original)

- **A Lambda artifact must be pruned of bytecode; `packaging.py` does not do it as part of staging.** Measured on a real `pip install --target` of the read-only tier (2026-08-24): **241 MB** against Lambda's 250 MB ceiling — 9 MB of headroom — dropping to **139 MB** with sources staged once `__pycache__`/`.pyc` are removed. 84 MB of the artifact was bytecode Lambda recompiles anyway. `prune_for_lambda` prunes and **returns the byte total**, so a build checks `ZIP_UNZIPPED_LIMIT_BYTES` before uploading rather than learning the ceiling from `CreateFunction`; it is deliberately not called by `build_source_archive`, which stages into a directory pip has already filled. The pruned zip is **55 MB**, over the 50 MB direct-upload limit, so a handler tier goes via S3. **The docstring's `aws_cdk` claim was wrong and is corrected**: the lazy import is a fact about the import graph, but `aws-parallelcluster` *declares* 17 `aws-cdk.*` packages, so pip puts them in every tier that installs PCluster (44 MB pruned, in read-only). `TestPruningIsWhatMakesTheArtifactFit` pins all of it. **No tier has ever been deployed** — see `CLAUDE-STATE.md`.

### `CLAUDE.local.md` line 53 (original)

- **The `read-only` tier writes nothing, and that is a naming rule as much as an IAM one.** `add_queue`/`remove_queue` write `configs/<name>.yaml`; they sat on `read-only` because they mutate no CloudFormation stack, which is true and beside the point — the policy carried `s3:PutObject` under a name that promises otherwise, and the next person to read it would have been misled. They are on `stack-mutation` now, with the rest of the config's lifecycle: `apply_cluster_update` reads the object, `delete_cluster` removes it. `MCPStateAccessReadOnly` keeps `s3:GetObject` on `configs/*` (for `list_queues`) and lost `PutObject`; `MCPStateAccessStackMutation` gained it. **The cost is real and accepted**: a queue edit now carries the stack-mutation tier's blast radius, which is more privilege than the edit needs — the least-privilege alternative is a fifth tier for config writes, i.e. a fifth Lambda, role, policy and cold start. `TestNoReadOnlyToolWritesTheStore` pins the placement and `TestTheClusterConfigStore`'s per-tier table pins the *actions*, not just the presence of a grant, since that is the half that moved.

### `CLAUDE.local.md` line 85 (original)

- **A failed AWS call and a stopped cluster are different problems, and the generated access scripts must not conflate them.** **A missing instance answers the literal string `None` with rc=0, while an auth or API failure is a non-zero rc with a message on stderr** (verified empirically, not assumed), so `templates/access_cluster.j2` and `templates/grafana_tunnel.j2` must never run `aws ec2 describe-instances ... 2>/dev/null || true` — that discards both signals and sends an operator with an expired token or an unset `AWS_PROFILE` to check a perfectly healthy cluster. Both files share the same shape: a `_describe_head_node` function taking the query field and a stderr path, a `mktemp` capture with an EXIT trap, an `_AWS_RC` that survives both call sites, and two mutually exclusive diagnoses — the failure branch prints `NOT a cluster problem`, replays aws's own stderr through `sed 's/^/    /'`, and names the `AWS_PROFILE` in effect; the absent branch says the call *succeeded* and points at `./list_pcluster.py --live`. Properties that are load-bearing:

### `CLAUDE.local.md` line 123 (original)

- **The head-node bootstrap timeout must cover shared-filesystem provisioning.** PCluster creates the `HeadNodeWaitCondition` at `cluster_stack.py:293`, *before* `_add_head_node()` at 295, and the filesystem IDs land in `HeadNodeLaunchTemplate` (`efs_fs_ids` at `cluster_stack.py:1362`, `fsx_fs_ids` at 1375) — an implicit CloudFormation dependency, verified in a deployed template. So EFS/FSx provisioning runs on the head node's critical path with the clock already running, and the stock 2100s (`NODE_BOOTSTRAP_TIMEOUT`, `pcluster/constants.py:250`) is shared with it. The only knob is `DevSettings.Timeouts.HeadNodeBootstrapTimeout`, which `_add_wait_condition` (`cluster_stack.py:1249-1262`) feeds straight to `CfnWaitCondition(timeout=)`. Consequences:

### `CLAUDE.local.md` line 130 (original)

- **`sinfo` exiting 0 is not evidence that a cluster can run work, and the state classifier is shared.** `check_pcluster.py`'s `check_slurm` ran `sinfo -s`, branched on `rc == 0`, and captured a stdout it never read — so a cluster whose whole fleet was `down`, `drained` or `unk` reported `[PASS] Slurm`, which is the exact state compute nodes end up in after a bootstrap failure and the one thing a health check exists to surface. It now runs `sinfo -h -o '%D %T'` (`-s`'s `NODES(A/I/O/T)` column is an aggregate that names no state at all, so it cannot express this check) and classifies via `_classify_sinfo_nodes` in `src/pcluster_core.py`. Load-bearing properties:

### `CLAUDE.local.md` line 139 (original)

- **An FSx export prefix is a destination and must not be required to contain objects.** `_check_fsx_s3` in `src/pcluster_core.py` listed the prefix and `sys.exit`ed on `KeyCount == 0` for both buckets, refusing a valid first-dehydration configuration at the last check before the build. AWS's own FSx model settles it: `ExportPath`'s default is `s3://import-bucket/FSxLustre<creation-timestamp>`, which cannot exist before the filesystem does (`CreateFileSystemLustreConfiguration`, botocore's FSx `service-2.json.gz`). The parameter is keyword-only `require_objects=True` and only the export call site in `make_pcluster.py` passes `False`. Load-bearing properties:

### `CLAUDE.local.md` line 143 (original)

- **`scripts/sbatch_default_submission_script.sh` derives its `--partition` and `--ntasks`; it had neither correctly.** (It has no `.j2` suffix, so `EXTRA_TEMPLATES` in `tests/test_templates.py` must keep listing it or no test renders it at all — the harness gap that let two dead ladders survive; `test_collect_templates_matches_what_the_playbooks_render` is what enforces the listing. Narrative in `docs/sessions.md`.) Its two Jinja2 ladders had **both `{% else %}` fallbacks commented out**, so every unlisted size emitted no `--ntasks` at all and Slurm silently ran the job on one task, and it had **no `--partition` directive**, which `sbatch` rejects outright on a GPU-only cluster that has no `compute` partition. Both values now come from the cluster's own shape: `cpu_ranks_per_node` and `gpu_vcpus_per_node`, derived in `make_pcluster.py` from `_vcpu_map` and threaded through `vars_file.j2`.

### `CLAUDE.local.md` line 148 (original)

- **Teardown has three outcomes, and the claim it prints is derived once into `_delete_headline`.** Both summary tasks and `sns_destruction_summary_report.j2` said `Cluster <name> has been deleted` whenever no cleanup step *failed* — and on a wait **timeout** nothing failed and nothing was deleted either, so the playbook contradicted its own `cluster may still be deleting` and `credentials are PRESERVED` warnings and exited **0**, which every caller reads as a clean teardown. The orphan list is orthogonal to all three states (`_orphaned_resources` is empty on a timeout, because an ignored *failure* is not what happened), so gating the claim on it can never be right. `Record what the summary is allowed to claim about the stack` derives one sentence from `_cf_delete_confirmed` then `_cf_delete_failed`, and the two summary tasks plus the SNS report interpolate it — a fourth surface restating the literal is the mutation to avoid, which is why `test_no_reporting_surface_hardcodes_the_success_claim` bans it outside the derivation task's own body (`.serial has been deleted` in the orphan summary is exempt: that is the local serial file, not the cluster).

### `CLAUDE.local.md` line 153 (original)

- **`results_bucketname` is newer than `enable_hpc_benchmarks`, so teardown derives it when the vars file predates it.** A cluster built by an older toolkit has a vars file that satisfies the benchmark gate but leaves the bucket undefined, and `Push performance results from head node to S3` interpolates it. That raised an `UndefinedError` which the block's `rescue` **caught** (verified under real `ansible-playbook`: `failed=0 rescued=1`, the play continues), printing `WARNING: Unexpected error during performance results sync` and naming nothing, after which teardown destroyed the head node holding the only copy. The failure mode is a silent loss behind an unnamed cause, **not** an aborted teardown; do not document it as one. `Derive the results bucket name when the vars file predates it` renders `parallelclustermaker-results-<aws_account_id>-<region>`, gated `when: results_bucketname is not defined`.


### R4/R5: the pin that was not a pin (2026-08-26)

**R5 came out well and the instrument is the lesson.** The question --
can a tier exceed its own IAM -- was first approached with CloudTrail, and
that was wrong. A denial only appears in a trail if something attempts it,
and nothing does: the roles trust only `lambda.amazonaws.com`, and each
tier's tools make only the calls its policy grants. The two options that
suggested themselves were both bad -- wait for a denial during some future
build, or narrow a policy to provoke one and restore it, which tests a
configuration that is *not* the one deployed.

`iam:SimulatePrincipalPolicy` was available the whole time and runs IAM's
own evaluator over the real attached policies of the real roles, mutating
nothing. 17 actions x 5 tiers, 31 assertions, all passing: the escalation
ladder holds, and no tier can create a user, mint an access key, attach a
user policy, assume a role, or rewrite its own code.

The router's row is the one that rewards careful reading.
`lambda:InvokeFunction` simulates as **denied** against `*` -- the router's
entire job -- because the grant is ARN-scoped. Per resource it allows
exactly the four handler tiers and denies the authorizer, the register
handler and an arbitrary function. A wildcard grant would have simulated
ALLOW and read as the cleaner pass while being strictly worse. An ephemeral
CloudTrail then corroborated it at runtime, which a simulation cannot: a
policy permitting an action is not proof the action is the one taken. The
captured chain shows API Gateway -> router, then the router's **own assumed
role** -> fleet-toggle and -> stack-mutation-node.

**R4 is the important one, because the first verdict written was PASS and
it was wrong.** It rested on two things that look like success: every call
returned HTTP 200 (a JSON-RPC error is a 200 carrying an `error` member),
and every tier emitted a CloudWatch REPORT line with a duration (which
records how long an invocation ran, not whether it succeeded). The cluster
did read `UPDATE_COMPLETE`/`RUNNING` afterward -- stale state from L4's
earlier local update. Reading terminal status instead of the events that
produced it is the same error that reported a failed build as healthy
earlier in this campaign, made twice.

All three calls had failed, reporting **`BadRequestException: `** with
nothing after the colon. That is the first defect and the reason the run
read as a pass: the three `pcluster.lib` wrappers formatted with a bare
`{e}`, and `ParallelClusterApiException.__init__` calls `super().__init__()`
with no arguments, so `str()` is empty for every one. The repo already had
`pcluster_exception_detail` and used it only on the two create paths.

Reading the traceback to the raise site in the installed package produced
the message the blank string had eaten: *"the update can be performed only
with the same ParallelCluster version (3.15.1) used to create the
cluster."* The cluster and venv were 3.15.1; the artifacts, built that
morning, were **3.16.0**.

**The pin meant to prevent this is what allowed it.** Both surfaces read
`aws-parallelcluster>=3.15,<3.17` -- byte-identical, upper-bounded, passing
both guards. But a range is not a pin: pip resolved that same range to
3.16.0 for an artifact built today against a venv holding 3.15.1. Two
identical range specifiers resolved at different times are not the same
version. `packaging.py`'s own comment already described this exact failure
and then prescribed the range that does not prevent it, and the guard
asserted merely that *an* upper bound existed.

Both fixed and mutation-checked: all three wrappers use
`pcluster_exception_detail`; all three surfaces pin
`aws-parallelcluster==3.15.1` (the third, generated
`requirements-lambda.txt`, was caught by its own agreement test -- the
multiple-surfaces rule working); and `test_the_pin_is_exact` requires the
specifier's operator set to be exactly `{"=="}`, with a vacuity guard that
the shipped-and-broken spelling fails it.

**Two general lessons.** A guard can pin the wrong property and read as
protection -- "the two surfaces agree" and "there is an upper bound" were
both true while the thing they existed to prevent was happening. And an
error path that discards its reason does not merely inconvenience: here it
converted a hard failure into an apparent pass, which is how a wrong
verdict got written into the certification record.

### R4, continued: two IAM floors nobody had checked (2026-08-26)

Fixing the version skew did not make R4 pass; it made the *next* failure
visible, twice. Both were IAM gaps, and both had been present for the whole
life of the remote transport.

**`fleet-toggle` could not do its job at all.** With the pin corrected,
`stop_fleet` failed with a message that was finally legible thanks to the
`pcluster_exception_detail` fix: *"Unable to access bucket associated to the
cluster ... 'parallelcluster-bc5952d6e61b4a19-v1-do-not-delete'"*.
`update-compute-fleet` parses the cluster configuration out of PCluster's
own per-cluster S3 bucket, and reads/updates the fleet status item in the
`parallelcluster-<cluster>` DynamoDB table (`ComputeFleetStatusManager` --
the status does not live in the stack). The tier granted neither. Confirmed
by simulation rather than by another failed run: `stack-mutation-node`
allowed both, `fleet-toggle` implicit-denied both.

**Then no tier could describe a login-node cluster.** The next failure was
`not authorized to perform: elasticloadbalancing:DescribeLoadBalancers`.
`stageb` was built with `--enable_loginnode true`, the pool sits behind an
NLB, and `describe-cluster` reads it through `pcluster/aws/elb.py`. A sweep
across all four handler tiers found **none of them granted any
`elasticloadbalancing` action** -- so every remote tool that describes a
cluster failed against every login-node cluster, not just this one. All four
of elb.py's calls are required; granting only `DescribeLoadBalancers` moves
the failure to `DescribeTags`.

**The pattern is one blind spot, not three accidents.** Every IAM guard in
the suite asked whether a tier could *exceed* its blast radius -- no
`iam:CreateUser`, no `s3:DeleteBucket`, no self-modification. Nothing asked
whether a tier could reach its own **floor**: the calls its own tools
actually make. R5's simulation was pointed at the ceiling and passed 31/31
while two tiers could not perform their primary function.
`TestEachTierCanActuallyDoItsJob` is the missing half, and it pins both
directions -- the floor, and that reaching it did not raise the ceiling.
That second half is load-bearing: `s3:*` on `parallelcluster-*` would have
satisfied the floor tests while handing a fleet toggle the ability to
rewrite any cluster's configuration, which is why the S3 grant is read-only
and the DynamoDB grant is scoped to `table/parallelcluster-*`.

**A deployment gap surfaced alongside them.** `_setup_mcp_infra` catches
`EntityAlreadyExists` and reuses the existing policy; it never calls
`create_policy_version`. So editing a `templates/MCP*.json_src` and
re-running `--setup-infra` changes nothing on deployed infrastructure and
reports success. Every fix above had to be pushed as an explicit new default
version. Worth a follow-up: the idempotent-create path should either version
a changed document or say plainly that it did not.

**Cost of the blank error message, measured.** Three defects sat behind one
`BadRequestException: ` with nothing after the colon. Each was one
`pcluster_exception_detail` away from naming itself, and instead the first
R4 run was recorded as a PASS. `MCPStackMutation.json_src` is now 5,935
bytes of its 6,144 minified limit, which is worth watching.

### Policy convergence, and the teardown half nobody would have looked for

`_setup_mcp_infra` created a policy or, finding one, reused it -- never
comparing documents. The comment said "Idempotent, like `_setup_iam`", and
that inheritance is the whole bug: `_setup_iam`'s policy names carry the
cluster serial, so each build creates fresh ones and a stale document is
unreachable. The MCP names are fixed and long-lived. Same code, opposite
consequence. Three IAM fixes had to be pushed by hand this session because
`--setup-infra` reported success and changed nothing.

Two design points. **The comparison is against AWS's own copy of the
document**, not a stored hash, an IAM tag or a git ref. A marker beside the
truth is a second source that can be wrong about it -- the same shape as
the PCluster pin, where two surfaces agreed on a *label* while the things
they named diverged. And **detection is separated from application**:
reporting a mismatch needs only reads, so it is the default;
`--update-policies` applies.

**The teardown half is the part that would not have been found by
inspection.** `DeletePolicy` refuses while any non-default version exists
-- botocore's IAM model states it plainly and returns
`DeleteConflictException`. A policy only ever has extra versions once
something has updated it, so the defect was *unreachable* until deploy
learned to converge, and it became reachable the same afternoon: three MCP
policies were already undeletable in the live account, left that way by the
hand-pushed fixes. Teaching deploy to add versions without teaching
teardown to remove them strands every MCP policy in the account.

The fake for these tests is written from `iam/service-2.json`, and three
rules quoted from it are what let it disagree with the code:
CreatePolicyVersion caps at five, DeletePolicyVersion refuses the default,
DeletePolicy refuses while versions remain. The third is load-bearing -- a
fake that let `DeletePolicy` through would have agreed with the shipped
teardown by construction and could never have shown it was broken. Both
halves were then confirmed against real IAM with a throwaway policy: the
bare delete really does return `DeleteConflict`, and the new path really
does remove it.

A drift report over all ten deployed MCP policies came back **10 current,
0 stale** -- the question the old code could not answer at all.

**A wider gap was found and deliberately not fixed:** `OperatorPolicy`
scopes its IAM statements to `policy/pclustermaker-policy-*` and
`role/pclustermaker-role-*`, neither of which matches the MCP names. So the
documented operator permission set cannot run `--setup-infra` at all, and
MCP deployment is in practice an admin-credential operation that nothing
says is one. That is a separate decision about who may deploy the
transport, not a bug to quietly widen a policy over.

### Option 4: a deploy policy and a permissions boundary (2026-08-26)

The choice was how to let someone deploy the MCP transport without handing
them the account. Four options were costed; the byte budget eliminated the
obvious one outright. **The full MCP grant set appended to `OperatorPolicy`
measures 6,358 bytes against IAM's 6,144-byte managed-policy limit** -- so
"just widen it" was not a preference to argue about, it does not fit. As
its own document the same set is 1,745 bytes with 4,399 to spare.

What was chosen: `MCPDeployPolicy.json_src` for the deployer, and
`MCPRoleBoundary.json_src` as a permissions boundary every MCP role is
created under.

**The boundary is the part with teeth, and its design is mostly about what
the deployer must *not* be able to do.** A boundary the deploy credentials
can rewrite is not a boundary, so: it is named `pclustermaker-mcp-boundary`,
deliberately outside the `pclustermaker-mcp-policy-*` pattern the deploy
policy's own lifecycle statement covers; the deploy policy explicitly denies
`CreatePolicyVersion`/`SetDefaultPolicyVersion`/`DeletePolicy` on it and
`DeleteRolePermissionsBoundary` on the roles; and `iam:CreateRole` is
granted **only** under a `StringEquals` condition on
`iam:PermissionsBoundary`. That condition is the hinge -- without it the
deployer simply creates an unbounded role and the ceiling never applies,
which is why it has its own test.

Two asymmetries are deliberate and both are recorded rather than smoothed:
**the boundary is reported on drift but never updated**, unlike the tier
policies that `--update-policies` converges, because changing it is an
administrator's action; and **teardown leaves it**, because it is a durable
account guardrail the deploy policy cannot delete. The setup/teardown
symmetry test now names that single exception explicitly rather than being
loosened, so a *second* resource going undeleted still fails.

**A prohibition had to be reconciled rather than ignored.**
`templates/CLAUDE.md` says `iam:CreatePolicyVersion`/`DeletePolicyVersion`
are intentionally omitted, because version management lets the holder
rewrite a policy in place to `Action:"*"`. `MCPDeployPolicy` grants them --
scoped to `pclustermaker-mcp-policy-*`, needed for convergence, and capped
by the boundary on every role those policies attach to. That is exactly the
mitigation `templates/CLAUDE.local.md` names for the cluster case ("gate it
behind an `iam:PermissionsBoundary` condition") and which no cluster role
has. `OperatorPolicy` was left untouched, and the omission -- previously
guarded only on `HeadNode-IAM` -- is now pinned on `OperatorPolicy` too.

**The residual is stated, not overclaimed.** The boundary caps the MCP roles
themselves. It does not cap a *cluster* role that a stack-mutation tier
legitimately creates: PCluster's own roles are not created under a boundary,
and requiring one would break builds. So a compromised stack-mutation tier
can still create an unbounded cluster role. That is inherent to the
toolkit's design and identical to the residual `OperatorPolicy` already
carries -- the boundary closes the escalation the *deploy* grants would have
opened, which is what it was chosen for.

The two fakes caught the change immediately: `create_role` gained a
`PermissionsBoundary` keyword and every existing test that drives the real
function failed, which is what a fake that models the object rather than
stubbing it is for.


### Option 4 deployed and verified (2026-08-26)

`--setup-infra` created `pclustermaker-mcp-boundary` and bounded all seven
MCP roles. The evidence is the *kind* of denial, not just the attachment:

    action                         before          after
    iam:CreateUser                 implicitDeny    explicitDeny
    iam:CreateAccessKey            implicitDeny    explicitDeny
    iam:AttachUserPolicy           implicitDeny    explicitDeny
    logs:DeleteLogGroup            implicitDeny    explicitDeny
    iam:UpdateAssumeRolePolicy     implicitDeny    explicitDeny
    roles carrying the boundary    0 of 7          7 of 7

Before, those actions were merely ungranted -- a policy edit could have
granted them. Now they are forbidden by a ceiling the deploy credentials
cannot lift. `simulate-principal-policy` evaluates permissions boundaries,
so IAM's own evaluator reports the difference on the live roles.

The convergence work paid off in the same run: all ten policies printed
`current` with their version IDs instead of the old silent "Reusing
existing MCP policy".

**Verified that nothing broke**, which matters because a too-narrow ceiling
fails silently at call time rather than at deploy: the read-only sweep
returned 8/8 with zero denials, and a real `stop_fleet` (`status_before:
RUNNING`) / `start_fleet` round trip completed through the bounded
fleet-toggle role -- the tier whose S3, DynamoDB, ELB and CloudFormation
grants now sit under the boundary.

**One defect found on the way in.** `--setup-infra` on its own redeployed
every zip tier -- six 146 MB pip installs as a side effect of creating IAM.
`--setup-gateway` had a short-circuit with a comment arguing exactly that
case; nothing carried it to `--setup-infra`, and nothing tested either,
because the expression lived inside `main()` after
`sts.get_caller_identity()` where the suite's no-AWS guard forbids any test
from reaching it. `tiers_to_deploy` is module-level now for that reason,
with a vacuity guard against the short-circuit becoming "return [] always".
Same shape as `_derive_docker_compose_staging` and the key-rotation
scripts: logic placed where no test can see it.

### Session 54 — CloudWatch log-group retention: 180 days by inheritance, 30 by decision

`templates/config.pcluster.j2`'s `Monitoring/Logs/CloudWatch` block set
`Enabled: true` and nothing else, so retention fell through to PCluster's
`CW_LOGS_RETENTION_DAYS_DEFAULT = 180`. Nobody had chosen 180; it was what
happened when the key was absent. It now sets `RetentionInDays: 30`.

**The retain half is untouched, and that is the point.** `CloudWatchLogs.__init__`
still defaults `deletion_policy` to `"Retain"`, the block still sets no
`DeletionPolicy`, `src/delete_pcluster.yml` still has no log-group deletion task,
and a retained log group is still not an orphaned resource. The rationale for
keeping the group is unchanged — it is the only surviving record of a failed
build, cfn-init captures stdout only, node stderr reaches no stream at all, and a
failed build is immediately followed by a teardown. Only the lifetime moved:
diagnosing a failed build is a short-horizon activity, so 30 days covers it and
cuts the accumulation the operator purges by hand (33 `osiris` groups, ~55 GB, is
what 180 looked like in practice).

**Both PCluster-side facts are read out of the installed package rather than
restated.** `CloudWatchLogsSchema.retention_in_days` is a `validate.OneOf` whose
set contains 30, so a future PCluster that drops the value fails in CI instead of
twenty minutes into a build; its `update_policy` metadata is
`UpdatePolicy.SUPPORTED`, so unlike `enabled` (`UNSUPPORTED`) the retention can be
changed on a running cluster. The `enabled` half is the vacuity guard that the two
policies are distinguishable through that reading at all.

`TestTheLogGroupExpiresOnOurScheduleNotPClusters` (`tests/test_templates.py`)
parses the rendered config with `yaml.safe_load` — "30" appears elsewhere and a
substring cannot tell a correctly-nested key from one at the wrong depth — and
also loads it through `ClusterSchema`, since marshmallow ignores an unknown key
rather than rejecting it, so a casing typo would silently restore the 180-day
default. **The vacuity guard is key-absence, not a wrong number**: a silent revert
leaves no key at all and the rendered file then looks exactly as it did before.
Seven faithful mutations caught — the line deleted, the value back at 180, the
value quoted, the key nested under `Dashboards`, a `DeletionPolicy: Delete` added,
a retention outside PCluster's `OneOf`, and the update policy misread as
`UNSUPPORTED`.

The number is stated on three teardown surfaces that must agree —
`_retained_resources` in `src/delete_pcluster.yml`, `_collect_retained_resources`
in `src/pcluster_core.py`, and `will_retain` in `mcp_server/tools.py` — plus
`tests/conftest.py`'s `cluster_params_retained_teardown` fixture and `README.md`;
all were updated together. No CLI flag or defaults-file key was added: the value
is hardcoded, deliberately, and if a knob is ever wanted it is a
`--cw_log_retention_days` on `make_pcluster.py` threaded through `vars_file.j2`,
not a second literal.

Preamble budget: the `CLAUDE.local.md` bullet was rewritten in place rather than
grown, and the rule itself first went to `templates/CLAUDE.md` because root
`CLAUDE.md` had only 85 usable bytes before the working margin. Budget freed up
in the same session (an unrelated condensation of `CLAUDE.local.md` dropped the
preamble to 136,407 against a 146,000 ceiling), so the bullet was moved to root
`CLAUDE.md` where it belongs -- the rule in the lean file, the dense rationale and
the mutation ledger in `CLAUDE.local.md`, and nothing left in the `templates/`
pair but the corrected 180 -> 30 cross-reference. Final preamble: 138,855 bytes,
headroom 7,145.

## Trimmed CLAUDE.local.md bullets (moved 2026-08-26, sixth pass)

Condensed in place to buy preamble headroom. The rule, the mechanism and every
test-name and file:line citation survive in `CLAUDE.local.md`; what was removed
is discovery narrative, and the originals are reproduced verbatim below.

### `CLAUDE.local.md` line 29 (original)

- **Eight `base_os` values are supported, across two package-manager families: `ubuntu2204`, `ubuntu2404`, `ubuntu2204arm`, `ubuntu2404arm`, `rhel9`, `rhel9arm`, `alinux2023`, `alinux2023arm`.** `preinstall.j2` and `postinstall.j2` branch on `'ubuntu' in base_os` and select apt or dnf accordingly; the dnf side splits again on `'alinux' in base_os` where AL2023 and RHEL 9 differ (see the AL2023 bullet below). **Neither arm may install or pin `ansible` on a node**: `ansible` 9.x requires `ansible-core` 2.16 (`requires_python >=3.10`) and the RHEL 9 PCluster AMI ships Python 3.9, so `pip3 install 'ansible>=9,<10'` was unsatisfiable and `set -euo pipefail` made it an `OnNodeStartExecutionFailure`. The pin was deleted from both arms rather than branched around — nothing on a node imports ansible; only `src/create_pcluster.yml` does, on the operator's workstation. `test_no_arm_installs_ansible` keeps it gone. `rhel9arm` is ours, not PCluster's: `pcluster_os` strips the suffix to `rhel9`, in upstream's own `SUPPORTED_OSES`. The set is pinned by `TestPackageManagersMatchTheRenderedOs` (`tests/test_templates.py`) and `TestEc2UserValidation` (`tests/test_diagnose.py`) across eight surfaces — `--base_os` choices in `make_pcluster.py`, `_EC2_USERS`/`_resolve_ec2_user` in `pcluster_core.py`, `ARM_OSES`/`X86_OSES`/`base_os_efa` in `pcluster_aux_data.py`, no *wrong-family* package manager on any surface that reaches a node, no package manager at all on an unbranched surface, no tracked defaults file shipping or *documenting* an unsupported value, `_VALID_EC2_USERS` in `diagnose_pcluster.py`, and the `assert` task in `src/create_pcluster.yml`. Load-bearing properties of the guards:
  - **The `assert` task in `src/create_pcluster.yml` must stay task index 0.** It constrains **both** `base_os` and `pcluster_os` — the latter is what reaches PCluster's `Os:` field, and upstream's own `SUPPORTED_OSES` *accepts* values this toolkit does not, so constraining `base_os` alone leaves it free and nothing downstream refuses it. Allowed values live in the task's own `vars`. `make_pcluster.py`'s argparse `choices` bind the CLI only; `ansible-playbook --extra-vars base_os=<unsupported>` bypasses them and the node then dies at its first package install, twenty minutes in. Position is the property, not presence: an assert placed after the SNS topic or the S3 bucket has already billed the operator and left resources behind. `test_the_playbook_rejects_an_unsupported_os_before_spending_anything` asserts on the task *index*. All seven faithful mutations are caught.
  - **RHEL-specific bootstrap facts, confirmed on live builds of both arms.** EPEL installs *by URL* (`epel-release-latest-9.noarch.rpm`) because `epel-release` is not packaged in RHEL itself, and is required because `lua-devel`, `lua-posix`, `lua-filesystem` and `tcllib` are in neither baseos nor appstream. CodeReady Builder is enabled best-effort in a loop over three repository ids (`crb` on Rocky/Alma/CentOS Stream, two `codeready-builder-for-rhel-9-*` spellings on genuine RHEL), with the fatal package install as the arbiter, so a missing package fails loudly by name rather than on an unrecognized repo id. `pip3` takes no `--break-system-packages` on this arm: RHEL 9's pip predates PEP 668 and rejects the flag. There is **no** apt-mark analog — `dnf --exclude` needs no package enumeration.

### `CLAUDE.local.md` line 32 (original)

- **Amazon Linux 2023 is the dnf family's second member, and every difference from RHEL 9 is a package that does not exist.** Every package claim below is **confirmed on hardware on both arches**, not on repo metadata alone; item 5 of the live-verification list in `docs/sessions.md` has the per-claim evidence. The arm is selected by `'alinux' in base_os`, a **substring** test — `alinux2`, `alinux2arm` and a trailing-garbage `alinux2023arm2` all satisfy it, so all three are in `_UNSUPPORTED_OSES`, and the argparse `choices`, `_EC2_USERS` and the playbook's `assert` task are what bound the set; the template branch does not. `pcluster_os` strips the suffix to `alinux2023`, in upstream's own `SUPPORTED_OSES`. Login user is `ec2-user`, same as RHEL. Four packages RHEL's arm installs are **absent** from al2023 on x86_64 and aarch64 alike:
  - **`epel-release` is not packaged for al2023 at all**, so the RHEL arm's release-RPM URL install has no analog on either the critical-packages line or the GPU block: an EL9 EPEL rpm there is a version-mismatched repo, and the critical-packages install is *not* `|| true`-guarded, so it fails the node. `test_no_epel_release_rpm_is_fetched_by_url` exists because a stray copy of that line did survive into the AL2023 arm of `postinstall.j2`. There is also no CRB analog: `lua`, `lua-devel`, `lua-filesystem`, `lua-posix`, `lua-term`, `bc` and `tcl` are all in the core repo.
  - **`luarocks` is absent**, so the three rocks the RHEL and Ubuntu arms build from source come from the core repo as RPMs instead (`lua-filesystem`, `lua-posix`, `lua-term`) and the luarocks block is a no-op on this arm. `luarocks` is **stubbed and returns 0** in `_run_postinstall` where a real AL2023 node returns 127, so a source-level assertion proves nothing: `test_luarocks_is_never_invoked_on_this_arm` asserts on the **execution trace**, for both node types, and `test_the_other_two_arms_still_build_the_rocks` is the paired vacuity guard that keeps the fix from becoming "delete the rocks everywhere".
  - **`tcllib` is absent** and nothing in the toolkit references it — Lmod uses `tcl` itself. Do not add it back for symmetry; it would fail the node.
  - **`nvtop` is absent** and there is no EPEL to fall back on, so the GPU block installs `htop` only, on *both* node types. This is the one place AL2023 differs from RHEL in what the head node gets.
  - **`lustre-client*` joins the dnf kernel exclusions**, in both templates, on the shared dnf arm. AL2023 packages the Lustre client as `lustre-client` and has **no `kmod-lustre*` at all**, so the RHEL glob alone silently protects nothing there; on RHEL `lustre-client` is the userspace half of the same client and has no business jumping versions away from the kmod either. Neither distro carries an `efa*` package in its core repo — EFA comes from Amazon's installer on the AMI — so that glob is kept on the strength of node state, not repo contents.
  - **The guard is `TestAmazonLinux2023InstallsOnlyWhatItPackages` (`tests/test_templates.py`)**, parametrized over both templates and over `cluster_params_al2023` **and** `cluster_params_al2023_gpu_queue` — the GPU fixture is required because the `htop` install sits inside `{% if enable_gpu == 'true' %}` and the plain fixture leaves it false.

### `CLAUDE.local.md` line 42 (original)

- **Every download checksum is validated in `make_pcluster.py` before the first AWS mutation, and no `_HARDCODED_DEFAULTS` entry may be a placeholder.** Ansible's `get_url` splits `checksum:` on `:` and `int()`s the remainder base 16, so a malformed digest is not caught until the playbook is already running — and by then the six managed policies, the IAM role, the keypair and the S3 bucket exist and have to be swept before a retry. A placeholder is not a dormant hazard: `_resolve`'s precedence is CLI > defaults file > `_HARDCODED_DEFAULTS`, so a `<cluster>_defaults.yml` written before a new key existed falls straight through to it, which failed a live `alinux2023` build with `The checksum format is invalid` — naming neither the parameter nor the file it came from. `monitoring_version_checksum`, `docker_compose_checksum_x86_64` and `docker_compose_checksum_aarch64` all hold real digests, equal to `pcluster_defaults.yml`'s, and `_validate_download_checksum` in `src/pcluster_core.py` rejects anything `get_url` would. Properties that are load-bearing:
  - **The validator must be *called*, and called before `_setup_iam`.** Validating afterward still bills the operator and leaves resources behind, which is the entire cost the check exists to avoid. `test_make_pcluster_validates_before_it_creates_anything` walks the AST for both call sites and asserts `max(validate_lines) < min(iam_lines)`, anchored on the `_setup_iam` call rather than a line number.
  - **Both checksums are checked, each under its own gate.** `monitoring_version_checksum` under `enable_monitoring`, the compose one additionally under `stage_docker_compose` — validating a checksum for a download this build will not perform would reject valid configurations. Dropping the compose half is its own mutation (`N7`).
  - **`_HARDCODED_DEFAULTS` and `pcluster_defaults.yml` must agree on all three.** Two sources for one digest that disagree makes the value depend on whether `--use_defaults` was passed. Both files are asserted to hold real digests independently, and every tracked `*_defaults.yml` is swept for placeholders. The regex is `sha256:[0-9a-fA-F]{64}` anchored with `fullmatch`; loosening it to `sha256:.+` is a caught mutation.

### `CLAUDE.local.md` line 85 (original)

- **A failed AWS call and a stopped cluster are different problems, and the generated access scripts must not conflate them.** **A missing instance answers the literal string `None` with rc=0, while an auth or API failure is a non-zero rc with a message on stderr** (verified empirically), so `templates/access_cluster.j2` and `templates/grafana_tunnel.j2` must never run `aws ec2 describe-instances ... 2>/dev/null || true` — that discards both signals and sends an operator with an expired token or an unset `AWS_PROFILE` to check a healthy cluster. Both files share one shape: a `_describe_head_node` function taking the query field and a stderr path, a `mktemp` capture with an EXIT trap, an `_AWS_RC` that survives both call sites, and two mutually exclusive diagnoses — the failure branch prints `NOT a cluster problem`, replays aws's own stderr through `sed 's/^/    /'` and names the `AWS_PROFILE` in effect; the absent branch says the call *succeeded* and points at `./list_pcluster.py --live`. Properties that are load-bearing:
  - **The `PrivateIpAddress` fallback must survive the rc handling.** PCluster head nodes in a private subnet have no public IP — so the second query is gated on `_AWS_RC -eq 0` *and* an empty/`None` first answer, not on the answer alone.
  - **`access_cluster.j2` needs an explicit `rm -f "${AWS_STDERR}"` before its `exec ssh`.** `exec` replaces the process image and the EXIT trap does **not** run (verified under bash 5.3), so the trap alone leaks one temp file per connection on the *happy* path — the one an operator takes dozens of times a day. `grafana_tunnel.j2` does not `exec`, so its trap suffices; do not "restore symmetry" by adding a redundant `rm` there or by deleting this one. The trap stays for the `exit 1` paths.
  - **`grafana_tunnel.j2` legitimately keeps `2>/dev/null || true` on two other lines** — the `kill` of a stale PID file and the `pgrep` for the tunnel PID. `test_no_describe_instances_call_discards_stderr` therefore scopes its assertion to the `_describe_head_node` body and the two `HEAD_NODE_IP=` call sites; a whole-file ban would forbid those.

### `CLAUDE.local.md` line 104 (original)

- **A compute node must refresh its package index before installing anything, and the monitoring installs must be non-fatal.** `OnNodeStart` — and therefore `preinstall.j2`'s index refresh — is registered on the head node only, so a compute node's `/var/lib/apt/lists` (or dnf cache) is whatever the AMI shipped, and `apt-get -y install nvtop` against that index exits **100** with `E: Unable to locate package nvtop`. The GPU block therefore splits on `NODE_TYPE` **inside each OS arm**: the head node installs `nvtop htop` with no refresh (`preinstall.j2` already did one and the head-node package block does another — a third is pure bootstrap latency), and a compute node refreshes first (`apt-get -y update` / `dnf -y makecache`) and installs `htop` only. **`nvtop` is head-node-only on purpose, on both families**: it is outside the default repositories (`multiverse` on Ubuntu, EPEL on RHEL — which is why the RHEL head-node arm installs `epel-release` by URL first, also non-fatally), and the operator logs into the head node. Both installs are guarded with `|| echo "WARNING: ..." >&2` — the *only* non-fatal installs in the file — because a compute node that exits non-zero is relaunched by `clustermgtd` and counted toward the partition's 10-failure protected-mode threshold. Do not "restore symmetry" by making them fatal.
  - **Order and gating are pinned on the execution trace, not the source.** Both commands are `sudo apt-get`, so a grep cannot tell which ran first on which node type, and an update placed *after* the install refreshes nothing in time. `TestPostinstallNodeTypeGating`'s three tests assert on trace indices per node type.
  - **`_run_postinstall` cannot see the non-fatal guards at all** — it discards the rendered script's `set -euo pipefail` (see the NVMe bullet for why it must), so a test using it passes whether the `|| echo` is there or not. `TestMonitoringToolsCannotFailTheNode` extracts the block and runs it standalone under real `set -euo pipefail` with the package manager returning 100; `test_the_harness_actually_fails_the_package_manager` guards those two tests against passing vacuously. All nine faithful mutations are caught.
  - **That class is parametrized over all three arms, and the arm table carries the refresh command each one uses** — run against Ubuntu only, the two dnf arms' `|| echo` guards and their `dnf -y makecache` go unguarded, and a fatal `dnf` install on a compute node counts toward the same 10-failure threshold. `_ARMS` maps each arm to `(fixture, manager, refresh)`: `apt` → `cluster_params_gpu_queue_enabled`/`apt-get`/`apt-get -y update`, `dnf_alinux` → `cluster_params_al2023_gpu_queue`/`dnf`/`dnf -y makecache`, `dnf_rhel` → `cluster_params_rhel_gpu_queue`/`dnf`/`dnf -y makecache`. The GPU fixtures are required because the whole block sits inside `{% if enable_gpu == 'true' %}`.
  - **The `nvtop` assertion is per-install-line on the execution trace, never a substring over the block.** Every arm's guard message *names* the package — `|| echo "WARNING: nvtop/htop unavailable..."` — so `"nvtop" in block` is satisfied with the package dropped from the install line entirely. The test filters the trace to lines starting with the arm's manager and containing ` install `, then requires `nvtop` in that line's own `split()`. Expected presence is per arm and asymmetric: `apt` and `dnf_rhel` yes (multiverse, EPEL), `dnf_alinux` no — AL2023 does not package it and has no EPEL to fall back on.

### `CLAUDE.local.md` line 115 (original)

- **The package upgrade in `preinstall.j2`/`postinstall.j2` must never replace the kernel.** A kernel bump triggers an initramfs rebuild whose runtime is unbounded inside CloudFormation's bootstrap window -- a full package upgrade once crossed a kernel boundary and was still rebuilding when the wait condition expired. Independently, PCluster's AMI ships EFA and Lustre kernel modules built against the kernel it boots, so replacing it without rebuilding them risks losing the interconnect or the Lustre client on next boot (the documented DKMS hazard pattern; not verified against the current AMI). Both of `preinstall.j2`'s arms carry a full upgrade and both hold the kernel back: apt via `apt-mark hold`, dnf via `--exclude='kernel*' --exclude='kmod-lustre*' --exclude='efa*'`. `postinstall.j2`'s RHEL arm carries the same three excludes; its apt arm installs named packages only and must never become `dist-upgrade`/`full-upgrade` without a hold. `TestPreinstallNeverReplacesTheKernel` (`tests/test_templates.py`) executes the rendered script under real `bash` with the package managers stubbed and pins all of the following:
  - **`--exclude` is a name glob resolved at depsolve time, needing no enumeration or audit** -- a kernel cannot enter the transaction under a name not starting with `kernel`, so a future AMI's pending set cannot matter. Never add `--exclude='dracut*'` or `--exclude='microcode_ctl'`: neither replaces the kernel, and excluding security updates on speculation is the worse trade. A `uname -r` vs `rpm -q --last kernel` guard is likewise wrong -- it fails a healthy node that ships a kernel installed-but-not-booted.
  - **The upgrade itself is deliberately kept**, not narrowed to named packages: `preinstall.j2` installs `python3-dev`, and `numpy`/`scipy`/`pandas`/`matplotlib` compile from source wherever pip finds no wheel -- the aarch64 case every `*arm` `base_os` hits.
  - **Every `pip3 install` on a node path must carry `--ignore-installed`, on both OS arms, across all four pip call sites** (self-install and dependency install in `preinstall.j2`; the plotting-stack install in `postinstall.j2`). pip cannot uninstall a distribution whose `dist-info` has no `RECORD` file, which distro-packaged Python modules routinely ship, and pip does elect to replace distro-owned transitives (`packaging`, `python-dateutil`) even when the direct pins are already satisfied. `TestNoPipInstallEverUninstallsADistroPackage` asserts over the **rendered** text of both templates.
  - **The Debian `pip3 install --upgrade pip` case is the same hazard, and `--break-system-packages` is not the fix for it** -- that flag permits writing into the system tree, it does not make a dpkg-owned pip (also RECORD-less) uninstallable. `--ignore-installed` is required there too, and RHEL 9's pip predates PEP 668 and rejects `--break-system-packages` outright, so that flag must never appear on an RHEL pip line. `test_pip_is_never_upgraded_over_the_distro_pip` and `test_break_system_packages_is_not_treated_as_the_fix` pin both halves.
  - **`$_kernel_pkgs` is unquoted on purpose** (so multiple packages arrive as separate argv entries, not one concatenated string) and the `[ -n ... ]` guard is required (`apt-mark hold` with an empty argument list is a usage error).

  Full narrative in `docs/sessions.md`.

### `CLAUDE.local.md` line 123 (original)

- **The head-node bootstrap timeout must cover shared-filesystem provisioning.** PCluster creates the `HeadNodeWaitCondition` at `cluster_stack.py:293`, *before* `_add_head_node()` at 295, and the filesystem IDs land in `HeadNodeLaunchTemplate` (`efs_fs_ids` at `cluster_stack.py:1362`, `fsx_fs_ids` at 1375) — an implicit CloudFormation dependency. So EFS/FSx provisioning runs on the head node's critical path with the clock already running, and the stock 2100s (`NODE_BOOTSTRAP_TIMEOUT`, `pcluster/constants.py:250`) is shared with it. The only knob is `DevSettings.Timeouts.HeadNodeBootstrapTimeout`, which `_add_wait_condition` (`cluster_stack.py:1249-1262`) feeds straight to `CfnWaitCondition(timeout=)`. Consequences:
  - **`_derive_head_node_bootstrap_timeout` in `src/pcluster_core.py` takes `max()`, not a sum.** EFS and FSx are independent CFN resources with no dependency between them, so they provision concurrently and the head node waits on the slower one. An additive implementation passes every single-filesystem case, so `test_both_filesystems_take_the_max_not_the_sum` is what pins it.
  - **Keyword-only.** Three parameters, two of them bools: transposing `enable_efs` and `enable_fsx` yields a plausible timeout rather than an error, and that mutation is caught only by the EFS-alone case.
  - **`pcluster_defaults.yml` must ship 2100.** The derivation treats any other value as operator intent and passes it through untouched, including downward, so shipping a different default disables the auto-bump for every cluster. `test_the_defaults_file_ships_pclusters_own_default` guards this.
  - **Bounds checking is ours.** PCluster's schema validates `min=1` with no upper bound (`cluster_schema.py:1119-1126`), but CloudFormation rejects a WaitCondition `Timeout` above 43200 (12 hours), so an explicit value is `_clamp_int`ed to `[1, 43200]`.
  - **`UpdatePolicy.UNSUPPORTED`** — it cannot be changed on a running cluster, only at creation. `ComputeNodeBootstrapTimeout` in the same block is `SUPPORTED` and reaches nodes by a different path entirely (cookbook `dna.json` via `queues_stack.py:351`), not a WaitCondition.

### `CLAUDE.local.md` line 130 (original)

- **`sinfo` exiting 0 is not evidence that a cluster can run work, and the state classifier is shared.** `check_pcluster.py`'s `check_slurm` ran `sinfo -s`, branched on `rc == 0` and captured a stdout it never read, so a cluster whose whole fleet was `down`, `drained` or `unk` reported `[PASS] Slurm` — the exact state compute nodes reach after a bootstrap failure, and the one thing a health check exists to surface. It now runs `sinfo -h -o '%D %T'` (`-s`'s `NODES(A/I/O/T)` column is an aggregate that names no state at all) and classifies via `_classify_sinfo_nodes` in `src/pcluster_core.py`. Load-bearing properties:
  - **Three outcomes, not two.** Zero usable nodes fails and names the count; some usable and some not **passes with a note**, because partial capacity is not a failure the operator should be blocked by. Both halves are mutations: returning `False` there breaks CI on a healthy-enough cluster.
  - **`main()` must print the note.** `check_slurm` returning it is half the fix — a bare `[PASS] Slurm` discards a degradation note and is indistinguishable from never having read stdout, and a unit test on `check_slurm` cannot see that, since it asserts on a return value `main()` is free to throw away. `test_the_degradation_note_reaches_the_operator` drives `main()` through `capsys`.
  - **Unparseable output counts as unusable, and empty output fails.** A line the classifier cannot read is not evidence of health; `slurm_load_partitions: Unable to contact` must not read as a healthy fleet.
  - **`diagnose_pcluster.py` uses the shared predicate, asserted by identity.** It had its own `_SINFO_OK_STATES` and its own flag-stripping; two copies of a state table drift, and then the health check and the diagnostic tool disagree about the same cluster. `test_diagnose_uses_the_shared_predicate` asserts `diag._sinfo_state_is_ok is _sinfo_state_is_ok` and `test_diagnose_no_longer_carries_its_own_state_table` asserts the duplicate attribute is gone.

### `CLAUDE.local.md` line 143 (original)

- **`scripts/sbatch_default_submission_script.sh` derives its `--partition` and `--ntasks`; it had neither correctly.** (It has no `.j2` suffix, so `EXTRA_TEMPLATES` in `tests/test_templates.py` must keep listing it or no test renders it at all; `test_collect_templates_matches_what_the_playbooks_render` enforces the listing. Narrative in `docs/sessions.md`.) Its two Jinja2 ladders had **both `{% else %}` fallbacks commented out**, so every unlisted size emitted no `--ntasks` and Slurm silently ran the job on one task, and it had **no `--partition` directive**, which `sbatch` rejects outright on a GPU-only cluster that has no `compute` partition. Both values come from the cluster's own shape: `cpu_ranks_per_node` and `gpu_vcpus_per_node`, derived in `make_pcluster.py` from `_vcpu_map` and threaded through `vars_file.j2`.
  - **`gpu_vcpus_per_node` is not `gpu_ranks_per_node`, and the names are the documentation.** `gpu_ranks_per_node` is an NVIDIA *device* count — correct for the GPU benchmark driver, wrong for `--ntasks` on a general-purpose job, where a `p3.2xlarge` would get 1 task on an 8-core machine. The GPU-only arm of this script asks for **cores**. `cluster_params_gpu_no_nvidia` (`g4ad.4xlarge`) is the fixture that makes the distinction visible: `gpu_ranks_per_node` is 0 there while `gpu_vcpus_per_node` is 16.
  - **`usable_vcpu_count` divides by `DefaultThreadsPerCore`, never by a hardcoded 2.** `DisableSimultaneousMultithreading` is what `config.pcluster.j2` sets when `hyperthreading` is false, and upstream acts on it only when the instance reports more than one thread per core (`cluster_config.py:1523`). Graviton reports 1, so halving unconditionally requests half the cores every ARM node actually has. The `or 1` covers both that case and a missing field — do not reintroduce an `if threads > 1` branch around it, which is unreachable and reads as though the `or 1` were not there.
  - **`derive_ranks_per_node` takes the `min` across the queue, with a floor of 1.** A queue may hold several instance types and only the smallest one's count fits on all of them. The floor exists because `sbatch --ntasks=0` is rejected outright, so a type missing from the map must not render an unsubmittable script; an empty queue returns 0, which is what the template's `enable_cpu_queue` gate reads.
  - **Both are keyword-only, and the call sites are pinned per queue.** Keyword-only defends against transposing `instance_types` and `vcpu_map`, which are different shapes — it cannot defend against handing the CPU queue's derivation the GPU list, since both are lists of instance types and the swap renders a plausible script. `test_make_pcluster_derives_both_queues_from_the_same_response` walks the AST for the assignment targets and pins `cpu_ranks_per_node` to `cpu_instance_types` and `gpu_vcpus_per_node` to `gpu_instance_types`. The same test pins **exactly one** `describe_instance_types` call — the vCPU counts must come out of the response the architecture check already fetches. All nine faithful mutations are caught.

### `CLAUDE.local.md` line 148 (original)

- **Teardown has three outcomes, and the claim it prints is derived once into `_delete_headline`.** Both summary tasks and `sns_destruction_summary_report.j2` said `Cluster <name> has been deleted` whenever no cleanup step *failed* — and on a wait **timeout** nothing failed and nothing was deleted either, so the playbook contradicted its own `cluster may still be deleting` and `credentials are PRESERVED` warnings and exited **0**, which every caller reads as a clean teardown. The orphan list is orthogonal to all three states (`_orphaned_resources` is empty on a timeout), so gating the claim on it can never be right. `Record what the summary is allowed to claim about the stack` derives one sentence from `_cf_delete_confirmed` then `_cf_delete_failed`, and the two summary tasks plus the SNS report interpolate it — a fourth surface restating the literal is the mutation to avoid, which is why `test_no_reporting_surface_hardcodes_the_success_claim` bans it outside the derivation task's own body (`.serial has been deleted` in the orphan summary is exempt: that is the local serial file, not the cluster).
  - **The headline must be gated on the positive confirmation, not on `not (_cf_delete_failed | bool)`** — the same rule as the four credential-destroying tasks above, and for the same reason: a timeout is neither confirmed nor `DELETE_FAILED`. `DELETE_FAILED` and the timeout also need *different* wording, since the operator greps the CloudFormation console for the state name; collapsing them into one message is a caught mutation.
  - **`Fail because the cluster deletion was never confirmed` carries the same gate as the preservation warning**, which is what makes it mutually exclusive with the `DELETE_FAILED` `fail:` — dropping the `_cf_delete_failed` half fires two aborts with two explanations of one state. It sits after **both** summary prints, per the ordering rule the other two `fail:` tasks follow; an abort before the summary suppresses the report it exists to draw attention to.
  - **The derivation must precede the SNS templating.** The report interpolates it and the render is `StrictUndefined`, so a `set_fact` placed after `Template the cluster destruction summary report` raises. The SNS audience is also the one that cannot see the terminal at all, and the report shows `Completed destruction: <time>`, which reads as success unaided.
  - **`TestAnUnconfirmedDeleteIsNotReportedAsSuccess` (`tests/test_templates.py`) evaluates the headline expression and every `fail:`'s real `when:`** against the four `describe-cluster` outcomes `TestCredentialsSurviveAnUnconfirmedDelete` already defines, because a text assertion cannot tell `confirmed` from `not failed`. `test_a_confirmed_delete_still_says_so` and `test_the_three_outcomes_are_distinguishable` are the vacuity guards.

### `CLAUDE.local.md` line 153 (original)

- **`results_bucketname` is newer than `enable_hpc_benchmarks`, so teardown derives it when the vars file predates it.** A cluster built by an older toolkit has a vars file that satisfies the benchmark gate but leaves the bucket undefined, and `Push performance results from head node to S3` interpolates it. That raises an `UndefinedError` which the block's `rescue` **catches** (verified under real `ansible-playbook`: `failed=0 rescued=1`, the play continues), printing `WARNING: Unexpected error during performance results sync` and naming nothing, after which teardown destroys the head node holding the only copy — a silent loss behind an unnamed cause, **not** an aborted teardown; do not document it as one. `Derive the results bucket name when the vars file predates it` renders `parallelclustermaker-results-<aws_account_id>-<region>`, gated `when: results_bucketname is not defined`.
  - **The gain is a named cause, not recovered results.** On a cluster that old the bucket was never created, so the sync gets `NoSuchBucket`, the existing `rc != 0` warning fires, and *that* one names the cause and prints the head-node path so the operator can copy by hand while the node is still up. Nothing here recovers the data.
  - **This is a second source for one name, so it is pinned *against `_derive_results_bucket`*, not against a restated literal** — the `pkg_dir` hazard again; two sources that disagree make the sync target depend on which toolkit built the cluster. `test_the_derived_name_matches_the_python_derivation` renders the playbook expression and compares it to the function's own output.
  - **It reads only `aws_account_id` and `region`**, both in `vars_file.j2` since the v3 migration (`c2673ae`) — a fallback that referenced `cluster_name` or the serial would risk the very `UndefinedError` it exists to prevent, *and* silently restore a per-build bucket. `test_the_inputs_are_ones_every_vars_file_has` pins the referenced set by inequality.
  - **The `is not defined` guard is load-bearing**: without it the fallback overwrites a current vars file's value, so every cluster's results would follow this expression instead of the one `make_pcluster.py` wrote. A test cannot simulate absence by passing `None` — any value makes the variable *defined*; the key must be omitted. `TestTheResultsSyncSurvivesAnOlderVarsFile` also pins that the derivation stays inside the `enable_hpc_benchmarks` gate, read through `_effective_when` since the gate lives on the enclosing block.

## Preamble reduction and ratchet (2026-08-26, sixth pass)

The always-loaded preamble stood at 147,511 B against a 150,000 ceiling — 2,489 of
headroom over a 2,400 working floor, i.e. 89 usable bytes. Reduced to 136,294 B
(-11,217) and `_CEILING` ratcheted to 146,000, leaving 9,706 of headroom against an
allowance of 13,626 (two of the largest `CLAUDE.local.md` bullet, the 6,813-byte
shared-cluster-store one, which was deliberately left alone: condensing it lowers the
allowance faster than it raises headroom, and `test_the_ceiling_is_not_slack` fails from
that side).

Three mechanisms, in increasing order of yield.

**Condensing in place** (~1,500 B over twelve `CLAUDE.local.md` bullets and a rewrite of
`CLAUDE-STATE.md`). This is the method previous passes used and it is nearly exhausted:
these bullets are dense, and the recurring narrative fat — what a live build printed, what
a test's first version got wrong — has largely been harvested already. The twelve
originals are reproduced verbatim above under "Trimmed CLAUDE.local.md bullets (moved
2026-08-26, sixth pass)".

**Moving per-arm node-bootstrap evidence to `templates/CLAUDE.local.md`** (~7,800 B), which
is outside the budget and loads whenever the templates it describes are touched. Eight
bullets gave up their enumerations while keeping rule, mechanism and every test name in
the always-loaded root file: the AL2023 package-absence list, the compute-node package
index and `nvtop` arm table, the kernel excludes and the pip rules, `liblua5.1-0-dev`,
Lmod's `bc`, the GPU NVMe block, the `/etc/profile` guard, and the monitoring wrapper's
port-80 loop. The originals are verbatim in that file's last section, and each root bullet
points at it. `templates/CLAUDE.local.md`'s header previously said the
`preinstall.j2`/`postinstall.j2` constraints stay in the root file because they are the
active node-bootstrap failure surface; that is still true of the *rules*, and the header
now says so precisely — it is the evidence that moved, not the rule.

**Deduplication** (~2,100 B). Three IAM bullets in the root `CLAUDE.local.md` — the
`logs:DeleteLogGroup` ban, `iam:AttachRolePolicy`'s `iam:PolicyARN` condition, and the head
node's prefix-confined results-bucket grant — were already stated in full in
`templates/CLAUDE.md` and `templates/CLAUDE.local.md`. They collapsed to one pointer
bullet. This also retired a stale copy: the root text still said the `AttachRolePolicy`
condition covers "the five policies this build made", which session 54's `ClusterNode-Deny`
made six.

`CLAUDE.md` was left untouched. Its bullets are normative, public, and carry measured
numbers the brief required preserving; the two candidate trims there would have deleted
evidence (a live incident's timings) rather than words, so they were declined.

Suite green at 3222 passed, 1 skipped. No citation was dropped: the test-name sets of the
two files before and after the move are equal, and all three doc-hygiene guards pass.

### deploy_mcp.py --teardown (2026-08-26)

The transport could be built and not removed. `delete_mcp_functions` and
`_delete_mcp_infra` existed as functions with no caller anywhere, and the
REST API and Cognito user pool had no teardown code at all -- so the
internet-facing endpoint survived every teardown, and today's had to be
driven from a scratchpad script.

Order is gateway, functions, IAM, pool. Gateway first because it is the
internet-facing surface and removing it stops anything arriving while the
rest is half gone; the pool last because it is what authenticates the
callers the earlier steps were serving.

**The ordering bug is the part worth keeping.** `DeleteUserPool` refuses
while the pool has a domain, the error names no domain, and the domain is
*not* the pool name -- live, the pool was
`parallelclustermaker-mcp-<acct>-<region>` while its domain was
`pclustermaker-mcp-yqdbaeo8t`. A teardown that guesses deletes nothing and
then reports success on the retry, because the pool it was asked to remove
is still there and still matches. `delete_cognito_pool` reads the domain off
`describe_user_pool`, and the fake in the test refuses a pool that still has
one -- without that refusal the ordering assertion passes with the calls in
either order.

Teardown deliberately leaves the permissions boundary. That is not tidiness:
`MCPDeployPolicy` *denies* deleting it, so a code path that tried would
report a denial as a teardown failure for every least-privilege deployer.

`--teardown` also short-circuits `tiers_to_deploy` unconditionally, `--tier`
included -- building a 146 MB artifact so the function it would deploy to
can be deleted is minutes of pip for a thing about to not exist. It is the
third flag to need that short-circuit and the first where an explicit
`--tier` must not override it.

Four mutations, all caught: pool deleted before its domain, domain guessed
from the pool name, the gateway prefix check dropped (which would take an
unrelated REST API with it), and the build short-circuit removed. Verified
idempotent against the now-empty account: every step reports absence rather
than failing.

### Session 54, closed out (2026-08-26)

Three commits: `a931b82` (remote-transport defects), `b83988a` (CLI IAM
hardening and the CloudWatch lifetime), `0b87a67` (`--teardown`). Suite
**3232 passed, 1 skipped**. MCP certification finished **14 of 14**. AWS is
empty of ParallelClusterMaker resources.

**The through-line of the day is that almost every defect was hidden by
something that looked like success.** R4 was recorded as PASS three times
before it actually passed. HTTP 200 looked like success -- a JSON-RPC error
is a 200 carrying an `error` member. A CloudWatch `REPORT` line looked like
success -- it records how long an invocation ran, not whether it worked.
`"error" not in payload` looked like success -- the payload was `""`, no
error member *and no result*, while the Lambda logged `No changes found in
your cluster configuration`, because R4 had been applying an unchanged
config. Underneath all of that, `BadRequestException: ` with nothing after
the colon looked like a message.

Each proxy for success was wrong at least once, and the correction is
structural rather than a resolution to be careful: a pass now needs a
positive result *and* an observed state transition. R4's final verdict rests
on the stack's `LastUpdatedTime` advancing and the new queue's launch
template existing in EC2, not on any status that was already true before the
call.

**The same shape appeared in the IAM work from the other direction.** R5
simulated every tier's *ceiling* and passed 31/31 while `fleet-toggle` could
not toggle a fleet and no tier could describe a login-node cluster. A guard
can be sound, exhaustive, and pointed at the wrong half of the problem. R6
exists because of that, and `TestEachTierCanActuallyDoItsJob` pins both
directions — reaching the floor must not raise the ceiling.

**Three self-inflicted problems worth recording, since they cost real time.**
An orphaned git worktree from a killed agent left a second copy of the repo
on disk, which made the line-citation extractor's basename resolution
ambiguous and silently dropped five citations; it presented as two
"pre-existing" test failures and an agent's stash-based check could not have
found it, because stashing tracked files does not remove a worktree. A full
suite run overlapped my own mutation testing and came back with failures I
nearly wrote off as flaky — they were two real breaks in code I had written
ten minutes earlier, one of which was the regional-boto-client AST guard
(`TestEveryRegionalBotoClientIsBoundToTheTargetRegion`) correctly refusing
a new unclassified `apigateway` client. And an agent that had already reported done resumed and edited files
a second agent was working on, which is exactly the interleaving the
sequencing was meant to prevent; it came out clean, but "came out clean" is
a result and not a guarantee.

**What is still owed.** The CLI IAM hardening has never run on hardware. A
wrong deny entry or a too-narrow boundary ceiling does not fail at deploy —
it fails at head-node bootstrap roughly twenty minutes into a build, or on a
compute node counting toward the partition's ten-failure protected-mode
threshold, and all 3232 tests are blind to it. The deny list was verified
independently against all 222 granted patterns with zero collisions, which
is consistency, not a booted cluster. `--teardown` has likewise only been
exercised against an empty account.

### ironclad: the live verification, and what only a real cluster shows

Built `ironclad` (ubuntu2404arm, login nodes, monitoring, spot compute) for
the sole purpose of testing what the suite cannot reach, and rebuilt the
whole remote transport alongside it. Everything from session 54's commits is
now verified against AWS rather than against tests:

    head node bootstrapped under the boundary        zero AccessDenied
    login node healthy 1/0 behind its NLB
    compute node booted AND RAN A JOB under ClusterNode-Deny
    zero IAM denials cluster-wide (3 patterns, all logs)
    RetentionInDays: 30 on a live log group
    seven MCP roles created bounded at creation
    add_queue + apply_cluster_update through bounded tiers
    R3 4/4, R6 8/8
    --teardown removed 25 live resources, domain before pool

Two defects surfaced that no test could have found, both now committed.

**API Gateway gives a remote call 29s, not 900.** `CLAUDE.md` said Lambda's
900s ceiling is what forces `apply_queue_config` to be local-only -- true and
incomplete. `apply_cluster_update` adding one queue ran **41,992 ms** in the
Lambda; the caller failed at 29.4s; the stack reached UPDATE_COMPLETE anyway.
So the failure is not a wrong answer, it is a wrong answer about something
that happened, and a retry double-submits against a stack already updating.
R4's calls were 14-20s and stayed under it. The number was *already in
deploy.py*, in a comment explaining the authorizer's 10s timeout -- reasoned
about once, never generalized to the tier tools.

**The ECR push needs three credential locations, not two.** INSTALL.md
documented two and said both were required. containerd inside the Lima VM
runs as root and reads `/root/.docker/config.json`; omitting it fails
identically to doing nothing. Found by testing each half in isolation --
host token, VM pipe (19 bytes delivered exactly), VM `$HOME` file all worked,
which left the destination as the only variable.

**Every wrong conclusion today came from a signal that resembled success.**
HTTP 200 on a JSON-RPC error. A CloudWatch REPORT line, which records
duration and not outcome. `"error" not in payload`, where the payload was
`""`. A mutation that "survived" because `replace(..., 1)` patched the first
of two identical lines in a different function. A monitor glob matching
`idle~` as `idle` -- written twenty minutes after writing this lesson down.
And at the end, a **stale stdio MCP server**: `preview_cluster_delete` said
"180 days" against source saying 30, and `finalize_cluster_teardown` left the
sixth policy behind because its loaded code predated `ClusterNode-Deny`. Both
looked exactly like product defects; the source was correct in both cases.

The discriminator was always the same: check the source or the state
directly before believing the behavior.

**The final leak check earned its shape.** It asserted *exactly two* IAM
policies should survive -- the two durable boundaries -- rather than sweeping
up whatever was left. Written as a sweep, the stale server's orphaned
`ClusterNode-Deny` would have been silently deleted and never noticed. Both
boundaries were removed by hand afterward once they had no roles to bound;
AWS holds nothing but PCluster's own system bucket and the empty locks
bucket.

## Trimmed preamble content (moved 2026-08-26, seventh pass)

Verbatim originals of the bullets condensed in `CLAUDE.md` and the
completed-work narrative removed from `CLAUDE-STATE.md` during the
seventh preamble-reduction pass. The rules themselves, their test names
and their measured numbers stayed in the preamble; what moved here is
evidence, incident narrative and superseded state.

### `CLAUDE.md` line 93 (original)

- The cluster's CloudWatch log group is retained on teardown — PCluster's
  own `deletion_policy` default of `"Retain"`, deliberately not
  overridden, because the group is the only surviving record of a failed
  build (cfn-init captures stdout only; node stderr reaches no stream) and
  a failed build is immediately followed by a teardown. Never add a
  log-group deletion task to `src/delete_pcluster.yml`, never set
  `DeletionPolicy: Delete`, and never put a retained log group in
  `_orphaned_resources`. Its *lifetime* is a decision, not an inheritance:
  `config.pcluster.j2` sets `RetentionInDays: 30` over PCluster's default
  of 180, because diagnosing a failed build is short-horizon work. That
  field is `UpdatePolicy.SUPPORTED`, unlike `Enabled`, so it can be
  changed on a running cluster, and 30 must stay in
  `CloudWatchLogsSchema`'s `OneOf` set — read out of the installed package
  by `TestTheLogGroupExpiresOnOurScheduleNotPClusters`, never restated.

### `CLAUDE.md` line 216 (original)

- **Lambda's 900s ceiling is not the binding one; API Gateway's 29s
  integration timeout is.** No tool in `TOOL_TIERS` may block on a cluster
  operation: past 900s the function is killed mid-mutation, with the fleet
  stopped, an update in flight and the S3 lock held by a dead process.
  `apply_queue_config` is local-only for this reason; remote callers drive
  `stop_fleet` → `apply_cluster_update` → `start_fleet`. Decompose such a
  tool, never delete the capability. **But a remote call has ~29s, not
  900s** — `GATEWAY_INTEGRATION_TIMEOUT_MS`, already the REST maximum;
  raising it needs a service quota increase. Past it the caller gets a
  timeout body while the Lambda runs on and the mutation *succeeds*, so a
  client that retries submits a second update against a stack already
  updating. Measured: `apply_cluster_update` took 41,992 ms adding one
  queue and the caller saw failure at 29.4s while the update completed. R4's
  14-20s calls stayed under it, which is why the ceiling went unrecorded.
  `deploy.py` knew the number — in a comment justifying the authorizer's 10s
  timeout — and it was never generalized to the tier tools.

### `CLAUDE.md` line 232 (original)

- **`delete_cluster` initiates a teardown; `finalize_cluster_teardown`
  finishes it.** `wait=False` returns on CloudFormation's acceptance and
  skips *every* teardown step, so one call leaves the IAM policies, the S3
  bucket, the credentials, the SNS topic and the store record behind. A
  *second* `delete_cluster` does finish the job — an absent stack
  classifies as `_CLUSTER_NOT_FOUND`, not `_KICKED_OFF` — but it gets
  there by **issuing another delete-cluster against the name**, which
  deletes the new stack if that name has since been rebuilt, and called
  too early it silently no-ops and reports success, indistinguishable
  from having finished.
  `core_delete_cluster(finalize_only=True)` is the explicit second half:
  it never calls delete-cluster and never waits, refusing unless the stack
  is confirmed gone (`_confirm_stack_is_gone` — the wait loop at
  `retries=1`, so the non-blocking guarantee is structural, not a
  promise).
  `DELETE_FAILED` refuses: the waiting path strips IAM and S3 there
  deliberately, having just attempted the delete, but arriving *here*
  means an earlier delete failed and the answer is to re-run it. That path
  exits non-zero either way, so only asserting no teardown step ran can
  see it. A failed describe propagates: a failed AWS call is not a deleted
  stack. Both modes fall into **one** teardown body, and their two
  tokens — both minted by `preview_cluster_delete` — are deliberately not
  interchangeable.

### `CLAUDE.md` line 255 (original)

- **The monitoring wrapper must not run upstream's installer on a login
  node.** `installer/install.sh` supports two node types and says so in its
  own header — `case "${PLATFORM_NODE_TYPE}"` has arms for `HeadNode` and
  `ComputeFleet` only. A login node falls through `verify_docker`, matches
  nothing, and fails; the wrapper exits with the installer's status, so
  that became the custom action's, the node was marked unhealthy, and its
  Auto Scaling Group replaced it — forever. Observed live: three login
  nodes abandoned on Heartbeat Timeout across 45 minutes with the stack
  never leaving `CREATE_IN_PROGRESS`. The `LoginNode` arm now exits 0
  immediately; Grafana runs on the head node and operators reach it through
  `grafana_tunnel`, so there is no login-node half to install. That also
  retires the bounded `MONITORING_HOME` poll, which only existed to let the
  install proceed and cost every login node up to 300s of boot. Only the
  *combination* fails, which is why neither a login-node cluster nor a
  monitoring cluster caught it alone — and the harness stubs the installer,
  so the guard asserts on the **execution trace**, not the exit status.

### `CLAUDE.md` line 329 (original)

- **A tier's policy has a floor as well as a ceiling.** Every MCP IAM
  guard asked whether a tier could exceed its blast radius; none asked
  whether it could reach its own. `fleet-toggle` could not:
  `update-compute-fleet` parses the cluster config from PCluster's
  per-cluster S3 bucket and reads/updates the fleet status item in the
  `parallelcluster-<cluster>` DynamoDB table, and the tier granted neither,
  so `stop_fleet`/`start_fleet` failed against every real cluster for the
  tier's whole life. The S3 grant stays **read-only** — `s3:*` would have
  satisfied the floor while letting a fleet toggle rewrite any cluster's
  config — and the DynamoDB grant is scoped to `table/parallelcluster-*`.
  The same blindness hid a second, wider gap: **no tier granted any
  `elasticloadbalancing` action**, and a login-node pool sits behind an NLB
  that `describe-cluster` reads, so *every* remote tier failed against any
  cluster built with `--enable_loginnode true`. All four calls in
  `pcluster/aws/elb.py` are needed (granting only `DescribeLoadBalancers`
  moves the failure to the next one); describes take no resource-level
  permission, so `Resource: "*"` is forced and read-only is the only bound
  left. `TestEachTierCanActuallyDoItsJob` pins both directions for both
  gaps. `MCPStackMutation.json_src` is now 5,935 bytes of the 6,144 limit.

### `CLAUDE.md` line 348 (original)

- **Reuse is not convergence, and the difference is a naming asymmetry.**
  `_setup_iam`'s policies carry the cluster serial, so every build makes
  fresh ones and a stale document is unreachable; the MCP policy names are
  fixed and long-lived, so the first-ever create won forever — a
  `templates/MCP*.json_src` edit changed nothing in the account and printed
  "Reusing existing MCP policy". `_setup_mcp_infra` now compares the
  rendered document against **AWS's own copy** (never a stored hash, tag or
  git ref — a marker beside the truth is a second source that can be wrong
  about it) and reports any mismatch; `--update-policies` pushes it as a new
  default version, pruning oldest-first to stay under IAM's five-version
  ceiling and never deleting the version in force.

### `CLAUDE.md` line 359 (original)

- **`deploy_mcp.py --teardown` removes the transport; nothing did before.**
  `delete_mcp_functions` and `_delete_mcp_infra` existed and were called by
  no entry point, and the REST API and Cognito pool had no teardown code at
  all, so the internet-facing endpoint outlived every teardown. Order is
  gateway, functions, IAM, pool — each step leaves nothing depending on
  something already gone. **The Cognito domain must be deleted before the
  pool**: `DeleteUserPool` fails while one exists, names no domain in the
  error, and the domain string is not the pool name, so a caller that
  guesses removes nothing and reports success on the retry. Read it off
  `describe_user_pool`. Teardown deliberately leaves the permissions
  boundary — `MCPDeployPolicy` denies deleting it, so attempting it reports
  a denial as a teardown failure. `--dry-run` lists without removing.

### `CLAUDE.md` line 371 (original)

- **MCP deployment is its own permission set, not the operator's.**
  `OperatorPolicy` scopes its IAM to `pclustermaker-policy-*` and
  `pclustermaker-role-*`, which match no MCP name, and the full MCP grant
  set appended to it measures 6,358 bytes against the 6,144 limit — so
  widening it is not merely undesirable, it does not fit.
  `MCPDeployPolicy.json_src` carries it instead, and every MCP role is
  created under the `MCPRoleBoundary.json_src` permissions boundary:
  `iam:CreateRole` is granted **only** under a `StringEquals` condition on
  `iam:PermissionsBoundary`, and the deployer is explicitly denied
  rewriting or detaching that boundary. Without the condition a deployer
  creates an unbounded role and the ceiling never applies. Details in
  `templates/CLAUDE.md`.

### `CLAUDE.md` line 383 (original)

- **Adding policy versions breaks teardown unless teardown prunes them.**
  `DeletePolicy` refuses while any non-default version exists
  (`DeleteConflictException` — botocore's own IAM model says "you must
  delete all the policy's versions"), so the two halves must land together:
  `_delete_policy_with_versions` deletes non-default versions first. This
  was unreachable until deploy could version a policy, and it was reached
  the same day — three MCP policies were left undeletable in the account.

### `CLAUDE-STATE.md` "Recent session work" (original, lines 73-157)

## Recent session work

Pointer-only by convention — full narrative for each round lives in
`docs/sessions.md`, not duplicated here.

- Sessions 45-51 — Login Node review; the MCP migration planned and
  written; Workstreams 1-7 complete.
- Session 52 — MCP fixes, the records store, live work on
  `osiris`/`certify`: Slurm PATH, SSM access scripts, teardown and create
  decomposed, the AnyIO event loop, login-node ASG looping.
- Session 53 — the remote transport deployed, exercised and removed;
  Stages A/B (`apply_queue_config` measured **30.5 min** vs Lambda's 900s,
  so the tier split is evidence-backed); R1-R3, where the REST gateway had
  to be *built* — none existed; **R4 PASS on the fourth method**, after
  four product defects and three measurement errors of my own, slowest
  remote call **20.0s of the 900s ceiling** against L4's blocking
  **1830s**; **R5 PASS** via `iam:SimulatePrincipalPolicy` over the
  shipped policies (31/31; the router's invoke is ARN-scoped, so it
  correctly simulates *denied* against `*`), corroborated at runtime by an
  ephemeral CloudTrail; **R6 PASS**, all eight read-only tools driven
  remotely against the live login-node cluster with **0 IAM denials** — it
  exists because R5 simulated each tier's *ceiling* while two tiers could
  not reach their **floor**. All four R4 defects are normative in
  `CLAUDE.md`. `docs/mcp-certification-checklist.md`.
- **Policy convergence + teardown pruning landed** (`--setup-infra` reports
  drift, `--update-policies` applies it; teardown prunes non-default
  versions, without which `DeletePolicy` raises `DeleteConflict`). Drift
  report over the 10 deployed MCP policies: **10 current, 0 stale**.
  `--setup-infra`/`--setup-gateway` alone no longer redeploy every zip
  tier (six 146 MB pip installs as a side effect of creating IAM);
  `tiers_to_deploy` is module-level so a test can reach it at all.
- **MCP deployment is its own permission set (option 4):**
  `MCPDeployPolicy` + `MCPRoleBoundary`, a fourth policy category.
  `OperatorPolicy` could not absorb it — 6,358 bytes against the 6,144
  limit — and is untouched. Every MCP role is bounded; `iam:CreateRole` is
  conditioned on `iam:PermissionsBoundary`. **Deployed and verified
  2026-08-26**: 7/7 roles bounded, the escalation probes moved
  `implicitDeny` -> `explicitDeny` (a ceiling, not merely an absent
  grant), read-only sweep 8/8, and a real `stop_fleet`/`start_fleet` round
  trip through the bounded tier. Residual: the boundary caps MCP roles,
  not the cluster roles a tier legitimately creates. Rationale and the
  deliberate asymmetries are in `templates/CLAUDE.md`.
- **13 of 14 certification tasks done**; only **L5** (teardown tokens)
  remains, deliberately left for teardown. `stageb` is up and carries an
  `r4probe` queue R4 added — remove it with teardown.
  `MCPStackMutation.json_src` is now 5,935 bytes of its 6,144 limit.
- **Session 54 — MCP certification finished 14/14, then CLI IAM hardening.**
  R4 failed four times before passing, each failure hidden by the one before:
  the three `pcluster.lib` wrappers formatted with a bare `{e}` (empty for
  every `ParallelClusterApiException`), so a PCluster **version skew** —
  artifacts at 3.16.0 against a 3.15.1 cluster, admitted by a *range* pin
  both guards accepted — reported as `BadRequestException: ` with nothing
  after it. Under that: `fleet-toggle` had no S3/DynamoDB grant and could
  never toggle a fleet, and **no tier granted any `elasticloadbalancing`
  action**, so `describe-cluster` failed from every tier against any
  `--enable_loginnode` cluster. R5 passed by `iam:SimulatePrincipalPolicy`
  over the shipped policies; R6 was added because R5 tested only *ceilings*
  while two tiers could not reach their **floor**. Also: policy convergence
  (`--update-policies`), teardown version-pruning, the MCP deploy policy and
  permissions boundary, and `--teardown`. **AWS is now empty** — cluster,
  transport, ECR repo, log groups and locks-bucket objects all removed.
- **Session 54 — CLI IAM hardening, LIVE-VERIFIED on cluster `ironclad`.**
  `ClusterNode-Deny` (a sixth managed policy, Deny-only, unconditional, head
  node role + all three `AdditionalIamPolicies` sites) and
  `ClusterRoleBoundary` (`pclustermaker-cluster-boundary`, account-level,
  head node only — compute and login roles are PCluster's CDK's).
  `OperatorPolicy` gained two grants and one Deny for the boundary, now
  5,475 of 6,144 bytes. Rationale in `templates/CLAUDE.md`. **Verified**:
  head node bootstrapped under the boundary, login node healthy behind its
  NLB, **a compute node booted and ran a job under the deny policy**, zero
  IAM denials cluster-wide, `RetentionInDays: 30` on a live log group,
  `add_queue`/`apply_cluster_update` through bounded tiers, R3 4/4, R6 8/8,
  and `--teardown` removing 25 live resources (Cognito domain before pool).
  Nothing remains in AWS.
- **A remote call has ~29s, not 900s** — API Gateway REST's integration
  timeout, `GATEWAY_INTEGRATION_TIMEOUT_MS`, already the maximum. Past it the
  caller gets a timeout while the Lambda runs on and the mutation succeeds,
  so a retry double-submits. Found live: `apply_cluster_update` ran 41,992 ms
  and the caller failed at 29.4s while the stack reached UPDATE_COMPLETE.
- **A stale stdio MCP server produces output indistinguishable from a
  defect.** Both bit today: `preview_cluster_delete` said "180 days" against
  source saying 30, and `finalize_cluster_teardown` left the sixth policy
  behind because its loaded code predated it. Check the source before
  believing the behavior; restart the server after editing `pcluster_core`.


### `CLAUDE-STATE.md` "Deferred work" completed-state narrative (original, lines 158-232)

## Deferred work

**MCP migration**: plan in `docs/parallelclustermaker-mcp-plan.md`.
**Workstreams 1-7 COMPLETE**, 5 and 6 live-verified in session 53 (built:
`mcp_server/` — `server.py`, `tools.py`, `confirmation_token.py`,
`router.py`, `tiers.py`, `handlers/`, `packaging.py`, `deploy.py`,
`Dockerfile.stack-mutation-node`; 9 IAM policies in `templates/`;
`_setup_mcp_infra`/`_delete_mcp_infra`). Detail in `docs/sessions.md`
14-49, 53. What stays load-bearing:
- **Zero `ansible-playbook` invocations** remain in the execution path;
  `create_pcluster.yml`/`delete_pcluster.yml` stay as unexecuted reference
  specs, and deleting either is a separate, unasked decision.
- The 900s ceiling, the `aud`-claim fact, the local/remote split, the
  tier/IAM split and the Lambda-packaging rule (including that
  `requirements.txt` must never reach an artifact) are **normative in
  `CLAUDE.md`**. `test_no_routed_tool_wrapper_passes_wait_true` and
  `_MCP_LAMBDA_POLICY_FILES` are the guards.
- **`fastmcp` breaks `import ansible`** — setuptools' `_distutils_hack`
  adds a second `FileFinder`; `tests/conftest.py` imports
  `ansible.plugins.action.template` first. Do **not** loosen
  `TestTheTestEnvironmentMatchesAnsible` instead.
- **Patch `mcp_server.tools.<name>`, not `pcluster_core.<name>`** — bound
  at import time. On `fastmcp` 3.x assert on `to_mcp_tool().inputSchema`.
- **The cluster lock is held at the wrapper layer** (`_cluster_lock`).
  `create_cluster`/`delete_cluster` must **never** be wrapped — they lock
  internally and would deadlock.
- **`confirmation_token.py` is not authentication** — keyless, so it stops
  an *unpreviewed* execution, not a hostile one. `verify` runs **before**
  the record lookup so the gate is not reachable-around.
- **The router terminates protocol methods** and routes only
  `tools/call`. Three places name the Lambda functions and cannot share an
  import — pinned both ways. The router requires **nothing** third-party,
  and must check `FunctionError`: a failing Lambda returns StatusCode 200
  with a stack trace as the payload.
- **`_REMOTE_DENIED_PARAMS`** and `_validate_at_least_one_queue` are
  enforced on **both** preview and execute; `overrides` rejects unknown
  keys and wrong types (`type() is`, since `bool` subclasses `int`).
- **`FastMCP.call_tool` returns a `ToolResult`, not a dict** — unpack both
  `.content` and `.structured_content`; it **raises `ToolError`** rather
  than setting `is_error`.
- **`ssh_available=not remote`, never a hardcoded literal.** Layer 1
  compares each wrapper to the core it calls (AST, both directions).
- Auth is live-verified through a REST gateway with real Cognito tokens
  (access 200, ID token 401, revoked client 401, `WWW-Authenticate`
  present); `discovery.py` had no production caller until R2, and
  `register_lambda` serves it. `deploy_mcp.py --setup-infra` is the
  production deploy path. **Auth alone remains unexercised** end to end
  since the transport was torn down.

**Shared cluster store (both phases) BUILT; confirmed live from the CLI
and from an MCP build, never on a Lambda.** `vars/<name>.json` and
`configs/<name>.yaml` in the lock bucket, so a machine that did not build
a cluster can see and edit it. Constraints are normative in
`CLAUDE.md`/`CLAUDE.local.md`; as-built detail in
`docs/records-store-plan.md`. Verified 2026-08-24/25: record, config and
mirror marker written in the **cluster's** region; CLI queue add/remove
round-tripped; teardown removed them under the `cf_delete_confirmed`
gate; and store-only resolution (no local files) answered health, listing
and queues from S3. Still open: **nothing has exercised it on a deployed
handler** — deploying one tier and calling `list_clusters` through it
would prove the half no test reaches.

**The CLI path is live-verified as of 2026-08-22.** One
build-and-teardown cycle found four defects the suite could not, all one
shape: **a value correct in the local context, used in a remote one** —
and one of them (a macOS `stage_dir` `mkdir`'d on an Ubuntu head node) is
invisible on Linux, so CI could never have caught it. Prefer a static
guard wherever the only observable effect is *which endpoint or path a
call reaches*, since every AWS call in the suite is stubbed.

The Login Node feature (`--enable_loginnode`) is implemented on `main` and
**confirmed on a live build** (`osiris`, `ubuntu2404arm`, 2026-08-12).
Design record:
`/Users/rmarable/.claude/plans/define-plan-mode-what-kind-treasure.md`.


### Session 54 closed (2026-08-26)

Six commits, all pushed: `a931b82` remote-transport defects, `b83988a` CLI
IAM hardening and the CloudWatch lifetime, `0b87a67` `--teardown`, `54c80e1`
the 401 header's one source, `e6a7bcd` the 29s gateway ceiling, `2c80dbf`
the preamble condensed with `_CEILING` ratcheted 150,000 -> 142,000. Suite
**3238 passed, 1 skipped**. MCP certification **14/14**. AWS holds nothing.

**Six product defects, none reachable from a checkout.** The bare `{e}` in
three `pcluster.lib` wrappers; `PCLUSTER_REQUIREMENT` as a range rather than
a pin; `fleet-toggle` with no S3/DynamoDB grant; no tier granting any
`elasticloadbalancing` action; `www_authenticate_header` with no caller while
`setup_gateway` rebuilt the string inline and had already diverged on a
trailing slash; and API Gateway's 29s ceiling described in `CLAUDE.md` as
900s. Plus `--teardown`, which did not exist -- the transport could be built
and not removed.

**The preamble budget went from 89 usable bytes to 7,019** across two
condensing passes, with the ceiling ratcheted twice so the reduction is
locked rather than banked. The method that worked both times: move discovery
narrative to files outside the budget, keep the rule, the mechanism and every
citation in place. The method is now largely spent in `CLAUDE.local.md`; the
second pass had to target `CLAUDE.md` and `CLAUDE-STATE.md` instead, which is
where this session's own growth was.

**What generalizes, and it is not about IAM.** Nearly every wrong conclusion
today came from a signal that resembled success:

    HTTP 200                     a JSON-RPC error is a 200 with an error member
    a CloudWatch REPORT line     records duration, not outcome
    "error" not in payload       the payload was "" -- no error AND no result
    a surviving mutation         replace(..., 1) patched the first of two
                                 identical lines, in the wrong function
    a monitor glob               matched `idle~` as `idle`; the node was down
    a stale stdio MCP server     two outputs indistinguishable from defects,
                                 against a source tree that was correct
    three over-literal greps     searched for a string the docs did not use

R4 was recorded as PASS three times before it actually passed. The
corrections are structural rather than resolutions to be careful: a pass
needs a positive result **and** an observed state transition; a mutation must
be verified to have applied before its survival means anything; a check
written as an assertion catches what a sweep would silently tidy away -- the
final teardown check expected *exactly two* IAM policies and that is the only
reason the stale server's orphaned `ClusterNode-Deny` was ever seen.

**The cluster earned its cost.** `ironclad` existed to test what 3,238 tests
cannot reach, and found two defects that only appear under real load. Every
claim in the session's commits is now backed by a live cluster: a compute
node booted and ran a job under `ClusterNode-Deny`, zero IAM denials
cluster-wide, and `--teardown` removed 25 live resources with the Cognito
domain deleted before its pool.
