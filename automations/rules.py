"""Loading rules.toml and matching events against rules.

A rule:
    [[rule]]
    name = "..."                 # unique
    source = "sms"               # see sources.SOURCES
    enabled = true               # optional
    from = ["@child"]            # optional: numbers, or @aliases from private.toml [contacts]
    package = "com.whatsapp"     # optional: notification source only
    contains_any = ["time"]      # optional: case-insensitive, matched against title + body
    regex = "..."                # optional
    cooldown_seconds = 60        # optional: don't fire again within this window
    at = ["05:00", "06:10"]      # optional: clock source only, local time (one or a list)
    days = ["sun", "mon"]        # optional: clock source only (sun mon tue wed thu fri sat)
    every = 15                   # optional: clock source only, every N minutes (from midnight)
    soon = true                  # optional: clock source only; while the phone is in use
                                 # (screen on, unlocked) it also runs every poll, so it acts at once
    actions = [{ type = "open_app", package = "..." }]
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .actions import ACTIONS
from .sources import SOURCES


def _digits(number: str) -> str:
    """Last 9 digits, so +972 58-..., 058-... and 58... all compare equal."""
    return re.sub(r"\D", "", number)[-9:]


@dataclass
class Rule:
    name: str
    source: str
    actions: list[dict]
    enabled: bool = True
    numbers: set[str] = field(default_factory=set)
    package: str | None = None
    contains_any: list[str] = field(default_factory=list)
    regex: re.Pattern | None = None
    cooldown_seconds: float = 0
    at: list[str] = field(default_factory=list)
    days: list[str] = field(default_factory=list)
    every: int = 0
    soon: bool = False      # clock rules: while the owner is using the phone, also run every tick

    def matches(self, event: dict) -> bool:
        if not self.enabled or event.get("source") != self.source:
            return False
        if self.numbers and _digits(event.get("number", "")) not in self.numbers:
            return False
        if self.at and event.get("time") not in self.at:
            return False
        if self.days and event.get("weekday") not in self.days:
            return False
        if self.every:
            h, _, m = event.get("time", "").partition(":")
            if not (h.isdigit() and m.isdigit()) or (int(h) * 60 + int(m)) % self.every:
                return False
        if self.package and event.get("package") != self.package:
            return False
        text = f"{event.get('title', '')}\n{event.get('body', '')}"
        if self.contains_any and not any(w.lower() in text.lower() for w in self.contains_any):
            return False
        if self.regex and not self.regex.search(text):
            return False
        return True


WEEKDAYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")


class ConfigError(ValueError):
    pass


def load(rules_path: Path, private_path: Path) -> tuple[dict, list[Rule]]:
    cfg = tomllib.loads(rules_path.read_text(encoding="utf-8"))
    private = tomllib.loads(private_path.read_text(encoding="utf-8")) if private_path.exists() else {}
    contacts = private.get("contacts", {})
    settings = {"poll_seconds": 10, **cfg.get("settings", {})}

    rules, names = [], set()
    for raw in cfg.get("rule", []):
        name = raw.get("name")
        if not name:
            raise ConfigError("a rule has no name")
        if name in names:
            raise ConfigError(f"duplicate rule name {name!r}")
        names.add(name)
        if raw.get("source") not in SOURCES:
            raise ConfigError(f"{name}: unknown source {raw.get('source')!r} (have {sorted(SOURCES)})")
        for a in raw.get("actions", []):
            if a.get("type") not in ACTIONS:
                raise ConfigError(f"{name}: unknown action {a.get('type')!r} (have {sorted(ACTIONS)})")
            # An option written "@table" takes that table from private.toml,
            # or else from rules.toml itself (shared settings like [noise]).
            # A list of "@table"s becomes a list of tables.
            def table(opt, ref):
                t = private.get(ref[1:], cfg.get(ref[1:]))
                if not isinstance(t, dict):
                    raise ConfigError(f"{name}: {opt} = {ref}, but there is no [{ref[1:]}] table "
                                      "in private.toml or rules.toml")
                return t
            for opt, val in list(a.items()):
                if opt == "type":
                    continue
                if isinstance(val, str) and val.startswith("@"):
                    a[opt] = table(opt, val)
                elif isinstance(val, list) and val and all(isinstance(v, str) and v.startswith("@") for v in val):
                    a[opt] = [table(opt, v) for v in val]
        at = raw.get("at", [])
        at = [at] if isinstance(at, str) else at
        days = [d.lower()[:3] for d in raw.get("days", [])]
        if raw.get("soon") and raw.get("source") != "clock":
            raise ConfigError(f"{name}: soon = true is for clock rules (they run on a schedule)")
        if bad := [t for t in at if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(t))]:
            raise ConfigError(f"{name}: at = {bad}, expected HH:MM (24-hour)")
        if bad := [d for d in days if d not in WEEKDAYS]:
            raise ConfigError(f"{name}: unknown day(s) {bad} (use {' '.join(WEEKDAYS)})")
        numbers = set()
        for f in raw.get("from", []):
            if f.startswith("@"):
                if f[1:] not in contacts:
                    raise ConfigError(f"{name}: alias {f} is not in private.toml [contacts]")
                vals = contacts[f[1:]]
                numbers |= {_digits(v) for v in ([vals] if isinstance(vals, str) else vals)}
            else:
                numbers.add(_digits(f))
        rules.append(Rule(
            name=name, source=raw["source"], actions=raw.get("actions", []),
            enabled=raw.get("enabled", True), numbers=numbers, package=raw.get("package"),
            contains_any=raw.get("contains_any", []),
            regex=re.compile(raw["regex"], re.I) if raw.get("regex") else None,
            cooldown_seconds=raw.get("cooldown_seconds", 0), at=at, days=days,
            every=int(raw.get("every", 0)), soon=bool(raw.get("soon", False)),
        ))
    return settings, rules
