from __future__ import annotations

import hashlib
import html
import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any


SEC_PACKED_TEXT_RENDERER_VERSION = "sec_packed_text_renderer_v8"
STRUCTURED_XML_EXCLUDED_QUALITY_FLAG = "structured_xml_excluded"
DUPLICATE_BLOCK_MIN_CHARS = 200
DUPLICATE_PLACEHOLDER_PREFIX_CHARS = 15

_UINT32_MAX = 4_294_967_295
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_SKIP_TAGS = {"script", "style", "noscript", "svg", "ix:hidden"}
_BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "caption",
    "dd",
    "details",
    "div",
    "dl",
    "dt",
    "figcaption",
    "footer",
    "form",
    "header",
    "hr",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "summary",
    "ul",
}
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_TABLE_TAGS = {"table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption", "colgroup", "col"}
_STRUCTURED_FUND_XML_FORM_PREFIXES = ("NPORT", "N-PORT", "N-CEN", "N-MFP")
_SEPARATOR_ONLY_RE = re.compile(r"^[\s|+\-_=*~`.]{3,}$")
_PAGE_MARKER_RE = re.compile(r"^<\s*page(?:\s+\d+)?\s*>$", re.I)
_NUMERICISH_RE = re.compile(r"^\s*(?:\$|usd)?\s*\(?-?[0-9][0-9,]*(?:\.[0-9]+)?%?\)?\s*$", re.I)
_DATEISH_RE = re.compile(
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2},\s+\d{4}\b|"
    r"\b\d{1,2}/\d{1,2}/\d{2,4}\b|"
    r"\b\d{4}-\d{2}-\d{2}\b",
    re.I,
)
_YEARISH_RE = re.compile(r"^(?:19|20)\d{2}$")
_MOJIBAKE_REPLACEMENTS = (
    ("\u00a0", " "),
    ("\u2007", " "),
    ("\u202f", " "),
    ("\u200b", ""),
    ("\u200c", ""),
    ("\u200d", ""),
    ("\ufeff", ""),
    ("\u2018", "'"),
    ("\u2019", "'"),
    ("\u201c", '"'),
    ("\u201d", '"'),
    ("\u2013", "-"),
    ("\u2014", "-"),
    ("\u2022", "*"),
    ("\u00c2", ""),
    ("\u00e2\u20ac\u2039", ""),
    ("\u00e2\u20ac\u2122", "'"),
    ("\u00e2\u20ac\u02dc", "'"),
    ("\u00e2\u20ac\u0153", '"'),
    ("\u00e2\u20ac\u009d", '"'),
    ("\u00e2\u20ac\u201c", "-"),
    ("\u00e2\u20ac\u201d", "-"),
    ("\u00e2\u20ac\u00a2", "*"),
    ("\u00e2\u02dc\u0090", "\u2610"),
    ("\u00e2\u02dc\u2018", "\u2611"),
    ("\u00e2\u02dc\u2019", "\u2612"),
    ("\u00e2\u20ac\u00a6", "..."),
    ("\u00e2\u201e\u00a2", "TM"),
    ("\u00e2\u20ac\u2030", " "),
    ("\u00ef\u00ac\u0081", "fi"),
    ("\u00ef\u00ac\u0082", "fl"),
    ("\ufb01", "fi"),
    ("\ufb02", "fl"),
    ("\u00c2\u00ae", "\u00ae"),
    ("\u00c2\u00a9", "\u00a9"),
    ("\u00c3\u00a1", "\u00e1"),
    ("\u00c3\u00a9", "\u00e9"),
    ("\u00c3\u00ad", "\u00ed"),
    ("\u00c3\u00b3", "\u00f3"),
    ("\u00c3\u00ba", "\u00fa"),
    ("\u00c3\u00b1", "\u00f1"),
    ("\u00c3\u00bc", "\u00fc"),
    ("\u00c3\u00b6", "\u00f6"),
    ("\u00c3\u00a4", "\u00e4"),
)


@dataclass(frozen=True, slots=True)
class RenderedBlock:
    kind: str
    text: str


@dataclass(frozen=True, slots=True)
class _TableCell:
    text: str
    colspan: int = 1
    rowspan: int = 1


@dataclass(frozen=True, slots=True)
class _ImageReference:
    src: str
    alt: str
    title: str
    width: str
    height: str


@dataclass(frozen=True, slots=True)
class PackedTextResult:
    packed_text: str
    intermediate_text: str
    renderer_version: str
    content_format: str
    block_count: int
    table_block_count: int
    duplicate_block_count: int
    block_hashes: list[int]
    duplicate_block_samples: list[str]
    source_text_hash: int
    packed_text_hash: int
    removed_layout_line_count: int
    quality_flags: list[str]


