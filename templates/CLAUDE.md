# Claude Instructions — templates/

Loaded when working in `templates/`. Full rationale, incident history, and
test-name citations live in `templates/CLAUDE.local.md` (gitignored, local
development only). The root `CLAUDE.md` holds everything else, including
`preinstall.j2`/`postinstall.j2` node-bootstrap constraints.

The IAM policy documents here are `.json_src` files rendered by Jinja2 and
guarded by `tests/test_templates.py`; `generate_operator_policy.py` renders
`OperatorPolicy.json_src`, and `_setup_iam` in `src/pcluster_core.py` creates
and deletes the five managed policies.

## Constraints

- **Five managed IAM policies**, named `<ec2_iam_policy>-<suffix>`, all
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
  - `HeadNode-Monitoring` — Grafana/Prometheus. Head node only, created
    only when `enable_monitoring=true`.
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
  cluster's policy in place.
