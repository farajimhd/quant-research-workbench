from __future__ import annotations

import re

from research.mlops.clickhouse import sql_string


SEC_MODEL_TEXT_NORMALIZER_VERSION = "sec_model_text_normalizer_v3"
_SEPARATOR_ONLY_LINE_RE = re.compile(r"(?m)^[ \t]*(?:[|+\-_=*~`.][ \t|+\-_=*~`.]*){3,}[ \t]*\n?")
_PIPE_ONLY_LINE_RE = re.compile(r"(?m)^[ \t|]{1,}\n?")
_STANDALONE_ARTIFACT_LINE_RE = re.compile(
    r"(?im)^[ \t|]*(?:"
    r"\[?(?:table text block|text block|abstract|member|axis)\]?"
    r"|(?:document and entity information|dei document information|entity information)"
    r"|(?:xbrl|ixbrl|inline xbrl)"
    r")[ \t|]*(?:\n|$)"
)
_PAGE_LINE_RE = re.compile(r"(?im)^[ \t]*(?:page|p\.)[ \t]*\d{1,4}(?:[ \t]*(?:of|/)[ \t]*\d{1,4})?[ \t]*(?:\n|$)")
_DASHED_PAGE_LINE_RE = re.compile(r"(?m)^[ \t]*[-\u2013\u2014][ \t]*\d{1,4}[ \t]*[-\u2013\u2014][ \t]*(?:\n|$)")
_FORM_PAGE_HEADER_RE = re.compile(r"(?im)^[ \t]*(?:form[ \t]+(?:10-k|10-q|8-k|6-k|20-f|40-f))[ \t]*(?:\n|$)")
_SPACED_WORD_RE = re.compile(r"\b([A-Z])\s([A-Z])\s([A-Z])\s([A-Z])(?:\s([A-Z]))?(?:\s([A-Z]))?(?:\s([A-Z]))?(?:\s([A-Z]))?\b")
_DATE_CELL_PATTERN = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\. [0-9]{1,2}, [0-9]{4}"
_NUMERIC_CELL_PATTERN = r"(?:\$ ?)?(?:\(?-?[0-9][0-9,]*(?:\.[0-9]+)?\)?|--[0-9]{2}-[0-9]{2})"
_DATE_PAIR_BEFORE_ROW_RE = re.compile(rf"(?m)^({_DATE_CELL_PATTERN})[ \t]*\n+({_DATE_CELL_PATTERN})[ \t]*\n+([^|\n]{{2,180}} \|)")
_TWO_VALUE_TABLE_ROW_RE = re.compile(rf"(?m)^([^|\n]{{2,180}}?) \|\n+({_NUMERIC_CELL_PATTERN})[ \t]*\n+({_NUMERIC_CELL_PATTERN})[ \t]*(\n|$)")
_ONE_VALUE_METADATA_ROW_RE = re.compile(r"(?m)^([^|\n]{2,120}:) \|\n+([^|\n]{1,180})")
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
    ("&nbsp;", " "),
    ("&#160;", " "),
    ("&amp;", "&"),
    ("&lt;", "<"),
    ("&gt;", ">"),
    ("&quot;", '"'),
    ("&#39;", "'"),
)


def _compact_fragmented_table_rows(value: str) -> str:
    value = _DATE_PAIR_BEFORE_ROW_RE.sub(r"Columns: \1; \2\n\n\3", value)
    value = _TWO_VALUE_TABLE_ROW_RE.sub(r"\1: \2; \3\4", value)
    value = _ONE_VALUE_METADATA_ROW_RE.sub(r"\1 \2", value)
    return value


