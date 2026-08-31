"""
Unit tests for src/pcluster_core.py — pure utility functions extracted from
make_pcluster.py.  No AWS credentials or venv required.
"""

import ast
import inspect
import os
import re
import subprocess
import sys
import types

from conftest import assert_source_is_real
import pytest
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcluster_core import (
    PClusterMakerError,
    MAKE_CLUSTER_DEFAULTS,
    build_make_cluster_params as _build_make_cluster_params,
    _derive_az_list,
    _b,
    _validate_az_input,
    _validate_cluster_name,
    _validate_cluster_owner,
    _resolve_ec2_user,
    _load_or_create_serial,
    _normalize_fsx_buckets,
    _check_fsx_s3,
    _check_external_nfs_reachable,
    _storage_summary_lines,
    _validate_network,
    _derive_head_node_bootstrap_timeout,
    _derive_docker_compose_staging,
    _derive_results_bucket,
    _validate_download_checksum,
)

# ---------------------------------------------------------------------------
# _b
# ---------------------------------------------------------------------------


class TestB:
    def test_true_returns_true_string(self):
        assert _b(True) == "true"

    def test_false_returns_false_string(self):
        assert _b(False) == "false"

    def test_truthy_int(self):
        assert _b(1) == "true"

    def test_falsy_int(self):
        assert _b(0) == "false"

    def test_nonempty_string_is_truthy(self):
        assert _b("yes") == "true"

    def test_empty_string_is_falsy(self):
        assert _b("") == "false"


# ---------------------------------------------------------------------------
# _validate_az_input
# ---------------------------------------------------------------------------


class TestValidateAzInput:
    def test_valid_az_passes(self):
        _validate_az_input("us-east-1a")  # must not raise
        _validate_az_input("eu-west-2b")
        _validate_az_input("ap-southeast-1c")

    def test_region_string_raises(self):
        with pytest.raises(SystemExit):
            _validate_az_input("us-east-1")

    def test_two_digit_region_raises(self):
        with pytest.raises(SystemExit):
            _validate_az_input("eu-west-2")

    def test_ap_region_raises(self):
        with pytest.raises(SystemExit):
            _validate_az_input("ap-southeast-1")

    @pytest.mark.parametrize("az", ["us-gov-west-1a", "us-gov-east-1b", "us-iso-east-1a"])
    def test_multi_segment_partition_az_passes(self, az):
        _validate_az_input(az)

    @pytest.mark.parametrize("region", ["us-gov-west-1", "us-iso-east-1"])
    def test_multi_segment_partition_region_raises(self, region):
        with pytest.raises(SystemExit):
            _validate_az_input(region)

    @pytest.mark.parametrize("bad", ["", "useast1a", "us-east-1ab", "us-east-a", "US-EAST-1A"])
    def test_malformed_az_raises(self, bad):
        with pytest.raises(SystemExit):
            _validate_az_input(bad)


# ---------------------------------------------------------------------------
# _validate_cluster_name
# ---------------------------------------------------------------------------


class TestValidateClusterName:
    def test_simple_valid(self):
        _validate_cluster_name("mycluster")

    def test_hyphens_and_digits_valid(self):
        _validate_cluster_name("my-cluster-01")

    def test_exactly_27_chars_valid(self):
        _validate_cluster_name("a" * 27)

    def test_digit_start_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_name("1cluster")

    def test_28_chars_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_name("a" * 28)

    def test_uppercase_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_name("MyCluster")

    def test_underscore_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_name("my_cluster")

    def test_leading_hyphen_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_name("-cluster")

    def test_space_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_name("my cluster")

    def test_empty_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_name("")

    def test_trailing_hyphen_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_name("cluster-")

    def test_consecutive_hyphens_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_name("my--cluster")


# ---------------------------------------------------------------------------
# _validate_cluster_owner
# ---------------------------------------------------------------------------


class TestValidateClusterOwner:
    def test_simple_lowercase_valid(self):
        _validate_cluster_owner("rodney")

    def test_alphanumeric_with_hyphens_valid(self):
        _validate_cluster_owner("rodney-marable")

    def test_starts_with_digit_valid(self):
        _validate_cluster_owner("1user")

    def test_max_length_valid(self):
        _validate_cluster_owner("a" * 63)  # 1 start + 62 body chars

    def test_uppercase_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_owner("RodneyMarable")

    def test_underscore_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_owner("rodney_marable")

    def test_space_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_owner("rodney marable")

    def test_empty_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_owner("")

    def test_at_sign_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_owner("rodney@example.com")

    def test_too_long_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_owner("a" * 64)

    def test_trailing_hyphen_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_owner("rodney-")

    def test_consecutive_hyphens_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_owner("rod--ney")


# ---------------------------------------------------------------------------
# _resolve_ec2_user
# ---------------------------------------------------------------------------


class TestResolveEc2User:
    def test_ubuntu2204(self):
        user, home = _resolve_ec2_user("ubuntu2204")
        assert user == "ubuntu"
        assert home == "/home/ubuntu"

    def test_ubuntu2404(self):
        user, home = _resolve_ec2_user("ubuntu2404")
        assert user == "ubuntu"
        assert home == "/home/ubuntu"

    def test_unknown_os_raises(self):
        with pytest.raises(SystemExit):
            _resolve_ec2_user("not-an-os")

    def test_rhel9(self):
        """PCluster's own OS_MAPPING["rhel9"] gives this login name; a mismatch
        means every ssh and every chown in the toolkit targets a user that does
        not exist on the node."""
        for base_os in ("rhel9", "rhel9arm"):
            assert _resolve_ec2_user(base_os) == ("ec2-user", "/home/ec2-user")

    def test_alinux2023(self):
        """Same login name, from PCluster's own OS_MAPPING["alinux2023"]."""
        for base_os in ("alinux2023", "alinux2023arm"):
            assert _resolve_ec2_user(base_os) == ("ec2-user", "/home/ec2-user")

    @pytest.mark.parametrize(
        "base_os",
        [
            "rhel8",
            "rhel8arm",
            "rhel10",
            "alinux2",
            "amzn2",
            "amzn2023",
            "rocky9",
            "centos7",
        ],
    )
    def test_unsupported_oses_are_rejected_by_exact_name(self, base_os):
        """rhel8 and rhel10 are the point of this list. While the RHEL arm was a
        `"rhel" in base_os` substring test, both were accepted and returned a login
        name, even though no template branch, arch table, or playbook gate knows
        either value -- so the build proceeded to a node nothing could reach.
        alinux2 and amzn2 are the same hazard on the AL2023 arm, which the
        templates select with `'alinux' in base_os`; amzn2023 is PCluster's AMI
        naming for the OS we call alinux2023 and must not be accepted as an alias.
        TestPackageManagersMatchTheRenderedOs in tests/test_templates.py is what
        closes the set by equality; these are the sampled rejects."""
        with pytest.raises(SystemExit):
            _resolve_ec2_user(base_os)


# ---------------------------------------------------------------------------
# _load_or_create_serial
# ---------------------------------------------------------------------------


class TestLoadOrCreateSerial:
    def test_creates_new_serial_when_missing(self, tmp_path):
        cluster_dir = str(tmp_path)
        serial_file, serial_number, datestamp, was_created = _load_or_create_serial(
            cluster_dir, "mycluster"
        )

        assert os.path.isfile(serial_file)
        assert serial_number.startswith("mycluster-")
        assert datestamp == serial_number.split("-")[-1]
        assert len(datestamp) == 14  # %S%M%H%d%m%Y
        assert was_created is True

        with open(serial_file) as fh:
            on_disk = fh.read().strip()
        assert on_disk == serial_number

    def test_serial_file_mode_is_0600(self, tmp_path):
        cluster_dir = str(tmp_path)
        serial_file, _, _, _ = _load_or_create_serial(cluster_dir, "mycluster")
        mode = oct(os.stat(serial_file).st_mode & 0o777)
        assert mode == oct(0o600)

    def test_resumes_existing_serial(self, tmp_path, capsys):
        cluster_dir = str(tmp_path)
        serial_file_path = os.path.join(cluster_dir, "mycluster.serial")
        with open(serial_file_path, "w") as fh:
            fh.write("mycluster-00305910072026\n")

        serial_file, serial_number, datestamp, was_created = _load_or_create_serial(
            cluster_dir, "mycluster"
        )

        assert serial_number == "mycluster-00305910072026"
        assert datestamp == "00305910072026"
        assert was_created is False
        captured = capsys.readouterr()
        assert "Resuming" in captured.out
        assert "mycluster-00305910072026" in captured.out

    def test_resume_does_not_overwrite_file(self, tmp_path):
        cluster_dir = str(tmp_path)
        serial_file_path = os.path.join(cluster_dir, "mycluster.serial")
        original = "mycluster-00305910072026"
        with open(serial_file_path, "w") as fh:
            fh.write(original + "\n")

        _load_or_create_serial(cluster_dir, "mycluster")

        with open(serial_file_path) as fh:
            assert fh.read().strip() == original

    def test_empty_serial_file_raises_systemexit(self, tmp_path):
        serial_file_path = tmp_path / "mycluster.serial"
        serial_file_path.write_text("")
        with pytest.raises(SystemExit) as exc_info:
            _load_or_create_serial(str(tmp_path), "mycluster")
        assert "empty or corrupted" in str(exc_info.value)


# ---------------------------------------------------------------------------
# _normalize_fsx_buckets
# ---------------------------------------------------------------------------


class TestNormalizeFsxBuckets:
    """FSx requires the export bucket to equal the import bucket; only the prefixes
    may differ. This class previously asserted the opposite -- that two different
    buckets were accepted silently -- which documented a config FSx rejects at
    filesystem creation, twenty minutes into a build."""

    def test_one_bucket_with_distinct_paths_is_the_supported_shape(self, capsys):
        out_bucket, out_path = _normalize_fsx_buckets(
            "data-bucket", "data-bucket", "import/", "export/"
        )
        assert out_bucket == "data-bucket"
        assert out_path == "export/"
        assert capsys.readouterr().out == "", "the supported shape must not warn"

    def test_two_different_buckets_are_rejected(self, capsys):
        """FSx's own model: "The Amazon S3 export bucket must be the same as the
        import bucket specified by ImportPath." Failing here costs seconds; failing
        in CloudFormation costs the whole build."""
        with pytest.raises(SystemExit) as exc:
            _normalize_fsx_buckets("import-bucket", "export-bucket", "import/", "export/")
        message = str(exc.value)
        assert "import-bucket" in message and "export-bucket" in message
        assert "same bucket" in message

    def test_export_undefined_defaults_to_the_import_bucket_and_path(self, capsys):
        out_bucket, out_path = _normalize_fsx_buckets("my-bucket", "UNDEFINED", "data/", "export/")
        assert out_bucket == "my-bucket"
        assert out_path == "data/", "the export path must follow the import path"
        assert "WARNING" in capsys.readouterr().out

    def test_one_bucket_with_one_path_warns_about_overwriting(self, capsys):
        """Hydration source and dehydration target are the same prefix, so exported
        files land on top of the input data. Legal, but never what someone means."""
        out_bucket, out_path = _normalize_fsx_buckets("same", "same", "path/", "path/")
        assert (out_bucket, out_path) == ("same", "path/")
        out = capsys.readouterr().out
        assert "WARNING" in out
        assert "overwrite" in out

    @pytest.mark.parametrize("export_bucket", ["UNDEFINED", "export-bucket"])
    def test_an_undefined_import_bucket_is_rejected(self, export_bucket):
        """Callers only reach this function when enable_fsx_hydration is true, and
        nothing upstream in make_pcluster.py rejects an unset import bucket -- its
        only FSx-S3 checks are hydration-off-with-buckets-set and
        enable_fsx-false-with-hydration-on. Left alone, config.pcluster.j2 renders
        a literal "ImportPath: s3://UNDEFINED/input/" and the build dies in
        CloudFormation."""
        with pytest.raises(SystemExit) as exc:
            _normalize_fsx_buckets("UNDEFINED", export_bucket, "import", "export")
        assert "fsx_s3_import_bucket" in str(exc.value)
        assert "enable_fsx_hydration" in str(exc.value)


# ---------------------------------------------------------------------------
# _check_fsx_s3
# ---------------------------------------------------------------------------


class _FakeClientError(ClientError):
    def __init__(self, code="404"):
        super().__init__({"Error": {"Code": code, "Message": "Error"}}, "HeadBucket")


def _make_s3_client(head_ok=True, key_count=1, head_error_code="404"):
    """Return a minimal mock S3 client."""
    client = types.SimpleNamespace()

    def head_bucket(Bucket):
        if not head_ok:
            raise _FakeClientError(head_error_code)

    def list_objects_v2(Bucket, Prefix):
        return {"KeyCount": key_count}

    client.head_bucket = head_bucket
    client.list_objects_v2 = list_objects_v2
    return client


class TestCheckFsxS3:
    def test_undefined_bucket_skips(self):
        bad_client = _make_s3_client(head_ok=False)
        _check_fsx_s3(bad_client, "UNDEFINED", "some/path", "import")  # must not raise

    def test_valid_bucket_and_path_passes(self):
        client = _make_s3_client(head_ok=True, key_count=5)
        _check_fsx_s3(client, "my-bucket", "data/", "import")  # must not raise

    # All three failures exit, so a bare raises() cannot tell "bucket missing"
    # from "access denied" from "prefix empty" — three completely different
    # remediations for the operator. Assert on the diagnosis, and on the label
    # so an import/export mix-up is caught too.
    def test_missing_bucket_raises(self):
        client = _make_s3_client(head_ok=False)
        with pytest.raises(SystemExit) as exc:
            _check_fsx_s3(client, "missing-bucket", "data/", "import")
        msg = str(exc.value)
        assert "import bucket s3://missing-bucket not found" in msg
        assert "access is denied" not in msg

    def test_empty_path_raises(self):
        client = _make_s3_client(head_ok=True, key_count=0)
        with pytest.raises(SystemExit) as exc:
            _check_fsx_s3(client, "my-bucket", "empty/", "import")
        assert "s3://my-bucket/empty/" in str(exc.value)

    def test_access_denied_raises(self):
        client = _make_s3_client(head_ok=False, head_error_code="403")
        with pytest.raises(SystemExit) as exc:
            _check_fsx_s3(client, "private-bucket", "data/", "import")
        assert "access is denied" in str(exc.value)

    def test_export_label_is_reported_for_export_bucket(self):
        client = _make_s3_client(head_ok=False)
        with pytest.raises(SystemExit) as exc:
            _check_fsx_s3(client, "missing-bucket", "data/", "export")
        assert "export bucket" in str(exc.value)


class TestAnEmptyExportPrefixIsNotAnError:
    """An export prefix is a destination. FSx's own default is
    s3://import-bucket/FSxLustre<creation-timestamp> (ExportPath, botocore's FSx
    CreateFileSystemLustreConfiguration model), which cannot exist before the
    filesystem does, and any prefix an operator names for a first dehydration is
    empty too. Requiring objects there refused a valid configuration at the last
    check before the build -- after every earlier validation had passed."""

    def test_an_empty_export_prefix_passes(self):
        client = _make_s3_client(head_ok=True, key_count=0)
        _check_fsx_s3(
            client, "my-bucket", "output/", "export", require_objects=False
        )  # must not raise

    def test_an_empty_import_prefix_still_fails(self):
        """The vacuity guard: the fix must not become "stop checking either one".
        Lustre reads the import path, so an empty one hydrates nothing."""
        client = _make_s3_client(head_ok=True, key_count=0)
        with pytest.raises(SystemExit) as exc:
            _check_fsx_s3(client, "my-bucket", "input/", "import")
        assert "s3://my-bucket/input/" in str(exc.value)

    def test_the_export_bucket_itself_is_still_validated(self):
        """require_objects=False must skip only the prefix listing. A typo'd
        export bucket is a real error, and AWS requires the export bucket to
        equal the import bucket, so head_bucket still has to run."""
        client = _make_s3_client(head_ok=False)
        with pytest.raises(SystemExit) as exc:
            _check_fsx_s3(client, "typo-bucket", "output/", "export", require_objects=False)
        assert "export bucket s3://typo-bucket not found" in str(exc.value)

    def test_an_access_denied_export_bucket_still_fails(self):
        client = _make_s3_client(head_ok=False, head_error_code="403")
        with pytest.raises(SystemExit) as exc:
            _check_fsx_s3(client, "private-bucket", "output/", "export", require_objects=False)
        assert "access is denied" in str(exc.value)

    def test_the_export_prefix_is_never_listed(self):
        """Not just "does not exit": the listing must not happen at all. It costs
        an API call whose result is discarded, and on a prefix with millions of
        keys that is a paginated scan of an answer nobody reads."""
        client = _make_s3_client(head_ok=True, key_count=0)

        def explode(Bucket, Prefix):
            raise AssertionError(f"listed s3://{Bucket}/{Prefix}")

        client.list_objects_v2 = explode
        _check_fsx_s3(
            client, "my-bucket", "output/", "export", require_objects=False
        )  # must not raise

    def test_require_objects_defaults_to_true(self):
        """Every other caller must keep the strict behavior without saying so."""
        params = inspect.signature(_check_fsx_s3).parameters
        assert params["require_objects"].default is True
        assert params["require_objects"].kind is inspect.Parameter.KEYWORD_ONLY

    def test_make_pcluster_relaxes_the_export_call_and_only_that_one(self):
        """Which call site got the flag is the whole fix. Both take the same four
        positional arguments and differ only in the label, so a flag on the
        import call reads as plausible and silently stops validating the one
        path that has to hold data. Both call sites live in
        pcluster_core.core_create_cluster since the core/shim split."""
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(repo_root, "src", "pcluster_core.py")) as fh:
            tree = ast.parse(fh.read())
        relaxed = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "id", None) != "_check_fsx_s3":
                continue
            label = node.args[3].value
            flags = [kw.value.value for kw in node.keywords if kw.arg == "require_objects"]
            relaxed[label] = flags[0] if flags else True
        assert relaxed == {"import": True, "export": False}, relaxed


# ---------------------------------------------------------------------------
# _check_external_nfs_reachable
# ---------------------------------------------------------------------------


class _FakeSocket:
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _fake_connect_ok(*a, **kw):
    return _FakeSocket()


def _fake_connect_refused(*a, **kw):
    raise OSError("Connection refused")


class _FakeShowmountResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestCheckExternalNfsReachable:
    """Only one outcome (confirmed-empty exports) is a hard failure; every
    other branch is a WARNING that lets the build proceed, because this
    check runs from the operator's machine, which may not share the target
    VPC's network path to the filer."""

    def test_reachable_with_exports_passes_silently(self, monkeypatch, capsys):
        monkeypatch.setattr("pcluster_core.socket.create_connection", _fake_connect_ok)
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            return _FakeShowmountResult(
                returncode=0, stdout="Export list for filer.corp:\n/data *\n"
            )

        monkeypatch.setattr("pcluster_core.subprocess.run", fake_run)
        _check_external_nfs_reachable("filer.corp")  # must not raise
        assert seen["cmd"] == ["showmount", "-e", "filer.corp"]
        assert "WARNING" not in capsys.readouterr().out

    def test_unreachable_port_warns_and_never_calls_showmount(self, monkeypatch, capsys):
        monkeypatch.setattr("pcluster_core.socket.create_connection", _fake_connect_refused)

        def explode(*a, **kw):
            raise AssertionError("showmount should not run when port 2049 is unreachable")

        monkeypatch.setattr("pcluster_core.subprocess.run", explode)
        _check_external_nfs_reachable("filer.corp")  # must not raise
        out = capsys.readouterr().out
        assert "WARNING" in out
        assert "port 2049" in out

    def test_showmount_missing_warns(self, monkeypatch, capsys):
        monkeypatch.setattr("pcluster_core.socket.create_connection", _fake_connect_ok)

        def missing(*a, **kw):
            raise FileNotFoundError("showmount")

        monkeypatch.setattr("pcluster_core.subprocess.run", missing)
        _check_external_nfs_reachable("filer.corp")  # must not raise
        assert "not installed" in capsys.readouterr().out

    def test_showmount_timeout_warns(self, monkeypatch, capsys):
        monkeypatch.setattr("pcluster_core.socket.create_connection", _fake_connect_ok)

        def slow(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="showmount", timeout=10)

        monkeypatch.setattr("pcluster_core.subprocess.run", slow)
        _check_external_nfs_reachable("filer.corp")  # must not raise
        assert "timed out" in capsys.readouterr().out

    def test_showmount_nonzero_exit_warns(self, monkeypatch, capsys):
        monkeypatch.setattr("pcluster_core.socket.create_connection", _fake_connect_ok)
        monkeypatch.setattr(
            "pcluster_core.subprocess.run",
            lambda *a, **kw: _FakeShowmountResult(
                returncode=1, stderr="showmount: filer.corp: RPC: Program not registered"
            ),
        )
        _check_external_nfs_reachable("filer.corp")  # must not raise
        out = capsys.readouterr().out
        assert "WARNING" in out
        assert "NFSv4-only" in out

    def test_confirmed_empty_exports_raises(self, monkeypatch):
        monkeypatch.setattr("pcluster_core.socket.create_connection", _fake_connect_ok)
        monkeypatch.setattr(
            "pcluster_core.subprocess.run",
            lambda *a, **kw: _FakeShowmountResult(
                returncode=0, stdout="Export list for filer.corp:\n"
            ),
        )
        with pytest.raises(SystemExit) as exc:
            _check_external_nfs_reachable("filer.corp")
        assert "exports nothing" in str(exc.value)


# ---------------------------------------------------------------------------
# _storage_summary_lines
# ---------------------------------------------------------------------------

_STORAGE_DEFAULTS = dict(
    ebs_shared_dir="/shared",
    ebs_shared_volume_size=250,
    ebs_shared_volume_type="gp3",
    enable_efs=False,
    efs_throughput_mode="bursting",
    enable_fsx=False,
    fsx_size=1200,
    enable_fsx_hydration=False,
    fsx_s3_import_bucket="in-bucket",
    fsx_s3_import_path="input/",
    fsx_s3_export_bucket="out-bucket",
    fsx_s3_export_path="output/",
    enable_external_nfs=False,
    external_nfs_server="nfs.example.com",
)


def _storage(**overrides):
    return _storage_summary_lines(**{**_STORAGE_DEFAULTS, **overrides})


