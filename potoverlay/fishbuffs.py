"""Fish buffs: which fish gives which buff when eaten, for the tray menu "Fish buffs".

Pick fish there and a line like "Pike - Stamina recovery up" shows next to the minimap (and in the
in-game map's legend), as a reminder of what to hunt for.

Source: pathoftitans.wiki (Buffs and Debuffs page, fish pages). The wiki only gives numbers for
Arapaima; fill in the others in data/fish_buffs.json (tray: Fish buffs > Edit buff values...) once
you know them from the game, e.g. "amount": "+20%". The file, when present, replaces the list below.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from . import config as C

log = logging.getLogger(__name__)

COLOR = "#4fc3ff"  # same blue as the fish markers


def path():
    """data/fish_buffs.json: your own values, if you made the file (tray: Edit buff values...)."""
    return C.DATA_DIR / "fish_buffs.json"


# id, fish, what the buff improves, amount ("" = not known), how long ("" = not known)
DEFAULTS = [
    ("pike", "Pike", "Stamina recovery", "", ""),
    ("tuna", "Tuna", "Stamina recovery", "", ""),
    ("catfish", "Catfish", "Health", "", ""),
    ("trout", "Trout", "Bleed healing", "", ""),
    ("kelp_shark", "Kelp Shark", "Bleed healing", "", ""),
    ("arapaima", "Arapaima", "Bleed healing & health recovery", "+20%", "2 min"),
    ("river_stingray", "River Stingray", "Venom healing", "", ""),
    ("mantaray", "Manta Ray", "Swim speed", "", ""),
    ("basilemys", "Basilemys", "Armor", "", ""),
    ("green_turtle", "Green Turtle", "Armor", "", ""),
]


@dataclass(frozen=True)
class FishBuff:
    id: str
    fish: str
    effect: str
    amount: str = ""
    duration: str = ""

    def text(self) -> str:
        """e.g. "Pike - +20% Stamina recovery (2 min)", or "Pike - Stamina recovery up" without a number."""
        what = f"{self.amount} {self.effect}" if self.amount else f"{self.effect} up"
        return f"{self.fish} - {what}" + (f" ({self.duration})" if self.duration else "")


def defaults() -> list[FishBuff]:
    return [FishBuff(*row) for row in DEFAULTS]


def load() -> list[FishBuff]:
    """data/fish_buffs.json if it exists (your own values), else the built-in list."""
    if path().exists():
        try:
            rows = json.loads(path().read_text(encoding="utf-8"))["fish"]
            out = [FishBuff(str(r["id"]), str(r["fish"]), str(r["effect"]), str(r.get("amount") or ""),
                            str(r.get("duration") or "")) for r in rows]
            if out:
                return out
        except (OSError, ValueError, KeyError, TypeError) as e:
            log.warning("fish_buffs.json unreadable (%s); using the built-in list", e)
    return defaults()


def write_file() -> None:
    """Create data/fish_buffs.json from the current list, for editing."""
    data = {
        "_help": "What each fish gives when eaten, shown by the overlay when ticked in the tray menu "
                 "'Fish buffs'. Fill in 'amount' (e.g. \"+20%\") and 'duration' (e.g. \"2 min\") when you "
                 "know them; leave \"\" if not. Add or remove fish freely ('id' must be unique). Then pick "
                 "'Reload config and quests' in the tray menu. Delete this file to go back to the built-in list.",
        "fish": [{"id": b.id, "fish": b.fish, "effect": b.effect, "amount": b.amount, "duration": b.duration}
                 for b in load()],
    }
    path().parent.mkdir(parents=True, exist_ok=True)
    path().write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def notes(selected: list[str]) -> list[tuple]:
    """Legend-style lines (text, colour, "fish") for the selected fish, in list order."""
    chosen = set(selected or [])
    return [(b.text(), COLOR, "fish") for b in load() if b.id in chosen]
