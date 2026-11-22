"""Actions a rule can run. Each takes the triggering event plus the action's
own options from rules.toml.

To add an action: write a function `(event, **opts)` and register it in ACTIONS.
Text options may use {placeholders} from the event, e.g. "{contact}: {body}".
Actions also receive `_state`, a dict of their own that persists across
restarts (e.g. to remember what was already offered); ignore it via **_.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shlex
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from . import owner
from . import paths
from .paths import VAR
from . import android
from . import claude
from . import digest as dg
from . import ensemble
from . import groups as grp
from . import school as sch
from . import secretary
from . import weather as wx
from . import quiet
from . import shabbat
from . import summaries
from . import reminders
from . import extract as ext
from . import urgent as urg
from . import calendars as cal
from . import parcels
from . import ideas
from . import calendar_copy as calcopy
from . import email_list as elist
from . import backup

log = logging.getLogger("automations")


def _fmt(text: str, event: dict) -> str:
    try:
        return text.format(**event)
    except (KeyError, IndexError):
        return text


def _enrich(event: dict, extract_name: str | None) -> tuple[dict, bool]:
    """Add an extractor's fields to the event for templates. Returns (event, matched)."""
    if not extract_name:
        return event, True
    sender = event.get("contact") or event.get("title") or event.get("number", "")
    fields = ext.EXTRACTORS[extract_name](event.get("body", ""), sender)
    return ({**event, **fields}, True) if fields else (event, False)


def _pick(template: str, fallback: str | None, event: dict, matched: bool) -> str:
    """Use the template when the extractor matched and every field resolves, else the fallback."""
    if matched:
        try:
            return template.format(**event)
        except (KeyError, IndexError):
            pass
    return _fmt(fallback or template, event)


def open_app(event, package: str, activity: str | None = None,
             fallback_title: str = "Open app", **_):
    """Open an app. If Android refuses, post a notification that opens it on tap."""
    try:
        android.open_app(package, activity)
    except android.AndroidError as e:
        log.warning("open_app %s failed (%s); posting a tap-to-open notification", package, e)
        tap = f"am start --user 0 -a android.intent.action.MAIN -c android.intent.category.LAUNCHER -p {package}"
        android.notify(_fmt(fallback_title, event), _fmt("{body}", event), on_tap=tap)


def notify(event, title: str, content: str = "{body}", on_tap: str | None = None, **_):
    android.notify(_fmt(title, event), _fmt(content, event), on_tap=on_tap)


def reply_card(event, title: str = "{contact}", content: str = "{body}",
               replies: list[dict] = (), open_chat_label: str = "💬 Reply",
               extract: str | None = None, fallback_title: str | None = None, **_):
    """Notification for an incoming SMS with one-tap replies.

    Tapping the notification (or the open-chat button) opens the conversation.
    Each reply button texts the sender its fixed message - only when tapped -
    then clears the notification and confirms with a toast.

        { type = "reply_card", replies = [{ label = "✅ Done", text = "Done!" }] }

    `extract` names an extractor (extract.EXTRACTORS) whose fields the title
    may use; if it finds nothing, `fallback_title` is used instead.
    """
    event, matched = _enrich(event, extract)
    title = _pick(title, fallback_title, event, matched)
    number = event.get("number", "")
    notif_id = f"reply-{event['id']}"
    buttons = []
    for reply in list(replies)[:2]:
        text = _fmt(reply["text"], event)
        if event.get("test") or not number:
            command = android.cmd_toast(f"Test only: would text {text!r}")
        else:
            command = android.cmd_send_sms_checked(number, text, notif_id)   # "Sent" only once it's in Sent
        buttons.append((reply["label"], command))
    open_chat = android.cmd_open_sms_thread(number) if number else None
    if open_chat:
        buttons.append((open_chat_label, open_chat))
    android.notify(title, _fmt(content, event), notif_id=notif_id,
                   on_tap=open_chat, buttons=buttons)


def alert_card(event, title: str, content: str = "{body}", extract: str | None = None,
               fallback_title: str | None = None, link_label: str = "🔗 Open link", **_):
    """High-priority card that stays until dismissed, for things that need the
    owner's attention (a declined charge, ...). If the message has a link, a
    button opens it. Nothing else happens.
    """
    event, matched = _enrich(event, extract)
    notif_id = f"alert-{event['id']}"
    buttons = []
    if url := ext.first_url(event.get("body", "")):
        buttons.append((link_label, f"{android.BIN}/termux-open-url {shlex.quote(url)}"))
    android.notify(_pick(title, fallback_title, event, matched), _fmt(content, event),
                   notif_id=notif_id, buttons=buttons, priority="max", vibrate="300,150,300")


def calendar_card(event, _state: dict | None = None, duration_minutes: int = 30,
                  title_prefixes: dict | None = None, calendar_app: str | None = android.GOOGLE_CALENDAR,
                  now: datetime | None = None, calendars: dict | None = None, **_):
    """For a booked-appointment text: a card whose button opens the calendar's
    new-event screen, prefilled (title, time, place, original text). Nothing is
    saved until the owner taps Save there. Skips past and cancelled
    appointments, and offers each appointment time only once, however many
    reminders arrive for it.

    title_prefixes maps a name to a prefix ({"Dana": "D"}): when the text is
    addressed to that name, the title becomes "J: ...". No match, no prefix.
    """
    body = event.get("body", "")
    sender = event.get("contact") or event.get("title") or event.get("number", "")
    appt = ext.appointment(body, sender)
    if not appt:
        log.info("calendar_card: no appointment found in %s", event["id"])
        return
    if appt.cancelled or appt.start <= (now or datetime.now()):
        log.info("calendar_card: skipping %s (%s)", event["id"],
                 "cancelled" if appt.cancelled else "in the past")
        return
    offered = _state.setdefault("offered", []) if _state is not None else []
    key = appt.start.isoformat()
    if key in offered and not event.get("test"):
        log.info("calendar_card: %s at %s already offered", appt.title, key)
        return
    offered.append(key)
    del offered[:-200]

    title = appt.title
    for name, prefix in (title_prefixes or {}).items():
        if name in body:
            title = f"{prefix}: {title}"
            break

    begin = int(appt.start.timestamp() * 1000)
    end = begin + duration_minutes * 60_000
    notif_id = f"cal-{key}"
    add = "; ".join([
        android.cmd_calendar_insert(title, begin, end, appt.location, body, app=calendar_app),
        android.cmd_remove_notification(notif_id),
    ])
    when = appt.start.strftime("%a %d/%m %H:%M")
    found = cal.where(appt.start, appt.title, calendars) if calendars else None
    if found and all(found.values()):                # already in both: no card
        log.info("calendar_card: %s at %s already in %s", appt.title, key, ", ".join(found))
        return
    where = f"\n{dg.LTR}📅 {cal.note(found)}" if found else ""
    android.notify(f"📅 {title}", f"{when}" + (f" · {appt.location}" if appt.location else "") + where,
                   notif_id=notif_id, on_tap=add,
                   buttons=[("📅 Add to calendar", add)])


DIGESTS = VAR / "digests"

# Card rule: anything with an action, or important, gets a card of its own
# (its own buttons, readable, tappable). Only purely informational lists are
# grouped on one card.


OPEN_CHAT = "💬 Open chat"           # only on a button that goes straight into the chat


def _own_card(nid: str, title: str, text: str, buttons: list[tuple[str, str]] = (),
              priority: str = "high", on_tap: str | None = None) -> None:
    """One item, one card. Each button also removes the card. No "hide" button:
    a swipe does the same. A card from a chat opens it when
    tapped, like the app's own notification, and the card goes; a "💬 Open chat" button (one that goes straight into the chat)
    is then left out, since the tap does that. A button that only opens the app
    stays, and the tap does the same."""
    done = android.cmd_remove_notification(nid)
    if on_tap is None:
        opener = next((b for b in buttons if b[0] == OPEN_CHAT), None) \
            or next((b for b in buttons if b[0].startswith(("Open ", "💬 Open in "))), None)
        if opener:
            on_tap = f"{opener[1]}; {done}"
            if opener[0] == OPEN_CHAT:
                buttons = [b for b in buttons if b is not opener]
    if len(buttons) >= 3:                       # three share the width: about 9 characters each show
        buttons = [(_short(label), cmd) for label, cmd in buttons]
    buttons = [(label, f"{cmd}; {done}") for label, cmd in buttons]
    android.notify(title, dg.LTR + text, notif_id=nid, priority=priority, buttons=buttons, on_tap=on_tap)


def _short(label: str) -> str:
    """A label for a card with three buttons, where only about 9 characters
    show: "⏰ Remind me tonight" -> "⏰ Tonight", "↪ Forward to Dana" -> "↪ To Dana"
    (cut off, "Forward…", "Remind…", "Remind…" tell nothing)."""
    for long, short in (("⏰ Remind me tonight", "⏰ Tonight"), ("⏰ Remind me tomorrow", "⏰ Tomorrow"),
                        ("↪ Forward to ", "↪ To ")):
        if label.startswith(long):
            return short + label[len(long):]
    return label


WEATHER_APP = "com.sec.android.daemonapp"


def _weather_card(res: dict, forward_to: str = "") -> None:
    """Out-of-the-ordinary weather: its own card; tap (or 🌤) opens the weather
    app. forward_to (private.toml [school] forward_to, e.g. a partner): a
    "↪ Forward to …" button with the weather typed in."""
    new = list(res["new"].values())
    open_app = android.cmd_open_app(WEATHER_APP)
    when = "today" if res["day"] == datetime.now().strftime("%Y-%m-%d") else "tomorrow"
    buttons = [("🌤 Weather", open_app)]
    fwd = _forward_button(forward_to, f"Weather {when}: " + "; ".join(new) + f"\n{res['line']}")
    if fwd:
        buttons.append(fwd)
    _own_card(f"weather-{res['day']}", f"{res['line'].split()[0]} {new[0]}"[:40],
              "\n".join(dg.LTR + r for r in new[1:] + [f"{when.capitalize()}: {res['line']}"]),
              buttons, priority="default", on_tap=open_app)
    wx.mark_shown(res["day"], res["new"].keys())


