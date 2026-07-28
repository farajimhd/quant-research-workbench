from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping


class ExecutionPolicyName(StrEnum):
    PASSIVE = "passive"
    MIDPOINT = "midpoint"
    ADAPTIVE_PATIENT = "adaptive_patient"
    ADAPTIVE_REGULAR = "adaptive_regular"
    ADAPTIVE_URGENT = "adaptive_urgent"
    ADAPTIVE_VERY_URGENT = "adaptive_very_urgent"
    IMMEDIATE_WITH_LIMIT = "immediate_with_limit"
    IBKR_NATIVE_ADAPTIVE = "ibkr_native_adaptive"
    CANCEL_IF_NOT_FILLED = "cancel_if_not_filled"


class PartialFillPolicy(StrEnum):
    COMPLETE_REMAINDER = "complete_remainder"
    ACCEPT_PARTIAL = "accept_partial"
    CANCEL_REMAINDER = "cancel_remainder"


class StopRuleType(StrEnum):
    FIXED_PRICE = "fixed_price"
    FIXED_PERCENT = "fixed_percent"
    FIXED_BPS = "fixed_bps"
    FIXED_CASH_RISK = "fixed_cash_risk"
    SWING_ANCHORED = "swing_anchored"
    VOLATILITY = "volatility"
    HYBRID = "hybrid"
    BREAKEVEN = "breakeven"
    CATASTROPHIC = "catastrophic"


class StopOrderType(StrEnum):
    STOP = "STP"
    STOP_LIMIT = "STOP_LIMIT"


class TrailingRuleType(StrEnum):
    NONE = "none"
    BROKER_AMOUNT = "broker_amount"
    BROKER_PERCENT = "broker_percent"
    VOLATILITY_TRAIL = "volatility_trail"
    SWING_TRAIL = "swing_trail"
    CHANDELIER = "chandelier"
    BREAKEVEN_THEN_TRAIL = "breakeven_then_trail"
    PROFIT_LOCK_R = "profit_lock_r"
    TIME_TIGHTENING = "time_tightening"


class ProfitPocketTransition(StrEnum):
    KEEP_EXISTING = "keep_existing"
    MOVE_TO_BREAKEVEN = "move_to_breakeven"
    LOCK_PROFIT_PRICE = "lock_profit_price"
    START_BROKER_TRAIL = "start_broker_trail"
    START_VOLATILITY_TRAIL = "start_volatility_trail"
    START_SWING_TRAIL = "start_swing_trail"
    TIGHTEN_EXISTING = "tighten_existing"
    REPLAN_REMAINING_SLICES = "replan_remaining_slices"
    FULL_EXIT_AND_OPTIONAL_REENTRY = "full_exit_and_optional_reentry"


class AddProtectionPolicy(StrEnum):
    INDEPENDENT_SLICE = "independent_slice"
    INHERIT_POSITION_STOP = "inherit_position_stop"
    REBASE_ALL = "rebase_all"
    TIGHTEN_ONLY = "tighten_only"
    PRESERVE_EXISTING = "preserve_existing"


@dataclass(frozen=True, slots=True)
class ExecutionEnvelope:
    maximum_buy_price: float | None = None
    minimum_sell_price: float | None = None
    deadline_ms: int = 750
    maximum_reprices: int = 4
    minimum_reprice_interval_ms: int = 50

    def __post_init__(self) -> None:
        if self.maximum_buy_price is not None and self.maximum_buy_price <= 0:
            raise ValueError("maximum_buy_price must be positive")
        if self.minimum_sell_price is not None and self.minimum_sell_price <= 0:
            raise ValueError("minimum_sell_price must be positive")
        if self.deadline_ms < 0 or self.maximum_reprices < 0:
            raise ValueError("execution deadline and maximum reprices cannot be negative")
        if self.minimum_reprice_interval_ms < 0:
            raise ValueError("minimum reprice interval cannot be negative")

    def bound(self, side: str, price: float) -> float:
        if side.upper() == "BUY" and self.maximum_buy_price is not None:
            return min(price, self.maximum_buy_price)
        if side.upper() == "SELL" and self.minimum_sell_price is not None:
            return max(price, self.minimum_sell_price)
        return price

    def permits(self, side: str, price: float) -> bool:
        return math.isclose(self.bound(side, price), price, rel_tol=0, abs_tol=1e-12)


