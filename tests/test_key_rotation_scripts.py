"""
Behavioral tests for the two shell scripts rotate_cluster_key.py runs over SSH.

These were the highest-severity survivors of the session-20 mutation
measurement: the scripts were Python string literals inside the entry point, so
nothing imported them and nothing ran them. A wrong grep flag or a deleted
assertion locks the operator out of their own head node, and the suite stayed
green. Both scripts now live in pcluster_core and take the authorized_keys path
as a parameter, so they can be executed under real bash against fixture files.
"""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcluster_core import _append_key_script, _remove_old_key_script

# Real ssh-ed25519 body shapes: base64 contains '+' and '/', both regex
# metacharacters, which is the whole reason the filter must be -F.
OLD_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI+old/KEYBODY0000000000000000000000 old@host"
NEW_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI+new/KEYBODY1111111111111111111111 new@host"
OTHER_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIunrelatedKEY22222222222222222222 someone@else"


def _prefix(key):
    return " ".join(key.split(" ")[:2])


def _akeys(tmp_path):
    d = tmp_path / ".ssh"
    d.mkdir()
    return d / "authorized_keys"


def _run(script, akeys, stdin=""):
    return subprocess.run(
        ["bash", "-c", script(str(akeys))],
        input=stdin.encode(),
        capture_output=True,
    )


class TestAppendKeyScript:
    def test_appends_to_a_file_with_a_trailing_newline(self, tmp_path):
        akeys = _akeys(tmp_path)
        akeys.write_text(OLD_KEY + "\n")
        r = _run(_append_key_script, akeys, NEW_KEY + "\n")
        assert r.returncode == 0, r.stderr
        assert akeys.read_text().splitlines() == [OLD_KEY, NEW_KEY]

    def test_does_not_concatenate_onto_an_unterminated_last_line(self, tmp_path):
        """The bug this guard exists for: a bare `cat >>` against a file with no
        trailing newline glues the new key onto the end of the old one, breaking
        both. The operator is then locked out and told the old key still works."""
        akeys = _akeys(tmp_path)
        akeys.write_text(OLD_KEY)
        r = _run(_append_key_script, akeys, NEW_KEY + "\n")
        assert r.returncode == 0, r.stderr
        assert akeys.read_text().splitlines() == [OLD_KEY, NEW_KEY]

    def test_creates_the_ssh_directory_and_file_when_absent(self, tmp_path):
        akeys = tmp_path / ".ssh" / "authorized_keys"
        r = _run(_append_key_script, akeys, NEW_KEY + "\n")
        assert r.returncode == 0, r.stderr
        assert akeys.read_text().splitlines() == [NEW_KEY]

    def test_empty_file_gains_no_leading_blank_line(self, tmp_path):
        akeys = _akeys(tmp_path)
        akeys.write_text("")
        r = _run(_append_key_script, akeys, NEW_KEY + "\n")
        assert r.returncode == 0, r.stderr
        assert akeys.read_text() == NEW_KEY + "\n"

    def test_permissions_are_tightened(self, tmp_path):
        akeys = _akeys(tmp_path)
        akeys.write_text(OLD_KEY + "\n")
        os.chmod(akeys, 0o644)
        os.chmod(akeys.parent, 0o755)
        assert _run(_append_key_script, akeys, NEW_KEY + "\n").returncode == 0
        assert oct(os.stat(akeys).st_mode & 0o777) == "0o600"
        assert oct(os.stat(akeys.parent).st_mode & 0o777) == "0o700"


