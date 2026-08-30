"""
Behavioral tests for the repo's shell surfaces: the benchmark driver and the
rendered monitoring wrapper.

Both were covered by shellcheck only, which sees style and quoting but not
logic. In the session-20 mutation measurement, replacing the benchmark's
checksum comparison with a non-empty test and deleting the monitoring wrapper's
`set -u` guard both survived the entire suite. shellcheck cannot catch either:
the first is valid bash, and the second is valid bash that only fails on the
distributions whose /etc/profile references an unset variable.
"""

import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCHMARK = os.path.join(REPO_ROOT, "hpc-benchmark", "hpc-benchmark.sh")
TEMPLATE_DIR = os.path.join(REPO_ROOT, "templates")

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash not available"
)


def _sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _run_fetch(tmp_path, payload, expected_sha, download_rc=0):
    """Drive _fetch with a stubbed _download that writes `payload` locally.

    Sourcing with HPC_BENCHMARK_LIB_ONLY defines the helpers without dispatching
    a command, so this exercises the real checksum logic — no reimplementation.
    """
    src = tmp_path / "payload"
    src.write_text(payload)
    script = f"""
    set -euo pipefail
    HPC_BENCHMARK_LIB_ONLY=1 source {BENCHMARK!r}
    _download() {{ cp {str(src)!r} "$1"; return {download_rc}; }}
    _fetch "{tmp_path}/dest" "https://example.invalid/tool.tar.gz" "{expected_sha}"
    echo FETCH_OK
    """
    return subprocess.run(["bash", "-c", script], capture_output=True)


class TestTheDriverCanBeSourcedFromAnInteractiveShell:
    """`HPC_BENCHMARK_LIB_ONLY=1 source ./hpc-benchmark.sh` is a documented entry
    point, but the guard that implements it sits just above the dispatch block --
    hundreds of lines after SCRIPT_DIR is computed from `$0`. In an interactive
    login shell `$0` is `-bash`, which `dirname` parses as a bundle of short
    options and rejects: `dirname: invalid option -- 'b'`. Observed on the osiris
    head node.

    Every existing test here sources under `bash -c`, where `$0` is plain `bash`
    and `dirname` answers `.` -- so the whole suite ran against a SCRIPT_DIR
    silently pointing at pytest's cwd and never saw this. That is why the case
    below fakes argv[0] rather than reusing the usual harness."""

    def _source_with_argv0(self, argv0, tmp_path):
        # exec -a is what puts an arbitrary string in $0; a login shell's leading
        # dash is not reproducible any other way from inside a test.
        inner = (
            f"HPC_BENCHMARK_LIB_ONLY=1 source {BENCHMARK!r}\n"
            'echo "SCRIPT_DIR=$SCRIPT_DIR"\n'
            'echo "BENCH_BIN=$BENCH_BIN"\n'
            "echo SOURCE_OK\n"
        )
        return subprocess.run(
            ["bash", "-c", f'exec -a {argv0} bash -s'],
            input=inner.encode(),
            capture_output=True,
            cwd=str(tmp_path),
        )

    def test_sourcing_from_a_login_shell_does_not_error(self, tmp_path):
        r = self._source_with_argv0("-bash", tmp_path)
        assert b"SOURCE_OK" in r.stdout, (
            f"sourcing the driver from a login shell fails.\n"
            f"stdout: {r.stdout.decode()}\nstderr: {r.stderr.decode()}"
        )
        assert b"invalid option" not in r.stderr and b"illegal option" not in r.stderr, (
            f"dirname parsed $0 as a flag; it needs `--`. stderr: {r.stderr.decode()}"
        )

    def test_script_dir_is_usable_when_argv0_names_no_path(self, tmp_path):
        """`dirname -- -bash` answers `.`, so SCRIPT_DIR resolves to the cwd --
        which is right for the documented usage (cd to the benchmark dir, then
        source). What must not happen is SCRIPT_DIR ending up empty or `/`, since
        BENCH_BIN is then the string "/bin" and `install` writes to the system
        directory."""
        r = self._source_with_argv0("-bash", tmp_path)
        out = r.stdout.decode()
        script_dir = re.search(r"SCRIPT_DIR=(.*)", out).group(1).strip()
        bench_bin = re.search(r"BENCH_BIN=(.*)", out).group(1).strip()
        assert script_dir, f"SCRIPT_DIR is empty: {out!r}"
        assert os.path.isdir(script_dir), f"SCRIPT_DIR is not a directory: {script_dir!r}"
        assert bench_bin == os.path.join(script_dir, "bin"), bench_bin
        assert bench_bin != "/bin", (
            "BENCH_BIN collapsed to /bin; install would write to the system dir"
        )

    def test_the_harness_reproduces_the_original_failure(self, tmp_path):
        """Guards the two tests above against passing vacuously: if `exec -a` ever
        stopped putting a leading dash in $0, they would pass against any
        implementation. This asserts the bare `dirname "$0"` this replaced does
        still fail on that argv[0]."""
        r = subprocess.run(
            ["bash", "-c", 'exec -a -bash bash -s'],
            input=b'dirname "$0"\n',
            capture_output=True,
            cwd=str(tmp_path),
        )
        assert r.returncode != 0, (
            "`dirname \"-bash\"` no longer fails, so these tests prove nothing"
        )


class TestSourcingDoesNotLeakShellOptionsIntoTheCaller:
    """`set -euo pipefail` applies to whatever shell runs it, so at the top of the
    file it leaked into every shell that sourced the driver. The documented library
    entry point therefore handed an interactive caller a shell where the next
    non-zero command exited the session -- which closed two SSH sessions on the
    osiris head node while debugging _native_march, and forced every diagnostic
    through `bash -c` one-liners.

    The `set` now sits below the HPC_BENCHMARK_LIB_ONLY guard. Nothing above it
    can fail: the sourced region executes twelve assignments plus the SCRIPT_DIR
    command substitution, and `dirname --` cannot reject an argv[0] while `cd .`
    cannot fail (the same reasoning that removed the unreachable
    `|| SCRIPT_DIR="$PWD"` fallback). Every function that needs -e is called from
    the dispatch block, which is below the `set`.

    The rest of the suite cannot see any of this: `bash -c` exits when its script
    ends, so a leaked -e has nothing left to kill. Faking a login shell's argv[0]
    is not enough either -- what matters here is running commands *after* the
    source and observing that the shell survives."""

    def _source_then(self, tail, tmp_path, argv0="-bash"):
        """Source the driver in a fake login shell, then run `tail` in it."""
        inner = (
            'echo "OPTS_BEFORE=[$-]"\n'
            f"HPC_BENCHMARK_LIB_ONLY=1 source {BENCHMARK!r}\n"
            'echo "OPTS_AFTER=[$-]"\n'
            f"{tail}\n"
        )
        return subprocess.run(
            ["bash", "-c", f"exec -a {argv0} bash -s"],
            input=inner.encode(),
            capture_output=True,
            cwd=str(tmp_path),
        )

    def test_sourcing_leaves_the_callers_shell_options_untouched(self, tmp_path):
        r = self._source_then("true", tmp_path)
        out = r.stdout.decode()
        before = re.search(r"OPTS_BEFORE=\[(.*)\]", out).group(1)
        after = re.search(r"OPTS_AFTER=\[(.*)\]", out).group(1)
        assert before == after, (
            f"sourcing changed the caller's shell options: {before!r} -> {after!r}"
        )
        assert "e" not in after, f"-e leaked into the caller: $- is {after!r}"
        assert "u" not in after, f"-u leaked into the caller: $- is {after!r}"

    def test_a_failing_command_after_sourcing_does_not_kill_the_session(self, tmp_path):
        """The actual symptom: with -e leaked, `false` ends the login shell and the
        SSH connection closes with status 1."""
        r = self._source_then("false\necho STILL_ALIVE", tmp_path)
        assert b"STILL_ALIVE" in r.stdout, (
            "the shell died on a failing command after sourcing -- set -e leaked.\n"
            f"stdout: {r.stdout.decode()}\nstderr: {r.stderr.decode()}"
        )
        assert r.returncode == 0, r.returncode

    def test_an_unset_variable_after_sourcing_does_not_kill_the_session(self, tmp_path):
        """-u is the other half, and it fires on ordinary interactive typing --
        referencing any variable the caller has not set."""
        r = self._source_then('echo "[${THIS_IS_NOT_SET:-}]"\n'
                              'echo "$THIS_IS_ALSO_NOT_SET"\n'
                              "echo STILL_ALIVE", tmp_path)
        assert b"STILL_ALIVE" in r.stdout, (
            "the shell died on an unset variable after sourcing -- set -u leaked.\n"
            f"stdout: {r.stdout.decode()}\nstderr: {r.stderr.decode()}"
        )

    def test_the_harness_can_see_a_leak(self, tmp_path):
        """Guards the three tests above against passing vacuously. They would all
        pass against a driver carrying no `set` line at all, and against a harness
        whose shell never had -e in the first place -- so assert that this harness
        does observe the leak when the shell really is put into -e."""
        inner = (
            "set -euo pipefail\n"
            'echo "OPTS_AFTER=[$-]"\n'
            "false\n"
            "echo STILL_ALIVE\n"
        )
        r = subprocess.run(
            ["bash", "-c", "exec -a -bash bash -s"],
            input=inner.encode(),
            capture_output=True,
            cwd=str(tmp_path),
        )
        assert b"STILL_ALIVE" not in r.stdout, (
            "this harness cannot observe a leaked set -e, so the tests above "
            f"prove nothing. stdout: {r.stdout.decode()}"
        )
        assert "e" in re.search(r"OPTS_AFTER=\[(.*)\]", r.stdout.decode()).group(1)


class TestTheDispatchPathStillRunsUnderStrictMode:
    """Moving the `set` must not become deleting it. `cmd_install`/`cmd_run` depend
    on -e for their abort semantics -- see TestCompileStreamFailuresReachTheCaller
    on the assignment-form rule -- and the concurrency-sensitive code they reach
    (_compile_stream's `mv` into shared bin/, _build_osu_cuda's mkdir lock) has
    only ever been reasoned about with strict mode on.

    The library tests cannot see this: they source the driver, which returns before
    the `set` is reached, and 16 of the 21 sourcing harnesses set -euo pipefail
    themselves anyway. So these run the driver as a *script*."""

    def _run_script(self, probe, tmp_path):
        """Invoke the driver as a script, with `probe` spliced in as the command."""
        return subprocess.run(
            [_BASH, str(BENCHMARK), probe],
            capture_output=True,
            cwd=str(tmp_path),
        )

    def test_the_set_is_below_the_library_guard_not_above_it(self):
        with open(BENCHMARK) as fh:
            lines = fh.read().splitlines()
        guard = next(
            i for i, ln in enumerate(lines)
            if ln.startswith('[[ -n "${HPC_BENCHMARK_LIB_ONLY')
        )
        sets = [i for i, ln in enumerate(lines) if ln.strip() == "set -euo pipefail"]
        assert len(sets) == 1, f"expected exactly one top-level `set`: lines {sets}"
        assert sets[0] > guard, (
            f"the `set` is at line {sets[0] + 1}, above the guard at "
            f"{guard + 1} -- it will leak into a sourcing caller"
        )

    def test_the_set_precedes_the_dispatch_case(self):
        """Below the guard is not enough: below the `case` would leave
        cmd_install/cmd_run running without -e, which is where the mv into shared
        bin/ and the mkdir lock live."""
        with open(BENCHMARK) as fh:
            lines = fh.read().splitlines()
        set_at = next(
            i for i, ln in enumerate(lines) if ln.strip() == "set -euo pipefail"
        )
        case_at = next(
            i for i, ln in enumerate(lines) if ln.strip() == 'case "$command" in'
        )
        assert set_at < case_at, (
            f"the `set` is at line {set_at + 1}, after the dispatch case at "
            f"{case_at + 1} -- cmd_install/cmd_run would run without strict mode"
        )

    def test_an_unknown_command_still_exits_non_zero(self, tmp_path):
        r = self._run_script("no-such-command", tmp_path)
        assert r.returncode != 0, r.stdout + r.stderr
        assert b"unknown command" in r.stderr, r.stderr

    def test_strict_mode_is_actually_in_force_by_the_time_a_command_runs(
        self, tmp_path
    ):
        """Reads the options back out of the running dispatch path rather than
        trusting the source: a `set` that is present but somehow not reached would
        pass the two static tests above."""
        with open(BENCHMARK) as fh:
            src = fh.read()
        probe = src.replace(
            '    -h|--help) _usage_main; exit 0 ;;',
            '    -h|--help) _usage_main; exit 0 ;;\n'
            '    _opts) echo "DISPATCH_OPTS=[$-]";'
            ' echo "PIPEFAIL=[$(set -o | grep ^pipefail)]"; exit 0 ;;',
            1,
        )
        assert "DISPATCH_OPTS" in probe, "could not splice the probe arm"
        spliced = tmp_path / "probe-driver.sh"
        spliced.write_text(probe)
        spliced.chmod(0o755)
        r = subprocess.run(
            [_BASH, str(spliced), "_opts"], capture_output=True, cwd=str(tmp_path)
        )
        out = r.stdout.decode()
        opts = re.search(r"DISPATCH_OPTS=\[(.*)\]", out).group(1)
        assert "e" in opts, f"-e is not in force in the dispatch path: {opts!r}"
        assert "u" in opts, f"-u is not in force in the dispatch path: {opts!r}"
        # `set -o` pads the name with spaces *and* a tab, so split on whitespace
        # rather than guessing the separator.
        pipefail = re.search(r"PIPEFAIL=\[(.*)\]", out).group(1).split()
        assert pipefail == ["pipefail", "on"], (
            f"pipefail is not in force in the dispatch path: {pipefail!r}"
        )


class TestTheStubPathCarriesEverythingTarNeeds:
    """The harnesses below replace PATH wholesale, so anything the driver's
    commands fork must be linked in explicitly. This failed exactly once, on CI
    and not locally: GNU tar forks a separate gzip for -xzf, while bsdtar on
    macOS decompresses in-process via libarchive, so three tests passed on a
    developer laptop and died on the runner with "gzip: Cannot exec".
    """

    def test_every_extraction_helper_the_driver_forks_is_available(self):
        with open(BENCHMARK) as fh:
            body = fh.read()
        assert "tar -xzf" in body, (
            "the driver no longer uses `tar -xzf`; if the compression changed, "
            "the decompressor in _STUB_PASSTHROUGH must change with it"
        )
        for helper in ("gzip", "gunzip"):
            assert helper in _STUB_PASSTHROUGH, (
                f"{helper} is missing from _STUB_PASSTHROUGH. GNU tar forks it "
                f"for -xzf; without it every tarball harness fails on Linux "
                f"while still passing under macOS bsdtar."
            )


class TestBenchmarkDownloadVerification:
    def test_accepts_a_download_whose_checksum_matches(self, tmp_path):
        payload = "int main(void) { return 0; }\n"
        digest = hashlib.sha256(payload.encode()).hexdigest()
        r = _run_fetch(tmp_path, payload, digest)
        assert r.returncode == 0, r.stderr
        assert b"FETCH_OK" in r.stdout
        assert _sha256(tmp_path / "dest") == digest

    def test_rejects_a_download_whose_checksum_does_not_match(self, tmp_path):
        """A moved, corrupted, or tampered artifact must abort the build rather
        than be compiled and run on the cluster. The surviving mutation replaced
        the comparison with a non-empty test on the computed hash, which is true
        for literally any file."""
        wrong = hashlib.sha256(b"something else entirely").hexdigest()
        r = _run_fetch(tmp_path, "malicious payload\n", wrong)
        assert r.returncode != 0
        assert b"FETCH_OK" not in r.stdout
        assert b"checksum mismatch" in r.stderr

    def test_reports_both_the_expected_and_actual_digest(self, tmp_path):
        payload = "tampered\n"
        wrong = hashlib.sha256(b"original").hexdigest()
        r = _run_fetch(tmp_path, payload, wrong)
        assert wrong.encode() in r.stderr
        assert hashlib.sha256(payload.encode()).hexdigest().encode() in r.stderr

    def test_aborts_when_the_download_itself_fails(self, tmp_path):
        payload = "partial"
        digest = hashlib.sha256(payload.encode()).hexdigest()
        r = _run_fetch(tmp_path, payload, digest, download_rc=1)
        assert r.returncode != 0
        assert b"download failed" in r.stderr

    def test_every_tool_url_is_paired_with_a_checksum(self):
        """A new tool added with a URL but no SHA256 would be fetched unverified,
        and _fetch's third argument would be the empty string."""
        with open(BENCHMARK) as fh:
            source = fh.read()
        urls = {
            line.split("_URL=")[0].strip()
            for line in source.splitlines()
            if "_URL=" in line and not line.strip().startswith("#")
        }
        shas = {
            line.split("_SHA256=")[0].strip()
            for line in source.splitlines()
            if "_SHA256=" in line and not line.strip().startswith("#")
        }
        assert urls, "no tool URLs found"
        assert urls == shas, f"URL/checksum pairs disagree: {urls ^ shas}"

    def test_no_fetch_call_passes_an_empty_checksum(self):
        with open(BENCHMARK) as fh:
            calls = [l.strip() for l in fh if l.strip().startswith("_fetch ")]
        assert calls, "no _fetch call sites found"
        for call in calls:
            assert call.count('"') >= 2, call
            assert '""' not in call, f"_fetch called with an empty argument: {call}"


def _function_body(name):
    """Return the source of a top-level shell function, brace to closing brace.

    Assertions on whole-file text cannot tell a defined-but-uncalled helper from
    a wired-up one, so call sites are checked inside the calling function.
    """
    with open(BENCHMARK) as fh:
        lines = fh.read().splitlines()
    start = next(
        (i for i, l in enumerate(lines) if l.startswith(f"{name}() {{")), None
    )
    assert start is not None, f"function {name}() not found in {BENCHMARK}"
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i] == "}"), None
    )
    assert end is not None, f"no closing brace found for {name}()"
    return "\n".join(lines[start : end + 1])


def _run_arch_check(tmp_path, stamp_contents):
    """Drive _check_arch_stamp against a prefix whose stamp we control.

    stamp_contents=None writes no stamp at all.
    """
    prefix = tmp_path / "bin"
    prefix.mkdir()
    if stamp_contents is not None:
        (prefix / ".build_arch").write_text(stamp_contents)
    script = f"""
    set -euo pipefail
    HPC_BENCHMARK_LIB_ONLY=1 source {BENCHMARK!r}
    _check_arch_stamp {str(prefix)!r}
    echo ARCH_OK
    """
    return subprocess.run(["bash", "-c", script], capture_output=True)


class TestBenchmarkArchitectureGuard:
    """STREAM is built with -march=native, so the binaries are specific to the
    build host. install runs on the head node; multi-node runs execute on
    compute nodes, which need not share its architecture. An x86_64 binary on
    Graviton fails with "Exec format error" deep inside mpirun, so the driver
    stamps the build arch and refuses up front."""

    def test_install_stamps_the_build_architecture(self):
        with open(BENCHMARK) as fh:
            source = fh.read()
        assert "_write_arch_stamp" in source
        assert source.count("_write_arch_stamp") >= 2, (
            "the stamp helper must be defined and actually called by install"
        )

    def test_run_actually_invokes_the_check(self):
        """Deleting the call site leaves the helper defined and every other test
        in this class green while the guard never fires on a real run."""
        body = _function_body("cmd_run")
        assert "_check_arch_stamp" in body, (
            "cmd_run does not call _check_arch_stamp; the guard is dead code"
        )

    def test_the_check_precedes_the_mpi_launcher(self):
        """Checking after mpirun has already been resolved and tests dispatched
        defeats the point: the operator is meant to be told before any binary
        is executed, not from inside a failing launch."""
        body = _function_body("cmd_run")
        assert body.index("_check_arch_stamp") < body.index("mpi_launcher"), (
            "the architecture check must run before the MPI launcher is set up"
        )

    def test_run_rejects_a_foreign_architecture(self, tmp_path):
        r = _run_arch_check(tmp_path, "s390x-not-this-host\n")
        assert r.returncode != 0, (
            "run must abort when the binaries were built for another architecture"
        )
        assert b"architecture mismatch" in r.stderr
        assert b"ARCH_OK" not in r.stdout

    def test_the_mismatch_message_names_both_architectures(self, tmp_path):
        r = _run_arch_check(tmp_path, "s390x-not-this-host\n")
        assert b"s390x-not-this-host" in r.stderr
        arch = subprocess.run(["uname", "-m"], capture_output=True).stdout.strip()
        assert arch in r.stderr, "the host architecture must appear in the error"

    def test_run_proceeds_when_the_architecture_matches(self, tmp_path):
        arch = subprocess.run(["uname", "-m"], capture_output=True).stdout.decode()
        r = _run_arch_check(tmp_path, arch)
        assert r.returncode == 0, r.stderr
        assert b"ARCH_OK" in r.stdout
        assert b"architecture mismatch" not in r.stderr

    def test_a_missing_stamp_warns_rather_than_blocking(self, tmp_path):
        """Trees built before this check, or by hand, have no stamp. Refusing to
        run them would break working installs for no safety gain."""
        r = _run_arch_check(tmp_path, None)
        assert r.returncode == 0, r.stderr
        assert b"ARCH_OK" in r.stdout
        assert b"WARNING" in r.stderr


_BASH = shutil.which("bash")


def _run_bash(script):
    """Run a snippet under the real bash and return its stdout."""
    return subprocess.run(
        [_BASH, "-c", script], capture_output=True, text=True
    ).stdout


# Everything cmd_run's stream path shells out to. PATH is replaced wholesale so
# the host's real gcc cannot answer for the fake one, which means every other
# command the driver needs has to be linked in explicitly.
# gzip and gunzip are here for `tar -xzf`, which the driver uses at four sites.
# GNU tar forks a separate gzip binary; bsdtar on macOS decompresses in-process
# via libarchive. Since these harnesses replace PATH wholesale, omitting gzip
# passes locally and fails on a Linux CI runner with "gzip: Cannot exec".
_STUB_PASSTHROUGH = (
    "bash", "sh", "mv", "rm", "find", "sort", "head", "awk", "uname",
    "basename", "dirname", "mkdir", "tee", "cat", "date", "cp", "chmod",
    "sed", "grep", "tr", "cut", "wc", "mktemp", "make", "sha256sum", "shasum",
    "gzip", "gunzip",
)


def _fake_gcc(bindir, march, compile_rc=0, resolvable=True, probe_rc=2,
              march_row=True):
    """A gcc stub that answers -Q --help=target with `march` and "compiles".

    The driver reads the microarchitecture out of gcc rather than out of
    `uname -m`, so a fake gcc is the only way to exercise a second node class
    from one host. Writing the output file rather than really compiling keeps
    the test independent of whether the host toolchain accepts -fopenmp.

    resolvable=False models a compiler that builds but cannot name its target
    (clang, or a gcc too old for -Q --help=target).

    The probe output carries the two other lines real gcc emits that the parse
    has to get past -- a neighboring `-mtune=` row and the `Known valid
    arguments for -march= option:` trailer (line 266 of the 271 gcc 13.3.0
    printed on the osiris head node). march_row=False drops only the value row
    and keeps the trailer, which is the shape that tells an exact-field parse
    from a substring one: a substring match reads `valid` off the trailer and
    names a shared binary `stream-valid`.

    probe_rc defaults to 2 because that is what real gcc does: it prints the
    option table and then exits non-zero, since -Q asks it to compile and no
    input file was given. Measured on the osiris head node (Ubuntu 24.04, gcc
    13.3.0) as `PIPESTATUS=2 0` -- gcc failing while awk succeeds on the line it
    already read. This stub exited 0 for four sessions, which is exactly why the
    suite reported green while every real node resolved to "unknown".
    """
    probe = "--help=target" if resolvable else "--never-matches"
    value_row = f'    echo "  -march=                               {march}"\n'
    gcc = bindir / "gcc"
    gcc.write_text(
        "#!/bin/bash\n"
        'for a in "$@"; do\n'
        f'  if [[ "$a" == "{probe}" ]]; then\n'
        '    echo "  -mtune=                               generic"\n'
        + (value_row if march_row else "")
        + '    echo "  Known valid arguments for -march= option:"\n'
        '    echo "    x86-64 x86-64-v2 skylake-avx512 znver3"\n'
        f"    exit {probe_rc}\n"
        "  fi\n"
        "done\n"
        f"[[ {compile_rc} -eq 0 ]] || exit {compile_rc}\n"
        "out=\n"
        'while [[ $# -gt 0 ]]; do\n'
        '  [[ "$1" == "-o" ]] && { out="$2"; shift 2; continue; }\n'
        "  shift\n"
        "done\n"
        'printf "#!/bin/bash\\necho STREAM_RAN\\n" > "$out"\n'
        'chmod +x "$out"\n'
    )
    gcc.chmod(0o755)
    return gcc


def _stream_harness(tmp_path, march, script, compile_rc=0, with_gcc=True,
                    resolvable=True, break_mv=False, probe_rc=2,
                    march_row=True):
    """Run `script` against the real driver with only a fake gcc on PATH.

    `nproc` is stubbed too: it is absent on macOS, where the suite also runs.
    """
    stub = tmp_path / "stub"
    stub.mkdir(parents=True, exist_ok=True)
    if with_gcc:
        _fake_gcc(stub, march, compile_rc, resolvable, probe_rc, march_row)
    # nproc is absent on macOS, where the suite also runs; cmd_run resolves an
    # MPI launcher before dispatching any test, stream-only runs included.
    for name, body in (("nproc", "echo 2\n"), ("mpirun", 'exec "$@"\n')):
        stub_cmd = stub / name
        stub_cmd.write_text("#!/bin/bash\n" + body)
        stub_cmd.chmod(0o755)
    for name in _STUB_PASSTHROUGH:
        real = shutil.which(name)
        if real and not (stub / name).exists():
            (stub / name).symlink_to(real)
    if break_mv:
        # Models the rename into shared storage failing -- a read-only or
        # root-squashed bin/, or another node holding the path.
        (stub / "mv").unlink()
        (stub / "mv").write_text("#!/bin/bash\necho 'mv: refused' >&2\nexit 1\n")
        (stub / "mv").chmod(0o755)
    env = dict(os.environ, PATH=str(stub))
    return subprocess.run([_BASH, "-c", script], capture_output=True, env=env)


