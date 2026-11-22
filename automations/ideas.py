"""The weekly 💡 Ideas card: suggestions for things that could be automated,
once a week (Friday midday).

The secretary already notes a rule idea for many notifications
(var/secretary/triaged.jsonl). Once a week Claude groups the week's ideas,
drops what the owner already decided or turned down, and picks at most three
that came up more than once. Each gets a card: "✅ Build it" types the idea
into the Claude session on the phone (the owner reads it and presses Enter;
nothing is built or switched on otherwise), "✋ No thanks" means it isn't
suggested again.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

from . import owner
from . import paths
from . import claude
from . import secretary

STATE = secretary.DIR / "ideas.json"
MAX = 3

PROMPT = """Below are ideas for phone automations, noted this week while triaging the owner's
notifications (one per notification, so the same idea repeats in different words). {about}

Group the ideas that mean the same thing. Leave out any idea that goes against the owner's
decisions or that they already turned down (lists below), and ideas for something already
handled. Of the rest, pick at most {max} that came up at least twice and would save the owner
the most bother, most useful first.

Output one JSON object per line, no other text:
{{"title": "<the idea, under 45 chars>", "idea": "<what the automation would do, 1-2 short sentences>",
  "seen": <how many of the notes it covers>}}
If nothing qualifies, output nothing.

The owner's decisions:
{decided}

Turned down before:
{declined}

What the rules already do (rule names, and the noise they clear or hide; e.g. watched WhatsApp/Telegram
groups already go to digests, WhatsApp's "N messages from N chats" is already hidden):
{rules}

This week's ideas (app | idea):
{ideas}"""


def _load() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def week_ideas(days: float = 7, now: datetime | None = None) -> list[str]:
    since = ((now or datetime.now()) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    return [f"{r.get('app', '')} | {r['rule_idea']}" for r in secretary._read(secretary.TRIAGED)
            if r.get("rule_idea") and r.get("at", "") >= since]


def idea_id(title: str) -> str:
    return "idea-" + hashlib.sha1(title.lower().encode()).hexdigest()[:10]


def _rules() -> str:
    import tomllib
    from pathlib import Path
    cfg = tomllib.loads(paths.RULES.read_text(encoding="utf-8"))
    names = [r["name"] for r in cfg.get("rule", []) if r.get("enabled", True)]
    noise = cfg.get("noise", {})
    return ("Rules: " + ", ".join(names) + "\nNoise titles: " + ", ".join(noise.get("titles", []))
            + "\nNoise patterns: " + " | ".join(noise.get("patterns", []))
            + "\nNoise apps: " + ", ".join(noise.get("packages", [])))


def pick(decided: list[str] = (), model: str = "sonnet", ask=None) -> list[dict]:
    """This week's best ideas: [{id, title, idea, seen}]."""
    notes = week_ideas()
    if not notes:
        return []
    st = _load()
    declined = [f"- {d['title']}: {d['idea']}" for d in st.get("declined", [])]
    prompt = PROMPT.format(max=MAX, about=owner.about(), decided="\n".join(f"- {d}" for d in decided) or "(none)",
                           declined="\n".join(declined) or "(none)", rules=_rules(),
                           ideas="\n".join(notes[-300:]))
    ans = (ask or claude.ask)(prompt, [], model=model, connect_wait=0)
    out = []
    for line in ans.text.splitlines():
        line = line.strip().strip(",")
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("title") and d.get("idea"):
            out.append({"id": idea_id(str(d["title"])), "title": str(d["title"])[:60],
                        "idea": str(d["idea"])[:300], "seen": int(d.get("seen") or 0)})
    gone = {d["id"] for d in st.get("declined", [])}
    return [d for d in out if d["id"] not in gone][:MAX]


def offered(ideas: list[dict]) -> None:
    st = _load()
    st["offered"] = {**st.get("offered", {}), **{d["id"]: d for d in ideas}}
    _save(st)


def answer(nid: str, yes: bool) -> dict | None:
    """The owner tapped ✅ or ✋ on an idea's card. Returns the idea."""
    st = _load()
    d = st.get("offered", {}).get(nid)
    if not d:
        return None
    key = "accepted" if yes else "declined"
    st[key] = [x for x in st.get(key, []) if x["id"] != nid] + [{**d, "at": datetime.now().isoformat(timespec="minutes")}]
    _save(st)
    return d


def build_prompt(d: dict) -> str:
    return (f"From the 💡 Ideas card (phone-automations): {d['title']}. {d['idea']} "
            "Look at how it would fit, ask me anything unclear, and show me before switching it on.")
