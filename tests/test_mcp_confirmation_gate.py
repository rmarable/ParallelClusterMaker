"""Workstream 7 layer 3: the preview/execute confirmation-token gate.

Pure Python -- no FastMCP, no AWS -- exactly as the plan's test-layer
mapping specifies: token generation, verification, TTL expiry, and
param-mismatch detection.

The property this whole module exists for is narrow and worth stating so
these tests are not over-read: a valid token proves *this exact action and
these exact parameters were previewed recently*. It proves nothing about
who the caller is. The hash is keyless by design, so anyone able to call
execute can also call preview; authorization is Workstream 6's separate
concern. Tests below therefore pin "what was previewed is what runs",
never "only an authorized caller can run this".
"""

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from mcp_server.confirmation_token import (  # noqa: E402
    ConfirmationTokenError,
    ExpiredToken,
    MalformedToken,
    TokenMismatch,
    canonicalize,
    mint,
    verify,
)

ACTION = "delete_cluster"
PARAMS = {"cluster_name": "osiris", "region": "us-east-2", "delete_s3_bucketname": True}


class TestCanonicalization:
    def test_key_order_does_not_change_the_result(self):
        a = canonicalize(ACTION, {"b": 2, "a": 1})
        b = canonicalize(ACTION, {"a": 1, "b": 2})
        assert a == b

    def test_nested_key_order_does_not_either(self):
        """sort_keys recurses; a shallow sort would make these differ and
        two identical previews mint different tokens."""
        a = canonicalize(ACTION, {"x": {"b": 2, "a": 1}})
        b = canonicalize(ACTION, {"x": {"a": 1, "b": 2}})
        assert a == b

    def test_list_order_is_significant(self):
        """Not incidental: parameters like an instance-type list are
        order-significant to the resulting cluster, so reordering one is a
        different plan and must not verify against the old token."""
        a = canonicalize(ACTION, {"types": ["c5.xlarge", "c6i.xlarge"]})
        b = canonicalize(ACTION, {"types": ["c6i.xlarge", "c5.xlarge"]})
        assert a != b

    def test_types_are_not_collapsed_to_strings(self):
        """A preview of max_queue_size=10 must not authorize an execute of
        max_queue_size="10"."""
        assert canonicalize(ACTION, {"n": 10}) != canonicalize(ACTION, {"n": "10"})

    def test_true_and_one_are_distinguished(self):
        """Python's True == 1, so a naive canonicalization can conflate
        them; JSON keeps `true` and `1` distinct."""
        assert canonicalize(ACTION, {"f": True}) != canonicalize(ACTION, {"f": 1})

    def test_the_action_is_part_of_the_canonical_form(self):
        assert canonicalize("create_cluster", PARAMS) != canonicalize("delete_cluster", PARAMS)

    def test_nan_is_rejected_rather_than_silently_emitted(self):
        """json.dumps emits bare NaN by default, which is not valid JSON and
        does not round-trip -- better to fail at mint time."""
        with pytest.raises(ValueError):
            canonicalize(ACTION, {"x": float("nan")})


class TestMintAndVerify:
    def test_a_fresh_token_verifies(self):
        assert verify(mint(ACTION, PARAMS), ACTION, PARAMS)

    def test_minting_is_deterministic_for_a_fixed_time(self):
        assert mint(ACTION, PARAMS, issued_at=1000) == mint(ACTION, PARAMS, issued_at=1000)

    def test_verify_returns_the_issued_at(self):
        assert verify(mint(ACTION, PARAMS, issued_at=1000), ACTION, PARAMS, now=1000) == 1000

    def test_equivalent_params_in_a_different_order_still_verify(self):
        token = mint(ACTION, {"a": 1, "b": 2})
        assert verify(token, ACTION, {"b": 2, "a": 1})


