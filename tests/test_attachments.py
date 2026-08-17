from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from local_matrix_assistant.core.models import ChatMessage
from local_matrix_assistant.services.attachments import AttachmentError, AttachmentService


class AttachmentServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = AttachmentService()

    def test_loads_utf8_text_as_bounded_local_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "example.py"
            path.write_text("print('hello')\n", encoding="utf-8")

            attachment = self.service.load(str(path))

            self.assertEqual("example.py", attachment.name)
            self.assertEqual("print('hello')\n", attachment.content)
            self.assertFalse(attachment.truncated)
            self.assertEqual(str(path), attachment.path)
            self.assertNotIn("path", attachment.metadata())

    def test_rejects_binary_and_oversized_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "image.bin"
            binary.write_bytes(b"image\x00payload")
            with self.assertRaisesRegex(AttachmentError, "binary"):
                self.service.load(str(binary))

            oversized = Path(tmp) / "large.txt"
            with oversized.open("wb") as handle:
                handle.truncate(self.service.max_source_bytes + 1)
            with self.assertRaisesRegex(AttachmentError, "2 MB"):
                self.service.load(str(oversized))

    def test_extracts_word_document_paragraphs_without_external_services(self) -> None:
        document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Open source models</w:t></w:r></w:p>
    <w:p><w:r><w:t>Local and private</w:t></w:r></w:p>
  </w:body>
</w:document>"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "models.docx"
            with ZipFile(path, "w") as archive:
                archive.writestr("word/document.xml", document_xml)

            attachment = self.service.load(str(path))

            self.assertEqual("Word document", attachment.kind)
            self.assertEqual("Open source models\nLocal and private", attachment.content)

    def test_extracts_selectable_pdf_text_with_page_markers(self) -> None:
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "guide.pdf"
            writer = PdfWriter()
            page = writer.add_blank_page(width=612, height=792)
            font = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
            page[NameObject("/Resources")] = DictionaryObject(
                {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
            )
            stream = DecodedStreamObject()
            stream.set_data(b"BT /F1 12 Tf 72 720 Td (Paco PDF extraction works) Tj ET")
            page[NameObject("/Contents")] = writer._add_object(stream)
            with path.open("wb") as handle:
                writer.write(handle)

            attachment = self.service.load(str(path))

            self.assertEqual("PDF document", attachment.kind)
            self.assertIn("[Page 1]", attachment.content)
            self.assertIn("Paco PDF extraction works", attachment.content)

    def test_rejects_pdf_without_selectable_text(self) -> None:
        from pypdf import PdfWriter

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scan.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            with path.open("wb") as handle:
                writer.write(handle)

            with self.assertRaisesRegex(AttachmentError, "no selectable text"):
                self.service.load(str(path))

    def test_image_is_resized_and_encoded_as_bounded_jpeg_snapshot(self) -> None:
        import base64

        from PySide6.QtGui import QColor, QImage

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "diagram.png"
            image = QImage(2000, 1000, QImage.Format.Format_ARGB32)
            image.fill(QColor("#23c976"))
            self.assertTrue(image.save(str(path), "PNG"))

            attachment = self.service.load(str(path))

            self.assertEqual("image", attachment.kind)
            self.assertEqual(1600, attachment.width)
            self.assertEqual(800, attachment.height)
            encoded = base64.b64decode(attachment.image_data)
            thumbnail = base64.b64decode(attachment.thumbnail_data)
            self.assertTrue(encoded.startswith(b"\xff\xd8"))
            self.assertTrue(thumbnail.startswith(b"\xff\xd8"))
            self.assertLessEqual(len(encoded), self.service.max_image_encoded_bytes)
            self.assertLessEqual(len(thumbnail), self.service.max_thumbnail_encoded_bytes)
            self.assertNotIn("path", attachment.metadata())

    def test_clipboard_image_becomes_an_in_memory_attachment(self) -> None:
        from PySide6.QtGui import QColor, QImage

        image = QImage(80, 40, QImage.Format.Format_ARGB32)
        image.fill(QColor("blue"))

        attachment = self.service.load_clipboard_image(image)

        self.assertEqual("clipboard-image.png", attachment.name)
        self.assertEqual("image", attachment.kind)
        self.assertEqual("image/jpeg", attachment.media_type)
        self.assertEqual((80, 40), (attachment.width, attachment.height))
        self.assertTrue(attachment.image_data)
        self.assertTrue(attachment.thumbnail_data)
        self.assertFalse(Path(attachment.path).is_absolute())

    def test_truncates_snapshot_to_remaining_conversation_allowance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notes.txt"
            path.write_text("abcdefghij", encoding="utf-8")

            attachment = self.service.load(str(path), available_characters=5)

            self.assertTrue(attachment.truncated)
            self.assertTrue(attachment.content.startswith("abcde"))
            self.assertIn("truncated", attachment.content)

    def test_augment_message_preserves_display_text_and_adds_untrusted_file_context(self) -> None:
        message = ChatMessage(
            "user",
            "Explain this code",
            "now",
            metadata={
                "attachments": [
                    {
                        "name": "main.py",
                        "size_bytes": 12,
                        "content": "print('ok')",
                        "kind": "text",
                        "truncated": False,
                    }
                ]
            },
        )

        augmented = self.service.augment_message(message)

        self.assertEqual("Explain this code", message.content)
        self.assertIn("untrusted data", augmented.content)
        self.assertIn('ATTACHMENT 1: "main.py"', augmented.content)
        self.assertIn("print('ok')", augmented.content)
        self.assertEqual(message.metadata, augmented.metadata)

    def test_corrupt_history_attachment_metadata_is_bounded_and_normalized(self) -> None:
        metadata = {
            "attachments": [
                {"name": f" file-{index}.txt\n", "content": "x" * 70_000, "size_bytes": "invalid"}
                for index in range(8)
            ]
        }

        attachments = self.service.metadata_attachments(metadata)

        self.assertEqual(self.service.max_files, len(attachments))
        self.assertLessEqual(sum(len(item["content"]) for item in attachments), 160_000)
        self.assertEqual("file-0.txt", attachments[0]["name"])
        self.assertEqual(0, attachments[0]["size_bytes"])
        self.assertTrue(attachments[0]["truncated"])

    def test_persisted_snapshot_can_be_restored_for_edit_and_resend_without_source_path(self) -> None:
        metadata = {
            "attachments": [
                {
                    "name": "notes.md",
                    "size_bytes": 18,
                    "content": "# Saved snapshot",
                    "kind": "text",
                    "truncated": False,
                }
            ]
        }

        restored = self.service.local_attachments_from_metadata(metadata, key_prefix="turn-2")

        self.assertEqual(1, len(restored))
        self.assertEqual("notes.md", restored[0].name)
        self.assertEqual("# Saved snapshot", restored[0].content)
        self.assertTrue(restored[0].path.startswith(".paco-history/turn-2"))


if __name__ == "__main__":
    unittest.main()
