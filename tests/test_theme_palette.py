import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ThemePaletteTests(unittest.TestCase):
    def test_native_palettes_match_single_source_and_text_is_readable(self):
        spec = importlib.util.spec_from_file_location("theme_generator", ROOT / "scripts/generate-themes.py")
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        for path, contents in generator.generated().items():
            self.assertEqual(path.read_text(), contents)
        palettes = json.loads((ROOT / "shared/catppuccin.json").read_text())
        def luminance(value):
            channels = [int(value[i:i+2], 16) / 255 for i in (1, 3, 5)]
            return sum(weight * (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
                       for c, weight in zip(channels, (0.2126, 0.7152, 0.0722)))
        for palette in palettes.values():
            colors = palette["colors"]
            for foreground in ("text", "muted", "accent", "red"):
                high, low = sorted((luminance(colors[foreground]), luminance(colors["base"])), reverse=True)
                self.assertGreaterEqual((high + 0.05) / (low + 0.05), 4.5)
            self.assertEqual(len(palette["ansi"]), 16)
