"""Quiet times: when the phone holds back. During one, urgent alerts aren't sent,
reminders wait until it's over, and the calendar copy doesn't run; a summary can come
when it ends (the after_quiet action).

Set in private.toml [quiet]. The one kind for now:
    shabbat = true      # from Friday candle lighting until Shabbat is out, at [location]
Without [quiet] there are no quiet times.
"""

from __future__ import annotations

from datetime import date, datetime

from . import location, owner, shabbat


def _shabbat_place() -> tuple[float, float] | None:
    return location.get() if owner.setting("quiet", "shabbat", False) else None


def active(now: datetime | None = None) -> bool:
    """Is it a quiet time now?"""
    place = _shabbat_place()
    return bool(place) and shabbat.active(now or datetime.now(), *place)


def after(t: datetime) -> datetime:
    """`t`, or just after the quiet time it falls in."""
    place = _shabbat_place()
    return shabbat.after(t, *place) if place else t


def ends(day: date, lat: float | None = None, lon: float | None = None,
         depression: float = shabbat.DEPRESSION) -> datetime | None:
    """When a quiet time ends on `day`, if one does (Shabbat: on Saturdays). lat/lon: a place
    of the rule's own instead of [location]."""
    if not owner.setting("quiet", "shabbat", False) or day.weekday() != 5:
        return None
    place = (lat, lon) if lat is not None and lon is not None else location.get()
    return shabbat.end(day, *place, depression) if place else None
