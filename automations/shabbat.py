"""When Shabbat ends: tzeit hakochavim, the sun 8.5° below the horizon (the
common measure in Israel; it matched the owner's calendar to the minute).
Worked out on the phone (solar position, no network), so it follows the
seasons: about 17:15 in winter, about 20:05 in summer.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

DEPRESSION = 8.5


def _altitude(t: datetime, lat: float, lon: float) -> float:
    """The sun's altitude in degrees at UTC time `t` (accurate to well under a minute of time)."""
    n = t.timestamp() / 86400 + 2440587.5 - 2451545.0
    L = (280.460 + 0.9856474 * n) % 360
    g = math.radians((357.528 + 0.9856003 * n) % 360)
    lam = math.radians(L + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))
    eps = math.radians(23.439 - 0.0000004 * n)
    ra = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))
    dec = math.asin(math.sin(eps) * math.sin(lam))
    ha = math.radians((280.46061837 + 360.98564736629 * n) % 360 + lon) - ra
    la = math.radians(lat)
    return math.degrees(math.asin(math.sin(la) * math.sin(dec) + math.cos(la) * math.cos(dec) * math.cos(ha)))


def end(day: date, lat: float, lon: float, depression: float = DEPRESSION) -> datetime:
    """When the sun reaches `depression` below the horizon on the evening of
    `day`: local time (the phone's zone), cut to the minute (17:18:40 -> 17:18)."""
    t = datetime(day.year, day.month, day.day, 12, 0, tzinfo=timezone.utc) - timedelta(hours=lon / 15)  # local noon
    lo, hi = t, t + timedelta(hours=12)
    while hi - lo > timedelta(seconds=20):        # altitude only falls between noon and midnight
        mid = lo + (hi - lo) / 2
        lo, hi = (mid, hi) if _altitude(mid, lat, lon) > -depression else (lo, mid)
    return hi.astimezone().replace(second=0, microsecond=0, tzinfo=None)   # to the minute, as calendars print it


def candle_lighting(day: date, lat: float, lon: float) -> datetime:
    """Friday's candle lighting: sunset less 40 minutes."""
    return end(day, lat, lon, 0.833) - timedelta(minutes=40)


def active(now: datetime, lat: float, lon: float) -> bool:
    """From Friday candle lighting until Shabbat is out."""
    if now.weekday() == 4:
        return now >= candle_lighting(now.date(), lat, lon)
    return now.weekday() == 5 and now < end(now.date(), lat, lon)


FRIDAY_FROM = 15                    # o'clock: a reminder later on Friday waits until Shabbat is out


def after(t: datetime, lat: float, lon: float) -> datetime:
    """`t`, or just after Shabbat ends if `t` falls on it (Friday from 15:00 counts, as a reminder
    then would be too close to Shabbat)."""
    wd = t.weekday()                                    # Mon 0 … Fri 4, Sat 5
    if wd == 4 and t.hour >= FRIDAY_FROM:
        sat = t.date() + timedelta(days=1)
    elif wd == 5:
        sat = t.date()
    else:
        return t
    out = end(sat, lat, lon) + timedelta(minutes=10)
    return max(t, out) if wd == 5 else out
