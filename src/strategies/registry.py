from __future__ import annotations

from pathlib import Path
from typing import Callable

from src.strategies.adaptive_live_trend_rotation.v1.config import (
    AdaptiveLiveTrendRotationConfig as AdaptiveLiveTrendRotationV1Config,
)
from src.strategies.adaptive_live_trend_rotation.v1.presentation import (
    chart_presentation as adaptive_live_trend_rotation_v1_chart_presentation,
)
from src.strategies.adaptive_live_trend_rotation.v1.strategy import AdaptiveLiveTrendRotationStrategy
from src.strategies.break_of_vwap.v1.config import BreakOfVwapConfig as BreakOfVwapV1Config
from src.strategies.break_of_vwap.v1.presentation import chart_presentation as break_of_vwap_v1_chart_presentation
from src.strategies.break_of_vwap.v1.strategy import BreakOfVwapStrategy
from src.strategies.liquidity_pullback_reversal.v1.config import (
    LiquidityPullbackReversalConfig as LiquidityPullbackReversalV1Config,
)
from src.strategies.liquidity_pullback_reversal.v1.presentation import (
    chart_presentation as liquidity_pullback_reversal_v1_chart_presentation,
)
from src.strategies.liquidity_pullback_reversal.v1.strategy import LiquidityPullbackReversalStrategy
from src.strategies.orb_5m_momentum.v1.config import OrbMomentumConfig as OrbMomentumV1Config
from src.strategies.orb_5m_momentum.v1.presentation import chart_presentation as orb_5m_momentum_v1_chart_presentation
from src.strategies.orb_5m_momentum.v1.strategy import OrbFiveMinuteMomentumStrategy
from src.strategies.orb_5m_momentum.v2.config import OrbMomentumConfig as OrbMomentumV2Config
from src.strategies.orb_5m_momentum.v2.presentation import chart_presentation as orb_5m_momentum_v2_chart_presentation
from src.strategies.orb_5m_momentum.v2.strategy import OrbFiveMinuteMomentumV2Strategy
from src.strategies.orb_5m_momentum.v3.config import OrbMomentumConfig as OrbMomentumV3Config
from src.strategies.orb_5m_momentum.v3.presentation import chart_presentation as orb_5m_momentum_v3_chart_presentation
from src.strategies.orb_5m_momentum.v3.strategy import OrbFiveMinuteMomentumV3Strategy
from src.strategies.orb_5m_momentum.v4.config import OrbMomentumConfig as OrbMomentumV4Config
from src.strategies.orb_5m_momentum.v4.presentation import chart_presentation as orb_5m_momentum_v4_chart_presentation
from src.strategies.orb_5m_momentum.v4.strategy import OrbFiveMinuteMomentumV4Strategy
from src.strategies.orb_5m_momentum.v5.config import OrbMomentumConfig as OrbMomentumV5Config
from src.strategies.orb_5m_momentum.v5.presentation import chart_presentation as orb_5m_momentum_v5_chart_presentation
from src.strategies.orb_5m_momentum.v5.strategy import OrbFiveMinuteMomentumV5Strategy
from src.strategies.orb_5m_momentum.v6.config import OrbMomentumConfig as OrbMomentumV6Config
from src.strategies.orb_5m_momentum.v6.presentation import chart_presentation as orb_5m_momentum_v6_chart_presentation
from src.strategies.orb_5m_momentum.v6.strategy import OrbFiveMinuteMomentumV6Strategy
from src.strategies.orb_5m_momentum.v7.config import OrbMomentumConfig as OrbMomentumV7Config
from src.strategies.orb_5m_momentum.v7.presentation import chart_presentation as orb_5m_momentum_v7_chart_presentation
from src.strategies.orb_5m_momentum.v7.strategy import OrbFiveMinuteMomentumV7Strategy

STRATEGIES_ROOT = Path(__file__).resolve().parent


