from __future__ import annotations

from typing import Any

from research.mlops.packed_market.time_features import (
    CAUSAL_TIME_FEATURE_DIM,
    CAUSAL_TIME_FEATURE_NAMES,
    SESSION_TIMEZONE,
    encode_causal_time_features,
)


TIME_FEATURE_NAMES = CAUSAL_TIME_FEATURE_NAMES
TIME_FEATURE_DIM = CAUSAL_TIME_FEATURE_DIM


def encode_time_features(
    published_at_utc: Any,
    _publication_session: Any = None,
) -> list[float]:
    return encode_causal_time_features(published_at_utc)


def contract_payload() -> dict[str, object]:
    return {
        "version": "packed_market_v1_publication_time_v1",
        "authority": "research.mlops.packed_market.time_features",
        "timezone": SESSION_TIMEZONE,
        "names": list(TIME_FEATURE_NAMES),
        "dimension": TIME_FEATURE_DIM,
        "semantics": (
            "The exact packed-market V1 causal timestamp features evaluated at "
            "news published_at_utc. UTC cycles and trend use publication time; "
            "session fields use America/New_York. No label, future timestamp, "
            "ticker, or issuer identity is included."
        ),
    }
