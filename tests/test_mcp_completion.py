"""The bound on the self-re-invoking teardown poller.

A Lambda that re-invokes itself has no natural end. This is the part of the
async design that can run up a bill or never stop, and it is the part a live
test exercises worst -- a real teardown reaches a terminal state in fifteen
minutes and never visits the cases that matter.
"""

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from mcp_server.completion import (  # noqa: E402
    DEADLINE_SECONDS,
    MAX_ATTEMPTS,
    decide,
    is_completion_event,
    make_completion_event,
    next_payload,
)


class TestTheLoopTerminates:
    """Three independent bounds, because one is a single point of failure."""

    def test_a_gone_stack_finalizes(self):
        d = decide(status="DELETE_COMPLETE", attempt=1, started_at=0, now=60)
        assert d.action == "finalize"

    def test_an_absent_stack_finalizes(self):
        """`describe_cluster` answering with no status at all is the normal
        end state once CloudFormation has removed the stack."""
        assert decide(status="", attempt=1, started_at=0, now=60).action == "finalize"

    def test_attempts_are_capped(self):
        d = decide(status="DELETE_IN_PROGRESS", attempt=MAX_ATTEMPTS, started_at=0, now=1)
        assert d.action == "give_up"
        assert "attempts" in d.reason

    def test_the_deadline_is_enforced_even_with_attempts_left(self):
        """The two bounds are not redundant. A fast poll loop exhausts
        attempts first; a slow or stalled one exhausts the clock first, and
        either alone leaves the other case unbounded."""
        d = decide(status="DELETE_IN_PROGRESS", attempt=1, started_at=0, now=DEADLINE_SECONDS + 1)
        assert d.action == "give_up"
        assert "deadline" in d.reason

    def test_an_unreadable_status_keeps_waiting_but_is_still_bounded(self):
        """A describe that could not be answered is not evidence the stack
        is gone -- the rule `_confirm_stack_is_gone` is built on. It waits,
        and the same two bounds stop it waiting forever."""
        assert decide(status=None, attempt=1, started_at=0, now=60).action == "retry"
        assert decide(status=None, attempt=MAX_ATTEMPTS, started_at=0, now=60).action == "give_up"
        assert (
            decide(status=None, attempt=1, started_at=0, now=DEADLINE_SECONDS + 1).action
            == "give_up"
        )

    def test_a_failed_delete_does_not_finalize(self):
        """DELETE_FAILED means the stack is still there and someone has to
        look. Finalizing would strip the IAM and credentials needed to
        investigate -- the guard `core_delete_cluster` already applies, kept
        here so the automatic path cannot bypass it."""
        d = decide(status="DELETE_FAILED", attempt=1, started_at=0, now=60)
        assert d.action == "give_up"

    def test_every_status_reaches_a_terminal_decision(self):
        """Exhaustive over the states CloudFormation can report: no input
        leaves the loop running past its bounds."""
        states = [
            None,
            "",
            "DELETE_IN_PROGRESS",
            "DELETE_COMPLETE",
            "DELETE_FAILED",
            "CREATE_COMPLETE",
            "UPDATE_IN_PROGRESS",
            "ROLLBACK_COMPLETE",
            "nonsense",
        ]
        for s in states:
            at_cap = decide(status=s, attempt=MAX_ATTEMPTS, started_at=0, now=1)
            assert at_cap.action in ("finalize", "give_up"), (s, at_cap)
            past_deadline = decide(status=s, attempt=0, started_at=0, now=DEADLINE_SECONDS + 1)
            assert past_deadline.action in ("finalize", "give_up"), (s, past_deadline)

    def test_the_attempt_counter_actually_advances(self):
        """Vacuity guard for the cap: a payload that never increments makes
        every bound above unreachable."""
        p = make_completion_event(
            cluster_name="osiris", cluster_owner="rm", region="us-east-1", started_at=0
        )
        assert p["attempt"] == 0
        for i in range(1, 5):
            p = next_payload(p)
            assert p["attempt"] == i
        assert p["cluster_name"] == "osiris"

    def test_a_bounded_run_cannot_exceed_the_cap(self):
        """Drive the real decision function in a loop the way the handler
        will, with a status that never becomes terminal."""
        p = make_completion_event(
            cluster_name="osiris", cluster_owner="rm", region="us-east-1", started_at=0
        )
        now, seen = 0.0, 0
        while True:
            d = decide(
                status="DELETE_IN_PROGRESS",
                attempt=p["attempt"],
                started_at=p["started_at"],
                now=now,
            )
            seen += 1
            if d.action != "retry":
                break
            p = next_payload(p)
            now += 60
            assert seen <= MAX_ATTEMPTS + 1, "loop did not terminate"
        assert d.action == "give_up"


