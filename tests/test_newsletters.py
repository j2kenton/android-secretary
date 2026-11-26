"""Newsletters under a Gmail label: a few 📚 lines on the morning card, asked
once a day, each newsletter summarised once."""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from automations import claude, newsletters as nl

CFG = {"label": "@News", "max_lines": 3, "interests": "front-end"}
TOOL = '{"threads":[{"id":"1a213f37610d8937","messageCount":1},{"id":"1a20ed02a162842a","messageCount":1}]}'


def answer(text, tool=TOOL):
    return claude.Answer(text, [], tool)


class NewslettersTests(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(nl, "STATE", Path(tempfile.mkdtemp()) / "nl.json")
        p.start()
        self.addCleanup(p.stop)

    def test_lines_once_a_day_and_made_up_threads_dropped(self):
        ans = answer("1a213f37610d8937 | Frontend Focus: new browser features\n"
                     "ffffffffffffffff | Made Up: not in the search\nchatter")
        with mock.patch.object(nl.claude, "ask", return_value=ans) as ask:
            first = nl.lines(CFG, datetime(2026, 11, 27, 5, 0))
            again = nl.lines(CFG, datetime(2026, 11, 27, 6, 10))
        self.assertEqual(first, ["📚 Frontend Focus: new browser features"])
        self.assertEqual(again, first)                       # 06:10: the same lines, Claude not asked again
        ask.assert_called_once()
        self.assertIn('label:"@News"', ask.call_args.args[0])

    def test_a_newsletter_seen_yesterday_is_skipped_today(self):
        with mock.patch.object(nl.claude, "ask", return_value=answer("NONE")):
            self.assertEqual(nl.lines(CFG, datetime(2026, 11, 27, 5, 0)), [])
        with mock.patch.object(nl.claude, "ask", return_value=answer(
                "1a20ed02a162842a | JavaScript Weekly: imports without a bundler")) as ask:
            out = nl.lines(CFG, datetime(2026, 11, 28, 5, 0))
        self.assertIn("1a213f37610d8937", ask.call_args.args[0])   # told to skip what it read yesterday
        self.assertEqual(out, [])                                   # and a line about one of them is dropped

    def test_a_failed_read_marks_nothing_and_is_tried_again(self):
        with mock.patch.object(nl.claude, "ask", side_effect=claude.ClaudeError("down")):
            with self.assertRaises(claude.ClaudeError):
                nl.lines(CFG, datetime(2026, 11, 27, 5, 0))
        with mock.patch.object(nl.claude, "ask", return_value=answer("NONE")) as ask:
            nl.lines(CFG, datetime(2026, 11, 27, 6, 10))
        ask.assert_called_once()

    def test_no_label_no_call(self):
        with mock.patch.object(nl.claude, "ask") as ask:
            self.assertEqual(nl.lines({}), [])
        ask.assert_not_called()


if __name__ == "__main__":
    unittest.main()
