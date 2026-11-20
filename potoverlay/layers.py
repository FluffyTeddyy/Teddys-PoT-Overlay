"""Always-on map layers that aren't quests: where food is (fish, insects, berries...). Toggled in
the tray's "Food" menu. The points come from Potservers' map data (data/potservers/map_<map>.json,
downloaded by setup), in the same pixel frame as the quests.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import numpy as np

from . import config as C

log = logging.getLogger(__name__)

GRID = 500.0       # Potservers map coordinates run 0..500
IMAGE_SIZE = 4096  # the map images are 4096 px


@dataclass(frozen=True)
class LayerKind:
    id: str      # Potservers marker kind
    label: str   # tray menu + legend
    color: str
    style: str   # how markers are drawn (see overlay.draw_marker)
    group: str = ""


@dataclass(frozen=True)
class FoodGroup:
    name: str
    color: str
    style: str
    items: tuple  # (Potservers marker kind, label)


# The tray's Food menu. One shape and colour per group, so groups are told apart at a glance.
FOOD_GROUPS = [
    FoodGroup("Fish", "#4fc3ff", "fish", (
        ("fish_spawn", "Fish"), ("diseased_fish", "Diseased fish"), ("krill", "Krill feeding areas"))),
    FoodGroup("Shellfish", "#c9a7ff", "diamond", (("clams", "Clams, scallops & conch"),)),
    FoodGroup("Meat", "#ff5a4f", "triangle", (("carrion", "Carrion"), ("burrow", "Critter burrows"))),
    FoodGroup("Insects", "#ffb84f", "square", (
        ("ant_hill", "Ant hills"), ("termites", "Termite mounds"), ("grubby_log", "Grubs"),
        ("wasp_nest", "Wasp nests"))),
    FoodGroup("Fruits & berries", "#ff6fd0", "dot", (
        ("berry", "Berries"), ("melon", "Melon"), ("lychee", "Lychee"), ("banana", "Banana"))),
    FoodGroup("Nuts & seeds", "#c8a15a", "dot", (
        ("acorn", "Acorns"), ("pinecone", "Pinecones"), ("tigernut", "Tiger nuts"), ("nypa_seed", "Nypa seeds"),
        ("monkey_puzzle_cone", "Monkey puzzle cones"))),
    FoodGroup("Mushrooms", "#f2f2f2", "mushroom", (
        ("milkcap", "Milk cap"), ("button", "Button mushroom"), ("chanterelle", "Chanterelle"),
        ("russula", "Russula"), ("armillaria", "Armillaria"), ("shelf_mushroom", "Shelf mushroom"),
        ("cave_mushroom", "Cave mushrooms"))),
    FoodGroup("Plants & herbs", "#7cff6b", "leaf", (
        ("yarrow", "Yarrow"), ("red_ginger", "Red ginger"), ("reeds", "Reeds"), ("kelp", "Kelp"),
        ("lake_weed", "Lakeweed"), ("roots", "Roots"), ("succulent", "Succulents"))),
]

LAYERS = [LayerKind(kid, label, g.color, g.style, g.name) for g in FOOD_GROUPS for kid, label in g.items]
BY_ID = {k.id: k for k in LAYERS}

# "Hoard: <type>" event quests: collect as much as you can of a list of items, shown under the
# title in the quest list. Potservers doesn't list these events, so while one is in the quest list
# every spawn of its items is shown, as one layer. The lists as the HUD shows them (names match
# Potservers' marker names); a Hoard type not listed here uses the item list read from the HUD.
HOARD_LISTS = {
    "Gemstones": ["Citrine Quartz", "Opal Chunk", "Amber", "Bloodstones", "Pearl", "Emerald", "Obsidian Chunk"],
    "Beachcomber": ["Clam", "Starfish", "Turtle Shell", "Pearl", "Shark Teeth", "Seashells", "Sand Dollars",
                    "Fish Bones", "Jellyfish", "Periwinkle", "Shark Egg", "Limpet", "Coral Chunks", "Anemone",
                    "Cuttlefish Bone", "Colorful Shell", "Bleached Corals", "Barnacle", "Lake Sponge"],
    "Scavenger": ["Bones", "Snake Shed", "Peeling Bark", "Cicada Molt", "Branches", "Wind Blown Branches",
                  "Turtle Shell", "Charred Wood", "Ammonites", "Pearlescent Shells", "Barnacle", "Jellyfish",
                  "Coral Chunks", "Snagged Fur", "Shark Teeth", "Sand Dollars", "Fish Bones", "Seashells", "Clam",
                  "Colorful Shell", "Cuttlefish Bone"],
}
HOARD_COLOR = "#ffe14d"
# HUD names that differ from Potservers' marker names.
_HOARD_ALIASES = {"cicada molt": "cicada"}


def _words(s: str) -> str:
    return " ".join(re.sub(r"[^a-z ]+", " ", s.lower()).split())


def hoard_kinds(markers: list[dict], items) -> list[str]:
    """Potservers marker kinds for item names as the HUD shows them ("Citrine Quartz", "Clam"...).
    The whole name must match closely (a marker named "Clam, Scallop & Conch" counts as each part),
    so "Fish Bones" isn't "Bones" and "Starfish" isn't "Glow Starfish". Misread names that match
    nothing are ignored."""
    from rapidfuzz import fuzz

    cands = []  # (normalized name or part, kind)
    for m in markers:
        if not m.get("points"):
            continue
        name = _words(m.get("name", ""))
        cands.append((name, m["id"]))
        cands += [(_words(p), m["id"]) for p in re.split(r"[,&]", m.get("name", "")) if _words(p) != name]
    out: list[str] = []
    for item in items:
        w = _words(item)
        w = _HOARD_ALIASES.get(w, w)
        if len(w) < 3:
            continue
        score, kind = max(((fuzz.ratio(w, c), k) for c, k in cands), default=(0.0, ""))
        if score >= 85 and kind not in out:
            out.append(kind)
    return out


@dataclass
class Layer:
    kind: LayerKind
    points: np.ndarray  # Nx2, full-size map image pixels


def layer_legend(layers: list["Layer"]) -> list[tuple[str, str, str]]:
    """Legend lines for the shown food: one per group ("Insects (3)"), or the item's own name if
    it's the only one of its group."""
    by_group: dict[str, list[Layer]] = {}
    for layer in layers:
        by_group.setdefault(layer.kind.group or layer.kind.label, []).append(layer)
    out = []
    for group, ls in by_group.items():
        k = ls[0].kind
        out.append((k.label if len(ls) == 1 else f"{group} ({len(ls)})", k.color, k.style))
    return out


