"""Running Slurm commands on a cluster node.

The connection is deliberately not reimplemented here: `access_cluster.
<name>.sh` already carries the SSM ProxyCommand, the plugin-absent
fallback, key retrieval and the rc/stderr diagnosis that tells a failed
AWS call from a stopped node. `core_ensure_generated_script`'s own
docstring says a second copy of that in Python would drift, and an earlier
draft of this feature was that copy -- it had no ProxyCommand, so every
private-subnet cluster would have failed.
"""

import base64
import os
import shlex
import subprocess

import pytest

from pcluster_core import (
    PClusterMakerError,
    SlurmCommandResult,
    _sbatch_script_payload,
    _validate_slurm_command,
    core_run_slurm_command,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestTheReadOnlyToolCannotWrite:
    """The whole point of the split. Each of these is an escape someone
    proposed or an adversarial review found."""

    @pytest.mark.parametrize("command", ["sbatch", "scancel"])
    def test_a_write_command_is_refused_outright(self, command):
        argv, err = _validate_slurm_command(command, "", "x", allow_writes=False)
        assert argv is None and "not available here" in err

    def test_scontrol_show_is_emitted_not_inferred(self):
        """Deciding "is this a read?" by scanning for the first non-flag
        token reimplements getopt against a parser we do not control, and
        scontrol's -M takes a separate-word value -- so
        `-M show update ...` reads as `show` to such a scanner while
        scontrol executes `update`. Writing `show` ourselves puts it
        first, where nothing can displace it."""
        argv, err = _validate_slurm_command(
            "scontrol", "nodes", None, allow_writes=False)
        assert argv[:2] == ["scontrol", "show"], argv

    def test_a_write_subcommand_cannot_lead(self):
        argv, err = _validate_slurm_command(
            "scontrol", "update NodeName=x State=DRAIN", None, allow_writes=False)
        assert argv is not None
        assert argv[1] == "show", "a caller token displaced the emitted subcommand"

    @pytest.mark.parametrize("switches", [
        "-M other update NodeName=x",
        "--cluster other update NodeName=x",
        "--cluster=other update NodeName=x",
    ])
    def test_the_cluster_redirect_is_refused(self, switches):
        """-M is the switch that makes the getopt disagreement possible,
        and it points the query at a different cluster than the one this
        tool was called for."""
        argv, err = _validate_slurm_command(
            "scontrol", switches, None, allow_writes=False)
        assert argv is None and "cluster" in err.lower()

    @pytest.mark.parametrize("command,switches", [
        ("sinfo", "-N -l"),
        ("squeue", "--format='%.18i %.9P'"),
        ("scontrol", "partitions"),
    ])
    def test_real_read_commands_still_work(self, command, switches):
        """The vacuity guard: refusing everything would satisfy every test
        above."""
        argv, err = _validate_slurm_command(
            command, switches, None, allow_writes=False)
        assert err is None, err
        assert argv[0] == command


class TestTheWriteToolBoundsWhatItCan:
    def test_sbatch_without_a_script_is_refused(self):
        """sbatch with no script argument reads from *stdin*. On the local
        stdio server the inherited stdin is the MCP JSON-RPC channel, so
        this would submit the client's protocol stream as a job."""
        argv, err = _validate_slurm_command("sbatch", "", None, allow_writes=True)
        assert argv is None and "standard input" in err

    @pytest.mark.parametrize("switches", ["--wrap=rm -rf /", "--wrap 'id'"])
    def test_sbatch_wrap_is_refused(self, switches):
        """--wrap submits an arbitrary command with no script file, which
        would make *switches* an arbitrary-code channel while `script` is
        merely the documented one. Exactly one parameter carries code."""
        argv, err = _validate_slurm_command(
            "sbatch", switches, "#!/bin/bash\ntrue", allow_writes=True)
        assert argv is None and "wrap" in err.lower()

    @pytest.mark.parametrize("switches", [
        "-u someone", "--user=someone", "--state=PENDING", "-t RUNNING",
        "--partition=compute", "-p compute", "--qos=high", "--name=job",
    ])
    def test_bulk_cancel_filters_are_refused(self, switches):
        """These cancel *sets* of jobs. The commands here are composed by a
        model, not typed by an operator who would have inspected them."""
        argv, err = _validate_slurm_command(
            "scancel", switches, None, allow_writes=True)
        assert argv is None and "set of jobs" in err

    def test_scancel_needs_an_explicit_job(self):
        argv, err = _validate_slurm_command("scancel", "", None, allow_writes=True)
        assert argv is None

    def test_scancel_by_id_works(self):
        argv, err = _validate_slurm_command(
            "scancel", "12345", None, allow_writes=True)
        assert err is None and argv == ["scancel", "12345"]

    def test_bare_scontrol_is_refused(self):
        """It is interactive and would hang until the timeout."""
        argv, err = _validate_slurm_command("scontrol", "", None, allow_writes=True)
        assert argv is None and "interactive" in err


class TestTheSbatchPayloadIsCorrectShell:
    """Executed under real bash, because every claim here is about what a
    shell does with the string."""

    def _run(self, payload, fake_sbatch_dir):
        return subprocess.run(
            ["bash", "-c", payload], capture_output=True, text=True,
            env=dict(os.environ, PATH=f"{fake_sbatch_dir}:{os.environ['PATH']}",
                     HOME=fake_sbatch_dir),
        )

    @pytest.fixture
    def fake_sbatch(self, tmp_path):
        p = tmp_path / "sbatch"
        p.write_text('#!/bin/bash\necho "Submitted batch job 4242"\ncat "$1"\n')
        p.chmod(0o755)
        return str(tmp_path)

    def test_a_script_containing_every_shell_metacharacter_round_trips(
            self, fake_sbatch):
        """Base64 rather than a heredoc: the alphabet contains no
        shell-meaningful character, so terminator collision, parameter
        expansion and line structure cease to be problems rather than
        being escaped around."""
        body = "#!/bin/bash\nEOF\n$(whoami) `id` 'x' \"y\" ; | & > <\nEOF\n"
        payload = _sbatch_script_payload(["sbatch"], body)
        r = self._run(payload, fake_sbatch)
        assert r.returncode == 0, r.stderr
        assert "Submitted batch job 4242" in r.stdout
        assert "$(whoami)" in r.stdout, "the body was expanded, not transmitted"
        assert "EOF" in r.stdout

    def test_no_heredoc_appears(self):
        """The property, not an example. A round-trip test using the
        literal 'EOF' passes against any heredoc a real person would write,
        since they would choose a longer terminator."""
        payload = _sbatch_script_payload(["sbatch"], "#!/bin/bash\ntrue\n")
        assert "<<" not in payload

    def test_it_uses_the_linux_base64_flag(self):
        """macOS accepts -D and Linux does not, so the wrong spelling
        passes on the developer's machine and fails on every node."""
        payload = _sbatch_script_payload(["sbatch"], "x")
        assert "base64 -d" in payload
        assert "base64 -D" not in payload

    def test_crlf_is_normalized(self):
        payload = _sbatch_script_payload(["sbatch"], "#!/bin/bash\r\ntrue\r\n")
        b64 = payload.split("printf %s ")[1].split(" |")[0].strip("'")
        assert b"\r" not in base64.b64decode(b64)

    def test_the_temp_file_is_removed_even_when_sbatch_fails(self, tmp_path):
        """Chaining cleanup behind && leaves the file when decoding or
        submission fails, and returns the wrong command's exit status."""
        fail = tmp_path / "sbatch"
        fail.write_text('#!/bin/bash\nexit 7\n')
        fail.chmod(0o755)
        payload = _sbatch_script_payload(["sbatch"], "x")
        r = subprocess.run(
            ["bash", "-c", payload], capture_output=True, text=True,
            env=dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}",
                     HOME=str(tmp_path)),
        )
        assert r.returncode == 7, "sbatch's exit status was not propagated"
        assert not list(tmp_path.glob(".pcm-sbatch-*")), "the temp file leaked"


