"""Spawns too deep to reach without diving (tray: "Hide underwater items (my dinosaur can't
dive)"). Baked into data/underwater/<map>.json by tools/build_underwater.py; this only loads it.
"""
from __future__ import annotations

import json
import logging

import numpy as np

from . import config as C

log = logging.getLogger(__name__)

_STRIDE = 1 << 14  # point (x, y) in whole pixels -> one number: x * _STRIDE + y


def _codes(pts: np.ndarray) -> np.ndarray:
    r = np.round(pts).astype(np.int64)
    return r[:, 0] * _STRIDE + r[:, 1]


class Underwater:
    """Underwater spawn positions of one map (map image pixels)."""

    def __init__(self, points: np.ndarray):
        pts = np.asarray(points, np.float64).reshape(-1, 2)
        self._codes = np.unique(_codes(pts)) if len(pts) else np.zeros(0, np.int64)

    def __len__(self) -> int:
        return len(self._codes)

    def mask(self, points) -> np.ndarray:
        """True for each point that is an underwater spawn (within a pixel: positions are rounded)."""
        pts = np.asarray(points, np.float64).reshape(-1, 2)
        if not len(pts) or not len(self._codes):
            return np.zeros(len(pts), bool)
        base = _codes(pts)
        hit = np.zeros(len(pts), bool)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                hit |= np.isin(base + dx * _STRIDE + dy, self._codes)
        return hit

    def dry(self, points) -> list:
        """The points that aren't underwater (same type of items as given)."""
        if not len(points):
            return list(points)
        keep = ~self.mask(points)
        return [p for p, k in zip(points, keep) if k]


def load(map_name: str) -> Underwater:
    try:
        raw = json.loads((C.DATA_DIR / "underwater" / f"{map_name}.json").read_text(encoding="utf-8"))
        pts = [p for ps in raw.get("points", {}).values() for p in ps]
    except (OSError, ValueError) as e:
        log.info("No underwater data for %s: %s", map_name, e)
        pts = []
    return Underwater(np.asarray(pts, np.float64).reshape(-1, 2))
