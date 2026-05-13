from __future__ import annotations

from typing import Callable

from src.strategies.orb_5m_momentum.config import OrbMomentumConfig
from src.strategies.orb_5m_momentum.v1.strategy import OrbFiveMinuteMomentumStrategy


def create_orb_5m_momentum_v1(params: dict | None = None) -> OrbFiveMinuteMomentumStrategy:
    return OrbFiveMinuteMomentumStrategy(OrbMomentumConfig.from_dict(params))


STRATEGY_FACTORIES: dict[tuple[str, str], Callable[[dict | None], object]] = {
    ("orb_5m_momentum", "v1"): create_orb_5m_momentum_v1,
}

DEFAULT_STRATEGY_VERSIONS: dict[str, str] = {
    "orb_5m_momentum": "v1",
}


def available_strategies() -> list[str]:
    return sorted(DEFAULT_STRATEGY_VERSIONS)


def available_strategy_versions(name: str) -> list[str]:
    return sorted(version for strategy_name, version in STRATEGY_FACTORIES if strategy_name == name)


def default_strategy_version(name: str) -> str:
    if name not in DEFAULT_STRATEGY_VERSIONS:
        raise KeyError(f"Unknown strategy: {name}")
    return DEFAULT_STRATEGY_VERSIONS[name]


def create_strategy(name: str, params: dict | None = None, version: str | None = None):
    selected_version = version or DEFAULT_STRATEGY_VERSIONS.get(name)
    if selected_version is None:
        raise KeyError(f"Unknown strategy: {name}")
    factory = STRATEGY_FACTORIES.get((name, selected_version))
    if factory is None:
        versions = ", ".join(available_strategy_versions(name)) or "none"
        raise KeyError(f"Unknown strategy version: {name} {selected_version}. Available versions: {versions}")
    return factory(params)
