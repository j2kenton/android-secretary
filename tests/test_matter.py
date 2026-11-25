"""A significant matter gets one card of its own, however many notifications
come about it, never lines on the summary; "🔗 Open" opens the thing itself."""

import unittest
from unittest import mock

from automations import actions, secretary

URL = "https://console.example.com/support/case/123"


def rec(summary, text, kind="act_now", matter="Support case 123", at="2026-11-25 12:49", **kw):
    return {"at": at, "app": "Zoho Mail", "package": "com.zoho.mail", "title": "no-reply@example.com",
            "text": text, "key": summary, "summary": summary, "kind": kind, "suggestion": "", "rule_idea": "",
            "reply": "", "event": None, "matter": matter, **kw}


class MatterTests(unittest.TestCase):
    def test_parse_keeps_matter_and_url(self):
        out = secretary.parse('{"i": 0, "kind": "act_now", "summary": "s", "matter": "Case 1", "url": "%s"}' % URL, 1)
        self.assertEqual((out[0]["matter"], out[0]["url"]), ("Case 1", URL))

    def triage(self, recs, state, live=()):
        with mock.patch.object(actions.secretary, "triage", return_value=recs), \
             mock.patch.object(actions.android, "notifications", return_value=[{"packageName": "com.termux.api",
                                                                               "tag": t} for t in live]), \
             mock.patch.object(actions.android, "dismiss"), \
             mock.patch.object(actions.summaries, "post") as post, \
             mock.patch.object(actions, "_card_with_reminders") as card, \
             mock.patch.object(actions, "secretary_card") as one:
            actions.secretary_triage({}, _state=state)
        return card, one, post

    def test_two_notifications_one_card_not_the_summary(self):
        state = {}
        recs = [rec("Case 123 opened", "opened"), rec("New reply on case 123", f"see {URL}", url=URL)]
        card, one, post = self.triage(recs, state)
        one.assert_not_called()
        post.assert_not_called()
        nid, title, text, buttons = card.call_args.args[:4]
        self.assertEqual(title, "❗ Support case 123 (2)")
        self.assertIn("12:49 Case 123 opened", text)
        self.assertEqual(buttons[0][0], "🔗 Open")
        self.assertIn(URL, buttons[0][1])

        card, _one, _post = self.triage([rec("Case 123 solved", "solved", at="2026-11-25 15:00")], state, live=[nid])
        self.assertEqual(card.call_args.args[0], nid)                     # the same card, now with three
        self.assertEqual(card.call_args.args[1], "❗ Support case 123 (3)")

    def test_a_swiped_card_starts_afresh(self):
        state = {}
        card, _o, _p = self.triage([rec("Case 123 opened", "opened")], state)
        self.assertEqual(card.call_args.args[1], "❗ Case 123 opened")
        card, _o, _p = self.triage([rec("Case 123 solved", "solved")], state, live=())
        self.assertEqual(card.call_args.args[1], "❗ Case 123 solved")

    def test_a_link_not_in_the_notification_is_dropped(self):
        q = [{"app": "Zoho Mail", "package": "com.zoho.mail", "title": "x", "text": "no link here", "key": "k"}]
        ans = '{"i": 0, "kind": "act_now", "summary": "s", "url": "https://made.up/thing"}'
        with mock.patch.object(secretary, "_read", return_value=q), \
             mock.patch.object(secretary, "_contacts", return_value=set()), \
             mock.patch.object(secretary.claude, "ask", return_value=secretary.claude.Answer(ans, [], "")), \
             mock.patch.object(secretary, "QUEUE"), mock.patch.object(secretary, "TRIAGED"), \
             mock.patch("builtins.open", mock.mock_open()):
            [r] = secretary.triage([])
        self.assertNotIn("url", r)


if __name__ == "__main__":
    unittest.main()