@dataclass(frozen=True, slots=True)
class ExecutionPolicy:
    policy_id: str = "adaptive-regular"
    revision: int = 1
    name: ExecutionPolicyName = ExecutionPolicyName.ADAPTIVE_REGULAR
    envelope: ExecutionEnvelope = field(default_factory=ExecutionEnvelope)
    partial_fill_policy: PartialFillPolicy = PartialFillPolicy.COMPLETE_REMAINDER
    quote_source: str = "qmd"

    def __post_init__(self) -> None:
        if not self.policy_id or self.revision < 1:
            raise ValueError("execution policy identity and revision are required")
        if self.quote_source not in {"qmd", "ibkr", "simulated"}:
            raise ValueError("execution quote source must be qmd, ibkr, or simulated")

    @property
    def identity(self) -> str:
        return f"{self.policy_id}@{self.revision}"


@dataclass(frozen=True, slots=True)
class StructuralAnchor:
    observation_id: str
    price: float
    confirmed_at: datetime
    timeframe: str = ""
    ordinal: str = "most_recent"

    def __post_init__(self) -> None:
        if not self.observation_id or self.price <= 0:
            raise ValueError("structural anchors require an observation id and positive price")
        if self.confirmed_at.tzinfo is None:
            raise ValueError("structural anchor confirmation time must include a timezone")


@dataclass(frozen=True, slots=True)
class StopRule:
    rule_type: StopRuleType = StopRuleType.FIXED_PRICE
    order_type: StopOrderType = StopOrderType.STOP
    price: float | None = None
    distance_percent: float | None = None
    distance_bps: float | None = None
    maximum_cash_risk: float | None = None
    volatility_multiple: float | None = None
    buffer_bps: float = 0.0
    anchor: StructuralAnchor | None = None
    stop_limit_offset_bps: float | None = None

    def __post_init__(self) -> None:
        for value in (
            self.price,
            self.distance_percent,
            self.distance_bps,
            self.maximum_cash_risk,
            self.volatility_multiple,
        ):
            if value is not None and value < 0:
                raise ValueError("stop rule values cannot be negative")
        if self.rule_type in {StopRuleType.FIXED_PRICE, StopRuleType.CATASTROPHIC} and not self.price:
            raise ValueError(f"{self.rule_type.value} stop requires a positive price")
        if self.rule_type == StopRuleType.SWING_ANCHORED and self.anchor is None:
            raise ValueError("swing-anchored stop requires a causal structural anchor")
        if self.order_type == StopOrderType.STOP_LIMIT and (
            self.stop_limit_offset_bps is None or self.stop_limit_offset_bps <= 0
        ):
            raise ValueError("stop-limit protection requires a positive limit offset")

    def resolve(
        self,
        *,
        reference_price: float,
        side: str,
        quantity: float,
        volatility: float = 0.0,
    ) -> float:
        long_position = side.lower() == "long"
        direction = -1.0 if long_position else 1.0
        if self.rule_type in {StopRuleType.FIXED_PRICE, StopRuleType.CATASTROPHIC}:
            result = float(self.price or 0)
        elif self.rule_type == StopRuleType.SWING_ANCHORED:
            result = float(self.anchor.price if self.anchor else 0)
            result *= 1.0 + direction * self.buffer_bps / 10_000.0
        elif self.rule_type == StopRuleType.FIXED_PERCENT:
            result = reference_price * (1.0 + direction * float(self.distance_percent or 0) / 100.0)
        elif self.rule_type == StopRuleType.FIXED_BPS:
            result = reference_price * (1.0 + direction * float(self.distance_bps or 0) / 10_000.0)
        elif self.rule_type == StopRuleType.FIXED_CASH_RISK:
            distance = float(self.maximum_cash_risk or 0) / max(quantity, 1e-12)
            result = reference_price + direction * distance
        elif self.rule_type in {StopRuleType.VOLATILITY, StopRuleType.HYBRID}:
            result = reference_price + direction * volatility * float(self.volatility_multiple or 0)
            if self.rule_type == StopRuleType.HYBRID and self.anchor is not None:
                structural = self.anchor.price * (1.0 + direction * self.buffer_bps / 10_000.0)
                result = max(result, structural) if long_position else min(result, structural)
        else:
            result = reference_price
        if result <= 0:
            raise ValueError("resolved stop price must be positive")
        if long_position and result >= reference_price:
            raise ValueError("long protective stop must be below the reference price")
        if not long_position and result <= reference_price:
            raise ValueError("short protective stop must be above the reference price")
        return result


