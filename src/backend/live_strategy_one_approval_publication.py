"""Operator-only Strategy 1 approval publication; never a live admission shortcut.

An unselected ClickHouse row is not approval. The persistent Keeper head is
created only after exact normalized row readback. A failed or ambiguous INSERT
leaves the mode unselected; the operator may inspect and retry the same ID.
"""
from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from src.backend.backtest_strategy_one_configuration import (
    CertifiedStrategyOneConfiguration,
)
from src.backend.live_strategy_one_approval import (
    ApprovalHead, KeeperApprovalHeadReader, TABLE, approval_row,
    read_approval_row, verify_selected_approval,
)
from src.trading_runtime.arte_journal_schema import storage_preflight
from src.trading_runtime.arte_journal_writer import _literal
from src.trading_runtime.keeper_session import ManagedKeeperSession


def publish_strategy_one_approval(*, write_client: Any, read_client: Any,
                                  session: ManagedKeeperSession,
                                  release: CertifiedStrategyOneConfiguration,
                                  approval_id: str, mode: str,
                                  approved_at_us: int,
                                  approver_id: str) -> dict[str, Any]:
    """Select one immutable row after exact SSD, row, and Keeper checks.

    The caller supplies an operator-owned INSERT client. Trading runtimes must
    never call this API or receive that credential. This function does not
    activate a strategy, recover an account, or submit an order.
    """
    if (write_client is read_client or not isinstance(session, ManagedKeeperSession)
            or not session.writable):
        raise ValueError("Strategy 1 approval requires distinct operator authorities")
    row = approval_row(approval_id=approval_id, mode=mode, release=release,
                       approved_at_us=approved_at_us, approver_id=approver_id)
    storage_preflight(read_client, tables=(TABLE,))
    reader = KeeperApprovalHeadReader(session)
    path = reader.path(mode)
    head = ApprovalHead(mode, row["approval_id"], row["content_hash"])
    try:
        session.client.get(path)
    except Exception as exc:
        if type(exc).__name__ != "NoNodeError":
            raise RuntimeError("Strategy 1 approval head cannot be inspected") from exc
    else:
        if reader.read_head(mode) != head:
            raise ValueError("Strategy 1 mode already selects a different approval")
        selected = verify_selected_approval(
            read_client, reader, mode=mode, release=release,
            expected_approval_id=row["approval_id"])
        if selected != row:
            raise ValueError("Strategy 1 selected approval differs from operator decision")
        return selected

    prior = read_approval_row(read_client, mode=mode,
                              approval_id=row["approval_id"], release=release,
                              required=False)
    if prior is not None and prior != row:
        raise ValueError("Strategy 1 approval ID already owns a different row")
    if prior is None:
        columns = ",".join(name for name, _ in TABLE.columns)
        token = sha256((mode + "\x00" + row["approval_id"] + "\x00"
                        + row["content_hash"]).encode("ascii")).hexdigest()
        query_id = "strategy_one_approval_" + token
        sql = (
            f"INSERT INTO arte.{TABLE.name} ({columns}) "
            "SETTINGS async_insert=1,wait_for_async_insert=1,insert_deduplicate=1,"
            f"insert_deduplication_token={_literal(token)} FORMAT JSONEachRow\n"
            + json.dumps(row, sort_keys=True, separators=(",", ":"))
        )
        # A lost HTTP response is ambiguous. Do not select the Keeper head;
        # the operator must cold-read the exact row before retrying.
        write_client.execute(sql, query_id=query_id)
    stored = read_approval_row(read_client, mode=mode,
                               approval_id=row["approval_id"], release=release)
    if stored != row:
        raise RuntimeError("Strategy 1 approval readback differs from INSERT")
    storage_preflight(read_client, tables=(TABLE,))
    if not session.writable:
        raise RuntimeError("Strategy 1 approval Keeper session changed before selection")
    session.client.ensure_path(path.rsplit("/", 1)[0])
    session.client.create(path,
                          f"1\n{mode}\n{row['approval_id']}\n{row['content_hash']}".encode(),
                          ephemeral=False)
    selected = verify_selected_approval(
        read_client, reader, mode=mode, release=release,
        expected_approval_id=row["approval_id"])
    if selected != row:
        raise RuntimeError("Strategy 1 approval selection differs from operator decision")
    return selected