def create_orb_5m_momentum_v1(params: dict | None = None) -> OrbFiveMinuteMomentumStrategy:
    return OrbFiveMinuteMomentumStrategy(OrbMomentumV1Config.from_dict(params))


def default_orb_5m_momentum_v1_params() -> dict:
    return OrbMomentumV1Config().to_dict()


def create_orb_5m_momentum_v2(params: dict | None = None) -> OrbFiveMinuteMomentumV2Strategy:
    return OrbFiveMinuteMomentumV2Strategy(OrbMomentumV2Config.from_dict(params))


def default_orb_5m_momentum_v2_params() -> dict:
    return OrbMomentumV2Config().to_dict()


def create_orb_5m_momentum_v3(params: dict | None = None) -> OrbFiveMinuteMomentumV3Strategy:
    return OrbFiveMinuteMomentumV3Strategy(OrbMomentumV3Config.from_dict(params))


def default_orb_5m_momentum_v3_params() -> dict:
    return OrbMomentumV3Config().to_dict()


def create_orb_5m_momentum_v4(params: dict | None = None) -> OrbFiveMinuteMomentumV4Strategy:
    return OrbFiveMinuteMomentumV4Strategy(OrbMomentumV4Config.from_dict(params))


def default_orb_5m_momentum_v4_params() -> dict:
    return OrbMomentumV4Config().to_dict()


def create_orb_5m_momentum_v5(params: dict | None = None) -> OrbFiveMinuteMomentumV5Strategy:
    return OrbFiveMinuteMomentumV5Strategy(OrbMomentumV5Config.from_dict(params))


def default_orb_5m_momentum_v5_params() -> dict:
    return OrbMomentumV5Config().to_dict()


def create_orb_5m_momentum_v6(params: dict | None = None) -> OrbFiveMinuteMomentumV6Strategy:
    return OrbFiveMinuteMomentumV6Strategy(OrbMomentumV6Config.from_dict(params))


def default_orb_5m_momentum_v6_params() -> dict:
    return OrbMomentumV6Config().to_dict()


def create_orb_5m_momentum_v7(params: dict | None = None) -> OrbFiveMinuteMomentumV7Strategy:
    return OrbFiveMinuteMomentumV7Strategy(OrbMomentumV7Config.from_dict(params))


def default_orb_5m_momentum_v7_params() -> dict:
    return OrbMomentumV7Config().to_dict()


def create_adaptive_live_trend_rotation_v1(params: dict | None = None) -> AdaptiveLiveTrendRotationStrategy:
    return AdaptiveLiveTrendRotationStrategy(AdaptiveLiveTrendRotationV1Config.from_dict(params))


def default_adaptive_live_trend_rotation_v1_params() -> dict:
    return AdaptiveLiveTrendRotationV1Config().to_dict()


def create_break_of_vwap_v1(params: dict | None = None) -> BreakOfVwapStrategy:
    return BreakOfVwapStrategy(BreakOfVwapV1Config.from_dict(params))


def default_break_of_vwap_v1_params() -> dict:
    return BreakOfVwapV1Config().to_dict()


def create_liquidity_pullback_reversal_v1(params: dict | None = None) -> LiquidityPullbackReversalStrategy:
    return LiquidityPullbackReversalStrategy(LiquidityPullbackReversalV1Config.from_dict(params))


def default_liquidity_pullback_reversal_v1_params() -> dict:
    return LiquidityPullbackReversalV1Config().to_dict()


STRATEGY_FACTORIES: dict[tuple[str, str], Callable[[dict | None], object]] = {
    ("adaptive_live_trend_rotation", "v1"): create_adaptive_live_trend_rotation_v1,
    ("break_of_vwap", "v1"): create_break_of_vwap_v1,
    ("liquidity_pullback_reversal", "v1"): create_liquidity_pullback_reversal_v1,
    ("orb_5m_momentum", "v1"): create_orb_5m_momentum_v1,
    ("orb_5m_momentum", "v2"): create_orb_5m_momentum_v2,
    ("orb_5m_momentum", "v3"): create_orb_5m_momentum_v3,
    ("orb_5m_momentum", "v4"): create_orb_5m_momentum_v4,
    ("orb_5m_momentum", "v5"): create_orb_5m_momentum_v5,
    ("orb_5m_momentum", "v6"): create_orb_5m_momentum_v6,
    ("orb_5m_momentum", "v7"): create_orb_5m_momentum_v7,
}

