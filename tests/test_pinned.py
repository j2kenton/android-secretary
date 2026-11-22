"""Telegram's untitled "<chat> pinned "…"" notice: marked read by text, watched chats only."""

import unittest
from unittest import mock

from automations import actions, android, groups as grp


class PinnedTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(mock.patch.stopall)
        self.run_ = mock.patch.object(android, "_run").start()
        mock.patch.object(android, "notification_actions", return_value=["Mark as read"]).start()
        mock.patch.object(android, "notifications", return_value=[]).start()
        mock.patch("time.sleep").start()
        self.profile = mock.patch.object(grp, "profile_of", return_value="news").start()

    def event(self, body):
        return {"source": "notification", "package": actions.TELEGRAM, "title": "",
                "body": body, "key": "0|org.telegram.messenger|2130106944|null|1"}

    def sent(self):
        return [c.args[0] for c in self.run_.call_args_list]

    def test_watched_channel_pin_is_marked_read_by_text(self):
        actions.group_collect(self.event('News Channel pinned "Talks began…"'))
        (args,) = self.sent()
        self.assertIn("phoneautomations.MARK_READ_TEXT", args)
        self.assertEqual(args[args.index("text") + 1], "News Channel pinned")
        self.assertEqual(args[args.index("button") + 1], "Mark as read")

    def test_chats_left_alone_or_muted_are_not_touched(self):
        for profile in ("active", "person", "muted", None):
            self.profile.return_value = profile
            actions.group_collect(self.event('News Channel pinned "Talks began…"'))
        self.assertEqual(self.sent(), [])

    def test_other_untitled_notices_and_cut_names_are_left(self):
        actions.group_collect(self.event("2 new messages"))
        actions.group_collect(self.event('A (b) pinned "x"'))          # name cut short by chat_filter
        self.assertEqual(self.sent(), [])

    def test_without_the_button_nothing_is_sent(self):
        with mock.patch.object(android, "notification_actions", return_value=[]):
            actions.group_collect(self.event('News Channel pinned "x"'))
        self.assertEqual(self.sent(), [])


if __name__ == "__main__":
    unittest.main()
