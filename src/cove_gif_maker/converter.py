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
    # Target-size compression: when > 0, the worker runs the conversion,
    # checks the output vs `target_size_kb`, and if it's too big, lowers
    # quality/palette/scale and retries up to `target_max_attempts` times.
    target_size_kb: float = 0.0
    target_max_attempts: int = 4


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
            self._run_with_target(job)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        if self._cancelled:
            self.failed.emit("Cancelled")
            return
        self.finished.emit(job.output)

    # --- target-size compression --------------------------------------

    def _run_with_target(self, job: ConvertJob) -> None:
        """Run the conversion once, then iteratively lower quality / scale
        if the output exceeds `target_size_kb`. With no target set, runs
        a single pass."""
        attempts = 0
        attempt_limit = max(1, job.target_max_attempts) if job.target_size_kb > 0 else 1
        while True:
            attempts += 1
            self._reset_progress_state()
            if attempts > 1:
                self.log.emit(
                    f"Compressing further (attempt {attempts}/{attempt_limit})"
                )
            if job.fmt == "gif":
                self._run_gif(job)
            else:
                self._run_webp(job)
            if self._cancelled or job.target_size_kb <= 0:
                return
            if not job.output.exists():
                return
            actual_kb = job.output.stat().st_size / 1024.0
            if actual_kb <= job.target_size_kb:
                self.log.emit(
                    f"Hit target ({actual_kb:.0f} KB ≤ {job.target_size_kb:.0f} KB)"
                )
                return
            if attempts >= attempt_limit:
                self.log.emit(
                    f"Stopping at {actual_kb:.0f} KB (target {job.target_size_kb:.0f} KB)"
                )
                return
            # Re-tune the job for the next attempt. We're aggressive with
            # the cut on the first retry (output is way over) and back off
            # to gentler steps as we approach the target.
            overshoot = actual_kb / job.target_size_kb
            self._tighten_job(job, overshoot=overshoot)

    def _reset_progress_state(self) -> None:
        # Each attempt starts the progress bar from 0%.
        self._eta_smoothed = None
        self._job_start_wall = time.monotonic()
        self.progress.emit(0)

    @staticmethod
    def _tighten_job(job: ConvertJob, *, overshoot: float) -> None:
        """Reduce quality / palette / scale so the next attempt produces
        a smaller file. Mutates `job` in place.

        The tightening strategy depends on overshoot magnitude:
        - WebP: lower quality first (roughly linear effect). Once quality
          hits the floor (q=20), or for very high overshoot (>3x), drop
          scale too — quality alone can't bridge a 5x gap, but cutting
          resolution does.
        - GIF: halve the palette first (big win, perceptually mild), then
          drop scale. Scale is the killer lever once the palette is at 64.
        """
        if job.fmt == "webp":
            old_q = job.webp_quality
            cut = 0.6 if overshoot > 3.0 else (0.7 if overshoot > 2.0 else 0.85)
            new_q = max(20, int(round(old_q * cut)))
            job.webp_quality = new_q
            # When quality can't drop further, OR we're far from target,
            # also reduce scale so the next pass has a real chance.
            quality_floored = (new_q == old_q == 20)
            if quality_floored or (new_q <= 30 and overshoot > 1.5):
                if job.scale_pct > 20:
                    scale_cut = 0.8 if overshoot > 2.0 else 0.9
                    job.scale_pct = max(20, int(round(job.scale_pct * scale_cut)))
        else:
            # GIF: palette → scale → fps drops, in that order of impact.
            if job.palette_colors > 64:
                job.palette_colors = max(64, job.palette_colors // 2)
            elif job.scale_pct > 20:
                cut = 0.7 if overshoot > 2.0 else 0.85
                job.scale_pct = max(20, int(round(job.scale_pct * cut)))
            elif job.fps > 8:
                job.fps = max(8, job.fps - 4)

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
        cmd = [cmd[0], "-progress", "pipe:1", "-nostats", "-loglevel", "error"] + cmd[1:]
        stderr_file = tempfile.NamedTemporaryFile(
            mode="w+", prefix="cove-ffstderr-", suffix=".txt", delete=False,
        )
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=stderr_file,
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
                    t = int(value) / 1_000_000
                    pct = min(1.0, max(0.0, t / clip_dur))
                    overall = phase_start + pct * phase_span
                    self.progress.emit(int(overall))
                    self._update_eta(overall)
                elif key == "progress" and value == "end":
                    self.progress.emit(phase_start + phase_span)
                    break
            rc = self._proc.wait()
            if rc != 0 and not self._cancelled:
                stderr_file.seek(0)
                err = stderr_file.read()
                raise RuntimeError(f"ffmpeg exited with code {rc}: {err.strip()[-300:]}")
        finally:
            stderr_file.close()
            try:
                os.unlink(stderr_file.name)
            except OSError:
                pass

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
    return thread, worker
