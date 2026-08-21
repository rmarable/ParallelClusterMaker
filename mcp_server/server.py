"""The two FastMCP server instances (Workstream 5).

Two instances, not one server with a runtime flag: the local/remote split
has to be visible in what is *registered*, so a tool that must never be
remotely callable cannot become so by a configuration mistake. Both are
built from the same wrappers in tools.py -- the difference is only which
set each one registers (see tools._LOCAL_ONLY).

  local  -- transport="stdio", full tool set. Runs on the operator's own
            machine beside the AWS credentials the CLI already uses.
  remote -- transport="streamable-http", restricted tool set. Fronted by
            API Gateway in the Lambda topology; SSH-key and tunnel
            operations are absent by construction.

Building an instance is side-effect-free: no boto3 client is constructed
and no credential is resolved until a tool is actually called, so
`build_local()`/`build_remote()` are safe to call from a test with nothing
stubbed.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from fastmcp import FastMCP  # noqa: E402

from mcp_server.tools import register_tools  # noqa: E402

_INSTRUCTIONS = (
    "Provision and operate AWS ParallelCluster HPC clusters. Cluster "
    "creation and teardown are long-running (20-45 minutes and 5-10 "
    "minutes respectively) and are kicked off without blocking -- poll "
    "list_clusters(live=True) or check_cluster_health() for progress "
    "rather than expecting the launch call to return a finished cluster."
)


def build_local():
    """Full tool set, for transport='stdio' on the operator's machine."""
    mcp = FastMCP(name="parallelclustermaker", instructions=_INSTRUCTIONS)
    register_tools(mcp, remote=False)
    return mcp


def build_remote():
    """Restricted tool set, for transport='streamable-http'."""
    mcp = FastMCP(name="parallelclustermaker-remote", instructions=_INSTRUCTIONS)
    register_tools(mcp, remote=True)
    return mcp


def main(argv=None):
    """Run the local stdio server -- the Claude Code / desktop entry point.

    The remote instance is not runnable from here on purpose: it is served
    by the Lambda topology behind API Gateway, not by a long-lived process
    someone starts by hand, and offering a --remote flag would invite
    running the restricted-but-still-privileged tool set on a host with
    the operator's full credentials and no authorizer in front of it.
    """
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        print(f"usage: {os.path.basename(sys.argv[0])}  (no arguments)", file=sys.stderr)
        return 2
    build_local().run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
