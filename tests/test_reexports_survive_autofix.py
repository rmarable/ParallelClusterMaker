"""Every unused-looking import in an entry point is protected, on purpose.

The entry points delegate to `pcluster_core` and re-export names the test
suite reaches *through the shim*, which is what keeps each shim honest
about the core it fronts. To a linter those imports look dead: 28 of them
were reported by pyflakes, and `ruff check --fix` on default settings
deletes exactly that category. 17 were load-bearing re-exports and 11 were
genuinely dead -- a distinction no tool can make, and one that only holds
if it is written down where the tool can read it.

So every survivor is protected in a way ruff honors: `__all__` for the
re-export groups, `# noqa: F401` with a rationale for the three module
imports that exist so tests can patch `mod.subprocess.run`. This test
fails if a new unprotected one appears, which is the moment to decide
which kind it is -- not later, in a diff nobody reads, after --fix has
already removed it.
"""

import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _unused_imports():
    files = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    files = [f for f in files if not f.startswith("tests/")]
    out = subprocess.run(
        [sys.executable, "-m", "pyflakes", *files],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    ).stdout
    return [ln for ln in out.splitlines() if "imported but unused" in ln]


def _protected(path, symbol):
    """Would ruff keep this import? `__all__` and `# noqa: F401` both work."""
    src = open(os.path.join(REPO_ROOT, path)).read()
    if f'"{symbol}"' in src and "__all__" in src:
        return True
    for line in src.splitlines():
        if symbol in line and "noqa" in line and "F401" in line:
            return True
    return False


class TestNoReExportIsLeftForAutofixToDelete:
    def test_every_unused_import_is_explicitly_protected(self):
        offenders = []
        for finding in _unused_imports():
            path, _, rest = finding.partition(":")
            symbol = rest.split("'")[1].split(".")[-1] if "'" in rest else rest
            if not _protected(path, symbol):
                offenders.append(f"{path}: {symbol}")
        assert offenders == [], (
            "unprotected unused import(s) -- `ruff check --fix` would delete "
            "these. If a name is a re-export the tests reach through this "
            "module, add it to __all__; if it is genuinely dead, remove it:\n  "
            + "\n  ".join(offenders)
        )

    @pytest.mark.parametrize(
        "module,symbol",
        [
            ("check_pcluster.py", "check_slurm"),
            ("cost_pcluster.py", "_safe"),
            ("diagnose_pcluster.py", "_format_sinfo"),
            ("list_pcluster.py", "_age_str"),
        ],
    )
    def test_the_re_exports_are_actually_reachable(self, module, symbol):
        """Vacuity guard. __all__ satisfies the linter whether or not the
        name is really exported, so assert the import still works."""
        mod = __import__(module[:-3])
        assert hasattr(mod, symbol), f"{module} no longer re-exports {symbol}"

    def test_the_shims_declare_what_they_re_export(self):
        for path in (
            "check_pcluster.py",
            "cost_pcluster.py",
            "diagnose_pcluster.py",
            "list_pcluster.py",
        ):
            src = open(os.path.join(REPO_ROOT, path)).read()
            assert "__all__" in src, f"{path} lost its __all__"