class _TableState:
    def __init__(self) -> None:
        self.caption_parts: list[str] = []
        self.rows: list[list[_TableCell]] = []
        self.current_row: list[_TableCell] | None = None
        self.current_cell_parts: list[str] | None = None
        self.current_cell_colspan = 1
        self.current_cell_rowspan = 1
        self.in_caption = False


class _SecHTMLPackedTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[RenderedBlock] = []
        self.buffer: list[str] = []
        self.buffer_kind = "text"
        self.skip_depth = 0
        self.hidden_node_count = 0
        self.page_artifact_count = 0
        self.table: _TableState | None = None
        self.table_depth = 0
        self.table_count = 0
        self.in_head = False
        self.in_title = False
        self.document_title_parts: list[str] = []
        self.image_references: list[_ImageReference] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr_map = {name.lower(): value or "" for name, value in attrs}
        if tag == "head":
            self.in_head = True
            return
        if tag == "body":
            # SEC HTML is often structurally invalid and may open BODY before
            # closing HEAD. BODY is authoritative for visible-content state.
            self.in_head = False
        if tag == "title":
            self.in_title = True
            return
        if self.in_head:
            return
        if tag == "img" and not self.skip_depth and not _is_hidden_tag(tag, attr_map):
            reference = _html_image_reference(attr_map)
            if reference is not None:
                self.image_references.append(reference)
        if self.skip_depth:
            if tag not in _VOID_TAGS:
                self.skip_depth += 1
            return
        if tag in _SKIP_TAGS or _is_hidden_tag(tag, attr_map):
            self.hidden_node_count += 1
            if tag not in _VOID_TAGS:
                self.skip_depth = 1
            return
        if self.table is not None:
            self._handle_table_start(tag, attr_map)
            return
        if tag == "table":
            self._flush_buffer()
            self.table = _TableState()
            self.table_depth = 1
            return
        if tag == "hr" and "page-break" in attr_map.get("style", "").lower():
            self._flush_buffer()
            if self.blocks and re.fullmatch(r"\d{1,4}", self.blocks[-1].text.strip()):
                self.blocks.pop()
                self.page_artifact_count += 1
            return
        if tag in _HEADING_TAGS:
            self._flush_buffer()
            self.buffer_kind = "heading"
            return
        if tag == "li":
            self._flush_buffer()
            self.buffer_kind = "list_item"
            self.buffer.append("- ")
            return
        if tag == "br":
            self.buffer.append("\n")
            return
        if tag in _BLOCK_TAGS:
            self._flush_buffer()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "head":
            self.in_head = False
            return
        if tag == "title":
            self.in_title = False
            return
        if self.in_head:
            return
        if self.skip_depth:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.table is not None:
            self._handle_table_end(tag)
            return
        if tag in _HEADING_TAGS:
            self._flush_buffer("heading")
            return
        if tag == "li":
            self._flush_buffer("list_item")
            return
        if tag in _BLOCK_TAGS:
            self._flush_buffer()

    def handle_data(self, data: str) -> None:
        if self.in_title and data:
            self.document_title_parts.append(data)
            return
        if self.in_head or self.skip_depth or not data:
            return
        if self.table is not None:
            if self.table.current_cell_parts is not None:
                self.table.current_cell_parts.append(data)
            elif self.table.in_caption:
                self.table.caption_parts.append(data)
            return
        self.buffer.append(data)

    def _handle_table_start(self, tag: str, attrs: dict[str, str]) -> None:
        assert self.table is not None
        if tag == "table":
            self.table_depth += 1
            if self.table.current_cell_parts is not None:
                self.table.current_cell_parts.append(" ")
            return
        if tag == "caption":
            self.table.in_caption = True
            return
        if tag == "tr":
            self._finish_row()
            self.table.current_row = []
            return
        if tag in {"td", "th"}:
            self._finish_cell()
            self.table.current_cell_parts = []
            self.table.current_cell_colspan = _positive_span(attrs.get("colspan"))
            self.table.current_cell_rowspan = _positive_span(attrs.get("rowspan"))
            return
        if tag == "br":
            if self.table.current_cell_parts is not None:
                self.table.current_cell_parts.append(" ")
            elif self.table.in_caption:
                self.table.caption_parts.append(" ")

    def _handle_table_end(self, tag: str) -> None:
        assert self.table is not None
        if tag in {"td", "th"}:
            self._finish_cell()
            return
        if tag == "tr":
            self._finish_row()
            return
        if tag == "caption":
            self.table.in_caption = False
            return
        if tag == "table":
            self.table_depth = max(0, self.table_depth - 1)
            if self.table_depth == 0:
                self._finish_cell()
                self._finish_row()
                self._emit_table_blocks(self.table)
                self.table = None
            return
        if tag in _TABLE_TAGS and self.table.current_cell_parts is not None:
            self.table.current_cell_parts.append(" ")

    def _finish_cell(self) -> None:
        if self.table is None or self.table.current_cell_parts is None:
            return
        cell = _clean_inline(" ".join(self.table.current_cell_parts))
        if self.table.current_row is None:
            self.table.current_row = []
        self.table.current_row.append(
            _TableCell(cell, self.table.current_cell_colspan, self.table.current_cell_rowspan)
        )
        self.table.current_cell_parts = None
        self.table.current_cell_colspan = 1
        self.table.current_cell_rowspan = 1

    def _finish_row(self) -> None:
        if self.table is None or self.table.current_row is None:
            return
        self._finish_cell()
        row = [
            _TableCell(_clean_inline(cell.text), cell.colspan, cell.rowspan)
            for cell in self.table.current_row
        ]
        if any(cell.text for cell in row):
            self.table.rows.append(row)
        self.table.current_row = None

    def _emit_table_blocks(self, table: _TableState) -> None:
        blocks = _render_table_blocks(table.rows, _clean_inline(" ".join(table.caption_parts)))
        if blocks:
            self.table_count += 1
            self.blocks.extend(blocks)

    def _flush_buffer(self, kind: str | None = None) -> None:
        text = _clean_block_text("".join(self.buffer))
        block_kind = kind or self.buffer_kind or "text"
        self.buffer = []
        self.buffer_kind = "text"
        if text and not _is_low_signal_text(text):
            self.blocks.append(RenderedBlock(block_kind, text))


