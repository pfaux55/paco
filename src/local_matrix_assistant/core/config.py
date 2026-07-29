from __future__ import annotations

import base64
import binascii
import json
import math
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from json import JSONDecodeError
from pathlib import Path


MAX_CHAT_DRAFTS = 20
MAX_CHAT_DRAFT_CHARACTERS = 20_000
MAX_CHAT_DRAFT_TOTAL_CHARACTERS = 100_000
_CONVERSATION_ID_PATTERN = re.compile(r"[0-9a-f]{32}", re.IGNORECASE)


def _read_json_object(path: Path) -> dict | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return None
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except OSError:
        temporary_path.unlink(missing_ok=True)
        raise


def _settings_backup_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".bak")


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _home() -> Path:
    return Path.home()


WINDOWS_OLLAMA_GUESSES = [
    r"D:\Program Files\Ollama\ollama.exe",
    r"D:\Ollama\ollama.exe",
    r"C:\Program Files\Ollama\ollama.exe",
    r"C:\Users\%USERNAME%\AppData\Local\Programs\Ollama\ollama.exe",
]


def expand_windows_guess(path: str) -> str:
    username = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    expanded = path.replace("%USERNAME%", username)
    return os.path.expandvars(os.path.expanduser(expanded))


def resolve_ollama_windows_path(configured_path: str = "") -> str:
    candidates: list[str] = []
    if configured_path:
        candidates.append(configured_path)

    if discovered := shutil.which("ollama"):
        candidates.append(discovered)

    candidates.extend(expand_windows_guess(path) for path in WINDOWS_OLLAMA_GUESSES)

    for candidate in candidates:
        resolved = Path(candidate).expanduser()
        if resolved.exists():
            return str(resolved)
    return configured_path


def _coerce_voice_paths(data: dict, defaults: "AppConfig") -> None:
    stt_model_dir = Path(str(data.get("stt_model_dir", "")))
    tts_model_path = Path(str(data.get("tts_model_path", "")))
    tts_config_path = Path(str(data.get("tts_config_path", "")))

    if not stt_model_dir.exists() and Path(defaults.stt_model_dir).exists():
        data["stt_model_dir"] = defaults.stt_model_dir
    if not tts_model_path.exists() and Path(defaults.tts_model_path).exists():
        data["tts_model_path"] = defaults.tts_model_path
    if not tts_config_path.exists() and Path(defaults.tts_config_path).exists():
        data["tts_config_path"] = defaults.tts_config_path


def _coerce_scalar_values(data: dict, defaults: "AppConfig") -> None:
    string_fields = (
        "ollama_base_url",
        "ollama_model",
        "ollama_windows_path",
        "stt_model_dir",
        "tts_model_path",
        "tts_config_path",
        "preferred_input_name",
        "playback_output_name",
        "window_geometry",
    )
    for field in string_fields:
        value = data.get(field)
        data[field] = value.strip() if isinstance(value, str) else getattr(defaults, field)
    if not data["ollama_base_url"]:
        data["ollama_base_url"] = defaults.ollama_base_url

    boolean_fields = (
        "voice_enabled",
        "auto_speak_responses",
        "web_search_enabled",
        "microphone_muted",
    )
    for field in boolean_fields:
        value = data.get(field)
        data[field] = value if isinstance(value, bool) else getattr(defaults, field)

    for field, minimum, maximum in (
        ("tts_rate", 0.5, 1.5),
        ("tts_volume", 0.0, 1.5),
    ):
        value = data.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            data[field] = getattr(defaults, field)
        else:
            data[field] = min(maximum, max(minimum, float(value)))


def _coerce_working_folders(data: dict) -> None:
    raw_folders = data.get("working_folders", [])
    if not isinstance(raw_folders, list):
        raw_folders = []

    folders: list[str] = []
    seen: set[str] = set()
    for value in raw_folders:
        if not isinstance(value, str) or not value.strip():
            continue
        folder = str(Path(value).expanduser())
        key = os.path.normcase(os.path.abspath(folder))
        if key not in seen:
            seen.add(key)
            folders.append(folder)

    active = data.get("active_working_folder", "")
    active = str(active).strip() if isinstance(active, str) else ""
    folder_keys = {os.path.normcase(os.path.abspath(folder)): folder for folder in folders}
    active_key = os.path.normcase(os.path.abspath(active)) if active else ""
    selected = folder_keys.get(active_key, folders[0] if folders else "")
    data["working_folders"] = [selected] if selected else []
    data["active_working_folder"] = selected


def _coerce_model_profile(data: dict) -> None:
    profile = str(data.get("model_profile", "auto")).strip().lower()
    data["model_profile"] = profile if profile in {"auto", "fast", "balanced", "coding", "reasoning", "manual"} else "auto"


