#!/bin/bash
# Cross-compile Windows binaries from Linux using Docker.
#
# Uses TWO containers:
#   1. tobix/pywine:3.12 — PyInstaller builds (onedir + onefile)
#   2. amake/innosetup   — Inno Setup compiler (ISCC) for Setup.exe
#
# Run from the HOST (not inside a container):
#   VERSION=2.0.0 bash .winebuild/build-windows.sh
#
# Or run the inner stages directly inside their respective containers
# (see stage_pyinstaller / stage_innosetup functions).
set -euo pipefail

VERSION="${VERSION:-2.0.0}"
APP="cove-gif-maker"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# ------------------------------------------------------------------
# Detect: are we inside a container (called as a stage) or on the host?
# ------------------------------------------------------------------
if [ "${_STAGE:-}" = "pyinstaller" ]; then
    # ---- Stage 1: PyInstaller (inside tobix/pywine:3.12) ----
    SRC="/src"
    WORK="/work"
    mkdir -p "$WORK"
    cd "$SRC"

    echo "==> Installing build deps into wine-Python"
    wine pip install --quiet --no-warn-script-location PySide6 pyinstaller Pillow

    echo "==> Fetching ffmpeg release-essentials"
    mkdir -p "$WORK/ff"
    curl -sSLfo "$WORK/ff.zip" \
      "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    unzip -q "$WORK/ff.zip" -d "$WORK/ff"
    FFROOT=$(find "$WORK/ff" -maxdepth 1 -type d -name 'ffmpeg-*' | head -1)
    FFEXE="$FFROOT/bin/ffmpeg.exe"
    FPEXE="$FFROOT/bin/ffprobe.exe"
    test -f "$FFEXE" -a -f "$FPEXE" || { echo "ffmpeg/ffprobe missing"; exit 1; }

    echo "==> Fetching gifsicle"
    mkdir -p "$WORK/gs"
    curl -sSLfo "$WORK/gs.zip" \
      "https://eternallybored.org/misc/gifsicle/releases/gifsicle-1.95-win64.zip"
    unzip -q "$WORK/gs.zip" -d "$WORK/gs"
    GSEXE=$(find "$WORK/gs" -name gifsicle.exe | head -1)
    test -f "$GSEXE" || { echo "gifsicle.exe missing"; exit 1; }

    echo "==> Generating cove_icon.ico from PNG"
    wine python - <<'PY'
from PIL import Image
Image.open(r"Z:\src\cove_icon.png").save(
    r"Z:\src\cove_icon.ico",
    sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)],
)
PY
    test -f "$SRC/cove_icon.ico" || { echo "icon generation failed"; exit 1; }

    echo "==> Cleaning previous build artifacts"
    rm -rf "$SRC/build" "$SRC/dist"
    find "$SRC" -maxdepth 1 -name '*.spec' -delete

    PORTABLE_NAME="${APP}-portable"
    SEP=";"

    PYSIDE_DIR_W=$(wine python -c \
      "import PySide6, os; print(os.path.dirname(PySide6.__file__))" \
      2>/dev/null | tr -d '\r')
    PYSIDE_DIR=$(wine winepath -u "$PYSIDE_DIR_W" 2>/dev/null | tr -d '\r')
    [ -d "$PYSIDE_DIR" ] || { echo "PySide6 install not found at $PYSIDE_DIR"; exit 1; }
    echo "==> PySide6 dir (Linux view): $PYSIDE_DIR"

    PLUGIN_BASE=""
    for cand in "$PYSIDE_DIR/plugins" "$PYSIDE_DIR/Qt6/plugins" "$PYSIDE_DIR/Qt/plugins"; do
        if [ -d "$cand/platforms" ]; then PLUGIN_BASE="$cand"; break; fi
    done
    [ -n "$PLUGIN_BASE" ] || { echo "Qt platforms dir not located"; exit 1; }
    echo "==> Qt plugins dir: $PLUGIN_BASE"

    PLUGIN_DIRS=(platforms imageformats styles multimedia tls iconengines)
    PLUGIN_ARGS=()
    for d in "${PLUGIN_DIRS[@]}"; do
        src="$PLUGIN_BASE/$d"
        if [ -d "$src" ]; then
            wpath=$(wine winepath -w "$src" 2>/dev/null | tr -d '\r')
            PLUGIN_ARGS+=(--add-data "${wpath}${SEP}PySide6\\plugins\\$d")
            echo "    + plugins/$d"
        fi
    done

    COMMON_PYINSTALLER_ARGS=(
      --noconfirm --clean --log-level WARN
      --windowed
      --icon "Z:\\src\\cove_icon.ico"
      --paths "Z:\\src\\src"
      --add-data "Z:\\src\\src\\cove_gif_maker\\assets\\cove_icon.png${SEP}cove_gif_maker\\assets"
      --hidden-import PySide6.QtMultimedia
      --hidden-import PySide6.QtMultimediaWidgets
      --exclude-module PySide6.QtWebEngineCore
      --exclude-module PySide6.QtWebEngineWidgets
      --exclude-module PySide6.QtQml
      --exclude-module PySide6.QtQuick
      --exclude-module PySide6.QtPdf
      --exclude-module PySide6.Qt3DCore
      --exclude-module PySide6.QtCharts
      --exclude-module PySide6.QtDataVisualization
      --exclude-module tkinter
      "${PLUGIN_ARGS[@]}"
      --add-binary "Z:${FFEXE//\//\\}${SEP}."
      --add-binary "Z:${FPEXE//\//\\}${SEP}."
      --add-binary "Z:${GSEXE//\//\\}${SEP}."
    )

    echo "==> Running PyInstaller (onedir, windowed)"
    wine pyinstaller \
      "${COMMON_PYINSTALLER_ARGS[@]}" \
      --name "$APP" \
      "Z:\\src\\packaging\\launcher.py"

    ONEDIR_BUNDLE="$SRC/dist/$APP"
    test -d "$ONEDIR_BUNDLE" || { echo "PyInstaller onedir bundle not found at $ONEDIR_BUNDLE"; exit 1; }

    cp -f "$SRC/LICENSE"  "$ONEDIR_BUNDLE/" 2>/dev/null || true
    cp -f "$SRC/README.md" "$ONEDIR_BUNDLE/" 2>/dev/null || true
    FFLICENSE=$(find "$WORK/ff" -name "LICENSE" -type f | head -1)
    [ -n "$FFLICENSE" ] && cp -f "$FFLICENSE" "$ONEDIR_BUNDLE/FFMPEG-LICENSE.txt" || true

    echo "==> Running PyInstaller (onefile, windowed)"
    wine pyinstaller \
      "${COMMON_PYINSTALLER_ARGS[@]}" \
      --onefile \
      --name "$PORTABLE_NAME" \
      "Z:\\src\\packaging\\launcher.py"

    mkdir -p "$SRC/release"
    SRC_EXE="$SRC/dist/${PORTABLE_NAME}.exe"
    test -f "$SRC_EXE" || { echo "PyInstaller did not produce $SRC_EXE"; exit 1; }
    PORTABLE_DEST="$SRC/release/Cove-GIF-Maker-${VERSION}-Portable.exe"
    cp -f "$SRC_EXE" "$PORTABLE_DEST"
    ( cd "$SRC/release" && sha256sum "$(basename "$PORTABLE_DEST")" > "$(basename "$PORTABLE_DEST").sha256" )

    echo "==> PyInstaller stage done"
    ls -lh "$PORTABLE_DEST" "$PORTABLE_DEST.sha256"
    ls -lh "$ONEDIR_BUNDLE/"
    exit 0
