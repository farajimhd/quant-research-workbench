from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

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
STRATEGY_REVISION = 5

RULE_COMPARATORS = {
    "above_by_bps",
    "equals",
    "greater_or_equal",
    "greater_than",
    "is_true",
    "less_or_equal",
    "less_than",
}


def strategy_input_catalog() -> list[dict[str, Any]]:
    """Code-owned strategy inputs and their authoritative runtime projections."""

    return [
        _input("market.last_price", "Last price", "Market", "qmd-derived", "price", "price", ["100ms", "1s", "5s", "10s", "30s", "1m", "5m"]),
        _input("market.previous_close", "Previous close", "Market", "qmd-reference", "previous_close", "price", ["session"]),
        _input("market.previous_high", "Previous high", "Market", "qmd-reference", "previous_high", "price", ["session"]),
        _input("indicator.structure.swing_high", "Confirmed swing high", "QMD indicator", "qmd", "swing_high", "price", ["100ms", "1s", "5s", "10s", "30s", "1m", "5m"]),
        _input("indicator.structure.swing_low", "Confirmed swing low", "QMD indicator", "qmd", "swing_low", "price", ["100ms", "1s", "5s", "10s", "30s", "1m", "5m"]),
        _input("indicator.structure.bullish_choch", "Bullish change of character", "QMD indicator", "qmd", "bullish_choch", "boolean", ["100ms", "1s", "5s", "10s", "30s", "1m", "5m"]),
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
    for group in stage.get("groups") or []:
        if not bool(group.get("enabled", True)):
            continue
        for condition in group.get("conditions") or []:
            for key in ("left_timeframe", "right_timeframe"):
                value = str(condition.get(key) or "")
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
        if self.strategy_id != STRATEGY_ID:
            raise ValueError(f"Unsupported strategy assignment: {self.strategy_id}")
        if self.strategy_revision != STRATEGY_REVISION:
            raise ValueError(f"Unsupported strategy revision: {self.strategy_revision}")
        if self.conid <= 0:
            raise ValueError("Strategy assignment conid must be positive")

    def payload(self) -> dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status.value
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
                    {"key": "flow_structure_composite", "timeframe": "100ms", "role": "confirmation", "required": False, "maximum_age_ms": 300, "minimum_score": 0.3, "minimum_confidence": 0.5},
                    {"key": "vwap", "timeframe": "5s", "role": "confirmation", "required": False, "maximum_age_ms": 6000},
                    {"key": "macd", "timeframe": "5s", "role": "confirmation", "required": False, "maximum_age_ms": 6000},
                    {"key": "generic_structure", "timeframe": "1s", "role": "trigger", "required": True, "maximum_age_ms": 2000},
                ],
                "signals": [
                    {"key": "price_volume_expansion", "timeframe": "1s", "role": "trigger", "required": False, "maximum_age_ms": 2000, "minimum_score": 0.65},
                    {"key": "vwap_transition", "timeframe": "10s", "role": "trigger", "required": False, "maximum_age_ms": 11000, "minimum_score": 0.6},
                    {"key": "flow_price_divergence", "timeframe": "100ms", "role": "veto", "required": False, "maximum_age_ms": 500},
                    {"key": "liquidity_dislocation", "timeframe": "100ms", "role": "veto", "required": False, "maximum_age_ms": 500},
                    {"key": "company_news", "role": "trigger", "required": False, "maximum_age_ms": 60000, "minimum_score": 0.7},
                    {"key": "sec_filing", "role": "trigger", "required": False, "maximum_age_ms": 60000},
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
            "veto": {"flow_price_divergence": 0.75, "liquidity_dislocation": 0.75},
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
                "maximum_risk_pct": 1.5,
            },
            "trailing": {
                "enabled": True,
                "activation_gain_pct": 0.5,
                "distance_volatility_multiple": 1.0,
                "minimum_distance_bps": 12.0,
            },
            "luld_profit_target": {"enabled": True, "buffer_bps": 10.0, "require_authoritative_band": True},
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
        "reentry": {"enabled": True, "cooldown_ms": 0, "maximum_attempts": 3, "require_new_confirmation": True},
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
    if int(parameters["reentry"]["cooldown_ms"]) < 0 or int(parameters["reentry"]["maximum_attempts"]) < 0:
        raise ValueError("Re-entry cooldown and maximum attempts cannot be negative")
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
    for group in stage.get("groups") or []:
        if not bool(group.get("enabled", True)):
            continue
        for condition in group.get("conditions") or []:
            for source_key, timeframe_key in (
                ("left_source_id", "left_timeframe"),
                ("right_source_id", "right_timeframe"),
            ):
                source_id = str(condition.get(source_key) or "")
                if source_id:
                    dependencies.add(
                        (source_id, str(condition.get(timeframe_key) or ""))
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


class LongMomentumStrategyEngine:
    """Deterministic long-only policy engine over causal point-in-time observations."""

    def evaluate(self, assignment: StrategyAssignment, observation: StrategyObservation) -> StrategyEngineResult:
        if assignment.ticker.upper() != observation.ticker.upper():
            raise ValueError("Observation ticker does not match strategy assignment")
        state = dict(assignment.state)
        status = assignment.status
        parameters = resolve_long_momentum_parameters(assignment.parameters)
        if status in {AssignmentStatus.DISABLED, AssignmentStatus.COMPLETED, AssignmentStatus.ERROR}:
            return self._result(assignment, observation, "wait", "assignment_not_active", 0.0, 1.0, state, status)
        if status == AssignmentStatus.PAUSED:
            return self._result(assignment, observation, "wait", "assignment_paused", 0.0, 1.0, state, status)
        if not assignment.permissions.observe:
            return self._result(assignment, observation, "wait", "observation_not_authorized", 0.0, 1.0, state, status)
        if not _observation_updates_active_rules(
            parameters,
            observation,
            reentries=int(state.get("reentries") or 0),
        ):
            return self._result(assignment, observation, "wait", "no_active_rule_source_updated", 0.0, 1.0, state, status)

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
        if reentries > int(reentry["maximum_attempts"]):
            return self._result(assignment, observation, "wait", "maximum_reentries_reached", 0.0, 1.0, state, AssignmentStatus.COMPLETED)
        if reentries and not assignment.permissions.reenter:
            return self._result(assignment, observation, "wait", "reentry_not_authorized", 0.0, 1.0, state, AssignmentStatus.COMPLETED)
        if reentries and state.get("last_exit_at"):
            last_exit = datetime.fromisoformat(str(state["last_exit_at"]).replace("Z", "+00:00"))
            elapsed_ms = (observation.observed_at - last_exit).total_seconds() * 1000
            if elapsed_ms < float(reentry["cooldown_ms"]):
                return self._result(assignment, observation, "wait", "reentry_cooldown", 0.0, 1.0, state, AssignmentStatus.REENTRY_COOLDOWN)
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

        phase_policy = dict(parameters.get("phase_policy") or {})
        phase = dict(phase_policy.get(phase_name) or {})
        phase_rules = (
            dict(phase.get("rules") or {})
            if reentries
            else dict(parameters.get("entry_rules") or {})
        )
        rule_result = evaluate_entry_decision_rules(phase_rules, observation)
        reference_name, reference, reference_buffer_bps = _trigger_reference(
            phase_rules,
            rule_result,
            observation,
        )
        operational_triggers = [
            key
            for key, value in {
                "manual_entry_request": observation.manual_entry_request,
                "force_entry": observation.force_entry,
            }.items()
            if value
        ]
        triggered = [*operational_triggers, *rule_result["trigger"]["matched_groups"]]
        confirmation_score = float(rule_result["confirmation"]["score"])
        confirmation = dict(rule_result["confirmation"]["groups"])
        vetoes = list(rule_result["veto"]["matched_groups"])
        can_enter = bool(triggered) and not vetoes and (
            observation.force_entry or bool(rule_result["confirmation"]["passed"])
        )
        if not can_enter:
            reason = "entry_vetoed" if vetoes else "entry_confirmation_incomplete" if triggered else "waiting_for_entry_trigger"
            return self._result(
                assignment,
                observation,
                "wait",
                reason,
                confirmation_score,
                _confirmation_confidence(observation),
                state,
                AssignmentStatus.WATCHING,
                metadata={"triggers": triggered, "vetoes": vetoes, "confirmation": confirmation, "entry_rules": rule_result},
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
            }
        )
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
                "entry_rules": rule_result,
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
        if side == "long":
            state["high_water_price"] = max(float(state.get("high_water_price") or observation.price), observation.price)
        else:
            state["low_water_price"] = min(float(state.get("low_water_price") or observation.price), observation.price)
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
        if protection_breached:
            exit_route = {
                "route_id": "oms-protective-stop",
                "name": "OMS protective stop",
                "mechanism": "protective_stop",
                "position_fraction": 1.0,
            }
        elif exit_automatic:
            exit_rule_sets = list(
                dict(
                    dict(parameters.get("phase_policy") or {}).get("exit") or {}
                ).get("rule_sets") or []
            )
            exit_route = (
                _matching_exit_rule_set(
                    exit_rule_sets,
                    observation=observation,
                    entry_at=str(state.get("entry_at") or ""),
                )
                if exit_rule_sets
                else _matching_exit_route(
                    list(parameters.get("exit_routes") or []),
                    observation=observation,
                    protective_stop=stop,
                    failed_breakout=failed_breakout,
                    side=side,
                )
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
        if exit_route is not None and (
            assignment.permissions.exit
            or str(exit_route.get("mechanism")) == "protective_stop"
        ):
            reason = str(exit_route["mechanism"])
            state["last_exit_reason"] = reason
            state["last_exit_route_id"] = str(exit_route["route_id"])
            state["last_exit_at"] = observation.observed_at.isoformat()
            state["reentries"] = int(state.get("reentries") or 0) + 1
            next_status = AssignmentStatus.COMPLETED if state.get("disable_after_exit") or not assignment.permissions.reenter else AssignmentStatus.REENTRY_COOLDOWN
            return self._result(
                assignment, observation, "exit" if side == "long" else "cover", reason, observation.qmd_score,
                max(observation.qmd_confidence, 0.5), state, next_status,
                quantity=observation.position_quantity * float(exit_route.get("position_fraction") or 1.0), invalidation_price=stop,
                trailing_amount=_trailing_amount(observation, parameters),
                order_intent=dict(exit_route.get("order_intent") or {}),
                metadata={
                    "exit_rule_set_id": exit_route["route_id"],
                    "exit_rule_set_name": exit_route["name"],
                    "exit_route_id": exit_route["route_id"],
                    "exit_route_name": exit_route["name"],
                    "buy_back": bool(
                        assignment.permissions.reenter
                        and not state.get("disable_after_exit")
                    ),
                },
            )

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
        entry_price = float(state.get("entry_reference_price") or observation.average_price or observation.price)
        gain_pct = (
            (observation.price / entry_price - 1) * 100
            if side == "long"
            else (entry_price / observation.price - 1) * 100
        ) if entry_price > 0 else 0
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
        pocket_qty = min(
            observation.position_quantity,
            max(0.0, observation.position_quantity * float(pocket["quantity_fraction"])),
        )
        remaining = observation.position_quantity - pocket_qty
        if (
            assignment.permissions.reduce
            and pocket["enabled"]
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
            state["last_exit_at"] = observation.observed_at.isoformat()
            return self._result(
                assignment, observation, "exit" if side == "long" else "cover", "profit_pocket",
                max(0.0, observation.qmd_score), _confirmation_confidence(observation),
                state, AssignmentStatus.EXIT_PENDING, quantity=pocket_qty,
                invalidation_price=stop,
                trailing_amount=_trailing_amount(observation, parameters),
                metadata={
                    "gain_pct": gain_pct,
                    "buy_back": bool(
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
                **(metadata or {}),
                "assignment_id": assignment.assignment_id,
                "reference_price": observation.price,
                "status": status.value,
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
                    outside_rth=False,
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
                        "session_routing": "smart",
                        "eligible_sessions": list(
                            dict(
                                assignment.parameters.get("strategy_behavior")
                                or {}
                            ).get("eligible_sessions")
                            or ["regular"]
                        ),
                        **(metadata or {}),
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
            "reference_price": observation.price,
            "invalidation_price": invalidation_price,
            "status": status.value,
            "state": state,
            "evidence": metadata or {},
        }
        return StrategyEngineResult(StrategyEvaluation(signals=(signal,), intents=intents), state, status, payload)


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
    slices: list[ProtectionSlice] = []
    for raw in configured.get("slices") or []:
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
            if anchor is None and (
                anchor_source == "strategy_swing"
                or rule_type == StopRuleType.SWING_ANCHORED
            ):
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
                quantity_fraction=float(raw.get("quantity_fraction") or 0),
                stop=stop,
                profit_target_price=(
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
        if anchor_source != "strategy_swing" and rule_type != StopRuleType.SWING_ANCHORED:
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
        self._campaigns.assert_owner(assignment)
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

    async def on_order_group_update(self, snapshot: Any) -> None:
        assignment_id = str(getattr(snapshot, "assignment_id", "") or "")
        if not assignment_id or str(getattr(snapshot, "state", "")) != "filled":
            return
        for key, assignment in self._assignments.items():
            if assignment.assignment_id != assignment_id:
                continue
            state = dict(assignment.state)
            action = str(getattr(snapshot, "action", ""))
            if action in {"enter_long", "add_long", "enter_short", "add_short"}:
                status = AssignmentStatus.MANAGING
            elif action in {"exit", "take_profit", "cover", "reduce_short"}:
                if bool(getattr(snapshot, "reentry_after_fill", False)):
                    state["reentries"] = int(state.get("reentries") or 0) + 1
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


def _trigger_reference(
    rules: dict[str, Any],
    result: dict[str, Any],
    observation: StrategyObservation,
) -> tuple[str, float | None, float]:
    matched = set(dict(result.get("trigger") or {}).get("matched_groups") or [])
    trigger_stage = dict(rules.get("trigger") or {})
    for group in trigger_stage.get("rule_sets") or trigger_stage.get("groups") or []:
        if str(group.get("group_id") or "") not in matched:
            continue
        for condition in group.get("conditions") or []:
            if str(condition.get("comparator") or "") != "above_by_bps":
                continue
            source_id = str(condition.get("right_source_id") or "")
            value = _source_value(
                observation,
                source_id,
                str(condition.get("right_timeframe") or ""),
            )
            if value is not None:
                return source_id, float(value), float(condition.get("value") or 0)
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
        rule_sets = stage.get("rule_sets") or stage.get("groups") or []
        for group in rule_sets:
            if not bool(group.get("enabled", True)):
                continue
            group_id = str(group.get("rule_set_id") or group.get("group_id") or "")
            condition_results = [
                _condition_matches(dict(condition), observation)
                for condition in group.get("conditions") or []
                if bool(condition.get("enabled", True))
            ]
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
            "matched_groups": matched,
            "operator": operator,
            "passed": passed,
            "score": score,
        }
    return output


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
    left = _source_value(
        observation,
        str(condition.get("left_source_id") or ""),
        str(condition.get("left_timeframe") or ""),
    )
    if left is None:
        return False
    comparator = str(condition.get("comparator") or "")
    if comparator == "is_true":
        return bool(left)
    right_source_id = str(condition.get("right_source_id") or "")
    right = (
        _source_value(
            observation,
            right_source_id,
            str(condition.get("right_timeframe") or ""),
        )
        if right_source_id
        else condition.get("value")
    )
    if right is None:
        return False
    if comparator == "equals":
        return left == right
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
                if left_source is None:
                    raise ValueError(f"Entry rule condition {condition.get('condition_id')} has unknown left source")
                _validate_rule_timeframe(condition, "left_timeframe", left_source)
                right_source_id = str(condition.get("right_source_id") or "")
                if comparator == "above_by_bps" and not right_source_id:
                    raise ValueError(
                        f"Entry rule condition {condition.get('condition_id')} requires a target source"
                    )
                if right_source_id:
                    right_source = catalog.get(right_source_id)
                    if right_source is None:
                        raise ValueError(f"Entry rule condition {condition.get('condition_id')} has unknown right source")
                    _validate_rule_timeframe(condition, "right_timeframe", right_source)
                elif comparator != "is_true" and condition.get("value") is None:
                    raise ValueError(f"Entry rule condition {condition.get('condition_id')} requires a value")


def _validate_rule_timeframe(
    condition: dict[str, Any],
    key: str,
    source: dict[str, Any],
) -> None:
    timeframe = str(condition.get(key) or "")
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
        "summary": f"{label} supplied by {provider}; the configured timeframe is part of the rule contract.",
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
    structure_base = (
        observation.swing_low if side == "long" else observation.swing_high
    ) or reference or observation.price
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
        else min(structure_stop, volatility_stop)
        if side == "long"
        else max(structure_stop, volatility_stop)
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
    if not policy["enabled"] or not observation.market_open:
        return None
    if side == "short" or observation.upper_luld_price is None:
        return None
    target = observation.upper_luld_price * (1 - float(policy["buffer_bps"]) / 10_000)
    return round(target, 4) if target > observation.price else None


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {key: dict(value) if isinstance(value, dict) else value for key, value in base.items()}
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
