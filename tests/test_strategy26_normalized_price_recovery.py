"""Staged26 current/first/price witnesses share one independently read source."""
from dataclasses import replace
from datetime import date
from uuid import UUID

import pytest

from test_backtest_strategy_ten_percent_financial_binding import source
from src.backend.backtest_strategy_certified_price_break import (
    bind_certified_price_break_proposal, certified_price_entry_intent,
    project_certified_price_entry, restore_certified_price_proposal,
    CertifiedPriceReadbackAuthority,
)
from src.trading_runtime.arte_rising_momentum_entry_v4 import (
    project_rising_momentum_entry, seal_rising_momentum_rows, restore_rising_momentum,
)
from src.trading_runtime.arte_initial_momentum_entry_v4 import (
    project_initial_momentum_entry, seal_initial_momentum_rows, restore_initial_momentum,
)
from src.trading_runtime.arte_first_price_entry_v4 import seal_first_price_rows


def graph(source_values=None, *, strategy_number=26):
    _, _, plan, original = source() if source_values is None else source_values
    proposal = bind_certified_price_break_proposal(plan, original, strategy_number=strategy_number)
    scope = dict(run_id='strategy26-staged-graph', batch_id=str(UUID(int=11)), event_month='2026-08-01')
    parent_id = str(UUID(int=12))
    kwargs = dict(scope, parent_record_id=parent_id)
    current = project_rising_momentum_entry(proposal, **kwargs)
    initial = project_initial_momentum_entry(proposal, proposal.initial_momentum, **kwargs)
    price = project_certified_price_entry(plan, proposal, **kwargs)
    intent_value = certified_price_entry_intent(plan, proposal, session_date=date(2026, 8, 18))
    entry = dict(scope, parent_record_id=parent_id, strategy_number=strategy_number,
        assignment_id=proposal.assignment_id, boundary_ms=proposal.boundary_ms,
        episode_start_ms=proposal.episode_start_ms)
    intent = dict(scope, record_id=parent_id, action='enter_long', reason='strategy_one_entry',
        intent_id=intent_value.intent_id, ticker=proposal.ticker, account_id=proposal.account_id)
    event = dict(scope, record_id=parent_id, category='strategy', entity_type='strategy_intent',
        entity_id=intent_value.intent_id, account_id=proposal.account_id,
        event_time=intent_value.event_time.isoformat())
    return plan, proposal, (entry,), (intent,), (event,), current, initial, price


@pytest.mark.parametrize("strategy_number", [26, 27])
def test_all_three_witness_families_seal_and_restore_native_26_proposal_and_intent(strategy_number):
    plan, proposal, entries, intents, events, current, initial, price = graph(strategy_number=strategy_number)
    sealed_current = seal_rising_momentum_rows(current, entries, intents, events)
    sealed_initial = seal_initial_momentum_rows(initial, entries, intents, events, sealed_current)
    sealed_price = seal_first_price_rows(price.rows, entries, intents, events, (price.authority,))
    restored_current = restore_rising_momentum(sealed_current, ticker=proposal.ticker,
        boundary_ms=proposal.boundary_ms, strategy_number=strategy_number)
    selection = restore_initial_momentum(sealed_initial, ticker=proposal.ticker,
        boundary_ms=proposal.boundary_ms, episode_start_ms=proposal.episode_start_ms,
        current_momentum=restored_current, strategy_number=strategy_number)
    assert selection == proposal.initial_momentum and restored_current == proposal.momentum
    readback = CertifiedPriceReadbackAuthority(entries[0]['run_id'], plan)
    authority = readback.resolve(entries[0]['run_id'], entries, intents)
    assert authority == (price.authority,) and authority[0].strategy_number == strategy_number
    reference = replace(proposal, first_price=None, price_source_token=None,
                        momentum=restored_current, initial_momentum=selection)
    restored = restore_certified_price_proposal(readback, reference, sealed_price,
        parent_record_id=entries[0]['parent_record_id'], batch_id=entries[0]['batch_id'])
    assert restored == proposal
    assert certified_price_entry_intent(plan, restored, session_date=date(2026, 8, 18)).intent_id == intents[0]['intent_id']


@pytest.mark.parametrize('field,value', [('current_close_int', 102), ('price_source_token', 'f'*64),
                                        ('first_setup_boundary_ms', 32_000), ('strategy_number', 25)])
def test_changed_price_values_or_number_cannot_restore_or_seal(field, value):
    _, _, entries, intents, events, _, _, price = graph()
    changed = tuple(dict(row, **{field: value}) for row in price.rows)
    with pytest.raises(ValueError):
        seal_first_price_rows(changed, entries, intents, events, (price.authority,))


def test_native_26_authority_cannot_be_substituted_for_native_27():
    _, _, entries, intents, events, _, _, price = graph(strategy_number=27)
    wrong_authority = replace(price.authority, strategy_number=26)
    with pytest.raises(ValueError, match="source authority differs"):
        seal_first_price_rows(price.rows, entries, intents, events, (wrong_authority,))


def test_legacy_first_policy_cannot_authenticate_relaxed_26_source():
    _, _, _, _, _, _, _, price = graph()
    with pytest.raises(ValueError, match='policy-matched'):
        replace(price.authority, strategy_number=25)


def test_source_policy_identity_is_required_even_when_both_growth_rules_pass():
    from test_backtest_strategy_first_price_source import authority as old_authority, Bars
    from src.backend.backtest_strategy_initial_ten_percent import compile_initial_ten_percent_plan
    from src.backend.backtest_strategy_first_price_source import load_first_price_source
    from src.backend.backtest_strategy_certified_price_break import compile_certified_price_break_plan
    market, old_parent = old_authority()
    parent = compile_initial_ten_percent_plan(old_parent.candidates, old_parent.entry, old_parent.momentum)
    plan = compile_certified_price_break_plan(load_first_price_source(market, parent, client=Bars()))
    _, _, _, original = source()
    original = replace(original, momentum=parent.momentum.lookup('AAA', 41_000),
                       initial_momentum=parent.selection_witness('AAA', 41_000))
    _, _, entries, intents, events, _, _, price = graph((market, parent, plan, original))
    # This authority passes the older scalar growth check; its policy identity
    # still cannot authenticate a26 entry even when numeric observations overlap.
    legacy_authority = replace(price.authority, strategy_number=25)
    with pytest.raises(ValueError, match='authority differs from entry policy'):
        seal_first_price_rows(price.rows, entries, intents, events, (legacy_authority,))
    old_plan = compile_certified_price_break_plan(load_first_price_source(market, old_parent, client=Bars()))
    with pytest.raises(ValueError, match='source policy'):
        CertifiedPriceReadbackAuthority(entries[0]['run_id'], old_plan).resolve(
            entries[0]['run_id'], entries, intents)


@pytest.mark.parametrize('family', ['current', 'initial', 'price'])
def test_missing_required_family_is_rejected(family):
    _, _, entries, intents, events, current, initial, price = graph()
    with pytest.raises(ValueError):
        if family == 'current':
            seal_rising_momentum_rows((), entries, intents, events)
        elif family == 'initial':
            seal_initial_momentum_rows((), entries, intents, events, current)
        else:
            seal_first_price_rows((), entries, intents, events, (price.authority,))
