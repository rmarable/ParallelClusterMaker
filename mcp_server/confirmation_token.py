"""Stateless confirmation tokens for the preview/execute tool pattern.

WHAT THIS IS NOT: authentication. The hash is **keyless** by design, so
anyone able to call `execute` can equally call `preview` and mint a valid
token for themselves. This gate exists to stop an *unpreviewed* execution
-- a model calling `create_cluster` or `delete_cluster` without first
having been shown, and having shown the operator, what it is about to do
-- not to stop a *hostile* one. Authorization is Workstream 6's job
(Cognito + the API Gateway authorizer); conflating the two would leave
both weaker. Anything that reads like "the token proves the caller is
allowed to do this" is wrong.

Why keyless rather than an HMAC: `execute` verifies by independently
recomputing the hash from the parameters it is about to act on. That needs
no shared secret, which in turn means no Secrets Manager/SSM/KMS grant in
any Lambda's IAM, and no secret distribution between the read-only tier
(which mints `preview_cluster_delete`'s token) and the stack-mutation tier
(which verifies it) -- two different deployment packages under Workstream
5's split.

The real exposure that split does create is version skew, and it is a
false negative rather than a hole: if one tier redeploys with a changed
canonicalization and the other has not, tokens minted before the change
stop verifying. A legitimate caller is rejected; a forged one is not
accepted. `_VERSION` is in the token precisely so that failure is
diagnosable as "stale/mismatched token version" rather than an unexplained
mismatch. The deployment rule (all handler Lambdas ship from one versioned
artifact for a token-touching change) is the actual mitigation and lives
in the plan, not in code.
"""

import hashlib
import hmac
import json
import time

_VERSION = "v1"
_DEFAULT_TTL_SECONDS = 900  # 15 minutes, per the migration plan


class ConfirmationTokenError(Exception):
    """Base for every token failure, so a tool wrapper can translate the
    whole family into one shaped MCP error rather than leaking a
    traceback."""


class MalformedToken(ConfirmationTokenError):
    pass


class ExpiredToken(ConfirmationTokenError):
    pass


class TokenMismatch(ConfirmationTokenError):
    """The token is well-formed and unexpired but does not match the action
    or parameters being executed -- i.e. what is about to happen is not
    what was previewed."""


def canonicalize(action, params):
    """Deterministic bytes for (action, params).

    json.dumps with sort_keys handles nested dicts recursively, and keeps
    lists in order -- which is correct here, since parameters like an
    instance-type list are order-significant to the resulting cluster.

    Types are preserved rather than stringified: 1 and "1" canonicalize
    differently, so a preview of max_queue_size=10 cannot authorize an
    execute of max_queue_size="10". allow_nan=False because NaN/Infinity
    are not valid JSON -- letting them through would produce output that
    does not round-trip, so this fails loudly instead.
    """
    payload = {"action": action, "params": params}
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _digest(action, params, issued_at):
    h = hashlib.sha256()
    h.update(_VERSION.encode("ascii"))
    h.update(b"\x00")
    h.update(str(issued_at).encode("ascii"))
    h.update(b"\x00")
    h.update(canonicalize(action, params))
    return h.hexdigest()


def mint(action, params, *, issued_at=None):
    """Return a token binding this exact (action, params) pair to a time.

    issued_at is carried in the token in the clear -- a hash cannot be
    reversed to recover it, and verification needs it both to recompute the
    digest and to judge the TTL. It is inside the digest as well, so it
    cannot be edited to extend a token's life.
    """
    if issued_at is None:
        issued_at = int(time.time())
    issued_at = int(issued_at)
    return f"{_VERSION}.{issued_at}.{_digest(action, params, issued_at)}"


def verify(token, action, params, *, now=None, ttl_seconds=_DEFAULT_TTL_SECONDS):
    """Raise unless `token` was minted for exactly this action and params
    and is still within its TTL. Returns the token's issued_at on success.

    The three failure modes stay distinct because they need different
    responses: a mismatch means re-preview (something changed), an expiry
    means re-preview (too slow), and a malformed token means the caller is
    passing something that never came from `mint` at all.
    """
    if not isinstance(token, str):
        raise MalformedToken(f"confirmation token must be a string, got {type(token).__name__}")
    parts = token.split(".")
    if len(parts) != 3:
        raise MalformedToken("confirmation token is not in <version>.<issued_at>.<digest> form")
    version, issued_at_raw, digest = parts
    if version != _VERSION:
        raise MalformedToken(
            f"confirmation token version {version!r} is not {_VERSION!r} -- it was minted by a "
            f"different build; re-run the preview tool to get a current one"
        )
    try:
        issued_at = int(issued_at_raw)
    except ValueError:
        raise MalformedToken("confirmation token timestamp is not an integer")

    now = int(time.time()) if now is None else int(now)
    age = now - issued_at
    if age > ttl_seconds:
        raise ExpiredToken(
            f"confirmation token expired {age - ttl_seconds}s ago (age {age}s, TTL "
            f"{ttl_seconds}s) -- re-run the preview tool"
        )
    if age < -ttl_seconds:
        # Clock skew beyond a whole TTL is not a token this build minted in
        # any meaningful sense; accepting it would let a future-dated token
        # outlive its window.
        raise MalformedToken(
            f"confirmation token is dated {-age}s in the future -- check clock skew"
        )

    expected = _digest(action, params, issued_at)
    # compare_digest rather than ==: free, and it keeps the comparison
    # habit correct if this ever does become keyed. Not a timing-attack
    # defense here -- there is no secret to leak.
    if not hmac.compare_digest(expected, digest):
        raise TokenMismatch(
            "confirmation token does not match the action and parameters being executed -- "
            "what is about to run is not what was previewed; re-run the preview tool"
        )
    return issued_at
