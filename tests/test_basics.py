"""Quick checks that don't need the game running.

    python -m tests.test_basics
"""
from __future__ import annotations

import os
import sys
import threading
import time

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from potoverlay.markers import layout_markers  # noqa: E402
from potoverlay.quests import ActiveQuestTracker, Quest, QuestDB, match_score, normalize  # noqa: E402

failures = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global failures
    print(("  ok   " if cond else "  FAIL ") + name + (f"  ({detail})" if detail else ""))
    failures += not cond


def test_matching() -> None:
    print("quest name matching")
    db = QuestDB("test", [
        Quest("Gather Ferns", [(1, 1)]),
        Quest("Gather Moss", [(2, 2)]),
        Quest("Explore the Waterfall", [(3, 3)], aliases=["Waterfall Point"]),
    ])
    t = ActiveQuestTracker(db, threshold=82, miss_tolerance=1)
    check("progress counter stripped", normalize("Gather Ferns 2/5") == "gather ferns")
    check("exact title", t.detect(["Gather Ferns 2/5"]) == {"Gather Ferns"})
    check("OCR typo tolerated", t.detect(["Gather Fems"]) == {"Gather Ferns"}, str(t.detect(["Gather Fems"])))
    check("partial word doesn't match both", t.detect(["Gather"]) == set(), str(t.detect(["Gather"])))
    check("wrapped title over two lines", t.detect(["Explore the", "Waterfall"]) == {"Explore the Waterfall"})
    check("alias", t.detect(["Waterfall Point"]) == {"Explore the Waterfall"})
    check("score sanity", match_score("gather moss", "gather ferns") < 82)
    def names():
        return set().union(*t.active) if t.active else set()

    t.update(["Gather Moss 0/3"])
    check("active after read", names() == {"Gather Moss"})
    t.update([])
    check("empty read keeps state", names() == {"Gather Moss"})
    t.update(["Some menu text", "Settings"])
    check("unrelated text on screen keeps state", names() == {"Gather Moss"})
    t.update(["Quests", "something else"])
    check("survives one miss", names() == {"Gather Moss"})
    t.update(["Quests"])
    check("dropped after misses (empty quest list)", names() == set())

    from potoverlay.quests import parse_panel
    e = parse_panel(["Quests", "Sand Rings Waystone", "- Restore Waystone at Sand Rings Waystone by",
                     "Delivering Glow Starfish!", "1% Complete", "Cicada Molt", "- Collect 20 Cicada Molt 0/20"])
    check("panel parsed into 2 quests", len(e) == 2, str(e))
    check("wrapped objective joined", e and e[0].objectives ==
          ["Restore Waystone at Sand Rings Waystone by Delivering Glow Starfish!"], str(e[0].objectives if e else ""))


