"""Key-color detection for the transparency effect.

MP4 can't carry an alpha channel, so an animated emote downloaded from
Discord or Tenor arrives with its transparent background already flattened
onto whatever flat color the site composited it over. Recovering the
transparency means color-keying that backdrop back out — which first
requires knowing what it is.

Guessing it is much nicer than making the user eyedropper it, and a flat
backdrop is easy to spot: it occupies the entire border of the frame. This
module samples that border ring out of a decoded frame and reports the
dominant color plus how uniform the ring is, so the caller can decide
whether the guess is trustworthy.
"""

from __future__ import annotations

import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from . import ffmpeg_utils as ff


# How thick a border ring to sample, as a fraction of the shorter side.
# 4 % is wide enough to average out h264 ringing at the frame edge without
# reaching into the subject on a tightly-cropped emote.
_RING_FRACTION = 0.04
_RING_MIN_PX = 2

# Two colors count as "the same backdrop" within this per-channel distance.
# Lossy encoding jitters a flat fill by a few levels; 12 covers that without
# merging genuinely different colors.
_MATCH_TOLERANCE = 12

# Below this share of matching border pixels the frame doesn't have a flat
# backdrop at all (a photo, a full-bleed scene), and we report no guess.
_MIN_CONFIDENCE = 0.55


@dataclass
class KeyGuess:
    """A detected backdrop color. `confidence` is the fraction of sampled
    border pixels within `_MATCH_TOLERANCE` of it — 1.0 means a perfectly
    flat border, ~0.6 means a mostly-flat border with the subject
    bleeding into the edge."""
    color: str          # "#RRGGBB"
    confidence: float   # 0..1


def detect_key_color_in_image(image: QImage) -> KeyGuess | None:
    """Find the dominant border color of an already-decoded frame."""
    if image.isNull():
        return None
    img = image.convertToFormat(QImage.Format_RGB32)
    w, h = img.width(), img.height()
    if w < 8 or h < 8:
        return None

    ring = max(_RING_MIN_PX, int(min(w, h) * _RING_FRACTION))
    counts: Counter[tuple[int, int, int]] = Counter()
    for y in range(h):
        in_horizontal_band = y < ring or y >= h - ring
        # Full rows top and bottom; only the left/right columns in between.
        xs = range(w) if in_horizontal_band else _edge_columns(w, ring)
        for x in xs:
            px = img.pixel(x, y)
            counts[((px >> 16) & 0xFF, (px >> 8) & 0xFF, px & 0xFF)] += 1

    if not counts:
        return None
    total = sum(counts.values())
    dominant, _ = counts.most_common(1)[0]
    # Confidence counts every near-match, not just exact hits — a flat fill
    # that h264 smeared into a dozen adjacent values is still a flat fill.
    matching = sum(
        n for color, n in counts.items() if _within_tolerance(color, dominant)
    )
    confidence = matching / total
    if confidence < _MIN_CONFIDENCE:
        return None
    r, g, b = dominant
    return KeyGuess(color=f"#{r:02X}{g:02X}{b:02X}", confidence=confidence)


def _edge_columns(width: int, ring: int) -> list[int]:
    """Left and right edge columns, deduplicated — on a frame narrower than
    two rings the two bands overlap and would otherwise be counted twice."""
    left = set(range(min(ring, width)))
    right = set(range(max(0, width - ring), width))
    return sorted(left | right)


def _within_tolerance(a: tuple[int, int, int], b: tuple[int, int, int]) -> bool:
    return all(abs(x - y) <= _MATCH_TOLERANCE for x, y in zip(a, b))


def detect_key_color(video: Path, at_time: float = 0.0) -> KeyGuess | None:
    """Decode one frame of `video` and detect its backdrop color."""
    with tempfile.TemporaryDirectory(prefix="cove-key-") as tmp:
        frame = Path(tmp) / "frame.png"
        ff.extract_frame_png(video, at_time, frame)
        if not frame.exists():
            return None
        return detect_key_color_in_image(QImage(str(frame)))


def sample_color_at(video: Path, at_time: float, x: int, y: int) -> str | None:
    """Read the color at source pixel (x, y) of the frame at `at_time`.

    Averaged over a 3x3 neighborhood so a single compression-noisy pixel
    doesn't decide the key color for the whole export."""
    with tempfile.TemporaryDirectory(prefix="cove-pick-") as tmp:
        frame = Path(tmp) / "frame.png"
        ff.extract_frame_png(video, at_time, frame)
        if not frame.exists():
            return None
        img = QImage(str(frame))
        if img.isNull():
            return None
        img = img.convertToFormat(QImage.Format_RGB32)
        w, h = img.width(), img.height()
        if not (0 <= x < w and 0 <= y < h):
            return None
        rs = gs = bs = n = 0
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                px_x, px_y = x + dx, y + dy
                if not (0 <= px_x < w and 0 <= px_y < h):
                    continue
                px = img.pixel(px_x, px_y)
                rs += (px >> 16) & 0xFF
                gs += (px >> 8) & 0xFF
                bs += px & 0xFF
                n += 1
        if not n:
            return None
        return f"#{rs // n:02X}{gs // n:02X}{bs // n:02X}"


class KeyDetectWorker(QThread):
    """Run `detect_key_color` off the UI thread.

    Detection shells out to ffmpeg and then walks a few thousand pixels in
    Python, which is fast but not instant — and it fires on every video
    load, so it must not stall the window."""

    detected = Signal(object, object)   # (path: Path, guess: KeyGuess | None)

    def __init__(self, path: Path, at_time: float = 0.0, parent=None) -> None:
        super().__init__(parent)
        self._path = path
        self._at_time = at_time

    def run(self) -> None:
        try:
            guess = detect_key_color(self._path, self._at_time)
        except Exception:  # noqa: BLE001
            # A failed guess is not an error worth interrupting the user
            # for — the key color stays at its default and they can pick
            # one by hand.
            guess = None
        self.detected.emit(self._path, guess)
