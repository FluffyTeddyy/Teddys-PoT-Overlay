"""Transparent, click-through, always-on-top windows: one over the minimap, one over the in-game map."""
from __future__ import annotations

import math
import time

import numpy as np

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from .markers import Marker, OverlayState
from .winutil import exclude_from_capture

PAD = 48  # space around the minimap for edge arrows, labels and status text
LEGEND_W = 200  # extra space on the left for the quest legend

_FLAGS = (Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
          | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)


def draw_text(p: QPainter, pos: QPointF, text: str, color: QColor) -> None:
    path = QPainterPath()
    path.addText(pos, p.font(), text)
    p.strokePath(path, QPen(QColor(0, 0, 0, 220), 3))
    p.fillPath(path, color)


def draw_marker(p: QPainter, x: float, y: float, m: Marker, r: float, default_color: str, label: bool,
                alpha: int = 255) -> None:
    col = QColor(m.color) if QColor.isValidColorName(m.color) else QColor(default_color)
    col.setAlpha(alpha)
    p.setPen(QPen(QColor(0, 0, 0, min(230, alpha)), 2))
    p.setBrush(col)
    if m.style == "fish":
        # A little fish: oval body, tail to the right. Never mistaken for a quest dot.
        p.setPen(QPen(QColor(0, 0, 0, min(230, alpha)), 1.5))
        p.drawEllipse(QPointF(x - r * 0.25, y), r * 0.9, r * 0.55)
        p.drawPolygon(QPolygonF([QPointF(x + r * 0.45, y), QPointF(x + r * 1.15, y - r * 0.55),
                                 QPointF(x + r * 1.15, y + r * 0.55)]))
        return
    if m.style:  # food layers: a small shape per food group, outlined thinly
        p.setPen(QPen(QColor(0, 0, 0, min(230, alpha)), 1.5))
        s = r * 0.8
        if m.style == "diamond":
            p.drawPolygon(QPolygonF([QPointF(x, y - s), QPointF(x + s, y), QPointF(x, y + s), QPointF(x - s, y)]))
        elif m.style == "triangle":
            p.drawPolygon(QPolygonF([QPointF(x, y - s), QPointF(x + s, y + s * 0.8), QPointF(x - s, y + s * 0.8)]))
        elif m.style == "square":
            p.drawRect(QRectF(x - s * 0.75, y - s * 0.75, s * 1.5, s * 1.5))
        elif m.style == "mushroom":
            path = QPainterPath()  # cap (half disc) on a short stem
            path.moveTo(x - s, y)
            path.arcTo(QRectF(x - s, y - s, 2 * s, 2 * s), 180, -180)
            path.closeSubpath()
            p.drawRect(QRectF(x - s * 0.3, y, s * 0.6, s * 0.7))
            p.drawPath(path)
        elif m.style == "leaf":
            path = QPainterPath()
            path.moveTo(x - s, y + s * 0.6)
            path.quadTo(x - s * 0.6, y - s, x + s, y - s * 0.6)
            path.quadTo(x + s * 0.6, y + s, x - s, y + s * 0.6)
            p.drawPath(path)
        else:  # "dot"
            p.drawEllipse(QPointF(x, y), s * 0.7, s * 0.7)
        return
    if m.inside and m.ring:
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(x, y), r * 1.7, r * 1.7)
        p.setPen(QPen(col, 2.5))
        p.drawEllipse(QPointF(x, y), r * 1.7, r * 1.7)
    elif m.inside:
        p.drawEllipse(QPointF(x, y), r, r)
    else:
        ca, sa = math.cos(m.angle), math.sin(m.angle)
        tip = QPointF(x + ca * r * 1.8, y + sa * r * 1.8)
        left = QPointF(x - sa * r, y + ca * r)
        right = QPointF(x + sa * r, y - ca * r)
        p.drawPolygon(QPolygonF([tip, left, right]))
    if label and m.inside:
        draw_text(p, QPointF(x + r + 3, y + 4), m.label, col)


ZONE_FILL = QColor(40, 150, 255, 85)
ZONE_EDGE = QColor(130, 210, 255, 190)


def draw_zones(p: QPainter, zones: list, dx: float = 0.0, dy: float = 0.0, clip: QPainterPath | None = None) -> None:
    """Fresh water: translucent blue areas with a light edge, under everything else."""
    if not zones:
        return
    path = QPainterPath()
    path.setFillRule(Qt.OddEvenFill)  # islands in a lake stay unfilled
    for z in zones:
        path.addPolygon(QPolygonF([QPointF(float(x) + dx, float(y) + dy) for x, y in z]))
        path.closeSubpath()
    p.save()
    if clip is not None:
        p.setClipPath(clip)
    p.setPen(QPen(ZONE_EDGE, 1.2))
    p.setBrush(ZONE_FILL)
    p.drawPath(path)
    p.restore()


