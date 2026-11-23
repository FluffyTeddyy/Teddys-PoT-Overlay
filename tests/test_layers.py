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
        legend = layer_legend(load_layers("riparia", saved()))
        lines = [e[0] for e in legend]
        check("legend: one line per item, each in its own colour",
              sorted(lines) == ["Ant hills", "Fish", "Grubs", "Termite mounds", "Wasp nests"]
              and len({e[1] for e in legend}) == 5, str(legend))
        from potoverlay.markers import MIN_COLOR_GAP, _lab
        labs = [_lab(e[1]) for e in legend]
        gap = min(float(np.linalg.norm(a - b)) for i, a in enumerate(labs) for b in labs[i + 1:])
        check("...that can be told apart", gap >= MIN_COLOR_GAP, f"closest pair {gap:.0f}")

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
    from potoverlay.app import item_colors
    from potoverlay.markers import _rgb, assign_colors
    names = sorted(n for _, n in picked)
    cols = assign_colors(names, item_colors(picked))
    check("both items listed: own legend line and colour each",
          names == ["Chanterelle Mushroom", "Death Cap"] and len(set(cols.values())) == 2, f"{names} {cols}")
    from potoverlay.markers import item_color
    r, g, b = _rgb(cols.get("Chanterelle Mushroom", "#000000"))
    check("...in the items' own colours (chanterelle orange)", r > g > b
          and cols.get("Death Cap") == item_color("deathcaps"), str(cols))
    check("their spawns don't overlap", not set(picked[0][0].points) & set(picked[1][0].points))
    w = TrackerWorker.__new__(TrackerWorker)
    w._layers, w._hoard, w._hoard_cache = [], None, (None, None)
    w.cfg = {"layers": []}
    w._load_layers("riparia")
    w.set_active(t.labeled_objectives)
    lay = w._shown_layers()
    check("Hoard: Scavenger: every item of its list is shown", len(lay) == 1 and len(lay[0].points) > 1000
          and lay[0].kind.label == "Hoard: Scavenger items", str([(l.kind.label, len(l.points)) for l in lay]))
    w.set_active([it for it in t.labeled_objectives if it[2] != HOARD_KEY])
    check("Hoard over: its items go", w._shown_layers() == [])

    # Other Hoard events (real reads): their own items, not the Scavenger list.
    from potoverlay.layers import _map_data
    names = {m["id"]: m["name"] for m in _map_data("riparia")["markers"]}
    gem = ["Quests", "Hoard;Gemstones", "—.Collect as much as you can", "7:38",
           "Citrifie'Quartz, Opal Chunk, Amber,", "BloodstonesoRearl, Emerald, Obsidian Chunk", "Volcano Islands",
           "Time Left: 29:55e", "Find Location: Volcano Islands", "Sticky Tar", "- Collect 25 sticky Tar 18/25"]
    g = ActiveQuestTracker(db)
    g.update(gem)
    w.set_active(g.labeled_objectives)
    lay = w._shown_layers()
    gem_kinds = {"quartz", "opal", "amber", "blood_stone", "pearl", "emerald", "obsidian"}
    from potoverlay.layers import load_layers  # noqa: F401
    import numpy as np
    raw = {m["id"]: np.asarray(m["points"]).reshape(-1, 2) * 4096 / 500 for m in _map_data("riparia")["markers"]
           if m.get("points")}
    expect = sum(len(raw[k]) for k in gem_kinds if k in raw)
    check("Hoard: Gemstones (garbled title) shows the 7 gemstones, nothing else",
          len(lay) == 1 and lay[0].kind.label == "Hoard: Gemstones items" and len(lay[0].points) == expect,
          f"{[(l.kind.label, len(l.points)) for l in lay]}, expected {expect}")
    labs = [lab for _, lab, _ in g.labeled_objectives]
    check("'Collect as much as you can' and the item list aren't read as quests",
          not any("collect" in x.lower() or "quartz" in x.lower() or "amber" in x.lower() for x in labs), str(labs))
    from potoverlay.layers import hoard_kinds
    beach = hoard_kinds(_map_data("riparia")["markers"], ["Starfish", "Fish Bones", "Wind Blown Branches", "Clam"])
    check("names match exactly-ish: Starfish/Fish Bones/Wind Blown Branches aren't Glow Starfish/Bones/Branches",
          beach == ["starfish", "fish_bones", "wind_blown_branches", "clams"], str([names.get(k) for k in beach]))
    odd = ["Quests", "Hoard: Lumberjack", "Collect as much as you can", "Branches, Wind Blown Branches,",
           "Peeling Bark, Charred Wood"]
    o = ActiveQuestTracker(db)
    o.update(odd)
    w.set_active(o.labeled_objectives)
    lay = w._shown_layers()
    check("a Hoard type we don't know: its items are read off the HUD",
          len(lay) == 1 and lay[0].kind.label == "Hoard: Lumberjack items", str([l.kind.label for l in lay]))
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
    check("riparia: a delta arm by Cedrus Forest is sea, not fresh", not inside(rip, 1318, 1883))
    check("riparia: the river down to the SW corner is fresh", inside(rip, 339, 3828))
    check("gondwa: snow isn't water", not inside(gondwa, 2985, 1756))
    # Ponds checked in game (you could drink there), though the map image barely shows them.
    near = [any(inside(rip, x + dx, y + dy) for dx in range(-10, 11, 2) for dy in range(-10, 11, 2))
            for x, y in ((2476, 1120), (1978, 3560), (2050, 3438))]
    check("riparia: the ponds checked in game are marked (Volcano Islands, 2 in Hollow Hills craters)",
          all(near), str(near))

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


