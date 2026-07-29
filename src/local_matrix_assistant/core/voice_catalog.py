from __future__ import annotations

from pathlib import Path

from local_matrix_assistant.core.models import VoiceOption


PIPER_ENGINE_NAME = "Piper (local)"

KNOWN_PIPER_VOICES: dict[str, dict[str, str]] = {
    "en_US-lessac-low": {
        "label": "Lessac Low",
        "gender": "Female",
        "sample_text": "Matrix systems online. Voice preview ready.",
        "model_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/low/en_US-lessac-low.onnx?download=true",
        "config_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/low/en_US-lessac-low.onnx.json?download=true",
    },
    "en_US-amy-medium": {
        "label": "Amy Medium",
        "gender": "Female",
        "sample_text": "I am Amy, running fully local on your machine.",
        "model_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx?download=true",
        "config_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx.json?download=true",
    },
    "en_GB-alan-medium": {
        "label": "Alan Medium",
        "gender": "Male",
        "sample_text": "I am Alan, ready for local playback and voice response.",
        "model_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx?download=true",
        "config_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx.json?download=true",
    },
    "en_US-ryan-high": {
        "label": "Ryan High",
        "gender": "Male",
        "sample_text": "I am Ryan, speaking through the local Piper engine.",
        "model_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/high/en_US-ryan-high.onnx?download=true",
        "config_url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/high/en_US-ryan-high.onnx.json?download=true",
    },
}


def voice_option_from_paths(model_path: Path, config_path: Path) -> VoiceOption:
    voice_id = model_path.stem
    metadata = KNOWN_PIPER_VOICES.get(voice_id, {})
    return VoiceOption(
        voice_id=voice_id,
        label=metadata.get("label", voice_id.replace("_", " ").title()),
        gender=metadata.get("gender", "Unknown"),
        engine=PIPER_ENGINE_NAME,
        model_path=model_path,
        config_path=config_path,
        sample_text=metadata.get("sample_text", "Local voice preview ready."),
    )


def discover_piper_voices(tts_dir: Path) -> list[VoiceOption]:
    voices: list[VoiceOption] = []
    if not tts_dir.exists():
        return voices
    for model_path in sorted(tts_dir.glob("*.onnx")):
        config_path = model_path.with_suffix(model_path.suffix + ".json")
        if not config_path.exists():
            continue
        voices.append(voice_option_from_paths(model_path, config_path))
    return voices