def legend_icon(p: QPainter, x: float, y: float, entry: tuple, r: float) -> None:
    """Legend entries are (name, colour) for quests or (name, colour, style) for layers."""
    style = entry[2] if len(entry) > 2 else ""
    if style == "zone":  # a little patch of fresh water
        p.setPen(QPen(ZONE_EDGE, 1.2))
        p.setBrush(QColor(40, 150, 255, 150))
        p.drawRoundedRect(QRectF(x - r, y - r * 0.7, 2 * r, 1.4 * r), 3, 3)
        return
    draw_marker(p, x, y, Marker(x, y, entry[1], entry[0], True, style=style), r, entry[1], False)


class _ClickThroughWindow(QWidget):
    def __init__(self):
        super().__init__(None, _FLAGS)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

    def showEvent(self, event):
        super().showEvent(event)
        # Hide our markers from screen capture, so they never confuse the matcher (or show in OBS).
        exclude_from_capture(int(self.winId()))


class Overlay(_ClickThroughWindow):
    """Sits over the minimap."""

    def __init__(self, cfg: dict, monitor: dict):
        super().__init__()
        self.cfg = cfg
        self.state = OverlayState()
        self.user_hidden = False
        self.notes: list[tuple] = []  # fish buff lines, under the legend
        self._legend_rows = 0
        self.apply_geometry(cfg, monitor)

    def _left_width(self) -> int:
        """Room left of the minimap: the legend's column, wider if a fish buff line needs it."""
        w = LEGEND_W if self.cfg.get("show_legend") else 0
        if self.notes:
            fm = QFontMetrics(QFont("Segoe UI", 9, QFont.DemiBold))
            w = max(w, max(fm.horizontalAdvance(n[0]) for n in self.notes) + 40)
        return w

    def apply_geometry(self, cfg: dict, monitor: dict) -> None:
        self.cfg, self.monitor = cfg, monitor
        mm = cfg["minimap"]
        self.mm_w, self.mm_h = int(mm["w"]), int(mm["h"])
        left = self._left_width()
        # (ox, oy): where the minimap's top-left corner is inside this window.
        self.ox, self.oy = PAD + left, PAD
        rows = self._legend_rows + len(self.notes)
        self.setGeometry(
            monitor["left"] + int(mm["x"]) - self.ox,
            monitor["top"] + int(mm["y"]) - self.oy,
            self.mm_w + 2 * PAD + left,
            max(self.mm_h + 2 * PAD + 24, self.oy + 18 * (rows + 1) + 16),  # taller for a long list
        )
        self.update()

    def set_notes(self, notes: list[tuple]) -> None:
        """Fish buff lines: (text, colour, style)."""
        self.notes = list(notes)
        self.apply_geometry(self.cfg, self.monitor)

    def set_state(self, state: OverlayState) -> None:
        self.state = state
        rows = len(state.legend) if self.cfg.get("show_legend") else 0
        if rows != self._legend_rows:
            self._legend_rows = rows
            self.apply_geometry(self.cfg, self.monitor)
        self.update()

    def flash(self, text: str, ms: int = 3000) -> None:
        """Show a short message under the minimap (e.g. "quests refreshed") for a few seconds."""
        self._flash = (text, time.monotonic() + ms / 1000)
        self.update()
        QTimer.singleShot(ms + 50, self.update)

    def paintEvent(self, event):
        st = self.state
        if self.user_hidden or not st.visible:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = float(self.cfg["marker_radius"])
        p.setFont(QFont("Segoe UI", 8, QFont.DemiBold))

        if st.zones:  # fresh water, kept inside the round minimap
            clip = QPainterPath()
            if self.cfg["minimap"].get("shape", "circle") == "circle":
                clip.addEllipse(QRectF(self.ox, self.oy, self.mm_w, self.mm_h))
            else:
                clip.addRect(QRectF(self.ox, self.oy, self.mm_w, self.mm_h))
            draw_zones(p, st.zones, self.ox, self.oy, clip)

        # Always-on layers (fish...) underneath, a bit smaller, so quest markers stay on top.
        for m in st.markers:
            if m.style:
                draw_marker(p, m.x + self.ox, m.y + self.oy, m, r * 0.9, self.cfg["default_marker_color"],
                            False, alpha=225)
        for m in st.markers:
            if not m.style:
                draw_marker(p, m.x + self.ox, m.y + self.oy, m, r, self.cfg["default_marker_color"],
                            self.cfg["show_labels"])

        flash = getattr(self, "_flash", None)
        if flash and time.monotonic() < flash[1]:
            draw_text(p, QPointF(self.ox, self.oy + self.mm_h + 16), flash[0], QColor(255, 220, 90))
        elif self.cfg["show_status"] and st.status:
            colr = QColor(120, 230, 120) if st.tracking else QColor(200, 200, 200)
            draw_text(p, QPointF(self.ox, self.oy + self.mm_h + 16), st.status, colr)

        legend = list(st.legend) if self.cfg.get("show_legend") else []
        if legend or self.notes:
            p.setFont(QFont("Segoe UI", 9, QFont.DemiBold))
            fm = p.fontMetrics()
            right = self.ox - 10  # right-aligned against the minimap's left edge
            y = self.oy + 18
            for entry in legend:
                text = fm.elidedText(entry[0], Qt.ElideRight, LEGEND_W - 30)
                tw = fm.horizontalAdvance(text)
                draw_text(p, QPointF(right - tw, y + 4), text, QColor(235, 235, 235))
                legend_icon(p, right - tw - 10, y, entry, r * 0.9)
                y += 18
            if legend and self.notes:
                y += 6
            for entry in self.notes:  # fish buffs: "Pike - Stamina recovery up"
                text = fm.elidedText(entry[0], Qt.ElideRight, self.ox - PAD - 30)
                tw = fm.horizontalAdvance(text)
                draw_text(p, QPointF(right - tw, y + 4), text, QColor(170, 225, 255))
                legend_icon(p, right - tw - 14, y, entry, r * 0.75)
                y += 18
        p.end()


