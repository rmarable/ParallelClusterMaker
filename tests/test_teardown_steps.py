"""
Workstream 3: direct tests for the boto3/pcluster.lib teardown functions in
pcluster_core.py that replace delete_pcluster.yml's Ansible tasks -- the
timer helper, the four credential-destroying steps gated on
_cf_delete_confirmed, the rest of the cleanup steps (S3 bucket, FSx
hydration inline policy, Grafana SSM parameter, managed IAM policies, IAM
role, external NFS security group), and the delete+wait+classify logic
that produces _cf_delete_confirmed/_cf_delete_failed/_delete_headline in
the first place. Not yet wired into kill_pcluster.py/core_delete_cluster;
these tests exercise the functions in isolation.
"""

import os
import re
import sys

import dataclasses

import pytest
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcluster_core import (
    TeardownStepResult,
    teardown_timestamp,
    _delete_ec2_keypair_step,
    _delete_local_ssh_key_step,
    _delete_secrets_manager_secret_step,
    _delete_cluster_data_dir_step,
    run_credential_teardown_steps,
    _delete_s3_bucket_step,
    _detach_fsx_hydration_policy_step,
    _delete_grafana_ssm_param_step,
    _delete_managed_iam_policies_step,
    _delete_monitoring_iam_policy_step,
    _delete_iam_role_step,
    _delete_external_nfs_sg_step,
    run_resource_teardown_steps,
    ClusterDeleteOutcome,
    _initiate_cluster_delete,
    _wait_for_cluster_delete,
    _classify_cluster_delete_outcome,
    run_cluster_delete_and_classify,
    NotFoundException,
    BadRequestException,
)


def _not_found(code, op):
    return ClientError({"Error": {"Code": code, "Message": ""}}, op)


class _FakeEc2:
    def __init__(self, raise_exc=None):
        self.raise_exc = raise_exc
        self.deleted = []

    def delete_key_pair(self, KeyName):
        if self.raise_exc:
            raise self.raise_exc
        self.deleted.append(KeyName)


class _FakeSecretsManager:
    def __init__(self, raise_exc=None):
        self.raise_exc = raise_exc
        self.deleted = []

    def delete_secret(self, SecretId, ForceDeleteWithoutRecovery):
        if self.raise_exc:
            raise self.raise_exc
        assert ForceDeleteWithoutRecovery is True, (
            "must match --force-delete-without-recovery: immediate delete, "
            "no recovery window"
        )
        self.deleted.append(SecretId)


class TestTeardownTimestamp:
    def test_matches_the_shell_date_format(self):
        """`date +%Y-%m-%d\\ \\@\\ %H:%M:%S` -- e.g. 2026-08-20 @ 16:57:47."""
        ts = teardown_timestamp()
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2} @ \d{2}:\d{2}:\d{2}", ts), ts


class TestDeleteEc2KeypairStep:
    def test_success(self):
        ec2 = _FakeEc2()
        result = _delete_ec2_keypair_step(ec2, "my-keypair")
        assert result.succeeded is True
        assert ec2.deleted == ["my-keypair"]

    def test_failure_is_reported_not_raised(self):
        ec2 = _FakeEc2(raise_exc=RuntimeError("AccessDenied"))
        result = _delete_ec2_keypair_step(ec2, "my-keypair")
        assert result.succeeded is False
        assert "AccessDenied" in result.detail

    def test_result_names_the_step(self):
        result = _delete_ec2_keypair_step(_FakeEc2(), "x")
        assert result.name == "Delete the EC2 keypair associated with this cluster"


class TestDeleteLocalSshKeyStep:
    def test_success(self, tmp_path):
        pem = tmp_path / "cluster.pem"
        pem.write_text("fake key material")
        result = _delete_local_ssh_key_step(str(pem))
        assert result.succeeded is True
        assert not pem.exists()

    def test_already_absent_is_not_a_failure(self, tmp_path):
        """Matches Ansible's file: state: absent -- idempotent, not an error."""
        missing = tmp_path / "does-not-exist.pem"
        result = _delete_local_ssh_key_step(str(missing))
        assert result.succeeded is True

    def test_a_real_os_error_is_reported_not_raised(self, tmp_path, monkeypatch):
        # The key has to exist: an absent path is a deliberate no-op now,
        # since a read-only filesystem raises EROFS rather than
        # FileNotFoundError and a purely local step must not be reported as
        # an AWS-side orphan.
        pem = tmp_path / "x.pem"
        pem.write_text("fake key material")

        def _boom(path):
            raise PermissionError("denied")

        monkeypatch.setattr(os, "remove", _boom)
        result = _delete_local_ssh_key_step(str(pem))
        assert result.succeeded is False
        assert "denied" in result.detail


class TestDeleteSecretsManagerSecretStep:
    def test_success(self):
        sm = _FakeSecretsManager()
        result = _delete_secrets_manager_secret_step(sm, "parallelcluster/foo/serial/ssh-private-key")
        assert result.succeeded is True
        assert sm.deleted == ["parallelcluster/foo/serial/ssh-private-key"]

    def test_failure_is_reported_not_raised(self):
        sm = _FakeSecretsManager(raise_exc=RuntimeError("ResourceNotFoundException"))
        result = _delete_secrets_manager_secret_step(sm, "x")
        assert result.succeeded is False
        assert "ResourceNotFoundException" in result.detail


