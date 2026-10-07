"""Constructed producer certificates test components, not installed authority."""
from dataclasses import replace
from datetime import date, timedelta
from uuid import uuid4

import pytest

from src.backend.backtest_strategy_one_v7_interval_store import CertifiedV7IntervalPlan, CertifiedV7IntervalUnit
from src.trading_runtime.fixed_structural_lot_policy import FixedStructuralLotPolicy, parse_fixed_structural_lot_policy
from src.trading_runtime.fixed_structural_lot_entry import prepare_fixed_structural_lot_entry, verify_fixed_structural_lot_entry, fixed_structural_lot_intent
from src.trading_runtime.fixed_structural_lot_management import advance_fixed_structural_lot_protection, require_fixed_structural_lot_transition, require_fixed_structural_lot_command
from src.trading_runtime.strategy_one_v7_intervals import V7IntervalProjector, clock_hash, interval_hash
from src.trading_runtime.strategy_one_intent import strategy_one_entry_intent
from src.trading_runtime.strategy_one_stateful import StrategyOneEntryProposal
from src.trading_runtime.arte_intent_projection import project_strategy_intent, restore_strategy_intent
from src.trading_runtime.strategy_orders import IbkrStrategyOrderPlanner
from src.trading_runtime.domain import InstrumentContract
from src.trading_runtime.strategy_one_position import ResistanceBreak
from tests.test_strategy_one_position import opening, advancing, level


def fixture(count=7, future=False):
    day = date(2026, 8, 18)
    projector = V7IntervalProjector()
    levels = [dict(level(i, 10 + i * .1), confirmed_at_ms=1787040000000,
                   historical=True, book_version='causal-level-book-v7-mle-1') for i in range(1, count + 1)]
    projector.observe(boundary_ms=0, levels=levels, valid_completed_second=False)
    projector.observe(boundary_ms=30_000, levels=levels, valid_completed_second=True)
    if future:
        projector.observe(boundary_ms=31_000, levels=[dict(row, lower=row['lower']+20,
                          upper=row['upper']+20) for row in levels], valid_completed_second=True)
    seconds, rows = projector.finish()
    unit = CertifiedV7IntervalUnit('AAA', str(uuid4()), str(uuid4()), '1'*64, '2'*64,
        '3'*64, '4'*64, 'legacy-unfiltered', len(seconds), len(rows), clock_hash(seconds), interval_hash(rows))
    plan = CertifiedV7IntervalPlan(str(uuid4()), day.isoformat(), (unit,), (('AAA', seconds),), (('AAA', rows),), '5'*64)
    proposal = StrategyOneEntryProposal('assignment', 'account', 'AAA', 30_100, 30_000,
        10.01, 9.69, 10.3, '3', .5, 30_000, 'support')
    return day, proposal, plan


def compiled():
    day, proposal, plan = fixture()
    policy = FixedStructuralLotPolicy()
    entry = prepare_fixed_structural_lot_entry(proposal, session_date=day, policy=policy, intervals=plan, tick=.01)
    original = strategy_one_entry_intent(proposal, session_date=day)
    intent = fixed_structural_lot_intent(original, entry, intervals=plan, intent_id=str(uuid4()))
    return entry, original, intent, plan


def test_three_targets_keep_original_request_and_shared_planner_quantity():
    entry, original, intent, plan = compiled()
    assert [x.price for x in entry.targets] == [10.3, 10.4, 10.5]
    assert original.protection_profile.slices[0].slice_id == 'all'
    assert intent.capital_request == original.capital_request
    assert intent.execution_policy == original.execution_policy
    assert intent.reference_price == original.reference_price
    assert intent.invalidation_price == original.invalidation_price
    assert intent.metadata == original.metadata == {}
    assert intent.protection_profile.profile_id != original.protection_profile.profile_id
    requested = replace(intent, quantity=100)
    cold = restore_strategy_intent(project_strategy_intent(requested))
    assert cold == requested
    orders = IbkrStrategyOrderPlanner().plan(account_id='account', instrument=InstrumentContract('AAA', 1, 'AAA', 'STK', 'USD'), intent=cold, strategy_id='own-fixture', strategy_revision=1)
    assert len(orders.orders) == 9 and len(orders.broker_batches) == 3
    assert sum(order.quantity for order in orders.orders if order.side == 'BUY') == 100
    assert set(orders.order_slice_ids) == {'lot-1', 'lot-2', 'lot-3'}


