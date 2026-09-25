"""Inactive terminal V3 INSERT admission with durable Keeper operation identities.

This adapter is deliberately separate from the running-batch dispatcher: the
running prefix is cold-fenced while terminal facts are published. A lost
response leaves an operation in ``started`` and the cold gate closed.
"""
from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Any

from src.backend.backtest_squeeze_v3_keeper import V3ColdFence
from src.trading_runtime.arte_typed_insert_dispatch import (
    TypedInsertDispatch, _gate_path, typed_insert_query_id,
)
from src.trading_runtime.keeper_ownership import KeeperUnavailable, _committed, _path


_TABLES = frozenset({
    "trading_event_v1", "trading_run_transition_v1",
    "trading_backtest_account_snapshot_v2", "trading_backtest_position_snapshot_v2",
    "trading_portfolio_snapshot_v1", "trading_portfolio_disabled_strategy_v1",
    "trading_portfolio_command_v1", "trading_portfolio_request_v1",
    "trading_portfolio_request_reason_v1", "trading_portfolio_reservation_v1",
    "trading_portfolio_allocation_v1", "trading_portfolio_reconciliation_v1",
    "trading_portfolio_snapshot_commit_v1", "trading_backtest_snapshot_anchor_v1",
    "trading_backtest_terminal_commit_v3",
})
_INSERT = re.compile(
    r"INSERT INTO arte\.([a-z0-9_]+) \([^\n]+?"
    r"insert_deduplication_token='([^'\n]+)' FORMAT JSONEachRow\n[\s\S]+\Z"
)


def _root(run_id: str, batch_id: str) -> str:
    return _path("backtest_terminal_v3_operations", run_id, batch_id)


def _decode(value: bytes, *, run_id: str, batch_id: str) -> tuple[str, str, str, str, str]:
    try:
        version, actual_run, actual_batch, table, token, query_id, sql_hash, status = (
            value.decode("ascii").split("\n"))
    except (ValueError, UnicodeError) as exc:
        raise KeeperUnavailable("V3 terminal operation is corrupt") from exc
    if (version != "1" or actual_run != run_id or actual_batch != batch_id
            or table not in _TABLES
            or query_id != typed_insert_query_id(run_id, table, token)
            or re.fullmatch(r"[0-9a-f]{64}", sql_hash) is None
            or status not in {"started", "acknowledged"}):
        raise KeeperUnavailable("V3 terminal operation identity is invalid")
    return table, token, query_id, sql_hash, status