def weather_watch(event, lat: float, lon: float, from_hour: int = 7, to_hour: int = 21,
                  school: dict | None = None, **_):
    """During the day: a card only for something new and significant (see weather.py)."""
    if not from_hour <= datetime.now().hour <= to_hour:
        return
    try:
        res = wx.check(lat, lon)
    except wx.WeatherError as e:
        log.warning("weather check failed: %s", e)
        return
    if res["new"]:
        _weather_card(res, (school or {}).get("forward_to", ""))


_CONTACTS: dict = {"at": 0.0, "book": {}}


def _number(name: str) -> str | None:
    """A contact's number by name (contacts cached for an hour)."""
    if not name:
        return None
    if datetime.now().timestamp() - _CONTACTS["at"] > 3600:
        try:
            _CONTACTS.update(at=datetime.now().timestamp(), book=android.contacts())
        except android.AndroidError as e:
            log.warning("contacts unavailable: %s", e)
    return _CONTACTS["book"].get(name)


def _forward_target(school: dict | None, group: str) -> str:
    """Who a school group's items go to: [school.forward] by group name, else forward_to."""
    school = school or {}
    for key, target in (school.get("forward") or {}).items():
        if sch.clean(key) in sch.clean(group):
            return target
    return school.get("forward_to", "")


def _class_label(school: dict | None, group: str) -> str:
    """[school.labels]: "Grade 10 (Ofek)" for a class group, else the group name."""
    for key, label in ((school or {}).get("labels") or {}).items():
        if sch.clean(key) in sch.clean(group):
            return label
    return group


def _forward_button(target, text: str) -> tuple[str, str] | None:
    """↪ button. target: a contact name, or {contact, label} (its chat opens with
    `text` typed in), or "group:<name>" (copy `text` and open that group if its
    chat id is known, else WhatsApp's chat picker). The owner sends."""
    if not target:
        return None
    if isinstance(target, dict):
        contact, name = target.get("contact", ""), target.get("label") or target.get("contact", "")
        return (f"↪ Forward to {name}", android.cmd_whatsapp_reply(text, _number(contact)))
    if target.startswith("group:"):
        name = target[6:]
        jid = android.jid_for(name)
        if jid:
            return (f"↪ Forward to {name}", f"{android.cmd_copy(text)}; {android.cmd_open_whatsapp_chat(jid)}; "
                                 f"{android.BIN}/termux-toast 'Copied: paste and send'")
        return (f"↪ Forward to {name}", android.cmd_whatsapp_reply(text, None))
    return (f"↪ Forward to {target}", android.cmd_whatsapp_reply(text, _number(target)))


APPROVAL = re.compile(r"אישור\s*הורים|parent(al)?\s*(approval|consent)|permission slip", re.I)


def _approval_button(texts, school: dict | None) -> tuple[str, str] | None:
    """A school item that needs a parent's approval, from a school listed in
    private.toml [school.approval] match (approvals that are given in the school app): a
    button that opens that app. None otherwise."""
    cfg = (school or {}).get("approval") or {}
    blob = " ".join(t for t in texts if t)
    if not cfg.get("app") or not APPROVAL.search(blob):
        return None
    if not any(m.lower() in blob.lower() for m in cfg.get("match", [])):
        return None
    return (cfg.get("label", "📲 Approve in app"), android.cmd_open_app(cfg["app"]))


def _school_card(r: dict, today=None, forward_to="", label: str = "", school: dict | None = None) -> None:
    """One school item, one button: approve it in the school's app if it needs a
    parent's approval (see _approval_button); open the app for an item that came
    from it (Mashov: register a lateness or absence; its own notification is then
    cleared); else forward it (English summary + class, then the original)."""
    who = " · ".join(x for x in (label or r.get("group", ""), r.get("from", "")) if x)
    app = ((school or {}).get("apps") or {}).get(r.get("package", ""))
    button = (_approval_button([r.get("line"), r.get("text"), r.get("group"), r.get("from")], school)
              or ((f"📲 Open {app}", android.cmd_open_app(r["package"])) if app else None)   # e.g. register a lateness
              or _forward_button(forward_to, sch.forward_text(r, label, today)))
    if app and r.get("key"):                  # the card replaces the app's own notification
        _clear({"key": r["key"], "package": r["package"], "title": ""})
    line = sch.headline(r)
    original = " ".join((r.get("text") or "").split())       # the title is the line: the body shows the message itself
    body = f"{who}\n{original[:220]}{'…' if len(original) > 220 else ''}" if original else f"{line}\n{dg.LTR}{who}"
    _card_with_reminders(sch.item_id(r), f"🏫 {sch.label(r['date'], today)}: {line}",
                         body, [button] if button else [],
                         day=date.fromisoformat(r["date"]))


def _without_times(text: str) -> str:
    """A card's text without the time each listed message came ("‎12:24 Dana: …"):
    what's left says what it's about (a time in it is a deadline)."""
    return "\n".join(re.sub(r"^[\u200e\u200f\u2066-\u2069]*\d{1,2}:\d\d\s", "", l) for l in text.splitlines())


def _card_with_reminders(nid: str, title: str, text: str, buttons: list[tuple[str, str]],
                         priority: str = "high", day=None, which=("evening", "morning"),
                         about: str | None = None) -> None:
    """A card with "⏰ Remind me tonight / tomorrow" after its own buttons (as
    many as fit in three: a reminder never pushes out the card's own button).
    A reminder takes the card away and brings it back then, with the same
    buttons (reminders.py)."""
    reminders.offer(nid, title, text, buttons, priority, day, which=tuple(which))
    now = datetime.now()
    extra = reminders.buttons(nid, now, day, which, deadline=None if day else reminders.deadline_in(
                                     about if about is not None else _without_times(f"{title}\n{text}"), now))
    _own_card(nid, title, text, (list(buttons) + extra)[:3], priority=priority)


# Personal cards (someone to get back to, something to do) get only "tonight"
#; group, calendar and email cards none.
TONIGHT = ("evening",)


def reminders_due(event, **_):
    """Cards whose reminder time has come: posted again, marked ⏰."""
    due = reminders.pop_due()
    live = {n.get("tag") for n in android.notifications() if n.get("packageName") == "com.termux.api"} if due else set()
    for c in due:
        if c["nid"] in live:                        # a chat's newer card is up: it already brings the chat back
            continue
        day = date.fromisoformat(c["day"]) if c.get("day") else None
        title = c["title"] if c["title"].startswith("⏰") else f"⏰ {c['title']}"
        if day:                                     # "Tomorrow: …" said yesterday is "Today: …" now
            title = re.sub(r"^(⏰ )?(\S+ )(Today|Tomorrow|\w{3} \d\d/\d\d): ",
                           lambda m: f"⏰ {m[2]}{sch.label(day.isoformat())}: ", title)
        _card_with_reminders(c["nid"], title, c["text"], [tuple(b) for b in c["buttons"]], c["priority"], day,
                             c.get("which") or ("evening", "morning"))


def _email_card(item: dict, school: dict | None = None) -> bool:
    """An email from the morning Gmail check gets a card only for something Gmail
    can't do: a school approval (opens the school's app). Otherwise it's a line
    on the summary; the email itself stays an email. No
    "open this email" button: this Gmail can't be opened at one email from a
    link (its link handler crashes; its main screen shows the inbox)."""
    approve = _approval_button([item["line"]], school)
    if not approve:
        return False
    sender, _, text = item["line"].partition(":")
    _own_card(f"mail-{item['thread']}", f"✉️ {sender.strip()}", text.strip(), [approve])
    return True


def notification_digest(event, title: str = "☀️ Morning", noise: dict | None = None,
                        ignore_packages=(), ignore_titles=(), max_items: int = 8,
                        email: bool = False, email_since_hour: int = 17, email_max: int = 5,
                        ensemble_status: bool = False, school: dict | bool = False,
                        weather: dict | None = None, weather_tomorrow: bool = False, **_):
    """The morning overview, as a summary card you page through like the others
    (summaries.py, "✓ Hide these lines"): the weather, then each notification
    on the phone that has no card of its own, one readable line each, then a
    short 🤖 Ensemble line. Each email that matters this morning (Claude reads
    Gmail, read-only, since `email_since_hour` yesterday) is a ✉️ line at the
    top; a card only for a school approval (an email stays an email). Each school item for today/tomorrow gets a card of its own.
    Lines hidden at 05:00 don't come back at 06:10. No card when nothing is
    left. Text is saved to var/digests/<date>.txt."""
    noise = noise or {}
    notifs = android.notifications()
    carded = _carded_keys(notifs) | android.media_keys()   # a secretary card already shows these; players
    headline, lines = dg.summarise([n for n in notifs if n.get("key") not in carded],
                                   [*noise.get("packages", []), *ignore_packages],
                                   [*noise.get("titles", []), *ignore_titles], 0,
                                   noise.get("patterns", []), width=90)
    lines = [l.replace(" · Snoozed: ", " · ⏰ ") for l in lines]     # Gmail: back from snooze
    separate = []
    if email:
        since = datetime.now().replace(hour=email_since_hour, minute=0, second=0, microsecond=0)
        since -= timedelta(days=1)
        try:
            ans = claude.ask(dg.EMAIL_PROMPT.format(since=int(since.timestamp()), max_items=email_max,
                                                    about=owner.about()),
                             dg.GMAIL_READ_TOOLS, require_tool=dg.GMAIL_READ_TOOLS[0])
            mail = []
            for item in dg.parse_email_items(ans.text, ans.tool_output, email_max):
                if _email_card(item, school if isinstance(school, dict) else None):
                    separate.append(f"✉️ {item['line']}")
                else:
                    mail.append(f"✉️ {item['line']}")
            lines = mail + lines                     # most urgent first, above the notifications
        except claude.ClaudeError as e:
            log.warning("email check failed: %s", e)
            lines.insert(0, "✉️ Email check didn't run")
    if school:
        today = datetime.now().date()
        cfg = school if isinstance(school, dict) else {}
        for r in sch.due():                    # those whose time has come (most wait for their own time)
            _school_card(r, today, _forward_target(cfg, r.get("group", "")), _class_label(cfg, r.get("group", "")), cfg)
            separate.append(f"🏫 {sch.label(r['date'], today)}: {sch.headline(r)}")
    extra = []                                      # (label, command, line) extra buttons on the card
    if weather:
        try:
            when = (datetime.now() + timedelta(days=1)).replace(hour=6, minute=0) if weather_tomorrow else None
            res = wx.check(weather["lat"], weather["lon"], now=when, morning=True)
            if res["new"]:
                _weather_card(res, (school if isinstance(school, dict) else {}).get("forward_to", ""))
                separate.append(f"🌦 {'; '.join(res['new'].values())}")
            lines.insert(0, ("Tomorrow " if weather_tomorrow else "") + res["line"])
            fwd = _forward_button((school if isinstance(school, dict) else {}).get("forward_to", ""),
                                  f"Weather {'tomorrow' if weather_tomorrow else 'today'}: {res['line']}")
            if fwd:                                # the morning weather can be sent on too
                extra.append((fwd[0].replace("↪ Forward to ", "↪ Weather to "), fwd[1], lines[0]))
        except (wx.WeatherError, KeyError) as e:
            log.warning("weather check failed: %s", e)
    status = []
    if ensemble_status:                            # only when it needs the owner
        st = ensemble.state()
        if st["kind"] == "needs":
            status = [f"🤖 Ensemble: {st['detail'].splitlines()[0]}"]
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    DIGESTS.mkdir(parents=True, exist_ok=True)
    with open(DIGESTS / f"{stamp[:10]}.txt", "a", encoding="utf-8") as f:
        f.write(f"{stamp}\n" + "".join(f"{l}\n" for l in (separate + lines + status) or ["(nothing)"]) + "\n")
    day = stamp[:10]
    items = [(f"m-{day}-" + hashlib.sha1(l.encode()).hexdigest()[:10], l) for l in lines + status]
    if not lines:
        items = []                                 # just the Ensemble line: no card for that
        log.info("morning digest: nothing to show, no card posted")
    ids = {l: i for i, l in items}
    summaries.post("morning", _fmt(title, event), items, nid="morning-digest", priority="default",
                   buttons=[(label, cmd, ids[l]) for label, cmd, l in extra if l in ids])
    news_roundup()                                  # the morning news round-up, on the 📰 card