class TestTheCompletionEventIsRecognizedByAMarker:
    def test_a_completion_event_is_detected(self):
        ev = make_completion_event(cluster_name="a", cluster_owner="b", region="us-east-1")
        assert is_completion_event(ev)

    def test_a_tools_call_is_not(self):
        """A malformed or unrelated event must not be mistaken for a
        completion poll and start deleting things."""
        for ev in (
            {"jsonrpc": "2.0", "method": "tools/call"},
            {},
            None,
            {"body": "{}"},
            {"_pcm_completion": "yes"},
            {"_pcm_completion": False},
        ):
            assert not is_completion_event(ev), ev

    def test_a_marker_on_a_forwarded_request_is_not_a_completion(self):
        """The F1 bypass: a caller adds the marker to a real `tools/call`
        body, the router forwards it verbatim, and without this the handler
        runs run_completion_attempt -- an unpreviewed, untokened teardown of
        any named cluster. The router only forwards a body carrying a
        `method`, so its presence alongside the marker marks the event as
        forwarded gateway input, not a self-invoke."""
        for ev in (
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "_pcm_completion": True,
                "cluster_name": "victim",
                "cluster_owner": "x",
                "region": "us-east-1",
            },
            {"_pcm_completion": True, "method": "tools/call"},
            {"_pcm_completion": True, "jsonrpc": "2.0"},
        ):
            assert not is_completion_event(ev), ev


