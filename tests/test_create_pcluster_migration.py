"""
Workstream 3, create-side migration: tests for the slices ported off
create_pcluster.yml so far -- the OS assert (task index 0), the S3
bucket/EC2 keypair/Secrets Manager block with its rescue:, the monitoring
tarball/Docker Compose checksum-verified downloads, the create-cluster
launch+wait+classify, and the SSH/SCP orchestration to the head node. None
are wired into core_create_cluster yet, and create_pcluster.yml itself
still runs all five slices' Ansible originals.
"""

import ast
import hashlib
import io
import os
import subprocess
import sys

import pytest
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import pcluster_aux_data
import pcluster_core
from pcluster_core import (
    _assert_supported_os,
    _create_s3_bucket_for_cluster,
    _create_external_nfs_sg,
    _generate_ec2_keypair,
    _abort_if_keypair_orphaned,
    _save_private_key_locally,
    _store_ssh_secret,
    _cleanup_after_provisioning_failure,
    provision_s3_keypair_and_secret,
    _ClusterProvisioningError,
    _EXTERNAL_NFS_PORTS,
    _download_with_checksum,
    _upload_file_to_s3,
    _stage_monitoring_tarball,
    _stage_docker_compose_plugin,
    stage_and_upload_monitoring_wrapper,
    ClusterCreateOutcome,
    _wait_for_cluster_create,
    _extract_head_node_ip,
    _classify_cluster_create_outcome,
    run_cluster_create_and_classify,
    _KICKED_OFF,
    _wait_for_ssh_port,
    _ensure_local_ssh_dir,
    _remove_stale_known_hosts_entry,
    _accept_ssh_fingerprint,
    _create_performance_dir_on_head_node,
    _transfer_staging_dir,
    _transfer_sbatch_script,
    _copy_performance_source_tree,
    _build_active_perf_dirs,
    _create_and_own_perf_dirs,
    _remove_head_node_staging_dir,
    deploy_staging_and_performance_tree_to_head_node,
    _create_sns_topic_and_notify,
    render_and_upload_cluster_config_and_scripts,
    _upload_external_nfs_mount_list,
    _create_hpc_results_bucket,
    stage_and_upload_hpc_benchmark_driver,
    print_cluster_launch_summary,
    finalize_staging_directory,
    render_and_publish_build_summary_report,
    print_fsx_hydration_helper_locations,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SUPPORTED = pcluster_aux_data.ARM_OSES + pcluster_aux_data.X86_OSES

_FAKE_PEM = "-----BEGIN PRIVATE KEY-----\nTHIS-IS-FAKE-KEY-MATERIAL\n-----END PRIVATE KEY-----\n"


def _client_error(code, op):
    return ClientError({"Error": {"Code": code, "Message": ""}}, op)


class TestAssertSupportedOs:
    @pytest.mark.parametrize("base_os", _SUPPORTED)
    def test_every_supported_value_passes(self, base_os):
        _assert_supported_os(base_os)  # must not raise

    def test_unsupported_value_exits(self):
        with pytest.raises(SystemExit):
            _assert_supported_os("centos7")

    def test_error_names_the_valid_values(self, capsys):
        with pytest.raises(SystemExit):
            _assert_supported_os("centos7")
        out = capsys.readouterr().out
        assert "centos7" in out
        for base_os in _SUPPORTED:
            assert base_os in out

    def test_a_trailing_garbage_suffix_is_rejected(self):
        """`'alinux' in base_os`-style substring gates elsewhere in this
        codebase are documented as a known hazard (CLAUDE.md) -- this
        assert uses exact membership, so a mutated/garbage spelling that
        merely resembles a supported value must still be rejected."""
        with pytest.raises(SystemExit):
            _assert_supported_os("alinux2023arm2")

    def test_the_pcluster_os_check_is_not_dead_code(self, monkeypatch):
        """Every real base_os's derived pcluster_os is, today, always a
        member of X86_OSES by construction (removesuffix("arm") on an
        ARM_OSES value always lands back in X86_OSES) -- so this check
        cannot be exercised by any real value. Narrowing X86_OSES here
        proves the second condition is actually evaluated, not vestigial,
        against a still-real base_os value."""
        monkeypatch.setattr(pcluster_aux_data, "X86_OSES", ("rhel9",))
        with pytest.raises(SystemExit):
            _assert_supported_os("ubuntu2404")

    def test_arm_oses_and_x86_oses_are_not_restated(self):
        """Vacuity guard: the valid set this function checks against must
        be pcluster_aux_data's own ARM_OSES/X86_OSES, not a fresh literal
        copy of the same eight strings that could silently drift from it."""
        with open(os.path.join(REPO_ROOT, "src", "pcluster_core.py")) as fh:
            source = fh.read()
        start = source.index("def _assert_supported_os(")
        end = source.index("\ndef ", start + 1)
        body = source[start:end]
        assert "ARM_OSES" in body and "X86_OSES" in body
        # None of the eight literal OS strings appear hardcoded in the body.
        for base_os in _SUPPORTED:
            assert f'"{base_os}"' not in body and f"'{base_os}'" not in body


class TestAssertRunsBeforeAnythingElse:
    """Position is the property, matching CLAUDE.md's own rule for the
    playbook's identical task-index-0 requirement: an assert anywhere else
    in core_create_cluster has already spent real time/API calls before
    rejecting a bad OS. A text/substring assertion cannot tell "first
    statement" from "present somewhere in the function" -- this walks the
    real AST."""

    def _core_create_cluster_body(self):
        with open(os.path.join(REPO_ROOT, "src", "pcluster_core.py")) as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "core_create_cluster":
                return node.body
        raise AssertionError("core_create_cluster not found")

    def test_assert_supported_os_is_the_first_statement(self):
        body = self._core_create_cluster_body()
        # Skip a leading docstring Expr, matching how Python itself treats one.
        stmts = body[1:] if isinstance(body[0], ast.Expr) else body
        first = stmts[0]
        assert isinstance(first, ast.Expr) and isinstance(first.value, ast.Call)
        call = first.value
        assert isinstance(call.func, ast.Name) and call.func.id == "_assert_supported_os"

    def test_the_discriminator_actually_catches_a_moved_call(self):
        """Vacuity guard: prove the test above fails if the call is moved
        later in the function, not just when it is absent."""
        src = (
            "def core_create_cluster(*, params):\n"
            "    '''doc'''\n"
            "    x = 1\n"
            "    _assert_supported_os(params.base_os)\n"
        )
        tree = ast.parse(src)
        node = tree.body[0]
        stmts = node.body[1:] if isinstance(node.body[0], ast.Expr) else node.body
        first = stmts[0]
        is_the_call = (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Call)
            and isinstance(first.value.func, ast.Name)
            and first.value.func.id == "_assert_supported_os"
        )
        assert not is_the_call


# ---------------------------------------------------------------------------
# The S3 bucket / EC2 keypair / Secrets Manager block, with rescue.
# ---------------------------------------------------------------------------


class _FakeS3:
    def __init__(self, already_owned=False, raise_on_create=None):
        self.already_owned = already_owned
        self.raise_on_create = raise_on_create
        self.created_buckets = []
        self.tags = {}
        self.deleted_buckets = []
        self.uploaded = []

    def create_bucket(self, **kwargs):
        if self.raise_on_create:
            raise self.raise_on_create
        if self.already_owned:
            raise _client_error("BucketAlreadyOwnedByYou", "CreateBucket")
        self.created_buckets.append(kwargs)

    def put_bucket_tagging(self, Bucket, Tagging):
        self.tags[Bucket] = Tagging

    def get_paginator(self, name):
        assert name == "list_object_versions"

        class _Paginator:
            def paginate(self, Bucket):
                yield {"Versions": [], "DeleteMarkers": []}

        return _Paginator()

    def delete_objects(self, **kwargs):
        pass

    def delete_bucket(self, Bucket):
        self.deleted_buckets.append(Bucket)

    def upload_file(self, src, bucket, key, ExtraArgs=None):
        self.uploaded.append({"src": src, "bucket": bucket, "key": key, "extra": ExtraArgs})


class _FakeEc2:
    def __init__(self, sg_already_exists=False, keypair_already_exists=False, raise_on_sg=None, raise_on_keypair=None):
        self.sg_already_exists = sg_already_exists
        self.keypair_already_exists = keypair_already_exists
        self.raise_on_sg = raise_on_sg
        self.raise_on_keypair = raise_on_keypair
        self.created_sgs = []
        self.authorized = None
        self.created_keypairs = []
        self.deleted_keypairs = []
        self.deleted_sgs = []
        self.describe_calls = []

    def create_security_group(self, GroupName, Description, VpcId):
        if self.raise_on_sg:
            raise self.raise_on_sg
        if self.sg_already_exists:
            raise _client_error("InvalidGroup.Duplicate", "CreateSecurityGroup")
        group_id = "sg-created0001"
        self.created_sgs.append({"GroupName": GroupName, "VpcId": VpcId})
        return {"GroupId": group_id}

    def authorize_security_group_ingress(self, GroupId, IpPermissions):
        self.authorized = (GroupId, IpPermissions)

    def describe_security_groups(self, Filters):
        self.describe_calls.append(Filters)
        return {"SecurityGroups": [{"GroupId": "sg-existing0001"}]}

    def delete_security_group(self, GroupId):
        self.deleted_sgs.append(GroupId)

    def create_key_pair(self, KeyName, KeyType):
        if self.raise_on_keypair:
            raise self.raise_on_keypair
        if self.keypair_already_exists:
            raise _client_error("InvalidKeyPair.Duplicate", "CreateKeyPair")
        self.created_keypairs.append(KeyName)
        return {"KeyMaterial": _FAKE_PEM}

    def delete_key_pair(self, KeyName):
        self.deleted_keypairs.append(KeyName)


class _FakeSecretsManager:
    def __init__(self, raise_on_create=None):
        self.raise_on_create = raise_on_create
        self.created_secrets = []
        self.deleted_secrets = []

    def create_secret(self, Name, Description, SecretString, Tags):
        if self.raise_on_create:
            raise self.raise_on_create
        self.created_secrets.append(
            {"Name": Name, "SecretString": SecretString, "Tags": Tags}
        )

    def delete_secret(self, SecretId, ForceDeleteWithoutRecovery):
        self.deleted_secrets.append(SecretId)


