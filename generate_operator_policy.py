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

_MCP_TEMPLATE = os.path.join(_repo_root, "templates", "MCPDeployPolicy.json_src")
_MCP_POLICY_NAME = "parallelcluster-mcp-deploy-pclustermaker"

# Two permission sets, deliberately not one. OperatorPolicy scopes its IAM
# to pclustermaker-policy-* and pclustermaker-role-*, which match no MCP
# resource name, and the MCP grants appended to it measure 6,358 bytes
# against IAM's 6,144-byte managed policy limit -- so merging them does not
# merely read badly, it does not fit.
#
# The MCP name is NOT of the form pclustermaker-mcp-policy-*: that is
# _mcp_policy_name()'s namespace for the seven handler policies, and this
# is the deployer's own policy, which must outlive `deploy_mcp.py
# --teardown` (you need it to run the teardown). Teardown enumerates
# _mcp_policy_templates() rather than sweeping a prefix, so the two do not
# collide today either way -- but a name that reads like a handler policy
# invites a future sweep to take it.
_MODES = {
    "operator": (
        _TEMPLATE,
        _POLICY_NAME,
        "ParallelClusterMaker operator policy — toolkit-level permissions",
    ),
    "mcp": (
        _MCP_TEMPLATE,
        _MCP_POLICY_NAME,
        "ParallelClusterMaker MCP remote transport deployment permissions",
    ),
}


def _get_account_id():
    try:
        return boto3.client("sts").get_caller_identity()["Account"]
    except (ClientError, BotoCoreError) as e:
        sys.exit(f"ERROR: could not resolve AWS account ID: {e}")


def _render(account_id, template=_TEMPLATE):
    try:
        with open(template) as fh:
            raw = fh.read()
    except OSError as e:
        sys.exit(f"ERROR: could not read policy template {template}: {e}")
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


def _run_bootstrap(rendered, policy_name, description, *, attach, dry_run):
    """Report a bootstrap in the terms an operator can act on.

    The decision logic is `bootstrap_operator_policy` in `pcluster_core`,
    where it is testable without AWS credentials; this is presentation only.
    """
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
    from pcluster_core import bootstrap_operator_policy

    r = bootstrap_operator_policy(
        boto3.client("iam"),
        boto3.client("sts"),
        policy_name=policy_name,
        rendered=rendered,
        description=description,
        attach=attach,
        dry_run=dry_run,
    )

    lead = "Would " if r.dry_run else ""
    said = {
        "created": f"{lead or 'C'}{'reate' if r.dry_run else 'reated'} policy",
        "updated": f"{lead or 'P'}{'ush' if r.dry_run else 'ushed'} a new default version of",
        "current": "Policy already matches this checkout:",
    }[r.policy_action]
    print(
        f"\n{said} {r.policy_arn}"
        + (f" ({r.version_id})" if r.version_id and r.policy_action != "current" else "")
    )

    print(f"Running as: {r.identity_arn}")
    if r.attach_action == "attached":
        verb = "Would attach" if r.dry_run else "Attached"
        print(f"{verb} it to {r.principal_type} {r.principal_name}")
    elif r.attach_action == "already":
        print(f"Already attached to {r.principal_type} {r.principal_name}")
    elif r.attach_action == "skipped":
        print(
            f"Not attached ({r.reason}). Attach it with:\n"
            f"  aws iam attach-user-policy --user-name <USERNAME> "
            f"--policy-arn {r.policy_arn}"
        )
    else:
        sys.exit(
            f"\nERROR: the policy is in place but could not be attached.\n"
            f"  {r.reason}\n\n"
            f"Attach it by hand once you have an attachable identity:\n"
            f"  aws iam attach-user-policy --user-name <USERNAME> "
            f"--policy-arn {r.policy_arn}\n"
        )

    if not r.dry_run and r.ok:
        print("\nThis account is ready to build clusters.")


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Render a ParallelClusterMaker IAM policy and optionally create it "
            "in AWS IAM. By default this is the operator policy, covering "
            "actions the toolkit itself needs (keypair management, Secrets "
            "Manager, SSM, Cost Explorer, Pricing). It does NOT include AWS "
            "ParallelCluster's own required permissions — see the PCluster IAM "
            "docs for those. Pass --mcp for the separate permission set that "
            "deploying the MCP remote transport needs."
        )
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        metavar="FILE",
        help="Write rendered JSON to FILE instead of stdout",
    )
    parser.add_argument(
        "--mcp",
        action="store_true",
        help=(
            "Render the MCP remote transport deployment policy "
            f"('{_MCP_POLICY_NAME}') instead of the operator policy. The "
            "operator policy does not cover deploying the transport and "
            "cannot be widened to: the combined grant set exceeds IAM's "
            "6,144-byte managed policy limit"
        ),
    )
    parser.add_argument(
        "--create",
        action="store_true",
        help="Create the managed policy in IAM",
    )
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help=(
            "One-command setup for a fresh clone: create the policy if it is "
            "absent, push a new default version if the account's copy differs "
            "from what this repo renders, then attach it to the identity you "
            "are running as. Idempotent -- safe to re-run after pulling a "
            "change that adds a grant"
        ),
    )
    parser.add_argument(
        "--no-attach",
        action="store_true",
        help="With --bootstrap, converge the policy but do not attach it",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --bootstrap, report what would change without changing it",
    )
    parser.add_argument(
        "--policy-name",
        default=None,
        metavar="NAME",
        help=f"Override the policy name (default: {_POLICY_NAME}, "
        f"or {_MCP_POLICY_NAME} with --mcp)",
    )
    parser.add_argument(
        "--description",
        default=None,
        help="Policy description (used only with --create)",
    )
    args = parser.parse_args()

    # Resolved after parsing, not as argparse defaults: a default bound to
    # the operator policy cannot be told apart from the operator name passed
    # explicitly, so --mcp would silently render the MCP document under the
    # operator policy's name.
    template, default_name, default_description = _MODES["mcp" if args.mcp else "operator"]
    policy_name = args.policy_name or default_name
    description = args.description or default_description

    account_id = _get_account_id()
    rendered = _render(account_id, template)

    # Validate JSON before printing or creating.
    try:
        json.loads(rendered)
    except json.JSONDecodeError as e:
        sys.exit(f"ERROR: rendered policy is not valid JSON: {e}")

    # The IAM call comes before the file write: writing first left a rendered
    # policy file on disk after a failed create, which reads as success.
    if args.bootstrap and args.create:
        sys.exit(
            "ERROR: pass --bootstrap or --create, not both. --bootstrap is "
            "the superset: it creates the policy when absent and converges "
            "it when present."
        )

    arn = None
    if args.bootstrap:
        _run_bootstrap(
            rendered,
            policy_name,
            description,
            attach=not args.no_attach,
            dry_run=args.dry_run,
        )
    elif args.create:
        iam = boto3.client("iam")
        arn = _create_policy(iam, rendered, policy_name, description)

    if args.output:
        try:
            with open(args.output, "w") as fh:
                fh.write(rendered)
            print(f"Policy written to: {args.output}")
        except OSError as e:
            sys.exit(f"ERROR: could not write {args.output}: {e}")
    elif not args.bootstrap:
        # --bootstrap is a setup command whose result is the summary above.
        # Dumping the document to stdout as well buries that under ~200
        # lines of JSON, which is what the documented one-liner would then
        # look like. Ask for the document explicitly with -o, or with no
        # flags at all.
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