def render_sec_packed_text(
    source_text: str | None,
    content_format: str | None,
    *,
    document_name: str = "",
    document_type: str = "",
    form_type: str = "",
    text_kind: str = "",
    include_intermediate: bool = True,
) -> PackedTextResult:
    source = "" if source_text is None else str(source_text)
    fmt = str(content_format or "").strip().lower() or "plain_text"
    flags = [f"format_{fmt}"]
    if "\ufffd" in source:
        flags.append("replacement_char")
    if _has_mojibake(source):
        flags.append("mojibake_suspect")
    if any(ord(ch) > 127 for ch in source[:5000]):
        flags.append("non_ascii")
    blocks: list[RenderedBlock]
    parser_hidden_nodes = 0
    parser_table_count = 0
    parser_page_artifacts = 0

    if fmt == "xml" and _is_structured_fund_xml(form_type=form_type, document_type=document_type, document_name=document_name):
        flags.extend(
            [
                STRUCTURED_XML_EXCLUDED_QUALITY_FLAG,
                f"structured_xml_form_{_flag_token(form_type or document_type or document_name)}",
            ]
        )
        blocks = []
    elif fmt == "html":
        parser = _SecHTMLPackedTextParser()
        try:
            parser.feed(source)
            parser.close()
            parser._flush_buffer()
            blocks = parser.blocks
            parser_hidden_nodes = parser.hidden_node_count
            parser_table_count = parser.table_count
            parser_page_artifacts = parser.page_artifact_count
            if not blocks and parser.image_references:
                blocks = _image_only_html_blocks(
                    _clean_inline(" ".join(parser.document_title_parts)),
                    parser.image_references,
                )
                flags.extend(
                    [
                        "html_image_only_document",
                        "html_image_references_preserved",
                        "image_content_not_ocr_extracted",
                    ]
                )
            elif not blocks and parser.document_title_parts:
                blocks = [
                    RenderedBlock("heading", "HTML document with title only"),
                    RenderedBlock(
                        "document_title",
                        f"Document title: {_clean_inline(' '.join(parser.document_title_parts))}",
                    ),
                ]
                flags.append("html_title_only_document")
        except Exception:  # noqa: BLE001
            flags.append("html_parser_fallback")
            blocks = _plain_text_blocks(_strip_markup(source))
    elif fmt == "xml":
        blocks = _xml_blocks(source)
        if any(block.kind == "xml_record" for block in blocks):
            flags.append("xml_repeated_records_packed")
        if any(block.kind == "xml_comment" for block in blocks):
            flags.append("xml_comments_preserved")
    else:
        blocks = _plain_text_blocks(source)

    blocks = _dedupe_empty_and_separator_blocks(blocks)
    blocks, duplicate_block_count, duplicate_block_samples = _replace_duplicate_blocks(blocks)
    if document_name:
        flags.append(f"document_name_present")
    if document_type:
        flags.append(f"document_type_{_flag_token(document_type)}")
    if form_type:
        flags.append(f"form_type_{_flag_token(form_type)}")
    if text_kind:
        flags.append(f"text_kind_{_flag_token(text_kind)}")
    if parser_hidden_nodes:
        flags.append("hidden_markup_removed")
    if parser_table_count:
        flags.append("html_tables_rendered")
    if parser_page_artifacts:
        flags.append("html_page_numbers_removed")
    if not blocks and source.strip():
        flags.append("empty_rendered_text")

    packed_text = "\n".join(block.text for block in blocks).strip()
    intermediate_text = (
        "\n".join(f"[{block.kind}] {block.text}" for block in blocks).strip()
        if include_intermediate
        else ""
    )
    block_keys = [_block_hash_key(block.text) for block in blocks if block.text.strip()]
    block_hashes = [stable_uint64(key) for key in block_keys]
    table_block_count = sum(1 for block in blocks if block.kind.startswith("table"))
    return PackedTextResult(
        packed_text=packed_text,
        intermediate_text=intermediate_text,
        renderer_version=SEC_PACKED_TEXT_RENDERER_VERSION,
        content_format=fmt,
        block_count=len(blocks),
        table_block_count=table_block_count,
        duplicate_block_count=duplicate_block_count,
        block_hashes=block_hashes,
        duplicate_block_samples=duplicate_block_samples,
        source_text_hash=stable_uint64(source),
        packed_text_hash=stable_uint64(packed_text),
        removed_layout_line_count=_removed_layout_line_count(source, packed_text),
        quality_flags=sorted(set(flags)),
    )


