"""Main app: background workers for tracking and OCR, the overlay, and a tray menu."""
from __future__ import annotations

import json
import logging
import math
import os
import re
import sys
import threading
import time
from collections import deque
from dataclasses import replace

import numpy as np

# One Qt pixel = one physical screen pixel, so overlay coordinates match mss captures.
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")

from PySide6.QtCore import QProcess, QThread, Qt, QTimer, QUrl, Signal  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QAction, QActionGroup, QColor, QDesktopServices, QIcon, QPainter, QPen, QPixmap,
)
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon  # noqa: E402

from . import config as C  # noqa: E402
from . import fishbuffs, freshwater, gamelaunch  # noqa: E402
from .capture import ScreenGrabber  # noqa: E402
from .locator import Fix, MapLocator, build_minimap_mask  # noqa: E402
from .mapdetect import MultiMapTracker  # noqa: E402
from .mapview import MapScreenDetector, MapWindowProbe, layout_view_layer, layout_view_markers  # noqa: E402
from .layers import (FOOD_GROUPS, HOARD_LISTS, available_kinds, hoard_layer, hoard_type,  # noqa: E402
                     layer_legend, load_layers)
from .markers import OverlayState, assign_colors, layout_layer, layout_markers  # noqa: E402
from .overlay import MapScreenOverlay, Overlay  # noqa: E402
from .quests import HOARD_KEY, ActiveQuestTracker, Quest, QuestDB, objective_id  # noqa: E402
from .questlog import QuestLog  # noqa: E402
from .updates import UpdateChecker  # noqa: E402
from .winutil import GlobalHotkey, game_is_focused  # noqa: E402

log = logging.getLogger("potoverlay")


def load_db(map_name: str) -> QuestDB:
    try:
        return QuestDB.load(C.quest_file(map_name), map_name)
    except Exception as e:
        log.error("Could not read quest file for %s: %s", map_name, e)
        return QuestDB(map_name)


def _sleep_until(t_end: float, stop_flag) -> None:
    while not stop_flag() and time.perf_counter() < t_end:
        time.sleep(min(0.05, max(0.0, t_end - time.perf_counter())))


