"""Handler Lambda for the 'read-only' tier."""

from mcp_server.handlers.base import make_lambda_handler

TIER = "read-only"

lambda_handler = make_lambda_handler(TIER)
