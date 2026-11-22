"""Copy the owner's events from their Personal calendar to the Family calendar,
exactly the same but with no reminders: the owner needs the reminder, the
family just needs to know.

Finding what's missing is plain code, on the phone's own calendar data (adb,
read only): each Personal event in the next DAYS that has no Family event at
the same time with the same title. Every one is asked about first; only those the
owner says yes to are copied. Left out: all-day events marked Free (such as
blocks of preparation time), titles in
private.toml [calendar_copy] skip (a recurring reminder of their own), declined and
cancelled events. A repeating series that's in Family only partly is asked
about with that said.

Copying goes through Google Calendar (Claude with the Calendar tools), so the
copy can be made with no reminders at all, not even the Family calendar's
default: same title, times, place, description and meeting link; no guests
(a copy must never invite anyone); nothing is ever changed or deleted. Each
event is tried once a day at most, and never in a quiet time (quiet.py).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from .paths import VAR
from . import android
from . import calendars as cal
from . import claude

STATE = VAR / "calendar-copy.json"
DAYS = 90
RETRY_HOURS = 24
FIELDS = "event_id:title:begin:end:allDay:calendar_id:availability:rrule:original_sync_id:_sync_id:selfAttendeeStatus:eventStatus"
ROW = re.compile(r"event_id=\d+, title=(?P<title>.*), begin=(?P<begin>\d+), end=(?P<end>\d+), allDay=(?P<allDay>\d), "
                 r"calendar_id=(?P<cal>\d+), availability=(?P<avail>\d+), rrule=(?P<rrule>.*?), "
                 r"original_sync_id=(?P<orig>.*?), _sync_id=(?P<sync>.*?), selfAttendeeStatus=(?P<self>\d+), "
                 r"eventStatus=(?P<status>\d+)\s*$")
TOOLS = ["ToolSearch", "mcp__claude_ai_Google_Calendar__get_event", "mcp__claude_ai_Google_Calendar__list_events",
         "mcp__claude_ai_Google_Calendar__create_event"]

PROMPT = """Step 1: call ToolSearch with query "select:mcp__claude_ai_Google_Calendar__get_event,mcp__claude_ai_Google_Calendar__list_events,mcp__claude_ai_Google_Calendar__create_event" to load the tools.

The owner keeps a copy of each of their own events in the Family calendar, identical except that the copy has
NO reminders. Copy each event below from calendar "{personal}" to calendar "{family}":
1. get_event(calendarId="{personal}", eventId=<id>).
2. list_events(calendarId="{family}") from 1 hour before to 1 hour after its start. If an event with the same
   title at the same start is already there, do not create it (result "exists").
3. Otherwise create_event with calendarId="{family}" and: summary exactly the same; startTime and endTime
   exactly the same (for an all-day event, allDay=true and the same dates); timeZone the event's own start
   time zone if it has one; location and description exactly the same (if any); googleMeetUrl = its
   conferenceUrl (if any); visibility, availability and colorId as the original (if set); recurrenceData =
   its recurrence (only if the original is a repeating series); useDefaultReminders=false and NO
   overrideReminders, so the copy has no reminders at all; NO attendees; notificationLevel="NONE".
Never change or delete any event, on any calendar. Nothing else.

Events (id | title | start):
{events}

