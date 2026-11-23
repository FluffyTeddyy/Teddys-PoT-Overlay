"""Give every item its own marker colour, taken from its picture: pinecones green, berries red,
chanterelles orange... Writes data/item_colors.json (shipped with the overlay).

Colours come from, in order: OVERRIDES (set by hand), GAME_ICONS (the game's own item icons),
then the Potservers picture for items the game has no icon for.

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
# Main colour of each item's own icon in the game (the picture in the "Pickup" prompt), measured
# from the game's UI textures (UI/UIElements/Item_Icons/T_Item_*_UI). These win over the
# Potservers pictures, which are fan-drawn and sometimes a different colour.
GAME_ICONS = {
    "acorn": "#d98845",
    "algae": "#b4d95b",
    "amanita": "#d94341",
    "amber": "#d9542f",
    "ammonites": "#d9bfa0",
    "ant_hill": "#d99f82",
    "armillaria": "#d9a180",
    "banana": "#d9be23",
    "barnacles": "#d9cea3",
    "berry": "#d93309",
    "bleached_coral": "#d9bbb1",
    "blood_stone": "#d9431c",
    "bones": "#d9af94",
    "branches": "#d9b87c",
    "butterfly": "#8094d9",
    "button": "#d9d8cd",
    "cactus": "#ccd957",
    "canna_flower": "#d90008",
    "carrion": "#d98855",
    "cave_mushroom": "#d9bda1",
    "chalcopyrite": "#d9d3d6",
    "chanterelle": "#d9680a",
    "charred_wood": "#d9c7d3",
    "cicada": "#d9855b",
    "clams": "#d97943",
    "clay": "#a6c1d9",
    "cliff_lichen": "#d97b29",
    "coal": "#d9d3d9",
    "coastal_lichen": "#d9d900",
    "cocoon": "#d9b84e",
    "coral": "#d96639",
    "cuttlefish": "#d9cec0",
    "daisy": "#bfd98c",
    "deathcaps": "#d9ba93",
    "desert_cliff_moss": "#d9ab00",
    "diseased_fish": "#d9a175",
    "dragonfly_shed": "#d9bbbb",
    "emerald": "#3ad9af",
    "fire_moth": "#d90021",
    "fish_bones": "#d9c69a",
    "frog_eggs": "#c6ccd9",
    "geode": "#5ecae2",
    "glow_starfish": "#d9d900",
    "golden_beetle": "#d9a500",
    "granite": "#d9c7ba",
    "grubby_log": "#d98746",
    "jellyfish": "#69bdf8",
    "kelp": "#bed933",
    "king_stag_beetle": "#cfcad9",
    "lake_sponge": "#d9a345",
    "lily": "#d9d7a5",
    "limpets": "#e4c063",
    "longhorn_beetle": "#e16c4c",
    "lychee": "#d94d24",
    "melon": "#d9b45f",
    "mica": "#d9c5b6",
    "milkcap": "#d9d9d4",
    "monkey_puzzle_cone": "#d9755f",
    "mosquito_swarm": "#d9d2c1",
    "nypa_seed": "#d97e35",
    "obsidian": "#6c9cd9",
    "opal": "#d9815f",
    "pearl": "#d9d3c8",
    "peeling_bark": "#d99a63",
    "periwinkle": "#d95f3b",
    "pinecone": "#abd951",
    "pink_yarrow": "#b7d979",
    "quartz": "#d9bf86",
    "rafflesia": "#d94c33",
    "red_ginger": "#afd975",
    "reeds": "#d9d962",
    "rocks": "#d9d5b6",
    "roots": "#d9ba7d",
    "russula": "#d98787",
    "salt_rock": "#d4dde3",
    "sand_dollars": "#b094d9",
    "sea_anemone": "#d9622d",
    "seashells": "#d98383",
    "shark_egg": "#d98972",
    "shark_jaw": "#dbbfa2",
    "shelf_mushroom": "#d9cdb5",
    "sky_moss": "#00d9c0",
    "snagged_fur": "#cad9c4",
    "snake_shed": "#d9bfbf",
    "snake_weed": "#eecb21",
    "spined_conch": "#e8ded2",
    "starfish": "#d9270f",
    "sticky_tar": "#d0cad9",
    "succulent": "#c1d995",
    "termites": "#d9c3ad",
    "tigernut": "#d98d58",
    "turtle_shell": "#d9d382",
    "water_lily": "#d989b5",
    "water_mineral": "#a4b4d9",
    "wind_blown_branches": "#b7d961",
    "wind_carved_stones": "#d9b9a0",
    "yarrow": "#d9d685",
    "yellow_coneflower": "#feee00",
}
# Set by hand where the icon misleads: too dark to show on the minimap, more leaf than flower, or
# the measured part isn't what you look for in game.
OVERRIDES = {
    "dragonfly_shed": "#e6d7b8",     # a pale beige husk in game
    "king_stag_beetle": "#a3abbd",   # glossy black: blue-grey shows on the minimap
    "charred_wood": "#8c8f99",       # charcoal
    "coal": "#9a9da6",
    "sticky_tar": "#8f8f8f",         # black
    "pink_yarrow": "#ff70c8",        # pink flowers
    "red_ginger": "#ff4b3a",         # red flower
    "yarrow": "#f2f0d8",             # white flowers
    "daisy": "#f2f2ea",              # white flower
    "lily": "#f4f4f0",               # white flower
    "opal": "#3fd0c0",               # turquoise
    "turtle_shell": "#d97a3a",       # orange-brown shell in the world (the icon is olive)
    "dried_kelp": "#a89650",         # dark olive-brown pile
    "fish_spawn": "#4fc3ff",         # the overlay's blue fish (as before item colours)
    "burrow": "#ff5a4f",             # the overlay's red burrow triangle (as before)
    # Checked in game (screenshots): what the item looks like in the world.
    "glow_starfish": "#4fd6b0",      # teal-green glow
    "glow_mushroom": "#a6e04a",      # lime green with spots
    "ammonites": "#c98a4a",          # orange-brown fossil
    "termites": "#a8865a",           # dark earthy mound
    "chalcopyrite": "#9b7fe6",       # rainbow sheen, mostly blue-purple
    "bones": "#d9cabb",              # pale bone
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
            colors[kind] = OVERRIDES.get(kind) or GAME_ICONS.get(kind) or for_display(main_color(img))
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
