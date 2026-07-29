from __future__ import annotations

from dataclasses import dataclass
import re

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
)

from local_matrix_assistant.ui.inputs import NoWheelComboBox


@dataclass(frozen=True, slots=True)
class DiffSection:
    path: str
    text: str
    additions: int
    deletions: int
    hunks: int


_HEADER_PAIR = re.compile(r"^--- (?:a/|/dev/null(?:\s|$))")
_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")


def parse_unified_diff(diff: str, fallback_path: str = "Changes") -> list[DiffSection]:
    lines = diff.splitlines(keepends=True)
    starts = _header_starts(lines)
    if not starts:
        if not diff.strip():
            return []
        additions, deletions, hunks = diff_stats(diff)
        return [DiffSection(fallback_path or "Changes", diff, additions, deletions, hunks)]

    sections: list[DiffSection] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        section_text = "".join(lines[start:end]).rstrip()
        destination = _header_path(lines[start + 1][4:])
        source = _header_path(lines[start][4:])
        path = destination if destination != "/dev/null" else source
        additions, deletions, hunks = diff_stats(section_text)
        sections.append(
            DiffSection(
                path=path or fallback_path or "Changes",
                text=section_text,
                additions=additions,
                deletions=deletions,
                hunks=hunks,
            )
        )
    return sections


def diff_stats(diff: str) -> tuple[int, int, int]:
    additions = 0
    deletions = 0
    hunks = 0
    for line in diff.splitlines():
        if line.startswith("@@"):
            hunks += 1
        elif line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
    return additions, deletions, hunks


def _header_path(value: str) -> str:
    path = value.rstrip("\r\n").split("\t", 1)[0].strip()
    if path.startswith(("a/", "b/")):
        path = path[2:]
    return path


def _header_starts(lines: list[str]) -> list[int]:
    starts: list[int] = []
    old_remaining = 0
    new_remaining = 0
    in_hunk = False
    for index, line in enumerate(lines):
        if (
            not in_hunk
            and index + 1 < len(lines)
            and _HEADER_PAIR.match(line)
            and lines[index + 1].startswith("+++ ")
        ):
            starts.append(index)
            continue
        hunk = _HUNK_HEADER.match(line)
        if hunk:
            old_remaining = int(hunk.group(1) or 1)
            new_remaining = int(hunk.group(2) or 1)
            in_hunk = old_remaining > 0 or new_remaining > 0
            continue
        if not in_hunk or line.startswith("\\"):
            continue
        if line.startswith("+"):
            new_remaining -= 1
        elif line.startswith("-"):
            old_remaining -= 1
        else:
            old_remaining -= 1
            new_remaining -= 1
        in_hunk = old_remaining > 0 or new_remaining > 0
    return starts


class UnifiedDiffHighlighter(QSyntaxHighlighter):
    def __init__(self, document) -> None:
        super().__init__(document)
        self.addition_format = self._format("#b8f5c9", "#0b2818")
        self.deletion_format = self._format("#ffb3ad", "#2a1010")
        self.hunk_format = self._format("#9ed8ff", "#102532", bold=True)
        self.header_format = self._format("#88aa95", "#0b1711", bold=True)
        self.warning_format = self._format("#ffd28a", "#2c2110", bold=True)

    def highlightBlock(self, text: str) -> None:  # type: ignore[override]
        if text.startswith(("--- ", "+++ ")):
            self.setFormat(0, len(text), self.header_format)
        elif text.startswith("@@"):
            self.setFormat(0, len(text), self.hunk_format)
        elif text.startswith("+"):
            self.setFormat(0, len(text), self.addition_format)
        elif text.startswith("-"):
            self.setFormat(0, len(text), self.deletion_format)
        elif "diff preview truncated" in text:
            self.setFormat(0, len(text), self.warning_format)

    @staticmethod
    def _format(foreground: str, background: str, *, bold: bool = False) -> QTextCharFormat:
        value = QTextCharFormat()
        value.setForeground(QColor(foreground))
        value.setBackground(QColor(background))
        if bold:
            value.setFontWeight(QFont.Weight.Bold)
        return value


