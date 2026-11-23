"""Fresh (drinkable) water (tray: Food > Fresh water): blue areas on the minimap and the in-game map.

Baked into data/freshwater/<map>.json by tools/build_freshwater.py; the app only loads that file.
The outlines are traced from the map image itself; VulnonaMAP (https://vulnona.com/game/map/, by
Coco.N) only guides which inland water counts and where small ponds and streams are. Water joined
to the sea is never fresh.
The helpers below are used by that build tool, not at runtime.
"""
from __future__ import annotations

import json
import logging

import cv2
import numpy as np

from . import config as C

log = logging.getLogger(__name__)

COLOR = "#3fa9ff"
LABEL = "Fresh water"
CREDIT = "Fresh water from VulnonaMAP (vulnona.com/game/map)"

_WATER_SPAWNS = {"lake_weed", "lake_sponge", "water_lily", "kelp", "starfish", "sea_anemone", "coral",
                 "fish_spawn", "jellyfish", "krill"}
_LAND_SPAWNS = {"roots", "rocks", "branches", "pinecone", "acorn", "berry", "yarrow", "ant_hill", "bones",
                "tigernut", "lychee", "burrow", "termites"}


def path(map_name: str):
    return C.DATA_DIR / "freshwater" / f"{map_name}.json"


# ---------- runtime ----------
def load(map_name: str) -> "FreshWater | None":
    """The map's baked fresh water (None if there's no file for it)."""
    try:
        raw = json.loads(path(map_name).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.info("No fresh water data for %s: %s", map_name, e)
        return None
    return FreshWater([np.asarray(z, np.float64).reshape(-1, 2) for z in raw.get("zones", [])],
                      [(n["name"], float(n["x"]), float(n["y"])) for n in raw.get("names", [])])


class FreshWater:
    """Fresh water outlines (full-map pixels, filled even-odd) and names, projected onto the
    minimap or the in-game map each frame."""

    def __init__(self, polys: list[np.ndarray], names: list[tuple[str, float, float]] = ()):
        self.polys = [np.asarray(p, np.float64).reshape(-1, 2) for p in polys]
        self.names = list(names)  # VulnonaMAP's names (kept in the data; not drawn)
        self._verts = np.vstack(self.polys) if self.polys else np.zeros((0, 2))
        self._splits = np.cumsum([len(p) for p in self.polys])[:-1]

    def __len__(self) -> int:
        return len(self.polys)

    def project(self, to_screen, x0: float, y0: float, x1: float, y1: float) -> list[np.ndarray]:
        """Outlines in screen coordinates (`to_screen`: full-map points -> screen points) that
        touch the rectangle (x0, y0)-(x1, y1). Islands in a lake come along with the lake."""
        if not self.polys:
            return []
        scr = to_screen(self._verts)
        out = []
        for p in np.split(scr, self._splits):
            if p[:, 0].max() >= x0 and p[:, 0].min() <= x1 and p[:, 1].max() >= y0 and p[:, 1].min() <= y1:
                out.append(p)
        return out


# ---------- build tool helpers (map image analysis) ----------
def spawn_points(markers: list[dict], kinds: set, map_name: str, size: int) -> np.ndarray:
    """Pixel positions (on a size x size map image) of these spawn kinds."""
    from .spawnfix import to_image
    pts = [to_image(map_name, m["points"]) for m in markers if m.get("id") in kinds and m.get("points")]
    if not pts:
        return np.zeros((0, 2), int)
    return np.clip((np.vstack(pts) * (size / 4096)).astype(int), 0, size - 1)


def water_score(img_bgr: np.ndarray, markers: list[dict], map_name: str) -> tuple[np.ndarray, float]:
    """How much each pixel of the map image looks like water: > 0 water, < 0 land. Learned per
    map from its colours at water spawns (lake weed, fish...) against land spawns (roots, rocks...),
    since water is navy on Riparia, teal on Gondwa and turquoise on Panjura.
    Returns (score, loose): pixels above `loose` are water-ish (used to follow a lake's edge or a
    thin stream out from clearly watery pixels)."""
    size = img_bgr.shape[0]
    sm = cv2.medianBlur(img_bgr, 3)
    b = sm.astype(np.float32)
    feat = np.dstack([cv2.cvtColor(sm, cv2.COLOR_BGR2LAB).astype(np.float32), b[..., 0] - b[..., 2], b[..., 0] - b[..., 1]])
    wp = spawn_points(markers, _WATER_SPAWNS, map_name, size)
    lp = spawn_points(markers, _LAND_SPAWNS, map_name, size)
    X1, X0 = feat[wp[:, 1], wp[:, 0]], feat[lp[:, 1], lp[:, 0]]
    w = np.linalg.solve(np.cov(X0.T) + np.cov(X1.T) + np.eye(5) * 1e-3, X1.mean(0) - X0.mean(0))
    s0, s1 = X0 @ w, X1 @ w
    cuts = np.linspace(min(s0.min(), s1.min()), max(s0.max(), s1.max()), 600)
    err = [(s0 > c).mean() + (s1 <= c).mean() for c in cuts]
    cut = cuts[int(np.argmin(err))]
    score = (feat.reshape(-1, 5) @ w - cut).reshape(size, size).astype(np.float32)
    # Water is clearly blue on every map; snow, salt and grey rock can pass the colour test above.
    blue = b[..., 0] - b[..., 2]
    score[(blue < 8) | (blue < 0.12 * (b[..., 0] + 1))] = -1e3
    loose = float(np.percentile(s0 - cut, 88))  # ~1 in 8 land spots pass: only used next to water
    return score, min(loose, 0.0)


def ocean(water: np.ndarray, connectors: np.ndarray | None = None, bridge: int = 9) -> np.ndarray:
    """The sea: big water reaching the map edge, plus everything joined to it (gaps up to
    `bridge` px closed, and rivers in `connectors` count as joins)."""
    m = water if connectors is None else (water | connectors)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (bridge, bridge)))
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    edge = set(np.unique(np.r_[lab[0], lab[-1], lab[:, 0], lab[:, -1]]).tolist()) - {0}
    big = [i for i in edge if st[i, cv2.CC_STAT_AREA] > 0.03 * m.size]
    return np.isin(lab, big).astype(np.uint8)


def outlines(mask: np.ndarray, full_size: tuple[int, int], eps: float = 0.6) -> list[np.ndarray]:
    """Outlines (outer edges and islands) in full-map pixels; fill them even-odd."""
    contours, _ = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    sx, sy = full_size[0] / mask.shape[1], full_size[1] / mask.shape[0]
    out = []
    for c in contours:
        c = cv2.approxPolyDP(c, eps, True).reshape(-1, 2).astype(np.float64)
        if len(c) >= 3:
            out.append((c + 0.5) * (sx, sy))
    return out
