from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, time as clock_time, timezone
from enum import StrEnum
from math import exp, floor
from typing import Any, Mapping
from uuid import uuid4
from zoneinfo import ZoneInfo

from src.request_context import causal_identity

from src.trading_runtime.execution_policies import (
    AddProtectionPolicy,
    ExecutionEnvelope,
    ExecutionPolicy,
    ExecutionPolicyName,
    PartialFillPolicy,
    ProfitPocketTransition,
    ProtectionProfile,
    ProtectionSlice,
    StopOrderType,
    StopRule,
    StopRuleType,
    StructuralAnchor,
    TrailingRule,
    TrailingRuleType,
    execution_policy_from_payload,
)
from src.trading_runtime.signals import CapitalRequest, StrategyEvaluation, StrategyIntent, StrategySignal
from src.market_engine.events import MarketEvent
from src.trading_runtime.strategy_campaign import StrategyCampaignOrchestrator


STRATEGY_ID = "long-momentum-campaign"
STRATEGY_REVISION = 17

RULE_COMPARATORS = {
    "above_by_bps",
    "equals",
    "greater_or_equal",
    "greater_than",
    "is_true",
    "less_or_equal",
    "less_than",
}
NEW_YORK = ZoneInfo("America/New_York")

STRATEGY_INPUT_SUMMARIES = {
    "market.last_price": "The most recent causally available market price used to compare the instrument with structural levels, indicators, and protection boundaries.",
    "market.previous_close": "The completed prior regular-session close used as a stable reference for gaps, returns, and price-location rules.",
    "market.previous_high": "The completed prior regular-session high used as a reference level for breakout and continuation rules.",
    "indicator.structure.swing_high": "The latest QMD-confirmed structural high for the selected timeframe, used as resistance and breakout evidence.",
    "indicator.structure.swing_low": "The latest QMD-confirmed structural low for the selected timeframe, used as support and invalidation evidence.",
    "indicator.structure.unified_resistance_upper": "The upper boundary of the nearest causally available resistance zone from QMD's persistent Unified Structural Level Book.",
    "indicator.structure.unified_support_lower": "The lower boundary of the nearest causally available support zone from QMD's persistent Unified Structural Level Book.",
    "indicator.structure.bullish_choch": "A QMD structure event that becomes true when price action confirms a bullish change of character on the selected timeframe.",
    "indicator.structure.bearish_choch": "A QMD structure event that becomes true when price action confirms a bearish change of character on the selected timeframe.",
    "indicator.vwap.value": "The selected timeframe's volume-weighted average price, used to judge whether trading occurs above, below, or through accepted value.",
    "indicator.vwap.slope": "The rate and direction of VWAP movement in basis points per second, used to distinguish rising, flat, and falling value.",
    "indicator.flow_structure.score": "QMD's signed composite of directional order flow and market structure, used to rank bullish versus bearish alignment.",
    "indicator.flow_structure.confidence": "QMD's confidence in the current flow-structure composite, used to require stronger evidence before acting on its score.",
    "indicator.macd.line": "The MACD fast-minus-slow momentum line for the selected timeframe, used to measure directional momentum.",
    "indicator.macd.signal": "The smoothed MACD signal line for the selected timeframe, used as the comparison baseline for momentum confirmation.",
    "indicator.macd.histogram": "The distance between the MACD and signal lines, used to measure whether momentum is strengthening or weakening.",
    "signal.price_volume_expansion.score": "A scored QMD event measuring whether price movement is confirmed by expanding trading activity and volume.",
    "signal.vwap_transition.score": "A scored QMD event measuring the strength of a price transition through VWAP and subsequent acceptance or rejection.",
    "signal.flow_price_divergence.score": "A scored QMD event measuring disagreement between directional order flow and observed price movement.",
    "signal.liquidity_dislocation.score": "A scored QMD event measuring abnormal liquidity loss, spread stress, or order-book displacement.",
    "signal.company_news.score": "A point-in-time company-news signal summarizing the direction and strength of newly available issuer information.",
    "signal.sec_filing.score": "A point-in-time SEC filing signal summarizing the direction and strength of newly available issuer disclosures.",
    "signal.news_labeled": "True only for a causally available news event that Text Intelligence successfully labeled under its validated contract; service health or an unlabeled event never turns it on.",
    "signal.sec_labeled": "True only for a causally available SEC event that Text Intelligence successfully labeled under its validated contract; service health or an unlabeled filing never turns it on.",
}


def strategy_input_catalog() -> list[dict[str, Any]]:
    """Code-owned strategy inputs and their authoritative runtime projections."""

    return [
        _input("market.last_price", "Last price", "Market", "qmd-derived", "price", "price", ["100ms", "1s", "5s", "10s", "30s", "1m", "5m"]),
        _input("market.previous_close", "Previous close", "Market", "qmd-reference", "previous_close", "price", ["session"]),
        _input("market.previous_high", "Previous high", "Market", "qmd-reference", "previous_high", "price", ["session"]),
        _input("indicator.structure.swing_high", "Confirmed swing high", "QMD indicator", "qmd", "swing_high", "price", ["100ms", "1s", "5s", "10s", "30s", "1m", "5m"]),
        _input("indicator.structure.swing_low", "Confirmed swing low", "QMD indicator", "qmd", "swing_low", "price", ["100ms", "1s", "5s", "10s", "30s", "1m", "5m"]),
        _input("indicator.structure.unified_resistance_upper", "Unified resistance upper", "QMD level book", "qmd", "structural_resistance_upper", "price", ["100ms", "1s", "5s", "10s", "30s", "1m", "5m"]),
        _input("indicator.structure.unified_support_lower", "Unified support lower", "QMD level book", "qmd", "structural_support_lower", "price", ["100ms", "1s", "5s", "10s", "30s", "1m", "5m"]),
        _input("indicator.structure.bullish_choch", "Bullish change of character", "QMD indicator", "qmd", "bullish_choch", "boolean", ["100ms", "1s", "5s", "10s", "30s", "1m", "5m"]),
        _input("indicator.structure.bearish_choch", "Bearish change of character", "QMD indicator", "qmd", "bearish_choch", "boolean", ["100ms", "1s", "5s", "10s", "30s", "1m", "5m"]),
        _input("indicator.vwap.value", "VWAP", "QMD indicator", "qmd", "vwap", "price", ["100ms", "1s", "5s", "10s", "30s", "1m", "5m"], parameter="value"),
        _input("indicator.vwap.slope", "VWAP slope", "QMD indicator", "qmd", "vwap_slope_bps_per_second", "bps_per_second", ["100ms", "1s", "5s", "10s", "30s", "1m", "5m"], parameter="slope_bps_per_second"),
        _input("indicator.flow_structure.score", "Flow-structure score", "QMD indicator", "qmd", "qmd_score", "score", ["100ms"], parameter="score"),
        _input("indicator.flow_structure.confidence", "Flow-structure confidence", "QMD indicator", "qmd", "qmd_confidence", "score", ["100ms"], parameter="confidence"),
        _input("indicator.macd.line", "MACD line", "Market indicator", "qmd", "macd_line", "number", ["1s", "5s", "10s", "30s", "1m", "5m"], parameter="line"),
        _input("indicator.macd.signal", "MACD signal", "Market indicator", "qmd", "macd_signal", "number", ["1s", "5s", "10s", "30s", "1m", "5m"], parameter="signal"),
        _input("indicator.macd.histogram", "MACD histogram", "Market indicator", "qmd", "macd_histogram", "number", ["1s", "5s", "10s", "30s", "1m", "5m"], parameter="histogram"),
        _input("signal.price_volume_expansion.score", "Price-volume expansion score", "QMD market signal", "qmd", "price_volume_expansion_score", "score", ["1s", "10s", "30s", "1m"], parameter="score"),
        _input("signal.vwap_transition.score", "VWAP transition score", "QMD market signal", "qmd", "vwap_transition_score", "score", ["1s", "10s", "30s", "1m"], parameter="score"),
        _input("signal.flow_price_divergence.score", "Flow-price divergence score", "QMD market signal", "qmd", "flow_price_divergence_score", "score", ["100ms"], parameter="score"),
        _input("signal.liquidity_dislocation.score", "Liquidity dislocation score", "QMD market signal", "qmd", "liquidity_dislocation_score", "score", ["100ms"], parameter="score"),
        _input("signal.company_news.score", "Company news score", "News signal", "news", "news_score", "score", ["event"], parameter="score"),
        _input("signal.sec_filing.score", "SEC filing score", "SEC signal", "sec", "sec_filing_score", "score", ["event"], parameter="score"),
        _input("signal.news_labeled", "News labeled", "Text Intelligence signal", "text-intelligence", "news_labeled", "boolean", ["event"], parameter="labeled"),
        _input("signal.sec_labeled", "SEC labeled", "Text Intelligence signal", "text-intelligence", "sec_labeled", "boolean", ["event"], parameter="labeled"),
    ]


