"""bin/automations reaches every subcommand of the program: card buttons run
`bin/automations <cmd>`, and one it doesn't know only prints its usage (the
Ensemble 💤 button did nothing that way)."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _program() -> set[str]:
    return set(re.findall(r'add_parser\("([a-z-]+)"', (ROOT / "automations" / "__main__.py").read_text()))


class Wrapper(unittest.TestCase):
    def test_every_subcommand_gets_through_the_wrapper(self):
        wrapper = set()
        for pattern in re.findall(r"^\s*([a-z|-]+)\)", (ROOT / "bin" / "automations").read_text(), re.M):
            wrapper.update(pattern.split("|"))
        self.assertEqual(sorted(_program() - {"run"} - wrapper), [], "not passed on by bin/automations")

    def test_every_button_command_is_a_subcommand(self):
        used = set()
        for f in (ROOT / "automations").glob("*.py"):
            text = f.read_text()
            used.update(re.findall(r"\{run\} ([a-z-]+)", text))
            used.update(re.findall(r"'automations'\)\)\}\s+([a-z-]+)", text))
        self.assertIn("ensemble-snooze", used, "found no button commands: the patterns above need updating")
        self.assertEqual(sorted(used - _program()), [], "buttons run unknown subcommands")


if __name__ == "__main__":
    unittest.main()
