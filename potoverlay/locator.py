"""Find where the minimap sits on the full map.

The full map is turned into SIFT features once (and cached). Each frame, features
from the minimap are matched against it and a similarity transform
(position + rotation + uniform scale) is fitted with RANSAC. That handles a
minimap that zooms or rotates with the camera.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)

CONTRAST_THRESHOLD = 0.015
QUERY_FEATURES = 500


def imread_any(path: Path | str) -> np.ndarray | None:
    """cv2.imread that also works with non-ASCII paths."""
    data = np.fromfile(str(path), dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def build_minimap_mask(w: int, h: int, shape: str, center_mask: float, edge_mask: float) -> np.ndarray:
    """255 where features may be used, 0 for the border and the player arrow."""
    mask = np.zeros((h, w), np.uint8)
    cx, cy = w / 2.0, h / 2.0
    if shape == "circle":
        r = min(w, h) / 2.0 * (1.0 - edge_mask)
        cv2.circle(mask, (int(round(cx)), int(round(cy))), int(r), 255, -1)
    else:
        mx, my = int(w * edge_mask / 2), int(h * edge_mask / 2)
        mask[my:h - my, mx:w - mx] = 255
    if center_mask > 0:
        rc = min(w, h) / 2.0 * center_mask
        cv2.circle(mask, (int(round(cx)), int(round(cy))), max(1, int(rc)), 0, -1)
    return mask


@dataclass
class Fix:
    """A minimap -> work-map transform. M is 2x3: [x', y'] = M @ [x, y, 1]."""
    M: np.ndarray
    inliers: int
    matches: int
    src: np.ndarray | None = None  # the inlier points in the source image (for sanity checks)

    @property
    def scale(self) -> float:
        return float(math.hypot(self.M[0, 0], self.M[1, 0]))

    @property
    def angle(self) -> float:
        return float(math.atan2(self.M[1, 0], self.M[0, 0]))

    def apply(self, pts: np.ndarray) -> np.ndarray:
        pts = np.asarray(pts, np.float64).reshape(-1, 2)
        return pts @ self.M[:, :2].T + self.M[:, 2]

    def apply_inverse(self, pts: np.ndarray) -> np.ndarray:
        inv = cv2.invertAffineTransform(self.M)
        pts = np.asarray(pts, np.float64).reshape(-1, 2)
        return pts @ inv[:, :2].T + inv[:, 2]


def fix_from_params(tx: float, ty: float, scale: float, angle: float) -> np.ndarray:
    c, s = math.cos(angle) * scale, math.sin(angle) * scale
    return np.array([[c, -s, tx], [s, c, ty]], np.float64)


class MapLocator:
    def __init__(
        self,
        map_path: Path | str,
        work_max_dim: int = 4096,
        cache_dir: Path | None = None,
        min_inliers: int = 10,
        ratio: float = 0.8,
    ):
        self.map_path = Path(map_path)
        full = imread_any(self.map_path)
        if full is None:
            raise FileNotFoundError(f"Could not read map image: {self.map_path}")
        fh, fw = full.shape[:2]
        self.full_size = (fw, fh)
        self.work_scale = min(1.0, work_max_dim / max(fw, fh))
        if self.work_scale < 1.0:
            work = cv2.resize(full, None, fx=self.work_scale, fy=self.work_scale, interpolation=cv2.INTER_AREA)
        else:
            work = full
        self.work_size = (work.shape[1], work.shape[0])
        # Kept (small, grey) to check where the in-game map window ends.
        self.work_gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
        self.min_inliers = min_inliers
        self.ratio = ratio
        # Lower contrast threshold than the default (0.04): terrain textures are soft.
        self.sift = cv2.SIFT_create(contrastThreshold=CONTRAST_THRESHOLD)
        # For the minimap only the strongest features are needed; more just costs time.
        self.sift_query = cv2.SIFT_create(nfeatures=QUERY_FEATURES, contrastThreshold=CONTRAST_THRESHOLD)
        self.kp_xy, self.desc = self._features(work, cache_dir)
        log.info("Map %s: %dx%d (work %dx%d), %d features",
                 self.map_path.name, fw, fh, *self.work_size, len(self.kp_xy))

        self.flann = cv2.FlannBasedMatcher(dict(algorithm=1, trees=5), dict(checks=64))
        self.flann.add([self.desc])
        self.flann.train()
        self.bf = cv2.BFMatcher(cv2.NORM_L2)
        self.last: Fix | None = None
        self.scale_hint: float | None = None  # learned minimap->work-map scale
        self._alt_i = 0
        self._sift_view = None
        self._sift_view_n = 0

    # ---------- setup ----------
    def _features(self, work: np.ndarray, cache_dir: Path | None):
        cache = None
        if cache_dir is not None:
            st = self.map_path.stat()
            key = (f"{self.map_path.stem}_{work.shape[1]}x{work.shape[0]}_{st.st_size}_{int(st.st_mtime)}"
                   f"_c{CONTRAST_THRESHOLD}.npz")
            cache = Path(cache_dir) / key
            if cache.exists():
                try:
                    data = np.load(cache)
                    return data["xy"], data["desc"]
                except Exception:  # corrupt cache, rebuild
                    log.warning("Ignoring bad feature cache %s", cache)
        log.info("Computing map features for %s (first run only, may take a bit)...", self.map_path.name)
        gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
        kps, desc = self.sift.detectAndCompute(gray, None)
        if desc is None or len(kps) < 50:
            raise ValueError(f"Map image {self.map_path.name} has too little detail to match against")
        xy = np.array([k.pt for k in kps], np.float32)
        desc = desc.astype(np.float32)
        if cache is not None:
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                np.savez(cache, xy=xy, desc=desc)
            except OSError as e:
                log.warning("Could not write feature cache: %s", e)
        return xy, desc

    # ---------- coordinate helpers ----------
    def full_to_work(self, pts) -> np.ndarray:
        return np.asarray(pts, np.float64).reshape(-1, 2) * self.work_scale

    def work_to_full(self, pts) -> np.ndarray:
        return np.asarray(pts, np.float64).reshape(-1, 2) / self.work_scale

    def full_to_minimap(self, fix: Fix, pts) -> np.ndarray:
        return fix.apply_inverse(self.full_to_work(pts))

    def reset(self) -> None:
        self.last = None

    # ---------- matching ----------
    def locate(self, minimap_bgr: np.ndarray, mask: np.ndarray | None = None, upscale: float = 1.0) -> Fix | None:
        """Find the minimap on the map. `upscale` is only a first guess: once a match is found, the
        minimap is resampled so its pixels line up 1:1 with the work map, which matches best."""
        if self.scale_hint is not None:
            tries = [self.scale_hint]
            if self.last is None:  # lost: also try one other zoom level per frame, cycling through them
                alts = [s for s in dict.fromkeys((upscale or 1.0, 1.0, 0.5, 2.0, 3.0))
                        if abs(s - self.scale_hint) > 0.2]
                if alts:
                    tries.append(alts[self._alt_i % len(alts)])
                    self._alt_i += 1
        else:
            # Zoom not known yet: one guess per frame (the configured one first), cycling.
            guesses = list(dict.fromkeys([upscale or 1.0, 1.0, 0.5, 2.0, 3.0]))
            tries = [guesses[self._alt_i % len(guesses)]]
            self._alt_i += 1
        for s in tries:
            fix = self._locate_at(minimap_bgr, mask, s)
            if fix is not None:
                self.scale_hint = float(np.clip(fix.scale, 0.4, 4.0))
                return fix
        return None

    def _locate_at(self, minimap_bgr: np.ndarray, mask: np.ndarray | None, upscale: float) -> Fix | None:
        img, m = minimap_bgr, mask
        if upscale and upscale != 1.0:
            img = cv2.resize(img, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
            if m is not None:
                m = cv2.resize(m, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_NEAREST)
        else:
            upscale = 1.0
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        kps, desc = self.sift_query.detectAndCompute(gray, m)
        if desc is None or len(kps) < self.min_inliers:
            return None
        q_xy = np.array([k.pt for k in kps], np.float32) / upscale
        desc = desc.astype(np.float32)
        h, w = minimap_bgr.shape[:2]

        fix = None
        if self.last is not None:
            fix = self._locate_local(q_xy, desc, w, h)
        if fix is None:
            fix = self._locate_global(q_xy, desc)
        if fix is not None:
            self.last = fix
        return fix

    def locate_view(self, img_bgr: np.ndarray, max_features: int = 1500, min_inliers: int = 30) -> Fix | None:
        """Find a large view of the map (the in-game map screen) in an image. Stateless: the map screen
        can be panned and zoomed freely, so every call searches the whole map.
        The returned Fix maps image pixels -> work-map pixels."""
        if self._sift_view is None or self._sift_view_n != max_features:
            self._sift_view = cv2.SIFT_create(nfeatures=max_features, contrastThreshold=CONTRAST_THRESHOLD)
            self._sift_view_n = max_features
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        kps, desc = self._sift_view.detectAndCompute(gray, None)
        if desc is None or len(kps) < min_inliers:
            return None
        q_xy = np.array([k.pt for k in kps], np.float32)
        knn = self.flann.knnMatch(desc.astype(np.float32), k=2)
        src, dst = self._ratio_filter(knn, q_xy, self.kp_xy)
        if len(src) < min_inliers:
            return None
        M, inl = cv2.estimateAffinePartial2D(
            src, dst, method=cv2.RANSAC, ransacReprojThreshold=4.0, maxIters=3000, confidence=0.995
        )
        if M is None or inl is None or int(inl.sum()) < min_inliers:
            return None
        return Fix(M.astype(np.float64), int(inl.sum()), len(src), src[inl.ravel().astype(bool)])

    def _locate_local(self, q_xy, desc, w, h) -> Fix | None:
        """Match only against map features near the last known position."""
        last = self.last
        center = last.apply([[w / 2.0, h / 2.0]])[0]
        # The minimap's own footprint plus room for a few frames of movement.
        radius = 0.75 * max(w, h) * last.scale + 48
        d2 = np.sum((self.kp_xy - center.astype(np.float32)) ** 2, axis=1)
        idx = np.nonzero(d2 < radius * radius)[0]
        if len(idx) < self.min_inliers * 2:
            return None
        knn = self.bf.knnMatch(desc, self.desc[idx], k=2)
        src, dst = self._ratio_filter(knn, q_xy, self.kp_xy[idx])
        fix = self._fit(src, dst)
        if fix is not None and not (0.6 < fix.scale / last.scale < 1.6):
            return None
        return fix

    def _locate_global(self, q_xy, desc) -> Fix | None:
        knn = self.flann.knnMatch(desc, k=2)
        src, dst = self._ratio_filter(knn, q_xy, self.kp_xy)
        return self._fit(src, dst)

    def _ratio_filter(self, knn, q_xy, t_xy):
        src, dst = [], []
        for pair in knn:
            if len(pair) < 2:
                continue
            a, b = pair
            if a.distance < self.ratio * b.distance:
                src.append(q_xy[a.queryIdx])
                dst.append(t_xy[a.trainIdx])
        return np.float32(src).reshape(-1, 2), np.float32(dst).reshape(-1, 2)

    def _fit(self, src, dst) -> Fix | None:
        if len(src) < self.min_inliers:
            return None
        M, inl = cv2.estimateAffinePartial2D(
            src, dst, method=cv2.RANSAC, ransacReprojThreshold=4.0, maxIters=3000, confidence=0.995
        )
        if M is None or inl is None:
            return None
        n = int(inl.sum())
        if n < self.min_inliers:
            return None
        fix = Fix(M.astype(np.float64), n, len(src), src[inl.ravel().astype(bool)])
        if not (1e-3 < fix.scale < 1e3):
            return None
        return fix
