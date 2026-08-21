"""Workstream 6: OAuth for the remote MCP server.

The load-bearing facts here are Cognito-specific and easy to get subtly
wrong in a way that still looks correct:

  * **Cognito access tokens carry no `aud` claim.** They carry `client_id`;
    only ID tokens have `aud`. Every audience check must go through
    `client_id`, and `token_use` must pin the token to an access token so
    an ID token cannot authorize a tool call.
  * **Every authentication failure must raise, never return a Deny policy.**
    API Gateway maps a raised `Unauthorized` to 401 and a Deny policy to
    403; an MCP client re-authenticates on 401 and gives up on 403.
  * **`RefreshTokenValidity` must be set explicitly.** Cognito's 30-day
    default does not reset on use, so leaving it silently turns "one
    consent click, ever" into "one click a month".

Real RS256 keys are generated per-test with `cryptography` and a real
`jwt.encode`, so the signature path is exercised rather than stubbed --
a fake decode would pass whatever claims the test handed it and prove
nothing about the checks that matter.
"""

import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

import jwt  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

from mcp_server.auth import authorizer_lambda as authz  # noqa: E402
from mcp_server.auth import discovery  # noqa: E402
from mcp_server.auth import register_lambda as reg  # noqa: E402

REGION = "us-east-2"
POOL = "us-east-2_AbCdEf123"
API = "https://mcp.example.com/prod"
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL}"


@pytest.fixture(scope="module")
def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key, key.public_key()


class _StubJWKS:
    """Stands in for PyJWKClient. Only the key resolution is stubbed; the
    signature verification itself is real."""

    def __init__(self, public_key):
        self._key = public_key

    def get_signing_key_from_jwt(self, token):
        return type("K", (), {"key": self._key})()


def _token(private_key, **claims):
    body = {
        "iss": ISSUER,
        "token_use": "access",
        "client_id": "abc123client",
        "sub": "user-1",
        "exp": 9999999999,
    }
    body.update(claims)
    for k in [k for k, v in body.items() if v is None]:
        del body[k]
    return jwt.encode(body, private_key, algorithm="RS256")


class _Cognito:
    def __init__(self, known=("abc123client",), raise_name=None):
        self.known = set(known)
        self.raise_name = raise_name
        self.calls = []

    def describe_user_pool_client(self, UserPoolId, ClientId):
        self.calls.append((UserPoolId, ClientId))
        if self.raise_name:
            raise type(self.raise_name, (Exception,), {})("boom")
        if ClientId not in self.known:
            raise type("ResourceNotFoundException", (Exception,), {})("no such client")
        return {"UserPoolClient": {"ClientId": ClientId}}

    def create_user_pool_client(self, **kw):
        self.calls.append(kw)
        return {"UserPoolClient": {
            "ClientId": "newclient123",
            "ClientName": kw["ClientName"],
            "CallbackURLs": kw.get("CallbackURLs", []),
        }}


