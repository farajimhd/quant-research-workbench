"""Strict typed projections for the ARTE journal persistence lane.

These conversions belong on the writer lane, not on a realtime market callback.
They never publish or write files. Unknown broker fields fail closed until a
versioned typed column or child family represents them.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from hashlib import sha256
import math
import re
from typing import Any, Mapping
from uuid import NAMESPACE_URL, UUID, uuid5

from src.trading_runtime.arte_journal_schema import TABLES
from src.trading_runtime.arte_journal_writer import (
    CommittedPrefix, V2CommittedPrefix, VerifiedPrefix, TypedJournalBatch,
    _canonical_typed_content, _committed_batch_filter, _literal, _rows,
)
from src.trading_runtime.domain import CommissionEvent
from src.trading_runtime.ibkr_client import _execution as parse_ibkr_execution
from src.trading_runtime.ibkr_schema import Execution, OrderRequest
from src.trading_runtime.journal_contract import JournalRecord, canonical_json
from src.trading_runtime.signals import StrategySignal


_SOURCE_FIELDS = frozenset({
    "execution_id", "executionId", "symbol", "side", "order_ref", "orderRef",
    "trade_time", "trade_time_r", "timestamp", "size", "quantity", "price",
    "order_id", "orderId", "account", "acctId", "conid", "con_id",
    "commission", "currency", "exchange",
})
_SCALE = Decimal("0.0000000001")
_MEASURE_SCALE = Decimal("0.000000000000000001")
_RISK_METRICS = (
    "net_liquidation", "available_funds", "buying_power", "gross_exposure",
    "net_exposure", "reserved_notional", "open_risk", "daily_loss",
    "drawdown", "position_count",
)
_RISK_SOURCE_FIELDS = frozenset({
    "account_id", "account_key", "state", "reasons", "metrics", "observed_at",
    "protection_required", "protection_coverage", "internal_reaction_ms",
})
_SIGNAL_EVIDENCE_MAX_NODES = 16_384
_SIGNAL_EVIDENCE_MAX_DEPTH = 24
_SIGNAL_EVIDENCE_MAX_BYTES = 8_388_608


def project_signal_evidence_nodes(metadata: Mapping[str, Any], *, run_id: str,
                                  event_month: str, batch_id: str,
                                  parent_record_id: str) -> tuple[dict[str, Any], ...]:
    """Flatten signal evidence into typed, bounded and ordered scalar nodes."""
    if not isinstance(metadata, Mapping):
        raise ValueError("Strategy signal metadata must be a mapping")
    rows: list[dict[str, Any]] = []
    active: set[int] = set()
    total_bytes = 0

    def visit(value: Any, parent: str | None, ordinal: int,
              key: str | None, depth: int) -> None:
        nonlocal total_bytes
        if depth > _SIGNAL_EVIDENCE_MAX_DEPTH or len(rows) >= _SIGNAL_EVIDENCE_MAX_NODES:
            raise ValueError("Strategy signal evidence exceeds its tree bound")
        kind = ("map" if isinstance(value, Mapping) else
                "tuple" if isinstance(value, tuple) else
                "list" if isinstance(value, list) else
                "null" if value is None else
                "bool" if type(value) is bool else
                "int" if type(value) is int else
                "float" if type(value) is float else
                "text" if type(value) is str else "unsupported")
        if kind == "unsupported":
            raise ValueError(f"Unsupported strategy signal evidence type: {type(value).__name__}")
        if kind == "int" and not -(2**63) <= value < 2**63:
            raise ValueError("Strategy signal evidence integer exceeds Int64")
        if kind == "float" and not math.isfinite(value):
            raise ValueError("Strategy signal evidence float must be finite")
        if key is not None and (not isinstance(key, str) or len(key) > 512):
            raise ValueError("Strategy signal evidence map key is invalid")
        if kind == "text" and len(value.encode("utf-8")) > 1_048_576:
            raise ValueError("Strategy signal evidence text exceeds its bound")
        total_bytes += (len(key.encode("utf-8")) if key is not None else 0)
        total_bytes += (len(value.encode("utf-8")) if kind == "text" else 0)
        if total_bytes > _SIGNAL_EVIDENCE_MAX_BYTES:
            raise ValueError("Strategy signal evidence exceeds its byte bound")
        node_id = str(uuid5(NAMESPACE_URL, f"{parent_record_id}:evidence:{len(rows)}"))
        rows.append(dict(record_id=node_id, run_id=run_id, event_month=event_month,
                         batch_id=batch_id, parent_record_id=parent_record_id,
                         parent_node_id=parent, ordinal=ordinal, map_key=key,
                         value_kind=kind,
                         value_text=value if kind == "text" else None,
                         value_int=value if kind == "int" else None,
                         value_float=value if kind == "float" else None,
                         value_bool=int(value) if kind == "bool" else None))
        if kind in {"map", "list", "tuple"}:
            identity = id(value)
            if identity in active:
                raise ValueError("Strategy signal evidence contains a cycle")
            active.add(identity)
            if kind == "map":
                if any(not isinstance(child_key, str) for child_key in value):
                    raise ValueError("Strategy signal evidence map keys must be strings")
                children = sorted(value.items())
            else:
                children = [(None, child) for child in value]
            for index, (child_key, child) in enumerate(children):
                visit(child, node_id, index, child_key, depth + 1)
            active.remove(identity)

    visit(metadata, None, 0, None, 0)
    return tuple(rows)


def recover_signal_evidence_nodes(rows: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
                                  *, parent_record_id: str) -> dict[str, Any]:
    """Reconstruct and validate a committed signal evidence tree."""
    if not rows or len(rows) > _SIGNAL_EVIDENCE_MAX_NODES:
        raise ValueError("Strategy signal evidence tree is absent or too large")
    total_bytes = sum(
        (len(row["map_key"].encode("utf-8")) if isinstance(row["map_key"], str) else 0)
        + (len(row["value_text"].encode("utf-8"))
           if isinstance(row["value_text"], str) else 0)
        for row in rows
    )
    if total_bytes > _SIGNAL_EVIDENCE_MAX_BYTES:
        raise ValueError("Strategy signal evidence exceeds its byte bound")
    indexed = {str(row["record_id"]): row for row in rows}
    if len(indexed) != len(rows) or any(str(row["parent_record_id"]) != parent_record_id for row in rows):
        raise ValueError("Strategy signal evidence has duplicate or foreign nodes")
    children: dict[str | None, list[Mapping[str, Any]]] = {}
    for row in rows:
        parent = row["parent_node_id"]
        parent = str(parent) if parent is not None else None
        children.setdefault(parent, []).append(row)
    if len(children.get(None, ())) != 1:
        raise ValueError("Strategy signal evidence requires one root")
    seen: set[str] = set()

    def build(row: Mapping[str, Any], depth: int) -> Any:
        node_id = str(row["record_id"])
        if depth > _SIGNAL_EVIDENCE_MAX_DEPTH or node_id in seen:
            raise ValueError("Strategy signal evidence has a cycle or excessive depth")
        seen.add(node_id)
        kind = str(row["value_kind"])
        descendants = sorted(children.get(node_id, ()), key=lambda child: int(child["ordinal"]))
        if [int(child["ordinal"]) for child in descendants] != list(range(len(descendants))):
            raise ValueError("Strategy signal evidence child order is incomplete")
        values = [row[name] for name in ("value_text", "value_int", "value_float", "value_bool")]
        expected = {"text": 0, "int": 1, "float": 2, "bool": 3}.get(kind)
        if any(value is not None for index, value in enumerate(values) if index != expected):
            raise ValueError("Strategy signal evidence scalar columns conflict")
        if kind in {"map", "list", "tuple"}:
            if kind == "map":
                keys = [child["map_key"] for child in descendants]
                if any(not isinstance(key, str) for key in keys) or len(set(keys)) != len(keys):
                    raise ValueError("Strategy signal evidence map keys are invalid")
                return {key: build(child, depth + 1) for key, child in zip(keys, descendants)}
            if any(child["map_key"] is not None for child in descendants):
                raise ValueError("Strategy signal evidence list has map keys")
            items = [build(child, depth + 1) for child in descendants]
            return tuple(items) if kind == "tuple" else items
        if descendants or kind not in {"null", "bool", "int", "float", "text"}:
            raise ValueError("Strategy signal evidence leaf is invalid")
        value = values[expected] if expected is not None else None
        if kind == "bool" and value not in {0, 1}:
            raise ValueError("Strategy signal evidence bool is invalid")
        if expected is not None and value is None:
            raise ValueError("Strategy signal evidence scalar is absent")
        return bool(value) if kind == "bool" else value

    root = children[None][0]
    if root["value_kind"] != "map" or root["map_key"] is not None or int(root["ordinal"]) != 0:
        raise ValueError("Strategy signal evidence root is invalid")
    recovered = build(root, 0)
    if len(seen) != len(rows):
        raise ValueError("Strategy signal evidence has unreachable nodes")
    return recovered


def load_committed_signal_evidence(client: Any, prefix: CommittedPrefix,
                                   record_id: str) -> dict[str, Any]:
    """Recover one typed signal tree only through a verified commit prefix."""
    if not isinstance(prefix, CommittedPrefix) or not prefix.batch_ids:
        raise ValueError("Signal evidence recovery requires a verified prefix")
    normalized = str(UUID(record_id))
    batches = ",".join(f"toUUID({_literal(value)})" for value in prefix.batch_ids)
    signal_columns = ",".join(name for name, _ in next(
        table for table in TABLES if table.name == "trading_strategy_signal_v1").columns)
    signals = _rows(client, f"SELECT {signal_columns} FROM arte.trading_strategy_signal_v1 "
                    f"WHERE run_id={_literal(prefix.run_id)} AND record_id=toUUID({_literal(normalized)}) "
                    f"AND batch_id IN ({batches}) FORMAT JSONEachRow")
    if len(signals) != 1:
        raise ValueError("Committed strategy signal is missing or ambiguous")
    signal = signals[0]
    node_columns = ",".join(name for name, _ in next(
        table for table in TABLES if table.name ==
        "trading_strategy_signal_evidence_node_v1").columns)
    nodes = _rows(client, f"SELECT {node_columns} "
                  "FROM arte.trading_strategy_signal_evidence_node_v1 "
                  f"WHERE run_id={_literal(prefix.run_id)} "
                  f"AND parent_record_id=toUUID({_literal(normalized)}) "
                  f"AND batch_id IN ({batches}) FORMAT JSONEachRow")
    if len(nodes) != int(signal["evidence_node_count"]):
        raise ValueError("Committed signal evidence node count changed")
    for name, rows in (("trading_strategy_signal_v1", signals),
                       ("trading_strategy_signal_evidence_node_v1", nodes)):
        for row in rows:
            content = {key: value for key, value in row.items() if key != "content_hash"}
            digest = sha256(canonical_json(_canonical_typed_content(
                name, content, stored_utc=True)).encode("utf-8")).hexdigest()
            if digest != row["content_hash"] or row["batch_id"] != signal["batch_id"]:
                raise ValueError("Committed signal evidence differs from its typed hash or batch")
    if not nodes:
        # Earlier typed signals could only publish empty metadata. The
        # additive column defaults their node count to zero on upgrade.
        return {}
    return recover_signal_evidence_nodes(nodes, parent_record_id=normalized)


@dataclass(frozen=True, slots=True)
class FillDetails:
    execution: dict[str, Any]
    commission: dict[str, Any] | None


def project_journal_record(
    record: JournalRecord, *, run_month: date, attempt_id: str,
    batch_id: str, prior_batch_id: str, source_cursor: str,
    expected_config: dict[str, Any] | None = None,
    expected_mode: str | None = None,
    fixed_market_parent_plan: Any | None = None,
    fixed_market_execution_plan: Any | None = None,
    expected_market_start: datetime | None = None,
) -> TypedJournalBatch:
    """Strict shared entry point for existing live/Backtest journal records.

    Domain-object projections (orders, fills, signals, intents) must receive
    their original typed objects. A flattened payload is not sufficient to
    reconstruct arbitrary broker evidence, so unsupported records fail here.
    """
    identity = dict(run_month=run_month, attempt_id=attempt_id,
                    batch_id=batch_id, prior_batch_id=prior_batch_id,
                    source_cursor=source_cursor)
    kind = (record.category, record.entity_type)
    if kind == ("lifecycle", "run"):
        return runtime_lifecycle_batch(record, **identity,
                                       expected_config=expected_config)
    if kind in {("broker", "connection_state"), ("risk", "risk_snapshot")}:
        return operational_fault_batch(record, **identity)
    if kind == ("risk", "continuous_risk_state"):
        if expected_mode is None:
            raise ValueError("Continuous risk projection requires the pinned run mode")
        return account_risk_batch(record, **identity, expected_mode=expected_mode)
    if kind in {("strategy_decision", "intent_rejection"),
                ("strategy_decision", "intent_deferral")}:
        return intent_decision_batch(record, **identity)
    if kind == ("checkpoint", "market_boundary"):
        return backtest_cursor_batch(record, **identity)
    if kind == ("data_authority", "source_revision"):
        if (fixed_market_parent_plan is None
                or fixed_market_execution_plan is None
                or expected_market_start is None):
            raise ValueError("Fixed market authority requires pinned parent/execution plans")
        from src.backend.backtest_fixed_market_authority import project_fixed_market_authority
        projected = project_fixed_market_authority(
            record, parent_plan=fixed_market_parent_plan,
            execution_plan=fixed_market_execution_plan,
            expected_event_time=expected_market_start,
        )
        if record.recorded_at.tzinfo is None:
            raise ValueError("Fixed market authority recorded time is naive")
        month = record.event_time.astimezone(timezone.utc).strftime("%Y-%m-01")
        event = {
            "run_id": record.run_id, "event_month": month,
            "attempt_id": attempt_id, "batch_id": batch_id,
            "record_id": record.record_id, "sequence": record.sequence,
            "event_time": projected.source_event_time.isoformat(),
            "recorded_at": record.recorded_at.astimezone(timezone.utc).isoformat(),
            "category": record.category, "entity_type": record.entity_type,
            "entity_id": record.entity_id, "account_id": "",
            "correlation_id": str(record.payload.get("correlation_id") or ""),
            "causation_id": str(record.payload.get("causation_id") or ""),
        }
        detail = {
            "record_id": record.record_id, "run_id": record.run_id,
            "event_month": month, "batch_id": batch_id, "account_id": "",
            "execution_plan_token": projected.execution_plan_token,
            "parent_market_plan_token": projected.parent_market_plan_token,
        }
        return TypedJournalBatch(
            record.run_id, run_month, attempt_id, batch_id, prior_batch_id,
            record.sequence, record.sequence, source_cursor, "running", (event,),
            backtest_market_authorities=(detail,),
        )
    if kind == ("market_discovery_signal", "signal_occurrence"):
        raise ValueError("Squeeze episode needs operator-provisioned Backtest-only journal family")
    if kind == ("strategy_decision", "signal"):
        from dataclasses import fields
        payload = dict(record.payload)
        signal_fields = {field.name for field in fields(StrategySignal)}
        required = signal_fields | {"strategy_id", "strategy_revision"}
        if set(payload) - {"correlation_id", "causation_id"} != required:
            raise ValueError("Strategy signal record has missing or unmodeled fields")
        event_time = payload["event_time"]
        if isinstance(event_time, str):
            event_time = datetime.fromisoformat(event_time)
        if (not isinstance(event_time, datetime) or event_time.tzinfo is None
                or record.event_time.tzinfo is None
                or event_time.astimezone(timezone.utc)
                != record.event_time.astimezone(timezone.utc)
                or payload["signal_id"] != record.entity_id
                or type(payload["strategy_revision"]) is not int
                or payload["strategy_revision"] < 0
                or not isinstance(payload["strategy_id"], str)
                or not payload["strategy_id"]):
            raise ValueError("Strategy signal record identity is invalid")
        signal_values = {key: payload[key] for key in signal_fields}
        signal_values["event_time"] = event_time
        metadata = payload["metadata"]
        common_metadata = {"assignment_id", "reference_price", "status",
                           "reason_code", "reason_detail", "correlation_id",
                           "causation_id"}
        if not isinstance(metadata, dict) or set(metadata) not in (set(), common_metadata):
            raise ValueError("Backtest signal metadata lacks a concrete typed catalog")
        if metadata and (any(not isinstance(metadata[key], str) or not metadata[key]
                             for key in common_metadata - {"reference_price"})
                         or metadata["reason_code"] != payload["reason"]
                         or metadata["correlation_id"] != payload.get("correlation_id")
                         or metadata["causation_id"] != payload.get("causation_id")):
            raise ValueError("Backtest signal decision metadata is invalid")
        if (any(not isinstance(payload.get(key), str) for key in (
                "signal_id", "signal_type", "ticker", "action", "direction",
                "reason", "working_timeframe", "strategy_id"))
                or not isinstance(payload["ticker"], str)
                or payload["ticker"] != payload["ticker"].upper()
                or not isinstance(payload["source_signal_ids"], (tuple, list))
                or not isinstance(payload["metadata"], dict)
                or any(not isinstance(payload.get(key), str) for key in (
                    "correlation_id", "causation_id") if key in payload)):
            raise ValueError("Backtest signal fields require exact typed values")
        signal = StrategySignal(**signal_values)
        return strategy_signal_batch(
            signal, run_id=record.run_id, run_month=run_month,
            account_id=record.account_id,
            strategy_id=payload["strategy_id"],
            strategy_revision=payload["strategy_revision"],
            attempt_id=attempt_id, batch_id=batch_id,
            prior_batch_id=prior_batch_id, sequence=record.sequence,
            source_cursor=source_cursor, run_status="running",
            recorded_at=record.recorded_at, record_id=record.record_id,
            correlation_id=str(payload.get("correlation_id") or ""),
            causation_id=str(payload.get("causation_id") or ""),
            persist_metadata_nodes=False,
            decision_metadata=metadata or None,
        )
    if kind == ("resource_lease", "prepared_v7_stream"):
        return prepared_v7_lease_batch(record, **identity)
    if kind == ("execution", "fill"):
        if (record.event_time.tzinfo is None or record.recorded_at.tzinfo is None
                or any(not isinstance(record.payload[key], str)
                       for key in ("correlation_id", "causation_id")
                       if key in record.payload)):
            raise ValueError("Fill journal source clocks or lineage are invalid")
        source = {key: value for key, value in record.payload.items()
                  if key not in {"correlation_id", "causation_id"}}
        execution = parse_ibkr_execution(source)
        if (execution.commission is not None
                or execution.execution_id != record.entity_id
                or execution.account != record.account_id
                or execution.trade_time != record.event_time.astimezone(timezone.utc)):
            raise ValueError(
                "Fill journal record needs exact identity and a separate commission event")
        month = execution.trade_time.strftime("%Y-%m-01")
        if run_month.isoformat() != month:
            raise ValueError("Fill journal partition differs from source trade time")
        details = broker_fill_details(
            execution, run_id=record.run_id, event_month=month,
            batch_id=batch_id, execution_record_id=record.record_id,
            commission_record_id=None, received_at=record.recorded_at,
        )
        event = {
            "run_id": record.run_id, "event_month": month,
            "attempt_id": attempt_id, "batch_id": batch_id,
            "record_id": record.record_id, "sequence": record.sequence,
            "event_time": execution.trade_time.isoformat(),
            "recorded_at": record.recorded_at.astimezone(timezone.utc).isoformat(),
            "category": "execution", "entity_type": "fill",
            "entity_id": record.entity_id, "account_id": record.account_id,
            "correlation_id": str(record.payload.get("correlation_id") or ""),
            "causation_id": str(record.payload.get("causation_id") or ""),
        }
        return TypedJournalBatch(
            record.run_id, run_month, attempt_id, batch_id, prior_batch_id,
            record.sequence, record.sequence, source_cursor, "running", (event,),
            executions=(details.execution,),
        )
    raise ValueError(
        f"Journal record {record.category}/{record.entity_type} lacks a typed projection"
    )


def prepared_v7_lease_batch(
    record: JournalRecord, *, run_month: date, attempt_id: str,
    batch_id: str, prior_batch_id: str, source_cursor: str,
) -> TypedJournalBatch:
    """Preserve the fixed Backtest's prepared V7 ownership transition."""
    if ((record.category, record.entity_type) != ("resource_lease", "prepared_v7_stream")
            or record.account_id or not record.entity_id
            or record.event_time.tzinfo is None or record.recorded_at.tzinfo is None):
        raise ValueError("Prepared V7 lease journal envelope is invalid")
    payload = dict(record.payload)
    required = {"stream_id", "owner_run_id", "owner_pid", "phase",
                "error_type", "recorded_at"}
    if set(payload) - {"correlation_id", "causation_id", "intent_id"} != required:
        raise ValueError("Prepared V7 lease has missing or unmodeled fields")
    phase = payload["phase"]
    error_type = payload["error_type"]
    pid = payload["owner_pid"]
    if (payload["stream_id"] != record.entity_id
            or payload["owner_run_id"] != record.run_id
            or type(pid) is not int or not 0 < pid < 2**32
            or phase not in {"acquiring", "acquired", "release_failed", "released"}
            or (phase == "release_failed") != (error_type is not None)
            or (error_type is not None and
                (not isinstance(error_type, str) or not error_type))):
        raise ValueError("Prepared V7 lease identity or phase is invalid")
    recorded = datetime.fromisoformat(str(payload["recorded_at"]))
    if recorded.tzinfo is None or recorded > record.recorded_at:
        raise ValueError("Prepared V7 lease receipt time is invalid")
    at = record.event_time.astimezone(timezone.utc).isoformat()
    month = record.event_time.astimezone(timezone.utc).strftime("%Y-%m-01")
    event = dict(run_id=record.run_id, event_month=month,
                 attempt_id=attempt_id, batch_id=batch_id,
                 record_id=record.record_id, sequence=record.sequence,
                 event_time=at, recorded_at=record.recorded_at.astimezone(timezone.utc).isoformat(),
                 category=record.category, entity_type=record.entity_type,
                 entity_id=record.entity_id, account_id="",
                 correlation_id=str(payload.get("correlation_id") or ""),
                 causation_id=str(payload.get("causation_id") or ""))
    lease = dict(record_id=record.record_id, run_id=record.run_id,
                 event_month=month, batch_id=batch_id, account_id="",
                 stream_id=record.entity_id, owner_pid=pid, phase=phase,
                 error_type=error_type, source_event_time=at,
                 lease_recorded_at=recorded.astimezone(timezone.utc).isoformat())
    return TypedJournalBatch(
        record.run_id, run_month, attempt_id, batch_id, prior_batch_id,
        record.sequence, record.sequence, source_cursor, "running", (event,),
        prepared_v7_leases=(lease,))


