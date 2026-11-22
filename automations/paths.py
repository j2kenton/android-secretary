"""Where the automations keep what they remember: var/, next to the code.

Tests never touch it: when unittest is running (or AUTOMATIONS_VAR is set), it's a
temporary folder instead, so running the tests can't change the live state.
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _var() -> Path:
    if os.environ.get("AUTOMATIONS_VAR"):
        return Path(os.environ["AUTOMATIONS_VAR"])
    if "unittest" in sys.modules:
        d = tempfile.mkdtemp(prefix="automations-test-var-")
        os.environ["AUTOMATIONS_VAR"] = d          # the same folder for anything started from the tests
        atexit.register(shutil.rmtree, d, True)
        return Path(d)
    return ROOT / "var"


VAR = _var()
# The owner's private settings and rules (both git-ignored). Tests don't read them, so they pass
# on any clone: no private settings, and the public example rules.
TESTING = "unittest" in sys.modules
PRIVATE = None if TESTING else ROOT / "private.toml"
RULES = ROOT / ("rules.example.toml" if TESTING else "rules.toml")
