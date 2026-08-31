"""
Direct tests for Workstream 4's first slice: the S3-backed distributed
cluster lock (_derive_locks_bucket, _create_locks_bucket,
s3_acquire_cluster_lock, s3_release_cluster_lock in pcluster_core.py).

Built round 26, wired into core_create_cluster/core_delete_cluster round
27 (the local mkdir lock it replaced is deleted). See pcluster_core.py's
own comment on this module for the design rationale.

These tests use a fake S3 client: they pin the *logic* (which branch is
taken, what gets written, what gets raised), not S3's actual
concurrent-write behavior, which a synchronous fake cannot reproduce. The
atomicity claim itself was verified separately against real S3 on
2026-08-21 (8 concurrent writers, exactly one winner in the acquire race,
the reclaim race, and through s3_acquire_cluster_lock itself) -- see
docs/sessions.md round 29. That run is what confirmed the 409
ConditionalRequestConflict shape _is_conditional_write_rejection handles
alongside 412 is real and reachable, which is precisely the class of thing
this file's fakes are structurally unable to test.
"""

import io
import os
import sys
from datetime import datetime as DateTime, timedelta, timezone

import pytest
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import pcluster_core
from pcluster_core import (
    ClusterLockError,
    _acquire_distributed_cluster_lock,
    _create_locks_bucket,
    _derive_locks_bucket,
    _derive_mcp_user_pool_name,
    _derive_results_bucket,
    _is_conditional_write_rejection,
    _lock_key,
    _lock_owner_body,
    _S3_BUCKET_NAME_MAX,
    s3_acquire_cluster_lock,
    s3_release_cluster_lock,
)
from pcluster.api.errors import NotFoundException

CLUSTER = "lockme"


def _client_error(code, op, status=None):
    response = {"Error": {"Code": code, "Message": ""}}
    if status is not None:
        response["ResponseMetadata"] = {"HTTPStatusCode": status}
    return ClientError(response, op)


class _FakeS3Lock:
    """Models S3's own conditional-write contract closely enough to exercise
    every branch in s3_acquire_cluster_lock: IfNoneMatch='*' fails with 412
    when the key exists; IfMatch=<etag> fails with 412 when the stored etag
    doesn't match (including "doesn't exist at all", which real S3 also
    rejects for IfMatch). Does not model the 409 ConditionalRequestConflict
    same-instant-race shape -- that is a live-S3-only behavior, not
    something a synchronous fake can reproduce meaningfully."""

    def __init__(self):
        self.objects = {}
        self._etag_counter = 0
        self.created_buckets = []
        self.public_access_blocked = []
        self.race_on_next_get = False
        self.release_on_next_get = False
        self.existing_buckets = set()

    def head_bucket(self, Bucket):
        """Modeled on the service contract: botocore's s3 service-2.json
        gives HeadBucket exactly one error, NoSuchBucket, at HTTP 404."""
        if Bucket not in self.existing_buckets:
            raise _client_error("NoSuchBucket", "HeadBucket", status=404)
        return {}

    def create_bucket(self, **kwargs):
        self.created_buckets.append(kwargs)
        self.existing_buckets.add(kwargs["Bucket"])

    def put_public_access_block(self, **kwargs):
        self.public_access_blocked.append(kwargs)

    def _new_etag(self):
        self._etag_counter += 1
        return f"etag-{self._etag_counter}"

    def put_object(self, Bucket, Key, Body, IfNoneMatch=None, IfMatch=None):
        existing = self.objects.get(Key)
        if IfNoneMatch == "*" and existing is not None:
            raise _client_error("PreconditionFailed", "PutObject", status=412)
        if IfMatch is not None and (existing is None or existing["etag"] != IfMatch):
            raise _client_error("PreconditionFailed", "PutObject", status=412)
        etag = self._new_etag()
        self.objects[Key] = {
            "body": Body,
            "etag": etag,
            "last_modified": DateTime.now(timezone.utc),
        }
        return {"ETag": etag}

    def set_last_modified(self, key, when):
        self.objects[key]["last_modified"] = when

    def get_object(self, Bucket, Key):
        if self.release_on_next_get:
            # The holder released the lock between our failed IfNoneMatch PUT
            # and this read -- the key is gone by the time we look.
            self.release_on_next_get = False
            self.objects.pop(Key, None)
        obj = self.objects.get(Key)
        if obj is None:
            # Contract-faithful: real S3 GetObject on a missing key returns
            # NoSuchKey / 404, not a Python KeyError. The old fake did the
            # dict lookup bare, so the release-during-acquire path could not
            # be modeled here at all.
            raise _client_error("NoSuchKey", "GetObject", status=404)
        result = {
            "ETag": obj["etag"],
            "LastModified": obj["last_modified"],
            "Body": io.BytesIO(obj["body"]),
        }
        if self.race_on_next_get:
            # Simulate a second writer's reclaim landing entirely between our
            # read (which legitimately returns the etag current at read
            # time) and our own reclaim write: by the time we PutObject with
            # that etag, the store has already moved on.
            self.race_on_next_get = False
            obj["etag"] = self._new_etag()
        return result

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)


