"""Unit tests for the transparency color-key: filter-graph construction and
backdrop detection.

The graph tests assert on command strings rather than running ffmpeg, so
they stay fast and work without a decoder. The detection tests build
synthetic QImages, so they need Qt but not ffmpeg.
"""
import sys
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


if __name__ == "__main__":
    unittest.main()
