from __future__ import annotations

import re
from datetime import UTC, datetime
import math
from typing import Any, Iterable, Mapping


ROUTER_VERSION = "news_synthesis_provider_context_router_v5"
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
    "bzi-aar": "automated_analyst_action_roundup",
    "bzi-shorthist": "short_interest_history",
    "bzi-uoa": "automated_unusual_options_activity",
    "bzi-pe": "scheduled_earnings_preview",
    "rsi": "technical_indicator_screen",
    "$500 dividend": "hypothetical_dividend_screen",
    "bzi-ipopreview": "scheduled_ipo_preview",
    "overbought stocks": "technical_indicator_screen",
    "oversold stocks": "technical_indicator_screen",
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
    "most accurate analysts": "analyst_forecast_roundup",
}

_EXACT_CONTEXT_CHANNEL_SETS: dict[frozenset[str], str] = {
    frozenset(("options",)): "options_market_activity",
    frozenset(("analyst ratings", "news", "price target")): "analyst_rating_roundup",
    frozenset(("analyst ratings", "hot", "news", "price target")): "analyst_rating_roundup",
    frozenset(("analyst ratings", "news", "price target", "reiteration")): "analyst_rating_roundup",
    frozenset(("analyst ratings", "initiation", "news", "price target")): "analyst_rating_roundup",
    frozenset(("analyst ratings", "news", "price target", "upgrades")): "analyst_rating_roundup",
    frozenset(("analyst ratings", "downgrades", "news", "price target")): "analyst_rating_roundup",
}
# These audited channel pairs are subset predicates: additional channels are
# allowed. Each has more than 10,000 corrected development examples, support in
# all three temporal partitions, zero eligible examples, and no unreviewed
# stable-path exception under the v2 label authority.
_CONTEXT_CHANNEL_SUBSETS: dict[frozenset[str], str] = {
    frozenset(("analyst ratings", "hot")): "analyst_rating_roundup",
    frozenset(("hot", "price target")): "analyst_rating_roundup",
}
_RESCUE_CHANNEL_SETS: dict[frozenset[str], str] = {
    frozenset(("movers",)): "mover_roundup",
}
# This pair is noise-dominant but retains 35 reviewed eligible articles in the
# corrected development authority. It is therefore an explicit semantic-rescue
# family, never a hard context rejection.
_RESCUE_CHANNEL_SUBSETS: dict[frozenset[str], str] = {
    frozenset(("long ideas", "markets")): "investment_idea_or_issuer_event",
}

_M_AND_A_EVENT_CHANNELS = frozenset(("m&a", "news"))
_SMALL_ISSUER_MOVER_BUCKET_SET = frozenset((
    "nano_lt_50m", "micro_50m_300m", "small_300m_2b",
))

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


