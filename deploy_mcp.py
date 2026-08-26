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
    FUNCTION_NAMES,
    deploy_tier,
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
    _derive_locks_bucket,
    _derive_mcp_user_pool_name,
    _setup_mcp_infra,
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
_PIP_PLATFORM = ["--platform", "manylinux2014_x86_64", "--implementation", "cp",
                 "--python-version", "3.12", "--only-binary=:all:"]


def _run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def _build_zip(tier, build_dir, zip_path):
    """pip install --target, stage this repo's sources, prune, zip."""
    req = os.path.join(build_dir, "requirements.txt")
    with open(req, "w") as fh:
        fh.write(render_requirements_file(tier))

    print(f"  installing dependencies for {tier}...")
    _run([os.path.join(_repo_root, ".venv", "bin", "pip"), "install", "--quiet",
          "--target", build_dir, *_PIP_PLATFORM, "--upgrade", "-r", req],
         stdout=subprocess.DEVNULL)

    for entry in sources_for(tier):
        src = os.path.join(_repo_root, entry)
        dst = os.path.join(build_dir, entry)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
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
        if type(e).__name__ not in ("InvalidParameterException",
                                    "ResourceConflictException"):
            raise
        print(f"  cognito domain {domain} (exists)")
    return domain


def main():
    p = argparse.ArgumentParser(
        description="Build and deploy the MCP remote transport's Lambda tiers.",
    )
    p.add_argument("--tier", "-T", action="append", choices=sorted(TIER_PACKAGES),
                   help="tier to deploy (repeatable; default: every zip tier)")
    p.add_argument("--region", "-R", default=os.environ.get("AWS_REGION", "us-east-1"))
    p.add_argument("--image-uri",
                   help="ECR image URI for the container tier; required to "
                        "deploy stack-mutation-node, which cannot be a zip "
                        "(pcluster's create/update need Node.js on PATH)")
    p.add_argument("--dry-run", action="store_true",
                   help="build and report sizes; upload and deploy nothing")
    p.add_argument("--setup-gateway", action="store_true",
                   help="create the HTTP API, its Lambda authorizer and its "
                        "routes, and the Cognito app client and domain the "
                        "OAuth flow needs; idempotent. This is what makes "
                        "the transport reachable from a browser.")
    p.add_argument("--setup-infra", action="store_true",
                   help="create the IAM roles and policies (and the Cognito "
                        "user pool if absent) before deploying; idempotent")
    args = p.parse_args()

    import boto3

    sts = boto3.client("sts")
    account = sts.get_caller_identity()["Account"]
    bucket = _derive_locks_bucket(aws_account_id=account, region=args.region)

    # --setup-gateway on its own is an infrastructure change, not a
    # deployment: rebuilding six 146 MB artifacts to attach a route would be
    # minutes of pip for nothing.
    gateway_only = args.setup_gateway and not args.tier and not args.setup_infra
    tiers = [] if gateway_only else (
        args.tier or [t for t, s in TIER_PACKAGES.items() if s["kind"] != "image"]
    )
    print(f"account {account}  region {args.region}  bucket {bucket}")
    print(f"deploying: {', '.join(tiers) if tiers else '(no tiers)'}\n")

    lam = boto3.client("lambda", region_name=args.region)
    s3 = boto3.client("s3", region_name=args.region)

    if args.setup_infra and not args.dry_run:
        # The pool name is derived, never chosen: it is one account+region
        # resource that outlives every cluster, and the hand-made one was
        # named after a cluster that no longer exists.
        cog = boto3.client("cognito-idp", region_name=args.region)
        want = _derive_mcp_user_pool_name(aws_account_id=account, region=args.region)
        pool_id = next(
            (p["Id"] for p in cog.list_user_pools(MaxResults=60)["UserPools"]
             if p["Name"] == want),
            None,
        )
        if pool_id is None:
            pool_id = cog.create_user_pool(PoolName=want)["UserPool"]["Id"]
            print(f"created Cognito user pool {want} ({pool_id})")
        else:
            print(f"reusing Cognito user pool {want} ({pool_id})")
        _setup_mcp_infra(
            boto3.client("iam"), aws_account_id=account, region=args.region,
            mcp_user_pool_id=pool_id,
        )
        print()

    for tier in tiers:
        spec = TIER_PACKAGES[tier]
        print(f"[{tier}] -> {FUNCTION_NAMES[tier]}")

        if spec["kind"] == "image":
            if not args.image_uri:
                sys.exit(
                    f"ERROR: {tier} is a container image; pass --image-uri.\n"
                    f"  Build it first (see INSTALL.md for the runtime, the\n"
                    f"  --platform trap, and the ECR steps):\n"
                    f"    finch build --platform linux/amd64 \\\n"
                    f"      -f mcp_server/Dockerfile.{tier} -t {tier}:latest ."
                )
            code = {"ImageUri": args.image_uri}
        else:
            with tempfile.TemporaryDirectory() as build_dir:
                zip_path = os.path.join(tempfile.gettempdir(), f"{tier}.zip")
                size = _build_zip(tier, build_dir, zip_path)
                print(f"  zip: {size / 1e6:.0f} MB", end="")
                if size > _DIRECT_UPLOAD_LIMIT:
                    print(f" (over the {_DIRECT_UPLOAD_LIMIT / 1e6:.0f} MB "
                          f"direct-upload limit, so via S3)")
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
            (p["Id"] for p in cog.list_user_pools(MaxResults=60)["UserPools"]
             if p["Name"] == want),
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
            account=account, region=args.region, user_pool_id=pool_id,
            cognito_domain=domain,
        )
        print(f"\n  MCP endpoint: {info['base_url']}/mcp")
        print(f"  Discovery:    {info['base_url']}"
              f"/.well-known/oauth-protected-resource")

    print("Done.")


if __name__ == "__main__":
    main()
