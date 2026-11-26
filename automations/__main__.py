"""phone-automations CLI.

    python -m automations run                  # the daemon (bin/automations start runs this in tmux)
    python -m automations validate             # check rules.toml / private.toml
    python -m automations doctor               # is everything in place? (read-only)
    python -m automations replay [--limit N]   # dry-run rules over past SMS: which would have fired
    python -m automations fire RULE            # run a rule's actions now, with a sample event
    python -m automations groups [--log N]     # WhatsApp groups: profiles, open questions, what was done
    python -m automations assign GROUP PROFILE # set a group's profile (answers a question about it)
    python -m automations resolve N            # close open question N (after handling it in chat)
    python -m automations adb [--wait MIN]     # after a reboot: reconnect adb (Wireless debugging on)
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import replace
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import paths
from .paths import VAR
from . import android
from .engine import Engine
from .rules import ConfigError, load

ROOT = Path(__file__).resolve().parent.parent
# VAR (var/, where state is kept) comes from paths.py


def setup_logging(to_file: bool) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if to_file:
        VAR.mkdir(exist_ok=True)
        handlers.append(RotatingFileHandler(VAR / "automations.log", maxBytes=512_000, backupCount=2))
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")


def under_proot() -> bool:
    """Whether this process runs under proot (Ubuntu's proot-distro), not in Termux itself."""
    try:
        with open("/proc/self/status") as f:
            tracer = next((int(l.split()[1]) for l in f if l.startswith("TracerPid:")), 0)
        if not tracer:
            return False
        with open(f"/proc/{tracer}/status") as f:
            return any(l.startswith("Name:") and "proot" in l for l in f)
    except (OSError, ValueError):
        return False


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="automations")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run")
    sub.add_parser("validate")
    sub.add_parser("doctor", help="is everything in place? read-only checks, each with what to do")
    r = sub.add_parser("replay")
    r.add_argument("--limit", type=int, default=200)
    g = sub.add_parser("groups")
    g.add_argument("--log", type=int, default=15)
    asg = sub.add_parser("assign")
    asg.add_argument("group")
    asg.add_argument("profile", help='one profile, or several: "community,news"')
    asg.add_argument("--note", default="", help="the owner's own word on this group")
    sn = sub.add_parser("seen", help="a summary card's ✓ button: mark those lines seen")
    sn.add_argument("name")
    sn.add_argument("ids", help="comma-separated line ids")
    rm = sub.add_parser("remind", help="a card's ⏰ button: bring the card back tonight (evening) or tomorrow (morning)")
    rm.add_argument("nid")
    rm.add_argument("when", help="evening, morning, before (the evening before its day), hour, or HH:MM today")
    sm = sub.add_parser("sms", help="a card's send button: text, check it went, then remove the card")
    sm.add_argument("number")
    sm.add_argument("text")
    sm.add_argument("--card", default="")
    ide = sub.add_parser("idea", help="a 💡 Ideas card's button: yes (build it) or no (don't suggest it again)")
    ide.add_argument("answer", choices=("yes", "no"))
    ide.add_argument("nid")
    cc = sub.add_parser("calcopy", help="a 📅 Copy to Family? card's button: yes (copy it) or no (never)")
    cc.add_argument("answer", choices=("yes", "no"))
    cc.add_argument("event_id", help="one event id, or several separated by commas")
    es = sub.add_parser("ensemble-snooze", help="the Ensemble card's 💤 button: no card until then; then a card only if still needed")
    es.add_argument("minutes", type=float, nargs="?", default=60)
    rs = sub.add_parser("resolve")
    rs.add_argument("n", type=int)
    f = sub.add_parser("fire")
    f.add_argument("rule")
    f.add_argument("--body", default="(manual test)", help="sample message text for the test event")
    ad = sub.add_parser("adb", help="after a reboot: reconnect adb once Wireless debugging is on")
    ad.add_argument("--wait", type=float, default=0, metavar="MINUTES",
                    help="keep trying this long, with a card saying what to turn on")
    a = p.parse_args(argv)

    setup_logging(to_file=a.cmd == "run")
    if a.cmd == "run" and under_proot():
        msg = ("The daemon was started inside Ubuntu (proot), where it can't call Claude and stops with "
               "that session. Start it in Termux itself: bin/automations start")
        logging.getLogger("automations").error(msg)
        from . import android
        android.notify("⚠️ Automations not started", msg, notif_id="automations-proot", priority="default")
        return 1
    if a.cmd == "doctor":                      # works before anything is set up
        from . import doctor
        return 1 if doctor.run() else 0
    if a.cmd == "adb":                         # after a reboot: no config needed
        from . import adbwait
        return adbwait.run(a.wait)
    if a.cmd == "seen":                        # from a tap: no config needed
        from . import summaries
        summaries.mark_seen(a.name, [i for i in a.ids.split(",") if i])
        return 0
    if a.cmd == "calcopy":                     # from a tap: no config needed
        from . import android, calendar_copy
        import hashlib
        ids = [i for i in a.event_id.split(",") if i]          # several: the batch card
        for i in ids:
            calendar_copy.answer(i, a.answer == "yes")
            android.remove("calcopy-" + hashlib.sha1(i.encode()).hexdigest()[:10])
        if len(ids) > 1:
            android.remove("calcopy-batch")
        return 0
    if a.cmd == "idea":                        # from a tap: no config needed
        from . import android, ideas
        ideas.answer(a.nid, a.answer == "yes")
        android.remove(a.nid)
        return 0
    if a.cmd == "sms":                         # from a tap: no config needed
        from . import android
        if android.send_sms_checked(a.number, a.text):
            if a.card:
                android.remove(a.card)
            android.toast(f"Sent: {a.text}")
            return 0
        android.toast("Not sent. Opening Messages with it typed in")
        android.open_sms_draft(a.number, a.text)
        return 1
    if a.cmd == "ensemble-snooze":             # from a tap: no config needed
        from . import android, ensemble
        from datetime import datetime
        until = ensemble.snooze(a.minutes)
        android.remove("ensemble")
        android.toast(f"Ensemble: I'll check again at {datetime.fromtimestamp(until):%H:%M}")
        return 0
    if a.cmd == "remind":                      # from a tap: no config needed
        from . import android, reminders
        due = reminders.snooze(a.nid, a.when)
        android.toast(f"I'll remind you {reminders.describe(due)}" if due else "That card can't come back (too old)")
        return 0
    try:
        if not paths.RULES.exists():
            raise SystemExit(f"No {paths.RULES.name}: copy rules.example.toml to rules.toml and make it yours "
                             "(and private.example.toml to private.toml).")
        settings, rules = load(paths.RULES, ROOT / "private.toml")
    except (ConfigError, OSError, ValueError) as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    if a.cmd in ("groups", "assign", "resolve"):
        from . import groups as grp
        if a.cmd == "assign":
            grp.assign(a.group, a.profile, note=a.note)
            qs = grp._read(grp.QUESTIONS)
            for q in qs:
                if q["status"] == "open" and q["group"] == grp.clean(a.group) and "profile" in q["question"]:
                    q["status"] = f"answered: {a.profile}"
            grp._write(grp.QUESTIONS, qs)
            from .actions import _questions_card
            _questions_card()                      # answered: the ❓ card shrinks or goes
            print(f"{a.group} -> {a.profile}")
            return 0
        if a.cmd == "resolve":
            qs = grp._read(grp.QUESTIONS)
            open_ = [q for q in qs if q["status"] == "open"]
            if not 1 <= a.n <= len(open_):
                print(f"no open question {a.n}", file=sys.stderr)
                return 2
            open_[a.n - 1]["status"] = "resolved"
            grp._write(grp.QUESTIONS, qs)
            from .actions import _questions_card
            _questions_card()
            return 0
        print("Groups:")
        for name, v in sorted(grp.assignments().items(), key=lambda kv: kv[1]["profile"]):
            print(f"  {v['profile']:<16} {name}  ({v['by']}, {v['at']})" + (f"\n{'':<20}note: {v['note']}" if v.get("note") else ""))
        print("Open questions:")
        for i, q in enumerate(grp.open_questions(), 1):
            print(f"  {i}. [{q['group']}] {q['question']}  ({q['at']})")
        print(f"Last {a.log} decisions:")
        for r in (grp._read(grp.LOG)[-a.log:] if a.log > 0 else []):
            print(f"  {r['at']}  {r['what']:<8} {r.get('group', '')}: {r.get('line') or r.get('profile') or r.get('question', '')}")
        return 0

    if a.cmd == "validate":
        for rule in rules:
            print(f"ok  {rule.name}  ({rule.source}, {'on' if rule.enabled else 'off'}, "
                  f"{len(rule.actions)} action(s))")
        return 0

    if a.cmd == "replay":
        eng = Engine(rules, VAR / "replay-state.json", dry_run=True)
        msgs = android.sms_inbox(limit=a.limit)
        for m in reversed(msgs):
            event = {"source": "sms", "id": f"sms:{m['_id']}", "number": m.get("number", ""),
                     "contact": m.get("sender") or "", "body": m.get("body", ""),
                     "received": m.get("received", "")}
            eng.last_fired.clear()          # judge each message on its own
            for name in eng.handle(event):
                print(f"{m.get('received')}  {name}  <- {m.get('body', '')[:70]!r}")
        return 0

    if a.cmd == "fire":
        rule = next((x for x in rules if x.name == a.rule), None)
        if not rule:
            print(f"no rule {a.rule!r}", file=sys.stderr)
            return 2
        # Same actions, no filters, so the sample event always matches.
        unfiltered = replace(rule, enabled=True, numbers=set(), package=None,
                             contains_any=[], regex=None, cooldown_seconds=0,
                             at=[], days=[], every=0)
        # test=True: reply buttons only show a toast instead of texting anyone.
        sample = {"source": rule.source, "id": "manual", "number": "0000000000", "contact": "Test",
                  "body": a.body, "title": "test", "package": "", "test": True}
        Engine([unfiltered], VAR / "fire-state.json").handle(sample)
        return 0

    VAR.mkdir(exist_ok=True)
    Engine(rules, VAR / "state.json").run(settings["poll_seconds"], settings.get("in_use_poll_seconds"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
