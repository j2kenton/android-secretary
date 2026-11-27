"""Job openings get a tier from [secretary.jobs]: focus -> a 🟢 card of its own,
maybe -> a line on 💼 Jobs, unlikely (clearly not) -> cleared, its email marked
read. The same job from LinkedIn's email and its app counts once."""

import unittest
from unittest import mock

from automations import actions, secretary

JOBS = {"about": "A frontend engineer.", "top": ["Meta"], "good": ["Snyk"], "avoid": ["Acme"]}


def rec(company, role, tier, app="LinkedIn", package="com.linkedin.android", why="", title="LinkedIn Job Alerts", **kw):
    return {"at": "2026-11-27 08:35", "app": app, "package": package, "title": title,
            "text": f"{company} {role}", "key": f"k-{company}-{role}-{app}", "summary": f"{company}: {role}",
            "kind": "later", "suggestion": "", "rule_idea": "", "reply": "", "event": None,
            "job": tier, "company": company, "role": role, "why": why, **kw}


class JobsTests(unittest.TestCase):
    marked = staticmethod(lambda alerts: list(range(len(alerts))))

    def triage(self, recs, state):
        with mock.patch.object(actions.secretary, "triage", return_value=recs), \
             mock.patch.object(actions.android, "notifications", return_value=[]), \
             mock.patch.object(actions.android, "dismiss") as dismiss, \
             mock.patch.object(actions.android, "notify") as notify, \
             mock.patch.object(actions.summaries, "post") as post, \
             mock.patch.object(actions.jobmail, "mark_read", side_effect=self.marked) as mark:
            actions.secretary_triage({}, _state=state)
        self.mark = mark
        posts = {c.args[0]: [l for _i, l in c.args[2]] for c in post.call_args_list}
        return posts, notify, dismiss

    def test_tiers(self):
        posts, notify, dismiss = self.triage([
            rec("Meta", "Software Engineer, Product", "focus", why="top payer, product role"),
            rec("Snowflake", "Senior Solution Engineer", "unlikely", why="sales role"),
            rec("Buildots", "Senior Frontend Engineer", "maybe")], {})
        self.assertEqual(notify.call_count, 1)
        self.assertEqual(notify.call_args.args[0], "🟢 Meta: Software Engineer, Product")
        self.assertIn("top payer", notify.call_args.args[1])
        self.assertEqual(posts["jobs"], ["Buildots · Senior Frontend Engineer"])   # unlikely: not shown
        self.assertNotIn("secretary", posts)              # never on 🗂 as well
        self.assertEqual(dismiss.call_count, 3)            # LinkedIn's own notifications go
        self.mark.assert_not_called()                      # no email to mark

    def test_unlikely_email_is_marked_read_then_cleared(self):
        state = {}
        _posts, _notify, dismiss = self.triage(
            [rec("Snowflake", "Senior Solution Engineer", "unlikely", app="Gmail", package=actions.GMAIL)], state)
        self.assertEqual(self.mark.call_args.args[0][0]["sender"], "LinkedIn Job Alerts")
        self.assertEqual(self.mark.call_args.args[0][0]["start"], "Snowflake Senior Solution Engineer")
        dismiss.assert_called_once()
        self.assertEqual(state["jobs_to_mark"], [])

    def test_unlikely_email_not_found_is_tried_twice_more_and_stays(self):
        state = {}
        with mock.patch.object(self, "marked", lambda alerts: []):
            alert = rec("Snowflake", "Senior Solution Engineer", "unlikely", app="Gmail", package=actions.GMAIL)
            _p, _n, dismiss = self.triage([alert], state)
            dismiss.assert_not_called()                      # never hidden while unread
            self.assertEqual(len(state["jobs_to_mark"]), 1)
            for _ in range(2):
                self.triage([rec("Other", "FE", "maybe")], state)
        self.assertEqual(state["jobs_to_mark"], [])

    def test_an_email_with_more_jobs_or_snoozed_is_never_marked_read(self):
        state = {}
        more = rec("Via", "Back-End Engineer", "unlikely", app="Gmail", package=actions.GMAIL)
        more["text"] = "Via Back-End Engineer and 7 other jobs you may be interested in"
        snoozed = rec("Acme", "QA", "unlikely", app="Gmail", package=actions.GMAIL, title="Snoozed: LinkedIn Job Alerts")
        self.triage([more, snoozed], state)
        self.mark.assert_not_called()

    def test_gmail_alert_is_left_as_it_is(self):
        _posts, _notify, dismiss = self.triage(
            [rec("Buildots", "Senior Frontend Engineer", "maybe", app="Gmail", package=actions.GMAIL)], {})
        dismiss.assert_not_called()

    def test_the_same_job_again_adds_nothing(self):
        state = {}
        self.triage([rec("Meta", "Software Engineer, Product", "focus", app="Gmail", package=actions.GMAIL)], state)
        _posts, notify, dismiss = self.triage([rec("Meta", "", "maybe")], state)   # "Meta: new opportunity"
        notify.assert_not_called()
        dismiss.assert_called_once()
        self.assertEqual(len(state["jobs"]), 1)

    def test_a_reply_from_a_recruiter_is_not_a_job_line(self):
        r = rec("Meta", "Software Engineer", "focus", kind="reply")
        posts, _notify, _dismiss = self.triage([r], {})
        self.assertNotIn("jobs", posts)

    def test_old_jobs_leave_the_card(self):
        state = {"jobs": [{"id": "job-1", "tier": "maybe", "company": "Old", "role": "FE", "why": "", "ts": 0}]}
        posts, _n, _d = self.triage([rec("Buildots", "Senior Frontend Engineer", "maybe")], state)
        self.assertEqual(posts["jobs"], ["Buildots · Senior Frontend Engineer"])


class JobMailTests(unittest.TestCase):
    def test_reads_which_were_marked(self):
        from automations import jobmail
        seen = {}
        ans = "1 done\n2 not found\n3. done\n9 done"
        got = jobmail.mark_read([{"sender": "LinkedIn Job Alerts", "start": f"Job {n}"} for n in range(3)],
                                ask=lambda p, *a, **k: seen.update(p=p) or jobmail.claude.Answer(ans, [], ""))
        self.assertEqual(got, [0, 2])
        self.assertIn("1. LinkedIn Job Alerts | Job 0", seen["p"])


class PromptTests(unittest.TestCase):
    def test_only_when_set_and_a_row_may_be_a_job(self):
        job = [{"title": "LinkedIn Job Alerts", "text": "Meta is hiring"}]
        other = [{"title": "Dana", "text": "see you at 5"}]
        self.assertIn("Top companies (the highest pay): Meta", secretary.jobs_prompt(JOBS, job))
        self.assertEqual(secretary.jobs_prompt(JOBS, other), "")
        self.assertEqual(secretary.jobs_prompt(None, job), "")

    def test_parse_keeps_the_tier(self):
        out = secretary.parse('{"i": 0, "kind": "later", "summary": "s", "job": "focus", "company": "Meta", '
                              '"role": "SWE, Product", "why": "top payer"}\n'
                              '{"i": 1, "kind": "later", "summary": "s", "job": "great", "company": "X"}', 2)
        self.assertEqual((out[0]["job"], out[0]["company"], out[0]["role"]), ("focus", "Meta", "SWE, Product"))
        self.assertNotIn("job", out[1])


if __name__ == "__main__":
    unittest.main()
