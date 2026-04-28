"""Custom window chrome — Windows-style titlebar.

Used when the app runs in frameless mode. Mirrors the cove-nexus pattern:
small icon + title on the left, three Windows-style controls on the right
(minimise, maximise, close). The whole bar is the drag region; the buttons
sit in `no-drag` zones so clicks on them don't accidentally start a window
move. Edge resizing is driven by `FramelessResizer`.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from . import theme


_TITLEBAR_HEIGHT = 36
_RESIZE_MARGIN = 6
_BTN_W = 46


def _hidpi_pixmap(path: str, size: int, widget: QWidget) -> QPixmap:
    """Load a pixmap rendered at the screen's actual pixel density.

    `QPixmap(path).scaled(14, 14, …)` at a 2× display throws away half the
    detail. By rendering at `size * dpr` and tagging the pixmap with the
    device pixel ratio, Qt downsamples once at the right size and the
    icon stays sharp."""
    dpr = float(widget.devicePixelRatioF()) if widget is not None else 1.0
    if dpr <= 0:
        dpr = 1.0
    actual = max(1, int(round(size * dpr)))
    pix = QPixmap(path).scaled(
        actual, actual, Qt.KeepAspectRatio, Qt.SmoothTransformation,
    )
    pix.setDevicePixelRatio(dpr)
    return pix


class _WinButton(QPushButton):
    """Windows-style flush window control: 46×titlebar, glyph drawn in code.

    Implementing the glyph in paint code (instead of Unicode text) keeps the
    line weights consistent at any DPI."""

    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind = kind
        self.setObjectName(f"winbtn-{kind}")
        self.setFixedSize(_BTN_W, _TITLEBAR_HEIGHT)
        self.setCursor(Qt.ArrowCursor)
        self.setFocusPolicy(Qt.NoFocus)
        # Hover background varies by kind: close goes red, others are a subtle
        # white-alpha. Use plain QSS for the background, paint the glyph in
        # code to keep stroke weights sharp.
        if kind == "close":
            self.setStyleSheet(
                f"QPushButton#winbtn-{kind} {{"
                f" background: transparent; border: none; padding: 0; }}"
                f"QPushButton#winbtn-{kind}:hover {{ background: #e81123; }}"
            )
        else:
            self.setStyleSheet(
                f"QPushButton#winbtn-{kind} {{"
                f" background: transparent; border: none; padding: 0; }}"
                f"QPushButton#winbtn-{kind}:hover {{"
                f" background: rgba(255,255,255,0.06); }}"
            )

    def paintEvent(self, event) -> None:  # noqa: ANN001
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        # Glyph color: white on red close-hover, dim text otherwise, full
        # text on hover.
        if self._kind == "close" and self.underMouse():
            color = QColor("#ffffff")
        elif self.underMouse():
            color = QColor(theme.TEXT)
        else:
            color = QColor(theme.TEXT_DIM)
        pen = p.pen()
        pen.setColor(color)
        pen.setWidth(1)
        p.setPen(pen)
        cx = self.width() / 2
        cy = self.height() / 2
        s = 5  # half-side of glyph
        if self._kind == "min":
            p.drawLine(int(cx - s), int(cy), int(cx + s), int(cy))
        elif self._kind == "max":
            p.drawRect(int(cx - s), int(cy - s), int(s * 2), int(s * 2))
        elif self._kind == "close":
            p.drawLine(int(cx - s), int(cy - s), int(cx + s), int(cy + s))
            p.drawLine(int(cx - s), int(cy + s), int(cx + s), int(cy - s))
        p.end()


class CoveTitleBar(QWidget):
    """Cove titlebar: brand mark on the left, centered title, and
    Windows-style minimize / maximize / close buttons flush right.

    Drag uses `windowHandle().startSystemMove()` so the window manager
    handles the move — that works on every platform (X11 + Wayland +
    Windows + macOS) and survives display-server quirks that the
    manual mouseMove approach doesn't. Manual fallback kicks in if the
    Qt window handle isn't ready yet.
    """

    def __init__(self, window: QWidget, *, icon_path: str | None = None,
                 title: str = "", version: str = "") -> None:
        super().__init__(window)
        self._window = window
        self._drag_press_pos: QPoint | None = None  # for fallback
        self._fallback_offset: QPoint | None = None
        self.setFixedHeight(_TITLEBAR_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setObjectName("cove-titlebar")
        self.setStyleSheet(
            f"QWidget#cove-titlebar {{"
            f"  background: {theme.BG};"
            f"  border-bottom: 1px solid {theme.BORDER};"
            f"}}"
        )

        # Outer layout: just the right-side buttons. The title block is a
        # child positioned absolutely (centered on resize) so it never
        # competes with the buttons for layout space.
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Left brand mark — small icon for context, NOT the title.
        # Rendered at slightly larger size (16px) and via _hidpi_pixmap so
        # it stays sharp on HiDPI displays.
        if icon_path:
            self._icon_path = icon_path
            self._brand = QLabel()
            self._brand.setFixedSize(16, 16)
            self._brand.setAttribute(Qt.WA_TransparentForMouseEvents)
            self._brand.setPixmap(_hidpi_pixmap(icon_path, 16, self._brand))
            self._brand.setStyleSheet("background: transparent;")
            self._brand.setParent(self)
            self._brand.move(14, (_TITLEBAR_HEIGHT - 16) // 2)
        else:
            self._icon_path = None
            self._brand = None

        layout.addStretch(1)
        # Right window controls — each is 46×bar, flush right, no
        # right-edge padding.
        self._btn_min   = _WinButton("min", self)
        self._btn_max   = _WinButton("max", self)
        self._btn_close = _WinButton("close", self)
        self._btn_min.clicked.connect(self._on_minimize)
        self._btn_max.clicked.connect(self._on_max_restore)
        self._btn_close.clicked.connect(self._window.close)
        layout.addWidget(self._btn_min)
        layout.addWidget(self._btn_max)
        layout.addWidget(self._btn_close)

        # Title block — absolutely positioned so it stays exactly centered
        # regardless of window width.
        self._title_block = QWidget(self)
        self._title_block.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._title_block.setStyleSheet("background: transparent;")
        title_layout = QHBoxLayout(self._title_block)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(8)
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(
            f"color: {theme.TEXT}; font-size: 12px; font-weight: 500;"
            f" background: transparent;"
        )
        title_layout.addWidget(title_lbl)
        if version:
            ver_lbl = QLabel(version)
            ver_lbl.setStyleSheet(
                f"color: {theme.TEXT_FAINT}; font-size: 10.5px;"
                f" font-family: '{theme.FONT_MONO}', monospace;"
                f" background: transparent;"
            )
            title_layout.addWidget(ver_lbl)
        self._title_block.adjustSize()

    # --- Title positioning ----------------------------------------------

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        block = self._title_block
        block.adjustSize()
        x = (self.width() - block.width()) // 2
        y = (self.height() - block.height()) // 2
        block.move(x, y)
        block.raise_()

    def showEvent(self, event) -> None:  # noqa: ANN001
        # Re-render the brand icon now that the window is on its actual
        # screen — devicePixelRatioF() is more accurate after show.
        super().showEvent(event)
        if self._brand is not None and self._icon_path:
            self._brand.setPixmap(_hidpi_pixmap(self._icon_path, 16, self._brand))

    # --- Window controls ------------------------------------------------

    def _on_minimize(self) -> None:
        self._window.showMinimized()

    def _on_max_restore(self) -> None:
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()

    # --- Drag to move ---------------------------------------------------

    def _hits_window_button(self, pos: QPoint) -> bool:
        for btn in (self._btn_min, self._btn_max, self._btn_close):
            if btn.geometry().contains(pos):
                return True
        return False

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if (event.button() == Qt.LeftButton
                and not self._hits_window_button(event.position().toPoint())):
            # Preferred path: ask the window manager to move the window.
            # `startSystemMove` is the reliable way to drag a frameless
            # window across X11 / Wayland / Win32 / macOS.
            handle = self._window.windowHandle()
            if handle is not None and hasattr(handle, "startSystemMove"):
                if handle.startSystemMove():
                    event.accept()
                    return
            # Fallback: manual move on platforms where startSystemMove
            # isn't available or refused (older Qt builds).
            self._fallback_offset = (
                event.globalPosition().toPoint()
                - self._window.frameGeometry().topLeft()
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._fallback_offset and event.buttons() & Qt.LeftButton:
            if self._window.isMaximized():
                self._window.showNormal()
                self._fallback_offset = QPoint(
                    self.width() // 2, _TITLEBAR_HEIGHT // 2,
                )
            self._window.move(
                event.globalPosition().toPoint() - self._fallback_offset
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._fallback_offset is not None:
            self._fallback_offset = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if (event.button() == Qt.LeftButton
                and not self._hits_window_button(event.position().toPoint())):
            self._on_max_restore()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class FramelessResizer:
    """Helper for resizing a frameless QMainWindow.

    Owned by `MainWindow`; the window's mouse event overrides delegate to
    `try_press` / `try_move` / `try_release`. Cursor feedback is handled
    via `QGuiApplication.setOverrideCursor` (a stack) so child widgets
    that set their own cursors always win — no more "stuck SizeVerCursor"
    after the user moves from a window edge into a control."""

    # Cached at class level so we don't allocate every move event.
    _CURSORS = {
        "l":  Qt.SizeHorCursor, "r":  Qt.SizeHorCursor,
        "t":  Qt.SizeVerCursor, "b":  Qt.SizeVerCursor,
        "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor,
        "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor,
    }

    def __init__(self, window) -> None:  # noqa: ANN001
        self._w = window
        self._resizing_edge: str | None = None
        self._press_global: QPoint | None = None
        self._press_geom: QRect | None = None
        # The current edge under the cursor (None when not on any edge).
        # When this changes, push/pop the override cursor.
        self._hover_edge: str | None = None

    def try_press(self, event) -> bool:  # noqa: ANN001
        if event.button() != Qt.LeftButton:
            return False
        edge = self._edge_for(event.position().toPoint())
        if not edge:
            return False
        self._resizing_edge = edge
        self._press_global = event.globalPosition().toPoint()
        self._press_geom = self._w.geometry()
        return True

    def try_move(self, event) -> bool:  # noqa: ANN001
        if not (event.buttons() & Qt.LeftButton):
            self._update_cursor(event.position().toPoint())
            return False
        if not self._resizing_edge:
            return False
        self._do_resize(event.globalPosition().toPoint())
        return True

    def try_release(self, _event) -> bool:  # noqa: ANN001
        if not self._resizing_edge:
            return False
        self._resizing_edge = None
        self._press_global = None
        self._press_geom = None
        # Drop the hover cursor — we may have left the edge during drag.
        self.clear_hover()
        return True

    def clear_hover(self) -> None:
        """Reset edge cursor — call from leaveEvent/focus loss to avoid
        leftover cursor when the pointer exits the window via a child
        widget that ate the move event."""
        if self._hover_edge is not None:
            QGuiApplication.restoreOverrideCursor()
            self._hover_edge = None

    def _edge_for(self, pos: QPoint) -> str | None:
        if self._w.isMaximized():
            return None
        m = _RESIZE_MARGIN
        w, h = self._w.width(), self._w.height()
        x, y = pos.x(), pos.y()
        on_l = x <= m
        on_r = x >= w - m
        on_t = y <= m
        on_b = y >= h - m
        if on_t and on_l: return "tl"
        if on_t and on_r: return "tr"
        if on_b and on_l: return "bl"
        if on_b and on_r: return "br"
        if on_l: return "l"
        if on_r: return "r"
        if on_t: return "t"
        if on_b: return "b"
        return None

    def _update_cursor(self, pos: QPoint) -> None:
        """Push/pop a single override cursor reflecting the current edge.

        Override cursor is a stack — child widgets that call setCursor()
        in their hover handlers will sit on top and win, so we never end
        up with a stuck resize cursor over a child widget."""
        edge = self._edge_for(pos)
        if edge == self._hover_edge:
            return
        if self._hover_edge is not None:
            QGuiApplication.restoreOverrideCursor()
        if edge is not None:
            QGuiApplication.setOverrideCursor(self._CURSORS[edge])
        self._hover_edge = edge

    def _do_resize(self, global_pos: QPoint) -> None:
        if not (self._press_global and self._press_geom and self._resizing_edge):
            return
        dx = global_pos.x() - self._press_global.x()
        dy = global_pos.y() - self._press_global.y()
        g = QRect(self._press_geom)
        edge = self._resizing_edge
        min_w = max(self._w.minimumSize().width(), 720)
        min_h = max(self._w.minimumSize().height(), 480)
        if "l" in edge:
            new_x = min(g.x() + dx, g.right() - min_w)
            g.setLeft(new_x)
        if "r" in edge:
            g.setRight(g.right() + dx)
        if "t" in edge:
            new_y = min(g.y() + dy, g.bottom() - min_h)
            g.setTop(new_y)
        if "b" in edge:
            g.setBottom(g.bottom() + dy)
        self._w.setGeometry(g)
