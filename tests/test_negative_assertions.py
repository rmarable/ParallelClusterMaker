"""A negative assertion that stops reading anything still passes.

`assert needle not in haystack` is the shape most of this repo's "never
reintroduce X" rules take, and it has a failure mode the pass/fail column
cannot show: if the haystack becomes empty, gets truncated, or is read from
a path that no longer exists, the assertion succeeds and the rule it names
goes unguarded -- silently, permanently, and with the suite green.

That is the opposite of how a guard should fail. A regression in the *code*
turns it red; a regression in the *test* must not turn it quietly green.

This was measured rather than assumed: 25 tests assert absence over Python
source, and 17 of them had no positive assertion at all on the same
haystack. This class keeps that number at zero. It is deliberately narrow
-- absence over *runtime output* (`trace`, `r.stdout`, `capsys`) is a
different thing, since a test that captured no output usually fails for
other reasons first.
"""

import ast
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(REPO_ROOT, "tests")

_READS_SOURCE = ("getsource", "pcluster_core.py", '.py")', ".py')", '"*.py"')
_PROVES_HAYSTACK = ("assert_source_is_real", "_uncommented")


def _tests_asserting_absence_over_source():
    """Every test function that reads Python source and asserts absence."""
    out = []
    for fname in sorted(os.listdir(TESTS)):
        if not fname.endswith(".py") or fname == os.path.basename(__file__):
            continue
        tree = ast.parse(open(os.path.join(TESTS, fname)).read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            body = ast.unparse(node)
            if not any(marker in body for marker in _READS_SOURCE):
                continue
            negatives = [
                st
                for st in ast.walk(node)
                if isinstance(st, ast.Assert)
                and isinstance(st.test, ast.Compare)
                and st.test.ops
                and isinstance(st.test.ops[0], ast.NotIn)
            ]
            if negatives:
                out.append((fname, node.name, body, len(negatives)))
    return out


class TestEveryNegativeSourceAssertionProvesItsHaystack:
    def test_none_can_pass_without_reading_anything(self):
        offenders = []
        for fname, name, body, n in _tests_asserting_absence_over_source():
            proves = any(m in body for m in _PROVES_HAYSTACK)
            fn = ast.parse(body).body[0]
            # The haystacks this test asserts absence against.
            haystacks = {
                ast.unparse(st.test.comparators[0])
                for st in ast.walk(fn)
                if isinstance(st, ast.Assert)
                and isinstance(st.test, ast.Compare)
                and st.test.ops
                and isinstance(st.test.ops[0], ast.NotIn)
            }
            # Any of three controls counts, because all three prove the same
            # thing -- that the haystack was actually read: the shared
            # helper, a positive `in` assertion, or a bare truthiness assert
            # on the haystack itself (which is the right control when the
            # haystack is a collection rather than source text).
            positive = any(
                isinstance(st, ast.Assert)
                and isinstance(st.test, ast.Compare)
                and st.test.ops
                and isinstance(st.test.ops[0], ast.In)
                for st in ast.walk(fn)
            )
            truthy = any(
                isinstance(st, ast.Assert) and ast.unparse(st.test) in haystacks
                for st in ast.walk(fn)
            )
            if not (proves or positive or truthy):
                offenders.append(f"{fname}::{name} ({n} negative assertion(s))")
        assert offenders == [], (
            "negative assertion(s) over Python source with nothing proving the "
            "haystack was read. Add `assert_source_is_real(src, label)` from "
            "conftest, or assert something that IS present:\n  " + "\n  ".join(offenders)
        )

    def test_the_sweep_actually_finds_tests(self):
        """Vacuity guard. If the detection stops matching -- a renamed
        helper, a changed idiom -- this class silently guards nothing, which
        is the exact failure it exists to prevent."""
        found = _tests_asserting_absence_over_source()
        assert len(found) >= 15, (
            f"only {len(found)} tests matched; the detector has probably "
            f"stopped recognising the pattern it is meant to sweep"
        )


class TestTheHelperItselfActuallyProvesSomething:
    """The guard needs a guard.

    `assert_source_is_real` exists so a negative assertion cannot pass
    against nothing -- but if its own checks were weakened to `pass`, every
    call site would keep passing and the class above would still be green,
    because that class only asks whether the helper is *called*. Caught by
    mutation: replacing the emptiness check survived the entire suite.
    """

    def test_it_rejects_an_empty_haystack(self):
        import pytest

        from conftest import assert_source_is_real

        for empty in ("", "   ", "\n\n"):
            with pytest.raises(AssertionError):
                assert_source_is_real(empty, "probe")

    def test_it_rejects_something_that_is_not_source(self):
        import pytest

        from conftest import assert_source_is_real

        with pytest.raises(AssertionError):
            assert_source_is_real("404 Not Found", "probe")

    def test_it_accepts_real_source(self):
        """Vacuity guard: a helper that rejected everything would satisfy
        both tests above and break every call site."""
        import inspect

        from conftest import assert_source_is_real

        import pcluster_core

        assert_source_is_real(inspect.getsource(pcluster_core), "pcluster_core")
