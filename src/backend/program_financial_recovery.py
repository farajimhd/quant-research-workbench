"""Owned cold reads of shared financial families for columnar programs.

These images preserve blocked admission until broker/OMS synchronization. They
are neither a strategy checkpoint nor permission to place orders.
"""
from datetime import datetime

from src.backend.qmd_columnar_history_transport import _owned_worker
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix, load_verified_v4_prefix
from src.trading_runtime.arte_portfolio_recovery import recover_portfolio_engine_state
from src.trading_runtime.portfolio import PortfolioAccountProfile


async def recover_program_portfolio_async(reader_factory, prefix, *, profiles, cutoff_at):
    """Read exact account revisions off-loop and reject movement of the fence."""
    profiles = tuple(profiles)
    if (type(prefix) is not V4CommittedPrefix or prefix.status != "running"
            or prefix.last_sequence < 1 or not prefix.batch_ids
            or not isinstance(cutoff_at, datetime) or cutoff_at.tzinfo is None
            or not profiles or any(type(row) is not PortfolioAccountProfile for row in profiles)
            or any(row.mode != "backtest" for row in profiles)
            or len({row.account_id for row in profiles}) != len(profiles)):
        raise ValueError("Program financial recovery requires a running typed backtest prefix")

    def read():
        client = reader_factory()
        try:
            if load_verified_v4_prefix(client, prefix.run_id) != prefix:
                raise ValueError("Program financial prefix changed before cold read")
            image = recover_portfolio_engine_state(client, run_id=prefix.run_id,
                profiles=profiles,
                state_revisions={row.account_id: prefix.last_sequence for row in profiles},
                cutoff_at=cutoff_at)
            if load_verified_v4_prefix(client, prefix.run_id) != prefix:
                raise ValueError("Program financial prefix changed during cold read")
            return image
        finally:
            client.close()

    return await _owned_worker(read)
