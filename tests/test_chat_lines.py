"""A chat's messages make one line on the 🗂 card, even when they come in at
different times: its newest summary takes in the lines before it, which go."""

import unittest
from unittest import mock

from automations import actions, secretary


def rec(text, summary, at="2026-11-26 14:56", title="Dana", kind="later", **kw):
    return {"at": at, "app": "WhatsApp", "package": "com.whatsapp", "title": title, "text": text, "key": text,
            "summary": summary, "kind": kind, "suggestion": "", "rule_idea": "", "reply": "", "event": None, **kw}


class ChatLinesTests(unittest.TestCase):
    def triage(self, recs, state):
        with mock.patch.object(actions.secretary, "triage", return_value=recs) as tri, \
             mock.patch.object(actions.android, "notifications", return_value=[]), \
             mock.patch.object(actions.android, "dismiss"), \
             mock.patch.object(actions.summaries, "post") as post:
            actions.secretary_triage({}, _state=state)
        lines = [l for _i, l in post.call_args.args[2]] if post.called else []
        return tri, lines

    def test_messages_at_different_times_make_one_line(self):
        state = {}
        _tri, lines = self.triage([rec("then cities", "Dana: plans up to cities")], state)
        self.assertEqual(len(lines), 1)
        tri, lines = self.triage([rec("then endless fun", "Dana: terrain, cities, then fun", at="2026-11-26 15:10")],
                                 state)
        chat = actions._chat_nid(rec("x", "x"))
        self.assertEqual(tri.call_args.kwargs["earlier"], {chat: ["Dana: plans up to cities"]})
        self.assertEqual(len(lines), 1)
        self.assertIn("terrain, cities, then fun", lines[0])

    def test_messages_in_one_batch_make_one_line(self):
        _tri, lines = self.triage([rec("a", "Dana: first"), rec("b", "Dana: first and second"),
                                   rec("c", "Sam: other", title="Sam")], {})
        self.assertEqual(len(lines), 2)
        self.assertTrue(any("first and second" in l for l in lines))
        self.assertFalse(any(l.endswith("Dana: first") for l in lines))

    def test_a_hidden_line_stays_hidden_and_isnt_summed_up_again(self):
        state = {}
        self.triage([rec("old", "Dana: old news")], state)
        hidden = secretary.item_id(state["card"][0])
        with mock.patch.object(actions.summaries, "seen", return_value={hidden}):
            tri, lines = self.triage([rec("new", "Dana: new news", at="2026-11-26 15:10")], state)
        self.assertEqual(tri.call_args.kwargs["earlier"], {})
        self.assertEqual([l for l in lines if "news" in l][-1].split("· ")[-1], "Dana: new news")

    def test_triage_tells_claude_which_messages_share_a_chat(self):
        q = [rec("a", ""), rec("b", ""), rec("c", "", title="Sam")]
        seen = {}
        secretary.DIR.mkdir(parents=True, exist_ok=True)
        ans = "\n".join('{"i": %d, "kind": "later", "summary": "s"}' % i for i in range(3))
        with mock.patch.object(secretary, "_read", return_value=q), \
             mock.patch.object(secretary, "_contacts", return_value=set()), \
             mock.patch.object(secretary.claude, "ask",
                               side_effect=lambda p, *a, **k: seen.update(p=p) or secretary.claude.Answer(ans, [], "")):
            secretary.triage([], chat_of=actions._chat_nid,
                             earlier={actions._chat_nid(q[0]): ["Dana: before"]})
        rows = seen["p"].splitlines()[-3:]
        self.assertIn('"chat": 1', rows[0])
        self.assertIn('"earlier": ["Dana: before"]', rows[0])
        self.assertIn('"chat": 1', rows[1])
        self.assertNotIn("earlier", rows[1])
        self.assertIn('"chat": 2', rows[2])


if __name__ == "__main__":
    unittest.main()
