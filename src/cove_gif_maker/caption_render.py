"""Render a `Caption` to a transparent PNG.

The bundled static ffmpeg (johnvansickle release-essentials) is built without
the libfreetype + libfontconfig pair, so its `drawtext` filter is missing.
Instead of changing ffmpeg vendors (which doubles the AppImage size), we burn
the caption to a transparent PNG via Qt and composite it with ffmpeg's
universally-supported `overlay` filter.

QImage + QPainter are safe to use from worker threads (unlike QPixmap which
can require a windowing system), so this can run inside the converter
QThread without touching the main GUI loop.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetricsF, QImage, QPainter

from . import ffmpeg_utils as ff


# ----- Font resolution ---------------------------------------------------

def _best_bold_font() -> QFont:
    """Pick a bold sans-serif that's likely to exist on every Linux box.

    DejaVu Sans is bundled with most distros (and with the AppImage runtime
    layer); fall back to Qt's font matcher if not. Either way we get a
    consistent bold sans for caption rendering."""
    families = QFontDatabase.families()
    for candidate in ("DejaVu Sans", "Liberation Sans", "Noto Sans", "Inter", "Arial"):
        if candidate in families:
            f = QFont(candidate)
            f.setBold(True)
            return f
    f = QFont()
    f.setBold(True)
    return f


def _outline_width_for(font_px: int) -> int:
    # Scale the outline thickness with the font so it stays visible at any
    # caption size — 4% of font size, minimum 2px.
    return max(2, int(round(font_px * 0.04)))


# ----- Public API --------------------------------------------------------

def render_to_png(caption: ff.Caption, video_w: int, video_h: int, out_path: Path) -> None:
    """Single-caption convenience wrapper around `render_many_to_png`."""
    render_many_to_png([caption], video_w, video_h, out_path)


def render_many_to_png(captions: list[ff.Caption], video_w: int, video_h: int,
                       out_path: Path) -> None:
    """Render any number of captions onto a single transparent canvas.

    Captions are drawn in list order — later captions sit on top of earlier
    ones if they overlap. Empty/whitespace-only captions are skipped.
    The caller hands the resulting PNG to ffmpeg as a second input and
    composites with `overlay=0:0`."""
    img = QImage(max(1, video_w), max(1, video_h), QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)

    drew_any = False
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)

    for caption in captions:
        text = caption.text
        if not text or not text.strip():
            continue

        font_px = max(8, int(round(video_h * max(2, caption.size_pct) / 100.0)))
        f = _best_bold_font()
        f.setPixelSize(font_px)
        p.setFont(f)

        fm = QFontMetricsF(f)
        text_w = fm.horizontalAdvance(text)
        text_h = fm.height()

        cx = caption.pos_x * video_w
        cy = caption.pos_y * video_h
        rect = QRectF(-text_w / 2, -text_h / 2, text_w, text_h)

        # Save state so each caption's translate/rotate doesn't leak into
        # the next one's coordinate system.
        p.save()
        p.translate(cx, cy)
        if caption.rotation_deg:
            p.rotate(caption.rotation_deg)

        outline_w = _outline_width_for(font_px)
        outline = QColor(caption.outline)
        outline.setAlphaF(0.95)
        p.setPen(outline)
        for dx in (-outline_w, 0, outline_w):
            for dy in (-outline_w, 0, outline_w):
                if dx == 0 and dy == 0:
                    continue
                p.drawText(rect.adjusted(dx, dy, dx, dy), Qt.AlignCenter, text)

        p.setPen(QColor(caption.color))
        p.drawText(rect, Qt.AlignCenter, text)
        p.restore()
        drew_any = True

    p.end()
    # Even if no caption drew, still write the (transparent) PNG so the
    # overlay filter call site is a safe no-op.
    img.save(str(out_path), "PNG")
