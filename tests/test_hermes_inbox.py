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

    def test_only_the_closing_part_counts(self):
        long = "Step 4: the Bot hits a 2FA code and you take over. " + "Details. " * 80 + "All set, it's merged."
        self.assertEqual(h.ending(long), "done")

    def test_question_and_done(self):
        self.assertEqual(h.ending("Want me to open the PR?"), "asked you a question")
        self.assertEqual(h.ending("Merged and deployed."), "done")
        self.assertEqual(h.ending("Rendered. Please check the GIF before I post."), "asked you to check")
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


class PrApp(unittest.TestCase):
    def test_hermes_repos_go_to_hermes(self):
        self.assertEqual(h.pr_app("me/hermes-newsroom", 3, ["hermes"], set(), False), "hermes")
        self.assertEqual(h.pr_app("me/Hermes-Agent-Archive", 3, ["hermes"], set(), False), "hermes")
        self.assertEqual(h.pr_app("me/screenpolish", 3, ["hermes", "screenpolish"], set(), False), "hermes")

    def test_claude_prs_only_when_the_switch_is_on(self):
        self.assertEqual(h.pr_app("me/dotfiles", 7, ["hermes"], {"me/dotfiles#7"}, True), "claude")
        self.assertIsNone(h.pr_app("me/dotfiles", 7, ["hermes"], {"me/dotfiles#7"}, False))
        self.assertIsNone(h.pr_app("me/dotfiles", 8, ["hermes"], {"me/dotfiles#7"}, True))

    def test_unrelated_prs_are_left_out(self):
        self.assertIsNone(h.pr_app("me/weekend-game", 1, ["hermes"], set(), True))


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


class Filed(unittest.TestCase):
    def test_card_with_url_and_bad_priority(self):
        c = h.filed_card({"id": "x", "title": "Pick a thumbnail", "url": "https://e.x", "priority": "urgent", "from": "Gary"}, NOW)
        self.assertEqual((c["id"], c["priority"], c["action"]["kind"]), ("filed:x", "today", "url"))
        self.assertEqual(c["why"], "Posted by Gary.")

    def test_session_card_opens_the_chat(self):
        c = h.filed_card({"id": "y", "title": "t", "session": "s9", "profile": "gary"}, NOW)
        self.assertEqual((c["action"]["kind"], c["action"]["session"], c["action"]["profile"]), ("session", "s9", "gary"))

    def test_expired_and_malformed_are_skipped(self):
        self.assertIsNone(h.filed_card({"id": "z", "title": "t", "expires_at": NOW - 1}, NOW))
        self.assertIsNone(h.filed_card({"title": "no id"}, NOW))
        self.assertIsNone(h.filed_card("junk", NOW))

    def test_post_update_and_done_round_trip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            old = h.FILED
            h.FILED = Path(d)
            try:
                a = h.file_card("Approve the deploy", sender="Jarvis", key="deploy")
                h.file_card("Approve the deploy (v2)", sender="Jarvis", key="deploy")  # same key updates
                cards = h.src_filed(NOW)
                self.assertEqual([c["title"] for c in cards], ["Approve the deploy (v2)"])
                self.assertTrue(h.remove_card(a["id"]))
                self.assertEqual(h.src_filed(NOW), [])
            finally:
                h.FILED = old


class Root(unittest.TestCase):
    def test_profile_home_maps_to_root(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"HERMES_HOME": "/x/.hermes/profiles/ceo"}, clear=False):
            os.environ.pop("HERMES_ROOT", None)
            self.assertEqual(h.hermes_root(), Path("/x/.hermes"))
        with mock.patch.dict(os.environ, {"HERMES_HOME": "/y/hh"}, clear=False):
            os.environ.pop("HERMES_ROOT", None)
            self.assertEqual(h.hermes_root(), Path("/y/hh"))


class Transcripts(unittest.TestCase):
    def test_waiting_only_when_the_assistant_spoke_last(self):
        it = h.transcript_item("codex", "abc", "Daily review", "assistant", "<final>Want me to post it?</final>", NOW, "/w", NOW)
        self.assertEqual((it["title"], it["agent"], it["app"]), ("Codex asked you a question", "Codex", "codex"))
        self.assertIn("codex resume abc", it["action"]["value"])
        self.assertIsNone(h.transcript_item("claude", "x", "t", "user", "Shall I?", NOW, "", NOW))
        self.assertIsNone(h.transcript_item("claude", "x", "t", "assistant", "Done, merged.", NOW, "", NOW))


class Switch(unittest.TestCase):
    def test_set_source_writes_config_and_keeps_other_settings(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            old = h.CONFIG
            h.CONFIG = Path(d) / "inbox.json"
            try:
                h.CONFIG.write_text('{"stale_pr_days": 9}')
                self.assertEqual(h.set_source("claude", True)["sources"], {"claude": True})
                h.set_source("claude", False)
                self.assertEqual(h.config(), {"stale_pr_days": 9, "sources": {"claude": False}})
            finally:
                h.CONFIG = old


class Time(unittest.TestCase):
    def test_iso_is_utc(self):
        self.assertEqual(h.iso_ts("1970-01-02T00:00:00.000Z"), 86400.0)
        self.assertEqual(h.iso_ts(None), 0.0)


if __name__ == "__main__":
    unittest.main()
