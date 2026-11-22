"""Where the owner is, roughly: private.toml [location] lat and lon. A town centre
is enough (not a home address). Used for quiet times (when Shabbat ends) and the weather.
"""

from __future__ import annotations

import logging
import tomllib

from . import paths

log = logging.getLogger("automations")
_warned = False


def get() -> tuple[float, float] | None:
    """(lat, lon), or None if private.toml has no [location] (or under tests). Quiet times
    that need it (Shabbat) are then off: said once in the log."""
    global _warned
    try:
        loc = tomllib.loads(paths.PRIVATE.read_text(encoding="utf-8"))["location"]
        return float(loc["lat"]), float(loc["lon"])
    except (AttributeError, OSError, KeyError, TypeError, ValueError, tomllib.TOMLDecodeError) as e:
        if paths.PRIVATE is not None and not _warned:
            _warned = True
            log.warning("no usable [location] in private.toml (%s): quiet times that need it are off", type(e).__name__)
        return None