def normalize_sec_model_text(text: str | None) -> str:
    """Conservatively normalize SEC text for embedding input."""
    value = "" if text is None else str(text)
    for source, replacement in _MOJIBAKE_REPLACEMENTS:
        value = value.replace(source, replacement)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = _SEPARATOR_ONLY_LINE_RE.sub("\n", value)
    value = _PIPE_ONLY_LINE_RE.sub("\n", value)
    value = _STANDALONE_ARTIFACT_LINE_RE.sub("\n", value)
    value = _PAGE_LINE_RE.sub("\n", value)
    value = _DASHED_PAGE_LINE_RE.sub("\n", value)
    value = _FORM_PAGE_HEADER_RE.sub("\n", value)
    value = _SPACED_WORD_RE.sub(lambda match: "".join(group for group in match.groups() if group), value)
    value = re.sub(r"[ \t]*\n[ \t]*", "\n", value)
    value = re.sub(r"[ \t]{2,}", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = _compact_fragmented_table_rows(value)
    value = re.sub(r"[ \t]+\|(\n|$)", r"\1", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def sec_model_text_diagnostics(original: str | None, normalized: str | None = None) -> dict[str, int | float]:
    source = "" if original is None else str(original)
    model = normalize_sec_model_text(source) if normalized is None else str(normalized)
    source_lf = source.replace("\r\n", "\n").replace("\r", "\n")
    source_lines = source_lf.split("\n")
    normalized_lines = model.split("\n") if model else []
    paragraphs = [re.sub(r"\s+", " ", part).strip().lower() for part in re.split(r"\n{2,}", source_lf) if len(part.strip()) >= 120]
    duplicate_paragraph_count = len(paragraphs) - len(set(paragraphs))
    line_keys = [re.sub(r"\s+", " ", line).strip().lower() for line in source_lines if 10 <= len(line.strip()) <= 120]
    repeated_line_count = len(line_keys) - len(set(line_keys))
    table_fragment_line_count = sum(1 for line in source_lines if line.count("|") >= 2 or re.fullmatch(r"[ \t|]+", line or ""))
    artifact_label_line_count = sum(1 for line in source_lines if _STANDALONE_ARTIFACT_LINE_RE.fullmatch(line + "\n") or _STANDALONE_ARTIFACT_LINE_RE.fullmatch(line))
    page_header_line_count = sum(
        1
        for line in source_lines
        if _PAGE_LINE_RE.fullmatch(line + "\n")
        or _DASHED_PAGE_LINE_RE.fullmatch(line + "\n")
        or _FORM_PAGE_HEADER_RE.fullmatch(line + "\n")
    )
    separator_only_line_count = sum(
        1
        for line in source_lines
        if _SEPARATOR_ONLY_LINE_RE.fullmatch(line + "\n")
        or _PIPE_ONLY_LINE_RE.fullmatch(line + "\n")
        or _SEPARATOR_ONLY_LINE_RE.fullmatch(line)
        or _PIPE_ONLY_LINE_RE.fullmatch(line)
    )
    mojibake_count = sum(source.count(pattern) for pattern, _ in _MOJIBAKE_REPLACEMENTS if pattern.strip())
    return {
        "source_chars": len(source),
        "normalized_chars": len(model),
        "char_delta": len(source) - len(model),
        "char_delta_pct": round(((len(source) - len(model)) / len(source) * 100.0) if source else 0.0, 4),
        "source_lines": len(source_lines),
        "normalized_lines": len(normalized_lines),
        "separator_only_line_count": separator_only_line_count,
        "artifact_label_line_count": artifact_label_line_count,
        "page_header_line_count": page_header_line_count,
        "table_fragment_line_count": table_fragment_line_count,
        "duplicate_paragraph_count": duplicate_paragraph_count,
        "repeated_line_count": repeated_line_count,
        "mojibake_count": mojibake_count,
    }


def sec_model_text_sql(source_text_expr: str) -> str:
    """Return a deterministic ClickHouse SQL expression for SEC embedding text.

    The v3 normalizer removes high-confidence extraction artifacts only. It does
    not remove SEC/legal boilerplate, infer table structure, or summarize text.
    """
    text = f"ifNull({source_text_expr}, '')"
    for source, replacement in _MOJIBAKE_REPLACEMENTS:
        text = f"replaceAll({text}, {sql_string(source)}, {sql_string(replacement)})"
    text = f"replaceAll(replaceAll({text}, {sql_string(chr(13) + chr(10))}, {sql_string(chr(10))}), {sql_string(chr(13))}, {sql_string(chr(10))})"
    text = f"replaceRegexpAll({text}, {sql_string(r'(?m)^[ \t]*(?:[|+\-_=*~`.][ \t|+\-_=*~`.]*){3,}[ \t]*\n?')}, {sql_string(chr(10))})"
    text = f"replaceRegexpAll({text}, {sql_string(r'(?m)^[ \t|]{1,}\n?')}, {sql_string(chr(10))})"
    text = f"replaceRegexpAll({text}, {sql_string(r'(?im)^[ \t|]*(?:\[?(?:table text block|text block|abstract|member|axis)\]?|(?:document and entity information|dei document information|entity information)|(?:xbrl|ixbrl|inline xbrl))[ \t|]*(?:\n|$)')}, {sql_string(chr(10))})"
    text = f"replaceRegexpAll({text}, {sql_string(r'(?im)^[ \t]*(?:page|p\.)[ \t]*\d{1,4}(?:[ \t]*(?:of|/)[ \t]*\d{1,4})?[ \t]*(?:\n|$)')}, {sql_string(chr(10))})"
    text = f"replaceRegexpAll({text}, {sql_string('(?m)^[ \\t]*[-' + chr(8211) + chr(8212) + '][ \\t]*\\d{1,4}[ \\t]*[-' + chr(8211) + chr(8212) + '][ \\t]*(?:\\n|$)')}, {sql_string(chr(10))})"
    text = f"replaceRegexpAll({text}, {sql_string(r'(?im)^[ \t]*(?:form[ \t]+(?:10-k|10-q|8-k|6-k|20-f|40-f))[ \t]*(?:\n|$)')}, {sql_string(chr(10))})"
    text = f"replaceRegexpAll({text}, {sql_string(r'\b([A-Z])\s([A-Z])\s([A-Z])\s([A-Z])(?:\s([A-Z]))?(?:\s([A-Z]))?(?:\s([A-Z]))?(?:\s([A-Z]))?\b')}, {sql_string(r'\1\2\3\4\5\6\7\8')})"
    text = f"replaceRegexpAll({text}, {sql_string(r'[ \t]*\n[ \t]*')}, {sql_string(chr(10))})"
    text = f"replaceRegexpAll({text}, {sql_string(r'[ \t]{2,}')}, {sql_string(' ')})"
    text = f"replaceRegexpAll({text}, {sql_string(r'\n{3,}')}, {sql_string(chr(10) + chr(10))})"
    text = f"replaceRegexpAll({text}, {sql_string('(?m)^(' + _DATE_CELL_PATTERN + ')[ \\t]*\\n+(' + _DATE_CELL_PATTERN + ')[ \\t]*\\n+([^|\\n]{2,180} \\|)')}, {sql_string(r'Columns: \1; \2' + chr(10) + chr(10) + r'\3')})"
    text = f"replaceRegexpAll({text}, {sql_string('(?m)^([^|\\n]{2,180}?) \\|\\n+(' + _NUMERIC_CELL_PATTERN + ')[ \\t]*\\n+(' + _NUMERIC_CELL_PATTERN + ')[ \\t]*(\\n|$)')}, {sql_string(r'\1: \2; \3\4')})"
    text = f"replaceRegexpAll({text}, {sql_string(r'(?m)^([^|\n]{2,120}:) \|\n+([^|\n]{1,180})')}, {sql_string(r'\1 \2')})"
    text = f"replaceRegexpAll({text}, {sql_string(r'[ \t]+\|(\n|$)')}, {sql_string(r'\1')})"
    text = f"replaceRegexpAll({text}, {sql_string(r'\n{3,}')}, {sql_string(chr(10) + chr(10))})"
    text = f"replaceRegexpAll({text}, {sql_string(r'^[\n\t ]+')}, '')"
    text = f"replaceRegexpAll({text}, {sql_string(r'[\n\t ]+$')}, '')"
    return text


def removed_layout_line_count_sql(source_text_expr: str, model_text_expr: str) -> str:
    newline = sql_string(chr(10))
    return (
        "toUInt32(greatest("
        "toInt64(0), "
        f"toInt64(length(splitByChar({newline}, ifNull({source_text_expr}, '')))) - "
        f"toInt64(length(splitByChar({newline}, ifNull({model_text_expr}, ''))))"
        "))"
    )
