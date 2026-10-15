"""Hermes Inbox's decisions, without Hermes, GitHub or X Studio."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hermes_inbox as h  # noqa: E402

NOW = 1_800_000_000.0


class Ending(unittest.TestCase):
    def test_stuck_endings(self):
        self.assertEqual(h.ending("I need you to sign in to GitHub first."), "needs sign-in")
        self.assertEqual(h.ending("This command requires approval."), "waiting on your approval")
        self.assertEqual(h.ending("I hit the tool-call limit."), "hit a limit")

    def test_question_and_done(self):
        self.assertEqual(h.ending("Want me to open the PR?"), "asked you a question")
        self.assertEqual(h.ending("Merged and deployed."), "done")
        self.assertEqual(h.ending(""), "done")


def sess(**kw):
    return {"id": "s1", "title": "Nightly digest", "last_activity_at": NOW - 60, "last_read_at": 0, **kw}


class SessionItem(unittest.TestCase):
    def test_sign_in_is_now_and_shows_no_message_text(self):
        it = h.session_item("ceo", "Sanvith", sess(), "Please log in to X, then I can continue.", NOW)
        self.assertEqual((it["priority"], it["title"]), ("now", "Sanvith needs sign-in"))
        self.assertNotIn("log in to X", it["detail"])
        self.assertEqual(it["action"]["value"], "hermes -p ceo chat --resume s1")

    def test_question_only_while_unread(self):
        self.assertEqual(h.session_item("ceo", "S", sess(), "Shall I go ahead?", NOW)["priority"], "today")
        self.assertIsNone(h.session_item("ceo", "S", sess(last_read_at=NOW), "Shall I go ahead?", NOW))

    def test_finished_session_is_not_an_item(self):
        self.assertIsNone(h.session_item("ceo", "S", sess(), "All done.", NOW))


class Prs(unittest.TestCase):
    def pr(self, **kw):
        return {"repo": "BkashJEE/river", "n": 3, "title": "t", "url": "u", "now": NOW, "at": NOW - 3600, **kw}

    def test_classify(self):
        self.assertEqual(h.classify_pr({"isDraft": True}), "draft")
        self.assertEqual(h.classify_pr({"baseRefName": "feat/x"}), "stacked")
        self.assertEqual(h.classify_pr({"baseRefName": "main", "mergeable": "CONFLICTING"}), "conflict")
        self.assertEqual(h.classify_pr({"baseRefName": "main", "mergeable": "MERGEABLE", "mergeStateStatus": "BLOCKED",
                                        "statusCheckRollup": [{"conclusion": "FAILURE"}]}), "failing-checks")
        self.assertEqual(h.classify_pr({"baseRefName": "main", "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"}), "ready")
        self.assertEqual(h.classify_pr({"baseRefName": "main", "mergeable": "UNKNOWN"}), "unknown")

    def test_fresh_ready_is_today_stale_ready_is_later(self):
        self.assertEqual(h.pr_item(self.pr(cat="ready"))["priority"], "today")
        self.assertEqual(h.pr_item(self.pr(cat="ready", at=NOW - 40 * 86400))["priority"], "later")

    def test_upstream_and_drafts_are_skipped(self):
        self.assertIsNone(h.pr_item(self.pr(cat="ready", repo="NousResearch/hermes-agent")))
        self.assertIsNone(h.pr_item(self.pr(cat="draft")))


def item(i, priority="today", at=NOW - 100, source="review"):
    return {"id": i, "source": source, "priority": priority, "title": i, "detail": "", "at": at,
            "action": {"kind": "url", "label": "Open", "value": "x"}}


class Visible(unittest.TestCase):
    def test_sorted_by_priority_then_newest(self):
        out = h.visible([item("a", "later"), item("b", "now", NOW - 500), item("c", "now", NOW - 10)], {}, NOW)
        self.assertEqual([i["id"] for i in out], ["c", "b", "a"])

    def test_done_hides_until_the_item_changes(self):
        state = {"a": {"done": True, "at": NOW - 50}}
        self.assertEqual(h.visible([item("a", at=NOW - 100)], state, NOW), [])
        self.assertEqual(len(h.visible([item("a", at=NOW - 10)], state, NOW)), 1)

    def test_snooze_hides_until_it_ends(self):
        self.assertEqual(h.visible([item("a")], {"a": {"snooze_until": NOW + 60}}, NOW), [])
        self.assertEqual(len(h.visible([item("a")], {"a": {"snooze_until": NOW - 60}}, NOW)), 1)


class Collapse(unittest.TestCase):
    def test_a_daily_cron_is_one_line(self):
        a = dict(item("session:prbabysitter:1", "now", NOW - 86400, "sessions"), title="PR Babysitter needs sign-in",
                 detail="“PR Babysitter — daily 08:00 Pacific · Oct 14 09:19”")
        b = dict(item("session:prbabysitter:2", "now", NOW - 60, "sessions"), title="PR Babysitter needs sign-in",
                 detail="“PR Babysitter — daily 08:00 Pacific · Oct 15 09:27”")
        out = h.collapse_sessions([a, b, item("review:1")])
        sessions = [i for i in out if i["source"] == "sessions"]
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["id"], "session:prbabysitter:2")
        self.assertIn("also 1 earlier time", sessions[0]["detail"])

    def test_different_titles_stay_separate(self):
        a = dict(item("session:ceo:1", source="sessions"), title="Sanvith asked you a question", detail="“Plan the week”")
        b = dict(item("session:ceo:2", source="sessions"), title="Sanvith asked you a question", detail="“Fix the login”")
        self.assertEqual(len(h.collapse_sessions([a, b])), 2)


class Time(unittest.TestCase):
    def test_iso_is_utc(self):
        self.assertEqual(h.iso_ts("1970-01-02T00:00:00.000Z"), 86400.0)
        self.assertEqual(h.iso_ts(None), 0.0)


if __name__ == "__main__":
    unittest.main()
