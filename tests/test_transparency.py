"""Unit tests for the transparency color-key: filter-graph construction and
backdrop detection.

The graph tests assert on command strings rather than running ffmpeg, so
they stay fast and work without a decoder. The detection tests build
synthetic QImages, so they need Qt but not ffmpeg.
"""
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cove_gif_maker import ffmpeg_utils as ff  # noqa: E402


VIDEO = Path("/tmp/clip.mp4")
PALETTE = Path("/tmp/palette.png")
OUT = Path("/tmp/out.gif")


def _vf(cmd: list[str]) -> str:
    """The filter string from a built command, whichever flag carries it."""
    for flag in ("-vf", "-filter_complex"):
        if flag in cmd:
            return cmd[cmd.index(flag) + 1]
    raise AssertionError(f"no filter flag in {cmd}")


class TransparencyModelTest(unittest.TestCase):
    def test_hex_becomes_an_ffmpeg_color_literal(self):
        self.assertEqual(ff.Transparency(color="#32323a").ffmpeg_color(), "0x32323A")
        self.assertEqual(ff.Transparency(color="32323A").ffmpeg_color(), "0x32323A")

    def test_chain_forces_an_alpha_capable_pixel_format_first(self):
        chain = ff.Transparency(color="#00FF00", similarity=0.2, blend=0.0).filter_chain()
        self.assertTrue(chain.startswith("format=rgba,colorkey=0x00FF00:"))

    def test_similarity_is_clamped_away_from_zero(self):
        # colorkey rejects similarity=0; the floor keeps a 0 slider usable.
        chain = ff.Transparency(similarity=0.0, blend=0.0).filter_chain()
        similarity = float(chain.rsplit(":", 2)[1])
        self.assertGreater(similarity, 0.0)

    def test_out_of_range_values_are_clamped(self):
        chain = ff.Transparency(similarity=5.0, blend=-1.0).filter_chain()
        _, similarity, blend = chain.rsplit(":", 2)
        self.assertEqual(float(similarity), 1.0)
        self.assertEqual(float(blend), 0.0)


class GifFilterOrderTest(unittest.TestCase):
    def test_key_lands_after_crop_and_before_scale(self):
        chain = ff.build_gif_filter(
            12, 50, 1.0, crop=(1, 2, 3, 4), transparency=ff.Transparency(),
        )
        parts = chain.split(",")
        crop = next(i for i, p in enumerate(parts) if p.startswith("crop="))
        key = next(i for i, p in enumerate(parts) if p.startswith("colorkey="))
        scale = next(i for i, p in enumerate(parts) if p.startswith("scale="))
        # Scaling AFTER the key is what resamples alpha into a smooth edge.
        self.assertLess(crop, key)
        self.assertLess(key, scale)

    def test_no_key_when_transparency_is_off(self):
        self.assertNotIn("colorkey", ff.build_gif_filter(12, 50, 1.0))
        self.assertNotIn("format=rgba", ff.build_gif_filter(12, 50, 1.0))


class PaletteCommandTest(unittest.TestCase):
    def _palettegen(self, palette_colors, transparency):
        return _vf(ff.build_palettegen_cmd(
            VIDEO, 0.0, 1.0, PALETTE, fps=12, scale_pct=50, speed=1.0,
            palette_colors=palette_colors, transparency=transparency,
        ))

    def test_palettegen_reserves_a_transparent_entry(self):
        self.assertIn("reserve_transparent=1", self._palettegen(128, ff.Transparency()))

    def test_palettegen_leaves_room_for_the_reserved_entry(self):
        # 256 opaque colors plus a transparent one overflows the palette.
        self.assertIn("max_colors=255", self._palettegen(256, ff.Transparency()))
        self.assertIn("max_colors=128", self._palettegen(128, ff.Transparency()))

    def test_opaque_exports_keep_the_full_palette_and_no_reservation(self):
        vf = self._palettegen(256, None)
        self.assertIn("max_colors=256", vf)
        self.assertNotIn("reserve_transparent", vf)

    def test_paletteuse_sets_the_binary_alpha_cutoff(self):
        graph = _vf(ff.build_paletteuse_cmd(
            VIDEO, 0.0, 1.0, PALETTE, OUT, fps=12, scale_pct=50, speed=1.0,
            loop=0, transparency=ff.Transparency(alpha_threshold=200),
        ))
        self.assertIn("alpha_threshold=200", graph)

    def test_paletteuse_omits_the_cutoff_when_opaque(self):
        graph = _vf(ff.build_paletteuse_cmd(
            VIDEO, 0.0, 1.0, PALETTE, OUT, fps=12, scale_pct=50, speed=1.0,
            loop=0,
        ))
        self.assertNotIn("alpha_threshold", graph)

    def test_key_survives_the_caption_and_boomerang_graph(self):
        graph = _vf(ff.build_paletteuse_cmd(
            VIDEO, 0.0, 1.0, PALETTE, OUT, fps=12, scale_pct=50, speed=1.0,
            loop=0, boomerang=True, caption_png=Path("/tmp/cap.png"),
            transparency=ff.Transparency(),
        ))
        self.assertIn("colorkey=", graph)
        self.assertIn("alpha_threshold=", graph)
        # Caption is input 1, palette input 2 — the key must not have
        # displaced the palette's pad reference.
        self.assertIn("[2:v]paletteuse", graph)


