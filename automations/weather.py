"""Weather: one line in the morning summary, a card of its own only when the
day is out of the ordinary (rain likely, much hotter or colder than yesterday,
heat, a cold night, storm or snow), and a card during the day only when a new
one of those turns up (e.g. rain now likely this afternoon).

Forecast: Open-Meteo (free, no key), for the place the rule gives (a town
centre is enough, not the owner's exact location). Nothing else is sent. What was already shown today is kept
in var/weather.json, so nothing is said twice.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from .paths import VAR

STATE = VAR / "weather.json"
API = "https://api.open-meteo.com/v1/forecast"

ICONS = [((0,), "☀️"), ((1, 2), "🌤"), ((3,), "☁️"), ((45, 48), "🌫"), (tuple(range(51, 68)), "🌧"),
         (tuple(range(71, 78)) + (85, 86), "🌨"), ((80, 81, 82), "🌦"), ((95, 96, 99), "⛈")]


class WeatherError(RuntimeError):
    pass


def fetch(lat: float, lon: float, timeout: float = 20) -> dict:
    """Yesterday, today and tomorrow (daily), plus today's hourly rain chance."""
    q = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "timezone": "auto", "past_days": 1, "forecast_days": 2,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum,weather_code",
        "hourly": "precipitation_probability"})
    try:
        req = urllib.request.Request(f"{API}?{q}", headers={"User-Agent": "phone-automations"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except (OSError, ValueError) as e:
        raise WeatherError(str(e)) from e


def days(data: dict) -> dict[str, dict]:
    """{"YYYY-MM-DD": {max, min, rain_pct, rain_mm, code}}."""
    d = data.get("daily") or {}
    out = {}
    for i, day in enumerate(d.get("time") or []):
        def v(k, default=0.0):
            x = (d.get(k) or [None] * (i + 1))[i]
            return default if x is None else x
        out[day] = {"max": v("temperature_2m_max"), "min": v("temperature_2m_min"),
                    "rain_pct": v("precipitation_probability_max"), "rain_mm": v("precipitation_sum"),
                    "code": int(v("weather_code"))}
    return out


def rain_hours(data: dict, day: str, from_hour: int = 0, pct: int = 50) -> list[int]:
    h = data.get("hourly") or {}
    return [int(t[11:13]) for t, p in zip(h.get("time") or [], h.get("precipitation_probability") or [])
            if t.startswith(day) and int(t[11:13]) >= from_hour and (p or 0) >= pct]


def icon(code: int) -> str:
    return next((i for codes, i in ICONS if code in codes), "🌡")


def line(today: dict, yesterday: dict | None = None) -> str:
    """'🌤 22°/14°, 3° warmer than yesterday, rain 30%'."""
    out = f"{icon(today['code'])} {today['max']:.0f}°/{today['min']:.0f}°"
    if yesterday:
        diff = round(today["max"] - yesterday["max"])
        if abs(diff) >= 2:
            out += f", {abs(diff)}° {'warmer' if diff > 0 else 'colder'} than yesterday"
    if today["rain_pct"] >= 20:
        out += f", rain {today['rain_pct']:.0f}%"
    return out


def reasons(today: dict, yesterday: dict | None, hours: list[int], swing: float = 5,
            hot: float = 32, cold_night: float = 4, rain_pct: float = 50) -> dict[str, str]:
    """What makes the day out of the ordinary: {key: short reason}. Empty = an ordinary day."""
    out = {}
    if today["rain_pct"] >= rain_pct or today["rain_mm"] >= 1 or hours:
        when = f" from {hours[0]:02d}:00" if hours else ""
        out["rain"] = f"Rain likely{when} ({today['rain_pct']:.0f}%" + (f", ~{today['rain_mm']:.0f} mm)" if today["rain_mm"] >= 1 else ")")
    if yesterday:
        d = today["max"] - yesterday["max"]
        if d >= swing:
            out["warmer"] = f"Much warmer: {today['max']:.0f}° (yesterday {yesterday['max']:.0f}°)"
        elif d <= -swing:
            out["colder"] = f"Much colder: {today['max']:.0f}° (yesterday {yesterday['max']:.0f}°)"
    if today["max"] >= hot:
        out["hot"] = f"Hot: up to {today['max']:.0f}°"
    if today["min"] <= cold_night:
        out["cold"] = f"Cold night: down to {today['min']:.0f}°"
    if today["code"] in (95, 96, 99):
        out["storm"] = "Thunderstorms forecast"
    if today["code"] in tuple(range(71, 78)) + (85, 86):
        out["snow"] = "Snow forecast"
    return out


def shown(day: str) -> set[str]:
    try:
        st = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return set(st.get("shown", [])) if st.get("date") == day else set()


def mark_shown(day: str, keys) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"date": day, "shown": sorted(shown(day) | set(keys))}), encoding="utf-8")


def check(lat: float, lon: float, now: datetime | None = None, morning: bool = False, **limits) -> dict:
    """{line, new: {key: reason}, all: {key: reason}}. `new` leaves out what was
    already shown today. In the morning rain counts for the whole day; later,
    only for the hours still ahead."""
    now = now or datetime.now()
    data = fetch(lat, lon)
    ds = days(data)
    today_s = now.date().isoformat()
    yday_s = date.fromordinal(now.date().toordinal() - 1).isoformat()
    today = ds.get(today_s)
    if not today:
        raise WeatherError(f"no forecast for {today_s}")
    hours = rain_hours(data, today_s, 0 if morning else now.hour)
    found = reasons(today, ds.get(yday_s), hours, **limits)
    if not morning and "rain" in found and not hours:
        del found["rain"]                         # the rain was earlier today; nothing ahead
    done = shown(today_s)                         # 05:00 and 06:10: one card, not two
    return {"line": line(today, ds.get(yday_s)), "new": {k: v for k, v in found.items() if k not in done},
            "all": found, "day": today_s}
