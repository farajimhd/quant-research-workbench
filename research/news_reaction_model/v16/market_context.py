from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping, Sequence

import numpy as np


MARKET_CONTEXT_SIZE = 100
MARKET_CONTEXT_MAX_SESSION_DISTANCE = 3
MARKET_LEADER_SIZE = 20
MARKET_WINDOWS_SECONDS = (60, 300, 600, 1800)
MARKET_WINDOW_NAMES = ("1m", "5m", "10m", "30m")

WINDOW_FIELDS = (
    "terminal_return",
    "high_return",
    "low_return",
    "volume_log",
    "dollar_volume_log",
    "trade_count_log",
    "quote_count_log",
    "vwap_distance",
    "available",
)
SESSION_FIELDS = tuple(f"session_{field}" for field in WINDOW_FIELDS)
RANK_FIELDS = (
    "return_percentile",
    "volume_percentile",
    "dollar_volume_percentile",
    "relative_volume_percentile",
    "is_top10_gainer",
    "is_top20_gainer",
    "is_top10_loser",
    "is_top20_loser",
    "is_top10_volume",
    "is_top20_volume",
    "is_top10_relative_volume",
    "is_top20_relative_volume",
)
CURRENT_MARKET_FEATURE_NAMES = tuple(
    f"pre_{window}_{field}"
    for window in MARKET_WINDOW_NAMES
    for field in WINDOW_FIELDS
) + SESSION_FIELDS + RANK_FIELDS
CURRENT_MARKET_FEATURE_DIM = len(CURRENT_MARKET_FEATURE_NAMES)

OBSERVED_WINDOW_FIELDS = (
    "terminal_return",
    "high_return",
    "low_return",
    "volume_log",
    "dollar_volume_log",
    "trade_count_log",
    "quote_count_log",
    "available",
)
OBSERVED_FEATURE_NAMES = tuple(
    f"observed_{window}_{field}"
    for window in MARKET_WINDOW_NAMES
    for field in OBSERVED_WINDOW_FIELDS
) + tuple(f"observed_asof_{field}" for field in OBSERVED_WINDOW_FIELDS)
RELATION_FEATURE_NAMES = (
    "age_log",
    "same_ticker",
    "same_exchange_session",
    "session_distance",
)
MARKET_NEWS_FEATURE_NAMES = (
    CURRENT_MARKET_FEATURE_NAMES + OBSERVED_FEATURE_NAMES + RELATION_FEATURE_NAMES
)
MARKET_NEWS_FEATURE_DIM = len(MARKET_NEWS_FEATURE_NAMES)

LEADER_FEATURE_NAMES = CURRENT_MARKET_FEATURE_NAMES + (
    "has_recent_news",
    "recent_news_count_log",
)
LEADER_FEATURE_DIM = len(LEADER_FEATURE_NAMES)


def signed_log1p(value: Any, *, scale: float = 10.0, limit: float = 8.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return max(-limit, min(limit, math.copysign(math.log1p(abs(number)) / scale, number)))


def nonnegative_log1p(value: Any, *, scale: float = 10.0, limit: float = 8.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number) or number < 0:
        return 0.0
    return min(limit, math.log1p(number) / scale)


def bounded_unit(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number)) if math.isfinite(number) else 0.0


def encode_market_window(values: Mapping[str, Any] | None) -> np.ndarray:
    row = values or {}
    available = bool(row.get("available"))
    if not available:
        return np.zeros(len(WINDOW_FIELDS), dtype=np.float32)
    return np.asarray(
        (
            float(row.get("terminal_return") or 0.0),
            float(row.get("high_return") or 0.0),
            float(row.get("low_return") or 0.0),
            nonnegative_log1p(row.get("volume")),
            nonnegative_log1p(row.get("dollar_volume")),
            nonnegative_log1p(row.get("trade_count")),
            nonnegative_log1p(row.get("quote_count")),
            float(row.get("vwap_distance") or 0.0),
            1.0,
        ),
        dtype=np.float32,
    )


