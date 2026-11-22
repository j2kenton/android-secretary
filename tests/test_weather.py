"""Weather: a line every morning, a card only when the day is out of the ordinary."""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from automations import actions, weather

DATA = {"daily": {"time": ["2026-11-12", "2026-11-13", "2026-11-14"],
                  "temperature_2m_max": [19.7, 21.4, 22.3], "temperature_2m_min": [11.0, 13.5, 14.6],
                  "precipitation_probability_max": [0, 0, 0], "precipitation_sum": [0.0, 0.0, 0.0],
                  "weather_code": [0, 1, 3]},
        "hourly": {"time": [f"2026-11-13T{h:02d}:00" for h in range(24)], "precipitation_probability": [0] * 24}}


def with_(**changes):
    import copy
    d = copy.deepcopy(DATA)
    for k, v in changes.items():
        if k == "rain_hours":
            for h in v:
                d["hourly"]["precipitation_probability"][h] = 70
        else:
            d["daily"][k][1] = v
    return d


class WeatherTests(unittest.TestCase):
    def setUp(self):
        mock.patch.object(weather, "STATE", Path(tempfile.mkdtemp()) / "w.json").start()

    def tearDown(self):
        mock.patch.stopall()

    def check(self, data, hour=5, morning=True):
        with mock.patch.object(weather, "fetch", return_value=data):
            return weather.check(32.08, 34.78, now=datetime(2026, 11, 13, hour), morning=morning)

    def test_ordinary_day_is_one_line_and_no_card(self):
        r = self.check(DATA)
        self.assertEqual(r["line"], "🌤 21°/14°, 2° warmer than yesterday")
        self.assertEqual(r["new"], {})

    def test_rain_and_big_swings_are_significant(self):
        r = self.check(with_(precipitation_probability_max=70, precipitation_sum=4.0, rain_hours=[15, 16]))
        self.assertEqual(r["new"]["rain"], "Rain likely from 15:00 (70%, ~4 mm)")
        self.assertIn("colder", self.check(with_(temperature_2m_max=13.0))["new"])
        self.assertIn("warmer", self.check(with_(temperature_2m_max=27.0))["new"])

    def test_during_the_day_only_new_things_and_only_rain_still_ahead(self):
        rainy = with_(precipitation_probability_max=70, rain_hours=[9])
        weather.mark_shown("2026-11-13", ["colder"])
        self.assertEqual(self.check(rainy, hour=12, morning=False)["new"], {})       # rain was this morning
        r = self.check(with_(precipitation_probability_max=70, rain_hours=[15]), hour=12, morning=False)
        self.assertIn("rain", r["new"])
        weather.mark_shown("2026-11-13", ["rain"])
        self.assertEqual(self.check(with_(precipitation_probability_max=70, rain_hours=[15]), hour=13, morning=False)["new"], {})

    def test_card_opens_the_weather_app(self):
        r = self.check(with_(precipitation_probability_max=70, rain_hours=[15]))
        with mock.patch.object(actions.android, "notify") as notify:
            actions._weather_card(r)
        title = notify.call_args.args[0]
        self.assertTrue(title.startswith("🌤 Rain likely from 15:00"))
        self.assertIn("AppLauncherActivity", notify.call_args.kwargs["on_tap"])
        self.assertEqual(self.check(with_(precipitation_probability_max=70, rain_hours=[15]), hour=6)["new"], {})  # 06:10: no second card

    def test_card_can_be_forwarded(self):
        """A button to send the weather card on to a partner."""
        r = self.check(with_(precipitation_probability_max=70, rain_hours=[15]))
        with mock.patch.object(actions.android, "notify") as notify, \
                mock.patch.object(actions, "_number", return_value="+972500000000"), \
                mock.patch.object(actions.android, "cmd_whatsapp_reply", return_value="WA") as wa:
            actions._weather_card(r, "Dana")
        labels = [b[0] for b in notify.call_args.kwargs["buttons"]]
        self.assertEqual(labels, ["🌤 Weather", "↪ Forward to Dana"])
        self.assertIn("Rain likely from 15:00", wa.call_args.args[0])