def _carded_keys(notifs: list[dict]) -> set[str]:
    """Keys of notifications that one of the secretary's own cards (still up) is about."""
    live = {n.get("tag") for n in notifs if n.get("packageName") == "com.termux.api"}
    keys = set()
    for r in secretary.recent(days=2):
        if r.get("key") and (secretary.item_id(r) in live or _chat_nid(r) in live):
            keys.add(r["key"])
    return keys


def after_quiet(event, lat: float | None = None, lon: float | None = None, depression: float = shabbat.DEPRESSION,
                  _state: dict | None = None, **digest):
    """The summary the owner gets on weekday mornings, once, as soon as a quiet
    time ends (quiet.py; Shabbat: at tzeit, see shabbat.py). Runs from a clock rule
    every few minutes; if the phone was off at the time, it runs at the first
    check after. Weather is tomorrow's. No quiet times set: it never runs."""
    state = _state if _state is not None else {}
    now = datetime.now()
    today = now.date().isoformat()
    if state.get("done") == today:
        return
    out = quiet.ends(now.date(), lat, lon, depression)
    if out is None or now < out:
        return
    state["done"] = today
    log.info("quiet time over at %s: posting the summary", out.strftime("%H:%M"))
    notification_digest(event, **{"title": "🌙 Shavua tov", "weather_tomorrow": True, **digest})


def calendar_copy(event, calendars: dict | None = None, copy_cfg: dict | None = None, **_):
    """Personal events with no copy in the Family calendar: asked about first,
    every one; the ones the owner says yes to are copied, with
    no reminders, and a card lists them. See calendar_copy.py. On demand:
    `automations fire calendar-copy`."""
    cals, cfg = calendars or {}, copy_cfg or {}
    if urg.quiet_now() or not (cals.get("Personal") and cals.get("Family") and cfg.get("family")):
        return
    found = calcopy.missing(cals["Personal"], cals["Family"], cfg.get("skip", []), int(cfg.get("days", calcopy.DAYS)))
    if found is None:
        log.warning("calendar copy: couldn't read the calendars")
        return
    copy_now, doubts = found
    everything = copy_now + doubts
    never, approved = calcopy.never(), calcopy.approved()
    todo = calcopy.due([i for i in everything if i["id"] in approved and i["id"] not in never])
    if todo:
        try:
            done = calcopy.copy(todo, cfg["personal"], cfg["family"])
        except claude.ClaudeError as e:
            log.warning("calendar copy failed: %s", e)
            return FAILED
        made = [d for d in done if d["result"] == "created"]
        bad = [d for d in done if d["result"] == "failed"]
        log.info("calendar copy: %d created, %d already there, %d failed", len(made),
                 len(done) - len(made) - len(bad), len(bad))
        if made or bad:
            lines = [_calrow(d) for d in made] + [f"Couldn't copy: {_calrow(d)}" for d in bad]
            _own_card("calendar-copy", f"📅 Copied to Family: {len(made)} (no reminders)" if made
                      else "📅 Couldn't copy to Family", "\n".join(dg.LTR + l for l in lines), priority="low")
    ask = calcopy.to_ask([i for i in everything if i["id"] not in approved and i["id"] not in never])
    if ask:
        _calcopy_ask(ask)


def _calrow(d: dict) -> str:
    when = datetime.fromisoformat(d["start"])
    return f"{when.strftime('%a %d/%m' if len(d['start']) == 10 else '%a %d/%m %H:%M')} · {d['title']}"


def _calcopy_ask(items: list[dict]) -> None:
    """Up to three: a card each (✅ Copy / ✋ Don't copy). More (a batch of
    bookings): one card listing them, ✅ Copy all / ✋ None of these / 🤖 Choose
    (Claude goes through them with the owner)."""
    run = shlex.quote(str(paths.ROOT / "bin" / "automations"))
    if len(items) <= 3:
        for d in items:
            nid = "calcopy-" + hashlib.sha1(d["id"].encode()).hexdigest()[:10]
            why = f"\n{dg.LTR}{d['why']}" if d.get("why") else ""
            _own_card(nid, f"📅 Copy to Family? {d['title']}"[:60],
                      f"{_calrow(d)}{why}\n{dg.LTR}The copy has no reminders.",
                      [("✅ Copy", f"{run} calcopy yes {shlex.quote(d['id'])}; {run} fire calendar-copy"),
                       ("✋ Don't copy", f"{run} calcopy no {shlex.quote(d['id'])}")], priority="default")
        return
    ids = shlex.quote(",".join(d["id"] for d in items))
    listing = "\n".join(f"{_calrow(d)}" + (f" ({d['why']})" if d.get("why") else "") for d in items)
    prompt = ("These events from my Personal calendar aren't in the Family calendar yet. Go through them with me "
              "and copy the ones I say to Family with phone-automations' calendar copy (bin/automations calcopy "
              "yes|no ID, then bin/automations fire calendar-copy): " + "; ".join(
                  f"{d['id']} = {_calrow(d)}" for d in items))
    _own_card("calcopy-batch", f"📅 Copy {len(items)} events to Family?", listing[:900] + f"\n{dg.LTR}Copies have no reminders.",
              [("✅ Copy all", f"{run} calcopy yes {ids}; {run} fire calendar-copy"),
               ("✋ None", f"{run} calcopy no {ids}"),
               ("🤖 Choose", android.cmd_ask_claude(prompt))], priority="default")


def weekly_ideas(event, secretary_cfg: dict | None = None, model: str = "sonnet", **_):
    """Friday midday: up to three 💡 cards, ideas for new automations from the
    week's notifications."""
    try:
        picked = ideas.pick((secretary_cfg or {}).get("decided", []), model)
    except claude.ClaudeError as e:
        log.warning("weekly ideas failed: %s", e)
        return FAILED
    log.info("weekly ideas: %d", len(picked))
    ideas.offered(picked)
    run = shlex.quote(str(paths.ROOT / "bin" / "automations"))
    for n, d in enumerate(picked, 1):
        seen = f"\n{dg.LTR}Came up {d['seen']} times this week" if d.get("seen", 0) > 1 else ""
        _own_card(d["id"], f"💡 Idea {n}/{len(picked)}: {d['title']}", d["idea"] + seen,
                  [("✅ Build it", f"{run} idea yes {d['id']}; {android.cmd_ask_claude(ideas.build_prompt(d))}"),
                   ("✋ No thanks", f"{run} idea no {d['id']}")], priority="low")


def parcel_watch(event, **_):
    """A post office "parcel waiting" text: a 📦 card with the estimated last
    day to collect it, the post office app and "📅 Add last day"; it comes
    back two working days before."""
    if event.get("package") not in SMS_APPS:
        return
    p = parcels.parse(event.get("title", ""), event.get("body", ""))
    if not p:
        return
    rec = parcels.seen(p)
    last = date.fromisoformat(rec["last"])
    nid = "parcel-" + hashlib.sha1((p["id"] or p["branch"]).encode()).hexdigest()[:10]
    begin = int(datetime.combine(last, datetime.min.time().replace(hour=9)).timestamp() * 1000)
    buttons = [("📦 Post office app", android.cmd_open_app(parcels.APP)),
               ("📅 Add last day", android.cmd_calendar_insert(
                   f"📦 Last day to collect parcel ({p['branch']})", begin, begin + 15 * 60_000,
                   p["address"], f"Parcel {p['id']} at {p['branch']}. Estimated: check the post office app."))]
    where = "pickup point" if p["pickup_point"] else "post office"
    text = (f"Collect by {last.strftime('%a %d/%m')} (estimate: about 14 working days from the first notice"
            + (", less at a pickup point" if p["pickup_point"] else "") + ")\n"
            + dg.LTR + " · ".join(x for x in (p["address"], p["id"]) if x))
    title = f"📦 Parcel waiting at the {where}: {p['branch']}"[:60]
    reminders.offer(nid, title, text, buttons, "default", day=last, which=())
    due = parcels.come_back(last)
    if due:
        reminders.schedule(nid, due)
    _own_card(nid, title, text, buttons, priority="default")


