"""Give every item its own marker colour, taken from its picture: pinecones green, berries red,
chanterelles orange... Writes data/item_colors.json (shipped with the overlay).

    python -m tools.build_item_colors

The pictures are Potservers' item icons (the icon_url of each spawn kind in the downloaded map
data), cached in .cache/item_icons. Each item's colour is the main colour of its picture: the
biggest, most colourful area, leaving out the dark outline. It's then brightened so it shows up on
the minimap.
"""
from __future__ import annotations

import colorsys
import json
import sys
import urllib.request

import cv2
import numpy as np

from potoverlay import config as C

HEADERS = {"User-Agent": "pot-quest-overlay/0.1 (personal minimap overlay)"}
ICONS = C.CACHE_DIR / "item_icons"
OUT = C.DATA_DIR / "item_colors.json"
# Where the picture's biggest colour isn't what you look for in game: flowers with more leaf than
# flower, and items too dark to show on the minimap.
OVERRIDES = {
    "pink_yarrow": "#ff70c8",   # pink flowers (leaves are the bigger part of the picture)
    "red_ginger": "#ff4b3a",    # red flower
    "yarrow": "#f2f0d8",        # white flowers
    "lily": "#f4f4f0",          # white flower
    "sticky_tar": "#8f8f8f",    # black: grey shows on the minimap
}


def icon_urls() -> dict[str, tuple[str, str]]:
    """{spawn kind: (name, icon url)} over all downloaded maps."""
    out = {}
    for f in sorted((C.DATA_DIR / "potservers").glob("map_*.json")):
        for m in json.loads(f.read_text(encoding="utf-8"))["data"].get("markers", []):
            if m.get("icon_url"):
                out.setdefault(m["id"], (m.get("name") or m["id"], m["icon_url"]))
    return out


def icon(kind: str, url: str) -> np.ndarray | None:
    path = ICONS / f"{kind}.png"
    if not path.exists():
        ICONS.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=30) as r:
            path.write_bytes(r.read())
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if img is None:
        return None
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
    elif img.shape[2] == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    return img


def main_color(img: np.ndarray) -> tuple[int, int, int]:
    """The picture's main colour (RGB): the biggest, most colourful group of similar pixels."""
    px = img[img[..., 3] >= 128][:, :3]
    if len(px) == 0:
        return (200, 200, 200)
    hsv = cv2.cvtColor(px.reshape(-1, 1, 3), cv2.COLOR_BGR2HSV).reshape(-1, 3)
    body = px[hsv[:, 2] >= 70]  # leave out the dark outline and shading
    if len(body) < 10:
        body = px
    lab = cv2.cvtColor(body.reshape(-1, 1, 3), cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
    k = min(4, len(lab))
    _, labels, centers = cv2.kmeans(lab, k, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 50, 0.5),
                                    5, cv2.KMEANS_PP_CENTERS)
    best, best_score = None, -1.0
    for i in range(k):
        members = body[labels.ravel() == i]
        if not len(members):
            continue
        s = cv2.cvtColor(members.reshape(-1, 1, 3), cv2.COLOR_BGR2HSV).reshape(-1, 3)[:, 1].mean() / 255
        score = len(members) * (0.35 + s)
        if score > best_score:
            best, best_score = members.mean(0), score
    b, g, r = (int(round(v)) for v in best)
    return (r, g, b)


def for_display(rgb: tuple[int, int, int]) -> str:
    """Brightened so dark items (bark, mud) still show on the minimap; greys stay grey."""
    h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
    v = max(v, 0.85)
    s = min(1.0, s * 1.15) if s > 0.2 else s
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))


def build() -> dict:
    colors, names = {}, {}
    for kind, (name, url) in sorted(icon_urls().items()):
        try:
            img = icon(kind, url)
        except OSError as e:
            print(f"  {kind}: couldn't download its picture ({e})")
            continue
        if img is not None:
            colors[kind] = OVERRIDES.get(kind) or for_display(main_color(img))
            names[kind] = name
    return {"source": "Colours from the item pictures on Potservers (https://potservers.net)",
            "colors": colors, "names": names}


def main() -> int:
    data = build()
    OUT.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    print(f"{len(data['colors'])} item colours -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
