from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QImage

from . import ffmpeg_utils as ff


class ThumbnailWorker(QObject):
    finished = Signal(list)  # list[QImage]
    failed = Signal(str)

    def __init__(self, video: Path, duration: float, count: int = 24, height: int = 80) -> None:
        super().__init__()
        self._video = video
        self._duration = duration
        self._count = max(1, count)
        self._height = height
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        images: list[QImage] = []
        try:
            with tempfile.TemporaryDirectory(prefix="cove-thumbs-") as tmp:
                tmp_path = Path(tmp)
                step = self._duration / self._count
                for i in range(self._count):
                    if self._cancelled:
                        return
                    t = min(self._duration - 0.05, max(0.0, step * (i + 0.5)))
                    out = tmp_path / f"t_{i:03d}.jpg"
                    try:
                        ff.extract_thumbnail(self._video, t, out, height=self._height)
                    except Exception:  # noqa: BLE001
                        continue
                    img = QImage(str(out))
                    if not img.isNull():
                        images.append(img.copy())
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        self.finished.emit(images)


def start_thumbnails(video: Path, duration: float, count: int = 24) -> tuple[QThread, ThumbnailWorker]:
    thread = QThread()
    worker = ThumbnailWorker(video, duration, count=count)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    worker.failed.connect(thread.quit)
    thread.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    return thread, worker
