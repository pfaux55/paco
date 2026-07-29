from __future__ import annotations

import argparse
import sys
import time
import wave
from io import BytesIO
from pathlib import Path

import miniaudio

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from local_matrix_assistant.core.config import AppConfig, AppPaths, resolve_ollama_windows_path
from local_matrix_assistant.core.models import ChatMessage
from local_matrix_assistant.services.audio import AudioPlayer, AudioRecorder
from local_matrix_assistant.services.history import HistoryStore
from local_matrix_assistant.services.ollama import OllamaClient
from local_matrix_assistant.services.stt import SttService
from local_matrix_assistant.services.tts import TtsService


def _wav_duration_seconds(wav_bytes: bytes) -> float:
    with wave.open(BytesIO(wav_bytes), "rb") as wav_file:
        frame_count = wav_file.getnframes()
        sample_rate = wav_file.getframerate()
    return frame_count / sample_rate if sample_rate else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local Windows-first checks for Local Matrix Assistant.")
    parser.add_argument("--voice-roundtrip", action="store_true", help="Record microphone audio, send it through STT -> Ollama -> TTS, and play the reply locally.")
    parser.add_argument("--voice-seconds", type=int, default=6, help="How many seconds to record when --voice-roundtrip is enabled.")
    args = parser.parse_args()

    paths = AppPaths.create()
    config = AppConfig.load(paths)
    ollama = OllamaClient(config.ollama_base_url)
    stt = SttService(config.stt_model_dir)
    tts = TtsService(config.tts_model_path, config.tts_config_path)
    recorder = AudioRecorder(config.preferred_input_name)
    player = AudioPlayer(config.playback_output_name)

    print("== Local Matrix Assistant Self Check ==")
    print(f"Resolved Ollama binary: {resolve_ollama_windows_path(config.ollama_windows_path) or '(not found)'}")
    print(f"Configured voice enabled: {config.voice_enabled}")
    print(f"Configured web search enabled: {config.web_search_enabled}")

    ollama_status = ollama.status()
    print(f"Ollama connected: {ollama_status.connected}")
    print(f"Ollama detail: {ollama_status.message}")
    print(f"Ollama models: {', '.join(ollama_status.models) if ollama_status.models else '(none)'}")

    stt_ready, stt_message = stt.ready()
    tts_ready, tts_message = tts.ready()
    print(f"STT ready: {stt_ready} | {stt_message}")
    print(f"TTS ready: {tts_ready} | {tts_message}")
    print(f"Installed voices: {[voice.label for voice in tts.list_voices()]}")
    print(f"Selected voice: {tts.current_voice().label if tts.current_voice() else '(missing)'}")
    print(f"Speaker output: {player.has_output()}")

    captures = [device["name"] for device in miniaudio.Devices([miniaudio.Backend.WASAPI]).get_captures()]
    playbacks = [device["name"] for device in miniaudio.Devices([miniaudio.Backend.WASAPI]).get_playbacks()]
    print(f"Capture devices: {captures}")
    print(f"Playback devices: {playbacks}")
    print(f"Microphone ready: {recorder.has_input()}")

    if tts_ready and stt_ready:
        phrase = "matrix systems local voice check"
        try:
            wav_bytes = tts.synthesize(phrase, rate=config.tts_rate, volume=config.tts_volume)
            sample_path = paths.cache_dir / "self_check_tts.wav"
            sample_path.write_bytes(wav_bytes)
            print(f"TTS sample written: {sample_path}")
            transcript = stt.transcribe(wav_bytes)
            print(f"Round-trip transcript: {transcript}")
            player.play_wav(wav_bytes)
            print("TTS playback: started")
            time.sleep(min(_wav_duration_seconds(wav_bytes) + 0.5, 6.0))
            print(f"TTS playback active after wait: {player.is_playing()}")
        except Exception as exc:  # noqa: BLE001
            print(f"Voice round-trip failed: {exc}")
    else:
        print("Skipping STT/TTS round-trip because one or both voice models are missing.")

    if ollama_status.connected and ollama_status.models:
        model_name = config.ollama_model or ollama_status.models[0]
        reply = ollama.chat(
            model_name,
            [
                ChatMessage(
                    role="user",
                    content="Reply in one short sentence confirming you are local.",
                    timestamp=HistoryStore.now_stamp(),
                )
            ],
        )
        print(f"Ollama chat ({model_name}): {reply}")
    else:
        print("Skipping Ollama chat because the local service or model is unavailable.")

    if args.voice_roundtrip:
        if not (ollama_status.connected and ollama_status.models and stt_ready and tts_ready):
            print("Skipping live voice round-trip because Ollama/STT/TTS are not all ready.")
            return 1

        model_name = config.ollama_model or ollama_status.models[0]
        print(f"Voice round-trip: speak a short phrase into your microphone during the next {args.voice_seconds} seconds.")
        recorder.start()
        time.sleep(args.voice_seconds)
        recorded_wav = recorder.stop()
        duration_seconds, average_level = recorder.inspect_wav(recorded_wav)
        print(f"Microphone capture: {duration_seconds:.2f}s | average level {average_level}")
        transcript = stt.transcribe(recorded_wav)
        print(f"Microphone transcript: {transcript}")
        reply = ollama.chat(
            model_name,
            [
                ChatMessage(
                    role="user",
                    content=transcript,
                    timestamp=HistoryStore.now_stamp(),
                )
            ],
        )
        print(f"Ollama reply: {reply}")
        reply_wav = tts.synthesize(reply, rate=config.tts_rate, volume=config.tts_volume)
        player.play_wav(reply_wav)
        print("Reply playback: started")
        time.sleep(min(_wav_duration_seconds(reply_wav) + 0.5, 8.0))
        print(f"Reply playback active after wait: {player.is_playing()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
