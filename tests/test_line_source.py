"""Summary lines say where they came from, and a long line is cut at a word."""

import unittest
from unittest import mock

from automations import actions, groups as grp, secretary


class LineSourceTests(unittest.TestCase):
    def test_clip_cuts_at_a_word(self):
        self.assertEqual(grp.clip("short", 10), "short")
        out = grp.clip("Ninja classes are on Sundays and Tuesdays", 22)
        self.assertEqual(out, "Ninja classes are on…")
        self.assertLessEqual(len(out), 22)

    def test_an_offer_keeps_who_its_for_whole(self):
        line = grp._offer_line(grp.clip("x " * 100, 120), {"fits": "Noa (grade 1)", "deadline": ""})
        self.assertTrue(line.endswith("… (for Noa (grade 1))"))

    def test_groups_summary_names_the_group(self):
        rows = [{"nid": "g1", "at": "2026-11-24 12:23", "line": "Ninja classes", "group": "Neighbours"}]
        with mock.patch.object(grp, "summary", return_value=rows), \
             mock.patch.object(actions.summaries, "post") as post:
            actions._groups_summary()
        [(nid, text)] = post.call_args.args[2]
        self.assertIn("Ninja classes · ⁨Neighbours⁩", text)

    def test_a_message_line_names_the_chat(self):
        r = {"at": "2026-11-24 09:00", "app": "WhatsApp", "package": "com.whatsapp", "title": "Class parents",
             "summary": "trip photos", "kind": "dismiss"}
        self.assertEqual(secretary.line(r, "2026-11-24"), "09:00 👋 WhatsApp · Class parents: trip photos")
        r["summary"] = "Class parents: trip photos"                    # already named: not twice
        self.assertEqual(secretary.line(r, "2026-11-24"), "09:00 👋 WhatsApp · Class parents: trip photos")


if __name__ == "__main__":
    unittest.main()
