"""Prepared 35 entry graph uses the same native source as its exact 34 parent."""
from dataclasses import replace
from datetime import date
from uuid import UUID

import pytest

from test_backtest_strategy_initial_momentum import plans
from test_backtest_strategy_initial_momentum_growth import market_for
from test_backtest_strategy_first_price_source import Bars
from test_strategy_one_intent import _proposal
from src.backend.backtest_strategy_initial_ten_percent import compile_initial_ten_percent_plan
from src.backend.backtest_strategy_first_price_source import load_first_price_source
from src.backend.backtest_strategy_certified_price_break import (
    CertifiedPriceReadbackAuthority, bind_certified_price_break_proposal,
    certified_price_entry_intent, compile_certified_price_break_plan,
    project_certified_price_entry, restore_certified_price_proposal,
)
from src.trading_runtime.arte_rising_momentum_entry_v4 import (
    project_rising_momentum_entry, restore_rising_momentum, seal_rising_momentum_rows,
)
from src.trading_runtime.arte_initial_momentum_entry_v4 import (
    project_initial_momentum_entry, restore_initial_momentum, seal_initial_momentum_rows,
)
from src.trading_runtime.arte_first_price_entry_v4 import seal_first_price_rows
from src.trading_runtime.arte_strategy_one_entry_journal import project_strategy_one_entry_evidence


def native_graph(number=35):
    candidates, entry, momentum, _ = plans()
    relaxed = compile_initial_ten_percent_plan(candidates, entry, momentum)
    market = market_for(candidates)
    bars = replace(market.units[0], stage='bars',
        attempt_id=candidates.coverage[0].source_attempts[0])
    market = replace(market, units=(*market.units, bars))
    plan = compile_certified_price_break_plan(load_first_price_source(market, relaxed, client=Bars()))
    original = replace(_proposal(), strategy_number=18, boundary_ms=41000,
        momentum=momentum.lookup('AAA', 41000), initial_momentum=relaxed.selection_witness('AAA', 41000))
    proposal = bind_certified_price_break_proposal(plan, original, strategy_number=number)
    authority = CertifiedPriceReadbackAuthority('native-35', plan)
    intent = certified_price_entry_intent(plan, proposal, session_date=date(2026, 8, 18))
    scope = dict(run_id='native-35', batch_id=str(UUID(int=11)), event_month='2026-08-01')
    parent_id = str(UUID(int=12))
    kwargs = dict(scope, parent_record_id=parent_id)
    current = project_rising_momentum_entry(proposal, **kwargs)
    initial = project_initial_momentum_entry(proposal, proposal.initial_momentum, **kwargs)
    price = project_certified_price_entry(plan, proposal, **kwargs)
    child = project_strategy_one_entry_evidence(proposal, intent, session_date=date(2026, 8, 18),
        run_id=scope['run_id'], batch_id=scope['batch_id'], parent_record_id=parent_id,
        first_price_source=authority)
    parent = dict(scope, record_id=parent_id, action='enter_long', reason='strategy_one_entry',
        intent_id=intent.intent_id, ticker='AAA', account_id=proposal.account_id)
    event = dict(scope, record_id=parent_id, category='strategy', entity_type='strategy_intent',
        entity_id=intent.intent_id, account_id=proposal.account_id, event_time=intent.event_time.isoformat())
    return plan, proposal, authority, intent, child, current, initial, price, parent, event


def test_complete_prepared_entry_graph_preserves_parent_financial_contract():
    graph = native_graph()
    plan, proposal, authority, intent, child, current, initial, price, parent, event = graph
    original = replace(proposal, strategy_number=18, first_price=None, price_source_token=None,
        initial_momentum=plan.source.parent.selection_witness(proposal.ticker, proposal.boundary_ms))
    old_proposal = bind_certified_price_break_proposal(plan, original, strategy_number=34)
    old_intent = certified_price_entry_intent(plan, old_proposal, session_date=date(2026, 8, 18))
    assert replace(proposal, strategy_number=34) == old_proposal
    assert replace(intent, intent_id=old_intent.intent_id) == old_intent
    assert intent.intent_id != old_intent.intent_id
    assert len(seal_rising_momentum_rows(current, (child,), (parent,), (event,))) == 2
    assert len(seal_initial_momentum_rows(initial, (child,), (parent,), (event,), current)) == 2
    assert len(seal_first_price_rows(price.rows, (child,), (parent,), (event,), (price.authority,))) == 1
    restored = restore_rising_momentum(current, ticker=proposal.ticker,
        boundary_ms=proposal.boundary_ms, strategy_number=35)
    restored_initial = restore_initial_momentum(initial, ticker=proposal.ticker,
        boundary_ms=proposal.boundary_ms, episode_start_ms=proposal.episode_start_ms,
        current_momentum=restored, strategy_number=35)
    reference = replace(proposal, momentum=restored, initial_momentum=restored_initial,
        first_price=None, price_source_token=None)
    assert restore_certified_price_proposal(authority, reference, price.rows,
        parent_record_id=child['parent_record_id'], batch_id=child['batch_id']) == proposal


