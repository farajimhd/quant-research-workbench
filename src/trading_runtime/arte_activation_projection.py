"""Lossless scalar projection of a live activation's causal signal inputs.

This is a preparation contract, not an activated persistence path. The live
supervisor still owns its SQLite checkpoint until typed publication, committed
recovery, and Keeper ownership are wired together.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from hashlib import sha256
import json
import math
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from src.trading_runtime.arte_journal_schema import ACTIVATION_TABLES
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_activation import strategy_observation_from_signal_occurrence


@dataclass(frozen=True, slots=True)
class ActivationProjection:
    delivery: tuple[tuple[str, str], ...]
    evidence: tuple[tuple[str, str, str], ...]
    field_evidence: tuple[tuple[str, str, str, str, str, str, str, str], ...]


_ACTIVATION_CONTRACTS = {table.name: table for table in ACTIVATION_TABLES}
ACTIVATION_RUN_ID = "live-strategy-runtime:activations"


def _scalar(value: Any) -> tuple[str, str]:
    if value is None:
        return "null", ""
    if type(value) is bool:
        return "bool", "1" if value else "0"
    if type(value) is int:
        return "int", str(value)
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("Activation evidence has a nonfinite value")
        return "float", repr(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Activation evidence has a nonfinite value")
        return "decimal", str(value)
    if isinstance(value, str):
        return "string", value
    raise ValueError(f"Activation evidence has an unsupported {type(value).__name__} value")


def _restore(kind: str, value: str) -> Any:
    if kind == "null" and value == "":
        return None
    if kind == "bool" and value in {"0", "1"}:
        return value == "1"
    if kind == "int":
        return int(value)
    if kind == "float":
        result = float(value)
        if math.isfinite(result):
            return result
    if kind == "decimal":
        result = Decimal(value)
        if result.is_finite():
            return result
    if kind == "string":
        return value
    raise ValueError("Activation scalar tag or value is invalid")


def project_activation(delivery: Mapping[str, Any]) -> ActivationProjection:
    """Prepare all fields consumed by activation replay; reject lossy input."""
    occurrence = delivery.get("occurrence")
    if not isinstance(occurrence, Mapping):
        raise ValueError("Activation requires a signal occurrence")
    run_plan_id = str(delivery.get("run_plan_id") or "")
    ticker = str(delivery.get("ticker") or "")
    event_id = str(delivery.get("event_id") or "")
    if (not run_plan_id or not ticker or not event_id
            or ticker != str(occurrence.get("ticker") or "")
            or event_id != str(occurrence.get("event_id") or "")):
        raise ValueError("Activation delivery and occurrence identities differ")
    effective_at = occurrence.get("effective_at") or occurrence.get("event_time")
    if not isinstance(effective_at, str):
        raise ValueError("Activation event time is missing")
    at = datetime.fromisoformat(effective_at.replace("Z", "+00:00"))
    if at.tzinfo is None:
        raise ValueError("Activation event time must be timezone-aware")
    delivery_time = datetime.fromisoformat(str(delivery.get("event_time") or "").replace("Z", "+00:00"))
    if (delivery_time.tzinfo is None or delivery_time != at
            or str(occurrence.get("signal_id") or event_id) != event_id
            or str(occurrence.get("signal_stream_id") or delivery.get("signal_stream_id") or "")
            != str(delivery.get("signal_stream_id") or "")):
        raise ValueError("Activation delivery time or signal identity differs from occurrence")
    if occurrence.get("event_time"):
        occurrence_time = datetime.fromisoformat(str(occurrence["event_time"]).replace("Z", "+00:00"))
        if occurrence_time.tzinfo is None or occurrence_time != at:
            raise ValueError("Activation occurrence has conflicting event times")
    fields = ("delivery_id", "run_plan_id", "profile_id", "book_id", "ticker",
              "signal_stream_id", "event_id", "event_time")
    delivery_fields = tuple((name, str(delivery.get(name) or "")) for name in fields)
    evidence = occurrence.get("evidence") or {}
    field_evidence = occurrence.get("field_evidence") or {}
    if not isinstance(evidence, Mapping) or not isinstance(field_evidence, Mapping):
        raise ValueError("Activation evidence must be keyed")
    evidence_rows = tuple(
        (str(key), *_scalar(value)) for key, value in sorted(evidence.items())
        if isinstance(key, str) and key
    )
    if len(evidence_rows) != len(evidence):
        raise ValueError("Activation evidence has an invalid field key")
    field_rows = []
    for key, raw in sorted(field_evidence.items()):
        if not isinstance(key, str) or not key or not isinstance(raw, Mapping):
            raise ValueError("Activation field evidence has an invalid record")
        if set(raw) - {"field_ref", "interval", "aggregation", "value", "available_at", "null_reason"}:
            raise ValueError("Activation field evidence has an unmodeled field")
        if any(raw.get(name) is not None and not isinstance(raw[name], str)
               for name in ("field_ref", "interval", "aggregation", "available_at", "null_reason")):
            raise ValueError("Activation field evidence metadata must be strings")
        value_kind, value = _scalar(raw.get("value"))
        available_at = str(raw.get("available_at") or "")
        if available_at:
            available_time = datetime.fromisoformat(available_at.replace("Z", "+00:00"))
            if available_time.tzinfo is None or available_time > at:
                raise ValueError("Activation evidence availability must be causal and timezone-aware")
        field_rows.append((key, str(raw.get("field_ref") or ""),
                           str(raw.get("interval") or ""), str(raw.get("aggregation") or ""),
                           value_kind, value, available_at, str(raw.get("null_reason") or "")))
    projected = ActivationProjection(delivery_fields, evidence_rows, tuple(field_rows))
    # Exercise the actual strategy input path before this evidence can be staged.
    strategy_observation_from_signal_occurrence(restore_activation(projected)["occurrence"])
    return projected


def restore_activation(projected: ActivationProjection) -> dict[str, Any]:
    delivery = dict(projected.delivery)
    delivery["occurrence"] = {
        "ticker": delivery["ticker"], "event_id": delivery["event_id"],
        "signal_id": delivery["event_id"], "event_time": delivery["event_time"],
        "effective_at": delivery["event_time"],
        "evidence": {key: _restore(kind, value) for key, kind, value in projected.evidence},
        "field_evidence": {
            key: {"field_ref": field_ref, "interval": interval,
                  "aggregation": aggregation, "value": _restore(kind, value),
                  "available_at": available_at or None, "null_reason": reason or None}
            for key, field_ref, interval, aggregation, kind, value, available_at, reason
            in projected.field_evidence
        },
    }
    return delivery


def _hash(row: Mapping[str, Any]) -> str:
    return sha256(canonical_json(dict(row)).encode("utf-8")).hexdigest()


def _sealed(row: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_row(row)
    return {**normalized, "content_hash": _hash(normalized)}


def _normalize_row(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for name in ("event_time", "available_at", "committed_at"):
        if result.get(name) is not None:
            at = datetime.fromisoformat(str(result[name]).replace("Z", "+00:00"))
            if at.tzinfo is None:
                # ClickHouse DateTime64 JSONEachRow omits its UTC zone suffix.
                at = at.replace(tzinfo=timezone.utc)
            result[name] = at.astimezone(timezone.utc).isoformat(timespec="microseconds")
    if result.get("value_decimal") is not None:
        with localcontext() as context:
            context.prec = 50
            result["value_decimal"] = str(Decimal(str(result["value_decimal"])).quantize(Decimal("0.000000000000000001")))
    return result


def _typed_value(kind: str, value: str) -> dict[str, Any]:
    result = dict(value_kind=kind, value_text=None, value_int=None,
                  value_float=None, value_decimal=None, value_bool=None)
    restored = _restore(kind, value)
    if kind == "string":
        result["value_text"] = restored
    elif kind == "int":
        if not -(2**63) <= restored < 2**63:
            raise ValueError("Activation integer exceeds Int64")
        result["value_int"] = restored
    elif kind == "float":
        result["value_float"] = restored
    elif kind == "decimal":
        if restored.as_tuple().exponent < -18 or restored.adjusted() >= 20:
            raise ValueError("Activation decimal exceeds Decimal(38, 18)")
        try:
            with localcontext() as context:
                context.prec = 50
                result["value_decimal"] = str(restored.quantize(Decimal("0.000000000000000001")))
        except InvalidOperation as exc:
            raise ValueError("Activation decimal exceeds Decimal(38, 18)") from exc
    elif kind == "bool":
        result["value_bool"] = int(restored)
    elif kind != "null":
        raise ValueError("Activation value kind is invalid")
    return result


def _untyped_value(row: Mapping[str, Any]) -> tuple[str, str]:
    kind = str(row["value_kind"])
    names = {"string": "value_text", "int": "value_int", "float": "value_float",
             "decimal": "value_decimal", "bool": "value_bool"}
    populated = [name for name in names.values() if row[name] is not None]
    if kind == "null":
        if populated:
            raise RuntimeError("Null activation evidence has a typed value")
        return "null", ""
    if kind not in names or populated != [names[kind]]:
        raise RuntimeError("Activation evidence value columns disagree with type")
    if kind == "bool" and row["value_bool"] not in (0, 1):
        raise RuntimeError("Activation boolean is invalid")
    value = row[names[kind]]
    if kind == "string":
        if not isinstance(value, str):
            raise RuntimeError("Activation string value is invalid")
        return _scalar(value)
    if kind == "int":
        if type(value) is not int:
            raise RuntimeError("Activation integer value is invalid")
        return _scalar(value)
    if kind == "float":
        if type(value) not in (int, float):
            raise RuntimeError("Activation float value is invalid")
        return _scalar(float(value))
    if kind == "decimal":
        if not isinstance(value, (str, Decimal)):
            raise RuntimeError("Activation decimal value is invalid")
        return _scalar(Decimal(value))
    return _scalar(bool(value))


def prepare_activation_rows(projected: ActivationProjection) -> dict[str, tuple[dict[str, Any], ...]]:
    """Seal one normalized activation and its independent late commit fence."""
    delivery = dict(projected.delivery)
    at = datetime.fromisoformat(delivery["event_time"].replace("Z", "+00:00"))
    if at.tzinfo is None:
        raise ValueError("Activation time must be timezone-aware")
    session_date = at.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    identity = {"run_id": ACTIVATION_RUN_ID, "session_date": session_date,
                "run_plan_id": delivery["run_plan_id"],
                "ticker": delivery["ticker"], "event_id": delivery["event_id"]}
    evidence = tuple(_sealed({**identity, "field_key": key,
                              **_typed_value(kind, value)})
                     for key, kind, value in projected.evidence)
    fields = tuple(_sealed({**identity, "field_key": key, "field_ref": ref,
                            "interval": interval, "aggregation": aggregation,
                            "available_at": available or None,
                            "null_reason": reason or None,
                            **_typed_value(kind, value)})
                   for key, ref, interval, aggregation, kind, value, available, reason
                   in projected.field_evidence)
    if (len({row["field_key"] for row in evidence}) != len(evidence)
            or len({row["field_key"] for row in fields}) != len(fields)
            or not delivery["run_plan_id"] or not delivery["ticker"] or not delivery["event_id"]):
        raise ValueError("Activation has duplicate or missing identity")
    parent = _sealed({**identity, "delivery_id": delivery["delivery_id"],
                      "profile_id": delivery["profile_id"],
                      "book_id": delivery["book_id"],
                      "signal_stream_id": delivery["signal_stream_id"],
                      "event_time": at.astimezone(timezone.utc).isoformat(),
                      "evidence_count": len(evidence),
                      "field_evidence_count": len(fields)})
    return {"trading_activation_v1": (parent,),
            "trading_activation_evidence_v1": evidence,
            "trading_activation_field_evidence_v1": fields}


def _literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _stored(client: Any, name: str, identity: Mapping[str, str]) -> tuple[dict[str, Any], ...]:
    where = " AND ".join(f"{key}={_literal(value)}" for key, value in identity.items())
    response = client.execute(
        f"SELECT * FROM arte.{name} WHERE {where} LIMIT 4097 FORMAT JSONEachRow")
    rows = tuple(json.loads(line) for line in response.splitlines() if line.strip())
    if len(rows) > 4096:
        raise RuntimeError("Activation recovery exceeds bounded row limit")
    expected = {column for column, _ in _ACTIVATION_CONTRACTS[name].columns}
    for row in rows:
        normalized = _normalize_row(row)
        if (set(normalized) != expected or any(str(normalized[key]) != value for key, value in identity.items())
                or _hash({key: value for key, value in normalized.items() if key != "content_hash"})
                != normalized["content_hash"]):
            raise RuntimeError(f"Stored {name} differs from its typed contract or hash")
    return tuple(_normalize_row(row) for row in rows)


def _family_hash(rows: tuple[Mapping[str, Any], ...]) -> str:
    return _hash(sorted((str(row.get("field_key") or ""), row["content_hash"]) for row in rows))


def _verify_rows(client: Any, identity: Mapping[str, str], *, require_commit: bool) -> dict[str, tuple[dict[str, Any], ...]]:
    result = {name: _stored(client, name, identity) for name in _ACTIVATION_CONTRACTS}
    parents, commits = result["trading_activation_v1"], result["trading_activation_commit_v1"]
    if len(parents) > 1 or len(commits) > 1:
        raise RuntimeError("Activation has duplicate parent or commit rows")
    if require_commit and not commits:
        raise RuntimeError("Activation lacks a committed fence")
    if commits:
        if len(parents) != 1:
            raise RuntimeError("Committed activation lacks one parent")
        commit = commits[0]
        evidence = result["trading_activation_evidence_v1"]
        fields = result["trading_activation_field_evidence_v1"]
        if (commit["parent_hash"] != parents[0]["content_hash"]
                or len(evidence) != int(parents[0]["evidence_count"])
                or len(fields) != int(parents[0]["field_evidence_count"])
                or len({row["field_key"] for row in evidence}) != len(evidence)
                or len({row["field_key"] for row in fields}) != len(fields)
                or commit["evidence_hash"] != _family_hash(evidence)
                or commit["field_evidence_hash"] != _family_hash(fields)):
            raise RuntimeError("Activation commit does not seal its parent and evidence")
    return result


def _insert(client: Any, name: str, rows: tuple[Mapping[str, Any], ...], token: str) -> None:
    if not rows:
        return
    columns = tuple(column for column, _ in _ACTIVATION_CONTRACTS[name].columns)
    body = "\n".join(canonical_json(row) for row in rows)
    client.execute(
        f"INSERT INTO arte.{name} ({','.join(columns)}) "
        "SETTINGS async_insert=1,wait_for_async_insert=1,insert_deduplicate=1,"
        f"insert_deduplication_token={_literal(token)} FORMAT JSONEachRow\n{body}")


def publish_activation(client: Any, projected: ActivationProjection, *,
                       keeper: Any, owner_id: str, epoch: int) -> str:
    """Publish validated families, verify readback, then publish a late fence.

    The caller must acquire and renew the Keeper claim and perform operator
    storage/grant preflight. This primitive verifies claim currency before and
    after each potentially blocking database operation; it never releases it.
    """
    prepared = prepare_activation_rows(projected)
    parent = prepared["trading_activation_v1"][0]
    identity = {key: str(parent[key]) for key in ("run_id", "session_date", "run_plan_id", "ticker", "event_id")}
    resource_id = f"activation:{identity['session_date']}:{identity['run_plan_id']}:{identity['ticker']}"
    if not owner_id or type(epoch) is not int or epoch < 1:
        raise ValueError("Activation requires a Keeper owner and positive epoch")

    def require_claim() -> None:
        if not keeper.portfolio_admission_lease_is_current(
            resource_id, owner_id=owner_id, epoch=epoch
        ):
            raise RuntimeError("Activation Keeper claim is no longer current")

    require_claim()
    existing = _verify_rows(client, identity, require_commit=False)
    require_claim()
    if existing["trading_activation_commit_v1"]:
        if any(tuple(sorted(row["content_hash"] for row in existing[name]))
               != tuple(sorted(row["content_hash"] for row in prepared[name]))
               for name in prepared):
            raise RuntimeError("Committed activation identity has conflicting content")
        return parent["content_hash"]
    for name, rows in prepared.items():
        found = {str(row.get("field_key") or ""): row for row in existing[name]}
        if len(found) != len(existing[name]):
            raise RuntimeError("Activation has duplicate uncommitted evidence")
        missing = []
        for row in rows:
            key = str(row.get("field_key") or "")
            if key in found:
                if found[key]["content_hash"] != row["content_hash"]:
                    raise RuntimeError("Activation retry conflicts with stored content")
            else:
                missing.append(row)
        if len(found) + len(missing) != len(rows):
            raise RuntimeError("Activation has unexpected uncommitted rows")
        missing_rows = tuple(missing)
        _insert(client, name, missing_rows,
                f"activation:{parent['content_hash']}:{name}:{_family_hash(missing_rows)}")
        require_claim()
    stored = _verify_rows(client, identity, require_commit=False)
    require_claim()
    if any(tuple(sorted(row["content_hash"] for row in stored[name]))
           != tuple(sorted(row["content_hash"] for row in prepared[name]))
           for name in prepared):
        raise RuntimeError("Activation rows did not become durable")
    commit = _sealed({**identity, "parent_hash": parent["content_hash"],
                      "evidence_hash": _family_hash(prepared["trading_activation_evidence_v1"]),
                      "field_evidence_hash": _family_hash(prepared["trading_activation_field_evidence_v1"]),
                      "committed_at": datetime.now(timezone.utc).isoformat()})
    _insert(client, "trading_activation_commit_v1", (commit,),
            f"activation:{parent['content_hash']}:commit")
    require_claim()
    _verify_rows(client, identity, require_commit=True)
    require_claim()
    return parent["content_hash"]


def load_activation(client: Any, *, session_date: date, run_plan_id: str,
                    ticker: str, event_id: str) -> dict[str, Any]:
    """Restore only a complete, hash-verified activation."""
    identity = {"run_id": ACTIVATION_RUN_ID, "session_date": session_date.isoformat(),
                "run_plan_id": run_plan_id,
                "ticker": ticker, "event_id": event_id}
    rows = _verify_rows(client, identity, require_commit=True)
    parent = rows["trading_activation_v1"][0]
    delivery = tuple((key, str(parent[key])) for key in (
        "delivery_id", "run_plan_id", "profile_id", "book_id", "ticker",
        "signal_stream_id", "event_id", "event_time"))
    evidence = tuple((str(row["field_key"]), *_untyped_value(row))
                     for row in rows["trading_activation_evidence_v1"])
    field_evidence = tuple((str(row["field_key"]), str(row["field_ref"]),
                            str(row["interval"]), str(row["aggregation"]),
                            *_untyped_value(row), str(row["available_at"] or ""),
                            str(row["null_reason"] or ""))
                           for row in rows["trading_activation_field_evidence_v1"])
    restored = restore_activation(ActivationProjection(delivery, evidence, field_evidence))
    strategy_observation_from_signal_occurrence(restored["occurrence"])
    return restored


def load_session_activations(client: Any, *, session_date: date,
                             run_plan_id: str, ticker: str) -> tuple[dict[str, Any], ...]:
    """Audit every current-session row for one plan/ticker before replay.

    Enumerating all families exposes prepared-only parents and orphan evidence,
    including identities that a commit-only query would silently omit.
    """
    if (type(session_date) is not date or not isinstance(run_plan_id, str)
            or not run_plan_id or not isinstance(ticker, str) or not ticker):
        raise ValueError("Activation recovery requires a session, run plan, and ticker")
    identity = {"run_id": ACTIVATION_RUN_ID, "session_date": session_date.isoformat(),
                "run_plan_id": run_plan_id, "ticker": ticker}
    where = " AND ".join(f"{key}={_literal(value)}" for key, value in identity.items())
    event_ids: set[str] = set()
    for name in _ACTIVATION_CONTRACTS:
        response = client.execute(
            f"SELECT event_id FROM arte.{name} WHERE {where} "
            "LIMIT 4097 FORMAT JSONEachRow")
        rows = tuple(json.loads(line) for line in response.splitlines() if line.strip())
        if len(rows) > 4096:
            raise RuntimeError("Activation session audit exceeds bounded row limit")
        for row in rows:
            if set(row) != {"event_id"} or not isinstance(row["event_id"], str) or not row["event_id"]:
                raise RuntimeError("Activation session audit returned an invalid event identity")
            event_ids.add(row["event_id"])
        if len(event_ids) > 4096:
            raise RuntimeError("Activation session audit exceeds bounded event limit")
    return tuple(load_activation(client, session_date=session_date,
                                 run_plan_id=run_plan_id, ticker=ticker, event_id=event_id)
                 for event_id in sorted(event_ids))
