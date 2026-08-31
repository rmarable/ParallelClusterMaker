#!/usr/bin/env python
#
################################################################################
# Name:         deploy_mcp.py
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Purpose:      Build and deploy the MCP remote transport's Lambda tiers
################################################################################

import os
import sys

_repo_root = os.path.dirname(os.path.abspath(__file__))
_src_dir = os.path.join(_repo_root, "src")
if os.path.realpath(sys.prefix) != os.path.realpath(os.path.join(_repo_root, ".venv")):
    sys.exit(
        f"ERROR: Run this script inside the repo virtual environment.\n"
        f"  $ source {os.path.join(_repo_root, '.venv', 'bin', 'activate')}\n"
        f"  $ {sys.argv[0]} ..."
    )

import argparse
import shutil
import subprocess
import tempfile

sys.path.insert(0, _src_dir)
sys.path.insert(0, _repo_root)

from mcp_server.deploy import (  # noqa: E402
    CONTAINER_RUNTIMES,
    FUNCTION_NAMES,
    IMAGE_PLATFORM,
    IMAGE_REPOSITORY,
    ecr_login,
    delete_cognito_pool,
    delete_gateway,
    delete_mcp_functions,
    build_and_push_image,
    delete_ecr_repository,
    deploy_tier,
    detect_container_runtime,
    ensure_cognito_user,
    ensure_ecr_repository,
    ensure_lambda_can_pull,
    ImageBuildError,
    preflight_deploy_permissions,
    generate_user_password,
    setup_gateway,
)
from mcp_server.packaging import (  # noqa: E402
    TIER_PACKAGES,
    ZIP_UNZIPPED_LIMIT_BYTES,
    prune_for_lambda,
    render_requirements_file,
    sources_for,
)
from pcluster_core import (  # noqa: E402
    _delete_mcp_infra,
    _derive_locks_bucket,
    _derive_mcp_user_pool_name,
    _mcp_boundary_name,
    _setup_mcp_infra,
    ensure_slurm_document,
    delete_slurm_document,
)

# Lambda refuses a direct upload over 50 MB; every zip tier here exceeds it
# once PCluster is installed, so the artifact goes via S3. The bucket is the
# lock bucket rather than a new one: it is already account+region scoped,
# already created by the first build, and already in every tier's IAM.
_DIRECT_UPLOAD_LIMIT = 50 * 1024 * 1024
_S3_PREFIX = "lambda"

# pip must resolve wheels for Lambda's platform, not the operator's. Without
# these an arm64 laptop stages arm64 wheels into an x86_64 function and the
# failure is an ImportError at the first invocation, not at build time.
_PIP_PLATFORM = [
    "--platform",
    "manylinux2014_x86_64",
    "--implementation",
    "cp",
    "--python-version",
    "3.12",
    "--only-binary=:all:",
]


def _run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def _build_zip(tier, build_dir, zip_path):
    """pip install --target, stage this repo's sources, prune, zip."""
    req = os.path.join(build_dir, "requirements.txt")
    with open(req, "w") as fh:
        fh.write(render_requirements_file(tier))

    print(f"  installing dependencies for {tier}...")
    _run(
        [
            os.path.join(_repo_root, ".venv", "bin", "pip"),
            "install",
            "--quiet",
            "--target",
            build_dir,
            *_PIP_PLATFORM,
            "--upgrade",
            "-r",
            req,
        ],
        stdout=subprocess.DEVNULL,
    )

    for entry in sources_for(tier):
        src = os.path.join(_repo_root, entry)
        dst = os.path.join(build_dir, entry)
        if os.path.isdir(src):
            shutil.copytree(
                src, dst, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
            )
        else:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)

    # src/ modules must import as top level: a zip tier has no src/ on its
    # PYTHONPATH, unlike the container image, which sets one.
    staged_src = os.path.join(build_dir, "src")
    if os.path.isdir(staged_src):
        for name in os.listdir(staged_src):
            if name.endswith(".py"):
                shutil.copy2(os.path.join(staged_src, name), build_dir)

    total = prune_for_lambda(build_dir)
    print(f"  unzipped: {total / 1e6:.0f} MB / {ZIP_UNZIPPED_LIMIT_BYTES / 1e6:.0f} MB")
    if total > ZIP_UNZIPPED_LIMIT_BYTES:
        sys.exit(
            f"ERROR: {tier} is {total / 1e6:.0f} MB unzipped, over Lambda's "
            f"{ZIP_UNZIPPED_LIMIT_BYTES / 1e6:.0f} MB limit. Prune the tier's "
            f"requirements in mcp_server/packaging.py."
        )

    shutil.make_archive(zip_path[:-4], "zip", build_dir)
    return os.path.getsize(zip_path)


