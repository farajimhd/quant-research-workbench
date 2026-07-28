from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable

from .schema import SemanticDocument, SemanticSpan, StructuralBlock
from .structure import block_for_offset, normalize_source_text


@dataclass(frozen=True, slots=True)
class PatternSpec:
    span_type: str
    subtype: str
    pattern: re.Pattern[str]
    unit: str = ""


MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
)
EXCHANGE = r"(?:NASDAQ|NYSE|NYSEAMERICAN|NYSE\s+AMERICAN|AMEX|OTC(?:QB|QX|MKTS)?|TSX|CSE|LSE)"
TICKER = r"[A-Z][A-Z0-9.-]{0,9}"

SPECS: tuple[PatternSpec, ...] = (
    PatternSpec("identifier", "sec_accession", re.compile(r"\b\d{10}-\d{2}-\d{6}\b")),
    PatternSpec(
        "identifier",
        "sec_form",
        re.compile(
            r"(?<![A-Z0-9-])(?:FORM\s+)?(?:X-17A-5|N-[A-Z0-9-]+|"
            r"(?:10|8|6|20|40)-[KQF]|S-[1348](?:ASR|MEF)?|F-[134680]|"
            r"424B\d*|DEF\s*14A|SC\s+13[DG]|"
            r"EX-\d+(?:\.\d+)?|T-\d+)(?![A-Z0-9-])",
            re.IGNORECASE,
        ),
    ),
    PatternSpec("identifier", "edgar_form_id", re.compile(r"\bForm\s+ID\b")),
    PatternSpec("identifier", "sec_form_list", re.compile(r"\bForms?\s+3,\s*4,?\s*(?:and\s+)?5\b", re.IGNORECASE)),
    PatternSpec("identifier", "sec_item", re.compile(r"\bItem\s+\d{1,2}(?:\.\d{2})?\b", re.IGNORECASE)),
    PatternSpec("identifier", "regulatory_citation", re.compile(r"\b(?:Section|Rule)\s+\d+(?:\([a-z0-9]+\))?(?:\([a-z0-9]+\))?\b", re.IGNORECASE)),
    PatternSpec("identifier", "cik", re.compile(r"\bCIK\s*[:#]?\s*0*\d{4,10}\b", re.IGNORECASE)),
    PatternSpec("identifier", "cik", re.compile(r"(?:filerCik|issuerCik|cik)\s*[>:]\s*0*\d{4,10}\b", re.IGNORECASE)),
    PatternSpec("identifier", "ein", re.compile(r"\b(?:EIN|IRS\s+Employer\s+Identification\s+No\.?)\s*[:#]?\s*\d{2}-\d{7}\b", re.IGNORECASE)),
    PatternSpec("identifier", "cusip", re.compile(r"\bCUSIP\s*[:#]?\s*[0-9A-Z*@#]{9}\b", re.IGNORECASE)),
    PatternSpec("identifier", "isin", re.compile(r"\bISIN\s*[:#]?\s*[A-Z]{2}[A-Z0-9]{9}\d\b", re.IGNORECASE)),
    PatternSpec("identifier", "postal_code", re.compile(r"(?:zipCode\s*[>:]\s*|Washington,\s*D\.C\.\s+)\d{5}(?:-\d{4})?", re.IGNORECASE)),
    PatternSpec("identifier", "email", re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")),
    PatternSpec("identifier", "url", re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE)),
    PatternSpec(
        "market_identity",
        "exchange_ticker",
        re.compile(rf"(?:\(|\b)({EXCHANGE})\s*:\s*({TICKER})(?:\)|\b)", re.IGNORECASE),
    ),
    PatternSpec("market_identity", "cashtag", re.compile(rf"(?<!\w)\$({TICKER})\b")),
    PatternSpec("temporal", "iso_datetime", re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?\b")),
    PatternSpec("temporal", "iso_date", re.compile(r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b")),
    PatternSpec("temporal", "named_date", re.compile(rf"\b{MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+(?:19|20)\d{{2}}\b", re.IGNORECASE)),
    PatternSpec("temporal", "month_day", re.compile(rf"\b{MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?\b", re.IGNORECASE)),
    PatternSpec("temporal", "numeric_date", re.compile(r"\b(?:0?[1-9]|1[0-2])[/.-](?:0?[1-9]|[12]\d|3[01])[/.-](?:19|20)?\d{2}\b")),
    PatternSpec("temporal", "clock_time", re.compile(r"\b(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d+)?)?\s*(?:a\.?m\.?|p\.?m\.?|ET|EST|EDT|UTC|GMT|PT|PST|PDT)?\b", re.IGNORECASE)),
    PatternSpec("temporal", "fiscal_period", re.compile(r"\b(?:FY|Q[1-4]|[1-4]Q)\s*(?:19|20)?\d{2}\b|\bfiscal\s+(?:year|quarter)\s+(?:ended|ending)?\s*[^,.;\n]{0,40}", re.IGNORECASE)),
    PatternSpec("temporal", "duration", re.compile(r"\b\d+(?:\.\d+)?\s+(?:business\s+|calendar\s+|trading\s+)?(?:minutes?|hours?|days?|weeks?|months?|years?)\b", re.IGNORECASE)),
    PatternSpec("temporal", "year", re.compile(r"\b(?:19|20)\d{2}\b")),
    PatternSpec("financial", "price_per_share", re.compile(r"(?:(?:US)?\$|USD\s*)\s*\d[\d,]*(?:\.\d+)?\s*(?:per|/)\s*(?:common\s+)?share", re.IGNORECASE), "USD/share"),
    PatternSpec("financial", "money_range", re.compile(r"(?:(?:US|CA|C|A)?\$|USD|CAD|EUR|GBP|€|£)\s*\d[\d,]*(?:\.\d+)?\s*(?:-|–|to)\s*\d[\d,]*(?:\.\d+)?\s*(?:thousand|million|billion|trillion|k|m|bn|b)?\b", re.IGNORECASE)),
    PatternSpec("financial", "money", re.compile(r"(?:(?:US|CA|C|A)?\$|USD|CAD|EUR|GBP|€|£)\s*\(?\s*\d(?:[\d,]*\d)?(?:\.\d+)?\s*\)?\s*(?:thousand|million|billion|trillion|k|m|bn|b)?\b", re.IGNORECASE)),
    PatternSpec("financial", "share_count", re.compile(r"\b\d[\d,]*(?:\.\d+)?\s*(?:thousand|million|billion|k|m|bn)?\s+(?:common\s+|ordinary\s+)?shares?\b", re.IGNORECASE), "shares"),
    PatternSpec("financial", "percentage", re.compile(r"(?<![\w.-])[+-]?\d[\d,]*(?:\.\d+)?\s*(?:%|percent\b)", re.IGNORECASE), "%"),
    PatternSpec("financial", "basis_points", re.compile(r"\b[+-]?\d[\d,]*(?:\.\d+)?\s*(?:basis\s+points?|bps)\b", re.IGNORECASE), "bps"),
    PatternSpec("financial", "multiple", re.compile(r"\b\d[\d,]*(?:\.\d+)?\s*[xX]\b"), "x"),
    PatternSpec("financial", "ratio", re.compile(r"\b\d+(?:\.\d+)?\s+(?:to)\s+\d+(?:\.\d+)?\b", re.IGNORECASE)),
)

GENERIC_NUMBER_RE = re.compile(r"(?<![\w.-])[+-]?\d(?:[\d,]*\d)?(?:\.\d+)?(?![\w.-])")


def extract_spans(
    document: SemanticDocument,
    blocks: tuple[StructuralBlock, ...],
) -> tuple[SemanticSpan, ...]:
    text = normalize_source_text(document.text)
    occupied: list[tuple[int, int]] = []
    spans: list[SemanticSpan] = []
    for spec in SPECS:
        for match in spec.pattern.finditer(text):
            start, end = match.span()
            block = block_for_offset(blocks, start)
            if block is not None and not block.semantic:
                continue
            if overlaps(occupied, start, end):
                continue
            raw = match.group(0)
            normalized, unit, attributes = normalize_span(spec, raw, match, block)
            spans.append(
                SemanticSpan(
                    span_type=spec.span_type,
                    subtype=spec.subtype,
                    raw=raw,
                    normalized=normalized,
                    start=start,
                    end=end,
                    context=context(text, start, end),
                    unit=unit,
                    attributes=attributes,
                )
            )
            occupied.append((start, end))

    spans.extend(extract_metadata_entities(document, text, blocks, occupied))

    for match in GENERIC_NUMBER_RE.finditer(text):
        start, end = match.span()
        block = block_for_offset(blocks, start)
        if block is not None and not block.semantic:
            continue
        if overlaps(occupied, start, end):
            continue
        raw = match.group(0)
        normalized = decimal_number(raw)
        attrs: dict[str, object] = {}
        subtype = "number"
        unit = ""
        if block and block.kind == "table_row" and block.table_multiplier > 1:
            subtype = "table_quantity"
            attrs = {
                "table_columns": block.table_columns,
                "inherited_multiplier": block.table_multiplier,
            }
            if block.table_currency:
                unit = block.table_currency
                normalized = scale_number(normalized, block.table_multiplier)
                attrs["inherited_currency"] = block.table_currency
        spans.append(
            SemanticSpan(
                span_type="quantity",
                subtype=subtype,
                raw=raw,
                normalized=normalized,
                start=start,
                end=end,
                context=context(text, start, end),
                unit=unit,
                attributes=attrs,
            )
        )
        occupied.append((start, end))
    return tuple(sorted(spans, key=lambda value: (value.start, value.end, value.span_type)))


def extract_metadata_entities(
    document: SemanticDocument,
    text: str,
    blocks: tuple[StructuralBlock, ...],
    occupied: list[tuple[int, int]],
) -> list[SemanticSpan]:
    values: list[SemanticSpan] = []
    ticker_set = {value.upper() for value in document.tickers if value}
    for ticker in ticker_set:
        pattern = re.compile(rf"(?<![A-Z0-9]){re.escape(ticker)}(?![A-Z0-9])", re.IGNORECASE)
        for match in pattern.finditer(text):
            start, end = match.span()
            block = block_for_offset(blocks, start)
            if (block and not block.semantic) or overlaps(occupied, start, end):
                continue
            values.append(SemanticSpan(
                "market_identity", "ticker", match.group(0), ticker, start, end,
                context(text, start, end), attributes={"source": "metadata"},
            ))
            occupied.append((start, end))
    for term in sorted({value.strip() for value in document.entity_terms if len(value.strip()) >= 3}, key=len, reverse=True):
        if term.upper() in ticker_set or term.isdigit():
            continue
        pattern = re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)
        for match in pattern.finditer(text):
            start, end = match.span()
            block = block_for_offset(blocks, start)
            if (block and not block.semantic) or overlaps(occupied, start, end):
                continue
            values.append(SemanticSpan(
                "entity", "issuer_or_named_entity", match.group(0), term, start, end,
                context(text, start, end), confidence=0.95,
                attributes={"source": "metadata", "role": "issuer_or_named_entity"},
            ))
            occupied.append((start, end))
    return values


def normalize_span(
    spec: PatternSpec,
    raw: str,
    match: re.Match[str],
    block: StructuralBlock | None,
) -> tuple[str, str, dict[str, object]]:
    attributes: dict[str, object] = {}
    unit = spec.unit
    if spec.subtype == "exchange_ticker":
        exchange = re.sub(r"\s+", "_", match.group(1).upper())
        ticker = match.group(2).upper()
        return f"{exchange}:{ticker}", "symbol", {"exchange": exchange, "ticker": ticker}
    if spec.subtype == "cashtag":
        return match.group(1).upper(), "symbol", {"ticker": match.group(1).upper()}
    if spec.span_type == "temporal":
        return re.sub(r"\s+", " ", raw.strip()), "datetime" if "time" in spec.subtype else "date", attributes
    if spec.span_type == "identifier":
        return canonical_identifier(spec.subtype, raw), spec.subtype, attributes
    if spec.span_type == "financial":
        number = decimal_number(raw)
        multiplier = magnitude_multiplier(raw)
        if multiplier != 1:
            number = scale_number(number, multiplier)
            attributes["explicit_multiplier"] = multiplier
        currency = currency_unit(raw)
        if currency:
            unit = currency if spec.subtype != "price_per_share" else f"{currency}/share"
        if block and block.kind == "table_row" and block.table_multiplier > 1:
            attributes["table_columns"] = block.table_columns
        if spec.subtype == "money_range":
            numbers = re.findall(r"\d(?:[\d,]*\d)?(?:\.\d+)?", raw)
            normalized_values = [
                scale_number(decimal_number(value), multiplier) for value in numbers[:2]
            ]
            attributes["lower"] = normalized_values[0] if normalized_values else ""
            attributes["upper"] = normalized_values[1] if len(normalized_values) > 1 else ""
            number = "..".join(normalized_values)
        return number, unit, attributes
    return raw.strip(), unit, attributes


def canonical_identifier(subtype: str, raw: str) -> str:
    value = re.sub(r"\s+", " ", raw.strip())
    if subtype in {"sec_form", "sec_item", "cik", "ein", "cusip", "isin"}:
        return value.upper()
    return value


def overlaps(occupied: Iterable[tuple[int, int]], start: int, end: int) -> bool:
    return any(start < occupied_end and end > occupied_start for occupied_start, occupied_end in occupied)


def decimal_number(raw: str) -> str:
    match = re.search(r"[+-]?\d[\d,]*(?:\.\d+)?", raw)
    if not match:
        return ""
    try:
        value = Decimal(match.group(0).replace(",", ""))
    except InvalidOperation:
        return ""
    if value == value.to_integral():
        return str(int(value))
    return format(value.normalize(), "f")


def scale_number(value: str, multiplier: int) -> str:
    if not value:
        return ""
    result = Decimal(value) * Decimal(multiplier)
    return str(int(result)) if result == result.to_integral() else format(result.normalize(), "f")


def magnitude_multiplier(raw: str) -> int:
    lowered = raw.casefold()
    if re.search(r"\b(?:trillion)\b", lowered):
        return 1_000_000_000_000
    if re.search(r"\b(?:billion|bn)\b|\d\s*b\b", lowered):
        return 1_000_000_000
    if re.search(r"\b(?:million)\b|\d\s*m\b", lowered):
        return 1_000_000
    if re.search(r"\b(?:thousand)\b|\d\s*k\b", lowered):
        return 1_000
    return 1


def currency_unit(raw: str) -> str:
    stripped = raw.lstrip()
    if stripped.startswith(("CA$", "C$")) or re.match(r"CAD\b", stripped, re.IGNORECASE):
        return "CAD"
    if stripped.startswith(("A$",)) or re.match(r"AUD\b", stripped, re.IGNORECASE):
        return "AUD"
    if stripped.startswith("€") or re.match(r"EUR\b", stripped, re.IGNORECASE):
        return "EUR"
    if stripped.startswith("£") or re.match(r"GBP\b", stripped, re.IGNORECASE):
        return "GBP"
    if "$" in stripped or re.match(r"USD\b", stripped, re.IGNORECASE):
        return "USD"
    return ""


def context(text: str, start: int, end: int, size: int = 180) -> str:
    half = size // 2
    left = max(0, start - half)
    right = min(len(text), end + half)
    value = re.sub(r"\s+", " ", text[left:right]).strip()
    return ("…" if left else "") + value + ("…" if right < len(text) else "")