class WebpCommandTest(unittest.TestCase):
    def _cmd(self, transparency):
        return ff.build_webp_cmd(
            VIDEO, 0.0, 1.0, OUT, fps=12, scale_pct=50, speed=1.0, loop=0,
            quality=80, transparency=transparency,
        )

    def test_alpha_capable_pixel_format_is_requested(self):
        cmd = self._cmd(ff.Transparency())
        self.assertIn("-pix_fmt", cmd)
        self.assertEqual(cmd[cmd.index("-pix_fmt") + 1], "bgra")

    def test_opaque_webp_leaves_pixel_format_to_ffmpeg(self):
        self.assertNotIn("-pix_fmt", self._cmd(None))

    def test_output_path_stays_last(self):
        # -pix_fmt is inserted before the output; getting that wrong makes
        # ffmpeg treat the format name as the output filename.
        self.assertEqual(self._cmd(ff.Transparency())[-1], str(OUT))


class FrameRateModeFlagTest(unittest.TestCase):
    """ffmpeg dropped the deprecated `-vsync` alias, and release builds
    bundle ffmpeg master — a hardcoded `-vsync` kills every WebP export
    with "Unrecognized option 'vsync'"."""

    def setUp(self):
        self._saved = ff._FPS_MODE_SUPPORTED

    def tearDown(self):
        ff._FPS_MODE_SUPPORTED = self._saved

    def test_modern_ffmpeg_gets_fps_mode(self):
        ff._FPS_MODE_SUPPORTED = True
        self.assertEqual(ff._frame_rate_mode_args(), ["-fps_mode", "passthrough"])

    def test_old_ffmpeg_falls_back_to_vsync(self):
        ff._FPS_MODE_SUPPORTED = False
        self.assertEqual(ff._frame_rate_mode_args(), ["-vsync", "0"])

    def test_webp_command_never_hardcodes_vsync_on_modern_ffmpeg(self):
        ff._FPS_MODE_SUPPORTED = True
        cmd = ff.build_webp_cmd(
            VIDEO, 0.0, 1.0, OUT, fps=12, scale_pct=50, speed=1.0, loop=0,
            quality=80,
        )
        self.assertNotIn("-vsync", cmd)
        self.assertIn("-fps_mode", cmd)
        self.assertEqual(cmd[-1], str(OUT))


