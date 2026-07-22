"""
Tests for IAM/policy/network/validation functions moved from make_pcluster.py
to src/pcluster_core.py.

Covers:
  - _validate_cluster_lifetime
  - _validate_fsx_size
  - _validate_ebs_config
  - _validate_ebs_shared_dir
  - _validate_queue_sizes
  - _render_policy
  - _setup_iam
  - _delete_managed_policies
  - _setup_fsx_hydration_iam
  - _validate_network
"""

import json
import os
import sys
import types

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
)

from pcluster_core import (
    _validate_cluster_lifetime,
    _validate_fsx_size,
    _validate_ebs_config,
    _validate_ebs_shared_dir,
    _validate_queue_sizes,
    _render_policy,
    _setup_iam,
    _cleanup_iam_on_failure,
    _delete_managed_policies,
    _setup_fsx_hydration_iam,
    _validate_network,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_DIR = os.path.join(REPO_ROOT, "templates")
POLICY_SRC = os.path.join(TEMPLATE_DIR, "ParallelClusterInstancePolicy.json_src")


# ---------------------------------------------------------------------------
# _validate_cluster_lifetime
# ---------------------------------------------------------------------------


class TestValidateClusterLifetime:
    def test_valid_d_h_m(self):
        _validate_cluster_lifetime("7:0:0")

    def test_valid_zeros(self):
        _validate_cluster_lifetime("0:24:0")

    def test_valid_large_values(self):
        _validate_cluster_lifetime("365:23:59")

    def test_missing_third_field_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_lifetime("7:0")

    def test_letters_raise(self):
        with pytest.raises(SystemExit):
            _validate_cluster_lifetime("7d:0h:0m")

    def test_empty_raises(self):
        with pytest.raises(SystemExit):
            _validate_cluster_lifetime("")

    def test_dashes_raise(self):
        with pytest.raises(SystemExit):
            _validate_cluster_lifetime("7-0-0")


# ---------------------------------------------------------------------------
# _validate_fsx_size
# ---------------------------------------------------------------------------


class TestValidateFsxSize:
    def test_valid_1200(self):
        _validate_fsx_size(1200, True)

    def test_valid_2400(self):
        _validate_fsx_size(2400, True)

    def test_valid_large(self):
        _validate_fsx_size(120000, True)

    def test_negative_multiple_raises(self):
        with pytest.raises(SystemExit):
            _validate_fsx_size(-1200, True)

    def test_zero_raises(self):
        with pytest.raises(SystemExit):
            _validate_fsx_size(0, True)

    def test_non_multiple_raises(self):
        with pytest.raises(SystemExit):
            _validate_fsx_size(1000, True)

    def test_skipped_when_disabled(self):
        _validate_fsx_size(-1200, False)
        _validate_fsx_size(0, False)
        _validate_fsx_size(999, False)


# ---------------------------------------------------------------------------
# _validate_ebs_config
# ---------------------------------------------------------------------------


class TestValidateEbsConfig:
    def _call(self, hn=100, cn=100, sh=100, st="gp2", iops=300, tp=200):
        _validate_ebs_config(hn, cn, sh, st, iops, tp)

    def test_valid_gp2(self):
        self._call(st="gp2")

    def test_valid_gp3(self):
        self._call(st="gp3", iops=3000, tp=125)

    def test_valid_io2(self):
        self._call(st="io2", iops=100)

    def test_headnode_below_1_raises(self):
        with pytest.raises(SystemExit):
            self._call(hn=0)

    def test_compute_below_1_raises(self):
        with pytest.raises(SystemExit):
            self._call(cn=0)

    def test_shared_below_1_raises(self):
        with pytest.raises(SystemExit):
            self._call(sh=0)

    def test_headnode_above_16384_raises(self):
        with pytest.raises(SystemExit):
            self._call(hn=16385)

    def test_shared_above_16384_raises(self):
        with pytest.raises(SystemExit):
            self._call(sh=16385)

    def test_gp3_iops_below_100_raises(self):
        with pytest.raises(SystemExit):
            self._call(st="gp3", iops=99, tp=125)

    def test_io1_iops_below_100_raises(self):
        with pytest.raises(SystemExit):
            self._call(st="io1", iops=50)

    def test_gp3_throughput_below_125_raises(self):
        with pytest.raises(SystemExit):
            self._call(st="gp3", iops=3000, tp=124)

    def test_gp2_ignores_iops_and_throughput(self):
        # gp2 doesn't validate IOPS or throughput — must not raise
        self._call(st="gp2", iops=0, tp=0)


# ---------------------------------------------------------------------------
# _validate_ebs_shared_dir
# ---------------------------------------------------------------------------


class TestValidateEbsSharedDir:
    def test_valid_simple(self):
        _validate_ebs_shared_dir("/shared")

    def test_valid_nested(self):
        _validate_ebs_shared_dir("/mnt/shared/data")

    def test_no_leading_slash_raises(self):
        with pytest.raises(SystemExit):
            _validate_ebs_shared_dir("shared")

    def test_embedded_quote_raises(self):
        with pytest.raises(SystemExit):
            _validate_ebs_shared_dir('/shared"dir')

    def test_semicolon_raises(self):
        with pytest.raises(SystemExit):
            _validate_ebs_shared_dir("/shared;rm -rf /")

    def test_backtick_raises(self):
        with pytest.raises(SystemExit):
            _validate_ebs_shared_dir("/shared`cmd`")

    def test_newline_raises(self):
        with pytest.raises(SystemExit):
            _validate_ebs_shared_dir("/shared\ndir")

    def test_dollar_raises(self):
        with pytest.raises(SystemExit):
            _validate_ebs_shared_dir("/shared/$HOME")


# ---------------------------------------------------------------------------
# _validate_queue_sizes
# ---------------------------------------------------------------------------


class TestValidateQueueSizes:
    def test_valid(self):
        _validate_queue_sizes(0, 10, 5)

    def test_valid_initial_equals_max(self):
        _validate_queue_sizes(10, 10, 1)

    def test_scaledown_zero_raises(self):
        with pytest.raises(SystemExit):
            _validate_queue_sizes(0, 10, 0)

    def test_scaledown_negative_raises(self):
        with pytest.raises(SystemExit):
            _validate_queue_sizes(0, 10, -1)

    def test_initial_negative_raises(self):
        with pytest.raises(SystemExit):
            _validate_queue_sizes(-1, 10, 5)

    def test_initial_exceeds_max_raises(self):
        with pytest.raises(SystemExit):
            _validate_queue_sizes(11, 10, 5)


# ---------------------------------------------------------------------------
# _render_policy
# ---------------------------------------------------------------------------

_RENDER_ARGS = (
    "123456789012",  # aws_account_id
    "us-east-1",     # region
    "vpc-abc12345",  # vpc_id
    "production",    # prod_level
    "test-cluster-00000000000000",  # cluster_serial_number
    "test-cluster",  # cluster_name
    "testowner",     # cluster_owner
    "00000000000000",  # cluster_serial_datestamp
)


class TestRenderPolicy:
    def test_renders_valid_json(self):
        src = os.path.join(TEMPLATE_DIR, "ParallelClusterInstancePolicy-A.json_src")
        result = _render_policy(src, *_RENDER_ARGS)
        data = json.loads(result)
        assert "Statement" in data
        assert len(data["Statement"]) > 0

    def test_minified_output(self):
        src = os.path.join(TEMPLATE_DIR, "ParallelClusterInstancePolicy-A.json_src")
        result = _render_policy(src, *_RENDER_ARGS)
        assert "\n" not in result
        assert "  " not in result

    def test_placeholders_substituted(self):
        src = os.path.join(TEMPLATE_DIR, "ParallelClusterInstancePolicy-A.json_src")
        result = _render_policy(src, *_RENDER_ARGS)
        assert "123456789012" in result
        assert "<AWS_ACCOUNT_ID>" not in result
        assert "<CLUSTER_NAME>" not in result

    def test_oversized_policy_raises(self, tmp_path):
        # Build a policy that is guaranteed to exceed 6144 bytes when minified.
        long_actions = [f"s3:SomeVeryLongActionName{i:04d}" for i in range(300)]
        big = {"Version": "2012-10-17", "Statement": [{"Sid": "X", "Effect": "Allow", "Action": long_actions, "Resource": "*"}]}
        src = tmp_path / "big.json_src"
        src.write_text(json.dumps(big))
        with pytest.raises(ValueError) as exc:
            _render_policy(str(src), *_RENDER_ARGS)
        assert "bytes" in str(exc.value)

    def test_all_three_policies_under_limit(self):
        for suffix in ("-A", "-B", "-C"):
            src = os.path.join(TEMPLATE_DIR, f"ParallelClusterInstancePolicy{suffix}.json_src")
            result = _render_policy(src, *_RENDER_ARGS)
            assert len(result.encode()) <= 6144


# ---------------------------------------------------------------------------
# _setup_iam / _delete_managed_policies — shared fake IAM client
# ---------------------------------------------------------------------------


class _FakeIAM:
    def __init__(self, role_exists=False):
        self._role_exists = role_exists
        self.created_roles = []
        self.created_policies = {}
        self.attached_policies = []
        self.detached_policies = []
        self.deleted_policies = []
        self.deleted_role_policies = []

    def get_role(self, RoleName):
        if not self._role_exists:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "NoSuchEntity", "Message": ""}}, "GetRole")
        return {"Role": {"RoleName": RoleName}}

    def list_attached_role_policies(self, RoleName):
        return {"AttachedPolicies": [{"PolicyName": n} for n in self.attached_policies]}

    def create_role(self, RoleName, AssumeRolePolicyDocument, Description=""):
        if self._role_exists:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "EntityAlreadyExists", "Message": ""}}, "CreateRole")
        self.created_roles.append(RoleName)
        self._role_exists = True
        return {"Role": {"RoleName": RoleName}}

    def delete_role(self, RoleName):
        self._role_exists = False
        if RoleName in self.created_roles:
            self.created_roles.remove(RoleName)

    def create_policy(self, PolicyName, PolicyDocument):
        arn = f"arn:aws:iam::123456789012:policy/{PolicyName}"
        self.created_policies[PolicyName] = arn
        return {"Policy": {"PolicyName": PolicyName, "Arn": arn}}

    def attach_role_policy(self, RoleName, PolicyArn):
        self.attached_policies.append(PolicyArn.split("/")[-1])

    def detach_role_policy(self, RoleName, PolicyArn):
        self.detached_policies.append(PolicyArn)

    def delete_policy(self, PolicyArn):
        self.deleted_policies.append(PolicyArn)

    def delete_role_policy(self, RoleName, PolicyName):
        self.deleted_role_policies.append((RoleName, PolicyName))

    def put_role_policy(self, RoleName, PolicyName, PolicyDocument):
        self.created_policies[PolicyName] = PolicyDocument


