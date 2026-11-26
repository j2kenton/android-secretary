"""Which Claude Code conversation to pick up after a crash or a reboot.

bin/claude-session starts Claude Code with Remote Control; with this it resumes
the conversation used most recently (`claude --resume <id>`, in that
conversation's folder) instead of starting a new one. A conversation still open
in a running Claude Code is never picked: two of them writing to one
conversation would mix it up.

    python -m automations claude-last      prints "<folder>\\t<id>", or nothing
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

ROOTFS = "/data/data/com.termux/files/usr/var/lib/proot-distro/containers/ubuntu/rootfs"
# Seen from Termux; from Ubuntu itself it's /root/.claude/projects.
PROJECTS = [Path(ROOTFS + "/root/.claude/projects"), Path("/root/.claude/projects")]
RESUME = re.compile(r"^(--resume|-r)$")


def conversations(projects: Path) -> list[tuple[float, str, str]]:
    """(modified, folder, id) of each conversation with at least one message, newest first."""
    out = []
    for f in projects.glob("*/*.jsonl"):
        cwd, said = None, False
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if cwd is None and '"cwd"' in line:
                        try:
                            cwd = json.loads(line).get("cwd")
                        except ValueError:
                            pass
                    said = said or '"type":"user"' in line
                    if cwd and said:
                        break
            if cwd and said:
                out.append((f.stat().st_mtime, cwd, f.stem))
        except OSError:
            continue
    return sorted(out, reverse=True)


def running() -> list[tuple[list[str], str]]:
    """(args, folder) of each interactive Claude Code running now (not `claude -p`)."""
    out = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            args = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace").split("\0")[:-1]
            if not args or not re.search(r"(^|/)claude$|/claude/versions/", args[0]) or {"-p", "--print"} & set(args):
                continue
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            continue
        out.append((args, cwd[len(ROOTFS):] if cwd.startswith(ROOTFS + "/") else cwd))
    return out


def pick(convs: list[tuple[float, str, str]], procs: list[tuple[list[str], str]]) -> tuple[str, str] | None:
    """The newest conversation not open in a running Claude Code: (folder, id)."""
    busy = {a[i + 1] for a, _ in procs for i in range(len(a) - 1) if RESUME.match(a[i])}
    for args, cwd in procs:                     # a new or --continue'd one: the newest in its folder
        if not any(RESUME.match(x) for x in args):
            newest = next((c for c in convs if c[1] == cwd and c[2] not in busy), None)
            if newest:
                busy.add(newest[2])
    return next(((cwd, cid) for _t, cwd, cid in convs if cid not in busy), None)


def main() -> int:
    projects = next((p for p in PROJECTS if p.is_dir()), None)
    found = pick(conversations(projects), running()) if projects else None
    if found:
        print(f"{found[0]}\t{found[1]}")
    return 0
