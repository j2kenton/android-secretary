"""Action tests with Android calls captured, so nothing is posted or sent."""

import unittest
from unittest import mock

from automations import actions, android
from automations import paths

EVENT = {"source": "sms", "id": "sms:42", "number": "+972501234567",
         "contact": "Kid", "body": "Can I have time please?"}
REPLIES = [{"label": "✅ Done", "text": "Done! 🙂"}]


class ReplyCardTests(unittest.TestCase):
    def post(self, event):
        with mock.patch.object(android, "notify") as notify:
            actions.reply_card(event, title="{contact} asked", replies=REPLIES)
        return notify.call_args

    def test_buttons_text_the_sender_only_on_tap(self):
        call = self.post(EVENT)
        (title, content), kw = call.args, call.kwargs
        self.assertEqual((title, content), ("Kid asked", "Can I have time please?"))
        (done_label, done_cmd), (chat_label, chat_cmd) = kw["buttons"]
        self.assertEqual(done_label, "✅ Done")
        self.assertIn("automations sms +972501234567 'Done! 🙂' --card ", done_cmd)   # checked send
        self.assertIn("--card reply-sms:42", done_cmd)                 # removed only once it went
        self.assertIn("smsto:+972501234567", chat_cmd)
        self.assertEqual(kw["on_tap"], chat_cmd)

    def test_test_events_never_send(self):
        call = self.post({**EVENT, "test": True})
        done_cmd = call.kwargs["buttons"][0][1]
        self.assertNotIn("termux-sms-send", done_cmd)
        self.assertIn("termux-toast", done_cmd)

    def test_quotes_hostile_text(self):
        cmd = android.cmd_send_sms("+1", "it's $(rm -rf ~); ok")
        self.assertIn("'it'\"'\"'s $(rm -rf ~); ok'", cmd)


if __name__ == "__main__":
    unittest.main()


class SpamDedupeTests(unittest.TestCase):
    SMS = {"key": "sms1", "packageName": "com.samsung.android.messaging", "title": "\u2068+972500000001\u2069",
           "content": "שלום,\nכרטיסך נחסם מטעמי אבטחה."}
    TC = {"key": "tc1", "packageName": "com.truecaller", "title": "+972500000001",
          "content": "🚨 Spam · שלום, כרטיסך נחסם מטעמי אבטחה."}

    def run_with(self, live, package):
        from automations import actions
        with mock.patch.object(actions.android, "notifications", return_value=live), \
             mock.patch.object(actions, "_clear") as clear:
            actions.spam_dedupe({"package": package})
        return [c.args[0]["key"] for c in clear.call_args_list]

    def test_keeps_the_warning_and_clears_the_sms_copy(self):
        self.assertEqual(self.run_with([self.SMS, self.TC], "com.truecaller"), ["sms1"])
        self.assertEqual(self.run_with([self.SMS, self.TC], "com.samsung.android.messaging"), ["sms1"])

    def test_no_warning_no_change(self):
        self.assertEqual(self.run_with([self.SMS], "com.samsung.android.messaging"), [])
        ok = dict(self.TC, content="Identified by Truecaller")
        self.assertEqual(self.run_with([self.SMS, ok], "com.truecaller"), [])

    def test_matches_by_text_when_the_sender_is_a_name(self):
        sms = dict(self.SMS, title="Bank")
        tc = dict(self.TC, title="Bank")
        self.assertEqual(self.run_with([sms, tc], "com.truecaller"), ["sms1"])


