"""Config file handling and project paths."""
from __future__ import annotations

import copy
import json
from pathlib import Path  # noqa: F401 (used by GAME_SETTINGS too)

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
DATA_DIR = ROOT / "data"
MAPS_DIR = DATA_DIR / "maps"
QUESTS_DIR = DATA_DIR / "quests"
CACHE_DIR = ROOT / ".cache"
CALIBRATION_SHOT = DATA_DIR / "calibration.png"

MAP_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")

DEFAULTS: dict = {
    # mss monitor number: 1 = primary monitor, 2 = second monitor, ...
    "monitor": 1,
    # Name of a map image in data/maps (file name without extension).
    "active_map": "",
    # Screen regions are in physical pixels, relative to the monitor's top-left.
    # Set them with calibrate.bat.
    "minimap": {"x": 0, "y": 0, "w": 0, "h": 0, "shape": "circle"},
    "quest_panel": {"x": 0, "y": 0, "w": 0, "h": 0},
    # The screen size the positions above were set for ([width, height]); calibrate.bat fills it in.
    "screen_size": None,
    # Your calibrations per screen size, e.g. {"2560x1440": {"minimap": {...}, "quest_panel": {...}}}.
    # The positions used are picked from here, LAYOUT_PRESETS or the HUD scaling rule.
    "layouts": {},
    # Read Path of Titans' own HUD Scale setting and size the layout to match.
    "use_game_hud_scale": True,

    # --- Minimap tracking ---
    "tracking_fps": 10,
    # Fraction of the minimap radius hidden from matching (the player arrow sits there).
    "center_mask": 0.18,
    # Fraction of the minimap edge ignored (border ring, compass letters).
    "edge_mask": 0.1,
    # Upscale the minimap before feature detection; helps small minimaps.
    "minimap_upscale": 2.0,
    # The full map is downscaled to this size (longest side) for matching. 2048 is about 1:1 with
    # the minimap at 1920x1200; larger is slower and uses more memory without being more accurate.
    "map_work_max_dim": 2048,
    # When the minimap stops matching the selected map, try the other maps and switch automatically.
    "auto_detect_map": True,
    # Seconds without a match before other maps are tried.
    "map_search_after_s": 1.5,
    "min_inliers": 10,
    # Hide markers if no match for this long.
    "lost_after_s": 0.8,
    # 0..1, higher = snappier, lower = smoother markers.
    "smoothing": 0.5,

    # --- In-game map screen ---
    # When the big map is open, put the quest markers on it too.
    "map_screen_mode": True,
    # On the big map, every spawn of the items your quests still need, all over the map (tray
    # toggle). Off: the quest area's spawns plus those near you, the same as on the minimap.
    "map_all_item_spawns": False,
    # Hide quest and Hoard items too deep to reach without diving, for dinosaurs that can't dive
    # (tray toggle). Underwater spawns are baked into data/underwater/ by tools/build_underwater.py.
    "hide_underwater": False,
    # Nesting mode (tray toggle): show every spawn of the nest materials (bones, branches, kelp, mud,
    # reeds, rocks, roots) in their item colours, each with its own marker shape.
    "nesting_mode": False,
    # Seconds without a minimap match before checking whether the map screen is open.
    "map_screen_after_s": 0.3,
    # Also check for the map screen this often while the minimap is tracking (the minimap can still
    # partly show beside the map window).
    "map_screen_probe_s": 1.5,
    # Updates per second while the map screen is open.
    "map_screen_fps": 12,
    # The screenshot is shrunk to this size before matching (faster; still accurate).
    "map_screen_scale": 0.4,
    # Image features used per map-screen update (fewer = faster; 400+ still match reliably).
    "map_screen_features": 800,
    # Agreeing features needed to accept the screen as the map. Raise if it triggers wrongly.
    "map_screen_min_inliers": 40,

    # CPU threads for image matching. Keep low so the game isn't slowed down.
    "cpu_threads": 2,

    # --- Quest panel OCR ---
    "ocr_enabled": True,
    "ocr_interval_s": 1.5,
    "ocr_upscale": 2.0,
    # BCP-47 tag like "en-US"; empty = use Windows display language.
    "ocr_language": "",
    # 0..100 fuzzy-match score needed to count a quest as active.
    "quest_match_threshold": 82,
    # A quest stays active for this many OCR reads after it stops being seen (reads that are
    # clearly incomplete count half). Press F9 to drop finished quests at once.
    "quest_miss_tolerance": 3,
    # Ignore the quest panel and show every quest on the map.
    "show_all_quests": False,

    # Food shown on the maps (tray menu "Food"), as Potservers marker kinds, e.g. ["fish_spawn",
    # "berry"]. All kinds and groups: FOOD_GROUPS in potoverlay/layers.py.
    "layers": [],
    # Fresh water as blue areas, named on the in-game map (tray: Food > Fresh water). Baked data in
    # data/freshwater/ (from VulnonaMAP; see potoverlay/freshwater.py).
    "fresh_water": False,
    # Fish whose buff is shown next to the minimap (tray menu "Fish buffs"), e.g. ["pike"]. The list
    # and the buff values: potoverlay/fishbuffs.py, or data/fish_buffs.json once you edit them.
    "fish_buffs": [],

    # --- Overlay look ---
    "marker_radius": 6,
    "show_labels": False,
    "show_offscreen_arrows": True,
    "show_status": True,
    # Quest names with their marker colour, to the left of the minimap.
    "show_legend": True,
    "default_marker_color": "#ffd23f",

    # --- Behaviour ---
    "only_when_game_focused": True,
    "game_window_titles": ["path of titans", "pathoftitans"],
    # Global hotkey that shows/hides the overlay. Examples: "F8", "ctrl+shift+o".
    "toggle_hotkey": "F8",
    # Global hotkey that re-reads the quest list right now (if a quest went missing). "" = off.
    "refresh_hotkey": "F9",
    # At start, ask Potservers (one small request) whether newer quest data is out, e.g. after a
    # game patch.
    "check_for_data_updates": True,
    # ...and if so, download it right away in the background (the overlay keeps working and switches
    # to the new quests when it's done). False: just show a notification to click instead.
    "auto_update_data": True,
    # run.bat installs a newer version of the overlay itself from GitHub before starting.
    "check_for_app_updates": True,
}

