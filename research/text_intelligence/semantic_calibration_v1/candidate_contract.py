from __future__ import annotations

import copy
import re
from dataclasses import replace
from typing import Any, Iterable, Mapping

from .comparison import CollectionItem


CANDIDATE_CONTRACT_VERSION = "news_issuer_candidate_contract_v2"

_TICKER = r"[A-Z][A-Z0-9]{0,5}(?:[.-][A-Z])?"
_EXCHANGE = (
    r"(?:NYSE(?:\s+American)?|NASDAQ|Nasdaq|New\s+York\s+Stock\s+Exchange|"
    r"American\s+Stock\s+Exchange|AMEX)"
)
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "us_exchange_symbol",
        re.compile(rf"\b{_EXCHANGE}\s*[:\-]\s*(?P<ticker>{_TICKER})\b"),
    ),
    (
        "announced_us_listing",
        re.compile(
            rf"\b(?:will|would|is\s+expected\s+to|expects\s+to)?\s*"
            rf"(?:trade|list|be\s+listed)\s+on\s+(?:the\s+)?{_EXCHANGE}\s+"
            rf"(?:under\s+)?(?:the\s+)?(?:ticker|symbol)\s*[:\-]?\s*"
            rf"(?P<ticker>{_TICKER})\b",
        ),
    ),
    (
        "explicit_ticker_symbol",
        re.compile(
            rf"\b(?:under\s+the\s+ticker|ticker\s+symbol(?:\s+of)?|"
            rf"trading\s+symbol)\s*[:\-]?\s*(?P<ticker>{_TICKER})\b"
        ),
    ),
)


def explicit_us_ticker_evidence(*texts: str) -> dict[str, tuple[str, ...]]:
    """Return strongly evidenced U.S. symbols without guessing from bare capitals.

    Exchange-qualified and explicit listing language is accepted. Foreign
    exchange identifiers and generic parenthetical capitals are intentionally
    excluded because they do not establish an in-scope U.S. instrument.
    """
    evidence: dict[str, list[str]] = {}
    for source_index, text in enumerate(texts):
        value = str(text or "")
        for family, pattern in _PATTERNS:
            for match in pattern.finditer(value):
                ticker = _normalize_ticker(match.group("ticker"))
                if not ticker:
                    continue
                token = f"explicit_symbol:{family}:{source_index}:{ticker}"
                evidence.setdefault(ticker, [])
                if token not in evidence[ticker]:
                    evidence[ticker].append(token)
    return {ticker: tuple(values) for ticker, values in evidence.items()}


def enrich_candidate_rows(
    candidates: Iterable[Mapping[str, Any]],
    *,
    title: str,
    teaser: str,
    rendered_text: str,
) -> list[dict[str, Any]]:
    merged: dict[str, list[str]] = {}
    order: list[str] = []
    for candidate in candidates:
        ticker = _canonical_authoritative_identifier(candidate.get("ticker"))
        if not ticker:
            continue
        if ticker not in merged:
            merged[ticker] = []
            order.append(ticker)
        for item in candidate.get("identity_evidence") or ():
            token = str(item)
            if token and token not in merged[ticker]:
                merged[ticker].append(token)
    explicit = explicit_us_ticker_evidence(title, teaser, rendered_text)
    for ticker, values in explicit.items():
        if ticker not in merged:
            merged[ticker] = []
            order.append(ticker)
        for token in values:
            if token not in merged[ticker]:
                merged[ticker].append(token)
    return [
        {"ticker": ticker, "identity_evidence": merged[ticker]}
        for ticker in order
    ]


def repair_item_candidates(item: CollectionItem) -> CollectionItem:
    blinded = copy.deepcopy(item.blinded)
    publication = blinded.get("publication") or {}
    rendered = blinded.get("rendered_product") or {}
    blinded["point_in_time_issuer_candidates"] = enrich_candidate_rows(
        blinded.get("point_in_time_issuer_candidates") or (),
        title=str(publication.get("title") or ""),
        teaser=str(publication.get("teaser") or ""),
        rendered_text=str(rendered.get("text") or ""),
    )
    blinded["issuer_candidate_contract_version"] = CANDIDATE_CONTRACT_VERSION
    return replace(item, blinded=blinded)


def candidate_tickers(item: CollectionItem) -> tuple[str, ...]:
    values = {
        _canonical_authoritative_identifier(candidate.get("ticker"))
        for candidate in item.blinded.get("point_in_time_issuer_candidates") or ()
    }
    values.update(
        _canonical_authoritative_identifier(ticker)
        for ticker in (item.blinded.get("publication") or {}).get("provider_tickers") or ()
    )
    return tuple(sorted(value for value in values if value))


def _normalize_ticker(value: Any) -> str:
    ticker = str(value or "").strip().upper()
    return ticker if re.fullmatch(_TICKER, ticker) else ""


def _canonical_authoritative_identifier(value: Any) -> str:
    """Preserve source-authoritative instruments; syntax gates only inference."""
    return str(value or "").strip().upper()
