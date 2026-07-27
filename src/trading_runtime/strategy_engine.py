from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from src.trading_runtime.signals import StrategyEvaluation, StrategyIntent, StrategySignal
from src.market_engine.events import MarketEvent


STRATEGY_ID = "long-momentum-campaign"
STRATEGY_REVISION = 2


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
    flow_price_divergence_score: float = 0.0
    liquidity_dislocation_score: float = 0.0
    volatility: float = 0.0
    acceleration: float = 0.0
    upper_luld_price: float | None = None
    market_open: bool = True
    manual_entry_request: bool = False
    force_entry: bool = False
    source_signal_ids: tuple[str, ...] = ()

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
            "direction": "long_only",
            "parameters": parameters,
            "parameter_space": {
                "entry.breakout_timeframe": ["100ms", "1s", "5s", "10s"],
                "entry.breakout_reference": ["previous_close", "previous_high", "confirmed_swing_high", "bullish_choch"],
                "entry.breakout_buffer_bps": [0, 3, 5, 10],
                "entry.minimum_confirmation_score": [0.45, 0.55, 0.65, 0.75],
                "entry.qmd.minimum_score": [0.15, 0.30, 0.50],
                "entry.qmd.minimum_confidence": [0.35, 0.50, 0.70],
                "protection.stop.method": ["structure", "volatility", "hybrid"],
                "protection.stop.volatility_multiple": [0.75, 1.0, 1.25, 1.5, 2.0],
                "profit_pocket.trigger": ["acceleration_slowdown", "favorable_move_pct", "volatility_multiple"],
                "profit_pocket.quantity_fraction": [1.0],
                "reentry.cooldown_ms": [0, 500, 1000, 5000, 30000],
                "execution.entry_urgency": ["patient", "regular", "urgent", "very_urgent"],
                "execution.exit_urgency": ["urgent", "very_urgent"],
            },
            "taxonomy": {
                "schema_version": 2,
                "indicators": [
                    {"key": "flow_structure_composite", "timeframe": "100ms", "role": "confirmation", "required": False, "maximum_age_ms": 300, "weight": 0.4, "minimum_score": 0.3, "minimum_confidence": 0.5},
                    {"key": "vwap", "timeframe": "5s", "role": "confirmation", "required": False, "maximum_age_ms": 6000, "weight": 0.3},
                    {"key": "macd", "timeframe": "5s", "role": "confirmation", "required": False, "maximum_age_ms": 6000, "weight": 0.3},
                    {"key": "generic_structure", "timeframe": "1s", "role": "trigger", "required": True, "maximum_age_ms": 2000},
                ],
                "signals": [
                    {"key": "price_volume_expansion", "timeframe": "1s", "role": "trigger", "required": False, "maximum_age_ms": 2000, "minimum_score": 0.65},
                    {"key": "vwap_transition", "timeframe": "5s", "role": "trigger", "required": False, "maximum_age_ms": 6000, "minimum_score": 0.6},
                    {"key": "flow_price_divergence", "timeframe": "100ms", "role": "veto", "required": False, "maximum_age_ms": 500},
                    {"key": "liquidity_dislocation", "timeframe": "100ms", "role": "veto", "required": False, "maximum_age_ms": 500},
                    {"key": "company_news", "role": "trigger", "required": False, "maximum_age_ms": 60000, "minimum_score": 0.7},
                ],
                "allow_developing_inputs": False,
                "evaluation_trigger": "indicator_update",
                "evaluation_triggers": ["indicator_update", "signal_event", "bar_close", "manual", "position_event", "order_event"],
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
    return {
        "entry": {
            "breakout_timeframe": "1s",
            "breakout_reference": "confirmed_swing_high",
            "breakout_buffer_bps": 5.0,
            "minimum_confirmation_score": 0.55,
            "news_minimum_score": 0.7,
            "price_expansion_minimum_score": 0.65,
            "vwap_transition_minimum_score": 0.6,
            "qmd": {"minimum_score": 0.3, "minimum_confidence": 0.5, "weight": 0.4},
            "vwap": {"minimum_slope_bps_per_second": 0.0, "weight": 0.3},
            "macd": {"require_positive_histogram": True, "weight": 0.3},
            "veto": {"flow_price_divergence": 0.75, "liquidity_dislocation": 0.75},
        },
        "sizing": {"initial_quantity": 100.0, "add_fraction": 0.5, "maximum_position_quantity": 300.0},
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
            "time_in_force": "DAY",
            "outside_rth": False,
        },
    }


