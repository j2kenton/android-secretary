import unittest
from datetime import date, datetime
from unittest import mock

from automations import actions, shabbat

PLACE = (32.08, 34.78)              # a test location (Tel Aviv), not the owner's
ISRAEL_TZ = "IST-2IDT,M3.4.4/26,M10.5.0"   # Israel time, as a POSIX TZ string



# Cards with reminders are remembered in var/reminders: keep tests out of the real one.
_reminders_patch = None


def setUpModule():
    global _reminders_patch
    import tempfile
    from pathlib import Path as _P
    from automations import reminders
    d = _P(tempfile.mkdtemp())
    _reminders_patch = [mock.patch.object(reminders, "DIR", d), mock.patch.object(reminders, "CARDS", d / "cards.json")]
    for p in _reminders_patch:
        p.start()


def tearDownModule():
    for p in _reminders_patch:
        p.stop()

class ShabbatEnd(unittest.TestCase):
    def test_follows_the_seasons(self):
        # For the owner's own area it matched their calendar to the minute.
        with mock.patch.dict("os.environ", {"TZ": ISRAEL_TZ}):
            import time; time.tzset()
            self.assertEqual(shabbat.end(date(2026, 11, 14), *PLACE).strftime("%H:%M"), "17:19")
            summer = shabbat.end(date(2026, 7, 4), *PLACE)   # summer time: about 20:30
            self.assertTrue(datetime(2026, 7, 4, 20, 15) <= summer <= datetime(2026, 7, 4, 20, 45), summer)

    def test_runs_once_after_shabbat(self):
        state = {}
        with mock.patch.object(actions, "notification_digest") as dig, \
             mock.patch.object(actions.quiet.location, "get", return_value=PLACE), \
             mock.patch.object(actions.quiet.owner, "setting", side_effect=lambda t, k, d=None: (t, k) == ("quiet", "shabbat") or d), \
             mock.patch.object(actions.shabbat, "end", return_value=datetime(2026, 11, 14, 17, 18)), \
             mock.patch.object(actions, "datetime") as dt:
            for t in ((17, 16), (17, 18), (17, 20)):
                dt.now.return_value = datetime(2026, 11, 14, *t)
                actions.ACTIONS["after_shabbat"]({}, _state=state, email=True)   # the old name still works
        self.assertEqual(dig.call_count, 1)
        self.assertTrue(dig.call_args.kwargs["weather_tomorrow"])
        self.assertTrue(dig.call_args.kwargs["email"])



class ChatNotificationsNeverSnoozed(unittest.TestCase):
    def test_failed_mark_read_leaves_a_chat_visible(self):
        ev = {"key": "0|com.whatsapp|1|NSU9oLOu=\n|10563", "package": "com.whatsapp", "title": "חדשות העיר 24: x"}
        with mock.patch.object(actions.android, "mark_read", return_value=False), \
             mock.patch.object(actions.android, "dismiss", return_value=True) as dismiss, \
             mock.patch.object(actions.android, "notifications", return_value=[
                 {"key": ev["key"]}, {"key": "0|org.telegram.messenger|-1000000001|null|10487"},
                 {"key": "0|com.whatsapp|1|null|10563"}]), mock.patch("time.sleep"), \
             mock.patch.object(actions.android, "snooze", return_value=True) as snooze:
            actions._clear(ev)
            actions._clear({**ev, "key": "0|org.telegram.messenger|-1000000001|null|10487",
                            "package": "org.telegram.messenger"})
            actions._clear({**ev, "key": "0|com.whatsapp|1|null|10563"})     # the summary: left alone
        snooze.assert_not_called()
        dismiss.assert_not_called()          # not read: it stays up

    def test_mark_read_retried_once_then_left_up(self):
        k = "0|com.whatsapp|1|abc=\n|10563"
        with mock.patch.object(actions.android, "mark_read", return_value=True) as mr, \
             mock.patch.object(actions.android, "dismiss", return_value=True) as dismiss, \
             mock.patch.object(actions.android, "notifications", side_effect=[[{"key": k}], [{"key": k}], [{"key": k}], [], []]), \
             mock.patch("time.sleep"), mock.patch.object(actions.android, "snooze") as snooze:
            actions._clear({"key": k, "package": "com.whatsapp", "title": "News group: x"})
        self.assertEqual(mr.call_count, 2)
        dismiss.assert_not_called()
        snooze.assert_not_called()

    def test_dismiss_needs_a_title(self):
        with mock.patch.object(actions.android, "_run") as run:
            self.assertFalse(actions.android.dismiss("com.whatsapp", ""))
            self.assertFalse(actions.android.dismiss("com.whatsapp", "\u200e: x"))
            run.assert_not_called()
            self.assertTrue(actions.android.dismiss("com.whatsapp", "News group: x", "k"))
            self.assertIn("phoneautomations.DISMISS", run.call_args[0][0])


class QuietTests(unittest.TestCase):
    """Quiet times are off unless private.toml [quiet] says shabbat = true."""

    def test_off_by_default(self):
        sat_noon = datetime(2026, 11, 21, 12, 0)
        from automations import quiet
        with mock.patch.object(quiet.location, "get", return_value=PLACE):
            self.assertFalse(quiet.active(sat_noon))
            self.assertEqual(quiet.after(sat_noon), sat_noon)
            self.assertIsNone(quiet.ends(sat_noon.date()))

    def test_shabbat_when_set(self):
        from automations import quiet
        with mock.patch.object(quiet.location, "get", return_value=PLACE), \
             mock.patch.object(quiet.owner, "setting", side_effect=lambda t, k, d=None: (t, k) == ("quiet", "shabbat") or d):
            self.assertTrue(quiet.active(datetime(2026, 11, 21, 12, 0)))
            self.assertFalse(quiet.active(datetime(2026, 11, 22, 12, 0)))
            self.assertGreater(quiet.after(datetime(2026, 11, 21, 12, 0)), datetime(2026, 11, 21, 17, 0))


if __name__ == "__main__":
    unittest.main()
