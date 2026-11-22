"""What the owner tells Claude about themselves, and other optional settings from
private.toml, read when needed (so an edit counts without a restart).

[owner] about: a line about the owner for Claude's prompts (where they live, languages,
family, work). Without it, nothing is said: everything still works, just less tuned.
"""

from __future__ import annotations

import tomllib

from . import paths


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
