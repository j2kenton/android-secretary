import tomllib
import unittest
from pathlib import Path

from automations import digest as dg
from automations import paths

CFG = tomllib.loads(paths.RULES.read_text(encoding="utf-8"))


def n(pkg, title, text):
    return {"packageName": pkg, "title": title, "content": text}


class NoiseAdded20261114(unittest.TestCase):
    GONE = [n("com.samsung.android.scloud", "Auto back up", "Auto backing up your data…"),
            n("com.samsung.android.lool", "Apps slated for deep sleep",
              "These are apps you haven't used in a while. To save battery, they'll be put in deep sleep."),
            n("com.google.android.googlequicksearchbox", "Reimagine your videos",
              "Turn text, images, and audio into stunning videos with Gemini Omni. Try now."),
            n("com.instagram.barcelona", "Sam Example (sam.example)", "Follow back"),
            n("com.instagram.barcelona", "r00d_queen · New", "THANK YOU KINDLY"),   # Threads: all spam
            n("com.wispr.flowapp", "Wispr Flow", "Voice dictation is ready"),
            n("com.sec.android.daemonapp", "Tomorrow | Ramat Aviv",
              "Generally clear. Highs 22 to 24°C and lows 11 to 13°C."),
            n("com.samsung.android.app.smartcapture", "Screenshot saved", "Tap here to see your screenshot.")]
    KEPT = [n("com.whatsapp", "Sam", "That new app is great. Try now."),       # only the Google app's promos
            n("com.whatsapp", "Sam", "Follow back"),                          # only Threads' prompt
            n("com.wispr.flowapp", "Wispr Flow", "Couldn't transcribe: no microphone access"),
            n("com.samsung.android.lool", "Phone is overheating", "Close some apps")]

    def test_cleared_and_noise(self):
        for table in ("clear", "noise"):
            for x in self.GONE:
                self.assertTrue(dg.is_noise(x, CFG[table]), (table, x["title"]))

    def test_real_messages_kept(self):
        for table in ("clear", "noise"):
            for x in self.KEPT:
                self.assertFalse(dg.is_noise(x, CFG[table]), (table, x["title"]))


if __name__ == "__main__":
    unittest.main()