@pytest.mark.parametrize('field,value', [('count', True), ('count', 3.0), ('count', 1), ('count', 33), ('allocation', 'increasing'), ('target_price_rule', 'lower_edge_minus_tick')])
def test_closed_declaration_rejects_alias_or_unsupported_variant(field, value):
    data = FixedStructuralLotPolicy().payload()
    data[field] = value
    with pytest.raises(ValueError):
        parse_fixed_structural_lot_policy(data)


def test_generic_counts_from_declared_policy_and_exact_keyset():
    for count in (2, 3, 5, 32):
        policy = FixedStructuralLotPolicy(count=count)
        assert len(policy.weights) == count
        assert parse_fixed_structural_lot_policy(policy.payload()) == policy
    for data in ({}, {**FixedStructuralLotPolicy().payload(), 'approve': True}):
        with pytest.raises(ValueError):
            parse_fixed_structural_lot_policy(data)


@pytest.mark.parametrize('change', ['clock_hash', 'clock_count', 'attempt', 'date', 'original', 'target_alias', 'token'])
def test_foreign_source_or_target_replay_fails(change):
    entry, _, _, plan = compiled()
    if change == 'clock_hash':
        plan = replace(plan, coverage=(replace(plan.coverage[0], clock_hash='f'*64),))
    elif change == 'clock_count':
        plan = replace(plan, coverage=(replace(plan.coverage[0], clock_count=True),))
    elif change == 'attempt':
        plan = replace(plan, coverage=(replace(plan.coverage[0], attempt_id=str(uuid4())),))
    elif change == 'date':
        plan = replace(plan, session_date='2026-08-19')
    elif change == 'original':
        entry = replace(entry, proposal=replace(entry.proposal, initial_target=10.31))
    elif change == 'target_alias':
        entry = replace(entry, targets=(replace(entry.targets[0], historical=1), *entry.targets[1:]))
    elif change == 'token':
        plan = replace(plan, token='f'*64)
    with pytest.raises(ValueError):
        verify_fixed_structural_lot_entry(entry, intervals=plan)


def test_missing_extensions_and_future_completed_clock_fail_closed():
    day, proposal, plan = fixture(4)
    with pytest.raises(ValueError, match='Missing distinct'):
        prepare_fixed_structural_lot_entry(proposal, session_date=day, policy=FixedStructuralLotPolicy(), intervals=plan, tick=.01)
    with pytest.raises(ValueError):
        prepare_fixed_structural_lot_entry(replace(proposal, boundary_ms=29_900), session_date=day, policy=FixedStructuralLotPolicy(), intervals=plan, tick=.01)


def test_selected_fixed_targets_preserve_earned_resistance_state_and_stop():
    opened = opening().state
    breaks = [ResistanceBreak(31_000, level(index, center)) for index, center in ((1,9.8),(2,9.9),(3,10.1))]
    inputs = dict(now_ms=31_000, bid=10., ask=10.01, tick=.01, low_boundary_ms=30_000,
        low_int=99_500, low_price_valid=True, low_extremes_valid=True, breaks=breaks,
        overhead_levels=[level(i, 11 + i*.1) for i in range(1,8)], price_bearing_bar=True,
        allows_completed_30s_trailing=False)
    selected = advance_fixed_structural_lot_protection(opened, policy=FixedStructuralLotPolicy(), **inputs)
    control = advancing(opened, breaks=breaks, overhead_levels=inputs['overhead_levels'],
                        allows_completed_30s_trailing=False, allows_target_escalation=False)
    assert selected == control and selected.target_amendment is None
    assert selected.state.target == opened.target
    assert selected.state.stop > opened.stop and len(selected.state.accepted_ids) == 3
    with pytest.raises(ValueError, match='cannot be overridden'):
        advance_fixed_structural_lot_protection(opened, policy=FixedStructuralLotPolicy(), allows_target_escalation=True, **inputs)
    advancing_target = advancing(opened, overhead_levels=inputs['overhead_levels'])
    with pytest.raises(ValueError, match='cannot be amended'):
        require_fixed_structural_lot_transition(opened, advancing_target, policy=FixedStructuralLotPolicy())


