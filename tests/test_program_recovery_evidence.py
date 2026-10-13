"""Composition guards; native attested readers have their own integration tests."""
import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from src.backend import program_recovery_evidence as joined
from src.backend.program_saved_definition import ProgramSavedDefinitionEvidence
from src.backend.program_checkpoint_recovery import ProgramFinancialCheckpoint
from src.backend.backtest_market_data import market_day_boundary
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix


@pytest.mark.parametrize("failure", [None, "accounts", "clock", "moved"])
def test_recovery_join_preserves_common_prefix_accounts_and_session_clock(monkeypatch, failure):
    prefix = V4CommittedPrefix("run", 10, "batch", "cursor", "running", ("batch",))
    definition = ProgramSavedDefinitionEvidence(prefix,
        dict(account_ids=("account",), session_date="2026-08-03"),
        dict(definition=dict(start_local_ms=14400000, end_local_ms=72000000)), {"content_hash": "a" * 64})
    reads, financial_calls = [], []
    async def read(factory, run_id, *, expected_code_hash):
        reads.append(run_id)
        if failure == "moved" and len(reads) == 2:
            return ProgramSavedDefinitionEvidence(prefix, definition.context, definition.saved, {})
        return definition
    async def financial(factory, **scope):
        assert scope["prefix"] is prefix
        financial_calls.append(scope)
        at = market_day_boundary(date(2026, 8, 3), 1000)
        if failure == "clock": at -= timedelta(days=1)
        return ProgramFinancialCheckpoint(prefix, object(), object(), object(), at)
    monkeypatch.setattr(joined, "load_program_saved_definition", read)
    monkeypatch.setattr(joined, "recover_program_financial_checkpoint", financial)
    async def run():
        kwargs = dict(expected_code_hash="a" * 64, broker_contract=object(), broker_head_reader=object(),
            observation_head_reader=object(), profiles=(SimpleNamespace(account_id="other" if failure == "accounts" else "account"),),
            broker_read_concurrency=2)
        if failure:
            with pytest.raises((ValueError, RuntimeError), match="accounts|session window|moved"):
                await joined.load_program_recovery_evidence(lambda: None, "run", **kwargs)
        else:
            result = await joined.load_program_recovery_evidence(lambda: None, "run", **kwargs)
            assert result.definition is definition and result.financial.prefix is prefix
            assert len(reads) == 2
    asyncio.run(run())
    assert len(financial_calls) == (0 if failure == "accounts" else 1)