def build_sec_text_context_row(row: dict[str, Any], *, updated_at: str | None = None) -> dict[str, Any]:
    source_text = str(row.get("source_text", "") or "")
    result = render_sec_packed_text(
        source_text,
        str(row.get("content_format", "") or ""),
        document_name=str(row.get("document_name", "") or ""),
        document_type=str(row.get("document_type", "") or ""),
        form_type=str(row.get("form_type", "") or ""),
        text_kind=str(row.get("text_kind", "") or ""),
        include_intermediate=False,
    )
    flags = _merge_quality_flags(row.get("quality_flags"), result.quality_flags)
    sequence = int(row.get("sequence_number", 0) or 0)
    text_rank = int(row.get("text_rank", 0) or 0)
    if text_rank == 0 and sequence > 0:
        text_rank = min(sequence, 255)
    payload = {
        "ticker": str(row.get("ticker", "") or "").upper(),
        "timestamp_us": int(row.get("timestamp_us", 0) or 0),
        "accepted_at_utc": str(row.get("accepted_at_utc", "") or ""),
        "cik": str(row.get("cik", "") or ""),
        "accession_number": str(row.get("accession_number", "") or ""),
        "form_type": str(row.get("form_type", "") or ""),
        "text_rank": text_rank,
        "document_id": str(row.get("document_id", "") or ""),
        "text_kind": str(row.get("text_kind", "") or ""),
        "text": result.packed_text,
        "text_char_count": _uint32_len(result.packed_text),
        "source_text_char_count": min(int(row.get("source_text_char_count", 0) or len(source_text)), _UINT32_MAX),
        "source_text_hash": int(row.get("source_text_hash", 0) or result.source_text_hash),
        "model_text_hash": result.packed_text_hash,
        "model_normalizer_version": result.renderer_version,
        "removed_layout_line_count": result.removed_layout_line_count,
        "renderer_block_count": result.block_count,
        "renderer_table_block_count": result.table_block_count,
        "renderer_duplicate_block_count": result.duplicate_block_count,
        "renderer_block_hashes": [int(value) for value in result.block_hashes],
        "quality_flags": ",".join(flags),
    }
    if updated_at is not None:
        payload["updated_at"] = updated_at
    return payload


def stable_uint64(value: Any) -> int:
    data = str(value or "").encode("utf-8", errors="ignore")
    if not data:
        return 0
    digest = hashlib.blake2b(data, digest_size=8).digest()
    return int.from_bytes(digest, "little", signed=False)


