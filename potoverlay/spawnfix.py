"""Potservers positions (grid 0..500) -> pixels on our 4096 px map image, the frame the overlay
finds you in (by matching the minimap against that image).

The plain conversion is grid x 4096/500. On Riparia that puts everything slightly too far out
from the middle: F10 position reports, standing on 51 items all over the map, show Potservers'
spawns 0.93 % too spread out around a point a bit north of the map centre. That's a few metres
near the middle and up to ~35 m in the corners; with the correction below, ~1 m everywhere.

Maps without a measured correction use the plain conversion. To measure one: about 10 F10
reports on that map, spread around the edges plus one near the middle.
"""
from __future__ import annotations

import numpy as np

GRID = 500.0       # Potservers map coordinates run 0..500
IMAGE_SIZE = 4096  # the map images are 4096 px

# map -> (scale, centre in image px): corrected = centre + scale * (plain - centre)
SPREAD = {
    "riparia": (0.9907, (2185.0, 1782.0)),
}


def to_image(map_name: str, pts) -> np.ndarray:
    """Potservers grid points (N x 2) -> image pixels (N x 2), corrected for this map."""
    return correct(map_name, np.asarray(pts, np.float64).reshape(-1, 2) * (IMAGE_SIZE / GRID))


def correct(map_name: str, px) -> np.ndarray:
    """Image-pixel points that came from Potservers' frame (plain conversion) -> corrected."""
    p = np.asarray(px, np.float64).reshape(-1, 2)
    fix = SPREAD.get(map_name)
    if fix:
        s, c = fix[0], np.asarray(fix[1], np.float64)
        p = c + s * (p - c)
    return p