class MapScreenOverlay(_ClickThroughWindow):
    """Covers the whole monitor while the in-game map is open. Hidden the rest of the time, so a
    full-screen window never sits over the game during normal play."""

    def __init__(self, cfg: dict, monitor: dict):
        super().__init__()
        self.cfg = cfg
        self.state = OverlayState()
        self.user_hidden = False
        self.notes: list[tuple] = []  # fish buff lines, added to the legend box
        self.apply_geometry(cfg, monitor)

    def apply_geometry(self, cfg: dict, monitor: dict) -> None:
        self.cfg = cfg
        self.setGeometry(monitor["left"], monitor["top"], monitor["width"], monitor["height"])

    def set_notes(self, notes: list[tuple]) -> None:
        self.notes = list(notes)
        if self.isVisible():
            self.update()

    def set_state(self, state: OverlayState) -> None:
        # The map often sits still while it's open: if no marker moved a pixel or more since the
        # last repaint, skip repainting. With lots of food shown that's thousands of shapes saved.
        kinds = (state.visible, tuple(state.legend), tuple((m.color, m.style, m.ring) for m in state.markers),
                 tuple(len(z) for z in state.zones), state.clip is None)
        # Zones move with the map as a whole: one corner of each tells whether they moved.
        pos = np.array([(m.x, m.y) for m in state.markers] + [tuple(z[0]) for z in state.zones]
                       + [state.legend_pos or (0, 0)]
                       + ([tuple(state.clip[:2]), tuple(state.clip[2:])] if state.clip else [(0, 0), (0, 0)]),
                       np.float64)
        last = getattr(self, "_painted", None)
        changed = (last is None or last[0] != kinds or last[1].shape != pos.shape
                   or float(np.max(np.abs(last[1] - pos))) >= 1.0)
        if changed:
            self._painted = (kinds, pos)
        self.state = state
        show = state.visible and not self.user_hidden
        if show and not self.isVisible():
            self.show()
        elif not show and self.isVisible():
            self.hide()
        if changed:
            self.update()

    def paintEvent(self, event):
        st = self.state
        if self.user_hidden or not st.visible:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = float(self.cfg["marker_radius"]) + 1
        p.setFont(QFont("Segoe UI", 9, QFont.DemiBold))
        if st.zones:  # fresh water, kept inside the map window
            clip = None
            if st.clip:
                clip = QPainterPath()
                x0, y0, x1, y1 = st.clip
                clip.addRect(QRectF(x0, y0, x1 - x0, y1 - y0))
            draw_zones(p, st.zones, clip=clip)
        # Always-on layers (fish...) first, smaller and fainter: there can be hundreds on the big map.
        for m in st.markers:
            if m.style:
                draw_marker(p, m.x, m.y, m, r * 0.8, self.cfg["default_marker_color"], False, alpha=175)
        for m in st.markers:
            if not m.style:
                draw_marker(p, m.x, m.y, m, r, self.cfg["default_marker_color"], self.cfg["show_labels"])
        legend = list(st.legend) if self.cfg.get("show_legend") else []
        if legend or self.notes:
            # Small boxed legend in the map window's top-left corner; fish buffs at the bottom.
            fm = p.fontMetrics()
            gap = 6 if legend and self.notes else 0
            box_w = max(fm.horizontalAdvance(e[0]) for e in legend + self.notes) + 44
            box_h = 18 * (len(legend) + len(self.notes)) + 12 + gap
            x0, y0 = st.legend_pos or (24, self.height() - box_h - 24)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 150))
            p.drawRoundedRect(QRectF(x0, y0, box_w, box_h), 6, 6)
            y = y0 + 16
            for entry in legend:
                legend_icon(p, x0 + 14, y - 4, entry, r * 0.8)
                draw_text(p, QPointF(x0 + 26, y), entry[0], QColor(235, 235, 235))
                y += 18
            y += gap
            for entry in self.notes:
                legend_icon(p, x0 + 12, y - 4, entry, r * 0.7)
                draw_text(p, QPointF(x0 + 26, y), entry[0], QColor(170, 225, 255))
                y += 18
        p.end()
