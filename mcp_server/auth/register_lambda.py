"""RFC 7591 Dynamic Client Registration, backed by Cognito app clients.

Cognito has no native DCR. That gap is the only reason this Lambda exists
-- and closing it is what makes `oauth_dcr` viable, which is the one OAuth
mode that needs no coordination with Anthropic at all (the alternatives,
`static_headers` and `oauth_anthropic_creds`, each require a human-mediated
approval step with no stated timeline).

This endpoint sits in front of API Gateway's authorizer rather than behind
it: nothing is authenticated yet at this point in the flow, by definition.

**The refresh-token lifetime is the whole reason this is a Lambda and not
a pre-created app client.** Cognito's `RefreshTokenValidity` defaults to 30
days, and that clock **does not reset on use** -- AWS's own documentation
is explicit that with rotation enabled "the new refresh token is valid for
the remaining duration of the original refresh token." Left at the default,
the operator re-clicks the OAuth consent screen roughly monthly, and
nothing about that failure names the cause. Setting it explicitly at
registration time is what makes "one click, ever" actually true. For a
single-operator private server a long-lived refresh token is the right
trade; on a multi-tenant service it would not be.
"""

import json
import os

from mcp_server.auth import discovery
import time

# Cognito's own maximum is 10 years. Expressed in days with an explicit
# TokenValidityUnits, because the API's raw integer bound (315,360,000) is
# in seconds and the unit is what decides how the number is read -- passing
# 3650 without the unit means 3650 *seconds*, i.e. an hour, which is the
# silent misconfiguration this constant pair exists to prevent.
REFRESH_TOKEN_VALIDITY_DAYS = 3650

# Bounded by the API model at 60. Non-zero so a client-side retry during
# rotation does not invalidate a token that was about to work.
ROTATION_RETRY_GRACE_SECONDS = 60

_ALLOWED_SCOPES = ["openid"]


class RegistrationError(Exception):
    """Carries the RFC 7591 error code alongside the message.

    Never a SystemExit: this runs in a Lambda, where SystemExit escapes an
    `except Exception` and kills the container rather than failing one
    request -- the same rule pcluster_core follows for the MCP layer.
    """

    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.status = status


def _client():
    import boto3

    # Runs only in-Lambda, where AWS_REGION is authoritatively this
    # function's own region and the Cognito pool it manages is in it.
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    return boto3.client("cognito-idp", region_name=region)


def register(payload, *, user_pool_id, cognito=None):
    """Create a Cognito app client for one DCR request.

    Returns the RFC 7591 client-information response body.
    """
    from mcp_server.auth.discovery import CLAUDE_REDIRECT_URI

    if not isinstance(payload, dict):
        raise RegistrationError(
            "invalid_client_metadata", "registration body must be a JSON object"
        )

    redirect_uris = payload.get("redirect_uris")
    if not isinstance(redirect_uris, list) or not redirect_uris:
        raise RegistrationError(
            "invalid_redirect_uri",
            "redirect_uris is required and must be a non-empty array",
        )
    if not all(isinstance(u, str) and u.startswith("https://") for u in redirect_uris):
        # An http:// callback would carry the authorization code in clear
        # text. Cognito itself permits http only for localhost; this server
        # has no localhost caller.
        raise RegistrationError(
            "invalid_redirect_uri", "every redirect_uri must be an https:// URL"
        )
    if CLAUDE_REDIRECT_URI not in redirect_uris:
        # Rejected here rather than left to fail later: an app client whose
        # callback list omits this can never complete the handshake, and
        # Cognito's own error at that point ("redirect_mismatch", on the
        # authorize redirect) names nothing an operator can act on.
        raise RegistrationError(
            "invalid_redirect_uri",
            f"redirect_uris must include {CLAUDE_REDIRECT_URI} -- without it "
            f"the connector cannot complete the OAuth handshake",
        )

    client_name = payload.get("client_name") or "claude-mcp-client"

    cognito = cognito or _client()
    try:
        resp = cognito.create_user_pool_client(
            UserPoolId=user_pool_id,
            ClientName=str(client_name)[:128],
            # Public client. A secret cannot be kept by a browser-side
            # caller, and RFC 7591 registration returns no secret here --
            # PKCE is what protects the code exchange instead.
            GenerateSecret=False,
            AllowedOAuthFlows=["code"],
            AllowedOAuthScopes=_ALLOWED_SCOPES,
            AllowedOAuthFlowsUserPoolClient=True,
            SupportedIdentityProviders=["COGNITO"],
            CallbackURLs=redirect_uris,
            # Satisfies the MCP spec's "rotate refresh tokens for public
            # clients" requirement natively -- no custom rotation logic.
            RefreshTokenRotation={
                "Feature": "ENABLED",
                "RetryGracePeriodSeconds": ROTATION_RETRY_GRACE_SECONDS,
            },
            RefreshTokenValidity=REFRESH_TOKEN_VALIDITY_DAYS,
            TokenValidityUnits={"RefreshToken": "days"},
            EnableTokenRevocation=True,
        )
    except Exception as e:
        name = type(e).__name__
        if name in (
            "InvalidParameterException",
            "InvalidOAuthFlowException",
            "ScopeDoesNotExistException",
        ):
            raise RegistrationError("invalid_client_metadata", str(e)) from e
        if name == "LimitExceededException":
            # The pool's app-client quota. Worth naming, because the fix is
            # to delete stale clients, not to retry.
            raise RegistrationError(
                "invalid_client_metadata",
                f"the user pool is at its app-client limit: {e}",
            ) from e
        raise

    client = resp["UserPoolClient"]
    return {
        "client_id": client["ClientId"],
        "client_id_issued_at": int(time.time()),
        "redirect_uris": list(client.get("CallbackURLs") or redirect_uris),
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        # Public client: no secret was generated, so the token endpoint
        # takes no client authentication.
        "token_endpoint_auth_method": "none",
        "client_name": client.get("ClientName", client_name),
    }


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Cache-Control": "no-store",
        },
        "body": json.dumps(body),
    }


