"""bin/automations doctor: one line per check, needed ones counted, nothing stops it."""

import unittest

from automations import doctor


def boom():
    raise RuntimeError("no such thing")


class DoctorTest(unittest.TestCase):
    def run_checks(self, checks):
        lines = []
        return doctor.run(checks, out=lines.append), lines

    def test_all_well(self):
        failed, lines = self.run_checks([("A", True, lambda: (True, "fine")), ("B", False, lambda: (True, "fine"))])
        self.assertEqual(failed, 0)
        self.assertTrue(lines[0].startswith("✓ A"))
        self.assertEqual(lines[-1], "All set.")

    def test_needed_and_optional_failures(self):
        failed, lines = self.run_checks([
            ("Needed", True, lambda: (False, "do this")),
            ("Optional", False, lambda: (False, "turn it on")),
            ("Crashes", True, boom),
        ])
        self.assertEqual(failed, 2)                       # the optional one isn't counted
        self.assertTrue(lines[0].startswith("✗ Needed") and lines[0].endswith("do this"))
        self.assertTrue(lines[1].startswith("– Optional"))
        self.assertIn("check failed: no such thing", lines[2])
        self.assertIn("2 thing(s) to fix", lines[-1])

    def test_settings_still_the_example(self):
        # Read-only: whatever this folder holds, it answers without crashing.
        ok, detail = doctor.private_settings()
        self.assertIsInstance(ok, bool)
        self.assertTrue(detail)


if __name__ == "__main__":
    unittest.main()