class TestCreateS3BucketForCluster:
    def test_creates_and_tags(self):
        s3 = _FakeS3()
        _create_s3_bucket_for_cluster(
            s3, s3_bucketname="my-bucket", region="us-west-2",
            tags={"ClusterID": "foo", "ProdLevel": "dev"},
        )
        assert s3.created_buckets == [
            {"Bucket": "my-bucket", "CreateBucketConfiguration": {"LocationConstraint": "us-west-2"}}
        ]
        assert s3.tags["my-bucket"] == {
            "TagSet": [{"Key": "ClusterID", "Value": "foo"}, {"Key": "ProdLevel", "Value": "dev"}]
        }

    def test_us_east_1_omits_location_constraint(self):
        s3 = _FakeS3()
        _create_s3_bucket_for_cluster(
            s3, s3_bucketname="my-bucket", region="us-east-1", tags={},
        )
        assert s3.created_buckets == [{"Bucket": "my-bucket"}]

    def test_already_owned_is_not_a_failure(self):
        s3 = _FakeS3(already_owned=True)
        _create_s3_bucket_for_cluster(
            s3, s3_bucketname="my-bucket", region="us-west-2", tags={"x": "y"},
        )
        assert s3.tags["my-bucket"]  # tagging still applied

    def test_other_failure_propagates(self):
        s3 = _FakeS3(raise_on_create=_client_error("AccessDenied", "CreateBucket"))
        with pytest.raises(ClientError):
            _create_s3_bucket_for_cluster(
                s3, s3_bucketname="my-bucket", region="us-west-2", tags={},
            )


class TestCreateExternalNfsSg:
    def test_creates_with_correct_ports_and_protocols(self):
        ec2 = _FakeEc2()
        group_id = _create_external_nfs_sg(
            ec2, cluster_name="foo", vpc_id="vpc-123", vpc_cidr="10.1.0.0/16",
        )
        assert group_id == "sg-created0001"
        assert ec2.created_sgs == [{"GroupName": "pcluster-foo-externalNfs", "VpcId": "vpc-123"}]
        gid, perms = ec2.authorized
        assert gid == "sg-created0001"
        assert len(perms) == 10  # 5 ports x 2 protocols
        for proto in ("tcp", "udp"):
            ports = {p["FromPort"] for p in perms if p["IpProtocol"] == proto}
            assert ports == set(_EXTERNAL_NFS_PORTS)
        assert all(p["IpRanges"] == [{"CidrIp": "10.1.0.0/16"}] for p in perms)

    def test_default_cidr_when_none_given(self):
        ec2 = _FakeEc2()
        _create_external_nfs_sg(ec2, cluster_name="foo", vpc_id="vpc-123", vpc_cidr=None)
        _, perms = ec2.authorized
        assert all(p["IpRanges"] == [{"CidrIp": "10.0.0.0/8"}] for p in perms)

    def test_duplicate_returns_existing_group_id_without_authorizing(self):
        ec2 = _FakeEc2(sg_already_exists=True)
        group_id = _create_external_nfs_sg(
            ec2, cluster_name="foo", vpc_id="vpc-123", vpc_cidr=None,
        )
        assert group_id == "sg-existing0001"
        assert ec2.authorized is None

    def test_other_failure_propagates(self):
        ec2 = _FakeEc2(raise_on_sg=_client_error("AccessDenied", "CreateSecurityGroup"))
        with pytest.raises(ClientError):
            _create_external_nfs_sg(ec2, cluster_name="foo", vpc_id="vpc-123", vpc_cidr=None)


class TestGenerateEc2Keypair:
    def test_new_keypair_returns_changed_true_with_material(self):
        ec2 = _FakeEc2()
        changed, pem = _generate_ec2_keypair(ec2, ec2_keypair="foo-key")
        assert changed is True
        assert pem == _FAKE_PEM
        assert ec2.created_keypairs == ["foo-key"]

    def test_duplicate_returns_changed_false_no_material(self):
        ec2 = _FakeEc2(keypair_already_exists=True)
        changed, pem = _generate_ec2_keypair(ec2, ec2_keypair="foo-key")
        assert changed is False
        assert pem is None

    def test_other_failure_propagates(self):
        ec2 = _FakeEc2(raise_on_keypair=_client_error("AccessDenied", "CreateKeyPair"))
        with pytest.raises(ClientError):
            _generate_ec2_keypair(ec2, ec2_keypair="foo-key")


class TestAbortIfKeypairOrphaned:
    def test_raises_when_not_changed_and_no_local_file(self):
        with pytest.raises(_ClusterProvisioningError, match="foo-key"):
            _abort_if_keypair_orphaned(
                changed=False, local_pem_exists=False,
                ec2_keypair="foo-key", region="us-west-2",
            )

    def test_does_not_raise_when_changed(self):
        _abort_if_keypair_orphaned(
            changed=True, local_pem_exists=False,
            ec2_keypair="foo-key", region="us-west-2",
        )

    def test_does_not_raise_when_local_file_exists(self):
        _abort_if_keypair_orphaned(
            changed=False, local_pem_exists=True,
            ec2_keypair="foo-key", region="us-west-2",
        )


class TestSavePrivateKeyLocally:
    def test_writes_with_mode_0600(self, tmp_path):
        import stat

        dest = tmp_path / "cluster.pem"
        _save_private_key_locally(str(dest), _FAKE_PEM)
        assert dest.read_text() == _FAKE_PEM
        mode = stat.S_IMODE(dest.stat().st_mode)
        assert mode == 0o600


class TestStoreSshSecret:
    def test_creates_secret_from_local_file(self, tmp_path):
        pem = tmp_path / "cluster.pem"
        pem.write_text(_FAKE_PEM)
        sm = _FakeSecretsManager()
        _store_ssh_secret(
            sm, ssh_secret_name="parallelcluster/foo/x/ssh-private-key",
            cluster_name="foo", ssh_keypair=str(pem),
        )
        assert sm.created_secrets[0]["SecretString"] == _FAKE_PEM
        assert sm.created_secrets[0]["Name"] == "parallelcluster/foo/x/ssh-private-key"

    def test_tolerates_resource_exists_exception(self, tmp_path):
        pem = tmp_path / "cluster.pem"
        pem.write_text(_FAKE_PEM)
        sm = _FakeSecretsManager(raise_on_create=_client_error("ResourceExistsException", "CreateSecret"))
        _store_ssh_secret(
            sm, ssh_secret_name="x", cluster_name="foo", ssh_keypair=str(pem),
        )  # must not raise

    def test_other_failure_propagates(self, tmp_path):
        pem = tmp_path / "cluster.pem"
        pem.write_text(_FAKE_PEM)
        sm = _FakeSecretsManager(raise_on_create=_client_error("AccessDenied", "CreateSecret"))
        with pytest.raises(ClientError):
            _store_ssh_secret(sm, ssh_secret_name="x", cluster_name="foo", ssh_keypair=str(pem))


class TestCleanupAfterProvisioningFailure:
    def test_cleans_up_bucket_keypair_and_secret(self):
        s3, ec2, sm = _FakeS3(), _FakeEc2(), _FakeSecretsManager()
        _cleanup_after_provisioning_failure(
            s3=s3, ec2=ec2, secretsmanager=sm,
            s3_bucketname="my-bucket", ec2_keypair="foo-key",
            ssh_secret_name="x", cluster_name="foo",
            external_nfs_sg_enabled=False,
        )
        assert s3.deleted_buckets == ["my-bucket"]
        assert ec2.deleted_keypairs == ["foo-key"]
        assert sm.deleted_secrets == ["x"]
        assert ec2.deleted_sgs == []

    def test_deletes_the_sg_only_when_external_nfs_is_enabled(self):
        s3, ec2, sm = _FakeS3(), _FakeEc2(), _FakeSecretsManager()
        _cleanup_after_provisioning_failure(
            s3=s3, ec2=ec2, secretsmanager=sm,
            s3_bucketname="my-bucket", ec2_keypair="foo-key",
            ssh_secret_name="x", cluster_name="foo",
            external_nfs_sg_enabled=True,
        )
        assert ec2.deleted_sgs == ["sg-existing0001"]

    def test_reuses_the_teardown_step_functions_rather_than_reimplementing(self):
        """Vacuity/DRY guard: these four AWS deletes already exist and are
        exhaustively tested in tests/test_teardown_steps.py -- this
        function must call them, not duplicate their logic."""
        with open(os.path.join(REPO_ROOT, "src", "pcluster_core.py")) as fh:
            source = fh.read()
        start = source.index("def _cleanup_after_provisioning_failure(")
        end = source.index("\ndef ", start + 1)
        body = source[start:end]
        for name in (
            "_delete_s3_bucket_step(",
            "_delete_ec2_keypair_step(",
            "_delete_secrets_manager_secret_step(",
            "_delete_external_nfs_sg_step(",
        ):
            assert name in body, f"{name} not called -- looks reimplemented instead of reused"


