"""Deployment packaging for the MCP Lambda topology.

Builds one deployment artifact per tier. Nothing here talks to AWS -- it
stages files and emits a manifest; uploading is a separate step, the same
split `_setup_mcp_infra` uses (IAM only, no function creation).

Two facts from measuring the real import graph, not from estimating:

  * The **router imports no third-party package at all** -- stdlib plus
    `mcp_server.tiers`. Its artifact is a few KB. That is the concrete
    payoff of keeping `pcluster_core` out of it, and it is why the router
    can be a zip with an empty requirement set rather than sharing the
    handlers' 77 MB.
  * `aws_cdk` (80 MB unpruned) does **not** appear when `pcluster_core`
    is imported -- PCluster imports it lazily, at synthesis time. That is
    a fact about the *import graph* and it does **not** mean only the
    create/update tier carries it: `aws-parallelcluster` declares 17
    `aws-cdk.*` packages as hard requirements, so pip installs them into
    every tier that installs PCluster at all. Measured on a real
    `pip install --target` of the read-only tier, 2026-08-24: `aws_cdk` is
    present, at 80 MB before pruning and 44 MB after. An earlier version
    of this docstring drew the opposite conclusion and was wrong.

A real artifact must be pruned, and nothing here does it for you.
Measured, same build: `pip install --target` of the read-only tier yields
**241 MB**, which is 9 MB under Lambda's 250 MB unzipped limit. Removing
`__pycache__` directories and `.pyc` files takes it to **139 MB** with the
tier's own sources staged -- 84 MB of the artifact was bytecode for
modules that Lambda recompiles anyway. `prune_for_lambda` below does that
and returns the byte total, so a build can check it against
`ZIP_UNZIPPED_LIMIT_BYTES` rather than discovering the ceiling at
CreateFunction time. The zip of that pruned tree is 55 MB, which is over
the 50 MB direct-upload limit, so a handler tier must be uploaded via S3.

`requirements.txt` is the development and CI set and must never be
installed wholesale into a Lambda artifact: it pulls `ansible` (~408 MB of
collections, for playbooks nothing executes any more) and the
`hpc-benchmark` plotting stack (scipy/pandas/matplotlib/PIL, ~250 MB).
Together those exceed Lambda's 250 MB unzipped limit on their own, for
code no tool calls. The per-tier sets below are deliberately explicit
rather than derived from that file.
"""

import os
import zipfile

# Lambda's own limits, for the checks below.
ZIP_UNZIPPED_LIMIT_BYTES = 250 * 1024 * 1024
IMAGE_LIMIT_BYTES = 10 * 1024 * 1024 * 1024

# Packages that must never reach a Lambda artifact. Named rather than
# inferred: each is here for a reason worth keeping, and an inferred
# exclusion list silently stops excluding when the inference changes.
EXCLUDED_FROM_LAMBDA = {
    "ansible": "template-semantics reference only; ~408 MB of collections",
    "pytest": "test-only",
    "pyflakes": "lint-only",
    "ruff": "lint-only",
    "pytest-asyncio": "test-only",
    "numpy": "hpc-benchmark plotting only",
    "scipy": "hpc-benchmark plotting only",
    "pandas": "hpc-benchmark plotting only",
    "matplotlib": "hpc-benchmark plotting only",
    "seaborn": "hpc-benchmark plotting only",
}

# tier -> (pip requirements, python modules/packages to stage, artifact kind)
#
# The router's empty requirement list is a load-bearing claim, not an
# oversight -- see this module's docstring and
# TestTheRouterPackageIsTiny.
# Must stay byte-identical to requirements.txt's own line, and the pin
# must be EXACT. PCluster refuses to manage a cluster created by a version
# it does not recognize, so an artifact and the operator's venv holding
# different versions means the remote transport builds real clusters the
# local CLI cannot describe or tear down.
#
# A bounded range was the first fix and it does not work. ">=3.15,<3.17"
# is one string on both surfaces, so the agreement test passed -- while
# pip resolved it to 3.16.0 for an artifact built today and the venv still
# held 3.15.1. Reproduced live in R4: every remote tool failed against a
# cluster the local CLI had built, with "the update can be performed only
# with the same ParallelCluster version used to create the cluster".
# Identical range specifiers resolved at different times are not the same
# version; only "==" makes both ends resolve alike. Two surfaces stating
# one version is the pattern this repo pins by test --
# TestBothSurfacesPinTheSamePclusterVersion.
PCLUSTER_REQUIREMENT = "aws-parallelcluster==3.15.1"

