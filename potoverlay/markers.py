"""Decide where quest markers go on the minimap (pure geometry, no Qt)."""
from __future__ import annotations

import colorsys
import json
import math
from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class Marker:
    x: float  # minimap-local pixels
    y: float
    color: str
    label: str
    inside: bool  # False = edge arrow pointing toward an off-minimap target
    angle: float = 0.0  # radians, direction of the arrow
    ring: bool = False  # a place to go (waystone, drop-off, POI) rather than an item
    style: str = ""     # "" quest marker; "fish" / "diamond" for always-on layers (fish spawns...)


@dataclass
class OverlayState:
    visible: bool = False
    tracking: bool = False
    markers: list[Marker] = field(default_factory=list)
    status: str = ""
    legend: list[tuple[str, str]] = field(default_factory=list)  # (quest name, colour)
    legend_pos: tuple[float, float] | None = None  # top-left corner for the legend (map screen)
    # Fresh water zones: outlines (Nx2 arrays, same coordinates as the markers), filled even-odd,
    # and the rectangle to keep them in (map screen: the map window).
    zones: list = field(default_factory=list)
    clip: tuple[float, float, float, float] | None = None


QUEST_PALETTE = ["#ffd23f", "#3fd2ff", "#ff6b6b", "#7cff6b", "#c77dff", "#ff9f40", "#ff5fd2", "#ffffff"]


_ITEM_COLORS: dict[str, str] | None = None


def item_color(kind: str) -> str:
    """An item's own colour (from its picture: pinecones green, berries red), "" if unknown.
    Built by tools/build_item_colors.py into data/item_colors.json."""
    global _ITEM_COLORS
    if _ITEM_COLORS is None:
        from . import config as C
        try:
            _ITEM_COLORS = json.loads((C.DATA_DIR / "item_colors.json").read_text(encoding="utf-8"))["colors"]
        except (OSError, ValueError, KeyError):
            _ITEM_COLORS = {}
    return _ITEM_COLORS.get(kind, "")


def _rgb(hexcolor: str) -> tuple[float, float, float]:
    h = hexcolor.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _lab(hexcolor: str) -> np.ndarray:
    px = np.array([[_rgb(hexcolor)[::-1]]], np.float32)  # BGR, 0..1
    return cv2.cvtColor(px, cv2.COLOR_BGR2LAB)[0, 0]


def _hue_shift(hexcolor: str, deg: float) -> str:
    h, s, v = colorsys.rgb_to_hsv(*_rgb(hexcolor))
    if s < 0.15:  # grey/white: change brightness instead of hue
        v = min(1.0, max(0.55, v - deg / 120))
    r, g, b = colorsys.hsv_to_rgb((h + deg / 360) % 1.0, s, v)
    return "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))


MIN_COLOR_GAP = 22.0  # Lab distance: two markers closer than this in colour are hard to tell apart


def assign_colors(names: list[str], preferred: dict[str, str] | None = None) -> dict[str, str]:
    """A colour per active quest entry, stable while the same ones stay active. Entries for an
    item get the item's own colour (`preferred`: name -> colour); if two of those look alike
    (death caps and pinecones are both green), the later one is nudged a little in hue until they
    can be told apart. Everything else gets a palette colour unlike the ones in use."""
    import zlib

    preferred = {n: c for n, c in (preferred or {}).items() if c}
    out: dict[str, str] = {}
    labs: list[np.ndarray] = []

    def free(color: str) -> bool:
        lab = _lab(color)
        return all(float(np.linalg.norm(lab - o)) >= MIN_COLOR_GAP for o in labs)

    for name in sorted(set(names) & set(preferred)):
        base = preferred[name]
        color = next((c for c in (_hue_shift(base, d) for d in (0, 18, -18, 32, -32, 48, -48, 64, -64))
                      if free(c)), base)
        out[name] = color
        labs.append(_lab(color))
    for name in sorted(set(names) - set(preferred)):
        i = zlib.crc32(name.encode()) % len(QUEST_PALETTE)
        order = [QUEST_PALETTE[(i + s) % len(QUEST_PALETTE)] for s in range(len(QUEST_PALETTE))]
        color = next((c for c in order if c not in out.values() and free(c)),
                     next((c for c in order if c not in out.values()), order[0]))
        out[name] = color
        labs.append(_lab(color))
    return out