def test_potservers_catalog() -> None:
    from potoverlay import config as C
    path = C.quest_file("riparia")
    if not path.exists():
        print("riparia catalog not built yet; skipping")
        return
    print("real quest catalog (Riparia) + quest list from the screenshot")
    db = QuestDB.load(path, "riparia")
    t = ActiveQuestTracker(db)
    lines = ['Quests', 'Gather Mushrooms', '- Collect 15 Milk Cap Mushrooms 0/1 5', 'Honour',
             '- Deliver 1 Bones 0/1', 'Sana Rings Waystone', '- Restore Waystone at Sand Rings Waystone by',
             'Delivering Glow Starfish!', '1% Complete', 'Cicada Moit', '- Collect 20 Cicada Molt 0/20',
             'Honour the Dead', '- Deliver 3 Bones 0/3']
    groups = t.detect_groups(lines)
    idx = db.by_key()
    titles = [sorted({idx[k].name for k in g}) for g in groups]
    for g, tl in zip(groups, titles):
        pois = sorted({idx[k].poi for k in g})
        print(f"     {tl} at {len(pois)} POI(s): {', '.join(pois[:4])}{'...' if len(pois) > 4 else ''}")
    flat = {n for tl in titles for n in tl}
    check("5 quests found", len(groups) == 5, str(len(groups)))
    check("expected titles", {"Gather Mushrooms", "Honour the Dead", "Sand Rings Waystone", "Cicada Molt"} <= flat,
          str(flat))
    check("each group is one quest title", all(len(tl) == 1 for tl in titles), str(titles))
    from potoverlay.app import pick_nearest
    player = (1709, 1524)  # where the minimap matcher put the screenshot (inside Big Tree Overlook)
    chosen = pick_nearest(groups, idx, player)
    check("nearest variants chosen (duplicates merged)", len(chosen) == 4, str(len(chosen)))
    for q in chosen:
        print(f"     -> {q.name} @ {q.poi}: {len(q.points)} item points, {len(q.targets)} targets")
    cm = [q for q in chosen if q.name == "Cicada Molt"]
    check("Cicada Molt resolved to Big Tree Overlook", bool(cm) and cm[0].poi == "Big Tree Overlook",
          cm[0].poi if cm else "")

    # Three consecutive live OCR reads of the same quest list, glitches and all.
    live = [
        ['Qu»ests', 'Hunt or hunted.', 'Hunt the dangerous critter—Se', 'Gather:Mushroomse',
         '- Collect 1 5 Milk Cap MuShrooms 0/1 5', "Fish'Bones", '- Collect 9 F@sh Bones 1/9',
         '11% Complete (0% Contribution)', 'Longhorn Beetle—ft..', 'Collect 5 Longhorn Beetle 3/5',
         '60% Complete (33% Contribution)', '- Collect 20 Algae 0/20'],
        ['QueSts', "Hunt or be hunted. '", 'Hunt the dangerous critter', 'Gather Mushrooms;',
         '—cbllect 15 Milk Cap Mushrooms 0/1 5', 'Fish Bones', '- Collect 9 Fish Bones 1/9',
         '11% Complete (0% Contribution)', 'Longhorn.Beetle', 'JCollect 5 Longhorn Beetle 3/5',
         '60% Complete (33% Contribution)', '- Collect 20 Algae 0/20'],
        ['Hunt or be hunted.', 'Hunt the dangerous:crittery', 'GatheÉMushrooms',
         '- Collect.1 58Milk,Cap Mushrooms 0/-15', 'Fisl*Bonés', '- Collect 9 Fish Bones;1/9',
         '11% Complete (0% Contribution)', 'LonghorQBeétle', '- Collect 5 Longhorn Beetle 3/5',
         '60% Complete (33% Contribution)', '• Collect 20 Algae 0/20'],
    ]
    reads = [sorted(sorted(idx[k].objectives[0] if idx[k].objectives else idx[k].name for k in g)[0]
                    for g in t.detect_groups(lines)) for lines in live]
    print("     live reads ->", reads[0])
    check("live: all 4 gather quests found", len(reads[0]) == 4 and any("Milk Cap" in r for r in reads[0]),
          str(reads[0]))
    check("live: same result on every read despite OCR glitches", reads[0] == reads[1] == reads[2],
          f"{reads[1]} / {reads[2]}")
    t2 = ActiveQuestTracker(db)
    counts = [len(t2.update(lines)) for lines in live * 2]
    check("live: active quest count steady", set(counts) == {4}, str(counts))


def test_markers() -> None:
    print("marker layout")
    pts_in = np.array([[100.0, 100.0]])
    pts_out = np.array([[400.0, 100.0], [100.0, 900.0]])
    ms = layout_markers([("a", "#fff", pts_in), ("b", "#fff", pts_out)], 200, 200, "circle", 6, True)
    check("inside point drawn as dot", any(m.label == "a" and m.inside for m in ms))
    arrows = [m for m in ms if m.label == "b"]
    check("one arrow for off-map quest", len(arrows) == 1 and not arrows[0].inside)
    if arrows:
        a = arrows[0]
        check("arrow points at nearest target (right)", abs(a.angle) < 0.01 and a.x > 180, f"{a.x:.0f},{a.angle:.2f}")


