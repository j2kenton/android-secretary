"""Is everything in place? Read-only checks, each with what to do if it isn't.

    bin/automations doctor

Run in Termux (bin/automations runs it there when called from Ubuntu). It only
reads: it never changes a setting, grants a permission or starts anything.
Each check is (name, needed, function); a function returns (ok, detail), and
detail says what to do when it isn't ok.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import tomllib
from typing import Callable

from . import android, paths

PREFIX = android.PREFIX
HOME = os.environ.get("HOME", "/data/data/com.termux/files/home")
Check = Callable[[], "tuple[bool, str]"]


def _ok(cmd: list[str], timeout: float = 20) -> bool:
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def python() -> tuple[bool, str]:
    v = sys.version_info
    return v >= (3, 11), f"Python {v.major}.{v.minor}" + ("" if v >= (3, 11) else ": needs 3.11+ (pkg upgrade python)")


def tools() -> tuple[bool, str]:
    missing = [t for t in ("tmux", "termux-notification-list", "proot-distro")
               if not os.path.exists(os.path.join(android.BIN, t))]
    return not missing, "tmux, termux-api, proot-distro" if not missing else \
        f"missing {', '.join(missing)}: run bin/setup"


def notification_access() -> tuple[bool, str]:
    try:
        n = android.notifications()
        return True, f"reads notifications ({len(n)} now)"
    except Exception as e:  # noqa: BLE001  (any failure means the same fix)
        return False, ("can't read notifications: install the Termux:API app (from the same place as Termux), "
                       "then Settings > Notifications > Device & app notifications > allow Termux:API "
                       f"({str(e)[:80]})")


def sms() -> tuple[bool, str]:
    try:
        android.sms_inbox(limit=1)
        return True, "reads SMS"
    except Exception:  # noqa: BLE001
        return False, "can't read SMS: Settings > Apps > Termux:API > Permissions > SMS > Allow"


def contacts() -> tuple[bool, str]:
    try:
        return True, f"reads contacts ({len(android.contacts())})"
    except Exception:  # noqa: BLE001
        return False, "can't read contacts: Settings > Apps > Termux:API > Permissions > Contacts > Allow"


def private_settings() -> tuple[bool, str]:
    p, example = paths.ROOT / "private.toml", paths.ROOT / "private.example.toml"
    if not p.exists():
        return False, "no private.toml: run bin/setup, then fill it in (SETUP.md)"
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        return False, f"private.toml doesn't parse: {e}"
    if example.exists() and p.read_bytes() == example.read_bytes():
        return False, "private.toml is still the example: make it yours (SETUP.md, step 4)"
    return True, f"private.toml ({len(data)} tables)"


def rules() -> tuple[bool, str]:
    if not paths.RULES.exists():
        return False, "no rules.toml: run bin/setup"
    from .rules import load
    try:
        _, rs = load(paths.RULES, paths.ROOT / "private.toml")
    except Exception as e:  # noqa: BLE001  (ConfigError, a TOML error...)
        return False, f"rules.toml has a problem: {e}"
    return True, f"rules.toml ({sum(r.enabled for r in rs)} rules on)"


def claude() -> tuple[bool, str]:
    from .claude import CLAUDE, DISTRO, PROOT_DISTRO
    if not os.path.exists(PROOT_DISTRO):
        return False, "no proot-distro: Claude's parts need Claude Code in Ubuntu (README: Claude Code on the phone)"
    if not _ok([PROOT_DISTRO, "login", DISTRO, "--", "test", "-x", CLAUDE], timeout=60):
        return False, f"Claude Code isn't installed in {DISTRO} at {CLAUDE} (README: Claude Code on the phone)"
    if not _ok([PROOT_DISTRO, "login", DISTRO, "--", "test", "-s", "/root/.claude/.credentials.json"], timeout=60):
        return False, "Claude Code isn't logged in: run claude in Ubuntu and /login"
    return True, "Claude Code installed and logged in"


def adb() -> tuple[bool, str]:
    if android.adb_output("echo ok", timeout=40) == "ok\n":
        return True, "adb reaches the phone"
    return False, ("adb isn't connected (needed to clear other apps' notifications): turn on "
                   "Wireless debugging, then bin/automations adb (first time: SETUP.md step 5)")


def overlay() -> tuple[bool, str]:
    out = android.adb_output("appops get com.termux SYSTEM_ALERT_WINDOW", timeout=40)
    if out is None:
        return False, "not checked (needs adb): Settings > Apps > Termux > Appear on top > Allow"
    return "allow" in out.lower(), "Termux can open apps from the background" if "allow" in out.lower() else \
        "Termux can't open apps from the background: Settings > Apps > Termux > Appear on top > Allow"


def boot() -> tuple[bool, str]:
    f = os.path.join(HOME, ".termux", "boot", "start-automations")
    if os.path.exists(f) and str(paths.ROOT) in open(f, encoding="utf-8").read():
        return True, "starts after a reboot (Termux:Boot)"
    return False, "won't start after a reboot: install the Termux:Boot app, open it once, run bin/setup"


def age() -> tuple[bool, str]:
    return (True, "age installed") if shutil.which("age") else (False, "no age: pkg install age")


def backup_remote() -> tuple[bool, str]:
    from . import backup
    problem = backup._cannot_push()
    return (True, "backups go to a private repo") if not problem else (False, problem)


def daemon() -> tuple[bool, str]:
    socks = sorted(glob.glob(f"{PREFIX}/var/run/tmux-*/default"))
    args = [os.path.join(android.BIN, "tmux"), *(["-S", socks[0]] if socks else []), "has-session", "-t", "automations"]
    return (True, "running") if _ok(args) else (False, "not running: bin/automations start")


CHECKS: list[tuple[str, bool, Check]] = [
    ("Python", True, python),
    ("Termux packages", True, tools),
    ("Notifications", True, notification_access),
    ("SMS", True, sms),
    ("Contacts", True, contacts),
    ("Your settings", True, private_settings),
    ("Rules", True, rules),
    ("Claude", False, claude),
    ("adb", False, adb),
    ("Open apps", False, overlay),
    ("After reboot", False, boot),
    ("Backup: age", False, age),
    ("Backup: repo", False, backup_remote),
    ("Daemon", False, daemon),
]


def run(checks: list[tuple[str, bool, Check]] = CHECKS, out=print) -> int:
    """Print one line per check. Returns how many needed checks failed."""
    failed = 0
    for name, needed, fn in checks:
        try:
            ok, detail = fn()
        except Exception as e:  # noqa: BLE001  (a check must never stop the others)
            ok, detail = False, f"check failed: {e}"
        mark = "✓" if ok else ("✗" if needed else "–")
        failed += needed and not ok
        out(f"{mark} {name:<16} {detail}")
    out("")
    out("All set." if not failed else
        f"{failed} thing(s) to fix (✗). Lines with – are optional: they turn on more features.")
    return failed