class TestTheConnectionIsNotReimplemented:
    _REC = {"enable_loginnode": "true", "loginnode_count": 2,
            "region": "us-east-1", "cluster_name": "c"}

    def _call(self, monkeypatch, tmp_path, **kw):
        seen = {}

        def _runner(argv, env, timeout):
            seen["argv"] = argv
            seen["env"] = env
            seen["timeout"] = timeout
            return 0, kw.pop("stdout", ""), ""

        monkeypatch.setattr("pcluster_core.core_ensure_generated_script",
                            lambda **k: None)
        monkeypatch.setattr("pcluster_core._resolve_access_script_path",
                            lambda root, name: str(tmp_path / "access.sh"))
        res = core_run_slurm_command(
            cluster_data_root=str(tmp_path), cluster_name="c",
            cluster_record=self._REC, repo_root=REPO_ROOT,
            runner=_runner, **kw,
        )
        return res, seen

    def test_it_invokes_the_generated_script(self, monkeypatch, tmp_path):
        res, seen = self._call(monkeypatch, tmp_path, command="sinfo",
                               switches="-N", allow_writes=False)
        assert seen["argv"][0] == "bash"
        assert seen["argv"][1].endswith("access.sh")

    def test_the_node_type_reaches_the_script(self, monkeypatch, tmp_path):
        """ACCESS_NODE_TYPE is the script's own interface for this."""
        res, seen = self._call(monkeypatch, tmp_path, command="sinfo",
                               allow_writes=False)
        assert seen["env"]["ACCESS_NODE_TYPE"] == "LoginNode"
        assert res.node_type == "LoginNode"

    def test_a_cluster_with_no_login_pool_uses_the_head_node(
            self, monkeypatch, tmp_path):
        rec = dict(self._REC, enable_loginnode="false", loginnode_count=0)
        monkeypatch.setattr("pcluster_core.core_ensure_generated_script",
                            lambda **k: None)
        monkeypatch.setattr("pcluster_core._resolve_access_script_path",
                            lambda root, name: str(tmp_path / "a.sh"))
        res = core_run_slurm_command(
            cluster_data_root=str(tmp_path), cluster_name="c",
            cluster_record=rec, repo_root=REPO_ROOT, command="sinfo",
            allow_writes=False, runner=lambda a, e, t: (0, "", ""),
        )
        assert res.node_type == "HeadNode"

    def test_the_remote_command_carries_the_slurm_path(self, monkeypatch, tmp_path):
        """A non-interactive shell has no /opt/slurm/bin on PATH, so sinfo
        exits 127. Asserted on the rendered string, not by patching
        _slurm_remote_cmd to observe the call -- patching would stub the
        thing under test."""
        res, seen = self._call(monkeypatch, tmp_path, command="sinfo",
                               allow_writes=False)
        joined = " ".join(seen["argv"])
        assert "/opt/slurm/bin" in joined

    def test_a_metacharacter_is_transmitted_as_data_not_executed(
            self, monkeypatch, tmp_path):
        """shlex.quote makes it an argument rather than syntax. An earlier
        version refused these outright, which was wrong in both
        directions: it blocked `sinfo -o "%P|%a|%D"` -- the standard
        machine-readable idiom, where the pipe is a field separator -- and
        it implied the quoting was not trusted."""
        res, seen = self._call(
            monkeypatch, tmp_path, command="squeue",
            switches="--format=%P|%a|%D", allow_writes=False)
        assert res.command.startswith("squeue")
        joined = " ".join(seen["argv"])
        assert "%P|%a|%D" in joined

    def test_real_switches_are_not_caught_by_that(self, monkeypatch, tmp_path):
        res, seen = self._call(
            monkeypatch, tmp_path, command="squeue",
            switches="--format=%.18i %.9P", allow_writes=False)
        assert res.command.startswith("squeue")