def _render_table_blocks(rows: list[list[_TableCell]], caption: str) -> list[RenderedBlock]:
    expanded_rows = _expand_table_grid(rows)
    cleaned_rows = [[_clean_inline(cell) for cell in row] for row in expanded_rows]
    cleaned_rows = [row for row in cleaned_rows if any(row)]
    if not cleaned_rows:
        return []
    blocks: list[RenderedBlock] = []
    if caption and not _is_low_signal_text(caption):
        blocks.append(RenderedBlock("table_caption", f"Table: {caption}"))

    header_index = _find_header_row(cleaned_rows)
    columns: list[str] = []
    data_rows = cleaned_rows
    if header_index >= 0:
        header = cleaned_rows[header_index]
        columns = _header_columns(header)
        for row in cleaned_rows[:header_index]:
            text = " | ".join(cell for cell in row if cell)
            if text and not _is_low_signal_text(text):
                blocks.append(RenderedBlock("table_preamble", text))
        data_rows = cleaned_rows[header_index + 1 :]
        if columns:
            blocks.append(RenderedBlock("table_columns", "Columns: " + "; ".join(_unique_in_order(columns))))

    for row in data_rows:
        text = _render_table_row(row, columns)
        if text and not _is_low_signal_text(text):
            blocks.append(RenderedBlock("table_row", text))
    if not blocks:
        for row in cleaned_rows:
            text = " | ".join(cell for cell in row if cell)
            if text:
                blocks.append(RenderedBlock("table_row", text))
    return blocks


def _expand_table_grid(rows: list[list[_TableCell]]) -> list[list[str]]:
    expanded: list[list[str]] = []
    pending: dict[int, tuple[int, str]] = {}
    for source_row in rows:
        active = pending
        pending = {}
        row: list[str] = []
        column = 0

        def fill_active() -> None:
            nonlocal column
            while column in active:
                remaining, value = active.pop(column)
                row.append(value)
                if remaining > 1:
                    pending[column] = (remaining - 1, value)
                column += 1

        for cell in source_row:
            fill_active()
            colspan = max(1, cell.colspan)
            rowspan = max(1, cell.rowspan)
            for offset in range(colspan):
                fill_active()
                value = cell.text if offset == 0 else ""
                row.append(value)
                if rowspan > 1:
                    pending[column] = (rowspan - 1, value)
                column += 1
        while active:
            if column in active:
                fill_active()
            else:
                row.append("")
                column += 1
        expanded.append(row)
    width = max((len(row) for row in expanded), default=0)
    return [row + [""] * (width - len(row)) for row in expanded]


def _find_header_row(rows: list[list[str]]) -> int:
    for index, row in enumerate(rows[:12]):
        if any(sum(1 for cell in prior if cell) >= 2 for prior in rows[:index]):
            continue
        if _looks_like_header_row(row) and _following_rows_match_header(rows, index):
            return index
    return -1


def _header_columns(row: list[str]) -> list[str]:
    cells = list(row)
    if sum(1 for cell in cells if cell) <= 1:
        return []
    columns: list[str] = []
    current = ""
    for cell in cells:
        if cell:
            current = cell
        columns.append(current)
    return columns


