from __future__ import annotations

from typing import Callable

from src.strategies.orb_5m_momentum.config import OrbMomentumConfig
from src.strategies.orb_5m_momentum.strategy import OrbFiveMinuteMomentumStrategy


def create_orb_5m_momentum(params: dict | None = None) -> OrbFiveMinuteMomentumStrategy:
    return OrbFiveMinuteMomentumStrategy(OrbMomentumConfig.from_dict(params))


STRATEGIES: dict[str, Callable[[dict | None], object]] = {
    "orb_5m_momentum": create_orb_5m_momentum,
}


def available_strategies() -> list[str]:
    return sorted(STRATEGIES)


def create_strategy(name: str, params: dict | None = None):
    if name not in STRATEGIES:
        raise KeyError(f"Unknown strategy: {name}")
    return STRATEGIES[name](params)