class TrackerWorker(QThread):
    """Captures the minimap, finds it on the full map, and lays out markers."""

    state_ready = Signal(object)         # minimap overlay
    view_ready = Signal(object)          # in-game map screen overlay
    map_screen = Signal(bool)            # in-game map opened / closed
    map_detected = Signal(str, object)   # map name, its QuestDB

    def __init__(self, cfg: dict, db: QuestDB):
        super().__init__()
        self.cfg = cfg
        self.db = db
        self.active: list = []  # (candidate quest keys, title shown in game) per objective
        self._layers: list = []  # always-on map layers (fish spawns...), loaded per map in run()
        # The Hoard event in the quest list, if any: (label, item names read), e.g. ("Hoard: Gemstones",
        # {...}); its layer (every spawn of its items on this map) is built when needed, then cached.
        self._hoard: tuple | None = None
        self._hoard_cache: tuple = (None, None)  # (key, layer)
        self._layers_map = ""
        self.show_all = bool(cfg["show_all_quests"])
        self._stop = False
        self._lock = threading.Lock()
        self._map_name = cfg["active_map"]
        self._map_dirty = True

    def stop(self) -> None:
        self._stop = True

    def set_map(self, name: str, db: QuestDB) -> None:
        with self._lock:
            self._map_name, self.db, self._map_dirty = name, db, True

    def set_active(self, active: list) -> None:
        """(candidate quest keys, title, objective) triples, pairs, or plain candidate sets."""
        self.active = list(active)
        hoard = next((it for it in self.active if isinstance(it, tuple) and len(it) > 2 and it[2] == HOARD_KEY), None)
        # (Hoard entries carry the item names read from the HUD where quests carry candidates.)
        self._hoard = (hoard[1], frozenset(hoard[0])) if hoard else None

    def _leave_map_screen(self) -> None:
        self.view_ready.emit(OverlayState(False))
        self.map_screen.emit(False)

    def _load_layers(self, map_name: str) -> None:
        self._layers = load_layers(map_name, list(self.cfg.get("layers") or []))
        self._layers_map = map_name
        self._fresh = None
        if self.cfg.get("fresh_water"):
            try:  # baked data: data/freshwater/<map>.json
                self._fresh = freshwater.load(map_name)
            except Exception:
                log.exception("Could not work out fresh water for %s", map_name)

    def _shown_layers(self) -> list:
        """Food layers, plus every item of the Hoard event (Gemstones, Beachcomber...) in the quest list."""
        hoard = getattr(self, "_hoard", None)
        if not hoard:
            return self._layers
        label, items = hoard
        # Known Hoard types have a fixed list: a new item misread doesn't need a rebuild.
        key = (self._layers_map, label, None if hoard_type(label) in HOARD_LISTS else items)
        if self._hoard_cache[0] != key:
            self._hoard_cache = (key, hoard_layer(self._layers_map, label, items))
        layer = self._hoard_cache[1]
        return self._layers + ([layer] if layer is not None else [])

    def _zones(self, to_screen, x0: float, y0: float, x1: float, y1: float) -> list:
        fresh = getattr(self, "_fresh", None)
        return fresh.project(to_screen, x0, y0, x1, y1) if fresh else []

    def _legend(self, colors: dict) -> list:
        if not self.cfg["show_legend"]:
            return []
        water = [(freshwater.LABEL, freshwater.COLOR, "zone")] if getattr(self, "_fresh", None) else []
        return sorted(colors.items()) + layer_legend(self._shown_layers()) + water

    def _emit_view(self, view, locator: MapLocator, db: QuestDB, index: dict, player, mon: dict) -> None:
        """Markers for the in-game map screen, in screen pixels."""
        if player is None:  # never saw the minimap: use the middle of the visible map instead
            player = locator.work_to_full(view.fix.apply([[mon["width"] / 2, mon["height"] / 2]]))[0]
        if self.show_all:
            shown = [(q, q.name) for q in db.quests if q.points or q.targets]
        else:
            shown = pick_labeled(self.active, index, player)
        colors = {} if self.show_all else assign_colors([name for _, name in shown])
        items = [(name, colors.get(name) or q.color or self.cfg["default_marker_color"], q.points, q.targets)
                 for q, name in shown]
        markers = layout_view_markers(view, locator, items, mon["width"], mon["height"])
        for layer in self._shown_layers():  # food, Hoard items
            k = layer.kind
            markers += layout_view_layer(view, locator, layer.points, mon["width"], mon["height"],
                                         k.label, k.color, k.style)
        legend = self._legend(colors)
        vx0, vy0 = max(0.0, view.viewport[0]), max(0.0, view.viewport[1])
        vx1, vy1 = min(float(mon["width"]), view.viewport[2]), min(float(mon["height"]), view.viewport[3])
        zones = self._zones(lambda pts: locator.full_to_minimap(view.fix, pts), vx0, vy0, vx1, vy1)
        self.view_ready.emit(OverlayState(True, True, markers, "", legend, (vx0 + 10, vy0 + 10),
                                          zones=zones, clip=(vx0, vy0, vx1, vy1)))

    def _make_locator(self, name: str) -> MapLocator:
        path = C.map_image_path(name)
        if path is None:
            raise FileNotFoundError(f"no map image '{name}' in data/maps")
        self.state_ready.emit(OverlayState(True, False, [], f"loading map {name}..."))
        return MapLocator(path, self.cfg["map_work_max_dim"], C.CACHE_DIR, self.cfg["min_inliers"])

    def run(self) -> None:
        cfg = self.cfg
        mm = cfg["minimap"]
        w, h, shape = int(mm["w"]), int(mm["h"]), mm.get("shape", "circle")
        grab = ScreenGrabber(cfg["monitor"])
        mask = build_minimap_mask(w, h, shape, cfg["center_mask"], cfg["edge_mask"])
        period = 1.0 / max(1.0, float(cfg["tracking_fps"]))
        maps = MultiMapTracker(
            C.list_maps(), self._map_name, self._make_locator,
            auto=bool(cfg["auto_detect_map"]), search_after_s=float(cfg["map_search_after_s"]),
        )
        smooth: np.ndarray | None = None
        last_ok = 0.0
        db = self.db
        index = db.by_key()
        self._load_layers(maps.active or self._map_name)
        mon = grab.monitor
        # minimap_upscale is a first guess tuned for 1080p; the minimap grows with the screen.
        mm_upscale = float(cfg["minimap_upscale"]) * 1080.0 / min(mon["width"], mon["height"])
        detector = MapScreenDetector(
            mon["width"], mon["height"], (int(mm["x"]), int(mm["y"]), w, h),
            scale=float(cfg["map_screen_scale"]), min_inliers=int(cfg["map_screen_min_inliers"]),
            max_features=int(cfg["map_screen_features"]),
        )
        probe = MapWindowProbe(C.DATA_DIR / f"map_probe_{mon['width']}x{mon['height']}.png")
        probe_learned = False  # learned (or confirmed) during this map-screen session
        view_frames = 0
        on_map_screen = False
        view_misses = 0
        last_player = None  # full-map position from the minimap, for picking quest versions
        inlier_hist: deque[int] = deque(maxlen=30)  # recent minimap match strength
        last_probe = 0.0
        while not self._stop:
            t0 = time.perf_counter()
            with self._lock:
                if self._map_dirty:  # map picked by hand in the tray menu
                    maps.set_active(self._map_name)
                    db, index, smooth = self.db, self.db.by_key(), None
                    self._load_layers(self._map_name)
                    self._map_dirty = False

            if cfg["only_when_game_focused"] and not game_is_focused(cfg["game_window_titles"]):
                self.state_ready.emit(OverlayState(False))
                if on_map_screen:
                    on_map_screen = False
                    self._leave_map_screen()
                _sleep_until(t0 + 0.3, lambda: self._stop)
                continue

            # ---- In-game map screen open: follow its pan/zoom until it closes ----
            if on_map_screen:
                locator = maps.locator(maps.active)
                s = probe.score(grab) if probe_learned else None
                if s is not None and s < probe.CLOSED:
                    view = None
                    view_misses = 99  # the window's title bar is gone: closed, no need to wait
                else:
                    screen = grab.grab()
                    view = detector.detect(locator, screen) if locator else None
                if view is not None:
                    view_misses = 0
                    view_frames += 1
                    self._emit_view(view, locator, db, index, last_player, mon)
                    if not probe_learned and view_frames >= 3:  # viewport has settled by now
                        probe_learned = probe.learn(screen, view.viewport)
                else:
                    view_misses += 1
                    # Hide straight away so stale markers never hang around; give the matcher one
                    # more try before leaving map mode (a single frame can fail mid-zoom).
                    self.view_ready.emit(OverlayState(False))
                    if view_misses >= 2:  # map closed
                        on_map_screen = False
                        detector.reset_viewport()
                        inlier_hist.clear()
                        last_probe = time.perf_counter()
                        self._leave_map_screen()
                        if locator:
                            locator.reset()
                busy = time.perf_counter() - t0
                _sleep_until(t0 + max(1.0 / max(1.0, float(cfg["map_screen_fps"])), busy), lambda: self._stop)
                continue
            if maps.locator(maps.active) is None and not maps.auto:
                err = maps.errors.get(maps.active, "no map selected")
                self.state_ready.emit(OverlayState(True, False, [], err))
                _sleep_until(t0 + 0.5, lambda: self._stop)
                continue

            img = grab.grab(mm)
            now = time.perf_counter()
            name, locator, fix, switched = maps.step(img, mask, mm_upscale, now)
            if switched:
                db = load_db(name)
                index, smooth = db.by_key(), None
                self._load_layers(name)
                with self._lock:
                    self._map_name, self.db = name, db
                self.map_detected.emit(name, db)
            if fix is not None:
                smooth = _blend(smooth, fix.M, float(cfg["smoothing"]), w, h)
                last_ok = now
            elif smooth is not None and now - last_ok > cfg["lost_after_s"]:
                smooth = None
                if locator:
                    locator.reset()

            if smooth is not None:
                f = Fix(smooth, 0, 0)
                player = locator.work_to_full(f.apply([[w / 2.0, h / 2.0]]))[0]
                last_player = player
                if self.show_all:
                    shown = [(q, q.name) for q in db.quests if q.points or q.targets]
                else:
                    shown = pick_labeled(self.active, index, player)
                colors = {} if self.show_all else assign_colors([name for _, name in shown])
                items = [
                    (name, colors.get(name) or q.color or cfg["default_marker_color"],
                     locator.full_to_minimap(f, q.points), locator.full_to_minimap(f, q.targets))
                    for q, name in shown
                ]
                arrows = cfg["show_offscreen_arrows"] and not self.show_all
                markers = layout_markers(items, w, h, shape, cfg["marker_radius"], arrows)
                for layer in self._shown_layers():  # food, Hoard items
                    k = layer.kind
                    markers += layout_layer(locator.full_to_minimap(f, layer.points), w, h, shape,
                                            cfg["marker_radius"] + 1, k.label, k.color, k.style)
                if self.show_all:
                    status = "showing all quests"
                else:
                    n = len({it[1] for it in self.active if isinstance(it, tuple)} or {nm for _, nm in shown})
                    status = f"{n} active quest" + ("" if n == 1 else "s")
                zones = self._zones(lambda pts: locator.full_to_minimap(f, pts), 0, 0, w, h)
                self.state_ready.emit(OverlayState(True, True, markers, status, self._legend(colors), zones=zones))
            else:
                status = f"searching... (trying {maps.searching})" if maps.searching else "searching map..."
                self.state_ready.emit(OverlayState(True, False, [], status))

            # Has the in-game map been opened? It's a window, and half the minimap still shows beside
            # it, so the minimap may keep matching (just with far fewer features). Check when:
            #  - the remembered strip of the map window's title bar is on screen (instant, ~1 ms),
            #  - the minimap has been lost for a moment,
            #  - its match suddenly got much weaker than usual,
            #  - and every few seconds anyway (until the title bar has been learned).
            if fix is not None:
                inlier_hist.append(fix.inliers)
            s = probe.score(grab) if cfg["map_screen_mode"] else None
            title_bar = s is not None and s >= probe.OPEN
            weak = (fix is not None and len(inlier_hist) >= 10
                    and fix.inliers < 0.5 * float(np.median(inlier_hist)))
            lost = fix is None and now - last_ok >= cfg["map_screen_after_s"]
            due = now - last_probe >= float(cfg["map_screen_probe_s"])
            if (cfg["map_screen_mode"] and (title_bar or lost or weak or due)
                    and maps.locator(maps.active) is not None):
                last_probe = now
                view = detector.detect(maps.locator(maps.active), grab.grab())
                if view is not None:
                    on_map_screen, view_misses, smooth = True, 0, None
                    view_frames = 1
                    # Found via the title bar: it's confirmed. Found another way: (re)learn it.
                    probe_learned = title_bar
                    self.state_ready.emit(OverlayState(False))  # hide the minimap overlay
                    self.map_screen.emit(True)
                    self._emit_view(view, maps.locator(maps.active), db, index, last_player, mon)
            busy = time.perf_counter() - t0
            # While lost (menus, loading screens), searching is costly: keep it to ~50% of one core.
            _sleep_until(t0 + max(period, busy * 2 if fix is None else 0), lambda: self._stop)
        grab.close()


