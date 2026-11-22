"""Declined-charge alerts and appointment-confirm cards (texts reworded, details invented)."""

import unittest
from unittest import mock

from automations import actions, android
from automations.extract import declined_charge

CARDCO = ("היי,\nרצינו לעדכן שבכרטיס שלך המסתיים בספרות 1234, לא אושרה עסקה בגובה 350.00 USD "
            "בבית העסק SHOP *example. כדי לאמת את העסקה: https://bank.example/verify")
ULTRA = "Thank you from Ultra. Your credit card charge failed but we renewed your plan. Expires at 11:59 PM PT"
CONFIRM = ("שלום ישראל, מזכירים לך את התור שקבעת לדר' שמש רון לתאריך 27/08/2099 בשעה 15:30 "
           "בכתובת הפסגה 1 תל אביב.\nלאישור התור, נא להקיש 1.\nלביטול התור, נא להקיש 5.")
NO_DATE = "שלום ישראל, טרם אישרת את הגעתך לתור שנקבע.\nלאישור התור, נא להקיש 1.\nלביטול התור, נא להקיש 5."


class DeclinedTests(unittest.TestCase):
    def test_reads_amount_and_merchant(self):
        self.assertEqual(declined_charge(CARDCO), {"amount": "350.00 USD", "merchant": "SHOP *example"})

    def test_unclear_decline_falls_back(self):
        self.assertIsNone(declined_charge(ULTRA))
        self.assertIsNone(declined_charge("בוצעה עסקה בסך 100 ש\"ח"))

    def alert(self, body, contact):
        with mock.patch.object(android, "notify") as notify:
            actions.alert_card({"id": "sms:1", "body": body, "contact": contact},
                               extract="declined_charge", title="⚠ Card declined: {amount} at {merchant}",
                               fallback_title="⚠ Card payment failed ({contact})")
        return notify.call_args

    def test_alert_titles_and_link(self):
        call = self.alert(CARDCO, "0501234567")
        self.assertEqual(call.args[0], "⚠ Card declined: 350.00 USD at SHOP *example")
        self.assertIn("termux-open-url https://bank.example/verify", call.kwargs["buttons"][0][1])
        self.assertEqual(self.alert(ULTRA, "6700").args[0], "⚠ Card payment failed (6700)")


class ConfirmTests(unittest.TestCase):
    def card(self, body):
        with mock.patch.object(android, "notify") as notify:
            actions.reply_card({"id": "sms:2", "body": body, "number": "0551234567", "contact": "clinic"},
                               extract="appointment", title="Confirm {appt_title}? {appt_when}",
                               fallback_title="Confirm your appointment? ({contact})",
                               replies=[{"label": "✅ Confirm", "text": "1"}])
        return notify.call_args

    def test_title_names_the_appointment_and_button_texts_1(self):
        call = self.card(CONFIRM)
        self.assertEqual(call.args[0], "Confirm תור לדר' שמש רון? Thu 27/08 15:30")
        labels = [b[0] for b in call.kwargs["buttons"]]
        self.assertEqual(labels, ["✅ Confirm", "💬 Reply"])
        self.assertIn("automations sms 0551234567 1 --card", call.kwargs["buttons"][0][1])

    def test_reminder_without_date_uses_fallback(self):
        self.assertEqual(self.card(NO_DATE).args[0], "Confirm your appointment? (clinic)")


if __name__ == "__main__":
    unittest.main()