class TestWhatTheCallerIsTold:
    _REC = {"enable_loginnode": "false", "loginnode_count": 0}

    def _run(self, monkeypatch, tmp_path, runner, **kw):
        monkeypatch.setattr("pcluster_core.core_ensure_generated_script",
                            lambda **k: None)
        monkeypatch.setattr("pcluster_core._resolve_access_script_path",
                            lambda root, name: str(tmp_path / "a.sh"))
        return core_run_slurm_command(
            cluster_data_root=str(tmp_path), cluster_name="c",
            cluster_record=self._REC, repo_root=REPO_ROOT, runner=runner, **kw)

    def test_a_slurm_rejection_is_a_result_not_an_exception(
            self, monkeypatch, tmp_path):
        """Raising would make squeue on an empty queue indistinguishable
        from a broken cluster."""
        res = self._run(monkeypatch, tmp_path,
                        lambda a, e, t: (1, "", "sbatch: error: invalid partition"),
                        command="squeue", allow_writes=False)
        assert res.success is False
        assert res.exit_code == 1
        assert "invalid partition" in res.stderr

    def test_stdout_and_stderr_stay_separate(self, monkeypatch, tmp_path):
        res = self._run(monkeypatch, tmp_path, lambda a, e, t: (0, "OUT", "ERR"),
                        command="sinfo", allow_writes=False)
        assert res.stdout == "OUT" and res.stderr == "ERR"

    def test_the_job_id_is_parsed(self, monkeypatch, tmp_path):
        """The entire point of calling sbatch is to learn it; making every
        caller regex the stdout of a tool that knows it ran sbatch is work
        with no purpose."""
        res = self._run(monkeypatch, tmp_path,
                        lambda a, e, t: (0, "Submitted batch job 98765\n", ""),
                        command="sbatch", script="#!/bin/bash\ntrue",
                        allow_writes=True)
        assert res.job_id == "98765"

    def test_a_non_sbatch_command_has_no_job_id(self, monkeypatch, tmp_path):
        res = self._run(monkeypatch, tmp_path, lambda a, e, t: (0, "x", ""),
                        command="sinfo", allow_writes=False)
        assert res.job_id is None

    def test_a_timeout_says_the_state_is_unknown(self, monkeypatch, tmp_path):
        """The local process is killed; the submission may already have
        reached slurmctld. Reporting a plain failure invites a retry that
        submits a second job -- the same hazard the 29s gateway ceiling
        has, reproduced locally."""
        def _boom(a, e, t):
            raise subprocess.TimeoutExpired(cmd="x", timeout=t)

        res = self._run(monkeypatch, tmp_path, _boom, command="sbatch",
                        script="#!/bin/bash\ntrue", allow_writes=True)
        assert res.timed_out is True
        assert res.success is False
        assert "unknown" in res.stderr
        assert "squeue" in res.stderr

    def test_an_invalid_command_raises_rather_than_returning(
            self, monkeypatch, tmp_path):
        with pytest.raises(PClusterMakerError):
            self._run(monkeypatch, tmp_path, lambda a, e, t: (0, "", ""),
                      command="rm", allow_writes=True)


