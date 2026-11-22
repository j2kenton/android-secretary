"""Ask Claude Code (installed in the proot Ubuntu) a one-off question, headless.

The daemon runs natively in Termux, so this goes through `proot-distro login`.
Only the tools named in `tools` are available (the run is non-interactive and
anything else is denied), so a caller can keep it read-only. claude.ai
connectors (Gmail, Calendar) connect in the background after startup, and a
prompt sent straight away can find them missing (the model then may answer
without them). So the prompt goes in over stream-json input only after
`connect_wait` seconds, and it should load the tools it needs with
ToolSearch, which is always allowed.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass
from .paths import VAR

CLAUDE = "/root/.local/bin/claude"
DISTRO = "ubuntu"
PROOT_DISTRO = os.environ.get("PREFIX", "/data/data/com.termux/files/usr") + "/bin/proot-distro"


class ClaudeError(RuntimeError):
    pass


class Blocked(ClaudeError):
    """The API refused us (e.g. Cloudflare's 403 "Attention Required"): not a
    logout. Claude Code shows it as "Please run /login", but logging in again
    doesn't help; it lifts by itself after a few minutes."""


# Seen 2026-11-13: "Failed to authenticate. API Error: 403 Attention Required! | Cloudflare"
# for about 4 minutes, in the daemon and the interactive session alike, with valid credentials.
BLOCK_RE = re.compile(r"API Error: 40[13]|Failed to authenticate|Attention Required|Cloudflare")
BLOCK_FILE = str(VAR / "claude-blocked.json")
BACKOFF = 300            # while blocked, don't call at all for this long (retries make it worse)
WARN_AFTER = 15 * 60     # blocked this long: tell the owner, once


def _block_state() -> dict:
    try:
        with open(BLOCK_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _set_blocked(detail: str, now: float | None = None) -> dict:
    now = now or time.time()
    st = _block_state()
    st.setdefault("since", now)
    st.update(until=now + BACKOFF, detail=detail[-200:])
    if now - st["since"] >= WARN_AFTER and not st.get("warned"):
        st["warned"] = True
        try:
            from . import android
            android.notify("⚠️ Claude is being refused (not a logout)",
                           f"Since {time.strftime('%H:%M', time.localtime(st['since']))}: {st['detail'][-90:]}\n"
                           "No need to /login. Digests wait and catch up when it lifts.",
                           notif_id="claude-blocked", priority="default")
        except Exception:                       # the warning is a nicety; never fail the caller on it
            pass
    with open(BLOCK_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f)
    return st


def _clear_blocked() -> None:
    st = _block_state()
    if not st:
        return
    os.remove(BLOCK_FILE)
    if st.get("warned"):
        try:
            from . import android
            android.remove("claude-blocked")
        except Exception:
            pass


@dataclass
class Answer:
    text: str                 # the final answer
    tools_called: list[str]   # names of tools actually run, in order
    tool_output: str          # everything those tools returned, for checking the answer against


def ask(prompt: str, tools: list[str], model: str = "opus", timeout: float = 120,
        attempts: int = 3, require_tool: str | None = None, connect_wait: float = 10) -> Answer:
    """Run the prompt, retrying a try that hangs (startup sometimes stalls in
    plugin sync), answers with a tool call written out as text, or never ran
    `require_tool` (a model that couldn't reach a connector may otherwise
    answer from imagination). A good run takes about 20-40s."""
    st = _block_state()
    if st.get("until", 0) > time.time():
        raise Blocked(f"API refusing us since {time.strftime('%H:%M', time.localtime(st['since']))}; "
                      f"next try after {time.strftime('%H:%M', time.localtime(st['until']))}")
    problem = ""
    for _ in range(attempts):
        try:
            ans = _ask_once(prompt, tools, model, timeout, connect_wait)
        except ClaudeError as e:
            problem = str(e)
            if BLOCK_RE.search(problem):        # retrying straight away only adds to it
                _set_blocked(problem)
                raise Blocked(f"API refused: {problem[-120:]}") from e
            continue
        if re.match(r"(Failed to authenticate|API Error: 40[13]|Please run /login)", ans.text):  # not a real answer
            _set_blocked(ans.text)
            raise Blocked(f"API refused: {ans.text[-120:]}")
        _clear_blocked()
        if any(tag in ans.text for tag in ("<invoke", "<parameter", "</invoke")):
            problem = f"garbled answer {ans.text[:80]!r}"
        elif require_tool and require_tool not in ans.tools_called:
            problem = f"never called {require_tool} (called {ans.tools_called})"
        else:
            return ans
    raise ClaudeError(f"no usable answer after {attempts} tries: {problem}")


def _ask_once(prompt: str, tools: list[str], model: str, timeout: float,
              connect_wait: float) -> Answer:
    allowed = ["ToolSearch", *tools]
    cmd = " ".join([
        "cd /tmp && DISABLE_AUTOUPDATER=1", CLAUDE, "-p", "--input-format stream-json",
        "--tools ToolSearch", "--allowedTools", shlex.quote(" ".join(allowed)),
        "--permission-mode dontAsk", "--model", shlex.quote(model),
        "--no-session-persistence", "--disable-slash-commands",
        "--output-format stream-json --verbose",
    ])
    # Own process group, so a hung try is killed whole (proot, bash, claude).
    proc = subprocess.Popen([PROOT_DISTRO, "login", DISTRO, "--", "bash", "-lc", cmd],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)
    time.sleep(connect_wait)                      # let claude.ai connectors connect
    message = {"type": "user", "message": {"role": "user", "content": prompt}}
    try:
        stdout, stderr = proc.communicate(json.dumps(message) + "\n", timeout=timeout)
    except subprocess.TimeoutExpired as e:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise ClaudeError(f"no answer within {timeout:.0f}s") from e
    if proc.returncode != 0:
        result = parse_stream(stdout).text       # the CLI's own one-line reason, if it gave one
        raise ClaudeError(result[:300] if result else (stderr or stdout).strip()[-300:] or f"exit {proc.returncode}")
    return parse_stream(stdout)


def parse_stream(stdout: str) -> Answer:
    """stream-json events -> Answer: tool_use names, tool_result text, final result."""
    text, called, outputs = "", [], []
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "result":
            text = ev.get("result") or ""
        for c in (ev.get("message") or {}).get("content") or []:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "tool_use":
                called.append(c.get("name", ""))
            elif c.get("type") == "tool_result":
                content = c.get("content")
                outputs.append(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False))
    return Answer(text.strip(), called, "\n".join(outputs))
