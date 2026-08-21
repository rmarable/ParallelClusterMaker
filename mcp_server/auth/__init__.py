"""Workstream 6: OAuth for the remote MCP server.

Side-effect-free by contract, like `mcp_server/__init__.py`: no boto3
client, no credential resolution, no network call at import. The authorizer
runs on *every* MCP request, so anything done here is done on every cold
start.

Three pieces sit on top of native Cognito, which supplies everything else:

  * `register_lambda` -- RFC 7591 Dynamic Client Registration. Cognito has
    no native DCR, and DCR is what lets Claude set the connector up with
    zero coordination with Anthropic.
  * `authorizer_lambda` -- an API Gateway Lambda authorizer, deliberately
    *not* the native JWT authorizer. See that module for why: Cognito
    access tokens carry no `aud` claim at all.
  * `discovery` -- the two static documents, hand-authored rather than
    proxied from Cognito's own.
"""
