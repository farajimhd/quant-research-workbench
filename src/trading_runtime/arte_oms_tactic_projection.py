"""Lossless scalar projection of one immutable OMS tactic revision.

The parent row records absence explicitly; child steps have ordered scalar
prices. This codec does not write ClickHouse. A caller must seal both families
under the same committed group revision before using them for live recovery.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Mapping
from uuid import NAMESPACE_URL, UUID, uuid5

from .arte_journal_projection import _exact_decimal
from .arte_journal_writer import _canonical_typed_content, typed_row
from .journal_contract import canonical_json
from .order_management import ExecutionQuote, ExecutionTactic, ExecutionUrgency, PriceStep

PARENT_TABLE = "trading_oms_execution_tactic_v1"
STEP_TABLE = "trading_oms_execution_step_v1"


def tactic_rows(
    tactic: ExecutionTactic | None, *, group_record_id: str, run_id: str,
    event_month: str, batch_id: str, account_id: str,
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    """Produce deterministic, typed parent and ordered steps for one group state."""
    group_id = str(UUID(group_record_id))
    batch = str(UUID(batch_id))
    if not run_id or not account_id:
        raise ValueError("OMS tactic needs run and account identity")
    parent_id = str(uuid5(NAMESPACE_URL, f"{group_id}:execution-tactic"))
    common = {"run_id": run_id, "event_month": event_month,
              "batch_id": batch, "account_id": account_id}
    if tactic is None:
        parent = {**common, "record_id": parent_id,
                  "parent_record_id": group_id, "has_tactic": 0,
                  "urgency": "", "side": "", "quote_bid": None,
                  "quote_ask": None, "quote_observed_at": None,
                  "quote_tick_size": None, "maximum_duration_ms": None,
                  "step_count": 0}
        return typed_row(PARENT_TABLE, parent), ()
    if (not isinstance(tactic, ExecutionTactic)
            or not 1 <= len(tactic.steps) <= 65535
            or tactic.side not in {"BUY", "SELL"}
            or tactic.maximum_duration_ms < 0):
        raise ValueError("OMS tactic is outside its typed contract")
    if any(type(step.after_ms) is not int or step.after_ms < 0
           or step.after_ms > tactic.maximum_duration_ms
           for step in tactic.steps):
        raise ValueError("OMS tactic step clock is outside its duration")
    if any(left.after_ms >= right.after_ms
           for left, right in zip(tactic.steps, tactic.steps[1:])):
        raise ValueError("OMS tactic steps require increasing activation times")
    quote = tactic.quote
    parent = {**common, "record_id": parent_id,
              "parent_record_id": group_id, "has_tactic": 1,
              "urgency": tactic.urgency.value, "side": tactic.side,
              "quote_bid": _exact_decimal(quote.bid),
              "quote_ask": _exact_decimal(quote.ask),
              "quote_observed_at": quote.observed_at.astimezone(timezone.utc).isoformat(),
              "quote_tick_size": _exact_decimal(quote.tick_size),
              "maximum_duration_ms": tactic.maximum_duration_ms,
              "step_count": len(tactic.steps)}
    steps = tuple(typed_row(STEP_TABLE, {
        **common, "record_id": str(uuid5(NAMESPACE_URL, f"{parent_id}:step:{ordinal}")),
        "parent_record_id": parent_id, "ordinal": ordinal,
        "after_ms": step.after_ms, "price": _exact_decimal(step.price),
    }) for ordinal, step in enumerate(tactic.steps))
    return typed_row(PARENT_TABLE, parent), steps


def _verified(name: str, row: Mapping[str, Any], *, stored_utc: bool) -> None:
    content = {key: value for key, value in row.items() if key != "content_hash"}
    digest = sha256(canonical_json(_canonical_typed_content(
        name, content, stored_utc=stored_utc)).encode("utf-8")).hexdigest()
    if digest != row.get("content_hash"):
        raise ValueError(f"OMS tactic {name} row differs from its typed hash")


def tactic_from_rows(
    parent: Mapping[str, Any], steps: tuple[Mapping[str, Any], ...],
    *, stored_utc: bool = False,
) -> ExecutionTactic | None:
    """Reject missing, duplicate, foreign, or corrupted child evidence."""
    _verified(PARENT_TABLE, parent, stored_utc=stored_utc)
    parent_id = str(UUID(str(parent["record_id"])))
    if len(steps) != int(parent["step_count"]):
        raise ValueError("OMS tactic step count differs from parent")
    for ordinal, row in enumerate(steps):
        _verified(STEP_TABLE, row, stored_utc=stored_utc)
        if (int(row["ordinal"]) != ordinal
                or str(UUID(str(row["parent_record_id"]))) != parent_id
                or any(row[key] != parent[key] for key in
                       ("run_id", "event_month", "batch_id", "account_id"))):
            raise ValueError("OMS tactic step lineage or order differs")
    if int(parent["has_tactic"]) == 0:
        if (steps or parent["urgency"] or parent["side"]
                or any(parent[key] is not None for key in (
                    "quote_bid", "quote_ask", "quote_observed_at",
                    "quote_tick_size", "maximum_duration_ms"))):
            raise ValueError("Absent OMS tactic has unexpected fields")
        return None
    if int(parent["has_tactic"]) != 1 or not steps:
        raise ValueError("Present OMS tactic is incomplete")
    quote_time = parent["quote_observed_at"]
    if stored_utc:
        quote_time = str(quote_time).replace(" ", "T") + "+00:00"
    quote = ExecutionQuote(
        float(parent["quote_bid"]), float(parent["quote_ask"]),
        datetime.fromisoformat(str(quote_time)), float(parent["quote_tick_size"]),
    )
    tactic = ExecutionTactic(
        ExecutionUrgency(parent["urgency"]), parent["side"],
        tuple(PriceStep(int(row["after_ms"]), float(row["price"]))
              for row in steps), quote, int(parent["maximum_duration_ms"]),
    )
    # Reuse the strict writer validation for all scalar bounds and ordering.
    tactic_rows(tactic, group_record_id=str(parent["parent_record_id"]),
                run_id=parent["run_id"], event_month=str(parent["event_month"]),
                batch_id=str(parent["batch_id"]), account_id=parent["account_id"])
    return tactic
