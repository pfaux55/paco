from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path


CREATE_ONLY_ACCESS = "create_only"
READ_ONLY_ACCESS = "read_only"
VALID_ACCESS_MODES = frozenset({CREATE_ONLY_ACCESS, READ_ONLY_ACCESS})
_LEGACY_STANDARD_ACCESS = "standard"


class AgentPermissionStore:
    """Persist bounded workspace-specific Agent access modes."""

    max_entries = 100
    max_file_bytes = 256_000

    def __init__(self, path: Path) -> None:
        self.path = path
        self._entries, self._fail_closed = self._load()

    def mode_for(self, workspace: Path | str) -> str:
        fallback = READ_ONLY_ACCESS if self._fail_closed else CREATE_ONLY_ACCESS
        return self._entries.get(self._key(workspace), fallback)

    def set_mode(self, workspace: Path | str, mode: str) -> None:
        normalized = mode if mode in VALID_ACCESS_MODES else ""
        if not normalized:
            raise ValueError(f"Unsupported Agent access mode: {mode}")
        key = self._key(workspace)
        previous = dict(self._entries)
        previous_fail_closed = self._fail_closed
        if normalized == CREATE_ONLY_ACCESS:
            self._entries.pop(key, None)
        else:
            self._entries[key] = normalized
        try:
            self._save()
        except OSError:
            self._entries = previous
            self._fail_closed = previous_fail_closed
            raise
        self._fail_closed = False

    def _load(self) -> tuple[dict[str, str], bool]:
        try:
            if self.path.stat().st_size > self.max_file_bytes:
                return {}, True
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}, False
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}, True
        if not isinstance(payload, dict):
            return {}, True
        records = payload.get("workspaces", [])
        if not isinstance(records, list):
            return {}, True
        entries: dict[str, str] = {}
        malformed = len(records) > self.max_entries
        for record in records[: self.max_entries]:
            if not isinstance(record, dict):
                malformed = True
                continue
            path = record.get("path")
            mode = record.get("mode")
            if mode == _LEGACY_STANDARD_ACCESS:
                mode = CREATE_ONLY_ACCESS
            if not isinstance(path, str) or not path.strip() or mode not in VALID_ACCESS_MODES:
                malformed = True
                continue
            try:
                entries[self._key(path)] = mode
            except (OSError, RuntimeError, ValueError):
                malformed = True
                continue
        return entries, malformed

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "workspaces": [
                {"path": path, "mode": mode}
                for path, mode in sorted(self._entries.items())[: self.max_entries]
            ],
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise

    @staticmethod
    def _key(workspace: Path | str) -> str:
        raw = str(workspace).strip()
        if not raw or "\x00" in raw:
            raise ValueError("Workspace path is invalid.")
        return os.path.normcase(os.path.abspath(os.path.expanduser(raw)))
