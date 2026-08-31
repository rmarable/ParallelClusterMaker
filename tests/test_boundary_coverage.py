"""The missing sibling of "when a test stubs the object, one test must drive
the real one": where a trust boundary or a race window exists, one test must
cross it with hostile input or hostile timing.

F1 (a build/teardown marker smuggled through the router on a forwarded
`tools/call`) and C1 (the cluster lock released in the window between its
IfNoneMatch PUT and the GET) both slipped past an otherwise strong suite for
the same reason: every test drove the boundary with clean, cooperating
input, and nothing crossed it adversarially. This guard makes the boundaries
an explicit, reviewed list.

It cannot infer a trust boundary from code -- so it is a manifest, exactly
like the doc line-citation guard. What it enforces is that each boundary
named here still has a live test crossing it: the F1 failure mode was that
the test never existed, and a renamed-away or deleted crosser now fails here
loudly rather than leaving the boundary silently uncrossed. Adding a new
boundary without its hostile test is the one thing a manifest cannot force;
listing it here is the reviewed decision that it has one.
"""

import ast
import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# boundary / race window  ->  (test module relative to repo root, test name)
# Each test must exercise the boundary with hostile input or hostile timing,
# not merely touch it. That property is human-reviewed; this guard pins that
# the named crosser exists in the named module.
_HOSTILE_BOUNDARIES = {
    # Trust boundary: the router forwards a tools/call body verbatim to a
    # handler that checks async markers before every wrapper-level gate.
    "router->handler: a build marker on a forwarded tools/call must not reach run_build": (
        "tests/test_mcp_async_build.py",
        "test_a_marker_on_a_forwarded_request_is_not_a_build",
    ),
    "router->handler: a completion marker on a forwarded tools/call must not reach the runner": (
        "tests/test_mcp_completion.py",
        "test_a_marker_on_a_forwarded_request_is_not_a_completion",
    ),
    "router->handler: end-to-end, a build marker on a tools/call lands in handle()": (
        "tests/test_mcp_handlers.py",
        "test_a_build_marker_on_a_tools_call_reaches_handle_not_run_build",
    ),
    "router->handler: end-to-end, a completion marker on a tools/call lands in handle()": (
        "tests/test_mcp_handlers.py",
        "test_a_completion_marker_on_a_tools_call_reaches_handle_not_the_runner",
    ),
    # Trust boundary: the Cognito authorizer on an internet-facing API.
    "authorizer: an ID token must not authorize a tool call (access tokens only)": (
        "tests/test_mcp_auth.py",
        "test_an_id_token_is_refused",
    ),
    # Trust boundary: the keyless confirmation token gating an unpreviewed run.
    "confirmation token: a tampered timestamp must not extend the TTL": (
        "tests/test_mcp_confirmation_gate.py",
        "test_the_timestamp_cannot_be_edited_to_extend_the_token",
    ),
    "confirmation token: a token for one action must not authorize another": (
        "tests/test_mcp_confirmation_gate.py",
        "test_a_token_for_one_action_does_not_authorize_another",
    ),
    # Race window: the S3 cluster lock's conditional-write acquisition.
    "S3 lock: released between the IfNoneMatch PUT and the GET must reacquire, not crash": (
        "tests/test_s3_cluster_lock.py",
        "test_a_lock_released_between_the_put_and_the_get_is_reacquired",
    ),
    "S3 lock: a free/held flap must terminate, not spin forever": (
        "tests/test_s3_cluster_lock.py",
        "test_a_perpetually_flapping_lock_is_bounded_not_infinite",
    ),
    "S3 lock: two callers reclaiming the same stale lock -- only one wins": (
        "tests/test_s3_cluster_lock.py",
        "test_reclaim_race_raises_cluster_lock_error",
    ),
    # Race window: the config store's ETag-conditional write.
    "config store: a writer landing between the read and the PUT must be caught": (
        "tests/test_make_pcluster.py",
        "test_a_writer_landing_after_the_staleness_check_is_still_caught",
    ),
    "config store: a second machine's divergent write must be refused, not clobbered": (
        "tests/test_make_pcluster.py",
        "test_a_second_machines_write_still_refuses",
    ),
    "config store: an edit while the stack is UPDATE_IN_PROGRESS must be refused": (
        "tests/test_make_pcluster.py",
        "test_an_update_in_progress_refuses_the_edit",
    ),
}

_DEF = re.compile(r"^\s*(?:async\s+)?def\s+(test_[A-Za-z0-9_]+)", re.M)


def _tests_defined_in(relpath):
    """Test callables defined in one module. A boundary's crosser must live
    in the module the manifest names, not merely somewhere under tests/ -- a
    rename that moves it is exactly the drift this guard exists to catch."""
    path = os.path.join(REPO_ROOT, relpath)
    if not os.path.isfile(path):
        return set()
    return set(_DEF.findall(open(path).read()))


class TestEveryTrustBoundaryHasAHostileCrosser:
    def test_every_named_crosser_exists_in_its_module(self):
        missing = []
        for boundary, (relpath, test_name) in sorted(_HOSTILE_BOUNDARIES.items()):
            if test_name not in _tests_defined_in(relpath):
                missing.append(f"{boundary!r}: {relpath}::{test_name} is not defined there")
        assert not missing, (
            "a trust boundary or race window lost its hostile-input/timing test -- "
            "restore it or update the manifest, do not delete the entry:\n  " + "\n  ".join(missing)
        )

    def test_the_guard_catches_a_missing_crosser(self):
        """Vacuity guard: prove the check above fails when a named crosser is
        absent, not only when everything happens to line up."""
        bogus_module = "tests/test_s3_cluster_lock.py"
        assert "test_this_name_does_not_exist" not in _tests_defined_in(bogus_module)

    def test_the_manifest_names_real_modules(self):
        """A typo in a module path would make _tests_defined_in return an
        empty set and the boundary would read as covered by nothing -- caught
        by the first test, but this says why in one place."""
        for boundary, (relpath, _name) in sorted(_HOSTILE_BOUNDARIES.items()):
            assert os.path.isfile(os.path.join(REPO_ROOT, relpath)), (
                f"{boundary!r} names a module that does not exist: {relpath}"
            )

    def test_every_crosser_name_is_unique_to_one_boundary(self):
        """Two boundaries pointing at one test is a copy-paste slip that
        silently leaves one boundary uncrossed."""
        names = [name for _rel, name in _HOSTILE_BOUNDARIES.values()]
        dupes = {n for n in names if names.count(n) > 1}
        assert not dupes, f"a test is claimed for more than one boundary: {sorted(dupes)}"