def project_portfolio_admission_records(
    records: tuple[tuple[str, str, str, dict[str, Any]], ...], *,
    run_id: str, run_month: date, attempt_id: str, batch_id: str,
    prior_batch_id: str, first_sequence: int, source_cursor: str,
) -> TypedJournalBatch:
    """Project staged Portfolio admission facts to normalized journal families."""
    from src.trading_runtime.portfolio import PortfolioReservation

    if not records or any(kind not in {"portfolio_decision", "portfolio_reservation"}
                          for kind, _, _, _ in records):
        raise ValueError("Portfolio admission has unmodeled staged records")
    events = []
    decisions = []
    reasons = []
    reservations = []
    metric_names = ("net_liquidation", "available_funds", "buying_power",
                    "gross_exposure", "net_exposure", "reserved_notional",
                    "open_risk", "daily_loss", "drawdown", "position_count")
    reservation_fields = {field.name for field in fields(PortfolioReservation)}
    for ordinal, (kind, entity_id, account_id, payload) in enumerate(records):
        if not account_id or not isinstance(payload, Mapping):
            raise ValueError("Portfolio admission fact lacks account or typed content")
        record_id = str(uuid5(NAMESPACE_URL, f"{batch_id}:portfolio:{ordinal}"))
        if kind == "portfolio_decision":
            expected = {"event", "ticker", "action", "decision_id", "request_id",
                        "account_key", "account_id", "policy_id", "policy_revision",
                        "snapshot_id", "status", "requested_quantity",
                        "approved_quantity", "approved_notional", "planned_loss",
                        "reservation_id", "reasons", "metrics_before", "metrics_after",
                        "decided_at", "correlation_id", "causation_id"}
            if (set(payload) != expected or payload["event"] != "portfolio_decision"
                    or payload["decision_id"] != entity_id
                    or payload["account_id"] != account_id
                    or payload["status"] not in {"approved", "resized"}
                    or not isinstance(payload["reasons"], (tuple, list))
                    or len(payload["reasons"]) > 65535
                    or any(not isinstance(reason, str) or not reason for reason in payload["reasons"])
                    or any(not isinstance(payload[name], Mapping)
                           or set(payload[name]) != set(metric_names)
                           for name in ("metrics_before", "metrics_after"))):
                raise ValueError("Portfolio decision admission fact is incomplete")
            at = payload["decided_at"]
            if not isinstance(at, datetime) or at.tzinfo is None:
                raise ValueError("Portfolio decision time is not causal")
            detail = {
                "record_id": record_id, "run_id": run_id,
                "event_month": at.astimezone(timezone.utc).strftime("%Y-%m-01"),
                "batch_id": batch_id, "account_id": account_id,
                **{key: payload[key] for key in (
                    "decision_id", "request_id", "account_key", "ticker", "action",
                    "policy_id", "policy_revision", "snapshot_id", "status",
                    "reservation_id")},
                **{key: _exact_decimal(payload[key], _MEASURE_SCALE) for key in (
                    "requested_quantity", "approved_quantity", "approved_notional", "planned_loss")},
                "reason_count": len(payload["reasons"]), "decided_at": at.isoformat(),
                **{f"{phase}_{metric}": _exact_decimal(payload[f"metrics_{phase}"][metric], _MEASURE_SCALE)
                   for phase in ("before", "after") for metric in metric_names},
            }
            decisions.append(detail)
            for reason_index, reason in enumerate(payload["reasons"]):
                reasons.append({"record_id": str(uuid5(NAMESPACE_URL, f"{record_id}:reason:{reason_index}")),
                                "run_id": run_id, "event_month": detail["event_month"],
                                "batch_id": batch_id, "parent_record_id": record_id,
                                "account_id": account_id, "ordinal": reason_index,
                                "reason": reason})
        else:
            extra = {"event", "correlation_id", "causation_id"}
            if (set(payload) - extra != reservation_fields
                    or payload["event"] not in {"reservation_created", "cash_tranche_budget_reserved"}
                    or payload["reservation_id"] != entity_id
                    or payload["account_id"] != account_id):
                raise ValueError("Portfolio reservation admission fact is incomplete")
            at = payload["created_at"]
            if not isinstance(at, datetime) or at.tzinfo is None:
                raise ValueError("Portfolio reservation time is not causal")
            numeric = ("quantity", "remaining_quantity", "reference_price",
                       "reserved_notional", "reserved_planned_risk", "filled_quantity",
                       "reserved_entry_fees", "cash_tranche_size", "cash_tranche_budget")
            detail = {
                "record_id": record_id, "run_id": run_id,
                "event_month": at.astimezone(timezone.utc).strftime("%Y-%m-01"),
                "batch_id": batch_id, "account_id": account_id,
                "event": payload["event"],
                **{key: payload[key] for key in reservation_fields - set(numeric) - {"created_at"}},
                **{key: _exact_decimal(payload[key], _MEASURE_SCALE) for key in numeric},
                "created_at": at.isoformat(),
            }
            reservations.append(detail)
        events.append({
            "run_id": run_id, "event_month": at.astimezone(timezone.utc).strftime("%Y-%m-01"),
            "attempt_id": attempt_id, "batch_id": batch_id, "record_id": record_id,
            "sequence": first_sequence + ordinal, "event_time": at.isoformat(),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "category": "portfolio_management", "entity_type": kind,
            "entity_id": entity_id, "account_id": account_id,
            "correlation_id": str(payload.get("correlation_id") or ""),
            "causation_id": str(payload.get("causation_id") or ""),
        })
    if not decisions or not reservations:
        raise ValueError("Portfolio admission needs decision and reservation facts")
    return TypedJournalBatch(
        run_id, run_month, attempt_id, batch_id, prior_batch_id,
        first_sequence, first_sequence + len(events) - 1, source_cursor,
        "running", tuple(events), portfolio_decisions=tuple(decisions),
        portfolio_decision_reasons=tuple(reasons),
        portfolio_reservation_events=tuple(reservations))


