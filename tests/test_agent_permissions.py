from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from local_matrix_assistant.services.agent_permissions import (
    AgentPermissionStore,
    CREATE_ONLY_ACCESS,
    READ_ONLY_ACCESS,
)


class AgentPermissionStoreTests(unittest.TestCase):
    def test_modes_are_workspace_specific_and_persist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "permissions.json"
            first = root / "first"
            second = root / "second"
            store = AgentPermissionStore(path)

            store.set_mode(first, READ_ONLY_ACCESS)

            restored = AgentPermissionStore(path)
            self.assertEqual(READ_ONLY_ACCESS, restored.mode_for(first))
            self.assertEqual(CREATE_ONLY_ACCESS, restored.mode_for(second))

            restored.set_mode(first, CREATE_ONLY_ACCESS)
            self.assertEqual(CREATE_ONLY_ACCESS, AgentPermissionStore(path).mode_for(first))

    def test_malformed_oversized_and_unknown_records_fail_closed_to_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "permissions.json"
            path.write_text("not json", encoding="utf-8")
            self.assertEqual(READ_ONLY_ACCESS, AgentPermissionStore(path).mode_for(root))

            path.write_text(
                json.dumps(
                    {
                        "workspaces": [
                            {"path": str(root), "mode": "unrestricted"},
                            "invalid",
                        ]
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(READ_ONLY_ACCESS, AgentPermissionStore(path).mode_for(root))

            path.write_bytes(b"{" + (b"x" * (AgentPermissionStore.max_file_bytes + 1)))
            self.assertEqual(READ_ONLY_ACCESS, AgentPermissionStore(path).mode_for(root))

    def test_user_can_replace_a_malformed_store_with_an_explicit_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "permissions.json"
            path.write_text("broken", encoding="utf-8")
            store = AgentPermissionStore(path)
            self.assertEqual(READ_ONLY_ACCESS, store.mode_for(root))

            store.set_mode(root, CREATE_ONLY_ACCESS)

            self.assertEqual(CREATE_ONLY_ACCESS, AgentPermissionStore(path).mode_for(root))

    def test_failed_atomic_save_restores_in_memory_state_and_cleans_temp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "permissions.json"
            store = AgentPermissionStore(path)

            with patch(
                "local_matrix_assistant.services.agent_permissions.os.replace",
                side_effect=OSError("disk full"),
            ):
                with self.assertRaisesRegex(OSError, "disk full"):
                    store.set_mode(root, READ_ONLY_ACCESS)

            self.assertEqual(CREATE_ONLY_ACCESS, store.mode_for(root))
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_invalid_mode_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AgentPermissionStore(Path(tmp) / "permissions.json")
            with self.assertRaisesRegex(ValueError, "Unsupported"):
                store.set_mode(tmp, "full_access")

    def test_legacy_standard_access_is_migrated_to_create_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "permissions.json"
            path.write_text(
                json.dumps({"workspaces": [{"path": str(root), "mode": "standard"}]}),
                encoding="utf-8",
            )

            self.assertEqual(CREATE_ONLY_ACCESS, AgentPermissionStore(path).mode_for(root))


if __name__ == "__main__":
    unittest.main()
