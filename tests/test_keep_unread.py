"""[groups] keep_unread: in a chat where only some posts matter, those are left
unread, and the chat isn't marked read (which would take them along) until the
owner has read them."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from automations import actions, android, groups as grp

KEY = "0|org.telegram.messenger|77|null|1"
CFG = {"keep_unread": {"Alerts": "springfield|whole country"}}


class KeepUnreadTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(mock.patch.stopall)
        d = Path(tempfile.mkdtemp())
        for name in ("DIR", "HELD", "MESSAGES", "LOG"):
            mock.patch.object(grp, name, d / getattr(grp, name).name if name != "DIR" else d).start()
        mock.patch.object(grp, "profile_of", side_effect=lambda g: "homefront" if g in ("Alerts", "Other") else None).start()
        self.live = mock.patch.object(android, "notifications", return_value=[]).start()
        self.clear = mock.patch.object(actions, "_clear").start()

    def post(self, text, chat="Alerts", cfg=CFG):
        actions.group_collect({"source": "notification", "package": actions.TELEGRAM, "title": chat,
                               "body": text, "key": KEY}, groups=cfg)

    def test_elsewhere_is_marked_read(self):
        self.post("Alert in Shelbyville")
        self.clear.assert_called_once()

    def test_a_post_for_the_owner_is_left_and_holds_the_chat_until_read(self):
        self.post("Alert in Springfield")
        self.clear.assert_not_called()
        self.assertEqual(grp.held(), {"Alerts": KEY})
        self.live.return_value = [{"key": KEY}]           # still unread: elsewhere is left too
        self.post("Alert in Shelbyville")
        self.clear.assert_not_called()
        self.live.return_value = []                        # the owner read it
        self.post("Alert in Shelbyville again")
        self.clear.assert_called_once()
        self.assertEqual(grp.held(), {})

    def test_only_the_chat_it_is_set_for(self):
        self.post("Alert in Springfield", chat="Other")      # same words, another chat: its usual rules
        self.clear.assert_called_once()

    def test_the_pinned_notice_and_sweep_leave_a_held_chat_alone(self):
        self.post("Whole country: stay near a shelter")
        with mock.patch.object(android, "mark_read_pinned") as pin:
            actions._clear_pin({"body": 'Alerts pinned "x"', "key": KEY})
        pin.assert_not_called()
        self.live.return_value = [{"packageName": actions.TELEGRAM, "key": KEY, "title": "Alerts",
                                   "content": "Whole country: stay near a shelter", "when": "2000-01-01 00:00:00"}]
        actions.sweep_handled({}, groups=CFG, _state={})
        self.clear.assert_not_called()


if __name__ == "__main__":
    unittest.main()