def portfolio_reconciliation_hash(captured: Any) -> str:
    """Bind the transition to every normalized snapshot difference row."""
    rows = sorted((asdict(row) for row in captured.reconciliation),
                  key=lambda row: (row["account_key"], row["ticker"]))
    if any(row["account_key"] != captured.account_key for row in rows):
        raise ValueError("Portfolio reconciliation mixes account keys")
    return sha256(canonical_json(rows).encode("utf-8")).hexdigest()


def project_portfolio_reconciliation_records(
    records: tuple[tuple[str, str, str, dict[str, Any]], ...], *,
    captured: Any, run_month: date, attempt_id: str, batch_id: str,
    prior_batch_id: str, first_sequence: int, source_cursor: str,
) -> TypedJournalBatch:
    """Seal exactly one broker reconciliation fact against its recovery image."""
    if len(records) != 1:
        raise ValueError("Broker sync requires exactly one typed reconciliation fact")
    kind, entity_id, account_id, payload = records[0]
    rows = sorted((asdict(row) for row in captured.reconciliation),
                  key=lambda row: (row["account_key"], row["ticker"]))
    if (kind != "portfolio_reconciliation" or entity_id != captured.account_key
            or account_id != captured.account_id or not isinstance(payload, Mapping)
            or set(payload) != {"event", "snapshot_id", "difference_count", "differences"}
            or payload["event"] != "portfolio_reconciliation_completed"
            or payload["snapshot_id"] != captured.broker_snapshot_id
            or type(payload["difference_count"]) is not int
            or payload["difference_count"] != len(rows)
            or payload["differences"] != rows
            or captured.observed_at is None or captured.observed_at.tzinfo is None):
        raise ValueError("Broker reconciliation fact differs from captured snapshot")
    at = captured.observed_at.astimezone(timezone.utc).isoformat()
    month = captured.observed_at.astimezone(timezone.utc).strftime("%Y-%m-01")
    record_id = str(uuid5(NAMESPACE_URL, f"{batch_id}:portfolio:reconciliation"))
    event = dict(run_id=captured.run_id, event_month=month,
                 attempt_id=attempt_id, batch_id=batch_id, record_id=record_id,
                 sequence=first_sequence, event_time=at,
                 recorded_at=datetime.now(timezone.utc).isoformat(),
                 category="portfolio_management", entity_type=kind,
                 entity_id=entity_id, account_id=account_id,
                 correlation_id="", causation_id="")
    detail = dict(record_id=record_id, run_id=captured.run_id,
                  event_month=month, batch_id=batch_id, account_id=account_id,
                  account_key=entity_id, snapshot_id=captured.broker_snapshot_id,
                  difference_count=len(rows),
                  difference_hash=portfolio_reconciliation_hash(captured),
                  source_event_time=at)
    return TypedJournalBatch(
        captured.run_id, run_month, attempt_id, batch_id, prior_batch_id,
        first_sequence, first_sequence, source_cursor, "running", (event,),
        portfolio_reconciliation_events=(detail,))