# Nothing but a greeting (Hebrew), punctuation and emoji allowed: "שבת שלום!", "שבוע טוב (:".
GREETING = re.compile(r'^(?:[\W_]*(?:שבת שלום|שבוע טוב|בוקר טוב|לילה טוב|ערב טוב|חג שמח|חג כשר ושמח|פסח כשר ושמח'
                      r'|שנה טובה(?: ומתוקה)?|גמר חתימה טובה|צום קל|מועדים לשמחה|חנוכה שמח|פורים שמח'
                      r'|(?:בוקר )?ל["״]ג בעומר שמח))+[\W_]*$')


def _greeting_only(event, senders) -> bool:
    """An SMS from one of `senders` (private.toml [secretary] greetings_only) that
    is only a greeting (a weekly "have a good weekend", say): marked read with the
    app's button, no card. Anything more, even "sorry I forgot to write", goes
    to the secretary as usual."""
    names = {sch.clean(x).lower() for x in senders or ()}
    if event.get("package") not in SMS_APPS or sch.clean(event.get("title", "")).lower() not in names:
        return False
    return bool(GREETING.match(sch.clean(event.get("body", ""))))


def secretary_collect(event, noise: dict | None = None, school: dict | None = None,
                      email_lists: dict | None = None, secretary_cfg: dict | None = None,
                      _state: dict | None = None, **_):
    """Queue every (non-noise) notification for the secretary's next triage.
    Messages from school groups and other watched groups are left to their
    digests, and a mailing list's emails (switched on in private.toml
    [email_lists]) to its daily line."""
    if _greeting_only(event, (secretary_cfg or {}).get("greetings_only")):
        log.info("greeting from %s: marked read", sch.clean(event.get("title", "")))
        _clear(event)                            # marked read, or left in the shade; never snoozed
        return
    key = elist.list_of(event, {k: v for k, v in (email_lists or {}).items() if v.get("on")})
    if key:
        elist.note(key)
        return
    if event.get("package") in SMS_APPS and parcels.parse(event.get("title", ""), event.get("body", "")):
        return                                   # parcel_watch has it
    if sch.group_of(event.get("title", ""), (school or {}).get("groups", [])):
        return
    if event.get("package") in (school or {}).get("apps", {}):
        return                                   # the school app: school_app_collect has it
    if event.get("package") == TELEGRAM and grp.watched(sch.clean(event.get("title", ""))):
        return
    if event.get("package") == "com.whatsapp":
        group, _sender, _text = grp.parse(event.get("title", ""), event.get("body", ""), grp.known)
        if group and (grp.watched(group) or sch.group_of(group, _school_groups(school))):
            return
    if secretary.collect(event, noise, _state) and event.get("package") == "com.whatsapp":
        _learn_jid(event.get("title", ""))       # while it's live: a reply card can then open this chat


def urgent_watch(event, urgent: dict | None = None, **_):
    """A clearly urgent message from someone close (private.toml [urgent]): their chat
    opens at once and a loud 🚨 card comes; maybe urgent, the same silently.
    From anyone else the owner knows: only certainly urgent, and silently
    (see urgent.py)."""
    people = (urgent or {}).get("people", {})
    if not urg.check(event, people, book=lambda: {c: _number(c) for c in people}):
        urg.check_others(event, people, book=_contact_book)


def _contact_book() -> dict[str, str]:
    """All saved contacts, name -> number (cached for an hour, as _number)."""
    _number("-")                      # refreshes the cache when it's old
    return _CONTACTS["book"]


RULES_FILE = paths.RULES


GLANCE_STAYS = ("com.google.android.calendar", "com.sec.android.app.clockpackage")   # their timing is the point
ONE_TIME_CODE = re.compile(r"\b(code|OTP|passcode)\b|קוד|סיסמ", re.I)
COUNT_HEADER = re.compile(r"^\s*\d+ new messages?\s*$")


def _glanced(r: dict, kind: str = "dismiss") -> bool:
    """A "dismiss" (glance and clear) item that moves to the 🗂 card, its own
    notification swiped away (e.g. a payment receipt). Not: chats (WhatsApp/Telegram), calendar reminders
    and alarms, one-time codes (needed within minutes), Gmail's count header."""
    text = f"{r.get('title', '')} {r.get('text', '')}"
    return (r.get("kind") == kind and not r.get("judged")
            and r.get("package") not in (*android.NEVER_SNOOZE, *GLANCE_STAYS)
            and not ONE_TIME_CODE.search(text) and not COUNT_HEADER.match(r.get("text") or ""))


# Returned by an action whose Claude call failed (its input is kept for the
# next run): the engine then holds off its "soon" runs for a while.
FAILED = "failed"


def secretary_triage(event, model: str = "sonnet", hold_hours: float = 12,
                     secretary_cfg: dict | None = None, school: dict | None = None,
                     calendars: dict | None = None, email_lists: dict | None = None,
                     groups: dict | None = None, _state: dict | None = None, **_):
    """If notifications are queued, have Claude suggest how to help with each.
    act_now / reply / schedule items get a card each with buttons (open the app,
    open the chat with a reply typed in, add to calendar); the rest are listed
    on one quiet 🗂 card."""
    import tomllib
    names = [r["name"] for r in tomllib.loads(RULES_FILE.read_text(encoding="utf-8")).get("rule", [])
             if r.get("enabled", True) and not r["name"].startswith("secretary")]
    try:
        records = secretary.triage(names, model, (secretary_cfg or {}).get("decided", []),
                                   (secretary_cfg or {}).get("judge", {}))
    except claude.ClaudeError as e:
        log.warning("secretary triage failed (queue kept): %s", e)
        return FAILED
    state = _state if _state is not None else {}
    listed = _email_lists(email_lists or {}, groups or {}, secretary_cfg or {}, state)
    if not records and not listed:
        return
    for r in records:                            # judged apps (Blind): clear / summary / leave
        if r.get("judged"):
            done = r["keep"] == "card" or (bool(r.get("key")) and android.snooze(r["key"]))
            secretary.log_judged(r, bool(done))
            if not done and r.get("key"):
                retry_later(r["key"])
    _cap_to_summary(records, (secretary_cfg or {}).get("summary_max", []))
    _big_transactions(records)
    _check_calendars(records, calendars or {})
    capped = {sch.clean(x).lower() for x in (secretary_cfg or {}).get("summary_max", [])}
    for r in records:
        # A capped sender's chat (a neighbourhood channel): marked read with
        # the app's own button, whatever it was. If it can't be, it stays in the shade.
        if r.get("key") and r.get("package") in CHAT_APPS and sch.clean(r.get("title", "")).lower() in capped:
            r["marked"] = True
            _clear({"key": r["key"], "package": r["package"], "title": r.get("title", "")})
    for r in records:     # "ignore" (spam, promos): swiped too, no line; "later": on 🗂, so swiped
        if r.get("marked"):
            continue
        if any(_glanced(r, k) for k in ("dismiss", "ignore", "later")) and r.get("key") \
                and r.get("package") != GMAIL \
                and _chat_nid(r) not in state.get("chats", {}):  # Gmail's are left as they are
            android.dismiss(r.get("package", ""), r.get("title", ""), r["key"])
    book: dict = {}
    live = {n.get("tag") for n in android.notifications() if n.get("packageName") == "com.termux.api"}
    chats = {k: v for k, v in state.get("chats", {}).items() if k in live}   # a card swiped or tapped: start afresh
    folded = set()
    for r in records:
        nid = _chat_nid(r)
        if r.get("judged") or not (r["kind"] in secretary.OWN_CARD or (nid in chats and r["kind"] != "ignore")):
            continue
        if nid:                                # one card per chat, its messages listed
            chat_card(r, book, chats, school)
            folded.add(id(r))
        elif r.get("package") == GMAIL and not _email_extra(r, school):
            continue                           # an email stays an email: Gmail's own notification is its card
        else:
            secretary_card(r, book, school)
    state["chats"] = chats

    # Everything else: one informational card, newest first, kept for `hold_hours`.
    now = datetime.now().timestamp()
    rest = [{**r, "ts": now} for r in records if id(r) not in folded and r.get("package") != GMAIL and
            (r.get("keep") == "summary" if r.get("judged") else r["kind"] not in secretary.OWN_CARD)]
    kept = rest + [r for r in state.get("card", []) if r.get("ts", 0) >= now - hold_hours * 3600
                   and r.get("package") != GMAIL]          # lines from before email left 🗂 (2026-11-17)
    state["card"] = kept[:30]
    if not rest and not listed:
        return
    shown = [r for r in reversed(kept)                                            # oldest first
             if r.get("keep") == "summary" or r["kind"] in ("later", "automate", "list") or _glanced(r)]
    summaries.post("secretary", "🗂 Secretary",
                   [(secretary.item_id(r), secretary.card_lines([r])[0]) for r in shown], nid="secretary")


GMAIL = "com.google.android.gm"


def _email_lists(lists: dict, groups: dict, secretary_cfg: dict, state: dict) -> bool:
    """Mailing lists with new emails (see email_list.py): their posts join the
    day's line on 🗂 (True if it changed), those for the owner get a card, and
    then the emails are marked read in Gmail. A failed read is tried again at
    the next triage; a failed mark-read every 30 minutes."""
    changed = False
    for key in elist.pending():
        cfg = lists.get(key)
        if not cfg or not cfg.get("on"):
            elist.finish(key, [])
            continue
        label = cfg.get("label") or key
        try:
            posts = elist.read(key, cfg, cfg.get("owner", ""), kids=groups.get("kids", ""),
                               texted=_texted_offers(secretary_cfg.get("summary_max")))
        except claude.ClaudeError as e:
            log.warning("email list %s: couldn't read it (tried again next time): %s", key, e)
            continue
        for p in posts:
            if p["card"]:
                _own_card(elist.card_id(p), f"📣 {p['title'] or p['short']}",
                          f"{p['line']}\n{dg.LTR}📧 {label}", [("Open Gmail", android.cmd_open_app(GMAIL))])
        if posts:
            rec = elist.day_line(label, posts)
            now = datetime.now()
            state["card"] = [{"at": now.strftime("%Y-%m-%d %H:%M"), "app": label, "package": "email-list",
                              "kind": "list", "list": label, "title": label, "text": rec["text"],
                              "summary": rec["text"], "ts": now.timestamp()}] + \
                [r for r in state.get("card", []) if not (r.get("list") == label and r["at"][:10] == rec["day"])]
            changed = True
        log.info("email list %s: %d posts, %d cards", key, len(posts), sum(1 for p in posts if p["card"]))
        elist.finish(key, sorted({p["thread"] for p in posts}))
        if posts:
            _mark_list_read()
    if not changed and elist.mark_due():
        _mark_list_read()
    return changed