_SETUP_KWARGS = dict(
    ec2_json_policy_template="/tmp/test-policy.json",
    aws_account_id="123456789012",
    prod_level="production",
    cluster_serial_number="test-cluster-00000000000000",
    cluster_name="test-cluster",
    cluster_owner="testowner",
    cluster_serial_datestamp="00000000000000",
    ec2_json_policy_src=os.path.join(TEMPLATE_DIR, "ParallelClusterInstancePolicy.json_src"),
    region="us-east-1",
    vpc_id="vpc-abc12345",
)


class TestSetupIam:
    def test_creates_role_and_three_policies(self):
        iam = _FakeIAM(role_exists=False)
        _setup_iam(iam, "test-role", "test-policy", **_SETUP_KWARGS)
        assert "test-role" in iam.created_roles
        assert "test-policy-A" in iam.created_policies
        assert "test-policy-B" in iam.created_policies
        assert "test-policy-C" in iam.created_policies
        assert "test-policy-M" not in iam.created_policies

    def test_creates_monitoring_policy_when_enabled(self):
        iam = _FakeIAM(role_exists=False)
        _setup_iam(iam, "test-role", "test-policy", enable_monitoring=True, **_SETUP_KWARGS)
        assert "test-policy-M" in iam.created_policies

    def test_idempotent_when_all_policies_attached(self, capsys):
        iam = _FakeIAM(role_exists=True)
        iam.attached_policies = ["test-policy-A", "test-policy-B", "test-policy-C"]
        _setup_iam(iam, "test-role", "test-policy", **_SETUP_KWARGS)
        assert iam.created_roles == []
        assert "Found" in capsys.readouterr().out

    def test_recreates_when_policy_missing(self, capsys):
        iam = _FakeIAM(role_exists=True)
        iam.attached_policies = ["test-policy-A", "test-policy-B"]
        _setup_iam(iam, "test-role", "test-policy", **_SETUP_KWARGS)
        # Role already existed so create_role is NOT called; policies are deleted and recreated.
        assert "test-role" not in iam.created_roles
        deleted_names = [a.split("/")[-1] for a in iam.deleted_policies]
        assert "test-policy-A" in deleted_names
        assert "test-policy-B" in deleted_names
        out = capsys.readouterr().out
        assert "missing" in out
        assert "test-policy-A" in out

    def test_resume_does_not_call_create_role(self, capsys):
        # Simulates a retry where the role exists but one policy is missing.
        # create_role must NOT be called — it would raise EntityAlreadyExists against real AWS.
        iam = _FakeIAM(role_exists=True)
        iam.attached_policies = ["test-policy-A"]
        _setup_iam(iam, "test-role", "test-policy", **_SETUP_KWARGS)
        assert "test-role" not in iam.created_roles
        assert "test-policy-A" in iam.created_policies
        assert "test-policy-B" in iam.created_policies
        assert "test-policy-C" in iam.created_policies

    def test_render_failure_propagates_to_caller(self, monkeypatch):
        # _render_policy raises ValueError (policy too large); _setup_iam must
        # propagate it so make_pcluster.py's except Exception handler can call
        # _delete_managed_policies and iam.delete_role.
        iam = _FakeIAM(role_exists=False)
        monkeypatch.setattr(
            "pcluster_core._render_policy",
            lambda *a, **kw: (_ for _ in ()).throw(ValueError("policy too big")),
        )
        with pytest.raises(ValueError, match="policy too big"):
            _setup_iam(iam, "test-role", "test-policy", **_SETUP_KWARGS)
        # Role must not have been created — render failed before create_role was called.
        assert "test-role" not in iam.created_roles