class _ScriptedDescribe:
    def __init__(self, status=None, raises=None):
        self.status = status
        self.raises = raises
        self.calls = []

    def __call__(self, cluster_name, region):
        self.calls.append((cluster_name, region))
        if self.raises:
            raise self.raises
        return {"clusterStatus": self.status}


class TestDeriveLocksBucket:
    def test_normal_derivation(self):
        assert (
            _derive_locks_bucket(aws_account_id="123456789012", region="us-east-2")
            == "parallelclustermaker-locks-123456789012-us-east-2"
        )

    def test_too_long_name_exits(self):
        with pytest.raises(SystemExit):
            _derive_locks_bucket(aws_account_id="1" * 40, region="us-east-2000000000")

    def test_derivation_reads_only_account_and_region(self):
        """Keyword-only, matching _derive_results_bucket's own guard against
        a cluster- or serial-derived input silently restoring a per-build
        bucket."""
        import inspect

        params = list(inspect.signature(_derive_locks_bucket).parameters)
        assert params == ["aws_account_id", "region"]
        assert (
            inspect.signature(_derive_locks_bucket).parameters["aws_account_id"].kind
            == inspect.Parameter.KEYWORD_ONLY
        )


class TestCreateLocksBucket:
    def test_creates_and_blocks_public_access(self):
        s3 = _FakeS3Lock()
        _create_locks_bucket(
            s3, locks_bucketname="parallelclustermaker-locks-123-us-east-2", region="us-east-2"
        )
        assert s3.created_buckets == [
            {
                "Bucket": "parallelclustermaker-locks-123-us-east-2",
                "CreateBucketConfiguration": {"LocationConstraint": "us-east-2"},
            }
        ]
        assert len(s3.public_access_blocked) == 1

    def test_omits_location_constraint_for_us_east_1(self):
        s3 = _FakeS3Lock()
        _create_locks_bucket(
            s3, locks_bucketname="parallelclustermaker-locks-123-us-east-1", region="us-east-1"
        )
        assert "CreateBucketConfiguration" not in s3.created_buckets[0]

    def test_idempotent_on_already_owned(self):
        class _AlreadyOwned(_FakeS3Lock):
            def create_bucket(self, **kwargs):
                raise _client_error("BucketAlreadyOwnedByYou", "CreateBucket")

        s3 = _AlreadyOwned()
        _create_locks_bucket(s3, locks_bucketname="x", region="us-east-1")  # must not raise
        assert len(s3.public_access_blocked) == 1

    def test_an_existing_bucket_is_neither_created_nor_reconfigured(self):
        """The locks bucket is long-lived and account-wide, so in any real
        deployment it already exists. Issuing CreateBucket anyway is what
        broke the first live create_cluster through a least-privilege Lambda
        role, which is correctly denied s3:CreateBucket."""
        s3 = _FakeS3Lock()
        s3.existing_buckets.add("parallelclustermaker-locks-123-us-east-1")
        _create_locks_bucket(
            s3,
            locks_bucketname="parallelclustermaker-locks-123-us-east-1",
            region="us-east-1",
        )
        assert s3.created_buckets == []
        assert s3.public_access_blocked == []

    def test_a_role_that_cannot_create_buckets_still_takes_the_lock(self):
        """The live failure, as a test: AccessDenied on CreateBucket for a
        bucket that already exists and that the role can read and write."""

        class _CannotCreate(_FakeS3Lock):
            def create_bucket(self, **kwargs):
                raise _client_error("AccessDenied", "CreateBucket", status=403)

            def put_public_access_block(self, **kwargs):
                raise _client_error("AccessDenied", "PutPublicAccessBlock", status=403)

        s3 = _CannotCreate()
        s3.existing_buckets.add("x")
        _create_locks_bucket(s3, locks_bucketname="x", region="us-east-1")

    def test_an_absent_bucket_is_still_created(self):
        """The vacuity guard: the fix must not become "never create it"."""
        s3 = _FakeS3Lock()
        _create_locks_bucket(s3, locks_bucketname="brand-new", region="us-east-1")
        assert [b["Bucket"] for b in s3.created_buckets] == ["brand-new"]
        assert len(s3.public_access_blocked) == 1

    def test_other_client_errors_propagate(self):
        class _Denied(_FakeS3Lock):
            def create_bucket(self, **kwargs):
                raise _client_error("AccessDenied", "CreateBucket")

        with pytest.raises(ClientError):
            _create_locks_bucket(_Denied(), locks_bucketname="x", region="us-east-1")


