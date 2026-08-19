#!/usr/bin/env python3
"""Reconciling the pet's session list against Claude Code's session registry.

Run with:  python3 -m unittest discover -s engine/tests -v
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sessions  # noqa: E402

LIVE = "11111111-1111-4111-8111-111111111111"
DEAD = "22222222-2222-4222-8222-222222222222"
OTHER = "33333333-3333-4333-8333-333333333333"

MAX_ROWS = 8


def pane(sid, title, state="running", pane_id="w1:p1"):
    return {sid: {"pane_id": pane_id, "tab_id": "w1:t1", "title": title,
                  "state": state, "focused": False, "cwd": ""}}


def task(state="running", title="", subtitle=""):
    return {"state": state, "title": title, "subtitle": subtitle}


class ClosingATab(unittest.TestCase):
    """The reported bug: closing a herdr tab left a nameless 'Working' row."""

    def test_row_disappears_when_the_session_died_with_its_tab(self):
        tasks = {DEAD: task(state="idle", subtitle="Ready")}
        rows = sessions.merge_rows({}, tasks, {LIVE: "still here"}, MAX_ROWS)
        self.assertEqual(rows, [])

    def test_without_a_registry_a_named_dead_session_still_lingers(self):
        """Nothing else can prove a session is gone, so on a machine with no
        registry the union is all we have. The documented limitation."""
        tasks = {DEAD: task(state="ready", title="finished work", subtitle="Done")}
        rows = sessions.merge_rows({}, tasks, None, MAX_ROWS)
        self.assertEqual([sid for sid, _ in rows], [DEAD])

    def test_the_registry_is_what_reaps_a_named_dead_session(self):
        tasks = {DEAD: task(state="ready", title="finished work", subtitle="Done")}
        self.assertEqual(sessions.merge_rows({}, tasks, {}, MAX_ROWS), [])

    def test_list_shrinks_by_exactly_the_closed_tab(self):
        agents = {**pane(LIVE, "keep me"), **pane(OTHER, "close me", pane_id="w2:p1")}
        tasks = {LIVE: task(title="keep me"), OTHER: task(title="close me")}
        alive = {LIVE: "keep me", OTHER: "close me"}
        self.assertEqual(len(sessions.merge_rows(agents, tasks, alive, MAX_ROWS)), 2)

        agents.pop(OTHER)          # the tab is closed and its session goes with it
        alive.pop(OTHER)
        after = sessions.merge_rows(agents, tasks, alive, MAX_ROWS)
        self.assertEqual([sid for sid, _ in after], [LIVE])

    def test_a_job_that_outlives_its_tab_keeps_its_name(self):
        """Background jobs survive their pane. The row stays - it must not
        collapse into the generic 'Working' fallback."""
        tasks = {LIVE: task(state="running", subtitle="Editing pet.py")}
        rows = sessions.merge_rows({}, tasks, {LIVE: "github commit review"}, MAX_ROWS)
        self.assertEqual([(sid, e["title"]) for sid, e in rows],
                         [(LIVE, "github commit review")])

    def test_hook_title_still_wins_over_the_registry_name(self):
        tasks = {LIVE: task(title="lets go with option 2")}
        rows = sessions.merge_rows({}, tasks, {LIVE: "github commit review"}, MAX_ROWS)
        self.assertEqual(rows[0][1]["title"], "lets go with option 2")


class NothingToShow(unittest.TestCase):
    """A session that has started but been given no work is not a row."""

    def test_a_started_session_with_no_work_is_not_listed(self):
        tasks = {LIVE: task(state="idle")}
        self.assertEqual(sessions.merge_rows({}, tasks, {LIVE: ""}, MAX_ROWS), [])

    def test_a_placeholder_subtitle_from_an_older_version_earns_no_row(self):
        """State written before this fix still carries subtitle "Ready"; those
        rows must clear themselves rather than linger as "Working"."""
        tasks = {LIVE: task(state="idle", subtitle="Ready")}
        self.assertEqual(sessions.merge_rows({}, tasks, {LIVE: ""}, MAX_ROWS), [])

    def test_an_idle_session_that_has_a_name_still_gets_a_row(self):
        tasks = {LIVE: task(state="idle", title="lets go with option 2")}
        rows = sessions.merge_rows({}, tasks, {LIVE: ""}, MAX_ROWS)
        self.assertEqual([sid for sid, _ in rows], [LIVE])

    def test_work_in_flight_earns_a_row_even_unnamed(self):
        tasks = {LIVE: task(state="needs_input", subtitle="Waiting for you")}
        rows = sessions.merge_rows({}, tasks, {LIVE: ""}, MAX_ROWS)
        self.assertEqual([sid for sid, _ in rows], [LIVE])

    def test_a_registry_name_that_is_just_the_id_is_not_a_name(self):
        tasks = {LIVE: task(state="idle")}
        rows = sessions.merge_rows({}, tasks, {LIVE: LIVE[:8]}, MAX_ROWS)
        self.assertEqual(rows, [])

    def test_a_pane_alone_is_enough_to_earn_a_row(self):
        rows = sessions.merge_rows(pane(LIVE, "in a tab"), {}, {LIVE: ""}, MAX_ROWS)
        self.assertEqual([sid for sid, _ in rows], [LIVE])


class Pruning(unittest.TestCase):

    def test_unknown_registry_prunes_nothing(self):
        tasks = {DEAD: task()}
        self.assertEqual(sessions.prune(tasks, None), tasks)

    def test_empty_registry_prunes_everything_claude_shaped(self):
        self.assertEqual(sessions.prune({DEAD: task()}, {}), {})

    def test_manual_sessions_are_never_reaped(self):
        tasks = {"manual-4321": task(title="hand-started")}
        self.assertEqual(sessions.prune(tasks, {}), tasks)

    def test_the_current_session_is_protected(self):
        """A hook must never delete the entry it just wrote, however the
        registry reads."""
        tasks = {DEAD: task(title="mine")}
        self.assertEqual(sessions.prune(tasks, {}, keep={DEAD}), tasks)


class Registry(unittest.TestCase):

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["CLAUDE_CONFIG_DIR"] = self.tmp.name
        self.addCleanup(os.environ.pop, "CLAUDE_CONFIG_DIR", None)
        self.dir = Path(self.tmp.name) / "sessions"

    def write(self, name, payload):
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / name).write_text(json.dumps(payload), encoding="utf-8")

    def test_missing_registry_reads_as_unknown(self):
        self.assertIsNone(sessions.live())

    def test_running_pid_is_live_and_dead_pid_is_not(self):
        self.write("1.json", {"pid": os.getpid(), "sessionId": LIVE, "name": "mine"})
        self.write("2.json", {"pid": 999999, "sessionId": DEAD, "name": "gone"})
        self.assertEqual(sessions.live(), {LIVE: "mine"})

    def test_unparseable_registry_reads_as_unknown(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "1.json").write_text("{not json", encoding="utf-8")
        self.assertIsNone(sessions.live())

    def test_an_empty_registry_directory_means_nothing_is_running(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        self.assertEqual(sessions.live(), {})


class Ordering(unittest.TestCase):

    def test_panes_sort_above_paneless_jobs_and_max_rows_is_honoured(self):
        agents = pane(OTHER, "zzz last alphabetically")
        tasks = {LIVE: task(title="aaa first alphabetically"), OTHER: task()}
        rows = sessions.merge_rows(agents, tasks, {LIVE: "", OTHER: ""}, 1)
        self.assertEqual([sid for sid, _ in rows], [OTHER])


if __name__ == "__main__":
    unittest.main(verbosity=2)
