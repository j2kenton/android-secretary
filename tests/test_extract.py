"""Appointment extraction, on the real message formats (names and addresses invented)."""

import unittest
from datetime import datetime
from unittest import mock

from automations import actions, android
from automations.extract import appointment

CLINIC_NEW = ("שלום ישראל,\n\nקבענו לך תור לדר' כהן דנה\n📆  בתאריך 09/11/2026\n"
                "🕜 בשעה 11:30\n📍 בכתובת הרצל 5, תל אביב\n\nבריאות שלמה, כללית")
CLINIC_OLD = ("לקוח/ה יקר/ה, זוהי תזכורת עבור תור שתואם לישראל לדר' לוי אבי (אורתופדיה) "
                "ב 10/09/26 18:50. לפרטים נוספים ניתן לפנות לאתר כללית")
CLINIC = ("שלום ישראל, מזכירים לך את התור שקבעת לדר' שמש רון לתאריך 27/08/2026 בשעה 15:30 "
          "בכתובת הפסגה 1 תל אביב.\nנשמח לדעת האם בכוונתך להגיע במועד שנקבע.")
POST = ("זומן תור בסניף רמת אביב\n \nבתאריך: 07/09/2026, בשעה 14:11.\n"
        "ניתן להגיע בטווח של 10 דקות מהשעה שנקבעה.\nלשעות פתיחה עדכניות, יש להיכנס לאתר הדואר")
BLOOD_DRIVE = ("תושבי תל אביב היקרים.\nשרותי הדם של מד\"א קוראים לציבור לבוא ולתרום דם\n"
               "יום שלישי 01.09.2026\nבתחנת מגן דוד אדום רח' המ\"ג 7 תל אביב בין השעות 12:00-20:00")
PROMO = "רוצים להיות הבאים בתור? המבצע הוארך גם לחגים. תקופת הפעילות: 1/9-31/10/26"
CANCELLED = "התור שנקבע ל חיסון שפעת, בתאריך 03/11/2026 בשעה 17:00 מבוטל. מתנצלים על אי הנוחות."


class ExtractTests(unittest.TestCase):
    def test_clinic_new_format(self):
        a = appointment(CLINIC_NEW)
        self.assertEqual(a.start, datetime(2026, 11, 9, 11, 30))
        self.assertEqual(a.title, "תור לדר' כהן דנה")
        self.assertEqual(a.location, "הרצל 5, תל אביב")

    def test_clinic_old_format_two_digit_year(self):
        a = appointment(CLINIC_OLD)
        self.assertEqual(a.start, datetime(2026, 9, 10, 18, 50))
        self.assertEqual(a.title, "תור לדר' לוי אבי (אורתופדיה)")

    def test_clinic(self):
        a = appointment(CLINIC)
        self.assertEqual((a.start, a.location), (datetime(2026, 8, 27, 15, 30), "הפסגה 1 תל אביב"))

    def test_post_office(self):
        a = appointment(POST, "Israel_Post")
        self.assertEqual(a.start, datetime(2026, 9, 7, 14, 11))
        self.assertEqual(a.title, "תור בדואר - סניף רמת אביב")

    def test_lookalikes_are_ignored(self):
        for text in (BLOOD_DRIVE, PROMO, "שלום, מה שלומך?"):
            self.assertIsNone(appointment(text), text[:30])

    def test_cancellation_is_flagged(self):
        a = appointment("זומן תור " + CANCELLED)
        self.assertTrue(a.cancelled)


class CalendarCardTests(unittest.TestCase):
    NOW = datetime(2026, 11, 1, 9, 0)

    def card(self, body, state):
        with mock.patch.object(android, "notify") as notify:
            actions.calendar_card({"id": "sms:1", "body": body, "contact": "Clalit"},
                                  _state=state, now=self.NOW)
        return notify

    def test_offers_once_per_appointment_time(self):
        state = {}
        first = self.card(CLINIC_NEW, state)
        second = self.card(CLINIC_NEW.replace("קבענו", "מזכירים שקבענו"), state)
        self.assertEqual(first.call_count, 1)
        self.assertEqual(second.call_count, 0)
        add_cmd = first.call_args.kwargs["buttons"][0][1]
        self.assertIn("android.intent.action.INSERT", add_cmd)
        self.assertIn(f"--el beginTime {int(datetime(2026, 11, 9, 11, 30).timestamp() * 1000)}", add_cmd)

    def test_prefix_only_when_addressed_by_name(self):
        people = {"ישראל": "I"}
        with mock.patch.object(android, "notify") as notify:
            actions.calendar_card({"id": "sms:1", "body": CLINIC_NEW}, _state={},
                                  title_prefixes=people, now=self.NOW)
            actions.calendar_card({"id": "sms:2", "body": CLINIC_OLD.replace("לישראל", "למשה")},
                                  _state={}, title_prefixes=people, now=datetime(2026, 9, 1))
        mine, other = (c.args[0] for c in notify.call_args_list)
        self.assertEqual(mine, "📅 I: תור לדר' כהן דנה")
        self.assertEqual(other, "📅 תור לדר' לוי אבי (אורתופדיה)")

    def test_prefers_google_calendar_with_fallback(self):
        add_cmd = self.card(CLINIC_NEW, {}).call_args.kwargs["buttons"][0][1]
        self.assertIn("-p com.google.android.calendar", add_cmd)
        self.assertIn('case "$out" in *Error*)', add_cmd)

    def test_skips_past_and_cancelled(self):
        self.assertEqual(self.card(CLINIC, {}).call_count, 0)              # 27/08 is past
        self.assertEqual(self.card("זומן תור " + CANCELLED, {}).call_count, 0)


if __name__ == "__main__":
    unittest.main()