TIER_PACKAGES = {
    "router": {
        "requirements": [],
        "sources": ["mcp_server/__init__.py", "mcp_server/router.py",
                    "mcp_server/tiers.py"],
        "handler": "mcp_server.router.lambda_handler",
        "kind": "zip",
    },
    "read-only": {
        "requirements": [PCLUSTER_REQUIREMENT, "boto3", "botocore",
                         "Jinja2", "PyYAML", "ruamel.yaml", "jmespath", "fastmcp"],
        "sources": ["mcp_server/", "src/pcluster_core.py",
                    "src/pcluster_aux_data.py", "templates/"],
        "handler": "mcp_server.handlers.read_only.lambda_handler",
        "kind": "zip",
    },
    "fleet-toggle": {
        "requirements": [PCLUSTER_REQUIREMENT, "boto3", "botocore",
                         "Jinja2", "PyYAML", "ruamel.yaml", "jmespath", "fastmcp"],
        "sources": ["mcp_server/", "src/pcluster_core.py",
                    "src/pcluster_aux_data.py", "templates/"],
        "handler": "mcp_server.handlers.fleet_toggle.lambda_handler",
        "kind": "zip",
    },
    "stack-mutation": {
        "requirements": [PCLUSTER_REQUIREMENT, "boto3", "botocore",
                         "Jinja2", "PyYAML", "ruamel.yaml", "jmespath", "fastmcp"],
        "sources": ["mcp_server/", "src/pcluster_core.py",
                    "src/pcluster_aux_data.py", "templates/"],
        "handler": "mcp_server.handlers.stack_mutation.lambda_handler",
        "kind": "zip",
    },
    "stack-mutation-node": {
        # Same Python requirements as the plain tier. The difference is
        # Node.js, not a package: create_cluster and update_cluster call
        # assert_valid_node_js() on their first line, and a zip artifact
        # cannot supply a Node runtime. Hence a container image.
        "requirements": [PCLUSTER_REQUIREMENT, "boto3", "botocore",
                         "Jinja2", "PyYAML", "ruamel.yaml", "jmespath", "fastmcp"],
        "sources": ["mcp_server/", "src/pcluster_core.py",
                    "src/pcluster_aux_data.py", "templates/", "scripts/"],
        "handler": "mcp_server.handlers.stack_mutation_node.lambda_handler",
        "kind": "image",
    },
    # The two Workstream 6 auth tiers. Neither carries pcluster_core or
    # fastmcp, and that is the point rather than an accident: the
    # authorizer runs on *every* MCP request, so its cold start is on the
    # critical path of the whole server, and /register runs before anything
    # is authenticated at all. Both need boto3 and nothing from the tool
    # surface.
    "register": {
        "requirements": ["boto3", "botocore"],
        "sources": ["mcp_server/__init__.py", "mcp_server/auth/__init__.py",
                    "mcp_server/auth/register_lambda.py",
                    "mcp_server/auth/discovery.py"],
        "handler": "mcp_server.auth.register_lambda.lambda_handler",
        "kind": "zip",
    },
    "authorizer": {
        # PyJWT + cryptography verify the RS256 signature against Cognito's
        # JWKS. PyJWT arrives transitively via `mcp` in the development
        # set, which is not a dependency this tier has -- it is named here
        # explicitly because nothing else in this artifact would pull it.
        "requirements": ["boto3", "botocore", "PyJWT", "cryptography"],
        "sources": ["mcp_server/__init__.py", "mcp_server/auth/__init__.py",
                    "mcp_server/auth/authorizer_lambda.py",
                    "mcp_server/auth/discovery.py"],
        "handler": "mcp_server.auth.authorizer_lambda.lambda_handler",
        "kind": "zip",
    },
}


def requirements_for(tier):
    return list(TIER_PACKAGES[tier]["requirements"])


def sources_for(tier):
    return list(TIER_PACKAGES[tier]["sources"])


