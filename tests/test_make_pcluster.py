"""
Unit tests for src/pcluster_core.py — pure utility functions extracted from
make_pcluster.py.  No AWS credentials or venv required.
"""

import os
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

    def test_rhel8(self):
        user, home = _resolve_ec2_user("rhel8")
        assert user == "ec2-user"
        assert home == "/home/ec2-user"

    def test_rhel9(self):
        user, home = _resolve_ec2_user("rhel9")
        assert user == "ec2-user"
        assert home == "/home/ec2-user"

    def test_unknown_os_raises(self):
        with pytest.raises(SystemExit):
            _resolve_ec2_user("centos7")


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
    def test_both_defined_and_different(self, capsys):
        out_bucket, out_path = _normalize_fsx_buckets(
            "import-bucket", "export-bucket", "import/", "export/"
        )
        assert out_bucket == "export-bucket"
        assert out_path == "export/"
        captured = capsys.readouterr()
        assert captured.out == ""  # no warnings

    def test_export_undefined_defaults_to_import(self, capsys):
        out_bucket, out_path = _normalize_fsx_buckets(
            "my-bucket", "UNDEFINED", "data/", "export/"
        )
        assert out_bucket == "my-bucket"
        assert out_path == "data/"
        captured = capsys.readouterr()
        assert "WARNING" in captured.out

    def test_same_bucket_warns(self, capsys):
        out_bucket, out_path = _normalize_fsx_buckets(
            "same-bucket", "same-bucket", "in/", "out/"
        )
        assert out_bucket == "same-bucket"
        captured = capsys.readouterr()
        assert "WARNING" in captured.out

    def test_same_bucket_and_path_warns_twice(self, capsys):
        _normalize_fsx_buckets("same", "same", "path/", "path/")
        captured = capsys.readouterr()
        assert captured.out.count("WARNING") >= 2

    def test_both_undefined_no_warning(self, capsys):
        out_bucket, out_path = _normalize_fsx_buckets(
            "UNDEFINED", "UNDEFINED", "import", "export"
        )
        assert out_bucket == "UNDEFINED"
        captured = capsys.readouterr()
        assert captured.out == ""


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

    def test_missing_bucket_raises(self):
        client = _make_s3_client(head_ok=False)
        with pytest.raises(SystemExit):
            _check_fsx_s3(client, "missing-bucket", "data/", "import")

    def test_empty_path_raises(self):
        client = _make_s3_client(head_ok=True, key_count=0)
        with pytest.raises(SystemExit):
            _check_fsx_s3(client, "my-bucket", "empty/", "import")

    def test_access_denied_raises(self):
        client = _make_s3_client(head_ok=False, head_error_code="403")
        with pytest.raises(SystemExit):
            _check_fsx_s3(client, "private-bucket", "data/", "import")
