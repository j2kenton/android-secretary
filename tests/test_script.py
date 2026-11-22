"""script.same_language: a suggested reply stays in the message's script."""

import unittest

from automations import script


class ScriptTests(unittest.TestCase):
    def test_dominant(self):
        self.assertEqual(script.dominant("( water)"), "LATIN")
        self.assertEqual(script.dominant("מה קרה עם המים?"), "HEBREW")
        self.assertEqual(script.dominant("Привет"), "CYRILLIC")
        self.assertEqual(script.dominant("מחר at 8, ok? בסדר גמור"), "HEBREW")    # most letters
        self.assertIsNone(script.dominant("👍 12:30!"))

    def test_same_language(self):
        self.assertFalse(script.same_language("( water)", "מה קרה עם המים?"))
        self.assertFalse(script.same_language("הגיע האוטובוס?", "Yes, it came"))
        self.assertTrue(script.same_language("הגיע האוטובוס?", "כן, הגיע"))
        self.assertTrue(script.same_language("Are you coming?", "On my way 🚗"))
        self.assertTrue(script.same_language("👍", "Great!"))          # nothing to judge the message by
        self.assertTrue(script.same_language("Coming?", "👍"))


if __name__ == "__main__":
    unittest.main()
