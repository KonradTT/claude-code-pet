#!/usr/bin/env python3
"""The task map in state.json: what hooks add, and what gets reaped.

Run with:  python3 engine/tests/test_petctl_tasks.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import petctl  # noqa: E402
import sessions  # noqa: E402

LIVE = "11111111-1111-4111-8111-111111111111"
DEAD = "22222222-2222-4222-8222-222222222222"


class TaskMap(unittest.TestCase):

    def setUp(self):
        self.pet = tempfile.TemporaryDirectory()
        self.cfg = tempfile.TemporaryDirectory()
        self.addCleanup(self.pet.cleanup)
        self.addCleanup(self.cfg.cleanup)
        os.environ["CLAUDE_CONFIG_DIR"] = self.cfg.name
        self.addCleanup(os.environ.pop, "CLAUDE_CONFIG_DIR", None)
        self.home = Path(self.pet.name)
        self.registry = Path(self.cfg.name) / "sessions"
        self.registry.mkdir(parents=True)

    def register(self, sid: str, name: str = "", pid: int | None = None) -> None:
        (self.registry / f"{sid[:8]}.json").write_text(json.dumps(
            {"pid": pid if pid is not None else os.getpid(),
             "sessionId": sid, "name": name}), encoding="utf-8")

    def state(self) -> dict:
        return json.loads((self.home / "state.json").read_text(encoding="utf-8"))

    def seed(self, tasks: dict) -> None:
        petctl.write(self.home / "state.json", {"tasks": tasks})

    def test_a_hook_reaps_the_session_that_died_with_its_tab(self):
        self.seed({LIVE: {"state": "running", "title": "keep"},
                   DEAD: {"state": "idle", "title": "", "subtitle": "Ready"}})
        self.register(LIVE, "keep")
        petctl.update_task(self.home, LIVE, state="running", subtitle="working")
        self.assertEqual(list(self.state()["tasks"]), [LIVE])
        self.assertEqual(self.state()["sessions"], 1)

    def test_a_hook_never_deletes_its_own_entry(self):
        petctl.update_task(self.home, DEAD, state="running", subtitle="working")
        self.assertEqual(list(self.state()["tasks"]), [DEAD])

    def test_session_end_still_removes_its_own_entry(self):
        self.register(LIVE, "keep")
        petctl.update_task(self.home, LIVE, state="idle")
        petctl.update_task(self.home, LIVE, remove=True)
        self.assertEqual(self.state()["tasks"], {})
        self.assertEqual(self.state()["sessions"], 0)

    def test_a_started_session_counts_for_visibility_but_draws_no_row(self):
        """The pet must still appear when a session opens, even though a session
        with no work yet has nothing to put in the list."""
        self.register(LIVE, "")
        petctl.update_task(self.home, LIVE, state="idle", title="", subtitle="")
        self.assertEqual(self.state()["sessions"], 1)
        self.assertEqual(
            sessions.merge_rows({}, self.state()["tasks"], sessions.live(), 8), [])

    def test_reaping_leaves_the_aggregate_state_consistent(self):
        self.seed({DEAD: {"state": "needs_input", "title": "gone", "subtitle": "?"}})
        self.register(LIVE, "here")
        petctl.update_task(self.home, LIVE, state="running", subtitle="working")
        after = self.state()
        self.assertEqual(after["state"], "running")
        self.assertEqual(after["sessions"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
