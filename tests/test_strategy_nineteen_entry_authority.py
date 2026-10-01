"""Strategy19 source-bound first growth, inherited current rule and cold seals."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5

import pytest

from tests.test_arte_initial_momentum_entry_v4 import unit, restore, register_staged_contract
from tests.test_strategy_initial_strong_momentum import witness
from src.trading_runtime.arte_initial_momentum_entry_v4 import (
    project_initial_momentum_entry, restore_initial_momentum, seal_initial_momentum_rows,
)
from src.trading_runtime.arte_rising_momentum_entry_v4 import project_rising_momentum_entry
from src.trading_runtime.strategy_one_intent import strategy_one_entry_intent
from src.backend.backtest_strategy_one_management import StrategyOneManagementState
from src.trading_runtime.strategy_one_management_snapshot import project_manager_snapshot


def momentum(boundary, histogram):
    source = witness(boundary)
    return replace(source, observations=(source.observations[0],
        replace(source.observations[1], current_line=float(histogram))))


def graph(*, number=19, first_histogram=1.6, boundary=40_100, first_boundary=30_100):
    proposal, selection, _, entries, intents, events, _ = unit()
    first = momentum(first_boundary, first_histogram)
    current = momentum(boundary, 1.2)
    episode = first_boundary // 1000 * 1000
    selection = replace(selection, initial=replace(selection.initial,
        episode_start_ms=episode, first_setup=first))
    proposal = replace(proposal, strategy_number=number, boundary_ms=boundary,
        episode_start_ms=episode, bos_break_boundary_ms=boundary // 1000 * 1000,
        momentum=current, initial_momentum=selection)
    entry = {**entries[0], 'strategy_number': number, 'boundary_ms': boundary,
             'episode_start_ms': episode}
    identity = (f'strategy-{number}:2026-08-18:{proposal.assignment_id}:'
                f'{proposal.account_id}:{proposal.ticker}:{boundary}:{episode}')
    intent = {**intents[0], 'intent_id': str(uuid5(NAMESPACE_URL, identity))}
    at = datetime(2026, 8, 18, 8, tzinfo=timezone.utc) + timedelta(milliseconds=boundary)
    event = {**events[0], 'event_time': at.isoformat(), 'entity_id': intent['intent_id']}
    kwargs = dict(run_id=entry['run_id'], batch_id=entry['batch_id'],
                  parent_record_id=entry['parent_record_id'], event_month='2026-08-01')
    return proposal, selection, entry, intent, event, kwargs


@pytest.mark.parametrize('number,first_histogram', [(18, 1.2), (19, 1.6)])
def test_source_projection_restoration_and_graph_seal_preserve_current_ten_pct(number, first_histogram):
    proposal, selection, entry, intent, event, kwargs = graph(
        number=number, first_histogram=first_histogram)
    rows = project_initial_momentum_entry(proposal, selection, **kwargs)
    current = project_rising_momentum_entry(proposal, **kwargs)
    assert {row['strategy_number'] for row in rows} == {number}
    assert restore(rows, proposal) == selection
    assert restore_initial_momentum(rows, ticker=proposal.ticker,
        boundary_ms=proposal.boundary_ms, episode_start_ms=proposal.episode_start_ms,
        current_momentum=proposal.momentum, strategy_number=number) == selection
    assert len(seal_initial_momentum_rows(rows, (entry,), (intent,), (event,), current)) == 2
    assert strategy_one_entry_intent(proposal, session_date=date(2026, 8, 18)).metadata == {}


@pytest.mark.parametrize('histogram', [1.2, 1.5])
def test_premarket_first_threshold_is_strict_across_factory_projection_and_snapshot(histogram):
    proposal, selection, _, _, _, kwargs = graph(first_histogram=histogram)
    with pytest.raises(ValueError, match='50pct'):
        project_initial_momentum_entry(proposal, selection, **kwargs)
    with pytest.raises(ValueError, match='50pct'):
        strategy_one_entry_intent(proposal, session_date=date(2026, 8, 18))
    key = (proposal.account_id, proposal.assignment_id, proposal.ticker)
    state = StrategyOneManagementState(proposal.boundary_ms, ((key, proposal),), (), ())
    with pytest.raises(ValueError, match='50pct'):
        project_manager_snapshot(run_id='nineteen', session_date=date(2026, 8, 18),
            checkpoint_sequence=1, state=state)


def test_afterhours_first_retains_ten_pct_and_exact_source_chronology():
    proposal, selection, entry, intent, event, kwargs = graph(first_histogram=1.2,
        boundary=43_240_100, first_boundary=43_230_100)
    rows = project_initial_momentum_entry(proposal, selection, **kwargs)
    current = project_rising_momentum_entry(proposal, **kwargs)
    assert restore(rows, proposal) == selection
    assert len(seal_initial_momentum_rows(rows, (entry,), (intent,), (event,), current)) == 2
    assert strategy_one_entry_intent(proposal, session_date=date(2026, 8, 18)).outside_rth


@pytest.mark.parametrize('family', ['initial', 'current', 'entry'])
def test_graph_seal_rejects_cross_number_retries(family):
    proposal, selection, entry, intent, event, kwargs = graph()
    rows = project_initial_momentum_entry(proposal, selection, **kwargs)
    current = project_rising_momentum_entry(proposal, **kwargs)
    if family == 'initial':
        rows = tuple({**row, 'strategy_number': 18} for row in rows)
    elif family == 'current':
        current = tuple({**row, 'strategy_number': 18} for row in current)
    else:
        entry = {**entry, 'strategy_number': 18}
    with pytest.raises(ValueError, match='parent/source scope'):
        seal_initial_momentum_rows(rows, (entry,), (intent,), (event,), current)


def test_explicit_restore_number_cannot_override_original_rows():
    proposal, selection, _, _, _, kwargs = graph()
    rows = project_initial_momentum_entry(proposal, selection, **kwargs)
    with pytest.raises(ValueError, match='identity'):
        restore_initial_momentum(rows, ticker=proposal.ticker, boundary_ms=proposal.boundary_ms,
            episode_start_ms=proposal.episode_start_ms, current_momentum=proposal.momentum,
            strategy_number=18)


def test_forged_weak_first_rows_fail_cold_restore_and_entire_graph():
    proposal, selection, entry, intent, event, kwargs = graph()
    rows = project_initial_momentum_entry(proposal, selection, **kwargs)
    rows = tuple({**row, 'current_line': 1.2} if row['resolution_ms'] == 10000 else row
                 for row in rows)
    with pytest.raises(ValueError, match='50pct'):
        restore(rows, proposal)
    with pytest.raises(ValueError, match='50pct'):
        seal_initial_momentum_rows(rows, (entry,), (intent,), (event,),
            project_rising_momentum_entry(proposal, **kwargs))


@pytest.mark.parametrize('compound', [False, True])
def test_committed_direct_and_compound_nineteen_exact_cold_source_roundtrip(compound):
    import json
    import re
    import struct
    from uuid import UUID
    from tests.test_arte_journal_writer import MemoryClient
    from tests.test_arte_journal_commit_v4 import attached_v4_client
    from src.trading_runtime.arte_initial_momentum_entry_v4 import INITIAL_MOMENTUM, VALUES
    from src.trading_runtime.arte_rising_momentum_entry_v4 import MOMENTUM
    from src.trading_runtime.arte_strategy_one_entry_journal import (
        project_strategy_one_entry_evidence, load_committed_strategy_one_entry_page,
        load_committed_strategy_one_source,
    )
    from src.trading_runtime.arte_intent_projection import strategy_intent_batch
    from src.trading_runtime.arte_journal_writer import V4StrategyOneEntryBatch
    from src.trading_runtime.arte_journal_commit_v4 import (
        publish_strategy_one_entry_batch_v4, load_verified_v4_prefix,
    )
    from src.trading_runtime.arte_journal_compound_v4 import coalesce_v4_units, publish_compound_v4

    class ExactBitClient(MemoryClient):
        def execute(self, sql):
            for contract in (MOMENTUM, INITIAL_MOMENTUM):
                if f'FROM arte.{contract.name} ' in sql and 'reinterpretAsUInt64' in sql:
                    raw = super().execute(re.sub(r'SELECT .*? FROM arte\.',
                        'SELECT ' + ','.join(name for name, _ in contract.columns) + ' FROM arte.',
                        sql, count=1))
                    rows = [json.loads(line) for line in raw.splitlines() if line]
                    for row in rows:
                        for name in VALUES:
                            value = row[name]
                            row[name + '_bits'] = None if value is None else int.from_bytes(
                                struct.pack('>d', value), 'big')
                    return '\n'.join(json.dumps(row) for row in rows)
            return super().execute(sql)

    proposal, selection, _, _, _, _ = graph()
    intent = strategy_one_entry_intent(proposal, session_date=date(2026, 8, 18))
    base = strategy_intent_batch(intent, run_id='nineteen-cold', run_month=date(2026, 8, 1),
        account_id=proposal.account_id, attempt_id=str(UUID(int=51)), batch_id=str(UUID(int=52)),
        prior_batch_id=str(UUID(int=0)), sequence=1, source_cursor='cursor',
        run_status='running', recorded_at=intent.event_time)
    kwargs = dict(run_id=base.run_id, batch_id=base.batch_id,
                  parent_record_id=base.intents[0]['record_id'])
    evidence = project_strategy_one_entry_evidence(proposal, intent,
        session_date=date(2026, 8, 18), **kwargs)
    momentum_rows = project_rising_momentum_entry(proposal, event_month='2026-08-01', **kwargs)
    initial_rows = project_initial_momentum_entry(proposal, selection,
        event_month='2026-08-01', **kwargs)
    batch = V4StrategyOneEntryBatch(base, (evidence,), momentum_evidence=momentum_rows,
                                   initial_momentum_evidence=initial_rows)
    client = attached_v4_client(ExactBitClient())
    if compound:
        second_proposal = replace(proposal, boundary_ms=40_200, momentum=momentum(40_200, 1.2))
        second_intent = strategy_one_entry_intent(second_proposal, session_date=date(2026, 8, 18))
        second_base = strategy_intent_batch(second_intent, run_id=base.run_id,
            run_month=date(2026, 8, 1), account_id=proposal.account_id,
            attempt_id=base.attempt_id, batch_id=str(UUID(int=53)),
            prior_batch_id=base.batch_id, sequence=2, source_cursor='second',
            run_status='running', recorded_at=second_intent.event_time)
        second_kwargs = dict(run_id=base.run_id, batch_id=second_base.batch_id,
                             parent_record_id=second_base.intents[0]['record_id'])
        second_evidence = project_strategy_one_entry_evidence(second_proposal, second_intent,
            session_date=date(2026, 8, 18), **second_kwargs)
        second_batch = V4StrategyOneEntryBatch(second_base, (second_evidence,),
            momentum_evidence=project_rising_momentum_entry(second_proposal,
                event_month='2026-08-01', **second_kwargs),
            initial_momentum_evidence=project_initial_momentum_entry(second_proposal, selection,
                event_month='2026-08-01', **second_kwargs))
        merged = coalesce_v4_units((batch, second_batch))
        publish_compound_v4(client, merged)
    else:
        publish_strategy_one_entry_batch_v4(client, base, entry_evidence=(evidence,),
            momentum_evidence=momentum_rows, initial_momentum_evidence=initial_rows)
    prefix = load_verified_v4_prefix(client, base.run_id)
    page = load_committed_strategy_one_entry_page(client, prefix)
    assert len(page.entries) == (2 if compound else 1)
    assert page.entries[0].proposal == proposal
    if compound:
        assert page.entries[1].proposal == second_proposal
        from src.backend.backtest_typed_publisher import _committed_intent_source
        expected_source = _committed_intent_source(merged.base, base.events[0]['record_id'])
    else:
        expected_source = base
    recovered, recovered_intent = load_committed_strategy_one_source(client, prefix, page.entries[0])
    assert recovered_intent == intent and recovered == expected_source
    forged = replace(page.entries[0], proposal=replace(proposal,
        initial_momentum=replace(selection, selection_token='f' * 64)))
    with pytest.raises(RuntimeError, match='committed first-setup selection'):
        load_committed_strategy_one_source(client, prefix, forged)


@pytest.mark.parametrize('current_histogram', [1.0, 1.1])
def test_nineteen_current_requires_inherited_strict_ten_pct(current_histogram):
    proposal, selection, _, _, _, kwargs = graph()
    proposal = replace(proposal, momentum=momentum(proposal.boundary_ms, current_histogram))
    with pytest.raises(ValueError):
        project_rising_momentum_entry(proposal, **kwargs)
    with pytest.raises(ValueError):
        project_initial_momentum_entry(proposal, selection, **kwargs)
    with pytest.raises(ValueError):
        strategy_one_entry_intent(proposal, session_date=date(2026, 8, 18))


def test_direct_projection_cannot_replace_original_selection_tokens():
    proposal, selection, _, _, _, kwargs = graph()
    with pytest.raises(ValueError, match='original selection tokens'):
        project_initial_momentum_entry(proposal,
            replace(selection, selection_token='f' * 64), **kwargs)


def test_runtime_rejects_weak_premarket_first_before_portfolio_or_journal():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from src.trading_runtime.runtime import TradingRuntime, RunMode
    from src.backend.backtest_journal_memory import BacktestMemoryJournal
    proposal, _, _, _, _, _ = graph(first_histogram=1.2)
    runtime = object.__new__(TradingRuntime)
    runtime.config = SimpleNamespace(mode=RunMode.BACKTEST,
        strategy_id="early-squeeze-strategy", strategy_revision=19,
        account_ids=("DU1",), anchor_date=date(2026, 8, 18))
    runtime.run_id = "nineteen-runtime"
    runtime.journal = BacktestMemoryJournal(run_id=runtime.run_id)
    runtime.portfolio = SimpleNamespace(approve=AsyncMock())
    runtime._execute_intents = AsyncMock()
    with pytest.raises(ValueError, match='50pct'):
        asyncio.run(runtime.submit_strategy_one_proposal(proposal))
    runtime.portfolio.approve.assert_not_awaited()
    runtime._execute_intents.assert_not_awaited()


def test_nineteen_failure_inherits_inclusive_first_minute_bound():
    from src.trading_runtime.arte_followthrough_failure_v4 import validate_numbered_failure
    from tests.test_arte_followthrough_failure_v4 import fixture
    source = replace(fixture()[0], first_held_boundary_ms=30_000)
    assert validate_numbered_failure(replace(source, boundary_ms=90_000), 19) is None
    with pytest.raises(ValueError, match='first-minute'):
        validate_numbered_failure(replace(source, boundary_ms=95_000), 19)


@pytest.mark.parametrize('relative,before,after', [
    ('trading_runtime/strategy_initial_momentum_growth.py',
     'FIRST_SETUP_GROWTH_FRACTION = 0.50', 'FIRST_SETUP_GROWTH_FRACTION = 0.10'),
    ('trading_runtime/strategy_initial_momentum_growth.py',
     'boundaries_ms < PREMARKET_END_MS', 'boundaries_ms <= 57_600_000'),
    ('backend/backtest_strategy_initial_momentum_growth.py',
     'first = initial.first_indices', 'first = np.arange(len(initial.first_indices))'),
])
def test_reviewed_source_rejects_weakened_or_reanchored_growth(tmp_path, relative, before, after):
    from pathlib import Path
    from src.backend.backtest_fixed_v4_certification import certify_rising_momentum_entry_source
    source = (Path(__file__).parents[1] / 'src' / relative).read_text(encoding='utf-8')
    assert source.count(before) == 1
    altered = tmp_path / 'mutated.py'
    altered.write_text(source.replace(before, after), encoding='utf-8')
    with pytest.raises(ValueError, match='reviewed source authority changed'):
        certify_rising_momentum_entry_source(source_overrides={relative: altered})


@pytest.mark.parametrize('first_histogram,allowed', [(1.2, False), (1.6, True)])
def test_cold_manager_restores_only_qualified_nineteen_first_setup(first_histogram, allowed):
    from tests.test_strategy_thirteen_manager_sources import manager
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    proposal, _, _, _, _, _ = graph(first_histogram=first_histogram)
    key = (proposal.account_id, proposal.assignment_id, proposal.ticker)
    state = StrategyOneManagementState(proposal.boundary_ms, ((key, proposal),), (), ())
    recovered = manager()
    recovered.contract = numbered_fixed_strategy(19)
    if allowed:
        recovered.restore_state(state)
        assert recovered.capture_state(boundary_ms=proposal.boundary_ms).submitted == state.submitted
    else:
        with pytest.raises(ValueError, match='50pct'):
            recovered.restore_state(state)