def _mark_list_read() -> None:
    threads = elist.to_mark()
    try:
        done = elist.mark_read(threads)
    except claude.ClaudeError as e:
        log.warning("email list: couldn't mark %d emails read (tried again later): %s", len(threads), e)
        done = []
    elist.marked(done)


def _email_extra(r: dict, school: dict | None) -> bool:
    """Whether a card adds something to Gmail's own notification: an event to
    put in the calendar, or a school approval to give in the school's app.
    Otherwise the email stays an email: no card, no 🗂
    line, and Gmail's notification is left as it is (it can't be marked read
    from here, so swiping it would only lose it)."""
    return bool((r["kind"] == "schedule" and r.get("event"))
                or _approval_button([r.get("title"), r.get("text"), r.get("summary")], school))


# A post that says nothing by itself (a channel's "2 new messages", "📷 Sent a photo"): no 🗂 line.
EMPTY_POST = re.compile(r"^(\d+ new messages?|(📷|🎥|🎤|🖼️?)?\s*(sent an? )?(photo|video|image|sticker|gif|voice message)"
                        r"( was sent)?|📷|🎥)$", re.I)


def _cap_to_summary(records: list[dict], senders) -> None:
    """Senders the owner never wants a card from (private.toml [secretary]
    summary_max, e.g. a programme's marketing SMS): at most a line on 🗂, so a
    card-worthy verdict becomes "later". Another app's copy of the same
    message (Truecaller's spam warning) is swiped without a line."""
    names = {sch.clean(x).lower() for x in senders}
    for r in records:
        if sch.clean(r.get("title", "")).lower() not in names:
            continue
        if r.get("package") == "com.truecaller" or EMPTY_POST.match((r.get("text") or "").strip()):
            r["kind"] = "ignore"               # Truecaller's copy, or "2 new messages" / "📷 Sent a photo": no line
        elif r["kind"] in secretary.OWN_CARD:
            r["kind"] = "later"


# A payment or charge of four figures or more, any currency: its own card.
CUR = r'(?:₪|\$|€|£|ש"ח|ש״ח|שח|NIS|ILS|USD|EUR|GBP|shekels?|dollars?)'
AMOUNT = re.compile(rf'{CUR}\s?(\d{{1,3}}(?:,\d{{3}})+|\d{{4,}})(?:\.\d+)?(?!\d)'
                    rf'|(?<![\d,.])(\d{{1,3}}(?:,\d{{3}})+|\d{{4,}})(?:\.\d+)?\s?{CUR}'
                    r'|(?:בסך|בסכום|בגובה|סך של)\s*(\d{1,3}(?:,\d{3})+|\d{4,})(?:\.\d+)?', re.I)
PAYMENT = re.compile(r"עסק[הא]|חיוב|חויב|תשלום|שולם|העברה|transaction|charged?|paid|payment|purchase|debit|transfer", re.I)


def big_amount(text: str) -> bool:
    """A transaction of 1,000 or more in any currency ("3,499.00 USD", "₪1200",
    "בסך 1,500"); a card's last four digits alone don't count."""
    return bool(PAYMENT.search(text or "")) and any(
        int((m[1] or m[2] or m[3]).replace(",", "")) >= 1000 for m in AMOUNT.finditer(text or ""))


def _big_transactions(records: list[dict]) -> None:
    """A big transaction is a card of its own, not a 🗂 line (an email stays an email)."""
    for r in records:
        if r.get("package") != GMAIL and r["kind"] not in secretary.OWN_CARD \
                and big_amount(f"{r.get('title', '')}\n{r.get('text', '')}"):
            r["kind"] = "act_now"
            r["suggestion"] = r.get("suggestion") or "A large amount: check it's yours"


def _check_calendars(records: list[dict], calendars: dict[str, int]) -> None:
    """Something to put in the calendar: look in the owner's calendars first
    (private.toml [calendars]; read only, nothing is ever added by itself).
    In all of them: no card, just a 🗂 line (an email: not
    even that, its own notification stays). Otherwise the card says where it's
    missing."""
    if not calendars:
        return
    for r in records:
        e = r.get("event")
        if r["kind"] != "schedule" or not e:
            continue
        try:
            found = cal.where(datetime.fromisoformat(e["start"]), e["title"], calendars)
        except (ValueError, KeyError):
            continue
        r["calendar"] = cal.note(found)
        if found and all(found.values()):
            r["kind"], r["suggestion"] = "later", r["calendar"]


def secretary_card(r: dict, book: dict, school: dict | None = None) -> None:
    """The card for one act_now / reply / schedule record. `book` caches the
    contacts (filled on first need)."""
    if r["kind"] == "reply" and r.get("reply") and r.get("package") == "com.whatsapp":
        if not book:
            try:
                book.update(android.contacts() or {"": ""})
            except android.AndroidError as e:
                log.warning("contacts unavailable (%s); reply buttons will ask which chat", e)
                book[""] = ""
        whatsapp_reply_card(r, book)
        return
    icon = {"act_now": "❗", "reply": "💬", "schedule": "📅"}[r["kind"]]
    buttons = []
    if r["kind"] == "schedule" and r.get("event"):
        e = r["event"]
        begin = int(datetime.fromisoformat(e["start"]).timestamp() * 1000)
        buttons.append(("📅 Add to calendar", android.cmd_calendar_insert(
            e["title"], begin, begin + e["minutes"] * 60_000, e["location"], r["text"])))
    approve = _approval_button([r.get("title"), r.get("text"), r.get("summary")], school)
    if approve:
        buttons.append(approve)
    elif not (r.get("package") == GMAIL and buttons):   # Gmail's notification opens the email; this would only open the inbox
        buttons.append(_open_chat_button(r, book))
    suggestion = f"📅 {r['calendar']}" if r.get("calendar") else r["suggestion"]
    title, text = f"{icon} {r['summary']}", "\n".join(x for x in (suggestion, f"{dg.LTR}{r['app']} · {r['title']}") if x)
    priority = "high" if r["kind"] == "act_now" else "default"
    if r["kind"] == "schedule":                 # the calendar does the reminding
        _own_card(secretary.item_id(r), title, text, buttons, priority=priority)
    else:
        _card_with_reminders(secretary.item_id(r), title, text, buttons, priority, which=TONIGHT)


CHAT_APPS = ("com.whatsapp", "com.whatsapp.w4b", "org.telegram.messenger",
             "com.samsung.android.messaging", "com.google.android.apps.messaging")


CALL_APPS = ("com.truecaller",)         # its "Missed call" notification names the caller
MISSED_CALL = re.compile(r"^missed call\b", re.I)


def _person(r: dict) -> str | None:
    """Who a private message or a missed call is from (the contact's name);
    None for groups and for other apps."""
    pkg, title = r.get("package"), sch.clean(r.get("title", ""))
    if pkg in CALL_APPS and MISSED_CALL.match(title):
        return sch.clean((r.get("text") or "").split("\n")[0]) or None
    if pkg in CHAT_APPS and not grp.parse(r.get("title", ""), "", lambda t: False)[0]:
        return re.sub(r"\s*\(\d+ (?:new )?messages?\)$", "", title) or None
    return None


def _chat_name(r: dict) -> str:
    """The chat a message is from: a WhatsApp group ("Group: Sender") or the person."""
    group, _s, _t = grp.parse(r.get("title", ""), "", lambda t: False)
    return group or _person(r) or sch.clean(r.get("title", ""))


def _chat_nid(r: dict) -> str | None:
    """One card per group chat, and one per person across WhatsApp, SMS,
    Telegram and missed calls (one card per person); None for other apps."""
    group = grp.parse(r.get("title", ""), "", lambda t: False)[0]
    if r.get("package") in CHAT_APPS and group:
        return "chat-" + hashlib.sha1(f"{r['package']}|{group}".encode()).hexdigest()[:10]
    person = _person(r)
    return "person-" + hashlib.sha1(person.encode()).hexdigest()[:10] if person else None


def chat_card(r: dict, book: dict, chats: dict, school: dict | None = None) -> None:
    """A chat's card, posted afresh with each message: the messages since the
    card was last cleared (newest last, up to 6), the latest suggestion, and one
    button (reply if there's a reply to offer, else open the chat). `chats` maps
    a live card's id to its lines and updates."""
    nid = _chat_nid(r)
    entry = chats.setdefault(nid, {"lines": [], "urgent": False})
    call = r.get("package") in CALL_APPS
    icon = "📞 " if call else ""
    entry["lines"] = (entry["lines"] + [f"{r.get('at', '')[11:16]} {icon}{r['summary']}".strip()])[-6:]
    entry["urgent"] = entry["urgent"] or r["kind"] == "act_now"
    if call:
        entry["called"] = True
    else:                                      # where they last wrote: "Open chat" goes there
        entry["via"] = {k: r.get(k, "") for k in ("package", "title", "app")}
    n = entry["count"] = entry.get("count", len(entry["lines"]) - 1) + 1      # all of them, not just the 6 shown
    what = "updates" if entry.get("called") else "messages"
    head = f"{'❗' if entry['urgent'] else '💬'} {_chat_name(r)}" + (f" · {n} {what}" if n > 1 else "")
    body = "\n".join(dg.LTR + l for l in entry["lines"])
    if r.get("suggestion") and r["kind"] in secretary.OWN_CARD:
        body += f"\n{dg.LTR}→ {r['suggestion']}"
    if r["kind"] == "reply" and r.get("reply") and r.get("package") == "com.whatsapp":
        whatsapp_reply_card(r, _contacts(book), nid=nid, head=head, body=body)
        return
    buttons = [_open_chat_button(entry["via"], book)] if entry.get("via") else []
    if entry.get("called"):
        number = _contacts(book).get(_chat_name(r), "") or (
            _chat_name(r) if re.fullmatch(r"\+?[\d -]{7,}", _chat_name(r)) else "")
        if number:
            buttons.append(("📞 Call back", f"{android.BIN}/termux-telephony-call {shlex.quote(number)}"))
    if not buttons:
        buttons = [(f"Open {r['app']}", android.cmd_open_app(r["package"]))]
    _card_with_reminders(nid, head, body, buttons, "high" if entry["urgent"] else "default", which=TONIGHT,
                         about=" ".join(l.split(" ", 1)[-1] for l in entry["lines"]))   # the messages, without their times


