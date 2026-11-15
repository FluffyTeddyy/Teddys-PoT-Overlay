"""Screen capture with mss. Create one ScreenGrabber per thread (mss isn't thread-safe)."""
from __future__ import annotations

import mss
import numpy as np


def list_monitors() -> list[dict]:
    with mss.mss() as sct:
        return [dict(m) for m in sct.monitors]


class ScreenGrabber:
    def __init__(self, monitor_index: int = 1):
        self.sct = mss.mss()
        mons = self.sct.monitors  # [0] = all monitors combined, [1..] = each monitor
        idx = monitor_index if 1 <= monitor_index < len(mons) else 1
        self.monitor = dict(mons[idx])

    def grab(self, region: dict | None = None) -> np.ndarray:
        """Return a BGR image of the whole monitor or of a region relative to it."""
        m = self.monitor
        if region is None:
            box = {"left": m["left"], "top": m["top"], "width": m["width"], "height": m["height"]}
        else:
            box = {
                "left": m["left"] + int(region["x"]),
                "top": m["top"] + int(region["y"]),
                "width": int(region["w"]),
                "height": int(region["h"]),
            }
        shot = self.sct.grab(box)
        return np.ascontiguousarray(np.asarray(shot)[:, :, :3])

    def close(self) -> None:
        self.sct.close()