def test_ocr() -> None:
    print("windows OCR on a fake quest panel (in a worker thread, like the app)")
    from potoverlay.ocr import WindowsOcr

    panel = np.full((220, 360, 3), (40, 55, 45), np.uint8)
    for i, line in enumerate(["QUESTS", "Gather Ferns 2/5", "Explore the Waterfall", "Hunt Prey 0/1"]):
        y = 40 + i * 48
        cv2.putText(panel, line, (14, y), cv2.FONT_HERSHEY_DUPLEX, 0.75, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(panel, line, (14, y), cv2.FONT_HERSHEY_DUPLEX, 0.75, (235, 235, 235), 1, cv2.LINE_AA)
    out: dict = {}

    def work():
        try:
            ocr = WindowsOcr()
            out["lines"] = ocr.read_lines(panel, 2.0)
        except Exception as e:  # noqa: BLE001
            out["err"] = repr(e)

    th = threading.Thread(target=work)
    th.start()
    th.join(30)
    check("OCR ran", "lines" in out, out.get("err", ""))
    lines = out.get("lines", [])
    print("     read:", lines)
    db = QuestDB("t", [Quest("Gather Ferns", [(0, 0)]), Quest("Explore the Waterfall", [(0, 0)]),
                       Quest("Gather Moss", [(0, 0)])])
    found = ActiveQuestTracker(db).detect(lines)
    check("found the two listed quests", found == {"Gather Ferns", "Explore the Waterfall"}, str(found))


def test_real_hud() -> None:
    print("real game HUD screenshot (1920x1200)")
    from pathlib import Path

    from potoverlay.locator import imread_any
    from potoverlay.ocr import WindowsOcr

    shot = imread_any(Path(__file__).parent / "fixtures" / "hud_1920x1200.png")
    out: dict = {}
    th = threading.Thread(target=lambda: out.update(lines=WindowsOcr().read_lines(shot[385:1005, 1540:1900], 2.0)))
    th.start()
    th.join(30)
    lines = out.get("lines", [])
    print("     read:", lines)
    db = QuestDB("t", [Quest(n, [(0, 0)]) for n in
                       ("Milk Cap Mushrooms", "Cicada Molt", "Glow Starfish", "Bones", "Rock Moss", "Ferns")])
    found = ActiveQuestTracker(db).detect(lines)
    want = {"Milk Cap Mushrooms", "Cicada Molt", "Glow Starfish", "Bones"}
    check("objective items found, decoys not", found == want, str(found))
    check("red titles readable", any("sand rings" in normalize(l) for l in lines))
    # The game removes finished objectives, so anything listed counts, whatever its (often
    # misread) counter says: "3/15" read as "3/1 5" must not look finished.
    check("listed objective counts even if its counter looks finished",
          ActiveQuestTracker(db).detect(["- Collect 15 Cicada Molt 3/1 5"]) == {"Cicada Molt"})


def test_hud_1080() -> None:
    from pathlib import Path

    from potoverlay import config as C
    from potoverlay.locator import MapLocator, build_minimap_mask, imread_any
    from potoverlay.ocr import WindowsOcr

    shot_path = Path(__file__).parent / "fixtures" / "hud_1920x1080.png"
    if not shot_path.exists() or not C.quest_file("riparia").exists():
        return
    print("1920x1080 HUD (built-in layout, no calibration)")
    cfg = C.load_config()
    cfg["layouts"] = {}  # test the built-in preset, not a personal calibration
    check("layout found for 1920x1080", C.apply_layout(cfg, 1920, 1080))
    shot = imread_any(shot_path)
    q, mm = cfg["quest_panel"], cfg["minimap"]
    out: dict = {}
    th = threading.Thread(target=lambda: out.update(
        lines=WindowsOcr().read_lines(shot[q["y"]:q["y"] + q["h"], q["x"]:q["x"] + q["w"]], 2.0)))
    th.start()
    th.join(30)
    db = QuestDB.load(C.quest_file("riparia"), "riparia")
    idx = db.by_key()
    names = {idx[k].name for g in ActiveQuestTracker(db).detect_groups(out.get("lines", [])) for k in g}
    check("quest list read over bright ground", "Ant Eater" in names, f"{names} from {out.get('lines')}")
    loc = MapLocator(C.map_image_path("riparia"), 2048, C.CACHE_DIR)
    mask = build_minimap_mask(mm["w"], mm["h"], "circle", 0.18, 0.1)
    fix = None
    for _ in range(5):
        fix = fix or loc.locate(shot[mm["y"]:mm["y"] + mm["h"], mm["x"]:mm["x"] + mm["w"]], mask, 2.0)
    check("minimap found at 1080p", fix is not None)
    if fix is not None:
        from potoverlay.app import pick_nearest
        player = loc.work_to_full(fix.apply([[mm["w"] / 2, mm["h"] / 2]]))[0]
        chosen = [q for q in pick_nearest(ActiveQuestTracker(db).detect_groups(out.get("lines", [])), idx, player)
                  if q.name == "Ant Eater"]
        check("Ant Eater resolved to Black Fern Hills (where the HUD says we are)",
              bool(chosen) and chosen[0].poi == "Black Fern Hills", chosen[0].poi if chosen else "")


def test_hud_hires() -> None:
    """1440p and 4K screenshots (Gondwa): positions come from the general HUD scaling rule."""
    from pathlib import Path

    from potoverlay import config as C
    from potoverlay.locator import MapLocator, build_minimap_mask, imread_any
    from potoverlay.ocr import WindowsOcr

    shots = sorted((Path(__file__).parent / "fixtures").glob("hud_*x*.png"))
    shots = [p for p in shots if imread_any(p).shape[0] > 1300]
    if not shots or not C.map_image_path("gondwa"):
        return
    db = QuestDB.load(C.quest_file("gondwa"), "gondwa")
    idx = db.by_key()
    loc = MapLocator(C.map_image_path("gondwa"), 2048, C.CACHE_DIR)
    for p in shots:
        img = imread_any(p)
        h, w = img.shape[:2]
        print(f"{w}x{h} HUD (scaling rule, no preset)")
        cfg = C.load_config()
        cfg["layouts"] = {}
        C.apply_layout(cfg, w, h)
        mm, q = cfg["minimap"], cfg["quest_panel"]
        mask = build_minimap_mask(mm["w"], mm["h"], "circle", 0.18, 0.1)
        loc.reset()
        loc.scale_hint = None
        fix = None
        for _ in range(6):
            fix = fix or loc.locate(img[mm["y"]:mm["y"] + mm["h"], mm["x"]:mm["x"] + mm["w"]], mask,
                                    cfg["minimap_upscale"] * 1080 / h)
        check("minimap found on Gondwa", fix is not None)
        out: dict = {}
        th = threading.Thread(target=lambda: out.update(lines=WindowsOcr().read_lines(
            img[q["y"]:q["y"] + q["h"], q["x"]:q["x"] + q["w"]], C.ocr_upscale_for(cfg, w, h))))
        th.start()
        th.join(30)
        lines = out.get("lines", [])
        t = ActiveQuestTracker(db)
        names = sorted({idx[k].name for g in t.update(lines) for k in g})
        check("Titan's Pass and Gather Clams read", {"Titan's Pass", "Gather Clams"} <= set(names), str(names))
        # A later read where the clam quest's title is garbled must not change what it is.
        garbled = [("G@th3r C|ams" if "gather" in normalize(l) and "clam" in normalize(l) else l) for l in lines]
        names2 = sorted({idx[k].name for g in t.update(garbled) for k in g})
        check("clam quest stays 'Gather Clams' when its title misreads", names2 == names, str(names2))


def test_map_detection() -> None:
    from pathlib import Path

    from potoverlay import config as C
    from potoverlay.locator import MapLocator, build_minimap_mask, imread_any
    from potoverlay.mapdetect import MultiMapTracker

    if not all(C.map_image_path(m) for m in ("gondwa", "panjura", "riparia")):
        print("maps not downloaded; skipping map detection")
        return
    print("automatic map detection (screenshot is Riparia)")
    shot = imread_any(Path(__file__).parent / "fixtures" / "hud_1920x1200.png")
    crop = shot[77:77 + 274, 1625:1625 + 274]
    mask = build_minimap_mask(274, 274, "circle", 0.18, 0.1)
    cache: dict = {}

    def make(n):
        if n not in cache:
            cache[n] = MapLocator(C.map_image_path(n), 2048, C.CACHE_DIR)
        cache[n].reset()
        return cache[n]

    t = MultiMapTracker(["gondwa", "panjura", "riparia"], "gondwa", make, search_after_s=0.5)
    now, steps, switched_to = 0.0, 0, None
    while steps < 40 and switched_to is None:
        name, loc, fix, sw = t.step(crop, mask, 2.0, now)
        if sw:
            switched_to = name
        now += 0.1
        steps += 1
    check("switched from Gondwa to Riparia", switched_to == "riparia", f"{switched_to} after {steps} frames")
    check("switch took under 2 s", steps <= 20, f"{steps} frames at 10 fps")

    blank = np.full_like(crop, 35)
    noise = np.random.default_rng(3).integers(0, 255, crop.shape, dtype=np.uint8)
    t2 = MultiMapTracker(["gondwa", "panjura", "riparia"], "riparia", make, search_after_s=0.0)
    bad = [n for i in range(30) for n, _, _, sw in [t2.step(blank if i % 2 else noise, mask, 2.0, i * 0.1)] if sw]
    check("menus / loading screens never switch the map", not bad, str(bad))


def test_overlay_hidden_from_capture() -> None:
    print("overlay is invisible to screen capture")
    from PySide6.QtWidgets import QApplication

    from potoverlay.capture import ScreenGrabber
    from potoverlay.config import DEFAULTS
    from potoverlay.markers import Marker, OverlayState
    from potoverlay.overlay import PAD, Overlay

    app = QApplication.instance() or QApplication(sys.argv)
    cfg = dict(DEFAULTS)
    cfg["minimap"] = {"x": 200, "y": 200, "w": 160, "h": 160, "shape": "circle"}
    grab = ScreenGrabber(1)
    region = {"x": 200 - PAD, "y": 200 - PAD, "w": 160 + 2 * PAD, "h": 160 + 2 * PAD}
    before = grab.grab(region).astype(np.int16)
    ov = Overlay(cfg, grab.monitor)
    markers = [Marker(x, y, "#ff00ff", "t", True) for x in range(20, 160, 20) for y in range(20, 160, 20)]
    ov.set_state(OverlayState(True, True, markers, "test"))
    ov.show()
    t_end = time.time() + 0.8
    while time.time() < t_end:
        app.processEvents()
        time.sleep(0.02)
    after = grab.grab(region).astype(np.int16)
    visible_on_screen = ov.isVisible()
    ov.close()
    app.processEvents()
    magenta = np.sum((after[:, :, 2] > 200) & (after[:, :, 0] > 200) & (after[:, :, 1] < 60))
    diff = float(np.mean(np.abs(after - before)))
    check("overlay window shown", visible_on_screen)
    check("markers not in capture", magenta < 20, f"{magenta} magenta px, mean diff {diff:.2f}")


if __name__ == "__main__":
    test_matching()
    test_markers()
    test_ocr()
    test_real_hud()
    test_potservers_catalog()
    test_hud_1080()
    test_hud_hires()
    test_map_detection()
    test_overlay_hidden_from_capture()
    print("\nALL PASSED" if failures == 0 else f"\n{failures} FAILED")
    sys.exit(1 if failures else 0)