class TestStdinIsClosed:
    """sbatch and a bare scontrol read from stdin. On the local stdio
    server the inherited stdin is the MCP JSON-RPC channel, so leaving it
    open feeds the client's protocol stream to Slurm."""

    def test_the_real_runner_closes_stdin(self):
        """Drives the real _run_access_script rather than the injected
        seam -- every other test here stubs it, and the repo's rule is that
        when a test stubs the object under test, one must drive the real
        one."""
        from pcluster_core import _run_access_script

        rc, out, err = _run_access_script(
            ["bash", "-c", "cat; echo done"], dict(os.environ), 10)
        assert rc == 0
        assert out.strip() == "done", "stdin was not closed -- cat read something"


class TestOnlyTheReadToolIsRemote:
    def test_the_write_tool_stays_local(self):
        """sbatch is arbitrary code by design. Porting the read half is
        exactly when the write half gets carried along by accident."""
        import mcp_server.tools as t
        from mcp_server.tiers import TOOL_TIERS

        assert "run_readwrite_slurm_command" in t._LOCAL_ONLY
        assert "run_readwrite_slurm_command" not in TOOL_TIERS

    def test_the_read_tool_is_remote(self):
        import mcp_server.tools as t
        from mcp_server.tiers import TOOL_TIERS

        assert "run_readonly_slurm_command" not in t._LOCAL_ONLY
        assert TOOL_TIERS["run_readonly_slurm_command"] == "read-only"