class EnsembleCardTests(unittest.TestCase):
    def card(self, kind):
        st = {"kind": kind, "title": "🔵 Ensemble needs you", "detail": "bugs9, impl-high-review: paused, 1 card waiting\nmore"}
        with mock.patch.object(actions.ensemble, "state", return_value=st), \
             mock.patch.object(actions.android, "notify") as notify:
            actions.ensemble_card({}, _state={})
        return notify.call_args.kwargs["buttons"]

    def test_ask_claude_button_types_the_question_into_the_session(self):
        (label, cmd), _snooze = self.card("needs")
        self.assertEqual(label, "🤖 Ask Claude")
        self.assertIn("send-keys -t claude -l '/ensemble-ops Ensemble needs you: bugs9, impl-high-review: paused, 1 card waiting.", cmd)
        self.assertNotIn("Enter", cmd)                       # the owner sends it
        self.assertIn("termux-notification-remove ensemble", cmd)    # and the card goes

    def test_quiet_unless_it_needs_the_owner(self):
        state = {}
        def run(kind):
            st = {"kind": kind, "title": kind, "detail": "d"}
            with mock.patch.object(actions.ensemble, "state", return_value=st), \
                 mock.patch.object(actions.android, "notify") as notify, \
                 mock.patch.object(actions.android, "remove") as remove:
                actions.ensemble_card({}, _state=state)
            return notify.called, remove.called
        self.assertEqual(run("running"), (False, False))          # nothing up, nothing to do
        self.assertEqual(run("needs"), (True, False))
        self.assertEqual(run("needs"), (False, False))            # same state: no repost
        self.assertEqual(run("running"), (False, True))           # back to running: the card goes
        self.assertEqual(run("stopped"), (False, False))
        self.assertEqual([run("unknown")[0] for _ in range(3)], [False, False, True])


    def test_snooze_means_check_again_in_an_hour(self):
        _ask, (label, cmd) = self.card("needs")
        self.assertEqual(label, "💤 1 hour")
        self.assertTrue(cmd.endswith("ensemble-snooze 60"))
        state, clock = {}, [1000.0]
        def run(kind, title="A", until=0.0):
            st = {"kind": kind, "title": title, "detail": f"waiting {clock[0]}"}   # the detail ticks
            with mock.patch.object(actions.ensemble, "state", return_value=st), \
                 mock.patch.object(actions.ensemble, "snoozed_until", return_value=until), \
                 mock.patch.object(actions.time, "time", return_value=clock[0]), \
                 mock.patch.object(actions.android, "notify") as notify, \
                 mock.patch.object(actions.android, "remove"):
                actions.ensemble_card({}, _state=state)
            return notify.call_args.kwargs["priority"] if notify.called else None
        self.assertEqual(run("needs"), "default")
        clock[0] = 1300.0
        self.assertIsNone(run("needs", until=4600))               # 💤 tapped: nothing, though the text changed
        self.assertIsNone(run("needs", "B", until=4600))          # nor for something new
        clock[0] = 5000.0
        self.assertEqual(run("needs", until=4600), "default")     # time's up, still needed: a nudge
        clock[0] = 6000.0
        self.assertIsNone(run("needs", until=9600))
        clock[0] = 10000.0
        self.assertIsNone(run("running", until=9600))             # time's up, sorted meanwhile: no card
        self.assertEqual(run("needs", until=9600), "default")     # needed again later: a card as usual

    def test_snooze_command_removes_the_card(self):
        from automations import __main__ as cli
        with mock.patch.object(actions.android, "remove") as remove, \
             mock.patch.object(actions.android, "toast") as toast:
            self.assertEqual(cli.main(["ensemble-snooze", "60"]), 0)
        remove.assert_called_once_with("ensemble")
        self.assertIn("check again at", toast.call_args.args[0])
        self.assertGreater(actions.ensemble.snoozed_until(), actions.time.time() + 3500)
        actions.ensemble.SNOOZE.unlink()


class ReplyCardChatTests(unittest.TestCase):
    def card(self, title, jids, book=None):
        r = {"title": title, "text": "x", "summary": "s", "reply": "Not that I've noticed"}
        with mock.patch.object(actions.android, "whatsapp_jids", return_value=jids), \
             mock.patch.object(actions.android, "notify") as notify:
            actions.whatsapp_reply_card(r, book or {})
        self.tap = notify.call_args.kwargs.get("on_tap")
        return {label: cmd for label, cmd in notify.call_args.kwargs["buttons"]}

    def test_group_reply_opens_the_group_with_the_reply_copied(self):
        b = self.card("Cousins Club 🐒: Sam", {"Cousins Club 🐒": "123@g.us"})
        self.assertIn("--es jid 123@g.us", b["✏️ Open with reply"])
        self.assertIn("termux-clipboard-set", b["✏️ Open with reply"])
        self.assertNotIn("android.intent.action.SEND", b["✏️ Open with reply"])
        self.assertNotIn("💬 Open chat", b)                     # tapping the card opens the chat instead
        self.assertIn("--es jid 123@g.us", self.tap)
        self.assertIn("termux-notification-remove", self.tap)
        self.assertTrue(all("termux-notification-remove" in c for c in b.values()))   # every button removes the card

    def test_unknown_group_falls_back_to_the_picker_and_contact_is_prefilled(self):
        self.assertIn("android.intent.action.SEND", self.card("Group: Dan", {})["✏️ Open with reply"])
        b = self.card("MUM", {"MUM": "447700900123@s.whatsapp.net"})
        self.assertIn("wa.me/447700900123?text=", b["✏️ Open with reply"])


