"""Secretary, Ensemble status line, clock `every`, and @tables from rules.toml."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from automations import ensemble, secretary
from automations import paths
from automations.rules import load

NOISE = {"packages": ["com.termux"], "titles": ["New voicemail"]}



# Cards with reminders are remembered in var/reminders: keep tests out of the real one.
_reminders_patch = None


def setUpModule():
    global _reminders_patch
    import tempfile
    from pathlib import Path as _P
    from automations import reminders
    d = _P(tempfile.mkdtemp())
    _reminders_patch = [mock.patch.object(reminders, "DIR", d), mock.patch.object(reminders, "CARDS", d / "cards.json")]
    for p in _reminders_patch:
        p.start()


def tearDownModule():
    for p in _reminders_patch:
        p.stop()

class SecretaryTests(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.patches = [mock.patch.object(secretary, "DIR", d),
                        mock.patch.object(secretary, "QUEUE", d / "q.jsonl"),
                        mock.patch.object(secretary, "TRIAGED", d / "t.jsonl"),
                        mock.patch("automations.android.notification_extras", return_value={})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def ev(self, title, body="", package="com.whatsapp"):
        return {"source": "notification", "package": package, "title": title, "body": body}

    def test_collect_skips_noise_and_repeats(self):
        state = {}
        self.assertTrue(secretary.collect(self.ev("Mum", "call me"), NOISE, state))
        self.assertFalse(secretary.collect(self.ev("Mum", "call me"), NOISE, state))
        self.assertFalse(secretary.collect(self.ev("New voicemail", "Ultra", "com.android.phone"), NOISE, state))
        self.assertFalse(secretary.collect(self.ev("Termux", "1 session", "com.termux"), NOISE, state))
        self.assertEqual(len(secretary._read(secretary.QUEUE)), 1)

    def test_collect_keeps_the_posts_community_and_full_text(self):
        """A Blind post's line said "rumored big January
        layoff" without saying it was Acme."""
        extras = {"subText": "Acme", "bigText": "Hello, I have recently heard from my M2 that there are rumors "
                                                     "going around that there is going to be a pretty big layoff"}
        with mock.patch("automations.android.notification_extras", return_value=extras):
            secretary.collect({"package": "com.teamblind.blind", "title": "January Layoffs",
                               "body": "Hello, I have recently heard", "key": "k1"}, NOISE, {})
        it = secretary._read(secretary.QUEUE)[0]
        self.assertEqual(it["context"], "Acme")
        self.assertTrue(it["text"].endswith("pretty big layoff"))
        with mock.patch.object(secretary.claude, "ask",
                               return_value=secretary.claude.Answer("", [], "")) as ask:
            secretary.triage([])
        self.assertIn('"context": "Acme"', ask.call_args[0][0])

    def test_collect_leaves_out_context_that_repeats_the_app_or_title(self):
        with mock.patch("automations.android.notification_extras",
                        return_value={"subText": "Blind", "conversationTitle": "January Layoffs"}):
            secretary.collect({"package": "com.teamblind.blind", "title": "January Layoffs",
                               "body": "Hello", "key": "k1"}, NOISE, {})
        self.assertNotIn("context", secretary._read(secretary.QUEUE)[0])

    def test_line_doesnt_repeat_the_app(self):
        r = {"at": "2026-11-18 19:10", "app": "Blind", "keep": "summary", "kind": "dismiss",
             "summary": "Blind: rumored big January layoff"}
        self.assertEqual(secretary.line(r, today="2026-11-18"), "19:10 📰 Blind · rumored big January layoff")

    def test_triage_records_suggestions_and_keeps_skipped_items(self):
        secretary.collect(self.ev("Mum", "call me"), NOISE, {})
        secretary.collect(self.ev("Promo", "50% off"), NOISE, {})
        answer = ('{"i": 0, "summary": "Mum asks for a call", "kind": "reply", '
                  '"suggestion": "Offer a reply: calling at 8", "rule_idea": ""}\nsome chatter')
        with mock.patch.object(secretary.claude, "ask",
                               return_value=secretary.claude.Answer(answer, [], "")):
            records = secretary.triage(["morning-summary"])
        self.assertEqual([r["kind"] for r in records], ["reply"])
        self.assertEqual(len(secretary._read(secretary.TRIAGED)), 1)
        left = secretary._read(secretary.QUEUE)
        self.assertEqual([(it["title"], it["tries"]) for it in left], [("Promo", 1)])

    def test_reply_in_another_language_is_dropped(self):
        """An English fragment ("( water)") got a suggested reply in Hebrew. A reply must
        be in the message's language: one in another script is dropped, the card stays."""
        secretary.collect(self.ev("Partner", "( water)"), NOISE, {})
        secretary.collect(self.ev("Sam", "Are you coming?"), NOISE, {})
        answer = "\n".join(json.dumps(d, ensure_ascii=False) for d in [
            {"i": 0, "summary": "a fragment about water", "kind": "reply", "suggestion": "Ask what she needs",
             "reply": "מה קרה עם המים?"},
            {"i": 1, "summary": "asks if you're coming", "kind": "reply", "suggestion": "Answer Sam",
             "reply": "Yes, on my way"}])
        with mock.patch.object(secretary.claude, "ask", return_value=secretary.claude.Answer(answer, [], "")):
            records = secretary.triage([])
        self.assertEqual([(r["kind"], r["reply"]) for r in records], [("reply", ""), ("reply", "Yes, on my way")])

    def test_parse_rejects_bad_kinds_and_indexes(self):
        ans = "\n".join(json.dumps(d) for d in [
            {"i": 0, "kind": "reply", "summary": "a", "suggestion": "b"},
            {"i": 5, "kind": "reply", "summary": "a", "suggestion": "b"},
            {"i": 1, "kind": "panic", "summary": "a", "suggestion": "b"}])
        self.assertEqual(list(secretary.parse(ans, 2)), [0])

    def test_judged_apps_get_keep_and_actions(self):
        """Blind is judged post by post: clear and summary snooze the notification,
        card leaves it; a summary post becomes a 📰 line; other apps are untouched."""
        from automations import actions
        blind = "com.teamblind.blind"
        for i, (t, b) in enumerate([("Okay who broke everything", "How"), ("Laid off at X?", "maybe"),
                                    ("Acme team offsite", "4 rounds"), ("No keep given", "?")]):
            secretary.collect({**self.ev(t, b, blind), "key": f"k{i}"}, NOISE, {})
        secretary.collect({**self.ev("Mum", "call me"), "key": "km"}, NOISE, {})
        rows = [(0, "clear"), (1, "summary"), (2, "card"), (3, None), (4, "clear")]
        answer = "\n".join(json.dumps({"i": i, "kind": "later", "summary": f"s{i}", "suggestion": "x",
                                       **({"keep": k} if k else {})}) for i, k in rows)
        seen = {}
        with mock.patch.object(secretary.claude, "ask", side_effect=lambda p, *a, **k: seen.update(p=p) or
                               secretary.claude.Answer(answer, [], "")), \
             mock.patch.object(secretary, "JUDGED", secretary.DIR / "j.jsonl"), \
             mock.patch.object(actions.android, "snooze", return_value=True) as snooze, \
             mock.patch.object(actions.android, "notifications", return_value=[]), \
             mock.patch.object(actions.summaries, "post") as post:
            actions.secretary_triage({}, secretary_cfg={"judge": {blind: "taste here"}}, _state={})
        self.assertIn('"judge": true', seen["p"])
        self.assertIn("taste here", seen["p"])
        self.assertEqual(seen["p"].count('"judge": true}'), 4)            # not Mum
        self.assertEqual(sorted(c.args[0] for c in snooze.call_args_list), ["k0", "k1"])
        lines = [l for _, l in post.call_args.args[2]]
        self.assertTrue(any(l.endswith("📰 Blind · s1") for l in lines))
        self.assertFalse(any("s0" in l or "s2" in l or "s3" in l for l in lines))
        self.assertTrue(any("s4" in l for l in lines))                      # Mum: an ordinary "later" line
        log = [(r["title"], r["keep"], r["done"]) for r in secretary._read(secretary.DIR / "j.jsonl")]
        self.assertEqual(log, [("Okay who broke everything", "clear", True), ("Laid off at X?", "summary", True),
                               ("Acme team offsite", "card", True), ("No keep given", "card", True)])

    def test_glance_and_clear_moves_to_the_card(self):
        """A receipt "can just be in a summary": a line on 🗂, the notification
        swiped (not snoozed). Chats, reminders and one-time codes stay in the shade. Not email:
        Gmail's notification stays as it is, and no 🗂 line."""
        from automations import actions
        evs = [("no-reply", "אישור תשלום לבית הספר", "com.google.android.gm"),
               ("Amazon", "Your verification code is 123456", "com.google.android.gm"),
               ("Physio", "Mon 11:00", "com.google.android.calendar"),
               ("Dan", "ok thanks", "com.whatsapp")]
        for i, (t, b, pkg) in enumerate(evs):
            secretary.collect({**self.ev(t, b, pkg), "key": f"k{i}"}, NOISE, {})
        evs.append(("Israel_Post", "היי, איך נשמעת לך חופשה לגמר ליגת האלופות?", "com.samsung.android.messaging"))
        secretary.collect({**self.ev(*evs[-1][:2], evs[-1][2]), "key": "k4"}, NOISE, {})
        evs.append(("Cal", "דף הפירוט הדיגיטלי של כרטיס האשראי שלך", "com.samsung.android.messaging"))
        secretary.collect({**self.ev(*evs[-1][:2], evs[-1][2]), "key": "k5"}, NOISE, {})
        kinds = {4: "ignore", 5: "later"}
        answer = "\n".join(json.dumps({"i": i, "kind": kinds.get(i, "dismiss"), "summary": f"s{i}",
                                       "suggestion": "x"}) for i in range(len(evs)))
        with mock.patch.object(secretary.claude, "ask", return_value=secretary.claude.Answer(answer, [], "")), \
             mock.patch.object(actions.android, "dismiss", return_value=True) as dismiss, \
             mock.patch.object(actions.android, "snooze") as snooze, \
             mock.patch.object(actions.android, "notifications", return_value=[]), \
             mock.patch.object(actions.summaries, "post") as post:
            actions.secretary_triage({}, _state={})
        self.assertEqual([c.args[2] for c in dismiss.call_args_list], ["k4", "k5"])  # spam: no line; statement: on 🗂
        snooze.assert_not_called()
        lines = [l for _, l in post.call_args.args[2]]
        self.assertEqual(len(lines), 1)
        self.assertIn("s5", lines[0])                   # the Gmail receipt (s0): neither swiped nor listed

    def test_summary_line_is_a_record_with_its_time(self):
        """The Family Link line read "→ Location log: clear it and add a line to the
        summary" (an instruction) with no time; a log needs when it happened."""
        r = {"at": "2026-11-17 15:12", "app": "Family Link", "summary": "Ari left ישיבת אופק", "kind": "dismiss",
             "suggestion": "Location log: clear it and add a line to the summary"}
        self.assertEqual(secretary.line(r, today="2026-11-17"), "15:12 👋 Family Link · Ari left ישיבת אופק")
        self.assertEqual(secretary.line(r, today="2026-11-18"), "17/11 15:12 👋 Family Link · Ari left ישיבת אופק")
        later = {**r, "kind": "later", "summary": "Swim lessons", "suggestion": "Call back this week"}
        self.assertTrue(secretary.line(later, today="2026-11-17").endswith("→ Call back this week"))

    def test_one_card_per_person_with_calls(self):
        """A missed call and WhatsApp messages from the same person: one card."""
        from automations import actions
        state = {}
        def triage(items, live):
            for pkg, title, body in items:
                secretary.collect({"package": pkg, "title": title, "body": body, "key": f"k{body}"}, NOISE, {})
            answer = "\n".join(json.dumps({"i": i, "kind": "reply", "summary": f"Shoshana: {b}", "suggestion": "answer her"})
                               for i, (_p, _t, b) in enumerate(items))
            with mock.patch.object(secretary.claude, "ask", return_value=secretary.claude.Answer(answer, [], "")), \
                 mock.patch.object(actions.android, "notifications",
                                   return_value=[{"packageName": "com.termux.api", "tag": t} for t in live]), \
                 mock.patch.object(actions, "_chat_jid", return_value=None), \
                 mock.patch.object(actions.android, "contacts", return_value={"Shoshana B": "050-111-2222"}), \
                 mock.patch.object(actions.summaries, "post"), \
                 mock.patch.object(actions, "_own_card") as card:
                actions.secretary_triage({}, _state=state)
            return card
        card = triage([("com.truecaller", "Missed call", "Shoshana B")], [])
        nid = card.call_args.args[0]
        self.assertEqual([b[0] for b in card.call_args.args[3]][:1], ["📞 Call back"])
        card = triage([("com.whatsapp", "Shoshana B", "are you home?")], [nid])
        self.assertEqual(card.call_args.args[0], nid)                            # the same card
        self.assertEqual(card.call_args.args[1], "💬 Shoshana B · 2 updates")
        self.assertIn("📞 Shoshana: Shoshana B", card.call_args.args[2])
        labels = [b[0] for b in card.call_args.args[3]]
        self.assertIn("📞 Call back", labels)
        self.assertTrue(any("Open chat" in l for l in labels))
        self.assertIn("termux-telephony-call", dict(card.call_args.args[3])["📞 Call back"])

    def test_one_card_per_chat(self):
        """James's messages popped up as a card each. One chat, one card,
        its messages listed; later ones join it while it's up, and start afresh once it's gone."""
        from automations import actions
        def triage(msgs, live, state):
            for i, (b, kind) in enumerate(msgs):
                secretary.collect({**self.ev("James", b), "key": f"k{b}"}, NOISE, {})
            answer = "\n".join(json.dumps({"i": i, "kind": k, "summary": f"James: {b}", "suggestion": "answer him"})
                               for i, (b, k) in enumerate(msgs))
            with mock.patch.object(secretary.claude, "ask", return_value=secretary.claude.Answer(answer, [], "")), \
                 mock.patch.object(actions.android, "notifications",
                                   return_value=[{"packageName": "com.termux.api", "tag": t} for t in live]), \
                 mock.patch.object(actions, "_chat_jid", return_value=None), \
                 mock.patch.object(actions.android, "contacts", return_value={"James": "050-000-0000"}), \
                 mock.patch.object(actions.summaries, "post") as post, \
                 mock.patch.object(actions, "_own_card") as card:
                actions.secretary_triage({}, _state=state)
            return card, post
        state = {}
        card, post = triage([("only 90%", "reply"), ("10% is lots of work", "reply"), ("sol 6.1", "later")], [], state)
        nid = card.call_args_list[0].args[0]
        self.assertTrue(nid.startswith("person-"))
        self.assertEqual({c.args[0] for c in card.call_args_list}, {nid})        # one card, re-posted
        self.assertEqual(card.call_args_list[-1].args[1], "💬 James · 3 messages")     # "later" joins the card
        card, post = triage([("loads", "later")], [nid], state)                  # card still up: joins it
        self.assertEqual(card.call_args.args[1], "💬 James · 4 messages")
        self.assertIn("James: loads", card.call_args.args[2])
        self.assertFalse(any("loads" in l for _, l in post.call_args.args[2]) if post.called else False)
        card, _ = triage([(f"m{i}", "later") for i in range(4)], [nid], state)  # 8 in all, 6 listed
        self.assertEqual(card.call_args.args[1], "💬 James · 8 messages")
        self.assertEqual(card.call_args.args[2].count("James: "), 6)
        card, _ = triage([("what do u mean", "reply")], [], state)              # swiped: a fresh card
        self.assertEqual(card.call_args.args[1], "💬 James")
        label, cmd = card.call_args.args[3][0]                                  # straight into his chat, by number
        self.assertEqual(label, "💬 Open chat")
        self.assertIn("https://wa.me/972500000000", cmd)

    def test_no_judge_text_without_judged_items(self):
        secretary.collect(self.ev("Mum", "call me"), NOISE, {})
        seen = {}
        with mock.patch.object(secretary.claude, "ask", side_effect=lambda p, *a, **k: seen.update(p=p) or
                               secretary.claude.Answer("", [], "")):
            secretary.triage([], judge={"com.teamblind.blind": "taste"})
        self.assertNotIn("judge", seen["p"])

    def test_card_puts_urgent_first(self):
        recs = [{"app": "Gmail", "kind": "later", "summary": "News", "suggestion": "read later"},
                {"app": "WhatsApp", "kind": "act_now", "summary": "School", "suggestion": "pick up at 12"}]
        self.assertTrue(secretary.card_lines(recs)[0].startswith("❗ WhatsApp"))