def resolve_long_momentum_parameters(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    parameters = _deep_merge(default_long_momentum_parameters(), dict(overrides or {}))
    entry = parameters["entry"]
    if entry["breakout_timeframe"] not in {"100ms", "1s", "5s", "10s", "30s", "1m", "5m"}:
        raise ValueError("Unsupported entry breakout timeframe")
    if entry["breakout_reference"] not in {"previous_close", "previous_high", "confirmed_swing_high", "bullish_choch"}:
        raise ValueError("Unsupported entry breakout reference")
    if not 0 <= float(entry["minimum_confirmation_score"]) <= 1:
        raise ValueError("Entry minimum confirmation score must be between 0 and 1")
    if parameters["protection"]["stop"]["method"] not in {"structure", "volatility", "hybrid"}:
        raise ValueError("Unsupported protective stop method")
    sizing = parameters["sizing"]
    if float(sizing["initial_quantity"]) <= 0 or float(sizing["maximum_position_quantity"]) <= 0:
        raise ValueError("Strategy quantities must be positive")
    if float(sizing["initial_quantity"]) > float(sizing["maximum_position_quantity"]):
        raise ValueError("Initial quantity cannot exceed maximum position quantity")
    if not 0 < float(sizing["add_fraction"]) <= 1:
        raise ValueError("Add fraction must be between 0 and 1")
    if float(parameters["profit_pocket"]["quantity_fraction"]) != 1:
        raise ValueError("Revision 2 profit pockets must close the full position before re-entry")
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
    if parameters["execution"]["time_in_force"] not in {"DAY", "GTC", "IOC", "OPG"}:
        raise ValueError("Unsupported strategy time in force")
    return parameters


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

        state["last_observed_at"] = observation.observed_at.isoformat()
        state["last_price"] = observation.price
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
        if assignment.status == AssignmentStatus.ENTRY_PENDING:
            return self._result(assignment, observation, "wait", "entry_fill_pending", 0.0, 1.0, state, AssignmentStatus.ENTRY_PENDING)
        if reentries > int(reentry["maximum_attempts"]):
            return self._result(assignment, observation, "wait", "maximum_reentries_reached", 0.0, 1.0, state, AssignmentStatus.COMPLETED)
        if reentries and not assignment.permissions.reenter:
            return self._result(assignment, observation, "wait", "reentry_not_authorized", 0.0, 1.0, state, AssignmentStatus.COMPLETED)
        if reentries and state.get("last_exit_at"):
            last_exit = datetime.fromisoformat(str(state["last_exit_at"]).replace("Z", "+00:00"))
            elapsed_ms = (observation.observed_at - last_exit).total_seconds() * 1000
            if elapsed_ms < float(reentry["cooldown_ms"]):
                return self._result(assignment, observation, "wait", "reentry_cooldown", 0.0, 1.0, state, AssignmentStatus.REENTRY_COOLDOWN)
        if not assignment.permissions.enter and not observation.force_entry:
            return self._result(assignment, observation, "wait", "entry_not_authorized", 0.0, 1.0, state, AssignmentStatus.WATCHING)
        if not observation.market_open:
            return self._result(assignment, observation, "wait", "market_not_open", 0.0, 1.0, state, AssignmentStatus.WATCHING)

        entry = parameters["entry"]
        reference_name = str(entry["breakout_reference"])
        reference = _entry_reference(observation, reference_name)
        buffer = observation.price * float(entry["breakout_buffer_bps"]) / 10_000
        structure_break = bool(reference and observation.price >= reference + buffer)
        bullish_choch = observation.structure_event == "choch" and observation.structure_direction == "bullish"
        triggers = {
            "manual_entry_request": observation.manual_entry_request,
            "force_entry": observation.force_entry,
            "news": observation.news_score >= float(entry["news_minimum_score"]),
            "price_volume_expansion": observation.price_volume_expansion_score >= float(entry["price_expansion_minimum_score"]),
            "vwap_transition": observation.vwap_transition_score >= float(entry["vwap_transition_minimum_score"]),
            "structure_break": structure_break,
            "bullish_choch": bullish_choch,
        }
        triggered = [key for key, value in triggers.items() if value]
        confirmation_score, confirmation = _confirmation_score(observation, entry)
        vetoes = []
        if observation.flow_price_divergence_score >= float(entry["veto"]["flow_price_divergence"]):
            vetoes.append("flow_price_divergence")
        if observation.liquidity_dislocation_score >= float(entry["veto"]["liquidity_dislocation"]):
            vetoes.append("liquidity_dislocation")
        can_enter = bool(triggered) and not vetoes and (
            observation.force_entry or confirmation_score >= float(entry["minimum_confirmation_score"])
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
                metadata={"triggers": triggered, "vetoes": vetoes, "confirmation": confirmation},
            )

        stop = _initial_stop(observation, parameters, reference)
        quantity = min(
            float(parameters["sizing"]["initial_quantity"]),
            float(parameters["sizing"]["maximum_position_quantity"]),
        )
        target = _luld_target(observation, parameters)
        state.update(
            {
                "breakout_level": reference,
                "entry_reference_price": observation.price,
                "initial_stop": stop,
                "active_stop": stop,
                "high_water_price": observation.price,
                "adds": 0,
                "profit_takes": 0,
                "entries": int(state.get("entries") or 0) + 1,
            }
        )
        return self._result(
            assignment,
            observation,
            "enter_long",
            "entry_confirmed",
            confirmation_score,
            _confirmation_confidence(observation),
            state,
            AssignmentStatus.ENTRY_PENDING,
            quantity=quantity,
            invalidation_price=stop,
            profit_target_price=target,
            trailing_amount=_trailing_amount(observation, parameters),
            metadata={"triggers": triggered, "confirmation": confirmation, "reference": reference_name},
        )

    def _evaluate_position(
        self,
        assignment: StrategyAssignment,
        observation: StrategyObservation,
        parameters: dict[str, Any],
        state: dict[str, Any],
    ) -> StrategyEngineResult:
        state["high_water_price"] = max(float(state.get("high_water_price") or observation.price), observation.price)
        state["active_stop"] = _ratcheted_stop(observation, parameters, state)
        stop = float(state["active_stop"])
        breakout_level = float(state.get("breakout_level") or 0)
        breakout_buffer = observation.price * float(parameters["entry"]["breakout_buffer_bps"]) / 10_000
        failed_breakout = bool(
            parameters["final_exit"]["exit_on_failed_breakout"]
            and breakout_level > 0
            and observation.price < breakout_level - breakout_buffer
        )
        bearish_qmd = (
            observation.qmd_score <= float(parameters["final_exit"]["qmd_score"])
            and observation.qmd_confidence >= float(parameters["final_exit"]["qmd_confidence"])
        )
        bearish_macd = (
            observation.macd_histogram is not None
            and observation.macd_histogram < 0
            and observation.macd_line is not None
            and observation.macd_signal is not None
            and observation.macd_line < observation.macd_signal
        )
        final_signal_exit = bearish_qmd and (
            bearish_macd or not parameters["final_exit"]["require_macd_bearish"]
        )
        if assignment.permissions.exit and (observation.price <= stop or failed_breakout or final_signal_exit):
            reason = "protective_stop" if observation.price <= stop else "failed_breakout" if failed_breakout else "bearish_qmd_macd"
            state["last_exit_reason"] = reason
            state["last_exit_at"] = observation.observed_at.isoformat()
            state["reentries"] = int(state.get("reentries") or 0) + 1
            next_status = AssignmentStatus.COMPLETED if state.get("disable_after_exit") or not assignment.permissions.reenter else AssignmentStatus.REENTRY_COOLDOWN
            return self._result(
                assignment, observation, "exit", reason, observation.qmd_score,
                max(observation.qmd_confidence, 0.5), state, next_status,
                quantity=observation.position_quantity, invalidation_price=stop,
                trailing_amount=_trailing_amount(observation, parameters),
            )

        add = parameters["add"]
        bullish_choch = observation.structure_event == "choch" and observation.structure_direction == "bullish"
        confirmation_score, confirmation = _confirmation_score(observation, parameters["entry"])
        adds = int(state.get("adds") or 0)
        maximum_qty = float(parameters["sizing"]["maximum_position_quantity"])
        add_qty = min(
            float(parameters["sizing"]["initial_quantity"]) * float(parameters["sizing"]["add_fraction"]),
            max(0.0, maximum_qty - observation.position_quantity),
        )
        if (
            assignment.permissions.add
            and add["enabled"]
            and bullish_choch
            and adds < int(add["maximum_adds"])
            and add_qty > 0
            and confirmation_score >= float(parameters["entry"]["minimum_confirmation_score"])
        ):
            state["adds"] = adds + 1
            return self._result(
                assignment, observation, "add_long", "bullish_choch_confirmed",
                confirmation_score, _confirmation_confidence(observation), state,
                AssignmentStatus.MANAGING, quantity=add_qty, invalidation_price=stop,
                trailing_amount=_trailing_amount(observation, parameters),
                metadata={"confirmation": confirmation},
            )

        pocket = parameters["profit_pocket"]
        entry_price = float(state.get("entry_reference_price") or observation.average_price or observation.price)
        gain_pct = (observation.price / entry_price - 1) * 100 if entry_price > 0 else 0
        previous_acceleration = float(state.get("last_acceleration") or 0)
        threshold = float(pocket["acceleration_slowdown_threshold"])
        slowdown = previous_acceleration > threshold and observation.acceleration <= threshold
        favorable_pct = gain_pct >= float(pocket["minimum_gain_pct"])
        favorable_volatility = (
            observation.volatility > 0
            and observation.price - entry_price >= observation.volatility * float(pocket["volatility_multiple"])
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
                assignment, observation, "exit", "profit_pocket",
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
        metadata: dict[str, Any] | None = None,
    ) -> StrategyEngineResult:
        event_id = str(uuid4())
        signal = StrategySignal(
            signal_id=event_id,
            signal_type=reason,
            ticker=observation.ticker.upper(),
            event_time=observation.observed_at,
            action=action,  # type: ignore[arg-type]
            direction="bearish" if action in {"reduce_long", "take_profit", "exit"} else "bullish" if action in {"enter_long", "add_long"} else "neutral",
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
        if action in {"enter_long", "add_long", "reduce_long", "take_profit", "exit"}:
            intents = (
                StrategyIntent(
                    intent_id=event_id,
                    ticker=observation.ticker.upper(),
                    event_time=observation.observed_at,
                    action=action,  # type: ignore[arg-type]
                    quantity=quantity,
                    reference_price=observation.price,
                    invalidation_price=invalidation_price,
                    profit_target_price=profit_target_price,
                    trailing_amount=trailing_amount,
                    urgency=str(assignment.parameters.get("execution", {}).get("entry_urgency") or "urgent") if action in {"enter_long", "add_long"} else str(assignment.parameters.get("execution", {}).get("exit_urgency") or "very_urgent"),  # type: ignore[arg-type]
                    time_in_force=str(assignment.parameters.get("execution", {}).get("time_in_force") or "DAY"),
                    outside_rth=bool(assignment.parameters.get("execution", {}).get("outside_rth", False)),
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


class AssignedLongMomentumStrategy:
    """Runtime strategy adapter; enriched observations are its only decision input."""

    strategy_id = STRATEGY_ID
    revision = STRATEGY_REVISION
    automatic = True

    def __init__(self, assignments: list[StrategyAssignment]) -> None:
        self._assignments = {
            (assignment.account_id, assignment.ticker.upper()): assignment
            for assignment in assignments
        }
        self._engine = LongMomentumStrategyEngine()

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
        result = self._engine.evaluate(assignment, observation)
        self._assignments[key] = StrategyAssignment(
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
        return result.evaluation

    def assignments(self) -> tuple[StrategyAssignment, ...]:
        return tuple(self._assignments.values())

    async def on_order_group_update(self, snapshot: Any) -> None:
        assignment_id = str(getattr(snapshot, "assignment_id", "") or "")
        if not assignment_id or str(getattr(snapshot, "state", "")) != "filled":
            return
        for key, assignment in self._assignments.items():
            if assignment.assignment_id != assignment_id:
                continue
            state = dict(assignment.state)
            action = str(getattr(snapshot, "action", ""))
            if action in {"enter_long", "add_long"}:
                status = AssignmentStatus.MANAGING
            elif action in {"exit", "take_profit"}:
                if bool(getattr(snapshot, "reentry_after_fill", False)):
                    state["reentries"] = int(state.get("reentries") or 0) + 1
                    status = AssignmentStatus.REENTRY_COOLDOWN
                else:
                    status = AssignmentStatus.COMPLETED
            else:
                return
            self._assignments[key] = StrategyAssignment(
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
            return


def _entry_reference(observation: StrategyObservation, name: str) -> float | None:
    if name == "previous_close":
        return observation.previous_close
    if name == "previous_high":
        return observation.previous_high
    if name == "confirmed_swing_high":
        return observation.swing_high
    if name == "bullish_choch" and observation.structure_event == "choch":
        return observation.swing_high or observation.previous_high
    return observation.swing_high or observation.previous_high or observation.previous_close


def _confirmation_score(observation: StrategyObservation, entry: dict[str, Any]) -> tuple[float, dict[str, float]]:
    qmd = 1.0 if (
        observation.qmd_score >= float(entry["qmd"]["minimum_score"])
        and observation.qmd_confidence >= float(entry["qmd"]["minimum_confidence"])
    ) else 0.0
    vwap = 1.0 if (
        observation.vwap is not None
        and observation.price >= observation.vwap
        and observation.vwap_slope_bps_per_second >= float(entry["vwap"]["minimum_slope_bps_per_second"])
    ) else 0.0
    macd = 1.0 if (
        observation.macd_line is not None
        and observation.macd_signal is not None
        and observation.macd_line >= observation.macd_signal
        and (not entry["macd"]["require_positive_histogram"] or float(observation.macd_histogram or 0) > 0)
    ) else 0.0
    evidence = {"qmd": qmd, "vwap": vwap, "macd": macd}
    weights = {
        "qmd": float(entry["qmd"]["weight"]),
        "vwap": float(entry["vwap"]["weight"]),
        "macd": float(entry["macd"]["weight"]),
    }
    denominator = sum(weights.values()) or 1.0
    return sum(evidence[key] * weights[key] for key in evidence) / denominator, evidence


def _confirmation_confidence(observation: StrategyObservation) -> float:
    return max(0.0, min(1.0, (observation.qmd_confidence + (0.75 if observation.vwap is not None else 0) + (0.75 if observation.macd_line is not None else 0)) / 3))


def _initial_stop(observation: StrategyObservation, parameters: dict[str, Any], reference: float | None) -> float:
    stop = parameters["protection"]["stop"]
    structure_base = observation.swing_low or reference or observation.price
    structure_stop = structure_base * (1 - float(stop["structure_buffer_bps"]) / 10_000)
    volatility = observation.volatility if observation.volatility > 0 else observation.price * 0.002
    volatility_stop = observation.price - volatility * float(stop["volatility_multiple"])
    method = stop["method"]
    selected = structure_stop if method == "structure" else volatility_stop if method == "volatility" else min(structure_stop, volatility_stop)
    maximum_risk_floor = observation.price * (1 - float(stop["maximum_risk_pct"]) / 100)
    return round(max(maximum_risk_floor, min(selected, observation.price * 0.9999)), 4)


def _trailing_amount(observation: StrategyObservation, parameters: dict[str, Any]) -> float | None:
    trailing = parameters["protection"]["trailing"]
    if not trailing["enabled"]:
        return None
    volatility_distance = observation.volatility * float(trailing["distance_volatility_multiple"])
    minimum_distance = observation.price * float(trailing["minimum_distance_bps"]) / 10_000
    return round(max(volatility_distance, minimum_distance), 4)


def _ratcheted_stop(observation: StrategyObservation, parameters: dict[str, Any], state: dict[str, Any]) -> float:
    current = float(state.get("active_stop") or state.get("initial_stop") or 0)
    entry = float(state.get("entry_reference_price") or observation.average_price or observation.price)
    gain_pct = (observation.price / entry - 1) * 100 if entry > 0 else 0
    trailing = parameters["protection"]["trailing"]
    if not trailing["enabled"] or gain_pct < float(trailing["activation_gain_pct"]):
        return current
    distance = _trailing_amount(observation, parameters) or 0
    return round(max(current, float(state["high_water_price"]) - distance), 4)


def _luld_target(observation: StrategyObservation, parameters: dict[str, Any]) -> float | None:
    policy = parameters["protection"]["luld_profit_target"]
    if not policy["enabled"] or not observation.market_open:
        return None
    if observation.upper_luld_price is None:
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
