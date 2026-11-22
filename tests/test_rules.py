"""Rule matching tests. Run: python -m unittest discover -s tests"""

import tempfile
import unittest
from pathlib import Path

from automations.engine import Engine
from automations.rules import ConfigError, load

RULES = """
[[rule]]
name = "time-request"
source = "sms"
from = ["@kid"]
contains_any = ["time", "זמן"]
cooldown_seconds = 60
actions = [{ type = "log" }]
"""
PRIVATE = '[contacts]\nkid = ["+972501234567"]\n'


def sms(body, number="+972501234567", i=1):
    return {"source": "sms", "id": f"sms:{i}", "number": number, "body": body}


class RuleTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "rules.toml").write_text(RULES)
        (self.dir / "private.toml").write_text(PRIVATE)
        _, self.rules = load(self.dir / "rules.toml", self.dir / "private.toml")
        self.rule = self.rules[0]

    def test_matches_his_real_phrasings(self):
        for body in ["Can I have some time please?", "Can I have more time please?",
                     "I'm coming home can I have time please?",
                     "Um... the time finished, can I have more please?", "אפשר עוד זמן?"]:
            self.assertTrue(self.rule.matches(sms(body)), body)

    def test_number_formats_compare_equal(self):
        for number in ["0501234567", "+972 50-123-4567", "972501234567"]:
            self.assertTrue(self.rule.matches(sms("more time?", number)), number)

    def test_ignores_other_senders_and_topics(self):
        self.assertFalse(self.rule.matches(sms("more time?", "+972509999999")))
        self.assertFalse(self.rule.matches(sms("I'm home")))
        self.assertFalse(self.rule.matches({**sms("time"), "source": "notification"}))

    def test_cooldown_fires_once(self):
        eng = Engine(self.rules, self.dir / "state.json", dry_run=True)
        self.assertEqual(eng.handle(sms("time please", i=1)), ["time-request"])
        self.assertEqual(eng.handle(sms("time please??", i=2)), [])

    def test_unknown_alias_is_a_config_error(self):
        (self.dir / "private.toml").write_text("[contacts]\n")
        with self.assertRaises(ConfigError):
            load(self.dir / "rules.toml", self.dir / "private.toml")


if __name__ == "__main__":
    unittest.main()


class SoonTests(unittest.TestCase):
    """While the phone is in use, `soon` clock rules run every tick."""

    def setUp(self):
        from unittest import mock
        from automations import actions
        from automations.rules import Rule
        self.calls = []
        self.result = None
        self.patch = mock.patch.dict(actions.ACTIONS, {"probe": lambda e, **_: self.calls.append(e) or self.result})
        self.patch.start()
        self.rule = Rule(name="digest", source="clock", actions=[{"type": "probe"}], every=2, soon=True)
        self.eng = Engine([self.rule], Path(tempfile.mkdtemp()) / "state.json")
        self.eng.sources = []

    def tearDown(self):
        self.patch.stop()

    def test_runs_every_tick_only_while_in_use(self):
        self.eng.in_use_check = lambda: False
        self.eng.tick()
        self.assertEqual(self.calls, [])
        self.eng.in_use_check = lambda: True
        self.eng.tick()
        self.eng.tick()
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(self.calls[0]["soon"])
        self.eng.in_use_check = lambda: None          # adb not reachable: the usual pace
        self.eng.tick()
        self.assertEqual(len(self.calls), 2)

    def test_a_failure_pauses_soon_runs(self):
        from automations.actions import FAILED
        self.eng.in_use_check = lambda: True
        self.result = FAILED
        self.eng.tick()
        self.eng.tick()
        self.assertEqual(len(self.calls), 1)

    def test_soon_is_for_clock_rules(self):
        d = Path(tempfile.mkdtemp())
        (d / "rules.toml").write_text('[[rule]]\nname = "x"\nsource = "sms"\nsoon = true\n')
        with self.assertRaises(ConfigError):
            load(d / "rules.toml", d / "private.toml")


class ExampleFilesTests(unittest.TestCase):
    """A fresh clone's starting point: the example rules load with the example private settings."""

    def test_examples_load_together(self):
        root = Path(__file__).resolve().parent.parent
        settings, rules = load(root / "rules.example.toml", root / "private.example.toml")
        self.assertGreater(len(rules), 20)
        self.assertIn("morning-summary", {r.name for r in rules})
