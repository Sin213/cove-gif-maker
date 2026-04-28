from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
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
    """Read width/height/duration/fps from a video file via ffprobe.

    Raises a `RuntimeError` with a human-readable message on failure so the
    UI can surface something more useful than a bare KeyError. The most
    common failure mode is a container that doesn't carry a `format.duration`
    field (some webm / fragmented mp4 / corrupt files); we also ask for the
    stream-level duration as a fallback."""
    # Also request stream=duration so we have a fallback when format-level
    # duration is missing.
    cmd = [
        require_ffprobe(),
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,duration:format=duration",
        "-of", "json",
        str(video),
    ]
    try:
        out = subprocess.check_output(cmd, text=True, **_SUBPROCESS_KWARGS)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"ffprobe failed for {video.name}. The file may be corrupt or "
            f"in an unsupported format."
        ) from exc

    try:
        data = json.loads(out)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe returned invalid JSON for {video.name}") from exc

    streams = data.get("streams") or []
    if not streams:
        raise RuntimeError(
            f"{video.name} doesn't appear to contain a video stream."
        )
    stream = streams[0]

    if "width" not in stream or "height" not in stream:
        raise RuntimeError(
            f"{video.name} is missing video dimensions — it may be audio-only "
            f"or use a codec the bundled ffmpeg can't decode."
        )

    fmt = data.get("format") or {}
    raw_duration = (
        fmt.get("duration")
        or stream.get("duration")
    )
    if raw_duration is None:
        raise RuntimeError(
            f"Couldn't determine the length of {video.name}. The container "
            f"may lack duration metadata (common with some .webm and "
            f"fragmented .mp4 files). Try re-encoding with:\n\n"
            f"    ffmpeg -i {video.name} -c copy fixed.mp4"
        )
    try:
        duration = float(raw_duration)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"Couldn't parse duration of {video.name} ('{raw_duration}')."
        ) from exc

    fps = 0.0
    rfr = stream.get("r_frame_rate", "0/1")
    if "/" in rfr:
        num_s, den_s = rfr.split("/", 1)
        try:
            num, den = float(num_s), float(den_s)
            if den:
                fps = num / den
        except ValueError:
            fps = 0.0

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


@dataclass
class Caption:
    """Burnt-in text overlay configuration.

    `pos_x` / `pos_y` are normalized 0..1 source-coord positions of the text's
    CENTER (so 0.5/0.5 = dead center, 0.5/0.95 = bottom-middle). The widget
    that lets the user drag the caption around stores the same normalized
    coordinates so the final output matches the on-screen preview.
    `rotation_deg` is degrees clockwise around the center.
    Rendering is no longer drawtext-based — see `caption_render.py`."""
    text: str
    pos_x: float = 0.5            # 0..1 horizontal center
    pos_y: float = 0.92           # 0..1 vertical center (default: bottom)
    color: str = "#ffffff"
    outline: str = "#000000"
    size_pct: float = 6.0         # font size as % of output height
    rotation_deg: float = 0.0     # rotation around the center, clockwise
    font_name: str = "DejaVu Sans:style=Bold"


def _has_caption(caption: Caption | None) -> bool:
    return bool(caption and caption.text and caption.text.strip())


def build_gif_filter(
    fps: int,
    scale_pct: int,
    speed: float,
    crop: tuple[int, int, int, int] | None = None,
) -> str:
    """Pre-paletteuse filter chain — single linear chain (no splits).

    Boomerang/reverse and caption overlay are NOT applied here because they
    require multi-input or split+concat structure that doesn't fit a `-vf`
    chain. Use `_wrap_loop` and the caption-input plumbing in the cmd
    builders below."""
    pts = 1.0 / speed
    parts = [f"setpts={pts:.4f}*PTS", f"fps={fps}"]
    if crop:
        x, y, w, h = crop
        parts.append(f"crop={w}:{h}:{x}:{y}")
    if scale_pct != 100:
        parts.append(f"scale=iw*{scale_pct/100:.4f}:-2:flags=lanczos")
    return ",".join(parts)


def _wrap_loop(base_chain: str, *, reverse: bool, boomerang: bool, in_label: str = "0:v",
               out_label: str = "x") -> str:
    """Wrap a base filter chain with reverse / boomerang loop logic.

    Returns a filter_complex graph. `in_label` is the input pad name (without
    brackets), `out_label` is the final output pad. The chain produces a
    single video stream tagged `[out_label]` ready to feed into paletteuse
    (or be the final output for WebP)."""
    if not reverse and not boomerang:
        return f"[{in_label}]{base_chain}[{out_label}]"
    if reverse and not boomerang:
        return f"[{in_label}]{base_chain},reverse[{out_label}]"
    # Boomerang: forward + reversed copy concatenated. Optional `reverse=true`
    # in combination would just play the backward half then the forward half —
    # same end effect, so we ignore the reverse flag when boomerang is on.
    return (
        f"[{in_label}]{base_chain},split[__a][__b];"
        f"[__b]reverse[__c];"
        f"[__a][__c]concat=n=2:v=1[{out_label}]"
    )