class EnsembleTests(unittest.TestCase):
    def test_status_from_waiting_check(self):
        report = """Ensemble tasks that have stopped or have a card open (waiting-check, read-only):
PAUSED   bugs9 (vs-code-ai-helper/.ensemble/2026-11-12_task_1), impl-high-review: paused 4 h 44 min ago
         4 card(s) open (the runner's list, exact):
         22:23 UTC: High-Level Code Review: something
Tell the owner what each is waiting for."""
        self.assertEqual(ensemble.summarise(report),
                         "bugs9, impl-high-review: paused 4 h 44 min ago, 4 cards waiting")
        self.assertEqual(ensemble.summarise("Ensemble tasks ...:\n"), "nothing waiting for you")


class EnsembleStateTests(unittest.TestCase):
    def probe(self, running=(), dead=(), waiting="Ensemble tasks ...:\n"):
        out = json.dumps({"running": list(running), "dead": list(dead), "waiting": waiting})
        return mock.patch.object(ensemble.subprocess, "run", return_value=mock.Mock(stdout=out))

    def test_three_states(self):
        run = {"name": "bugs9", "stage": "impl-high-review", "since": "2026-11-13T06:19:09Z", "workspace": "w"}
        with self.probe(running=[run]):
            self.assertEqual(ensemble.state()["kind"], "running")
        with self.probe():
            self.assertEqual(ensemble.state()["kind"], "stopped")
        paused = "x:\nPAUSED   bugs9 (w/.ensemble/t), impl-high-review: paused 5 h ago\n         4 card(s) open (the runner's list, exact):"
        with self.probe(waiting=paused):
            st = ensemble.state()
        self.assertEqual(st["kind"], "needs")
        self.assertEqual(st["title"], "🔴 Ensemble needs you")
        self.assertIn("4 cards waiting", st["detail"])
        with self.probe(dead=[run]):
            self.assertIn("run died", ensemble.state()["detail"])

    def test_idle_or_deferred_is_not_needs(self):
        idle = "x:\nPAUSED   bugs9 (w/.ensemble/t), impl-high-review: paused 53 min ago\nTell the owner"
        with self.probe(waiting=idle):
            self.assertEqual(ensemble.state()["kind"], "stopped")       # paused, nothing to answer: quiet
        deferred = ("x:\nPAUSED   --- DEFERRED --- bugs9 (w/.ensemble/t), impl-high-review: paused 5 h ago\n"
                    "         4 card(s) open (the runner's list, exact):")
        with self.probe(waiting=deferred):
            self.assertEqual(ensemble.state()["kind"], "stopped")       # the owner parked it
        self.assertEqual(ensemble.summarise(deferred), "nothing waiting for you")
        self.assertEqual(ensemble.summarise(idle), "bugs9, impl-high-review: paused 53 min ago (idle, nothing to answer)")
        with self.probe(dead=[{"name": "--- DEFERRED --- bugs9", "stage": "s", "since": "", "workspace": "w"}]):
            self.assertEqual(ensemble.state()["kind"], "stopped")
        q = "x:\nSTOPPED  bugs10 (w/.ensemble/t), plan: stopped 1 h ago\n         1 unanswered question card(s) in Chat With AI"
        with self.probe(waiting=q):
            self.assertEqual(ensemble.state()["kind"], "needs")

    def test_card_while_running_is_not_needs(self):
        run = {"name": "bugs9", "stage": "impl", "since": "", "workspace": "w"}
        card = "x:\nCARD     bugs9: still running; 2 card(s) open (the runner's list, exact):"
        with self.probe(running=[run], waiting=card):
            st = ensemble.state()
        self.assertEqual(st["kind"], "running")
        self.assertIn("2 cards open", st["detail"])


