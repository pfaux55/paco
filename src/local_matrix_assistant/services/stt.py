from __future__ import annotations

import audioop
import json
import wave
from io import BytesIO
from pathlib import Path

from vosk import KaldiRecognizer, Model, SetLogLevel


SetLogLevel(-1)


class SttService:
    def __init__(self, model_dir: str) -> None:
        self.model_dir = Path(model_dir)
        self._model: Model | None = None

    def ready(self) -> tuple[bool, str]:
        if not self.model_dir.exists():
            return False, f"Missing STT model: {self.model_dir}"
        return True, "Ready"

    def load(self) -> None:
        if self._model is None:
            ok, message = self.ready()
            if not ok:
                raise FileNotFoundError(message)
            self._model = Model(str(self.model_dir))

    def update_model_dir(self, model_dir: str) -> None:
        self.model_dir = Path(model_dir)
        self._model = None

    def transcribe(self, wav_bytes: bytes) -> str:
        self.load()
        pcm_data, sample_rate = self._prepare_pcm(wav_bytes)
        recognizer = KaldiRecognizer(self._model, sample_rate)
        recognizer.SetWords(True)
        chunk_size = 4000
        for index in range(0, len(pcm_data), chunk_size):
            recognizer.AcceptWaveform(pcm_data[index : index + chunk_size])
        result = json.loads(recognizer.FinalResult())
        text = result.get("text", "").strip()
        if not text:
            raise RuntimeError("Speech was captured, but no text was recognized.")
        return text

    @staticmethod
    def _prepare_pcm(wav_bytes: bytes) -> tuple[bytes, int]:
        with wave.open(BytesIO(wav_bytes), "rb") as wav_file:
            sample_rate = wav_file.getframerate()
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            frames = wav_file.readframes(wav_file.getnframes())

        if sample_width != 2:
            raise RuntimeError("Only 16-bit PCM WAV audio is supported for STT.")
        if channels > 1:
            frames = audioop.tomono(frames, sample_width, 1, 1)
        if sample_rate != 16000:
            frames, _ = audioop.ratecv(frames, sample_width, 1, sample_rate, 16000, None)
            sample_rate = 16000
        return frames, sample_rate
