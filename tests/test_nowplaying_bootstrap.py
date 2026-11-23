"""Regression checks for the experimental Material Skin Now Playing boot order."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "MaterialSkin" / "HTML" / "material" / "index.html"

class NowPlayingBootstrapTest(unittest.TestCase):
    def test_nowplaying_handler_is_loaded_once_early(self):
        html = HTML.read_text(encoding="utf-8")
        self.assertEqual(html.count('html/js/nowplaying-functions.js'), 1)
        self.assertLess(html.index('html/js/nowplaying-page.js'), html.index('html/js/nowplaying-functions.js'))
        self.assertLess(html.index('html/js/nowplaying-functions.js'), html.index('html/js/main.js'))

    def test_no_duplicate_deferred_load(self):
        html = HTML.read_text(encoding="utf-8")
        self.assertNotIn('"nowplaying-functions"', html)
        self.assertIn('function loadOtherFiles()', html)

if __name__ == "__main__":
    unittest.main()