def available_kinds(map_name: str) -> set[str]:
    """Which layer kinds the map data has spawn points for."""
    data = _map_data(map_name)
    return {m["id"] for m in data.get("markers", []) if m["id"] in BY_ID and m.get("points")} if data else set()


def load_layers(map_name: str, kinds: list[str]) -> list[Layer]:
    data = _map_data(map_name)
    if not data or not kinds:
        return []
    out = []
    for m in data.get("markers", []):
        if m["id"] in kinds and m["id"] in BY_ID and m.get("points"):
            pts = np.asarray(m["points"], np.float64).reshape(-1, 2) * (IMAGE_SIZE / GRID)
            out.append(Layer(BY_ID[m["id"]], pts))
    return out


def hoard_type(label: str) -> str:
    """"Hoard: Gemstones" -> "Gemstones" (the label the quest reader gives the event)."""
    return label.split(":", 1)[1].strip() if ":" in label else ""


def hoard_layer(map_name: str, label: str, items_read=()) -> Layer | None:
    """Every spawn on this map of the items of the Hoard event called `label` ("Hoard: Gemstones").
    Known Hoard types use their built-in list; others use the item names read from the HUD."""
    data = _map_data(map_name)
    if not data:
        return None
    markers = data.get("markers", [])
    names = HOARD_LISTS.get(hoard_type(label)) or list(items_read)
    kinds = set(hoard_kinds(markers, names))
    pts = [np.asarray(m["points"], np.float64).reshape(-1, 2) for m in markers if m["id"] in kinds and m.get("points")]
    if not pts:
        return None
    kind = LayerKind("hoard", f"{label} items" if label else "Hoard items", HOARD_COLOR, "dot", "Hoard")
    return Layer(kind, np.vstack(pts) * (IMAGE_SIZE / GRID))


def _map_data(map_name: str) -> dict | None:
    path = C.DATA_DIR / "potservers" / f"map_{map_name}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))["data"]
    except (OSError, ValueError, KeyError) as e:
        log.info("No map data for layers (%s): %s", map_name, e)
        return None