def backtest_cursor_batch(
    record: JournalRecord, *, run_month: date, attempt_id: str,
    batch_id: str, prior_batch_id: str, source_cursor: str,
) -> TypedJournalBatch:
    """Normalize a completed fixed Backtest market/frame cursor."""
    if ((record.category, record.entity_type) != ("checkpoint", "market_boundary")
            or record.account_id or record.event_time.tzinfo is None
            or record.recorded_at.tzinfo is None):
        raise ValueError("Backtest cursor envelope is invalid")
    payload = dict(record.payload)
    required = {"session_date", "boundary_ms", "market_sequence",
                "frame_as_of", "frame_ticker", "frame_timeframe", "frame_sequence"}
    if set(payload) - {"correlation_id", "causation_id"} != required:
        raise ValueError("Backtest cursor has missing or unmodeled fields")
    day = payload["session_date"]
    if isinstance(day, str):
        day = date.fromisoformat(day)
    if not isinstance(day, date) or isinstance(day, datetime):
        raise ValueError("Backtest cursor session date is invalid")
    boundary = payload["boundary_ms"]
    market_sequence = payload["market_sequence"]
    if (type(boundary) is not int or not 0 <= boundary <= 57_600_000
            or type(market_sequence) is not int or market_sequence < 0
            or record.entity_id != f"{day.isoformat()}:{boundary}"
            or source_cursor != record.entity_id):
        raise ValueError("Backtest market boundary identity is invalid")
    from src.backend.backtest_market_data import market_day_boundary
    if record.event_time.astimezone(timezone.utc) != market_day_boundary(
            day, boundary).astimezone(timezone.utc):
        raise ValueError("Backtest cursor event time differs from its completed boundary")
    frame = tuple(payload[key] for key in (
        "frame_as_of", "frame_ticker", "frame_timeframe", "frame_sequence"))
    if any(value is not None for value in frame):
        if (any(value is None for value in frame)
                or not isinstance(frame[0], str)
                or not isinstance(frame[1], str) or not frame[1]
                or not isinstance(frame[2], str) or not frame[2]
                or type(frame[3]) is not int or not 0 <= frame[3] <= market_sequence):
            raise ValueError("Backtest frame cursor is incomplete")
        frame_at = datetime.fromisoformat(frame[0])
        if (frame_at.tzinfo is None
                or frame_at.astimezone(timezone.utc) > record.event_time.astimezone(timezone.utc)):
            raise ValueError("Backtest frame cursor is not causal")
    else:
        frame_at = None
    at = record.event_time.astimezone(timezone.utc).isoformat()
    event_month = record.event_time.astimezone(timezone.utc).strftime("%Y-%m-01")
    event = {
        "run_id": record.run_id, "event_month": event_month,
        "attempt_id": attempt_id, "batch_id": batch_id,
        "record_id": record.record_id, "sequence": record.sequence,
        "event_time": at, "recorded_at": record.recorded_at.astimezone(timezone.utc).isoformat(),
        "category": "checkpoint", "entity_type": "market_boundary",
        "entity_id": record.entity_id, "account_id": "",
        "correlation_id": str(payload.get("correlation_id") or ""),
        "causation_id": str(payload.get("causation_id") or ""),
    }
    cursor = {
        "record_id": record.record_id, "run_id": record.run_id,
        "event_month": event_month, "batch_id": batch_id, "account_id": "",
        "session_date": day.isoformat(), "boundary_ms": boundary,
        "market_sequence": market_sequence,
        "frame_as_of": frame_at.astimezone(timezone.utc).isoformat() if frame_at else None,
        "frame_ticker": frame[1], "frame_timeframe": frame[2],
        "frame_sequence": frame[3],
    }
    return TypedJournalBatch(
        record.run_id, run_month, attempt_id, batch_id, prior_batch_id,
        record.sequence, record.sequence, source_cursor, "running", (event,),
        backtest_cursors=(cursor,),
    )