class TestIsConditionalWriteRejection:
    def test_412_by_code(self):
        assert _is_conditional_write_rejection(_client_error("PreconditionFailed", "PutObject"))

    def test_409_by_code(self):
        assert _is_conditional_write_rejection(
            _client_error("ConditionalRequestConflict", "PutObject")
        )

    def test_412_by_status_only(self):
        assert _is_conditional_write_rejection(
            _client_error("SomethingElse", "PutObject", status=412)
        )

    def test_409_by_status_only(self):
        assert _is_conditional_write_rejection(
            _client_error("SomethingElse", "PutObject", status=409)
        )

    def test_unrelated_error_is_not_a_rejection(self):
        assert not _is_conditional_write_rejection(
            _client_error("AccessDenied", "PutObject", status=403)
        )


class TestLockKeyAndOwnerBody:
    def test_lock_key_is_namespaced(self):
        assert _lock_key(CLUSTER) == f"locks/{CLUSTER}.lock"

    def test_owner_body_is_json_with_pid_host_command(self):
        import json

        body = json.loads(_lock_owner_body(command="make_pcluster.py -N lockme"))
        assert body["pid"] == os.getpid()
        assert body["command"] == "make_pcluster.py -N lockme"
        assert "started" in body and "host" in body


class TestS3AcquireClusterLock:
    def test_acquires_when_absent(self):
        s3 = _FakeS3Lock()
        key = s3_acquire_cluster_lock(
            s3,
            locks_bucketname="b",
            cluster_name=CLUSTER,
            command="make_pcluster.py -N lockme",
        )
        assert key == _lock_key(CLUSTER)
        assert key in s3.objects

    def test_fails_when_held_and_fresh_no_describe_fn(self):
        s3 = _FakeS3Lock()
        s3_acquire_cluster_lock(s3, locks_bucketname="b", cluster_name=CLUSTER, command="first")
        with pytest.raises(ClusterLockError, match="Another operation"):
            s3_acquire_cluster_lock(
                s3, locks_bucketname="b", cluster_name=CLUSTER, command="second"
            )

    def test_fails_when_held_and_stale_but_no_describe_fn_supplied(self):
        """Fail safe: a caller with no way to check terminal state (e.g. no
        AWS credentials yet) must not auto-reclaim on age alone."""
        s3 = _FakeS3Lock()
        s3_acquire_cluster_lock(s3, locks_bucketname="b", cluster_name=CLUSTER, command="first")
        s3.set_last_modified(_lock_key(CLUSTER), DateTime.now(timezone.utc) - timedelta(hours=10))
        with pytest.raises(ClusterLockError):
            s3_acquire_cluster_lock(
                s3,
                locks_bucketname="b",
                cluster_name=CLUSTER,
                command="second",
                staleness_ceiling_seconds=7200,
            )

    def test_fails_when_stale_but_cluster_not_yet_terminal(self):
        s3 = _FakeS3Lock()
        s3_acquire_cluster_lock(s3, locks_bucketname="b", cluster_name=CLUSTER, command="first")
        s3.set_last_modified(_lock_key(CLUSTER), DateTime.now(timezone.utc) - timedelta(hours=10))
        describe = _ScriptedDescribe(status="CREATE_IN_PROGRESS")
        with pytest.raises(ClusterLockError):
            s3_acquire_cluster_lock(
                s3,
                locks_bucketname="b",
                cluster_name=CLUSTER,
                command="second",
                describe_fn=describe,
                region="us-east-2",
                staleness_ceiling_seconds=7200,
            )
        assert describe.calls == [(CLUSTER, "us-east-2")]

    def test_fails_when_fresh_even_with_terminal_describe_fn(self):
        """Age gates the check before status does -- a fresh lock on a
        cluster that happens to already be terminal (e.g. a build that just
        finished and hasn't released yet) must not be reclaimed out from
        under the still-running caller."""
        s3 = _FakeS3Lock()
        s3_acquire_cluster_lock(s3, locks_bucketname="b", cluster_name=CLUSTER, command="first")
        describe = _ScriptedDescribe(status="CREATE_COMPLETE")
        with pytest.raises(ClusterLockError):
            s3_acquire_cluster_lock(
                s3,
                locks_bucketname="b",
                cluster_name=CLUSTER,
                command="second",
                describe_fn=describe,
                region="us-east-2",
            )
        assert describe.calls == [], "must not even check status while the lock is still fresh"

    def test_reclaims_stale_terminal_lock(self, capsys):
        s3 = _FakeS3Lock()
        s3_acquire_cluster_lock(s3, locks_bucketname="b", cluster_name=CLUSTER, command="first")
        s3.set_last_modified(_lock_key(CLUSTER), DateTime.now(timezone.utc) - timedelta(hours=10))
        describe = _ScriptedDescribe(status="CREATE_FAILED")
        key = s3_acquire_cluster_lock(
            s3,
            locks_bucketname="b",
            cluster_name=CLUSTER,
            command="second",
            describe_fn=describe,
            region="us-east-2",
            staleness_ceiling_seconds=7200,
        )
        assert key == _lock_key(CLUSTER)
        import json

        assert json.loads(s3.objects[key]["body"])["command"] == "second"
        assert "Reclaimed a stale lock" in capsys.readouterr().out

    def test_reclaims_when_cluster_is_gone(self):
        """NotFoundException means the cluster is fully torn down -- as
        terminal as it gets."""
        s3 = _FakeS3Lock()
        s3_acquire_cluster_lock(s3, locks_bucketname="b", cluster_name=CLUSTER, command="first")
        s3.set_last_modified(_lock_key(CLUSTER), DateTime.now(timezone.utc) - timedelta(hours=10))
        describe = _ScriptedDescribe(raises=NotFoundException("gone"))
        key = s3_acquire_cluster_lock(
            s3,
            locks_bucketname="b",
            cluster_name=CLUSTER,
            command="second",
            describe_fn=describe,
            region="us-east-2",
            staleness_ceiling_seconds=7200,
        )
        assert key == _lock_key(CLUSTER)

    def test_describe_fn_call_signature_matches_pcluster_lib(self):
        """describe_fn must be called exactly like pc.describe_cluster is
        called elsewhere in this file (cluster_name=, region=, both
        keyword) -- a positional call would break the real integration
        silently, since a fake accepting *args would never catch it."""
        s3 = _FakeS3Lock()
        s3_acquire_cluster_lock(s3, locks_bucketname="b", cluster_name=CLUSTER, command="first")
        s3.set_last_modified(_lock_key(CLUSTER), DateTime.now(timezone.utc) - timedelta(hours=10))

        def _strict_describe(*, cluster_name, region):
            return {"clusterStatus": "CREATE_COMPLETE"}

        s3_acquire_cluster_lock(
            s3,
            locks_bucketname="b",
            cluster_name=CLUSTER,
            command="second",
            describe_fn=_strict_describe,
            region="us-east-2",
            staleness_ceiling_seconds=7200,
        )

    def test_reclaim_race_raises_cluster_lock_error(self):
        """Two callers independently concluding a lock is stale at the same
        moment must not both succeed -- the second reclaim's IfMatch loses
        against the first reclaim's fresh etag, and must surface as a
        ClusterLockError telling the caller to retry, not an unhandled
        ClientError."""
        s3 = _FakeS3Lock()
        s3_acquire_cluster_lock(s3, locks_bucketname="b", cluster_name=CLUSTER, command="first")
        s3.set_last_modified(_lock_key(CLUSTER), DateTime.now(timezone.utc) - timedelta(hours=10))
        s3.race_on_next_get = True
        describe = _ScriptedDescribe(status="CREATE_FAILED")
        with pytest.raises(ClusterLockError, match="reclaimed it first"):
            s3_acquire_cluster_lock(
                s3,
                locks_bucketname="b",
                cluster_name=CLUSTER,
                command="second",
                describe_fn=describe,
                region="us-east-2",
                staleness_ceiling_seconds=7200,
            )

    def test_a_lock_released_between_the_put_and_the_get_is_reacquired(self):
        """C1: the IfNoneMatch PUT fails because the lock is held, but the
        holder releases it before the GET -- so the lock is free by the time
        we look. The acquire must re-attempt and win, not let the GET's
        NoSuchKey escape as an opaque failure under exactly the contention
        the lock exists for. _is_missing_key_rejection already documents the
        reading: a vanished object means nobody holds the lock."""
        s3 = _FakeS3Lock()
        s3.objects[_lock_key(CLUSTER)] = {
            "body": b"{}",
            "etag": "e0",
            "last_modified": DateTime.now(timezone.utc),
        }
        s3.release_on_next_get = True
        key = s3_acquire_cluster_lock(
            s3, locks_bucketname="b", cluster_name=CLUSTER, command="mine"
        )
        assert key == _lock_key(CLUSTER)
        assert _lock_key(CLUSTER) in s3.objects  # this caller now holds it

    def test_a_perpetually_flapping_lock_is_bounded_not_infinite(self):
        """The retry is bounded: a pathological free/held flap (every PUT
        finds it held, every GET finds it gone) must terminate with a
        ClusterLockError, never spin forever."""

        class _Flap(_FakeS3Lock):
            def put_object(self, **kwargs):
                if kwargs.get("IfNoneMatch") == "*":
                    raise _client_error("PreconditionFailed", "PutObject", status=412)
                return super().put_object(**kwargs)

            def get_object(self, Bucket, Key):
                raise _client_error("NoSuchKey", "GetObject", status=404)

        with pytest.raises(ClusterLockError, match="repeatedly acquired and released"):
            s3_acquire_cluster_lock(
                _Flap(), locks_bucketname="b", cluster_name=CLUSTER, command="mine"
            )

    def test_unrelated_put_object_error_propagates(self):
        class _Denied(_FakeS3Lock):
            def put_object(self, **kwargs):
                raise _client_error("AccessDenied", "PutObject", status=403)

        with pytest.raises(ClientError):
            s3_acquire_cluster_lock(
                _Denied(), locks_bucketname="b", cluster_name=CLUSTER, command="first"
            )

    def test_an_unrelated_get_object_error_still_propagates(self):
        """The retry is scoped to NoSuchKey. A denied or transient GET is not
        evidence the lock is free and must not be swallowed into a retry."""

        class _DeniedGet(_FakeS3Lock):
            def get_object(self, Bucket, Key):
                raise _client_error("AccessDenied", "GetObject", status=403)

        s3 = _DeniedGet()
        s3.objects[_lock_key(CLUSTER)] = {
            "body": b"{}",
            "etag": "e0",
            "last_modified": DateTime.now(timezone.utc),
        }
        with pytest.raises(ClientError):
            s3_acquire_cluster_lock(s3, locks_bucketname="b", cluster_name=CLUSTER, command="mine")

    def test_unexpected_describe_exception_is_treated_as_not_terminal(self):
        """An ambiguous describe_cluster failure (network error, transient
        AWS issue) must not be read as evidence the cluster is done --
        failing to reclaim (and telling the caller to retry the whole
        operation) is the safe default, not guessing terminal."""
        s3 = _FakeS3Lock()
        s3_acquire_cluster_lock(s3, locks_bucketname="b", cluster_name=CLUSTER, command="first")
        s3.set_last_modified(_lock_key(CLUSTER), DateTime.now(timezone.utc) - timedelta(hours=10))
        describe = _ScriptedDescribe(raises=RuntimeError("network blip"))
        with pytest.raises(ClusterLockError):
            s3_acquire_cluster_lock(
                s3,
                locks_bucketname="b",
                cluster_name=CLUSTER,
                command="second",
                describe_fn=describe,
                region="us-east-2",
                staleness_ceiling_seconds=7200,
            )


