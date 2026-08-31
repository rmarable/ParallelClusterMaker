"""No Python file in this repo may reference an undefined name.

This gate exists because a real one shipped and reached a live build.
Commit 2677416 deleted `s3 = boto3.resource("s3")` as unused; a serial
upload 1,000 lines below still called `s3.Object(...)`, and the sweep that
"proved" it unused filtered on a pattern that also matched
`s3_bucketname`, hiding the one line that used it.

Nothing caught it:

  * no Python linting ran anywhere in the gates -- `make lint` was
    ansible-lint over two playbooks nothing executed, and it runs this
    same sweep now;
  * every test stubs at the AWS boundary, so no test executes that line;
  * and the call sits inside `except Exception: print("WARNING: could not
    upload serial number to S3: {e}")`, which turned a NameError into a
    line an operator reads as a transient S3 hiccup. The build reported
    success.

An undefined name is never a style question -- it is a guaranteed
NameError on any path that reaches it. This checks only that class.
pyflakes reports plenty else (unused locals, f-strings without
placeholders); those are deliberately not gated here, because turning this
into a general lint gate is a separate decision with a large backlog, and
a gate nobody can keep green stops being read.
"""

import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _python_files():
    """Every tracked Python file, plus the untracked ones under mcp_server/
    and tests/ -- the doc-hygiene tests are gitignored, and mcp_server/ was
    untracked until recently, so a git-only sweep would have skipped the
    newest code."""
    out = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    files = {os.path.join(REPO_ROOT, p) for p in out.stdout.split()}
    for sub in ("mcp_server", "tests", "src"):
        for dirpath, dirnames, filenames in os.walk(os.path.join(REPO_ROOT, sub)):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            files.update(os.path.join(dirpath, f) for f in filenames if f.endswith(".py"))
    return sorted(p for p in files if os.path.isfile(p))


def _undefined_names(paths):
    result = subprocess.run(
        [sys.executable, "-m", "pyflakes", *paths],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return [
        line for line in (result.stdout + result.stderr).splitlines() if "undefined name" in line
    ]


class TestNoUndefinedNames:
    def test_the_sweep_finds_the_repo_python(self):
        """Vacuity guard. A sweep that matched nothing would pass the check
        below in silence, and the obvious breakages -- a changed glob, a
        cwd slip -- are invisible without this."""
        files = _python_files()
        assert len(files) > 40, len(files)
        names = {os.path.basename(f) for f in files}
        assert {"pcluster_core.py", "make_pcluster.py", "tools.py"} <= names

    def test_no_file_references_an_undefined_name(self):
        offenders = _undefined_names(_python_files())
        assert offenders == [], (
            "undefined names are guaranteed NameErrors on any path that "
            "reaches them:\n  " + "\n  ".join(offenders)
        )

    def test_the_check_actually_detects_one(self, tmp_path):
        """Discrimination guard, driving the real helper rather than
        re-implementing it -- an `assert offenders == []` against a broken
        invocation passes forever."""
        bad = tmp_path / "broken.py"
        bad.write_text("def f():\n    return undefined_thing\n")
        assert _undefined_names([str(bad)])

    def test_a_clean_file_reports_nothing(self, tmp_path):
        good = tmp_path / "fine.py"
        good.write_text("import os\n\n\ndef f():\n    return os.getcwd()\n")
        assert _undefined_names([str(good)]) == []

    def test_pyflakes_is_a_declared_dependency(self):
        """The gate is only as reliable as its tool being installed. It
        arrived transitively before this; CI installs from requirements.txt
        and a transitive can vanish in any upstream release."""
        with open(os.path.join(REPO_ROOT, "requirements.txt")) as fh:
            names = {
                l.strip().split(">=")[0].split("==")[0]
                for l in fh
                if l.strip() and not l.startswith("#")
            }
        assert "pyflakes" in names