Finally output one JSON object per event, one per line, nothing else:
{{"id": "<id>", "result": "created" | "exists" | "failed", "title": "<title>", "start": "<start as YYYY-MM-DD HH:MM>"}}"""


def norm(title: str) -> str:
    return re.sub(r"\s+", " ", title or "").strip().lower()


def matches(a: str, b: str) -> bool:
    """Same event title: equal, or one cut short ("J: Dentist" for "J: Dentist - Dr Levi - Ramat Aviv")."""
    x, y = norm(a), norm(b)
    return bool(x and y) and (x == y or x.startswith(y) or y.startswith(x) or cal.same(a, b))


def instances(begin: datetime, end: datetime) -> list[dict] | None:
    b, e = int(begin.timestamp() * 1000), int(end.timestamp() * 1000)
    out = android.adb_output(f"content query --uri content://com.android.calendar/instances/when/{b}/{e} "
                             f"--projection {FIELDS}", timeout=20)
    if out is None:
        return None
    rows = []
    for line in out.splitlines():
        m = ROW.search(line)
        if not m:
            continue
        rows.append({"title": m["title"], "begin": int(m["begin"]), "end": int(m["end"]),
                     "all_day": m["allDay"] == "1", "calendar_id": int(m["cal"]), "free": m["avail"] == "1",
                     "rrule": None if m["rrule"] == "NULL" else m["rrule"],
                     "sync_id": m["sync"], "original": None if m["orig"] == "NULL" else m["orig"],
                     "declined": m["self"] == "2", "cancelled": m["status"] == "2"})
    return rows


def missing(personal: int, family: int, skip=(), days: int = DAYS, now: datetime | None = None,
            rows: list[dict] | None = None) -> tuple[list[dict], list[dict]] | None:
    """(to copy, doubts): Personal events with no copy in Family. Each item:
    {id, title, start, recurring}. None if the phone can't be read."""
    now = now or datetime.now()
    rows = rows if rows is not None else instances(now, now + timedelta(days=days))
    if rows is None:
        return None
    fam = [r for r in rows if r["calendar_id"] == family and not r["cancelled"]]
    skips = [re.compile(s, re.I) for s in skip]

    def has_copy(r):
        return any(abs(f["begin"] - r["begin"]) <= 60_000 and matches(r["title"], f["title"]) for f in fam)

    series: dict[str, list[bool]] = {}
    first: dict[str, dict] = {}
    for r in rows:
        if r["calendar_id"] != personal or r["cancelled"] or r["declined"] or r["begin"] < now.timestamp() * 1000:
            continue
        if r["all_day"] and r["free"]:
            continue                                   # planning blocks
        if any(s.search(r["title"]) for s in skips):
            continue
        key = r["original"] or r["sync_id"] if (r["rrule"] or r["original"]) else r["sync_id"]
        series.setdefault(key, []).append(has_copy(r))
        first.setdefault(key, r)
    copy, doubt = [], []
    for key, found in series.items():
        if all(found):
            continue
        r = first[key]
        when = datetime.fromtimestamp(r["begin"] / 1000)
        item = {"id": key, "title": r["title"].strip(), "start": when.strftime("%Y-%m-%d") if r["all_day"] else when.strftime("%Y-%m-%d %H:%M"),
                "recurring": bool(r["rrule"] or r["original"])}
        if item["recurring"] and any(found):
            doubt.append({**item, "why": f"repeating: {found.count(True)} of {len(found)} already in Family"})
        else:
            copy.append(item)
    return copy, doubt


def _load() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def due(items: list[dict], now: datetime | None = None) -> list[dict]:
    """Those not tried in the last RETRY_HOURS (the phone's copy of Google lags a little)."""
    now = now or datetime.now()
    tried = _load().get("tried", {})
    cut = (now - timedelta(hours=RETRY_HOURS)).isoformat()
    return [i for i in items if tried.get(i["id"], "") < cut]


def copy(items: list[dict], personal: str, family: str, ask=None) -> list[dict]:
    """Copy through Google Calendar. Returns [{id, result, title, start}]."""
    if not items:
        return []
    st = _load()
    st["tried"] = {**st.get("tried", {}), **{i["id"]: datetime.now().isoformat(timespec="minutes") for i in items}}
    _save(st)
    events = "\n".join(f"{i['id']} | {i['title']} | {i['start']}" for i in items)
    ans = (ask or claude.ask)(PROMPT.format(personal=personal, family=family, events=events), TOOLS,
                              model="sonnet", timeout=300, connect_wait=10,
                              require_tool="mcp__claude_ai_Google_Calendar__get_event")
    known = {i["id"]: i for i in items}
    out = []
    for line in ans.text.splitlines():
        line = line.strip().strip(",")
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("id") in known and d.get("result") in ("created", "exists", "failed"):
            out.append({**known[d["id"]], "result": d["result"]})
    seen = {d["id"] for d in out}
    out += [{**i, "result": "failed"} for i in items if i["id"] not in seen]
    st = _load()
    st["log"] = (st.get("log", []) + [{**d, "at": datetime.now().isoformat(timespec="minutes")} for d in out])[-200:]
    _save(st)
    return out


def answer(event_id: str, yes: bool) -> None:
    """The owner's ✅ / ✋ on a doubt's card."""
    st = _load()
    key = "approved" if yes else "never"
    st[key] = sorted(set(st.get(key, [])) | {event_id})
    if yes:
        st.get("tried", {}).pop(event_id, None)
    _save(st)


def never() -> set[str]:
    return set(_load().get("never", []))


def approved() -> set[str]:
    return set(_load().get("approved", []))


def to_ask(doubts: list[dict], days: int = 7) -> list[dict]:
    """Doubts not asked about in the last `days` (marks them asked)."""
    st = _load()
    asked = st.get("asked", {})
    cut = (datetime.now() - timedelta(days=days)).isoformat()
    out = [d for d in doubts if asked.get(d["id"], "") < cut]
    if out:
        st["asked"] = {**asked, **{d["id"]: datetime.now().isoformat(timespec="minutes") for d in out}}
        _save(st)
    return out
