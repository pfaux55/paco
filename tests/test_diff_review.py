from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from local_matrix_assistant.ui.diff_review import DiffReviewWidget, diff_stats, parse_unified_diff


MULTI_DIFF = """--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,2 @@
-old = True
+new = True
 keep = 1

--- /dev/null
+++ b/src/health.py
@@ -0,0 +1,2 @@
+def healthy():
+    return True
"""


class DiffParserTests(unittest.TestCase):
    def test_parser_splits_files_and_counts_changes_and_hunks(self) -> None:
        sections = parse_unified_diff(MULTI_DIFF, "2 files")

        self.assertEqual(["src/app.py", "src/health.py"], [section.path for section in sections])
        self.assertEqual((1, 1, 1), (sections[0].additions, sections[0].deletions, sections[0].hunks))
        self.assertEqual((2, 0, 1), (sections[1].additions, sections[1].deletions, sections[1].hunks))
        self.assertEqual((3, 1, 2), diff_stats(MULTI_DIFF))

    def test_header_like_changed_lines_do_not_create_false_file_sections(self) -> None:
        diff = """--- a/example.txt
+++ b/example.txt
@@ -1,2 +1,2 @@
--- a/not-a-header
+++ b/not-a-header
 unchanged
"""

        sections = parse_unified_diff(diff, "example.txt")

        self.assertEqual(1, len(sections))
        self.assertEqual("example.txt", sections[0].path)

    def test_nonstandard_and_empty_diffs_have_explicit_fallback_states(self) -> None:
        sections = parse_unified_diff("custom diff body", "config.toml")

        self.assertEqual(1, len(sections))
        self.assertEqual("config.toml", sections[0].path)
        self.assertEqual([], parse_unified_diff("", "config.toml"))

    def test_truncation_marker_remains_with_the_last_file(self) -> None:
        sections = parse_unified_diff(
            MULTI_DIFF.rstrip() + "\n... combined diff preview truncated.",
            "2 files",
        )

        self.assertEqual(2, len(sections))
        self.assertIn("diff preview truncated", sections[-1].text)


class DiffReviewWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.widget = DiffReviewWidget()
        self.widget.show()

    def tearDown(self) -> None:
        self.widget.close()

    def test_multi_file_review_navigates_between_all_and_individual_files(self) -> None:
        self.widget.set_diff("2 files", MULTI_DIFF)

        self.assertFalse(self.widget.file_selector.isHidden())
        self.assertEqual(3, self.widget.file_selector.count())
        self.assertEqual(MULTI_DIFF, self.widget.current_diff)
        self.assertIn("2 files", self.widget.summary_label.text())
        self.assertIn("+3", self.widget.summary_label.text())

        self.widget.file_selector.setCurrentIndex(2)

        self.assertIn("src/health.py", self.widget.current_diff)
        self.assertNotIn("src/app.py", self.widget.current_diff)
        self.assertIn("1 file", self.widget.summary_label.text())
        self.assertIn("+2", self.widget.summary_label.text())

    def test_copy_and_empty_states_are_explicit(self) -> None:
        self.widget.set_diff("src/app.py", MULTI_DIFF.split("\n\n", 1)[0])
        self.widget.copy_button.click()

        self.assertEqual(self.widget.current_diff, QApplication.clipboard().text())
        self.assertEqual("Copied", self.widget.copy_button.text())

        self.widget.clear()

        self.assertIs(self.widget.empty_label, self.widget.content_stack.currentWidget())
        self.assertFalse(self.widget.copy_button.isEnabled())
        self.assertEqual("No changes", self.widget.summary_label.text())

    def test_highlighter_applies_formats_to_change_lines(self) -> None:
        self.widget.set_diff("src/app.py", MULTI_DIFF.split("\n\n", 1)[0])
        QTest.qWait(10)

        addition = self.widget.diff_view.document().findBlockByLineNumber(4)
        self.assertTrue(addition.layout().formats())


if __name__ == "__main__":
    unittest.main()
