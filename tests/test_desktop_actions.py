from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from local_matrix_assistant.core.models import WebSearchResponse
from local_matrix_assistant.services.command_router import explicit_web_search_query
from local_matrix_assistant.services.desktop_actions import DesktopActionError, DesktopActionService
from local_matrix_assistant.ui.main_window_chat import ChatWindowMixin


class CommandRouterTests(unittest.TestCase):
    def test_explicit_web_search_extracts_query(self) -> None:
        self.assertEqual("current weather in Toronto", explicit_web_search_query("Search the web for current weather in Toronto"))
        self.assertEqual("Python 3.14 news", explicit_web_search_query("please web search: Python 3.14 news"))

    def test_normal_chat_does_not_trigger_web_search(self) -> None:
        self.assertIsNone(explicit_web_search_query("How does web search work?"))

    def test_explicit_search_enables_web_and_uses_clean_query(self) -> None:
        class FakeButton:
            def __init__(self) -> None:
                self.checked = False
                self.enabled = True

            def isChecked(self) -> bool:
                return self.checked

            def setEnabled(self, enabled: bool) -> None:
                self.enabled = enabled

        class FakePanel:
            def __init__(self) -> None:
                self.web_search_button = FakeButton()
                self.voice_button = FakeButton()
                self.cancel_button = FakeButton()
                self.send_button = FakeButton()

        class FakeAgentPanel:
            busy = False

            def set_busy(self, busy: bool) -> None:
                self.busy = busy

        class FakeSearchService:
            query = ""

            def search(self, query: str, *, should_cancel) -> WebSearchResponse:
                self.query = query
                return WebSearchResponse(
                    provider="test",
                    query=query,
                    results=[],
                    canceled=should_cancel(),
                )

        class ImmediateRunner:
            @staticmethod
            def start_stream(worker, on_chunk, on_result, _on_error) -> None:
                on_result(worker.fn(on_chunk, worker.is_cancelled))

        class FakeWindow(ChatWindowMixin):
            def __init__(self) -> None:
                self.chat_panel = FakePanel()
                self.agent_panel = FakeAgentPanel()
                self.web_search_service = FakeSearchService()
                self.task_runner = ImmediateRunner()
                self.received_search = None
                self._awaiting_response = False

            def _on_web_search_toggled(self, checked: bool) -> None:
                self.chat_panel.web_search_button.checked = checked

            def _set_activity(self, _message: str) -> None:
                pass

            def _request_assistant_response(self, _model: str, search_response) -> None:
                self.received_search = search_response

            def _update_send_enabled_state(self) -> None:
                pass

        window = FakeWindow()
        window._begin_assistant_response("model", "search the web for Toronto weather")

        self.assertTrue(window.chat_panel.web_search_button.checked)
        self.assertEqual("Toronto weather", window.web_search_service.query)
        self.assertIsNotNone(window.received_search)
        self.assertTrue(window._awaiting_response)
        self.assertTrue(window.agent_panel.busy)