STRATEGY_CONFIG_FACTORIES: dict[tuple[str, str], Callable[[], dict]] = {
    ("adaptive_live_trend_rotation", "v1"): default_adaptive_live_trend_rotation_v1_params,
    ("break_of_vwap", "v1"): default_break_of_vwap_v1_params,
    ("liquidity_pullback_reversal", "v1"): default_liquidity_pullback_reversal_v1_params,
    ("orb_5m_momentum", "v1"): default_orb_5m_momentum_v1_params,
    ("orb_5m_momentum", "v2"): default_orb_5m_momentum_v2_params,
    ("orb_5m_momentum", "v3"): default_orb_5m_momentum_v3_params,
    ("orb_5m_momentum", "v4"): default_orb_5m_momentum_v4_params,
    ("orb_5m_momentum", "v5"): default_orb_5m_momentum_v5_params,
    ("orb_5m_momentum", "v6"): default_orb_5m_momentum_v6_params,
    ("orb_5m_momentum", "v7"): default_orb_5m_momentum_v7_params,
}

STRATEGY_CHART_PRESENTATION_FACTORIES: dict[tuple[str, str], Callable[[], dict]] = {
    ("adaptive_live_trend_rotation", "v1"): adaptive_live_trend_rotation_v1_chart_presentation,
    ("break_of_vwap", "v1"): break_of_vwap_v1_chart_presentation,
    ("liquidity_pullback_reversal", "v1"): liquidity_pullback_reversal_v1_chart_presentation,
    ("orb_5m_momentum", "v1"): orb_5m_momentum_v1_chart_presentation,
    ("orb_5m_momentum", "v2"): orb_5m_momentum_v2_chart_presentation,
    ("orb_5m_momentum", "v3"): orb_5m_momentum_v3_chart_presentation,
    ("orb_5m_momentum", "v4"): orb_5m_momentum_v4_chart_presentation,
    ("orb_5m_momentum", "v5"): orb_5m_momentum_v5_chart_presentation,
    ("orb_5m_momentum", "v6"): orb_5m_momentum_v6_chart_presentation,
    ("orb_5m_momentum", "v7"): orb_5m_momentum_v7_chart_presentation,
}

STRATEGY_DESCRIPTIONS: dict[str, str] = {
    "adaptive_live_trend_rotation": (
        "Ranks live upward momentum every minute, enters the strongest open names, "
        "and rotates capital away from weaker holdings."
    ),
    "break_of_vwap": (
        "Trades long when a liquid symbol reclaims VWAP with momentum confirmation "
        "and exits through the shared risk and trend rules."
    ),
    "liquidity_pullback_reversal": (
        "Looks for liquid symbols that pull back into support, recover with momentum, "
        "and offer asymmetric long continuation."
    ),
    "orb_5m_momentum": (
        "Versioned 5-minute opening-range breakout family, evolving scanner, entry, "
        "profit-reentry, and risk rules incrementally across versions."
    ),
}

DEFAULT_STRATEGY_VERSIONS: dict[str, str] = {
    "adaptive_live_trend_rotation": "v1",
    "break_of_vwap": "v1",
    "liquidity_pullback_reversal": "v1",
    "orb_5m_momentum": "v3",
}


def available_strategies() -> list[str]:
    return sorted(DEFAULT_STRATEGY_VERSIONS)


def available_strategy_versions(name: str) -> list[str]:
    return sorted(version for strategy_name, version in STRATEGY_FACTORIES if strategy_name == name)


def strategy_description(name: str) -> str:
    return STRATEGY_DESCRIPTIONS.get(name, name.replace("_", " ").title())


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
