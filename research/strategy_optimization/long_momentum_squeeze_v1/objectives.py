from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class TrialMetrics:
    net_pnl: float
    realized_pnl: float
    commissions: float
    maximum_realized_drawdown: float
    execution_count: int
    entry_intent_count: int
    exit_intent_count: int
    reentry_count: int
    profit_take_count: int
    liquidity_violations: int
    causal_violations: int
    squeeze_occurrence_count: int
    watchlist_transition_count: int
    final_position_count: int
    final_absolute_position_quantity: float
    final_open_order_count: int

    @property
    def admissible(self) -> bool:
        return (
            self.liquidity_violations == 0
            and self.causal_violations == 0
            and self.squeeze_occurrence_count > 0
            and self.watchlist_transition_count > 0
            and self.entry_intent_count > 0
            and self.exit_intent_count > 0
            and self.execution_count >= 2
            and self.final_position_count == 0
            and self.final_absolute_position_quantity <= 1e-9
            and self.final_open_order_count == 0
        )

    @property
    def objective(self) -> tuple[int, float, float, int]:
        return (
            int(self.admissible),
            self.net_pnl,
            -self.maximum_realized_drawdown,
            self.execution_count,
        )

    def payload(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "admissible": self.admissible,
            "objective": list(self.objective),
        }


def realized_drawdown(executions: Iterable[dict[str, Any]]) -> float:
    """Compute a conservative closed-trade P/L drawdown including commissions."""

    positions: dict[tuple[str, int], tuple[float, float]] = {}
    curve = 0.0
    high = 0.0
    drawdown = 0.0
    ordered = sorted(
        (dict(row) for row in executions),
        key=lambda row: (str(row.get("trade_time") or ""), str(row.get("execution_id") or "")),
    )
    for row in ordered:
        key = (str(row.get("account") or ""), int(row.get("conid") or 0))
        quantity = float(row.get("size") or 0)
        price = float(row.get("price") or 0)
        commission = float(row.get("commission") or 0)
        held, average = positions.get(key, (0.0, 0.0))
        if str(row.get("side") or "").upper() == "B":
            total = average * held + price * quantity
            held += quantity
            average = total / held if held else 0.0
            curve -= commission
        else:
            closing = min(held, quantity)
            curve += (price - average) * closing - commission
            held = max(0.0, held - closing)
            if held == 0:
                average = 0.0
        positions[key] = (held, average)
        high = max(high, curve)
        drawdown = max(drawdown, high - curve)
    return drawdown


def count_causal_violations(records: Iterable[Any]) -> int:
    violations = 0
    last_by_entity: dict[tuple[str, str], datetime] = {}
    for record in sorted(records, key=lambda item: int(item.sequence)):
        event_time = record.event_time
        key = (str(record.category), str(record.entity_id))
        if event_time.tzinfo is None:
            violations += 1
        previous = last_by_entity.get(key)
        if previous is not None and event_time < previous:
            violations += 1
        last_by_entity[key] = event_time
        available = dict(record.payload).get("available_at")
        source_event = dict(record.payload).get("event_time")
        if available and source_event:
            available_at = datetime.fromisoformat(str(available).replace("Z", "+00:00"))
            event_at = datetime.fromisoformat(str(source_event).replace("Z", "+00:00"))
            if available_at < event_at:
                violations += 1
    return violations


def liquidity_membership_violations(
    intent_records: Iterable[Any],
    timeline: list[dict[str, Any]],
) -> int:
    transitions = sorted(timeline, key=lambda row: row["effective_at"])
    transition_times = [row["effective_at"] for row in transitions]
    memberships = [
        {
            str(member.get("ticker") or "").upper()
            for member in snapshot.get("members") or []
        }
        for snapshot in transitions
    ]
    violations = 0
    for record in intent_records:
        payload = dict(record.payload)
        if str(payload.get("action") or "") not in {"enter_long", "enter_short"}:
            continue
        index = bisect_right(transition_times, record.event_time) - 1
        active = memberships[index] if index >= 0 else set()
        if str(payload.get("ticker") or "").upper() not in active:
            violations += 1
    return violations


def decision_counts(records: Iterable[Any]) -> dict[str, int]:
    counts: defaultdict[str, int] = defaultdict(int)
    for record in records:
        payload = dict(record.payload)
        action = str(payload.get("action") or "")
        reason = str(payload.get("reason") or "")
        if action:
            counts[f"action:{action}"] += 1
        if reason:
            counts[f"reason:{reason}"] += 1
    return dict(counts)