def _distance(pts: list, player) -> float:
    if not pts:
        return math.inf
    a = np.asarray(pts, np.float64)
    return float(np.min(np.hypot(a[:, 0] - float(player[0]), a[:, 1] - float(player[1]))))


def _item_name(q: Quest, key: str | None) -> str | None:
    """The item a collect/deliver objective is about, as the catalog writes it ("Collect 15 Death
    Cap" -> "Death Cap"); None for other objectives (waystones, places to find)."""
    if not key or not key.startswith(("collect:", "deliver:")):
        return None
    for o in q.objectives:
        if objective_id(o) == key:
            m = re.match(r"^\s*(?:collect|deliver)\s+\d+\s+(.+?)\s*$", o, re.I)
            if m:
                return m[1]
    return key.split(":", 1)[1].title()


def pick_labeled(active: list, index: dict[str, Quest], player) -> list[tuple[Quest, str]]:
    """For each active objective (candidate quests, title shown in game, objective id), the
    nearest catalog version and the name to show. Only the items of objectives listed on the HUD
    get markers: a finished objective leaves the list, and its markers go with it. Item objectives
    are named after their item ("Death Cap", "Chanterelle Mushroom"), so two items of one quest get
    their own colour and legend line; other quests show their title."""
    picked: dict[tuple[str, str], tuple[Quest, str, set]] = {}
    for item in active:
        if isinstance(item, tuple):
            group, label, key = (tuple(item) + (None,))[:3]
        else:
            group, label, key = item, None, None
        if key == HOARD_KEY:  # the Hoard event: shown as a layer of its items, not a quest
            continue
        cands = [index[k] for k in group if k in index]
        if not cands:
            continue
        # Nearest by where this objective's own items are (not the whole quest's).
        q = min(cands, key=lambda c: _distance(c.points_for({key}) + list(c.targets)
                                               + ([c.center] if c.center else []), player))
        name = _item_name(q, key) or label or q.name
        entry = picked.setdefault((name, q.key), (q, name, set()))  # same item twice: once
        if key:
            entry[2].add(key)
    out, ringed = [], set()
    for q, name, ids in picked.values():
        # Places to go (drop-off, waystone) belong to the quest: draw them once, not per item.
        targets = q.targets if q.key not in ringed else []
        ringed.add(q.key)
        out.append((replace(q, points=q.points_for(ids), targets=targets), name))
    return out


def pick_nearest(groups: list[frozenset[str]], index: dict[str, Quest], player) -> list[Quest]:
    """The same quest exists at many POIs; for each active quest use the version closest to the player."""
    out: dict[str, Quest] = {}
    px, py = float(player[0]), float(player[1])

    def dist(q: Quest) -> float:
        pts = q.all_points() + ([q.center] if q.center else [])
        if not pts:
            return math.inf
        a = np.asarray(pts, np.float64)
        return float(np.min(np.hypot(a[:, 0] - px, a[:, 1] - py)))

    for g in groups:
        cands = [index[k] for k in g if k in index]
        if cands:
            best = min(cands, key=dist)
            # Two entries for one quest at one POI (e.g. "Deliver 1 Bones" and "Deliver 3 Bones")
            # would draw the same markers twice.
            out[f"{best.name}|{best.poi}"] = best
    return list(out.values())


