from __future__ import annotations

import os
import base64
import unittest
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from PySide6.QtCore import QByteArray, QBuffer, QCoreApplication, QEvent, QIODevice, QMimeData, QPoint, QPointF, QUrl
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QImage, QKeyEvent, QWheelEvent
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt

from local_matrix_assistant.ui.chat_panel import ChatPanel
from local_matrix_assistant.core.models import ChatMessage
from local_matrix_assistant.services.attachments import LocalAttachment
from local_matrix_assistant.ui.main_window_chat import ChatWindowMixin
from local_matrix_assistant.ui.inputs import ClipboardShortcutFilter
from local_matrix_assistant.ui.widgets import MessageBubble


class ChatScrollHarness(ChatWindowMixin):
    pass


class ChatPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.panel = ChatPanel()
        self.panel.show()

    def tearDown(self) -> None:
        self.panel.close()

    def test_chat_header_uses_paco_mark(self) -> None:
        self.assertFalse(self.panel.paco_mark.pixmap().isNull())
        self.assertEqual("Paco chat", self.panel.paco_mark.accessibleName())

    def test_send_button_tracks_input_presence(self) -> None:
        self.assertFalse(self.panel.send_button.isEnabled())
        self.panel.input_box.setPlainText("hello")
        QTest.qWait(10)
        self.assertTrue(self.panel.send_button.isEnabled())
        self.panel.input_box.clear()
        QTest.qWait(10)
        self.assertFalse(self.panel.send_button.isEnabled())

    def test_enter_submits_and_shift_enter_inserts_newline(self) -> None:
        submits: list[str] = []
        self.panel.input_box.submit_requested.connect(lambda: submits.append("submit"))
        self.panel.input_box.setFocus()
        QTest.keyClicks(self.panel.input_box, "hello")
        QTest.keyClick(self.panel.input_box, Qt.Key_Return)
        self.assertEqual(["submit"], submits)

        self.panel.input_box.clear()
        submits.clear()
        QTest.keyClicks(self.panel.input_box, "hello")
        QTest.keyClick(self.panel.input_box, Qt.Key_Return, Qt.ShiftModifier)
        self.assertEqual([], submits)
        self.assertIn("\n", self.panel.input_box.toPlainText())

    def test_ctrl_c_and_ctrl_v_copy_and_paste_in_chat_composer(self) -> None:
        self.panel.input_box.setPlainText("copy this")
        self.panel.input_box.selectAll()
        self.panel.input_box.setFocus()

        QTest.keyClick(self.panel.input_box, Qt.Key_C, Qt.ControlModifier)
        self.assertEqual("copy this", QApplication.clipboard().text())

        self.panel.input_box.clear()
        QApplication.clipboard().setText("paste this")
        QTest.keyClick(self.panel.input_box, Qt.Key_V, Qt.ControlModifier)
        self.assertEqual("paste this", self.panel.input_box.toPlainText())

    def test_application_filter_handles_ctrl_v_before_window_shortcuts(self) -> None:
        editor = QLineEdit()
        QApplication.clipboard().setText("paste from application filter")
        event = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_V,
            Qt.KeyboardModifier.ControlModifier,
        )

        handled = ClipboardShortcutFilter().eventFilter(editor, event)

        self.assertTrue(handled)
        self.assertEqual("paste from application filter", editor.text())

    def test_voice_only_mode_switches_content_screen(self) -> None:
        self.assertFalse(self.panel.voice_only_mode_active())
        self.panel.show_voice_only_mode(True)
        QTest.qWait(10)
        self.assertTrue(self.panel.voice_only_mode_active())
        self.panel.show_voice_only_mode(False)
        QTest.qWait(10)
        self.assertFalse(self.panel.voice_only_mode_active())

    def test_model_profile_selector_exposes_all_routing_modes(self) -> None:
        profiles = [
            self.panel.model_profile_combo.itemData(index)
            for index in range(self.panel.model_profile_combo.count())
        ]

        self.assertEqual(["auto", "fast", "balanced", "coding", "reasoning", "manual"], profiles)
        self.panel.set_model_profile("coding")
        self.assertEqual("coding", self.panel.current_model_profile())

    def test_think_button_uses_a_monochrome_icon_and_is_checkable(self) -> None:
        self.assertTrue(self.panel.think_button.isCheckable())
        self.assertTrue(self.panel.think_button.icon().isNull() is False)
        self.assertFalse(self.panel.think_button.isChecked())

        self.panel.think_button.click()

        self.assertTrue(self.panel.think_button.isChecked())
        self.assertEqual("Think", self.panel.think_button.text())
        self.assertLess(
            self.panel.left_action_stack.indexOf(self.panel.think_button),
            self.panel.left_action_stack.indexOf(self.panel.attach_button),
        )

    def test_history_rows_elide_without_a_horizontal_scrollbar(self) -> None:
        self.assertEqual(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
            self.panel.history_list.horizontalScrollBarPolicy(),
        )
        self.assertEqual(Qt.TextElideMode.ElideRight, self.panel.history_list.textElideMode())

    def test_compact_mode_wraps_composer_controls_and_restores_wide_layout(self) -> None:
        layout = self.panel.composer_controls_layout

        self.panel.set_compact_mode(True)

        cancel_position = layout.getItemPosition(layout.indexOf(self.panel.cancel_button))
        model_position = layout.getItemPosition(layout.indexOf(self.panel.model_profile_combo))
        self.assertEqual(1, cancel_position[0])
        self.assertEqual(0, model_position[0])
        self.assertTrue(self.panel._compact_mode)

        self.panel.set_compact_mode(False)

        positions = [layout.getItemPosition(layout.indexOf(widget))[0] for widget in self.panel._composer_control_widgets]
        self.assertEqual([0, 0, 0, 0, 0], positions)

    def test_context_note_wraps_and_tracks_visibility(self) -> None:
        self.panel.set_context_note("Context adjusted because a long local-model request exceeded the input budget.")

        self.assertTrue(self.panel.context_note.isVisible())
        self.assertTrue(self.panel.context_note.wordWrap())

        self.panel.set_context_note("")
        self.assertFalse(self.panel.context_note.isVisible())

    def test_attachment_tray_enables_send_and_emits_remove_request(self) -> None:
        attachment = LocalAttachment(
            path="C:/work/example.py",
            name="example.py",
            size_bytes=24,
            content="print('attached')",
        )
        removed: list[str] = []
        self.panel.attachment_remove_requested.connect(removed.append)

        self.panel.set_pending_attachments([attachment])

        self.assertFalse(self.panel.attachment_tray.isHidden())
        self.assertTrue(self.panel.send_button.isEnabled())
        row = self.panel.attachment_items_layout.itemAt(0).widget()
        remove_button = row.findChild(type(self.panel.send_button), "attachmentRemoveButton")
        self.assertIsNotNone(remove_button)
        remove_button.click()
        self.assertEqual([attachment.path], removed)

        self.panel.set_pending_attachments([])
        self.assertTrue(self.panel.attachment_tray.isHidden())
        self.assertFalse(self.panel.send_button.isEnabled())

    def test_message_input_detects_local_file_urls_for_drop(self) -> None:
        mime_data = QMimeData()
        mime_data.setUrls([QUrl.fromLocalFile("C:/work/example.py"), QUrl("https://example.com")])

        self.assertEqual(["C:/work/example.py"], self.panel.input_box._local_file_paths(mime_data))
        self.assertTrue(self.panel.composer_panel.acceptDrops())

    def test_drop_over_chat_scroll_reaches_page_file_handler(self) -> None:
        mime_data = QMimeData()
        mime_data.setUrls([QUrl.fromLocalFile("C:/work/example.py")])
        dropped: list[list[str]] = []
        self.panel.file_paths_dropped.connect(dropped.append)
        target = self.panel.chat_scroll.viewport()
        drag_event = QDragEnterEvent(
            QPoint(5, 5),
            Qt.DropAction.CopyAction,
            mime_data,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        drop_event = QDropEvent(
            QPointF(5, 5),
            Qt.DropAction.CopyAction,
            mime_data,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )

        QCoreApplication.sendEvent(target, drag_event)
        QCoreApplication.sendEvent(target, drop_event)

        self.assertTrue(drag_event.isAccepted())
        self.assertTrue(drop_event.isAccepted())
        self.assertEqual([["C:/work/example.py"]], dropped)

    def test_message_input_emits_clipboard_image_without_inserting_text(self) -> None:
        image = QImage(24, 16, QImage.Format.Format_RGB32)
        image.fill(QColor("green"))
        mime_data = QMimeData()
        mime_data.setImageData(image)
        pasted: list[QImage] = []
        self.panel.input_box.clipboard_image_pasted.connect(pasted.append)

        self.panel.input_box.insertFromMimeData(mime_data)

        self.assertEqual(1, len(pasted))
        self.assertEqual((24, 16), (pasted[0].width(), pasted[0].height()))
        self.assertEqual("", self.panel.input_box.toPlainText())

    def test_message_input_preserves_normal_text_paste(self) -> None:
        mime_data = QMimeData()
        mime_data.setText("normal clipboard text")

        self.panel.input_box.insertFromMimeData(mime_data)

        self.assertEqual("normal clipboard text", self.panel.input_box.toPlainText())

    def test_attachment_tray_height_is_bounded_for_five_files(self) -> None:
        attachments = [
            LocalAttachment(
                path=f"C:/work/file-{index}.txt",
                name=f"file-{index}.txt",
                size_bytes=10,
                content="content",
            )
            for index in range(5)
        ]

        self.panel.set_pending_attachments(attachments)

        self.assertEqual(5, self.panel.attachment_count())
        self.assertLessEqual(self.panel.attachment_scroll.height(), 106)
        self.assertTrue(all(row.isVisible() for row, _preview, _label, _button in self.panel._attachment_rows))

    def test_image_attachment_shows_local_thumbnail(self) -> None:
        image = QImage(16, 16, QImage.Format.Format_RGB32)
        image.fill(QColor("green"))
        payload = QByteArray()
        buffer = QBuffer(payload)
        self.assertTrue(buffer.open(QIODevice.OpenModeFlag.WriteOnly))
        self.assertTrue(image.save(buffer, "JPEG"))
        buffer.close()
        attachment = LocalAttachment(
            path="C:/work/screen.png",
            name="screen.png",
            size_bytes=100,
            content="Local image snapshot.",
            kind="image",
            media_type="image/jpeg",
            image_data=base64.b64encode(bytes(payload)).decode("ascii"),
            width=16,
            height=16,
        )

        self.panel.set_pending_attachments([attachment])

        _row, preview, label, _button = self.panel._attachment_rows[0]
        self.assertFalse(preview.isHidden())
        self.assertFalse(preview.pixmap().isNull())
        self.assertIn("Image", label.text())

    def test_message_edit_banner_warns_about_replaced_history_and_can_cancel(self) -> None:
        canceled: list[bool] = []
        self.panel.edit_cancel_requested.connect(lambda: canceled.append(True))

        self.panel.set_message_edit_state(True, later_messages=3)

        self.assertFalse(self.panel.edit_message_banner.isHidden())
        self.assertIn("remove 3 later messages", self.panel.edit_message_label.text())
        self.panel.cancel_message_edit_button.click()
        self.assertEqual([True], canceled)

        self.panel.set_message_edit_state(False)
        self.assertTrue(self.panel.edit_message_banner.isHidden())

    def test_streaming_follows_latest_only_while_reader_is_near_the_bottom(self) -> None:
        self.panel.resize(700, 520)
        tall_history = QLabel("Earlier message")
        tall_history.setFixedHeight(1_200)
        self.panel.chat_layout.insertWidget(self.panel.chat_layout.count() - 1, tall_history)
        self.panel.chat_container.setMinimumHeight(1_500)
        pending = MessageBubble(
            ChatMessage(
                "assistant",
                "Thinking...",
                "now",
                metadata={"pending": True},
            )
        )
        self.panel.chat_layout.insertWidget(self.panel.chat_layout.count() - 1, pending)
        self.app.processEvents()

        harness = ChatScrollHarness()
        harness.chat_panel = self.panel
        harness.history_store = type(
            "History",
            (),
            {"now_stamp": staticmethod(lambda: "now")},
        )()
        harness._pending_assistant_bubble = pending
        harness._pending_assistant_text = "Streaming response\n" + ("More detail\n" * 30)
        harness._active_reply_metadata = {}

        bar = self.panel.chat_scroll.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        bar.setValue(0)
        harness._flush_pending_stream_render()
        self.app.processEvents()

        self.assertEqual(0, bar.value())
        self.assertFalse(self.panel.jump_to_latest_button.isHidden())

        self.panel.jump_to_latest_button.click()
        QTest.qWait(260)
        self.assertEqual(bar.maximum(), bar.value())
        self.assertTrue(self.panel.jump_to_latest_button.isHidden())

        harness._pending_assistant_text += "Latest tail\n" * 40
        harness._flush_pending_stream_render()
        QTest.qWait(260)
        self.assertEqual(bar.maximum(), bar.value())

    def test_chat_wheel_scroll_animates_to_its_target(self) -> None:
        self.panel.resize(700, 520)
        self.panel.chat_container.setMinimumHeight(1_500)
        self.app.processEvents()
        bar = self.panel.chat_scroll.verticalScrollBar()
        bar.setValue(200)
        wheel_event = QWheelEvent(
            QPointF(10, 10),
            QPointF(10, 10),
            QPoint(),
            QPoint(0, -120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.ScrollUpdate,
            False,
        )

        QCoreApplication.sendEvent(self.panel.chat_scroll.viewport(), wheel_event)

        self.assertTrue(wheel_event.isAccepted())
        QTest.qWait(210)
        self.assertGreater(bar.value(), 200)


if __name__ == "__main__":
    unittest.main()
