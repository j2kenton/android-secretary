"""Own cards per actionable item; news, secretary events, email items."""

import json
import unittest
from unittest import mock

from automations import actions, secretary
from automations.digest import parse_email_items



def own_buttons(card):
    """A card's own buttons, without the ⏰ reminder ones (those depend on the time of day)."""
    return [b for b in card.call_args.args[3] if not b[0].startswith("⏰")]


# Cards with reminders are remembered in var/reminders: keep tests out of the real one.
_reminders_patch = None


def setUpModule():
    global _reminders_patch
    import tempfile
    from pathlib import Path as _P
    from automations import reminders
    d = _P(tempfile.mkdtemp())
    _reminders_patch = [mock.patch.object(reminders, "DIR", d), mock.patch.object(reminders, "CARDS", d / "cards.json")]
    for p in _reminders_patch:
        p.start()


def tearDownModule():
    for p in _reminders_patch:
        p.stop()

class CardTests(unittest.TestCase):
    def test_own_card_buttons_also_clear_the_card(self):
        with mock.patch.object(actions.android, "notify") as notify:
            actions._own_card("x1", "T", "text", [("Open", "am start foo")])
        _, kw = notify.call_args
        labels = [b[0] for b in kw["buttons"]]
        self.assertEqual(labels, ["Open"])
        self.assertIn("termux-notification-remove x1", kw["buttons"][0][1])   # tapping also clears

    def test_secretary_event_is_validated(self):
        self.assertIsNone(secretary._event({"title": "x", "start": "tomorrow"}))
        e = secretary._event({"title": "Dentist", "start": "2026-11-15T09:00", "minutes": 9999})
        self.assertEqual((e["start"], e["minutes"]), ("2026-11-15T09:00", 1440))

    def test_email_items_carry_thread_ids(self):
        items = parse_email_items("1a1c99ef68fe4fe6 | Clinic: check-up 10:00",
                                  '{"id": "1a1c99ef68fe4fe6"}')
        self.assertEqual(items, [{"thread": "1a1c99ef68fe4fe6", "line": "Clinic: check-up 10:00"}])


if __name__ == "__main__":
    unittest.main()


class SchoolForwardTests(unittest.TestCase):
    def test_school_card_offers_forward_with_original_text(self):
        from automations import school
        r = {"date": "2026-11-13", "line": "No breakfast needed", "group": "שלום כיתה ב'2",
             "from": "רינה", "text": "אין צורך להביא ארוחת בוקר"}
        from datetime import date
        self.assertEqual(school.forward_text({**r, "line": "maths test"}, "Grade 10 (Ofek)", date(2026, 11, 13)),
                         "Reminder: maths test today\n(Grade 10 (Ofek))\n\nאין צורך להביא ארוחת בוקר")
        with mock.patch.object(actions.android, "notify") as notify, \
             mock.patch.object(actions, "_number", return_value="054-1234567"):
            actions._school_card(r, forward_to="Dana")
        _, kw = notify.call_args
        labels = [b[0] for b in kw["buttons"]]
        self.assertEqual(labels[0], "↪ Forward to Dana")            # one action, not forward + group
        self.assertTrue(all(l.startswith("⏰ Remind me") for l in labels[1:]))   # then only reminders
        self.assertIn("wa.me/972541234567", kw["buttons"][0][1])

    def test_forward_to_a_contact_with_its_own_label(self):
        with mock.patch.object(actions, "_number", return_value="0521234567"):
            label, cmd = actions._forward_button({"contact": "Ari Cohen Mb", "label": "Ari"}, "Reminder: x")
        self.assertEqual(label, "↪ Forward to Ari")
        self.assertIn("wa.me/972521234567?text=Reminder", cmd)

    def test_per_group_forward_target_and_group_buttons(self):
        school = {"forward_to": "Dana", "forward": {"כיתות י' אופק": "group:Ari and Parents"},
                  "labels": {"כיתות י' אופק": "Grade 10 (Ofek)"}}
        self.assertEqual(actions._forward_target(school, "כיתות י' אופק הודעות"), "group:Ari and Parents")
        self.assertEqual(actions._forward_target(school, "שלום כיתה ב'2"), "Dana")
        self.assertEqual(actions._class_label(school, "כיתות י' אופק הודעות"), "Grade 10 (Ofek)")
        r = {"date": "2026-11-15", "line": "Test: math", "group": "כיתות י' אופק", "text": "מבחן במתמטיקה"}
        with mock.patch.object(actions.android, "notify") as notify, \
             mock.patch.object(actions.android, "jid_for", return_value=None):
            actions._school_card(r, forward_to="group:Ari and Parents", label="Grade 10 (Ofek)")
        _, kw = notify.call_args
        self.assertEqual(kw["buttons"][0][0], "↪ Forward to Ari and Parents")
        self.assertIn("android.intent.action.SEND", kw["buttons"][0][1])      # chat id unknown: picker
        with mock.patch.object(actions.android, "notify") as notify, \
             mock.patch.object(actions.android, "jid_for", return_value="1203@g.us"):
            actions._school_card(r, forward_to="group:Ari and Parents", label="Grade 10 (Ofek)")
        _, kw = notify.call_args
        self.assertIn("--es jid 1203@g.us", kw["buttons"][0][1])               # known: copy + open that group
        self.assertIn("termux-clipboard-set", kw["buttons"][0][1])


