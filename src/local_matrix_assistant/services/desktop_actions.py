from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import subprocess


class DesktopActionError(RuntimeError):
    """Raised when a requested local desktop action cannot be completed."""


@dataclass(frozen=True, slots=True)
class DesktopAction:
    kind: str
    target: str
    content: str = ""
    title: str = ""
    generate_content: bool = False
    auto_unique: bool = False


@dataclass(frozen=True, slots=True)
class DesktopActionResult:
    kind: str
    message: str
    target: str


_POLITE_PREFIX = r"(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?"
_OPEN_COMMAND = re.compile(
    rf"^\s*{_POLITE_PREFIX}(?:open|launch|start)(?:\s+up)?\s+(?:the\s+)?(?P<target>.+?)\s*[.!?]*\s*$",
    re.IGNORECASE,
)
_CREATE_FILE_COMMAND = re.compile(
    rf"^\s*{_POLITE_PREFIX}(?:create|make)(?:\s+me)?\s+(?:a\s+)?(?:new\s+)?file"
    r"(?:\s+(?:called|named))?\s+(?P<details>.+?)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_INCOMPLETE_CREATE_FILE_COMMAND = re.compile(
    rf"^\s*{_POLITE_PREFIX}(?:create|make)(?:\s+me)?\s+(?:a\s+)?(?:new\s+)?file\s*[.!?]*\s*$",
    re.IGNORECASE,
)
_CREATE_WORD_DOCUMENT_COMMAND = re.compile(
    rf"^\s*{_POLITE_PREFIX}(?:create|make)(?:\s+me)?\s+(?:a\s+)?(?:new\s+)?"
    r"(?:word\s+(?:document|file)|docx(?:\s+(?:document|file))?)"
    r"(?P<details>.*?)\s*[.!?]*\s*$",
    re.IGNORECASE | re.DOTALL,
)
_WORD_CONTENT_PREFIX = re.compile(
    r"^(?P<prefix>about|on|covering|outlining|that\s+outlines?)\s+(?P<topic>.+)$",
    re.IGNORECASE | re.DOTALL,
)
_WORD_LITERAL_CONTENT_PREFIX = re.compile(
    r"^with\s+(?:(?:the\s+)?(?:content|text))\s*:?[ \t]*(?P<content>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_WORD_NAMED_PREFIX = re.compile(r"^(?:called|named)\s+", re.IGNORECASE)
_WORD_NAMED_CONTENT_MARKER = re.compile(
    r"\s+(?=(?:about|on|covering|outlining|that\s+outlines?|with\s+(?:(?:the\s+)?(?:content|text)))\b)",
    re.IGNORECASE,
)
_CONTENT_SEPARATOR = re.compile(
    r"\s+(?:with\s+(?:(?:the\s+)?(?:content|text))|containing|that\s+contains)(?:\s*:\s*|\s+|$)",
    re.IGNORECASE,
)
_SAFE_APP_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+()-]{0,100}$")


