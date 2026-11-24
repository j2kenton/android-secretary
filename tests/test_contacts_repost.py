"""Senders in the phone's contacts are marked for triage; the same text days later is news again."""

import unittest
from datetime import datetime
from unittest import mock

from automations import secretary


class ContactsRepostTests(unittest.TestCase):
    def test_in_contacts(self):
        names = {"dana", "sam k"}
        self.assertTrue(secretary.in_contacts("⁨Dana⁩", names))
        self.assertTrue(secretary.in_contacts("Class parents: Sam K", names))
        self.assertTrue(secretary.in_contacts("Dana (2 messages)", names))
        self.assertFalse(secretary.in_contacts("Some Page", names))
        self.assertFalse(secretary.in_contacts("", names))

    def test_the_listing_marks_contacts(self):
        seen = {}
        q = [{"app": "Facebook", "package": "com.facebook.katana", "title": t, "text": "mentioned you in a post.",
              "key": t} for t in ("Dana", "Some Page")]

        def ask(prompt, *a, **k):
            seen["p"] = prompt
            return secretary.claude.Answer("", [], "")
        with mock.patch.object(secretary, "_read", return_value=q), \
             mock.patch.object(secretary, "_contacts", return_value={"dana"}), \
             mock.patch.object(secretary.claude, "ask", side_effect=ask), \
             mock.patch.object(secretary, "QUEUE"), mock.patch.object(secretary, "TRIAGED"), \
             mock.patch("builtins.open", mock.mock_open()):
            secretary.triage([])
        rows = seen["p"].splitlines()
        self.assertIn('"contact": true', next(r for r in rows if '"title": "Dana"' in r))
        self.assertNotIn('"contact"', next(r for r in rows if '"title": "Some Page"' in r))

    def test_same_text_is_a_repost_only_for_a_while(self):
        ev = {"package": "com.facebook.katana", "title": "Dana", "body": "mentioned you in a post.", "key": "k"}
        state = {"recent": ["old|list|format"]}
        with mock.patch.object(secretary, "_extras", return_value={}), \
             mock.patch("builtins.open", mock.mock_open()), mock.patch.object(secretary, "DIR"), \
             mock.patch.object(secretary, "datetime", wraps=datetime) as now_dt:
            now = now_dt.now
            t0 = datetime(2026, 11, 20, 16, 0)
            now.return_value = t0
            self.assertTrue(secretary.collect(ev, {}, state))
            now.return_value = t0.replace(hour=17)
            self.assertFalse(secretary.collect(ev, {}, state))          # re-posted within the hour
            now.return_value = datetime(2026, 11, 24, 17, 5)
            self.assertTrue(secretary.collect(ev, {}, state))           # days later: a new tag


if __name__ == "__main__":
    unittest.main()
