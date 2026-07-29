from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from local_matrix_assistant.core.voice_catalog import KNOWN_PIPER_VOICES

VOSK_MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
DEFAULT_VOICE_IDS = [
    "en_US-lessac-low",
    "en_US-amy-medium",
    "en_GB-alan-medium",
    "en_US-ryan-high",
]


def download_file(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=120) as response:
        response.raise_for_status()
        with destination.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)


def download_vosk(target_dir: Path) -> Path:
    zip_path = target_dir / "vosk-model-small-en-us-0.15.zip"
    model_dir = target_dir / "vosk-model-small-en-us-0.15"
    if model_dir.exists():
        return model_dir
    download_file(VOSK_MODEL_URL, zip_path)
    with zipfile.ZipFile(zip_path, "r") as archive:
        archive.extractall(target_dir)
    zip_path.unlink(missing_ok=True)
    return model_dir


def download_piper_voice(voice_id: str, target_dir: Path) -> tuple[Path, Path]:
    metadata = KNOWN_PIPER_VOICES.get(voice_id)
    if not metadata:
        raise KeyError(f"Unknown Piper voice '{voice_id}'. Available: {', '.join(sorted(KNOWN_PIPER_VOICES))}")
    model_path = target_dir / f"{voice_id}.onnx"
    config_path = target_dir / f"{voice_id}.onnx.json"
    if not model_path.exists():
        download_file(metadata["model_url"], model_path)
    if not config_path.exists():
        download_file(metadata["config_url"], config_path)
    return model_path, config_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Download local STT/TTS models for the assistant.")
    parser.add_argument("--root", default=ROOT, type=Path)
    parser.add_argument(
        "--voices",
        nargs="*",
        default=DEFAULT_VOICE_IDS,
        help="Voice ids to download. Defaults to a local set with multiple male and female Piper voices.",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    stt_dir = root / "models" / "stt"
    tts_dir = root / "models" / "tts"
    stt_dir.mkdir(parents=True, exist_ok=True)
    tts_dir.mkdir(parents=True, exist_ok=True)

    stt_model = download_vosk(stt_dir)
    print(f"STT model ready: {stt_model}")

    downloaded: list[tuple[str, Path, Path]] = []
    for voice_id in args.voices:
        model_path, config_path = download_piper_voice(voice_id, tts_dir)
        downloaded.append((voice_id, model_path, config_path))
        print(f"Voice ready: {voice_id} | {model_path.name}")

    print("Installed Piper voices:")
    for voice_id, model_path, config_path in downloaded:
        print(f"- {voice_id}: {model_path} | {config_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