class TestDeriveHeadNodeBootstrapTimeout:
    """PCluster creates the HeadNodeWaitCondition before the head node instance
    (cluster_stack.py:293 precedes _add_head_node at 295) and the filesystem IDs
    land in HeadNodeLaunchTemplate, so EFS/FSx provisioning spends the bootstrap
    budget before preinstall runs. On the osiris build that failed, FSx took
    17m22s of the stock 2100s window; preinstall got ~15 of 35 minutes."""

    def test_no_shared_filesystem_leaves_the_pcluster_default_alone(self):
        assert (
            _derive_head_node_bootstrap_timeout(configured=2100, enable_efs=False, enable_fsx=False)
            == 2100
        )

    def test_fsx_adds_its_allowance(self):
        assert (
            _derive_head_node_bootstrap_timeout(configured=2100, enable_efs=False, enable_fsx=True)
            == 3900
        )

    def test_efs_adds_a_smaller_allowance(self):
        """Measured on the successful osiris build of 2026-07-28: the filesystem
        completed in 4s but its mount target took 1m33s, and the head node
        instance appeared 4m24s after the wait condition started -- so the 600s
        allowance is headroom over a measured window, not an estimate."""
        assert (
            _derive_head_node_bootstrap_timeout(configured=2100, enable_efs=True, enable_fsx=False)
            == 2700
        )

    def test_both_filesystems_take_the_max_not_the_sum(self):
        """EFS and FSx are independent CloudFormation resources with no dependency
        between them, so they provision concurrently and the head node waits on the
        slower one. Summing would over-grant by the EFS allowance, and an additive
        implementation passes every single-filesystem case above."""
        both = _derive_head_node_bootstrap_timeout(
            configured=2100, enable_efs=True, enable_fsx=True
        )
        assert both == 3900, "expected max(FSx, EFS), not FSx + EFS"

    def test_an_explicit_value_is_never_overridden(self):
        """Including downward: an operator who deliberately shortens the window to
        fail fast must not have it silently raised because FSx is on."""
        assert (
            _derive_head_node_bootstrap_timeout(configured=1200, enable_efs=True, enable_fsx=True)
            == 1200
        )
        assert (
            _derive_head_node_bootstrap_timeout(configured=7200, enable_efs=False, enable_fsx=True)
            == 7200
        )

    def test_an_explicit_value_is_clamped_to_cloudformations_ceiling(self):
        """CloudFormation rejects a WaitCondition Timeout above 43200 (12 hours);
        PCluster's own schema only validates min=1, so nothing downstream catches
        it before the stack fails."""
        assert (
            _derive_head_node_bootstrap_timeout(
                configured=99999, enable_efs=False, enable_fsx=False
            )
            == 43200
        )
        assert (
            _derive_head_node_bootstrap_timeout(configured=0, enable_efs=False, enable_fsx=False)
            == 1
        )

    def test_the_signature_is_keyword_only(self):
        """Three arguments, two of them bools: passing enable_efs where enable_fsx
        belongs yields a plausible-looking timeout rather than an error."""
        import inspect

        params = inspect.signature(_derive_head_node_bootstrap_timeout).parameters
        positional = [
            name
            for name, p in params.items()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        assert not positional, f"{positional} can be passed positionally"

    def test_the_make_pcluster_call_site_names_every_argument(self):
        import ast

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(repo_root, "make_pcluster.py")) as fh:
            tree = ast.parse(fh.read())
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "_derive_head_node_bootstrap_timeout"
        ]
        assert calls, "make_pcluster.py never derives the bootstrap timeout"
        for call in calls:
            assert not call.args, "call site passes a positional argument"
            passed = {kw.arg for kw in call.keywords}
            assert passed, (
                "test_the_make_pcluster_call_site_names_every_argument: nothing to assert absence against"
            )
            assert None not in passed, "call site splats **kwargs instead of naming"
            assert passed == {"configured", "enable_efs", "enable_fsx"}, (
                f"call site keywords {sorted(passed)} do not match the signature"
            )


class TestStorageSummaryLinesTakesKeywordsOnly:
    """14 parameters, most of them the same type, so a transposed pair at the call
    site produced a plausible summary instead of an error. Two mutations proved it
    while the signature was positional -- swapping the import/export bucket pairs,
    and swapping the EBS size with the volume type -- and both survived the entire
    suite, because every test called through a keyword dict and only production
    used the ordering. Keyword-only makes the whole class unrepresentable."""

    def test_positional_arguments_are_rejected(self):
        import inspect

        params = inspect.signature(_storage_summary_lines).parameters
        positional = [
            name
            for name, p in params.items()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        assert not positional, (
            f"{positional} can be passed positionally again; a transposed pair at "
            "the call site would render a wrong summary rather than raising"
        )

    def test_calling_positionally_raises(self):
        with pytest.raises(TypeError):
            _storage_summary_lines(*_STORAGE_DEFAULTS.values())

    def test_the_make_pcluster_call_site_names_every_argument(self):
        """A keyword-only signature is only half of it: the call site has to pass
        names, and `f(**locals())` or a stray positional would defeat the guard.
        Lives in pcluster_core.core_create_cluster since the core/shim split."""
        import ast

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(repo_root, "src", "pcluster_core.py")) as fh:
            tree = ast.parse(fh.read())
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "_storage_summary_lines"
        ]
        assert calls, "no _storage_summary_lines call found in src/pcluster_core.py"
        expected = set(_STORAGE_DEFAULTS)
        for call in calls:
            assert not call.args, "call site passes a positional argument"
            passed = {kw.arg for kw in call.keywords}
            assert passed, (
                "test_the_make_pcluster_call_site_names_every_argument: nothing to assert absence against"
            )
            assert None not in passed, "call site splats **kwargs instead of naming"
            assert passed == expected, (
                f"call site keywords {sorted(passed ^ expected)} do not match the "
                "function's parameters"
            )


_VALIDATE_NETWORK_DEFAULTS = dict(
    ec2client=object(),
    az="us-east-1a",
    vpc_name="vpc_default",
    headnode_subnet_id="",
    compute_az_list=["us-east-1a"],
    compute_subnet_ids_override="",
    use_private_compute_subnet="false",
    cluster_name="test-cluster",
    gpu_az_list=None,
    gpu_subnet_ids_override=None,
    use_private_gpu_subnet="false",
    enable_loginnode="false",
    loginnode_subnet_id="",
)


class TestValidateNetworkTakesKeywordsOnly:
    """Adding the login-node subnet block made this four near-identical
    subnet-resolution blocks (head/compute/gpu/login) with several
    similarly-typed string/list parameters -- exactly the shape that let
    _storage_summary_lines silently accept a transposed argument pair through
    the whole suite before it was made keyword-only. Same guard, same reason."""

    def test_positional_arguments_are_rejected(self):
        params = inspect.signature(_validate_network).parameters
        positional = [
            name
            for name, p in params.items()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        assert not positional, (
            f"{positional} can be passed positionally again; a transposed pair "
            "at the call site would resolve a wrong subnet rather than raising"
        )

    def test_calling_positionally_raises(self):
        with pytest.raises(TypeError):
            _validate_network(*_VALIDATE_NETWORK_DEFAULTS.values())

    def test_the_make_pcluster_call_site_names_every_argument(self):
        """A keyword-only signature is only half of it: the call site has to
        pass names, and f(**locals()) or a stray positional would defeat the
        guard. core_create_cluster (pcluster_core.py, since the core/shim
        split) calls this via ThreadPoolExecutor.submit(_validate_network,
        ...), so the function reference itself is a legitimate positional
        argument to submit() -- everything else must still be a keyword."""
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(repo_root, "src", "pcluster_core.py")) as fh:
            tree = ast.parse(fh.read())
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and any(isinstance(a, ast.Name) and a.id == "_validate_network" for a in node.args)
        ]
        assert calls, "no _validate_network call found in src/pcluster_core.py"
        expected = set(_VALIDATE_NETWORK_DEFAULTS)
        for call in calls:
            other_positional = [
                a
                for a in call.args
                if not (isinstance(a, ast.Name) and a.id == "_validate_network")
            ]
            assert not other_positional, "call site passes a positional argument"
            passed = {kw.arg for kw in call.keywords}
            assert passed, (
                "test_the_make_pcluster_call_site_names_every_argument: nothing to assert absence against"
            )
            assert None not in passed, "call site splats **kwargs instead of naming"
            assert passed == expected, (
                f"call site keywords {sorted(passed ^ expected)} do not match "
                "the function's parameters"
            )


class TestStorageSummaryLines:
    """The Options: line named "FSx/Lustre" and stopped there, so an operator was
    told Lustre existed without being told where it was mounted or how large it
    is. Every filesystem the cluster actually has must name its mount point."""

    def test_the_ebs_volume_is_always_named_with_its_mount_point(self):
        text = "\n".join(_storage())
        assert "/shared" in text
        assert "EBS (gp3, 250 GB)" in text

    def test_every_active_mount_line_starts_its_description_in_the_same_column(self):
        """The real bug: a fixed field width of 6 didn't fit `/shared` (7
        characters, the actual --ebs_shared_dir default), so its line lost its
        padding entirely while /efs, /fsx, and /nfs (all 4 characters) kept
        theirs -- every real build's summary had a misaligned first line."""
        lines = _storage(enable_efs=True, enable_fsx=True, enable_external_nfs=True)
        markers = ("EBS (", "EFS (", "FSx for Lustre (", "external NFS (")
        columns = {
            line.split()[0]: next(line.find(m) for m in markers if m in line)
            for line in lines
            if any(m in line for m in markers)
        }
        assert len(columns) == 4, f"expected four mount lines, found {columns}"
        assert len(set(columns.values())) == 1, f"mount lines do not align: {columns}"

    def test_a_longer_custom_mount_point_widens_every_column(self):
        """The column width must be derived from the longest active label, not
        a hardcoded constant -- otherwise a longer override just relocates the
        misalignment bug instead of fixing it."""
        default_col = next(
            line.find("EFS (") for line in _storage(enable_efs=True) if "EFS (" in line
        )
        wide_col = next(
            line.find("EFS (")
            for line in _storage(ebs_shared_dir="/very-long-share", enable_efs=True)
            if "EFS (" in line
        )
        assert wide_col > default_col, (
            "a longer --ebs_shared_dir did not widen the /efs column; the "
            "width looks hardcoded rather than derived from the active labels"
        )

    def test_a_custom_ebs_mount_point_is_reported(self):
        text = "\n".join(_storage(ebs_shared_dir="/work"))
        assert "/work" in text
        assert "/shared" not in text

    def test_lustre_names_its_mount_point_and_size(self):
        """The reported bug: enable_fsx=true produced a summary with no /fsx."""
        text = "\n".join(_storage(enable_fsx=True, fsx_size=2400))
        assert "/fsx" in text, "an FSx cluster's summary does not mention /fsx"
        assert "FSx for Lustre (2400 GB)" in text

    def test_lustre_is_absent_when_not_enabled(self):
        text = "\n".join(_storage())
        assert "/fsx" not in text
        assert "Lustre" not in text

    def test_efs_names_its_mount_point_and_throughput_mode(self):
        text = "\n".join(_storage(enable_efs=True, efs_throughput_mode="elastic"))
        assert "/efs" in text
        assert "elastic" in text

    def test_efs_is_absent_when_not_enabled(self):
        assert "/efs" not in "\n".join(_storage())

    def test_external_nfs_names_its_server(self):
        text = "\n".join(_storage(enable_external_nfs=True, external_nfs_server="filer.corp"))
        assert "/nfs" in text
        assert "filer.corp" in text

    def test_external_nfs_is_absent_when_not_enabled(self):
        assert "/nfs" not in "\n".join(_storage())

    def test_hydration_reports_both_buckets_and_the_helper_scripts(self):
        """A hydrated filesystem is useless if the operator cannot find the
        import/export scripts; the playbook only prints them when hydration is
        on, and that task is skipped on a plain FSx cluster."""
        text = "\n".join(_storage(enable_fsx=True, enable_fsx_hydration=True))
        assert "s3://in-bucket/input/" in text
        assert "s3://out-bucket/output/" in text
        assert "import-s3-to-lustre.sh" in text
        assert "export-lustre-to-s3.sh" in text
        assert "check-lustre-export-progress.sh" in text

    def test_a_plain_fsx_cluster_says_nothing_about_hydration(self):
        """osiris ran with enable_fsx=true and hydration off. Naming S3 buckets
        that were never configured would be worse than saying nothing."""
        text = "\n".join(_storage(enable_fsx=True))
        assert "s3://" not in text
        assert "import-s3-to-lustre.sh" not in text

    def test_every_enabled_filesystem_appears_together(self):
        text = "\n".join(_storage(enable_efs=True, enable_fsx=True, enable_external_nfs=True))
        for mount in ("/shared", "/efs", "/fsx", "/nfs"):
            assert mount in text, f"{mount} missing from a fully-loaded cluster"


_PKG_DIR_CASES = [
    ({}, "/shared/pkg"),
    ({"enable_external_nfs": True}, "/nfs/pkg"),
    ({"enable_efs": True}, "/efs/pkg"),
    ({"enable_efs": True, "enable_external_nfs": True}, "/efs/pkg"),
    ({"enable_fsx": True}, "/fsx/pkg"),
    ({"enable_fsx": True, "enable_efs": True}, "/fsx/pkg"),
    ({"enable_fsx": True, "enable_efs": True, "enable_external_nfs": True}, "/fsx/pkg"),
]


class TestStorageSummaryPkgDirMatchesTheVarsFile:
    """pkg_dir is not a Python variable -- vars_file.j2 derives it, with
    fsx > efs > nfs > ebs precedence, and the summary has to reproduce that or it
    tells the operator Spack lives somewhere it does not. The rendered template
    is the reference, so a change to either side has to be made to both."""

    @staticmethod
    def _rendered_pkg_dir(overrides, cluster_params):
        import yaml
        from jinja2 import Environment, FileSystemLoader, StrictUndefined

        params = dict(cluster_params)
        for key in ("enable_efs", "enable_fsx", "enable_external_nfs"):
            params[key] = "true" if overrides.get(key) else "false"
        params["ebs_shared_dir"] = _STORAGE_DEFAULTS["ebs_shared_dir"]
        env = Environment(
            loader=FileSystemLoader(
                os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "templates",
                )
            ),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
        )
        rendered = env.get_template("vars_file.j2").render(**params)
        return yaml.safe_load(rendered)["pkg_dir"]

    @pytest.mark.parametrize("overrides,expected", _PKG_DIR_CASES)
    def test_the_summary_reports_the_documented_precedence(self, overrides, expected):
        text = "\n".join(_storage(**overrides))
        assert f"install under {expected}" in text, (
            f"with {overrides or 'EBS only'} the summary must point at {expected}"
        )

    @pytest.mark.parametrize("overrides,expected", _PKG_DIR_CASES)
    def test_the_summary_agrees_with_the_rendered_vars_file(
        self, overrides, expected, cluster_params
    ):
        assert self._rendered_pkg_dir(overrides, cluster_params) == expected
        assert f"install under {expected}" in "\n".join(_storage(**overrides))


# ---------------------------------------------------------------------------
# _derive_docker_compose_staging
# ---------------------------------------------------------------------------


class TestDeriveDockerComposeStaging:
    """Amazon Linux 2023 is the only supported base_os with no
    docker-compose-plugin package, so aws-parallelcluster-monitoring's own
    installer/os/alinux2023.sh curls the binary from github.com at node boot --
    unverified, and impossible from a private subnet. The build stages a
    checksummed copy to S3 and the wrapper installs it first.

    This lives in pcluster_core.py rather than inline in make_pcluster.py because
    inline it was unreachable by any test: dropping the enable_monitoring half of
    the gate and inverting the arch selection both survived the entire suite."""

    _ARM = ("ubuntu2204arm", "ubuntu2404arm", "rhel9arm", "alinux2023arm")

    def _derive(self, base_os, enable_monitoring=True, version="v2.29.7"):
        return _derive_docker_compose_staging(
            base_os=base_os,
            arm_oses=self._ARM,
            enable_monitoring=enable_monitoring,
            version=version,
        )

    @pytest.mark.parametrize("base_os", ["alinux2023", "alinux2023arm"])
    def test_al2023_with_monitoring_stages_the_plugin(self, base_os):
        stage, _ = self._derive(base_os)
        assert stage is True

    @pytest.mark.parametrize("base_os", ["alinux2023", "alinux2023arm"])
    def test_al2023_without_monitoring_stages_nothing(self, base_os):
        """The upload happens inside create_pcluster.yml's monitoring block, so
        with monitoring off there is no S3 object -- and the wrapper that would
        fetch it is not rendered either. Staging anyway would be a download and
        an upload for a file nothing reads."""
        stage, _ = self._derive(base_os, enable_monitoring=False)
        assert stage is False

    @pytest.mark.parametrize(
        "base_os",
        ["ubuntu2204", "ubuntu2404", "ubuntu2204arm", "ubuntu2404arm", "rhel9", "rhel9arm"],
    )
    def test_every_other_os_stages_nothing_even_with_monitoring(self, base_os):
        """These all install docker-compose-plugin from a signed distro
        repository. Staging for them uploads a binary no node fetches, and
        renders a wrapper block whose `aws s3 cp` would be the only thing that
        could fail."""
        stage, _ = self._derive(base_os)
        assert stage is False

    @pytest.mark.parametrize(
        "base_os,expected",
        [
            ("alinux2023", "x86_64"),
            ("alinux2023arm", "aarch64"),
            ("ubuntu2404", "x86_64"),
            ("ubuntu2404arm", "aarch64"),
            ("rhel9", "x86_64"),
            ("rhel9arm", "aarch64"),
        ],
    )
    def test_the_arch_matches_the_plugin_binarys_own_suffix(self, base_os, expected):
        """docker/compose publishes docker-compose-linux-aarch64 and
        -x86_64 -- uname -m's spelling, not arm64/amd64. The arch also selects
        which of the two checksums in pcluster_defaults.yml is used, so an
        inverted mapping downloads the right binary for the wrong architecture,
        fails the checksum at build time if lucky, and ships an unrunnable
        binary if not."""
        _, arch = self._derive(base_os)
        assert arch == expected

    def test_the_arch_is_derived_for_every_os_not_just_al2023(self):
        """make_pcluster.py threads docker_compose_arch to templates
        unconditionally -- vars_file.j2 references it whenever monitoring is on --
        so returning None or "" off the AL2023 path is an UndefinedError."""
        for base_os in ("ubuntu2404", "rhel9arm"):
            _, arch = self._derive(base_os, enable_monitoring=False)
            assert arch in ("x86_64", "aarch64")

    @pytest.mark.parametrize("version", ["2.29.7", "v2.29", "v2", "latest", "", None, "v2.29.7.1"])
    def test_a_malformed_version_exits_rather_than_building_a_bad_url(self, version):
        """The version goes straight into the GitHub release URL and into the S3
        object name. A malformed one is a 404 at build time, which is cheap -- but
        it is also the name the wrapper's `aws s3 cp` uses at node boot, so the
        failure would otherwise land twenty minutes in."""
        with pytest.raises(SystemExit):
            self._derive("alinux2023", version=version)

    def test_a_well_formed_version_is_accepted(self):
        """Guards the test above against passing because everything raises."""
        stage, arch = self._derive("alinux2023", version="v2.30.0")
        assert stage is True and arch == "x86_64"


