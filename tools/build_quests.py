"""Turn downloaded Potservers data into data/quests/<map>.json for the overlay.

    python -m tools.build_quests            # all maps that have been downloaded

For each quest:
  points  = spawn points of the quest's items inside (or near) the quest's POI
  targets = where to go: the waystone, the drop-off point, or the POI itself
Quests you added yourself in the quest editor (no "source") are kept.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

from potoverlay import config as C
from potoverlay.quests import Quest, QuestDB

RAW = C.DATA_DIR / "potservers"
ATTRIBUTION = "Data from Potservers (https://potservers.net). Unofficial; Path of Titans belongs to Alderon Games."
GRID = 500.0          # Potservers map coordinates run 0..500
IMAGE_SIZE = 4096     # we download the 4096 px image
NEAR_POI = 6.0        # grid units outside the POI outline that still count as "in" it
FALLBACK_NEAREST = 25 # if no spawns in the POI, use this many nearest to it

TYPE_COLORS = {
    "gathering": "#ffd23f",
    "delivering": "#ff9f40",
    "exploration": "#3fd2ff",
    "move_to": "#3fd2ff",
    "group_meet": "#c77dff",
    "hunting": "#ff6b6b",
    "trophy_delivery": "#ff6b6b",
}


def px(pt) -> tuple[float, float]:
    return (round(float(pt[0]) * IMAGE_SIZE / GRID, 1), round(float(pt[1]) * IMAGE_SIZE / GRID, 1))


def _norm(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
    return " ".join(w[:-1] if len(w) > 3 and w.endswith("s") else w for w in s.split())


class MapData:
    def __init__(self, raw: dict):
        self.kinds = {m["id"]: np.float32(m["points"]).reshape(-1, 2) for m in raw["markers"]}
        self.kind_by_name: dict[str, str] = {}
        for m in raw["markers"]:
            self.kind_by_name[_norm(m["id"])] = m["id"]
            self.kind_by_name[_norm(m["name"])] = m["id"]
        self.regions = {r["id"]: (np.float32(r["polygon"]).reshape(-1, 1, 2), r["center"], r["name"])
                        for r in raw["regions"]}
        self.landmarks = {l["id"]: l["position"] for l in raw["landmarks"]}

    def kinds_for_item(self, item: dict) -> list[str]:
        if item.get("marker_kinds"):
            return [k for k in item["marker_kinds"] if k in self.kinds]
        for key in (item.get("id") or "", item.get("name") or ""):
            k = self.kind_by_name.get(_norm(key))
            if k:
                return [k]
        return []

    def points_near_region(self, kind: str, region_id: str | None) -> np.ndarray:
        pts = self.kinds.get(kind, np.zeros((0, 2), np.float32))
        if len(pts) == 0 or region_id not in self.regions:
            # No POI outline (e.g. "Flooded Cave" isn't in the data): every spawn on the map would be
            # useless clutter, and would make this version look "nearest" wherever you stand.
            return pts[:0]
        poly, center, _ = self.regions[region_id]
        inside = np.array([cv2.pointPolygonTest(poly, (float(x), float(y)), True) >= -NEAR_POI for x, y in pts])
        if inside.any():
            return pts[inside]
        d = np.hypot(pts[:, 0] - center[0], pts[:, 1] - center[1])
        return pts[np.argsort(d)[:FALLBACK_NEAREST]]

    def nearest_in_region(self, kind: str, region_id: str | None) -> list:
        pts = self.points_near_region(kind, region_id)
        if len(pts) == 0 or region_id not in self.regions:
            return []
        c = self.regions[region_id][1]
        return [pts[int(np.argmin(np.hypot(pts[:, 0] - c[0], pts[:, 1] - c[1])))]]


def convert(q: dict, md: MapData) -> Quest | None:
    region_id = (q.get("location") or {}).get("region_id")
    poi = (q.get("location") or {}).get("name") or ""
    points, targets, objectives = [], [], []
    per_objective: dict[str, list] = {}
    for t in q["tasks"]:
        ttype, count = t["type"], t.get("count")
        items = {(i.get("id"), i.get("name")): i for i in (t.get("items") or [])}.values()  # drop diet duplicates
        for it in items:
            name = it.get("name") or t.get("name") or ""
            n = it.get("count") or count
            obj = None  # this item's objective text, as the HUD shows it
            if ttype == "gather" and name and n:
                obj = f"Collect {n} {name}"
            elif ttype == "deliver" and name and n:
                obj = f"Deliver {n} {name}"
            elif ttype == "waystone_cooldown":
                obj = f"Restore Waystone at {t.get('name') or q['name']} by Delivering {name}"
            if obj:
                objectives.append(obj)
            if ttype in ("gather", "deliver", "waystone_cooldown"):
                mine = [px(p) for k in md.kinds_for_item(it) for p in md.points_near_region(k, region_id)]
                points.extend(mine)
                if obj and mine:
                    per_objective.setdefault(obj, []).extend(mine)
        if ttype == "waystone_cooldown":
            targets.extend(px(p) for p in md.nearest_in_region("waystone", region_id))
        elif ttype == "deliver":
            targets.extend(px(p) for p in md.nearest_in_region("delivery", region_id))
        elif ttype in ("explore", "water_restore", "move_to", "group_meet"):
            for it in items:
                if it.get("id") in md.landmarks:
                    targets.append(px(md.landmarks[it["id"]]))
                elif it.get("id") in md.regions:
                    targets.append(px(md.regions[it["id"]][1]))
            if not targets and region_id in md.regions:
                targets.append(px(md.regions[region_id][1]))
    if not points and not targets:
        return None  # e.g. fishing quests: no fixed location
    center = px(md.regions[region_id][1]) if region_id in md.regions else (targets or points)[0]
    return Quest(
        name=q["name"], points=sorted(set(points)), color=TYPE_COLORS.get(q.get("type"), ""),
        id=q["id"], poi=poi, type=q.get("type") or "", objectives=list(dict.fromkeys(objectives)),
        targets=list(dict.fromkeys(targets)), center=center, source="potservers",
        objective_points={o: sorted(set(ps)) for o, ps in per_objective.items()},
    )


def build_map(map_name: str) -> int:
    map_path = RAW / f"map_{map_name}.json"
    if not map_path.exists():
        print(f"[{map_name}] not downloaded, skipping")
        return 0
    md = MapData(json.loads(map_path.read_text(encoding="utf-8"))["data"])
    built: dict[tuple, Quest] = {}
    skipped = defaultdict(int)
    # Only quests in Potservers' current list: a quest removed by a game patch may still have an
    # old downloaded file lying around.
    lst = RAW / "quests.json"
    current = {q["id"] for q in json.loads(lst.read_text(encoding="utf-8"))["data"]} if lst.exists() else None
    for f in sorted((RAW / "quests").glob("*.json")):
        if current is not None and f.stem not in current:
            continue
        q = json.loads(f.read_text(encoding="utf-8"))["data"]
        if q.get("map") != map_name or not q.get("enabled", True) or q.get("tutorial"):
            continue
        quest = convert(q, md)
        if quest is None:
            skipped[q["name"]] += 1
            continue
        # Solo and group versions of the same quest are identical on the map: keep one.
        key = (quest.name, quest.poi, tuple(quest.objectives), tuple(quest.points), tuple(quest.targets))
        built.setdefault(key, quest)
    out = C.quest_file(map_name)
    existing = QuestDB.load(out, map_name)
    mine = [q for q in existing.quests if q.source != "potservers"]
    stamp = json.loads(lst.read_text(encoding="utf-8"))["meta"].get("updated_at", "") if lst.exists() else ""
    db = QuestDB(map_name, mine + sorted(built.values(), key=lambda q: (q.poi, q.name)), attribution=ATTRIBUTION,
                 data_updated_at=stamp)
    db.save(out)
    print(f"[{map_name}] {len(built)} quests with locations -> {out.name}"
          + (f" (kept {len(mine)} of yours)" if mine else "")
          + (f"; skipped {sum(skipped.values())} without a known place (fishing, hunting, "
             f"or a POI missing from the data)" if skipped else ""))
    return len(built)


def build_all(maps=("riparia", "gondwa", "panjura")) -> None:
    for m in maps:
        build_map(m)


if __name__ == "__main__":
    build_all(tuple(sys.argv[1:]) or ("riparia", "gondwa", "panjura"))