class TestRemoveOldKeyScript:
    def _stdin(self, old=OLD_KEY, new=NEW_KEY):
        return old + "\n" + new + "\n"

    def test_revokes_the_old_key_and_keeps_the_new_one(self, tmp_path):
        akeys = _akeys(tmp_path)
        akeys.write_text(OLD_KEY + "\n" + NEW_KEY + "\n")
        r = _run(_remove_old_key_script, akeys, self._stdin())
        assert r.returncode == 0, r.stderr
        lines = akeys.read_text().splitlines()
        assert lines == [NEW_KEY]

    def test_matches_on_the_type_and_body_prefix_not_the_whole_line(self, tmp_path):
        """`ssh-keygen -y` emits no comment but the deployed line carries one, so
        a whole-line match filters nothing and the "old key absent" assertion
        passes vacuously — rotation reports success while revoking nothing."""
        akeys = _akeys(tmp_path)
        akeys.write_text(OLD_KEY + "\n" + NEW_KEY + "\n")
        # The old key as ssh-keygen -y produces it: no trailing comment.
        r = _run(_remove_old_key_script, akeys, self._stdin(old=_prefix(OLD_KEY)))
        assert r.returncode == 0, r.stderr
        assert akeys.read_text().splitlines() == [NEW_KEY]

    def test_unrelated_keys_are_preserved(self, tmp_path):
        akeys = _akeys(tmp_path)
        akeys.write_text(OTHER_KEY + "\n" + OLD_KEY + "\n" + NEW_KEY + "\n")
        r = _run(_remove_old_key_script, akeys, self._stdin())
        assert r.returncode == 0, r.stderr
        assert akeys.read_text().splitlines() == [OTHER_KEY, NEW_KEY]

    def test_key_prefix_is_matched_literally_not_as_a_regex(self, tmp_path):
        """`grep` defaults to basic regular expressions, in which the base64
        alphabet's `+` and `/` are literal — so for an ordinary generated key,
        dropping `-F` selects exactly the same lines and is undetectable. The
        difference only shows up when the matched text contains a real BRE
        metacharacter. Options fields and third-party key comments can, so the
        filter has to be literal regardless of what wrote the file.

        Here the old key's options field carries a `.`, which as a regex matches
        any character. Without `-F` the filter also revokes the unrelated key
        that differs from it only at that position."""
        akeys = _akeys(tmp_path)
        old = 'from="10.0.0.1" ' + OLD_KEY
        bystander = 'from="10x0.0.1" ' + OTHER_KEY
        akeys.write_text(bystander + "\n" + old + "\n" + NEW_KEY + "\n")

        r = _run(_remove_old_key_script, akeys, self._stdin(old=old))
        assert r.returncode == 0, r.stderr

        remaining = akeys.read_text().splitlines()
        assert bystander in remaining, (
            "an unrelated key was revoked because it matched the old key's "
            "prefix as a regular expression — the filter is not literal"
        )
        assert old not in remaining
        assert NEW_KEY in remaining

    def test_aborts_without_touching_the_file_when_the_new_key_is_absent(self, tmp_path):
        """The validate-then-mv ordering. If the candidate does not contain the
        new key, nothing may be moved into place — otherwise the operator is
        locked out with no working key at all."""
        akeys = _akeys(tmp_path)
        original = OLD_KEY + "\n"
        akeys.write_text(original)
        r = _run(_remove_old_key_script, akeys, self._stdin())
        assert r.returncode != 0
        assert b"new key missing from candidate" in r.stderr
        assert akeys.read_text() == original

    def test_aborts_when_old_and_new_are_the_same_key(self, tmp_path):
        """Rotating a key to itself: the filter strips the only authorized line,
        so the candidate authorizes nobody. It must not be moved into place."""
        akeys = _akeys(tmp_path)
        original = OLD_KEY + "\n"
        akeys.write_text(original)
        r = _run(_remove_old_key_script, akeys, self._stdin(new=OLD_KEY))
        assert r.returncode != 0
        assert akeys.read_text() == original

    def test_old_key_absence_is_asserted_before_the_file_is_replaced(self):
        """This guard is unreachable from any input — `grep -vF` always removes
        every matching line — so it only fires when the filter itself produced
        garbage (short write, killed process, full disk). There is no input that
        exercises it, which is exactly why its deletion needs a structural
        assertion: all three checks must precede the `mv`."""
        script = _remove_old_key_script("/tmp/akeys")
        mv = script.index("mv ")
        for guard, why in (
            ('[ -n "$OLDPFX" ]', "unparseable key material"),
            ('grep -qF "$NEWPFX"', "new key present in candidate"),
            ('if grep -qF "$OLDPFX"', "old key absent from candidate"),
        ):
            assert guard in script, f"missing guard: {why}"
            assert script.index(guard) < mv, (
                f"the {why} check must run before the candidate replaces the "
                f"live authorized_keys file"
            )

    def test_aborts_on_unparseable_key_material(self, tmp_path):
        akeys = _akeys(tmp_path)
        original = OLD_KEY + "\n" + NEW_KEY + "\n"
        akeys.write_text(original)
        r = _run(_remove_old_key_script, akeys, "\n\n")
        assert r.returncode != 0
        assert b"could not parse key material" in r.stderr
        assert akeys.read_text() == original

    def test_leaves_no_temporary_file_behind_on_abort(self, tmp_path):
        akeys = _akeys(tmp_path)
        akeys.write_text(OLD_KEY + "\n")
        assert _run(_remove_old_key_script, akeys, self._stdin()).returncode != 0
        leftovers = [p.name for p in akeys.parent.iterdir() if p.name != "authorized_keys"]
        assert not leftovers, f"temp files left in .ssh: {leftovers}"

    def test_replacement_file_is_mode_600(self, tmp_path):
        akeys = _akeys(tmp_path)
        akeys.write_text(OLD_KEY + "\n" + NEW_KEY + "\n")
        assert _run(_remove_old_key_script, akeys, self._stdin()).returncode == 0
        assert oct(os.stat(akeys).st_mode & 0o777) == "0o600"


