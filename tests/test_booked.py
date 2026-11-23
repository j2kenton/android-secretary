"""Confirmations of bookings the owner made: no card of their own; one card
later, only for those that never reached the calendar."""

import unittest
from unittest import mock

from automations import actions, secretary

LIVE = [{"packageName": "com.termux.api", "tag": actions.BOOKED_CARD}]


def rec(title, start, n, **kw):
    return {"kind": "schedule", "booked": True, "app": "Gmail", "package": "com.google.android.gm",
            "title": "Shop", "text": f"Session #{n} is booked", "summary": "s", "suggestion": "x", "key": f"k{n}{title}",
            "event": {"title": f"{title} (#{n})", "start": start, "minutes": 60, "location": ""}, **kw}


class BookedTests(unittest.TestCase):
    def test_parse_reads_booked(self):
        out = secretary.parse('{"i": 0, "kind": "schedule", "summary": "s", "booked": true}\n'
                              '{"i": 1, "kind": "schedule", "summary": "s", "booked": "yes"}', 2)
        self.assertTrue(out[0]["booked"])
        self.assertNotIn("booked", out[1])

    def triage(self, recs, state, where=None):
        with mock.patch.object(actions.secretary, "triage", return_value=recs), \
             mock.patch.object(actions.android, "notifications", return_value=[]), \
             mock.patch.object(actions.android, "dismiss"), \
             mock.patch.object(actions.android, "cmd_calendar_insert", return_value="cal"), \
             mock.patch.object(actions.cal, "where", return_value=where), \
             mock.patch.object(actions.summaries, "post"), \
             mock.patch.object(actions, "_own_card") as card, \
             mock.patch.object(actions, "secretary_card") as sec_card:
            actions.secretary_triage({}, calendars={"Personal": 1, "Family": 2}, _state=state)
        return card, sec_card

    def test_bookings_get_no_card_and_two_emails_are_one_booking(self):
        state = {}
        recs = [rec("Coaching with Sam", "2026-12-08T07:00", 4109),
                rec("Sam / me interview coaching", "2026-12-08T07:00", 4109),
                rec("Coaching with Sam", "2026-12-09T07:00", 4110)]
        card, sec_card = self.triage(recs, state, where={"Personal": False, "Family": False})
        card.assert_not_called()
        sec_card.assert_not_called()
        self.assertEqual([h["event"]["start"] for h in state["booked"]], ["2026-12-08T07:00", "2026-12-09T07:00"])

    def test_a_booking_already_in_the_calendars_isnt_held(self):
        state = {}
        card, sec_card = self.triage([rec("Dentist", "2026-12-08T07:00", 1)], state,
                                     where={"Personal": True, "Family": True})
        sec_card.assert_not_called()
        self.assertEqual(state["booked"], [])

    def check(self, state, now, where, live=()):
        with mock.patch.object(actions.time, "time", return_value=now), \
             mock.patch.object(actions.cal, "where", return_value=where) as w, \
             mock.patch.object(actions.android, "notifications", return_value=list(live)), \
             mock.patch.object(actions.android, "cmd_calendar_insert", return_value="cal"), \
             mock.patch.object(actions.android, "notify") as notify, \
             mock.patch.object(actions.android, "remove") as remove:
            actions._booked_check(state, {"Personal": 1, "Family": 2})
        return w, notify, remove

    def held(self, *starts):
        return {"booked": [{"event": {"title": f"Coaching (#{i})", "start": s, "minutes": 60, "location": ""},
                            "app": "Gmail", "title": "Shop", "text": "", "at": 1000.0}
                           for i, s in enumerate(starts)]}

    def test_nothing_shown_or_checked_until_the_wait_is_over(self):
        state = self.held("2026-12-08T07:00")
        w, notify, remove = self.check(state, 1000 + actions.BOOKED_WAIT - 1, None)
        w.assert_not_called()
        notify.assert_not_called()
        self.assertEqual(len(state["booked"]), 1)

    def test_found_by_then_nothing_is_shown(self):
        state = self.held("2026-12-08T07:00")
        w, notify, remove = self.check(state, 1000 + actions.BOOKED_WAIT, {"Personal": True, "Family": True})
        notify.assert_not_called()
        self.assertEqual(state["booked"], [])

    def test_missing_ones_share_one_card_updated_as_they_turn_up(self):
        state = self.held("2026-12-09T07:00", "2026-12-08T07:00")
        t = 1000 + actions.BOOKED_WAIT
        _w, notify, _r = self.check(state, t, {"Personal": True, "Family": False})
        title, text = notify.call_args.args
        self.assertEqual(title, "📅 2 bookings not in your calendar")
        self.assertIn("Tue 8 Dec 07:00 Coaching (#1) · In Personal; not in Family", text.splitlines()[0])
        self.assertEqual([b[0] for b in notify.call_args.kwargs["buttons"]], ["📅 8 Dec", "📅 9 Dec"])
        self.assertFalse(any("termux-notification-remove" in b[1] for b in notify.call_args.kwargs["buttons"]))

        w, notify, _r = self.check(state, t + 60, {"Personal": True, "Family": False}, LIVE)
        w.assert_not_called()                          # rechecked only every BOOKED_RECHECK
        notify.assert_not_called()

        def one_found(start, title, cals):
            return {"Personal": True, "Family": "#1" in title}
        with mock.patch.object(actions.time, "time", return_value=t + actions.BOOKED_RECHECK), \
             mock.patch.object(actions.cal, "where", side_effect=one_found), \
             mock.patch.object(actions.android, "notifications", return_value=LIVE), \
             mock.patch.object(actions.android, "cmd_calendar_insert", return_value="cal"), \
             mock.patch.object(actions.android, "notify") as notify:
            actions._booked_check(state, {"Personal": 1, "Family": 2})
        self.assertEqual(notify.call_args.args[0], "📅 Booked: Coaching (#0)")
        self.assertEqual([b[0] for b in notify.call_args.kwargs["buttons"]], ["📅 Add to calendar"])

        _w, notify, remove = self.check(state, t + 2 * actions.BOOKED_RECHECK, {"Personal": True, "Family": True}, LIVE)
        remove.assert_called_once_with(actions.BOOKED_CARD)
        self.assertEqual(state["booked"], [])

    def test_a_swiped_card_is_done_with(self):
        state = self.held("2026-12-08T07:00")
        t = 1000 + actions.BOOKED_WAIT
        self.check(state, t, {"Personal": False, "Family": False})
        _w, notify, _r = self.check(state, t + actions.BOOKED_RECHECK, {"Personal": False, "Family": False}, live=())
        notify.assert_not_called()
        self.assertEqual(state["booked"], [])


if __name__ == "__main__":
    unittest.main()