def encode_current_market_features(
    windows: Mapping[str, Mapping[str, Any]],
    session: Mapping[str, Any] | None,
    ranks: Mapping[str, Any] | None,
) -> np.ndarray:
    values = [encode_market_window(windows.get(window)) for window in MARKET_WINDOW_NAMES]
    values.append(encode_market_window(session))
    rank = ranks or {}
    rank_values = np.asarray(
        (
            bounded_unit(rank.get("return_percentile")),
            bounded_unit(rank.get("volume_percentile")),
            bounded_unit(rank.get("dollar_volume_percentile")),
            bounded_unit(rank.get("relative_volume_percentile")),
            float(bool(rank.get("is_top10_gainer"))),
            float(bool(rank.get("is_top20_gainer"))),
            float(bool(rank.get("is_top10_loser"))),
            float(bool(rank.get("is_top20_loser"))),
            float(bool(rank.get("is_top10_volume"))),
            float(bool(rank.get("is_top20_volume"))),
            float(bool(rank.get("is_top10_relative_volume"))),
            float(bool(rank.get("is_top20_relative_volume"))),
        ),
        dtype=np.float32,
    )
    result = np.concatenate((*values, rank_values)).astype(np.float32, copy=False)
    if result.shape != (CURRENT_MARKET_FEATURE_DIM,) or not np.isfinite(result).all():
        raise ValueError(f"Invalid current-market vector {result.shape}.")
    return result


def encode_observed_window(values: Mapping[str, Any] | None) -> np.ndarray:
    row = values or {}
    available = bool(row.get("available"))
    if not available:
        return np.zeros(len(OBSERVED_WINDOW_FIELDS), dtype=np.float32)
    return np.asarray(
        (
            float(row.get("terminal_return") or 0.0),
            float(row.get("high_return") or 0.0),
            float(row.get("low_return") or 0.0),
            nonnegative_log1p(row.get("volume")),
            nonnegative_log1p(row.get("dollar_volume")),
            nonnegative_log1p(row.get("trade_count")),
            nonnegative_log1p(row.get("quote_count")),
            1.0,
        ),
        dtype=np.float32,
    )


def encode_market_news_feature(
    *,
    pre_news: Sequence[float],
    completed_reactions: Mapping[str, Mapping[str, Any]],
    asof_reaction: Mapping[str, Any] | None,
    age_seconds: float,
    same_ticker: bool,
    same_exchange_session: bool,
    session_distance: int,
) -> np.ndarray:
    pre = np.asarray(pre_news, dtype=np.float32)
    if pre.shape != (CURRENT_MARKET_FEATURE_DIM,):
        raise ValueError(f"Invalid prior pre-news vector {pre.shape}.")
    observed = [
        encode_observed_window(completed_reactions.get(window))
        for window in MARKET_WINDOW_NAMES
    ]
    observed.append(encode_observed_window(asof_reaction))
    metadata = np.asarray(
        (
            nonnegative_log1p(max(0.0, age_seconds), scale=8.0),
            float(bool(same_ticker)),
            float(bool(same_exchange_session)),
            max(
                0.0,
                min(
                    1.0,
                    float(session_distance) / max(1, MARKET_CONTEXT_MAX_SESSION_DISTANCE),
                ),
            ),
        ),
        dtype=np.float32,
    )
    result = np.concatenate((pre, *observed, metadata)).astype(np.float32, copy=False)
    if result.shape != (MARKET_NEWS_FEATURE_DIM,) or not np.isfinite(result).all():
        raise ValueError(f"Invalid market-news vector {result.shape}.")
    return result


def contract_payload() -> dict[str, Any]:
    return {
        "version": "news_market_context_v16",
        "selection": {
            "articles": (
                "latest 100 canonical single-ticker articles with publication time "
                "strictly before the current article; equal timestamps are excluded"
            ),
            "maximum_session_distance": MARKET_CONTEXT_MAX_SESSION_DISTANCE,
            "leaders": (
                "up to 20 point-in-time gainers, losers, volume, dollar-volume, "
                "and relative-volume leaders, deduplicated by ticker"
            ),
        },
        "pre_news_windows_seconds": MARKET_WINDOWS_SECONDS,
        "current_feature_names": CURRENT_MARKET_FEATURE_NAMES,
        "market_news_feature_names": MARKET_NEWS_FEATURE_NAMES,
        "leader_feature_names": LEADER_FEATURE_NAMES,
        "causality": {
            "main_article": (
                "all event windows use completed one-minute bars ending no later "
                "than the historical publication timestamp"
            ),
            "prior_article": (
                "post-news reaction is capped at the current article publication time; "
                "unfinished fixed windows are zero and masked"
            ),
            "rank": "calculated from the causal tradable universe at the same as-of time",
            "padding": "index -1, zero feature vector, false mask",
        },
        "identity": "ticker identity is not embedded; only same-ticker relation is exposed",
        "dimensions": {
            "current_market": CURRENT_MARKET_FEATURE_DIM,
            "market_news": MARKET_NEWS_FEATURE_DIM,
            "leader": LEADER_FEATURE_DIM,
            "market_news_slots": MARKET_CONTEXT_SIZE,
            "leader_slots": MARKET_LEADER_SIZE,
        },
    }


def contract_sha256() -> str:
    body = json.dumps(contract_payload(), sort_keys=True, separators=(",", ":"), default=list)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()