def test_item_colors() -> None:
    print("markers coloured like the item")
    from potoverlay.layers import BY_ID
    from potoverlay.markers import MIN_COLOR_GAP, _lab, _rgb, assign_colors, item_color
    r, g, b = _rgb(item_color("pinecone"))
    check("pinecones are green", g > r and g > b, item_color("pinecone"))
    r, g, b = _rgb(item_color("berry"))
    check("berries are red", r > g and r > b, item_color("berry"))
    check("food layers use the item's colour", BY_ID["pinecone"].color == item_color("pinecone"))
    cols = assign_colors(["Death Cap", "Pinecone", "Gather Clams"],
                         {"Death Cap": item_color("deathcaps"), "Pinecone": item_color("pinecone")})
    labs = [_lab(c) for c in cols.values()]
    gap = min(float(np.linalg.norm(a - b)) for i, a in enumerate(labs) for b in labs[i + 1:])
    check("two green items at once still look different", gap >= MIN_COLOR_GAP, f"{cols} gap {gap:.0f}")


def test_spawn_fix() -> None:
    print("spawn positions corrected per map (F10 reports)")
    from potoverlay.spawnfix import to_image
    corner = to_image("riparia", [[10, 490]])[0]  # SW corner: pulled in towards the middle
    plain = np.array([10, 490]) * 4096 / 500
    check("Riparia's corner spawns move inwards ~20-35 m", corner[0] > plain[0] + 15 and corner[1] < plain[1] - 15,
          f"{plain} -> {corner.round(1)}")
    mid = to_image("riparia", [[2185 * 500 / 4096, 1782 * 500 / 4096]])[0]
    check("no change at the correction's centre", np.allclose(mid, [2185, 1782]), str(mid))
    other = to_image("gondwa", [[10, 490]])[0]
    check("maps without a measured correction keep the plain conversion", np.allclose(other, plain), str(other))
    berry = next(m for m in json.loads((C.DATA_DIR / "potservers" / "map_riparia.json").read_text(encoding="utf-8"))
                 ["data"]["markers"] if m["id"] == "berry")["points"]
    lay = load_layers("riparia", ["berry"])
    check("food layers use the corrected positions", lay and np.allclose(lay[0].points, to_image("riparia", berry)))


