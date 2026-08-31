"""Direct tests for manage_pcluster_queue.py's CLI-side helpers.

The queue editing logic itself lives in pcluster_core and is covered by
tests/test_queue_config.py; what is unique to this shim is how it resolves
the shared store before handing it to the core.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from entrypoint_harness import load_entrypoint  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import pcluster_core  # noqa: E402


class TestAnUnreachableStoreIsAnnounced:
    """There are two ways to lose the shared store and they took different
    paths. `_save_cluster_config` prints "Shared store unreachable" when it
    holds a client and the read fails -- but a failure while *resolving*
    the store (bad credentials, no record, an STS call that will not
    answer) hands it no client at all, so it took the no-store path and
    said nothing. The edit landed locally and the operator had no signal it
    had not propagated.

    Verified with deliberately invalid credentials before the fix: the edit
    applied and not one line mentioned the store.
    """

    def _mod(self):
        return load_entrypoint("manage_pcluster_queue.py")

    def test_a_resolution_failure_warns(self, monkeypatch, capsys):
        mod = self._mod()
        monkeypatch.setattr(
            mod,
            "_read_cluster_record",
            lambda name, root: {"region": "us-east-1"},
        )

        def _boom(region):
            raise RuntimeError("InvalidClientTokenId")

        monkeypatch.setattr(mod, "_aws_account_id", _boom)
        s3, bucket = mod._cluster_store("certify")
        out = capsys.readouterr().out
        assert (s3, bucket) == (None, None)
        assert "WARNING" in out and "store unreachable" in out.lower()
        assert "InvalidClientTokenId" in out, (
            "the cause has to be named -- 'unreachable' alone does not tell "
            "the operator whether to fix credentials or a bucket"
        )

    def test_a_missing_region_warns_too(self, monkeypatch, capsys):
        """A record with no region cannot address the bucket at all; that is
        just as silent a loss as a failed call."""
        mod = self._mod()
        monkeypatch.setattr(mod, "_read_cluster_record", lambda name, root: {})
        s3, bucket = mod._cluster_store("certify")
        out = capsys.readouterr().out
        assert (s3, bucket) == (None, None)
        assert "WARNING" in out and "locally only" in out

    def test_the_happy_path_is_quiet(self, monkeypatch, capsys):
        """Vacuity guard: warning on success would train the operator to
        ignore it."""
        mod = self._mod()
        monkeypatch.setattr(
            mod,
            "_read_cluster_record",
            lambda name, root: {"region": "us-east-1"},
        )
        monkeypatch.setattr(mod, "_aws_account_id", lambda region: "123456789012")
        monkeypatch.setattr(
            mod, "_derive_locks_bucket", lambda **kw: "parallelclustermaker-locks-x"
        )
        import boto3

        monkeypatch.setattr(boto3, "client", lambda *a, **k: object())
        s3, bucket = mod._cluster_store("certify")
        assert bucket == "parallelclustermaker-locks-x"
        assert "WARNING" not in capsys.readouterr().out


class TestTheQueueReminderReadsAsEnglish:
    """`action` already carries its preposition, so the format string must
    not add one. Both call sites printed "added to in <path>" and "removed
    from in <path>" for the life of the script -- observed on cluster
    stageb while certifying the store-unreachable warning.

    Small, but this is the line an operator reads immediately after every
    queue edit, and it is the one telling them which file to apply.
    """

    _ACTIONS = ("added to", "removed from")

    def _reminder(self, action, capsys):
        pcluster_core._print_update_reminder("c1", "us-east-1", "q1", action)
        return capsys.readouterr().out

    @pytest.mark.parametrize("action", _ACTIONS)
    def test_no_doubled_preposition(self, action, capsys):
        out = self._reminder(action, capsys)
        assert f"{action} in " not in out, f'reads "{action} in <path>"'

    @pytest.mark.parametrize("action", _ACTIONS)
    def test_it_still_names_the_queue_and_the_file(self, action, capsys):
        """The vacuity guard: deleting the sentence would also pass the
        test above, and that sentence is what tells the operator which file
        to hand to `pcluster update-cluster`."""
        out = self._reminder(action, capsys)
        assert '"q1"' in out
        assert "active_clusters/c1/config.c1" in out
        assert action in out

    @pytest.mark.parametrize("action", _ACTIONS)
    def test_the_whole_sentence_is_well_formed(self, action, capsys):
        out = self._reminder(action, capsys)
        expected = f'Queue "q1" {action} active_clusters/c1/config.c1'
        assert expected in out, f"expected {expected!r}"
