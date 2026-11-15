"""Text recognition using the OCR engine built into Windows 10/11 (no extra install)."""
from __future__ import annotations

import asyncio

import cv2
import numpy as np


def brighten_colored_text(bgr: np.ndarray) -> np.ndarray:
    """Quest titles are drawn in red/blue/green. In plain grayscale dark red almost vanishes,
    so take each pixel's brightest channel and stretch the contrast instead."""
    m = bgr.max(axis=2).astype(np.float32)
    m = np.clip((m - 50.0) * (255.0 / 150.0), 0, 255).astype(np.uint8)
    return cv2.cvtColor(m, cv2.COLOR_GRAY2BGR)


def keep_thin_bright_strokes(bgr: np.ndarray) -> np.ndarray:
    """Top-hat filter: keeps text-sized bright strokes, flattens bright ground behind them."""
    m = bgr.max(axis=2)
    th = cv2.morphologyEx(m, cv2.MORPH_TOPHAT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    th = cv2.normalize(th, None, 0, 255, cv2.NORM_MINMAX)
    return cv2.cvtColor(th, cv2.COLOR_GRAY2BGR)


def _letters(lines: list[str]) -> int:
    return sum(ch.isalpha() for line in lines for ch in line)


class WindowsOcr:
    """Use from a single thread; each instance owns its own event loop."""

    def __init__(self, language: str = ""):
        from winrt.windows.media.ocr import OcrEngine

        self._loop = asyncio.new_event_loop()
        if language:
            from winrt.windows.globalization import Language

            self.engine = OcrEngine.try_create_from_language(Language(language))
        else:
            self.engine = OcrEngine.try_create_from_user_profile_languages()
        if self.engine is None:
            raise RuntimeError(
                f"Windows OCR has no engine for language '{language or 'display language'}'. "
                "Install the language's OCR pack under Settings > Time & language > Language."
            )
        self.max_dim = int(OcrEngine.max_image_dimension)

    def read_lines(self, bgr: np.ndarray, upscale: float = 1.0, preprocess: bool = True) -> list[str]:
        if upscale and upscale != 1.0:
            bgr = cv2.resize(bgr, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
        if not preprocess:
            return self._read(bgr)
        lines = self._read(brighten_colored_text(bgr))
        if _letters(lines) < 15:
            # Over bright ground (sunlit sand, rocks) Windows OCR sometimes misreads the whole panel.
            # Flattening the background usually rescues it; keep whichever read found more text.
            alt = self._read(keep_thin_bright_strokes(bgr))
            if _letters(alt) > _letters(lines):
                lines = alt
        return lines

    def read_lines_all_ways(self, bgr: np.ndarray, upscale: float = 1.0) -> list[list[str]]:
        """Read with every preprocessing (for a manual refresh): each catches things the others miss."""
        if upscale and upscale != 1.0:
            bgr = cv2.resize(bgr, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
        return [self._read(brighten_colored_text(bgr)), self._read(keep_thin_bright_strokes(bgr)),
                self._read(bgr)]

    def _read(self, img: np.ndarray) -> list[str]:
        h, w = img.shape[:2]
        if max(h, w) > self.max_dim:
            s = self.max_dim / max(h, w)
            img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        return self._loop.run_until_complete(self._recognize(img))

    async def _recognize(self, bgr: np.ndarray) -> list[str]:
        from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.storage.streams import DataWriter

        bgra = cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA)
        h, w = bgra.shape[:2]
        writer = DataWriter()
        writer.write_bytes(bgra.tobytes())
        bmp = SoftwareBitmap.create_copy_from_buffer(
            writer.detach_buffer(), BitmapPixelFormat.BGRA8, w, h, BitmapAlphaMode.PREMULTIPLIED
        )
        result = await self.engine.recognize_async(bmp)
        return [line.text for line in result.lines if line.text.strip()]

    def close(self) -> None:
        self._loop.close()