@dataclass(frozen=True, slots=True)
class TrailingRule:
    rule_type: TrailingRuleType = TrailingRuleType.NONE
    amount: float | None = None
    percent: float | None = None
    volatility_multiple: float | None = None
    activation_gain_percent: float = 0.0
    breakeven_buffer_bps: float = 0.0
    structural_timeframe: str = ""

    def __post_init__(self) -> None:
        values = (self.amount, self.percent, self.volatility_multiple, self.activation_gain_percent)
        if any(value is not None and value < 0 for value in values):
            raise ValueError("trailing rule values cannot be negative")
        if self.rule_type == TrailingRuleType.BROKER_AMOUNT and not self.amount:
            raise ValueError("broker amount trail requires a positive amount")
        if self.rule_type == TrailingRuleType.BROKER_PERCENT and not self.percent:
            raise ValueError("broker percent trail requires a positive percent")


@dataclass(frozen=True, slots=True)
class ProtectionSlice:
    slice_id: str
    quantity_fraction: float
    stop: StopRule
    profit_target_price: float | None = None
    trailing: TrailingRule = field(default_factory=TrailingRule)

    def __post_init__(self) -> None:
        if not self.slice_id or not 0 < self.quantity_fraction <= 1:
            raise ValueError("protection slice identity and fraction are required")
        if self.profit_target_price is not None and self.profit_target_price <= 0:
            raise ValueError("profit target must be positive")


@dataclass(frozen=True, slots=True)
class ProtectionProfile:
    profile_id: str
    revision: int
    slices: tuple[ProtectionSlice, ...]
    add_policy: AddProtectionPolicy = AddProtectionPolicy.INDEPENDENT_SLICE
    profit_pocket_transition: ProfitPocketTransition = ProfitPocketTransition.KEEP_EXISTING
    mandatory_catastrophic_backstop: bool = True
    emergency_repair_deadline_ms: int = 500

    def __post_init__(self) -> None:
        if not self.profile_id or self.revision < 1 or not self.slices:
            raise ValueError("protection profile identity, revision, and slices are required")
        total = sum(item.quantity_fraction for item in self.slices)
        if not math.isclose(total, 1.0, abs_tol=1e-9):
            raise ValueError("protection slice fractions must total exactly one")
        if len({item.slice_id for item in self.slices}) != len(self.slices):
            raise ValueError("protection slice ids must be unique")
        if self.emergency_repair_deadline_ms < 1:
            raise ValueError("protection repair deadline must be positive")

    @property
    def identity(self) -> str:
        return f"{self.profile_id}@{self.revision}"

    def payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ExecutionMarketSnapshot:
    ticker: str
    bid: float
    ask: float
    tick_size: float
    observed_at: datetime
    source: str
    volatility: float = 0.0
    upper_price_band: float | None = None
    lower_price_band: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.ticker or self.bid <= 0 or self.ask <= 0 or self.ask < self.bid:
            raise ValueError("execution market snapshot requires a positive non-crossed quote")
        if self.tick_size <= 0 or self.observed_at.tzinfo is None or not self.source:
            raise ValueError("execution market snapshot requires tick size, source, and timezone")

    @property
    def midpoint(self) -> float:
        return (self.bid + self.ask) / 2.0


class ExecutionMarketDataProvider:
    """In-memory, mode-independent authority for execution-time observations."""

    def __init__(self) -> None:
        self._snapshots: dict[str, ExecutionMarketSnapshot] = {}

    def update(self, snapshot: ExecutionMarketSnapshot) -> None:
        key = snapshot.ticker.upper()
        previous = self._snapshots.get(key)
        if previous is not None and snapshot.observed_at < previous.observed_at:
            return
        self._snapshots[key] = snapshot

    def snapshot(self, ticker: str) -> ExecutionMarketSnapshot | None:
        return self._snapshots.get(ticker.upper())