# Where new versions of the overlay come from (GitHub releases); see tools/self_update.py.
UPDATE_REPO = "FluffyTeddyy/Teddys-PoT-Overlay"


# Update speed presets for the tray menu: (name, minimap updates/s, in-game map updates/s, note).
# Any other values can be set by hand as "tracking_fps" / "map_screen_fps" in config.json.
# Rough costs on this PC: a minimap update ~30 ms of CPU, an in-game map update ~40-60 ms, so
# the map screen tops out around 15-20/s however high it's set.
SPEED_PRESETS = [
    ("Power saver", 5, 6, "least CPU"),
    ("Balanced", 10, 12, "default"),
    ("Smooth", 20, 15, "more CPU"),
    # The in-game map maxes out around 15/s on a recent 8-core CPU; asking more just costs CPU.
    ("Fastest", 30, 20, "as fast as your CPU allows"),
]


def speed_preset_name(cfg: dict) -> str | None:
    for name, mm_fps, map_fps, _ in SPEED_PRESETS:
        if float(cfg["tracking_fps"]) == mm_fps and float(cfg["map_screen_fps"]) == map_fps:
            return name
    return None


# Built-in screen positions for tested resolutions (default HUD scale), so most people never need
# to calibrate. calibrate.bat adds others to config.json under "layouts".
LAYOUT_PRESETS: dict[str, dict] = {
    "1920x1200": {
        "minimap": {"x": 1625, "y": 77, "w": 274, "h": 274, "shape": "circle"},
        "quest_panel": {"x": 1540, "y": 385, "w": 360, "h": 620},
    },
    "1920x1080": {
        "minimap": {"x": 1650, "y": 64, "w": 251, "h": 251, "shape": "circle"},
        "quest_panel": {"x": 1565, "y": 345, "w": 350, "h": 640},
    },
}


