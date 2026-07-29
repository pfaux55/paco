from __future__ import annotations

from local_matrix_assistant.core.constants import (
    MIC_GUIDANCE,
    OLLAMA_MODEL_MISSING_GUIDANCE,
    OLLAMA_NO_MODELS_GUIDANCE,
    OLLAMA_OFFLINE_GUIDANCE,
    OUTPUT_GUIDANCE,
    READY_GUIDANCE,
    VOICE_MODELS_GUIDANCE,
)
from local_matrix_assistant.core.models import StatusSnapshot
from local_matrix_assistant.services.audio import AudioPlayer, AudioRecorder
from local_matrix_assistant.services.ollama import OllamaClient
from local_matrix_assistant.services.stt import SttService
from local_matrix_assistant.services.tts import TtsService


class RuntimeStatusService:
    def __init__(
        self,
        ollama_client: OllamaClient,
        stt_service: SttService,
        tts_service: TtsService,
        recorder: AudioRecorder,
        player: AudioPlayer,
    ) -> None:
        self._ollama_client = ollama_client
        self._stt_service = stt_service
        self._tts_service = tts_service
        self._recorder = recorder
        self._player = player

    def build_snapshot(self, model_name: str) -> StatusSnapshot:
        ollama_status = self._ollama_client.status()
        stt_ready, stt_message = self._stt_service.ready()
        if stt_ready:
            try:
                self._stt_service.load()
                stt_message = "Ready"
            except Exception as exc:  # noqa: BLE001
                stt_ready = False
                stt_message = str(exc)

        tts_ready, tts_message = self._tts_service.ready()
        if tts_ready:
            try:
                self._tts_service.load()
                tts_message = "Ready"
            except Exception as exc:  # noqa: BLE001
                tts_ready = False
                tts_message = str(exc)

        try:
            mic_ready, mic_message = self._recorder.has_input()
        except Exception as exc:  # noqa: BLE001
            mic_ready, mic_message = False, f"Microphone check failed: {exc}"
        try:
            output_ready, output_message = self._player.has_output()
        except Exception as exc:  # noqa: BLE001
            output_ready, output_message = False, f"Speaker check failed: {exc}"
        model_ready = model_name in ollama_status.models if ollama_status.connected else False

        if ollama_status.connected and not ollama_status.models:
            model_message = "No local models installed"
            guidance = OLLAMA_NO_MODELS_GUIDANCE
        elif model_ready:
            model_message = f"Using {model_name}"
            guidance = READY_GUIDANCE
        elif ollama_status.connected:
            model_message = f"Selected model '{model_name or 'none'}' is not installed"
            guidance = OLLAMA_MODEL_MISSING_GUIDANCE
        else:
            model_message = "Model unavailable until Ollama connects"
            guidance = OLLAMA_OFFLINE_GUIDANCE

        if not (stt_ready and tts_ready):
            guidance = VOICE_MODELS_GUIDANCE
        if not mic_ready:
            guidance = MIC_GUIDANCE
        if not output_ready:
            guidance = OUTPUT_GUIDANCE
        if not ollama_status.connected:
            guidance = OLLAMA_OFFLINE_GUIDANCE

        return StatusSnapshot(
            ollama_connected=ollama_status.connected,
            ollama_message=ollama_status.message,
            available_models=ollama_status.models,
            model_ready=model_ready,
            model_name=model_name,
            model_message=model_message,
            mic_available=mic_ready,
            mic_message=mic_message,
            output_available=output_ready,
            output_message=output_message,
            stt_ready=stt_ready,
            stt_message=stt_message,
            tts_ready=tts_ready,
            tts_message=tts_message,
            guidance_message=guidance,
        )