class ConfigTests(unittest.TestCase):
    def test_every_and_noise_table_from_rules_toml(self):
        d = Path(tempfile.mkdtemp())
        (d / "rules.toml").write_text('''
[[rule]]
name = "tri"
source = "clock"
every = 15
[[rule.actions]]
type = "log"
noise = "@noise"

[noise]
packages = ["x"]
''')
        _, [rule] = load(d / "rules.toml", d / "private.toml")
        self.assertEqual(rule.actions[0]["noise"], {"packages": ["x"]})
        ev = lambda t: {"source": "clock", "id": "c", "time": t, "weekday": "fri"}
        self.assertTrue(rule.matches(ev("07:15")))
        self.assertTrue(rule.matches(ev("00:00")))
        self.assertFalse(rule.matches(ev("07:16")))


if __name__ == "__main__":
    unittest.main()


class NoisePatternTests(unittest.TestCase):
    def test_patterns_drop_status_shapes_but_not_real_messages(self):
        import tomllib
        from automations.digest import is_noise
        noise = tomllib.loads(paths.RULES.read_text(encoding="utf-8"))["noise"]
        n = lambda pkg, t, c: {"packageName": pkg, "title": t, "content": c}
        for drop in [n("com.whatsapp", "WhatsApp", "6 messages from 4 chats"),
                     n("com.red.alert", "RedAlert", "Connecting..."),
                     n("com.truecaller", "Call from Dana", "Identified by Truecaller"),
                     n("io.heckel.ntfy", "Waiting for network", "Notifications will resume"),
                     n("com.samsung.android.incallui", "Dana", "Incoming call")]:
            self.assertTrue(is_noise(drop, noise), drop)
        for keep in [n("com.red.alert", "RedAlert", "צבע אדום: תל אביב"),
                     n("com.whatsapp", "Mum", "6 messages from 4 chats, did you see?"),
                     n("com.truecaller", "Spam call", "Likely spam: 03-1234567")]:
            self.assertFalse(is_noise(keep, noise), keep)