def backtest_cursor_record_fields(
    market_cursor: Mapping[str, Any], frame_cursor: Mapping[str, Any],
    *, completed_at: datetime,
) -> tuple[str, dict[str, Any]]:
    """Freeze a controller boundary as a normalized journal event identity."""
    if (not isinstance(market_cursor, Mapping) or
            set(market_cursor) != {"session_date", "boundary_ms", "sequence"} or
            not isinstance(frame_cursor, Mapping) or completed_at.tzinfo is None):
        raise ValueError("Fixed Backtest cursor is incomplete")
    day = date.fromisoformat(str(market_cursor["session_date"]))
    boundary = market_cursor["boundary_ms"]
    sequence = market_cursor["sequence"]
    from src.backend.backtest_market_data import market_day_boundary
    if (type(boundary) is not int or type(sequence) is not int or sequence < 0
            or completed_at.astimezone(timezone.utc) != market_day_boundary(
                day, boundary).astimezone(timezone.utc)):
        raise ValueError("Fixed Backtest cursor differs from its completed boundary")
    if frame_cursor:
        if set(frame_cursor) != {"as_of", "ticker", "timeframe", "sequence"}:
            raise ValueError("Fixed Backtest frame cursor is incomplete")
        frame_at = datetime.fromisoformat(str(frame_cursor["as_of"]).replace("Z", "+00:00"))
        frame_sequence = frame_cursor["sequence"]
        if (frame_at.tzinfo is None or frame_at > completed_at
                or not str(frame_cursor["ticker"]) or not str(frame_cursor["timeframe"])
                or type(frame_sequence) is not int or not 0 <= frame_sequence <= sequence):
            raise ValueError("Fixed Backtest frame cursor is not causal")
    else:
        frame_at = None
        frame_sequence = None
    entity_id = f"{day.isoformat()}:{boundary}"
    return entity_id, {
        "session_date": day.isoformat(), "boundary_ms": boundary,
        "market_sequence": sequence,
        "frame_as_of": frame_at.astimezone(timezone.utc).isoformat() if frame_at else None,
        "frame_ticker": str(frame_cursor["ticker"]) if frame_at else None,
        "frame_timeframe": str(frame_cursor["timeframe"]) if frame_at else None,
        "frame_sequence": frame_sequence,
    }


def load_latest_backtest_cursor(client: Any, prefix: VerifiedPrefix) -> dict[str, Any] | None:
    """Read the latest cursor from a previously verified committed prefix."""
    if not isinstance(prefix, (CommittedPrefix, V2CommittedPrefix)) or not prefix.batch_ids:
        raise ValueError("Backtest cursor recovery requires a verified prefix")
    rows = _rows(client,
        "SELECT c.*,e.sequence AS event_sequence,e.category AS event_category,"
        "e.entity_type AS event_entity_type,e.entity_id AS event_entity_id "
        "FROM arte.trading_backtest_cursor_v1 AS c "
        "INNER JOIN arte.trading_event_v1 AS e "
        "ON c.run_id=e.run_id AND c.batch_id=e.batch_id AND c.record_id=e.record_id "
        f"WHERE c.run_id={_literal(prefix.run_id)} "
        f"AND e.sequence<={int(prefix.last_sequence)} "
        f"{_committed_batch_filter(prefix, batch_column='c.batch_id')}"
        "ORDER BY e.sequence DESC LIMIT 2 FORMAT JSONEachRow")
    if not rows:
        return None
    if (len(rows) > 1
            and int(rows[0]["event_sequence"]) == int(rows[1]["event_sequence"])):
        raise RuntimeError("Backtest cursor recovery has an ambiguous latest row")
    row = dict(rows[0])
    sequence = int(row.pop("event_sequence"))
    category, entity_type = row.pop("event_category"), row.pop("event_entity_type")
    entity_id = row.pop("event_entity_id")
    if ((category, entity_type) != ("checkpoint", "market_boundary")
            or entity_id != f"{row['session_date']}:{int(row['boundary_ms'])}"
            or sequence > prefix.last_sequence
            or str(row["batch_id"]) not in prefix.batch_ids):
        raise RuntimeError("Backtest cursor recovery differs from its event")
    content = {key: value for key, value in row.items() if key != "content_hash"}
    canonical = _canonical_typed_content(
        "trading_backtest_cursor_v1", content, stored_utc=True)
    digest = sha256(canonical_json(canonical).encode("utf-8")).hexdigest()
    if digest != str(row["content_hash"]):
        raise RuntimeError("Backtest cursor recovery differs from its hash")
    return {**canonical, "event_sequence": sequence}


def runtime_lifecycle_batch(
    record: JournalRecord, *, run_month: date, attempt_id: str,
    batch_id: str, prior_batch_id: str, source_cursor: str,
    expected_config: dict[str, Any] | None = None,
) -> TypedJournalBatch:
    """Project the runtime's start/finish record without persisting config twice."""
    if (record.category, record.entity_type, record.entity_id, record.account_id) != (
        "lifecycle", "run", record.run_id, "",
    ):
        raise ValueError("Run lifecycle record identity is invalid")
    if record.event_time.tzinfo is None or record.recorded_at.tzinfo is None:
        raise ValueError("Run lifecycle timestamps must be timezone-aware")
    payload = dict(record.payload)
    status = payload.get("status")
    if status not in {"running", "completed", "stopped", "failed"}:
        raise ValueError("Run lifecycle status is invalid")
    common = {"status", "correlation_id", "causation_id"}
    if status == "running":
        if set(payload) - common != {"config"} or expected_config is None:
            raise ValueError("Run start lacks its pinned typed configuration")
        if canonical_json(payload["config"]) != canonical_json(expected_config):
            raise ValueError("Run start configuration differs from the typed run context")
        processed_events = None
    else:
        if set(payload) - common != {"processed_events"}:
            raise ValueError("Run finish has unmodeled lifecycle evidence")
        processed_events = payload["processed_events"]
        if type(processed_events) is not int or processed_events < 0:
            raise ValueError("Run finish processed-event count is invalid")
    at = record.event_time.astimezone(timezone.utc).isoformat()
    received = record.recorded_at.astimezone(timezone.utc).isoformat()
    event_month = record.event_time.astimezone(timezone.utc).strftime("%Y-%m-01")
    event = {
        "run_id": record.run_id, "event_month": event_month,
        "attempt_id": attempt_id, "batch_id": batch_id,
        "record_id": record.record_id, "sequence": record.sequence,
        "event_time": at, "recorded_at": received,
        "category": "lifecycle", "entity_type": "run",
        "entity_id": record.run_id, "account_id": "",
        "correlation_id": str(payload.get("correlation_id") or ""),
        "causation_id": str(payload.get("causation_id") or ""),
    }
    transition = {
        "record_id": record.record_id, "run_id": record.run_id,
        "event_month": event_month, "batch_id": batch_id,
        "account_id": "", "status": status,
        "processed_events": processed_events, "source_event_time": at,
    }
    return TypedJournalBatch(
        record.run_id, run_month, attempt_id, batch_id, prior_batch_id,
        record.sequence, record.sequence, source_cursor, status, (event,),
        run_transitions=(transition,),
    )


def operational_fault_batch(
    record: JournalRecord, *, run_month: date, attempt_id: str,
    batch_id: str, prior_batch_id: str, source_cursor: str,
) -> TypedJournalBatch:
    """Project live broker disconnect and risk-refresh faults without JSON."""
    expected_status = {
        ("broker", "connection_state"): "disconnected",
        ("risk", "risk_snapshot"): "stale",
    }.get((record.category, record.entity_type))
    if (expected_status is None or record.entity_id != record.run_id
            or record.account_id or record.event_time.tzinfo is None
            or record.recorded_at.tzinfo is None):
        raise ValueError("Operational fault identity or time is invalid")
    payload = dict(record.payload)
    if (set(payload) - {"correlation_id", "causation_id"}
            != {"status", "error", "entries_frozen"}
            or payload["status"] != expected_status
            or not isinstance(payload["error"], str)
            or payload["entries_frozen"] is not True):
        raise ValueError("Operational fault has unmodeled or inconsistent evidence")
    at = record.event_time.astimezone(timezone.utc).isoformat()
    received = record.recorded_at.astimezone(timezone.utc).isoformat()
    event_month = record.event_time.astimezone(timezone.utc).strftime("%Y-%m-01")
    event = {
        "run_id": record.run_id, "event_month": event_month,
        "attempt_id": attempt_id, "batch_id": batch_id,
        "record_id": record.record_id, "sequence": record.sequence,
        "event_time": at, "recorded_at": received,
        "category": record.category, "entity_type": record.entity_type,
        "entity_id": record.run_id, "account_id": "",
        "correlation_id": str(payload.get("correlation_id") or ""),
        "causation_id": str(payload.get("causation_id") or ""),
    }
    fault = {
        "record_id": record.record_id, "run_id": record.run_id,
        "event_month": event_month, "batch_id": batch_id,
        "account_id": "", "status": expected_status,
        "error": payload["error"], "entries_frozen": 1,
        "source_event_time": at,
    }
    return TypedJournalBatch(
        record.run_id, run_month, attempt_id, batch_id, prior_batch_id,
        record.sequence, record.sequence, source_cursor, "running", (event,),
        operational_faults=(fault,),
    )


