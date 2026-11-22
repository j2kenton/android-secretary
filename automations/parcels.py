"""Parcels waiting at the post office.

Israel Post texts "להזכירך-משלוח RS…IL עדיין ממתין לך ביחידת הדואר- <branch>-
כתובת <address>." Usually that one reminder is all (in the owner's texts since
2024: one per parcel, once two, never a last warning). Israel Post's postal
guide: a parcel is kept 14 working days from the first notice, then goes back
to the sender; the second notice comes about day 5. So from a reminder there
are about 9 working days left (from a first notice, 14). A pickup point (a
shop, "(בית עסק)") may keep it as little as 3 working days, so that's what is
assumed there. Working days: Sunday to Thursday.

The card: the branch and the estimated last day, "📦 Post office app" (to book
an appointment, as the owner does) and "📅 Add last day" (opens the calendar's
new-event screen; nothing is added by itself). It comes back two working days
before the last day.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from .paths import VAR

ROOT = Path(__file__).resolve().parent.parent
STATE = VAR / "parcels.json"
SENDERS = {"israel_post", "israelpost", "israel post", "דואר ישראל"}
APP = "com.israelpost.israelpost"

WAITING = re.compile(r"ממתין לך ב(?:יחידת|סניף)|הגיע ל(?:יחידת|סניף)")
PARCEL_ID = re.compile(r"משלוח\s*-?\s*([A-Z]{2}\w{6,})")
PLACE = re.compile(r"ביחידת הדואר\s*-\s*(.+?)\s*-\s*כתובת\s+(.+?)\s*\.")
KEEP_DAYS, AFTER_REMINDER, PICKUP_POINT_DAYS = 14, 9, 3


def _clean(s: str) -> str:
    return re.sub("[‎‏‪-‮⁦-⁩]", "", s or "").strip()


def parse(title: str, body: str) -> dict | None:
    """A "parcel waiting" text -> {id, branch, address, pickup_point, reminder}; else None."""
    if _clean(title).lower() not in SENDERS or not WAITING.search(body or ""):
        return None
    pid, place = PARCEL_ID.search(body), PLACE.search(body)
    branch = place[1].strip() if place else "the post office"
    return {"id": pid[1] if pid else "", "branch": re.sub(r"\s*\(בית עסק\)", "", branch),
            "address": place[2].strip() if place else "", "pickup_point": "בית עסק" in branch,
            "reminder": "להזכירך" in body}


def add_working_days(d: date, n: int) -> date:
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() not in (4, 5):        # Fri, Sat
            n -= 1
    return d


def less_working_days(d: date, n: int) -> date:
    while n > 0:
        d -= timedelta(days=1)
        if d.weekday() not in (4, 5):
            n -= 1
    return d


def last_day(p: dict, first: date) -> date:
    if p["pickup_point"]:
        return add_working_days(first, PICKUP_POINT_DAYS)
    return add_working_days(first, AFTER_REMINDER if p["reminder"] else KEEP_DAYS)


def _load() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def seen(p: dict, today: date | None = None) -> dict:
    """Remember the parcel; its last day is set the first time it's seen
    (a later reminder for the same parcel doesn't push it back)."""
    today = today or date.today()
    st = {k: v for k, v in _load().items() if v.get("last", "") >= (today - timedelta(days=30)).isoformat()}
    key = p["id"] or f"{p['branch']}|{today.isoformat()}"
    rec = st.get(key) or {"first": today.isoformat(), "last": last_day(p, today).isoformat()}
    rec.update(branch=p["branch"], address=p["address"], id=p["id"])
    st[key] = rec
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    return rec


def come_back(last: date, now: datetime | None = None) -> datetime | None:
    """06:10 two working days before the last day; None if that's already past."""
    now = now or datetime.now()
    t = datetime.combine(less_working_days(last, 2), time(6, 10))
    return t if t > now else None