class TestProvisionS3KeypairAndSecret:
    def _kwargs(self, s3, ec2, sm, ssh_keypair, **overrides):
        base = dict(
            s3=s3, ec2=ec2, secretsmanager=sm,
            s3_bucketname="my-bucket", region="us-west-2", tags={"ClusterID": "foo"},
            enable_external_nfs=False, cluster_name="foo",
            vpc_id="vpc-123", vpc_cidr="10.1.0.0/16",
            ec2_keypair="foo-key", ssh_keypair=ssh_keypair,
            ssh_secret_name="x",
        )
        base.update(overrides)
        return base

    def test_happy_path_no_external_nfs(self, tmp_path):
        s3, ec2, sm = _FakeS3(), _FakeEc2(), _FakeSecretsManager()
        ssh_keypair = str(tmp_path / "cluster.pem")
        result = provision_s3_keypair_and_secret(
            **self._kwargs(s3, ec2, sm, ssh_keypair)
        )
        assert result is None
        assert s3.created_buckets
        assert ec2.created_keypairs == ["foo-key"]
        assert os.path.isfile(ssh_keypair)
        assert sm.created_secrets

    def test_happy_path_with_external_nfs_returns_sg_id(self, tmp_path):
        s3, ec2, sm = _FakeS3(), _FakeEc2(), _FakeSecretsManager()
        ssh_keypair = str(tmp_path / "cluster.pem")
        result = provision_s3_keypair_and_secret(
            **self._kwargs(s3, ec2, sm, ssh_keypair, enable_external_nfs=True)
        )
        assert result == "sg-created0001"

    def test_resumed_build_still_writes_the_secret_unconditionally(self, tmp_path):
        """The exact property TestTheSshSecretIsWrittenOnEveryRun pins on
        the Ansible side: an already-existing keypair (changed=False) with
        the local .pem already present must still reach the secret write."""
        s3 = _FakeS3()
        ec2 = _FakeEc2(keypair_already_exists=True)
        sm = _FakeSecretsManager()
        ssh_keypair = tmp_path / "cluster.pem"
        ssh_keypair.write_text(_FAKE_PEM)
        provision_s3_keypair_and_secret(
            **self._kwargs(s3, ec2, sm, str(ssh_keypair))
        )
        assert sm.created_secrets
        assert sm.created_secrets[0]["SecretString"] == _FAKE_PEM

    def test_orphaned_keypair_aborts_and_cleans_up_everything(self, tmp_path):
        s3 = _FakeS3()
        ec2 = _FakeEc2(keypair_already_exists=True)
        sm = _FakeSecretsManager()
        ssh_keypair = str(tmp_path / "does-not-exist.pem")
        with pytest.raises(_ClusterProvisioningError):
            provision_s3_keypair_and_secret(
                **self._kwargs(s3, ec2, sm, ssh_keypair, enable_external_nfs=True)
            )
        assert s3.deleted_buckets == ["my-bucket"]
        assert ec2.deleted_keypairs == ["foo-key"]
        assert sm.deleted_secrets == ["x"]
        assert ec2.deleted_sgs == ["sg-existing0001"]
        assert sm.created_secrets == []  # never reached

    def test_an_unexpected_failure_cleans_up_and_reraises_unchanged(self, tmp_path):
        s3 = _FakeS3()
        ec2 = _FakeEc2(raise_on_keypair=_client_error("AccessDenied", "CreateKeyPair"))
        sm = _FakeSecretsManager()
        ssh_keypair = str(tmp_path / "cluster.pem")
        with pytest.raises(ClientError) as exc:
            provision_s3_keypair_and_secret(
                **self._kwargs(s3, ec2, sm, ssh_keypair)
            )
        assert exc.value.response["Error"]["Code"] == "AccessDenied"
        assert s3.deleted_buckets == ["my-bucket"]

    def test_private_key_material_never_appears_in_output(self, tmp_path, capsys):
        s3, ec2, sm = _FakeS3(), _FakeEc2(), _FakeSecretsManager()
        ssh_keypair = str(tmp_path / "cluster.pem")
        provision_s3_keypair_and_secret(
            **self._kwargs(s3, ec2, sm, ssh_keypair)
        )
        out = capsys.readouterr()
        assert "THIS-IS-FAKE-KEY-MATERIAL" not in out.out
        assert "THIS-IS-FAKE-KEY-MATERIAL" not in out.err


# ---------------------------------------------------------------------------
# Monitoring tarball / Docker Compose CLI plugin checksum-verified downloads.
# ---------------------------------------------------------------------------


class _FakeUrlopenResponse:
    def __init__(self, content):
        self._buf = io.BytesIO(content)

    def read(self, n=-1):
        return self._buf.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(content=b"", raise_exc=None, calls=None):
    def _opener(url, timeout=None):
        if calls is not None:
            calls.append(url)
        if raise_exc:
            raise raise_exc
        return _FakeUrlopenResponse(content)

    return _opener


def _sha256_of(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


class TestDownloadWithChecksum:
    def test_downloads_and_verifies_matching_checksum(self, tmp_path, monkeypatch):
        content = b"the-tarball-bytes"
        monkeypatch.setattr(pcluster_core.urllib.request, "urlopen", _fake_urlopen(content))
        dest = tmp_path / "out.tar.gz"
        _download_with_checksum("https://example.invalid/x", str(dest), _sha256_of(content), mode=0o644)
        assert dest.read_bytes() == content

    def test_file_mode_is_set_correctly(self, tmp_path, monkeypatch):
        import stat

        content = b"plugin-binary"
        monkeypatch.setattr(pcluster_core.urllib.request, "urlopen", _fake_urlopen(content))
        dest = tmp_path / "out"
        _download_with_checksum("https://example.invalid/x", str(dest), _sha256_of(content), mode=0o755)
        assert stat.S_IMODE(dest.stat().st_mode) == 0o755

    def test_checksum_mismatch_raises_and_leaves_no_file(self, tmp_path, monkeypatch):
        content = b"the-real-bytes"
        wrong_checksum = _sha256_of(b"different-bytes")
        monkeypatch.setattr(pcluster_core.urllib.request, "urlopen", _fake_urlopen(content))
        dest = tmp_path / "out.tar.gz"
        with pytest.raises(ValueError, match="checksum mismatch"):
            _download_with_checksum("https://example.invalid/x", str(dest), wrong_checksum, mode=0o644)
        assert not dest.exists()
        assert list(tmp_path.iterdir()) == []  # no leftover temp file either

    def test_unsupported_algorithm_raises(self, tmp_path):
        dest = tmp_path / "out.tar.gz"
        with pytest.raises(ValueError, match="unsupported checksum algorithm"):
            _download_with_checksum(
                "https://example.invalid/x", str(dest), "md5:abc123", mode=0o644,
            )
        assert not dest.exists()

    def test_network_failure_leaves_no_partial_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            pcluster_core.urllib.request, "urlopen",
            _fake_urlopen(raise_exc=OSError("network unreachable")),
        )
        dest = tmp_path / "out.tar.gz"
        with pytest.raises(OSError):
            _download_with_checksum(
                "https://example.invalid/x", str(dest), _sha256_of(b"x"), mode=0o644,
            )
        assert not dest.exists()
        assert list(tmp_path.iterdir()) == []


class TestUploadFileToS3:
    def test_uploads_with_server_side_encryption_and_no_acl(self, tmp_path):
        src = tmp_path / "f.txt"
        src.write_text("x")
        s3 = _FakeS3()
        _upload_file_to_s3(s3, bucket="my-bucket", key="scripts/f.txt", src=str(src))
        assert s3.uploaded == [
            {"src": str(src), "bucket": "my-bucket", "key": "scripts/f.txt",
             "extra": {"ServerSideEncryption": "AES256"}}
        ]
        assert "ACL" not in s3.uploaded[0]["extra"]


class TestStageMonitoringTarball:
    def test_downloads_with_correct_url_and_uploads(self, tmp_path, monkeypatch):
        content = b"tarball-bytes"
        urls = []
        monkeypatch.setattr(
            pcluster_core.urllib.request, "urlopen", _fake_urlopen(content, calls=urls),
        )
        s3 = _FakeS3()
        _stage_monitoring_tarball(
            s3, cluster_data_dir=str(tmp_path), s3_bucketname="my-bucket",
            s3_script_path="cluster_scripts/prod",
            monitoring_version="v2.6.0", monitoring_version_checksum=_sha256_of(content),
        )
        assert urls == [
            "https://github.com/aws-samples/aws-parallelcluster-monitoring/"
            "archive/refs/tags/v2.6.0.tar.gz"
        ]
        assert (tmp_path / "aws-parallelcluster-monitoring-v2.6.0.tar.gz").read_bytes() == content
        assert s3.uploaded[0]["key"] == (
            "cluster_scripts/prod/aws-parallelcluster-monitoring-v2.6.0.tar.gz"
        )


class TestStageDockerComposePlugin:
    def test_downloads_with_correct_url_and_uploads(self, tmp_path, monkeypatch):
        content = b"docker-compose-binary"
        urls = []
        monkeypatch.setattr(
            pcluster_core.urllib.request, "urlopen", _fake_urlopen(content, calls=urls),
        )
        s3 = _FakeS3()
        dest = tmp_path / "docker-compose-linux-aarch64-v2.29.7"
        _stage_docker_compose_plugin(
            s3, s3_bucketname="my-bucket", s3_script_path="cluster_scripts/prod",
            docker_compose_version="v2.29.7", docker_compose_arch="aarch64",
            docker_compose_checksum=_sha256_of(content),
            docker_compose_local_dest=str(dest),
            docker_compose_s3_dest="docker-compose-linux-aarch64-v2.29.7",
        )
        assert urls == [
            "https://github.com/docker/compose/releases/download/"
            "v2.29.7/docker-compose-linux-aarch64"
        ]
        assert dest.read_bytes() == content
        assert s3.uploaded[0]["key"] == "cluster_scripts/prod/docker-compose-linux-aarch64-v2.29.7"