def _prepend_caption_overlay(graph: str, *, caption_index: int) -> str:
    """Prepend a caption-overlay step to a filter_complex graph.

    The caption PNG (rendered at source resolution) is composited onto
    `[0:v]` first, and the rest of the graph then operates on the merged
    stream by reading `[0:v]` — except we rename the merged stream to a
    fresh label and substitute it. Simpler: we pre-bind the caption result
    into [v_src] and the existing graph reads [0:v]. Easiest of all: emit
    a new pad name and rewrite the graph's first input reference."""
    # The caller's graphs all use [0:v] as the very first reference. Rebind:
    # composite [0:v] with caption PNG into [v_src], then string-replace the
    # first [0:v] in the existing graph with [v_src].
    overlay = f"[0:v][{caption_index}:v]overlay=0:0[v_src]"
    rebound = graph.replace("[0:v]", "[v_src]", 1)
    return f"{overlay};{rebound}"


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
    caption_png: Path | None = None,
) -> list[str]:
    duration = max(0.01, end - start)
    # Palette analysis runs on the ORIGINAL frame order — boomerang/reverse
    # don't introduce new colors, so we save the doubled work.
    base = build_gif_filter(fps, scale_pct, speed, crop)
    cmd = [
        require_ffmpeg(),
        "-y",
        "-ss", f"{start:.3f}",
        "-t", f"{duration:.3f}",
        "-i", str(video),
    ]
    if caption_png is not None:
        # Add the caption PNG as input #1 and run a filter_complex graph
        # that overlays it before palettegen — this way the caption colors
        # are included in the palette analysis.
        cmd += ["-i", str(caption_png)]
        graph = f"[0:v]{base},palettegen=max_colors={palette_colors}:stats_mode=diff[out]"
        graph = _prepend_caption_overlay(graph, caption_index=1)
        cmd += ["-filter_complex", graph, "-map", "[out]", str(palette)]
    else:
        vf = base + f",palettegen=max_colors={palette_colors}:stats_mode=diff"
        cmd += ["-vf", vf, str(palette)]
    return cmd


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
    reverse: bool = False,
    boomerang: bool = False,
    caption_png: Path | None = None,
) -> list[str]:
    duration = max(0.01, end - start)
    base = build_gif_filter(fps, scale_pct, speed, crop)
    cmd = [
        require_ffmpeg(),
        "-y",
        "-ss", f"{start:.3f}",
        "-t", f"{duration:.3f}",
        "-i", str(video),
    ]
    if caption_png is not None:
        # Inputs: 0=video, 1=caption PNG, 2=palette PNG
        cmd += ["-i", str(caption_png), "-i", str(palette)]
        seq = _wrap_loop(base, reverse=reverse, boomerang=boomerang, out_label="seq")
        seq = _prepend_caption_overlay(seq, caption_index=1)
        graph = f"{seq};[seq][2:v]paletteuse=dither={dither}"
    else:
        cmd += ["-i", str(palette)]
        seq = _wrap_loop(base, reverse=reverse, boomerang=boomerang, out_label="seq")
        graph = f"{seq};[seq][1:v]paletteuse=dither={dither}"
    cmd += ["-filter_complex", graph, "-loop", str(loop), str(out)]
    return cmd


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
    reverse: bool = False,
    boomerang: bool = False,
    caption_png: Path | None = None,
) -> list[str]:
    duration = max(0.01, end - start)
    base = build_gif_filter(fps, scale_pct, speed, crop)
    cmd = [
        require_ffmpeg(),
        "-y",
        "-ss", f"{start:.3f}",
        "-t", f"{duration:.3f}",
        "-i", str(video),
    ]
    use_complex = boomerang or caption_png is not None
    if use_complex:
        if caption_png is not None:
            cmd += ["-i", str(caption_png)]
        seq = _wrap_loop(base, reverse=reverse, boomerang=boomerang, out_label="seq")
        if caption_png is not None:
            seq = _prepend_caption_overlay(seq, caption_index=1)
        cmd += ["-filter_complex", seq, "-map", "[seq]"]
    else:
        vf = base + (",reverse" if reverse else "")
        cmd += ["-vf", vf]
    cmd += [
        "-vcodec", "libwebp",
        "-lossless", "0",
        "-q:v", str(quality),
        "-loop", str(loop),
        "-an",
        "-vsync", "0",
        str(out),
    ]
    return cmd
