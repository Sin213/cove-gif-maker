"""Cove design system for the GIF Maker.

Mirrors the look of Cove Nexus (the Electron sister app): teal accent on a
deep, slightly purple-tinted dark background, Geist for text, Geist Mono for
technical metadata. Exposes:

* Color, radius, font constants — for paint code in `timeline.py` and
  `crop_overlay.py`.
* `apply(app)` — installs the QPalette + global QSS on a `QApplication`.
* `apply_drop_style(frame, state)` — keeps the drop-frame stylesheet in one
  place.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QFrame


# ----- Palette ------------------------------------------------------------
# Tokens lifted from the Cove design reference (Cove GIF Maker.html). Slightly
# darker and more saturated-cool than the previous values.

BG          = "#0a0a0e"
BG_2        = "#0d0d13"
BG_GRAD_TOP = "#0d0d13"
BG_GRAD_BOT = "#0a0a0e"
SURFACE     = "#11111a"
SURFACE_2   = "#161620"
SURFACE_3   = "#1c1c28"
SURFACE_4   = "#232331"
BORDER      = "rgba(255,255,255,0.06)"
BORDER_HARD = "rgba(255,255,255,0.10)"
BORDER_STRONG = "rgba(255,255,255,0.16)"

TEXT         = "#ececf1"
TEXT_DIM     = "#9a9aae"
TEXT_FAINT   = "#6b6b80"
TEXT_FAINTER = "#4a4a5c"

# Accent is teal with a slightly brighter companion used for gradients.
ACCENT       = "#50e6cf"
ACCENT_2     = "#7af5e0"
ACCENT_SOFT  = "rgba(80,230,207,0.13)"
ACCENT_RING  = "rgba(80,230,207,0.32)"
ACCENT_GLOW  = "rgba(80,230,207,0.45)"
ACCENT_ON    = "#0a0a0e"

GOOD        = "#3ddc97"
WARN        = "#ffb454"
DANGER      = "#ff6b6b"

# Solid-color helpers (for paint code that wants QColor instances)
QC_BG          = QColor(BG)
QC_BG_2        = QColor(BG_2)
QC_SURFACE     = QColor(SURFACE)
QC_SURFACE_2   = QColor(SURFACE_2)
QC_SURFACE_3   = QColor(SURFACE_3)
QC_SURFACE_4   = QColor(SURFACE_4)
QC_TEXT        = QColor(TEXT)
QC_TEXT_DIM    = QColor(TEXT_DIM)
QC_TEXT_FAINT  = QColor(TEXT_FAINT)
QC_TEXT_FAINTER = QColor(TEXT_FAINTER)
QC_ACCENT      = QColor(ACCENT)
QC_ACCENT_2    = QColor(ACCENT_2)
QC_ACCENT_SOFT = QColor(80, 230, 207, 33)   # 0.13 alpha
QC_ACCENT_RING = QColor(80, 230, 207, 82)   # 0.32 alpha
QC_ACCENT_GLOW = QColor(80, 230, 207, 115)  # 0.45 alpha
QC_BORDER      = QColor(255, 255, 255, 15)  # 0.06 alpha
QC_DIM_MASK    = QColor(0, 0, 0, 158)       # 0.62 alpha — matches HTML

# ----- Geometry & typography ---------------------------------------------

RADIUS     = 12
RADIUS_SM  = 8
RADIUS_XS  = 6

FONT_SANS = "Geist"
FONT_MONO = "Geist Mono"
FONT_FALLBACK_SANS = "Inter, ui-sans-serif, system-ui, Segoe UI, Roboto, sans-serif"
FONT_FALLBACK_MONO = "JetBrains Mono, ui-monospace, Cascadia Mono, Menlo, monospace"


# ----- Drop-frame states (kept here so app.py is paint-free) -------------

def _drop_qss(border: str, bg: str = "transparent", accent: bool = False) -> str:
    glow = ""
    if accent:
        glow = (
            f"background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
            f" stop:0 rgba(80,230,207,0.06),"
            f" stop:1 rgba(80,230,207,0.02));"
        )
    return (
        "QFrame#drop {"
        f" border: 1.5px dashed {border};"
        f" border-radius: {RADIUS}px;"
        f" background: {bg};"
        f" {glow}"
        "}"
    )


DROP_STYLE_IDLE   = _drop_qss(BORDER_HARD, bg=SURFACE)
DROP_STYLE_HOVER  = _drop_qss(ACCENT_RING, bg=SURFACE, accent=True)
DROP_STYLE_LOADED = (
    "QFrame#drop {"
    f" border: 1px solid {BORDER};"
    f" border-radius: {RADIUS}px;"
    f" background: #000;"
    "}"
)


def apply_drop_style(frame: QFrame, *, loaded: bool, hover: bool = False) -> None:
    if hover:
        frame.setStyleSheet(DROP_STYLE_HOVER)
    elif loaded:
        frame.setStyleSheet(DROP_STYLE_LOADED)
    else:
        frame.setStyleSheet(DROP_STYLE_IDLE)


# ----- App-wide QSS -------------------------------------------------------

def _stylesheet() -> str:
    return f"""
    /* ---- Window root ---------------------------------------------- */
    QMainWindow, QWidget#cove-root {{
        background: {BG};
        color: {TEXT};
    }}
    QWidget#cove-chrome {{
        background: {BG};
        border: none;
        border-radius: 0;
    }}
    QWidget#cove-stage {{ background: transparent; border-right: 1px solid {BORDER}; }}
    QWidget#cove-rail  {{ background: {BG_2}; }}
    QFrame#cove-divider {{ background: {BORDER}; max-height: 1px; min-height: 1px; }}

    QToolTip {{
        background: {SURFACE_2};
        color: {TEXT};
        border: 1px solid {BORDER_HARD};
        padding: 6px 9px;
        border-radius: {RADIUS_SM}px;
        font-size: 11.5px;
    }}

    /* ---- Labels --------------------------------------------------- */
    QLabel {{ color: {TEXT}; }}
    QLabel[role="dim"]   {{ color: {TEXT_DIM}; }}
    QLabel[role="faint"] {{ color: {TEXT_FAINT}; font-size: 11px; }}
    QLabel[role="title"] {{ color: {TEXT}; font-size: 22px; font-weight: 600; letter-spacing: -0.02em; }}
    QLabel[role="subtitle"] {{ color: {TEXT_DIM}; font-size: 12.5px; }}
    QLabel[role="section"] {{
        color: {TEXT_FAINT};
        font-size: 10.5px;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        font-weight: 500;
    }}
    QLabel[role="mono"] {{
        color: {TEXT_DIM};
        font-family: "{FONT_MONO}", {FONT_FALLBACK_MONO};
        font-size: 11.5px;
    }}
    QLabel[role="status-pill"] {{
        color: {TEXT_DIM};
        background: rgba(255,255,255,0.03);
        border: 1px solid {BORDER};
        border-radius: 999px;
        padding: 3px 10px;
        font-family: "{FONT_MONO}", {FONT_FALLBACK_MONO};
        font-size: 11px;
    }}
    QLabel[role="size-est"] {{
        color: {ACCENT};
        background: {ACCENT_SOFT};
        border: 1px solid {ACCENT_RING};
        border-radius: 999px;
        padding: 4px 10px;
        font-family: "{FONT_MONO}", {FONT_FALLBACK_MONO};
        font-size: 11.5px;
    }}
    QLabel[role="kv-label"] {{
        color: {TEXT_FAINTER};
        font-family: "{FONT_MONO}", {FONT_FALLBACK_MONO};
        font-size: 9.5px;
        letter-spacing: 0.14em;
    }}
    QLabel[role="kv-value"] {{
        color: {TEXT};
        font-family: "{FONT_MONO}", {FONT_FALLBACK_MONO};
        font-size: 12px;
        font-weight: 500;
    }}
    QLabel[role="rail-section"] {{
        color: {TEXT_FAINT};
        font-family: "{FONT_MONO}", {FONT_FALLBACK_MONO};
        font-size: 10.5px;
        letter-spacing: 0.16em;
        font-weight: 500;
        text-transform: uppercase;
    }}
    QLabel[role="hero-title"] {{
        color: {TEXT};
        font-size: 19px;
        font-weight: 600;
        letter-spacing: -0.015em;
    }}
    QLabel[role="hero-sub"] {{ color: {TEXT_DIM}; font-size: 12px; }}

    /* ---- Surface panels ------------------------------------------- */
    QFrame#panel {{
        background: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: {RADIUS}px;
    }}

    /* ---- Buttons -------------------------------------------------- */
    QPushButton, QToolButton {{
        background: transparent;
        color: {TEXT_DIM};
        border: 1px solid {BORDER_HARD};
        padding: 6px 12px;
        border-radius: {RADIUS_SM}px;
        font-size: 12.5px;
    }}
    QPushButton:hover, QToolButton:hover {{
        color: {TEXT};
        background: {SURFACE_2};
        border-color: {BORDER_HARD};
    }}
    QPushButton:pressed, QToolButton:pressed {{ background: {SURFACE_3}; }}
    QPushButton:disabled, QToolButton:disabled {{
        color: {TEXT_FAINT};
        background: transparent;
        border-color: {BORDER};
    }}
    QPushButton:checked, QToolButton:checked {{
        color: {ACCENT};
        background: {ACCENT_SOFT};
        border-color: {ACCENT_RING};
    }}

    QPushButton#btn-primary {{
        color: #ffffff;
        background: {ACCENT};
        border: 1px solid rgba(255,255,255,0.18);
        font-weight: 600;
        padding: 10px 18px;
    }}
    QPushButton#btn-primary:hover    {{ background: #6cf0db; color: #ffffff; }}
    QPushButton#btn-primary:pressed  {{ background: #44d4be; color: #ffffff; }}
    QPushButton#btn-primary:disabled {{
        color: {TEXT_FAINT}; background: {SURFACE_2}; border-color: {BORDER};
    }}

    QPushButton#btn-danger {{ color: #ffffff; }}
    QPushButton#btn-danger:hover {{
        color: #ffffff; border-color: rgba(255,107,107,0.55); background: rgba(255,107,107,0.18);
    }}
    QPushButton#btn-danger:disabled {{ color: {TEXT_FAINT}; }}

    /* ---- Combo boxes ---------------------------------------------- */
    QComboBox {{
        background: {SURFACE_2};
        color: {TEXT};
        border: 1px solid {BORDER_HARD};
        border-radius: {RADIUS_SM}px;
        padding: 6px 10px;
        min-height: 22px;
        selection-background-color: {ACCENT};
        selection-color: {ACCENT_ON};
    }}
    QComboBox:hover {{ border-color: {ACCENT_RING}; }}
    QComboBox:focus {{ border-color: {ACCENT}; }}

    /* ---- Slider --------------------------------------------------- */
    /* Track is a slim 4px pill, the filled portion uses the accent
       gradient (ACCENT → ACCENT_2). Handle is a small white knob with a
       crisp dark ring — same look as the cove design reference. */
    QSlider::groove:horizontal {{
        background: {SURFACE_3};
        height: 4px;
        border-radius: 2px;
        border: none;
    }}
    QSlider::sub-page:horizontal {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                    stop:0 {ACCENT}, stop:1 {ACCENT_2});
        height: 4px;
        border-radius: 2px;
    }}
    QSlider::add-page:horizontal {{
        background: {SURFACE_3};
        height: 4px;
        border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        background: #ffffff;
        border: 2px solid #0a0a0e;
        width: 14px; height: 14px;
        margin: -7px 0;
        border-radius: 9px;
    }}
    QSlider::handle:horizontal:hover {{
        background: #ffffff;
        border-color: {ACCENT};
    }}
    QSlider::handle:horizontal:pressed {{ background: {ACCENT_2}; }}
    QSlider::handle:horizontal:disabled {{
        background: {SURFACE_3}; border-color: {BORDER_HARD};
    }}
    QComboBox::drop-down {{
        subcontrol-origin: padding; subcontrol-position: top right;
        width: 22px; border: none;
        background: transparent;
    }}
    /* Fusion's default down-arrow renders fine; no override here. */
    QComboBox QAbstractItemView {{
        background: {SURFACE_2};
        color: {TEXT};
        border: 1px solid {BORDER_HARD};
        border-radius: {RADIUS_SM}px;
        padding: 4px;
        outline: 0;
        selection-background-color: {ACCENT_SOFT};
        selection-color: {TEXT};
    }}


    /* ---- Progress bar -------------------------------------------- */
    /* Text sits ON TOP of the teal chunk once the bar is full, so make it
       white and bold for readability — dim defaults blend right in. */
    QProgressBar {{
        background: {SURFACE_2};
        color: #ffffff;
        border: 1px solid {BORDER};
        border-radius: 8px;
        text-align: center;
        font-family: "{FONT_MONO}", {FONT_FALLBACK_MONO};
        font-size: 11px;
        font-weight: 600;
        padding: 0;
        min-height: 18px;
    }}
    QProgressBar::chunk {{
        background: {ACCENT};
        border-radius: 7px;
    }}

    /* ---- Status bar ---------------------------------------------- */
    QStatusBar {{
        background: {BG};
        color: {TEXT_DIM};
        border-top: 1px solid {BORDER};
        font-family: "{FONT_MONO}", {FONT_FALLBACK_MONO};
        font-size: 11px;
    }}
    QStatusBar::item {{ border: none; }}

    /* ---- Form layout labels -------------------------------------- */
    QFormLayout QLabel {{ color: {TEXT_DIM}; font-size: 12px; }}

    /* ---- Scrollbars ---------------------------------------------- */
    QScrollBar:vertical, QScrollBar:horizontal {{
        background: transparent; border: none; margin: 0;
        width: 10px; height: 10px;
    }}
    QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
        background: rgba(255,255,255,0.08);
        border-radius: 5px; min-height: 24px; min-width: 24px;
    }}
    QScrollBar::handle:hover {{ background: rgba(255,255,255,0.16); }}
    QScrollBar::add-line, QScrollBar::sub-line,
    QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: none; }}

    /* ---- Menu / message box -------------------------------------- */
    QMenu {{
        background: {SURFACE_2};
        color: {TEXT};
        border: 1px solid {BORDER_HARD};
        border-radius: {RADIUS_SM}px;
        padding: 4px;
    }}
    QMenu::item {{ padding: 6px 14px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {ACCENT_SOFT}; color: {TEXT}; }}

    QMessageBox {{ background: {SURFACE}; }}
    QMessageBox QLabel {{ color: {TEXT}; }}
    """


def _try_load_geist() -> None:
    """Best-effort Geist load: if the user has it installed, Qt picks it up
    automatically by family name; otherwise the QSS fallback chain takes over.
    No bundled font files yet — keeping the package light."""
    # Hook left intentionally for future asset bundling.
    return


def accent_qcolor() -> QColor:
    return QColor(ACCENT)


def _accent_variants(hex_color: str) -> tuple[str, str, str]:
    """Compute soft / ring / on-accent variants from a base hex color."""
    c = QColor(hex_color)
    soft = f"rgba({c.red()},{c.green()},{c.blue()},0.14)"
    ring = f"rgba({c.red()},{c.green()},{c.blue()},0.35)"
    # Pick black/white text against the accent based on perceived luminance.
    luma = (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) / 255.0
    on = "#0b0b10" if luma > 0.55 else "#ffffff"
    return soft, ring, on


def set_accent(hex_color: str) -> None:
    """Update the live accent and re-apply the QSS so existing widgets repaint."""
    global ACCENT, ACCENT_SOFT, ACCENT_RING, ACCENT_ON
    ACCENT = hex_color
    ACCENT_SOFT, ACCENT_RING, ACCENT_ON = _accent_variants(hex_color)
    app = QApplication.instance()
    if app is not None:
        app.setStyleSheet(_stylesheet())


def apply(app: QApplication, *, accent: str | None = None) -> None:
    """Apply the cove design system to a Qt app.

    If `accent` is provided it overrides the default teal — used at startup
    so the user's persisted accent applies before any window paints.
    """
    app.setStyle("Fusion")  # Fusion respects QPalette + QSS uniformly across OSes.
    _try_load_geist()

    if accent:
        global ACCENT, ACCENT_SOFT, ACCENT_RING, ACCENT_ON
        ACCENT = accent
        ACCENT_SOFT, ACCENT_RING, ACCENT_ON = _accent_variants(accent)

    base_font = QFont(FONT_SANS, 10)
    base_font.setStyleStrategy(QFont.PreferAntialias)
    app.setFont(base_font)

    p = QPalette()
    p.setColor(QPalette.Window,          QColor(BG))
    p.setColor(QPalette.WindowText,      QColor(TEXT))
    p.setColor(QPalette.Base,            QColor(SURFACE_2))
    p.setColor(QPalette.AlternateBase,   QColor(SURFACE_3))
    p.setColor(QPalette.ToolTipBase,     QColor(SURFACE_2))
    p.setColor(QPalette.ToolTipText,     QColor(TEXT))
    p.setColor(QPalette.Text,            QColor(TEXT))
    p.setColor(QPalette.Button,          QColor(SURFACE))
    p.setColor(QPalette.ButtonText,      QColor(TEXT))
    p.setColor(QPalette.BrightText,      QColor(DANGER))
    p.setColor(QPalette.Link,            QColor(ACCENT))
    p.setColor(QPalette.Highlight,       QColor(ACCENT))
    p.setColor(QPalette.HighlightedText, QColor(ACCENT_ON))
    p.setColor(QPalette.PlaceholderText, QColor(TEXT_FAINT))
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        p.setColor(QPalette.Disabled, role, QColor(TEXT_FAINT))
    app.setPalette(p)

    app.setStyleSheet(_stylesheet())
