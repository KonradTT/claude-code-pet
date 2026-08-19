#!/usr/bin/env python3
"""Always-on-top desktop pet driven by Claude Code lifecycle hooks.

Artwork and animation set are imported from the Codex pet spritesheet by
import_codex.py - 11 animations, 74 frames, every frame sharing one baseline.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

import sessions

try:
    import herdr_link
except ImportError:                       # the integration is optional
    herdr_link = None

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import (QAction, QColor, QCursor, QFont, QFontMetrics,
                           QPainter, QPixmap, QRegion)
from PySide6.QtWidgets import QApplication, QMenu, QWidget

STATES = ("idle", "running", "needs_input", "ready", "blocked")
DOT = {"idle": "#94a3b8", "running": "#38bdf8", "needs_input": "#f59e0b",
       "ready": "#22c55e", "blocked": "#ef4444"}
DEFAULT_MSG = {"idle": "Waiting for a task", "running": "Working…",
               "needs_input": "Needs your input", "ready": "Task ready",
               "blocked": "Something went wrong"}

# steady-state loop for each activity state
STATE_ANIM = {"running": "think", "needs_input": "shrug",
              "blocked": "angry", "ready": "idle", "idle": "idle"}
# one-shot played the moment a state is entered
STATE_ENTER = {"ready": "celebrate"}

MIN_H, MAX_H, DEFAULT_H = 48, 400, 180
PET_TOP = 6                   # y of the pet inside the window
CARD_GAP = 10                 # pet -> card
CARD_AREA = 64                # reserved height below the pet, so geometry is stable
CARD_MAX_W = 320
CARD_LINGER_MS = 5000         # how long the card stays up after work finishes
CTRL_GAP, CTRL_H, BTN_R = 6, 30, 13     # hover control row under the pet
ROW_H = 40                    # one task row in the expanded panel
MAX_ROWS = 8                  # keep the panel a sane height
TICK_MS = 40
MOVE_STOP_MS = 160
RUN_SPEED = 260               # px/sec of drag above which the walk speeds up


def pet_home() -> Path:
    configured = os.environ.get("ASSISTANT_PET_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".assistant-pet"


def pets_dir() -> Path:
    """Where the pet library lives - one folder per pet, each with a pet.json."""
    configured = os.environ.get("CLAUDE_PETS_DIR")
    return (Path(configured).expanduser() if configured
            else Path.home() / "Documents/Claude/Pets")


def available_pets() -> list[str]:
    try:
        return sorted(d.name for d in pets_dir().iterdir()
                      if (d / "pet.json").is_file())
    except OSError:
        return []


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


class Pet(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.home = pet_home()
        self.state_path = self.home / "state.json"
        self.config_path = self.home / "config.json"
        cfg = read_json(self.config_path)
        self.pet_id = str(cfg.get("pet") or "mumu")
        self.pet_meta, self.anims = self.load_pet(self.pet_id)
        if not self.anims:
            found = available_pets()
            sys.exit(f"No pet named {self.pet_id!r} in {pets_dir()}. "
                     + (f"Available: {', '.join(found)}" if found
                        else "Hatch one with the hatch-pet skill."))
        self.pet_h = int(cfg.get("height", DEFAULT_H))
        self.always = bool(cfg.get("always", False))
        self.enabled = bool(cfg.get("enabled", True))
        self.invert_facing = bool(cfg.get("invert_facing", False))

        self.state = "idle"
        self.message = ""
        self.title = ""
        self.subtitle = ""
        self.tasks: dict = {}         # from Claude Code hooks
        self.herdr: dict = {}         # from herdr, adds pane ids and unseen sessions
        self._live: dict | None = None  # Claude's own registry; None until first poll
        self._sessions_dirty = False  # the poller ran; the GUI thread must redraw
        self._herdr_tick = 0
        self.rows: list = []          # (rect, session_id, entry) for hit testing
        self.expanded = False
        self._btn_armed = False
        self.card_until = 0
        self.sessions = 0
        self.facing = 1               # 1 right, -1 left
        self.moving = False
        self.fast = False
        self.hovered = False
        self.oneshot: str | None = None
        self.playing = "idle"
        self.frame = 0
        self.frame_clock = 0
        self.tick = 0
        self.last_drag_t = 0
        self.drag_origin: QPoint | None = None
        self.desired_pos: tuple[int, int] | None = None
        self._hide_pending = False
        self._furniture = None
        self._mask_cache = None
        self._scale_cache: dict[tuple, QPixmap] = {}
        self._state_mtime = None
        self._config_mtime = None
        self._last_play_id = None

        self.setWindowTitle("Claude Pet")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool
                            | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # macOS marks Qt.Tool windows hidesOnDeactivate, so the pet disappears the
        # moment its own process is not frontmost - i.e. always, from a terminal.
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        self.setMouseTracking(True)

        self.resize_to_pet()
        self.restore_position(cfg)

        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self.advance)
        self.anim_timer.start(TICK_MS)
        self.move_timer = QTimer(self); self.move_timer.setSingleShot(True)
        self.move_timer.timeout.connect(self.stop_moving)
        self.poll = QTimer(self); self.poll.timeout.connect(self.reload); self.poll.start(250)
        self.reload(force=True)
        self.refresh_herdr()

    # ---------------------------------------------------------- animations --
    def load_pet(self, pet_id: str):
        """Load one pet from the library. Returns (metadata, animations)."""
        root = pets_dir() / pet_id
        meta = read_json(root / "pet.json")
        out = {}
        for name, spec in (meta.get("animations") or {}).items():
            path = root / spec["file"]
            if not path.exists():
                continue
            strip = QPixmap(str(path))
            n = max(1, int(spec.get("frames", 1)))
            w = strip.width() // n
            out[name] = {
                "frames": [strip.copy(i * w, 0, w, strip.height()) for i in range(n)],
                "ms": int(spec.get("ms", 120)),
                "loop": bool(spec.get("loop", True)),
            }
        return meta, out

    def switch_pet(self, pet_id: str) -> bool:
        meta, anims = self.load_pet(pet_id)
        if not anims:
            return False
        self.pet_id, self.pet_meta, self.anims = pet_id, meta, anims
        self._scale_cache.clear()
        self.frame = 0
        self.playing = self.pick("idle")
        self.resize_to_pet()
        self.clamp_to_screen()
        self.desired_pos = (self.x(), self.y())
        self.play_once("turn_in")
        return True

    def pick(self, name: str) -> str:
        return name if name in self.anims else "idle"

    def desired_anim(self) -> str:
        """What should be on screen right now. Order: one-shot, drag, hover, state."""
        if self.oneshot:
            return self.oneshot
        if self.moving:
            left = self.facing < 0
            if self.invert_facing:
                left = not left
            return self.pick("walk_left" if left else "walk_right")
        if self.hovered:
            return self.pick("wave")
        return self.pick(STATE_ANIM.get(self.state, "idle"))

    def frame_ms(self, name: str) -> int:
        ms = self.anims[name]["ms"]
        if self.moving and self.fast:
            ms = max(40, round(ms * 0.62))       # drag fast and he picks up the pace
        return ms

    def play_once(self, name: str) -> None:
        if name in self.anims:
            self.oneshot = name
            self.playing = name
            self.frame = 0
            self.frame_clock = 0
            self.update()

    def current_pixmap(self) -> QPixmap:
        frames = self.anims[self.playing]["frames"]
        src = frames[self.frame % len(frames)]
        key = (id(src), self.pet_h)
        hit = self._scale_cache.get(key)
        if hit is None:
            hit = src.scaledToHeight(self.pet_h, Qt.TransformationMode.SmoothTransformation)
            if len(self._scale_cache) > 160:
                self._scale_cache.clear()
            self._scale_cache[key] = hit
        return hit

    def advance(self) -> None:
        self.tick += TICK_MS
        self.hold_position()
        self._herdr_tick += TICK_MS
        if self._herdr_tick >= 3000 and (self.isVisible() or self.expanded):
            self._herdr_tick = 0
            self.refresh_herdr()
        if self.expanded:
            self.update()          # row hover highlight follows the cursor

        want = self.desired_anim()
        if want != self.playing and not self.oneshot:
            self.playing = want
            self.frame = 0
            self.frame_clock = 0
            self.update()

        spec = self.anims[self.playing]
        self.frame_clock += TICK_MS
        if self.frame_clock >= self.frame_ms(self.playing):
            self.frame_clock = 0
            self.frame += 1
            if self.frame >= len(spec["frames"]):
                if spec["loop"] and not self.oneshot:
                    self.frame = 0
                else:                       # a one-shot has played through
                    self.frame = len(spec["frames"]) - 1
                    finished, self.oneshot = self.oneshot, None
                    if finished == "turn_out" and self._hide_pending:
                        self._hide_pending = False
                        super().hide()
                    self.playing = self.desired_anim()
                    self.frame = 0
            self.update()
        rows = len(self.session_list())
        furniture = (self.showing_card(), self.showing_controls(),
                     self.expanded, rows)
        if furniture != self._furniture or self._sessions_dirty:
            row_count_changed = self._furniture is None or rows != self._furniture[3]
            self._furniture, self._sessions_dirty = furniture, False
            if self.expanded and row_count_changed:
                self.relayout()       # the panel is exactly as tall as the list
            else:
                self.apply_mask()
            self.update()

    # ------------------------------------------------------------ geometry --
    def all_frames(self):
        for spec in self.anims.values():
            yield from spec["frames"]

    def panel_top(self) -> int:
        return PET_TOP + self.pet_h + CTRL_GAP + CTRL_H + CARD_GAP

    def panel_height(self) -> int:
        if self.expanded:
            return 12 + max(1, len(self.session_list())) * ROW_H + 8
        return CARD_AREA

    def resize_to_pet(self) -> None:
        widest = max((pm.width() * self.pet_h / max(1, pm.height())
                      for pm in self.all_frames()), default=self.pet_h)
        # wide enough for the task card too, so it never has to resize the window
        self.resize(max(round(widest), CARD_MAX_W) + 24,
                    self.panel_top() + self.panel_height())
        self._mask_cache = None
        self.apply_mask()

    def relayout(self) -> None:
        """Expanding the task list grows the window downward, so the pet stays put."""
        self.resize_to_pet()
        self.update()

    def apply_mask(self) -> None:
        """Shape the window to the union of every frame.

        Masking to just the current frame clips the next one - a lifted leg or a
        raised claw falls outside the silhouette the mask was built from.
        """
        key = (self.pet_h, self.width(), self.height())
        if self._mask_cache is None or self._mask_cache[0] != key:
            region = QRegion()
            for pm in self.all_frames():
                sc = pm.scaledToHeight(self.pet_h, Qt.TransformationMode.FastTransformation)
                r = QRegion(sc.mask())
                r.translate((self.width() - sc.width()) // 2,
                            PET_TOP + (self.pet_h - sc.height()))
                region = region.united(r)
            self._mask_cache = (key, region)
        region = QRegion(self._mask_cache[1])
        if self.showing_card():
            region = region.united(QRegion(self.card_rect()))
        if self.showing_controls():
            region = region.united(QRegion(self.toggle_rect()))
            # A corridor from the pet's feet down past the button to the card.
            # Without it the cursor crosses masked-out space on the way to the
            # button, leaveEvent fires, and the button vanishes before it can
            # be clicked.
            btn, card = self.toggle_rect(), self.card_rect()
            left = min(btn.left(), card.left() if self.showing_card() else btn.left())
            right = max(btn.right(), card.right() if self.showing_card() else btn.right())
            bottom = (card.bottom() if self.showing_card() else btn.bottom())
            region = region.united(QRegion(
                left - 6, PET_TOP + self.pet_h - 10,
                (right - left) + 12, bottom - (PET_TOP + self.pet_h - 10) + 1))
        self.setMask(region)

    # ------------------------------------------------------------ task card --
    @staticmethod
    def title_font() -> QFont:
        f = QFont(); f.setPointSizeF(11.5); f.setWeight(QFont.Weight.DemiBold)
        return f

    @staticmethod
    def sub_font() -> QFont:
        f = QFont(); f.setPointSizeF(10.5); f.setWeight(QFont.Weight.Normal)
        return f

    def session_list(self) -> list[tuple[str, dict]]:
        return sessions.merge_rows(self.herdr, self.tasks, self._live, MAX_ROWS)

    def refresh_herdr(self) -> None:
        """Poll herdr and the session registry off the UI thread - one shells
        out and the other walks a directory, either would stutter here."""
        def work():
            live = sessions.live()
            found = (herdr_link.agents()
                     if herdr_link is not None and herdr_link.available() else {})
            if (found, live) != (self.herdr, self._live):
                self.herdr, self._live = found, live
                self._sessions_dirty = True
        threading.Thread(target=work, daemon=True).start()

    def card_lines(self) -> tuple[str, str]:
        title = self.title or DEFAULT_MSG.get(self.state, "")
        sub = self.subtitle or ""
        if not self.title and not sub:
            sub = ""
        return title, sub

    def showing_card(self) -> bool:
        """Up while you are looking at him, and briefly after you look away.

        Work in flight is not enough on its own: a card pinned up for the whole
        of a long turn is clutter, and the animation already says he is busy.
        """
        if self.expanded:
            return True
        if not (self.title or self.subtitle):
            return False
        return self.hovered or self.tick < self.card_until

    def card_rect(self) -> QRect:
        if self.expanded:
            w = CARD_MAX_W
            return QRect((self.width() - w) // 2, self.panel_top(),
                         w, self.panel_height())
        title, sub = self.card_lines()
        tw = QFontMetrics(self.title_font()).horizontalAdvance(title)
        sw = QFontMetrics(self.sub_font()).horizontalAdvance(sub) if sub else 0
        w = min(CARD_MAX_W, max(150, max(tw, sw) + 26 + 14))
        h = 44 if sub else 30
        return QRect((self.width() - w) // 2, self.panel_top(), w, h)

    def toggle_rect(self) -> QRect:
        """The hover button: shows how many sessions are live, expands the list."""
        cy = PET_TOP + self.pet_h + CTRL_GAP + CTRL_H // 2
        return QRect(self.width() // 2 - BTN_R, cy - BTN_R, BTN_R * 2, BTN_R * 2)

    def showing_controls(self) -> bool:
        return self.hovered or self.expanded

    # ------------------------------------------------------------- painting --
    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        pm = self.current_pixmap()
        p.drawPixmap((self.width() - pm.width()) // 2,
                     PET_TOP + (self.pet_h - pm.height()), pm)
        if self.showing_card():
            if self.expanded:
                self.paint_task_list(p)
            else:
                self.paint_card(p)
        if self.showing_controls():
            self.paint_controls(p)

    def paint_controls(self, p: QPainter) -> None:
        """One round button under the pet: the live session count, or a chevron
        when the list is already open."""
        r = self.toggle_rect()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QColor(255, 255, 255, 46))
        p.setBrush(QColor(30, 36, 46, 236))
        p.drawEllipse(r)
        p.setPen(QColor(235, 240, 246))
        if self.expanded:
            cx, cy = r.center().x(), r.center().y()
            pen = p.pen(); pen.setWidth(2); p.setPen(pen)
            p.drawLine(cx - 4, cy - 2, cx, cy + 2)
            p.drawLine(cx, cy + 2, cx + 4, cy - 2)
        else:
            f = QFont(); f.setPointSizeF(10.5); f.setWeight(QFont.Weight.Bold)
            p.setFont(f)
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, str(len(self.session_list())))

    def paint_task_list(self, p: QPainter) -> None:
        """One row per live Claude session."""
        rect = self.card_rect()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QColor(255, 255, 255, 38))
        p.setBrush(QColor(17, 22, 30, 236))
        p.drawRoundedRect(rect, 13, 13)
        fm_t, fm_s = QFontMetrics(self.title_font()), QFontMetrics(self.sub_font())
        items = self.session_list() or [("", {"state": "idle", "title": "No sessions",
                                          "subtitle": "", "pane": None})]
        self.rows = []
        y = rect.top() + 10
        for sid, t in items:
            row_rect = QRect(rect.left() + 5, y, rect.width() - 10, ROW_H - 2)
            self.rows.append((row_rect, sid, t))
            if t.get("pane") and row_rect.contains(self.mapFromGlobal(QCursor.pos())):
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 16))      # hover highlight
                p.drawRoundedRect(row_rect, 8, 8)
            dot = QColor(DOT.get(t.get("state", "idle"), "#94a3b8"))
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(dot)
            p.drawEllipse(rect.left() + 13, y + 11, 7, 7)
            body_x, body_w = rect.left() + 28, rect.width() - 40
            p.setFont(self.title_font())
            p.setPen(QColor(240, 243, 247) if t.get("pane") else QColor(176, 184, 196))
            p.drawText(QRect(body_x, y + 2, body_w - 14, fm_t.height()),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       fm_t.elidedText(t.get("title") or "Working",
                                       Qt.TextElideMode.ElideRight, body_w - 14))
            p.setFont(self.sub_font()); p.setPen(QColor(150, 160, 175))
            sub = t.get("subtitle") or ("" if t.get("pane") else "background job")
            p.drawText(QRect(body_x, y + 2 + fm_t.height(), body_w, fm_s.height()),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       fm_s.elidedText(sub, Qt.TextElideMode.ElideRight, body_w))
            if t.get("pane"):                              # a chevron: this one opens
                p.setPen(QColor(120, 132, 148))
                pen = p.pen(); pen.setWidth(2); p.setPen(pen)
                ax, ay = rect.right() - 16, y + ROW_H // 2 - 1
                p.drawLine(ax - 3, ay - 4, ax + 1, ay)
                p.drawLine(ax + 1, ay, ax - 3, ay + 4)
            y += ROW_H

    def paint_card(self, p: QPainter) -> None:
        """A task card under the pet: what he is doing, and the step in flight."""
        rect = self.card_rect()
        title, sub = self.card_lines()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QColor(255, 255, 255, 38))
        p.setBrush(QColor(17, 22, 30, 232))
        p.drawRoundedRect(rect, 13, 13)

        dot = QColor(DOT.get(self.state, "#94a3b8"))
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(dot)
        p.drawEllipse(rect.left() + 13, rect.top() + (16 if sub else 12), 7, 7)

        body = rect.adjusted(28, 8, -12, -8)
        fm_t = QFontMetrics(self.title_font())
        p.setFont(self.title_font()); p.setPen(QColor(240, 243, 247))
        p.drawText(QRect(body.left(), body.top(), body.width(), fm_t.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   fm_t.elidedText(title, Qt.TextElideMode.ElideRight, body.width()))
        if sub:
            fm_s = QFontMetrics(self.sub_font())
            p.setFont(self.sub_font()); p.setPen(QColor(150, 160, 175))
            p.drawText(QRect(body.left(), body.top() + fm_t.height() + 1,
                             body.width(), fm_s.height()),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       fm_s.elidedText(sub, Qt.TextElideMode.ElideRight, body.width()))

    # ---------------------------------------------------------------- input --
    def enterEvent(self, event) -> None:  # noqa: N802
        if not self.hovered:
            self.hovered = True
            self.apply_mask()             # the control row is outside the idle mask
            self.play_once("jump")        # a hop hello, then he waves
        event.accept()

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self.hovered:
            self.hovered = False
            self.card_until = self.tick + CARD_LINGER_MS   # long enough to read
            if self.oneshot in ("jump", "wave"):
                self.oneshot = None
            self.apply_mask()
            self.update()
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position().toPoint()
        if self.showing_controls() and self.toggle_rect().contains(pos):
            self._btn_armed = True          # a button press, not the start of a drag
            event.accept()
            return
        if self.expanded and any(r.contains(pos) and t.get("pane")
                                 for r, _, t in self.rows):
            self._btn_armed = True
            event.accept()
            return
        self.drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self.drag_origin is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        target = event.globalPosition().toPoint() - self.drag_origin
        dx = target.x() - self.x()
        self.move(target)
        self.desired_pos = (self.x(), self.y())
        if abs(dx) >= 1:
            self.facing = 1 if dx > 0 else -1
            dt = max(1, self.tick - self.last_drag_t)
            self.fast = abs(dx) * 1000.0 / dt >= RUN_SPEED
            self.last_drag_t = self.tick
            if not self.moving:
                self.moving = True
                self.oneshot = None       # dragging beats a hop mid-air
                self.frame = 0
            self.move_timer.start(MOVE_STOP_MS)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._btn_armed:
            self._btn_armed = False
            pos = event.position().toPoint()
            if self.toggle_rect().contains(pos):
                self.expanded = not self.expanded
                self.relayout()
            else:
                for r, sid, t in self.rows:
                    if r.contains(pos) and t.get("pane") and herdr_link:
                        threading.Thread(target=herdr_link.focus, args=(sid,),
                                         daemon=True).start()
                        break
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_origin = None
            self.desired_pos = (self.x(), self.y())
            self.move_timer.start(MOVE_STOP_MS)
            self.save_config()
            event.accept()

    def stop_moving(self) -> None:
        if self.moving:
            self.moving = False
            self.fast = False
            self.frame = 0
            self.update()

    def wheelEvent(self, event) -> None:  # noqa: N802
        self.set_height(self.pet_h + (8 if event.angleDelta().y() > 0 else -8))
        event.accept()

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = QMenu(self)
        for label, h in (("Small", 96), ("Medium", 180), ("Large", 260), ("Huge", 340)):
            act = QAction(f"{label}  ({h}px)", self)
            act.setCheckable(True); act.setChecked(abs(self.pet_h - h) < 5)
            act.triggered.connect(lambda _, v=h: self.set_height(v))
            menu.addAction(act)
        menu.addSeparator()
        for label, name in (("Wave", "wave"), ("Jump", "jump"), ("Celebrate", "celebrate")):
            if name in self.anims:
                act = QAction(f"Make him {label.lower()}", self)
                act.triggered.connect(lambda _, n=name: self.play_once(n))
                menu.addAction(act)
        menu.addSeparator()
        flip = QAction("Reverse walking direction", self)
        flip.setCheckable(True); flip.setChecked(self.invert_facing)
        flip.triggered.connect(self.flip)
        menu.addAction(flip)
        always = QAction("Stay visible without a session", self)
        always.setCheckable(True); always.setChecked(self.always)
        always.triggered.connect(self.toggle_always)
        menu.addAction(always)
        menu.addSeparator()
        hide = QAction("Hide pet", self); hide.triggered.connect(self.hide_pet)
        menu.addAction(hide)
        quit_act = QAction("Quit", self); quit_act.triggered.connect(QApplication.quit)
        menu.addAction(quit_act)
        menu.exec(event.globalPos())

    def flip(self) -> None:
        self.invert_facing = not self.invert_facing
        self.save_config()
        self.update()

    def toggle_always(self) -> None:
        self.always = not self.always
        self.save_config(); self.apply_visibility()

    def hide_pet(self) -> None:
        self.enabled = False
        self.save_config(); self.apply_visibility()

    # --------------------------------------------------------------- sizing --
    def set_height(self, h: int) -> None:
        h = max(MIN_H, min(MAX_H, int(h)))
        if h == self.pet_h:
            return
        centre = self.geometry().center()
        self.pet_h = h
        self._scale_cache.clear()
        self.resize_to_pet()
        self.place(centre.x() - self.width() // 2, centre.y() - self.height() // 2)
        self.save_config(); self.publish_runtime(); self.update()

    # ------------------------------------------------------------- position --
    def cursor_screen(self):
        return (QApplication.screenAt(QCursor.pos())
                or self.screen() or QApplication.primaryScreen())

    def window_screen(self):
        return (QApplication.screenAt(self.geometry().center())
                or self.screen() or QApplication.primaryScreen())

    def place(self, x: int, y: int, screen=None) -> None:
        self.move(int(x), int(y))
        self.clamp_to_screen(screen)
        self.desired_pos = (self.x(), self.y())

    def hold_position(self) -> None:
        """Qt re-anchors frameless tool windows when they cross displays with a
        different devicePixelRatio - the pet lands on another monitor a frame
        later. Put him back unless the user is the one moving him."""
        if self.desired_pos is None or self.drag_origin is not None:
            return
        if (self.x(), self.y()) != self.desired_pos:
            self.move(*self.desired_pos)

    def center_on_screen(self, index=None) -> None:
        screens = QApplication.screens()
        if isinstance(index, int) and 0 <= index < len(screens):
            screen = screens[index]
        else:
            screen = self.cursor_screen()
        if screen:
            a = screen.availableGeometry()
            self.place(a.center().x() - self.width() // 2,
                       a.center().y() - self.height() // 2, screen)
        self.raise_(); self.save_config(); self.publish_runtime()

    def clamp_to_screen(self, screen=None) -> None:
        screen = screen or self.window_screen()
        if not screen:
            return
        a = screen.availableGeometry()
        self.move(max(a.left(), min(self.x(), a.right() - self.width() + 1)),
                  max(a.top(), min(self.y(), a.bottom() - self.height() + 1)))

    def restore_position(self, cfg: dict) -> None:
        x, y = cfg.get("x"), cfg.get("y")
        if x is None or y is None:
            screen = self.cursor_screen()
            a = screen.availableGeometry() if screen else QRect(0, 0, 1280, 800)
            self.place(a.right() - self.width() - 28, a.bottom() - self.height() - 28, screen)
            return
        self.move(int(x), int(y))
        if QApplication.screenAt(self.geometry().center()) is None:
            self.clamp_to_screen(self.cursor_screen())
        self.desired_pos = (self.x(), self.y())

    def save_config(self) -> None:
        write_json(self.config_path, {
            "height": self.pet_h, "x": self.x(), "y": self.y(),
            "pet": self.pet_id,
            "enabled": self.enabled, "always": self.always,
            "invert_facing": self.invert_facing,
            "recenter": False, "recenter_screen": None,
        })

    # ----------------------------------------------------------------- poll --
    def reload(self, force: bool = False) -> None:
        try:
            sm = self.state_path.stat().st_mtime_ns
        except OSError:
            sm = None
        if force or sm != self._state_mtime:
            self._state_mtime = sm
            data = read_json(self.state_path)
            state = data.get("state", "idle")
            state = state if state in STATES else "idle"
            entered = state != self.state
            self.state = state
            self.message = str(data.get("message") or DEFAULT_MSG.get(state, ""))[:160]
            new_title = str(data.get("title") or "")[:160]
            new_sub = str(data.get("subtitle") or "")[:160]
            if (new_title, new_sub) != (self.title, self.subtitle) or force:
                self.title, self.subtitle = new_title, new_sub
                self._mask_cache = None       # a new line is a new card width
            self.sessions = int(data.get("sessions", 0) or 0)
            tasks = data.get("tasks")
            new_tasks = tasks if isinstance(tasks, dict) else {}
            if len(new_tasks) != len(self.tasks):
                self._mask_cache = None
                if self.expanded:
                    QTimer.singleShot(0, self.relayout)
            self.tasks = new_tasks
            if entered and not force and state in STATE_ENTER:
                self.play_once(STATE_ENTER[state])
            self.apply_mask()

        try:
            cm = self.config_path.stat().st_mtime_ns
        except OSError:
            cm = None
        if force or cm != self._config_mtime:
            self._config_mtime = cm
            cfg = read_json(self.config_path)
            self.enabled = bool(cfg.get("enabled", self.enabled))
            self.always = bool(cfg.get("always", self.always))
            self.invert_facing = bool(cfg.get("invert_facing", self.invert_facing))
            h = int(cfg.get("height", self.pet_h))
            if h != self.pet_h:
                self.pet_h = max(MIN_H, min(MAX_H, h))
                self._scale_cache.clear()
                self.resize_to_pet()
                self.clamp_to_screen()
                self.desired_pos = (self.x(), self.y())
            if cfg.get("recenter"):
                self.center_on_screen(cfg.get("recenter_screen"))
            wanted = str(cfg.get("pet") or self.pet_id)
            if wanted != self.pet_id:
                self.switch_pet(wanted)
            play_id = cfg.get("play_id")
            if play_id and play_id != self._last_play_id:
                self._last_play_id = play_id
                self.play_once(str(cfg.get("play", "")))
        self.apply_visibility()

    def apply_visibility(self) -> None:
        should = self.enabled and (self.always or self.sessions > 0)
        if should:
            self._hide_pending = False
            if not self.isVisible():
                self.show()
                self.play_once("turn_in")     # spins in rather than popping
        elif self.isVisible() and not self._hide_pending:
            self._hide_pending = True
            self.play_once("turn_out")        # spins away, then hides
        self.publish_runtime()

    def publish_runtime(self) -> None:
        screens = []
        for i, sc in enumerate(QApplication.screens()):
            g = sc.geometry()
            screens.append({"index": i, "name": sc.name(),
                            "geometry": f"{g.width()}x{g.height()} at {g.x()},{g.y()}",
                            "has_pet": sc is self.window_screen()})
        snapshot = {"pid": os.getpid(),
                    "pet": self.pet_id,
                    "petName": self.pet_meta.get("displayName", self.pet_id),
                    "available": available_pets(),
                    "on_screen": self.isVisible(),
                    "height": self.pet_h, "screens": screens,
                    "x": self.x(), "y": self.y(), "state": self.state,
                    "animation": self.playing, "moving": self.moving,
                    "hovered": self.hovered,
                    "facing": "right" if self.facing > 0 else "left"}
        if snapshot != getattr(self, "_last_runtime", None):
            self._last_runtime = snapshot
            write_json(self.home / "runtime.json", snapshot)


def acquire_single_instance():
    """Hold an exclusive lock so only one overlay ever runs.

    Both the LaunchAgent and `bin/pet` can start the overlay; without this they
    race and you end up with two pets fighting over position and state.
    Returns the lock file handle, which must stay open for the process lifetime.
    """
    import fcntl
    home = pet_home()
    home.mkdir(parents=True, exist_ok=True)
    handle = open(home / "overlay.lock", "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def main() -> int:
    lock = acquire_single_instance()
    if lock is None:
        print("another pet overlay is already running", file=sys.stderr)
        return 0
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    pet = Pet()
    pet.apply_visibility()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
