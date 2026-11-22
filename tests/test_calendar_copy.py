import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from automations import actions, calendar_copy as cc, claude

NOW = datetime(2026, 11, 19, 12, 0)


def ms(s):
    return int(datetime.fromisoformat(s).timestamp() * 1000)


def row(title, start, cal, **kw):
    return {"title": title, "begin": ms(start), "end": ms(start) + 3600_000, "all_day": False, "calendar_id": cal,
            "free": False, "rrule": None, "sync_id": kw.pop("sid", title), "original": None, "declined": False,
            "cancelled": False, **kw}


class CalendarCopyTests(unittest.TestCase):
    """Copy Personal events to Family, no reminders; ask only when in doubt."""

    def setUp(self):
        mock.patch.object(cc, "STATE", Path(tempfile.mkdtemp()) / "c.json").start()

    def tearDown(self):
        mock.patch.stopall()

    def test_what_is_missing(self):
        rows = [row("Coaching #71869", "2026-11-24T21:00", 6), row("Coaching #71869", "2026-11-24T21:00", 4),
                row("J: Dentist - Dr Levi - Ramat Aviv", "2027-01-13T09:45", 6), row("J: Dentist", "2027-01-13T09:45", 4),
                row("J: Acme sync ", "2027-01-20T14:00", 6), row("J: Acme sync", "2027-01-20T14:00", 4),
                row("New physio", "2026-12-20T12:00", 6),
                row("WK -8 (prep week 1)", "2026-11-22T00:00", 6, all_day=True, free=True),
                row("》》》RENEW PASSPORT SITE!!! 《《《", "2026-12-06T11:00", 6, rrule="FREQ=MONTHLY"),
                row("Declined thing", "2026-12-01T10:00", 6, declined=True),
                row("Old", "2026-11-18T10:00", 6),
                row("Weekly call", "2026-11-23T10:00", 6, rrule="FREQ=WEEKLY", sid="w"),
                row("Weekly call", "2026-11-30T10:00", 6, rrule="FREQ=WEEKLY", sid="w"),
                row("Weekly call", "2026-11-23T10:00", 4, sid="fw")]
        copy, doubt = cc.missing(6, 4, ["RENEW PASSPORT"], now=NOW, rows=rows)
        self.assertEqual([c["title"] for c in copy], ["New physio"])
        self.assertEqual([(d["title"], d["why"]) for d in doubt], [("Weekly call", "repeating: 1 of 2 already in Family")])

    def test_a_whole_missing_series_is_copied_once(self):
        rows = [row("Weekly call", "2026-11-23T10:00", 6, rrule="FREQ=WEEKLY", sid="w"),
                row("Weekly call", "2026-11-30T10:00", 6, rrule="FREQ=WEEKLY", sid="w")]
        copy, doubt = cc.missing(6, 4, now=NOW, rows=rows)
        self.assertEqual([(c["id"], c["recurring"]) for c in copy], [("w", True)])

    def test_copy_asks_for_no_reminders_no_guests_and_tries_once_a_day(self):
        items = [{"id": "abc", "title": "New physio", "start": "2026-12-20 12:00", "recurring": False}]
        seen = {}
        def ask(prompt, tools, **k):
            seen["prompt"], seen["tools"] = prompt, tools
            return claude.Answer('{"id": "abc", "result": "created", "title": "New physio", "start": "2026-12-20 12:00"}', [], "")
        out = cc.copy(items, "me@example.com", "family@group", ask=ask)
        self.assertEqual(out[0]["result"], "created")
        p = seen["prompt"]
        for must in ("useDefaultReminders=false", "overrideReminders, so the copy has no reminders", "NO attendees", 'notificationLevel="NONE"',
                     "Never change or delete", 'calendarId="family@group"', "abc | New physio"):
            self.assertIn(must, p)
        self.assertNotIn("mcp__claude_ai_Google_Calendar__delete_event", seen["tools"])
        self.assertEqual(cc.due(items), [])                      # not again within a day

    def test_asks_first_and_copies_only_after_yes(self):
        item = {"id": "abc", "title": "New physio", "start": "2026-12-20 12:00", "recurring": False}
        kw = dict(calendars={"Personal": 6, "Family": 4}, copy_cfg={"personal": "me@example.com", "family": "family@group"})
        with mock.patch.object(actions.urg, "quiet_now", return_value=False), \
             mock.patch.object(cc, "missing", return_value=([item], [])), \
             mock.patch.object(cc, "copy", return_value=[{**item, "result": "created"}]) as copy, \
             mock.patch.object(actions.android, "notify") as notify:
            actions.calendar_copy({}, **kw)
            copy.assert_not_called()                                         # asked, not copied
            self.assertEqual(notify.call_args.args[0], "📅 Copy to Family? New physio")
            (l1, c1), (l2, c2) = notify.call_args.kwargs["buttons"]
            self.assertEqual((l1, l2), ("✅ Copy", "✋ Don't copy"))
            self.assertIn("calcopy yes abc", c1)
            self.assertIn("fire calendar-copy", c1)
            actions.calendar_copy({}, **kw)
            copy.assert_not_called()                                         # no answer yet: not asked again
            cc.answer("abc", True)                                          # ✅
            actions.calendar_copy({}, **kw)
            copy.assert_called_once()
            self.assertEqual(notify.call_args.args[0], "📅 Copied to Family: 1 (no reminders)")
            self.assertIn("Sun 20/12 12:00 · New physio", notify.call_args.args[1])

    def test_a_batch_is_one_card(self):
        items = [{"id": f"e{i}", "title": f"Coaching #{i}", "start": f"2026-12-0{i} 21:00", "recurring": False} for i in range(1, 6)]
        with mock.patch.object(actions.urg, "quiet_now", return_value=False), \
             mock.patch.object(cc, "missing", return_value=(items, [])), \
             mock.patch.object(actions.android, "notify") as notify:
            actions.calendar_copy({}, calendars={"Personal": 6, "Family": 4}, copy_cfg={"personal": "a", "family": "b"})
        self.assertEqual(notify.call_count, 1)
        self.assertEqual(notify.call_args.args[0], "📅 Copy 5 events to Family?")
        labels = [b[0] for b in notify.call_args.kwargs["buttons"]]
        self.assertEqual(labels, ["✅ Copy all", "✋ None", "🤖 Choose"])
        self.assertIn("calcopy yes e1,e2,e3,e4,e5", notify.call_args.kwargs["buttons"][0][1])

    def test_dont_copy_is_remembered(self):
        cc.answer("w", False)
        self.assertIn("w", cc.never())

    def test_not_on_shabbat(self):
        with mock.patch.object(actions.urg, "quiet_now", return_value=True), \
             mock.patch.object(cc, "missing") as missing:
            actions.calendar_copy({}, calendars={"Personal": 6, "Family": 4}, copy_cfg={"personal": "a", "family": "b"})
        missing.assert_not_called()


if __name__ == "__main__":
    unittest.main()