def _coerce_ui_preferences(data: dict) -> None:
    data["sidebar_collapsed"] = data.get("sidebar_collapsed") is True
    data["continuous_voice_enabled"] = data.get("continuous_voice_enabled") is True
    active_page = data.get("active_page")
    data["active_page"] = (
        active_page
        if isinstance(active_page, int)
        and not isinstance(active_page, bool)
        and 0 <= active_page <= 3
        else 0
    )
    geometry = data.get("window_geometry", "")
    try:
        decoded_geometry = base64.b64decode(geometry, validate=True) if geometry and len(geometry) <= 8192 else b""
    except (binascii.Error, ValueError):
        decoded_geometry = b""
    if not decoded_geometry or len(decoded_geometry) > 4096:
        data["window_geometry"] = ""
    last_conversation_id = data.get("last_conversation_id", "")
    data["last_conversation_id"] = (
        last_conversation_id.lower()
        if isinstance(last_conversation_id, str)
        and _CONVERSATION_ID_PATTERN.fullmatch(last_conversation_id)
        else ""
    )
    raw_drafts = data.get("chat_drafts", {})
    drafts: dict[str, str] = {}
    total_characters = 0
    if isinstance(raw_drafts, dict):
        for key, value in reversed(list(raw_drafts.items())):
            conversation_id = key.lower() if isinstance(key, str) else ""
            if (
                len(drafts) >= MAX_CHAT_DRAFTS
                or not _CONVERSATION_ID_PATTERN.fullmatch(conversation_id)
                or not isinstance(value, str)
                or not value.strip()
            ):
                continue
            draft = value[:MAX_CHAT_DRAFT_CHARACTERS]
            if total_characters + len(draft) > MAX_CHAT_DRAFT_TOTAL_CHARACTERS:
                continue
            drafts[conversation_id] = draft
            total_characters += len(draft)
    data["chat_drafts"] = dict(reversed(list(drafts.items())))
    if data["continuous_voice_enabled"]:
        data["voice_enabled"] = True
        data["auto_speak_responses"] = True


@dataclass(slots=True)
class AppPaths:
    root: Path
    data_dir: Path
    models_dir: Path
    stt_dir: Path
    tts_dir: Path
    cache_dir: Path
    chats_dir: Path
    history_file: Path
    settings_file: Path

    @classmethod
    def create(cls) -> "AppPaths":
        root = _project_root()
        data_dir = root / "data"
        models_dir = root / "models"
        stt_dir = models_dir / "stt"
        tts_dir = models_dir / "tts"
        cache_dir = root / "cache"
        chats_dir = data_dir / "chats"
        for directory in (data_dir, models_dir, stt_dir, tts_dir, cache_dir, chats_dir):
            directory.mkdir(parents=True, exist_ok=True)
        return cls(
            root=root,
            data_dir=data_dir,
            models_dir=models_dir,
            stt_dir=stt_dir,
            tts_dir=tts_dir,
            cache_dir=cache_dir,
            chats_dir=chats_dir,
            history_file=data_dir / "conversation_history.json",
            settings_file=data_dir / "settings.json",
        )


@dataclass(slots=True)
class AppConfig:
    ollama_base_url: str
    ollama_model: str
    ollama_windows_path: str
    stt_model_dir: str
    tts_model_path: str
    tts_config_path: str
    voice_enabled: bool
    auto_speak_responses: bool
    tts_rate: float
    tts_volume: float
    preferred_input_name: str
    playback_output_name: str
    web_search_enabled: bool
    working_folders: list[str]
    active_working_folder: str
    microphone_muted: bool = False
    continuous_voice_enabled: bool = False
    model_profile: str = "auto"
    sidebar_collapsed: bool = False
    window_geometry: str = ""
    active_page: int = 0
    last_conversation_id: str = ""
    chat_drafts: dict[str, str] = field(default_factory=dict)

    @classmethod
    def defaults(cls, paths: AppPaths) -> "AppConfig":
        configured_ollama_path = os.environ.get(
            "OLLAMA_WINDOWS_PATH",
            r"D:\Program Files\Ollama\ollama.exe",
        )
        return cls(
            ollama_base_url=os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            ollama_model=os.environ.get("OLLAMA_MODEL", ""),
            ollama_windows_path=resolve_ollama_windows_path(configured_ollama_path),
            stt_model_dir=os.environ.get(
                "STT_MODEL_DIR",
                str(paths.stt_dir / "vosk-model-small-en-us-0.15"),
            ),
            tts_model_path=os.environ.get(
                "TTS_MODEL_PATH",
                str(paths.tts_dir / "en_US-lessac-low.onnx"),
            ),
            tts_config_path=os.environ.get(
                "TTS_CONFIG_PATH",
                str(paths.tts_dir / "en_US-lessac-low.onnx.json"),
            ),
            voice_enabled=True,
            auto_speak_responses=True,
            tts_rate=1.0,
            tts_volume=1.0,
            preferred_input_name="",
            playback_output_name="",
            web_search_enabled=False,
            working_folders=[],
            active_working_folder="",
            microphone_muted=False,
            continuous_voice_enabled=False,
            model_profile="auto",
            sidebar_collapsed=False,
            window_geometry="",
            active_page=0,
            last_conversation_id="",
            chat_drafts={},
        )

    @classmethod
    def load(cls, paths: AppPaths) -> "AppConfig":
        defaults = cls.defaults(paths)
        payload = _read_json_object(paths.settings_file)
        recovered_from_backup = payload is None
        if recovered_from_backup:
            payload = _read_json_object(_settings_backup_path(paths.settings_file))
        if payload is None:
            return defaults

        data = asdict(defaults)
        data.update({key: value for key, value in payload.items() if key in data})
        _coerce_scalar_values(data, defaults)
        data["ollama_windows_path"] = resolve_ollama_windows_path(str(data.get("ollama_windows_path", "")))
        _coerce_voice_paths(data, defaults)
        _coerce_working_folders(data)
        _coerce_model_profile(data)
        _coerce_ui_preferences(data)
        config = cls(**data)
        if recovered_from_backup:
            try:
                _atomic_write_json(paths.settings_file, asdict(config))
            except OSError:
                pass
        return config

    def save(self, paths: AppPaths) -> None:
        payload = asdict(self)
        payload["ollama_windows_path"] = resolve_ollama_windows_path(self.ollama_windows_path)
        _atomic_write_json(paths.settings_file, payload)
        try:
            _atomic_write_json(_settings_backup_path(paths.settings_file), payload)
        except OSError:
            pass
