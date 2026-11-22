"""Morning summary: clock source, at/days matching, and the digest text."""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from automations.digest import parse_email_lines, summarise
from automations.rules import ConfigError, load
from automations.sources import ClockSource

RULES = """
[[rule]]
name = "morning"
source = "clock"
at = ["05:00", "06:10"]
days = ["sun", "mon", "tue", "wed", "thu", "fri"]
actions = [{ type = "log" }]
"""


class ClockTests(unittest.TestCase):
    def test_baseline_then_one_event_per_minute_with_catch_up_cap(self):
        now = [datetime(2026, 11, 13, 4, 58, 30)]
        src = ClockSource({}, now=lambda: now[0])
        self.assertEqual(src.poll(), [])                       # baseline
        now[0] = datetime(2026, 11, 13, 5, 0, 5)
        self.assertEqual([e["time"] for e in src.poll()], ["04:59", "05:00"])
        self.assertEqual(src.poll(), [])                       # same minute again
        now[0] = datetime(2026, 11, 13, 9, 0)                  # daemon was down for hours
        self.assertEqual(len(src.poll()), 30)

    def test_rule_matches_time_and_weekday(self):
        d = Path(tempfile.mkdtemp())
        (d / "rules.toml").write_text(RULES)
        _, [rule] = load(d / "rules.toml", d / "private.toml")
        ev = lambda t, wd: {"source": "clock", "id": "x", "time": t, "weekday": wd}
        self.assertTrue(rule.matches(ev("05:00", "fri")))
        self.assertFalse(rule.matches(ev("05:00", "sat")))
        self.assertTrue(rule.matches(ev("06:10", "sun")))
        self.assertFalse(rule.matches(ev("05:01", "fri")))

    def test_bad_time_or_day_is_a_config_error(self):
        d = Path(tempfile.mkdtemp())
        for bad in ('at = "5am"', 'days = ["shabbat"]'):
            (d / "rules.toml").write_text(f'[[rule]]\nname="x"\nsource="clock"\n{bad}\n')
            with self.assertRaises(ConfigError):
                load(d / "rules.toml", d / "private.toml")


class DigestTests(unittest.TestCase):
    def n(self, pkg, title, content="", when="2026-11-13 02:00:00"):
        return {"packageName": pkg, "title": title, "content": content, "when": when}

    def test_one_line_per_title_newest_first_noise_dropped(self):
        notifs = [
            self.n("com.whatsapp", "Mum", "see you at 8", "2026-11-13 04:10:00"),
            self.n("com.whatsapp", "Mum", "night", "2026-11-12 23:00:00"),
            self.n("com.google.android.gm", "City Clinic Ltd", "Your session is tomorrow at 10:00 sharp", "2026-11-13 01:00:00"),
            self.n("io.heckel.ntfy", "bugs9: needs you", "2 questions"),
            self.n("com.android.phone", "New voicemail", "Ultra"),
            self.n("com.whatsapp", "", ""),                    # group header
            self.n("com.whatsapp", "Mum: x", "", "2026-11-13 04:00:00"),  # summary copy of one message
            self.n("com.whatsapp", "Mum", "x", "2026-11-13 04:00:00"),
        ]
        headline, lines = summarise(notifs, ["io.heckel.ntfy"], ["New voicemail"])
        self.assertEqual(headline, "3 things waiting")
        self.assertEqual(lines, ["WhatsApp · Mum: see you at 8", "WhatsApp · Mum: x",
                                 "Gmail · City Clinic Ltd: Your session is tomorrow at…"])

    def test_nothing_left_means_no_card(self):
        self.assertEqual(summarise([self.n("com.termux", "Termux")], ["com.termux"]), ("", []))


    def test_email_lines_need_a_thread_id_from_the_real_search(self):
        found = '{"threads": [{"id": "19a3f0c2b7d4e5f6", "from": "School office"}]}'
        self.assertEqual(parse_email_lines("NONE", found), [])
        answer = ("Here you go:\n"
                  "- 19a3f0c2b7d4e5f6 | School office: No school today, staff strike\n"
                  "2. 18ffffffffffffff | Bank: Mortgage payment failed")       # id never returned: made up
        self.assertEqual(parse_email_lines(answer, found), ["School office: No school today, staff strike"])


class StreamTests(unittest.TestCase):
    def test_parse_stream_collects_tool_calls_and_results(self):
        from automations.claude import parse_stream
        out = "\n".join([
            '{"type":"system","subtype":"init"}',
            '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"mcp__claude_ai_Gmail__search_threads"}]}}',
            '{"type":"user","message":{"content":[{"type":"tool_result","content":[{"type":"text","text":"id abc123"}]}]}}',
            '{"type":"result","result":"abc123 | X: y"}',
        ])
        ans = parse_stream(out)
        self.assertEqual(ans.tools_called, ["mcp__claude_ai_Gmail__search_threads"])
        self.assertIn("abc123", ans.tool_output)
        self.assertEqual(ans.text, "abc123 | X: y")

if __name__ == "__main__":
    unittest.main()