class TestStageAndUploadMonitoringWrapper:
    def _kwargs(self, tmp_path, wrapper_path, **overrides):
        base = dict(
            enable_monitoring=True, cluster_data_dir=str(tmp_path),
            s3_bucketname="my-bucket", s3_script_path="cluster_scripts/prod",
            monitoring_version="v2.6.0", monitoring_version_checksum=None,
            stage_docker_compose=False, docker_compose_version="v2.29.7",
            docker_compose_arch="aarch64", docker_compose_checksum=None,
            docker_compose_local_dest=str(tmp_path / "docker-compose"),
            docker_compose_s3_dest="docker-compose-linux-aarch64-v2.29.7",
            monitoring_wrapper_dest=str(wrapper_path),
            monitoring_s3_dest="monitoring-post-install-wrapper.foo.sh",
        )
        base.update(overrides)
        return base

    def test_disabled_monitoring_does_nothing(self, tmp_path, monkeypatch):
        urls = []
        monkeypatch.setattr(
            pcluster_core.urllib.request, "urlopen", _fake_urlopen(calls=urls),
        )
        s3 = _FakeS3()
        wrapper = tmp_path / "wrapper.sh"
        wrapper.write_text("#!/bin/bash\n")
        stage_and_upload_monitoring_wrapper(
            s3, **self._kwargs(tmp_path, wrapper, enable_monitoring=False),
        )
        assert urls == []
        assert s3.uploaded == []

    def test_enabled_without_docker_compose_stages_tarball_and_wrapper_only(self, tmp_path, monkeypatch):
        tarball = b"tarball-bytes"
        monkeypatch.setattr(
            pcluster_core.urllib.request, "urlopen", _fake_urlopen(tarball),
        )
        s3 = _FakeS3()
        wrapper = tmp_path / "wrapper.sh"
        wrapper.write_text("#!/bin/bash\n")
        stage_and_upload_monitoring_wrapper(
            s3, **self._kwargs(
                tmp_path, wrapper, monitoring_version_checksum=_sha256_of(tarball),
            ),
        )
        keys = [u["key"] for u in s3.uploaded]
        assert "cluster_scripts/prod/aws-parallelcluster-monitoring-v2.6.0.tar.gz" in keys
        assert "cluster_scripts/prod/monitoring-post-install-wrapper.foo.sh" in keys
        assert not any("docker-compose" in k for k in keys)

    def test_enabled_with_docker_compose_stages_all_three(self, tmp_path, monkeypatch):
        tarball = b"tarball-bytes"
        compose = b"compose-binary"
        responses = iter([tarball, compose])
        monkeypatch.setattr(
            pcluster_core.urllib.request, "urlopen",
            lambda url, timeout=None: _FakeUrlopenResponse(next(responses)),
        )
        s3 = _FakeS3()
        wrapper = tmp_path / "wrapper.sh"
        wrapper.write_text("#!/bin/bash\n")
        stage_and_upload_monitoring_wrapper(
            s3, **self._kwargs(
                tmp_path, wrapper,
                monitoring_version_checksum=_sha256_of(tarball),
                stage_docker_compose=True,
                docker_compose_checksum=_sha256_of(compose),
            ),
        )
        assert len(s3.uploaded) == 3

    def test_tarball_checksum_mismatch_stops_before_docker_compose(self, tmp_path, monkeypatch):
        """Ordering property: a bad tarball must not let the Docker Compose
        download proceed as though nothing had gone wrong."""
        tarball = b"tarball-bytes"
        urls = []
        monkeypatch.setattr(
            pcluster_core.urllib.request, "urlopen", _fake_urlopen(tarball, calls=urls),
        )
        s3 = _FakeS3()
        wrapper = tmp_path / "wrapper.sh"
        wrapper.write_text("#!/bin/bash\n")
        with pytest.raises(ValueError, match="checksum mismatch"):
            stage_and_upload_monitoring_wrapper(
                s3, **self._kwargs(
                    tmp_path, wrapper,
                    monitoring_version_checksum=_sha256_of(b"wrong-bytes"),
                    stage_docker_compose=True,
                    docker_compose_checksum=_sha256_of(b"whatever"),
                ),
            )
        assert len(urls) == 1  # only the tarball was ever attempted
        assert s3.uploaded == []


# ---------------------------------------------------------------------------
# The create-cluster launch + wait + 3-way classify, and head-node-IP
# extraction -- directly parallel to the delete side's
# run_cluster_delete_and_classify.
# ---------------------------------------------------------------------------


def _fake_sleep(_seconds):
    pass


class _FakeCreateFn:
    def __init__(self, raise_exc=None):
        self.raise_exc = raise_exc
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.raise_exc:
            raise self.raise_exc


class _ScriptedDescribeFn:
    """Same shape as test_teardown_steps.py's own -- a scripted sequence of
    describe-cluster responses (dicts) or exceptions, indexed by call
    count and clamped to the last entry once exhausted."""

    def __init__(self, sequence):
        self._sequence = list(sequence)
        self.calls = []

    def __call__(self, cluster_name, region):
        self.calls.append((cluster_name, region))
        item = self._sequence[min(len(self.calls) - 1, len(self._sequence) - 1)]
        if isinstance(item, Exception):
            raise item
        return item


def test_rollback_states_collapse_into_create_failed_in_the_installed_package():
    """Live-verified against the actually installed pcluster package (not
    assumed from reading the source alone, the same discipline
    docs/sessions.md documents for the delete side's NotFoundException
    finding): if a pcluster upgrade ever stops collapsing CloudFormation's
    ROLLBACK_* states into ClusterStatus.CREATE_FAILED,
    _wait_for_cluster_create checking only two clusterStatus outcomes
    silently stops being equivalent to the playbook's four-string check,
    and this test is what would catch that."""
    from pcluster.api.converters import cloud_formation_status_to_cluster_status
    from pcluster.api.models.cloud_formation_stack_status import CloudFormationStackStatus as CFN
    from pcluster.api.models.cluster_status import ClusterStatus

    for raw in (CFN.ROLLBACK_IN_PROGRESS, CFN.ROLLBACK_FAILED, CFN.ROLLBACK_COMPLETE):
        assert cloud_formation_status_to_cluster_status(raw) == ClusterStatus.CREATE_FAILED
    assert cloud_formation_status_to_cluster_status(CFN.CREATE_FAILED) == ClusterStatus.CREATE_FAILED
    assert cloud_formation_status_to_cluster_status(CFN.CREATE_COMPLETE) == ClusterStatus.CREATE_COMPLETE


class TestWaitForClusterCreate:
    def test_create_complete_on_first_attempt(self):
        resp = {"clusterStatus": "CREATE_COMPLETE", "headNode": {"publicIpAddress": "1.2.3.4"}}
        describe_fn = _ScriptedDescribeFn([resp])
        state, last = _wait_for_cluster_create(
            describe_fn, "foo", "us-east-1", retries=5, delay_seconds=1, sleep_fn=_fake_sleep,
        )
        assert state == "CREATE_COMPLETE"
        assert last == resp

    def test_create_failed_on_first_attempt(self):
        resp = {"clusterStatus": "CREATE_FAILED"}
        describe_fn = _ScriptedDescribeFn([resp])
        state, last = _wait_for_cluster_create(
            describe_fn, "foo", "us-east-1", retries=5, delay_seconds=1, sleep_fn=_fake_sleep,
        )
        assert state == "CREATE_FAILED"
        assert last == resp

    def test_still_building_then_completes(self):
        describe_fn = _ScriptedDescribeFn([
            {"clusterStatus": "CREATE_IN_PROGRESS"},
            {"clusterStatus": "CREATE_IN_PROGRESS"},
            {"clusterStatus": "CREATE_COMPLETE"},
        ])
        sleeps = []
        state, last = _wait_for_cluster_create(
            describe_fn, "foo", "us-east-1", retries=5, delay_seconds=1, sleep_fn=sleeps.append,
        )
        assert state == "CREATE_COMPLETE"
        assert len(describe_fn.calls) == 3
        assert len(sleeps) == 2

    def test_times_out_after_retries_exhausted(self):
        describe_fn = _ScriptedDescribeFn([{"clusterStatus": "CREATE_IN_PROGRESS"}])
        state, last = _wait_for_cluster_create(
            describe_fn, "foo", "us-east-1", retries=4, delay_seconds=1, sleep_fn=_fake_sleep,
        )
        assert state == "TIMED_OUT"
        assert last is None
        assert len(describe_fn.calls) == 4

    def test_transient_error_then_recovers(self):
        resp = {"clusterStatus": "CREATE_COMPLETE"}
        describe_fn = _ScriptedDescribeFn([RuntimeError("throttled"), resp])
        state, last = _wait_for_cluster_create(
            describe_fn, "foo", "us-east-1", retries=5, delay_seconds=1, sleep_fn=_fake_sleep,
        )
        assert state == "CREATE_COMPLETE"
        assert last == resp

    def test_persistent_error_raises_on_final_attempt(self):
        describe_fn = _ScriptedDescribeFn([RuntimeError("AccessDenied")])
        with pytest.raises(RuntimeError, match="AccessDenied"):
            _wait_for_cluster_create(
                describe_fn, "foo", "us-east-1", retries=3, delay_seconds=1, sleep_fn=_fake_sleep,
            )


class TestExtractHeadNodeIp:
    def test_prefers_public_ip(self):
        resp = {"headNode": {"publicIpAddress": "1.2.3.4", "privateIpAddress": "10.0.0.1"}}
        assert _extract_head_node_ip(resp) == "1.2.3.4"

    def test_falls_back_to_private_ip(self):
        resp = {"headNode": {"privateIpAddress": "10.0.0.1"}}
        assert _extract_head_node_ip(resp) == "10.0.0.1"

    def test_empty_when_no_response(self):
        assert _extract_head_node_ip(None) == ""

    def test_empty_when_no_head_node_key(self):
        assert _extract_head_node_ip({"clusterStatus": "CREATE_COMPLETE"}) == ""


class TestClassifyClusterCreateOutcome:
    def test_create_complete_is_confirmed(self):
        confirmed, failed, headline = _classify_cluster_create_outcome(
            "CREATE_COMPLETE", "foo", {"clusterStatus": "CREATE_COMPLETE"},
        )
        assert confirmed is True
        assert failed is False
        assert headline == "Cluster foo was created successfully."

    def test_create_failed_with_failures_detail(self):
        resp = {
            "clusterStatus": "CREATE_FAILED",
            "failures": [{"failureCode": "InsufficientCapacity", "failureReason": "no capacity"}],
        }
        confirmed, failed, headline = _classify_cluster_create_outcome("CREATE_FAILED", "foo", resp)
        assert confirmed is False
        assert failed is True
        assert "InsufficientCapacity" in headline
        assert "no capacity" in headline

    def test_create_failed_without_failures_detail(self):
        confirmed, failed, headline = _classify_cluster_create_outcome(
            "CREATE_FAILED", "foo", {"clusterStatus": "CREATE_FAILED"},
        )
        assert failed is True
        assert headline == "Cluster foo creation failed (CREATE_FAILED)."

    def test_timed_out_is_neither_confirmed_nor_failed(self):
        confirmed, failed, headline = _classify_cluster_create_outcome("TIMED_OUT", "foo", None)
        assert confirmed is False
        assert failed is False
        assert headline == "Creation of cluster foo was NOT confirmed."


