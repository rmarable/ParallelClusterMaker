"""API Gateway Lambda authorizer for the remote MCP server.

**Why not API Gateway's native JWT authorizer.** Cognito *access* tokens
carry no `aud` claim at all -- they carry `client_id` instead; only *ID*
tokens have `aud`. The native JWT authorizer validates audience via `aud`
specifically, so against Cognito it either rejects every valid access token
or has to run with audience validation switched off entirely. The second
defeats the requirement the MCP spec states plainly: a server MUST validate
that access tokens were issued specifically for its use. So the check here
is `client_id`, which is the same guarantee expressed in the claim Cognito
actually populates.

**Why `token_use` is checked and not treated as belt-and-braces.** An ID
token *does* carry `aud`, and its value is the app client id -- so a check
written against `aud` would accept an ID token, and a check written against
`client_id` alone would reject it for the wrong reason (absent claim) and
keep working right up until someone "helpfully" fell back to `aud`. ID
tokens are not access tokens and must never authorize a tool call. Pinning
`token_use == "access"` states that directly.

**No allowlist store.** This pool has exactly one purpose and
`register_lambda` is the only thing that ever calls `CreateUserPoolClient`
against it, so any client id that currently exists in the pool is
legitimate by construction. `DescribeUserPoolClient` is the check, and
`ResourceNotFoundException` is a *deny* -- a client that was deleted (say,
to revoke access) stops working immediately, with no second store to keep
in sync.

**Deny vs. Unauthorized is not a style choice.** API Gateway turns a raised
`Unauthorized` into **401** and an explicit Deny policy into **403**. An MCP
client treats 401 as "re-authenticate" and 403 as "you are authenticated
and forbidden" -- so returning a Deny policy for a missing or expired token
leaves the connector permanently broken instead of prompting a refresh,
which Claude does automatically on a 401. Every authentication failure here
therefore raises; the Deny policy is not used at all. (This is documented
API Gateway behavior, taken from AWS's docs -- not something this repo has
live-verified.)
"""

import os

_TOKEN_USE = "access"
_ALGORITHMS = ["RS256"]

# Cached across warm invocations. The authorizer runs on *every* MCP
# request, so refetching Cognito's JWKS each time adds a round trip to the
# critical path and risks throttling; PyJWT's client caches the keys and
# refetches only on an unknown `kid`, which is exactly the rotation case.
_JWKS_CLIENT = None


class Unauthorized(Exception):
    """Raised for every authentication failure, with a message that says why.

    The **message** is what API Gateway maps, not the class name, and it
    must be exactly `Unauthorized` to produce a 401 -- measured, not
    assumed: a REST authorizer raising the bare word returned 401 and one
    raising a sentence returned 500. So these descriptive messages are for
    the log, and `lambda_handler` re-raises the bare word to the transport.

    Never replace a raise with a Deny policy: that is a 403, which does not
    prompt a client to re-authenticate. See this module's docstring.
    """


def _jwks_client(*, region, user_pool_id):
    global _JWKS_CLIENT
    if _JWKS_CLIENT is None:
        import jwt

        from mcp_server.auth.discovery import jwks_uri

        _JWKS_CLIENT = jwt.PyJWKClient(
            jwks_uri(region=region, user_pool_id=user_pool_id),
            cache_keys=True,
        )
    return _JWKS_CLIENT


def _bearer_token(event):
    """Pull the bearer token out of either authorizer event shape.

    A TOKEN authorizer gets `authorizationToken`; a REQUEST authorizer gets
    the raw headers. Both are handled because which one is configured is an
    infrastructure detail, and reading the wrong one yields "no token" --
    indistinguishable from an unauthenticated caller.
    """
    raw = event.get("authorizationToken")
    if not raw:
        headers = event.get("headers") or {}
        for key, value in headers.items():
            if key.lower() == "authorization":
                raw = value
                break
    if not raw:
        raise Unauthorized("no Authorization header")
    parts = raw.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise Unauthorized("Authorization header is not a Bearer token")
    return parts[1]