class BackdropDetectionTest(unittest.TestCase):
    """Detection needs QImage, so skip cleanly where Qt can't load."""

    @classmethod
    def setUpClass(cls):
        try:
            from PySide6.QtGui import QImage  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise unittest.SkipTest(f"PySide6 unavailable: {exc}")

    @staticmethod
    def _frame(background, subject=None):
        """A 100x100 frame filled with `background`, optionally with a
        50x50 `subject` block centered in it."""
        from PySide6.QtGui import QColor, QImage, QPainter
        img = QImage(100, 100, QImage.Format_RGB32)
        img.fill(QColor(background))
        if subject:
            p = QPainter(img)
            p.fillRect(25, 25, 50, 50, QColor(subject))
            p.end()
        return img

    def test_flat_backdrop_is_detected_with_full_confidence(self):
        from cove_gif_maker import keying
        guess = keying.detect_key_color_in_image(
            self._frame("#32323A", subject="#FF0000")
        )
        self.assertIsNotNone(guess)
        self.assertEqual(guess.color, "#32323A")
        self.assertEqual(guess.confidence, 1.0)

    def test_subject_touching_the_border_lowers_confidence(self):
        from cove_gif_maker import keying
        img = self._frame("#32323A")
        from PySide6.QtGui import QColor, QPainter
        p = QPainter(img)
        p.fillRect(0, 0, 100, 15, QColor("#FF0000"))  # bleeds off the top edge
        p.end()
        guess = keying.detect_key_color_in_image(img)
        self.assertIsNotNone(guess)
        self.assertEqual(guess.color, "#32323A")
        self.assertLess(guess.confidence, 1.0)

    def test_no_flat_backdrop_returns_no_guess(self):
        from cove_gif_maker import keying
        from PySide6.QtGui import QColor, QImage, QPainter
        img = QImage(100, 100, QImage.Format_RGB32)
        p = QPainter(img)
        # A border of many distinct colors — nothing dominant to key on.
        for x in range(100):
            p.fillRect(x, 0, 1, 100, QColor(x * 2 % 256, (x * 5) % 256, (x * 11) % 256))
        p.end()
        self.assertIsNone(keying.detect_key_color_in_image(img))

    def test_compression_jitter_still_reads_as_one_backdrop(self):
        from cove_gif_maker import keying
        from PySide6.QtGui import QColor, QPainter
        img = self._frame("#32323A")
        p = QPainter(img)
        # Nudge scattered border pixels by a couple of levels, the way
        # lossy encoding does to a flat fill.
        for x in range(0, 100, 3):
            p.fillRect(x, 0, 1, 1, QColor("#34323C"))
        p.end()
        guess = keying.detect_key_color_in_image(img)
        self.assertIsNotNone(guess)
        self.assertEqual(guess.color, "#32323A")
        self.assertEqual(guess.confidence, 1.0)


class EyedropperFailureTest(unittest.TestCase):
    """Manual sampling runs off a click, so a broken ffmpeg has to come
    back as None rather than raise through the Qt event handler."""

    @classmethod
    def setUpClass(cls):
        try:
            from PySide6.QtGui import QImage  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise unittest.SkipTest(f"PySide6 unavailable: {exc}")

    def _sample_with(self, boom):
        from cove_gif_maker import ffmpeg_utils, keying
        original = ffmpeg_utils.extract_frame_png
        ffmpeg_utils.extract_frame_png = boom
        try:
            return keying.sample_color_at(VIDEO, 0.0, 10, 10)
        finally:
            ffmpeg_utils.extract_frame_png = original

    def test_missing_ffmpeg_returns_no_color(self):
        from cove_gif_maker import ffmpeg_utils

        def boom(*a, **kw):
            raise ffmpeg_utils.FFmpegMissingError("no ffmpeg")

        self.assertIsNone(self._sample_with(boom))

    def test_a_failed_seek_returns_no_color(self):
        import subprocess

        def boom(*a, **kw):
            raise subprocess.CalledProcessError(1, "ffmpeg")

        self.assertIsNone(self._sample_with(boom))

    def test_a_filesystem_error_returns_no_color(self):
        def boom(*a, **kw):
            raise OSError("disk full")

        self.assertIsNone(self._sample_with(boom))

    def test_a_frame_that_never_appears_returns_no_color(self):
        # ffmpeg "succeeds" but writes nothing.
        self.assertIsNone(self._sample_with(lambda *a, **kw: None))


