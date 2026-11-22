"""Pull structured facts out of message text.

appointment(text) recognises booked-appointment texts (health fund clinics and their
clinics, Israel Post, and similar Hebrew/English formats) and returns their
title, start time and location. It deliberately ignores look-alikes: blood
drives and opening hours ("בין השעות 12:00-20:00"), promos that say "בתור",
and anything without both a date and a time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

# Phrases that only appear when an appointment was actually booked for you.
BOOKED = re.compile(
    r"קבענו לך תור|התור שקבעת|תור שתואם|זומן תור|נקבע תור|"
    r"your appointment|appointment (?:is )?(?:scheduled|booked|confirmed)", re.I)
CANCELLED = re.compile(r"מבוטל|ביטלנו|בוטל|cancell?ed", re.I)

DATE = re.compile(r"(?<!\d)(\d{1,2})[/.](\d{1,2})[/.](\d{4}|\d{2})(?!\d)")
# A clock time that isn't part of a range like 12:00-20:00.
TIME = re.compile(r"(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])(?!\s*[-–]\s*\d)")

DOCTOR = re.compile(r"ד['׳]?ר['׳]?\s+([^\n/:,.()0-9📆🕜📍]+?)\s*(\([^)]{2,30}\))?\s*(?=[:\n/,.]|לתאריך|ב\s?-?\d|📆|$)")
BRANCH = re.compile(r"סניף\s+([^\n/,.0-9]+?)\s*(?=\n|/|,|\.|ב-|בתאריך|$)")
SERVICE = re.compile(r"(?:תור|התור שנקבע)\s+ל\s?([^\n/,.0-9]{3,30}?)\s*(?=,|\n|/|בתאריך|$)")
ADDRESS = re.compile(r"בכתובת:?\s*([^\n/]+?)\s*(?=\.\s|\.$|\n|/|$)")


@dataclass
class Appointment:
    title: str
    start: datetime
    location: str
    cancelled: bool


def _when(text: str) -> datetime | None:
    d, t = DATE.search(text), TIME.search(text)
    if not d or not t:
        return None
    day, month, year = int(d[1]), int(d[2]), int(d[3])
    year += 2000 if year < 100 else 0
    try:
        return datetime(year, month, day, int(t[1]), int(t[2]))
    except ValueError:
        return None


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip(" -:")


AMOUNT = re.compile(r"(?:בגובה|בסך|of)\s*([\d,]+(?:\.\d+)?\s*(?:USD|EUR|ש\"ח|₪|\$)?)", re.I)
MERCHANT = re.compile(r"בבית העסק\s+(.+?)(?:\.\s|\.$|,|\n|$)")
URL = re.compile(r"https?://\S+|\b(?:bit\.ly|l5k\.me|katzr\.net)/\S+")


def declined_charge(text: str, sender: str = "") -> dict | None:
    """'לא אושרה עסקה בגובה 350.00 USD בבית העסק X' -> {amount, merchant};
    None when it isn't a decline, or the amount/merchant can't be read."""
    if not re.search(r"לא אושרה עסקה|נדחתה|סורבה|charge failed|declined", text, re.I):
        return None
    amount, merchant = AMOUNT.search(text), MERCHANT.search(text)
    if not (amount and merchant):
        return None          # declined, but no clear amount/merchant: callers fall back
    return {"amount": _clean(amount[1]), "merchant": _clean(merchant[1])}


def first_url(text: str) -> str:
    m = URL.search(text)
    if not m:
        return ""
    url = m[0].rstrip(".,)")
    return url if url.startswith("http") else "https://" + url


def appointment_fields(text: str, sender: str = "") -> dict | None:
    """appointment() flattened for message templates: {appt_title, appt_when, appt_place}."""
    a = appointment(text, sender)
    if not a:
        return None
    return {"appt_title": a.title, "appt_when": a.start.strftime("%a %d/%m %H:%M"),
            "appt_place": a.location}


def appointment(text: str, sender: str = "") -> Appointment | None:
    if not BOOKED.search(text):
        return None
    start = _when(text)
    if not start:
        return None
    if m := DOCTOR.search(text):
        title = f"תור לדר' {_clean(m[1])}" + (f" {m[2]}" if m[2] else "")
    elif m := BRANCH.search(text):
        title = f"תור בדואר - סניף {_clean(m[1])}" if "דואר" in text or "Post" in sender \
            else f"תור - סניף {_clean(m[1])}"
    elif m := SERVICE.search(text):
        title = f"תור ל{_clean(m[1])}"
    else:
        title = f"תור - {sender}" if sender else "תור"
    loc = ADDRESS.search(text)
    location = _clean(loc[1]) if loc else ""
    if not location and (m := BRANCH.search(text)):
        location = f"סניף {_clean(m[1])}" + (" דואר ישראל" if "דואר" in text else "")
    return Appointment(title=title, start=start, location=location,
                       cancelled=bool(CANCELLED.search(text)))


# Extractors a rule's action can name with `extract = "..."`: each takes
# (text, sender) and returns a
# dict of extra template fields, or None when the text doesn't fit.
EXTRACTORS = {
    "declined_charge": declined_charge,
    "appointment": appointment_fields,
}