class TerminalV3DispatchClient:
    """Proxy SELECTs; persist and acknowledge every exact suffix INSERT."""

    def __init__(self, client: Any, dispatch: TypedInsertDispatch,
                 fence: V3ColdFence, *, run_id: str, batch_id: str,
                 last_sequence: int) -> None:
        if (not isinstance(dispatch, TypedInsertDispatch)
                or fence.barrier.authority is not dispatch
                or fence.barrier.run_id != run_id):
            raise ValueError("V3 terminal writer differs from held cold authority")
        self._client = client
        self._dispatch = dispatch
        self._fence = fence
        self.run_id = run_id
        self.batch_id = batch_id
        self.last_sequence = last_sequence
        self._root = _root(run_id, batch_id)
        self._acknowledged_tables: frozenset[str] | None = None
        self._acknowledged_keys: frozenset[tuple[str, str]] | None = None

    def execute(self, sql: str) -> Any:
        if not sql.startswith("INSERT "):
            return self._client.execute(sql)
        match = _INSERT.fullmatch(sql)
        if match is None or match.group(1) not in _TABLES:
            raise ValueError("V3 terminal INSERT is outside closed typed families")
        table, token = match.groups()
        portfolio_token = token.startswith(f"portfolio-state:{self.run_id}:") and (
            f":{self.last_sequence}:" in token)
        if self.batch_id not in token and not portfolio_token:
            raise ValueError("V3 terminal INSERT lacks pinned batch identity")
        body = sql.split("FORMAT JSONEachRow\n", 1)[1].splitlines()
        if not body or len(body) > 100_000:
            raise ValueError("V3 terminal INSERT row count is invalid")
        for line in body:
            try:
                row = json.loads(line)
            except (TypeError, ValueError) as exc:
                raise ValueError("V3 terminal INSERT row is invalid") from exc
            if (not isinstance(row, dict) or row.get("run_id") != self.run_id
                    or ("batch_id" in row and str(row["batch_id"]) != self.batch_id)
                    or ("state_revision" in row and
                        int(row["state_revision"]) != self.last_sequence)):
                raise ValueError("V3 terminal INSERT row differs from pinned run")
        query_id = typed_insert_query_id(self.run_id, table, token)
        path = f"{self._root}/{query_id}"
        base = (f"1\n{self.run_id}\n{self.batch_id}\n{table}\n{token}\n{query_id}\n"
                f"{sha256(sql.encode()).hexdigest()}\n").encode("ascii")
        started, acknowledged = base + b"started", base + b"acknowledged"
        keeper = self._dispatch.keeper
        self._fence.barrier.assert_fenced(self.run_id)
        keeper.ensure_path(self._root)
        gate, version = self._dispatch._read_gate(self.run_id)
        if gate.mode != "closed":
            raise KeeperUnavailable("V3 terminal cold gate is open")
        # Only exact acknowledged retries can skip transport; higher-level
        # family readback must still verify full row identity.
        try:
            existing, _ = keeper.get(path)
        except Exception as exc:
            if type(exc).__name__ != "NoNodeError":
                raise KeeperUnavailable("V3 terminal operation cannot be read") from exc
        else:
            if existing == acknowledged:
                return ""
            raise KeeperUnavailable("V3 terminal INSERT response is ambiguous")
        txn = keeper.transaction()
        txn.check(_gate_path(self.run_id), version=version)
        txn.create(path, started, ephemeral=False)
        # A lost CAS response can mean the operation was registered. Keep the
        # running gate closed even when admission itself is uncertain.
        self._fence.retain_on_failure = True
        if not _committed(txn.commit()):
            raise KeeperUnavailable("V3 terminal operation admission is uncertain")
        # A transport exception leaves ``started`` durable and the run closed.
        result = self._client.execute(sql, query_id=query_id)
        self._fence.barrier.assert_fenced(self.run_id)
        value, stat = keeper.get(path)
        if value != started:
            raise KeeperUnavailable("V3 terminal operation changed during INSERT")
        _, version = self._dispatch._read_gate(self.run_id)
        txn = keeper.transaction()
        txn.check(_gate_path(self.run_id), version=version)
        txn.set_data(path, acknowledged, version=stat.version)
        if not _committed(txn.commit()):
            raise KeeperUnavailable("V3 terminal acknowledgement is uncertain")
        return result

    def acknowledged_operations(self) -> tuple[tuple[str, int, str], ...]:
        """Exact complete inventory for the V3 terminal proof CAS."""
        self._fence.barrier.assert_fenced(self.run_id)
        keeper = self._dispatch.keeper
        try:
            children = keeper.get_children(self._root)
        except Exception as exc:
            raise KeeperUnavailable("V3 terminal operation inventory unavailable") from exc
        if not children or len(children) > self._dispatch.max_operations:
            raise KeeperUnavailable("V3 terminal operation count is invalid")
        result = []
        tables = set()
        keys = set()
        for child in sorted(children):
            path = f"{self._root}/{child}"
            value, stat = keeper.get(path)
            table, token, query_id, sql_hash, status = _decode(
                value, run_id=self.run_id, batch_id=self.batch_id)
            if query_id != child or status != "acknowledged":
                raise KeeperUnavailable("V3 terminal has unresolved operation")
            tables.add(table)
            keys.add((table, token))
            result.append((path, stat.version, sha256(value).hexdigest()))
        if "trading_backtest_terminal_commit_v3" not in tables:
            raise KeeperUnavailable("V3 terminal seal lacks acknowledged INSERT")
        self._acknowledged_tables = frozenset(tables)
        self._acknowledged_keys = frozenset(keys)
        return tuple(result)

    def assert_covers(self, required_tables: set[str]) -> None:
        if self._acknowledged_tables is None:
            raise KeeperUnavailable("V3 terminal operations were not inventoried")
        if not required_tables <= self._acknowledged_tables:
            raise KeeperUnavailable("V3 terminal adopted unattested family rows")

    def assert_account_covers(
        self, account_tables: dict[str, set[str]],
    ) -> None:
        if self._acknowledged_keys is None:
            raise KeeperUnavailable("V3 terminal operations were not inventoried")
        for account_id, tables in account_tables.items():
            for table in tables:
                token = (f"terminal-v2:{self.batch_id}:anchor:{account_id}"
                         if table == "trading_backtest_snapshot_anchor_v1" else
                         f"portfolio-state:{self.run_id}:{account_id}:"
                         f"{self.last_sequence}:"
                         f"{'commit' if table == 'trading_portfolio_snapshot_commit_v1' else table}")
                if (table, token) not in self._acknowledged_keys:
                    raise KeeperUnavailable("V3 terminal adopted unattested account rows")
