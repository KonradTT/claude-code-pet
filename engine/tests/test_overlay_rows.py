#!/usr/bin/env python3
"""End to end through the overlay widget: closing a tab shrinks the panel.

Needs PySide6 and runs headless. Run with the pet's own interpreter:
    QT_QPA_PLATFORM=offscreen .venv/bin/python engine/tests/test_overlay_rows.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_home = tempfile.mkdtemp(prefix="pet-home-")
_cfg = tempfile.mkdtemp(prefix="pet-cfg-")
os.environ["ASSISTANT_PET_HOME"] = _home
os.environ["CLAUDE_CONFIG_DIR"] = _cfg
os.environ.setdefault("CLAUDE_PETS_DIR", str(ENGINE.parent / "pets"))

try:
    from PySide6.QtWidgets import QApplication
    import pet_overlay
except ImportError:                              # pragma: no cover
    QApplication = None

IN_TAB = "11111111-1111-4111-8111-111111111111"
CLOSED = "22222222-2222-4222-8222-222222222222"


def agent(pane_id, title, state="running"):
    return {"pane_id": pane_id, "tab_id": pane_id.replace("p", "t"),
            "title": title, "state": state, "focused": False, "cwd": ""}


@unittest.skipUnless(QApplication, "PySide6 not installed")
class OverlayPanel(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.registry = Path(_cfg) / "sessions"
        cls.registry.mkdir(parents=True, exist_ok=True)
        cls.pet = pet_overlay.Pet()

    def register(self, sid, name=""):
        (self.registry / f"{sid[:8]}.json").write_text(
            json.dumps({"pid": os.getpid(), "sessionId": sid, "name": name}),
            encoding="utf-8")

    def unregister(self, sid):
        (self.registry / f"{sid[:8]}.json").unlink(missing_ok=True)

    def sync(self):
        """What the 3-second poller does, run inline."""
        import sessions
        self.pet._live = sessions.live()

    def test_closing_a_tab_removes_its_row_and_shrinks_the_panel(self):
        pet = self.pet
        pet.expanded = True
        pet.tasks = {IN_TAB: {"state": "running", "title": "", "subtitle": "Editing"},
                     CLOSED: {"state": "running", "title": "", "subtitle": "Editing"}}
        pet.herdr = {IN_TAB: agent("w1:p1", "keep me"),
                     CLOSED: agent("w2:p1", "close me")}
        self.register(IN_TAB, "keep me")
        self.register(CLOSED, "close me")
        self.sync()
        self.assertEqual(len(pet.session_list()), 2)
        tall = pet.panel_height()

        # The tab is closed: herdr stops reporting it and its session is gone.
        pet.herdr.pop(CLOSED)
        self.unregister(CLOSED)
        self.sync()

        rows = pet.session_list()
        self.assertEqual([sid for sid, _ in rows], [IN_TAB])
        self.assertLess(pet.panel_height(), tall)
        self.assertEqual(pet.panel_height(), tall - pet_overlay.ROW_H)

    def test_no_row_is_ever_drawn_as_the_bare_word_working(self):
        """The regression itself: a session that outlives its pane must still
        say which session it is."""
        pet = self.pet
        pet.tasks = {IN_TAB: {"state": "running", "title": "", "subtitle": "Editing"}}
        pet.herdr = {}
        self.register(IN_TAB, "github commit code review")
        self.sync()
        titles = [e["title"] for _, e in pet.session_list()]
        self.assertEqual(titles, ["github commit code review"])
        self.assertNotIn("", titles)

    def test_the_badge_counts_the_rows_it_draws(self):
        pet = self.pet
        pet.tasks = {IN_TAB: {"state": "running", "title": "one", "subtitle": ""},
                     CLOSED: {"state": "idle", "title": "", "subtitle": "Ready"}}
        pet.herdr = {}
        self.register(IN_TAB, "one")
        self.unregister(CLOSED)
        self.sync()
        self.assertEqual(len(pet.session_list()), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