class TestRotateEntryPointUsesTheSharedScripts:
    """As of the Workstream 1 migration (docs/parallelclustermaker-mcp-plan.md),
    the actual key-rotation orchestration -- including the calls to
    _append_key_script/_remove_old_key_script -- lives entirely in
    pcluster_core.core_rotate_cluster_key, not in rotate_cluster_key.py
    itself. The risk this class originally guarded against (a duplicated
    inline copy of either script, invisible to every test in this file) is
    now checked one level up: core_rotate_cluster_key must call the real,
    tested functions, and the CLI shim must delegate to core_rotate_cluster_key
    rather than carrying its own copy of the orchestration."""

    def test_core_rotate_cluster_key_does_not_carry_its_own_copies(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "src", "pcluster_core.py",
        )
        with open(path) as fh:
            source = fh.read()
        assert "_append_key_script()" in source
        assert "_remove_old_key_script()" in source
        assert "authorized_keys > " not in source, (
            "pcluster_core.py appears to inline an authorized_keys filter "
            "again instead of calling the tested _append_key_script/"
            "_remove_old_key_script versions"
        )

    def test_rotate_cluster_key_delegates_to_the_core_function(self):
        """The CLI shim must not carry its own copy of the rotation
        orchestration -- it should just call core_rotate_cluster_key."""
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "rotate_cluster_key.py",
        )
        with open(path) as fh:
            source = fh.read()
        assert "core_rotate_cluster_key(" in source
        assert "_append_key_script" not in source, (
            "rotate_cluster_key.py appears to call the shared scripts "
            "directly again instead of going through core_rotate_cluster_key"
        )
        assert "authorized_keys > " not in source


# ---------------------------------------------------------------------------
# core_rotate_cluster_key -- the orchestration function added in the
# Workstream 1 migration. High-consequence (real key material, real AWS
# mutations in the original script), so covered more thoroughly than most
# core functions in this series: every raise path, the two non-fatal
# warning-only failure modes, and the full happy-path call sequence.
# ---------------------------------------------------------------------------

import types

import pcluster_core
from pcluster_core import ClusterRecord, PClusterMakerError, core_rotate_cluster_key

_RECORD_KWARGS = {
    "cluster_name": "keycluster",
    "cluster_owner": "rmarable",
    "serial": "202608200001",
    "region": "us-east-1",
    "headnode_instance_type": "c5.xlarge",
    "enable_loginnode": "false",
    "loginnode_instance_type": "",
    "loginnode_count": 0,
    "cpu_instance_types": ["c5.xlarge"],
    "gpu_instance_types": [],
    "enable_cpu_queue": "true",
    "enable_gpu_queue": "false",
    "initial_cpu_queue_size": 2,
    "max_cpu_queue_size": 8,
    "initial_gpu_queue_size": 0,
    "max_gpu_queue_size": 0,
    "cluster_type": "ondemand",
    "deployment_date": "2026-08-20",
    "ssh_keypair": "",
    "ec2_keypair": "keycluster-keypair",
    "ec2_user": "ubuntu",
    "s3_bucketname": "my-bucket",
    "enable_monitoring": "false",
}


def _record(**overrides):
    return ClusterRecord(**{**_RECORD_KWARGS, **overrides})


class _FakeEc2:
    def __init__(self, head_ip="1.2.3.4", describe_raises=None):
        self.head_ip = head_ip
        self.describe_raises = describe_raises
        self.import_calls = []
        self.delete_calls = []
        self.duplicate_once_for = None  # keypair name that fails once with Duplicate

    def describe_instances(self, **kwargs):
        if self.describe_raises:
            raise self.describe_raises
        if not self.head_ip:
            return {"Reservations": []}
        return {"Reservations": [{"Instances": [{"PublicIpAddress": self.head_ip}]}]}

    def import_key_pair(self, KeyName, PublicKeyMaterial):
        self.import_calls.append(KeyName)
        if KeyName == self.duplicate_once_for and self.import_calls.count(KeyName) == 1:
            from botocore.exceptions import ClientError
            raise ClientError(
                {"Error": {"Code": "InvalidKeyPair.Duplicate", "Message": "dup"}}, "ImportKeyPair"
            )

    def delete_key_pair(self, KeyName):
        self.delete_calls.append(KeyName)


