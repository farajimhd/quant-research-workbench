from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, time as clock_time, timedelta, timezone
from enum import StrEnum
from math import floor, inf, isfinite, nextafter
from typing import Any, Mapping
from uuid import uuid4
from zoneinfo import ZoneInfo

from src.request_context import causal_identity

from src.trading_runtime.execution_policies import (
    AddProtectionPolicy,
    DEFAULT_VERY_URGENT_PRICE_DISCRETION_TICKS,
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
STRATEGY_REVISION = 44
HISTORICAL_STRATEGY_REVISIONS = (26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43)

_COMPLETED_FRAME_TOP_N_ENTRY_MODE = "prior_completed_frame_top_n_below_session_high"
_EVENT_PRICE_TOP_N_ENTRY_MODE = "event_price_top_n_below_session_high"
_TOP_N_SESSION_HIGH_ENTRY_MODES = {
    _COMPLETED_FRAME_TOP_N_ENTRY_MODE,
    _EVENT_PRICE_TOP_N_ENTRY_MODE,
}

SOURCE_MAXIMUM_AGE_MS = {
    "100ms": 500,
    "1s": 2_000,
    "5s": 6_000,
    "10s": 11_000,
    "30s": 31_000,
    "1m": 61_000,
    "5m": 301_000,
}
SOURCE_MAXIMUM_AGE_OVERRIDES_MS = {
    "indicator.flow_structure.score": 300,
    "indicator.flow_structure.confidence": 300,
    "signal.company_news.score": 60_000,
    "signal.sec_filing.score": 60_000,
    "signal.news_labeled": 60_000,
    "signal.sec_labeled": 60_000,
}

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
    "indicator.vwap.execution_value": "The sole causal VWAP authority: session volume-eligible trades inside the prevailing NBBO no more than one second old.",
    "indicator.flow_structure.score": "QMD's signed composite of directional order flow and market structure, used to rank bullish versus bearish alignment.",
    "indicator.flow_structure.confidence": "QMD's confidence in the current flow-structure composite, used to require stronger evidence before acting on its score.",
    "indicator.macd.line": "The MACD fast-minus-slow momentum line for the selected timeframe, used to measure directional momentum.",
    "indicator.macd.signal": "The smoothed MACD signal line for the selected timeframe, used as the comparison baseline for momentum confirmation.",
    "indicator.macd.histogram": "The distance between the MACD and signal lines, used to measure whether momentum is strengthening or weakening.",
    "signal.price_volume_expansion.score": "A scored QMD event measuring whether price movement is confirmed by expanding trading activity and volume.",
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
        _input("indicator.vwap.execution_value", "VWAP", "QMD indicator", "qmd", "execution_vwap", "price", ["100ms", "1s", "5s", "10s", "30s", "1m", "5m"], parameter="execution_value"),
        _input("indicator.flow_structure.score", "Flow-structure score", "QMD indicator", "qmd", "qmd_score", "score", ["100ms"], parameter="score"),
        _input("indicator.flow_structure.confidence", "Flow-structure confidence", "QMD indicator", "qmd", "qmd_confidence", "score", ["100ms"], parameter="confidence"),
        _input("indicator.macd.line", "MACD line", "Market indicator", "qmd", "macd_line", "number", ["1s", "5s", "10s", "30s", "1m", "5m"], parameter="line"),
        _input("indicator.macd.signal", "MACD signal", "Market indicator", "qmd", "macd_signal", "number", ["1s", "5s", "10s", "30s", "1m", "5m"], parameter="signal"),
        _input("indicator.macd.histogram", "MACD histogram", "Market indicator", "qmd", "macd_histogram", "number", ["1s", "5s", "10s", "30s", "1m", "5m"], parameter="histogram"),
        _input("signal.price_volume_expansion.score", "Price-volume expansion score", "QMD market signal", "qmd", "price_volume_expansion_score", "score", ["1s", "10s", "30s", "1m"], parameter="score"),
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
                    _condition("price-over-vwap", "market.last_price", breakout_timeframe, "above_by_bps", right_source_id="indicator.vwap.execution_value", right_timeframe=breakout_timeframe, value=float(entry.get("breakout_buffer_bps") or 5)),
                ]),
                _rule_group("bullish-choch", "Bullish change of character", "all", [
                    _condition("bullish-choch", "indicator.structure.bullish_choch", breakout_timeframe, "is_true"),
                ]),
                _rule_group("price-volume-expansion", "Price-volume expansion", "all", [
                    _condition("price-volume-expansion-score", "signal.price_volume_expansion.score", "1s", "greater_or_equal", value=float(entry.get("price_expansion_minimum_score") or 0.65)),
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
                _rule_group("macd-confirmation", "MACD confirms momentum", "all", [
                    _condition("macd-line-over-signal", "indicator.macd.line", "5s", "greater_or_equal", right_source_id="indicator.macd.signal", right_timeframe="5s"),
                    _condition("macd-line-positive", "indicator.macd.line", "5s", "greater_than", value=0),
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
    bar_open: float | None = None
    bid: float = 0.0
    ask: float = 0.0
    position_quantity: float = 0.0
    pending_exit_quantity: float = 0.0
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
    structural_session_high: float | None = None
    structural_up_probability: float = 0.5
    structure_event: str = ""
    structure_direction: str = ""
    vwap: float | None = None
    execution_vwap: float | None = None
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


def long_momentum_strategy_definition(
    *, revision: int = STRATEGY_REVISION
) -> dict[str, Any]:
    """Canonical built-in definition and optimization space for the first post-refactor strategy."""
    if revision not in {*HISTORICAL_STRATEGY_REVISIONS, STRATEGY_REVISION}:
        raise ValueError(f"Unsupported Long Momentum Strategy revision: {revision}")
    parameters = default_long_momentum_parameters(revision=revision)
    if revision == 26:
        macd_conditions = next(
            row
            for row in parameters["entry_rules"]["confirmation"]["groups"]
            if row.get("group_id") == "macd-confirmation"
        )["conditions"]
        macd_conditions.insert(
            2,
            _condition(
                "macd-signal-positive",
                "indicator.macd.signal",
                "5s",
                "greater_than",
                value=0,
            ),
        )
    return {
        "strategy_id": STRATEGY_ID,
        "revision": revision,
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
                **({"protection.trailing.mode": ["qualified_support", "support_distance"]}
                   if revision >= 37 else {}),
                "protection.stop.method": [
                    "structure",
                    "volatility",
                    "hybrid",
                    "ordinal_qualified_support",
                ],
                "protection.stop.volatility_multiple": [0.75, 1.0, 1.25, 1.5, 2.0],
                **(
                    {
                        "profit_pocket.trigger": [
                            "acceleration_slowdown",
                            "favorable_move_pct",
                            "volatility_multiple",
                        ],
                        "profit_pocket.quantity_fraction": [1.0],
                    }
                    if revision <= 32
                    else {}
                ),
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
                "allow_developing_inputs": revision >= 34,
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


def default_long_momentum_parameters(
    *, revision: int = STRATEGY_REVISION,
) -> dict[str, Any]:
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
                **(
                    {
                        "minimum_ticker_relative_quality_score": 0.20,
                        "strict_ticker_relative_quality_gate": revision >= 35,
                    }
                    if revision >= 34
                    else {
                        "minimum_hold_probability": 0.0,
                        "minimum_hold_quality_score": 0.70,
                    }
                ),
                "minimum_hold_observations": 1,
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
            "maximum_admission_spread_bps": 60.0,
            "maximum_current_spread_bps": 100.0,
            # Compatibility authority for immutable configurations created before
            # admission and order-time spread limits were represented separately.
            "maximum_spread_bps": 100.0,
        },
        "entry_momentum_confirmation": {
            "enabled": False,
            "timeframe": "1s",
            "histogram_lookback_ms": 5_000,
            "minimum_histogram_increase": 0.0,
            "minimum_histogram_increase_bps": 0.0,
        },
        "entry_candle_confirmation": {
            "enabled": revision <= 33 or revision >= 39,
            "timeframe": "1s",
            "require_closed_bar": revision <= 33 or revision >= 39,
            "reject_bearish_close": revision <= 33 or revision >= 39,
            "minimum_macd_open_gap_bps": 0.5,
        },
        "sizing": {
            "request_mode": "fixed_quantity",
            "request_value": 100.0,
            "initial_quantity": 100.0,
            "add_fraction": 0.5,
        },
        "protection": {
            "stop": {
                "method": "ordinal_qualified_support",
                "require_qualified_support": revision >= 37,
                "structure_buffer_bps": 0.0,
                "volatility_multiple": 1.25,
                "maximum_risk_pct": 15.0,
                **(
                    {
                        "minimum_ticker_relative_quality_score": 0.20,
                        "strict_ticker_relative_quality_gate": revision >= 35,
                    }
                    if revision >= 34
                    else {
                        "minimum_hold_probability": 0.0,
                        "minimum_hold_quality_score": 0.70,
                    }
                ),
                "minimum_hold_observations": 1,
                "support_level_ordinal": 2,
                "prefer_closer_hybrid": True,
            },
            "trailing": {
                "enabled": True,
                "mode": "qualified_support" if revision >= 37 else "support_distance",
                "activation_gain_pct": 0.0,
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
                **(
                    {
                        "minimum_ticker_relative_quality_score": 0.20,
                        "strict_ticker_relative_quality_gate": revision >= 35,
                    }
                    if revision >= 34
                    else {
                        "minimum_hold_probability": 0.0,
                        "minimum_hold_quality_score": 0.70,
                    }
                ),
                "minimum_hold_observations": 1,
                "minimum_composite_score": 0.0,
                "minimum_entry_target_gap_bps": 0.0,
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
            # Revision 33 removes profit pocketing from the active strategy.
            # Keep the prior default only for immutable historical definitions.
            "enabled": revision <= 32,
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
            "pullback_reclaim": {
                "enabled": False,
                "minimum_pullback_atr_multiple": 0.50,
                "minimum_pullback_bps": 25.0,
            },
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
                "macd_closed": True,
                "below_vwap": True,
                "vwap_source_id": "indicator.vwap.execution_value",
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
    if revision >= 37:
        parameters["protection"]["stop"].update({"method": "ordinal_qualified_support", "structure_buffer_bps": 0.0})
        parameters["protection"]["profit_ladder"].update({
            "selection_mode": "ordinal_qualified_level", "target_level_ordinal": 3,
            "maximum_targets": 1, "require_resistance_role": True,
        })
        parameters["add"]["enabled"] = False
    if revision >= 43:
        parameters["structural_entry"].update(
            persistent_r3_acceptance=True, maximum_entry_levels=3, acceptance_buffer_bps=0.0,
        )
    if revision >= 44:
        parameters["structural_entry"]["intrabar_after_completed_r3"] = True
        parameters["entry_candle_confirmation"]["evaluate_macd_intrabar"] = True
        parameters["momentum_management"]["minimum_macd_exit_gap_bps"] = 0.5
        parameters["momentum_management"]["macd_backstop"].update(
            enabled=True, timeframe="1s", active_after_ms=0, closed_for_ms=0,
            close_condition="signal_above_line",
        )
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


def resolve_long_momentum_parameters(
    overrides: dict[str, Any] | None = None,
    *,
    revision: int = STRATEGY_REVISION,
) -> dict[str, Any]:
    parameters = _deep_merge(
        default_long_momentum_parameters(revision=revision),
        dict(overrides or {}),
    )
    if revision >= 33:
        # Profit pocketing is not part of the active strategy contract. Ignore
        # stale persisted overrides; revision 32 remains reproducible.
        parameters["profit_pocket"]["enabled"] = False
    if revision >= 37:
        parameters["add"]["enabled"] = False
        parameters["structural_entry"]["entry_tranche_count"] = 1
        parameters["structural_entry"]["retain_crossing_role_flip"] = True
        parameters["protection"]["stop"]["require_qualified_support"] = True
        if "initial_entry" in parameters.get("phase_policy", {}):
            parameters["phase_policy"]["initial_entry"]["add_steps"] = []
        parameters["reentry"]["target_replenishment"]["enabled"] = False
        if parameters["protection"]["trailing"].get("mode") not in {
            "qualified_support", "support_distance"
        }:
            raise ValueError("Trailing mode must be qualified_support or support_distance")
    if revision >= 38:
        # Break probability is a percentage; the old lifetime break-count
        # ceiling is not part of the current level qualification contract.
        parameters["structural_entry"].pop("maximum_break_count", None)
        parameters["protection"]["profit_ladder"].pop("maximum_break_count", None)
    if revision >= 39:
        parameters["entry_candle_confirmation"].update(
            enabled=True, timeframe="1s", require_closed_bar=True, reject_bearish_close=True,
        )
        parameters["structural_entry"]["selection_mode"] = _COMPLETED_FRAME_TOP_N_ENTRY_MODE
        parameters["structural_entry"]["follow_current_level_prices"] = True
    parameters["structural_entry"]["persistent_r3_acceptance"] = revision >= 43
    if revision >= 43:
        parameters["structural_entry"]["maximum_entry_levels"] = 3
        parameters["structural_entry"]["acceptance_buffer_bps"] = 0.0
    if revision >= 44:
        parameters["structural_entry"]["intrabar_after_completed_r3"] = True
        parameters["entry_candle_confirmation"]["evaluate_macd_intrabar"] = True
        parameters["momentum_management"]["macd_backstop"].update(
            enabled=True, timeframe="1s", active_after_ms=0, closed_for_ms=0,
            close_condition="signal_above_line",
        )
        parameters["momentum_management"]["downside_loss_guard"]["timeframe"] = "1s"
        for section, key in (
            (parameters["entry_candle_confirmation"], "minimum_macd_open_gap_bps"),
            (parameters["momentum_management"], "minimum_macd_exit_gap_bps"),
        ):
            value = float(section[key])
            if not isfinite(value) or value < 0:
                raise ValueError("MACD gaps must be finite nonnegative basis points")
            section[key] = value
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
    if parameters["protection"]["stop"]["method"] not in {
        "structure",
        "volatility",
        "hybrid",
        "ordinal_qualified_support",
    }:
        raise ValueError("Unsupported protective stop method")
    stop = parameters["protection"]["stop"]
    if not 0 < float(stop.get("maximum_risk_pct") or 0) < 100:
        raise ValueError("Protective stop maximum risk must be between zero and 100 percent")
    if str(stop.get("method") or "") == "ordinal_qualified_support":
        if "minimum_ticker_relative_quality_score" in stop:
            if not 0 <= float(stop["minimum_ticker_relative_quality_score"]) <= 1:
                raise ValueError("Protective support ticker-relative quality score must be in [0, 1]")
        else:
            if not 0 <= float(stop.get("minimum_hold_probability") or 0) < 1:
                raise ValueError("Protective support hold threshold must be in [0, 1)")
            if not 0 <= float(stop.get("minimum_hold_quality_score") or 0) <= 1:
                raise ValueError("Protective support quality score must be in [0, 1]")
        if int(stop.get("minimum_hold_observations") or 0) < 0:
            raise ValueError("Protective support observation count cannot be negative")
        if int(stop.get("support_level_ordinal") or 0) < 1:
            raise ValueError("Protective support level ordinal must be positive")
    quality_policies = (
        ("Structural entry", dict(parameters.get("structural_entry") or {})),
        (
            "Structural profit target",
            dict(parameters.get("protection", {}).get("profit_ladder") or {}),
        ),
    )
    for label, policy in quality_policies:
        quality_key = (
            "minimum_ticker_relative_quality_score"
            if "minimum_ticker_relative_quality_score" in policy
            else "minimum_hold_quality_score"
        )
        if not 0 <= float(policy.get(quality_key) or 0) <= 1:
            raise ValueError(f"{label} quality score must be in [0, 1]")
        if int(policy.get("minimum_hold_observations") or 0) < 0:
            raise ValueError(f"{label} observation count cannot be negative")
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
    pullback_reclaim = dict(
        parameters["reentry"].get("pullback_reclaim") or {}
    )
    if float(pullback_reclaim.get("minimum_pullback_atr_multiple") or 0) < 0:
        raise ValueError("Re-entry pullback ATR multiple cannot be negative")
    if float(pullback_reclaim.get("minimum_pullback_bps") or 0) < 0:
        raise ValueError("Re-entry pullback basis points cannot be negative")
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
            vwap_source_id = str(
                downside.get("vwap_source_id")
                or "indicator.vwap.execution_value"
            )
            dependencies.update({
                ("indicator.macd.line", timeframe),
                ("indicator.macd.signal", timeframe),
                (vwap_source_id, timeframe),
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
    *,
    require_positive_signal: bool = False,
) -> tuple[bool, dict[str, float | None]]:
    line = _numeric_source_value(observation, "indicator.macd.line", timeframe)
    signal = _numeric_source_value(observation, "indicator.macd.signal", timeframe)
    evidence = {"macd_line": line, "macd_signal": signal}
    return bool(
        line is not None
        and signal is not None
        and line > signal
        and line > 0
        and (not require_positive_signal or signal > 0)
    ), evidence


def _macd_gap_bps(price: float, line: Any, signal: Any) -> float | None:
    """Signed MACD-minus-signal distance, normalized by current trade price."""
    if line is None or signal is None or not isfinite(price) or price <= 0:
        return None
    line, signal = float(line), float(signal)
    if not isfinite(line) or not isfinite(signal):
        return None
    return (line - signal) / price * 10_000.0


def _record_macd_histogram_history(
    state: dict[str, Any], observation: StrategyObservation
) -> None:
    """Keep a small causal 1-second MACD history in durable strategy state."""

    line = _numeric_source_value(observation, "indicator.macd.line", "1s")
    signal = _numeric_source_value(observation, "indicator.macd.signal", "1s")
    if line is None or signal is None:
        return
    sample = {
        "observed_at": observation.observed_at.isoformat(),
        "histogram": line - signal,
    }
    history = [
        dict(row)
        for row in state.get("macd_histogram_history_1s") or []
        if isinstance(row, dict)
    ]
    if history and str(history[-1].get("observed_at") or "") == sample["observed_at"]:
        history[-1] = sample
    else:
        history.append(sample)
    cutoff = observation.observed_at - timedelta(seconds=30)
    retained: list[dict[str, Any]] = []
    for row in history[-64:]:
        try:
            observed_at = datetime.fromisoformat(
                str(row.get("observed_at") or "").replace("Z", "+00:00")
            )
        except ValueError:
            continue
        if observed_at >= cutoff:
            retained.append(row)
    state["macd_histogram_history_1s"] = retained


def _entry_momentum_strengthening_result(
    observation: StrategyObservation,
    policy: dict[str, Any],
    state: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    timeframe = str(policy.get("timeframe") or "1s")
    line = _numeric_source_value(observation, "indicator.macd.line", timeframe)
    signal = _numeric_source_value(observation, "indicator.macd.signal", timeframe)
    current = line - signal if line is not None and signal is not None else None
    lookback_ms = max(1, int(policy.get("histogram_lookback_ms") or 5_000))
    boundary = observation.observed_at - timedelta(milliseconds=lookback_ms)
    baseline: float | None = None
    baseline_at: datetime | None = None
    for row in state.get("macd_histogram_history_1s") or []:
        if not isinstance(row, dict):
            continue
        try:
            observed_at = datetime.fromisoformat(
                str(row.get("observed_at") or "").replace("Z", "+00:00")
            )
            value = float(row.get("histogram"))
        except (TypeError, ValueError):
            continue
        if observed_at <= boundary and (baseline_at is None or observed_at > baseline_at):
            baseline_at = observed_at
            baseline = value
    minimum_increase = float(policy.get("minimum_histogram_increase") or 0)
    increase = current - baseline if current is not None and baseline is not None else None
    increase_bps = (
        increase / observation.price * 10_000.0
        if increase is not None and observation.price > 0
        else None
    )
    minimum_increase_bps = float(
        policy.get("minimum_histogram_increase_bps") or 0
    )
    checks = {
        "history_ready": baseline is not None,
        "histogram_strengthening": increase is not None and increase > minimum_increase,
        "histogram_strengthening_bps": (
            increase_bps is not None and increase_bps >= minimum_increase_bps
        ),
    }
    return all(checks.values()), {
        "checks": checks,
        "failed": [name for name, passed in checks.items() if not passed],
        "timeframe": timeframe,
        "lookback_ms": lookback_ms,
        "current_histogram": current,
        "baseline_histogram": baseline,
        "baseline_observed_at": baseline_at.isoformat() if baseline_at else "",
        "histogram_increase": increase,
        "histogram_increase_bps": increase_bps,
        "minimum_histogram_increase": minimum_increase,
        "minimum_histogram_increase_bps": minimum_increase_bps,
    }


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
    maximum_admission_spread_bps = float(
        policy.get("maximum_admission_spread_bps")
        if policy.get("maximum_admission_spread_bps") is not None
        else policy.get("maximum_spread_bps") or float("inf")
    )
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
        <= maximum_admission_spread_bps,
    }
    return all(checks.values()), {
        "facts": facts,
        "checks": checks,
        "failed": [name for name, passed in checks.items() if not passed],
    }


def _current_execution_quality_result(
    observation: StrategyObservation,
    policy: dict[str, Any],
    *,
    reentry: bool = False,
) -> tuple[bool, dict[str, Any]]:
    spread = _spread_bps(observation)
    trade_rate_10s = _numeric_source_value(observation, "market.trade_rate_10s")
    trade_rate_60s = _numeric_source_value(observation, "market.trade_rate_60s")
    execution_vwap = observation.execution_vwap or _numeric_source_value(
        observation, "indicator.vwap.execution_value", "1s"
    )
    vwap_extension_bps = (
        (observation.price / execution_vwap - 1.0) * 10_000.0
        if execution_vwap is not None and execution_vwap > 0
        else None
    )
    minimum_trade_rate_10s = float(
        policy.get("minimum_current_trade_rate_10s")
        if policy.get("minimum_current_trade_rate_10s") is not None
        else policy.get("minimum_trade_rate_10s") or 0
    )
    minimum_trade_rate_60s = float(
        policy.get("minimum_current_trade_rate_60s") or 0
    )
    phase_vwap_key = (
        "minimum_reentry_vwap_extension_bps"
        if reentry
        else "minimum_initial_vwap_extension_bps"
    )
    minimum_vwap_extension_bps = float(
        policy.get(phase_vwap_key)
        if policy.get(phase_vwap_key) is not None
        else policy.get("minimum_vwap_extension_bps") or 0
    )
    configured_maximum_vwap_extension_bps = policy.get(
        "maximum_vwap_extension_bps"
    )
    maximum_vwap_extension_bps = (
        float(configured_maximum_vwap_extension_bps)
        if configured_maximum_vwap_extension_bps is not None
        and float(configured_maximum_vwap_extension_bps) > 0
        else None
    )
    maximum_current_spread_bps = float(
        policy.get("maximum_current_spread_bps")
        if policy.get("maximum_current_spread_bps") is not None
        else policy.get("maximum_spread_bps") or float("inf")
    )
    checks = {
        "current_trade_rate_10s": trade_rate_10s is not None
        and trade_rate_10s >= minimum_trade_rate_10s,
        "current_trade_rate_60s": minimum_trade_rate_60s <= 0
        or (
            trade_rate_60s is not None
            and trade_rate_60s >= minimum_trade_rate_60s
        ),
        "current_spread": spread is not None
        and spread <= maximum_current_spread_bps,
        "vwap_extension_floor": minimum_vwap_extension_bps <= 0
        or (
            vwap_extension_bps is not None
            and vwap_extension_bps >= minimum_vwap_extension_bps
        ),
        "vwap_extension_ceiling": maximum_vwap_extension_bps is None
        or (
            vwap_extension_bps is not None
            and vwap_extension_bps <= maximum_vwap_extension_bps
        ),
    }
    spread_dollars = (
        observation.ask - observation.bid
        if observation.ask >= observation.bid > 0
        else (
            spread * observation.price / 10_000.0
            if spread is not None
            else None
        )
    )
    vwap_extension_dollars = (
        observation.price - execution_vwap
        if execution_vwap is not None and execution_vwap > 0
        else None
    )
    thresholds = {
        "phase": "reentry" if reentry else "initial_entry",
        "minimum_trade_rate_10s": minimum_trade_rate_10s,
        "minimum_trade_rate_60s": minimum_trade_rate_60s,
        "maximum_current_spread_bps": maximum_current_spread_bps,
        "maximum_current_spread_dollars": (
            maximum_current_spread_bps * observation.price / 10_000.0
        ),
        "minimum_vwap_extension_bps": minimum_vwap_extension_bps,
        "minimum_vwap_extension_dollars": (
            minimum_vwap_extension_bps * execution_vwap / 10_000.0
            if execution_vwap is not None and execution_vwap > 0
            else None
        ),
    }
    if maximum_vwap_extension_bps is not None:
        thresholds["maximum_vwap_extension_bps"] = maximum_vwap_extension_bps
        thresholds["maximum_vwap_extension_dollars"] = (
            maximum_vwap_extension_bps * execution_vwap / 10_000.0
            if execution_vwap is not None and execution_vwap > 0
            else None
        )
    return all(checks.values()), {
        "facts": {
            "trade_rate_10s": trade_rate_10s,
            "trade_rate_60s": trade_rate_60s,
            "spread_bps": spread,
            "spread_dollars": spread_dollars,
            "vwap_extension_bps": vwap_extension_bps,
            "vwap_extension_dollars": vwap_extension_dollars,
        },
        "thresholds": thresholds,
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


def _level_has_minimum_hold_quality(
    row: Mapping[str, Any],
    minimum_quality: float,
) -> bool:
    """Require the canonical conservative score whenever a threshold is active."""

    if minimum_quality <= 0:
        return True
    raw = row.get("hold_quality_score")
    try:
        quality = float(raw)
    except (TypeError, ValueError):
        return False
    return isfinite(quality) and quality >= minimum_quality


def _level_has_minimum_ticker_relative_quality(
    row: Mapping[str, Any],
    minimum_quality: float,
    *,
    strict: bool,
) -> bool:
    """Apply a configured ticker-relative threshold as a strict gate."""

    if minimum_quality <= 0:
        return True
    status = str(row.get("ticker_relative_quality_status") or "").strip().lower()
    if not strict and status != "available":
        # Revision 34 followed the shared book's informational-status contract.
        # Preserve that behavior only for immutable historical replays.
        return status in {
            "same_session_provisional",
            "insufficient_population",
            "insufficient_level_evidence",
            "unavailable",
        }
    raw = row.get("ticker_relative_quality_score")
    try:
        quality = float(raw)
    except (TypeError, ValueError):
        return False
    return isfinite(quality) and quality >= minimum_quality


def _level_passes_configured_quality(
    row: Mapping[str, Any], policy: Mapping[str, Any]
) -> bool:
    if "minimum_ticker_relative_quality_score" in policy:
        return _level_has_minimum_ticker_relative_quality(
            row,
            float(policy.get("minimum_ticker_relative_quality_score") or 0.0),
            strict=bool(policy.get("strict_ticker_relative_quality_gate", False)),
        )
    minimum_hold_probability = float(policy.get("minimum_hold_probability") or 0.0)
    return bool(
        (
            minimum_hold_probability <= 0
            or _level_metric(dict(row), "hold_probability")
            >= minimum_hold_probability
        )
        and _level_has_minimum_hold_quality(
            row,
            float(policy.get("minimum_hold_quality_score") or 0.0),
        )
    )


def _compact_structural_level_reference(row: Mapping[str, Any] | None) -> dict[str, Any]:
    """Persist only the stable identity and frontier facts needed by the strategy."""

    if not isinstance(row, Mapping):
        return {}
    scalar_fields = (
        "unified_level_id",
        "side",
        "price",
        "lower",
        "upper",
        "salience",
        "confidence",
        "reaction_probability",
        "hold_probability",
        "hold_rate",
        "hold_observation_count",
        "hold_evidence_reliability",
        "hold_quality_score",
        "hold_score_revision",
        "ticker_relative_quality_score",
        "ticker_relative_quality_status",
        "ticker_relative_quality_population_size",
        "ticker_relative_quality_reference_session",
        "ticker_relative_quality_revision",
        "ticker_relative_quality_distribution_hash",
        "reversal_probability",
        "independent_pivot_count",
        "source_count",
        "touch_count",
        "hold_count",
        "break_count",
        "role_flip_count",
        "created_at_ms",
        "confirmed_at_ms",
        "unified_break_boundary",
        "swing_high_boundary",
        "active_resistance_boundary",
        "combined_entry_boundary",
    )
    compact = {
        field: row[field]
        for field in scalar_fields
        if field in row and row[field] is not None
    }
    component_ids = [
        component.get("unified_level_id")
        for component in row.get("component_levels") or ()
        if isinstance(component, Mapping)
        and component.get("unified_level_id") is not None
    ]
    if component_ids:
        compact["component_levels"] = [
            {"unified_level_id": level_id}
            for level_id in dict.fromkeys(component_ids)
        ]
    return compact


def _decision_structural_level_snapshot(
    observation: StrategyObservation,
    parameters: Mapping[str, Any],
) -> dict[str, Any]:
    """Capture the nearest physical supports and resistances at a decision."""

    quality_policy = dict(parameters.get("structural_entry") or {})
    rows = _consolidated_structure_levels(
        [
            dict(row)
            for row in (
                *observation.structural_support_levels,
                *observation.structural_resistance_levels,
            )
            if isinstance(row, Mapping)
        ],
        side="long",
    )
    qualified = [
        row
        for row in rows
        if _level_passes_configured_quality(row, quality_policy)
    ]
    supports = sorted(
        (
            row
            for row in qualified
            if _level_metric(row, "price", "lower", "upper") < observation.price
        ),
        key=lambda row: _level_metric(row, "price", "lower", "upper"),
        reverse=True,
    )[:3]
    resistances = sorted(
        (
            row
            for row in qualified
            if _level_metric(row, "price", "lower", "upper") > observation.price
        ),
        key=lambda row: _level_metric(row, "price", "lower", "upper"),
    )[:3]
    return {
        "observed_at": observation.observed_at.isoformat(),
        "reference_price": observation.price,
        "session_high": observation.structural_session_high,
        "supports": [_compact_structural_level_reference(row) for row in supports],
        "resistances": [
            _compact_structural_level_reference(row) for row in resistances
        ],
    }


def _level_is_entry_quality(
    row: dict[str, Any],
    policy: dict[str, Any],
    *,
    observed_at: datetime,
) -> bool:
    created_at_ms = _level_metric(row, "created_at_ms")
    age_ms = (
        observed_at.timestamp() * 1_000.0 - created_at_ms
        if created_at_ms > 0
        else 0.0
    )
    hold_probability = _level_metric(row, "hold_probability")
    hold_observations = _level_metric(row, "hold_observation_count")
    if "hold_observation_count" not in row:
        hold_observations = (
            _level_metric(row, "hold_count") + _level_metric(row, "break_count")
            if "hold_count" in row or "break_count" in row
            else float("hold_probability" in row)
        )
    break_probability = _level_metric(row, "break_probability")
    if "break_probability" not in row:
        break_probability = max(0.0, 1.0 - hold_probability)
    break_count = _level_metric(row, "break_count")
    maximum_break_count = policy.get("maximum_break_count")
    return bool(
        _level_metric(row, "salience", "strength")
        >= float(policy.get("minimum_salience") or 0)
        and _level_metric(row, "confidence")
        >= float(policy.get("minimum_confidence") or 0)
        and _level_metric(row, "reaction_probability")
        >= float(policy.get("minimum_reaction_probability") or 0)
        and _level_passes_configured_quality(row, policy)
        and hold_observations >= float(policy.get("minimum_hold_observations") or 0)
        and break_probability
        <= float(policy.get("maximum_break_probability", 1.0))
        and (
            maximum_break_count is None
            or break_count <= float(maximum_break_count)
        )
        and _level_metric(row, "independent_pivot_count")
        >= float(policy.get("minimum_independent_pivot_count") or 0)
        and age_ms >= float(policy.get("minimum_level_age_ms") or 0)
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
                _level_metric(
                    item,
                    "ticker_relative_quality_score",
                    "hold_quality_score",
                    "hold_probability",
                ),
                _level_metric(item, "hold_observation_count"),
                _level_metric(item, "role_flip_count"),
                _level_metric(item, "independent_pivot_count"),
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
    selection_mode = str(policy.get("selection_mode") or "").lower()
    if selection_mode == _COMPLETED_FRAME_TOP_N_ENTRY_MODE:
        return _prior_completed_frame_resistance_trigger(
            observation,
            policy,
            state,
        )
    if selection_mode == _EVENT_PRICE_TOP_N_ENTRY_MODE:
        return _event_price_top_n_resistance_trigger(
            observation,
            policy,
            state,
        )
    buffer_bps = float(policy.get("acceptance_buffer_bps") or 0)
    previous_price = state.get("previous_observed_price")
    # A level's stored side is its last confirmed lifecycle role, not an
    # immutable geometric role.  A former support band above current price is
    # overhead structure and must be eligible for a long breakout; excluding
    # it makes the strategy skip causal swing highs after gaps and pullbacks.
    # Consolidate the complete level book, then classify by price below.
    rows = _consolidated_structure_levels([
        dict(row)
        for row in (
            *observation.structural_support_levels,
            *observation.structural_resistance_levels,
        )
        if isinstance(row, dict)
        and _level_is_entry_quality(
            dict(row), policy, observed_at=observation.observed_at
        )
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
    crossed_now = [
        item
        for item in usable
        if previous_price is not None
        and float(previous_price) <= item[0]
        and observation.price > item[0]
    ]
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
    acceptance_expires = bool(policy.get("acceptance_expires", True))
    accepted_threshold = accepted_boundary * (1 + buffer_bps / 10_000)
    if accepted_boundary > 0 and (
        not acceptance_expires or acceptance_age_ms <= acceptance_hold_ms
    ):
        # Acceptance belongs to the local structural crossing. Higher levels
        # are subsequent resistance/target evidence; they do not invalidate a
        # breakout while MACD or liquidity confirmation is catching up.
        # While other confirmation gates are still catching up, a newly
        # crossed higher qualified level becomes the current causal frontier.
        # Keeping the first accepted band forever made a healthy campaign wait
        # for a pullback to obsolete lower structure after price had already
        # accepted several successor levels.
        successor = [item for item in crossed_now if item[0] > accepted_boundary]
        if successor:
            accepted_boundary, accepted_level = max(successor, key=lambda item: item[0])
            accepted_threshold = accepted_boundary * (1 + buffer_bps / 10_000)
            accepted_at = observation.observed_at
            acceptance_age_ms = 0.0
            state["accepted_entry_resistance"] = {
                "boundary": accepted_boundary,
                "level": _compact_structural_level_reference(accepted_level),
                "accepted_at": observation.observed_at.isoformat(),
                "acceptance_reason": "successor_resistance_crossed",
                "acceptance_previous_price": previous_price,
                "acceptance_observed_price": observation.price,
                "acceptance_threshold_price": accepted_threshold,
            }
            accepted = state["accepted_entry_resistance"]
        breakout_extension_bps = (
            (observation.price / accepted_threshold - 1.0) * 10_000.0
            if accepted_threshold > 0
            else float("inf")
        )
        maximum_breakout_extension_bps = float(
            policy.get("maximum_breakout_extension_bps") or float("inf")
        )
        if observation.price > accepted_threshold:
            passed = breakout_extension_bps <= maximum_breakout_extension_bps
            if passed:
                return {
                    "passed": True,
                    "reason": "unified_resistance_acceptance_held",
                    "level": accepted_level,
                    "reference_price": accepted_boundary,
                    "threshold_price": accepted_threshold,
                    "previous_price": previous_price,
                    "accepted_at": accepted.get("accepted_at"),
                    "acceptance_reason": accepted.get("acceptance_reason"),
                    "acceptance_previous_price": accepted.get(
                        "acceptance_previous_price"
                    ),
                    "acceptance_observed_price": accepted.get(
                        "acceptance_observed_price"
                    ),
                    "acceptance_threshold_price": accepted.get(
                        "acceptance_threshold_price"
                    ),
                    "acceptance_age_ms": acceptance_age_ms,
                    "acceptance_hold_ms": acceptance_hold_ms,
                    "acceptance_expires": acceptance_expires,
                    "breakout_extension_bps": breakout_extension_bps,
                    "maximum_breakout_extension_bps": maximum_breakout_extension_bps,
                }
            # The extension ceiling is an explicit declaration that this
            # accepted frontier is no longer locally actionable. Retire it and
            # immediately re-arm from the current qualified book instead of
            # requiring a retest of obsolete lower resistance.
            state["retired_entry_resistance"] = {
                "boundary": accepted_boundary,
                "level": _compact_structural_level_reference(accepted_level),
                "retired_at": observation.observed_at.isoformat(),
                "reason": "maximum_breakout_extension_exceeded",
                "breakout_extension_bps": breakout_extension_bps,
            }
            state.pop("accepted_entry_resistance", None)
            accepted_boundary = 0.0
        # A breakout acceptance is not a permanent reservation of that price
        # band. Returning below it invalidates the entry latch, after which a
        # newly crossed qualified level may become the causal frontier.
        else:
            state.pop("accepted_entry_resistance", None)
    else:
        state.pop("accepted_entry_resistance", None)

    # Arm one causal frontier and wait for price to clear that frontier.  A
    # newly formed *closer* resistance may tighten the watched threshold, but a
    # later higher band must not make the strategy chase price.  This is the
    # event-driven equivalent of selecting the current swing high and entering
    # on its next pass.
    overhead = [item for item in usable if item[0] >= observation.price]
    candidate_boundary, candidate_level = (
        # A real-time cross is the causal entry event. It must take precedence
        # over a more distant overhead band; otherwise a newly opened MACD
        # regime is incorrectly forced to wait for the *next* swing high.
        max(crossed_now, key=lambda item: item[0])
        if crossed_now
        else min(overhead, key=lambda item: item[0])
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
    crossed_after_arming = bool(
        previous_price is not None
        and float(previous_price) <= threshold
        and observation.price > threshold
    )
    # Early-squeeze and liquidity admission are campaign latches. If the
    # campaign first becomes actionable after price has already cleared the
    # highest qualifying resistance, admit that causal state instead of
    # demanding an artificial second cross of the same level.
    first_post_admission_evaluation = bool(
        state.get("liquidity_admitted_at")
        and not state.get("structural_entry_initialized_at")
        and int(state.get("reentries") or 0) == 0
        and not state.get("last_entry_resistance")
    )
    state.setdefault(
        "structural_entry_initialized_at", observation.observed_at.isoformat()
    )
    already_cleared_on_admission = bool(
        first_post_admission_evaluation
        and pending_boundary <= 0
        and not overhead
        and observation.price > threshold
    )
    crossed = crossed_after_arming or already_cleared_on_admission
    breakout_extension_bps = (
        (observation.price / threshold - 1.0) * 10_000.0
        if threshold > 0
        else float("inf")
    )
    maximum_breakout_extension_bps = float(
        policy.get("maximum_breakout_extension_bps") or float("inf")
    )
    passed = bool(
        crossed
        and breakout_extension_bps <= maximum_breakout_extension_bps
    )
    if passed:
        state["accepted_entry_resistance"] = {
            "boundary": boundary,
            "level": _compact_structural_level_reference(level),
            "accepted_at": observation.observed_at.isoformat(),
            "acceptance_reason": (
                "initial_campaign_already_cleared"
                if already_cleared_on_admission
                else "resistance_crossed_after_arming"
            ),
            "acceptance_previous_price": previous_price,
            "acceptance_observed_price": observation.price,
            "acceptance_threshold_price": threshold,
        }
        state.pop("pending_entry_resistance", None)
    else:
        state["pending_entry_resistance"] = {
            "boundary": boundary,
            "level": _compact_structural_level_reference(level),
            "armed_at": armed_at,
        }
    return {
        "passed": passed,
        "reason": (
            "unified_resistance_already_cleared"
            if passed and already_cleared_on_admission
            else "unified_resistance_accepted"
            if passed
            else "waiting_for_unified_resistance_retest"
            if crossed and breakout_extension_bps > maximum_breakout_extension_bps
            else "waiting_for_unified_resistance_break"
        ),
        "level": level,
        "reference_price": boundary,
        "threshold_price": threshold,
        "previous_price": previous_price,
        "breakout_extension_bps": breakout_extension_bps,
        "maximum_breakout_extension_bps": maximum_breakout_extension_bps,
    }


def _prior_completed_frame_resistance_trigger(
    observation: StrategyObservation,
    policy: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate completed-close entry evidence using the versioned contract."""

    observed_at = observation.observed_at.isoformat()
    # Remove legacy timed-cross frontier state. Revision 43 keeps separate
    # R3 confirmation evidence that is revalidated against each current book.
    state.pop("accepted_entry_resistance", None)
    state.pop("pending_entry_resistance", None)
    completed_one_second = bool(
        "bar_close" in observation.evaluation_events
        and observation.source_timeframe.lower() in {"", "1s"}
    )
    intrabar = bool(
        policy.get("intrabar_after_completed_r3")
        and "market_data_update" in observation.evaluation_events
        and observation.source_timeframe.lower() in {"", "1s"}
    )
    if not completed_one_second and not intrabar:
        return {
            "passed": False,
            "reason": "waiting_for_completed_one_second_resistance_snapshot",
            "level": None,
            "observed_at": observed_at,
        }
    cached = state.get("latest_structural_entry_trigger")
    if (
        completed_one_second and isinstance(cached, Mapping)
        and cached.get("observed_at") == observed_at
        and cached.get("completed_one_second") is True
    ):
        return dict(cached)

    prior = state.get("qualified_entry_resistance_snapshot")
    prior_levels = (
        [dict(row) for row in prior.get("levels") or () if isinstance(row, Mapping)]
        if isinstance(prior, Mapping)
        else []
    )
    prior_close = (
        _level_metric(dict(prior), "reference_close")
        if isinstance(prior, Mapping)
        else 0.0
    )
    prior_selected_at = (
        _optional_aware_datetime(prior.get("selected_at"))
        if isinstance(prior, Mapping)
        else None
    )
    prior_is_immediate = bool(
        prior_selected_at is not None
        and 0.0
        < (observation.observed_at - prior_selected_at).total_seconds()
        <= 1.001
    )
    buffer_bps = float(policy.get("acceptance_buffer_bps") or 0.0)
    crossed: list[dict[str, Any]] = []
    current_by_id = {str(row.get("unified_level_id")): row for row in
                     (*observation.structural_resistance_levels, *observation.structural_support_levels)}
    current_candidates = sorted(
        (row for row in observation.structural_resistance_levels
         if int(row.get("side") or 0) < 0
         and 0 < _level_metric(row, "price", "upper", "lower") <= float(observation.structural_session_high or 0)
         and _level_is_entry_quality(row, policy, observed_at=observation.observed_at)),
        key=lambda row: -_level_metric(row, "price", "upper", "lower"),
    )[:max(1, int(policy.get("maximum_entry_levels") or 3))] if policy.get("follow_current_level_prices") else []
    current_candidate_ids = {str(row.get("unified_level_id")) for row in current_candidates}
    for level in prior_levels if prior_is_immediate else ():
        if policy.get("follow_current_level_prices"):
            identity = str(level.get("unified_level_id"))
            current = current_by_id.get(identity)
            if current is None or not _level_is_entry_quality(current, policy, observed_at=observation.observed_at):
                continue
            if int(current.get("side") or 0) < 0 and identity not in current_candidate_ids:
                continue
            level = {**level, **_compact_structural_level_reference(current),
                     "entry_boundary": _level_metric(current, "price", "upper", "lower")}
            if level["entry_boundary"] > float(observation.structural_session_high or 0):
                continue
        boundary = _level_metric(level, "entry_boundary", "price")
        threshold = boundary * (1.0 + buffer_bps / 10_000.0)
        if boundary > 0 and prior_close <= threshold and observation.price > threshold:
            crossed.append({**level, "entry_boundary": boundary, "threshold_price": threshold})

    selected_cross = (
        max(crossed, key=lambda row: float(row["entry_boundary"]))
        if crossed
        else None
    )
    result: dict[str, Any] = {
        "passed": selected_cross is not None,
        "completed_one_second": completed_one_second,
        "reason": (
            "prior_completed_one_second_resistance_crossed"
            if selected_cross is not None
            else "waiting_for_prior_top_resistance_cross"
            if prior_levels and prior_is_immediate
            else "waiting_for_fresh_prior_completed_one_second_resistance_snapshot"
            if prior_levels
            else "waiting_for_prior_completed_one_second_resistance_snapshot"
        ),
        "level": dict(selected_cross or {}) or None,
        "reference_price": (
            float(selected_cross["entry_boundary"])
            if selected_cross is not None
            else None
        ),
        "threshold_price": (
            float(selected_cross["threshold_price"])
            if selected_cross is not None
            else None
        ),
        "previous_price": prior_close or None,
        "observed_at": observed_at,
        "prior_snapshot_selected_at": (
            prior.get("selected_at") if isinstance(prior, Mapping) else None
        ),
        "prior_snapshot_is_immediate": prior_is_immediate,
        "prior_snapshot_session_high": (
            prior.get("session_high") if isinstance(prior, Mapping) else None
        ),
        "prior_snapshot_levels": prior_levels,
        "crossed_level_ids": [
            str(row.get("unified_level_id") or "") for row in crossed
        ],
    }

    session_high = observation.structural_session_high
    maximum_levels = max(1, int(policy.get("maximum_entry_levels") or 3))
    qualified: list[dict[str, Any]] = []
    if session_high is not None and session_high > 0:
        for raw in observation.structural_resistance_levels:
            if not isinstance(raw, Mapping):
                continue
            row = dict(raw)
            if int(row.get("side") or 0) >= 0:
                continue
            if policy.get("persistent_r3_acceptance"):
                try:
                    if any(not isfinite(float(row[key])) or float(row[key]) > observation.observed_at.timestamp() * 1000
                           for key in ("created_at_ms", "confirmed_at_ms", "updated_at_ms")
                           if row.get(key) is not None):
                        continue
                except (TypeError, ValueError):
                    continue
            price = _level_metric(row, "price", "upper", "lower")
            if (
                price <= 0
                or price > session_high
                or not _level_is_entry_quality(
                    row, policy, observed_at=observation.observed_at
                )
            ):
                continue
            qualified.append({
                **_compact_structural_level_reference(row),
                "side": int(row.get("side") or -1),
                "price": price,
                "entry_boundary": price,
                "hold_probability": _level_metric(row, "hold_probability"),
            })
    qualified.sort(
        key=lambda row: (
            -float(row["entry_boundary"]),
            str(row.get("unified_level_id") or ""),
        )
    )
    # The entry set is always the highest N qualified resistance records whose
    # level price is not above the live session high.  Session-high band
    # containment is deliberately not a separate authority: it previously
    # changed which records were selected and made a visual "near the high"
    # interpretation affect an otherwise exact level-book ordering contract.
    current_levels = qualified[:maximum_levels]
    top_selection = "highest_qualified_levels_below_session_high"
    state["qualified_entry_resistance_snapshot"] = {
        "selected_at": observed_at,
        "session_high": session_high,
        "reference_close": observation.price,
        "maximum_entry_levels": maximum_levels,
        "top_selection": top_selection,
        "levels": current_levels,
    }
    result["current_snapshot"] = dict(state["qualified_entry_resistance_snapshot"])
    if policy.get("persistent_r3_acceptance"):
        # R1 is nearest HOD; R3 is third down. A fresh crossover is not
        # required when other gates become ready on a later completed bar.
        # Re-select from the causal book each time, never a latched ladder.
        r3 = current_levels[2] if len(current_levels) >= 3 else None
        boundary = float(r3["entry_boundary"]) if r3 else None
        above = boundary is not None and observation.price > boundary
        non_red = bool(observation.bar_open is not None and observation.bar_open > 0
                       and observation.price >= observation.bar_open)
        accepted = state.get("accepted_entry_r3")
        same_level = bool(
            isinstance(accepted, Mapping) and r3
            and accepted.get("unified_level_id") == r3.get("unified_level_id")
            and accepted.get("threshold_price") == boundary
        )
        if same_level and policy.get("intrabar_after_completed_r3"):
            accepted_at = _optional_aware_datetime(accepted.get("accepted_at"))
            same_level = bool(accepted_at and accepted_at <= observation.observed_at
                              and _level_metric(accepted, "confirmation_close") > boundary)
        if not above or not same_level:
            state.pop("accepted_entry_r3", None)
        if above and non_red and completed_one_second and "accepted_entry_r3" not in state:
            state["accepted_entry_r3"] = {
                "unified_level_id": r3.get("unified_level_id"),
                "threshold_price": boundary,
                "accepted_at": observed_at,
                "confirmation_close": observation.price,
            }
        accepted = state.get("accepted_entry_r3")
        passed = bool(above and non_red and accepted)
        result.update(
            passed=passed,
            reason=("current_r3_completed_close_accepted" if passed and completed_one_second else
                    "current_r3_acceptance_valid_intrabar" if passed else
                    "waiting_for_three_qualified_entry_resistances" if r3 is None else
                    "entry_closed_candle_bearish" if not non_red else
                    "waiting_for_completed_close_above_current_r3"),
            level={**r3, "threshold_price": boundary} if r3 else None,
            reference_price=boundary,
            threshold_price=boundary,
            acceptance=dict(accepted) if accepted else None,
            # This is an above-R3 acceptance, not a new crossing of R1/R2.
            crossed_level_ids=[],
        )
    state["latest_structural_entry_trigger"] = result
    return result


def _event_price_top_n_resistance_trigger(
    observation: StrategyObservation,
    policy: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    """Cross the live top-N qualified resistance set on each causal trade event."""

    observed_at = observation.observed_at.isoformat()
    state.pop("accepted_entry_resistance", None)
    state.pop("pending_entry_resistance", None)
    session_high = observation.structural_session_high
    maximum_levels = max(1, int(policy.get("maximum_entry_levels") or 3))
    qualified: list[dict[str, Any]] = []
    if session_high is not None and session_high > 0:
        for raw in observation.structural_resistance_levels:
            if not isinstance(raw, Mapping):
                continue
            row = dict(raw)
            if int(row.get("side") or 0) >= 0:
                continue
            price = _level_metric(row, "price", "upper", "lower")
            if (
                price <= 0
                or price > session_high
                or not _level_is_entry_quality(
                    row, policy, observed_at=observation.observed_at
                )
            ):
                continue
            qualified.append({
                **_compact_structural_level_reference(row),
                "side": int(row.get("side") or -1),
                "price": price,
                "entry_boundary": price,
            })
    qualified.sort(
        key=lambda row: (
            -float(row["entry_boundary"]),
            str(row.get("unified_level_id") or ""),
        )
    )
    current_levels = qualified[:maximum_levels]
    previous_price = state.get("previous_observed_price")
    crossing_levels = list(current_levels)
    if policy.get("retain_crossing_role_flip"):
        # The producer can flip the crossed resistance to support on this very
        # trade. Keep its prior candidacy, using its current producer record.
        prior_ids = {str(row.get("unified_level_id") or "")
                     for row in dict(state.get("qualified_entry_resistance_snapshot") or {}).get("levels") or ()}
        for row in observation.structural_support_levels:
            identity = str(row.get("unified_level_id") or "")
            boundary = _level_metric(dict(row), "price", "upper", "lower")
            if (identity and identity in prior_ids and 0 < boundary <= float(session_high or 0)
                    and _level_is_entry_quality(row, policy, observed_at=observation.observed_at)):
                crossing_levels.append({**_compact_structural_level_reference(row),
                                        "price": boundary, "entry_boundary": boundary,
                                        "crossing_role_flip": True})
    buffer_bps = float(policy.get("acceptance_buffer_bps") or 0.0)
    is_trade_event = "market_data_update" in observation.evaluation_events
    crossed: list[dict[str, Any]] = []
    if is_trade_event and previous_price is not None:
        for level in crossing_levels:
            boundary = float(level["entry_boundary"])
            threshold = boundary * (1.0 + buffer_bps / 10_000.0)
            if float(previous_price) <= threshold and observation.price > threshold:
                crossed.append({**level, "threshold_price": threshold})
    selected_cross = (
        max(crossed, key=lambda row: float(row["entry_boundary"]))
        if crossed
        else None
    )
    snapshot = {
        "selected_at": observed_at,
        "session_high": session_high,
        "reference_price": observation.price,
        "maximum_entry_levels": maximum_levels,
        "top_selection": "highest_qualified_levels_below_session_high",
        "levels": current_levels,
    }
    state["qualified_entry_resistance_snapshot"] = snapshot
    result: dict[str, Any] = {
        "passed": selected_cross is not None,
        "event_native": True,
        "reason": (
            "event_price_top_resistance_crossed"
            if selected_cross is not None
            else "waiting_for_event_price_top_resistance_cross"
            if current_levels
            else "waiting_for_event_price_top_resistance_snapshot"
        ),
        "level": dict(selected_cross or {}) or None,
        "reference_price": (
            float(selected_cross["entry_boundary"])
            if selected_cross is not None
            else None
        ),
        "threshold_price": (
            float(selected_cross["threshold_price"])
            if selected_cross is not None
            else None
        ),
        "previous_price": previous_price,
        "observed_price": observation.price,
        "observed_at": observed_at,
        "current_snapshot": snapshot,
        "crossed_level_ids": [
            str(row.get("unified_level_id") or "") for row in crossed
        ],
    }
    state["latest_structural_entry_trigger"] = result
    return result


def _entry_rule_result_with_unified_trigger(
    rule_result: dict[str, Any],
    unified_trigger: Mapping[str, Any],
    observation: StrategyObservation,
) -> dict[str, Any]:
    """Project the executor's structural authority into the decision audit."""

    group_id = "strategy-squeeze-unified-resistance-break"
    passed = bool(unified_trigger.get("passed"))
    reference = unified_trigger.get("reference_price")
    threshold = unified_trigger.get("threshold_price")
    trigger_stage = {
        "operator": "any",
        "groups": {group_id: passed},
        "group_scores": {group_id: 1.0 if passed else 0.0},
        "matched_groups": [group_id] if passed else [],
        "condition_evidence": {
            group_id: [{
                "condition_id": "squeeze-price-over-unified-resistance",
                "comparator": "above_by_bps",
                "left_source_id": "data.market.last_price@1:value",
                "left_timeframe": "",
                "left_value": observation.price,
                "right_source_id": "data.indicator.structure.unified_resistance_upper@1:value",
                "right_timeframe": "1s",
                "right_value": reference,
                "buffer_bps": 0.0,
                "threshold_value": threshold,
                "passed": passed,
            }],
        },
        "passed": passed,
        "score": 1.0 if passed else 0.0,
    }
    return {**rule_result, "trigger": trigger_stage}


class LongMomentumStrategyEngine:
    """Deterministic long-only policy engine over causal point-in-time observations."""

    def __init__(self, *, revision: int = STRATEGY_REVISION) -> None:
        if revision not in {*HISTORICAL_STRATEGY_REVISIONS, STRATEGY_REVISION}:
            raise ValueError(f"Unsupported Long Momentum Strategy revision: {revision}")
        self.revision = revision

    def evaluate(self, assignment: StrategyAssignment, observation: StrategyObservation) -> StrategyEngineResult:
        if assignment.strategy_revision != self.revision:
            raise ValueError(
                "Strategy assignment revision does not match Long Momentum executor revision"
            )
        if assignment.ticker.upper() != observation.ticker.upper():
            raise ValueError("Observation ticker does not match strategy assignment")
        state = dict(assignment.state)
        status = assignment.status
        parameters = resolve_long_momentum_parameters(
            assignment.parameters,
            revision=self.revision,
        )
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
        if status == AssignmentStatus.EXIT_PENDING and observation.position_quantity > 0 and self.revision < 37:
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
        _record_macd_histogram_history(state, observation)
        _record_structural_anchors(state, observation)
        structural_policy = dict(parameters.get("structural_entry") or {})
        if (
            bool(structural_policy.get("enabled", False))
            and str(structural_policy.get("selection_mode") or "").lower()
            in _TOP_N_SESSION_HIGH_ENTRY_MODES
        ):
            if (
                str(structural_policy.get("selection_mode") or "").lower()
                == _COMPLETED_FRAME_TOP_N_ENTRY_MODE
            ):
                _prior_completed_frame_resistance_trigger(
                    observation,
                    structural_policy,
                    state,
                )
            else:
                _event_price_top_n_resistance_trigger(
                    observation,
                    structural_policy,
                    state,
                )
        if self.revision >= 37 and status == AssignmentStatus.EXIT_PENDING:
            if self.revision >= 39 and observation.position_quantity <= observation.pending_exit_quantity + 1e-9:
                return self._result(
                    assignment, observation, "hold", "exit_fill_pending", 0.0, 1.0,
                    state, AssignmentStatus.EXIT_PENDING,
                )
            return self._result(
                assignment, observation, "exit", str(state.get("last_exit_reason") or "exit_pending"),
                0.0, 1.0, state, AssignmentStatus.EXIT_PENDING,
                quantity=observation.position_quantity,
                metadata={"cancel_entry_acquisition": True, "position_fraction": 1.0},
            )
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
        pending_capital = dict(state.get("pending_capital_request") or {})
        if self.revision >= 41 and pending_capital:
            # Waiting for cash does not suspend exit authority. No broker
            # position/order exists yet, so invalidation withdraws the request.
            probe = self._evaluate_position(
                replace(assignment, status=AssignmentStatus.ENTRY_PENDING),
                observation, parameters, dict(state),
            )
            if any(i.action in {"exit", "cover"} for i in probe.evaluation.intents):
                state.pop("pending_capital_request", None)
                return self._result(assignment, observation, "wait", "capital_request_invalidated",
                    0.0, 1.0, state, AssignmentStatus.WATCHING,
                    metadata={"request_id": pending_capital["request_id"],
                              "exit_reason": probe.evaluation.intents[0].reason})
        if assignment.status == AssignmentStatus.ENTRY_PENDING:
            if self.revision >= 37:
                return self._evaluate_position(assignment, observation, parameters, state)
            return self._result(assignment, observation, "wait", "entry_fill_pending", 0.0, 1.0, state, AssignmentStatus.ENTRY_PENDING)
        if assignment.status == AssignmentStatus.EXIT_PENDING and self.revision >= 37:
            return self._result(
                assignment, observation, "exit", str(state.get("last_exit_reason") or "exit_pending"),
                0.0, 1.0, state, AssignmentStatus.EXIT_PENDING,
                metadata={"cancel_entry_acquisition": True, "position_fraction": 1.0},
            )
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

        structural_selection_mode = str(
            dict(parameters.get("structural_entry") or {}).get("selection_mode") or ""
        ).lower()
        if (
            reentries
            and structural_selection_mode not in _TOP_N_SESSION_HIGH_ENTRY_MODES
        ):
            pullback_result = self._regular_reentry_pullback_result(
                assignment,
                observation,
                parameters,
                state,
            )
            if pullback_result is not None:
                return pullback_result

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
        if self.revision >= 41 and pending_capital:
            witness = dict(pending_capital.get("trigger") or {})
            identity = str(dict(witness.get("level") or {}).get("unified_level_id") or "")
            current = next((dict(row) for row in
                (*observation.structural_resistance_levels, *observation.structural_support_levels)
                if identity and str(row.get("unified_level_id") or "") == identity), None)
            if current is not None:
                boundary = _level_metric(current, "price", "entry_boundary")
                valid = (_level_passes_configured_quality(current, parameters["structural_entry"])
                         and observation.price > boundary > 0)
            else:
                valid = False
            if not valid:
                state.pop("pending_capital_request", None)
                return self._result(assignment, observation, "wait", "capital_request_structure_invalidated",
                    0.0, 1.0, state, AssignmentStatus.WATCHING,
                    metadata={"request_id": pending_capital["request_id"]})
            unified_trigger = {**witness, "passed": True, "level": current,
                "reference_price": boundary, "threshold_price": boundary,
                "reason": "pending_capital_breakout_revalidated",
                "original_cross_at": pending_capital["requested_at"],
                "revalidated_at": observation.observed_at.isoformat()}

        liquidity_policy = dict(parameters.get("liquidity_admission") or {})
        execution_detail: dict[str, Any] = {}
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
                observation, liquidity_policy, reentry=bool(reentries)
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
        if unified_trigger is not None:
            rule_result = _entry_rule_result_with_unified_trigger(
                rule_result,
                unified_trigger,
                observation,
            )
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

        candle_policy = dict(parameters.get("entry_candle_confirmation") or {})
        entry_timeframe = str(candle_policy.get("timeframe") or "1s").lower()
        closed_entry_frame = (
            "bar_close" in observation.evaluation_events
            and observation.source_timeframe.lower() in {"", entry_timeframe}
        )
        if (
            bool(candle_policy.get("enabled", True))
            and bool(candle_policy.get("require_closed_bar", True))
            and not bool(candle_policy.get("evaluate_macd_intrabar", False))
            and not closed_entry_frame
            and not observation.force_entry
        ):
            return self._result(
                assignment,
                observation,
                "wait",
                "entry_waiting_for_closed_one_second_macd",
                confirmation_score,
                _confirmation_confidence(observation),
                state,
                AssignmentStatus.REENTRY_COOLDOWN
                if reentries
                else AssignmentStatus.WATCHING,
                metadata={
                    "triggers": triggered,
                    "vetoes": vetoes,
                    "confirmation": confirmation,
                    "entry_rules": rule_result,
                    "unified_structural_trigger": unified_trigger,
                    "entry_frame": {
                        "required_timeframe": entry_timeframe,
                        "source_timeframe": observation.source_timeframe,
                        "evaluation_events": list(observation.evaluation_events),
                    },
                },
            )

        # This strategy's momentum regime is a semantic invariant, not merely
        # one editable rule-set row. Configuration materialization, re-entry
        # rule pruning, or a future catalog migration must never authorize an
        # order unless the latest causal one-second MACD is exactly open and
        # positive and open: line > signal and line > 0. The signal line may
        # still be below zero during an early momentum turn.
        entry_macd_open, entry_macd_evidence = _exact_positive_open_macd(
            observation,
            "1s",
            require_positive_signal=self.revision == 26,
        )
        if not entry_macd_open and not observation.force_entry:
            return self._result(
                assignment,
                observation,
                "wait",
                "entry_macd_not_positive_open",
                confirmation_score,
                _confirmation_confidence(observation),
                state,
                AssignmentStatus.REENTRY_COOLDOWN
                if reentries
                else AssignmentStatus.WATCHING,
                metadata={
                    "triggers": triggered,
                    "vetoes": vetoes,
                    "confirmation": confirmation,
                    "entry_rules": rule_result,
                    "unified_structural_trigger": unified_trigger,
                    "macd": entry_macd_evidence,
                },
            )

        minimum_macd_gap_bps = max(
            0.0, float(candle_policy.get("minimum_macd_open_gap_bps") or 0.0)
        )
        macd_gap_bps = (
            max(0.0, float(observation.macd_line or 0) - float(observation.macd_signal or 0))
            / observation.price
            * 10_000.0
        )
        if self.revision >= 44:
            macd_gap_bps = _macd_gap_bps(observation.price,
                                       entry_macd_evidence["macd_line"],
                                       entry_macd_evidence["macd_signal"])
        if (
            minimum_macd_gap_bps > 0
            and (macd_gap_bps is None or macd_gap_bps + 1e-12 < minimum_macd_gap_bps)
            and not observation.force_entry
        ):
            return self._result(
                assignment,
                observation,
                "wait",
                "entry_macd_open_gap_below_threshold",
                confirmation_score,
                _confirmation_confidence(observation),
                state,
                AssignmentStatus.REENTRY_COOLDOWN
                if reentries
                else AssignmentStatus.WATCHING,
                metadata={
                    "triggers": triggered,
                    "vetoes": vetoes,
                    "confirmation": confirmation,
                    "entry_rules": rule_result,
                    "unified_structural_trigger": unified_trigger,
                    "macd": {
                        **entry_macd_evidence,
                        "open_gap_bps": macd_gap_bps,
                        "minimum_open_gap_bps": minimum_macd_gap_bps,
                    },
                },
            )

        # Revision 44 applies this gate to the current forming candle too.
        # Completed R3 acceptance is separate from real-time MACD readiness.
        side = _strategy_side(parameters)
        if (
            bool(candle_policy.get("enabled", True))
            and bool(candle_policy.get("reject_bearish_close", True))
            and not observation.force_entry
        ):
            bar_open = observation.bar_open
            acceptable_close = (
                bar_open is not None
                and bar_open > 0
                and (
                    observation.price >= bar_open
                    if side == "long"
                    else observation.price <= bar_open
                )
            )
            if not acceptable_close:
                return self._result(
                    assignment,
                    observation,
                    "wait",
                    "entry_closed_candle_bearish",
                    confirmation_score,
                    _confirmation_confidence(observation),
                    state,
                    AssignmentStatus.REENTRY_COOLDOWN
                    if reentries
                    else AssignmentStatus.WATCHING,
                    metadata={
                        "triggers": triggered,
                        "vetoes": vetoes,
                        "confirmation": confirmation,
                        "entry_rules": rule_result,
                        "unified_structural_trigger": unified_trigger,
                        "completed_candle": {
                            "timeframe": entry_timeframe,
                            "side": side,
                            "open": bar_open,
                            "close": observation.price,
                            "required": "close >= open" if side == "long" else "close <= open",
                        },
                    },
                )

        momentum_detail: dict[str, Any] = {}
        momentum_policy = dict(parameters.get("entry_momentum_confirmation") or {})
        if bool(momentum_policy.get("enabled", False)) and not observation.force_entry:
            momentum_ready, momentum_detail = _entry_momentum_strengthening_result(
                observation,
                momentum_policy,
                state,
            )
            if not momentum_ready:
                return self._result(
                    assignment,
                    observation,
                    "wait",
                    "entry_momentum_not_strengthening",
                    confirmation_score,
                    _confirmation_confidence(observation),
                    state,
                    AssignmentStatus.REENTRY_COOLDOWN
                    if reentries
                    else AssignmentStatus.WATCHING,
                    metadata={
                        "triggers": triggered,
                        "vetoes": vetoes,
                        "confirmation": confirmation,
                        "entry_rules": rule_result,
                        "unified_structural_trigger": unified_trigger,
                        "entry_momentum_confirmation": momentum_detail,
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

        protective_stop_selection: dict[str, Any] = {}
        stop = _initial_stop(
            observation,
            parameters,
            reference,
            side=side,
            selection_evidence=protective_stop_selection,
        )
        if stop <= 0:
            return self._result(
                assignment, observation, "wait", "qualified_support_unavailable", 0.0, 1.0,
                state, assignment.status,
                metadata={"protective_stop_selection": protective_stop_selection},
            )
        trailing_amount = _trailing_amount(
            observation,
            parameters,
            stop=stop,
        )
        capital_request = _phase_capital_request(
            parameters,
            phase_name,
            fallback_quantity=float(parameters["sizing"]["initial_quantity"]),
        )
        structural_entry_policy = dict(parameters.get("structural_entry") or {})
        entry_level_ids = _structural_entry_level_ids(unified_trigger)
        entry_tranche_count = max(
            1,
            int(
                structural_entry_policy.get("entry_tranche_count")
                or structural_entry_policy.get("maximum_entry_levels")
                or 1
            ),
        )
        quantity = (
            capital_request.value
            if capital_request.mode == "fixed_quantity"
            else 0.0
        )
        target = _luld_target(observation, parameters, side=side)
        luld_target = target
        profit_target_selection: dict[str, Any] = {}
        profit_targets = _structural_profit_targets(
            observation,
            parameters,
            stop=stop,
            side=side,
            luld_target=target,
            selection_evidence=profit_target_selection,
        )
        if profit_targets:
            target = profit_targets[0]
        elif self.revision >= 37:
            return self._result(
                assignment, observation, "wait", "qualified_target_unavailable", 0.0, 1.0,
                state, assignment.status, metadata={"profit_target_selection": profit_target_selection},
            )
        profit_policy = dict(parameters["protection"].get("profit_ladder") or {})
        minimum_entry_target_gap_bps = max(
            0.0,
            float(profit_policy.get("minimum_entry_target_gap_bps") or 0.0),
        )
        blocked_room = dict(state.get("entry_target_room_retest") or {})
        blocked_boundary = _level_metric(blocked_room, "boundary")
        if blocked_boundary > 0:
            still_beyond_boundary = (
                observation.price > blocked_boundary
                if side == "long"
                else observation.price < blocked_boundary
            )
            if still_beyond_boundary:
                return self._result(
                    assignment,
                    observation,
                    "wait",
                    "structural_target_room_retest_required",
                    confirmation_score,
                    _confirmation_confidence(observation),
                    state,
                    AssignmentStatus.REENTRY_COOLDOWN
                    if reentries
                    else AssignmentStatus.WATCHING,
                    metadata={
                        "triggers": triggered,
                        "confirmation": confirmation,
                        "entry_rules": rule_result,
                        "unified_structural_trigger": unified_trigger,
                        "entry_target_room_retest": blocked_room,
                        "current_price": observation.price,
                    },
                )
            state.pop("entry_target_room_retest", None)
        entry_target_room_selection: dict[str, Any] = {}
        entry_target_room_reference = float(observation.price)
        entry_target_room_targets = profit_targets
        if minimum_entry_target_gap_bps > 0:
            previous_price = (unified_trigger or {}).get("previous_price")
            try:
                previous_price_value = float(previous_price)
            except (TypeError, ValueError):
                previous_price_value = 0.0
            if previous_price_value > 0:
                # Assess room from the last causal price before this market
                # event. Otherwise crossing a nearby level can instantly
                # remove it from the ordered ladder and make the third target
                # jump to a distant level, admitting the same campaign that
                # failed the room gate one event earlier.
                entry_target_room_reference = (
                    min(previous_price_value, float(observation.price))
                    if side == "long"
                    else max(previous_price_value, float(observation.price))
                )
                entry_target_room_targets = _structural_profit_targets(
                    replace(observation, price=entry_target_room_reference),
                    parameters,
                    stop=stop,
                    side=side,
                    luld_target=luld_target,
                    selection_evidence=entry_target_room_selection,
                )
        selected_target = (
            entry_target_room_targets[0] if entry_target_room_targets else None
        )
        structural_target_gap_bps = (
            (
                (float(selected_target) - entry_target_room_reference)
                / entry_target_room_reference
                * 10_000.0
            )
            * (1.0 if side == "long" else -1.0)
            if selected_target is not None and entry_target_room_reference > 0
            else None
        )
        if minimum_entry_target_gap_bps > 0 and (
            structural_target_gap_bps is None
            or structural_target_gap_bps < minimum_entry_target_gap_bps
        ):
            qualified_room_levels = list(
                entry_target_room_selection.get("qualified_levels") or ()
            )
            first_room_level = (
                _level_metric(dict(qualified_room_levels[0]), "target_price", "price")
                if qualified_room_levels
                else 0.0
            )
            crossed_room_boundary = bool(
                first_room_level > 0
                and (
                    entry_target_room_reference <= first_room_level < observation.price
                    if side == "long"
                    else entry_target_room_reference >= first_room_level > observation.price
                )
            )
            if crossed_room_boundary:
                state["entry_target_room_retest"] = {
                    "boundary": first_room_level,
                    "blocked_at": observation.observed_at.isoformat(),
                    "side": side,
                    "selected_target": selected_target,
                    "target_gap_bps": structural_target_gap_bps,
                    "minimum_target_gap_bps": minimum_entry_target_gap_bps,
                }
            return self._result(
                assignment,
                observation,
                "wait",
                "insufficient_structural_target_room",
                confirmation_score,
                _confirmation_confidence(observation),
                state,
                AssignmentStatus.REENTRY_COOLDOWN
                if reentries
                else AssignmentStatus.WATCHING,
                metadata={
                    "triggers": triggered,
                    "confirmation": confirmation,
                    "entry_rules": rule_result,
                    "unified_structural_trigger": unified_trigger,
                    "profit_target_selection": profit_target_selection,
                    "entry_target_room_selection": entry_target_room_selection,
                    "entry_target_room_reference": entry_target_room_reference,
                    "selected_structural_target": selected_target,
                    "structural_target_gap_bps": structural_target_gap_bps,
                    "minimum_entry_target_gap_bps": minimum_entry_target_gap_bps,
                },
            )
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
                "entry_acquisition_exit_latched": False,
                "liquidation_origin_fill_role": "",
                "liquidation_origin_reentry_after_fill": False,
                "last_exit_reason": "",
                "trailing_support_selection": None,
                "pending_profit_target_advance": None,
                "previous_target_close": None,
                "last_target_candle_at": "",
                "initial_stop": stop,
                "active_stop": stop,
                "trailing_amount": trailing_amount,
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
                "structural_profit_target_frontier": _target_frontier_from_selection(
                    profit_target_selection
                ),
                "last_entry_resistance": _compact_structural_level_reference(
                    (unified_trigger or {}).get("level")
                ),
                "position_entry_level_ids": entry_level_ids,
                "position_entry_tranches": min(
                    entry_tranche_count,
                    len(entry_level_ids),
                ) if entry_level_ids else 1,
            }
        )
        if self.revision >= 42:
            state["target_resistance_snapshot"] = [
                _compact_structural_level_reference(row)
                for row in observation.structural_resistance_levels
            ]
        state.pop("entry_target_room_retest", None)
        state.pop("accepted_entry_resistance", None)
        state.pop("pending_entry_resistance", None)
        for field_name in (
            "reentry_pullback_peak_price",
            "reentry_pullback_low_price",
            "reentry_pullback_confirmed_at",
        ):
            state.pop(field_name, None)
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
            trailing_amount=trailing_amount,
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
                "protective_stop_selection": protective_stop_selection,
                "trailing_amount": trailing_amount,
                "profit_targets": profit_targets,
                "profit_target_selection": profit_target_selection,
                "entry_target_room_selection": entry_target_room_selection,
                "entry_target_room_reference": entry_target_room_reference,
                "structural_target_gap_bps": structural_target_gap_bps,
                "minimum_entry_target_gap_bps": minimum_entry_target_gap_bps,
                "unified_structural_trigger": unified_trigger,
                "execution_quality": execution_detail,
                "liquidity_admission": dict(
                    state.get("liquidity_admission_evidence") or {}
                ),
                "entry_momentum_confirmation": momentum_detail,
                **(
                    {
                        "completed_candle": {
                            "timeframe": entry_timeframe,
                            "side": side,
                            "open": observation.bar_open,
                            "close": observation.price,
                            "required": (
                                "close >= open"
                                if side == "long"
                                else "close <= open"
                            ),
                            "passed": True,
                        }
                    }
                    if bool(candle_policy.get("enabled", True))
                    else {
                        "entry_evaluation": {
                            "mode": "event_native",
                            "candle_confirmation_required": False,
                        }
                    }
                ),
                "macd": {
                    **entry_macd_evidence,
                    "open_gap_bps": macd_gap_bps,
                    "minimum_open_gap_bps": minimum_macd_gap_bps,
                },
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
        previous_stop = float(state.get("active_stop") or state.get("initial_stop") or 0)
        previous_support_selection = state.get("trailing_support_selection")
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
        target_liquidation_required = bool(
            state.pop("profit_target_liquidation_required", False)
        )
        if target_liquidation_required:
            incomplete_target_policy = dict(
                dict(parameters.get("protection") or {})
                .get("profit_ladder", {})
                .get("incomplete_target_exit", {})
                or {}
            )
            outside_regular_hours = _outside_regular_hours(observation.observed_at)
            exit_route = {
                "route_id": "profit-target-incomplete",
                "name": "Profit target touched but not fully filled",
                "mechanism": "profit_target_incomplete",
                "position_fraction": 1.0,
                "priority": 110,
                "evidence": dict(state.get("last_profit_target_fill") or {}),
                "order_intent": {
                    "execution_policy": str(
                        incomplete_target_policy.get(
                            "extended_hours_execution_policy"
                            if outside_regular_hours
                            else "regular_hours_execution_policy"
                        )
                        or (
                            ExecutionPolicyName.ADAPTIVE_URGENT
                            if outside_regular_hours
                            else ExecutionPolicyName.ADAPTIVE_VERY_URGENT
                        )
                    ),
                    "partial_fill_policy": str(
                        incomplete_target_policy.get("partial_fill_policy")
                        or PartialFillPolicy.COMPLETE_REMAINDER
                    ),
                    "deadline_ms": int(
                        incomplete_target_policy.get("deadline_ms") or 5_000
                    ),
                },
            }
        elif _at_or_after_session_time(observation.observed_at, flatten_time):
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
                if self.revision >= 37:
                    state["entry_acquisition_exit_latched"] = True
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
                    "cancel_entry_acquisition": self.revision >= 37 and not partial_reduction,
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

        if self.revision >= 37 and observation.position_quantity <= 0:
            return self._result(
                assignment, observation, "wait", "entry_fill_pending", 0.0, 1.0,
                state, AssignmentStatus.ENTRY_PENDING,
            )

        stop_replacement = None
        if (self.revision >= 37 and stop > previous_stop > 0
                and parameters["protection"]["trailing"].get("mode") == "qualified_support"):
            stop_replacement = self._result(
                assignment, observation, "replace_protective_stop", "qualified_support_advanced",
                observation.qmd_score, 1.0, state, AssignmentStatus.MANAGING,
                quantity=observation.position_quantity, invalidation_price=stop,
                metadata={"previous_stop": previous_stop,
                          "previous_support_selection": previous_support_selection,
                          "protective_stop_selection": state.get("trailing_support_selection", {})},
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

        structural_add = self._structural_entry_tranche_add_result(
            assignment,
            observation,
            parameters,
            state,
            side=side,
        )
        if structural_add is not None:
            return structural_add

        target_replacement = self._structural_target_replacement_result(
            assignment,
            observation,
            parameters,
            state,
            side=side,
            stop=stop,
        )
        if target_replacement is not None:
            if stop_replacement is not None:
                return replace(target_replacement, evaluation=StrategyEvaluation(
                    signals=stop_replacement.evaluation.signals + target_replacement.evaluation.signals,
                    intents=stop_replacement.evaluation.intents + target_replacement.evaluation.intents,
                ))
            return target_replacement
        if stop_replacement is not None:
            return stop_replacement

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
        for add_step in ([] if self.revision >= 37 else add_steps):
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

    def _structural_entry_tranche_add_result(
        self,
        assignment: StrategyAssignment,
        observation: StrategyObservation,
        parameters: dict[str, Any],
        state: dict[str, Any],
        *,
        side: str,
    ) -> StrategyEngineResult | None:
        if self.revision >= 37:
            return None
        policy = dict(parameters.get("structural_entry") or {})
        if (
            not bool(policy.get("enabled", False))
            or str(policy.get("selection_mode") or "").lower()
            not in _TOP_N_SESSION_HIGH_ENTRY_MODES
        ):
            return None
        tranche_count = max(
            1,
            int(policy.get("entry_tranche_count") or policy.get("maximum_entry_levels") or 1),
        )
        if tranche_count <= 1 or not assignment.permissions.add:
            return None
        trigger = state.get("latest_structural_entry_trigger")
        if not isinstance(trigger, Mapping) or not bool(trigger.get("passed")):
            return None
        consumed = list(dict.fromkeys(
            str(value)
            for value in state.get("position_entry_level_ids") or ()
            if str(value)
        ))
        used_tranches = max(
            int(state.get("position_entry_tranches") or len(consumed) or 1),
            len(consumed),
        )
        remaining_tranches = max(0, tranche_count - used_tranches)
        crossed_ids = _structural_entry_level_ids(trigger)
        new_ids = [value for value in crossed_ids if value not in consumed][
            :remaining_tranches
        ]
        if not new_ids:
            return None

        liquidity_policy = dict(parameters.get("liquidity_admission") or {})
        execution_ready, execution_detail = _current_execution_quality_result(
            observation,
            liquidity_policy,
            reentry=True,
        )
        phase_policy = dict(parameters.get("phase_policy") or {})
        reentry_phase = dict(phase_policy.get("reentry") or {})
        phase_rules = dict(reentry_phase.get("rules") or parameters.get("entry_rules") or {})
        if state.get("liquidity_admitted_at"):
            phase_rules = {
                **phase_rules,
                "confirmation": entry_stage_without_rule_set(
                    dict(phase_rules.get("confirmation") or {}),
                    "strategy-squeeze-volume-spread-quality",
                ),
            }
        rule_result = evaluate_entry_decision_rules(phase_rules, observation)
        rule_result = _entry_rule_result_with_unified_trigger(
            rule_result,
            trigger,
            observation,
        )
        macd_open, macd_evidence = _exact_positive_open_macd(
            observation,
            "1s",
            require_positive_signal=self.revision == 26,
        )
        candle_policy = dict(parameters.get("entry_candle_confirmation") or {})
        bar_open = observation.bar_open
        candle_passed = bool(
            bar_open is not None
            and bar_open > 0
            and (
                observation.price >= bar_open
                if side == "long"
                else observation.price <= bar_open
            )
        )
        minimum_macd_gap_bps = max(
            0.0,
            float(candle_policy.get("minimum_macd_open_gap_bps") or 0.0),
        )
        macd_gap_bps = (
            max(
                0.0,
                float(observation.macd_line or 0)
                - float(observation.macd_signal or 0),
            )
            / observation.price
            * 10_000.0
        )
        passed = bool(
            state.get("liquidity_admitted_at")
            and execution_ready
            and rule_result["confirmation"]["passed"]
            and not rule_result["veto"]["matched_groups"]
            and macd_open
            and macd_gap_bps >= minimum_macd_gap_bps
            and (
                not bool(candle_policy.get("reject_bearish_close", True))
                or candle_passed
            )
        )
        state["last_structural_add_evaluation"] = {
            "observed_at": observation.observed_at.isoformat(),
            "crossed_level_ids": crossed_ids,
            "eligible_level_ids": new_ids,
            "passed": passed,
            "execution_quality": execution_detail,
            "entry_rules": rule_result,
            "macd": {
                **macd_evidence,
                "open_gap_bps": macd_gap_bps,
                "minimum_open_gap_bps": minimum_macd_gap_bps,
            },
            "completed_candle": {
                "open": bar_open,
                "close": observation.price,
                "passed": candle_passed,
            },
        }
        if not passed:
            return None

        protective_stop_selection: dict[str, Any] = {}
        stop = _initial_stop(
            observation,
            parameters,
            trigger.get("reference_price"),
            side=side,
            selection_evidence=protective_stop_selection,
        )
        initial_phase = dict(phase_policy.get("initial_entry") or {})
        capital_request = _phase_capital_request(
            parameters,
            "initial_entry",
            fallback_quantity=float(parameters["sizing"]["initial_quantity"]),
        )
        quantity = capital_request.value if capital_request.mode == "fixed_quantity" else 0.0
        targets = [
            float(value)
            for value in state.get("structural_profit_targets") or ()
            if isinstance(value, (int, float)) and float(value) > observation.price
        ]
        if not targets:
            targets = _structural_profit_targets(
                observation,
                parameters,
                stop=stop,
                side=side,
                luld_target=_luld_target(observation, parameters, side=side),
            )
        state["position_entry_level_ids"] = [*consumed, *new_ids]
        state["position_entry_tranches"] = used_tranches + len(new_ids)
        state["adds"] = int(state.get("adds") or 0) + len(new_ids)
        state["last_entry_resistance"] = _compact_structural_level_reference(
            trigger.get("level")
        )
        return self._result(
            assignment,
            observation,
            "add_long" if side == "long" else "add_short",
            "structural_entry_tranche_confirmed",
            float(rule_result["confirmation"]["score"]),
            _confirmation_confidence(observation),
            state,
            AssignmentStatus.MANAGING,
            quantity=quantity,
            invalidation_price=stop,
            profit_target_price=targets[0] if targets else None,
            trailing_amount=_trailing_amount(observation, parameters, stop=stop),
            capital_request=capital_request,
            order_intent=dict(initial_phase.get("order_intent") or {}),
            metadata={
                "execution_role": "structural_entry_tranche",
                "crossed_level_ids": crossed_ids,
                "entry_level_ids": new_ids,
                "entry_tranche_count": tranche_count,
                "position_entry_tranches": state["position_entry_tranches"],
                "unified_structural_trigger": dict(trigger),
                "entry_rules": rule_result,
                "execution_quality": execution_detail,
                "macd": macd_evidence,
                "protective_stop_selection": protective_stop_selection,
                "profit_targets": targets,
            },
        )

    def _regular_reentry_pullback_result(
        self,
        assignment: StrategyAssignment,
        observation: StrategyObservation,
        parameters: dict[str, Any],
        state: dict[str, Any],
    ) -> StrategyEngineResult | None:
        policy = dict(
            dict(parameters.get("reentry") or {}).get("pullback_reclaim") or {}
        )
        if not bool(policy.get("enabled", False)):
            return None
        peak = max(
            float(state.get("reentry_pullback_peak_price") or 0),
            observation.price,
        )
        low = min(
            float(state.get("reentry_pullback_low_price") or observation.price),
            observation.price,
        )
        state["reentry_pullback_peak_price"] = peak
        state["reentry_pullback_low_price"] = low
        pullback_required = max(
            observation.volatility
            * float(policy.get("minimum_pullback_atr_multiple") or 0),
            peak * float(policy.get("minimum_pullback_bps") or 0) / 10_000.0,
        )
        pullback = peak - low
        evidence = {
            "peak_price": peak,
            "low_price": low,
            "last_price": observation.price,
            "pullback": pullback,
            "pullback_required": pullback_required,
        }
        if not state.get("reentry_pullback_confirmed_at"):
            if pullback_required <= 0 or pullback < pullback_required:
                return self._result(
                    assignment,
                    observation,
                    "wait",
                    "waiting_for_reentry_pullback",
                    0.0,
                    _confirmation_confidence(observation),
                    state,
                    AssignmentStatus.REENTRY_COOLDOWN,
                    metadata={"reentry_pullback": evidence},
                )
            state["reentry_pullback_confirmed_at"] = observation.observed_at.isoformat()
            # A structural acceptance observed before the pullback cannot
            # authorize the next campaign. Re-arm from the pullback low so the
            # next order requires a causal post-pullback reclaim.
            state.pop("accepted_entry_resistance", None)
            state.pop("pending_entry_resistance", None)
            return self._result(
                assignment,
                observation,
                "wait",
                "waiting_for_reentry_reclaim",
                0.0,
                _confirmation_confidence(observation),
                state,
                AssignmentStatus.REENTRY_COOLDOWN,
                metadata={"reentry_pullback": evidence},
            )
        return None

    def _moving_target_result(
        self, assignment: StrategyAssignment, observation: StrategyObservation,
        parameters: dict[str, Any], state: dict[str, Any], *, side: str, stop: float,
    ) -> StrategyEngineResult | None:
        """Consume a causal first-resistance hit, using the moving producer book."""
        if self.revision >= 42 and side == "long":
            return self._closed_dynamic_target_result(assignment, observation, parameters, state, stop=stop)
        candle_clock = self.revision >= 38
        if candle_clock and (observation.source_timeframe != "1s" or "bar_close" not in observation.evaluation_events):
            return None
        if not candle_clock and "market_data_update" not in observation.evaluation_events:
            return None
        previous = state.get("previous_target_close") if candle_clock else state.get("previous_observed_price")
        previous_frontier = list(state.get("structural_profit_target_frontier") or ())
        retry = state.get("pending_profit_target_advance")
        if candle_clock and state.get("last_target_candle_at", "") >= observation.observed_at.isoformat():
            return None
        selection: dict[str, Any] = {}
        targets = _structural_profit_targets(
            observation, parameters, stop=stop, side=side,
            luld_target=_luld_target(observation, parameters, side=side),
            selection_evidence=selection,
        )
        current_frontier = _target_frontier_from_selection(selection)
        # Keep the tracked ladder until its first resistance is passed. A
        # candle that merely changes the nearby book cannot consume that R1.
        if self.revision < 39:
            state["structural_profit_target_frontier"] = current_frontier
        if candle_clock:
            closed_at = observation.observed_at.isoformat()
            state["last_target_candle_at"] = closed_at
            state["previous_target_close"] = observation.price
        if (previous is None and not candle_clock) or (not previous_frontier and not retry):
            return None
        first = dict(previous_frontier[0]) if previous_frontier else {}
        identity = str(first.get("unified_level_id") or "")
        # Follow updated producer prices for a known level. Preserve the prior
        # resistance when this very event flips it to support.
        found = False
        for row in (*observation.structural_resistance_levels, *observation.structural_support_levels):
            if identity and str(row.get("unified_level_id") or "") == identity:
                if not retry and not _level_passes_configured_quality(row, parameters["protection"]["profit_ladder"]):
                    if self.revision < 41:
                        return None
                    continue
                first = {**first, **_compact_structural_level_reference(row)}
                found = True
                break
        if not found and not retry:
            if self.revision < 41:
                return None
            # Producer levels can merge or retire. Reconcile the unconsumed
            # ladder against today's book rather than waiting forever for a
            # vanished identity. This is a current producer level, never a
            # synthetic boundary or an unconditional target advance.
            anchor = _level_metric(first, "price", "target_price")
            reconciled: dict[str, Any] = {}
            _structural_profit_targets(
                replace(observation, price=anchor), parameters, stop=stop,
                side=side, luld_target=_luld_target(observation, parameters, side=side),
                selection_evidence=reconciled,
            )
            surviving = _target_frontier_from_selection(reconciled)
            if not surviving:
                return None
            first = dict(surviving[0])
            state["structural_profit_target_frontier"] = surviving
            first["reconciled_from_missing_level_id"] = identity
        boundary = _level_metric(first, "price", "target_price")
        crossed = ((observation.price > boundary if side == "long" else observation.price < boundary)
                   if candle_clock else
                   (float(previous) < boundary <= observation.price if side == "long"
                    else float(previous) > boundary >= observation.price))
        crossed = crossed or bool(retry)
        existing = list(state.get("structural_profit_targets") or ())
        if not crossed or not existing:
            return None
        acceptance = retry or {"passed": True, "reason": "first_resistance_close" if candle_clock else "first_resistance_hit",
                               "level": first, "previous_price": previous, "price": observation.price,
                               "observed_at": observation.observed_at.isoformat()}
        state["pending_profit_target_advance"] = acceptance
        if not targets:
            return None
        candidate = targets[0]
        if not (candidate > existing[0] if side == "long" else candidate < existing[0]):
            return None
        state["structural_profit_targets"] = [candidate]
        state["structural_profit_target_frontier"] = current_frontier
        state.pop("pending_profit_target_advance", None)
        state["last_profit_target_replaced_at"] = observation.observed_at.isoformat()
        return self._result(
            assignment, observation, "replace_profit_target", "structural_profit_target_advanced",
            observation.qmd_score, 1.0, state, AssignmentStatus.MANAGING,
            quantity=observation.position_quantity, profit_target_price=candidate,
            metadata={"previous_profit_target": existing[0], "profit_target": candidate,
                      "previous_profit_target_frontier": previous_frontier,
                      "profit_target_selection": selection, "ratchet_clock": "completed_1s_bar" if candle_clock else "resistance_hit",
                      "ratchet_acceptance": acceptance},
        )

    def _closed_dynamic_target_result(
        self, assignment: StrategyAssignment, observation: StrategyObservation,
        parameters: dict[str, Any], state: dict[str, Any], *, stop: float,
    ) -> StrategyEngineResult | None:
        if observation.source_timeframe != "1s" or "bar_close" not in observation.evaluation_events:
            return None
        closed_at = observation.observed_at.isoformat()
        if state.get("last_target_candle_at", "") >= closed_at:
            return None
        previous = state.get("previous_target_close", observation.previous_close)
        if previous is None:
            previous = observation.previous_close
        if previous is None:
            previous = state.get("entry_reference_price", state.get("previous_observed_price"))
        prior_frontier = list(state.get("structural_profit_target_frontier") or ())
        prior_rows = state.get("target_resistance_snapshot", prior_frontier)
        prior_ids = {str(row.get("unified_level_id")) for row in prior_rows}
        pending = state.get("pending_profit_target_advance")
        pending_id = str(dict((pending or {}).get("level") or {}).get("unified_level_id") or "")
        if pending_id:
            prior_ids.add(pending_id)
        now_ms = observation.observed_at.timestamp() * 1000

        def causal(row: Mapping[str, Any]) -> bool:
            try:
                return all(isfinite(float(row[key])) and float(row[key]) <= now_ms
                           for key in ("created_at_ms", "confirmed_at_ms", "updated_at_ms")
                           if row.get(key) is not None)
            except (TypeError, ValueError):
                return False

        resistances = tuple(row for row in observation.structural_resistance_levels if causal(row))
        supports = tuple(row for row in observation.structural_support_levels if causal(row))
        # Retain only identities for recognizing a resistance that this candle
        # flipped to support. Prices and quality always come from the current book.
        state["target_resistance_snapshot"] = [_compact_structural_level_reference(row) for row in resistances]
        state["last_target_candle_at"] = closed_at
        state["previous_target_close"] = observation.price
        state.pop("pending_profit_target_advance", None)
        if (observation.bar_open is None or not isfinite(observation.bar_open)
                or not isfinite(observation.price) or observation.price < observation.bar_open
                or previous is None or not isfinite(float(previous)) or float(previous) <= 0):
            return None
        current = replace(observation, structural_resistance_levels=resistances,
                          structural_support_levels=supports)
        crossing_rows = (*resistances, *(row for row in supports
            if str(row.get("unified_level_id")) in prior_ids))
        # A deferred command may retry only if its level still exists, qualifies
        # now, and is below this eligible close. Never reuse its old acceptance.
        floor_price = float(previous)
        for row in crossing_rows:
            if pending_id and str(row.get("unified_level_id")) == pending_id:
                floor_price = min(floor_price, _level_metric(row, "price"))
        qualified: list[dict[str, Any]] = []
        _structural_profit_targets(
            replace(current, price=nextafter(floor_price, -inf),
                    structural_resistance_levels=crossing_rows, structural_support_levels=()),
            parameters, stop=stop, side="long", luld_target=None,
            qualified_levels_out=qualified,
        )
        crossed = [row for row in qualified
                   if observation.price > _level_metric(row, "price")
                   and (float(previous) <= _level_metric(row, "price")
                        or (pending_id and str(row.get("unified_level_id")) == pending_id))]
        if not crossed or not state.get("structural_profit_targets"):
            return None
        highest = max(crossed, key=lambda row: _level_metric(row, "price"))
        boundary = _level_metric(highest, "price")
        selection: dict[str, Any] = {}
        targets = _structural_profit_targets(
            replace(current, price=boundary), parameters, stop=stop, side="long",
            luld_target=_luld_target(current, parameters, side="long"), selection_evidence=selection,
        )
        acceptance = {"passed": True, "reason": "highest_resistance_non_red_close",
                      "level": highest, "crossed_levels": crossed,
                      "previous_price": previous, "price": observation.price,
                      "bar_open": observation.bar_open, "observed_at": closed_at}
        existing = state["structural_profit_targets"][0]
        if not targets or targets[0] <= existing:
            state["pending_profit_target_advance"] = acceptance
            return None
        state["structural_profit_targets"] = targets
        state["structural_profit_target_frontier"] = _target_frontier_from_selection(selection)
        state["last_profit_target_replaced_at"] = closed_at
        return self._result(
            assignment, observation, "replace_profit_target", "structural_profit_target_advanced",
            observation.qmd_score, 1.0, state, AssignmentStatus.MANAGING,
            quantity=observation.position_quantity, profit_target_price=targets[0],
            metadata={"previous_profit_target": existing, "profit_target": targets[0],
                      "previous_profit_target_frontier": prior_frontier,
                      "profit_target_selection": selection, "ratchet_clock": "completed_1s_bar",
                      "ratchet_acceptance": acceptance},
        )

    def _structural_target_replacement_result(
        self,
        assignment: StrategyAssignment,
        observation: StrategyObservation,
        parameters: dict[str, Any],
        state: dict[str, Any],
        *,
        side: str,
        stop: float,
    ) -> StrategyEngineResult | None:
        if self.revision >= 37:
            return self._moving_target_result(assignment, observation, parameters, state, side=side, stop=stop)
        policy = dict(parameters["protection"].get("profit_ladder") or {})
        if (
            not bool(policy.get("enabled", True))
            or str(policy.get("selection_mode") or "")
            not in {"ordinal_qualified_level", "second_next_level"}
            or observation.source_timeframe != "1s"
            or "bar_close" not in observation.evaluation_events
        ):
            return None
        macd_open, macd_evidence = _exact_positive_open_macd(
            observation,
            "1s",
            require_positive_signal=self.revision == 26,
        )
        if not macd_open:
            return None
        existing = [
            float(value)
            for value in state.get("structural_profit_targets") or ()
            if isinstance(value, (int, float)) and float(value) > 0
        ]
        if not existing:
            return None
        current_target = existing[0]
        prior_frontier = [
            dict(row)
            for row in state.get("structural_profit_target_frontier") or ()
            if isinstance(row, Mapping)
        ]
        acceptance = _target_ratchet_acceptance(
            prior_frontier,
            close=observation.price,
            side=side,
            buffer_bps=max(
                0.0,
                float(policy.get("ratchet_acceptance_buffer_bps") or 0.0),
            ),
        )
        if not acceptance["passed"]:
            return None
        profit_target_selection: dict[str, Any] = {}
        candidate_targets = _structural_profit_targets(
            observation,
            parameters,
            stop=stop,
            side=side,
            luld_target=_luld_target(observation, parameters, side=side),
            selection_evidence=profit_target_selection,
        )
        if not candidate_targets:
            return None
        candidate = candidate_targets[0]
        advances = (
            candidate > current_target + 1e-9
            if side == "long"
            else candidate < current_target - 1e-9
        )
        if not advances:
            return None
        state["structural_profit_targets"] = [candidate]
        state["structural_profit_target_frontier"] = (
            _target_frontier_from_selection(profit_target_selection)
        )
        state["last_profit_target_replaced_at"] = observation.observed_at.isoformat()
        return self._result(
            assignment,
            observation,
            "replace_profit_target",
            "structural_profit_target_advanced",
            observation.qmd_score,
            _confirmation_confidence(observation),
            state,
            AssignmentStatus.MANAGING,
            quantity=observation.position_quantity,
            profit_target_price=candidate,
            metadata={
                "previous_profit_target": current_target,
                "previous_profit_target_frontier": prior_frontier,
                "profit_target": candidate,
                "profit_target_selection": profit_target_selection,
                "ratchet_acceptance": acceptance,
                "macd": macd_evidence,
                "ratchet_clock": "completed_1s_bar",
            },
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
        macd_open, macd_evidence = _exact_positive_open_macd(
            observation,
            "1s",
            require_positive_signal=self.revision == 26,
        )
        vwap = _numeric_source_value(
            observation, "indicator.vwap.execution_value", "1s"
        )
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
        pending_capital = dict(state.get("pending_capital_request") or {})
        capital_retry = self.revision >= 41 and bool(pending_capital) and action in {"enter_long", "enter_short"}
        if capital_retry:
            resolved_metadata.update(capital_request_id=pending_capital["request_id"],
                capital_requested_at=pending_capital["requested_at"], capital_revalidation=True)
        if action in {
            "enter_long",
            "enter_short",
            "add_long",
            "add_short",
            "reduce_long",
            "reduce_short",
            "take_profit",
            "exit",
            "cover",
            "replace_profit_target",
            "replace_protective_stop",
        }:
            resolved_metadata.setdefault(
                "structural_level_snapshot",
                _decision_structural_level_snapshot(
                    observation,
                    assignment.parameters,
                ),
            )
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
            action="hold" if capital_retry else action,  # type: ignore[arg-type]
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
        if action in {"enter_long", "add_long", "reduce_long", "take_profit", "exit", "enter_short", "add_short", "reduce_short", "cover", "replace_profit_target", "replace_protective_stop"}:
            resolved_order_intent = dict(order_intent or {})
            if self.revision >= 39 and action in {"enter_long", "enter_short", "exit", "cover"}:
                resolved_order_intent.update(
                    execution_policy="adaptive_urgent", persist_until_cancelled=True,
                    partial_fill_policy="complete_remainder",
                )
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
                    intent_id=pending_capital["request_id"] if capital_retry else event_id,
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
                        **({"wait_for_capital": True} if self.revision >= 41
                           and action in {"enter_long", "enter_short"} else {}),
                        **({"entry_completion_quote": "ask" if self.revision >= 39 else "bid"} if self.revision >= 37
                           and action in {"enter_long", "add_long"} else {}),
                        "bid": observation.bid,
                        "ask": observation.ask,
                        "quote_observed_at": (
                            dict(observation.source_values.get("market.spread_bps") or {}).get("observed_at")
                            or observation.observed_at.isoformat()
                        ),
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
        minimum_quantity=float(payload.get("minimum_quantity") or 0.0),
        maximum_quantity=(
            float(payload["maximum_quantity"])
            if payload.get("maximum_quantity") is not None
            else None
        ),
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


def _structural_entry_level_ids(
    unified_trigger: Mapping[str, Any] | None,
) -> list[str]:
    if not isinstance(unified_trigger, Mapping):
        return []
    crossed = [
        str(value)
        for value in unified_trigger.get("crossed_level_ids") or ()
        if str(value)
    ]
    if crossed:
        return list(dict.fromkeys(crossed))
    level = unified_trigger.get("level")
    if isinstance(level, Mapping):
        level_id = str(level.get("unified_level_id") or "")
        if level_id:
            return [level_id]
    return []


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
        if payload.get("persist_until_cancelled") is not None:
            envelope["persist_until_cancelled"] = bool(
                payload["persist_until_cancelled"]
            )
        if buying and name == ExecutionPolicyName.IMMEDIATE_WITH_LIMIT and envelope.get("maximum_buy_price") is None:
            envelope["maximum_buy_price"] = observation.price
        if not buying and name == ExecutionPolicyName.IMMEDIATE_WITH_LIMIT and envelope.get("minimum_sell_price") is None:
            envelope["minimum_sell_price"] = observation.price
        configured["envelope"] = envelope
        if payload.get("partial_fill_policy"):
            configured["partial_fill_policy"] = str(payload["partial_fill_policy"])
        policy = execution_policy_from_payload(configured)
        if policy.name == ExecutionPolicyName.ADAPTIVE_VERY_URGENT:
            discretion_ticks = max(
                0,
                int(
                    configured.get(
                        "maximum_price_discretion_ticks",
                        DEFAULT_VERY_URGENT_PRICE_DISCRETION_TICKS,
                    )
                ),
            )
            tick_size = float(
                dict(parameters.get("execution") or {}).get("tick_size") or 0.01
            )
            if (
                buying
                and policy.envelope.maximum_buy_price is None
                and not policy.envelope.persist_until_cancelled
            ):
                touch = max(float(observation.price), float(observation.ask or 0))
                policy = replace(
                    policy,
                    envelope=replace(
                        policy.envelope,
                        maximum_buy_price=touch + tick_size * discretion_ticks,
                    ),
                )
            elif not buying and policy.envelope.minimum_sell_price is None and not policy.envelope.persist_until_cancelled:
                bid = float(observation.bid or 0)
                touch = min(float(observation.price), bid) if bid > 0 else float(observation.price)
                policy = replace(
                    policy,
                    envelope=replace(
                        policy.envelope,
                        minimum_sell_price=max(tick_size, touch - tick_size * discretion_ticks),
                    ),
                )
        return policy
    return ExecutionPolicy(
        policy_id=f"strategy-{name.value}",
        name=name,
        envelope=ExecutionEnvelope(
            maximum_buy_price=observation.price if buying and name == ExecutionPolicyName.IMMEDIATE_WITH_LIMIT else None,
            minimum_sell_price=observation.price if not buying and name == ExecutionPolicyName.IMMEDIATE_WITH_LIMIT else None,
            deadline_ms=int(payload.get("deadline_ms") or 750),
            persist_until_cancelled=bool(
                payload.get("persist_until_cancelled", False)
            ),
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
        if parameters["protection"]["trailing"].get("mode") == "qualified_support":
            trailing_rule = TrailingRuleType.NONE
            trailing_raw = {}
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
    automatic = True

    def __init__(
        self,
        assignments: list[StrategyAssignment],
        *,
        revision: int = STRATEGY_REVISION,
    ) -> None:
        if revision not in {*HISTORICAL_STRATEGY_REVISIONS, STRATEGY_REVISION}:
            raise ValueError(f"Unsupported Long Momentum Strategy revision: {revision}")
        if any(assignment.strategy_revision != revision for assignment in assignments):
            raise ValueError(
                "Strategy assignments do not match Long Momentum executor revision"
            )
        self.revision = revision
        self._campaigns = StrategyCampaignOrchestrator(assignments)
        self._assignments = {
            (assignment.account_id, assignment.ticker.upper()): assignment
            for assignment in assignments
        }
        if len(self._assignments) != len(assignments):
            raise ValueError(
                "A Strategy Campaign may have only one active account leg per ticker and account"
            )
        self._engine = LongMomentumStrategyEngine(revision=revision)

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
            if str(intent.action) in {"enter_long", "enter_short"}:
                state.pop("pending_capital_request", None)
            if str(intent.action) == "replace_protective_stop":
                state["active_stop"] = float(intent.metadata["previous_stop"])
                state["trailing_support_selection"] = intent.metadata.get("previous_support_selection")
                self._assignments[key] = replace(assignment, state=state, updated_at=event_time)
                return
            if str(intent.action) == "replace_profit_target":
                if assignment.strategy_revision >= 37:
                    state["pending_profit_target_advance"] = intent.metadata.get("ratchet_acceptance")
                previous = float(intent.metadata.get("previous_profit_target") or 0)
                if previous > 0:
                    state["structural_profit_targets"] = [previous]
                previous_frontier = intent.metadata.get(
                    "previous_profit_target_frontier"
                )
                if isinstance(previous_frontier, (list, tuple)):
                    state["structural_profit_target_frontier"] = [
                        dict(row)
                        for row in previous_frontier
                        if isinstance(row, Mapping)
                    ]
                state["last_intent_rejection"] = {
                    "intent_id": intent.intent_id,
                    "reasons": list(reasons),
                    "rejected_at": event_time.isoformat(),
                    "execution_role": "profit_target_replacement",
                }
                updated = replace(
                    assignment,
                    status=AssignmentStatus.MANAGING,
                    state=state,
                    updated_at=event_time,
                )
                self._assignments[key] = updated
                self._campaigns.register(updated)
                return
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
            if str(intent.action) not in {"enter_long", "enter_short"}:
                # A rejected add/reduction/exit does not revoke the position
                # that already exists or its working protection contract.
                updated = replace(assignment, state=state, updated_at=event_time)
                self._assignments[key] = updated
                self._campaigns.register(updated)
                return
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
                "structural_profit_target_frontier",
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

    async def on_intent_deferred(self, intent: StrategyIntent, *, reasons: tuple[str, ...],
                                 event_time: datetime) -> None:
        for key, assignment in self._assignments.items():
            if assignment.assignment_id != str(intent.metadata.get("assignment_id") or ""):
                continue
            state = dict(assignment.state)
            previous = dict(state.get("pending_capital_request") or {})
            state["pending_capital_request"] = previous or {
                "request_id": intent.intent_id, "requested_at": intent.event_time.isoformat(),
                "trigger": dict(intent.metadata.get("unified_structural_trigger") or {}),
            }
            state["entries"] = max(0, int(state.get("entries") or 0) - 1)
            state["pending_capital_reasons"] = list(reasons)
            updated = replace(assignment, state=state, status=AssignmentStatus.WATCHING, updated_at=event_time)
            self._assignments[key] = updated
            self._campaigns.register(updated)
            return

    def on_capital_request_funded(self, intent: StrategyIntent) -> None:
        for key, assignment in self._assignments.items():
            if assignment.assignment_id == str(intent.metadata.get("assignment_id") or ""):
                state = dict(assignment.state)
                state.pop("pending_capital_request", None)
                state.pop("pending_capital_reasons", None)
                updated = replace(assignment, state=state)
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
                        "structural_profit_target_frontier",
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
                if assignment.strategy_revision >= 37 and state.get("entry_acquisition_exit_latched"):
                    status = (AssignmentStatus.EXIT_PENDING if abs(float(aggregate_position_quantity or 0)) > 1e-9
                              else AssignmentStatus.COMPLETED)
                    if (assignment.strategy_revision >= 41 and status == AssignmentStatus.COMPLETED
                            and not state.get("disable_after_exit") and assignment.permissions.reenter
                            and bool(resolve_long_momentum_parameters(assignment.parameters)["reentry"].get("enabled"))):
                        status = AssignmentStatus.WATCHING
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
                if assignment.strategy_revision >= 37 and state.get("entry_acquisition_exit_latched"):
                    status = AssignmentStatus.EXIT_PENDING
            elif action in {"reduce_long", "reduce_short"}:
                status = AssignmentStatus.MANAGING
            elif action in {"exit", "take_profit", "cover"}:
                fill_role = str(getattr(snapshot, "fill_role", "") or "")
                if (assignment.strategy_revision >= 40 and incremental_fill > 0
                        and not state.get("liquidation_origin_fill_role")):
                    # The first sell owns the liquidation cause. A managed
                    # remainder must not erase a partially filled stop/target.
                    state["liquidation_origin_fill_role"] = fill_role or "managed_exit"
                    state["liquidation_origin_reentry_after_fill"] = bool(
                        getattr(snapshot, "reentry_after_fill", False)
                    )
                    if not state.get("last_exit_reason"):
                        state["last_exit_reason"] = fill_role or "managed_exit"
                if assignment.strategy_revision >= 37:
                    state["entry_acquisition_exit_latched"] = True
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
                        state["profit_target_liquidation_required"] = True
                        state["target_replenishment_quantity"] = 0.0
                        state["target_replenishment_pending"] = False
                if (
                    aggregate_position_quantity is not None
                    and abs(float(aggregate_position_quantity)) > 1e-9
                ):
                    # A profit target is a planned partial reduction and may
                    # return the remaining campaign to management. A partial
                    # managed/protective exit still owns the entire liquidation
                    # mandate; keep the campaign EXIT_PENDING so a subsequent
                    # market frame cannot submit a duplicate full-position
                    # sell while the first exit and its fallback remain open.
                    status = (
                        AssignmentStatus.MANAGING
                        if fill_role == "profit_target"
                        else AssignmentStatus.EXIT_PENDING
                    )
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
                    state["reentry_pullback_peak_price"] = float(
                        state.get("last_price") or 0
                    )
                    state.pop("reentry_pullback_low_price", None)
                    state.pop("reentry_pullback_confirmed_at", None)
                    state.pop("accepted_entry_resistance", None)
                    state.pop("pending_entry_resistance", None)
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
    if assignment.strategy_revision >= 40 and state.get("liquidation_origin_fill_role"):
        fill_role = str(state["liquidation_origin_fill_role"])
    if fill_role in {"protective_stop", "trailing_stop", "protective_exit"}:
        return bool(reentry.get("after_protective_exit", False))
    if fill_role == "profit_target":
        return True
    return bool(
        state.get("liquidation_origin_reentry_after_fill", False)
        if assignment.strategy_revision >= 40 and state.get("liquidation_origin_fill_role")
        else getattr(snapshot, "reentry_after_fill", False)
    )


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
        operator = str(
            expression.get("operator")
            or stage.get("operator")
            or "any"
        )
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
    left_operand = _condition_operand(condition, "left", observation)
    right_operand = (
        _condition_operand(condition, "right", observation)
        if right_source
        else {
            "value": condition.get("value"),
            "observed_at": None,
            "age_ms": None,
            "maximum_age_ms": None,
            "fresh": True,
            "freshness": "constant",
        }
    )
    return {
        "condition_id": str(condition.get("condition_id") or ""),
        "left_source_id": left_source,
        "left_timeframe": left_timeframe,
        "left_value": left_operand["value"],
        "left_observed_at": left_operand["observed_at"],
        "left_age_ms": left_operand["age_ms"],
        "left_maximum_age_ms": left_operand["maximum_age_ms"],
        "left_fresh": left_operand["fresh"],
        "left_freshness": left_operand["freshness"],
        "comparator": str(condition.get("comparator") or ""),
        "right_source_id": right_source,
        "right_timeframe": right_timeframe,
        "right_value": right_operand["value"],
        "right_observed_at": right_operand["observed_at"],
        "right_age_ms": right_operand["age_ms"],
        "right_maximum_age_ms": right_operand["maximum_age_ms"],
        "right_fresh": right_operand["fresh"],
        "right_freshness": right_operand["freshness"],
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
    if reason == "entry_macd_not_positive_open":
        detail = dict(metadata.get("macd") or {})
        return (
            "Wait: exact causal one-second MACD entry regime is closed — "
            f"line={_display_value(detail.get('macd_line'))}, "
            f"signal={_display_value(detail.get('macd_signal'))}; requires "
            "line > signal and line > 0."
        )
    if reason == "entry_waiting_for_closed_one_second_macd":
        detail = dict(metadata.get("entry_frame") or {})
        return (
            "Wait: entry requires the completed one-second MACD frame — "
            f"source timeframe={detail.get('source_timeframe') or 'event-native'}, "
            f"events={', '.join(detail.get('evaluation_events') or []) or 'none'}."
        )
    if reason == "entry_macd_open_gap_below_threshold":
        detail = dict(metadata.get("macd") or {})
        return (
            "Wait: completed one-second MACD is positive/open but its separation is below "
            "the noise threshold — "
            f"line={_display_value(detail.get('macd_line'))}, "
            f"signal={_display_value(detail.get('macd_signal'))}, "
            f"gap={_display_value(detail.get('open_gap_bps'))} bps; requires at least "
            f"{_display_value(detail.get('minimum_open_gap_bps'))} bps."
        )
    if reason == "entry_closed_candle_bearish":
        detail = dict(metadata.get("completed_candle") or {})
        return (
            "Wait: the completed one-second candle is bearish for the requested entry — "
            f"open={_display_value(detail.get('open'))}, "
            f"close={_display_value(detail.get('close'))}; requires "
            f"{detail.get('required') or 'a non-bearish close'}."
        )
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
    if reason == "entry_momentum_not_strengthening":
        detail = dict(metadata.get("entry_momentum_confirmation") or {})
        return (
            "Wait: price cleared structural resistance while one-second MACD remained positive/open, "
            "but breakout momentum was contracting — histogram "
            f"{_display_value(detail.get('current_histogram'))} versus "
            f"{_display_value(detail.get('baseline_histogram'))} "
            f"{int(detail.get('lookback_ms') or 0)} ms earlier."
        )
    if reason == "insufficient_structural_target_room":
        return (
            "Wait: the configured structural profit target does not leave enough room for entry — "
            f"reference={_display_value(metadata.get('entry_target_room_reference'))}, "
            f"target={_display_value(metadata.get('selected_structural_target'))}, "
            f"room={_display_value(metadata.get('structural_target_gap_bps'))} bps; requires at least "
            f"{_display_value(metadata.get('minimum_entry_target_gap_bps'))} bps."
        )
    if reason == "structural_target_room_retest_required":
        detail = dict(metadata.get("entry_target_room_retest") or {})
        return (
            "Wait: the prior resistance crossing failed the structural target-room gate and price "
            "has not retested that boundary — "
            f"price={observation.price:.4g}, boundary={_display_value(detail.get('boundary'))}."
        )
    if reason == "waiting_for_target_replenishment_pullback":
        detail = dict(metadata.get("target_replenishment") or {})
        failed = ", ".join(str(value) for value in detail.get("failed") or [])
        return f"Hold: profit target filled; replenishment is armed but waiting — {failed or 'pullback confirmation unavailable'}."
    if reason == "waiting_for_reentry_pullback":
        return "Wait: the prior campaign is flat; waiting for the configured ATR/basis-point pullback before another entry can arm."
    if reason == "waiting_for_reentry_reclaim":
        return "Wait: the post-exit pullback is confirmed; waiting for a fresh causal Unified resistance reclaim with positive/open one-second MACD."
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
        "entry_confirmed": "Enter: latched liquidity, executable spread/activity, VWAP, positive/open one-second MACD (line > signal and line > 0), and Unified resistance acceptance all passed.",
        "reentry_confirmed": "Re-enter: executable spread/activity, VWAP, positive/open one-second MACD (line > signal and line > 0), and a fresh Unified resistance recovery all passed.",
        "structural_entry_tranche_confirmed": "Add: another selected top-three Unified resistance crossed on the completed one-second bar while current liquidity, VWAP, and positive/open MACD all passed.",
        "target_profit_replenishment": "Profit-target replenishment: a target filled, price made a causal pullback, Unified support held, and VWAP plus positive/open one-second MACD remained valid.",
        "structural_profit_target_advanced": "Target update: a completed one-second candle held above another qualifying level while MACD remained positive and open; the live profit target advanced to the configured ordinal qualifying level.",
        "failure_to_extend_partial": "Profit reduction: price stopped extending while QMD flow deteriorated; sell half and keep the protected remainder.",
        "qmd_flow_geometry_exhaustion": "Exit: QMD flow structure weakened with confident flow-price divergence.",
        "loss_of_confirmed_higher_low": "Exit: price lost the latest causally confirmed one-second higher low.",
        "macd_closed_backstop": "Exit: one-second MACD remained closed for the configured backstop duration.",
        "macd_signal_crossed_above_line": "Exit: the causal one-second MACD signal crossed strictly above the MACD line.",
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
    left_operand = _condition_operand(condition, "left", observation)
    left = left_operand["value"]
    if left is None or not bool(left_operand["fresh"]):
        return False
    comparator = str(condition.get("comparator") or "")
    if comparator == "is_true":
        return bool(left)
    right_source_id = str(
        condition.get("right_field_ref") or condition.get("right_source_id") or ""
    )
    right_operand = (
        _condition_operand(condition, "right", observation)
        if right_source_id
        else {"value": condition.get("value"), "fresh": True}
    )
    right = right_operand["value"]
    if right is None or not bool(right_operand["fresh"]):
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
    operand = _condition_operand(condition, side, observation)
    return operand["value"] if bool(operand["fresh"]) else None


def _condition_operand(
    condition: dict[str, Any], side: str, observation: StrategyObservation
) -> dict[str, Any]:
    field_ref = str(condition.get(f"{side}_field_ref") or "")
    source_id = str(condition.get(f"{side}_source_id") or "")
    interval = _condition_interval_expression(
        condition.get(f"{side}_interval")
        or condition.get(f"{side}_timeframe")
    )
    aggregation = str(condition.get(f"{side}_aggregation") or "")
    effective_timeframe = interval or observation.source_timeframe
    candidates: list[str] = []
    for identity in (field_ref, source_id):
        if not identity:
            continue
        if interval and aggregation:
            candidates.append(f"{identity}@{interval}#{aggregation}")
        if interval:
            candidates.append(f"{identity}@{interval}")
        elif effective_timeframe:
            candidates.append(f"{identity}@{effective_timeframe}")
        candidates.append(identity)
    for candidate in candidates:
        cached = observation.source_values.get(candidate)
        if isinstance(cached, dict):
            if cached.get("value") is not None:
                return _operand_with_freshness(
                    cached.get("value"),
                    cached.get("observed_at"),
                    source_id=source_id or field_ref,
                    timeframe=effective_timeframe,
                    observation=observation,
                    maximum_age_ms=condition.get(f"{side}_maximum_age_ms"),
                )
        elif cached is not None:
            return _operand_with_freshness(
                cached,
                None,
                source_id=source_id or field_ref,
                timeframe=effective_timeframe,
                observation=observation,
                maximum_age_ms=condition.get(f"{side}_maximum_age_ms"),
            )
    value = _observation_source_value(
        observation,
        source_id or field_ref,
        effective_timeframe,
    )
    return _operand_with_freshness(
        value,
        observation.observed_at.isoformat() if value is not None else None,
        source_id=source_id or field_ref,
        timeframe=effective_timeframe,
        observation=observation,
        maximum_age_ms=condition.get(f"{side}_maximum_age_ms"),
    )


def _operand_with_freshness(
    value: Any,
    observed_at: Any,
    *,
    source_id: str,
    timeframe: str,
    observation: StrategyObservation,
    maximum_age_ms: Any = None,
) -> dict[str, Any]:
    maximum_age = _source_maximum_age_ms(
        source_id,
        timeframe,
        override=maximum_age_ms,
    )
    result = {
        "value": value,
        "observed_at": str(observed_at or "") or None,
        "age_ms": None,
        "maximum_age_ms": maximum_age,
        "fresh": value is not None,
        "freshness": "current" if value is not None else "unavailable",
    }
    if value is None or maximum_age is None:
        return result
    if not observed_at:
        result.update({"fresh": False, "freshness": "missing_observed_at"})
        return result
    try:
        source_time = datetime.fromisoformat(str(observed_at).replace("Z", "+00:00"))
        age_ms = (observation.observed_at - source_time).total_seconds() * 1_000.0
    except (TypeError, ValueError):
        result.update({"fresh": False, "freshness": "invalid_observed_at"})
        return result
    result["age_ms"] = age_ms
    if age_ms < 0:
        result.update({"fresh": False, "freshness": "future"})
    elif age_ms > maximum_age:
        result.update({"fresh": False, "freshness": "stale"})
    return result


def _source_maximum_age_ms(
    source_id: str,
    timeframe: str,
    *,
    override: Any = None,
) -> int | None:
    if override is not None:
        return max(0, int(override))
    if source_id in SOURCE_MAXIMUM_AGE_OVERRIDES_MS:
        return SOURCE_MAXIMUM_AGE_OVERRIDES_MS[source_id]
    source = next(
        (row for row in strategy_input_catalog() if row["source_id"] == source_id),
        None,
    )
    if source is None:
        return SOURCE_MAXIMUM_AGE_MS.get(timeframe)
    supported = set(source["timeframes"])
    if supported == {"session"}:
        return None
    return SOURCE_MAXIMUM_AGE_MS.get(timeframe)


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
    effective_timeframe = timeframe or observation.source_timeframe
    cached = observation.source_values.get(f"{source_id}@{effective_timeframe}")
    if cached is None:
        cached = observation.source_values.get(source_id)
    if isinstance(cached, dict):
        operand = _operand_with_freshness(
            cached.get("value"),
            cached.get("observed_at"),
            source_id=source_id,
            timeframe=effective_timeframe,
            observation=observation,
        )
        return operand["value"] if bool(operand["fresh"]) else None
    if cached is not None:
        operand = _operand_with_freshness(
            cached,
            None,
            source_id=source_id,
            timeframe=effective_timeframe,
            observation=observation,
        )
        return operand["value"] if bool(operand["fresh"]) else None
    return _observation_source_value(observation, source_id, timeframe)


def _observation_source_value(
    observation: StrategyObservation,
    source_id: str,
    timeframe: str,
) -> Any:
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
    exit_gap = settings.get("minimum_macd_exit_gap_bps")
    elapsed_ms = _elapsed_since(str(state.get("entry_at") or ""), observation.observed_at)

    downside = dict(settings.get("downside_loss_guard") or {})
    downside_timeframe = str(downside.get("timeframe") or "1s")
    if bool(downside.get("enabled", False)) and gain_pct < 0:
        line = _source_value(observation, "indicator.macd.line", downside_timeframe)
        signal = _source_value(observation, "indicator.macd.signal", downside_timeframe)
        gap_bps = _macd_gap_bps(observation.price, line, signal)
        if bool(downside.get("macd_closed", True)) and (
            line is not None
            and signal is not None
            and float(signal) > float(line)
            and (exit_gap is None or (gap_bps is not None and -gap_bps + 1e-12 >= float(exit_gap)))
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
                    "histogram_bps": gap_bps,
                    "minimum_exit_gap_bps": exit_gap,
                },
            }
        vwap_source_id = str(
            downside.get("vwap_source_id")
            or "indicator.vwap.execution_value"
        )
        vwap = _source_value(observation, vwap_source_id, downside_timeframe)
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
                    "vwap_source_id": vwap_source_id,
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
    gap_bps = _macd_gap_bps(observation.price, line, signal)
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
    if exit_gap is not None:
        macd_closed = bool(gap_bps is not None and -gap_bps + 1e-12 >= float(exit_gap))
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
                "histogram_bps": gap_bps,
                "minimum_exit_gap_bps": exit_gap,
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
        "maximum_age_ms_by_timeframe": {
            timeframe: SOURCE_MAXIMUM_AGE_OVERRIDES_MS.get(
                source_id,
                SOURCE_MAXIMUM_AGE_MS.get(timeframe),
            )
            for timeframe in timeframes
            if SOURCE_MAXIMUM_AGE_OVERRIDES_MS.get(
                source_id,
                SOURCE_MAXIMUM_AGE_MS.get(timeframe),
            ) is not None
        },
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
    return max(0.0, min(1.0, (observation.qmd_confidence + (0.75 if observation.execution_vwap is not None else 0) + (0.75 if observation.macd_line is not None else 0)) / 3))


def _initial_stop(
    observation: StrategyObservation,
    parameters: dict[str, Any],
    reference: float | None,
    *,
    side: str,
    selection_evidence: dict[str, Any] | None = None,
) -> float:
    stop = parameters["protection"]["stop"]
    method = str(stop.get("method") or "hybrid")
    direction = -1 if side == "long" else 1
    maximum_risk_pct = float(stop.get("maximum_risk_pct") or 15.0)
    maximum_risk = observation.price * (
        1 + direction * maximum_risk_pct / 100
    )
    if method == "ordinal_qualified_support":
        relative_threshold = float(
            stop.get("minimum_ticker_relative_quality_score") or 0.0
        )
        minimum_observations = float(stop.get("minimum_hold_observations") or 0.0)
        ordinal = max(1, int(stop.get("support_level_ordinal") or 2))
        rows = _consolidated_structure_levels(
            [dict(row) for row in observation.structural_support_levels],
            side=side,
        )
        qualified: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            candidate = _level_metric(row, "price", "lower", "upper")
            if candidate <= 0:
                continue
            on_protective_side = (
                candidate < observation.price
                if side == "long"
                else candidate > observation.price
            )
            observations = _level_metric(row, "hold_observation_count")
            if "hold_observation_count" not in row:
                observations = (
                    _level_metric(row, "hold_count") + _level_metric(row, "break_count")
                    if "hold_count" in row or "break_count" in row
                    else float("hold_probability" in row)
                )
            if (
                on_protective_side
                and _level_passes_configured_quality(row, stop)
                and observations >= minimum_observations
            ):
                qualified.append((candidate, row))
        qualified.sort(key=lambda item: item[0], reverse=side == "long")
        selected_row = qualified[ordinal - 1][1] if len(qualified) >= ordinal else None
        selected_level = qualified[ordinal - 1][0] if selected_row is not None else None
        buffer_bps = float(stop.get("structure_buffer_bps") or 0.0)
        structural_stop = (
            selected_level * (1 + direction * buffer_bps / 10_000.0)
            if selected_level is not None
            else None
        )
        if bool(stop.get("require_qualified_support")):
            # Portfolio must size from actual structural risk. Never manufacture
            # a percentage stop when the required support is absent or distant.
            selected = structural_stop or 0.0
        elif side == "long":
            selected = max(
                maximum_risk,
                structural_stop if structural_stop is not None else maximum_risk,
            )
            selected = min(selected, observation.price * 0.9999)
        else:
            selected = min(
                maximum_risk,
                structural_stop if structural_stop is not None else maximum_risk,
            )
            selected = max(selected, observation.price * 1.0001)
        selected = round(selected, 4)
        if selection_evidence is not None:
            audit_rows = qualified[: max(3, ordinal + 1)]
            quality_threshold_evidence = (
                {
                    "minimum_ticker_relative_quality_score": relative_threshold,
                    "ticker_relative_unavailable_policy": (
                        "fail_closed" if stop.get("strict_ticker_relative_quality_gate") else "fail_open"
                    ),
                }
                if "minimum_ticker_relative_quality_score" in stop
                else {
                    "minimum_hold_probability_exclusive": float(
                        stop.get("minimum_hold_probability") or 0.0
                    ),
                    "minimum_hold_quality_score": float(
                        stop.get("minimum_hold_quality_score") or 0.0
                    ),
                }
            )
            selection_evidence.update({
                "selection_mode": method,
                "reference_price": observation.price,
                "maximum_risk_pct": maximum_risk_pct,
                "maximum_risk_stop": round(maximum_risk, 4),
                **quality_threshold_evidence,
                "minimum_hold_observations": minimum_observations,
                "support_level_ordinal": ordinal,
                "qualified_level_count": len(qualified),
                "qualified_levels_truncated": len(audit_rows) < len(qualified),
                "qualified_levels": [
                    {
                        **_compact_structural_level_reference(row),
                        "protective_price": round(price, 4),
                        "ordinal_below_reference": index + 1,
                    }
                    for index, (price, row) in enumerate(audit_rows)
                ],
                "selected_support_level": (
                    _compact_structural_level_reference(selected_row)
                    if selected_row is not None
                    else None
                ),
                "selected_stop": selected,
                "fallback_reason": (
                    None
                    if selected_row is not None
                    else "fewer_than_required_qualified_support_levels"
                ),
            })
        return selected

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
    structure_stop = structure_base * (
        1 + direction * float(stop["structure_buffer_bps"]) / 10_000
    )
    volatility = observation.volatility if observation.volatility > 0 else observation.price * 0.002
    volatility_stop = observation.price + direction * volatility * float(stop["volatility_multiple"])
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
    if side == "long":
        return round(max(maximum_risk, min(selected, observation.price * 0.9999)), 4)
    return round(min(maximum_risk, max(selected, observation.price * 1.0001)), 4)


def _trailing_amount(
    observation: StrategyObservation,
    parameters: dict[str, Any],
    *,
    stop: float | None = None,
) -> float | None:
    trailing = parameters["protection"]["trailing"]
    if trailing.get("mode") == "qualified_support":
        return None
    if not trailing["enabled"]:
        return None
    if str(parameters["protection"]["stop"].get("method") or "") == "ordinal_qualified_support":
        protective_stop = (
            stop
            if stop is not None
            else _initial_stop(
                observation,
                parameters,
                observation.price,
                side=_strategy_side(parameters),
            )
        )
        return round(abs(observation.price - protective_stop), 4)
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
    if trailing.get("mode") == "qualified_support":
        evidence: dict[str, Any] = {}
        candidate = _initial_stop(observation, parameters, None, side=side, selection_evidence=evidence)
        selected = evidence.get("selected_support_level") or {}
        confirmed = float(selected.get("confirmed_at_ms") or 0)
        entry_at = _optional_aware_datetime(state.get("entry_at"))
        prior_selection = dict(state.get("trailing_support_selection") or {}).get("selected_support_level") or {}
        prior_confirmed = float(prior_selection.get("confirmed_at_ms") or 0)
        newer = (entry_at is not None
                 and max(entry_at.timestamp() * 1000, prior_confirmed) < confirmed
                 <= observation.observed_at.timestamp() * 1000)
        tightens = candidate > current if side == "long" else 0 < candidate < current
        if newer and tightens:
            state["trailing_support_selection"] = evidence
            return candidate
        return current
    distance = float(
        state.get("trailing_amount")
        or _trailing_amount(observation, parameters, stop=current)
        or 0
    )
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


def _profit_level_score(
    row: dict[str, Any], policy: Mapping[str, Any] | None = None
) -> float:
    """Rank by the quality authority selected by the revisioned policy."""

    if "minimum_ticker_relative_quality_score" in (policy or {}):
        return _level_metric(row, "ticker_relative_quality_score")
    return _level_metric(row, "hold_quality_score", "hold_probability")


def _target_frontier_from_selection(
    selection: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Retain the bounded, ordered level ladder used by target management."""

    if not isinstance(selection, Mapping):
        return []
    return [
        _compact_structural_level_reference(row)
        | {
            "target_price": _level_metric(dict(row), "target_price", "price"),
            "ordinal_above_reference": int(row.get("ordinal_above_reference") or index + 1),
        }
        for index, row in enumerate(selection.get("qualified_levels") or ())
        if isinstance(row, Mapping)
    ][:3]


def _target_ratchet_acceptance(
    frontier: list[dict[str, Any]],
    *,
    close: float,
    side: str,
    buffer_bps: float,
) -> dict[str, Any]:
    """Require a completed close beyond the nearest prior target zone."""

    if not frontier:
        return {
            "passed": False,
            "reason": "prior_target_frontier_unavailable",
            "close": close,
        }
    nearest = frontier[0]
    center = _level_metric(nearest, "target_price", "price")
    zone_boundary = (
        _level_metric(nearest, "upper", "target_price", "price")
        if side == "long"
        else _level_metric(nearest, "lower", "target_price", "price")
    )
    threshold = zone_boundary * (
        1.0 + buffer_bps / 10_000.0
        if side == "long"
        else 1.0 - buffer_bps / 10_000.0
    )
    passed = close > threshold if side == "long" else close < threshold
    return {
        "passed": passed,
        "reason": (
            "completed_close_beyond_nearest_target_zone"
            if passed
            else "completed_close_not_beyond_nearest_target_zone"
        ),
        "close": close,
        "level": nearest,
        "level_price": center,
        "zone_boundary": zone_boundary,
        "threshold_price": threshold,
        "buffer_bps": buffer_bps,
    }


def _structural_profit_targets(
    observation: StrategyObservation,
    parameters: dict[str, Any],
    *,
    stop: float,
    side: str,
    luld_target: float | None,
    selection_evidence: dict[str, Any] | None = None,
    qualified_levels_out: list[dict[str, Any]] | None = None,
) -> list[float]:
    """Build causal targets from level-book resistance/support evidence."""
    policy = dict(parameters["protection"].get("profit_ladder") or {})
    if not bool(policy.get("enabled", True)):
        return [luld_target] if luld_target is not None else []
    entry = observation.price
    # Profit targets are geometric: any qualifying level above a long entry
    # (or below a short entry) is relevant even when its last lifecycle role
    # was the opposite side.  Price-relative filtering below assigns the role.
    level_rows = _consolidated_structure_levels(list(
        observation.structural_resistance_levels if side == "long" and policy.get("require_resistance_role")
        else (*observation.structural_support_levels, *observation.structural_resistance_levels)
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
    ranked_candidates: list[tuple[float, float, dict[str, Any]]] = []
    for row in level_rows:
        strength = _level_metric(dict(row), "salience", "strength")
        confidence = _level_metric(dict(row), "confidence")
        reaction = _level_metric(dict(row), "reaction_probability")
        reversal = _level_metric(dict(row), "reversal_probability")
        hold = _level_metric(dict(row), "hold_probability")
        hold_observations = _level_metric(dict(row), "hold_observation_count")
        if "hold_observation_count" not in row:
            hold_observations = (
                _level_metric(dict(row), "hold_count") + _level_metric(dict(row), "break_count")
                if "hold_count" in row or "break_count" in row
                else float("hold_probability" in row)
            )
        break_probability = _level_metric(dict(row), "break_probability")
        if "break_probability" not in row:
            break_probability = max(0.0, 1.0 - hold)
        break_count = _level_metric(dict(row), "break_count")
        maximum_break_count = policy.get("maximum_break_count")
        score = _profit_level_score(dict(row), policy)
        candidate = row.get("price")
        if candidate is None:
            candidate = row.get("lower") if side == "long" else row.get("upper")
        if (
            candidate is not None
            and strength >= float(policy.get("minimum_level_strength") or 0.0)
            and confidence >= float(policy.get("minimum_level_confidence") or 0.0)
            and reaction >= float(policy.get("minimum_reaction_probability") or 0.0)
            and reversal >= float(policy.get("minimum_reversal_probability") or 0.0)
            and _level_passes_configured_quality(row, policy)
            and hold_observations >= float(policy.get("minimum_hold_observations") or 0.0)
            and break_probability
            <= float(policy.get("maximum_break_probability", 1.0))
            and (
                maximum_break_count is None
                or break_count <= float(maximum_break_count)
            )
            and score >= float(policy.get("minimum_composite_score") or 0.0)
        ):
            candidate_value = float(candidate)
            favorable_side = (
                candidate_value > entry
                if side == "long"
                else candidate_value < entry
            )
            if favorable_side and (
                maximum_price is None or candidate_value <= maximum_price
            ):
                ranked_candidates.append((score, candidate_value, dict(row)))
    # The scalar nearest-level fields do not carry the Unified Level Book's
    # hold/break lifecycle evidence.  They may remain a compatibility fallback
    # only when the configured strategy does not require that evidence; using
    # them under an active quality gate would silently bypass the agreed contract.
    if (
        not ranked_candidates
        and float(policy.get("minimum_ticker_relative_quality_score") or 0.0) <= 0.0
        and float(policy.get("minimum_hold_probability") or 0.0) <= 0.0
        and float(policy.get("minimum_hold_quality_score") or 0.0) <= 0.0
    ):
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
        nearest_score = _profit_level_score(nearest_row, policy)
        if (
            nearest_price is not None
            and nearest_strength >= float(policy.get("minimum_level_strength") or 0.0)
            and nearest_confidence >= float(policy.get("minimum_level_confidence") or 0.0)
            and nearest_strength >= float(policy.get("minimum_reaction_probability") or 0.0)
            and nearest_strength >= float(policy.get("minimum_reversal_probability") or 0.0)
            and nearest_score >= float(policy.get("minimum_composite_score") or 0.0)
            and (maximum_price is None or float(nearest_price) <= maximum_price)
        ):
            ranked_candidates.append((nearest_score, float(nearest_price), nearest_row))
    if qualified_levels_out is not None:
        qualified_levels_out.extend(_compact_structural_level_reference(row)
                                    for _, _, row in ranked_candidates)
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
    if selection_mode in {"ordinal_qualified_level", "second_next_level"}:
        ordered_prices = sorted(
            {candidate for _, candidate, _ in ranked_candidates},
            reverse=side == "short",
        )
        default_ordinal = 2 if selection_mode == "second_next_level" else 3
        target_ordinal = max(
            1,
            int(policy.get("target_level_ordinal") or default_ordinal),
        )
        selected = (
            [ordered_prices[target_ordinal - 1]]
            if len(ordered_prices) >= target_ordinal
            else []
        )
    elif selection_mode == "highest_price_below_cap":
        ordered_prices = sorted(
            {candidate for _, candidate, _ in ranked_candidates},
            reverse=side == "long",
        )
        selected = ordered_prices[:maximum]
    else:
        selected = [
            candidate
            for _, candidate, _ in sorted(
                ranked_candidates,
                key=lambda item: (-item[0], item[1] if side == "long" else -item[1]),
            )[:maximum]
        ]
    append_candidates(selected)
    unique.sort(reverse=side == "short")
    if selection_evidence is not None:
        default_ordinal = 2 if selection_mode == "second_next_level" else 3
        target_ordinal = max(
            1,
            int(policy.get("target_level_ordinal") or default_ordinal),
        )
        ordered_ladder = sorted(
            ranked_candidates,
            key=lambda item: item[1],
            reverse=side == "short",
        )
        # Strategy Activity needs enough evidence to prove the ordinal choice,
        # not a copy of the complete level book on every target replacement.
        # Keep the selected neighborhood bounded so high-frequency runs remain
        # responsive even when a ticker owns thousands of historical levels.
        audit_ladder = ordered_ladder[: max(3, target_ordinal + 1)]
        selection_evidence.update({
            "selection_mode": selection_mode,
            "target_level_ordinal": target_ordinal,
            "reference_price": entry,
            "qualified_level_count": len(ordered_ladder),
            "qualified_levels_truncated": len(audit_ladder) < len(ordered_ladder),
            "qualified_levels": [
                {
                    **_compact_structural_level_reference(row),
                    "target_price": round(price, 4),
                    "composite_score": score,
                    "ordinal_above_reference": index + 1,
                }
                for index, (score, price, row) in enumerate(audit_ladder)
            ],
            "selected_target_prices": list(unique),
        })
    return unique


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {key: dict(value) if isinstance(value, dict) else value for key, value in base.items()}
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