class TestDownloadChecksumsAreValidatedBeforeAnythingIsCreated:
    """Every checksum the build resolves is handed to Ansible's get_url, which
    splits on ':' and int()s the remainder base 16. A placeholder therefore
    survives all of make_pcluster.py and dies 18 tasks into the playbook with
    "The checksum format is invalid" -- after the five managed IAM policies, the
    role, the keypair, and the S3 bucket exist, all of which must then be cleaned
    up before a retry.

    That is not hypothetical: _HARDCODED_DEFAULTS shipped
    "sha256:REPLACE_WITH_ACTUAL_SHA256" for three keys, and a
    <cluster>_defaults.yml written before the docker_compose_* keys existed fell
    straight through _resolve to it, failing a live alinux2023 build of osiris.

    Two independent properties are pinned here: the validator rejects what
    get_url would reject, and no default is a value it would reject."""

    _REAL = "sha256:" + "a" * 64

    def test_a_well_formed_digest_is_accepted(self):
        """Vacuity guard: without this, a validator that rejected everything
        would pass every negative test below."""
        _validate_download_checksum("monitoring_version_checksum", self._REAL)

    def test_mixed_case_hex_is_accepted(self):
        _validate_download_checksum("x", "sha256:" + "AbCdEf0123456789" * 4)

    @pytest.mark.parametrize(
        "value",
        [
            "sha256:REPLACE_WITH_ACTUAL_SHA256",  # the exact value that shipped
            "sha256:" + "a" * 63,  # one digit short
            "sha256:" + "a" * 65,  # one digit long
            "sha256:" + "g" * 64,  # not hex
            "a" * 64,  # no algorithm prefix
            "md5:" + "a" * 32,  # get_url accepts md5, the toolkit does not
            "sha256:",
            "sha256",
            "",
            None,
            "  sha256:" + "a" * 64,  # leading whitespace
            "sha256:" + "a" * 64 + "\n",  # trailing newline from a shell capture
        ],
    )
    def test_anything_get_url_would_reject_exits(self, value):
        with pytest.raises(SystemExit):
            _validate_download_checksum("monitoring_version_checksum", value)

    def test_the_error_names_the_parameter_and_how_to_fix_it(self):
        """ "The checksum format is invalid" names neither the key nor the file it
        came from, which is what made the live failure hard to place."""
        with pytest.raises(SystemExit) as exc:
            _validate_download_checksum("docker_compose_checksum_x86_64", "sha256:nope")
        msg = str(exc.value)
        assert "docker_compose_checksum_x86_64" in msg
        assert "sha256" in msg
        assert "defaults file" in msg

    # --- the defaults themselves ---

    _CHECKSUM_KEYS = (
        "monitoring_version_checksum",
        "docker_compose_checksum_x86_64",
        "docker_compose_checksum_aarch64",
    )

    @staticmethod
    def _hardcoded_defaults():
        import ast

        # Read from pcluster_core, not make_pcluster: the dict moved to
        # module scope in the core layer so an MCP create_cluster wrapper
        # could reach it (it was a local inside main() before, unreachable
        # by anything else). make_pcluster.py now just aliases it.
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "src",
            "pcluster_core.py",
        )
        with open(path) as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and getattr(node.targets[0], "id", "") == "MAKE_CLUSTER_DEFAULTS"
            ):
                return ast.literal_eval(node.value)
        raise AssertionError("MAKE_CLUSTER_DEFAULTS not found in src/pcluster_core.py")

    @staticmethod
    def _toolkit_defaults():
        import yaml

        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "pcluster_defaults.yml",
        )
        with open(path) as fh:
            return yaml.safe_load(fh)

    @pytest.mark.parametrize("key", _CHECKSUM_KEYS)
    def test_download_checksum_defaults_are_real_digests(self, key):
        """_HARDCODED_DEFAULTS is the only source when no defaults file is passed,
        and the fallback for any key a stale defaults file predates -- so a
        placeholder here is a build failure, not a prompt to the operator."""
        hardcoded = self._hardcoded_defaults()
        assert key in hardcoded, f"{key} has no _HARDCODED_DEFAULTS entry"
        _validate_download_checksum(key, hardcoded[key])

    @pytest.mark.parametrize("key", _CHECKSUM_KEYS)
    def test_the_toolkit_defaults_file_agrees_with_the_hardcoded_default(self, key):
        """Two sources for the same digest that disagree means the value depends
        on whether --use_defaults was passed, which no operator would expect."""
        assert self._hardcoded_defaults()[key] == self._toolkit_defaults()[key], (
            f"{key} differs between _HARDCODED_DEFAULTS and pcluster_defaults.yml"
        )

    @pytest.mark.parametrize("key", _CHECKSUM_KEYS)
    def test_the_toolkit_defaults_file_ships_a_real_digest_too(self, key):
        _validate_download_checksum(key, self._toolkit_defaults()[key])

    def test_no_tracked_defaults_file_ships_a_placeholder(self):
        """A placeholder in any tracked *_defaults.yml is the same failure one
        indirection away."""
        import glob
        import yaml

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        tracked = subprocess.run(
            ["git", "ls-files", "*_defaults.yml"],
            cwd=root,
            capture_output=True,
            text=True,
        ).stdout.split()
        assert "pcluster_defaults.yml" in tracked, "vacuity guard: no defaults file tracked"
        for rel in tracked:
            with open(os.path.join(root, rel)) as fh:
                data = yaml.safe_load(fh) or {}
            for key in self._CHECKSUM_KEYS:
                if key in data:
                    _validate_download_checksum(f"{rel}:{key}", data[key])

    def test_make_pcluster_validates_before_it_creates_anything(self):
        """A validator nothing calls is decoration. The call must also come before
        the first AWS mutation -- validating after _setup_iam would still leave
        five managed policies and a role behind on the failing path, which is the
        entire cost this check exists to avoid.

        Since the core/shim split, checksum validation (during CLI param
        resolution) and _setup_iam (inside core_create_cluster) no longer live
        in the same file, and the property this test pins is now even
        stronger: validation must complete, in the CLI shim, before
        core_create_cluster -- the only caller of _setup_iam -- is ever
        invoked at all. Anchored on the core_create_cluster call site rather
        than on a line number so that reordering unrelated code does not fail
        it."""
        import ast

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "make_pcluster.py")) as fh:
            shim_src = fh.read()
        shim_tree = ast.parse(shim_src)

        validate_lines, core_call_lines = [], []
        for node in ast.walk(shim_tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name == "_validate_download_checksum":
                validate_lines.append(node.lineno)
            elif name == "core_create_cluster":
                core_call_lines.append(node.lineno)

        assert len(validate_lines) >= 2, (
            "expected the monitoring tarball and the compose plugin checksums to "
            f"be validated; found {len(validate_lines)} call(s)"
        )
        assert core_call_lines, (
            "vacuity guard: core_create_cluster call not found in make_pcluster.py"
        )
        assert max(validate_lines) < min(core_call_lines), (
            "checksum validation must complete before core_create_cluster -- the "
            "only caller of _setup_iam -- is ever invoked"
        )

        with open(os.path.join(root, "src", "pcluster_core.py")) as fh:
            core_tree = ast.parse(fh.read())
        iam_lines = [
            node.lineno
            for node in ast.walk(core_tree)
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_setup_iam"
        ]
        assert iam_lines, "vacuity guard: _setup_iam call not found in src/pcluster_core.py"
        assert max(validate_lines) < min(iam_lines), (
            "checksums are validated after IAM resources are created; a bad "
            "checksum then leaves five policies and a role to clean up"
        )


class TestDeriveResultsBucket:
    """Benchmark results were synced to the per-build bucket that teardown then
    deleted with force=true, which purges objects first. Both tasks succeeded, so
    nothing was collected as orphaned and teardown printed "has been deleted" over
    a silent data loss on the default path (delete_s3_bucketname defaults to true).

    The results bucket must therefore be keyed on something that outlives a build.
    s3_bucketname is "parallelclustermaker-<cluster_serial_number>" and the serial
    embeds a timestamp, so anything derived from the cluster or the serial is a new
    bucket per build -- which is also why the documented "rebuilds of the same
    cluster name accumulate rather than overwrite" was impossible before this."""

    _ACCT = "123456789012"

    def test_the_name_is_stable_across_builds(self):
        first = _derive_results_bucket(aws_account_id=self._ACCT, region="us-east-1")
        second = _derive_results_bucket(aws_account_id=self._ACCT, region="us-east-1")
        assert first == second

    def test_the_derivation_cannot_see_the_cluster_or_the_serial(self):
        """The whole point: a per-cluster or per-serial name is a per-build bucket,
        which is the bug. Pinned on the signature rather than on the rendered name,
        because a 12-digit account ID is indistinguishable from a serial datestamp
        by inspection -- the only robust statement is that neither value is an
        input. Keyword-only for the usual reason: two same-typed string parameters
        transpose into a plausible bucket name instead of an error."""
        params = inspect.signature(_derive_results_bucket).parameters
        assert set(params) == {"aws_account_id", "region"}, (
            f"unexpected inputs {sorted(params)}; anything cluster- or "
            f"serial-derived makes this a per-build bucket again"
        )
        assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in params.values()), (
            "must be keyword-only: two string parameters transpose silently"
        )

    def test_the_name_is_never_the_per_build_bucket(self):
        """s3_bucketname is deleted on teardown by default; this one must not be it."""
        serial = "osiris-00412128072026"
        per_build = "parallelclustermaker-" + serial
        assert _derive_results_bucket(aws_account_id=self._ACCT, region="us-east-1") != per_build

    def test_the_region_is_in_the_name(self):
        """S3 bucket names are global but buckets are regional, so one name for two
        regions in the same account is a BucketAlreadyOwnedByYou in the wrong place."""
        east = _derive_results_bucket(aws_account_id=self._ACCT, region="us-east-1")
        west = _derive_results_bucket(aws_account_id=self._ACCT, region="us-west-2")
        assert east != west
        assert east.endswith("us-east-1") and west.endswith("us-west-2")

    def test_the_account_is_in_the_name(self):
        """Bucket names are globally unique across all AWS accounts, so a name
        without the account collides with every other user of this toolkit."""
        mine = _derive_results_bucket(aws_account_id=self._ACCT, region="us-east-1")
        theirs = _derive_results_bucket(aws_account_id="210987654321", region="us-east-1")
        assert mine != theirs
        assert self._ACCT in mine

    @pytest.mark.parametrize(
        "region",
        ["us-east-1", "us-west-2", "ap-southeast-4", "il-central-1", "us-gov-east-1"],
    )
    def test_every_real_region_yields_a_legal_bucket_name(self, region):
        """S3 rejects names over 63 characters, and uppercase or underscores."""
        name = _derive_results_bucket(aws_account_id=self._ACCT, region=region)
        assert 3 <= len(name) <= 63, f"{name} is {len(name)} characters"
        assert re.fullmatch(r"[a-z0-9][a-z0-9.-]*[a-z0-9]", name), name
        assert "_" not in name and name == name.lower()

    def test_an_overlong_name_aborts_rather_than_failing_at_aws(self):
        """A 63-character overrun is an opaque S3 error mid-build otherwise."""
        with pytest.raises(SystemExit):
            _derive_results_bucket(aws_account_id="1" * 40, region="ap-southeast-4")


class TestDeriveAzList:
    """The last genuinely shim-local derivation, extracted to core so an
    MCP create_cluster wrapper can build a MakeClusterParams without
    reimplementing it (round 43).

    The asymmetric fallback is the whole point of the signature: compute
    falls back to the head node's AZ, GPU falls back to None. Collapsing
    them to one internal default would give a GPU-less cluster a GPU AZ
    list, which is why the fallback is a required keyword rather than a
    default.
    """

    def test_a_single_az_parses(self):
        assert _derive_az_list("us-east-1a", fallback=["x"]) == ["us-east-1a"]

    def test_several_azs_parse_in_order(self):
        assert _derive_az_list("us-east-1a,us-east-1b", fallback=None) == [
            "us-east-1a",
            "us-east-1b",
        ]

    def test_whitespace_is_stripped(self):
        assert _derive_az_list(" us-east-1a , us-east-1b ", fallback=None) == [
            "us-east-1a",
            "us-east-1b",
        ]

    def test_a_trailing_comma_does_not_yield_an_empty_az(self):
        """'us-east-1a,' is what an operator actually types. An empty-string
        AZ would survive all the way to cluster creation before failing."""
        assert _derive_az_list("us-east-1a,", fallback=None) == ["us-east-1a"]

    def test_empty_input_returns_the_fallback(self):
        assert _derive_az_list("", fallback=["us-east-1a"]) == ["us-east-1a"]

    def test_none_input_returns_the_fallback(self):
        assert _derive_az_list(None, fallback=None) is None

    def test_an_all_separator_string_returns_the_fallback(self):
        """',,,' parses to zero AZs; returning [] would be a queue pinned
        to no AZ at all, which fails obscurely at creation. The fallback is
        the right answer."""
        assert _derive_az_list(",,,", fallback=["us-east-1a"]) == ["us-east-1a"]

    def test_the_two_fallbacks_are_genuinely_different(self):
        """Pins the asymmetry rather than the implementation: a GPU-less
        cluster must get None, not a list."""
        assert _derive_az_list("", fallback=["us-east-1a"]) == ["us-east-1a"]
        assert _derive_az_list("", fallback=None) is None

    def test_the_fallback_is_required(self):
        """A default would let a caller silently get the wrong one."""
        import inspect

        sig = inspect.signature(_derive_az_list)
        assert sig.parameters["fallback"].kind == inspect.Parameter.KEYWORD_ONLY
        assert sig.parameters["fallback"].default is inspect.Parameter.empty

    def test_both_call_sites_pass_the_right_fallback(self):
        """The asymmetry is only correct if the call sites preserve it, and
        a swap renders a plausible cluster rather than an error."""
        import ast

        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "make_pcluster.py",
        )
        with open(path) as fh:
            tree = ast.parse(fh.read())
        found = {}
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
                continue
            if getattr(node.value.func, "id", "") != "_derive_az_list":
                continue
            target = node.targets[0].id
            fb = next(k.value for k in node.value.keywords if k.arg == "fallback")
            found[target] = "list" if isinstance(fb, ast.List) else "none"
        assert found == {"compute_az_list": "list", "gpu_az_list": "none"}, found


class TestBuildMakeClusterParams:
    """The bridge that lets a non-argparse caller construct a
    MakeClusterParams (round 44). 84 fields, none with a default:
    MAKE_CLUSTER_DEFAULTS supplies 70, four are required inputs, and the
    remaining ten are derived here through the same core helpers
    make_pcluster.py's main() calls -- not reimplemented, so a change to
    any of them reaches both callers.
    """

    _REQUIRED = dict(
        cluster_name="osiris",
        cluster_owner="testuser",
        cluster_owner_email="testuser@example.com",
        az="us-east-2a",
        headnode_instance_type="c5.xlarge",
    )
    # Both instance-type defaults are "", and a cluster with neither queue
    # is refused (see TestAtLeastOneQueue), so every build here needs one.
    _QUEUE = {"compute_instance_type": "c5.2xlarge"}

    def _build(self, **kw):
        merged = dict(self._REQUIRED)
        overrides = dict(self._QUEUE)
        overrides.update(kw.pop("overrides", None) or {})
        merged.update(kw)
        merged["overrides"] = overrides
        return _build_make_cluster_params(**merged)

    def test_it_produces_a_complete_params_object(self):
        """Every field populated -- the point of the exercise, since
        MakeClusterParams has no defaults of its own."""
        import dataclasses

        params = self._build()
        for f in dataclasses.fields(params):
            getattr(params, f.name)  # must not raise

    def test_the_required_inputs_land_where_they_belong(self):
        params = self._build()
        assert params.cluster_name == "osiris"
        assert params.cluster_owner == "testuser"
        assert params.cluster_owner_email == "testuser@example.com"
        assert params.az == "us-east-2a"
        assert params.headnode_instance_type == "c5.xlarge"

    def test_headnode_instance_type_is_required(self):
        """An earlier draft defaulted it to the *login node* default --
        a plausible value from the wrong knob, which would have silently
        built head nodes sized for a login node."""
        import inspect

        sig = inspect.signature(_build_make_cluster_params)
        p = sig.parameters["headnode_instance_type"]
        assert p.default is inspect.Parameter.empty
        assert p.kind == inspect.Parameter.KEYWORD_ONLY

    def test_defaults_fill_everything_not_overridden(self):
        params = self._build()
        assert params.base_os == MAKE_CLUSTER_DEFAULTS["base_os"]
        assert params.scheduler == MAKE_CLUSTER_DEFAULTS["scheduler"]

    def test_overrides_win_over_defaults(self):
        params = self._build(overrides={"base_os": "rhel9arm", "enable_fsx": "true"})
        assert params.base_os == "rhel9arm"
        # A real bool, not the string: MakeClusterParams annotates this
        # `bool` and core_create_cluster tests it with truthiness, so the
        # string form this once asserted was the defect, not the contract.
        assert params.enable_fsx is True

    def test_an_unknown_override_is_rejected(self):
        """Silently ignoring a typo is the worst outcome: an operator who
        asked for FSx and did not get it, with no error anywhere."""
        with pytest.raises(PClusterMakerError, match="unknown cluster parameter"):
            self._build(overrides={"enable_fsxx": "true"})

    def test_the_rejection_names_the_offending_key(self):
        with pytest.raises(PClusterMakerError, match="enable_fsxx"):
            self._build(overrides={"enable_fsxx": "true"})

    def test_the_az_list_derivation_is_used(self):
        """Not reimplemented -- the same _derive_az_list the CLI calls,
        including its asymmetric fallbacks."""
        params = self._build()
        assert params.compute_az_list == ["us-east-2a"]
        assert params.gpu_az_list is None

    def test_an_explicit_compute_az_overrides_the_fallback(self):
        params = self._build(overrides={"compute_az": "us-east-2b,us-east-2c"})
        assert params.compute_az_list == ["us-east-2b", "us-east-2c"]

    def test_the_docker_compose_arch_follows_base_os(self):
        """Graviton base_os must select the aarch64 checksum; getting this
        wrong makes every node fetch a wrong-architecture binary."""
        x86 = self._build()
        arm = self._build(overrides={"base_os": "rhel9arm"})
        assert x86.docker_compose_arch == "x86_64"
        assert arm.docker_compose_arch == "aarch64"

    def test_the_checksum_matches_the_derived_arch(self):
        arm = self._build(overrides={"base_os": "rhel9arm"})
        assert (
            arm.docker_compose_checksum == MAKE_CLUSTER_DEFAULTS["docker_compose_checksum_aarch64"]
        )

    def test_the_bootstrap_timeout_is_bumped_for_shared_filesystems(self):
        """FSx provisioning runs on the head node's critical path with the
        wait-condition clock already running."""
        plain = self._build()
        fsx = self._build(overrides={"enable_fsx": "true"})
        assert fsx.head_node_bootstrap_timeout > plain.head_node_bootstrap_timeout
        assert plain.configured_head_node_bootstrap_timeout == 2100

    def test_the_loginnode_type_falls_back_by_architecture(self):
        """The architecture-aware fallback, not a flat literal: a Graviton
        default on an x86 cluster fails preflight."""
        x86 = self._build()
        arm = self._build(overrides={"base_os": "ubuntu2404arm"})
        assert x86.loginnode_instance_type != arm.loginnode_instance_type

    def test_an_explicit_loginnode_type_is_kept(self):
        params = self._build(overrides={"loginnode_instance_type": "c8g.4xlarge"})
        assert params.loginnode_instance_type == "c8g.4xlarge"


class TestAtLeastOneQueue:
    """A cluster with neither a CPU nor a GPU queue has a head node and
    nothing to run jobs on.

    Both instance-type defaults are "" and both queue flags are derived
    purely from whether their string is non-empty, so this is what a
    defaults-only cluster is. config.pcluster.j2 renders `SlurmQueues:
    None` and PCluster's schema rejects it -- but only after
    core_create_cluster has created the IAM role, S3 bucket, keypair and
    SSH secret, which the late-stage failure handler then deliberately
    preserves. A full provisioning cycle plus a manual kill_pcluster.py,
    for an input error visible before anything is spent.

    core_remove_queue already enforces the same invariant from the other
    direction ("A cluster must have at least one queue"); creation simply
    never did.
    """

    _REQ = dict(
        cluster_name="osiris",
        cluster_owner="testuser",
        cluster_owner_email="testuser@example.com",
        az="us-east-2a",
        headnode_instance_type="c5.xlarge",
    )

    def _build(self, **overrides):
        return _build_make_cluster_params(**self._REQ, overrides=overrides or None)

    def test_defaults_alone_are_rejected(self):
        """The default cluster is the broken one, which is what makes this
        worth guarding rather than assuming nobody would ask for it."""
        with pytest.raises(PClusterMakerError, match="no compute queue"):
            self._build()

    def test_a_cpu_queue_alone_is_fine(self):
        assert self._build(compute_instance_type="c5.xlarge").compute_instance_type

    def test_a_gpu_queue_alone_is_fine(self):
        """GPU-only clusters are a supported shape -- the benchmark job
        template has a whole branch for them."""
        assert self._build(gpu_instance_type="g5.xlarge").gpu_instance_type

    def test_both_together_are_fine(self):
        params = self._build(compute_instance_type="c5.xlarge", gpu_instance_type="g5.xlarge")
        assert params.compute_instance_type and params.gpu_instance_type

    def test_the_message_names_both_parameters_and_a_concrete_value(self):
        """A caller told only "no compute queue" has to go read the source
        to find out which knob to turn."""
        with pytest.raises(PClusterMakerError) as exc:
            self._build()
        text = str(exc.value)
        assert "compute_instance_type" in text
        assert "gpu_instance_type" in text
        assert "c5.xlarge" in text

    def test_the_message_says_what_it_saves(self):
        """The reason this is worth failing early rather than letting
        PCluster reject it: the expensive state is already created."""
        with pytest.raises(PClusterMakerError) as exc:
            self._build()
        assert "keypair" in str(exc.value)

    def test_the_config_template_really_renders_no_queues(self):
        """Grounds the whole guard: without it, this is what gets built."""
        import yaml

        import conftest
        from pcluster_core import render_template

        ctx = dict(conftest.cluster_params.__wrapped__())
        ctx.update(
            {
                "enable_cpu_queue": "false",
                "enable_gpu_queue": "false",
                "cpu_instance_types": [],
                "gpu_instance_types": [],
                "external_nfs_sg": {"group_id": "sg-0"},
            }
        )
        rendered = yaml.safe_load(
            render_template(
                os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates"
                ),
                "config.pcluster.j2",
                **ctx,
            )
        )
        assert rendered["Scheduling"]["SlurmQueues"] is None


class TestTheExistingVarsFileAbortNamesTheRightRemedy:
    """Two ways to reach this abort look identical from the vars file alone
    and have opposite remedies. A running cluster must be torn down; a build
    that died before launching its stack created nothing in AWS, so
    kill_pcluster.py sends the operator after a cluster that never existed.

    That is what shipped, and it was reached by a real build: the S3
    region-binding bug failed after the vars file was written, and the
    rollback removed the serial file but left the vars file -- so the next
    run hit this abort and was told to tear down a cluster that had never
    been created.

    The serial file is the discriminator: written once a build commits to a
    cluster identity, removed by the pre-launch rollback.
    """

    _ARGS = dict(
        cluster_name="osiris",
        cluster_owner="rmarable",
        az="us-east-1a",
        cluster_build_command="./make_pcluster.py -N osiris ...",
    )

    def _lines(self, tmp_path, *, serial):
        repo = tmp_path
        vf = repo / "src" / "vars_files" / "osiris.yml"
        vf.parent.mkdir(parents=True)
        vf.write_text("---\n")
        data = repo / "active_clusters" / "osiris"
        data.mkdir(parents=True)
        if serial:
            (data / "osiris.serial").write_text("123\n")
        import pcluster_core

        return pcluster_core.existing_vars_file_guidance(
            repo_root=str(repo), vars_file_path=str(vf), **self._ARGS
        )

    def test_a_serial_file_means_teardown(self, tmp_path):
        text = "\n".join(self._lines(tmp_path, serial=True))
        assert "./kill_pcluster.py -N osiris -O rmarable -A us-east-1a" in text
        assert "rm -rf" not in text

    def test_no_serial_file_means_remove_local_state(self, tmp_path):
        """The case the shipped message got wrong."""
        text = "\n".join(self._lines(tmp_path, serial=False))
        assert "rm -f src/vars_files/osiris.yml" in text
        assert "rm -rf active_clusters/osiris" in text
        assert "kill_pcluster.py" not in text, "there is no cluster to tear down on this branch"

    def test_the_vars_file_is_named_not_merely_alluded_to(self, tmp_path):
        """The original said a vars file 'was found' without saying where,
        which is the whole complaint."""
        for serial in (True, False):
            text = "\n".join(self._lines(tmp_path / str(serial), serial=serial))
            assert "src/vars_files/osiris.yml" in text

    def test_paths_are_repo_relative_not_absolute(self, tmp_path):
        """An absolute path built from tmp_path (or a developer's home) is
        not something an operator can paste."""
        text = "\n".join(self._lines(tmp_path, serial=False))
        assert str(tmp_path) not in text

    def test_both_branches_offer_the_rebuild_command(self, tmp_path):
        for serial in (True, False):
            text = "\n".join(self._lines(tmp_path / f"r{serial}", serial=serial))
            assert self._ARGS["cluster_build_command"] in text

    def test_the_two_branches_are_distinguishable(self, tmp_path):
        """Vacuity guard: one hedged message mentioning both remedies would
        satisfy every individually-worded assertion above."""
        a = "\n".join(self._lines(tmp_path / "a", serial=True))
        b = "\n".join(self._lines(tmp_path / "b", serial=False))
        assert a != b
        assert ("kill_pcluster.py" in a) and ("kill_pcluster.py" not in b)


class TestAFailedPreLaunchBuildLeavesNoLocalState:
    """The root cause of the abort above. The pre-launch rollback removed
    the serial file and released the lock but left the vars file and the
    rendered artifacts in active_clusters/, so every failed build armed the
    next run's abort.

    Safe to remove precisely here: the vars-file check refuses to start when
    either already exists, so anything present at rollback was written by
    this build, and no stack was ever launched.
    """

    def test_the_rollback_removes_the_vars_file_and_the_data_dir(self):
        import ast

        src = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "src",
            "pcluster_core.py",
        )
        with open(src) as fh:
            tree = ast.parse(fh.read())
        fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "core_create_cluster"
        )
        # The rollback handler is the except: block that prints the
        # "Cleaning up" banner.
        bodies = [
            h
            for h in ast.walk(fn)
            if isinstance(h, ast.ExceptHandler)
            and "Cleaning up everything this build" in ast.dump(h)
        ]
        assert bodies, "the pre-launch rollback handler moved or was renamed"
        handler = bodies[0]

        # Structural, not substring: vars_file_path also appears in the
        # "Removed local state" print on the same block, so `"vars_file_path"
        # in ast.dump(...)` stays true with the os.remove deleted -- that
        # mutation survived the first version of this test.
        removed = set()
        rmtree_targets = set()
        for node in ast.walk(handler):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not isinstance(fn, ast.Attribute) or not node.args:
                continue
            target = node.args[0]
            name = target.id if isinstance(target, ast.Name) else None
            if fn.attr == "remove" and name:
                removed.add(name)
            if fn.attr == "rmtree" and name:
                rmtree_targets.add(name)

        assert "vars_file_path" in removed, (
            "rollback leaves the vars file behind, so the next build aborts "
            "on state this build created"
        )
        assert "cluster_serial_number_file" in removed, "serial removal was dropped"
        assert "cluster_data_dir" in rmtree_targets, "rollback leaves active_clusters/<name> behind"