def _blend(prev: np.ndarray | None, new: np.ndarray, alpha: float, w: int, h: int) -> np.ndarray:
    """Smooth the minimap transform around the minimap centre; snap on big jumps (respawn, teleport)."""
    if prev is None or alpha >= 1.0:
        return new
    c_local = np.array([w / 2.0, h / 2.0])

    def params(M):
        c = M[:, :2] @ c_local + M[:, 2]
        return c, math.hypot(M[0, 0], M[1, 0]), math.atan2(M[1, 0], M[0, 0])

    pc, ps, pa = params(prev)
    nc, ns, na = params(new)
    if np.linalg.norm(nc - pc) > 0.5 * max(w, h) * ns:
        return new
    da = (na - pa + math.pi) % (2 * math.pi) - math.pi
    c = pc + alpha * (nc - pc)
    s = ps + alpha * (ns - ps)
    a = pa + alpha * da
    R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]]) * s
    return np.hstack([R, (c - R @ c_local).reshape(2, 1)])


class QuestWorker(QThread):
    """Reads the quest panel with OCR and reports which quests are active."""

    active_changed = Signal(object, object)  # set[str], list[str] raw lines
    refreshed = Signal(int)  # manual refresh done: number of quests found (-1: map open, can't read)
    failed = Signal(str)

    def __init__(self, cfg: dict, db: QuestDB):
        super().__init__()
        self.cfg = cfg
        self.tracker = ActiveQuestTracker(db, cfg["quest_match_threshold"], cfg["quest_miss_tolerance"])
        self._lock = threading.Lock()
        self._pending_db: QuestDB | None = None
        self._stop = False
        # Set while the in-game map is open: the quest list isn't on screen then, and whatever is
        # in its place must not make quests look finished.
        self.paused = False
        self._refresh = False
        self.qlog = QuestLog(C.DATA_DIR / "quest_log.txt")

    def stop(self) -> None:
        self._stop = True

    def request_refresh(self) -> None:
        """Re-read the quest list now, thoroughly (F9)."""
        self._refresh = True

    def _quest_names(self) -> list[str]:
        return sorted({name for _, name in self.tracker.labeled})

    def _do_refresh(self, ocr, grab, region, upscale) -> None:
        if self.paused:
            self.refreshed.emit(-1)
            return
        captures: list[list[list[str]]] = []
        for i in range(3):  # a few moments apart: pop-ups and flickers come and go
            if i:
                time.sleep(0.15)
            captures.append(ocr.read_lines_all_ways(grab.grab(region), upscale))
        self.tracker.refresh(captures)
        self.qlog.write("refresh (F9) found", self._quest_names(), [c[0] for c in captures])
        if self.tracker.dropped:
            self.qlog.write("refresh (F9) removed", sorted(set(self.tracker.dropped)), [])
        best = max((r for c in captures for r in c), key=len)
        self.qlog.saw(best)
        self.active_changed.emit(self.tracker.labeled_objectives, best)
        self.refreshed.emit(len(self._quest_names()))

    def set_db(self, db: QuestDB) -> None:
        with self._lock:
            self._pending_db = db

    def run(self) -> None:
        from .ocr import WindowsOcr

        cfg = self.cfg
        try:
            ocr = WindowsOcr(cfg["ocr_language"])
        except Exception as e:
            self.failed.emit(f"OCR unavailable: {e}")
            return
        grab = ScreenGrabber(cfg["monitor"])
        region = cfg["quest_panel"]
        upscale = C.ocr_upscale_for(cfg, grab.monitor["width"], grab.monitor["height"])
        while not self._stop:
            t0 = time.perf_counter()
            with self._lock:
                if self._pending_db is not None:
                    self.tracker.set_db(self._pending_db)
                    self._pending_db = None
            if self._refresh:
                self._refresh = False
                try:
                    self._do_refresh(ocr, grab, region, upscale)
                except Exception:
                    log.exception("Quest refresh failed")
            elif not self.paused and (not cfg["only_when_game_focused"]
                                      or game_is_focused(cfg["game_window_titles"])):
                try:
                    lines = ocr.read_lines(grab.grab(region), upscale)
                    self.tracker.update(lines)
                    self.qlog.saw(lines)
                    if self.tracker.dropped:
                        self.qlog.write("dropped (not seen in the last reads)", sorted(set(self.tracker.dropped)))
                    if self.tracker.added:
                        self.qlog.write("added", sorted(set(self.tracker.added)), [lines])
                    self.active_changed.emit(self.tracker.labeled_objectives, lines)
                except Exception:
                    log.exception("OCR read failed")
            _sleep_until(t0 + float(cfg["ocr_interval_s"]), lambda: self._stop or self._refresh)
        grab.close()
        ocr.close()


def make_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(20, 20, 20), 4))
    p.setBrush(QColor(60, 110, 70))
    p.drawEllipse(4, 4, 56, 56)
    p.setBrush(QColor("#ffd23f"))
    p.drawEllipse(36, 14, 16, 16)
    p.end()
    return QIcon(pm)