def _contacts(book: dict) -> dict:
    """The contacts (name -> number), read once per run into `book`."""
    if not book:
        try:
            book.update(android.contacts() or {"": ""})
        except android.AndroidError as e:
            log.warning("contacts unavailable (%s); chat buttons will open the app", e)
            book[""] = ""
    return book


def _open_chat_button(r: dict, book: dict) -> tuple[str, str]:
    """💬 Open chat, straight into the chat: a WhatsApp group by its id, a
    person by their number from the contacts. The app itself only when neither
    is known (the label says so)."""
    if r.get("package") == "com.whatsapp":
        jid = _chat_jid(r["title"])
        if jid:
            return ("💬 Open chat", android.cmd_open_whatsapp_chat(jid))
        if not grp.parse(r["title"], "", lambda t: False)[0]:
            cmd = android.cmd_open_whatsapp_number(_contacts(book).get(sch.clean(r["title"]), ""))
            if cmd:
                return ("💬 Open chat", cmd)
    if r.get("package") in ("com.samsung.android.messaging", "com.google.android.apps.messaging"):
        number = _contacts(book).get(sch.clean(r["title"]), "") or (sch.clean(r["title"]) if re.fullmatch(r"\+?[\d -]{7,}", sch.clean(r["title"])) else "")
        if number:
            return ("💬 Open chat", android.cmd_open_sms_thread(number))
    return (f"Open {r['app']}", android.cmd_open_app(r["package"]))


def _chat_jid(title: str) -> str | None:
    """The WhatsApp chat id for a notification title ("Group: Sender" or a contact)."""
    group, _s, _t = grp.parse(title, "", lambda t: False)
    return android.jid_for(group or sch.clean(title))


def whatsapp_reply_card(r: dict, book: dict[str, str], nid: str | None = None,
                        head: str | None = None, body: str | None = None) -> None:
    """A suggested WhatsApp reply. "✏️ Open with reply" opens that chat: a
    contact's with the reply typed in, a group's (which can't be prefilled)
    with the reply copied, to paste. "💬 Open chat" just opens it. Either
    removes the card. Nothing is sent from here."""
    nid = nid or "reply-" + secretary.item_id(r)
    title = r["title"].strip()
    name = dg.LTR + title
    _learn_jid(title)
    jid = _chat_jid(title)
    group = grp.parse(title, "", lambda t: False)[0]
    if group:
        reply = (f"{android.cmd_copy(r['reply'])}; {android.cmd_open_whatsapp_chat(jid)}; "
                 f"{android.BIN}/termux-toast 'Reply copied: paste and send'") if jid \
            else android.cmd_whatsapp_reply(r["reply"], None)      # chat unknown: WhatsApp asks which chat
    else:
        number = ("+" + jid.split("@")[0]) if jid and jid.endswith("@s.whatsapp.net") else book.get(title)  # WhatsApp's own id first
        reply = android.cmd_whatsapp_reply(r["reply"], number)
    second = ("💬 Open chat", android.cmd_open_whatsapp_chat(jid)) if jid else ("📋 Copy reply", android.cmd_copy(r["reply"]))
    if not jid and not group and android.cmd_open_whatsapp_number(number or ""):
        second = ("💬 Open chat", android.cmd_open_whatsapp_number(number))
    _card_with_reminders(nid, head or f"💬 {name}", f"{body or r['summary']}\nSuggested: {r['reply']}",
                         [("✏️ Open with reply", reply), second], "default", which=TONIGHT)


def _learn_jid(title: str) -> None:
    group, _s, _t = grp.parse(title, "", lambda t: False)
    name = group or sch.clean(title)
    if name and not android.jid_for(name):
        android.whatsapp_jids(refresh=True)


def _clear(event) -> None:
    """Get a watched message out of the way: mark it read via Tasker (the app's
    own button), and if the notification is still there a few seconds later,
    snooze it (adb). Either can be missing; then it just stays.

    A chat's own notification (WhatsApp's per-chat one, any Telegram one) is
    never snoozed: Android keeps a snoozed notification snoozed when the app
    updates it, so every later message in that chat would vanish unseen and
    stay unread. Nor is it
    dismissed: if it can't be marked read (tried twice), it stays in the shade
    and the log says so. Only a notification that went once its "Mark as read"
    was pressed counts as cleared (better a notification left up than a chat
    left unread with nothing to show for it)."""
    import time
    key, pkg, title = event.get("key"), event.get("package", ""), event.get("title", "")
    if not key:
        return
    name = android.chat_filter(title) or key
    if pkg in android.NEVER_SNOOZE and not is_chat_notification(pkg, key):
        return          # WhatsApp's summary: clearing it would take the chats under it too

    def gone(wait: float) -> bool:
        time.sleep(wait)
        return not any(n.get("key") == key for n in android.notifications())

    if gone(0):         # already gone (read, swiped): "marked read" would be a guess
        return

    for _attempt in range(2):                    # a second try: most misses were timing
        if not android.mark_read(pkg, title, key):
            break
        if gone(4):
            log.info("marked read: %s", name)
            _clear_orphan_summary(pkg)
            return
    if is_chat_notification(pkg, key):
        # Never a snooze or a dismiss (see above): it stays where it can be seen.
        log.warning("couldn't mark read %s: left in the shade", name)
        return
    if not android.snooze(key):
        log.info("couldn't clear %s (Tasker didn't, adb not reachable): retried once adb is back", key)
        retry_later(key)
        return
    _clear_orphan_summary(pkg)


def is_chat_notification(pkg: str, key: str) -> bool:
    """A notification the app keeps updating with each new message in one chat.
    WhatsApp's summary ("…|1|null|…") isn't one. Neither is Telegram's: its id
    is the account number + 1 (chat ids are large hashes); it carries one
    chat's title but no "Mark as read", and swiping it takes the chat
    notifications under it along, unread."""
    if pkg == TELEGRAM:
        return key.split("|")[2:3] not in (["1"], ["2"], ["3"])
    if pkg in SMS_APPS:
        # One notification per conversation (its tag is the thread), updated with
        # each SMS: a snoozed promo from the card company would hide its next transaction
        # alert. Marked read, or left.
        return True
    return pkg == "com.whatsapp" and key.split("|")[3:4] != ["null"]


def _clear_orphan_summary(pkg: str) -> None:
    """WhatsApp/Telegram keep a summary ("2 messages from 2 chats") over their
    chat notifications. Once no chat notification of that app is left, the
    summary is an empty header: dismiss it too (Tasker, like a swipe). Never a
    snooze: a snoozed summary hides every chat notification posted under it."""
    if pkg != "com.whatsapp":                   # Telegram's chat notifications look like summaries
        return
    mine = [n for n in android.notifications() if n.get("packageName") == pkg]
    chats = [n for n in mine if n["key"].split("|")[3:4] != ["null"]]
    if not chats:
        for n in mine:
            android.dismiss(pkg, n.get("title", ""), n["key"])


def _collect_and_clear(event, groups: list[str], messages: Path, dismiss: bool) -> None:
    """Save a watched group's message, then snooze its notification so it stops
    sitting in the shade (needs adb; if adb isn't up, it just stays)."""
    if not sch.group_of(event.get("title", ""), groups):
        return
    sch.collect(event.get("title", ""), event.get("body", ""), groups, messages)
    _learn_jid(event.get("title", ""))           # before clearing: it reads the live notification
    if dismiss:
        _clear(event)


def _school_groups(school: dict | None) -> list[str]:
    """Class groups: private.toml [school] groups, plus groups assigned the 'school' profile."""
    assigned = [g for g, a in grp.assignments().items() if a.get("profile") == "school"]
    return [*(school or {}).get("groups", []), *assigned]


def school_collect(event, school: dict | None = None, dismiss: bool = True, **_):
    """Save every message from the class groups for the next school digest, and clear it."""
    _collect_and_clear(event, _school_groups(school), sch.MESSAGES, dismiss)


def school_app_collect(event, school: dict | None = None, **_):
    """The school app ([school] apps = {package = "name"}, e.g. Mashov's absence
    and behaviour notes): save each notification for the school digest. The
    notification stays until the digest has seen it (see school_digest)."""
    name = ((school or {}).get("apps") or {}).get(event.get("package", ""))
    if name:
        sch.collect_app(event, name)


def _school_summary_card(school: dict | None, today=None) -> None:
    """The school notes of the last week not yet marked seen, oldest first, on
    one card you can finish (see summaries.py)."""
    rows = list(reversed(sch.notes(days=7)))
    summaries.post("school", "🏫 School summary",
                   [(sch.item_id(r), _note_line(_class_label(school, r.get("group", "")), sch.headline(r))) for r in rows],
                   nid="school-summary")


def _note_line(label: str, line: str) -> str:
    """"Noa · Grade 1: meal today", not "...: Noa: meal today"."""
    name = label.split(" · ")[0]
    if " · " in label and line.startswith(name + ":"):
        line = line[len(name) + 1:].strip()
    return f"{label}: {line}"


