"""Clears that failed while adb was down are retried once it's back."""

import json
import unittest
from unittest import mock

from automations import actions, android


class RetryClearsTest(unittest.TestCase):
    def setUp(self):
        actions.RETRY_FILE.unlink(missing_ok=True)
        self.addCleanup(mock.patch.stopall)
        self.adb = mock.patch.object(android, "adb_output", return_value="ok\n").start()
        self.snooze = mock.patch.object(android, "snooze", return_value=True).start()
        self.live = mock.patch.object(android, "notifications", return_value=[
            {"key": "0|com.facebook.katana|0|stale_badge|1"}]).start()

    def pending(self):
        return json.loads(actions.RETRY_FILE.read_text())

    def test_chats_are_never_queued(self):
        actions.retry_later("0|org.telegram.messenger|5|null|1")
        actions.retry_later("0|com.whatsapp|5|x|1")
        self.assertFalse(actions.RETRY_FILE.exists())

    def test_waits_while_adb_is_down(self):
        actions.retry_later("0|com.facebook.katana|0|stale_badge|1")
        self.adb.return_value = None
        actions.retry_clears({})
        self.snooze.assert_not_called()
        self.assertIn("0|com.facebook.katana|0|stale_badge|1", self.pending())

    def test_snoozes_once_back_and_drops_what_went(self):
        actions.retry_later("0|com.facebook.katana|0|stale_badge|1")
        actions.retry_later("0|com.example|1|null|1")           # no longer in the shade
        actions.retry_clears({})
        self.snooze.assert_called_once_with("0|com.facebook.katana|0|stale_badge|1")
        self.assertEqual(self.pending(), {})

    def test_too_old_is_dropped(self):
        actions.RETRY_FILE.write_text(json.dumps({"0|com.facebook.katana|0|stale_badge|1": 0}))
        actions.retry_clears({})
        self.snooze.assert_not_called()
        self.assertEqual(self.pending(), {})

    def test_failed_snooze_stays_queued(self):
        self.snooze.return_value = False
        actions.retry_later("0|com.facebook.katana|0|stale_badge|1")
        actions.retry_clears({})
        self.assertIn("0|com.facebook.katana|0|stale_badge|1", self.pending())


if __name__ == "__main__":
    unittest.main()