class TestTheAudienceCheckMatchesHowCognitoActuallyWorks:
    """The central correctness fact of this workstream."""

    def test_a_valid_access_token_is_accepted(self, keypair):
        priv, pub = keypair
        event = {"authorizationToken": "Bearer " + _token(priv),
                 "methodArn": "arn:aws:execute-api:r:a:api/prod/POST/mcp"}
        policy = authz.authorize(
            event, region=REGION, user_pool_id=POOL,
            cognito=_Cognito(), jwks_client=_StubJWKS(pub),
        )
        assert policy["policyDocument"]["Statement"][0]["Effect"] == "Allow"
        assert policy["principalId"] == "abc123client"

    def test_an_id_token_is_refused(self, keypair):
        """An ID token carries `aud` equal to the client id, so a check
        written against `aud` would accept it. It is not an access token
        and must never authorize a tool call."""
        priv, pub = keypair
        token = _token(priv, token_use="id", aud="abc123client", client_id=None)
        with pytest.raises(authz.Unauthorized, match="token_use"):
            authz.verify_claims(token, region=REGION, user_pool_id=POOL,
                                jwks_client=_StubJWKS(pub))

    def test_a_token_without_client_id_is_refused(self, keypair):
        priv, pub = keypair
        token = _token(priv, client_id=None)
        with pytest.raises(authz.Unauthorized, match="client_id"):
            authz.verify_claims(token, region=REGION, user_pool_id=POOL,
                                jwks_client=_StubJWKS(pub))

    def test_audience_verification_stays_disabled(self, keypair):
        """Vacuity guard on the exemption itself: `verify_aud` is off
        because Cognito access tokens have no `aud`. If someone "restores"
        it, every valid token starts failing -- so a token with no `aud`
        must keep working."""
        priv, pub = keypair
        claims = authz.verify_claims(_token(priv), region=REGION,
                                     user_pool_id=POOL, jwks_client=_StubJWKS(pub))
        assert "aud" not in claims

    def test_a_token_signed_by_another_key_is_refused(self, keypair):
        """The signature check is real, not stubbed."""
        _, pub = keypair
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with pytest.raises(authz.Unauthorized, match="rejected"):
            authz.verify_claims(_token(other), region=REGION,
                                user_pool_id=POOL, jwks_client=_StubJWKS(pub))

    def test_an_expired_token_is_refused(self, keypair):
        priv, pub = keypair
        with pytest.raises(authz.Unauthorized, match="rejected"):
            authz.verify_claims(_token(priv, exp=1), region=REGION,
                                user_pool_id=POOL, jwks_client=_StubJWKS(pub))

    def test_a_token_from_another_pool_is_refused(self, keypair):
        """Issuer binds the token to this pool. Without it, any Cognito
        pool in any account would authorize."""
        priv, pub = keypair
        token = _token(priv, iss="https://cognito-idp.us-east-2.amazonaws.com/us-east-2_Other")
        with pytest.raises(authz.Unauthorized, match="rejected"):
            authz.verify_claims(token, region=REGION, user_pool_id=POOL,
                                jwks_client=_StubJWKS(pub))


class TestTheClientMustStillExistInThePool:
    def test_a_deleted_client_is_denied(self, keypair):
        """Deleting the app client is how access is revoked; it must take
        effect immediately, with no second store to keep in sync."""
        priv, pub = keypair
        event = {"authorizationToken": "Bearer " + _token(priv), "methodArn": "a/b/c"}
        with pytest.raises(authz.Unauthorized, match="does not exist"):
            authz.authorize(event, region=REGION, user_pool_id=POOL,
                            cognito=_Cognito(known=()), jwks_client=_StubJWKS(pub))

    def test_the_check_is_scoped_to_this_pool(self, keypair):
        priv, pub = keypair
        cog = _Cognito()
        authz.authorize({"authorizationToken": "Bearer " + _token(priv),
                         "methodArn": "a/b/c"},
                        region=REGION, user_pool_id=POOL,
                        cognito=cog, jwks_client=_StubJWKS(pub))
        assert cog.calls == [(POOL, "abc123client")]

    def test_an_api_failure_is_not_reported_as_an_unknown_client(self, keypair):
        """Throttling or a denied IAM call is not evidence the client is
        invalid. Fail closed, but say which."""
        priv, pub = keypair
        event = {"authorizationToken": "Bearer " + _token(priv), "methodArn": "a/b/c"}
        with pytest.raises(authz.Unauthorized, match="could not verify"):
            authz.authorize(event, region=REGION, user_pool_id=POOL,
                            cognito=_Cognito(raise_name="TooManyRequestsException"),
                            jwks_client=_StubJWKS(pub))


class TestEveryFailureRaisesRatherThanDenying:
    """API Gateway maps a raised Unauthorized to 401 and a Deny policy to
    403. Claude re-authenticates on 401 and gives up on 403, so a Deny
    policy for an expired token leaves the connector permanently broken."""

    def test_a_missing_header_raises(self):
        with pytest.raises(authz.Unauthorized, match="no Authorization"):
            authz.authorize({}, region=REGION, user_pool_id=POOL)

    def test_a_non_bearer_header_raises(self):
        with pytest.raises(authz.Unauthorized, match="Bearer"):
            authz.authorize({"authorizationToken": "Basic abc"},
                            region=REGION, user_pool_id=POOL)

    def test_the_module_never_builds_a_deny_policy(self):
        """Source-level, because a Deny is exactly what a well-meaning
        refactor would add -- it reads as the "proper" authorizer shape."""
        with open(os.path.join(REPO_ROOT, "mcp_server", "auth",
                               "authorizer_lambda.py")) as fh:
            body = "\n".join(
                l for l in fh if not l.lstrip().startswith("#")
            )
        assert '"Deny"' not in body and "'Deny'" not in body

    def test_a_misconfigured_authorizer_fails_closed(self, monkeypatch):
        monkeypatch.delenv("MCP_USER_POOL_ID", raising=False)
        monkeypatch.setenv("AWS_REGION", REGION)
        with pytest.raises(authz.Unauthorized, match="not configured"):
            authz.lambda_handler({"authorizationToken": "Bearer x"}, None)

    def test_the_exception_name_is_the_one_api_gateway_maps_to_401(self):
        """The class name is the contract, not just a label."""
        assert authz.Unauthorized.__name__ == "Unauthorized"


