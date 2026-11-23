import unittest
from datetime import datetime
from unittest import mock

from automations import claude, urgent


def ev(title, body, package="com.whatsapp"):
    return {"source": "notification", "package": package, "title": title, "body": body}


PEOPLE = {"Dana": {"label": "Dana", "who": "their partner"},
          "Shira K": {"label": "Shira", "who": "their daughter Shira"}}


class UrgentTests(unittest.TestCase):
    """A clearly urgent message from their partner opens the chat at once."""

    def setUp(self):
        urgent._seen.clear()
        urgent._last.clear()
        mock.patch.object(urgent, "quiet_now", return_value=False).start()
        self.notify = mock.patch.object(urgent.android, "notify").start()
        self.adb = mock.patch.object(urgent.android, "adb_shell").start()
        self.run = mock.patch.object(urgent, "_run").start()
        self.ask = mock.patch.object(urgent.claude, "ask").start()
        self.in_use = mock.patch.object(urgent.android, "in_use", return_value=True).start()

    def tearDown(self):
        mock.patch.stopall()

    def check(self, e, answer="NO"):
        self.ask.return_value = claude.Answer(answer, [], "")
        return urgent.check(e, PEOPLE, book=lambda: {"Dana": "+972501234567", "Shira K": "+972521234567"},
                            background=False)

    def test_obvious_words_open_the_chat_without_asking_claude(self):
        self.assertTrue(self.check(ev("⁨Dana⁩", "דחוף תתקשר")))
        self.ask.assert_not_called()
        title = self.notify.call_args.args[0]
        self.assertEqual(title, "🚨 Dana: urgent (WhatsApp)")
        labels = [b[0] for b in self.notify.call_args.kwargs["buttons"]]
        self.assertEqual(labels, ["📞 Call"])                         # tapping the card opens her chat
        self.assertIn("wa.me/972501234567", self.notify.call_args.kwargs["on_tap"])
        self.assertEqual(self.notify.call_args.args[1], "דחוף תתקשר")    # no model asked: the start of it
        cmds = [c.args[0] for c in self.run.call_args_list]
        self.assertTrue(any("wa.me/972501234567" in c for c in cmds))
        self.assertTrue(any("termux-tts-speak -s ALARM" in c for c in cmds))
        self.adb.assert_called_once()

    def test_claude_decides_the_rest(self):
        self.check(ev("Dana", "Can you get bread on the way home?"), "NO")
        self.notify.assert_not_called()
        self.check(ev("Dana", "Ben fell off the slide, bleeding, come now"), "URGENT: Ben hurt, come now")
        self.notify.assert_called_once()
        self.assertEqual(self.notify.call_args.args[1], "Ben hurt, come now")    # a summary, not the message again

    def test_a_voice_message_or_photo_alone_is_not_judged(self):
        for body in ("🎤 Voice message (0:46)", "📷 Photo", "📷 Sent a photo", "🎥 Video (1:02)"):
            self.assertTrue(self.check(ev("Dana", body), "MAYBE: unclear voice message"))
        self.ask.assert_not_called()
        self.notify.assert_not_called()
        self.check(ev("Dana", "📷 Ben fell, look at his arm, come now"), "URGENT: Ben hurt, come now")
        self.assertIn("Ben fell, look at his arm", self.ask.call_args.args[0])
        self.assertNotIn("📷", self.ask.call_args.args[0])

    def test_not_urgent_said_outright(self):
        self.check(ev("Dana", "Not urgent, but call the plumber"))
        self.notify.assert_not_called()
        self.ask.assert_not_called()

    def test_only_her_own_chat(self):
        self.assertFalse(self.check(ev("Family: Dana", "דחוף")))
        self.assertFalse(self.check(ev("Someone", "urgent")))
        self.assertFalse(self.check(ev("Dana", "urgent", "com.facebook.katana")))
        self.notify.assert_not_called()
        self.assertTrue(self.check(ev("Dana (2 messages)", "urgent")))

    def test_a_burst_alarms_once_and_repeats_are_skipped(self):
        self.check(ev("Dana", "urgent!"))
        self.check(ev("Dana", "urgent!"))                  # WhatsApp re-posting the same message
        self.check(ev("Dana", "hospital, come"))
        self.assertEqual(self.notify.call_count, 2)          # the card is updated
        self.assertEqual(self.adb.call_count, 1)             # but the screen wakes, and she's opened, once

    def test_locked_phone_opens_the_chat_on_unlock(self):
        self.in_use.side_effect = [False, False, True]
        self.assertTrue(urgent._open_when_unlocked("OPEN", wait=60, _sleep=lambda s: None))
        self.run.assert_called_once_with("OPEN")

    def test_locked_alert_waits_for_unlock(self):
        self.in_use.return_value = False
        with mock.patch.object(urgent.threading, "Thread") as thread:
            self.check(ev("Dana", "urgent"))
        self.assertIs(thread.call_args.kwargs["target"], urgent._open_when_unlocked)
        cmds = [c.args[0] for c in self.run.call_args_list]
        self.assertFalse(any("wa.me" in c for c in cmds))        # not opened behind the lock screen
        self.assertTrue(any("tts-speak" in c for c in cmds))

    def test_a_child_by_sms_with_their_own_name(self):
        """Owner: the same for the kids; only Ari has WhatsApp, the others text."""
        self.check(ev("\u2068Shira K\u2069", "Can you come get me, I feel sick", "com.samsung.android.messaging"),
                   "URGENT")
        self.assertIn("their daughter Shira", self.ask.call_args.args[0])
        self.assertEqual(self.notify.call_args.args[0], "🚨 Shira: urgent (SMS)")
        self.assertTrue(any("Urgent message from Shira" in c.args[0] for c in self.run.call_args_list))
        self.assertTrue(any("smsto:+972521234567" in c.args[0] for c in self.run.call_args_list))

    def test_whatsapp_and_sms_each_open_their_own_app(self):
        """Always the app they wrote in, even within one burst."""
        self.check(ev("Dana", "urgent, on WhatsApp"))
        self.check(ev("Dana", "urgent, by SMS", "com.samsung.android.messaging"))
        opened = [c.args[0] for c in self.run.call_args_list if "tts-speak" not in c.args[0]]
        self.assertEqual(len(opened), 2)
        self.assertIn("wa.me/972501234567", opened[0])
        self.assertIn("smsto:+972501234567", opened[1])
        ids = [c.kwargs["notif_id"] for c in self.notify.call_args_list]
        self.assertEqual(ids, ["urgent-Dana-WhatsApp", "urgent-Dana-SMS"])

    def test_sms_opens_the_messages_thread(self):
        self.check(ev("Dana", "emergency", "com.samsung.android.messaging"))
        self.assertTrue(any("smsto" in c.args[0] or "sms" in c.args[0] for c in self.run.call_args_list))


