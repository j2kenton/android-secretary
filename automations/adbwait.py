"""After a reboot, get adb back: bin/automations adb [--wait MINUTES].

A reboot turns Wireless debugging off, and only the owner can turn it back on
(this never changes a phone setting). With --wait, a card says what to turn on
and opens Developer options when tapped; once it's on, adb reconnects by itself
and the card goes away.
"""

from __future__ import annotations

import logging
import os
import time

from . import android

NOTIF_ID = "adb-reconnect"
TITLE = "🔌 Turn on Wireless debugging"
TEXT = ("The phone restarted, so I can't clear other apps' notifications or mark chats "
        "read yet. Turn on Wireless debugging (in Developer options, or its Quick "
        "Settings tile) and I'll reconnect by myself. If it's already on, or shows no "
        "IP address and port, turn it off and on again: after a restart that's often needed.")
OFF = ("adb isn't connected: turn on Wireless debugging (Settings > Developer options; if "
       "it's already on, turn it off and on again), then run this again. Never set up? "
       "SETUP.md step 5.")
POLL_SECONDS = 20
SCAN_EVERY = 6          # the slower scan of local ports, every SCAN_EVERY polls (two minutes)


def run(wait_minutes: float = 0, out=print, sleep=time.sleep, clock=time.monotonic) -> int:
    serial = android.adb_reconnect()
    if serial:
        out(f"adb connected ({serial})")
        return 0
    if wait_minutes <= 0:
        out(OFF)
        return 1
    if not os.path.exists(android.ADB_SERIAL_FILE):     # adb never worked here: don't nag after reboots
        out("adb was never set up here (SETUP.md step 5): not waiting for it")
        return 1
    try:
        android.notify(TITLE, TEXT, notif_id=NOTIF_ID, on_tap=android.cmd_open_dev_settings(),
                       buttons=[("Open Developer options", android.cmd_open_dev_settings())],
                       priority="default")
    except android.AndroidError as e:
        logging.warning("adb: couldn't post the Wireless debugging card: %s", e)
    out(f"waiting up to {wait_minutes:g} min for Wireless debugging")
    end, polls = clock() + wait_minutes * 60, 0
    while clock() < end:
        sleep(POLL_SECONDS)
        polls += 1
        serial = android.adb_reconnect(scan=polls % SCAN_EVERY == 0)
        if serial:
            try:
                android.remove(NOTIF_ID)
                android.toast("adb reconnected")
            except android.AndroidError:
                pass
            out(f"adb connected ({serial})")
            return 0
    out("gave up waiting; " + OFF)
    return 1
