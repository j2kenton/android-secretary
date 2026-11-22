"""The polling loop: ask each needed source for new events, run matching rules."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path

from . import android
from .actions import ACTIONS, FAILED
from .rules import Rule
from .sources import SOURCES

log = logging.getLogger("automations")


class Engine:
    SOON_PAUSE = 300        # seconds without "soon" runs after a rule's action failed

    def __init__(self, rules: list[Rule], state_path: Path, dry_run: bool = False):
        self.rules = rules
        self.state_path = state_path
        self.dry_run = dry_run
        self.state = json.loads(state_path.read_text()) if state_path.exists() else {}
        self.last_fired: dict[str, float] = {}
        needed = {r.source for r in rules if r.enabled}
        self.sources = [SOURCES[name](self.state) for name in sorted(needed)]
        self.in_use = False
        self.soon_paused: dict[str, float] = {}
        self.in_use_check = android.in_use      # replaced in tests

    def save(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state))
        tmp.replace(self.state_path)

    def handle(self, event: dict) -> list[str]:
        fired = []
        for rule in self.rules:
            if not rule.matches(event):
                continue
            now = time.time()
            if now - self.last_fired.get(rule.name, 0) < rule.cooldown_seconds:
                log.info("rule %s matched %s but is cooling down", rule.name, event["id"])
                continue
            self.last_fired[rule.name] = now
            fired.append(rule.name)
            log.info("rule %s fired on %s", rule.name, event["id"])
            if self.dry_run:
                continue
            self._run_actions(rule, event)
        return fired

    def _run_actions(self, rule: Rule, event: dict) -> bool:
        """Run the rule's actions; False if any failed (raised, or said so)."""
        ok = True
        for action in rule.actions:
            opts = {k: v for k, v in action.items() if k != "type"}
            opts["_state"] = self.state.setdefault("actions", {}).setdefault(action["type"], {})
            try:
                ok = ACTIONS[action["type"]](event, **opts) != FAILED and ok
            except Exception:
                log.exception("rule %s: action %s failed", rule.name, action["type"])
                ok = False
        return ok

    def run_soon(self) -> None:
        """While the phone is in use, the `soon` rules (digests, triage) run
        every tick, right after new notifications were collected, instead of
        waiting for their minute (on the phone, things are handled at once). They do nothing when
        nothing is queued. Not logged as "fired": that would be every tick."""
        soon = [r for r in self.rules if r.enabled and r.soon]
        if not soon:
            return
        was, self.in_use = self.in_use, bool(self.in_use_check())
        if self.in_use != was:
            log.info("phone %s", "in use: acting at once" if self.in_use else "not in use: back to the usual pace")
        if not self.in_use or self.dry_run:
            return
        now = datetime.now()
        event = {"source": "clock", "id": f"soon:{now.isoformat(timespec='seconds')}", "soon": True,
                 "time": now.strftime("%H:%M"), "weekday": now.strftime("%a").lower(),
                 "date": now.strftime("%Y-%m-%d")}
        for rule in soon:
            # After a failure (Claude down, say) only the rule's own schedule
            # retries for a while, not every few seconds.
            if time.time() < self.soon_paused.get(rule.name, 0):
                continue
            if not self._run_actions(rule, event):
                self.soon_paused[rule.name] = time.time() + self.SOON_PAUSE

    def tick(self) -> None:
        for source in self.sources:
            try:
                events = source.poll()
            except Exception as e:
                log.warning("source %s poll failed: %s", source.name, e)
                continue
            for event in events:
                self.handle(event)
        self.run_soon()
        self.save()

    def run(self, poll_seconds: float, in_use_seconds: float | None = None) -> None:
        """Poll every `poll_seconds`, or every `in_use_seconds` while the phone is in use."""
        log.info("started: %d rule(s), sources %s, every %ss (%ss while the phone is in use)",
                 len(self.rules), [s.name for s in self.sources], poll_seconds, in_use_seconds or poll_seconds)
        while True:
            started = time.monotonic()
            self.tick()
            wait = in_use_seconds if self.in_use and in_use_seconds else poll_seconds
            time.sleep(max(1.0, wait - (time.monotonic() - started)))
