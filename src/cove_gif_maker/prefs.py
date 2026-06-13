"""Persistent user preferences.

Wraps QSettings so the rest of the app doesn't sprinkle string keys around.
Lives at the platform-default location (registry on Windows, ~/.config/Cove on
Linux). Anything that should survive across launches goes here: accent color,
default preset, default output directory, recent files, frameless toggle.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QSettings

from .portable import is_portable, portable_data_dir


# Sentinel for "no value stored yet" — distinguishes from explicit empty string.
_UNSET = object()

# Built-in accent palette. Keep in sync with the settings modal swatches.
ACCENT_PALETTE: list[tuple[str, str]] = [
    ("Teal",   "#50e6cf"),  # cove default
    ("Purple", "#7c5cff"),
    ("Green",  "#3ddc97"),
    ("Blue",   "#5ab7ff"),
    ("Red",    "#ff6b6b"),
    ("Amber",  "#ffb454"),
    ("White",  "#f0f0f0"),
]

DEFAULT_ACCENT  = "#50e6cf"
DEFAULT_PRESET  = "Discord"
RECENT_LIMIT    = 8


def _qs() -> QSettings:
    if is_portable():
        _portable_dir = portable_data_dir("cove-gif-maker")
        return QSettings(os.path.join(_portable_dir, "settings.ini"), QSettings.IniFormat)
    return QSettings("Cove", "GifMaker")


# ----- Accent ------------------------------------------------------------

def accent() -> str:
    return str(_qs().value("ui/accent", DEFAULT_ACCENT))


def set_accent(hex_color: str) -> None:
    _qs().setValue("ui/accent", hex_color)


# ----- Default preset ----------------------------------------------------

def default_preset() -> str:
    return str(_qs().value("ui/defaultPreset", DEFAULT_PRESET))


def set_default_preset(name: str) -> None:
    _qs().setValue("ui/defaultPreset", name)


# ----- Frameless window --------------------------------------------------

def frameless() -> bool:
    return _qs().value("ui/frameless", False, type=bool)


def set_frameless(flag: bool) -> None:
    _qs().setValue("ui/frameless", bool(flag))


# ----- Default output directory ------------------------------------------

def output_dir() -> str:
    return str(_qs().value("io/outputDir", ""))


def set_output_dir(path: str) -> None:
    _qs().setValue("io/outputDir", path)


# ----- Recent files ------------------------------------------------------

def recent_files() -> list[str]:
    raw = _qs().value("io/recents", [])
    if not raw:
        return []
    if isinstance(raw, str):
        # QSettings collapses single-element lists to bare strings.
        return [raw]
    out = [str(p) for p in raw if str(p).strip()]
    # Filter out files that no longer exist — recents are a UX shortcut, not a
    # historical log; pointing the user at a missing file is just noise.
    return [p for p in out if Path(p).exists()]


def push_recent(path: str | Path) -> None:
    p = str(path)
    items = [x for x in recent_files() if x != p]
    items.insert(0, p)
    items = items[:RECENT_LIMIT]
    _qs().setValue("io/recents", items)


def clear_recents() -> None:
    _qs().remove("io/recents")


# ----- Window geometry ---------------------------------------------------
# Persisted as raw bytes via QMainWindow.saveGeometry()/restoreGeometry().

def window_geometry() -> bytes | None:
    val = _qs().value("ui/windowGeometry", _UNSET)
    if val is _UNSET or val is None:
        return None
    return bytes(val)


def set_window_geometry(geom: bytes) -> None:
    _qs().setValue("ui/windowGeometry", geom)
