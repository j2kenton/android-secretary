"""Nightly encrypted backup of what isn't in git.

What: private.toml, rules.toml, the decisions in var/ (group answers, offers, ideas,
calendar answers, chat ids, ...), Claude's memory notes for this setup, the
Tasker export, the Termux boot script and the installed package list. Not the
logs or saved message copies.

How: a reproducible tar, encrypted with age to the public key in backup.pub
(the matching secret key is the owner's, kept in their password manager and
not on the phone, so the phone can lock a backup but never open one), pushed
to the `backup` branch of a PRIVATE repo, only when something changed. That repo
is the git remote named `backup` (`git remote add backup <url of a private repo>`),
never `origin`: the code's own repo may be public. No `backup` remote, or one that
GitHub says is public: the backup is committed locally and not pushed.

Restore (in a clone of the private repo, with the secret key in key.txt):
    git fetch backup backup
    git show backup/backup:backup.tar.gz.age | age -d -i key.txt | tar xz -C <dir>
Paths inside are relative to the Termux home (<this folder>/…, .termux/…)
or, for the memory notes, claude-memory/.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import logging
import os
import shutil
import subprocess
import tarfile
from datetime import datetime
from pathlib import Path
from .paths import VAR

log = logging.getLogger("automations.backup")

ROOT = Path(__file__).resolve().parent.parent           # phone-automations
HOME = ROOT.parent
PUBKEY = ROOT / "backup.pub"
STATE = VAR / "backup.json"
WORKTREE = HOME / ".phone-automations-backup"         # the backup branch, checked out on its own
BRANCH = "backup"
REMOTE = "backup"                                       # a private repo; never origin (see the top)
OUT = "backup.tar.gz.age"

VAR_KEEP = ["groups/assign.json", "groups/offers.json", "groups/times.json", "groups/questions.jsonl",
            "groups/roundup.json", "secretary/ideas.json", "calendar-copy.json", "whatsapp-jids.json",
            "launchers.json", "email-lists.json", "parcels.json", "reminders/cards.json", "school/items.jsonl",
            "summaries", "state.json", "adb-serial"]
MEMORY = Path("/data/data/com.termux/files/usr/var/lib/proot-distro/containers/ubuntu/rootfs"
              "/root/.claude/projects/-root-proj/memory")
TASKER = HOME / "storage" / "shared" / "Tasker" / "configs" / "user" / "backup.xml"


def sources() -> list[tuple[Path, str]]:
    """(file on the phone, its name in the archive), for every file that exists."""
    out = [(ROOT / "private.toml", f"{ROOT.name}/private.toml"),
           (ROOT / "rules.toml", f"{ROOT.name}/rules.toml")]           # the owner's own rules: not in git either
    for rel in VAR_KEEP:
        p = VAR / rel
        files = sorted(x for x in p.rglob("*") if x.is_file()) if p.is_dir() else [p]
        out += [(f, f"{ROOT.name}/var/{f.relative_to(VAR)}") for f in files]
    if MEMORY.is_dir():
        out += [(f, f"claude-memory/{f.name}") for f in sorted(MEMORY.glob("*.md"))]
    out += [(TASKER, "tasker/backup.xml"), (HOME / ".termux" / "boot" / "start-automations", ".termux/boot/start-automations")]
    return [(p, name) for p, name in out if p.is_file()]


def archive(files: list[tuple[Path, str]], extra: dict[str, bytes] | None = None) -> bytes:
    """A tar.gz that's byte-for-byte the same for the same contents (no times, owners)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        items = [(name, p.read_bytes()) for p, name in files] + sorted((extra or {}).items())
        for name, data in sorted(items):
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode = len(data), 0, 0o600
            tar.addfile(info, io.BytesIO(data))
    import gzip
    return gzip.compress(buf.getvalue(), mtime=0)


def _git(*args, cwd=WORKTREE, check=True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check, timeout=120)