fi

if [ "${_STAGE:-}" = "innosetup" ]; then
    # ---- Stage 2: Inno Setup (inside amake/innosetup) ----
    echo "==> Building Setup.exe via Inno Setup"
    ISCC_PATH="$(winepath -u "$(wine cmd /c 'echo %PROGRAMFILES%' 2>/dev/null | tr -d '\r')" 2>/dev/null)/Inno Setup 6/ISCC.exe"
    wine "$ISCC_PATH" \
      "/DAppVersion=$VERSION" \
      "/DSourceDir=Z:\src\dist\\$APP" \
      "/DOutputDir=Z:\src\release" \
      "/DIconFile=Z:\src\cove_icon.ico" \
      "Z:\src\packaging\installer.iss"

    SETUP_DEST="/src/release/Cove-GIF-Maker-${VERSION}-Setup.exe"
    SETUP_ISS_OUT="/src/release/${APP}-${VERSION}-Setup.exe"
    if [ -f "$SETUP_ISS_OUT" ] && [ "$SETUP_ISS_OUT" != "$SETUP_DEST" ]; then
        mv -f "$SETUP_ISS_OUT" "$SETUP_DEST"
    fi
    test -f "$SETUP_DEST" || { echo "Inno Setup did not produce Setup.exe"; ls -la /src/release/; exit 1; }

    ( cd /src/release && sha256sum "$(basename "$SETUP_DEST")" > "$(basename "$SETUP_DEST").sha256" )
    echo "==> Inno Setup stage done"
    ls -lh "$SETUP_DEST" "$SETUP_DEST.sha256"
    exit 0
fi

# ------------------------------------------------------------------
# Host orchestrator: run both stages via Docker
# ------------------------------------------------------------------
cd "$ROOT"

echo "============================================="
echo "  Cove GIF Maker — Windows cross-compile"
echo "  Version: $VERSION"
echo "============================================="

# Clean root-owned leftovers from previous Docker runs
if [ -d build ] || [ -d dist ]; then
    echo "==> Cleaning previous build artifacts"
    docker run --rm -v "$ROOT:/src" tobix/pywine:3.12 \
      rm -rf /src/build /src/dist
fi

echo ""
echo "=== Stage 1/2: PyInstaller (tobix/pywine:3.12) ==="
docker run --rm \
  -v "$ROOT:/src" \
  -e VERSION="$VERSION" \
  -e _STAGE=pyinstaller \
  tobix/pywine:3.12 \
  bash /src/.winebuild/build-windows.sh

echo ""
echo "=== Stage 2/2: Inno Setup (amake/innosetup) ==="
docker run --rm \
  -v "$ROOT:/src" \
  -e VERSION="$VERSION" \
  -e APP="$APP" \
  -e _STAGE=innosetup \
  --entrypoint bash \
  amake/innosetup \
  /src/.winebuild/build-windows.sh

echo ""
echo "==> All Windows artifacts:"
ls -lh "$ROOT/release/"*"$VERSION"*exe* 2>/dev/null || echo "(none found)"
