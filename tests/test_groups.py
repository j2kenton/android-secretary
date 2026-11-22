"""WhatsApp groups: titles, assignment, collecting, digest decisions."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from automations import actions, groups
from automations.claude import Answer


class GenericPromptTests(unittest.TestCase):
    """Without the owner's settings the prompts are generic; the owner's own lines come from private.toml."""

    def fill(self, setting):
        with mock.patch.object(groups.owner, "setting", side_effect=setting):
            rules, _, _ = groups.offer_context()
            parts = groups.PRAYER_TIMES if groups.owner.setting("groups", "prayer_times", False) else groups.NO_PRAYER_TIMES
            return groups.DIGEST.format(now="N", offer_rules=rules, about=groups.owner.about(), profiles="P", surfaced="S",
                                        messages="M", **{k: v.format(shabbat="X", times="T") for k, v in parts.items()})

    def test_generic_by_default(self):
        p = self.fill(lambda t, k, d=None: d)
        for word in ("shul", "Shabbat", "Israel", "minyan", "Women-only"):
            self.assertNotIn(word, p)
        self.assertIn('doesn\'t say;', p)

    def test_the_owners_settings_add_their_lines(self):
        mine = {("owner", "about"): "Lives in Ruritania.", ("groups", "prayer_times"): True,
                ("groups", "never_fits"): "Opera nights are \"\" too"}
        p = self.fill(lambda t, k, d=None: mine.get((t, k), d))
        self.assertIn("About the owner: Lives in Ruritania.", p)
        self.assertIn("Prayer times posted for this week (Shabbat X)", p)
        self.assertIn("doesn't say. Opera nights are \"\" too;", p)


class GroupTests(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.patches = [mock.patch.object(groups, n, d / f) for n, f in (
            ("DIR", ""), ("ASSIGN", "a.json"), ("MESSAGES", "m.jsonl"), ("LOG", "l.jsonl"),
            ("QUESTIONS", "q.jsonl"), ("SURFACED", "s.jsonl"), ("TIMES", "t.json"), ("ROUNDUP", "r.json"),
            ("OFFERS", "o.json"))]
        self.patches[0] = mock.patch.object(groups, "DIR", d)
        self.patches.append(mock.patch.object(actions, "_learn_jid"))     # no adb in tests
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_split_title(self):
        self.assertEqual(groups.split_title("‎Rakevet updates: Admin"), ("Rakevet updates", "Admin"))
        self.assertEqual(groups.split_title("Dana"), ("", ""))

    def test_message_count_is_not_part_of_the_group(self):
        self.assertEqual(groups.parse("Shul (3 messages): Dan", "hi")[:2], ("Shul", "Dan"))
        self.assertEqual(groups.parse("Shul (12 messages)", "Dan: hi", lambda t: t == "Shul"),
                         ("Shul", "Dan", "hi"))

    def test_parse_both_whatsapp_forms(self):
        self.assertEqual(groups.parse("Lev: Josh", "need 2 for minyan"), ("Lev", "Josh", "need 2 for minyan"))
        self.assertEqual(groups.parse("Lev", "Josh: need 2", lambda t: t == "Lev"), ("Lev", "Josh", "need 2"))
        self.assertEqual(groups.parse("Dana", "see you: 8pm", lambda t: False), ("", "", "see you: 8pm"))

    def test_update_replaces_the_earlier_card(self):
        groups.assign("Lev", "shul")
        groups.collect("Lev", "Josh", "need 2 for minyan 8:30")
        first = '{"i": 0, "do": "card", "line": "Lev 8:30 minyan short 2 (asked 08:30)"}'
        with mock.patch.object(groups.claude, "ask", return_value=Answer(first, [], "")):
            [card] = groups.digest({"shul": ".."})
        groups.collect("Lev", "Dan", "we have a minyan now")
        upd = '{"i": 0, "do": "update", "ref": "s0", "line": "Lev 8:30 minyan: they have a minyan now"}'
        with mock.patch.object(groups.claude, "ask", return_value=Answer(upd, [], "")) as ask:
            [u] = groups.digest({"shul": ".."})
        self.assertIn("s0: [Lev,", ask.call_args[0][0])
        self.assertEqual((u["do"], u["nid"], u["was"]), ("update", card["nid"], card["line"]))

    def test_family_update_to_a_summary_line_is_a_card(self):
        groups.assign("Ari & Parents", "family")
        groups.collect("Ari & Parents", "Dana", "600 for these?")
        with mock.patch.object(groups.claude, "ask", return_value=Answer('{"i": 0, "do": "summary", "line": "Partner: 600 for the repair?"}', [], "")):
            groups.digest({"family": ".."})
        groups.collect("Ari & Parents", "Dana", "??")
        upd = '{"i": 0, "do": "update", "ref": "s0", "line": "Still waiting on you: 600 or 300?"}'
        with mock.patch.object(groups.claude, "ask", return_value=Answer(upd, [], "")):
            [u] = groups.digest({"family": ".."})
        self.assertEqual(u["do"], "card")
        self.assertNotIn("replaces", u)

    def test_a_mention_of_the_owner_is_always_a_card(self):
        groups.assign("Ari & Parents", "family")
        groups.collect("Ari & Parents", "📣 Mentioned you", "Dana: @Owner ??")
        ans = '{"i": 0, "do": "skip", "line": ""}'
        with mock.patch.object(groups.claude, "ask", return_value=Answer(ans, [], "")):
            [r] = groups.digest({"family": ".."})
        self.assertEqual((r["do"], r["title"], r["line"]), ("card", "Dana mentioned you", "Dana: @Owner ??"))

    def test_update_to_a_summary_line_is_a_summary_line_and_replaces_it(self):
        groups.assign("News", "community")                 # news channels never use the summary (see below)
        groups.collect("News", "", "bus fare 8.4 expected", app="telegram")
        first = '{"i": 0, "do": "summary", "line": "Bus fare 8.4 NIS expected"}'
        with mock.patch.object(groups.claude, "ask", return_value=Answer(first, [], "")):
            [old] = groups.digest({"news": ".."})
        groups.collect("News", "", "bus fare 9 from January", app="telegram")
        upd = '{"i": 0, "do": "update", "ref": "s0", "line": "Bus fare 9 NIS from January, not 8.4"}'
        with mock.patch.object(groups.claude, "ask", return_value=Answer(upd, [], "")):
            [u] = groups.digest({"news": ".."})
        self.assertEqual(u["do"], "summary")
        self.assertNotEqual(u["nid"], old["nid"])
        self.assertEqual([s["line"] for s in groups.summary()], ["Bus fare 9 NIS from January, not 8.4"])

    def test_daily_news_waits_for_the_roundup(self):
        groups.assign("News", "news")
        groups.assign("Building", "building")
        groups.collect("News", "", "analysis: what the coalition talks mean", app="telegram")
        groups.collect("Building", "Avi", "water off now in the building after a burst pipe")
        ans = "\n".join(json.dumps(d) for d in [
            {"i": 0, "do": "daily", "line": "Analysis of the coalition talks"},
            {"i": 1, "do": "summary", "line": "Water off in the building (burst pipe)"}])
        with mock.patch.object(groups.claude, "ask", return_value=Answer(ans, [], "")):
            out = groups.digest({"news": ".."})
        self.assertEqual([r["do"] for r in out], ["daily", "summary"])
        self.assertEqual([s["line"] for s in groups.summary()], ["Water off in the building (burst pipe)"])
        with mock.patch.object(actions.summaries, "post") as post:
            actions.news_roundup()                                     # morning or 18:00
        self.assertEqual(len(post.call_args[0][2]), 2)
        self.assertEqual(sorted(s["line"] for s in groups.summary()),
                         ["Analysis of the coalition talks", "Water off in the building (burst pipe)"])
        groups.collect("News", "", "the talks: a later analysis", app="telegram")
        upd = '{"i": 0, "do": "update", "ref": "s0", "line": "Coalition talks: new analysis"}'
        with mock.patch.object(groups.claude, "ask", return_value=Answer(upd, [], "")):
            [u] = groups.digest({"news": ".."})
        self.assertEqual(u["do"], "daily")                              # an update to a held line waits too

    def test_news_channel_summary_waits_for_the_roundup(self):
        groups.assign("News", "news")
        groups.assign("Shul A", "shul")
        groups.collect("News", "", "video of the leaders' meeting", app="telegram")
        groups.collect("Shul A", "Gabbai", "new mikveh hours from next week")
        ans = "\n".join(json.dumps(d) for d in [{"i": 0, "do": "summary", "line": "Video of the meeting"},
                                                 {"i": 1, "do": "summary", "line": "New mikveh hours"}])
        with mock.patch.object(groups.claude, "ask", return_value=Answer(ans, [], "")):
            out = groups.digest({"news": "..", "shul": ".."})
        self.assertEqual([r["do"] for r in out], ["daily", "summary"])

    def test_telegram_group_card_opens_telegram_not_whatsapp(self):
        r = {"group": "News", "telegram": True}
        with mock.patch.object(actions.android, "jid_for", return_value=None):
            label, cmd = actions._open_group_button(r)
        self.assertIn("Telegram", label)
        self.assertIn(actions.TELEGRAM.split(".")[-1], cmd)
        self.assertNotIn("whatsapp", cmd)

    def test_question_card_gets_reply_buttons_and_forward_profiles_forward(self):
        groups.assign("Bus 11", "shuttle")
        groups.assign("Building", "building")
        groups.collect("Bus 11", "Hana", "ההסעה הגיעה?")
        groups.collect("Building", "Dov", "של מי האופניים הקשורים בחוץ?")
        ans = "\n".join(json.dumps(d) for d in [
            {"i": 0, "do": "card", "title": "Did the bus come?", "line": "Hana asks if the bus came",
             "replies": ["כן, הגיעה", "עוד לא", "third"]},
            {"i": 1, "do": "card", "title": "Whose bikes?", "line": "Bikes chained outside", "replies": "bad"}])
        with mock.patch.object(groups.claude, "ask", return_value=Answer(ans, [], "")):
            out = groups.digest({"shuttle": "..", "building": ".."})
        self.assertEqual(out[0]["replies"], ["כן, הגיעה", "עוד לא"])
        self.assertEqual(out[1]["replies"], [])
        with mock.patch.object(groups.claude, "ask", return_value=Answer(ans, [], "")), \
             mock.patch.object(actions.grp, "digest", return_value=out), \
             mock.patch.object(actions.android, "jid_for", return_value="1@g.us"), \
             mock.patch.object(actions, "_number", return_value="972500000000"), \
             mock.patch.object(actions, "_own_card") as card:
            actions.group_digest({}, groups={"profiles": {}, "forward": {"building": "Dana"}})
        bus, bikes = (c.args[3] for c in card.call_args_list)
        self.assertEqual([b[0] for b in bus], ["📋 Copy “כן, הגיעה”", "📋 Copy “עוד לא”"])
        self.assertIn("1@g.us", bus[0][1])
        self.assertEqual([b[0] for b in bikes], ["↪ Forward to Dana"])

    def test_news_card_offers_forward_and_keeps_open(self):
        groups.assign("CBS", "news")
        out = [{"do": "card", "nid": "n1", "group": "CBS", "sender": "", "title": "Iran strike possible",
                "line": "Israel and US weighing a new attack", "text": "דיווח", "url": "", "telegram": True,
                "replies": []}]
        with mock.patch.object(actions.grp, "digest", return_value=out), \
             mock.patch.object(actions.android, "jid_for", return_value=None), \
             mock.patch.object(actions, "_number", return_value="972500000000"), \
             mock.patch.object(actions, "_own_card") as card:
            actions.group_digest({}, groups={"profiles": {}, "forward": {"news": "Dana"}})
        buttons = card.call_args.args[3]
        self.assertEqual([b[0] for b in buttons], ["↪ Forward to Dana", "💬 Open in Telegram"])
        self.assertIn("wa.me/972500000000", buttons[0][1])

    def test_sweep_clears_leftovers_from_handled_groups_only(self):
        groups.assign("Bus 11", "shuttle")
        old = "2026-01-01 08:00:00"
        live = [{"key": "a", "packageName": "com.whatsapp", "title": "Bus 11 (2 messages)", "content": "כן", "when": old},
                {"key": "b", "packageName": "com.whatsapp", "title": "Daily Quote", "content": "Joke", "when": old},
                {"key": "c", "packageName": "com.whatsapp", "title": "Bus 11: Hana", "content": "new",
                 "when": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")}]
        state = {}
        with mock.patch.object(actions.android, "notifications", return_value=live), \
             mock.patch.object(actions, "_clear") as clear:
            actions.sweep_handled({}, school={}, _state=state)
            actions.sweep_handled({}, school={}, _state=state)               # tried once only
        self.assertEqual([c.args[0]["key"] for c in clear.call_args_list], ["a"])
        self.assertEqual(groups._read(groups.MESSAGES)[-1]["text"], "כן")

    def test_active_groups_are_never_collected_or_watched(self):
        groups.assign("Family", "active")
        self.assertFalse(groups.collect("Family", "Mum", "hi"))
        self.assertFalse(groups.watched("Family"))
        groups.assign("Trains", "trains")
        self.assertTrue(groups.collect("Trains", "Bot", "Line closed"))
        self.assertFalse(groups.collect("Trains", "Bot", "Line closed"))      # repeat
        self.assertTrue(groups.watched("Trains"))

    def test_digest_cards_summaries_asks_and_skips(self):
        groups.assign("Shul A", "shul")
        groups.assign("News", "community")
        groups.collect("Shul A", "Gabbai", "ברוך דיין האמת, Mr Cohen passed away; shiva at 5 Hapisga St")
        groups.collect("Shul A", "Avi", "המקום ינחם")
        groups.collect("News", "Bot", "Rail strike tomorrow https://ynet.co.il/x")
        ans = "\n".join(json.dumps(d) for d in [
            {"i": 0, "do": "card", "line": "Mr Cohen passed away; shiva at 5 Hapisga St"},
            {"i": 1, "do": "skip"},
            {"i": 2, "do": "summary", "line": "Rail strike tomorrow", "url": "https://evil.example"}])
        with mock.patch.object(groups.claude, "ask", return_value=Answer(ans, [], "")):
            out = groups.digest({"shul": "..", "news": ".."})
        self.assertEqual([r["do"] for r in out], ["card", "summary"])
        self.assertEqual(out[1]["url"], "")                                   # link not in message: dropped
        self.assertTrue(all(m["done"] for m in groups._read(groups.MESSAGES)))
        self.assertEqual(len(groups.summary()), 1)

    def test_offer_rule(self):
        today = groups.date(2026, 11, 19)
        fits = {"fits": "Noa (grade 1)", "deadline": "2026-11-24", "must": False}
        self.assertEqual(groups.offer_do(fits, None, today), "summary")                  # first time, fits
        self.assertEqual(groups.offer_do({**fits, "deadline": "2026-11-20"}, None, today), "card")   # closes tomorrow
        self.assertEqual(groups.offer_do({**fits, "deadline": "2026-11-18"}, None, today), "skip")   # closed
        self.assertEqual(groups.offer_do({**fits, "fits": ""}, None, today), "skip")     # nobody in the family
        self.assertEqual(groups.offer_do({**fits, "deadline": "2026-11-19"}, {"must": False}, today), "skip")  # heard before
        must = {"fits": "Ben (kindergarten)", "deadline": "", "must": True}
        self.assertEqual(groups.offer_do(must, None, today), "card")
        self.assertEqual(groups.offer_do({**must, "deadline": "2026-11-20"}, {"must": True}, today), "card")  # last call
        self.assertEqual(groups.offer_do({**must, "deadline": "2026-11-20"}, {"must": True, "last_call": "x"}, today), "skip")
        self.assertEqual(groups.offer_do({**must, "deadline": "2026-11-26"}, {"must": True}, today), "skip")

    def test_offers_first_time_only_and_never_asked(self):
        groups.assign("News", "news")
        groups.collect("News", "", "קייטנת אופק בחנוכה - ההרשמה פתוחה לגנים וכיתות א'-ג'", app="telegram")
        offer = {"name": "Ofek Youth Hanukkah", "same_as": "", "deadline": "2099-12-01",
                 "fits": "Noa (grade 1), Ben (kindergarten)", "must": False}
        first = json.dumps({"i": 0, "do": "ask", "title": "Ofek Youth Hanukkah", "line": "Ofek Youth Hanukkah sign-up",
                            "question": "Kids' activities?", "offer": offer})
        with mock.patch.object(groups.claude, "ask", return_value=Answer(first, [], "")) as ask:
            [r] = groups.digest({"news": ".."}, kids="Noa: grade 1", texted=["2026-11-16 KIDZONE: other thing"])
        self.assertIn("Noa: grade 1", ask.call_args[0][0])
        self.assertIn("t0: (texted to the owner) 2026-11-16 KIDZONE: other thing", ask.call_args[0][0])
        self.assertEqual(r["do"], "summary")                                  # not a question, not held for the round-up
        self.assertIn("for Noa (grade 1), Ben (kindergarten); closes", r["line"])
        self.assertEqual(groups.open_questions(), [])
        [oid] = groups._offers()
        groups.collect("News", "", "תזכורת: קייטנת אופק בחנוכה, נשארו מקומות אחרונים", app="telegram")
        again = json.dumps({"i": 0, "do": "card", "line": "Ofek Youth: last places", "offer": {**offer, "same_as": oid}})
        with mock.patch.object(groups.claude, "ask", return_value=Answer(again, [], "")) as ask:
            self.assertEqual(groups.digest({"news": ".."}), [])                # heard before: nothing
        self.assertIn(f"{oid}: Ofek Youth Hanukkah;", ask.call_args[0][0])
        self.assertEqual(groups._offers()[oid]["seen"], 2)

    def test_offer_already_texted_is_not_news(self):
        groups.assign("News", "news")
        groups.collect("News", "", "קייטנת אופק בחנוכה", app="telegram")
        offer = {"name": "Ofek Youth Hanukkah", "same_as": "t0", "deadline": "", "fits": "Ben", "must": False}
        ans = json.dumps({"i": 0, "do": "summary", "line": "Ofek Youth Hanukkah sign-up", "offer": offer})
        with mock.patch.object(groups.claude, "ask", return_value=Answer(ans, [], "")):
            self.assertEqual(groups.digest({"news": ".."}, texted=["2026-11-16 KIDZONE: Hanukkah Ofek Youth signup"]), [])

    def test_texted_offers_come_from_summary_max_senders(self):
        rows = [{"at": "2026-11-18 09:51", "title": "\u2068KIDZONE\u2069", "summary": "KIDZONE: Ofek Youth closes Thu"},
                {"at": "2026-11-18 09:51", "title": "KIDZONE", "summary": "KIDZONE: Ofek Youth closes Thu"},
                {"at": "2026-11-18 10:00", "title": "Dana", "summary": "Milk"}]
        with mock.patch.object(actions.secretary, "recent", return_value=rows):
            self.assertEqual(actions._texted_offers(["KIDZONE"]), ["2026-11-18 KIDZONE: Ofek Youth closes Thu"])
        self.assertEqual(actions._texted_offers(None), [])

    def test_a_person_is_left_to_the_secretary(self):
        groups.assign("Aunt Vera", "person")
        self.assertFalse(groups.watched("Aunt Vera"))
        self.assertFalse(groups.collect("Aunt Vera", "", "Привет, как дела"))
        ev = {"package": actions.TELEGRAM, "title": "Aunt Vera", "body": "Привет"}
        with mock.patch.object(actions, "_clear") as clear, mock.patch.object(groups, "classify") as classify:
            actions.group_collect(ev, {}, {})
        clear.assert_not_called()
        classify.assert_not_called()
        self.assertIn("person", groups.CLASSIFY + "".join(groups.BUILTIN))

    def test_question_carries_its_message_to_the_chat(self):
        groups.ask("Shul", "Card for prayer requests?", about="Ari: נשמח שתתפללו לרפואתו")
        text = groups.questions_prompt(groups.open_questions())
        self.assertIn("[Shul] Card for prayer requests?", text)
        self.assertIn("נשמח שתתפללו לרפואתו", text)
        with mock.patch.object(actions.android, "notify") as notify:
            actions._questions_card()
        label, cmd = notify.call_args.kwargs["buttons"][0]
        self.assertEqual(label, "📋 Copy question, open Claude")
        self.assertIn("termux-clipboard-set", cmd)
        self.assertIn("נשמח שתתפללו לרפואתו", cmd)

    def test_shabbat_of(self):
        self.assertEqual(groups.shabbat_of("2026-11-13 08:59"), "2026-11-14")    # Friday: tomorrow
        self.assertEqual(groups.shabbat_of("2026-11-14 09:00"), "2026-11-14")    # Shabbat morning: today
        self.assertEqual(groups.shabbat_of("2026-11-14 18:00"), "2026-11-21")    # after Shabbat: next week
        self.assertEqual(groups.shabbat_of("2026-11-15 10:00"), "2026-11-21")

    def test_posted_times_are_saved_and_offered(self):
        on = mock.patch.object(groups.owner, "setting",           # private.toml [groups] prayer_times = true
                               side_effect=lambda t, k, d=None: True if (t, k) == ("groups", "prayer_times") else d)
        on.start()
        self.addCleanup(on.stop)
        groups.assign("Times", "minyan")
        groups.collect("Times", "Yossi", "זמני תפילות פרשת ויצא בבית הכנסת הגדול\n13:30-מנחה א'")
        ans = json.dumps({"i": 0, "do": "skip", "times": {"shul": "בית הכנסת הגדול", "parsha": "ויצא", "text": "13:30 מנחה א'"}})
        with mock.patch.object(groups.claude, "ask", return_value=Answer(ans, [], "")):
            self.assertEqual(groups.digest({"minyan": ".."}), [])
        self.assertEqual(list(groups.current_times()), ["בית הכנסת הגדול"])
        groups.collect("Times", "Moshe", "מתי מנחה בבית הכנסת הגדול?")
        ans = json.dumps({"i": 0, "do": "card", "title": "Mincha time asked", "line": "Moshe asks the Great Synagogue mincha",
                          "replies": ["בית הכנסת הגדול מנחה 13:30"], "uses_times": ["בית הכנסת הגדול"]})
        with mock.patch.object(groups.claude, "ask", return_value=Answer(ans, [], "")) as ask:
            out = groups.digest({"minyan": ".."})
        self.assertIn("13:30 מנחה א'", ask.call_args.args[0])                # the saved times reach Claude
        self.assertEqual(out[0]["replies"], ["בית הכנסת הגדול מנחה 13:30"])
        self.assertIn("בית הכנסת הגדול times posted", out[0]["times_from"])

    def test_last_weeks_times_are_never_offered(self):
        groups.assign("Times", "minyan")
        groups.TIMES.write_text(json.dumps({"בית הכנסת הגדול": {"week": "2026-11-14", "at": "2026-11-13 08:59",
                                                          "group": "Times", "parsha": "תולדות", "text": "13:30 מנחה"}}))
        groups.collect("Times", "Moshe", "מתי מנחה בבית הכנסת הגדול?")
        ans = json.dumps({"i": 0, "do": "card", "title": "Mincha asked", "line": "Moshe asks the Great Synagogue mincha",
                          "replies": ["בית הכנסת הגדול מנחה 13:30"], "uses_times": ["בית הכנסת הגדול"]})
        with mock.patch.object(groups, "datetime", wraps=groups.datetime) as dt, \
             mock.patch.object(groups.claude, "ask", return_value=Answer(ans, [], "")) as ask:
            dt.now.return_value = groups.datetime(2026, 11, 15, 10, 0)
            dt.strptime = groups.datetime.strptime
            out = groups.digest({"minyan": ".."})
        self.assertNotIn("13:30 מנחה", ask.call_args.args[0].split("New messages")[0])
        self.assertEqual(out[0]["replies"], [])                               # no stale answer to copy
        self.assertNotIn("times_from", out[0])

    def test_unsure_new_group_is_asked_not_watched(self):
        state = {}
        with mock.patch.object(groups.claude, "ask",
                               return_value=Answer('{"profile": "shul", "sure": false, "why": "maybe"}', [], "")), \
             mock.patch.object(actions.android, "notify"), mock.patch.object(actions.android, "snooze") as snooze:
            actions.group_collect({"title": "Beit Knesset?: Moshe", "body": "שבת שלום", "key": "k1"},
                                  groups={"profiles": {"shul": ".."}}, _state=state)
        self.assertIsNone(groups.profile_of("Beit Knesset?"))
        self.assertEqual(len(groups.open_questions()), 1)
        snooze.assert_not_called()                                            # never cleared while unsure

    def test_telegram_channel_is_a_group_by_its_title(self):
        with mock.patch.object(groups.claude, "ask",
                               return_value=Answer('{"profile": "news", "sure": true, "why": "news channel"}', [], "")), \
             mock.patch.object(actions.android, "mark_read", return_value=False), \
             mock.patch.object(actions.android, "dismiss", return_value=False), \
             mock.patch.object(actions.android, "snooze", return_value=True) as snooze:
            actions.group_collect({"package": "org.telegram.messenger", "title": "World News in English",
                                   "body": "📹 Trump: Iran", "key": "t1"}, groups={"profiles": {"news": ".."}}, _state={})
        self.assertEqual(groups.profile_of("World News in English"), "news")
        m = groups._read(groups.MESSAGES)[0]
        self.assertEqual((m["sender"], m["app"]), ("", "telegram"))
        snooze.assert_not_called()       # a chat is never snoozed: its later messages would vanish (2026-11-14)

    def test_sure_new_group_is_assigned_and_cleared(self):
        with mock.patch.object(groups.claude, "ask",
                               return_value=Answer('{"profile": "trains", "sure": true, "why": "rail"}', [], "")), \
             mock.patch.object(actions.android, "mark_read", return_value=False), \
             mock.patch.object(actions.android, "notifications", return_value=[{"key": "k2"}]), \
             mock.patch.object(actions.android, "snooze", return_value=True) as snooze:
            actions.group_collect({"title": "רכבת ישראל עדכונים: Bot", "body": "שינוי בלוח", "key": "k2"},
                                  groups={"profiles": {"trains": ".."}}, _state={})
        self.assertEqual(groups.profile_of("רכבת ישראל עדכונים"), "trains")
        snooze.assert_called_once_with("k2")


class CombinedProfileTests(unittest.TestCase):
    def test_combined_profile_description(self):
        d = groups.describe("shul,community", {"shul": "S", "community": "C"})
        self.assertEqual(d, "[shul] S AND [community] C")


class MarkReadTests(unittest.TestCase):
    def test_chat_filter_and_request(self):
        from automations import android
        self.assertEqual(android.chat_filter("\u200e\u202bשלום כיתה ב׳2🌷\u202c\u200e: \u200e\u202bרינה\u202c\u200e"), "שלום כיתה ב׳2🌷")
        self.assertEqual(android.chat_filter("מתכנתים משוחחים (ציבורי): Rina"), "מתכנתים משוחחים")
        self.assertEqual(android.chat_filter("\u2068+972500000001\u2069"), "972500000001")
        with mock.patch.object(android, "_run") as run:
            self.assertTrue(android.mark_read("com.whatsapp", "Shul group: Josh"))
        dump = """    NotificationRecord(0x1: pkg=com.whatsapp user=UserHandle{0} id=1 tag=abc= importance=3 key=0|com.whatsapp|1|abc=
|10563: Notification(channel=x shortcut=1@g.us)
            actions={
                [0] "Reply" -> PendingIntent{1}
                [1] "Mark as read" -> PendingIntent{2}
    NotificationRecord(0x2: pkg=com.whatsapp user=UserHandle{0} id=1 tag=null importance=2 key=0|com.whatsapp|1|null|10563: Notification(channel=y)
            actions={
    NotificationRecord(0x3: pkg=com.termux.api"""
        with mock.patch.object(android, "adb_output", return_value=dump):
            self.assertEqual(android.notification_actions("0|com.whatsapp|1|abc=\n|10563"), ["Reply", "Mark as read"])
            self.assertEqual(android.notification_actions("0|com.whatsapp|1|null|10563"), [])
            with mock.patch.object(android, "_run") as run2:
                self.assertFalse(android.mark_read("com.whatsapp", "Group: A", "0|com.whatsapp|1|null|10563"))
                run2.assert_not_called()                                   # summary copy: no button, no request
        self.assertIn("phoneautomations.MARK_READ", run.call_args[0][0])
        self.assertIn("Mark as read", run.call_args[0][0])
        self.assertFalse(android.mark_read("com.example", "X: y"))      # no known button

    def test_clear_falls_back_to_snooze_when_still_showing(self):
        """A non-chat notification falls back to a snooze; WhatsApp's summary and
        a chat's own notification never do (2026-11-14)."""
        k = "0|com.example|1|null|10563"
        ev = {"key": k, "package": "com.example", "title": "Group: A"}
        with mock.patch.object(actions.android, "mark_read", return_value=True), \
             mock.patch.object(actions.android, "notifications", return_value=[{"key": k}]), \
             mock.patch("time.sleep"), mock.patch.object(actions.android, "snooze", return_value=True) as snooze:
            actions._clear(ev)
            snooze.assert_any_call(k)
            snooze.reset_mock()
            for key in ("0|com.whatsapp|1|null|10563", "0|com.whatsapp|1|abc=\n|10563"):
                actions._clear({**ev, "package": "com.whatsapp", "key": key})
            snooze.assert_not_called()
        with mock.patch.object(actions.android, "mark_read", return_value=True), \
             mock.patch.object(actions.android, "notifications", return_value=[]), \
             mock.patch("time.sleep"), mock.patch.object(actions.android, "snooze") as snooze:
            actions._clear(ev)
        snooze.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class JidTests(unittest.TestCase):
    def test_chat_ids_learned_from_both_header_forms(self):
        from automations import android
        dump = """    NotificationRecord(0x1: pkg=com.whatsapp user=UserHandle{0} id=1 tag=null importance=2 key=0|com.whatsapp|1|null|10563: Notification(channel=s shortcut=999-1@g.us c
                android.title=String (WhatsApp)
    NotificationRecord(0x2: pkg=com.whatsapp user=UserHandle{0} id=1 tag=sHRo9V=
|10563: Notification(channel=group_chat shortcut=120-7@g.us contentView=null
                android.title=String (‎Cousins Club 🐒: Sam)
                android.conversationTitle=String (Cousins Club 🐒)
    NotificationRecord(0x3: pkg=com.whatsapp user=UserHandle{0} id=1 tag=abc= importance=3 key=0|com.whatsapp|1|abc= shortcut=447700900123@s.whatsapp.net x
                android.title=String (MUM)
"""
        import tempfile, os
        f = os.path.join(tempfile.mkdtemp(), "j.json")
        with mock.patch.object(android, "JIDS_FILE", f), mock.patch.object(android, "adb_output", return_value=dump):
            known = android.whatsapp_jids(refresh=True)
        self.assertEqual(known, {"Cousins Club 🐒": "120-7@g.us", "MUM": "447700900123@s.whatsapp.net"})


class MutedGroupTests(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.patches = [mock.patch.object(groups, n, d / f) for n, f in (
            ("ASSIGN", "a.json"), ("MESSAGES", "m.jsonl"), ("LOG", "l.jsonl"), ("QUESTIONS", "q.jsonl"))]
        self.patches += [mock.patch.object(groups, "DIR", d), mock.patch.object(actions, "_learn_jid")]
        for p in self.patches:
            p.start()
        groups.assign("Rav Q&A", "muted")

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_a_reply_is_a_card_and_the_notification_stays(self):
        ev = {"package": "com.whatsapp", "title": "Rav Q&A: ⤷ You got a reply", "body": "R B: it's fine to eat", "key": "k"}
        state = {}
        with mock.patch.object(actions.grp, "parse", return_value=("Rav Q&A", "⤷ You got a reply", "R B: it's fine to eat")), \
             mock.patch.object(actions, "_own_card") as card, mock.patch.object(actions, "_clear") as clear, \
             mock.patch.object(actions.android, "jid_for", return_value=None):
            actions.group_collect(ev, _state=state)
            actions.group_collect(ev, _state=state)            # a repost: no second card
        self.assertEqual(card.call_count, 1)
        self.assertIn("R B", card.call_args.args[1])
        self.assertIn("it's fine to eat", card.call_args.args[2])
        self.assertFalse(clear.called)
        self.assertEqual(groups._read(groups.MESSAGES), [])     # not saved for a digest

    def test_count_header_is_skipped(self):
        with mock.patch.object(actions.grp, "parse", return_value=("Rav Q&A", "", "2 new messages")), \
             mock.patch.object(actions, "_own_card") as card, mock.patch.object(actions, "_clear") as clear:
            actions.group_collect({"package": "com.whatsapp", "title": "Rav Q&A", "body": "2 new messages", "key": "k"}, _state={})
        self.assertFalse(card.called or clear.called)
