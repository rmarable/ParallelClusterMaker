"""The two OAuth discovery documents, hand-authored rather than proxied.

Stdlib only, and deliberately so: these are served as API Gateway mock
integrations, not Lambda functions, and this module exists so the JSON has
one source that a test can check rather than living as a literal pasted
into infrastructure configuration.

**Why not just point Claude at Cognito's own `.well-known/openid-configuration`.**
Cognito's document is unreliable about advertising
`code_challenge_methods_supported: ["S256"]` -- observed present in some
regions and times and silently absent in others, with no deprecation notice
either way. The MCP spec is explicit that a client encountering its absence
MUST refuse to proceed, so a connector that worked yesterday would stop
working with no change on our side and nothing in the failure naming the
cause. This document hardcodes it. That is safe because PKCE S256 support
is a property of Cognito's authorization endpoint that holds regardless of
whether the metadata advertises it -- the unreliability is in the
advertisement, not the capability.

Everything else points straight at Cognito's real endpoints.
`authorization_endpoint`, `token_endpoint` and `jwks_uri` need no proxy;
only `registration_endpoint` is ours, because Cognito has no native DCR.
"""

# Claude's fixed OAuth callback. A registration whose redirect_uris omit
# this cannot complete the handshake, so register_lambda rejects it up
# front rather than creating an app client that can never be used.
CLAUDE_REDIRECT_URI = "https://claude.ai/api/mcp/auth_callback"


def cognito_issuer(*, region, user_pool_id):
    return f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"


def jwks_uri(*, region, user_pool_id):
    return cognito_issuer(region=region, user_pool_id=user_pool_id) + "/.well-known/jwks.json"


def authorization_server_metadata(*, region, user_pool_id, cognito_domain, api_base_url):
    """RFC 8414 metadata, served at `/.well-known/oauth-authorization-server`.

    `cognito_domain` is the Hosted UI domain (the authorize/token endpoints
    live there, not on the `cognito-idp` API host, which serves only the
    issuer and JWKS).
    """
    domain = cognito_domain.rstrip("/")
    return {
        "issuer": cognito_issuer(region=region, user_pool_id=user_pool_id),
        "authorization_endpoint": f"{domain}/oauth2/authorize",
        "token_endpoint": f"{domain}/oauth2/token",
        "jwks_uri": jwks_uri(region=region, user_pool_id=user_pool_id),
        "registration_endpoint": f"{api_base_url.rstrip('/')}/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        # The whole reason this document is hand-authored. Never make this
        # conditional on what Cognito's own metadata says today.
        "code_challenge_methods_supported": ["S256"],
        # Public client, PKCE instead of a secret.
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["openid"],
    }


def protected_resource_metadata(*, region, user_pool_id, api_base_url):
    """RFC 9728 metadata, served at `/.well-known/oauth-protected-resource`.

    Names this server as the resource and points at the authorization
    server above. This is what the `WWW-Authenticate` header on a 401
    refers to, which is how a client discovers where to authenticate.
    """
    base = api_base_url.rstrip("/")
    return {
        "resource": base,
        "authorization_servers": [
            cognito_issuer(region=region, user_pool_id=user_pool_id)
        ],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["openid"],
    }


def www_authenticate_header(*, api_base_url):
    """The fixed `WWW-Authenticate` value for an unauthenticated request.

    Configured on API Gateway's UNAUTHORIZED gateway response, not returned
    by any Lambda -- the authorizer never gets to set headers on its own
    rejection. Without this header a client receiving a 401 has no way to
    discover where to authenticate, and the connector setup dead-ends.
    """
    base = api_base_url.rstrip("/")
    return (
        f'Bearer resource_metadata="{base}/.well-known/oauth-protected-resource"'
    )
