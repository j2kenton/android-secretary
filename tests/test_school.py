"""School WhatsApp groups: collect, digest into dated items, show what's ahead."""

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from automations import school
from automations.claude import Answer

TITLE = "‎‫שלום כיתה ב׳2🌷‬‎: ‎‫רינה לוי מחנכת‬‎"
GROUPS = ["שלום כיתה ב'2"]
TODAY = date(2026, 11, 13)


class SchoolTests(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.patches = [mock.patch.object(school, "DIR", d),
                        mock.patch.object(school, "MESSAGES", d / "m.jsonl"),
                        mock.patch.object(school, "ITEMS", d / "i.jsonl")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_group_match_ignores_direction_marks_and_geresh(self):
        self.assertEqual(school.group_of(TITLE, GROUPS), GROUPS[0])
        self.assertIsNone(school.group_of("Mum", GROUPS))

    def test_collect_only_class_groups_once(self):
        self.assertTrue(school.collect(TITLE, "אין צורך להביא ארוחת בוקר", GROUPS))
        self.assertFalse(school.collect(TITLE, "אין צורך להביא ארוחת בוקר", GROUPS))   # re-post
        self.assertFalse(school.collect("Family: Mum", "bring cake", GROUPS))
        self.assertEqual(school._read(school.MESSAGES)[0]["sender"], "רינה לוי מחנכת")

    def test_announcement_groups_without_sender_and_count_lines(self):
        groups = ["כיתות י' אופק"]
        self.assertFalse(school.collect("כיתות י' אופק הודעות", "2 new messages", groups))
        self.assertTrue(school.collect("כיתות י' אופק הודעות", "מיקוד למבחן", groups))
        self.assertEqual(school._read(school.MESSAGES)[0]["sender"], "")

    def test_digest_keeps_only_items_tied_to_a_real_message(self):
        school.collect(TITLE, "אין צורך להביא ארוחת בוקר", GROUPS)
        school.collect(TITLE, "תודה לכולם!", GROUPS)
        answer = "\n".join(json.dumps(d) for d in [
            {"i": 0, "date": "2026-11-13", "line": "No breakfast needed, Shabbat meal at school"},
            {"i": 7, "date": "2026-11-14", "line": "Made up"},          # no such message
            {"i": 1, "date": "not a date", "line": "x"}])
        with mock.patch.object(school.claude, "ask", return_value=Answer(answer, [], "")):
            new = school.digest(today=TODAY)
        self.assertEqual([r["line"] for r in new], ["No breakfast needed, Shabbat meal at school"])
        self.assertTrue(all(m["done"] for m in school._read(school.MESSAGES)))
        with mock.patch.object(school.claude, "ask") as ask:       # nothing new: no call
            self.assertEqual(school.digest(today=TODAY), [])
            ask.assert_not_called()

    def test_failed_digest_leaves_messages_for_next_time(self):
        school.collect(TITLE, "מחר יציאה לטיול", GROUPS)
        with mock.patch.object(school.claude, "ask", side_effect=school.claude.ClaudeError("x")):
            with self.assertRaises(school.claude.ClaudeError):
                school.digest(today=TODAY)
        self.assertFalse(school._read(school.MESSAGES)[0]["done"])

    def test_current_drops_past_items_and_labels_days(self):
        school._write(school.ITEMS, [
            {"date": "2026-11-12", "line": "old", "added": "1"},
            {"date": "2026-11-15", "line": "trip, bring hat", "added": "2"},
            {"date": "2026-11-13", "line": "no breakfast", "added": "3"},
            {"date": "2026-11-14", "line": "pajama day", "added": "4"}])
        self.assertEqual(school.card_lines(school.current(TODAY), TODAY),
                         ["Today: no breakfast", "Tomorrow: pajama day", "Sun 15/11: trip, bring hat"])
        self.assertEqual(len(school.current(TODAY, until=date(2026, 11, 14))), 2)

    def test_notes_go_to_the_summary_not_the_cards(self):
        school.collect(TITLE, "האגרת השבועית להורים", GROUPS)
        school.collect(TITLE, "מחר יציאה לטיול, להביא כובע", GROUPS)
        answer = "\n".join(json.dumps(d) for d in [
            {"i": 0, "kind": "note", "date": "2026-11-13", "line": "Principal's weekly letter"},
            {"i": 1, "kind": "action", "date": "2026-11-14", "line": "trip, bring a hat"}])
        with mock.patch.object(school.claude, "ask", return_value=Answer(answer, [], "")):
            new = school.digest(today=TODAY)
        self.assertEqual([r["kind"] for r in new], ["note", "action"])
        self.assertEqual([r["line"] for r in school.current(TODAY)], ["trip, bring a hat"])
        self.assertEqual([r["line"] for r in school.notes()], ["Principal's weekly letter"])

    def test_school_app_cleared_unless_it_brought_an_action(self):
        ev = lambda k, body: {"key": k, "package": "com.mashov.main", "title": "הערת התנהגות", "body": body}
        self.assertTrue(school.collect_app(ev("k1", "חיסור מוצדק בלשון"), "Mashov"))
        self.assertTrue(school.collect_app(ev("k2", "יש לחתום על אישור לטיול עד מחר"), "Mashov"))
        self.assertFalse(school.collect_app(ev("k3", ""), "Mashov"))              # the app's group summary
        self.assertFalse(school.collect_app(ev("k1", "חיסור מוצדק בלשון"), "Mashov"))  # same again
        answer = "\n".join(json.dumps(d) for d in [
            {"i": 0, "kind": "note", "date": "2026-11-13", "line": "Ari: excused absence, Language"},
            {"i": 1, "kind": "action", "date": "2026-11-14", "line": "sign the trip permission"}])
        with mock.patch.object(school.claude, "ask", return_value=Answer(answer, [], "")):
            school.digest(today=TODAY)
        self.assertEqual([m["key"] for m in school.to_clear()], ["k1"])
        self.assertEqual(school.to_clear(), [])                                  # only once
        # Mashov re-sends an old note (2026-11-17): the repeat is cleared too, not digested again
        self.assertFalse(school.collect_app(ev("k9", "חיסור מוצדק בלשון"), "Mashov"))
        self.assertEqual([m["key"] for m in school.to_clear()], ["k9"])


class ReplyTests(unittest.TestCase):
    def test_whatsapp_numbers_and_commands(self):
        from automations import android
        self.assertEqual(android.whatsapp_number("054-123 4567"), "972541234567")
        self.assertEqual(android.whatsapp_number("+972 54-123-4567"), "972541234567")
        self.assertEqual(android.whatsapp_number("017447700900123"), "447700900123")    # UK, dialled via 017
        self.assertEqual(android.whatsapp_number("00 1 201 421 1822"), "12014211822")
        self.assertIn("android.intent.action.SEND", android.cmd_whatsapp_reply("hi", "12345"))   # impossible: pick the chat
        self.assertIn("wa.me/972541234567?text=%D7%91", android.cmd_whatsapp_reply("ב", "0541234567"))
        self.assertIn("android.intent.action.SEND", android.cmd_whatsapp_reply("hi", None))


if __name__ == "__main__":
    unittest.main()


class HeadlineTests(unittest.TestCase):
    def test_day_words_are_dropped_from_the_line(self):
        from automations import school as sch
        for line, want in [
            ("Ari: timetable change tomorrow (see image in Ofek grade 10 group)",
             "Ari: timetable change (see image in Ofek grade 10 group)"),
            ("maths test for tomorrow", "maths test"),
            ("Noa: tomorrow's lesson 8 with Ms Levi", "Noa: lesson 8 with Ms Levi"),
            ("no school today, teachers' strike", "no school, teachers' strike"),
            ("Ari: excused absence, Language, lesson 3", "Ari: excused absence, Language, lesson 3"),
        ]:
            self.assertEqual(sch.headline({"line": line}), want)

    def test_forward_text_says_the_day_once(self):
        from datetime import date
        from automations import school as sch
        r = {"line": "maths test tomorrow", "date": "2026-11-18", "group": "", "text": ""}
        self.assertEqual(sch.forward_text(r, today=date(2026, 11, 18)), "Reminder: maths test today")