def account_risk_batch(
    record: JournalRecord, *, run_month: date, attempt_id: str,
    batch_id: str, prior_batch_id: str, source_cursor: str,
    expected_mode: str,
) -> TypedJournalBatch:
    """Project the fixed risk metric set and ordered reasons without a map column."""
    if (record.category != "risk" or record.entity_type != "continuous_risk_state"
            or not record.account_id or record.entity_id != record.account_id
            or record.event_time.tzinfo is None or record.recorded_at.tzinfo is None):
        raise ValueError("Continuous risk record identity or time is invalid")
    payload = dict(record.payload)
    if not _RISK_SOURCE_FIELDS.issubset(payload) or set(payload) - _RISK_SOURCE_FIELDS - {
        "enforced", "mode", "correlation_id", "causation_id",
    }:
        raise ValueError("Continuous risk record has unmodeled fields")
    enforced = "enforced" not in payload
    if not enforced and (payload["enforced"] is not False
                         or payload.get("mode") != expected_mode
                         or payload["state"] != "normal"):
        raise ValueError("Disabled risk evaluation differs from its run mode")
    if enforced and "mode" in payload:
        raise ValueError("Enforced risk evaluation has unexpected mode evidence")
    if (payload["account_id"] != record.account_id
            or not isinstance(payload["account_key"], str)
            or not payload["account_key"]
            or payload["state"] not in {"normal", "entries_paused", "reduce_only",
                                        "emergency_exit", "reconciling", "fully_blocked"}):
        raise ValueError("Continuous risk account or state is invalid")
    observed_at = payload["observed_at"]
    if not isinstance(observed_at, datetime) or observed_at.tzinfo is None:
        raise ValueError("Continuous risk observation time is invalid")
    if observed_at.astimezone(timezone.utc) != record.event_time.astimezone(timezone.utc):
        raise ValueError("Continuous risk observation differs from event time")
    metrics = payload["metrics"]
    if not isinstance(metrics, dict) or set(metrics) != set(_RISK_METRICS):
        raise ValueError("Continuous risk metrics differ from the fixed producer contract")
    count = metrics["position_count"]
    if (isinstance(count, bool) or not isinstance(count, (int, float))
            or not float(count).is_integer() or not 0 <= count < 2**32):
        raise ValueError("Continuous risk position count is invalid")
    reasons = payload["reasons"]
    if (not isinstance(reasons, (tuple, list)) or len(reasons) > 65535
            or any(not isinstance(reason, str) or not reason for reason in reasons)
            or len(set(reasons)) != len(reasons)):
        raise ValueError("Continuous risk reasons are invalid")
    at = record.event_time.astimezone(timezone.utc).isoformat()
    received = record.recorded_at.astimezone(timezone.utc).isoformat()
    event_month = record.event_time.astimezone(timezone.utc).strftime("%Y-%m-01")
    event = {
        "run_id": record.run_id, "event_month": event_month,
        "attempt_id": attempt_id, "batch_id": batch_id,
        "record_id": record.record_id, "sequence": record.sequence,
        "event_time": at, "recorded_at": received,
        "category": "risk", "entity_type": "continuous_risk_state",
        "entity_id": record.account_id, "account_id": record.account_id,
        "correlation_id": str(payload.get("correlation_id") or ""),
        "causation_id": str(payload.get("causation_id") or ""),
    }
    state = {
        "record_id": record.record_id, "run_id": record.run_id,
        "event_month": event_month, "batch_id": batch_id,
        "account_id": record.account_id, "account_key": payload["account_key"],
        "state": str(payload["state"]), "enforced": int(enforced),
        **{key: _exact_decimal(metrics[key], _MEASURE_SCALE)
           for key in _RISK_METRICS if key != "position_count"},
        "position_count": int(count),
        "protection_required": _exact_decimal(payload["protection_required"], _MEASURE_SCALE),
        "protection_coverage": _exact_decimal(payload["protection_coverage"], _MEASURE_SCALE),
        "internal_reaction_ms": (
            _exact_decimal(payload["internal_reaction_ms"], _MEASURE_SCALE)
            if payload["internal_reaction_ms"] is not None else None
        ),
        "reason_count": len(reasons), "source_event_time": at,
    }
    reason_rows = tuple({
        "record_id": str(uuid5(NAMESPACE_URL,
            f"{record.record_id}:risk-reason:{ordinal}:{reason}")),
        "run_id": record.run_id, "event_month": event_month,
        "batch_id": batch_id, "parent_record_id": record.record_id,
        "ordinal": ordinal, "reason": reason,
    } for ordinal, reason in enumerate(reasons))
    return TypedJournalBatch(
        record.run_id, run_month, attempt_id, batch_id, prior_batch_id,
        record.sequence, record.sequence, source_cursor, "running", (event,),
        account_risk_states=(state,), account_risk_reasons=reason_rows,
    )


def intent_decision_batch(
    record: JournalRecord, *, run_month: date, attempt_id: str,
    batch_id: str, prior_batch_id: str, source_cursor: str,
) -> TypedJournalBatch:
    """Normalize portfolio and execution rejections without opaque payloads."""
    kind = record.entity_type
    if (record.category != "strategy_decision"
            or kind not in {"intent_rejection", "intent_deferral"}
            or not record.account_id or record.event_time.tzinfo is None
            or record.recorded_at.tzinfo is None):
        raise ValueError("Intent decision envelope is invalid")
    payload = dict(record.payload)
    shared = {"intent_id", "action", "reason", "reason_detail", "ticker"}
    portfolio = {"event", "rejection_reasons", "reference_price",
                 "strategy_id", "strategy_revision", "status"}
    if kind == "intent_deferral" or payload.get("reason") == "portfolio_rejected":
        if set(payload) - {"correlation_id", "causation_id"} != (shared | portfolio):
            raise ValueError("Portfolio intent decision has unmodeled fields")
        if (payload["event"] != ("intent_deferred" if kind == "intent_deferral"
                                else "intent_rejected")
                or payload["reason"] != ("portfolio_deferred" if kind == "intent_deferral"
                                          else "portfolio_rejected")
                or record.entity_id != f"{payload['intent_id']}:portfolio-{'deferred' if kind == 'intent_deferral' else 'rejected'}"):
            raise ValueError("Portfolio intent decision identity is inconsistent")
        reasons = payload["rejection_reasons"]
        if (not isinstance(reasons, (tuple, list)) or len(reasons) > 65535
                or any(not isinstance(reason, str) or not reason for reason in reasons)
                or len(set(reasons)) != len(reasons)):
            raise ValueError("Portfolio intent decision reasons are invalid")
        reference_price = _exact_decimal(payload["reference_price"])
        strategy_id = payload["strategy_id"]
        revision = payload["strategy_revision"]
        status = payload["status"]
    else:
        if (kind != "intent_rejection" or set(payload) - {"correlation_id", "causation_id"}
                != shared
                or payload.get("reason") != "execution_stop_already_triggered"
                or record.entity_id != payload.get("intent_id")):
            raise ValueError("Execution intent rejection has unmodeled evidence")
        reasons = ()
        reference_price = None
        strategy_id = ""
        revision = 0
        status = ""
    if (not isinstance(payload.get("intent_id"), str) or not payload["intent_id"]
            or payload.get("action") != "wait"
            or not isinstance(payload.get("ticker"), str) or not payload["ticker"]
            or not isinstance(payload.get("reason_detail"), str)
            or not isinstance(strategy_id, str)
            or type(revision) is not int or not 0 <= revision < 2**32
            or not isinstance(status, str)):
        raise ValueError("Intent decision fields are invalid")
    at = record.event_time.astimezone(timezone.utc).isoformat()
    received = record.recorded_at.astimezone(timezone.utc).isoformat()
    month = record.event_time.astimezone(timezone.utc).strftime("%Y-%m-01")
    event = {
        "run_id": record.run_id, "event_month": month,
        "attempt_id": attempt_id, "batch_id": batch_id,
        "record_id": record.record_id, "sequence": record.sequence,
        "event_time": at, "recorded_at": received,
        "category": record.category, "entity_type": kind,
        "entity_id": record.entity_id, "account_id": record.account_id,
        "correlation_id": str(payload.get("correlation_id") or ""),
        "causation_id": str(payload.get("causation_id") or ""),
    }
    decision = {
        "record_id": record.record_id, "run_id": record.run_id,
        "event_month": month, "batch_id": batch_id,
        "account_id": record.account_id, "intent_id": payload["intent_id"],
        "ticker": payload["ticker"].upper(), "decision_kind": kind,
        "action": "wait", "reason_code": payload["reason"],
        "reason_detail": payload["reason_detail"],
        "reference_price": reference_price, "strategy_id": strategy_id,
        "strategy_revision": revision, "assignment_status": status,
        "reason_count": len(reasons), "source_event_time": at,
    }
    reason_rows = tuple({
        "record_id": str(uuid5(NAMESPACE_URL,
            f"{record.record_id}:intent-decision-reason:{ordinal}:{reason}")),
        "run_id": record.run_id, "event_month": month, "batch_id": batch_id,
        "parent_record_id": record.record_id, "account_id": record.account_id,
        "ordinal": ordinal, "reason": reason,
    } for ordinal, reason in enumerate(reasons))
    return TypedJournalBatch(
        record.run_id, run_month, attempt_id, batch_id, prior_batch_id,
        record.sequence, record.sequence, source_cursor, "running", (event,),
        intent_decisions=(decision,), intent_decision_reasons=reason_rows,
    )


def _exact_decimal(value: float | Decimal, scale: Decimal = _SCALE) -> str:
    try:
        with localcontext() as context:
            context.prec = 50
            decimal = Decimal(str(value))
            quantized = decimal.quantize(scale)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Number cannot fit typed Decimal(38) precision") from exc
    if not decimal.is_finite() or decimal != quantized:
        raise ValueError("Number cannot fit typed Decimal(38) losslessly")
    if quantized.copy_abs() >= Decimal(10) ** (38 + scale.as_tuple().exponent):
        raise ValueError("Number exceeds typed Decimal(38) width")
    return format(quantized, "f")