class _FakeSecretsManager:
    def __init__(self):
        self.put_calls = []

    def put_secret_value(self, SecretId, SecretString):
        self.put_calls.append((SecretId, SecretString))


class _FakeRotationSubprocess:
    """Dispatches subprocess.run calls by argv shape and call order. Real
    ssh-keygen keypair generation is faked by writing placeholder files to
    the -f path, since core_rotate_cluster_key opens and reads them back.
    Add/remove authorized_keys steps are distinguished by order (add always
    precedes the verify step, remove always follows it), not content --
    both pass an opaque shell script as the remote command."""

    def __init__(self, old_pub_key="ssh-ed25519 OLDKEY old@host",
                 new_pub_key="ssh-ed25519 NEWKEY new@host",
                 verify_fails=False, remove_fails=False):
        self.old_pub_key = old_pub_key
        self.new_pub_key = new_pub_key
        self.verify_fails = verify_fails
        self.remove_fails = remove_fails
        self.calls = []
        self._verify_seen = False

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))

        if args[0] == "which":
            return types.SimpleNamespace(returncode=0)

        if args[:2] == ["ssh-keygen", "-y"]:
            return types.SimpleNamespace(returncode=0, stdout=self.old_pub_key + "\n", stderr="")

        if args[:3] == ["ssh-keygen", "-t", "ed25519"]:
            key_path = args[args.index("-f") + 1]
            with open(key_path, "w") as f:
                f.write("FAKE PRIVATE KEY\n")
            with open(key_path + ".pub", "w") as f:
                f.write(self.new_pub_key + "\n")
            return types.SimpleNamespace(returncode=0)

        if args[0] == "ssh":
            remote_cmd = args[-1]
            if remote_cmd == "true":
                self._verify_seen = True
                if self.verify_fails:
                    raise subprocess.CalledProcessError(255, args)
                return types.SimpleNamespace(returncode=0)
            # add or remove -- distinguished by order
            if not self._verify_seen:
                return types.SimpleNamespace(returncode=0)  # add
            if self.remove_fails:
                raise subprocess.CalledProcessError(1, args)
            return types.SimpleNamespace(returncode=0)  # remove

        raise AssertionError(f"unexpected subprocess call: {args}")


def _stage(monkeypatch, ec2=None, sm=None, sub=None):
    ec2 = ec2 or _FakeEc2()
    sm = sm or _FakeSecretsManager()
    sub = sub if sub is not None else _FakeRotationSubprocess()
    monkeypatch.setattr(pcluster_core.boto3, "client", lambda service, **kw: {"ec2": ec2, "secretsmanager": sm}[service])
    monkeypatch.setattr(pcluster_core.subprocess, "run", sub)
    return ec2, sm, sub