def legacy_execution_policy(
    *,
    urgency: str,
    reference_price: float,
    metadata: Mapping[str, Any],
) -> ExecutionPolicy:
    normalized = urgency.lower()
    name = {
        "patient": ExecutionPolicyName.ADAPTIVE_PATIENT,
        "passive_limit": ExecutionPolicyName.PASSIVE,
        "regular": ExecutionPolicyName.ADAPTIVE_REGULAR,
        "urgent": ExecutionPolicyName.ADAPTIVE_URGENT,
        "aggressive_limit": ExecutionPolicyName.ADAPTIVE_URGENT,
        "market": ExecutionPolicyName.ADAPTIVE_VERY_URGENT,
        "very_urgent": ExecutionPolicyName.ADAPTIVE_VERY_URGENT,
    }.get(normalized, ExecutionPolicyName.ADAPTIVE_REGULAR)
    maximum_buy = metadata.get("maximum_buy_price")
    minimum_sell = metadata.get("minimum_sell_price")
    return ExecutionPolicy(
        policy_id=f"legacy-{name.value}",
        name=name,
        envelope=ExecutionEnvelope(
            maximum_buy_price=float(maximum_buy) if maximum_buy else None,
            minimum_sell_price=float(minimum_sell) if minimum_sell else None,
            deadline_ms=int(metadata.get("execution_deadline_ms") or 750),
        ),
        quote_source=str(metadata.get("execution_quote_source") or "qmd"),
    )


def execution_policy_from_payload(payload: Mapping[str, Any]) -> ExecutionPolicy:
    envelope_raw = payload.get("envelope") or {}
    return ExecutionPolicy(
        policy_id=str(payload.get("policy_id") or "recovered"),
        revision=int(payload.get("revision") or 1),
        name=ExecutionPolicyName(str(payload.get("name") or ExecutionPolicyName.ADAPTIVE_REGULAR)),
        envelope=ExecutionEnvelope(**dict(envelope_raw)),
        partial_fill_policy=PartialFillPolicy(
            str(payload.get("partial_fill_policy") or PartialFillPolicy.COMPLETE_REMAINDER)
        ),
        quote_source=str(payload.get("quote_source") or "qmd"),
    )


def protection_profile_from_payload(payload: Mapping[str, Any]) -> ProtectionProfile:
    slices: list[ProtectionSlice] = []
    for raw in payload.get("slices") or ():
        stop_raw = dict(raw.get("stop") or {})
        anchor_raw = stop_raw.get("anchor")
        if anchor_raw:
            stop_raw["anchor"] = StructuralAnchor(
                observation_id=str(anchor_raw["observation_id"]),
                price=float(anchor_raw["price"]),
                confirmed_at=_aware(anchor_raw["confirmed_at"]),
                timeframe=str(anchor_raw.get("timeframe") or ""),
                ordinal=str(anchor_raw.get("ordinal") or "most_recent"),
            )
        stop_raw["rule_type"] = StopRuleType(str(stop_raw.get("rule_type") or StopRuleType.FIXED_PRICE))
        stop_raw["order_type"] = StopOrderType(str(stop_raw.get("order_type") or StopOrderType.STOP))
        trailing_raw = dict(raw.get("trailing") or {})
        trailing_raw["rule_type"] = TrailingRuleType(
            str(trailing_raw.get("rule_type") or TrailingRuleType.NONE)
        )
        slices.append(
            ProtectionSlice(
                slice_id=str(raw["slice_id"]),
                quantity_fraction=float(raw["quantity_fraction"]),
                stop=StopRule(**stop_raw),
                profit_target_price=(
                    float(raw["profit_target_price"])
                    if raw.get("profit_target_price") is not None
                    else None
                ),
                trailing=TrailingRule(**trailing_raw),
            )
        )
    return ProtectionProfile(
        profile_id=str(payload.get("profile_id") or "recovered"),
        revision=int(payload.get("revision") or 1),
        slices=tuple(slices),
        add_policy=AddProtectionPolicy(
            str(payload.get("add_policy") or AddProtectionPolicy.INDEPENDENT_SLICE)
        ),
        profit_pocket_transition=ProfitPocketTransition(
            str(payload.get("profit_pocket_transition") or ProfitPocketTransition.KEEP_EXISTING)
        ),
        mandatory_catastrophic_backstop=bool(
            payload.get("mandatory_catastrophic_backstop", True)
        ),
        emergency_repair_deadline_ms=int(payload.get("emergency_repair_deadline_ms") or 500),
    )


def _aware(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("recovered policy timestamps must include a timezone")
    return parsed
