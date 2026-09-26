"""Pure V4 terminal broker snapshot block normalization.

Broker account/position evidence is distinct from the portfolio recovery
capture. It must survive as tabular Float64 rows with complete grouping and
source identities; the V4 writer does not publish this product yet.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
from uuid import UUID

from src.backend.backtest_terminal_snapshot_v2 import project_snapshot_group
from src.backend.backtest_terminal_v2_fence import seal_v2_row
from src.trading_runtime.journal_contract import JournalRecord


@dataclass(frozen=True, slots=True)
class V4BrokerSnapshotRows:
    accounts: tuple[Mapping[str, object], ...]
    positions: tuple[Mapping[str, object], ...]
    first_sequence: int
    last_sequence: int


def project_v4_terminal_broker_snapshots(
    records: tuple[JournalRecord, ...], *, run_id: str,
    account_ids: tuple[str, ...], batch_id: str,
) -> V4BrokerSnapshotRows:
    """Require complete account blocks immediately before terminal lifecycle."""
    UUID(batch_id)
    if (not records or not run_id or not account_ids
            or len(set(account_ids)) != len(account_ids)
            or any(row.run_id != run_id for row in records)
            or any(row.sequence != records[0].sequence + index
                   for index, row in enumerate(records))
            or (records[-1].category, records[-1].entity_type,
                records[-1].entity_id) != ("lifecycle", "run", run_id)
            or records[-1].payload.get("status") not in
            {"completed", "stopped", "failed"}):
        raise ValueError("V4 terminal broker snapshot suffix is incomplete")
    accounts = []
    positions = []
    offset = 0
    while offset < len(records) - 1:
        account = records[offset]
        count = account.payload.get("expected_position_count")
        if (account.category, account.entity_type) != ("snapshot", "portfolio") \
                or type(count) is not int or count < 0:
            raise ValueError("V4 terminal broker account block is invalid")
        children = records[offset + 1:offset + 1 + count]
        parent, child_rows = project_snapshot_group(
            account, children, batch_id=batch_id)
        accounts.append(seal_v2_row(
            "trading_backtest_account_snapshot_v2", parent))
        positions.extend(seal_v2_row(
            "trading_backtest_position_snapshot_v2", row)
            for row in child_rows)
        offset += count + 1
    if (offset != len(records) - 1
            or tuple(row["account_id"] for row in accounts)
            != account_ids
            or any(row.event_time != records[-1].event_time
                   for row in records[:-1])):
        raise ValueError("V4 terminal broker snapshot population differs from run")
    return V4BrokerSnapshotRows(
        tuple(accounts), tuple(positions), records[0].sequence,
        records[-1].sequence)
