"""
Direct tests for make_pcluster.py's main() / core_create_cluster().

main() used to be ~1900 lines with no direct coverage: the pure validators it
called were tested individually, but nothing checked the sequencing that
makes a failed build recoverable. The specific invariants pinned here are the
ones a refactor would silently break — that IAM is cleaned up when the build
fails so a retry is possible, that the abort window opens before the cluster
is actually launched, that an existing vars file blocks a rebuild before any
AWS call is made, and that derived variables (pcluster_os, enable_efa_gdr)
reach the rendered vars file correctly.

Everything past argument parsing is stubbed at the AWS and subprocess
boundaries; the point is the control flow, not the API payloads.

Since the core/shim split, the bulk of main()'s old body lives in
pcluster_core.core_create_cluster -- so the bespoke-name monkeypatches below
(_validate_network, _load_or_create_serial, _setup_iam, _get_od_price,
_get_spot_price, _delete_managed_policies, _cleanup_iam_on_failure) patch the
pcluster_core module directly, not make_pcluster's own copy of those names:
core_create_cluster resolves them as bare names in pcluster_core's own
globals, and make_pcluster.py's `from pcluster_core import _setup_iam` (etc.)
is a separate binding a patch on `mp` would never reach. ctrlC_Abort is a
further wrinkle: core_create_cluster imports it locally
(`from pcluster_aux_data import ctrlC_Abort`) inside its own body, so it is
re-resolved from pcluster_aux_data's namespace on every call -- patch
pcluster_aux_data.ctrlC_Abort, not pcluster_core.ctrlC_Abort (which does not
exist as a persistent binding at all). boto3.client/boto3.resource and
subprocess.run are the one class of patch that needed no change: those are
shared, process-wide module objects, so patching mp.boto3/mp.subprocess
still reaches every call site regardless of which file it lives in.

Since Workstream 3's create-side wiring (round 25), core_create_cluster no
longer shells out to ansible-playbook at all: `import pcluster.lib as pc`
happens at call time inside core_create_cluster, exactly like the delete
side (see test_kill_pcluster.py's own identical note), so the fake here is a
types.ModuleType installed into sys.modules under "pcluster.lib" rather than
a monkeypatched attribute. The granular per-function unit coverage for every
piece core_create_cluster's tail now calls (provision_s3_keypair_and_secret,
render_and_upload_cluster_config_and_scripts, run_cluster_create_and_classify,
deploy_staging_and_performance_tree_to_head_node, etc.) already lives in
test_create_pcluster_migration.py; this file only checks the
integration-level properties that make a build safe end to end.
"""

import io
import os
import stat
import sys
import types
from datetime import datetime as DateTime, timezone

import pytest
import yaml
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from entrypoint_harness import REPO_ROOT, load_entrypoint

sys.path.insert(0, os.path.join(REPO_ROOT, "src"))
import pcluster_core
import pcluster_aux_data

CLUSTER = "buildme"
OWNER = "testuser"
AZ = "us-east-1a"
SERIAL = "buildme-00001220260720"
DATESTAMP = "00001220260720"
_FAKE_PEM = "-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n-----END OPENSSH PRIVATE KEY-----\n"


def _arch_for(instance_type):
    """Graviton families end their family name in 'g'; everything else is x86."""
    family = instance_type.split(".")[0]
    return "arm64" if family.endswith("g") or family.endswith("gd") else "x86_64"


class _FakeEc2:
    def __init__(self):
        self.created_keypairs = []
        self.deleted_keypairs = []
        self.created_sgs = []
        self.deleted_sgs = []
        self.authorized = None

    def describe_availability_zones(self, ZoneNames=None, **kw):
        return {"AvailabilityZones": [{"RegionName": "us-east-1"}]}

    def describe_instance_types(self, InstanceTypes=None, **kw):
        return {
            "InstanceTypes": [
                {"InstanceType": t,
                 "ProcessorInfo": {"SupportedArchitectures": [_arch_for(t)]}}
                for t in (InstanceTypes or [])
            ]
        }

    def describe_images(self, **kw):
        return {"Images": [{"ImageId": "ami-0abc"}]}

    def describe_spot_price_history(self, InstanceTypes=None, **kw):
        return {
            "SpotPriceHistory": [
                {"InstanceType": t, "SpotPrice": "0.05",
                 "Timestamp": "2026-07-20T00:00:00Z"}
                for t in (InstanceTypes or [])
            ]
        }

    # -- provision_s3_keypair_and_secret's EC2 surface --

    def create_key_pair(self, KeyName, KeyType):
        self.created_keypairs.append(KeyName)
        return {"KeyMaterial": _FAKE_PEM}

    def delete_key_pair(self, KeyName):
        self.deleted_keypairs.append(KeyName)

    def create_security_group(self, GroupName, Description, VpcId):
        self.created_sgs.append(GroupName)
        return {"GroupId": "sg-created0001"}

    def authorize_security_group_ingress(self, GroupId, IpPermissions):
        self.authorized = (GroupId, IpPermissions)

    def describe_security_groups(self, Filters):
        return {"SecurityGroups": [{"GroupId": "sg-existing0001"}]}

    def delete_security_group(self, GroupId):
        self.deleted_sgs.append(GroupId)


class _FakeSts:
    def get_caller_identity(self):
        return {"Account": "123456789012"}


class _FakeIam:
    def __init__(self):
        self.deleted_roles = []

    def delete_role(self, RoleName):
        self.deleted_roles.append(RoleName)


class _FakeS3Object:
    def put(self, **kw):
        return {}


class _FakeS3Resource:
    def Object(self, bucket, key):
        return _FakeS3Object()


