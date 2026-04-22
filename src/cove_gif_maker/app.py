from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QRectF,
    QSizeF,
    QStandardPaths,
    QThread,
    QTimer,
    QUrl,
    Qt,
    Signal,
)
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QIcon, QMouseEvent, QPainter
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStatusBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import __version__, ffmpeg_utils as ff, updater
from .converter import ConvertJob, start_conversion
from .crop_overlay import CropOverlay
from .thumbnails import start_thumbnails
from .timeline import TrimBar


VIDEO_FILTERS = "Videos (*.mp4 *.mkv *.webm *.mov *.avi *.m4v *.mpg *.mpeg *.wmv);;All files (*)"

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
ICON_PATH = ASSETS_DIR / "cove_icon.png"

DROP_STYLE_IDLE = "QFrame#drop { border: 2px dashed #4a5160; border-radius: 8px; }"
DROP_STYLE_HOVER = (
    "QFrame#drop { border: 2px dashed #5fb4ff; border-radius: 8px; background: #1b2330; }"
)
DROP_STYLE_LOADED = "QFrame#drop { border: 1px solid #2a2f3a; border-radius: 8px; }"


class DropFrame(QFrame):
    fileDropped = Signal(str)
    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("drop")
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.StyledPanel)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(DROP_STYLE_IDLE)

    def set_loaded_style(self, loaded: bool) -> None:
        self.setStyleSheet(DROP_STYLE_LOADED if loaded else DROP_STYLE_IDLE)
        self.setCursor(Qt.ArrowCursor if loaded else Qt.PointingHandCursor)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet(DROP_STYLE_HOVER)

    def dragLeaveEvent(self, _event) -> None:  # noqa: ANN001
        self.setStyleSheet(DROP_STYLE_IDLE)

    def dropEvent(self, event: QDropEvent) -> None:
        self.setStyleSheet(DROP_STYLE_IDLE)
        urls = event.mimeData().urls()
        if not urls:
            return
        path = urls[0].toLocalFile()
        if path:
            self.fileDropped.emit(path)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class VideoView(QGraphicsView):
    """QGraphicsView wrapping a QGraphicsVideoItem.

    Renders through Qt's paint system (not a native surface) so sibling
    overlays remain visible during playback. Emits ``clicked`` for clicks
    on empty / video areas.
    """

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
        # Items in this scene don't accept mouse, so any click toggles play.
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"Cove GIF Maker v{__version__}")
        self.resize(1080, 720)
        if ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(ICON_PATH)))

        self._video_path: Path | None = None
        self._info: ff.VideoInfo | None = None
        self._thumbs_thread: QThread | None = None
        self._thumbs_worker = None
        self._convert_thread: QThread | None = None
        self._convert_worker = None

        self._build_ui()
        self._update_controls_enabled()
        self._check_ffmpeg()

        self._updater = updater.UpdateController(
            parent=self,
            current_version=__version__,
            repo="Sin213/cove-gif-maker",
            app_display_name="Cove GIF Maker",
            cache_subdir="cove-gif-maker",
        )
        QTimer.singleShot(4000, self._updater.check)

    # --- UI construction ----------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # video preview / drop area
        self.drop_frame = DropFrame()
        self.drop_frame.setMinimumHeight(360)
        self.drop_frame.fileDropped.connect(self._on_file_dropped)
        self.drop_frame.clicked.connect(self._on_drop_frame_clicked)
        drop_layout = QVBoxLayout(self.drop_frame)
        drop_layout.setContentsMargins(0, 0, 0, 0)
        drop_layout.setSpacing(8)

        self.video_container = QWidget()
        self.video_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.video_view = VideoView(self.video_container)
        self.video_view.clicked.connect(self._toggle_play)
        self.crop_overlay = CropOverlay(self.video_container)
        self.crop_overlay.setVisible(False)
        self.video_container.installEventFilter(self)

        self.placeholder = QWidget()
        ph_layout = QVBoxLayout(self.placeholder)
        ph_layout.setContentsMargins(0, 0, 0, 0)
        ph_layout.setSpacing(6)
        ph_layout.addStretch(1)

        self.placeholder_text = QLabel("Click or drop a video here")
        self.placeholder_text.setAlignment(Qt.AlignCenter)
        self.placeholder_text.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.placeholder_text.setStyleSheet(
            "color:#7a8294; font-size:15px; border:none; background:transparent;"
        )
        ph_layout.addWidget(self.placeholder_text)

        self.placeholder_hint = QLabel("MP4, MKV, WebM, MOV, AVI…")
        self.placeholder_hint.setAlignment(Qt.AlignCenter)
        self.placeholder_hint.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.placeholder_hint.setStyleSheet(
            "color:#5a616f; font-size:11px; border:none; background:transparent;"
        )
        ph_layout.addWidget(self.placeholder_hint)
        ph_layout.addStretch(1)

        drop_layout.addWidget(self.placeholder)
        drop_layout.addWidget(self.video_container)
        self.video_container.hide()

        # Make every visible widget in the window route drag/drop back to us via
        # eventFilter — Qt does not bubble drag events up the parent chain on its
        # own, so children with acceptDrops=False would otherwise reject drops.
        self.setAcceptDrops(True)
        for w in (
            self.video_container,
            self.video_view,
            self.video_view.viewport(),
            self.crop_overlay,
        ):
            w.setAcceptDrops(True)
            w.installEventFilter(self)

        root.addWidget(self.drop_frame, stretch=1)

        # player + controls
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.6)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video_view.video_output())
        self.player.positionChanged.connect(self._on_player_position)
        self.player.mediaStatusChanged.connect(self._on_media_status)

        # transport row
        transport = QHBoxLayout()
        self.play_btn = QToolButton()
        self.play_btn.setText("Play")
        self.play_btn.clicked.connect(self._toggle_play)
        self.set_in_btn = QPushButton("Start")
        self.set_in_btn.setToolTip("Set Start at playhead")
        self.set_in_btn.clicked.connect(self._set_in_at_playhead)
        self.set_out_btn = QPushButton("End")
        self.set_out_btn.setToolTip("Set End at playhead")
        self.set_out_btn.clicked.connect(self._set_out_at_playhead)
        self.crop_btn = QPushButton("Crop")
        self.crop_btn.setCheckable(True)
        self.crop_btn.setToolTip("Toggle crop overlay")
        self.crop_btn.toggled.connect(self._on_crop_toggled)
        self.crop_reset_btn = QPushButton("Reset")
        self.crop_reset_btn.setToolTip("Reset crop to full frame")
        self.crop_reset_btn.setVisible(False)
        self.crop_reset_btn.clicked.connect(self._on_crop_reset)
        self.range_label = QLabel("—")
        self.range_label.setStyleSheet("color:#cfd0d4;")
        transport.addWidget(self.play_btn)
        transport.addWidget(self.set_in_btn)
        transport.addWidget(self.set_out_btn)
        transport.addSpacing(12)
        transport.addWidget(self.crop_btn)
        transport.addWidget(self.crop_reset_btn)
        transport.addStretch(1)
        transport.addWidget(self.range_label)
        root.addLayout(transport)

        # trim bar
        self.trim_bar = TrimBar()
        self.trim_bar.rangeChanged.connect(self._on_range_changed)
        self.trim_bar.rangePreviewing.connect(self._on_range_previewing)
        self.trim_bar.playheadMoved.connect(self._on_playhead_moved)
        root.addWidget(self.trim_bar)

        # settings + actions row
        bottom = QHBoxLayout()
        bottom.setSpacing(16)

        settings_form = QFormLayout()
        settings_form.setLabelAlignment(Qt.AlignRight)
        settings_form.setHorizontalSpacing(10)

        self.fps_combo = QComboBox()
        self.fps_combo.addItems(["8", "12", "15", "24"])
        self.fps_combo.setCurrentText("15")

        self.scale_combo = QComboBox()
        self.scale_combo.addItems(["25", "50", "75", "100"])
        self.scale_combo.setCurrentText("50")

        self.palette_combo = QComboBox()
        self.palette_combo.addItem("Low (64)", 64)
        self.palette_combo.addItem("Medium (128)", 128)
        self.palette_combo.addItem("High (192)", 192)
        self.palette_combo.addItem("Max (256)", 256)
        self.palette_combo.setCurrentIndex(2)

        self.speed_combo = QComboBox()
        for label, value in [("0.5x", 0.5), ("1x", 1.0), ("1.5x", 1.5), ("2x", 2.0)]:
            self.speed_combo.addItem(label, value)
        self.speed_combo.setCurrentIndex(1)

        self.loop_combo = QComboBox()
        self.loop_combo.addItem("Infinite", 0)
        self.loop_combo.addItem("Once", -1)
        self.loop_combo.addItem("3x", 2)
        self.loop_combo.addItem("5x", 4)

        self.format_combo = QComboBox()
        self.format_combo.addItems(["GIF", "WebP"])
        self.format_combo.currentTextChanged.connect(self._on_format_changed)

        self.webp_quality = QSpinBox()
        self.webp_quality.setRange(1, 100)
        self.webp_quality.setValue(80)
        self.webp_quality.setSuffix("  (WebP)")
        self.webp_quality.setEnabled(False)

        settings_form.addRow("FPS", self.fps_combo)
        settings_form.addRow("Scale (%)", self.scale_combo)
        settings_form.addRow("Palette", self.palette_combo)
        settings_form.addRow("Speed", self.speed_combo)
        settings_form.addRow("Loop", self.loop_combo)
        settings_form.addRow("Format", self.format_combo)
        settings_form.addRow("Quality", self.webp_quality)

        settings_box = QWidget()
        settings_box.setLayout(settings_form)
        bottom.addWidget(settings_box, stretch=1)

        actions = QVBoxLayout()
        actions.setSpacing(8)
        self.convert_btn = QPushButton("Convert")
        self.convert_btn.setMinimumHeight(40)
        self.convert_btn.setStyleSheet(
            "QPushButton { background:#2563eb; color:white; font-weight:600; "
            "border:none; border-radius:6px; padding:8px 16px; }"
            "QPushButton:hover { background:#1d4ed8; }"
            "QPushButton:disabled { background:#3a4150; color:#9aa0ad; }"
        )
        self.convert_btn.clicked.connect(self._on_convert_clicked)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("%p%")
        self._last_progress = 0
        self._last_eta: float | None = None

        actions.addWidget(self.convert_btn)
        actions.addWidget(self.cancel_btn)
        actions.addWidget(self.progress)
        actions.addStretch(1)

        actions_box = QWidget()
        actions_box.setLayout(actions)
        bottom.addWidget(actions_box, stretch=1)

        root.addLayout(bottom)

        self.status = QStatusBar()
        self.setStatusBar(self.status)

    # --- file loading -------------------------------------------------

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
        self.crop_overlay.set_video_aspect(info.width / info.height)
        self.crop_overlay.set_normalized_rect(QRectF(0, 0, 1, 1))
        self.crop_btn.setChecked(False)
        self.crop_overlay.setVisible(False)
        self.crop_reset_btn.setVisible(False)
        self.drop_frame.set_loaded_style(True)
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.trim_bar.set_duration(info.duration)
        self._update_range_label()
        self.status.showMessage(
            f"{path.name} • {info.width}×{info.height} @ {info.fps:.2f}fps • "
            f"{info.duration:.2f}s"
        )
        self._kick_off_thumbs(path, info.duration)
        self._update_controls_enabled()

    def _kick_off_thumbs(self, path: Path, duration: float) -> None:
        if self._thumbs_thread:
            try:
                self._thumbs_worker.cancel()
            except Exception:  # noqa: BLE001
                pass
        thread, worker = start_thumbnails(path, duration, count=28)
        # Bound QObject methods get Qt.QueuedConnection automatically; lambdas
        # don't, which lets cross-thread calls into widgets crash the app.
        worker.finished.connect(self.trim_bar.set_thumbnails, Qt.QueuedConnection)
        worker.failed.connect(self._on_thumb_error, Qt.QueuedConnection)
        self._thumbs_thread = thread
        self._thumbs_worker = worker
        thread.start()

    def _on_thumb_error(self, msg: str) -> None:
        self.status.showMessage(f"Thumbnail error: {msg}", 5000)

    # --- player events ------------------------------------------------

    def _toggle_play(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            self.play_btn.setText("Play")
        else:
            self.player.play()
            self.play_btn.setText("Pause")

    def _on_player_position(self, ms: int) -> None:
        if not self._info:
            return
        t = ms / 1000.0
        self.trim_bar.set_playhead(t)
        if t > self.trim_bar.end() + 0.05:
            self.player.pause()
            self.player.setPosition(int(self.trim_bar.start() * 1000))
            self.play_btn.setText("Play")

    def _on_media_status(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.LoadedMedia and self._info is None:
            # fallback if probe somehow missed
            self.trim_bar.set_duration(self.player.duration() / 1000.0)

    def _on_playhead_moved(self, t: float) -> None:
        self.player.setPosition(int(t * 1000))

    def _on_range_changed(self, start: float, end: float) -> None:
        self._update_range_label()

    def _on_range_previewing(self, start: float, end: float) -> None:
        self._update_range_label()

    def _update_range_label(self) -> None:
        if not self._info:
            self.range_label.setText("—")
            return
        s = self.trim_bar.start()
        e = self.trim_bar.end()
        self.range_label.setText(f"Start {_fmt(s)}  •  End {_fmt(e)}  •  {_fmt(e - s)}")

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

    # --- format toggle ------------------------------------------------

    def _on_format_changed(self, fmt: str) -> None:
        self.webp_quality.setEnabled(fmt == "WebP")
        self.palette_combo.setEnabled(fmt == "GIF")

    # --- conversion ---------------------------------------------------

    def _on_convert_clicked(self) -> None:
        if not self._video_path or not self._info:
            return
        fmt = self.format_combo.currentText().lower()
        ext = "gif" if fmt == "gif" else "webp"
        suggested = str(self._video_path.with_suffix(f".{ext}"))
        out_path, _ = QFileDialog.getSaveFileName(
            self, f"Save {ext.upper()}", suggested,
            f"{ext.upper()} (*.{ext});;All files (*)",
        )
        if not out_path:
            return

        loop_value = self.loop_combo.currentData()
        job = ConvertJob(
            video=self._video_path,
            output=Path(out_path),
            start=self.trim_bar.start(),
            end=self.trim_bar.end(),
            fps=int(self.fps_combo.currentText()),
            scale_pct=int(self.scale_combo.currentText()),
            speed=float(self.speed_combo.currentData()),
            palette_colors=int(self.palette_combo.currentData()),
            loop=int(loop_value),
            fmt=fmt,
            webp_quality=self.webp_quality.value(),
            crop=self._crop_pixels(),
        )

        self._last_progress = 0
        self._last_eta = None
        self.progress.setValue(0)
        self.progress.setFormat("starting…")
        self.convert_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.status.showMessage("Converting...")

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
            self.status.showMessage("Cancelling...")

    def _on_worker_log(self, msg: str) -> None:
        self.status.showMessage(msg, 4000)

    def _on_progress(self, pct: int) -> None:
        self._last_progress = max(self._last_progress, pct)
        self.progress.setValue(self._last_progress)
        self._refresh_progress_text()

    def _on_eta(self, seconds: float) -> None:
        self._last_eta = seconds
        self._refresh_progress_text()

    def _refresh_progress_text(self) -> None:
        if self._last_progress >= 99 or self._last_eta is None:
            self.progress.setFormat("%p%")
        else:
            secs = max(0, int(round(self._last_eta)))
            m, s = divmod(secs, 60)
            self.progress.setFormat(f"%p%  •  ETA {m}:{s:02d}")

    def _on_conversion_done(self, out: Path) -> None:
        size_kb = out.stat().st_size / 1024
        unit = "KB" if size_kb < 1024 else "MB"
        size = size_kb if unit == "KB" else size_kb / 1024
        self.status.showMessage(f"Saved {out.name} ({size:.1f} {unit})", 8000)
        self._last_progress = 100
        self._last_eta = None
        self.progress.setValue(100)
        self.progress.setFormat("%p%")

    def _on_conversion_failed(self, msg: str) -> None:
        self.status.showMessage(f"Failed: {msg}", 8000)
        QMessageBox.warning(self, "Conversion failed", msg)

    def _reset_after_conversion(self) -> None:
        self._convert_thread = None
        self._convert_worker = None
        self.convert_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)

    # --- misc ---------------------------------------------------------

    def _update_controls_enabled(self) -> None:
        loaded = self._info is not None
        for w in (
            self.play_btn, self.set_in_btn, self.set_out_btn, self.crop_btn,
            self.fps_combo, self.scale_combo, self.palette_combo,
            self.speed_combo, self.loop_combo, self.format_combo,
            self.convert_btn,
        ):
            w.setEnabled(loaded)
        self.webp_quality.setEnabled(loaded and self.format_combo.currentText() == "WebP")

    # --- window-level drag & drop ------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls() and any(
            u.toLocalFile() for u in event.mimeData().urls()
        ):
            event.acceptProposedAction()
            self.drop_frame.setStyleSheet(DROP_STYLE_HOVER)

    def dragMoveEvent(self, event) -> None:  # noqa: ANN001
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragLeaveEvent(self, _event) -> None:  # noqa: ANN001
        self.drop_frame.set_loaded_style(self._video_path is not None)

    def dropEvent(self, event: QDropEvent) -> None:
        self.drop_frame.set_loaded_style(self._video_path is not None)
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path:
                self._load_video(Path(path))
                event.acceptProposedAction()
                return

    # --- crop ---------------------------------------------------------

    def eventFilter(self, obj, event) -> bool:  # noqa: ANN001
        et = event.type()
        if et in (QEvent.DragEnter, QEvent.DragMove):
            if event.mimeData().hasUrls() and any(
                u.toLocalFile() for u in event.mimeData().urls()
            ):
                event.acceptProposedAction()
                self.drop_frame.setStyleSheet(DROP_STYLE_HOVER)
                return True
        elif et == QEvent.DragLeave:
            self.drop_frame.set_loaded_style(self._video_path is not None)
            return False
        elif et == QEvent.Drop:
            self.drop_frame.set_loaded_style(self._video_path is not None)
            for url in event.mimeData().urls():
                path = url.toLocalFile()
                if path:
                    event.acceptProposedAction()
                    self._load_video(Path(path))
                    return True
        elif obj is self.video_container and et == QEvent.Resize:
            r = self.video_container.rect()
            self.video_view.setGeometry(r)
            self.crop_overlay.setGeometry(r)
            self.crop_overlay.raise_()
        return super().eventFilter(obj, event)

    def _on_crop_toggled(self, checked: bool) -> None:
        if checked and self._info is None:
            self.crop_btn.setChecked(False)
            return
        if checked and self._info:
            self.crop_overlay.set_video_aspect(self._info.width / self._info.height)
            if self.crop_overlay.normalized_rect() == QRectF(0, 0, 1, 1):
                self.crop_overlay.set_normalized_rect(QRectF(0.1, 0.1, 0.8, 0.8))
        self.crop_overlay.setVisible(checked)
        self.crop_overlay.raise_()
        self.crop_reset_btn.setVisible(checked)

    def _on_crop_reset(self) -> None:
        self.crop_overlay.reset()

    def _crop_pixels(self) -> tuple[int, int, int, int] | None:
        if not self.crop_btn.isChecked() or self._info is None:
            return None
        r = self.crop_overlay.normalized_rect()
        if r == QRectF(0, 0, 1, 1):
            return None
        sw, sh = self._info.width, self._info.height
        x = int(round(r.x() * sw))
        y = int(round(r.y() * sh))
        w = int(round(r.width() * sw))
        h = int(round(r.height() * sh))
        # ensure even dimensions for codec friendliness
        w -= w % 2
        h -= h % 2
        x = max(0, min(sw - w, x - x % 2))
        y = max(0, min(sh - h, y - y % 2))
        if w < 2 or h < 2:
            return None
        return (x, y, w, h)

    def closeEvent(self, event) -> None:  # noqa: ANN001
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
                f"application. If you are running from source, install ffmpeg "
                f"with winget (winget install Gyan.FFmpeg) or drop ffmpeg.exe "
                f"and ffprobe.exe next to the app.",
            )
        if not ff.has_gifsicle():
            self.status.showMessage(
                "Tip: drop gifsicle.exe next to the app for extra GIF size "
                "optimization", 6000,
            )


def _fmt(seconds: float) -> str:
    seconds = max(0.0, seconds)
    m = int(seconds // 60)
    s = seconds - m * 60
    return f"{m:02d}:{s:05.2f}"