class DiffReviewWidget(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("diffReviewPanel")
        self.setMaximumHeight(300)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.file_label = QLabel("Changes")
        self.file_label.setObjectName("diffFileLabel")
        toolbar.addWidget(self.file_label)
        self.file_selector = NoWheelComboBox()
        self.file_selector.setObjectName("diffFileSelector")
        self.file_selector.setAccessibleName("Select a changed file")
        self.file_selector.setVisible(False)
        toolbar.addWidget(self.file_selector)
        self.summary_label = QLabel("No changes")
        self.summary_label.setObjectName("diffSummary")
        toolbar.addWidget(self.summary_label)
        toolbar.addStretch(1)
        self.copy_button = QPushButton("Copy Diff")
        self.copy_button.setObjectName("diffCopyButton")
        self.copy_button.setToolTip("Copy the currently displayed unified diff")
        self.copy_button.setEnabled(False)
        toolbar.addWidget(self.copy_button)
        layout.addLayout(toolbar)

        self.content_stack = QStackedWidget()
        self.content_stack.setObjectName("diffContentStack")
        self.empty_label = QLabel("No reviewable diff was produced.")
        self.empty_label.setObjectName("diffEmptyState")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.content_stack.addWidget(self.empty_label)

        self.diff_view = QPlainTextEdit()
        self.diff_view.setObjectName("agentDiffPreview")
        self.diff_view.setReadOnly(True)
        self.diff_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.diff_view.setMinimumHeight(140)
        self.diff_view.setMaximumHeight(235)
        self.diff_view.setAccessibleName("Proposed unified diff")
        self.content_stack.addWidget(self.diff_view)
        layout.addWidget(self.content_stack)

        self.highlighter = UnifiedDiffHighlighter(self.diff_view.document())
        self.sections: list[DiffSection] = []
        self.full_diff = ""
        self.current_diff = ""
        self.file_selector.currentIndexChanged.connect(self._render_selection)
        self.copy_button.clicked.connect(self.copy_current_diff)
        self._copy_reset_timer = QTimer(self)
        self._copy_reset_timer.setSingleShot(True)
        self._copy_reset_timer.setInterval(1200)
        self._copy_reset_timer.timeout.connect(lambda: self.copy_button.setText("Copy Diff"))

    def set_diff(self, target: str, diff: str) -> None:
        self.full_diff = diff
        self.sections = parse_unified_diff(diff, target)
        self.file_selector.blockSignals(True)
        self.file_selector.clear()
        if len(self.sections) > 1:
            additions, deletions, _hunks = diff_stats(diff)
            self.file_selector.addItem(
                f"All Changes ({len(self.sections)} files, +{additions} -{deletions})",
                -1,
            )
            self.file_selector.setItemData(0, "All changed files", Qt.ItemDataRole.ToolTipRole)
            for index, section in enumerate(self.sections):
                self.file_selector.addItem(
                    f"{section.path} (+{section.additions} -{section.deletions})",
                    index,
                )
                self.file_selector.setItemData(
                    self.file_selector.count() - 1,
                    section.path,
                    Qt.ItemDataRole.ToolTipRole,
                )
            self.file_selector.setCurrentIndex(0)
            self.file_selector.setVisible(True)
            self.file_label.setVisible(False)
        else:
            self.file_selector.setVisible(False)
            self.file_label.setVisible(True)
            self.file_label.setText(self.sections[0].path if self.sections else target or "Changes")
            self.file_label.setToolTip(self.file_label.text())
        self.file_selector.blockSignals(False)
        self._render_selection()

    def clear(self) -> None:
        self.sections.clear()
        self.full_diff = ""
        self.current_diff = ""
        self.file_selector.clear()
        self.file_selector.setVisible(False)
        self.file_label.setVisible(True)
        self.file_label.setText("Changes")
        self.summary_label.setText("No changes")
        self.diff_view.clear()
        self.copy_button.setEnabled(False)
        self.copy_button.setText("Copy Diff")
        self.content_stack.setCurrentWidget(self.empty_label)

    def copy_current_diff(self) -> None:
        if not self.current_diff:
            return
        QApplication.clipboard().setText(self.current_diff)
        self.copy_button.setText("Copied")
        self._copy_reset_timer.start()

    def _render_selection(self, _index: int = 0) -> None:
        if not self.sections:
            self.current_diff = ""
            self.summary_label.setText("No changes")
            self.copy_button.setEnabled(False)
            self.content_stack.setCurrentWidget(self.empty_label)
            return

        selected = self.file_selector.currentData() if len(self.sections) > 1 else 0
        if selected == -1:
            self.current_diff = self.full_diff
            self.file_selector.setToolTip("All changed files")
            additions, deletions, hunks = diff_stats(self.full_diff)
            self.summary_label.setText(self._summary(len(self.sections), additions, deletions, hunks))
        else:
            section = self.sections[int(selected or 0)]
            self.current_diff = section.text
            self.file_selector.setToolTip(section.path)
            self.summary_label.setText(self._summary(1, section.additions, section.deletions, section.hunks))
        self.diff_view.setPlainText(self.current_diff)
        cursor = self.diff_view.textCursor()
        cursor.movePosition(cursor.MoveOperation.Start)
        self.diff_view.setTextCursor(cursor)
        self.copy_button.setEnabled(True)
        self.content_stack.setCurrentWidget(self.diff_view)

    @staticmethod
    def _summary(files: int, additions: int, deletions: int, hunks: int) -> str:
        file_text = f"{files} file" + ("s" if files != 1 else "")
        hunk_text = f"{hunks} hunk" + ("s" if hunks != 1 else "")
        return f"{file_text}  ·  +{additions}  -{deletions}  ·  {hunk_text}"