class TestRunClusterCreateAndClassify:
    def _kwargs(self, create_fn, describe_fn, **overrides):
        base = dict(
            cluster_configuration_path="/tmp/config.foo",
            retries=5, delay_seconds=1, sleep_fn=_fake_sleep,
        )
        base.update(overrides)
        return dict(
            create_fn=create_fn, describe_fn=describe_fn,
            cluster_name="foo", region="us-east-1", **base,
        )

    def test_passes_the_configuration_path_not_content(self):
        create_fn = _FakeCreateFn()
        describe_fn = _ScriptedDescribeFn([{"clusterStatus": "CREATE_COMPLETE"}])
        run_cluster_create_and_classify(**self._kwargs(create_fn, describe_fn))
        assert create_fn.calls[0]["cluster_configuration"] == "/tmp/config.foo"
        assert create_fn.calls[0]["cluster_name"] == "foo"
        assert create_fn.calls[0]["region"] == "us-east-1"

    def test_rollback_on_failure_defaults_to_false(self):
        create_fn = _FakeCreateFn()
        describe_fn = _ScriptedDescribeFn([{"clusterStatus": "CREATE_COMPLETE"}])
        run_cluster_create_and_classify(**self._kwargs(create_fn, describe_fn))
        assert create_fn.calls[0]["rollback_on_failure"] is False

    def test_happy_path_returns_head_node_ip(self):
        create_fn = _FakeCreateFn()
        describe_fn = _ScriptedDescribeFn(
            [{"clusterStatus": "CREATE_COMPLETE", "headNode": {"publicIpAddress": "1.2.3.4"}}]
        )
        outcome = run_cluster_create_and_classify(**self._kwargs(create_fn, describe_fn))
        assert outcome == ClusterCreateOutcome(
            "CREATE_COMPLETE", True, False,
            "Cluster foo was created successfully.", "1.2.3.4",
        )

    def test_create_call_failure_propagates_immediately_no_tolerance(self):
        """Unlike the delete side, nothing here tolerates a failed launch
        call -- a preflight earlier in core_create_cluster's pipeline
        already rules out "cluster already exists"."""
        create_fn = _FakeCreateFn(raise_exc=RuntimeError("BadRequestException"))
        describe_fn = _ScriptedDescribeFn([{"clusterStatus": "CREATE_COMPLETE"}])
        with pytest.raises(RuntimeError, match="BadRequestException"):
            run_cluster_create_and_classify(**self._kwargs(create_fn, describe_fn))
        assert describe_fn.calls == []  # never even reached the wait loop

    def test_create_failed_flow_has_no_head_node_ip(self):
        create_fn = _FakeCreateFn()
        describe_fn = _ScriptedDescribeFn(
            [{"clusterStatus": "CREATE_FAILED", "headNode": {"publicIpAddress": "1.2.3.4"}}]
        )
        outcome = run_cluster_create_and_classify(**self._kwargs(create_fn, describe_fn))
        assert outcome.create_confirmed is False
        assert outcome.create_failed is True
        assert outcome.head_node_public_ip == ""

    def test_timeout_flow_has_no_head_node_ip(self):
        create_fn = _FakeCreateFn()
        describe_fn = _ScriptedDescribeFn([{"clusterStatus": "CREATE_IN_PROGRESS"}])
        outcome = run_cluster_create_and_classify(
            **self._kwargs(create_fn, describe_fn, retries=2)
        )
        assert outcome.terminal_state == "TIMED_OUT"
        assert outcome.create_confirmed is False
        assert outcome.head_node_public_ip == ""


# ---------------------------------------------------------------------------
# The SSH/SCP orchestration to the head node.
# ---------------------------------------------------------------------------


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _RecordingRun:
    """Stand-in for subprocess.run. `responses` maps the program name
    (argv[0]) to a _FakeCompletedProcess or an exception to raise;
    unmatched programs default to a plain rc=0 success."""

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def __call__(self, cmd, *args, **kwargs):
        self.calls.append(list(cmd))
        resp = self.responses.get(cmd[0], _FakeCompletedProcess(0))
        if isinstance(resp, Exception):
            raise resp
        if kwargs.get("check") and resp.returncode != 0:
            raise subprocess.CalledProcessError(resp.returncode, cmd)
        return resp


class _FakeSocketCtx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FailNTimesThenSucceed:
    def __init__(self, fail_times=0, always_fail=False):
        self.fail_times = fail_times
        self.always_fail = always_fail
        self.calls = 0

    def __call__(self, addr, timeout=None):
        self.calls += 1
        if self.always_fail or self.calls <= self.fail_times:
            raise OSError("connection refused")
        return _FakeSocketCtx()


class _SequencedClock:
    """Returns each value in order, clamping to the last once exhausted --
    a deterministic fake for time.monotonic()."""

    def __init__(self, values):
        self._values = list(values)
        self._i = 0

    def __call__(self):
        v = self._values[min(self._i, len(self._values) - 1)]
        self._i += 1
        return v


class TestWaitForSshPort:
    def test_succeeds_immediately(self, monkeypatch):
        conn = _FailNTimesThenSucceed(fail_times=0)
        monkeypatch.setattr(pcluster_core.socket, "create_connection", conn)
        _wait_for_ssh_port(
            "1.2.3.4", delay=5, timeout=300, sleep_fn=lambda s: None,
            time_fn=_SequencedClock([0]),
        )
        assert conn.calls == 1

    def test_sleeps_for_delay_before_the_first_probe(self, monkeypatch):
        conn = _FailNTimesThenSucceed(fail_times=0)
        monkeypatch.setattr(pcluster_core.socket, "create_connection", conn)
        sleeps = []
        _wait_for_ssh_port(
            "1.2.3.4", delay=5, timeout=300, sleep_fn=sleeps.append,
            time_fn=_SequencedClock([0]),
        )
        assert sleeps[0] == 5

    def test_retries_then_succeeds(self, monkeypatch):
        conn = _FailNTimesThenSucceed(fail_times=2)
        monkeypatch.setattr(pcluster_core.socket, "create_connection", conn)
        sleeps = []
        _wait_for_ssh_port(
            "1.2.3.4", delay=5, timeout=300, poll_interval=1, sleep_fn=sleeps.append,
            time_fn=_SequencedClock([0, 1, 2, 3]),
        )
        assert conn.calls == 3
        assert sleeps == [5, 1, 1]  # the initial delay, then one per retry

    def test_raises_timeout_error_when_port_never_opens(self, monkeypatch):
        conn = _FailNTimesThenSucceed(always_fail=True)
        monkeypatch.setattr(pcluster_core.socket, "create_connection", conn)
        with pytest.raises(TimeoutError, match="1.2.3.4"):
            _wait_for_ssh_port(
                "1.2.3.4", delay=5, timeout=300, sleep_fn=lambda s: None,
                time_fn=_SequencedClock([0, 1000]),
            )


class TestEnsureLocalSshDir:
    def test_creates_the_directory_with_mode_0700(self, tmp_path):
        import stat

        known_hosts = tmp_path / "ssh" / "known_hosts"
        _ensure_local_ssh_dir(str(known_hosts))
        assert known_hosts.parent.is_dir()
        assert stat.S_IMODE(known_hosts.parent.stat().st_mode) == 0o700

    def test_corrects_an_already_existing_directorys_mode(self, tmp_path):
        import stat

        ssh_dir = tmp_path / "ssh"
        ssh_dir.mkdir(mode=0o755)
        _ensure_local_ssh_dir(str(ssh_dir / "known_hosts"))
        assert stat.S_IMODE(ssh_dir.stat().st_mode) == 0o700


class TestRemoveStaleKnownHostsEntry:
    def test_success(self, tmp_path, monkeypatch):
        runner = _RecordingRun()
        monkeypatch.setattr(subprocess, "run", runner)
        _remove_stale_known_hosts_entry("1.2.3.4", str(tmp_path / "known_hosts"))
        assert runner.calls[0][:2] == ["ssh-keygen", "-R"]

    def test_never_raises_regardless_of_ssh_keygen_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            pcluster_core.subprocess, "run",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no such file")),
        )
        _remove_stale_known_hosts_entry("1.2.3.4", str(tmp_path / "known_hosts"))  # must not raise


class TestAcceptSshFingerprint:
    def test_appends_new_fingerprint_lines(self, tmp_path, monkeypatch):
        known_hosts = tmp_path / "known_hosts"
        monkeypatch.setattr(
            pcluster_core.subprocess, "run",
            lambda *a, **k: _FakeCompletedProcess(0, stdout="1.2.3.4 ssh-ed25519 AAAA...\n"),
        )
        added = _accept_ssh_fingerprint("1.2.3.4", str(known_hosts))
        assert added is True
        assert "1.2.3.4 ssh-ed25519 AAAA..." in known_hosts.read_text()

    def test_already_present_line_is_not_duplicated_and_reports_unchanged(self, tmp_path, monkeypatch):
        known_hosts = tmp_path / "known_hosts"
        known_hosts.write_text("1.2.3.4 ssh-ed25519 AAAA...\n")
        monkeypatch.setattr(
            pcluster_core.subprocess, "run",
            lambda *a, **k: _FakeCompletedProcess(0, stdout="1.2.3.4 ssh-ed25519 AAAA...\n"),
        )
        added = _accept_ssh_fingerprint("1.2.3.4", str(known_hosts))
        assert added is False
        assert known_hosts.read_text().count("1.2.3.4 ssh-ed25519 AAAA...") == 1

    def test_no_host_key_at_all_raises(self, tmp_path, monkeypatch):
        known_hosts = tmp_path / "known_hosts"
        monkeypatch.setattr(
            pcluster_core.subprocess, "run",
            lambda *a, **k: _FakeCompletedProcess(0, stdout=""),
        )
        with pytest.raises(RuntimeError, match="no host key"):
            _accept_ssh_fingerprint("1.2.3.4", str(known_hosts))


class TestCreatePerformanceDirOnHeadNode:
    def test_runs_ssh_mkdir(self, monkeypatch):
        runner = _RecordingRun()
        monkeypatch.setattr(subprocess, "run", runner)
        _create_performance_dir_on_head_node(
            ssh_keypair="/x.pem", ec2_user="ubuntu", head_node_ip="1.2.3.4",
            headnode_performance_dir_dest="/home/ubuntu/hpc-benchmark/foo",
        )
        cmd = runner.calls[0]
        assert cmd[0] == "ssh"
        assert cmd[-1] == "mkdir -p /home/ubuntu/hpc-benchmark/foo"


class TestTransferStagingDir:
    def test_ssh_mkdir_then_scp_dash_r(self, monkeypatch):
        runner = _RecordingRun()
        monkeypatch.setattr(subprocess, "run", runner)
        _transfer_staging_dir(
            ssh_keypair="/x.pem", ec2_user="ubuntu", head_node_ip="1.2.3.4",
            stage_dir="/tmp/_stage/serial123",
        )
        assert runner.calls[0][0] == "ssh"
        assert runner.calls[0][-1] == "mkdir -p /tmp/_stage"
        assert runner.calls[1][0] == "scp"
        assert "-r" in runner.calls[1]
        assert runner.calls[1][-2] == "/tmp/_stage/serial123"
        assert runner.calls[1][-1] == "ubuntu@1.2.3.4:/tmp/_stage/"

    def test_scp_is_skipped_when_the_remote_mkdir_fails(self, monkeypatch):
        runner = _RecordingRun(responses={"ssh": _FakeCompletedProcess(255)})
        monkeypatch.setattr(subprocess, "run", runner)
        with pytest.raises(subprocess.CalledProcessError):
            _transfer_staging_dir(
                ssh_keypair="/x.pem", ec2_user="ubuntu", head_node_ip="1.2.3.4",
                stage_dir="/tmp/_stage/serial123",
            )
        assert all(c[0] != "scp" for c in runner.calls)