class JidTests(unittest.TestCase):
    def test_chat_ids_from_dumpsys(self):
        import tempfile
        from automations import android
        dump = """    NotificationRecord(0x0ed50f51: pkg=com.whatsapp user=UserHandle{0} id=1 tag=null importance=2 key=0|com.whatsapp|1|null|10563: Notification(channel=silent_notifications_3 shortcut=120363000000000001@g.us contentView=null)
      key=0|com.whatsapp|1|null|10563
                android.title=String (זמני תפילות/שיעורים בשכונה)
    NotificationRecord(0x0faf0542: pkg=com.whatsapp user=UserHandle{0} id=1 tag=xOCM importance=3 key=0|com.whatsapp|1|xOCM|10563: Notification(channel=120363... shortcut=120363000000000001@g.us contentView=null)
                android.title=String (‎‫זמני תפילות/שיעורים בשכונה‬‎: ‎‫~ משה לוי‬‎)
    NotificationRecord(0x1: pkg=com.whatsapp user=UserHandle{0} id=1 key=k: Notification(channel=c shortcut=972541234567@s.whatsapp.net contentView=null)
                android.title=String (Dana)
    NotificationRecord(0x2: pkg=com.termux.api user=UserHandle{0} id=1 key=k2: Notification(channel=c shortcut=null)
                android.title=String (🏫 Today)"""
        f = tempfile.mktemp()
        with mock.patch.object(android, "JIDS_FILE", f), mock.patch.object(android, "adb_output", return_value=dump):
            ids = android.whatsapp_jids(refresh=True)
            self.assertEqual(ids, {"זמני תפילות/שיעורים בשכונה": "120363000000000001@g.us",
                                   "Dana": "972541234567@s.whatsapp.net"})
            self.assertEqual(android.jid_for("זמני תפילות"), "120363000000000001@g.us")