class _FakeS3Client:
    """head_bucket must 404 by default: a bucket that already exists aborts a
    fresh build. Also covers provision_s3_keypair_and_secret's and the final
    slice's upload surface (create_bucket/put_bucket_tagging/upload_file/
    put_public_access_block)."""

    def __init__(self, bucket_exists=False):
        self.bucket_exists = bucket_exists
        self.created_buckets = []
        self.tags = {}
        self.uploaded = []
        self._objects = {}  # Workstream 4's S3 distributed lock: key -> {"body","etag","last_modified"}
        self._etag_counter = 0

    def head_bucket(self, Bucket=None, **kw):
        if self.bucket_exists:
            return {}
        raise ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadBucket"
        )

    def create_bucket(self, **kwargs):
        self.created_buckets.append(kwargs)

    def put_bucket_tagging(self, Bucket, Tagging):
        self.tags[Bucket] = Tagging

    def put_public_access_block(self, **kw):
        pass

    def _new_etag(self):
        self._etag_counter += 1
        return f"etag-{self._etag_counter}"

    def put_object(self, Bucket, Key, Body, IfNoneMatch=None, IfMatch=None):
        """Workstream 4's S3 distributed lock uses conditional PutObject --
        modeled the same way tests/test_s3_cluster_lock.py's own
        _FakeS3Lock models it."""
        existing = self._objects.get(Key)
        if IfNoneMatch == "*" and existing is not None:
            raise ClientError(
                {"Error": {"Code": "PreconditionFailed", "Message": ""},
                 "ResponseMetadata": {"HTTPStatusCode": 412}}, "PutObject",
            )
        if IfMatch is not None and (existing is None or existing["etag"] != IfMatch):
            raise ClientError(
                {"Error": {"Code": "PreconditionFailed", "Message": ""},
                 "ResponseMetadata": {"HTTPStatusCode": 412}}, "PutObject",
            )
        etag = self._new_etag()
        self._objects[Key] = {"body": Body, "etag": etag, "last_modified": DateTime.now(timezone.utc)}
        return {"ETag": etag}

    def get_object(self, Bucket, Key):
        obj = self._objects[Key]
        return {"ETag": obj["etag"], "LastModified": obj["last_modified"], "Body": io.BytesIO(obj["body"])}

    def delete_object(self, Bucket, Key):
        self._objects.pop(Key, None)

    def upload_file(self, src, bucket, key, ExtraArgs=None):
        self.uploaded.append({"src": src, "bucket": bucket, "key": key})


class _FakeSecretsManager:
    def __init__(self):
        self.created_secrets = []

    def create_secret(self, Name, Description, SecretString, Tags):
        self.created_secrets.append(Name)

    def delete_secret(self, SecretId, ForceDeleteWithoutRecovery):
        pass


class _FakeSns:
    def __init__(self):
        self.topics = {}
        self.published = []

    def create_topic(self, Name):
        arn = f"arn:aws:sns:us-east-1:123456789012:{Name}"
        self.topics[Name] = arn
        return {"TopicArn": arn}

    def set_topic_attributes(self, TopicArn, AttributeName, AttributeValue):
        pass

    def subscribe(self, TopicArn, Protocol, Endpoint):
        pass

    def publish(self, TopicArn, Message, Subject):
        self.published.append({"TopicArn": TopicArn, "Subject": Subject})

    def delete_topic(self, TopicArn):
        pass


class _FakePcLibCreate(types.ModuleType):
    """Stand-in for `import pcluster.lib as pc`, installed into
    sys.modules -- the same pattern test_kill_pcluster.py uses on the
    delete side. describe_response is what every describe_cluster() call
    returns (a create build's own _wait_for_cluster_create only ever needs
    one snapshot: CREATE_COMPLETE resolves on the very first poll, so no
    test here needs the retry/backoff behavior already pinned in
    test_create_pcluster_migration.py's TestWaitForClusterCreate)."""

    def __init__(self, describe_response=None, create_raises=None):
        super().__init__("pcluster.lib")
        self.describe_response = describe_response or {
            "clusterStatus": "CREATE_COMPLETE",
            "headNode": {"publicIpAddress": "203.0.113.5"},
        }
        self.create_raises = create_raises
        self.create_calls = []

    def create_cluster(self, cluster_name, cluster_configuration, region, rollback_on_failure):
        self.create_calls.append(
            {"cluster_name": cluster_name, "cluster_configuration": cluster_configuration,
             "region": region, "rollback_on_failure": rollback_on_failure}
        )
        if self.create_raises:
            raise self.create_raises

    def describe_cluster(self, cluster_name, region):
        return self.describe_response


class _FakeSocketCtx:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def mp():
    return load_entrypoint("make_pcluster.py")