class TestTransferSbatchScript:
    def test_scp_of_the_sbatch_script(self, monkeypatch):
        runner = _RecordingRun()
        monkeypatch.setattr(subprocess, "run", runner)
        _transfer_sbatch_script(
            ssh_keypair="/x.pem", ec2_user="ubuntu", ec2_user_home="/home/ubuntu",
            head_node_ip="1.2.3.4", stage_dir="/tmp/_stage/serial123",
        )
        cmd = runner.calls[0]
        assert cmd[0] == "scp"
        assert cmd[-2] == "/tmp/_stage/serial123/sbatch_default_submission_script.sh"
        assert cmd[-1] == "ubuntu@1.2.3.4:/home/ubuntu"


class TestCopyPerformanceSourceTree:
    def test_expands_the_glob_and_scps_every_match(self, tmp_path, monkeypatch):
        perf_dir = tmp_path / "perf_stage"
        perf_dir.mkdir()
        (perf_dir / "a.sh").write_text("x")
        (perf_dir / "b.sh").write_text("x")
        runner = _RecordingRun()
        monkeypatch.setattr(subprocess, "run", runner)
        _copy_performance_source_tree(
            ssh_keypair="/x.pem", ec2_user="ubuntu", head_node_ip="1.2.3.4",
            performance_stage_dir=str(perf_dir),
            headnode_performance_dir_dest="/home/ubuntu/hpc-benchmark/foo",
        )
        cmd = runner.calls[0]
        assert str(perf_dir / "a.sh") in cmd
        assert str(perf_dir / "b.sh") in cmd
        assert cmd[-1] == "ubuntu@1.2.3.4:/home/ubuntu/hpc-benchmark/foo"

    def test_an_empty_directory_copies_nothing_rather_than_failing(self, tmp_path, monkeypatch):
        perf_dir = tmp_path / "perf_stage"
        perf_dir.mkdir()
        runner = _RecordingRun()
        monkeypatch.setattr(subprocess, "run", runner)
        _copy_performance_source_tree(
            ssh_keypair="/x.pem", ec2_user="ubuntu", head_node_ip="1.2.3.4",
            performance_stage_dir=str(perf_dir),
            headnode_performance_dir_dest="/home/ubuntu/hpc-benchmark/foo",
        )
        assert runner.calls == []


class TestBuildActivePerfDirs:
    def test_ebs_always_included(self):
        dirs = _build_active_perf_dirs(
            ebs_hpc_performance_dir="/shared/hpc", enable_efs=False,
            efs_hpc_performance_dir="/efs/hpc", enable_fsx=False,
            fsx_hpc_performance_dir="/fsx/hpc",
        )
        assert dirs == ["/shared/hpc"]

    def test_efs_and_fsx_appended_when_enabled(self):
        dirs = _build_active_perf_dirs(
            ebs_hpc_performance_dir="/shared/hpc", enable_efs=True,
            efs_hpc_performance_dir="/efs/hpc", enable_fsx=True,
            fsx_hpc_performance_dir="/fsx/hpc",
        )
        assert dirs == ["/shared/hpc", "/efs/hpc", "/fsx/hpc"]


class TestCreateAndOwnPerfDirs:
    def test_three_phases_over_every_directory_in_order(self, monkeypatch):
        """mkdir dir1, mkdir dir2, then chown dir1, chown dir2, then cp
        dir1, cp dir2 -- not interleaved per directory, matching three
        separate looped Ansible tasks running to completion in sequence."""
        runner = _RecordingRun()
        monkeypatch.setattr(subprocess, "run", runner)
        _create_and_own_perf_dirs(
            ssh_keypair="/x.pem", ec2_user="ubuntu", head_node_ip="1.2.3.4",
            perf_dirs=["/shared/hpc", "/efs/hpc"],
            headnode_performance_dir_dest="/home/ubuntu/hpc-benchmark/foo",
        )
        remote_commands = [c[-1] for c in runner.calls]
        assert remote_commands == [
            "sudo mkdir -p /shared/hpc",
            "sudo mkdir -p /efs/hpc",
            "sudo chown -R ubuntu:ubuntu /shared/hpc",
            "sudo chown -R ubuntu:ubuntu /efs/hpc",
            "cp -a /home/ubuntu/hpc-benchmark/foo/* /shared/hpc",
            "cp -a /home/ubuntu/hpc-benchmark/foo/* /efs/hpc",
        ]

    def test_empty_perf_dirs_runs_nothing(self, monkeypatch):
        runner = _RecordingRun()
        monkeypatch.setattr(subprocess, "run", runner)
        _create_and_own_perf_dirs(
            ssh_keypair="/x.pem", ec2_user="ubuntu", head_node_ip="1.2.3.4",
            perf_dirs=[], headnode_performance_dir_dest="/home/ubuntu/hpc-benchmark/foo",
        )
        assert runner.calls == []


class TestRemoveHeadNodeStagingDir:
    def test_ssh_rm_rf(self, monkeypatch):
        runner = _RecordingRun()
        monkeypatch.setattr(subprocess, "run", runner)
        _remove_head_node_staging_dir(
            ssh_keypair="/x.pem", ec2_user="ubuntu", head_node_ip="1.2.3.4",
            stage_dir="/tmp/_stage/serial123",
        )
        assert runner.calls[0][-1] == "rm -rf /tmp/_stage/serial123"


class TestDeployStagingAndPerformanceTreeToHeadNode:
    def _kwargs(self, tmp_path, **overrides):
        base = dict(
            head_node_public_ip="1.2.3.4",
            ssh_keypair="/x.pem",
            ssh_known_hosts=str(tmp_path / "ssh" / "known_hosts"),
            ec2_user="ubuntu", ec2_user_home="/home/ubuntu",
            stage_dir=str(tmp_path / "_stage" / "serial123"),
            enable_hpc_benchmarks=False,
            performance_stage_dir=str(tmp_path / "perf_stage"),
            headnode_performance_dir_dest="/home/ubuntu/hpc-benchmark/foo",
            ebs_hpc_performance_dir="/shared/hpc", enable_efs=False,
            efs_hpc_performance_dir="/efs/hpc", enable_fsx=False,
            fsx_hpc_performance_dir="/fsx/hpc",
        )
        base.update(overrides)
        return base

    def _stub_everything(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, *a, **k: _FakeCompletedProcess(
                0, stdout="1.2.3.4 ssh-ed25519 AAAA...\n" if cmd[0] == "ssh-keyscan" else "",
            ),
        )
        monkeypatch.setattr(
            pcluster_core.socket, "create_connection",
            lambda addr, timeout=None: _FakeSocketCtx(),
        )
        monkeypatch.setattr(pcluster_core.time, "sleep", lambda s: None)

    def test_empty_head_node_ip_does_nothing(self, tmp_path, monkeypatch):
        self._stub_everything(monkeypatch)
        calls = []
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a) or _FakeCompletedProcess(0))
        deploy_staging_and_performance_tree_to_head_node(
            **self._kwargs(tmp_path, head_node_public_ip=""),
        )
        assert calls == []

    def test_without_hpc_benchmarks_skips_performance_steps(self, tmp_path, monkeypatch):
        self._stub_everything(monkeypatch)
        runner = _RecordingRun()

        def _run(cmd, *a, **k):
            runner(cmd, *a, **k)
            if cmd[0] == "ssh-keyscan":
                return _FakeCompletedProcess(0, stdout="1.2.3.4 ssh-ed25519 AAAA...\n")
            return _FakeCompletedProcess(0)

        monkeypatch.setattr(subprocess, "run", _run)
        deploy_staging_and_performance_tree_to_head_node(**self._kwargs(tmp_path))
        remote_commands = " ".join(c[-1] for c in runner.calls if c[0] == "ssh")
        assert "hpc-benchmark" not in remote_commands

    def test_with_hpc_benchmarks_runs_the_full_sequence(self, tmp_path, monkeypatch):
        self._stub_everything(monkeypatch)
        perf_dir = tmp_path / "perf_stage"
        perf_dir.mkdir()
        (perf_dir / "hpc-benchmark.sh").write_text("x")
        runner = _RecordingRun()

        def _run(cmd, *a, **k):
            runner(cmd, *a, **k)
            if cmd[0] == "ssh-keyscan":
                return _FakeCompletedProcess(0, stdout="1.2.3.4 ssh-ed25519 AAAA...\n")
            return _FakeCompletedProcess(0)

        monkeypatch.setattr(subprocess, "run", _run)
        deploy_staging_and_performance_tree_to_head_node(
            **self._kwargs(tmp_path, enable_hpc_benchmarks=True),
        )
        programs = [c[0] for c in runner.calls]
        assert programs.count("scp") >= 3  # staging dir, sbatch script, perf source tree
        remote_commands = [c[-1] for c in runner.calls if c[0] == "ssh"]
        assert any("mkdir -p /home/ubuntu/hpc-benchmark/foo" == cmd for cmd in remote_commands)
        assert any(cmd.startswith("sudo mkdir -p") for cmd in remote_commands)
        assert any(cmd.startswith("sudo chown -R") for cmd in remote_commands)
        assert any(cmd.startswith("cp -a") for cmd in remote_commands)
        assert any(cmd.startswith("rm -rf") for cmd in remote_commands)

    def test_known_hosts_setup_happens_before_any_scp(self, tmp_path, monkeypatch):
        self._stub_everything(monkeypatch)
        runner = _RecordingRun()

        def _run(cmd, *a, **k):
            runner(cmd, *a, **k)
            if cmd[0] == "ssh-keyscan":
                return _FakeCompletedProcess(0, stdout="1.2.3.4 ssh-ed25519 AAAA...\n")
            return _FakeCompletedProcess(0)

        monkeypatch.setattr(subprocess, "run", _run)
        deploy_staging_and_performance_tree_to_head_node(**self._kwargs(tmp_path))
        programs = [c[0] for c in runner.calls]
        assert programs.index("ssh-keyscan") < programs.index("scp")