def _parse_utc(value: Any) -> datetime | None:
    if value is None or not str(value).strip():
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _market_cap_evidence(source: Mapping[str, Any], tickers: tuple[str, ...]) -> dict[str, Any]:
    ticker_count = len(tickers)
    expected_tickers = set(tickers)
    published = _parse_utc(source.get("source_timestamp") or source.get("published_at_utc") or source.get("published_at_text"))
    raw_rows = source.get("market_cap_tickers")
    if published is None or not isinstance(raw_rows, (list, tuple)):
        return {"available": False, "causal": False, "reason": "missing_timestamp_or_ticker_context"}
    buckets: set[str] = set()
    known = 0
    invalid = 0
    sources: set[str] = set()
    seen_tickers: set[str] = set()
    for row in raw_rows:
        if not isinstance(row, Mapping):
            invalid += 1
            continue
        value = _optional_float(row, "market_cap")
        available_at = _parse_utc(row.get("market_cap_available_at_utc"))
        bucket = str(row.get("market_cap_bucket") or "").strip()
        if value is None:
            continue
        ticker = str(row.get("ticker") or "").strip().casefold()
        if ticker not in expected_tickers or ticker in seen_tickers:
            invalid += 1
            continue
        seen_tickers.add(ticker)
        if value <= 0 or not math.isfinite(value) or available_at is None or available_at >= published or not bucket or bucket == "missing":
            invalid += 1
            continue
        known += 1
        buckets.add(bucket)
        sources.add(str(row.get("market_cap_source") or "unknown"))
    if invalid:
        return {
            "available": False, "causal": False, "reason": "invalid_or_noncausal_ticker_context",
            "known_ticker_count": known, "invalid_ticker_rows": invalid,
        }
    if known == 0:
        return {
            "available": False, "causal": True, "reason": "no_known_market_caps",
            "known_ticker_count": 0, "ticker_count": ticker_count,
        }
    ordered = sorted(buckets)
    bucket_rank = {
        "nano_lt_50m": 0, "micro_50m_300m": 1, "small_300m_2b": 2,
        "mid_2b_10b": 3, "large_10b_200b": 4, "mega_gte_200b": 5,
    }
    if any(bucket not in bucket_rank for bucket in ordered):
        return {"available": False, "causal": False, "reason": "unknown_market_cap_bucket"}
    return {
        "available": True,
        "causal": True,
        "reason": "strictly_prior_ticker_context",
        "known_ticker_count": known,
        "ticker_count": ticker_count,
        "coverage": "complete" if known == ticker_count else "partial",
        "bucket_set": ordered,
        "max_bucket": max(ordered, key=bucket_rank.__getitem__),
        "source_set": sorted(sources),
    }


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
    exact_context_channel_family = _EXACT_CONTEXT_CHANNEL_SETS.get(frozenset(channels)) if provider == "benzinga" else None
    rescue_channel_family = _RESCUE_CHANNEL_SETS.get(frozenset(channels)) if provider == "benzinga" else None
    channel_set = frozenset(channels)
    context_channel_subset = next(
        (
            (subset, family)
            for subset, family in _CONTEXT_CHANNEL_SUBSETS.items()
            if provider == "benzinga" and subset.issubset(channel_set)
        ),
        None,
    )
    rescue_channel_subset = next(
        (
            (subset, family)
            for subset, family in _RESCUE_CHANNEL_SUBSETS.items()
            if provider == "benzinga" and subset.issubset(channel_set)
        ),
        None,
    )
    ticker_values = _normalized(source.get("tickers") or source.get("entity_terms") or ())
    ticker_count = len(ticker_values)
    market_cap = _market_cap_evidence(source, ticker_values)
    market_cap_rule = ""
    if provider == "benzinga" and bool(market_cap.get("available")):
        bucket_set = frozenset(str(value) for value in market_cap.get("bucket_set") or ())
        if "movers" in channel_set and bucket_set == _SMALL_ISSUER_MOVER_BUCKET_SET:
            market_cap_rule = "small_issuer_multi_band_mover"
        elif ticker_count > 10 and market_cap.get("max_bucket") == "micro_50m_300m":
            market_cap_rule = "micro_cap_many_ticker_list"
    event_prior = (
        provider == "benzinga"
        and channel_set == _M_AND_A_EVENT_CHANNELS
        and not tags
        and ticker_count == 2
    )
    reason_codes: list[str] = []

    if market_cap_rule:
        route = "context_only"
        family = market_cap_rule
        reason_codes.extend(("validated_market_cap_context_rule", f"market_cap_rule:{market_cap_rule}"))
    elif matched_rescue or rescue_channel_family or rescue_channel_subset:
        route = "semantic_rescue_required"
        family = (
            RESCUE_TAG_FAMILIES[matched_rescue[0]]
            if matched_rescue
            else str(rescue_channel_family)
            if rescue_channel_family
            else str(rescue_channel_subset[1])
        )
        evidence_code = (
            f"provider_tag:{matched_rescue[0]}"
            if matched_rescue
            else f"channel_set:{'|'.join(channels)}"
            if rescue_channel_family
            else f"channel_subset:{'|'.join(sorted(rescue_channel_subset[0]))}"
        )
        reason_codes.extend(("mixed_provider_template", evidence_code))
    elif matched_context or exact_context_channel_family or context_channel_subset:
        route = "context_only"
        family = (
            CONTEXT_ONLY_TAG_FAMILIES[matched_context[0]]
            if matched_context
            else str(exact_context_channel_family)
            if exact_context_channel_family
            else str(context_channel_subset[1])
        )
        evidence_code = (
            f"provider_tag:{matched_context[0]}"
            if matched_context
            else f"channel_set:{'|'.join(channels)}"
            if exact_context_channel_family
            else f"channel_subset:{'|'.join(sorted(context_channel_subset[0]))}"
        )
        reason_codes.extend(("validated_context_template", evidence_code))
        if material_language:
            reason_codes.append("material_language_not_authoritative_for_validated_template")
    elif event_prior:
        route = "forecast_candidate"
        family = "merger_acquisition_event_prior"
        reason_codes.extend((
            "audited_event_prior_requires_semantic_confirmation",
            "metadata_signature:m&a|news:no_tags:ticker_count_2",
        ))
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
            "matched_exact_channel_family": exact_context_channel_family or "",
            "matched_rescue_channel_family": rescue_channel_family or "",
            "matched_context_channel_subset": (
                sorted(context_channel_subset[0]) if context_channel_subset else []
            ),
            "matched_rescue_channel_subset": (
                sorted(rescue_channel_subset[0]) if rescue_channel_subset else []
            ),
            "matched_event_prior": event_prior,
            "market_cap": market_cap,
            "matched_market_cap_rule": market_cap_rule,
        },
        "temporal_novelty": {
            "available": novelty_available,
            "any_ticker_first_session": first_session,
            "min_ticker_session_ordinal": min_ordinal,
            "min_seconds_since_previous_ticker_news": seconds_previous,
            "decision_role": "trace_only_v5",
        },
    }
    if route not in ROUTES:  # defensive invariant for callers outside this package
        raise AssertionError(f"invalid provider route: {route}")
    return result
