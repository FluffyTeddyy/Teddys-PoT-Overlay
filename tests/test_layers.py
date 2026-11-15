"""Always-on layers (fish spawns etc.): data, placement, and the tray toggle.

    python -m tests.test_layers
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from potoverlay import config as C  # noqa: E402
from potoverlay.layers import available_kinds, load_layers  # noqa: E402
from potoverlay.locator import imread_any  # noqa: E402
from potoverlay.markers import layout_layer  # noqa: E402

failures = 0


def check(name, cond, detail=""):
    global failures
    print(("  ok   " if cond else "  FAIL ") + name + (f"  ({detail})" if detail else ""))
    failures += not cond


def test_data() -> None:
    print("fish spawn data")
    counts = {m: len(next(iter(load_layers(m, ["fish_spawn"])), None).points) for m in ("riparia", "gondwa", "panjura")}
    check("fish spawns on all three maps", all(v > 100 for v in counts.values()), str(counts))
    check("krill only where the data has it", "krill" in available_kinds("gondwa")
          and "krill" not in available_kinds("panjura"))
    # Fish spawn in water: on the map image, water is blue-ish (more blue than red).
    for m in ("riparia", "gondwa"):
        img = imread_any(C.map_image_path(m))
        pts = load_layers(m, ["fish_spawn"])[0].points.astype(int)
        blur = cv2.blur(img, (9, 9))  # small area around each point, not one pixel
        px = blur[np.clip(pts[:, 1], 0, img.shape[0] - 1), np.clip(pts[:, 0], 0, img.shape[1] - 1)].astype(int)
        water = float(np.mean(px[:, 0] > px[:, 2]))
        check(f"{m}: fish spawn points are on water", water > 0.8, f"{water:.0%} on blue")


def test_layout() -> None:
    print("placement on the minimap")
    pts = np.array([[100, 100], [5, 5], [272, 137], [137, 20]])  # inside, corner, on the rim, inside
    mk = layout_layer(pts, 274, 274, "circle", 7, "Fish", "#4fc3ff", "fish")
    check("only points inside the round minimap", [(m.x, m.y) for m in mk] == [(100, 100), (137, 20)],
          str([(m.x, m.y) for m in mk]))
    check("drawn as fish, no edge arrows", all(m.style == "fish" and m.inside for m in mk))


def test_no_needless_repaints() -> None:
    print("in-game map: no repainting while it sits still")
    from PySide6.QtWidgets import QApplication

    from potoverlay.markers import Marker, OverlayState
    from potoverlay.overlay import MapScreenOverlay

    QApplication.instance() or QApplication(sys.argv)
    ov = MapScreenOverlay(C.load_config(), {"left": 0, "top": 0, "width": 800, "height": 600})
    updates = []
    ov.update = lambda: updates.append(1)
    rng = np.random.default_rng(0)
    base = rng.uniform(50, 550, (3000, 2))

    def state(jitter=0.0, shift=0.0):
        pts = base + rng.uniform(-jitter, jitter, base.shape) + shift
        return OverlayState(False, True, [Marker(x, y, "#4fc3ff", "Fish", True, style="fish") for x, y in pts])
    ov.set_state(state())
    n0 = len(updates)
    for _ in range(12):  # one second of updates, map still (tracking jitters by a fraction of a pixel)
        ov.set_state(state(jitter=0.2))
    check("still map: no repaints", len(updates) - n0 <= 2, f"{len(updates) - n0} of 12")
    ov.set_state(state(shift=15))
    check("panned map: repaints", len(updates) - n0 >= 1)


def test_toggle() -> None:
    print("tray toggle")
    from PySide6.QtWidgets import QApplication

    tmp = Path(tempfile.mkdtemp(prefix="potoverlay_layers_"))
    cfg = json.loads(C.CONFIG_PATH.read_text(encoding="utf-8"))
    cfg.update(toggle_hotkey="", refresh_hotkey="", check_for_data_updates=False, layers=[], fish_buffs=[],
               fresh_water=False, active_map="riparia")
    (tmp / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    C.CONFIG_PATH = tmp / "config.json"
    qapp = QApplication.instance() or QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    from potoverlay.app import App
    app = App(qapp)

    def group_menu(name):
        food = next(a.menu() for a in app.menu.actions() if a.menu() and a.text() == "Food")
        return food, next(a.menu() for a in food.actions() if a.menu() and a.text().startswith(name))

    def saved():
        return sorted(json.loads(C.CONFIG_PATH.read_text(encoding="utf-8")).get("layers") or [])

    try:
        food, fish = group_menu("Fish")
        groups = [a.text() for a in food.actions() if a.menu()]
        check("Food menu has the food groups", groups[:3] == ["Fish", "Shellfish", "Meat"] and len(groups) == 8,
              str(groups))
        items = {a.text(): a for a in fish.actions() if a.text()}
        check("Fish group: 'All' plus each item", list(items) == ["All fish", "Fish", "Diseased fish",
                                                                 "Krill feeding areas"], str(list(items)))
        items["Fish"].setChecked(True)  # what clicking it does
        end = time.time() + 5
        while time.time() < end and not app.tracker._layers:
            qapp.processEvents()
            time.sleep(0.05)
        check("turning one item on is saved", saved() == ["fish_spawn"], str(saved()))
        check("overlay loads it", [l.kind.id for l in app.tracker._layers] == ["fish_spawn"])
        food, fish = group_menu("Fish")
        check("menu shows it ticked, group shows the count", any(a.text() == "Fish" and a.isChecked()
              for a in fish.actions()) and any(a.text() == "Fish (1 on)" for a in food.actions()))

        _, insects = group_menu("Insects")
        next(a for a in insects.actions() if a.text() == "All insects").setChecked(True)
        qapp.processEvents()
        check("'All insects' turns on the whole group", saved() == sorted(
            ["fish_spawn", "ant_hill", "termites", "grubby_log", "wasp_nest"]), str(saved()))
        from potoverlay.layers import layer_legend, load_layers
        lines = [e[0] for e in layer_legend(load_layers("riparia", saved()))]
        check("legend: one line per group", sorted(lines) == ["Fish", "Insects (4)"], str(lines))

        food, _ = group_menu("Fish")
        next(a for a in food.actions() if a.text() == "Hide all food").trigger()
        qapp.processEvents()
        check("'Hide all food' clears everything", saved() == [], str(saved()))

        food, _ = group_menu("Fish")
        water = next(a for a in food.actions() if a.text().startswith("Fresh water"))
        water.setChecked(True)
        end = time.time() + 10
        while time.time() < end and getattr(app.tracker, "_fresh", None) is None:
            qapp.processEvents()
            time.sleep(0.05)
        check("Fresh water toggle: saved and loaded",
              json.loads(C.CONFIG_PATH.read_text(encoding="utf-8")).get("fresh_water") is True
              and app.tracker._fresh is not None and len(app.tracker._fresh) > 0)
        check("legend line for it", ("Fresh water", "#3fa9ff", "zone") in app.tracker._legend({}))

        app.cfg["active_map"] = "panjura"
        app._build_menu()
        _, fish = group_menu("Fish")
        check("items missing on this map are marked", any(a.text() == "Krill feeding areas (not on this map)"
                                                          for a in fish.actions()))

        print("fish buffs")

        def buff_menu():
            return next(a.menu() for a in app.menu.actions() if a.menu() and a.text().startswith("Fish buffs"))
        acts = {a.text(): a for a in buff_menu().actions() if a.isCheckable()}
        check("one toggle per fish", len(acts) >= 10 and "Pike - Stamina recovery up" in acts, str(list(acts)))
        restarts = []
        app.restart = lambda: restarts.append(1)  # toggling a buff must not restart the tracking
        acts["Pike - Stamina recovery up"].setChecked(True)
        acts["Arapaima - +20% Bleed healing & health recovery (2 min)"].setChecked(True)
        qapp.processEvents()
        check("saved", json.loads(C.CONFIG_PATH.read_text(encoding="utf-8"))["fish_buffs"] == ["pike", "arapaima"])
        check("shown next to the minimap and on the in-game map",
              [n[0] for n in app.overlay.notes] == ["Pike - Stamina recovery up",
                                                    "Arapaima - +20% Bleed healing & health recovery (2 min)"]
              and app.map_overlay.notes == app.overlay.notes, str(app.overlay.notes))
        check("no restart", not restarts)
        check("overlay widened for the long line", app.overlay.ox > 48 + 200, str(app.overlay.ox))
        check("menu shows the count", buff_menu() is not None and buff_menu().title() == "Fish buffs (2 shown)",
              buff_menu().title())
        next(a for a in buff_menu().actions() if a.text() == "Hide all fish buffs").trigger()
        qapp.processEvents()
        check("'Hide all fish buffs'", app.overlay.notes == [] and app.overlay.ox == 48 + 200)
    finally:
        app.shutdown()


def test_fish_buff_file() -> None:
    print("fish buff values file")
    from potoverlay import fishbuffs
    old = C.DATA_DIR
    C.DATA_DIR = Path(tempfile.mkdtemp(prefix="potoverlay_buffs_"))
    try:
        check("built-in list without a file", fishbuffs.load() == fishbuffs.defaults())
        fishbuffs.write_file()
        data = json.loads(fishbuffs.path().read_text(encoding="utf-8"))
        next(r for r in data["fish"] if r["id"] == "pike").update(amount="+20%", effect="Stamina recovery")
        fishbuffs.path().write_text(json.dumps(data), encoding="utf-8")
        check("your numbers are used", fishbuffs.notes(["pike"])[0][0] == "Pike - +20% Stamina recovery",
              str(fishbuffs.notes(["pike"])))
        fishbuffs.path().write_text("{ broken", encoding="utf-8")
        check("broken file: built-in list", fishbuffs.load() == fishbuffs.defaults())
    finally:
        C.DATA_DIR = old


def test_hud_objectives_and_hoard() -> None:
    """From a real session: Gather Mushrooms at Stillwater Bog with only Death Cap left on the HUD
    (chanterelles done), and the Hoard: Scavenger event above it, read as noisily as it was."""
    print("only the HUD's objectives get markers; Hoard event")
    from potoverlay.app import TrackerWorker, pick_labeled
    from potoverlay.quests import HOARD_KEY, ActiveQuestTracker, QuestDB

    db = QuestDB.load(C.quest_file("riparia"), "riparia")
    reads = [
        ["Quests", "Hoard: Scavenger", "Tier 2", "Reward Multiplier: 1,25", "0:56",
         "•Bones, Snake Shed, Peeling Bark, Cicada Molt,", "Branches/Wind BlowntBranches*Turtle Shell/",
         "—Barnacle, Jellyfish, Coral Chunks, Snagged Fur,", "CAShark>Teeth/ Sand", "Fish Bones,",
         "Seashells, Clam, Colorful Shell, Cuttlefish Bone", "Gather Mushrooms",
         "- Collect 15 Death Cap 3/1 5", "Hollow Hills", "Time Left: 2:14", "- Find Location: Hollow Hills",
         "Alpha Critter", "Kill 6 Critters 1/6"],
        ["Quests", "Hoard: Scavenger", "Rank: 6 Score: 28", "Top: XIIX, someone, Elisopod",
         "Shark Teeth, Sand Dollars, Fish Bones,", "Gather Mushrooms", "- Collect 15 Death Cap 3/15",
         "Hollow Hills", "- Find Location: Hollow Hills", "Alpha Critter", "Kill 6 Critters 1/6"],
    ]
    t = ActiveQuestTracker(db)
    for r in reads * 2:
        t.update(r)
    names = sorted({it[1] for it in t.labeled_objectives})
    check("quests read right, no item-list 'quests'",
          {"Gather Mushrooms", "Hoard: Scavenger", "Hollow Hills"} <= set(names)
          <= {"Alpha Critter", "Gather Mushrooms", "Hoard: Scavenger", "Hollow Hills"}, str(names))
    check("'3/1 5' (a misread 3/15) doesn't hide the objective", "Gather Mushrooms" in names)
    shown = {name: q for q, name in pick_labeled(t.labeled_objectives, db.by_key(), (1979.0, 2441.7))}
    check("legend says the item, not the quest", "Death Cap" in shown and "Gather Mushrooms" not in shown,
          str(list(shown)))
    gm = shown.get("Death Cap")
    full = next(q for q in db.quests if q.key == gm.key) if gm else None
    deathcaps = set(full.points_for({"collect:death cap"})) if full else set()
    check("Stillwater Bog's quest, death caps only (chanterelles are done)",
          gm is not None and gm.poi == "Stillwater Bog" and set(gm.points) == deathcaps
          and 0 < len(gm.points) < len(full.points), f"{len(gm.points) if gm else 0} of {len(full.points) if full else 0}")
    both = ActiveQuestTracker(db)
    for _ in range(2):
        both.update(["Quests", "Gather Mushrooms", "- Collect 15 Chanterelle Mushroom 4/15",
                     "- Collect 15 Death Cap 3/15"])
    picked = pick_labeled(both.labeled_objectives, db.by_key(), (1979.0, 2441.7))
    from potoverlay.markers import assign_colors
    names = sorted(n for _, n in picked)
    cols = assign_colors(names)
    check("both items listed: own legend line and colour each",
          names == ["Chanterelle Mushroom", "Death Cap"] and len(set(cols.values())) == 2, f"{names} {cols}")
    check("their spawns don't overlap", not set(picked[0][0].points) & set(picked[1][0].points))
    w = TrackerWorker.__new__(TrackerWorker)
    w._layers = []
    w._load_layers = TrackerWorker._load_layers.__get__(w)
    w.cfg = {"layers": []}
    w._load_layers("riparia")
    w.set_active(t.labeled_objectives)
    lay = w._shown_layers()
    check("Hoard: every item of its list is shown", len(lay) == 1 and len(lay[0].points) > 1000
          and any(it[2] == HOARD_KEY for it in t.labeled_objectives), str([len(l.points) for l in lay]))
    w.set_active([it for it in t.labeled_objectives if it[2] != HOARD_KEY])
    check("Hoard over: its items go", w._shown_layers() == [])
    check("'Scavenge from the Dead' is a normal quest, not the Hoard",
          not any(it[2] == HOARD_KEY for it in _tracked(db, ["Quests", "Scavenge from the Dead",
                                                              "- Collect 10 Bones 0/10"])))


def test_fresh_water() -> None:
    print("fresh water (water not connected to the ocean)")
    import cv2

    from potoverlay import freshwater as F

    def inside(fw, x, y):  # even-odd: inside a lake, not on an island in it
        return sum(cv2.pointPolygonTest(p.astype(np.float32).reshape(-1, 1, 2), (x, y), False) >= 0
                   for p in fw.polys) % 2 == 1

    for m in ("riparia", "gondwa", "panjura"):
        fw = F.load(m)
        mask = np.zeros((1024, 1024), np.uint8)
        cv2.fillPoly(mask, [(p / 4).astype(np.int32) for p in fw.polys], 1)
        share = float(mask.mean())
        check(f"{m}: some fresh water, not the sea", 0.003 < share < 0.08, f"{share:.1%} of the map, {len(fw)} areas")
    gondwa = F.load("gondwa")
    check("gondwa: the open sea is not fresh", not any(inside(gondwa, x, y) for x, y in
                                                       ((200, 200), (3900, 2000), (2000, 3950), (300, 3000))))
    rip = F.load("riparia")
    # Ponds by Black Fern Hills (seen on the map image), and a grey cliff edge next to them.
    check("riparia: Black Fern Hills ponds are fresh",
          all(inside(rip, x, y) for x, y in ((300, 1750), (650, 1990), (400, 2400))))
    check("riparia: the cliff beside them isn't", not inside(rip, 110, 2250))
    check("riparia: the delta joins the sea, so it isn't fresh", not inside(rip, 2600, 1300))

    from PySide6.QtWidgets import QApplication

    from potoverlay.markers import OverlayState
    from potoverlay.overlay import MapScreenOverlay
    QApplication.instance() or QApplication(sys.argv)
    ov = MapScreenOverlay(C.load_config(), {"left": 0, "top": 0, "width": 800, "height": 600})
    updates = []
    ov.update = lambda: updates.append(1)
    zones = [p / 6 for p in rip.polys[:40]]
    ov.set_state(OverlayState(True, True, [], "", zones=zones, clip=(0, 0, 800, 600)))
    n0 = len(updates)
    for _ in range(10):
        ov.set_state(OverlayState(True, True, [], "", zones=[z + 0.2 for z in zones], clip=(0, 0, 800, 600)))
    check("still map with water areas: no repaints", len(updates) == n0, f"{len(updates) - n0} of 10")
    ov.set_state(OverlayState(True, True, [], "", zones=[z + 12 for z in zones], clip=(0, 0, 800, 600)))
    check("panned: repaints", len(updates) > n0)


def _tracked(db, lines):
    from potoverlay.quests import ActiveQuestTracker
    t = ActiveQuestTracker(db)
    t.update(lines)
    return t.labeled_objectives


if __name__ == "__main__":
    test_data()
    test_layout()
    test_no_needless_repaints()
    test_toggle()
    test_fish_buff_file()
    test_hud_objectives_and_hoard()
    test_fresh_water()
    print("\nALL PASSED" if failures == 0 else f"\n{failures} FAILED")
    sys.exit(1 if failures else 0)
