# Final live-verification runbook

> **Status — VALIDATED 2026-08-31.** Run end to end on `osiris` (login-node
> Graviton, us-east-1): all four checks closed, the claude.ai connector hop
> included. The run earned its keep — it caught a real bug no render test
> could: an accounting pool override larger than the head node's RAM aborted
> MariaDB and silently disabled accounting, now fixed by a boot-time RAM
> clamp (`postinstall.j2`, commit `dd9af91`). Afterward the cluster, the
> transport, the claude.ai connector and even the retained CloudWatch log
> group were all torn down — the account is clean, zero footprint. The
> procedure below stays runnable for any future cluster or as a regression
> check.

Four things were proven by test and static analysis but, until this runbook
was first run, not on live AWS. It closes all four in **one session, one
cluster, one transport deploy**, across three phases:

1. **Claude Code (local CLI)** — stand up the cluster and verify #1 and #2.
2. **claude.ai (browser connector)** — deploy the remote transport and
   verify #3, plus general monitoring.
3. **Claude Code (local CLI)** — tear the cluster and transport down.

| # | What each check exercises (all closed in the run above) | Phase | Why not claude.ai |
|---|---|---|---|
| 1 | An **explicit accounting-DB size override** starts MariaDB cleanly on the target instance. | 1 | Confirming the size needs a MariaDB query on the head node (SSH). The browser has no shell. |
| 2 | A **compute node's** stdout reaches CloudWatch and **survives scaledown**. | 1 | Needs `aws logs` on the compute instance's stream. The browser has only the MCP tool surface, and `diagnose_cluster` reads *head-node* logs. |
| 3 | The **claude.ai browser connector hop** — real Cognito OAuth/PKCE through API Gateway. | 2 | This *is* the browser path; Claude Code's local stdio server bypasses it entirely. |
| 4 | **A login-node cluster is describable from the remote tiers.** The pool sits behind an NLB, and no MCP tier granted `elasticloadbalancing` until the review fixed it — so `describe-cluster` (and every tool built on it) failed from the browser against any `--enable_loginnode` cluster. Fixed and pinned by test; confirmed live in this run. | 1 + 2 | The remote-tier ELB path only exists in the browser; Claude Code's local server never hit the gap. |

**Why the split matters:** Claude Code and claude.ai are different
transports. Claude Code (local stdio) has the full tool set and can SSH and
run the AWS CLI — so #1 and #2 live there. claude.ai reaches only the remote
Lambda tiers over OAuth, cannot SSH or submit jobs, and is the *only* thing
that exercises the connector hop — so #3 lives there.

Run everything in **us-east-1** (the account's MCP topology and locks bucket
live there — see `CLAUDE-STATE.md`). Replace `<...>` placeholders. The
example cluster is `osiris`.

---

## Phase 1 — Claude Code: build the cluster and verify #1 and #2

From the repo root with the venv active (`source .venv/bin/activate`).

Prerequisites: the operator policy is attached
(`./generate_operator_policy.py --bootstrap`, once per account); Node.js is
on `PATH` (`node --version`).

### 1.1 Build with an explicit size override

The override values are deliberately larger than the `"auto"` ceiling (pool
cap 4096M, log cap 256M) so a clean start proves the override took, not the
derivation.

**Note the boot-time clamp.** A buffer pool larger than the head node's RAM
aborts InnoDB at startup and the non-fatal latch then leaves accounting
silently OFF — found live on 2026-08-31, where `8192M` on a 3.8 GB
`c8g.large` head node did exactly that. `postinstall.j2` now clamps an
explicit override to **80% of RAM** and warns on stdout. So `8192M` is only
used verbatim on a head node with ≥ ~10.24 GB RAM; on a smaller one it is
reduced to 80% of RAM (e.g. 3040M on 3.8 GB) with a `WARNING: … clamping to
…` line in the head node's log, and MariaDB still starts. Pick a head node
sized for the override you want tested, or read the effective value back in
1.2.

```bash
./make_pcluster.py \
  --az us-east-1a \
  --cluster_name osiris \
  --cluster_owner <you> \
  --cluster_owner_email <you@example.com> \
  --base_os ubuntu2404 \
  --headnode_instance_type c5.xlarge \
  --compute_instance_type c5.large \
  --enable_loginnode true \
  --loginnode_count 1 \
  --enable_slurm_accounting true \
  --slurm_accounting_buffer_pool_mb 8192 \
  --slurm_accounting_log_file_mb 512
```

`--loginnode_count 1` is deliberate: `--enable_loginnode true` alone does not
guarantee a running login-node instance, and both the browser's
`resolve_access_info` and `access_cluster.py`'s default target depend on one
actually being up.

~20–35 minutes. Then:

```bash
./check_pcluster.py -N osiris
```

**Pass:** `[PASS] Slurm` with a usable node count. A fleet that is all
`down`/`drained` is a failed bootstrap, not a pass.

### 1.2 Verify accounting and the override (#1)

```bash
./access_cluster.py -N osiris        # SSH to the head node
```

On the head node:

