"""Bake fresh water into data/freshwater/<map>.json (run after tools.fetch_vulnona).

    python -m tools.build_freshwater

Outlines come from the map image: water is told from land by its colour on that map (learned
from water and land spawns), and water joined to the sea is never fresh. VulnonaMAP guides which
inland water counts: next to one of its lakes, ponds or rivers (Gondwa, Panjura), or to one of its
water names (Riparia, where it has names only; big water there counts without one). Its rivers
also bridge small gaps in a stream and count as joins to the sea.

VulnonaMAP uses its own coordinates; they're fitted onto ours with the waystones both have
(typically ~0.4 grid units off, 3 px on the 4096 px map).
"""
from __future__ import annotations

import json
import re
import sys

import cv2
import numpy as np

from potoverlay import config as C
from potoverlay import freshwater as F
from potoverlay import spawnfix
from potoverlay.locator import imread_any

RAW = C.DATA_DIR / "vulnona"
MAPS = ("riparia", "gondwa", "panjura")
NUM = re.compile(r"^(-?[\d.]+),(-?[\d.]+)(?:,(.*))?$")


def parse(text: str) -> tuple[dict, list[dict]]:
    """VulnonaMAP data_1.txt -> (config, records). A record is a header line
    ("path<TAB>water<TAB>Sleeping Lake:pos<TAB>fill") followed by "x,y,..." lines."""
    cfg, recs, cur = {}, [], None
    for line in text.splitlines():
        if line.startswith("#cfg"):
            p = line.split("\t")
            if len(p) >= 3:
                cfg[p[1]] = p[2]
            continue
        if not line.strip() or line.startswith("#"):
            cur = None
            continue
        m = NUM.match(line.strip())
        if m:
            if cur is not None:
                cur["pts"].append((float(m[1]), float(m[2]), m[3] or ""))
            continue
        p = line.split("\t")
        if p[0] in ("dir", "dirEnd"):
            cur = None
            continue
        cur = {"type": p[0], "cls": p[1] if len(p) > 1 else "", "name": p[2] if len(p) > 2 else "",
               "flags": " ".join(p[3:]), "pts": []}
        recs.append(cur)
    return cfg, recs


def _radius(extra: str) -> tuple[float, float, float] | None:
    """A pond given as an ellipse on a point: "R=2/5/14" (rx/ry/rotation), "R=12-14 T=-35",
    "R=5-6", "R=5". None for an ordinary point."""
    m = re.search(r"R=([\d.]+)(?:[/-]([\d.]+))?(?:/(-?[\d.]+))?", extra or "")
    if not m:
        return None
    rx = float(m[1])
    ry = float(m[2]) if m[2] else rx
    t = re.search(r"T=(-?[\d.]+)", extra)
    rot = float(m[3]) if m[3] else (float(t[1]) if t else 0.0)
    return rx, ry, rot


def fit(recs: list[dict], map_name: str) -> tuple[float, np.ndarray, float]:
    """Vulnona coords -> Potservers grid: scale s and offset T (grid = s * v + T), from the
    waystones both sources mark. Returns (s, T, median error in grid units)."""
    V = np.array([r["pts"][0][:2] for r in recs if r["type"] == "icon" and r["cls"] == "WayStone" and r["pts"]])
    raw = json.loads((C.DATA_DIR / "potservers" / f"map_{map_name}.json").read_text(encoding="utf-8"))["data"]
    P = np.asarray(next(m["points"] for m in raw["markers"] if m["id"] == "waystone"), np.float64).reshape(-1, 2)
    s = P.std(0).mean() / V.std(0).mean()
    T = P.mean(0) - s * V.mean(0)
    for _ in range(10):
        Q = s * V + T
        j = np.hypot(*(Q[:, None, :] - P[None, :, :]).transpose(2, 0, 1)).argmin(1)
        A = np.c_[np.r_[V[:, 0], V[:, 1]], np.r_[np.ones(len(V)), np.zeros(len(V))],
                  np.r_[np.zeros(len(V)), np.ones(len(V))]]
        sol, *_ = np.linalg.lstsq(A, np.r_[P[j, 0], P[j, 1]], rcond=None)
        s, T = float(sol[0]), sol[1:]
    Q = s * V + T
    err = np.hypot(*(Q - P[np.hypot(*(Q[:, None, :] - P[None, :, :]).transpose(2, 0, 1)).argmin(1)]).T)
    return s, T, float(np.median(err))


