"""Thin wrappers around Termux:API and Android's `am`, so rules never shell out directly.

Everything here runs natively in Termux (not in the proot distro), which is
where the daemon lives. Each call has a timeout: Termux:API can hang when a
permission is missing.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import urllib.parse
import subprocess
from .paths import VAR

PREFIX = os.environ.get("PREFIX", "/data/data/com.termux/files/usr")
BIN = os.path.join(PREFIX, "bin")


class AndroidError(RuntimeError):
    pass


def _run(args: list[str], timeout: float = 30) -> str:
    try:
        out = subprocess.run(
            [os.path.join(BIN, args[0]), *args[1:]],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise AndroidError(f"{args[0]} timed out after {timeout}s") from e
    if out.returncode != 0:
        raise AndroidError(f"{args[0]} exited {out.returncode}: {out.stderr.strip()[:300]}")
    return out.stdout


def _json(args: list[str], timeout: float = 30):
    raw = _run(args, timeout)
    data = json.loads(raw) if raw.strip() else []
    if isinstance(data, dict) and "error" in data:
        raise AndroidError(f"{args[0]}: {data['error']}")
    return data


# ---- reading -------------------------------------------------------------

def sms_inbox(limit: int = 20) -> list[dict]:
    """Newest-first inbox messages: _id, number, sender (contact name), body, received."""
    return _json(["termux-sms-list", "-t", "inbox", "-l", str(limit)])


def contacts() -> dict[str, str]:
    """Contact name -> number (needs Termux:API's contacts permission)."""
    return {c.get("name", ""): c.get("number", "") for c in _json(["termux-contact-list"], timeout=60)}


def notifications() -> list[dict]:
    """Current notifications: id, key, packageName, title, content, when, ongoing."""
    return _json(["termux-notification-list"])


def media_keys() -> set[str]:
    """Keys of media players' notifications (a video or podcast's play/pause
    controls). termux-notification-list can't tell them apart, and a paused
    player isn't 'ongoing', so ask Android which ones are category=transport.
    Empty if adb isn't reachable."""
    out = adb_output("dumpsys notification --noredact | grep 'category=transport' || true")
    return set(re.findall(r"key=(\S+?): Notification\(", out or ""))


def battery() -> dict:
    return _json(["termux-battery-status"])


# ---- acting --------------------------------------------------------------

def open_app(package: str, activity: str | None = None) -> None:
    """Bring an app to the foreground (Termux needs 'display over other apps')."""
    if activity:
        target = ["-n", f"{package}/{activity}"]
    else:
        target = ["-a", "android.intent.action.MAIN",
                  "-c", "android.intent.category.LAUNCHER", "-p", package]
    _run(["am", "start", "--user", "0", *target])


def notify(title: str, content: str, *, notif_id: str | None = None,
           on_tap: str | None = None, buttons: list[tuple[str, str]] = (),
           priority: str = "high", vibrate: str | None = None) -> None:
    """Post a notification. `on_tap` and each button's command are shell
    commands Termux runs only when the owner taps them (max 3 buttons)."""
    # Android trims single-line content to two lines even when expanded; with a
    # line break it shows the whole text when the card is opened.
    if "\n" not in content:
        content += "\n\u200e"
    args = ["termux-notification", "--title", title, "--content", content,
            "--priority", priority]
    if notif_id:
        args += ["--id", notif_id]
    if on_tap:
        args += ["--action", on_tap]
    if vibrate:
        args += ["--vibrate", vibrate]
    for i, (label, command) in enumerate(buttons[:3], start=1):
        args += [f"--button{i}", label, f"--button{i}-action", command]
    _run(args)


# ---- other apps' notifications (needs adb: Wireless debugging) ----

# adb lives in the proot distro. It uses whatever device adb already has; else it
# reconnects to the last one that worked (var/adb-serial: Wireless debugging's
# ip:port, which changes when debugging is toggled or the phone restarts), or to
# 127.0.0.1:5555 if adbd was put on a fixed port with `adb tcpip 5555`.
PROOT_DISTRO = os.path.join(BIN, "proot-distro")
ADB_SERIAL_FILE = str(VAR / "adb-serial")


def in_use() -> bool | None:
    """Is the owner using the phone: screen on and not locked? None if adb isn't
    reachable (callers then keep the slower, screen-off pace)."""
    out = adb_output("dumpsys power | grep -m1 'mWakefulness='; "
                     "dumpsys window policy | grep -m1 -E '^ *showing='", timeout=10)
    if out is None:
        return None
    awake = "mWakefulness=Awake" in out
    locked = re.search(r"^\s*showing=true", out, re.M) is not None
    return awake and not locked


def adb_shell(command: str, timeout: float = 30) -> bool:
    """Run a shell command on the phone as adb's shell user. False if adb isn't reachable."""
    return adb_output(command, timeout) is not None


def adb_output(command: str, timeout: float = 30) -> str | None:
    """Like adb_shell, but returns the command's output (None if adb isn't reachable or it failed)."""
    first = "adb devices | awk 'NR>1 && $2==\"device\" {print $1; exit}'"
    script = f"""
S=$({first})
if [ -z "$S" ]; then
  for c in $(cat {shlex.quote(ADB_SERIAL_FILE)} 2>/dev/null) 127.0.0.1:5555; do
    adb connect "$c" >/dev/null 2>&1
  done
  S=$({first})
fi
[ -n "$S" ] || exit 3
echo "$S" > {shlex.quote(ADB_SERIAL_FILE)}
adb -s "$S" shell {shlex.quote(command)}
"""
    try:
        out = subprocess.run([PROOT_DISTRO, "login", "ubuntu", "--", "bash", "-c", script],
                             capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    # adb's own failures go to stderr ("error: device offline"); stdout is the
    # command's output, which can say "error" (a notification's text) and is fine.
    if out.returncode != 0 or re.search(r"^(adb: )?error", out.stderr, re.M | re.I):
        return None
    return out.stdout


# Tasker profile "Intent Received: phoneautomations.MARK_READ" runs AutoNotification
# Actions with Button Text Filter %button, Package Name Filter %app, Title Filter %chat.
MARK_READ_BUTTON = {"com.whatsapp": "Mark as read", "org.telegram.messenger": "Mark as Read",
                    "com.samsung.android.messaging": "Mark as read"}


def chat_filter(title: str) -> str:
    """A title filter for AutoNotification: the chat name as it appears in the
    notification title (direction marks dropped, nothing else changed), cut at
    the first character a filter might read as a pattern."""
    t = re.sub("[\u200e\u200f\u202a-\u202e\u2066-\u2069]", "", title)
    t = t.split(": ", 1)[0].lstrip("()[]{}*+?|^$\\. ")     # "+97253…": a number's "+"
    return re.split(r"[()\[\]{}*+?|^$\\.]", t, maxsplit=1)[0].strip()


EXTRA_FIELDS = ("subText", "summaryText", "infoText", "conversationTitle", "bigText")


def notification_extras(key: str) -> dict[str, str]:
    """The extra text of the live notification `key` (dumpsys, via adb):
    {field: value} for the EXTRA_FIELDS that are set (e.g. a forum post's
    community or company in subText, the whole post in bigText). Empty if adb
    isn't reachable or the notification is gone."""
    out = adb_output("dumpsys notification --noredact") if key else None
    if not out:
        return {}
    head = key.split("\n", 1)[0]
    found, inside, field = {}, False, None
    for line in out.splitlines():
        if "NotificationRecord(" in line:
            if found or inside:
                break
            inside = f"key={head}" in line and (head.endswith("=") or f"key={key}" in line)
            continue
        if not inside:
            continue
        m = re.match(r"\s*android\.(\w+)=(?:String|SpannableString) \((.*)$", line)
        if m:
            field = m[1] if m[1] in EXTRA_FIELDS else None
            if field:
                found[field] = m[2]
            continue
        if line[:1].isspace():                 # dumpsys indents fields; a value's own lines aren't
            field = None
        elif field:
            found[field] += "\n" + line.strip()
    return {k: v[:-1].strip() if v.endswith(")") else v.strip() for k, v in found.items() if v.strip(") ")}


def notification_actions(key: str) -> list[str] | None:
    """The button labels of the live notification `key` (dumpsys, via adb);
    None if adb isn't reachable or the notification isn't there."""
    out = adb_output("dumpsys notification --noredact")
    if out is None:
        return None
    head = key.split("\n", 1)[0]
    labels, inside, found = [], False, False
    for line in out.splitlines():
        if "NotificationRecord(" in line:
            if found:
                break
            inside = f"key={head}" in line and (head.endswith("=") or f"key={key}" in line)
            found = inside
            continue
        if inside:
            m = re.match(r'\s*\[\d+\] "(.*)" ->', line)
            if m:
                labels.append(m[1])
    return labels if found else None


def dismiss(package: str, title: str, key: str = "") -> bool:
    """Ask Tasker/AutoNotification to cancel another app's notification, as a
    swipe would (the chat stays unread; the app posts afresh for the next
    message, unlike a snooze). Tasker profile "Intent Received:
    phoneautomations.DISMISS" runs AutoNotification Cancel with Package Name
    Filter %app and Title Filter %chat (set up and tested 2026-11-14). Never
    sent without a usable title: an empty filter would cancel all of that app's
    notifications. Returns whether a request was sent."""
    chat = chat_filter(title)
    if not package or len(chat) < 3:
        return False
    _run(["am", "broadcast", "--user", "0", "-a", "phoneautomations.DISMISS",
          "--es", "app", package, "--es", "chat", chat, "--es", "key", key])
    return True


def mark_read(package: str, title: str, key: str = "") -> bool:
    """Ask Tasker/AutoNotification to press the notification's own "Mark as read"
    (the chat is marked read in the app, and the notification goes). Only sent
    when that notification really has the button (WhatsApp's summary copy and
    some channels don't; AutoNotification would just post an error). Returns
    whether a request was sent; the caller checks the notification is gone."""
    button, chat = MARK_READ_BUTTON.get(package), chat_filter(title)
    if not button or len(chat) < 3:
        return False
    if key:
        actions = notification_actions(key)
        if actions is not None:
            match = next((a for a in actions if a.strip().lower() == button.lower()), None)
            if not match:
                return False
            button = match
    _run(["am", "broadcast", "--user", "0", "-a", "phoneautomations.MARK_READ",
          "--es", "app", package, "--es", "chat", chat, "--es", "button", button])
    return True


# Never snoozed: Android keeps a snoozed key snoozed when the app updates it,
# and snoozing a group summary snoozes every notification under it, so a chat's
# later messages would vanish unseen and stay unread (2026-11-14, twice).
NEVER_SNOOZE = ("com.whatsapp", "com.whatsapp.w4b", "org.telegram.messenger")


def snooze(key: str, hours: float = 24 * 7) -> bool:
    """Hide another app's notification (by its key) from the shade. Android has no
    shell 'dismiss' or 'mark as read' for other apps; snoozing is the closest.
    Refused (False) for messaging apps, see NEVER_SNOOZE."""
    if key.split("|")[1:2] and key.split("|")[1] in NEVER_SNOOZE:
        return False
    return adb_shell(f"cmd notification snooze --for {int(hours * 3600_000)} {shlex.quote(key)}")


# ---- shell commands for notification taps (run later, by Termux) ----------

LAUNCHERS_FILE = os.path.join(os.path.dirname(ADB_SERIAL_FILE), "launchers.json")
# Known launch screens; others are looked up once via adb and remembered.
LAUNCHERS = {"com.anthropic.claude": "com.anthropic.claude/.mainactivity.MainActivity",
             "com.whatsapp": "com.whatsapp/.Main",
             "com.google.android.gm": "com.google.android.gm/.ConversationListActivityGmail",
             "com.sec.android.daemonapp": "com.sec.android.daemonapp/com.samsung.android.weather.app.AppLauncherActivity"}


def launcher(package: str) -> str | None:
    """An app's launch activity ("pkg/.Activity"). Termux can't see every app by
    package name (Android's package visibility), so `am start -p` fails for some
    (Claude); naming the activity works."""
    if package in LAUNCHERS:
        return LAUNCHERS[package]
    try:
        cache = json.loads(open(LAUNCHERS_FILE, encoding="utf-8").read())
    except (OSError, ValueError):
        cache = {}
    if package not in cache:
        out = adb_output("cmd package resolve-activity --brief -a android.intent.action.MAIN "
                         f"-c android.intent.category.LAUNCHER {shlex.quote(package)}")
        last = (out or "").strip().splitlines()[-1:] or [""]
        if "/" not in last[0]:
            return None                           # not resolvable now; don't cache a miss
        cache[package] = last[0].strip()
        with open(LAUNCHERS_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f)
    return cache[package]


def cmd_open_app(package: str) -> str:
    component = launcher(package)
    if component:
        return f"{BIN}/am start --user 0 -n {shlex.quote(component)}"
    return (f"{BIN}/am start --user 0 -a android.intent.action.MAIN "
            f"-c android.intent.category.LAUNCHER -p {shlex.quote(package)}")


def cmd_ask_claude(prompt: str, session: str = "claude") -> str:
    """Type `prompt` into the Claude Code session running in Termux's tmux
    session `session` (not sent: the owner reads it and presses Enter), then
    bring Termux up. If that session isn't running, Termux just opens."""
    sock = f"$(ls -d {PREFIX}/var/run/tmux-*/default 2>/dev/null | head -n 1)"
    return (f"{BIN}/tmux -S {sock} send-keys -t {shlex.quote(session)} -l {shlex.quote(prompt)} 2>/dev/null; "
            f"{BIN}/am start --user 0 -n com.termux/.app.TermuxActivity")


def cmd_open_url(url: str) -> str:
    return f"{BIN}/termux-open-url {shlex.quote(url)}"


def cmd_open_sms_thread(number: str) -> str:
    return (f"{BIN}/am start --user 0 -a android.intent.action.SENDTO "
            f"-d {shlex.quote('smsto:' + number)}")


GOOGLE_CALENDAR = "com.google.android.calendar"


def cmd_calendar_insert(title: str, begin_ms: int, end_ms: int, location: str = "",
                        description: str = "", app: str | None = GOOGLE_CALENDAR) -> str:
    """Open a calendar app's 'new event' screen, prefilled; the owner taps Save.

    Tries `app` (Google Calendar by default) and falls back to whichever
    calendar app Android picks if that one isn't installed.
    """
    parts = [f"{BIN}/am start --user 0 -a android.intent.action.INSERT",
             # Google Calendar only accepts the insert when the type is explicit.
             "-d content://com.android.calendar/events -t vnd.android.cursor.dir/event",
             f"--el beginTime {begin_ms} --el endTime {end_ms}",
             f"--es title {shlex.quote(title)}"]
    if location:
        parts.append(f"--es eventLocation {shlex.quote(location)}")
    if description:
        parts.append(f"--es description {shlex.quote(description)}")
    generic = " ".join(parts)
    if not app:
        return generic
    # `am` reports a missing app as "Error: ..." without a reliable exit code.
    return (f'out=$({generic} -p {shlex.quote(app)} 2>&1); '
            f'case "$out" in *Error*) {generic};; esac')


def cmd_send_sms(number: str, text: str) -> str:
    return f"{BIN}/termux-sms-send -n {shlex.quote(number)} {shlex.quote(text)}"


def _same_number(a: str, b: str) -> bool:
    da, db = re.sub(r"\D", "", a or ""), re.sub(r"\D", "", b or "")
    return bool(da) and bool(db) and da[-9:] == db[-9:]


def send_sms_checked(number: str, text: str, wait: float = 15, _sleep=None) -> bool:
    """Send, then look for it in the phone's sent messages: only that counts as
    sent (a tap could say "Sent" while Android was still asking Termux:API for
    the Phone permission, and nothing went)."""
    import time
    sleep = _sleep or time.sleep
    started = time.time()
    try:
        _run(["termux-sms-send", "-n", number, text], timeout=30)
    except AndroidError:
        pass                                   # the Sent folder decides
    while True:
        try:
            sent = _json(["termux-sms-list", "-t", "sent", "-l", "5"]) or []
        except AndroidError:
            sent = []
        for m in sent:
            if m.get("body", "").strip() == text.strip() and _same_number(m.get("number") or m.get("address", ""), number):
                return True
        if time.time() - started > wait:
            return False
        sleep(2)


def cmd_send_sms_checked(number: str, text: str, notif_id: str) -> str:
    """A tap that sends, checks it went, and only then removes the card (else
    Messages opens with the text typed in)."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return (f"{shlex.quote(os.path.join(root, 'bin', 'automations'))} sms {shlex.quote(number)} "
            f"{shlex.quote(text)} --card {shlex.quote(notif_id)}")


def open_sms_draft(number: str, text: str) -> None:
    subprocess.run([os.path.join(BIN, "am"), "start", "--user", "0", "-a", "android.intent.action.SENDTO",
                    "-d", "smsto:" + number, "--es", "sms_body", text], capture_output=True, timeout=20)


# Dialled-abroad prefixes: 00, and Israel's carrier codes 012-019 (no Israeli
# area code starts 01, so "017 44 7970..." is a UK number dialled via 017).
INTL_PREFIX = re.compile(r"^(00|01[2-9])")


def whatsapp_number(number: str, country: str = "972") -> str:
    """'054-123 4567' / '+972 54...' / '017 44 7970...' -> international digits (what wa.me wants)."""
    digits = re.sub(r"\D", "", number)
    if number.strip().startswith("+"):
        return digits
    m = INTL_PREFIX.match(digits)
    if m:
        return digits[m.end():]
    if digits.startswith(country):
        return digits
    return country + digits.lstrip("0")


def cmd_whatsapp_reply(text: str, number: str | None = None) -> str:
    """Open WhatsApp with `text` typed in, ready for the owner to edit and send.
    With a number, straight into that chat; without (groups), WhatsApp asks which chat."""
    intl = whatsapp_number(number) if number else ""
    if not 10 <= len(intl) <= 15:          # not a number WhatsApp can take: let the owner pick the chat
        number = None
    if number:
        url = f"https://wa.me/{intl}?text={urllib.parse.quote(text)}"
        return f"{BIN}/am start --user 0 -a android.intent.action.VIEW -d {shlex.quote(url)} -p com.whatsapp"
    return (f"{BIN}/am start --user 0 -a android.intent.action.SEND -t text/plain "
            f"--es android.intent.extra.TEXT {shlex.quote(text)} -p com.whatsapp")


JIDS_FILE = os.path.join(os.path.dirname(ADB_SERIAL_FILE), "whatsapp-jids.json")


def whatsapp_jids(refresh: bool = False) -> dict[str, str]:
    """WhatsApp chat name -> chat id ("1203...@g.us" for a group), learned from
    the `shortcut=` of WhatsApp's notifications (dumpsys, via adb) and kept in
    var/whatsapp-jids.json. refresh=True reads the current notifications first."""
    try:
        known = json.loads(open(JIDS_FILE, encoding="utf-8").read())
    except (OSError, ValueError):
        known = {}
    if refresh:
        out = adb_output("dumpsys notification --noredact") or ""
        jid, wa = None, False
        for line in out.splitlines():
            if "NotificationRecord(" in line:
                jid, wa = None, "pkg=com.whatsapp " in line
            # A chat notification's tag holds a line break, so its header (and the
            # shortcut) continues on the next line: look on every line of the record.
            m = wa and re.search(r"shortcut=([\w.@-]+@(?:g\.us|s\.whatsapp\.net|newsletter))", line)
            if m:
                jid = m[1]
            t = re.search(r"android\.(?:conversationTitle|title)=String \((.*)\)\s*$", line)
            if jid and t:
                name = re.sub("[\u200e\u200f\u202a-\u202e\u2066-\u2069]", "", t[1]).replace("׳", "'").strip()
                if jid.endswith("@g.us") and ": " in name:
                    name = name.split(": ", 1)[0].strip()      # "Group: Sender" -> group
                name = re.sub(r"\s*\(\d+ (?:new )?messages?\)$", "", name)
                if name and name != "WhatsApp":         # the app's summary copy names no chat
                    known[name] = jid
        with open(JIDS_FILE, "w", encoding="utf-8") as f:
            json.dump(known, f, ensure_ascii=False, indent=1)
    return known


def jid_for(name: str) -> str | None:
    """The chat id for a name (exact, else a chat whose name contains it)."""
    known = whatsapp_jids()
    n = re.sub("[\u200e\u200f\u202a-\u202e\u2066-\u2069]", "", name).replace("׳", "'").strip()
    return known.get(n) or next((j for k, j in known.items() if n and n in k), None)


def cmd_open_whatsapp_number(number: str) -> str | None:
    """Open a person's WhatsApp chat by phone number (their notifications don't
    carry a chat id, unlike groups'). None if it isn't a usable number."""
    intl = whatsapp_number(number) if number else ""
    if not 10 <= len(intl) <= 15:
        return None
    return f"{BIN}/am start --user 0 -a android.intent.action.VIEW -d {shlex.quote('https://wa.me/' + intl)} -p com.whatsapp"


def cmd_open_whatsapp_chat(jid: str | None) -> str:
    """Open a WhatsApp chat (group, contact) by its id; WhatsApp itself if unknown."""
    if not jid:
        return cmd_open_app("com.whatsapp")
    return (f"{BIN}/am start --user 0 -a android.intent.action.VIEW -n com.whatsapp/.Conversation "
            f"--es jid {shlex.quote(jid)}")


def cmd_copy(text: str) -> str:
    return f"{BIN}/termux-clipboard-set {shlex.quote(text)} && {BIN}/termux-toast Copied"


def cmd_remove_notification(notif_id: str) -> str:
    return f"{BIN}/termux-notification-remove {shlex.quote(notif_id)}"


def cmd_toast(text: str) -> str:
    return f"{BIN}/termux-toast {shlex.quote(text)}"


def remove(notif_id: str) -> None:
    """Remove one of our own cards."""
    _run(["termux-notification-remove", notif_id])


def vibrate(ms: int = 400) -> None:
    _run(["termux-vibrate", "-d", str(ms), "-f"])


def toast(text: str) -> None:
    _run(["termux-toast", text])