@pytest.fixture
def staged(mp, tmp_path, monkeypatch):
    """make_pcluster.main() with every AWS, pcluster.lib, and subprocess
    boundary stubbed."""
    src = tmp_path / "src"
    (src / "vars_files").mkdir(parents=True)
    (tmp_path / "active_clusters").mkdir()
    # The real templates: main() renders vars_file.j2 (and, since round 25,
    # config.pcluster.j2 too) under StrictUndefined, so these runs also prove
    # every cluster_parameters key every template needs is actually present.
    (tmp_path / "templates").symlink_to(os.path.join(REPO_ROOT, "templates"))
    # scripts/ itself must be a real directory, not a symlink: make_pcluster.py's
    # own pre_install_script/post_install_script escape check resolves symlinks
    # with os.path.realpath, and a symlinked scripts/ makes the *default*
    # "scripts/pre-deployment.sh" value realpath out of tmp_path and into the
    # real repo -- exactly the escape that check exists to catch. Symlink only
    # the individual files core_create_cluster's own render/upload steps need:
    # the default sbatch script (rendered by every build), and the default
    # pre/post-deployment hooks render_and_upload_cluster_config_and_scripts
    # now copies into cluster_data_dir and uploads (round 25's final slice).
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "sbatch_default_submission_script.sh").symlink_to(
        os.path.join(REPO_ROOT, "scripts", "sbatch_default_submission_script.sh")
    )
    # pre-deployment.sh/post-deployment.sh must be real files, not symlinks:
    # make_pcluster.py's pre_install_script/post_install_script escape check
    # calls os.path.realpath on the joined path, which follows a symlink to
    # its real-repo target -- outside tmp_path -- and aborts the build before
    # core_create_cluster is ever reached.
    for _f in ("pre-deployment.sh", "post-deployment.sh"):
        (tmp_path / "scripts" / _f).write_text("#!/bin/bash\nexit 0\n")
    (tmp_path / "hpc-benchmark").symlink_to(os.path.join(REPO_ROOT, "hpc-benchmark"))
    monkeypatch.setattr(mp, "_repo_root", str(tmp_path))
    monkeypatch.setattr(mp, "_src_dir", str(src))

    # ssh_known_hosts is derived inside core_create_cluster as
    # os.path.expanduser("~")/.ssh/known_hosts -- redirect HOME so the SSH
    # orchestration slice's real (non-subprocess) file writes
    # (_ensure_local_ssh_dir, _accept_ssh_fingerprint) land in tmp_path
    # rather than touching the developer's actual ~/.ssh/known_hosts.
    monkeypatch.setenv("HOME", str(tmp_path))

    iam = _FakeIam()
    secretsmanager = _FakeSecretsManager()
    sns = _FakeSns()
    s3_client = _FakeS3Client()
    clients = {"ec2": _FakeEc2(), "sts": _FakeSts(), "iam": iam,
               "s3": s3_client, "pricing": object(),
               "secretsmanager": secretsmanager, "sns": sns}
    monkeypatch.setattr(mp.boto3, "client",
                        lambda name, **kw: clients.get(name, object()))
    monkeypatch.setattr(mp.boto3, "resource", lambda *a, **k: _FakeS3Resource())

    pc_lib = _FakePcLibCreate()
    monkeypatch.setitem(sys.modules, "pcluster.lib", pc_lib)

    record = {"calls": []}

    def _run(cmd, *args, **kwargs):
        record["calls"].append(list(cmd))
        if "--version" in cmd:
            return _Proc(0, "ansible [core 2.16.0]\n")
        if "describe-cluster" in cmd:
            # rc != 0 means "cluster does not exist yet", which is what a
            # fresh build requires. This is the *pcluster CLI* preflight
            # existence check core_create_cluster still runs via subprocess
            # (unchanged, unrelated to pcluster.lib) -- not the create/wait
            # path, which goes through pc_lib above.
            return _Proc(record.get("describe_rc", 1))
        if "ssh-keyscan" in cmd:
            return _Proc(0, "203.0.113.5 ssh-ed25519 AAAAfakekey\n")
        return _Proc(0)

    monkeypatch.setattr(mp.subprocess, "run", _run)
    # _wait_for_ssh_port's real socket.create_connection would otherwise try
    # an actual TCP connection to the fake head node IP.
    monkeypatch.setattr(pcluster_core.socket, "create_connection",
                        lambda addr, timeout=None: _FakeSocketCtx())
    # _wait_for_ssh_port unconditionally sleeps `delay` seconds before its
    # first probe; no test here needs real wall-clock timing.
    monkeypatch.setattr(pcluster_core.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        pcluster_core, "_validate_network",
        lambda *a, **k: ("vpc-0abc", "subnet-0head", ["subnet-0cpu"],
                         ["subnet-0gpu"], "10.0.0.0/16", "subnet-0login"),
    )
    monkeypatch.setattr(
        pcluster_core, "_load_or_create_serial",
        lambda data_dir, name: (
            os.path.join(str(tmp_path), "active_clusters", name,
                         name + ".serial"),
            SERIAL, DATESTAMP, True,
        ),
    )
    monkeypatch.setattr(pcluster_core, "_setup_iam", lambda *a, **k: None)
    monkeypatch.setattr(pcluster_core, "_get_od_price", lambda *a, **k: 0.34)
    monkeypatch.setattr(pcluster_core, "_get_spot_price", lambda *a, **k: 0.11)

    deleted = []
    monkeypatch.setattr(
        pcluster_core, "_delete_managed_policies",
        lambda *a, **k: deleted.append({"args": a, "kwargs": k}),
    )
    monkeypatch.setattr(pcluster_core, "_cleanup_iam_on_failure",
                        lambda *a, **k: deleted.append({"cleanup": True}))

    def _abort(timer, line_length, *args, **kwargs):
        record.setdefault("abort", {
            "timer": timer,
            "args": args,
            "kwargs": kwargs,
            "cluster_created": bool(pc_lib.create_calls),
        })

    monkeypatch.setattr(pcluster_aux_data, "ctrlC_Abort", _abort)

    serial_dir = tmp_path / "active_clusters" / CLUSTER
    serial_dir.mkdir(parents=True, exist_ok=True)
    (serial_dir / f"{CLUSTER}.serial").write_text(SERIAL + "\n")

    return {"mod": mp, "record": record, "iam": iam, "deleted": deleted,
            "clients": clients, "root": tmp_path, "src": src, "pc_lib": pc_lib,
            "s3_client": s3_client}


class _Proc:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def _argv(*extra):
    argv = ["make_pcluster.py", "-N", CLUSTER, "-O", OWNER, "-A", AZ,
            "-E", OWNER + "@example.com"]
    for flag, value in (("--headnode_instance_type", "c8g.2xlarge"),
                        ("--compute_instance_type", "c8g.4xlarge"),
                        ("--base_os", "ubuntu2404arm")):
        if flag not in extra:
            argv += [flag, value]
    return argv + list(extra)


def _run_main(staged, monkeypatch, *extra):
    monkeypatch.setattr(sys, "argv", _argv(*extra))
    return staged["mod"].main()


def _vars_file_path(staged):
    return staged["src"] / "vars_files" / f"{CLUSTER}.yml"


def _vars_file_written(staged):
    """Whether core_create_cluster ever got as far as rendering vars_file.j2
    -- the modern equivalent of the pre-wiring test suite's "was
    ansible-playbook ever invoked with --extra-vars" check. Every early
    preflight abort in this file happens strictly before that render, so
    this draws the identical line the old check drew."""
    return _vars_file_path(staged).exists()


def _swap_ec2(staged, monkeypatch, ec2):
    """Replace only the ec2 client; every other client stays as staged."""
    clients = dict(staged["clients"], ec2=ec2)
    monkeypatch.setattr(staged["mod"].boto3, "client",
                        lambda name, **kw: clients.get(name, object()))


def _rendered_vars(staged):
    """The vars file core_create_cluster writes and every later render
    (preinstall/postinstall, config.pcluster.j2, ...) reads from. Rendered
    with StrictUndefined, so a missing cluster_parameters key fails the
    build rather than the assert."""
    with open(_vars_file_path(staged)) as fh:
        return yaml.safe_load(fh)


