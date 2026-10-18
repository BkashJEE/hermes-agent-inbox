"""0.6.0: shared state actions, the change stamp, the guarded cron re-run, and the Desktop backend
(dashboard/plugin_api.py) through FastAPI's test client.

Run: ~/.hermes/hermes-agent/venv/bin/python tests/test_backend.py
(the backend tests skip themselves without fastapi; the rest run under plain python3).
"""
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import hermes_inbox as h  # noqa: E402

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
except ImportError:
    FastAPI = None


class Home(unittest.TestCase):
    """A throwaway Hermes root with one profile and one failing cron job."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.hermes = base / ".hermes"
        (self.hermes / "cron").mkdir(parents=True)
        self.jobs = self.hermes / "cron" / "jobs.json"
        self.jobs.write_text(json.dumps({"jobs": [
            {"id": "j1", "name": "nightly", "enabled": True, "last_status": "error", "failure_streak": 2, "last_error": "boom"},
            {"id": "j2", "name": "fine", "enabled": True, "last_status": "ok"}]}))
        self.patches = [mock.patch.object(h, "HERMES", self.hermes), mock.patch.object(h, "STATE", base / "state" / "state.json"),
                        mock.patch.object(h, "FILED", self.hermes / "inbox"), mock.patch.object(h, "CONFIG", self.hermes / "inbox.json")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()


class StateActions(Home):
    def test_done_and_snooze_hide_items(self):
        h.mark_done("x:1")
        h.snooze("x:2", 2)
        st = h.load_state()
        self.assertTrue(st["x:1"]["done"])
        self.assertGreater(st["x:2"]["snooze_until"], time.time() + 3600)
        h.reset_state()
        self.assertEqual(h.load_state(), {})

    def test_dispatch_uses_them(self):
        a = mock.Mock(action="snooze", target=["x:3", "1"])
        self.assertEqual(json.loads(h.dispatch(a)), {"ok": True})
        self.assertIn("x:3", h.load_state())
        with self.assertRaises(ValueError):
            h.dispatch(mock.Mock(action="snooze", target=["x:3"]))


class Stamp(Home):
    def test_moves_when_a_cron_file_changes(self):
        first = h.change_stamp()["stamp"]
        later = first + 50
        os.utime(self.jobs, (later, later))
        self.assertEqual(h.change_stamp()["stamp"], later)

    def test_missing_files_are_fine(self):
        self.jobs.unlink()
        self.assertIsInstance(h.change_stamp()["stamp"], float)


class RunCron(Home):
    def test_failed_job_carries_its_id(self):
        items = h.src_crons(time.time())
        self.assertEqual([i["cron"] for i in items], [{"profile": "default", "id": "j1"}])

    def test_refuses_a_job_that_is_not_failing(self):
        with mock.patch.object(h.subprocess, "run") as run:
            self.assertFalse(h.run_cron("default", "j2")["ok"])
            self.assertFalse(h.run_cron("ceo", "j1")["ok"])  # right id, wrong profile
            run.assert_not_called()

    def test_runs_the_failing_job_as_argv(self):
        with mock.patch.object(h.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="Queued j1\n", stderr="")) as run, \
             mock.patch.object(h.shutil, "which", return_value="/usr/bin/hermes"):
            r = h.run_cron("default", "j1")
        self.assertTrue(r["ok"])
        self.assertEqual(run.call_args[0][0], ["/usr/bin/hermes", "-p", "default", "cron", "run", "j1"])


@unittest.skipIf(FastAPI is None, "fastapi not installed; run with ~/.hermes/hermes-agent/venv/bin/python")
class Backend(Home):
    def setUp(self):
        super().setUp()
        sys.modules["hermes_agent_inbox_script"] = h  # the backend loads hermes_inbox.py by path; reuse the patched module
        spec = importlib.util.spec_from_file_location("hermes_dashboard_plugin_hermes-agent-inbox", ROOT / "dashboard" / "plugin_api.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        app = FastAPI()
        app.include_router(mod.router, prefix="/api/plugins/hermes-agent-inbox")
        self.c = TestClient(app)
        self.u = "/api/plugins/hermes-agent-inbox"

    def tearDown(self):
        sys.modules.pop("hermes_agent_inbox_script", None)
        super().tearDown()

    def test_health_and_stamp(self):
        self.assertTrue(self.c.get(f"{self.u}/health").json()["ok"])
        self.assertIn("stamp", self.c.get(f"{self.u}/stamp").json())

    def test_list_passes_apps_and_is_not_trimmed(self):
        big = {"items": [{"id": f"i{n}", "title": "x" * 500} for n in range(200)]}  # ~100 KB, over the CLI cap
        with mock.patch.object(h, "collect", return_value=big) as collect:
            r = self.c.get(f"{self.u}/list?app=hermes&app=xstudio")
        self.assertEqual(collect.call_args[0], (None, ["hermes", "xstudio"]))
        self.assertEqual(len(r.json()["items"]), 200)

    def test_actions_validate_their_bodies(self):
        self.assertEqual(self.c.post(f"{self.u}/done", json={}).status_code, 400)
        self.assertEqual(self.c.post(f"{self.u}/snooze", json={"id": "a", "hours": -1}).status_code, 400)
        self.assertEqual(self.c.post(f"{self.u}/snooze", json={"id": "a", "hours": 99999}).status_code, 400)
        self.assertEqual(self.c.post(f"{self.u}/set", json={"source": "nope", "on": True}).status_code, 400)
        self.assertEqual(self.c.post(f"{self.u}/done", json={"id": "a"}).json(), {"ok": True})
        self.assertEqual(self.c.post(f"{self.u}/snooze", json={"id": "b", "hours": 24}).json(), {"ok": True})

    def test_run_cron_is_guarded(self):
        with mock.patch.object(h.subprocess, "run") as run:
            self.assertFalse(self.c.post(f"{self.u}/run-cron", json={"profile": "default", "id": "j2"}).json()["ok"])
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
