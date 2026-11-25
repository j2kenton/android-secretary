"""One line of Ensemble status, read live from the cloud box.

Runs the ensemble-ops skill's read-only waiting-check on the box over ssh
(Termux's ssh config has the `ensemble-devbox` alias) and boils it down to
"bugs9, impl-high-review: paused 4 h 44 min ago, 4 cards waiting".
"""

from __future__ import annotations

import os
import re
import subprocess

HOST = "ensemble-devbox"
CHECK = "python3 /workspace/claude-skills/ensemble-ops/scripts/waiting-check.py"
SSH = os.environ.get("PREFIX", "/data/data/com.termux/files/usr") + "/bin/ssh"


# Runs on the box (python3 -, read-only): live run claims with their process
# checked, plus waiting-check's report. Prints one JSON object.
PROBE = r"""
import glob, json, os, subprocess
running, dead = [], []
for claim in glob.glob("/workspace/*/.ensemble/*/admission-v1/admission.*"):
    if claim.endswith(".tombstone"):
        continue
    task = os.path.dirname(os.path.dirname(claim))
    try:
        c = json.load(open(claim))
        prog = json.load(open(os.path.join(task, "task-progress.json")))
    except Exception:
        continue
    try:
        os.kill(int(c.get("pid")), 0); alive = True
    except Exception:
        alive = False
    item = {"name": prog.get("displayName") or os.path.basename(task), "stage": prog.get("currentStage", ""),
            "since": c.get("startedAt", ""), "workspace": task.split("/")[2]}
    (running if alive else dead).append(item)
wc = subprocess.run(["python3", "/workspace/claude-skills/ensemble-ops/scripts/waiting-check.py"],
                    capture_output=True, text=True, timeout=60).stdout
print(json.dumps({"running": running, "dead": dead, "waiting": wc}))
"""


def _ago(iso: str) -> str:
    from datetime import datetime, timezone
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    mins = int((datetime.now(timezone.utc) - t).total_seconds() // 60)
    return f"{mins} min" if mins < 60 else f"{mins // 60} h {mins % 60} min"


def state(timeout: float = 60) -> dict:
    """{"kind": "needs" | "running" | "stopped" | "unknown", "title": ..., "detail": ...}
    needs = a card waits for the owner, a run was paused or stopped, or a run's
    process died; running = a live run and nothing waiting; stopped = neither."""
    import json
    try:
        out = subprocess.run([SSH, "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", HOST, "python3", "-"],
                             input=PROBE, capture_output=True, text=True, timeout=timeout)
        data = json.loads(out.stdout)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return {"kind": "unknown", "title": "⚠️ Ensemble: box unreachable", "detail": "Couldn't check the box.\nEnsemble's own notifications are left as they are."}
    items = [i for i in parse(data.get("waiting", "")) if not i["deferred"]]
    waiting = summarise(data.get("waiting", ""))
    # Needs the owner: a card waiting on a stopped task, or a run that died.
    # A pause or stop with nothing open is just idle: it doesn't need the owner.
    blocking = [i for i in items if i["kind"] in ("DEAD", "ORPHAN")
                or (i["kind"] in ("PAUSED", "STOPPED") and i["cards"])]
    run = data.get("running", [])
    run_line = "; ".join(f"{r['name']} · {r['stage']} ({_ago(r['since'])})" for r in run)
    dead = [d for d in data.get("dead", []) if not DEFERRED.search(d.get("name", ""))]
    if dead:
        d = dead[0]
        return {"kind": "needs", "title": "🔵 Ensemble needs you",
                "detail": f"{d['name']} · {d['stage']}: the run died (its claim is held, the process is gone)."}
    if blocking:
        return {"kind": "needs", "title": "🔵 Ensemble needs you",
                "detail": "; ".join(_line(i) for i in blocking) + "\n"
                          + (("Also running: " + run_line) if run else "Nothing else is running.")}
    if run:
        extra = "" if waiting == "nothing waiting for you" else f"\nCards are open but block nothing: {waiting}"
        return {"kind": "running", "title": "🟢 Ensemble running",
                "detail": f"{run_line}\nNothing needs you; let it go." + extra}
    return {"kind": "stopped", "title": "⚪ Ensemble stopped, nothing needed",
            "detail": "No run is going and nothing is waiting."}


def status_line(timeout: float = 40) -> str:
    try:
        out = subprocess.run([SSH, "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", HOST, CHECK],
                             capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return "box unreachable"
    if out.returncode != 0:
        return "box unreachable"
    return summarise(out.stdout)


DEFERRED = re.compile(r"-*\s*DEFERRED\s*-*\s*", re.I)    # the owner's mark on a parked task's name


def parse(report: str) -> list[dict]:
    """waiting-check output -> [{kind: PAUSED|STOPPED|DEAD|ORPHAN|CARD..., body, cards, deferred}]."""
    items: list[dict] = []
    for line in report.splitlines():
        if m := re.match(r"([A-Z]{4,})\s+(.*)", line):          # PAUSED / STOPPED / CARD ...
            body = re.sub(r"\bcard\(s\)", "cards", m[2])
            body = re.sub(r"\s*\([^)]*\)", "", body).strip()     # drop the "(workspace/.ensemble/...)" path
            body = re.sub(r"\s*\(the runner's list, exact\):?$|:$", "", body)
            deferred = bool(DEFERRED.search(body))
            items.append({"kind": m[1], "body": DEFERRED.sub("", body).strip(), "cards": 0, "deferred": deferred})
            if c := re.search(r"(\d+) cards open", body):
                items[-1]["cards"] = int(c[1])
        elif items and (c := re.search(r"(\d+) (?:unanswered question )?card\(s\)", line)):
            items[-1]["cards"] += int(c[1])
    return items


def _line(it: dict) -> str:
    cards = it["cards"]
    extra = f", {cards} card{'s' * (cards != 1)} waiting" if cards and "card" not in it["body"] else ""
    return it["body"] + extra


def summarise(report: str) -> str:
    """waiting-check output -> one line ("nothing waiting for you" when it lists
    nothing). Tasks the owner marked DEFERRED are left out; a pause with no card
    open says "idle", not waiting."""
    items = [i for i in parse(report) if not i["deferred"]]
    if not items:
        return "nothing waiting for you"
    return "; ".join(_line(i) + ("" if i["cards"] or i["kind"] not in ("PAUSED", "STOPPED") else " (idle, nothing to answer)")
                     for i in items)