class ApprovalButtonTests(unittest.TestCase):
    SCHOOL = {"approval": {"match": ["אופק"], "app": "com.mashov.main", "label": "📲 Approve in Mashov"}}

    def test_school_approval_opens_the_school_app_and_others_dont(self):
        from automations import actions
        rec = {"kind": "act_now", "app": "Gmail", "package": "com.google.android.gm", "summary": "Parent approval: trip",
               "suggestion": "Sign it", "title": "מורים/x",
               "text": 'מורים/x (אולפנת אופק): אישור הורים לפעילות חדש פורסם: "טיול שכבה ז\'"'}
        with mock.patch.object(actions, "_own_card") as card, \
             mock.patch.object(actions.android, "cmd_open_app", side_effect=lambda p: f"open {p}"):
            actions.secretary_card(rec, {}, self.SCHOOL)
            self.assertEqual(own_buttons(card), [("📲 Approve in Mashov", "open com.mashov.main")])
            actions.secretary_card({**rec, "text": "אישור הורים לטיול (בית ספר אחר)"}, {}, self.SCHOOL)
            self.assertEqual(own_buttons(card), [("Open Gmail", "open com.google.android.gm")])
            actions.secretary_card(rec, {}, None)                                   # not configured
            self.assertEqual(card.call_args.args[3][0][0], "Open Gmail")

    def test_school_card_approval_replaces_forward(self):
        from automations import actions
        r = {"date": "2026-11-16", "line": "Parental approval needed: grade 7 trip", "group": "Ofek 7",
             "from": "", "text": "אישור הורים לטיול", "kind": "act"}
        with mock.patch.object(actions, "_own_card") as card, \
             mock.patch.object(actions.android, "cmd_open_app", side_effect=lambda p: f"open {p}"):
            actions._school_card(r, forward_to="Dana", school={"approval": {**self.SCHOOL["approval"], "match": ["Ofek"]}})
        self.assertEqual(card.call_args.args[3][0], ("📲 Approve in Mashov", "open com.mashov.main"))
        self.assertTrue(all(b[0].startswith("⏰") for b in card.call_args.args[3][1:]))


    def test_school_app_item_opens_the_app_and_replaces_its_notification(self):
        """Shira's Mashov lateness needs a click through to Mashov, not a summary line."""
        from automations import actions
        r = {"date": "2026-11-15", "line": "Shira: late to Science, lesson 3", "group": "Mashov", "from": "",
             "text": "הערת התנהגות: הוזן לנועה איחור במדע", "kind": "action",
             "key": "0|com.mashov.main|0|FCM-Notification:1|10509", "package": "com.mashov.main"}
        with mock.patch.object(actions, "_own_card") as card, mock.patch.object(actions, "_clear") as clear, \
             mock.patch.object(actions.android, "cmd_open_app", side_effect=lambda p: f"open {p}"):
            actions._school_card(r, forward_to="Dana", school={"apps": {"com.mashov.main": "Mashov"}})
        self.assertEqual(card.call_args.args[3][0], ("📲 Open Mashov", "open com.mashov.main"))
        self.assertTrue(all(b[0].startswith("⏰") for b in card.call_args.args[3][1:]))
        self.assertEqual(clear.call_args.args[0]["key"], r["key"])


class EmailStaysEmailTests(unittest.TestCase):
    """An email stays an email. Gmail's own notification is its
    card (a tap opens that email; ours could only open the inbox). A card of ours
    only adds something Gmail can't: a calendar event, a school approval."""
    SCHOOL = ApprovalButtonTests.SCHOOL

    def rec(self, kind, text, **kw):
        return {"kind": kind, "app": "Gmail", "package": "com.google.android.gm", "title": "Someone",
                "text": text, "summary": "s", "suggestion": "x", "key": f"k-{kind}", **kw}

    def test_secretary_cards_for_email_only_with_an_extra(self):
        from automations import actions
        event = {"title": "Dentist", "start": "2026-11-17T10:00", "minutes": 45, "location": ""}
        recs = [self.rec("act_now", "Your appointment is confirmed"),
                self.rec("reply", "Can you send the form?"),
                self.rec("schedule", "Dentist Tue 10:00", event=event),
                self.rec("act_now", 'מורים/x (אולפנת אופק): אישור הורים לפעילות חדש פורסם', key="k-appr")]
        with mock.patch.object(actions.secretary, "triage", return_value=recs), \
             mock.patch.object(actions.android, "notifications", return_value=[]), \
             mock.patch.object(actions.android, "dismiss") as dismiss, \
             mock.patch.object(actions.android, "cmd_open_app", side_effect=lambda p: f"open {p}"), \
             mock.patch.object(actions.android, "cmd_calendar_insert", return_value="cal"), \
             mock.patch.object(actions.summaries, "post"), \
             mock.patch.object(actions, "_own_card") as card:
            actions.secretary_triage({}, school=self.SCHOOL, _state={})
        dismiss.assert_not_called()                                    # Gmail's notifications stay
        buttons = [[b for b in c.args[3] if not b[0].startswith("⏰")] for c in card.call_args_list]
        self.assertEqual(buttons, [[("📅 Add to calendar", "cal")],    # no "Open Gmail": it only opens the inbox
                                   [("📲 Approve in Mashov", "open com.mashov.main")]])

    def test_personal_cards_offer_tonight_only_and_calendar_cards_none(self):
        """"Remind me tonight" on someone-to-get-back-to cards, in a free slot."""
        from datetime import datetime as real
        from automations import actions
        recs = [self.rec("act_now", "Physio asks you to get in touch"),
                self.rec("schedule", "Dentist Tue 10:00", event={"title": "I", "start": "2026-11-18T10:00",
                                                                   "minutes": 45, "location": ""})]
        for r in recs:
            r["package"] = "com.whatsapp"
        with mock.patch.object(actions.android, "notify") as notify, \
             mock.patch.object(actions.android, "cmd_calendar_insert", return_value="cal"), \
             mock.patch.object(actions, "_open_chat_button", return_value=("💬 Open chat", "open")), \
             mock.patch.object(actions, "datetime", wraps=real) as dt:
            dt.now.return_value = real(2026, 11, 17, 12, 0)
            for r in recs:
                actions.secretary_card(r, {"": ""})
        labels = [[b[0] for b in c.kwargs["buttons"]] for c in notify.call_args_list]
        self.assertEqual(labels, [["⏰ Remind me tonight"], ["📅 Add to calendar"]])      # the chat opens on a tap
        self.assertTrue(all(c.kwargs["on_tap"].startswith("open; ") for c in notify.call_args_list))

    def test_morning_email_is_a_line_unless_it_needs_an_approval(self):
        from automations import actions
        with mock.patch.object(actions, "_own_card") as card, \
             mock.patch.object(actions.android, "cmd_open_app", side_effect=lambda p: f"open {p}"):
            self.assertFalse(actions._email_card({"thread": "1a2b3c4d", "line": "Acme: meeting moved to 11:00"}, self.SCHOOL))
            card.assert_not_called()
            self.assertTrue(actions._email_card({"thread": "1a2b3c4e", "line": "Ofek (אופק): אישור הורים לטיול"}, self.SCHOOL))
        self.assertEqual(own_buttons(card), [("📲 Approve in Mashov", "open com.mashov.main")])


