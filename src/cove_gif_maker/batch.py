"""Batch queue model + UI.

When the user drops more than one video at once, the extras get queued. The
currently-loaded clip is item 0 (with full preview and trim editing); the
rest of the queue runs sequentially after the first finishes, using the same
settings + presets as the visible clip. Per-item trim/crop is intentionally
not in scope — batch is for "convert these 12 clips with the same Discord
preset", not "edit each one individually".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import theme


class QueueStatus(str, Enum):
    PENDING  = "pending"
    ACTIVE   = "active"
    DONE     = "done"
    FAILED   = "failed"
    SKIPPED  = "skipped"


@dataclass
class QueueItem:
    path: Path
    status: QueueStatus = QueueStatus.PENDING
    output: Path | None = None
    error: str | None = None


class BatchQueue(QObject):
    """In-memory queue of additional source files to process.

    The currently-loaded clip in the main window is intentionally NOT part of
    the queue — it lives in `MainWindow._video_path`. Queue items are the
    extras that came in on the same drop or were appended after.
    """

    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._items: list[QueueItem] = []

    def items(self) -> list[QueueItem]:
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def is_empty(self) -> bool:
        return not self._items

    def add(self, paths: list[Path]) -> None:
        existing = {str(it.path) for it in self._items}
        for p in paths:
            sp = str(p)
            if sp in existing:
                continue
            self._items.append(QueueItem(path=p))
            existing.add(sp)
        self.changed.emit()

    def remove(self, index: int) -> None:
        if 0 <= index < len(self._items):
            del self._items[index]
            self.changed.emit()

    def clear(self) -> None:
        self._items.clear()
        self.changed.emit()

    def next_pending(self) -> tuple[int, QueueItem] | None:
        for i, it in enumerate(self._items):
            if it.status == QueueStatus.PENDING:
                return i, it
        return None

    def mark(self, index: int, status: QueueStatus, *,
             output: Path | None = None, error: str | None = None) -> None:
        if 0 <= index < len(self._items):
            self._items[index].status = status
            if output is not None:
                self._items[index].output = output
            if error is not None:
                self._items[index].error = error
            self.changed.emit()


# ----- UI -----------------------------------------------------------------


_STATUS_DOT = {
    QueueStatus.PENDING: theme.TEXT_FAINT,
    QueueStatus.ACTIVE:  theme.WARN,
    QueueStatus.DONE:    theme.GOOD,
    QueueStatus.FAILED:  theme.DANGER,
    QueueStatus.SKIPPED: theme.TEXT_FAINT,
}


class _QueueRow(QFrame):
    removeRequested = Signal(int)

    def __init__(self, index: int, item: QueueItem, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._index = index
        self._item = item
        self.setStyleSheet(
            f"QFrame {{ background: {theme.SURFACE_2}; border: 1px solid {theme.BORDER};"
            f" border-radius: {theme.RADIUS_SM}px; }}"
            f"QLabel {{ background: transparent; border: none; }}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 7, 8, 7)
        layout.setSpacing(8)

        dot = QLabel("●")
        dot.setStyleSheet(f"color: {_STATUS_DOT[item.status]}; font-size: 10px;")
        layout.addWidget(dot)

        name = QLabel(item.path.name)
        name.setStyleSheet(f"color: {theme.TEXT}; font-size: 12px;")
        name.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        name.setToolTip(str(item.path))
        layout.addWidget(name, stretch=1)

        status = QLabel(self._status_text(item))
        status.setStyleSheet(
            f"color: {theme.TEXT_DIM}; font-family: '{theme.FONT_MONO}', monospace;"
            f" font-size: 11px;"
        )
        layout.addWidget(status)

        if item.status in (QueueStatus.PENDING, QueueStatus.FAILED, QueueStatus.SKIPPED):
            rm = QPushButton("×")
            rm.setFixedSize(22, 22)
            rm.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {theme.TEXT_FAINT};"
                f" border: 1px solid {theme.BORDER}; border-radius: 11px;"
                f" padding: 0; font-size: 14px; }}"
                f"QPushButton:hover {{ color: {theme.DANGER};"
                f" border-color: rgba(255,107,107,0.45); }}"
            )
            rm.setCursor(Qt.PointingHandCursor)
            rm.clicked.connect(lambda: self.removeRequested.emit(self._index))
            layout.addWidget(rm)

    def _status_text(self, item: QueueItem) -> str:
        if item.status == QueueStatus.DONE and item.output is not None:
            try:
                kb = item.output.stat().st_size / 1024
                if kb < 1024:
                    return f"done · {kb:.0f} KB"
                return f"done · {kb / 1024:.1f} MB"
            except OSError:
                return "done"
        return item.status.value


class BatchQueuePanel(QWidget):
    """Inline list shown above the right panel when the queue is non-empty.

    No fancy reordering — just visibility into what's queued, plus a remove
    button per pending item and a clear-all. When the queue empties, the
    parent collapses this widget.
    """

    removeRequested  = Signal(int)
    clearRequested   = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._queue: BatchQueue | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("Queue")
        title.setProperty("role", "section")
        self._count_lbl = QLabel("0")
        self._count_lbl.setProperty("role", "mono")
        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setCursor(Qt.PointingHandCursor)
        self._clear_btn.clicked.connect(self.clearRequested.emit)
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self._count_lbl)
        head.addWidget(self._clear_btn)
        layout.addLayout(head)

        self._rows_holder = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_holder)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(6)
        layout.addWidget(self._rows_holder)

    def bind(self, queue: BatchQueue) -> None:
        self._queue = queue
        queue.changed.connect(self._render)
        self._render()

    def _render(self) -> None:
        # Wipe and re-render — the queue is small (typically <20 items) so a
        # full rebuild beats the bookkeeping for an incremental diff.
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.deleteLater()
        if self._queue is None:
            return
        items = self._queue.items()
        self._count_lbl.setText(f"{len(items)} item{'s' if len(items) != 1 else ''}")
        for i, it in enumerate(items):
            row = _QueueRow(i, it, self)
            row.removeRequested.connect(self.removeRequested.emit)
            self._rows_layout.addWidget(row)