class TestTheAllowPolicyIsNotScopedToOnePath:
    def test_it_widens_to_the_whole_stage(self):
        """API Gateway caches an authorizer response by token. A policy
        scoped to whichever path was called first would be replayed for
        every other path and deny it."""
        arn = "arn:aws:execute-api:us-east-2:1:api1/prod/POST/mcp"
        assert authz._api_wildcard(arn) == (
            "arn:aws:execute-api:us-east-2:1:api1/prod/*"
        )

    def test_a_second_path_is_covered_by_the_first_response(self, keypair):
        priv, pub = keypair
        policy = authz.authorize(
            {"authorizationToken": "Bearer " + _token(priv),
             "methodArn": "arn:aws:execute-api:r:a:api1/prod/POST/mcp"},
            region=REGION, user_pool_id=POOL,
            cognito=_Cognito(), jwks_client=_StubJWKS(pub),
        )
        resource = policy["policyDocument"]["Statement"][0]["Resource"]
        assert resource.endswith("/prod/*")


class TestRegistrationSetsTheRefreshTokenLifetime:
    """Cognito's 30-day default does not reset on use, so leaving it
    unset silently turns "one consent click, ever" into a monthly chore
    with nothing naming the cause."""

    def _register(self, cog=None, **over):
        payload = {"redirect_uris": [discovery.CLAUDE_REDIRECT_URI]}
        payload.update(over)
        return reg.register(payload, user_pool_id=POOL, cognito=cog or _Cognito())

    def test_the_validity_is_set_explicitly(self):
        cog = _Cognito()
        self._register(cog)
        kw = cog.calls[0]
        assert kw["RefreshTokenValidity"] == reg.REFRESH_TOKEN_VALIDITY_DAYS

    def test_the_unit_is_sent_with_it(self):
        """The API's raw bound is in seconds. Sending 3650 without the
        unit means 3650 seconds -- about an hour -- which looks like a
        correctly-configured long lifetime and is not one."""
        cog = _Cognito()
        self._register(cog)
        assert cog.calls[0]["TokenValidityUnits"]["RefreshToken"] == "days"

    def test_the_value_is_within_cognitos_own_maximum(self):
        """10 years, from the service model's own bound of 315,360,000
        seconds."""
        assert 0 < reg.REFRESH_TOKEN_VALIDITY_DAYS * 86400 <= 315_360_000

    def test_rotation_is_enabled(self):
        """Satisfies the MCP spec's "rotate refresh tokens for public
        clients" natively, with no custom rotation logic."""
        cog = _Cognito()
        self._register(cog)
        assert cog.calls[0]["RefreshTokenRotation"]["Feature"] == "ENABLED"

    def test_the_retry_grace_is_within_the_api_bound(self):
        assert 0 <= reg.ROTATION_RETRY_GRACE_SECONDS <= 60

    def test_the_client_is_public(self):
        """A browser-side caller cannot keep a secret; PKCE protects the
        code exchange instead."""
        cog = _Cognito()
        self._register(cog)
        assert cog.calls[0]["GenerateSecret"] is False

    def test_only_the_code_flow_is_allowed(self):
        cog = _Cognito()
        self._register(cog)
        assert cog.calls[0]["AllowedOAuthFlows"] == ["code"]

    def test_no_secret_is_returned(self):
        body = self._register()
        assert "client_secret" not in body
        assert body["token_endpoint_auth_method"] == "none"


