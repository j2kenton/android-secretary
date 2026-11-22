"""Summary cards you can finish; Claude backing off when the API refuses us."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from automations import claude, summaries


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.p = mock.patch.object(summaries, "DIR", Path(tempfile.mkdtemp()))
        self.p.start()
        self.notify = mock.patch.object(summaries.android, "notify").start()
        self.remove = mock.patch.object(summaries.android, "remove").start()

    def tearDown(self):
        mock.patch.stopall()

    def shown(self):
        content = self.notify.call_args.args[1]
        return [l.strip("‎") for l in content.split("\n")]

    def test_page_ends_with_an_end_line_and_seen_marks_only_what_was_shown(self):
        items = [(f"id{n}", f"line {n} " + "x" * 30) for n in range(10)]     # one row each
        summaries.post("t", "Title", items)
        lines = self.shown()
        self.assertEqual(len(lines), summaries.ROWS + 1)
        self.assertEqual(lines[-1], "— end · 3 more after ✓ —")
        label, cmd = self.notify.call_args.kwargs["buttons"][0]
        self.assertEqual(label, "✓ Hide these lines")
        self.assertIn("seen t id0,id1,id2,id3,id4,id5,id6", cmd)
        summaries.mark_seen("t", [f"id{n}" for n in range(7)])
        self.assertEqual(self.shown()[-1], "— end —")
        self.assertEqual(self.notify.call_args.kwargs["buttons"][0][0], "✓ Hide these lines")
        summaries.post("t", "Title", items + [("id10", "new one")])         # seen ones don't come back
        self.assertEqual(len(self.shown()), 5)
        summaries.mark_seen("t", ["id7", "id8", "id9", "id10"])
        self.remove.assert_called_with("t")

    def test_an_extra_button_shows_while_its_line_is_on_the_card(self):
        summaries.post("t", "Title", [("w", "☁ 22°/12°"), ("x", "other")], buttons=[("↪ Weather to Dana", "CMD", "w")])
        self.assertEqual(self.notify.call_args.kwargs["buttons"][1], ("↪ Weather to Dana", "CMD"))
        summaries.mark_seen("t", ["w"])
        self.assertEqual(len(self.notify.call_args.kwargs["buttons"]), 1)

    def test_long_lines_take_more_rows_and_a_huge_one_is_cut(self):
        self.assertEqual(len(summaries.page([["a", "x" * 100], ["b", "x" * 100], ["c", "x" * 100]])), 2)
        one = summaries.page([["a", "x" * 1000]])
        self.assertEqual(len(one), 1)
        self.assertTrue(one[0][1].endswith("… (cut)"))
        self.assertLessEqual(summaries.rows(one[0][1]), summaries.ROWS)


class BlockedTests(unittest.TestCase):
    def setUp(self):
        self.f = Path(tempfile.mkdtemp()) / "b.json"
        mock.patch.object(claude, "BLOCK_FILE", str(self.f)).start()
        mock.patch("automations.android.notify").start()

    def tearDown(self):
        mock.patch.stopall()

    def test_cloudflare_403_stops_retries_and_backs_off(self):
        err = claude.ClaudeError('"result":"Failed to authenticate. API Error: 403 Attention Required! | Cloudflare"')
        with mock.patch.object(claude, "_ask_once", side_effect=err) as once:
            with self.assertRaises(claude.Blocked):
                claude.ask("q", [])
            self.assertEqual(once.call_count, 1)                    # no retries
            with self.assertRaises(claude.Blocked):
                claude.ask("q", [])                                 # backing off: not even tried
            self.assertEqual(once.call_count, 1)

    def test_success_clears_the_block(self):
        self.f.write_text(json.dumps({"since": time.time() - 999, "until": time.time() - 1}))
        with mock.patch.object(claude, "_ask_once", return_value=claude.Answer("ok", [], "")):
            self.assertEqual(claude.ask("q", []).text, "ok")
        self.assertFalse(self.f.exists())

    def test_warns_once_after_a_long_block(self):
        with mock.patch("automations.android.notify") as notify:
            claude._set_blocked("403", now=1000)
            claude._set_blocked("403", now=1000 + claude.WARN_AFTER)
            claude._set_blocked("403", now=1000 + claude.WARN_AFTER + 300)
        self.assertEqual(notify.call_count, 1)
