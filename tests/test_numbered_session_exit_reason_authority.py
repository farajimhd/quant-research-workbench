"""Real numbered session-source admission and independent source mutation checks."""
import asyncio
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.backend.backtest_fixed_v4_certification import certify_numbered_session_exit_reason_source
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.numbered_fixed_strategy import numbered_session_exit_reason
from src.trading_runtime.numbered_session_exit import numbered_session_exit_intent
from src.trading_runtime.runtime import TradingRuntime, RunMode
from src.trading_runtime.signals import StrategyEvaluation


EXPECTED = dict(zip(range(2, 18), (
    'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine',
    'ten', 'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen')))


def source(number):
    return numbered_session_exit_intent(
        session_date=date(2026, 8, 18), account_id='DU1', assignment_id='A1',
        ticker='AAA', boundary_ms=57_300_000, quantity=3.0, bid=10.0,
        strategy_number=number)


def runtime(number):
    """Real admission/journal; downstream Portfolio explicitly declines orders.

    No tested factory, reason helper, admission or memory method is replaced.
    This fixture deliberately ends after source admission; it does not claim
    an OMS submission or a fill.
    """
    value = object.__new__(TradingRuntime)
    value.config = SimpleNamespace(
        strategy_id='early-squeeze-strategy', strategy_revision=number,
        mode=RunMode.BACKTEST, anchor_date=date(2026, 8, 18))
    value.run_id = 'session-reason-authority'
    value.journal = BacktestMemoryJournal(run_id=value.run_id)
    value.intent_planner = object()
    value.order_manager = object()
    value.approvals = []

    async def approve(intent, *, account_id, assignment_id):
        value.approvals.append((intent, account_id, assignment_id))
        decision = SimpleNamespace(reasons=('entry_request_already_allocated',),
                                   payload=lambda: {'status': 'fixture_declined'})
        return decision, None

    async def no_funding(*args):
        pass

    value.portfolio = SimpleNamespace(approve=approve)
    value._fund_momentum_request = no_funding
    return value


@pytest.mark.parametrize('number', range(2, 18))
def test_factory_runtime_admission_and_typed_memory_share_exact_reason(number):
    intent = source(number)
    expected = f'strategy_{EXPECTED[number]}_session_exit'
    assert intent.reason == expected == numbered_session_exit_reason(number)
    assert intent.metadata == {}
    value = runtime(number)
    result = asyncio.run(value._execute_intents(
        StrategyEvaluation(intents=(intent,)), 'DU1', None,
        numbered_exit_assignment_id='A1'))
    assert result == [{'decision': {'status': 'fixture_declined'}, 'order_group': None}]
    records = value.journal.records(value.run_id)
    assert len(records) == 1
    record = records[0]
    assert record.payload['reason'] == expected
    assert record.payload['strategy_revision'] == number
    assert value.journal.numbered_session_exit_for_record(record.record_id) == intent
    assert value.approvals == [(intent, 'DU1', 'A1')]


@pytest.mark.parametrize('number', [15, 16])
def test_fourteen_reason_cannot_enter_successor_runtime_or_typed_memory(number):
    forged = replace(source(number), reason='strategy_fourteen_session_exit')
    value = runtime(number)
    with pytest.raises(ValueError, match='typed source authority'):
        asyncio.run(value._execute_intents(
            StrategyEvaluation(intents=(forged,)), 'DU1', None,
            numbered_exit_assignment_id='A1'))
    assert value.journal.pending_record_count == 0
    assert value.approvals == []
    with pytest.raises(ValueError, match='normalized scalar source'):
        value.journal.append_numbered_session_exit_intent(
            intent=forged, account_id='DU1', strategy_id='early-squeeze-strategy',
            strategy_revision=number)
    assert value.journal.pending_record_count == 0


@pytest.mark.parametrize('number', [True, 19.0, '19', 1, 34])
def test_reason_helper_retains_installed_typed_contract_guard(number):
    with pytest.raises(ValueError):
        numbered_session_exit_reason(number)


@pytest.mark.parametrize('relative,before,after', [
    ('trading_runtime/numbered_fixed_strategy.py',
     '16: "strategy_sixteen_session_exit"', '16: "strategy_fourteen_session_exit"'),
    ('trading_runtime/numbered_session_exit.py',
     'reason=numbered_session_exit_reason(strategy_number)',
     'reason="strategy_fourteen_session_exit"'),
    ('trading_runtime/runtime.py',
     'intent.reason != numbered_session_exit_reason(self.config.strategy_revision)',
     'intent.reason != "strategy_fourteen_session_exit"'),
    ('backend/backtest_journal_memory.py',
     'intent.reason != numbered_session_exit_reason(strategy_revision)',
     'intent.reason != "strategy_fourteen_session_exit"'),
])
def test_source_certificate_rejects_map_and_real_caller_mutations(tmp_path, relative, before, after):
    original = Path('src', relative).read_text(encoding='utf-8')
    assert original.count(before) == 1
    changed = tmp_path / Path(relative).name
    changed.write_text(original.replace(before, after), encoding='utf-8')
    assert len(certify_numbered_session_exit_reason_source()) == 64
    with pytest.raises(ValueError, match='reviewed authority changed'):
        certify_numbered_session_exit_reason_source(source_overrides={relative: changed})
