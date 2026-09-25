"""Read-only verification of occupied, pre-cursor V1 signal batches.

This verifies one signal family against its legacy commit count/hash. It is
not a whole-run committed-prefix verifier and grants no restart authority.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from hashlib import sha256
import json
import re
from typing import Any, Mapping
from uuid import UUID

from src.trading_runtime.arte_journal_schema import (
    LEGACY_COMMIT_V1, LEGACY_STRATEGY_SIGNAL_V1,
)
from src.trading_runtime.arte_journal_writer import _datetime_wire, _literal, _rows
from src.trading_runtime.journal_contract import canonical_json


_COLUMNS = LEGACY_STRATEGY_SIGNAL_V1.columns


def _require_legacy_catalog(client: Any) -> None:
    rows = _rows(client,
        "SELECT table,name,type FROM system.columns WHERE database='arte' "
        "AND table IN ('trading_commit_v1','trading_strategy_signal_v1') "
        "ORDER BY table,position FORMAT JSONEachRow")
    for contract in (LEGACY_COMMIT_V1, LEGACY_STRATEGY_SIGNAL_V1):
        actual = tuple((row.get("name"), row.get("type")) for row in rows
                       if row.get("table") == contract.name)
        if actual != contract.columns:
            raise RuntimeError(f"Legacy V1 deployed schema differs: {contract.name}")
    if {row.get("table") for row in rows} != {
            LEGACY_COMMIT_V1.name, LEGACY_STRATEGY_SIGNAL_V1.name}:
        raise RuntimeError("Legacy V1 deployed catalog is incomplete")


def _legacy_signal_content(row: Mapping[str, Any]) -> dict[str, Any]:
    """Exact scalar canonicalizer from the deployed 8764dbc8 V1 recipe."""
    if set(row) != {name for name, _ in _COLUMNS} - {"content_hash"}:
        raise ValueError("Legacy V1 signal has missing or extra columns")
    result: dict[str, Any] = {}
    for name, kind in _COLUMNS:
        if name == "content_hash":
            continue
        value = row[name]
        if value is None:
            if not kind.startswith("Nullable("):
                raise ValueError(f"Legacy V1 signal {name} cannot be null")
            result[name] = None
            continue
        base = kind[9:-1] if kind.startswith("Nullable(") else kind
        if base == "UUID":
            result[name] = str(UUID(str(value)))
        elif base == "Date":
            result[name] = date.fromisoformat(str(value)).isoformat()
        elif base.startswith("DateTime64(9"):
            result[name] = _datetime_wire(value, 9, stored_utc=True)
        elif base.startswith("UInt"):
            if isinstance(value, bool) or not str(value).isdigit():
                raise ValueError(f"Legacy V1 signal {name} is not unsigned")
            number = int(value)
            if number >= 1 << int(base[4:]):
                raise ValueError(f"Legacy V1 signal {name} exceeds width")
            result[name] = number
        elif base.startswith("Decimal("):
            match = re.fullmatch(r"Decimal\((\d+),\s*(\d+)\)", base)
            if match is None:
                raise ValueError("Legacy V1 decimal type is invalid")
            precision, scale = map(int, match.groups())
            try:
                with localcontext() as context:
                    context.prec = 50
                    number = Decimal(str(value))
                    quantized = number.quantize(Decimal(1).scaleb(-scale))
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(f"Legacy V1 signal {name} is not decimal") from exc
            if (not number.is_finite() or number != quantized
                    or quantized.copy_abs() >= Decimal(10) ** (precision - scale)):
                raise ValueError(f"Legacy V1 signal {name} loses precision")
            result[name] = format(quantized, f".{scale}f")
        elif base in {"String", "LowCardinality(String)"}:
            if not isinstance(value, str):
                raise ValueError(f"Legacy V1 signal {name} is not text")
            candidate = value.lstrip()
            if candidate.startswith(("{", "[")):
                try:
                    decoded = json.loads(candidate)
                except (json.JSONDecodeError, ValueError):
                    pass
                else:
                    if isinstance(decoded, (dict, list)):
                        raise ValueError("Legacy V1 signal contains opaque JSON text")
            result[name] = value
        else:
            raise ValueError(f"Legacy V1 signal type is unsupported: {name}")
    return result


def _verified_signal_digest(content: Mapping[str, Any], stored_hash: str) -> str:
    """Accept only the two proven historical V1 row-hash recipes.

    The earlier writer hashed source typed scalars before DateTime64 rendering.
    Its zero-fraction UTC datetime serialized with ISO seconds; ClickHouse
    returns the same instant with nine fractional digits. Other original
    timestamp spellings cannot be recovered from the stored value.
    """
    digest = sha256(canonical_json(content).encode("utf-8")).hexdigest()
    if digest == stored_hash:
        return digest
    stamp = content["source_event_time"]
    if isinstance(stamp, str) and stamp.endswith(".000000000"):
        raw = dict(content)
        raw["source_event_time"] = stamp[:-10].replace(" ", "T") + "+00:00"
        digest = sha256(canonical_json(raw).encode("utf-8")).hexdigest()
        if digest == stored_hash:
            return digest
    raise RuntimeError("Legacy V1 signal row hash or batch differs")


def load_legacy_v1_signal_batch(client: Any, *, run_id: str, batch_id: str) -> tuple[dict[str, Any], ...]:
    """Verify only legacy signal rows under one exact V1 commit family seal."""
    _require_legacy_catalog(client)
    batch = str(UUID(batch_id))
    commits = _rows(client,
        "SELECT run_id,batch_id,signal_count,signal_hash FROM arte.trading_commit_v1 "
        f"WHERE run_id={_literal(run_id)} AND batch_id=toUUID({_literal(batch)}) "
        "FORMAT JSONEachRow")
    if len(commits) != 1 or commits[0]["run_id"] != run_id or str(UUID(str(
            commits[0]["batch_id"]))) != batch:
        raise RuntimeError("Legacy V1 signal batch lacks one commit")
    # JSONEachRow otherwise decodes Decimal as binary float before verification.
    columns = ",".join(
        f"toString({name}) AS {name}" if kind.startswith("Decimal(")
        or kind.startswith("Nullable(Decimal(") else name
        for name, kind in _COLUMNS)
    rows = _rows(client, f"SELECT {columns} FROM arte.trading_strategy_signal_v1 "
                 f"WHERE run_id={_literal(run_id)} AND batch_id=toUUID({_literal(batch)}) "
                 "FORMAT JSONEachRow")
    identities = []
    result = []
    for row in rows:
        content = {key: value for key, value in row.items() if key != "content_hash"}
        canonical = _legacy_signal_content(content)
        digest = _verified_signal_digest(canonical, str(row.get("content_hash")))
        if (canonical["run_id"] != run_id or canonical["batch_id"] != batch):
            raise RuntimeError("Legacy V1 signal row hash or batch differs")
        identities.append((canonical["record_id"], digest))
        result.append({**canonical, "content_hash": digest})
    identities.sort()
    if len({key for key, _ in identities}) != len(identities):
        raise RuntimeError("Legacy V1 signal batch repeats a record")
    family_hash = sha256(canonical_json(identities).encode("utf-8")).hexdigest()
    if (len(result) != int(commits[0]["signal_count"])
            or family_hash != str(commits[0]["signal_hash"])):
        raise RuntimeError("Legacy V1 signal family differs from committed fence")
    return tuple(result)
