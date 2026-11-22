#!/usr/bin/env python3
"""On-demand phone lookups for a Claude session (runs where `adb` is: the proot distro).

    bin/phone tab            # the Chrome tab used most recently (normally the one on screen), incognito included
    bin/phone tabs [-n N]    # Chrome's open tabs, most recently used first
    bin/phone inbox [-n N]   # links shared to Termux (Share -> Termux), newest first

Chrome lookups need the Wireless debugging connection (home Wi-Fi). Nothing
here opens, changes or saves tabs; the inbox is a plain file on the phone.
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INBOX = ROOT / "var" / "shared.tsv"


def adb(*args: str, timeout: float = 15) -> str:
    out = subprocess.run(["adb", *args], capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise SystemExit(f"adb {' '.join(args)} failed: {(out.stderr or out.stdout).strip()}"
                         "\nIs Wireless debugging on and connected? (adb devices)")
    return out.stdout


def chrome_tabs() -> list[dict]:
    # Chrome's DevTools socket is named chrome_devtools_remote[_<pid>] on this phone.
    sockets = re.findall(r"@(chrome_devtools_remote\S*)", adb("shell", "cat /proc/net/unix"))
    if not sockets:
        raise SystemExit("Chrome isn't exposing its tab list (is Chrome running?)")
    with socket.socket() as s:                       # pick a free local port
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    adb("forward", f"tcp:{port}", f"localabstract:{sockets[0]}")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=10) as r:
            targets = json.load(r)
    finally:
        adb("forward", "--remove", f"tcp:{port}")
    return [{"title": t.get("title", ""), "url": t.get("url", "")}
            for t in targets if t.get("type") == "page"]


def main() -> int:
    p = argparse.ArgumentParser(prog="phone")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tab")
    for name in ("tabs", "inbox"):
        sub.add_parser(name).add_argument("-n", type=int, default=50)
    a = p.parse_args()

    if a.cmd in ("tab", "tabs"):
        tabs = chrome_tabs()
        for t in tabs[:1] if a.cmd == "tab" else tabs[:a.n]:
            print(f"{t['title']}\n  {t['url']}")
        return 0

    lines = INBOX.read_text(encoding="utf-8").splitlines() if INBOX.exists() else []
    for line in reversed(lines[-a.n:]):
        when, _, link = line.partition("\t")
        print(f"{when}  {link}")
    if not lines:
        print("(nothing shared yet)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
