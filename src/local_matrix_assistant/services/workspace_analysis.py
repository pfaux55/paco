from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from local_matrix_assistant.services.desktop_actions import DesktopActionError
from local_matrix_assistant.services.workspace_actions import WorkspaceActionService, WorkspaceFileSnapshot


@dataclass(frozen=True, slots=True)
class WorkspaceAnalysisContext:
    query: str
    root: Path
    manifest: str
    sources: str
    selected_files: tuple[str, ...]
    eligible_files: tuple[str, ...]
    discovered_files: int
    scanned_files: int
    scanned_bytes: int
    truncated: bool = False

    def scan_summary(self) -> str:
        qualifier = " (bounded scan)" if self.truncated else ""
        return (
            f"Reviewed {len(self.selected_files)} source files selected from {self.discovered_files} discovered "
            f"files; scanned {self.scanned_files} text files / {self.scanned_bytes:,} bytes{qualifier}."
        )


class WorkspaceAnalysisService:
    """Build a bounded, source-grounded local-model context from a selected workspace."""

    max_discovered_files = 240
    max_scanned_files = 180
    max_scanned_bytes = 6 * 1024 * 1024
    max_file_bytes = 256 * 1024
    max_manifest_characters = 2_500
    max_selected_files = 6
    max_source_characters = 11_000
    max_excerpt_characters = 5_200

    text_suffixes = {
        ".bat",
        ".c",
        ".cfg",
        ".cpp",
        ".cs",
        ".css",
        ".csv",
        ".go",
        ".h",
        ".hpp",
        ".html",
        ".ini",
        ".java",
        ".js",
        ".json",
        ".jsx",
        ".md",
        ".ps1",
        ".py",
        ".rs",
        ".sh",
        ".sql",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }
    extensionless_text_names = {
        "dockerfile",
        "license",
        "makefile",
        "procfile",
        "readme",
    }
    foundational_names = {
        "cargo.toml",
        "dockerfile",
        "package.json",
        "pyproject.toml",
        "readme.md",
        "requirements.txt",
        "setup.cfg",
        "setup.py",
    }
    source_suffixes = {".c", ".cpp", ".cs", ".go", ".java", ".js", ".jsx", ".py", ".rs", ".ts", ".tsx"}
    architecture_terms = {"architecture", "component", "entrypoint", "flow", "overview", "setup", "structure"}
    sensitive_names = {
        ".npmrc",
        ".pypirc",
        "credentials.json",
        "id_dsa",
        "id_ed25519",
        "id_rsa",
        "secrets.json",
    }
    sensitive_suffixes = {".key", ".p12", ".pem", ".pfx"}
    stop_words = {
        "are",
        "about",
        "analyze",
        "before",
        "codebase",
        "explain",
        "file",
        "files",
        "find",
        "fix",
        "from",
        "how",
        "investigate",
        "project",
        "review",
        "that",
        "this",
        "workspace",
        "with",
    }
    term_aliases = {
        "application": "app",
        "authentication": "auth",
        "authorization": "auth",
        "configuration": "config",
        "created": "create",
        "creating": "create",
        "creation": "create",
        "generated": "generate",
        "generating": "generate",
        "initialization": "init",
        "validation": "valid",
        "validated": "valid",
        "validates": "valid",
        "validating": "valid",
    }

    def __init__(self, workspace_actions: WorkspaceActionService) -> None:
        self.workspace_actions = workspace_actions

    def build(self, query: str) -> WorkspaceAnalysisContext:
        cleaned_query = re.sub(r"\s+", " ", query).strip()
        if not cleaned_query:
            cleaned_query = "Explain the architecture, main components, and execution flow."
        terms = self._query_terms(cleaned_query)
        root = self.workspace_actions.workspace_root()

        paths: list[Path] = []
        discovery_truncated = False
        for path in self.workspace_actions.iter_workspace_files():
            if not self._eligible(path):
                continue
            if len(paths) >= self.max_discovered_files:
                discovery_truncated = True
                break
            paths.append(path)
        paths.sort(key=lambda path: path.relative_to(root).as_posix().casefold())
        manifest, manifest_truncated = self._manifest(paths, root)

        ranked: list[tuple[int, WorkspaceFileSnapshot]] = []
        scanned_bytes = 0
        scanned_files = 0
        scan_truncated = False
        scan_order = sorted(
            paths,
            key=lambda path: (-self._path_score(path, root, terms), path.as_posix().casefold()),
        )
        for path in scan_order:
            if scanned_files >= self.max_scanned_files:
                scan_truncated = True
                break
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > self.max_file_bytes:
                scan_truncated = True
                continue
            if scanned_bytes + size > self.max_scanned_bytes:
                scan_truncated = True
                continue
            try:
                snapshot = self.workspace_actions.load_edit_target(str(path))
            except DesktopActionError:
                continue
            scanned_files += 1
            scanned_bytes += size
            ranked.append((self._content_score(snapshot, terms), snapshot))

        ranked.sort(key=lambda item: (-item[0], item[1].relative_path.casefold()))
        selected = ranked[: self.max_selected_files]
        source_parts: list[str] = []
        selected_files: list[str] = []
        source_characters = 0
        excerpt_truncated = False
        for _score, snapshot in selected:
            remaining = self.max_source_characters - source_characters
            if remaining <= 160:
                excerpt_truncated = True
                break
            file_limit = 1_800 if snapshot.path.suffix.casefold() == ".md" else self.max_excerpt_characters
            allowance = min(file_limit, remaining - 80)
            excerpt, shortened = self._excerpt(snapshot.content, terms, allowance)
            block = f"FILE: {snapshot.relative_path}\n{excerpt}"
            if source_parts:
                block = "\n\n" + block
            if len(block) > remaining:
                block = block[:remaining].rstrip() + "\n... source context truncated."
                shortened = True
            source_parts.append(block)
            selected_files.append(snapshot.relative_path)
            source_characters += len(block)
            excerpt_truncated = excerpt_truncated or shortened

        if not source_parts:
            raise DesktopActionError("No readable source or project files were found in the active Agent folder.")
        return WorkspaceAnalysisContext(
            query=cleaned_query,
            root=root,
            manifest=manifest,
            sources="".join(source_parts),
            selected_files=tuple(selected_files),
            eligible_files=tuple(path.relative_to(root).as_posix() for path in paths),
            discovered_files=len(paths),
            scanned_files=scanned_files,
            scanned_bytes=scanned_bytes,
            truncated=(
                discovery_truncated
                or manifest_truncated
                or scan_truncated
                or excerpt_truncated
            ),
        )

    def _eligible(self, path: Path) -> bool:
        name = path.name.casefold()
        if name == ".env" or name.startswith(".env."):
            return False
        if name in self.sensitive_names or path.suffix.casefold() in self.sensitive_suffixes:
            return False
        if name.startswith("secrets.") or name.startswith("credentials."):
            return False
        return path.suffix.casefold() in self.text_suffixes or name in self.extensionless_text_names

    def _manifest(self, paths: list[Path], root: Path) -> tuple[str, bool]:
        lines: list[str] = []
        characters = 0
        truncated = False
        for path in paths:
            relative = path.relative_to(root).as_posix()
            addition = ("\n" if lines else "") + relative
            if characters + len(addition) > self.max_manifest_characters:
                truncated = True
                break
            lines.append(relative)
            characters += len(addition)
        if truncated:
            lines.append("... manifest truncated.")
        return "\n".join(lines) or "(no eligible files)", truncated

    def _content_score(self, snapshot: WorkspaceFileSnapshot, terms: tuple[str, ...]) -> int:
        path = Path(snapshot.relative_path)
        score = self._path_score(path, Path("."), terms, already_relative=True)
        folded = snapshot.content.casefold()
        for term in terms:
            score += min(3, folded.count(term)) * 2
        return score

    def _path_score(
        self,
        path: Path,
        root: Path,
        terms: tuple[str, ...],
        *,
        already_relative: bool = False,
    ) -> int:
        relative = path if already_relative else path.relative_to(root)
        folded = relative.as_posix().casefold()
        name = relative.name.casefold()
        foundational_score = 12 if not terms or any(term in self.architecture_terms for term in terms) else 4
        score = foundational_score if name in self.foundational_names else 0
        if relative.suffix.casefold() in self.source_suffixes:
            score += 10
        if "tests" in relative.parts or name.startswith("test_"):
            score -= 4
        if name in {"app.py", "main.py", "index.js", "index.ts", "__init__.py"}:
            score += 5
        for term in terms:
            if term in name:
                score += 20
            elif term in folded:
                score += 12
        if any(term in {"bug", "fail", "test", "tests"} for term in terms) and "test" in folded:
            score += 6
        return score

    @classmethod
    def _query_terms(cls, query: str) -> tuple[str, ...]:
        terms: list[str] = []
        for term in re.findall(r"[a-zA-Z_][a-zA-Z0-9_-]{2,}", query.casefold()):
            if term in cls.stop_words:
                continue
            alias = cls.term_aliases.get(term)
            candidates = [alias or term]
            if alias is None and term.endswith("s") and len(term) > 4:
                candidates.append(term[:-1])
            for candidate in candidates:
                if candidate not in terms:
                    terms.append(candidate)
        return tuple(terms[:16])

    @staticmethod
    def _excerpt(content: str, terms: tuple[str, ...], allowance: int) -> tuple[str, bool]:
        lines = content.splitlines()
        if not lines:
            return "   1 | (empty file)", False
        folded_lines = [line.casefold() for line in lines]
        term_frequency = {
            term: sum(term in line for line in folded_lines)
            for term in terms
        }
        scored_hits: list[tuple[int, int]] = []
        for index, line in enumerate(folded_lines):
            matched = [term for term in terms if term in line]
            if not matched:
                continue
            score = sum(max(2, 12 - min(10, term_frequency[term])) for term in matched)
            stripped = line.lstrip()
            if stripped.startswith(("def ", "async def ", "class ")):
                score += 14
            if stripped.startswith(("def ", "async def ")) and any(
                marker in stripped for marker in ("_validate", "apply_", "prepare_")
            ):
                score += 10
            scored_hits.append((score, index))
        scored_hits.sort(key=lambda item: (-item[0], item[1]))
        chosen_hits: list[int] = []
        for _score, index in scored_hits:
            if any(abs(index - selected) <= 5 for selected in chosen_hits):
                continue
            chosen_hits.append(index)
            if len(chosen_hits) >= 3:
                break
        hits = chosen_hits
        ranges: list[tuple[int, int]] = []
        if hits:
            for hit in hits:
                structural = folded_lines[hit].lstrip().startswith(("def ", "async def ", "class "))
                end = min(len(lines), hit + 42 if structural else hit + 7)
                if structural:
                    indentation = len(lines[hit]) - len(lines[hit].lstrip())
                    for candidate in range(hit + 1, end):
                        stripped_candidate = lines[candidate].lstrip()
                        candidate_indentation = len(lines[candidate]) - len(stripped_candidate)
                        if candidate_indentation == indentation and stripped_candidate.startswith(
                            ("@", "def ", "async def ", "class ")
                        ):
                            end = candidate
                            break
                ranges.append(
                    (max(0, hit - 2 if structural else hit - 4), end)
                )
        else:
            ranges.append((0, min(len(lines), 80)))

        rendered: list[str] = []
        shortened = ranges[-1][1] < len(lines) or any(start > 0 for start, _end in ranges)
        for start, end in ranges:
            if rendered:
                rendered.append("     | ...")
            for index in range(start, end):
                line = f"{index + 1:>4} | {lines[index]}"
                if len("\n".join([*rendered, line])) > allowance:
                    rendered.append("     | ... excerpt truncated")
                    return "\n".join(rendered), True
                rendered.append(line)
        return "\n".join(rendered), shortened
