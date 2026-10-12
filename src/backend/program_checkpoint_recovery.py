"""Concurrent cold checkpoint reads with one common completed app boundary."""
import asyncio
from dataclasses import dataclass
from threading import Lock

from .qmd_columnar_history_transport import _owned_worker
from .backtest_program_journal_startup import _settle_owned_task
from .backtest_market_data import market_day_boundary
from .program_financial_recovery import recover_program_portfolio_async
from src.trading_runtime.arte_broker_match_snapshot import (
    ManagedSharedBrokerMatchHeadReader, load_attested_shared_broker_snapshot,
)
from src.trading_runtime.arte_oms_observation_snapshot import (
    ManagedSharedOmsObservationHeadReader, load_attested_shared_oms_observations,
)
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix, load_verified_v4_prefix
from src.trading_runtime.arte_journal_projection import load_latest_backtest_cursor
from src.trading_runtime.backtest_broker_contract import BacktestBrokerContract
from src.trading_runtime.portfolio import PortfolioAccountProfile


@dataclass(frozen=True, slots=True)
class ProgramFinancialCheckpoint:
    prefix: V4CommittedPrefix
    broker: object
    observations: object
    portfolio: object
    completed_at: object


async def recover_program_financial_checkpoint(client_factory, *, prefix, broker_contract,
        broker_head_reader, observation_head_reader, profiles, broker_read_concurrency):
    """Join attested broker/observations and exact account revisions concurrently.

    This returns cold evidence, without installing actors or admitting restart.
    Source, programme policy and complete OMS lineage remain separate proofs.
    """
    profiles = tuple(profiles)
    if (not callable(client_factory) or type(prefix) is not V4CommittedPrefix
            or prefix.status != "running" or prefix.last_sequence < 1
            or type(broker_contract) is not BacktestBrokerContract
            or broker_contract.row["run_id"] != prefix.run_id
            or type(broker_head_reader) is not ManagedSharedBrokerMatchHeadReader
            or type(observation_head_reader) is not ManagedSharedOmsObservationHeadReader
            or broker_head_reader._session is not observation_head_reader._session
            or not profiles or any(type(row) is not PortfolioAccountProfile or row.mode != "backtest" for row in profiles)
            or len({row.account_id for row in profiles}) != len(profiles)
            or type(broker_read_concurrency) is not int or not 1 <= broker_read_concurrency <= 8):
        raise ValueError("Programme financial checkpoint needs one bounded central recovery scope")
    session = broker_head_reader._session
    keeper, generation, client_id = session.client, session._generation, session.client.client_id
    connections, connection_lock = [], Lock()
    def owned_factory():
        client = client_factory()
        with connection_lock:
            if any(client is existing for existing in connections):
                raise ValueError("Programme checkpoint reads cannot reuse an owned connection")
            connections.append(client)
        return client
    def guard():
        client = owned_factory()
        try:
            if load_verified_v4_prefix(client, prefix.run_id) != prefix:
                raise ValueError("Programme financial checkpoint prefix moved")
            cursor = load_latest_backtest_cursor(client, prefix)
            heads = (broker_head_reader.read_head(run_id=prefix.run_id),
                     observation_head_reader.read_head(run_id=prefix.run_id))
            if (not isinstance(cursor, dict) or cursor.get("run_id") != prefix.run_id
                    or cursor.get("event_sequence") != prefix.last_sequence
                    or cursor.get("batch_id") != prefix.last_batch_id
                    or any(head.checkpoint_sequence != prefix.last_sequence
                           or head.journal_batch_id != prefix.last_batch_id for head in heads)
                    or load_verified_v4_prefix(client, prefix.run_id) != prefix):
                raise ValueError("Programme financial checkpoint lacks one selected completed cursor")
            from datetime import date
            at = market_day_boundary(date.fromisoformat(cursor["session_date"]), cursor["boundary_ms"])
            if (broker_head_reader._session is not session or observation_head_reader._session is not session
                    or session.client is not keeper or not session.writable
                    or session._generation != generation or keeper.client_id != client_id):
                raise RuntimeError("Programme financial checkpoint Keeper session changed")
            return cursor, heads, at
        finally:
            client.close()
    initial = await _owned_worker(guard)
    job = asyncio.ensure_future(asyncio.gather(
        load_attested_shared_broker_snapshot(owned_factory, broker_head_reader,
            concurrency=broker_read_concurrency, prefix=prefix, broker_contract=broker_contract),
        load_attested_shared_oms_observations(owned_factory, observation_head_reader, prefix=prefix),
        recover_program_portfolio_async(owned_factory, prefix, profiles=profiles, cutoff_at=initial[2]),
        return_exceptions=True))
    try:
        results = await asyncio.shield(job)
    except asyncio.CancelledError:
        await _settle_owned_task(job)
        raise
    errors = [row for row in results if isinstance(row, BaseException)]
    if len(errors) == 1:
        raise errors[0]
    if errors:
        raise BaseExceptionGroup("Programme financial checkpoint reads failed", errors)
    if await _owned_worker(guard) != initial:
        raise ValueError("Programme financial checkpoint selection moved during parallel reads")
    broker, observations, portfolio = results
    if (broker.snapshot["session_date"] != observations.root["session_date"]
            or broker.snapshot["boundary_ms"] != observations.root["boundary_ms"]
            or broker.snapshot["session_date"] != initial[0]["session_date"]
            or broker.snapshot["boundary_ms"] != initial[0]["boundary_ms"]
            or {row["account_id"] for row in broker.accounts} != {row.account_id for row in profiles}
            or len(broker.accounts) != len(profiles)
            or set(portfolio.revisions) != {row.account_id for row in profiles}
            or any(revision != prefix.last_sequence for revision in portfolio.revisions.values())):
        raise ValueError("Programme financial checkpoint images differ from their common revision")
    return ProgramFinancialCheckpoint(prefix, broker, observations, portfolio, initial[2])