class TestDeleteClusterDataDirStep:
    def test_success(self, tmp_path):
        cdd = tmp_path / "active_clusters" / "foo"
        cdd.mkdir(parents=True)
        (cdd / "foo.serial").write_text("serial")
        result = _delete_cluster_data_dir_step(str(cdd))
        assert result.succeeded is True
        assert not cdd.exists()

    def test_already_absent_is_not_a_failure(self, tmp_path):
        missing = tmp_path / "active_clusters" / "gone"
        result = _delete_cluster_data_dir_step(str(missing))
        assert result.succeeded is True

    def test_a_real_os_error_is_reported_not_raised(self, tmp_path, monkeypatch):
        import shutil

        def _boom(path):
            raise PermissionError("denied")

        monkeypatch.setattr(shutil, "rmtree", _boom)
        result = _delete_cluster_data_dir_step(str(tmp_path / "x"))
        assert result.succeeded is False
        assert "denied" in result.detail


class TestRunCredentialTeardownSteps:
    """The shared gate: all four steps run together, or none of them do --
    matching delete_pcluster.yml's identical `when: _cf_delete_confirmed`
    on all four tasks."""

    def _kwargs(self, tmp_path, ec2=None, sm=None):
        cdd = tmp_path / "active_clusters" / "foo"
        cdd.mkdir(parents=True)
        pem = tmp_path / "foo.pem"
        pem.write_text("key")
        return dict(
            ec2=ec2 or _FakeEc2(),
            secretsmanager=sm or _FakeSecretsManager(),
            ec2_keypair="foo-keypair",
            ssh_keypair=str(pem),
            ssh_secret_name="parallelcluster/foo/serial/ssh-private-key",
            cluster_data_dir=str(cdd),
        )

    def test_confirmed_delete_runs_all_four_steps(self, tmp_path):
        ec2 = _FakeEc2()
        sm = _FakeSecretsManager()
        results = run_credential_teardown_steps(
            cf_delete_confirmed=True, **self._kwargs(tmp_path, ec2, sm)
        )
        assert len(results) == 4
        assert all(r.succeeded for r in results)
        assert ec2.deleted == ["foo-keypair"]
        assert sm.deleted == ["parallelcluster/foo/serial/ssh-private-key"]

    def test_unconfirmed_delete_skips_all_four_and_preserves_everything(self, tmp_path):
        """The exact bug CLAUDE.md documents: a wait timeout is neither
        confirmed nor DELETE_FAILED, and must not be treated as
        "safe to delete credentials" -- the head node may still be running
        and billing, and these are the only way back into it."""
        kwargs = self._kwargs(tmp_path)
        pem_path = kwargs["ssh_keypair"]
        cdd_path = kwargs["cluster_data_dir"]
        results = run_credential_teardown_steps(cf_delete_confirmed=False, **kwargs)
        assert len(results) == 4
        assert all(r.succeeded for r in results)
        assert all("not confirmed" in r.detail for r in results)
        # Nothing was actually touched.
        assert os.path.exists(pem_path)
        assert os.path.exists(cdd_path)
        assert kwargs["ec2"].deleted == []
        assert kwargs["secretsmanager"].deleted == []

    def test_one_failing_step_does_not_abort_the_rest(self, tmp_path):
        """Matches ignore_errors: true on every one of the four Ansible
        tasks -- a denied DeleteKeyPair must not abandon the other three."""
        ec2 = _FakeEc2(raise_exc=RuntimeError("AccessDenied"))
        results = run_credential_teardown_steps(
            cf_delete_confirmed=True, **self._kwargs(tmp_path, ec2=ec2)
        )
        assert len(results) == 4
        assert results[0].succeeded is False
        assert all(r.succeeded for r in results[1:]), (
            "a failure in the first step must not prevent the other three from running"
        )

    def test_results_are_returned_in_the_playbooks_own_task_order(self, tmp_path):
        results = run_credential_teardown_steps(
            cf_delete_confirmed=True, **self._kwargs(tmp_path)
        )
        assert [r.name for r in results] == [
            "Delete the EC2 keypair associated with this cluster",
            "Delete the SSH private key associated with this cluster",
            "Delete SSH private key from Secrets Manager",
            "Delete the cluster data directory",
        ]


def test_teardown_step_result_is_a_plain_named_tuple_of_fields():
    """Vacuity guard: TeardownStepResult must carry exactly the fields the
    tests above rely on."""
    r = TeardownStepResult(name="x", succeeded=True)
    assert r.name == "x"
    assert r.succeeded is True
    assert r.detail == ""
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.succeeded = False  # frozen dataclass


# ---------------------------------------------------------------------------
# The rest of teardown's cleanup steps -- S3 bucket, FSx hydration policy,
# Grafana SSM parameter, managed IAM policies, IAM role, external NFS SG.
# ---------------------------------------------------------------------------