class TestTheDefaultsFileIsAppliedWhenItExists:
    """`<cluster_name>_defaults.yml` is the operator's description of a
    cluster. It is applied automatically now, by both entry points -- the
    CLI used to require --use_defaults and merely warn that the file
    existed, and the MCP server, which has no flags to pass, could not
    honor it at all. Same cluster name, same cluster, whichever surface
    asks.

    The file sits between explicit input and MAKE_CLUSTER_DEFAULTS, which
    is the precedence `_resolve` already gave the CLI.
    """

    _REQUIRED = dict(
        cluster_name="osiris",
        cluster_owner="testuser",
        cluster_owner_email="testuser@example.com",
        az="us-east-2a",
        headnode_instance_type="c5.xlarge",
    )

    def _write(self, tmp_path, monkeypatch, contents, name="osiris"):
        import yaml as _yaml

        path = tmp_path / f"{name}_defaults.yml"
        path.write_text(_yaml.safe_dump(contents))
        monkeypatch.setattr("pcluster_core._default_repo_root", lambda: str(tmp_path))
        return path

    def _build(self, **kw):
        from pcluster_core import build_make_cluster_params

        return build_make_cluster_params(**dict(self._REQUIRED, **kw))

    def test_a_value_in_the_file_reaches_the_built_cluster(self, tmp_path, monkeypatch):
        self._write(
            tmp_path,
            monkeypatch,
            {
                "compute_instance_type": "c6g.8xlarge",
                "base_os": "rhel9arm",
            },
        )
        params = self._build()
        assert params.compute_instance_type == "c6g.8xlarge"
        assert params.base_os == "rhel9arm"

    def test_the_file_beats_the_hardcoded_default(self, tmp_path, monkeypatch):
        from pcluster_core import MAKE_CLUSTER_DEFAULTS

        other = "ondemand" if MAKE_CLUSTER_DEFAULTS["cluster_type"] == "spot" else "spot"
        self._write(
            tmp_path,
            monkeypatch,
            {
                "compute_instance_type": "c5.2xlarge",
                "cluster_type": other,
            },
        )
        assert self._build().cluster_type == other

    def test_an_explicit_override_beats_the_file(self, tmp_path, monkeypatch):
        """Precedence, and the half an operator notices: a parameter typed
        at the tool must not be silently overruled by a file on disk."""
        self._write(
            tmp_path,
            monkeypatch,
            {
                "compute_instance_type": "c6g.8xlarge",
            },
        )
        params = self._build(overrides={"compute_instance_type": "c5.9xlarge"})
        assert params.compute_instance_type == "c5.9xlarge"

    def test_a_teardown_key_in_the_file_is_ignored_not_rejected(self, tmp_path, monkeypatch):
        """One file serves make_pcluster.py and kill_pcluster.py both, so
        `delete_s3_bucketname` is legitimately in there and is not a build
        parameter. Rejecting it -- which is right for a typo'd override --
        would make every real operator's file unusable. This exact key
        bounced a real preview call before the file was wired up at all.

        What makes it true is the field filter on the MakeClusterParams
        construction, not a second filter on the file: a mutation removing
        one at the merge passed this whole class, because the other still
        drops the key.
        """
        self._write(
            tmp_path,
            monkeypatch,
            {
                "compute_instance_type": "c5.2xlarge",
                "delete_s3_bucketname": "true",
            },
        )
        assert self._build().compute_instance_type == "c5.2xlarge"

    def test_a_typo_in_an_override_is_still_rejected(self, tmp_path, monkeypatch):
        """The tolerance above is scoped to the file. An override is
        something a caller just typed, and silently ignoring it builds a
        cluster that differs from the one asked for."""
        from pcluster_core import PClusterMakerError

        self._write(tmp_path, monkeypatch, {})
        with pytest.raises(PClusterMakerError, match="unknown cluster parameter"):
            self._build(
                overrides={
                    "compute_instance_type": "c5.2xlarge",
                    "enable_fsxx": "true",
                }
            )

    def test_another_clusters_file_is_not_applied(self, tmp_path, monkeypatch):
        """Discovery is keyed on the cluster name. A prefix or glob match
        would let `osiris-test` inherit `osiris`'s cluster."""
        self._write(
            tmp_path,
            monkeypatch,
            {
                "compute_instance_type": "c6g.8xlarge",
            },
            name="something-else",
        )
        params = self._build(overrides={"compute_instance_type": "c5.2xlarge"})
        assert params.compute_instance_type == "c5.2xlarge"

    def test_no_file_leaves_the_hardcoded_defaults_in_charge(self, tmp_path, monkeypatch):
        """Absence is the ordinary case, not an error -- unlike the
        --use_defaults path, which exits when the named file is missing."""
        from pcluster_core import MAKE_CLUSTER_DEFAULTS

        monkeypatch.setattr("pcluster_core._default_repo_root", lambda: str(tmp_path))
        params = self._build(overrides={"compute_instance_type": "c5.2xlarge"})
        assert params.cluster_type == MAKE_CLUSTER_DEFAULTS["cluster_type"]

    def test_a_file_that_is_not_valid_yaml_names_itself(self, tmp_path, monkeypatch):
        from pcluster_core import PClusterMakerError

        (tmp_path / "osiris_defaults.yml").write_text("compute: [unclosed\n")
        monkeypatch.setattr("pcluster_core._default_repo_root", lambda: str(tmp_path))
        with pytest.raises(PClusterMakerError, match="osiris_defaults.yml"):
            self._build(overrides={"compute_instance_type": "c5.2xlarge"})

    def test_a_key_with_no_value_falls_through_to_the_default(self, tmp_path, monkeypatch):
        """`gpu_instance_type:` with nothing after it is None once YAML
        parses it. Every reader treats a present key as an explicit
        setting, so None overwrote the default and reached a field typed
        `str` -- where `.split(",")` raises AttributeError. A bare key
        reads as "leave this alone".

        This predates the automatic load (`_resolve` returns a file's None
        unchanged on the --use_defaults path too) but was unreachable
        without the flag; auto-applying the file put it in front of every
        operator who has one.
        """
        from pcluster_core import MAKE_CLUSTER_DEFAULTS

        self._write(
            tmp_path,
            monkeypatch,
            {
                "compute_instance_type": "c5.2xlarge",
                "gpu_instance_type": None,
            },
        )
        params = self._build()
        assert params.gpu_instance_type == MAKE_CLUSTER_DEFAULTS["gpu_instance_type"]
        assert params.gpu_instance_type.split(",") == [""]

    def test_the_use_defaults_path_drops_unset_keys_too(self, tmp_path):
        """Both loaders, or the same file behaves differently depending on
        whether it was named on the command line."""
        import yaml as _yaml

        from pcluster_core import _load_defaults_file

        path = tmp_path / "named.yml"
        path.write_text(_yaml.safe_dump({"gpu_instance_type": None, "base_os": "rhel9"}))
        loaded = _load_defaults_file(str(path), str(tmp_path / "toolkit.yml"), "osiris")
        assert loaded == {"base_os": "rhel9"}


class TestTheClusterRecordStore:
    """Phase 1 of the records store: the S3 object that lets a machine
    other than the one that built a cluster see that it exists.

    The bucket and its `vars/` prefix are not new -- every
    `templates/MCPStateAccess*.json_src` has granted them since the
    Workstream 5 split, with no code behind them. The prefix name is
    therefore fixed by IAM, not chosen here.
    """

    _RECORD = {
        "cluster_name": "osiris",
        "cluster_owner": "rmarable",
        "serial": "osiris-202608221200",
        "region": "us-east-2",
        "headnode_instance_type": "c8g.large",
        "enable_loginnode": "false",
        "loginnode_instance_type": "",
        "loginnode_count": 0,
        "cpu_instance_types": ["c8g.xlarge"],
        "gpu_instance_types": [],
        "enable_cpu_queue": "true",
        "enable_gpu_queue": "false",
        "initial_cpu_queue_size": 1,
        "max_cpu_queue_size": 8,
        "initial_gpu_queue_size": 0,
        "max_gpu_queue_size": 0,
        "cluster_type": "spot",
        "deployment_date": "2026-08-22",
        "ssh_keypair": "osiris.pem",
        "ec2_keypair": "osiris-key",
        "ec2_user": "ubuntu",
        "s3_bucketname": "parallelclustermaker-osiris",
        "enable_monitoring": "false",
        # Teardown's own inputs, added once core_delete_cluster had to run
        # on a machine that did not build the cluster.
        "aws_account_id": "",
        "az": "",
        "ec2_iam_policy": "",
        "ec2_iam_role": "",
        "ec2_user_home": "",
        "ssh_secret_name": "",
        "fsx_hydration_iam_policy": "",
        "results_bucketname": "",
        "enable_external_nfs": "false",
        "enable_fsx_hydration": "false",
        "enable_hpc_benchmarks": "false",
    }

    class _S3:
        """A fake modelled on S3's own contract, not on what the callers
        happen to need.

        Written from `botocore/data/s3/*/service-2.json` rather than from
        memory, because the previous hand-written version hid two real
        defects in consecutive rounds: it discarded `Bucket` (so every
        primitive could address the wrong one, green), and its
        `put_object` returned `None` where `PutObjectOutput` carries an
        `ETag` (so the mirror marker was never written under test and
        direction detection was silently off).

        Modelled deliberately, each because production code depends on it:
          * PutObjectOutput carries ETag.
          * GetObjectOutput carries Body, ETag, ContentLength, LastModified;
            a missing key raises NoSuchKey.
          * ListObjectsV2Output **omits Contents entirely** when nothing
            matches, and carries KeyCount/IsTruncated/NextContinuationToken;
            an unknown bucket raises NoSuchBucket. `max_keys` is small so
            pagination is actually exercised rather than assumed.
          * DeleteObject succeeds on a key that is not there.
        """

        max_keys = 2  # small on purpose: one page is not a test of paging

        def __init__(self, objects=None, bucket="b"):
            self.objects = dict(objects or {})
            self.etags = {k: f'"seed-{i}"' for i, k in enumerate(self.objects)}
            self.calls = []
            self.bucket = bucket
            self._seq = 0

        # -- helpers -------------------------------------------------
        def _err(self, code, op, status):
            from botocore.exceptions import ClientError

            return ClientError(
                {
                    "Error": {"Code": code, "Message": code},
                    "ResponseMetadata": {"HTTPStatusCode": status},
                },
                op,
            )

        def _check(self, Bucket, op):
            if Bucket != self.bucket:
                raise self._err("NoSuchBucket", op, 404)

        def _new_etag(self, Key):
            self._seq += 1
            self.etags[Key] = f'"etag-{self._seq}"'
            return self.etags[Key]

        # -- operations ----------------------------------------------
        def put_object(self, Bucket=None, Key=None, Body=None, IfMatch=None, **kw):
            self._check(Bucket, "PutObject")
            self.calls.append(("put", Key))
            if IfMatch is not None:
                if Key not in self.objects:
                    # AWS returns 404 for a conditional write against a key
                    # that is not there -- not 412. Production catches only
                    # 412/409, so this surfaces as a raw ClientError rather
                    # than ClusterConfigConflict; the fake models it so that
                    # is visible rather than assumed away.
                    raise self._err("NoSuchKey", "PutObject", 404)
                if self.etags.get(Key) != IfMatch:
                    raise self._err("PreconditionFailed", "PutObject", 412)
            self.objects[Key] = Body
            return {"ETag": self._new_etag(Key)}

        def get_object(self, Bucket=None, Key=None, **kw):
            self._check(Bucket, "GetObject")
            self.calls.append(("get", Key))
            if Key not in self.objects:
                raise self._err("NoSuchKey", "GetObject", 404)
            import datetime as _dt
            import io as _io

            body = self.objects[Key]
            return {
                "Body": _io.BytesIO(body),
                "ETag": self.etags.get(Key),
                "ContentLength": len(body),
                "LastModified": _dt.datetime(2026, 8, 24, tzinfo=_dt.timezone.utc),
            }

        def delete_object(self, Bucket=None, Key=None, **kw):
            self._check(Bucket, "DeleteObject")
            self.calls.append(("delete", Key))
            self.objects.pop(Key, None)
            self.etags.pop(Key, None)
            return {}

        def list_objects_v2(self, Bucket=None, Prefix="", ContinuationToken=None, **kw):
            self._check(Bucket, "ListObjectsV2")
            self.calls.append(("list", Prefix))
            keys = sorted(k for k in self.objects if k.startswith(Prefix))
            if ContinuationToken:
                keys = [k for k in keys if k > ContinuationToken]
            page, rest = keys[: self.max_keys], keys[self.max_keys :]
            out = {
                "Name": Bucket,
                "Prefix": Prefix,
                "MaxKeys": self.max_keys,
                "KeyCount": len(page),
                "IsTruncated": bool(rest),
            }
            # S3 omits Contents entirely when nothing matches. Always
            # returning it means `resp.get("Contents") or []` is never
            # exercised on the empty path.
            if page:
                out["Contents"] = [{"Key": k} for k in page]
            if rest:
                out["NextContinuationToken"] = page[-1]
            return out

    def test_the_key_lives_under_the_prefix_the_iam_grants(self):
        """The one property nothing else can catch: a `records/` prefix
        reads perfectly and is AccessDenied on every deployed call, because
        the policies name `vars/*`."""
        import glob
        import os

        from pcluster_core import _records_key

        assert _records_key("osiris") == "vars/osiris.json"
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        policies = glob.glob(os.path.join(repo_root, "templates", "MCPStateAccess*.json_src"))
        assert policies
        for path in policies:
            assert "/vars/*" in open(path).read(), path

    def test_a_record_round_trips(self):
        from pcluster_core import get_cluster_record, put_cluster_record

        s3 = self._S3()
        put_cluster_record(s3, locks_bucketname="b", cluster_name="osiris", record=self._RECORD)
        assert get_cluster_record(s3, locks_bucketname="b", cluster_name="osiris") == self._RECORD

    def test_a_cluster_record_dataclass_is_accepted_directly(self):
        """The wire format and the in-process type stay pinned to each
        other: a second serializer is a second thing to keep in step."""
        from pcluster_core import ClusterRecord, get_cluster_record, put_cluster_record

        s3 = self._S3()
        put_cluster_record(
            s3,
            locks_bucketname="b",
            cluster_name="osiris",
            record=ClusterRecord.from_dict(self._RECORD),
        )
        assert get_cluster_record(s3, locks_bucketname="b", cluster_name="osiris") == self._RECORD

    def test_an_absent_record_is_none_not_an_error(self):
        from pcluster_core import get_cluster_record

        assert get_cluster_record(self._S3(), locks_bucketname="b", cluster_name="nope") is None

    def test_delete_is_idempotent(self):
        """A re-run teardown must not fail on a record already gone."""
        from pcluster_core import delete_cluster_record

        s3 = self._S3()
        delete_cluster_record(s3, locks_bucketname="b", cluster_name="osiris")
        delete_cluster_record(s3, locks_bucketname="b", cluster_name="osiris")

    def test_delete_removes_the_vars_file_too(self, tmp_path):
        """Both objects, not just the record.

        `put_cluster_vars_file` writes `vars/<name>.yml` beside
        `vars/<name>.json`, and this deleted only the second -- so every
        torn-down cluster left its vars file in the store. Found on the
        first real teardown after that was added: everything else was gone
        and `connector1.yml` was still there.

        Same coupling as adding policy versions without teaching teardown
        to prune them, and as the forced ECR delete. A create and its
        delete land together or the leak is one per cluster, forever.
        """
        from pcluster_core import (
            delete_cluster_record,
            put_cluster_record,
            put_cluster_vars_file,
            _records_key,
            _vars_file_key,
        )

        s3 = self._S3()
        vf = tmp_path / "osiris.yml"
        vf.write_text("cluster_name: osiris\n")
        put_cluster_record(
            s3, locks_bucketname="b", cluster_name="osiris", record={"cluster_name": "osiris"}
        )
        put_cluster_vars_file(
            s3, locks_bucketname="b", cluster_name="osiris", vars_file_path=str(vf)
        )
        for key in (_records_key("osiris"), _vars_file_key("osiris")):
            assert key in s3.objects, f"{key} was never stored"

        delete_cluster_record(s3, locks_bucketname="b", cluster_name="osiris")

        assert _records_key("osiris") not in s3.objects
        assert _vars_file_key("osiris") not in s3.objects, (
            "the stored vars file outlived the cluster; the store leaks one object per teardown"
        )

    def test_listing_returns_cluster_names_not_keys(self):
        from pcluster_core import list_cluster_records, put_cluster_record

        s3 = self._S3()
        for name in ("osiris", "iris"):
            put_cluster_record(
                s3,
                locks_bucketname="b",
                cluster_name=name,
                record=dict(self._RECORD, cluster_name=name),
            )
        s3.objects["locks/osiris.lock"] = b"{}"
        assert list_cluster_records(s3, locks_bucketname="b") == ["iris", "osiris"]

    def test_the_listing_pages_past_the_first_response(self):
        """`list_cluster_records` hand-rolls pagination rather than using a
        paginator, so the loop has to be exercised. The old fake always
        answered `IsTruncated: False`, so deleting the loop entirely
        changed nothing -- a store with more than one page of clusters
        would have silently listed only the first."""
        from pcluster_core import list_cluster_records, put_cluster_record

        s3 = self._S3()
        names = [f"cluster-{i:02d}" for i in range(7)]
        assert len(names) > s3.max_keys * 2, "must span at least three pages"
        for name in names:
            put_cluster_record(
                s3,
                locks_bucketname="b",
                cluster_name=name,
                record=dict(self._RECORD, cluster_name=name),
            )

        assert list_cluster_records(s3, locks_bucketname="b") == names

    def test_an_empty_prefix_returns_nothing_rather_than_raising(self):
        """S3 omits `Contents` entirely when nothing matches -- it does not
        return an empty list. The old fake always included the key, so
        `resp.get("Contents") or []` was never exercised on the empty path
        and indexing it directly would have passed."""
        from pcluster_core import list_cluster_records

        s3 = self._S3()
        s3.objects["locks/other.lock"] = b"{}"  # present, but not under vars/
        assert list_cluster_records(s3, locks_bucketname="b") == []

    def test_an_unknown_bucket_is_not_reported_as_an_empty_store(self):
        """`NoSuchBucket` is the pre-first-build case and must degrade to
        an empty listing, not raise -- but it must be the bucket check that
        decides that, not a swallowed error of any kind."""
        from pcluster_core import list_cluster_records

        s3 = self._S3(bucket="the-real-one")
        assert list_cluster_records(s3, locks_bucketname="a-different-one") == []

    def test_a_local_record_wins_over_a_divergent_stored_one(self, tmp_path):
        """Order is the property, and a test with only one source present
        cannot see it. On the operator's machine the vars file is
        authoritative: a stale record from another machine must never
        shadow a fresh local build."""
        import os

        from pcluster_core import _read_cluster_record, put_cluster_record

        root = tmp_path
        os.makedirs(root / "active_clusters" / "osiris")
        os.makedirs(root / "src" / "vars_files")
        (root / "src" / "vars_files" / "osiris.yml").write_text(
            "cluster_name: osiris\nregion: us-east-2\ncluster_serial_number: local-serial\n"
        )
        s3 = self._S3()
        put_cluster_record(
            s3,
            locks_bucketname="b",
            cluster_name="osiris",
            record=dict(self._RECORD, serial="stored-serial"),
        )

        rec = _read_cluster_record("osiris", str(root), s3=s3, locks_bucketname="b")

        assert rec["serial"] == "local-serial"
        assert ("get", "vars/osiris.json") not in s3.calls, (
            "the store must not even be consulted when a local record exists"
        )

    def test_the_store_answers_when_there_is_no_local_record(self, tmp_path):
        from pcluster_core import _read_cluster_record, put_cluster_record

        s3 = self._S3()
        put_cluster_record(s3, locks_bucketname="b", cluster_name="osiris", record=self._RECORD)

        rec = _read_cluster_record("osiris", str(tmp_path), s3=s3, locks_bucketname="b")

        assert rec["serial"] == "osiris-202608221200"
        assert rec["deployment_date"] == "2026-08-22"

    def test_the_two_renamed_fields_survive_a_round_trip(self, tmp_path):
        """`serial` and `deployment_date` are the only names that differ
        between the vars file and the record, and re-projecting a stored
        record would blank exactly those two while keeping everything else
        -- a record that looks right and has lost two fields.

        What makes this pass is the resolver's shape (the store path skips
        _project_vars_file entirely), not a fallback inside the projection:
        a mutation adding record-key fallbacks there changed nothing here,
        which is how they were found to be unreachable."""
        from pcluster_core import _read_cluster_record, put_cluster_record

        s3 = self._S3()
        put_cluster_record(s3, locks_bucketname="b", cluster_name="osiris", record=self._RECORD)
        rec = _read_cluster_record("osiris", str(tmp_path), s3=s3, locks_bucketname="b")
        assert rec["serial"] and rec["deployment_date"]

    def test_a_stored_record_is_sanitized_like_a_local_one(self, tmp_path):
        """The sanitizer stays at the single read point. An S3 object is
        more exposed to a corrupted write than a local file, not less, and
        an escape sequence reaching list_pcluster.py's output is what
        _safe exists to stop."""
        from pcluster_core import _read_cluster_record, put_cluster_record

        s3 = self._S3()
        put_cluster_record(
            s3,
            locks_bucketname="b",
            cluster_name="osiris",
            record=dict(self._RECORD, cluster_owner="rmarable\n\x1b[31mEVIL"),
        )

        rec = _read_cluster_record("osiris", str(tmp_path), s3=s3, locks_bucketname="b")

        assert "\n" not in rec["cluster_owner"]
        assert "\x1b" not in rec["cluster_owner"]

    def test_without_a_client_nothing_changes(self, tmp_path):
        """The CLI's behavior when the store is unreachable, not yet
        created, or simply not wired in must be exactly what it was."""
        from pcluster_core import _read_cluster_record

        assert _read_cluster_record("osiris", str(tmp_path)) is None

    def test_the_record_is_deleted_only_on_a_confirmed_delete(self):
        """Same rule as the four credential steps, and for the same
        reason one step removed: a wait timeout is neither confirmed nor
        DELETE_FAILED, and deleting the record then hides a cluster that
        may still be running and billing from every other machine."""
        from pcluster_core import delete_cluster_record_step, put_cluster_record

        s3 = self._S3()
        put_cluster_record(s3, locks_bucketname="b", cluster_name="osiris", record=self._RECORD)

        skipped = delete_cluster_record_step(
            s3,
            cf_delete_confirmed=False,
            locks_bucketname="b",
            cluster_name="osiris",
        )

        assert skipped.succeeded
        assert "not confirmed" in skipped.detail
        assert "vars/osiris.json" in s3.objects, "the record must survive"

        done = delete_cluster_record_step(
            s3,
            cf_delete_confirmed=True,
            locks_bucketname="b",
            cluster_name="osiris",
        )

        assert done.succeeded and not done.detail
        assert "vars/osiris.json" not in s3.objects

    def test_a_failed_delete_is_reported_as_a_step_failure(self):
        """It has to reach _collect_orphaned_resources like every other
        cleanup step -- a record left behind is a resource left behind."""
        from botocore.exceptions import ClientError

        from pcluster_core import delete_cluster_record_step

        class _Denied(self._S3):
            def delete_object(self, **kw):
                raise ClientError(
                    {"Error": {"Code": "AccessDenied", "Message": "nope"}},
                    "DeleteObject",
                )

        result = delete_cluster_record_step(
            _Denied(),
            cf_delete_confirmed=True,
            locks_bucketname="b",
            cluster_name="osiris",
        )

        assert not result.succeeded
        assert "AccessDenied" in result.detail

    def test_publishing_never_fails_the_build(self, tmp_path, capsys):
        """The cluster exists and is billing by the time this runs. A
        store the operator cannot write is a discoverability problem, not
        a reason to abandon a live cluster."""
        import os

        from botocore.exceptions import ClientError

        from pcluster_core import _publish_cluster_record

        root = tmp_path
        os.makedirs(root / "active_clusters" / "osiris")
        os.makedirs(root / "src" / "vars_files")
        (root / "src" / "vars_files" / "osiris.yml").write_text(
            "cluster_name: osiris\nregion: us-east-2\n"
        )

        class _Denied(self._S3):
            def put_object(self, **kw):
                raise ClientError(
                    {"Error": {"Code": "AccessDenied", "Message": "nope"}},
                    "PutObject",
                )

        assert (
            _publish_cluster_record(
                _Denied(),
                locks_bucketname="b",
                cluster_name="osiris",
                repo_root=str(root),
            )
            is False
        )
        out = capsys.readouterr().out
        assert "WARNING" in out
        assert "The cluster is fine" in out

    def test_what_is_published_is_what_the_readers_read(self, tmp_path):
        """The stored projection comes from _read_cluster_record itself, so
        there is no second projection to drift from the one every consumer
        already goes through."""
        import os

        from pcluster_core import _publish_cluster_record, get_cluster_record

        root = tmp_path
        os.makedirs(root / "active_clusters" / "osiris")
        os.makedirs(root / "src" / "vars_files")
        (root / "src" / "vars_files" / "osiris.yml").write_text(
            "cluster_name: osiris\nregion: us-east-2\n"
            "cluster_serial_number: osiris-42\nDEPLOYMENT_DATE: 2026-08-22\n"
        )
        s3 = self._S3()

        assert _publish_cluster_record(
            s3, locks_bucketname="b", cluster_name="osiris", repo_root=str(root)
        )

        stored = get_cluster_record(s3, locks_bucketname="b", cluster_name="osiris")
        assert stored["serial"] == "osiris-42"
        assert stored["deployment_date"] == "2026-08-22"
        assert stored["region"] == "us-east-2"


