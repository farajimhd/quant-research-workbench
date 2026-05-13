from __future__ import annotations

from pathlib import Path
from typing import Callable

from src.strategies.orb_5m_momentum.v1.config import OrbMomentumConfig as OrbMomentumV1Config
from src.strategies.orb_5m_momentum.v1.presentation import chart_presentation as orb_5m_momentum_v1_chart_presentation
from src.strategies.orb_5m_momentum.v1.strategy import OrbFiveMinuteMomentumStrategy
from src.strategies.orb_5m_momentum.v2.config import OrbMomentumConfig as OrbMomentumV2Config
from src.strategies.orb_5m_momentum.v2.presentation import chart_presentation as orb_5m_momentum_v2_chart_presentation
from src.strategies.orb_5m_momentum.v2.strategy import OrbFiveMinuteMomentumV2Strategy

STRATEGIES_ROOT = Path(__file__).resolve().parent


def create_orb_5m_momentum_v1(params: dict | None = None) -> OrbFiveMinuteMomentumStrategy:
    return OrbFiveMinuteMomentumStrategy(OrbMomentumV1Config.from_dict(params))


def default_orb_5m_momentum_v1_params() -> dict:
    return OrbMomentumV1Config().to_dict()


def create_orb_5m_momentum_v2(params: dict | None = None) -> OrbFiveMinuteMomentumV2Strategy:
    return OrbFiveMinuteMomentumV2Strategy(OrbMomentumV2Config.from_dict(params))


def default_orb_5m_momentum_v2_params() -> dict:
    return OrbMomentumV2Config().to_dict()


STRATEGY_FACTORIES: dict[tuple[str, str], Callable[[dict | None], object]] = {
    ("orb_5m_momentum", "v1"): create_orb_5m_momentum_v1,
    ("orb_5m_momentum", "v2"): create_orb_5m_momentum_v2,
}

STRATEGY_CONFIG_FACTORIES: dict[tuple[str, str], Callable[[], dict]] = {
    ("orb_5m_momentum", "v1"): default_orb_5m_momentum_v1_params,
    ("orb_5m_momentum", "v2"): default_orb_5m_momentum_v2_params,
}

STRATEGY_CHART_PRESENTATION_FACTORIES: dict[tuple[str, str], Callable[[], dict]] = {
    ("orb_5m_momentum", "v1"): orb_5m_momentum_v1_chart_presentation,
    ("orb_5m_momentum", "v2"): orb_5m_momentum_v2_chart_presentation,
}

DEFAULT_STRATEGY_VERSIONS: dict[str, str] = {
    "orb_5m_momentum": "v2",
}


def available_strategies() -> list[str]:
    return sorted(DEFAULT_STRATEGY_VERSIONS)


def available_strategy_versions(name: str) -> list[str]:
    return sorted(version for strategy_name, version in STRATEGY_FACTORIES if strategy_name == name)


def default_strategy_version(name: str) -> str:
    if name not in DEFAULT_STRATEGY_VERSIONS:
        raise KeyError(f"Unknown strategy: {name}")
    return DEFAULT_STRATEGY_VERSIONS[name]


def default_strategy_params(name: str, version: str | None = None) -> dict:
    selected_version = version or default_strategy_version(name)
    factory = STRATEGY_CONFIG_FACTORIES.get((name, selected_version))
    if factory is None:
        versions = ", ".join(available_strategy_versions(name)) or "none"
        raise KeyError(f"Unknown strategy config version: {name} {selected_version}. Available versions: {versions}")
    return factory()


def strategy_chart_presentation(name: str, version: str | None = None) -> dict:
    selected_version = version or default_strategy_version(name)
    factory = STRATEGY_CHART_PRESENTATION_FACTORIES.get((name, selected_version))
    if factory is None:
        versions = ", ".join(available_strategy_versions(name)) or "none"
        raise KeyError(f"Unknown strategy chart presentation version: {name} {selected_version}. Available versions: {versions}")
    return factory()


def strategy_readme_path(name: str, version: str | None = None) -> Path:
    selected_version = version or default_strategy_version(name)
    version_path = STRATEGIES_ROOT / name / selected_version / "README.md"
    if version_path.exists():
        return version_path
    return STRATEGIES_ROOT / name / "README.md"


def create_strategy(name: str, params: dict | None = None, version: str | None = None):
    selected_version = version or DEFAULT_STRATEGY_VERSIONS.get(name)
    if selected_version is None:
        raise KeyError(f"Unknown strategy: {name}")
    factory = STRATEGY_FACTORIES.get((name, selected_version))
    if factory is None:
        versions = ", ".join(available_strategy_versions(name)) or "none"
        raise KeyError(f"Unknown strategy version: {name} {selected_version}. Available versions: {versions}")
    return factory(params)