class TestParamMismatchIsDetected:
    def test_a_changed_value_fails(self):
        token = mint(ACTION, PARAMS)
        changed = dict(PARAMS, cluster_name="not-osiris")
        with pytest.raises(TokenMismatch):
            verify(token, ACTION, changed)

    def test_an_added_param_fails(self):
        token = mint(ACTION, PARAMS)
        with pytest.raises(TokenMismatch):
            verify(token, ACTION, dict(PARAMS, extra="surprise"))

    def test_a_removed_param_fails(self):
        token = mint(ACTION, PARAMS)
        fewer = {k: v for k, v in PARAMS.items() if k != "region"}
        with pytest.raises(TokenMismatch):
            verify(token, ACTION, fewer)

    def test_a_token_for_one_action_does_not_authorize_another(self):
        """The load-bearing one: a token minted by previewing a delete must
        never authorize a create of the same cluster, and vice versa."""
        token = mint("preview_cluster_delete", PARAMS)
        with pytest.raises(TokenMismatch):
            verify(token, "create_cluster", PARAMS)

    def test_a_flipped_boolean_fails(self):
        token = mint(ACTION, PARAMS)
        flipped = dict(PARAMS, delete_s3_bucketname=False)
        with pytest.raises(TokenMismatch):
            verify(token, ACTION, flipped)


class TestTtl:
    def test_a_token_inside_its_ttl_verifies(self):
        token = mint(ACTION, PARAMS, issued_at=1000)
        assert verify(token, ACTION, PARAMS, now=1000 + 899, ttl_seconds=900)

    def test_a_token_at_exactly_the_ttl_still_verifies(self):
        """Boundary pinned deliberately: an off-by-one here rejects a token
        the operator was told is valid for 15 minutes at 15 minutes."""
        token = mint(ACTION, PARAMS, issued_at=1000)
        assert verify(token, ACTION, PARAMS, now=1900, ttl_seconds=900)

    def test_a_token_one_second_past_the_ttl_is_expired(self):
        token = mint(ACTION, PARAMS, issued_at=1000)
        with pytest.raises(ExpiredToken):
            verify(token, ACTION, PARAMS, now=1901, ttl_seconds=900)

    def test_the_expiry_message_says_how_stale(self):
        token = mint(ACTION, PARAMS, issued_at=1000)
        with pytest.raises(ExpiredToken, match="re-run the preview"):
            verify(token, ACTION, PARAMS, now=5000, ttl_seconds=900)

    def test_the_timestamp_cannot_be_edited_to_extend_the_token(self):
        """issued_at is carried in the clear so verify can read it, so the
        obvious attack on a keyless token is to bump it. It is inside the
        digest too, which is what makes that fail."""
        version, issued_at, digest = mint(ACTION, PARAMS, issued_at=1000).split(".")
        forged = f"{version}.{int(issued_at) + 100000}.{digest}"
        with pytest.raises(TokenMismatch):
            verify(forged, ACTION, PARAMS, now=1000 + 100000)

    def test_a_far_future_token_is_rejected(self):
        """Accepting a future-dated token would let it outlive its window."""
        token = mint(ACTION, PARAMS, issued_at=99000)
        with pytest.raises(MalformedToken, match="future"):
            verify(token, ACTION, PARAMS, now=1000, ttl_seconds=900)


class TestMalformedTokens:
    @pytest.mark.parametrize("bad", ["", "garbage", "v1.1000", "v1.1000.abc.def", "..", "v1..x"])
    def test_structurally_invalid_tokens_are_rejected(self, bad):
        with pytest.raises(MalformedToken):
            verify(bad, ACTION, PARAMS)

    def test_a_non_string_is_rejected_without_a_type_error(self):
        """A tool wrapper receiving a bad JSON type must get a
        ConfirmationTokenError it can translate, not a raw TypeError."""
        with pytest.raises(MalformedToken):
            verify(None, ACTION, PARAMS)

    def test_a_foreign_version_is_rejected_and_says_why(self):
        """Version skew across the read-only / stack-mutation deployment
        split is a false negative by design; the message has to make that
        diagnosable rather than looking like a corrupt token."""
        _v, issued_at, digest = mint(ACTION, PARAMS).split(".")
        with pytest.raises(MalformedToken, match="different build"):
            verify(f"v99.{issued_at}.{digest}", ACTION, PARAMS)

    def test_every_failure_is_one_catchable_family(self):
        """A wrapper should be able to translate the whole family into one
        shaped MCP error without enumerating subclasses."""
        for exc in (MalformedToken, ExpiredToken, TokenMismatch):
            assert issubclass(exc, ConfirmationTokenError)
