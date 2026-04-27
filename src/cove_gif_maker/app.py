from __future__ import annotations

import math
import sys as _sys
from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QPoint,
    QRectF,
    QSize,
    QSizeF,
    QStandardPaths,
    QThread,
    QTimer,
    QUrl,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QIcon,
    QKeySequence,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPixmap,
    QRadialGradient,
    QShortcut,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (
    QColorDialog,
    QFileDialog,
    QFrame,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import __version__, ffmpeg_utils as ff, prefs, theme, updater
from .batch import BatchQueue, BatchQueuePanel, QueueStatus
from .caption_overlay import CaptionOverlay, CaptionStyle
from .chrome import CoveTitleBar, FramelessResizer
from .controls import (
    CoveSlider, EffectRow, FilePill, KV, PresetCard, RailTabs, Segmented,
    StatusLine, Stepper,
)
from .converter import ConvertJob, start_conversion
from .crop_overlay import CropOverlay
from .thumbnails import start_thumbnails
from .timeline import TrimBar


VIDEO_FILTERS = "Videos (*.mp4 *.mkv *.webm *.mov *.avi *.m4v *.mpg *.mpeg *.wmv);;All files (*)"

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
ICON_PATH = ASSETS_DIR / "cove_icon.png"


# Quality presets — same content as before, but now exposed as a 4-card
# grid so "Custom" disappears from the picker (it's still implied when the
# user touches an individual setting).
PRESETS: dict[str, dict] = {
    # WebP is the default — smaller files at the same quality, supported
    # by every modern client (Discord/Reddit/Slack/etc.).
    "Discord":     {"fps": 12, "scale": 50, "palette": 128, "fmt": "WebP", "webp_q": 80,
                    "hint": "≤10 MB · 12 fps"},
    "Reddit / X":  {"fps": 15, "scale": 75, "palette": 192, "fmt": "WebP", "webp_q": 85,
                    "hint": "15 fps · HQ"},
    "Email (tiny)":{"fps":  8, "scale": 25, "palette":  64, "fmt": "GIF",  "webp_q": 70,
                    "hint": "≤2 MB · GIF"},
    "Full / WebP": {"fps": 24, "scale": 100,"palette": 256, "fmt": "WebP", "webp_q": 92,
                    "hint": "24 fps · max"},
}


def _sys_is_macos() -> bool:
    return _sys.platform == "darwin"


def _hidpi_pixmap(path: str, size: int, widget=None) -> QPixmap:
    """Load a pixmap at the screen's actual pixel density."""
    dpr = float(widget.devicePixelRatioF()) if widget is not None else 1.0
    if dpr <= 0:
        dpr = 1.0
    actual = max(1, int(round(size * dpr)))
    pix = QPixmap(path).scaled(
        actual, actual, Qt.KeepAspectRatio, Qt.SmoothTransformation,
    )
    pix.setDevicePixelRatio(dpr)
    return pix


# =====================================================================
# CoveRoot — paints the radial-glow background
# =====================================================================

class CoveRoot(QWidget):
    """Central widget that paints the cove-nexus radial-glow background."""

    def paintEvent(self, _event) -> None:  # noqa: ANN001
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect()
        base = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        base.setColorAt(0.0, QColor(theme.BG_GRAD_TOP))
        base.setColorAt(1.0, QColor(theme.BG_GRAD_BOT))
        p.fillRect(rect, base)
        # Top-right teal glow.
        a = QColor(theme.ACCENT)
        g1 = QRadialGradient(rect.right() - 200, -120, max(rect.width(), rect.height()))
        g1.setColorAt(0.00, QColor(a.red(), a.green(), a.blue(), 13))
        g1.setColorAt(0.55, QColor(a.red(), a.green(), a.blue(), 0))
        p.fillRect(rect, g1)
        # Bottom-left purple.
        g2 = QRadialGradient(80, rect.bottom() + 80, max(rect.width(), rect.height()))
        g2.setColorAt(0.00, QColor(124, 92, 255, 10))
        g2.setColorAt(0.60, QColor(124, 92, 255, 0))
        p.fillRect(rect, g2)
        p.end()


# =====================================================================
# Preview — checkered transparency-style background under the video
# =====================================================================

class PreviewSurface(QWidget):
    """Background widget for the preview area. Paints a subtle checker
    pattern + radial vignette so the preview reads as a "stage" even when
    no video is loaded."""

    def paintEvent(self, _event) -> None:  # noqa: ANN001
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        p.fillRect(self.rect(), QColor(theme.BG))
        # Subtle 16px checker pattern.
        light = QColor(255, 255, 255, 3)
        cell = 16
        for y in range(0, self.height(), cell):
            for x in range(0, self.width(), cell):
                if ((x // cell) + (y // cell)) % 2 == 0:
                    p.fillRect(x, y, cell, cell, light)
        # Vignette: a soft radial darken in the corners.
        p.setRenderHint(QPainter.Antialiasing, True)
        rad = QRadialGradient(
            self.rect().center(),
            max(self.width(), self.height()) * 0.6,
        )
        rad.setColorAt(0.0, QColor(0, 0, 0, 0))
        rad.setColorAt(0.7, QColor(0, 0, 0, 0))
        rad.setColorAt(1.0, QColor(0, 0, 0, 90))
        p.fillRect(self.rect(), rad)
        p.end()


# =====================================================================
# Drop frame — handles drag/drop of video files
# =====================================================================

class DropFrame(QFrame):
    fileDropped = Signal(str)
    filesDropped = Signal(list)
    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("drop")
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        theme.apply_drop_style(self, loaded=False)

    def set_loaded_style(self, loaded: bool) -> None:
        theme.apply_drop_style(self, loaded=loaded)
        self.setCursor(Qt.ArrowCursor if loaded else Qt.PointingHandCursor)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            theme.apply_drop_style(self, loaded=False, hover=True)

    def dragLeaveEvent(self, _event) -> None:  # noqa: ANN001
        theme.apply_drop_style(self, loaded=False)

    def dropEvent(self, event: QDropEvent) -> None:
        theme.apply_drop_style(self, loaded=False)
        urls = event.mimeData().urls()
        paths = [u.toLocalFile() for u in urls if u.toLocalFile()]
        if not paths:
            return
        if len(paths) == 1:
            self.fileDropped.emit(paths[0])
        else:
            self.filesDropped.emit(paths)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


# =====================================================================
# Video view
# =====================================================================

class VideoView(QGraphicsView):
    """QGraphicsView wrapping a QGraphicsVideoItem."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.SmoothPixmapTransform)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet("background:#000; border:none;")
        self.setAlignment(Qt.AlignCenter)

        self.video_item = QGraphicsVideoItem()
        self.video_item.setSize(QSizeF(640, 360))
        self._scene.addItem(self.video_item)
        self._scene.setSceneRect(QRectF(0, 0, 640, 360))

    def video_output(self) -> QGraphicsVideoItem:
        return self.video_item

    def set_native_size(self, width: int, height: int) -> None:
        size = QSizeF(width, height)
        self.video_item.setSize(size)
        self._scene.setSceneRect(QRectF(0, 0, width, height))
        self._fit()

    def _fit(self) -> None:
        if not self._scene.sceneRect().isEmpty():
            self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        self._fit()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


# =====================================================================
# Color picker button — opens QColorDialog, paints as the chosen color
# =====================================================================

class ColorButton(QPushButton):
    colorChanged = Signal(str)

    def __init__(self, hex_color: str, label: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hex = hex_color
        self._label = label
        self.setMinimumHeight(38)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 10, 6)
        layout.setSpacing(8)
        self._swatch = QLabel()
        self._swatch.setFixedSize(22, 22)
        self._swatch.setStyleSheet(self._swatch_qss())
        layout.addWidget(self._swatch)
        text_block = QVBoxLayout()
        text_block.setSpacing(1)
        self._label_lbl = QLabel(label)
        self._label_lbl.setStyleSheet(
            f"color: {theme.TEXT_DIM}; font-size: 11px; background: transparent; border: none;"
        )
        self._hex_lbl = QLabel(hex_color.upper())
        self._hex_lbl.setStyleSheet(
            f"color: {theme.TEXT_FAINT}; font-size: 10.5px;"
            f" font-family: '{theme.FONT_MONO}', monospace;"
            f" background: transparent; border: none;"
        )
        text_block.addWidget(self._label_lbl)
        text_block.addWidget(self._hex_lbl)
        layout.addLayout(text_block, stretch=1)
        self._refresh_style()
        self.clicked.connect(self._open_dialog)

    def hex(self) -> str:
        return self._hex

    def set_hex(self, hex_color: str) -> None:
        self._hex = hex_color
        self._swatch.setStyleSheet(self._swatch_qss())
        self._hex_lbl.setText(hex_color.upper())
        self.colorChanged.emit(hex_color)

    def _swatch_qss(self) -> str:
        return (
            f"background: {self._hex}; border: 1px solid {theme.BORDER_STRONG};"
            f" border-radius: 5px;"
        )

    def _refresh_style(self) -> None:
        self.setStyleSheet(
            f"QPushButton {{ background: {theme.SURFACE};"
            f" border: 1px solid {theme.BORDER}; border-radius: {theme.RADIUS_SM}px;"
            f" padding: 0; text-align: left; }}"
            f"QPushButton:hover {{ background: {theme.SURFACE_2};"
            f" border-color: {theme.BORDER_HARD}; }}"
        )

    def _open_dialog(self) -> None:
        col = QColorDialog.getColor(
            QColor(self._hex), self,
            f"Pick {self._label.lower() or 'color'}",
            QColorDialog.ShowAlphaChannel,
        )
        if col.isValid():
            self.set_hex(col.name())


# =====================================================================
# Result chip
# =====================================================================

class ResultChip(QFrame):
    cleared = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._path: Path | None = None
        self.setVisible(False)
        self.setStyleSheet(
            f"QFrame {{ background: {theme.SURFACE_2}; border: 1px solid {theme.ACCENT_RING};"
            f" border-radius: {theme.RADIUS_SM}px; }}"
            f"QLabel {{ background: transparent; border: none; }}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 8, 8)
        layout.setSpacing(8)

        self._icon = QLabel()
        self._icon.setFixedSize(36, 36)
        self._icon.setAlignment(Qt.AlignCenter)
        self._icon.setStyleSheet(
            f"background: #000; border: 1px solid {theme.BORDER_HARD};"
            f" border-radius: 6px;"
        )
        layout.addWidget(self._icon)

        text_block = QVBoxLayout()
        text_block.setSpacing(2)
        self._name = QLabel("")
        self._name.setStyleSheet(f"color: {theme.TEXT}; font-size: 12.5px; font-weight: 500;")
        self._meta = QLabel("")
        self._meta.setStyleSheet(
            f"color: {theme.TEXT_DIM}; font-family: '{theme.FONT_MONO}', monospace;"
            f" font-size: 10.5px;"
        )
        text_block.addWidget(self._name)
        text_block.addWidget(self._meta)
        layout.addLayout(text_block, stretch=1)

        self._open_btn = QPushButton("Open folder")
        self._open_btn.setCursor(Qt.PointingHandCursor)
        self._open_btn.setStyleSheet(
            f"QPushButton {{ background: {theme.ACCENT_SOFT}; color: {theme.ACCENT};"
            f" border: 1px solid {theme.ACCENT_RING}; border-radius: 6px;"
            f" padding: 4px 10px; font-size: 11.5px; font-weight: 500; }}"
            f"QPushButton:hover {{ background: rgba(80,230,207,0.22); color: #ffffff; }}"
        )
        self._open_btn.clicked.connect(self._on_open_folder)
        layout.addWidget(self._open_btn)

        clear = QPushButton("×")
        clear.setFixedSize(20, 20)
        clear.setCursor(Qt.PointingHandCursor)
        clear.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {theme.TEXT_FAINT};"
            f" border: 1px solid {theme.BORDER}; border-radius: 10px;"
            f" padding: 0; font-size: 12px; }}"
            f"QPushButton:hover {{ color: {theme.DANGER};"
            f" border-color: rgba(255,107,107,0.45); }}"
        )
        clear.clicked.connect(self._on_clear)
        layout.addWidget(clear)

    def show_result(self, path: Path) -> None:
        self._path = path
        self._name.setText(path.name)
        try:
            kb = path.stat().st_size / 1024
            size_text = f"{kb:.0f} KB" if kb < 1024 else f"{kb / 1024:.1f} MB"
        except OSError:
            size_text = "—"
        self._meta.setText(f"{path.suffix.lstrip('.').upper()} · {size_text}")
        pix = QPixmap(str(path))
        if not pix.isNull():
            self._icon.setPixmap(pix.scaled(
                36, 36, Qt.KeepAspectRatio, Qt.SmoothTransformation,
            ))
        self.setVisible(True)

    def _on_clear(self) -> None:
        self._path = None
        self.setVisible(False)
        self.cleared.emit()

    def _on_open_folder(self) -> None:
        if self._path is None:
            return
        import os as _os
        import subprocess as _sp
        target = str(self._path.parent)
        try:
            if _os.name == "nt":
                _sp.Popen(["explorer", f"/select,{self._path}"])
            elif _sys_is_macos():
                _sp.Popen(["open", "-R", str(self._path)])
            else:
                _sp.Popen(["xdg-open", target])
        except Exception:  # noqa: BLE001
            pass


# =====================================================================
# Recent files row — chips inside the placeholder
# =====================================================================

class RecentRow(QWidget):
    fileChosen = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(6)
        self._label = QLabel("Recent")
        self._label.setProperty("role", "rail-section")
        self._label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._label)
        self._row = QHBoxLayout()
        self._row.setSpacing(6)
        self._row.setAlignment(Qt.AlignCenter)
        layout.addLayout(self._row)
        self.refresh()

    def refresh(self) -> None:
        while self._row.count():
            item = self._row.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.deleteLater()
        recents = prefs.recent_files()
        if not recents:
            self.setVisible(False)
            return
        self.setVisible(True)
        for path in recents[:5]:
            chip = self._make_chip(path)
            self._row.addWidget(chip)

    def _make_chip(self, path_str: str) -> QPushButton:
        p = Path(path_str)
        chip = QPushButton(p.name)
        chip.setCursor(Qt.PointingHandCursor)
        chip.setToolTip(path_str)
        chip.setStyleSheet(
            f"QPushButton {{ background: rgba(255,255,255,0.03); color: {theme.TEXT_DIM};"
            f" border: 1px solid {theme.BORDER}; border-radius: 999px;"
            f" padding: 4px 12px; font-size: 11.5px;"
            f" font-family: '{theme.FONT_MONO}', monospace; }}"
            f"QPushButton:hover {{ color: {theme.TEXT};"
            f" border-color: {theme.ACCENT_RING}; background: {theme.ACCENT_SOFT}; }}"
        )
        chip.clicked.connect(lambda: self.fileChosen.emit(path_str))
        return chip


# =====================================================================
# Main window
# =====================================================================

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"Cove GIF Maker v{__version__}")
        self.resize(1380, 880)
        self.setMinimumSize(960, 640)
        if ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(ICON_PATH)))

        self._video_path: Path | None = None
        self._info: ff.VideoInfo | None = None
        self._thumbs_thread: QThread | None = None
        self._thumbs_worker = None
        self._convert_thread: QThread | None = None
        self._convert_worker = None
        self._suppress_preset_reset = False
        self._batch = BatchQueue()
        self._batch.changed.connect(self._on_batch_changed)
        self._batch_active_idx: int | None = None
        self._tool: str = "trim"   # "trim" | "crop"
        self._active_preset: str | None = None
        self._last_progress = 0
        self._last_eta: float | None = None

        # Frameless window with custom titlebar (cove design).
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)
        self._frameless_resizer = FramelessResizer(self)
        self.setMouseTracking(True)

        self._build_ui()

        # Apply Discord preset on startup (or persisted default).
        startup_preset = prefs.default_preset()
        if startup_preset in self._preset_cards:
            self._apply_preset(startup_preset)
        else:
            self._apply_preset("Discord")

        self._update_controls_enabled()
        self._refresh_size_estimate()
        self._check_ffmpeg()

        # Spacebar play/pause shortcut.
        sc = QShortcut(QKeySequence(Qt.Key_Space), self)
        sc.setContext(Qt.WindowShortcut)
        sc.activated.connect(self._on_space_pressed)

        # Restore window geometry across launches.
        geom = prefs.window_geometry()
        if geom:
            self.restoreGeometry(geom)

        self._updater = updater.UpdateController(
            parent=self,
            current_version=__version__,
            repo="Sin213/cove-gif-maker",
            app_display_name="Cove GIF Maker",
            cache_subdir="cove-gif-maker",
        )
        QTimer.singleShot(4000, self._updater.check)

    # -----------------------------------------------------------------
    # Layout
    # -----------------------------------------------------------------

    def _build_ui(self) -> None:
        central = CoveRoot()
        central.setObjectName("cove-root")
        self.setCentralWidget(central)

        # Full-bleed chrome — same idea as nexus's data-chrome="none" mode.
        # The titlebar + body extend edge-to-edge with no outer margin,
        # so the app fills the entire window.
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        chrome = QWidget()
        chrome.setObjectName("cove-chrome")
        outer.addWidget(chrome)

        chrome_layout = QVBoxLayout(chrome)
        chrome_layout.setContentsMargins(0, 0, 0, 0)
        chrome_layout.setSpacing(0)

        # Custom macOS-style titlebar.
        self._titlebar = CoveTitleBar(
            self,
            icon_path=str(ICON_PATH) if ICON_PATH.exists() else None,
            title="Cove GIF Maker",
            version=f"v{__version__}",
        )
        chrome_layout.addWidget(self._titlebar)

        # App body: stage + rail.
        body = QWidget()
        chrome_layout.addWidget(body, stretch=1)
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(self._build_stage(), stretch=1)
        body_layout.addWidget(self._build_rail(), stretch=0)

        # Hidden status bar for log messages — not shown but used by the
        # existing self.status.showMessage() calls; we surface relevant
        # parts via the StatusLine in the rail footer.
        self.status = QStatusBar()
        self.status.setVisible(False)
        self.setStatusBar(self.status)

    # -----------------------------------------------------------------
    # Stage (left)
    # -----------------------------------------------------------------

    def _build_stage(self) -> QWidget:
        stage = QWidget()
        stage.setObjectName("cove-stage")
        layout = QVBoxLayout(stage)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_stage_head())
        layout.addWidget(self._build_preview(), stretch=1)
        layout.addWidget(self._build_timeline())
        return stage

    def _build_stage_head(self) -> QWidget:
        head = QWidget()
        head.setStyleSheet(
            f"QWidget {{ background: transparent; }}"
            f"QFrame#cove-divider {{ background: {theme.BORDER}; }}"
        )
        layout = QVBoxLayout(head)
        layout.setContentsMargins(24, 18, 24, 14)
        layout.setSpacing(0)

        row = QHBoxLayout()
        row.setSpacing(12)

        # Title block: pulse + "GIF Maker", "Source: <pill>"
        title_block = QVBoxLayout()
        title_block.setSpacing(4)

        title_row = QHBoxLayout()
        title_row.setSpacing(9)
        self._pulse = StatusLine._PulseDot if False else _PulseDot(self)  # noqa: SLF001
        self._pulse.set_state("idle")
        title_row.addWidget(self._pulse)
        title_lbl = QLabel("GIF Maker")
        title_lbl.setProperty("role", "hero-title")
        title_row.addWidget(title_lbl)
        title_row.addStretch(1)
        title_block.addLayout(title_row)

        sub_row = QHBoxLayout()
        sub_row.setSpacing(8)
        sub_label = QLabel("Source")
        sub_label.setProperty("role", "hero-sub")
        self._file_pill = FilePill()
        self._file_pill.removeRequested.connect(self._on_clear_video)
        self._no_file_lbl = QLabel("Drop a video to begin")
        self._no_file_lbl.setStyleSheet(
            f"color: {theme.TEXT_FAINT}; font-size: 12px;"
        )
        sub_row.addWidget(sub_label)
        sub_row.addWidget(self._file_pill)
        sub_row.addWidget(self._no_file_lbl)
        sub_row.addStretch(1)
        title_block.addLayout(sub_row)

        row.addLayout(title_block)
        row.addStretch(1)

        # Right-side meta KV pairs.
        meta_row = QHBoxLayout()
        meta_row.setSpacing(20)
        self._kv_source = KV("Source", "—")
        self._kv_duration = KV("Duration", "—")
        self._kv_fps = KV("FPS", "—")
        meta_row.addWidget(self._kv_source)
        meta_row.addWidget(self._kv_duration)
        meta_row.addWidget(self._kv_fps)
        row.addLayout(meta_row)

        layout.addLayout(row)

        # Divider underneath
        divider = QFrame()
        divider.setObjectName("cove-divider")
        divider.setFixedHeight(1)
        layout.addSpacing(14)
        layout.addWidget(divider)
        return head

    # -----------------------------------------------------------------
    # Preview area (drop frame + video container)
    # -----------------------------------------------------------------

    def _build_preview(self) -> QWidget:
        wrap = PreviewSurface()
        wrap.setStyleSheet(f"background: {theme.BG};")
        wrap.setMinimumHeight(360)
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(0)

        # Drop frame is the click-target for opening files when no video.
        self.drop_frame = DropFrame()
        self.drop_frame.fileDropped.connect(self._on_file_dropped)
        self.drop_frame.filesDropped.connect(self._on_files_dropped)
        self.drop_frame.clicked.connect(self._on_drop_frame_clicked)
        drop_layout = QVBoxLayout(self.drop_frame)
        drop_layout.setContentsMargins(0, 0, 0, 0)
        drop_layout.setSpacing(8)

        # Video container (shown after a clip loads).
        self.video_container = QWidget()
        self.video_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.video_view = VideoView(self.video_container)
        self.video_view.clicked.connect(self._toggle_play)
        self.crop_overlay = CropOverlay(self.video_container)
        self.crop_overlay.setVisible(False)
        # Two caption overlays — second one stays empty/hidden until the
        # user clicks the + in the Caption tab.
        self.caption_overlay = CaptionOverlay(self.video_container)
        self.caption_overlay.positionChanged.connect(self._on_caption_position_changed)
        self.caption_overlay.sizeChanged.connect(self._on_caption_size_changed)
        self.caption_overlay.rotationChanged.connect(self._on_caption_rotation_changed)
        self.caption_overlay_2 = CaptionOverlay(self.video_container)
        self.caption_overlay_2.positionChanged.connect(self._on_caption_position_changed)
        self.caption_overlay_2.sizeChanged.connect(self._on_caption_size_changed)
        self.caption_overlay_2.rotationChanged.connect(self._on_caption_rotation_changed)
        self._caption2_visible = False
        self.video_container.installEventFilter(self)

        # Placeholder shown when no video is loaded.
        self.placeholder = QWidget()
        ph_layout = QVBoxLayout(self.placeholder)
        ph_layout.setContentsMargins(0, 0, 0, 0)
        ph_layout.setSpacing(10)
        ph_layout.addStretch(1)

        self.placeholder_text = QLabel("Drop a video here")
        self.placeholder_text.setAlignment(Qt.AlignCenter)
        self.placeholder_text.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.placeholder_text.setStyleSheet(
            f"color: {theme.TEXT}; font-size: 16px; font-weight: 500;"
            f" background: transparent; border: none;"
        )
        ph_layout.addWidget(self.placeholder_text)

        self.placeholder_hint = QLabel(
            "…or click to browse — MP4, MKV, WebM, MOV, AVI. Drop multiple to queue."
        )
        self.placeholder_hint.setAlignment(Qt.AlignCenter)
        self.placeholder_hint.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.placeholder_hint.setStyleSheet(
            f"color: {theme.TEXT_FAINT}; font-size: 12px;"
            f" background: transparent; border: none;"
        )
        ph_layout.addWidget(self.placeholder_hint)

        # Recent files row inside the placeholder.
        self.recent_row = RecentRow(self.placeholder)
        self.recent_row.fileChosen.connect(lambda s: self._load_video(Path(s)))
        ph_layout.addWidget(self.recent_row, alignment=Qt.AlignHCenter)
        ph_layout.addStretch(1)

        drop_layout.addWidget(self.placeholder)
        drop_layout.addWidget(self.video_container)
        self.video_container.hide()

        # Drag/drop bubbling.
        self.setAcceptDrops(True)
        for w in (
            self.video_container,
            self.video_view,
            self.video_view.viewport(),
            self.crop_overlay,
            self.caption_overlay,
            self.caption_overlay_2,
        ):
            w.setAcceptDrops(True)
            w.installEventFilter(self)

        layout.addWidget(self.drop_frame, stretch=1)

        # Player.
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.6)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video_view.video_output())
        self.player.positionChanged.connect(self._on_player_position)
        self.player.mediaStatusChanged.connect(self._on_media_status)
        return wrap

    # -----------------------------------------------------------------
    # Timeline (controls row + filmstrip)
    # -----------------------------------------------------------------

    def _build_timeline(self) -> QWidget:
        wrap = QWidget()
        wrap.setStyleSheet(
            f"QWidget {{ background: transparent; }}"
            f"QFrame#cove-tldivider {{ background: {theme.BORDER}; }}"
        )
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(24, 14, 24, 18)
        layout.setSpacing(12)

        # Top divider.
        top_div = QFrame()
        top_div.setObjectName("cove-tldivider")
        top_div.setFixedHeight(1)
        layout.insertWidget(0, top_div)

        # Controls row: round play button | trim/crop seg | start/end | range markers.
        ctrl = QHBoxLayout()
        ctrl.setSpacing(10)

        self.play_btn = QPushButton()
        self.play_btn.setFixedSize(32, 32)
        self.play_btn.setCursor(Qt.PointingHandCursor)
        self.play_btn.setText("▶")
        self.play_btn.setStyleSheet(
            f"QPushButton {{ background: {theme.ACCENT}; color: {theme.ACCENT_ON};"
            f" border: none; border-radius: 8px; font-size: 12px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {theme.ACCENT_2}; }}"
            f"QPushButton:disabled {{ background: {theme.SURFACE_3};"
            f" color: {theme.TEXT_FAINT}; }}"
        )
        self.play_btn.clicked.connect(self._toggle_play)
        ctrl.addWidget(self.play_btn)

        # Trim/Crop tool segmented.
        self._tool_seg = Segmented(
            options=[("trim", "Trim"), ("crop", "Crop")],
            active="trim",
        )
        self._tool_seg.activeChanged.connect(self._on_tool_changed)
        ctrl.addWidget(self._tool_seg)

        # Start/End buttons (set in/out at playhead).
        for label, slot in (("Start", self._set_in_at_playhead),
                            ("End", self._set_out_at_playhead)):
            btn = QPushButton(label)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(self._ghost_btn_qss())
            btn.clicked.connect(slot)
            ctrl.addWidget(btn)
            if label == "Start":
                self.set_in_btn = btn
            else:
                self.set_out_btn = btn

        self.crop_reset_btn = QPushButton("Reset crop")
        self.crop_reset_btn.setCursor(Qt.PointingHandCursor)
        self.crop_reset_btn.setStyleSheet(self._ghost_btn_qss())
        self.crop_reset_btn.setVisible(False)
        self.crop_reset_btn.clicked.connect(self._on_crop_reset)
        ctrl.addWidget(self.crop_reset_btn)

        ctrl.addStretch(1)

        # Range markers — Geist Mono mono-style:  00:00.00 → 00:05.60 · 5.60s
        self.range_label = QLabel("—")
        self.range_label.setStyleSheet(
            f"color: {theme.TEXT_DIM}; font-family: '{theme.FONT_MONO}', monospace;"
            f" font-size: 11px;"
        )
        ctrl.addWidget(self.range_label)
        layout.addLayout(ctrl)

        # Trim bar + batch panel.
        self.trim_bar = TrimBar()
        self.trim_bar.rangeChanged.connect(self._on_range_changed)
        self.trim_bar.rangePreviewing.connect(self._on_range_previewing)
        self.trim_bar.playheadMoved.connect(self._on_playhead_moved)
        layout.addWidget(self.trim_bar)

        self.batch_panel = BatchQueuePanel()
        self.batch_panel.bind(self._batch)
        self.batch_panel.removeRequested.connect(self._batch.remove)
        self.batch_panel.clearRequested.connect(self._batch.clear)
        self.batch_panel.setVisible(False)
        layout.addWidget(self.batch_panel)
        return wrap

    def _ghost_btn_qss(self) -> str:
        return (
            f"QPushButton {{ background: {theme.SURFACE}; color: {theme.TEXT_DIM};"
            f" border: 1px solid {theme.BORDER}; border-radius: {theme.RADIUS_XS}px;"
            f" padding: 5px 12px; font-size: 12px; }}"
            f"QPushButton:hover {{ background: {theme.SURFACE_2}; color: {theme.TEXT};"
            f" border-color: {theme.BORDER_HARD}; }}"
            f"QPushButton:disabled {{ color: {theme.TEXT_FAINTER};"
            f" background: transparent; }}"
        )

    # -----------------------------------------------------------------
    # Rail (right) — tabs + body + sticky footer
    # -----------------------------------------------------------------

    def _build_rail(self) -> QWidget:
        rail = QWidget()
        rail.setObjectName("cove-rail")
        rail.setFixedWidth(360)
        layout = QVBoxLayout(rail)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Tabs.
        self._tabs = RailTabs([
            ("output", "Output"),
            ("effects", "Effects"),
            ("caption", "Caption"),
        ], active="output")
        self._tabs.activeChanged.connect(self._on_tab_changed)
        layout.addWidget(self._tabs)

        # Stack of tab content (Output / Effects / Caption).
        self._tab_stack = QStackedWidget()
        layout.addWidget(self._tab_stack, stretch=1)

        self._tab_stack.addWidget(self._build_tab_output())
        self._tab_stack.addWidget(self._build_tab_effects())
        self._tab_stack.addWidget(self._build_tab_caption())

        # Sticky footer — estimated size + Convert/Cancel + status line.
        layout.addWidget(self._build_rail_footer())
        return rail

    def _build_tab_output(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 4, 0, 110)
        layout.setSpacing(0)

        # Preset section ----------------------------------------------
        self._preset_cards: dict[str, PresetCard] = {}

        section = self._make_section_widget("Preset", reset_label="Reset")
        section_inner_layout = section.property("layout")
        grid_widget = QWidget()
        grid = QHBoxLayout(grid_widget)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        col1 = QVBoxLayout()
        col2 = QVBoxLayout()
        col1.setSpacing(6); col2.setSpacing(6)
        for i, (name, cfg) in enumerate(PRESETS.items()):
            card = PresetCard(name, cfg["hint"])
            card.clicked.connect(lambda _=False, n=name: self._apply_preset(n))
            self._preset_cards[name] = card
            (col1 if i % 2 == 0 else col2).addWidget(card)
        grid.addLayout(col1, stretch=1)
        grid.addLayout(col2, stretch=1)
        section_inner_layout.addWidget(grid_widget)
        layout.addWidget(section)

        # Frame & size section ----------------------------------------
        section, inner = self._make_section("Frame & size")
        self.fps_stepper = Stepper(minimum=5, maximum=60, value=12)
        self.fps_stepper.valueChanged.connect(lambda _v: self._on_setting_changed())
        inner.addLayout(self._row("FPS", self.fps_stepper))

        # Scale slider with value badge.
        self.scale_slider = CoveSlider(minimum=10, maximum=100, value=50)
        self.scale_slider.valueChanged.connect(lambda _v: self._on_scale_changed())
        self._scale_value_lbl = QLabel("50%")
        self._scale_value_lbl.setStyleSheet(
            f"color: {theme.TEXT_DIM}; min-width: 36px;"
            f" font-family: '{theme.FONT_MONO}', monospace; font-size: 11px;"
        )
        self._scale_value_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        inner.addLayout(self._row("Scale", self.scale_slider, self._scale_value_lbl, fill=True))

        self.palette_seg = Segmented([
            (64, "64"), (128, "128"), (192, "192"), (256, "256"),
        ], active=128)
        self.palette_seg.activeChanged.connect(lambda _v: self._on_setting_changed())
        inner.addLayout(self._row("Palette", self.palette_seg))
        layout.addWidget(section)

        # Playback section --------------------------------------------
        section, inner = self._make_section("Playback")
        self.speed_seg = Segmented([
            (0.5, "½×"), (1.0, "1×"), (1.5, "1½×"), (2.0, "2×"),
        ], active=1.0)
        self.speed_seg.activeChanged.connect(lambda _v: self._on_setting_changed())
        inner.addLayout(self._row("Speed", self.speed_seg))

        # Loop: Once = -1, 3x = 2, ∞ = 0
        self.loop_seg = Segmented([
            (-1, "Once"), (2, "3×"), (0, "∞"),
        ], active=0)
        self.loop_seg.activeChanged.connect(lambda _v: self._on_setting_changed())
        inner.addLayout(self._row("Loop", self.loop_seg))
        layout.addWidget(section)

        # Encoding section --------------------------------------------
        section, inner = self._make_section("Encoding")
        self.format_seg = Segmented([("GIF", "GIF"), ("WebP", "WebP")], active="WebP")
        self.format_seg.activeChanged.connect(self._on_format_changed)
        inner.addLayout(self._row("Format", self.format_seg))

        self.webp_quality = CoveSlider(minimum=1, maximum=100, value=80)
        self.webp_quality.setEnabled(False)
        self.webp_quality.valueChanged.connect(lambda _v: self._on_quality_changed())
        self._quality_value_lbl = QLabel("80")
        self._quality_value_lbl.setStyleSheet(
            f"color: {theme.TEXT_DIM}; min-width: 28px;"
            f" font-family: '{theme.FONT_MONO}', monospace; font-size: 11px;"
        )
        self._quality_value_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        inner.addLayout(self._row("Quality", self.webp_quality, self._quality_value_lbl, fill=True))
        layout.addWidget(section)

        layout.addStretch(1)
        return scroll

    def _build_tab_effects(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 4, 0, 110)
        layout.setSpacing(0)

        section, inner = self._make_section("Time effects")
        # Boomerang
        boom_icon = QLabel("⤺")
        boom_icon.setStyleSheet("background: transparent;")
        self.boomerang_row = EffectRow(
            icon=boom_icon, title="Boomerang",
            desc="forward, then reverse",
        )
        self.boomerang_row.toggled.connect(lambda _on: self._on_setting_changed())
        inner.addWidget(self.boomerang_row)

        # Reverse
        rev_icon = QLabel("◂◂")
        rev_icon.setStyleSheet("background: transparent;")
        self.reverse_row = EffectRow(
            icon=rev_icon, title="Reverse playback",
            desc="play end → start",
        )
        self.reverse_row.toggled.connect(lambda _on: self._on_setting_changed())
        inner.addWidget(self.reverse_row)
        layout.addWidget(section)

        layout.addStretch(1)
        return scroll

    def _build_tab_caption(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 4, 0, 110)
        layout.setSpacing(0)

        # First caption ------------------------------------------------
        # Use a custom action button "+ Add" instead of the default Reset.
        section, inner = self._make_section_with_actions(
            "Text overlay",
            actions=[("+ Add", self._on_add_caption_clicked, "add"),
                     ("Clear", lambda: self.caption_edit.setText(""), "clear")],
        )
        self._caption_add_btn = section.property("action_add")

        self.caption_edit = QLineEdit()
        self.caption_edit.setPlaceholderText("e.g. WOAH, SAME, lol no")
        self.caption_edit.setStyleSheet(self._caption_edit_qss())
        self.caption_edit.textChanged.connect(self._on_caption_text_changed)
        inner.addWidget(self.caption_edit)

        color_row = QHBoxLayout()
        color_row.setSpacing(8)
        self.caption_text_color = ColorButton("#FFFFFF", "Text")
        self.caption_outline_color = ColorButton("#000000", "Outline")
        self.caption_text_color.colorChanged.connect(self._on_caption_style_changed)
        self.caption_outline_color.colorChanged.connect(self._on_caption_style_changed)
        color_row.addWidget(self.caption_text_color, stretch=1)
        color_row.addWidget(self.caption_outline_color, stretch=1)
        inner.addSpacing(8)
        inner.addLayout(color_row)

        self.caption_drag_hint = QLabel(
            "Drag to move · corner to resize · bubble to rotate."
        )
        self.caption_drag_hint.setStyleSheet(
            f"color: {theme.TEXT_FAINT}; font-size: 11px;"
        )
        self.caption_drag_hint.setWordWrap(True)
        inner.addSpacing(8)
        inner.addWidget(self.caption_drag_hint)
        layout.addWidget(section)

        # Second caption (hidden until + is clicked) -------------------
        self._caption2_section, c2_inner = self._make_section_with_actions(
            "Second text",
            actions=[("× Remove", self._on_remove_caption_2, "remove")],
        )
        self.caption_edit_2 = QLineEdit()
        self.caption_edit_2.setPlaceholderText("…")
        self.caption_edit_2.setStyleSheet(self._caption_edit_qss())
        self.caption_edit_2.textChanged.connect(self._on_caption2_text_changed)
        c2_inner.addWidget(self.caption_edit_2)

        c2_color_row = QHBoxLayout()
        c2_color_row.setSpacing(8)
        self.caption_text_color_2 = ColorButton("#FFFFFF", "Text")
        self.caption_outline_color_2 = ColorButton("#000000", "Outline")
        self.caption_text_color_2.colorChanged.connect(self._on_caption2_style_changed)
        self.caption_outline_color_2.colorChanged.connect(self._on_caption2_style_changed)
        c2_color_row.addWidget(self.caption_text_color_2, stretch=1)
        c2_color_row.addWidget(self.caption_outline_color_2, stretch=1)
        c2_inner.addSpacing(8)
        c2_inner.addLayout(c2_color_row)

        layout.addWidget(self._caption2_section)
        self._caption2_section.setVisible(False)

        layout.addStretch(1)
        return scroll

    def _caption_edit_qss(self) -> str:
        return (
            f"QLineEdit {{ background: {theme.SURFACE}; color: {theme.TEXT};"
            f" border: 1px solid {theme.BORDER};"
            f" border-radius: {theme.RADIUS_SM}px;"
            f" padding: 10px 12px; font-size: 13px; }}"
            f"QLineEdit:focus {{ outline: none; border-color: {theme.ACCENT_RING};"
            f" background: {theme.SURFACE_2}; }}"
        )

    # -----------------------------------------------------------------
    # Rail footer — estimated size + Convert/Cancel + StatusLine
    # -----------------------------------------------------------------

    def _build_rail_footer(self) -> QWidget:
        wrap = QWidget()
        wrap.setStyleSheet(
            f"background: {theme.BG_2};"
            f" border-top: 1px solid {theme.BORDER};"
        )
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(18, 14, 18, 18)
        layout.setSpacing(10)

        # Estimated size card.
        est_card = QFrame()
        est_card.setStyleSheet(
            f"QFrame {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER};"
            f" border-radius: {theme.RADIUS_SM}px; }}"
            f"QLabel {{ background: transparent; border: none; }}"
        )
        est_layout = QHBoxLayout(est_card)
        est_layout.setContentsMargins(12, 10, 12, 10)
        est_layout.setSpacing(8)

        lhs = QVBoxLayout(); lhs.setSpacing(2)
        l1 = QLabel("Estimated size")
        l1.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 11px;")
        self._est_dim = QLabel("—")
        self._est_dim.setStyleSheet(
            f"color: {theme.TEXT_FAINTER};"
            f" font-family: '{theme.FONT_MONO}', monospace; font-size: 10.5px;"
        )
        lhs.addWidget(l1)
        lhs.addWidget(self._est_dim)
        est_layout.addLayout(lhs)

        est_layout.addStretch(1)

        rhs = QVBoxLayout(); rhs.setSpacing(2)
        rhs.setAlignment(Qt.AlignRight)
        self._est_size = QLabel("—")
        self._est_size.setStyleSheet(
            f"color: {theme.ACCENT}; font-size: 17px; font-weight: 600;"
            f" font-family: '{theme.FONT_MONO}', monospace;"
        )
        self._est_size.setAlignment(Qt.AlignRight)
        self._est_kb = QLabel("")
        self._est_kb.setStyleSheet(
            f"color: {theme.TEXT_FAINT};"
            f" font-family: '{theme.FONT_MONO}', monospace; font-size: 10px;"
        )
        self._est_kb.setAlignment(Qt.AlignRight)
        rhs.addWidget(self._est_size)
        rhs.addWidget(self._est_kb)
        est_layout.addLayout(rhs)

        layout.addWidget(est_card)

        # Convert + cancel-square row.
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.convert_btn = QPushButton("Convert to WebP")
        self.convert_btn.setCursor(Qt.PointingHandCursor)
        self.convert_btn.setMinimumHeight(40)
        self.convert_btn.setStyleSheet(self._convert_btn_qss(active=True))
        self.convert_btn.clicked.connect(self._on_convert_clicked)
        actions.addWidget(self.convert_btn, stretch=1)

        self.cancel_btn = QPushButton("×")
        self.cancel_btn.setFixedSize(40, 40)
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setStyleSheet(
            f"QPushButton {{ background: {theme.SURFACE}; color: {theme.TEXT_DIM};"
            f" border: 1px solid {theme.BORDER};"
            f" border-radius: 9px; font-size: 16px; }}"
            f"QPushButton:hover {{ background: {theme.SURFACE_2}; color: {theme.TEXT};"
            f" border-color: {theme.BORDER_HARD}; }}"
            f"QPushButton:disabled {{ color: {theme.TEXT_FAINTER}; }}"
        )
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        actions.addWidget(self.cancel_btn)
        layout.addLayout(actions)

        # Thin progress bar (only visible while running).
        self.progress = QFrame()
        self.progress.setFixedHeight(4)
        self.progress.setStyleSheet(
            f"QFrame {{ background: {theme.SURFACE_3}; border-radius: 2px; }}"
        )
        self._progress_fill = QWidget(self.progress)
        self._progress_fill.setStyleSheet(
            f"background: {theme.ACCENT}; border-radius: 2px;"
        )
        self._progress_fill.setGeometry(0, 0, 0, 4)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        # Status line.
        self.status_line = StatusLine()
        layout.addWidget(self.status_line)

        # Result chip — shown after conversion.
        self.result_chip = ResultChip()
        layout.addWidget(self.result_chip)
        return wrap

    def _convert_btn_qss(self, *, active: bool) -> str:
        if active:
            return (
                f"QPushButton {{ background: {theme.ACCENT}; color: #ffffff;"
                f" border: 1px solid rgba(255,255,255,0.18);"
                f" border-radius: 9px; font-size: 13.5px; font-weight: 600;"
                f" padding: 0 14px; }}"
                f"QPushButton:hover {{ background: {theme.ACCENT_2}; }}"
                f"QPushButton:pressed {{ background: #44d4be; }}"
                f"QPushButton:disabled {{ color: {theme.TEXT_FAINT};"
                f" background: {theme.SURFACE_3}; border-color: {theme.BORDER}; }}"
            )
        # Disabled-during-running state.
        return (
            f"QPushButton {{ background: {theme.SURFACE_3}; color: {theme.TEXT_FAINT};"
            f" border: 1px solid {theme.BORDER}; border-radius: 9px;"
            f" font-size: 13.5px; padding: 0 14px; }}"
        )

    # -----------------------------------------------------------------
    # Section helpers
    # -----------------------------------------------------------------

    def _make_section(self, title: str, *, reset_label: str = "") -> tuple[QWidget, QVBoxLayout]:
        section = self._make_section_widget(title, reset_label=reset_label)
        return section, section.property("layout")

    def _make_section_with_actions(
        self, title: str,
        *, actions: list[tuple[str, "Callable", str]],
    ) -> tuple[QWidget, QVBoxLayout]:
        """Section with multiple action buttons in the header instead of
        a single Reset link. `actions` is a list of (label, handler, key)
        tuples; the resulting button is exposed via section.property(f"action_{key}")
        so the caller can toggle visibility."""
        section = QWidget()
        section.setStyleSheet(
            f"QWidget {{ background: transparent; border-bottom: 1px solid {theme.BORDER}; }}"
        )
        outer = QVBoxLayout(section)
        outer.setContentsMargins(18, 14, 18, 16)
        outer.setSpacing(10)

        head_row = QHBoxLayout()
        head_row.setSpacing(8)
        head_lbl = QLabel(title)
        head_lbl.setProperty("role", "rail-section")
        head_row.addWidget(head_lbl)
        head_row.addStretch(1)
        for label, handler, key in actions:
            btn = QPushButton(label)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {theme.TEXT_FAINTER};"
                f" border: none; padding: 0;"
                f" font-family: '{theme.FONT_MONO}', monospace;"
                f" font-size: 10.5px; }}"
                f"QPushButton:hover {{ color: {theme.ACCENT}; }}"
            )
            btn.clicked.connect(handler)
            section.setProperty(f"action_{key}", btn)
            head_row.addWidget(btn)
        outer.addLayout(head_row)

        inner_layout = QVBoxLayout()
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(6)
        outer.addLayout(inner_layout)

        section.setProperty("layout", inner_layout)
        return section, inner_layout

    def _make_section_widget(self, title: str, *, reset_label: str = "") -> QWidget:
        section = QWidget()
        section.setStyleSheet(
            f"QWidget {{ background: transparent; border-bottom: 1px solid {theme.BORDER}; }}"
        )
        outer = QVBoxLayout(section)
        outer.setContentsMargins(18, 14, 18, 16)
        outer.setSpacing(10)

        head_row = QHBoxLayout()
        head_row.setSpacing(8)
        head_lbl = QLabel(title)
        head_lbl.setProperty("role", "rail-section")
        head_row.addWidget(head_lbl)
        head_row.addStretch(1)
        if reset_label:
            reset_btn = QPushButton(reset_label)
            reset_btn.setCursor(Qt.PointingHandCursor)
            reset_btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {theme.TEXT_FAINTER};"
                f" border: none; padding: 0;"
                f" font-family: '{theme.FONT_MONO}', monospace; font-size: 10.5px; }}"
                f"QPushButton:hover {{ color: {theme.ACCENT}; }}"
            )
            head_row.addWidget(reset_btn)
            section.setProperty("reset_btn", reset_btn)
        outer.addLayout(head_row)

        # Inner container — caller adds to this.
        inner_layout = QVBoxLayout()
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(6)
        outer.addLayout(inner_layout)

        section.setProperty("layout", inner_layout)
        return section

    def _row(self, label: str, *widgets: QWidget, fill: bool = False) -> QHBoxLayout:
        """Build a label + value-controls row.

        - When `fill=False` (default): widgets sit right-aligned at their
          natural size, with empty space between them and the label.
        - When `fill=True`: the FIRST widget gets stretch=1 so it fills the
          remaining row width (use this for sliders that should occupy the
          row).
        """
        row = QHBoxLayout()
        row.setSpacing(12)
        lbl = QLabel(label)
        lbl.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 12px; min-width: 70px;")
        row.addWidget(lbl)
        if fill:
            for i, w in enumerate(widgets):
                row.addWidget(w, stretch=(1 if i == 0 else 0))
        else:
            row.addStretch(1)
            for w in widgets:
                row.addWidget(w)
        return row

    # -----------------------------------------------------------------
    # File loading
    # -----------------------------------------------------------------

    def _on_drop_frame_clicked(self) -> None:
        if self._video_path is None:
            self._open_file_dialog()

    def _open_file_dialog(self) -> None:
        videos_dir = QStandardPaths.writableLocation(QStandardPaths.MoviesLocation)
        path, _ = QFileDialog.getOpenFileName(
            self, "Open video", videos_dir, VIDEO_FILTERS,
        )
        if path:
            self._load_video(Path(path))

    def _on_file_dropped(self, path: str) -> None:
        self._load_video(Path(path))

    def _on_files_dropped(self, paths: list[str]) -> None:
        first, rest = paths[0], paths[1:]
        self._load_video(Path(first))
        if rest:
            self._batch.add([Path(p) for p in rest])

    def _on_clear_video(self) -> None:
        # Hard reset to placeholder state.
        self._video_path = None
        self._info = None
        self.video_container.hide()
        self.placeholder.show()
        self.drop_frame.set_loaded_style(False)
        self.player.setSource(QUrl())
        self.trim_bar.clear()
        self._file_pill.set_name("")
        self._no_file_lbl.setVisible(True)
        for kv in (self._kv_source, self._kv_duration, self._kv_fps):
            kv.set_value("—")
        self._pulse.set_state("idle")
        self._update_controls_enabled()
        self._refresh_size_estimate()

    def _load_video(self, path: Path) -> None:
        try:
            info = ff.probe(path)
        except ff.FFmpegMissingError as exc:
            QMessageBox.critical(self, "Missing dependency", str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Could not open video", str(exc))
            return

        self._video_path = path
        self._info = info
        self.placeholder.hide()
        self.video_container.show()
        self.video_view.setGeometry(self.video_container.rect())
        self.crop_overlay.setGeometry(self.video_container.rect())
        self.video_view.set_native_size(info.width, info.height)
        aspect = info.width / info.height
        self.crop_overlay.set_video_aspect(aspect)
        self.crop_overlay.set_normalized_rect(QRectF(0, 0, 1, 1))
        self.caption_overlay.set_video_aspect(aspect)
        self.caption_overlay.setGeometry(self.video_container.rect())
        self.caption_overlay.raise_()
        self.caption_overlay_2.set_video_aspect(aspect)
        self.caption_overlay_2.setGeometry(self.video_container.rect())
        self.caption_overlay_2.raise_()
        self._tool = "trim"
        self._tool_seg.set_active("trim")
        self.crop_overlay.setVisible(False)
        self.crop_reset_btn.setVisible(False)
        self.drop_frame.set_loaded_style(True)
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.trim_bar.set_duration(info.duration)
        self._update_range_label()
        # Stage-head meta.
        self._file_pill.set_name(path.name)
        self._no_file_lbl.setVisible(False)
        self._kv_source.set_value(f"{info.width}×{info.height}")
        self._kv_duration.set_value(f"{info.duration:.2f}s")
        self._kv_fps.set_value(f"{info.fps:.1f}")
        self._pulse.set_state("running")
        QTimer.singleShot(800, lambda: self._pulse.set_state("idle"))
        self.status.showMessage(
            f"{path.name}  ·  {info.width}×{info.height} @ {info.fps:.2f}fps  ·  "
            f"{info.duration:.2f}s"
        )
        self._kick_off_thumbs(path, info.duration)
        self._update_controls_enabled()
        self._refresh_size_estimate()
        prefs.push_recent(path)
        self.recent_row.refresh()

    def _kick_off_thumbs(self, path: Path, duration: float) -> None:
        if self._thumbs_thread:
            try:
                self._thumbs_worker.cancel()
            except Exception:  # noqa: BLE001
                pass
        thread, worker = start_thumbnails(path, duration, count=14)
        worker.finished.connect(self.trim_bar.set_thumbnails, Qt.QueuedConnection)
        worker.failed.connect(self._on_thumb_error, Qt.QueuedConnection)
        self._thumbs_thread = thread
        self._thumbs_worker = worker
        thread.start()

    def _on_thumb_error(self, msg: str) -> None:
        self.status.showMessage(f"Thumbnail error: {msg}", 5000)

    # -----------------------------------------------------------------
    # Player / playhead / trim
    # -----------------------------------------------------------------

    def _toggle_play(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            self.play_btn.setText("▶")
        else:
            self.player.play()
            self.play_btn.setText("‖")

    def _on_space_pressed(self) -> None:
        if self.caption_edit.hasFocus():
            self.caption_edit.insert(" ")
            return
        if self._info is None:
            return
        self._toggle_play()

    def _on_player_position(self, ms: int) -> None:
        if not self._info:
            return
        t = ms / 1000.0
        self.trim_bar.set_playhead(t)
        if t > self.trim_bar.end() + 0.05:
            self.player.pause()
            self.player.setPosition(int(self.trim_bar.start() * 1000))
            self.play_btn.setText("▶")

    def _on_media_status(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.LoadedMedia and self._info is None:
            self.trim_bar.set_duration(self.player.duration() / 1000.0)

    def _on_playhead_moved(self, t: float) -> None:
        self.player.setPosition(int(t * 1000))

    def _on_range_changed(self, start: float, end: float) -> None:
        self._update_range_label()
        self._refresh_size_estimate()

    def _on_range_previewing(self, start: float, end: float) -> None:
        self._update_range_label()
        self._refresh_size_estimate()

    def _update_range_label(self) -> None:
        if not self._info:
            self.range_label.setText("—")
            return
        s = self.trim_bar.start()
        e = self.trim_bar.end()
        self.range_label.setText(f"{_fmt(s)}  →  {_fmt(e)}   ·   {_fmt(e - s)}")

    def _set_in_at_playhead(self) -> None:
        if not self._info:
            return
        t = self.player.position() / 1000.0
        if t < self.trim_bar.end() - 0.05:
            self.trim_bar._start = t  # noqa: SLF001
            self.trim_bar.update()
            self.trim_bar.rangeChanged.emit(self.trim_bar.start(), self.trim_bar.end())

    def _set_out_at_playhead(self) -> None:
        if not self._info:
            return
        t = self.player.position() / 1000.0
        if t > self.trim_bar.start() + 0.05:
            self.trim_bar._end = t  # noqa: SLF001
            self.trim_bar.update()
            self.trim_bar.rangeChanged.emit(self.trim_bar.start(), self.trim_bar.end())

    # -----------------------------------------------------------------
    # Tabs / tool
    # -----------------------------------------------------------------

    def _on_tab_changed(self, name: str) -> None:
        idx = {"output": 0, "effects": 1, "caption": 2}.get(name, 0)
        self._tab_stack.setCurrentIndex(idx)

    def _on_tool_changed(self, tool: str) -> None:
        self._tool = tool
        if tool == "crop":
            self._on_crop_toggled(True)
        else:
            self._on_crop_toggled(False)

    # -----------------------------------------------------------------
    # Presets
    # -----------------------------------------------------------------

    def _apply_preset(self, name: str) -> None:
        cfg = PRESETS.get(name)
        if not cfg:
            return
        self._suppress_preset_reset = True
        try:
            self.fps_stepper.set_value(cfg["fps"])
            self.scale_slider.setValue(cfg["scale"])
            self.palette_seg.set_active(cfg["palette"])
            self.format_seg.set_active(cfg["fmt"])
            self.webp_quality.setValue(cfg["webp_q"])
        finally:
            self._suppress_preset_reset = False
        self._active_preset = name
        for n, card in self._preset_cards.items():
            card.set_active(n == name)
        self._refresh_size_estimate()

    def _on_setting_changed(self) -> None:
        if not self._suppress_preset_reset and self._active_preset is not None:
            self._active_preset = None
            for card in self._preset_cards.values():
                card.set_active(False)
        self._refresh_size_estimate()

    def _on_scale_changed(self) -> None:
        self._scale_value_lbl.setText(f"{self.scale_slider.value()}%")
        self._on_setting_changed()

    def _on_quality_changed(self) -> None:
        self._quality_value_lbl.setText(str(self.webp_quality.value()))
        self._on_setting_changed()

    # -----------------------------------------------------------------
    # Format
    # -----------------------------------------------------------------

    def _on_format_changed(self, fmt: str) -> None:
        is_webp = fmt == "WebP"
        self.webp_quality.setEnabled(is_webp)
        self._quality_value_lbl.setEnabled(is_webp)
        self.palette_seg.setEnabled(fmt == "GIF")
        # Update Convert button label.
        self.convert_btn.setText(f"Convert to {fmt}")
        self._on_setting_changed()

    # -----------------------------------------------------------------
    # Caption
    # -----------------------------------------------------------------

    def _build_captions(self) -> list[ff.Caption]:
        """Collect non-empty captions in display order. Capped at 2."""
        out: list[ff.Caption] = []
        t1 = self.caption_edit.text().strip()
        if t1:
            cx, cy = self.caption_overlay.normalized_position()
            out.append(ff.Caption(
                text=t1,
                pos_x=cx, pos_y=cy,
                color=self.caption_text_color.hex(),
                outline=self.caption_outline_color.hex(),
                size_pct=self.caption_overlay.size_pct(),
                rotation_deg=self.caption_overlay.rotation_deg(),
            ))
        if self._caption2_visible:
            t2 = self.caption_edit_2.text().strip()
            if t2:
                cx, cy = self.caption_overlay_2.normalized_position()
                out.append(ff.Caption(
                    text=t2,
                    pos_x=cx, pos_y=cy,
                    color=self.caption_text_color_2.hex(),
                    outline=self.caption_outline_color_2.hex(),
                    size_pct=self.caption_overlay_2.size_pct(),
                    rotation_deg=self.caption_overlay_2.rotation_deg(),
                ))
        return out

    def _current_caption_style(self) -> CaptionStyle:
        return CaptionStyle(
            text=self.caption_edit.text(),
            color=self.caption_text_color.hex(),
            outline=self.caption_outline_color.hex(),
            size_pct=self.caption_overlay.size_pct(),
            rotation_deg=self.caption_overlay.rotation_deg(),
        )

    def _caption2_style(self) -> CaptionStyle:
        return CaptionStyle(
            text=self.caption_edit_2.text(),
            color=self.caption_text_color_2.hex(),
            outline=self.caption_outline_color_2.hex(),
            size_pct=self.caption_overlay_2.size_pct(),
            rotation_deg=self.caption_overlay_2.rotation_deg(),
        )

    def _on_caption_text_changed(self, _text: str) -> None:
        self.caption_overlay.set_style(self._current_caption_style())
        self.caption_overlay.raise_()
        self._on_setting_changed()

    def _on_caption_style_changed(self, _hex: str) -> None:
        self.caption_overlay.set_style(self._current_caption_style())
        self._on_setting_changed()

    def _on_caption2_text_changed(self, _text: str) -> None:
        self.caption_overlay_2.set_style(self._caption2_style())
        self.caption_overlay_2.raise_()
        self._on_setting_changed()

    def _on_caption2_style_changed(self, _hex: str) -> None:
        self.caption_overlay_2.set_style(self._caption2_style())
        self._on_setting_changed()

    def _on_caption_position_changed(self, _cx: float, _cy: float) -> None:
        pass

    def _on_caption_size_changed(self, _size_pct: float) -> None:
        self._on_setting_changed()

    def _on_caption_rotation_changed(self, _deg: float) -> None:
        pass

    # --- Add / remove second caption ---------------------------------

    def _on_add_caption_clicked(self) -> None:
        if self._caption2_visible:
            return
        self._caption2_visible = True
        self._caption2_section.setVisible(True)
        if self._caption_add_btn is not None:
            self._caption_add_btn.setVisible(False)
        # Default the second caption to top-center so it doesn't sit on
        # top of the first (which lives at bottom-center by default).
        self.caption_overlay_2.set_normalized_position(0.5, 0.15)
        self.caption_overlay_2.set_style(self._caption2_style())
        self.caption_overlay_2.raise_()

    def _on_remove_caption_2(self) -> None:
        self._caption2_visible = False
        self._caption2_section.setVisible(False)
        if self._caption_add_btn is not None:
            self._caption_add_btn.setVisible(True)
        # Clear the second caption so the overlay disappears + the build
        # step won't include it in the output PNG.
        self.caption_edit_2.clear()
        self.caption_overlay_2.set_style(CaptionStyle())
        self._on_setting_changed()

    # -----------------------------------------------------------------
    # Size estimation
    # -----------------------------------------------------------------

    def _refresh_size_estimate(self) -> None:
        if not self._info:
            self._est_size.setText("—")
            self._est_kb.setText("")
            self._est_dim.setText("—")
            return
        kb = self._estimate_size_kb()
        if kb is None:
            self._est_size.setText("—")
            self._est_kb.setText("")
        else:
            if kb < 1024:
                self._est_size.setText(f"~{kb:.0f} KB")
                self._est_kb.setText("")
            else:
                self._est_size.setText(f"~{kb / 1024:.1f} MB")
                self._est_kb.setText(f"~{kb:.0f} KB")
        # Output dimensions for the dim sub-line.
        crop = self._crop_pixels()
        if crop is not None:
            sw, sh = crop[2], crop[3]
        else:
            sw, sh = self._info.width, self._info.height
        scale = max(1, int(self.scale_slider.value())) / 100.0
        w = max(2, int(sw * scale) // 2 * 2)
        h = max(2, int(sh * scale) // 2 * 2)
        flags = []
        if self.boomerang_row.is_checked():
            flags.append("↻")
        flags_text = (" · " + " ".join(flags)) if flags else ""
        self._est_dim.setText(f"{w}×{h} · {self.format_seg.active()}{flags_text}")

    def _estimate_size_kb(self) -> float | None:
        if not self._info:
            return None
        start = self.trim_bar.start()
        end = self.trim_bar.end()
        clip_seconds = max(0.0, end - start)
        if clip_seconds <= 0:
            return 0.0

        speed = float(self.speed_seg.active() or 1.0)
        out_seconds = clip_seconds / max(0.1, speed)
        if self.boomerang_row.is_checked():
            out_seconds *= 2.0
        fps = max(1, int(self.fps_stepper.value()))
        frames = max(1, int(round(out_seconds * fps)))

        crop = self._crop_pixels()
        if crop is not None:
            src_w, src_h = crop[2], crop[3]
        else:
            src_w, src_h = self._info.width, self._info.height
        scale = max(1, int(self.scale_slider.value())) / 100.0
        w = max(2, int(src_w * scale) // 2 * 2)
        h = max(2, int(src_h * scale) // 2 * 2)

        fmt = self.format_seg.active()
        if fmt == "GIF":
            colors = int(self.palette_seg.active() or 128)
            bits = max(1, math.ceil(math.log2(max(2, colors))))
            lzw = 2.6
            bytes_total = frames * w * h * bits / 8.0 / lzw
        else:
            q = max(1, self.webp_quality.value()) / 100.0
            bits_first = q * 0.25
            inter_ratio = 0.45
            first = w * h * bits_first / 8.0
            bytes_total = first + (frames - 1) * first * inter_ratio
        return bytes_total / 1024.0

    # -----------------------------------------------------------------
    # Conversion
    # -----------------------------------------------------------------

    def _on_convert_clicked(self) -> None:
        if not self._video_path or not self._info:
            return
        fmt = self.format_seg.active().lower()
        ext = "gif" if fmt == "gif" else "webp"
        suggested = self._suggested_output_path(self._video_path, ext)
        chosen, _ = QFileDialog.getSaveFileName(
            self, f"Save {ext.upper()}", str(suggested),
            f"{ext.upper()} (*.{ext});;All files (*)",
        )
        if not chosen:
            return
        self._launch_job(self._video_path, Path(chosen), primary=True)

    def _suggested_output_path(self, source: Path, ext: str) -> Path:
        out_dir = prefs.output_dir()
        target_dir = Path(out_dir) if out_dir and Path(out_dir).is_dir() else source.parent
        return target_dir / f"{source.stem}.{ext}"

    def _launch_job(self, source: Path, out_path: Path, *, primary: bool) -> None:
        loop_value = self.loop_seg.active()
        source_w = source_h = 0
        if primary and self._info is not None:
            source_w, source_h = self._info.width, self._info.height
        job = ConvertJob(
            video=source,
            output=out_path,
            start=self.trim_bar.start() if primary else 0.0,
            end=self.trim_bar.end() if primary else 0.0,
            fps=int(self.fps_stepper.value()),
            scale_pct=int(self.scale_slider.value()),
            speed=float(self.speed_seg.active()),
            palette_colors=int(self.palette_seg.active()),
            loop=int(loop_value),
            fmt=self.format_seg.active().lower(),
            webp_quality=self.webp_quality.value(),
            crop=self._crop_pixels() if primary else None,
            reverse=self.reverse_row.is_checked(),
            boomerang=self.boomerang_row.is_checked(),
            captions=self._build_captions(),
            source_width=source_w,
            source_height=source_h,
        )

        if not primary:
            try:
                info = ff.probe(source)
                job.end = info.duration
                job.source_width = info.width
                job.source_height = info.height
            except Exception as exc:  # noqa: BLE001
                self.status_line.set_failed(f"Skipping {source.name}: {exc}")
                return

        self._last_progress = 0
        self._last_eta = None
        self._set_progress_pct(0)
        self.progress.setVisible(True)
        self.convert_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self._pulse.set_state("running")
        prefix = "Converting" if primary else f"queue · {source.name}"
        self.status_line.set_running(f"{prefix} · 0%")

        thread, worker = start_conversion(job)
        worker.progress.connect(self._on_progress, Qt.QueuedConnection)
        worker.eta.connect(self._on_eta, Qt.QueuedConnection)
        worker.log.connect(self._on_worker_log, Qt.QueuedConnection)
        worker.finished.connect(self._on_conversion_done, Qt.QueuedConnection)
        worker.failed.connect(self._on_conversion_failed, Qt.QueuedConnection)
        thread.finished.connect(self._reset_after_conversion)
        self._convert_thread = thread
        self._convert_worker = worker
        thread.start()

    def _on_cancel_clicked(self) -> None:
        if self._convert_worker:
            self._convert_worker.cancel()
            self.status_line.set_running("Cancelling…")

    def _on_worker_log(self, _msg: str) -> None:
        # Quietly absorb log lines — we surface aggregate progress in the
        # status line, not raw ffmpeg invocation strings.
        pass

    def _on_progress(self, pct: int) -> None:
        self._last_progress = max(self._last_progress, pct)
        self._set_progress_pct(self._last_progress)
        self._refresh_status_line()

    def _on_eta(self, seconds: float) -> None:
        self._last_eta = seconds
        self._refresh_status_line()

    def _refresh_status_line(self) -> None:
        if self._last_progress >= 99 or self._last_eta is None:
            self.status_line.set_running(f"encoding · {self._last_progress}%")
        else:
            secs = max(0, int(round(self._last_eta)))
            m, s = divmod(secs, 60)
            self.status_line.set_running(
                f"encoding · {self._last_progress}% · ETA {m}:{s:02d}"
            )

    def _on_conversion_done(self, out: Path) -> None:
        size_kb = out.stat().st_size / 1024
        unit = "KB" if size_kb < 1024 else "MB"
        size = size_kb if unit == "KB" else size_kb / 1024
        self._set_progress_pct(100)
        self._pulse.set_state("done")
        self.status_line.set_done(f"complete · {out.name} ({size:.1f} {unit})")
        self.result_chip.show_result(out)
        self.progress.setVisible(False)

        if self._batch_active_idx is not None:
            self._batch.mark(self._batch_active_idx, QueueStatus.DONE, output=out)
            self._batch_active_idx = None
        QTimer.singleShot(50, self._maybe_run_next_batch_item)

    def _on_conversion_failed(self, msg: str) -> None:
        self._pulse.set_state("idle")
        self.status_line.set_failed(f"failed · {msg[:80]}")
        self.progress.setVisible(False)
        if self._batch_active_idx is not None:
            self._batch.mark(self._batch_active_idx, QueueStatus.FAILED, error=msg)
            self._batch_active_idx = None
            QTimer.singleShot(50, self._maybe_run_next_batch_item)
        else:
            QMessageBox.warning(self, "Conversion failed", msg)

    def _reset_after_conversion(self) -> None:
        self._convert_thread = None
        self._convert_worker = None
        self.convert_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)

    def _set_progress_pct(self, pct: int) -> None:
        if not self.progress.isVisible():
            return
        w = max(0, min(self.progress.width(), int(self.progress.width() * pct / 100)))
        self._progress_fill.setGeometry(0, 0, w, self.progress.height())

    def _maybe_run_next_batch_item(self) -> None:
        nxt = self._batch.next_pending()
        if not nxt:
            if not self._batch.is_empty():
                self.status_line.set_done("Batch complete.")
            return
        idx, item = nxt
        self._batch_active_idx = idx
        self._batch.mark(idx, QueueStatus.ACTIVE)
        ext = self.format_seg.active().lower()
        out_path = self._suggested_output_path(item.path, ext)
        base = out_path
        n = 1
        while out_path.exists():
            n += 1
            out_path = base.with_name(f"{base.stem}-{n}{base.suffix}")
        self._launch_job(item.path, out_path, primary=False)

    # -----------------------------------------------------------------
    # Batch
    # -----------------------------------------------------------------

    def _on_batch_changed(self) -> None:
        self.batch_panel.setVisible(not self._batch.is_empty())

    # -----------------------------------------------------------------
    # Misc
    # -----------------------------------------------------------------

    def _update_controls_enabled(self) -> None:
        loaded = self._info is not None
        for w in (
            self.play_btn, self.set_in_btn, self.set_out_btn, self._tool_seg,
            self.fps_stepper, self.scale_slider, self.palette_seg,
            self.speed_seg, self.loop_seg, self.format_seg,
            self.convert_btn,
            self.boomerang_row, self.reverse_row,
            self.caption_edit, self.caption_text_color, self.caption_outline_color,
        ):
            w.setEnabled(loaded)
        # Preset cards always selectable as long as the app is alive — they
        # reset settings even when no clip is loaded so the user can preview
        # what each preset implies.
        for card in self._preset_cards.values():
            card.setEnabled(True)
        is_webp = self.format_seg.active() == "WebP"
        self.webp_quality.setEnabled(loaded and is_webp)
        self._quality_value_lbl.setEnabled(loaded and is_webp)

    # -----------------------------------------------------------------
    # Drag & drop bubbling
    # -----------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls() and any(
            u.toLocalFile() for u in event.mimeData().urls()
        ):
            event.acceptProposedAction()
            theme.apply_drop_style(self.drop_frame, loaded=False, hover=True)

    def dragMoveEvent(self, event) -> None:  # noqa: ANN001
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragLeaveEvent(self, _event) -> None:  # noqa: ANN001
        self.drop_frame.set_loaded_style(self._video_path is not None)

    def dropEvent(self, event: QDropEvent) -> None:
        self.drop_frame.set_loaded_style(self._video_path is not None)
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.toLocalFile()]
        if not paths:
            return
        event.acceptProposedAction()
        if len(paths) == 1:
            self._load_video(Path(paths[0]))
        else:
            self._on_files_dropped(paths)

    def eventFilter(self, obj, event) -> bool:  # noqa: ANN001
        et = event.type()
        if et in (QEvent.DragEnter, QEvent.DragMove):
            if event.mimeData().hasUrls() and any(
                u.toLocalFile() for u in event.mimeData().urls()
            ):
                event.acceptProposedAction()
                theme.apply_drop_style(self.drop_frame, loaded=False, hover=True)
                return True
        elif et == QEvent.DragLeave:
            self.drop_frame.set_loaded_style(self._video_path is not None)
            return False
        elif et == QEvent.Drop:
            self.drop_frame.set_loaded_style(self._video_path is not None)
            paths = [u.toLocalFile() for u in event.mimeData().urls() if u.toLocalFile()]
            if paths:
                event.acceptProposedAction()
                if len(paths) == 1:
                    self._load_video(Path(paths[0]))
                else:
                    self._on_files_dropped(paths)
                return True
        elif obj is self.video_container and et == QEvent.Resize:
            r = self.video_container.rect()
            self.video_view.setGeometry(r)
            self.crop_overlay.setGeometry(r)
            self.caption_overlay.setGeometry(r)
            self.caption_overlay_2.setGeometry(r)
            self.crop_overlay.raise_()
            self.caption_overlay.raise_()
            self.caption_overlay_2.raise_()
        return super().eventFilter(obj, event)

    # -----------------------------------------------------------------
    # Frameless mouse hooks (edge resize)
    # -----------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._frameless_resizer is not None and self._frameless_resizer.try_press(event):
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._frameless_resizer is not None and self._frameless_resizer.try_move(event):
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._frameless_resizer is not None and self._frameless_resizer.try_release(event):
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # -----------------------------------------------------------------
    # Crop
    # -----------------------------------------------------------------

    def _on_crop_toggled(self, checked: bool) -> None:
        if checked and self._info is None:
            self._tool_seg.set_active("trim")
            self._tool = "trim"
            return
        if checked and self._info:
            self.crop_overlay.set_video_aspect(self._info.width / self._info.height)
            if self.crop_overlay.normalized_rect() == QRectF(0, 0, 1, 1):
                self.crop_overlay.set_normalized_rect(QRectF(0.1, 0.1, 0.8, 0.8))
        self.crop_overlay.setVisible(checked)
        self.crop_overlay.raise_()
        self.crop_reset_btn.setVisible(checked)
        self._refresh_size_estimate()

    def _on_crop_reset(self) -> None:
        self.crop_overlay.reset()
        self._refresh_size_estimate()

    def _crop_pixels(self) -> tuple[int, int, int, int] | None:
        if self._tool != "crop" or self._info is None:
            return None
        r = self.crop_overlay.normalized_rect()
        if r == QRectF(0, 0, 1, 1):
            return None
        sw, sh = self._info.width, self._info.height
        x = int(round(r.x() * sw))
        y = int(round(r.y() * sh))
        w = int(round(r.width() * sw))
        h = int(round(r.height() * sh))
        w -= w % 2
        h -= h % 2
        x = max(0, min(sw - w, x - x % 2))
        y = max(0, min(sh - h, y - y % 2))
        if w < 2 or h < 2:
            return None
        return (x, y, w, h)

    # -----------------------------------------------------------------
    # Window lifecycle
    # -----------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: ANN001
        prefs.set_window_geometry(bytes(self.saveGeometry()))
        for worker in (self._convert_worker, self._thumbs_worker):
            if worker is not None:
                try:
                    worker.cancel()
                except Exception:  # noqa: BLE001
                    pass
        for thread in (self._convert_thread, self._thumbs_thread):
            if thread is not None and thread.isRunning():
                thread.quit()
                thread.wait(2000)
        super().closeEvent(event)

    def _check_ffmpeg(self) -> None:
        try:
            ff.require_ffmpeg()
            ff.require_ffprobe()
        except ff.FFmpegMissingError as exc:
            QMessageBox.critical(
                self, "Missing dependency",
                f"{exc}\n\nffmpeg.exe and ffprobe.exe should ship next to this "
                f"application.",
            )
        if not ff.has_gifsicle():
            self.status_line.set_idle(
                "tip · install gifsicle for extra GIF compression"
            )


# =====================================================================
# Helpers
# =====================================================================

def _fmt(seconds: float) -> str:
    seconds = max(0.0, seconds)
    m = int(seconds // 60)
    s = seconds - m * 60
    return f"{m:02d}:{s:05.2f}"


def _fmt_short(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{seconds:.1f}s"
    m = int(seconds // 60)
    s = int(seconds - m * 60)
    return f"{m}m{s:02d}s"


# Lightweight pulse dot used in the stage header (separate from the one
# embedded in StatusLine — keeps the dependency direction clean).

class _PulseDot(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(7, 7)
        self._state = "idle"

    def set_state(self, s: str) -> None:
        self._state = s
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: ANN001
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        if self._state == "running":
            p.setBrush(QColor(theme.ACCENT))
        elif self._state == "done":
            p.setBrush(QColor(theme.GOOD))
        else:
            p.setBrush(QColor(theme.ACCENT))
        p.setPen(Qt.NoPen)
        p.drawEllipse(0, 0, 6, 6)
        p.end()