def verify_claims(token, *, region, user_pool_id, jwks_client=None, jwt_module=None):
    """Verify signature, expiry and issuer, then the two Cognito-specific
    claims. Returns the decoded claims."""
    import jwt as _jwt

    jwt_module = jwt_module or _jwt
    from mcp_server.auth.discovery import cognito_issuer

    client = jwks_client or _jwks_client(region=region, user_pool_id=user_pool_id)
    try:
        signing_key = client.get_signing_key_from_jwt(token)
    except Exception as e:
        raise Unauthorized(f"cannot resolve signing key: {type(e).__name__}: {e}") from e

    try:
        claims = jwt_module.decode(
            token,
            signing_key.key,
            algorithms=_ALGORITHMS,
            issuer=cognito_issuer(region=region, user_pool_id=user_pool_id),
            # Cognito access tokens have no `aud`; verifying it would fail
            # every valid token. The client_id check below is what replaces
            # it -- never relax one without the other.
            options={"verify_aud": False, "require": ["exp", "iss"]},
        )
    except Exception as e:
        raise Unauthorized(f"token rejected: {type(e).__name__}: {e}") from e

    if claims.get("token_use") != _TOKEN_USE:
        raise Unauthorized(
            f"token_use is {claims.get('token_use')!r}, expected {_TOKEN_USE!r} "
            f"-- an ID token must never authorize a tool call"
        )
    if not claims.get("client_id"):
        raise Unauthorized("token carries no client_id claim")
    return claims


def _client_exists(client_id, *, user_pool_id, cognito=None):
    if cognito is None:
        import boto3

        cognito = boto3.client("cognito-idp")
    try:
        cognito.describe_user_pool_client(
            UserPoolId=user_pool_id,
            ClientId=client_id,
        )
    except Exception as e:
        if type(e).__name__ == "ResourceNotFoundException":
            return False
        # Anything else -- throttling, a denied IAM call, an outage -- is
        # not evidence the client is invalid. Fail closed, but say so
        # distinctly rather than reporting an unknown client.
        raise Unauthorized(
            f"could not verify client id against the user pool: {type(e).__name__}: {e}"
        ) from e
    return True


def authorize(event, *, region, user_pool_id, cognito=None, jwks_client=None):
    """Verify the request's bearer token. Returns the allow policy."""
    token = _bearer_token(event)
    claims = verify_claims(
        token,
        region=region,
        user_pool_id=user_pool_id,
        jwks_client=jwks_client,
    )
    client_id = claims["client_id"]
    if not _client_exists(client_id, user_pool_id=user_pool_id, cognito=cognito):
        raise Unauthorized(
            f"client_id {client_id!r} does not exist in this user pool -- it was "
            f"never registered here, or it has been deleted to revoke access"
        )
    return _allow(claims, event)


def _allow(claims, event):
    return {
        "principalId": claims["client_id"],
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Action": "execute-api:Invoke",
                    "Effect": "Allow",
                    # The whole API, not event["methodArn"]: API Gateway caches
                    # an authorizer response by token, and a policy scoped to
                    # the one path that happened to be called first would then
                    # be replayed for every other path and deny it.
                    "Resource": _api_wildcard(event.get("methodArn", "")),
                }
            ],
        },
        "context": {
            "client_id": claims["client_id"],
            "sub": str(claims.get("sub", "")),
        },
    }


def _api_wildcard(method_arn):
    """Widen `.../stage/METHOD/path` to `.../stage/*` -- see _allow."""
    parts = method_arn.split("/")
    if len(parts) < 2:
        return method_arn or "*"
    return "/".join(parts[:2]) + "/*"


def lambda_handler(event, context):
    """Authorize one request, or deny it as a 401.

    Two messages, deliberately: the descriptive one goes to CloudWatch,
    where an operator can see *which* rule refused the token, and the bare
    word `Unauthorized` goes to API Gateway, which is the only string it
    maps to a 401. Raising the descriptive message at the transport yields
    a **500** -- measured on a REST API, both ways -- and a 500 is worse
    than either alternative here: a client reads it as a server fault and
    never re-authenticates, which is the entire behaviour the 401 exists to
    trigger.

    An *unexpected* exception is deliberately not converted. A bug in this
    function is a server fault and should surface as one; turning it into a
    401 would send a correctly-credentialled client into a re-auth loop
    over a defect it cannot fix.
    """
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    user_pool_id = os.environ.get("MCP_USER_POOL_ID")
    try:
        if not user_pool_id or not region:
            # Fail closed. A misconfigured authorizer must never allow.
            raise Unauthorized("authorizer is not configured with a user pool and region")
        return authorize(event, region=region, user_pool_id=user_pool_id)
    except Unauthorized as e:
        print(f"Unauthorized: {e}")
        raise Exception("Unauthorized") from None
