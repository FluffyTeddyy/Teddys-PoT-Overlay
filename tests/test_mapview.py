"""In-game map screen detection, on simulated map screens (no game needed).

    python -m tests.test_mapview
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import cv2
import numpy as np

from potoverlay import config as C
from potoverlay.locator import MapLocator, imread_any
from potoverlay.mapview import MapScreenDetector, layout_view_markers

W, H = 1920, 1200
MINIMAP = (1625, 77, 274, 274)
failures = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global failures
    print(("  ok   " if cond else "  FAIL ") + name + (f"  ({detail})" if detail else ""))
    failures += not cond


def fake_map_screen(full: np.ndarray, zoom: float, center: tuple[float, float], seed: int = 0):
    """Render the map like a map screen: screen = zoom * map + offset, darkened, with UI panels.
    Returns (image, zoom, offset)."""
    off = np.array([W / 2 - zoom * center[0], H / 2 - zoom * center[1]])
    M = np.array([[zoom, 0, off[0]], [0, zoom, off[1]]], np.float64)
    img = cv2.warpAffine(full, M, (W, H), flags=cv2.INTER_AREA if zoom < 1 else cv2.INTER_LINEAR,
                         borderValue=(28, 24, 22))
    img = cv2.convertScaleAbs(img, alpha=0.8, beta=8)  # map screens are often dimmed a little
    rng = np.random.default_rng(seed)
    cv2.rectangle(img, (0, 0), (W, 70), (20, 20, 20), -1)  # title bar
    cv2.putText(img, "MAP", (40, 50), cv2.FONT_HERSHEY_DUPLEX, 1.4, (230, 230, 230), 2)
    cv2.rectangle(img, (W - 330, 120), (W - 30, 640), (25, 25, 25), -1)  # legend panel
    for i in range(8):
        cv2.putText(img, f"Legend item {i}", (W - 300, 170 + 55 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                    (200, 200, 200), 1)
    for _ in range(25):  # map icons
        c = (int(rng.integers(0, W)), int(rng.integers(80, H)))
        cv2.circle(img, c, 9, tuple(int(v) for v in rng.integers(60, 255, 3)), -1)
    return img, zoom, off


def main() -> int:
    path = C.map_image_path("riparia")
    if path is None:
        print("riparia map not downloaded")
        return 1
    full = imread_any(path)
    loc = MapLocator(path, 2048, C.CACHE_DIR)
    det = MapScreenDetector(W, H, MINIMAP)

    print("simulated map screens")
    cases = [("whole map visible", 1100 / 4096, (2048, 2048)),
             ("zoomed in", 0.8, (1700, 1500)),
             ("zoomed far in", 2.0, (1137, 1433))]
    for name, zoom, center in cases:
        img, z, off = fake_map_screen(full, zoom, center)
        det.reset_viewport()  # each case is a separate "map opened" session
        t = time.perf_counter()
        view = det.detect(loc, img)
        dt = 1000 * (time.perf_counter() - t)
        if view is None:
            check(f"{name}: detected", False, det.reason)
            continue
        # Random quest-like points on the visible part of the map.
        rng = np.random.default_rng(1)
        pts = np.column_stack([rng.uniform(center[0] - 0.4 * W / z, center[0] + 0.4 * W / z, 30),
                               rng.uniform(center[1] - 0.4 * H / z, center[1] + 0.4 * H / z, 30)])
        pts = pts[(pts >= 0).all(axis=1) & (pts < 4096).all(axis=1)]
        truth = pts * z + off
        got = loc.full_to_minimap(view.fix, pts)
        err = np.hypot(*(got - truth).T)
        check(f"{name}: detected, markers within 3 px", err.max() < 3,
              f"max err {err.max():.2f}px, {view.fix.inliers} inliers, {dt:.0f} ms")
        markers = layout_view_markers(view, loc, [("q", "#fff", pts, None)], W, H)
        check(f"{name}: markers clipped to the screen", len(markers) == len(pts), f"{len(markers)}/{len(pts)}")

    real = Path(__file__).parent / "fixtures" / "mapscreen_riparia.png"
    if real.exists():
        print("real in-game map screen (map window, half the minimap still visible)")
        import json

        img = imread_any(real)
        det.reset_viewport()
        view = det.detect(loc, img)
        check("real map screen detected", view is not None, det.reason)
        if view is not None:
            vx0, vy0, vx1, vy1 = view.viewport
            check("viewport = the map window, not the whole map picture",
                  abs(vx0 - 171) < 25 and abs(vy0 - 190) < 25 and abs(vx1 - 1748) < 25 and abs(vy1 - 1000) < 25,
                  f"{[round(v) for v in view.viewport]}")
            # The game draws waystones as green icons; ours come from Potservers data. They must coincide.
            md = json.loads((C.DATA_DIR / "potservers" / "map_riparia.json").read_text(encoding="utf-8"))["data"]
            ws = np.array(next(m for m in md["markers"] if m["id"] == "waystone")["points"]) * 4096 / 500
            proj = loc.full_to_minimap(view.fix, ws)
            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
            for ax, ay in ((948, 544), (1660, 832), (360, 988)):  # the game's green icons (approx.)
                win = hsv[ay - 30:ay + 30, ax - 30:ax + 30]
                ys, xs = np.nonzero((win[:, :, 0] > 40) & (win[:, :, 0] < 85) & (win[:, :, 1] > 120)
                                    & (win[:, :, 2] > 90))
                icon = np.array([ax - 30 + xs.mean(), ay - 30 + ys.mean()])
                d = np.min(np.hypot(*(proj - icon).T))
                # The screen<->map fit itself is ~0.3 px; a few px remain because the data's waystone
                # point and the game's icon aren't at exactly the same spot (~25 m at this zoom).
                check(f"waystone lands on the game's icon near ({ax},{ay})", d < 10, f"{d:.1f}px")
            mk = layout_view_markers(view, loc, [("w", "#fff", ws, None)], W, H)
            check("no markers outside the map window",
                  all(vx0 <= m.x <= vx1 and vy0 <= m.y <= vy1 for m in mk), f"{len(mk)} inside")

            # Follow-up frames only look inside the map window: same answer, faster.
            t = time.perf_counter()
            view2 = det.detect(loc, img)
            dt = 1000 * (time.perf_counter() - t)
            if view2 is not None:
                d = np.max(np.hypot(*(loc.full_to_minimap(view2.fix, ws) - proj).T))
                check("follow-up frame (window only) agrees", d < 1.5, f"{d:.2f}px apart, {dt:.0f} ms")
            else:
                check("follow-up frame (window only) detected", False, det.reason)

            # Title-bar check: learn it from this screen, then it must recognise the map screen and
            # not fire on gameplay.
            import tempfile

            from potoverlay.mapview import MapWindowProbe

            class Grab:
                def __init__(self, im):
                    self.im = im

                def grab(self, r):
                    return self.im[r["y"]:r["y"] + r["h"], r["x"]:r["x"] + r["w"]]

            probe = MapWindowProbe(Path(tempfile.mkdtemp()) / "probe.png")
            check("title bar learned", probe.learn(img, view.viewport), str(probe.region))
            if probe.ready:
                again = MapWindowProbe(probe.path)  # also survives a restart
                s_map = again.score(Grab(img))
                check("title bar recognised on the map screen", s_map is not None and s_map >= probe.OPEN,
                      f"{s_map:.2f}" if s_map is not None else "")
                for shot in [Path(__file__).parent / "fixtures" / n
                             for n in ("hud_1920x1200.png", "gameplay_cedrus_forest.png")]:
                    s = again.score(Grab(imread_any(shot)))
                    check(f"title bar check quiet on {shot.name}", s is not None and s < probe.CLOSED,
                          f"{s:.2f}" if s is not None else "")
        det.reset_viewport()

    print("normal gameplay must not count as the map screen")
    shots = [Path(__file__).parent / "fixtures" / "hud_1920x1200.png"]
    shots += sorted((Path(__file__).parent / "fixtures").glob("gameplay_*.png"))
    for shot in shots:
        img = imread_any(shot)
        view = det.detect(loc, img)
        check(f"{shot.name}: not detected", view is None, det.reason or "detected!")
    print("\nALL PASSED" if failures == 0 else f"\n{failures} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
