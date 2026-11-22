"""Event sources. Each one polls something on the phone and yields new events.

An event is a plain dict with at least `source` and `id`. Sources remember
what they've already seen (in the shared state dict), and the first poll
after a fresh start only sets a baseline, so old messages never fire rules.

To add a source: subclass Source, give it a `name`, implement `poll()`,
and register it in SOURCES.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from . import android


class Source:
    name = ""

    def __init__(self, state: dict):
        self.state = state.setdefault(self.name, {})

    def poll(self) -> list[dict]:
        raise NotImplementedError


class SmsSource(Source):
    """New inbox SMS. Fields: id, number, contact, body, received."""
    name = "sms"

    def poll(self) -> list[dict]:
        msgs = android.sms_inbox(limit=20)
        last = self.state.get("last_id")
        newest = max((int(m["_id"]) for m in msgs), default=last or 0)
        if last is None:                      # first run: baseline only
            self.state["last_id"] = newest
            return []
        fresh = sorted((m for m in msgs if int(m["_id"]) > last), key=lambda m: int(m["_id"]))
        self.state["last_id"] = max(newest, last)
        return [{
            "source": self.name,
            "id": f"sms:{m['_id']}",
            "number": m.get("number", ""),
            "contact": m.get("sender") or "",
            "body": m.get("body", ""),
            "received": m.get("received", ""),
        } for m in fresh]


class NotificationSource(Source):
    """Newly posted notifications from any app (WhatsApp, Family Link, ...).

    Fields: id, key (Android's notification key), package, title, body, when. Ongoing notifications and media players are skipped.
    """
    name = "notification"

    def poll(self) -> list[dict]:
        current = android.notifications()
        seen = set(self.state.get("seen", []))
        keys = {f"{n.get('key')}|{n.get('when')}": n for n in current if not n.get("ongoing")}
        first = "seen" not in self.state
        self.state["seen"] = list(keys)[-500:]
        if first:
            return []
        fresh = {k: n for k, n in keys.items() if k not in seen}
        if fresh:                                   # never media players: they aren't messages
            media = android.media_keys()
            fresh = {k: n for k, n in fresh.items() if n.get("key") not in media}
        return [{
            "source": self.name,
            "id": f"notification:{k}",
            "key": n.get("key", ""),
            "package": n.get("packageName", ""),
            "title": n.get("title", ""),
            "body": n.get("content", ""),
            "when": n.get("when", ""),
        } for k, n in fresh.items()]


class ClockSource(Source):
    """One event per minute of local time, for scheduled rules (`at`, `days`).

    Fields: id, time ("05:00"), weekday ("fri"), date. Minutes missed while the
    daemon was stopped are caught up, up to `CATCH_UP` back, so a restart at
    05:03 still runs a 05:00 rule; anything older is skipped.
    """
    name = "clock"
    CATCH_UP = timedelta(minutes=30)

    def __init__(self, state: dict, now=datetime.now):
        super().__init__(state)
        self.now = now

    def poll(self) -> list[dict]:
        now = self.now().replace(second=0, microsecond=0)
        last = self.state.get("last_minute")
        self.state["last_minute"] = now.isoformat(timespec="minutes")
        if last is None:                      # first run: baseline only
            return []
        minute = max(datetime.fromisoformat(last), now - self.CATCH_UP) + timedelta(minutes=1)
        events = []
        while minute <= now:
            events.append({
                "source": self.name,
                "id": f"clock:{minute.isoformat(timespec='minutes')}",
                "time": minute.strftime("%H:%M"),
                "weekday": minute.strftime("%a").lower(),
                "date": minute.strftime("%Y-%m-%d"),
            })
            minute += timedelta(minutes=1)
        return events


SOURCES: dict[str, type[Source]] = {s.name: s for s in (SmsSource, NotificationSource, ClockSource)}
