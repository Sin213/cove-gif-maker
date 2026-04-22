from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget


HANDLE_W = 10
TRACK_PAD = HANDLE_W // 2


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
        p.setRenderHint(QPainter.Antialiasing, False)
        p.fillRect(self.rect(), QColor("#1f2024"))

        track = self._track_rect()
        p.fillRect(track, QColor("#101115"))

        if self._thumbs and self._duration > 0:
            n = len(self._thumbs)
            slice_w = track.width() / n
            for i, thumb in enumerate(self._thumbs):
                target = QRect(
                    int(track.left() + i * slice_w),
                    track.top(),
                    int(slice_w) + 1,
                    track.height(),
                )
                scaled = thumb.scaledToHeight(track.height(), Qt.SmoothTransformation)
                src_x = max(0, (scaled.width() - target.width()) // 2)
                p.drawPixmap(
                    target,
                    scaled,
                    QRect(src_x, 0, min(target.width(), scaled.width()), scaled.height()),
                )

        # dim outside selection
        if self._duration > 0:
            sx = self._time_to_x(self._start)
            ex = self._time_to_x(self._end)
            dim = QColor(0, 0, 0, 140)
            p.fillRect(QRect(track.left(), track.top(), sx - track.left(), track.height()), dim)
            p.fillRect(QRect(ex, track.top(), track.right() - ex + 1, track.height()), dim)

            # selection border
            sel_pen = QPen(QColor("#5fb4ff"))
            sel_pen.setWidth(2)
            p.setPen(sel_pen)
            p.drawRect(QRect(sx, track.top(), max(1, ex - sx), track.height() - 1))

            # handles
            self._draw_handle(p, sx, track, color="#5fb4ff", left=True)
            self._draw_handle(p, ex, track, color="#5fb4ff", left=False)

            # playhead
            ph = self._time_to_x(self._playhead)
            p.setPen(QPen(QColor("#ffffff"), 1))
            p.drawLine(ph, track.top(), ph, track.bottom())

            # time labels
            p.setPen(QColor("#cfd0d4"))
            f = p.font()
            f.setPointSize(8)
            p.setFont(f)
            label_y = track.bottom() + 14
            p.drawText(QPoint(sx, label_y), _fmt_time(self._start))
            end_text = _fmt_time(self._end)
            tw = p.fontMetrics().horizontalAdvance(end_text)
            p.drawText(QPoint(min(ex, self.rect().right() - tw - 2), label_y), end_text)

        p.end()

    def _draw_handle(self, p: QPainter, x: int, track: QRect, color: str, left: bool) -> None:
        rect = QRect(x - HANDLE_W // 2, track.top() - 2, HANDLE_W, track.height() + 4)
        p.fillRect(rect, QColor(color))
        p.setPen(QPen(QColor("#0d1216"), 1))
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
