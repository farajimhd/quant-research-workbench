from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Mapping, Sequence

import numpy as np

from research.news_reaction_model.v16 import HORIZONS, SESSIONS
from research.news_reaction_model.v16.time_features import parse_published_at_utc


CONTEXT_SIZE = 4
CONTEXT_LOOKBACK_DAYS = 7
REACTION_VALUES_PER_HORIZON = 3
REACTION_VALUE_DIM = len(HORIZONS) * REACTION_VALUES_PER_HORIZON
REACTION_AVAILABILITY_DIM = len(HORIZONS)
CONTEXT_TEMPORAL_DIM = 3
CONTEXT_FLAG_DIM = 2
CONTEXT_SESSION_DIM = len(SESSIONS)
CONTEXT_FEATURE_DIM = (
    REACTION_VALUE_DIM
    + REACTION_AVAILABILITY_DIM
    + CONTEXT_TEMPORAL_DIM
    + CONTEXT_FLAG_DIM
    + CONTEXT_SESSION_DIM
)

_MAX_GAP_MINUTES = CONTEXT_LOOKBACK_DAYS * 24.0 * 60.0
_MAX_SESSION_DISTANCE = 5.0


def context_contract() -> dict[str, Any]:
    return {
        "version": "causal_prior_news_context_v1",
        "context_size": CONTEXT_SIZE,
        "lookback_days": CONTEXT_LOOKBACK_DAYS,
        "ordering": "oldest_to_newest",
        "strict_predecessor": "prior published_at_utc must be strictly less than current published_at_utc",
        "same_timestamp_policy": "excluded",
        "reaction_values": [
            f"{horizon}_{component}"
            for horizon in HORIZONS
            for component in ("terminal", "high", "low")
        ],
        "reaction_availability": [f"{horizon}_available" for horizon in HORIZONS],
        "availability_rule": "q_live.news_reaction_labels_v2.available_at_utc < current published_at_utc",
        "temporal_features": [
            "log_gap_minutes",
            "calendar_days_ago",
            "market_sessions_ago",
        ],
        "flags": ["same_market_day", "same_market_session"],
        "prior_publication_session_one_hot": list(SESSIONS),
        "missing_value_policy": "zero value with explicit context and horizon availability masks",
        "feature_dim": CONTEXT_FEATURE_DIM,
    }


def normalized_context_metadata(
    *,
    prior_published_at_utc: Any,
    current_published_at_utc: Any,
    prior_publication_session: str,
    current_publication_session: str,
    prior_reaction_session_index: int,
    current_reaction_session_index: int,
) -> np.ndarray:
    prior = parse_published_at_utc(prior_published_at_utc)
    current = parse_published_at_utc(current_published_at_utc)
    if not prior < current:
        raise ValueError("Prior news must be strictly earlier than current news.")
    gap_minutes = (current - prior).total_seconds() / 60.0
    if gap_minutes > _MAX_GAP_MINUTES + 1e-6:
        raise ValueError("Prior news exceeds the configured causal lookback.")
    prior_et = prior.astimezone(_exchange_timezone())
    current_et = current.astimezone(_exchange_timezone())
    calendar_days = max(0, (current_et.date() - prior_et.date()).days)
    sessions_ago = max(0, int(current_reaction_session_index) - int(prior_reaction_session_index))
    same_day = float(prior_et.date() == current_et.date())
    same_session = float(
        same_day
        and str(prior_publication_session).strip().lower()
        == str(current_publication_session).strip().lower()
    )
    session = str(prior_publication_session).strip().lower()
    session_one_hot = [float(session == candidate) for candidate in SESSIONS]
    return np.asarray(
        [
            math.log1p(max(0.0, gap_minutes)) / math.log1p(_MAX_GAP_MINUTES),
            min(float(CONTEXT_LOOKBACK_DAYS), float(calendar_days)) / float(CONTEXT_LOOKBACK_DAYS),
            min(_MAX_SESSION_DISTANCE, float(sessions_ago)) / _MAX_SESSION_DISTANCE,
            same_day,
            same_session,
            *session_one_hot,
        ],
        dtype=np.float32,
    )


def build_context_feature(
    *,
    prior_returns: np.ndarray,
    prior_horizon_codes: Sequence[str],
    available_at_by_horizon: Mapping[str, datetime],
    current_published_at_utc: Any,
    metadata: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Build one prior-news feature using only reactions known before current news."""
    current = parse_published_at_utc(current_published_at_utc)
    returns = np.zeros((len(HORIZONS), REACTION_VALUES_PER_HORIZON), dtype=np.float32)
    availability = np.zeros(len(HORIZONS), dtype=np.float32)
    source_by_horizon = {
        str(code): np.asarray(values, dtype=np.float32)
        for code, values in zip(prior_horizon_codes, np.asarray(prior_returns))
    }
    for horizon_index, horizon in enumerate(HORIZONS):
        values = source_by_horizon.get(horizon)
        available_at = available_at_by_horizon.get(horizon)
        if (
            values is None
            or values.shape != (REACTION_VALUES_PER_HORIZON,)
            or not np.isfinite(values).all()
            or (values < -1.0).any()
            or available_at is None
            or not available_at < current
        ):
            continue
        returns[horizon_index] = values
        availability[horizon_index] = 1.0
    metadata = np.asarray(metadata, dtype=np.float32)
    expected_metadata = CONTEXT_TEMPORAL_DIM + CONTEXT_FLAG_DIM + CONTEXT_SESSION_DIM
    if metadata.shape != (expected_metadata,):
        raise ValueError(
            f"Expected context metadata shape {(expected_metadata,)}, got {metadata.shape}."
        )
    feature = np.concatenate((returns.reshape(-1), availability, metadata)).astype(
        np.float32,
        copy=False,
    )
    if feature.shape != (CONTEXT_FEATURE_DIM,):
        raise AssertionError(f"Unexpected context feature shape {feature.shape}.")
    return feature, availability.astype(np.bool_)


def _exchange_timezone():
    from zoneinfo import ZoneInfo

    return ZoneInfo("America/New_York")