class TestTheAccessScriptKeepsStdoutClean:
    def test_no_progress_message_goes_to_stdout(self):
        """Three echoes announced the connection on stdout, which would
        contaminate captured Slurm output. An earlier draft of this plan
        identified two of them and missed the key-retrieval one."""
        src = open(os.path.join(REPO_ROOT, "templates",
                                "access_cluster.j2")).read()
        for line in src.splitlines():
            st = line.strip()
            if st.startswith("echo ") and ">&2" not in st:
                pytest.fail(f"writes to stdout: {st}")


class TestTheSSMDocumentIsTheBoundary:
    """The document's constraints are enforced by SSM before dispatch --
    verified live against AWS, not assumed. That is what makes them bound
    a caller holding the tier's role, rather than merely bounding our own
    code, and it is why the read-only tool can be remote at all."""

    @staticmethod
    def _doc():
        import json
        return json.load(open(os.path.join(
            REPO_ROOT, "templates", "ssm_slurm_readonly.json")))

    def test_the_command_set_is_closed(self):
        vals = self._doc()["parameters"]["command"]["allowedValues"]
        assert set(vals) == {"sinfo", "squeue", "scontrol"}

    def test_the_user_set_is_closed_to_real_login_users(self):
        from pcluster_core import _VALID_EC2_USERS

        vals = self._doc()["parameters"]["user"]["allowedValues"]
        assert set(vals) == _VALID_EC2_USERS

    def test_the_switch_parameter_admits_only_base64(self):
        """The parameter cannot carry a shell metacharacter because the
        base64 alphabet has none. That is stronger than enumerating
        dangerous characters: there is nothing to enumerate."""
        import re as _re

        pat = self._doc()["parameters"]["switchesB64"]["allowedPattern"]
        assert pat == "^[A-Za-z0-9+/=]*$", pat
        c = _re.compile(pat)
        for meta in list(";|&$`><'\"\\()*?{}!#~ ") + ["\n"]:
            assert not c.fullmatch(meta), f"{meta!r} is not base64"

    def test_the_switches_reach_the_command_through_an_array(self):
        """Decoded values go into a bash array, and an expanded variable is
        never re-parsed -- so a metacharacter inside one is a literal
        argument, not syntax. Substituting the decoded text into the
        command line directly would undo that."""
        body = "\n".join(self._doc()["mainSteps"][0]["inputs"]["runCommand"])
        assert "mapfile -t ARGS" in body
        assert 'ARGS[@]+"${ARGS[@]}"' in body, (
            "the array must be expanded quoted, and guarded for the empty "
            "case -- an unset array under set -u is an error before bash 4.4"
        )

    @pytest.mark.parametrize("switches", [
        "-N -l", "--format=%.18i %.9P", "-n node-[1-4]", "--nodes=gpu[01-04]",
        "-p compute,gpu", "--states=IDLE,DOWN", "-j 1,2,3", "nodes",
    ])
    def test_real_slurm_switches_survive_the_encoding(self, switches):
        """The vacuity guard, and the case that live testing found: a
        charset that excluded `|` rejected `sinfo -o "%P|%a|%D"`, which is
        the standard machine-readable idiom."""
        import base64, re as _re
        from pcluster_core import _encode_slurm_switches

        enc = _encode_slurm_switches(switches)
        assert _re.fullmatch(r"[A-Za-z0-9+/=]*", enc), "not base64"
        import shlex as _shlex
        assert base64.b64decode(enc).decode().split("\n") == _shlex.split(switches)

    def test_globbing_is_disabled_in_the_body(self):
        """[ and ] are permitted for node ranges, so they must not expand
        against the node's working directory."""
        body = "\n".join(self._doc()["mainSteps"][0]["inputs"]["runCommand"])
        assert "set -f" in body

    def test_the_scontrol_subcommand_is_emitted_not_parameterized(self):
        """allowedValues admits `scontrol`; without this the subcommand
        would ride in through switches and a write would be reachable."""
        body = "\n".join(self._doc()["mainSteps"][0]["inputs"]["runCommand"])
        assert "SUB=(show)" in body
        assert '{{ command }} "${SUB[@]}"' in body

    def test_it_drops_root(self):
        """SSM RunShellScript executes as root; these commands have no
        business doing so, and $HOME would be wrong."""
        body = "\n".join(self._doc()["mainSteps"][0]["inputs"]["runCommand"])
        assert "runuser -u {{ user }}" in body

    def test_the_slurm_path_is_exported(self):
        """A non-interactive shell has no /opt/slurm/bin, and sinfo exits
        127 without it."""
        body = "\n".join(self._doc()["mainSteps"][0]["inputs"]["runCommand"])
        assert "/opt/slurm/bin" in body




    def test_no_charset_filter_survives_anywhere(self):
        """An earlier version validated switches against a character
        allowlist on both paths. Live testing killed it: the allowlist
        rejected `sinfo -o "%P|%a|%D"`, and widening it to admit the pipe
        would have admitted a pipeline. Base64 plus a bash array makes the
        question moot, so a reintroduced filter is a regression to a design
        that was wrong in both directions."""
        import pcluster_core as pc

        assert not hasattr(pc, "_SLURM_SWITCH_CHARSET")