def _humanize(name: str) -> str:
    """"BigQuillLake" / "Big_Quill_Lake:pos" / "( WhistlingLake )" -> "Big Quill Lake"."""
    name = re.sub(r"<br\s*/?>", " ", name.split(":")[0], flags=re.I)
    name = re.sub(r"<[^>]+>", "", name).replace("_", " ").strip(" ,()")
    name = re.sub(r"\briv\b", "River", name)
    return " ".join(re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name).split())


def _clean(label: str) -> str:
    return _humanize(label.replace(":p", ""))


def _proper(name: str) -> bool:
    """A real name to show ("Deep Lake"), not a mapper's note ("-80,220 pond", "GC東・池1")."""
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9'.\- ]*", name)) and any(len(w) >= 3 for w in name.split()) \
        and name.lower() not in ("pond", "lake", "river", "water") \
        and not re.match(r"^[ns]\d+[ew]\d+", name, re.I) and "xxx" not in name.lower()


SUPPORT = 24        # px: inland water this close to a VulnonaMAP lake, pond or river counts
NAME_REACH = 98     # px (12 grid units): or this close to a water name (labels sit beside the water)
BIG = 1600          # px area (~50 m across): on maps where VulnonaMAP has only names, water this
                    # big counts without a name next to it
RIVER_GAP = 15      # px: gaps along a VulnonaMAP river (a bridge, trees) are closed up to this
POND_R = 7          # px (~9 m): a pond too small or shaded to see on the map image is drawn this big
SEA_CLEAR = 25      # px: a pond VulnonaMAP marks this close to the sea is left out (it may be the sea)
POND_WORDS = re.compile(r"\b(pond|ponds|puddle|pool|spring|springs)\b", re.I)

# Water checked in game (map image pixels: where the player stood drinking, found from the
# minimap of a screenshot). Kept even where the map image shows no water.
CONFIRMED = {
    "riparia": [
        (2476, 1120, "Volcano south puddle"),   # pond in a clearing on Volcano Islands
        (1978, 3560, "Hollow Hills Pond 3"),    # puddle in a crater
        (2050, 3438, "Hollow Hills Pond 2"),    # puddle in a crater
    ],
}


def guides(recs: list[dict], to_px, size: int) -> tuple[np.ndarray, np.ndarray, dict]:
    """VulnonaMAP's water on our map image: (lakes and ponds filled, rivers as lines, names)."""
    water = [r for r in recs if r["cls"] == "water" and r["pts"]]
    shapes = np.zeros((size, size), np.uint8)
    rivers = np.zeros((size, size), np.uint8)
    names: dict[str, tuple[float, float]] = {}
    for r in water:  # names: the label text where Vulnona places one, else the shape's name
        if r["type"] == "text":
            label = _clean(r["pts"][0][2]) or _humanize(r["name"])
            if _proper(label):
                names.setdefault(label, tuple(to_px(r["pts"][0][:2])[0]))
    texted = {_humanize(r["name"]).lower() for r in water if r["type"] == "text"}
    for r in water:
        nm = _humanize(r["name"])
        if r["type"] != "text" and "notxt" not in r["flags"] and nm.lower() not in texted and _proper(nm):
            names.setdefault(nm, tuple(to_px([p[:2] for p in r["pts"]]).mean(0)))

    unit = float(np.hypot(*(to_px([(1, 0)]) - to_px([(0, 0)]))[0]))  # px per Vulnona unit

    def ellipse(x, y, rx, ry, rot):
        c = to_px([(x, y)])[0]
        cv2.ellipse(shapes, (int(round(c[0])), int(round(c[1]))),
                    (max(3, int(round(rx * unit))), max(3, int(round(ry * unit)))), rot, 0, 360, 1, -1)

    def pieces(r):  # "M" starts a new stretch / sub-outline
        g = to_px([p[:2] for p in r["pts"]])
        out, cur = [], []
        for (x, y), p in zip(g, r["pts"]):
            if p[2].startswith("M") and cur:
                out.append(cur)
                cur = []
            cur.append((x, y))
        out.append(cur)
        return [np.round(np.array(pc)).astype(np.int32) for pc in out]

    for r in water:
        radii = [_radius(p[2]) for p in r["pts"]]
        if any(radii):  # a list of ponds, each "x,y,R=rx/ry/rot"
            for (x, y, _), rr in zip(r["pts"], radii):
                if rr:
                    ellipse(x, y, *rr)
        elif r["type"] == "circle":  # ponds, each "x,y,rx,ry,rot"
            for x, y, rest in r["pts"]:
                vals = [float(v) for v in rest.split(",") if re.fullmatch(r"-?[\d.]+", v.strip())]
                rx = vals[0] if vals else 1.0
                ellipse(x, y, rx, vals[1] if len(vals) > 1 else rx, vals[2] if len(vals) > 2 else 0.0)
        elif r["type"] == "path" and "fill" in r["flags"]:
            shape = np.zeros_like(shapes)
            for part in pieces(r):  # sub-outlines alternate lake / island (even-odd)
                if len(part) >= 3:
                    one = np.zeros_like(shapes)
                    cv2.fillPoly(one, [part], 1)
                    shape ^= one
            shapes |= shape
        elif r["type"] in ("line", "path"):
            cv2.polylines(rivers, [pc for pc in pieces(r) if len(pc) >= 2], False, 1, 3)
    return shapes, rivers, names


