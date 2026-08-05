#!/usr/bin/env python
#
################################################################################
# Name:         generate_operator_policy.py
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Purpose:      Generate the IAM operator policy for ParallelClusterMaker
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
import json

import boto3
from botocore.exceptions import BotoCoreError, ClientError

_TEMPLATE = os.path.join(_repo_root, "templates", "OperatorPolicy.json_src")
_POLICY_NAME = "parallelcluster-operator-pclustermaker"


def _get_account_id():
    try:
        return boto3.client("sts").get_caller_identity()["Account"]
    except (ClientError, BotoCoreError) as e:
        sys.exit(f"ERROR: could not resolve AWS account ID: {e}")


def _render(account_id):
    try:
        with open(_TEMPLATE) as fh:
            raw = fh.read()
    except OSError as e:
        sys.exit(f"ERROR: could not read policy template {_TEMPLATE}: {e}")
    return raw.replace("<AWS_ACCOUNT_ID>", account_id)


def _create_policy(iam, rendered, policy_name, description):
    try:
        resp = iam.create_policy(
            PolicyName=policy_name,
            PolicyDocument=rendered,
            Description=description,
        )
        return resp["Policy"]["Arn"]
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "EntityAlreadyExists":
            sys.exit(
                f"ERROR: policy '{policy_name}' already exists.\n"
                f"  Delete it first, or omit --create to just print the JSON."
            )
        if code in ("AccessDenied", "UnauthorizedAccess"):
            sys.exit(
                f"ERROR: insufficient permissions to create IAM policy.\n"
                f"  Your identity needs iam:CreatePolicy on "
                f"arn:aws:iam::*:policy/{policy_name}."
            )
        sys.exit(f"ERROR: {e}")
    except BotoCoreError as e:
        sys.exit(f"ERROR: {e}")


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Render the ParallelClusterMaker operator IAM policy and optionally "
            "create it in AWS IAM. This policy covers actions required by the "
            "toolkit itself (keypair management, Secrets Manager, SSM, Cost "
            "Explorer, Pricing). It does NOT include AWS ParallelCluster's own "
            "required permissions — see the PCluster IAM docs for those."
        )
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        metavar="FILE",
        help="Write rendered JSON to FILE instead of stdout",
    )
    parser.add_argument(
        "--create",
        action="store_true",
        help=f"Create the managed policy in IAM as '{_POLICY_NAME}'",
    )
    parser.add_argument(
        "--policy-name",
        default=_POLICY_NAME,
        metavar="NAME",
        help=f"Override the policy name (default: {_POLICY_NAME})",
    )
    parser.add_argument(
        "--description",
        default="ParallelClusterMaker operator policy — toolkit-level permissions",
        help="Policy description (used only with --create)",
    )
    args = parser.parse_args()

    account_id = _get_account_id()
    rendered = _render(account_id)

    # Validate JSON before printing or creating.
    try:
        json.loads(rendered)
    except json.JSONDecodeError as e:
        sys.exit(f"ERROR: rendered policy is not valid JSON: {e}")

    # The IAM call comes before the file write: writing first left a rendered
    # policy file on disk after a failed create, which reads as success.
    arn = None
    if args.create:
        iam = boto3.client("iam")
        arn = _create_policy(iam, rendered, args.policy_name, args.description)

    if args.output:
        try:
            with open(args.output, "w") as fh:
                fh.write(rendered)
            print(f"Policy written to: {args.output}")
        except OSError as e:
            sys.exit(f"ERROR: could not write {args.output}: {e}")
    else:
        print(rendered)

    if arn:
        print(f"\nCreated managed policy: {arn}")
        print(
            f"\nAttach it to your IAM user or role:\n"
            f"  aws iam attach-user-policy --user-name <USERNAME> --policy-arn {arn}\n"
            f"  aws iam attach-role-policy --role-name <ROLENAME> --policy-arn {arn}"
        )


if __name__ == "__main__":
    main()
