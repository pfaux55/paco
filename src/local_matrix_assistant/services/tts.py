from __future__ import annotations

import audioop
from io import BytesIO
from pathlib import Path
import wave

from piper.config import SynthesisConfig
from piper.voice import PiperVoice

from local_matrix_assistant.core.models import VoiceOption
from local_matrix_assistant.core.voice_catalog import PIPER_ENGINE_NAME, discover_piper_voices, voice_option_from_paths


class TtsService:
    def __init__(self, model_path: str, config_path: str) -> None:
        self.model_path = Path(model_path)
        self.config_path = Path(config_path)
        self._voice: PiperVoice | None = None

    @property
    def engine_name(self) -> str:
        return PIPER_ENGINE_NAME

    def ready(self) -> tuple[bool, str]:
        if not self.model_path.exists():
            return False, f"Missing TTS model: {self.model_path}"
        if not self.config_path.exists():
            return False, f"Missing TTS config: {self.config_path}"
        return True, "Ready"

    def load(self) -> None:
        if self._voice is None:
            ok, message = self.ready()
            if not ok:
                raise FileNotFoundError(message)
            self._voice = PiperVoice.load(self.model_path, self.config_path)

    def update_paths(self, model_path: str, config_path: str) -> None:
        self.model_path = Path(model_path)
        self.config_path = Path(config_path)
        self._voice = None

    def current_voice(self) -> VoiceOption | None:
        if self.model_path.exists() and self.config_path.exists():
            return voice_option_from_paths(self.model_path, self.config_path)
        return None

    def list_voices(self) -> list[VoiceOption]:
        return discover_piper_voices(self.model_path.parent)

    def synthesize(self, text: str, *, rate: float = 1.0, volume: float = 1.0) -> bytes:
        self.load()
        config = SynthesisConfig(
            length_scale=max(0.4, min(2.0, 1.0 / max(rate, 0.2))),
            volume=max(0.0, min(volume, 2.0)),
        )
        buffer = BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            self._voice.synthesize_wav(text, wav_file, syn_config=config)
        wav_bytes = buffer.getvalue()
        return self._normalize_wav_bytes(wav_bytes)

    @staticmethod
    def _normalize_wav_bytes(wav_bytes: bytes) -> bytes:
        with wave.open(BytesIO(wav_bytes), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            frames = wav_file.readframes(wav_file.getnframes())

        if sample_width != 2:
            return wav_bytes
        if channels > 1:
            frames = audioop.tomono(frames, sample_width, 1, 1)
            channels = 1
        if sample_rate != 16000:
            frames, _ = audioop.ratecv(frames, sample_width, channels, sample_rate, 16000, None)
            sample_rate = 16000

        output = BytesIO()
        with wave.open(output, "wb") as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(sample_width)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(frames)
        return output.getvalue()