def broker_fill_details(
    execution: Execution, *, run_id: str, event_month: str,
    batch_id: str, execution_record_id: str, commission_record_id: str | None,
    received_at: datetime, strategy_id: str = "", strategy_revision: int = 0,
    setup: str = "", exit_reason: str = "",
) -> FillDetails:
    """Project a broker fill without dropping an unmodeled source attribute."""
    if not run_id or not execution.execution_id or not execution.account or not execution.symbol:
        raise ValueError("Broker execution identity is incomplete")
    if execution.trade_time.tzinfo is None or received_at.tzinfo is None:
        raise ValueError("Broker execution timestamps must be timezone-aware")
    if execution.trade_time.astimezone(timezone.utc).strftime("%Y-%m-01") != event_month:
        raise ValueError("Broker execution partition differs from source time")
    if abs(execution.trade_time.timestamp() * 1000 - execution.trade_time_r) >= 1000:
        raise ValueError("Broker execution millisecond clock disagrees with trade time")
    unknown = set(execution.raw) - _SOURCE_FIELDS
    if unknown:
        raise ValueError(f"Broker execution has unmodeled source fields: {sorted(unknown)}")
    if execution.raw:
        parsed = parse_ibkr_execution(execution.raw)
        for field in fields(Execution):
            if field.name != "raw" and getattr(parsed, field.name) != getattr(execution, field.name):
                raise ValueError(f"Broker execution source disagrees on {field.name}")
    if execution.commission is not None and not commission_record_id:
        raise ValueError("Confirmed broker commission needs a separate typed event")
    if execution.commission is None and commission_record_id:
        raise ValueError("Pending broker commission cannot be published as final")
    common = {
        "run_id": run_id, "event_month": event_month, "batch_id": batch_id,
        "account_id": execution.account,
    }
    at = execution.trade_time.astimezone(timezone.utc).isoformat()
    received = received_at.astimezone(timezone.utc).isoformat()
    fill = {
        **common, "record_id": execution_record_id,
        "execution_id": execution.execution_id, "broker_order_id": execution.order_id,
        "client_order_id": execution.order_ref, "conid": execution.conid,
        "ticker": execution.symbol.upper(), "side": execution.side,
        "quantity": _exact_decimal(execution.size),
        "price": _exact_decimal(execution.price),
        "exchange": str(execution.raw.get("exchange") or ""),
        "currency": execution.currency, "net_amount": None,
        "cumulative_quantity": None, "average_price": None, "liquidity": "",
        "liquidation_trade": 0, "signal_price": None, "arrival_midpoint": None,
        "planned_risk": None, "source_event_time": at, "received_at": received,
        "strategy_id": strategy_id, "strategy_revision": strategy_revision,
        "setup": setup, "exit_reason": exit_reason,
    }
    commission = None
    if execution.commission is not None:
        commission = {
            **common, "record_id": commission_record_id,
            "execution_id": execution.execution_id,
            "commission": _exact_decimal(execution.commission),
            "currency": execution.currency, "status": "final",
            "time_authority": "execution",
            "realized_pnl": None, "source_event_time": at, "received_at": received,
        }
    return FillDetails(fill, commission)


def broker_fill_batch(
    execution: Execution, *, run_id: str, run_month: date, attempt_id: str,
    batch_id: str, prior_batch_id: str, first_sequence: int,
    source_cursor: str, status: str, received_at: datetime,
    strategy_id: str = "", strategy_revision: int = 0,
    setup: str = "", exit_reason: str = "",
) -> TypedJournalBatch:
    """Build one retry-stable fill batch on a background persistence lane."""
    event_month = execution.trade_time.astimezone(timezone.utc).strftime("%Y-%m-01")
    identity = f"{run_id}:{batch_id}:{execution.execution_id}"
    fill_id = str(uuid5(NAMESPACE_URL, identity + ":fill"))
    fee_id = (str(uuid5(NAMESPACE_URL, identity + ":commission"))
              if execution.commission is not None else None)
    details = broker_fill_details(
        execution, run_id=run_id, event_month=event_month, batch_id=batch_id,
        execution_record_id=fill_id, commission_record_id=fee_id,
        received_at=received_at, strategy_id=strategy_id,
        strategy_revision=strategy_revision, setup=setup,
        exit_reason=exit_reason,
    )
    common = {
        "run_id": run_id, "event_month": event_month,
        "attempt_id": attempt_id, "batch_id": batch_id,
        "event_time": execution.trade_time.astimezone(timezone.utc).isoformat(),
        "recorded_at": received_at.astimezone(timezone.utc).isoformat(),
        "category": "execution", "entity_id": execution.execution_id,
        "account_id": execution.account, "correlation_id": "", "causation_id": "",
    }
    events = [{**common, "record_id": fill_id, "sequence": first_sequence,
               "entity_type": "fill"}]
    if fee_id is not None:
        events.append({**common, "record_id": fee_id,
                       "sequence": first_sequence + 1,
                       "entity_type": "commission"})
    return TypedJournalBatch(
        run_id, run_month, attempt_id, batch_id, prior_batch_id,
        first_sequence, first_sequence + len(events) - 1,
        source_cursor, status, tuple(events),
        executions=(details.execution,),
        commissions=(details.commission,) if details.commission is not None else (),
    )


def order_command_batch(
    request: OrderRequest, *, run_id: str, run_month: date,
    attempt_id: str, batch_id: str, prior_batch_id: str,
    sequence: int, source_cursor: str, run_status: str,
    command_id: str, created_at: datetime, recorded_at: datetime,
    strategy_id: str = "", strategy_revision: int = 0,
    strategy_intent_id: str = "", order_group_id: str = "",
    policy_version: str = "", strategy_intent_record_id: str = "",
    strategy_intent_content_hash: str = "",
) -> TypedJournalBatch:
    """Capture one simple broker command losslessly before external dispatch.

    Strategy metadata and broker algo parameters still need typed child rows;
    neither may be silently discarded by this projection.
    """
    if request.raw or request.strategyParameters:
        raise ValueError("Order command has unmodeled nested broker or strategy evidence")
    if (not command_id or not request.cOID or not run_id
            or created_at.tzinfo is None or recorded_at.tzinfo is None
            or strategy_revision < 0):
        raise ValueError("Order command identity or time is incomplete")
    if any((strategy_intent_id, order_group_id, policy_version)) and not all(
        (strategy_intent_id, order_group_id, policy_version)
    ):
        raise ValueError("Strategy order command context must be complete")
    if bool(strategy_intent_record_id) != bool(strategy_intent_content_hash):
        raise ValueError("Exact intent revision identity and hash must be paired")
    if strategy_intent_record_id and not strategy_intent_id:
        raise ValueError("Exact intent revision requires strategy command context")
    if strategy_intent_content_hash and not re.fullmatch(r"[0-9a-f]{64}", strategy_intent_content_hash):
        raise ValueError("Exact intent revision hash must be SHA-256")
    at = created_at.astimezone(timezone.utc).isoformat()
    received = recorded_at.astimezone(timezone.utc).isoformat()
    event_month = created_at.astimezone(timezone.utc).strftime("%Y-%m-01")
    record_id = str(uuid5(NAMESPACE_URL, f"{run_id}:{batch_id}:{command_id}:order-command"))
    event = {
        "run_id": run_id, "event_month": event_month,
        "attempt_id": attempt_id, "batch_id": batch_id,
        "record_id": record_id, "sequence": sequence,
        "event_time": at, "recorded_at": received,
        "category": "order_management", "entity_type": "order_command",
        "entity_id": command_id, "account_id": request.acctId,
        "correlation_id": "", "causation_id": "",
    }
    detail = {
        "record_id": record_id, "run_id": run_id, "event_month": event_month,
        "batch_id": batch_id, "account_id": request.acctId,
        "command_id": command_id, "client_order_id": request.cOID,
        "conid": request.conid, "ticker": request.ticker,
        "side": request.side, "order_type": request.orderType,
        "time_in_force": request.tif,
        "quantity": _exact_decimal(request.quantity) if request.quantity is not None else None,
        "cash_quantity": _exact_decimal(request.cashQty) if request.cashQty is not None else None,
        "limit_price": _exact_decimal(request.price) if request.price is not None else None,
        "aux_price": _exact_decimal(request.auxPrice) if request.auxPrice is not None else None,
        "outside_rth": int(request.outsideRTH),
        "parent_command_id": "", "oca_group": "",
        "strategy_id": strategy_id, "strategy_revision": strategy_revision,
        "created_at": at, "security_type": request.secType,
        "listing_exchange": request.listingExchange,
        "trailing_amount": (_exact_decimal(request.trailingAmt)
                            if request.trailingAmt is not None else None),
        "trailing_type": request.trailingType or "",
        "single_group": int(request.isSingleGroup),
        "manual_indicator": int(request.manualIndicator),
        "external_operator": request.extOperator or "",
        "referrer": request.referrer or "",
        "broker_strategy": request.strategy or "",
        "parent_broker_order_id": request.parentId or "",
    }
    context = ({
        "record_id": str(uuid5(NAMESPACE_URL, f"{record_id}:strategy-context")),
        "parent_record_id": record_id, "run_id": run_id,
        "event_month": event_month, "batch_id": batch_id,
        "account_id": request.acctId,
        "strategy_intent_id": strategy_intent_id,
        "order_group_id": order_group_id,
        "policy_version": policy_version,
    },) if strategy_intent_id else ()
    intent_use = ({
        "record_id": str(uuid5(NAMESPACE_URL, f"{record_id}:intent-use")),
        "parent_record_id": record_id, "run_id": run_id,
        "event_month": event_month, "batch_id": batch_id,
        "account_id": request.acctId,
        "intent_record_id": str(UUID(strategy_intent_record_id)),
        "intent_content_hash": strategy_intent_content_hash,
    },) if strategy_intent_record_id else ()
    return TypedJournalBatch(
        run_id, run_month, attempt_id, batch_id, prior_batch_id,
        sequence, sequence, source_cursor, run_status, (event,),
        order_commands=(detail,), order_contexts=context, intent_uses=intent_use,
    )


