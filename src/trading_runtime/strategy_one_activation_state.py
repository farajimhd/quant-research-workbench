"""Immutable Strategy 1 activation evidence frozen at the signal boundary.

STRATEGY CREATION RULES: this is an unpublished Strategy 1 state transition.
It consumes one certified completed 100ms activation price and already
admitted as-of V7 levels. It never queries, creates, or persists market data.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Mapping, Sequence

from src.backend.backtest_market_data import market_day_boundary
from .early_squeeze_momentum import freeze_gap


@dataclass(frozen=True, slots=True)
class FrozenActivation:
    ticker: str
    boundary_ms: int
    price_int: int
    average_gap: float | None
    resistance_ids: tuple[str, ...]


def freeze_strategy_one_activation(*, session_date: str, ticker: str,
                                   boundary_ms: int, price_int: int,
                                   admitted_levels: Sequence[Mapping]) -> FrozenActivation:
    if (not ticker or ticker != ticker.upper()
            or type(boundary_ms) is not int or not 0 < boundary_ms <= 57_600_000
            or boundary_ms % 100 or type(price_int) is not int or price_int <= 0
            or not isinstance(admitted_levels, (tuple, list))):
        raise ValueError("Strategy 1 activation lacks typed completed evidence")
    at = market_day_boundary(session_date, boundary_ms)
    rows = {}
    for level in admitted_levels:
        if not isinstance(level, Mapping):
            raise ValueError("Strategy 1 activation V7 level is malformed")
        identity = level.get("unified_level_id")
        if not isinstance(identity, str) or not identity or identity in rows:
            raise ValueError("Strategy 1 activation V7 identity is invalid")
        rows[identity] = level
    gap = freeze_gap(rows, price_int / 10_000, at.timestamp())
    average = gap["average"]
    if average is not None and (not isfinite(average) or average <= 0):
        raise ValueError("Strategy 1 frozen resistance gap is invalid")
    return FrozenActivation(
        ticker, boundary_ms, price_int, average,
        tuple(str(level["unified_level_id"]) for level in gap["levels"]))


class ActivationCatalog:
    """Retain each episode snapshot without allowing a future book to reprice it."""

    def __init__(self) -> None:
        self._by_episode: dict[tuple[str, int], FrozenActivation] = {}

    def add(self, activation: FrozenActivation) -> None:
        if not isinstance(activation, FrozenActivation):
            raise ValueError("Strategy 1 activation snapshot is untyped")
        key = (activation.ticker, activation.boundary_ms)
        if key in self._by_episode:
            raise ValueError("Strategy 1 activation episode was already frozen")
        self._by_episode[key] = activation

    def get(self, ticker: str, episode_start_ms: int) -> FrozenActivation:
        try:
            return self._by_episode[(ticker, episode_start_ms)]
        except KeyError as exc:
            raise ValueError("Strategy 1 candidate lacks frozen activation") from exc
