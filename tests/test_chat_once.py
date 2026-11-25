"""A chat's messages are on its card or on 🗂, never both: once a chat has a
card (or gets one in this batch), all its messages go on it, and its earlier
🗂 lines move onto the new card."""

import unittest
from unittest import mock

from automations import actions, secretary

WA = "com.whatsapp"


def rec(text, kind, at="2026-11-25 13:31", suggestion="", **kw):
    return {"at": at, "app": "WhatsApp", "package": WA, "title": "Sam", "text": text, "key": "0|com.whatsapp|1|null|7",
            "summary": f"Sam: {text}", "kind": kind, "suggestion": suggestion, "rule_idea": "", "reply": "",
            "event": None, **kw}


class ChatOnceTests(unittest.TestCase):
    def triage(self, recs, state, live=()):
        with mock.patch.object(actions.secretary, "triage", return_value=recs), \
             mock.patch.object(actions.android, "notifications", return_value=[{"packageName": "com.termux.api",
                                                                               "tag": t} for t in live]), \
             mock.patch.object(actions.android, "dismiss") as dismiss, \
             mock.patch.object(actions.android, "contacts", return_value={}), \
             mock.patch.object(actions.summaries, "seen", return_value=set()), \
             mock.patch.object(actions.summaries, "post") as post, \
             mock.patch.object(actions, "_card_with_reminders") as card:
            actions.secretary_triage({}, _state=state)
        return card, post, dismiss

    def test_a_later_message_before_the_question_joins_its_card(self):
        state = {}
        card, post, dismiss = self.triage([rec("Focusing on data roles", "later"),
                                           rec("Has it changed since August?", "reply", suggestion="Say if it has")], state)
        text = card.call_args.args[2]
        self.assertIn("Focusing on data roles", text)
        self.assertIn("since August", text)
        post.assert_not_called()                       # no 🗂 line for it
        dismiss.assert_not_called()                    # nor is the chat's notification swiped
        self.assertEqual(state["card"], [])

    def test_the_question_stays_when_a_later_message_follows(self):
        card, _post, _d = self.triage([rec("Has it changed since August?", "reply", suggestion="Say if it has"),
                                       rec("It's a tight market", "later")], {})
        self.assertIn("→ Say if it has", card.call_args.args[2])

    def test_its_earlier_summary_line_moves_onto_the_new_card(self):
        state = {}
        _card, post, _d = self.triage([rec("Focusing on data roles", "later", at="2026-11-25 13:00")], state)
        self.assertEqual(len(post.call_args.args[2]), 1)                  # on 🗂 for now
        card, post, _d = self.triage([rec("Has it changed since August?", "reply")], state)
        text = card.call_args.args[2]
        self.assertLess(text.index("13:00 Sam: Focusing"), text.index("13:31 Sam: Has it"))
        self.assertEqual(post.call_args.args[2], [])                       # and off 🗂
        self.assertEqual(state["card"], [])

    def test_other_chats_lines_stay(self):
        state = {}
        self.triage([{**rec("Lunch?", "later"), "title": "Alex", "summary": "Alex: lunch?"}], state)
        _card, post, _d = self.triage([rec("Has it changed since August?", "reply")], state)
        post.assert_not_called()                                            # 🗂 left as it is
        self.assertEqual([r["title"] for r in state["card"]], ["Alex"])


if __name__ == "__main__":
    unittest.main()
