"""Coverage-last publication of one immutable, normalized Strategy 1 release.

This module is producer-owned. Backtest and live runtimes only call the
separate SELECT-only release reader. JSON is an HTTP transport format here;
no JSON value or blob is inserted into the typed ClickHouse tables.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any, Mapping
from uuid import uuid4

from src.backend.backtest_strategy_one_configuration import (
    certify_strategy_one_configuration,
)
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_one_configuration_tree import (
    NODE_TABLE, RELEASE_TABLE, decode_nodes, encode_nodes, node_hash,
    verify_tables,
)
from src.trading_runtime.strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER


_HEX = re.compile(r"[0-9a-f]{64}\Z")


def publication_envelope(source: Mapping[str, Any]) -> dict[str, Any]:
    """Compile the legacy candidate once without altering its disk record."""
    from pipelines.strategy_one.configuration_migration import (
        compile_strategy_one_configuration,
    )

    payload, payload_hash, nodes_hash, count = compile_strategy_one_configuration(source)
    return {
        "source_candidate_id": source["revision_id"],
        "source_candidate_hash": source["content_hash"],
        "payload_hash": payload_hash,
        "node_hash": nodes_hash,
        "node_count": count,
        "payload": payload,
    }


def _rows(client: Any, sql: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in client.execute(
        sql + " FORMAT JSONEachRow").splitlines() if line.strip()]


def _verified_envelope(envelope: Mapping[str, Any]) -> tuple[dict, tuple[dict, ...]]:
    from pipelines.strategy_one.configuration_migration import (
        SOURCE_CANDIDATE_HASH, SOURCE_CANDIDATE_ID,
    )

    if (not isinstance(envelope, Mapping)
            or set(envelope) != {"source_candidate_id", "source_candidate_hash",
                                    "payload_hash", "node_hash", "node_count", "payload"}
            or envelope["source_candidate_id"] != SOURCE_CANDIDATE_ID
            or envelope["source_candidate_hash"] != SOURCE_CANDIDATE_HASH
            or any(not isinstance(envelope[key], str)
                   or _HEX.fullmatch(envelope[key]) is None
                   for key in ("payload_hash", "node_hash"))
            or not isinstance(envelope["payload"], dict)):
        raise ValueError("Strategy 1 transfer envelope has a foreign source or shape")
    payload = envelope["payload"]
    strategy = dict(payload.get("strategy") or {})
    if (strategy.get("strategy_id") != STRATEGY_ID
            or strategy.get("strategy_number") != STRATEGY_NUMBER
            or strategy.get("revision") != STRATEGY_NUMBER
            or strategy.get("execution_interval") != "100ms"):
        raise ValueError("Strategy 1 transfer has a foreign numbered contract")
    nodes = encode_nodes(payload)
    digest = sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    if (digest != envelope["payload_hash"]
            or node_hash(nodes) != envelope["node_hash"]
            or len(nodes) != envelope["node_count"]):
        raise ValueError("Strategy 1 transfer does not match its typed content seal")
    # The source snapshot itself is read only on the laptop; SSH authentication
    # and the transfer seal bind that trusted emitter to this receiver.
    return payload, nodes


def _insert_rows(client: Any, table: str, columns: tuple[str, ...],
                 rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("Strategy 1 publication cannot insert an empty batch")
    body = "\n".join(json.dumps(row, separators=(",", ":"),
                                 ensure_ascii=True, allow_nan=False)
                     for row in rows)
    client.execute(f"INSERT INTO {table} ({','.join(columns)}) "
                   "FORMAT JSONEachRow\n" + body)


def publish_configuration(client: Any, keeper: Any,
                          envelope: Mapping[str, Any]) -> str:
    """Publish nodes, verify their typed readback, then publish one release.

    An ambiguous node INSERT leaves an invisible orphan attempt. An ambiguous
    release INSERT must be resolved by the release reader, never blind retry.
    """
    payload, nodes = _verified_envelope(envelope)
    try:
        verify_tables(client)
    except Exception as exc:
        raise PublicationStageError("layout", exc) from exc
    lock_path = "/trading/ownership/v1/strategy_one_configuration/1"
    try:
        keeper.create(lock_path, uuid4().hex.encode("ascii"),
                      ephemeral=True, makepath=True)
    except Exception as exc:
        if type(exc).__name__ == "NodeExistsError":
            raise RuntimeError("Another Strategy 1 configuration publisher owns the release") from exc
        raise
    stage = "existing_release"
    try:
        existing = _rows(client, "SELECT release_attempt_id,payload_hash "
                         f"FROM {RELEASE_TABLE} WHERE strategy_number=1")
        if existing:
            if len(existing) == 1 and existing[0]["payload_hash"] == envelope["payload_hash"]:
                certified = certify_strategy_one_configuration(client)
                if certified.source_candidate_hash != envelope["source_candidate_hash"]:
                    raise RuntimeError("Strategy 1 existing source differs")
                return certified.token
            raise RuntimeError("Strategy 1 already has a different immutable release")
        attempt = str(uuid4())
        columns = ("strategy_number", "release_attempt_id", "node_id",
                   "parent_node_id", "child_key", "child_ordinal", "value_kind",
                   "text_value", "int_value", "float_value", "bool_value")
        for start in range(0, len(nodes), 500):
            stage = "node_insert"
            if not keeper.connected:
                raise RuntimeError("Strategy 1 Keeper claim was lost")
            batch = [dict(strategy_number=STRATEGY_NUMBER,
                          release_attempt_id=attempt, **row)
                     for row in nodes[start:start + 500]]
            _insert_rows(client, NODE_TABLE, columns, batch)
        stage = "node_readback"
        readback = _rows(client,
            "SELECT node_id,parent_node_id,child_key,child_ordinal,value_kind,"
            "text_value,int_value,float_value,bool_value "
            f"FROM {NODE_TABLE} WHERE strategy_number=1 "
            f"AND release_attempt_id=toUUID('{attempt}') ORDER BY node_id")
        if len(readback) != len(nodes):
            raise RuntimeError(
                f"node_count:{len(readback)}:expected:{len(nodes)}")
        actual_hash = node_hash(readback)
        if actual_hash != envelope["node_hash"]:
            raise RuntimeError(
                f"node_hash:{actual_hash}:expected:{envelope['node_hash']}")
        if decode_nodes(readback) != payload:
            raise RuntimeError("node_payload_mismatch")
        if not keeper.connected:
            raise RuntimeError("Strategy 1 Keeper claim was lost before release")
        release = {
            "strategy_number": STRATEGY_NUMBER,
            "release_attempt_id": attempt,
            "strategy_id": STRATEGY_ID,
            "source_candidate_id": envelope["source_candidate_id"],
            "source_candidate_hash": envelope["source_candidate_hash"],
            "payload_hash": envelope["payload_hash"],
            "node_count": len(nodes), "node_hash": envelope["node_hash"],
            "published_at": datetime.now(timezone.utc).isoformat(),
        }
        stage = "release_insert"
        _insert_rows(client, RELEASE_TABLE, tuple(release), [release])
        stage = "release_readback"
        certified = certify_strategy_one_configuration(client)
        if certified.attempt_id != attempt or certified.payload != payload:
            raise RuntimeError("Strategy 1 released rows differ from typed transfer")
        return certified.token
    except Exception as exc:
        if isinstance(exc, PublicationStageError):
            raise
        raise PublicationStageError(stage, exc) from exc
    finally:
        try:
            keeper.delete(lock_path)
        except Exception:
            pass


class PublicationStageError(RuntimeError):
    """Sanitized stage and numeric server codes; never retain response text."""

    def __init__(self, stage: str, cause: Exception) -> None:
        status = getattr(cause, "status_code", None)
        code_match = re.search(r"Code: ([0-9]{1,4})\b", str(cause))
        code = code_match.group(1) if code_match else "unknown"
        self.safe_diagnostic = (
            f"{stage}:HTTP{status if type(status) is int else 'unknown'}:CH{code}"
            f":{type(cause).__name__}"
            + (f":{str(cause)}" if isinstance(cause, RuntimeError)
               and str(cause).startswith(("node_count:", "node_hash:",
                                           "node_payload_mismatch")) else ""))
        super().__init__(self.safe_diagnostic)