def pond_at(img: np.ndarray, x: float, y: float, search: int = 14) -> np.ndarray:
    """A small pond at (x, y), as a mask the size of the map: the blue patch right there on the
    map image (a pool shows bluer than the rock and grass around it), or a small round pond where
    the map shows none (too small, or under trees)."""
    size = img.shape[0]
    out = np.zeros((size, size), np.uint8)
    x, y = int(round(x)), int(round(y))
    x0, y0 = max(0, x - search), max(0, y - search)
    patch = cv2.GaussianBlur(img[y0:y + search + 1, x0:x + search + 1].astype(np.float32), (3, 3), 0)
    blue = patch[..., 0] - patch[..., 2]
    disc = np.zeros(blue.shape, np.uint8)
    cv2.circle(disc, (x - x0, y - y0), search, 1, -1)
    if disc.any():
        cut = max(4.0, float(np.median(blue[disc == 1])) + 15)  # (cracks in the rock are barely blue)
        n, lab, st, _ = cv2.connectedComponentsWithStats(((blue > cut) & (disc == 1)).astype(np.uint8), connectivity=8)
        if n > 1:  # the bluest patch: the pool, not a shadow beside it
            peak = [float(blue[lab == j].max()) for j in range(1, n)]
            i = 1 + int(np.argmax(peak))
            if 4 <= st[i, cv2.CC_STAT_AREA] <= 400:
                blob = cv2.dilate((lab == i).astype(np.uint8), np.ones((3, 3), np.uint8))
                out[y0:y0 + blob.shape[0], x0:x0 + blob.shape[1]] = blob
                return out
    cv2.circle(out, (x, y), POND_R, 1, -1)
    return out


