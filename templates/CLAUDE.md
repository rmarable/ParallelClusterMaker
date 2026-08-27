# Claude Instructions — templates/

Loaded when working in `templates/`. Full rationale, incident history, and
test-name citations live in `templates/CLAUDE.local.md` (gitignored, local
development only). The root `CLAUDE.md` holds everything else, including
`preinstall.j2`/`postinstall.j2` node-bootstrap constraints.

The IAM policy documents here are `.json_src` files rendered by Jinja2 and
guarded by `tests/test_templates.py`; `generate_operator_policy.py` renders
`OperatorPolicy.json_src`, and `_setup_iam` in `src/pcluster_core.py` creates
and deletes the six managed policies.

## Constraints

- **Six managed IAM policies**, named `<ec2_iam_policy>-<suffix>`, all
  deleted on teardown:
  - `HeadNode-Compute` — EC2, AutoScaling, LaunchTemplates, CreateFleet,
    DynamoDB lifecycle, SQS management, SNS, STS. Head node only.
  - `HeadNode-Storage` — CloudFormation, S3, Lambda, EFS, FSx, Route53.
    Head node only.
  - `HeadNode-IAM` — IAM: CreateRole, PassRole, AttachPolicy,
    InstanceProfile, RolePolicy (read/delete only). Head node only.
  - `ComputeNode-Base` — S3 read, CloudWatch Logs/metrics, SSM, DynamoDB
    CRUD, SQS signals, STS. Attached to the head node role **and** every
    compute queue via `AdditionalIamPolicies`.
  - `ClusterNode-Deny` — Deny statements only, no Allow. Attached to the
    head node role **and** every queue and the login node pool, like
    `ComputeNode-Base`, and **unconditional** — never gated on a flag.
  - `HeadNode-Monitoring` — Grafana/Prometheus. Head node only, created
    only when `enable_monitoring=true`.
- **`ClusterNode-Deny` denies only escalation primitives that no
  instance-reachable document grants**, so it hardens the posture without
  changing behavior. An explicit Deny beats any Allow in any attached
  policy — including one nobody added to a test list, which is what
  `LustreS3HydrationPolicy` was — so it turns the repo's IAM bans into a
  property of the account rather than of CI. Derive any addition from the
  documents (`TestTheDenyPolicyDeniesOnlyThingsNothingGrants` re-runs that
  derivation); denying something in use fails on a live node mid-bootstrap,
  where no test can see it. Every statement is `Effect: Deny` on
  `Resource: "*"` — a scoped Deny leaves the action reachable everywhere
  else, which for an escalation primitive is the whole account.
- **`logs:DescribeLogGroups` requires `Resource: "*"`** in every policy that
  grants it — the API call carries no log-group ARN at the IAM level. Keep
  it in its own statement, separate from scoped stream-level actions.
- **No policy an instance carries (the five managed ones or the inline
  Lustre-hydration policy) may grant `logs:DeleteLogGroup`** — and neither
  may the MCP Lambda execution-role policies (`templates/MCP*.json_src`).
  Retained log groups are the only surviving record of a failed build, which
  turns on what the log group is worth, not on whether the principal is an
  EC2 instance. Match with `fnmatch`, not string equality, when testing this
  (`logs:*` also grants it). `OperatorPolicy` stays exempt: purging by hand
  under operator credentials is what the retain rule expects.
- **`templates/MCP*.json_src` are a third policy category**, neither
  instance-reachable nor the operator's own: Lambda execution roles for the
  MCP remote transport (Workstream 5). They are listed in
  `_MCP_LAMBDA_POLICY_FILES` (`tests/test_templates.py`), not
  `_POLICY_FILES` — that one is pinned by equality to the five managed
  cluster policies. `TestMcpLambdaPolicies` gives them the same structural
  guards (valid JSON, the 6,144-byte limit **measured minified**, unique
  Sids, no unsubstituted placeholders) plus a cross-check that every
  placeholder they use is actually substituted by `_render_policy`.
  `<MCP_USER_POOL_ID>` is theirs alone.
- **A tier's policy has a floor as well as a ceiling — the detail behind
  the rule in the root `CLAUDE.md`.** Every MCP IAM guard asked whether a
  tier could exceed its blast radius; none asked whether it could reach
  its own, and two could not. `fleet-toggle`: `update-compute-fleet`
  parses the cluster config from PCluster's per-cluster S3 bucket and
  reads/updates the fleet status item in the `parallelcluster-<cluster>`
  DynamoDB table, and the tier granted neither, so
  `stop_fleet`/`start_fleet` failed against every real cluster for the
  tier's whole life. The S3 grant stays **read-only** — `s3:*` would have
  satisfied the floor while letting a fleet toggle rewrite any cluster's
  config — and the DynamoDB grant is scoped to `table/parallelcluster-*`.
  The same blindness hid a wider gap: **no tier granted any
  `elasticloadbalancing` action**, and a login-node pool sits behind an
  NLB that `describe-cluster` reads, so *every* remote tier failed against
  any `--enable_loginnode` cluster. All four calls in `pcluster/aws/elb.py`
  are needed (granting only `DescribeLoadBalancers` moves the failure to
  the next one); describes take no resource-level permission, so
  `Resource: "*"` is forced and read-only is the only bound left.
  `TestEachTierCanActuallyDoItsJob` pins both directions for both gaps.