class TestTheRunnerActsOnTheDecision:
    """The AWS half. The decision logic is tested exhaustively above; this
    checks that each decision is actually carried out, and -- the part that
    matters most for an unattended path -- that a failure is reported to
    somebody rather than returned to nobody."""

    def _payload(self, attempt=0, started_at=0.0):
        return {
            "_pcm_completion": True,
            "cluster_name": "osiris",
            "cluster_owner": "rm",
            "region": "us-east-1",
            "delete_s3_bucketname": True,
            "attempt": attempt,
            "started_at": started_at,
        }

    def test_a_gone_stack_finalizes_and_does_not_reinvoke(self):
        from mcp_server.completion_runner import run_completion_attempt

        calls, reinvokes = [], []

        class _R:
            success = True
            message = ""

        out = run_completion_attempt(
            self._payload(),
            now=60,
            describe=lambda n, r: "",
            finalize=lambda p: calls.append(p) or _R(),
            reinvoke=lambda p: reinvokes.append(p),
            sleeper=lambda s: None,
        )
        assert out["action"] == "finalize"
        assert len(calls) == 1 and not reinvokes

    def test_a_running_delete_reinvokes_with_an_incremented_attempt(self):
        from mcp_server.completion_runner import run_completion_attempt

        reinvokes, finals = [], []
        out = run_completion_attempt(
            self._payload(attempt=3),
            now=60,
            describe=lambda n, r: "DELETE_IN_PROGRESS",
            finalize=lambda p: finals.append(p),
            reinvoke=lambda p: reinvokes.append(p),
            sleeper=lambda s: None,
        )
        assert out["action"] == "retry"
        assert not finals, "finalized while the stack was still deleting"
        assert len(reinvokes) == 1

    def test_it_never_finalizes_a_failed_delete(self):
        """DELETE_FAILED leaves the stack up. Finalizing strips the IAM and
        credentials needed to investigate."""
        from mcp_server.completion_runner import run_completion_attempt

        finals = []
        out = run_completion_attempt(
            self._payload(),
            now=60,
            describe=lambda n, r: "DELETE_FAILED",
            finalize=lambda p: finals.append(p),
            reinvoke=lambda p: None,
            sleeper=lambda s: None,
        )
        assert out["action"] == "give_up"
        assert not finals

    def test_giving_up_notifies_rather_than_failing_silently(self, monkeypatch):
        """The whole hazard of an unattended path: nobody is waiting on the
        return value, so silence is indistinguishable from success."""
        import mcp_server.completion_runner as cr
        from mcp_server.completion import MAX_ATTEMPTS

        sent = []
        monkeypatch.setattr(cr, "_notify", lambda p, s, m: sent.append((s, m)))
        cr.run_completion_attempt(
            self._payload(attempt=MAX_ATTEMPTS),
            now=60,
            describe=lambda n, r: "DELETE_IN_PROGRESS",
            finalize=lambda p: None,
            reinvoke=lambda p: None,
            sleeper=lambda s: None,
        )
        assert sent, "gave up without telling anyone"
        assert "finalize_cluster_teardown" in sent[0][1], (
            "the notification does not say how to recover"
        )

    def test_a_failed_finalize_notifies_too(self, monkeypatch):
        import mcp_server.completion_runner as cr

        sent = []
        monkeypatch.setattr(cr, "_notify", lambda p, s, m: sent.append((s, m)))

        class _R:
            success = False
            message = "AccessDenied on iam:DeletePolicy"

        cr.run_completion_attempt(
            self._payload(),
            now=60,
            describe=lambda n, r: "",
            finalize=lambda p: _R(),
            reinvoke=lambda p: None,
            sleeper=lambda s: None,
        )
        assert sent, "a failed cleanup told nobody"
        assert "AccessDenied" in sent[0][1]

    def test_an_unreadable_describe_is_not_treated_as_absence(self):
        """The rule `_confirm_stack_is_gone` is built on: a failed AWS call
        is not evidence a stack is gone. Finalizing here would destroy the
        credentials of a cluster that may still be running."""
        from mcp_server.completion_runner import run_completion_attempt

        finals = []
        out = run_completion_attempt(
            self._payload(),
            now=60,
            describe=lambda n, r: None,
            finalize=lambda p: finals.append(p),
            reinvoke=lambda p: None,
            sleeper=lambda s: None,
        )
        assert out["action"] == "retry"
        assert not finals


class TestTheRetryPathReallySleeps:
    """Every other test here injects `sleeper=lambda s: None`, so none of them
    can see that the retry path waits inside the invocation -- the repo's own
    "when a test stubs the object under test, at least one test must drive the
    real one" rule, unapplied to the one seam that costs money.

    Driven with the real `time.sleep` and POLL_SECONDS patched to ~0, so the
    call is real and the test is fast."""

    def test_the_default_sleeper_is_real_time_sleep(self, monkeypatch):
        import time

        from mcp_server import completion_runner as cr

        monkeypatch.setattr(cr, "POLL_SECONDS", 0.01)
        slept = []
        real_sleep = time.sleep

        def spy(seconds):
            slept.append(seconds)
            return real_sleep(seconds)

        monkeypatch.setattr(time, "sleep", spy)
        cr.run_completion_attempt(
            {"cluster_name": "c", "region": "us-east-1", "attempt": 0, "started_at": 0.0},
            now=1.0,
            describe=lambda *a, **k: "DELETE_IN_PROGRESS",
            reinvoke=lambda payload: None,
        )
        assert slept == [0.01], (
            "the retry path no longer sleeps in-process. If that is deliberate "
            "-- SQS DelaySeconds or an EventBridge schedule -- update the notes "
            "in completion.py and completion_runner.py, which describe the wait."
        )