def test_quest_places() -> None:
    print("quests whose place Potservers names differently, or with no items near it")
    from potoverlay.app import pick_labeled
    from potoverlay.quests import QuestDB

    db = QuestDB.load(C.quest_file("riparia"), "riparia")
    swamp = (3060.0, 2700.0)  # Coastal Swamp ("Coastland Swamp" in the quest data)
    lines = ["Quests", "Wetland Reeds", "- Collect 30 Reeds 4/30"]
    for _ in range(2):
        act = _tracked(db, lines + lines[1:])
    shown = {name: q for q, name in pick_labeled(act, db.by_key(), swamp)}
    reeds = shown.get("Reeds")
    check("Wetland Reeds at Coastland Swamp has reed markers", reeds is not None and reeds.poi == "Coastland Swamp"
          and len(reeds.points) > 50, str({k: (v.poi, len(v.points)) for k, v in shown.items()}))

    bog = (1979.0, 2441.7)  # Stillwater Bog
    lines = ["Quests", "Gifts from the Sea", "- Collect 30 Starfish 0/30", "- Collect 25 Conch 0/25"]
    act = _tracked(db, lines)
    act = _tracked(db, lines)
    shown = {name: q for q, name in pick_labeled(act, db.by_key(), bog)}
    far = [n for n, q in shown.items() if q.poi == "Stillwater Bog" and q.points and
           max(np.hypot(*(np.asarray(q.points) - bog).T)) > 1000]  # ~1.3 km: across the map
    check("Stillwater Bog's Gifts from the Sea: no markers far outside the bog", not far and "Conch" in shown,
          str({k: (v.poi, len(v.points)) for k, v in shown.items()}))

    # Quest items within minimap reach are shown wherever they are, even outside the quest's POI.
    from potoverlay.layers import spawn_index
    spawns = spawn_index("riparia")
    star = spawns["starfish"]
    me = star[int(np.argmin(np.hypot(*(star - bog).T)))]  # the starfish nearest the bog, outside it
    reach = 300.0

    def nearby(kinds):
        return [tuple(p) for k in kinds for p in spawns.get(k, [])
                if np.hypot(p[0] - me[0], p[1] - me[1]) <= reach]
    shown = {name: q for q, name in pick_labeled(act, db.by_key(), me, nearby)}
    stars = np.asarray(shown["Starfish"].points) if "Starfish" in shown else np.zeros((0, 2))
    want = int((np.hypot(*(star - me).T) <= reach).sum())
    close = int((np.hypot(*(stars - me).T) <= reach).sum())
    check("every starfish within minimap reach is shown, POI or not", want > 1 and close == want, f"{close} of {want}")
    gaps = np.hypot(*(stars[:, None] - stars[None]).transpose(2, 0, 1)) + np.eye(len(stars)) * 1e9
    check("...once each", len(stars) and gaps.min() >= 1.0)

    # Big map toggle (default off): every spawn of the needed item, anywhere on the map.
    from potoverlay import config as Cfg
    from potoverlay.app import TrackerWorker
    check("big map: all spawns is off by default", Cfg.DEFAULTS.get("map_all_item_spawns") is False)
    w = TrackerWorker.__new__(TrackerWorker)
    w._spawns, w._near_radius = spawns, reach
    near_only = {n: len(q.points) for q, n in pick_labeled(act, db.by_key(), me, w._near(me))}
    everywhere = {n: q for q, n in pick_labeled(act, db.by_key(), me, w._near(me, True))}
    check("...and when on, every starfish on the map is shown", len(everywhere["Starfish"].points) >= len(star)
          and len(everywhere["Starfish"].points) > near_only["Starfish"],
          f"{near_only['Starfish']} near -> {len(everywhere['Starfish'].points)} of {len(star)}")


def test_underwater() -> None:
    print("hide underwater items (can't dive)")
    from potoverlay import underwater
    from potoverlay.app import TrackerWorker, pick_labeled
    from potoverlay.layers import spawn_index
    from potoverlay.quests import QuestDB

    spawns = spawn_index("gondwa")
    uw = underwater.load("gondwa")
    share = {k: float(uw.mask(spawns[k]).mean()) for k in ("sea_anemone", "starfish", "lake_weed", "berry", "roots")}
    check("seabed items are underwater (anemones, starfish), floating and land ones aren't",
          share["sea_anemone"] > 0.8 and share["starfish"] > 0.8 and share["lake_weed"] < 0.05
          and share["berry"] == 0 and share["roots"] < 0.01, str({k: round(v, 2) for k, v in share.items()}))

    db = QuestDB.load(C.quest_file("riparia"), "riparia")
    lines = ["Quests", "Gifts from the Sea", "- Collect 15 Conch 0/15", "- Collect 15 Starfish 0/15",
             "- Collect 15 Anemone 0/15"]
    act = _tracked(db, lines)
    act = _tracked(db, lines)
    here = (2700.0, 600.0)  # Kelp Vale
    shown = pick_labeled(act, db.by_key(), here)
    w = TrackerWorker.__new__(TrackerWorker)
    w._underwater = underwater.load("riparia")
    w.cfg = {"hide_underwater": False}
    same = w._dry(shown)
    w.cfg = {"hide_underwater": True}
    dry = {n: len(q.points) for q, n in w._dry(shown)}
    full = {n: len(q.points) for q, n in shown}
    check("off (default): nothing hidden", [len(q.points) for q, _ in same] == [len(q.points) for q, _ in shown]
          and C.DEFAULTS.get("hide_underwater") is False)
    check("on: Gifts from the Sea loses its deep anemones and starfish",
          dry.get("Anemone", 0) < full.get("Anemone", 0) and dry.get("Starfish", 0) < full.get("Starfish", 0),
          f"{full} -> {dry}")


