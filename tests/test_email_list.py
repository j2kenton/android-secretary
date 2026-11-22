"""Mailing lists read as one daily line (email_list.py)."""

import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from automations import email_list as el
from automations import groups as grp

LISTS = {"green hills": {"on": True, "name": "green hills", "from": "list@example.com",
                             "label": "Green Hills", "owner": "Owner"}}


class Base(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        for p in (mock.patch.object(el, "STATE", d / "lists.json"), mock.patch.object(grp, "OFFERS", d / "offers.json"),
                  mock.patch.object(grp, "DIR", d)):
            p.start()
            self.addCleanup(p.stop)


class ListOfTests(Base):
    def test_gmail_title_is_the_list(self):
        self.assertEqual(el.list_of({"package": el.GMAIL, "title": "‎green hills‬"}, LISTS), "green hills")

    def test_snoozed_return_and_other_apps_are_not(self):
        self.assertIsNone(el.list_of({"package": el.GMAIL, "title": "Snoozed: green hills"}, LISTS))
        self.assertIsNone(el.list_of({"package": "com.whatsapp", "title": "green hills"}, LISTS))


class ParseTests(Base):
    def test_only_threads_gmail_returned(self):
        text = ('{"thread": "1a1f54593dc43644", "short": "job wanted", "card": ""}\n'
                '{"thread": "deadbeef00000000", "short": "made up", "card": "addressed"}\n'
                '{"thread": "1a1f54593dc43644", "short": "water cut Sunday", "card": "disruption", "line": "Water off Sun 9-13"}')
        posts = el.parse(text, '{"id":"1a1f54593dc43644"}')
        self.assertEqual([p["short"] for p in posts], ["job wanted", "water cut Sunday"])
        self.assertEqual(posts[1]["card"], "disruption")


class LineTests(Base):
    def test_day_line_grows_and_is_cut(self):
        el.day_line("Green Hills", [{"short": "women's evening", "title": ""}], day="2026-11-20")
        rec = el.day_line("Green Hills", [{"short": "kids' workshops", "title": ""},
                                              {"short": "women's evening", "title": ""}], day="2026-11-20")
        self.assertEqual(rec["text"], "(2) women's evening; kids' workshops")
        long = el.text_of("Green Hills", [f"post number {n}" for n in range(20)])
        self.assertLessEqual(len(long) + len("Green Hills · "), el.LINE_MAX + 3)
        self.assertRegex(long, r"; \+\d+ more$")


class DecideTests(Base):
    def test_offer_that_fits_and_closes_soon_is_a_card(self):
        posts = [{"thread": "a1b2c3d4e5", "short": "Hanukkah camp", "card": "", "title": "", "line": "",
                  "offer": {"name": "Hanukkah camp", "same_as": "", "deadline": "2026-11-21",
                            "fits": "Ben (kindergarten)", "must": False}},
                 {"thread": "a1b2c3d4e5", "short": "adult pottery", "card": "", "title": "", "line": "",
                  "offer": {"name": "Pottery", "same_as": "", "deadline": "", "fits": "", "must": False}}]
        out = el.decide(posts, "Green Hills", today=date(2026, 11, 20))
        self.assertEqual([p["card"] for p in out], ["kids", ""])
        self.assertIn("Ben", out[0]["line"])


class TriageTests(Base):
    def test_posts_go_on_one_line_cards_for_the_owner_then_marked_read(self):
        from automations import actions
        el.note("green hills")
        posts = [{"thread": "1a1f54593dc43644", "short": "job wanted", "card": "", "title": "", "line": "", "offer": None},
                 {"thread": "1a1f54593dc43644", "short": "water cut", "card": "disruption", "title": "Water cut Sunday",
                  "line": "Water off Sun 9-13", "offer": None}]
        posted = {}
        with mock.patch.object(actions.secretary, "triage", return_value=[]), \
             mock.patch.object(el, "read", return_value=posts), \
             mock.patch.object(el, "mark_read", return_value=["1a1f54593dc43644"]) as mark, \
             mock.patch.object(actions, "_own_card") as card, \
             mock.patch.object(actions.android, "notifications", return_value=[]), \
             mock.patch.object(actions.summaries, "post", side_effect=lambda *a, **k: posted.update(lines=a[2])):
            state = {}
            actions.secretary_triage({}, email_lists=LISTS, groups={}, _state=state)
        self.assertEqual(card.call_count, 1)
        self.assertIn("Water", card.call_args.args[1])
        self.assertEqual(mark.call_args.args[0], ["1a1f54593dc43644"])
        self.assertEqual(len(posted["lines"]), 1)
        self.assertIn("📧 Green Hills · (2) job wanted; water cut", posted["lines"][0][1])
        self.assertEqual(el.pending(), [])
        self.assertEqual(el.to_mark(), [])

    def test_failed_mark_is_kept_for_later(self):
        from automations import actions
        el.note("green hills")
        posts = [{"thread": "1a1f54593dc43644", "short": "job wanted", "card": "", "title": "", "line": "", "offer": None}]
        with mock.patch.object(actions.secretary, "triage", return_value=[]), \
             mock.patch.object(el, "read", return_value=posts), \
             mock.patch.object(el, "mark_read", return_value=[]), \
             mock.patch.object(actions.android, "notifications", return_value=[]), \
             mock.patch.object(actions.summaries, "post"):
            actions.secretary_triage({}, email_lists=LISTS, groups={}, _state={})
        self.assertEqual(el.to_mark(), ["1a1f54593dc43644"])

    def test_off_list_goes_to_the_secretary(self):
        from automations import actions
        with mock.patch.object(actions.secretary, "collect", return_value=True) as collect:
            actions.secretary_collect({"package": el.GMAIL, "title": "green hills", "body": "x"},
                                      email_lists={"green hills": {**LISTS["green hills"], "on": False}})
        self.assertTrue(collect.called)
        with mock.patch.object(actions.secretary, "collect") as collect:
            actions.secretary_collect({"package": el.GMAIL, "title": "green hills", "body": "x"},
                                      email_lists=LISTS)
        self.assertFalse(collect.called)
        self.assertEqual(el.pending(), ["green hills"])


class MarkReadTests(Base):
    def test_only_threads_reported_done(self):
        ans = SimpleNamespace(text="1a1f54593dc43644 done\n1a1f39fce0419446 failed", tool_output="", tools_called=[])
        got = el.mark_read(["1a1f54593dc43644", "1a1f39fce0419446", "bad id!"], ask=lambda *a, **k: ans)
        self.assertEqual(got, ["1a1f54593dc43644"])


if __name__ == "__main__":
    unittest.main()
