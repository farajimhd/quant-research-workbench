"""Cold simulator trade history from normalized committed V4 journal facts.

This is a read-only, bounded projection. It does not authorize resuming a run;
the shared portfolio, OMS, strategy manager, and market cursor must also pass.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.trading_runtime.arte_journal_writer import (
    load_committed_commission_page, load_committed_execution_page,
)
from src.trading_runtime.ibkr_schema import OrderRequest


def _pages(loader: Any, client: Any, prefix: V4CommittedPrefix,
           *, maximum: int) -> tuple[dict, ...]:
    rows: list[dict] = []
    after = 0
    while True:
        page = loader(client, prefix, after_sequence=after, limit=1000)
        if len(rows) + len(page) > maximum:
            raise RuntimeError("V4 broker trade history exceeds recovery bound")
        rows.extend(page)
        if len(page) < 1000:
            return tuple(rows)
        next_after = int(page[-1]["sequence"])
        if next_after <= after:
            raise RuntimeError("V4 broker trade history cursor did not advance")
        after = next_after


def reconstruct_broker_executions(
    fills: tuple[Mapping[str, Any], ...],
    commissions: tuple[Mapping[str, Any], ...], *,
    requests_by_coid: Mapping[str, OrderRequest],
    coid_by_broker_id: Mapping[str, str],
    next_execution_id: int,
) -> list[dict[str, Any]]:
    """Rebuild the simulator's exact execution cache at a committed boundary."""
    if type(next_execution_id) is not int or next_execution_id < 1:
        raise ValueError("V4 broker execution counter is invalid")
    final_fees = {}
    for fee in commissions:
        identity = str(fee["execution_id"])
        sequence = int(fee["sequence"])
        if identity not in final_fees or sequence > int(final_fees[identity]["sequence"]):
            final_fees[identity] = fee
    if set(final_fees) != {str(fill["execution_id"]) for fill in fills}:
        raise RuntimeError("V4 broker fee identities differ from executions")
    if len(fills) != next_execution_id - 1:
        raise RuntimeError("V4 broker execution count differs from checkpoint")
    results = []
    seen = set()
    for fill in fills:
        identity = str(fill["execution_id"])
        if not identity.startswith("SIM-") or not identity[4:].isdigit():
            raise RuntimeError("V4 broker execution identity is not simulator-owned")
        ordinal = int(identity[4:])
        if not 1 <= ordinal < next_execution_id or ordinal in seen:
            raise RuntimeError("V4 broker execution identity repeats or exceeds checkpoint")
        seen.add(ordinal)
        fee = final_fees.get(identity)
        if (fee is None or str(fee["status"]).lower() != "final"
                or fee["account_id"] != fill["account_id"]
                or fee["currency"] != fill["currency"]):
            raise RuntimeError("V4 broker execution lacks final matching commission")
        request = requests_by_coid.get(str(fill["client_order_id"]))
        if (request is None or request.acctId != fill["account_id"]
                or coid_by_broker_id.get(str(fill["broker_order_id"])) != request.cOID
                or request.conid != int(fill["conid"])
                or request.ticker != fill["ticker"]
                or request.side.upper() != {"B": "BUY", "S": "SELL"}.get(
                    str(fill["side"]).upper())):
            raise RuntimeError("V4 broker execution lacks exact OMS request")
        at = datetime.fromisoformat(str(fill["source_event_time"]).replace("Z", "+00:00"))
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        at = at.astimezone(timezone.utc)
        results.append(dict(
            execution_id=identity, symbol=str(fill["ticker"]),
            side=str(fill["side"]), order_ref=request.cOID,
            trade_time=at.isoformat(), trade_time_r=int(at.timestamp() * 1000),
            size=float(fill["quantity"]), price=float(fill["price"]),
            order_id=str(fill["broker_order_id"]), account=request.acctId,
            conid=request.conid, commission=float(fee["commission"]),
            currency=str(fill["currency"]),
            raw={
                "strategy_id": request.raw.get("canonical_strategy_id", ""),
                "canonical_strategy_revision": request.raw.get(
                    "canonical_strategy_revision", 0),
                "canonical_run_id": request.raw.get("canonical_run_id", ""),
                "canonical_metadata": request.raw.get("canonical_metadata", {}),
            },
        ))
    if seen != set(range(1, next_execution_id)):
        raise RuntimeError("V4 broker execution identities have a gap")
    return sorted(results, key=lambda row: int(row["execution_id"][4:]))


def load_v4_broker_executions(
    client: Any, prefix: V4CommittedPrefix, *,
    requests_by_coid: Mapping[str, OrderRequest],
    coid_by_broker_id: Mapping[str, str],
    next_execution_id: int, maximum: int = 100_000,
) -> list[dict[str, Any]]:
    """SELECT committed normalized facts; never read a disk journal."""
    if (not isinstance(prefix, V4CommittedPrefix) or not prefix.batch_ids
            or type(maximum) is not int or not 1 <= maximum <= 100_000
            or next_execution_id - 1 > maximum):
        raise ValueError("V4 broker execution recovery scope is invalid")
    fills = _pages(load_committed_execution_page, client, prefix, maximum=maximum)
    fees = _pages(load_committed_commission_page, client, prefix, maximum=maximum * 4)
    return reconstruct_broker_executions(
        fills, fees, requests_by_coid=requests_by_coid,
        coid_by_broker_id=coid_by_broker_id,
        next_execution_id=next_execution_id)