def _contains(x: float, y: float, w: int, h: int, shape: str, margin: float) -> bool:
    if shape == "circle":
        r = min(w, h) / 2.0 - margin
        return (x - w / 2.0) ** 2 + (y - h / 2.0) ** 2 <= r * r
    return margin <= x <= w - margin and margin <= y <= h - margin


def _edge_point(dx: float, dy: float, w: int, h: int, shape: str, margin: float) -> tuple[float, float]:
    cx, cy = w / 2.0, h / 2.0
    d = math.hypot(dx, dy) or 1.0
    if shape == "circle":
        r = min(w, h) / 2.0 - margin
        return cx + dx / d * r, cy + dy / d * r
    hw, hh = w / 2.0 - margin, h / 2.0 - margin
    t = min(hw / abs(dx) if dx else math.inf, hh / abs(dy) if dy else math.inf)
    return cx + dx * t, cy + dy * t


def layout_layer(points, w: int, h: int, shape: str, margin: float, label: str, color: str,
                 style: str) -> list[Marker]:
    """Markers for an always-on layer (e.g. fish spawns): the points inside the minimap only, no
    edge arrows (there are hundreds of spawns around the map)."""
    pts = np.asarray(points, np.float64).reshape(-1, 2)
    if len(pts) == 0:
        return []
    if shape == "circle":
        r = min(w, h) / 2.0 - margin
        inside = (pts[:, 0] - w / 2.0) ** 2 + (pts[:, 1] - h / 2.0) ** 2 <= r * r
    else:
        inside = ((pts[:, 0] >= margin) & (pts[:, 0] <= w - margin)
                  & (pts[:, 1] >= margin) & (pts[:, 1] <= h - margin))
    return [Marker(float(x), float(y), color, label, True, style=style) for x, y in pts[inside]]


def layout_markers(
    quests: list[tuple],
    w: int,
    h: int,
    shape: str,
    marker_radius: float,
    arrows: bool,
) -> list[Marker]:
    """quests: (name, color, Nx2 item points[, Mx2 target points]) in minimap pixels.
    Points inside the minimap become dots (targets become rings); a quest with nothing
    visible gets one edge arrow toward its nearest point."""
    out: list[Marker] = []
    margin = marker_radius + 1
    cx, cy = w / 2.0, h / 2.0
    for entry in quests:
        name, color, pts = entry[0], entry[1], entry[2]
        tgt = entry[3] if len(entry) > 3 and entry[3] is not None else np.zeros((0, 2))
        pts = np.zeros((0, 2)) if pts is None else np.asarray(pts, np.float64).reshape(-1, 2)
        tgt = np.asarray(tgt, np.float64).reshape(-1, 2)
        if len(pts) == 0 and len(tgt) == 0:
            continue
        visible = 0
        for arr, ring in ((pts, False), (tgt, True)):
            for x, y in arr:
                if _contains(x, y, w, h, shape, margin):
                    out.append(Marker(float(x), float(y), color, name, True, ring=ring))
                    visible += 1
        pts = np.vstack([pts, tgt])
        if not visible and arrows:
            d2 = (pts[:, 0] - cx) ** 2 + (pts[:, 1] - cy) ** 2
            tx, ty = pts[int(np.argmin(d2))]
            dx, dy = float(tx - cx), float(ty - cy)
            ex, ey = _edge_point(dx, dy, w, h, shape, margin)
            out.append(Marker(ex, ey, color, name, False, math.atan2(dy, dx)))
    return out
