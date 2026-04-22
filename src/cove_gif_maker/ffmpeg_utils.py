from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


class FFmpegMissingError(RuntimeError):
    pass


# Hide the flashing console window when we shell out from a --windowed build.
if os.name == "nt":
    _CREATE_NO_WINDOW = 0x08000000
    _SUBPROCESS_KWARGS: dict = {"creationflags": _CREATE_NO_WINDOW}
else:
    _SUBPROCESS_KWARGS = {}


def _bundle_dirs() -> list[Path]:
    """Directories to check for bundled binaries (ffmpeg, ffprobe, gifsicle).

    Order: PyInstaller runtime extract dir (_MEIPASS), the directory of the
    running executable, and the source-tree assets/bin folder for dev runs.
    """
    dirs: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(Path(meipass))
    if getattr(sys, "frozen", False):
        dirs.append(Path(sys.executable).resolve().parent)
    dirs.append(Path(__file__).resolve().parent.parent.parent / "assets" / "bin")
    return dirs


def _find_binary(name: str) -> str | None:
    exe = f"{name}.exe" if os.name == "nt" else name
    for d in _bundle_dirs():
        candidate = d / exe
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name)


def require_ffmpeg() -> str:
    path = _find_binary("ffmpeg")
    if not path:
        raise FFmpegMissingError("ffmpeg not found")
    return path


def require_ffprobe() -> str:
    path = _find_binary("ffprobe")
    if not path:
        raise FFmpegMissingError("ffprobe not found")
    return path


def has_gifsicle() -> bool:
    return _find_binary("gifsicle") is not None


def gifsicle_path() -> str | None:
    return _find_binary("gifsicle")


@dataclass
class VideoInfo:
    duration: float
    width: int
    height: int
    fps: float


def probe(video: Path) -> VideoInfo:
    cmd = [
        require_ffprobe(),
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate:format=duration",
        "-of", "json",
        str(video),
    ]
    out = subprocess.check_output(cmd, text=True, **_SUBPROCESS_KWARGS)
    data = json.loads(out)
    stream = data["streams"][0]
    duration = float(data["format"]["duration"])
    num, den = stream["r_frame_rate"].split("/")
    fps = float(num) / float(den) if float(den) else 0.0
    return VideoInfo(
        duration=duration,
        width=int(stream["width"]),
        height=int(stream["height"]),
        fps=fps,
    )


def extract_thumbnail(video: Path, time: float, out: Path, height: int = 80) -> None:
    cmd = [
        require_ffmpeg(),
        "-y",
        "-ss", f"{time:.3f}",
        "-i", str(video),
        "-frames:v", "1",
        "-vf", f"scale=-2:{height}",
        "-q:v", "5",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True, **_SUBPROCESS_KWARGS)


def build_gif_filter(
    fps: int,
    scale_pct: int,
    speed: float,
    crop: tuple[int, int, int, int] | None = None,
) -> str:
    pts = 1.0 / speed
    parts = [f"setpts={pts:.4f}*PTS", f"fps={fps}"]
    if crop:
        x, y, w, h = crop
        parts.append(f"crop={w}:{h}:{x}:{y}")
    if scale_pct != 100:
        parts.append(f"scale=iw*{scale_pct/100:.4f}:-2:flags=lanczos")
    return ",".join(parts)


def build_palettegen_cmd(
    video: Path,
    start: float,
    end: float,
    palette: Path,
    fps: int,
    scale_pct: int,
    speed: float,
    palette_colors: int,
    crop: tuple[int, int, int, int] | None = None,
) -> list[str]:
    duration = max(0.01, end - start)
    vf = build_gif_filter(fps, scale_pct, speed, crop) + f",palettegen=max_colors={palette_colors}:stats_mode=diff"
    return [
        require_ffmpeg(),
        "-y",
        "-ss", f"{start:.3f}",
        "-t", f"{duration:.3f}",
        "-i", str(video),
        "-vf", vf,
        str(palette),
    ]


def build_paletteuse_cmd(
    video: Path,
    start: float,
    end: float,
    palette: Path,
    out: Path,
    fps: int,
    scale_pct: int,
    speed: float,
    loop: int,
    dither: str = "sierra2_4a",
    crop: tuple[int, int, int, int] | None = None,
) -> list[str]:
    duration = max(0.01, end - start)
    base = build_gif_filter(fps, scale_pct, speed, crop)
    filter_complex = f"[0:v]{base}[x];[x][1:v]paletteuse=dither={dither}"
    return [
        require_ffmpeg(),
        "-y",
        "-ss", f"{start:.3f}",
        "-t", f"{duration:.3f}",
        "-i", str(video),
        "-i", str(palette),
        "-filter_complex", filter_complex,
        "-loop", str(loop),
        str(out),
    ]


def build_webp_cmd(
    video: Path,
    start: float,
    end: float,
    out: Path,
    fps: int,
    scale_pct: int,
    speed: float,
    loop: int,
    quality: int,
    crop: tuple[int, int, int, int] | None = None,
) -> list[str]:
    duration = max(0.01, end - start)
    vf = build_gif_filter(fps, scale_pct, speed, crop)
    return [
        require_ffmpeg(),
        "-y",
        "-ss", f"{start:.3f}",
        "-t", f"{duration:.3f}",
        "-i", str(video),
        "-vf", vf,
        "-vcodec", "libwebp",
        "-lossless", "0",
        "-q:v", str(quality),
        "-loop", str(loop),
        "-an",
        "-vsync", "0",
        str(out),
    ]