class ClearSweepTests(unittest.TestCase):
    def test_sweep_clears_known_noise_already_on_the_phone_never_our_cards(self):
        import tomllib
        from pathlib import Path
        clear = tomllib.loads(paths.RULES.read_text())["clear"]
        live = [{"key": "0|com.termux|1337|null|10473", "packageName": "com.termux", "title": "Termux",
                 "content": "1 session (wake lock held)"},
                {"key": "0|com.termux.api|0|weather|1", "packageName": "com.termux.api", "title": "Termux",
                 "content": "1 session"},
                {"key": "0|com.whatsapp|1|x|1", "packageName": "com.whatsapp", "title": "Dana", "content": "hi"}]
        with mock.patch.object(actions.android, "notifications", return_value=live), \
             mock.patch.object(actions.android, "snooze", return_value=True) as snooze:
            actions.clear_noise_sweep({}, clear=clear)
        self.assertEqual([c.args[0] for c in snooze.call_args_list], ["0|com.termux|1337|null|10473"])


class ClearBlankAndStoreTests(unittest.TestCase):
    """Threads' blank notifications and Galaxy Store's "update available" are hidden."""

    def test_blank_threads_and_store_updates_cleared_other_blanks_kept(self):
        import tomllib
        from pathlib import Path
        clear = tomllib.loads(paths.RULES.read_text())["clear"]
        live = [{"key": "0|com.instagram.barcelona|64278|a|1", "packageName": "com.instagram.barcelona", "title": "", "content": ""},
                {"key": "0|com.sec.android.app.samsungapps|1|b|1", "packageName": "com.sec.android.app.samsungapps",
                 "title": "Galaxy Store", "content": "1 update available"},
                {"key": "0|com.whatsapp|0|c|1", "packageName": "com.whatsapp", "title": "", "content": ""}]
        with mock.patch.object(actions.android, "notifications", return_value=live), \
             mock.patch.object(actions.android, "snooze", return_value=True) as snooze:
            actions.clear_noise_sweep({}, clear=clear)
        self.assertEqual([c.args[0] for c in snooze.call_args_list],
                         ["0|com.instagram.barcelona|64278|a|1", "0|com.sec.android.app.samsungapps|1|b|1"])


class CheckedSmsTests(unittest.TestCase):
    def test_sent_only_when_it_is_in_the_sent_folder(self):
        from automations import android
        sent = [{"number": "+972 50-123-4567", "body": "Done! 🙂"}]
        with mock.patch.object(android, "_run"), mock.patch.object(android, "_json", return_value=sent):
            self.assertTrue(android.send_sms_checked("0501234567", "Done! 🙂", _sleep=lambda s: None))
        with mock.patch.object(android, "_run", side_effect=android.AndroidError("no permission")), \
             mock.patch.object(android, "_json", return_value=[{"number": "0501234567", "body": "ok"}]):
            self.assertFalse(android.send_sms_checked("0501234567", "Done! 🙂", wait=0, _sleep=lambda s: None))