class TestTheClusterConfigStore:
    """Phase 2: the `configs/` prefix, the other half of the bucket's IAM
    that had no code behind it.

    Unlike the record, a config is *edited* -- by add_queue and
    remove_queue, which take no cluster lock because they are edits rather
    than cluster mutations. That makes concurrent edits an ordinary
    read-modify-write race, and the conditional write is what turns a lost
    edit into an error.
    """

    _CONFIG = "Region: us-east-2\nImage:\n  Os: ubuntu2404\n"

    # One fake, modelled on the service contract in
    # TestTheClusterRecordStore._S3. The ETag and IfMatch behaviour this
    # class needs is S3's behaviour, not a test-local embellishment, so
    # there is nothing to subclass for.
    _S3 = TestTheClusterRecordStore._S3

    def _seeded(self):
        from pcluster_core import put_cluster_config_object

        s3 = self._S3()
        put_cluster_config_object(
            s3, locks_bucketname="b", cluster_name="osiris", text=self._CONFIG
        )
        return s3

    def test_the_key_lives_under_the_prefix_the_iam_grants(self):
        import glob
        import os

        from pcluster_core import _config_key

        assert _config_key("osiris") == "configs/osiris.yaml"
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        # A per-tier expectation table, not `if "configs/" in text`. That
        # conditional was satisfied only by the string it checked for, so
        # renaming the grant to /cfgs/* made the assertion *vanish* rather
        # than fail -- the guard neutralized itself on exactly the drift it
        # existed to catch.
        # Presence of a configs/ grant at all, per tier.
        expected = {
            "MCPStateAccessReadOnly.json_src": True,  # list_queues reads it
            "MCPStateAccessStackMutation.json_src": True,  # edit + apply + teardown
            "MCPStateAccessFleetToggle.json_src": False,  # touches no config
        }
        found = {
            os.path.basename(p): open(p).read()
            for p in glob.glob(os.path.join(repo_root, "templates", "MCPStateAccess*.json_src"))
        }
        assert set(found) == set(expected), (
            f"policy set changed: {sorted(found)} vs {sorted(expected)}"
        )
        for name, should_grant in expected.items():
            has = "/configs/*" in found[name]
            assert has is should_grant, (
                f"{name} {'lost' if should_grant else 'gained'} a configs/ grant"
            )

        # And which *actions*, which is the half that moved. add_queue and
        # remove_queue write configs/<name>.yaml; a tier named read-only
        # carrying s3:PutObject misrepresents itself to whoever reads the
        # policy next, so the tools moved to stack-mutation and the write
        # grant moved with them. read-only keeps GetObject for list_queues.
        import json as _json

        def _config_actions(name):
            for st in _json.loads(found[name])["Statement"]:
                if "/configs/*" in _json.dumps(st.get("Resource", "")):
                    a = st["Action"]
                    return set(a if isinstance(a, list) else [a])
            return set()

        assert _config_actions("MCPStateAccessReadOnly.json_src") == {"s3:GetObject"}, (
            "the read-only tier must not write cluster configs"
        )
        assert _config_actions("MCPStateAccessStackMutation.json_src") == {
            "s3:GetObject",
            "s3:PutObject",
            "s3:DeleteObject",
        }, "the tier that owns config edits needs the whole lifecycle"

    def test_a_losing_edit_is_refused_not_swallowed(self, tmp_path):
        """The whole reason for the ETag. Both callers read the same
        config, both add a queue; without IfMatch the second write wins and
        the first queue is gone with nothing raised anywhere."""
        from pcluster_core import ClusterConfigConflict, _load_cluster_config, _save_cluster_config

        s3 = self._seeded()
        first, _, etag_a = _load_cluster_config(
            "osiris", str(tmp_path), s3=s3, locks_bucketname="b"
        )
        second, _, etag_b = _load_cluster_config(
            "osiris", str(tmp_path), s3=s3, locks_bucketname="b"
        )
        assert etag_a == etag_b

        first["Region"] = "us-west-2"
        _save_cluster_config(
            first,
            config_path=None,
            etag=etag_a,
            s3=s3,
            locks_bucketname="b",
            cluster_name="osiris",
        )

        second["Region"] = "eu-west-1"
        with pytest.raises(ClusterConfigConflict, match="Re-read"):
            _save_cluster_config(
                second,
                config_path=None,
                etag=etag_b,
                s3=s3,
                locks_bucketname="b",
                cluster_name="osiris",
            )

        winner, _, _ = _load_cluster_config("osiris", str(tmp_path), s3=s3, locks_bucketname="b")
        assert winner["Region"] == "us-west-2", "the first edit must survive"

    @pytest.mark.parametrize(
        "code,status",
        [
            ("PreconditionFailed", 412),
            ("ConditionalRequestConflict", 409),
        ],
    )
    def test_both_rejection_shapes_are_a_conflict(self, code, status):
        """S3 reports a same-instant race as 409 rather than 412 and its own
        documentation says to treat them identically. Handling only 412
        crashed a build under contention once already."""
        from botocore.exceptions import ClientError

        from pcluster_core import ClusterConfigConflict, put_cluster_config_object

        class _Rejects(self._S3):
            def put_object(self, **kw):
                raise ClientError(
                    {
                        "Error": {"Code": code, "Message": "x"},
                        "ResponseMetadata": {"HTTPStatusCode": status},
                    },
                    "PutObject",
                )

        with pytest.raises(ClusterConfigConflict):
            put_cluster_config_object(
                _Rejects(),
                locks_bucketname="b",
                cluster_name="osiris",
                text="Region: x\n",
                etag='"stale"',
            )

    def _local(self, tmp_path, text):
        import os

        os.makedirs(tmp_path / "active_clusters" / "osiris", exist_ok=True)
        path = tmp_path / "active_clusters" / "osiris" / "config.osiris"
        path.write_text(text)
        return path

    def test_a_local_config_wins_for_reading(self, tmp_path):
        from pcluster_core import _load_cluster_config

        self._local(tmp_path, "Region: us-east-1\n")
        config, path, etag = _load_cluster_config(
            "osiris", str(tmp_path), s3=self._seeded(), locks_bucketname="b"
        )
        assert config["Region"] == "us-east-1", "local must win"
        assert path and etag is None

    def test_a_local_edit_mirrors_when_the_store_agrees(self, tmp_path):
        """The ordinary case: the store holds what this machine last put
        there, so the mirror follows the local edit."""
        from pcluster_core import (
            _load_cluster_config,
            _save_cluster_config,
            get_cluster_config_object,
        )

        s3 = self._seeded()
        self._local(tmp_path, self._CONFIG)

        config, path, etag = _load_cluster_config(
            "osiris", str(tmp_path), s3=s3, locks_bucketname="b"
        )
        config["Region"] = "eu-west-1"
        _save_cluster_config(
            config,
            config_path=path,
            etag=etag,
            s3=s3,
            locks_bucketname="b",
            cluster_name="osiris",
        )

        text, _ = get_cluster_config_object(s3, locks_bucketname="b", cluster_name="osiris")
        assert "eu-west-1" in text

    def test_a_local_edit_mirrors_when_there_is_no_stored_copy_yet(self, tmp_path):
        """Vacuity guard for the staleness check: an absent stored object
        is not divergence, and refusing here would break the first edit
        after every build."""
        from pcluster_core import _save_cluster_config, get_cluster_config_object

        s3 = self._S3()
        path = self._local(tmp_path, self._CONFIG)
        _save_cluster_config(
            {"Region": "us-east-2"},
            config_path=str(path),
            etag=None,
            s3=s3,
            locks_bucketname="b",
            cluster_name="osiris",
        )
        text, _ = get_cluster_config_object(s3, locks_bucketname="b", cluster_name="osiris")
        assert text is not None

    def test_a_stale_local_copy_is_refused_rather_than_pushed_down(self, tmp_path):
        """The lost-update path the mirror used to guarantee.

        A: local edit, mirrored -> store v2. B (remote, no local file):
        reads v2, adds a queue, conditional write succeeds -> store v3. A's
        local file is still v2 and nothing refreshes it. A edits again and
        the mirror used to overwrite v3 with A's stale content -- B's queue
        gone, no exception, no warning, and then applied to the live
        cluster by the next apply_cluster_update.
        """
        from pcluster_core import (
            ClusterConfigConflict,
            _save_cluster_config,
            get_cluster_config_object,
        )

        s3 = self._seeded()  # store holds _CONFIG
        path = self._local(tmp_path, self._CONFIG)

        # B moves the store on.
        from pcluster_core import put_cluster_config_object

        _, etag = get_cluster_config_object(s3, locks_bucketname="b", cluster_name="osiris")
        put_cluster_config_object(
            s3,
            locks_bucketname="b",
            cluster_name="osiris",
            text="Region: eu-central-1\n",
            etag=etag,
        )

        # Refusal is the property; the *wording* depends on whether this
        # machine has a mirror marker, and here it has none (the store was
        # seeded directly, not published from this repo). The direction
        # cases are test_a_second_machines_write_still_refuses and
        # test_divergence_with_no_marker_claims_no_direction.
        with pytest.raises(ClusterConfigConflict):
            _save_cluster_config(
                {"Region": "us-west-1"},
                config_path=str(path),
                etag=None,
                s3=s3,
                locks_bucketname="b",
                cluster_name="osiris",
            )

        text, _ = get_cluster_config_object(s3, locks_bucketname="b", cluster_name="osiris")
        assert "eu-central-1" in text, "the other machine's edit must survive"
        assert path.read_text() == self._CONFIG, "the local file is untouched too"

    def test_a_writer_landing_after_the_staleness_check_is_still_caught(self, tmp_path):
        """What the ETag is actually for.

        The staleness check closes the "local was already behind" case, but
        it reads the store and then writes -- and another machine can land
        in that window. Without IfMatch the mirror overwrites that write
        with no error, which is why removing the etag= argument survived
        every other test in this class.
        """
        from pcluster_core import (
            ClusterConfigConflict,
            _save_cluster_config,
            get_cluster_config_object,
            put_cluster_config_object,
        )

        outer = self

        class _RacyS3(self._S3):
            """Agrees when read, then a second writer lands before our put."""

            def __init__(self):
                super().__init__()
                self.raced = False

            def get_object(self, Bucket=None, Key=None, **kw):
                out = super().get_object(Bucket=Bucket, Key=Key)
                if Key.startswith("configs/") and not self.raced:
                    self.raced = True
                    # Someone else writes between our read and our write.
                    super().put_object(
                        Bucket=Bucket,
                        Key=Key,
                        Body=b"Region: ap-south-1\n",
                    )
                    self.etags[Key] = '"etag-raced"'
                return out

        s3 = _RacyS3()
        put_cluster_config_object(
            s3, locks_bucketname="b", cluster_name="osiris", text=outer._CONFIG
        )
        path = self._local(tmp_path, outer._CONFIG)

        with pytest.raises(ClusterConfigConflict):
            _save_cluster_config(
                {"Region": "us-west-1"},
                config_path=str(path),
                etag=None,
                s3=s3,
                locks_bucketname="b",
                cluster_name="osiris",
            )

        text, _ = get_cluster_config_object(s3, locks_bucketname="b", cluster_name="osiris")
        assert "ap-south-1" in text, "the racing writer's edit must survive"
        # C2: the losing conditional put must leave the local file untouched
        # too -- the pre-check conflict branches promise "Nothing was
        # written", and writing locally before the put is confirmed left the
        # local file holding an edit the store had rejected.
        assert path.read_text() == outer._CONFIG, "the local file must be untouched on conflict"

    def test_whitespace_alone_is_not_divergence(self, tmp_path):
        """A hand-edited local file differs in whitespace from a
        semantically identical stored copy. Comparing bytes would call that
        divergence and refuse a legitimate edit, so the comparison is on
        normalized dumps."""
        from pcluster_core import _save_cluster_config

        s3 = self._seeded()
        path = self._local(tmp_path, "Region:    us-east-2\n\n\nImage:\n  Os: ubuntu2404\n")
        _save_cluster_config(
            {"Region": "us-east-2"},
            config_path=str(path),
            etag=None,
            s3=s3,
            locks_bucketname="b",
            cluster_name="osiris",
        )

    def test_apply_gets_a_path_that_exists_and_cleans_it_up(self, tmp_path, monkeypatch):
        """pcluster.lib's cluster_configuration must be a PATH -- the CLI
        model tags it "file" and the dispatcher calls read_file() on it, so
        YAML content in its place does not work."""
        import pcluster_core

        seen = {}

        def _fake_update(cluster_name, region, config_path):
            seen["path"] = config_path
            seen["existed"] = os.path.isfile(config_path)
            seen["text"] = open(config_path).read()
            return {"cluster": {"clusterStatus": "UPDATE_IN_PROGRESS"}}

        import os

        monkeypatch.setattr(pcluster_core, "_update_cluster_lib", _fake_update)
        monkeypatch.setattr(pcluster_core, "_poll_cluster_update", lambda *a, **kw: None)

        pcluster_core.core_apply_cluster_update(
            cluster_name="osiris",
            region="us-east-2",
            pcluster_bin="pcluster",
            wait=False,
            s3=self._seeded(),
            locks_bucketname="b",
        )

        assert seen["existed"], "a path that does not exist is not a config"
        assert "us-east-2" in seen["text"]
        assert not os.path.exists(seen["path"]), (
            "the temp copy must not outlive the call -- a reused container "
            "would apply it on behalf of the next caller"
        )

    def test_apply_without_a_config_anywhere_says_so(self, tmp_path):
        from pcluster_core import PClusterMakerError, core_apply_cluster_update

        with pytest.raises(PClusterMakerError, match="no stored configuration"):
            core_apply_cluster_update(
                cluster_name="osiris",
                region="us-east-2",
                pcluster_bin="pcluster",
                wait=False,
                s3=self._S3(),
                locks_bucketname="b",
            )

    def test_a_local_copy_that_is_ahead_is_mirrored_not_refused(self, tmp_path):
        """Direction matters and content alone cannot supply it.

        Before the mirror marker, "the local file differs from the store"
        was reported as "the local copy is behind" in both directions --
        including the one the un-mirrored CLI produced, where local was
        the *newer* copy. The operator was told to re-read the stale one.
        """
        from pcluster_core import (
            _publish_cluster_config,
            _save_cluster_config,
            get_cluster_config_object,
        )

        s3 = self._seeded()
        path = self._local(tmp_path, self._CONFIG)
        # This machine published it, so the marker records what it wrote.
        _publish_cluster_config(
            s3,
            locks_bucketname="b",
            cluster_name="osiris",
            repo_root=str(tmp_path),
        )
        # An edit through a path that does not mirror -- the CLI, before.
        path.write_text("Region: eu-west-1\n")

        _save_cluster_config(
            {"Region": "ap-south-1"},
            config_path=str(path),
            etag=None,
            s3=s3,
            locks_bucketname="b",
            cluster_name="osiris",
        )

        text, _ = get_cluster_config_object(s3, locks_bucketname="b", cluster_name="osiris")
        assert "ap-south-1" in text, "the local edit should have been mirrored"

    def test_the_marker_keeps_up_across_successive_edits(self, tmp_path):
        """The marker has to be rewritten on every mirror, not just at
        publish. Otherwise it goes stale after the first edit and the
        *second* local-ahead edit is misdiagnosed as another machine's
        write -- by this machine, about its own change. Not updating it
        survived every other test in this class.
        """
        from pcluster_core import (
            _publish_cluster_config,
            _save_cluster_config,
            get_cluster_config_object,
        )

        s3 = self._seeded()
        path = self._local(tmp_path, self._CONFIG)
        _publish_cluster_config(
            s3,
            locks_bucketname="b",
            cluster_name="osiris",
            repo_root=str(tmp_path),
        )

        for region in ("eu-west-1", "ap-south-1", "sa-east-1"):
            # Each round: an out-of-band local edit, then a mirror.
            path.write_text(f"Region: {region}\n")
            _save_cluster_config(
                {"Region": region},
                config_path=str(path),
                etag=None,
                s3=s3,
                locks_bucketname="b",
                cluster_name="osiris",
            )
            text, _ = get_cluster_config_object(s3, locks_bucketname="b", cluster_name="osiris")
            assert region in text, f"round {region} was not mirrored"

    def test_a_second_machines_write_still_refuses(self, tmp_path):
        """Vacuity guard for the above: recognising 'ahead' must not become
        'always mirror'. Once someone else writes, our marker no longer
        matches the store and the local copy really is behind."""
        from pcluster_core import (
            ClusterConfigConflict,
            _publish_cluster_config,
            _save_cluster_config,
            put_cluster_config_object,
        )

        s3 = self._seeded()
        path = self._local(tmp_path, self._CONFIG)
        _publish_cluster_config(
            s3,
            locks_bucketname="b",
            cluster_name="osiris",
            repo_root=str(tmp_path),
        )
        # Another machine writes after our mirror.
        put_cluster_config_object(
            s3,
            locks_bucketname="b",
            cluster_name="osiris",
            text="Region: eu-central-1\n",
        )
        path.write_text("Region: eu-west-1\n")

        with pytest.raises(ClusterConfigConflict, match="another machine"):
            _save_cluster_config(
                {"Region": "ap-south-1"},
                config_path=str(path),
                etag=None,
                s3=s3,
                locks_bucketname="b",
                cluster_name="osiris",
            )

    def test_divergence_with_no_marker_claims_no_direction(self, tmp_path):
        """A machine that has never mirrored cannot know which is newer,
        and must not pretend. The old message asserted 'the local copy is
        behind' unconditionally."""
        from pcluster_core import ClusterConfigConflict, _save_cluster_config

        s3 = self._seeded()
        path = self._local(tmp_path, "Region: eu-west-1\n")

        with pytest.raises(ClusterConfigConflict) as exc:
            _save_cluster_config(
                {"Region": "ap-south-1"},
                config_path=str(path),
                etag=None,
                s3=s3,
                locks_bucketname="b",
                cluster_name="osiris",
            )
        msg = str(exc.value)
        assert "no record of which is newer" in msg
        assert "is behind" not in msg, "must not claim a direction it cannot know"

    def test_an_unreachable_store_still_edits_locally(self, tmp_path, capsys):
        """Editing a file on this machine's disk must not depend on the
        store being reachable -- the rule _publish_cluster_record follows.
        get_cluster_config_object raises on AccessDenied, so without this
        the CLI would hard-fail for an operator with cluster permissions
        but no store permissions."""
        from botocore.exceptions import ClientError

        from pcluster_core import _save_cluster_config

        class _Denied(self._S3):
            def get_object(self, **kw):
                raise ClientError(
                    {
                        "Error": {"Code": "AccessDenied", "Message": "no"},
                        "ResponseMetadata": {"HTTPStatusCode": 403},
                    },
                    "GetObject",
                )

        path = self._local(tmp_path, self._CONFIG)
        _save_cluster_config(
            {"Region": "ap-south-1"},
            config_path=str(path),
            etag=None,
            s3=_Denied(),
            locks_bucketname="b",
            cluster_name="osiris",
        )
        assert "ap-south-1" in path.read_text(), "the local edit must land"
        assert "Shared store unreachable" in capsys.readouterr().out

    def test_a_deleted_config_is_a_conflict_not_a_raw_client_error(self):
        """S3 answers a conditional write against a missing key with
        NoSuchKey/404, not 412 -- verified against real S3, not inferred.
        `_is_conditional_write_rejection` covers 412 and 409 only, so this
        case escaped as a boto ClientError for a situation
        ClusterConfigConflict exists to describe.
        """
        from pcluster_core import ClusterConfigConflict, put_cluster_config_object

        s3 = self._S3()
        with pytest.raises(ClusterConfigConflict, match="was deleted"):
            put_cluster_config_object(
                s3,
                locks_bucketname="b",
                cluster_name="osiris",
                text="Region: us-east-2\n",
                etag='"gone"',
            )

    @pytest.mark.parametrize(
        "code,status",
        [
            ("AccessDenied", 403),
            ("InternalError", 500),
            ("SlowDown", 503),
        ],
    )
    def test_an_unrelated_error_is_not_reported_as_a_deleted_config(self, code, status):
        """The predicate has to discriminate, not just say yes. Returning
        True for everything satisfies the deleted-config test and turns an
        IAM denial into "your config was deleted" -- pointing the operator
        at cluster state for a permissions problem, the same failure
        `_s3_absence_or_raise` was written to stop."""
        from botocore.exceptions import ClientError

        from pcluster_core import ClusterConfigConflict, put_cluster_config_object

        class _Broken(self._S3):
            def put_object(self, **kw):
                raise self._err(code, "PutObject", status)

        with pytest.raises(ClientError):
            put_cluster_config_object(
                _Broken(),
                locks_bucketname="b",
                cluster_name="osiris",
                text="Region: us-east-2\n",
                etag='"whatever"',
            )

    def test_the_two_conflict_causes_read_differently(self):
        """ "Changed" and "deleted" need different remedies -- re-read
        versus re-publish -- so one message covering both would be worse
        than the raw error it replaced."""
        from pcluster_core import ClusterConfigConflict, put_cluster_config_object

        s3 = self._seeded()
        _, etag = __import__("pcluster_core").get_cluster_config_object(
            s3, locks_bucketname="b", cluster_name="osiris"
        )
        # Someone else writes: the key exists, our ETag is stale -> 412.
        put_cluster_config_object(
            s3,
            locks_bucketname="b",
            cluster_name="osiris",
            text="Region: eu-west-1\n",
        )
        with pytest.raises(ClusterConfigConflict) as changed:
            put_cluster_config_object(
                s3,
                locks_bucketname="b",
                cluster_name="osiris",
                text="Region: sa-east-1\n",
                etag=etag,
            )
        assert "changed while this edit" in str(changed.value)
        assert "deleted" not in str(changed.value)

    def test_the_lock_predicate_is_not_widened_to_cover_404(self):
        """The cluster lock shares `_is_conditional_write_rejection`, and
        there a vanished object means *nobody holds the lock* -- the
        opposite of what 412 supports. Only the config store may read a
        404 as a conflict."""
        from botocore.exceptions import ClientError

        from pcluster_core import _is_conditional_write_rejection

        gone = ClientError(
            {
                "Error": {"Code": "NoSuchKey", "Message": "gone"},
                "ResponseMetadata": {"HTTPStatusCode": 404},
            },
            "PutObject",
        )
        assert not _is_conditional_write_rejection(gone)

    def test_the_config_is_deleted_only_on_a_confirmed_delete(self):
        from pcluster_core import delete_cluster_config_step

        s3 = self._seeded()
        skipped = delete_cluster_config_step(
            s3,
            cf_delete_confirmed=False,
            locks_bucketname="b",
            cluster_name="osiris",
        )
        assert skipped.succeeded and "not confirmed" in skipped.detail
        assert "configs/osiris.yaml" in s3.objects

        done = delete_cluster_config_step(
            s3,
            cf_delete_confirmed=True,
            locks_bucketname="b",
            cluster_name="osiris",
        )
        assert done.succeeded
        assert "configs/osiris.yaml" not in s3.objects

    def test_an_explicit_path_is_used_as_given(self, monkeypatch):
        """Regression: an earlier draft fell back to the store whenever the
        supplied path did not exist on disk, which silently changed what
        this function did with a path it was handed and broke four
        TestCoreApplyClusterUpdate tests. pcluster's own error on a bad
        path is clearer than one invented here."""
        import pcluster_core

        seen = {}
        monkeypatch.setattr(
            pcluster_core,
            "_update_cluster_lib",
            lambda cluster, region, config_path: (
                seen.update(path=config_path)
                or {"cluster": {"clusterStatus": "UPDATE_IN_PROGRESS"}}
            ),
        )
        monkeypatch.setattr(pcluster_core, "_poll_cluster_update", lambda *a, **kw: None)

        pcluster_core.core_apply_cluster_update(
            cluster_name="osiris",
            config_path="/nowhere/cfg.yaml",
            region="us-east-2",
            pcluster_bin="pcluster",
            wait=False,
            s3=self._seeded(),
            locks_bucketname="b",
        )

        assert seen["path"] == "/nowhere/cfg.yaml", (
            "an explicit path must not be second-guessed against the store"
        )

    def test_every_config_reader_reaches_the_store(self, tmp_path):
        """list_queues read the config through the same loader as
        add_queue but was never handed the store, so on a machine with no
        local file one worked and the other reported the config missing.
        Asserted over the signatures rather than by calling each one: the
        next reader added is the one that will be forgotten."""
        import inspect

        import pcluster_core

        for name in (
            "core_list_queues",
            "core_add_queue",
            "core_remove_queue",
            "core_apply_cluster_update",
        ):
            params = inspect.signature(getattr(pcluster_core, name)).parameters
            assert "s3" in params and "locks_bucketname" in params, (
                f"{name} cannot reach the shared config store"
            )

    def test_list_queues_reads_a_stored_config(self, tmp_path):
        from pcluster_core import core_list_queues

        result = core_list_queues(
            cluster_name="osiris",
            repo_root=str(tmp_path),
            s3=self._seeded(),
            locks_bucketname="b",
        )
        assert result is not None