class DesktopActionServiceTests(unittest.TestCase):
    def test_workspace_root_returns_active_existing_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = DesktopActionService(
                root / "default",
                working_folders=[str(root)],
                active_working_folder=str(root),
            )

            self.assertEqual(root.resolve(), service.workspace_root())

    def test_workspace_root_rejects_missing_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing"
            service = DesktopActionService(
                missing,
                working_folders=[str(missing)],
                active_working_folder=str(missing),
            )

            with self.assertRaisesRegex(DesktopActionError, "existing folder"):
                service.workspace_root()

    def test_natural_word_document_command_extracts_topic_and_filename(self) -> None:
        service = DesktopActionService()

        action = service.parse("create a word document outlining open source models")

        self.assertEqual("create_word_document", action.kind)  # type: ignore[union-attr]
        self.assertEqual("open-source-models.docx", action.target)  # type: ignore[union-attr]
        self.assertEqual("Open source models", action.title)  # type: ignore[union-attr]
        self.assertTrue(action.generate_content)  # type: ignore[union-attr]

    def test_short_word_file_command_creates_blank_document_action(self) -> None:
        service = DesktopActionService()

        action = service.parse("create word file")

        self.assertEqual("create_word_document", action.kind)  # type: ignore[union-attr]
        self.assertEqual("document.docx", action.target)  # type: ignore[union-attr]
        self.assertFalse(action.generate_content)  # type: ignore[union-attr]

    def test_named_word_document_keeps_filename_and_topic(self) -> None:
        service = DesktopActionService()

        action = service.parse('create a Word document named "model guide.docx" about open source models')

        self.assertEqual("model guide.docx", action.target)  # type: ignore[union-attr]
        self.assertEqual("Open source models", action.title)  # type: ignore[union-attr]
        self.assertFalse(action.auto_unique)  # type: ignore[union-attr]

    def test_unmatched_word_document_name_quote_is_rejected(self) -> None:
        service = DesktopActionService()

        with self.assertRaisesRegex(DesktopActionError, "unmatched quote"):
            service.parse('create a Word document named "report.docx')

    def test_create_relative_file_with_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = DesktopActionService(Path(tmp))
            action = service.parse('create a file called notes.txt with content "Buy milk"')

            self.assertIsNotNone(action)
            result = service.execute(action)  # type: ignore[arg-type]

            destination = Path(tmp) / "notes.txt"
            self.assertEqual("Buy milk", destination.read_text(encoding="utf-8"))
            self.assertEqual(str(destination.resolve()), result.target)

    def test_create_file_without_name_uses_unique_default_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = DesktopActionService(Path(tmp))

            first = service.parse("create file")
            second = service.parse("create a new file.")
            service.execute(first)  # type: ignore[arg-type]
            service.execute(second)  # type: ignore[arg-type]

            self.assertTrue((Path(tmp) / "untitled.txt").exists())
            self.assertTrue((Path(tmp) / "untitled-2.txt").exists())
            self.assertTrue(first.auto_unique)  # type: ignore[union-attr]

    def test_filename_with_spaces_and_content_punctuation_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = DesktopActionService(Path(tmp))
            action = service.parse('create file "meeting notes.txt" with content "Ready now."')

            self.assertEqual("meeting notes.txt", action.target)  # type: ignore[union-attr]
            self.assertEqual("Ready now.", action.content)  # type: ignore[union-attr]
            service.execute(action)  # type: ignore[arg-type]

            self.assertEqual("Ready now.", (Path(tmp) / "meeting notes.txt").read_text(encoding="utf-8"))

    def test_multiline_content_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = DesktopActionService(Path(tmp))
            action = service.parse("create file script.py with content:\ndef run():\n    return True")
            service.execute(action)  # type: ignore[arg-type]

            self.assertEqual("def run():\n    return True", (Path(tmp) / "script.py").read_text(encoding="utf-8"))

    def test_content_separator_inside_quoted_filename_is_ignored(self) -> None:
        service = DesktopActionService()
        action = service.parse('create file "notes containing ideas.txt" with content hello')

        self.assertEqual("notes containing ideas.txt", action.target)  # type: ignore[union-attr]
        self.assertEqual("hello", action.content)  # type: ignore[union-attr]

    def test_apostrophe_in_unquoted_filename_does_not_hide_content(self) -> None:
        service = DesktopActionService()
        action = service.parse("create file John's notes.txt with content ready")

        self.assertEqual("John's notes.txt", action.target)  # type: ignore[union-attr]
        self.assertEqual("ready", action.content)  # type: ignore[union-attr]

    def test_unmatched_filename_quote_is_rejected(self) -> None:
        service = DesktopActionService()
        with self.assertRaisesRegex(DesktopActionError, "unmatched quote"):
            service.parse('create file "notes.txt with content hello')

    def test_unquoted_filename_containing_with_is_not_split_as_content(self) -> None:
        service = DesktopActionService()
        action = service.parse("create file notes with spaces.txt")

        self.assertEqual("notes with spaces.txt", action.target)  # type: ignore[union-attr]
        self.assertEqual("", action.content)  # type: ignore[union-attr]

    def test_dotfile_does_not_receive_txt_extension(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = DesktopActionService(Path(tmp))
            action = service.parse("create file .gitignore with content cache/")
            service.execute(action)  # type: ignore[arg-type]

            self.assertTrue((Path(tmp) / ".gitignore").exists())
            self.assertFalse((Path(tmp) / ".gitignore.txt").exists())

    def test_create_file_adds_txt_extension_and_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = DesktopActionService(Path(tmp))
            action = service.parse("make a new file named ideas")
            service.execute(action)  # type: ignore[arg-type]

            self.assertTrue((Path(tmp) / "ideas.txt").exists())
            with self.assertRaisesRegex(DesktopActionError, "not overwritten"):
                service.execute(action)  # type: ignore[arg-type]

    def test_selected_working_folder_receives_relative_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            default_folder = root / "default"
            working_folder = root / "project"
            service = DesktopActionService(
                default_folder,
                working_folders=[str(working_folder)],
                active_working_folder=str(working_folder),
            )

            action = service.parse("create file result.txt with content ready")
            service.execute(action)  # type: ignore[arg-type]

            self.assertEqual("ready", (working_folder / "result.txt").read_text(encoding="utf-8"))
            self.assertFalse((default_folder / "result.txt").exists())

    def test_locked_root_ignores_saved_and_runtime_folder_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sandbox = root / "sandbox"
            outside = root / "outside"
            outside.mkdir()
            service = DesktopActionService(
                working_folders=[str(outside)],
                active_working_folder=str(outside),
                locked_root=sandbox,
            )

            service.update_working_folders([str(outside)], str(outside))
            action = service.parse("create file result.txt with content ready")
            service.execute(action)  # type: ignore[arg-type]

            self.assertEqual(sandbox.resolve(), service.active_working_folder)
            self.assertEqual([sandbox.resolve()], service.working_folders)
            self.assertEqual("ready", (sandbox / "result.txt").read_text(encoding="utf-8"))
            with self.assertRaisesRegex(DesktopActionError, "Agent tab"):
                service.execute(  # type: ignore[arg-type]
                    service.parse(f'create file "{outside / "blocked.txt"}"')
                )

    def test_absolute_file_path_must_be_in_allowed_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = DesktopActionService(root / "default", working_folders=[str(root / "allowed")])
            action = service.parse(f'create file "{root / "outside" / "blocked.txt"}"')

            with self.assertRaisesRegex(DesktopActionError, "Agent tab"):
                service.execute(action)  # type: ignore[arg-type]

    def test_relative_path_cannot_escape_active_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = DesktopActionService(Path(tmp) / "allowed")
            action = service.parse("create file ../escaped.txt")

            with self.assertRaisesRegex(DesktopActionError, "active working folder"):
                service.execute(action)  # type: ignore[arg-type]

    def test_open_known_app_uses_direct_launcher(self) -> None:
        service = DesktopActionService()
        action = service.parse("could you please open the calculator app for me")

        with patch.object(service, "_launch_target") as launch:
            result = service.execute(action)  # type: ignore[arg-type]

        launch.assert_called_once_with("calc.exe")
        self.assertEqual("open_app", result.kind)

    def test_polite_suffixes_are_removed_from_app_name(self) -> None:
        service = DesktopActionService()
        action = service.parse("open Notepad please for me")

        self.assertEqual("Notepad", action.target)  # type: ignore[union-attr]

    def test_shell_syntax_is_rejected_as_an_app_name(self) -> None:
        service = DesktopActionService()
        action = service.parse("open notepad & calculator")

        with self.assertRaisesRegex(DesktopActionError, "Use an app name"):
            service.execute(action)  # type: ignore[arg-type]

    def test_discussion_about_files_is_not_executed(self) -> None:
        service = DesktopActionService()
        self.assertIsNone(service.parse("Explain how to create a file in Python"))

    def test_artifact_actions_open_documents_for_editing_and_the_containing_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "report.py"
            document = root / "report.docx"
            web_page = root / "index.html"
            source.write_text("print('ready')", encoding="utf-8")
            document.write_bytes(b"docx")
            web_page.write_text("<!doctype html><title>Ready</title>", encoding="utf-8")
            service = DesktopActionService(
                root,
                working_folders=[str(root)],
                active_working_folder=str(root),
            )

            with patch.object(os, "startfile") as startfile:
                self.assertEqual(source.resolve(), service.open_artifact_file(str(source)))
                startfile.assert_called_with(str(source.resolve()), "edit")

                self.assertEqual(document.resolve(), service.open_artifact_file(str(document)))
                startfile.assert_called_with(str(document.resolve()), "open")

                self.assertEqual(web_page.resolve(), service.open_artifact_file(str(web_page)))
                startfile.assert_called_with(str(web_page.resolve()), "open")

                self.assertEqual(root.resolve(), service.open_artifact_folder(str(source)))
                startfile.assert_called_with(str(root.resolve()), "open")

    def test_artifact_actions_reject_unsafe_missing_relative_and_out_of_scope_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            allowed = root / "allowed"
            allowed.mkdir()
            executable = allowed / "payload.exe"
            executable.write_bytes(b"not executable")
            outside = root / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            service = DesktopActionService(
                allowed,
                working_folders=[str(allowed)],
                active_working_folder=str(allowed),
            )

            with self.assertRaisesRegex(DesktopActionError, "Executable artifacts"):
                service.open_artifact_file(str(executable))
            with self.assertRaisesRegex(DesktopActionError, "Choose the artifact's folder"):
                service.open_artifact_file(str(outside))
            with self.assertRaisesRegex(DesktopActionError, "absolute path"):
                service.open_artifact_file("relative.txt")
            with self.assertRaisesRegex(DesktopActionError, "no longer exists"):
                service.open_artifact_file(str(allowed / "deleted.txt"))


if __name__ == "__main__":
    unittest.main()