class TestCoreRotateClusterKey:
    def test_missing_serial_raises(self, monkeypatch):
        _stage(monkeypatch)
        with pytest.raises(PClusterMakerError, match="cluster_serial_number or ec2_keypair"):
            core_rotate_cluster_key(cluster_record=_record(serial=""), region="us-east-1")

    def test_missing_ec2_keypair_raises(self, monkeypatch):
        _stage(monkeypatch)
        with pytest.raises(PClusterMakerError, match="cluster_serial_number or ec2_keypair"):
            core_rotate_cluster_key(cluster_record=_record(ec2_keypair=""), region="us-east-1")

    def test_describe_instances_failure_raises(self, monkeypatch):
        from botocore.exceptions import ClientError
        _stage(monkeypatch, ec2=_FakeEc2(describe_raises=ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "no"}}, "DescribeInstances"
        )))
        with pytest.raises(PClusterMakerError, match="Could not describe EC2 instances"):
            core_rotate_cluster_key(cluster_record=_record(), region="us-east-1")

    def test_no_running_head_node_raises(self, monkeypatch):
        _stage(monkeypatch, ec2=_FakeEc2(head_ip=""))
        with pytest.raises(PClusterMakerError, match="No running head node"):
            core_rotate_cluster_key(cluster_record=_record(), region="us-east-1")

    def test_dry_run_makes_no_mutations(self, monkeypatch):
        ec2, sm, sub = _stage(monkeypatch)
        result = core_rotate_cluster_key(cluster_record=_record(), region="us-east-1", dry_run=True)
        assert result.dry_run is True
        assert ec2.import_calls == []
        assert ec2.delete_calls == []
        assert sm.put_calls == []

    def test_new_key_auth_failure_raises_and_touches_no_aws_resources(self, monkeypatch):
        ec2, sm, sub = _stage(monkeypatch, sub=_FakeRotationSubprocess(verify_fails=True))
        with pytest.raises(PClusterMakerError, match="new key failed to authenticate"):
            core_rotate_cluster_key(cluster_record=_record(), region="us-east-1")
        assert ec2.import_calls == []
        assert sm.put_calls == []

    def test_remove_old_key_failure_raises_with_no_aws_changes_message(self, monkeypatch):
        ec2, sm, sub = _stage(
            monkeypatch,
            ec2=_FakeEc2(),
            sub=_FakeRotationSubprocess(remove_fails=True),
        )
        # Need an existing ssh_keypair file so old_pub_key is captured, or
        # the remove step never runs at all.
        with pytest.raises(PClusterMakerError, match="could not safely remove the old key"):
            core_rotate_cluster_key(
                cluster_record=_record(ssh_keypair=__file__),  # any real file
                region="us-east-1",
            )
        assert ec2.import_calls == []
        assert sm.put_calls == []

    def test_happy_path_full_sequence(self, monkeypatch):
        ec2, sm, sub = _stage(monkeypatch)
        result = core_rotate_cluster_key(cluster_record=_record(), region="us-east-1")
        assert result.dry_run is False
        assert result.cluster_name == "keycluster"
        assert result.head_ip == "1.2.3.4"
        assert result.old_keypair_deleted is True
        # No ssh_keypair set in the default record -> nothing to overwrite locally.
        assert result.local_key_path_updated is False
        assert ec2.import_calls == ["keycluster-keypair-rotated", "keycluster-keypair"]
        assert ec2.delete_calls == ["keycluster-keypair", "keycluster-keypair-rotated"]
        assert sm.put_calls[0][0] == result.secret_name

    def test_local_key_write_failure_does_not_abort_rotation(self, monkeypatch, tmp_path):
        # A directory in place of the target file makes os.open() raise OSError.
        bad_path = tmp_path / "not_a_file"
        bad_path.mkdir()
        ec2, sm, sub = _stage(monkeypatch)
        result = core_rotate_cluster_key(
            cluster_record=_record(ssh_keypair=str(bad_path)), region="us-east-1",
        )
        assert result.local_key_path_updated is False
        # Rotation still completed -- the secret was still updated.
        assert sm.put_calls

    def test_old_keypair_delete_failure_does_not_abort_rotation(self, monkeypatch):
        from botocore.exceptions import ClientError

        class _FlakyDeleteEc2(_FakeEc2):
            def delete_key_pair(self, KeyName):
                if KeyName == "keycluster-keypair" and "keycluster-keypair" not in self.delete_calls:
                    self.delete_calls.append(KeyName)
                    raise ClientError({"Error": {"Code": "Other", "Message": "boom"}}, "DeleteKeyPair")
                super().delete_key_pair(KeyName)

        ec2, sm, sub = _stage(monkeypatch, ec2=_FlakyDeleteEc2())
        result = core_rotate_cluster_key(cluster_record=_record(), region="us-east-1")
        assert result.old_keypair_deleted is False
        # Rotation still completed the rename step afterward.
        assert "keycluster-keypair" in ec2.import_calls


class TestImportEc2Keypair:
    def test_success_imports_once(self):
        ec2 = _FakeEc2()
        pcluster_core._import_ec2_keypair(ec2, "mykey", "ssh-ed25519 XXX")
        assert ec2.import_calls == ["mykey"]

    def test_duplicate_name_deletes_then_reimports(self):
        ec2 = _FakeEc2()
        ec2.duplicate_once_for = "mykey"
        pcluster_core._import_ec2_keypair(ec2, "mykey", "ssh-ed25519 XXX")
        assert ec2.import_calls == ["mykey", "mykey"]
        assert ec2.delete_calls == ["mykey"]

    def test_other_client_error_propagates(self):
        from botocore.exceptions import ClientError

        class _FailingEc2(_FakeEc2):
            def import_key_pair(self, KeyName, PublicKeyMaterial):
                raise ClientError({"Error": {"Code": "Other", "Message": "boom"}}, "ImportKeyPair")

        with pytest.raises(ClientError):
            pcluster_core._import_ec2_keypair(_FailingEc2(), "mykey", "ssh-ed25519 XXX")
