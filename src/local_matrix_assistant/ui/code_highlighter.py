from __future__ import annotations

import re

from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextDocument


LANGUAGE_ALIASES = {
    "py": "python",
    "python3": "python",
    "js": "javascript",
    "jsx": "javascript",
    "node": "javascript",
    "ts": "typescript",
    "tsx": "typescript",
    "sh": "shell",
    "bash": "shell",
    "zsh": "shell",
    "ps1": "shell",
    "powershell": "shell",
    "html": "markup",
    "htm": "markup",
    "xml": "markup",
    "scss": "css",
    "c++": "cpp",
    "cs": "csharp",
    "c#": "csharp",
    "yml": "yaml",
}

KEYWORDS = {
    "python": (
        "and as assert async await break case class continue def del elif else except False "
        "finally for from global if import in is lambda match None nonlocal not or pass raise "
        "return True try while with yield"
    ).split(),
    "javascript": (
        "async await break case catch class const continue debugger default delete do else export "
        "extends false finally for from function if import in instanceof let new null of return static "
        "super switch this throw true try typeof undefined var void while with yield"
    ).split(),
    "typescript": (
        "abstract any as async await boolean break case catch class const constructor continue "
        "declare default delete do else enum export extends false finally for from function get if "
        "implements import in infer instanceof interface keyof let namespace never new null number "
        "object of private protected public readonly return set static string super switch symbol this "
        "throw true try type typeof undefined unknown var void while yield"
    ).split(),
    "shell": (
        "case do done elif else esac export fi for function if in local return select then until while"
    ).split(),
    "sql": (
        "alter and as asc begin between by case commit create delete desc distinct drop else end exists "
        "from full group having in index inner insert into is join left like limit not null on or order "
        "outer primary references right rollback select set table then union unique update values when "
        "where with"
    ).split(),
    "c_like": (
        "abstract as async await bool break case catch char class const continue default defer do else "
        "enum extern false final finally float for foreach func function if implements import in int "
        "interface internal let long namespace new nil null override package private protected public "
        "return short signed static string struct super switch this throw trait true try type unsigned "
        "using var virtual void volatile while"
    ).split(),
}

C_LIKE_LANGUAGES = {
    "c",
    "cpp",
    "csharp",
    "java",
    "kotlin",
    "go",
    "rust",
    "swift",
    "dart",
    "php",
}


def language_family(language: str) -> str:
    normalized = LANGUAGE_ALIASES.get(language.strip().casefold(), language.strip().casefold())
    if normalized in C_LIKE_LANGUAGES:
        return "c_like"
    if normalized in {"json", "yaml", "toml"}:
        return "data"
    if normalized in {"python", "javascript", "typescript", "shell", "sql", "markup", "css"}:
        return normalized
    return "generic"


class CodeSyntaxHighlighter(QSyntaxHighlighter):
    """Small dependency-free highlighter for common local-agent output languages."""

    def __init__(self, document: QTextDocument, language: str) -> None:
        super().__init__(document)
        self.family = language_family(language)
        self.keyword_format = _format("#5de392", bold=True)
        self.type_format = _format("#78b8e8")
        self.string_format = _format("#d7bd74")
        self.comment_format = _format("#668b73", italic=True)
        self.number_format = _format("#b7a0e8")
        self.function_format = _format("#72d1d8")
        self.tag_format = _format("#5de392")
        self.attribute_format = _format("#78b8e8")
        self._rules = self._build_rules()

    def _build_rules(self) -> list[tuple[re.Pattern[str], QTextCharFormat]]:
        rules: list[tuple[re.Pattern[str], QTextCharFormat]] = []
        keywords = KEYWORDS.get(self.family, ())
        if keywords:
            flags = re.IGNORECASE if self.family == "sql" else 0
            rules.append(
                (re.compile(r"\b(?:" + "|".join(map(re.escape, keywords)) + r")\b", flags), self.keyword_format)
            )

        if self.family == "python":
            rules.append((re.compile(r"\b(?:self|cls|str|int|float|bool|list|dict|set|tuple)\b"), self.type_format))
            rules.append((re.compile(r"\b(?:def|class)\s+([A-Za-z_]\w*)"), self.function_format))
        elif self.family in {"javascript", "typescript", "c_like"}:
            rules.append(
                (
                    re.compile(r"\b([A-Za-z_$][\w$]*)\s*(?=\()"),
                    self.function_format,
                )
            )
        elif self.family == "markup":
            rules.extend(
                [
                    (re.compile(r"</?\s*[A-Za-z][\w:-]*"), self.tag_format),
                    (re.compile(r"\b[A-Za-z_:][\w:.-]*(?=\s*=)"), self.attribute_format),
                ]
            )
        elif self.family == "data":
            rules.append((re.compile(r'"(?:\\.|[^"\\])*"(?=\s*:)'), self.attribute_format))

        rules.append((re.compile(r"\b(?:0x[0-9A-Fa-f]+|\d+(?:\.\d+)?)\b"), self.number_format))
        if self.family in {"python", "shell"}:
            rules.append((re.compile(r"#.*$"), self.comment_format))
        elif self.family == "sql":
            rules.append((re.compile(r"--.*$"), self.comment_format))
        elif self.family in {"javascript", "typescript", "c_like", "css"}:
            rules.append((re.compile(r"//.*$"), self.comment_format))

        rules.extend(
            [
                (re.compile(r'"(?:\\.|[^"\\])*"'), self.string_format),
                (re.compile(r"'(?:\\.|[^'\\])*'"), self.string_format),
            ]
        )
        if self.family in {"javascript", "typescript", "shell"}:
            rules.append((re.compile(r"`(?:\\.|[^`\\])*`"), self.string_format))
        return rules

    def highlightBlock(self, text: str) -> None:  # type: ignore[override]
        for pattern, char_format in self._rules:
            for match in pattern.finditer(text):
                start, end = match.span(1) if match.lastindex else match.span()
                self.setFormat(start, end - start, char_format)
        self._highlight_block_comments(text)

    def _highlight_block_comments(self, text: str) -> None:
        if self.family not in {"javascript", "typescript", "c_like", "css"}:
            return
        self.setCurrentBlockState(0)
        start = 0 if self.previousBlockState() == 1 else text.find("/*")
        while start >= 0:
            end = text.find("*/", start + (0 if self.previousBlockState() == 1 else 2))
            if end < 0:
                self.setCurrentBlockState(1)
                length = len(text) - start
            else:
                length = end - start + 2
            self.setFormat(start, length, self.comment_format)
            if end < 0:
                break
            start = text.find("/*", start + length)


def _format(color: str, *, bold: bool = False, italic: bool = False) -> QTextCharFormat:
    char_format = QTextCharFormat()
    char_format.setForeground(QColor(color))
    if bold:
        char_format.setFontWeight(QFont.Weight.DemiBold)
    char_format.setFontItalic(italic)
    return char_format
