from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from json import JSONDecodeError
from pathlib import Path
import re
from threading import RLock
import uuid

from local_matrix_assistant.core.models import ChatMessage, ConversationMemory, ConversationRecord, ConversationSummary
from local_matrix_assistant.services.attachments import AttachmentService


_INDEX_FILENAME = "_history_index.json"
_INDEX_VERSION = 1
_CONVERSATION_ID_PATTERN = re.compile(r"[0-9a-f]{32}", re.IGNORECASE)


@dataclass(slots=True)
class _HistoryIndexEntry:
    summary: ConversationSummary
    size: int
    modified_ns: int
    searchable_text: str | None = None


class HistoryStore:
    interrupted_reply_message = (
        "The app closed before the local model finished this reply. Retry to generate it again."
    )

    def __init__(self, history_dir: Path, legacy_history_file: Path) -> None:
        self._history_dir = history_dir
        self._legacy_history_file = legacy_history_file
        self._index_path = history_dir / _INDEX_FILENAME
        self._index_lock = RLock()
        self._index: dict[str, _HistoryIndexEntry] = {}
        self._index_reconciled = False
        self._directory_modified_ns = 0
        self._history_dir.mkdir(parents=True, exist_ok=True)
        self._load_index()
        self._migrate_legacy_history_if_needed()

    def list_conversations(self) -> list[ConversationSummary]:
        self._refresh_index()
        with self._index_lock:
            summaries = [entry.summary for entry in self._index.values()]
        summaries.sort(key=lambda item: item.updated_at, reverse=True)
        return summaries

    def search_conversations(self, query: str) -> list[ConversationSummary]:
        terms = [term.casefold() for term in re.findall(r"\S+", query.strip())]
        summaries = self.list_conversations()
        if not terms:
            return summaries
        return [
            summary
            for summary in summaries
            if all(term in self._searchable_text(summary.conversation_id) for term in terms)
        ]

    def rename_conversation(self, conversation_id: str, title: str) -> ConversationSummary:
        conversation_id = self._validated_conversation_id(conversation_id)
        cleaned = re.sub(r"\s+", " ", title).strip()
        if not cleaned:
            raise ValueError("Conversation title cannot be empty.")
        if len(cleaned) > 80:
            raise ValueError("Conversation title must be 80 characters or fewer.")
        path = self._conversation_path(conversation_id)
        payload = self._load_payload(path)
        if not payload:
            raise ValueError("Conversation could not be found.")
        messages = self._deserialize_messages(payload.get("messages", []))
        summary = ConversationSummary(
            conversation_id=conversation_id,
            title=cleaned,
            created_at=str(payload.get("created_at") or self.now_stamp()),
            updated_at=self.now_stamp(),
            message_count=len(messages),
            preview=self._preview_from_messages(messages),
        )
        memory = self._deserialize_memory(payload.get("memory"), len(messages))
        self._write_record(summary, messages, memory)
        return summary

    def load_latest_or_create(self) -> ConversationRecord:
        summaries = self.list_conversations()
        if summaries:
            return self.load_conversation(summaries[0].conversation_id)
        return self.create_conversation()

    def load_preferred_or_latest(self, preferred_conversation_id: str) -> ConversationRecord:
        summaries = self.list_conversations()
        preferred = preferred_conversation_id.strip().casefold()
        if preferred:
            match = next(
                (
                    summary
                    for summary in summaries
                    if summary.conversation_id.casefold() == preferred
                ),
                None,
            )
            if match is not None:
                return self.load_conversation(match.conversation_id)
        if summaries:
            return self.load_conversation(summaries[0].conversation_id)
        return self.create_conversation()

    def create_conversation(self, title: str = "New Chat") -> ConversationRecord:
        conversation_id = uuid.uuid4().hex
        timestamp = self.now_stamp()
        summary = ConversationSummary(
            conversation_id=conversation_id,
            title=title,
            created_at=timestamp,
            updated_at=timestamp,
            message_count=0,
            preview="",
        )
        memory = ConversationMemory()
        self._write_record(summary, [], memory)
        return ConversationRecord(summary=summary, messages=[], memory=memory)

    def load_conversation(self, conversation_id: str) -> ConversationRecord:
        conversation_id = self._validated_conversation_id(conversation_id)
        path = self._conversation_path(conversation_id)
        payload = self._load_payload(path)
        if not payload:
            return self.create_conversation()
        raw_messages = payload.get("messages", [])
        recovered_pending_reply = self._has_pending_reply(raw_messages)
        messages = self._deserialize_messages(raw_messages)
        summary = ConversationSummary(
            conversation_id=conversation_id,
            title=str(payload.get("title") or self._title_from_messages(messages)),
            created_at=str(payload.get("created_at") or self.now_stamp()),
            updated_at=str(payload.get("updated_at") or self.now_stamp()),
            message_count=len(messages),
            preview=self._preview_from_messages(messages),
        )
        memory = self._deserialize_memory(payload.get("memory"), len(messages))
        if recovered_pending_reply:
            summary.updated_at = self.now_stamp()
            self._write_record(summary, messages, memory)
        else:
            self._cache_record(path, summary, messages)
        return ConversationRecord(summary=summary, messages=messages, memory=memory)

    def save_conversation(
        self,
        conversation_id: str,
        messages: list[ChatMessage],
        *,
        title: str | None = None,
        created_at: str | None = None,
        memory: ConversationMemory | None = None,
    ) -> ConversationSummary:
        conversation_id = self._validated_conversation_id(conversation_id)
        existing = self._load_payload(self._conversation_path(conversation_id)) or {}
        summary = ConversationSummary(
            conversation_id=conversation_id,
            title=title or str(existing.get("title") or self._title_from_messages(messages)),
            created_at=created_at or str(existing.get("created_at") or self.now_stamp()),
            updated_at=self.now_stamp(),
            message_count=len(messages),
            preview=self._preview_from_messages(messages),
        )
        stored_memory = memory or self._deserialize_memory(existing.get("memory"), len(messages))
        if stored_memory.covered_messages > len(messages):
            stored_memory = ConversationMemory()
        self._write_record(summary, messages, stored_memory)
        return summary

    def delete_conversation(self, conversation_id: str) -> None:
        conversation_id = self._validated_conversation_id(conversation_id)
        self._conversation_path(conversation_id).unlink(missing_ok=True)
        with self._index_lock:
            removed = self._index.pop(conversation_id, None)
            if removed is not None:
                self._write_index_locked()

    def _migrate_legacy_history_if_needed(self) -> None:
        if self._conversation_files() or not self._legacy_history_file.exists():
            return
        messages = self._load_legacy_messages()
        if not messages:
            return
        record = self.create_conversation()
        self.save_conversation(
            record.summary.conversation_id,
            messages,
            title=self._title_from_messages(messages),
            created_at=messages[0].timestamp if messages else record.summary.created_at,
        )

    def _load_legacy_messages(self) -> list[ChatMessage]:
        try:
            raw = self._legacy_history_file.read_text(encoding="utf-8").strip()
        except OSError:
            return []
        if not raw:
            return []
        try:
            payload = json.loads(raw)
        except JSONDecodeError:
            return []
        if not isinstance(payload, list):
            return []
        return self._deserialize_messages(payload)

    @staticmethod
    def _deserialize_messages(payload: object) -> list[ChatMessage]:
        if not isinstance(payload, list):
            return []
        messages: list[ChatMessage] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", ""))
            content = str(item.get("content", ""))
            metadata = item.get("metadata", {}) if isinstance(item.get("metadata", {}), dict) else {}
            metadata = dict(metadata)
            if role == "assistant" and metadata.get("pending"):
                metadata.pop("pending", None)
                metadata.pop("pending_label", None)
                metadata["error"] = True
                metadata["interrupted"] = True
                metadata["error_message"] = HistoryStore.interrupted_reply_message
                if content.strip() == "Thinking...":
                    content = ""
            messages.append(
                ChatMessage(
                    role=role,
                    content=content,
                    timestamp=str(item.get("timestamp", "")),
                    metadata=metadata,
                )
            )
        return messages

    @staticmethod
    def _has_pending_reply(payload: object) -> bool:
        return isinstance(payload, list) and any(
            isinstance(item, dict)
            and str(item.get("role", "")) == "assistant"
            and isinstance(item.get("metadata"), dict)
            and bool(item["metadata"].get("pending"))
            for item in payload
        )

    @staticmethod
    def _deserialize_memory(payload: object, message_count: int) -> ConversationMemory:
        if not isinstance(payload, dict):
            return ConversationMemory()
        try:
            covered_messages = max(0, min(message_count, int(payload.get("covered_messages", 0))))
        except (TypeError, ValueError):
            covered_messages = 0
        content = str(payload.get("content", "")).strip()
        if not content or covered_messages == 0:
            return ConversationMemory()
        return ConversationMemory(
            content=content,
            covered_messages=covered_messages,
            updated_at=str(payload.get("updated_at", "")),
            source=str(payload.get("source", "")),
        )

    def _write_record(
        self,
        summary: ConversationSummary,
        messages: list[ChatMessage],
        memory: ConversationMemory,
    ) -> None:
        payload = {
            "conversation_id": summary.conversation_id,
            "title": summary.title,
            "created_at": summary.created_at,
            "updated_at": summary.updated_at,
            "messages": [asdict(message) for message in messages],
            "memory": asdict(memory),
        }
        path = self._conversation_path(summary.conversation_id)
        temporary_path = path.with_suffix(".tmp")
        try:
            with temporary_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
        except (OSError, TypeError, ValueError):
            temporary_path.unlink(missing_ok=True)
            raise
        self._cache_record(path, summary, messages, persist=True)

    def _load_index(self) -> None:
        payload = self._load_payload(self._index_path)
        if not payload or payload.get("version") != _INDEX_VERSION:
            return
        raw_entries = payload.get("conversations")
        if not isinstance(raw_entries, list):
            return
        loaded: dict[str, _HistoryIndexEntry] = {}
        for raw in raw_entries:
            if not isinstance(raw, dict):
                continue
            try:
                conversation_id = self._validated_conversation_id(raw.get("conversation_id"))
            except ValueError:
                continue
            try:
                summary = ConversationSummary(
                    conversation_id=conversation_id,
                    title=str(raw.get("title") or "New Chat"),
                    created_at=str(raw.get("created_at") or ""),
                    updated_at=str(raw.get("updated_at") or ""),
                    message_count=max(0, int(raw.get("message_count", 0))),
                    preview=str(raw.get("preview") or ""),
                )
                size = max(0, int(raw.get("size", 0)))
                modified_ns = max(0, int(raw.get("modified_ns", 0)))
            except (TypeError, ValueError):
                continue
            loaded[conversation_id] = _HistoryIndexEntry(summary, size, modified_ns)
        with self._index_lock:
            self._index = loaded

    def _refresh_index(self) -> None:
        try:
            directory_modified_ns = self._history_dir.stat().st_mtime_ns
        except OSError:
            directory_modified_ns = 0
        with self._index_lock:
            if (
                self._index_reconciled
                and directory_modified_ns == self._directory_modified_ns
            ):
                return
        files = {path.stem.casefold(): path for path in self._conversation_files()}
        changed = False
        with self._index_lock:
            for conversation_id in list(self._index):
                if conversation_id not in files:
                    del self._index[conversation_id]
                    changed = True

            for conversation_id, path in files.items():
                try:
                    stat = path.stat()
                except OSError:
                    continue
                cached = self._index.get(conversation_id)
                if (
                    cached is not None
                    and cached.size == stat.st_size
                    and cached.modified_ns == stat.st_mtime_ns
                ):
                    continue
                payload = self._load_payload(path)
                if not payload:
                    if cached is not None:
                        del self._index[conversation_id]
                        changed = True
                    continue
                messages = self._deserialize_messages(payload.get("messages", []))
                summary = self._summary_from_payload(conversation_id, payload, messages)
                self._index[conversation_id] = _HistoryIndexEntry(
                    summary,
                    stat.st_size,
                    stat.st_mtime_ns,
                    self._build_searchable_text(summary, messages),
                )
                changed = True
            if changed:
                self._write_index_locked()
            self._mark_directory_reconciled_locked()

    def _cache_record(
        self,
        path: Path,
        summary: ConversationSummary,
        messages: list[ChatMessage],
        *,
        persist: bool = False,
    ) -> None:
        try:
            stat = path.stat()
        except OSError:
            return
        entry = _HistoryIndexEntry(
            summary,
            stat.st_size,
            stat.st_mtime_ns,
            self._build_searchable_text(summary, messages),
        )
        with self._index_lock:
            self._index[summary.conversation_id] = entry
            if persist:
                self._write_index_locked()

    def _searchable_text(self, conversation_id: str) -> str:
        with self._index_lock:
            entry = self._index.get(conversation_id)
            if entry is None:
                return ""
            if entry.searchable_text is not None:
                return entry.searchable_text
            expected_size = entry.size
            expected_modified_ns = entry.modified_ns

        path = self._conversation_path(conversation_id)
        payload = self._load_payload(path)
        if not payload:
            return ""
        messages = self._deserialize_messages(payload.get("messages", []))
        with self._index_lock:
            current = self._index.get(conversation_id)
            if current is None:
                return ""
            summary = current.summary
        searchable = self._build_searchable_text(summary, messages)
        with self._index_lock:
            current = self._index.get(conversation_id)
            if (
                current is not None
                and current.size == expected_size
                and current.modified_ns == expected_modified_ns
            ):
                current.searchable_text = searchable
        return searchable

    @staticmethod
    def _build_searchable_text(
        summary: ConversationSummary,
        messages: list[ChatMessage],
    ) -> str:
        attachment_text = [
            str(value)
            for message in messages
            for attachment in AttachmentService.metadata_attachments(message.metadata)
            for value in (attachment.get("name", ""), attachment.get("content", ""))
        ]
        return "\n".join(
            [
                summary.title,
                summary.preview,
                *(message.content for message in messages),
                *attachment_text,
            ]
        ).casefold()

    def _write_index_locked(self) -> None:
        payload = {
            "version": _INDEX_VERSION,
            "conversations": [
                {
                    **asdict(entry.summary),
                    "size": entry.size,
                    "modified_ns": entry.modified_ns,
                }
                for entry in self._index.values()
            ],
        }
        temporary_path = self._index_path.with_suffix(".tmp")
        try:
            with temporary_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.flush()
            temporary_path.replace(self._index_path)
        except OSError:
            temporary_path.unlink(missing_ok=True)
        self._mark_directory_reconciled_locked()

    def _mark_directory_reconciled_locked(self) -> None:
        try:
            self._directory_modified_ns = self._history_dir.stat().st_mtime_ns
        except OSError:
            self._directory_modified_ns = 0
        self._index_reconciled = True

    def _conversation_files(self) -> list[Path]:
        return [
            path
            for path in sorted(self._history_dir.glob("*.json"))
            if _CONVERSATION_ID_PATTERN.fullmatch(path.stem)
        ]

    @classmethod
    def _summary_from_payload(
        cls,
        conversation_id: str,
        payload: dict,
        messages: list[ChatMessage],
    ) -> ConversationSummary:
        created_at = str(payload.get("created_at") or cls.now_stamp())
        return ConversationSummary(
            conversation_id=conversation_id,
            title=str(payload.get("title") or cls._title_from_messages(messages)),
            created_at=created_at,
            updated_at=str(payload.get("updated_at") or created_at),
            message_count=len(messages),
            preview=cls._preview_from_messages(messages),
        )

    @staticmethod
    def _title_from_messages(messages: list[ChatMessage]) -> str:
        for message in messages:
            if message.role == "user" and message.content.strip():
                first_line = message.content.strip().splitlines()[0]
                return first_line[:48] + ("..." if len(first_line) > 48 else "")
        return "New Chat"

    @staticmethod
    def _preview_from_messages(messages: list[ChatMessage]) -> str:
        for message in reversed(messages):
            if message.content.strip():
                preview = message.content.strip().replace("\n", " ")
                return preview[:72] + ("..." if len(preview) > 72 else "")
        return ""

    @staticmethod
    def _load_payload(path: Path) -> dict | None:
        if not path.exists():
            return None
        try:
            raw = path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    def _conversation_path(self, conversation_id: str) -> Path:
        return self._history_dir / f"{conversation_id}.json"

    @staticmethod
    def _validated_conversation_id(conversation_id: object) -> str:
        if not isinstance(conversation_id, str) or not _CONVERSATION_ID_PATTERN.fullmatch(
            conversation_id
        ):
            raise ValueError("Conversation ID is invalid.")
        return conversation_id.casefold()

    @staticmethod
    def now_stamp() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
