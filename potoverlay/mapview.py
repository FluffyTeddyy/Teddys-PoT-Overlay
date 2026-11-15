"""Recognise the in-game map screen and place quest markers on it.

When the big map is open, the whole screen is matched against the full map image. That gives the
map screen's current pan and zoom, so markers can be drawn at the right screen positions.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .locator import Fix, MapLocator
from .markers import Marker


@dataclass
class MapView:
    fix: Fix               # screen pixels -> work-map pixels
    polygon: np.ndarray    # the map image's outline on screen (4x2), for clipping markers
    # The part of the screen showing the map (x0, y0, x1, y1). The in-game map is a window:
    # the map picture continues past its frame, so markers must be clipped to this.
    viewport: tuple[float, float, float, float] = (0, 0, 1e9, 1e9)


class MapScreenDetector:
    """Decides whether the screen shows the in-game map, and where.

    Guards against false positives:
      * the matched features must be spread over a large part of the screen (gameplay with the
        minimap visible only matches inside the small minimap),
      * at most a small share of them may lie inside the minimap's area,
      * the big map is never rotated.
    """

    def __init__(self, screen_w: int, screen_h: int, minimap_rect: tuple[int, int, int, int],
                 scale: float = 0.4, min_inliers: int = 40, max_features: int = 800,
                 min_spread: float = 0.25):
        self.w, self.h = screen_w, screen_h
        self.minimap_rect = minimap_rect
        self.scale = scale
        self.min_inliers = min_inliers
        self.max_features = max_features
        self.min_spread = min_spread
        self.reason = ""  # why the last detect() said no (for debugging)
        self._good = scale
        self._failed_once = False
        self._rot = 0
        self._viewport: tuple[float, float, float, float] | None = None
        self.viewport_margin = 12.0

    def detect(self, locator: MapLocator, screen_bgr: np.ndarray) -> MapView | None:
        # Zoomed far in, the screen is much blurrier than the stored map; shrinking the screenshot
        # brings them back to a similar detail level. Try the size that worked last time first.
        others = [s for s in (self.scale, 0.3, 0.2) if s != self._good]
        if self._failed_once:
            # Map probably not open: keep the per-frame cost down, cycling through the other sizes.
            sizes = [self._good, others[self._rot % len(others)]]
            self._rot += 1
        else:
            sizes = [self._good] + others
        for s in sizes:
            view = self._detect_at(locator, screen_bgr, s)
            if view is not None:
                self._good = s
                self._failed_once = False
                return view
        self._failed_once = True
        return None

    def _detect_at(self, locator: MapLocator, screen_bgr: np.ndarray, s: float) -> MapView | None:
        # While the map is open, only look inside its window (about half the screen): faster.
        ox = oy = 0
        img = screen_bgr
        if self._viewport is not None:
            vx0, vy0, vx1, vy1 = self._viewport
            ox, oy = max(0, int(vx0) - 16), max(0, int(vy0) - 16)
            x1, y1 = min(screen_bgr.shape[1], int(vx1) + 16), min(screen_bgr.shape[0], int(vy1) + 16)
            img = screen_bgr[oy:y1, ox:x1]
        small = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s != 1 else img
        # The first frame measures the map window from where features are found, so it uses more
        # of them (features reach closer to the window's edges); later frames can be lighter.
        n = self.max_features if self._viewport is not None else max(self.max_features, 1500)
        fix = locator.locate_view(small, n, self.min_inliers)
        if fix is None:
            self.reason = "no match"
            return None
        # Back to full screen pixels: crop = s * (screen - offset), work = A * crop + t.
        M = fix.M.copy()
        M[:, :2] *= s
        M[:, 2] -= M[:, :2] @ np.array([ox, oy], np.float64)
        src = fix.src / s + np.array([ox, oy], np.float32)
        x0, y0 = src.min(axis=0)
        x1, y1 = src.max(axis=0)
        if (x1 - x0) < self.min_spread * self.w or (y1 - y0) < self.min_spread * self.h:
            self.reason = f"matches only cover {x1 - x0:.0f}x{y1 - y0:.0f} px"
            return None
        mx, my, mw, mh = self.minimap_rect
        in_mm = ((src[:, 0] >= mx) & (src[:, 0] <= mx + mw) & (src[:, 1] >= my) & (src[:, 1] <= my + mh)).mean()
        if in_mm > 0.3:
            self.reason = f"{in_mm:.0%} of matches are on the minimap"
            return None
        angle = math.atan2(M[1, 0], M[0, 0])
        if abs(angle) > math.radians(3):
            self.reason = f"rotated {math.degrees(angle):.1f} deg"
            return None
        view_fix = Fix(M, fix.inliers, fix.matches, src)
        ww, wh = locator.work_size
        polygon = view_fix.apply_inverse([[0, 0], [ww, 0], [ww, wh], [0, wh]])
        # Matched features sit all over the visible map, so their extent (a little wider, and
        # remembered while the map stays open) is the map window's viewport.
        m = self.viewport_margin
        box = (x0 - m, y0 - m, x1 + m, y1 + m)
        if self._viewport is None:  # first frame of this session: find the window's real edges
            box = self._refine_viewport(locator, screen_bgr, view_fix, box)
        if self._viewport is not None:
            v = self._viewport
            box = (min(v[0], box[0]), min(v[1], box[1]), max(v[2], box[2]), max(v[3], box[3]))
        self._viewport = box
        self.reason = ""
        return MapView(view_fix, polygon, box)

    @staticmethod
    def _refine_viewport(locator: MapLocator, screen_bgr: np.ndarray, fix: Fix,
                         box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        """Matched features thin out near the window's edges, so their extent falls a bit short.
        Draw the map where we think it is, then push each edge outward while the screen still
        looks like that drawing; it stops at the window frame (or where the map picture ends)."""
        k = 0.5  # work at half resolution: plenty for finding an edge
        sh, sw = screen_bgr.shape[:2]
        W, H = int(sw * k), int(sh * k)
        S = np.array([[1 / k, 0, 0], [0, 1 / k, 0], [0, 0, 1]])  # small-screen px -> screen px
        M = np.vstack([fix.M, [0, 0, 1]]) @ S  # small-screen px -> work-map px
        drawn = cv2.warpAffine(locator.work_gray, M[:2], (W, H), flags=cv2.WARP_INVERSE_MAP | cv2.INTER_AREA,
                               borderMode=cv2.BORDER_CONSTANT, borderValue=0).astype(np.float32)
        seen = cv2.cvtColor(cv2.resize(screen_bgr, (W, H), interpolation=cv2.INTER_AREA),
                            cv2.COLOR_BGR2GRAY).astype(np.float32)

        def agree(a: np.ndarray, b: np.ndarray) -> float:
            if a.size == 0 or float(b.std()) < 8:  # flat (the window's dark frame): not map
                return 0.0
            a, b = a - a.mean(), b - b.mean()
            d = float(np.sqrt((a * a).sum() * (b * b).sum()))
            return float((a * b).sum() / d) if d > 1e-3 else 0.0

        x0, y0, x1, y1 = (int(max(0, box[0] * k)), int(max(0, box[1] * k)),
                          int(min(W, box[2] * k)), int(min(H, box[3] * k)))
        step, ok = 3, 0.35
        while x0 - step >= 0 and agree(drawn[y0:y1, x0 - step:x0], seen[y0:y1, x0 - step:x0]) > ok:
            x0 -= step
        while x1 + step <= W and agree(drawn[y0:y1, x1:x1 + step], seen[y0:y1, x1:x1 + step]) > ok:
            x1 += step
        while y0 - step >= 0 and agree(drawn[y0 - step:y0, x0:x1], seen[y0 - step:y0, x0:x1]) > ok:
            y0 -= step
        while y1 + step <= H and agree(drawn[y1:y1 + step, x0:x1], seen[y1:y1 + step, x0:x1]) > ok:
            y1 += step
        return (x0 / k, y0 / k, x1 / k, y1 / k)

    def reset_viewport(self) -> None:
        """Call when the map screen closes (it may open at a different size next time)."""
        self._viewport = None


class MapWindowProbe:
    """A cheap "is the map window open?" check (~1 ms instead of ~60 ms for a full match).

    The first time the map screen is found, a strip of the window's frame just above the map
    (where the "Map" title is) is remembered and saved, per screen size. After that each frame
    only compares that small strip. If it ever stops matching a map screen that was found the
    slow way (UI changed), it's learned again."""

    OPEN = 0.75   # similarity at/above this: the map window is there
    CLOSED = 0.45  # below this while in map mode: it's gone

    def __init__(self, path: Path):
        self.path = path
        self.meta_path = path.with_suffix(".json")
        self.region: dict | None = None
        self.template: np.ndarray | None = None
        try:
            if path.exists() and self.meta_path.exists():
                self.region = json.loads(self.meta_path.read_text(encoding="utf-8"))
                data = np.fromfile(str(path), np.uint8)
                self.template = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
        except Exception:
            self.region = self.template = None

    @property
    def ready(self) -> bool:
        return self.template is not None and self.region is not None

    def score(self, grab) -> float | None:
        if not self.ready:
            return None
        img = cv2.cvtColor(grab.grab(self.region), cv2.COLOR_BGR2GRAY)
        if img.shape != self.template.shape:
            return None
        return float(cv2.matchTemplate(img, self.template, cv2.TM_CCOEFF_NORMED)[0, 0])

    def learn(self, screen_bgr: np.ndarray, viewport: tuple[float, float, float, float]) -> bool:
        vx0, vy0, vx1, _ = viewport
        sh, sw = screen_bgr.shape[:2]
        cx = (vx0 + vx1) / 2
        x, y, w, h = int(cx - 160), int(vy0 - 80), 320, 50
        if x < 0 or y < 0 or x + w > sw or y + h > sh:
            return False
        strip = cv2.cvtColor(screen_bgr[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY)
        if float(strip.std()) < 12:  # flat area: nothing distinctive to recognise
            return False
        self.region, self.template = {"x": x, "y": y, "w": w, "h": h}, strip
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            cv2.imencode(".png", strip)[1].tofile(str(self.path))
            self.meta_path.write_text(json.dumps(self.region), encoding="utf-8")
        except OSError:
            pass  # still works for this session
        return True


def layout_view_layer(view: MapView, locator: MapLocator, points, screen_w: int, screen_h: int,
                      label: str, color: str, style: str, margin: float = 4, cell: float = 7.0) -> list[Marker]:
    """Food layer (e.g. fish spawns) on the map screen: the points in the map window. Zoomed out,
    spots of one kind pile on top of each other; only one per `cell` px is drawn (drawing thousands
    of overlapping markers costs a lot and shows nothing more). Zoomed in, every spot is drawn."""
    pts = np.asarray(points, np.float64).reshape(-1, 2)
    if len(pts) == 0:
        return []
    scr = locator.full_to_minimap(view.fix, pts)
    vx0, vy0, vx1, vy1 = view.viewport
    x0, y0 = max(vx0, margin), max(vy0, margin)
    x1, y1 = min(vx1, screen_w - margin), min(vy1, screen_h - margin)
    scr = scr[(scr[:, 0] >= x0) & (scr[:, 0] <= x1) & (scr[:, 1] >= y0) & (scr[:, 1] <= y1)]
    if len(scr) and cell > 0:
        _, first = np.unique(np.floor(scr / cell).astype(np.int64), axis=0, return_index=True)
        scr = scr[np.sort(first)]
    return [Marker(float(x), float(y), color, label, True, style=style) for x, y in scr]


def layout_view_markers(view: MapView, locator: MapLocator, quests: list[tuple],
                        screen_w: int, screen_h: int, margin: float = 4) -> list[Marker]:
    """quests: (name, colour, full-map points, full-map targets). Returns screen-space markers for the
    points that are on the visible part of the map."""
    poly = view.polygon.astype(np.float32).reshape(-1, 1, 2)
    vx0, vy0, vx1, vy1 = view.viewport
    out: list[Marker] = []
    for name, color, pts, tgts in quests:
        for arr, ring in ((pts, False), (tgts, True)):
            if arr is None or len(arr) == 0:
                continue
            scr = locator.full_to_minimap(view.fix, arr)  # full map -> screen (same maths as the minimap)
            for x, y in scr:
                if not (margin <= x <= screen_w - margin and margin <= y <= screen_h - margin):
                    continue
                if not (vx0 <= x <= vx1 and vy0 <= y <= vy1):
                    continue
                if cv2.pointPolygonTest(poly, (float(x), float(y)), False) < 0:
                    continue
                out.append(Marker(float(x), float(y), color, name, True, ring=ring))
    return out
