"""Render what the overlay would draw on a screenshot (no game needed).

    python -m tools.preview                      # data/calibration.png, active map
    python -m tools.preview --screenshot shot.png --map riparia

Writes data/preview.png: the screenshot with the overlay's markers painted on top.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from potoverlay import config as C  # noqa: E402
from potoverlay.app import pick_nearest  # noqa: E402
from potoverlay.locator import MapLocator, build_minimap_mask, imread_any  # noqa: E402
from potoverlay.layers import layer_legend, load_layers  # noqa: E402
from potoverlay.markers import OverlayState, assign_colors, layout_layer, layout_markers  # noqa: E402
from potoverlay.ocr import WindowsOcr  # noqa: E402
from potoverlay.overlay import Overlay  # noqa: E402
from potoverlay.quests import ActiveQuestTracker, QuestDB  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--screenshot", type=Path, default=C.CALIBRATION_SHOT)
    ap.add_argument("--map", default=None)
    ap.add_argument("--out", type=Path, default=C.DATA_DIR / "preview.png")
    ap.add_argument("--layers", default=None,
                    help="always-on layers to draw, e.g. fish_spawn,krill (default: as in config.json)")
    args = ap.parse_args()
    cfg = C.load_config()
    map_name = args.map or cfg["active_map"]
    shot = imread_any(args.screenshot)
    C.apply_layout(cfg, shot.shape[1], shot.shape[0])
    mm, qp = cfg["minimap"], cfg["quest_panel"]
    w, h = mm["w"], mm["h"]

    lines = WindowsOcr(cfg["ocr_language"]).read_lines(
        shot[qp["y"]:qp["y"] + qp["h"], qp["x"]:qp["x"] + qp["w"]],
        C.ocr_upscale_for(cfg, shot.shape[1], shot.shape[0]))
    db = QuestDB.load(C.quest_file(map_name), map_name)
    groups = ActiveQuestTracker(db, cfg["quest_match_threshold"]).detect_groups(lines)

    loc = MapLocator(C.map_image_path(map_name), cfg["map_work_max_dim"], C.CACHE_DIR, cfg["min_inliers"])
    mask = build_minimap_mask(w, h, mm.get("shape", "circle"), cfg["center_mask"], cfg["edge_mask"])
    fix = loc.locate(shot[mm["y"]:mm["y"] + h, mm["x"]:mm["x"] + w], mask, cfg["minimap_upscale"])
    if fix is None:
        print("minimap not found on the map")
        return 1
    player = loc.work_to_full(fix.apply([[w / 2, h / 2]]))[0]
    shown = pick_nearest(groups, db.by_key(), player)
    colors = assign_colors([q.name for q in shown])
    items = [(q.name, colors[q.name], loc.full_to_minimap(fix, q.points),
              loc.full_to_minimap(fix, q.targets)) for q in shown]
    markers = layout_markers(items, w, h, mm.get("shape", "circle"), cfg["marker_radius"],
                             cfg["show_offscreen_arrows"])
    for q in shown:
        print(f"{q.name} @ {q.poi}: {len(q.points)} points, {len(q.targets)} targets")
    kinds = args.layers.split(",") if args.layers is not None else list(cfg.get("layers") or [])
    layers = load_layers(map_name, [k for k in kinds if k])
    for layer in layers:
        k = layer.kind
        mk = layout_layer(loc.full_to_minimap(fix, layer.points), w, h, mm.get("shape", "circle"),
                          cfg["marker_radius"] + 1, k.label, k.color, k.style)
        markers += mk
        print(f"{k.label}: {len(mk)} on the minimap (of {len(layer.points)} on the map)")
    legend = sorted(colors.items()) + layer_legend(layers) if cfg["show_legend"] else []

    app = QApplication.instance() or QApplication(sys.argv)
    ov = Overlay(cfg, {"left": 0, "top": 0})
    ov.set_state(OverlayState(True, True, markers, f"{len(shown)} active quests", legend))
    img = ov.grab().toImage().convertToFormat(QImage.Format_RGBA8888)
    ptr = img.constBits()
    rgba = np.frombuffer(ptr, np.uint8, img.sizeInBytes()).reshape(img.height(), img.bytesPerLine() // 4, 4)
    rgba = rgba[:, :img.width()].astype(np.float32)
    x0, y0 = mm["x"] - ov.ox, mm["y"] - ov.oy
    out = shot.copy()
    H, W = rgba.shape[:2]
    y1, x1 = min(out.shape[0], y0 + H), min(out.shape[1], x0 + W)
    region = out[y0:y1, x0:x1].astype(np.float32)
    src = rgba[: y1 - y0, : x1 - x0]
    a = src[:, :, 3:4] / 255.0
    region = region * (1 - a) + src[:, :, [2, 1, 0]] * a
    out[y0:y1, x0:x1] = region.astype(np.uint8)
    cv2.imencode(".png", out)[1].tofile(str(args.out))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
