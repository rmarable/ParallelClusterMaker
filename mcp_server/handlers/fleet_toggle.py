"""Handler Lambda for the 'fleet-toggle' tier."""

from mcp_server.handlers.base import make_lambda_handler

TIER = "fleet-toggle"

lambda_handler = make_lambda_handler(TIER)
