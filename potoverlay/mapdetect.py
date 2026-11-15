"""Track the minimap on the selected map, and switch maps automatically when it's clearly another one."""
from __future__ import annotations

import logging
import math
from typing import Callable

import numpy as np

from .locator import Fix, MapLocator

log = logging.getLogger(__name__)


class MultiMapTracker:
    """Wraps one MapLocator per map.

    Every frame the selected map is tried. Once it has failed for `search_after_s`, one other map is
    also tried per frame (round robin). A switch needs `confirm` matches in a row on that map, each
    with at least `min_switch_inliers` agreeing features, so a menu or loading screen can't trigger it.
    """

    def __init__(
        self,
        map_names: list[str],
        active: str,
        make_locator: Callable[[str], MapLocator],
        auto: bool = True,
        search_after_s: float = 1.5,
        confirm: int = 3,
        min_switch_inliers: int = 25,
    ):
        self.names = list(map_names)
        self.active = active if active in self.names else (self.names[0] if self.names else "")
        self.make_locator = make_locator
        self.auto = auto
        self.search_after_s = search_after_s
        self.confirm = confirm
        self.min_switch_inliers = min_switch_inliers
        self.errors: dict[str, str] = {}
        self._locators: dict[str, MapLocator] = {}
        self._last_ok: float | None = None
        self._rr = 0
        self._streak_map: str | None = None
        self._streak = 0
        self.searching: str | None = None  # map being tried right now, for the status text

    def locator(self, name: str) -> MapLocator | None:
        if name not in self._locators and name not in self.errors:
            try:
                self._locators[name] = self.make_locator(name)
            except Exception as e:  # missing/corrupt map image
                log.exception("Could not load map %s", name)
                self.errors[name] = str(e)
        return self._locators.get(name)

    def set_active(self, name: str) -> None:
        if name != self.active:
            self.active = name
            self._last_ok = None
            self._streak_map, self._streak = None, 0
            loc = self._locators.get(name)
            if loc:
                loc.reset()

    def step(self, img: np.ndarray, mask: np.ndarray | None, upscale: float, now: float
             ) -> tuple[str, MapLocator | None, Fix | None, bool]:
        """Returns (active map, its locator, fix or None, whether the map just changed)."""
        loc = self.locator(self.active) if self.active else None
        fix = loc.locate(img, mask, upscale) if loc else None
        if fix is not None:
            self._last_ok = now
            self._streak_map, self._streak = None, 0
            self.searching = None
            return self.active, loc, fix, False

        lost_for = math.inf if self._last_ok is None else now - self._last_ok
        others = [n for n in self.names if n != self.active and n not in self.errors]
        if not (self.auto and others and lost_for >= self.search_after_s):
            self.searching = None
            return self.active, loc, None, False

        cand = self._streak_map or others[self._rr % len(others)]
        self.searching = cand
        cl = self.locator(cand)
        cf = cl.locate(img, mask, upscale) if cl else None
        if cf is not None and cf.inliers >= self.min_switch_inliers:
            self._streak = self._streak + 1 if self._streak_map == cand else 1
            self._streak_map = cand
            if self._streak >= self.confirm:
                log.info("Minimap matches %s (%d inliers); switching from %s", cand, cf.inliers, self.active)
                if loc:
                    loc.reset()
                self.active = cand
                self._last_ok = now
                self._streak_map, self._streak = None, 0
                self.searching = None
                return cand, cl, cf, True
        else:
            if cl:
                cl.reset()
            self._streak_map, self._streak = None, 0
            self._rr += 1
        return self.active, loc, None, False
