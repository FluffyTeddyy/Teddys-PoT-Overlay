"""Fresh (drinkable) water (tray: Food > Fresh water): blue areas on the minimap and the in-game map.

Baked into data/freshwater/<map>.json by tools/build_freshwater.py; the app only loads that file.
Sources:
  - VulnonaMAP (https://vulnona.com/game/map/, by Coco.N): drinking water outlines, ponds, rivers
    and names for Gondwa and Panjura; names and positions only for Riparia.
  - Riparia's outlines: found on the map image (water is bluer than land; water reaching the map's
    edge is the ocean, the rest is fresh), kept only where VulnonaMAP names a water spot.
The helpers below are used by that build tool, not at runtime.
"""
from __future__ import annotations

import json
import logging

import cv2
import numpy as np

from . import config as C

log = logging.getLogger(__name__)

WORK = 1024   # the map image is analysed at this size (build tool only)
GRID = 500.0  # Potservers coordinates run 0..500
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
def spawn_points(markers: list[dict], kinds: set, size: int) -> np.ndarray:
    pts = [np.asarray(m["points"], np.float64).reshape(-1, 2) for m in markers
           if m.get("id") in kinds and m.get("points")]
    if not pts:
        return np.zeros((0, 2), int)
    return np.clip((np.vstack(pts) * (size / GRID)).astype(int), 0, size - 1)


def water_mask(img_bgr: np.ndarray, markers: list[dict]) -> np.ndarray:
    """1 where the map shows water (sea or fresh), at WORK x WORK. Water is bluer than it is red
    on every map (navy or turquoise); earth and rock are brown or grey. The cut-off sits between
    typical land and typical water spawns of this map."""
    W = WORK
    img = cv2.resize(img_bgr, (W, W), interpolation=cv2.INTER_AREA)
    wp, lp = spawn_points(markers, _WATER_SPAWNS, W), spawn_points(markers, _LAND_SPAWNS, W)
    if not len(wp) or not len(lp):
        return np.zeros((W, W), np.uint8)
    blur = cv2.GaussianBlur(img, (5, 5), 0).astype(np.int16)
    blue = blur[..., 0] - blur[..., 2]
    cut = (np.percentile(blue[lp[:, 1], lp[:, 0]], 90) + np.percentile(blue[wp[:, 1], wp[:, 0]], 25)) / 2
    water = (blue > cut).astype(np.uint8)
    water = cv2.morphologyEx(water, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    return cv2.morphologyEx(water, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))


def inland(water: np.ndarray) -> np.ndarray:
    """Water not connected to the ocean (= water reaching the map edge, small gaps from the sea's
    texture bridged first). Single specks dropped."""
    bridged = cv2.morphologyEx(water, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    _, lab0 = cv2.connectedComponents(bridged, connectivity=8)
    edge = np.setdiff1d(np.unique(np.r_[lab0[0], lab0[-1], lab0[:, 0], lab0[:, -1]]), [0])
    ocean = cv2.dilate(np.isin(lab0, edge).astype(np.uint8), np.ones((5, 5), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(water & (1 - ocean), connectivity=8)
    keep = stats[:, cv2.CC_STAT_AREA] >= 12
    keep[0] = False
    return keep[labels].astype(np.uint8)


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