class TapOpensChatTests(unittest.TestCase):
    def post(self, buttons, on_tap=None):
        with mock.patch.object(actions.android, "notify") as notify:
            actions._own_card("n1", "t", "x", buttons, on_tap=on_tap)
        return [b[0] for b in notify.call_args.kwargs["buttons"]], notify.call_args.kwargs["on_tap"]

    def test_a_chat_card_opens_the_chat_when_tapped_and_loses_its_button(self):
        labels, tap = self.post([("💬 Open chat", "openjid"), ("⏰ Remind me tonight", "r")])
        self.assertEqual(labels, ["⏰ Remind me tonight"])
        self.assertTrue(tap.startswith("openjid; ") and "termux-notification-remove n1" in tap)

    def test_an_app_only_button_stays_and_the_tap_does_the_same(self):
        labels, tap = self.post([("💬 Open in Telegram", "opentg")])
        self.assertEqual(labels, ["💬 Open in Telegram"])
        self.assertTrue(tap.startswith("opentg; "))

    def test_other_cards_are_unchanged(self):
        self.assertEqual(self.post([("📲 Open Mashov", "m")]), (["📲 Open Mashov"], None))
        self.assertEqual(self.post([("🌤 Weather", "w")], on_tap="w")[1], "w")

    def test_group_button_says_whatsapp_when_the_chat_is_not_known(self):
        with mock.patch.object(actions.android, "jid_for", return_value=None):
            self.assertEqual(actions._open_group_button({"group": "G"})[0], "Open WhatsApp")
        with mock.patch.object(actions.android, "jid_for", return_value="1@g.us"):
            self.assertEqual(actions._open_group_button({"group": "G"})[0], "💬 Open chat")


class SmsNeverSnoozedTests(unittest.TestCase):
    """An SMS notification is the whole conversation: a snoozed promo would hide
    the sender's next SMS (an CardCo transaction alert). Marked read, or left."""
    KEY = "0|com.samsung.android.messaging|123|com.samsung.android.messaging:MESSAGE_RECEIVED:52|10125"

    def test_sms_is_a_chat_notification(self):
        self.assertTrue(actions.is_chat_notification("com.samsung.android.messaging", self.KEY))

    def test_noise_sms_is_marked_read_not_snoozed(self):
        clear = {"by_package": {"com.samsung.android.messaging": ["(?s)^\\W*CardCo\\W*\\n.*פרסומת"]}}
        ev = {"key": self.KEY, "package": "com.samsung.android.messaging", "title": "⁨CardCo⁩",
              "body": "הטבה חדשה! פרסומת"}
        with mock.patch.object(actions.android, "snooze") as snooze, mock.patch.object(actions, "_clear") as cl:
            actions.clear_noise(ev, clear=clear)
        self.assertFalse(snooze.called)
        self.assertEqual(cl.call_args.args[0]["key"], self.KEY)

    def test_unmarked_sms_stays(self):
        with mock.patch.object(actions.android, "notifications", return_value=[{"key": self.KEY}]), \
             mock.patch.object(actions.android, "mark_read", return_value=False), \
             mock.patch.object(actions.android, "snooze") as snooze, \
             mock.patch.object(actions.android, "dismiss") as dismiss:
            actions._clear({"key": self.KEY, "package": "com.samsung.android.messaging", "title": "CardCo"})
        self.assertFalse(snooze.called or dismiss.called)


class GreetingOnlyTests(unittest.TestCase):
    CFG = {"greetings_only": ["Friend"]}

    def run_collect(self, title, body, package="com.samsung.android.messaging"):
        with mock.patch.object(actions, "_clear") as clear, \
             mock.patch.object(actions.secretary, "collect", return_value=True) as collect:
            actions.secretary_collect({"package": package, "title": title, "body": body, "key": "k"},
                                      secretary_cfg=self.CFG)
        return clear.called, collect.called

    def test_plain_greetings_are_marked_read(self):
        for body in ("שבת שלום", "שבוע טוב!:)", "בוקר ל\"ג בעומר שמח", "מועדים לשמחה(:", "שבת שלום\nשבוע טוב"):
            self.assertEqual(self.run_collect("⁨Friend⁩", body), (True, False), body)

    def test_anything_more_goes_to_the_secretary(self):
        for body in ("שבוע טוב סליחה ששחכתי ליכתוב שבת שלום", "שבוע טוב god wek", "אני לא מרגישה טוב", "תודה"):
            self.assertEqual(self.run_collect("Friend", body), (False, True), body)

    def test_other_senders_and_apps_untouched(self):
        self.assertEqual(self.run_collect("Someone", "שבת שלום"), (False, True))
        self.assertEqual(self.run_collect("Friend", "שבת שלום", package="com.whatsapp"), (False, True))
