from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from pathlib import Path
import re
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

from local_matrix_assistant.services.desktop_actions import (
    DesktopAction,
    DesktopActionError,
    DesktopActionResult,
    DesktopActionService,
)


_XML_HEADER = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
_WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_REL_NAMESPACE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_REQUIRED_PARTS = {
    "[Content_Types].xml",
    "_rels/.rels",
    "docProps/app.xml",
    "docProps/core.xml",
    "word/document.xml",
    "word/footer.xml",
    "word/numbering.xml",
    "word/settings.xml",
    "word/styles.xml",
    "word/_rels/document.xml.rels",
}


@dataclass(frozen=True, slots=True)
class _DocumentBlock:
    kind: str
    text: str


class WordDocumentService:
    """Create valid DOCX packages using only the Python standard library."""

    def __init__(self, desktop_actions: DesktopActionService) -> None:
        self.desktop_actions = desktop_actions

    def create(self, action: DesktopAction, body_markdown: str = "") -> DesktopActionResult:
        if action.kind != "create_word_document":
            raise DesktopActionError(f"Unsupported Word document action: {action.kind}")

        requested = Path(action.target)
        if requested.suffix.lower() != ".docx":
            requested = requested.with_suffix(".docx")
        destination = self.desktop_actions.resolve_output_path(str(requested), default_suffix=".docx")
        if action.auto_unique:
            destination = self._available_destination(destination)

        title = action.title.strip() or self._title_from_destination(destination)
        blocks = self._parse_markdown(body_markdown, title)
        package = self._package_parts(title, blocks)
        self._write_package(destination, package)
        try:
            self._validate_package(destination)
        except Exception:
            self._remove_partial(destination)
            raise
        return DesktopActionResult(
            kind=action.kind,
            message=f"Created Word document: {destination}",
            target=str(destination),
        )

    @staticmethod
    def fallback_outline(instruction: str, title: str) -> str:
        request = re.sub(r"\s+", " ", instruction).strip(" .")
        subject = title.strip() or "the requested topic"
        return (
            "## Overview\n"
            f"This document provides an editable outline for {subject}. "
            f"Requested scope: {request or subject}.\n\n"
            "## Core Topics\n"
            "- Definition and scope\n"
            "- Main categories and representative examples\n"
            "- Capabilities, limitations, and practical uses\n"
            "- Evaluation criteria and implementation considerations\n\n"
            "## Next Steps\n"
            "1. Confirm the intended audience and level of detail.\n"
            "2. Add verified examples and supporting sources.\n"
            "3. Review the outline and expand the most relevant sections."
        )

    @staticmethod
    def _available_destination(destination: Path) -> Path:
        if not destination.exists():
            return destination
        for index in range(2, 10_000):
            candidate = destination.with_name(f"{destination.stem}-{index}{destination.suffix}")
            if not candidate.exists():
                return candidate
        raise DesktopActionError("Could not choose an unused Word document name.")

    @staticmethod
    def _title_from_destination(destination: Path) -> str:
        title = re.sub(r"[_-]+", " ", destination.stem).strip()
        return title.title() or "Document"

    @classmethod
    def _parse_markdown(cls, markdown: str, title: str) -> list[_DocumentBlock]:
        cleaned = re.sub(r"^\s*```(?:markdown|md)?\s*", "", markdown.strip(), flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```\s*$", "", cleaned)
        blocks: list[_DocumentBlock] = []
        paragraph_lines: list[str] = []

        def flush_paragraph() -> None:
            if paragraph_lines:
                text = " ".join(line.strip() for line in paragraph_lines if line.strip())
                if text:
                    blocks.append(_DocumentBlock("paragraph", cls._clean_inline_markdown(text)))
                paragraph_lines.clear()

        for raw_line in cleaned.splitlines():
            line = raw_line.strip()
            if not line:
                flush_paragraph()
                continue
            heading = re.match(r"^(#{1,3})\s+(.+)$", line)
            bullet = re.match(r"^[-*+]\s+(.+)$", line)
            numbered = re.match(r"^\d+[.)]\s+(.+)$", line)
            if heading:
                flush_paragraph()
                heading_text = cls._clean_inline_markdown(heading.group(2))
                if not blocks and cls._normalized_title(heading_text) == cls._normalized_title(title):
                    continue
                level = min(len(heading.group(1)), 3)
                blocks.append(_DocumentBlock(f"heading{level}", heading_text))
            elif bullet:
                flush_paragraph()
                blocks.append(_DocumentBlock("bullet", cls._clean_inline_markdown(bullet.group(1))))
            elif numbered:
                flush_paragraph()
                blocks.append(_DocumentBlock("number", cls._clean_inline_markdown(numbered.group(1))))
            else:
                paragraph_lines.append(line)
        flush_paragraph()
        return blocks

    @staticmethod
    def _clean_inline_markdown(text: str) -> str:
        text = re.sub(r"\[([^]]+)]\(([^)]+)\)", r"\1 (\2)", text)
        text = re.sub(r"(?<!\\)(?:\*\*|__)(.+?)(?:\*\*|__)", r"\1", text)
        text = re.sub(r"(?<!\\)(?:\*|_)(.+?)(?:\*|_)", r"\1", text)
        text = text.replace("`", "").strip()
        return re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", text)

    @staticmethod
    def _normalized_title(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", text.lower())

    def _write_package(self, destination: Path, parts: dict[str, str]) -> None:
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as output:
                with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
                    for name, content in parts.items():
                        archive.writestr(name, content.encode("utf-8"))
        except FileExistsError as exc:
            raise DesktopActionError(f"The file already exists and was not overwritten: {destination}") from exc
        except OSError as exc:
            self._remove_partial(destination)
            raise DesktopActionError(f"Could not create the Word document: {exc}") from exc
        except Exception:
            self._remove_partial(destination)
            raise

    @staticmethod
    def _remove_partial(destination: Path) -> None:
        try:
            if destination.exists():
                destination.unlink()
        except OSError:
            pass

    @staticmethod
    def _validate_package(destination: Path) -> None:
        try:
            with ZipFile(destination) as archive:
                names = set(archive.namelist())
                missing = _REQUIRED_PARTS - names
                if missing:
                    raise DesktopActionError(f"The Word document is incomplete: {', '.join(sorted(missing))}")
                if archive.testzip() is not None:
                    raise DesktopActionError("The Word document package is corrupt.")
                for part in ("word/document.xml", "word/styles.xml", "word/numbering.xml"):
                    ElementTree.fromstring(archive.read(part))
        except ElementTree.ParseError as exc:
            raise DesktopActionError(f"The Word document XML is invalid: {exc}") from exc
        except (BadZipFile, OSError) as exc:
            raise DesktopActionError(f"Could not verify the Word document: {exc}") from exc

    @classmethod
    def _package_parts(cls, title: str, blocks: list[_DocumentBlock]) -> dict[str, str]:
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        return {
            "[Content_Types].xml": cls._content_types_xml(),
            "_rels/.rels": cls._root_relationships_xml(),
            "docProps/app.xml": cls._app_properties_xml(),
            "docProps/core.xml": cls._core_properties_xml(title, now),
            "word/document.xml": cls._document_xml(title, blocks),
            "word/footer.xml": cls._footer_xml(),
            "word/fontTable.xml": cls._font_table_xml(),
            "word/numbering.xml": cls._numbering_xml(),
            "word/settings.xml": cls._settings_xml(),
            "word/styles.xml": cls._styles_xml(),
            "word/_rels/document.xml.rels": cls._document_relationships_xml(),
        }

    @staticmethod
    def _content_types_xml() -> str:
        return f"""{_XML_HEADER}
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
  <Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>
  <Override PartName="/word/fontTable.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.fontTable+xml"/>
  <Override PartName="/word/footer.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>"""

    @staticmethod
    def _root_relationships_xml() -> str:
        return f"""{_XML_HEADER}
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>"""

    @staticmethod
    def _document_relationships_xml() -> str:
        return f"""{_XML_HEADER}
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>
  <Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/fontTable" Target="fontTable.xml"/>
  <Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer.xml"/>
</Relationships>"""

    @staticmethod
    def _app_properties_xml() -> str:
        return f"""{_XML_HEADER}
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Jarvis</Application><AppVersion>1.0</AppVersion>
</Properties>"""

    @staticmethod
    def _core_properties_xml(title: str, timestamp: str) -> str:
        return f"""{_XML_HEADER}
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>{escape(title)}</dc:title><dc:creator>Jarvis</dc:creator><cp:lastModifiedBy>Jarvis</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{timestamp}</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">{timestamp}</dcterms:modified>
</cp:coreProperties>"""

    @classmethod
    def _document_xml(cls, title: str, blocks: list[_DocumentBlock]) -> str:
        paragraphs = [cls._paragraph_xml(title, "Title")]
        for block in blocks:
            if block.kind.startswith("heading"):
                paragraphs.append(cls._paragraph_xml(block.text, f"Heading{block.kind[-1]}"))
            elif block.kind == "bullet":
                paragraphs.append(cls._paragraph_xml(block.text, "ListParagraph", num_id=1))
            elif block.kind == "number":
                paragraphs.append(cls._paragraph_xml(block.text, "ListParagraph", num_id=2))
            else:
                paragraphs.append(cls._paragraph_xml(block.text, "Normal"))
        body = "\n".join(paragraphs)
        return f"""{_XML_HEADER}
<w:document xmlns:w="{_WORD_NAMESPACE}" xmlns:r="{_REL_NAMESPACE}">
  <w:body>
{body}
    <w:sectPr>
      <w:footerReference w:type="default" r:id="rId5"/>
      <w:pgSz w:w="12240" w:h="15840"/>
      <w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="708" w:footer="708" w:gutter="0"/>
      <w:cols w:space="720"/><w:docGrid w:linePitch="360"/>
    </w:sectPr>
  </w:body>
</w:document>"""

    @staticmethod
    def _paragraph_xml(text: str, style: str, *, num_id: int | None = None) -> str:
        numbering = ""
        if num_id is not None:
            numbering = f'<w:numPr><w:ilvl w:val="0"/><w:numId w:val="{num_id}"/></w:numPr>'
        safe_text = escape(text)
        return (
            "    <w:p><w:pPr>"
            f'<w:pStyle w:val="{style}"/>{numbering}'
            f'</w:pPr><w:r><w:t xml:space="preserve">{safe_text}</w:t></w:r></w:p>'
        )

    @staticmethod
    def _styles_xml() -> str:
        return f"""{_XML_HEADER}
<w:styles xmlns:w="{_WORD_NAMESPACE}">
  <w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="22"/><w:szCs w:val="22"/><w:lang w:val="en-US"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="300" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:before="0" w:after="120" w:line="300" w:lineRule="auto"/></w:pPr><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="22"/><w:szCs w:val="22"/><w:color w:val="000000"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="0" w:after="240" w:line="360" w:lineRule="auto"/></w:pPr><w:rPr><w:rFonts w:ascii="Calibri Light" w:hAnsi="Calibri Light"/><w:b/><w:color w:val="0B2545"/><w:sz w:val="52"/><w:szCs w:val="52"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:keepLines/><w:spacing w:before="360" w:after="200" w:line="300" w:lineRule="auto"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:color w:val="2E74B5"/><w:sz w:val="32"/><w:szCs w:val="32"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:keepLines/><w:spacing w:before="280" w:after="140" w:line="300" w:lineRule="auto"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:color w:val="2E74B5"/><w:sz w:val="26"/><w:szCs w:val="26"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:keepLines/><w:spacing w:before="200" w:after="100" w:line="300" w:lineRule="auto"/><w:outlineLvl w:val="2"/></w:pPr><w:rPr><w:b/><w:color w:val="1F4D78"/><w:sz w:val="24"/><w:szCs w:val="24"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/><w:uiPriority w:val="34"/><w:qFormat/><w:pPr><w:contextualSpacing/></w:pPr></w:style>
</w:styles>"""

    @staticmethod
    def _numbering_xml() -> str:
        return f"""{_XML_HEADER}
<w:numbering xmlns:w="{_WORD_NAMESPACE}">
  <w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="singleLevel"/><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="&#x2022;"/><w:lvlJc w:val="left"/><w:pPr><w:tabs><w:tab w:val="num" w:pos="270"/></w:tabs><w:spacing w:after="80" w:line="300" w:lineRule="auto"/><w:ind w:left="540" w:hanging="270"/></w:pPr><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="22"/></w:rPr></w:lvl></w:abstractNum>
  <w:abstractNum w:abstractNumId="1"><w:multiLevelType w:val="singleLevel"/><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/><w:lvlJc w:val="left"/><w:pPr><w:tabs><w:tab w:val="num" w:pos="270"/></w:tabs><w:spacing w:after="80" w:line="300" w:lineRule="auto"/><w:ind w:left="540" w:hanging="270"/></w:pPr></w:lvl></w:abstractNum>
  <w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num><w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>
</w:numbering>"""

    @staticmethod
    def _settings_xml() -> str:
        return f"""{_XML_HEADER}
<w:settings xmlns:w="{_WORD_NAMESPACE}"><w:zoom w:percent="100"/><w:defaultTabStop w:val="720"/><w:updateFields w:val="true"/><w:compat/></w:settings>"""

    @staticmethod
    def _font_table_xml() -> str:
        return f"""{_XML_HEADER}
<w:fonts xmlns:w="{_WORD_NAMESPACE}"><w:font w:name="Calibri"/><w:font w:name="Calibri Light"/><w:font w:name="Symbol"/></w:fonts>"""

    @staticmethod
    def _footer_xml() -> str:
        return f"""{_XML_HEADER}
<w:ftr xmlns:w="{_WORD_NAMESPACE}"><w:p><w:pPr><w:jc w:val="right"/><w:spacing w:before="0" w:after="0"/></w:pPr><w:r><w:rPr><w:color w:val="777777"/><w:sz w:val="18"/></w:rPr><w:t xml:space="preserve">Page </w:t></w:r><w:fldSimple w:instr=" PAGE "><w:r><w:rPr><w:color w:val="777777"/><w:sz w:val="18"/></w:rPr><w:t>1</w:t></w:r></w:fldSimple></w:p></w:ftr>"""