class TestS3ReleaseClusterLock:
    def test_release_deletes_the_object(self):
        s3 = _FakeS3Lock()
        s3_acquire_cluster_lock(s3, locks_bucketname="b", cluster_name=CLUSTER, command="first")
        s3_release_cluster_lock(s3, locks_bucketname="b", cluster_name=CLUSTER)
        assert _lock_key(CLUSTER) not in s3.objects

    def test_release_is_safe_when_already_gone(self):
        s3 = _FakeS3Lock()
        s3_release_cluster_lock(s3, locks_bucketname="b", cluster_name=CLUSTER)  # must not raise

    def test_release_swallows_a_client_error(self):
        """Release is best-effort cleanup, often the last statement before a
        caller returns or exits -- a raised exception here would mask
        whatever the caller was actually reporting, and a leaked lock is
        recovered automatically by the staleness/reclaim path anyway."""

        class _Denied(_FakeS3Lock):
            def delete_object(self, **kwargs):
                raise _client_error("AccessDenied", "DeleteObject", status=403)

        s3_release_cluster_lock(
            _Denied(), locks_bucketname="b", cluster_name=CLUSTER
        )  # must not raise


class TestAcquireDistributedClusterLock:
    """The CLI-facing composing entry point: ensures the locks bucket
    exists, then acquires the lock, converting ClusterLockError into
    sys.exit(...) -- the exact shape the old local mkdir lock's failure
    already had, which every existing CLI-behavior test depends on."""

    def test_creates_the_bucket_and_acquires_the_lock(self):
        s3 = _FakeS3Lock()
        key = _acquire_distributed_cluster_lock(
            s3,
            locks_bucketname="b",
            region="us-east-2",
            cluster_name=CLUSTER,
            command="first",
        )
        assert key == _lock_key(CLUSTER)
        assert s3.created_buckets == [
            {"Bucket": "b", "CreateBucketConfiguration": {"LocationConstraint": "us-east-2"}}
        ]

    def test_a_held_lock_exits_rather_than_raising(self):
        s3 = _FakeS3Lock()
        _acquire_distributed_cluster_lock(
            s3,
            locks_bucketname="b",
            region="us-east-2",
            cluster_name=CLUSTER,
            command="first",
        )
        with pytest.raises(SystemExit) as exc:
            _acquire_distributed_cluster_lock(
                s3,
                locks_bucketname="b",
                region="us-east-2",
                cluster_name=CLUSTER,
                command="second",
            )
        assert "already running" in str(exc.value.code)

    def test_describe_fn_is_threaded_through_for_reclaim(self):
        s3 = _FakeS3Lock()
        _acquire_distributed_cluster_lock(
            s3,
            locks_bucketname="b",
            region="us-east-2",
            cluster_name=CLUSTER,
            command="first",
        )
        s3.set_last_modified(_lock_key(CLUSTER), DateTime.now(timezone.utc) - timedelta(hours=10))
        describe = _ScriptedDescribe(status="CREATE_FAILED")
        key = _acquire_distributed_cluster_lock(
            s3,
            locks_bucketname="b",
            region="us-east-2",
            cluster_name=CLUSTER,
            command="second",
            describe_fn=describe,
        )
        assert key == _lock_key(CLUSTER)
        assert describe.calls == [(CLUSTER, "us-east-2")]