# The game's HUD scales with the screen's height and is anchored to the top-right corner (measured
# at 1080p, 1200p, 1440p and 4K: identical once divided by height/1080). So one layout in "1080p
# units", measured from the right edge, fits every resolution.
HUD_1080 = {
    # minimap: circle centre 142.5 px left of the right edge, 192 px down, radius 125
    "minimap": {"right": 267.5, "top": 67.0, "size": 250.0},
    # quest list: a column 355 px wide ending 5 px from the right edge, from below the minimap down
    "quest_panel": {"right": 355.0, "top": 345.0, "w": 350.0, "h": 640.0},
}

GAME_SETTINGS = Path.home() / "AppData" / "Local" / "PathOfTitans" / "Saved" / "Config" / "Windows" / \
    "GameUserSettings.ini"


def game_hud_scale() -> float:
    """The game's own HUD Scale setting (1.0 if it can't be read)."""
    try:
        for line in GAME_SETTINGS.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip().lower().startswith("hudscale="):
                v = float(line.split("=", 1)[1])
                return v if 0.3 <= v <= 3.0 else 1.0
    except (OSError, ValueError):
        pass
    return 1.0


def scaled_layout(width: int, height: int, hud_scale: float = 1.0) -> dict:
    """Minimap and quest-list boxes for any screen size, from the HUD_1080 reference."""
    s = min(width, height) / 1080.0 * hud_scale
    mm, qp = HUD_1080["minimap"], HUD_1080["quest_panel"]
    size = round(mm["size"] * s)
    return {
        "minimap": {"x": round(width - mm["right"] * s), "y": round(mm["top"] * s), "w": size, "h": size,
                    "shape": "circle"},
        "quest_panel": {"x": round(width - qp["right"] * s), "y": round(qp["top"] * s),
                        "w": round(qp["w"] * s), "h": min(round(qp["h"] * s), height - round(qp["top"] * s))},
    }


def ocr_upscale_for(cfg: dict, width: int, height: int) -> float:
    """ocr_upscale is tuned for 1080p; the quest text grows with the screen, so enlarge less."""
    return max(1.0, float(cfg["ocr_upscale"]) * 1080.0 / min(width, height))


def apply_layout(cfg: dict, width: int, height: int) -> bool:
    """Use the minimap/quest-list positions for this screen size, in this order: your own
    calibration for that size, a built-in measured preset, then the general HUD scaling rule.
    The presets are measured at the default HUD Scale, so a changed HUD Scale uses the rule."""
    key = f"{width}x{height}"
    hud = game_hud_scale() if cfg.get("use_game_hud_scale", True) else 1.0
    layout = (cfg.get("layouts") or {}).get(key)
    if layout is None and abs(hud - 1.0) < 0.01:
        layout = LAYOUT_PRESETS.get(key)
    if layout is None:
        layout = scaled_layout(width, height, hud)
    cfg["minimap"] = dict(layout["minimap"])
    cfg["quest_panel"] = dict(layout["quest_panel"])
    cfg["screen_size"] = [width, height]
    return True


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user = json.load(f)
        return _merge(DEFAULTS, user)
    return copy.deepcopy(DEFAULTS)


def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")


def region_ok(region: dict | None) -> bool:
    return bool(region) and region.get("w", 0) > 4 and region.get("h", 0) > 4


def list_maps() -> list[str]:
    if not MAPS_DIR.exists():
        return []
    return sorted(p.stem for p in MAPS_DIR.iterdir() if p.suffix.lower() in MAP_EXTENSIONS)


def map_image_path(name: str) -> Path | None:
    for ext in MAP_EXTENSIONS:
        for p in (MAPS_DIR / f"{name}{ext}", MAPS_DIR / f"{name}{ext.upper()}"):
            if p.exists():
                return p
    return None


def quest_file(map_name: str) -> Path:
    return QUESTS_DIR / f"{map_name}.json"