class TestTheRemotePathIsReadOnlyByConstruction:
    def test_the_read_tool_is_on_the_read_only_tier(self):
        from mcp_server.tiers import TOOL_TIERS

        assert TOOL_TIERS["run_readonly_slurm_command"] == "read-only"

    def test_the_write_tool_did_not_follow_it(self):
        """sbatch is arbitrary code. It must never become reachable from
        an internet-facing Lambda, and porting the read half is exactly
        when that would happen by accident."""
        import mcp_server.tools as t
        from mcp_server.tiers import TOOL_TIERS

        assert "run_readwrite_slurm_command" in t._LOCAL_ONLY
        assert "run_readwrite_slurm_command" not in TOOL_TIERS

    def test_the_iam_names_the_document_not_the_shell_runner(self):
        """Granting SendCommand on AWS-RunShellScript would be arbitrary
        command execution. The grant names our constrained document, so a
        compromised tier can still only run three read commands."""
        import json

        d = json.loads(open(os.path.join(
            REPO_ROOT, "templates", "MCPReadOnlyLambda.json_src")).read()
            .replace("<AWS_ACCOUNT_ID>", "123456789012"))
        docs = [r for st in d["Statement"]
                if "ssm:SendCommand" in st.get("Action", [])
                for r in (st["Resource"] if isinstance(st["Resource"], list)
                          else [st["Resource"]])
                if ":document/" in r]
        assert docs == [
            "arn:aws:ssm:*:123456789012:document/PClusterMakerSlurmReadOnly"], docs
        assert not any("RunShellScript" in r for r in docs)

    def test_send_command_is_confined_to_cluster_instances(self):
        import json

        d = json.loads(open(os.path.join(
            REPO_ROOT, "templates", "MCPReadOnlyLambda.json_src")).read()
            .replace("<AWS_ACCOUNT_ID>", "123456789012"))
        inst = [st for st in d["Statement"]
                if "ssm:SendCommand" in st.get("Action", [])
                and any("instance/" in r for r in (
                    st["Resource"] if isinstance(st["Resource"], list)
                    else [st["Resource"]]))]
        assert inst, "no instance-scoped SendCommand statement"
        cond = inst[0].get("Condition", {})
        assert "parallelcluster:cluster-name" in json.dumps(cond), (
            "SendCommand reaches instances this toolkit did not build")

    def test_the_remote_path_refuses_a_write_command(self, monkeypatch):
        from pcluster_core import core_run_slurm_command_via_ssm

        with pytest.raises(PClusterMakerError):
            core_run_slurm_command_via_ssm(
                cluster_record={"region": "us-east-1"}, cluster_name="c",
                command="sbatch")

    def test_truncation_is_reported_not_hidden(self, monkeypatch):
        """SSM cuts stdout at exactly 24000 bytes. squeue on a busy
        cluster exceeds it, and silently short output is worse than none."""
        import pcluster_core as pc

        monkeypatch.setattr(pc, "run_slurm_via_ssm",
                            lambda **k: (0, "x" * 24000, "", True))
        r = pc.core_run_slurm_command_via_ssm(
            cluster_record={"region": "us-east-1", "ec2_user": "ubuntu",
                            "enable_loginnode": "false", "loginnode_count": 0},
            cluster_name="c", command="squeue", ssm=object(), ec2=object())
        assert r.truncated is True
        assert "truncated" in r.stderr

    def test_an_unknown_login_user_is_refused_before_sending(self):
        """The document's allowedValues would reject it anyway, but with a
        message naming nothing. Refuse here and say which users exist."""
        import pcluster_core as pc

        with pytest.raises(PClusterMakerError, match="login user"):
            pc.core_run_slurm_command_via_ssm(
                cluster_record={"region": "us-east-1", "ec2_user": "root",
                                "enable_loginnode": "false", "loginnode_count": 0},
                cluster_name="c", command="sinfo", ssm=object(), ec2=object())


