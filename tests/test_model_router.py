from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from local_matrix_assistant.services.model_router import ModelRouter


INSTALLED_MODELS = [
    "llama3.2:3b",
    "gemma3:1b",
    "mistral-nemo:latest",
    "qwen2.5-coder:7b",
    "llama3.1:8b",
    "qwen3:4b",
]


class ModelRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = ModelRouter()

    def test_auto_uses_fast_model_for_short_general_request(self) -> None:
        selection = self.router.select("Write a friendly greeting.", "auto", INSTALLED_MODELS, "qwen2.5-coder:7b")

        self.assertEqual("fast", selection.profile)
        self.assertEqual("llama3.2:3b", selection.model)
        self.assertTrue(selection.automatic)
        self.assertEqual(8192, selection.context_window)
        self.assertEqual(768, selection.max_output_tokens)
        self.assertEqual(7168, selection.input_token_budget)

    def test_auto_uses_coder_for_programming_request(self) -> None:
        selection = self.router.select(
            "Debug this Python traceback and write a unit test.",
            "auto",
            INSTALLED_MODELS,
            "qwen3:4b",
        )

        self.assertEqual("coding", selection.profile)
        self.assertEqual("qwen2.5-coder:7b", selection.model)
        self.assertEqual(8192, selection.context_window)
        self.assertEqual(2048, selection.max_output_tokens)

    def test_auto_recognizes_plain_language_repository_bug_request(self) -> None:
        selection = self.router.select(
            "Fix this bug in my repository.",
            "auto",
            INSTALLED_MODELS,
            "llama3.2:3b",
        )

        self.assertEqual("coding", selection.profile)

    def test_auto_uses_reasoning_model_for_complex_analysis(self) -> None:
        selection = self.router.select(
            "Analyze the architectural trade-offs between these approaches.",
            "auto",
            INSTALLED_MODELS,
            "llama3.2:3b",
        )

        self.assertEqual("reasoning", selection.profile)
        self.assertEqual("qwen3:4b", selection.model)

    def test_explicit_profile_overrides_prompt_classification(self) -> None:
        selection = self.router.select("Write Python code", "fast", INSTALLED_MODELS, "qwen2.5-coder:7b")

        self.assertEqual("fast", selection.profile)
        self.assertEqual("llama3.2:3b", selection.model)
        self.assertFalse(selection.automatic)

    def test_manual_profile_keeps_selected_model(self) -> None:
        selection = self.router.select("Any request", "manual", INSTALLED_MODELS, "llama3.1:8b")

        self.assertEqual("manual", selection.profile)
        self.assertEqual("llama3.1:8b", selection.model)

    def test_oversized_model_is_not_used_when_safe_models_exist(self) -> None:
        selection = self.router.select(
            "Analyze this difficult decision.",
            "reasoning",
            ["mistral-nemo:latest", "qwen3:4b"],
            "mistral-nemo:latest",
        )

        self.assertEqual("qwen3:4b", selection.model)

    def test_coding_system_prompt_requires_maintainable_fenced_output(self) -> None:
        prompt = self.router.system_prompt("coding")

        self.assertIn("production-ready", prompt)
        self.assertIn("fenced code blocks", prompt)

    def test_image_attachment_overrides_text_model_with_installed_vision_model(self) -> None:
        models = [*INSTALLED_MODELS, "qwen3.5:4b", "qwen3-vl:2b"]

        selection = self.router.select(
            "Describe the screenshot",
            "manual",
            models,
            "llama3.2:3b",
            requires_vision=True,
        )

        self.assertEqual("vision", selection.profile)
        self.assertEqual("qwen3.5:4b", selection.model)
        self.assertTrue(selection.automatic)

    def test_image_attachment_returns_no_model_when_only_text_models_are_installed(self) -> None:
        selection = self.router.select(
            "Describe the screenshot",
            "auto",
            INSTALLED_MODELS,
            "qwen3:4b",
            requires_vision=True,
        )

        self.assertEqual("", selection.model)
        self.assertIn("No installed", selection.reason)

    def test_vision_capability_detection_excludes_text_only_gemma_variant(self) -> None:
        self.assertFalse(self.router.is_vision_model("gemma3:1b"))
        self.assertTrue(self.router.is_vision_model("gemma3:4b"))
        self.assertTrue(self.router.is_vision_model("qwen3.5:4b"))

    def test_new_general_model_is_preferred_when_installed(self) -> None:
        selection = self.router.select(
            "Explain this design in detail for a project team.",
            "balanced",
            [*INSTALLED_MODELS, "qwen3.5:4b"],
            "qwen3:4b",
        )

        self.assertEqual("qwen3.5:4b", selection.model)


if __name__ == "__main__":
    unittest.main()