class ShabbatTests(unittest.TestCase):
    def test_shabbat_window(self):
        p = mock.patch.object(urgent.quiet, "_shabbat_place", return_value=(32.08, 34.78))   # [quiet] shabbat, a test location
        p.start()
        self.addCleanup(p.stop)
        self.assertFalse(urgent.quiet_now(datetime(2026, 11, 20, 12, 0)))   # Friday noon
        self.assertTrue(urgent.quiet_now(datetime(2026, 11, 20, 16, 30)))   # Friday after candle lighting
        self.assertTrue(urgent.quiet_now(datetime(2026, 11, 21, 12, 0)))    # Shabbat day
        self.assertFalse(urgent.quiet_now(datetime(2026, 11, 21, 18, 0)))   # out
        self.assertFalse(urgent.quiet_now(datetime(2026, 11, 19, 20, 0)))   # Thursday


if __name__ == "__main__":
    unittest.main()


class CardTextTests(unittest.TestCase):
    def test_card_text(self):
        self.assertEqual(urgent.card_text("x" * 100, "Ari hurt"), "Ari hurt")
        self.assertEqual(urgent.card_text("a\nb"), "a b")
        self.assertEqual(len(urgent.card_text("y" * 100)), 70)


class OthersTests(unittest.TestCase):
    """Anyone else the owner knows gets the alert only when it's
    certainly urgent, and even then silently (like a family "maybe")."""

    BOOK = {"Dana": "+972501234567", "Dana Friend": "+972541234567"}

    def setUp(self):
        urgent._seen.clear()
        urgent._last.clear()
        mock.patch.object(urgent, "quiet_now", return_value=False).start()
        self.notify = mock.patch.object(urgent.android, "notify").start()
        self.adb = mock.patch.object(urgent.android, "adb_shell").start()
        self.run = mock.patch.object(urgent, "_run").start()
        self.ask = mock.patch.object(urgent.claude, "ask").start()
        mock.patch.object(urgent.android, "in_use", return_value=True).start()

    def tearDown(self):
        mock.patch.stopall()

    def check(self, e, answer="NO"):
        self.ask.return_value = claude.Answer(answer, [], "")
        return urgent.check_others(e, PEOPLE, book=lambda: self.BOOK, background=False)

    def test_certainly_urgent_is_a_silent_alert(self):
        self.assertTrue(self.check(ev("Dana Friend", "urgent, call me now"), "URGENT: Dana's car broke down, asks you to call"))
        self.ask.assert_called_once()                         # a word alone doesn't settle it for others
        self.assertIn("not family", self.ask.call_args.args[0])
        title = self.notify.call_args.args[0]
        self.assertEqual(title, "🟠 Dana Friend: urgent (WhatsApp)")
        self.assertNotIn("vibrate", self.notify.call_args.kwargs)
        cmds = [c.args[0] for c in self.run.call_args_list]
        self.assertFalse(any("tts-speak" in c for c in cmds))   # no voice
        self.assertTrue(any("wa.me/972541234567" in c for c in cmds))   # the chat opens, as for a family maybe

    def test_maybe_from_others_is_nothing(self):
        self.check(ev("Dana Friend", "where are you?"), "MAYBE: asks where you are")
        self.notify.assert_not_called()

    def test_everyday_messages_never_reach_claude(self):
        self.assertFalse(self.check(ev("Dana Friend", "see you tomorrow at 8")))
        self.ask.assert_not_called()
        self.notify.assert_not_called()

    def test_only_saved_contacts_in_their_own_chat(self):
        self.assertFalse(self.check(ev("Super Deals", "URGENT: last day of the sale!")))   # not a contact
        self.assertFalse(self.check(ev("Work: Dana Friend", "urgent")))                    # a group
        self.assertFalse(self.check(ev("Dana Friend", "דחוף", "com.facebook.katana")))
        self.ask.assert_not_called()

    def test_family_are_left_to_the_family_check(self):
        self.assertFalse(self.check(ev("Dana", "urgent")))
        self.ask.assert_not_called()

    def test_watch_runs_others_only_when_not_family(self):
        from automations import actions
        with mock.patch.object(actions.urg, "check", return_value=True) as fam, \
                mock.patch.object(actions.urg, "check_others") as others:
            actions.urgent_watch(ev("Dana", "urgent"), urgent={"people": PEOPLE})
            others.assert_not_called()
            fam.return_value = False
            actions.urgent_watch(ev("Dana Friend", "urgent"), urgent={"people": PEOPLE})
            others.assert_called_once()
