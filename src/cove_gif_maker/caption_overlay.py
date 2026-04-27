"""Draggable caption overlay shown on top of the video preview.

Mirrors the role of `crop_overlay.py` — it sits at the same z-level as the
video, accounts for letterboxing, and emits normalized (0..1 of source) center
coordinates so the converter can plug those straight into ffmpeg's drawtext.

The overlay only paints itself when text is non-empty; it stays a transparent
no-op when the user hasn't added a caption yet.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QMouseEvent, QPainter, QPen,
    QPolygon, QPolygonF, QRegion, QTransform,
)
from PySide6.QtWidgets import QWidget

from . import theme


@dataclass
class CaptionStyle:
    text: str = ""
    color: str = "#ffffff"
    outline: str = "#000000"
    size_pct: float = 6.0          # font size as % of video display height
    rotation_deg: float = 0.0      # clockwise rotation around the text center


# Resize handle visual size + click-target padding.
_HANDLE_SIZE = 8
_HANDLE_HIT = 14
# How far the rotate handle floats above the top edge of the text rect.
_ROTATE_OFFSET = 24
_ROTATE_HANDLE_RADIUS = 6


class CaptionOverlay(QWidget):
    """Renders the caption text on top of the video and handles drag.

    Click+drag the text to MOVE it.
    Click+drag a corner handle to RESIZE it.
    Position is stored as normalized (0..1) coordinates of the CENTER in
    source-video space. Size is `style.size_pct` (font size as a percent of
    the source video height) — the same value that gets passed to the
    PNG renderer, so on-preview matches the burnt-in result.
    """

    positionChanged = Signal(float, float)   # cx, cy normalized
    sizeChanged     = Signal(float)          # new size_pct
    rotationChanged = Signal(float)          # degrees, clockwise

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self._video_aspect: float = 16 / 9
        self._style = CaptionStyle()
        self._cx: float = 0.5     # normalized center
        self._cy: float = 0.92
        self._mode: str = ""      # "" | "move" | "resize" | "rotate"
        self._drag_offset = QPointF(0, 0)
        self._resize_anchor: QPointF | None = None
        self._resize_start_size: float = self._style.size_pct
        self._resize_start_dist: float = 0.0
        self._rotate_start_angle: float = 0.0   # degrees the cursor was at
        self._rotate_start_rotation: float = 0.0  # degrees the text was at
        # Start fully masked-out — without a mask the empty overlay would
        # eat clicks meant for the video below.
        self._refresh_mask()

    def rotation_deg(self) -> float:
        return self._style.rotation_deg

    # --- public API ---------------------------------------------------

    def set_video_aspect(self, aspect: float) -> None:
        if aspect > 0:
            self._video_aspect = aspect
            self._refresh_mask()
            self.update()

    def set_style(self, style: CaptionStyle) -> None:
        self._style = style
        self._refresh_mask()
        self.update()

    def set_normalized_position(self, cx: float, cy: float) -> None:
        self._cx = max(0.0, min(1.0, cx))
        self._cy = max(0.0, min(1.0, cy))
        self._refresh_mask()
        self.update()

    def normalized_position(self) -> tuple[float, float]:
        return (self._cx, self._cy)

    def size_pct(self) -> float:
        return self._style.size_pct

    # --- mouse passthrough -------------------------------------------

    def _refresh_mask(self) -> None:
        """Restrict mouse-clickable area to the rotated text bbox plus the
        rotation handle's bubble. Anything outside passes clicks through
        to the video below so play/pause still works."""
        if not self._style.text.strip():
            self.setMask(QRegion())
            return
        poly = self._text_polygon()
        if poly.isEmpty():
            self.setMask(QRegion())
            return
        # Bounding rect of the rotated text + a margin big enough to cover
        # the corner resize hit zones.
        pad = _HANDLE_HIT + 2
        bbox = poly.boundingRect().adjusted(-pad, -pad, pad, pad).toRect()
        region = QRegion(bbox)
        # Extend the mask up to include the rotate handle bubble.
        rh = self._rotate_handle_pos()
        if not rh.isNull():
            r = _ROTATE_HANDLE_RADIUS + _HANDLE_HIT
            region = region.united(QRegion(
                int(rh.x() - r), int(rh.y() - r), int(2 * r), int(2 * r),
                QRegion.Ellipse,
            ))
        self.setMask(region)

    def resizeEvent(self, _event) -> None:  # noqa: ANN001
        self._refresh_mask()

    # --- handle helpers ----------------------------------------------

    def _handle_centers(self) -> dict[str, QPointF]:
        """The four corner-resize handles, mapped through the rotation."""
        c = self._text_rect()
        if c.isEmpty():
            return {}
        corners = {
            "tl": QPointF(c.left(), c.top()),
            "tr": QPointF(c.right(), c.top()),
            "bl": QPointF(c.left(), c.bottom()),
            "br": QPointF(c.right(), c.bottom()),
        }
        t = self._rotation_transform()
        return {k: t.map(v) for k, v in corners.items()}

    def _hit_handle(self, pos: QPointF) -> str | None:
        for name, center in self._handle_centers().items():
            if (abs(pos.x() - center.x()) <= _HANDLE_HIT
                    and abs(pos.y() - center.y()) <= _HANDLE_HIT):
                return name
        return None

    def _hit_rotate_handle(self, pos: QPointF) -> bool:
        rh = self._rotate_handle_pos()
        if rh.isNull():
            return False
        r = _ROTATE_HANDLE_RADIUS + _HANDLE_HIT
        dx = pos.x() - rh.x()
        dy = pos.y() - rh.y()
        return dx * dx + dy * dy <= r * r

    def _angle_to(self, pos: QPointF) -> float:
        """Angle in degrees from the text center to `pos`, clockwise from
        the +x axis. Used to compute the rotation delta during drag."""
        c = self._text_center()
        return math.degrees(math.atan2(pos.y() - c.y(), pos.x() - c.x()))

    # --- geometry helpers --------------------------------------------

    def _video_display_rect(self) -> QRectF:
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return QRectF(0, 0, 0, 0)
        widget_aspect = w / h
        if widget_aspect > self._video_aspect:
            actual_h = float(h)
            actual_w = h * self._video_aspect
            x = (w - actual_w) / 2
            y = 0.0
        else:
            actual_w = float(w)
            actual_h = w / self._video_aspect
            x = 0.0
            y = (h - actual_h) / 2
        return QRectF(x, y, actual_w, actual_h)

    def _font_for(self, video_h: float) -> QFont:
        f = QFont()
        f.setBold(True)
        # Keep the on-screen size visually consistent with what ffmpeg will
        # render — both compute font size as a % of output height.
        size_px = max(8, int(round(video_h * max(2, self._style.size_pct) / 100.0)))
        f.setPixelSize(size_px)
        return f

    def _text_rect(self) -> QRectF:
        """Axis-aligned text rect (pre-rotation), in widget coords.

        For hit-testing the rotated text we also expose `_text_polygon()`
        which applies the current rotation to this rect's corners."""
        v = self._video_display_rect()
        if v.isEmpty() or not self._style.text.strip():
            return QRectF()
        f = self._font_for(v.height())
        fm = QFontMetrics(f)
        text_w = fm.horizontalAdvance(self._style.text)
        text_h = fm.height()
        cx = v.x() + self._cx * v.width()
        cy = v.y() + self._cy * v.height()
        # When rotation is 0 we keep the old "stay inside the video rect"
        # clamp. When rotated we skip it because the bounding shape changes
        # mid-rotation and clamping mid-rotation feels jittery.
        if not self._style.rotation_deg:
            half_w = text_w / 2
            half_h = text_h / 2
            cx = max(v.left() + half_w, min(v.right() - half_w, cx))
            cy = max(v.top() + half_h, min(v.bottom() - half_h, cy))
        return QRectF(cx - text_w / 2, cy - text_h / 2, text_w, text_h)

    def _text_center(self) -> QPointF:
        v = self._video_display_rect()
        return QPointF(v.x() + self._cx * v.width(), v.y() + self._cy * v.height())

    def _rotation_transform(self) -> QTransform:
        """QTransform that rotates around the current text center."""
        c = self._text_center()
        t = QTransform()
        t.translate(c.x(), c.y())
        t.rotate(self._style.rotation_deg)
        t.translate(-c.x(), -c.y())
        return t

    def _text_polygon(self) -> QPolygonF:
        """The four corners of the (possibly rotated) text rect."""
        rect = self._text_rect()
        if rect.isEmpty():
            return QPolygonF()
        poly = QPolygonF([
            rect.topLeft(), rect.topRight(),
            rect.bottomRight(), rect.bottomLeft(),
        ])
        return self._rotation_transform().map(poly)

    def _rotate_handle_pos(self) -> QPointF:
        """World-coord position of the rotation handle (rotated above the
        text top edge)."""
        rect = self._text_rect()
        if rect.isEmpty():
            return QPointF()
        # Pre-rotation: directly above text center.
        pre = QPointF(rect.center().x(), rect.top() - _ROTATE_OFFSET)
        return self._rotation_transform().map(pre)

    # --- painting -----------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: ANN001
        if not self._style.text.strip():
            return
        rect = self._text_rect()
        if rect.isEmpty():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        v = self._video_display_rect()
        f = self._font_for(v.height())
        p.setFont(f)

        # Draw the text inside a saved-rotated coordinate system so the
        # outline + fill stay coherent. The chrome (dashed border, handles,
        # rotate bubble) gets drawn in screen coords via the polygon helpers
        # so the math stays simple.
        center = self._text_center()
        local_rect = QRectF(-rect.width() / 2, -rect.height() / 2,
                             rect.width(), rect.height())
        p.save()
        p.translate(center)
        if self._style.rotation_deg:
            p.rotate(self._style.rotation_deg)
        outline = QColor(self._style.outline)
        outline.setAlphaF(0.95)
        p.setPen(outline)
        for dx, dy in ((-2,-2),(0,-2),(2,-2),(-2,0),(2,0),(-2,2),(0,2),(2,2)):
            p.drawText(local_rect.adjusted(dx, dy, dx, dy),
                       Qt.AlignCenter, self._style.text)
        p.setPen(QColor(self._style.color))
        p.drawText(local_rect, Qt.AlignCenter, self._style.text)
        p.restore()

        # Hover/drag chrome — dashed border around the rotated polygon, a
        # corner handle at each corner, and a rotation bubble above with a
        # connector line. Always shown when active so all three affordances
        # (move / resize / rotate) are discoverable.
        active = self.underMouse() or self._mode
        if not active:
            p.end()
            return

        poly = self._text_polygon()
        pen = QPen(QColor(theme.ACCENT))
        pen.setWidth(1)
        pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPolygon(poly)

        # Corner resize handles.
        p.setPen(QPen(QColor(theme.ACCENT_ON), 1))
        p.setBrush(QColor(theme.ACCENT))
        for pt in self._handle_centers().values():
            p.drawRect(QRectF(
                pt.x() - _HANDLE_SIZE / 2, pt.y() - _HANDLE_SIZE / 2,
                _HANDLE_SIZE, _HANDLE_SIZE,
            ))

        # Rotation bubble — small filled circle above, connected to the
        # text top edge with a thin dashed line. Always reachable above
        # the (rotated) text, just like Photoshop / Illustrator.
        rh = self._rotate_handle_pos()
        # Connector line: from the rotated top-edge midpoint to the bubble.
        top_mid_local = QPointF(0, -rect.height() / 2)
        # Apply the same translate+rotate the text uses.
        t = QTransform()
        t.translate(center.x(), center.y())
        t.rotate(self._style.rotation_deg)
        top_mid_world = t.map(top_mid_local)
        line_pen = QPen(QColor(theme.ACCENT))
        line_pen.setWidth(1)
        line_pen.setStyle(Qt.DashLine)
        p.setPen(line_pen)
        p.setBrush(Qt.NoBrush)
        p.drawLine(top_mid_world, rh)
        # Bubble.
        p.setPen(QPen(QColor(theme.ACCENT_ON), 1))
        p.setBrush(QColor(theme.ACCENT))
        p.drawEllipse(rh, _ROTATE_HANDLE_RADIUS, _ROTATE_HANDLE_RADIUS)

        p.end()

    # --- mouse handling ----------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.LeftButton:
            return
        # Priority: rotate handle > corner resize > body move. The body
        # check is polygon-based (the rotated text shape) rather than
        # rectangle-based so users can grab rotated text from anywhere
        # within its visible bounds.
        if self._hit_rotate_handle(event.position()):
            self._mode = "rotate"
            self._rotate_start_angle = self._angle_to(event.position())
            self._rotate_start_rotation = self._style.rotation_deg
            self.setCursor(Qt.CrossCursor)
            event.accept()
            return
        handle = self._hit_handle(event.position())
        if handle is not None:
            self._mode = "resize"
            self._resize_anchor = self._text_center()
            self._resize_start_dist = max(
                1.0,
                _length(event.position() - self._resize_anchor),
            )
            self._resize_start_size = self._style.size_pct
            self.setCursor(Qt.SizeFDiagCursor)
            event.accept()
            return
        if not self._text_polygon().containsPoint(
                event.position(), Qt.OddEvenFill):
            return  # let other widgets get the click
        self._mode = "move"
        self._drag_offset = event.position() - self._text_center()
        self.setCursor(Qt.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._mode:
            if self._hit_rotate_handle(event.position()):
                self.setCursor(Qt.CrossCursor)
            elif self._hit_handle(event.position()):
                self.setCursor(Qt.SizeFDiagCursor)
            elif self._text_polygon().containsPoint(
                    event.position(), Qt.OddEvenFill):
                self.setCursor(Qt.OpenHandCursor)
            else:
                self.unsetCursor()
            return
        v = self._video_display_rect()
        if v.isEmpty():
            return
        if self._mode == "move":
            target = event.position() - self._drag_offset
            cx = (target.x() - v.x()) / v.width()
            cy = (target.y() - v.y()) / v.height()
            self._cx = max(0.0, min(1.0, cx))
            self._cy = max(0.0, min(1.0, cy))
            self._refresh_mask()
            self.update()
            self.positionChanged.emit(self._cx, self._cy)
        elif self._mode == "resize" and self._resize_anchor is not None:
            cur_dist = max(1.0, _length(event.position() - self._resize_anchor))
            scale = cur_dist / self._resize_start_dist
            new_size = max(2.0, min(40.0, self._resize_start_size * scale))
            self._style.size_pct = new_size
            self._refresh_mask()
            self.update()
            self.sizeChanged.emit(new_size)
        elif self._mode == "rotate":
            cur_angle = self._angle_to(event.position())
            delta = cur_angle - self._rotate_start_angle
            self._style.rotation_deg = (
                self._rotate_start_rotation + delta
            ) % 360
            self._refresh_mask()
            self.update()
            self.rotationChanged.emit(self._style.rotation_deg)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._mode:
            self._mode = ""
            self._resize_anchor = None
            self.setCursor(Qt.OpenHandCursor)
            event.accept()


def _length(p: QPointF) -> float:
    return math.sqrt(p.x() * p.x() + p.y() * p.y())
