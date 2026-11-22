"""Is it already in the owner's calendars? Things to schedule are only suggested,
never added by themselves, and first looked for in each calendar that counts
(it may be in one and missing from another).

Read-only: the phone's calendar provider, through adb. Which calendars count
is private.toml [calendars] ({label: calendar id}, ids from the provider).
Nothing here ever writes to a calendar.
"""

from __future__ import annotations

import re
import tomllib
from datetime import datetime, timedelta

from . import android
from . import paths

WINDOW = timedelta(minutes=20)          # an event starting this close to the time counts
STOP = {"with", "and", "the", "for", "session", "meeting", "call", "today", "tomorrow", "tonight",
        "invite", "invitation", "calendar", "add", "event", "של", "עם"}


def _owner_names() -> set[str]:
    """The owner's own names (private.toml [people]): they're in the titles of their own events,
    so they tell nothing about which event it is."""
    try:
        people = tomllib.loads(paths.PRIVATE.read_text(encoding="utf-8")).get("people", {})
    except (AttributeError, OSError, tomllib.TOMLDecodeError):
        return set()
    return {w for name in people for w in re.findall(r"[^\W_]+", name.lower())}


STOP |= _owner_names()


def words(title: str) -> set[str]:
    """Telling words of a title: 'Dentist check-up with Dr Levi (#71869)'
    -> {'dentist', 'check', 'levi', '71869'}."""
    out = set()
    for w in re.findall(r"[^\W_]+", title.lower()):
        if (len(w) >= 4 or w.isdigit() and len(w) >= 3) and w not in STOP:
            out.add(w)
    return out


def same(title: str, other: str) -> bool:
    a, b = words(title), words(other)
    nums_a, nums_b = {w for w in a if w.isdigit()}, {w for w in b if w.isdigit()}
    if nums_a and nums_b:                       # booking numbers decide: #71869 isn't #71870
        return bool(nums_a & nums_b)
    return bool(a & b)


def events(begin: datetime, end: datetime) -> list[dict] | None:
    """[{title, begin (datetime), calendar_id}] in a window; None if the phone can't be read."""
    b, e = int(begin.timestamp() * 1000), int(end.timestamp() * 1000)
    out = android.adb_output(f"content query --uri content://com.android.calendar/instances/when/{b}/{e} "
                             "--projection title:begin:calendar_id", timeout=15)
    if out is None:
        return None
    found = []
    for line in out.splitlines():
        m = re.search(r"title=(.*), begin=(\d+), calendar_id=(\d+)\s*$", line)
        if m:
            found.append({"title": m[1], "begin": datetime.fromtimestamp(int(m[2]) / 1000),
                          "calendar_id": int(m[3])})
    return found


def where(start: datetime, title: str, calendars: dict[str, int]) -> dict[str, bool] | None:
    """{label: is it there} for each of `calendars`; None if the phone can't be read."""
    found = events(start - WINDOW, start + WINDOW + timedelta(hours=1))
    if found is None:
        return None
    hits = {e["calendar_id"] for e in found if abs(e["begin"] - start) <= WINDOW and same(title, e["title"])}
    return {label: cid in hits for label, cid in calendars.items()}


def note(found: dict[str, bool] | None) -> str:
    """'In Family, not in Personal', 'Not in your calendars yet', ''."""
    if not found:
        return ""
    have = [k for k, v in found.items() if v]
    missing = [k for k, v in found.items() if not v]
    if not missing:
        return f"Already in {' and '.join(have)}"
    if not have:
        return "Not in your calendars yet"
    return f"In {', '.join(have)}; not in {', '.join(missing)}"
