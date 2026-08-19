#!/usr/bin/env python3
"""When the task card is up: on hover, and for a short while after it leaves.

Needs PySide6 and runs headless. Run with the pet's own interpreter:
    QT_QPA_PLATFORM=offscreen .venv/bin/python engine/tests/test_card_visibility.py
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
_home = tempfile.mkdtemp(prefix="pet-card-home-")
os.environ["ASSISTANT_PET_HOME"] = _home
os.environ.setdefault("CLAUDE_PETS_DIR", str(ENGINE.parent / "pets"))

try:
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication
    import pet_overlay
except ImportError:                              # pragma: no cover
    QApplication = None


@unittest.skipUnless(QApplication, "PySide6 not installed")
class CardVisibility(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.pet = pet_overlay.Pet()

    def setUp(self):
        pet = self.pet
        pet.expanded = False
        pet.hovered = False
        pet.tick = 100_000
        pet.card_until = 0
        pet.state = "needs_input"
        pet.title = "lets go with option 2"
        pet.subtitle = "Claude is waiting for you"

    def leave(self):
        self.pet.leaveEvent(QEvent(QEvent.Type.Leave))

    def enter(self):
        self.pet.enterEvent(QEvent(QEvent.Type.Enter))

    def test_needs_input_alone_does_not_put_the_card_up(self):
        self.assertFalse(self.pet.showing_card())

    def test_neither_does_running_or_blocked(self):
        for state in ("running", "blocked", "ready", "idle"):
            with self.subTest(state=state):
                self.pet.state = state
                self.assertFalse(self.pet.showing_card())

    def test_hovering_puts_the_card_up(self):
        self.pet.hovered = True
        self.assertTrue(self.pet.showing_card())

    def test_the_card_stays_up_for_five_seconds_after_the_mouse_leaves(self):
        self.pet.hovered = True
        self.assertTrue(self.pet.showing_card())
        self.leave()
        self.assertTrue(self.pet.showing_card())

        self.pet.tick += pet_overlay.CARD_LINGER_MS - 1
        self.assertTrue(self.pet.showing_card())
        self.pet.tick += 2
        self.assertFalse(self.pet.showing_card())

    def test_the_linger_is_five_seconds(self):
        self.assertEqual(pet_overlay.CARD_LINGER_MS, 5000)

    def test_an_empty_card_is_never_drawn_even_on_hover(self):
        self.pet.title = ""
        self.pet.subtitle = ""
        self.pet.hovered = True
        self.assertFalse(self.pet.showing_card())

    def test_the_expanded_session_list_ignores_hover(self):
        self.pet.expanded = True
        self.assertTrue(self.pet.showing_card())
        self.pet.title = ""
        self.pet.subtitle = ""
        self.assertTrue(self.pet.showing_card())

    def test_work_steps_do_not_keep_the_card_up_while_not_hovering(self):
        """The subtitle changes on every tool call. That must not re-arm the
        linger, or the card is up for the whole of a long turn."""
        pet = self.pet
        state = Path(_home) / "state.json"
        for step in ("Reading pet.py", "Editing pet.py", "Running the tests"):
            state.write_text(json.dumps({
                "state": "running", "title": "fix the card",
                "subtitle": step, "sessions": 1, "tasks": {}}), encoding="utf-8")
            pet.reload(force=True)
            pet.tick += 1000
            self.assertFalse(pet.showing_card(), f"card up during {step!r}")

    def test_hovering_mid_turn_still_shows_the_current_step(self):
        pet = self.pet
        state = Path(_home) / "state.json"
        state.write_text(json.dumps({
            "state": "running", "title": "fix the card",
            "subtitle": "Editing pet.py", "sessions": 1, "tasks": {}}),
            encoding="utf-8")
        pet.reload(force=True)
        self.enter()
        self.assertTrue(pet.showing_card())
        self.assertEqual(pet.card_lines(), ("fix the card", "Editing pet.py"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
