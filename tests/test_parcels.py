import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

from automations import actions, calendars, parcels, reminders

BRANCH = ("להזכירך-משלוח LS123456789CH ג 466  עדיין ממתין לך ביחידת הדואר- רמת אביב- כתובת רמת אביב 22 תל אביב..  "
          "למידע נוסף:  https://example.invalid  לתשומת ליבך , ליחידות הדואר נדרש לזמן תור מראש  תודה, דואר ישראל")
POINT = ("להזכירך-משלוח RS123456789Y ג 452  עדיין ממתין לך ביחידת הדואר- זול טוב רמת אביב(בית עסק)- כתובת הברושים 13 תל אביב."
         "לידיעתך, למידע נוסף לחץ:  https://example.invalid")


class ParcelTests(unittest.TestCase):
    """Parcel reminders with the last day, the post office app and a calendar button."""

    def setUp(self):
        d = Path(tempfile.mkdtemp())
        mock.patch.object(parcels, "STATE", d / "p.json").start()
        mock.patch.object(reminders, "DIR", d).start()
        mock.patch.object(reminders, "CARDS", d / "cards.json").start()

    def tearDown(self):
        mock.patch.stopall()

    def test_parse(self):
        p = parcels.parse("⁨Israel_Post⁩", BRANCH)
        self.assertEqual((p["id"], p["branch"], p["address"], p["pickup_point"], p["reminder"]),
                         ("LS123456789CH", "רמת אביב", "רמת אביב 22 תל אביב", False, True))
        q = parcels.parse("Israel_Post", POINT)
        self.assertEqual((q["branch"], q["pickup_point"]), ("זול טוב רמת אביב", True))
        self.assertIsNone(parcels.parse("Israel_Post", "להזכירך, נקבע תור ליחידת הדואר סניף רמת אביב"))
        self.assertIsNone(parcels.parse("Someone", BRANCH))

    def test_last_day_counts_sunday_to_thursday(self):
        thu = date(2026, 11, 19)
        self.assertEqual(parcels.add_working_days(thu, 1), date(2026, 11, 22))          # Sun
        self.assertEqual(parcels.last_day(parcels.parse("Israel_Post", BRANCH), thu), date(2026, 12, 2))
        self.assertEqual(parcels.last_day(parcels.parse("Israel_Post", POINT), thu), date(2026, 11, 24))

    def test_a_second_reminder_keeps_the_first_last_day(self):
        p = parcels.parse("Israel_Post", BRANCH)
        first = parcels.seen(p, date(2026, 11, 19))
        again = parcels.seen(p, date(2026, 11, 22))
        self.assertEqual(first["last"], again["last"])

    def test_card_buttons_and_comeback(self):
        with mock.patch.object(actions.android, "notify") as notify, \
             mock.patch.object(parcels, "come_back", return_value=datetime(2026, 11, 30, 6, 10)):
            actions.parcel_watch({"package": "com.samsung.android.messaging", "title": "Israel_Post", "body": BRANCH})
        title = notify.call_args.args[0]
        self.assertIn("רמת אביב", title)
        labels = [b[0] for b in notify.call_args.kwargs["buttons"]]
        self.assertEqual(labels, ["📦 Post office app", "📅 Add last day"])
        self.assertIn("com.israelpost.israelpost", notify.call_args.kwargs["buttons"][0][1])
        self.assertIn("android.intent.action.INSERT", notify.call_args.kwargs["buttons"][1][1])
        card = next(iter(reminders._load().values()))
        self.assertEqual(card["due"], "2026-11-30T06:10")

    def test_secretary_leaves_it_to_the_parcel_card(self):
        with mock.patch.object(actions.secretary, "collect") as collect:
            actions.secretary_collect({"package": "com.samsung.android.messaging", "title": "Israel_Post", "body": BRANCH})
        collect.assert_not_called()


class CalendarTests(unittest.TestCase):
    """Check both calendars first; never add by itself."""

    OUT = ("Row: 0 title=Levi physio follow-up session (#71869), begin={b}, calendar_id=4\n"
           "Row: 1 title=Levi physio follow-up session (#71869), begin={b}, calendar_id=6\n"
           "Row: 2 title=Dentist, begin={b}, calendar_id=6\n")

    def where(self, title, ids=(4, 6)):
        start = datetime(2026, 11, 24, 21, 0)
        out = "".join(l for l in self.OUT.format(b=int(start.timestamp() * 1000)).splitlines(True)
                      if any(f"calendar_id={i}" in l for i in ids))
        with mock.patch.object(calendars.android, "adb_output", return_value=out):
            return calendars.where(start, title, {"Personal": 6, "Family": 4})

    def test_found_in_both_by_name_or_booking_number(self):
        self.assertEqual(self.where("Physio with Levi"), {"Personal": True, "Family": True})
        self.assertEqual(self.where("IGotAnOffer session #71869"), {"Personal": True, "Family": True})
        self.assertEqual(self.where("Physio with Levi #71870"), {"Personal": False, "Family": False})

    def test_only_in_one(self):
        found = self.where("Physio with Levi", ids=(4,))
        self.assertEqual(found, {"Personal": False, "Family": True})
        self.assertEqual(calendars.note(found), "In Family; not in Personal")
        self.assertEqual(calendars.note({"Personal": False, "Family": False}), "Not in your calendars yet")

    def test_in_both_is_a_line_not_a_card(self):
        records = [{"kind": "schedule", "event": {"title": "Physio with Levi", "start": "2026-11-24T21:00",
                                                    "minutes": 60, "location": ""}, "suggestion": "Add it"}]
        with mock.patch.object(actions.cal, "where", return_value={"Personal": True, "Family": True}):
            actions._check_calendars(records, {"Personal": 6, "Family": 4})
        self.assertEqual((records[0]["kind"], records[0]["suggestion"]), ("later", "Already in Personal and Family"))


if __name__ == "__main__":
    unittest.main()
