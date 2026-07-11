from __future__ import annotations

import re

from research.mlops.clickhouse import sql_string


SEC_MODEL_TEXT_NORMALIZER_VERSION = "sec_model_text_normalizer_v1"
_SEPARATOR_ONLY_LINE_RE = re.compile(r"(?m)^[ \t]*(?:[|+\-_=*~`.][ \t|+\-_=*~`.]*){3,}[ \t]*\n?")


def normalize_sec_model_text(text: str | None) -> str:
    """Conservatively normalize SEC text for embedding input."""
    value = "" if text is None else str(text)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = _SEPARATOR_ONLY_LINE_RE.sub("\n", value)
    value = re.sub(r"[ \t]*\n[ \t]*", "\n", value)
    value = re.sub(r"[ \t]{2,}", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def sec_model_text_sql(source_text_expr: str) -> str:
    """Return a conservative ClickHouse SQL expression for SEC embedding text.

    The v1 normalizer removes layout artifacts only. It does not remove legal
    boilerplate, infer table structure, or rewrite submitted text content.
    """
    text = f"replaceAll(replaceAll(ifNull({source_text_expr}, ''), {sql_string(chr(13) + chr(10))}, {sql_string(chr(10))}), {sql_string(chr(13))}, {sql_string(chr(10))})"
    text = f"replaceRegexpAll({text}, {sql_string(r'(?m)^[ \t]*(?:[|+\-_=*~`.][ \t|+\-_=*~`.]*){3,}[ \t]*\n?')}, {sql_string(chr(10))})"
    text = f"replaceRegexpAll({text}, {sql_string(r'[ \t]*\n[ \t]*')}, {sql_string(chr(10))})"
    text = f"replaceRegexpAll({text}, {sql_string(r'[ \t]{2,}')}, {sql_string(' ')})"
    text = f"replaceRegexpAll({text}, {sql_string(r'\n{3,}')}, {sql_string(chr(10) + chr(10))})"
    return f"trim({text})"


def removed_layout_line_count_sql(source_text_expr: str, model_text_expr: str) -> str:
    newline = sql_string(chr(10))
    return (
        "toUInt32(greatest("
        "toInt64(0), "
        f"toInt64(length(splitByChar({newline}, ifNull({source_text_expr}, '')))) - "
        f"toInt64(length(splitByChar({newline}, ifNull({model_text_expr}, ''))))"
        "))"
    )
