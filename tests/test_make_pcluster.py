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

import pytest
from botocore.exceptions import ClientError

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
)

from pcluster_core import (
    _b,
    _validate_az_input,
    _validate_cluster_name,
    _validate_cluster_owner,
    _resolve_ec2_user,
    _load_or_create_serial,
    _normalize_fsx_buckets,
    _check_fsx_s3,
    _storage_summary_lines,
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
            _normalize_fsx_buckets(
                "import-bucket", "export-bucket", "import/", "export/"
            )
        message = str(exc.value)
        assert "import-bucket" in message and "export-bucket" in message
        assert "same bucket" in message

    def test_export_undefined_defaults_to_the_import_bucket_and_path(self, capsys):
        out_bucket, out_path = _normalize_fsx_buckets(
            "my-bucket", "UNDEFINED", "data/", "export/"
        )
        assert out_bucket == "my-bucket"
        assert out_path == "data/", "the export path must follow the import path"
        assert "WARNING" in capsys.readouterr().out

    def test_one_bucket_with_one_path_warns_about_overwriting(self, capsys):
        """Hydration source and dehydration target are the same prefix, so exported
        files land on top of the input data. Legal, but never what someone means."""
        out_bucket, out_path = _normalize_fsx_buckets(
            "same", "same", "path/", "path/"
        )
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
            _check_fsx_s3(
                client, "typo-bucket", "output/", "export", require_objects=False
            )
        assert "export bucket s3://typo-bucket not found" in str(exc.value)

    def test_an_access_denied_export_bucket_still_fails(self):
        client = _make_s3_client(head_ok=False, head_error_code="403")
        with pytest.raises(SystemExit) as exc:
            _check_fsx_s3(
                client, "private-bucket", "output/", "export", require_objects=False
            )
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
        path that has to hold data."""
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(repo_root, "make_pcluster.py")) as fh:
            tree = ast.parse(fh.read())
        relaxed = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "id", None) != "_check_fsx_s3":
                continue
            label = node.args[3].value
            flags = [
                kw.value.value for kw in node.keywords if kw.arg == "require_objects"
            ]
            relaxed[label] = flags[0] if flags else True
        assert relaxed == {"import": True, "export": False}, relaxed


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
            _derive_head_node_bootstrap_timeout(
                configured=2100, enable_efs=False, enable_fsx=False
            )
            == 2100
        )

    def test_fsx_adds_its_allowance(self):
        assert (
            _derive_head_node_bootstrap_timeout(
                configured=2100, enable_efs=False, enable_fsx=True
            )
            == 3900
        )

    def test_efs_adds_a_smaller_allowance(self):
        """Measured on the successful osiris build of 2026-07-28: the filesystem
        completed in 4s but its mount target took 1m33s, and the head node
        instance appeared 4m24s after the wait condition started -- so the 600s
        allowance is headroom over a measured window, not an estimate."""
        assert (
            _derive_head_node_bootstrap_timeout(
                configured=2100, enable_efs=True, enable_fsx=False
            )
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
            _derive_head_node_bootstrap_timeout(
                configured=1200, enable_efs=True, enable_fsx=True
            )
            == 1200
        )
        assert (
            _derive_head_node_bootstrap_timeout(
                configured=7200, enable_efs=False, enable_fsx=True
            )
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
            _derive_head_node_bootstrap_timeout(
                configured=0, enable_efs=False, enable_fsx=False
            )
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
            and getattr(node.func, "id", None)
            == "_derive_head_node_bootstrap_timeout"
        ]
        assert calls, "make_pcluster.py never derives the bootstrap timeout"
        for call in calls:
            assert not call.args, "call site passes a positional argument"
            passed = {kw.arg for kw in call.keywords}
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
            name for name, p in params.items()
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
        names, and `f(**locals())` or a stray positional would defeat the guard."""
        import ast

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(repo_root, "make_pcluster.py")) as fh:
            tree = ast.parse(fh.read())
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "_storage_summary_lines"
        ]
        assert calls, "no _storage_summary_lines call found in make_pcluster.py"
        expected = set(_STORAGE_DEFAULTS)
        for call in calls:
            assert not call.args, "call site passes a positional argument"
            passed = {kw.arg for kw in call.keywords}
            assert None not in passed, "call site splats **kwargs instead of naming"
            assert passed == expected, (
                f"call site keywords {sorted(passed ^ expected)} do not match the "
                "function's parameters"
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
        text = "\n".join(_storage(enable_external_nfs=True,
                                 external_nfs_server="filer.corp"))
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
        text = "\n".join(_storage(enable_efs=True, enable_fsx=True,
                                 enable_external_nfs=True))
        for mount in ("/shared", "/efs", "/fsx", "/nfs"):
            assert mount in text, f"{mount} missing from a fully-loaded cluster"


_PKG_DIR_CASES = [
    ({}, "/shared/pkg"),
    ({"enable_external_nfs": True}, "/nfs/pkg"),
    ({"enable_efs": True}, "/efs/pkg"),
    ({"enable_efs": True, "enable_external_nfs": True}, "/efs/pkg"),
    ({"enable_fsx": True}, "/fsx/pkg"),
    ({"enable_fsx": True, "enable_efs": True}, "/fsx/pkg"),
    ({"enable_fsx": True, "enable_efs": True, "enable_external_nfs": True},
     "/fsx/pkg"),
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

    @pytest.mark.parametrize(
        "version", ["2.29.7", "v2.29", "v2", "latest", "", None, "v2.29.7.1"]
    )
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
        """"The checksum format is invalid" names neither the key nor the file it
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

        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "make_pcluster.py"
        )
        with open(path) as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and getattr(node.targets[0], "id", "") == "_HARDCODED_DEFAULTS"
            ):
                return ast.literal_eval(node.value)
        raise AssertionError("_HARDCODED_DEFAULTS not found in make_pcluster.py")

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

        Anchored on the IAM setup call rather than on a line number so that
        reordering unrelated code does not fail it."""
        import ast

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "make_pcluster.py")) as fh:
            src = fh.read()
        tree = ast.parse(src)

        validate_lines, iam_lines = [], []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name == "_validate_download_checksum":
                validate_lines.append(node.lineno)
            elif name == "_setup_iam":
                iam_lines.append(node.lineno)

        assert len(validate_lines) >= 2, (
            "expected the monitoring tarball and the compose plugin checksums to "
            f"be validated; found {len(validate_lines)} call(s)"
        )
        assert iam_lines, "vacuity guard: _setup_iam call not found in make_pcluster.py"
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
        assert all(
            p.kind is inspect.Parameter.KEYWORD_ONLY for p in params.values()
        ), "must be keyword-only: two string parameters transpose silently"

    def test_the_name_is_never_the_per_build_bucket(self):
        """s3_bucketname is deleted on teardown by default; this one must not be it."""
        serial = "osiris-00412128072026"
        per_build = "parallelclustermaker-" + serial
        assert (
            _derive_results_bucket(aws_account_id=self._ACCT, region="us-east-1")
            != per_build
        )

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
