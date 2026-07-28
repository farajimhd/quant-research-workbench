from __future__ import annotations

import hashlib
import re

from research.text_intelligence.semantic_label_authority_v1.structure import (
    normalize_source_text,
    segment_rendered_text,
)

from .schema import NEWS_EXTRACTOR_VERSION, ObservedReaction, RelevantTextUnit


EXCHANGE_TICKER_RE = re.compile(
    r"\b(?:NASDAQ|NYSE|NYSEAMERICAN|NYSE\s+AMERICAN|AMEX|OTC(?:QX|QB)?|"
    r"TSX|TSXV|CSE)\s*[:\-]\s*([A-Z][A-Z0-9.-]{0,9})\b",
    re.IGNORECASE,
)
MOVE_RE = re.compile(
    r"\b(?:shares?|stock)\s+"
    r"(?P<verb>rose|gained|climbed|jumped|surged|rallied|advanced|"
    r"fell|dropped|declined|slid|plunged|tumbled|lost)\s+"
    r"(?P<pct>\d+(?:\.\d+)?)%\s*(?:to|at)\s*\$(?P<price>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
SESSION_RE = re.compile(
    r"\b(pre[- ]market|premarket|after[- ]hours|post[- ]market|"
    r"regular(?:[- ]hours)? trading|midday|mid-day)\b",
    re.IGNORECASE,
)
CATALYST_RE = re.compile(
    r"\b(?:after|following|because|as\s+(?=(?:the company|it|shares?|stock)\b))"
    r"\s+(?P<catalyst>[^.;]{8,320})",
    re.IGNORECASE,
)
AGGREGATION_TITLE_RE = re.compile(
    r"\b(?:biggest|top)\s+(?:stock\s+)?(?:gainers|losers|movers)\b|"
    r"\bstocks?\s+moving\s+(?:in|during)\b|"
    r"\b(?:pre[- ]market|after[- ]hours|mid[- ]day)\s+movers?\b",
    re.IGNORECASE,
)


def extract_news_units(
    *,
    source_id: str,
    title: str,
    text: str,
    tickers: tuple[str, ...],
) -> tuple[RelevantTextUnit, ...]:
    clean = normalize_source_text(text)
    known = tuple(dict.fromkeys(value.upper() for value in tickers if value))
    blocks = segment_rendered_text("news", clean)
    aggregation = bool(AGGREGATION_TITLE_RE.search(title))
    candidates = [
        block for block in blocks
        if block.semantic and block.kind not in {"blank", "heading"}
    ]

    if len(known) == 1 and not aggregation:
        semantic = "\n".join(
            _strip_renderer_label(block.text)
            for block in candidates
            if _strip_renderer_label(block.text)
        ).strip()
        if not semantic:
            return ()
        reaction = extract_observed_reaction(semantic)
        return (
            RelevantTextUnit(
                corpus="news",
                source_id=source_id,
                unit_id=_unit_id(source_id, 1, semantic),
                ordinal=1,
                role="primary_or_editorial_document",
                text=semantic,
                start=0,
                end=len(clean),
                tickers=known,
                shared_context=False,
                observed_reaction=reaction,
                reported_catalyst=(
                    extract_reported_catalyst(semantic)
                    if reaction.direction else ""
                ),
                extractor_version=NEWS_EXTRACTOR_VERSION,
            ),
        )

    output: list[RelevantTextUnit] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()

    for block in candidates:
        payload = _strip_renderer_label(block.text)
        associated = _associated_tickers(payload, known)
        if aggregation or len(known) > 1:
            if not associated:
                continue
        elif len(known) == 1:
            associated = known
        elif not associated:
            continue
        compact = re.sub(r"\s+", " ", payload).strip()
        key = (compact.casefold(), associated)
        if len(compact) < 20 or key in seen:
            continue
        seen.add(key)
        reaction = extract_observed_reaction(compact)
        catalyst = extract_reported_catalyst(compact)
        role = _unit_role(
            aggregation,
            len(known) > 1,
            reaction,
            compact,
        )
        ordinal = len(output) + 1
        output.append(
            RelevantTextUnit(
                corpus="news",
                source_id=source_id,
                unit_id=_unit_id(source_id, ordinal, compact),
                ordinal=ordinal,
                role=role,
                text=compact,
                start=block.start,
                end=block.end,
                tickers=associated,
                shared_context=len(associated) > 1,
                observed_reaction=reaction,
                reported_catalyst=catalyst,
                extractor_version=NEWS_EXTRACTOR_VERSION,
                quality_flags=(
                    ("context_only_market_observation",)
                    if aggregation or reaction.direction
                    else ()
                ),
            )
        )

    return tuple(output)


def extract_observed_reaction(text: str) -> ObservedReaction:
    match = MOVE_RE.search(text)
    if not match:
        return ObservedReaction()
    verb = match.group("verb").casefold()
    direction = "up" if verb in {
        "rose", "gained", "climbed", "jumped", "surged", "rallied", "advanced"
    } else "down"
    session_match = SESSION_RE.search(text)
    session = (
        session_match.group(1).casefold().replace(" ", "_").replace("-", "_")
        if session_match else ""
    )
    return ObservedReaction(
        direction=direction,
        move_pct=float(match.group("pct")),
        resulting_price=float(match.group("price")),
        market_session=session,
        evidence=match.group(0),
    )


def extract_reported_catalyst(text: str) -> str:
    match = CATALYST_RE.search(text)
    return re.sub(r"\s+", " ", match.group("catalyst")).strip() if match else ""


def _associated_tickers(text: str, known: tuple[str, ...]) -> tuple[str, ...]:
    explicit = {match.group(1).upper() for match in EXCHANGE_TICKER_RE.finditer(text)}
    for ticker in known:
        if re.search(rf"(?<![A-Z0-9]){re.escape(ticker)}(?![A-Z0-9])", text):
            explicit.add(ticker)
    return tuple(value for value in known if value in explicit) or tuple(sorted(explicit))


def _unit_role(
    aggregation: bool,
    multi_ticker: bool,
    reaction: ObservedReaction,
    text: str,
) -> str:
    if aggregation:
        return "ticker_market_observation"
    if multi_ticker:
        return "ticker_scoped_editorial_context"
    if reaction.direction:
        return "editorial_reaction_explanation"
    if re.search(r"\b(?:analyst|price target|upgrade|downgrade)\b", text, re.I):
        return "analyst_opinion"
    return "primary_or_editorial_evidence"


def _strip_renderer_label(text: str) -> str:
    value = re.sub(r"^\s*(?:Title|Teaser|Summary|Body)\s*:\s*", "", text, flags=re.I)
    value = re.sub(r"^\s*[-*]\s*", "", value)
    return re.sub(r"^\s*(?:Gainers|Losers)\s+", "", value, flags=re.I).strip()


def _unit_id(source_id: str, ordinal: int, text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return f"{source_id}:news:{ordinal}:{digest}"