def school_digest(event, school: dict | None = None, **_):
    """If school messages came in, pick out what matters. Each action item gets
    a card when the owner needs to hear about it, as Claude judged: now, the
    evening before (something to prepare) or that morning (a timetable change),
    with reminder buttons to push it later. Notes refresh the
    🏫 School summary card. School-app notifications that brought no action are
    then cleared; one with an action stays, to tap through (sign, answer)."""
    try:
        new = sch.digest(kids=(school or {}).get("kids", ""))
    except claude.ClaudeError as e:
        log.warning("school digest failed (messages kept): %s", e)
        return FAILED
    today = datetime.now().date()
    for r in sch.due():                         # each when the owner needs it: now, the evening before, that morning
        g = r.get("group", "")
        _school_card(r, today, _forward_target(school, g), _class_label(school, g), school)
    if any(r.get("kind") == "note" for r in new):
        _school_summary_card(school, today)
    for m in sch.to_clear():
        _clear({"key": m["key"], "package": m.get("package", ""), "title": m.get("title", "")})


SMS_APPS = ("com.samsung.android.messaging", "com.google.android.apps.messaging")


def _sms_match(n: dict) -> tuple[str, str]:
    """(last 9 digits of the sender, the first 30 letters/digits of the text)."""
    body = re.sub(r"^\W*(likely )?spam\W*", "", n.get("content", ""), flags=re.I)
    return re.sub(r"\D", "", n.get("title", ""))[-9:], re.sub(r"\W", "", body)[:30]


def spam_dedupe(event, **_):
    """Truecaller warns about a spam SMS and the Messages app shows the same SMS:
    keep the warning (it's the protection), mark the plain copy read. Runs on
    either one arriving, since either can come first."""
    if event.get("package") not in ("com.truecaller", *SMS_APPS):
        return
    live = android.notifications()
    warned = [_sms_match(n) for n in live if n.get("packageName") == "com.truecaller"
              and "spam" in n.get("content", "")[:25].lower()]
    for n in live:
        if n.get("packageName") not in SMS_APPS:
            continue
        num, text = _sms_match(n)
        if any((num and num == w[0]) or (len(text) >= 12 and text == w[1]) for w in warned):
            log.info("spam SMS from %s: Truecaller's warning kept, the Messages copy marked read", num or "?")
            _clear({"key": n["key"], "package": n["packageName"], "title": n.get("title", "")})


TELEGRAM = "org.telegram.messenger"


def group_collect(event, groups: dict | None = None, school: dict | None = None,
                  dismiss: bool = True, _state: dict | None = None, **_):
    """Every WhatsApp group message (not school groups: school_collect has those).
    A new group is classified once (assigned only when Claude is sure, else the
    owner is asked). Messages from watched groups are saved and snoozed out of
    the shade; 'active' groups and unclassified ones are left alone."""
    telegram = event.get("package") == TELEGRAM
    if telegram and not event.get("title"):
        _clear_pin(event, dismiss, school)
        return
    if telegram:                                  # Telegram channels/groups: the title is the chat
        group, sender, text = sch.clean(event.get("title", "")), "", sch.clean(event.get("body", ""))
        if re.fullmatch(r"\d+ new messages?( from \d+ chats?)?|Telegram|joined Telegram!?", text or group):
            return
    else:
        group, sender, text = grp.parse(event.get("title", ""), event.get("body", ""), grp.known)
    if not group or sch.group_of(group, _school_groups(school)):
        return
    if grp.profile_of(group) in grp.LEFT_ALONE:      # a group the owner is in, or a person's chat
        return
    if grp.profile_of(group) in grp.NATIVE:
        _muted_card(event, group, sender, text, telegram, _state)
        return
    grp.collect(group, sender, text, "telegram" if telegram else "whatsapp")
    if not telegram:
        _learn_jid(event.get("title", ""))
    state = _state if _state is not None else {}
    pending = state.setdefault("pending", {})
    if grp.profile_of(group) is None and group not in pending:
        samples = [m["text"] for m in grp._read(grp.MESSAGES) if m["group"] == group][-5:]
        try:
            c = grp.classify(group, samples, (groups or {}).get("profiles", {}))
        except claude.ClaudeError as e:
            log.warning("couldn't classify group %s: %s", group, e)
            return
        if c["sure"]:
            grp.assign(group, c["profile"], by="auto")
            if c["profile"] == "school":           # from now on school_collect has it; hand over this message
                sch.collect(event.get("title", ""), event.get("body", ""), [group])
                return
        else:
            pending[group] = c["profile"]
            guess = f" Looks like '{c['profile']}' ({c['why']})." if c["profile"] else ""
            grp.ask(group, f"Which profile is group \"{group}\"?{guess}")
            _questions_card()
    if dismiss and grp.watched(group):
        _clear(event)


PINNED = re.compile(r"^(.+?) pinned ")


def _clear_pin(event, dismiss: bool = True, school: dict | None = None) -> None:
    """Telegram's "<chat> pinned "…"" notice (no title; the pinned text is cut
    short and was posted before, so nothing is saved): marked read like the
    chat's other notifications when the chat is watched. Never snoozed (it's the
    chat's own notification); if it can't be marked read it stays, and the
    chat's next message replaces it."""
    import time
    m = PINNED.match(event.get("body") or "")
    key = event.get("key")
    if not (m and key and dismiss):
        return
    group = sch.clean(m.group(1))
    if not grp.watched(group) or grp.profile_of(group) in grp.NATIVE \
            or sch.group_of(group, _school_groups(school)):
        return                     # muted (left as it is) or school (school_collect's): not ours to clear
    if not android.mark_read_pinned(TELEGRAM, group, key):
        log.info("pinned notice in %s: left until its next message replaces it", group)
        return
    time.sleep(4)
    if any(n.get("key") == key for n in android.notifications()):
        log.info("pinned notice in %s: not marked read (Tasker's MARK_READ_TEXT profile set up?)", group)
    else:
        log.info("marked read (pinned notice): %s", group)


def _muted_card(event, group: str, sender: str, text: str, telegram: bool, _state: dict | None) -> None:
    """A muted group only notifies when the owner is replied to or mentioned:
    each such notification is a card of its own, and WhatsApp's notification is
    left exactly as it is. Count headers ("2 new messages")
    and a repeat of the same message are skipped."""
    if not text or re.fullmatch(r"\d+ new messages?( from \d+ chats?)?", text):
        return
    state = _state if _state is not None else {}
    seen = state.setdefault("muted_seen", [])
    mark = f"{group}|{text}"
    if mark in seen:
        return
    seen.append(mark)
    del seen[:-200]
    if not telegram:
        _learn_jid(event.get("title", ""))
    label = sender.lstrip("⤷📣 ").strip() if sender.startswith(("⤷", "📣")) else ""
    who, _, said = text.partition(": ") if ": " in text[:40] else ("", "", text)
    title = f"{'⤷' if sender.startswith('⤷') else '💬'} {who + ' · ' if who else ''}{group}"[:60]
    body = f"{said}\n{dg.LTR}{label or (sender + ' in ' + group if sender else group)}"
    nid = "muted-" + hashlib.sha1(mark.encode()).hexdigest()[:10]
    _own_card(nid, title, body, [_open_group_button({"group": group, "telegram": telegram})])
    grp.log("card", group=group, line=text[:80], why="muted group")


def sweep_handled(event, school: dict | None = None, min_age: float = 60,
                  _state: dict | None = None, **_):
    """Leftover notifications from groups that are handled (watched or school):
    ones that came in before the group was assigned, or while it was being asked
    about. Save the message (repeats are dropped) and clear it like any other.
    Fresh ones (under `min_age` seconds) are left to group/school collect."""
    state = _state if _state is not None else {}
    tried = state.setdefault("tried", [])
    now = datetime.now()
    for n in android.notifications():
        pkg, title, body = n.get("packageName"), n.get("title", ""), n.get("content", "")
        mark = f"{n.get('key')}|{body[:60]}"
        if pkg not in ("com.whatsapp", TELEGRAM) or mark in tried:
            continue
        try:
            if (now - datetime.strptime(n.get("when", ""), "%Y-%m-%d %H:%M:%S")).total_seconds() < min_age:
                continue
        except ValueError:
            pass
        if pkg == TELEGRAM:
            group, sender, text = sch.clean(title), "", sch.clean(body)
        else:
            group, sender, text = grp.parse(title, body, grp.known)
        if not group:
            continue
        if sch.group_of(group, _school_groups(school)):
            sch.collect(title, body, _school_groups(school))
        elif grp.watched(group) and grp.profile_of(group) not in grp.NATIVE:
            grp.collect(group, sender, text, "telegram" if pkg == TELEGRAM else "whatsapp")
        else:
            continue
        tried.append(mark)
        del tried[:-300]
        log.info("sweep: clearing a leftover notification from %s", group)
        _clear({"key": n["key"], "package": pkg, "title": title})


def _group_reply_button(group: str, text: str) -> tuple[str, str]:
    """💬 <reply>: copy the reply and open the group (a group chat can't be
    prefilled), or WhatsApp's share sheet with it if the group's chat id isn't known."""
    jid = android.jid_for(group)
    label = f"📋 Copy “{text if len(text) <= 14 else text[:13] + '…'}”"
    if jid:
        return (label, f"{android.cmd_copy(text)}; {android.cmd_open_whatsapp_chat(jid)}; "
                       f"{android.BIN}/termux-toast 'Copied: paste and send'")
    return (label, android.cmd_whatsapp_reply(text, None))


def _questions_card() -> None:
    qs = grp.open_questions()
    if not qs:
        android.remove("questions")                # all answered: the card goes
        return
    _own_card("questions", f"❓ {len(qs)} question{'s' * (len(qs) != 1)} for you",
              "\n".join(f"{dg.LTR}• {q['question']}" for q in qs[-5:]),
              [("📋 Copy question, open Claude" if len(qs) == 1 else "📋 Copy questions, open Claude",
                f"{android.cmd_copy(grp.questions_prompt(qs[-5:]))}; {android.cmd_open_app('com.anthropic.claude')}; "
                f"{android.BIN}/termux-toast 'Copied: paste into the chat and answer'")], priority="default")