def commission_revision_batch(
    report: CommissionEvent, *, run_id: str, run_month: date,
    attempt_id: str, batch_id: str, prior_batch_id: str,
    sequence: int, source_cursor: str, run_status: str,
    time_authority: str, commission_status: str = "final",
) -> TypedJournalBatch:
    """Publish a later fee revision without duplicating its execution row."""
    if report.raw:
        raise ValueError("Commission report has unmodeled source fields")
    if not report.execution_id or not report.account_id or not report.currency:
        raise ValueError("Commission report identity is incomplete")
    if report.source_event_time.tzinfo is None or report.received_at.tzinfo is None:
        raise ValueError("Commission report timestamps must be timezone-aware")
    if time_authority not in {"broker", "observation"}:
        raise ValueError("Commission report time authority is required")
    source_time = report.source_event_time.astimezone(timezone.utc)
    received = report.received_at.astimezone(timezone.utc)
    if time_authority == "observation" and source_time != received:
        raise ValueError("Observed commission time must equal receipt time")
    if commission_status not in {"final", "corrected", "reversed"}:
        raise ValueError("Commission revision status is invalid")
    event_month = source_time.strftime("%Y-%m-01")
    record_id = str(uuid5(
        NAMESPACE_URL,
        f"{run_id}:{batch_id}:{report.execution_id}:commission-revision",
    ))
    event = {
        "run_id": run_id, "event_month": event_month,
        "attempt_id": attempt_id, "batch_id": batch_id,
        "record_id": record_id, "sequence": sequence,
        "event_time": source_time.isoformat(), "recorded_at": received.isoformat(),
        "category": "execution", "entity_type": "commission",
        "entity_id": report.execution_id, "account_id": report.account_id,
        "correlation_id": "", "causation_id": "",
    }
    detail = {
        "record_id": record_id, "run_id": run_id,
        "event_month": event_month, "batch_id": batch_id,
        "account_id": report.account_id, "execution_id": report.execution_id,
        "commission": _exact_decimal(report.commission),
        "currency": report.currency, "status": commission_status,
        "time_authority": time_authority,
        "realized_pnl": (_exact_decimal(report.realized_pnl)
                         if report.realized_pnl is not None else None),
        "source_event_time": source_time.isoformat(),
        "received_at": received.isoformat(),
    }
    return TypedJournalBatch(
        run_id, run_month, attempt_id, batch_id, prior_batch_id,
        sequence, sequence, source_cursor, run_status, (event,),
        commissions=(detail,),
    )


def strategy_signal_batch(
    signal: StrategySignal, *, run_id: str, run_month: date,
    account_id: str, strategy_id: str, strategy_revision: int,
    attempt_id: str, batch_id: str, prior_batch_id: str,
    sequence: int, source_cursor: str, run_status: str,
    recorded_at: datetime,
    record_id: str | None = None,
    correlation_id: str = "",
    causation_id: str = "",
    persist_metadata_nodes: bool = False,
    decision_metadata: Mapping[str, Any] | None = None,
) -> TypedJournalBatch:
    """Project a signal only when every source and evidence field is represented."""
    if not signal.signal_id or not signal.ticker or not strategy_id or not account_id:
        raise ValueError("Strategy signal identity is incomplete")
    if not (-1 <= signal.score <= 1 and 0 <= signal.confidence <= 1):
        raise ValueError("Strategy signal score or confidence is out of range")
    if (len(signal.source_signal_ids) > 65535
            or any(not isinstance(source, str) or not source
                   for source in signal.source_signal_ids)):
        raise ValueError("Strategy signal sources are invalid")
    if signal.event_time.tzinfo is None or recorded_at.tzinfo is None:
        raise ValueError("Strategy signal timestamps must be timezone-aware")
    at = signal.event_time.astimezone(timezone.utc).isoformat()
    received = recorded_at.astimezone(timezone.utc).isoformat()
    month = signal.event_time.astimezone(timezone.utc).strftime("%Y-%m-01")
    record_id = record_id or str(uuid5(NAMESPACE_URL,
        f"{run_id}:{batch_id}:{strategy_id}:{signal.signal_id}:signal"))
    event = {
        "run_id": run_id, "event_month": month, "attempt_id": attempt_id,
        "batch_id": batch_id, "record_id": record_id, "sequence": sequence,
        "event_time": at, "recorded_at": received,
        "category": "strategy_decision", "entity_type": "signal",
        "entity_id": signal.signal_id, "account_id": account_id,
        "correlation_id": correlation_id, "causation_id": causation_id,
    }
    if persist_metadata_nodes:
        raise ValueError("Generic signal evidence nodes are retired for new writes")
    if signal.metadata and decision_metadata is None:
        raise ValueError("Strategy signal metadata lacks a concrete typed catalog")
    if decision_metadata is not None and dict(decision_metadata) != signal.metadata:
        raise ValueError("Strategy signal decision metadata differs from its source")
    evidence_nodes: tuple[Mapping[str, Any], ...] = ()
    detail = {
        "record_id": record_id, "run_id": run_id, "event_month": month,
        "batch_id": batch_id, "account_id": account_id,
        "strategy_id": strategy_id, "strategy_revision": strategy_revision,
        "signal_id": signal.signal_id, "signal_type": signal.signal_type,
        "ticker": signal.ticker.upper(),
        "action": str(getattr(signal.action, "value", signal.action)),
        "direction": str(getattr(signal.direction, "value", signal.direction)),
        "score": _exact_decimal(signal.score, _MEASURE_SCALE),
        "confidence": _exact_decimal(signal.confidence, _MEASURE_SCALE),
        "reason": signal.reason, "working_timeframe": signal.working_timeframe,
        "invalidation_price": (_exact_decimal(signal.invalidation_price)
                               if signal.invalidation_price is not None else None),
        "source_signal_count": len(signal.source_signal_ids),
        "evidence_node_count": len(evidence_nodes),
        "decision_assignment_id": (decision_metadata["assignment_id"]
                                   if decision_metadata is not None else None),
        "decision_reference_price": (_exact_decimal(decision_metadata["reference_price"])
                                     if decision_metadata is not None else None),
        "decision_status": (decision_metadata["status"]
                            if decision_metadata is not None else None),
        "decision_reason_detail": (decision_metadata["reason_detail"]
                                   if decision_metadata is not None else None),
        "source_event_time": at,
    }
    sources = tuple({
        "record_id": str(uuid5(NAMESPACE_URL,
            f"{record_id}:source:{ordinal}:{source_id}")),
        "run_id": run_id, "event_month": month, "batch_id": batch_id,
        "parent_record_id": record_id, "source_ordinal": ordinal,
        "source_signal_id": source_id,
    } for ordinal, source_id in enumerate(signal.source_signal_ids))
    return TypedJournalBatch(
        run_id, run_month, attempt_id, batch_id, prior_batch_id,
        sequence, sequence, source_cursor, run_status, (event,),
        signals=(detail,), signal_sources=sources,
        signal_evidence_nodes=evidence_nodes,
    )


def recover_common_signal_decision_metadata(
    event: Mapping[str, Any], detail: Mapping[str, Any],
) -> dict[str, Any]:
    """Rebuild the compact Backtest metadata from typed detail and envelope."""
    names = ("decision_assignment_id", "decision_reference_price",
             "decision_status", "decision_reason_detail")
    values = tuple(detail.get(name) for name in names)
    if all(value is None for value in values):
        return {}
    if (any(value is None for value in values)
            or event.get("record_id") != detail.get("record_id")
            or any(not isinstance(value, str) or not value for value in (
                detail["decision_assignment_id"], detail["decision_status"],
                detail["decision_reason_detail"], detail.get("reason"),
                event.get("correlation_id"), event.get("causation_id")))):
        raise ValueError("Typed Backtest signal decision metadata is incomplete")
    price = float(detail["decision_reference_price"])
    if _exact_decimal(price) != detail["decision_reference_price"]:
        raise ValueError("Typed Backtest signal reference price cannot round-trip")
    return {
        "assignment_id": detail["decision_assignment_id"],
        "reference_price": price,
        "status": detail["decision_status"],
        "reason_code": detail["reason"],
        "reason_detail": detail["decision_reason_detail"],
        "correlation_id": event["correlation_id"],
        "causation_id": event["causation_id"],
    }