class _FakeS3:
    def __init__(self, objects=None, raise_exc=None, bucket_missing=False):
        self._objects = objects or []
        self.raise_exc = raise_exc
        self.bucket_missing = bucket_missing
        self.deleted_objects = []
        self.deleted_buckets = []

    def get_paginator(self, name):
        assert name == "list_object_versions"
        return self

    def paginate(self, Bucket):
        yield {
            "Versions": [{"Key": k, "VersionId": "null"} for k in self._objects],
            "DeleteMarkers": [],
        }

    def delete_objects(self, Bucket, Delete):
        self.deleted_objects.extend(o["Key"] for o in Delete["Objects"])

    def delete_bucket(self, Bucket):
        if self.raise_exc:
            raise self.raise_exc
        if self.bucket_missing:
            raise _not_found("NoSuchBucket", "DeleteBucket")
        self.deleted_buckets.append(Bucket)


class _FakeSsm:
    def __init__(self, raise_exc=None):
        self.raise_exc = raise_exc
        self.deleted = []

    def delete_parameter(self, Name):
        if self.raise_exc:
            raise self.raise_exc
        self.deleted.append(Name)


class _FakeIamForTeardown:
    def __init__(self):
        self.deleted_role_policies = []
        self.detached = []
        self.deleted_policies = []
        self.deleted_roles = []
        self.attached_policies = []  # [{"PolicyArn": ...}]
        self.inline_policy_names = []
        self.instance_profiles = []  # [{"InstanceProfileName": ...}]
        self.raise_on = {}  # method name -> exception

    def _maybe_raise(self, method):
        if method in self.raise_on:
            raise self.raise_on[method]

    def delete_role_policy(self, RoleName, PolicyName):
        self._maybe_raise("delete_role_policy")
        self.deleted_role_policies.append((RoleName, PolicyName))

    def detach_role_policy(self, RoleName, PolicyArn):
        self._maybe_raise("detach_role_policy")
        self.detached.append(PolicyArn)

    def delete_policy(self, PolicyArn):
        self._maybe_raise("delete_policy")
        self.deleted_policies.append(PolicyArn)

    def list_attached_role_policies(self, RoleName):
        return {"AttachedPolicies": self.attached_policies}

    def list_role_policies(self, RoleName):
        return {"PolicyNames": self.inline_policy_names}

    def list_instance_profiles_for_role(self, RoleName):
        return {"InstanceProfiles": self.instance_profiles}

    def remove_role_from_instance_profile(self, InstanceProfileName, RoleName):
        pass

    def delete_instance_profile(self, InstanceProfileName):
        pass

    def delete_role(self, RoleName):
        self._maybe_raise("delete_role")
        self.deleted_roles.append(RoleName)


class _FakeEc2ForSg:
    def __init__(self, groups=None, raise_exc=None):
        self._groups = groups if groups is not None else []
        self.raise_exc = raise_exc
        self.deleted_group_ids = []

    def describe_security_groups(self, Filters):
        if self.raise_exc:
            raise self.raise_exc
        return {"SecurityGroups": self._groups}

    def delete_security_group(self, GroupId):
        self.deleted_group_ids.append(GroupId)


class TestDeleteS3BucketStep:
    def test_empties_and_deletes(self):
        s3 = _FakeS3(objects=["a.txt", "b.txt"])
        result = _delete_s3_bucket_step(s3, "my-bucket")
        assert result.succeeded is True
        assert sorted(s3.deleted_objects) == ["a.txt", "b.txt"]
        assert s3.deleted_buckets == ["my-bucket"]

    def test_already_absent_is_not_a_failure(self):
        s3 = _FakeS3(bucket_missing=True)
        result = _delete_s3_bucket_step(s3, "gone-bucket")
        assert result.succeeded is True

    def test_other_failure_is_reported_not_raised(self):
        s3 = _FakeS3(raise_exc=RuntimeError("AccessDenied"))
        result = _delete_s3_bucket_step(s3, "my-bucket")
        assert result.succeeded is False
        assert "AccessDenied" in result.detail


class TestDetachFsxHydrationPolicyStep:
    def test_success(self):
        iam = _FakeIamForTeardown()
        result = _detach_fsx_hydration_policy_step(iam, "role", "fsx-policy")
        assert result.succeeded is True
        assert iam.deleted_role_policies == [("role", "fsx-policy")]

    def test_already_absent_is_not_a_failure(self):
        iam = _FakeIamForTeardown()
        iam.raise_on["delete_role_policy"] = _not_found("NoSuchEntity", "DeleteRolePolicy")
        result = _detach_fsx_hydration_policy_step(iam, "role", "fsx-policy")
        assert result.succeeded is True

    def test_other_failure_is_reported(self):
        iam = _FakeIamForTeardown()
        iam.raise_on["delete_role_policy"] = RuntimeError("AccessDenied")
        result = _detach_fsx_hydration_policy_step(iam, "role", "fsx-policy")
        assert result.succeeded is False
        assert "AccessDenied" in result.detail


