from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from local_matrix_assistant.services.agent_intent import AgentIntentService
from local_matrix_assistant.services.desktop_actions import DesktopActionError


class AgentIntentServiceTests(unittest.TestCase):
    def test_accepts_general_language_but_blocks_destructive_fallbacks(self) -> None:
        self.assertTrue(AgentIntentService.can_interpret("explain that in simpler terms"))
        self.assertTrue(AgentIntentService.can_interpret("I want something fun to play"))
        self.assertFalse(AgentIntentService.can_interpret("delete file app.py"))
        self.assertFalse(AgentIntentService.can_interpret("install this package"))

    def test_parses_supported_contextual_routes(self) -> None:
        intent = AgentIntentService.parse_response(
            '{"kind":"workspace_change","request":"Make the existing login form responsive."}'
        )

        self.assertEqual("workspace_change", intent.kind)
        self.assertIn("login form", intent.request)

    def test_rejects_unknown_routes(self) -> None:
        with self.assertRaises(DesktopActionError):
            AgentIntentService.parse_response('{"kind":"shell","request":"run arbitrary command"}')

    def test_uses_valid_embedded_object_after_malformed_braced_prose(self) -> None:
        intent = AgentIntentService.parse_response(
            'Draft {kind: unknown}; final: {"kind":"answer","request":"Explain the result."}'
        )

        self.assertEqual("answer", intent.kind)
        self.assertEqual("Explain the result.", intent.request)

    def test_truncates_oversized_model_text_consistently(self) -> None:
        intent = AgentIntentService.parse_response(
            '{"kind":"answer","request":"' + ("a" * 2_010) + '"}'
        )

        self.assertEqual(AgentIntentService.max_request_characters + 3, len(intent.request))
        self.assertTrue(intent.request.endswith("..."))


if __name__ == "__main__":
    unittest.main()