class TestDeleteManagedPolicies:
    def test_deletes_abc_policies(self):
        iam = _FakeIAM()
        _delete_managed_policies(iam, "test-role", "test-policy", "123456789012", suppress=False)
        deleted_names = [a.split("/")[-1] for a in iam.deleted_policies]
        assert "test-policy-A" in deleted_names
        assert "test-policy-B" in deleted_names
        assert "test-policy-C" in deleted_names
        assert "test-policy-M" not in deleted_names

    def test_deletes_monitoring_policy_when_enabled(self):
        iam = _FakeIAM()
        _delete_managed_policies(
            iam, "test-role", "test-policy", "123456789012",
            suppress=False, enable_monitoring=True
        )
        deleted_names = [a.split("/")[-1] for a in iam.deleted_policies]
        assert "test-policy-M" in deleted_names

    def test_deletes_fsx_inline_policy(self):
        iam = _FakeIAM()
        _delete_managed_policies(
            iam, "test-role", "test-policy", "123456789012",
            suppress=False, fsx_policy="fsx-hydration-policy"
        )
        assert ("test-role", "fsx-hydration-policy") in iam.deleted_role_policies

    def test_suppress_mode_swallows_errors(self):
        class _BrokenIAM(_FakeIAM):
            def detach_role_policy(self, **kw):
                raise Exception("no such entity")
            def delete_policy(self, **kw):
                raise Exception("no such entity")
        iam = _BrokenIAM()
        _delete_managed_policies(iam, "test-role", "test-policy", "123456789012", suppress=True)


