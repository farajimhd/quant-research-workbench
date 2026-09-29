"""Read-only V4 checkpoint evidence across independent normalized families.

This attests a common cursor; it does not install mutable actors or enable
resume. A pure helper can reconstruct the fixed-bar broker image from pinned
liquidity and OMS; execution history and admission remain separate gates.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from src.backend.backtest_market_data import CertifiedMarketDayPlan
from src.backend.backtest_market_data import market_day_boundary
from src.backend.backtest_v4_broker_quote_restore import (
    CompletedBrokerQuote, load_completed_broker_quotes,
)
from src.backend.backtest_v4_broker_state_restore import reconstruct_broker_match_state
from src.backend.backtest_v4_execution_restore import load_v4_broker_executions
from src.backend.backtest_v4_running_portfolio import (
    load_v4_running_portfolio_images,
)
from src.trading_runtime.arte_journal_commit_v4 import (
    V4CommittedPrefix, load_verified_v4_prefix,
)
from src.trading_runtime.arte_oms_projection import (
    RecoveredStrategyOneOmsLineage, load_recovered_strategy_one_oms_lineage,
)
from src.trading_runtime.strategy_one_broker_match_snapshot import (
    BrokerMatchSnapshotRows, load_attested_broker_match_snapshot,
)
from src.trading_runtime.strategy_one_management_snapshot import (
    StrategyOneManagementState, load_attested_manager_snapshot,
)


@dataclass(frozen=True, slots=True)
class V4RunningRecoveryEvidence:
    prefix: V4CommittedPrefix
    portfolio_images: dict[str, dict[str, Any]]
    manager: StrategyOneManagementState
    broker: BrokerMatchSnapshotRows
    oms: tuple[RecoveredStrategyOneOmsLineage, ...]
    quotes: dict[str, CompletedBrokerQuote]


def reconstruct_v4_broker_state(evidence: V4RunningRecoveryEvidence) -> dict:
    """Build the exact open-order simulator image from joined cold evidence.

    This is an offline integrity step, not authorization to resume execution.
    Completed fills and execution history remain journal-owned.
    """
    if not isinstance(evidence, V4RunningRecoveryEvidence):
        raise TypeError("V4 broker restoration requires joined recovery evidence")
    requests = {}
    for lineage in evidence.oms:
        for binding in lineage.state.broker_bindings:
            index = binding["request_index"]
            if index is None or binding["terminal"]:
                continue
            if type(index) is not int or not 0 <= index < len(lineage.orders):
                raise RuntimeError("V4 broker restoration has invalid OMS binding")
            broker_id = binding["broker_order_id"]
            if broker_id in requests:
                raise RuntimeError("V4 broker restoration repeats OMS binding")
            requests[broker_id] = lineage.orders[index]
    open_ids = {row["broker_order_id"] for row in evidence.broker.open_orders}
    if not open_ids.issubset(requests):
        raise RuntimeError("V4 broker restoration lacks open OMS request")
    return reconstruct_broker_match_state(
        evidence.broker,
        requests_by_broker_id={key: requests[key] for key in open_ids},
        quotes=evidence.quotes,
    )


def load_v4_running_broker_image(client: Any,
                                 evidence: V4RunningRecoveryEvidence) -> dict:
    """Cold-join broker matching and full trade history at one V4 prefix.

    The result is an in-memory candidate only. No execution actor is installed
    and no resume admission is granted by this read-only projection.
    """
    state = reconstruct_v4_broker_state(evidence)
    requests_by_coid = {}
    coid_by_broker_id = {}
    for lineage in evidence.oms:
        for request in lineage.orders:
            if request.cOID in requests_by_coid:
                raise RuntimeError("V4 broker image repeats OMS client order")
            requests_by_coid[request.cOID] = request
        for binding in lineage.state.broker_bindings:
            index = binding["request_index"]
            if index is None:
                continue
            if type(index) is not int or not 0 <= index < len(lineage.orders):
                raise RuntimeError("V4 broker image has invalid OMS binding")
            broker_id = binding["broker_order_id"]
            if broker_id in coid_by_broker_id:
                raise RuntimeError("V4 broker image repeats broker order")
            coid_by_broker_id[broker_id] = lineage.orders[index].cOID
    state["executions"] = load_v4_broker_executions(
        client, evidence.prefix, requests_by_coid=requests_by_coid,
        coid_by_broker_id=coid_by_broker_id,
        next_execution_id=state["next_execution_id"],
    )
    root = evidence.broker.snapshot
    boundary = market_day_boundary(date.fromisoformat(root["session_date"]), 0)
    boundary += timedelta(milliseconds=int(root["boundary_ms"]))
    for execution in state["executions"]:
        at = datetime.fromisoformat(execution["trade_time"])
        if at.tzinfo is None or at.astimezone(timezone.utc) > boundary:
            raise RuntimeError("V4 broker execution exceeds completed boundary")
    if load_verified_v4_prefix(client, evidence.prefix.run_id) != evidence.prefix:
        raise RuntimeError("V4 broker image prefix moved across trade reads")
    return state


def load_v4_running_recovery_evidence(
    client: Any, *, run_id: str, account_ids: tuple[str, ...],
    manager_keeper: Any, broker_keeper: Any,
    market_client: Any, market_plan: CertifiedMarketDayPlan,
) -> V4RunningRecoveryEvidence:
    """Join every available recovery family at the same committed cursor."""
    prefix, portfolios = load_v4_running_portfolio_images(
        client, run_id=run_id, account_ids=account_ids)
    manager = load_attested_manager_snapshot(
        client, manager_keeper, run_id=run_id,
        checkpoint_sequence=prefix.last_sequence)
    broker = load_attested_broker_match_snapshot(
        client, broker_keeper, run_id=run_id,
        checkpoint_sequence=prefix.last_sequence)
    if (not isinstance(manager, StrategyOneManagementState)
            or not isinstance(broker, BrokerMatchSnapshotRows)
            or broker.snapshot.get("checkpoint_sequence") != prefix.last_sequence
            or {row["account_id"] for row in broker.accounts} != set(account_ids)
            or len(broker.accounts) != len(account_ids)
            or manager.boundary_ms != broker.snapshot.get("boundary_ms")
            or any(key[0] not in portfolios
                   for family in (manager.submitted, manager.positions,
                                  manager.pending_breaks)
                   for key, _ in family)):
        raise RuntimeError("V4 recovery families differ from pinned accounts or cursor")
    oms = load_recovered_strategy_one_oms_lineage(
        client, prefix, allowed_accounts=frozenset(account_ids))
    requests = {}
    broker_bindings = {}
    seen_broker_ids = set()
    for lineage in oms:
        if (not isinstance(lineage, RecoveredStrategyOneOmsLineage)
                or lineage.through_sequence != prefix.last_sequence
                or lineage.state.group.get("account_id") not in portfolios):
            raise RuntimeError("V4 OMS lineage differs from pinned cursor")
        for request in lineage.orders:
            key = request.cOID
            if key in requests or request.acctId not in portfolios:
                raise RuntimeError("V4 OMS recovery repeats or changes an order identity")
            requests[key] = request
        for binding in lineage.state.broker_bindings:
            broker_id = binding["broker_order_id"]
            index = binding["request_index"]
            if broker_id in seen_broker_ids:
                raise RuntimeError("V4 OMS recovery repeats a broker order identity")
            seen_broker_ids.add(broker_id)
            if index is None:
                continue
            if type(index) is not int or not 0 <= index < len(lineage.orders):
                raise RuntimeError("V4 OMS broker binding has no request")
            broker_bindings[broker_id] = (
                lineage.orders[index].cOID, bool(binding["terminal"]))
    for order in broker.open_orders:
        request = requests.get(order["client_order_id"])
        binding = broker_bindings.get(order.get("broker_order_id"))
        if (request is None or request.acctId != order["account_id"]
                or request.conid != order["conid"]
                or request.ticker != order["ticker"]
                or binding != (request.cOID, False)):
            raise RuntimeError("V4 broker open order lacks exact OMS lineage")
    quotes = load_completed_broker_quotes(
        market_client, plan=market_plan, broker=broker)
    if load_verified_v4_prefix(client, run_id) != prefix:
        raise RuntimeError("V4 recovery prefix moved across domain reads")
    return V4RunningRecoveryEvidence(prefix, portfolios, manager, broker, oms,
                                     quotes)