def build(map_name: str) -> tuple[dict, dict]:
    """(the baked data, notes for checking: water kept on size alone, big water dropped, names
    with no water found near them)."""
    cfg, recs = parse((RAW / f"{map_name}.txt").read_text(encoding="utf-8"))
    s, T, err = fit(recs, map_name)
    img = imread_any(C.map_image_path(map_name))
    size = img.shape[0]

    def to_px(xy):  # Vulnona -> Potservers grid (fitted on waystones) -> our map image
        g = s * np.asarray(xy, np.float64).reshape(-1, 2) + T
        return spawnfix.correct(map_name, g * (spawnfix.IMAGE_SIZE / spawnfix.GRID)) * (size / spawnfix.IMAGE_SIZE)

    shapes, rivers, names = guides(recs, to_px, size)
    names_only = not shapes.any() and not rivers.any()

    markers = json.loads((C.DATA_DIR / "potservers" / f"map_{map_name}.json").read_text(encoding="utf-8"))
    score, loose = F.water_score(img, markers["data"].get("markers", []), map_name)
    # Water: water-ish areas that are clearly water somewhere, or where VulnonaMAP has water.
    wet = cv2.morphologyEx((score > loose).astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab, _, _ = cv2.connectedComponentsWithStats(wet, connectivity=8)
    strong = np.bincount(lab.ravel(), (score > 0).ravel(), n)
    guided = np.bincount(lab.ravel(), (shapes | rivers).ravel(), n)
    keep = (strong >= 4) | (guided > 0)
    keep[0] = False
    water = keep[lab].astype(np.uint8)
    del lab, wet, score

    sea = cv2.dilate(F.ocean(water, rivers), np.ones((5, 5), np.uint8))
    inland = water & (1 - sea)
    if rivers.any():  # close small gaps along rivers (bridges, trees), never into the sea
        near_river = cv2.dilate(rivers, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (RIVER_GAP, RIVER_GAP))
        inland |= cv2.morphologyEx(inland, cv2.MORPH_CLOSE, k) & near_river & (1 - sea)

    # Which inland water counts: next to VulnonaMAP's water or a water name; or big (names-only maps)
    support = cv2.dilate(shapes | rivers, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * SUPPORT + 1,) * 2))
    for x, y in names.values():
        cv2.circle(support, (int(round(x)), int(round(y))), NAME_REACH, 1, -1)
    n, lab, st, cen = cv2.connectedComponentsWithStats(inland, connectivity=8)
    supported = np.bincount(lab.ravel(), support.ravel(), n) > 0
    area = st[:, cv2.CC_STAT_AREA]
    keep = (area >= 24) & (supported | (names_only & (area >= BIG)))
    keep[0] = False
    fresh = keep[lab].astype(np.uint8)
    del lab

    # Ponds VulnonaMAP knows that don't show on the map image (too small, or shaded): every one
    # checked in game was real. Its pond shapes, then pond names with no water around them; never
    # next to the sea. Spots confirmed in game always.
    near_sea = cv2.dilate(sea, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * SEA_CLEAR + 1,) * 2))
    seen = cv2.dilate(fresh, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * SUPPORT + 1,) * 2))
    added = []
    n, lab, st, cen = cv2.connectedComponentsWithStats(shapes, connectivity=8)
    for i in range(1, n):
        one = lab == i
        if not seen[one].any() and not near_sea[one].any():
            fresh[one] = 1
            added.append(("shape", cen[i].round().tolist()))
    del lab
    confirmed = CONFIRMED.get(map_name, [])
    for nm, (x, y) in names.items():
        xi, yi = int(np.clip(x, 0, size - 1)), int(np.clip(y, 0, size - 1))
        # (a label sits beside its water: any water within NAME_REACH is probably the one named)
        water_near = fresh[max(0, yi - NAME_REACH):yi + NAME_REACH, max(0, xi - NAME_REACH):xi + NAME_REACH].any()
        if (POND_WORDS.search(nm) and not any(c[2] == nm for c in confirmed)
                and not water_near and not near_sea[yi, xi]):
            fresh |= pond_at(img, x, y)
            added.append((nm, [round(x), round(y)]))
    for x, y, nm in confirmed:
        if not fresh[max(0, y - POND_R):y + POND_R, max(0, x - POND_R):x + POND_R].any():
            fresh |= pond_at(img, x, y)
            added.append((nm + " (checked in game)", [x, y]))

    notes = {
        "ponds_added": added,
        "kept_on_size_only": [(cen[i].round().tolist(), int(area[i])) for i in range(1, n)
                              if keep[i] and not supported[i]],
        "dropped_big": [(cen[i].round().tolist(), int(area[i])) for i in range(1, n)
                        if not keep[i] and area[i] >= 400],
        "names_without_water": [nm for nm, (x, y) in names.items()
                                if not fresh[max(0, int(y) - NAME_REACH):int(y) + NAME_REACH,
                                             max(0, int(x) - NAME_REACH):int(x) + NAME_REACH].any()],
    }
    zones = F.outlines(fresh, (size, size), eps=1.0)
    return {
        "map": map_name,
        "source": F.CREDIT + " (guide); outlines from the map image",
        "fit_error_grid": round(err, 2),
        "zones": [[[round(x, 1), round(y, 1)] for x, y in z] for z in zones],
        "names": [{"name": nm, "x": round(x, 1), "y": round(y, 1)} for nm, (x, y) in sorted(names.items())],
    }, notes


def main() -> int:
    out_dir = C.DATA_DIR / "freshwater"
    out_dir.mkdir(parents=True, exist_ok=True)
    for m in MAPS:
        if not (RAW / f"{m}.txt").exists():
            print(f"[{m}] no VulnonaMAP data (run python -m tools.fetch_vulnona)")
            continue
        data, notes = build(m)
        F.path(m).write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
        print(f"[{m}] {len(data['zones'])} outlines, {len(data['names'])} names, "
              f"coordinates fitted to {data['fit_error_grid']} grid units -> {F.path(m).name}")
        for k, v in notes.items():
            if v:
                print(f"    {k.replace('_', ' ')}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