class TestTheArrayIsWhatMakesTheSwitchesSafe:
    """Executed under real bash, because the claim is about what a shell
    does. Mirrors what was verified on a live login node: a pipe in a
    Slurm format string reaches the command as a field separator, and a
    semicolon reaches it as a literal argument."""

    def _decode_like_the_document(self, b64, tmp_path):
        # The document's own shape: decode into an array, expand quoted.
        script = (
            "set -f\n"
            'mapfile -t ARGS < <(printf %s "$1" | base64 -d)\n'
            'for a in ${ARGS[@]+"${ARGS[@]}"}; do echo "ARG:[$a]"; done\n'
        )
        return subprocess.run(["bash", "-c", script, "_", b64],
                              capture_output=True, text=True, cwd=tmp_path)

    def test_a_pipe_survives_as_data(self, tmp_path):
        from pcluster_core import _encode_slurm_switches

        r = self._decode_like_the_document(
            _encode_slurm_switches("-o %P|%a|%D"), tmp_path)
        assert r.returncode == 0, r.stderr
        assert "ARG:[-o]" in r.stdout
        assert "ARG:[%P|%a|%D]" in r.stdout, r.stdout

    def test_a_command_separator_is_not_executed(self, tmp_path):
        from pcluster_core import _encode_slurm_switches

        marker = tmp_path / "SHOULD_NOT_EXIST"
        r = self._decode_like_the_document(
            _encode_slurm_switches(f"-N ; touch {marker}"), tmp_path)
        assert r.returncode == 0, r.stderr
        assert not marker.exists(), "the separator was executed"
        assert "ARG:[;]" in r.stdout

    def test_an_argument_containing_a_space_stays_one_argument(self, tmp_path):
        """--format='%.18i %.9P' is one switch, not two. Word-splitting a
        decoded string rather than reading it into an array would break
        this, and it is the reason for mapfile."""
        from pcluster_core import _encode_slurm_switches

        r = self._decode_like_the_document(
            _encode_slurm_switches("--format='%.18i %.9P'"), tmp_path)
        assert "ARG:[--format=%.18i %.9P]" in r.stdout, r.stdout

    def test_empty_switches_do_not_break_under_set_u(self, tmp_path):
        """An unset array expanded without the +alternate guard is an
        unbound-variable error before bash 4.4 -- the same trap
        grafana_tunnel.j2 hit with PROXY_ARGS."""
        from pcluster_core import _encode_slurm_switches

        script = (
            "set -euf\n"
            "ARGS=()\n"
            'if [ -n "$1" ]; then mapfile -t ARGS < <(printf %s "$1" | base64 -d); fi\n'
            'echo "count=${#ARGS[@]}"\n'
            'echo done ${ARGS[@]+"${ARGS[@]}"}\n'
        )
        r = subprocess.run(["bash", "-c", script, "_", _encode_slurm_switches("")],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        assert "count=0" in r.stdout


class TestTheAssembledRemoteCommandActuallyRuns:
    """The gap that let a live bug through.

    `TestTheSbatchPayloadIsCorrectShell` executes the payload directly
    under `bash -c` -- around `_slurm_remote_cmd`, not through it. So it
    passed while the real invocation was broken: that function emits
    `exec {script}`, and `exec` treats `tmp="$(mktemp)"` as a command
    name, which on a live login node produced

        bash: line 1: /home/ubuntu/tmp=/home/ubuntu/.pcm-sbatch-UnJ6fh:
        No such file or directory

    These tests take what `_slurm_remote_cmd` actually returns and run it,
    which is the only shape that could have failed.
    """

    def _execute(self, remote_cmd, cwd, extra_path):
        # remote_cmd is ["bash", "-c", "<shell-quoted script>"]. ssh would
        # hand that to the remote shell, which unquotes it; `bash -c` here
        # plays that part.
        joined = " ".join(remote_cmd)
        return subprocess.run(
            ["bash", "-c", joined], capture_output=True, text=True, cwd=cwd,
            env=dict(os.environ, PATH=f"{extra_path}:{os.environ['PATH']}",
                     HOME=str(cwd)),
        )

    @pytest.fixture
    def fake_sbatch(self, tmp_path):
        p = tmp_path / "sbatch"
        p.write_text('#!/bin/bash\necho "Submitted batch job 777"\n')
        p.chmod(0o755)
        return tmp_path

    def test_a_compound_sbatch_payload_survives_assembly(self, fake_sbatch):
        from pcluster_core import _sbatch_script_payload, _slurm_remote_cmd

        cmd = _slurm_remote_cmd(
            _sbatch_script_payload(["sbatch"], "#!/bin/bash\ntrue\n"),
            exec_prefix=False)
        r = self._execute(cmd, fake_sbatch, fake_sbatch)
        assert r.returncode == 0, f"rc={r.returncode} stderr={r.stderr}"
        assert "Submitted batch job 777" in r.stdout, r.stdout + r.stderr
        assert "No such file or directory" not in r.stderr

    def test_the_exec_form_would_have_failed_it(self, fake_sbatch):
        """The vacuity guard, and the bug itself. Without this the test
        above passes whether or not exec_prefix does anything."""
        from pcluster_core import _sbatch_script_payload, _slurm_remote_cmd

        cmd = _slurm_remote_cmd(
            _sbatch_script_payload(["sbatch"], "#!/bin/bash\ntrue\n"),
            exec_prefix=True)
        r = self._execute(cmd, fake_sbatch, fake_sbatch)
        assert r.returncode != 0, (
            "exec on a compound script should fail -- if it does not, "
            "exec_prefix is solving a problem that no longer exists")

    def test_a_one_command_caller_still_gets_exec(self):
        """The three existing callers were written for it: it saves a
        process, and the docstring's reasoning about not using `bash -lc`
        assumes this shape."""
        from pcluster_core import _slurm_remote_cmd

        assert "exec sinfo" in _slurm_remote_cmd("sinfo -N")[2]

    def test_the_slurm_path_survives_both_forms(self):
        from pcluster_core import _slurm_remote_cmd

        for kw in ({}, {"exec_prefix": False}):
            assert "/opt/slurm/bin" in _slurm_remote_cmd("sinfo", **kw)[2]

    def test_the_end_to_end_path_uses_the_compound_form(self):
        """core_run_slurm_command must pass exec_prefix=False for sbatch
        and leave it alone otherwise -- asserted on the source, because
        the branch is what was wrong."""
        import ast, io

        src = io.open(os.path.join(REPO_ROOT, "src", "pcluster_core.py"),
                      encoding="utf-8").read()
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "core_run_slurm_command")
        calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name)
                 and n.func.id == "_slurm_remote_cmd"]
        assert len(calls) == 2, f"expected two call sites, found {len(calls)}"
        kwargs = [{k.arg for k in c.keywords} for c in calls]
        assert {"exec_prefix"} in kwargs, "no call site disables exec"
        assert set() in kwargs, "every call site disables exec -- the one-command form is gone"