class TestBuildPreflight:
    def test_existing_vars_file_blocks_a_rebuild(self, staged, monkeypatch):
        """A vars file means the cluster is already tracked; rebuilding over it
        orphans the old stack's IAM and serial state."""
        _vars_file_path(staged).write_text("x: 1\n")
        with pytest.raises(SystemExit) as exc:
            _run_main(staged, monkeypatch)
        assert exc.value.code == 1

    def test_existing_cluster_stack_blocks_the_build(self, staged, monkeypatch):
        staged["record"]["describe_rc"] = 0
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch)
        assert not _vars_file_written(staged)

    def test_unknown_az_aborts_before_any_playbook(self, staged, monkeypatch):
        class _NoAz(_FakeEc2):
            def describe_availability_zones(self, ZoneNames=None, **kw):
                return {"AvailabilityZones": []}

        _swap_ec2(staged, monkeypatch, _NoAz())
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch)
        assert not _vars_file_written(staged)

    def test_mixed_cpu_architectures_are_rejected(self, staged, monkeypatch, capsys):
        """A Graviton head node with x86 compute nodes builds a cluster whose
        compute fleet cannot run the head node's binaries. This branch only
        fires for a family absent from the hardcoded ARM_FAMILIES prefix list,
        which is exactly why describe_instance_types is consulted — so the fake
        reports c6i as arm64, standing in for a future Graviton family."""
        class _SurpriseArm(_FakeEc2):
            def describe_instance_types(self, InstanceTypes=None, **kw):
                return {
                    "InstanceTypes": [
                        {"InstanceType": t,
                         "ProcessorInfo": {"SupportedArchitectures": [
                             "arm64" if t.startswith("c6i") else _arch_for(t)]}}
                        for t in (InstanceTypes or [])
                    ]
                }

        _swap_ec2(staged, monkeypatch, _SurpriseArm())
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch, "--base_os", "ubuntu2404",
                      "--headnode_instance_type", "c5.xlarge",
                      "--compute_instance_type", "c6i.2xlarge")
        assert "Mixed architectures detected" in capsys.readouterr().out
        assert not _vars_file_written(staged)

    def test_base_os_architecture_must_match_the_instances(self, staged, monkeypatch, capsys):
        """An x86 base OS on Graviton hardware produces a cluster that cannot
        boot at all."""
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch, "--base_os", "ubuntu2404",
                      "--headnode_instance_type", "c8g.2xlarge",
                      "--compute_instance_type", "c8g.4xlarge")
        out = capsys.readouterr().out
        assert "is ARM/Graviton but base_os=ubuntu2404 is x86_64" in out
        assert not _vars_file_written(staged)

    def test_api_arch_overrides_the_hardcoded_prefix_list(self, staged, monkeypatch, capsys):
        """describe_instance_types is authoritative: a uniformly-arm64 fleet on
        an x86 base OS must be rejected even when no instance family matches the
        ARM_FAMILIES prefixes that base_os_instance_check knows about."""
        class _AllArm(_FakeEc2):
            def describe_instance_types(self, InstanceTypes=None, **kw):
                return {
                    "InstanceTypes": [
                        {"InstanceType": t,
                         "ProcessorInfo": {"SupportedArchitectures": ["arm64"]}}
                        for t in (InstanceTypes or [])
                    ]
                }

        _swap_ec2(staged, monkeypatch, _AllArm())
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch, "--base_os", "ubuntu2404",
                      "--headnode_instance_type", "c5.xlarge",
                      "--compute_instance_type", "c5.2xlarge")
        assert "base_os=ubuntu2404 is x86_64" in capsys.readouterr().out
        assert not _vars_file_written(staged)

    def test_loginnode_instance_type_must_exist(self, staged, monkeypatch, capsys):
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch, "--enable_loginnode", "true",
                      "--loginnode_instance_type", "not-a-real-instance-type")
        out = capsys.readouterr().out
        assert "not-a-real-instance-type" in out
        assert not _vars_file_written(staged)

    def test_loginnode_architecture_must_match_base_os(self, staged, monkeypatch, capsys):
        """A Graviton login node on an x86 cluster cannot boot; this must fail
        at preflight, not 15+ minutes into a real build."""
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch, "--base_os", "ubuntu2404",
                      "--headnode_instance_type", "c5.xlarge",
                      "--compute_instance_type", "c5.2xlarge",
                      "--enable_loginnode", "true",
                      "--loginnode_instance_type", "c8g.xlarge")
        out = capsys.readouterr().out
        assert "is ARM/Graviton but base_os=ubuntu2404 is x86_64" in out
        assert not _vars_file_written(staged)

    def test_loginnode_instance_type_falls_back_by_architecture_on_x86(self, staged, monkeypatch):
        """Regression: a flat c8g.xlarge (Graviton) fallback would silently
        fail preflight for an operator who opts into --enable_loginnode=true
        on an x86_64 cluster without also setting --loginnode_instance_type
        and without --use_defaults. The architecture-aware fallback in
        _default_loginnode_instance_type must resolve to c5.xlarge here
        instead, so preflight succeeds."""
        with pytest.raises(SystemExit) as exc:
            _run_main(staged, monkeypatch, "--base_os", "ubuntu2404",
                      "--headnode_instance_type", "c5.xlarge",
                      "--compute_instance_type", "c5.2xlarge",
                      "--enable_loginnode", "true")
        assert exc.value.code == 0
        v = _rendered_vars(staged)
        assert v["loginnode_instance_type"] == "c5.xlarge"

    def test_loginnode_count_cannot_be_negative(self, staged, monkeypatch, capsys):
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch, "--enable_loginnode", "true",
                      "--loginnode_count", "-1")
        assert "loginnode_count must be >= 0" in capsys.readouterr().out
        assert not _vars_file_written(staged)

    def test_loginnode_count_zero_is_accepted(self, staged, monkeypatch):
        """AWS's own LoginNodesPoolSchema.count floors at 0 — a defined-but-empty
        pool is valid, the same shape as a compute queue scaled to zero."""
        with pytest.raises(SystemExit) as exc:
            _run_main(staged, monkeypatch, "--enable_loginnode", "true",
                      "--loginnode_count", "0")
        assert exc.value.code == 0
        v = _rendered_vars(staged)
        assert v["loginnode_count"] == 0

    def test_loginnode_count_validation_skipped_when_disabled(self, staged, monkeypatch):
        """A negative loginnode_count must not fail preflight when the feature
        is off entirely — the validation is gated on enable_loginnode, not on
        the count alone."""
        with pytest.raises(SystemExit) as exc:
            _run_main(staged, monkeypatch, "--loginnode_count", "-1")
        assert exc.value.code == 0


