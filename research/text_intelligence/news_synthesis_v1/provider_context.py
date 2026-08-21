from __future__ import annotations

import re
from typing import Any, Iterable, Mapping


ROUTER_VERSION = "news_synthesis_provider_context_router_v1"
ROUTES = frozenset(("forecast_candidate", "context_only", "semantic_rescue_required"))

# These exact Benzinga template tags had no eligible examples in each available
# 2025, Jan-Apr 2026, and May-Aug 2026 split after the provider-filter label
# correction. Generic keyword matches are deliberately not allowed to override
# these provider-family semantics; future exceptions must change the versioned
# family rule through audited evidence.
CONTEXT_ONLY_TAG_FAMILIES: dict[str, str] = {
    "bzi-pod": "automated_price_or_options_digest",
    "bzi-tfm": "automated_ticker_mover_feed",
    "halts": "trading_halt_notice",
    "bzi-auoa": "automated_unusual_options_activity",
    "top upgrades": "analyst_rating_roundup",
    "top downgrades": "analyst_rating_roundup",
    "analysts forecasts": "analyst_forecast_roundup",
    "bzi-ep": "scheduled_earnings_preview",
}

# These families contain both material issuer events and contextual/noise rows.
# Metadata identifies the template, but is not semantic authority.
RESCUE_TAG_FAMILIES: dict[str, str] = {
    "bzi-recaps": "earnings_recap",
    "big losers": "mover_roundup",
    "big gainers": "mover_roundup",
    "mid morning market update": "market_update",
    "mid day market update": "market_update",
    "mid day movers": "mover_roundup",
}

_MATERIAL_EVENT_RE = re.compile(
    r"\b(?:acquir(?:e|es|ed|ing)|merger|definitive agreement|financing|offering|"
    r"guidance|outlook|reports? (?:quarterly|annual|Q[1-4])|earnings results?|"
    r"FDA|clinical trial|primary endpoint|regulatory approval|complete response letter|"
    r"lawsuit|settlement|contract award|appoints?|resigns?|launches?|recall|"
    r"bankruptcy|restructuring|dividend|buyback)\b",
    re.I,
)
_TEXTUAL_AMBIGUOUS_FAMILY_RE = re.compile(
    r"\b(?:market (?:wrap|recap|overview|update)|midday market update|"
    r"movers?|gainers?|losers?|top (?:upgrades|downgrades)|analyst ratings?)\b",
    re.I,
)


def _normalized(values: Iterable[Any]) -> tuple[str, ...]:
    if isinstance(values, str):
        values = (values,)
    return tuple(sorted({str(value).strip().casefold() for value in values if str(value).strip()}))


def _optional_int(source: Mapping[str, Any], name: str) -> int | None:
    value = source.get(name)
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(source: Mapping[str, Any], name: str) -> float | None:
    value = source.get(name)
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, parsed)


def _optional_bool(source: Mapping[str, Any], name: str) -> bool | None:
    value = source.get(name)
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().casefold()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    return None


def classify_provider_context(source: Mapping[str, Any]) -> dict[str, Any]:
    """Return a cheap, deterministic, auditable pre-synthesis route.

    `context_only` means skip the expensive issuer-forecast lane but retain the
    source for the separate market-context lane. `forecast_candidate` means run
    normal News Synthesis. `semantic_rescue_required` means metadata is mixed or
    contradictory and a semantic pass must decide.
    """

    provider = str(source.get("provider") or "").strip().casefold()
    tags = _normalized(source.get("provider_tags") or ())
    channels = _normalized(source.get("channels") or ())
    title = str(source.get("title") or "").strip()
    text = str(source.get("text") or source.get("rendered_text") or "").strip()
    bounded_text = f"{title}\n{text}"[:12_000]
    material_language = bool(_MATERIAL_EVENT_RE.search(bounded_text))

    matched_rescue = tuple(
        tag for tag in tags if provider == "benzinga" and tag in RESCUE_TAG_FAMILIES
    )
    matched_context = tuple(
        tag for tag in tags if provider == "benzinga" and tag in CONTEXT_ONLY_TAG_FAMILIES
    )
    reason_codes: list[str] = []

    if matched_rescue:
        route = "semantic_rescue_required"
        family = RESCUE_TAG_FAMILIES[matched_rescue[0]]
        reason_codes.extend(("mixed_provider_template", f"provider_tag:{matched_rescue[0]}"))
    elif matched_context:
        route = "context_only"
        family = CONTEXT_ONLY_TAG_FAMILIES[matched_context[0]]
        reason_codes.extend(("validated_context_template", f"provider_tag:{matched_context[0]}"))
        if material_language:
            reason_codes.append("material_language_not_authoritative_for_validated_template")
    elif material_language:
        route = "forecast_candidate"
        family = "material_issuer_event"
        reason_codes.append("material_semantic_signal")
    elif _TEXTUAL_AMBIGUOUS_FAMILY_RE.search(title):
        route = "semantic_rescue_required"
        family = "textual_roundup_or_market_update"
        reason_codes.append("text_family_without_metadata_authority")
    else:
        route = "forecast_candidate"
        family = "unclassified"
        reason_codes.append("fail_open_no_context_rule")

    first_session = _optional_bool(source, "any_ticker_first_session")
    min_ordinal = _optional_int(source, "min_ticker_session_ordinal")
    seconds_previous = _optional_float(source, "min_seconds_since_previous_ticker_news")
    novelty_available = first_session is not None or min_ordinal is not None or seconds_previous is not None
    if novelty_available:
        reason_codes.append("temporal_novelty_observed_not_decisive_v1")

    result = {
        "router_version": ROUTER_VERSION,
        "route": route,
        "content_family": family,
        "reason_codes": reason_codes,
        "material_language_detected": material_language,
        "metadata_evidence": {
            "provider": provider,
            "provider_tags": list(tags),
            "channels": list(channels),
            "matched_context_tags": list(matched_context),
            "matched_rescue_tags": list(matched_rescue),
        },
        "temporal_novelty": {
            "available": novelty_available,
            "any_ticker_first_session": first_session,
            "min_ticker_session_ordinal": min_ordinal,
            "min_seconds_since_previous_ticker_news": seconds_previous,
            "decision_role": "trace_only_v1",
        },
    }
    if route not in ROUTES:  # defensive invariant for callers outside this package
        raise AssertionError(f"invalid provider route: {route}")
    return result
