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
    def test_rotate_cluster_key_does_not_carry_its_own_copies(self):
        """The scripts were duplicated inside the entry point once; a copy there
        is invisible to every test in this file."""
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "rotate_cluster_key.py",
        )
        with open(path) as fh:
            source = fh.read()
        assert "_append_key_script()" in source
        assert "_remove_old_key_script()" in source
        assert "authorized_keys > " not in source, (
            "rotate_cluster_key.py appears to inline an authorized_keys filter "
            "again instead of calling the tested pcluster_core version"
        )