class TestEveryBoolFieldIsARealBool:
    """MAKE_CLUSTER_DEFAULTS and every defaults file carry booleans as the
    strings "true"/"false". MakeClusterParams annotates those fields `bool`
    and core_create_cluster tests them with plain truthiness -- `if
    enable_efa:`, `if enable_fsx:`, `if enable_monitoring:`. A string
    "false" in one of those slots is truthy, so a build with no features
    requested provisioned FSx, EFS, EFA, monitoring and a login-node pool,
    while preview_cluster_config reported every one of them "false" to the
    operator approving it.

    argparse handed the CLI real bools, so this only ever affected
    build_make_cluster_params -- and it was unreachable while
    create_cluster still died on params.region, which is why fixing that
    bug is what made this one live.
    """

    _REQUIRED = dict(
        cluster_name="zzz-no-defaults-file",
        cluster_owner="testuser",
        cluster_owner_email="testuser@example.com",
        az="us-east-2a",
        headnode_instance_type="c5.xlarge",
    )
    _QUEUE = {"compute_instance_type": "c5.2xlarge"}

    def _build(self, **overrides):
        from pcluster_core import build_make_cluster_params

        merged = dict(self._QUEUE)
        merged.update(overrides)
        return build_make_cluster_params(**self._REQUIRED, overrides=merged)

    def test_no_bool_annotated_field_holds_a_string(self):
        """Asserted over the annotations, not a hand-written list: a field
        added later cannot quietly miss the coercion."""
        from pcluster_core import _bool_field_names

        params = self._build()
        names = _bool_field_names()
        assert names, "the dataclass introspection found no bool fields"

        wrong = {
            n: repr(getattr(params, n)) for n in names if not isinstance(getattr(params, n), bool)
        }
        assert not wrong, f"bool-annotated fields holding non-bools: {wrong}"

    def test_a_feature_nobody_asked_for_is_off(self):
        """The behavioral half. `if enable_fsx:` is what core_create_cluster
        actually runs, so the property that matters is falsiness, not type."""
        params = self._build()
        for name in (
            "enable_fsx",
            "enable_efs",
            "enable_efa",
            "enable_monitoring",
            "enable_loginnode",
            "enable_external_nfs",
            "enable_hpc_benchmarks",
        ):
            assert not getattr(params, name), f"{name} is truthy by default"

    def test_a_feature_that_was_asked_for_is_on(self):
        """Vacuity guard: coercing everything to False would satisfy the
        test above and break every feature."""
        params = self._build(enable_efs="true")
        assert params.enable_efs is True

    def test_a_yaml_native_bool_from_a_defaults_file_is_accepted(self, tmp_path, monkeypatch):
        """`enable_efs: true` is the natural thing to write in YAML and
        parses as a real bool. The CLI's _resolve_bool has always accepted
        it; the shim must agree, or one file builds two clusters."""
        import yaml as _yaml

        (tmp_path / "zzz-no-defaults-file_defaults.yml").write_text(
            _yaml.safe_dump({"compute_instance_type": "c5.2xlarge", "enable_efs": True})
        )
        monkeypatch.setattr("pcluster_core._default_repo_root", lambda: str(tmp_path))
        from pcluster_core import build_make_cluster_params

        params = build_make_cluster_params(**self._REQUIRED)
        assert params.enable_efs is True

    def test_the_cli_and_the_shim_agree(self):
        """One coercion, not two. _resolve_bool is what argparse's path
        uses; the shim reuses it via _coerce_bool rather than restating the
        rule, since two readings of "false" is how this diverged."""
        from pcluster_core import _coerce_bool

        for raw, expected in (
            ("true", True),
            ("false", False),
            ("True", True),
            ("FALSE", False),
            (True, True),
            (False, False),
            (1, True),
            (0, False),
        ):
            assert _coerce_bool(raw) is expected, raw


class TestTheBuildPathValidatesItsOwnInputs:
    """The MCP build tools never called _validate_cluster_name -- every
    sibling tool did, but preview_cluster_config and create_cluster went
    straight into build_make_cluster_params, which fed the raw name to
    os.path.join for the defaults file and, on the create path, to makedirs
    under active_clusters/. The check now lives in the core, where the
    repo's own architecture rule says it belongs, rather than in whichever
    shim remembers.
    """

    _REQ = dict(
        cluster_owner="testuser",
        cluster_owner_email="testuser@example.com",
        az="us-east-2a",
        headnode_instance_type="c5.xlarge",
    )

    def _build(self, name):
        from pcluster_core import build_make_cluster_params

        return build_make_cluster_params(
            cluster_name=name,
            **self._REQ,
            overrides={"compute_instance_type": "c5.2xlarge"},
        )

    @pytest.mark.parametrize(
        "name",
        [
            "../../tmp/evil",
            "../osiris",
            "/etc/passwd",
            "UPPER",
            "9start",
            "trailing-",
            "double--hyphen",
            "",
            "a" * 40,
        ],
    )
    def test_a_name_that_is_not_a_cluster_name_is_refused(self, name):
        from pcluster_core import PClusterMakerError

        with pytest.raises(PClusterMakerError):
            self._build(name)

    def test_a_trailing_newline_is_refused(self):
        """`$` matches before a trailing newline and `\\Z` does not. The name
        becomes an S3 key, a directory name and a config filename, so what
        this validator accepts and what every downstream consumer accepts
        have to be the same set."""
        from pcluster_core import PClusterMakerError, _validate_cluster_name

        with pytest.raises(SystemExit):
            _validate_cluster_name("abc\n")
        with pytest.raises(PClusterMakerError):
            self._build("osiris\n")

    def test_a_real_name_still_builds(self):
        """Vacuity guard -- refusing everything would pass the above."""
        assert self._build("zzz-no-defaults-file").cluster_name == ("zzz-no-defaults-file")

    def test_the_toolkit_template_is_not_a_cluster_defaults_file(self):
        """`pcluster_defaults.yml` is tracked, sits at the repo root, and
        sets all three _REMOTE_DENIED_PARAMS. Auto-discovery matched it for
        cluster_name="pcluster", so a caller who could not pass
        post_install_script as an override could still get it applied by
        choosing that one name -- defeating the denial on the local server.
        """
        import os

        from pcluster_core import discover_defaults_file

        # The real repo root, not _default_repo_root() -- conftest's autouse
        # isolation fixture points that at an empty tmp dir, which would
        # make this guard pass for the wrong reason.
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        assert os.path.isfile(os.path.join(root, "pcluster_defaults.yml")), (
            "the template moved; this guard is now vacuous"
        )
        assert discover_defaults_file("pcluster", repo_root=root) is None

    def test_the_denied_params_are_what_made_that_collision_matter(self):
        """Pins why the exclusion exists. If the template stops setting
        these, the reasoning above needs revisiting rather than silently
        becoming decoration."""
        import os

        import yaml as _yaml

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "pcluster_defaults.yml")) as fh:
            template = _yaml.safe_load(fh)
        assert {"pre_install_script", "post_install_script", "custom_ami"} <= set(template)


class TestEveryCreateExitPublishesTheClusterState:
    """`core_create_cluster` has two exits and the record was published
    from one of them. The other is the `_KICKED_OFF` branch that every
    `wait=False` caller takes -- which is every MCP build -- so a remote
    build produced a live, billing cluster that the transport which built
    it could not see, poll, or tear down. The store's only writer was
    unreachable from the surface the store exists to serve.

    Structural, not behavioral, and deliberately so: the property is "no
    exit from this function skips the publisher", which is about the shape
    of the function rather than about any one run through it. The
    behavioral half is TestTheClusterRecordStore, which drives the
    publisher itself.
    """

    def _create_fn(self):
        import ast

        from pcluster_core import __file__ as core_file

        tree = ast.parse(open(core_file).read())
        return next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "core_create_cluster"
        )

    def test_no_successful_return_skips_the_publisher(self):
        """Every successful return must have a publish call before it. A
        failure return is exempt -- there is no cluster to publish.

        This used to look for `sys.exit(0)`. The function now returns a
        CreateClusterResult on every path (a sys.exit here killed the MCP
        server, since SystemExit is a BaseException and this tool cannot
        use the lock-translating wrapper), so the guard follows the same
        property to its new shape rather than being deleted with the
        construct it happened to match."""
        import ast

        fn = self._create_fn()
        publishes = [
            n.lineno
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_publish_cluster_state"
        ]
        successes = []
        for n_ in ast.walk(fn):
            if not (isinstance(n_, ast.Return) and isinstance(n_.value, ast.Call)):
                continue
            if getattr(n_.value.func, "id", None) != "CreateClusterResult":
                continue
            for kw in n_.value.keywords:
                if (
                    kw.arg == "success"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True
                ):
                    successes.append(n_.lineno)
        assert successes, "no successful return found -- this guard is vacuous"
        assert publishes, "core_create_cluster never publishes"

        # "some publish appears earlier in the file" is too weak: with two
        # success paths and two publishes, deleting the publish next to the
        # second one still leaves the first at a lower line number, and the
        # check passes while that path publishes nothing. Require a publish
        # among the return's own preceding siblings, which is what "this
        # path publishes" actually means.
        parents = {}
        for node in ast.walk(fn):
            for child in ast.iter_child_nodes(node):
                parents[child] = node

        def _publishes_on_this_path(ret):
            block_owner = parents.get(ret)
            for field in ("body", "orelse", "finalbody"):
                for stmts in [getattr(block_owner, field, None)] if block_owner else []:
                    if not isinstance(stmts, list) or ret not in stmts:
                        continue
                    for stmt in stmts[: stmts.index(ret)]:
                        # Deliberately NOT ast.walk: that descends into
                        # earlier sibling *blocks*, so the publish nested in
                        # the _KICKED_OFF branch counted as covering the
                        # final return on a path that never runs it -- and
                        # deleting the real one still passed. Both publishes
                        # are direct statements, so match only those.
                        if not isinstance(stmt, ast.Expr):
                            continue
                        call = stmt.value
                        if (
                            isinstance(call, ast.Call)
                            and getattr(call.func, "id", None) == "_publish_cluster_state"
                        ):
                            return True
            return False

        returns = [
            n_ for n_ in ast.walk(fn) if isinstance(n_, ast.Return) and n_.lineno in successes
        ]
        for ret in returns:
            assert _publishes_on_this_path(ret), (
                f"the success return at line {ret.lineno} does not publish the "
                f"cluster state on its own path; publishes at {publishes}"
            )

    def test_the_function_never_exits_the_process(self):
        """The defect this whole shape exists to prevent: a sys.exit
        anywhere core_create_cluster controls takes the MCP server down
        with it, because SystemExit is a BaseException and create_cluster
        cannot be wrapped in _cluster_lock's translation (it locks
        internally, so wrapping deadlocks). Observed live on 2026-08-25 --
        a *successful* build killed the server."""
        import ast

        fn = self._create_fn()
        exits = [
            n.lineno
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "exit"
            and getattr(n.func.value, "id", None) == "sys"
        ]
        assert not exits, (
            f"core_create_cluster calls sys.exit at lines {exits}; it must "
            f"return a CreateClusterResult and let the CLI shim exit"
        )

    def test_the_publisher_writes_both_halves(self):
        """A record without a config leaves every remote queue tool
        failing, which is the state the store shipped in."""
        import os
        import tempfile

        from pcluster_core import (
            _publish_cluster_state,
            get_cluster_config_object,
            get_cluster_record,
        )

        s3 = TestTheClusterConfigStore._S3()
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "active_clusters", "osiris"))
            os.makedirs(os.path.join(tmp, "src", "vars_files"))
            with open(os.path.join(tmp, "src", "vars_files", "osiris.yml"), "w") as fh:
                fh.write(
                    "cluster_name: osiris\nregion: us-east-2\ncluster_serial_number: osiris-1\n"
                )
            with open(os.path.join(tmp, "active_clusters", "osiris", "config.osiris"), "w") as fh:
                fh.write("Region: us-east-2\n")

            _publish_cluster_state(s3, locks_bucketname="b", cluster_name="osiris", repo_root=tmp)

        assert get_cluster_record(s3, locks_bucketname="b", cluster_name="osiris") is not None
        text, _ = get_cluster_config_object(s3, locks_bucketname="b", cluster_name="osiris")
        assert text and "us-east-2" in text


class TestTheSmallerReviewFindings:
    """The tail of the adversarial review: each of these is small, and each
    turns a confusing failure into a nameable one."""

    def test_a_denial_is_not_reported_as_a_missing_cluster(self):
        """`except _ClientError: return None` told the operator "no cluster
        named x is tracked here" when the fault was an IAM policy --
        pointing at cluster state for a permissions problem. Absence is
        NoSuchKey/NoSuchBucket/404; AccessDenied is not absence."""
        from botocore.exceptions import ClientError

        from pcluster_core import PClusterMakerError, get_cluster_record

        class _Denied(TestTheClusterRecordStore._S3):
            def get_object(self, **kw):
                raise ClientError(
                    {
                        "Error": {"Code": "AccessDenied", "Message": "no"},
                        "ResponseMetadata": {"HTTPStatusCode": 403},
                    },
                    "GetObject",
                )

        with pytest.raises(PClusterMakerError, match="MCPStateAccess"):
            get_cluster_record(_Denied(), locks_bucketname="b", cluster_name="osiris")

    @pytest.mark.parametrize(
        "code,status",
        [
            ("NoSuchKey", 404),
            ("NoSuchBucket", 404),
        ],
    )
    def test_a_genuine_absence_is_still_none(self, code, status):
        """Vacuity guard: raising on everything would break the ordinary
        pre-first-build case, where neither key nor bucket exists yet."""
        from botocore.exceptions import ClientError

        from pcluster_core import get_cluster_record

        class _Absent(TestTheClusterRecordStore._S3):
            def get_object(self, **kw):
                raise ClientError(
                    {
                        "Error": {"Code": code, "Message": "gone"},
                        "ResponseMetadata": {"HTTPStatusCode": status},
                    },
                    "GetObject",
                )

        assert get_cluster_record(_Absent(), locks_bucketname="b", cluster_name="osiris") is None

    def test_an_s3_uri_is_refused_as_a_config_path(self):
        """add_queue returns `s3://bucket/configs/x.yaml` when it edited the
        stored config. A model passing that straight to
        apply_cluster_update hit the "used exactly as given" branch and the
        URI went to pcluster.lib's read_file() as a filesystem path."""
        from pcluster_core import PClusterMakerError, core_apply_cluster_update

        with pytest.raises(PClusterMakerError, match="omit config_path"):
            core_apply_cluster_update(
                cluster_name="osiris",
                config_path="s3://parallelclustermaker-locks-1-2/configs/osiris.yaml",
                region="us-east-2",
                pcluster_bin="pcluster",
                wait=False,
            )

    @pytest.mark.parametrize(
        "body,expected",
        [
            ("- enable_efs: true\n", "mapping"),
            ("just a string\n", "mapping"),
        ],
    )
    def test_a_defaults_file_that_is_not_a_mapping_says_so(
        self, body, expected, tmp_path, monkeypatch
    ):
        """`_drop_unset` calls .items(); a list or scalar document raised a
        raw AttributeError. Inert while the file was only read under
        --use_defaults; read on every run now."""
        from pcluster_core import PClusterMakerError, load_cluster_defaults

        (tmp_path / "osiris_defaults.yml").write_text(body)
        monkeypatch.setattr("pcluster_core._default_repo_root", lambda: str(tmp_path))
        with pytest.raises(PClusterMakerError, match=expected):
            load_cluster_defaults("osiris")

    def test_a_region_passed_where_an_az_belongs_gets_the_right_message(self):
        """`resolve_region_from_az("us-east-1")` built a client for region
        'us-east-' and surfaced botocore's InvalidRegionError -- raised on
        the line *above* the except clause that names ValueError. The
        format check now runs first, so the operator gets the message
        _validate_az_input exists to print."""
        from pcluster_core import PClusterMakerError, resolve_region_from_az

        with pytest.raises(PClusterMakerError, match="not a valid Availability Zone"):
            resolve_region_from_az("us-east-1")
        with pytest.raises(PClusterMakerError, match="not a valid Availability Zone"):
            resolve_region_from_az("")

    def test_an_absent_az_raises_the_distinct_type(self):
        """The CLI maps this one to illegal_az_msg's exact wording, so it
        has to be distinguishable from a failed call -- and it must still
        be a PClusterMakerError so every existing handler catches it."""
        from pcluster_core import (
            AvailabilityZoneNotFound,
            PClusterMakerError,
            resolve_region_from_az,
        )

        class _Empty:
            def describe_availability_zones(self, ZoneNames=None, **kw):
                return {"AvailabilityZones": []}

        assert issubclass(AvailabilityZoneNotFound, PClusterMakerError)
        with pytest.raises(AvailabilityZoneNotFound):
            resolve_region_from_az("us-east-2a", ec2_client=_Empty())

    def test_the_cli_no_longer_carries_its_own_az_resolution(self):
        """Two copies of one resolution is how they drifted: the shared one
        gained a format check and a client inside the try, and the CLI's
        copy had neither."""
        import ast
        import os

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        tree = ast.parse(open(os.path.join(root, "make_pcluster.py")).read())
        calls = [
            n.func.attr
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        ]
        assert "describe_availability_zones" not in calls, (
            "make_pcluster.py resolves the region itself again"
        )
        names = [
            n.func.id
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        ]
        assert "resolve_region_from_az" in names