# ---------------------------------------------------------------------------
# Everything else create_pcluster.yml still does: SNS topic + notify,
# config.pcluster.j2's render + script/config uploads, external NFS mount
# list upload, HPC driver upload + results bucket, the pre-launch summary
# print, staging-dir finalization, and the final SNS build-summary report.
# ---------------------------------------------------------------------------

REPO_ROOT_TEMPLATES = os.path.join(REPO_ROOT, "templates")


class _FakeSns:
    def __init__(self, raise_on_publish=None):
        self.raise_on_publish = raise_on_publish
        self.topics = {}
        self.attrs = {}
        self.subscriptions = []
        self.published = []

    def create_topic(self, Name):
        arn = f"arn:aws:sns:us-east-1:123456789012:{Name}"
        self.topics[Name] = arn
        return {"TopicArn": arn}

    def set_topic_attributes(self, TopicArn, AttributeName, AttributeValue):
        self.attrs[(TopicArn, AttributeName)] = AttributeValue

    def subscribe(self, TopicArn, Protocol, Endpoint):
        self.subscriptions.append((TopicArn, Protocol, Endpoint))

    def publish(self, TopicArn, Message, Subject):
        if self.raise_on_publish:
            raise self.raise_on_publish
        self.published.append({"TopicArn": TopicArn, "Message": Message, "Subject": Subject})


def _extend_ctx_for_full_pipeline(cluster_params, tmp_path):
    """cluster_params (conftest.py) already carries every variable any
    *template* needs; these composing functions also need real local file
    paths for the non-template half of their work (writes, copies,
    uploads), which no template render exercises and the fixture
    therefore never had reason to carry."""
    cluster_data_dir = tmp_path / "active_clusters" / "test-cluster"
    cluster_data_dir.mkdir(parents=True)
    stage_dir = tmp_path / "stage"
    stage_dir.mkdir()
    preinstall_rendered = cluster_data_dir / "preinstall.test-cluster.sh"
    preinstall_rendered.write_text("#!/bin/bash\n")
    postinstall_rendered = cluster_data_dir / "postinstall.test-cluster.sh"
    postinstall_rendered.write_text("#!/bin/bash\n")
    user_preinstall_src = tmp_path / "pre-deployment.sh"
    user_preinstall_src.write_text("#!/bin/bash\n")
    user_postinstall_src = tmp_path / "post-deployment.sh"
    user_postinstall_src.write_text("#!/bin/bash\n")
    external_nfs_mount_list_src = cluster_data_dir / "external_nfs_mount_list.test-cluster.conf"
    external_nfs_mount_list_src.write_text("nfs.example.com:/export /nfs nfs defaults 0 0\n")
    performance_rootdir = tmp_path / "hpc-benchmark"
    performance_rootdir.mkdir()
    (performance_rootdir / "hpc-benchmark.sh").write_text("#!/bin/bash\n")
    performance_stage_dir = stage_dir / "hpc-benchmark" / "slurm"
    performance_stage_dir.mkdir(parents=True)

    return {
        **cluster_params,
        "cluster_data_dir": str(cluster_data_dir),
        "stage_dir": str(stage_dir),
        "cluster_config_template": str(cluster_data_dir / "config.test-cluster"),
        "cluster_config_dest": "config.test-cluster",
        "preinstall_rendered": str(preinstall_rendered),
        "postinstall_rendered": str(postinstall_rendered),
        "user_preinstall_src": str(user_preinstall_src),
        "user_postinstall_src": str(user_postinstall_src),
        "external_nfs_mount_list_template_src": str(external_nfs_mount_list_src),
        "performance_rootdir": str(performance_rootdir),
        "performance_stage_dir": str(performance_stage_dir),
        "sns_build_summary_report_dest": str(cluster_data_dir / "sns_build_summary.test-cluster.txt"),
    }


class TestCreateSnsTopicAndNotify:
    def test_creates_subscribes_and_notifies(self):
        sns = _FakeSns()
        arn = _create_sns_topic_and_notify(
            sns, cluster_name="foo", cluster_owner_email="owner@example.com",
            start_timestamp="2026-01-01 00:00:00",
        )
        assert arn == "arn:aws:sns:us-east-1:123456789012:sns_alerts_foo"
        assert sns.subscriptions == [(arn, "email", "owner@example.com")]
        assert sns.published[0]["TopicArn"] == arn
        assert "foo" in sns.published[0]["Message"]


class TestRenderAndUploadClusterConfigAndScripts:
    def test_renders_config_and_uploads_everything(self, cluster_params, tmp_path):
        ctx = _extend_ctx_for_full_pipeline(cluster_params, tmp_path)
        s3 = _FakeS3()
        render_and_upload_cluster_config_and_scripts(
            s3, ctx=ctx, external_nfs_sg_id="sg-abc123", templates_dir=REPO_ROOT_TEMPLATES,
        )
        assert os.path.isfile(ctx["cluster_config_template"])
        assert "sg-abc123" in open(ctx["cluster_config_template"]).read()
        keys = [u["key"] for u in s3.uploaded]
        assert f"{ctx['s3_script_path']}/{ctx['cluster_config_dest']}" in keys
        assert f"{ctx['s3_script_path']}/{ctx['preinstall_s3_dest']}" in keys
        assert f"{ctx['s3_script_path']}/{ctx['postinstall_s3_dest']}" in keys
        assert f"{ctx['s3_script_path']}/{ctx['user_preinstall_s3_dest']}" in keys
        assert f"{ctx['s3_script_path']}/{ctx['user_postinstall_s3_dest']}" in keys

    def test_does_not_collide_when_ctx_already_carries_external_nfs_sg(self, cluster_params, tmp_path):
        """cluster_params already carries its own external_nfs_sg (built
        for other templates); this must not raise a "multiple values for
        keyword argument" TypeError."""
        ctx = _extend_ctx_for_full_pipeline(cluster_params, tmp_path)
        assert "external_nfs_sg" in ctx  # the fixture's own precondition
        s3 = _FakeS3()
        render_and_upload_cluster_config_and_scripts(
            s3, ctx=ctx, external_nfs_sg_id="sg-the-real-one", templates_dir=REPO_ROOT_TEMPLATES,
        )
        assert "sg-the-real-one" in open(ctx["cluster_config_template"]).read()
        assert "sg-0abc123externalnfs" not in open(ctx["cluster_config_template"]).read()


class TestUploadExternalNfsMountList:
    def test_uploads_to_the_right_key(self, cluster_params, tmp_path):
        ctx = _extend_ctx_for_full_pipeline(cluster_params, tmp_path)
        s3 = _FakeS3()
        _upload_external_nfs_mount_list(s3, ctx=ctx)
        assert s3.uploaded[0]["key"] == (
            f"{ctx['s3_script_path']}/{ctx['external_nfs_mount_list_template_dest']}"
        )


class TestCreateHpcResultsBucket:
    def test_creates_public_access_block_and_tags(self):
        s3 = _FakeS3()
        s3.put_public_access_block = lambda **kw: setattr(s3, "_pab", kw)
        _create_hpc_results_bucket(s3, results_bucketname="parallelclustermaker-results-x", region="us-west-2")
        assert s3.created_buckets
        assert s3.tags["parallelclustermaker-results-x"]["TagSet"][0]["Key"] == "Name"

    def test_already_owned_is_not_a_failure(self):
        s3 = _FakeS3(already_owned=True)
        s3.put_public_access_block = lambda **kw: None
        _create_hpc_results_bucket(s3, results_bucketname="x", region="us-east-1")
        assert s3.tags["x"]  # tagging still applied


class TestStageAndUploadHpcBenchmarkDriver:
    def test_copies_uploads_and_creates_results_bucket(self, cluster_params, tmp_path, monkeypatch):
        ctx = _extend_ctx_for_full_pipeline(cluster_params, tmp_path)
        s3 = _FakeS3()
        s3.put_public_access_block = lambda **kw: None
        runner = _RecordingRun()
        monkeypatch.setattr(subprocess, "run", runner)
        stage_and_upload_hpc_benchmark_driver(s3, ctx=ctx, region="us-east-1")
        dest = os.path.join(ctx["performance_stage_dir"], "hpc-benchmark.sh")
        assert os.path.isfile(dest)
        cmd = runner.calls[0]
        assert cmd[:3] == ["aws", "s3", "sync"]
        assert "--include" in cmd and "hpc-benchmark.sh" in cmd
        assert s3.created_buckets  # the results bucket


class TestPrintClusterLaunchSummary:
    def test_prints_the_core_fields(self, cluster_params, capsys):
        print_cluster_launch_summary(cluster_params, launch_timestamp="2026-01-01 00:00:00")
        out = capsys.readouterr().out
        assert "test-cluster" in out
        assert "Cluster Launch Summary" in out
        assert "us-east-1a" in out

    def test_gated_lines_only_appear_when_enabled(self, cluster_params, capsys):
        ctx = {**cluster_params, "enable_monitoring": "true"}
        print_cluster_launch_summary(ctx, launch_timestamp="x")
        assert "Monitoring:        TRUE" in capsys.readouterr().out

        capsys.readouterr()
        print_cluster_launch_summary(cluster_params, launch_timestamp="x")
        assert "Monitoring:        TRUE" not in capsys.readouterr().out


class TestFinalizeStagingDirectory:
    def test_copies_syncs_and_removes_the_staging_dir(self, tmp_path, monkeypatch):
        stage_dir = tmp_path / "stage"
        stage_dir.mkdir()
        (stage_dir / "f.sh").write_text("x")
        cluster_data_dir = tmp_path / "active_clusters" / "foo"
        cluster_data_dir.mkdir(parents=True)
        (stage_dir / "secret.pem").write_text("PRIVATE KEY")

        uploads = []

        class _S3:
            def upload_file(self, filename, bucket, key):
                uploads.append((filename, bucket, key))

        finalize_staging_directory(
            _S3(), stage_dir=str(stage_dir),
            cluster_data_dir=str(cluster_data_dir),
            s3_bucketname="my-bucket", region="us-east-1",
        )
        assert (cluster_data_dir / "f.sh").exists()
        assert not stage_dir.exists()

        keys = [k for _f, _b, k in uploads]
        assert "f.sh" in keys
        assert all(b == "my-bucket" for _f, b, _k in uploads)
        # boto3 now, not `aws s3 sync`: the CLI is not in the container
        # tier's image, so the subprocess this replaces was a dependency on
        # a binary that is absent wherever this runs remotely.
        assert not any(k.endswith(".pem") for k in keys), (
            f"a private key reached S3: {keys}")