class MorningCardTests(unittest.TestCase):
    def test_morning_is_a_paged_summary_without_carded_or_count_lines(self):
        import tempfile
        from pathlib import Path
        from automations import summaries
        rec = {"title": "Snoozed: Accommodations", "text": "x", "key": "k-carded"}
        notifs = [
            {"packageName": "com.google.android.gm", "key": "k-carded", "title": "Snoozed: Accommodations", "content": "x", "when": "2026-11-15 06:00:05"},
            {"packageName": "com.google.android.gm", "key": "k2", "title": "Snoozed: Sam Taylor", "content": "project feedback " + "thanks again for " * 4, "when": "2026-11-15 06:00:04"},
            {"packageName": "com.google.android.gm", "key": "k3", "title": "26 new messages", "content": "", "when": "2026-11-15 06:00:09"},
            {"packageName": "com.termux.api", "tag": secretary.item_id(rec), "key": "t", "title": "❗ card", "content": "c", "when": "2026-11-15 06:15:00"},
        ]
        d = Path(tempfile.mkdtemp())
        with mock.patch.object(summaries, "DIR", d, create=True), \
             mock.patch.object(summaries, "_load", side_effect=lambda n: {}), \
             mock.patch.object(summaries, "_save"), \
             mock.patch.object(summaries, "render"), \
             mock.patch.object(actions.android, "notifications", return_value=notifs), \
             mock.patch.object(actions.android, "media_keys", return_value={"k3"}), \
             mock.patch.object(actions.secretary, "recent", return_value=[rec]), \
             mock.patch.object(actions.ensemble, "state", return_value={"kind": "stopped", "title": "", "detail": "x"}), \
             mock.patch.object(actions, "DIGESTS", d), \
             mock.patch.object(actions, "news_roundup") as roundup, \
             mock.patch.object(actions.summaries, "post") as post:
            actions.notification_digest({}, noise={"packages": ["com.termux.api"], "patterns": [r'^\n?\d+ new messages?\n?$']},
                                        ensemble_status=True)
        name, title, items = post.call_args.args
        roundup.assert_called_once()                     # the morning news round-up comes with it
        lines = [l for _, l in items]
        self.assertEqual(name, "morning")
        self.assertTrue(lines[0].startswith("Gmail · ⏰ Sam Taylor: project feedback"))
        self.assertGreater(len(lines[0]), 55)                          # readable, not cut at 55
        self.assertEqual(len(lines), 1)            # no carded email, count header, "+N" lines or idle Ensemble line