class TestAConfigEditIsRefusedMidUpdate:
    """apply_cluster_update returns when CloudFormation *accepts* the
    update -- it must, since a Lambda cannot block for the ~30 minutes an
    update takes -- so the cluster lock is released while the stack is
    still UPDATE_IN_PROGRESS. add_queue/remove_queue take no lock at all,
    so an edit lands in that window, the store moves on, the cluster
    converges on what was applied, and nothing detects the divergence.

    Only a *confirmed* UPDATE_IN_PROGRESS refuses. The precedent is
    _check_external_nfs_reachable: a check that runs from wherever the
    operator happens to be must not make an ordinary operation depend on
    AWS being reachable.
    """

    # A queue must already exist: core_add_queue derives the new queue's
    # subnets from the existing ones (_get_subnet_ids).
    _CONFIG = """\
Region: us-east-2
Image:
  Os: ubuntu2404
HeadNode:
  InstanceType: c8g.xlarge
  Networking:
    SubnetId: subnet-0abc123
Scheduling:
  Scheduler: slurm
  SlurmQueues:
    - Name: compute
      CapacityType: SPOT
      Networking:
        SubnetIds:
          - subnet-0abc123
      ComputeResources:
        - Name: compute-resource
          Instances:
            - InstanceType: c5.xlarge
          MinCount: 0
          MaxCount: 8
"""

    def _repo(self, tmp_path):
        import os

        os.makedirs(tmp_path / "active_clusters" / "osiris")
        (tmp_path / "active_clusters" / "osiris" / "config.osiris").write_text(self._CONFIG)
        return str(tmp_path)

    def _add(self, tmp_path, describe_fn):
        from pcluster_core import core_add_queue

        return core_add_queue(
            cluster_name="osiris",
            repo_root=self._repo(tmp_path),
            queue_type="cpu",
            ec2_instance_type="c5.xlarge",
            queue_name="q1",
            describe_fn=describe_fn,
        )

    def test_an_update_in_progress_refuses_the_edit(self, tmp_path):
        from pcluster_core import ClusterConfigConflict

        with pytest.raises(ClusterConfigConflict, match="UPDATE_IN_PROGRESS"):
            self._add(tmp_path, lambda n, r: {"clusterStatus": "UPDATE_IN_PROGRESS"})

    def test_the_config_is_untouched_when_the_edit_is_refused(self, tmp_path):
        """Refusing after writing would be worse than not checking."""
        import os

        from pcluster_core import ClusterConfigConflict

        root = self._repo(tmp_path)
        path = os.path.join(root, "active_clusters", "osiris", "config.osiris")
        with pytest.raises(ClusterConfigConflict):
            from pcluster_core import core_add_queue

            core_add_queue(
                cluster_name="osiris",
                repo_root=root,
                queue_type="cpu",
                ec2_instance_type="c5.xlarge",
                queue_name="q1",
                describe_fn=lambda n, r: {"clusterStatus": "UPDATE_IN_PROGRESS"},
            )
        assert open(path).read() == self._CONFIG

    def test_a_settled_cluster_still_accepts_the_edit(self, tmp_path):
        """Vacuity guard: refusing on every status would break queue
        editing entirely."""
        result = self._add(tmp_path, lambda n, r: {"clusterStatus": "UPDATE_COMPLETE"})
        assert result.queue_name == "q1"

    @pytest.mark.parametrize(
        "boom",
        [
            lambda n, r: (_ for _ in ()).throw(RuntimeError("no credentials")),
            lambda n, r: None,
            lambda n, r: {},
        ],
    )
    def test_an_unanswerable_describe_lets_the_edit_proceed(self, tmp_path, boom):
        """A describe that fails -- no credentials, no such cluster, an API
        error -- must not block editing a config file. Only a confirmed
        in-flight update does."""
        assert self._add(tmp_path, boom).queue_name == "q1"

    def test_remove_queue_is_guarded_too(self, tmp_path):
        """Both editors, or the guard is a coin flip on which one the
        caller reached for."""
        from pcluster_core import ClusterConfigConflict, core_remove_queue

        root = self._repo(tmp_path)
        with pytest.raises(ClusterConfigConflict, match="UPDATE_IN_PROGRESS"):
            core_remove_queue(
                cluster_name="osiris",
                repo_root=root,
                queue_name="compute",
                describe_fn=lambda n, r: {"clusterStatus": "UPDATE_IN_PROGRESS"},
            )


class TestFinalizeCompletesWhatTheBuildStarted:
    """`core_create_cluster(wait=False)` returns as soon as CloudFormation
    accepts the stack, so everything needing a live head node is skipped:
    the access scripts are rendered into stage_dir and lost with the
    process, the staging tree never reaches the node, the summary is never
    sent. The build's own message says to re-run once the stack completes,
    and re-running is refused -- it aborts on the vars file it wrote
    itself. So an MCP-built cluster could not be finished at all.

    Verified live on 2026-08-25 against a cluster in exactly that state.
    """

    def _staged(self, tmp_path, monkeypatch, status="CREATE_COMPLETE", describe_calls=None):
        import sys
        import types

        import pcluster_core

        (tmp_path / "active_clusters" / "certify").mkdir(parents=True)
        (tmp_path / "active_clusters" / "certify" / "certify.serial").write_text("s\n")
        (tmp_path / "src" / "vars_files").mkdir(parents=True)
        (tmp_path / "src" / "vars_files" / "certify.yml").write_text(
            "aws_account_id: '123456789012'\naz: us-east-1a\n"
        )

        calls = describe_calls if describe_calls is not None else []

        pc = types.ModuleType("pcluster.lib")

        def _describe(cluster_name, region):
            calls.append((cluster_name, region))
            return {"clusterStatus": status}

        pc.describe_cluster = _describe
        monkeypatch.setitem(sys.modules, "pcluster.lib", pc)
        monkeypatch.setattr(
            pcluster_core, "_acquire_distributed_cluster_lock", lambda *a, **k: None
        )
        monkeypatch.setattr(pcluster_core, "s3_release_cluster_lock", lambda *a, **k: None)
        monkeypatch.setattr(pcluster_core.boto3, "client", lambda *a, **k: object())
        return calls

    def _run(self, tmp_path):
        import pcluster_core

        return pcluster_core.core_finalize_cluster_build(
            cluster_name="certify",
            cluster_owner="rmarable",
            region="us-east-1",
            repo_root=str(tmp_path),
        )

    @pytest.mark.parametrize(
        "status", ["CREATE_IN_PROGRESS", "CREATE_FAILED", "ROLLBACK_COMPLETE", "DELETE_IN_PROGRESS"]
    )
    def test_it_refuses_unless_the_stack_is_complete(self, tmp_path, monkeypatch, status):
        """Every step needs a head node that answers. Running against a
        half-built stack would scp into nothing and publish a summary for a
        cluster that may never exist."""
        self._staged(tmp_path, monkeypatch, status=status)
        r = self._run(tmp_path)
        assert r.success is False and r.exit_code == 1
        assert status in r.message

    def test_a_refusal_renders_nothing(self, tmp_path, monkeypatch):
        """Refusing has to mean refusing: a half-rendered access script in
        active_clusters/ would look like a finished build."""
        self._staged(tmp_path, monkeypatch, status="CREATE_IN_PROGRESS")
        self._run(tmp_path)
        produced = list((tmp_path / "active_clusters" / "certify").iterdir())
        assert [p.name for p in produced] == ["certify.serial"]

    def test_it_cannot_wait(self, tmp_path, monkeypatch):
        """Structural, like the teardown twin: one describe, no retry loop,
        so it stays callable from anything that cannot block."""
        calls = []
        self._staged(tmp_path, monkeypatch, status="CREATE_IN_PROGRESS", describe_calls=calls)
        self._run(tmp_path)
        assert len(calls) == 1

    def test_a_missing_vars_file_is_refused(self, tmp_path, monkeypatch):
        """Only a cluster this machine built can be finalized here -- the
        vars file is the whole rendering context."""
        self._staged(tmp_path, monkeypatch)
        (tmp_path / "src" / "vars_files" / "certify.yml").unlink()
        r = self._run(tmp_path)
        assert r.success is False and "vars file" in r.message

    def test_a_missing_serial_file_is_refused(self, tmp_path, monkeypatch):
        self._staged(tmp_path, monkeypatch)
        (tmp_path / "active_clusters" / "certify" / "certify.serial").unlink()
        r = self._run(tmp_path)
        assert r.success is False and "serial file" in r.message

    def test_a_failed_describe_propagates(self, tmp_path, monkeypatch):
        """A failed AWS call is not an incomplete cluster -- the same rule
        the teardown gate follows."""
        import sys
        import types

        self._staged(tmp_path, monkeypatch)
        pc = types.ModuleType("pcluster.lib")

        def _boom(cluster_name, region):
            raise RuntimeError("ExpiredToken")

        pc.describe_cluster = _boom
        monkeypatch.setitem(sys.modules, "pcluster.lib", pc)
        with pytest.raises(RuntimeError):
            self._run(tmp_path)

    def test_the_refusal_explains_an_in_progress_stack(self, tmp_path, monkeypatch, capsys):
        self._staged(tmp_path, monkeypatch, status="CREATE_IN_PROGRESS")
        self._run(tmp_path)
        out = capsys.readouterr().out
        assert "still building" in out
        assert "head node" in out


class TestAccessDoesNotDependOnTheBuildHavingFinished:
    """`access_cluster.py` required the generated script and nothing else,
    so an MCP-built cluster -- which never gets one, because a wait=False
    build returns before the scripts are copied out of stage_dir -- could
    not be reached from the CLI at all. The message made it worse: "Make
    sure the cluster was built with ./make_pcluster.py" says the operator
    built it wrong, when they built it in a supported way, and offers a
    rebuild as the remedy.

    The script is a pure function of the vars file, so it is rendered on
    demand. Rendering rather than reimplementing: the template carries the
    SSM ProxyCommand, the plugin-absent fallback and the rc/stderr
    diagnosis, and a second copy of that in Python would drift from it.
    """

    _ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _repo(self, tmp_path):
        import shutil

        (tmp_path / "src" / "vars_files").mkdir(parents=True)
        (tmp_path / "active_clusters" / "certify").mkdir(parents=True)
        shutil.copytree(os.path.join(self._ROOT, "templates"), str(tmp_path / "templates"))
        return tmp_path

    def test_it_renders_the_script_when_the_build_did_not(self, tmp_path):
        """The whole point: no script on disk, and access still resolves."""
        import yaml

        from pcluster_core import core_ensure_generated_script

        repo = self._repo(tmp_path)
        # A real rendered vars file is the context; use the live one if this
        # machine has it, else skip -- this asserts the rendering path, not
        # the template's own contents.
        live = os.path.join(self._ROOT, "src", "vars_files")
        candidates = (
            [f for f in os.listdir(live) if f.endswith(".yml")] if os.path.isdir(live) else []
        )
        if not candidates:
            pytest.skip("no rendered vars file on this machine to render from")
        ctx = yaml.safe_load(open(os.path.join(live, candidates[0])))
        (repo / "src" / "vars_files" / "certify.yml").write_text(yaml.safe_dump(ctx))

        path = core_ensure_generated_script(
            cluster_data_root=str(repo / "active_clusters"),
            cluster_name="certify",
            repo_root=str(repo),
            template="access_cluster.j2",
            dest_name="access_cluster.certify.sh",
        )
        assert os.path.isfile(path)
        assert os.access(path, os.X_OK), "the rendered script must be executable"

    def test_an_existing_script_is_not_overwritten(self, tmp_path):
        """A build that did finish, or an operator edit, wins over a
        re-render."""
        from pcluster_core import core_ensure_generated_script

        repo = self._repo(tmp_path)
        dest = repo / "active_clusters" / "certify" / "access_cluster.certify.sh"
        dest.write_text("# hand-edited\n")
        path = core_ensure_generated_script(
            cluster_data_root=str(repo / "active_clusters"),
            cluster_name="certify",
            repo_root=str(repo),
            template="access_cluster.j2",
            dest_name="access_cluster.certify.sh",
        )
        assert open(path).read() == "# hand-edited\n"

    def test_no_vars_file_names_the_real_reason(self, tmp_path):
        """A cluster known only through the shared store keeps the 22-field
        record, not the 124-key vars file, so it cannot be rendered here --
        and the message has to say that rather than blame the build."""
        from pcluster_core import PClusterMakerError, core_ensure_generated_script

        repo = self._repo(tmp_path)
        with pytest.raises(PClusterMakerError) as exc:
            core_ensure_generated_script(
                cluster_data_root=str(repo / "active_clusters"),
                cluster_name="certify",
                repo_root=str(repo),
                template="access_cluster.j2",
                dest_name="access_cluster.certify.sh",
            )
        msg = str(exc.value)
        assert "did not build" in msg
        assert "shared record store" in msg
        assert "make_pcluster.py" not in msg, (
            "the old message blamed the build and offered a rebuild; a cluster "
            "built over MCP is not a cluster built wrongly"
        )

    def test_a_traversing_name_cannot_escape_active_clusters(self, tmp_path):
        """The message matters, not just the raise: without the guard this
        still fails, but on the *missing vars file* further down, so an
        assertion that only checks the exception type passes either way."""
        from pcluster_core import PClusterMakerError, core_ensure_generated_script

        repo = self._repo(tmp_path)
        with pytest.raises(PClusterMakerError) as exc:
            core_ensure_generated_script(
                cluster_data_root=str(repo / "active_clusters"),
                cluster_name="../escape",
                repo_root=str(repo),
                template="access_cluster.j2",
                dest_name="x.sh",
            )
        assert "escapes active_clusters/" in str(exc.value)

    def test_the_shim_actually_calls_the_renderer(self):
        """Asserted over the source: the core function can be perfect and
        access_cluster.py can simply stop calling it, which is exactly the
        state this change fixed and which no test of the core can see."""
        import ast

        tree = ast.parse(open(os.path.join(self._ROOT, "access_cluster.py")).read())
        called = {
            n.func.id
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "core_ensure_generated_script" in called, (
            "access_cluster.py must render the script on demand; without it "
            "an MCP-built cluster is unreachable from the CLI"
        )

    def test_the_tunnel_shim_calls_it_too(self):
        """Same gap on the monitoring path -- grafana_tunnel.py had the same
        hard dependency and the same misleading message."""
        import ast

        tree = ast.parse(open(os.path.join(self._ROOT, "grafana_tunnel.py")).read())
        called = {
            n.func.id
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "core_ensure_generated_script" in called

    def test_no_shim_tells_the_operator_to_rebuild(self):
        """Swept across both shims and the core: the misleading remedy must
        not come back in either the access or the tunnel path."""
        for rel in ("access_cluster.py", "grafana_tunnel.py", "src/pcluster_core.py"):
            body = open(os.path.join(self._ROOT, rel)).read()
            assert_source_is_real(body, "test_no_shim_tells_the_operator_to_rebuild")
            assert "Make sure the cluster was built" not in body, rel


class TestPclusterCallsWorkOffTheMainThread:
    """`aws-parallelcluster`'s CDK layer calls `asyncio.get_event_loop()`,
    which returns a loop on the main thread and **raises** on any other.
    FastMCP dispatches sync tools on an AnyIO worker thread, so every MCP
    `create_cluster` failed with "There is no current event loop in thread
    'AnyIO worker thread'".

    Three things hid it: the CLI runs on the main thread and never hit it;
    PCluster wrapped it in an exception whose `str()` is empty; and
    `core_create_cluster` exited rather than returned, so the caller saw
    nothing at all. Only driving the create path through a real MCP
    session exposed it.
    """

    def test_a_worker_thread_gets_a_loop(self):
        """The property itself. Asserted on a real thread, because the main
        thread already has a loop and would pass without the fix."""
        import asyncio
        import threading

        from pcluster_core import ensure_event_loop

        seen = {}

        def _worker():
            try:
                asyncio.get_event_loop()
                seen["before"] = "had one"
            except RuntimeError:
                seen["before"] = "none"
            ensure_event_loop()
            seen["after"] = type(asyncio.get_event_loop()).__name__

        t = threading.Thread(target=_worker)
        t.start()
        t.join()
        assert seen["before"] == "none", (
            "a fresh worker thread must start with no loop, or this test cannot see the fix"
        )
        assert "EventLoop" in seen["after"]

    def test_it_is_idempotent_and_safe_on_the_main_thread(self):
        import asyncio

        from pcluster_core import ensure_event_loop

        ensure_event_loop()
        first = asyncio.get_event_loop()
        ensure_event_loop()
        assert asyncio.get_event_loop() is first, "an existing loop must not be replaced"

    def test_every_pcluster_lib_import_installs_a_loop(self):
        """Any of these can be reached from a tool call, so the guard has to
        sit at each one. Asserted over the source: a site that drops it
        fails only off the main thread, which no ordinary test runs on.

        Matched on the AST, not on line adjacency. This compared
        `src[i + 1].strip()` for most of its life, which `CLAUDE.md` already
        described as an AST check -- and which a blank line or a comment
        between the import and the call defeats silently, leaving the guard
        green while the site it names is unprotected. The statement *after*
        the import in the same suite is the property; whitespace is not.
        """
        import ast
        import inspect

        import pcluster_core

        tree = ast.parse(inspect.getsource(pcluster_core))

        def _imports_pcluster_lib(node):
            return isinstance(node, ast.Import) and any(
                alias.name == "pcluster.lib" for alias in node.names
            )

        def _calls_ensure_event_loop(node):
            return (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "ensure_event_loop"
            )

        found, missing = 0, []
        for node in ast.walk(tree):
            for attr in ("body", "orelse", "finalbody"):
                suite = getattr(node, attr, None)
                if not isinstance(suite, list):
                    continue
                for i, stmt in enumerate(suite):
                    if not _imports_pcluster_lib(stmt):
                        continue
                    found += 1
                    following = suite[i + 1] if i + 1 < len(suite) else None
                    if following is None or not _calls_ensure_event_loop(following):
                        missing.append(stmt.lineno)

        assert found, "no pcluster.lib import found -- this guard is vacuous"
        assert not missing, (
            f"pcluster.lib is imported without ensure_event_loop() at line(s) "
            f"{sorted(missing)}; that call fails only on a worker thread"
        )


class TestAPclusterExceptionAlwaysSaysSomething:
    """`ParallelClusterApiException.__init__` calls `super().__init__()`
    with no arguments, so `str(exc)` is empty for **every** PCluster API
    exception -- including the validation failures that are the usual way
    a build is rejected. "Exception launching cluster: " with nothing
    after it is what a live operator saw, and what cost two build attempts
    to see through.
    """

    def _exc(self, message=None, errors=()):
        content = type(
            "C",
            (),
            {
                "message": message,
                "configuration_validation_errors": list(errors),
            },
        )()
        return type("CreateClusterBadRequestException", (Exception,), {})(), content

    def test_the_message_is_extracted_from_content(self):
        from pcluster_core import pcluster_exception_detail

        exc, content = self._exc(message="Invalid cluster configuration")
        exc.content = content
        out = pcluster_exception_detail(exc)
        assert "Invalid cluster configuration" in out
        assert out.startswith("CreateClusterBadRequestException")

    def test_validation_errors_name_the_validator(self):
        """The validator's name is the actionable part -- "RoleValidator"
        tells you where to look, the prose alone often does not."""
        from pcluster_core import pcluster_exception_detail

        err = type(
            "E", (), {"level": "ERROR", "type": "RoleValidator", "message": "role not found"}
        )()
        exc, content = self._exc(message="Invalid", errors=[err])
        exc.content = content
        out = pcluster_exception_detail(exc)
        assert "RoleValidator" in out and "role not found" in out

    def test_info_level_findings_are_not_reported_as_errors(self):
        """PCluster returns INFO entries alongside real failures; folding
        them in makes a one-line problem read as several."""
        from pcluster_core import pcluster_exception_detail

        info = type(
            "E",
            (),
            {
                "level": "INFO",
                "type": "DeletionPolicyValidator",
                "message": "storage will be deleted",
            },
        )()
        exc, content = self._exc(message="Invalid", errors=[info])
        exc.content = content
        assert "DeletionPolicyValidator" not in pcluster_exception_detail(exc)

    def test_it_never_returns_an_empty_description(self):
        """The whole point: an exception with no content and no str() must
        still produce something an operator can act on."""
        from pcluster_core import pcluster_exception_detail

        out = pcluster_exception_detail(Exception())
        assert out.strip()
        assert "Exception" in out

    def test_a_plain_exception_still_reports_its_message(self):
        from pcluster_core import pcluster_exception_detail

        out = pcluster_exception_detail(RuntimeError("boom"))
        assert "RuntimeError" in out and "boom" in out


class TestABuildFailureSaysWhatWentWrong:
    """`create_cluster` returned `exit_code: 1` and no reason.

    All three failure paths in `core_create_cluster` set
    `message="build failed; see the messages above"`, and "above" is
    stdout -- which on a Lambda is CloudWatch, where the caller cannot see
    it. A remote `create_cluster` therefore reported `success: false,
    exit_code: 1` and nothing else; diagnosing a plain AccessDenied on
    `iam:CreatePolicy` meant reading the container tier's log group by
    hand, twice.

    The CLI was never affected, which is why it survived this long: the
    operator is looking at the stdout the message points them to.
    """

    def test_the_exception_reaches_the_caller(self):
        from pcluster_core import _build_failure_message

        msg = _build_failure_message(
            "IAM role/policy setup failed",
            Exception("AccessDenied: iam:CreatePolicy on pclustermaker-cluster-boundary"),
        )
        assert "IAM role/policy setup failed" in msg
        assert "AccessDenied" in msg
        assert "iam:CreatePolicy" in msg

    def test_it_names_the_type_when_the_text_is_empty(self):
        """A bare `str()` is empty for at least one class this codebase
        already works around -- `pcluster_exception_detail` exists because
        `ParallelClusterApiException.__init__` passes no arguments to
        `super().__init__()`. A message built only from the text is then a
        sentence with nothing in it, which is the failure this replaces."""
        from pcluster_core import _build_failure_message

        class SilentBoom(Exception):
            def __init__(self):
                super().__init__()

        msg = _build_failure_message("template render failed", SilentBoom())
        assert "template render failed" in msg
        assert "SilentBoom" in msg
        assert msg.count("SilentBoom") == 1, (
            f"the class name is repeated, which reads as two errors: {msg}"
        )

    def test_it_is_truncated(self):
        """A message summarizes; the log is still printed in full."""
        from pcluster_core import _build_failure_message

        msg = _build_failure_message("x", Exception("y" * 5000))
        assert len(msg) < 900
        assert "[...]" in msg

    def test_no_failure_path_still_returns_the_generic_string(self):
        """The string that shipped, banned by name.

        Three sites carried it and a fix to one reads as done. Anchored on
        the literal so a fourth copy cannot appear either.
        """
        import io
        import os

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = io.open(os.path.join(root, "src", "pcluster_core.py"), encoding="utf-8").read()
        assert_source_is_real(src, "test_no_failure_path_still_returns_the_generic_string")
        assert "build failed; see the messages above" not in src, (
            "a build failure still tells a remote caller to read stdout it cannot see"
        )

    def test_every_create_failure_path_builds_a_real_message(self):
        """All three, not just the one that was diagnosed."""
        import ast
        import io
        import os

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = io.open(os.path.join(root, "src", "pcluster_core.py"), encoding="utf-8").read()
        tree = ast.parse(src)
        fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "core_create_cluster"
        )
        # The message may be built inline or bound to a name first -- the
        # failure paths now compute it once so the same text can be
        # published to the store *and* returned. Following the binding is
        # what keeps this a test of the property rather than of the
        # syntax: rejecting the variable form would have failed a refactor
        # that strictly improved the thing being tested.
        bound = {
            t.id
            for n in ast.walk(fn)
            if isinstance(n, ast.Assign)
            for t in n.targets
            if isinstance(t, ast.Name)
            and isinstance(n.value, ast.Call)
            and isinstance(n.value.func, ast.Name)
            and n.value.func.id == "_build_failure_message"
        }
        built = 0
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            if not (isinstance(node.func, ast.Name) and node.func.id == "CreateClusterResult"):
                continue
            for kw in node.keywords:
                if kw.arg != "message":
                    continue
                v = kw.value
                if (
                    isinstance(v, ast.Call)
                    and isinstance(v.func, ast.Name)
                    and v.func.id == "_build_failure_message"
                ):
                    built += 1
                elif isinstance(v, ast.Name) and v.id in bound:
                    built += 1
        assert built >= 3, (
            f"only {built} create-failure paths build a real message; "
            f"the others still return a constant"
        )


class TestARetryAfterTheGatewayTimeoutIsNotToldToCleanUp:
    """A remote `create_cluster` outlives API Gateway's 29s ceiling.

    Measured on a live build: 43,615 ms, of which ~5s is this toolkit's own
    work -- network resolution, IAM, bucket, keypair, secret, staging
    upload -- and ~39s is inside `pcluster create-cluster`, where the CDK
    synthesizes the template. The ceiling is already the REST maximum, so
    the caller is cut off while the Lambda runs on and the build succeeds.

    The obvious next move is to retry, and the retry lands on the
    vars-file guard. Answering "a vars file for this cluster already
    exists" is true and reads as a stale artifact from a dead run -- whose
    remedy is to clean up. That inference was drawn on a live build, and
    the only thing that prevented a teardown of a running cluster was
    `finalize_cluster_teardown` refusing while a stack existed.
    """

    def _guarded(self, monkeypatch, status, tmp_path):
        import pcluster_core as pc

        monkeypatch.setattr(pc, "_describe_cluster_status_quietly", lambda n, r: status)
        return pc

    def test_an_in_flight_build_says_so_and_says_not_to_clean_up(self):
        import pcluster_core as pc

        assert "CREATE_IN_PROGRESS" in pc._CLUSTER_BUILD_IN_FLIGHT

    def test_a_finished_stack_also_counts_as_in_flight(self):
        """`CREATE_COMPLETE` is not "safe to rebuild": the stack is there,
        this call created nothing, and the remedy is finalize or poll --
        never delete."""
        import pcluster_core as pc

        assert "CREATE_COMPLETE" in pc._CLUSTER_BUILD_IN_FLIGHT

    def test_an_update_is_not_treated_as_a_build(self):
        """A different operation with a different remedy. Widening this set
        to every *_IN_PROGRESS would make create_cluster claim ownership of
        states it knows nothing about."""
        import pcluster_core as pc

        for s in (
            "UPDATE_IN_PROGRESS",
            "DELETE_IN_PROGRESS",
            "CREATE_FAILED",
            "DELETE_FAILED",
            "ROLLBACK_COMPLETE",
        ):
            assert s not in pc._CLUSTER_BUILD_IN_FLIGHT, s

    def test_an_unreadable_status_falls_back_rather_than_failing(self, monkeypatch):
        """The lookup exists to make a refusal more informative. If it
        cannot answer, the refusal must be exactly what it was -- a create
        that errors because CloudFormation was briefly unreachable is worse
        than a generic message.

        The failure is injected rather than reached for real. Letting this
        actually import `pcluster.lib` sets a global event loop that 21
        later tests in another module then inherit and fail on -- which is
        what the first version of this test did, and it only showed up in
        the full-suite ordering, not in the file alone.
        """
        import sys
        import types

        import pcluster_core as pc

        stub = types.ModuleType("pcluster.lib")
        stub.describe_cluster = lambda **kw: (_ for _ in ()).throw(
            RuntimeError("CloudFormation unreachable")
        )
        monkeypatch.setitem(sys.modules, "pcluster.lib", stub)

        assert pc._describe_cluster_status_quietly("nope", "us-east-1") == ""

    def test_the_refusal_checks_the_status_before_the_generic_guidance(self):
        """Order is the whole fix. The guidance prints and returns, so a
        status check placed after it never runs."""
        import ast
        import io
        import os

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = io.open(os.path.join(root, "src", "pcluster_core.py"), encoding="utf-8").read()
        tree = ast.parse(src)
        fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "core_create_cluster"
        )
        status_at = [
            n.lineno
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_describe_cluster_status_quietly"
        ]
        guidance_at = [
            n.lineno
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "existing_vars_file_guidance"
        ]
        assert status_at, "the in-flight check is gone"
        assert guidance_at, "the guidance call is gone"
        assert min(status_at) < min(guidance_at), (
            "the status check runs after the guidance returns, so it never runs at all"
        )


