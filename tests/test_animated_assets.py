from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from xml.etree import ElementTree

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from PySide6.QtWidgets import QApplication

from local_matrix_assistant.ui.animated import AnimatedSvgWidget
from local_matrix_assistant.ui.startup_overlay import StartupOverlay


class AnimatedAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_bundled_svg_animations_are_valid_and_animated(self) -> None:
        asset_dir = SRC / "local_matrix_assistant" / "assets" / "svg-spinners"

        for filename in ("180-ring.svg", "270-ring-with-bg.svg"):
            with self.subTest(filename=filename):
                widget = AnimatedSvgWidget(asset_dir / filename)
                self.assertTrue(widget.is_valid)
                self.assertTrue(widget.is_animated)
                widget.close()
                widget.deleteLater()

    def test_animation_stops_while_hidden(self) -> None:
        asset = SRC / "local_matrix_assistant" / "assets" / "svg-spinners" / "180-ring.svg"
        widget = AnimatedSvgWidget(asset)
        widget.show()
        self.app.processEvents()
        self.assertTrue(widget._renderer.isAnimationEnabled())

        widget.hide()
        self.app.processEvents()
        self.assertFalse(widget._renderer.isAnimationEnabled())

        widget.show()
        self.app.processEvents()
        self.assertTrue(widget.is_valid)
        self.assertTrue(widget.is_animated)
        self.assertTrue(widget._renderer.isAnimationEnabled())
        widget.close()
        widget.deleteLater()

    def test_assets_contain_no_scripts_or_remote_references(self) -> None:
        asset_dir = SRC / "local_matrix_assistant" / "assets" / "svg-spinners"

        for asset in asset_dir.glob("*.svg"):
            with self.subTest(asset=asset.name):
                root = ElementTree.parse(asset).getroot()
                for element in root.iter():
                    tag = element.tag.rsplit("}", 1)[-1].lower()
                    self.assertNotEqual("script", tag)
                    for name, value in element.attrib.items():
                        self.assertNotIn(name.rsplit("}", 1)[-1].lower(), {"href", "src"})
                        self.assertNotIn("url(", value.lower())

    def test_startup_overlay_uses_the_bundled_animation(self) -> None:
        overlay = StartupOverlay("JARVIS")
        self.assertTrue(overlay._pulse_indicator.is_valid)
        self.assertTrue(overlay._pulse_indicator.is_animated)
        overlay.close()
        overlay.deleteLater()


if __name__ == "__main__":
    unittest.main()
