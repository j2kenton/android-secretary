"""bin/automations adb: reconnect after a reboot, with a card while waiting."""

import unittest
from unittest import mock

from automations import adbwait, android


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class AdbWaitTest(unittest.TestCase):
    def setUp(self):
        self.notify = mock.patch.object(android, "notify").start()
        self.remove = mock.patch.object(android, "remove").start()
        self.toast = mock.patch.object(android, "toast").start()
        self.addCleanup(mock.patch.stopall)
        self.lines = []
        self.used_before = mock.patch("os.path.exists", return_value=True).start()

    def test_already_reachable(self):
        with mock.patch.object(android, "adb_reconnect", return_value="127.0.0.1:5555"):
            self.assertEqual(adbwait.run(0, out=self.lines.append), 0)
        self.notify.assert_not_called()
        self.assertIn("127.0.0.1:5555", self.lines[-1])

    def test_off_without_wait_says_what_to_do(self):
        with mock.patch.object(android, "adb_reconnect", return_value=None):
            self.assertEqual(adbwait.run(0, out=self.lines.append), 1)
        self.notify.assert_not_called()
        self.assertIn("Wireless debugging", self.lines[-1])

    def test_waits_with_a_card_then_clears_it(self):
        clock = Clock()
        answers = iter([None, None, None, "127.0.0.1:5555"])
        with mock.patch.object(android, "adb_reconnect", side_effect=lambda **kw: next(answers)) as rc:
            self.assertEqual(adbwait.run(10, out=self.lines.append, sleep=clock.sleep, clock=clock), 0)
        self.notify.assert_called_once()
        self.assertEqual(self.notify.call_args.kwargs["notif_id"], adbwait.NOTIF_ID)
        self.assertIn("APPLICATION_DEVELOPMENT_SETTINGS", self.notify.call_args.kwargs["on_tap"])
        self.remove.assert_called_once_with(adbwait.NOTIF_ID)
        self.assertEqual(rc.call_count, 4)

    def test_no_card_if_adb_was_never_set_up(self):
        self.used_before.return_value = False
        with mock.patch.object(android, "adb_reconnect", return_value=None):
            self.assertEqual(adbwait.run(10, out=self.lines.append, sleep=lambda s: None), 1)
        self.notify.assert_not_called()

    def test_gives_up(self):
        clock = Clock()
        with mock.patch.object(android, "adb_reconnect", return_value=None):
            self.assertEqual(adbwait.run(1, out=self.lines.append, sleep=clock.sleep, clock=clock), 1)
        self.remove.assert_not_called()
        self.assertIn("gave up", self.lines[-1])


class ReconnectScriptTest(unittest.TestCase):
    def test_tries_ports_and_fixes_the_port(self):
        s = android._reconnect_script(["127.0.0.1:41000", "192.168.0.2:42000"], fixed_port=True)
        self.assertIn("for c in 127.0.0.1:41000 192.168.0.2:42000", s)
        self.assertIn("adb mdns services", s)
        self.assertIn("tcpip 5555", s)
        self.assertIn("[ 1 = 1 ]", s)

    def test_no_scan_skips_the_rest_when_already_connected(self):
        with mock.patch.object(android, "_proot_bash", return_value="127.0.0.1:5555\n") as pb, \
             mock.patch.object(android, "open_local_ports") as scan:
            self.assertEqual(android.adb_reconnect(), "127.0.0.1:5555")
        scan.assert_not_called()
        self.assertEqual(pb.call_count, 1)


if __name__ == "__main__":
    unittest.main()