def _worktree() -> None:
    """The backup branch in its own folder (an orphan branch: no code in it)."""
    if (WORKTREE / ".git").exists():
        _git("pull", "-q", "--rebase", REMOTE, BRANCH, check=False)
        return
    _git("fetch", "-q", REMOTE, BRANCH, cwd=ROOT, check=False)
    has_remote = _git("rev-parse", "--verify", "-q", f"{REMOTE}/{BRANCH}", cwd=ROOT, check=False).returncode == 0
    if has_remote:
        _git("worktree", "add", "-q", str(WORKTREE), "-B", BRANCH, f"{REMOTE}/{BRANCH}", cwd=ROOT)
    else:
        _git("worktree", "add", "-q", "--detach", str(WORKTREE), cwd=ROOT)
        _git("checkout", "-q", "--orphan", BRANCH)
        _git("rm", "-rfq", ".", check=False)


def _cannot_push() -> str:
    """Why the backup mustn't be pushed, or "" if it may: the `backup` remote must exist and
    mustn't be a public repo (asked of GitHub when the gh CLI is there)."""
    url = _git("remote", "get-url", REMOTE, cwd=ROOT, check=False).stdout.strip()
    if not url:
        return f"no git remote named {REMOTE!r} (a private repo): add one with `git remote add {REMOTE} <url>`"
    m = re.search(r"github\.com[:/]([^/]+/[^/.]+?)(?:\.git)?/?$", url)
    gh = shutil.which("gh")
    if m and gh:
        r = subprocess.run([gh, "api", f"repos/{m[1]}", "--jq", ".private"], capture_output=True, text=True, timeout=60)
        if r.returncode == 0 and r.stdout.strip() == "false":
            return f"{m[1]} is a public repo"
    return ""


def run(event=None, push: bool = True, **_) -> str:
    """Make the backup; returns what happened ("unchanged", "pushed", "committed")."""
    recipient = PUBKEY.read_text(encoding="utf-8").strip()
    files = sources()
    pkgs = subprocess.run(["dpkg-query", "-W", "-f", "${Package}\\n"], capture_output=True, text=True,
                          timeout=60).stdout.encode()
    data = archive(files, {"termux-packages.txt": pkgs})
    digest = hashlib.sha256(data).hexdigest()
    try:
        st = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    if st.get("sha256") == digest and st.get("pushed"):
        return "unchanged"
    enc = subprocess.run(["age", "-r", recipient], input=data, capture_output=True, timeout=120, check=True).stdout
    _worktree()
    (WORKTREE / OUT).write_bytes(enc)
    (WORKTREE / "README.md").write_text(
        "Encrypted backup of phone-automations' private config and state (see automations/backup.py on main).\n"
        "Restore: `git show backup/backup:backup.tar.gz.age | age -d -i key.txt | tar xz -C <dir>`\n", encoding="utf-8")
    _git("add", OUT, "README.md")
    if _git("diff", "--cached", "--quiet", check=False).returncode != 0:
        _git("-c", "user.name=phone-automations", "-c", "user.email=phone-automations@localhost",
             "commit", "-q", "-m", f"Backup {datetime.now():%Y-%m-%d %H:%M} ({len(files)} files)")
    result = "committed"
    why_not = push and _cannot_push()
    if why_not:
        log.warning("backup: not pushed: %s", why_not)
    elif push:
        r = _git("push", "-q", "-u", REMOTE, BRANCH, check=False)
        if r.returncode != 0:
            log.warning("backup: push failed (kept for next time): %s", r.stderr.strip()[:200])
        else:
            result = "pushed"
    st.update(sha256=digest, pushed=result == "pushed", at=datetime.now().isoformat(timespec="minutes"),
              files=len(files), size=len(enc))
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=1), encoding="utf-8")
    log.info("backup: %s (%d files, %d KB)", result, len(files), len(enc) // 1024)
    return result
