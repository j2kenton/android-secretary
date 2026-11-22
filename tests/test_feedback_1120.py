"""Owner feedback 2026-11-20: reminders before a deadline, the silent maybe-urgent alert, big transactions."""

import unittest
from datetime import datetime
from unittest import mock

from automations import actions, reminders, urgent
from automations.claude import Answer


class DeadlineReminderTests(unittest.TestCase):
    NOW = datetime(2026, 11, 19, 11, 55)                 # a Thursday

    def labels(self, title, now=None):
        now = now or self.NOW
        return [b[0] for b in reminders.buttons("n", now, which=("evening",), deadline=reminders.deadline_in(title, now))]

    def test_no_tonight_for_something_due_today(self):
        self.assertEqual(self.labels("Dana: pick up kids by 12:30 today"), ["⏰ At 12:15"])

    def test_too_close_no_button_at_all(self):
        self.assertEqual(self.labels("Dana: pick up kids by 12:30", datetime(2026, 11, 19, 12, 10)), [])

    def test_no_time_still_tonight(self):
        self.assertEqual(self.labels("Mum: call when you can"), ["⏰ Remind me tonight"])

    def test_tomorrow_or_evening_time_keeps_tonight(self):
        self.assertEqual(self.labels("Meet tomorrow at 10:00"), ["⏰ Remind me tonight"])
        self.assertIn("⏰ Remind me tonight", self.labels("Parents' meeting at 20:00"))

    def test_message_times_on_a_chat_card_are_not_deadlines(self):
        self.assertIsNone(reminders.deadline_in(actions._without_times("‎12:24 Dana: 600 for these?"), self.NOW))

    def test_at_time_target(self):
        self.assertEqual(reminders.target("12:15", self.NOW), datetime(2026, 11, 19, 12, 15))


class MaybeUrgentTests(unittest.TestCase):
    def test_levels(self):
        ask = lambda *a, **k: Answer("MAYBE: asks if all is okay with Noa", [], "")
        self.assertEqual(urgent.urgency("Is everything okay with nc?", ask=ask)[0], "maybe")
        self.assertIsNone(urgent.urgent_summary("Is everything okay with nc?", ask=ask))   # not the loud one
        self.assertEqual(urgent.urgency("urgent call me", ask=ask), ("urgent", ""))

    def test_maybe_is_silent(self):
        with mock.patch.object(urgent.android, "notify") as notify, mock.patch.object(urgent, "_run") as run, \
             mock.patch.object(urgent.android, "adb_shell"), mock.patch.object(urgent.android, "in_use", return_value=True):
            urgent._last.clear()
            urgent.alert("Partner", "Is everything okay?", "com.whatsapp", "+972500000000", now=1000.0, summary="s", silent=True)
        self.assertNotIn("vibrate", notify.call_args.kwargs)
        self.assertTrue(notify.call_args.args[0].startswith("🟠"))
        self.assertFalse(any("tts" in c.args[0] for c in run.call_args_list))
        self.assertTrue(run.called)                       # the chat still opens


class BigTransactionTests(unittest.TestCase):
    def test_four_figures_any_currency(self):
        for t in ("זיהינו כעת עסקה בסכום 3499.00 USD", "Paid $3,499 at X", "חיוב בסך 1,200 ש\"ח", "€1500 payment"):
            self.assertTrue(actions.big_amount(t), t)
        for t in ("בוצעה עסקה על סך 300 ₪ בכרטיס 3416", "חיוב בכרטיסך המסתיים בספרות 3416", "הלוואה עד 200,000 ש\"ח"):
            self.assertFalse(actions.big_amount(t), t)

    def test_line_becomes_a_card(self):
        recs = [{"kind": "later", "package": "com.samsung.android.messaging", "title": "CardCo",
                 "text": "זיהינו כעת עסקה בסכום 1,250.00 ₪", "suggestion": ""},
                {"kind": "later", "package": "com.google.android.gm", "title": "Bank", "text": "Paid $5,000", "suggestion": ""}]
        actions._big_transactions(recs)
        self.assertEqual([r["kind"] for r in recs], ["act_now", "later"])


if __name__ == "__main__":
    unittest.main()