class TestTheMcpUserPoolNameIsDerivedNotChosen:
    """The pool is one account+region resource that outlives every cluster.
    The one created by hand during certification was
    "pclustermaker-mcp-certify" -- a cluster's name on an account-wide
    resource, which became actively misleading the moment that cluster was
    torn down and the pool was not.
    """

    def test_it_keys_on_account_and_region(self):
        assert (
            _derive_mcp_user_pool_name(aws_account_id="183295445014", region="us-east-1")
            == "parallelclustermaker-mcp-183295445014-us-east-1"
        )

    def test_two_regions_do_not_collide(self):
        a = _derive_mcp_user_pool_name(aws_account_id="1", region="us-east-1")
        b = _derive_mcp_user_pool_name(aws_account_id="1", region="us-west-2")
        assert a != b

    def test_it_cannot_see_a_cluster_or_a_serial(self):
        """The whole point. A cluster- or serial-derived input would restore
        the per-cluster naming this replaces, and a 12-digit account id is
        indistinguishable from a serial datestamp by inspection -- the same
        guard _derive_results_bucket carries."""
        import inspect

        params = list(inspect.signature(_derive_mcp_user_pool_name).parameters)
        assert params == ["aws_account_id", "region"]

    def test_it_is_keyword_only(self):
        """Two same-typed parameters: transposing them yields a plausible
        name rather than an error."""
        import inspect

        for p in inspect.signature(_derive_mcp_user_pool_name).parameters.values():
            assert p.kind == inspect.Parameter.KEYWORD_ONLY

    def test_it_matches_the_sibling_derivations(self):
        """One naming scheme across the three long-lived account+region
        resources, so an operator reading a console listing sees one family."""
        kw = dict(aws_account_id="183295445014", region="us-east-1")
        pool = _derive_mcp_user_pool_name(**kw)
        locks = _derive_locks_bucket(**kw)
        results = _derive_results_bucket(**kw)
        for name in (pool, locks, results):
            assert name.startswith("parallelclustermaker-")
            assert name.endswith("-183295445014-us-east-1")

    def test_an_overlong_name_is_refused(self, monkeypatch):
        """Cognito caps PoolName at 128; a silent truncation would collide
        two accounts onto one pool."""
        import pcluster_core

        monkeypatch.setattr(pcluster_core, "_MCP_USER_POOL_PREFIX", "x" * 130)
        with pytest.raises(SystemExit):
            _derive_mcp_user_pool_name(aws_account_id="1", region="us-east-1")