class TestRegistrationValidatesRedirectUris:
    def test_claudes_callback_must_be_present(self):
        """An app client without it can never complete the handshake, and
        Cognito's own later error names nothing actionable."""
        with pytest.raises(reg.RegistrationError, match="claude.ai"):
            reg.register({"redirect_uris": ["https://example.com/cb"]},
                         user_pool_id=POOL, cognito=_Cognito())

    def test_an_http_callback_is_refused(self):
        """It would carry the authorization code in clear text.

        The required Claude callback is included alongside it, so the
        *only* check that can reject this payload is the https one. An
        earlier version passed a lone `http://claude.ai/...`, which the
        missing-Claude-callback check rejected instead -- and since that
        message quotes `https://claude.ai/...`, a `match="https"` matched
        it and the test passed with the https check deleted.
        """
        payload = {"redirect_uris": [
            discovery.CLAUDE_REDIRECT_URI, "http://evil.example.com/cb",
        ]}
        with pytest.raises(reg.RegistrationError, match="every redirect_uri"):
            reg.register(payload, user_pool_id=POOL, cognito=_Cognito())

    def test_missing_redirect_uris_is_refused(self):
        with pytest.raises(reg.RegistrationError, match="redirect_uris"):
            reg.register({}, user_pool_id=POOL, cognito=_Cognito())

    def test_a_non_object_body_is_refused(self):
        with pytest.raises(reg.RegistrationError):
            reg.register([], user_pool_id=POOL, cognito=_Cognito())

    def test_the_error_carries_an_rfc7591_code(self):
        try:
            reg.register({}, user_pool_id=POOL, cognito=_Cognito())
        except reg.RegistrationError as e:
            assert e.code == "invalid_redirect_uri"

    def test_it_raises_rather_than_exiting(self):
        assert not issubclass(reg.RegistrationError, SystemExit)


class TestTheRegisterHandlerShape:
    def test_success_is_201_per_rfc7591(self, monkeypatch):
        monkeypatch.setenv("MCP_USER_POOL_ID", POOL)
        monkeypatch.setattr(reg, "_client", lambda: _Cognito())
        resp = reg.lambda_handler(
            {"body": json.dumps({"redirect_uris": [discovery.CLAUDE_REDIRECT_URI]})},
            None,
        )
        assert resp["statusCode"] == 201
        assert json.loads(resp["body"])["client_id"] == "newclient123"

    def test_a_bad_body_is_400_not_500(self, monkeypatch):
        monkeypatch.setenv("MCP_USER_POOL_ID", POOL)
        resp = reg.lambda_handler({"body": "{not json"}, None)
        assert resp["statusCode"] == 400

    def test_a_missing_pool_is_500_not_400(self, monkeypatch):
        """A 400 would tell Claude its own request was malformed and send
        the operator looking in entirely the wrong place."""
        monkeypatch.delenv("MCP_USER_POOL_ID", raising=False)
        resp = reg.lambda_handler({"body": "{}"}, None)
        assert resp["statusCode"] == 500

    def test_the_response_is_not_cached(self, monkeypatch):
        monkeypatch.setenv("MCP_USER_POOL_ID", POOL)
        monkeypatch.setattr(reg, "_client", lambda: _Cognito())
        resp = reg.lambda_handler(
            {"body": json.dumps({"redirect_uris": [discovery.CLAUDE_REDIRECT_URI]})},
            None,
        )
        assert resp["headers"]["Cache-Control"] == "no-store"


class TestTheDiscoveryDocument:
    def _meta(self):
        return discovery.authorization_server_metadata(
            region=REGION, user_pool_id=POOL,
            cognito_domain="https://mcp-auth.auth.us-east-2.amazoncognito.com",
            api_base_url=API,
        )

    def test_s256_is_advertised_unconditionally(self):
        """The entire reason this document is hand-authored rather than
        proxied. The MCP spec says a client MUST refuse to proceed if this
        field is absent, and Cognito's own metadata omits it
        unpredictably."""
        assert self._meta()["code_challenge_methods_supported"] == ["S256"]

    def test_the_registration_endpoint_is_ours(self):
        """The one endpoint Cognito cannot serve."""
        assert self._meta()["registration_endpoint"] == f"{API}/register"

    def test_the_other_endpoints_point_straight_at_cognito(self):
        """No proxy: proxying the token endpoint would put this server on
        the critical path of every refresh for no benefit."""
        meta = self._meta()
        assert "amazoncognito.com/oauth2/authorize" in meta["authorization_endpoint"]
        assert "amazoncognito.com/oauth2/token" in meta["token_endpoint"]
        assert meta["jwks_uri"].startswith(f"https://cognito-idp.{REGION}")

    def test_the_issuer_matches_what_the_authorizer_verifies(self):
        """Two spellings of one issuer that never import each other would
        drift, and the failure is every token being rejected."""
        assert self._meta()["issuer"] == discovery.cognito_issuer(
            region=REGION, user_pool_id=POOL
        )

    def test_the_protected_resource_document_names_the_authorization_server(self):
        prm = discovery.protected_resource_metadata(
            region=REGION, user_pool_id=POOL, api_base_url=API,
        )
        assert prm["resource"] == API
        assert prm["authorization_servers"] == [
            discovery.cognito_issuer(region=REGION, user_pool_id=POOL)
        ]

    def test_the_www_authenticate_header_points_at_that_document(self):
        """A 401 without this leaves a client no way to discover where to
        authenticate, and connector setup dead-ends."""
        header = discovery.www_authenticate_header(api_base_url=API)
        assert header.startswith("Bearer ")
        assert f'resource_metadata="{API}/.well-known/oauth-protected-resource"' in header

    def test_both_documents_are_json_serializable(self):
        json.loads(json.dumps(self._meta()))
        json.loads(json.dumps(discovery.protected_resource_metadata(
            region=REGION, user_pool_id=POOL, api_base_url=API)))