class TestExistingS3Bucket:
    """The bucket name is derived from the serial, so a bucket that already
    exists on a fresh build means the name collides with another cluster's
    storage — continuing would write this cluster's scripts into it."""

    def test_existing_bucket_aborts_a_fresh_build(self, staged, monkeypatch, capsys):
        monkeypatch.setitem(staged["clients"], "s3", _FakeS3Client(bucket_exists=True))
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch)
        assert "existing S3 bucket" in capsys.readouterr().out
        assert not _vars_file_written(staged)

    def test_existing_bucket_is_reused_when_resuming(self, staged, monkeypatch, capsys):
        """A serial file that already existed means an earlier run was
        interrupted after creating the bucket; that build must be resumable."""
        monkeypatch.setitem(staged["clients"], "s3", _FakeS3Client(bucket_exists=True))
        monkeypatch.setattr(
            pcluster_core, "_load_or_create_serial",
            lambda data_dir, name: (
                os.path.join(str(staged["root"]), "active_clusters", name,
                             name + ".serial"),
                SERIAL, DATESTAMP, False,
            ),
        )
        with pytest.raises(SystemExit) as exc:
            _run_main(staged, monkeypatch)
        assert "interrupted run" in capsys.readouterr().out
        assert exc.value.code == 0
        assert _vars_file_written(staged)


class TestLocalStateDirMode:
    """Workstream 3's create-side migration, first slice: create_pcluster.yml's
    "Create a local state directory for this cluster" task chmods explicitly
    after creation, bypassing umask; core_create_cluster's own os.makedirs
    alone does not fix an already-existing directory's mode at all --
    os.makedirs(path, exist_ok=True) is a no-op on mode when the directory
    already exists, which the staged fixture's own setup guarantees it does
    (it creates active_clusters/<cluster>/ itself, to write the serial file,
    before main() ever runs). Pre-setting a wrong mode here is what actually
    exercises core_create_cluster's own fix, rather than a restrictive-umask
    approach that -- verified while writing this test -- never reaches the
    directory-creation branch at all, since the directory already exists by
    the time core_create_cluster's own os.makedirs runs."""

    def test_cluster_data_dir_is_corrected_to_0755(self, staged, monkeypatch):
        cluster_data_dir = staged["root"] / "active_clusters" / CLUSTER
        os.chmod(cluster_data_dir, 0o700)
        with pytest.raises(SystemExit) as exc:
            _run_main(staged, monkeypatch)
        assert exc.value.code == 0
        mode = stat.S_IMODE(cluster_data_dir.stat().st_mode)
        assert mode == 0o755, f"expected 0o755, got {oct(mode)}"


class TestBuildAbortWindow:
    def test_abort_window_opens_before_cluster_launch(self, staged, monkeypatch):
        """Ctrl-C in the window is supposed to cancel the build and clean up
        IAM. If the window opened after pc.create_cluster, the CloudFormation
        stack would already exist by the time the operator could abort."""
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch)
        assert staged["record"]["abort"]["cluster_created"] is False

    def test_debug_mode_lengthens_the_abort_window(self, staged, monkeypatch):
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch, "-D", "true")
        assert staged["record"]["abort"]["timer"] == 15

    def test_default_abort_window_is_five_seconds(self, staged, monkeypatch):
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch)
        assert staged["record"]["abort"]["timer"] == 5

    def test_abort_window_gets_the_real_cleanup_targets(self, staged, monkeypatch):
        """Unlike kill_pcluster.py, which passes None because nothing exists yet,
        a build has already created the IAM role, the serial file, and the vars
        file by this point — Ctrl-C has to be able to remove all three or the
        next build collides on the same serial-derived names."""
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch)
        abort = staged["record"]["abort"]
        vars_file_path, serial_file, serial = abort["args"][0:3]
        assert vars_file_path.endswith(f"vars_files/{CLUSTER}.yml")
        assert serial_file.endswith(f"{CLUSTER}.serial")
        assert serial == SERIAL
        assert abort["kwargs"]["aws_account_id"] == "123456789012"


class TestBuildFailureCleansUpIam:
    """A failed cluster launch leaves the IAM role and policies behind. The
    next build derives the same names from the same serial, so CreatePolicy
    fails with EntityAlreadyExists and the retry is blocked until an
    operator deletes them by hand."""

    def _fail_the_launch(self, staged):
        staged["pc_lib"].describe_response = {
            "clusterStatus": "CREATE_FAILED",
            "failures": [{"failureCode": "InsufficientCapacity", "failureReason": "no capacity"}],
        }

    def test_failed_launch_deletes_the_managed_policies(self, staged, monkeypatch):
        self._fail_the_launch(staged)
        with pytest.raises(SystemExit) as exc:
            _run_main(staged, monkeypatch)
        assert exc.value.code == 1
        assert any("args" in d for d in staged["deleted"])

    def test_failed_launch_deletes_the_iam_role(self, staged, monkeypatch):
        self._fail_the_launch(staged)
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch)
        assert staged["iam"].deleted_roles == ["pclustermaker-role-" + SERIAL]

    def test_failure_exit_code_is_one(self, staged, monkeypatch):
        self._fail_the_launch(staged)
        with pytest.raises(SystemExit) as exc:
            _run_main(staged, monkeypatch)
        assert exc.value.code == 1

    def test_failure_tells_the_operator_how_to_tear_down(self, staged, monkeypatch, capsys):
        self._fail_the_launch(staged)
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch)
        out = capsys.readouterr().out
        assert "kill_pcluster.py" in out
        assert f"-N {CLUSTER}" in out

    def test_a_raised_launch_exception_is_treated_as_a_failed_launch(self, staged, monkeypatch, capsys):
        """pc.create_cluster itself can raise (AccessDenied, ValidationError,
        ...) rather than merely fail to confirm -- that must reach the same
        cleanup path, not propagate as an unhandled traceback."""
        staged["pc_lib"].create_raises = RuntimeError("AccessDenied")
        with pytest.raises(SystemExit) as exc:
            _run_main(staged, monkeypatch)
        assert exc.value.code == 1
        assert "kill_pcluster.py" in capsys.readouterr().out

    def test_iam_setup_failure_triggers_cleanup_and_aborts(self, staged, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("AccessDenied")

        monkeypatch.setattr(pcluster_core, "_setup_iam", _boom)
        with pytest.raises(SystemExit) as exc:
            _run_main(staged, monkeypatch)
        assert exc.value.code == 1
        assert any(d.get("cleanup") for d in staged["deleted"])
        assert not _vars_file_written(staged)


class TestClusterLockDuringBuild:
    """The build lock must be released on every exit path a real build can
    take -- a stuck lock after a normal failure would block every retry,
    not just a concurrent second process. Since Workstream 4's first slice,
    core_create_cluster locks via the S3-backed distributed lock
    (pcluster_core.s3_acquire_cluster_lock), not the old local mkdir lock;
    staged["clients"]["s3"] is the shared fake every boto3.client("s3", ...)
    call in the build reuses, so it is both where the lock lands and the
    handle these tests use to inspect or pre-populate it."""

    def _locks_bucket(self):
        return pcluster_core._derive_locks_bucket(aws_account_id="123456789012", region="us-east-1")

    def test_lock_is_released_after_a_successful_build(self, staged, monkeypatch):
        with pytest.raises(SystemExit) as exc:
            _run_main(staged, monkeypatch)
        assert exc.value.code == 0
        assert pcluster_core._lock_key(CLUSTER) not in staged["clients"]["s3"]._objects

    def test_lock_is_released_after_a_failed_launch(self, staged, monkeypatch):
        staged["pc_lib"].describe_response = {"clusterStatus": "CREATE_FAILED", "failures": []}
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch)
        assert pcluster_core._lock_key(CLUSTER) not in staged["clients"]["s3"]._objects

    def test_lock_is_released_after_iam_setup_fails(self, staged, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("AccessDenied")

        monkeypatch.setattr(pcluster_core, "_setup_iam", _boom)
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch)
        assert pcluster_core._lock_key(CLUSTER) not in staged["clients"]["s3"]._objects

    def test_a_lock_already_held_aborts_before_any_aws_mutation(self, staged, monkeypatch):
        """Simulates the actual osiris scenario from the other direction: a
        build already in progress, and a second invocation (this one) must
        fail fast rather than touch IAM at all."""
        pcluster_core.s3_acquire_cluster_lock(
            staged["clients"]["s3"], locks_bucketname=self._locks_bucket(),
            cluster_name=CLUSTER, command="make_pcluster.py -N buildme (other process)",
        )
        with pytest.raises(SystemExit) as exc:
            _run_main(staged, monkeypatch)
        assert "already running" in str(exc.value.code)
        assert not staged["deleted"], "no IAM mutation should have been attempted"
        assert not _vars_file_written(staged)