- **`MCPDeployPolicy.json_src` and `MCPRoleBoundary.json_src` are a fourth
  category** — what an administrator grants to whoever deploys the
  transport, plus the permissions boundary every MCP role is created under.
  Listed in `_MCP_DEPLOY_POLICY_FILES`; they get the same structural guards
  (`_MCP_ALL_POLICY_FILES`). They used to sit **outside** `_BAN_APPLIES_TO`
  because the boundary *denies* `logs:DeleteLogGroup` and the ban read every
  statement without looking at `Effect`. The ban is `Effect`-aware now — it
  reads Allow statements only, and returns early when the document denies the
  action unscoped, which is IAM's own Deny-beats-Allow rule — so every policy
  document in the repo is inside it, `MCPDeployPolicy` included. **Do not go
  back to excluding files**: exclusion also stops the ban seeing an Allow that
  later lands in the same file, which is the hole
  `test_every_policy_template_is_covered_by_this_ban` exists to close.
  `_denied_outright` requires the Deny's `Resource` to be `"*"` and matches
  the action with `fnmatch`; `test_the_ban_still_fails_a_real_grant` drives the
  real assertion with a synthetic document, because both filters are ways for
  the ban to read nothing and pass.
- **The head node role is created under `pclustermaker-cluster-boundary`
  (`templates/ClusterRoleBoundary.json_src`), and only the head node role.**
  `config.pcluster.j2` gives the head node `InstanceRole:` — a role
  `_setup_iam` creates — but gives every `SlurmQueue` and the `LoginNodes`
  pool `AdditionalIamPolicies:`, so PCluster's CDK creates those roles.
  There is no `create_role` to pass `PermissionsBoundary=` on and no name
  known ahead of time, and conditioning `iam:CreateRole` on
  `iam:PermissionsBoundary` in `OperatorPolicy` — what `MCPDeployPolicy`
  does for the roles it owns — would refuse the CDK its own unbounded
  `CreateRole` and break every build. Compute and login nodes are capped by
  `ClusterNode-Deny` instead. This asymmetry with the MCP side is
  deliberate; document it, do not close it.
- **The ceiling is the union of services the instance-reachable documents
  actually grant**, plus four named margin services
  (`elasticloadbalancing`, `secretsmanager`, `resource-groups`, `tag`)
  pinned by equality. A boundary is an intersection, so a missing service
  removes every grant in it — and not at deploy time: `CreatePolicy` and
  `create_role` both succeed and the head node fails partway through
  bootstrap. Be generous at `svc:*` and precise in the Deny statements.
  `TestTheBoundaryCeilingCoversWhatTheClusterPoliciesGrant` re-derives it.
- **The cluster boundary is account-level and teardown deliberately leaves
  it**, for a stronger reason than the MCP boundary's: every other live
  cluster's role in the account is bounded by the same document, so
  deleting it on one teardown uncaps all of them. No teardown surface may
  name it.
- **`OperatorPolicy` gained `IAMClusterBoundaryBootstrapReadAndCreate`,
  `IAMBoundClusterRoleOnly` and `IAMDenyWeakeningTheClusterBoundary`.** The
  boundary's name is outside the `pclustermaker-policy-*` wildcard
  `IAMManagedPolicyLifecycle` covers, deliberately — an operator who can
  version their own boundary does not have one — so it needs its own
  create/read grant. `iam:PutRolePermissionsBoundary` is conditioned on
  `iam:PermissionsBoundary` equalling this boundary; `iam:CreateRole` is
  **not** conditioned, per the bullet above. Residual, stated rather than
  hidden: a `pclustermaker-role-*` role can still be created unbounded by
  whoever holds these credentials.
- **The boundary is named `pclustermaker-mcp-boundary`, outside the
  `pclustermaker-mcp-policy-*` pattern `MCPDeployPolicy`'s lifecycle
  statement covers.** A deployer who can version their own boundary does
  not have one. `MCPDeployPolicy` additionally *denies* `CreatePolicyVersion`/
  `SetDefaultPolicyVersion`/`DeletePolicy` on it and
  `DeleteRolePermissionsBoundary` on the roles, and grants `iam:CreateRole`
  **only** under a `StringEquals` condition on `iam:PermissionsBoundary` —
  without that condition the deployer creates an unbounded role and the
  ceiling never applies. `_setup_mcp_infra` creates the boundary before any
  role, passes `PermissionsBoundary=` on every `create_role`, and calls
  `put_role_permissions_boundary` on a role that already existed.
  Teardown deliberately leaves it: it is a durable account guardrail, and
  `MCPDeployPolicy` cannot delete it.
