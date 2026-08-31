"""
Load a top-level entry-point script as a module so its main() can be tested.

Every entry-point script fires a sys.exit() venv guard at import time by
comparing os.path.realpath(sys.prefix) against the repo's .venv/ directory.
Patching sys.prefix past that guard is the only way to import them; there is
no importable seam because the guard has to run before any project import.

Each loaded module gets a private copy in sys.modules keyed by an alias, so a
test that monkeypatches one module's globals cannot leak into another test.
"""

import importlib.util
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_entrypoint(script, alias=None):
    """Import <repo_root>/<script> with the venv guard satisfied."""
    name = alias or ("_ep_" + script.replace(".py", "").replace("-", "_"))
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO_ROOT, script))
    mod = importlib.util.module_from_spec(spec)
    orig_prefix = sys.prefix
    orig_mod = sys.modules.get(name)
    sys.prefix = os.path.join(REPO_ROOT, ".venv")
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.prefix = orig_prefix
        if orig_mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = orig_mod
    return mod


class RecordingRun:
    """Stand-in for subprocess.run that records argv and returns a set rc."""

    def __init__(self, returncode=0, rc_by_command=None):
        self.calls = []
        self.returncode = returncode
        self.rc_by_command = rc_by_command or {}

    def __call__(self, cmd, *args, **kwargs):
        self.calls.append(list(cmd))
        rc = self.returncode
        for key, value in self.rc_by_command.items():
            if key in cmd:
                rc = value
                break
        if kwargs.get("check") and rc != 0:
            import subprocess

            raise subprocess.CalledProcessError(rc, cmd)
        return _Completed(rc, cmd)

    def command_containing(self, token):
        """The first recorded argv containing token, or None."""
        for call in self.calls:
            if token in call:
                return call
        return None


class _Completed:
    def __init__(self, returncode, args):
        self.returncode = returncode
        self.args = args
        self.stdout = ""
        self.stderr = ""