class TestDerivedVariablesReachTheVarsFile:
    def test_pcluster_os_strips_the_arm_suffix(self, staged, monkeypatch):
        """PCluster's Os: field rejects the arm suffix, so pcluster_os is
        base_os minus 'arm'. Passing base_os through unchanged fails at
        cluster-create time with a schema error."""
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch, "--base_os", "ubuntu2404arm")
        v = _rendered_vars(staged)
        assert v["base_os"] == "ubuntu2404arm"
        assert v["pcluster_os"] == "ubuntu2404"

    def test_non_arm_os_is_unchanged(self, staged, monkeypatch):
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch, "--base_os", "ubuntu2404",
                      "--headnode_instance_type", "c5.xlarge",
                      "--compute_instance_type", "c5.xlarge")
        v = _rendered_vars(staged)
        assert v["pcluster_os"] == v["base_os"] == "ubuntu2404"

    def test_iam_names_are_derived_from_the_serial_number(self, staged, monkeypatch):
        """kill_pcluster.py rebuilds these names from the serial alone; a
        different derivation here orphans them."""
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch)
        v = _rendered_vars(staged)
        assert v["ec2_iam_role"] == "pclustermaker-role-" + SERIAL
        assert v["ec2_iam_policy"] == "pclustermaker-policy-" + SERIAL

    def test_efa_gdr_stays_off_for_non_gdr_instances(self, staged, monkeypatch):
        """GdrSupport is rejected at cluster-create time by any instance family
        that does not support it, so this must not be set unconditionally."""
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch, "--enable_efa", "true",
                      "--headnode_instance_type", "c8g.2xlarge",
                      "--compute_instance_type", "hpc7g.16xlarge")
        v = _rendered_vars(staged)
        assert v["enable_efa"] == "true"
        assert v["enable_efa_gdr"] == "false"

    def test_serial_number_reaches_the_vars_file(self, staged, monkeypatch):
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch)
        assert _rendered_vars(staged)["cluster_serial_number"] == SERIAL

    def test_loginnode_instance_type_reaches_the_vars_file(self, staged, monkeypatch):
        with pytest.raises(SystemExit) as exc:
            _run_main(staged, monkeypatch, "--enable_loginnode", "true",
                      "--loginnode_instance_type", "c8g.2xlarge",
                      "--loginnode_count", "2")
        assert exc.value.code == 0
        v = _rendered_vars(staged)
        assert v["enable_loginnode"] == "true"
        assert v["loginnode_instance_type"] == "c8g.2xlarge"
        assert v["loginnode_count"] == 2

    def test_cluster_configuration_handed_to_pcluster_lib_is_the_rendered_config(
        self, staged, monkeypatch
    ):
        """pc.create_cluster's cluster_configuration argument must be the
        rendered config.pcluster.j2 file path, not the vars file -- passing
        the wrong path either fails FileNotFoundError or (worse) silently
        creates a cluster from stale/wrong config."""
        with pytest.raises(SystemExit) as exc:
            _run_main(staged, monkeypatch)
        assert exc.value.code == 0
        [call] = staged["pc_lib"].create_calls
        assert call["cluster_name"] == CLUSTER
        assert call["cluster_configuration"].endswith(f"config.{CLUSTER}")
        assert os.path.isfile(call["cluster_configuration"])
        assert call["rollback_on_failure"] is False


_GPU_COUNTS = {
    "p3.2xlarge": ("NVIDIA", 1),
    "p4d.24xlarge": ("NVIDIA", 8),
    "g5.xlarge": ("NVIDIA", 1),
    "g4ad.4xlarge": ("AMD", 1),
}


class _GpuAwareEc2(_FakeEc2):
    """describe_instance_types with the GpuInfo shape the real API returns."""

    def describe_instance_types(self, InstanceTypes=None, **kw):
        out = []
        for t in InstanceTypes or []:
            entry = {"InstanceType": t,
                     "ProcessorInfo": {"SupportedArchitectures": [_arch_for(t)]}}
            if t in _GPU_COUNTS:
                mfr, count = _GPU_COUNTS[t]
                entry["GpuInfo"] = {"Gpus": [{"Manufacturer": mfr, "Count": count}]}
            out.append(entry)
        return {"InstanceTypes": out}