class TestDeleteGrafanaSsmParamStep:
    def test_success(self):
        ssm = _FakeSsm()
        result = _delete_grafana_ssm_param_step(ssm, "foo")
        assert result.succeeded is True
        assert ssm.deleted == ["/parallelcluster/foo/grafana/admin-password"]

    def test_already_absent_is_not_a_failure(self):
        ssm = _FakeSsm(raise_exc=_not_found("ParameterNotFound", "DeleteParameter"))
        result = _delete_grafana_ssm_param_step(ssm, "foo")
        assert result.succeeded is True

    def test_other_failure_is_reported(self):
        ssm = _FakeSsm(raise_exc=RuntimeError("AccessDenied"))
        result = _delete_grafana_ssm_param_step(ssm, "foo")
        assert result.succeeded is False
        assert "AccessDenied" in result.detail


class TestDeleteManagedIamPoliciesStep:
    def test_success_detaches_and_deletes_all_five(self):
        """Five, not four: ClusterNode-Deny is unconditional, so it is a base
        suffix alongside the original four rather than a monitoring-style
        conditional one. A count left at four here still passes while the
        deny policy is orphaned in the account on every teardown."""
        iam = _FakeIamForTeardown()
        result = _delete_managed_iam_policies_step(iam, "role", "test-policy", "123456789012")
        assert result.succeeded is True
        assert len(iam.detached) == 5
        assert len(iam.deleted_policies) == 5
        assert "arn:aws:iam::123456789012:policy/test-policy-HeadNode-Compute" in iam.detached
        assert "arn:aws:iam::123456789012:policy/test-policy-ClusterNode-Deny" in iam.detached

    def test_already_absent_policies_are_not_a_failure(self):
        iam = _FakeIamForTeardown()
        iam.raise_on["detach_role_policy"] = _not_found("NoSuchEntity", "DetachRolePolicy")
        iam.raise_on["delete_policy"] = _not_found("NoSuchEntity", "DeletePolicy")
        result = _delete_managed_iam_policies_step(iam, "role", "test-policy", "123456789012")
        assert result.succeeded is True

    def test_one_failing_policy_is_named_and_the_rest_still_run(self):
        iam = _FakeIamForTeardown()
        iam.raise_on["delete_policy"] = RuntimeError("AccessDenied")
        result = _delete_managed_iam_policies_step(iam, "role", "test-policy", "123456789012")
        assert result.succeeded is False
        assert "AccessDenied" in result.detail
        # all five were still attempted despite the failures
        assert len(iam.detached) == 5


class TestDeleteMonitoringIamPolicyStep:
    def test_success(self):
        iam = _FakeIamForTeardown()
        result = _delete_monitoring_iam_policy_step(iam, "role", "test-policy", "123456789012")
        assert result.succeeded is True
        assert iam.deleted_policies == [
            "arn:aws:iam::123456789012:policy/test-policy-HeadNode-Monitoring"
        ]

    def test_other_failure_is_reported(self):
        iam = _FakeIamForTeardown()
        iam.raise_on["delete_policy"] = RuntimeError("AccessDenied")
        result = _delete_monitoring_iam_policy_step(iam, "role", "test-policy", "123456789012")
        assert result.succeeded is False


class TestDeleteIamRoleStep:
    def test_detaches_everything_then_deletes_the_role(self):
        iam = _FakeIamForTeardown()
        iam.attached_policies = [{"PolicyArn": "arn:aws:iam::123456789012:policy/leftover"}]
        iam.inline_policy_names = ["stray-inline"]
        iam.instance_profiles = [{"InstanceProfileName": "role"}]
        result = _delete_iam_role_step(iam, "role")
        assert result.succeeded is True
        assert "arn:aws:iam::123456789012:policy/leftover" in iam.detached
        assert ("role", "stray-inline") in iam.deleted_role_policies
        assert iam.deleted_roles == ["role"]

    def test_already_absent_role_is_not_a_failure(self):
        iam = _FakeIamForTeardown()
        iam.raise_on["delete_role"] = _not_found("NoSuchEntity", "DeleteRole")
        result = _delete_iam_role_step(iam, "role")
        assert result.succeeded is True

    def test_other_failure_is_reported(self):
        iam = _FakeIamForTeardown()
        iam.raise_on["delete_role"] = RuntimeError("AccessDenied")
        result = _delete_iam_role_step(iam, "role")
        assert result.succeeded is False
        assert "AccessDenied" in result.detail


class TestDeleteExternalNfsSgStep:
    def test_deletes_the_matching_group(self):
        ec2 = _FakeEc2ForSg(groups=[{"GroupId": "sg-12345678"}])
        result = _delete_external_nfs_sg_step(ec2, "foo")
        assert result.succeeded is True
        assert ec2.deleted_group_ids == ["sg-12345678"]

    def test_no_matching_group_is_not_a_failure(self):
        ec2 = _FakeEc2ForSg(groups=[])
        result = _delete_external_nfs_sg_step(ec2, "foo")
        assert result.succeeded is True
        assert ec2.deleted_group_ids == []

    def test_other_failure_is_reported(self):
        ec2 = _FakeEc2ForSg(raise_exc=RuntimeError("AccessDenied"))
        result = _delete_external_nfs_sg_step(ec2, "foo")
        assert result.succeeded is False
        assert "AccessDenied" in result.detail