class EffectiveScaleTest(unittest.TestCase):
    """The `max_px` cap has to survive the scale slider's 10% floor.

    Calls the method unbound against a stub so the test needs the app
    module but not a live window.
    """

    @classmethod
    def setUpClass(cls):
        try:
            from cove_gif_maker import app  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise unittest.SkipTest(f"app module unavailable: {exc}")

    @staticmethod
    def _stub(*, width, height, slider_pct, preset, crop=None):
        """Stands in for MainWindow with only what the method touches."""
        return types.SimpleNamespace(
            scale_slider=types.SimpleNamespace(value=lambda: slider_pct),
            _active_preset=preset,
            _info=types.SimpleNamespace(width=width, height=height),
            _crop_pixels=lambda: crop,
        )

    def _pct(self, **kw):
        from cove_gif_maker.app import MainWindow
        return MainWindow._effective_scale_pct(self._stub(**kw))

    def test_large_source_beats_the_slider_floor(self):
        # 128/1920 is 6.6%, below the slider's 10% minimum.
        pct = self._pct(
            width=1920, height=1080, slider_pct=10, preset="Discord Emote"
        )
        self.assertEqual(pct, 6)
        self.assertLessEqual(1920 * pct / 100, 128)

    def test_cap_never_widens_a_smaller_slider_setting(self):
        pct = self._pct(
            width=1920, height=1080, slider_pct=3, preset="Discord Emote"
        )
        self.assertEqual(pct, 3)

    def test_cap_resolves_against_the_crop_not_the_source(self):
        pct = self._pct(
            width=1920, height=1080, slider_pct=50, preset="Discord Emote",
            crop=(0, 0, 256, 256),
        )
        self.assertEqual(pct, 50)

    def test_source_already_under_the_cap_keeps_the_slider(self):
        pct = self._pct(
            width=100, height=100, slider_pct=50, preset="Discord Emote"
        )
        self.assertEqual(pct, 50)

    def test_presets_without_a_cap_keep_the_slider(self):
        pct = self._pct(width=1920, height=1080, slider_pct=10, preset="Discord")
        self.assertEqual(pct, 10)

    def test_no_active_preset_keeps_the_slider(self):
        pct = self._pct(width=1920, height=1080, slider_pct=10, preset=None)
        self.assertEqual(pct, 10)


class PresetStateTest(unittest.TestCase):
    """Background key detection is housekeeping, not a user edit, so it
    must leave the active preset alone."""

    @classmethod
    def setUpClass(cls):
        try:
            from cove_gif_maker import app  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise unittest.SkipTest(f"app module unavailable: {exc}")

    def test_auto_detect_does_not_clear_the_active_preset(self):
        from cove_gif_maker.app import MainWindow
        seen = []
        stub = types.SimpleNamespace(
            _key_detect_thread=object(),
            _video_path=Path("/tmp/clip.mp4"),
            _key_guess=None,
            _key_detect_hint="",
            _key_color_user_set=False,
            _suppress_preset_reset=False,
            _active_preset="Discord Emote",
            _preset_cards={},
            _sync_transparency_controls=lambda: None,
            _refresh_size_estimate=lambda: None,
        )
        stub._on_setting_changed = lambda: MainWindow._on_setting_changed(stub)
        # The real swatch emits colorChanged on set_hex, which lands in
        # _on_key_color_changed; mirror that wiring.
        stub.key_color_btn = types.SimpleNamespace(
            set_hex=lambda hex_: (
                seen.append(hex_),
                MainWindow._on_key_color_changed(stub),
            )
        )
        guess = types.SimpleNamespace(color="#32323A", confidence=0.84)
        MainWindow._on_key_detected(stub, Path("/tmp/clip.mp4"), guess)

        self.assertEqual(seen, ["#32323A"])
        self.assertEqual(stub._active_preset, "Discord Emote")
        self.assertFalse(stub._suppress_preset_reset)
        # And the guess must not masquerade as a deliberate pick.
        self.assertFalse(stub._key_color_user_set)

    def test_a_manual_color_pick_marks_the_color_as_user_set(self):
        from cove_gif_maker.app import MainWindow
        stub = types.SimpleNamespace(
            _suppress_preset_reset=False,
            _key_color_user_set=False,
            _active_preset=None,
            _preset_cards={},
            _refresh_size_estimate=lambda: None,
        )
        stub._on_setting_changed = lambda: MainWindow._on_setting_changed(stub)
        MainWindow._on_key_color_changed(stub)
        self.assertTrue(stub._key_color_user_set)

    def test_a_programmatic_color_write_does_not(self):
        from cove_gif_maker.app import MainWindow
        stub = types.SimpleNamespace(
            _suppress_preset_reset=True,
            _key_color_user_set=False,
            _active_preset="Discord Emote",
            _preset_cards={},
            _refresh_size_estimate=lambda: None,
        )
        stub._on_setting_changed = lambda: MainWindow._on_setting_changed(stub)
        MainWindow._on_key_color_changed(stub)
        self.assertFalse(stub._key_color_user_set)
        self.assertEqual(stub._active_preset, "Discord Emote")

    def test_a_superseded_detection_result_is_ignored(self):
        from cove_gif_maker.app import MainWindow
        current = object()
        stale = object()
        stub = types.SimpleNamespace(
            _key_detect_thread=current,
            _video_path=Path("/tmp/clip.mp4"),
            _key_guess=None,
            _key_detect_hint="",
            _key_color_user_set=False,
            _suppress_preset_reset=False,
            _active_preset=None,
            _preset_cards={},
            key_color_btn=types.SimpleNamespace(set_hex=lambda _h: None),
            _sync_transparency_controls=lambda: None,
            _refresh_size_estimate=lambda: None,
        )
        guess = types.SimpleNamespace(color="#112233", confidence=0.9)
        MainWindow._on_key_detected(stub, Path("/tmp/clip.mp4"), guess, stale)

        self.assertIsNone(stub._key_guess)
        self.assertIs(stub._key_detect_thread, current)

    def test_a_hand_picked_color_still_clears_the_preset(self):
        from cove_gif_maker.app import MainWindow
        stub = types.SimpleNamespace(
            _suppress_preset_reset=False,
            _active_preset="Discord Emote",
            _preset_cards={},
            _refresh_size_estimate=lambda: None,
        )
        MainWindow._on_setting_changed(stub)
        self.assertIsNone(stub._active_preset)


