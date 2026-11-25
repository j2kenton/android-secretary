"""Summary cards you can finish.

A summary card shows only lines you haven't marked seen, one page at a time:
as many as fit on an expanded card, then an end line ("— end —", or how many
are still waiting). If you can see the end line, you've seen the whole page.
"✓ Hide these lines" marks exactly the lines shown as seen (never lines you didn't see) and
shows the next page, or removes the card when nothing is left. Swiping the card
away marks nothing: it comes back with the same lines the next time it updates.

State lives in var/summaries/<name>.json: the ids marked seen, and the lines
waiting (as last rendered), so the button's command can show the next page
without redoing the digest.
"""

from __future__ import annotations

import json
import math
import shlex
from pathlib import Path

from .paths import VAR
from . import android
from . import digest as dg

ROOT = Path(__file__).resolve().parent.parent
DIR = VAR / "summaries"

# An expanded card shows about 8 rows of about 40 characters (Samsung, default
# font). A page fills ROWS rows, then the end line takes the last one.
ROWS = 7
WIDTH = 40


def _path(name: str) -> Path:
    return DIR / f"{name}.json"


def _load(name: str) -> dict:
    try:
        return json.loads(_path(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"seen": [], "pending": [], "title": name, "nid": name, "priority": "low"}


def _save(name: str, st: dict) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    _path(name).write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")


def rows(line: str) -> int:
    return max(1, math.ceil(len(line) / WIDTH))


def page(pending: list[list[str]]) -> list[list[str]]:
    """The first lines that fit in ROWS rows (always at least one; a line
    longer than a whole page is cut, and says so)."""
    out, used = [], 0
    for item in pending:
        need = rows(item[1])
        if out and used + need > ROWS:
            break
        out.append(item)
        used += need
    if out and rows(out[0][1]) > ROWS:
        out[0] = [out[0][0], out[0][1][:ROWS * WIDTH - 12] + "… (cut)"]
    return out


def post(name: str, title: str, items: list[tuple[str, str]], nid: str | None = None,
         priority: str = "low", buttons=()) -> list[tuple[str, str]]:
    """Show the unseen `items` ((id, line), oldest first). Returns them.
    buttons: (label, command, item id): an extra button, shown while that
    line is on the card (e.g. the morning weather line's "↪ Weather to …")."""
    st = _load(name)
    seen = set(st.get("seen", []))
    st.update(pending=[[i, l] for i, l in items if i not in seen], title=title,
              nid=nid or name, priority=priority, buttons=[list(b) for b in buttons])
    _save(name, st)
    render(name)
    return [(i, l) for i, l in st["pending"]]


def seen(name: str) -> set[str]:
    """The ids of the lines the owner already hid."""
    return set(_load(name).get("seen", []))


def render(name: str) -> None:
    st = _load(name)
    pending, nid = st.get("pending", []), st.get("nid", name)
    if not pending:
        android.remove(nid)
        return
    shown = page(pending)
    left = len(pending) - len(shown)
    end = f"— end · {left} more after ✓ —" if left else "— end —"
    ids = ",".join(i for i, _ in shown)
    cmd = f"{shlex.quote(str(ROOT / 'bin' / 'automations'))} seen {shlex.quote(name)} {shlex.quote(ids)}"
    android.notify(f"{st.get('title', name)} ({len(pending)})",
                   "\n".join(dg.LTR + l for _, l in shown) + f"\n{dg.LTR}{end}",
                   notif_id=nid, priority=st.get("priority", "low"),
                   buttons=[("✓ Hide these lines", cmd)]       # only these lines; the originals are untouched
                   + [(label, extra) for label, extra, item in st.get("buttons", []) if item in ids.split(",")])


def mark_seen(name: str, ids: list[str]) -> None:
    """The owner saw these lines: drop them, show the next page (or nothing)."""
    st = _load(name)
    gone = set(ids)
    st["seen"] = (st.get("seen", []) + [i for i in ids if i not in st.get("seen", [])])[-3000:]
    st["pending"] = [p for p in st.get("pending", []) if p[0] not in gone]
    _save(name, st)
    render(name)
