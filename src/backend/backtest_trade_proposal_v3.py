"""Inactive V3-only typed trade-proposal facts and exact aggregate seal.

No generic evidence nodes: every row is a named child of one proposal event.
The nine tables are staged definitions only; no DDL or INSERT occurs here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any, Mapping, Sequence
from uuid import UUID

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.arte_trade_proposal_projection import (
    TABLES as BASE_TABLES, project_trade_proposal,
)
from src.trading_runtime.arte_trade_proposal_children import (
    TABLES as CHILD_TABLES, project_market_child, project_result_children,
)
from src.trading_runtime.journal_contract import JournalRecord, canonical_json


def _table(source: TableContract, *, parent: bool = False) -> TableContract:
    columns = list(source.columns)
    columns.insert(2, ("batch_id", "UUID"))
    if parent:
        columns.extend((
            ("intent_present", "Bool"), ("market_present", "Bool"),
            ("decision_present", "Bool"), ("order_group_present", "Bool"),
            ("reason_count", "UInt32"), ("order_id_count", "UInt32"),
        ))
    return TableContract(source.name.removesuffix("_v1") + "_v3",
                         tuple(columns), source.partition,
                         source.order.replace("run_id,", "run_id, batch_id,"))


TABLES = tuple(_table(source, parent=index == 0)
               for index, source in enumerate(BASE_TABLES + CHILD_TABLES))
BY_NAME = {table.name: table for table in TABLES}
PARENT = TABLES[0]
TABLE_BY_KIND = dict(zip(("parent", "intent", "result", "market", "decision",
                          "metrics", "reasons", "order_group", "order_ids"), TABLES))


@dataclass(frozen=True, slots=True)
class ProposalV3Projection:
    event: dict[str, Any]
    rows: tuple[tuple[str, dict[str, Any]], ...]


def _timestamp(value: Any, *, stored_utc: bool = False) -> str:
    if isinstance(value, datetime):
        at = value
    elif isinstance(value, str):
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("Proposal timestamp has unknown type")
    if at.tzinfo is None:
        if not stored_utc or isinstance(value, datetime):
            raise ValueError("Proposal timestamp lacks timezone authority")
        # ClickHouse JSONEachRow returns a naive UTC string for DateTime64.
        if re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{6}(?:000)?", value) is None:
            raise ValueError("Proposal stored UTC precision differs")
        at = at.replace(tzinfo=timezone.utc)
    return at.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _canonical(row: Mapping[str, Any], table: TableContract,
               *, stored_utc: bool = False) -> dict[str, Any]:
    if set(row) != {name for name, _ in table.columns}:
        raise ValueError(f"{table.name} columns differ")
    result = {}
    for name, kind in table.columns:
        value = row[name]
        nullable = kind.startswith("Nullable(")
        if value is None and nullable:
            result[name] = None
            continue
        inner = kind[9:-1] if nullable else kind
        if inner == "UUID":
            result[name] = str(UUID(str(value)))
        elif inner.startswith("DateTime64"):
            result[name] = _timestamp(value, stored_utc=stored_utc)
        elif inner.startswith("Decimal"):
            from decimal import Decimal, InvalidOperation
            try:
                number = Decimal(str(value))
                scaled = number.quantize(Decimal("0.000000000000000001"))
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(f"Invalid {table.name}.{name}") from exc
            if not number.is_finite() or number != scaled or abs(number) >= Decimal(10) ** 20:
                raise ValueError(f"Invalid {table.name}.{name}")
            result[name] = format(scaled, "f")
        elif inner in {"UInt16", "UInt32", "UInt64", "Int32", "Int64"}:
            bits = int(re.search(r"\d+", inner).group())
            lower = 0 if inner.startswith("U") else -(2 ** (bits - 1))
            upper = 2 ** bits if inner.startswith("U") else 2 ** (bits - 1)
            if type(value) is not int or not lower <= value < upper:
                raise ValueError(f"Invalid {table.name}.{name}")
            result[name] = value
        elif inner in {"Bool", "UInt8"}:
            if type(value) is bool:
                result[name] = value
            elif stored_utc and type(value) is int and value in (0, 1):
                result[name] = bool(value)
            else:
                raise ValueError(f"Invalid {table.name}.{name}")
        elif inner in {"String", "LowCardinality(String)", "FixedString(64)"}:
            if not isinstance(value, str):
                raise ValueError(f"Invalid {table.name}.{name}")
            if inner == "FixedString(64)" and re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ValueError(f"Invalid {table.name}.{name} hash")
            candidate = value.lstrip("\ufeff \t\r\n")
            if candidate.startswith(("{", "[")):
                try:
                    decoded = json.loads(candidate)
                except (ValueError, TypeError):
                    pass
                else:
                    if isinstance(decoded, (dict, list)):
                        raise ValueError(f"Opaque JSON is forbidden in {table.name}.{name}")
            result[name] = value
        else:
            raise ValueError(f"Unmodeled {table.name}.{name} type")
    return result


def project_trade_proposal_v3(
    record: JournalRecord, *, attempt_id: str, batch_id: str,
) -> ProposalV3Projection:
    UUID(record.record_id)
    UUID(attempt_id)
    UUID(batch_id)
    _timestamp(record.event_time)
    _timestamp(record.recorded_at)
    base = project_trade_proposal(record)
    children = None
    if record.entity_type == "trade_proposal_confirmed" and record.payload["intent"]["metadata"]:
        children = project_market_child(record)
    elif record.entity_type == "trade_proposal_result" and "decision" in record.payload:
        decision = record.payload["decision"]
        if (isinstance(decision, Mapping) and
                (record.payload.get("order_group") is not None or
                 set(decision) - {"status", "reason", "held_quantity"})):
            children = project_result_children(record)
    children_by_kind = {
        "parent": (base.parent,), "intent": (base.intent,) if base.intent else (),
        "result": (base.result,) if base.result else (),
        "market": (children.market,) if children and children.market else (),
        "decision": (children.decision,) if children and children.decision else (),
        "metrics": children.metrics if children else (),
        "reasons": children.reasons if children else (),
        "order_group": (children.order_group,) if children and children.order_group else (),
        "order_ids": children.order_ids if children else (),
    }
    parent = dict(base.parent)
    parent.update(intent_present=bool(children_by_kind["intent"]),
                  market_present=bool(children_by_kind["market"]),
                  decision_present=bool(children_by_kind["decision"]),
                  order_group_present=bool(children_by_kind["order_group"]),
                  reason_count=len(children_by_kind["reasons"]),
                  order_id_count=len(children_by_kind["order_ids"]))
    children_by_kind["parent"] = (parent,)
    rows = tuple((TABLE_BY_KIND[kind].name,
                  _canonical({**row, "batch_id": batch_id}, TABLE_BY_KIND[kind]))
                 for kind in TABLE_BY_KIND for row in children_by_kind[kind])
    payload = record.payload
    event = {"record_id": record.record_id, "run_id": record.run_id,
             "event_month": record.event_time.astimezone(timezone.utc).date().replace(day=1).isoformat(),
             "batch_id": batch_id, "attempt_id": attempt_id,
             "sequence": record.sequence, "account_id": record.account_id,
             "event_time": record.event_time, "recorded_at": record.recorded_at,
             "category": record.category, "entity_type": record.entity_type,
             "entity_id": record.entity_id,
             "correlation_id": payload.get("correlation_id", ""),
             "causation_id": payload.get("causation_id", "")}
    return ProposalV3Projection(event, rows)


def seal_trade_proposals_v3(
    rows: Sequence[tuple[str, Mapping[str, Any]]],
    parents: Sequence[Mapping[str, Any]], *, run_id: str, batch_id: str,
    stored_utc: bool = False,
) -> dict[str, Any]:
    """Bind all typed rows and require exact parent/child cardinality."""
    batch = str(UUID(batch_id))
    events = {str(UUID(str(e["record_id"]))): e for e in parents
              if (e["category"], e["entity_type"]) in {
                  ("trade_proposal", "trade_proposal_confirmed"),
                  ("trade_proposal", "trade_proposal_result")}}
    if len(events) != sum((e["category"], e["entity_type"]) in {
            ("trade_proposal", "trade_proposal_confirmed"),
            ("trade_proposal", "trade_proposal_result")} for e in parents):
        raise ValueError("Duplicate proposal parent")
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
    identities = set()
    hashes = []
    for name, raw in rows:
        table = BY_NAME.get(name)
        if table is None:
            raise ValueError("Unknown proposal child table")
        row = _canonical(raw, table, stored_utc=stored_utc)
        record_id = row["record_id"]
        event = events.get(record_id)
        if event is None or row["run_id"] != run_id or row["batch_id"] != batch or (
                str(UUID(str(event["batch_id"]))) != batch or event["run_id"] != run_id
                or row["event_time"] != _timestamp(
                    event["event_time"], stored_utc=stored_utc)):
            raise ValueError("Orphan proposal child")
        identity = (name, record_id, row.get("phase"), row.get("kind"), row.get("ordinal"))
        if identity in identities:
            raise ValueError("Duplicate proposal child")
        identities.add(identity)
        grouped.setdefault(record_id, {}).setdefault(name, []).append(row)
        hashes.append((name, identity[1:], sha256(canonical_json(row).encode()).hexdigest()))
    for record_id, event in events.items():
        by_table = grouped.get(record_id, {})
        parent_rows = by_table.get(PARENT.name, ())
        if len(parent_rows) != 1:
            raise ValueError("Proposal parent lacks typed marker")
        marker = parent_rows[0]
        confirmed = event["entity_type"] == "trade_proposal_confirmed"
        if (marker["entity_type"] != event["entity_type"] or marker["proposal_id"] != event["entity_id"]
                or marker["account_id"] != event["account_id"]
                or marker["authority"] not in {"manual", "semi_automatic"}
                or marker["status"] != ("confirmed" if confirmed else marker["status"])
                or len(by_table.get(TABLE_BY_KIND["intent"].name, ())) != int(confirmed)
                or len(by_table.get(TABLE_BY_KIND["result"].name, ())) != int(not confirmed)
                or marker["intent_present"] != confirmed
                or marker["market_present"] != confirmed
                or len(by_table.get(TABLE_BY_KIND["market"].name, ())) != int(marker["market_present"])
                or len(by_table.get(TABLE_BY_KIND["decision"].name, ())) != int(marker["decision_present"])
                or len(by_table.get(TABLE_BY_KIND["order_group"].name, ())) != int(marker["order_group_present"])
                or len(by_table.get(TABLE_BY_KIND["metrics"].name, ())) != 2 * int(marker["decision_present"])
                or len(by_table.get(TABLE_BY_KIND["reasons"].name, ())) != marker["reason_count"]
                or len(by_table.get(TABLE_BY_KIND["order_ids"].name, ())) != marker["order_id_count"]):
            raise ValueError("Proposal child cardinality differs from marker")
        intent_rows = by_table.get(TABLE_BY_KIND["intent"].name, ())
        decision_rows = by_table.get(TABLE_BY_KIND["decision"].name, ())
        order_rows = by_table.get(TABLE_BY_KIND["order_group"].name, ())
        if (intent_rows and (intent_rows[0]["intent_id"] != f"proposal:{marker['proposal_id']}"
                             or intent_rows[0]["ticker"] == "")
                or decision_rows and (decision_rows[0]["request_id"] !=
                                      f"proposal:{marker['proposal_id']}"
                                      or decision_rows[0]["account_id"] != marker["account_id"]
                                      or decision_rows[0]["status"] != marker["status"])
                or order_rows and (not decision_rows or order_rows[0]["intent_id"] !=
                                   decision_rows[0]["request_id"]
                                   or order_rows[0]["account_id"] != marker["account_id"])):
            raise ValueError("Proposal nested identity differs")
        if decision_rows and {row["phase"] for row in by_table.get(
                TABLE_BY_KIND["metrics"].name, ())} != {"before", "after"}:
            raise ValueError("Proposal metric phases differ")
        reasons = by_table.get(TABLE_BY_KIND["reasons"].name, ())
        if sorted(row["ordinal"] for row in reasons) != list(range(len(reasons))):
            raise ValueError("Proposal reason ordinals differ")
        order_ids = by_table.get(TABLE_BY_KIND["order_ids"].name, ())
        for kind in {row["kind"] for row in order_ids}:
            if kind not in {"client_order_ids", "broker_order_ids", "warning_message_ids"} or sorted(
                    row["ordinal"] for row in order_ids if row["kind"] == kind) != list(range(
                        sum(row["kind"] == kind for row in order_ids))):
                raise ValueError("Proposal order-id ordinals differ")
        if not confirmed and marker["status"] in {"approved", "resized", "deferred"} and not decision_rows:
            raise ValueError("Approved proposal lacks Portfolio decision")
        if confirmed and (marker["decision_present"] or marker["order_group_present"] or
                          marker["reason_count"] or marker["order_id_count"]):
            raise ValueError("Confirmation carries result children")
        if not confirmed and marker["market_present"]:
            raise ValueError("Result carries market child")
        if not confirmed and marker["status"] == "confirmed":
            raise ValueError("Result has confirmation status")
    return {"trade_proposal_child_count": len(rows),
            "trade_proposal_child_hash": sha256(canonical_json(sorted(hashes)).encode()).hexdigest()}