class MorningWeatherTests(unittest.TestCase):
    def test_the_weather_line_can_be_forwarded(self):
        """The morning card's weather line, sent on to a partner."""
        import tempfile
        from pathlib import Path
        res = {"line": "☁ 22°/12°, 2° warmer than yesterday", "new": {}, "day": "2026-11-19"}
        with mock.patch.object(actions.android, "notifications", return_value=[]), \
             mock.patch.object(actions.secretary, "recent", return_value=[]), \
             mock.patch.object(actions.wx, "check", return_value=res), \
             mock.patch.object(actions.sch, "due", return_value=[]), \
             mock.patch.object(actions, "_number", return_value="+972500000000"), \
             mock.patch.object(actions.android, "cmd_whatsapp_reply", return_value="WA") as wa, \
             mock.patch.object(actions, "DIGESTS", Path(tempfile.mkdtemp())), \
             mock.patch.object(actions, "news_roundup"), \
             mock.patch.object(actions.summaries, "post") as post:
            actions.notification_digest({}, weather={"lat": 1, "lon": 2}, school={"forward_to": "Dana"})
        (label, cmd, item), = post.call_args.kwargs["buttons"]
        self.assertEqual((label, cmd), ("↪ Weather to Dana", "WA"))
        self.assertEqual(dict(post.call_args.args[2])[item], res["line"])
        self.assertEqual(wa.call_args.args[0], "Weather today: " + res["line"])


class MediaPlayerTests(unittest.TestCase):
    """A video or podcast player's notification is never taken as a new message
    (a course video became a secretary card)."""

    def test_source_skips_media_players(self):
        from automations import sources
        player = {"key": "0|com.example.learning|1|null|10753", "packageName": "com.example.learning",
                  "title": "Intro to Watercolours", "content": "", "when": "2026-11-15 09:48:24"}
        msg = {"key": "0|com.whatsapp|1|null|10563", "packageName": "com.whatsapp",
               "title": "Dana", "content": "hi", "when": "2026-11-15 09:48:30"}
        src = sources.NotificationSource({"notification": {"seen": []}})
        with mock.patch.object(sources.android, "notifications", return_value=[player, msg]), \
             mock.patch.object(sources.android, "media_keys", return_value={player["key"]}):
            events = src.poll()
        self.assertEqual([e["key"] for e in events], [msg["key"]])

    def test_media_keys_parses_dumpsys(self):
        line = ("    NotificationRecord(0x0e9b27f7: pkg=com.example.learning user=UserHandle{0} id=100000002 "
                "tag=null importance=2 key=0|com.example.learning|100000002|null|10753: Notification("
                "channel=PlaybackService flags=NO_CLEAR category=transport vis=PRIVATE)\n")
        with mock.patch.object(actions.android, "adb_output", return_value=line):
            self.assertEqual(actions.android.media_keys(), {"0|com.example.learning|100000002|null|10753"})
        with mock.patch.object(actions.android, "adb_output", return_value=None):
            self.assertEqual(actions.android.media_keys(), set())


class ShortLabelTests(unittest.TestCase):
    def test_three_buttons_get_short_labels_two_keep_theirs(self):
        three = [("↪ Forward to Ari", "a"), ("⏰ Remind me tonight", "b"), ("⏰ Remind me tomorrow", "c")]
        with mock.patch.object(actions.android, "notify") as notify:
            actions._own_card("x", "t", "b", three)
            actions._own_card("y", "t", "b", three[1:])
        self.assertEqual([b[0] for b in notify.call_args_list[0].kwargs["buttons"]], ["↪ To Ari", "⏰ Tonight", "⏰ Tomorrow"])
        self.assertEqual([b[0] for b in notify.call_args_list[1].kwargs["buttons"]], ["⏰ Remind me tonight", "⏰ Remind me tomorrow"])