def test_nesting() -> None:
    print("nesting mode: nest materials")
    from potoverlay.app import TrackerWorker
    from potoverlay.layers import NEST_KINDS, _map_data, hoard_kinds, layer_legend
    w = TrackerWorker.__new__(TrackerWorker)
    w._layers, w._hoard, w._hoard_cache = [], None, (None, None)
    w.cfg = {"layers": ["roots", "berry"], "nesting_mode": False}
    w._load_layers("riparia")
    check("off (default): only the food layers", C.DEFAULTS.get("nesting_mode") is False
          and sorted(l.kind.id for l in w._shown_layers()) == ["berry", "roots"])
    w.cfg["nesting_mode"] = True
    shown = w._shown_layers()
    ids = [l.kind.id for l in shown]
    check("on: all seven nest materials, roots once (not again as food)",
          set(ids) == NEST_KINDS | {"berry"} and ids.count("roots") == 1 and all(len(l.points) > 50 for l in shown),
          str({l.kind.id: len(l.points) for l in shown}))
    nest = [l for l in shown if l.kind.group == "Nesting"]
    check("each material told apart by colour or shape", len({(l.kind.color, l.kind.style) for l in nest}) == 7
          and len({l.kind.style for l in nest}) >= 4, str([(l.kind.label, l.kind.color, l.kind.style) for l in nest]))
    lines = [e[0] for e in layer_legend(shown)]
    check("a legend line per material", all(x in lines for x in ("Bones", "Branches", "Rocks", "Mud", "Roots", "Reeds", "Kelp")),
          str(lines))
    from potoverlay.layers import HOARD_LISTS
    kinds = set(hoard_kinds(_map_data("riparia")["markers"], HOARD_LISTS["Nesting Materials"]))
    check("Hoard: Nesting Materials finds the same seven items", kinds == NEST_KINDS, str(sorted(kinds)))

    # The nest quest, read off the HUD (as in a screenshot), next to a normal quest.
    from potoverlay.app import pick_labeled
    from potoverlay.quests import NEST_KEY, QuestDB
    db = QuestDB.load(C.quest_file("riparia"), "riparia")
    lines = ["Quests", "Finish Nest Construction", "Finish constructing your nest!",
             "Gather Mushrooms", "- Collect 15 Death Cap 3/15"]
    act = _tracked(db, lines)
    act = _tracked(db, lines)
    keys = [k for _, _, k in act]
    check("'Finish Nest Construction' is recognised, the other quest too",
          NEST_KEY in keys and "collect:death cap" in keys, str([(n, k) for _, n, k in act]))
    w.cfg["nesting_mode"] = False
    w.set_active(act)
    ids = {l.kind.id for l in w._shown_layers()}
    check("while it's listed: nest materials shown without nesting mode", NEST_KINDS <= ids, str(sorted(ids)))
    shown = [n for _, n in pick_labeled(act, db.by_key(), (1979.0, 2441.7))]
    check("...and it isn't drawn as a quest of its own", shown == ["Death Cap"], str(shown))
    w.set_active([a for a in act if a[2] != NEST_KEY])
    left = [l.kind.id for l in w._shown_layers()]
    check("quest done (gone from the list): nest materials go too (roots stays: it's on under Food)",
          not any(l.kind.group == "Nesting" for l in w._shown_layers()) and sorted(left) == ["berry", "roots"], str(left))


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
    test_item_colors()
    test_spawn_fix()
    test_quest_places()
    test_underwater()
    test_nesting()
    print("\nALL PASSED" if failures == 0 else f"\n{failures} FAILED")
    sys.exit(1 if failures else 0)
