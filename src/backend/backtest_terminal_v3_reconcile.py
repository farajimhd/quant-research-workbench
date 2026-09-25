"""Inactive cold reconciliation for an ambiguous terminal V3 INSERT.

Only a fully visible, exact typed family can turn a durable ``started``
operation into ``acknowledged``. Absence, partial visibility, or a mismatched
operation never triggers another ClickHouse INSERT and leaves the run closed.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from hashlib import sha256
import json
from typing import Any
from uuid import UUID

from src.backend.backtest_fixed_v3_preflight import read_v3_preflight
from src.backend.backtest_squeeze_v3_keeper import verify_retained_v3_cold_gate
from src.backend.backtest_terminal_v2_fence import _verify_rows
from src.backend.backtest_terminal_v2_keeper import FixedTerminalKeeperAuthority
from src.backend.backtest_terminal_v3_dispatch import _INSERT, _TABLES, _decode, _root
from src.backend.backtest_terminal_v3_fence import TERMINAL_COMMIT_V3
from src.trading_runtime.arte_journal_schema import fixed_backtest_v2_contracts
from src.trading_runtime.arte_journal_writer import (
    _canonical_typed_content, _datetime_wire, _literal,
)
from src.trading_runtime.arte_typed_insert_dispatch import (
    TypedInsertDispatch, _gate_path, typed_insert_query_id,
)
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.keeper_ownership import KeeperUnavailable, _committed, _path


_CONTRACTS = {contract.name: contract for contract in fixed_backtest_v2_contracts()}
_CONTRACTS[TERMINAL_COMMIT_V3.name] = TERMINAL_COMMIT_V3
if not _TABLES <= _CONTRACTS.keys():
    raise RuntimeError("V3 reconciliation lacks a closed typed table contract")
_V2_ROWS = {"trading_backtest_account_snapshot_v2",
            "trading_backtest_position_snapshot_v2"}


def _normalized(table: str, row: dict[str, Any], *, stored: bool) -> dict[str, Any]:
    contract = _CONTRACTS[table]
    if set(row) != {name for name, _ in contract.columns}:
        raise ValueError("V3 reconciliation row columns differ from typed schema")
    if table in _V2_ROWS:
        return _verify_rows(table, (row,))[0]
    if table == TERMINAL_COMMIT_V3.name:
        result = dict(row)
        for column, kind in contract.columns:
            if kind == "UUID":
                result[column] = str(UUID(str(row[column])))
            elif kind == "Date":
                result[column] = date.fromisoformat(str(row[column])).isoformat()
            elif kind.startswith("DateTime64(6"):
                result[column] = _datetime_wire(row[column], 6, stored_utc=stored)
        return result
    has_hash = "content_hash" in row
    content = {key: value for key, value in row.items() if key != "content_hash"}
    canonical = _canonical_typed_content(table, content, stored_utc=stored)
    if has_hash:
        digest = sha256(canonical_json(canonical).encode()).hexdigest()
        if digest != row["content_hash"]:
            raise ValueError("V3 reconciliation row hash differs")
        canonical["content_hash"] = digest
    return canonical


def _exact_visible_rows(read_client: Any, *, table: str, run_id: str,
                        batch_id: str, last_sequence: int,
                        expected: tuple[dict[str, Any], ...]) -> None:
    names = {name for name, _ in _CONTRACTS[table].columns}
    clauses = [f"run_id={_literal(run_id)}"]
    if "batch_id" in names:
        clauses.append(f"batch_id=toUUID({_literal(batch_id)})")
    elif "account_id" in names and "state_revision" in names:
        accounts = {row["account_id"] for row in expected}
        if len(accounts) != 1 or any(row["state_revision"] != last_sequence
                                     for row in expected):
            raise ValueError("V3 portfolio operation spans account revisions")
        clauses.extend((f"account_id={_literal(next(iter(accounts)))}",
                        f"state_revision={last_sequence}"))
    elif "account_id" in names:
        accounts = {row["account_id"] for row in expected}
        if len(accounts) != 1:
            raise ValueError("V3 terminal operation spans accounts")
        clauses.append(f"account_id={_literal(next(iter(accounts)))}")
    sql = (f"SELECT * FROM arte.{table} WHERE " + " AND ".join(clauses)
           + " FORMAT JSONEachRow")
    raw = read_client.execute(sql)
    try:
        actual = tuple(json.loads(line) for line in raw.splitlines() if line.strip())
        expected_rows = Counter(canonical_json(_normalized(table, row, stored=False))
                                for row in expected)
        actual_rows = Counter(canonical_json(_normalized(table, row, stored=True))
                              for row in actual)
    except (TypeError, ValueError, KeyError) as exc:
        raise KeeperUnavailable("V3 terminal typed readback is malformed") from exc
    if actual_rows != expected_rows:
        raise KeeperUnavailable("V3 terminal INSERT is absent, partial, or conflicting")


def acknowledge_visible_terminal_v3_operation(
    read_client: Any, authority: FixedTerminalKeeperAuthority,
    dispatch: TypedInsertDispatch, *, run_id: str,
    account_ids: tuple[str, ...], batch_id: str, last_sequence: int,
    expected_market_plan_token: str, expected_query_sha256: str,
    deterministic_sql: str,
) -> str:
    """Acknowledge one uncertain op only after exact cold readback; never INSERT.

    The original deterministic SQL is required because the durable operation
    stores its hash, not an opaque copy of the rows. A returned acknowledgement
    does not certify the terminal suffix or reopen running admission.
    """
    if (not isinstance(authority, FixedTerminalKeeperAuthority)
            or not isinstance(dispatch, TypedInsertDispatch)
            or dispatch.keeper is not authority.keeper._client
            or read_client is authority.client._client
            or type(last_sequence) is not int or last_sequence < 1):
        raise ValueError("V3 reconciliation lacks distinct held authority")
    match = _INSERT.fullmatch(deterministic_sql)
    if match is None or match.group(1) not in _TABLES:
        raise ValueError("V3 reconciliation requires the exact typed INSERT")
    table, token = match.groups()
    batch_id = str(UUID(batch_id))
    if (batch_id not in token
            and not (token.startswith(f"portfolio-state:{run_id}:")
                     and f":{last_sequence}:" in token)):
        raise ValueError("V3 reconciliation token lacks pinned suffix identity")
    query_id = typed_insert_query_id(run_id, table, token)
    path = f"{_root(run_id, batch_id)}/{query_id}"
    try:
        expected = tuple(json.loads(line) for line in deterministic_sql.split(
            "FORMAT JSONEachRow\n", 1)[1].splitlines() if line.strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("V3 reconciliation SQL rows are invalid") from exc
    if (not expected or len(expected) > 100_000
            or any(not isinstance(row, dict) or row.get("run_id") != run_id
                   or ("batch_id" in row and str(row["batch_id"]) != batch_id)
                   for row in expected)):
        raise ValueError("V3 reconciliation SQL differs from pinned run")
    authority.assert_current(run_id, account_ids)
    read_v3_preflight(read_client)
    keeper = dispatch.keeper
    gate, gate_version = dispatch._read_gate(run_id)
    if gate.mode != "closed" or gate.inflight or gate.registered:
        raise KeeperUnavailable("V3 reconciliation needs a quiescent closed gate")
    lock = _path("backtest_terminal_v3_reconcile", run_id, batch_id)
    keeper.ensure_path(lock.rsplit("/", 1)[0])
    txn = keeper.transaction()
    txn.check(_gate_path(run_id), version=gate_version)
    txn.create(lock, b"1", ephemeral=True)
    if not _committed(txn.commit()):
        raise KeeperUnavailable("V3 reconciliation claim is unavailable")
    try:
        lock_value, lock_stat = keeper.get(lock)
        if lock_value != b"1" or lock_stat.ephemeralOwner != keeper.client_id[0]:
            raise KeeperUnavailable("V3 reconciliation claim changed")
        fence = verify_retained_v3_cold_gate(
            read_client, dispatch, run_id=run_id,
            expected_market_plan_token=expected_market_plan_token,
            expected_query_sha256=expected_query_sha256)
        fence.barrier.assert_fenced(run_id)
        if (last_sequence <= fence.prefix.last_sequence
                or any("sequence" in row and not (
                    fence.prefix.last_sequence < int(row["sequence"]) <= last_sequence)
                       for row in expected)
                or (table == TERMINAL_COMMIT_V3.name and any(
                    int(row["prior_v3_sequence"]) != fence.prefix.last_sequence
                    or str(row["prior_v3_batch_id"]) != fence.prefix.last_batch_id
                    or int(row["last_sequence"]) != last_sequence
                    for row in expected))):
            raise KeeperUnavailable("V3 terminal operation lacks causal suffix identity")
        value, stat = keeper.get(path)
        stored_table, stored_token, stored_query, sql_hash, status = _decode(
            value, run_id=run_id, batch_id=batch_id)
        if (stored_table != table or stored_token != token
                or stored_query != query_id
                or sql_hash != sha256(deterministic_sql.encode()).hexdigest()
                or path.rsplit("/", 1)[1] != query_id):
            raise KeeperUnavailable("V3 reconciliation operation identity differs")
        _exact_visible_rows(
            read_client, table=table, run_id=run_id, batch_id=batch_id,
            last_sequence=last_sequence, expected=expected)
        authority.assert_current(run_id, account_ids)
        fence.barrier.assert_fenced(run_id)
        if status == "acknowledged":
            return query_id
        acknowledged = value[:-len(b"started")] + b"acknowledged"
        _, version = dispatch._read_gate(run_id)
        txn = keeper.transaction()
        txn.check(_gate_path(run_id), version=version)
        txn.check(lock, version=lock_stat.version)
        txn.set_data(path, acknowledged, version=stat.version)
        if not _committed(txn.commit()):
            raise KeeperUnavailable("V3 reconciliation acknowledgement is uncertain")
        reread, _ = keeper.get(path)
        if reread != acknowledged:
            raise KeeperUnavailable("V3 reconciliation acknowledgement changed")
        authority.assert_current(run_id, account_ids)
        return query_id
    finally:
        # The lock is ephemeral and never authorizes opening the cold gate.
        try:
            stat = keeper.exists(lock)
            if stat is not None and stat.ephemeralOwner == keeper.client_id[0]:
                keeper.delete(lock, version=stat.version)
        except Exception as exc:
            raise KeeperUnavailable("V3 reconciliation claim cleanup is uncertain") from exc