def _request_path(event):
    """The path, across both API Gateway payload shapes.

    HTTP APIs (payload 2.0) put it in `rawPath`; REST APIs and payload 1.0
    use `path`. Reading only one yields an empty string, which would route
    every request to registration -- including the two discovery documents,
    which must never require a body.
    """
    return event.get("rawPath") or event.get("path") or ""


def _discovery_response(path, *, user_pool_id, region):
    """Serve the two metadata documents, or None if this is not one.

    They live here rather than in their own function because they are the
    same kind of endpoint as registration: public, unauthenticated, tiny,
    and part of one OAuth flow. A separate tier would mean another Lambda,
    role, policy and cold start to return a dict.
    """
    base = os.environ.get("MCP_API_BASE_URL", "")
    domain = os.environ.get("MCP_COGNITO_DOMAIN", "")
    if path.endswith("/.well-known/oauth-authorization-server"):
        return _response(
            200,
            discovery.authorization_server_metadata(
                region=region,
                user_pool_id=user_pool_id,
                cognito_domain=domain,
                api_base_url=base,
            ),
        )
    if path.endswith("/.well-known/oauth-protected-resource"):
        return _response(
            200,
            discovery.protected_resource_metadata(
                region=region,
                user_pool_id=user_pool_id,
                api_base_url=base,
            ),
        )
    return None


def lambda_handler(event, context):
    user_pool_id = os.environ.get("MCP_USER_POOL_ID")
    if not user_pool_id:
        # Configuration error, not a client error. A 400 here would tell
        # Claude its own request was malformed and send the operator
        # looking in the wrong place entirely.
        return _response(
            500,
            {
                "error": "server_error",
                "error_description": "MCP_USER_POOL_ID is not configured on this function",
            },
        )

    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or ""
    served = _discovery_response(
        _request_path(event),
        user_pool_id=user_pool_id,
        region=region,
    )
    if served is not None:
        return served

    raw = event.get("body")
    try:
        payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except ValueError as e:
        return _response(
            400,
            {
                "error": "invalid_client_metadata",
                "error_description": f"body is not valid JSON: {e}",
            },
        )

    try:
        body = register(payload, user_pool_id=user_pool_id)
    except RegistrationError as e:
        return _response(
            e.status,
            {
                "error": e.code,
                "error_description": str(e),
            },
        )
    except Exception as e:
        return _response(
            500,
            {
                "error": "server_error",
                "error_description": f"{type(e).__name__}: {e}",
            },
        )

    # RFC 7591: 201 on successful registration, not 200.
    return _response(201, body)