class ClearNoiseTests(unittest.TestCase):
    def test_only_the_exact_noise_is_cleared(self):
        import tomllib
        from automations import actions
        clear = tomllib.loads(paths.RULES.read_text(encoding="utf-8"))["clear"]
        ev = lambda pkg, t, b: {"key": "k", "package": pkg, "title": t, "body": b}
        with mock.patch.object(actions.android, "snooze", return_value=True) as snooze:
            for e in [ev("com.android.phone", "New voicemail", "Ultra"),
                      ev("com.red.alert", "RedAlert", "Connecting..."),
                      ev("android", "Wispr Flow is displaying over other apps", "If you don't want")]:
                actions.clear_noise(e, clear=clear)
            self.assertEqual(snooze.call_count, 3)
            snooze.reset_mock()
            actions.clear_noise(ev("com.alert.meserhadash", "", "Operating in the background - waiting to receive alerts"), clear=clear)
            actions.clear_noise(ev("io.heckel.ntfy", "Listening for incoming notifications", "Subscribed"), clear=clear)
            self.assertEqual(snooze.call_count, 2)
            snooze.reset_mock()
            for e in [ev("com.red.alert", "RedAlert", "צבע אדום: תל אביב"),
                      ev("com.alert.meserhadash", "התרעה", "ירי רקטות וטילים - תל אביב"),
                      ev("com.whatsapp", "Mum", "New voicemail from the clinic?"),
                      ev("com.android.phone", "Missed call", "Dana")]:
                actions.clear_noise(e, clear=clear)
            snooze.assert_not_called()