class TestRunResourceTeardownSteps:
    def _kwargs(self, **overrides):
        base = dict(
            s3=_FakeS3(),
            iam=_FakeIamForTeardown(),
            ssm=_FakeSsm(),
            ec2=_FakeEc2ForSg(),
            cluster_name="foo",
            ec2_iam_role="role",
            ec2_iam_policy="test-policy",
            aws_account_id="123456789012",
            s3_bucketname="my-bucket",
            delete_s3_bucketname=True,
            enable_fsx_hydration=False,
            fsx_hydration_iam_policy="UNDEFINED",
            enable_monitoring=False,
            enable_external_nfs=False,
        )
        base.update(overrides)
        return base

    def test_minimal_cluster_runs_only_the_unconditional_steps(self):
        results = run_resource_teardown_steps(**self._kwargs())
        assert [r.name for r in results] == [
            "Delete the S3 bucket associated with this cluster",
            "Detach and delete managed IAM policies associated with the cluster stack",
            "Delete the IAM roles associated with the cluster stack",
        ]
        assert all(r.succeeded for r in results)

    def test_delete_s3_bucketname_false_skips_that_step(self):
        results = run_resource_teardown_steps(**self._kwargs(delete_s3_bucketname=False))
        assert "Delete the S3 bucket associated with this cluster" not in [r.name for r in results]

    def test_every_gated_step_fires_when_every_flag_is_on(self):
        results = run_resource_teardown_steps(
            **self._kwargs(
                enable_fsx_hydration=True,
                fsx_hydration_iam_policy="fsx-policy",
                enable_monitoring=True,
                enable_external_nfs=True,
            )
        )
        assert [r.name for r in results] == [
            "Delete the S3 bucket associated with this cluster",
            "Detach the FSx hydration IAM policy from the role",
            "Delete Grafana SSM password parameter",
            "Detach and delete managed IAM policies associated with the cluster stack",
            "Detach and delete monitoring IAM policy",
            "Delete the IAM roles associated with the cluster stack",
            "Delete the external NFS security group associated with this cluster",
        ]
        assert all(r.succeeded for r in results)


# ---------------------------------------------------------------------------
# The delete+wait+classify logic that produces _cf_delete_confirmed /
# _cf_delete_failed / _delete_headline.
# ---------------------------------------------------------------------------


def _fake_sleep(_seconds):
    pass  # no real sleeping in tests


class _FakeDeleteFn:
    def __init__(self, raise_exc=None):
        self.raise_exc = raise_exc
        self.calls = []

    def __call__(self, cluster_name, region):
        self.calls.append((cluster_name, region))
        if self.raise_exc:
            raise self.raise_exc


class _ScriptedDescribeFn:
    """Returns a scripted sequence of clusterStatus values (or raises a
    scripted exception) on successive calls; the last entry repeats
    forever once the sequence is exhausted, so a single-element sequence
    models a persistent condition."""

    def __init__(self, sequence):
        self._sequence = list(sequence)
        self.calls = []

    def __call__(self, cluster_name, region):
        self.calls.append((cluster_name, region))
        item = self._sequence[min(len(self.calls) - 1, len(self._sequence) - 1)]
        if isinstance(item, Exception):
            raise item
        return {"clusterStatus": item}


class TestInitiateClusterDelete:
    def test_success_returns_false(self):
        delete_fn = _FakeDeleteFn()
        already_gone = _initiate_cluster_delete(delete_fn, "foo", "us-east-1")
        assert already_gone is False
        assert delete_fn.calls == [("foo", "us-east-1")]

    def test_not_found_returns_true(self):
        delete_fn = _FakeDeleteFn(raise_exc=NotFoundException("gone"))
        already_gone = _initiate_cluster_delete(delete_fn, "foo", "us-east-1")
        assert already_gone is True

    def test_bad_request_is_tolerated_not_raised(self):
        delete_fn = _FakeDeleteFn(raise_exc=BadRequestException("incompatible version"))
        already_gone = _initiate_cluster_delete(delete_fn, "foo", "us-east-1")
        assert already_gone is False

    def test_other_exception_propagates(self):
        delete_fn = _FakeDeleteFn(raise_exc=RuntimeError("AccessDenied"))
        with pytest.raises(RuntimeError):
            _initiate_cluster_delete(delete_fn, "foo", "us-east-1")


