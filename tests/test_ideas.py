import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from automations import actions, claude, ideas, secretary


class IdeasTests(unittest.TestCase):
    """Weekly automation ideas, Friday midday; nothing built without them."""

    def setUp(self):
        d = Path(tempfile.mkdtemp())
        mock.patch.object(ideas, "STATE", d / "ideas.json").start()
        mock.patch.object(secretary, "TRIAGED", d / "t.jsonl").start()
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        rows = [{"at": now, "app": "Gmail", "rule_idea": "Add coaching sessions to the calendar"}] * 3
        (d / "t.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        self.answer = "\n".join(json.dumps(x) for x in [
            {"title": "Coaching sessions → calendar", "idea": "Offer to add each booked session.", "seen": 3},
            {"title": "Clear Maps prompts", "idea": "Clear 'how crowded' prompts.", "seen": 2}])

    def tearDown(self):
        mock.patch.stopall()

    def ask(self, prompt, *a, **k):
        self.prompt = prompt
        return claude.Answer(self.answer, [], "")

    def test_cards_and_a_no_is_not_asked_again(self):
        picked = ideas.pick(["Downloads are left alone"], ask=self.ask)
        self.assertIn("Downloads are left alone", self.prompt)
        self.assertIn("Gmail | Add coaching sessions to the calendar", self.prompt)
        self.assertEqual([d["title"] for d in picked], ["Coaching sessions → calendar", "Clear Maps prompts"])
        ideas.offered(picked)
        ideas.answer(picked[1]["id"], yes=False)
        again = ideas.pick([], ask=self.ask)
        self.assertEqual([d["title"] for d in again], ["Coaching sessions → calendar"])
        self.assertIn("Clear Maps prompts", self.prompt)                 # Claude is told it was turned down

    def test_weekly_cards_have_build_and_no_buttons(self):
        with mock.patch.object(actions.ideas, "pick", return_value=[
                {"id": "idea-1", "title": "Coaching sessions → calendar", "idea": "Offer to add.", "seen": 3}]), \
             mock.patch.object(actions.android, "notify") as notify:
            actions.weekly_ideas({}, secretary_cfg={})
        self.assertEqual(notify.call_args.args[0], "💡 Idea 1/1: Coaching sessions → calendar")
        (l1, c1), (l2, c2) = notify.call_args.kwargs["buttons"]
        self.assertEqual((l1, l2), ("✅ Build it", "✋ No thanks"))
        self.assertIn("idea yes idea-1", c1)
        self.assertIn("send-keys", c1)                                   # typed into Claude, not sent
        self.assertIn("idea no idea-1", c2)


if __name__ == "__main__":
    unittest.main()
