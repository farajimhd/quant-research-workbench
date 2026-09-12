"""Opt-in prediction input for the next reaction-model strategy.

This adapter uses the same versioned provider as chart /series, without HTTP.
It does not change existing strategies or their order rules. Prepared historical
sessions only: missing artifacts fail explicitly, never use a different book.
"""
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from research.reaction_levels.v1.inference import PredictionRequest, predict_series


class LevelReactionFeed:
    def __init__(self, model_id: str):
        self.model_id = model_id

    def at_close(self, ticker: str, candle_close: int, observed_at: datetime, *, timeframe_seconds: int = 1):
        if observed_at.tzinfo is None:
            raise ValueError('Observation timestamp must be timezone-aware')
        now = observed_at.timestamp()
        if candle_close > now or now-candle_close >= timeframe_seconds:
            raise ValueError('Reaction input must belong to the latest completed candle; no stale or future result')
        local = observed_at.astimezone(ZoneInfo('America/New_York'))
        request = PredictionRequest(model_id=self.model_id, ticker=ticker,
            session_date=local.date(), time_et=local.strftime('%H:%M:%S'))
        result = predict_series(request, [candle_close], timeframe_seconds=timeframe_seconds)
        record = result.pop('records')[0]
        return dict(result, **record)

    def enrich(self, observation, candle_close: int, *, timeframe_seconds: int = 1):
        """Attach full probabilities/provenance to the real strategy observation."""
        record = self.at_close(observation.ticker, candle_close, observation.observed_at,
                               timeframe_seconds=timeframe_seconds)
        return replace(observation, reaction_prediction=record)