def test_actual_cold_target_command_rejected_and_aggregate_exit_terms_preserved():
    _, original, intent, _ = compiled()
    for action in ('replace_profit_target', 'exit_long'):
        changed = replace(intent, action=action, profit_target_price=11.)
        cold = restore_strategy_intent(project_strategy_intent(changed))
        with pytest.raises(ValueError, match='forbidden'):
            require_fixed_structural_lot_command(cold, policy=FixedStructuralLotPolicy())
    exit_intent = replace(intent, action='exit_long', profit_target_price=None, protection_profile=None)
    require_fixed_structural_lot_command(exit_intent, policy=FixedStructuralLotPolicy())
    assert exit_intent.capital_request == original.capital_request


def test_component_has_no_parent_identity_or_detached_clock_fallback():
    entry, original, _, plan = compiled()
    for changed, own_id in ((original, original.intent_id),
                            (replace(original, event_time=original.event_time + timedelta(milliseconds=100)), str(uuid4())),
                            (replace(original, ticker='OTHER'), str(uuid4()))):
        with pytest.raises(ValueError):
            fixed_structural_lot_intent(changed, entry, intervals=plan, intent_id=own_id)


def test_all_lots_must_have_quantity_without_recalculating_portfolio_size():
    _, _, intent, _ = compiled()
    with pytest.raises(ValueError):
        IbkrStrategyOrderPlanner().plan(account_id='account', instrument=InstrumentContract('AAA', 1, 'AAA', 'STK', 'USD'),
            intent=replace(intent, quantity=2), strategy_id='own-fixture', strategy_revision=1)


def test_generic_two_lot_plan_uses_declared_count_without_behavior_numbers():
    day, proposal, intervals = fixture()
    policy = FixedStructuralLotPolicy(count=2)
    entry = prepare_fixed_structural_lot_entry(proposal, session_date=day, policy=policy, intervals=intervals, tick=.01)
    assert [t.price for t in entry.targets] == [10.3,10.4]
    assert policy.weights == ((1,2),(1,2))


@pytest.mark.parametrize('change', ['capital', 'execution', 'profile', 'revision_alias', 'fraction_alias', 'outside_rth', 'account', 'assignment'])
def test_actual_parent_factory_entire_request_is_required(change):
    entry, original, _, plan = compiled()
    if change == 'capital':
        original = replace(original, capital_request=replace(original.capital_request, value=.9))
    elif change == 'execution':
        original = replace(original, execution_policy=replace(original.execution_policy, quote_source='ibkr'))
    elif change == 'profile':
        original = replace(original, protection_profile=replace(original.protection_profile, mandatory_catastrophic_backstop=False))
    elif change == 'revision_alias':
        original = replace(original, protection_profile=replace(original.protection_profile, revision=1.0))
    elif change == 'fraction_alias':
        original = replace(original, protection_profile=replace(original.protection_profile,
            slices=(replace(original.protection_profile.slices[0], quantity_fraction=1),)))
    elif change == 'outside_rth':
        original = replace(original, outside_rth=False)
    elif change == 'account':
        entry = replace(entry, proposal=replace(entry.proposal, account_id='other'))
    elif change == 'assignment':
        entry = replace(entry, proposal=replace(entry.proposal, assignment_id='other'))
    with pytest.raises(ValueError):
        fixed_structural_lot_intent(original, entry, intervals=plan, intent_id=str(uuid4()))


def test_future_geometry_cannot_change_frozen_targets_at_original_clock():
    policy = FixedStructuralLotPolicy()
    day, proposal, original_plan = fixture()
    _, _, extended_plan = fixture(future=True)
    original = prepare_fixed_structural_lot_entry(proposal, session_date=day, policy=policy, intervals=original_plan, tick=.01)
    extended = prepare_fixed_structural_lot_entry(proposal, session_date=day, policy=policy, intervals=extended_plan, tick=.01)
    assert original.targets == extended.targets


def test_duplicate_active_source_ids_and_source_scalar_aliases_rejected():
    day, proposal, plan = fixture()
    rows = plan.intervals[0][1]
    for changed in ((rows[0], replace(rows[1], level_id=rows[0].level_id), *rows[2:]),
                    (replace(rows[0], lower=10), *rows[1:])):
        unit = replace(plan.coverage[0], interval_hash=interval_hash(changed))
        mutated = replace(plan, intervals=(('AAA',changed),), coverage=(unit,))
        with pytest.raises((RuntimeError,ValueError)):
            prepare_fixed_structural_lot_entry(proposal, session_date=day, policy=FixedStructuralLotPolicy(), intervals=mutated, tick=.01)
