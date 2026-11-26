"""bin/claude-session resumes the most recent conversation, never one still open."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from automations import claude_last as cl

CONVS = [(3.0, "/root/proj", "newest"), (2.0, "/root/proj", "older"), (1.0, "/root", "oldest")]


class PickTests(unittest.TestCase):
    def test_newest_when_nothing_runs(self):
        self.assertEqual(cl.pick(CONVS, []), ("/root/proj", "newest"))

    def test_skips_one_resumed_in_a_running_claude(self):
        procs = [(["/root/.local/bin/claude", "--resume", "newest", "--remote-control"], "/root/proj")]
        self.assertEqual(cl.pick(CONVS, procs), ("/root/proj", "older"))

    def test_skips_the_newest_in_the_folder_of_a_new_running_claude(self):
        procs = [(["/root/.local/bin/claude", "--remote-control"], "/root/proj")]
        self.assertEqual(cl.pick(CONVS, procs), ("/root/proj", "older"))
        procs = [(["/root/.local/bin/claude"], "/root")]
        self.assertEqual(cl.pick(CONVS, procs), ("/root/proj", "newest"))

    def test_none_left(self):
        procs = [(["claude", "-r", "newest"], "/"), (["claude", "-r", "older"], "/"), (["claude", "-r", "oldest"], "/")]
        self.assertIsNone(cl.pick(CONVS, procs))


class ConversationsTests(unittest.TestCase):
    def test_reads_folder_and_skips_empty_ones(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "-root-proj"
            p.mkdir()
            lines = [{"type": "summary"}, {"type": "user", "cwd": "/root/proj", "sessionId": "a"}]
            (p / "a.jsonl").write_text("\n".join(json.dumps(x, separators=(",", ":")) for x in lines))
            (p / "b.jsonl").write_text(json.dumps({"type": "summary", "cwd": "/root/proj"}))   # nothing said
            sub = p / "a" / "subagents"
            sub.mkdir(parents=True)
            (sub / "agent.jsonl").write_text((p / "a.jsonl").read_text())                  # not a conversation
            os.utime(p / "a.jsonl", (5, 5))
            self.assertEqual(cl.conversations(Path(d)), [(5.0, "/root/proj", "a")])


if __name__ == "__main__":
    unittest.main()
