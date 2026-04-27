from __future__ import annotations

import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from . import ffmpeg_utils as ff


if os.name == "nt":
    _CREATE_NO_WINDOW = 0x08000000
    _POPEN_KWARGS: dict = {"creationflags": _CREATE_NO_WINDOW}
else:
    _POPEN_KWARGS = {}


@dataclass
class ConvertJob:
    video: Path
    output: Path
    start: float
    end: float
    fps: int
    scale_pct: int
    speed: float
    palette_colors: int
    loop: int
    fmt: str  # "gif" or "webp"
    webp_quality: int = 80
    optimize_with_gifsicle: bool = True
    crop: tuple[int, int, int, int] | None = None  # x, y, w, h in source pixels
    reverse: bool = False
    boomerang: bool = False
    captions: list[ff.Caption] = field(default_factory=list)
    # Source video dimensions — required when captions are set so the
    # renderer can produce the burn-in PNG at the right resolution. App
    # fills these from probe().
    source_width: int = 0
    source_height: int = 0


class ConvertWorker(QObject):
    progress = Signal(int)        # 0-100 overall
    eta = Signal(float)           # seconds remaining (smoothed)
    log = Signal(str)
    finished = Signal(Path)
    failed = Signal(str)

    def __init__(self, job: ConvertJob) -> None:
        super().__init__()
        self._job = job
        self._cancelled = False
        self._proc: subprocess.Popen | None = None
        self._job_start_wall: float = 0.0
        self._eta_smoothed: float | None = None

    def cancel(self) -> None:
        self._cancelled = True
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    def run(self) -> None:
        job = self._job
        self._job_start_wall = time.monotonic()
        self._eta_smoothed = None
        try:
            if job.fmt == "gif":
                self._run_gif(job)
            else:
                self._run_webp(job)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        if self._cancelled:
            self.failed.emit("Cancelled")
            return
        self.finished.emit(job.output)

    # --- format-specific pipelines -------------------------------------

    def _render_caption_png(self, job: ConvertJob, tmp: str) -> Path | None:
        """Burn all captions onto a single transparent PNG at source res.

        The bundled static ffmpeg lacks `drawtext`, so we render via Qt and
        composite later with the universally-available `overlay` filter.
        Returns None when no caption has any text — that lets the cmd
        builders skip the extra input + filter_complex graph."""
        non_empty = [
            c for c in (job.captions or [])
            if c.text and c.text.strip()
        ]
        if not non_empty:
            return None
        if not job.source_width or not job.source_height:
            self.log.emit("Caption skipped: source dimensions unknown")
            return None
        from . import caption_render  # local import — keeps module import cheap
        out = Path(tmp) / "caption.png"
        caption_render.render_many_to_png(
            non_empty, job.source_width, job.source_height, out,
        )
        return out

    def _run_gif(self, job: ConvertJob) -> None:
        with tempfile.TemporaryDirectory(prefix="cove-gif-") as tmp:
            caption_png = self._render_caption_png(job, tmp)
            palette = Path(tmp) / "palette.png"
            self.log.emit("Generating palette...")
            self._run_ffmpeg(
                ff.build_palettegen_cmd(
                    job.video, job.start, job.end, palette,
                    fps=job.fps, scale_pct=job.scale_pct, speed=job.speed,
                    palette_colors=job.palette_colors, crop=job.crop,
                    caption_png=caption_png,
                ),
                phase_start=0, phase_span=30,
            )
            if self._cancelled:
                return
            self.log.emit("Rendering GIF...")
            self._run_ffmpeg(
                ff.build_paletteuse_cmd(
                    job.video, job.start, job.end, palette, job.output,
                    fps=job.fps, scale_pct=job.scale_pct, speed=job.speed,
                    loop=job.loop, crop=job.crop,
                    reverse=job.reverse, boomerang=job.boomerang,
                    caption_png=caption_png,
                ),
                phase_start=30, phase_span=60,
                dur_multiplier=2.0 if job.boomerang else 1.0,
            )
            if self._cancelled:
                return
            gifsicle = ff.gifsicle_path()
            if job.optimize_with_gifsicle and gifsicle:
                self.log.emit("Optimizing with gifsicle...")
                self._run_subprocess([
                    gifsicle, "-O3", "--batch", str(job.output),
                ])
            self.progress.emit(100)

    def _run_webp(self, job: ConvertJob) -> None:
        with tempfile.TemporaryDirectory(prefix="cove-webp-") as tmp:
            caption_png = self._render_caption_png(job, tmp)
            self.log.emit("Rendering WebP...")
            self._run_ffmpeg(
                ff.build_webp_cmd(
                    job.video, job.start, job.end, job.output,
                    fps=job.fps, scale_pct=job.scale_pct, speed=job.speed,
                    loop=job.loop, quality=job.webp_quality, crop=job.crop,
                    reverse=job.reverse, boomerang=job.boomerang,
                    caption_png=caption_png,
                ),
                phase_start=0, phase_span=100,
                dur_multiplier=2.0 if job.boomerang else 1.0,
            )

    # --- subprocess helpers --------------------------------------------

    def _run_ffmpeg(
        self, cmd: list[str], *, phase_start: int, phase_span: int,
        dur_multiplier: float = 1.0,
    ) -> None:
        self.log.emit("$ " + " ".join(cmd))
        # Inject -progress (newline-separated key=value on stdout) and quiet stderr
        cmd = [cmd[0], "-progress", "pipe:1", "-nostats", "-loglevel", "error"] + cmd[1:]
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            **_POPEN_KWARGS,
        )
        clip_dur = max(0.01, (self._job.end - self._job.start) * dur_multiplier)
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            if self._cancelled:
                self._proc.terminate()
                break
            line = line.strip()
            if not line:
                continue
            key, _, value = line.partition("=")
            if key in ("out_time_us", "out_time_ms") and value.lstrip("-").isdigit():
                t = int(value) / 1_000_000  # both keys carry microseconds in modern ffmpeg
                pct = min(1.0, max(0.0, t / clip_dur))
                overall = phase_start + pct * phase_span
                self.progress.emit(int(overall))
                self._update_eta(overall)
            elif key == "progress" and value == "end":
                self.progress.emit(phase_start + phase_span)
                break
        rc = self._proc.wait()
        if rc != 0 and not self._cancelled:
            err = self._proc.stderr.read() if self._proc.stderr else ""
            raise RuntimeError(f"ffmpeg exited with code {rc}: {err.strip()[-300:]}")

    def _update_eta(self, overall_pct: float) -> None:
        if overall_pct < 2.0:
            return
        elapsed = time.monotonic() - self._job_start_wall
        if elapsed < 0.5:
            return
        eta_raw = max(0.0, elapsed * (100.0 - overall_pct) / overall_pct)
        if self._eta_smoothed is None:
            self._eta_smoothed = eta_raw
        else:
            alpha = 0.35
            self._eta_smoothed = alpha * eta_raw + (1 - alpha) * self._eta_smoothed
        self.eta.emit(self._eta_smoothed)

    def _run_subprocess(self, cmd: list[str]) -> None:
        self.log.emit("$ " + " ".join(cmd))
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **_POPEN_KWARGS,
        )
        rc = self._proc.wait()
        if rc != 0 and not self._cancelled:
            err = self._proc.stderr.read().decode(errors="ignore") if self._proc.stderr else ""
            raise RuntimeError(f"{cmd[0]} exited with code {rc}: {err}")


def start_conversion(job: ConvertJob) -> tuple[QThread, ConvertWorker]:
    """Create a QThread + worker pair. Caller must connect signals before start."""
    thread = QThread()
    worker = ConvertWorker(job)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    worker.failed.connect(thread.quit)
    thread.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    return thread, worker