class TestTheAuthTiersStayLean:
    """The authorizer runs on every MCP request, so its cold start is on
    the critical path of the whole server."""

    def test_neither_tier_carries_pcluster_core_or_fastmcp(self):
        from mcp_server.packaging import sources_for, requirements_for

        for tier in ("register", "authorizer"):
            assert not any("pcluster_core" in s for s in sources_for(tier)), tier
            assert "fastmcp" not in requirements_for(tier), tier
            assert "aws-parallelcluster>=3.15" not in requirements_for(tier), tier

    def test_the_authorizer_names_pyjwt_directly(self):
        """It arrives transitively via `mcp` in the development set, and
        this artifact carries neither `mcp` nor `fastmcp` -- so relying on
        the transitive would produce a function that cannot verify a
        token."""
        from mcp_server.packaging import requirements_for

        assert "PyJWT" in requirements_for("authorizer")

    def test_pyjwt_is_a_direct_development_dependency_too(self):
        with open(os.path.join(REPO_ROOT, "requirements.txt")) as fh:
            names = {
                l.strip().split(">=")[0].split("==")[0]
                for l in fh if l.strip() and not l.startswith("#")
            }
        assert "PyJWT" in names

    def test_importing_the_authorizer_loads_no_pcluster_dependency(self):
        """Against the real import graph, not the declaration."""
        import subprocess

        script = (
            "import sys\n"
            f"sys.path.insert(0, {REPO_ROOT!r})\n"
            "import mcp_server.auth.authorizer_lambda\n"
            "bad = [m for m in ('pcluster', 'fastmcp', 'boto3', 'jwt') "
            "if m in sys.modules]\n"
            "print(','.join(sorted(bad)))\n"
        )
        out = subprocess.run([sys.executable, "-c", script],
                             capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr
        # boto3 and jwt are imported lazily inside the functions that need
        # them, so a cold start that rejects a malformed request pays for
        # neither.
        assert out.stdout.strip() == "", out.stdout


class TestTheAuthPolicyScoping:
    _DIR = os.path.join(REPO_ROOT, "templates")

    def _policy(self, name):
        with open(os.path.join(self._DIR, name)) as fh:
            return json.load(fh)

    def test_register_can_only_create_clients(self):
        stmts = self._policy("MCPRegisterLambda.json_src")["Statement"]
        actions = {a for s in stmts for a in s["Action"]}
        assert actions == {"cognito-idp:CreateUserPoolClient"}

    def test_the_authorizer_can_only_describe(self):
        """It must not be able to create or delete an app client -- it runs
        on every request, which is the widest exposure in the system."""
        stmts = self._policy("MCPAuthorizerLambda.json_src")["Statement"]
        actions = {a for s in stmts for a in s["Action"]}
        assert actions == {"cognito-idp:DescribeUserPoolClient"}

    def test_neither_is_scoped_to_all_user_pools(self):
        for name in ("MCPRegisterLambda.json_src", "MCPAuthorizerLambda.json_src"):
            for stmt in self._policy(name)["Statement"]:
                for res in stmt["Resource"]:
                    assert res.endswith("<MCP_USER_POOL_ID>"), (name, res)
                    assert "*" not in res, (name, res)
