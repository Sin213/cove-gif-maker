from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor, QFont, QImage, QMouseEvent, QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import QWidget

from . import theme


HANDLE_W = 10
TRACK_PAD = HANDLE_W // 2

# Cove-themed colors for the trim bar.
_BG          = QColor(theme.SURFACE)        # outer background of the widget
_TRACK_BG    = QColor(theme.BG)             # behind thumbnails (sits at panel bottom)
_DIM         = QColor(0, 0, 0, 140)         # mask outside selection
_SELECT      = QColor(theme.ACCENT)         # selection border + handles
_HANDLE_NOTCH = QColor(theme.ACCENT_ON)     # tiny notch in middle of handle
_PLAYHEAD    = QColor(theme.TEXT)
_LABEL       = QColor(theme.TEXT_DIM)


@dataclass
class _Drag:
    target: str  # "start", "end", "playhead", or ""
    grab_offset_px: int = 0


class TrimBar(QWidget):
    """Thumbnail strip with two draggable trim handles and a playhead."""

    rangeChanged = Signal(float, float)   # start, end (seconds)
    rangePreviewing = Signal(float, float)  # while dragging
    playheadMoved = Signal(float)         # seconds (user scrub)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(96)
        self.setMouseTracking(True)
        self._duration: float = 0.0
        self._start: float = 0.0
        self._end: float = 0.0
        self._playhead: float = 0.0
        self._thumbs: list[QPixmap] = []
        self._drag = _Drag(target="")

    # --- public API -----------------------------------------------------

    def set_duration(self, seconds: float) -> None:
        self._duration = max(0.0, seconds)
        self._start = 0.0
        self._end = self._duration
        self._playhead = 0.0
        self.update()

    def set_thumbnails(self, images: list[QImage]) -> None:
        self._thumbs = [QPixmap.fromImage(img) for img in images]
        self.update()

    def clear(self) -> None:
        self._thumbs.clear()
        self._duration = 0.0
        self._start = 0.0
        self._end = 0.0
        self._playhead = 0.0
        self.update()

    def set_playhead(self, seconds: float) -> None:
        self._playhead = max(self._start, min(self._end, seconds))
        self.update()

    def start(self) -> float:
        return self._start

    def end(self) -> float:
        return self._end

    def duration(self) -> float:
        return self._duration

    # --- geometry helpers ----------------------------------------------

    def _track_rect(self) -> QRect:
        return self.rect().adjusted(TRACK_PAD, 4, -TRACK_PAD, -20)

    def _time_to_x(self, t: float) -> int:
        if self._duration <= 0:
            return self._track_rect().left()
        track = self._track_rect()
        ratio = t / self._duration
        return int(track.left() + ratio * track.width())

    def _x_to_time(self, x: int) -> float:
        if self._duration <= 0:
            return 0.0
        track = self._track_rect()
        ratio = (x - track.left()) / max(1, track.width())
        return max(0.0, min(self._duration, ratio * self._duration))

    # --- painting -------------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: ANN001
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), _BG)

        track = self._track_rect()
        # Round the thumbnail strip corners so it reads as a contained
        # element instead of a hard rectangle bleeding into the panel.
        radius = 6
        track_path = QPainterPath()
        track_path.addRoundedRect(QRectF(track), radius, radius)
        p.setClipPath(track_path)
        p.fillRect(track, _TRACK_BG)

        if self._thumbs and self._duration > 0:
            n = len(self._thumbs)
            slice_w = track.width() / n
            for i, thumb in enumerate(self._thumbs):
                # Use floats then round to avoid 1-px gaps between tiles.
                left = track.left() + i * slice_w
                right = track.left() + (i + 1) * slice_w
                target = QRect(
                    int(round(left)), track.top(),
                    int(round(right)) - int(round(left)), track.height(),
                )
                if target.width() <= 0:
                    continue
                # Scale by EXPANDING the smaller dimension to fill, then
                # center-crop. With fewer slices this keeps more of each
                # frame visible than the previous height-only scale.
                scaled = thumb.scaled(
                    target.size(), Qt.KeepAspectRatioByExpanding,
                    Qt.SmoothTransformation,
                )
                src_x = max(0, (scaled.width() - target.width()) // 2)
                src_y = max(0, (scaled.height() - target.height()) // 2)
                p.drawPixmap(
                    target, scaled,
                    QRect(src_x, src_y, target.width(), target.height()),
                )

            # Subtle vertical separators between thumbnails so the strip
            # reads as discrete frames rather than one mushy ribbon.
            sep = QPen(QColor(0, 0, 0, 70))
            sep.setWidth(1)
            p.setPen(sep)
            for i in range(1, n):
                x = int(round(track.left() + i * slice_w))
                p.drawLine(x, track.top(), x, track.bottom())

        p.setClipping(False)

        # dim outside selection
        if self._duration > 0:
            sx = self._time_to_x(self._start)
            ex = self._time_to_x(self._end)
            p.setClipPath(track_path)
            p.fillRect(QRect(track.left(), track.top(), sx - track.left(), track.height()), _DIM)
            p.fillRect(QRect(ex, track.top(), track.right() - ex + 1, track.height()), _DIM)
            p.setClipping(False)

            # selection border (rounded to match the strip)
            sel_pen = QPen(_SELECT)
            sel_pen.setWidth(2)
            p.setPen(sel_pen)
            p.setBrush(Qt.NoBrush)
            sel_rect = QRectF(sx, track.top(), max(1, ex - sx), track.height() - 1)
            p.drawRoundedRect(sel_rect, 3, 3)

            # handles
            self._draw_handle(p, sx, track)
            self._draw_handle(p, ex, track)

            # playhead — soft glow trail behind a crisp white line
            ph = self._time_to_x(self._playhead)
            glow_pen = QPen(QColor(80, 230, 207, 110))
            glow_pen.setWidth(3)
            p.setPen(glow_pen)
            p.drawLine(ph, track.top(), ph, track.bottom())
            p.setPen(QPen(_PLAYHEAD, 1))
            p.drawLine(ph, track.top(), ph, track.bottom())

            # time labels — Geist Mono so timecodes line up
            p.setPen(_LABEL)
            f = QFont(theme.FONT_MONO)
            f.setPointSize(8)
            p.setFont(f)
            label_y = track.bottom() + 14
            p.drawText(QPoint(sx, label_y), _fmt_time(self._start))
            end_text = _fmt_time(self._end)
            tw = p.fontMetrics().horizontalAdvance(end_text)
            p.drawText(QPoint(min(ex, self.rect().right() - tw - 2), label_y), end_text)

        p.end()

    def _draw_handle(self, p: QPainter, x: int, track: QRect) -> None:
        rect = QRect(x - HANDLE_W // 2, track.top() - 2, HANDLE_W, track.height() + 4)
        p.fillRect(rect, _SELECT)
        p.setPen(QPen(_HANDLE_NOTCH, 1))
        notch_x = rect.center().x()
        p.drawLine(notch_x, rect.top() + 4, notch_x, rect.bottom() - 4)

    # --- mouse handling ------------------------------------------------

    def _hit_test(self, pos: QPoint) -> str:
        if self._duration <= 0:
            return ""
        sx = self._time_to_x(self._start)
        ex = self._time_to_x(self._end)
        if abs(pos.x() - sx) <= HANDLE_W:
            return "start"
        if abs(pos.x() - ex) <= HANDLE_W:
            return "end"
        if sx < pos.x() < ex:
            return "playhead"
        return ""

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.LeftButton or self._duration <= 0:
            return
        target = self._hit_test(event.position().toPoint())
        if not target:
            # clicking on dim area => move closest handle
            x = event.position().x()
            sx = self._time_to_x(self._start)
            ex = self._time_to_x(self._end)
            target = "start" if abs(x - sx) <= abs(x - ex) else "end"
        self._drag = _Drag(target=target)
        self._apply_drag(event.position().x())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag.target:
            self._apply_drag(event.position().x())
        else:
            cursor_target = self._hit_test(event.position().toPoint())
            if cursor_target in ("start", "end"):
                self.setCursor(Qt.SplitHCursor)
            elif cursor_target == "playhead":
                self.setCursor(Qt.SizeHorCursor)
            else:
                self.unsetCursor()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag.target in ("start", "end"):
            self.rangeChanged.emit(self._start, self._end)
        self._drag = _Drag(target="")

    def _apply_drag(self, x: float) -> None:
        t = self._x_to_time(int(x))
        min_gap = max(0.05, self._duration * 0.005)
        if self._drag.target == "start":
            self._start = min(t, self._end - min_gap)
            self._start = max(0.0, self._start)
            self._playhead = max(self._playhead, self._start)
            self.rangePreviewing.emit(self._start, self._end)
        elif self._drag.target == "end":
            self._end = max(t, self._start + min_gap)
            self._end = min(self._duration, self._end)
            self._playhead = min(self._playhead, self._end)
            self.rangePreviewing.emit(self._start, self._end)
        elif self._drag.target == "playhead":
            self._playhead = max(self._start, min(self._end, t))
            self.playheadMoved.emit(self._playhead)
        self.update()

    def sizeHint(self) -> QSize:  # noqa: D401
        return QSize(640, 110)


def _fmt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    m = int(seconds // 60)
    s = seconds - m * 60
    return f"{m:02d}:{s:05.2f}"