class TestWaitForClusterDelete:
    def test_delete_complete_on_first_attempt(self):
        describe_fn = _ScriptedDescribeFn(["DELETE_COMPLETE"])
        state = _wait_for_cluster_delete(
            describe_fn, "foo", "us-east-1", retries=5, delay_seconds=1, sleep_fn=_fake_sleep
        )
        assert state == "DELETE_COMPLETE"
        assert len(describe_fn.calls) == 1

    def test_delete_failed_on_first_attempt(self):
        describe_fn = _ScriptedDescribeFn(["DELETE_FAILED"])
        state = _wait_for_cluster_delete(
            describe_fn, "foo", "us-east-1", retries=5, delay_seconds=1, sleep_fn=_fake_sleep
        )
        assert state == "DELETE_FAILED"

    def test_not_found_during_poll(self):
        describe_fn = _ScriptedDescribeFn([NotFoundException("gone")])
        state = _wait_for_cluster_delete(
            describe_fn, "foo", "us-east-1", retries=5, delay_seconds=1, sleep_fn=_fake_sleep
        )
        assert state == "CLUSTER_NOT_FOUND"

    def test_still_building_then_completes(self):
        describe_fn = _ScriptedDescribeFn(
            ["DELETE_IN_PROGRESS", "DELETE_IN_PROGRESS", "DELETE_COMPLETE"]
        )
        sleeps = []
        state = _wait_for_cluster_delete(
            describe_fn, "foo", "us-east-1", retries=5, delay_seconds=1,
            sleep_fn=sleeps.append,
        )
        assert state == "DELETE_COMPLETE"
        assert len(describe_fn.calls) == 3
        assert len(sleeps) == 2  # slept between attempts 1->2 and 2->3, not after the last

    def test_times_out_after_retries_exhausted(self):
        describe_fn = _ScriptedDescribeFn(["DELETE_IN_PROGRESS"])
        sleeps = []
        state = _wait_for_cluster_delete(
            describe_fn, "foo", "us-east-1", retries=4, delay_seconds=1,
            sleep_fn=sleeps.append,
        )
        assert state == "TIMED_OUT"
        assert len(describe_fn.calls) == 4
        assert len(sleeps) == 3  # never sleeps after the final (4th) attempt

    def test_transient_error_then_recovers(self):
        describe_fn = _ScriptedDescribeFn([RuntimeError("throttled"), "DELETE_COMPLETE"])
        state = _wait_for_cluster_delete(
            describe_fn, "foo", "us-east-1", retries=5, delay_seconds=1, sleep_fn=_fake_sleep
        )
        assert state == "DELETE_COMPLETE"

    def test_persistent_error_raises_on_final_attempt(self):
        """The playbook's failed_when aborts the whole play when the final
        describe-cluster attempt is still an unrecognized failure -- this
        must propagate, not fold into TIMED_OUT, so a caller can't
        mistakenly treat "never resolved" as "safe to proceed"."""
        describe_fn = _ScriptedDescribeFn([RuntimeError("AccessDenied")])
        with pytest.raises(RuntimeError, match="AccessDenied"):
            _wait_for_cluster_delete(
                describe_fn, "foo", "us-east-1", retries=3, delay_seconds=1, sleep_fn=_fake_sleep
            )


class TestClassifyClusterDeleteOutcome:
    def test_delete_complete_is_confirmed(self):
        confirmed, failed, headline = _classify_cluster_delete_outcome("DELETE_COMPLETE", "foo")
        assert confirmed is True
        assert failed is False
        assert headline == "Cluster foo has been deleted."

    def test_cluster_not_found_is_confirmed_the_same_as_delete_complete(self):
        confirmed, failed, headline = _classify_cluster_delete_outcome("CLUSTER_NOT_FOUND", "foo")
        assert confirmed is True
        assert failed is False
        assert headline == "Cluster foo has been deleted."

    def test_delete_failed_is_neither_confirmed_nor_silent(self):
        confirmed, failed, headline = _classify_cluster_delete_outcome("DELETE_FAILED", "foo")
        assert confirmed is False
        assert failed is True
        assert headline == "Cluster foo reached DELETE_FAILED and was NOT deleted."

    def test_timed_out_is_neither_confirmed_nor_failed(self):
        """The exact case CLAUDE.md documents: a wait timeout must not be
        treated as a clean delete -- credentials stay preserved."""
        confirmed, failed, headline = _classify_cluster_delete_outcome("TIMED_OUT", "foo")
        assert confirmed is False
        assert failed is False
        assert headline == "Deletion of cluster foo was NOT confirmed."


class TestRunClusterDeleteAndClassify:
    def test_already_gone_skips_the_wait_loop_entirely(self):
        delete_fn = _FakeDeleteFn(raise_exc=NotFoundException("gone"))
        describe_fn = _ScriptedDescribeFn(["DELETE_IN_PROGRESS"])
        outcome = run_cluster_delete_and_classify(
            delete_fn, describe_fn, "foo", "us-east-1", sleep_fn=_fake_sleep
        )
        assert outcome.terminal_state == "CLUSTER_NOT_FOUND"
        assert outcome.cf_delete_confirmed is True
        assert describe_fn.calls == []

    def test_normal_delete_flow_confirms(self):
        delete_fn = _FakeDeleteFn()
        describe_fn = _ScriptedDescribeFn(["DELETE_IN_PROGRESS", "DELETE_COMPLETE"])
        outcome = run_cluster_delete_and_classify(
            delete_fn, describe_fn, "foo", "us-east-1",
            retries=5, delay_seconds=1, sleep_fn=_fake_sleep,
        )
        assert outcome == ClusterDeleteOutcome(
            "DELETE_COMPLETE", True, False, "Cluster foo has been deleted."
        )

    def test_delete_failed_flow(self):
        delete_fn = _FakeDeleteFn()
        describe_fn = _ScriptedDescribeFn(["DELETE_FAILED"])
        outcome = run_cluster_delete_and_classify(
            delete_fn, describe_fn, "foo", "us-east-1",
            retries=5, delay_seconds=1, sleep_fn=_fake_sleep,
        )
        assert outcome.terminal_state == "DELETE_FAILED"
        assert outcome.cf_delete_confirmed is False
        assert outcome.cf_delete_failed is True

    def test_timeout_flow_preserves_credentials(self):
        delete_fn = _FakeDeleteFn()
        describe_fn = _ScriptedDescribeFn(["DELETE_IN_PROGRESS"])
        outcome = run_cluster_delete_and_classify(
            delete_fn, describe_fn, "foo", "us-east-1",
            retries=2, delay_seconds=1, sleep_fn=_fake_sleep,
        )
        assert outcome.terminal_state == "TIMED_OUT"
        assert outcome.cf_delete_confirmed is False
        assert outcome.cf_delete_failed is False
        assert outcome.delete_headline == "Deletion of cluster foo was NOT confirmed."