def validate_requirements(tier):
    """Refuse a tier whose requirements name an excluded package.

    The failure this prevents is not a crash: an artifact that quietly
    includes `ansible` is 400 MB larger, may exceed the unzipped limit,
    and nothing about the deployed function announces why.
    """
    bad = []
    for req in requirements_for(tier):
        name = req.split(">=")[0].split("==")[0].split("[")[0].strip()
        if name in EXCLUDED_FROM_LAMBDA:
            bad.append(f"{name} ({EXCLUDED_FROM_LAMBDA[name]})")
    if bad:
        raise ValueError(
            f"tier {tier!r} requires packages excluded from Lambda artifacts: "
            + "; ".join(bad)
        )


def prune_for_lambda(build_dir):
    """Remove bytecode from a staged artifact and return its byte size.

    `pip install --target` leaves a `__pycache__` beside every module. On
    the read-only tier that is 84 MB of a 241 MB artifact -- against a
    250 MB limit -- and Lambda recompiles from source regardless, so the
    bytecode buys nothing at runtime. Returning the size rather than just
    pruning is what lets a build compare against
    ZIP_UNZIPPED_LIMIT_BYTES before it uploads anything.

    Deliberately not called from build_source_archive: that stages this
    repo's own files into a directory pip has already populated, and the
    pruning has to happen after *both* steps.
    """
    for dirpath, dirnames, filenames in os.walk(build_dir, topdown=False):
        for name in list(dirnames):
            if name == "__pycache__":
                full = os.path.join(dirpath, name)
                for f in os.listdir(full):
                    os.unlink(os.path.join(full, f))
                os.rmdir(full)
                dirnames.remove(name)
        for name in filenames:
            if name.endswith((".pyc", ".pyo")):
                os.unlink(os.path.join(dirpath, name))

    return sum(
        os.path.getsize(os.path.join(r, f))
        for r, _, fs in os.walk(build_dir) for f in fs
    )


def _iter_source_files(repo_root, sources):
    for entry in sources:
        path = os.path.join(repo_root, entry)
        if os.path.isdir(path):
            for dirpath, dirnames, filenames in os.walk(path):
                dirnames[:] = [d for d in dirnames if d != "__pycache__"]
                for name in filenames:
                    if name.endswith(".pyc"):
                        continue
                    full = os.path.join(dirpath, name)
                    yield full, os.path.relpath(full, repo_root)
        elif os.path.isfile(path):
            yield path, entry


def build_source_archive(tier, repo_root, dest_path):
    """Zip this tier's own source files. Dependencies are installed
    separately (pip --target into the same directory before zipping, for a
    real build); keeping the two apart is what makes this testable without
    a network."""
    validate_requirements(tier)
    staged = []
    with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for full, arcname in sorted(_iter_source_files(repo_root, sources_for(tier))):
            zf.write(full, arcname)
            staged.append(arcname)
    return staged


def render_requirements_file(tier):
    """The pip requirements for one tier, as a requirements.txt body.

    Generated from TIER_PACKAGES rather than maintained as a second file,
    so the Dockerfile's `pip install -r` and this module cannot disagree
    about what a tier needs. A checked-in requirements-lambda.txt that
    drifted from the tier spec would produce an image missing a package
    the handler imports -- discovered at the first invocation.
    """
    validate_requirements(tier)
    header = [
        f"# Generated from mcp_server/packaging.py for the {tier!r} tier.",
        "# Do not edit by hand -- regenerate with:",
        "#   python -c \"import sys; sys.path.insert(0,'.'); "
        "from mcp_server.packaging import render_requirements_file as r; "
        f"print(r('{tier}'), end='')\" > requirements-lambda.txt",
        "#",
        "# Deliberately NOT requirements.txt: that is the development set and",
        "# pulls ansible (~408 MB of collections) plus the plotting stack,",
        "# which together exceed Lambda's 250 MB unzipped limit on their own.",
    ]
    return "\n".join(header + requirements_for(tier)) + "\n"


def manifest(tier):
    """Everything a deployment step needs, without performing one."""
    spec = TIER_PACKAGES[tier]
    return {
        "tier": tier,
        "kind": spec["kind"],
        "handler": spec["handler"],
        "requirements": requirements_for(tier),
        "sources": sources_for(tier),
        "size_limit_bytes": (
            IMAGE_LIMIT_BYTES if spec["kind"] == "image" else ZIP_UNZIPPED_LIMIT_BYTES
        ),
    }