# ---------------------------------------------------------------------------
# _cleanup_iam_on_failure
# ---------------------------------------------------------------------------


class TestCleanupIamOnFailure:
    def test_deletes_policies_and_role(self):
        iam = _FakeIAM(role_exists=True)
        iam.attached_policies = ["test-policy-A", "test-policy-B", "test-policy-C"]
        deleted_roles = []
        iam.delete_role = lambda RoleName: deleted_roles.append(RoleName)
        _cleanup_iam_on_failure(iam, "test-role", "test-policy", "123456789012")
        deleted_names = [a.split("/")[-1] for a in iam.deleted_policies]
        assert "test-policy-A" in deleted_names
        assert "test-policy-B" in deleted_names
        assert "test-policy-C" in deleted_names
        assert "test-role" in deleted_roles

    def test_includes_monitoring_policy_when_enabled(self):
        iam = _FakeIAM(role_exists=True)
        iam.attached_policies = [
            "test-policy-A", "test-policy-B", "test-policy-C", "test-policy-M"
        ]
        deleted_roles = []
        iam.delete_role = lambda RoleName: deleted_roles.append(RoleName)
        _cleanup_iam_on_failure(
            iam, "test-role", "test-policy", "123456789012", enable_monitoring=True
        )
        deleted_names = [a.split("/")[-1] for a in iam.deleted_policies]
        assert "test-policy-M" in deleted_names
        assert "test-role" in deleted_roles

    def test_suppresses_errors_on_missing_role(self):
        iam = _FakeIAM(role_exists=False)
        deleted_roles = []
        iam.delete_role = lambda RoleName: deleted_roles.append(RoleName)
        # Should not raise even though role/policies do not exist.
        _cleanup_iam_on_failure(iam, "test-role", "test-policy", "123456789012")
        assert deleted_roles == ["test-role"]


# _setup_fsx_hydration_iam
# ---------------------------------------------------------------------------