class TestAFailedBuildLeavesARecordTheCallerCanRead:
    """The message was already good; nobody received it.

    `_build_failure_message` names the exception type and uses
    `pcluster_exception_detail`, and every `CreateClusterResult` carries a
    message -- verified by AST, not by eye. But a remote `create_cluster`
    measures 43.6s against API Gateway's 29s integration timeout, which is
    already the REST maximum, so the caller is disconnected roughly
    fourteen seconds before the return value exists. A better sentence
    returned into a closed socket is still nothing.

    So the outcome is written somewhere durable instead. These tests pin
    the properties that make that worth relying on.
    """

    _S3 = TestTheClusterRecordStore._S3

    def test_the_key_sits_under_the_prefix_iam_already_grants(self):
        """`vars/`, not `builds/`. `MCPStateAccess*` grants `vars/*` and
        `configs/*` and nothing else, so a new prefix reads perfectly in a
        checkout and is AccessDenied on every deployed call -- a mistake
        this repo has already made once, which is why the vars file rides
        beside the record rather than anywhere tidier."""
        from pcluster_core import _build_failure_key

        key = _build_failure_key("osiris")
        assert key.startswith("vars/"), key
        assert "osiris" in key

    def test_every_post_lock_failure_path_records_itself(self):
        """The structural one. A failure path that returns without
        publishing is invisible again, and it is invisible in exactly the
        way that cost two rounds of diagnosis -- so this walks the AST
        rather than trusting that the four sites stay wired.

        Scoped to failures after the lock is taken, which is where the
        first AWS mutation happens: before it, nothing was created and the
        call returns fast enough that the caller still holds the
        connection.
        """
        import ast
        import io
        import os

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = io.open(os.path.join(root, "src", "pcluster_core.py"), encoding="utf-8").read()
        fn = next(
            n
            for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.FunctionDef) and n.name == "core_create_cluster"
        )
        lock_line = min(
            n.lineno
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "client"
            and any(isinstance(a, ast.Constant) and a.value == "s3" for a in n.args)
        )
        failures = sorted(
            n.lineno
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "CreateClusterResult"
            and any(
                k.arg == "success" and isinstance(k.value, ast.Constant) and k.value.value is False
                for k in n.keywords
            )
            and n.lineno > lock_line
        )
        assert failures, "no post-lock failure path found -- the scan is broken"

        # Paired by *block*, not by line distance. This used to require the
        # publish within 12 lines of the return, which is a fact about
        # layout rather than about the code: reformatting spread the calls
        # apart and every post-lock path looked unrecorded. Same suite,
        # same behaviour, different whitespace.
        parents = {}
        for node in ast.walk(fn):
            for child in ast.iter_child_nodes(node):
                parents[child] = node

        def _publishes_in_the_same_block(return_node):
            """The publish must be in the *handler* this return sits in.

            Walking all the way up to the function body would accept a
            publish anywhere in core_create_cluster, which is looser than
            the 12-line window it replaced -- caught by mutation: deleting
            one of the four publishes left every path still passing. So the
            walk stops at the first except/if that encloses the return.
            """
            # Stops at core_create_cluster itself, and counts a nested
            # helper's own body: _fail_after_launch publishes and returns as
            # siblings there, inside no branch at all, so a walk that halted
            # at the first FunctionDef reported it as unrecorded.
            node = parents.get(return_node)
            while node is not None and node is not fn:
                if isinstance(node, (ast.ExceptHandler, ast.If, ast.FunctionDef)) and any(
                    isinstance(c, ast.Call)
                    and isinstance(c.func, ast.Name)
                    and c.func.id == "_publish_build_failure"
                    for stmt in node.body
                    for c in ast.walk(stmt)
                ):
                    return True
                node = parents.get(node)
            return False

        failure_nodes = [
            n
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "CreateClusterResult"
            and any(
                k.arg == "success" and isinstance(k.value, ast.Constant) and k.value.value is False
                for k in n.keywords
            )
            and n.lineno > lock_line
        ]
        for node in failure_nodes:
            assert _publishes_in_the_same_block(node), (
                f"the failure path returning at line {node.lineno} publishes no "
                f"build-failure record in its own block, so a caller cut off "
                f"at 29s has no way to learn why"
            )

    def test_recording_the_failure_can_never_replace_the_failure(self):
        """Best-effort, and that is load-bearing rather than lax. The
        caller is already on an error path and the printed output still
        reaches CloudWatch; raising here would swap a real diagnosis for a
        bookkeeping error. Same rule as the teardown poller's SNS notify.
        """
        from pcluster_core import _publish_build_failure

        class _Boom:
            def put_object(self, **kw):
                raise RuntimeError("store is on fire")

        ok = _publish_build_failure(
            _Boom(),
            locks_bucketname="b",
            cluster_name="osiris",
            region="us-east-1",
            cluster_owner="rmarable",
            stage="IAM setup",
            message="AccessDenied",
        )
        assert ok is False

    def test_no_store_is_a_no_op_rather_than_a_crash(self):
        from pcluster_core import _publish_build_failure

        assert (
            _publish_build_failure(
                None,
                locks_bucketname="b",
                cluster_name="osiris",
                region="us-east-1",
                cluster_owner="rmarable",
                stage="IAM setup",
                message="x",
            )
            is False
        )

    def test_what_is_stored_is_what_is_read_back(self):
        from pcluster_core import (
            _publish_build_failure,
            get_build_failure,
            delete_build_failure,
        )

        s3 = self._S3()
        _publish_build_failure(
            s3,
            locks_bucketname="b",
            cluster_name="osiris",
            region="us-east-1",
            cluster_owner="rmarable",
            stage="IAM setup",
            message="AccessDenied: iam:CreatePolicy on pclustermaker-policy-*",
            serial="osiris-202608280001",
        )
        rec = get_build_failure(s3, locks_bucketname="b", cluster_name="osiris")
        assert rec["stage"] == "IAM setup"
        assert "AccessDenied" in rec["message"]
        assert rec["cluster_serial_number"] == "osiris-202608280001"
        assert rec["region"] == "us-east-1"
        assert rec["failed_at"]

        delete_build_failure(s3, locks_bucketname="b", cluster_name="osiris")
        assert get_build_failure(s3, locks_bucketname="b", cluster_name="osiris") is None

    def test_deleting_an_absent_record_is_not_an_error(self):
        """Called from the success path without a preceding read, so it has
        to be safe on a key that was never written -- which is the vast
        majority of builds."""
        from pcluster_core import delete_build_failure

        delete_build_failure(self._S3(), locks_bucketname="b", cluster_name="never-failed")

    def test_a_successful_build_clears_a_stale_record(self):
        """Left behind, a previous attempt's reason answers the next
        'why did my build fail?' -- with the wrong answer, confidently."""
        import ast
        import io
        import os

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = io.open(os.path.join(root, "src", "pcluster_core.py"), encoding="utf-8").read()
        fn = next(
            n
            for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.FunctionDef) and n.name == "_publish_cluster_state"
        )
        called = {
            n.func.id
            for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "delete_build_failure" in called, (
            "the one publisher every create-path success goes through does "
            "not clear a stale build-failure record"
        )

    def test_teardown_clears_it_too(self):
        import ast
        import io
        import os

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = io.open(os.path.join(root, "src", "pcluster_core.py"), encoding="utf-8").read()
        fn = next(
            n
            for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.FunctionDef) and n.name == "delete_cluster_record_step"
        )
        called = {
            n.func.id
            for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "delete_build_failure" in called


class TestGetBuildStatusAnswersHonestly:
    """The read side. Its whole value is that a caller can trust the
    answer, so the three outcomes must stay distinguishable."""

    _S3 = TestTheClusterRecordStore._S3

    def test_an_unreachable_store_is_never_reported_as_success(self):
        """The one that matters. Absence of evidence is not evidence of
        success: reporting `failed: false` for a store we could not read
        would be exactly the false reassurance this tool exists to
        replace."""
        from pcluster_core import core_get_build_status

        out = core_get_build_status("osiris", s3=None, locks_bucketname=None)
        assert out["failed"] is False
        assert out["store_reachable"] is False
        assert "not reachable" in out["detail"]

    def test_a_reachable_store_with_no_record_says_so_differently(self):
        from pcluster_core import core_get_build_status

        out = core_get_build_status("osiris", s3=self._S3(), locks_bucketname="b")
        assert out["failed"] is False
        assert out["store_reachable"] is True
        assert "list_clusters" in out["detail"]

    def test_a_recorded_failure_is_reported_with_its_stage(self):
        from pcluster_core import _publish_build_failure, core_get_build_status

        s3 = self._S3()
        _publish_build_failure(
            s3,
            locks_bucketname="b",
            cluster_name="osiris",
            region="us-east-1",
            cluster_owner="rmarable",
            stage="cluster launch",
            message="Exception launching cluster: boom",
        )
        out = core_get_build_status("osiris", s3=s3, locks_bucketname="b")
        assert out["failed"] is True
        assert out["store_reachable"] is True
        assert out["stage"] == "cluster launch"
        assert "boom" in out["message"]

    def test_it_validates_the_cluster_name(self):
        """The name becomes an S3 key. Validation lives in the core, not
        the shim, for the same reason build_make_cluster_params does."""
        from pcluster_core import core_get_build_status

        # SystemExit specifically, verified rather than assumed: the shared
        # validators exit rather than raise, and `BaseException` here would
        # also have been satisfied by a KeyboardInterrupt or a typo in the
        # call above.
        with pytest.raises(SystemExit):
            core_get_build_status("../../etc/passwd", s3=self._S3(), locks_bucketname="b")

    def test_it_is_read_only_and_on_the_read_only_tier(self):
        from mcp_server.tiers import TOOL_TIERS

        assert TOOL_TIERS["get_build_status"] == "read-only"


class TestTheSummaryNamesTheBucketResultsActuallyGoTo:
    """The build summary and the teardown sync must name the same bucket.

    They did not. The summary told the operator results land in
    `s3://<s3_bucketname>/hpc-benchmark-results/...` while
    `_sync_performance_results_to_s3` has always written to
    `<results_bucketname>` -- and `s3_bucketname` is the per-build bucket
    teardown deletes, so the operator was directed to a bucket that stops
    existing at exactly the moment they go looking for their results.

    This is the multi-surface drift CLAUDE.md warns about ("a duration,
    path or label stated on more than one surface needs a guard that they
    agree"). It survived because the only test covering the summary's copy
    read it out of a *playbook* that no longer executes; deleting that
    playbook is what exposed it.
    """

    def _core_source(self):
        import inspect

        import pcluster_core

        return inspect.getsource(pcluster_core)

    def test_the_summary_line_uses_the_long_lived_bucket(self):
        src = self._core_source()
        line = next((ln for ln in src.splitlines() if "Results sync to s3://" in ln), None)
        assert line is not None, "the summary no longer names a results bucket"
        assert "results_bucketname" in line, line.strip()
        assert "{s3_bucketname}" not in line, (
            "the summary names the per-build bucket, which teardown deletes: " + line.strip()
        )

    def test_the_sync_itself_still_uses_it(self):
        """Vacuity guard: the fix is aligning the summary to the sync, not
        renaming both to whatever the summary happened to say."""
        src = self._core_source()
        assert "s3://{results_bucketname}/hpc-benchmark-results/" in src

    def test_the_two_surfaces_name_one_bucket(self):
        """The property, stated once. Both lines mention the prefix; neither
        may reach for the per-build bucket to do it."""
        offenders = [
            ln.strip()
            for ln in self._core_source().splitlines()
            if "hpc-benchmark-results" in ln and "{s3_bucketname}" in ln
        ]
        assert offenders == [], offenders


class TestBuildContextKeepsTheTypesItWasValidatedAs:
    """The dict was doing two incompatible jobs; this is the separation.

    `cluster_parameters` carried booleans as the strings "true"/"false",
    because that is what `vars_file.j2` compares against -- and it was also
    the only object holding the build's resolved state. Reading a flag back
    out of it therefore gave `"false"`, which is **truthy**. That is not
    hypothetical: extracting the 129-line build summary to read its inputs
    from that dict inverted every flag and failed three tests in one run.

    `BuildContext` holds the real types. `to_template_vars()` applies the
    string form at the render boundary, which is the only place it is
    correct. Anything upstream that needs a flag reads the bool.
    """

    @property
    def _ctx_cls(self):
        import pcluster_core

        return pcluster_core.BuildContext

    def test_every_flag_is_a_real_bool_on_the_context(self):
        import typing

        hints = typing.get_type_hints(self._ctx_cls)
        flags = {k for k, v in hints.items() if v is bool}
        assert flags == set(self._ctx_cls._COERCED_TO_STRINGS), (
            "the fields typed bool and the fields coerced to strings have "
            "diverged; one of them is now lying"
        )
        assert len(flags) >= 19, f"only {len(flags)} flags -- expected 19+"

    def test_the_string_form_exists_only_in_the_template_payload(self):
        """The round trip, on a real instance: bool in, "true"/"false" out."""
        import dataclasses

        cls = self._ctx_cls
        flags = set(cls._COERCED_TO_STRINGS)
        kwargs = {
            f.name: (True if f.name in flags else f"<{f.name}>") for f in dataclasses.fields(cls)
        }
        ctx = cls(**kwargs)
        for name in flags:
            assert getattr(ctx, name) is True, f"{name} is not a bool on the context"
        payload = ctx.to_template_vars()
        for name in flags:
            assert payload[name] == "true", f"{name} was not coerced for the vars file"

    def test_a_false_flag_survives_as_false(self):
        """The actual bug: `"false"` is truthy, so a flag read off the payload
        reads as enabled. On the context it stays False."""
        import dataclasses

        cls = self._ctx_cls
        flags = set(cls._COERCED_TO_STRINGS)
        kwargs = {
            f.name: (False if f.name in flags else f"<{f.name}>") for f in dataclasses.fields(cls)
        }
        ctx = cls(**kwargs)
        for name in flags:
            assert getattr(ctx, name) is False
            assert not getattr(ctx, name), f"{name} is truthy when disabled"
        payload = ctx.to_template_vars()
        for name in flags:
            assert payload[name] == "false"
            assert payload[name], (
                "vacuity check: the payload's string form IS truthy -- which "
                "is exactly why nothing upstream may read flags from it"
            )

    def test_the_context_is_frozen(self):
        """State that can be mutated mid-build is state two readers can
        disagree about."""
        import dataclasses

        import pytest

        cls = self._ctx_cls
        kwargs = {
            f.name: (False if f.name in set(cls._COERCED_TO_STRINGS) else "x")
            for f in dataclasses.fields(cls)
        }
        ctx = cls(**kwargs)
        with pytest.raises(dataclasses.FrozenInstanceError):
            ctx.cluster_name = "other"


class TestPartialProgressSurvivesForTheRollback:
    """A build that fails half-way must still clean up what it created.

    The pre-launch stage creates an SNS topic, then a bucket, keypair,
    secret and uploads. If it fails after the topic and before the rest,
    the rollback has to delete that topic -- and to do that it has to know
    the topic exists.

    That is why extracting this stage was declined twice: the value lived
    in a local, and moving the body into a function left the caller's copy
    at None, so a mid-way failure would orphan a topic on every failed
    build. `_PreLaunchProgress` is mutable for exactly this reason and
    holds only what the rollback reads.
    """

    def _progress_cls(self):
        import pcluster_core

        return pcluster_core._PreLaunchProgress

    def test_the_stage_records_the_topic_as_soon_as_it_exists(self):
        """On the AST: the assignment must be to the progress object, not to
        a local the caller cannot see."""
        import ast
        import inspect

        import pcluster_core

        src = inspect.getsource(pcluster_core._provision_pre_launch_resources)
        tree = ast.parse(src.lstrip())
        recorded = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Attribute) and t.attr == "sns_topic_arn" for t in n.targets)
        ]
        assert recorded, (
            "the SNS topic is not recorded on the progress object; a failure "
            "after it is created would orphan it"
        )

    def test_the_rollback_reads_progress_not_a_local(self):
        import inspect

        import pcluster_core

        src = inspect.getsource(pcluster_core.core_create_cluster)
        start = src.index("def _rollback_pre_launch_resources")
        body = src[start : src.index("def _fail_after_launch")]
        assert "_progress.sns_topic_arn" in body, (
            "the rollback reads a local, which is unbound after the stage "
            "moved into its own function"
        )
        # Every mention, not just one. The closure names it twice -- the
        # guard and the delete -- so a substring check passes with one of
        # them reverted, which is how a mutation slipped through once.
        import re

        bare = [m for m in re.finditer(r"(?<!_progress\.)\bsns_topic_arn\b", body)]
        assert not bare, (
            f"{len(bare)} bare `sns_topic_arn` reference(s) left in the "
            f"rollback; on a mid-way failure that name is unbound"
        )

    def test_a_half_finished_stage_leaves_the_topic_visible(self):
        """The behaviour itself, not its shape: set the topic, then raise,
        and the caller can still see what to delete."""
        cls = self._progress_cls()
        progress = cls()
        assert progress.sns_topic_arn is None

        def _stage(progress):
            progress.sns_topic_arn = "arn:aws:sns:us-east-1:1:topic"
            raise RuntimeError("upload failed after the topic was created")

        try:
            _stage(progress)
        except RuntimeError:
            pass
        assert progress.sns_topic_arn == "arn:aws:sns:us-east-1:1:topic", (
            "the caller cannot see the topic the failed stage created"
        )

    def test_it_carries_only_what_the_rollback_needs(self):
        """Vacuity guard against it becoming a second state bag. BuildContext
        is the state; this is the short list of things created mid-flight."""
        import dataclasses

        names = {f.name for f in dataclasses.fields(self._progress_cls())}
        assert names == {"sns_topic_arn"}, (
            f"_PreLaunchProgress has grown to {sorted(names)}; anything that "
            f"is not read by the rollback belongs on BuildContext or a return"
        )