class TestGpuRanksPerNodeReachTheVarsFile:
    """gpu_ranks_per_node shapes --ntasks-per-node in the rendered benchmark job
    script. vars_file.j2 renders under StrictUndefined, so a break in this thread
    fails the cluster build, not just the benchmark."""

    def _run(self, staged, monkeypatch, *extra):
        _swap_ec2(staged, monkeypatch, _GpuAwareEc2())
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch, "--base_os", "ubuntu2404",
                      "--headnode_instance_type", "c5.xlarge", *extra)
        return _rendered_vars(staged)

    def test_a_cpu_only_cluster_reports_zero(self, staged, monkeypatch):
        v = self._run(staged, monkeypatch, "--compute_instance_type", "c5.2xlarge")
        assert v["enable_gpu_queue"] == "false"
        assert v["gpu_ranks_per_node"] == 0

    def test_a_single_gpu_instance_reports_one(self, staged, monkeypatch):
        v = self._run(staged, monkeypatch, "--compute_instance_type", "c5.2xlarge",
                      "--gpu_instance_type", "p3.2xlarge")
        assert v["gpu_ranks_per_node"] == 1

    def test_a_multi_gpu_instance_reports_every_device(self, staged, monkeypatch):
        v = self._run(staged, monkeypatch, "--compute_instance_type", "",
                      "--gpu_instance_type", "p4d.24xlarge")
        assert v["enable_cpu_queue"] == "false"
        assert v["gpu_ranks_per_node"] == 8

    def test_a_mixed_gpu_queue_reports_the_minimum(self, staged, monkeypatch):
        """Only the smallest count is satisfiable by every node in the queue;
        the maximum would oversubscribe the g5 nodes 8:1."""
        v = self._run(staged, monkeypatch, "--compute_instance_type", "",
                      "--gpu_instance_type", "p4d.24xlarge,g5.xlarge")
        assert v["gpu_ranks_per_node"] == 1

    def test_an_amd_gpu_queue_reports_zero(self, staged, monkeypatch):
        """g4ad is Radeon, invisible to CUDA and GRES. The job template falls
        back to a CPU-shaped rank count on this value."""
        v = self._run(staged, monkeypatch, "--compute_instance_type", "",
                      "--gpu_instance_type", "g4ad.4xlarge")
        assert v["enable_gpu_queue"] == "true"
        assert v["gpu_ranks_per_node"] == 0

    def test_no_extra_api_calls_are_made_for_the_gpu_count(self, staged, monkeypatch):
        """The count is read out of the describe_instance_types response the
        architecture check already fetches. A second sweep would double the API
        calls on every build."""
        class _Counting(_GpuAwareEc2):
            calls = 0

            def describe_instance_types(self, InstanceTypes=None, **kw):
                type(self).calls += 1
                return super().describe_instance_types(InstanceTypes=InstanceTypes, **kw)

        ec2 = _Counting()
        _swap_ec2(staged, monkeypatch, ec2)
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch, "--base_os", "ubuntu2404",
                      "--headnode_instance_type", "c5.xlarge",
                      "--compute_instance_type", "c5.2xlarge",
                      "--gpu_instance_type", "p4d.24xlarge")
        assert _Counting.calls == 1, (
            f"describe_instance_types called {_Counting.calls} times; the GPU count "
            "should come from the architecture check's existing response"
        )


class TestIdleComputeNotice:
    """The toolkit schedules no teardown of any kind, so the build summary is the
    only place the operator learns what an idle cluster costs. Removing
    --cluster_lifetime removed a self-termination promise from the summary; this
    replaces it with an accurate statement of what actually bounds the bill."""

    def test_summary_states_the_idle_scaledown_window(self, staged, monkeypatch, capsys):
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch, "--scaledown_idletime", "17")
        out = capsys.readouterr().out
        assert "Idle compute:" in out
        assert "17 minutes" in out, (
            "the summary must quote the cluster's real scaledown_idletime, not a "
            "hardcoded default"
        )
        assert "scales its compute fleet to zero" in out

    def test_summary_warns_when_the_queue_floor_is_pinned_above_zero(
        self, staged, monkeypatch, capsys
    ):
        """maintain_cpu_initial_size pins MinCount above zero, so the fleet never
        scales to zero and those nodes bill continuously. Telling that operator
        their cluster costs nothing when idle would be false."""
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch, "--maintain_cpu_initial_size", "true")
        out = capsys.readouterr().out
        assert "maintain_" in out and "bill continuously" in out
        assert "scales its compute fleet to zero" not in out

    def test_summary_points_at_manual_teardown(self, staged, monkeypatch, capsys):
        """Nothing tears the cluster down on a timer any more; the head node bills
        until the operator acts."""
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch)
        out = capsys.readouterr().out
        assert "head node keeps running and billing" in out
        assert f"./kill_pcluster.py -N {CLUSTER}" in out


class TestStorageReachesTheBuildSummary:
    """A cluster built with --enable_fsx=true printed "Options: FSx/Lustre" and
    nothing else: no /fsx, no size, and the only task naming the hydration
    helpers is skipped when hydration is off. The summary is where the operator
    learns what to cd into, so every filesystem has to name its mount point."""

    def _out(self, staged, monkeypatch, capsys, *extra):
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch, *extra)
        return capsys.readouterr().out

    def test_a_default_cluster_reports_the_shared_ebs_volume(
        self, staged, monkeypatch, capsys
    ):
        out = self._out(staged, monkeypatch, capsys)
        assert "Shared storage:" in out
        assert "/shared" in out
        assert "EBS" in out

    def test_an_fsx_cluster_reports_the_lustre_mount_point(
        self, staged, monkeypatch, capsys
    ):
        """The reported bug, end to end through main()."""
        out = self._out(staged, monkeypatch, capsys, "--enable_fsx", "true",
                        "--fsx_size", "2400")
        assert "/fsx" in out, (
            "the build summary of an FSx cluster still does not mention /fsx"
        )
        assert "FSx for Lustre (2400 GB)" in out

    def test_a_non_fsx_cluster_does_not_mention_lustre(
        self, staged, monkeypatch, capsys
    ):
        out = self._out(staged, monkeypatch, capsys)
        assert "/fsx" not in out
        assert "Lustre" not in out

    def test_an_efs_cluster_reports_the_efs_mount_point(
        self, staged, monkeypatch, capsys
    ):
        out = self._out(staged, monkeypatch, capsys, "--enable_efs", "true")
        assert "/efs" in out

    def test_the_summary_reports_where_spack_installs(
        self, staged, monkeypatch, capsys
    ):
        """pkg_dir follows the storage precedence, so it moves when FSx is added.
        An operator told /shared/pkg on an FSx cluster looks in the wrong place."""
        out = self._out(staged, monkeypatch, capsys)
        assert "install under /shared/pkg" in out

    def test_spack_moves_to_lustre_on_an_fsx_cluster(
        self, staged, monkeypatch, capsys
    ):
        """An operator told /shared/pkg on an FSx cluster looks in the wrong
        place. One main() call per test: the vars file it writes blocks a second
        build of the same cluster name."""
        out = self._out(staged, monkeypatch, capsys, "--enable_fsx", "true")
        assert "install under /fsx/pkg" in out

    def test_the_summary_agrees_with_the_rendered_vars_file(
        self, staged, monkeypatch, capsys
    ):
        """The vars file is what postinstall and config.pcluster.j2 actually
        use. If the summary and the vars file disagree, one of them is lying
        to the operator."""
        out = self._out(staged, monkeypatch, capsys, "--enable_fsx", "true")
        assert f"install under {_rendered_vars(staged)['pkg_dir']}" in out