```bash
sbatch --wrap="hostname; sleep 10"
squeue
sacct --format=JobID,JobName,State,Elapsed        # job history from MariaDB

sudo mariadb -N -B -e 'SELECT @@innodb_buffer_pool_size, @@innodb_log_file_size;'
```

**Pass (#1):** the job reaches `COMPLETED` in `sacct`, **and**
`innodb_buffer_pool_size` is the **effective** override — `8589934592`
(8192 MB) on a head node with ≥ ~10.24 GB RAM, or the clamped **80% of RAM**
(e.g. `3187671040` = 3040 MB on a 3.8 GB node) with a `clamping to …` line in
the head node's cloud-init log. `innodb_log_file_size` is `536870912`
(512 MB) — the log lives on disk, so it is never clamped. A value **below**
either the override or its clamp is a fail; MariaDB may round the pool up, so
at-or-above the effective value is a pass. If MariaDB is not running at all,
the override exceeded RAM *and* the clamp did not fire — capture the config:

```bash
sudo cat /etc/mysql/mariadb.conf.d/99-slurm-acct.cnf 2>/dev/null || \
  sudo cat /etc/my.cnf.d/99-slurm-acct.cnf 2>/dev/null
```

Record the exact bytes seen — that measured evidence is what closes #1.

### 1.3 Verify compute-node stdout reaches CloudWatch (#2)

The stderr→stdout capture fix rests on stdout being captured to CloudWatch;
that is confirmed on the head node. This checks a **compute** node, and that
it ships **before scaledown** terminates it. A healthy node emits no
warning, so this verifies the *capture path* (a known stdout line survives),
which is sufficient — warnings ride the same stream.

Keep a compute node alive long enough to inspect (from the head node, or
via a longer job):

```bash
sbatch --wrap="sleep 600"
squeue -o "%N %T"
```

From your **workstation** (Claude Code, not the head node):

```bash
# log group is discovered by prefix (creation-timestamp suffix)
GROUP=$(aws logs describe-log-groups --region us-east-1 \
  --log-group-name-prefix /aws/parallelcluster/osiris- \
  --query 'logGroups[0].logGroupName' --output text)

# the running compute instance
CID=$(aws ec2 describe-instances --region us-east-1 \
  --filters "Name=tag:parallelcluster:cluster-name,Values=osiris" \
            "Name=tag:parallelcluster:node-type,Values=Compute" \
            "Name=instance-state-name,Values=running" \
  --query 'Reservations[0].Instances[0].InstanceId' --output text)

# a stdout line every compute node writes
aws logs filter-log-events --region us-east-1 --log-group-name "$GROUP" \
  --log-stream-names "${CID}.cloud-init-output" \
  --filter-pattern '"Ready to finish joining the cluster"'
```

**Pass (capture):** the `Ready to finish joining the cluster!` line appears
in the compute instance's stream.

**The hard half — survives scaledown:** let the node idle out
(`scaledown_idletime`) until `aws ec2 describe-instances` shows it gone, then
**re-run the `filter-log-events` above**. The log group is retained.

**Pass (#2):** the line is still readable *after* the instance is terminated
— it shipped before dying. If the stream is gone after scaledown, that is the
real gap the todo warned about; record which streams exist for `$CID`.

*(Optional literal warning: a second queue on an instance type whose distro
lacks `nvtop`/`htop` forces the non-fatal `WARNING:` at
`postinstall.j2:234-262`. Not required — the capture checks close #2.)*

### 1.4 Verify the login node operates (part of #4)

The login-node *feature* was confirmed on an earlier live build (IAM is
`ComputeNode-Base` only, `/home` is NFS-mounted not rebuilt, `-H`/`-L`/default
resolve to distinct hosts). This step re-confirms it operates in *this*
cluster; Phase 2 then confirms the browser can describe it.

```bash
./access_cluster.py -N osiris -L        # -L lands on the login node, not the head node
```

On the login node, confirm it shares the head node's home and runs Slurm:

```bash
whoami; hostname                        # a distinct host from the head node
ls -la ~                                # same NFS-mounted /home the head node exports
sinfo -o "%P %D %T"                     # Slurm environment works from the login node
sbatch --wrap="hostname"                # a general user can submit from here
```

**Pass (login node):** `-L` lands on a host distinct from `-H`, `$HOME` is
the shared tree, and `sinfo`/`sbatch` work — a general user can operate
without touching the head node. (Contrast: with `-H` you reach the head node,
where the accounting DB and Spack/Lmod build live.)

**Leave the cluster up** — Phase 2 monitors it from the browser.

---

## Phase 2 — Deploy the transport, then verify #3 in claude.ai

The transport is currently torn down. Deploy it from **Claude Code** (it is a
CLI), then do the actual verification in a **browser** at claude.ai — that
browser OAuth flow is the hop, and Claude Code cannot stand in for it.

### 2.1 Deploy the transport (Claude Code)

```bash
# MCP deploy policy is separate from the operator policy; once per account:
./generate_operator_policy.py --mcp --create

# six of seven tiers (no container tier needed just to monitor):
AWS_REGION=us-east-1 ./deploy_mcp.py --bootstrap --create-user <you@example.com>
```

It prints the **MCP endpoint** (`/mcp` URL), the **OAuth Client ID**, and a
**one-time password** (unless `MCP_USER_PASSWORD` is set; a lost one is
re-set by re-running `--create-user`, never recovered).

### 2.2 Connect in claude.ai (browser)

1. **Customize → Connectors → `+`** (Team/Enterprise: an Owner adds it under
   **Organization settings → Connectors → Add → Custom → Web**).
2. Paste the **MCP endpoint** (the `/mcp` URL, not a discovery URL).
3. **Advanced settings → OAuth Client ID** = the value from 2.1. Leave
   **OAuth Client Secret empty** (public PKCE client).
4. **Add → Connect**, sign in at the Cognito page with the username and
   password from `--create-user`. Enter the username **exactly** as created
   (an email is a literal username, not an alias).
5. Approve the consent screen.

### 2.3 Verify the hop and monitor osiris (browser)

**Pass (#3):** the tool list shows **15** tools, and a browser chat request —
"check the health of osiris" or "list my clusters" — returns a real answer.
That single round-trip (browser → Cognito → gateway → authorizer → router →
handler → back) is the whole check-off.

**Pass (#4, the half that only the browser can prove):** because osiris has a
login node behind an NLB, `check_cluster_health` / `diagnose_cluster` /
`resolve_access_info` from the browser reach `describe-cluster`, which reads
the load balancer. Before the review, every remote tier lacked
`elasticloadbalancing` and these failed with an opaque error against any
login-node cluster; the fix is pinned by test but unconfirmed live. A clean
answer here — health returned, login-node access info surfaced — is that
confirmation. An error mentioning `elasticloadbalancing` or a load balancer
is the regression.

While connected, confirm the monitoring surface against the live osiris:
- `check_cluster_health`, `diagnose_cluster`, `list_queues`,
  `get_cost_report`, `resolve_access_info`, read-only Slurm queries
  (`sacct`/`sinfo`-style job, queue and node state), `start_fleet`/
  `stop_fleet`.
- **Cannot submit jobs** from the browser — `run_readwrite_slurm_command` is
  local-only. You inspect job/queue/node state but do not launch work.
- **Cannot create/modify** clusters unless the container tier is also
  deployed (`./deploy_mcp.py --tier stack-mutation-node`, needs a container
  runtime — `INSTALL.md`).

**If it will not connect:** a *reload* reuses the stored OAuth session and
keeps failing when the session is the problem. **Disconnect and add it
again.** The first call after idle is a Lambda cold start; retry.

---

## Phase 3 — Claude Code: tear both down

```bash
# the cluster
./kill_pcluster.py --az us-east-1a -N osiris -O <you>

# the transport (add --dry-run first to list)
AWS_REGION=us-east-1 ./deploy_mcp.py --teardown
```

Left behind on purpose: the permissions boundary, the Lambda log groups, and
the cluster's own CloudWatch log group (each is the only surviving record of
a failure, or a boundary a deployer must not be able to delete). Confirm the
rest is gone:

```bash
./list_pcluster.py                       # no osiris
aws lambda list-functions --region us-east-1 \
  --query "Functions[?starts_with(FunctionName, 'pclustermaker-mcp-')].FunctionName" \
  --output text
```

**Pass:** no cluster listed, no `pclustermaker-mcp-*` functions. Idle cost
back to zero.

---

## Checklist — closed 2026-08-31 on osiris

Boxes reflect that run. To re-run against a new cluster, copy them and clear.

- [x] **#1** (Phase 1, Claude Code) — 8192M > the 3.8G head node's RAM aborted
      InnoDB (accounting silently OFF); the boot-time clamp reduced it to
      3040M (`@@innodb_buffer_pool_size = 3187671040`), MariaDB started, and
      `sacct` returned the job. Bug caught and fixed (`dd9af91`).
- [x] **#2** (Phase 1, Claude Code) — `Ready to finish joining the cluster!`
      found in a compute node's `cloud-init-output` stream that **outlived
      the node** (`i-01677a…`, already terminated). Capture + scaledown
      survival both confirmed.
- [x] **#3** (Phase 2, claude.ai) — after a disconnect/re-add, the connector
      listed 15 tools and executed List Queues / List Clusters / Diagnose
      through the Cognito OAuth flow.
- [x] **#4a** (Phase 1, Claude Code) — `-L` reached a distinct host
      (`ip-172-31-29-138`), `/home` NFS-shared, `sinfo`/`sbatch` worked there.
- [x] **#4b** (Phase 2, claude.ai) — `diagnose osiris` reached
      `describe-cluster` + CloudWatch against the login-node cluster with no
      `elasticloadbalancing` error (the review's ELB grant, confirmed live).
- [x] (Phase 3, Claude Code) — cluster and transport both torn down, no
      `pclustermaker-mcp-*` left, `list_pcluster.py` shows no cluster; the
      connector and the retained CloudWatch log group were removed too.

`CLAUDE-STATE.md` was updated accordingly (session 77): all three open live
items closed, and the login-node cluster recorded as operated and described
end to end through the remote transport.
