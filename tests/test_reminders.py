"""Cards that come back later, and school items told at the right time."""

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

from automations import reminders, school


class ReminderTests(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.patches = [mock.patch.object(reminders, "DIR", d), mock.patch.object(reminders, "CARDS", d / "cards.json"),
                        mock.patch.object(school, "DIR", d), mock.patch.object(school, "ITEMS", d / "items.jsonl"),
                        mock.patch.object(school, "MESSAGES", d / "messages.jsonl")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_targets(self):
        tue_noon = datetime(2026, 11, 17, 12, 0)                     # a Tuesday
        wed = date(2026, 11, 18)
        self.assertEqual(reminders.target("morning", tue_noon, wed), datetime(2026, 11, 18, 6, 10))
        self.assertEqual(reminders.target("evening", tue_noon, wed), datetime(2026, 11, 17, 19, 30))
        self.assertEqual(reminders.target("evening", tue_noon), datetime(2026, 11, 17, 19, 30))
        self.assertEqual(reminders.target("morning", tue_noon), datetime(2026, 11, 18, 6, 10))
        self.assertEqual(reminders.target("evening", tue_noon, date(2026, 11, 17)), tue_noon)   # already past: now
        self.assertEqual(reminders.target("now", tue_noon, wed), tue_noon)

    def test_never_on_shabbat(self):
        p = mock.patch.object(reminders.quiet, "_shabbat_place", return_value=(32.08, 34.78))   # [quiet] shabbat, a test location
        p.start()
        self.addCleanup(p.stop)
        fri = datetime(2026, 11, 20, 10, 0)
        out = reminders.target("evening", fri)                          # Friday 19:30 is Shabbat
        self.assertEqual(out.date(), date(2026, 11, 21))
        self.assertTrue(17 <= out.hour <= 18)
        sun_item = reminders.target("evening", fri, date(2026, 11, 22))  # the evening before Sunday: Motzei Shabbat
        self.assertEqual(sun_item, datetime(2026, 11, 21, 19, 30))

    def test_buttons(self):
        tue_noon, tue_late = datetime(2026, 11, 17, 12, 0), datetime(2026, 11, 17, 20, 0)
        labels = lambda now, day, **kw: [b[0] for b in reminders.buttons("n", now, day, **kw)]
        self.assertEqual(labels(tue_noon, date(2026, 11, 18)), ["⏰ Remind me tonight", "⏰ Remind me tomorrow"])
        self.assertEqual(labels(tue_late, date(2026, 11, 18)), ["⏰ Remind me tomorrow"])    # evening already here

    def test_today_is_later_today_not_tonight(self):
        tue = date(2026, 11, 17)
        labels = lambda h, m=0, **kw: [b[0] for b in reminders.buttons("n", datetime(2026, 11, 17, h, m), tue, **kw)]
        self.assertEqual(labels(6, 10), ["⏰ In an hour"])
        self.assertEqual(labels(6, 10, deadline=datetime(2026, 11, 17, 13, 30)), ["⏰ At 13:15", "⏰ In an hour"])
        self.assertEqual(labels(13, 0, deadline=datetime(2026, 11, 17, 13, 30)), ["⏰ At 13:15"])   # sooner than an hour
        self.assertEqual(labels(17, 45), ["⏰ Remind me tonight"])          # nothing earlier fits: tonight still helps
        self.assertEqual(labels(20, 0), [])

    def test_further_off_is_tonight_and_the_evening_before(self):
        tue_7 = datetime(2026, 11, 17, 7, 0)
        mon = date(2026, 11, 23)
        b = reminders.buttons("n", tue_7, mon)
        self.assertEqual([x[0] for x in b], ["⏰ Remind me tonight", "⏰ Sun eve"])
        reminders.offer("n", "🏫 Mon 23/11: breakfast list", "t", [], day=mon, now=tue_7)
        self.assertEqual(reminders.snooze("n", "before", tue_7), datetime(2026, 11, 22, 19, 30))
        reminders.offer("n", "🏫 x", "t", [], day=None, now=tue_7)
        self.assertEqual(reminders.snooze("n", "hour", tue_7), datetime(2026, 11, 17, 8, 0))

    def test_snooze_and_come_back_once(self):
        now = datetime(2026, 11, 17, 12, 0)
        reminders.offer("school-x", "🏫 Tomorrow: maths test", "text", [("↪ Forward", "fwd")], day=date(2026, 11, 18), now=now)
        self.assertEqual(reminders.snooze("school-x", "evening", now), datetime(2026, 11, 17, 19, 30))
        self.assertEqual(reminders.pop_due(datetime(2026, 11, 17, 19, 0)), [])
        [c] = reminders.pop_due(datetime(2026, 11, 17, 19, 30))
        self.assertEqual((c["nid"], c["buttons"]), ("school-x", [["↪ Forward", "fwd"]]))
        self.assertEqual(reminders.pop_due(datetime(2026, 11, 17, 20, 0)), [])
        self.assertIsNone(reminders.snooze("unknown", "evening", now))

    def test_school_items_are_told_at_their_time(self):
        school.collect("Grade 10", "שינוי מערכת מחר", ["Grade 10"])
        school.collect("Grade 10", "מבחן במתמטיקה מחר", ["Grade 10"])
        school.collect("Grade 10", "טופס לטיול עד יום ראשון", ["Grade 10"])
        ans = "\n".join([
            '{"i": 0, "kind": "action", "date": "2026-11-18", "tell": "morning", "line": "Ari: timetable change"}',
            '{"i": 1, "kind": "action", "date": "2026-11-18", "tell": "evening", "line": "Ari: maths test"}',
            '{"i": 2, "kind": "action", "date": "2026-11-22", "tell": "now", "line": "Ari: trip form due"}'])
        from automations.claude import Answer
        with mock.patch.object(school.claude, "ask", return_value=Answer(ans, [], "")), \
             mock.patch.object(school, "datetime", wraps=datetime) as dt:
            dt.now.return_value = datetime(2026, 11, 17, 12, 0)
            school.digest(today=date(2026, 11, 17))
        line = lambda rows: [r["line"] for r in rows]
        self.assertEqual(line(school.due(datetime(2026, 11, 17, 12, 0))), ["Ari: trip form due"])
        self.assertEqual(line(school.due(datetime(2026, 11, 17, 19, 30))), ["Ari: maths test"])
        self.assertEqual(line(school.due(datetime(2026, 11, 18, 6, 10))), ["Ari: timetable change"])
        self.assertEqual(school.due(datetime(2026, 11, 18, 7, 0)), [])                    # each once


if __name__ == "__main__":
    unittest.main()