def _looks_like_header_row(row: list[str]) -> bool:
    non_empty = [cell for cell in row if cell]
    if len(non_empty) < 2:
        return False
    numeric_count = sum(1 for cell in non_empty if _is_numericish(cell))
    date_count = sum(1 for cell in non_empty if _is_dateish(cell))
    year_count = sum(1 for cell in non_empty if _is_yearish(cell))
    alpha_count = sum(1 for cell in non_empty if re.search(r"[A-Za-z]", cell))
    avg_len = sum(len(cell) for cell in non_empty) / max(1, len(non_empty))
    if date_count >= 2:
        return True
    if year_count >= 2:
        return True
    if len(non_empty) >= 3 and avg_len <= 80 and alpha_count >= 2 and numeric_count <= max(1, len(non_empty) // 3):
        return True
    return len(non_empty) == 2 and avg_len <= 80 and alpha_count == 2 and numeric_count == 0


def _following_rows_match_header(rows: list[list[str]], header_index: int) -> bool:
    columns = _header_columns(rows[header_index])
    if not columns:
        return False
    column_count = len(columns)
    matched = 0
    for row in rows[header_index + 1 : header_index + 16]:
        non_empty = [cell for cell in row if cell]
        if not non_empty:
            continue
        if len(non_empty) == 1:
            continue
        if len(row) == column_count:
            matched += 1
        elif abs(len(row) - column_count) <= 1 and column_count >= 2:
            matched += 1
    return matched > 0


def _render_table_row(row: list[str], columns: list[str]) -> str:
    if not any(row):
        return ""
    cells = [cell for cell in row if cell]
    if columns and len(row) == len(columns):
        grouped: list[tuple[str, list[str]]] = []
        for index, (column, value) in enumerate(zip(columns, row), 1):
            if not value:
                continue
            label = column or f"Column {index}"
            if grouped and grouped[-1][0] == label:
                grouped[-1][1].append(value)
            else:
                grouped.append((label, [value]))
        if grouped:
            return "; ".join(f"{label}={' '.join(values)}" for label, values in grouped)
    if len(cells) == 2 and _looks_like_label_cell(cells[0]):
        label = cells[0].rstrip(":")
        return f"{label}: {cells[1]}"
    if len(cells) == 2 and cells[0] in {"\u2610", "\u2611", "\u2612", "o", "x"}:
        return f"{cells[0]} {cells[1]}"
    return " | ".join(cells)


def _unique_in_order(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and (not result or result[-1] != value):
            result.append(value)
    return result


def _xml_blocks(source: str) -> list[RenderedBlock]:
    text = _prepare_xml_source(source)
    if not text:
        return []
    try:
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        root = ET.fromstring(text, parser=parser)
    except ET.ParseError:
        return _xml_like_blocks_with_tags(source)
    repeated_record_blocks = _xml_repeated_record_blocks(root)
    if repeated_record_blocks:
        return repeated_record_blocks
    blocks: list[RenderedBlock] = []
    _walk_xml(root, [], blocks)
    return blocks or _xml_like_blocks_with_tags(source)


def _xml_repeated_record_blocks(root: ET.Element) -> list[RenderedBlock]:
    children = list(root)
    element_children = [child for child in children if not _is_xml_comment(child)]
    if len(element_children) < 3:
        return []
    child_tags = [_strip_namespace(child.tag) for child in element_children]
    record_tag, record_count = Counter(child_tags).most_common(1)[0]
    if not record_tag or record_count < 3 or record_count / len(element_children) < 0.6:
        return []

    root_tag = _strip_namespace(root.tag)
    blocks: list[RenderedBlock] = []
    if root_tag:
        blocks.append(RenderedBlock("xml_section", f"<{root_tag}>"))
    root_attrs = _xml_attrs(root)
    if root_attrs and root_tag:
        blocks.append(RenderedBlock("xml_attrs", f"<{root_tag}> " + "; ".join(root_attrs)))
    root_text = _clean_block_text(root.text or "")
    if root_text:
        blocks.append(RenderedBlock("xml_text", root_text))

    for child in children:
        tag = _strip_namespace(child.tag)
        if tag != record_tag:
            _walk_xml(child, [root_tag] if root_tag else [], blocks)
            continue
        fields: list[str] = []
        _flatten_xml_record(child, [], fields)
        record_text = f"<{record_tag}>"
        if fields:
            record_text += " " + "; ".join(fields)
        blocks.append(RenderedBlock("xml_record", record_text))
    return blocks


def _flatten_xml_record(node: ET.Element, path: list[str], fields: list[str]) -> None:
    if _is_xml_comment(node):
        comment = _clean_block_text(node.text or "")
        if comment and not _is_low_signal_text(comment):
            fields.append(f"comment={comment}")
        return
    tag = _strip_namespace(node.tag)
    next_path = [*path, tag] if tag else path
    field_name = "/".join(next_path[1:] if len(next_path) > 1 else next_path)
    for name, value in sorted(node.attrib.items()):
        clean_value = _clean_inline(value)
        if clean_value:
            fields.append(f"{field_name}/@{_strip_namespace(name)}={clean_value}")
    text = _clean_block_text(node.text or "")
    if text and field_name:
        fields.append(f"{field_name}={text}")
    for child in list(node):
        _flatten_xml_record(child, next_path, fields)
    tail = _clean_block_text(node.tail or "")
    if tail:
        fields.append(f"text={tail}")


def _prepare_xml_source(source: str) -> str:
    text = (source or "").strip()
    wrapper_match = re.fullmatch(r"(?is)<XML>\s*(.*?)\s*</XML>", text)
    if wrapper_match:
        text = wrapper_match.group(1).strip()
    text = re.sub(r"(?is)<\?xml[^>]*\?>", "", text).strip()
    return text


def _xml_like_blocks_with_tags(source: str) -> list[RenderedBlock]:
    blocks = [
        RenderedBlock("xml_comment", text)
        for raw in re.findall(r"(?is)<!--(.*?)-->", source or "")
        if (text := _clean_block_text(raw)) and not _is_low_signal_text(text)
    ]
    for match in re.finditer(r"(?is)<([A-Za-z_][\w:.-]*)(?:\s[^>]*)?>\s*([^<]+?)\s*</\1>", source or ""):
        tag = _strip_namespace(match.group(1))
        text = _clean_block_text(match.group(2))
        if tag and text and not _is_low_signal_text(text):
            blocks.append(RenderedBlock("xml_leaf", f"{tag}: {text}"))
    return blocks or _plain_text_blocks(_strip_markup(source))


def _is_structured_fund_xml(*, form_type: str, document_type: str, document_name: str) -> bool:
    candidates = [form_type, document_type, document_name]
    for value in candidates:
        token = re.sub(r"[^A-Z0-9]+", "-", str(value or "").upper()).strip("-")
        if any(token.startswith(prefix) for prefix in _STRUCTURED_FUND_XML_FORM_PREFIXES):
            return True
    return False


def _walk_xml(node: ET.Element, path: list[str], blocks: list[RenderedBlock]) -> None:
    if _is_xml_comment(node):
        text = _clean_block_text(node.text or "")
        if text and not _is_low_signal_text(text):
            blocks.append(RenderedBlock("xml_comment", text))
        return
    tag = _strip_namespace(node.tag)
    next_path = [*path, tag] if tag else path
    text = _clean_block_text(node.text or "")
    children = list(node)
    compact_path = _xml_compact_path(next_path)
    if tag and children:
        blocks.append(RenderedBlock("xml_section", f"<{compact_path}>"))
    attrs = _xml_attrs(node)
    if attrs and tag:
        blocks.append(RenderedBlock("xml_attrs", f"<{compact_path}> " + "; ".join(attrs)))
    if text and not children:
        blocks.append(RenderedBlock("xml_leaf", f"<{compact_path}>: {text}"))
    for child in children:
        _walk_xml(child, next_path, blocks)
    tail = _clean_block_text(node.tail or "")
    if tail:
        blocks.append(RenderedBlock("xml_tail", tail))


def _xml_attrs(node: ET.Element) -> list[str]:
    attrs: list[str] = []
    for name, value in sorted(node.attrib.items()):
        clean_value = _clean_inline(value)
        if clean_value:
            attrs.append(f"@{_strip_namespace(name)}={clean_value}")
    return attrs


def _is_xml_comment(node: ET.Element) -> bool:
    return node.tag is ET.Comment


def _xml_compact_path(path: list[str]) -> str:
    clean_path = [part for part in path if part]
    return "/".join(clean_path[-2:])


def _plain_text_blocks(source: str) -> list[RenderedBlock]:
    value = _repair_text(html.unescape(source or ""))
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", value)
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    blocks: list[RenderedBlock] = []
    for paragraph in re.split(r"\n{2,}", value):
        text = _clean_block_text(paragraph)
        if text and not _is_low_signal_text(text):
            blocks.append(RenderedBlock("text", text))
    return blocks


def _strip_markup(source: str) -> str:
    value = re.sub(r"(?is)<!--.*?-->", " ", source or "")
    value = re.sub(r"(?is)<script\b.*?</script>", " ", value)
    value = re.sub(r"(?is)<style\b.*?</style>", " ", value)
    value = re.sub(r"(?is)<ix:hidden\b.*?</ix:hidden>", " ", value)
    value = re.sub(r"(?is)<[^>]+>", " ", value)
    return html.unescape(value)


def _dedupe_empty_and_separator_blocks(blocks: list[RenderedBlock]) -> list[RenderedBlock]:
    result: list[RenderedBlock] = []
    for block in blocks:
        text = _clean_block_text(block.text)
        if text and not _is_low_signal_text(text):
            result.append(RenderedBlock(block.kind, text))
    return result


def _replace_duplicate_blocks(blocks: list[RenderedBlock]) -> tuple[list[RenderedBlock], int, list[str]]:
    result: list[RenderedBlock] = []
    first_text_by_key: dict[str, str] = {}
    duplicate_samples: list[str] = []
    duplicate_count = 0

    for block in blocks:
        key = _block_hash_key(block.text)
        if len(key) < DUPLICATE_BLOCK_MIN_CHARS:
            result.append(block)
            continue
        first_text = first_text_by_key.get(key)
        if first_text is None:
            first_text_by_key[key] = block.text
            result.append(block)
            continue

        duplicate_count += 1
        if len(duplicate_samples) < 5:
            duplicate_samples.append(block.text)
        result.append(RenderedBlock("duplicate", f"DUPLICATE of [{_duplicate_placeholder_prefix(first_text)}]"))

    return result, duplicate_count, duplicate_samples


def _duplicate_placeholder_prefix(text: str) -> str:
    prefix = _clean_inline(text)[:DUPLICATE_PLACEHOLDER_PREFIX_CHARS].strip()
    return prefix or "empty"


def _clean_block_text(value: str) -> str:
    text = _repair_text(html.unescape(value or ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)
    text = re.sub(r"[ \t\f\v]*\n[ \t\f\v]*", "\n", text)
    text = re.sub(r"[ \t\f\v]{2,}", " ", text)
    lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        if _is_layout_only_line(stripped):
            continue
        lines.append(line)
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _clean_inline(value: str) -> str:
    return re.sub(r"\s+", " ", _clean_block_text(value)).strip()


def _html_image_reference(attrs: dict[str, str]) -> _ImageReference | None:
    src = _clean_inline(attrs.get("src", ""))
    if not src or src.lower().startswith("data:"):
        return None
    width = _clean_inline(attrs.get("width", ""))
    height = _clean_inline(attrs.get("height", ""))
    if width in {"0", "1", "2"} and height in {"0", "1", "2"}:
        return None
    return _ImageReference(
        src=src,
        alt=_clean_inline(attrs.get("alt", "")),
        title=_clean_inline(attrs.get("title", "")),
        width=width,
        height=height,
    )


def _image_only_html_blocks(title: str, references: list[_ImageReference]) -> list[RenderedBlock]:
    blocks = [RenderedBlock("heading", "Image-only HTML document")]
    if title:
        blocks.append(RenderedBlock("document_title", f"Document title: {title}"))
    blocks.append(RenderedBlock("image_inventory", f"Image references: {len(references)}"))
    for index, reference in enumerate(references, start=1):
        fields = [f"src={reference.src}"]
        if reference.alt:
            fields.append(f"alt={reference.alt}")
        if reference.title:
            fields.append(f"title={reference.title}")
        if reference.width:
            fields.append(f"width={reference.width}")
        if reference.height:
            fields.append(f"height={reference.height}")
        blocks.append(RenderedBlock("image_reference", f"Image {index}: {'; '.join(fields)}"))
    return blocks


def _repair_text(value: str) -> str:
    text = value or ""
    for source, replacement in _MOJIBAKE_REPLACEMENTS:
        text = text.replace(source, replacement)
    return text


def _has_mojibake(value: str) -> bool:
    sample = (value or "")[:10000]
    return any(token in sample for token in ("\u00c3", "\u00c2", "\u00e2\u20ac", "\u00ef\u00ac"))


def _is_hidden_tag(tag: str, attrs: dict[str, str]) -> bool:
    if "hidden" in attrs:
        return True
    if attrs.get("aria-hidden", "").strip().lower() == "true":
        return True
    if tag == "input" and attrs.get("type", "").strip().lower() == "hidden":
        return True
    style = attrs.get("style", "").replace(" ", "").lower()
    return "display:none" in style or "visibility:hidden" in style


def _is_low_signal_text(text: str) -> bool:
    stripped = text.strip()
    return not stripped or bool(_SEPARATOR_ONLY_RE.fullmatch(stripped))


def _is_layout_only_line(text: str) -> bool:
    return bool(text and (_SEPARATOR_ONLY_RE.fullmatch(text) or _PAGE_MARKER_RE.fullmatch(text)))


def _is_numericish(text: str) -> bool:
    return bool(_NUMERICISH_RE.fullmatch(text.strip()))


def _is_dateish(text: str) -> bool:
    return bool(_DATEISH_RE.search(text.strip()))


def _is_yearish(text: str) -> bool:
    return bool(_YEARISH_RE.fullmatch(text.strip()))


def _looks_like_label_cell(text: str) -> bool:
    value = text.strip()
    return bool(value) and not _is_numericish(value) and len(value) <= 80 and (value.endswith(":") or len(value.split()) <= 6)


def _trim_empty_edge_cells(row: list[str]) -> list[str]:
    start = 0
    end = len(row)
    while start < end and not row[start]:
        start += 1
    while end > start and not row[end - 1]:
        end -= 1
    return row[start:end]


def _positive_span(value: str | None) -> int:
    try:
        return max(1, min(int(str(value or "1")), 1000))
    except ValueError:
        return 1


def _strip_namespace(tag: str) -> str:
    return str(tag or "").split("}", 1)[-1].split(":", 1)[-1]


def _flag_token(value: str) -> str:
    token = re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower())
    return token.strip("_")[:80] or "unknown"


def _block_hash_key(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _removed_layout_line_count(source: str, packed: str) -> int:
    source_lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n") if source else []
    packed_lines = packed.split("\n") if packed else []
    return max(0, len(source_lines) - len(packed_lines))


def _merge_quality_flags(existing: Any, renderer_flags: list[str]) -> list[str]:
    flags: set[str] = set(renderer_flags)
    if isinstance(existing, str):
        flags.update(part.strip() for part in existing.split(",") if part.strip())
    elif isinstance(existing, list):
        flags.update(str(part).strip() for part in existing if str(part).strip())
    return sorted(flags)


def _uint32_len(value: str) -> int:
    return min(len(value or ""), _UINT32_MAX)