class TestStreamFollowsTheNodeClass:
    """-march=native makes the STREAM binary specific to the microarchitecture,
    not just the architecture: an Intel head node and an AMD GPU node both report
    x86_64, so `uname -m` cannot tell them apart and the head node's binary runs
    on the GPU node while quietly under-reporting its bandwidth. The driver keeps
    one binary per resolved -march target and compiles the missing one on
    whichever node the job lands on, which is what makes a GPU-partition job
    correct with no manual rebuild."""

    def _prefix(self, tmp_path):
        """A bin/ that looks like a completed `install --tools stream`."""
        prefix = tmp_path / "bin"
        (prefix / "src").mkdir(parents=True)
        (prefix / "src" / "stream.c").write_text("int main(void){return 0;}\n")
        return prefix

    def _bin_path_script(self, prefix):
        return f"""
        set -euo pipefail
        HPC_BENCHMARK_LIB_ONLY=1 source {BENCHMARK!r}
        _stream_bin_path {str(prefix)!r}
        """

    def test_the_binary_is_named_for_the_resolved_march(self, tmp_path):
        prefix = self._prefix(tmp_path)
        r = _stream_harness(tmp_path, "skylake-avx512", self._bin_path_script(prefix))
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip().decode() == str(prefix / "stream-skylake-avx512")

    def test_a_different_microarchitecture_gets_a_different_name(self, tmp_path):
        """The whole mechanism rests on this: if both node classes resolved to the
        same filename, the second would silently reuse the first's binary."""
        prefix = self._prefix(tmp_path)
        intel = _stream_harness(tmp_path, "skylake-avx512", self._bin_path_script(prefix))
        amd = _stream_harness(tmp_path / "amd", "znver3", self._bin_path_script(prefix))
        assert intel.stdout != amd.stdout
        assert amd.stdout.strip().decode().endswith("stream-znver3")

    def test_march_characters_illegal_in_a_filename_are_folded(self, tmp_path):
        """Graviton's gcc answers with a +-separated feature list
        (armv8.2-a+crypto+fp16). Used verbatim it is still a legal filename, but
        the + characters make the path a shell-glob and word-splitting hazard for
        anything downstream; fold them instead."""
        prefix = self._prefix(tmp_path)
        r = _stream_harness(tmp_path, "armv8.2-a+crypto+fp16", self._bin_path_script(prefix))
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip().decode().endswith("stream-armv8.2-a_crypto_fp16")

    def test_no_gcc_resolves_to_unknown_rather_than_empty(self, tmp_path):
        """An empty march would name the binary `stream-`, which collides across
        every node class."""
        r = _stream_harness(
            tmp_path, "irrelevant",
            f"""
            set -euo pipefail
            HPC_BENCHMARK_LIB_ONLY=1 source {BENCHMARK!r}
            _native_march
            """,
            with_gcc=False,
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == b"unknown"


    def _run_stream(self, tmp_path, prefix, march, results, **kw):
        # BENCH_BIN is set both before and after sourcing: the driver assigns it
        # from SCRIPT_DIR at source time, and cmd_run reads it at call time.
        script = f"""
        set -euo pipefail
        BENCH_BIN={str(prefix)!r}
        HPC_BENCHMARK_LIB_ONLY=1 source {BENCHMARK!r}
        BENCH_BIN={str(prefix)!r}
        cmd_run --tests stream --results-dir {str(results)!r}
        echo RUN_OK
        """
        return _stream_harness(tmp_path, march, script, **kw)

    def test_run_compiles_for_a_node_class_that_has_no_binary_yet(self, tmp_path):
        """The GPU-node case. install ran on the head node, so bin/ holds only the
        head node's binary; the run must build this node's rather than execute
        one tuned for another core."""
        prefix = self._prefix(tmp_path)
        (prefix / "stream-skylake-avx512").write_text("#!/bin/bash\necho HEAD_NODE_BIN\n")
        (prefix / "stream-skylake-avx512").chmod(0o755)
        r = self._run_stream(tmp_path, prefix, "znver3", tmp_path / "results")
        assert r.returncode == 0, r.stderr + r.stdout
        assert b"RUN_OK" in r.stdout
        assert (prefix / "stream-znver3").exists(), (
            "run did not build a STREAM binary for this node's microarchitecture"
        )
        assert b"HEAD_NODE_BIN" not in r.stdout, (
            "run executed the binary built for another microarchitecture"
        )
        assert b"STREAM_RAN" in r.stdout

    def test_the_head_node_binary_is_left_in_place(self, tmp_path):
        """bin/ is on shared storage. Overwriting rather than adding would make
        every job thrash the binary back and forth between node classes."""
        prefix = self._prefix(tmp_path)
        (prefix / "stream-skylake-avx512").write_text("#!/bin/bash\ntrue\n")
        (prefix / "stream-skylake-avx512").chmod(0o755)
        self._run_stream(tmp_path, prefix, "znver3", tmp_path / "results")
        assert (prefix / "stream-skylake-avx512").exists()
        assert (prefix / "stream-znver3").exists()

    def test_a_second_run_on_the_same_class_does_not_recompile(self, tmp_path):
        """The compile is a few seconds, but paying it on every job in a queue is
        pure waste and would show up as noise in the results."""
        prefix = self._prefix(tmp_path)
        first = self._run_stream(tmp_path, prefix, "znver3", tmp_path / "results")
        assert first.returncode == 0, first.stderr
        assert b"compiling for this node" in first.stdout
        second = self._run_stream(tmp_path, prefix, "znver3", tmp_path / "results2")
        assert second.returncode == 0, second.stderr
        assert b"compiling for this node" not in second.stdout

    def test_a_failed_compile_aborts_instead_of_running_nothing(self, tmp_path):
        """_compile_stream returns its path on stdout, and `set -e` does not
        propagate a failure out of a command substitution in an assignment.
        Without the explicit || _die the run continued and exec'd a binary that
        was never produced."""
        prefix = self._prefix(tmp_path)
        r = self._run_stream(tmp_path, prefix, "znver3", tmp_path / "results",
                             compile_rc=1)
        assert r.returncode != 0, r.stdout
        assert b"RUN_OK" not in r.stdout
        assert b"STREAM compile failed" in r.stderr

    def test_a_missing_cached_source_names_the_install_command(self, tmp_path):
        """A node that cannot find bin/src/stream.c cannot download it either --
        compute nodes in a private subnet have no route out."""
        prefix = tmp_path / "bin"
        prefix.mkdir()
        r = self._run_stream(tmp_path, prefix, "znver3", tmp_path / "results")
        assert r.returncode != 0
        assert b"install --tools stream" in r.stderr

    def test_compiling_without_a_cached_source_never_reaches_the_compiler(
        self, tmp_path
    ):
        """The pre-flight check on the cached source is what turns a missing file
        into an actionable message. Without it gcc is handed a nonexistent path
        and the operator gets a compiler error about the driver's internals."""
        prefix = tmp_path / "bin"
        prefix.mkdir()
        script = f"""
        set -euo pipefail
        HPC_BENCHMARK_LIB_ONLY=1 source {BENCHMARK!r}
        _compile_stream {str(prefix)!r}
        echo COMPILE_OK
        """
        r = _stream_harness(tmp_path, "znver3", script)
        assert r.returncode != 0
        assert b"COMPILE_OK" not in r.stdout
        assert b"source not cached" in r.stderr, (
            "a missing bin/src/stream.c must be named as such, not left to gcc"
        )
        assert not list(prefix.glob("stream-*")), (
            "a binary was produced from a source file that does not exist"
        )

    def test_a_rename_that_fails_does_not_report_success(self, tmp_path):
        """bin/ is on shared storage, so the compile lands on a private path and
        is renamed into place. If the rename fails the binary is not there, and
        the caller reads its path off stdout -- reporting success hands the run a
        path to nothing."""
        prefix = self._prefix(tmp_path)
        script = f"""
        set -euo pipefail
        HPC_BENCHMARK_LIB_ONLY=1 source {BENCHMARK!r}
        _compile_stream {str(prefix)!r}
        echo COMPILE_OK
        """
        r = _stream_harness(tmp_path, "znver3", script, break_mv=True)
        assert r.returncode != 0
        assert b"COMPILE_OK" not in r.stdout
        assert b"could not install the STREAM binary" in r.stderr

    def test_install_aborts_when_the_stream_compile_fails(self, tmp_path):
        """Same set -e blind spot as in run: install reads the path out of a
        command substitution, so without an explicit check a failed compile left
        install printing "STREAM installed at" and exiting 0."""
        prefix = tmp_path / "bin"
        script = f"""
        set -euo pipefail
        HPC_BENCHMARK_LIB_ONLY=1 source {BENCHMARK!r}
        _fetch() {{ printf 'int main(void){{return 0;}}\\n' > "$1"; }}
        cmd_install --prefix {str(prefix)!r} --tools stream
        echo INSTALL_OK
        """
        r = _stream_harness(tmp_path, "znver3", script, compile_rc=1)
        assert r.returncode != 0, r.stdout
        assert b"INSTALL_OK" not in r.stdout
        assert b"STREAM installed at" not in r.stdout, (
            "install reported a successful STREAM build after the compile failed"
        )

    def test_a_node_that_cannot_name_its_march_does_not_poison_the_cache(self, tmp_path):
        """With no resolvable march, every node class would share the filename
        stream-unknown and hand each other the wrong binary. Build into the run's
        own results directory instead, and say so."""
        prefix = self._prefix(tmp_path)
        results = tmp_path / "results"
        script = f"""
        set -euo pipefail
        BENCH_BIN={str(prefix)!r}
        HPC_BENCHMARK_LIB_ONLY=1 source {BENCHMARK!r}
        BENCH_BIN={str(prefix)!r}
        cmd_run --tests stream --results-dir {str(results)!r}
        echo RUN_OK
        """
        r = _stream_harness(tmp_path, "ignored", script, resolvable=False)
        assert r.returncode == 0, r.stderr + r.stdout
        assert b"RUN_OK" in r.stdout
        assert b"cannot report the -march=native target" in r.stderr
        assert not list(prefix.glob("stream-unknown")), (
            "an unlabeled binary was cached in the shared bin/, where the next "
            "node class would pick it up as its own"
        )
        assert list(results.glob("*/stream.bin")), (
            "the throwaway binary was not built into this run's results directory"
        )

    def test_a_failed_throwaway_compile_also_aborts(self, tmp_path):
        """The unknown-march path has its own _compile_stream call site and so its
        own instance of the set -e blind spot. Without its explicit check the run
        exec'd a stream.bin that was never written."""
        prefix = self._prefix(tmp_path)
        r = self._run_stream(tmp_path, prefix, "ignored", tmp_path / "results",
                             compile_rc=1, resolvable=False)
        assert r.returncode != 0, r.stdout
        assert b"RUN_OK" not in r.stdout
        assert b"could not be built on this node" in r.stderr

    def test_a_node_with_no_compiler_falls_back_with_a_warning(self, tmp_path):
        """A compute node without gcc cannot build anything. Running a binary from
        another node class is still better than no measurement, but the operator
        has to be told the number may under-report."""
        prefix = self._prefix(tmp_path)
        (prefix / "stream-skylake-avx512").write_text("#!/bin/bash\necho STREAM_RAN\n")
        (prefix / "stream-skylake-avx512").chmod(0o755)
        r = self._run_stream(tmp_path, prefix, "znver3", tmp_path / "results",
                             with_gcc=False)
        assert r.returncode == 0, r.stderr + r.stdout
        assert b"RUN_OK" in r.stdout
        assert b"may under-report" in r.stderr
        assert b"STREAM_RAN" in r.stdout

    def test_a_node_with_neither_compiler_nor_binary_aborts(self, tmp_path):
        prefix = self._prefix(tmp_path)
        r = self._run_stream(tmp_path, prefix, "znver3", tmp_path / "results",
                             with_gcc=False)
        assert r.returncode != 0
        assert b"no STREAM binary" in r.stderr

    def test_a_stream_only_run_ignores_the_architecture_stamp(self, tmp_path):
        """The stamp guards the configure/make-built tools. STREAM builds itself
        for the node it lands on, so blocking a stream-only run on a foreign
        architecture refuses work that would have succeeded."""
        prefix = self._prefix(tmp_path)
        (prefix / ".build_arch").write_text("s390x-not-this-host\n")
        r = self._run_stream(tmp_path, prefix, "znver3", tmp_path / "results")
        assert r.returncode == 0, r.stderr + r.stdout
        assert b"architecture mismatch" not in r.stderr

    def test_a_mixed_run_still_honors_the_architecture_stamp(self, tmp_path):
        """Adding stream to the list must not become a way to bypass the guard on
        the binaries that genuinely cannot execute here."""
        prefix = self._prefix(tmp_path)
        (prefix / ".build_arch").write_text("s390x-not-this-host\n")
        script = f"""
        set -euo pipefail
        BENCH_BIN={str(prefix)!r}
        HPC_BENCHMARK_LIB_ONLY=1 source {BENCHMARK!r}
        BENCH_BIN={str(prefix)!r}
        cmd_run --tests stream,osu --results-dir {str(tmp_path / 'results')!r}
        """
        r = _stream_harness(tmp_path, "znver3", script)
        assert r.returncode != 0
        assert b"architecture mismatch" in r.stderr

    def test_install_caches_the_source_next_to_the_binary(self, tmp_path):
        """run's rebuild is a local compile of this file; without it a private-
        subnet compute node, which has no route to the internet, has nothing to
        build from. _fetch is stubbed so this exercises install's own caching
        rather than the download."""
        prefix = tmp_path / "bin"
        script = f"""
        set -euo pipefail
        HPC_BENCHMARK_LIB_ONLY=1 source {BENCHMARK!r}
        _fetch() {{ printf 'int main(void){{return 0;}}\\n' > "$1"; }}
        cmd_install --prefix {str(prefix)!r} --tools stream
        """
        r = _stream_harness(tmp_path, "znver3", script)
        assert r.returncode == 0, r.stderr + r.stdout
        assert (prefix / "src" / "stream.c").is_file(), (
            "install no longer caches stream.c; the on-demand rebuild in run has "
            "no source to compile on a node with no route to the internet"
        )
        assert (prefix / "stream-znver3").is_file(), (
            "install did not produce a STREAM binary named for this node's "
            "microarchitecture"
        )

    def test_run_actually_calls_the_per_march_helpers(self):
        """Deleting either call site leaves every helper defined and the unit
        tests above green while a GPU job silently runs the head node's binary."""
        body = _function_body("cmd_run")
        assert "_stream_bin_path" in body
        assert "_compile_stream" in body


class TestGccsExitStatusDoesNotDecideTheMarch:
    """`gcc -march=native -Q --help=target` prints the option table and *then*
    exits non-zero: -Q asks gcc to compile, and no input file was given, so it
    ends with `gcc: fatal error: no input files` on stderr that the probe's
    2>/dev/null discards. Measured on the osiris head node (Ubuntu 24.04, gcc
    13.3.0) as `PIPESTATUS=2 0` -- gcc failing while awk succeeds on the line it
    had already read.

    The driver runs under `set -euo pipefail`, so while the probe piped straight
    out of gcc the whole pipeline failed and the `|| resolved=""` fallback turned
    a perfectly good `skylake-avx512` into `unknown` on every node. Nothing was
    computed wrong -- cmd_run falls back to a per-run throwaway binary -- but the
    per-microarchitecture cache in bin/ was dead, so every job recompiled STREAM
    and the WARNING implied the compiler could not name its target when it could.

    It was invisible interactively (the same pipeline prints the right answer;
    only the discarded exit status differs) and invisible to this suite, whose
    fake gcc exited 0 on the probe. So both halves are asserted here: the march
    survives a non-zero gcc, and the stub really does exit non-zero."""

    _SCRIPT = f"""
        set -euo pipefail
        HPC_BENCHMARK_LIB_ONLY=1 source {BENCHMARK!r}
        _native_march
        """

    def test_the_march_survives_a_gcc_that_exits_non_zero(self, tmp_path):
        r = _stream_harness(tmp_path, "skylake-avx512", self._SCRIPT, probe_rc=2)
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == b"skylake-avx512"

    def test_the_harness_gcc_really_does_fail_the_probe(self, tmp_path):
        """Without this the test above passes whether or not the stub models the
        bug, and the suite goes back to reporting green on the shipped defect."""
        r = _stream_harness(
            tmp_path, "skylake-avx512",
            """
            set -uo pipefail
            gcc -march=native -Q --help=target >/dev/null 2>&1
            echo "rc=$?"
            """,
            probe_rc=2,
        )
        assert r.stdout.strip() == b"rc=2", (r.stdout, r.stderr)

    def test_a_gcc_that_exits_zero_is_read_the_same_way(self, tmp_path):
        """gcc's status is ignored, not inverted: a toolchain that succeeds on the
        probe must still resolve."""
        r = _stream_harness(tmp_path, "znver3", self._SCRIPT, probe_rc=0)
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == b"znver3"

    def test_the_march_is_read_off_the_value_row_not_the_trailer(self, tmp_path):
        """gcc prints `Known valid arguments for -march= option:` five lines from
        the end of its 271-line table. A substring match on `-march=` hits that
        trailer too and reads `valid` off it -- which is a legal filename, so
        every node class would quietly share one `stream-valid` binary. The parse
        must anchor on $1 being exactly `-march=`."""
        r = _stream_harness(
            tmp_path, "irrelevant", self._SCRIPT, march_row=False, probe_rc=2,
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == b"unknown", r.stdout

    def test_a_neighboring_option_row_is_not_mistaken_for_the_march(self, tmp_path):
        """`-mtune=` sits next to `-march=` in the same table with the same
        two-field shape."""
        r = _stream_harness(tmp_path, "znver3", self._SCRIPT)
        assert r.stdout.strip() == b"znver3", r.stdout

    def test_a_gcc_that_prints_nothing_still_yields_unknown(self, tmp_path):
        """Ignoring the status must not become ignoring the output. A compiler too
        old for -Q --help=target, or clang, prints no -march= row; the caller's
        `unknown` path is what keeps that off the shared bin/."""
        r = _stream_harness(
            tmp_path, "irrelevant", self._SCRIPT, resolvable=False, probe_rc=2,
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == b"unknown"

    def test_the_probe_is_not_piped_straight_out_of_gcc(self, tmp_path):
        """The regression is a shape, and the runtime tests above cannot prove it
        stays gone: a future `gcc ... | awk` is invisible to them on any node whose
        gcc happens to exit 0. gcc's output must be captured on its own command,
        with the status discarded, and parsed on a later one."""
        with open(BENCHMARK) as fh:
            src = fh.read()
        start = src.index("_native_march()")
        body = src[start:src.index("\n}\n", start)]
        gcc_cmd = [
            ln.split("#")[0].rstrip() for ln in body.splitlines()
            if "gcc -march=native" in ln.split("#")[0]
        ]
        assert len(gcc_cmd) == 1, gcc_cmd
        line = gcc_cmd[0]
        assert line.endswith("|| true"), (
            f"gcc's exit status must be discarded explicitly: {line.strip()!r}"
        )
        assert not line.endswith("\\"), (
            f"gcc's command must not continue onto the next line: {line.strip()!r}"
        )
        # Everything up to the `|| true` is the command itself; a pipe there is
        # the regression, and `|| true` guards only the pipeline's *last* stage.
        assert "|" not in line[: -len("|| true")], (
            f"gcc's output must not be piped: {line.strip()!r}"
        )


class TestBenchmarkBuildDiagnostics:
    """A failed build reported only "HPCG build failed" because every configure
    and make was redirected to /dev/null, leaving the operator with no cause to
    act on. _build_step captures the output and prints the tail on failure."""

    def test_no_build_command_discards_its_output(self):
        with open(BENCHMARK) as fh:
            lines = fh.read().splitlines()
        offenders = [
            line.strip()
            for line in lines
            if ">/dev/null 2>&1" in line
            and any(tok in line for tok in ("make ", "configure", "./configure"))
            # `command -v make` asks whether the tool exists; it builds nothing,
            # and its output is the answer rather than a diagnostic.
            and "command -v" not in line
        ]
        assert not offenders, (
            "build steps must log through _build_step, not discard output: " f"{offenders}"
        )

    def test_build_failure_prints_the_log_tail_and_path(self, tmp_path):
        logfile = tmp_path / "probe.log"
        script = f"""
        set -euo pipefail
        HPC_BENCHMARK_LIB_ONLY=1 source {BENCHMARK!r}
        _build_step "PROBE build failed" {str(logfile)!r} \
            bash -c 'echo compiler-diagnostic-line >&2; exit 1'
        echo SHOULD_NOT_REACH
        """
        r = subprocess.run(["bash", "-c", script], capture_output=True)
        assert r.returncode != 0
        assert b"SHOULD_NOT_REACH" not in r.stdout
        assert b"PROBE build failed" in r.stderr
        assert b"compiler-diagnostic-line" in r.stderr, (
            "the actual compiler output must reach the operator"
        )
        assert str(logfile).encode() in r.stderr, "the log path must be reported"

    def test_build_step_is_transparent_on_success(self, tmp_path):
        logfile = tmp_path / "probe.log"
        script = f"""
        set -euo pipefail
        HPC_BENCHMARK_LIB_ONLY=1 source {BENCHMARK!r}
        _build_step "PROBE failed" {str(logfile)!r} bash -c 'echo built; exit 0'
        echo STEP_OK
        """
        r = subprocess.run(["bash", "-c", script], capture_output=True)
        assert r.returncode == 0, r.stderr
        assert b"STEP_OK" in r.stdout
        assert b"built" in logfile.read_bytes(), "output must be captured to the log"


def _driver_sed_expr(needle):
    """Return the driver's single quoted sed expression containing needle.

    The expression may sit on a `sed -i.bak` line or on its backslash
    continuation, so match on the quoted expression itself rather than the
    command name.
    """
    with open(BENCHMARK) as fh:
        lines = fh.read().splitlines()
    matches = [
        l.strip() for l in lines
        if needle in l and l.strip().startswith("'s/") and not l.strip().startswith("#")
    ]
    assert len(matches) == 1, f"expected exactly one sed expression for {needle!r}: {matches}"
    return matches[0].split("'")[1]


class TestHpcgConfigureTarget:
    """HPCG's configure does not generate a setup file — it requires
    setup/Make.<TARGET> to already exist in the tarball. The driver asked for
    MPI_GCC, which HPCG 3.1 does not ship (it ships MPI_GCC_OMP). configure
    printed "Please create the configuration file" and exited 127 without
    writing a Makefile; with output discarded, the operator saw only
    "ERROR: HPCG build failed"."""

    def test_the_driver_targets_a_setup_file_hpcg_actually_ships(self):
        with open(BENCHMARK) as fh:
            source = fh.read()
        assert "configure MPI_GCC_OMP" in source
        assert "configure MPI_GCC " not in source and not source.count(
            "configure MPI_GCC\n"
        ), "MPI_GCC has no setup/Make.MPI_GCC in HPCG 3.1; configure exits 127"

    def test_a_missing_setup_file_is_reported_before_configure_runs(self, tmp_path):
        """Without this check the failure surfaces one step later, as a make
        error, which points at the compiler rather than at the wrong target."""
        with open(BENCHMARK) as fh:
            source = fh.read()
        assert "HPCG setup file not found" in source, (
            "the pre-configure existence check on setup/Make.MPI_GCC_OMP is gone"
        )
        # The diagnostic must enumerate the targets that do exist, or the
        # operator has no way to pick a valid one.
        assert "find" in source.split("HPCG setup file not found")[1][:400], (
            "the error must list the setup targets present in the source tree"
        )


class TestHpcgOpenMpDefaultNone:
    """HPCG 3.1 predates OpenMP 4.0, which stopped treating a const scalar as
    predetermined-shared. ComputeResidual.cpp's `parallel default(none)` omits
    `n`, so GCC 9+ rejects the enclosed `omp for` with "'n' not specified in
    enclosing 'parallel'". Verified against real upstream source with GCC 16."""

    PRISTINE = (
        "int ComputeResidual(const local_int_t n, const Vector & v1,\n"
        "                    const Vector & v2, double & residual) {\n"
        "#ifndef HPCG_NO_OPENMP\n"
        "  #pragma omp parallel default(none) shared(local_residual, v1v, v2v)\n"
        "  {\n"
        "    #pragma omp for\n"
        "  }\n"
        "#endif\n"
    )

    def _apply(self, tmp_path, text):
        src = tmp_path / "ComputeResidual.cpp"
        src.write_text(text)
        subprocess.run(
            ["sed", "-i.bak", _driver_sed_expr("omp parallel default(none)"), str(src)],
            check=True,
        )
        return src.read_text()

    def test_n_is_added_to_the_shared_clause(self, tmp_path):
        result = self._apply(tmp_path, self.PRISTINE)
        assert "shared(n, local_residual, v1v, v2v)" in result

    def test_default_none_is_preserved(self, tmp_path):
        """Dropping default(none) also compiles, but it disables the compiler's
        check that nothing else is captured implicitly. Keep the stricter form."""
        result = self._apply(tmp_path, self.PRISTINE)
        assert "default(none)" in result

    def test_the_rewrite_is_idempotent(self, tmp_path):
        """install can run twice against the same extracted tree. An unanchored
        pattern yields `shared(n, n, ...)`, which is a duplicate-in-clause
        error — a fix that breaks the build on the second run."""
        once = self._apply(tmp_path, self.PRISTINE)
        twice = self._apply(tmp_path, once)
        assert twice == once
        assert "n, n" not in twice

    def test_the_source_path_is_checked_before_rewriting(self):
        with open(BENCHMARK) as fh:
            source = fh.read()
        assert "HPCG source not found" in source, (
            "a moved ComputeResidual.cpp must be reported, not silently skipped"
        )


INTEGRATION_SCRIPT = os.path.join(
    REPO_ROOT, "tests", "integration", "run_integration_test.sh"
)
DOCS = [
    os.path.join(REPO_ROOT, "README.md"),
    os.path.join(REPO_ROOT, "tests", "integration", "README.md"),
]


def _integration_flags():
    """Flags the integration script's case block actually accepts."""
    import re

    with open(INTEGRATION_SCRIPT) as fh:
        source = fh.read()
    block = source.split("while [[ $# -gt 0 ]]; do", 1)[1].split("done", 1)[0]
    return set(re.findall(r"^\s*(--[a-z-]+)\)", block, re.MULTILINE))


class TestIntegrationScriptDocsMatchTheScript:
    """The docs told operators to run `run_integration_test.sh --help`, which the
    script has never accepted — its case block exits 1 on any unknown argument.
    Copy-pasting the documented command produced `ERROR: Unknown argument`."""

    def test_the_script_accepts_the_flags_the_docs_advertise(self):
        import re

        accepted = _integration_flags()
        assert accepted, "no flags parsed out of the integration script"
        for doc in DOCS:
            with open(doc) as fh:
                text = fh.read()
            for match in re.finditer(r"run_integration_test\.sh([^\n]*(?:\\\n[^\n]*)*)", text):
                for flag in re.findall(r"--[a-z-]+", match.group(1)):
                    assert flag in accepted, (
                        f"{os.path.basename(doc)} documents "
                        f"run_integration_test.sh {flag}, which the script rejects "
                        f"with 'Unknown argument'. Accepted: {sorted(accepted)}"
                    )

    def test_an_unknown_flag_is_rejected_rather_than_ignored(self, tmp_path):
        """If the script ever grew a permissive fallback, the assertion above
        would stop meaning anything."""
        r = subprocess.run(
            ["bash", INTEGRATION_SCRIPT, "--help"], capture_output=True, cwd=tmp_path
        )
        assert r.returncode != 0
        assert b"Unknown argument" in r.stderr


def _render_wrapper(cluster_params):
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    return env.get_template("monitoring-post-install-wrapper.j2").render(**cluster_params)


class TestMonitoringWrapperProfileGuard:
    def test_rendered_wrapper_is_valid_bash(self, cluster_params):
        r = subprocess.run(
            ["bash", "-n"], input=_render_wrapper(cluster_params).encode(), capture_output=True
        )
        assert r.returncode == 0, r.stderr

    def test_sourcing_a_profile_with_an_unset_variable_does_not_abort(self, tmp_path, cluster_params):
        """A profile that references an unset variable on an export line is a
        fatal error under `set -u`, which the wrapper enables on line 2, so the
        monitoring install dies before it starts. Ubuntu's own /etc/profile is
        clean, so the guard looks removable — but a site can drop anything into
        /etc/profile.d, and the failure would only appear on that operator's
        cluster. It was written for the RPM-based /etc/profile, which exports
        $HISTCONTROL unconditionally, and matters on both supported families.

        This reproduces the guard's prologue against such a profile rather than
        asserting on the source text, so a guard that is present but no longer
        wraps the `source` still fails."""
        profile = tmp_path / "profile"
        profile.write_text('export HISTCONTROL="$HISTCONTROL"\n')

        wrapper = _render_wrapper(cluster_params)
        # Split at the first line after the guard rather than at prose that can be
        # reworded: a stale marker silently widens the prologue to the whole
        # wrapper, and the failure then looks like the guard is broken.
        marker = "\nMONITORING_VERSION="
        assert marker in wrapper, "the wrapper no longer sets MONITORING_VERSION"
        prologue = wrapper.split(marker, 1)[0]
        assert "source /etc/profile" in prologue, (
            "the /etc/profile source is no longer in the wrapper prologue"
        )
        prologue = prologue.replace("source /etc/profile", f"source {profile}")

        r = subprocess.run(
            ["bash", "-c", prologue + "\necho PROLOGUE_OK\n"],
            capture_output=True,
            env={"PATH": os.environ["PATH"]},
        )
        assert r.returncode == 0 and b"PROLOGUE_OK" in r.stdout, (
            f"the wrapper aborts when /etc/profile references an unset variable; "
            f"the guard around `source /etc/profile` is missing or "
            f"no longer encloses it. stderr: {r.stderr!r}"
        )

    def test_the_options_are_restored_after_the_profile_is_sourced(self, cluster_params):
        """A guard that suspends the options and never restores them silently
        disables the protection for the whole rest of the install.

        All three are suspended, not just -u: AL2023's debuginfod.sh assigns from
        a pipeline whose first stage fails, which is fatal under pipefail. The
        behavior is executed for real by
        tests/test_templates.py::TestTheProfileGuardSuspendsEveryOption, which
        covers this template too; this only pins the ordering."""
        wrapper = _render_wrapper(cluster_params)
        lines = [l.strip() for l in wrapper.splitlines()]
        assert "set -euo pipefail" in lines
        assert lines.index("set +euo pipefail") < lines.index("source /etc/profile")
        assert lines.index("source /etc/profile") < lines.index(
            "set -euo pipefail", lines.index("source /etc/profile")
        )


# Upstream installer/os/alinux2023.sh's compose download, verbatim from
# aws-parallelcluster-monitoring v2.6 -- a two-line `curl` continuation followed by
# a chmod that acts on whatever landed there. The wrapper's patch has to remove
# both curl lines and leave the chmod, so the shape is the fixture: a stub with a
# single-line curl would let a `,+0`-equivalent patch pass.
_UPSTREAM_AL2023_INSTALLER = """#!/bin/bash
set -euo pipefail
dnf -y install --allowerasing docker jq tar gzip
COMPOSE_VERSION="v2.29.7"
install -d -m 0755 /usr/libexec/docker/cli-plugins
curl -fsSL "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-$(uname -m)" \\
    -o /usr/libexec/docker/cli-plugins/docker-compose
chmod +x /usr/libexec/docker/cli-plugins/docker-compose
systemctl enable docker
"""


def _write_upstream_installer(monitoring_home, body=_UPSTREAM_AL2023_INSTALLER):
    os.makedirs(os.path.join(monitoring_home, "installer", "os"), exist_ok=True)
    path = os.path.join(monitoring_home, "installer", "os", "alinux2023.sh")
    with open(path, "w") as fh:
        fh.write(body)
    return path


def _run_wrapper(
    cluster_params,
    tmp_path,
    node_type="HeadNode",
    tree=False,
    upstream_body=None,
    neuter_the_patch=False,
    extra_env=None,
    background=False,
):
    """Execute the rendered wrapper under real bash with the world stubbed.

    Whether the extraction is gated is a runtime property of the `if`, so it is
    checked by recording what the script actually did. A text assertion cannot
    tell a gated block from an ungated one.

    The script's own `set -euo pipefail` is kept -- unlike postinstall's harness
    there is no head-node path that needs a stubbed mkdir's directories to
    exist, and `set -u` is what makes an unset cfn_node_type abort instead of
    defaulting.

    node_type is written into a fake cfnconfig as cfn_node_type, which is where
    ParallelCluster publishes it; node_type=None writes a cfnconfig with no
    cfn_node_type at all. tree=True pre-creates the installer the head node
    would have left on NFS-exported /home. upstream_body overrides the fake
    upstream alinux2023.sh, for the case where its shape changed.

    neuter_the_patch replaces the compose patch's awk program with `cat`, leaving
    the post-patch verification intact. The two share a predicate on purpose, so
    breaking the patch is the only way to exercise the check.

    extra_env is merged into the subprocess environment -- used to override the
    LoginNode wait/poll seconds so a test does not actually wait 300s.

    background=True launches the script via Popen and returns the Popen object
    in place of a CompletedProcess, letting a caller create the tree mid-wait
    to exercise the retry-then-succeed path; the caller is responsible for
    waiting on it and reading trace/stdout/stderr only after it exits.

    Returns (CompletedProcess, trace, monitoring_home), or (Popen, None, monitoring_home)
    when background=True.
    """
    root = str(tmp_path)
    # Callers pass tmp_path / "<name>" for a second, independent run.
    os.makedirs(root, exist_ok=True)
    wrapper = _render_wrapper(cluster_params)

    for original, replacement in (
        ("/etc/profile", f"{root}/profile"),
        ("/etc/parallelcluster/cfnconfig", f"{root}/cfnconfig"),
        ("/var/log/parallelcluster-monitoring-install.log", f"{root}/install.log"),
        ('/home/${CLUSTER_USER}', root + '/home/${CLUSTER_USER}'),
        ('/tmp/${TARBALL}', root + '/${TARBALL}'),
    ):
        assert original in wrapper, f"the wrapper no longer references {original}"
        wrapper = wrapper.replace(original, replacement)

    # Only rendered when stage_docker_compose is true, so this one is conditional
    # -- but it must be redirected when it IS present: `install -d` and `chmod`
    # against the real /usr/libexec would need root and would touch the
    # developer's own machine.
    compose_dir = "/usr/libexec/docker/cli-plugins"
    staged_compose = cluster_params.get("stage_docker_compose") == "true"
    assert staged_compose == (compose_dir in wrapper), (
        f"the compose plugin block's presence disagrees with "
        f"stage_docker_compose={cluster_params.get('stage_docker_compose')!r}"
    )
    wrapper = wrapper.replace(compose_dir, f"{root}/libexec/docker/cli-plugins")

    if neuter_the_patch:
        assert staged_compose, "neuter_the_patch needs the compose block rendered"
        marker = '\tawk "$_compose_fetch"'
        assert marker in wrapper, "the compose patch is no longer an awk program"
        head, _, tail = wrapper.partition(marker)
        end = tail.index("' \"$_al2023_installer\" > ") + len("' ")
        wrapper = head + "\tcat " + tail[end:]

    with open(os.path.join(root, "profile"), "w"):
        pass
    with open(os.path.join(root, "cfnconfig"), "w") as fh:
        fh.write("cfn_cluster_user=ubuntu\n")
        if node_type is not None:
            fh.write(f"cfn_node_type={node_type}\n")

    # What the tar stub below unpacks, and what tree=True pre-places: the same
    # bytes either way, so the head node (which extracts) and a compute node
    # (which reads the extracted copy) are testing against one fixture.
    with open(os.path.join(root, "upstream_alinux2023.sh"), "w") as fh:
        fh.write(upstream_body or _UPSTREAM_AL2023_INSTALLER)

    monitoring_home = os.path.join(root, "home", "ubuntu", "aws-parallelcluster-monitoring")
    if tree:
        os.makedirs(os.path.join(monitoring_home, "installer"))
        with open(os.path.join(monitoring_home, "installer", "install.sh"), "w") as fh:
            fh.write('#!/bin/bash\necho install.sh >> "$TRACE"\n')
        _write_upstream_installer(
            monitoring_home, upstream_body or _UPSTREAM_AL2023_INSTALLER
        )

    # rm and mkdir run for real so the tree's survival is observable, which is
    # the whole property under test -- but a failed path substitution above
    # would then point them at the developer's own /home. The guard turns that
    # into a loud failure instead of data loss.
    harness = f"""
    export TRACE={root}/trace
    : > "$TRACE"
    _log() {{ echo "$*" >> "$TRACE"; }}
    _guard() {{
        for _a in "$@"; do
            case "$_a" in
                -*) ;;
                {root}/*) ;;
                *) echo "HARNESS: refusing to touch $_a outside the tmpdir" >&2
                   return 1 ;;
            esac
        done
    }}
    rm() {{ _log "rm $*"; _guard "$@" || return 1; command rm "$@"; }}
    mkdir() {{ _log "mkdir $*"; _guard "$@" || return 1; command mkdir "$@"; }}
    tar() {{
        _log "tar $*"
        local _dest=""
        while [ $# -gt 0 ]; do
            [ "$1" = "-C" ] && {{ _dest="$2"; shift; }}
            shift
        done
        if [ -n "$_dest" ]; then
            command mkdir -p "$_dest/installer/os"
            printf '%s\\n' '#!/bin/bash' 'echo install.sh >> "$TRACE"' \\
                > "$_dest/installer/install.sh"
            command cp {root}/upstream_alinux2023.sh "$_dest/installer/os/alinux2023.sh"
        fi
        return 0
    }}
    systemctl() {{ _log "systemctl $*"; return 1; }}
    chown() {{ _log "chown $*"; return 0; }}
    # `aws s3 cp` has to materialize its destination: the compose block chmods
    # what it just downloaded, and under `set -e` a stub that only logs would
    # abort there -- which looks like the block is broken rather than stubbed.
    aws() {{
        _log "aws $*"
        if [ "${{1:-}}" = "s3" ] && [ "${{2:-}}" = "cp" ] && [ -n "${{4:-}}" ]; then
            printf 'stub compose binary\\n' > "$4"
        fi
        return 0
    }}
    cd {root}
    """

    script = os.path.join(root, "wrapper.sh")
    with open(script, "w") as fh:
        fh.write(harness + "\n" + wrapper)
    env = {**os.environ, **(extra_env or {})}
    if background:
        p = subprocess.Popen(
            ["bash", script], cwd=root, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        return p, None, monitoring_home
    r = subprocess.run(["bash", script], capture_output=True, cwd=root, env=env)
    with open(os.path.join(root, "trace")) as fh:
        trace = fh.read()
    return r, trace, monitoring_home


class TestMonitoringWrapperOnlyTheHeadNodeWritesTheTree:
    """MONITORING_HOME is /home/<cluster_user>/aws-parallelcluster-monitoring, and
    /home is NFS-exported from the head node to every compute node -- verified in
    a compute node's own chef log (`mount 10.0.1.10:/home to /home`). Upstream's
    installer/install.sh computes that path itself with no override, so a
    node-local prefix is not available.

    The wrapper used to `rm -rf` and re-extract it on every node. On 2026-07-27
    two c5.2xlarge nodes booting 92ms apart destroyed each other's tree: one
    failed OnNodeConfigured with rc=127 because install.sh had been deleted
    between its own tar and the bash that runs it, the other with rc=1, counting
    2 toward the partition's 10-failure protected-mode threshold. The race is
    intermittent -- the relaunched pair happened to survive -- which is exactly
    why it needs a test rather than a rebuild."""

    def test_the_head_node_still_extracts_the_tree(self, cluster_params, tmp_path):
        r, trace, home = _run_wrapper(cluster_params, tmp_path, "HeadNode")
        assert r.returncode == 0, (
            f"the head node no longer completes.\nstdout: {r.stdout.decode()}\n"
            f"stderr: {r.stderr.decode()}"
        )
        assert "aws s3 cp" in trace, f"the head node did not fetch the tarball: {trace}"
        assert "tar " in trace, f"the head node did not extract the tarball: {trace}"
        assert "chown " in trace, f"the head node did not chown the tree: {trace}"
        assert "install.sh" in trace, f"the installer never ran: {trace}"
        assert os.path.isfile(os.path.join(home, "installer", "install.sh"))

    def test_a_compute_node_never_writes_the_shared_tree(self, cluster_params, tmp_path):
        r, trace, _ = _run_wrapper(cluster_params, tmp_path, "ComputeFleet", tree=True)
        assert r.returncode == 0, (
            f"the wrapper aborts on a compute node.\nstdout: {r.stdout.decode()}\n"
            f"stderr: {r.stderr.decode()}"
        )
        for forbidden in ("aws s3 cp", "rm ", "mkdir ", "tar ", "chown "):
            assert forbidden not in trace, (
                f"a compute node ran {forbidden!r} against NFS-exported "
                f"MONITORING_HOME; concurrent nodes destroy each other's tree "
                f"and fail OnNodeConfigured. trace: {trace}"
            )
        assert "install.sh" in trace, (
            f"the compute node did not run the installer, so the exporters never "
            f"start: {trace}"
        )

    def test_the_tree_the_compute_node_reads_survives_the_run(
        self, cluster_params, tmp_path
    ):
        """The trace assertion above pins the commands; this pins the outcome
        they exist to protect, so an extraction reintroduced by some other
        spelling is still caught."""
        stamp = '#!/bin/bash\necho install.sh >> "$TRACE"\n'
        r, _, home = _run_wrapper(cluster_params, tmp_path, "ComputeFleet", tree=True)
        assert r.returncode == 0, r.stderr.decode()
        with open(os.path.join(home, "installer", "install.sh")) as fh:
            assert fh.read() == stamp, (
                "the compute node replaced the head node's installer"
            )

    def test_a_compute_node_fails_loudly_when_the_tree_is_absent(
        self, cluster_params, tmp_path
    ):
        """A compute node cannot create the tree, so if it is missing the head
        node's install did not complete. Exiting 0 there would leave the node up
        with no exporters and nothing in the log to explain it -- and `bash
        $MONITORING_HOME/installer/install.sh` on an absent file is rc=127, the
        same opaque code the race produced."""
        r, trace, _ = _run_wrapper(cluster_params, tmp_path, "ComputeFleet", tree=False)
        assert r.returncode != 0, "a compute node with no monitoring tree exited 0"
        output = (r.stdout + r.stderr).decode()
        assert "install.sh" in output, (
            f"the failure does not name what is missing: {output!r}"
        )
        # Deleting the elif arm also exits non-zero -- bash on a missing script is
        # rc=127 -- so the diagnosis is the property, not the exit status.
        assert "the head node's monitoring install did not complete" in output, (
            f"the readability check is gone; the operator gets bash's bare "
            f"'No such file or directory' instead of the cause: {output!r}"
        )
        assert "aws s3 cp" not in trace, (
            f"the failure path still tried to fetch the tarball: {trace}"
        )
        # Dropping the `exit 1` leaves the two echoes in place and bash still
        # exits 127 on the absent script, so neither the status nor the message
        # can see it. What changes is that the run continues into the installer
        # invocation and dumps 80 lines of an install that never started.
        assert "monitoring install FAILED" not in output, (
            f"the wrapper carried on and ran the installer it just reported "
            f"missing; the `exit 1` in the elif arm is gone: {output!r}"
        )

    def test_a_login_node_does_not_write_the_shared_tree(
        self, cluster_params, tmp_path
    ):
        """The gate must name the one node type that may write, not exclude the
        one that may not. Spelling it `!= "ComputeFleet"` is equivalent only
        while those two values are the whole world -- ParallelCluster also has
        login nodes, and they NFS-mount /home from the head node exactly as
        compute nodes do, so one would reintroduce the race. LoginNode has its
        own named branch (see TestMonitoringWrapperLoginNodeBootRace below for
        the wait/retry it needs that ComputeFleet does not); this test's only
        concern is that, tree already present, it never writes."""
        r, trace, _ = _run_wrapper(cluster_params, tmp_path, "LoginNode", tree=True)
        for forbidden in ("aws s3 cp", "rm ", "mkdir ", "tar ", "chown "):
            assert forbidden not in trace, (
                f"node type LoginNode ran {forbidden!r} against NFS-exported "
                f"MONITORING_HOME: {trace}"
            )
        assert r.returncode == 0 or b"install.sh" in r.stdout + r.stderr

    def test_a_cfnconfig_without_cfn_node_type_aborts_rather_than_defaulting(
        self, cluster_params, tmp_path
    ):
        """`${cfn_node_type:-HeadNode}` is the mutation that recreates the bug:
        every node would read as a head node and extract. `set -u` with no
        default is what makes a changed upstream contract fail instead. The
        wrapper only ever runs as a ParallelCluster custom action, so there is no
        off-cluster case to accommodate -- CLUSTER_USER is already unguarded for
        the same reason."""
        r, trace, _ = _run_wrapper(cluster_params, tmp_path, node_type=None, tree=True)
        assert r.returncode != 0, (
            "a cfnconfig with no cfn_node_type must not be treated as a head node"
        )
        assert "tar " not in trace, f"it extracted anyway: {trace}"

    def test_the_harness_can_see_an_ungated_extraction(self, cluster_params, tmp_path):
        """Guards the two negative tests above against passing vacuously: if the
        stubs or the path substitutions silently stopped working, an unmodified
        head-node run would log nothing either."""
        _, trace, _ = _run_wrapper(cluster_params, tmp_path, "HeadNode")
        for expected in ("aws s3 cp", "rm ", "mkdir ", "tar ", "chown "):
            assert expected in trace, (
                f"the harness records no {expected!r} even on a head node, so the "
                f"compute-node assertions prove nothing: {trace}"
            )


class TestMonitoringWrapperSkipsLoginNodes:
    """Upstream's installer supports two node types and no more: its header
    says "ParallelCluster HeadNode and ComputeFleet nodes" and its
    `case "${PLATFORM_NODE_TYPE}"` has arms for exactly those. A login node
    falls through `verify_docker` and matches nothing, so the run fails --
    and this wrapper exits with the installer's status, so that failure
    became the custom action's, the node was marked unhealthy, and its Auto
    Scaling Group replaced it. Observed live: three login nodes launched and
    abandoned on Heartbeat Timeout across 45 minutes, the stack never
    leaving CREATE_IN_PROGRESS.

    This class replaced TestMonitoringWrapperLoginNodeBootRace, which pinned
    a bounded poll for MONITORING_HOME. That poll was a correct answer to
    the wrong question: it made the login node wait for a tree it was then
    going to fail on anyway, and cost every login node up to 300s of boot
    time to do it.
    """

    def test_a_login_node_does_no_work_and_exits_zero(
        self, cluster_params, tmp_path
    ):
        """Whether or not the tree is present -- there is nothing here for a
        login node either way."""
        for tree in (False, True):
            r, trace, _ = _run_wrapper(
                cluster_params, tmp_path, "LoginNode", tree=tree,
            )
            assert r.returncode == 0, (
                f"tree={tree}: a login node must not fail this wrapper; "
                f"{(r.stdout + r.stderr).decode()[:300]}"
            )
            for forbidden in ("aws s3 cp", "rm ", "mkdir ", "tar ", "chown "):
                assert forbidden not in trace, (
                    f"tree={tree}: login node ran {forbidden!r}"
                )

    def test_it_never_runs_the_installer(self, cluster_params, tmp_path):
        """The defect itself. The installer is what fails on a login node,
        so the assertion is on the execution trace, not on the exit status:
        a stubbed installer returns 0 and would hide this entirely."""
        r, trace, _ = _run_wrapper(
            cluster_params, tmp_path, "LoginNode", tree=True,
        )
        assert "install.sh" not in trace, (
            f"the login node invoked the installer: {trace}"
        )

    def test_it_does_not_wait(self, cluster_params, tmp_path):
        """With nothing to install there is nothing to wait for. The old
        poll ran up to 300s per login node boot."""
        start = time.time()
        r, _, _ = _run_wrapper(cluster_params, tmp_path, "LoginNode", tree=False)
        assert r.returncode == 0
        assert time.time() - start < 10, "the login node arm is still waiting"

    def test_it_says_why_it_did_nothing(self, cluster_params, tmp_path):
        """Silence here reads as a broken custom action to whoever is
        reading the bootstrap log."""
        r, _, _ = _run_wrapper(cluster_params, tmp_path, "LoginNode", tree=True)
        out = (r.stdout + r.stderr).decode()
        assert "HeadNode" in out and "ComputeFleet" in out, out

    def test_the_head_node_still_installs(self, cluster_params, tmp_path):
        """Vacuity guard: 'skip the installer' must not become 'skip it
        everywhere'."""
        r, trace, _ = _run_wrapper(
            cluster_params, tmp_path, "HeadNode", tree=True,
        )
        assert "install.sh" in trace, (
            f"the head node no longer runs the installer: {trace}"
        )


class TestTheDockerComposePluginIsStagedNotFetchedFromGitHub:
    """Amazon Linux 2023 is the one supported base_os with no
    docker-compose-plugin package -- confirmed absent from the al2023 core repo on
    x86_64 and aarch64 alike -- so upstream's installer/os/alinux2023.sh curls the
    binary from github.com with no checksum. That fails outright on a private
    subnet and is an unverified download everywhere else, which is the same reason
    the monitoring tarball itself is staged to S3.

    Two properties, and they pull in opposite directions on node type:

    - The plugin must be installed on EVERY node. install.sh sources the OS script
      and then calls verify_docker (`docker compose version`) BEFORE it branches on
      PLATFORM_NODE_TYPE, so a compute node without the plugin dies there.
    - The tree may only be patched by the head node, because MONITORING_HOME is
      NFS-exported from it (see the class above).

    So the block deliberately straddles the gate, and both halves need a runtime
    check: a text assertion cannot tell which side of an `if` a line sits on."""

    def _plugin(self, tmp_path):
        return os.path.join(
            str(tmp_path), "libexec", "docker", "cli-plugins", "docker-compose"
        )

    def _upstream(self, home):
        with open(os.path.join(home, "installer", "os", "alinux2023.sh")) as fh:
            return fh.read()

    def test_the_head_node_installs_the_plugin_from_s3(
        self, cluster_params_al2023_monitoring, tmp_path
    ):
        r, trace, _ = _run_wrapper(
            cluster_params_al2023_monitoring, tmp_path, "HeadNode"
        )
        assert r.returncode == 0, (
            f"the head node no longer completes.\nstdout: {r.stdout.decode()}\n"
            f"stderr: {r.stderr.decode()}"
        )
        assert "docker-compose" in trace, f"the plugin was never fetched: {trace}"
        assert os.path.isfile(self._plugin(tmp_path)), (
            "the compose plugin is not on disk after a head node run"
        )

    def test_a_compute_node_installs_the_plugin_too(
        self, cluster_params_al2023_monitoring, tmp_path
    ):
        """This is the half that is easy to get wrong: the natural instinct is to
        put the whole block behind the head-node gate alongside the tree patch.
        verify_docker runs before install.sh branches on node type, so a compute
        node with no plugin fails OnNodeConfigured -- and clustermgtd relaunches
        it until the partition hits its 10-failure protected-mode threshold."""
        r, trace, _ = _run_wrapper(
            cluster_params_al2023_monitoring, tmp_path, "ComputeFleet", tree=True
        )
        assert r.returncode == 0, (
            f"the wrapper aborts on a compute node.\nstdout: {r.stdout.decode()}\n"
            f"stderr: {r.stderr.decode()}"
        )
        assert os.path.isfile(self._plugin(tmp_path)), (
            f"a compute node did not install the compose plugin, so install.sh's "
            f"verify_docker will fail it before it reaches the compute branch. "
            f"trace: {trace}"
        )

    def test_only_the_head_node_patches_the_shared_installer(
        self, cluster_params_al2023_monitoring, tmp_path
    ):
        """The patch writes into MONITORING_HOME, which every compute node
        NFS-mounts. The head node has already patched it by the time any compute
        node boots (cfn-init ordering, see the class above), so a compute node
        rewriting it is pure race."""
        r, _, home = _run_wrapper(
            cluster_params_al2023_monitoring, tmp_path, "ComputeFleet", tree=True
        )
        assert r.returncode == 0, r.stderr.decode()
        assert self._upstream(home) == _UPSTREAM_AL2023_INSTALLER, (
            "a compute node rewrote the head node's installer/os/alinux2023.sh"
        )

    def test_the_head_node_removes_upstreams_github_download(
        self, cluster_params_al2023_monitoring, tmp_path
    ):
        """Pre-placing the binary is not enough on its own: upstream's curl is
        unconditional, so it would overwrite the staged copy and then fail on a
        private subnet. Both continuation lines have to go, and the chmod below
        them must survive -- it is what makes the staged binary executable in
        upstream's own flow."""
        r, _, home = _run_wrapper(
            cluster_params_al2023_monitoring, tmp_path, "HeadNode"
        )
        assert r.returncode == 0, r.stderr.decode()
        patched = self._upstream(home)
        assert "github.com/docker/compose/releases" not in patched, (
            f"upstream's github.com download survived the patch:\n{patched}"
        )
        assert "-o /usr/libexec/docker/cli-plugins/docker-compose" not in patched, (
            f"the curl's second line survived, so `-o <path>` is left as a bare "
            f"command and the node dies on it:\n{patched}"
        )
        assert "chmod +x /usr/libexec/docker/cli-plugins/docker-compose" in patched, (
            f"the patch removed one line too many and took upstream's chmod with "
            f"it:\n{patched}"
        )
        r2 = subprocess.run(
            ["bash", "-n"], input=patched.encode(), capture_output=True
        )
        assert r2.returncode == 0, (
            f"the patched installer is no longer valid bash: {r2.stderr!r}"
        )

    def test_patching_twice_is_a_no_op(
        self, cluster_params_al2023_monitoring, tmp_path
    ):
        """The head node's own postinstall can be re-run by hand, and a
        line-offset delete (`sed '/pat/,+1d'` and friends) eats two more lines on
        every pass. Deleting whole logical commands is what makes this idempotent,
        so the second pass is the property, not the guard around it."""
        r, _, home = _run_wrapper(
            cluster_params_al2023_monitoring, tmp_path, "HeadNode"
        )
        assert r.returncode == 0, r.stderr.decode()
        once = self._upstream(home)

        r2, _, home2 = _run_wrapper(
            cluster_params_al2023_monitoring,
            tmp_path / "again",
            "HeadNode",
            upstream_body=once,
        )
        assert r2.returncode == 0, r2.stderr.decode()
        assert self._upstream(home2) == once, (
            f"a second patch of an already-patched installer changed it:\n"
            f"first pass:\n{once}\nsecond pass:\n{self._upstream(home2)}"
        )

    # Three reshapes of upstream's fetch, none of them contrived: the flag order
    # is arbitrary, collapsing a continuation is a routine tidy-up, and wget is
    # the obvious substitution on an image where curl-minimal conflicts (which
    # upstream's own comment in this very file complains about). A line-offset
    # patch mangles the first two into invalid bash and misses the third
    # entirely.
    _RESHAPES = {
        "o_flag_before_the_url": (
            "curl -fsSL \\\n"
            "    -o /usr/libexec/docker/cli-plugins/docker-compose \\\n"
            '    "https://github.com/docker/compose/releases/download/'
            '${COMPOSE_VERSION}/docker-compose-linux-$(uname -m)"\n'
        ),
        "collapsed_to_one_line": (
            "curl -fsSL -o /usr/libexec/docker/cli-plugins/docker-compose "
            '"https://github.com/docker/compose/releases/download/'
            '${COMPOSE_VERSION}/docker-compose-linux-$(uname -m)"\n'
        ),
        "wget_from_another_host": (
            "wget -q -O /usr/libexec/docker/cli-plugins/docker-compose \\\n"
            '    "https://mirror.example.com/docker-compose-linux-$(uname -m)"\n'
        ),
    }

    _UPSTREAM_FETCH = (
        'curl -fsSL "https://github.com/docker/compose/releases/download/'
        '${COMPOSE_VERSION}/docker-compose-linux-$(uname -m)" \\\n'
        "    -o /usr/libexec/docker/cli-plugins/docker-compose\n"
    )

    @pytest.mark.parametrize("shape", sorted(_RESHAPES))
    def test_a_reshaped_upstream_download_is_still_removed(
        self, shape, cluster_params_al2023_monitoring, tmp_path
    ):
        """The patch matches a downloader invocation that mentions
        docker-compose, joined across continuations -- not a line spelling. That
        is deliberate: a partial delete leaves invalid bash behind, and a missed
        delete lets upstream overwrite the staged binary and fetch over the
        network from a node that may have none."""
        assert self._UPSTREAM_FETCH in _UPSTREAM_AL2023_INSTALLER
        body = _UPSTREAM_AL2023_INSTALLER.replace(
            self._UPSTREAM_FETCH, self._RESHAPES[shape]
        )

        r, _, home = _run_wrapper(
            cluster_params_al2023_monitoring, tmp_path, "HeadNode", upstream_body=body
        )
        assert r.returncode == 0, (
            f"the wrapper aborted on a {shape} fetch.\nstdout: {r.stdout.decode()}\n"
            f"stderr: {r.stderr.decode()}"
        )
        patched = self._upstream(home)
        for downloader in ("curl", "wget"):
            assert downloader not in patched, (
                f"a {shape} fetch survived the patch:\n{patched}"
            )
        assert "chmod +x /usr/libexec/docker/cli-plugins/docker-compose" in patched, (
            f"the patch took upstream's chmod with it:\n{patched}"
        )
        r2 = subprocess.run(
            ["bash", "-n"], input=patched.encode(), capture_output=True
        )
        assert r2.returncode == 0, (
            f"the patched installer is not valid bash, which is a dead node: "
            f"{r2.stderr!r}\n{patched}"
        )

    def test_the_verification_fires_when_the_patch_does_nothing(
        self, cluster_params_al2023_monitoring, tmp_path
    ):
        """Guards the post-patch check itself. The patch and the check share a
        predicate, so no upstream body can satisfy one and not the other -- the
        only way to exercise the check is to break the patch. Here the wrapper's
        patch program is neutered in the rendered text and the check must still
        catch the surviving download."""
        r, _, _ = _run_wrapper(
            cluster_params_al2023_monitoring,
            tmp_path,
            "HeadNode",
            neuter_the_patch=True,
        )
        assert r.returncode != 0, (
            "the wrapper accepted an installer whose compose download it failed "
            "to remove; upstream will overwrite the staged binary and fetch over "
            "the network at node boot"
        )
        output = (r.stdout + r.stderr).decode()
        assert "survived the patch" in output, (
            f"the failure does not say what went wrong: {output!r}"
        )

    def test_no_block_at_all_when_the_plugin_is_not_staged(
        self, cluster_params_monitoring_enabled, tmp_path
    ):
        """Every other base_os packages docker-compose-plugin, so the block must
        not render there: the S3 object does not exist, and `aws s3 cp` on a
        missing key is a failed node. stage_docker_compose is
        `enable_monitoring and 'alinux' in base_os` for exactly that reason."""
        r, trace, _ = _run_wrapper(
            cluster_params_monitoring_enabled, tmp_path, "HeadNode"
        )
        assert r.returncode == 0, r.stderr.decode()
        assert "cli-plugins" not in trace, (
            f"an Ubuntu cluster tried to install the compose plugin from S3: {trace}"
        )
        assert not os.path.exists(self._plugin(tmp_path))

    def test_the_harness_can_see_an_unpatched_installer(
        self, cluster_params_al2023_monitoring, tmp_path
    ):
        """Guards the assertions above against passing vacuously. If the fixture
        or the tar stub stopped delivering upstream's script, `patched` would be
        an empty string and every `not in` assertion would pass."""
        assert "github.com/docker/compose/releases" in _UPSTREAM_AL2023_INSTALLER
        r, _, home = _run_wrapper(
            cluster_params_al2023_monitoring, tmp_path, "ComputeFleet", tree=True
        )
        assert r.returncode == 0, r.stderr.decode()
        assert "github.com/docker/compose/releases" in self._upstream(home), (
            "the unpatched fixture does not reach the tree, so the head-node "
            "patch assertions prove nothing"
        )


PERF_DOC_TEMPLATE = os.path.join(
    REPO_ROOT, "hpc-benchmark", "README-PERFORMANCE.md.j2"
)
PERF_DOC_RENDERED = os.path.join(REPO_ROOT, "hpc-benchmark", "README-PERFORMANCE.md")


class TestPerformanceDocsMatchTheBenchmarkDriver:
    """STREAM is the only tool built with -march=native, so it is the only one
    whose binary is specific to a microarchitecture and the only one the driver
    rebuilds per node class. The docs describe that split; these tests fail if
    the driver changes in a way that makes the documented advice wrong."""

    def test_only_stream_is_built_with_march_native(self):
        """The docs tell operators OSU, IOR, and HPCG are portable within an
        architecture. Adding -march=native to another tool's build silently
        invalidates that, and would also make .build_arch insufficient for them."""
        with open(BENCHMARK) as fh:
            lines = fh.read().splitlines()
        # Lines that hand the flag to a compiler or build system, however it is
        # spelled: a direct compiler call, a configure CFLAGS/CXXFLAGS
        # assignment, or a make variable. Excluded are comments, the probe in
        # _native_march (which asks gcc what the flag resolves to and compiles
        # nothing), and prose in usage text and warning messages.
        probe = _function_body("_native_march").splitlines()
        build_lines = [
            line
            for line in lines
            if "march=native" in line
            and not line.lstrip().startswith("#")
            and line not in probe
            and not line.lstrip().startswith("echo ")
            and any(
                tok in line for tok in ("gcc ", "CFLAGS", "CXXFLAGS", "cc ", "make ")
            )
        ]
        assert len(build_lines) == 1, (
            f"expected exactly one -march=native compile line, found {build_lines}"
        )
        # That line must be _compile_stream's, which only the stream arms call.
        assert build_lines[0] in _function_body("_compile_stream"), (
            f"-march=native appears outside _compile_stream: {build_lines[0]!r}"
        )

    def test_the_arch_stamp_records_only_uname_m(self):
        """The stamp guards the configure/make-built tools, which are portable
        across microarchitectures but not architectures. STREAM's own guard is
        the per-microarchitecture binary name, not this stamp; recording finer
        detail here would make the stamp reject valid OSU/IOR/HPCG binaries."""
        body = _function_body("_write_arch_stamp")
        assert "uname -m" in body, body
        for finer in ("cpuinfo", "vendor_id", "model name", "-Q --help=target"):
            assert finer not in body, (
                f"the stamp now records {finer!r}, which would make it reject "
                "OSU/IOR/HPCG binaries that are valid on this host"
            )

    def test_the_docs_describe_cuda_as_following_the_build_node(self):
        """The GPU section used to state flatly that OSU cannot measure the GPU
        interconnect because CUDA is compiled out. That is now conditional on the
        node that ran install, and the docs have to say which node and how to
        change the answer -- otherwise an operator on a CPU head node reads the
        absent device-to-device results as a bug."""
        for path in (PERF_DOC_TEMPLATE, PERF_DOC_RENDERED):
            with open(path) as fh:
                text = fh.read()
            assert "--enable-cuda=no" in text, (
                f"{path} does not document the host-to-host-only build"
            )
            assert "`=yes`" in text and "`=basic`" in text, (
                f"{path} does not document both CUDA build modes"
            )
            assert "nvidia-smi" in text, (
                f"{path} does not say what decides whether CUDA is built at all"
            )
            assert "nvcc" in text, (
                f"{path} does not say what decides =yes vs =basic. An operator "
                "who reads =yes as unconditional will report the =basic build "
                "as a defect"
            )
            assert "bin/osu-cuda" in text, (
                f"{path} does not tell the operator how a CPU-head-node cluster "
                "gets device-to-device results. This used to be a manual srun "
                "into the gpu partition and is now automatic; an operator "
                "following stale instructions rebuilds a tree by hand for "
                "nothing"
            )
            assert "latency_cuda.txt" in text, (
                f"{path} does not name the device-to-device result files"
            )

    def test_the_job_template_does_not_still_prescribe_the_manual_build(self):
        """The job script's GPU section told the operator to `srun -p gpu --pty
        bash` and re-run install by hand. run now builds bin/osu-cuda itself, so
        those instructions produce a second tree for no benefit -- and nothing
        pinned that comment block, which is how it survived the change."""
        with open(
            os.path.join(REPO_ROOT, "hpc-benchmark", "job_hpc-benchmark.sh.j2")
        ) as fh:
            text = fh.read()
        assert "srun" not in text, (
            "the job script still tells the operator to build OSU from an "
            "interactive allocation; run does that automatically now"
        )
        assert "bin/osu-cuda" in text, (
            "the job script does not say where the device-to-device build comes "
            "from on a CPU-head-node cluster"
        )
        assert "latency_cuda.txt" in text and "bandwidth_cuda.txt" in text, (
            "the job script does not name the device-to-device result files"
        )

    def test_the_microarchitecture_caveat_is_documented_in_both_copies(self):
        for path in (PERF_DOC_TEMPLATE, PERF_DOC_RENDERED):
            with open(path) as fh:
                text = fh.read()
            assert "microarchitecture" in text, f"{path} omits the caveat"
            assert "march=native" in text, f"{path} omits the cause"

    def test_the_rendered_doc_matches_the_template(self):
        """README-PERFORMANCE.md is generated from the .j2 with the cluster
        placeholders substituted; an edit to one copy only is drift."""
        with open(PERF_DOC_TEMPLATE) as fh:
            expected = (
                fh.read()
                .replace("{{ cluster_name }}", "<cluster_name>")
                .replace("{{ cluster_owner }}", "<cluster_owner>")
            )
        with open(PERF_DOC_RENDERED) as fh:
            actual = fh.read()
        assert actual == expected, (
            "README-PERFORMANCE.md is out of sync with README-PERFORMANCE.md.j2"
        )

    def test_the_partition_table_matches_what_the_job_template_emits(self):
        """The docs previously said the job script ships on partition `compute`
        unconditionally, which was true of the template and wrong for a GPU-only
        cluster. Both are now conditional; if the template's partition logic
        changes, the table has to change with it."""
        with open(
            os.path.join(REPO_ROOT, "hpc-benchmark", "job_hpc-benchmark.sh.j2")
        ) as fh:
            job_template = fh.read()
        assert "#SBATCH --partition={{" in job_template, (
            "the job script's partition is hardcoded again; a GPU-only cluster has "
            "no compute partition and sbatch rejects the job"
        )
        assert "gpu_ranks_per_node" in job_template, (
            "the job script no longer derives its GPU rank count from the "
            "instance types' NVIDIA GPU count"
        )
        for path in (PERF_DOC_TEMPLATE, PERF_DOC_RENDERED):
            with open(path) as fh:
                text = fh.read()
            assert "| GPU queue only | `gpu` |" in text, (
                f"{path} does not document that a GPU-only cluster targets the "
                "gpu partition"
            )
            assert "sbatch --partition=gpu --ntasks-per-node=" in text, (
                f"{path} does not document the GPU override for a mixed cluster"
            )
            assert "CPU head node can benchmark both partitions" in text, (
                f"{path} does not state that the head node's instance type is "
                "independent of the queues. The operator asked this directly, "
                "and the c5-head/g5-queue layout is the one the suite is "
                "designed around"
            )

    def test_the_docs_describe_the_s3_upload_as_the_allowlist_it_is(self):
        """The docs said the build "uploads the full performance source tree"
        to the cluster bucket, which was true when it was a
        blocklist sync of the whole tracked directory -- the shape that shipped
        hpc-benchmark/CLAUDE.md and two raw .j2 files into the operator's home.
        The sync is now `--exclude "*" --include "hpc-benchmark.sh"`, and a doc
        still promising the whole tree tells an operator that losing the head
        node's EBS root is recoverable when only the driver comes back.

        Pinned against the sync's own arguments rather than as prose, so the
        doc and the sync cannot drift apart in either direction."""
        import ast
        import inspect

        src = os.path.join(REPO_ROOT, "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        import pcluster_core

        argv = ast.unparse(
            ast.parse(
                inspect.getsource(
                    pcluster_core.stage_and_upload_hpc_benchmark_driver
                ).lstrip()
            )
        )
        assert "'--exclude', '*'" in argv and "'--include', 'hpc-benchmark.sh'" in argv, (
            "the driver upload is no longer an allowlist; if it went back to "
            "syncing the tree, these docs have to say so again"
        )
        for path in (PERF_DOC_TEMPLATE, PERF_DOC_RENDERED):
            with open(path) as fh:
                text = fh.read()
            assert "full performance source tree" not in text, (
                f"{path} still claims the whole tree is uploaded; the sync is an "
                "allowlist of hpc-benchmark.sh alone"
            )
            assert "and only that file" in text, (
                f"{path} does not say the upload is confined to the driver"
            )
            assert "is *not* on S3" in text, (
                f"{path} does not say the personalized slurm/ tree cannot be "
                "restored from S3, which is the half an operator acts on"
            )


class TestCompileStreamFailuresReachTheCaller:
    """Verified against bash 5.3, not assumed: a plain assignment from a command
    substitution propagates the failure under `set -e`, but one that is part of a
    builtin declaration does not -- `local v=$(f)` reports `local`'s own status, so
    the caller continues with v set to the failed command's partial stdout. Every
    _compile_stream call site therefore has to keep the `local v` / `v=$(...)`
    split, and no behavioral test can see the difference: both forms leave the
    driver aborting today, one because set -e fires and one because of the
    explicit || guard. Delete the guard from the split form and it still aborts;
    collapse the declaration and it stops aborting the moment the guard goes too."""

    @staticmethod
    def _assignments_from(body):
        return [
            l.strip() for l in body.splitlines()
            if "_compile_stream" in l and "=" in l.split("_compile_stream")[0]
        ]

    # Asserted individually below rather than one case standing in for the rest.
    # typeset is declare's synonym and swallows the failure identically; it was
    # missing from the guard's original blacklist, which is why the guard is now a
    # whitelist instead.
    _DECLARATIONS = ("local", "export", "readonly", "declare", "typeset")

    @pytest.mark.parametrize("func", ["cmd_install", "cmd_run"])
    def test_call_sites_are_bare_assignments(self, func):
        """A whitelist, not a blacklist of the builtins above. Any word before the
        variable name makes this a builtin declaration whose status set -e reads
        instead of the compile's, and a blacklist only rejects the forms someone
        remembered -- `typeset` sat outside the original list, and it swallows the
        failure exactly like `declare`."""
        for line in self._assignments_from(_function_body(func)):
            lhs = line.split("=")[0]
            assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", lhs), (
                f"{func}: `{line}` is not a bare assignment. Anything before the "
                f"variable name ({lhs!r}) makes this a builtin declaration, whose "
                "own exit status is what set -e sees -- a failed compile would no "
                "longer abort. Split it into a bare declaration on its own line "
                "and a plain assignment."
            )

    @pytest.mark.parametrize("builtin", _DECLARATIONS)
    def test_a_declaration_form_swallows_the_failure(self, builtin):
        """One case per builtin the guard above rejects. Asserting only `local`
        would leave the other four as claims about bash rather than facts, and the
        guard would be rejecting forms nothing had shown to be dangerous."""
        out = _run_bash(
            f"f() {{ echo partial; exit 3; }}\n"
            f"g() {{ {builtin} v=$(f); echo continued; }}\n"
            "( set -euo pipefail; g ); echo \"rc=$?\"\n"
        )
        assert "continued" in out, f"{builtin}: expected the caller to continue"
        assert "rc=0" in out, (
            f"{builtin} now propagates the failure under set -e: {out!r}"
        )

    @pytest.mark.parametrize(
        "form,body",
        [
            ("bare assignment", "v=$(f)"),
            ("split declaration", "local v; v=$(f)"),
        ],
    )
    def test_a_plain_assignment_propagates(self, form, body):
        """The other half of the rule, and the half the driver depends on: these are
        the forms every _compile_stream call site uses."""
        out = _run_bash(
            "f() { echo partial; exit 3; }\n"
            f"g() {{ {body}; echo continued; }}\n"
            "( set -euo pipefail; g ); echo \"rc=$?\"\n"
        )
        assert "continued" not in out, f"{form}: the caller should not have continued"
        assert "rc=3" in out, f"{form} no longer propagates: {out!r}"

    def test_the_swallowed_failure_leaves_a_plausible_value_behind(self):
        """Why the swallow is worse than a bare non-zero: the variable is not empty,
        it holds whatever the failed command managed to print, so the caller goes on
        to use it as a path. An `if [ -z "$v" ]` check would not catch this."""
        out = _run_bash(
            "f() { echo partial; exit 3; }\n"
            "g() { local v=$(f); echo \"v=[$v]\"; }\n"
            "( set -euo pipefail; g )\n"
        )
        assert "v=[partial]" in out, out


def _cuda_harness(tmp_path, script, *, gpu=False, toolkit=False,
                  header=True, runtime=True, cuda_home_env=None,
                  nvcc=None):
    """Drive the CUDA detection helpers with the GPU and the toolkit each faked.

    Both halves have to be independently controllable: a CPU head node fronting
    a GPU queue has neither, a DLAMI GPU node has both, and a GPU node whose
    toolkit was never installed has only the device -- which is the case that
    decides whether --enable-cuda=yes would abort OSU's configure.

    nvcc is a third axis, not a property of the toolkit tree: a node can carry
    cuda.h and libcudart with no compiler driver, which is what separates
    --enable-cuda=yes from =basic. None means absent, "path" puts it on PATH,
    "cuda_home" puts it only at $CUDA_HOME/bin/nvcc. Note that nvcc is
    deliberately not in _STUB_PASSTHROUGH -- PATH is replaced wholesale, so the
    host's own nvcc (if any) cannot answer for the stub.
    """
    stub = tmp_path / "stub"
    stub.mkdir(parents=True, exist_ok=True)
    if gpu:
        smi = stub / "nvidia-smi"
        smi.write_text(
            "#!/bin/bash\n"
            'echo "GPU 0: NVIDIA A10G (UUID: GPU-0000)"\n'
        )
        smi.chmod(0o755)
    for name in _STUB_PASSTHROUGH:
        real = shutil.which(name)
        if real and not (stub / name).exists():
            (stub / name).symlink_to(real)

    # header/runtime are separately suppressible: a toolkit tree with one half
    # missing is what distinguishes "checks both" from "checks whichever one
    # happens to be there", and a harness that omits the whole directory cannot
    # tell those apart.
    root = tmp_path / "cuda"
    if toolkit:
        (root / "include").mkdir(parents=True, exist_ok=True)
        (root / "lib64").mkdir(parents=True, exist_ok=True)
        if header:
            (root / "include" / "cuda.h").write_text("/* stub */\n")
        if runtime:
            (root / "lib64" / "libcudart.so").write_text("")

    assert nvcc in (None, "path", "cuda_home"), f"bad nvcc mode: {nvcc!r}"
    if nvcc == "path":
        fake = stub / "nvcc"
        fake.write_text("#!/bin/bash\nexit 0\n")
        fake.chmod(0o755)
    elif nvcc == "cuda_home":
        (root / "bin").mkdir(parents=True, exist_ok=True)
        fake = root / "bin" / "nvcc"
        fake.write_text("#!/bin/bash\nexit 0\n")
        fake.chmod(0o755)

    env = dict(os.environ, PATH=str(stub))
    env.pop("CUDA_HOME", None)
    if cuda_home_env is not None:
        env["CUDA_HOME"] = str(root) if cuda_home_env == "fake" else cuda_home_env
    return subprocess.run([_BASH, "-c", script], capture_output=True, env=env)


class TestOsuCudaFollowsTheBuildNode:
    """OSU's device-to-device tests need --enable-cuda=yes, and that flag makes
    configure AC_MSG_ERROR out on a missing -lcuda, -lcudart, or cuda.h rather
    than degrading. install runs on the head node, so keying the flag off the
    cluster's enable_gpu would abort the entire install on the common layout --
    a CPU head node fronting a GPU compute queue -- taking STREAM, IOR, and HPCG
    down with OSU. The signal has to be the build node's own hardware."""

    def _detect(self, tmp_path, **kw):
        script = f"""
        set -uo pipefail
        HPC_BENCHMARK_LIB_ONLY=1 source {BENCHMARK!r}
        if _host_has_gpu; then echo "GPU=yes"; else echo "GPU=no"; fi
        echo "CUDA_HOME=[$(_cuda_home)]"
        """
        return self._out(_cuda_harness(tmp_path, script, **kw))

    @staticmethod
    def _out(r):
        return r.stdout.decode()

    def test_a_cpu_node_reports_no_gpu(self, tmp_path):
        """The head node on the reference osiris config is a c5.xlarge."""
        assert "GPU=no" in self._detect(tmp_path, gpu=False)

    def test_a_gpu_node_with_a_toolkit_is_ready_for_cuda(self, tmp_path):
        out = self._detect(tmp_path, gpu=True, toolkit=True, cuda_home_env="fake")
        assert "GPU=yes" in out
        assert "CUDA_HOME=[]" not in out, "the toolkit under CUDA_HOME was not found"

    def test_a_gpu_node_without_a_toolkit_reports_no_cuda_home(self, tmp_path):
        """This is the case that would abort the build. The device is there, so a
        naive nvidia-smi-only check would pass --enable-cuda=yes to configure and
        fail on the missing cuda.h."""
        out = self._detect(tmp_path, gpu=True, toolkit=False, cuda_home_env="fake")
        assert "GPU=yes" in out
        assert "CUDA_HOME=[]" in out, (
            "a CUDA_HOME with no cuda.h/libcudart was accepted as a usable toolkit"
        )

    @pytest.mark.parametrize(
        "missing,kw",
        [
            ("cuda.h", {"header": False}),
            ("libcudart", {"runtime": False}),
        ],
    )
    def test_half_a_toolkit_is_not_a_toolkit(self, tmp_path, missing, kw):
        """configure tests for the header AND both libraries, so either half
        missing is still a build abort. Asserted separately because a probe that
        checks only one of them passes any test that removes the whole tree."""
        out = self._detect(tmp_path, gpu=True, toolkit=True,
                           cuda_home_env="fake", **kw)
        assert "CUDA_HOME=[]" in out, (
            f"a toolkit tree with no {missing} was accepted; --enable-cuda=yes "
            f"would be passed to configure and the OSU build would abort"
        )

    def test_a_cuda_home_pointing_nowhere_is_rejected(self, tmp_path):
        """A stale CUDA_HOME left in the environment must not be trusted."""
        out = self._detect(tmp_path, gpu=True, toolkit=False,
                           cuda_home_env="/nonexistent/cuda")
        assert "CUDA_HOME=[]" in out

    def _mode(self, tmp_path, **kw):
        script = f"""
        set -uo pipefail
        HPC_BENCHMARK_LIB_ONLY=1 source {BENCHMARK!r}
        home="$(_cuda_home)"
        echo "NVCC=[$(_cuda_nvcc "$home")]"
        echo "MODE=[$(_osu_cuda_mode "$home")]"
        """
        return self._out(_cuda_harness(tmp_path, script, **kw))

    @pytest.mark.parametrize("where", ["path", "cuda_home"])
    def test_nvcc_gets_the_kernel_build(self, tmp_path, where):
        """--enable-cuda=yes adds util/kernel.cu to UTILITIES in eleven
        Makefile.am files and compiles it with NVCC = nvcc. It is only a legal
        choice where nvcc actually exists.

        Both locations are asserted: CUDA's packages leave nvcc at
        /usr/local/cuda/bin without putting it on a login shell's PATH, so a
        PATH-only probe reports no compiler on a fully equipped node."""
        out = self._mode(tmp_path, gpu=True, toolkit=True,
                         cuda_home_env="fake", nvcc=where)
        assert "NVCC=[]" not in out, f"nvcc at {where} was not found: {out}"
        assert "MODE=[yes]" in out, out

    def test_no_nvcc_falls_back_to_basic_instead_of_failing_make(self, tmp_path):
        """configure never tests for nvcc, so =yes on a node with the runtime
        but no compiler driver configures cleanly and then dies in make. =basic
        still defines _ENABLE_CUDA_ -> CUDA_ENABLED 1, which is the only thing
        '-d cuda' reads, and every _ENABLE_CUDA_KERNEL_ block in osu_latency.c
        and osu_bw.c is additionally guarded on managed memory -- which run
        never requests. So =basic loses nothing this suite measures."""
        out = self._mode(tmp_path, gpu=True, toolkit=True,
                         cuda_home_env="fake", nvcc=None)
        assert "NVCC=[]" in out, out
        assert "MODE=[basic]" in out, (
            "a toolkit with no nvcc was given --enable-cuda=yes; configure "
            f"passes and the OSU build then fails in make: {out}"
        )

    def test_the_configure_mode_is_the_one_the_probe_chose(self):
        """The mode has to reach configure. Asserting both strings exist
        somewhere in cmd_install is what let the original hardcoded-=yes
        mutation survive."""
        body = _function_body("cmd_install")
        assert "_osu_cuda_mode" in body, (
            "cmd_install no longer asks which --enable-cuda value this node can "
            "build, so a node without nvcc gets =yes and fails in make"
        )
        assert '"--enable-cuda=$osu_cuda_mode"' in body, (
            "the CUDA args no longer carry the mode _osu_cuda_mode returned"
        )
        for line in body.splitlines():
            if "--enable-cuda=yes" in line and not line.lstrip().startswith("#"):
                raise AssertionError(
                    f"cmd_install hardcodes --enable-cuda=yes: {line.strip()!r}. "
                    "On a toolkit without nvcc that configures cleanly and then "
                    "fails in make."
                )

    def test_the_stamp_records_the_mode_not_just_the_path(self, tmp_path):
        """run has to be able to tell a =basic tree from a =yes one without
        re-probing the node, since it executes somewhere else -- and it has to be
        able to tell which MPI the tree is linked against, for the same reason."""
        body = _function_body("cmd_install")
        assert '"$osu_cuda_mode $osu_cuda_home $osu_mpi_root"' in body, (
            "the CUDA stamp does not record the mode and the MPI root. Without "
            "the MPI, run cannot tell a tree built against a non-CUDA-aware MPI "
            "from one it can actually launch -d cuda with, and both MPIs on a "
            "ParallelCluster GPU AMI ship SONAME libmpi.so.40"
        )

    def test_the_configure_flag_is_never_hardcoded(self):
        """The whole point: no literal --enable-cuda=yes/no outside the branch that
        chose it, and nothing keying the decision off a cluster-level flag."""
        body = _function_body("cmd_install")
        assert "--enable-cuda=no" in body, (
            "cmd_install can no longer build OSU without CUDA, which is the only "
            "thing a CPU head node can do"
        )
        assert '"--enable-cuda=$osu_cuda_mode"' in body, (
            "cmd_install can no longer build OSU with CUDA"
        )
        # The flags existing in the branch that chose them proves nothing if the
        # configure call ignores the result -- that is exactly the mutation where
        # both strings are still present and every invocation is CUDA-on. The
        # ./configure line itself must carry no literal --enable-cuda.
        configure = [
            line for line in body.splitlines() if "./configure" in line
        ]
        assert configure, "cmd_install no longer calls ./configure for OSU"
        for line in configure:
            assert "--enable-cuda" not in line, (
                f"the configure call hardcodes a CUDA mode: {line.strip()!r}. It "
                "must pass the array the hardware check populated, or a CPU head "
                "node's install aborts in configure"
            )
        assert '"${osu_cuda_args[@]}"' in body, (
            "the configure call no longer receives the CUDA args chosen by the "
            "hardware check"
        )
        assert "_host_has_gpu" in body, (
            "cmd_install does not consult the build node's hardware; if this became "
            "a cluster-level flag again, a CPU head node's install aborts"
        )
        # Comments are allowed to name the flag -- explaining why it is the wrong
        # signal is the point. Code that reads it is not.
        code = "\n".join(
            line for line in body.splitlines() if not line.lstrip().startswith("#")
        )
        assert "enable_gpu" not in code, (
            "cmd_install reads a cluster-level GPU flag. hpc-benchmark.sh is copied, "
            "not rendered, so it has no cluster vars -- and the head node's hardware "
            "is the thing that decides whether the build can even succeed"
        )

    def test_the_build_records_what_it_chose(self, tmp_path):
        """run executes on a compute node, not the node that built OSU, so the
        CUDA-ness of the binary cannot be re-derived there from local hardware."""
        script = f"""
        set -uo pipefail
        HPC_BENCHMARK_LIB_ONLY=1 source {BENCHMARK!r}
        mkdir -p "{tmp_path}/bin/osu"
        if _osu_cuda_enabled "{tmp_path}/bin"; then echo "BEFORE=yes"; else echo "BEFORE=no"; fi
        echo /opt/cuda > "$(_osu_cuda_stamp_path "{tmp_path}/bin")"
        if _osu_cuda_enabled "{tmp_path}/bin"; then echo "AFTER=yes"; else echo "AFTER=no"; fi
        """
        out = self._out(_cuda_harness(tmp_path, script))
        assert "BEFORE=no" in out and "AFTER=yes" in out, out

    def test_a_rebuild_without_cuda_clears_the_stamp(self):
        """bin/ is shared storage and survives rebuilds. A stale stamp would make
        run pass '-d cuda' to a binary that no longer has the option compiled in."""
        body = _function_body("cmd_install")
        assert "rm -f \"$(_osu_cuda_stamp_path" in body, (
            "a non-CUDA OSU rebuild leaves the previous CUDA stamp in place"
        )

    def test_device_tests_require_all_three_halves(self):
        """A CUDA-linked binary, a device, and a CUDA-aware MPI. The third was
        missing for the life of this branch and is the one whose absence does not
        fail: 4.1.7 accepts '-d cuda D D' and then spins forever."""
        # Comments stripped: the block explaining why all three are needed names
        # both '-d cuda' and the hang, and it sits ABOVE the guards -- matching it
        # would put the "use" before every "guard" and fail on correct code.
        body = "\n".join(
            line for line in _function_body("cmd_run").splitlines()
            if not line.lstrip().startswith("#")
        )
        guards = {
            # The binary half, and specifically the MPI-aware form of it: a tree
            # built by the default mpicc satisfies _osu_cuda_enabled and still
            # hangs, so a bare stamp-existence check is not one of the three.
            "_osu_cuda_tree_matches_mpi": "which MPI the CUDA tree was built by",
            "_host_has_gpu": "whether this node has a device",
            "_cuda_aware_mpi_root": "whether any MPI here can move device buffers",
        }
        idx_use = body.index("-d cuda")
        for name, what in guards.items():
            assert name in body, f"cmd_run no longer checks {what} before -d cuda"
            assert body.index(name) < idx_use, (
                f"the -d cuda run is not behind the {name} guard"
            )


def _fake_mpi_root(tmp_path, name, *, cuda=True, wrappers=True, launcher=True):
    """A fake MPI install root whose ompi_info answers the CUDA question.

    HPC_BENCHMARK_CUDA_MPI is what points the driver here. That variable is a
    test seam as much as an operator override: this suite runs on macOS and on
    CI, neither of which has any MPI at all, so without it every assertion about
    which MPI is chosen would pass against an implementation containing no probe.
    Same role HPC_BENCHMARK_NET_DIR plays for /sys/class/net.
    """
    assert wrappers in (True, False, "mpicc_only"), f"bad wrappers: {wrappers!r}"
    root = tmp_path / name
    (root / "bin").mkdir(parents=True, exist_ok=True)
    # lib64 only, no lib -- the Amazon packages ship lib64 and nothing else
    # (checked on the live AMI), and _mpi_lib_dirs is supposed to skip a
    # directory that does not exist rather than name it. A root with both would
    # make an implementation that emits lib unconditionally look correct.
    (root / "lib64").mkdir(parents=True, exist_ok=True)
    info = root / "bin" / "ompi_info"
    value = "true" if cuda else "false"
    info.write_text(
        "#!/bin/bash\n"
        f'echo "mca:mpi:base:param:mpi_built_with_cuda_support:value:{value}"\n'
    )
    info.chmod(0o755)
    if launcher:
        mpirun = root / "bin" / "mpirun"
        # Announces which MPI launched, so the choice is observable rather than
        # inferred: the whole bug was the d2d pair running under the wrong one.
        #
        # It also reports the LD_LIBRARY_PATH it was handed and which variables
        # -x forwarded, because choosing the right launcher is not sufficient:
        # LD_LIBRARY_PATH outranks the binaries' RUNPATH, so a d2d pair launched
        # by the CUDA-aware mpirun still loaded the other MPI's libmpi.so.40 and
        # died on an undefined symbol. A stub that only named itself could not
        # see that. Real option parsing rather than `shift 2` -- that form
        # silently ate `-x LD_LIBRARY_PATH` and exec'd the flag as the program.
        mpirun.write_text(
            '#!/bin/bash\n'
            f'echo "LAUNCHED BY {name}"\n'
            'echo "LAUNCHER LD_LIBRARY_PATH=${LD_LIBRARY_PATH:-<unset>}"\n'
            'while [[ $# -gt 0 ]]; do\n'
            '  case "$1" in\n'
            '    -n|-np) shift 2 ;;\n'
            '    -x) echo "LAUNCHER FORWARDS $2"; shift 2 ;;\n'
            '    *) break ;;\n'
            '  esac\n'
            'done\n'
            'exec "$@"\n'
        )
        mpirun.chmod(0o755)
    # "mpicc_only" is the one shape that reaches _build_osu_cuda's own wrapper
    # check: _mpi_is_cuda_aware requires bin/mpicc, so wrappers=False is rejected
    # one level earlier as "not a CUDA-aware MPI" and the build is never entered.
    names = {True: ("mpicc", "mpicxx"), "mpicc_only": ("mpicc",), False: ()}[wrappers]
    for w in names:
        c = root / "bin" / w
        c.write_text(f'#!/bin/bash\necho "COMPILED BY {name}"\nexit 0\n')
        c.chmod(0o755)
    return root


def _osu_cuda_run_harness(tmp_path, *, gpu=True, installed_cuda=False,
                          cached_src=True, src_payload=None,
                          with_make=True, toolkit=True, prebuilt_tree=False,
                          lock_held=False, build_rc=0, tests="osu", nvcc=None,
                          cuda_mpi=True, cuda_mpi_wrappers=True,
                          stamp_mpi="__match__", inherited_llp=None):
    """Run cmd_run's osu branch with the whole OSU toolchain faked.

    The point of interest is which pt2pt directory the -d cuda tests execute
    from, which MPI launches them, and whether a run-time build happens at all --
    so the OSU binaries are stubs that announce their own path, and configure/make
    are stubs that populate the tree the real ones would have.

    cuda_mpi=False models an AMI with no CUDA-aware MPI (the pre-fix osiris
    state). stamp_mpi overrides the MPI root recorded in an existing tree's
    stamp: "__match__" writes the CUDA-aware root, None writes a legacy two-field
    stamp, and any string writes that literal.
    """
    prefix = tmp_path / "bin"
    pt2pt = prefix / "osu" / "libexec" / "osu-micro-benchmarks" / "mpi" / "pt2pt"
    coll = prefix / "osu" / "libexec" / "osu-micro-benchmarks" / "mpi" / "collective"
    for d in (pt2pt, coll):
        d.mkdir(parents=True, exist_ok=True)
    for name in ("osu_latency", "osu_bw"):
        b = pt2pt / name
        b.write_text(f'#!/bin/bash\necho "RAN {name} FROM installed ARGS=$*"\n')
        b.chmod(0o755)
    for name in ("osu_allreduce", "osu_alltoall"):
        b = coll / name
        b.write_text(f'#!/bin/bash\necho "RAN {name}"\n')
        b.chmod(0o755)
    (prefix / ".build_arch").write_text(
        subprocess.run(["uname", "-m"], capture_output=True, text=True).stdout
    )

    # The CUDA-aware MPI the driver should find, and the non-CUDA-aware one that
    # stands in for the default on PATH. Both exist even when cuda_mpi=False, so
    # the no-CUDA-MPI case models an AMI whose only MPI cannot do -d cuda rather
    # than a node with no MPI.
    cuda_mpi_root = _fake_mpi_root(tmp_path, "mpi-cuda", cuda=cuda_mpi,
                                   wrappers=cuda_mpi_wrappers)
    stamp_root = str(cuda_mpi_root) if stamp_mpi == "__match__" else stamp_mpi

    def _stamp(text_root):
        if text_root is None:
            return "basic /usr/local/cuda\n"
        return f"basic /usr/local/cuda {text_root}\n"

    if installed_cuda:
        (prefix / "osu" / ".cuda_enabled").write_text(_stamp(stamp_root))

    cuda_tree = prefix / "osu-cuda"
    if prebuilt_tree:
        d = cuda_tree / "libexec" / "osu-micro-benchmarks" / "mpi" / "pt2pt"
        d.mkdir(parents=True, exist_ok=True)
        for name in ("osu_latency", "osu_bw"):
            b = d / name
            b.write_text(f'#!/bin/bash\necho "RAN {name} FROM cuda-tree ARGS=$*"\n')
            b.chmod(0o755)
        (cuda_tree / ".cuda_enabled").write_text(_stamp(stamp_root))
    if lock_held:
        (prefix / ".osu-cuda.lock").mkdir(parents=True, exist_ok=True)

    # A stand-in tarball with a no-op configure, plus the digest the driver will
    # be told to expect. The digest is always the good tarball's, so a
    # src_payload override genuinely mismatches rather than being waved through.
    version = _driver_var("OSU_VERSION")
    inner = tmp_path / f"osu-micro-benchmarks-{version}"
    inner.mkdir(parents=True, exist_ok=True)
    # Echoes its own argv: --with-cuda is what OSU substitutes NVCC from, and a
    # flag inside an unreached branch is not one any node passes.
    #
    # It also runs whatever CC= names, the way real configure does when it checks
    # the compiler. That is what makes "which mpicc compiled the CUDA tree"
    # observable in the run's own output -- passing CC= is not the same as it
    # being honored, and both wrappers here announce themselves.
    #
    # And it writes its Makefile into $PWD, not into its own directory, because
    # that is what config.status does: invoking configure by absolute path from
    # elsewhere is a VPATH build whose output lands in the CWD. A stub that wrote
    # the Makefile beside itself could not tell an in-tree build from an
    # out-of-tree one, which is exactly the bug that shipped -- `make -C srcdir`
    # then found no Makefile and died on "No rule to make target 'install'".
    (inner / "configure").write_text(
        '#!/bin/bash\n'
        'echo "CONFIGURE ARGS=$*"\n'
        'for a in "$@"; do\n'
        '  case "$a" in CC=*|CXX=*) "${a#*=}" --version || exit 1 ;; esac\n'
        'done\n'
        'echo "CONFIGURED IN $PWD" > Makefile\n'
        'exit 0\n'
    )
    (inner / "configure").chmod(0o755)
    good = tmp_path / "good.tar.gz"
    subprocess.run(
        ["tar", "-czf", str(good), "-C", str(tmp_path), inner.name], check=True
    )
    expected_sha = hashlib.sha256(good.read_bytes()).hexdigest()

    src = prefix / "src" / f"osu-micro-benchmarks-{version}.tar.gz"
    if cached_src:
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(good.read_bytes() if src_payload is None else src_payload)

    stub = tmp_path / "stub"
    stub.mkdir(parents=True, exist_ok=True)
    if with_make:
        # Stand in for `make install`: create the tree configure would have
        # targeted, with binaries that name themselves so the caller's choice of
        # tree is observable. Written before the passthrough loop, which would
        # otherwise symlink the real make here first.
        #
        # It honors -C and REQUIRES a Makefile in the directory it builds in,
        # exiting 2 with GNU make's own wording when there is none. Without that
        # this stub built the tree from anywhere, so a configure whose Makefile
        # landed in the wrong directory still "succeeded" here while failing on
        # every real node.
        m = stub / "make"
        m.write_text(
            "#!/bin/bash\n"
            f"rc={build_rc}\n"
            'while [[ $# -gt 0 ]]; do\n'
            '  case "$1" in -C) cd "$2" || exit 1; shift 2 ;; *) shift ;; esac\n'
            'done\n'
            '[[ -f Makefile ]] || {\n'
            '  echo "make: *** No rule to make target \'install\'.  Stop." >&2\n'
            '  exit 2\n'
            '}\n'
            '[[ "$rc" == 0 ]] || { echo "make: simulated failure" >&2; exit "$rc"; }\n'
            f'd={str(cuda_tree)!r}/libexec/osu-micro-benchmarks/mpi/pt2pt\n'
            'mkdir -p "$d"\n'
            'for n in osu_latency osu_bw; do\n'
            '  printf "#!/bin/bash\\necho \\"RAN %s FROM cuda-tree ARGS=\\$*\\"\\n" "$n" > "$d/$n"\n'
            '  chmod 755 "$d/$n"\n'
            'done\n'
        )
        m.chmod(0o755)
    for name in _STUB_PASSTHROUGH + ("tar", "nproc", "rmdir", "tail", "ln"):
        # with_make=False models a node with no make, so the real one must not be
        # linked in behind the fake's back.
        if name == "make" and not with_make:
            continue
        real = shutil.which(name)
        if real and not (stub / name).exists():
            (stub / name).symlink_to(real)
    if gpu:
        smi = stub / "nvidia-smi"
        smi.write_text('#!/bin/bash\necho "GPU 0: NVIDIA A10G (UUID: GPU-0)"\n')
        smi.chmod(0o755)
    # The default PATH toolchain, standing in for 4.1.7 on a ParallelCluster GPU
    # AMI. Every stub announces itself so that "the host-to-host tests still use
    # the default launcher" and "the CUDA build never touches a bare mpicc" are
    # both observable rather than argued from the source.
    launcher = stub / "mpirun"
    # Reports its LD_LIBRARY_PATH for the same reason the CUDA-aware one does,
    # and this is the half that guards the mirror-image mutation: exporting the
    # CUDA MPI's lib64 once for the whole osu branch would fix the d2d tests and
    # silently change which libmpi produced every headline number. Without this
    # line the host-to-host launches emit nothing to assert on.
    launcher.write_text(
        '#!/bin/bash\n'
        'echo "LAUNCHED BY path-default"\n'
        'echo "DEFAULT LAUNCHER LD_LIBRARY_PATH=${LD_LIBRARY_PATH:-<unset>}"\n'
        'shift 2\n'
        'exec "$@"\n'
    )
    launcher.chmod(0o755)
    for name in ("mpicc", "mpicxx"):
        c = stub / name
        c.write_text(f'#!/bin/bash\necho "COMPILED BY path-default {name}"\nexit 0\n')
        c.chmod(0o755)
    root = tmp_path / "cuda"
    if toolkit:
        (root / "include").mkdir(parents=True, exist_ok=True)
        (root / "lib64").mkdir(parents=True, exist_ok=True)
        (root / "include" / "cuda.h").write_text("/* stub */\n")
        (root / "lib64" / "libcudart.so").write_text("")

    # nvcc is what separates --enable-cuda=yes from =basic, and "cuda_home" is
    # the case that failed on osiris: present at $CUDA_HOME/bin, absent from
    # PATH, so the mode is correctly =yes and a bare `nvcc` in the makefile
    # cannot be resolved. Deliberately not in _STUB_PASSTHROUGH -- PATH is
    # replaced wholesale, so the host's own nvcc cannot answer for the stub.
    assert nvcc in (None, "path", "cuda_home"), f"bad nvcc mode: {nvcc!r}"
    if nvcc == "path":
        fake = stub / "nvcc"
        fake.write_text("#!/bin/bash\nexit 0\n")
        fake.chmod(0o755)
    elif nvcc == "cuda_home":
        (root / "bin").mkdir(parents=True, exist_ok=True)
        fake = root / "bin" / "nvcc"
        fake.write_text("#!/bin/bash\nexit 0\n")
        fake.chmod(0o755)

    results = tmp_path / "results"
    # OSU_SHA256 is overridden after sourcing so the fixture tarball is the one
    # the real _verify_cached_src compares against -- the verification logic
    # under test is the driver's, not a reimplementation.
    script = f"""
    set -euo pipefail
    BENCH_BIN={str(prefix)!r}
    HPC_BENCHMARK_LIB_ONLY=1 source {BENCHMARK!r}
    BENCH_BIN={str(prefix)!r}
    OSU_SHA256={expected_sha!r}
    cmd_run --tests {tests} --results-dir {str(results)!r}
    echo RUN_OK
    """
    env = dict(os.environ, PATH=str(stub),
               HPC_BENCHMARK_CUDA_MPI=str(cuda_mpi_root))
    env.pop("CUDA_HOME", None)
    # Always decided here, never inherited: the developer's own LD_LIBRARY_PATH
    # would otherwise leak into every assertion about what the driver prepends.
    # inherited_llp models the job script's `module load openmpi`, which exports
    # the NON-CUDA-aware MPI's lib64 -- the value that outranks the CUDA tree's
    # RUNPATH and caused the undefined-symbol failure.
    env.pop("LD_LIBRARY_PATH", None)
    if inherited_llp is not None:
        env["LD_LIBRARY_PATH"] = inherited_llp
    if toolkit:
        env["CUDA_HOME"] = str(root)
    # A dedicated CWD standing in for the job's submit directory, which on a real
    # cluster is shared storage. Without it the run inherits the repo root and no
    # test can see the build littering it -- which it did, with Makefile,
    # config.log, config.status, libtool and a whole c/ tree.
    submitdir = tmp_path / "submitdir"
    submitdir.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([_BASH, "-c", script], capture_output=True, env=env,
                       cwd=str(submitdir))
    return r, prefix, results, submitdir


def _driver_var(name):
    """Read a top-level assignment out of the driver, e.g. OSU_VERSION."""
    with open(BENCHMARK) as fh:
        for line in fh:
            m = re.match(rf'^{name}="?([^"\n]+)"?$', line.strip())
            if m:
                return m.group(1)
    raise AssertionError(f"{name} not found in {BENCHMARK}")


class TestRunBuildsCudaOsuOnTheNodeThatNeedsIt:
    """install compiles on the head node, so a CPU head node fronting a GPU queue
    produces a host-to-host OSU and the cluster's device interconnect goes
    unmeasured. STREAM already solves the general shape of this -- run compiles
    the missing per-microarchitecture binary on whatever node the job lands on --
    and the same applies here: the tarball is cached at install time, and run
    builds a CUDA tree on the GPU node instead of requiring an interactive srun.
    """

    def test_a_gpu_node_builds_and_uses_a_cuda_tree(self, tmp_path):
        r, prefix, results, _ = _osu_cuda_run_harness(tmp_path)
        assert r.returncode == 0, r.stderr.decode() + r.stdout.decode()
        out = r.stdout.decode()
        assert "Building CUDA-enabled OSU" in out, out
        assert (prefix / "osu-cuda" / ".cuda_enabled").is_file(), (
            "the completion stamp was not written, so the next run rebuilds"
        )
        assert "FROM cuda-tree ARGS=-d cuda D D" in out, (
            f"the device tests did not run from the CUDA tree: {out}"
        )
        assert (results / next(p.name for p in results.iterdir())
                / "osu" / "latency_cuda.txt").is_file()

    def test_the_build_happens_in_its_own_srcdir_not_the_submit_directory(
        self, tmp_path
    ):
        """configure was invoked by absolute path from wherever the job started,
        which autoconf treats as a VPATH build: Makefile, config.status,
        config.log, libtool and the whole c/ tree are written to the CWD, not the
        srcdir. Two consequences, and only the second was visible in the results.

        The submit directory is shared storage, so every GPU job dropped ~500 KB
        of autotools output into the operator's working directory -- observed on
        osiris, alongside the rendered job script and the .out files.

        And `make -C srcdir install` then had no Makefile to read, so every
        device test failed with `make: *** No rule to make target 'install'.`
        The run itself still exited 0 and the host-to-host numbers were fine, so
        the only signal was a NOTE on stderr saying the CUDA tree could not be
        built. Confirmed in job 8's own osu-cuda.log on osiris (2026-07-31),
        where config.log recorded the configure line as
        /tmp/hpc-benchmark-osu-cuda.XXXXXX/osu-micro-benchmarks-7.5.2/configure.

        Asserting the tree got built is not enough on its own: the stub `make`
        created it from any directory until it was taught to require a Makefile,
        which is why both halves are checked here.
        """
        r, prefix, _, submitdir = _osu_cuda_run_harness(tmp_path)
        assert r.returncode == 0, r.stderr.decode() + r.stdout.decode()
        assert (prefix / "osu-cuda" / ".cuda_enabled").is_file(), (
            "the CUDA tree was not built: " + r.stderr.decode()
        )
        litter = sorted(p.name for p in submitdir.iterdir())
        assert litter == [], (
            f"the CUDA OSU build wrote {litter} into the submit directory, which "
            f"is shared storage on a real cluster"
        )

    def test_host_to_host_still_runs_from_the_installed_tree(self, tmp_path):
        """Only the device tests move. Reusing the CUDA tree for host-to-host
        would silently change what the headline latency/bandwidth numbers mean
        between a GPU-node run and a head-node run."""
        r, _, _, _ = _osu_cuda_run_harness(tmp_path)
        out = r.stdout.decode()
        assert "RAN osu_latency FROM installed ARGS=" in out, out
        assert "RAN osu_bw FROM installed ARGS=" in out, out

    def test_a_prebuilt_cuda_tree_is_reused_not_rebuilt(self, tmp_path):
        """bin/ is shared storage, so the second GPU job must not pay the build
        cost again."""
        r, _, _, _ = _osu_cuda_run_harness(tmp_path, prebuilt_tree=True)
        out = r.stdout.decode()
        assert r.returncode == 0, r.stderr.decode() + out
        assert "Building CUDA-enabled OSU" not in out, (
            "an existing CUDA tree was rebuilt"
        )
        assert "FROM cuda-tree ARGS=-d cuda D D" in out, out

    def test_a_cuda_capable_install_is_used_directly(self, tmp_path):
        """A GPU head node already built CUDA into bin/osu. Building a second
        tree there is pure waste."""
        r, prefix, _, _ = _osu_cuda_run_harness(tmp_path, installed_cuda=True)
        out = r.stdout.decode()
        assert r.returncode == 0, r.stderr.decode() + out
        assert "Building CUDA-enabled OSU" not in out, out
        assert not (prefix / "osu-cuda").exists(), (
            "a redundant CUDA tree was built alongside a CUDA-capable install"
        )
        assert "FROM installed ARGS=-d cuda D D" in out, out

    def test_a_cpu_node_neither_builds_nor_runs_device_tests(self, tmp_path):
        r, prefix, _, _ = _osu_cuda_run_harness(tmp_path, gpu=False)
        out, err = r.stdout.decode(), r.stderr.decode()
        assert r.returncode == 0, err + out
        assert "Building CUDA-enabled OSU" not in out, (
            "a node with no device built a CUDA tree it cannot execute"
        )
        assert "-d cuda" not in out, out
        assert not (prefix / "osu-cuda").exists()

    @pytest.mark.parametrize(
        "kw,expected",
        [
            ({"cached_src": False}, b"cached source missing"),
            ({"cuda_mpi_wrappers": "mpicc_only"}, b"no mpicc/mpicxx under"),
            ({"cuda_mpi": False}, b"no CUDA-aware MPI"),
            ({"with_make": False}, b"no make on this node"),
            ({"toolkit": False}, b"no CUDA toolkit on this node"),
            ({"lock_held": True}, b"another node is building"),
            ({"build_rc": 1}, b"CUDA OSU build failed"),
        ],
    )
    def test_a_failed_optional_build_never_fails_the_run(self, tmp_path, kw, expected):
        """The host-to-host results are already written when this runs. Aborting
        the job over an optional extra would throw away real measurements -- and
        on a private-subnet node with no toolkit, that is the normal case."""
        r, _, results, _ = _osu_cuda_run_harness(tmp_path, **kw)
        out, err = r.stdout.decode(), r.stderr.decode()
        assert r.returncode == 0, f"the run aborted: {err + out}"
        assert "RUN_OK" in out, out
        assert expected in r.stderr, f"reason not reported: {err}"
        assert "RAN osu_latency FROM installed" in out, (
            "the host-to-host results were lost to a failed optional build"
        )
        assert "-d cuda" not in out, "device tests ran without a usable CUDA tree"

    def test_a_tampered_cache_is_rejected_before_it_is_extracted(self, tmp_path):
        """bin/ is shared, writable storage that outlives any one cluster, so the
        tarball reaching run is not necessarily the one install verified."""
        r, prefix, _, _ = _osu_cuda_run_harness(
            tmp_path, src_payload=b"not a tarball, and not the pinned checksum\n"
        )
        assert r.returncode == 0, r.stderr.decode()
        assert b"checksum mismatch for cached source" in r.stderr, r.stderr
        assert not (prefix / "osu-cuda" / ".cuda_enabled").exists(), (
            "a tree was stamped complete from an unverified tarball"
        )

    def test_an_empty_cache_is_reported_as_empty(self, tmp_path):
        """sha256sum of a zero-byte file is a valid digest that simply will not
        match, so without the bounds check the operator gets a checksum-mismatch
        alert -- which reads as tampering -- for a truncated copy."""
        r, _, _, _ = _osu_cuda_run_harness(tmp_path, src_payload=b"")
        assert r.returncode == 0, r.stderr.decode()
        assert b"cached source is empty" in r.stderr, r.stderr

    def test_the_harness_can_see_an_out_of_tree_build(self, tmp_path,
                                                     monkeypatch):
        """Vacuity guard for the two halves above. The stub configure writes its
        Makefile into $PWD the way config.status does, and the stub make requires
        one where it builds -- neither was true before, so the shipped bug passed.

        Driving the real driver with the exact broken invocation is the only way
        to prove the harness sees it: an assertion about the source text cannot
        tell a `cd` that happened from one that was optimized into a comment.
        """
        source = open(BENCHMARK).read()
        broken = source.replace(
            'cd "$tmpdir" \\\n        && tar -xzf "$src" \\\n'
            '        && cd "osu-micro-benchmarks-${OSU_VERSION}" \\',
            'tar -xzf "$src" -C "$tmpdir" \\',
        ).replace(
            "            ./configure \\",
            '            "$tmpdir/osu-micro-benchmarks-${OSU_VERSION}/configure" \\',
        ).replace(
            '            make -j"$(nproc)" install',
            '            make -C "$tmpdir/osu-micro-benchmarks-${OSU_VERSION}" '
            '-j"$(nproc)" install',
        )
        assert broken != source, "the out-of-tree mutation matched nothing"
        broken_path = tmp_path / "broken-hpc-benchmark.sh"
        broken_path.write_text(broken)
        broken_path.chmod(0o755)
        monkeypatch.setattr(f"{__name__}.BENCHMARK", str(broken_path))
        r, prefix, _, submitdir = _osu_cuda_run_harness(tmp_path)
        assert r.returncode == 0, r.stderr.decode()
        assert b"No rule to make target" in r.stderr, (
            "the harness did not reproduce the out-of-tree make failure: "
            + r.stderr.decode()
        )
        assert not (prefix / "osu-cuda" / ".cuda_enabled").exists(), (
            "a broken build was stamped complete"
        )
        assert sorted(p.name for p in submitdir.iterdir()) == ["Makefile"], (
            "the harness cannot see configure littering the submit directory"
        )

    def test_install_caches_the_osu_tarball(self, tmp_path):
        """Without the cache there is nothing for run to build from on a node
        with no route to the download host."""
        prefix = tmp_path / "bin"
        version = _driver_var("OSU_VERSION")
        stub = tmp_path / "stub"
        stub.mkdir(parents=True, exist_ok=True)
        for name in _STUB_PASSTHROUGH + ("tar", "nproc", "gcc", "tail", "true"):
            real = shutil.which(name)
            if real and not (stub / name).exists():
                (stub / name).symlink_to(real)
        for name in ("mpirun", "mpicc", "mpicxx"):
            c = stub / name
            c.write_text("#!/bin/bash\nexit 0\n")
            c.chmod(0o755)
        # A tarball whose configure and make both no-op: install's own caching is
        # what is under test, not OSU's build.
        inner = tmp_path / f"osu-micro-benchmarks-{version}"
        inner.mkdir(parents=True, exist_ok=True)
        (inner / "configure").write_text("#!/bin/bash\nexit 0\n")
        (inner / "configure").chmod(0o755)
        (inner / "Makefile").write_text("install:\n\t@true\n")
        tarball = tmp_path / "osu.tar.gz"
        subprocess.run(
            ["tar", "-czf", str(tarball), "-C", str(tmp_path), inner.name],
            check=True,
        )
        script = f"""
        set -euo pipefail
        HPC_BENCHMARK_LIB_ONLY=1 source {BENCHMARK!r}
        _fetch() {{ cp {str(tarball)!r} "$1"; }}
        cmd_install --prefix {str(prefix)!r} --tools osu
        """
        r = subprocess.run([_BASH, "-c", script], capture_output=True,
                           env=dict(os.environ, PATH=str(stub)))
        assert r.returncode == 0, r.stderr.decode() + r.stdout.decode()
        cached = prefix / "src" / f"osu-micro-benchmarks-{version}.tar.gz"
        assert cached.is_file(), (
            "install no longer caches the OSU tarball; run cannot build a CUDA "
            "tree on a private-subnet compute node"
        )
        assert cached.read_bytes() == tarball.read_bytes()

    def test_the_stamp_is_written_after_the_build_not_before(self):
        """The stamp is the completion marker. Written first, a killed or failed
        build leaves a half-populated tree that the next run selects and executes
        binaries out of."""
        body = _function_body("_build_osu_cuda")
        idx_make = body.index("CUDA OSU build failed")
        idx_stamp = body.index("_osu_cuda_stamp_path")
        assert idx_make < idx_stamp, (
            "the CUDA tree is stamped complete before make install has run"
        )

    def test_the_build_is_serialized_across_nodes(self):
        """bin/ is shared storage. Two GPU nodes running make install into one
        prefix interleave into a corrupt tree."""
        body = _function_body("_build_osu_cuda")
        assert "mkdir \"$lock\"" in body, (
            "the run-time build is not serialized; mkdir is the atomic primitive"
        )
        idx_lock = body.index('mkdir "$lock"')
        idx_make = body.index("CUDA OSU build failed")
        assert idx_lock < idx_make, "the build is not inside the lock"
        assert "rmdir \"$lock\"" in body, "the lock is never released"

    def test_run_actually_calls_the_runtime_builder(self):
        """Every unit test above passes with the call site deleted, while a CPU-
        head-node cluster silently reports no device numbers."""
        body = _function_body("cmd_run")
        assert "_build_osu_cuda" in body, (
            "cmd_run no longer builds a CUDA OSU tree, so a CPU head node's "
            "cluster is back to needing an interactive srun and a manual rebuild"
        )
        assert "_osu_cuda_tree" in body, (
            "cmd_run does not consult the run-time CUDA tree"
        )


class TestDeviceTestsRunUnderAnMpiThatCanDoThem:
    """A ParallelCluster GPU AMI ships two Open MPIs and the one on the default
    PATH is not the CUDA-aware one. On the AL2023 x86_64 image, measured:
    /opt/amazon/openmpi is 4.1.7 with mpi_built_with_cuda_support:false;
    /opt/amazon/openmpi5 is 5.0.9amzn1 with it true. Handing '-d cuda D D' to
    4.1.7 does not fail -- it HANGS, at the first message size, both ranks at
    99.9% CPU and 0% GPU, until the allocation's time limit. On a queue with
    TimeLimit=UNLIMITED that is forever, which is why the wrong MPI is worse than
    no MPI: nothing reports it and no result is ever written.

    The probe is deliberately free of any distro or path decision -- ompi_info's
    own answer is the acceptance test -- so nothing here is specific to AL2023.
    Only AL2023 x86_64 is hardware-verified; the other seven base_os values rely
    on that OS-agnosticism, whose failure mode is a skip with a named reason.
    """

    def _probe(self, tmp_path, *, roots=(), override=None, on_path=None,
               launcher=True):
        """Ask _cuda_aware_mpi_root what it finds, with PATH replaced wholesale.

        `roots` are extra fake MPI trees to create (name, cuda) pairs; `on_path`
        names the one whose bin/ goes on PATH, standing in for the node's default
        MPI. No real MPI exists on a developer's machine or on CI, so without
        these seams every assertion below would pass against an empty function.
        """
        stub = tmp_path / "stub"
        stub.mkdir(parents=True, exist_ok=True)
        for name in _STUB_PASSTHROUGH:
            real = shutil.which(name)
            if real and not (stub / name).exists():
                (stub / name).symlink_to(real)
        made = {}
        for name, cuda in roots:
            made[name] = _fake_mpi_root(tmp_path, name, cuda=cuda,
                                        launcher=launcher)
        path = str(stub)
        if on_path is not None:
            path = f"{made[on_path]}/bin:{path}"
        script = f"""
        set -uo pipefail
        HPC_BENCHMARK_LIB_ONLY=1 source {BENCHMARK!r}
        echo "ROOT=[$(_cuda_aware_mpi_root)]"
        """
        env = dict(os.environ, PATH=path)
        env.pop("HPC_BENCHMARK_CUDA_MPI", None)
        if override is not None:
            env["HPC_BENCHMARK_CUDA_MPI"] = str(
                made.get(override, tmp_path / str(override))
            )
        r = subprocess.run([_BASH, "-c", script], capture_output=True, text=True,
                           env=env)
        assert r.returncode == 0, r.stderr + r.stdout
        m = re.search(r"^ROOT=\[(.*)\]$", r.stdout, re.M)
        assert m, r.stdout
        return m.group(1), made

    def test_a_cuda_aware_default_mpi_is_used_with_no_path_guessing(self, tmp_path):
        """This is what makes the fix OS-agnostic rather than an AL2023 special
        case: on an image whose default mpirun is already CUDA-aware, the probe
        accepts it and never consults a glob. Every base_os with a working
        default is therefore covered by the same code path."""
        root, made = self._probe(
            tmp_path, roots=[("mpi-default", True)], on_path="mpi-default"
        )
        assert root == str(made["mpi-default"]), (
            f"a CUDA-aware default MPI was not accepted: {root!r}"
        )

    def test_a_non_cuda_default_mpi_is_rejected(self, tmp_path):
        """The osiris case. 4.1.7 is on PATH, answers false, and must not be
        handed the device tests -- there is no CUDA-aware MPI in this fixture's
        glob range, so the correct answer is nothing at all."""
        root, _ = self._probe(
            tmp_path, roots=[("mpi-default", False)], on_path="mpi-default"
        )
        assert root == "", (
            f"a non-CUDA-aware MPI was accepted for the device tests: {root!r}"
        )

    def test_the_operator_override_is_honored(self, tmp_path):
        root, made = self._probe(
            tmp_path,
            roots=[("mpi-default", False), ("mpi5", True)],
            on_path="mpi-default",
            override="mpi5",
        )
        assert root == str(made["mpi5"]), (
            f"HPC_BENCHMARK_CUDA_MPI did not select the MPI it names: {root!r}"
        )

    def test_an_override_that_cannot_do_cuda_is_still_rejected(self, tmp_path):
        """The override says where to look, not what the answer is. Trusting it
        blindly reintroduces the hang for anyone who points it at 4.1.7."""
        root, _ = self._probe(
            tmp_path, roots=[("mpi-default", False)], override="mpi-default"
        )
        assert root == "", (
            f"the override bypassed the CUDA-support check: {root!r}"
        )

    def test_a_root_with_no_launcher_or_no_compiler_is_rejected(self, tmp_path):
        """ompi_info answering true is not enough: the root has to carry the
        mpirun that will launch the tests and the mpicc that will build them.
        A root selected without them yields "$root/bin/mpirun" as the launcher,
        and a command-not-found inside the run's tee pipeline is a non-zero
        status that pipefail propagates -- so the job dies instead of skipping an
        optional test. Both halves, because the two are separate reasons: an
        ompi_info-only tree (a devel or docs package) has neither, but a runtime
        package can ship the launcher with no wrappers at all."""
        for kw in ({"launcher": False}, {"wrappers": False}):
            root = _fake_mpi_root(tmp_path, f"mpi-{list(kw)[0]}", cuda=True, **kw)
            got, _ = self._probe(tmp_path, override=str(root))
            assert got == "", (
                f"a root missing {list(kw)[0]} was accepted as usable: {got!r}"
            )

    def test_an_override_naming_nothing_is_rejected_rather_than_echoed(self, tmp_path):
        """A typo'd path must skip the device tests, not become a launcher prefix
        -- $root/bin/mpirun on a nonexistent root is a command-not-found inside a
        pipeline whose exit status pipefail then propagates."""
        root, _ = self._probe(tmp_path, override="no-such-mpi")
        assert root == "", root

    def test_the_probe_never_decides_on_a_version_or_a_path(self):
        """ompi_info's answer is the acceptance test. A version comparison or a
        '5' in the directory name would be right on today's AL2023 image and
        wrong on every other base_os -- and wrong again the next time upstream
        renumbers."""
        body = _function_body("_mpi_is_cuda_aware")
        assert "mpi_built_with_cuda_support:value:true" in body, (
            "the CUDA question is no longer asked of ompi_info itself"
        )
        code = "\n".join(
            line for line in body.splitlines() if not line.lstrip().startswith("#")
        )
        for token in ("openmpi5", "5.0", "amzn", "alinux", "ubuntu", "rhel"):
            assert token not in code, (
                f"_mpi_is_cuda_aware decides on {token!r}, which is a property of "
                "one image rather than of the MPI's CUDA support"
            )

    def test_the_globs_are_not_specific_to_one_distro(self):
        """The globs are hints for where to LOOK. If they only covered
        /opt/amazon, a Debian- or RHEL-packaged CUDA-aware MPI would be invisible
        on the seven base_os values that are not hardware-verified."""
        # Read back out of a sourced driver rather than parsed: _driver_var's
        # regex only strips double quotes, and this list is single-quoted so the
        # first element would carry a leading quote and match no prefix test.
        r = subprocess.run(
            [_BASH, "-c",
             f'HPC_BENCHMARK_LIB_ONLY=1 source {BENCHMARK!r}; '
             'echo "$_CUDA_MPI_GLOBS"'],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, r.stderr
        globs = r.stdout.split()
        assert globs, "the driver defines no CUDA MPI search globs at all"
        assert any(g.startswith("/opt/amazon/") for g in globs), (
            "ParallelCluster's own layout is not searched"
        )
        assert any("lib64" in g for g in globs), "no RHEL-family layout is searched"
        assert any(g.startswith("/usr/lib/") for g in globs), (
            "no Debian-family multiarch layout is searched"
        )

    def test_the_device_tests_are_launched_by_the_cuda_aware_mpi(self, tmp_path):
        """The launcher, not just the compiler. The binaries are linked against
        the CUDA-aware MPI's libmpi and both ship SONAME libmpi.so.40, so
        launching them with the default mpirun is not even a clean failure."""
        r, _, _, _ = _osu_cuda_run_harness(tmp_path)
        out = r.stdout.decode()
        assert r.returncode == 0, r.stderr.decode() + out
        d2d = [
            line for line in out.splitlines()
            if "ARGS=-d cuda" in line or "LAUNCHED BY" in line
        ]
        assert any("LAUNCHED BY mpi-cuda" in line for line in d2d), (
            f"the device tests were not launched by the CUDA-aware MPI: {d2d}"
        )

    def test_the_host_to_host_tests_stay_on_the_default_launcher(self, tmp_path):
        """Moving them too would silently change what every headline latency and
        bandwidth number means, and break comparability with every past run."""
        r, _, _, _ = _osu_cuda_run_harness(tmp_path)
        out = r.stdout.decode()
        assert "LAUNCHED BY path-default" in out, (
            f"the host-to-host tests no longer use the default MPI: {out}"
        )

    def test_the_cuda_tree_is_compiled_by_the_cuda_aware_wrappers(self, tmp_path):
        """A tree built by the default mpicc hangs under -d cuda no matter what
        launches it, so the compiler half is not optional. Read out of the build
        log because _try_build_step sends configure's output there -- which is
        also where an operator would look."""
        r, prefix, _, _ = _osu_cuda_run_harness(tmp_path)
        out = r.stdout.decode()
        assert r.returncode == 0, r.stderr.decode() + out
        log = (prefix / "build_logs" / "osu-cuda.log").read_text()
        assert "COMPILED BY mpi-cuda" in log, (
            f"the CUDA tree was not built by the CUDA-aware MPI's wrappers: {log}"
        )
        assert "COMPILED BY path-default" not in log, (
            f"the CUDA tree was built by a bare mpicc from PATH: {log}"
        )

    def test_the_build_passes_the_wrappers_by_absolute_path(self):
        body = _function_body("_build_osu_cuda")
        code = "\n".join(
            line for line in body.splitlines() if not line.lstrip().startswith("#")
        )
        assert 'mpicc="$mpi_root/bin/mpicc"' in code, (
            "the CUDA build no longer resolves mpicc under the chosen MPI root"
        )
        assert 'CC="$mpicc"' in code and 'CXX="$mpicxx"' in code, (
            "configure is not told which compiler wrappers to use, so it finds "
            "whichever mpicc is on PATH -- the non-CUDA-aware one"
        )

    def test_ld_library_path_is_never_set_for_longer_than_one_command(self):
        """Both MPIs ship SONAME libmpi.so.40 (libmpi.so.40.30.7 vs .40.40.7), so
        a value that outlives one command redirects every 4.1.7-linked binary
        here too, including the host-to-host benchmarks whose numbers would
        silently change with nothing to indicate it.

        The CUDA tree does need the variable -- LD_LIBRARY_PATH outranks the
        RUNPATH its binaries carry and the job script's own `module load openmpi`
        exports the other MPI's lib64, so without it osu_latency dies on
        `undefined symbol: ompi_mpi_instance_null` (osiris job 10, exit 127).
        What must not come back is the shell-wide form: an `export`, or a bare
        assignment on a line that is not a per-command prefix.  Either one
        poisons every later launch in the same shell, which is what
        test_the_host_to_host_launch_is_left_alone observes at runtime."""
        with open(BENCHMARK) as fh:
            lines = fh.read().splitlines()
        code = [
            (n, l) for n, l in enumerate(lines, 1)
            if not l.lstrip().startswith("#")
        ]
        exports = [
            (n, l.strip()) for n, l in code
            if re.search(r"\bexport\s+LD_LIBRARY_PATH", l)
        ]
        assert not exports, (
            f"LD_LIBRARY_PATH is exported: {exports}. That outlives the command "
            "and silently relinks the host-to-host benchmarks too"
        )
        # A command prefix is only a prefix if a command follows it, and here
        # that command is the CUDA launcher on the continued line.
        for n, line in code:
            if not re.match(r"\s*LD_LIBRARY_PATH=", line):
                continue
            assert line.rstrip().endswith("\\"), (
                f"{BENCHMARK}:{n} assigns LD_LIBRARY_PATH as a statement rather "
                f"than as a prefix to one command: {line.strip()!r}"
            )
            assert "$cuda_mpi_launcher" in lines[n], (
                f"{BENCHMARK}:{n} sets LD_LIBRARY_PATH for something other than "
                f"the CUDA-aware launcher: next line is {lines[n].strip()!r}"
            )

    def test_a_tree_built_against_another_mpi_is_rebuilt_not_reused(self, tmp_path):
        """The stamp records which MPI built the tree. Reusing a tree whose MPI
        is not the one that will launch it is the hang again, one cache hit
        later -- and bin/ is shared storage that outlives the cluster, so this is
        the normal state after any AMI or module change."""
        r, prefix, _, _ = _osu_cuda_run_harness(
            tmp_path, prebuilt_tree=True, stamp_mpi="/opt/amazon/openmpi"
        )
        out = r.stdout.decode()
        assert r.returncode == 0, r.stderr.decode() + out
        assert "Building CUDA-enabled OSU" in out, (
            f"a tree built by a different MPI was reused as-is: {out}"
        )

    def test_a_legacy_two_field_stamp_forces_a_rebuild(self, tmp_path):
        """Every tree built before the MPI root was recorded has a two-field
        stamp, and bin/ is shared storage that outlives clusters -- so this is not
        a hypothetical. An unknown MPI must degrade to a rebuild; treating it as
        a match is the hang, and treating it as a skip loses the device numbers
        on every pre-existing install."""
        r, _, _, _ = _osu_cuda_run_harness(
            tmp_path, prebuilt_tree=True, stamp_mpi=None
        )
        out = r.stdout.decode()
        assert r.returncode == 0, r.stderr.decode() + out
        assert "Building CUDA-enabled OSU" in out, (
            f"a legacy stamp with no MPI root was treated as a match: {out}"
        )
        assert "FROM cuda-tree ARGS=-d cuda D D" in out, (
            f"a legacy stamp skipped the device tests instead of rebuilding: {out}"
        )

    def test_an_installed_tree_built_against_another_mpi_is_not_used_directly(
        self, tmp_path
    ):
        """The GPU-head-node shortcut has to be MPI-aware too: bin/osu is built
        with the DEFAULT mpicc because it also serves the host-to-host tests, so
        on a ParallelCluster GPU AMI its stamp names 4.1.7 and the shortcut must
        decline."""
        r, prefix, _, _ = _osu_cuda_run_harness(
            tmp_path, installed_cuda=True, stamp_mpi="/opt/amazon/openmpi"
        )
        out = r.stdout.decode()
        assert r.returncode == 0, r.stderr.decode() + out
        assert "FROM installed ARGS=-d cuda" not in out, (
            f"the device tests ran from a tree built by another MPI: {out}"
        )
        assert "FROM cuda-tree ARGS=-d cuda D D" in out, out

    def test_a_hang_is_never_launched_and_the_reason_is_named(self, tmp_path):
        """No CUDA-aware MPI anywhere. Skipping is correct; launching is a job
        that never ends. The note has to name the check and the override, because
        an operator on a base_os this was not verified on has nothing else to go
        on."""
        r, _, _, _ = _osu_cuda_run_harness(tmp_path, cuda_mpi=False)
        out, err = r.stdout.decode(), r.stderr.decode()
        assert r.returncode == 0, err + out
        assert "-d cuda" not in out, (
            f"the device tests were launched with no CUDA-aware MPI: {out}"
        )
        assert "no CUDA-aware MPI" in err, err
        for hint in ("mpi_built_with_cuda_support", "HPC_BENCHMARK_CUDA_MPI",
                     "openmpi5"):
            assert hint in err, (
                f"the skip note does not name {hint!r}, so an operator cannot act "
                f"on it: {err}"
            )
        assert "RAN osu_latency FROM installed" in out, (
            "the host-to-host results were lost to a missing CUDA-aware MPI"
        )

    def test_the_harness_can_see_a_driver_that_uses_the_wrong_mpi(self, tmp_path):
        """Vacuity guard. Every assertion above is about which of two MPIs ran
        something, so the fixture has to be able to observe the wrong one being
        used -- otherwise a driver with no probe at all would pass them."""
        r, prefix, _, _ = _osu_cuda_run_harness(tmp_path)
        out = r.stdout.decode()
        assert "LAUNCHED BY path-default" in out and "LAUNCHED BY mpi-cuda" in out, (
            "the fixture cannot distinguish the two MPIs, so no test above proves "
            f"anything about which one was chosen: {out}"
        )
        log = (prefix / "build_logs" / "osu-cuda.log").read_text()
        assert "COMPILED BY" in log, (
            "configure never runs what CC= names, so passing the wrong wrapper "
            f"would be invisible: {log}"
        )

    def _install(self, tmp_path, *, default_cuda):
        """Run cmd_install's osu branch on a GPU node whose default MPI is or is
        not CUDA-aware, and return its output.

        bin/osu is built with the DEFAULT mpicc by design (it serves the
        host-to-host tests, whose numbers must stay comparable), so on a
        ParallelCluster GPU AMI install silently produces a tree that can never
        do -d cuda. The only place that is knowable at install time is here.
        """
        prefix = tmp_path / "bin"
        version = _driver_var("OSU_VERSION")
        mpi = _fake_mpi_root(tmp_path, "mpi-default", cuda=default_cuda)
        stub = tmp_path / "stub"
        stub.mkdir(parents=True, exist_ok=True)
        for name in _STUB_PASSTHROUGH + ("tar", "nproc", "gcc", "tail", "true"):
            real = shutil.which(name)
            if real and not (stub / name).exists():
                (stub / name).symlink_to(real)
        inner = tmp_path / f"osu-micro-benchmarks-{version}"
        inner.mkdir(parents=True, exist_ok=True)
        (inner / "configure").write_text("#!/bin/bash\nexit 0\n")
        (inner / "configure").chmod(0o755)
        # The prefix has to appear, because the stamp is written into it.
        (inner / "Makefile").write_text(
            f"install:\n\t@mkdir -p {str(prefix / 'osu')}\n"
        )
        tarball = tmp_path / "osu.tar.gz"
        subprocess.run(
            ["tar", "-czf", str(tarball), "-C", str(tmp_path), inner.name], check=True
        )
        smi = stub / "nvidia-smi"
        smi.write_text('#!/bin/bash\necho "GPU 0: NVIDIA A10G (UUID: GPU-0)"\n')
        smi.chmod(0o755)
        cuda = tmp_path / "cuda"
        (cuda / "include").mkdir(parents=True, exist_ok=True)
        (cuda / "lib64").mkdir(parents=True, exist_ok=True)
        (cuda / "include" / "cuda.h").write_text("")
        (cuda / "lib64" / "libcudart.so").write_text("")
        script = f"""
        set -euo pipefail
        HPC_BENCHMARK_LIB_ONLY=1 source {BENCHMARK!r}
        _fetch() {{ cp {str(tarball)!r} "$1"; }}
        cmd_install --prefix {str(prefix)!r} --tools osu
        """
        env = dict(os.environ, PATH=f"{mpi}/bin:{stub}", CUDA_HOME=str(cuda))
        env.pop("HPC_BENCHMARK_CUDA_MPI", None)
        r = subprocess.run([_BASH, "-c", script], capture_output=True, text=True,
                           env=env)
        assert r.returncode == 0, r.stderr + r.stdout
        return r, mpi, prefix

    def test_install_says_whether_the_tree_it_built_can_do_device_tests(self, tmp_path):
        """Only alinux2023 x86_64 is hardware-verified. On the other seven base_os
        values the whole design rests on ompi_info's answer, and the one thing an
        operator debugging such a build needs is which MPI install picked -- said
        at install time, not inferred from a device test that silently did not
        run. Both arms, because the reassuring case is the one that proves the
        message is not unconditional."""
        r, mpi, _ = self._install(tmp_path, default_cuda=False)
        out = r.stdout + r.stderr
        assert str(mpi) in out, (
            f"install never names the MPI it linked OSU against: {out}"
        )
        assert "NOT CUDA-aware" in out, (
            "install does not say that the tree it just built cannot do -d cuda, "
            f"which is the whole reason run builds a second one: {out}"
        )
        assert "HPC_BENCHMARK_CUDA_MPI" in out, (
            f"the operator is not told how to name a CUDA-aware MPI: {out}"
        )

        r, mpi, _ = self._install(tmp_path / "ok", default_cuda=True)
        out = r.stdout + r.stderr
        assert str(mpi) in out and "NOT CUDA-aware" not in out, (
            "install reports a CUDA-aware default MPI as unusable, so the message "
            f"says nothing about the node it ran on: {out}"
        )
        assert "is CUDA-aware" in out, (
            f"install does not confirm the tree can do the device tests: {out}"
        )


def _net_dir(tmp_path, ifaces):
    """Build a fake /sys/class/net.  Real entries there are symlinks into
    /sys/devices, so directories are the closest faithful stand-in."""
    netdir = tmp_path / "net"
    netdir.mkdir()
    for name in ifaces:
        (netdir / name).mkdir()
    return netdir


def _run_isolation(tmp_path, ifaces, preset=None, netdir=None):
    """Execute _isolate_mpi_interfaces against a fake sysfs and read back what
    it exported.

    Source-level assertions cannot see this: the whole mechanism is which
    interface names come out of the probe and whether an operator's own value
    survives, both of which are runtime.  `preset` is a dict of environment
    variables to define before the call -- an empty string is a legitimate
    value the operator may have chosen, so this must be able to preset one.
    """
    if netdir is None:
        netdir = str(_net_dir(tmp_path, ifaces))
    script = f"""
    set -euo pipefail
    HPC_BENCHMARK_LIB_ONLY=1 source {BENCHMARK!r}
    HPC_BENCHMARK_NET_DIR={netdir!r}
    _isolate_mpi_interfaces
    echo "BTL=[${{OMPI_MCA_btl_tcp_if_exclude-<unset>}}]"
    echo "OOB=[${{OMPI_MCA_oob_tcp_if_exclude-<unset>}}]"
    """
    env = dict(os.environ)
    for name in ("OMPI_MCA_btl_tcp_if_exclude", "OMPI_MCA_oob_tcp_if_exclude"):
        env.pop(name, None)
    if preset:
        env.update(preset)
    r = subprocess.run([_BASH, "-c", script], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr + r.stdout
    values = {
        line[: line.index("=")]: line[line.index("[") + 1 : -1]
        for line in r.stdout.splitlines()
        if line.startswith(("BTL=[", "OOB=["))
    }
    assert set(values) == {"BTL", "OOB"}, r.stdout
    return values, r


class TestTheCudaTreeLoadsItsOwnLibmpi:
    """Choosing the CUDA-aware launcher is not sufficient, and this is measured.

    Both MPIs on a ParallelCluster GPU AMI ship SONAME libmpi.so.40 --
    /opt/amazon/openmpi is .40.30.7, /opt/amazon/openmpi5 is .40.40.7 -- and the
    CUDA tree's binaries carry RUNPATH to openmpi5/lib64. But LD_LIBRARY_PATH
    outranks RUNPATH in the loader's search order, and the generated job script's
    own `module load openmpi` exports LD_LIBRARY_PATH=/opt/amazon/openmpi/lib64.
    So on osiris job 10 the d2d pair was launched by openmpi5's mpirun, loaded
    4.1.7's libmpi anyway, and died at exit 127 with `symbol lookup error:
    undefined symbol: ompi_mpi_instance_null` -- an Open MPI 5.x symbol 4.1.7
    does not define (`nm -D --defined-only` on the live node: 1 in openmpi5's
    libmpi, 0 in openmpi's).

    Session 40 fixed the launcher and session 41 fixed the build; this is the
    third independent defect on the same path, and the reason it hid behind the
    other two is that a tree has to build successfully before anything can fail
    to load it.

    -x matters as much as the value: prterun ships its own environment to the
    ranks, so an LD_LIBRARY_PATH set only in the launcher's process never reaches
    the process that actually dies.
    """

    _OTHER_MPI = "/opt/amazon/openmpi/lib64"

    def _launch_lines(self, out):
        return [ln for ln in out.splitlines()
                if ln.startswith(("LAUNCHER LD_LIBRARY_PATH=", "LAUNCHER FORWARDS",
                                  "LAUNCHED BY", "RAN "))]

    def test_the_cuda_mpi_lib_dir_comes_first(self, tmp_path):
        r, prefix, _, _ = _osu_cuda_run_harness(
            tmp_path, inherited_llp=self._OTHER_MPI
        )
        assert r.returncode == 0, r.stderr.decode() + r.stdout.decode()
        out = r.stdout.decode()
        seen = [ln.split("=", 1)[1] for ln in out.splitlines()
                if ln.startswith("LAUNCHER LD_LIBRARY_PATH=")]
        assert seen, f"the launcher never reported its LD_LIBRARY_PATH: {out}"
        cuda_lib = str(tmp_path / "mpi-cuda" / "lib64")
        for value in seen:
            assert value.split(":")[0] == cuda_lib, (
                "the CUDA-aware MPI's lib64 is not first in LD_LIBRARY_PATH, so "
                f"the loader takes the other MPI's libmpi.so.40 first: {value!r}"
            )

    def test_the_inherited_value_is_kept_not_discarded(self, tmp_path):
        """Prepending, not replacing. The inherited value may carry CUDA or other
        libraries the binary also needs, so dropping it to fix the MPI trades one
        missing symbol for another."""
        r, _, _, _ = _osu_cuda_run_harness(
            tmp_path, inherited_llp=self._OTHER_MPI
        )
        assert r.returncode == 0, r.stderr.decode()
        out = r.stdout.decode()
        seen = [ln.split("=", 1)[1] for ln in out.splitlines()
                if ln.startswith("LAUNCHER LD_LIBRARY_PATH=")]
        assert seen, out
        for value in seen:
            assert self._OTHER_MPI in value.split(":"), (
                "the inherited LD_LIBRARY_PATH was discarded rather than "
                f"prepended to: {value!r}"
            )

    def test_the_value_is_forwarded_to_the_ranks(self, tmp_path):
        """prterun ships its own environment. Without -x the launcher's own
        LD_LIBRARY_PATH never reaches the rank that dies, so the fix is inert --
        and inert in a way no assertion on the value alone can see."""
        r, _, _, _ = _osu_cuda_run_harness(
            tmp_path, inherited_llp=self._OTHER_MPI
        )
        assert r.returncode == 0, r.stderr.decode()
        out = r.stdout.decode()
        assert "LAUNCHER FORWARDS LD_LIBRARY_PATH" in out, (
            "mpirun was not asked to forward LD_LIBRARY_PATH to the ranks, so "
            f"the ranks inherit prterun's own environment instead: {out}"
        )
        assert out.count("LAUNCHER FORWARDS LD_LIBRARY_PATH") == 2, (
            "both device tests must forward it, not just one: "
            f"{self._launch_lines(out)}"
        )

    def test_an_unset_inherited_value_produces_no_empty_entry(self, tmp_path):
        """A trailing colon in LD_LIBRARY_PATH means "the current directory" to
        the loader, which on shared storage is an arbitrary tree of other
        people's files. `${LD_LIBRARY_PATH:+:...}` is what avoids it."""
        r, _, _, _ = _osu_cuda_run_harness(tmp_path, inherited_llp=None)
        assert r.returncode == 0, r.stderr.decode()
        out = r.stdout.decode()
        seen = [ln.split("=", 1)[1] for ln in out.splitlines()
                if ln.startswith("LAUNCHER LD_LIBRARY_PATH=")]
        assert seen, out
        for value in seen:
            assert "" not in value.split(":"), (
                f"LD_LIBRARY_PATH has an empty entry: {value!r}"
            )
            assert not value.endswith(":"), value

    def test_only_directories_that_exist_are_named(self, tmp_path):
        """The fake MPI root has lib64 and no lib, like the Amazon packages. An
        implementation that emits both unconditionally names a directory that
        does not exist -- harmless to the loader, but it makes the value a lie
        about what was found, and it is the same shape as naming the wrong one."""
        r, _, _, _ = _osu_cuda_run_harness(tmp_path, inherited_llp=None)
        assert r.returncode == 0, r.stderr.decode()
        out = r.stdout.decode()
        seen = [ln.split("=", 1)[1] for ln in out.splitlines()
                if ln.startswith("LAUNCHER LD_LIBRARY_PATH=")]
        assert seen, out
        root = tmp_path / "mpi-cuda"
        for value in seen:
            assert str(root / "lib") not in value.split(":"), (
                f"{root / 'lib'} does not exist but is in LD_LIBRARY_PATH: {value!r}"
            )

    def test_the_host_to_host_launch_is_left_alone(self, tmp_path):
        """The host-to-host tree is linked against the DEFAULT MPI and is what
        every headline number comes from. Prepending the CUDA MPI's lib64 to its
        launch would silently change which libmpi those numbers were produced
        under -- the mirror image of the bug, and the reason this is scoped to the
        two d2d lines rather than exported once for the whole osu branch."""
        r, _, _, _ = _osu_cuda_run_harness(
            tmp_path, inherited_llp=self._OTHER_MPI
        )
        assert r.returncode == 0, r.stderr.decode()
        out = r.stdout.decode()
        cuda_lib = str(tmp_path / "mpi-cuda" / "lib64")
        # The DEFAULT launcher's own reports, which is what makes this scoped to
        # the host-to-host launches rather than to a position in the output: the
        # CUDA-aware stub prints its report before exec'ing, so a positional split
        # on the first "FROM cuda-tree" line attributes that launch's own value to
        # the host-to-host side and the assertion can never hold.
        seen = [ln.split("=", 1)[1] for ln in out.splitlines()
                if ln.startswith("DEFAULT LAUNCHER LD_LIBRARY_PATH=")]
        assert seen, (
            f"the default launcher never reported its LD_LIBRARY_PATH: {out}"
        )
        for value in seen:
            assert value.split(":")[0] != cuda_lib, (
                "a host-to-host launch got the CUDA MPI's lib64 first, so "
                f"those numbers came from a different libmpi: {value!r}"
            )

    def test_the_harness_can_see_the_undefined_symbol_failure(self, tmp_path,
                                                              monkeypatch):
        """Vacuity guard. Every assertion above is on a string the stub prints, so
        a stub that printed them regardless of what the driver did would keep them
        all green. This drives the SHIPPED form -- the launcher chosen correctly,
        LD_LIBRARY_PATH untouched -- against a fake libmpi resolver that fails the
        way the live node did, and requires the harness to see it."""
        broken = tmp_path / "broken-driver.sh"
        source = open(BENCHMARK).read()
        shipped = (
            '                $cuda_mpi_launcher -n 2 "$osu_cuda_pt2pt/osu_latency"'
            ' -d cuda D D \\\n'
        )
        fixed_start = source.index('                local cuda_llp')
        fixed_end = source.index(
            '                _info "Running OSU bandwidth, device-to-device'
        )
        block = source[fixed_start:fixed_end]
        assert "-x LD_LIBRARY_PATH" in block, (
            "the fixed latency launch no longer looks the way this guard expects; "
            "re-derive the replacement rather than loosening the assertion"
        )
        reverted = (
            '                _info "Running OSU latency, device-to-device '
            '(2 ranks, -d cuda, MPI $cuda_mpi_root)..."\n'
            + shipped
            + '                    | tee "$results_dir/$ts/osu/latency_cuda.txt"\n\n'
        )
        broken.write_text(source[:fixed_start] + reverted + source[fixed_end:])
        broken.chmod(0o755)
        monkeypatch.setattr(f"{__name__}.BENCHMARK", str(broken))
        r, _, _, _ = _osu_cuda_run_harness(
            tmp_path, inherited_llp=self._OTHER_MPI
        )
        out = r.stdout.decode()
        reports = [ln.split("=", 1)[1] for ln in out.splitlines()
                   if ln.startswith("LAUNCHER LD_LIBRARY_PATH=")]
        cuda_lib = str(tmp_path / "mpi-cuda" / "lib64")
        assert reports, out
        assert any(v.split(":")[0] != cuda_lib for v in reports), (
            "the reverted driver still put the CUDA MPI's lib64 first, so the "
            "harness cannot tell the shipped form from the fixed one"
        )
        assert "LAUNCHER FORWARDS LD_LIBRARY_PATH" not in out, (
            "the reverted driver still forwards LD_LIBRARY_PATH, so "
            "test_the_value_is_forwarded_to_the_ranks passes vacuously"
        )


class TestTheKernelBuildCanFindNvcc:
    """--enable-cuda=yes compiles util/kernel.cu with $(NVCC), and OSU resolves
    that from --with-cuda: configure.ac's AC_ARG_WITH([cuda]) sets
    NVCC="$with_cuda/bin/nvcc" and AC_SUBST([NVCC]) renders it into every
    Makefile as `NVCC = @NVCC@`. Confirmed on the osiris head node -- the
    generated c/mpi/pt2pt/standard/Makefile reads
    `NVCC = /usr/local/cuda/bin/nvcc`.

    So --with-cuda is load-bearing for the kernel build and not merely for the
    include and library paths, and dropping it from either configure line puts
    the bare word `nvcc` back on the compile line. That is what failed the g6
    osiris build of 2026-07-31 under OSU 7.4, which hardcoded `NVCC = nvcc` in
    eleven Makefile.am files with no @NVCC@ substitution at all: _osu_cuda_mode
    correctly answered =yes (the node does have a compiler driver at
    $CUDA_HOME/bin), configure never tests for nvcc, and the build reached
    make[4] and died with `nvcc: No such file or directory` and Error 127 after
    STREAM had already installed.
    """

    @pytest.mark.parametrize("func", ["cmd_install", "_build_osu_cuda"])
    def test_both_build_paths_pass_with_cuda(self, func):
        """Neither path may configure CUDA without it. cmd_install builds on a GPU
        head node (the g6 osiris layout); _build_osu_cuda is the only thing that
        compiles the kernels on the common CPU-head layout."""
        body = _function_body(func)
        lines = [
            line for line in body.splitlines()
            if not line.lstrip().startswith("#")
        ]
        text = "\n".join(lines)
        assert "--enable-cuda=" in text, f"{func} no longer configures CUDA at all"
        assert "--with-cuda=" in text, (
            f"{func} configures --enable-cuda with no --with-cuda. OSU substitutes "
            "NVCC from it; without it the kernel compile runs a bare `nvcc` "
            "resolved against PATH and dies with Error 127 on any node whose nvcc "
            "lives only at $CUDA_HOME/bin."
        )

    def test_the_run_time_build_passes_the_toolkit_path(self, tmp_path):
        """Executed rather than read: a --with-cuda inside an unreached branch is
        not one any node uses. The argv is recovered from the build log the driver
        writes, which is also where an operator would look."""
        r, prefix, _, _ = _osu_cuda_run_harness(tmp_path, nvcc="cuda_home")
        out = r.stdout.decode()
        assert r.returncode == 0, r.stderr.decode() + out
        assert "--enable-cuda=yes" in out, (
            f"the harness did not reach the =yes path, so this test is vacuous: {out}"
        )
        logs = list((prefix / "build_logs").glob("osu-cuda.log"))
        assert logs, f"_build_osu_cuda wrote no build log under {prefix}"
        argv = [
            line for line in logs[0].read_text().splitlines()
            if line.startswith("CONFIGURE ARGS=")
        ]
        assert argv, f"configure was never invoked: {logs[0].read_text()!r}"
        assert all("--with-cuda=" in line for line in argv), (
            "_build_osu_cuda configured CUDA without --with-cuda, so OSU has "
            f"nothing to substitute NVCC from: {argv}"
        )

    def test_the_version_is_new_enough_to_substitute_nvcc(self):
        """The @NVCC@ substitution is a 7.5-series feature. 7.4 hardcoded
        `NVCC = nvcc`, which no configure argument could reach, and 7.4 also
        predates CUDA 13 -- it calls the 4-argument cudaMemPrefetchAsync that
        NVIDIA replaced with the cudaMemLocation form, so it cannot compile
        against the toolkit on the current PCluster GPU AMI at all."""
        version = _driver_var("OSU_VERSION")
        parts = version.split(".")
        assert len(parts) >= 2, f"unparseable OSU_VERSION: {version!r}"
        major, minor = int(parts[0]), int(parts[1])
        assert (major, minor) >= (7, 5), (
            f"OSU_VERSION is {version}; versions before 7.5 hardcode `NVCC = nvcc` "
            "instead of substituting it from --with-cuda, and call the "
            "pre-CUDA-13 cudaMemPrefetchAsync signature that no longer exists."
        )

    def test_nvcc_is_not_worked_around_by_hand(self):
        """Both of the obvious hand-rolled fixes are wrong and are the ones a
        future reader would reach for. Measured against GNU make, not assumed: an
        exported NVCC loses to a makefile's own assignment, so `export NVCC=` has
        no effect; and a `make NVCC=...` override would beat configure's correctly
        matched value with whatever happens to be first on PATH."""
        with open(BENCHMARK) as fh:
            for n, line in enumerate(fh, 1):
                if line.lstrip().startswith("#"):
                    continue
                if re.search(r"\bexport\s+NVCC=", line):
                    raise AssertionError(
                        f"{BENCHMARK}:{n} exports NVCC: {line.strip()!r}. A "
                        "makefile assignment beats the environment; this has no "
                        "effect on the kernel build. --with-cuda is the mechanism."
                    )
                if re.search(r"\bmake\b[^#]*\bNVCC=", line):
                    raise AssertionError(
                        f"{BENCHMARK}:{n} overrides NVCC on the make command line: "
                        f"{line.strip()!r}. configure already substitutes it from "
                        "--with-cuda; an override here can point the kernel build "
                        "at a different nvcc than the headers it compiles against."
                    )


class TestTwoConcurrentRunsDoNotFightOverTheIorScratchFiles:
    """--fs-path names a FILESYSTEM to stress, and the default is a directory
    under the submit tree, so two jobs on one cluster are meant to share it.
    The test FILES were a fixed `ior_testfile`, though, so they shared those too:
    both jobs wrote the same per-rank files, and whichever finished first ran
    `rm -f "$fs_path/ior_testfile"*` and deleted the other's out from under it.

    IOR reports that as `ERROR: stat(...) failed, (aiori-POSIX.c:866)` naming
    only the path, with nothing pointing at another job -- so the operator reads
    it as a filesystem fault. Observed on osiris with jobs 7 (compute queue) and
    8 (gpu queue) overlapping by four seconds, both writing
    .../ior_scratch/ior_testfile.00000000 and .00000001.

    The fix is $ts in the object name, which is date+PID -- the same value that
    already makes $results_dir/$ts unique per run.
    """

    def _prefix(self, tmp_path):
        prefix = tmp_path / "bin"
        b = prefix / "ior" / "bin"
        b.mkdir(parents=True, exist_ok=True)
        # Stands in for IOR: records the -o value it was handed, writes its
        # per-rank files, waits, then checks they are still there -- which is
        # what a real IOR does between its write and read phases, and where the
        # aiori-POSIX.c:866 stat() failure came from.
        #
        # The wait is read from the environment so the two runs finish at
        # different times. Equal durations do not reproduce anything: both would
        # check before either had reached its cleanup. On osiris the shorter job
        # (8) ran its rm at 14:41:08, four seconds into the longer one (7).
        (b / "ior").write_text(
            "#!/bin/bash\n"
            'o=""\n'
            'while [[ $# -gt 0 ]]; do\n'
            '  case "$1" in -o) o="$2"; shift 2 ;; *) shift ;; esac\n'
            'done\n'
            'echo "IOR OBJECT=$o"\n'
            'for r in 00000000 00000001; do : > "$o.$r"; done\n'
            'sleep "${HARNESS_IOR_SLEEP:-1}"\n'
            'for r in 00000000 00000001; do\n'
            '  [[ -f "$o.$r" ]] || { echo "IOR STAT FAILED $o.$r" >&2; exit 1; }\n'
            'done\n'
            'echo "IOR OK"\n'
        )
        (b / "ior").chmod(0o755)
        (prefix / ".build_arch").write_text(
            subprocess.run(["uname", "-m"], capture_output=True,
                           text=True).stdout
        )
        return prefix

    def _launch(self, tmp_path, prefix, fs_path, results, tag, sleep):
        stub = tmp_path / f"stub-{tag}"
        stub.mkdir(parents=True, exist_ok=True)
        for name in _STUB_PASSTHROUGH + ("tail", "sleep", "nproc"):
            real = shutil.which(name)
            if real and not (stub / name).exists():
                (stub / name).symlink_to(real)
        if not (stub / "nproc").exists():
            # macOS has no nproc; cmd_run reads it before dispatching any tool.
            (stub / "nproc").write_text("#!/bin/bash\necho 2\n")
            (stub / "nproc").chmod(0o755)
        launcher = stub / "mpirun"
        launcher.write_text('#!/bin/bash\nshift 2\nexec "$@"\n')
        launcher.chmod(0o755)
        script = f"""
        set -euo pipefail
        BENCH_BIN={str(prefix)!r}
        HPC_BENCHMARK_LIB_ONLY=1 source {BENCHMARK!r}
        BENCH_BIN={str(prefix)!r}
        cmd_run --tests ior --fs-path {str(fs_path)!r} \
            --results-dir {str(results)!r}
        echo RUN_OK
        """
        return subprocess.Popen(
            [_BASH, "-c", script], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(os.environ, PATH=str(stub),
                     HARNESS_IOR_SLEEP=str(sleep)),
            cwd=str(tmp_path),
        )

    def _both(self, tmp_path):
        prefix = self._prefix(tmp_path)
        fs_path = tmp_path / "ior_scratch"
        procs = [
            self._launch(tmp_path, prefix, fs_path,
                         tmp_path / f"results-{tag}", tag, sleep)
            for tag, sleep in (("a", 4), ("b", 1))
        ]
        return [(p.wait(), *p.communicate()) for p in procs]

    def test_neither_run_deletes_the_others_test_files(self, tmp_path):
        for rc, out, err in self._both(tmp_path):
            text = out.decode() + err.decode()
            assert b"IOR STAT FAILED" not in err, (
                "one run's rm deleted the other's test files, which IOR reports "
                f"as a stat() failure on a path it does not explain: {text}"
            )
            assert rc == 0, text
            assert b"IOR OK" in out, text

    def test_each_run_uses_its_own_object_name(self, tmp_path):
        objects = set()
        for rc, out, err in self._both(tmp_path):
            for line in out.decode().splitlines():
                if line.startswith("IOR OBJECT="):
                    objects.add(line.split("=", 1)[1])
        assert len(objects) == 2, (
            f"both runs handed IOR the same -o value: {objects}. The rm after "
            f"the run then matches the other job's files too."
        )

    def test_each_run_still_removes_its_own_files(self, tmp_path):
        """Vacuity guard on the other direction: making the names unique by
        dropping the cleanup would leave a full-size file per rank per run on the
        filesystem being benchmarked, which fills it."""
        self._both(tmp_path)
        left = sorted(p.name for p in (tmp_path / "ior_scratch").iterdir())
        assert left == [], f"IOR test files were left behind: {left}"

    def test_the_harness_can_see_the_collision(self, tmp_path, monkeypatch):
        """Without this the three above pass against any implementation whose
        two runs simply do not overlap."""
        source = open(BENCHMARK).read()
        broken = source.replace('-o "$fs_path/ior_testfile.$ts" \\',
                                '-o "$fs_path/ior_testfile" \\')
        broken = broken.replace('rm -f "$fs_path/ior_testfile.$ts"*',
                                'rm -f "$fs_path/ior_testfile"*')
        assert broken != source, "the fixed-name mutation matched nothing"
        broken_path = tmp_path / "broken-hpc-benchmark.sh"
        broken_path.write_text(broken)
        broken_path.chmod(0o755)
        monkeypatch.setattr(f"{__name__}.BENCHMARK", str(broken_path))
        results = self._both(tmp_path)
        assert any(b"IOR STAT FAILED" in err for _, _, err in results), (
            "the harness did not reproduce the collision, so the tests above "
            "prove nothing: " + repr([err.decode() for _, _, err in results])
        )


class TestMpiIgnoresInterfacesEveryNodeShares:
    """A Docker bridge is 172.17.0.1/16 on every node, so a rank that advertises
    it hands every peer an address that routes back to the peer itself.  Open MPI
    does not fail on that -- it hangs.  Cluster iris deadlocked 13h14m on an
    8-rank all-reduce with TimeLimit=UNLIMITED, having written a 90-byte header
    and no data rows, while the 2-rank tests passed because both ranks were on
    one node.  --enable_monitoring is what installs Docker, so any monitored
    cluster has the bridge.

    These run the real function against a fake /sys/class/net, because the
    defect is entirely in which names the probe returns and whether an
    operator's own setting survives -- neither is visible in the source text.
    """

    def test_a_docker_bridge_is_excluded_on_both_channels(self, tmp_path):
        values, _ = _run_isolation(tmp_path, ["docker0", "eth0", "lo"])
        assert values["BTL"] == "lo,docker0", values
        assert values["OOB"] == "lo,docker0", values

    def test_the_oob_channel_is_set_too(self, tmp_path):
        """oob is the out-of-band wire-up and its own default exclude list is
        EMPTY (btl's is at least 127.0.0.1/8,sppp), so oob is the more exposed
        of the two.  Setting btl alone leaves the hang in place."""
        values, _ = _run_isolation(tmp_path, ["docker0", "eth0"])
        assert values["OOB"] != "<unset>", (
            "only btl was excluded; the out-of-band channel still dials the bridge"
        )

    def test_the_real_interface_is_never_excluded(self, tmp_path):
        """Excluding the only routable interface leaves MPI no transport at all,
        which is a worse failure than the one being fixed."""
        values, _ = _run_isolation(tmp_path, ["docker0", "eth0", "ens5", "efa0"])
        for iface in ("eth0", "ens5", "efa0"):
            assert iface not in values["BTL"].split(","), (
                f"{iface} is a real interface and must not be excluded: {values}"
            )
            assert iface not in values["OOB"].split(","), values

    @pytest.mark.parametrize(
        "iface",
        ["docker0", "br-1a2b3c", "virbr0", "veth9f2ab", "cni0", "flannel.1",
         "cali1234", "tunl0", "nerdctl0"],
    )
    def test_every_glob_in_the_list_actually_matches(self, tmp_path, iface):
        """_VIRTUAL_IFACE_GLOBS is only as good as its globs.  `br-*` needs the
        hyphen (bare `br*` would swallow a real `br0` bond) and `flannel.1`
        carries a dot, so each entry is exercised against a name of the shape
        the runtime actually produces."""
        values, _ = _run_isolation(tmp_path, [iface, "eth0"])
        assert values["BTL"] == f"lo,{iface}", (
            f"{iface} matched no glob in _VIRTUAL_IFACE_GLOBS: {values}"
        )

    def test_a_bare_bridge_name_is_not_swallowed_by_the_br_glob(self, tmp_path):
        """`br-*` rather than `br*`: `br0` is a conventional name for a real
        bonded interface, and excluding it would take the node off the network."""
        values, _ = _run_isolation(tmp_path, ["br0", "eth0"])
        assert values["BTL"] == "<unset>", (
            f"br0 is not a container bridge and must not be excluded: {values}"
        )

    def test_several_bridges_are_joined_with_commas(self, tmp_path):
        """Open MPI parses the value as a comma-separated list.  Any other
        separator is silently taken as one interface name that matches nothing."""
        values, _ = _run_isolation(tmp_path, ["docker0", "br-abc123", "veth9f2", "eth0"])
        assert values["BTL"].startswith("lo,"), values
        assert sorted(values["BTL"].split(",")) == sorted(
            ["lo", "br-abc123", "docker0", "veth9f2"]
        ), values

    def test_a_node_with_no_bridges_sets_nothing(self, tmp_path):
        """An unmonitored cluster has no Docker.  Exporting an exclude list there
        is a behavior change for no reason, and it would mask a future default."""
        values, _ = _run_isolation(tmp_path, ["eth0", "lo"])
        assert values["BTL"] == "<unset>", values
        assert values["OOB"] == "<unset>", values

    def test_an_operator_setting_survives(self, tmp_path):
        """Whoever set the variable has diagnosed something we have not."""
        values, _ = _run_isolation(
            tmp_path, ["docker0", "eth0"],
            preset={"OMPI_MCA_btl_tcp_if_exclude": "lo,docker0,ib0"},
        )
        assert values["BTL"] == "lo,docker0,ib0", values

    def test_an_operator_who_set_it_empty_keeps_it_empty(self, tmp_path):
        """Open MPI reports "" as `data source: environment` -- it is a chosen
        value, not an absent one.  A `-z` test on the value rather than a `+x`
        test on the name overwrites it, which is exactly the operator override
        this is meant to honor."""
        values, _ = _run_isolation(
            tmp_path, ["docker0", "eth0"],
            preset={"OMPI_MCA_oob_tcp_if_exclude": ""},
        )
        assert values["OOB"] == "", (
            f"an explicitly empty operator value was overwritten: {values}"
        )
        assert values["BTL"] == "lo,docker0", (
            "presetting one channel must not suppress the other"
        )

    def test_a_missing_sysfs_is_not_an_error(self, tmp_path):
        """The driver runs under `set -euo pipefail`.  macOS has no
        /sys/class/net, and neither does a container; an unmatched glob leaves
        the loop iterating over the literal pattern, so the `-e` test on each
        entry is load-bearing."""
        values, r = _run_isolation(
            tmp_path, [], netdir=str(tmp_path / "definitely-absent")
        )
        assert r.returncode == 0, r.stderr
        assert values["BTL"] == "<unset>", values

    def test_an_empty_sysfs_is_not_an_error(self, tmp_path):
        values, r = _run_isolation(tmp_path, [])
        assert r.returncode == 0, r.stderr
        assert values["BTL"] == "<unset>", values

    def test_the_exclusion_is_announced(self, tmp_path):
        """Silently rewriting a rank's transport selection is the kind of thing
        an operator has to be able to see in the job log when the numbers move."""
        _, r = _run_isolation(tmp_path, ["docker0", "eth0"])
        assert "docker0" in r.stdout, r.stdout
        assert "Excluded virtual interfaces" in r.stdout, r.stdout

    def test_run_actually_calls_it(self):
        """Every test above passes with the call site deleted while every real
        multi-node job on a monitored cluster still hangs."""
        body = _function_body("cmd_run")
        assert "_isolate_mpi_interfaces" in body, (
            "cmd_run no longer isolates the virtual interfaces; a monitored "
            "cluster's multi-node MPI jobs deadlock again"
        )

    def test_the_isolation_precedes_the_first_launch(self):
        """Open MPI reads OMPI_MCA_* from the environment at launch, so exporting
        after the first mpirun leaves that test running on the bridge."""
        body = _function_body("cmd_run")
        assert body.index("_isolate_mpi_interfaces") < body.index("$mpi_launcher"), (
            "the interfaces must be isolated before any MPI launch"
        )

    def test_the_variables_are_exported_not_just_assigned(self):
        """mpirun is a child process.  A plain assignment is invisible to it,
        and every runtime test above passes either way because the reads happen
        in the same shell."""
        body = _function_body("_isolate_mpi_interfaces")
        for name in ("OMPI_MCA_btl_tcp_if_exclude", "OMPI_MCA_oob_tcp_if_exclude"):
            assert f"export {name}=" in body, (
                f"{name} is assigned but not exported, so mpirun never sees it"
            )

    def test_env_vars_rather_than_mca_flags(self):
        """$mpi_launcher may be Intel MPI's mpiexec, which rejects --mca
        outright and would fail every run on such a cluster.  OMPI_MCA_* is
        ignored by any launcher that is not Open MPI."""
        with open(BENCHMARK) as fh:
            lines = [
                l for l in fh.read().splitlines()
                if not l.strip().startswith("#")
            ]
        offenders = [l for l in lines if "--mca" in l]
        assert not offenders, (
            f"--mca is not portable to Intel MPI's mpiexec: {offenders}"
        )

    def test_the_harness_can_see_an_unisolated_driver(self, tmp_path):
        """Vacuity guard for the whole class: with the exports removed, the
        assertions above must fail rather than pass on an absent mechanism."""
        with open(BENCHMARK) as fh:
            source = fh.read()
        neutered = tmp_path / "hpc-benchmark.sh"
        neutered.write_text(
            source.replace(
                'export OMPI_MCA_btl_tcp_if_exclude="lo,$excluded"', ":"
            ).replace(
                'export OMPI_MCA_oob_tcp_if_exclude="lo,$excluded"', ":"
            )
        )
        assert ":" in neutered.read_text()
        script = f"""
        set -euo pipefail
        HPC_BENCHMARK_LIB_ONLY=1 source {str(neutered)!r}
        HPC_BENCHMARK_NET_DIR={str(_net_dir(tmp_path, ["docker0", "eth0"]))!r}
        _isolate_mpi_interfaces
        echo "BTL=[${{OMPI_MCA_btl_tcp_if_exclude-<unset>}}]"
        """
        env = dict(os.environ)
        env.pop("OMPI_MCA_btl_tcp_if_exclude", None)
        r = subprocess.run([_BASH, "-c", script], capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
        assert "BTL=[<unset>]" in r.stdout, (
            "the harness cannot tell an isolated driver from an unisolated one"
        )


_ACCESS_SCRIPTS = ("access_cluster.j2", "grafana_tunnel.j2")


def _render_template(name, cluster_params):
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    return env.get_template(name).render(**cluster_params)


def _run_access_script(tmp_path, name, cluster_params, *, aws_rc, aws_stdout,
                       aws_stderr="", env_extra=None):
    """Execute a rendered access script with a stub `aws` and stop before ssh.

    A missing instance and a failed API call are told apart only by the exit
    status of `aws`, and a text assertion cannot see which branch a rendered
    script takes -- so the whole point is to run it with each and read the
    diagnosis back.  ssh is stubbed to exit 0 so a successful resolution ends
    cleanly rather than dialing a real host.

    Returns (CompletedProcess, capture_dir), where capture_dir is where the
    stubbed mktemp puts the stderr capture file so its cleanup is observable.
    """
    rendered = _render_template(name, cluster_params)
    # Unique per call: one test runs the same script twice to compare the two
    # diagnoses against each other.
    run_dir = tmp_path / f"run-{name}-{aws_rc}"
    run_dir.mkdir()
    script = run_dir / f"{name}.sh"
    script.write_text(rendered)

    stub = run_dir / "bin"
    stub.mkdir()
    # A separate directory from the mktemp capture below: that one is asserted
    # empty after the run to prove the stderr-capture cleanup trap fired, and
    # this argv log would otherwise look like a leak of its own.
    argv_dir = run_dir / "argv"
    argv_dir.mkdir()
    # shlex.quote, not repr: Python's repr renders a newline as a backslash-n
    # escape, which bash single quotes pass through literally -- the stub then
    # answers the 6-character string "None\n" and the `== "None"` test misses.
    (stub / "aws").write_text(
        "#!/bin/bash\n"
        f'echo "$@" >> {shlex.quote(str(argv_dir / "aws_args"))}\n'
        f"printf '%s' {shlex.quote(aws_stdout)}\n"
        f"printf '%s' {shlex.quote(aws_stderr)} >&2\n"
        f"exit {aws_rc}\n"
    )
    # A resolved IP must not reach a real host, and grafana_tunnel additionally
    # forks pgrep for the tunnel PID.
    (stub / "ssh").write_text("#!/bin/bash\necho SSH_REACHED \"$@\"\nexit 0\n")
    (stub / "pgrep").write_text("#!/bin/bash\necho 4242\n")

    # mktemp is stubbed rather than passed through so the capture file lands
    # somewhere observable.  TMPDIR is not a usable seam: macOS mktemp resolves
    # its default directory from _CS_DARWIN_USER_TEMP_DIR and ignores TMPDIR
    # entirely, so a TMPDIR-based leak check silently watches an empty directory.
    capture_dir = run_dir / "captures"
    capture_dir.mkdir()
    (stub / "mktemp").write_text(
        "#!/bin/bash\n"
        f"p={str(capture_dir)!r}/capture.$$\n"
        ': > "$p"\n'
        'echo "$p"\n'
    )
    for tool in ("bash", "sh", "sed", "rm", "dirname", "basename",
                 "cat", "kill", "printf", "echo"):
        path = shutil.which(tool)
        if path:
            (stub / tool).symlink_to(path)
    for f in stub.iterdir():
        if not f.is_symlink():
            f.chmod(0o755)

    # The key must exist, or both scripts detour into retrieve_ssh_key.
    keypair = tmp_path / "key.pem"
    keypair.write_text("")

    env = dict(os.environ, PATH=str(stub), AWS_PROFILE="stub-profile")
    if env_extra:
        env.update(env_extra)
    r = subprocess.run(
        [_BASH, str(script)],
        capture_output=True, text=True, env=env, cwd=str(run_dir),
    )
    return r, capture_dir


@pytest.fixture
def access_params(cluster_params, tmp_path):
    return dict(cluster_params, ssh_keypair=str(tmp_path / "key.pem"))


class TestAFailedAwsCallIsNotReportedAsAStoppedCluster:
    """`describe-instances 2>/dev/null || true` collapsed two unrelated failures
    into one message.  A missing instance answers the literal string "None" with
    rc=0; expired credentials, an unset AWS_PROFILE, or a throttled API answer
    with a non-zero rc and a message on stderr.  Both used to print "Is the
    cluster running?", which sends an operator whose cluster is perfectly healthy
    to check the cluster.  That happened on iris.

    These execute the rendered scripts with a stub `aws`, because the defect is
    which branch runs and a rendered-text assertion cannot tell them apart.
    """

    @pytest.mark.parametrize("name", _ACCESS_SCRIPTS)
    def test_the_rendered_script_is_valid_bash(self, name, access_params):
        r = subprocess.run(
            ["bash", "-n"], input=_render_template(name, access_params).encode(),
            capture_output=True,
        )
        assert r.returncode == 0, r.stderr

    @pytest.mark.parametrize("name", _ACCESS_SCRIPTS)
    def test_an_auth_failure_says_it_is_not_a_cluster_problem(
        self, tmp_path, name, access_params
    ):
        r, _ = _run_access_script(
            tmp_path, name, access_params, aws_rc=255, aws_stdout="",
            aws_stderr="An error occurred (ExpiredToken) when calling the "
                       "DescribeInstances operation: The security token "
                       "included in the request is expired\n",
        )
        assert r.returncode != 0, r.stdout
        assert "NOT a cluster problem" in r.stderr, r.stderr
        assert "Is the cluster running?" not in r.stderr + r.stdout, (
            "an AWS API failure is still being reported as a stopped cluster"
        )

    @pytest.mark.parametrize("name", _ACCESS_SCRIPTS)
    def test_the_real_aws_error_reaches_the_operator(
        self, tmp_path, name, access_params
    ):
        """2>/dev/null discarded the one line that names the actual cause."""
        r, _ = _run_access_script(
            tmp_path, name, access_params, aws_rc=255, aws_stdout="",
            aws_stderr="An error occurred (ExpiredToken): token is expired\n",
        )
        assert "ExpiredToken" in r.stderr, (
            f"aws's own message was swallowed: {r.stderr}"
        )

    @pytest.mark.parametrize("name", _ACCESS_SCRIPTS)
    def test_the_auth_message_names_the_profile_in_effect(
        self, tmp_path, name, access_params
    ):
        """The usual cause is the wrong AWS_PROFILE, and the operator cannot see
        which one was used from the error alone."""
        r, _ = _run_access_script(
            tmp_path, name, access_params, aws_rc=255, aws_stdout="",
            aws_stderr="AccessDenied\n",
        )
        assert "stub-profile" in r.stderr, r.stderr
        assert "get-caller-identity" in r.stderr, (
            "the message does not tell the operator how to check credentials"
        )

    @pytest.mark.parametrize("name", _ACCESS_SCRIPTS)
    def test_a_stopped_cluster_still_says_so(self, tmp_path, name, access_params):
        """The other half: rc=0 with "None" is a genuinely absent head node, and
        it must not be reported as a credentials problem."""
        r, _ = _run_access_script(
            tmp_path, name, access_params, aws_rc=0, aws_stdout="None\n"
        )
        assert r.returncode != 0, r.stdout
        assert "No running head node" in r.stderr, r.stderr
        assert "NOT a cluster problem" not in r.stderr, (
            "an absent cluster is being reported as an AWS failure"
        )
        assert "list_pcluster.py" in r.stderr, (
            "the message does not tell the operator how to check the cluster"
        )

    @pytest.mark.parametrize("name", _ACCESS_SCRIPTS)
    def test_the_two_diagnoses_are_distinguishable(
        self, tmp_path, name, access_params
    ):
        """Vacuity guard for the pair above: the whole point is that the two
        inputs produce different text, and a single generic message would satisfy
        every individually-worded assertion if it happened to mention both."""
        failed, _ = _run_access_script(
            tmp_path, name, access_params, aws_rc=255, aws_stdout="",
            aws_stderr="AccessDenied\n",
        )
        absent, _ = _run_access_script(
            tmp_path, name, access_params, aws_rc=0, aws_stdout="None\n"
        )
        assert failed.stderr != absent.stderr, (
            "a failed AWS call and a stopped cluster print the same thing"
        )

    @pytest.mark.parametrize("name", _ACCESS_SCRIPTS)
    def test_a_private_ip_only_cluster_resolves(self, tmp_path, name, access_params):
        """PCluster head nodes in a private subnet have no public IP -- iris's
        head node answered "None" for PublicIpAddress and 10.0.1.20 for
        PrivateIpAddress -- so the fallback must survive the rc handling."""
        r, _ = _run_access_script(
            tmp_path, name, access_params, aws_rc=0, aws_stdout="10.0.1.20\n"
        )
        assert r.returncode == 0, r.stderr + r.stdout
        assert "10.0.1.20" in r.stdout + r.stderr, r.stdout

    @pytest.mark.parametrize("name", _ACCESS_SCRIPTS)
    def test_no_describe_instances_call_discards_stderr(self, name):
        """`2>/dev/null` on the call is the defect itself, and `|| true` erases
        the exit status the two branches are told apart by."""
        with open(os.path.join(TEMPLATE_DIR, name)) as fh:
            lines = [
                l for l in fh.read().splitlines()
                if not l.strip().startswith("#")
            ]
        joined = "\n".join(lines)
        assert "describe-instances" in joined, f"{name} no longer queries EC2"

        # Scoped to the query and its call sites, not the whole file: the `kill`
        # on a stale PID file and the `pgrep` for the tunnel PID both discard
        # stderr legitimately, and a whole-file assertion would forbid those too.
        start = next(i for i, l in enumerate(lines) if "describe-instances" in l)
        end = next(i for i in range(start, len(lines)) if lines[i].strip() == "}")
        query = "\n".join(lines[start : end + 1])
        callers = [l for l in lines if l.strip().startswith("HEAD_NODE_IP=")]
        assert len(callers) == 2, (
            f"{name} no longer falls back from the public to the private IP: {callers}"
        )
        for region, label in ((query, "query"), ("\n".join(callers), "call sites")):
            assert "2>/dev/null" not in region, (
                f"{name}'s {label} discards aws's stderr again, so the operator "
                f"cannot see why the call failed"
            )
            assert "|| true" not in region, (
                f"{name}'s {label} erases aws's exit status again, which is the "
                f"only thing separating a failed call from a stopped cluster"
            )

    @pytest.mark.parametrize("name", _ACCESS_SCRIPTS)
    @pytest.mark.parametrize("aws_rc,aws_stdout", [(0, "10.0.1.20\n"), (255, "")])
    def test_the_stderr_capture_is_cleaned_up(
        self, tmp_path, name, access_params, aws_rc, aws_stdout
    ):
        """The capture file is a mktemp in the operator's temp dir and these
        scripts are run interactively many times a day.  Both exits are checked:
        access_cluster ends in `exec ssh`, which replaces the shell, and an EXIT
        trap does still fire on exec -- but the failure paths take `exit 1`
        instead, so a trap that only covered one of the two would go unnoticed."""
        r, capture_dir = _run_access_script(
            tmp_path, name, access_params, aws_rc=aws_rc, aws_stdout=aws_stdout,
            aws_stderr="AccessDenied\n" if aws_rc else "",
        )
        assert (r.returncode == 0) == (aws_rc == 0), r.stderr + r.stdout
        leaked = list(capture_dir.iterdir())
        assert not leaked, f"{name} left its stderr capture behind: {leaked}"


def _argv_dir_for(tmp_path, name, aws_rc):
    """Mirrors _run_access_script's own run_dir/argv_dir derivation. Needed
    because that function returns (result, capture_dir) throughout the
    existing suite, and adding a third return value would touch every one of
    its call sites for the sake of this one class."""
    return tmp_path / f"run-{name}-{aws_rc}" / "argv"


class TestAccessClusterLoginNodeSelection:
    """access_cluster.py resolves -L/-H into one of two fixed literals and
    passes it via the ACCESS_NODE_TYPE environment variable; access_cluster.j2
    reads it (defaulting to HeadNode when unset, so existing behavior is
    unchanged for clusters that never set it) and both the EC2 tag filter and
    the diagnostic messages must follow it. grafana_tunnel.j2 has none of this
    -- it is untouched, out of scope per the plan -- so this class covers
    access_cluster.j2 only, reusing the same rc/stderr matrix the head-node
    tests above use."""

    def test_default_still_queries_head_node(self, tmp_path, access_params):
        r, _ = _run_access_script(
            tmp_path, "access_cluster.j2", access_params,
            aws_rc=0, aws_stdout="10.0.1.20\n",
        )
        assert r.returncode == 0, r.stderr + r.stdout
        args = (_argv_dir_for(tmp_path, "access_cluster.j2", 0) / "aws_args").read_text()
        assert "Values=HeadNode" in args
        assert "Values=LoginNode" not in args

    def test_login_node_tag_filter_is_used_when_selected(self, tmp_path, access_params):
        r, _ = _run_access_script(
            tmp_path, "access_cluster.j2", access_params,
            aws_rc=0, aws_stdout="10.0.1.20\n",
            env_extra={"ACCESS_NODE_TYPE": "LoginNode"},
        )
        assert r.returncode == 0, r.stderr + r.stdout
        args = (_argv_dir_for(tmp_path, "access_cluster.j2", 0) / "aws_args").read_text()
        assert "Values=LoginNode" in args
        assert "Values=HeadNode" not in args

    def test_login_node_diagnostics_name_the_login_node_when_absent(
        self, tmp_path, access_params
    ):
        r, _ = _run_access_script(
            tmp_path, "access_cluster.j2", access_params,
            aws_rc=0, aws_stdout="None\n",
            env_extra={"ACCESS_NODE_TYPE": "LoginNode"},
        )
        assert r.returncode != 0, r.stdout
        assert "No running login node found" in r.stderr, r.stderr
        assert "head node" not in r.stderr

    def test_login_node_diagnostics_name_the_login_node_on_auth_failure(
        self, tmp_path, access_params
    ):
        r, _ = _run_access_script(
            tmp_path, "access_cluster.j2", access_params,
            aws_rc=255, aws_stdout="", aws_stderr="AccessDenied\n",
            env_extra={"ACCESS_NODE_TYPE": "LoginNode"},
        )
        assert r.returncode != 0, r.stdout
        assert "while looking up the login node" in r.stderr, r.stderr


def _run_osu_slots(tmp_path, tests, extra_args):
    """Invoke `run` against a stubbed MPI launcher and an arch stamp that
    matches, and report whether the OSU slot preflight fired.

    This has to execute: the check is a function of the derived total_ranks and
    of whether "osu" is in a comma-separated list, and neither is visible in the
    source text.  Every path here fails eventually -- there are no real
    benchmark binaries -- so the assertion is on WHICH error message comes out,
    not on the exit status, which is 1 either way.
    """
    stub = tmp_path / "stub"
    stub.mkdir()
    launcher = stub / "mpirun"
    launcher.write_text("#!/bin/bash\nexit 0\n")
    launcher.chmod(0o755)

    tree = tmp_path / "hb"
    shutil.copytree(os.path.join(REPO_ROOT, "hpc-benchmark"), tree)
    binroot = tree / "bin"
    binroot.mkdir(exist_ok=True)
    (binroot / ".build_arch").write_text(
        subprocess.run(["uname", "-m"], capture_output=True, text=True).stdout
    )

    env = dict(os.environ)
    env["PATH"] = f"{stub}:{env['PATH']}"
    r = subprocess.run(
        [_BASH, str(tree / "hpc-benchmark.sh"), "run", "--tests", tests,
         *extra_args, "--results-dir", str(tmp_path / "res")],
        capture_output=True, text=True, env=env, cwd=str(tree),
    )
    return r


_SLOT_MESSAGE = "at least 2 MPI slots"


class TestOsuRefusesAnAllocationItCannotRunIn:
    """OSU pt2pt is hardcoded `-n 2` -- osu_latency, osu_bw, and the two CUDA
    device-to-device variants -- so a 1-slot allocation cannot run it no matter
    what --ppn says.  Open MPI does not explain that: it prints "There are not
    enough slots available in the system to satisfy the 2 slots that were
    requested" followed by the absolute path of the binary, which reads as a
    problem with the binary.  That cost a round trip on the g6 osiris build of
    2026-07-29.

    The pre-existing multi-node gate cannot catch this -- it tests `nodes -gt 1`
    only, so `--nodes 1 --ppn 1` sails straight past it into mpirun.
    """

    def test_a_single_slot_osu_run_is_refused_by_name(self, tmp_path):
        r = _run_osu_slots(tmp_path, "osu", ["--nodes", "1", "--ppn", "1"])
        assert _SLOT_MESSAGE in r.stdout + r.stderr, r.stdout + r.stderr

    def test_the_diagnosis_names_the_slot_count_it_found(self, tmp_path):
        """"needs at least 2" alone does not tell the operator what they asked
        for; the count is what makes it actionable."""
        r = _run_osu_slots(tmp_path, "osu", ["--nodes", "1", "--ppn", "1"])
        assert "has 1" in r.stdout + r.stderr, r.stdout + r.stderr

    def test_the_diagnosis_carries_a_command_that_would_work(self, tmp_path):
        """The whole point is not repeating the round trip: --ntasks=2 is the
        fix and the message must contain it."""
        r = _run_osu_slots(tmp_path, "osu", ["--nodes", "1", "--ppn", "1"])
        out = r.stdout + r.stderr
        assert "--ntasks=2" in out, out

    @pytest.mark.parametrize("tests", ["osu", "osu,ior", "stream,osu", "ior,osu,hpcg"])
    def test_osu_anywhere_in_the_list_is_caught(self, tmp_path, tests):
        """A substring test on "osu" would also match a hypothetical "osu2",
        and a test on the whole string would miss every combined list -- which
        is what the job template actually submits (stream,osu,ior,hpcg)."""
        r = _run_osu_slots(tmp_path, tests, ["--nodes", "1", "--ppn", "1"])
        assert _SLOT_MESSAGE in r.stdout + r.stderr, (
            f"--tests {tests} was not caught: " + r.stdout + r.stderr
        )

    @pytest.mark.parametrize("tests", ["osu_cuda", "osuxx", "myosu"])
    def test_the_match_is_on_the_token_not_a_substring(self, tmp_path, tests):
        """`[[ "$tests" == *osu* ]]` is the tempting spelling and it is wrong:
        it claims any future test name containing "osu" needs 2 slots, and
        answers with the slot diagnosis instead of the "unknown test" error that
        actually applies.  The comma-delimited form is what makes the check
        precise.  These names are all rejected downstream -- the property is
        that the rejection is the RIGHT one, so this asserts on which message
        comes out, which is the only observable difference between the two
        spellings."""
        r = _run_osu_slots(tmp_path, tests, ["--nodes", "1", "--ppn", "1"])
        out = r.stdout + r.stderr
        assert _SLOT_MESSAGE not in out, (
            f"{tests!r} merely contains 'osu' and must not trip the slot "
            "guard: " + out
        )
        assert "unknown test" in out, (
            f"{tests!r} should have been rejected as an unknown test: " + out
        )

    @pytest.mark.parametrize("tests", ["stream", "ior", "hpcg", "stream,ior"])
    def test_a_list_without_osu_is_never_blocked(self, tmp_path, tests):
        """STREAM is single-node by design and IOR/HPCG take their rank count
        from --ppn, so a 1-slot run of any of them is legitimate.  Blocking
        them would be a worse regression than the bug being fixed."""
        r = _run_osu_slots(tmp_path, tests, ["--nodes", "1", "--ppn", "1"])
        assert _SLOT_MESSAGE not in r.stdout + r.stderr, (
            f"--tests {tests} needs no 2 slots but was blocked: "
            + r.stdout + r.stderr
        )

    def test_two_slots_are_enough(self, tmp_path):
        """The guard must stop at the actual requirement.  If this fires the
        check is off by one and every correct invocation is rejected."""
        r = _run_osu_slots(tmp_path, "osu", ["--nodes", "1", "--ppn", "2"])
        assert _SLOT_MESSAGE not in r.stdout + r.stderr, r.stdout + r.stderr

    def test_the_check_runs_before_a_results_directory_is_created(self, tmp_path):
        """Same principle as validating checksums before the first AWS
        mutation: a refused run must leave nothing behind to clean up."""
        res = tmp_path / "res"
        r = _run_osu_slots(tmp_path, "osu", ["--nodes", "1", "--ppn", "1"])
        assert _SLOT_MESSAGE in r.stdout + r.stderr, r.stdout + r.stderr
        assert not res.exists(), f"a refused run created {res}"

    def test_the_harness_can_see_an_unguarded_run(self, tmp_path):
        """Vacuity guard.  Every path in _run_osu_slots exits non-zero, so a
        harness that never reached the preflight at all would satisfy each
        must-not-fire test above.  A 2-slot osu run has to get PAST the guard
        and die on the missing binaries instead."""
        r = _run_osu_slots(tmp_path, "osu", ["--nodes", "1", "--ppn", "2"])
        assert "OSU not found" in r.stdout + r.stderr, (
            "the harness never reached the OSU stage, so the negative tests "
            "above prove nothing: " + r.stdout + r.stderr
        )


class TestEveryShellScriptIsUnderTheShellcheckGate:
    """The gate ran on one file out of five for the life of the repo, so
    `run_integration_test.sh` accumulated 11 findings nobody saw and the two
    deployment scripts were clean only by luck.  The Makefile now derives the
    list from `git ls-files` rather than naming files, which is the property
    under test: a new .sh must join the gate without anyone remembering it.

    The excluded file is excluded for a reason that must stay true -- it is a
    Jinja2 template, and raw shellcheck stops analyzing it at SC1072."""

    _EXCLUDED = "scripts/sbatch_default_submission_script.sh"

    def _makefile(self):
        with open(os.path.join(REPO_ROOT, "Makefile")) as fh:
            return fh.read()

    def _gated_files(self):
        """Ask make itself, rather than reimplementing the filter-out."""
        r = subprocess.run(
            ["make", "-n", "shellcheck"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        line = next(
            l for l in r.stdout.splitlines() if l.strip().startswith("shellcheck ")
        )
        return set(line.split()[1:])

    def _tracked_shell_files(self):
        r = subprocess.run(
            ["git", "ls-files", "*.sh"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        assert r.returncode == 0, r.stderr
        return set(r.stdout.split())

    def test_the_gate_is_derived_not_a_hardcoded_list(self):
        """A literal list is exactly as wide as whoever wrote it remembered --
        which is how four files went unchecked.  The recipe has to expand
        `git ls-files`, so an added script is covered on the next run."""
        text = self._makefile()
        assert "git ls-files" in text, (
            "the shellcheck gate no longer discovers its own file list; a "
            "hardcoded list is what let four of five shell scripts go unchecked"
        )

    def test_every_tracked_shell_script_is_checked_or_deliberately_excluded(self):
        tracked = self._tracked_shell_files()
        gated = self._gated_files()
        assert tracked, "git ls-files found no shell scripts at all"
        unaccounted = tracked - gated - {self._EXCLUDED}
        assert not unaccounted, (
            f"{sorted(unaccounted)} are tracked shell scripts that the "
            "shellcheck gate neither checks nor explicitly excludes"
        )

    def test_the_exclusion_list_holds_only_the_jinja2_template(self):
        """The escape hatch must not become a dumping ground for scripts that
        merely fail.  Only a template belongs here, because only a template
        cannot be parsed at all."""
        text = self._makefile()
        line = next(
            l for l in text.splitlines() if l.startswith("SHELLCHECK_EXCLUDE")
        )
        excluded = line.split(":=", 1)[1].split()
        assert excluded == [self._EXCLUDED], (
            f"the shellcheck exclusion list is {excluded}; only the Jinja2 "
            "template may be excluded, and every other .sh must pass"
        )

    def test_the_excluded_file_really_is_unparseable_by_shellcheck(self):
        """Vacuity guard on the exclusion.  If the template ever stops carrying
        Jinja2 -- or gains a render step -- the reason for the exclusion is gone
        and it must rejoin the gate.  SC1072/SC1073 mean shellcheck gave up
        parsing, which is different from a script with findings."""
        path = os.path.join(REPO_ROOT, self._EXCLUDED)
        assert os.path.exists(path), f"{self._EXCLUDED} no longer exists"
        with open(path) as fh:
            body = fh.read()
        assert "{%" in body, (
            f"{self._EXCLUDED} no longer contains Jinja2, so the reason it is "
            "excluded from the shellcheck gate no longer holds"
        )
        r = subprocess.run(
            ["shellcheck", self._EXCLUDED],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        assert r.returncode != 0, (
            f"{self._EXCLUDED} now passes shellcheck; remove it from "
            "SHELLCHECK_EXCLUDE"
        )
        assert "SC1072" in r.stdout or "SC1073" in r.stdout, (
            "the exclusion is justified by shellcheck being unable to PARSE "
            "the file, not by it having findings; it now reports only "
            f"findings:\n{r.stdout}"
        )

    def test_the_gate_passes(self):
        """The gate is only worth widening if it is green -- a red gate gets
        disabled, and CI runs exactly this."""
        r = subprocess.run(
            ["make", "shellcheck"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        assert r.returncode == 0, r.stdout + r.stderr

    def test_no_disable_directive_silences_a_whole_file(self):
        """The other way to defeat a widened gate: a `# shellcheck disable=` above
        the first command applies to the entire file, so the gate keeps running on
        four scripts and keeps finding nothing.  Widening the file list and adding
        a blanket disable passed every other test here.

        A directive must sit on the line it excuses and carry a reason.  Both of
        the repo's real ones do -- SC2053 in hpc-benchmark.sh and SC2029 in the
        integration test -- and each is one line above the command it applies to."""
        for relpath in self._tracked_shell_files():
            with open(os.path.join(REPO_ROOT, relpath)) as fh:
                lines = fh.read().splitlines()
            # The first executable line: a file-level directive must precede it.
            first_code = next(
                (i for i, l in enumerate(lines)
                 if l.strip() and not l.lstrip().startswith("#")),
                len(lines),
            )
            for i, line in enumerate(lines):
                if "shellcheck disable" not in line:
                    continue
                assert i > first_code, (
                    f"{relpath}:{i + 1} carries a shellcheck disable before the "
                    "first command, which applies to the whole file and silences "
                    "the gate: " + line.strip()
                )
                assert "#" in line.split("disable=", 1)[1], (
                    f"{relpath}:{i + 1} disables a check without stating why: "
                    + line.strip()
                )


class TestSshOptionsSurviveWordSplitting:
    """`SSH_OPTS` held four options plus the key path in one string, so every
    `ssh $SSH_OPTS` depended on an unquoted expansion -- and shellcheck's own
    SC2086 fix is WRONG here: `"$SSH_OPTS"` hands ssh a single 90-character
    argument and every call fails.  Applying that suggestion mechanically to
    silence the gate would have broken the whole integration test, so the array
    form is pinned rather than left to the next person's judgment."""

    _SCRIPT = "tests/integration/run_integration_test.sh"

    def _text(self):
        with open(os.path.join(REPO_ROOT, self._SCRIPT)) as fh:
            return fh.read()

    def _code(self):
        """Comment lines stripped.  The comment above SSH_OPTS quotes both broken
        forms verbatim to explain why they are wrong, so matching against the raw
        file finds the very strings the script no longer executes."""
        return "\n".join(
            l for l in self._text().splitlines() if not l.lstrip().startswith("#")
        )

    def test_ssh_opts_is_an_array(self):
        text = self._text()
        assert re.search(r"^SSH_OPTS=\(", text, re.M), (
            "SSH_OPTS is not an array; as a string it either word-splits from "
            "an unquoted expansion or, if quoted, becomes one giant argument"
        )

    def test_no_call_site_passes_it_as_a_single_string(self):
        """The mutation this exists to catch: `ssh "$SSH_OPTS"`."""
        text = self._code()
        bad = re.findall(r'ssh\s+"\$\{?SSH_OPTS\}?"', text)
        assert not bad, (
            f"{bad} passes every ssh option as one argument; use "
            '"${SSH_OPTS[@]}"'
        )

    def test_no_call_site_relies_on_an_unquoted_expansion(self):
        text = self._code()
        bad = re.findall(r"ssh\s+\$\{?SSH_OPTS\b", text)
        assert not bad, f"{bad} is an unquoted expansion (SC2086)"

    def test_every_ssh_call_uses_the_array(self):
        text = self._code()
        calls = re.findall(r"ssh\s+(\S+)", text)
        calls = [c for c in calls if "SSH_OPTS" in c or c.startswith("$")]
        assert calls, "no ssh call sites found; this test has stopped covering anything"
        for c in calls:
            assert c == '"${SSH_OPTS[@]}"', (
                f"ssh is invoked with {c}, not the SSH_OPTS array"
            )

    def test_the_key_path_is_quoted_inside_the_array(self):
        """The array is also what makes a space in the key path survivable --
        the string form could not quote one element without quoting all of them."""
        text = self._text()
        block = text[text.index("SSH_OPTS=("):]
        block = block[:block.index(")")]
        assert '-i "${SSH_KEY}"' in block, (
            "the key path is unquoted inside the array; a path with a space "
            "would split into two arguments"
        )

    def test_the_array_expands_to_separate_arguments(self):
        """Executed, not asserted: whether ssh receives 8 arguments or 1 is a
        property of bash's expansion rules, not of the text.  Rebuild the real
        array from the script and count what a command would actually see."""
        text = self._text()
        block = text[text.index("SSH_OPTS=("):]
        block = block[:block.index(")") + 1]
        script = f'SSH_KEY="/path/with space/key.pem"\n{block}\nprintf "%s\\n" "${{SSH_OPTS[@]}}"\n'
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        args = r.stdout.splitlines()
        assert len(args) == 8, f"expected 8 separate arguments, got {args}"
        assert "/path/with space/key.pem" in args, (
            f"the key path did not survive as one argument: {args}"
        )

    def test_the_harness_can_see_the_broken_form(self):
        """Vacuity guard.  The count assertion above is only meaningful if the
        quoted-string form really does collapse to one argument."""
        script = (
            'SSH_KEY="/tmp/k.pem"\n'
            'SSH_OPTS="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=30 '
            '-o BatchMode=yes -i ${SSH_KEY}"\n'
            'printf "%s\\n" "$SSH_OPTS"\n'
        )
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        assert len(r.stdout.splitlines()) == 1, (
            "the quoted string form did not collapse to one argument, so the "
            "8-argument assertion above proves nothing"
        )
