"""Scalar, normalized projection of strategy intent and protection slices.

This is a staged contract, not yet a journal writer. The live strategy still
emits open-ended metadata; until that evidence has named typed families this
projection rejects it and the runtime cutover remains closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from src.trading_runtime.signals import StrategyIntent


_DECIMAL18 = Decimal("0.000000000000000001")
_MAX_DECIMAL18 = Decimal("100000000000000000000")


def _number(value: float | Decimal | None) -> str | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Strategy intent number cannot fit Decimal(38, 18)") from exc
    if not number.is_finite():
        raise ValueError("Strategy intent contains a non-finite number")
    try:
        exact = number.quantize(_DECIMAL18)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Strategy intent number cannot fit Decimal(38, 18)") from exc
    if number != exact or abs(number) >= _MAX_DECIMAL18:
        raise ValueError("Strategy intent number cannot fit Decimal(38, 18) losslessly")
    return format(number, "f")


def _instant(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("Strategy intent timestamp must include a timezone")
    return value.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class ProjectedIntent:
    core: dict[str, Any]
    protection_slices: tuple[dict[str, Any], ...]


def project_strategy_intent(intent: StrategyIntent) -> ProjectedIntent:
    """Flatten every declared intent/policy field; refuse arbitrary metadata."""
    if intent.metadata:
        raise ValueError("Strategy intent metadata lacks a normalized typed contract")
    capital = intent.capital_request
    policy = intent.execution_policy
    envelope = policy.envelope if policy is not None else None
    protection = intent.protection_profile
    core = {
        "intent_id": intent.intent_id,
        "ticker": intent.ticker.upper(),
        "event_time": _instant(intent.event_time),
        "action": intent.action,
        "quantity": _number(intent.quantity),
        "reference_price": _number(intent.reference_price),
        "schema_version": intent.schema_version,
        "invalidation_price": _number(intent.invalidation_price),
        "profit_target_price": _number(intent.profit_target_price),
        "trailing_amount": _number(intent.trailing_amount),
        "urgency": intent.urgency,
        "time_in_force": intent.time_in_force,
        "outside_rth": int(intent.outside_rth),
        "reason": intent.reason,
        "capital_mode": capital.mode if capital else None,
        "capital_value": _number(capital.value) if capital else None,
        "capital_minimum_quantity": _number(capital.minimum_quantity) if capital else None,
        "capital_maximum_quantity": _number(capital.maximum_quantity) if capital else None,
        "capital_allow_replacement": int(capital.allow_replacement) if capital else None,
        "execution_policy_id": policy.policy_id if policy else None,
        "execution_policy_revision": policy.revision if policy else None,
        "execution_policy_name": policy.name.value if policy else None,
        "execution_partial_fill_policy": policy.partial_fill_policy.value if policy else None,
        "execution_quote_source": policy.quote_source if policy else None,
        "execution_maximum_buy_price": _number(envelope.maximum_buy_price) if envelope else None,
        "execution_minimum_sell_price": _number(envelope.minimum_sell_price) if envelope else None,
        "execution_deadline_ms": envelope.deadline_ms if envelope else None,
        "execution_maximum_reprices": envelope.maximum_reprices if envelope else None,
        "execution_minimum_reprice_interval_ms": (
            envelope.minimum_reprice_interval_ms if envelope else None
        ),
        "execution_persist_until_cancelled": (
            int(envelope.persist_until_cancelled) if envelope else None
        ),
        "protection_profile_id": protection.profile_id if protection else None,
        "protection_profile_revision": protection.revision if protection else None,
        "protection_add_policy": protection.add_policy.value if protection else None,
        "protection_profit_pocket_transition": (
            protection.profit_pocket_transition.value if protection else None
        ),
        "protection_mandatory_catastrophic_backstop": (
            int(protection.mandatory_catastrophic_backstop) if protection else None
        ),
        "protection_emergency_repair_deadline_ms": (
            protection.emergency_repair_deadline_ms if protection else None
        ),
        "protection_slice_count": len(protection.slices) if protection else 0,
    }
    slices = []
    for ordinal, item in enumerate(protection.slices if protection else ()):
        stop = item.stop
        trail = item.trailing
        anchor = stop.anchor
        slices.append({
            "intent_id": intent.intent_id,
            "ordinal": ordinal,
            "slice_id": item.slice_id,
            "quantity_fraction": _number(item.quantity_fraction),
            "profit_target_price": _number(item.profit_target_price),
            "inherit_profit_target": int(item.inherit_profit_target),
            "stop_rule_type": stop.rule_type.value,
            "stop_order_type": stop.order_type.value,
            "stop_price": _number(stop.price),
            "stop_distance_percent": _number(stop.distance_percent),
            "stop_distance_bps": _number(stop.distance_bps),
            "stop_maximum_cash_risk": _number(stop.maximum_cash_risk),
            "stop_volatility_multiple": _number(stop.volatility_multiple),
            "stop_buffer_bps": _number(stop.buffer_bps),
            "stop_limit_offset_bps": _number(stop.stop_limit_offset_bps),
            "anchor_observation_id": anchor.observation_id if anchor else None,
            "anchor_price": _number(anchor.price) if anchor else None,
            "anchor_confirmed_at": _instant(anchor.confirmed_at) if anchor else None,
            "anchor_timeframe": anchor.timeframe if anchor else None,
            "anchor_ordinal": anchor.ordinal if anchor else None,
            "trailing_rule_type": trail.rule_type.value,
            "trailing_amount": _number(trail.amount),
            "trailing_percent": _number(trail.percent),
            "trailing_volatility_multiple": _number(trail.volatility_multiple),
            "trailing_activation_gain_percent": _number(trail.activation_gain_percent),
            "trailing_breakeven_buffer_bps": _number(trail.breakeven_buffer_bps),
            "trailing_structural_timeframe": trail.structural_timeframe,
        })
    return ProjectedIntent(core, tuple(slices))
