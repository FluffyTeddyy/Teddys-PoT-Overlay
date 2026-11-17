# Teddy's PoT Overlay

**Download:** the zip from the [latest release](https://github.com/FluffyTeddyy/Teddys-PoT-Overlay/releases/latest), unzip, run `run.bat`. It updates itself after that.

A small Windows overlay for **Path of Titans** that puts markers for your active quests on the in-game minimap.

How it works:

1. **Captures the minimap** about 10 times a second and **finds that patch on the full map** with OpenCV SIFT features + RANSAC. This gives your exact position (and the minimap's zoom and rotation).
2. **Reads the quest list** on the right of the screen with the OCR engine built into Windows, e.g. `Gather Mushrooms` / `- Collect 15 Milk Cap Mushrooms 0/15`. Each objective is tracked by its item, under its quest's title (a quest with three objectives gets one name and colour). OCR is noisy, so a quest only appears after being read twice and only disappears after a few reads without it; F9 refreshes on the spot.
3. **Looks the quests up** in a catalog of every quest on the map. The same quest usually exists at several POIs, so it picks the one nearest to you.
4. **Draws markers** only for the objectives listed on your HUD: when you've gathered enough chanterelles and the game takes that line off the list, their markers go too (the death caps you still need stay). The **Hoard: Scavenger** event isn't in Potservers' data, so while it's in your quest list every spawn of its items (bones, branches, shells, turtle shells...) is shown in yellow. Markers: a dot on every spawn point of the quest's items (milk caps, cicada molts, bones…) and a ring on places to go (waystones, drop-off points, POIs). Each item gets its own colour and legend line named after it ("Death Cap", "Chanterelle Mushroom"), so two items of one quest are easy to tell apart; other quests (waystones, places to find) show their title, with a legend left of the minimap. A quest with nothing on the minimap gets an arrow on the edge pointing toward it. Everything listed counts: the game removes finished objectives from the list itself, so progress counters (which OCR often garbles) aren't used.
5. **Detects the map automatically**: if the minimap stops matching the selected map for a moment, it tries the others and switches when one matches clearly (a tray notification says which).
6. **Works on the in-game map too**: open the big map and your active quests' markers appear on it within a fraction of a second, kept inside the map window and following as you pan and zoom (12 updates a second). The first time, it learns what the map window's title bar looks like; after that opening and closing the map is picked up almost instantly. Close it and the minimap markers come back. Toggle with *Markers on the in-game map too* in the tray menu.

It uses about half of one CPU core while tracking (10 updates a second).

It only reads pixels from the screen. It never touches game files or memory. The overlay window is hidden from screen capture, so its markers don't confuse its own matching (they also won't show up in OBS or screenshots).

**Map images, item spawn points and quest data come from [Potservers](https://potservers.net)** (free API, used with credit). **Fresh water from [VulnonaMAP](https://vulnona.com/game/map/)** by Coco.N (its data is shared as "collective intelligence"; the author asks for a link beside work that uses it). Unofficial; Path of Titans belongs to Alderon Games.

## Quick start

1. Download `PoT-Quest-Overlay-<version>.zip` from the [latest release](https://github.com/FluffyTeddyy/Teddys-PoT-Overlay/releases/latest) and unzip it somewhere **outside** Documents/Desktop, e.g. `C:\Games\pot-quest-overlay` (Windows' ransomware protection can block it inside Documents).
2. Double-click **`run.bat`**. The first time, it sets itself up: it finds Python 3.12 (or offers to install it), creates its own `.venv` folder, installs the packages (~200 MB download, a few minutes) and downloads the maps (~21 MB). After that it starts in a second or two.
3. In Path of Titans use **Windowed Fullscreen / Borderless**.
4. No setup for your screen size: the game's HUD scales with screen height from the top-right corner, so the app works out where the minimap and quest list are for any resolution (tested at 1080p, 1200p, 1440p and 4K). It also reads the game's own *HUD Scale* setting. Only if markers look off (unusual HUD settings) run `calibrate.bat`: have the game open, switch to it within 5 seconds, then drag a box around the minimap (Enter) and the quest list (Enter). That's remembered per resolution.

## Updates (automatic)

The overlay updates itself: each time `run.bat` starts, it asks GitHub (one quick request) whether a newer version is out, and if so downloads and installs it before starting, then shows what changed. Your own files are never touched: `config.json`, the `.venv`, map images, downloaded data, the quest log, your fish buff values and quests you made in the quest editor. Offline? It just starts the version you have. Turn it off with `"check_for_app_updates": false` in config.json.

## Getting it / sharing it

Everyone downloads it from GitHub: the zip on the [latest release](https://github.com/FluffyTeddyy/Teddys-PoT-Overlay/releases/latest) (about 0.5 MB: the code and quest data; `run.bat` sets up the rest on first start). To share it, send the link. Updates arrive by themselves.

(For the maintainer: `python -m tools.publish_release "what changed"` bumps the version, builds the zip, pushes and creates the GitHub release.)

## Setup

`setup.bat` does everything (and `run.bat` calls it automatically when something is missing). To rebuild the quest data from scratch instead of using the included files:

```bash
.venv\Scripts\python.exe -m tools.fetch_potservers
```

1. **Game settings:** use **Windowed Fullscreen / Borderless**. Exclusive fullscreen draws over every overlay.
2. **Screen positions** are worked out automatically for any resolution (see Quick start). Only if markers look off, run `calibrate.bat`, switch to the game within 5 seconds, and drag a box around the minimap (Enter), then around the quest list (Enter).
3. **Play:** run `run.bat`. The app sits in the system tray; right-click the icon for options. The map is detected automatically.

## Start it together with the game

Tray menu → **Make desktop shortcut: Path of Titans + Overlay**. That shortcut opens the Alderon Games Launcher and the overlay together; click Play as usual. (The launcher has to start the game itself: it signs you in each time, so the game can't be started directly.) Until the game is running the overlay doesn't look at your screen at all, it only checks every 3 seconds whether the game's process exists; then it starts tracking, and it closes when you quit the game. The launcher is found wherever it's installed (its Windows uninstall entry or its `alderon-games://` link), and the shortcut gets its icon. Plain `run.bat` still works as before.

## Tray menu

| Item | What it does |
|---|---|
| Show overlay (F8) | Show/hide markers. F8 works in game too (change `toggle_hotkey` in config.json) |
| Refresh quests now (F9) | Rebuilds the quest list from what's on screen right now: 3 captures a moment apart, each read 3 ways; a quest counts if it's in at least 2 of the 3 captures. Brings back a quest that went missing and removes ones you've finished. "quests refreshed: N found" shows under the minimap. Change with `refresh_hotkey` |
| Open quest log | `data\quest_log.txt`: when quests were added or dropped, with what was read just before. Useful to see why one went missing |
| Markers on the in-game map too | Put quest markers on the big map when it's open |
| Show all quests | Ignore the quest list and show every quest's points on the minimap |
| Map | Auto-detect on/off, or pick Riparia, Gondwa or Panjura by hand |
| Food > Fresh water | Lakes, ponds and rivers you can drink from as translucent blue areas, on the minimap and the in-game map. Built in (`data\freshwater\`), nothing is worked out on your PC. Gondwa and Panjura: the drinking water mapped by hand on [VulnonaMAP](https://vulnona.com/game/map/) (by Coco.N). Riparia: VulnonaMAP only names its water spots, so the outlines come from the map image (inland water: not connected to the sea), kept where VulnonaMAP names a spot; Riparia's big delta joins the sea and isn't marked |
| Food | Show where food is, on the minimap and the in-game map, independent of quests. Groups: Fish (fish, diseased fish, krill feeding areas), Shellfish, Meat (carrion, critter burrows), Insects, Fruits & berries, Nuts & seeds, Mushrooms (edible ones), Plants & herbs. Each group has an "All …" switch and one per item; each group has its own marker shape (fish shape for fish, triangles for meat, squares for insects…). "Hide all food" turns everything off |
| Fish buffs | Tick a fish and a reminder of its buff shows left of the minimap (and in the in-game map's legend), e.g. "Pike - Stamina recovery up". Covers Pike, Tuna, Catfish, Trout, Kelp Shark, Arapaima, River Stingray, Manta Ray, Basilemys and Green Turtle (from pathoftitans.wiki). Only Arapaima's numbers are known there (+20%, 2 min); *Edit buff values...* opens `data\fish_buffs.json` to fill in the rest (e.g. `"amount": "+20%"`), then *Reload config and quests* |
| Update speed | How often markers update: Power saver (5/6 per second), Balanced (10/12, default), Smooth (20/15), Fastest (30/20). First number is the minimap, second the in-game map. *Set your own in config.json* opens the file: change `tracking_fps` and `map_screen_fps`, save, then *Reload config and quests* |
| Show last quest panel text | What OCR read, and which quests it matched |
| Quest editor | Browse quests, or add your own markers |
| Calibrate | Re-mark minimap and quest list positions |

## Updating the data (after a game patch)

Potservers updates its quest data a while after each game patch. **The overlay takes care of it by itself:** each time it starts it checks Potservers (one small request, under a second, in the background), and if newer data is out it downloads it **in the background while you keep playing**. A tray notification says so, progress shows in the tray menu, and the overlay switches to the new quests by itself when it's done. If you quit halfway, the next start continues where it stopped.

Settings in config.json: `auto_update_data: false` only shows a notification to click instead; `check_for_data_updates: false` turns the check off completely. *Update quest data* in the tray menu runs it by hand.

The update itself is **`update-data.bat`** (about 15 minutes: every quest is re-downloaded, slowly, to respect the site's limits). It:

- re-downloads every quest, so changed objectives are picked up,
- removes quests the patch took out, and adds new ones,
- downloads map images only if they changed,
- continues where it stopped if it gets interrupted (just run it again).

Afterwards pick *Reload config and quests* in the tray menu (or restart the overlay). Quests you made yourself in the quest editor are kept; edits to imported quests are replaced. Every PC updates its own quest data this way, so there's nothing to send around.

The same from a terminal:

```bash
.venv\Scripts\python.exe -m tools.fetch_potservers --refresh
```

## Tuning (`config.json`)

All settings are listed with comments in [potoverlay/config.py](potoverlay/config.py). The most useful ones:

- `quest_match_threshold` (0–100): lower it if quests aren't detected, raise it if wrong ones are.
- `quest_miss_tolerance`: how many OCR reads a quest may be missing before its markers disappear.
- `smoothing`: lower = steadier markers, higher = snappier.
- `marker_radius`, `show_labels`, `show_offscreen_arrows`, `show_legend`: marker look.
- `auto_detect_map`, `map_search_after_s`: automatic map switching.
- `cpu_threads`: CPU threads for matching (default 2, so the game keeps its CPU).
- `map_screen_mode`, `map_screen_fps`, `map_screen_min_inliers`: in-game map markers. Raise `map_screen_min_inliers` if something other than the map ever triggers them.
- `only_when_game_focused`: markers hide when you alt-tab.

## Tools and tests

| Command | What it does |
|---|---|
| `test-match.bat` | Finds your position in `data/calibration.png` and writes `data/locator_debug.png` |
| `python -m tools.preview` | Draws the overlay onto `data/calibration.png` (OCR + matching + markers) → `data/preview.png` |
| `python -m tools.build_quests` | Rebuild `data/quests/*.json` from the downloaded data |
| `python -m tools.fetch_vulnona` | Re-download VulnonaMAP's water data (3 small files) and rebuild `data/freshwater/*.json` (`python -m tools.build_freshwater` rebuilds without downloading) |
| `python -m tests.test_basics` | OCR, quest matching (incl. a real HUD screenshot), markers, capture hiding |
| `python -m tests.test_e2e` | Shows a fake minimap + quest list for a few seconds and runs the whole app against them |
| `python -m tests.test_mapview` | In-game map detection on simulated map screens; gameplay screenshots must not trigger it |
| `python -m tests.test_modes` | Minimap → map screen → minimap switching, with scripted frames |
| `python -m tests.test_speed_menu` | Tray *Update speed* presets and custom values from config.json |
| `python -m tests.test_refresh` | F9 quest refresh, the quest log, and that F9 can be registered |
| `python -m tests.test_layers` | Food layers: data (fish spawns are on water), placement, the Food menu, no needless repaints |
| `python -m tests.test_update_flow` | Tray "Update quest data": runs in the background, shows progress, overlay keeps working, reloads by itself |
| `python -m tests.test_update_check` | The startup check for newer quest data (faked answers: newer, same, offline) |
| `python -m tests.test_updater` | The data updater against a fake Potservers: removed/new quests, unchanged images, resuming |
| `python -m tests.test_live_reads` | Quest tracking on 127 real, noisy OCR reads from a play session (stable list, right titles, F9) |
| `python -m tests.replay_log --extract` | Turn your current `data\quest_log.txt` into new replay data for that test |
| `python -m tools.test_locator --synthetic` | Stress-test the minimap matcher on a generated map |

## Project layout

```
potoverlay/          the app
  app.py             tray, worker threads, picking the nearest quest variant
  locator.py         minimap -> full map matching
  mapdetect.py       automatic map switching
  mapview.py         recognising the in-game map screen
  ocr.py             Windows OCR wrapper (+ brightening coloured quest titles)
  quests.py          quest catalog, parsing the quest list, matching
  markers.py         dot/ring/arrow placement
  overlay.py         the transparent window
tools/               fetch_potservers, build_quests, calibrate, quest_editor, test_locator
tests/               checks that run without the game
data/maps/           full map images (4096 px)
data/quests/         quest catalogs per map (full-map pixel coordinates)
data/potservers/     raw downloaded data
```

## Notes

- This folder lives outside Documents because Windows Defender's Controlled Folder Access blocked Python from writing there.
- Some community servers have rules about third-party map helpers. Check before using this on one.
