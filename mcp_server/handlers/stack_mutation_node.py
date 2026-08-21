"""Handler Lambda for the 'stack-mutation-node' tier."""

from mcp_server.handlers.base import make_lambda_handler

TIER = "stack-mutation-node"

lambda_handler = make_lambda_handler(TIER)
