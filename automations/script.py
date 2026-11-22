"""Which writing system a text is in, to keep a suggested reply in the language of
the message it answers.

Claude is asked to reply in the message's language, but with a short message
(a fragment like "( water)") it sometimes picks the owner's other language. A
reply in a different script from the message (Hebrew letters answering Latin
ones, Latin answering Cyrillic, ...) is dropped. Same-script languages (English
and French, say) aren't told apart: that's left to the prompt.
"""

from __future__ import annotations

import unicodedata
from collections import Counter


def dominant(text: str) -> str | None:
    """The script most of the text's letters are in ("LATIN", "HEBREW", "ARABIC",
    "CYRILLIC", ...), or None when it has no letters (emoji, numbers, punctuation)."""
    scripts = Counter()
    for ch in text:
        if ch.isalpha():
            name = unicodedata.name(ch, "")
            if name:
                scripts[name.split()[0]] += 1
    return scripts.most_common(1)[0][0] if scripts else None


def same_language(message: str, reply: str) -> bool:
    """False when the reply is plainly in another script than the message.
    A message or reply with no letters can't be judged, so it passes."""
    m, r = dominant(message), dominant(reply)
    return m is None or r is None or m == r