class App:
    GAME_CHECK_MS = 3000         # with_game: how often the process list is checked
    GIVE_UP_WAITING_S = 30 * 60  # with_game: quit if the game never starts

    def __init__(self, qapp: QApplication, with_game: bool = False):
        self.qapp = qapp
        self.with_game = with_game
        # with_game: True until Path of Titans is running; no screen capture meanwhile.
        self.waiting_for_game = with_game and not gamelaunch.game_running()
        self._game_misses = 0
        self._wait_started = time.monotonic()
        grab = ScreenGrabber(C.load_config()["monitor"])
        self.monitor = grab.monitor
        grab.close()
        self.cfg = self._load_cfg()
        maps = C.list_maps()
        if not self.cfg["active_map"] and maps:
            self.cfg["active_map"] = maps[0]
        self.last_lines: list[str] = []
        self.active: list[frozenset[str]] = []
        self.db = QuestDB(self.cfg["active_map"])
        self.tracker: TrackerWorker | None = None
        self.quests: QuestWorker | None = None
        self.proc: QProcess | None = None

        self.overlay = Overlay(self.cfg, self.monitor)
        self.map_overlay = MapScreenOverlay(self.cfg, self.monitor)
        self.map_overlay.winId()  # create the native window now, so the first show has no stall
        self._apply_fish_buffs()

        self.tray = QSystemTrayIcon(make_icon())
        self.tray.setToolTip("PoT Quest Overlay")
        self._build_menu()
        self.tray.show()

        self.hotkey = None
        if self.cfg.get("toggle_hotkey"):
            try:
                self.hotkey = GlobalHotkey(self.cfg["toggle_hotkey"])
                self.hotkey.triggered.connect(self.toggle_overlay)
            except ValueError as e:
                log.warning("%s", e)
        self.refresh_hotkey = None
        if self.cfg.get("refresh_hotkey"):
            try:
                self.refresh_hotkey = GlobalHotkey(self.cfg["refresh_hotkey"])
                self.refresh_hotkey.triggered.connect(self.refresh_quests)
            except ValueError as e:
                log.warning("%s", e)

        if self.waiting_for_game:
            self.tray.setToolTip("PoT Quest Overlay - waiting for Path of Titans (click Play in the launcher)")
            if gamelaunch.start_launcher():
                self.tray.showMessage("PoT Quest Overlay", "Opening the Alderon Games Launcher. Click Play: "
                                      "the overlay starts by itself once the game is running.",
                                      QSystemTrayIcon.Information, 6000)
            else:
                self.tray.showMessage("PoT Quest Overlay", "Couldn't find the Alderon Games Launcher. Start "
                                      "Path of Titans as usual: the overlay starts once the game is running.",
                                      QSystemTrayIcon.Information, 8000)
        else:
            self.start_workers()
        if self.with_game:
            self._game_timer = QTimer()
            self._game_timer.timeout.connect(self._check_game)
            self._game_timer.start(self.GAME_CHECK_MS)
        self.overlay.show()
        qapp.aboutToQuit.connect(self.shutdown)

        # Newer quest data out (e.g. after a game patch)? One quick request, in the background.
        self.data_update: str = ""   # Potservers' newer data date, if any
        self._update_proc: QProcess | None = None
        self._update_progress = ""
        self._update_last_line = ""
        self._update_before: set = set()
        self._msg_is_update = False
        self.tray.messageClicked.connect(self._on_message_clicked)
        self.updater = UpdateChecker()
        self.updater.available.connect(self.on_data_update)
        if self.cfg.get("check_for_data_updates", True):
            self.updater.check()

    def _check_game(self) -> None:
        """with_game: start when Path of Titans is running, quit when it's closed. Only looks at the
        process list (cheap), never the screen."""
        running = gamelaunch.game_running()
        if self.waiting_for_game:
            if running:
                self.waiting_for_game = False
                self.tray.setToolTip("PoT Quest Overlay")
                self.start_workers()
            elif time.monotonic() - self._wait_started > self.GIVE_UP_WAITING_S:
                log.info("Path of Titans didn't start; quitting")
                self.qapp.quit()
            return
        self._game_misses = 0 if running else self._game_misses + 1
        if self._game_misses >= 2:  # gone for two checks in a row: the game was closed
            log.info("Path of Titans closed; quitting")
            self.qapp.quit()

    def _load_cfg(self) -> dict:
        cfg = C.load_config()
        C.apply_layout(cfg, self.monitor["width"], self.monitor["height"])
        return cfg

    # ---------- workers ----------
    def start_workers(self) -> None:
        db = self.db = load_db(self.cfg["active_map"])
        self.tracker = TrackerWorker(self.cfg, db)
        self.tracker.state_ready.connect(self.overlay.set_state)
        self.tracker.view_ready.connect(self.map_overlay.set_state)
        self.tracker.map_screen.connect(self.on_map_screen)
        self.tracker.map_detected.connect(self.on_map_detected)
        self.tracker.start()
        if self.cfg["ocr_enabled"] and C.region_ok(self.cfg["quest_panel"]) and not self.cfg["show_all_quests"]:
            self.quests = QuestWorker(self.cfg, db)
            self.quests.active_changed.connect(self.on_active)
            self.quests.refreshed.connect(self.on_refreshed)
            self.quests.failed.connect(lambda msg: self.tray.showMessage("PoT Quest Overlay", msg))
            self.quests.start()
        elif not self.cfg["show_all_quests"]:
            log.warning("Quest panel not calibrated; no quests will show until you calibrate it "
                        "or enable 'Show all quests'")

    def stop_workers(self) -> None:
        for wkr in (self.tracker, self.quests):
            if wkr is not None:
                wkr.stop()
                wkr.wait(5000)
        self.tracker = self.quests = None

    def restart(self) -> None:
        self.stop_workers()
        old_map = self.cfg.get("active_map", "")
        self.cfg = self._load_cfg()
        if not self.cfg["active_map"]:
            self.cfg["active_map"] = old_map
        self.overlay.apply_geometry(self.cfg, self.monitor)
        self.map_overlay.set_state(OverlayState(False))
        self.map_overlay.apply_geometry(self.cfg, self.monitor)
        self._apply_fish_buffs()  # also picks up edited data/fish_buffs.json
        self.active = []
        self._build_menu()
        if not self.waiting_for_game:
            self.start_workers()

    def refresh_quests(self) -> None:
        """F9 / tray: re-read the quest list now."""
        if self.waiting_for_game:
            self.overlay.flash("waiting for Path of Titans to start")
            return
        if self.quests is None:
            self.overlay.flash("quest reading is off (Show all quests?)")
            return
        self.overlay.flash("refreshing quests...")
        self.quests.request_refresh()

    def on_refreshed(self, count: int) -> None:
        if count < 0:
            self.overlay.flash("close the map to refresh quests")
        else:
            self.overlay.flash(f"quests refreshed: {count} found")

    # ---------- quest data updates ----------
    def on_data_update(self, remote: str, local: str) -> None:
        self.data_update = remote
        if self.cfg.get("auto_update_data", True):
            self.run_data_update(auto=True)
            return
        self._build_menu()
        self._notify("New quest data available",
                     f"Potservers has updated quests ({remote[:10]}), e.g. after a game patch. Click here to "
                     "update in the background: keep playing, the overlay keeps working and switches to the new "
                     "quests by itself when it's done (about 15 min).",
                     is_update=True)

    def _notify(self, title: str, text: str, is_update: bool = False) -> None:
        self._msg_is_update = is_update  # so clicking another notification doesn't start an update
        self.tray.showMessage(title, text, QSystemTrayIcon.Information, 10000)

    def _on_message_clicked(self) -> None:
        if self._msg_is_update:
            self.run_data_update()

    UPDATE_ARGS = ["-u", "-m", "tools.fetch_potservers", "--refresh"]

    def run_data_update(self, auto: bool = False) -> None:
        """Update the quest data invisibly in the background. The overlay keeps running on the
        current quests meanwhile (progress in the tray menu/tooltip) and reloads when it's done.
        `auto`: started by itself at launch because Potservers has newer data."""
        if self._update_proc is not None:
            return  # already running
        self._update_before = self._quest_names_on_disk()
        self._update_progress = "starting"
        self._update_last_line = ""
        p = QProcess()
        p.setWorkingDirectory(str(C.ROOT))
        p.setProcessChannelMode(QProcess.MergedChannels)
        p.readyReadStandardOutput.connect(self._on_update_output)
        p.finished.connect(self._on_update_finished)
        self._update_proc = p
        p.start(sys.executable, self.UPDATE_ARGS)
        self._build_menu()
        if auto:
            when = f" ({self.data_update[:10]})" if self.data_update else ""
            self._notify("Updating quest data in the background",
                         f"Potservers has new quest data{when}, e.g. after a game patch; it's downloading now. "
                         "Keep playing: the overlay keeps working and switches to the new quests by itself "
                         "when it's done (about 15 min).")
        else:
            self._notify("Updating quest data in the background",
                         "Keep playing: the overlay keeps working with the current quests and switches to the "
                         "new ones by itself when it's done (about 15 min). Progress is in the tray menu.")

    def _on_update_output(self) -> None:
        text = bytes(self._update_proc.readAllStandardOutput()).decode("utf-8", "replace")
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            self._update_last_line = line
            m = re.match(r"(\d+)/(\d+)$", line)
            if m:
                self._update_progress = f"{m[1]}/{m[2]} quests"
            elif line.startswith("quests:"):
                self._update_progress = "downloading quests"
            elif line.startswith("["):
                self._update_progress = "maps"
        self.tray.setToolTip(f"PoT Quest Overlay - updating quest data: {self._update_progress}"[:127])
        if self.act_update is not None:
            self.act_update.setText(self._update_text())

    def _update_text(self) -> str:
        return f"Updating quest data in the background: {self._update_progress} (keep playing)"

    def _on_update_finished(self, code: int, _status=None) -> None:
        self._finished_proc = self._update_proc  # keep it alive until Qt is done with this signal
        self._update_proc = None
        if code == 0:
            self.data_update = ""
            after = self._quest_names_on_disk()
            new, gone = len(after - self._update_before), len(self._update_before - after)
            self.restart()  # loads the new quest files
            self._notify("Quest data updated",
                         f"Now using the new quests ({new} new, {gone} removed). Nothing else to do.")
        else:
            log.warning("Quest data update failed: %s", self._update_last_line)
            self._build_menu()
            self._notify("Quest data update stopped",
                         f"{self._update_last_line[:120]}\nNo harm done: the overlay still uses the current "
                         "quests. Try again from the tray menu; it continues where it stopped.")

    @staticmethod
    def _quest_names_on_disk() -> set:
        out = set()
        for p in C.QUESTS_DIR.glob("*.json"):
            try:
                out |= {(p.stem, q["name"]) for q in json.loads(p.read_text(encoding="utf-8"))["quests"]}
            except (OSError, ValueError, KeyError):
                pass
        return out

    def on_map_screen(self, is_open: bool) -> None:
        if self.quests:
            self.quests.paused = is_open

    def on_map_detected(self, name: str, db: QuestDB) -> None:
        self.cfg["active_map"] = name
        self.db = db
        self.active = []
        if self.quests:
            self.quests.set_db(db)
        self._save_cfg_key("active_map", name)
        self._build_menu()
        self.tray.showMessage("PoT Quest Overlay", f"Detected map: {name.title()}",
                              QSystemTrayIcon.Information, 3000)

    def active_names(self) -> list[str]:
        return sorted({item[1] for item in self.active})

    def on_active(self, labeled: list, lines: list) -> None:
        self.active = list(labeled)  # (candidate quest keys, title) per objective
        self.last_lines = list(lines)
        if self.tracker:
            self.tracker.set_active(self.active)
        names = ", ".join(self.active_names()) or "none"
        self.tray.setToolTip(f"PoT Quest Overlay - active: {names}"[:127])

    # ---------- tray menu ----------
    def _build_menu(self) -> None:
        menu = QMenu()
        a = QAction(f"Show overlay ({self.cfg.get('toggle_hotkey') or 'no hotkey'})", menu, checkable=True)
        a.setChecked(not self.overlay.user_hidden)
        a.toggled.connect(self.set_overlay_visible)
        self.act_show = a
        menu.addAction(a)

        a = QAction("Markers on the in-game map too", menu, checkable=True)
        a.setChecked(bool(self.cfg["map_screen_mode"]))
        a.toggled.connect(self.set_map_screen_mode)
        menu.addAction(a)

        a = QAction("Show all quests (ignore quest panel)", menu, checkable=True)
        a.setChecked(bool(self.cfg["show_all_quests"]))
        a.toggled.connect(self.set_show_all)
        menu.addAction(a)

        maps_menu = menu.addMenu("Map")
        a = QAction("Auto-detect map", maps_menu, checkable=True)
        a.setChecked(bool(self.cfg["auto_detect_map"]))
        a.toggled.connect(self.set_auto_detect)
        maps_menu.addAction(a)
        maps_menu.addSeparator()
        group = QActionGroup(maps_menu)
        maps = C.list_maps()
        for name in maps:
            act = QAction(name, maps_menu, checkable=True)
            act.setChecked(name == self.cfg["active_map"])
            act.triggered.connect(lambda _=False, n=name: self.set_map(n))
            group.addAction(act)
            maps_menu.addAction(act)
        if not maps:
            maps_menu.addAction("(put map images in data/maps)").setEnabled(False)

        food_menu = menu.addMenu("Food")
        a = QAction("Fresh water (blue areas)", food_menu, checkable=True)
        a.setChecked(bool(self.cfg.get("fresh_water")))
        a.toggled.connect(self.set_fresh_water)
        food_menu.addAction(a)
        food_menu.addSeparator()
        on = set(self.cfg.get("layers") or [])
        here = available_kinds(self.cfg.get("active_map", ""))
        for group in FOOD_GROUPS:
            ids = [kid for kid, _ in group.items]
            n_on = sum(kid in on for kid in ids)
            sub = food_menu.addMenu(f"{group.name} ({n_on} on)" if n_on else group.name)
            if len(ids) > 1:
                act = QAction(f"All {group.name.lower()}", sub, checkable=True)
                act.setChecked(n_on == len(ids))
                act.toggled.connect(lambda checked, k=ids: self.set_layers(k, checked))
                sub.addAction(act)
                sub.addSeparator()
            for kid, label in group.items:
                missing = here and kid not in here
                act = QAction(label + (" (not on this map)" if missing else ""), sub, checkable=True)
                act.setChecked(kid in on)
                act.toggled.connect(lambda checked, k=kid: self.set_layers([k], checked))
                sub.addAction(act)
        if on:
            food_menu.addSeparator()
            food_menu.addAction("Hide all food", lambda: self.set_layers(sorted(on), False))

        chosen = set(self.cfg.get("fish_buffs") or [])
        buffs = fishbuffs.load()
        n_on = sum(b.id in chosen for b in buffs)
        buff_menu = menu.addMenu(f"Fish buffs ({n_on} shown)" if n_on else "Fish buffs")
        for b in buffs:
            act = QAction(b.text(), buff_menu, checkable=True)
            act.setChecked(b.id in chosen)
            act.toggled.connect(lambda checked, k=b.id: self.set_fish_buff(k, checked))
            buff_menu.addAction(act)
        buff_menu.addSeparator()
        if chosen:
            buff_menu.addAction("Hide all fish buffs", lambda: self.set_fish_buffs([]))
        buff_menu.addAction("Edit buff values...", self.edit_fish_buffs)

        speed_menu = menu.addMenu("Update speed")
        speed_group = QActionGroup(speed_menu)
        current = C.speed_preset_name(self.cfg)
        for name, mm_fps, map_fps, note in C.SPEED_PRESETS:
            act = QAction(f"{name}: minimap {mm_fps}/s, in-game map {map_fps}/s ({note})", speed_menu,
                          checkable=True)
            act.setChecked(name == current)
            act.triggered.connect(lambda _=False, a=mm_fps, b=map_fps: self.set_speed(a, b))
            speed_group.addAction(act)
            speed_menu.addAction(act)
        if current is None:
            act = QAction(f"Custom: minimap {self.cfg['tracking_fps']:g}/s, in-game map "
                          f"{self.cfg['map_screen_fps']:g}/s (from config.json)", speed_menu, checkable=True)
            act.setChecked(True)
            act.setEnabled(False)
            speed_menu.addAction(act)
        speed_menu.addSeparator()
        speed_menu.addAction("Set your own in config.json...", self.edit_config)

        menu.addSeparator()
        menu.addAction(f"Refresh quests now ({self.cfg.get('refresh_hotkey') or 'no hotkey'})",
                       self.refresh_quests)
        menu.addAction("Show last quest panel text...", self.show_last_text)
        menu.addAction("Open quest log...",
                       lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(C.DATA_DIR / "quest_log.txt"))))
        menu.addAction("Quest editor...", lambda: self.run_tool("tools.quest_editor"))
        menu.addAction("Calibrate screen regions...", lambda: self.run_tool("tools.calibrate"))
        menu.addAction("Make desktop shortcut: Path of Titans + Overlay", self.make_game_shortcut)
        menu.addAction("Reload config and quests", self.restart)
        if getattr(self, "_update_proc", None) is not None:
            self.act_update = menu.addAction(self._update_text())
            self.act_update.setEnabled(False)
        else:
            new = getattr(self, "data_update", "")
            self.act_update = menu.addAction(
                f"Update quest data: new from {new[:10]} (runs in the background)" if new
                else "Update quest data (runs in the background)", self.run_data_update)
        menu.addSeparator()
        menu.addAction("Map and quest data from Potservers (potservers.net)",
                       lambda: QDesktopServices.openUrl(QUrl("https://potservers.net")))
        menu.addAction("Fresh water from VulnonaMAP by Coco.N (vulnona.com/game/map)",
                       lambda: QDesktopServices.openUrl(QUrl("https://vulnona.com/game/map/")))
        menu.addSeparator()
        menu.addAction("Quit", self.qapp.quit)
        self.menu = menu  # keep a reference
        self.tray.setContextMenu(menu)

    def toggle_overlay(self) -> None:
        self.act_show.setChecked(self.overlay.user_hidden)

    def set_overlay_visible(self, on: bool) -> None:
        self.overlay.user_hidden = not on
        self.overlay.update()
        self.map_overlay.user_hidden = not on
        self.map_overlay.set_state(self.map_overlay.state)

    def set_map_screen_mode(self, on: bool) -> None:
        self._save_cfg_key("map_screen_mode", on)
        self.restart()

    def set_layers(self, kinds: list[str], on: bool) -> None:
        """Tray "Food": show or hide food kinds (one item, or a whole group with "All ...")."""
        layers = [k for k in (self.cfg.get("layers") or []) if k not in kinds] + (list(kinds) if on else [])
        self.cfg["layers"] = layers
        self._save_cfg_key("layers", layers)
        self.restart()

    def set_layer(self, kind: str, on: bool) -> None:
        self.set_layers([kind], on)

    def set_fresh_water(self, on: bool) -> None:
        """Tray "Food > Fresh water": blue areas on the minimap and the in-game map."""
        self.cfg["fresh_water"] = on
        self._save_cfg_key("fresh_water", on)
        self.restart()

    def _apply_fish_buffs(self) -> None:
        notes = fishbuffs.notes(self.cfg.get("fish_buffs") or [])
        self.overlay.set_notes(notes)
        self.map_overlay.set_notes(notes)

    def set_fish_buff(self, fish_id: str, on: bool) -> None:
        """Tray "Fish buffs": show or hide one fish's buff next to the minimap. No restart needed."""
        ids = [k for k in (self.cfg.get("fish_buffs") or []) if k != fish_id] + ([fish_id] if on else [])
        self.set_fish_buffs(ids)

    def set_fish_buffs(self, ids: list[str]) -> None:
        self.cfg["fish_buffs"] = list(ids)
        self._save_cfg_key("fish_buffs", list(ids))
        self._apply_fish_buffs()
        QTimer.singleShot(0, self._build_menu)  # not while the menu's own signal is running

    def edit_fish_buffs(self) -> None:
        """Open data/fish_buffs.json to fill in the numbers; 'Reload config and quests' applies them."""
        if not fishbuffs.path().exists():
            fishbuffs.write_file()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(fishbuffs.path())))
        self.tray.showMessage(
            "PoT Quest Overlay",
            "Fill in \"amount\" (e.g. \"+20%\") and \"duration\" (e.g. \"2 min\") for each fish, save, then pick "
            "\"Reload config and quests\" from this menu.",
            QSystemTrayIcon.Information, 8000)

    def set_speed(self, minimap_fps: float, map_fps: float) -> None:
        self._save_cfg_key("tracking_fps", minimap_fps)
        self._save_cfg_key("map_screen_fps", map_fps)
        self.restart()

    def edit_config(self) -> None:
        """Open config.json in the default editor; 'Reload config and quests' applies changes."""
        if not C.CONFIG_PATH.exists():
            C.save_config(C.load_config())
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(C.CONFIG_PATH)))
        self.tray.showMessage(
            "PoT Quest Overlay",
            "Change \"tracking_fps\" (minimap) and \"map_screen_fps\" (in-game map), save, then pick "
            "\"Reload config and quests\" from this menu.",
            QSystemTrayIcon.Information, 8000)

    def set_show_all(self, on: bool) -> None:
        self.cfg["show_all_quests"] = on
        self._save_cfg_key("show_all_quests", on)
        self.restart()

    def set_auto_detect(self, on: bool) -> None:
        self._save_cfg_key("auto_detect_map", on)
        self.restart()

    def set_map(self, name: str) -> None:
        self.cfg["active_map"] = name
        self._save_cfg_key("active_map", name)
        db = self.db = load_db(name)
        self.active = []
        if self.tracker:
            self.tracker.set_map(name, db)
        if self.quests:
            self.quests.set_db(db)

    def _save_cfg_key(self, key: str, value) -> None:
        disk = C.load_config()
        disk[key] = value
        try:
            C.save_config(disk)
        except OSError as e:
            QMessageBox.warning(None, "PoT Quest Overlay", f"Could not save config.json:\n{e}")

    def show_last_text(self) -> None:
        text = "\n".join(self.last_lines) or "(nothing read yet - is the quest panel calibrated and the game focused?)"
        index = self.db.by_key()
        found = []
        for g, name, *_ in self.active:
            qs = [index[k] for k in g if k in index]
            if qs:
                pois = sorted({q.poi for q in qs if q.poi})
                items = sorted({o for q in qs for o in q.objectives})[:1]
                what = f": {items[0]}" if items else ""
                where = f" ({len(pois)} possible POIs, nearest is used)" if len(pois) > 1 else (
                    f" ({pois[0]})" if pois else "")
                found.append(f"- {name}{what}{where}")
        active = "\n".join(found) or "none"
        QMessageBox.information(None, "Quest panel text", f"OCR read:\n\n{text}\n\nMatched quests:\n{active}")

    def run_tool(self, module: str) -> None:
        if self.proc is not None and self.proc.state() != QProcess.NotRunning:
            return
        self.proc = QProcess()
        self.proc.setWorkingDirectory(str(C.ROOT))
        self.proc.finished.connect(lambda *_: self.restart())
        self.proc.start(sys.executable, ["-m", module])

    def make_game_shortcut(self) -> None:
        """Tray: a desktop shortcut that opens the Alderon launcher and this overlay together."""
        try:
            lnk = gamelaunch.create_shortcut(C.ROOT)
        except Exception as e:
            QMessageBox.warning(None, "PoT Quest Overlay", f"Couldn't create the shortcut:\n{e}")
            return
        found = gamelaunch.find_launcher() is not None
        self.tray.showMessage(
            "PoT Quest Overlay",
            f"'{lnk.stem}' is on your desktop. It opens the Alderon Games Launcher"
            + ("" if found else " (not found on this PC: start the game yourself)")
            + " and the overlay, which waits for the game without using the screen and closes with it.",
            QSystemTrayIcon.Information, 8000)

    def shutdown(self) -> None:
        self.stop_workers()
        for hk in (self.hotkey, self.refresh_hotkey):
            if hk:
                hk.stop()
        self.tray.hide()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Create Qt before anything else touches the screen: Qt sets the process's DPI awareness
    # (per-monitor v2), and Windows only allows that once. Setting it earlier ourselves made Qt
    # print "SetProcessDpiAwarenessContext() failed: Access is denied".
    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    cfg = C.load_config()
    # OpenCV uses every core by default; the game needs them more than we do.
    import cv2

    cv2.setNumThreads(int(cfg["cpu_threads"]))
    if not C.list_maps():
        QMessageBox.information(
            None, "PoT Quest Overlay",
            f"No map images found in\n{C.MAPS_DIR}\n\nRun setup.bat to download them.",
        )
        return 1
    # Screen positions are worked out for any resolution (C.apply_layout, in App).
    # --with-game (the "Path of Titans + Overlay" desktop shortcut): open the Alderon launcher,
    # wait for the game without capturing the screen, and quit when the game closes.
    app = App(qapp, with_game="--with-game" in sys.argv)  # noqa: F841 (keeps everything alive)
    return qapp.exec()