@pytest.mark.parametrize('family', ['current', 'initial', 'price'])
@pytest.mark.parametrize('change', ['missing', 'parent_identity', 'future_clock', 'source_policy'])
def test_each_native_companion_rejects_missing_or_crossed_authority(family, change):
    _, _, _, _, child, current, initial, price, parent, event = native_graph()
    rows = {'current': current, 'initial': initial, 'price': price.rows}[family]
    if change == 'missing': changed = ()
    else:
        field, value = {'parent_identity': ('parent_record_id', str(UUID(int=99))),
            'future_clock': ('boundary_ms', 42000), 'source_policy': ('strategy_number', 34)}[change]
        changed = tuple(dict(row, **{field: value}) for row in rows)
    with pytest.raises(ValueError):
        if family == 'current': seal_rising_momentum_rows(changed, (child,), (parent,), (event,))
        elif family == 'initial': seal_initial_momentum_rows(changed, (child,), (parent,), (event,), current)
        else: seal_first_price_rows(changed, (child,), (parent,), (event,), (price.authority,))


def test_installed35_preserves_parent_capabilities():
    native_graph()
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    contract = numbered_fixed_strategy(35)
    assert contract.strategy_number == 35 and contract.allows_session_exit
    assert not contract.allows_adds and not contract.allows_completed_30s_trailing
    assert not contract.allows_target_escalation and contract.caps_entry_at_reference_ask


@pytest.mark.parametrize('change', [None, 'missing_price', 'changed_entry_hash', 'foreign_attempt'])
def test_existing_cold_entry_reader_rebuilds_exact_prepared_graph(monkeypatch, change):
    import struct
    from src.trading_runtime import arte_strategy_one_entry_journal as reader
    from src.trading_runtime.arte_intent_projection import RecoveredIntent
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    from src.trading_runtime.arte_rising_momentum_entry_v4 import MOMENTUM, VALUES
    from src.trading_runtime.arte_initial_momentum_entry_v4 import INITIAL_MOMENTUM
    from src.trading_runtime.arte_first_price_entry_v4 import FIRST_PRICE
    _, proposal, authority, intent, child, current, initial, price, parent, event = native_graph()
    sealed_current = seal_rising_momentum_rows(current, (child,), (parent,), (event,))
    sealed_initial = seal_initial_momentum_rows(initial, (child,), (parent,), (event,), current)
    sealed_price = seal_first_price_rows(price.rows, (child,), (parent,), (event,), (price.authority,))
    def bits(rows):
        return tuple(dict(row, **{name+'_bits': None if row[name] is None else
            int.from_bytes(struct.pack('>d', row[name]), 'big') for name in VALUES}) for row in rows)
    tables = {reader.ENTRY_EVIDENCE.name: (reader.seal_strategy_one_entry_evidence(child),),
        MOMENTUM.name: bits(sealed_current), INITIAL_MOMENTUM.name: bits(sealed_initial),
        FIRST_PRICE.name: sealed_price}
    if change == 'missing_price': tables[FIRST_PRICE.name] = ()
    elif change == 'changed_entry_hash':
        tables[reader.ENTRY_EVIDENCE.name] = (dict(tables[reader.ENTRY_EVIDENCE.name][0], content_hash='f'*64),)
    elif change == 'foreign_attempt':
        tables[FIRST_PRICE.name] = tuple(dict(row, bars_attempt_id=str(UUID(int=99))) for row in sealed_price)
    calls = []
    def rows(client, sql):
        calls.append(sql)
        assert 'LIMIT' in sql and 'parent_record_id IN' in sql
        selected = [value for name, value in tables.items() if 'FROM arte.'+name+' ' in sql]
        assert len(selected) == 1
        return selected[0]
    prefix = V4CommittedPrefix('native-35', 1, child['batch_id'], 'cursor', 'running', (child['batch_id'],))
    recovered = RecoveredIntent(1, proposal.account_id, child['parent_record_id'], child['batch_id'], intent)
    monkeypatch.setattr(reader, 'load_committed_strategy_intent_page', lambda *a, **k: (recovered,))
    monkeypatch.setattr(reader, '_rows', rows)
    if change is None:
        page = reader.load_committed_strategy_one_entry_page(None, prefix, first_price_source=authority)
        assert len(page.entries) == 1 and page.exhausted
        assert page.entries[0].proposal == proposal and page.entries[0].intent == intent
        assert len(calls) == 4
    else:
        with pytest.raises((ValueError, RuntimeError)):
            reader.load_committed_strategy_one_entry_page(None, prefix, first_price_source=authority)