class DesktopActionService:
    """Parse and execute narrow, explicit app-launch and file-create commands."""

    APP_TARGETS = {
        "browser": "https://www.google.com",
        "calculator": "calc.exe",
        "command prompt": "cmd.exe",
        "cmd": "cmd.exe",
        "file explorer": "explorer.exe",
        "explorer": "explorer.exe",
        "notepad": "notepad.exe",
        "paint": "mspaint.exe",
        "powershell": "powershell.exe",
        "settings": "ms-settings:",
        "task manager": "taskmgr.exe",
        "terminal": "wt.exe",
        "windows terminal": "wt.exe",
    }
    BLOCKED_ARTIFACT_EXTENSIONS = {
        ".appref-ms",
        ".com",
        ".cpl",
        ".dll",
        ".exe",
        ".hta",
        ".inf",
        ".jar",
        ".lnk",
        ".msi",
        ".msp",
        ".reg",
        ".scr",
        ".sys",
        ".url",
    }
    DOCUMENT_ARTIFACT_EXTENSIONS = {
        ".bmp",
        ".doc",
        ".docx",
        ".gif",
        ".jpeg",
        ".jpg",
        ".odt",
        ".pdf",
        ".png",
        ".svg",
        ".webp",
        ".xls",
        ".xlsx",
    }
    BROWSER_ARTIFACT_EXTENSIONS = {
        ".htm",
        ".html",
        ".xhtml",
    }

    def __init__(
        self,
        default_files_dir: Path | None = None,
        *,
        working_folders: list[str] | None = None,
        active_working_folder: str = "",
    ) -> None:
        self.default_files_dir = default_files_dir or (Path.home() / "Documents" / "Paco Files")
        self.update_working_folders(working_folders or [], active_working_folder)

    def update_working_folders(self, folders: list[str], active_folder: str = "") -> None:
        normalized: list[Path] = []
        seen: set[str] = set()
        for folder in folders:
            if not folder.strip():
                continue
            resolved = Path(os.path.abspath(os.path.expanduser(folder)))
            key = os.path.normcase(str(resolved))
            if key not in seen:
                seen.add(key)
                normalized.append(resolved)

        active = Path(os.path.abspath(os.path.expanduser(active_folder))) if active_folder.strip() else None
        if active is None or os.path.normcase(str(active)) not in seen:
            active = normalized[0] if normalized else None
        self.working_folders = normalized
        self.active_working_folder = active

    def parse(self, text: str) -> DesktopAction | None:
        word_match = _CREATE_WORD_DOCUMENT_COMMAND.match(text)
        if word_match:
            return self._parse_create_word_document(word_match.group("details"))

        create_match = _CREATE_FILE_COMMAND.match(text)
        if create_match:
            return self._parse_create_file(create_match.group("details"))
        if _INCOMPLETE_CREATE_FILE_COMMAND.match(text):
            return DesktopAction(kind="create_file", target="untitled.txt", auto_unique=True)

        open_match = _OPEN_COMMAND.match(text)
        if not open_match:
            return None
        target = self._clean_app_target(open_match.group("target"))
        if not target:
            raise DesktopActionError("No app name was provided.")
        return DesktopAction(kind="open_app", target=target)

    def execute(self, action: DesktopAction) -> DesktopActionResult:
        if action.kind == "create_file":
            return self._create_file(action)
        if action.kind == "open_app":
            return self._open_app(action)
        raise DesktopActionError(f"Unsupported desktop action: {action.kind}")

    def _parse_create_word_document(self, details: str) -> DesktopAction:
        cleaned = self._strip_path_sentence_punctuation(details.strip())
        if not cleaned:
            return DesktopAction(
                kind="create_word_document",
                target="document.docx",
                title="Document",
                auto_unique=True,
            )

        raw_target = ""
        instruction = cleaned
        if _WORD_NAMED_PREFIX.match(cleaned):
            named_details = _WORD_NAMED_PREFIX.sub("", cleaned, count=1).strip()
            marker = _WORD_NAMED_CONTENT_MARKER.search(named_details)
            if marker:
                raw_target = named_details[: marker.start()].strip()
                instruction = named_details[marker.end() :].strip()
            else:
                raw_target = named_details
                instruction = ""
            if raw_target[:1] in {'"', "'"} and (len(raw_target) < 2 or raw_target[-1] != raw_target[0]):
                raise DesktopActionError("The Word document name has an unmatched quote.")
            raw_target = self._strip_matching_quotes(raw_target)
            if not raw_target:
                raise DesktopActionError("No Word document name was provided.")
        elif self._looks_like_word_filename(cleaned):
            raw_target = self._strip_matching_quotes(cleaned)
            instruction = ""

        literal_match = _WORD_LITERAL_CONTENT_PREFIX.match(instruction)
        topic_match = _WORD_CONTENT_PREFIX.match(instruction)
        if literal_match:
            content = self._strip_matching_quotes(literal_match.group("content").strip())
            generate_content = False
            topic = ""
        elif topic_match:
            topic = self._strip_matching_quotes(topic_match.group("topic").strip())
            if not topic:
                raise DesktopActionError("No Word document topic was provided.")
            content = instruction
            generate_content = True
        elif instruction:
            topic = self._strip_matching_quotes(instruction)
            content = instruction
            generate_content = True
        else:
            topic = ""
            content = ""
            generate_content = False

        title = self._title_from_topic(topic) if topic else self._title_from_filename(raw_target or "document.docx")
        target = raw_target or f"{self._filename_slug(topic) or 'document'}.docx"
        if Path(target).suffix.lower() != ".docx":
            target = str(Path(target).with_suffix(".docx"))
        return DesktopAction(
            kind="create_word_document",
            target=target,
            content=content,
            title=title,
            generate_content=generate_content,
            auto_unique=not bool(raw_target),
        )

    def _parse_create_file(self, details: str) -> DesktopAction:
        split = self._find_unquoted_content_separator(details)
        if split:
            raw_path = details[: split.start()].strip()
            content = details[split.end() :].strip()
        else:
            raw_path = self._strip_path_sentence_punctuation(details.strip())
            content = ""

        if raw_path[:1] in {'"', "'"} and (len(raw_path) < 2 or raw_path[-1] != raw_path[0]):
            raise DesktopActionError("The file name has an unmatched quote.")
        raw_path = self._strip_matching_quotes(raw_path)
        content = self._strip_matching_quotes(content)
        if not raw_path:
            raise DesktopActionError("No file name was provided.")
        if "\x00" in raw_path:
            raise DesktopActionError("The file name is invalid.")
        return DesktopAction(kind="create_file", target=raw_path, content=content)

    @staticmethod
    def _find_unquoted_content_separator(details: str) -> re.Match[str] | None:
        for match in _CONTENT_SEPARATOR.finditer(details):
            active_quote = ""
            for index, character in enumerate(details[: match.start()]):
                if character not in {'"', "'"}:
                    continue
                if not active_quote:
                    if index == 0 or details[index - 1].isspace():
                        active_quote = character
                elif active_quote == character:
                    active_quote = ""
            if not active_quote:
                return match
        return None

    def _create_file(self, action: DesktopAction) -> DesktopActionResult:
        destination = self.resolve_output_path(action.target, default_suffix=".txt")
        if action.auto_unique:
            destination = self._available_destination(destination)

        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("x", encoding="utf-8", newline="") as handle:
                handle.write(action.content)
        except FileExistsError as exc:
            raise DesktopActionError(f"The file already exists and was not overwritten: {destination}") from exc
        except OSError as exc:
            raise DesktopActionError(f"Could not create the file: {exc}") from exc

        return DesktopActionResult(
            kind=action.kind,
            message=f"Created file: {destination}",
            target=str(destination),
        )

    @staticmethod
    def _available_destination(destination: Path) -> Path:
        if not destination.exists():
            return destination
        for index in range(2, 10_000):
            candidate = destination.with_name(f"{destination.stem}-{index}{destination.suffix}")
            if not candidate.exists():
                return candidate
        raise DesktopActionError("Could not choose an unused file name.")

    def resolve_output_path(self, target: str, *, default_suffix: str = "") -> Path:
        """Resolve a requested output path inside the active/allowed Agent folder."""
        if "\x00" in target:
            raise DesktopActionError("The file name is invalid.")
        requested = Path(target).expanduser()
        if requested.name in {"", ".", ".."}:
            raise DesktopActionError("The file name is invalid.")
        is_dotfile = requested.name.startswith(".") and len(requested.name) > 1
        if default_suffix and not requested.suffix and not is_dotfile:
            requested = requested.with_suffix(default_suffix)

        default_root = self.default_files_dir.resolve()
        allowed_roots = [default_root, *(folder.resolve() for folder in self.working_folders)]
        if requested.is_absolute():
            destination = requested.resolve()
            if not any(self._is_inside(destination, root) for root in allowed_roots):
                raise DesktopActionError("Choose that folder in the Agent tab before creating files there.")
        else:
            base = (self.active_working_folder or default_root).resolve()
            destination = (base / requested).resolve()
            try:
                destination.relative_to(base)
            except ValueError as exc:
                raise DesktopActionError("Relative file paths must stay inside the active working folder.") from exc
        return destination

    def open_artifact_file(self, target: str) -> Path:
        path = self._resolve_allowed_artifact(target, expect_file=True)
        if path.suffix.casefold() in self.BLOCKED_ARTIFACT_EXTENSIONS:
            raise DesktopActionError(
                "Executable artifacts are not opened directly. Use Open Folder and review the file first."
            )
        startfile = getattr(os, "startfile", None)
        if startfile is None:
            raise DesktopActionError("Opening generated files is supported on Windows only.")
        open_extensions = self.DOCUMENT_ARTIFACT_EXTENSIONS | self.BROWSER_ARTIFACT_EXTENSIONS
        operation = "open" if path.suffix.casefold() in open_extensions else "edit"
        try:
            startfile(str(path), operation)
        except OSError as exc:
            raise DesktopActionError(f"Windows could not open the file: {exc}") from exc
        return path

    def open_artifact_folder(self, target: str) -> Path:
        path = self._resolve_allowed_artifact(target)
        directory = path if path.is_dir() else path.parent
        startfile = getattr(os, "startfile", None)
        if startfile is None:
            raise DesktopActionError("Opening generated-file folders is supported on Windows only.")
        try:
            startfile(str(directory), "open")
        except OSError as exc:
            raise DesktopActionError(f"Windows could not open the folder: {exc}") from exc
        return directory

    def _resolve_allowed_artifact(self, target: str, *, expect_file: bool = False) -> Path:
        if not target.strip() or "\x00" in target:
            raise DesktopActionError("The saved artifact path is invalid.")
        requested = Path(target).expanduser()
        if not requested.is_absolute():
            raise DesktopActionError("Saved artifact actions require an absolute path.")
        try:
            path = requested.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise DesktopActionError("The saved artifact no longer exists.") from exc
        allowed_roots = [
            self.default_files_dir.resolve(),
            *(folder.resolve() for folder in self.working_folders),
        ]
        if not any(self._is_inside(path, root) for root in allowed_roots):
            raise DesktopActionError("Choose the artifact's folder in Agent before opening it.")
        if expect_file and not path.is_file():
            raise DesktopActionError("The saved artifact is not a file.")
        if not expect_file and not (path.is_file() or path.is_dir()):
            raise DesktopActionError("The saved artifact is unavailable.")
        return path

    @staticmethod
    def _is_inside(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _open_app(self, action: DesktopAction) -> DesktopActionResult:
        requested_name = action.target
        normalized_name = self._normalize_app_name(requested_name)
        configured_target = self.APP_TARGETS.get(normalized_name)

        if configured_target:
            self._launch_target(configured_target)
            return DesktopActionResult(
                kind=action.kind,
                message=f"Opened {requested_name}.",
                target=requested_name,
            )

        explicit_path = Path(requested_name).expanduser()
        if explicit_path.is_absolute() and explicit_path.exists() and explicit_path.suffix.lower() in {".exe", ".lnk"}:
            self._launch_target(str(explicit_path))
            return DesktopActionResult(action.kind, f"Opened {explicit_path.stem}.", str(explicit_path))

        if not _SAFE_APP_NAME.fullmatch(requested_name):
            raise DesktopActionError("Use an app name or an absolute .exe/.lnk path.")

        shortcut = self._find_shortcut(requested_name)
        if shortcut:
            self._launch_target(str(shortcut))
            return DesktopActionResult(action.kind, f"Opened {shortcut.stem}.", str(shortcut))

        executable_name = requested_name if requested_name.lower().endswith(".exe") else f"{requested_name}.exe"
        executable = shutil.which(executable_name)
        if executable:
            self._launch_target(executable)
            return DesktopActionResult(action.kind, f"Opened {requested_name}.", executable)

        raise DesktopActionError(f"App not found: {requested_name}. Use its Windows Start menu name.")

    @staticmethod
    def _launch_target(target: str) -> None:
        if target.startswith(("http://", "https://", "ms-")) or target.lower().endswith((".lnk", ".url")):
            startfile = getattr(os, "startfile", None)
            if startfile is None:
                raise DesktopActionError("Opening shortcuts is supported on Windows only.")
            try:
                startfile(target)
            except OSError as exc:
                raise DesktopActionError(f"Windows could not open the app: {exc}") from exc
            return

        try:
            subprocess.Popen(
                [shutil.which(target) or target],
                close_fds=True,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
        except OSError as exc:
            raise DesktopActionError(f"Windows could not open the app: {exc}") from exc

    @classmethod
    def _clean_app_target(cls, target: str) -> str:
        cleaned = target.strip()
        previous = None
        while cleaned != previous:
            previous = cleaned
            cleaned = cleaned.rstrip(" ,")
            cleaned = re.sub(r"\s+(?:for\s+me|please|app|application)\s*$", "", cleaned, flags=re.IGNORECASE)
        return cleaned.strip().strip('"\'')

    @staticmethod
    def _strip_path_sentence_punctuation(value: str) -> str:
        if len(value) >= 3 and value[-1] in ".!?" and value[0] in {'"', "'"} and value[-2] == value[0]:
            return value[:-1]
        value = value.rstrip("!?")
        if value.endswith("."):
            return value[:-1]
        return value

    @staticmethod
    def _strip_matching_quotes(value: str) -> str:
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            return value[1:-1]
        return value

    @staticmethod
    def _normalize_app_name(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()

    @classmethod
    def _looks_like_word_filename(cls, value: str) -> bool:
        stripped = cls._strip_matching_quotes(value)
        return bool(re.fullmatch(r"[^\r\n]+\.docx?", stripped, flags=re.IGNORECASE))

    @staticmethod
    def _title_from_filename(filename: str) -> str:
        stem = Path(filename).stem
        title = re.sub(r"[_-]+", " ", stem).strip()
        return title.title() or "Document"

    @staticmethod
    def _title_from_topic(topic: str) -> str:
        title = re.sub(r"\s+", " ", topic).strip(" .!?")
        return title[:1].upper() + title[1:] if title else "Document"

    @staticmethod
    def _filename_slug(topic: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")
        slug = slug[:64].rstrip("-")
        if re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", slug, flags=re.IGNORECASE):
            slug = f"document-{slug.lower()}"
        return slug

    def _find_shortcut(self, requested_name: str) -> Path | None:
        desired = self._normalize_app_name(requested_name)
        if not desired:
            return None

        candidates: list[tuple[int, int, Path]] = []
        for root in self._shortcut_roots():
            if not root.exists():
                continue
            for directory, _subdirs, files in os.walk(root, onerror=lambda _error: None):
                for filename in files:
                    if not filename.lower().endswith((".lnk", ".url")):
                        continue
                    path = Path(directory) / filename
                    normalized_stem = self._normalize_app_name(path.stem)
                    if normalized_stem == desired:
                        candidates.append((0, 0, path))
                    elif len(desired) >= 4 and desired in normalized_stem:
                        candidates.append((1, len(normalized_stem) - len(desired), path))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1], str(item[2]).lower()))
        return candidates[0][2]

    @staticmethod
    def _shortcut_roots() -> list[Path]:
        roots: list[Path] = []
        program_data = os.environ.get("PROGRAMDATA")
        app_data = os.environ.get("APPDATA")
        public_dir = os.environ.get("PUBLIC")
        if program_data:
            roots.append(Path(program_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
        if app_data:
            roots.append(Path(app_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
        roots.append(Path.home() / "Desktop")
        if public_dir:
            roots.append(Path(public_dir) / "Desktop")
        return roots
