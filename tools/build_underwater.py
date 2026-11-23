"""Find which spawns are underwater (too deep to reach without diving) and bake them into
data/underwater/<map>.json, for the tray toggle "Hide underwater items (can't dive)".

    python -m tools.build_underwater

Potservers gives each spawn's height. A spawn counts as underwater when it's in water on the map
image and more than DEPTH_M below that water's surface. The surface of each lake, river or sea is
where its floating plants are (lakeweed, water lilies, kelp, jellyfish: they sit on the surface);
a pond without any uses the bank around it instead (the lowest land spawns next to it), which
only ever under-counts.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict

import cv2
import numpy as np

from potoverlay import config as C
from potoverlay import freshwater as F
from potoverlay.locator import imread_any
from potoverlay.spawnfix import to_image

DEPTH_M = 2.5          # deeper than this below the surface: you'd have to dive
FLOATING = {"lake_weed", "water_lily", "kelp", "jellyfish"}
BANK_PX = 64           # land spawns this close to a pond tell where its bank is
OUT_DIR = C.DATA_DIR / "underwater"


def spawns(map_name: str, markers: list[dict]):
    """(positions in image px, heights in m, kinds) of every spawn with a height."""
    P, Z, K = [], [], []
    for m in markers:
        pts, wpts = m.get("points") or [], m.get("world_points") or []
        if not pts or len(pts) != len(wpts):
            continue
        w = np.asarray(wpts, np.float64).reshape(len(pts), -1)
        if w.shape[1] < 3:
            continue
        P.append(to_image(map_name, pts))
        Z.append(w[:, 2] / 100.0)  # Unreal units are centimetres
        K += [m["id"]] * len(pts)
    return np.vstack(P), np.concatenate(Z), np.array(K)


def build(map_name: str) -> dict:
    markers = json.loads((C.DATA_DIR / "potservers" / f"map_{map_name}.json").read_text(encoding="utf-8"))
    markers = markers["data"].get("markers", [])
    img = imread_any(C.map_image_path(map_name))
    size = img.shape[0]
    score, _ = F.water_score(img, markers, map_name)
    water = cv2.morphologyEx((score > 0).astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    del score
    bodies = cv2.morphologyEx(water, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    _, lab = cv2.connectedComponents(bodies, connectivity=8)

    P, Z, K = spawns(map_name, markers)
    xi = np.clip(P.round().astype(int), 0, size - 1)
    body = np.zeros(len(P), int)
    for dx in (0, -2, 2):  # spawns on the water's edge count as in it
        for dy in (0, -2, 2):
            b = lab[np.clip(xi[:, 1] + dy, 0, size - 1), np.clip(xi[:, 0] + dx, 0, size - 1)]
            body = np.where(body == 0, b, body)

    # Each water body's surface: where its floating plants are.
    floating = np.isin(K, list(FLOATING)) & (body > 0)
    surface = {}
    for b in np.unique(body[body > 0]):
        z = Z[floating & (body == b)]
        if len(z) >= 2:
            surface[int(b)] = float(np.median(z))
    # Without any: the bank around it (land spawns close by; low end, as banks rise from the water).
    land = defaultdict(list)
    for i in np.nonzero(body == 0)[0]:
        land[(xi[i, 0] // BANK_PX, xi[i, 1] // BANK_PX)].append(i)
    under = np.zeros(len(P), bool)
    for i in np.nonzero(body > 0)[0]:
        s = surface.get(int(body[i]))
        if s is None:
            cx, cy = xi[i, 0] // BANK_PX, xi[i, 1] // BANK_PX
            near = np.array([j for a in (-1, 0, 1) for b in (-1, 0, 1) for j in land.get((cx + a, cy + b), [])], int)
            if len(near):
                near = near[np.hypot(*(P[near] - P[i]).T) <= BANK_PX]
            if len(near) < 3:
                continue  # can't tell: shown
            s = float(np.percentile(Z[near], 10))
        under[i] = Z[i] < s - DEPTH_M

    out: dict[str, list] = defaultdict(list)
    for i in np.nonzero(under)[0]:
        out[str(K[i])].append([round(float(P[i, 0]), 1), round(float(P[i, 1]), 1)])
    return {"map": map_name, "depth_m": DEPTH_M,
            "source": "Spawn heights from Potservers (https://potservers.net)",
            "points": dict(sorted(out.items()))}


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for f in sorted((C.DATA_DIR / "potservers").glob("map_*.json")):
        name = f.stem[4:]
        if C.map_image_path(name) is None:
            print(f"[{name}] no map image, skipping")
            continue
        data = build(name)
        (OUT_DIR / f"{name}.json").write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
        n = sum(len(v) for v in data["points"].values())
        print(f"[{name}] {n} underwater spawns -> data/underwater/{name}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