class OrphanSummaryTests(unittest.TestCase):
    def test_summary_cleared_only_when_no_chat_is_left(self):
        from automations import actions
        summary = {"packageName": "com.whatsapp", "key": "0|com.whatsapp|1|null|10563"}
        chat = {"packageName": "com.whatsapp", "key": "0|com.whatsapp|1|xOCM=|10563"}
        summary["title"] = "WhatsApp"
        with mock.patch.object(actions.android, "notifications", return_value=[summary, chat]), \
             mock.patch.object(actions.android, "dismiss") as dismiss:
            actions._clear_orphan_summary("com.whatsapp")
        dismiss.assert_not_called()                                  # a real chat is still showing
        with mock.patch.object(actions.android, "notifications", return_value=[summary]), \
             mock.patch.object(actions.android, "dismiss") as dismiss:
            actions._clear_orphan_summary("com.whatsapp")
        dismiss.assert_called_once_with("com.whatsapp", "WhatsApp", summary["key"])


class NeverSnoozeChatsTests(unittest.TestCase):
    def test_messaging_apps_are_never_snoozed(self):
        from automations import android
        with mock.patch.object(android, "adb_shell", return_value=True) as sh:
            self.assertFalse(android.snooze("0|com.whatsapp|1|null|10563"))        # the summary hides the chats too
            self.assertFalse(android.snooze("0|com.whatsapp|1|NSU9=|10563"))
            self.assertFalse(android.snooze("0|org.telegram.messenger|-15|null|10487"))
            sh.assert_not_called()
            self.assertTrue(android.snooze("0|com.teamblind.blind|0|FCM:1|10704"))

    def test_whatsapp_summary_is_left_alone(self):
        from automations import actions
        summary = {"key": "0|com.whatsapp|1|null|10563", "package": "com.whatsapp", "title": "חדשות העיר 24"}
        with mock.patch.object(actions.android, "mark_read") as mr, \
             mock.patch.object(actions.android, "dismiss") as dm, \
             mock.patch.object(actions.android, "notifications", return_value=[{"key": summary["key"]}]):
            actions._clear(summary)
        mr.assert_not_called(); dm.assert_not_called()

    def test_summary_max_sender_never_gets_a_card(self):
        """A summary_max sender (KIDZONE) never gets a card: at most a line."""
        from automations import actions
        recs = [{"kind": "schedule", "package": "com.samsung.android.messaging", "title": "\u2068KIDZONE\u2069"},
                {"kind": "dismiss", "package": "com.truecaller", "title": "KIDZONE"},
                {"kind": "reply", "package": "com.samsung.android.messaging", "title": "Dana"}]
        actions._cap_to_summary(recs, ["KIDZONE"])
        self.assertEqual([r["kind"] for r in recs], ["later", "ignore", "reply"])

    def test_telegram_summary_is_left_alone(self):
        """Telegram's summary (id 1) carries a chat's title; swiping it took the
        chat's own notification along, unread (2026-11-15)."""
        from automations import actions
        summary = {"key": "0|org.telegram.messenger|1|null|10487", "package": "org.telegram.messenger",
                   "title": "World News in English"}
        with mock.patch.object(actions.android, "mark_read") as mr, \
             mock.patch.object(actions.android, "dismiss") as dm, \
             mock.patch.object(actions.android, "notifications", return_value=[{"key": summary["key"]}]):
            actions._clear(summary)
        mr.assert_not_called(); dm.assert_not_called()
        self.assertTrue(actions.is_chat_notification("org.telegram.messenger", "0|org.telegram.messenger|-1000000001|null|10487"))

    def test_not_logged_as_read_when_it_was_already_gone(self):
        from automations import actions
        chat = {"key": "0|com.whatsapp|1|NSU9=|10563", "package": "com.whatsapp", "title": "חדשות העיר 24"}
        with mock.patch.object(actions.android, "mark_read") as mr, \
             mock.patch.object(actions.android, "notifications", return_value=[]), \
             self.assertNoLogs(actions.log, "INFO"):
            actions._clear(chat)
        mr.assert_not_called()