def _open_group_button(r: dict) -> tuple[str, str]:
    """Open the group the item came from: its WhatsApp chat, or Telegram for a
    Telegram channel (it has no WhatsApp chat; the button used to open
    WhatsApp's home screen, 2026-11-16)."""
    jid = android.jid_for(r["group"])
    if r.get("telegram") and not jid:
        return ("💬 Open in Telegram", android.cmd_open_app(TELEGRAM))
    if not jid:                                 # chat id not known yet: only WhatsApp itself
        return ("Open WhatsApp", android.cmd_open_whatsapp_chat(None))
    return (OPEN_CHAT, android.cmd_open_whatsapp_chat(jid))


def _texted_offers(senders) -> list[str]:
    """What senders like a kids' programme's marketing texted lately, so a
    group post of the same programme counts as heard before."""
    names = {s.lower() for s in senders or ()}
    if not names:
        return []
    out = []
    for r in secretary.recent(days=21):
        if re.sub("[\u2066-\u2069\u200e\u200f]", "", r.get("title", "")).strip().lower() in names and r.get("summary"):
            line = f"{r['at'][:10]} {r['summary']}"
            if line not in out:
                out.append(line)
    return out


def group_digest(event, groups: dict | None = None, secretary_cfg: dict | None = None, **_):
    """New messages from watched groups -> important ones get a card each,
    still-worth-knowing ones go on one 📰 summary card, unsure ones become
    questions. Nothing worth anything -> nothing shown."""
    try:
        decided = grp.digest((groups or {}).get("profiles", {}), kids=(groups or {}).get("kids", ""),
                             texted=_texted_offers((secretary_cfg or {}).get("summary_max")))
    except claude.ClaudeError as e:
        log.warning("group digest failed (messages kept): %s", e)
        return FAILED
    for r in decided:
        if r["do"] == "update":
            who = " · ".join(x for x in (r["group"], r["sender"]) if x)
            _own_card(r["nid"], f"🔄 {r['title']}", f"{r['line']}\n{dg.LTR}was: {r['was']}\n{dg.LTR}{who}",
                      [_open_group_button(r)], priority="default")
        if r["do"] == "card":
            buttons = [("🔗 Open link", android.cmd_open_url(r["url"]))] if r["url"] else []
            target = next((t for p, t in ((groups or {}).get("forward") or {}).items()
                           if p in (grp.profile_of(r["group"]) or "").split(",")), None)
            forward = _forward_button(target, f"{r['line']}\n\n{r['text']}") if target else None
            if r.get("replies") and not r.get("telegram"):     # someone asked; the likely answers, ready to paste
                buttons += [_group_reply_button(r["group"], text) for text in r["replies"][:3 - len(buttons)]]
            elif forward:                          # forward or reply, not both: this profile forwards
                buttons.append(forward)
                if "news" in (grp.profile_of(r["group"]) or "").split(","):
                    buttons.append(_open_group_button(r))   # news keeps "Open" too, next to its "↪ To …" button
            else:
                buttons.append(_open_group_button(r))
            who = " · ".join(x for x in (r["group"], r["sender"]) if x)
            body = f"{r['line']}\n{dg.LTR}{who}"
            if r.get("times_from") and buttons:      # say where the ready answer comes from, so it can be checked
                body += "".join(f"\n{dg.LTR}↪ {t}" for t in r["replies"]) + f"\n{dg.LTR}🕒 From {r['times_from']}"
            _own_card(r["nid"], f"📣 {r['title']}", body, buttons)
    if any(r["do"] == "summary" for r in decided):
        _groups_summary()
    if any(r["do"] == "ask" for r in decided):
        _questions_card()


def _groups_summary() -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    summaries.post("groups", "📰 Worth knowing",
                   [(s["nid"], f"{s['at'][11:] if s['at'][:10] == today else s['at'][5:]} {s['line']}")
                    for s in grp.summary(hours=36)], nid="groups-summary")


def news_roundup(event=None, **_):
    """News held for the round-up (groups "daily" lines) goes on the 📰 card:
    with each morning summary, and at 18:00."""
    grp.release()
    _groups_summary()


def ensemble_card(event, claude_session: str = "claude", unknown_after: int = 3,
                  _state: dict | None = None, **_):
    """A card only when Ensemble needs the owner (quiet if everything's good). Running, or stopped with nothing needed: no card (one
    already up is removed). Checked live on the box when Ensemble pushes
    something (its ntfy notification is then cleared) and every few minutes.
    If the box can't be checked `unknown_after` times in a row, a quiet card
    says so, since a need could be hiding behind it."""
    st = ensemble.state()
    state = _state if _state is not None else {}
    if st["kind"] != "unknown" and event.get("package") == "io.heckel.ntfy" and event.get("key"):
        android.snooze(event["key"])
    state["unknown"] = state.get("unknown", 0) + 1 if st["kind"] == "unknown" else 0
    show = st["kind"] == "needs" or (st["kind"] == "unknown" and state["unknown"] >= unknown_after)
    if not show:
        if state.get("shown"):
            android.remove("ensemble")
        state.update(shown=False, kind=st["kind"], last=[st["title"], st["detail"]])
        return
    if state.get("shown") and (st["title"], st["detail"]) == tuple(state.get("last", ())):
        return
    alert = st["kind"] == "needs" and state.get("kind") != "needs"
    state.update(shown=True, kind=st["kind"], last=[st["title"], st["detail"]])
    situation = re.sub(r"[^\w\s,.:;()'/-]", "", f"{st['title']}: {st['detail'].splitlines()[0]}").strip()
    buttons = [("🤖 Ask Claude", android.cmd_ask_claude(
        f"/ensemble-ops {situation}. What's waiting, and what should I click?", claude_session)
        + f"; {android.cmd_remove_notification('ensemble')}")]      # handled in chat now: the card goes
    android.notify(st["title"], dg.LTR + st["detail"], notif_id="ensemble",
                   priority="high" if alert else "low", buttons=buttons)


def clear_noise(event, clear: dict | None = None, **_):
    """Clear (snooze) a notification that matches the [clear] table: a short,
    exact list of known noise. Comes back -> cleared again."""
    n = {"packageName": event.get("package", ""), "title": event.get("title", ""), "content": event.get("body", "")}
    # A blank one is only cleared from a whole-app entry (some apps post blank ones).
    whole_app = n["packageName"] in (clear or {}).get("packages", [])
    if not event.get("key") or not (whole_app or n["title"] or n["content"]) or not dg.is_noise(n, clear):
        return
    if n["packageName"] in android.NEVER_SNOOZE:      # would hide the chats' later messages
        return
    if n["packageName"] in SMS_APPS:                  # a conversation: marked read, never snoozed
        _clear(event)
        return
    if android.snooze(event["key"]):
        log.info("cleared noise: %s", (n["title"] or n["content"])[:40])


# Clears that failed because adb wasn't reachable (after a reboot, until Wireless
# debugging is back on), tried again by the retry-clears rule once it is. Only
# snoozable notifications are queued (never a chat's own: see NEVER_SNOOZE).
RETRY_FILE = VAR / "retry-clears.json"


def retry_later(key: str) -> None:
    if key.split("|")[1:2] and key.split("|")[1] in android.NEVER_SNOOZE:
        return
    try:
        pending = json.loads(RETRY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pending = {}
    pending.setdefault(key, time.time())
    VAR.mkdir(parents=True, exist_ok=True)
    RETRY_FILE.write_text(json.dumps(pending), encoding="utf-8")


def retry_clears(event, max_age_hours: float = 24, **_):
    """Snooze what couldn't be cleared while adb was down, once adb is back.
    Dropped when it's gone from the shade by itself, or after `max_age_hours`."""
    try:
        pending = json.loads(RETRY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not pending or android.adb_output("echo ok", timeout=20) != "ok\n":
        return                                   # nothing queued, or adb still down: next time
    live = {n.get("key") for n in android.notifications()}
    for key, since in list(pending.items()):
        if key not in live or time.time() - since > max_age_hours * 3600:
            del pending[key]
        elif android.snooze(key):
            log.info("cleared once adb was back: %s", key)
            del pending[key]
    RETRY_FILE.write_text(json.dumps(pending), encoding="utf-8")


def clear_noise_sweep(event, clear: dict | None = None, **_):
    """The same as clear_noise, for what's already on the phone: noise posted
    before the daemon started (boot), or that comes back when a snooze runs out
    without being posted again."""
    for n in android.notifications():
        if n.get("key") and not n["key"].startswith("0|com.termux.api|"):   # never our own cards
            clear_noise({"key": n["key"], "package": n.get("packageName", ""), "title": n.get("title", ""),
                         "body": n.get("content", "")}, clear=clear)


def vibrate(event, ms: int = 400, **_):
    android.vibrate(ms)


def toast(event, text: str, **_):
    android.toast(_fmt(text, event))


def log_event(event, **_):
    log.info("event %s", event)


ACTIONS = {
    "backup": lambda event, **kw: backup.run(event, **kw),
    "open_app": open_app,
    "notify": notify,
    "reply_card": reply_card,
    "calendar_card": calendar_card,
    "alert_card": alert_card,
    "notification_digest": notification_digest,
    "school_collect": school_collect,
    "school_digest": school_digest,
    "school_app_collect": school_app_collect,
    "spam_dedupe": spam_dedupe,
    "weather_watch": weather_watch,
    "clear_noise_sweep": clear_noise_sweep,
    "sweep_handled": sweep_handled,
    "retry_clears": retry_clears,
    "group_collect": group_collect,
    "ensemble_card": ensemble_card,
    "clear_noise": clear_noise,
    "group_digest": group_digest,
    "secretary_collect": secretary_collect,
    "urgent_watch": urgent_watch,
    "parcel_watch": parcel_watch,
    "weekly_ideas": weekly_ideas,
    "calendar_copy": calendar_copy,
    "after_quiet": after_quiet,
    "after_shabbat": after_quiet,          # its old name
    "secretary_triage": secretary_triage,
    "reminders_due": reminders_due,
    "news_roundup": news_roundup,
    "vibrate": vibrate,
    "toast": toast,
    "log": log_event,
}
