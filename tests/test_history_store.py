from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from local_matrix_assistant.core.models import ChatMessage, ConversationMemory
from local_matrix_assistant.services.history import HistoryStore


class HistoryStoreTests(unittest.TestCase):
    def test_preferred_conversation_opens_instead_of_the_latest_and_missing_id_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = HistoryStore(root / "chats", root / "legacy.json")
            with patch.object(HistoryStore, "now_stamp", return_value="2026-01-01 10:00:00"):
                preferred = store.create_conversation("Preferred")
            with patch.object(HistoryStore, "now_stamp", return_value="2026-01-01 11:00:00"):
                latest = store.create_conversation("Latest")

            restored = store.load_preferred_or_latest(preferred.summary.conversation_id)
            fallback = store.load_preferred_or_latest("f" * 32)

            self.assertEqual(preferred.summary.conversation_id, restored.summary.conversation_id)
            self.assertEqual(latest.summary.conversation_id, fallback.summary.conversation_id)

    def test_create_save_load_and_delete_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history_dir = root / "chats"
            legacy_file = root / "conversation_history.json"
            store = HistoryStore(history_dir, legacy_file)

            created = store.create_conversation("Smoke Test")
            messages = [
                ChatMessage(role="user", content="hello history", timestamp=store.now_stamp()),
                ChatMessage(role="assistant", content="history works", timestamp=store.now_stamp()),
            ]
            summary = store.save_conversation(created.summary.conversation_id, messages, created_at=created.summary.created_at)

            self.assertEqual("history works", summary.preview)
            loaded = store.load_conversation(created.summary.conversation_id)
            self.assertEqual("Smoke Test", loaded.summary.title)
            self.assertEqual(2, len(loaded.messages))
            self.assertEqual("history works", loaded.messages[1].content)

            store.delete_conversation(created.summary.conversation_id)
            self.assertEqual([], store.list_conversations())

    def test_conversation_memory_round_trips_and_survives_normal_saves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = HistoryStore(root / "chats", root / "legacy.json")
            created = store.create_conversation()
            messages = [
                ChatMessage("user", "Use Python 3.12", "now"),
                ChatMessage("assistant", "Understood", "now"),
                ChatMessage("user", "Continue", "now"),
            ]
            memory = ConversationMemory(
                content="- User requires Python 3.12.",
                covered_messages=2,
                updated_at="now",
                source="local_model",
            )

            store.save_conversation(created.summary.conversation_id, messages, memory=memory)
            store.save_conversation(created.summary.conversation_id, messages)
            loaded = store.load_conversation(created.summary.conversation_id)

            self.assertEqual(memory, loaded.memory)

    def test_pending_reply_recovers_atomically_as_retryable_interruption(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history_dir = root / "chats"
            legacy_file = root / "legacy.json"
            store = HistoryStore(history_dir, legacy_file)
            created = store.create_conversation()
            store.save_conversation(
                created.summary.conversation_id,
                [
                    ChatMessage("user", "Continue the explanation", "now"),
                    ChatMessage(
                        "assistant",
                        "Thinking...",
                        "now",
                        metadata={
                            "pending": True,
                            "pending_label": "Waiting for local-model...",
                            "model_name": "local-model",
                        },
                    ),
                ],
            )

            restarted = HistoryStore(history_dir, legacy_file)
            recovered = restarted.load_conversation(created.summary.conversation_id)

            reply = recovered.messages[-1]
            self.assertEqual("", reply.content)
            self.assertTrue(reply.metadata["error"])
            self.assertTrue(reply.metadata["interrupted"])
            self.assertNotIn("pending", reply.metadata)
            self.assertEqual(
                HistoryStore.interrupted_reply_message,
                reply.metadata["error_message"],
            )
            persisted = HistoryStore(history_dir, legacy_file).load_conversation(
                created.summary.conversation_id
            )
            self.assertEqual(reply, persisted.messages[-1])
            self.assertEqual("Continue the explanation", persisted.summary.preview)

    def test_pending_reply_recovery_preserves_checkpointed_partial_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = HistoryStore(root / "chats", root / "legacy.json")
            created = store.create_conversation()
            store.save_conversation(
                created.summary.conversation_id,
                [
                    ChatMessage("user", "Explain", "now"),
                    ChatMessage(
                        "assistant",
                        "A partial response",
                        "now",
                        metadata={"pending": True},
                    ),
                ],
            )

            recovered = HistoryStore(root / "chats", root / "legacy.json").load_conversation(
                created.summary.conversation_id
            )

            self.assertEqual("A partial response", recovered.messages[-1].content)
            self.assertTrue(recovered.messages[-1].metadata["interrupted"])

    def test_invalid_memory_coverage_is_discarded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = HistoryStore(root / "chats", root / "legacy.json")
            created = store.create_conversation()
            messages = [ChatMessage("user", "hello", "now")]
            invalid = ConversationMemory("bad", 99, "now", "test")

            store.save_conversation(created.summary.conversation_id, messages, memory=invalid)
            loaded = store.load_conversation(created.summary.conversation_id)

            self.assertEqual(ConversationMemory(), loaded.memory)

    def test_search_matches_titles_previews_and_older_message_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = HistoryStore(root / "chats", root / "legacy.json")
            first = store.create_conversation("Python Project")
            store.save_conversation(
                first.summary.conversation_id,
                [
                    ChatMessage("user", "Use SQLite for durable storage", "now"),
                    ChatMessage("assistant", "Implemented the repository layer", "now"),
                ],
                title="Python Project",
            )
            second = store.create_conversation("Vacation")
            store.save_conversation(
                second.summary.conversation_id,
                [ChatMessage("user", "Plan Toronto restaurants", "now")],
                title="Vacation",
            )

            matches = store.search_conversations("sqlite repository")

            self.assertEqual([first.summary.conversation_id], [item.conversation_id for item in matches])
            self.assertEqual([], store.search_conversations("missing phrase"))

    def test_search_matches_attached_filename_and_snapshot_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = HistoryStore(root / "chats", root / "legacy.json")
            record = store.create_conversation()
            store.save_conversation(
                record.summary.conversation_id,
                [
                    ChatMessage(
                        "user",
                        "Please review this",
                        "now",
                        metadata={
                            "attachments": [
                                {
                                    "name": "database.py",
                                    "content": "def migrate_ledger_schema(): pass",
                                    "size_bytes": 34,
                                }
                            ]
                        },
                    )
                ],
            )

            self.assertEqual(1, len(store.search_conversations("database.py")))
            self.assertEqual(1, len(store.search_conversations("ledger schema")))

    def test_rename_persists_without_changing_messages_or_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = HistoryStore(root / "chats", root / "legacy.json")
            created = store.create_conversation()
            messages = [ChatMessage("user", "Original content", "now")]
            memory = ConversationMemory("- Keep this fact.", 1, "now", "local_model")
            store.save_conversation(created.summary.conversation_id, messages, memory=memory)

            summary = store.rename_conversation(created.summary.conversation_id, "  Project   Notes  ")
            loaded = store.load_conversation(created.summary.conversation_id)

            self.assertEqual("Project Notes", summary.title)
            self.assertEqual("Project Notes", loaded.summary.title)
            self.assertEqual(messages, loaded.messages)
            self.assertEqual(memory, loaded.memory)

    def test_rename_rejects_empty_and_oversized_titles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = HistoryStore(root / "chats", root / "legacy.json")
            created = store.create_conversation()

            with self.assertRaisesRegex(ValueError, "cannot be empty"):
                store.rename_conversation(created.summary.conversation_id, "   ")
            with self.assertRaisesRegex(ValueError, "80 characters"):
                store.rename_conversation(created.summary.conversation_id, "x" * 81)

    def test_persisted_index_avoids_reloading_unchanged_conversation_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history_dir = root / "chats"
            legacy_file = root / "legacy.json"
            store = HistoryStore(history_dir, legacy_file)
            first = store.create_conversation("First")
            second = store.create_conversation("Second")
            store.save_conversation(first.summary.conversation_id, [ChatMessage("user", "alpha", "now")])
            store.save_conversation(second.summary.conversation_id, [ChatMessage("user", "beta", "now")])

            self.assertTrue((history_dir / "_history_index.json").is_file())
            reloaded = HistoryStore(history_dir, legacy_file)

            with patch.object(reloaded, "_load_payload", wraps=reloaded._load_payload) as load_payload:
                summaries = reloaded.list_conversations()

            self.assertEqual(2, len(summaries))
            load_payload.assert_not_called()

            with patch.object(
                reloaded,
                "_conversation_files",
                side_effect=AssertionError("routine refresh rescanned the archive"),
            ):
                self.assertEqual(2, len(reloaded.list_conversations()))

    def test_full_text_search_is_loaded_once_then_cached_in_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history_dir = root / "chats"
            legacy_file = root / "legacy.json"
            store = HistoryStore(history_dir, legacy_file)
            record = store.create_conversation("Indexed")
            store.save_conversation(
                record.summary.conversation_id,
                [ChatMessage("user", "unique migration token", "now")],
            )
            reloaded = HistoryStore(history_dir, legacy_file)

            with patch.object(reloaded, "_load_payload", wraps=reloaded._load_payload) as load_payload:
                first = reloaded.search_conversations("migration")
                first_load_count = load_payload.call_count
                second = reloaded.search_conversations("migration")

            self.assertEqual(1, len(first))
            self.assertEqual(first, second)
            self.assertEqual(1, first_load_count)
            self.assertEqual(first_load_count, load_payload.call_count)

    def test_corrupt_index_is_rebuilt_from_intact_conversations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history_dir = root / "chats"
            legacy_file = root / "legacy.json"
            store = HistoryStore(history_dir, legacy_file)
            record = store.create_conversation("Recoverable")
            store.save_conversation(record.summary.conversation_id, [ChatMessage("user", "saved", "now")])
            (history_dir / "_history_index.json").write_text("{broken", encoding="utf-8")

            recovered = HistoryStore(history_dir, legacy_file)

            self.assertEqual(
                [record.summary.conversation_id],
                [summary.conversation_id for summary in recovered.list_conversations()],
            )

    def test_failed_atomic_write_preserves_record_and_removes_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history_dir = root / "chats"
            store = HistoryStore(history_dir, root / "legacy.json")
            record = store.create_conversation()
            store.save_conversation(
                record.summary.conversation_id,
                [ChatMessage("user", "saved before failure", "now")],
            )
            path = history_dir / f"{record.summary.conversation_id}.json"
            temporary_path = path.with_suffix(".tmp")

            with (
                patch(
                    "local_matrix_assistant.services.history.os.fsync",
                    side_effect=OSError("disk full"),
                ),
                self.assertRaisesRegex(OSError, "disk full"),
            ):
                store.save_conversation(
                    record.summary.conversation_id,
                    [ChatMessage("user", "must not replace original", "now")],
                )

            self.assertFalse(temporary_path.exists())
            recovered = HistoryStore(history_dir, root / "legacy.json").load_conversation(
                record.summary.conversation_id
            )
            self.assertEqual("saved before failure", recovered.messages[0].content)

    def test_invalid_conversation_ids_cannot_escape_the_history_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = HistoryStore(root / "chats", root / "legacy.json")

            for operation in (
                lambda: store.load_conversation("../outside"),
                lambda: store.save_conversation("../outside", []),
                lambda: store.rename_conversation("../outside", "Unsafe"),
                lambda: store.delete_conversation("../outside"),
            ):
                with self.subTest(operation=operation), self.assertRaisesRegex(
                    ValueError,
                    "Conversation ID is invalid",
                ):
                    operation()

            self.assertFalse((root / "outside.json").exists())

    def test_non_conversation_json_files_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history_dir = root / "chats"
            history_dir.mkdir()
            (history_dir / "notes.json").write_text(
                '{"title": "Not a conversation", "messages": []}',
                encoding="utf-8",
            )

            store = HistoryStore(history_dir, root / "legacy.json")

            self.assertEqual([], store.list_conversations())


if __name__ == "__main__":
    unittest.main()