class SuggestionTests(unittest.TestCase):
    def test_notes_to_itself_are_not_shown(self):
        from automations import secretary
        for note in ("Summary line only: Ofek Youth sign-up closes today 19/11; owner decides",
                     "Remind the owner at 12:30 so they can arrive by 13:00",
                     "Confirm with the clinic whether 15:00 works; then offer to add it to the calendar",
                     "Same card as the 15:00 question; answer the clinic once",
                     "Truecaller spam alert (kept). Summary line only, no card.",
                     "Unverified rumor; check if it concerns the owner's company"):
            self.assertEqual(secretary.for_owner(note), "", note)
        for ok in ("Reply to Yossi: will you submit through the system?",
                   "Check the 3,499 ₪ charge; if it isn't yours, call CardCo",
                   "Answer Dana whether to buy hummus"):
            self.assertEqual(secretary.for_owner(ok), ok)


class SummaryMaxTests(unittest.TestCase):
    def test_capped_sender_never_gets_a_card_and_empty_posts_no_line(self):
        from automations import actions
        recs = [{"title": "גינת הפרחים 🍃", "package": "com.whatsapp", "kind": k, "text": t}
                for k, t in (("act_now", "חנוכה בגינה, 6-10.12"), ("later", "2 new messages"),
                             ("later", "📷 Sent a photo"), ("later", "Sent a photo"))]
        recs.append({"title": "Dana", "package": "com.whatsapp", "kind": "reply", "text": "2 new messages"})
        actions._cap_to_summary(recs, ["KIDZONE", "גינת הפרחים 🍃"])
        self.assertEqual([r["kind"] for r in recs], ["later", "ignore", "ignore", "ignore", "reply"])