class TestBuildWaitFalse:
    """Workstream 4: core_create_cluster gained a `wait` parameter
    (default True, so make_pcluster.py's CLI behavior is unchanged --
    it passes no wait at all). wait=False launches the stack and exits
    without waiting, for a future MCP tool wrapper that cannot block for
    the 20-45 minutes a real build takes."""

    def _run(self, staged, monkeypatch, **overrides):
        monkeypatch.setattr(sys, "argv", _argv())
        mp = staged["mod"]
        params = None
        real_core = pcluster_core.core_create_cluster

        def _capture(**kwargs):
            nonlocal params
            params = kwargs
            return real_core(**{**kwargs, **overrides})

        monkeypatch.setattr(mp, "core_create_cluster", _capture)
        with pytest.raises(SystemExit) as exc:
            mp.main()
        return exc.value.code, params

    def test_wait_false_still_launches_the_cluster(self, staged, monkeypatch):
        code, _ = self._run(staged, monkeypatch, wait=False)
        assert code == 0
        assert len(staged["pc_lib"].create_calls) == 1

    def test_wait_false_skips_the_post_launch_pipeline(self, staged, monkeypatch, capsys):
        """Everything after the launch needs a reachable head node -- the
        SSH/SCP staging transfer, the performance tree, the build-summary
        report. None of it can run against a cluster that is still
        building, and the operator has to be told so plainly."""
        self._run(staged, monkeypatch, wait=False)
        out = capsys.readouterr().out
        assert "not waiting" in out
        assert "NOT transferred" in out
        assert "NOT sent" in out

    def test_wait_false_releases_the_build_lock(self, staged, monkeypatch):
        """This process is done with the cluster even though the build is
        not -- holding the lock would block the follow-up run that
        finishes the job."""
        self._run(staged, monkeypatch, wait=False)
        assert pcluster_core._lock_key(CLUSTER) not in staged["clients"]["s3"]._objects

    def test_wait_false_preserves_the_vars_file(self, staged, monkeypatch):
        """The vars file is what the cluster is being built with; a
        follow-up completion step needs it."""
        self._run(staged, monkeypatch, wait=False)
        assert _vars_file_written(staged)

    def test_the_cli_shim_passes_no_wait_so_the_default_governs(self, staged, monkeypatch):
        """make_pcluster.py deliberately grows no --wait flag: the plan's
        design is that the CLI always blocks and only an MCP wrapper opts
        out. Pins both halves -- the shim passes nothing, and the default
        is True."""
        import inspect

        _, params = self._run(staged, monkeypatch)
        assert "wait" not in params, "the CLI shim must not pass wait"
        sig = inspect.signature(pcluster_core.core_create_cluster)
        assert sig.parameters["wait"].default is True


class TestTheCliRefusesAClusterWithNoQueue:
    """Closing the CLI half of the round-46 finding.

    Both instance-type defaults are "" and the queue flags derive from
    them, so `./make_pcluster.py` with neither set builds a config whose
    SlurmQueues is None. PCluster rejects that -- but only after
    core_create_cluster has created the IAM role, S3 bucket, keypair and
    SSH secret, which the late-stage failure handler then preserves,
    leaving the operator to run kill_pcluster.py by hand.

    The MCP path got this guard in round 46; the CLI builds its
    MakeClusterParams directly rather than through
    build_make_cluster_params, so it needed the check wiring separately.
    """

    def test_no_instance_types_aborts(self, staged, monkeypatch):
        with pytest.raises(SystemExit) as exc:
            _run_main(staged, monkeypatch,
                      "--compute_instance_type", "", "--gpu_instance_type", "")
        assert "no compute queue" in str(exc.value.code)

    def test_it_aborts_before_any_aws_mutation(self, staged, monkeypatch):
        """The whole point: the vars file is written well after this check,
        and IAM/S3/keypair later still. Nothing may be spent."""
        with pytest.raises(SystemExit):
            _run_main(staged, monkeypatch,
                      "--compute_instance_type", "", "--gpu_instance_type", "")
        assert not _vars_file_written(staged)
        assert not staged["deleted"], "no IAM mutation should have been attempted"
        assert staged["pc_lib"].create_calls == []

    def test_a_cpu_queue_alone_is_accepted(self, staged, monkeypatch):
        with pytest.raises(SystemExit) as exc:
            _run_main(staged, monkeypatch, "--gpu_instance_type", "")
        assert exc.value.code == 0

    def test_a_gpu_only_cluster_is_accepted(self, staged, monkeypatch):
        """GPU-only is supported; refusing it is the obvious
        over-correction."""
        with pytest.raises(SystemExit) as exc:
            _run_main(staged, monkeypatch,
                      "--compute_instance_type", "",
                      "--gpu_instance_type", "g5.xlarge",
                      "--base_os", "ubuntu2404",
                      "--headnode_instance_type", "c5.xlarge")
        assert exc.value.code == 0

    def test_the_message_tells_the_operator_what_to_set(self):
        """Shared with the MCP path, so the wording is pinned once here
        and the CLI just converts it to a sys.exit."""
        from pcluster_core import PClusterMakerError, _validate_at_least_one_queue

        with pytest.raises(PClusterMakerError) as exc:
            _validate_at_least_one_queue("", "")
        assert "compute_instance_type" in str(exc.value)
