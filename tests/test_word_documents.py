from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from local_matrix_assistant.services.desktop_actions import DesktopActionError, DesktopActionService
from local_matrix_assistant.services.word_documents import WordDocumentService


class WordDocumentServiceTests(unittest.TestCase):
    def test_creates_real_docx_with_styles_headings_and_list_numbering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            desktop = DesktopActionService(folder)
            service = WordDocumentService(desktop)
            action = desktop.parse("create a word document named guide.docx about open source models")

            result = service.create(
                action,  # type: ignore[arg-type]
                "## Overview\nOpen models publish reusable model assets.\n\n"
                "## Evaluation\n- License terms\n- Hardware needs\n\n1. Define requirements\n2. Test candidates",
            )

            destination = Path(result.target)
            self.assertTrue(destination.is_file())
            self.assertEqual(".docx", destination.suffix)
            with ZipFile(destination) as archive:
                names = set(archive.namelist())
                self.assertIn("word/document.xml", names)
                self.assertIn("word/styles.xml", names)
                self.assertIn("word/numbering.xml", names)
                document = archive.read("word/document.xml").decode("utf-8")
                numbering = archive.read("word/numbering.xml").decode("utf-8")
            self.assertIn("Open source models", document)
            self.assertIn("Overview", document)
            self.assertIn('w:numId w:val="1"', document)
            self.assertIn('w:numId w:val="2"', document)
            self.assertIn('w:numFmt w:val="bullet"', numbering)
            self.assertIn('w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"', numbering)

    def test_generated_names_are_unique(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            desktop = DesktopActionService(Path(tmp))
            service = WordDocumentService(desktop)
            action = desktop.parse("create word file")

            first = service.create(action)  # type: ignore[arg-type]
            second = service.create(action)  # type: ignore[arg-type]

            self.assertEqual("document.docx", Path(first.target).name)
            self.assertEqual("document-2.docx", Path(second.target).name)

    def test_explicit_word_filename_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            desktop = DesktopActionService(Path(tmp))
            service = WordDocumentService(desktop)
            action = desktop.parse("create word document named report.docx")
            service.create(action)  # type: ignore[arg-type]

            with self.assertRaisesRegex(DesktopActionError, "not overwritten"):
                service.create(action)  # type: ignore[arg-type]

    def test_active_agent_folder_receives_word_document(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            selected = root / "selected"
            selected.mkdir()
            desktop = DesktopActionService(
                root / "default",
                working_folders=[str(selected)],
                active_working_folder=str(selected),
            )
            action = desktop.parse("create word file")

            result = WordDocumentService(desktop).create(action)  # type: ignore[arg-type]

            self.assertEqual(selected.resolve(), Path(result.target).parent)

    def test_invalid_xml_control_characters_are_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            desktop = DesktopActionService(Path(tmp))
            service = WordDocumentService(desktop)
            action = desktop.parse("create word file")

            result = service.create(action, "Valid\x0b text")  # type: ignore[arg-type]

            with ZipFile(result.target) as archive:
                document = archive.read("word/document.xml").decode("utf-8")
            self.assertIn("Valid text", document)


if __name__ == "__main__":
    unittest.main()
