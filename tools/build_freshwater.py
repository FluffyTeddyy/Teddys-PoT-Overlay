"""Bake fresh water into data/freshwater/<map>.json (run after tools.fetch_vulnona).

    python -m tools.build_freshwater

Gondwa and Panjura: VulnonaMAP's drinking water outlines (lakes), circles (ponds) and lines
(rivers), with their names. Riparia: VulnonaMAP only has names there, so the outlines come from
the map image (inland water, see potoverlay/freshwater.py), kept only where VulnonaMAP names a
water spot; a named spot with no outline gets the water right around it, or a small circle.

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
RES = 2048  # Vulnona shapes are drawn at this size, then outlined
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


def build(map_name: str) -> dict:
    cfg, recs = parse((RAW / f"{map_name}.txt").read_text(encoding="utf-8"))
    s, T, err = fit(recs, map_name)
    img = imread_any(C.map_image_path(map_name))
    full = (img.shape[1], img.shape[0])
    to_grid = lambda xy: s * np.asarray(xy, np.float64).reshape(-1, 2) + T  # noqa: E731
    water = [r for r in recs if r["cls"] == "water" and r["pts"]]

    # Names: the label text where Vulnona places one, else the shape's name at its middle.
    names: dict[str, tuple[float, float]] = {}
    for r in water:
        if r["type"] == "text":
            label = _clean(r["pts"][0][2]) or _humanize(r["name"])
            if _proper(label):
                names.setdefault(label, tuple(to_grid(r["pts"][0][:2])[0]))
    texted = {_humanize(r["name"]).lower() for r in water if r["type"] == "text"}
    for r in water:
        nm = _humanize(r["name"])
        if r["type"] != "text" and "notxt" not in r["flags"] and nm.lower() not in texted and _proper(nm):
            names.setdefault(nm, tuple(to_grid([p[:2] for p in r["pts"]]).mean(0)))

    k = RES / F.GRID  # grid -> raster px
    mask = np.zeros((RES, RES), np.uint8)

    def ellipse(x, y, rx, ry, rot):
        c = to_grid([(x, y)])[0] * k
        cv2.ellipse(mask, (int(round(c[0])), int(round(c[1]))),
                    (int(round(max(1.5, s * rx) * k)), int(round(max(1.5, s * ry) * k))), rot, 0, 360, 1, -1)

    for r in water:  # outlines, ponds and rivers drawn by hand on VulnonaMAP
        g = to_grid([p[:2] for p in r["pts"]]) * k
        radii = [_radius(p[2]) for p in r["pts"]]
        if any(radii):  # a list of ponds, each "x,y,R=rx/ry/rot" (or "R=rx-ry T=rot")
            for (x, y, _), rr in zip(r["pts"], radii):
                if rr:
                    ellipse(x, y, *rr)
            continue
        if r["type"] == "circle":  # one or more ponds, each "x,y,rx,ry,rot"
            for x, y, rest in r["pts"]:
                vals = [float(v) for v in rest.split(",") if re.fullmatch(r"-?[\d.]+", v.strip())]
                rx = vals[0] if vals else 1.0
                ellipse(x, y, rx, vals[1] if len(vals) > 1 else rx, vals[2] if len(vals) > 2 else 0.0)
            continue
        if r["type"] == "path" and "fill" in r["flags"]:
            shape = np.zeros_like(mask)
            parts, cur = [], []
            for (x, y), p in zip(g, r["pts"]):
                if p[2].startswith("M") and cur:
                    parts.append(cur)
                    cur = []
                cur.append((x, y))
            parts.append(cur)
            for part in parts:  # sub-outlines alternate lake / island (even-odd)
                if len(part) >= 3:
                    one = np.zeros_like(mask)
                    cv2.fillPoly(one, [np.round(np.array(part)).astype(np.int32)], 1)
                    shape ^= one
            mask |= shape
        elif r["type"] in ("line", "path"):  # rivers; "M" starts a new stretch of river
            pieces, cur = [], []
            for (x, y), p in zip(g, r["pts"]):
                if p[2].startswith("M") and cur:
                    pieces.append(cur)
                    cur = []
                cur.append((x, y))
            pieces.append(cur)
            cv2.polylines(mask, [np.round(np.array(pc)).astype(np.int32) for pc in pieces if len(pc) >= 2],
                          False, 1, max(2, int(round(1.2 * k))))

    detected = 0
    drawn = any(r["type"] in ("path", "line") for r in water)  # lakes/rivers outlined by hand
    if not drawn and names:
        # Riparia: names only. Outlines from the map image, kept where a named water spot is.
        markers = json.loads((C.DATA_DIR / "potservers" / f"map_{map_name}.json").read_text(encoding="utf-8"))
        wat = F.water_mask(img, markers["data"].get("markers", []))
        fresh = F.inland(wat)
        W = F.WORK
        kw = W / F.GRID
        n, lab, stats, _ = cv2.connectedComponentsWithStats(fresh, connectivity=8)
        dist = cv2.distanceTransform(1 - fresh, cv2.DIST_L2, 5)
        keep = np.zeros(n, bool)
        local = np.zeros_like(fresh)
        for name, (gx, gy) in names.items():
            x, y = int(np.clip(gx * kw, 0, W - 1)), int(np.clip(gy * kw, 0, W - 1))
            if dist[y, x] <= 12 * kw:  # water at or beside the name (labels sit next to the water)
                disc = np.zeros_like(fresh)
                cv2.circle(disc, (x, y), int(12 * kw), 1, -1)
                keep[np.unique(lab[(disc == 1) & (fresh == 1)])] = True
            else:  # nothing detected there: the water right around the name, or a small circle
                disc = np.zeros_like(fresh)
                cv2.circle(disc, (x, y), int(4 * kw), 1, -1)
                around = wat & disc
                if around.sum() >= 6:
                    local |= around
                else:
                    cv2.circle(local, (x, y), int(2 * kw), 1, -1)
        keep[0] = False
        detected = int(keep.sum())
        work_mask = keep[lab].astype(np.uint8) | local
        mask |= cv2.resize(work_mask, (RES, RES), interpolation=cv2.INTER_NEAREST)

    zones = F.outlines(mask, full, eps=0.8)
    if drawn:  # VulnonaMAP shapes were fitted to Potservers' waystones: same correction as spawns
        zones = [spawnfix.correct(map_name, z).tolist() for z in zones]
    # (Riparia's outlines come from the map image itself, which is already the overlay's frame.)
    px = full[0] / F.GRID
    return {
        "map": map_name,
        "source": F.CREDIT + (" + map image (outlines)" if detected else ""),
        "fit_error_grid": round(err, 2),
        "zones": [[[round(x, 1), round(y, 1)] for x, y in z] for z in zones],
        "names": [{"name": n, "x": round(gx * px, 1), "y": round(gy * px, 1)} for n, (gx, gy) in sorted(names.items())],
    }


def main() -> int:
    out_dir = C.DATA_DIR / "freshwater"
    out_dir.mkdir(parents=True, exist_ok=True)
    for m in MAPS:
        if not (RAW / f"{m}.txt").exists():
            print(f"[{m}] no VulnonaMAP data (run python -m tools.fetch_vulnona)")
            continue
        data = build(m)
        F.path(m).write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
        print(f"[{m}] {len(data['zones'])} outlines, {len(data['names'])} names, "
              f"coordinates fitted to {data['fit_error_grid']} grid units -> {F.path(m).name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
