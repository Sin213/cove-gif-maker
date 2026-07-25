"""Small reusable widgets used throughout the redesigned UI.

Each widget is a one-job thing — Stepper, Segmented, EffectRow, PresetCard,
KV pair, FilePill, StatusLine, CoveSlider. They emit Qt signals so they plug
into the existing app state without any of the React-style state callbacks
the design reference used.
"""

from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush, QColor, QLinearGradient, QMouseEvent, QPainter, QPen,
)
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

from . import theme


# =====================================================================
# Stepper — − [value] +
# =====================================================================

class Stepper(QWidget):
    valueChanged = Signal(int)

    def __init__(self, *, minimum: int = 1, maximum: int = 999,
                 value: int = 1, step: int = 1, suffix: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._min = minimum
        self._max = maximum
        self._step = step
        self._suffix = suffix
        self._value = max(minimum, min(maximum, value))
        self.setStyleSheet(
            f"QFrame#cove-stepper {{ background: {theme.SURFACE};"
            f" border: 1px solid {theme.BORDER};"
            f" border-radius: {theme.RADIUS_XS}px;"
            f" padding: 2px; }}"
            f"QFrame#cove-stepper:focus-within {{ border-color: {theme.ACCENT_RING}; }}"
        )
        wrap = QFrame(self)
        wrap.setObjectName("cove-stepper")
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(wrap)

        layout = QHBoxLayout(wrap)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        self._dec = QPushButton("−")
        self._dec.setFixedSize(22, 22)
        self._dec.setCursor(Qt.PointingHandCursor)
        self._dec.setStyleSheet(self._btn_qss())
        self._dec.clicked.connect(lambda: self.set_value(self._value - self._step))
        self._inc = QPushButton("+")
        self._inc.setFixedSize(22, 22)
        self._inc.setCursor(Qt.PointingHandCursor)
        self._inc.setStyleSheet(self._btn_qss())
        self._inc.clicked.connect(lambda: self.set_value(self._value + self._step))

        self._edit = QLineEdit()
        self._edit.setFixedWidth(46)
        self._edit.setAlignment(Qt.AlignCenter)
        self._edit.setStyleSheet(
            f"QLineEdit {{ background: transparent; color: {theme.TEXT};"
            f" border: none; font-family: '{theme.FONT_MONO}', monospace;"
            f" font-size: 12px; }}"
        )
        self._edit.editingFinished.connect(self._on_edit_done)

        layout.addWidget(self._dec)
        layout.addWidget(self._edit)
        layout.addWidget(self._inc)

        self._refresh_text()

    def _btn_qss(self) -> str:
        return (
            f"QPushButton {{ background: transparent; color: {theme.TEXT_FAINT};"
            f" border: none; border-radius: 4px; font-size: 14px;"
            f" padding: 0; }}"
            f"QPushButton:hover {{ background: {theme.SURFACE_3}; color: {theme.TEXT}; }}"
        )

    def value(self) -> int:
        return self._value

    def set_value(self, v: int) -> None:
        v = max(self._min, min(self._max, int(v)))
        if v == self._value:
            self._refresh_text()
            return
        self._value = v
        self._refresh_text()
        self.valueChanged.emit(v)

    def _refresh_text(self) -> None:
        text = f"{self._value}{self._suffix}" if self._suffix else str(self._value)
        if self._edit.text() != text:
            self._edit.setText(text)

    def _on_edit_done(self) -> None:
        digits = "".join(c for c in self._edit.text() if c.isdigit() or c == "-")
        try:
            self.set_value(int(digits or self._value))
        except ValueError:
            self._refresh_text()


# =====================================================================
# TargetSizeInput — KB/MB-aware stepper for the Compression tab
# =====================================================================

class TargetSizeInput(QWidget):
    """Stepper that stores KB internally and displays "256 KB" or "10 MB"
    based on magnitude. Adapts step size to the current value so the user
    can dial in tight targets (Discord emoji 256 KB) AND large ones
    (Reddit 100 MB) from the same control."""

    valueChanged = Signal(int)  # KB

    _MIN_KB = 64
    _MAX_KB = 200 * 1024  # 200 MB ceiling

    def __init__(self, *, value_kb: int = 10 * 1024,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._value_kb = max(self._MIN_KB, min(self._MAX_KB, int(value_kb)))
        self.setStyleSheet(
            f"QFrame#cove-stepper {{ background: {theme.SURFACE};"
            f" border: 1px solid {theme.BORDER};"
            f" border-radius: {theme.RADIUS_XS}px;"
            f" padding: 2px; }}"
            f"QFrame#cove-stepper:focus-within {{ border-color: {theme.ACCENT_RING}; }}"
        )
        wrap = QFrame(self)
        wrap.setObjectName("cove-stepper")
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(wrap)

        layout = QHBoxLayout(wrap)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        btn_qss = (
            f"QPushButton {{ background: transparent; color: {theme.TEXT_FAINT};"
            f" border: none; border-radius: 4px; font-size: 14px; padding: 0; }}"
            f"QPushButton:hover {{ background: {theme.SURFACE_3}; color: {theme.TEXT}; }}"
        )
        self._dec = QPushButton("−")
        self._dec.setFixedSize(22, 22)
        self._dec.setCursor(Qt.PointingHandCursor)
        self._dec.setStyleSheet(btn_qss)
        self._dec.clicked.connect(self._on_dec)
        self._inc = QPushButton("+")
        self._inc.setFixedSize(22, 22)
        self._inc.setCursor(Qt.PointingHandCursor)
        self._inc.setStyleSheet(btn_qss)
        self._inc.clicked.connect(self._on_inc)
        self._edit = QLineEdit()
        self._edit.setFixedWidth(72)
        self._edit.setAlignment(Qt.AlignCenter)
        self._edit.setStyleSheet(
            f"QLineEdit {{ background: transparent; color: {theme.TEXT};"
            f" border: none; font-family: '{theme.FONT_MONO}', monospace;"
            f" font-size: 12px; }}"
        )
        self._edit.editingFinished.connect(self._on_edit_done)

        layout.addWidget(self._dec)
        layout.addWidget(self._edit)
        layout.addWidget(self._inc)
        self._refresh_text()

    # -- Public API -------------------------------------------------

    def value_kb(self) -> int:
        return self._value_kb

    def set_value_kb(self, kb: int) -> None:
        kb = max(self._MIN_KB, min(self._MAX_KB, int(kb)))
        if kb == self._value_kb:
            self._refresh_text()
            return
        self._value_kb = kb
        self._refresh_text()
        self.valueChanged.emit(kb)

    # -- Step rules -------------------------------------------------

    def _step(self) -> int:
        """Step size adapts to the current value so tight (KB-scale)
        targets are reachable AND large (MB-scale) targets don't take
        forever to scroll to."""
        v = self._value_kb
        if v < 1024:           return 64        # 64 KB
        if v < 10 * 1024:      return 256       # 0.25 MB
        if v < 50 * 1024:      return 1024      # 1 MB
        return 5 * 1024                          # 5 MB

    def _on_dec(self) -> None:
        self.set_value_kb(self._value_kb - self._step())

    def _on_inc(self) -> None:
        self.set_value_kb(self._value_kb + self._step())

    # -- Text formatting --------------------------------------------

    def _refresh_text(self) -> None:
        text = self._format(self._value_kb)
        if self._edit.text() != text:
            self._edit.setText(text)

    @staticmethod
    def _format(kb: int) -> str:
        if kb < 1024:
            return f"{kb} KB"
        mb = kb / 1024
        return f"{mb:.0f} MB" if abs(mb - round(mb)) < 0.05 else f"{mb:.1f} MB"

    def _on_edit_done(self) -> None:
        # Accept "256", "256 KB", "10 MB", "10.5MB", "0.5 MB"
        text = self._edit.text().strip().upper().replace(" ", "")
        try:
            if text.endswith("MB"):
                kb = int(round(float(text[:-2]) * 1024))
            elif text.endswith("KB"):
                kb = int(round(float(text[:-2])))
            else:
                kb = int(round(float(text)))  # bare number → KB
            self.set_value_kb(kb)
        except (ValueError, TypeError):
            self._refresh_text()


# =====================================================================
# Segmented control — pill of buttons, exactly one active
# =====================================================================

class Segmented(QWidget):
    """Inline group of small buttons. One can be active at a time.

    `options` is a list of (value, label) pairs. The control emits the
    active value, not the label.
    """

    activeChanged = Signal(object)

    def __init__(self, options: Sequence[tuple[object, str]],
                 *, active: object | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._options = list(options)
        self._buttons: dict[object, QPushButton] = {}
        self._active = active if active is not None else (
            self._options[0][0] if self._options else None
        )
        self.setStyleSheet(
            f"QFrame#cove-seg {{ background: {theme.SURFACE};"
            f" border: 1px solid {theme.BORDER};"
            f" border-radius: {theme.RADIUS_XS}px;"
            f" padding: 2px; }}"
        )
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        wrap = QFrame()
        wrap.setObjectName("cove-seg")
        outer.addWidget(wrap)

        layout = QHBoxLayout(wrap)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        for value, label in self._options:
            btn = QPushButton(label)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setStyleSheet(self._btn_qss(active=value == self._active))
            btn.clicked.connect(lambda _=False, v=value: self.set_active(v))
            self._buttons[value] = btn
            layout.addWidget(btn)

    def _btn_qss(self, *, active: bool) -> str:
        if active:
            return (
                f"QPushButton {{ background: {theme.SURFACE_3}; color: {theme.TEXT};"
                f" border: none; border-radius: 5px; padding: 4px 10px;"
                f" font-family: '{theme.FONT_MONO}', monospace; font-size: 11px; }}"
            )
        return (
            f"QPushButton {{ background: transparent; color: {theme.TEXT_FAINT};"
            f" border: none; border-radius: 5px; padding: 4px 10px;"
            f" font-family: '{theme.FONT_MONO}', monospace; font-size: 11px; }}"
            f"QPushButton:hover {{ color: {theme.TEXT}; }}"
        )

    def active(self) -> object:
        return self._active

    def set_active(self, value: object) -> None:
        if value == self._active or value not in self._buttons:
            return
        self._active = value
        for v, btn in self._buttons.items():
            btn.setStyleSheet(self._btn_qss(active=v == value))
        self.activeChanged.emit(value)

    def setEnabled(self, on: bool) -> None:  # noqa: N802
        super().setEnabled(on)
        for btn in self._buttons.values():
            btn.setEnabled(on)


# =====================================================================
# EffectRow — icon + title + description + toggle
# =====================================================================

class EffectRow(QFrame):
    toggled = Signal(bool)

    def __init__(self, *, icon: QWidget | None, title: str, desc: str,
                 checked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._on = bool(checked)
        self._icon = icon
        self.setCursor(Qt.PointingHandCursor)
        self._title = title
        self._desc = desc

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 9, 10, 9)
        layout.setSpacing(10)

        # Icon box
        self._icon_wrap = QFrame()
        self._icon_wrap.setFixedSize(26, 26)
        self._icon_wrap.setObjectName("cove-effect-icon")
        ilay = QHBoxLayout(self._icon_wrap)
        ilay.setContentsMargins(0, 0, 0, 0)
        ilay.addWidget(self._icon, alignment=Qt.AlignCenter) if self._icon else None
        layout.addWidget(self._icon_wrap)

        text_block = QVBoxLayout()
        text_block.setSpacing(1)
        self._title_lbl = QLabel(title)
        self._title_lbl.setStyleSheet(
            f"color: {theme.TEXT}; font-size: 12.5px; font-weight: 500;"
        )
        self._desc_lbl = QLabel(desc)
        self._desc_lbl.setStyleSheet(
            f"color: {theme.TEXT_FAINT}; font-size: 10.5px;"
            f" font-family: '{theme.FONT_MONO}', monospace;"
        )
        text_block.addWidget(self._title_lbl)
        text_block.addWidget(self._desc_lbl)
        layout.addLayout(text_block, stretch=1)

        # Toggle switch (custom paint)
        self._switch = _ToggleSwitch(self._on)
        layout.addWidget(self._switch)

        self._refresh_style()

    def is_checked(self) -> bool:
        return self._on

    def set_checked(self, on: bool) -> None:
        if on == self._on:
            return
        self._on = on
        self._switch.set_on(on)
        self._refresh_style()
        self.toggled.emit(on)

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        if event.button() == Qt.LeftButton:
            self.set_checked(not self._on)
            event.accept()

    def _refresh_style(self) -> None:
        if self._on:
            self.setStyleSheet(
                f"EffectRow {{ background: {theme.ACCENT_SOFT};"
                f" border: 1px solid {theme.ACCENT_RING};"
                f" border-radius: {theme.RADIUS_SM}px; }}"
                f"QFrame#cove-effect-icon {{ background: {theme.ACCENT};"
                f" border-radius: 6px; color: {theme.ACCENT_ON}; }}"
            )
        else:
            self.setStyleSheet(
                f"EffectRow {{ background: {theme.SURFACE};"
                f" border: 1px solid {theme.BORDER};"
                f" border-radius: {theme.RADIUS_SM}px; }}"
                f"EffectRow:hover {{ background: {theme.SURFACE_2};"
                f" border-color: {theme.BORDER_HARD}; }}"
                f"QFrame#cove-effect-icon {{ background: {theme.SURFACE_3};"
                f" border-radius: 6px; color: {theme.TEXT_DIM}; }}"
            )


class _ToggleSwitch(QWidget):
    def __init__(self, on: bool) -> None:
        super().__init__()
        self._on = on
        self.setFixedSize(28, 16)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def set_on(self, on: bool) -> None:
        if on != self._on:
            self._on = on
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: ANN001
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        bg = QColor(0, 0, 0, 102) if self._on else QColor(theme.SURFACE_3)
        p.setBrush(bg)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(self.rect(), 8, 8)
        # Knob
        knob_color = QColor(theme.ACCENT) if self._on else QColor(theme.TEXT_FAINT)
        p.setBrush(knob_color)
        x = 14 if self._on else 2
        p.drawEllipse(x, 2, 12, 12)
        p.end()


# =====================================================================
# PresetCard — small card with name + size hint, click to activate
# =====================================================================

class PresetCard(QFrame):
    """QFrame-based card so child labels render properly. (QPushButton
    swallows child widget layouts.)"""

    clicked = Signal()

    def __init__(self, name: str, hint: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._active = False
        self.setCursor(Qt.PointingHandCursor)
        self._name = name
        self._hint = hint
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(3)
        self._name_lbl = QLabel(name)
        self._hint_lbl = QLabel(hint)
        layout.addWidget(self._name_lbl)
        layout.addWidget(self._hint_lbl)
        self._refresh_style()

    def set_active(self, on: bool) -> None:
        if on == self._active:
            return
        self._active = on
        self._refresh_style()

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()

    def _refresh_style(self) -> None:
        if self._active:
            self.setStyleSheet(
                f"PresetCard {{ background: {theme.ACCENT_SOFT};"
                f" border: 1px solid {theme.ACCENT_RING};"
                f" border-radius: {theme.RADIUS_SM}px; }}"
            )
            self._name_lbl.setStyleSheet(
                f"color: {theme.ACCENT}; font-size: 12.5px; font-weight: 500;"
                f" background: transparent; border: none;"
            )
        else:
            self.setStyleSheet(
                f"PresetCard {{ background: {theme.SURFACE};"
                f" border: 1px solid {theme.BORDER};"
                f" border-radius: {theme.RADIUS_SM}px; }}"
                f"PresetCard:hover {{ background: {theme.SURFACE_2};"
                f" border-color: {theme.BORDER_HARD}; }}"
            )
            self._name_lbl.setStyleSheet(
                f"color: {theme.TEXT}; font-size: 12.5px; font-weight: 500;"
                f" background: transparent; border: none;"
            )
        self._hint_lbl.setStyleSheet(
            f"color: {theme.TEXT_FAINT}; font-size: 10.5px;"
            f" font-family: '{theme.FONT_MONO}', monospace;"
            f" background: transparent; border: none;"
        )


# =====================================================================
# FilePill — chip with filename + remove ×
# =====================================================================

class FilePill(QFrame):
    removeRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._name = ""
        self.setStyleSheet(
            f"QFrame {{ background: {theme.SURFACE};"
            f" border: 1px solid {theme.BORDER_HARD};"
            f" border-radius: 6px; }}"
            f"QLabel {{ background: transparent; border: none; }}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 8, 3)
        layout.setSpacing(7)
        self._name_lbl = QLabel("")
        self._name_lbl.setStyleSheet(
            f"color: {theme.TEXT}; font-family: '{theme.FONT_MONO}', monospace;"
            f" font-size: 11px;"
        )
        layout.addWidget(self._name_lbl)
        self._x = QPushButton("×")
        self._x.setCursor(Qt.PointingHandCursor)
        self._x.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {theme.TEXT_FAINT};"
            f" border: none; padding: 0 1px; font-size: 13px; }}"
            f"QPushButton:hover {{ color: {theme.TEXT}; }}"
        )
        self._x.clicked.connect(self.removeRequested.emit)
        layout.addWidget(self._x)
        self.setVisible(False)

    def set_name(self, name: str) -> None:
        self._name = name
        self._name_lbl.setText(name)
        self.setVisible(bool(name))


# =====================================================================
# KV — small mono "label / value" pair shown in stage header
# =====================================================================

class KV(QWidget):
    def __init__(self, label: str, value: str = "—",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self._label = QLabel(label.upper())
        self._label.setProperty("role", "kv-label")
        self._value = QLabel(value)
        self._value.setProperty("role", "kv-value")
        layout.addWidget(self._label)
        layout.addWidget(self._value)

    def set_value(self, v: str) -> None:
        self._value.setText(v)


# =====================================================================
# StatusLine — pulsing dot + text at the bottom of the rail footer
# =====================================================================

class StatusLine(QWidget):
    """Three states: idle / running / done. The dot pulses while running
    and glows steady-green when done."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = "idle"
        self._pulse = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._tick)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._dot = _PulseDot(self)
        layout.addWidget(self._dot)
        self._text = QLabel("idle · ready to convert")
        self._text.setStyleSheet(
            f"color: {theme.TEXT_FAINT}; font-size: 11px;"
            f" font-family: '{theme.FONT_MONO}', monospace;"
        )
        layout.addWidget(self._text, stretch=1)

    def set_idle(self, msg: str = "idle · ready to convert") -> None:
        self._state = "idle"
        self._dot.set_state("idle")
        self._timer.stop()
        self._text.setText(msg)

    def set_running(self, msg: str) -> None:
        self._state = "running"
        self._dot.set_state("running")
        self._timer.start()
        self._text.setText(msg)

    def set_done(self, msg: str) -> None:
        self._state = "done"
        self._dot.set_state("done")
        self._timer.stop()
        self._text.setText(msg)

    def set_failed(self, msg: str) -> None:
        self._state = "failed"
        self._dot.set_state("failed")
        self._timer.stop()
        self._text.setText(msg)

    def _tick(self) -> None:
        self._dot.advance()


class _PulseDot(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(8, 8)
        self._state = "idle"
        self._phase = 0.0

    def set_state(self, s: str) -> None:
        self._state = s
        self.update()

    def advance(self) -> None:
        self._phase = (self._phase + 0.07) % 1.0
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: ANN001
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        if self._state == "running":
            import math
            opacity = 0.4 + 0.6 * abs(math.sin(self._phase * math.pi))
            color = QColor(theme.ACCENT)
            color.setAlphaF(opacity)
            p.setBrush(color)
        elif self._state == "done":
            color = QColor(theme.GOOD)
            p.setBrush(color)
        elif self._state == "failed":
            p.setBrush(QColor(theme.DANGER))
        else:
            p.setBrush(QColor(theme.TEXT_FAINT))
        p.setPen(Qt.NoPen)
        p.drawEllipse(0, 0, self.width() - 1, self.height() - 1)
        p.end()


# =====================================================================
# CoveSlider — fully custom-painted slider matching the cove design
# =====================================================================

class CoveSlider(QWidget):
    """Horizontal slider painted by hand so the knob looks identical to
    the cove design reference. Avoids QSS+Fusion fighting over the handle
    geometry."""

    valueChanged = Signal(int)

    def __init__(self, *, minimum: int = 0, maximum: int = 100,
                 value: int = 50, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._min = minimum
        self._max = maximum
        self._value = max(minimum, min(maximum, value))
        self._dragging = False
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumSize(140, 22)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    # API surface compatible with QSlider so app.py can swap it in.
    def value(self) -> int:
        return self._value

    def setValue(self, v: int) -> None:  # noqa: N802
        v = max(self._min, min(self._max, int(v)))
        if v == self._value:
            return
        self._value = v
        self.update()
        self.valueChanged.emit(v)

    def setRange(self, lo: int, hi: int) -> None:  # noqa: N802
        self._min = int(lo)
        self._max = int(hi)
        self.setValue(self._value)

    def minimum(self) -> int:
        return self._min

    def maximum(self) -> int:
        return self._max

    # --- internal layout helpers ------------------------------------

    _TRACK_H = 4
    _KNOB = 14

    def _knob_x(self) -> float:
        if self._max <= self._min:
            return 0.0
        usable = self.width() - self._KNOB
        pct = (self._value - self._min) / (self._max - self._min)
        return self._KNOB / 2 + usable * pct - self.width() / 2  # offset from center

    def _value_for_x(self, x: float) -> int:
        usable = self.width() - self._KNOB
        if usable <= 0:
            return self._value
        pct = (x - self._KNOB / 2) / usable
        pct = max(0.0, min(1.0, pct))
        return round(self._min + pct * (self._max - self._min))

    # --- painting ---------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: ANN001
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        cy = self.height() / 2
        track_y = cy - self._TRACK_H / 2

        # Background track.
        bg = QColor(theme.SURFACE_3)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(
            QRectF(0, track_y, self.width(), self._TRACK_H),
            self._TRACK_H / 2, self._TRACK_H / 2,
        )

        # Fill (left side of the knob), gradient ACCENT → ACCENT_2.
        knob_cx = self._KNOB / 2 + (
            (self._value - self._min) / max(1, (self._max - self._min))
        ) * (self.width() - self._KNOB)
        if knob_cx > 0:
            grad = QLinearGradient(0, 0, knob_cx, 0)
            grad.setColorAt(0.0, QColor(theme.ACCENT))
            grad.setColorAt(1.0, QColor(theme.ACCENT_2))
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(
                QRectF(0, track_y, knob_cx, self._TRACK_H),
                self._TRACK_H / 2, self._TRACK_H / 2,
            )

        # Knob: white fill + 2px dark ring + subtle outer hairline so the
        # knob stays crisp on any background.
        knob_x = knob_cx - self._KNOB / 2
        knob_y = cy - self._KNOB / 2
        # Outer 1px dark ring (mimics the box-shadow 0 0 0 1px in CSS).
        p.setPen(QPen(QColor(255, 255, 255, 30), 1))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(knob_x - 0.5, knob_y - 0.5, self._KNOB + 1, self._KNOB + 1))
        # 2px dark border + white fill.
        p.setPen(QPen(QColor("#0a0a0e"), 2))
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QRectF(knob_x, knob_y, self._KNOB, self._KNOB))

        # Hover/focus accent ring
        if self.underMouse() and not self._dragging:
            p.setPen(QPen(QColor(theme.ACCENT_RING), 1))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QRectF(knob_x - 2, knob_y - 2, self._KNOB + 4, self._KNOB + 4))

        if not self.isEnabled():
            p.fillRect(self.rect(), QColor(0, 0, 0, 100))
        p.end()

    # --- mouse ------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.LeftButton:
            return
        self._dragging = True
        self.setValue(self._value_for_x(event.position().x()))
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging and (event.buttons() & Qt.LeftButton):
            self.setValue(self._value_for_x(event.position().x()))
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._dragging:
            self._dragging = False
            event.accept()


# =====================================================================
# RailTabs — small tab bar at the top of the right rail
# =====================================================================

class RailTabs(QWidget):
    activeChanged = Signal(str)

    def __init__(self, options: Sequence[tuple[str, str]],
                 *, active: str | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buttons: dict[str, QPushButton] = {}
        self._active = active or (options[0][0] if options else "")
        self.setStyleSheet(
            f"QWidget#cove-railtabs {{ border-bottom: 1px solid {theme.BORDER}; }}"
        )
        self.setObjectName("cove-railtabs")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(2)
        for value, label in options:
            btn = QPushButton(label)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setStyleSheet(self._btn_qss(value == self._active))
            btn.clicked.connect(lambda _=False, v=value: self.set_active(v))
            self._buttons[value] = btn
            layout.addWidget(btn)
        layout.addStretch(1)

    def _btn_qss(self, active: bool) -> str:
        if active:
            return (
                f"QPushButton {{ background: transparent; color: {theme.TEXT};"
                f" border: none; border-bottom: 2px solid {theme.ACCENT};"
                f" padding: 12px 12px 8px; font-size: 12px; }}"
            )
        return (
            f"QPushButton {{ background: transparent; color: {theme.TEXT_DIM};"
            f" border: none; border-bottom: 2px solid transparent;"
            f" padding: 12px 12px 8px; font-size: 12px; }}"
            f"QPushButton:hover {{ color: {theme.TEXT}; }}"
        )

    def active(self) -> str:
        return self._active

    def set_active(self, value: str) -> None:
        if value == self._active or value not in self._buttons:
            return
        self._active = value
        for v, btn in self._buttons.items():
            btn.setStyleSheet(self._btn_qss(v == value))
        self.activeChanged.emit(value)
