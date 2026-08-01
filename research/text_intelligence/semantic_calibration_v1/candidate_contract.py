from __future__ import annotations

import copy
import re
from dataclasses import replace
from typing import Any, Iterable, Mapping

from .comparison import CollectionItem


CANDIDATE_CONTRACT_VERSION = "news_instrument_candidate_contract_v3"

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
    """Return strongly evidenced U.S. symbols without guessing bare capitals."""
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
    authoritative_identifiers: Iterable[Any] = (),
) -> list[dict[str, Any]]:
    """Build one typed candidate roster keyed by exact canonical instrument ID."""
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    def ensure(identifier: Any) -> dict[str, Any] | None:
        canonical = _canonical_authoritative_identifier(identifier)
        if not canonical:
            return None
        if canonical not in merged:
            merged[canonical] = {
                "canonical_instrument_id": canonical,
                "display_symbol": display_symbol(canonical),
                "instrument_type": instrument_type(canonical),
                "identity_evidence": [],
            }
            order.append(canonical)
        return merged[canonical]

    for candidate in candidates:
        row = ensure(
            candidate.get("canonical_instrument_id") or candidate.get("ticker")
        )
        if row is None:
            continue
        for item in candidate.get("identity_evidence") or ():
            token = str(item)
            if token and token not in row["identity_evidence"]:
                row["identity_evidence"].append(token)
    for identifier in authoritative_identifiers:
        row = ensure(identifier)
        if row is not None:
            token = f"provider_instrument:{row['canonical_instrument_id']}"
            if token not in row["identity_evidence"]:
                row["identity_evidence"].append(token)
    for identifier, values in explicit_us_ticker_evidence(
        title, teaser, rendered_text
    ).items():
        row = ensure(identifier)
        if row is None:
            continue
        for token in values:
            if token not in row["identity_evidence"]:
                row["identity_evidence"].append(token)
    return [merged[identifier] for identifier in order]


def repair_item_candidates(item: CollectionItem) -> CollectionItem:
    blinded = copy.deepcopy(item.blinded)
    publication = blinded.get("publication") or {}
    rendered = blinded.get("rendered_product") or {}
    blinded["point_in_time_issuer_candidates"] = enrich_candidate_rows(
        blinded.get("point_in_time_issuer_candidates") or (),
        title=str(publication.get("title") or ""),
        teaser=str(publication.get("teaser") or ""),
        rendered_text=str(rendered.get("text") or ""),
        authoritative_identifiers=publication.get("provider_tickers") or (),
    )
    blinded["issuer_candidate_contract_version"] = CANDIDATE_CONTRACT_VERSION
    return replace(item, blinded=blinded)


def instrument_candidates(item: CollectionItem) -> tuple[dict[str, Any], ...]:
    repaired = repair_item_candidates(item)
    return tuple(
        dict(candidate)
        for candidate in repaired.blinded.get("point_in_time_issuer_candidates") or ()
    )


def candidate_instrument_ids(item: CollectionItem) -> tuple[str, ...]:
    return tuple(
        sorted(
            str(candidate["canonical_instrument_id"]).upper()
            for candidate in instrument_candidates(item)
        )
    )


def candidate_tickers(item: CollectionItem) -> tuple[str, ...]:
    """Compatibility name for historical audits; values are canonical IDs."""
    return candidate_instrument_ids(item)


def instrument_type(identifier: Any) -> str:
    canonical = _canonical_authoritative_identifier(identifier)
    if canonical.startswith("X:") and canonical.endswith("USD"):
        return "crypto_pair"
    if re.fullmatch(_TICKER, canonical):
        return "us_equity_or_fund"
    if re.search(r"\.(?:HK|SH|SZ|L|TO|V)$", canonical):
        return "foreign_listed_security"
    return "other_security"


def display_symbol(identifier: Any) -> str:
    canonical = _canonical_authoritative_identifier(identifier)
    if canonical.startswith("X:") and canonical.endswith("USD"):
        base = canonical[2:-3]
        return f"{base}/USD" if base else canonical
    return canonical


def _normalize_ticker(value: Any) -> str:
    ticker = str(value or "").strip().upper()
    return ticker if re.fullmatch(_TICKER, ticker) else ""


def _canonical_authoritative_identifier(value: Any) -> str:
    return str(value or "").strip().upper()