class TestSetupFsxHydrationIam:
    def test_writes_policy_file_and_calls_put_role_policy(self, tmp_path):
        src = tmp_path / "LustreS3HydrationPolicy.json_src"
        src.write_text(
            '{"Version":"2012-10-17","Statement":[{"Sid":"S3","Effect":"Allow",'
            '"Action":["s3:GetObject"],"Resource":["arn:aws:s3:::<FSX_S3_EXPORT_BUCKET>/*",'
            '"arn:aws:s3:::<FSX_S3_IMPORT_BUCKET>/*"]}]}'
        )
        dest = tmp_path / "fsx_policy.json"
        iam = _FakeIAM()
        _setup_fsx_hydration_iam(
            iam,
            "test-role",
            "test-fsx-policy",
            str(src),
            str(dest),
            "my-export-bucket",
            "my-import-bucket",
        )
        assert dest.exists()
        content = dest.read_text()
        assert "my-export-bucket" in content
        assert "my-import-bucket" in content
        assert "<FSX_S3_EXPORT_BUCKET>" not in content
        assert "test-fsx-policy" in iam.created_policies

    def test_policy_file_mode_is_0600(self, tmp_path):
        src = tmp_path / "policy.json_src"
        src.write_text('{"Version":"2012-10-17","Statement":[]}')
        dest = tmp_path / "out.json"
        iam = _FakeIAM()
        _setup_fsx_hydration_iam(iam, "r", "p", str(src), str(dest), "b1", "b2")
        mode = oct(os.stat(str(dest)).st_mode & 0o777)
        assert mode == oct(0o600)


# ---------------------------------------------------------------------------
# _validate_network
# ---------------------------------------------------------------------------


def _make_ec2client(
    vpcs=None,
    subnets_by_az=None,
):
    """Build a minimal fake EC2 client for network validation tests."""
    if vpcs is None:
        vpcs = [{"VpcId": "vpc-abc12345", "CidrBlock": "10.0.0.0/16"}]
    if subnets_by_az is None:
        subnets_by_az = {"us-east-1a": [{"SubnetId": "subnet-aaa"}]}

    client = types.SimpleNamespace()

    def describe_vpcs(Filters):
        return {"Vpcs": vpcs}

    def describe_subnets(Filters):
        az = next((f["Values"][0] for f in Filters if f["Name"] == "availabilityZone"), None)
        return {"Subnets": subnets_by_az.get(az, [])}

    client.describe_vpcs = describe_vpcs
    client.describe_subnets = describe_subnets
    return client


class TestValidateNetwork:
    def test_explicit_subnets_returned_unchanged(self):
        ec2 = _make_ec2client()
        vpc_id, hn_subnet, compute_subnets, cidr = _validate_network(
            ec2, "us-east-1a", "vpc_default",
            headnode_subnet_id="subnet-explicit-hn",
            compute_az_list=["us-east-1a"],
            compute_subnet_ids_override="subnet-explicit-c1,subnet-explicit-c2",
            use_private_compute_subnet="false",
        )
        assert vpc_id == "vpc-abc12345"
        assert hn_subnet == "subnet-explicit-hn"
        assert compute_subnets == ["subnet-explicit-c1", "subnet-explicit-c2"]

    def test_auto_discovers_headnode_subnet(self):
        ec2 = _make_ec2client()
        _, hn_subnet, _, _ = _validate_network(
            ec2, "us-east-1a", "vpc_default",
            headnode_subnet_id="",
            compute_az_list=["us-east-1a"],
            compute_subnet_ids_override="",
            use_private_compute_subnet="false",
        )
        assert hn_subnet == "subnet-aaa"

    def test_undefined_vpc_raises(self):
        ec2 = _make_ec2client(vpcs=[])
        with pytest.raises(SystemExit):
            _validate_network(
                ec2, "us-east-1a", "my-missing-vpc",
                headnode_subnet_id="",
                compute_az_list=["us-east-1a"],
                compute_subnet_ids_override="",
                use_private_compute_subnet="false",
            )

    def test_no_subnets_in_az_raises(self):
        ec2 = _make_ec2client(subnets_by_az={"us-east-1a": []})
        with pytest.raises(SystemExit):
            _validate_network(
                ec2, "us-east-1a", "vpc_default",
                headnode_subnet_id="",
                compute_az_list=["us-east-1a"],
                compute_subnet_ids_override="",
                use_private_compute_subnet="false",
            )

    def test_multiple_subnets_warns_and_picks_first(self, capsys):
        ec2 = _make_ec2client(
            subnets_by_az={"us-east-1a": [{"SubnetId": "subnet-1"}, {"SubnetId": "subnet-2"}]}
        )
        _, hn_subnet, _, _ = _validate_network(
            ec2, "us-east-1a", "vpc_default",
            headnode_subnet_id="",
            compute_az_list=[],
            compute_subnet_ids_override="subnet-explicit",
            use_private_compute_subnet="false",
        )
        assert hn_subnet == "subnet-1"
        assert "WARNING" in capsys.readouterr().out
