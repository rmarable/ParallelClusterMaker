"""Handler Lambda for the 'stack-mutation' tier."""

from mcp_server.handlers.base import make_lambda_handler

TIER = "stack-mutation"

lambda_handler = make_lambda_handler(TIER)
