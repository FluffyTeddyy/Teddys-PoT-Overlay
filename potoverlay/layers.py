"""Always-on map layers that aren't quests: where food is (fish, insects, berries...). Toggled in
the tray's "Food" menu. The points come from Potservers' map data (data/potservers/map_<map>.json,
downloaded by setup), in the same pixel frame as the quests.
"""
from __future__ import annotations

import json
import logging
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

# The "Hoard: Scavenger" event quest: pick up as much as you can of a fixed list of items (the same
# everywhere). Potservers doesn't list the event, so while it's in the quest list every spawn of
# these items is shown, as one layer. Potservers marker kinds for the list the HUD shows: Bones,
# Snake Shed, Peeling Bark, Cicada Molt, Branches, Wind Blown Branches, Turtle Shell, Charred Wood,
# Ammonites, Pearlescent Shells, Barnacle, Jellyfish, Coral Chunks, Snagged Fur, Shark Teeth,
# Sand Dollars, Fish Bones, Seashells, Clam, Colorful Shell, Cuttlefish Bone.
HOARD_KINDS = (
    "bones", "snake_shed", "peeling_bark", "cicada", "branches", "wind_blown_branches", "turtle_shell",
    "charred_wood", "ammonites", "pearlescent_shells", "barnacles", "jellyfish", "coral", "snagged_fur",
    "shark_jaw", "sand_dollars", "fish_bones", "seashells", "clams", "color_shells", "cuttlefish",
)
HOARD = LayerKind("hoard", "Hoard: Scavenger items", "#ffe14d", "dot", "Hoard")


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


def hoard_layer(map_name: str) -> Layer | None:
    """Every spawn of the Hoard: Scavenger items on this map, as one layer."""
    data = _map_data(map_name)
    if not data:
        return None
    pts = [np.asarray(m["points"], np.float64).reshape(-1, 2) for m in data.get("markers", [])
           if m["id"] in HOARD_KINDS and m.get("points")]
    return Layer(HOARD, np.vstack(pts) * (IMAGE_SIZE / GRID)) if pts else None


def _map_data(map_name: str) -> dict | None:
    path = C.DATA_DIR / "potservers" / f"map_{map_name}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))["data"]
    except (OSError, ValueError, KeyError) as e:
        log.info("No map data for layers (%s): %s", map_name, e)
        return None