class TestRenderAndPublishBuildSummaryReport:
    def test_renders_writes_and_publishes(self, cluster_params, tmp_path):
        ctx = _extend_ctx_for_full_pipeline(cluster_params, tmp_path)
        sns = _FakeSns()
        render_and_publish_build_summary_report(
            sns, ctx=ctx, sns_topic_arn="arn:aws:sns:us-east-1:123456789012:sns_alerts_foo",
            templates_dir=REPO_ROOT_TEMPLATES, head_node_public_ip="1.2.3.4",
            start_overall_timestamp="t1", start_stack_timestamp="t2",
            stop_stack_timestamp="t3", stop_overall_timestamp="t4",
        )
        assert os.path.isfile(ctx["sns_build_summary_report_dest"])
        report = open(ctx["sns_build_summary_report_dest"]).read()
        assert "1.2.3.4" in report
        assert sns.published[0]["Message"] == report

    def test_a_publish_failure_does_not_raise(self, cluster_params, tmp_path):
        """Best-effort, matching every other SNS publish in this codebase
        (round 19's delete-side report is equally tolerant)."""
        ctx = _extend_ctx_for_full_pipeline(cluster_params, tmp_path)
        sns = _FakeSns(raise_on_publish=RuntimeError("throttled"))
        render_and_publish_build_summary_report(
            sns, ctx=ctx, sns_topic_arn="arn:x", templates_dir=REPO_ROOT_TEMPLATES,
            head_node_public_ip="1.2.3.4", start_overall_timestamp="t1",
            start_stack_timestamp="t2", stop_stack_timestamp="t3", stop_overall_timestamp="t4",
        )  # must not raise
        assert os.path.isfile(ctx["sns_build_summary_report_dest"])  # written anyway


class TestPrintFsxHydrationHelperLocations:
    def test_prints_when_enabled(self, cluster_params, capsys):
        print_fsx_hydration_helper_locations(cluster_params)  # enable_fsx_hydration: "true"
        out = capsys.readouterr().out
        assert "test-import-bucket" in out
        assert "import-s3-to-lustre.sh" in out

    def test_silent_when_disabled(self, cluster_params, capsys):
        ctx = {**cluster_params, "enable_fsx_hydration": "false"}
        print_fsx_hydration_helper_locations(ctx)
        assert capsys.readouterr().out == ""


class TestWaitFalseKicksOffWithoutPolling:
    """Workstream 4's core async parameter: one function, one parameter,
    two callers. wait=True (the default, and what every CLI shim passes)
    polls internally and blocks -- structurally identical to what the
    Ansible until:/retries: loop always did, so CLI behavior is preserved
    by construction rather than by parallel-implementation discipline.
    wait=False returns immediately after the kickoff call, which is what a
    future MCP tool wrapper needs since a single tool call cannot block
    for the 20-45 minutes a real build takes."""

    def _kwargs(self, create_fn, describe_fn, **overrides):
        base = dict(
            cluster_configuration_path="/tmp/config.foo",
            retries=5, delay_seconds=1, sleep_fn=_fake_sleep,
        )
        base.update(overrides)
        return dict(
            create_fn=create_fn, describe_fn=describe_fn,
            cluster_name="foo", region="us-east-1", **base,
        )

    def test_wait_false_still_launches_the_cluster(self):
        create_fn = _FakeCreateFn()
        describe_fn = _ScriptedDescribeFn([{"clusterStatus": "CREATE_COMPLETE"}])
        run_cluster_create_and_classify(**self._kwargs(create_fn, describe_fn, wait=False))
        assert len(create_fn.calls) == 1, "the kickoff must still happen"

    def test_wait_false_never_polls(self):
        """The whole point: no describe_cluster call at all, not merely a
        shorter wait."""
        create_fn = _FakeCreateFn()
        describe_fn = _ScriptedDescribeFn([{"clusterStatus": "CREATE_COMPLETE"}])
        run_cluster_create_and_classify(**self._kwargs(create_fn, describe_fn, wait=False))
        assert describe_fn.calls == []

    def test_wait_false_reports_kicked_off_not_timed_out(self):
        """_KICKED_OFF must be distinct from _TIMED_OUT: conflating them
        would make a caller that never intended to wait look like a failed
        build, and _TIMED_OUT is a real failure signal on both sides."""
        create_fn = _FakeCreateFn()
        describe_fn = _ScriptedDescribeFn([{"clusterStatus": "CREATE_IN_PROGRESS"}])
        outcome = run_cluster_create_and_classify(
            **self._kwargs(create_fn, describe_fn, wait=False)
        )
        assert outcome.terminal_state == _KICKED_OFF
        assert outcome.terminal_state != "TIMED_OUT"

    def test_wait_false_confirms_nothing(self):
        """create_confirmed gates every post-launch step in
        core_create_cluster (SSH orchestration, staging sync, the build
        summary). "We did not look" is not confirmation, so both flags
        must be False -- an outcome that claimed confirmation here would
        send the whole post-launch pipeline at a cluster that may not
        exist yet."""
        create_fn = _FakeCreateFn()
        describe_fn = _ScriptedDescribeFn([{"clusterStatus": "CREATE_COMPLETE"}])
        outcome = run_cluster_create_and_classify(
            **self._kwargs(create_fn, describe_fn, wait=False)
        )
        assert outcome.create_confirmed is False
        assert outcome.create_failed is False
        assert outcome.head_node_public_ip == ""

    def test_wait_false_headline_tells_the_caller_how_to_poll(self):
        create_fn = _FakeCreateFn()
        describe_fn = _ScriptedDescribeFn([{"clusterStatus": "CREATE_COMPLETE"}])
        outcome = run_cluster_create_and_classify(
            **self._kwargs(create_fn, describe_fn, wait=False)
        )
        assert "not waiting" in outcome.create_headline
        assert "describe-cluster" in outcome.create_headline
        assert "foo" in outcome.create_headline

    def test_wait_defaults_to_true(self):
        """The CLI shim must keep blocking without passing anything --
        a default of False would silently turn every existing CLI build
        into a fire-and-forget, the exact regression the top-of-plan
        "CLI behavior cannot change" constraint forbids."""
        create_fn = _FakeCreateFn()
        describe_fn = _ScriptedDescribeFn([{"clusterStatus": "CREATE_COMPLETE"}])
        outcome = run_cluster_create_and_classify(**self._kwargs(create_fn, describe_fn))
        assert outcome.create_confirmed is True
        assert describe_fn.calls, "the default must still poll"

    def test_a_failed_kickoff_still_raises_under_wait_false(self):
        """wait=False changes whether we poll, never whether a failed
        launch is tolerated -- create_fn's own exception must still
        propagate."""
        create_fn = _FakeCreateFn(raise_exc=RuntimeError("AccessDenied"))
        describe_fn = _ScriptedDescribeFn([{"clusterStatus": "CREATE_COMPLETE"}])
        with pytest.raises(RuntimeError, match="AccessDenied"):
            run_cluster_create_and_classify(**self._kwargs(create_fn, describe_fn, wait=False))


class TestBuildProgressIsReportedDuringTheWait:
    """The 20-45 minute wait was previously entirely silent (the Ansible
    until: loop printed nothing per attempt), so an operator could not
    tell a healthy slow build from a hung one without opening the
    CloudFormation console. A deliberate, scoped exception to "CLI
    behavior unchanged" -- the final build summary it precedes is still
    byte-identical."""

    def _kwargs(self, create_fn, describe_fn, **overrides):
        base = dict(
            cluster_configuration_path="/tmp/config.foo",
            retries=5, delay_seconds=1, sleep_fn=_fake_sleep,
        )
        base.update(overrides)
        return dict(
            create_fn=create_fn, describe_fn=describe_fn,
            cluster_name="foo", region="us-east-1", **base,
        )

    def test_progress_fn_fires_once_per_non_terminal_poll(self):
        create_fn = _FakeCreateFn()
        describe_fn = _ScriptedDescribeFn([
            {"clusterStatus": "CREATE_IN_PROGRESS"},
            {"clusterStatus": "CREATE_IN_PROGRESS"},
            {"clusterStatus": "CREATE_COMPLETE"},
        ])
        seen = []
        run_cluster_create_and_classify(
            **self._kwargs(create_fn, describe_fn, progress_fn=lambda *a: seen.append(a))
        )
        assert len(seen) == 2, "one line per non-terminal poll, none for the terminal one"

    def test_progress_fn_receives_attempt_status_and_cfn_status(self):
        """cloudFormationStackStatus is the field that actually carries
        ROLLBACK_* detail -- clusterStatus collapses all of those to
        CREATE_FAILED (the round-23 finding), so a progress line showing
        only clusterStatus would hide the one thing an operator watching a
        slow build wants to see."""
        create_fn = _FakeCreateFn()
        describe_fn = _ScriptedDescribeFn([
            {"clusterStatus": "CREATE_IN_PROGRESS",
             "cloudFormationStackStatus": "CREATE_IN_PROGRESS"},
            {"clusterStatus": "CREATE_COMPLETE"},
        ])
        seen = []
        run_cluster_create_and_classify(
            **self._kwargs(create_fn, describe_fn, progress_fn=lambda *a: seen.append(a))
        )
        assert seen == [(0, "CREATE_IN_PROGRESS", "CREATE_IN_PROGRESS")]

    def test_no_progress_fn_is_silent_and_still_works(self):
        """The default must stay None: every existing caller and test that
        does not opt in must behave exactly as before."""
        create_fn = _FakeCreateFn()
        describe_fn = _ScriptedDescribeFn([
            {"clusterStatus": "CREATE_IN_PROGRESS"},
            {"clusterStatus": "CREATE_COMPLETE"},
        ])
        outcome = run_cluster_create_and_classify(**self._kwargs(create_fn, describe_fn))
        assert outcome.create_confirmed is True

    def test_progress_fn_is_never_called_when_not_waiting(self):
        create_fn = _FakeCreateFn()
        describe_fn = _ScriptedDescribeFn([{"clusterStatus": "CREATE_IN_PROGRESS"}])
        seen = []
        run_cluster_create_and_classify(
            **self._kwargs(create_fn, describe_fn, wait=False,
                           progress_fn=lambda *a: seen.append(a))
        )
        assert seen == []