def _upload(s3, bucket, key, path):
    s3.upload_file(path, bucket, key)
    return {"S3Bucket": bucket, "S3Key": key}


def _ensure_cognito_client(cog, pool_id, base_url, region):
    """A Cognito domain and an app client, both required by the OAuth flow.

    The domain is what serves /authorize and /token; without it the
    discovery document points at endpoints that do not resolve. The client
    is created by dynamic registration at run time for real callers -- this
    one exists so the pool has a domain and so the flow can be exercised
    before any client has registered.
    """
    domain = f"pclustermaker-mcp-{pool_id.split('_')[-1].lower()}"
    try:
        cog.create_user_pool_domain(Domain=domain, UserPoolId=pool_id)
        print(f"  cognito domain {domain}")
    except Exception as e:
        if type(e).__name__ not in ("InvalidParameterException", "ResourceConflictException"):
            raise
        print(f"  cognito domain {domain} (exists)")
    return domain


def _container_tier_tools():
    """The tools only the container tier serves, derived from the routing
    table rather than restated.

    It was written out in three places and went stale the moment a fourth
    tool joined the tier -- the same multi-surface drift this repo pins by
    test everywhere else, except a help string has no test to fail.
    """
    from mcp_server.tiers import TOOL_TIERS

    return sorted(t for t, v in TOOL_TIERS.items() if v == "stack-mutation-node")


def _container_tier_tools_phrase():
    names = _container_tier_tools()
    return ", ".join(names[:-1]) + " and " + names[-1]


def normalize_bootstrap(args, fail):
    """Resolve `--bootstrap` into the flags main() already acts on.

    It is deliberately a *spelling* of `--setup-infra --setup-gateway` plus
    the default tier list rather than a fourth code path: a second
    deployment sequence is a second thing to keep in step with the first,
    and the ordering (IAM and pool, then functions, then the gateway that
    routes to them) is already correct in main().

    Module level, not inline, for the reason `tiers_to_deploy` is: inside
    main() this sits past `sts.get_caller_identity()` and no test the no-AWS
    guard allows can reach it. `fail` is `ArgumentParser.error` in
    production -- passed in so a test can observe the rejection without
    catching SystemExit and guessing which argument caused it.
    """
    if args.bootstrap:
        if args.teardown:
            fail("--bootstrap and --teardown are opposites; pick one")
        args.setup_infra = True
        args.setup_gateway = True
    if args.create_user and args.teardown:
        fail("--create-user creates a user in a pool --teardown deletes")
    return args


def tiers_to_deploy(args):
    """Which tiers a run should build, given the flags.

    An infrastructure flag on its own is not a deployment: rebuilding six
    146 MB artifacts to attach a route, or to create a role, is minutes of
    pip for nothing. `--setup-gateway` had this short-circuit;
    `--setup-infra` did not, and so silently redeployed every zip tier as a
    side effect of creating IAM. An explicit `--tier` always wins, so the
    two can still be combined deliberately.

    `--teardown` returns nothing unconditionally, `--tier` included:
    building an artifact in order to delete the function it would have
    been deployed to is minutes of pip for a thing about to not exist.

    `--bootstrap` is the one case that wants both an infrastructure flag
    and the default tier list, so it is checked before that short-circuit.
    Without it the short-circuit fires on the implied `--setup-infra` and
    the run builds a gateway routing to functions that do not exist -- the
    exact half-deployment the flag exists to prevent.
    """
    if args.teardown:
        return []
    zips = [t for t, s in TIER_PACKAGES.items() if s["kind"] != "image"]
    if args.bootstrap and not args.tier:
        return zips
    if (args.setup_gateway or args.setup_infra) and not args.tier:
        return []
    return args.tier or zips