class TargetSizeScaleFloorTest(unittest.TestCase):
    """A max_px job can start below the usual 20% floor, and the
    target-size ladder still has to be able to shrink it."""

    @classmethod
    def setUpClass(cls):
        try:
            from cove_gif_maker import converter  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise unittest.SkipTest(f"converter unavailable: {exc}")

    @staticmethod
    def _job(**kw):
        from cove_gif_maker.converter import ConvertJob
        base = dict(
            video=VIDEO, output=OUT, start=0.0, end=1.0, fps=15,
            scale_pct=50, speed=1.0, palette_colors=64, loop=0,
            fmt="gif", webp_quality=80, target_size_kb=256,
        )
        base.update(kw)
        return ConvertJob(**base)

    def test_normal_jobs_keep_the_twenty_percent_floor(self):
        from cove_gif_maker.converter import ConvertWorker
        self.assertEqual(ConvertWorker._scale_floor(50), 20)
        self.assertEqual(ConvertWorker._scale_floor(20), 20)

    def test_a_job_starting_below_the_floor_gets_a_lower_one(self):
        from cove_gif_maker.converter import ConvertWorker
        self.assertLess(ConvertWorker._scale_floor(6), 6)
        self.assertGreaterEqual(ConvertWorker._scale_floor(6), 1)

    def test_emote_scale_job_can_still_shrink(self):
        from cove_gif_maker.converter import ConvertWorker
        job = self._job(scale_pct=6, palette_colors=64)
        ConvertWorker._tighten_job(job, overshoot=2.5)
        self.assertLess(job.scale_pct, 6)

    def test_normal_gif_job_stops_shrinking_at_twenty(self):
        from cove_gif_maker.converter import ConvertWorker
        job = self._job(scale_pct=20, palette_colors=64, fps=15)
        ConvertWorker._tighten_job(job, overshoot=2.5)
        self.assertEqual(job.scale_pct, 20)
        self.assertEqual(job.fps, 11)  # falls through to the fps rung

    def test_floor_stays_where_the_job_started(self):
        from cove_gif_maker.converter import ConvertWorker
        job = self._job(scale_pct=8, palette_colors=64, fps=8)
        floor = ConvertWorker._scale_floor(job.scale_pct)
        for _ in range(20):
            ConvertWorker._tighten_job(job, overshoot=2.5, scale_floor=floor)
        # Without the pinned floor this ratchets down toward 1%.
        self.assertEqual(job.scale_pct, floor)
        self.assertEqual(floor, 2)

    def test_ladder_terminates_and_never_stalls(self):
        from cove_gif_maker.converter import ConvertWorker
        job = self._job(scale_pct=6, palette_colors=64, fps=15)
        seen = set()
        for _ in range(40):
            state = (job.palette_colors, job.scale_pct, job.fps)
            if state in seen:
                break
            seen.add(state)
            ConvertWorker._tighten_job(job, overshoot=1.2)
        self.assertGreaterEqual(job.scale_pct, 1)
        self.assertEqual(job.fps, 8)


if __name__ == "__main__":
    unittest.main()
