"""Decide where quest markers go on the minimap (pure geometry, no Qt)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

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


def assign_colors(names: list[str]) -> dict[str, str]:
    """A distinct colour per active quest, stable while the same quests stay active."""
    import zlib

    out: dict[str, str] = {}
    used: set[int] = set()
    for name in sorted(set(names)):
        i = zlib.crc32(name.encode()) % len(QUEST_PALETTE)
        for step in range(len(QUEST_PALETTE)):
            j = (i + step) % len(QUEST_PALETTE)
            if j not in used:
                i = j
                break
        used.add(i)
        out[name] = QUEST_PALETTE[i]
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
