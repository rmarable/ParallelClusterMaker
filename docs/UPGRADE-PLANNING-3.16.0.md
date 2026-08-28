# Upgrading to AWS ParallelCluster 3.16.0

Status: **not started.** Pinned at 3.15.1 (2026-05-28); 3.16.0 was released
2026-08-20. Python stays at **3.12** — see [Python](#python-stays-at-312).

This is a plan, not a runbook. It is ordered so that the cheap checks that
could stop the upgrade happen before the expensive ones, and so that
nothing is deployed until the suite is green.

## Why this needs a plan at all

Two properties of this repo make a minor version bump more than a string
change.

**The pin is exact on four surfaces, and that is deliberate.** A bounded
range was tried and failed: `>=3.15,<3.17` is one identical string
everywhere, so an agreement test passes while pip resolves it to different
versions at different times — a Lambda artifact on 3.16.0 against a venv on
3.15.1. Every remote tool then fails with *"the update can be performed
only with the same ParallelCluster version."* `test_the_pin_is_exact`
requires the operator specifier set to be exactly `{"=="}`.

**Tests read facts out of the installed package rather than restating
them.** That is the right call — a restated constant drifts silently — but
it means an upstream change surfaces as a test failure here, and each one
needs judging rather than fixing. The full surface is listed below.

## What the upgrade touches

### The version pin, all four at once

| surface | what it is |
|---|---|
| `requirements.txt` | the development set |
| `PCLUSTER_REQUIREMENT` in `mcp_server/packaging.py` | the source of truth for Lambda artifacts |
| `requirements-lambda.txt` | **generated** — regenerate, never hand-edit |
| the `.venv` itself | `pip install -r requirements.txt` |

A partial bump fails `test_the_pin_is_exact` immediately, which is the
point. `requirements-lambda.txt` is regenerated with the command in its
own header comment; a byte-equality test compares it to what the tier spec
would produce.

### The upstream API surface this repo depends on

Every one of these is imported directly and can move in a minor release:

```
pcluster.lib                          create_cluster, delete_cluster,
                                      describe_cluster, update_cluster,
                                      update_compute_fleet
pcluster.api.errors                   BadRequestException, NotFoundException
pcluster.api.converters               cloud_formation_status_to_cluster_status
pcluster.api.models                   CloudFormationStackStatus, ClusterStatus
pcluster.config.cluster_config        CloudWatchLogs
pcluster.config.update_policy         UpdatePolicy
pcluster.constants                    CW_LOGS_RETENTION_DAYS_DEFAULT
pcluster.schemas.cluster_schema       ClusterSchema, CloudWatchLogsSchema
pcluster.utils                        get_installed_version
```

Three deserve specific attention, because a test asserts on their
*content* rather than their existence:

* **`CloudWatchLogsSchema.retention_in_days`** — its `validate.OneOf` set
  must still contain 30, and its `update_policy` must still be
  `UpdatePolicy.SUPPORTED` (with `enabled` still `UNSUPPORTED`, which is
  the vacuity guard that the two are distinguishable at all).
* **`CW_LOGS_RETENTION_DAYS_DEFAULT`** — asserted to differ from our 30, so
  a change upstream to 30 would fail a test that is then wrong rather than
  useful.
* **`ClusterSchema`** — the rendered cluster config is loaded through it,
  and marshmallow *ignores* unknown keys rather than rejecting them, so a
  renamed key fails quietly at build time instead of loudly in CI.

`cluster_stack.py` line citations in `CLAUDE.local.md` (293, 295, 1362,
1375, 1249-1262) will almost certainly drift. They are swept by
`tests/test_claude_docs_line_citations.py`, which will name each one.

## Changes in 3.16.0 that affect this toolkit

### Already handled

**`tag:GetResources` is a new required CLI permission** — it resolves login
node load balancer ARNs by tag. Granted in `cb3f50c` to
`MCPStackMutation`, `MCPReadOnlyLambda`, `MCPFleetToggleLambda` and
`OperatorPolicy`, with `tag:*` added to `MCPRoleBoundary`'s ceiling —
without which all three tier grants are nullified, silently, since a
boundary is an intersection.

**Not yet deployed.** The tier policies land on the next `--bootstrap`;
`OperatorPolicy` needs a new version pushed under the operator's own
credentials, because `deploy_mcp.py` deliberately cannot touch it.

**NFS lock manager port moves 32768 → 4045.** `_EXTERNAL_NFS_PORTS` is
already `(111, 2049, 4045, 4046, 4047)`. No change needed — luck rather
than foresight, and worth an explicit re-check rather than trusting this
line.

**Amazon Linux 2 and AWS Batch are no longer supported.** `alinux2` and
`alinux2arm` are already in `_UNSUPPORTED_OSES`
(`tests/test_templates.py`) and absent from `make_pcluster.py`'s
`--base_os` choices; the scheduler is always Slurm. Nothing to do.

**`ClusterNameValidator` limits names to 40 characters with
`ExternalSlurmdbd`.** Not used here.

### Needs investigation before bumping

**Bootstrap files move off `/tmp` into `/opt/parallelcluster/tmp`**, so
that builds work on custom AMIs mounting `/tmp` with `noexec`.

There is **no path collision**: this repo stages to
`/tmp/_ParallelClusterMaker_stage/<serial>` — rooted at the literal
`/tmp`, never `tempfile.gettempdir()`, because the resolution runs on
macOS and the path is used on an Ubuntu head node — while PCluster's files
move elsewhere entirely.

The real question is the one that motivated upstream's change: **if `/tmp`
is mounted `noexec`, our staging tree is affected too.** It carries the
generated access scripts, the Grafana tunnel and the benchmark tree, and
those are executed. Upstream has just made itself immune to a condition we
remain exposed to. Not a blocker for the upgrade — the exposure exists
today on 3.15.1 — but worth deciding on deliberately rather than
inheriting.

**`MultiNetworkInterfacesInstancesValidator` now also covers
single-network-card instances with EFA enabled**, which cannot be
auto-assigned a public IP. `config.pcluster.j2` sets no `AssignPublicIp`,
so the subnet default decides — meaning a configuration that built
yesterday could be rejected today on a public subnet with EFA. This is the
most likely source of a surprise rejection.

**Login node update orchestration is now head-node-driven**, replacing
`cfn-hup`/`cfn-init`. Combined with a fix for login nodes not mounting
`/opt/parallelcluster/shared` under EFS, this is the area of largest
behavioral change for a feature this toolkit supports. `postinstall.j2`
gates on `NODE_TYPE == "LoginNode"` and the monitoring wrapper exits 0
there; both want re-checking on a live login-node build.

**NFSv4-only on the managed NFS server.** External NFSv3 mounts are
explicitly unaffected, but `enable_external_nfs` is worth exercising.

### Informational — node-level upgrades

Slurm 25.11.6, EFA installer 1.49.0, CUDA 13.2.2, NVIDIA driver 595.71.05,
DCGM 4.6.0, Enroot 4.2.1, Pyxis 0.24.0, GDRCopy 2.6, PMIx 5.0.11, Cinc
19.3.14, stunnel 5.78, and Python 3.14.6 in the AMI. NVIDIA components now
install from distribution packages rather than run-file installers, and on
RHEL-family OSes the Xorg driver is installed and Wayland disabled.

Two egress changes matter for restricted networks: `amazon-efs-utils` now
comes from `amazon-efs-utils.aws.com` (allowlist it), and
`aws-parallelcluster-node` from S3 rather than PyPI, which *helps*
air-gapped setups.

## Python stays at 3.12

3.16.0 adds Python 3.13 support to the CLI and declares
`requires_python >=3.9`, so **3.12 remains supported and nothing forces a
move.** `.python-version` stays `3.12`, the venv guard stays as it is, and
`INSTALL.md`'s "Python 3.12 only" language stays accurate.

One wording change is worth making: `CLAUDE.local.md` says
`aws-parallelcluster` "does not support Python 3.13 or 3.14," which
becomes false for 3.13 on this version. It is still true that this project
pins 3.12 — the reason just changes from *upstream cannot* to *we choose
not to*, and a normative doc should not assert the wrong one.

## Sequence

Ordered so nothing expensive happens before the cheap thing that could
stop it.

1. **Scratch venv, suite only.** Install 3.16.0 into a throwaway venv,
   point the suite at it, and read every failure. Do not touch tracked
   files. This is the go/no-go, and it costs nothing.
2. **Judge each failure.** A line citation that drifted is bookkeeping. A
   `OneOf` set that lost 30, or an `update_policy` that changed, is a
   decision about behavior. Do not fix a test until it is clear which it
   is.
3. **Bump all four surfaces**, regenerate `requirements-lambda.txt`, and
   run the full suite plus `make lint` and `make shellcheck`.
4. **Correct the Python wording** in `CLAUDE.local.md`.
5. **Deploy the IAM first**, before any cluster: push the new
   `OperatorPolicy` version, then `--bootstrap` for the tier policies.
   `tag:GetResources` is required *by 3.16.0*, so it must be in place
   before the first 3.16.0 cluster, not after.
6. **Rebuild and push the container image.** The Lambda artifact bakes the
   pin, so skipping this leaves the node tier on 3.15.1 against a 3.16.0
   venv — exactly the skew the exact pin exists to prevent. Needs the
   Finch credential dance; scrub all four locations afterward, the macOS
   keychain included.
7. **Build one plain cluster** and tear it down.
8. **Build one `--enable_loginnode` cluster.** This is the real test:
   login nodes have the most 3.16.0 change, and `tag:GetResources` exists
   for them. Exercise `access_cluster.py -L`, then tear it down and
   confirm auto-finalize still completes.
9. **Build one `--enable_efa` cluster on the subnet you normally use**, to
   settle the `MultiNetworkInterfacesInstancesValidator` question against
   reality rather than by reading.

## What would make this not worth doing yet

Nothing in 3.16.0 is a fix this toolkit is currently suffering from. The
login-node load balancer race and the IAM tag-condition bug are real, but
neither has been observed here. If step 1 produces failures that are
decisions rather than bookkeeping, deferring is reasonable — 3.15.1 is
four months old, not abandoned.

The one argument for moving sooner is that `tag:GetResources` is already
granted and unused, so half the migration cost is paid.