def default_entry_decision_rules(parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    entry = dict((parameters or {}).get("entry") or {})
    breakout_timeframe = str(entry.get("breakout_timeframe") or "1s")
    breakout_source = {
        "previous_close": "market.previous_close",
        "previous_high": "market.previous_high",
        "confirmed_swing_high": "indicator.structure.swing_high",
        "bullish_choch": "indicator.structure.swing_high",
    }.get(str(entry.get("breakout_reference") or ""), "indicator.structure.swing_high")
    return {
        "trigger": {
            "operator": "any",
            "groups": [
                _rule_group("break-structure", "Break configured structure", "all", [
                    _condition("price-over-structure", "market.last_price", breakout_timeframe, "above_by_bps", right_source_id=breakout_source, right_timeframe=breakout_timeframe, value=float(entry.get("breakout_buffer_bps") or 5)),
                ]),
                _rule_group("break-vwap", "Break VWAP", "all", [
                    _condition("price-over-vwap", "market.last_price", breakout_timeframe, "above_by_bps", right_source_id="indicator.vwap.value", right_timeframe=breakout_timeframe, value=float(entry.get("breakout_buffer_bps") or 5)),
                ]),
                _rule_group("bullish-choch", "Bullish change of character", "all", [
                    _condition("bullish-choch", "indicator.structure.bullish_choch", breakout_timeframe, "is_true"),
                ]),
                _rule_group("price-volume-expansion", "Price-volume expansion", "all", [
                    _condition("price-volume-expansion-score", "signal.price_volume_expansion.score", "1s", "greater_or_equal", value=float(entry.get("price_expansion_minimum_score") or 0.65)),
                ]),
                _rule_group("vwap-transition", "VWAP transition", "all", [
                    _condition("vwap-transition-score", "signal.vwap_transition.score", "10s", "greater_or_equal", value=float(entry.get("vwap_transition_minimum_score") or 0.6)),
                ]),
                _rule_group("company-news", "Company news", "all", [
                    _condition("company-news-score", "signal.company_news.score", "event", "greater_or_equal", value=float(entry.get("news_minimum_score") or 0.7)),
                ]),
            ],
        },
        "confirmation": {
            "operator": "all",
            "groups": [
                _rule_group("qmd-alignment", "QMD flow and structure", "all", [
                    _condition("qmd-score", "indicator.flow_structure.score", "100ms", "greater_or_equal", value=float(dict(entry.get("qmd") or {}).get("minimum_score") or 0.3)),
                    _condition("qmd-confidence", "indicator.flow_structure.confidence", "100ms", "greater_or_equal", value=float(dict(entry.get("qmd") or {}).get("minimum_confidence") or 0.5)),
                ]),
                _rule_group("vwap-confirmation", "Price accepted above rising VWAP", "all", [
                    _condition("price-above-vwap", "market.last_price", "5s", "greater_or_equal", right_source_id="indicator.vwap.value", right_timeframe="5s"),
                    _condition("vwap-rising", "indicator.vwap.slope", "5s", "greater_or_equal", value=float(dict(entry.get("vwap") or {}).get("minimum_slope_bps_per_second") or 0)),
                ]),
                _rule_group("macd-confirmation", "MACD confirms momentum", "all", [
                    _condition("macd-line-over-signal", "indicator.macd.line", "5s", "greater_or_equal", right_source_id="indicator.macd.signal", right_timeframe="5s"),
                    _condition("macd-line-positive", "indicator.macd.line", "5s", "greater_than", value=0),
                    _condition("macd-signal-positive", "indicator.macd.signal", "5s", "greater_than", value=0),
                    _condition("macd-positive-histogram", "indicator.macd.histogram", "5s", "greater_than", value=0),
                ]),
            ],
        },
        "veto": {
            "operator": "any",
            "groups": [
                _rule_group("flow-price-divergence", "Flow-price divergence", "all", [
                    _condition("flow-price-divergence-score", "signal.flow_price_divergence.score", "100ms", "greater_or_equal", value=float(dict(entry.get("veto") or {}).get("flow_price_divergence") or 0.75)),
                ]),
                _rule_group("liquidity-dislocation", "Liquidity dislocation", "all", [
                    _condition("liquidity-dislocation-score", "signal.liquidity_dislocation.score", "100ms", "greater_or_equal", value=float(dict(entry.get("veto") or {}).get("liquidity_dislocation") or 0.75)),
                ]),
            ],
        },
    }


def _rule_stage_timeframes(stage: dict[str, Any]) -> set[str]:
    timeframes: set[str] = set()
    for group in stage.get("rule_sets") or stage.get("groups") or []:
        if not bool(group.get("enabled", True)):
            continue
        for condition in group.get("conditions") or []:
            for side in ("left", "right"):
                value = _condition_interval_expression(
                    condition.get(f"{side}_interval")
                    or condition.get(f"{side}_timeframe")
                )
                if value not in {"", "event", "session"}:
                    timeframes.add(value)
    return timeframes


def strategy_rule_timeframes(parameters: dict[str, Any]) -> set[str]:
    """Return every derived-data timeframe referenced by active lifecycle rules."""

    timeframes: set[str] = set()
    for stage in dict(parameters.get("entry_rules") or {}).values():
        if isinstance(stage, dict):
            timeframes.update(_rule_stage_timeframes(stage))
    phase_policy = dict(parameters.get("phase_policy") or {})
    for stage in dict(dict(phase_policy.get("reentry") or {}).get("rules") or {}).values():
        if isinstance(stage, dict):
            timeframes.update(_rule_stage_timeframes(stage))
    for step in dict(phase_policy.get("initial_entry") or {}).get("add_steps") or []:
        if bool(step.get("enabled", True)):
            timeframes.update(_rule_stage_timeframes(dict(step.get("rules") or {})))
    for route in dict(phase_policy.get("exit") or {}).get("rule_sets") or []:
        if bool(route.get("enabled", True)):
            timeframes.update(_rule_stage_timeframes(dict(route.get("rules") or {})))
    return timeframes or {
        str(dict(parameters.get("entry") or {}).get("breakout_timeframe") or "1s")
    }


class AssignmentStatus(StrEnum):
    DISABLED = "disabled"
    WATCHING = "watching"
    ENTRY_PENDING = "entry_pending"
    EXIT_PENDING = "exit_pending"
    MANAGING = "managing"
    REENTRY_COOLDOWN = "reentry_cooldown"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class StrategyPermissions:
    observe: bool = True
    enter: bool = False
    add: bool = False
    reduce: bool = True
    exit: bool = True
    reenter: bool = False

    def payload(self) -> dict[str, bool]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class StrategyAssignment:
    assignment_id: str
    strategy_id: str
    strategy_revision: int
    account_id: str
    ticker: str
    conid: int
    status: AssignmentStatus
    permissions: StrategyPermissions
    parameters: dict[str, Any]
    state: dict[str, Any] = field(default_factory=dict)
    source: str = "order_entry"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.assignment_id or not self.account_id or not self.ticker:
            raise ValueError("Strategy assignment identity, account, and ticker are required")
        if not self.strategy_id or self.strategy_revision <= 0:
            raise ValueError("Strategy assignment requires a versioned Strategy identity")
        if self.conid <= 0:
            raise ValueError("Strategy assignment conid must be positive")

    def payload(self) -> dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status.value
        result["created_at"] = self.created_at.isoformat()
        result["updated_at"] = self.updated_at.isoformat()
        return result


@dataclass(frozen=True, slots=True)
class StrategyObservation:
    ticker: str
    observed_at: datetime
    price: float
    bid: float = 0.0
    ask: float = 0.0
    position_quantity: float = 0.0
    average_price: float = 0.0
    previous_close: float | None = None
    previous_high: float | None = None
    swing_high: float | None = None
    swing_low: float | None = None
    structural_support_price: float | None = None
    structural_support_lower: float | None = None
    structural_support_upper: float | None = None
    structural_support_strength: float = 0.0
    structural_support_confidence: float = 0.0
    structural_resistance_price: float | None = None
    structural_resistance_lower: float | None = None
    structural_resistance_upper: float | None = None
    structural_resistance_strength: float = 0.0
    structural_resistance_confidence: float = 0.0
    structural_support_levels: tuple[dict[str, Any], ...] = ()
    structural_resistance_levels: tuple[dict[str, Any], ...] = ()
    structural_up_probability: float = 0.5
    structure_event: str = ""
    structure_direction: str = ""
    vwap: float | None = None
    vwap_slope_bps_per_second: float = 0.0
    macd_line: float | None = None
    macd_signal: float | None = None
    macd_histogram: float | None = None
    qmd_score: float = 0.0
    qmd_confidence: float = 0.0
    qmd_bias: str = "neutral"
    price_volume_expansion_score: float = 0.0
    vwap_transition_score: float = 0.0
    news_score: float = 0.0
    sec_filing_score: float = 0.0
    # TODO(text-intelligence-label-events): Populate these flags only after the
    # canonical News and SEC label-event contracts are finalized. The adapter
    # must require validated point-in-time label evidence, preserve its event id
    # in source_signal_ids, and leave the flag false for unavailable, failed, or
    # merely service-healthy states.
    news_labeled: bool = False
    sec_labeled: bool = False
    flow_price_divergence_score: float = 0.0
    liquidity_dislocation_score: float = 0.0
    volatility: float = 0.0
    acceleration: float = 0.0
    upper_luld_price: float | None = None
    market_open: bool = True
    manual_entry_request: bool = False
    force_entry: bool = False
    evaluation_events: tuple[str, ...] = ("indicator_update",)
    changed_source_ids: tuple[str, ...] = ()
    source_signal_ids: tuple[str, ...] = ()
    source_timeframe: str = ""
    source_values: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("Strategy observation time must be timezone-aware")
        if self.price <= 0:
            raise ValueError("Strategy observation price must be positive")

    def payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class StrategyEngineResult:
    evaluation: StrategyEvaluation
    state: dict[str, Any]
    status: AssignmentStatus
    evaluation_payload: dict[str, Any]


def long_momentum_strategy_definition() -> dict[str, Any]:
    """Canonical built-in definition and optimization space for the first post-refactor strategy."""
    parameters = default_long_momentum_parameters()
    return {
        "strategy_id": STRATEGY_ID,
        "revision": STRATEGY_REVISION,
        "name": "Long Momentum Campaign",
        "implementation": "src.trading_runtime.strategy_engine.LongMomentumStrategyEngine",
        "automatic": True,
        "enabled": True,
        "config": {
            "direction": "single_side",
            "supported_sides": ["long", "short"],
            "parameters": parameters,
            "input_catalog": strategy_input_catalog(),
            "parameter_space": {
                "protection.stop.method": ["structure", "volatility", "hybrid"],
                "protection.stop.volatility_multiple": [0.75, 1.0, 1.25, 1.5, 2.0],
                "profit_pocket.trigger": ["acceleration_slowdown", "favorable_move_pct", "volatility_multiple"],
                "profit_pocket.quantity_fraction": [1.0],
                "reentry.cooldown_ms": [0, 500, 1000, 5000, 30000],
                "execution.entry_urgency": ["patient", "regular", "urgent", "very_urgent"],
                "execution.exit_urgency": ["urgent", "very_urgent"],
            },
            "taxonomy": {
                "schema_version": 3,
                "indicators": [
                    {"key": "flow_structure_composite", "producer": "qmd", "capability_key": "flow_structure_composite", "timeframe": "100ms", "role": "confirmation", "required": False, "maximum_age_ms": 300, "minimum_score": 0.3, "minimum_confidence": 0.5},
                    {"key": "vwap", "producer": "qmd", "capability_key": "momentum_core", "timeframe": "5s", "role": "confirmation", "required": False, "maximum_age_ms": 6000},
                    {"key": "macd", "producer": "qmd", "capability_key": "momentum_core", "timeframe": "5s", "role": "confirmation", "required": False, "maximum_age_ms": 6000},
                    {"key": "generic_structure", "producer": "qmd", "capability_key": "qmd_generic_structure", "timeframe": "1s", "role": "trigger", "required": True, "maximum_age_ms": 2000},
                ],
                "signals": [
                    {"key": "price_volume_expansion", "producer": "qmd", "capability_key": "price_volume_expansion", "timeframe": "1s", "role": "trigger", "required": False, "maximum_age_ms": 2000, "minimum_score": 0.65},
                    {"key": "vwap_transition", "producer": "qmd", "capability_key": "vwap_transition", "timeframe": "10s", "role": "trigger", "required": False, "maximum_age_ms": 11000, "minimum_score": 0.6},
                    {"key": "flow_price_divergence", "producer": "qmd", "capability_key": "flow_price_divergence", "timeframe": "100ms", "role": "veto", "required": False, "maximum_age_ms": 500},
                    {"key": "liquidity_dislocation", "producer": "qmd", "capability_key": "liquidity_dislocation", "timeframe": "100ms", "role": "veto", "required": False, "maximum_age_ms": 500},
                    {"key": "company_news", "producer": "news_gateway", "role": "trigger", "required": False, "maximum_age_ms": 60000, "minimum_score": 0.7},
                    {"key": "sec_filing", "producer": "sec_gateway", "role": "trigger", "required": False, "maximum_age_ms": 60000},
                ],
                "allow_developing_inputs": False,
                "presentation": {
                    "label": "Long campaign",
                    "show_entries": True,
                    "show_adds": True,
                    "show_reductions": True,
                    "show_exits": True,
                    "show_holds": False,
                    "show_waits": False,
                    "show_invalidation": True,
                    "show_confidence": True,
                },
            },
        },
    }


def default_long_momentum_parameters() -> dict[str, Any]:
    parameters = {
        "entry": {
            "breakout_timeframe": "1s",
            "breakout_reference": "confirmed_swing_high",
            "breakout_buffer_bps": 5.0,
            "minimum_confirmation_score": 0.55,
            "news_minimum_score": 0.7,
            "price_expansion_minimum_score": 0.65,
            "vwap_transition_minimum_score": 0.6,
            "qmd": {"minimum_score": 0.3, "minimum_confidence": 0.5},
            "vwap": {"minimum_slope_bps_per_second": 0.0},
            "macd": {"require_positive_histogram": True},
            "use_unified_structure_levels": False,
            "structural_level": {
                "minimum_salience": 0.45,
                "minimum_confidence": 0.50,
                "minimum_reaction_probability": 0.50,
                "acceptance_buffer_bps": 0.0,
                "acceptance_hold_ms": 15_000,
            },
            "veto": {"flow_price_divergence": 0.75, "liquidity_dislocation": 0.75},
        },
        "liquidity_admission": {
            "enabled": False,
            "latched": True,
            "minimum_price": 2.0,
            "maximum_price": 50.0,
            "minimum_session_dollar_volume": 1_000_000.0,
            "minimum_session_share_volume": 100_000.0,
            "minimum_trade_rate_10s": 1.0,
            "minimum_trade_rate_60s": 0.5,
            "maximum_spread_bps": 60.0,
        },
        "sizing": {
            "request_mode": "fixed_quantity",
            "request_value": 100.0,
            "initial_quantity": 100.0,
            "add_fraction": 0.5,
        },
        "protection": {
            "stop": {
                "method": "hybrid",
                "structure_buffer_bps": 8.0,
                "volatility_multiple": 1.25,
                "maximum_risk_pct": 6.0,
                "prefer_closer_hybrid": False,
            },
            "trailing": {
                "enabled": True,
                "activation_gain_pct": 8.0,
                "distance_volatility_multiple": 2.0,
                "minimum_distance_bps": 50.0,
            },
            "profit_ladder": {
                "enabled": True,
                "risk_multiples": [0.5, 1.0, 1.618, 2.618, 4.236],
                "maximum_targets": 5,
                "minimum_spacing_bps": 10.0,
                "minimum_level_strength": 0.30,
                "minimum_level_confidence": 0.50,
                "minimum_reaction_probability": 0.0,
                "minimum_reversal_probability": 0.0,
                "minimum_composite_score": 0.0,
                "premarket_maximum_gain_pct": 200.0,
            },
            "luld_profit_target": {
                "enabled": True,
                "buffer_bps": 25.0,
                "minimum_tick_offset_count": 2,
                "tick_size": 0.01,
                "include_current_spread": True,
                "require_authoritative_band": True,
            },
        },
        "add": {"enabled": True, "trigger": "bullish_choch_after_pullback", "maximum_adds": 2},
        "profit_pocket": {
            "enabled": True,
            "trigger": "acceleration_slowdown",
            "minimum_gain_pct": 0.75,
            "acceleration_slowdown_threshold": 0.15,
            "volatility_multiple": 1.5,
            "quantity_fraction": 1.0,
            "minimum_remaining_quantity": 1.0,
        },
        "reentry": {
            "enabled": True,
            "cooldown_ms": 0,
            "maximum_attempts": 3,
            "unlimited_attempts": False,
            "require_new_confirmation": True,
            "target_replenishment": {
                "enabled": False,
                "minimum_pullback_atr_multiple": 0.50,
                "minimum_pullback_bps": 25.0,
                "support_buffer_bps": 10.0,
            },
        },
        "momentum_management": {
            "downside_loss_guard": {
                "enabled": False,
                "timeframe": "1s",
                "bearish_choch": True,
                "macd_closed": True,
                "below_vwap": True,
            },
            "failure_to_extend": {
                "enabled": False,
                "minimum_gain_pct": 0.75,
                "minimum_extension_bps": 5.0,
                "stalled_for_ms": 3_000,
                "maximum_flow_structure_score": 0.15,
                "minimum_flow_price_divergence_score": 0.55,
                "position_fraction": 0.50,
                "maximum_uses": 1,
            },
            "qmd_exhaustion": {
                "enabled": False,
                "active_after_ms": 1_000,
                "maximum_flow_structure_score": -0.10,
                "minimum_confidence": 0.55,
                "minimum_flow_price_divergence_score": 0.60,
            },
            "structure_failure": {
                "enabled": False,
                "active_after_ms": 1_000,
                "buffer_bps": 5.0,
                "require_higher_low": True,
            },
            "macd_backstop": {
                "enabled": False,
                "active_after_ms": 5_000,
                "closed_for_ms": 1_000,
                "timeframe": "1s",
            },
        },
        "final_exit": {
            "qmd_score": -0.35,
            "qmd_confidence": 0.55,
            "require_macd_bearish": True,
            "exit_on_failed_breakout": True,
        },
        "execution": {
            "entry_urgency": "urgent",
            "exit_urgency": "very_urgent",
            "limit_offset_bps": 5.0,
            "tick_size": 0.01,
        },
    }
    parameters["exit_routes"] = default_exit_routes(parameters["final_exit"])
    parameters["entry_rules"] = default_entry_decision_rules(parameters)
    parameters["structural_entry"] = {
        "enabled": bool(parameters["entry"].get("use_unified_structure_levels", False)),
        **dict(parameters["entry"].get("structural_level") or {}),
    }
    parameters.pop("entry", None)
    return parameters


def default_exit_routes(final_exit: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    settings = dict(final_exit or {})
    return [
        {
            "route_id": "protective-stop",
            "name": "Protective stop",
            "category": "protective",
            "mechanism": "protective_stop",
            "action": "close",
            "priority": 100,
            "enabled": True,
            "protected": True,
            "summary": "Close immediately when shared protection is breached.",
            "settings": {},
        },
        {
            "route_id": "failed-breakout",
            "name": "Failed breakout",
            "category": "strategic",
            "mechanism": "failed_breakout",
            "action": "close",
            "priority": 80,
            "enabled": bool(settings.get("exit_on_failed_breakout", True)),
            "protected": False,
            "summary": "Exit when price loses the structure that justified entry.",
            "settings": {},
        },
        {
            "route_id": "bearish-momentum",
            "name": "Bearish momentum confirmation",
            "category": "strategic",
            "mechanism": "bearish_qmd_macd",
            "action": "close",
            "priority": 70,
            "enabled": bool(settings.get("bearish_momentum_enabled", True)),
            "protected": False,
            "summary": "Exit when adverse QMD evidence and optional MACD confirmation pass.",
            "settings": {
                "qmd_score": float(settings.get("qmd_score") or -0.35),
                "qmd_confidence": float(settings.get("qmd_confidence") or 0.55),
                "require_macd_bearish": bool(
                    settings.get("require_macd_bearish", True)
                ),
            },
        },
    ]


def resolve_long_momentum_parameters(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    parameters = _deep_merge(default_long_momentum_parameters(), dict(overrides or {}))
    execution = dict(parameters.get("execution") or {})
    execution.pop("time_in_force", None)
    execution.pop("outside_rth", None)
    parameters["execution"] = execution
    side = str(dict(parameters.get("strategy_behavior") or {}).get("side") or "long")
    if side not in {"long", "short"}:
        raise ValueError("Strategy side must be long or short")
    behavior = dict(parameters.get("strategy_behavior") or {})
    for key in ("entry_cutoff_time", "flatten_time"):
        value = str(behavior.get(key) or "").strip()
        if value:
            try:
                datetime.strptime(value, "%H:%M:%S")
            except ValueError as exc:
                raise ValueError(f"Strategy {key} must use HH:MM:SS New York time") from exc
    if parameters["protection"]["stop"]["method"] not in {"structure", "volatility", "hybrid"}:
        raise ValueError("Unsupported protective stop method")
    sizing = parameters["sizing"]
    if sizing["request_mode"] not in {
        "fixed_quantity",
        "mandate_fraction",
        "risk_fraction",
        "all_available",
    }:
        raise ValueError("Unsupported strategy capital request mode")
    if float(sizing["request_value"]) < 0:
        raise ValueError("Strategy capital request value cannot be negative")
    if float(sizing["initial_quantity"]) <= 0:
        raise ValueError("Strategy fallback initial quantity must be positive")
    if not 0 < float(sizing["add_fraction"]) <= 1:
        raise ValueError("Add fraction must be between 0 and 1")
    if not 0 < float(parameters["profit_pocket"]["quantity_fraction"]) <= 1:
        raise ValueError("Profit-pocket quantity fraction must be between 0 and 1")
    if parameters["profit_pocket"]["trigger"] not in {"acceleration_slowdown", "favorable_move_pct", "volatility_multiple"}:
        raise ValueError("Unsupported profit-pocket trigger")
    if int(parameters["reentry"]["cooldown_ms"]) < 0:
        raise ValueError("Re-entry cooldown cannot be negative")
    required_signal_stream_id = str(
        parameters["reentry"].get("require_new_signal_stream_id") or ""
    ).strip()
    if required_signal_stream_id and not required_signal_stream_id.replace("-", "").isalnum():
        raise ValueError("Re-entry signal stream id is invalid")
    supported_urgencies = {
        "passive_limit",
        "aggressive_limit",
        "market",
        "patient",
        "regular",
        "urgent",
        "very_urgent",
    }
    if parameters["execution"]["entry_urgency"] not in supported_urgencies:
        raise ValueError("Unsupported entry urgency")
    if parameters["execution"]["exit_urgency"] not in supported_urgencies:
        raise ValueError("Unsupported exit urgency")
    if float(parameters["execution"]["tick_size"]) <= 0:
        raise ValueError("Execution tick size must be positive")
    _validate_exit_routes(list(parameters.get("exit_routes") or []))
    _validate_entry_rules(dict(parameters.get("entry_rules") or {}))
    phase_policy = dict(parameters.get("phase_policy") or {})
    if phase_policy:
        for phase_name in ("initial_entry", "manage", "reentry", "exit"):
            mode = str(dict(phase_policy.get(phase_name) or {}).get("mode") or "automatic")
            if mode not in {"automatic", "manual"}:
                raise ValueError(f"Unsupported strategy {phase_name} mode")
        for phase_name in ("initial_entry", "reentry"):
            phase = dict(phase_policy.get(phase_name) or {})
            if phase:
                _validate_phase_capital_request(
                    dict(phase.get("capital_request") or {})
                )
                _validate_phase_order_intent(dict(phase.get("order_intent") or {}))
        for step in dict(phase_policy.get("initial_entry") or {}).get("add_steps") or []:
            _validate_phase_capital_request(dict(step.get("capital_request") or {}))
            _validate_phase_order_intent(dict(step.get("order_intent") or {}))
    return parameters


def _rule_stage_source_dependencies(
    stage: dict[str, Any],
) -> set[tuple[str, str]]:
    dependencies: set[tuple[str, str]] = set()
    for group in stage.get("rule_sets") or stage.get("groups") or []:
        if not bool(group.get("enabled", True)):
            continue
        for condition in group.get("conditions") or []:
            for side in ("left", "right"):
                source_id = str(condition.get(f"{side}_source_id") or "")
                if source_id:
                    dependencies.add(
                        (
                            source_id,
                            _condition_interval_expression(
                                condition.get(f"{side}_interval")
                                or condition.get(f"{side}_timeframe")
                            ),
                        )
                    )
    return dependencies


def _decision_rules_source_dependencies(
    rules: dict[str, Any],
) -> set[tuple[str, str]]:
    dependencies: set[tuple[str, str]] = set()
    for stage in rules.values():
        if isinstance(stage, dict):
            dependencies.update(_rule_stage_source_dependencies(stage))
    return dependencies


def _phase_is_automatic(parameters: dict[str, Any], phase_name: str) -> bool:
    return str(
        dict(dict(parameters.get("phase_policy") or {}).get(phase_name) or {}).get("mode")
        or "automatic"
    ) == "automatic"


def _active_rule_source_dependencies(
    parameters: dict[str, Any],
    observation: StrategyObservation,
    *,
    reentries: int,
) -> set[tuple[str, str]]:
    phase_policy = dict(parameters.get("phase_policy") or {})
    if not observation.position_quantity:
        if reentries:
            if not _phase_is_automatic(parameters, "reentry"):
                return set()
            return _decision_rules_source_dependencies(
                dict(dict(phase_policy.get("reentry") or {}).get("rules") or {})
            )
        if not _phase_is_automatic(parameters, "initial_entry"):
            return set()
        return _decision_rules_source_dependencies(
            dict(parameters.get("entry_rules") or {})
        )

    # Last price remains subscribed for protective-stop enforcement in every
    # mode. Structural/trailing and add dependencies belong to automatic
    # position management; strategic-exit dependencies belong to automatic exit.
    dependencies = {("market.last_price", "")}
    if _phase_is_automatic(parameters, "exit"):
        for route in dict(phase_policy.get("exit") or {}).get("rule_sets") or []:
            if bool(route.get("enabled", True)):
                dependencies.update(
                    _rule_stage_source_dependencies(dict(route.get("rules") or {}))
                )
        management = dict(parameters.get("momentum_management") or {})
        downside = dict(management.get("downside_loss_guard") or {})
        if bool(downside.get("enabled", False)):
            timeframe = str(downside.get("timeframe") or "1s")
            dependencies.update({
                ("indicator.structure.bearish_choch", timeframe),
                ("indicator.macd.line", timeframe),
                ("indicator.macd.signal", timeframe),
                ("indicator.vwap.value", timeframe),
            })
    if _phase_is_automatic(parameters, "manage"):
        dependencies.update({
            ("indicator.structure.swing_high", ""),
            ("indicator.structure.swing_low", ""),
        })
        for step in dict(phase_policy.get("initial_entry") or {}).get("add_steps") or []:
            if bool(step.get("enabled", True)):
                dependencies.update(
                    _rule_stage_source_dependencies(dict(step.get("rules") or {}))
                )
    return dependencies


def _observation_updates_active_rules(
    parameters: dict[str, Any],
    observation: StrategyObservation,
    *,
    reentries: int,
) -> bool:
    if set(observation.evaluation_events) & {"manual", "position_event", "order_event"}:
        return True
    if not observation.changed_source_ids:
        # Older callers submit a complete causal snapshot without change metadata.
        # Continue to evaluate those snapshots while source-aware publishers use
        # the precise dependency routing below.
        return True
    active_dependencies = _active_rule_source_dependencies(
        parameters,
        observation,
        reentries=reentries,
    )
    for changed in observation.changed_source_ids:
        changed_source_id, separator, changed_timeframe = changed.rpartition("@")
        if not separator:
            changed_source_id, changed_timeframe = changed, ""
        for source_id, timeframe in active_dependencies:
            if changed_source_id != source_id:
                continue
            if not changed_timeframe or not timeframe or changed_timeframe == timeframe:
                return True
    return False


def _reentry_confirmation_is_fresh(
    phase_rules: dict[str, Any],
    observation: StrategyObservation,
    previous_exit_at: str,
) -> bool:
    if not previous_exit_at:
        return False
    try:
        boundary = datetime.fromisoformat(previous_exit_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    dependencies = _rule_stage_source_dependencies(
        dict(phase_rules.get("confirmation") or {})
    )
    if not dependencies:
        return False
    for source_id, timeframe in dependencies:
        candidates = [f"{source_id}@{timeframe}"] if timeframe else []
        candidates.append(source_id)
        cached = next(
            (
                observation.source_values[candidate]
                for candidate in candidates
                if candidate in observation.source_values
            ),
            None,
        )
        if isinstance(cached, dict):
            observed_at = cached.get("observed_at")
            if not observed_at:
                return False
            try:
                if datetime.fromisoformat(str(observed_at).replace("Z", "+00:00")) <= boundary:
                    return False
            except (TypeError, ValueError):
                return False
        elif _source_value(observation, source_id, timeframe) is None:
            return False
        else:
            try:
                if observation.observed_at <= boundary:
                    return False
            except TypeError:
                return False
    return True


def _reentry_signal_is_fresh(
    reentry: dict[str, Any],
    observation: StrategyObservation,
    previous_exit_at: str,
) -> bool:
    stream_id = str(reentry.get("require_new_signal_stream_id") or "").strip()
    if not stream_id:
        return True
    if not previous_exit_at:
        return False
    cached = observation.source_values.get(f"signal.activation.{stream_id}")
    if not isinstance(cached, dict) or not bool(cached.get("value")):
        return False
    try:
        activated_at = datetime.fromisoformat(
            str(cached.get("observed_at") or "").replace("Z", "+00:00")
        )
        boundary = datetime.fromisoformat(previous_exit_at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return activated_at > boundary


def _numeric_source_value(
    observation: StrategyObservation,
    source_id: str,
    timeframe: str = "",
) -> float | None:
    value = _source_value(observation, source_id, timeframe)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _exact_positive_open_macd(
    observation: StrategyObservation,
    timeframe: str = "1s",
) -> tuple[bool, dict[str, float | None]]:
    line = _numeric_source_value(observation, "indicator.macd.line", timeframe)
    signal = _numeric_source_value(observation, "indicator.macd.signal", timeframe)
    evidence = {"macd_line": line, "macd_signal": signal}
    return bool(
        line is not None
        and signal is not None
        and line > signal
        and line > 0
        and signal > 0
    ), evidence


def _spread_bps(observation: StrategyObservation) -> float | None:
    explicit = _numeric_source_value(observation, "market.spread_bps")
    if explicit is not None:
        return explicit
    if observation.bid > 0 and observation.ask >= observation.bid:
        midpoint = (observation.bid + observation.ask) / 2.0
        if midpoint > 0:
            return (observation.ask - observation.bid) / midpoint * 10_000.0
    return None


def _liquidity_admission_result(
    observation: StrategyObservation,
    policy: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    facts = {
        "price": observation.price,
        "session_dollar_volume": _numeric_source_value(
            observation, "market.session_dollar_volume"
        ),
        "session_share_volume": _numeric_source_value(observation, "market.volume"),
        "trade_rate_10s": _numeric_source_value(observation, "market.trade_rate_10s"),
        "trade_rate_60s": _numeric_source_value(observation, "market.trade_rate_60s"),
        "spread_bps": _spread_bps(observation),
    }
    checks = {
        "price_floor": facts["price"] >= float(policy.get("minimum_price") or 0),
        "price_ceiling": facts["price"] <= float(policy.get("maximum_price") or float("inf")),
        "session_dollar_volume": facts["session_dollar_volume"] is not None
        and float(facts["session_dollar_volume"])
        >= float(policy.get("minimum_session_dollar_volume") or 0),
        "session_share_volume": facts["session_share_volume"] is not None
        and float(facts["session_share_volume"])
        >= float(policy.get("minimum_session_share_volume") or 0),
        "trade_rate_10s": facts["trade_rate_10s"] is not None
        and float(facts["trade_rate_10s"])
        >= float(policy.get("minimum_trade_rate_10s") or 0),
        "trade_rate_60s": facts["trade_rate_60s"] is not None
        and float(facts["trade_rate_60s"])
        >= float(policy.get("minimum_trade_rate_60s") or 0),
        "spread": facts["spread_bps"] is not None
        and float(facts["spread_bps"])
        <= float(policy.get("maximum_spread_bps") or float("inf")),
    }
    return all(checks.values()), {
        "facts": facts,
        "checks": checks,
        "failed": [name for name, passed in checks.items() if not passed],
    }


def _current_execution_quality_result(
    observation: StrategyObservation,
    policy: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    spread = _spread_bps(observation)
    trade_rate = _numeric_source_value(observation, "market.trade_rate_10s")
    checks = {
        "current_trade_rate_10s": trade_rate is not None
        and trade_rate >= float(policy.get("minimum_trade_rate_10s") or 0),
        "current_spread": spread is not None
        and spread <= float(policy.get("maximum_spread_bps") or float("inf")),
    }
    return all(checks.values()), {
        "facts": {"trade_rate_10s": trade_rate, "spread_bps": spread},
        "checks": checks,
        "failed": [name for name, passed in checks.items() if not passed],
    }


def _level_metric(row: dict[str, Any], *names: str) -> float:
    for name in names:
        value = row.get(name)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return 0.0


def _level_is_entry_quality(row: dict[str, Any], policy: dict[str, Any]) -> bool:
    return bool(
        _level_metric(row, "salience", "strength")
        >= float(policy.get("minimum_salience") or 0)
        and _level_metric(row, "confidence")
        >= float(policy.get("minimum_confidence") or 0)
        and _level_metric(row, "reaction_probability")
        >= float(policy.get("minimum_reaction_probability") or 0)
    )


def _consolidated_structure_levels(
    rows: list[dict[str, Any]],
    *,
    side: str,
) -> list[dict[str, Any]]:
    """Merge overlapping level-book bands into one causal structural frontier."""

    prepared: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        price = _level_metric(row, "price", "lower", "upper")
        lower = _level_metric(row, "lower") or price
        upper = _level_metric(row, "upper") or price
        if price <= 0 or lower <= 0 or upper <= 0:
            continue
        if upper < lower:
            lower, upper = upper, lower
        row.update({"price": price, "lower": lower, "upper": upper})
        prepared.append(row)
    prepared.sort(key=lambda row: (float(row["lower"]), float(row["upper"])))
    merged: list[dict[str, Any]] = []
    for row in prepared:
        if not merged or float(row["lower"]) > float(merged[-1]["upper"]):
            merged.append({**row, "component_levels": [dict(row)]})
            continue
        current = merged[-1]
        components = [*list(current.get("component_levels") or []), dict(row)]
        representative = max(
            components,
            key=lambda item: (
                _level_metric(item, "salience", "strength"),
                _level_metric(item, "confidence"),
                _level_metric(item, "reaction_probability"),
            ),
        )
        current.update(representative)
        current["lower"] = min(float(item["lower"]) for item in components)
        current["upper"] = max(float(item["upper"]) for item in components)
        component_prices = [float(item.get("price") or 0) for item in components]
        current["price"] = (
            max(component_prices) if side == "long" else min(component_prices)
        )
        current["component_levels"] = components
    return merged


def _unified_entry_trigger(
    observation: StrategyObservation,
    parameters: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    policy = dict(parameters.get("structural_entry") or {})
    buffer_bps = float(policy.get("acceptance_buffer_bps") or 0)
    previous_price = state.get("previous_observed_price")
    rows = _consolidated_structure_levels([
        dict(row)
        for row in observation.structural_resistance_levels
        if isinstance(row, dict) and _level_is_entry_quality(dict(row), policy)
    ], side="long")
    if not rows and observation.structural_resistance_upper is not None:
        rows = [{
            "unified_level_id": "nearest-resistance",
            "price": observation.structural_resistance_price,
            "lower": observation.structural_resistance_lower,
            "upper": observation.structural_resistance_upper,
            "salience": observation.structural_resistance_strength,
            "confidence": observation.structural_resistance_confidence,
            "reaction_probability": observation.structural_resistance_strength,
        }]
    usable: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        boundary = row.get("upper") or row.get("price")
        try:
            boundary_value = float(boundary)
        except (TypeError, ValueError):
            continue
        if boundary_value > 0:
            usable.append((boundary_value, row))
    if not usable:
        return {"passed": False, "reason": "waiting_for_unified_resistance", "level": None}
    accepted = state.get("accepted_entry_resistance")
    accepted_level = (
        dict(accepted.get("level") or {})
        if isinstance(accepted, Mapping)
        else {}
    )
    accepted_boundary = (
        _level_metric(dict(accepted), "boundary")
        if isinstance(accepted, Mapping)
        else 0.0
    )
    accepted_at = (
        _optional_aware_datetime(accepted.get("accepted_at"))
        if isinstance(accepted, Mapping)
        else None
    )
    acceptance_age_ms = (
        max(0.0, (observation.observed_at - accepted_at).total_seconds() * 1_000)
        if accepted_at is not None
        else float("inf")
    )
    acceptance_hold_ms = max(0.0, float(policy.get("acceptance_hold_ms") or 0))
    accepted_threshold = accepted_boundary * (1 + buffer_bps / 10_000)
    if accepted_boundary > 0 and acceptance_age_ms <= acceptance_hold_ms:
        passed = observation.price > accepted_threshold
        return {
            "passed": passed,
            "reason": (
                "unified_resistance_acceptance_held"
                if passed
                else "waiting_for_accepted_resistance_frontier"
            ),
            "level": accepted_level,
            "reference_price": accepted_boundary,
            "threshold_price": accepted_threshold,
            "previous_price": previous_price,
            "accepted_at": accepted.get("accepted_at"),
            "acceptance_age_ms": acceptance_age_ms,
            "acceptance_hold_ms": acceptance_hold_ms,
        }
    state.pop("accepted_entry_resistance", None)

    # Arm one causal frontier and wait for price to clear that frontier.  A
    # newly formed *closer* resistance may tighten the watched threshold, but a
    # later higher band must not make the strategy chase price.  This is the
    # event-driven equivalent of selecting the current swing high and entering
    # on its next pass.
    overhead = [item for item in usable if item[0] >= observation.price]
    candidate_boundary, candidate_level = (
        min(overhead, key=lambda item: item[0])
        if overhead
        else max(usable, key=lambda item: item[0])
    )

    def combined_frontier(
        unified_boundary: float, level: dict[str, Any]
    ) -> tuple[float, dict[str, Any]]:
        swing_boundary = (
            float(observation.swing_high)
            if bool(policy.get("require_swing_high_frontier", False))
            and observation.swing_high is not None
            and float(observation.swing_high) > 0
            else 0.0
        )
        active_resistance_boundary = (
            float(observation.structural_resistance_upper)
            if bool(policy.get("require_active_resistance_frontier", False))
            and observation.structural_resistance_upper is not None
            and float(observation.structural_resistance_upper) > 0
            else 0.0
        )
        combined_boundary = max(
            unified_boundary,
            swing_boundary,
            active_resistance_boundary,
        )
        return combined_boundary, {
            **dict(level),
            "unified_break_boundary": unified_boundary,
            "swing_high_boundary": swing_boundary or None,
            "active_resistance_boundary": active_resistance_boundary or None,
            "combined_entry_boundary": combined_boundary,
        }

    candidate_boundary, candidate_level = combined_frontier(
        candidate_boundary, candidate_level
    )
    pending = state.get("pending_entry_resistance")
    pending_boundary = (
        _level_metric(dict(pending), "boundary")
        if isinstance(pending, Mapping)
        else 0.0
    )
    pending_level = (
        dict(pending.get("level") or {})
        if isinstance(pending, Mapping)
        else {}
    )
    def level_ids(row: Mapping[str, Any] | None) -> set[str]:
        if not isinstance(row, Mapping):
            return set()
        values = {
            str(row.get("unified_level_id") or "").strip(),
            *{
                str(component.get("unified_level_id") or "").strip()
                for component in row.get("component_levels") or ()
                if isinstance(component, Mapping)
            },
        }
        return {value for value in values if value}

    last_entry_level = state.get("last_entry_resistance")
    last_entry_ids = level_ids(last_entry_level)
    fresh_reentry_frontier = bool(
        last_entry_ids
        and level_ids(pending_level) & last_entry_ids
        and not level_ids(candidate_level) & last_entry_ids
    )
    frontier_changed = bool(
        pending_boundary <= 0
        or candidate_boundary < pending_boundary
        or fresh_reentry_frontier
    )
    if frontier_changed:
        boundary = candidate_boundary
        level = candidate_level
        armed_at = observation.observed_at.isoformat()
    else:
        boundary = pending_boundary
        level = pending_level
        armed_at = str(pending.get("armed_at") or observation.observed_at.isoformat())

    threshold = boundary * (1 + buffer_bps / 10_000)
    crossed = bool(
        previous_price is not None
        and float(previous_price) <= threshold
        and observation.price > threshold
    )
    repeats_last_entry_level = bool(
        level_ids(level) & last_entry_ids
    )
    already_above_new_frontier = bool(
        frontier_changed
        and observation.price > threshold
        and not repeats_last_entry_level
    )
    passed = crossed or already_above_new_frontier
    if passed:
        state["accepted_entry_resistance"] = {
            "boundary": boundary,
            "level": dict(level),
            "accepted_at": observation.observed_at.isoformat(),
        }
        state.pop("pending_entry_resistance", None)
    else:
        state["pending_entry_resistance"] = {
            "boundary": boundary,
            "level": dict(level),
            "armed_at": armed_at,
        }
    return {
        "passed": passed,
        "reason": "unified_resistance_accepted" if passed else "waiting_for_unified_resistance_break",
        "level": level,
        "reference_price": boundary,
        "threshold_price": threshold,
        "previous_price": previous_price,
    }


class LongMomentumStrategyEngine:
    """Deterministic long-only policy engine over causal point-in-time observations."""

    def evaluate(self, assignment: StrategyAssignment, observation: StrategyObservation) -> StrategyEngineResult:
        if assignment.ticker.upper() != observation.ticker.upper():
            raise ValueError("Observation ticker does not match strategy assignment")
        state = dict(assignment.state)
        status = assignment.status
        parameters = resolve_long_momentum_parameters(assignment.parameters)
        flatten_due = bool(
            observation.position_quantity > 0
            and _at_or_after_session_time(
                observation.observed_at,
                str(dict(parameters.get("strategy_behavior") or {}).get("flatten_time") or ""),
            )
        )
        if status in {AssignmentStatus.DISABLED, AssignmentStatus.COMPLETED, AssignmentStatus.ERROR}:
            return self._result(assignment, observation, "wait", "assignment_not_active", 0.0, 1.0, state, status)
        if status == AssignmentStatus.PAUSED:
            return self._result(assignment, observation, "wait", "assignment_paused", 0.0, 1.0, state, status)
        if status == AssignmentStatus.EXIT_PENDING and observation.position_quantity > 0:
            return self._result(
                assignment,
                observation,
                "hold",
                "exit_fill_pending",
                0.0,
                1.0,
                state,
                AssignmentStatus.EXIT_PENDING,
            )
        if not assignment.permissions.observe:
            return self._result(assignment, observation, "wait", "observation_not_authorized", 0.0, 1.0, state, status)
        if not flatten_due and not _observation_updates_active_rules(
            parameters,
            observation,
            reentries=int(state.get("reentries") or 0),
        ):
            return self._result(assignment, observation, "wait", "no_active_rule_source_updated", 0.0, 1.0, state, status)

        state["previous_observed_price"] = state.get("last_price")
        state["last_observed_at"] = observation.observed_at.isoformat()
        state["last_price"] = observation.price
        _record_structural_anchors(state, observation)
        if observation.position_quantity > 0:
            return self._evaluate_position(assignment, observation, parameters, state)
        return self._evaluate_flat(assignment, observation, parameters, state)

    def _evaluate_flat(
        self,
        assignment: StrategyAssignment,
        observation: StrategyObservation,
        parameters: dict[str, Any],
        state: dict[str, Any],
    ) -> StrategyEngineResult:
        reentries = int(state.get("reentries") or 0)
        reentry = parameters["reentry"]
        state.pop("manual_entry_requested", None)
        state.pop("force_entry_requested", None)
        if assignment.status == AssignmentStatus.ENTRY_PENDING:
            return self._result(assignment, observation, "wait", "entry_fill_pending", 0.0, 1.0, state, AssignmentStatus.ENTRY_PENDING)
        phase_name = "reentry" if reentries else "initial_entry"
        if not _phase_is_automatic(parameters, phase_name):
            return self._result(
                assignment,
                observation,
                "wait",
                "reentry_manual_mode" if reentries else "initial_entry_manual_mode",
                0.0,
                1.0,
                state,
                AssignmentStatus.REENTRY_COOLDOWN if reentries else AssignmentStatus.WATCHING,
            )
        if reentries and not assignment.permissions.reenter:
            return self._result(assignment, observation, "wait", "reentry_not_authorized", 0.0, 1.0, state, AssignmentStatus.COMPLETED)
        if reentries and state.get("last_exit_at"):
            last_exit = datetime.fromisoformat(str(state["last_exit_at"]).replace("Z", "+00:00"))
            elapsed_ms = (observation.observed_at - last_exit).total_seconds() * 1000
            if elapsed_ms < float(reentry["cooldown_ms"]):
                return self._result(assignment, observation, "wait", "reentry_cooldown", 0.0, 1.0, state, AssignmentStatus.REENTRY_COOLDOWN)
            if not _reentry_signal_is_fresh(
                reentry,
                observation,
                str(state.get("last_exit_at") or ""),
            ):
                return self._result(
                    assignment,
                    observation,
                    "wait",
                    "waiting_for_renewed_early_squeeze",
                    0.0,
                    _confirmation_confidence(observation),
                    state,
                    AssignmentStatus.REENTRY_COOLDOWN,
                    metadata={
                        "required_signal_stream_id": str(
                            reentry.get("require_new_signal_stream_id") or ""
                        )
                    },
                )
        authority_key = (
            "reentry_authority" if reentries else "initial_entry_authority"
        )
        authority = _campaign_authority(state, authority_key, "automatic")
        if authority == "disabled":
            return self._result(
                assignment,
                observation,
                "wait",
                "reentry_disabled" if reentries else "initial_entry_disabled",
                0.0,
                1.0,
                state,
                AssignmentStatus.COMPLETED,
            )
        if authority == "manual" and not observation.manual_entry_request and not observation.force_entry:
            reason = (
                "reentry_manual_request_required"
                if reentries
                else "initial_entry_manual_request_required"
            )
            return self._result(
                assignment,
                observation,
                "wait",
                reason,
                0.0,
                1.0,
                state,
                AssignmentStatus.REENTRY_COOLDOWN
                if reentries
                else AssignmentStatus.WATCHING,
            )
        if not assignment.permissions.enter and not observation.force_entry:
            return self._result(assignment, observation, "wait", "entry_not_authorized", 0.0, 1.0, state, AssignmentStatus.WATCHING)
        if not observation.market_open:
            return self._result(assignment, observation, "wait", "market_not_open", 0.0, 1.0, state, AssignmentStatus.WATCHING)
        entry_cutoff = str(
            dict(parameters.get("strategy_behavior") or {}).get("entry_cutoff_time") or ""
        )
        if _at_or_after_session_time(observation.observed_at, entry_cutoff):
            return self._result(
                assignment,
                observation,
                "wait",
                "entry_cutoff_reached",
                0.0,
                1.0,
                state,
                AssignmentStatus.COMPLETED,
            )

        # Structural acceptance is an event in the watched campaign, not a
        # side-effect of whichever confirmation gate happens to finish last.
        # Arm one causal frontier, allow only a newly observed closer frontier
        # to tighten it, and latch its fresh cross before liquidity, VWAP, and
        # MACD can return early. Later frames may enter while that acceptance
        # remains above the same boundary.
        unified_trigger: dict[str, Any] | None = None
        if bool(dict(parameters.get("structural_entry") or {}).get("enabled", False)):
            unified_trigger = _unified_entry_trigger(
                observation,
                parameters,
                state,
            )

        replenishment = dict(reentry.get("target_replenishment") or {})
        entitlement = float(state.get("target_replenishment_quantity") or 0)
        if (
            reentries
            and entitlement > 0
            and bool(replenishment.get("enabled", False))
        ):
            replenishment_result = self._target_replenishment_result(
                assignment,
                observation,
                parameters,
                state,
                flat=True,
            )
            if replenishment_result is not None:
                return replenishment_result

        liquidity_policy = dict(parameters.get("liquidity_admission") or {})
        if bool(liquidity_policy.get("enabled", False)):
            admitted = bool(state.get("liquidity_admitted_at"))
            admission_detail: dict[str, Any] = {}
            if not admitted:
                admitted, admission_detail = _liquidity_admission_result(
                    observation, liquidity_policy
                )
                if admitted:
                    state["liquidity_admitted_at"] = observation.observed_at.isoformat()
                    state["liquidity_admission_evidence"] = admission_detail
            if not admitted:
                return self._result(
                    assignment,
                    observation,
                    "wait",
                    "liquidity_admission_incomplete",
                    0.0,
                    1.0,
                    state,
                    AssignmentStatus.REENTRY_COOLDOWN if reentries else AssignmentStatus.WATCHING,
                    metadata={"liquidity_admission": admission_detail},
                )
            execution_ready, execution_detail = _current_execution_quality_result(
                observation, liquidity_policy
            )
            if not execution_ready:
                return self._result(
                    assignment,
                    observation,
                    "wait",
                    "current_execution_quality_incomplete",
                    0.0,
                    1.0,
                    state,
                    AssignmentStatus.REENTRY_COOLDOWN if reentries else AssignmentStatus.WATCHING,
                    metadata={"execution_quality": execution_detail},
                )

        phase_policy = dict(parameters.get("phase_policy") or {})
        phase = dict(phase_policy.get(phase_name) or {})
        phase_rules = (
            dict(phase.get("rules") or {})
            if reentries
            else dict(parameters.get("entry_rules") or {})
        )
        if state.get("liquidity_admitted_at"):
            confirmation_stage = entry_stage_without_rule_set(
                dict(phase_rules.get("confirmation") or {}),
                "strategy-squeeze-volume-spread-quality",
            )
            phase_rules = {**phase_rules, "confirmation": confirmation_stage}
        if (
            reentries
            and bool(reentry.get("require_new_confirmation", True))
            and not _reentry_confirmation_is_fresh(
                phase_rules,
                observation,
                str(state.get("last_exit_at") or ""),
            )
        ):
            return self._result(
                assignment,
                observation,
                "wait",
                "reentry_confirmation_not_fresh",
                0.0,
                _confirmation_confidence(observation),
                state,
                AssignmentStatus.REENTRY_COOLDOWN,
            )
        rule_result = evaluate_entry_decision_rules(phase_rules, observation)
        reference_name, reference, reference_buffer_bps = _trigger_reference(
            phase_rules,
            rule_result,
            observation,
        )
        if reference is None:
            reference_name, reference, reference_buffer_bps = (
                _configured_trigger_reference(phase_rules, observation)
            )
        operational_triggers = [
            key
            for key, value in {
                "manual_entry_request": observation.manual_entry_request,
                "force_entry": observation.force_entry,
            }.items()
            if value
        ]
        if unified_trigger is not None:
            triggered = [*operational_triggers]
            if bool(unified_trigger.get("passed")):
                triggered.append("unified-structural-resistance")
            reference_name = "qmd.unified_structure.resistance"
            reference = unified_trigger.get("reference_price")
            reference_buffer_bps = float(
                dict(parameters.get("structural_entry") or {}).get("acceptance_buffer_bps")
                or 0
            )
        else:
            triggered = [*operational_triggers, *rule_result["trigger"]["matched_groups"]]
        confirmation_score = float(rule_result["confirmation"]["score"])
        confirmation = dict(rule_result["confirmation"]["groups"])
        vetoes = list(rule_result["veto"]["matched_groups"])
        trigger_passed = (
            bool(unified_trigger.get("passed"))
            if unified_trigger is not None
            else bool(triggered)
        )
        can_enter = trigger_passed and not vetoes and (
            observation.force_entry or bool(rule_result["confirmation"]["passed"])
        )
        if not can_enter:
            confirmation_passed = bool(rule_result["confirmation"]["passed"])
            if vetoes:
                reason = "entry_vetoed"
            elif not confirmation_passed:
                reason = "entry_confirmation_incomplete"
            elif unified_trigger is not None:
                reason = str(unified_trigger.get("reason") or "waiting_for_unified_resistance_break")
            elif reference_name.endswith("indicator.structure.swing_high"):
                reason = (
                    "waiting_for_swing_high_reference"
                    if reference is None
                    else "waiting_for_swing_high_cross"
                )
            else:
                reason = "waiting_for_entry_trigger"
            trigger_threshold = (
                float(reference) * (1.0 + float(reference_buffer_bps) / 10_000.0)
                if reference is not None
                else None
            )
            return self._result(
                assignment,
                observation,
                "wait",
                reason,
                confirmation_score,
                _confirmation_confidence(observation),
                state,
                AssignmentStatus.WATCHING,
                metadata={
                    "triggers": triggered,
                    "vetoes": vetoes,
                    "confirmation": confirmation,
                    "entry_rules": rule_result,
                    "trigger_reference_name": reference_name,
                    "trigger_reference_price": reference,
                    "trigger_threshold_price": trigger_threshold,
                    "unified_structural_trigger": unified_trigger,
                },
            )
        if authority == "confirm" and not observation.manual_entry_request and not observation.force_entry:
            return self._result(
                assignment,
                observation,
                "wait",
                "reentry_confirmation_required" if reentries else "initial_entry_confirmation_required",
                confirmation_score,
                _confirmation_confidence(observation),
                state,
                AssignmentStatus.REENTRY_COOLDOWN if reentries else AssignmentStatus.WATCHING,
                metadata={"triggers": triggered, "vetoes": vetoes, "confirmation": confirmation, "entry_rules": rule_result},
            )

        side = _strategy_side(parameters)
        stop = _initial_stop(observation, parameters, reference, side=side)
        capital_request = _phase_capital_request(
            parameters,
            phase_name,
            fallback_quantity=float(parameters["sizing"]["initial_quantity"]),
        )
        quantity = (
            capital_request.value
            if capital_request.mode == "fixed_quantity"
            else 0.0
        )
        target = _luld_target(observation, parameters, side=side)
        profit_targets = _structural_profit_targets(
            observation,
            parameters,
            stop=stop,
            side=side,
            luld_target=target,
        )
        if profit_targets:
            target = profit_targets[0]
        order_intent = dict(phase.get("order_intent") or {})
        missing_anchor = _missing_protection_anchor(
            order_intent,
            observation=observation,
            parameters=parameters,
            state=state,
            side=side,
        )
        if missing_anchor:
            return self._result(
                assignment,
                observation,
                "wait",
                "protection_anchor_unavailable",
                confirmation_score,
                _confirmation_confidence(observation),
                state,
                AssignmentStatus.WATCHING,
                metadata={"missing_protection_anchor": missing_anchor},
            )
        state.update(
            {
                "breakout_level": reference,
                "breakout_buffer_bps": reference_buffer_bps,
                "entry_reference_price": observation.price,
                "entry_at": observation.observed_at.isoformat(),
                "initial_stop": stop,
                "active_stop": stop,
                "high_water_price": observation.price,
                "low_water_price": observation.price,
                "adds": 0,
                "profit_takes": 0,
                "entries": int(state.get("entries") or 0) + 1,
                "last_extension_at": observation.observed_at.isoformat(),
                "failure_to_extend_uses": 0,
                "macd_closed_since": "",
                "latest_post_entry_swing_low": None,
                "previous_post_entry_swing_low": None,
                "higher_low_confirmed": False,
                "structural_profit_targets": profit_targets,
                "last_entry_resistance": dict(
                    (unified_trigger or {}).get("level") or {}
                ),
            }
        )
        state.pop("accepted_entry_resistance", None)
        state.pop("pending_entry_resistance", None)
        return self._result(
            assignment,
            observation,
            "enter_long" if side == "long" else "enter_short",
            "reentry_confirmed" if reentries else "entry_confirmed",
            confirmation_score,
            _confirmation_confidence(observation),
            state,
            AssignmentStatus.ENTRY_PENDING,
            quantity=quantity,
            invalidation_price=stop,
            profit_target_price=target,
            trailing_amount=_trailing_amount(observation, parameters),
            capital_request=capital_request,
            order_intent=order_intent,
            metadata={
                "triggers": triggered,
                "confirmation": confirmation,
                "reference": reference_name,
                "trigger_reference_name": reference_name,
                "trigger_reference_price": reference,
                "trigger_threshold_price": (
                    float(reference)
                    * (1.0 + float(reference_buffer_bps) / 10_000.0)
                    if reference is not None
                    else None
                ),
                "entry_rules": rule_result,
                "initial_stop": stop,
                "profit_targets": profit_targets,
                "unified_structural_trigger": unified_trigger,
            },
        )

    def _evaluate_position(
        self,
        assignment: StrategyAssignment,
        observation: StrategyObservation,
        parameters: dict[str, Any],
        state: dict[str, Any],
    ) -> StrategyEngineResult:
        side = _strategy_side(parameters)
        manage_automatic = _phase_is_automatic(parameters, "manage")
        previous_high_water = float(
            state.get("high_water_price") or observation.price
        )
        if side == "long":
            state["high_water_price"] = max(previous_high_water, observation.price)
        else:
            state["low_water_price"] = min(float(state.get("low_water_price") or observation.price), observation.price)
        entry_price = float(
            state.get("entry_reference_price")
            or observation.average_price
            or observation.price
        )
        gain_pct = (
            (observation.price / entry_price - 1) * 100
            if side == "long"
            else (entry_price / observation.price - 1) * 100
        ) if entry_price > 0 else 0.0
        _update_momentum_management_state(
            parameters,
            observation,
            state,
            side=side,
            previous_high_water=previous_high_water,
        )
        if manage_automatic:
            state["active_stop"] = _ratcheted_stop(observation, parameters, state, side=side)
        else:
            state["active_stop"] = float(
                state.get("active_stop")
                or state.get("initial_stop")
                or _initial_stop(observation, parameters, observation.price, side=side)
            )
        stop = float(state["active_stop"])
        breakout_level = float(state.get("breakout_level") or 0)
        breakout_buffer = observation.price * float(state.get("breakout_buffer_bps") or 0) / 10_000
        failed_breakout = bool(
            breakout_level > 0
            and (
                observation.price < breakout_level - breakout_buffer
                if side == "long"
                else observation.price > breakout_level + breakout_buffer
            )
        )
        protection_breached = (
            observation.price <= stop if side == "long" else observation.price >= stop
        )
        exit_automatic = _phase_is_automatic(parameters, "exit")
        flatten_time = str(
            dict(parameters.get("strategy_behavior") or {}).get("flatten_time") or ""
        )
        if _at_or_after_session_time(observation.observed_at, flatten_time):
            state["disable_after_exit"] = True
            exit_route = {
                "route_id": "session-flatten",
                "name": "Session flatten",
                "mechanism": "session_flatten",
                "position_fraction": 1.0,
            }
        elif protection_breached:
            exit_route = {
                "route_id": "oms-protective-stop",
                "name": "OMS protective stop",
                "mechanism": "protective_stop",
                "position_fraction": 1.0,
            }
        elif exit_automatic:
            exit_route = _matching_momentum_management_route(
                parameters,
                observation,
                state,
                gain_pct=gain_pct,
                side=side,
            )
            if exit_route is None:
                exit_phase = dict(
                    dict(parameters.get("phase_policy") or {}).get("exit") or {}
                )
                if "rule_sets" in exit_phase:
                    exit_route = _matching_exit_rule_set(
                        list(exit_phase.get("rule_sets") or []),
                        observation=observation,
                        entry_at=str(state.get("entry_at") or ""),
                    )
                else:
                    exit_route = _matching_exit_route(
                        list(parameters.get("exit_routes") or []),
                        observation=observation,
                        protective_stop=stop,
                        failed_breakout=failed_breakout,
                        side=side,
                    )
        else:
            exit_route = None
        manual_exit_requested = bool(state.pop("manual_exit_requested", False))
        if manual_exit_requested:
            exit_route = {
                "route_id": "manual-exit",
                "name": "Operator-requested exit",
                "mechanism": "manual_exit",
                "priority": 100,
            }
        if (
            exit_route is not None
            and str(exit_route.get("mechanism")) != "protective_stop"
            and not manual_exit_requested
            and _campaign_authority(state, "exit_authority", "automatic")
            in {"manual", "confirm"}
        ):
            return self._result(
                assignment,
                observation,
                "hold",
                "exit_confirmation_required",
                observation.qmd_score,
                _confirmation_confidence(observation),
                state,
                AssignmentStatus.MANAGING,
                invalidation_price=stop,
                trailing_amount=_trailing_amount(observation, parameters),
                metadata={
                    "proposed_exit_rule_set_id": exit_route["route_id"],
                    "proposed_exit_rule_set_name": exit_route["name"],
                },
            )
        route_fraction = (
            float(exit_route.get("position_fraction") or 1.0)
            if exit_route is not None
            else 1.0
        )
        route_is_partial = route_fraction < 1.0
        if exit_route is not None and (
            (assignment.permissions.reduce if route_is_partial else assignment.permissions.exit)
            or str(exit_route.get("mechanism")) == "protective_stop"
        ):
            reason = str(exit_route["mechanism"])
            if reason == "session_flatten" or (
                reason in {"protective_stop", "failed_breakout", "bearish_qmd_macd"}
                and not bool(parameters["reentry"].get("after_protective_exit", False))
            ):
                state["disable_after_exit"] = True
            state["last_exit_reason"] = reason
            state["last_exit_route_id"] = str(exit_route["route_id"])
            position_fraction = route_fraction
            partial_reduction = position_fraction < 1.0
            if partial_reduction:
                state["failure_to_extend_uses"] = int(
                    state.get("failure_to_extend_uses") or 0
                ) + 1
            else:
                state["last_exit_at"] = observation.observed_at.isoformat()
            next_status = AssignmentStatus.EXIT_PENDING
            action = (
                "reduce_long"
                if side == "long" and partial_reduction
                else "reduce_short"
                if side == "short" and partial_reduction
                else "exit"
                if side == "long"
                else "cover"
            )
            return self._result(
                assignment, observation, action, reason, observation.qmd_score,
                max(observation.qmd_confidence, 0.5), state, next_status,
                quantity=observation.position_quantity * position_fraction, invalidation_price=stop,
                trailing_amount=_trailing_amount(observation, parameters),
                order_intent=dict(exit_route.get("order_intent") or {}),
                metadata={
                    "exit_rule_set_id": exit_route["route_id"],
                    "exit_rule_set_name": exit_route["name"],
                    "exit_route_id": exit_route["route_id"],
                    "exit_route_name": exit_route["name"],
                    "buy_back": bool(
                        not partial_reduction
                        and
                        assignment.permissions.reenter
                        and not state.get("disable_after_exit")
                    ),
                    "position_fraction": position_fraction,
                    "gain_pct": gain_pct,
                    **dict(exit_route.get("evidence") or {}),
                },
            )

        if float(state.get("target_replenishment_quantity") or 0) > 0:
            replenishment_result = self._target_replenishment_result(
                assignment,
                observation,
                parameters,
                state,
                flat=False,
            )
            if replenishment_result is not None:
                return replenishment_result

        if not manage_automatic:
            return self._result(
                assignment,
                observation,
                "hold",
                "position_management_manual_mode",
                observation.qmd_score,
                _confirmation_confidence(observation),
                state,
                AssignmentStatus.MANAGING,
                invalidation_price=stop,
                trailing_amount=_trailing_amount(observation, parameters),
            )

        confirmation: dict[str, bool] = {}
        add_steps = list(
            dict(
                dict(parameters.get("phase_policy") or {}).get("initial_entry") or {}
            ).get("add_steps") or []
        )
        if not add_steps and bool(dict(parameters.get("add") or {}).get("enabled")):
            legacy_add = dict(parameters.get("add") or {})
            legacy_rules = evaluate_entry_decision_rules(
                dict(parameters.get("entry_rules") or {}),
                observation,
            )
            if (
                observation.structure_event == "choch"
                and observation.structure_direction == ("bullish" if side == "long" else "bearish")
                and bool(legacy_rules["confirmation"]["passed"])
            ):
                add_steps = [{
                    "step_id": "legacy-confirmed-add",
                    "name": "Legacy confirmed add",
                    "enabled": True,
                    "rules": {"operator": "any", "groups": []},
                    "capital_request": {
                        "mode": "fixed_quantity",
                        "value": float(parameters["sizing"]["initial_quantity"])
                        * float(parameters["sizing"]["add_fraction"]),
                        "priority": 50,
                        "allow_replacement": False,
                    },
                    "order_intent": {},
                    "maximum_uses": int(legacy_add.get("maximum_adds") or 0),
                    "_legacy_rules_passed": True,
                }]
        for add_step in add_steps:
            uses = dict(state.get("add_step_uses") or {})
            step_id = str(add_step.get("step_id") or "")
            used = int(uses.get(step_id) or 0)
            if (
                not assignment.permissions.add
                or not bool(add_step.get("enabled", True))
                or used >= int(add_step.get("maximum_uses") or 1)
                or not (
                    bool(add_step.get("_legacy_rules_passed"))
                    or _rule_stage_passed(dict(add_step.get("rules") or {}), observation)
                )
            ):
                continue
            add_request = _capital_request_from_payload(
                dict(add_step.get("capital_request") or {})
            )
            order_intent = dict(add_step.get("order_intent") or {})
            missing_anchor = _missing_protection_anchor(
                order_intent,
                observation=observation,
                parameters=parameters,
                state=state,
                side=side,
            )
            if missing_anchor:
                return self._result(
                    assignment,
                    observation,
                    "hold",
                    "protection_anchor_unavailable",
                    observation.qmd_score,
                    _confirmation_confidence(observation),
                    state,
                    AssignmentStatus.MANAGING,
                    metadata={"missing_protection_anchor": missing_anchor},
                )
            add_qty = add_request.value if add_request.mode == "fixed_quantity" else 0.0
            uses[step_id] = used + 1
            state["add_step_uses"] = uses
            state["adds"] = int(state.get("adds") or 0) + 1
            return self._result(
                assignment, observation, "add_long" if side == "long" else "add_short", step_id or "position_add",
                observation.qmd_score, _confirmation_confidence(observation), state,
                AssignmentStatus.MANAGING, quantity=add_qty, invalidation_price=stop,
                trailing_amount=_trailing_amount(observation, parameters),
                capital_request=add_request,
                order_intent=order_intent,
                metadata={"add_step_id": step_id, "add_step_name": add_step.get("name")},
            )

        pocket = parameters["profit_pocket"]
        previous_acceleration = float(state.get("last_acceleration") or 0)
        threshold = float(pocket["acceleration_slowdown_threshold"])
        slowdown = previous_acceleration > threshold and observation.acceleration <= threshold
        favorable_pct = gain_pct >= float(pocket["minimum_gain_pct"])
        favorable_volatility = (
            observation.volatility > 0
            and abs(observation.price - entry_price) >= observation.volatility * float(pocket["volatility_multiple"])
        )
        trigger_name = str(pocket["trigger"])
        pocket_triggered = (
            slowdown if trigger_name == "acceleration_slowdown"
            else favorable_volatility if trigger_name == "volatility_multiple"
            else favorable_pct
        )
        state["last_acceleration"] = observation.acceleration
        executor_owned_partial = bool(
            dict(
                dict(parameters.get("momentum_management") or {}).get(
                    "failure_to_extend"
                )
                or {}
            ).get("enabled", False)
        )
        pocket_qty = min(
            observation.position_quantity,
            max(0.0, observation.position_quantity * float(pocket["quantity_fraction"])),
        )
        remaining = observation.position_quantity - pocket_qty
        if (
            assignment.permissions.reduce
            and pocket["enabled"]
            and not executor_owned_partial
            and favorable_pct
            and pocket_triggered
            and pocket_qty > 0
            and (
                pocket_qty >= observation.position_quantity
                or remaining >= float(pocket["minimum_remaining_quantity"])
            )
        ):
            state["profit_takes"] = int(state.get("profit_takes") or 0) + 1
            state["last_profit_take_at"] = observation.observed_at.isoformat()
            state["last_exit_reason"] = "profit_pocket"
            partial_reduction = pocket_qty < observation.position_quantity
            return self._result(
                assignment,
                observation,
                (
                    "reduce_long"
                    if side == "long" and partial_reduction
                    else "reduce_short"
                    if side == "short" and partial_reduction
                    else "exit"
                    if side == "long"
                    else "cover"
                ),
                "profit_pocket",
                max(0.0, observation.qmd_score), _confirmation_confidence(observation),
                state, AssignmentStatus.EXIT_PENDING, quantity=pocket_qty,
                invalidation_price=stop,
                trailing_amount=_trailing_amount(observation, parameters),
                metadata={
                    "gain_pct": gain_pct,
                    "buy_back": bool(
                        not partial_reduction
                        and
                        assignment.permissions.reenter
                        and not state.get("disable_after_exit")
                    ),
                },
            )

        return self._result(
            assignment, observation, "hold", "position_managed",
            observation.qmd_score, _confirmation_confidence(observation),
            state, AssignmentStatus.MANAGING, invalidation_price=stop,
            metadata={"confirmation": confirmation, "gain_pct": gain_pct},
        )

    def _target_replenishment_result(
        self,
        assignment: StrategyAssignment,
        observation: StrategyObservation,
        parameters: dict[str, Any],
        state: dict[str, Any],
        *,
        flat: bool,
    ) -> StrategyEngineResult | None:
        reentry = dict(parameters.get("reentry") or {})
        policy = dict(reentry.get("target_replenishment") or {})
        if not bool(policy.get("enabled", False)):
            return None
        entitlement = float(state.get("target_replenishment_quantity") or 0)
        if entitlement <= 0:
            return None
        if bool(state.get("target_replenishment_pending")):
            return self._result(
                assignment,
                observation,
                "wait" if flat else "hold",
                "target_replenishment_fill_pending",
                0.0,
                1.0,
                state,
                AssignmentStatus.ENTRY_PENDING if flat else AssignmentStatus.MANAGING,
                metadata={"replenishment_quantity": entitlement},
            )
        authorized = assignment.permissions.reenter if flat else assignment.permissions.add
        if not authorized:
            return self._result(
                assignment,
                observation,
                "wait" if flat else "hold",
                "target_replenishment_not_authorized",
                0.0,
                1.0,
                state,
                AssignmentStatus.REENTRY_COOLDOWN if flat else AssignmentStatus.MANAGING,
                metadata={"replenishment_quantity": entitlement},
            )
        peak = max(
            float(state.get("target_replenishment_peak_price") or 0),
            observation.price,
        )
        state["target_replenishment_peak_price"] = peak
        pullback_required = max(
            observation.volatility
            * float(policy.get("minimum_pullback_atr_multiple") or 0),
            peak * float(policy.get("minimum_pullback_bps") or 0) / 10_000.0,
        )
        pullback = peak - observation.price
        macd_open, macd_evidence = _exact_positive_open_macd(observation, "1s")
        vwap = _numeric_source_value(observation, "indicator.vwap.value", "1s")
        above_vwap = vwap is not None and observation.price > vwap
        support_lower = observation.structural_support_lower
        support_buffer = (
            float(support_lower)
            * float(policy.get("support_buffer_bps") or 0)
            / 10_000.0
            if support_lower is not None
            else 0.0
        )
        support_held = support_lower is None or observation.price >= float(support_lower) - support_buffer
        bearish_choch = bool(
            observation.structure_event == "choch"
            and observation.structure_direction == "bearish"
        ) or not support_held
        liquidity_policy = dict(parameters.get("liquidity_admission") or {})
        if bool(liquidity_policy.get("enabled", False)):
            execution_ready, execution_evidence = _current_execution_quality_result(
                observation, liquidity_policy
            )
        else:
            execution_ready, execution_evidence = True, {"checks": {}, "failed": []}
        try:
            armed_at = datetime.fromisoformat(
                str(state.get("target_replenishment_armed_at") or "").replace("Z", "+00:00")
            )
            after_fill = observation.observed_at > armed_at
        except ValueError:
            after_fill = False
        checks = {
            "after_target_fill": after_fill,
            "pullback_confirmed": pullback >= pullback_required and pullback_required > 0,
            "macd_positive_and_open": macd_open,
            "above_vwap": above_vwap,
            "no_bearish_choch": not bearish_choch,
            "execution_quality": execution_ready,
        }
        evidence = {
            "checks": checks,
            "failed": [name for name, passed in checks.items() if not passed],
            "target_level_price": state.get("target_replenishment_level_price"),
            "peak_price": peak,
            "last_price": observation.price,
            "pullback": pullback,
            "pullback_required": pullback_required,
            "vwap": vwap,
            "support_lower": support_lower,
            **macd_evidence,
            "execution_quality_detail": execution_evidence,
        }
        if not all(checks.values()):
            return self._result(
                assignment,
                observation,
                "wait" if flat else "hold",
                "waiting_for_target_replenishment_pullback",
                0.0,
                _confirmation_confidence(observation),
                state,
                AssignmentStatus.REENTRY_COOLDOWN if flat else AssignmentStatus.MANAGING,
                metadata={"target_replenishment": evidence},
            )
        quantity = float(max(1, floor(entitlement)))
        side = _strategy_side(parameters)
        stop = _initial_stop(
            observation,
            parameters,
            state.get("target_replenishment_level_price") or observation.price,
            side=side,
        )
        luld_target = _luld_target(observation, parameters, side=side)
        targets = _structural_profit_targets(
            observation,
            parameters,
            stop=stop,
            side=side,
            luld_target=luld_target,
        )
        # Every structural target owns one independently protected whole-share
        # slice. A small replenishment therefore cannot carry more targets than
        # executable shares. Preserve the nearest/highest-ranked targets and
        # let any residual quantity remain distributed across those slices.
        targets = targets[: max(1, int(quantity))]
        state["structural_profit_targets"] = targets
        state["target_replenishment_pending"] = True
        state["target_replenishment_pending_quantity"] = quantity
        state["target_replenishment_quantity"] = max(0.0, entitlement - quantity)
        state["entries"] = int(state.get("entries") or 0) + 1
        state["entry_reference_price"] = observation.price if flat else state.get("entry_reference_price")
        state["entry_at"] = observation.observed_at.isoformat() if flat else state.get("entry_at")
        capital_request = CapitalRequest(
            mode="fixed_quantity",
            value=quantity,
            allow_replacement=False,
        )
        order_intent = dict(
            dict(dict(parameters.get("phase_policy") or {}).get("reentry") or {}).get(
                "order_intent"
            )
            or {}
        )
        return self._result(
            assignment,
            observation,
            "enter_long" if flat else "add_long",
            "target_profit_replenishment",
            1.0,
            _confirmation_confidence(observation),
            state,
            AssignmentStatus.ENTRY_PENDING if flat else AssignmentStatus.MANAGING,
            quantity=quantity,
            invalidation_price=stop,
            profit_target_price=targets[0] if targets else None,
            trailing_amount=_trailing_amount(observation, parameters),
            capital_request=capital_request,
            order_intent=order_intent,
            metadata={
                "execution_role": "target_replenishment",
                "replenishment_quantity": quantity,
                "profit_targets": targets,
                "target_replenishment": evidence,
            },
        )

    def _result(
        self,
        assignment: StrategyAssignment,
        observation: StrategyObservation,
        action: str,
        reason: str,
        score: float,
        confidence: float,
        state: dict[str, Any],
        status: AssignmentStatus,
        *,
        quantity: float = 0.0,
        invalidation_price: float | None = None,
        profit_target_price: float | None = None,
        trailing_amount: float | None = None,
        capital_request: CapitalRequest | None = None,
        order_intent: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> StrategyEngineResult:
        event_id = str(uuid4())
        resolved_metadata = dict(metadata or {})
        reason_detail = _decision_reason_detail(
            action,
            reason,
            observation,
            resolved_metadata,
            state,
        )
        resolved_metadata["reason_code"] = reason
        resolved_metadata["reason_detail"] = reason_detail
        source_cause = (
            observation.source_signal_ids[-1]
            if observation.source_signal_ids
            else f"{observation.ticker.upper()}:{observation.observed_at.isoformat()}"
        )
        lineage = causal_identity(
            correlation_seed=assignment.assignment_id,
            causation_seed=source_cause,
        )
        signal = StrategySignal(
            signal_id=event_id,
            signal_type=reason,
            ticker=observation.ticker.upper(),
            event_time=observation.observed_at,
            action=action,  # type: ignore[arg-type]
            direction=(
                "bearish"
                if action in {"reduce_long", "take_profit", "exit", "enter_short", "add_short"}
                else "bullish"
                if action in {"enter_long", "add_long", "reduce_short", "cover"}
                else "neutral"
            ),
            score=max(-1.0, min(1.0, score)),
            confidence=max(0.0, min(1.0, confidence)),
            reason=reason,
            source_signal_ids=observation.source_signal_ids,
            working_timeframe=str(assignment.parameters.get("entry", {}).get("breakout_timeframe") or "1s"),
            invalidation_price=invalidation_price,
            metadata={
                **resolved_metadata,
                "assignment_id": assignment.assignment_id,
                "reference_price": observation.price,
                "status": status.value,
                **lineage,
            },
        )
        intents: tuple[StrategyIntent, ...] = ()
        if action in {"enter_long", "add_long", "reduce_long", "take_profit", "exit", "enter_short", "add_short", "reduce_short", "cover"}:
            resolved_order_intent = dict(order_intent or {})
            if "time_in_force" in resolved_order_intent or "outside_rth" in resolved_order_intent:
                raise ValueError(
                    "Strategy order intents cannot override OMS session routing"
                )
            resolved_capital_request = capital_request or (
                _capital_request(assignment.parameters, quantity=quantity, action=action)
                if action in {"enter_long", "add_long", "enter_short", "add_short"}
                else None
            )
            intents = (
                StrategyIntent(
                    intent_id=event_id,
                    ticker=observation.ticker.upper(),
                    event_time=observation.observed_at,
                    action=action,  # type: ignore[arg-type]
                    quantity=quantity,
                    reference_price=observation.price,
                    capital_request=resolved_capital_request,
                    invalidation_price=invalidation_price,
                    profit_target_price=profit_target_price,
                    trailing_amount=trailing_amount,
                    execution_policy=_execution_policy_from_phase(
                        resolved_order_intent,
                        observation=observation,
                        action=action,
                        parameters=assignment.parameters,
                    ) if resolved_order_intent else None,
                    protection_profile=_protection_profile_from_phase(
                        resolved_order_intent,
                        observation=observation,
                        action=action,
                        quantity=quantity,
                        parameters=assignment.parameters,
                        state=state,
                        invalidation_price=invalidation_price,
                        profit_target_price=profit_target_price,
                        trailing_amount=trailing_amount,
                    ) if resolved_order_intent and action in {
                        "enter_long", "add_long", "enter_short", "add_short"
                    } else None,
                    urgency=str(assignment.parameters.get("execution", {}).get("entry_urgency") or "urgent") if action in {"enter_long", "add_long", "enter_short", "add_short"} else str(assignment.parameters.get("execution", {}).get("exit_urgency") or "very_urgent"),  # type: ignore[arg-type]
                    time_in_force="",
                    outside_rth=_outside_regular_hours(observation.observed_at),
                    reason=reason,
                    metadata={
                        "assignment_id": assignment.assignment_id,
                        "bid": observation.bid,
                        "ask": observation.ask,
                        "quote_observed_at": observation.observed_at.isoformat(),
                        "tick_size": float(
                            assignment.parameters.get("execution", {}).get("tick_size") or 0.01
                        ),
                        "position_quantity": observation.position_quantity,
                        "opportunity_score": score,
                        "volatility": observation.volatility,
                        "session_routing": "smart",
                        "eligible_sessions": list(
                            dict(
                                assignment.parameters.get("strategy_behavior")
                                or {}
                            ).get("eligible_sessions")
                            or ["regular"]
                        ),
                        **resolved_metadata,
                        **lineage,
                    },
                ),
            )
        payload = {
            "assignment_id": assignment.assignment_id,
            "strategy_id": assignment.strategy_id,
            "strategy_revision": assignment.strategy_revision,
            "ticker": observation.ticker.upper(),
            "effective_at": observation.observed_at.isoformat(),
            "event_id": event_id,
            "action": action,
            "direction": signal.direction,
            "score": signal.score,
            "confidence": signal.confidence,
            "reason": reason,
            "reason_detail": reason_detail,
            "reference_price": observation.price,
            "invalidation_price": invalidation_price,
            "status": status.value,
            "state": state,
            "evidence": resolved_metadata,
            **lineage,
        }
        return StrategyEngineResult(StrategyEvaluation(signals=(signal,), intents=intents), state, status, payload)


def _at_or_after_session_time(observed_at: datetime, configured: str) -> bool:
    if not configured:
        return False
    threshold = datetime.strptime(configured, "%H:%M:%S").time()
    return observed_at.astimezone(NEW_YORK).time().replace(tzinfo=None) >= threshold


def _outside_regular_hours(observed_at: datetime) -> bool:
    market_time = observed_at.astimezone(NEW_YORK).time().replace(tzinfo=None)
    return not (clock_time(9, 30) <= market_time < clock_time(16, 0))


def _capital_request(
    parameters: dict[str, Any],
    *,
    quantity: float,
    action: str,
) -> CapitalRequest:
    sizing = dict(parameters.get("sizing") or {})
    mode = str(sizing.get("request_mode") or "fixed_quantity")
    if mode not in {"fixed_quantity", "mandate_fraction", "risk_fraction", "all_available"}:
        raise ValueError(f"Unsupported strategy capital request mode: {mode}")
    value = (
        quantity
        if mode == "fixed_quantity"
        else float(sizing.get("request_value") or 1.0)
    )
    if action in {"add_long", "add_short"} and mode != "fixed_quantity":
        value *= float(sizing.get("add_fraction") or 0)
    return CapitalRequest(
        mode=mode,  # type: ignore[arg-type]
        value=value,
        allow_replacement=bool(sizing.get("allow_replacement", False)),
    )


def _capital_request_from_payload(payload: dict[str, Any]) -> CapitalRequest:
    mode = str(payload.get("mode") or "mandate_fraction")
    value = 1.0 if mode == "all_available" else float(payload.get("value") or 0)
    return CapitalRequest(
        mode=mode,  # type: ignore[arg-type]
        value=value,
        allow_replacement=bool(payload.get("allow_replacement", False)),
    )


def _phase_capital_request(
    parameters: dict[str, Any],
    phase_name: str,
    *,
    fallback_quantity: float,
) -> CapitalRequest:
    phase = dict(
        dict(parameters.get("phase_policy") or {}).get(phase_name) or {}
    )
    payload = dict(phase.get("capital_request") or {})
    if payload:
        return _capital_request_from_payload(payload)
    return CapitalRequest(mode="fixed_quantity", value=fallback_quantity)


def _execution_policy_from_phase(
    payload: dict[str, Any],
    *,
    observation: StrategyObservation,
    action: str,
    parameters: dict[str, Any],
) -> ExecutionPolicy:
    reference = str(
        payload.get("execution_policy") or ExecutionPolicyName.ADAPTIVE_REGULAR
    )
    catalog = dict(parameters.get("execution_policy_catalog") or {})
    configured = dict(catalog.get(reference) or {})
    name = ExecutionPolicyName(
        str(configured.get("name") or reference)
    )
    buying = action in {"enter_long", "add_long", "reduce_short", "cover"}
    if configured:
        envelope = dict(configured.get("envelope") or {})
        if int(payload.get("deadline_ms") or 0) > 0:
            envelope["deadline_ms"] = int(payload["deadline_ms"])
        if buying and name == ExecutionPolicyName.IMMEDIATE_WITH_LIMIT and envelope.get("maximum_buy_price") is None:
            envelope["maximum_buy_price"] = observation.price
        if not buying and name == ExecutionPolicyName.IMMEDIATE_WITH_LIMIT and envelope.get("minimum_sell_price") is None:
            envelope["minimum_sell_price"] = observation.price
        configured["envelope"] = envelope
        if payload.get("partial_fill_policy"):
            configured["partial_fill_policy"] = str(payload["partial_fill_policy"])
        return execution_policy_from_payload(configured)
    return ExecutionPolicy(
        policy_id=f"strategy-{name.value}",
        name=name,
        envelope=ExecutionEnvelope(
            maximum_buy_price=observation.price if buying and name == ExecutionPolicyName.IMMEDIATE_WITH_LIMIT else None,
            minimum_sell_price=observation.price if not buying and name == ExecutionPolicyName.IMMEDIATE_WITH_LIMIT else None,
            deadline_ms=int(payload.get("deadline_ms") or 750),
        ),
        partial_fill_policy=PartialFillPolicy(str(payload.get("partial_fill_policy") or "complete_remainder")),
        quote_source="qmd",
    )


def _protection_profile_from_phase(
    payload: dict[str, Any],
    *,
    observation: StrategyObservation,
    action: str,
    quantity: float,
    parameters: dict[str, Any],
    state: dict[str, Any],
    invalidation_price: float | None,
    profit_target_price: float | None,
    trailing_amount: float | None,
) -> ProtectionProfile | None:
    reference = str(payload.get("protection_profile") or "")
    configured = dict(
        dict(parameters.get("protection_profile_catalog") or {}).get(reference)
        or {}
    )
    if not configured:
        return None
    side = "short" if action in {"enter_short", "add_short"} else "long"
    strategy_targets = [
        float(value)
        for value in state.get("structural_profit_targets") or []
        if isinstance(value, (int, float)) and float(value) > 0
    ]
    configured_slices = [dict(raw) for raw in configured.get("slices") or []]
    has_indexed_slices = any(
        raw.get("strategy_profit_target_index") is not None
        for raw in configured_slices
    )
    indexed_slices = [
        raw
        for raw in configured_slices
        if raw.get("strategy_profit_target_index") is not None
        and 0 <= int(raw["strategy_profit_target_index"]) < len(strategy_targets)
    ]
    if has_indexed_slices and quantity >= 1:
        indexed_slices = indexed_slices[: max(1, floor(quantity + 1e-9))]
    if not has_indexed_slices:
        active_slices = configured_slices
    elif indexed_slices:
        active_slices = indexed_slices
    elif configured_slices:
        # No structural level qualified. Keep the complete quantity protected
        # as a runner without inventing a synthetic profit target.
        active_slices = [{
            **configured_slices[0],
            "strategy_profit_target_index": None,
            "profit_target_price": None,
            "use_strategy_profit_target": False,
        }]
    else:
        active_slices = []
    dynamic_fraction = 1.0 / len(active_slices) if has_indexed_slices and active_slices else 0.0
    slices: list[ProtectionSlice] = []
    for raw in active_slices:
        stop_raw = dict(raw.get("stop") or {})
        rule_type = StopRuleType(str(stop_raw.pop("rule_type", StopRuleType.FIXED_PRICE)))
        order_type = StopOrderType(str(stop_raw.pop("order_type", StopOrderType.STOP)))
        anchor_source = str(stop_raw.pop("anchor_source", "") or "")
        ordinal = str(stop_raw.pop("anchor_ordinal", "most_recent") or "most_recent")
        timeframe = str(stop_raw.pop("structural_timeframe", "") or "")
        raw_anchor_value = stop_raw.pop("anchor", None)
        anchor = None
        if anchor_source == "strategy_swing" or rule_type == StopRuleType.SWING_ANCHORED:
            anchor = _structural_anchor_from_state(
                state,
                observation=observation,
                side=side,
                ordinal=ordinal,
                timeframe=timeframe,
            )
            # A hybrid stop is explicitly able to fall back to its volatility
            # leg when no causal swing has formed yet. A purely swing-anchored
            # stop must continue to fail closed.
            if anchor is None and rule_type == StopRuleType.SWING_ANCHORED:
                raise ValueError(
                    f"Protection profile {reference} requires unavailable {ordinal} causal swing"
                )
        elif isinstance(raw_anchor_value, dict):
            raw_anchor = dict(raw_anchor_value)
            anchor = StructuralAnchor(
                observation_id=str(raw_anchor["observation_id"]),
                price=float(raw_anchor["price"]),
                confirmed_at=_aware_datetime(raw_anchor["confirmed_at"]),
                timeframe=str(raw_anchor.get("timeframe") or ""),
                ordinal=str(raw_anchor.get("ordinal") or ordinal),
            )
        if rule_type in {StopRuleType.FIXED_PRICE, StopRuleType.CATASTROPHIC} and not stop_raw.get("price"):
            stop_raw["price"] = invalidation_price
        stop = StopRule(
            rule_type=rule_type,
            order_type=order_type,
            anchor=anchor,
            **stop_raw,
        )
        trailing_raw = dict(raw.get("trailing") or {})
        trailing_rule = TrailingRuleType(
            str(trailing_raw.pop("rule_type", TrailingRuleType.NONE))
        )
        if trailing_rule == TrailingRuleType.BROKER_AMOUNT and not trailing_raw.get("amount"):
            trailing_raw["amount"] = trailing_amount
        trailing = TrailingRule(rule_type=trailing_rule, **trailing_raw)
        slices.append(
            ProtectionSlice(
                slice_id=str(raw.get("slice_id") or ""),
                quantity_fraction=(
                    dynamic_fraction
                    if has_indexed_slices
                    else float(raw.get("quantity_fraction") or 0)
                ),
                stop=stop,
                profit_target_price=(
                    strategy_targets[int(raw["strategy_profit_target_index"])]
                    if raw.get("strategy_profit_target_index") is not None
                    and 0 <= int(raw["strategy_profit_target_index"]) < len(strategy_targets)
                    else
                    profit_target_price
                    if bool(raw.get("use_strategy_profit_target"))
                    else float(raw["profit_target_price"])
                    if raw.get("profit_target_price") is not None
                    else None
                ),
                trailing=trailing,
            )
        )
    return ProtectionProfile(
        profile_id=str(configured.get("profile_id") or reference),
        revision=int(configured.get("revision") or 1),
        slices=tuple(slices),
        add_policy=AddProtectionPolicy(
            str(configured.get("add_policy") or AddProtectionPolicy.INDEPENDENT_SLICE)
        ),
        profit_pocket_transition=ProfitPocketTransition(
            str(configured.get("profit_pocket_transition") or ProfitPocketTransition.KEEP_EXISTING)
        ),
        mandatory_catastrophic_backstop=bool(
            configured.get("mandatory_catastrophic_backstop", True)
        ),
        emergency_repair_deadline_ms=int(
            configured.get("emergency_repair_deadline_ms") or 500
        ),
    )


def _missing_protection_anchor(
    payload: dict[str, Any],
    *,
    observation: StrategyObservation,
    parameters: dict[str, Any],
    state: dict[str, Any],
    side: str,
) -> str:
    reference = str(payload.get("protection_profile") or "")
    configured = dict(
        dict(parameters.get("protection_profile_catalog") or {}).get(reference)
        or {}
    )
    for raw in configured.get("slices") or []:
        stop = dict(raw.get("stop") or {})
        rule_type = str(stop.get("rule_type") or "")
        anchor_source = str(stop.get("anchor_source") or "")
        if rule_type != StopRuleType.SWING_ANCHORED:
            continue
        ordinal = str(stop.get("anchor_ordinal") or "most_recent")
        if _structural_anchor_from_state(
            state,
            observation=observation,
            side=side,
            ordinal=ordinal,
            timeframe=str(stop.get("structural_timeframe") or ""),
        ) is None:
            return ordinal
    return ""


def _record_structural_anchors(
    state: dict[str, Any], observation: StrategyObservation
) -> None:
    anchors = dict(state.get("structural_anchors") or {})
    for side, price in (("long", observation.swing_low), ("short", observation.swing_high)):
        if price is None or price <= 0:
            continue
        rows = list(anchors.get(side) or [])
        if rows and abs(float(rows[0].get("price") or 0) - float(price)) < 1e-12:
            continue
        rows.insert(0, {
            "observation_id": (
                str(observation.source_signal_ids[0])
                if observation.source_signal_ids
                else f"{observation.ticker}:{side}:{observation.observed_at.isoformat()}"
            ),
            "price": float(price),
            "confirmed_at": observation.observed_at.isoformat(),
            "timeframe": "strategy",
        })
        anchors[side] = rows[:8]
    state["structural_anchors"] = anchors


def _structural_anchor_from_state(
    state: dict[str, Any],
    *,
    observation: StrategyObservation,
    side: str,
    ordinal: str,
    timeframe: str,
) -> StructuralAnchor | None:
    rows = list(dict(state.get("structural_anchors") or {}).get(side) or [])
    index = {
        "most_recent": 0,
        "second_recent": 1,
        "third_recent": 2,
        "fourth_recent": 3,
    }.get(ordinal, 0)
    if index >= len(rows):
        return None
    raw = rows[index]
    return StructuralAnchor(
        observation_id=str(raw.get("observation_id") or ""),
        price=float(raw.get("price") or 0),
        confirmed_at=_aware_datetime(raw.get("confirmed_at")),
        timeframe=timeframe or str(raw.get("timeframe") or "strategy"),
        ordinal=ordinal,
    )


def _aware_datetime(value: Any) -> datetime:
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    )
    if parsed.tzinfo is None:
        raise ValueError("Structural anchor confirmation time must include a timezone")
    return parsed


def _optional_aware_datetime(value: Any) -> datetime | None:
    if value in {None, ""}:
        return None
    try:
        return _aware_datetime(value)
    except (TypeError, ValueError):
        return None


def _validate_phase_capital_request(payload: dict[str, Any]) -> None:
    _capital_request_from_payload(payload)
    mode = str(payload.get("mode") or "")
    value = float(payload.get("value") or 0)
    if mode in {"mandate_fraction", "risk_fraction"} and not 0 < value <= 1:
        raise ValueError("Strategy capital-request fraction must be between zero and one")
    if mode == "fixed_quantity" and value <= 0:
        raise ValueError("Strategy fixed-quantity request must be positive")


def _validate_phase_order_intent(payload: dict[str, Any]) -> None:
    if not str(payload.get("execution_policy") or "").strip():
        raise ValueError("Strategy phase execution policy is required")
    PartialFillPolicy(str(payload.get("partial_fill_policy") or ""))
    if "time_in_force" in payload or "outside_rth" in payload:
        raise ValueError(
            "Strategy phase session routing must be derived by OMS"
        )
    if int(payload.get("deadline_ms") or 0) < 0:
        raise ValueError("Strategy phase execution deadline cannot be negative")


def _strategy_side(parameters: dict[str, Any]) -> str:
    side = str(dict(parameters.get("strategy_behavior") or {}).get("side") or "long")
    if side not in {"long", "short"}:
        raise ValueError("Strategy side must be long or short")
    return side


class AssignedLongMomentumStrategy:
    """Runtime strategy adapter; enriched observations are its only decision input."""

    strategy_id = STRATEGY_ID
    revision = STRATEGY_REVISION
    automatic = True

    def __init__(self, assignments: list[StrategyAssignment]) -> None:
        self._campaigns = StrategyCampaignOrchestrator(assignments)
        self._assignments = {
            (assignment.account_id, assignment.ticker.upper()): assignment
            for assignment in assignments
        }
        if len(self._assignments) != len(assignments):
            raise ValueError(
                "A Strategy Campaign may have only one active account leg per ticker and account"
            )
        self._engine = LongMomentumStrategyEngine()

    def bind_campaign_registry(
        self, registry: StrategyCampaignOrchestrator
    ) -> None:
        """Bind this run to the process-wide campaign lease authority."""

        for assignment in self._assignments.values():
            registry.register(assignment)
        self._campaigns = registry

    async def on_event(self, event: MarketEvent, account_id: str) -> StrategyEvaluation:
        # Raw trades and quotes update QMD and bar authorities. The strategy
        # evaluates only their normalized causal observations.
        return StrategyEvaluation()

    async def on_observation(
        self,
        observation: StrategyObservation,
        account_id: str,
    ) -> StrategyEvaluation:
        key = (account_id, observation.ticker.upper())
        assignment = self._assignments.get(key)
        if assignment is None:
            return StrategyEvaluation()
        if not self._campaigns.can_evaluate(assignment):
            return StrategyEvaluation()
        result = self._engine.evaluate(assignment, observation)
        updated = StrategyAssignment(
            assignment_id=assignment.assignment_id,
            strategy_id=assignment.strategy_id,
            strategy_revision=assignment.strategy_revision,
            account_id=assignment.account_id,
            ticker=assignment.ticker,
            conid=assignment.conid,
            status=result.status,
            permissions=assignment.permissions,
            parameters=assignment.parameters,
            state=result.state,
            source=assignment.source,
            created_at=assignment.created_at,
            updated_at=observation.observed_at,
        )
        self._assignments[key] = updated
        self._campaigns.register(updated)
        return result.evaluation

    def assignments(self) -> tuple[StrategyAssignment, ...]:
        return tuple(self._assignments.values())

    async def on_intent_rejected(
        self,
        intent: StrategyIntent,
        *,
        reasons: tuple[str, ...],
        event_time: datetime,
    ) -> None:
        """Clear a pending state when Portfolio never authorized an order."""

        assignment_id = str(intent.metadata.get("assignment_id") or "")
        if not assignment_id:
            return
        for key, assignment in self._assignments.items():
            if assignment.assignment_id != assignment_id:
                continue
            state = dict(assignment.state)
            if str(intent.metadata.get("execution_role") or "") == "target_replenishment":
                restored = float(
                    state.pop("target_replenishment_pending_quantity", 0.0)
                    or intent.quantity
                    or 0.0
                )
                state["target_replenishment_quantity"] = float(
                    state.get("target_replenishment_quantity") or 0
                ) + restored
                state["target_replenishment_pending"] = False
                state["last_intent_rejection"] = {
                    "intent_id": intent.intent_id,
                    "reasons": list(reasons),
                    "rejected_at": event_time.isoformat(),
                    "execution_role": "target_replenishment",
                }
                updated = replace(
                    assignment,
                    status=(
                        AssignmentStatus.MANAGING
                        if str(intent.action) == "add_long"
                        else AssignmentStatus.REENTRY_COOLDOWN
                    ),
                    state=state,
                    updated_at=event_time,
                )
                self._assignments[key] = updated
                self._campaigns.register(updated)
                return
            state["last_intent_rejection"] = {
                "intent_id": intent.intent_id,
                "reasons": list(reasons),
                "rejected_at": event_time.isoformat(),
            }
            # These fields describe an accepted entry campaign.  Keeping them
            # after Portfolio rejected the intent produces a phantom position
            # and prevents a later causal observation from retrying.
            for field_name in (
                "entry_reference_price",
                "entry_at",
                "initial_stop",
                "active_stop",
                "high_water_price",
                "low_water_price",
                "structural_profit_targets",
            ):
                state.pop(field_name, None)
            state["entries"] = max(0, int(state.get("entries") or 0) - 1)
            next_status = (
                AssignmentStatus.REENTRY_COOLDOWN
                if int(state.get("reentries") or 0) > 0
                else AssignmentStatus.WATCHING
            )
            updated = replace(
                assignment,
                status=next_status,
                state=state,
                updated_at=event_time,
            )
            self._assignments[key] = updated
            self._campaigns.register(updated)
            return

    def upsert_assignment(self, assignment: StrategyAssignment) -> None:
        key = (assignment.account_id, assignment.ticker.upper())
        current = self._assignments.get(key)
        if (
            current is not None
            and current.assignment_id != assignment.assignment_id
            and current.status
            not in {
                AssignmentStatus.DISABLED,
                AssignmentStatus.COMPLETED,
                AssignmentStatus.ERROR,
            }
        ):
            raise ValueError(
                f"{assignment.ticker} already has an active campaign leg for {assignment.account_id}"
            )
        self._campaigns.register(assignment)
        self._assignments[key] = assignment

    def command_assignment(
        self,
        assignment_id: str,
        command: str,
        *,
        event_time: datetime,
        detail: dict[str, Any] | None = None,
    ) -> StrategyAssignment:
        normalized = command.strip().lower()
        status_map = {
            "arm": AssignmentStatus.WATCHING,
            "resume": AssignmentStatus.WATCHING,
            "pause": AssignmentStatus.PAUSED,
            "disable": AssignmentStatus.DISABLED,
            "complete": AssignmentStatus.COMPLETED,
        }
        if normalized not in {
            *status_map,
            "disable_after_exit",
            "request_entry",
            "force_entry",
            "request_exit",
            "exit_and_stop",
            "exit_keep_watching",
        }:
            raise ValueError(f"Unsupported strategy assignment command: {command}")
        for key, assignment in self._assignments.items():
            if assignment.assignment_id != assignment_id:
                continue
            state = dict(assignment.state)
            if normalized == "disable_after_exit":
                state["disable_after_exit"] = True
            elif normalized == "request_entry":
                state["manual_entry_requested"] = True
            elif normalized == "force_entry":
                state["force_entry_requested"] = True
            elif normalized in {
                "request_exit",
                "exit_and_stop",
                "exit_keep_watching",
            }:
                state["manual_exit_requested"] = True
                state["disable_after_exit"] = normalized == "exit_and_stop"
            if detail:
                state["last_command_detail"] = dict(detail)
            updated = replace(
                assignment,
                status=status_map.get(normalized, assignment.status),
                state=state,
                updated_at=event_time,
            )
            self._assignments[key] = updated
            self._campaigns.register(updated)
            return updated
        raise KeyError(assignment_id)

    async def on_order_group_update(
        self,
        snapshot: Any,
        *,
        aggregate_position_quantity: float | None = None,
    ) -> None:
        assignment_id = str(getattr(snapshot, "assignment_id", "") or "")
        snapshot_state = str(getattr(snapshot, "state", ""))
        incremental_fill = float(
            getattr(snapshot, "fill_incremental_quantity", 0.0) or 0.0
        )
        # Protected children can fill incrementally while the parent order
        # group remains PARTIALLY_FILLED (for example when the entry itself
        # completed only partially before its deadline).  The fill callback is
        # authoritative for each positive increment, so consume it now rather
        # than waiting for a terminal group state that may never become
        # FILLED.  Otherwise profit-target replenishment silently loses the
        # target fills that created its entitlement.
        if (
            not assignment_id
            or snapshot_state not in {"filled", "cancelled"}
            and incremental_fill <= 0
        ):
            return
        for key, assignment in self._assignments.items():
            if assignment.assignment_id != assignment_id:
                continue
            state = dict(assignment.state)
            action = str(getattr(snapshot, "action", ""))
            if snapshot_state == "cancelled" and action in {
                "enter_long",
                "enter_short",
                "add_long",
                "add_short",
            }:
                filled_quantity = float(getattr(snapshot, "filled_quantity", 0.0) or 0.0)
                if filled_quantity > 0 or float(aggregate_position_quantity or 0.0) != 0:
                    status = AssignmentStatus.MANAGING
                else:
                    for field_name in (
                        "entry_reference_price",
                        "entry_at",
                        "initial_stop",
                        "active_stop",
                        "high_water_price",
                        "low_water_price",
                        "structural_profit_targets",
                    ):
                        state.pop(field_name, None)
                    state["entries"] = max(0, int(state.get("entries") or 0) - 1)
                    state["last_entry_order_cancelled"] = {
                        "intent_id": str(getattr(snapshot, "intent_id", "") or ""),
                        "cancelled_at": getattr(snapshot, "updated_at").isoformat(),
                        "reason": "execution_deadline",
                    }
                    status = (
                        AssignmentStatus.REENTRY_COOLDOWN
                        if int(state.get("reentries") or 0) > 0
                        else AssignmentStatus.WATCHING
                    )
                updated = replace(
                    assignment,
                    status=status,
                    state=state,
                    updated_at=getattr(snapshot, "updated_at"),
                )
                self._assignments[key] = updated
                self._campaigns.register(updated)
                return
            if action in {"enter_long", "add_long", "enter_short", "add_short"}:
                if state.get("target_replenishment_pending"):
                    state["target_replenishment_pending"] = False
                    state.pop("target_replenishment_pending_quantity", None)
                    state["target_replenishments"] = int(
                        state.get("target_replenishments") or 0
                    ) + 1
                status = AssignmentStatus.MANAGING
            elif action in {"reduce_long", "reduce_short"}:
                status = AssignmentStatus.MANAGING
            elif action in {"exit", "take_profit", "cover"}:
                fill_role = str(getattr(snapshot, "fill_role", "") or "")
                incremental = incremental_fill
                if fill_role == "profit_target" and incremental > 0:
                    targets = [
                        float(value)
                        for value in state.get("structural_profit_targets") or []
                        if isinstance(value, (int, float)) and float(value) > 0
                    ]
                    slice_id = str(getattr(snapshot, "slice_id", "") or "")
                    try:
                        target_index = max(0, int(slice_id.rsplit("-", 1)[-1]) - 1)
                    except (TypeError, ValueError):
                        target_index = 0
                    target_level = (
                        targets[target_index]
                        if target_index < len(targets)
                        else targets[-1]
                        if targets
                        else float(state.get("high_water_price") or 0)
                    )
                    state["target_replenishment_quantity"] = float(
                        state.get("target_replenishment_quantity") or 0
                    ) + incremental
                    state["target_replenishment_level_price"] = target_level
                    state["target_replenishment_peak_price"] = target_level
                    state["target_replenishment_armed_at"] = getattr(
                        snapshot, "updated_at", datetime.now(timezone.utc)
                    ).isoformat()
                    state["last_profit_target_fill"] = {
                        "slice_id": slice_id,
                        "quantity": incremental,
                        "level_price": target_level,
                        "filled_at": state["target_replenishment_armed_at"],
                    }
                if (
                    aggregate_position_quantity is not None
                    and abs(float(aggregate_position_quantity)) > 1e-9
                ):
                    # A protected position can have several independently held
                    # target/stop slices. One child fill reduces the position;
                    # it does not terminate the campaign.
                    status = AssignmentStatus.MANAGING
                elif assignment.status in {
                    AssignmentStatus.REENTRY_COOLDOWN,
                    AssignmentStatus.COMPLETED,
                }:
                    return
                elif _fill_allows_reentry(assignment, snapshot, state):
                    state["reentries"] = int(state.get("reentries") or 0) + 1
                    state["last_exit_at"] = getattr(
                        snapshot, "updated_at", datetime.now(timezone.utc)
                    ).isoformat()
                    status = AssignmentStatus.REENTRY_COOLDOWN
                else:
                    status = AssignmentStatus.COMPLETED
            else:
                return
            updated = StrategyAssignment(
                assignment_id=assignment.assignment_id,
                strategy_id=assignment.strategy_id,
                strategy_revision=assignment.strategy_revision,
                account_id=assignment.account_id,
                ticker=assignment.ticker,
                conid=assignment.conid,
                status=status,
                permissions=assignment.permissions,
                parameters=assignment.parameters,
                state=state,
                source=assignment.source,
                created_at=assignment.created_at,
                updated_at=getattr(snapshot, "updated_at", datetime.now(timezone.utc)),
            )
            self._assignments[key] = updated
            self._campaigns.register(updated)
            return


def _fill_allows_reentry(
    assignment: StrategyAssignment,
    snapshot: Any,
    state: dict[str, Any],
) -> bool:
    if state.get("disable_after_exit") or not assignment.permissions.reenter:
        return False
    reentry = dict(resolve_long_momentum_parameters(assignment.parameters).get("reentry") or {})
    if not bool(reentry.get("enabled", True)):
        return False
    fill_role = str(getattr(snapshot, "fill_role", "") or "")
    if fill_role in {"protective_stop", "trailing_stop", "protective_exit"}:
        return bool(reentry.get("after_protective_exit", False))
    if fill_role == "profit_target":
        return True
    return bool(getattr(snapshot, "reentry_after_fill", False))


def _trigger_reference(
    rules: dict[str, Any],
    result: dict[str, Any],
    observation: StrategyObservation,
) -> tuple[str, float | None, float]:
    matched = set(dict(result.get("trigger") or {}).get("matched_groups") or [])
    trigger_stage = dict(rules.get("trigger") or {})
    for group in trigger_stage.get("rule_sets") or trigger_stage.get("groups") or []:
        group_id = str(group.get("rule_set_id") or group.get("group_id") or "")
        if group_id not in matched:
            continue
        for condition in group.get("conditions") or []:
            if str(condition.get("comparator") or "") != "above_by_bps":
                continue
            source_id = str(
                condition.get("right_field_ref")
                or condition.get("right_source_id")
                or ""
            )
            value = _condition_operand_value(condition, "right", observation)
            if value is not None:
                return source_id, float(value), float(condition.get("value") or 0)
    return "", None, 0.0


def _configured_trigger_reference(
    rules: dict[str, Any], observation: StrategyObservation
) -> tuple[str, float | None, float]:
    trigger_stage = dict(rules.get("trigger") or {})
    for group in trigger_stage.get("rule_sets") or trigger_stage.get("groups") or []:
        if not bool(group.get("enabled", True)):
            continue
        for condition in group.get("conditions") or []:
            if (
                not bool(condition.get("enabled", True))
                or str(condition.get("comparator") or "") != "above_by_bps"
            ):
                continue
            source_id = str(
                condition.get("right_source_id")
                or condition.get("right_field_ref")
                or ""
            )
            value = _condition_operand_value(condition, "right", observation)
            return (
                source_id,
                float(value) if value is not None else None,
                float(condition.get("value") or 0),
            )
    return "", None, 0.0


def evaluate_entry_decision_rules(
    rules: dict[str, Any],
    observation: StrategyObservation,
) -> dict[str, Any]:
    resolved = rules or default_entry_decision_rules()
    output: dict[str, Any] = {}
    for stage_name in ("trigger", "confirmation", "veto"):
        stage = dict(resolved.get(stage_name) or {})
        group_results: dict[str, bool] = {}
        group_scores: dict[str, float] = {}
        condition_evidence: dict[str, list[dict[str, Any]]] = {}
        rule_sets = stage.get("rule_sets") or stage.get("groups") or []
        for group in rule_sets:
            if not bool(group.get("enabled", True)):
                continue
            group_id = str(group.get("rule_set_id") or group.get("group_id") or "")
            enabled_conditions = [
                dict(condition)
                for condition in group.get("conditions") or []
                if bool(condition.get("enabled", True))
            ]
            condition_rows = [
                _condition_evidence(condition, observation)
                for condition in enabled_conditions
            ]
            condition_results = [bool(row["passed"]) for row in condition_rows]
            operator = str(group.get("operator") or "all")
            score = (
                sum(1 for matched in condition_results if matched)
                / len(condition_results)
                if condition_results
                else 0.0
            )
            passed = bool(condition_results) and (
                all(condition_results)
                if operator == "all"
                else score >= float(group.get("required_score") or 0)
                if operator == "score"
                else any(condition_results)
            )
            group_results[group_id] = passed
            group_scores[group_id] = score
            condition_evidence[group_id] = condition_rows
        matched = [group_id for group_id, passed in group_results.items() if passed]
        expression = dict(stage.get("expression") or {})
        operator = str(stage.get("operator") or "any")
        score = len(matched) / len(group_results) if group_results else 0.0
        passed = (
            _rule_expression_passed(expression, group_results)
            if expression
            else bool(group_results) and (
                all(group_results.values()) if operator == "all" else bool(matched)
            )
        )
        output[stage_name] = {
            "groups": group_results,
            "group_scores": group_scores,
            "condition_evidence": condition_evidence,
            "matched_groups": matched,
            "operator": operator,
            "passed": passed,
            "score": score,
        }
    return output


def entry_stage_without_rule_set(
    stage: Mapping[str, Any], rule_set_id: str
) -> dict[str, Any]:
    """Remove one compiled rule set and every Boolean-expression reference."""

    normalized = dict(stage)
    normalized["rule_sets"] = [
        dict(rule_set)
        for rule_set in normalized.get("rule_sets") or []
        if str(rule_set.get("rule_set_id") or "") != rule_set_id
    ]

    def prune(node: Mapping[str, Any]) -> dict[str, Any]:
        current = dict(node)
        if (
            str(current.get("kind") or "") == "rule_set"
            and str(current.get("rule_set_id") or "") == rule_set_id
        ):
            return {}
        if str(current.get("kind") or "") != "operator":
            return current
        children = [
            child
            for raw in current.get("children") or []
            if isinstance(raw, Mapping)
            for child in (prune(raw),)
            if child
        ]
        return {**current, "children": children} if children else {}

    expression = normalized.get("expression")
    if isinstance(expression, Mapping):
        normalized["expression"] = prune(expression)
    return normalized


def _condition_evidence(
    condition: dict[str, Any], observation: StrategyObservation
) -> dict[str, Any]:
    left_source = str(
        condition.get("left_field_ref") or condition.get("left_source_id") or ""
    )
    right_source = str(
        condition.get("right_field_ref") or condition.get("right_source_id") or ""
    )
    left_timeframe = _condition_interval_expression(
        condition.get("left_interval") or condition.get("left_timeframe")
    )
    right_timeframe = _condition_interval_expression(
        condition.get("right_interval") or condition.get("right_timeframe")
    )
    left_value = _condition_operand_value(condition, "left", observation)
    right_value = (
        _condition_operand_value(condition, "right", observation)
        if right_source
        else condition.get("value")
    )
    return {
        "condition_id": str(condition.get("condition_id") or ""),
        "left_source_id": left_source,
        "left_timeframe": left_timeframe,
        "left_value": left_value,
        "comparator": str(condition.get("comparator") or ""),
        "right_source_id": right_source,
        "right_timeframe": right_timeframe,
        "right_value": right_value,
        "buffer_bps": (
            float(condition.get("value") or 0)
            if str(condition.get("comparator") or "") == "above_by_bps"
            else None
        ),
        "passed": _condition_matches(condition, observation),
    }


def _display_value(value: Any) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def _failed_entry_conditions(metadata: dict[str, Any]) -> list[str]:
    rules = dict(metadata.get("entry_rules") or {})
    failures: list[str] = []
    for stage_name in ("confirmation", "trigger", "veto"):
        stage = dict(rules.get(stage_name) or {})
        for group_id, rows in dict(stage.get("condition_evidence") or {}).items():
            for row in rows or []:
                passed = bool(row.get("passed"))
                failed = not passed if stage_name != "veto" else passed
                if not failed:
                    continue
                left = str(row.get("left_source_id") or row.get("condition_id") or "condition")
                timeframe = str(row.get("left_timeframe") or "")
                comparator = str(row.get("comparator") or "condition")
                right = _display_value(row.get("right_value"))
                buffer_bps = row.get("buffer_bps")
                requirement = (
                    f"above {right} by {float(buffer_bps):g} bps"
                    if comparator == "above_by_bps" and buffer_bps is not None
                    else f"{comparator.replace('_', ' ')} {right}"
                )
                failures.append(
                    f"{group_id}: {left}{f' ({timeframe})' if timeframe else ''} "
                    f"is {_display_value(row.get('left_value'))}; requires {requirement}"
                )
    return failures


def _decision_reason_detail(
    action: str,
    reason: str,
    observation: StrategyObservation,
    metadata: dict[str, Any],
    state: dict[str, Any],
) -> str:
    prefix = "Wait" if action == "wait" else "Hold" if action == "hold" else "Act"
    if reason in {"entry_confirmation_incomplete", "entry_vetoed"}:
        failures = _failed_entry_conditions(metadata)
        label = "entry veto passed" if reason == "entry_vetoed" else "entry confirmation is incomplete"
        return f"{prefix}: {label}" + (f" — {'; '.join(failures[:6])}" if failures else "")
    if reason == "waiting_for_swing_high_reference":
        return "Wait: no causally confirmed one-second swing high is available yet."
    if reason == "waiting_for_swing_high_cross":
        return (
            "Wait: price "
            f"{observation.price:.4g} has not crossed the one-second swing-high threshold "
            f"{_display_value(metadata.get('trigger_threshold_price'))}."
        )
    if reason == "waiting_for_unified_resistance":
        return "Wait: no important causal resistance zone is available in the Unified Structural Level Book."
    if reason == "waiting_for_unified_resistance_break":
        trigger = dict(metadata.get("unified_structural_trigger") or {})
        return (
            "Wait: price "
            f"{observation.price:.4g} has not causally cleared Unified resistance "
            f"{_display_value(trigger.get('threshold_price'))}; previous price "
            f"{_display_value(trigger.get('previous_price'))}."
        )
    if reason == "liquidity_admission_incomplete":
        detail = dict(metadata.get("liquidity_admission") or {})
        failed = ", ".join(str(value) for value in detail.get("failed") or [])
        return f"Wait: liquidity admission is not yet earned — failed: {failed or 'required evidence unavailable'}."
    if reason == "current_execution_quality_incomplete":
        detail = dict(metadata.get("execution_quality") or {})
        failed = ", ".join(str(value) for value in detail.get("failed") or [])
        return f"Wait: current order execution is unsafe — failed: {failed or 'spread or activity unavailable'}."
    if reason == "waiting_for_target_replenishment_pullback":
        detail = dict(metadata.get("target_replenishment") or {})
        failed = ", ".join(str(value) for value in detail.get("failed") or [])
        return f"Hold: profit target filled; replenishment is armed but waiting — {failed or 'pullback confirmation unavailable'}."
    if reason == "target_replenishment_fill_pending":
        return "Hold: a profit-target replenishment order is working; duplicate replenishment is prohibited."
    if reason == "waiting_for_renewed_early_squeeze":
        return (
            "Wait: re-entry requires a new Early Squeeze Move occurrence after the last "
            f"completed exit; stream {metadata.get('required_signal_stream_id') or 'price-squeeze-early'} has not renewed."
        )
    if reason == "reentry_confirmation_not_fresh":
        return "Wait: the re-entry confirmation inputs were observed at or before the last exit."
    if reason == "reentry_cooldown":
        return "Wait: the configured post-exit re-entry cooldown has not elapsed."
    if reason == "entry_fill_pending":
        return "Wait: an entry order is working; a duplicate entry is prohibited until its fill state is final."
    if reason == "exit_fill_pending":
        return "Hold: an exit or reduction order is working; no second exit is emitted until its fill state is final."
    if reason == "position_managed":
        return (
            "Hold: no configured exit condition passed; "
            f"price={observation.price:.4g}, active stop={_display_value(state.get('active_stop'))}, "
            f"gain={float(metadata.get('gain_pct') or 0):+.3f}%."
        )
    labels = {
        "entry_confirmed": "Enter: latched liquidity, executable spread/activity, VWAP, exact positive/open one-second MACD, and Unified resistance acceptance all passed.",
        "reentry_confirmed": "Re-enter: executable spread/activity, VWAP, exact positive/open one-second MACD, and a fresh Unified resistance recovery all passed.",
        "target_profit_replenishment": "Profit-target replenishment: a target filled, price made a causal pullback, Unified support held, and VWAP plus exact positive/open one-second MACD remained valid.",
        "failure_to_extend_partial": "Profit reduction: price stopped extending while QMD flow deteriorated; sell half and keep the protected remainder.",
        "qmd_flow_geometry_exhaustion": "Exit: QMD flow structure weakened with confident flow-price divergence.",
        "loss_of_confirmed_higher_low": "Exit: price lost the latest causally confirmed one-second higher low.",
        "macd_closed_backstop": "Exit: one-second MACD remained closed for the configured backstop duration.",
        "macd_signal_crossed_above_line": "Exit: the causal one-second MACD signal crossed strictly above the MACD line.",
        "downside_bearish_choch": "Loss exit: while below entry, a bearish one-second change of character occurred.",
        "downside_macd_closed": "Loss exit: while below entry, the one-second MACD closed; no confirmation delay applies.",
        "downside_vwap_lost": "Loss exit: while below entry, price moved under the causal one-second VWAP.",
        "protective_stop": "Exit: price breached the active causal protective stop.",
        "session_flatten": "Exit: the configured premarket flatten time was reached.",
        "profit_pocket": "Profit exit: the configured favorable-move and momentum-slowdown rule passed.",
        "no_active_rule_source_updated": "Wait: this observation did not update any source used by the active strategy phase.",
        "market_not_open": "Wait: the configured extended-hours execution session is not tradable at this clock.",
        "entry_cutoff_reached": "Wait: the configured entry cutoff has passed; no new exposure is allowed.",
        "protection_anchor_unavailable": "Wait: the causal structural anchor required to protect the order is unavailable.",
        "assignment_not_active": "Wait: the strategy assignment is disabled, completed, or in error.",
        "assignment_paused": "Wait: the strategy assignment is paused by operator authority.",
        "entry_not_authorized": "Wait: this assignment is not authorized to open exposure.",
    }
    return labels.get(reason, f"{prefix}: {reason.replace('_', ' ')}.")


def strategy_observation_source_values(
    observation: StrategyObservation,
    timeframe: str,
) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for source in strategy_input_catalog():
        supported = list(source["timeframes"])
        if supported not in (["event"], ["session"]) and timeframe not in supported:
            continue
        value = _observation_field_value(observation, str(source["runtime_field"]))
        if value is None:
            continue
        source_timeframe = (
            str(source["timeframes"][0])
            if source["timeframes"] in (["event"], ["session"])
            else timeframe
        )
        values[f"{source['source_id']}@{source_timeframe}"] = {
            "observed_at": observation.observed_at.isoformat(),
            "value": value,
        }
    return values


def _condition_matches(condition: dict[str, Any], observation: StrategyObservation) -> bool:
    left = _condition_operand_value(condition, "left", observation)
    if left is None:
        return False
    comparator = str(condition.get("comparator") or "")
    if comparator == "is_true":
        return bool(left)
    right_source_id = str(
        condition.get("right_field_ref") or condition.get("right_source_id") or ""
    )
    right = (
        _condition_operand_value(condition, "right", observation)
        if right_source_id
        else condition.get("value")
    )
    if right is None:
        return False
    if comparator == "equals":
        return left == right
    if comparator == "not_equals":
        return left != right
    try:
        left_number = float(left)
        right_number = float(right)
    except (TypeError, ValueError):
        return False
    if comparator == "above_by_bps":
        return left_number >= right_number * (1 + float(condition.get("value") or 0) / 10_000)
    if comparator == "greater_than":
        return left_number > right_number
    if comparator == "greater_or_equal":
        return left_number >= right_number
    if comparator == "less_than":
        return left_number < right_number
    if comparator == "less_or_equal":
        return left_number <= right_number
    return False


def _condition_operand_value(
    condition: dict[str, Any], side: str, observation: StrategyObservation
) -> Any:
    field_ref = str(condition.get(f"{side}_field_ref") or "")
    source_id = str(condition.get(f"{side}_source_id") or "")
    interval = _condition_interval_expression(
        condition.get(f"{side}_interval")
        or condition.get(f"{side}_timeframe")
    )
    aggregation = str(condition.get(f"{side}_aggregation") or "")
    candidates = []
    for identity in (field_ref, source_id):
        if not identity:
            continue
        if interval and aggregation:
            candidates.append(f"{identity}@{interval}#{aggregation}")
        if interval:
            candidates.append(f"{identity}@{interval}")
        candidates.append(identity)
    for candidate in candidates:
        cached = observation.source_values.get(candidate)
        if isinstance(cached, dict):
            if cached.get("value") is not None:
                return cached.get("value")
        elif cached is not None:
            return cached
    return _source_value(observation, source_id or field_ref, interval)


def _condition_interval_expression(value: Any) -> str:
    if isinstance(value, str):
        return value.strip().lower()
    if not isinstance(value, dict):
        return ""
    count = value.get("value")
    unit = str(value.get("unit") or "").strip().lower()
    if count in (None, "") or not unit:
        return ""
    aliases = {
        "millisecond": "ms", "milliseconds": "ms", "ms": "ms",
        "second": "s", "seconds": "s", "s": "s",
        "minute": "m", "minutes": "m", "m": "m",
        "hour": "h", "hours": "h", "h": "h",
        "day": "d", "days": "d", "d": "d",
        "week": "w", "weeks": "w", "w": "w",
        "month": "mo", "months": "mo", "mo": "mo",
    }
    suffix = aliases.get(unit)
    return f"{count}{suffix}" if suffix else ""


def _source_value(
    observation: StrategyObservation,
    source_id: str,
    timeframe: str,
) -> Any:
    cached = observation.source_values.get(f"{source_id}@{timeframe}")
    if cached is None:
        cached = observation.source_values.get(source_id)
    if isinstance(cached, dict):
        return cached.get("value")
    if cached is not None:
        return cached
    source = next(
        (row for row in strategy_input_catalog() if row["source_id"] == source_id),
        None,
    )
    if source is None:
        return None
    supported = set(source["timeframes"])
    if (
        timeframe not in {"", "event", "session"}
        and observation.source_timeframe
        and timeframe != observation.source_timeframe
        and supported not in ({"event"}, {"session"})
    ):
        return None
    return _observation_field_value(observation, str(source["runtime_field"]))


def _observation_field_value(observation: StrategyObservation, runtime_field: str) -> Any:
    if runtime_field == "bullish_choch":
        return observation.structure_event == "choch" and observation.structure_direction == "bullish"
    if runtime_field == "bearish_choch":
        return observation.structure_event == "choch" and observation.structure_direction == "bearish"
    return getattr(observation, runtime_field, None)


def _matching_exit_route(
    routes: list[dict[str, Any]],
    *,
    observation: StrategyObservation,
    protective_stop: float,
    failed_breakout: bool,
    side: str,
) -> dict[str, Any] | None:
    for route in sorted(
        (row for row in routes if bool(row.get("enabled", True))),
        key=lambda row: (-int(row.get("priority") or 0), str(row.get("route_id") or "")),
    ):
        mechanism = str(route.get("mechanism") or "")
        settings = dict(route.get("settings") or {})
        if mechanism == "protective_stop" and (
            observation.price <= protective_stop
            if side == "long"
            else observation.price >= protective_stop
        ):
            return route
        rules = dict(route.get("rules") or {})
        rules_passed = (
            _rule_stage_passed(rules, observation)
            if list(rules.get("groups") or [])
            else True
        )
        if mechanism == "failed_breakout" and failed_breakout and rules_passed:
            return route
        if mechanism == "bearish_qmd_macd":
            bearish_qmd = (
                observation.qmd_score <= float(settings.get("qmd_score") or -0.35)
                and observation.qmd_confidence
                >= float(settings.get("qmd_confidence") or 0.55)
            )
            bearish_macd = (
                observation.macd_histogram is not None
                and observation.macd_histogram < 0
                and observation.macd_line is not None
                and observation.macd_signal is not None
                and observation.macd_line < observation.macd_signal
            )
            adverse_qmd = bearish_qmd if side == "long" else (
                observation.qmd_score >= abs(float(settings.get("qmd_score") or -0.35))
                and observation.qmd_confidence >= float(settings.get("qmd_confidence") or 0.55)
            )
            adverse_macd = bearish_macd if side == "long" else (
                observation.macd_histogram is not None
                and observation.macd_histogram > 0
                and observation.macd_line is not None
                and observation.macd_signal is not None
                and observation.macd_line > observation.macd_signal
            )
            if adverse_qmd and rules_passed and (
                adverse_macd
                or not bool(settings.get("require_macd_bearish", True))
            ):
                return route
    return None


def _matching_exit_rule_set(
    rule_sets: list[dict[str, Any]],
    *,
    observation: StrategyObservation,
    entry_at: str,
) -> dict[str, Any] | None:
    elapsed_ms = 0
    if entry_at:
        try:
            elapsed_ms = max(
                0,
                int(
                    (
                        observation.observed_at
                        - datetime.fromisoformat(entry_at.replace("Z", "+00:00"))
                    ).total_seconds()
                    * 1000
                ),
            )
        except ValueError:
            elapsed_ms = 0
    for rule_set in rule_sets:
        if not bool(rule_set.get("enabled", True)):
            continue
        timing = dict(rule_set.get("timing") or {})
        active_after_ms = int(timing.get("active_after_ms") or 0)
        expires_after_ms = int(timing.get("expires_after_ms") or 0)
        if elapsed_ms < active_after_ms:
            continue
        if expires_after_ms > 0 and elapsed_ms > expires_after_ms:
            continue
        if _rule_stage_passed(dict(rule_set.get("rules") or {}), observation):
            return {
                **rule_set,
                "route_id": str(rule_set.get("rule_set_id") or ""),
                "mechanism": str(rule_set.get("rule_set_id") or "exit_rule_set"),
            }
    return None


def _elapsed_since(value: str, observed_at: datetime) -> int:
    if not value:
        return 0
    try:
        return max(
            0,
            int(
                (
                    observed_at
                    - datetime.fromisoformat(value.replace("Z", "+00:00"))
                ).total_seconds()
                * 1000
            ),
        )
    except (TypeError, ValueError):
        return 0


def _update_momentum_management_state(
    parameters: dict[str, Any],
    observation: StrategyObservation,
    state: dict[str, Any],
    *,
    side: str,
    previous_high_water: float,
) -> None:
    settings = dict(parameters.get("momentum_management") or {})
    if not settings or side != "long":
        return
    failure = dict(settings.get("failure_to_extend") or {})
    extension_bps = float(failure.get("minimum_extension_bps") or 0)
    if observation.source_timeframe in {"", "1s"} and observation.price >= (
        previous_high_water * (1 + extension_bps / 10_000)
    ):
        state["last_extension_at"] = observation.observed_at.isoformat()

    if observation.source_timeframe not in {"", "1s"}:
        return
    swing_low = _source_value(
        observation, "indicator.structure.swing_low", "1s"
    )
    if swing_low is None:
        swing_low = observation.swing_low
    if swing_low is None or float(swing_low) <= 0:
        return
    entry_at = str(state.get("entry_at") or "")
    cached = observation.source_values.get("indicator.structure.swing_low@1s")
    if isinstance(cached, dict) and entry_at:
        try:
            if datetime.fromisoformat(
                str(cached.get("observed_at") or "").replace("Z", "+00:00")
            ) <= datetime.fromisoformat(entry_at.replace("Z", "+00:00")):
                return
        except (TypeError, ValueError):
            return
    latest = state.get("latest_post_entry_swing_low")
    current = float(swing_low)
    if latest is not None and abs(float(latest) - current) < 1e-12:
        return
    if latest is not None:
        state["previous_post_entry_swing_low"] = float(latest)
        state["higher_low_confirmed"] = current > float(latest)
    state["latest_post_entry_swing_low"] = current


def _matching_momentum_management_route(
    parameters: dict[str, Any],
    observation: StrategyObservation,
    state: dict[str, Any],
    *,
    gain_pct: float,
    side: str,
) -> dict[str, Any] | None:
    settings = dict(parameters.get("momentum_management") or {})
    if not settings or side != "long":
        return None
    elapsed_ms = _elapsed_since(str(state.get("entry_at") or ""), observation.observed_at)

    downside = dict(settings.get("downside_loss_guard") or {})
    downside_timeframe = str(downside.get("timeframe") or "1s")
    if bool(downside.get("enabled", False)) and gain_pct < 0:
        unified_support = observation.structural_support_lower
        previous_price = state.get("previous_observed_price")
        unified_support_broken = bool(
            unified_support is not None
            and observation.price < float(unified_support)
            and (
                previous_price is None
                or float(previous_price) >= float(unified_support)
            )
        )
        generic_bearish_choch = bool(
            observation.structure_event == "choch"
            and observation.structure_direction == "bearish"
            and observation.source_timeframe in {"", downside_timeframe}
        )
        if bool(downside.get("bearish_choch", True)) and (
            unified_support_broken or generic_bearish_choch
        ):
            return {
                "route_id": "downside-bearish-choch",
                "name": "Below-entry bearish structural CHOCH",
                "mechanism": "downside_bearish_choch",
                "position_fraction": 1.0,
                "evidence": {
                    "gain_pct": gain_pct,
                    "structure_timeframe": (
                        "unified_level_book"
                        if unified_support_broken
                        else downside_timeframe
                    ),
                    "structural_support_lower": unified_support,
                },
            }
        line = _source_value(observation, "indicator.macd.line", downside_timeframe)
        signal = _source_value(observation, "indicator.macd.signal", downside_timeframe)
        if bool(downside.get("macd_closed", True)) and (
            line is not None
            and signal is not None
            and (
                float(line) <= float(signal)
                or float(line) <= 0
                or float(signal) <= 0
            )
        ):
            return {
                "route_id": "downside-macd-closed",
                "name": "Below-entry one-second MACD close",
                "mechanism": "downside_macd_closed",
                "position_fraction": 1.0,
                "evidence": {
                    "gain_pct": gain_pct,
                    "macd_timeframe": downside_timeframe,
                    "macd_line": line,
                    "macd_signal": signal,
                },
            }
        vwap = _source_value(observation, "indicator.vwap.value", downside_timeframe)
        if (
            bool(downside.get("below_vwap", True))
            and vwap is not None
            and observation.price < float(vwap)
        ):
            return {
                "route_id": "downside-vwap-lost",
                "name": "Below-entry loss of one-second VWAP",
                "mechanism": "downside_vwap_lost",
                "position_fraction": 1.0,
                "evidence": {
                    "gain_pct": gain_pct,
                    "vwap_timeframe": downside_timeframe,
                    "vwap": float(vwap),
                    "last_price": observation.price,
                },
            }

    structure = dict(settings.get("structure_failure") or {})
    latest_swing_low = state.get("latest_post_entry_swing_low")
    structure_ready = (
        bool(structure.get("enabled", False))
        and elapsed_ms >= int(structure.get("active_after_ms") or 0)
        and latest_swing_low is not None
        and (
            bool(state.get("higher_low_confirmed"))
            or not bool(structure.get("require_higher_low", True))
        )
    )
    if structure_ready:
        threshold = float(latest_swing_low) * (
            1 - float(structure.get("buffer_bps") or 0) / 10_000
        )
        if observation.price <= threshold:
            return {
                "route_id": "loss-of-confirmed-higher-low",
                "name": "Loss of confirmed one-second higher low",
                "mechanism": "loss_of_confirmed_higher_low",
                "position_fraction": 1.0,
                "evidence": {
                    "latest_confirmed_higher_low": float(latest_swing_low),
                    "exit_threshold_price": threshold,
                },
            }

    exhaustion = dict(settings.get("qmd_exhaustion") or {})
    if (
        bool(exhaustion.get("enabled", False))
        and observation.source_timeframe in {"", "100ms"}
        and elapsed_ms >= int(exhaustion.get("active_after_ms") or 0)
        and observation.qmd_score
        <= float(exhaustion.get("maximum_flow_structure_score") or 0)
        and observation.qmd_confidence
        >= float(exhaustion.get("minimum_confidence") or 0)
        and observation.flow_price_divergence_score
        >= float(exhaustion.get("minimum_flow_price_divergence_score") or 0)
    ):
        return {
            "route_id": "qmd-flow-geometry-exhaustion",
            "name": "QMD flow-geometry exhaustion",
            "mechanism": "qmd_flow_geometry_exhaustion",
            "position_fraction": 1.0,
            "evidence": {
                "flow_structure_score": observation.qmd_score,
                "flow_structure_confidence": observation.qmd_confidence,
                "flow_price_divergence_score": observation.flow_price_divergence_score,
            },
        }

    failure = dict(settings.get("failure_to_extend") or {})
    stalled_ms = _elapsed_since(
        str(state.get("last_extension_at") or state.get("entry_at") or ""),
        observation.observed_at,
    )
    flow_deteriorated = (
        observation.qmd_score
        <= float(failure.get("maximum_flow_structure_score") or 0)
        or observation.flow_price_divergence_score
        >= float(failure.get("minimum_flow_price_divergence_score") or 1)
    )
    if (
        bool(failure.get("enabled", False))
        and observation.source_timeframe in {"", "100ms"}
        and int(state.get("failure_to_extend_uses") or 0)
        < int(failure.get("maximum_uses") or 1)
        and gain_pct >= float(failure.get("minimum_gain_pct") or 0)
        and stalled_ms >= int(failure.get("stalled_for_ms") or 0)
        and flow_deteriorated
    ):
        return {
            "route_id": "failure-to-extend-partial",
            "name": "Failure to extend with deteriorating QMD flow",
            "mechanism": "failure_to_extend_partial",
            "position_fraction": float(failure.get("position_fraction") or 0.5),
            "evidence": {
                "gain_pct": gain_pct,
                "stalled_for_ms": stalled_ms,
                "flow_structure_score": observation.qmd_score,
                "flow_price_divergence_score": observation.flow_price_divergence_score,
            },
        }

    macd = dict(settings.get("macd_backstop") or {})
    timeframe = str(macd.get("timeframe") or "1s")
    line = _source_value(observation, "indicator.macd.line", timeframe)
    signal = _source_value(observation, "indicator.macd.signal", timeframe)
    histogram = _source_value(observation, "indicator.macd.histogram", timeframe)
    close_condition = str(macd.get("close_condition") or "regime_closed")
    macd_closed = (
        line is not None
        and signal is not None
        and (
            float(signal) > float(line)
            if close_condition == "signal_above_line"
            else (
                float(line) <= float(signal)
                or float(line) <= 0
                or float(signal) <= 0
            )
        )
    )
    if observation.source_timeframe in {"", timeframe}:
        if macd_closed:
            if not state.get("macd_closed_since"):
                state["macd_closed_since"] = observation.observed_at.isoformat()
        else:
            state["macd_closed_since"] = ""
    if (
        bool(macd.get("enabled", False))
        and elapsed_ms >= int(macd.get("active_after_ms") or 0)
        and macd_closed
        and _elapsed_since(
            str(state.get("macd_closed_since") or ""), observation.observed_at
        )
        >= int(macd.get("closed_for_ms") or 0)
    ):
        return {
            "route_id": (
                "macd-signal-crossed-above-line"
                if close_condition == "signal_above_line"
                else "macd-closed-backstop"
            ),
            "name": (
                "One-second MACD signal crossed above line"
                if close_condition == "signal_above_line"
                else "One-second MACD closed backstop"
            ),
            "mechanism": (
                "macd_signal_crossed_above_line"
                if close_condition == "signal_above_line"
                else "macd_closed_backstop"
            ),
            "position_fraction": 1.0,
            "evidence": {
                "macd_timeframe": timeframe,
                "macd_line": line,
                "macd_signal": signal,
                "macd_histogram": histogram,
            },
        }
    return None


def _rule_stage_passed(
    stage: dict[str, Any],
    observation: StrategyObservation,
) -> bool:
    groups = list(stage.get("rule_sets") or stage.get("groups") or [])
    if not groups:
        return False
    group_results: dict[str, bool] = {}
    for group in groups:
        if not bool(group.get("enabled", True)):
            continue
        conditions = [
            _condition_matches(dict(condition), observation)
            for condition in group.get("conditions") or []
            if bool(condition.get("enabled", True))
        ]
        operator = str(group.get("operator") or "all")
        score = sum(1 for matched in conditions if matched) / len(conditions) if conditions else 0.0
        passed = bool(conditions) and (
            all(conditions)
            if operator == "all"
            else score >= float(group.get("required_score") or 0)
            if operator == "score"
            else any(conditions)
        )
        group_id = str(group.get("rule_set_id") or group.get("group_id") or "")
        group_results[group_id] = passed
    if not group_results:
        return False
    expression = dict(stage.get("expression") or {})
    if expression:
        return _rule_expression_passed(expression, group_results)
    operator = str(stage.get("operator") or "any")
    if operator == "all":
        return all(group_results.values())
    return any(group_results.values())


def _rule_expression_passed(
    expression: dict[str, Any],
    rule_set_results: dict[str, bool],
) -> bool:
    if str(expression.get("kind") or "") == "rule_set":
        return bool(rule_set_results.get(str(expression.get("rule_set_id") or ""), False))
    children = [
        _rule_expression_passed(dict(child), rule_set_results)
        for child in expression.get("children") or []
    ]
    if not children:
        return False
    return all(children) if str(expression.get("operator") or "") == "and" else any(children)


def _campaign_authority(
    state: dict[str, Any],
    key: str,
    default: str,
) -> str:
    return str(dict(state.get("campaign_policy") or {}).get(key) or default)


def _validate_runtime_rule_expression(
    expression: dict[str, Any],
    rule_set_ids: set[str],
    label: str,
) -> None:
    kind = str(expression.get("kind") or "")
    if kind == "rule_set":
        rule_set_id = str(expression.get("rule_set_id") or "")
        if rule_set_id not in rule_set_ids:
            raise ValueError(f"{label} references unknown rule set {rule_set_id or '<empty>'}")
        return
    if kind != "operator" or str(expression.get("operator") or "") not in {"and", "or"}:
        raise ValueError(f"{label} expression is unsupported")
    children = list(expression.get("children") or [])
    if not children:
        raise ValueError(f"{label} expression requires at least one child")
    for child in children:
        _validate_runtime_rule_expression(dict(child), rule_set_ids, label)


def _validate_exit_routes(routes: list[dict[str, Any]]) -> None:
    if not routes:
        raise ValueError("Strategy exit routes are required")
    ids = [str(row.get("route_id") or "") for row in routes]
    if any(not route_id for route_id in ids) or len(ids) != len(set(ids)):
        raise ValueError("Strategy exit route ids must be present and unique")
    protective = next(
        (row for row in routes if row.get("mechanism") == "protective_stop"),
        None,
    )
    if (
        protective is None
        or not bool(protective.get("enabled"))
        or not bool(protective.get("protected"))
        or int(protective.get("priority") or 0) != 100
        or str(protective.get("action")) != "close"
    ):
        raise ValueError("The protective-stop exit route cannot be disabled")
    supported = {"protective_stop", "failed_breakout", "bearish_qmd_macd"}
    for route in routes:
        if str(route.get("mechanism") or "") not in supported:
            raise ValueError(f"Unsupported strategy exit mechanism: {route.get('mechanism')}")
        priority = int(route.get("priority") or 0)
        if priority < 0 or priority > 100:
            raise ValueError("Strategy exit route priority must be between 0 and 100")


def _validate_entry_rules(rules: dict[str, Any]) -> None:
    catalog = {str(row["source_id"]): row for row in strategy_input_catalog()}
    for stage_name in ("trigger", "confirmation", "veto"):
        stage = dict(rules.get(stage_name) or {})
        expression = dict(stage.get("expression") or {})
        operator = str(stage.get("operator") or "")
        if not expression and operator not in {"all", "any"}:
            raise ValueError(f"Entry {stage_name} operator is unsupported")
        groups = list(stage.get("rule_sets") or stage.get("groups") or [])
        if not groups:
            raise ValueError(f"Entry {stage_name} requires at least one rule group")
        if not any(bool(group.get("enabled", True)) for group in groups):
            raise ValueError(f"Entry {stage_name} requires at least one enabled rule group")
        group_ids = [str(group.get("rule_set_id") or group.get("group_id") or "") for group in groups]
        if any(not group_id for group_id in group_ids) or len(set(group_ids)) != len(group_ids):
            raise ValueError(f"Entry {stage_name} rule group ids must be present and unique")
        if expression:
            _validate_runtime_rule_expression(expression, set(group_ids), f"Entry {stage_name}")
        for group in groups:
            group_operator = str(group.get("operator") or "")
            if group_operator not in {"all", "any", "score"}:
                raise ValueError(f"Entry rule group {group.get('group_id')} operator is unsupported")
            if group_operator == "score" and not 0 < float(group.get("required_score") or 0) <= 1:
                raise ValueError(
                    f"Entry rule group {group.get('group_id')} required score must be between zero and one"
                )
            conditions = list(group.get("conditions") or [])
            if not conditions:
                raise ValueError(f"Entry rule group {group.get('group_id')} requires a condition")
            if bool(group.get("enabled", True)) and not any(
                bool(condition.get("enabled", True)) for condition in conditions
            ):
                raise ValueError(
                    f"Entry rule group {group.get('group_id')} requires an enabled condition"
                )
            condition_ids = [str(condition.get("condition_id") or "") for condition in conditions]
            if any(not condition_id for condition_id in condition_ids) or len(set(condition_ids)) != len(condition_ids):
                raise ValueError(f"Entry rule group {group.get('group_id')} condition ids must be present and unique")
            for condition in conditions:
                comparator = str(condition.get("comparator") or "")
                if comparator not in RULE_COMPARATORS:
                    raise ValueError(f"Entry rule condition {condition.get('condition_id')} comparator is unsupported")
                left_source = catalog.get(str(condition.get("left_source_id") or ""))
                left_field_ref = str(condition.get("left_field_ref") or "")
                if left_source is None and not left_field_ref:
                    raise ValueError(f"Entry rule condition {condition.get('condition_id')} has unknown left source")
                if left_source is not None:
                    _validate_rule_timeframe(condition, "left_timeframe", left_source)
                right_source_id = str(condition.get("right_source_id") or "")
                right_field_ref = str(condition.get("right_field_ref") or "")
                if comparator == "above_by_bps" and not right_source_id:
                    if not right_field_ref:
                        raise ValueError(
                            f"Entry rule condition {condition.get('condition_id')} requires a target source"
                        )
                if right_source_id:
                    right_source = catalog.get(right_source_id)
                    if right_source is None and not right_field_ref:
                        raise ValueError(f"Entry rule condition {condition.get('condition_id')} has unknown right source")
                    if right_source is not None:
                        _validate_rule_timeframe(condition, "right_timeframe", right_source)
                elif comparator != "is_true" and condition.get("value") is None:
                    raise ValueError(f"Entry rule condition {condition.get('condition_id')} requires a value")


def _validate_rule_timeframe(
    condition: dict[str, Any],
    key: str,
    source: dict[str, Any],
) -> None:
    timeframe = str(condition.get(key) or "")
    # Schema-v27 Rule Sets reference an exact Data Field output whose context
    # owns the timeframe. Legacy embedded rules may still carry this field.
    if not timeframe:
        return
    if timeframe not in set(source["timeframes"]):
        raise ValueError(
            f"Entry rule source {source['source_id']} does not support timeframe {timeframe}"
        )


def _input(
    source_id: str,
    label: str,
    category: str,
    provider: str,
    runtime_field: str,
    value_type: str,
    timeframes: list[str],
    *,
    parameter: str = "value",
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "label": label,
        "category": category,
        "provider": provider,
        "runtime_field": runtime_field,
        "value_type": value_type,
        "timeframes": timeframes,
        "parameter": parameter,
        "summary": STRATEGY_INPUT_SUMMARIES.get(
            source_id,
            f"{label} supplied by {provider}; the configured timeframe is part of the rule contract.",
        ),
    }


def _condition(
    condition_id: str,
    left_source_id: str,
    left_timeframe: str,
    comparator: str,
    *,
    right_source_id: str = "",
    right_timeframe: str = "",
    value: Any = None,
) -> dict[str, Any]:
    return {
        "condition_id": condition_id,
        "enabled": True,
        "left_source_id": left_source_id,
        "left_timeframe": left_timeframe,
        "comparator": comparator,
        "right_source_id": right_source_id,
        "right_timeframe": right_timeframe,
        "value": value,
    }


def _rule_group(
    group_id: str,
    label: str,
    operator: str,
    conditions: list[dict[str, Any]],
    *,
    required_score: float = 1.0,
) -> dict[str, Any]:
    return {
        "group_id": group_id,
        "label": label,
        "enabled": True,
        "operator": operator,
        "required_score": required_score,
        "conditions": conditions,
    }


def _confirmation_confidence(observation: StrategyObservation) -> float:
    return max(0.0, min(1.0, (observation.qmd_confidence + (0.75 if observation.vwap is not None else 0) + (0.75 if observation.macd_line is not None else 0)) / 3))


def _initial_stop(
    observation: StrategyObservation,
    parameters: dict[str, Any],
    reference: float | None,
    *,
    side: str,
) -> float:
    stop = parameters["protection"]["stop"]
    causal_structure_candidates = [
        value
        for value in (
            observation.swing_low if side == "long" else observation.swing_high,
            observation.structural_support_lower
            if side == "long"
            else observation.structural_resistance_upper,
        )
        if value is not None
        and value > 0
        and (value < observation.price if side == "long" else value > observation.price)
    ]
    structure_base = (
        min(causal_structure_candidates)
        if side == "long" and causal_structure_candidates
        else max(causal_structure_candidates)
        if causal_structure_candidates
        else reference or observation.price
    )
    direction = -1 if side == "long" else 1
    structure_stop = structure_base * (
        1 + direction * float(stop["structure_buffer_bps"]) / 10_000
    )
    volatility = observation.volatility if observation.volatility > 0 else observation.price * 0.002
    volatility_stop = observation.price + direction * volatility * float(stop["volatility_multiple"])
    method = stop["method"]
    selected = (
        structure_stop
        if method == "structure"
        else volatility_stop
        if method == "volatility"
        else (
            max(structure_stop, volatility_stop)
            if bool(stop.get("prefer_closer_hybrid", False))
            else min(structure_stop, volatility_stop)
        )
        if side == "long"
        else (
            min(structure_stop, volatility_stop)
            if bool(stop.get("prefer_closer_hybrid", False))
            else max(structure_stop, volatility_stop)
        )
    )
    maximum_risk = observation.price * (
        1 + direction * float(stop["maximum_risk_pct"]) / 100
    )
    if side == "long":
        return round(max(maximum_risk, min(selected, observation.price * 0.9999)), 4)
    return round(min(maximum_risk, max(selected, observation.price * 1.0001)), 4)


def _trailing_amount(observation: StrategyObservation, parameters: dict[str, Any]) -> float | None:
    trailing = parameters["protection"]["trailing"]
    if not trailing["enabled"]:
        return None
    volatility_distance = observation.volatility * float(trailing["distance_volatility_multiple"])
    minimum_distance = observation.price * float(trailing["minimum_distance_bps"]) / 10_000
    return round(max(volatility_distance, minimum_distance), 4)


def _ratcheted_stop(
    observation: StrategyObservation,
    parameters: dict[str, Any],
    state: dict[str, Any],
    *,
    side: str,
) -> float:
    current = float(state.get("active_stop") or state.get("initial_stop") or 0)
    entry = float(state.get("entry_reference_price") or observation.average_price or observation.price)
    gain_pct = (
        (observation.price / entry - 1) * 100
        if side == "long"
        else (entry / observation.price - 1) * 100
    ) if entry > 0 else 0
    trailing = parameters["protection"]["trailing"]
    if not trailing["enabled"] or gain_pct < float(trailing["activation_gain_pct"]):
        return current
    distance = _trailing_amount(observation, parameters) or 0
    if side == "long":
        return round(max(current, float(state["high_water_price"]) - distance), 4)
    return round(min(current, float(state["low_water_price"]) + distance), 4)


def _luld_target(
    observation: StrategyObservation,
    parameters: dict[str, Any],
    *,
    side: str,
) -> float | None:
    policy = parameters["protection"]["luld_profit_target"]
    local_time = observation.observed_at.astimezone(NEW_YORK).time().replace(tzinfo=None)
    regular_session = clock_time(9, 30) <= local_time < clock_time(16, 0)
    if not policy["enabled"] or not regular_session:
        return None
    if side == "short" or observation.upper_luld_price is None:
        return None
    spread = max(0.0, observation.ask - observation.bid)
    offset = max(
        observation.upper_luld_price * float(policy.get("buffer_bps") or 0) / 10_000,
        float(policy.get("tick_size") or 0.01)
        * int(policy.get("minimum_tick_offset_count") or 0),
        spread if bool(policy.get("include_current_spread", True)) else 0.0,
    )
    target = observation.upper_luld_price - offset
    return round(target, 4) if target > observation.price else None


def _profit_level_score(row: dict[str, Any]) -> float:
    salience = _level_metric(row, "salience", "strength")
    confidence = _level_metric(row, "confidence")
    reaction = _level_metric(row, "reaction_probability")
    reversal = _level_metric(row, "reversal_probability")
    hold = _level_metric(row, "hold_probability")
    pivot_breadth = 1.0 - exp(-max(0.0, _level_metric(row, "independent_pivot_count")))
    role_flip = 1.0 - exp(-max(0.0, _level_metric(row, "role_flip_count")))
    pressure = abs(_level_metric(row, "pressure_bias"))
    return (
        0.20 * salience
        + 0.20 * confidence
        + 0.20 * reaction
        + 0.15 * reversal
        + 0.10 * hold
        + 0.05 * pivot_breadth
        + 0.05 * role_flip
        + 0.05 * pressure
    )


def _structural_profit_targets(
    observation: StrategyObservation,
    parameters: dict[str, Any],
    *,
    stop: float,
    side: str,
    luld_target: float | None,
) -> list[float]:
    """Build a variable causal ladder from important, high-probability levels."""
    policy = dict(parameters["protection"].get("profit_ladder") or {})
    if not bool(policy.get("enabled", True)):
        return [luld_target] if luld_target is not None else []
    entry = observation.price
    level_rows = _consolidated_structure_levels(list(
        observation.structural_resistance_levels
        if side == "long"
        else observation.structural_support_levels
    ), side=side)
    local_time = observation.observed_at.astimezone(NEW_YORK).time().replace(tzinfo=None)
    regular_session = clock_time(9, 30) <= local_time < clock_time(16, 0)
    maximum_price = (
        luld_target
        if side == "long" and regular_session
        else entry
        * (1 + float(policy.get("premarket_maximum_gain_pct") or 200.0) / 100.0)
        if side == "long"
        else None
    )
    ranked_candidates: list[tuple[float, float]] = []
    for row in level_rows:
        strength = _level_metric(dict(row), "salience", "strength")
        confidence = _level_metric(dict(row), "confidence")
        reaction = _level_metric(dict(row), "reaction_probability")
        reversal = _level_metric(dict(row), "reversal_probability")
        score = _profit_level_score(dict(row))
        candidate = row.get("price")
        if candidate is None:
            candidate = row.get("lower") if side == "long" else row.get("upper")
        if (
            candidate is not None
            and strength >= float(policy.get("minimum_level_strength") or 0.0)
            and confidence >= float(policy.get("minimum_level_confidence") or 0.0)
            and reaction >= float(policy.get("minimum_reaction_probability") or 0.0)
            and reversal >= float(policy.get("minimum_reversal_probability") or 0.0)
            and score >= float(policy.get("minimum_composite_score") or 0.0)
        ):
            candidate_value = float(candidate)
            if maximum_price is None or candidate_value <= maximum_price:
                ranked_candidates.append((score, candidate_value))
    if not ranked_candidates:
        nearest_price = (
            observation.structural_resistance_lower
            if side == "long"
            else observation.structural_support_upper
        )
        nearest_strength = (
            observation.structural_resistance_strength
            if side == "long"
            else observation.structural_support_strength
        )
        nearest_confidence = (
            observation.structural_resistance_confidence
            if side == "long"
            else observation.structural_support_confidence
        )
        nearest_row = {
            "salience": nearest_strength,
            "confidence": nearest_confidence,
            "reaction_probability": nearest_strength,
            "reversal_probability": nearest_strength,
        }
        nearest_score = _profit_level_score(nearest_row)
        if (
            nearest_price is not None
            and nearest_strength >= float(policy.get("minimum_level_strength") or 0.0)
            and nearest_confidence >= float(policy.get("minimum_level_confidence") or 0.0)
            and nearest_strength >= float(policy.get("minimum_reaction_probability") or 0.0)
            and nearest_strength >= float(policy.get("minimum_reversal_probability") or 0.0)
            and nearest_score >= float(policy.get("minimum_composite_score") or 0.0)
            and (maximum_price is None or float(nearest_price) <= maximum_price)
        ):
            ranked_candidates.append((nearest_score, float(nearest_price)))
    spacing = entry * float(policy.get("minimum_spacing_bps") or 0.0) / 10_000.0
    unique: list[float] = []
    maximum = max(0, int(policy.get("maximum_targets") or 0))

    def append_candidates(values: list[float]) -> None:
        for candidate in sorted(values, reverse=side == "short"):
            if candidate <= 0 or not (
                candidate > entry if side == "long" else candidate < entry
            ):
                continue
            if any(abs(candidate - existing) < spacing for existing in unique):
                continue
            unique.append(round(candidate, 4))
            if len(unique) >= maximum:
                return

    selection_mode = str(policy.get("selection_mode") or "ranked")
    if selection_mode == "highest_price_below_cap":
        ordered_prices = sorted(
            {candidate for _, candidate in ranked_candidates},
            reverse=side == "long",
        )
        selected = ordered_prices[:maximum]
    else:
        selected = [
            candidate
            for _, candidate in sorted(
                ranked_candidates,
                key=lambda item: (-item[0], item[1] if side == "long" else -item[1]),
            )[:maximum]
        ]
    append_candidates(selected)
    unique.sort(reverse=side == "short")
    return unique


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {key: dict(value) if isinstance(value, dict) else value for key, value in base.items()}
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
