"""Scalar, normalized projection of strategy intent and protection slices.

This is a staged contract, not yet a journal writer. The live strategy still
emits open-ended metadata; until that evidence has named typed families this
projection rejects it and the runtime cutover remains closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import re
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from src.trading_runtime.arte_journal_writer import (
    CommittedPrefix, TypedJournalBatch, _CONTRACTS, _canonical_typed_content,
    _committed_batch_filter, _literal, _rows,
)
from src.trading_runtime.execution_policies import (
    execution_policy_from_payload, protection_profile_from_payload,
)
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.signals import CapitalRequest, StrategyIntent


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
    return format(exact, "f")


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
        "ticker": intent.ticker,
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


def restore_strategy_intent(rows: ProjectedIntent) -> StrategyIntent:
    """Rehydrate only a complete canonical projection, never infer omitted data."""
    core = rows.core
    if (len(rows.protection_slices) != core["protection_slice_count"]
            or [row["ordinal"] for row in rows.protection_slices]
            != list(range(len(rows.protection_slices)))
            or any(row["intent_id"] != core["intent_id"] for row in rows.protection_slices)):
        raise ValueError("Strategy intent projection has missing, extra, or altered fields")

    def number(name: str) -> float | None:
        value = core[name]
        return float(value) if value is not None else None

    capital = None
    if core["capital_mode"] is not None:
        capital = CapitalRequest(
            mode=core["capital_mode"], value=float(core["capital_value"]),
            minimum_quantity=float(core["capital_minimum_quantity"]),
            maximum_quantity=number("capital_maximum_quantity"),
            allow_replacement=bool(core["capital_allow_replacement"]),
        )
    policy = None
    if core["execution_policy_id"] is not None:
        policy = execution_policy_from_payload({
            "policy_id": core["execution_policy_id"],
            "revision": core["execution_policy_revision"],
            "name": core["execution_policy_name"],
            "partial_fill_policy": core["execution_partial_fill_policy"],
            "quote_source": core["execution_quote_source"],
            "envelope": {
                "maximum_buy_price": number("execution_maximum_buy_price"),
                "minimum_sell_price": number("execution_minimum_sell_price"),
                "deadline_ms": core["execution_deadline_ms"],
                "maximum_reprices": core["execution_maximum_reprices"],
                "minimum_reprice_interval_ms": core["execution_minimum_reprice_interval_ms"],
                "persist_until_cancelled": bool(core["execution_persist_until_cancelled"]),
            },
        })
    protection = None
    if core["protection_profile_id"] is not None:
        slices = []
        for row in rows.protection_slices:
            anchor = None
            if row["anchor_observation_id"] is not None:
                anchor = {
                    "observation_id": row["anchor_observation_id"],
                    "price": float(row["anchor_price"]),
                    "confirmed_at": row["anchor_confirmed_at"],
                    "timeframe": row["anchor_timeframe"],
                    "ordinal": row["anchor_ordinal"],
                }
            slices.append({
                "slice_id": row["slice_id"],
                "quantity_fraction": float(row["quantity_fraction"]),
                "profit_target_price": (
                    float(row["profit_target_price"])
                    if row["profit_target_price"] is not None else None
                ),
                "inherit_profit_target": bool(row["inherit_profit_target"]),
                "stop": {
                    "rule_type": row["stop_rule_type"],
                    "order_type": row["stop_order_type"],
                    "price": float(row["stop_price"]) if row["stop_price"] is not None else None,
                    "distance_percent": (
                        float(row["stop_distance_percent"])
                        if row["stop_distance_percent"] is not None else None
                    ),
                    "distance_bps": (
                        float(row["stop_distance_bps"])
                        if row["stop_distance_bps"] is not None else None
                    ),
                    "maximum_cash_risk": (
                        float(row["stop_maximum_cash_risk"])
                        if row["stop_maximum_cash_risk"] is not None else None
                    ),
                    "volatility_multiple": (
                        float(row["stop_volatility_multiple"])
                        if row["stop_volatility_multiple"] is not None else None
                    ),
                    "buffer_bps": float(row["stop_buffer_bps"]),
                    "anchor": anchor,
                    "stop_limit_offset_bps": (
                        float(row["stop_limit_offset_bps"])
                        if row["stop_limit_offset_bps"] is not None else None
                    ),
                },
                "trailing": {
                    "rule_type": row["trailing_rule_type"],
                    "amount": (
                        float(row["trailing_amount"])
                        if row["trailing_amount"] is not None else None
                    ),
                    "percent": (
                        float(row["trailing_percent"])
                        if row["trailing_percent"] is not None else None
                    ),
                    "volatility_multiple": (
                        float(row["trailing_volatility_multiple"])
                        if row["trailing_volatility_multiple"] is not None else None
                    ),
                    "activation_gain_percent": float(row["trailing_activation_gain_percent"]),
                    "breakeven_buffer_bps": float(row["trailing_breakeven_buffer_bps"]),
                    "structural_timeframe": row["trailing_structural_timeframe"],
                },
            })
        protection = protection_profile_from_payload({
            "profile_id": core["protection_profile_id"],
            "revision": core["protection_profile_revision"],
            "slices": slices,
            "add_policy": core["protection_add_policy"],
            "profit_pocket_transition": core["protection_profit_pocket_transition"],
            "mandatory_catastrophic_backstop": bool(
                core["protection_mandatory_catastrophic_backstop"]
            ),
            "emergency_repair_deadline_ms": core["protection_emergency_repair_deadline_ms"],
        })
    restored = StrategyIntent(
        intent_id=core["intent_id"], ticker=core["ticker"],
        event_time=datetime.fromisoformat(core["event_time"]),
        action=core["action"], quantity=float(core["quantity"]),
        reference_price=float(core["reference_price"]),
        schema_version=int(core["schema_version"]), capital_request=capital,
        invalidation_price=number("invalidation_price"),
        profit_target_price=number("profit_target_price"),
        trailing_amount=number("trailing_amount"),
        execution_policy=policy, protection_profile=protection,
        urgency=core["urgency"], time_in_force=core["time_in_force"],
        outside_rth=bool(core["outside_rth"]), reason=core["reason"],
    )
    verified = project_strategy_intent(restored)
    if verified != rows:
        changed = sorted(key for key in set(verified.core) | set(core)
                         if verified.core.get(key) != core.get(key))
        raise ValueError(
            "Strategy intent projection has missing, extra, or altered fields: "
            + ",".join(changed or ["protection_slices"])
        )
    return restored


def strategy_intent_batch(
    intent: StrategyIntent, *, run_id: str, run_month: date, account_id: str,
    attempt_id: str, batch_id: str, prior_batch_id: str, sequence: int,
    source_cursor: str, run_status: str, recorded_at: datetime,
) -> TypedJournalBatch:
    """Build a fenced journal event with one typed intent and child slices."""
    if not run_id or not account_id or recorded_at.tzinfo is None:
        raise ValueError("Strategy intent journal identity and receipt clock are required")
    projected = project_strategy_intent(intent)
    event_month = intent.event_time.astimezone(timezone.utc).strftime("%Y-%m-01")
    record_id = str(uuid5(NAMESPACE_URL, f"{run_id}:{batch_id}:{intent.intent_id}:intent"))
    event = {
        "run_id": run_id, "event_month": event_month,
        "attempt_id": attempt_id, "batch_id": batch_id,
        "record_id": record_id, "sequence": sequence,
        "event_time": projected.core["event_time"],
        "recorded_at": _instant(recorded_at),
        "category": "strategy", "entity_type": "strategy_intent",
        "entity_id": intent.intent_id, "account_id": account_id,
        "correlation_id": "", "causation_id": "",
    }
    identity = {
        "run_id": run_id, "event_month": event_month,
        "batch_id": batch_id, "account_id": account_id,
    }
    detail = {
        "record_id": record_id, **identity,
        **{key: value for key, value in projected.core.items() if key != "event_time"},
    }
    children = tuple({
        "record_id": str(uuid5(NAMESPACE_URL, f"{record_id}:slice:{row['ordinal']}")),
        "parent_record_id": record_id, **identity,
        **{key: value for key, value in row.items() if key != "intent_id"},
    } for row in projected.protection_slices)
    return TypedJournalBatch(
        run_id, run_month, attempt_id, batch_id, prior_batch_id,
        sequence, sequence, source_cursor, run_status, (event,),
        intents=(detail,), intent_slices=children,
    )


@dataclass(frozen=True, slots=True)
class RecoveredIntent:
    sequence: int
    account_id: str
    record_id: str
    intent: StrategyIntent


def _stored_instant(value: str) -> str:
    match = re.fullmatch(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\.(\d{6})000", value)
    if match is None:
        raise ValueError("Stored intent time cannot fit Python's causal clock")
    return _instant(datetime.fromisoformat(
        f"{match.group(1)}.{match.group(2)}+00:00"
    ))


def _verify_stored_row(name: str, row: dict[str, Any]) -> dict[str, Any]:
    content = {key: value for key, value in row.items() if key != "content_hash"}
    canonical = _canonical_typed_content(
        name, content, stored_utc=True,
    )
    digest = sha256(canonical_json(canonical).encode("utf-8")).hexdigest()
    if digest != str(row["content_hash"]):
        raise RuntimeError(f"Recovered {name} row differs from its committed hash")
    return canonical


def load_committed_strategy_intent_page(
    client: Any, prefix: CommittedPrefix, *, after_sequence: int = 0,
    limit: int = 200, max_slices: int = 4096,
) -> tuple[RecoveredIntent, ...]:
    """Read one bounded, fully typed intent page from a verified prefix."""
    if not isinstance(prefix, CommittedPrefix) or not prefix.batch_ids:
        raise ValueError("Intent recovery requires a verified committed prefix")
    if after_sequence < 0 or not 1 <= limit <= 500 or max_slices < 1:
        raise ValueError("Intent recovery page bounds are invalid")
    event_columns = ",".join(column for column, _ in _CONTRACTS["trading_event_v1"].columns)
    events = _rows(client,
        f"SELECT {event_columns} FROM arte.trading_event_v1 "
        f"WHERE run_id={_literal(prefix.run_id)} "
        f"AND sequence>{after_sequence} AND sequence<={prefix.last_sequence} "
        "AND category='strategy' AND entity_type='strategy_intent' "
        f"{_committed_batch_filter(prefix)}"
        f"ORDER BY sequence LIMIT {limit} FORMAT JSONEachRow")
    if not events:
        return ()
    ids = [str(UUID(str(row["record_id"]))) for row in events]
    if len(set(ids)) != len(ids):
        raise RuntimeError("Committed intent page repeated an event identity")
    allowed_batches = set(prefix.batch_ids)
    previous = after_sequence
    for event in events:
        _verify_stored_row("trading_event_v1", event)
        sequence = int(event["sequence"])
        if (sequence <= previous or sequence > prefix.last_sequence
                or str(UUID(str(event["batch_id"]))) not in allowed_batches):
            raise RuntimeError("Intent event is outside the committed prefix")
        previous = sequence
    ids_sql = ",".join(f"toUUID({_literal(value)})" for value in ids)
    parent_columns = ",".join(column for column, _ in
                              _CONTRACTS["trading_strategy_intent_v1"].columns)
    parents = _rows(client,
        f"SELECT {parent_columns} FROM arte.trading_strategy_intent_v1 "
        f"WHERE run_id={_literal(prefix.run_id)} AND record_id IN ({ids_sql}) "
        f"{_committed_batch_filter(prefix)}"
        "FORMAT JSONEachRow")
    if len(parents) != len(events):
        raise RuntimeError("Committed intent page has missing or duplicate details")
    by_id = {str(UUID(str(row["record_id"]))): row for row in parents}
    if set(by_id) != set(ids):
        raise RuntimeError("Intent details differ from committed events")
    expected_slices = sum(int(row["protection_slice_count"]) for row in parents)
    if expected_slices > max_slices:
        raise RuntimeError("Intent page exceeds its bounded protection-slice budget")
    slices: list[dict[str, Any]] = []
    if expected_slices:
        child_columns = ",".join(column for column, _ in
                                 _CONTRACTS["trading_intent_protection_slice_v1"].columns)
        slices = _rows(client,
            f"SELECT {child_columns} FROM arte.trading_intent_protection_slice_v1 "
            f"WHERE run_id={_literal(prefix.run_id)} "
            f"AND parent_record_id IN ({ids_sql}) "
            f"{_committed_batch_filter(prefix)}"
            f"LIMIT {max_slices + 1} FORMAT JSONEachRow")
    if len(slices) != expected_slices:
        raise RuntimeError("Committed intent page has missing or excess slices")
    by_parent: dict[str, list[dict[str, Any]]] = {}
    for row in slices:
        canonical_row = _verify_stored_row("trading_intent_protection_slice_v1", row)
        parent_id = str(UUID(str(row["parent_record_id"])))
        if parent_id not in by_id:
            raise RuntimeError("Protection slice lacks a committed intent parent")
        by_parent.setdefault(parent_id, []).append(canonical_row)
    recovered = []
    for event in events:
        record_id = str(UUID(str(event["record_id"])))
        parent = by_id[record_id]
        canonical_parent = _verify_stored_row("trading_strategy_intent_v1", parent)
        if (str(UUID(str(parent["batch_id"]))) != str(UUID(str(event["batch_id"])))
                or parent["event_month"] != event["event_month"]
                or parent["account_id"] != event["account_id"]
                or parent["intent_id"] != event["entity_id"]):
            raise RuntimeError("Intent detail differs from its event envelope")
        projected_core = {
            key: value for key, value in canonical_parent.items()
            if key not in {"record_id", "run_id", "event_month", "batch_id",
                           "account_id", "content_hash"}
        }
        projected_core["event_time"] = _stored_instant(event["event_time"])
        projected_slices = []
        for row in sorted(by_parent.get(record_id, []), key=lambda value: value["ordinal"]):
            if (str(UUID(str(row["batch_id"]))) != str(UUID(str(parent["batch_id"])))
                    or row["event_month"] != parent["event_month"]
                    or row["account_id"] != parent["account_id"]):
                raise RuntimeError("Protection slice differs from its intent parent")
            projected = {
                key: value for key, value in row.items()
                if key not in {"record_id", "parent_record_id", "run_id",
                               "event_month", "batch_id", "account_id", "content_hash"}
            }
            projected["intent_id"] = parent["intent_id"]
            if projected["anchor_confirmed_at"] is not None:
                projected["anchor_confirmed_at"] = _stored_instant(
                    projected["anchor_confirmed_at"]
                )
            projected_slices.append(projected)
        reconstructed = restore_strategy_intent(
            ProjectedIntent(projected_core, tuple(projected_slices))
        )
        recovered.append(RecoveredIntent(
            int(event["sequence"]), str(event["account_id"]), record_id, reconstructed,
        ))
    return tuple(recovered)
