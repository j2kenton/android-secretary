"""What the owner tells Claude about themselves, and other optional settings from
private.toml, read when needed (so an edit counts without a restart).

[owner] about: a line about the owner for Claude's prompts (where they live, languages,
family, work). Without it, nothing is said: everything still works, just less tuned.
"""

from __future__ import annotations

import tomllib

from . import paths


# Added to every prompt whose words reach the owner's phone (cards, summary lines, titles).
VOICE = """Whatever you write for the owner (a title, line, summary or suggestion) is shown to them as is, on their phone.
Write it to them: "you", "your son", "asks you to call", never "the owner", "the user", or "he"/"him" for them.
Say what happened and what they need to know or do, plainly. Leave out your own handling and judging: no "glance and
clear", "nothing to do", "no action needed", "no question asked", "unclear message", "summary line only". If there's
nothing for them to do, just say what it is. Don't guess at a meaning the message doesn't give."""


def setting(table: str, key: str, default=None):
    """private.toml [table] key, or default (no private.toml, under tests, or not set)."""
    try:
        return tomllib.loads(paths.PRIVATE.read_text(encoding="utf-8")).get(table, {}).get(key, default)
    except (AttributeError, OSError, tomllib.TOMLDecodeError):
        return default


def about(prefix: str = "About the owner: ") -> str:
    """"About the owner: …" for a prompt, or "" if private.toml has no [owner] about."""
    text = str(setting("owner", "about", "") or "").strip()
    return f"{prefix}{text}" if text else ""