class TestTeardownWaitFalseKicksOffWithoutPolling:
    """Workstream 4's `wait: bool` parameter on the teardown side -- the
    same one-function-two-callers shape the create side uses, and the same
    reason: an MCP tool call cannot block for the 15-20 minutes a teardown
    takes."""

    def test_wait_false_still_initiates_the_delete(self):
        delete_fn = _FakeDeleteFn()
        describe_fn = _ScriptedDescribeFn(["DELETE_COMPLETE"])
        run_cluster_delete_and_classify(
            delete_fn, describe_fn, "foo", "us-east-1", wait=False, sleep_fn=_fake_sleep,
        )
        assert delete_fn.calls == [("foo", "us-east-1")]

    def test_wait_false_never_polls(self):
        delete_fn = _FakeDeleteFn()
        describe_fn = _ScriptedDescribeFn(["DELETE_COMPLETE"])
        run_cluster_delete_and_classify(
            delete_fn, describe_fn, "foo", "us-east-1", wait=False, sleep_fn=_fake_sleep,
        )
        assert describe_fn.calls == []

    def test_wait_false_does_not_confirm_the_delete(self):
        """Load-bearing, and the single most important property in this
        class: every credential-destroying teardown step (EC2 keypair,
        local .pem, Secrets Manager secret, the cluster data dir) is gated
        on cf_delete_confirmed, and CLAUDE.md's teardown-gate bullet
        requires *positive* confirmation the stack is gone. "We did not
        look" is not confirmation -- a True here would destroy the only
        ways back into a head node whose stack may still be running."""
        delete_fn = _FakeDeleteFn()
        describe_fn = _ScriptedDescribeFn(["DELETE_COMPLETE"])
        outcome = run_cluster_delete_and_classify(
            delete_fn, describe_fn, "foo", "us-east-1", wait=False, sleep_fn=_fake_sleep,
        )
        assert outcome.terminal_state == "KICKED_OFF"
        assert outcome.cf_delete_confirmed is False
        assert outcome.cf_delete_failed is False

    def test_wait_false_is_distinct_from_timed_out(self):
        delete_fn = _FakeDeleteFn()
        describe_fn = _ScriptedDescribeFn(["DELETE_IN_PROGRESS"])
        outcome = run_cluster_delete_and_classify(
            delete_fn, describe_fn, "foo", "us-east-1", wait=False, sleep_fn=_fake_sleep,
        )
        assert outcome.terminal_state != "TIMED_OUT"

    def test_an_already_gone_cluster_still_confirms_under_wait_false(self):
        """already_gone is checked before the wait branch, and correctly
        so: a NotFoundException from delete_cluster is positive evidence
        the stack is gone, which no amount of not-waiting can undo."""
        delete_fn = _FakeDeleteFn(raise_exc=NotFoundException("gone"))
        describe_fn = _ScriptedDescribeFn(["DELETE_IN_PROGRESS"])
        outcome = run_cluster_delete_and_classify(
            delete_fn, describe_fn, "foo", "us-east-1", wait=False, sleep_fn=_fake_sleep,
        )
        assert outcome.terminal_state == "CLUSTER_NOT_FOUND"
        assert outcome.cf_delete_confirmed is True

    def test_wait_defaults_to_true(self):
        delete_fn = _FakeDeleteFn()
        describe_fn = _ScriptedDescribeFn(["DELETE_COMPLETE"])
        outcome = run_cluster_delete_and_classify(
            delete_fn, describe_fn, "foo", "us-east-1",
            retries=5, delay_seconds=1, sleep_fn=_fake_sleep,
        )
        assert outcome.cf_delete_confirmed is True
        assert describe_fn.calls, "the default must still poll"


