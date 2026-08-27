"""
Tests for IAM/policy/network/validation functions moved from make_pcluster.py
to src/pcluster_core.py.

Covers:
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
    _get_efa_instance_types,
    _ssh_secret_name,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_DIR = os.path.join(REPO_ROOT, "templates")


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


class TestFsxSizeMatchesUpstreamsOwnRule:
    """The rule is `1200 or n % 2400 == 0`, not `n % 1200 == 0`.

    FsxStorageCapacityValidator (pcluster/validators/fsx_validators.py) accepts
    1200 or any multiple of 2400 for SCRATCH_2, PERSISTENT_1 and PERSISTENT_2.
    SCRATCH_2 is what this toolkit gets: config.pcluster.j2's FsxLustreSettings
    sets StorageCapacity and no DeploymentType, and cluster_config.py defaults it
    to SCRATCH_2. A multiple-of-1200 test admits every odd multiple, all of which
    FSx rejects twenty minutes into the build with the IAM role, keypair, S3
    bucket and Secrets Manager secret already created.
    """

    # Every odd multiple of 1200 above 1200: accepted by the old rule, rejected
    # by FSx. 1200 itself is the documented exception and must stay valid.
    @pytest.mark.parametrize("size", [3600, 6000, 8400, 10800, 13200])
    def test_odd_multiples_of_1200_are_rejected(self, size):
        with pytest.raises(SystemExit) as exc:
            _validate_fsx_size(size, True)
        assert "2400" in str(exc.value), "the message must name the real rule"

    @pytest.mark.parametrize("size", [1200, 2400, 4800, 7200, 9600, 120000])
    def test_the_sizes_fsx_actually_accepts_are_still_valid(self, size):
        _validate_fsx_size(size, True)

    def test_the_rule_matches_upstreams_validator_exactly(self):
        """Differential check against PCluster's own predicate, not a restatement.

        Anything that agrees with upstream on 1200 and on multiples of 2400 but
        disagrees anywhere else is a divergence this catches without anyone
        having to think of the specific number.
        """
        for size in range(0, 24001, 1200):
            upstream_ok = size == 1200 or (size != 0 and size % 2400 == 0)
            try:
                _validate_fsx_size(size, True)
                ours_ok = True
            except SystemExit:
                ours_ok = False
            assert ours_ok == upstream_ok, (
                f"fsx_size={size}: this toolkit says "
                f"{'valid' if ours_ok else 'invalid'}, FSx says "
                f"{'valid' if upstream_ok else 'invalid'}"
            )


# ---------------------------------------------------------------------------
# _validate_ebs_config
# ---------------------------------------------------------------------------


def _ebs_call(hn=100, cn=100, sh=100, st="gp2", iops=300, tp=200, gpu=100, **over):
    """Validate with one volume type/iops/throughput applied to all four volumes.

    `st`/`iops`/`tp` set every volume at once so the pre-existing cases below
    read unchanged; `over` reaches an individual volume's parameter by its real
    keyword (`headnode_type=`, `gpu_iops=`, ...).
    """
    kwargs = dict(
        headnode_size=hn,
        headnode_type=st,
        headnode_iops=iops,
        headnode_throughput=tp,
        compute_size=cn,
        compute_type=st,
        compute_iops=iops,
        compute_throughput=tp,
        gpu_size=gpu,
        gpu_type=st,
        gpu_iops=iops,
        gpu_throughput=tp,
        shared_size=sh,
        shared_type=st,
        shared_iops=iops,
        shared_throughput=tp,
        enable_cpu_queue=True,
        enable_gpu_queue=True,
    )
    kwargs.update(over)
    _validate_ebs_config(**kwargs)


class TestValidateEbsConfig:
    def _call(self, **kwargs):
        _ebs_call(**kwargs)

    def test_valid_gp2(self):
        self._call(st="gp2")

    def test_valid_gp3(self):
        self._call(st="gp3", iops=3000, tp=125)

    def test_valid_io2(self):
        self._call(st="io2", iops=100)

    # A bare pytest.raises(SystemExit) cannot tell which of the parameters was
    # rejected: swapping the headnode/compute labels in the error message left
    # all of these green while the operator was told to fix the wrong volume.
    # Assert on the message so the blamed parameter is pinned down.
    def test_headnode_below_1_raises(self):
        with pytest.raises(SystemExit) as exc:
            self._call(hn=0)
        assert "headnode_root_volume_size" in str(exc.value)

    def test_compute_below_1_raises(self):
        with pytest.raises(SystemExit) as exc:
            self._call(cn=0)
        assert "compute_root_volume_size" in str(exc.value)

    def test_shared_below_1_raises(self):
        with pytest.raises(SystemExit) as exc:
            self._call(sh=0)
        assert "ebs_shared_volume_size" in str(exc.value)

    def test_headnode_above_16384_raises(self):
        with pytest.raises(SystemExit) as exc:
            self._call(hn=16385)
        assert "headnode_root_volume_size" in str(exc.value)
        assert "16,384" in str(exc.value)

    def test_shared_above_16384_raises(self):
        with pytest.raises(SystemExit) as exc:
            self._call(sh=16385)
        assert "ebs_shared_volume_size" in str(exc.value)
        assert "16,384" in str(exc.value)

    def test_gp3_iops_below_100_raises(self):
        with pytest.raises(SystemExit) as exc:
            self._call(st="gp3", iops=99, tp=125)
        assert "headnode_root_volume_iops" in str(exc.value)

    def test_io1_iops_below_100_raises(self):
        with pytest.raises(SystemExit) as exc:
            self._call(st="io1", iops=50)
        assert "headnode_root_volume_iops" in str(exc.value)

    def test_gp3_throughput_below_125_raises(self):
        with pytest.raises(SystemExit) as exc:
            self._call(st="gp3", iops=3000, tp=124)
        assert "headnode_root_volume_throughput" in str(exc.value)

    def test_gp2_ignores_iops_and_throughput(self):
        # gp2 doesn't validate IOPS or throughput — must not raise
        self._call(st="gp2", iops=0, tp=0)


class TestEveryVolumeIsCheckedNotJustTheSharedOne:
    """All four volumes config.pcluster.j2 renders reach a local check.

    The validator took six parameters -- three sizes plus the shared volume's
    type/iops/throughput -- so `gpu_root_volume_*` reached no local check at all
    and the head node's and compute's iops/throughput reached none either. All of
    them are rendered into the cluster config, and upstream validates all of them
    (HeadNode and SlurmQueue register EbsVolumeIopsValidator on their root
    volumes; the Ebs base class covers the shared one) -- but at stack launch,
    after the five managed policies, the IAM role, the keypair, the S3 bucket and
    the Secrets Manager secret exist and have to be swept before a retry.

    Parametrized over the volume rather than written out four times, so a fifth
    volume added to the config without a check here is a one-line failure.
    """

    _VOLUMES = ["headnode", "compute", "gpu", "shared"]

    @pytest.mark.parametrize("vol", _VOLUMES)
    def test_an_out_of_range_iops_is_rejected_for_every_volume(self, vol):
        with pytest.raises(SystemExit) as exc:
            _ebs_call(st="gp3", iops=3000, tp=125, **{f"{vol}_iops": 99})
        assert vol in str(exc.value), (
            f"{vol}'s IOPS was rejected but the message blames another volume"
        )

    @pytest.mark.parametrize("vol", _VOLUMES)
    def test_an_out_of_range_throughput_is_rejected_for_every_volume(self, vol):
        with pytest.raises(SystemExit) as exc:
            _ebs_call(st="gp3", iops=3000, tp=125, **{f"{vol}_throughput": 1001})
        assert vol in str(exc.value)

    @pytest.mark.parametrize("vol", _VOLUMES)
    def test_an_out_of_range_size_is_rejected_for_every_volume(self, vol):
        with pytest.raises(SystemExit) as exc:
            _ebs_call(st="gp3", iops=3000, tp=125, **{f"{vol}_size": 0})
        assert vol in str(exc.value)

    def test_the_shipped_defaults_are_valid(self):
        """Vacuity guard, and the thing that must never regress.

        pcluster_defaults.yml ships gp3 at 3000 IOPS / 125 MB/s on all four
        volumes, with sizes 100/250/250/250. Every bound added here has to leave
        that configuration valid or no cluster builds at all.
        """
        _ebs_call(hn=100, cn=250, gpu=250, sh=250, st="gp3", iops=3000, tp=125)


class TestEbsBoundsMatchUpstream:
    """The bounds are PCluster's own, including the two ratio rules.

    A floor-and-ceiling check on each parameter in isolation passes values that
    EBS itself rejects: IOPS is additionally capped at a per-GiB ratio, and gp3
    throughput at a quarter of the provisioned IOPS. Both are enforced by
    EbsVolumeIopsValidator and EbsVolumeThroughputIopsValidator upstream, so
    without them here the config is rejected at stack launch instead.
    """

    def test_gp3_iops_floor_is_3000_not_100(self):
        """EBS_VOLUME_IOPS_BOUNDS["gp3"] is (3000, 16000); io1/io2 are (100, ...).

        The single shared floor of 100 accepted every gp3 value in 100..2999.
        """
        with pytest.raises(SystemExit) as exc:
            _ebs_call(st="gp3", iops=2999, tp=125, hn=1000, cn=1000, gpu=1000, sh=1000)
        assert "3,000" in str(exc.value) or "3000" in str(exc.value)

    def test_gp3_iops_ceiling(self):
        with pytest.raises(SystemExit):
            _ebs_call(st="gp3", iops=16001, tp=125, hn=1000, cn=1000, gpu=1000, sh=1000)

    def test_io1_iops_ceiling(self):
        with pytest.raises(SystemExit):
            _ebs_call(st="io1", iops=64001, tp=125, hn=2000, cn=2000, gpu=2000, sh=2000)

    def test_iops_to_size_ratio_is_enforced(self):
        """gp3 allows 500 IOPS per GiB. A 10 GiB volume caps at 5000."""
        _ebs_call(st="gp3", iops=5000, tp=125, hn=10, cn=10, gpu=10, sh=10)
        with pytest.raises(SystemExit) as exc:
            _ebs_call(st="gp3", iops=5001, tp=125, hn=10, cn=10, gpu=10, sh=10)
        assert "per GiB" in str(exc.value)

    def test_throughput_to_iops_ratio_is_enforced(self):
        """gp3 throughput may not exceed 0.25 x IOPS. 3000 IOPS caps at 750."""
        _ebs_call(st="gp3", iops=3000, tp=750, hn=100, cn=100, gpu=100, sh=100)
        with pytest.raises(SystemExit) as exc:
            _ebs_call(st="gp3", iops=3000, tp=751, hn=100, cn=100, gpu=100, sh=100)
        assert "0.25" in str(exc.value)

    def test_gp3_throughput_ceiling(self):
        with pytest.raises(SystemExit):
            _ebs_call(st="gp3", iops=16000, tp=1001, hn=1000, cn=1000, gpu=1000, sh=1000)

    def test_st1_minimum_size_is_500(self):
        """EBS_VOLUME_TYPE_TO_VOLUME_SIZE_BOUNDS["st1"] is (500, 16384).

        st1 is in every root-volume argparse `choices` list, and a flat floor of
        1 GiB accepted 1..499 for it.
        """
        with pytest.raises(SystemExit) as exc:
            _ebs_call(st="st1", hn=499, cn=500, gpu=500, sh=500)
        assert "500" in str(exc.value)
        _ebs_call(st="st1", hn=500, cn=500, gpu=500, sh=500)

    def test_io2_ceiling_is_65536_not_16384(self):
        """io2 goes to 64 TiB upstream; every other type caps at 16 TiB.

        A flat 16,384 ceiling rejected a valid io2 volume.
        """
        _ebs_call(st="io2", iops=100, hn=20000, cn=20000, gpu=20000, sh=20000)
        with pytest.raises(SystemExit):
            _ebs_call(st="io2", iops=100, hn=65537, cn=100, gpu=100, sh=100)

    def test_the_bounds_tables_agree_with_the_installed_pcluster(self):
        """Differential check against upstream's own tables, not a restatement.

        These are copied constants; a version bump that changes them upstream
        must fail here rather than silently diverge.
        """
        from pcluster.validators.ebs_validators import (
            EBS_VOLUME_IOPS_BOUNDS,
            EBS_VOLUME_TYPE_TO_IOPS_RATIO,
            EBS_VOLUME_TYPE_TO_VOLUME_SIZE_BOUNDS,
        )
        from pcluster_core import (
            _EBS_IOPS_BOUNDS,
            _EBS_IOPS_TO_SIZE_RATIO,
            _EBS_SIZE_BOUNDS,
        )

        assert _EBS_IOPS_BOUNDS == EBS_VOLUME_IOPS_BOUNDS
        assert _EBS_IOPS_TO_SIZE_RATIO == EBS_VOLUME_TYPE_TO_IOPS_RATIO
        assert _EBS_SIZE_BOUNDS == EBS_VOLUME_TYPE_TO_VOLUME_SIZE_BOUNDS


class TestOnlyTheQueuesThatExistAreValidated:
    """A queue's root volume is checked only when config.pcluster.j2 renders it.

    Both queue blocks are gated (`enable_cpu_queue`, `enable_gpu_queue`), so on a
    GPU-only cluster the compute values are never written into the config and
    rejecting them would fail a configuration that builds fine. The head node and
    the shared EBS volume are unconditional.
    """

    def test_a_gpu_only_cluster_ignores_the_compute_volume(self):
        _ebs_call(st="gp3", iops=3000, tp=125, compute_iops=1, enable_cpu_queue=False)

    def test_a_cpu_only_cluster_ignores_the_gpu_volume(self):
        _ebs_call(st="gp3", iops=3000, tp=125, gpu_iops=1, enable_gpu_queue=False)

    def test_the_head_node_and_shared_volumes_are_never_gated(self):
        for param in ("headnode_iops", "shared_iops"):
            with pytest.raises(SystemExit):
                _ebs_call(
                    st="gp3",
                    iops=3000,
                    tp=125,
                    enable_cpu_queue=False,
                    enable_gpu_queue=False,
                    **{param: 1},
                )


class TestEbsValidationTakesKeywordsOnly:
    """Sixteen same-typed parameters; a transposition must not render valid.

    Same reasoning as _storage_summary_lines: with four (size, type, iops,
    throughput) quadruples in a row, swapping two volumes' values produces a
    plausible validation pass and blames the wrong parameter when it does fail.
    The leading `*` in both signatures is load-bearing.
    """

    def test_validate_ebs_config_rejects_positional_arguments(self):
        import inspect

        from pcluster_core import _validate_ebs_config as fn

        for name, p in inspect.signature(fn).parameters.items():
            assert p.kind is inspect.Parameter.KEYWORD_ONLY, (
                f"_validate_ebs_config({name}) is positional; add a leading '*'"
            )

    def test_validate_ebs_volume_rejects_positional_arguments(self):
        import inspect

        from pcluster_core import _validate_ebs_volume as fn

        for name, p in inspect.signature(fn).parameters.items():
            assert p.kind is inspect.Parameter.KEYWORD_ONLY, (
                f"_validate_ebs_volume({name}) is positional; add a leading '*'"
            )

    def test_the_call_site_passes_no_positional_arguments(self):
        """An AST walk over src/pcluster_core.py's core_create_cluster (where
        this call has lived since the core/shim split), so the signature
        cannot drift alone."""
        import ast

        src = os.path.join(REPO_ROOT, "src", "pcluster_core.py")
        with open(src) as fh:
            tree = ast.parse(fh.read())
        calls = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_validate_ebs_config"
        ]
        assert calls, "src/pcluster_core.py no longer calls _validate_ebs_config"
        for call in calls:
            assert not call.args, "_validate_ebs_config called with positional args"
            assert not any(
                kw.arg is None for kw in call.keywords
            ), "_validate_ebs_config called with a **kwargs splat"


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

    # Three positional ints: a bare raises() cannot distinguish which one the
    # validator actually rejected, so assert on the named parameter too.
    def test_scaledown_zero_raises(self):
        with pytest.raises(SystemExit) as exc:
            _validate_queue_sizes(0, 10, 0)
        assert "scaledown_idletime" in str(exc.value)

    def test_scaledown_negative_raises(self):
        with pytest.raises(SystemExit) as exc:
            _validate_queue_sizes(0, 10, -1)
        assert "scaledown_idletime" in str(exc.value)

    def test_initial_negative_raises(self):
        with pytest.raises(SystemExit) as exc:
            _validate_queue_sizes(-1, 10, 5)
        assert "initial_queue_size" in str(exc.value)

    def test_initial_exceeds_max_raises(self):
        with pytest.raises(SystemExit) as exc:
            _validate_queue_sizes(11, 10, 5)
        assert "initial_queue_size" in str(exc.value)
        assert "max_queue_size" in str(exc.value)


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
        src = os.path.join(TEMPLATE_DIR, "HeadNode-Compute.json_src")
        result = _render_policy(src, *_RENDER_ARGS)
        data = json.loads(result)
        assert "Statement" in data
        assert len(data["Statement"]) > 0

    def test_minified_output(self):
        src = os.path.join(TEMPLATE_DIR, "HeadNode-Compute.json_src")
        result = _render_policy(src, *_RENDER_ARGS)
        assert "\n" not in result
        assert "  " not in result

    def test_placeholders_substituted(self):
        src = os.path.join(TEMPLATE_DIR, "HeadNode-Compute.json_src")
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

    def test_all_policies_under_limit(self):
        for fname in ("HeadNode-Compute", "HeadNode-Storage", "HeadNode-IAM",
                      "ComputeNode-Base", "HeadNode-Monitoring"):
            src = os.path.join(TEMPLATE_DIR, f"{fname}.json_src")
            result = _render_policy(src, *_RENDER_ARGS)
            assert len(result.encode()) <= 6144, f"{fname} exceeds 6144 bytes"


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
        # {role_name: boundary_arn or None}. Recording the absence is what
        # lets a test see an unbounded role rather than infer it.
        self.boundaries = {}
        self.reasserted_boundaries = []

    def get_role(self, RoleName):
        if not self._role_exists:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "NoSuchEntity", "Message": ""}}, "GetRole")
        return {"Role": {"RoleName": RoleName}}

    def list_attached_role_policies(self, RoleName):
        return {"AttachedPolicies": [{"PolicyName": n} for n in self.attached_policies]}

    def create_role(self, RoleName, AssumeRolePolicyDocument, Description="",
                    PermissionsBoundary=None):
        if self._role_exists:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "EntityAlreadyExists", "Message": ""}}, "CreateRole")
        self.created_roles.append(RoleName)
        self._role_exists = True
        self.boundaries[RoleName] = PermissionsBoundary
        return {"Role": {"RoleName": RoleName}}

    def put_role_permissions_boundary(self, RoleName, PermissionsBoundary):
        self.boundaries[RoleName] = PermissionsBoundary
        self.reasserted_boundaries.append((RoleName, PermissionsBoundary))
        return {}

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
    region="us-east-1",
    vpc_id="vpc-abc12345",
)


class TestSetupIam:
    def test_creates_role_and_policies(self):
        iam = _FakeIAM(role_exists=False)
        _setup_iam(iam, "test-role", "test-policy", **_SETUP_KWARGS)
        assert "test-role" in iam.created_roles
        assert "test-policy-HeadNode-Compute" in iam.created_policies
        assert "test-policy-HeadNode-Storage" in iam.created_policies
        assert "test-policy-HeadNode-IAM" in iam.created_policies
        assert "test-policy-ComputeNode-Base" in iam.created_policies
        assert "test-policy-HeadNode-Monitoring" not in iam.created_policies

    def test_creates_monitoring_policy_when_enabled(self):
        iam = _FakeIAM(role_exists=False)
        _setup_iam(iam, "test-role", "test-policy", enable_monitoring=True, **_SETUP_KWARGS)
        assert "test-policy-HeadNode-Monitoring" in iam.created_policies

    def test_idempotent_when_all_policies_attached(self, capsys):
        iam = _FakeIAM(role_exists=True)
        iam.attached_policies = [
            "test-policy-HeadNode-Compute", "test-policy-HeadNode-Storage",
            "test-policy-HeadNode-IAM", "test-policy-ComputeNode-Base",
        ]
        _setup_iam(iam, "test-role", "test-policy", **_SETUP_KWARGS)
        assert iam.created_roles == []
        assert "Found" in capsys.readouterr().out

    def test_recreates_when_policy_missing(self, capsys):
        iam = _FakeIAM(role_exists=True)
        iam.attached_policies = ["test-policy-HeadNode-Compute", "test-policy-HeadNode-Storage"]
        _setup_iam(iam, "test-role", "test-policy", **_SETUP_KWARGS)
        assert "test-role" not in iam.created_roles
        deleted_names = [a.split("/")[-1] for a in iam.deleted_policies]
        assert "test-policy-HeadNode-Compute" in deleted_names
        assert "test-policy-HeadNode-Storage" in deleted_names
        out = capsys.readouterr().out
        assert "missing" in out

    def test_prunes_stale_monitoring_policy_when_disabled(self, capsys):
        # Role was built with enable_monitoring=True previously; a same-serial
        # rebuild with enable_monitoring=False (the current call) must detach
        # and delete the now-unwanted HeadNode-Monitoring policy rather than
        # treating the superset of attached policies as already-satisfied.
        iam = _FakeIAM(role_exists=True)
        iam.attached_policies = [
            "test-policy-HeadNode-Compute", "test-policy-HeadNode-Storage",
            "test-policy-HeadNode-IAM", "test-policy-ComputeNode-Base",
            "test-policy-HeadNode-Monitoring",
        ]
        _setup_iam(iam, "test-role", "test-policy", enable_monitoring=False, **_SETUP_KWARGS)
        deleted_names = [a.split("/")[-1] for a in iam.deleted_policies]
        assert "test-policy-HeadNode-Monitoring" in deleted_names
        assert "test-policy-HeadNode-Monitoring" not in iam.created_policies
        out = capsys.readouterr().out
        assert "stale" in out

    def test_resume_does_not_call_create_role(self, capsys):
        iam = _FakeIAM(role_exists=True)
        iam.attached_policies = ["test-policy-HeadNode-Compute"]
        _setup_iam(iam, "test-role", "test-policy", **_SETUP_KWARGS)
        assert "test-role" not in iam.created_roles
        assert "test-policy-HeadNode-Compute" in iam.created_policies
        assert "test-policy-HeadNode-Storage" in iam.created_policies
        assert "test-policy-HeadNode-IAM" in iam.created_policies
        assert "test-policy-ComputeNode-Base" in iam.created_policies

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
    def test_deletes_base_policies(self):
        iam = _FakeIAM()
        _delete_managed_policies(iam, "test-role", "test-policy", "123456789012", suppress=False)
        deleted_names = [a.split("/")[-1] for a in iam.deleted_policies]
        assert "test-policy-HeadNode-Compute" in deleted_names
        assert "test-policy-HeadNode-Storage" in deleted_names
        assert "test-policy-HeadNode-IAM" in deleted_names
        assert "test-policy-ComputeNode-Base" in deleted_names
        assert "test-policy-HeadNode-Monitoring" not in deleted_names

    def test_deletes_monitoring_policy_when_enabled(self):
        iam = _FakeIAM()
        _delete_managed_policies(
            iam, "test-role", "test-policy", "123456789012",
            suppress=False, enable_monitoring=True
        )
        deleted_names = [a.split("/")[-1] for a in iam.deleted_policies]
        assert "test-policy-HeadNode-Monitoring" in deleted_names

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
        iam.attached_policies = [
            "test-policy-HeadNode-Compute", "test-policy-HeadNode-Storage",
            "test-policy-HeadNode-IAM", "test-policy-ComputeNode-Base",
        ]
        deleted_roles = []
        iam.delete_role = lambda RoleName: deleted_roles.append(RoleName)
        _cleanup_iam_on_failure(iam, "test-role", "test-policy", "123456789012")
        deleted_names = [a.split("/")[-1] for a in iam.deleted_policies]
        assert "test-policy-HeadNode-Compute" in deleted_names
        assert "test-policy-HeadNode-Storage" in deleted_names
        assert "test-policy-HeadNode-IAM" in deleted_names
        assert "test-policy-ComputeNode-Base" in deleted_names
        assert "test-role" in deleted_roles

    def test_includes_monitoring_policy_when_enabled(self):
        iam = _FakeIAM(role_exists=True)
        iam.attached_policies = [
            "test-policy-HeadNode-Compute", "test-policy-HeadNode-Storage",
            "test-policy-HeadNode-IAM", "test-policy-ComputeNode-Base",
            "test-policy-HeadNode-Monitoring",
        ]
        deleted_roles = []
        iam.delete_role = lambda RoleName: deleted_roles.append(RoleName)
        _cleanup_iam_on_failure(
            iam, "test-role", "test-policy", "123456789012", enable_monitoring=True
        )
        deleted_names = [a.split("/")[-1] for a in iam.deleted_policies]
        assert "test-policy-HeadNode-Monitoring" in deleted_names
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
        vpc_id, hn_subnet, compute_subnets, gpu_subnets, cidr, _ln = _validate_network(
            ec2client=ec2, az="us-east-1a", vpc_name="vpc_default",
            headnode_subnet_id="subnet-explicit-hn",
            compute_az_list=["us-east-1a"],
            compute_subnet_ids_override="subnet-explicit-c1,subnet-explicit-c2",
            use_private_compute_subnet="false",
        )
        assert vpc_id == "vpc-abc12345"
        assert hn_subnet == "subnet-explicit-hn"
        assert compute_subnets == ["subnet-explicit-c1", "subnet-explicit-c2"]
        assert gpu_subnets == ["subnet-explicit-c1", "subnet-explicit-c2"]

    def test_auto_discovers_headnode_subnet(self):
        ec2 = _make_ec2client()
        _, hn_subnet, _, _, _, _ = _validate_network(
            ec2client=ec2, az="us-east-1a", vpc_name="vpc_default",
            headnode_subnet_id="",
            compute_az_list=["us-east-1a"],
            compute_subnet_ids_override="",
            use_private_compute_subnet="false",
        )
        assert hn_subnet == "subnet-aaa"

    # This path prints its diagnosis and then sys.exit(1), so the exception
    # itself carries no message — a bare raises() would pass even if the VPC
    # branch were replaced by an unrelated failure. Assert on stdout.
    def test_undefined_vpc_raises(self, capsys):
        ec2 = _make_ec2client(vpcs=[])
        with pytest.raises(SystemExit):
            _validate_network(
                ec2client=ec2, az="us-east-1a", vpc_name="my-missing-vpc",
                headnode_subnet_id="",
                compute_az_list=["us-east-1a"],
                compute_subnet_ids_override="",
                use_private_compute_subnet="false",
            )
        out = capsys.readouterr().out
        assert "VPC not found" in out
        assert "my-missing-vpc" in out

    def test_no_subnets_in_az_raises(self, capsys):
        ec2 = _make_ec2client(subnets_by_az={"us-east-1a": []})
        with pytest.raises(SystemExit):
            _validate_network(
                ec2client=ec2, az="us-east-1a", vpc_name="vpc_default",
                headnode_subnet_id="",
                compute_az_list=["us-east-1a"],
                compute_subnet_ids_override="",
                use_private_compute_subnet="false",
            )
        out = capsys.readouterr().out
        assert "No subnets found in AZ us-east-1a" in out

    def test_multiple_subnets_warns_and_picks_first(self, capsys):
        ec2 = _make_ec2client(
            subnets_by_az={"us-east-1a": [{"SubnetId": "subnet-1"}, {"SubnetId": "subnet-2"}]}
        )
        _, hn_subnet, _, _, _, _ = _validate_network(
            ec2client=ec2, az="us-east-1a", vpc_name="vpc_default",
            headnode_subnet_id="",
            compute_az_list=[],
            compute_subnet_ids_override="subnet-explicit",
            use_private_compute_subnet="false",
        )
        assert hn_subnet == "subnet-1"
        assert "WARNING" in capsys.readouterr().out

    def test_gpu_subnet_falls_back_to_compute_subnets(self):
        # Production path: gpu_az_list=None (user didn't set --gpu_az),
        # no gpu_subnet_ids_override — expects gpu_subnet_ids == compute_subnet_ids.
        ec2 = _make_ec2client(
            subnets_by_az={"us-east-1a": [{"SubnetId": "subnet-aaa"}]}
        )
        _, _, compute_subnets, gpu_subnets, _, _ = _validate_network(
            ec2client=ec2, az="us-east-1a", vpc_name="vpc_default",
            headnode_subnet_id="subnet-hn",
            compute_az_list=["us-east-1a"],
            compute_subnet_ids_override="subnet-compute-1",
            use_private_compute_subnet="false",
            gpu_az_list=None,
        )
        assert gpu_subnets == compute_subnets

    def test_explicit_gpu_subnet_overrides_compute(self):
        ec2 = _make_ec2client(
            subnets_by_az={"us-east-1a": [{"SubnetId": "subnet-aaa"}]}
        )
        _, _, compute_subnets, gpu_subnets, _, _ = _validate_network(
            ec2client=ec2, az="us-east-1a", vpc_name="vpc_default",
            headnode_subnet_id="subnet-hn",
            compute_az_list=["us-east-1a"],
            compute_subnet_ids_override="subnet-compute-1",
            use_private_compute_subnet="false",
            gpu_subnet_ids_override="subnet-gpu-1,subnet-gpu-2",
        )
        assert gpu_subnets == ["subnet-gpu-1", "subnet-gpu-2"]
        assert gpu_subnets != compute_subnets


def _make_public_private_ec2client():
    """Fake EC2 client that honors the map-public-ip-on-launch filter.

    _make_ec2client above ignores it, so it cannot tell a private discovery from
    a public one -- which is precisely the distinction --use_private_gpu_subnet
    exists to make.
    """
    public = {"us-east-1a": [{"SubnetId": "subnet-public-a"}]}
    private = {"us-east-1a": [{"SubnetId": "subnet-private-a"}]}

    client = types.SimpleNamespace()
    client.describe_vpcs = lambda Filters: {
        "Vpcs": [{"VpcId": "vpc-abc12345", "CidrBlock": "10.0.0.0/16"}]
    }

    def describe_subnets(Filters):
        az = next(
            (f["Values"][0] for f in Filters if f["Name"] == "availabilityZone"), None
        )
        private_only = any(
            f["Name"] == "map-public-ip-on-launch" and f["Values"] == ["false"]
            for f in Filters
        )
        table = private if private_only else public
        return {"Subnets": table.get(az, [])}

    client.describe_subnets = describe_subnets
    return client


class TestPrivateGpuSubnetIsNotSilentlyIgnored:
    """--use_private_gpu_subnet must be honored without --gpu_az.

    The flag was read only inside `elif gpu_az_list:`, so with no --gpu_az and no
    --gpu_subnet_ids the `else:` arm copied compute's subnets verbatim and the
    flag did nothing. That is not a harmless default: a cluster built with
    --use_private_gpu_subnet=true and --use_private_compute_subnet left at its
    default put the entire GPU fleet on public subnets, which is the one outcome
    the flag was passed to prevent.

    Reusing compute's subnets stays correct when nothing distinguishes the two
    queues' placement -- that is the documented fallback and the common case.
    """

    _COMMON = dict(
        headnode_subnet_id="subnet-hn",
        compute_az_list=["us-east-1a"],
        compute_subnet_ids_override="",
    )

    def _call(self, **over):
        kwargs = dict(self._COMMON)
        kwargs.update(over)
        ec2 = _make_public_private_ec2client()
        _, _, compute_subnets, gpu_subnets, _, _ = _validate_network(
            ec2client=ec2, az="us-east-1a", vpc_name="vpc_default", **kwargs
        )
        return compute_subnets, gpu_subnets

    def test_private_gpu_without_gpu_az_discovers_a_private_subnet(self):
        compute, gpu = self._call(
            use_private_compute_subnet="false",
            gpu_az_list=None,
            use_private_gpu_subnet="true",
        )
        assert compute == ["subnet-public-a"]
        assert gpu == ["subnet-private-a"], (
            "--use_private_gpu_subnet=true was ignored: the GPU fleet landed on "
            "compute's public subnet"
        )

    def test_the_flag_reaches_a_discovery_not_just_a_different_list(self):
        """The private subnet must come from a filtered describe_subnets call."""
        ec2 = _make_public_private_ec2client()
        seen = []
        _inner = ec2.describe_subnets

        def _spy(Filters):
            seen.append(Filters)
            return _inner(Filters)

        ec2.describe_subnets = _spy
        _validate_network(
            ec2client=ec2, az="us-east-1a", vpc_name="vpc_default",
            headnode_subnet_id="subnet-hn",
            compute_az_list=["us-east-1a"],
            compute_subnet_ids_override="",
            use_private_compute_subnet="false",
            gpu_az_list=None,
            use_private_gpu_subnet="true",
        )
        assert any(
            any(
                f["Name"] == "map-public-ip-on-launch" and f["Values"] == ["false"]
                for f in filters
            )
            for filters in seen
        ), "no describe_subnets call filtered for private subnets"

    def test_the_default_still_reuses_computes_subnets(self):
        """Vacuity guard: the fix must not make every GPU queue discover its own.

        With the flag off, copying compute's subnets is the documented behavior
        and the only one that keeps a single-subnet cluster on one subnet.
        """
        compute, gpu = self._call(
            use_private_compute_subnet="false",
            gpu_az_list=None,
            use_private_gpu_subnet="false",
        )
        assert gpu == compute == ["subnet-public-a"]

    def test_private_compute_already_satisfies_a_private_gpu_request(self):
        """Both flags set: compute's discovered subnets are already private.

        Rediscovering would issue an identical query and, in an AZ with several
        private subnets, could pick a different one -- splitting the two queues
        for no reason.
        """
        compute, gpu = self._call(
            use_private_compute_subnet="true",
            gpu_az_list=None,
            use_private_gpu_subnet="true",
        )
        assert gpu == compute == ["subnet-private-a"]

    def test_explicit_compute_subnets_are_not_assumed_private(self):
        """--compute_subnet_ids says nothing about public vs private.

        use_private_compute_subnet is not consulted on that path, so its value
        cannot be taken as evidence about the subnets the operator named. A
        private GPU request must discover its own.
        """
        compute, gpu = self._call(
            compute_subnet_ids_override="subnet-operator-supplied",
            use_private_compute_subnet="true",
            gpu_az_list=None,
            use_private_gpu_subnet="true",
        )
        assert compute == ["subnet-operator-supplied"]
        assert gpu == ["subnet-private-a"]

    def test_explicit_gpu_subnets_still_win_over_the_flag(self):
        compute, gpu = self._call(
            use_private_compute_subnet="false",
            gpu_subnet_ids_override="subnet-gpu-explicit",
            gpu_az_list=None,
            use_private_gpu_subnet="true",
        )
        assert gpu == ["subnet-gpu-explicit"]

    def test_gpu_az_falls_back_to_compute_az_as_documented(self):
        """README.md: --gpu_az falls back to compute_az, then to --az.

        With --gpu_az unset and a private GPU request, discovery has to happen
        somewhere; compute_az_list is what the docs promise (and it already
        defaults to [az] in make_pcluster.py).
        """
        ec2 = _make_public_private_ec2client()
        seen = []
        _inner = ec2.describe_subnets

        def _spy(Filters):
            az = next(
                (f["Values"][0] for f in Filters if f["Name"] == "availabilityZone"),
                None,
            )
            private_only = any(
                f["Name"] == "map-public-ip-on-launch" for f in Filters
            )
            if private_only:
                seen.append(az)
            return _inner(Filters)

        ec2.describe_subnets = _spy
        _validate_network(
            ec2client=ec2, az="us-east-1a", vpc_name="vpc_default",
            headnode_subnet_id="subnet-hn",
            compute_az_list=["us-east-1a"],
            compute_subnet_ids_override="",
            use_private_compute_subnet="false",
            gpu_az_list=None,
            use_private_gpu_subnet="true",
        )
        assert seen == ["us-east-1a"]


# ---------------------------------------------------------------------------
# _get_efa_instance_types
# ---------------------------------------------------------------------------

_STATIC_FALLBACK = ["c5n.18xlarge", "p4d.24xlarge"]


class _FakeEC2ForEfa:
    def __init__(self, pages=None, raises=None):
        self._pages = pages if pages is not None else []
        self._raises = raises

    def get_paginator(self, operation):
        assert operation == "describe_instance_types"
        return self

    def paginate(self, **kwargs):
        if self._raises:
            raise self._raises
        return self._pages


class TestGetEfaInstanceTypes:
    def test_returns_live_types_when_api_succeeds(self):
        pages = [
            {"InstanceTypes": [{"InstanceType": "p5.48xlarge"}, {"InstanceType": "hpc7a.48xlarge"}]},
            {"InstanceTypes": [{"InstanceType": "c6gn.16xlarge"}]},
        ]
        ec2 = _FakeEC2ForEfa(pages=pages)
        result = _get_efa_instance_types(ec2, _STATIC_FALLBACK)
        assert result == {"p5.48xlarge", "hpc7a.48xlarge", "c6gn.16xlarge"}

    def test_falls_back_on_api_error(self, capsys):
        ec2 = _FakeEC2ForEfa(raises=Exception("AccessDenied"))
        result = _get_efa_instance_types(ec2, _STATIC_FALLBACK)
        assert result == set(_STATIC_FALLBACK)
        assert "built-in list" in capsys.readouterr().out

    def test_falls_back_on_empty_response(self, capsys):
        ec2 = _FakeEC2ForEfa(pages=[{"InstanceTypes": []}])
        result = _get_efa_instance_types(ec2, _STATIC_FALLBACK)
        assert result == set(_STATIC_FALLBACK)
        assert "built-in list" in capsys.readouterr().out

    def test_live_result_does_not_contain_fallback_types(self):
        pages = [{"InstanceTypes": [{"InstanceType": "p5.48xlarge"}]}]
        ec2 = _FakeEC2ForEfa(pages=pages)
        result = _get_efa_instance_types(ec2, _STATIC_FALLBACK)
        assert "c5n.18xlarge" not in result


# ---------------------------------------------------------------------------
# _ssh_secret_name
# ---------------------------------------------------------------------------


class TestSshSecretName:
    def test_returns_expected_path(self):
        result = _ssh_secret_name("mycluster", "mycluster-00305910072026")
        assert result == "parallelcluster/mycluster/mycluster-00305910072026/ssh-private-key"

    def test_cluster_name_and_serial_are_in_path(self):
        result = _ssh_secret_name("hpc-cluster", "hpc-cluster-99999911072026")
        assert "hpc-cluster" in result
        assert "hpc-cluster-99999911072026" in result
        assert result.startswith("parallelcluster/")
        assert result.endswith("/ssh-private-key")


class TestEveryRegionalBotoClientIsBoundToTheTargetRegion:
    """A regional boto3 client built without `region_name` resolves its
    endpoint from the ambient environment -- AWS_DEFAULT_REGION, AWS_REGION,
    or the active profile -- which need not be the region the build targets.

    That is not a latent tidiness issue. It shipped: `s3_client =
    boto3.client("s3")` in core_create_cluster sent CreateBucket to the
    operator's ambient region while the build targeted us-east-1, so the
    call correctly omitted LocationConstraint (right for us-east-1) and was
    rejected by the other region's endpoint with
    IllegalLocationConstraintException -- *after* four IAM policies and a
    role had been created, forcing the rollback path.

    Worse than the failure is the near-miss: the very next calls on that
    path create an EC2 keypair and a Secrets Manager secret. Had S3 not
    failed first, those would have been created in the wrong region and
    the build would have continued.

    iam and sts are global services and are deliberately exempt -- binding
    them to a region is meaningless, and requiring it would be cargo cult.
    """

    # Services whose endpoint is regional, so a client must say which one.
    _REGIONAL = {
        "s3", "ec2", "cloudformation", "logs", "secretsmanager", "lambda",
        "cognito-idp", "ce", "pricing", "fsx", "efs", "sns", "ssm",
        "resourcegroupstaggingapi", "application-autoscaling", "apigateway",
        # A registry is per-region, and its hostname carries the region --
        # an unbound client would create the repository in one region and
        # the Lambda would pull from another.
        "ecr",
    }
    # Global services: one endpoint, no region to bind.
    _GLOBAL = {"iam", "sts"}

    @staticmethod
    def _client_calls(path):
        import ast

        with open(path) as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not isinstance(fn, ast.Attribute):
                continue
            if fn.attr not in ("client", "resource"):
                continue
            if not (isinstance(fn.value, ast.Name) and fn.value.id == "boto3"):
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            service = node.args[0].value
            bound = any(k.arg == "region_name" for k in node.keywords)
            yield node.lineno, service, bound

    def _sources(self):
        import glob

        return sorted(
            glob.glob(os.path.join(REPO_ROOT, "src", "*.py"))
            + glob.glob(os.path.join(REPO_ROOT, "*.py"))
        )

    def test_no_regional_client_is_left_unbound(self):
        offenders = []
        for path in self._sources():
            for lineno, service, bound in self._client_calls(path):
                if service in self._REGIONAL and not bound:
                    offenders.append(
                        f"{os.path.relpath(path, REPO_ROOT)}:{lineno} "
                        f"boto3 {service} client has no region_name"
                    )
        assert offenders == [], offenders

    def test_the_service_lists_cover_every_client_actually_built(self):
        """Vacuity guard. A service in neither list is unchecked, and the
        obvious way to silence a failure above is to build a client for a
        name the check has never heard of."""
        unknown = set()
        for path in self._sources():
            for _lineno, service, _bound in self._client_calls(path):
                if service not in self._REGIONAL and service not in self._GLOBAL:
                    unknown.add(service)
        assert unknown == set(), (
            f"classify these services as regional or global: {sorted(unknown)}"
        )

    def test_the_scan_finds_the_clients_that_are_there(self):
        """Second vacuity guard: an AST walk that matched nothing would
        pass both checks above in silence."""
        found = [
            (svc, bound)
            for path in self._sources()
            for _l, svc, bound in self._client_calls(path)
        ]
        assert len(found) >= 10, found
        assert any(svc == "s3" and bound for svc, bound in found)

    def test_global_services_are_not_required_to_be_bound(self):
        """The exemption is real, not an oversight -- iam and sts clients
        exist unbound on purpose."""
        seen = [
            svc for path in self._sources()
            for _l, svc, bound in self._client_calls(path)
            if svc in self._GLOBAL and not bound
        ]
        assert seen, "expected at least one deliberately unbound global client"