def main():
    p = argparse.ArgumentParser(
        description="Build and deploy the MCP remote transport's Lambda tiers.",
    )
    p.add_argument(
        "--tier",
        "-T",
        action="append",
        choices=sorted(TIER_PACKAGES),
        help="tier to deploy (repeatable; default: every zip tier)",
    )
    p.add_argument("--region", "-R", default=os.environ.get("AWS_REGION", "us-east-1"))
    p.add_argument(
        "--image-uri",
        help="use an already-built image for the container tier "
        "instead of building one. Without it, deploying "
        "stack-mutation-node creates the ECR repository, "
        "builds the image and pushes it",
    )
    p.add_argument(
        "--runtime",
        choices=CONTAINER_RUNTIMES,
        help="container runtime to build with (default: the "
        "first of %s found on PATH)" % ", ".join(CONTAINER_RUNTIMES),
    )
    p.add_argument(
        "--dry-run", action="store_true", help="build and report sizes; upload and deploy nothing"
    )
    p.add_argument(
        "--setup-gateway",
        action="store_true",
        help="create the REST API, its Lambda authorizer and its "
        "routes, and the Cognito app client and domain the "
        "OAuth flow needs; idempotent. This is what makes "
        "the transport reachable from a browser.",
    )
    p.add_argument(
        "--setup-infra",
        action="store_true",
        help="create the IAM roles and policies (and the Cognito "
        "user pool if absent) before deploying; idempotent. "
        "Reports any policy whose deployed document no longer "
        "matches templates/, but does not change it",
    )
    p.add_argument(
        "--teardown",
        action="store_true",
        help="remove the deployed transport: REST API, the seven "
        "Lambda functions, then the IAM roles and policies, "
        "then the Cognito user pool. Combine with --dry-run "
        "to list what would go without removing it. The "
        "permissions boundary is deliberately left behind",
    )
    p.add_argument(
        "--bootstrap",
        action="store_true",
        help="stand the whole transport up in one run: the IAM "
        "and Cognito pool, every zip tier, then the REST "
        "API and its OAuth front end. Equivalent to "
        "--setup-infra --setup-gateway with every zip tier "
        "named explicitly, in that order. Idempotent, so it "
        "is also the update path. The container tier is not "
        "included -- it needs --image-uri and a runtime; "
        "without it the transport still serves every tool "
        "except those the container tier owns",
    )
    p.add_argument(
        "--create-user",
        metavar="USERNAME",
        help="create a Cognito user to sign in as at the Hosted "
        "UI, with a permanent password; idempotent. Takes "
        "the password from MCP_USER_PASSWORD if set, "
        "otherwise generates one and prints it once. "
        "Nothing else creates a user, so without this a "
        "freshly deployed transport has nobody to "
        "authenticate",
    )
    p.add_argument(
        "--update-policies",
        action="store_true",
        help="with --setup-infra, push a changed policy document as "
        "a new default version instead of only reporting it. "
        "Needs iam:CreatePolicyVersion and "
        "iam:DeletePolicyVersion",
    )
    args = p.parse_args()

    normalize_bootstrap(args, p.error)

    import boto3

    sts = boto3.client("sts")
    account = sts.get_caller_identity()["Account"]
    bucket = _derive_locks_bucket(aws_account_id=account, region=args.region)

    tiers = tiers_to_deploy(args)
    print(f"account {account}  region {args.region}  bucket {bucket}")
    if not args.teardown:
        print(f"deploying: {', '.join(tiers) if tiers else '(no tiers)'}\n")

    lam = boto3.client("lambda", region_name=args.region)
    s3 = boto3.client("s3", region_name=args.region)
    endpoint = None
    connector_client_id = None

    if args.teardown:
        # Gateway first: it is the internet-facing surface, and removing it
        # stops anything arriving while the rest is half torn down. Then the
        # functions, then the IAM they run under, then the pool that
        # authenticates callers -- each step leaving nothing that depends on
        # something already gone.
        apigw = boto3.client("apigateway", region_name=args.region)
        cog = boto3.client("cognito-idp", region_name=args.region)
        lam = boto3.client("lambda", region_name=args.region)
        iam = boto3.client("iam")
        pool_prefix = _derive_mcp_user_pool_name(aws_account_id=account, region=args.region)

        if args.dry_run:
            print("would remove (dry run -- nothing deleted):")
            for api in apigw.get_rest_apis().get("items", []):
                if api["name"].startswith("pclustermaker-mcp"):
                    print(f"  REST API   {api['name']} ({api['id']})")
            for fn in FUNCTION_NAMES.values():
                try:
                    lam.get_function(FunctionName=fn)
                    print(f"  function   {fn}")
                except Exception:
                    pass
            for pool in cog.list_user_pools(MaxResults=60)["UserPools"]:
                if pool["Name"].startswith(pool_prefix):
                    print(f"  user pool  {pool['Name']} ({pool['Id']})")
            try:
                boto3.client("ecr", region_name=args.region).describe_repositories(
                    repositoryNames=[IMAGE_REPOSITORY]
                )
                print(f"  ECR repo   {IMAGE_REPOSITORY}")
            except Exception:
                pass
            print("  plus the MCP IAM roles and policies")
            print(f"\nleft in place: {_mcp_boundary_name()}")
            return 0

        print("tearing down the MCP remote transport\n")
        delete_gateway(apigw)
        delete_mcp_functions(lam)
        # After the functions, which reference the image, and before IAM,
        # which is what grants the delete. A repository this deploy created
        # always holds an image, so this needs force=True.
        if delete_ecr_repository(boto3.client("ecr", region_name=args.region)):
            print(f"  Deleted ECR repository: {IMAGE_REPOSITORY}")
        delete_slurm_document(boto3.client("ssm", region_name=args.region))
        result = _delete_mcp_infra(iam, aws_account_id=account, verbose=True)
        delete_cognito_pool(cog, pool_name_prefix=pool_prefix)

        # Durable by design, and MCPDeployPolicy denies deleting it -- a
        # deployer who can remove their own boundary does not have one. Say
        # so rather than attempting it and reporting the denial as a
        # teardown failure.
        print(
            f"\nleft in place: {_mcp_boundary_name()} (permissions boundary, "
            f"durable by design -- remove by hand if the account should be "
            f"empty)"
        )
        if result.failed:
            print(f"\n*** {len(result.failed)} IAM step(s) FAILED ***")
            return 1
        return 0

    # Before the first mutation, not after: a deploy that gets six tiers in
    # and then cannot create the gateway leaves a half-built transport whose
    # cause -- a missing policy on the operator's own identity -- is not
    # what the AccessDenied appears to be about.
    if not args.dry_run and (args.setup_infra or args.setup_gateway or tiers):
        missing = preflight_deploy_permissions(
            boto3.client("iam"),
            caller_arn=sts.get_caller_identity()["Arn"],
            aws_account_id=account,
            region=args.region,
        )
        if missing:
            sys.exit(
                "ERROR: this identity is missing permissions the deploy "
                "needs:\n"
                + "".join(f"  {a}\n" for a in missing)
                + "\n  MCPDeployPolicy carries them, and the operator policy "
                "does not\n"
                "  (the two together exceed IAM's 6,144-byte limit). Create "
                "and attach it:\n"
                "    ./generate_operator_policy.py --mcp --create\n\n"
                "  deploy_mcp.py cannot create it itself: a deploy tool able "
                "to grant\n"
                "  itself permissions has no ceiling."
            )
        if missing is None:
            print("  (could not verify deploy permissions -- continuing)\n")

    if args.setup_infra and not args.dry_run:
        # The pool name is derived, never chosen: it is one account+region
        # resource that outlives every cluster, and the hand-made one was
        # named after a cluster that no longer exists.
        cog = boto3.client("cognito-idp", region_name=args.region)
        want = _derive_mcp_user_pool_name(aws_account_id=account, region=args.region)
        pool_id = next(
            (p["Id"] for p in cog.list_user_pools(MaxResults=60)["UserPools"] if p["Name"] == want),
            None,
        )
        if pool_id is None:
            pool_id = cog.create_user_pool(PoolName=want)["UserPool"]["Id"]
            print(f"created Cognito user pool {want} ({pool_id})")
        else:
            print(f"reusing Cognito user pool {want} ({pool_id})")
        _setup_mcp_infra(
            boto3.client("iam"),
            aws_account_id=account,
            region=args.region,
            mcp_user_pool_id=pool_id,
            update_policies=args.update_policies,
        )
        # The document is the read-only Slurm tool's security boundary:
        # SSM enforces its allowedValues/allowedPattern before dispatch,
        # so it bounds the tier's role rather than merely our code.
        ensure_slurm_document(boto3.client("ssm", region_name=args.region))
        print()

    for tier in tiers:
        spec = TIER_PACKAGES[tier]
        print(f"[{tier}] -> {FUNCTION_NAMES[tier]}")

        if spec["kind"] == "image":
            # An explicit --image-uri is used exactly as given: someone who
            # built elsewhere (CI, another architecture, a mirror) has
            # already answered the question this branch exists to answer.
            if args.image_uri:
                code = {"ImageUri": args.image_uri}
            else:
                runtime = args.runtime or detect_container_runtime()
                if runtime is None:
                    sys.exit(
                        f"ERROR: {tier} ships as a container image and no "
                        f"container runtime\n"
                        f"  was found on PATH (looked for: "
                        f"{', '.join(CONTAINER_RUNTIMES)}).\n\n"
                        f'  Install one (see "Adding cluster creation" in '
                        f"INSTALL.md), or pass\n"
                        f"  --image-uri for an image built elsewhere.\n\n"
                        f"  Without this tier the transport still serves every "
                        f"tool except\n"
                        f"  {_container_tier_tools_phrase()}."
                    )
                ecr = boto3.client("ecr", region_name=args.region)
                uri, created = ensure_ecr_repository(ecr)
                print(f"  ECR repository {'created' if created else 'exists'}: {uri}")
                # Before the push, not after: the push is the slow part, and
                # failing this afterwards means a 300 MB upload is thrown away
                # on a permission the deploy could have checked in a second.
                ensure_lambda_can_pull(ecr, aws_account_id=account, region=args.region)
                print("  repository policy: lambda.amazonaws.com may pull")
                registry = ecr_login(ecr, runtime)
                print(f"  {runtime} logged in to {registry}")
                image_uri = f"{uri}:latest"
                print(
                    f"  building with {runtime} (--platform "
                    f"{IMAGE_PLATFORM}); this takes a few minutes..."
                )
                try:
                    build_and_push_image(
                        runtime,
                        image_uri=image_uri,
                        repo_root=_repo_root,
                        dockerfile=os.path.join(_repo_root, "mcp_server", f"Dockerfile.{tier}"),
                    )
                except ImageBuildError as e:
                    sys.exit(f"ERROR: {e}")
                print(f"  pushed {image_uri}")
                code = {"ImageUri": image_uri}
        else:
            with tempfile.TemporaryDirectory() as build_dir:
                zip_path = os.path.join(tempfile.gettempdir(), f"{tier}.zip")
                size = _build_zip(tier, build_dir, zip_path)
                print(f"  zip: {size / 1e6:.0f} MB", end="")
                if size > _DIRECT_UPLOAD_LIMIT:
                    print(
                        f" (over the {_DIRECT_UPLOAD_LIMIT / 1e6:.0f} MB "
                        f"direct-upload limit, so via S3)"
                    )
                else:
                    print()
                if args.dry_run:
                    print("  dry run: not uploaded, not deployed\n")
                    continue
                key = f"{_S3_PREFIX}/{tier}.zip"
                code = _upload(s3, bucket, key, zip_path)
                print(f"  uploaded s3://{bucket}/{key}")

        if args.dry_run:
            print("  dry run: not deployed\n")
            continue

        arn = deploy_tier(lam, tier, aws_account_id=account, code=code)
        print(f"  {arn}\n")

    if args.setup_gateway and not args.dry_run:
        cog = boto3.client("cognito-idp", region_name=args.region)
        want = _derive_mcp_user_pool_name(aws_account_id=account, region=args.region)
        pool_id = next(
            (p["Id"] for p in cog.list_user_pools(MaxResults=60)["UserPools"] if p["Name"] == want),
            None,
        )
        if pool_id is None:
            sys.exit(
                f"ERROR: no Cognito user pool named {want!r}.\n"
                f"  Run --setup-infra first; it creates the pool the "
                f"authorizer validates against."
            )
        print(f"\nGateway (pool {pool_id}):")
        domain = _ensure_cognito_client(cog, pool_id, None, args.region)
        info = setup_gateway(
            account=account,
            region=args.region,
            user_pool_id=pool_id,
            cognito_domain=domain,
        )
        print(f"\n  MCP endpoint: {info['base_url']}/mcp")
        print(f"  Discovery:    {info['base_url']}/.well-known/oauth-protected-resource")
        endpoint = f"{info['base_url']}/mcp"
        connector_client_id = info.get("connector_client_id")
        if connector_client_id:
            print(f"  OAuth client: {connector_client_id}")

    if args.create_user and not args.dry_run:
        cog = boto3.client("cognito-idp", region_name=args.region)
        want = _derive_mcp_user_pool_name(aws_account_id=account, region=args.region)
        pool_id = next(
            (p["Id"] for p in cog.list_user_pools(MaxResults=60)["UserPools"] if p["Name"] == want),
            None,
        )
        if pool_id is None:
            sys.exit(
                f"ERROR: no Cognito user pool named {want!r}.\n"
                f"  Run --setup-infra (or --bootstrap) first; it creates the "
                f"pool the user lives in."
            )
        # An explicit password is the operator's own; a generated one is
        # printed because it is the only time it can be. Cognito stores a
        # hash, so a lost generated password is re-set by re-running this,
        # never recovered.
        supplied = os.environ.get("MCP_USER_PASSWORD")
        password = supplied or generate_user_password()
        created = ensure_cognito_user(
            cog, pool_id=pool_id, username=args.create_user, password=password
        )
        verb = "created" if created else "already existed; password reset"
        print(f"\n  Cognito user: {args.create_user} ({verb})")
        if supplied:
            print("  Password:     from MCP_USER_PASSWORD (not shown)")
        else:
            print(f"  Password:     {password}")
            print("  ^ generated, shown once, not recoverable -- save it now")

    if args.bootstrap and not args.dry_run:
        print("\nThe transport is up. To connect it from claude.ai:")
        print("  1. Customize -> Connectors, then the '+' beside Connectors")
        print("     (Team/Enterprise: an Owner adds it under Organization")
        print("      settings -> Connectors -> Add -> Custom -> Web)")
        print(f"  2. Paste this URL: {endpoint}")
        if connector_client_id:
            print("  3. Open the advanced settings and set")
            print(f"       OAuth Client ID:     {connector_client_id}")
            print("       OAuth Client Secret: leave empty")
            print("     Cognito cannot register a client on demand, so this")
            print("     ID has to be supplied by hand; the secret is empty")
            print("     because the connector is a public PKCE client.")
        else:
            print("  3. Set the advanced OAuth Client ID to a Cognito app")
            print("     client for this pool; leave the secret empty")
        print("  4. Sign in at the Cognito Hosted UI when prompted")
        if not args.create_user:
            print("\nNo Cognito user exists to sign in as. Create one with")
            print("  deploy_mcp.py --create-user <username>")
        if "stack-mutation-node" not in tiers:
            _missing = _container_tier_tools()
            print(f"\n{len(_missing)} tools are NOT in this deployment:")
            print("  " + ", ".join(_missing))
            print("  -- i.e. you can inspect and operate clusters from the browser, but not")
            print("  create or modify them. They live in the stack-mutation-node tier, which")
            print("  ships as a container image. To add it:")
            print("    ./deploy_mcp.py --tier stack-mutation-node")
            found = detect_container_runtime()
            print(
                f"  ({
                    'using ' + found
                    if found
                    else 'needs a container runtime on PATH -- see INSTALL.md'
                })"
            )

    print("Done.")


if __name__ == "__main__":
    main()