class TestTeardownProgressIsReportedDuringTheWait:
    def test_progress_fn_fires_once_per_non_terminal_poll(self):
        delete_fn = _FakeDeleteFn()
        describe_fn = _ScriptedDescribeFn(
            ["DELETE_IN_PROGRESS", "DELETE_IN_PROGRESS", "DELETE_COMPLETE"]
        )
        seen = []
        run_cluster_delete_and_classify(
            delete_fn, describe_fn, "foo", "us-east-1",
            retries=5, delay_seconds=1, sleep_fn=_fake_sleep,
            progress_fn=lambda *a: seen.append(a),
        )
        assert len(seen) == 2

    def test_progress_fn_is_never_called_when_not_waiting(self):
        delete_fn = _FakeDeleteFn()
        describe_fn = _ScriptedDescribeFn(["DELETE_IN_PROGRESS"])
        seen = []
        run_cluster_delete_and_classify(
            delete_fn, describe_fn, "foo", "us-east-1", wait=False, sleep_fn=_fake_sleep,
            progress_fn=lambda *a: seen.append(a),
        )
        assert seen == []

    def test_no_progress_fn_is_silent_and_still_works(self):
        delete_fn = _FakeDeleteFn()
        describe_fn = _ScriptedDescribeFn(["DELETE_IN_PROGRESS", "DELETE_COMPLETE"])
        outcome = run_cluster_delete_and_classify(
            delete_fn, describe_fn, "foo", "us-east-1",
            retries=5, delay_seconds=1, sleep_fn=_fake_sleep,
        )
        assert outcome.cf_delete_confirmed is True


class TestEverySurfaceQuotesTheSameTeardownDuration:
    """Five places tell an operator how long a teardown takes, and they had
    all quoted the old, too-short figure while real teardowns ran
    longer. They are not
    generated from one another -- the CLI print, the README, the MCP
    server's instructions string, the delete_cluster tool docstring, the
    automatic finisher's poll bounds, and this file's own note -- so nothing
    stopped one from being corrected and the rest going stale. (There were
    six until src/delete_pcluster.yml, the unexecuted reference spec, was
    deleted.)

    A stale figure here is not cosmetic: it is the number an operator uses
    to decide whether a teardown has hung, so understating it manufactures
    false alarms and a habit of interrupting deletes partway.
    """

    _ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _DURATION = "15-20"
    _SURFACES = {
        "src/pcluster_core.py": "approximately 15-20 minutes to complete",
        "mcp_server/server.py": "20-45 minutes and 15-20 ",
        "mcp_server/tools.py": "teardown takes 15-20 minutes",
        # The automatic finisher states the same duration, and states
        # it because the poll bounds are chosen against it.
        "mcp_server/completion.py": "teardown is 15-20 minutes",
        # The README uses a typographic en dash throughout; keep its style
        # rather than normalising it to the ASCII form the code uses.
        "README.md": "Teardown takes 15–20 minutes.",
    }

    def test_every_surface_states_the_current_duration(self):
        missing = []
        for relpath, expected in self._SURFACES.items():
            with open(os.path.join(self._ROOT, relpath)) as fh:
                if expected not in fh.read():
                    missing.append(f"{relpath}: expected {expected!r}")
        assert missing == [], missing

    def test_no_surface_still_quotes_the_old_figure(self):
        """The direction that actually rots: a new figure lands in one file
        and the others keep the old one, so both numbers are live at once.

        The superseded strings are assembled from their parts rather than
        written out. This file is one of the surfaces it scans, so spelling
        the old figure anywhere here -- including in this docstring, which
        is how the first two attempts failed -- makes the check match
        itself and fail permanently.
        """
        low, high = "5", "10"
        stale = []
        for relpath in self._SURFACES:
            with open(os.path.join(self._ROOT, relpath)) as fh:
                body = fh.read()
            for dash in ("-", "\u2013"):
                superseded = f"{low}{dash}{high} minutes"
                if superseded in body:
                    stale.append(f"{relpath}: {superseded!r}")
        assert stale == [], stale

    def test_the_manifest_covers_every_file_that_mentions_a_teardown_duration(self):
        """Vacuity guard, and the one that matters: a seventh surface added
        later is invisible to the two checks above. Sweeps the repo rather
        than trusting the list -- mcp_server/server.py was itself only found
        by a wider grep than the obvious one."""
        import re

        pattern = re.compile(r"teardown[^.\n]{0,60}?\d+[-–]\d+\s*minutes", re.I)
        found = set()
        for sub in (".", "src", "mcp_server", "tests", "templates"):
            base = os.path.join(self._ROOT, sub)
            if not os.path.isdir(base):
                continue
            for name in os.listdir(base):
                path = os.path.join(base, name)
                if not os.path.isfile(path):
                    continue
                if not name.endswith((".py", ".md", ".yml", ".j2")):
                    continue
                with open(path, errors="ignore") as fh:
                    if pattern.search(fh.read()):
                        found.add(os.path.relpath(path, self._ROOT))
        # This file is the checker, not an operator-facing surface: its
        # prose describes the change, and any figure spelled here would be
        # matched by the stale scan above. Exempt deliberately.
        uncovered = found - set(self._SURFACES) - {"tests/test_teardown_steps.py"}
        assert uncovered == set(), (
            f"these mention a teardown duration but are not in the manifest: "
            f"{sorted(uncovered)}"
        )