- **The boundary is reported on drift but never updated**, unlike the tier
  policies, which `--update-policies` converges. That asymmetry is the
  point — an administrator changes it out of band.
- **`MCPRouterLambda.json_src` must stay near-zero: one action
  (`lambda:InvokeFunction`), four explicit handler ARNs, no wildcard, and
  nothing outside the `lambda:` service.** The router is the
  internet-facing endpoint behind API Gateway and executes no tool logic;
  the whole 5-Lambda split exists to keep blast radius off it, so a single
  PCluster grant there defeats the design. The handler names it encodes
  (`pclustermaker-mcp-read-only`, `-fleet-toggle`, `-stack-mutation`,
  `-stack-mutation-node`) must match what `_setup_mcp_infra` creates —
  `TestRouterPolicyStaysNearZero` pins them. Execution logging for every
  MCP Lambda comes from the AWS-managed `AWSLambdaBasicExecutionRole`,
  attached separately, not from these documents.
- **`HeadNode-IAM` must never grant `iam:PutRolePolicy`.** Combined with
  `IAMCreateRole` and `IAMPassRoleInstanceProfile` it's a privilege-escalation
  chain to account admin. The toolkit's only `put_role_policy` call (FSx-S3
  hydration) runs under operator credentials, not on the head node.
- **`IAMAttachDetachPolicy`'s `iam:PolicyARN` condition must scope to
  `policy/pclustermaker-policy-<CLUSTER_SERIAL_NUMBER>-*`** — never to
  `parallelcluster-*` (matches nothing of ours) or to cluster *name* alone
  (`_validate_cluster_name` accepts `operator`, which would readmit the
  operator policy).
- **CloudFormation stack ARNs use the bare cluster name** —
  `arn:aws:cloudformation:*:<acct>:stack/<CLUSTER_NAME>/*` — there is no
  `parallelcluster-` prefix on the stack itself (unlike DynamoDB/SQS/S3
  resource names, which do carry it).
- **`ComputeNode-Base` must grant read on `arn:aws:s3:::*-aws-parallelcluster/*`**
  — the regional PCluster system bucket, which doesn't match
  `parallelcluster-*`.
- **`IAMListGlobal` in `HeadNode-IAM` is intentional** — `iam:ListRoles` etc.
  use wildcard resource paths because the PCluster head-node daemon calls
  `iam:ListRoles` at startup with no way to scope it further.
- **Route53 hosted-zone lifecycle (`CreateHostedZone`/`DeleteHostedZone`)
  belongs to the operator**, not the head node — zone IDs are random, so a
  per-cluster ARN scope is impossible. The head node only gets
  `ChangeResourceRecordSets` and read-only lookups.
- **Instance-profile and role ARNs are pinned to `<CLUSTER_SERIAL_NUMBER>`**
  — a bare `pclustermaker-role-*` wildcard lets cluster A touch cluster B's
  role/instance-profile.
- **The long-lived results bucket grant is `ListBucket` on the bucket plus
  `GetObject`/`PutObject` scoped to `hpc-benchmark-results/*`** — never
  `DeleteObject`/`DeleteBucket`, and never widen the object-level `Resource`
  to the whole bucket (that would let a shell on the head node overwrite or
  read every other cluster's results).
- **Operator-only IAM permissions** (never on a head/compute node): policy
  CRUD scoped to `pclustermaker-policy-*`; role attach/detach scoped to
  `pclustermaker-role-*` roles with an `iam:PolicyARN` condition; role CRUD;
  instance-profile management; keypair management; Secrets Manager CRUD
  scoped to `parallelcluster/*`; SSM parameter CRUD scoped to
  `/parallelcluster/*`; Cost Explorer/Pricing/STS read. See
  `templates/OperatorPolicy.json_src` and `generate_operator_policy.py`.
  `iam:CreatePolicyVersion`/`DeletePolicyVersion` are intentionally
  **omitted** — the toolkit only ever calls `create_policy`/`delete_policy`,
  and granting version-management would let the operator rewrite any
  cluster's policy in place. Pinned by
  `test_operator_policy_omits_policy_version_management`; the older guard
  covered `HeadNode-IAM` only. **`MCPDeployPolicy` does grant them and that
  is not a contradiction**: scoped to `pclustermaker-mcp-policy-*`, needed
  for `--setup-infra` to converge on a changed document, and every role
  those policies attach to is bounded — which is the mitigation
  `templates/CLAUDE.local.md` names for the cluster case and which no
  cluster role has.
