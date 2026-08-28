"""Tier -> Lambda function name. Deliberately dependency-free.

Three places need to agree on these names:

  1. this module, which the Router Lambda uses to invoke a handler;
  2. `_MCP_LAMBDA_TIERS` in `src/pcluster_core.py`, which creates the
     execution role per tier;
  3. `templates/MCPRouterLambda.json_src`, which grants
     `lambda:InvokeFunction` on exactly these ARNs.

They are not collapsed into one source because the dependency directions
conflict: the router's deployment package must not pull in
`pcluster_core` (and through it boto3, PCluster, Jinja2 -- the whole chain
whose absence is what makes the router's near-zero IAM meaningful rather
than incidental), while `src/` should not depend on `mcp_server/` either.

So the agreement is enforced by test instead of by import --
`TestTierNamesAgreeEverywhere` in tests/test_mcp_router.py compares all
three. A mismatch here is invisible until deployment, where it surfaces as
the router being denied on an ARN that does not match any function it can
reach.
"""

FUNCTION_NAMES = {
    "router": "pclustermaker-mcp-router",
    "read-only": "pclustermaker-mcp-read-only",
    "fleet-toggle": "pclustermaker-mcp-fleet-toggle",
    "stack-mutation": "pclustermaker-mcp-stack-mutation",
    "stack-mutation-node": "pclustermaker-mcp-stack-mutation-node",
    "register": "pclustermaker-mcp-register",
    "authorizer": "pclustermaker-mcp-authorizer",
}


# Tool name -> tier. Lives here, not in router.py or tools.py, because all
# three need it and this module is the only one all three can import: the
# router's deployment package must stay free of pcluster_core, and tools.py
# is the opposite end of that dependency. Names only, never schemas --
# schemas stay with the handler that implements the tool.
#
# This is the *planned* surface. Some entries are not implemented in
# tools.py yet; UNIMPLEMENTED below names those explicitly so a routed but
# missing tool produces a clear error rather than looking like a typo, and
# so a test can tell "not built yet" from "silently dropped".
TOOL_TIERS = {
    "list_clusters": "read-only",
    "get_build_status": "read-only",
    "run_readonly_slurm_command": "read-only",
    "check_cluster_health": "read-only",
    "get_cost_report": "read-only",
    "diagnose_cluster": "read-only",
    "resolve_access_info": "read-only",
    "list_queues": "read-only",
    "preview_cluster_delete": "read-only",
    # add_queue/remove_queue WRITE configs/<name>.yaml. They were on the
    # read-only tier because they mutate no stack -- true, and beside the
    # point: a tier called read-only whose policy carries s3:PutObject
    # misrepresents itself to whoever reads the policy next. They sit with
    # the rest of the config's lifecycle instead (apply reads it, teardown
    # deletes it). The cost is real and is accepted: a queue edit now
    # carries the stack-mutation tier's blast radius, which is more than
    # the edit needs. The alternative -- a fifth tier for config writes --
    # buys least privilege for a fifth Lambda, its own role, policy and
    # cold start.
    "add_queue": "stack-mutation",
    "remove_queue": "stack-mutation",
    "stop_fleet": "fleet-toggle",
    "start_fleet": "fleet-toggle",
    "delete_cluster": "stack-mutation",
    "finalize_cluster_teardown": "stack-mutation",
    # create/update both call assert_valid_node_js() on their first line
    "create_cluster": "stack-mutation-node",
    "finalize_cluster_build": "stack-mutation-node",
    "apply_cluster_update": "stack-mutation-node",
    "preview_cluster_config": "stack-mutation-node",
}

# Routed but not yet implemented in mcp_server/tools.py. Kept as an
# explicit list rather than inferred: the difference between "planned, not
# built" and "was implemented and got dropped" is exactly what a reader
# needs, and a test pins that this list shrinks to nothing as the wrappers
# land rather than quietly absorbing a regression.
UNIMPLEMENTED = frozenset()

# Tiers that serve tools (and so answer tools/list). The auth tiers exist
# for the Workstream 6 flow and are never invoked by the router.
HANDLER_TIERS = ("read-only", "fleet-toggle", "stack-mutation", "stack-mutation-node")


def tools_for(tier):
    """Tool names this tier serves, implemented or not."""
    return sorted(name for name, t in TOOL_TIERS.items() if t == tier)


def function_name_for(tier):
    try:
        return FUNCTION_NAMES[tier]
    except KeyError:
        raise KeyError(
            f"unknown MCP tier {tier!r} -- known tiers: {', '.join(sorted(FUNCTION_NAMES))}"
        )
