"""Cards that come back later: tomorrow's timetable change matters tomorrow, not
now; tomorrow's test, this evening, in time to remind the child.

A card offered with reminders is saved here (title, text, its own buttons).
Its ⏰ buttons ("In an hour", "At 13:15", "Remind me tonight", "Remind me
tomorrow", "Sun eve": which ones depends on when the item is about, see
buttons()) remove it and set when it comes back; the reminders rule posts it
again then, with all its buttons. "Tonight" is 19:30 and "tomorrow" is 06:10
(with the morning summary). Nothing lands in a quiet time (quiet.py; Shabbat: from Friday
afternoon until it's out): it moves to just after it ends.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path

from .paths import VAR
from . import quiet

ROOT = Path(__file__).resolve().parent.parent
DIR = VAR / "reminders"
CARDS = DIR / "cards.json"

EVENING = time(19, 30)
MORNING = time(6, 10)
KEEP_DAYS = 7


def outside_quiet(t: datetime) -> datetime:
    """`t`, or just after the quiet time (e.g. Shabbat) it falls in."""
    return quiet.after(t)


TIME = re.compile(r"(?<![\d:/.])([01]?\d|2[0-3])[:.]([0-5]\d)(?![\d/])")
NOT_TODAY = re.compile(r"\btomorrow\b|מחר|next week|שבוע הבא|\b\d{1,2}/\d{1,2}\b", re.I)
BEFORE = timedelta(minutes=15)      # "⏰ At …": this long before the time the item is about
LATER = timedelta(hours=1)          # "⏰ In an hour"


def deadline_in(text: str, now: datetime) -> datetime | None:
    """The time today an item is about ("pick up the kids by 12:30" -> 12:30
    today), from times written in it; None if it names none, or another day.
    The latest time counts (a reminder after it is useless)."""
    if not text or NOT_TODAY.search(text):
        return None
    times = [datetime.combine(now.date(), time(int(h), int(m))) for h, m in TIME.findall(text)]
    return max(times) if times else None


def target(which: str, now: datetime, day: date | None = None) -> datetime:
    """When to come back. With `day` (what the item is about): "evening" = the
    evening before it, "morning" = that morning. Without: "evening" = tonight,
    "morning" = tomorrow morning. "before" = the evening before `day`; "hour" =
    an hour from now; "HH:MM" = today then. Never earlier than `now`."""
    if which == "evening" or (which == "before" and day):
        t = datetime.combine((day - timedelta(days=1)) if day else now.date(), EVENING)
    elif which == "morning":
        t = datetime.combine(day if day else now.date() + timedelta(days=1), MORNING)
    elif which == "hour":
        t = now + LATER
    elif re.fullmatch(r"\d\d:\d\d", which):          # "⏰ At 12:15": today at that time
        t = datetime.combine(now.date(), time(*map(int, which.split(":"))))
    else:
        return now
    return outside_quiet(max(t, now))


def buttons(nid: str, now: datetime, day: date | None = None,
            which: tuple[str, ...] = ("evening", "morning"), deadline: datetime | None = None) -> list[tuple[str, str]]:
    """The reminder buttons that make sense now (at most two), for an item about
    `day` (None: not tied to a day):
    - a time today in it (`deadline`): "⏰ At …" shortly before it, if that's still ahead,
      and never a reminder after it (an item due at noon gets no "remind me tonight");
    - about today: later today, "⏰ In an hour" (unless "⏰ At …" comes sooner, or it
      would be evening by then); "tonight" only when nothing earlier fits;
    - about tomorrow: tonight, and tomorrow morning;
    - further off: tonight (part of it, like signing up, may be sooner), and the
      evening before it, not tomorrow;
    - not tied to a day: tonight (and tomorrow, if asked for).
    Tonight is only offered before the evening, and not on Saturday."""
    import shlex
    cmd = f"{shlex.quote(str(ROOT / 'bin' / 'automations'))} remind {shlex.quote(nid)}"
    out = []
    evening = datetime.combine(now.date(), EVENING)
    at = None
    if deadline is not None:
        at = deadline - BEFORE
        if at - now >= timedelta(minutes=10) and outside_quiet(at) == at:
            out.append((f"⏰ At {at:%H:%M}", f"{cmd} {at:%H:%M}"))
        else:
            at = None
    if day == now.date():
        later = now + LATER
        if later < evening - timedelta(hours=1) and (at is None or later < at) and outside_quiet(later) == later:
            out.append(("⏰ In an hour", f"{cmd} hour"))
    if deadline is not None and deadline <= evening:
        return out
    if "evening" in which and now.time() < time(18, 30) and now.weekday() != 5 and not (day == now.date() and out):
        out.append(("⏰ Remind me tonight", f"{cmd} evening"))
    if "morning" in which and day is not None and day > now.date() + timedelta(days=1):
        t = target("before", now, day)
        out.append((f"⏰ {t:%a} eve", f"{cmd} before"))
    elif "morning" in which and (day is None or day > now.date()):
        out.append(("⏰ Remind me tomorrow", f"{cmd} morning"))
    return out[:2]


def describe(t: datetime, now: datetime | None = None) -> str:
    """'tonight at 19:30', 'tomorrow at 06:10', 'Sat at 17:25'."""
    now = now or datetime.now()
    if t.date() == now.date():
        day = "tonight" if t.hour >= 17 else "today"
    elif t.date() == now.date() + timedelta(days=1):
        day = "tomorrow"
    else:
        day = t.strftime("%a")
    return f"{day} at {t.strftime('%H:%M')}"


def _load() -> dict:
    try:
        return json.loads(CARDS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(cards: dict) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = CARDS.with_suffix(".tmp")
    tmp.write_text(json.dumps(cards, ensure_ascii=False))
    tmp.replace(CARDS)


def offer(nid: str, title: str, text: str, card_buttons: list[tuple[str, str]],
          priority: str = "high", day: date | None = None, now: datetime | None = None,
          which: tuple[str, ...] = ("evening", "morning")) -> None:
    """Remember a card that's up with reminder buttons, so it can come back."""
    now = now or datetime.now()
    cards = {k: v for k, v in _load().items()
             if v.get("due") or v.get("saved", "") >= (now - timedelta(days=KEEP_DAYS)).isoformat()}
    cards[nid] = {"title": title, "text": text, "buttons": [list(b) for b in card_buttons],
                  "priority": priority, "day": day.isoformat() if day else None,
                  "saved": now.isoformat(timespec="minutes"), "due": None, "which": list(which)}
    _save(cards)


def snooze(nid: str, which: str, now: datetime | None = None) -> datetime | None:
    """A reminder button was tapped: the card comes back at its time. None if
    the card isn't known (then nothing is set)."""
    now = now or datetime.now()
    cards = _load()
    c = cards.get(nid)
    if not c:
        return None
    day = date.fromisoformat(c["day"]) if which == "before" and c.get("day") else None
    due = target(which, now, day)
    c["due"] = due.isoformat(timespec="minutes")
    _save(cards)
    return due


def schedule(nid: str, due: datetime) -> bool:
    """Bring an offered card back at `due` (moved past a quiet time), without a tap."""
    cards = _load()
    if nid not in cards:
        return False
    cards[nid]["due"] = outside_quiet(due).isoformat(timespec="minutes")
    _save(cards)
    return True


def pop_due(now: datetime | None = None) -> list[dict]:
    """Cards whose time has come ({nid, title, text, buttons, priority, day}), each once."""
    now = now or datetime.now()
    cards = _load()
    due = [{"nid": k, **v} for k, v in cards.items() if v.get("due") and v["due"] <= now.isoformat()]
    if due:
        for c in due:
            cards.pop(c["nid"], None)
        _save(cards)
    return due
