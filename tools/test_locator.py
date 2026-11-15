"""Check the minimap matcher.

    python -m tools.test_locator --synthetic        # self-test on a generated map
    python -m tools.test_locator                    # use data/calibration.png + config.json
    python -m tools.test_locator --screenshot shot.png --map gondwa

The screenshot mode writes data/locator_debug.png showing where the minimap was found.
"""
from __future__ import annotations

import argparse
import math
import sys
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

from potoverlay import config as C
from potoverlay.locator import MapLocator, build_minimap_mask, imread_any


def synthetic_map(size: int, seed: int = 1) -> np.ndarray:
    """Terrain-like texture: layered noise, colour-mapped, plus rivers and rocks."""
    rng = np.random.default_rng(seed)
    acc = np.zeros((size, size), np.float32)
    for octave, weight in ((8, 1.0), (32, 0.5), (128, 0.25), (512, 0.12), (1500, 0.08)):
        small = rng.random((octave, octave)).astype(np.float32)
        acc += weight * cv2.resize(small, (size, size), interpolation=cv2.INTER_CUBIC)
    acc = cv2.normalize(acc, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    img = cv2.applyColorMap(acc, cv2.COLORMAP_SUMMER)
    for _ in range(25):
        pts = np.cumsum(rng.normal(0, 40, (30, 2)), axis=0) + rng.random(2) * size
        cv2.polylines(img, [pts.astype(np.int32)], False, (160, 110, 40), int(rng.integers(3, 12)))
    for _ in range(6000):  # rocks, trees, bushes
        c = tuple(int(v) for v in rng.random(2) * size)
        cv2.circle(img, c, int(rng.integers(2, 14)), tuple(int(v) for v in rng.integers(40, 200, 3)), -1)
    return img


def fake_minimap(full: np.ndarray, cx: float, cy: float, zoom: float, angle: float, size: int) -> np.ndarray:
    """Render what a rotated/zoomed round minimap centred on (cx, cy) would show."""
    a = math.radians(angle)
    c, s = math.cos(a) / zoom, math.sin(a) / zoom
    # minimap pixel -> map pixel
    M = np.array([[c, -s, 0], [s, c, 0]], np.float64)
    M[:, 2] = np.array([cx, cy]) - M[:, :2] @ np.array([size / 2, size / 2])
    mm = cv2.warpAffine(full, M, (size, size), flags=cv2.WARP_INVERSE_MAP | cv2.INTER_LINEAR)
    mm = cv2.convertScaleAbs(mm, alpha=0.85, beta=10)  # minimap is a bit washed out
    mm = cv2.add(mm, np.random.default_rng().integers(0, 12, mm.shape, dtype=np.uint8))
    h = size // 2
    cv2.fillPoly(mm, [np.array([[h, h - 12], [h - 8, h + 8], [h + 8, h + 8]])], (255, 255, 255))  # player arrow
    cv2.circle(mm, (h, h), h - 2, (30, 30, 30), 5)  # border ring
    return mm


def run_synthetic(trials: int) -> int:
    full = synthetic_map(3000)
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "synthetic.png"
        cv2.imwrite(str(path), full)
        t = time.perf_counter()
        loc = MapLocator(path, work_max_dim=C.DEFAULTS["map_work_max_dim"])
        print(f"map features: {len(loc.kp_xy)} in {time.perf_counter() - t:.1f}s")
    size = 220
    mask = build_minimap_mask(size, size, "circle", 0.12, 0.06)
    rng = np.random.default_rng(7)
    ok, bad, errs, times = 0, 0, [], []
    for i in range(trials):
        cx, cy = rng.uniform(300, 2700, 2)
        zoom = rng.uniform(0.5, 1.3)  # minimap pixels per full-map pixel
        angle = rng.uniform(-180, 180)
        mm = fake_minimap(full, cx, cy, zoom, angle, size)
        loc.reset()
        t = time.perf_counter()
        fix = loc.locate(mm, mask, upscale=2.0)
        times.append(time.perf_counter() - t)
        if fix is None:
            print(f"  #{i:02d} no fix  (zoom {zoom:.2f}, angle {angle:.0f})")
            continue
        got = loc.work_to_full(fix.apply([[size / 2, size / 2]]))[0]
        err = float(np.hypot(*(got - [cx, cy])))
        # Quest point round trip: a map point 60 px from the player, projected onto the minimap,
        # must land where the renderer actually drew it.
        a = math.radians(angle)
        qx, qy = cx + 60, cy - 40
        dx, dy = qx - cx, qy - cy  # map offset -> minimap offset: rotate by -angle, scale by zoom
        truth = np.array([size / 2 + zoom * (math.cos(a) * dx + math.sin(a) * dy),
                          size / 2 + zoom * (-math.sin(a) * dx + math.cos(a) * dy)])
        proj = loc.full_to_minimap(fix, [[qx, qy]])[0]
        perr = float(np.hypot(*(proj - truth)))
        errs.append(err)
        good = err < 5 and perr < 3
        ok += good
        bad += not good
        print(f"  #{i:02d} err {err:5.2f}px  marker err {perr:5.2f}px  inliers {fix.inliers:3d}  "
              f"zoom {zoom:.2f}  angle {angle:5.0f}" + ("" if good else "  <-- WRONG"))
    # Tracking: player walks and turns; every frame should lock using the local search.
    cx, cy, zoom, angle = 1500, 1500, 0.9, 30
    loc.reset()
    loc.locate(fake_minimap(full, cx, cy, zoom, angle, size), mask, 2.0)
    tt, lost = [], 0
    for step in range(40):
        mm = fake_minimap(full, cx + step * 3, cy - step * 2, zoom, angle + step * 2, size)
        t = time.perf_counter()
        fix = loc.locate(mm, mask, 2.0)
        tt.append(time.perf_counter() - t)
        lost += fix is None
    print(f"\nfirst-frame lock: {ok}/{trials}, wrong positions: {bad}, median err "
          f"{np.median(errs) if errs else float('nan'):.2f}px, median {1000 * np.median(times):.0f} ms")
    print(f"tracking:         {40 - lost}/40 frames locked, median {1000 * np.median(tt):.0f} ms per frame")
    passed = bad == 0 and ok >= trials * 0.6 and lost <= 2
    print("PASS" if passed else "FAIL")
    return 0 if passed else 1


def run_screenshot(shot_path: Path, map_name: str) -> int:
    cfg = C.load_config()
    shot = imread_any(shot_path)
    if shot is None:
        print(f"Could not read {shot_path}")
        return 1
    C.apply_layout(cfg, shot.shape[1], shot.shape[0])
    mm = cfg["minimap"]
    if not C.region_ok(mm):
        print("Minimap region not set; run calibrate first.")
        return 1
    map_path = C.map_image_path(map_name)
    if map_path is None:
        print(f"No map image '{map_name}' in {C.MAPS_DIR}")
        return 1
    crop = shot[mm["y"]:mm["y"] + mm["h"], mm["x"]:mm["x"] + mm["w"]]
    loc = MapLocator(map_path, cfg["map_work_max_dim"], C.CACHE_DIR, cfg["min_inliers"])
    mask = build_minimap_mask(mm["w"], mm["h"], mm.get("shape", "circle"), cfg["center_mask"], cfg["edge_mask"])
    t = time.perf_counter()
    fix = loc.locate(crop, mask, cfg["minimap_upscale"])
    print(f"locate took {1000 * (time.perf_counter() - t):.0f} ms")
    if fix is None:
        print("No match. Check the minimap box, the map image, or try a different minimap_upscale.")
        return 1
    w, h = mm["w"], mm["h"]
    corners = loc.work_to_full(fix.apply([[0, 0], [w, 0], [w, h], [0, h]]))
    center = loc.work_to_full(fix.apply([[w / 2, h / 2]]))[0]
    print(f"player at full-map pixel ({center[0]:.0f}, {center[1]:.0f}), inliers {fix.inliers}/{fix.matches}, "
          f"rotation {math.degrees(fix.angle):.1f} deg, minimap shows {w * fix.scale / loc.work_scale:.0f} map px across")
    full = imread_any(map_path)
    cv2.polylines(full, [corners.astype(np.int32)], True, (0, 0, 255), max(2, full.shape[1] // 800))
    cv2.circle(full, tuple(int(v) for v in center), max(4, full.shape[1] // 300), (0, 0, 255), -1)
    s = min(1.0, 2000 / max(full.shape[:2]))
    out = cv2.resize(full, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    dbg = C.DATA_DIR / "locator_debug.png"
    cv2.imencode(".png", out)[1].tofile(str(dbg))
    print(f"wrote {dbg}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--trials", type=int, default=20)
    ap.add_argument("--screenshot", type=Path, default=C.CALIBRATION_SHOT)
    ap.add_argument("--map", default=None)
    args = ap.parse_args()
    if args.synthetic:
        return run_synthetic(args.trials)
    return run_screenshot(args.screenshot, args.map or C.load_config()["active_map"] or (C.list_maps() or [""])[0])


if __name__ == "__main__":
    sys.exit(main())