class CappedChatReadTests(unittest.TestCase):
    def test_capped_channel_is_marked_read_not_swiped(self):
        from automations import actions
        recs = [{"title": "גינת הפרחים 🍃", "package": "com.whatsapp", "kind": "later", "text": "חנוכה בגינה",
                 "key": "0|com.whatsapp|59|abc|1", "summary": "s", "suggestion": "", "app": "WhatsApp", "at": "2026-11-20 08:16"},
                {"title": "Shop", "package": "com.example", "kind": "later", "text": "sale",
                 "key": "0|com.example|1|null|1", "summary": "s", "suggestion": "", "app": "Shop", "at": "2026-11-20 08:16"}]
        with mock.patch.object(actions.secretary, "triage", return_value=recs), \
             mock.patch.object(actions, "_clear") as clear, \
             mock.patch.object(actions.android, "dismiss") as dismiss, \
             mock.patch.object(actions.android, "notifications", return_value=[]), \
             mock.patch.object(actions.summaries, "post"):
            actions.secretary_triage({}, secretary_cfg={"summary_max": ["גינת הפרחים 🍃"]}, _state={})
        self.assertEqual(clear.call_args.args[0]["key"], "0|com.whatsapp|59|abc|1")
        self.assertEqual([c.args[0] for c in dismiss.call_args_list], ["com.example"])
