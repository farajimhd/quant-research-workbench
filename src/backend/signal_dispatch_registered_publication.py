"""Blocking control-plane publication of normalized typed dispatch rows.

Call only from a bounded writer worker. The source cursor must have its own
Keeper-attested committed prefix before intent publication; this publisher
never admits a signal to trading and never creates missing tables.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from src.backend.signal_dispatch_insert_dispatch import (
    SignalDispatchInsertDispatch, dispatch_family_hash,
)
from src.backend.signal_dispatch_typed_cursor import (
    ACK, ACK_COMMIT, INTENT, INTENT_COMMIT, TypedTable,
    verify_dispatch_cursor, verify_dispatch_intents,
)
from src.backend.signal_stream_typed_readback import canonical_row
from src.trading_runtime.journal_contract import canonical_json


def _literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _exact_rows(client: Any, table: TypedTable, *,
                physical_name: str,
                session_key: str, sequence: int,
                maximum: int) -> list[dict[str, Any]]:
    columns = ",".join(name for name, _ in table.columns)
    sql = (f"SELECT {columns} FROM arte.{physical_name} "
           f"WHERE session_key={_literal(session_key)} "
           f"AND source_batch_sequence={sequence} "
           f"LIMIT {maximum + 1} FORMAT JSONEachRow")
    result = [canonical_row(table, json.loads(line)) for line in
              client.execute(sql).splitlines() if line.strip()]
    if len(result) > maximum:
        raise ValueError("typed dispatch readback exceeds bounded batch")
    if table in (INTENT, ACK):
        result.sort(key=lambda row: row["ordinal"])
    return result


def _publish_family(
    client: Any, dispatch: SignalDispatchInsertDispatch, *,
    run_id: str, session_key: str, sequence: int, phase: str,
    table: TypedTable, rows: list[Mapping[str, Any]], maximum: int,
) -> None:
    digest = dispatch_family_hash(tuple(row["content_hash"] for row in rows)) if rows else None
    physical = dispatch.physical(table.name)
    if rows:
        columns = ",".join(name for name, _ in table.columns)
        token = f"dispatch:{run_id}:{sequence}:{physical}:{digest}"
        sql = (f"INSERT INTO arte.{physical} ({columns}) SETTINGS "
               "async_insert=1,wait_for_async_insert=1,insert_deduplicate=1,"
               f"insert_deduplication_token='{token}' FORMAT JSONEachRow\n" +
               "\n".join(canonical_json(row) for row in rows))
        dispatch.execute(client, run_id=run_id, sequence=sequence,
                         phase=phase, table=physical, row_hash=digest, sql=sql)
    readback = _exact_rows(client, table, physical_name=physical,
                           session_key=session_key,
                           sequence=sequence, maximum=maximum)
    if readback != rows:
        raise ValueError(f"typed dispatch {table.name} readback differs")
    if digest is not None:
        dispatch.seal_readback(run_id=run_id, sequence=sequence,
                               phase=phase, table=physical, row_hash=digest)


def publish_registered_intents(
    client: Any, dispatch: SignalDispatchInsertDispatch, *,
    run_id: str, projected: Mapping[str, Any],
) -> str:
    """Publish an exact intent fence before any activation writer submission."""
    verify_dispatch_intents(projected)
    intents = projected["intents"]
    commit = projected["commit"]
    if len(intents) > 256:
        raise ValueError("typed dispatch intent batch exceeds 256 deliveries")
    session_key, sequence = commit["session_key"], commit["source_batch_sequence"]
    families = ((INTENT, intents), (INTENT_COMMIT, [commit]))
    dispatch.reserve(run_id, sequence=sequence, phase="intent",
                     row_hashes={dispatch.physical(table.name): dispatch_family_hash(tuple(
                         row["content_hash"] for row in rows)) if rows else None
                                 for table, rows in families})
    for table, rows in families:
        _publish_family(client, dispatch, run_id=run_id,
                        session_key=session_key, sequence=sequence,
                        phase="intent", table=table, rows=rows,
                        maximum=256 if table == INTENT else 1)
    dispatch.finish_intent(
        run_id=run_id, sequence=sequence,
        intent_commit_hash=commit["content_hash"],
        commit_family_hash=dispatch_family_hash((commit["content_hash"],)))
    return commit["content_hash"]


def publish_registered_ack(
    client: Any, dispatch: SignalDispatchInsertDispatch, *,
    run_id: str, intents: Mapping[str, Any], projected: Mapping[str, Any],
) -> str:
    """Publish ACK only after exact committed intent and activation receipts."""
    verify_dispatch_cursor(intents, projected)
    rows = projected["acks"]
    commit = projected["commit"]
    if len(rows) > 256:
        raise ValueError("typed dispatch ACK batch exceeds 256 deliveries")
    session_key, sequence = commit["session_key"], commit["source_batch_sequence"]
    for table, expected in ((INTENT, intents["intents"]),
                            (INTENT_COMMIT, [intents["commit"]])):
        if _exact_rows(client, table, physical_name=dispatch.physical(table.name),
                       session_key=session_key,
                       sequence=sequence,
                       maximum=256 if table == INTENT else 1) != expected:
            raise ValueError("typed dispatch intent predecessor differs")
    families = ((ACK, rows), (ACK_COMMIT, [commit]))
    dispatch.reserve(run_id, sequence=sequence, phase="ack",
                     intent_commit_hash=intents["commit"]["content_hash"],
                     row_hashes={dispatch.physical(table.name): dispatch_family_hash(tuple(
                         row["content_hash"] for row in family)) if family else None
                                 for table, family in families})
    for table, family in families:
        _publish_family(client, dispatch, run_id=run_id,
                        session_key=session_key, sequence=sequence,
                        phase="ack", table=table, rows=family,
                        maximum=256 if table == ACK else 1)
    dispatch.finish_ack(
        run_id=run_id, sequence=sequence,
        intent_commit_hash=intents["commit"]["content_hash"],
        ack_commit_hash=commit["content_hash"],
        commit_family_hash=dispatch_family_hash((commit["content_hash"],)))
    return commit["content_hash"]
