"""Analytical measures of native ledger facts; no database admission is claimed."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from dataclasses import replace
import asyncio

import polars as pl
import pytest

from src.backend.filled_session_measures import project_filled_session_measures, project_filled_session_measures_async
from src.trading_runtime.filled_position_state import FilledPositionState, FillFact, FeeFact
from src.trading_runtime.oms_assignment_observation import AssignmentOrderObservation, ObservedWorkingOrder

START = datetime(2026, 8, 1, 8, tzinfo=timezone.utc)
KEY = ('account', 'strategy', 'assignment', 'listing')


def inputs():
    ledger = FilledPositionState()
    for index, (action, quantity, price) in enumerate([
            ('enter_long', '10', '10'), ('add_long', '5', '12'),
            ('reduce_long', '5', '11'), ('exit', '10', '9')]):
        identity = str(index)
        ledger.apply_fill(FillFact(identity, identity, identity, *KEY, 'ABC', action,
            D(quantity), D(price), START + timedelta(seconds=index, milliseconds=500)))
        ledger.apply_fee(FeeFact(identity, D(1), 'USD'))
    clocks = [START + timedelta(seconds=index) for index in range(1, 5)]
    market = pl.DataFrame(dict(boundary_at=clocks, available_at=clocks,
        listing_id=['listing'] * 4, reference_close=[11., 10., 12., 9.]))
    observations = tuple((KEY, AssignmentOrderObservation('account', 'assignment', 'ABC', at,
        False, False, (), (ObservedWorkingOrder('stop', 'protective_stop', quantity, 0., 9., 'Submitted'),), 0.))
        for at, quantity in zip(clocks[:3], [10., 15., 10.]))
    return ledger, market, observations


def measure(ledger, market, observations, **kwargs):
    return project_filled_session_measures(ledger, market, observations,
        start_at=START, stop_at=START + timedelta(seconds=4), maximum_rows=100, **kwargs)


def test_exposure_uses_actual_add_reduce_exit_and_fee_cashflows_at_completed_clocks():
    ledger, market, observations = inputs()
    before = dict(ledger.positions)
    result = measure(ledger, market, observations)
    # At clocks 1..4 equity is [9, -12, 12, -19]; peak starts at zero.
    assert result == dict(net_pnl=D(-19), drawdown=31., capital_dollar_seconds=380.,
        stop_risk_dollar_seconds=65., inactivity_fraction=.25, closed_positions=1,
        winning_positions=0, worst_position_pnl=D(-19), filled_batches=1,
        add_count=1, reduce_count=1, terminal_valid=True)
    assert dict(ledger.positions) == before


def test_owned_worker_projection_retains_same_results():
    args = inputs()
    result = asyncio.run(project_filled_session_measures_async(*args, start_at=START,
        stop_at=START + timedelta(seconds=4), maximum_rows=100))
    assert result == measure(*args)


@pytest.mark.parametrize('failure', ['missing_mark', 'duplicate_mark', 'future_mark', 'null_mark',
    'missing_stop', 'unobserved_stop', 'overlapping_stops', 'duplicate_stop',
    'duplicate_observation', 'foreign_observation', 'non_grid_mark'])
def test_incomplete_or_conflicting_clock_evidence_is_never_a_complete_measure(failure):
    ledger, market, observations = inputs()
    if failure == 'missing_mark': market = market.slice(1)
    elif failure == 'duplicate_mark': market = pl.concat([market, market.slice(0, 1)])
    elif failure == 'future_mark': market = market.with_columns((pl.col('available_at') + pl.duration(seconds=1)))
    elif failure == 'null_mark': market = market.with_columns(pl.lit(None, dtype=pl.Float64).alias('reference_close'))
    elif failure == 'non_grid_mark': market = market.with_columns((pl.col('boundary_at') + pl.duration(milliseconds=1)))
    elif failure == 'missing_stop': observations = observations[1:]
    elif failure == 'duplicate_observation': observations += observations[:1]
    else:
        key, obs = observations[0]
        if failure == 'unobserved_stop': obs = replace(obs, unobserved_order_ids=('stop',))
        elif failure == 'overlapping_stops': obs = replace(obs, working_orders=(*obs.working_orders,
            replace(obs.working_orders[0], broker_order_id='another', stop_price=8.)))
        elif failure == 'duplicate_stop': obs = replace(obs, working_orders=obs.working_orders * 2)
        elif failure == 'foreign_observation': obs = replace(obs, assignment_id='foreign')
        observations = ((key, obs), *observations[1:])
    with pytest.raises(ValueError): measure(ledger, market, observations)


def test_known_unprotected_partial_and_multiple_stop_quantities_have_actual_risk():
    ledger, market, observations = inputs()
    key, observation = observations[0]
    empty = ((key, replace(observation, working_orders=())), *observations[1:])
    assert measure(ledger, market, empty)['stop_risk_dollar_seconds'] == 155.  # 110 + 15 + 30
    partial = ((key, replace(observation, working_orders=(replace(observation.working_orders[0],
        remaining_quantity=9.),))), *observations[1:])
    assert measure(ledger, market, partial)['stop_risk_dollar_seconds'] == 74.  # 18 + 11 + 15 + 30
    sliced = ((key, replace(observation, working_orders=(
        replace(observation.working_orders[0], remaining_quantity=5.),
        replace(observation.working_orders[0], broker_order_id='second', remaining_quantity=5., stop_price=8.)))),
        *observations[1:])
    assert measure(ledger, market, sliced)['stop_risk_dollar_seconds'] == 70.


def test_empty_session_still_has_bounded_complete_clock_count_and_no_invented_position():
    ledger = FilledPositionState()
    result = measure(ledger, pl.DataFrame(), ())
    assert result['inactivity_fraction'] == 1 and result['drawdown'] == result['capital_dollar_seconds'] == 0
    assert result['stop_risk_dollar_seconds'] == 0 and result['closed_positions'] == 0
    with pytest.raises(ValueError, match='row bound'):
        project_filled_session_measures(ledger, pl.DataFrame(), (), start_at=START,
            stop_at=START + timedelta(seconds=4), maximum_rows=3)


def unfilled_observation(key=KEY):
    return (key, AssignmentOrderObservation(key[0], key[2], 'ABC',
        START + timedelta(seconds=1), True, False, ('unacknowledged',),
        (ObservedWorkingOrder('entry', 'entry', 10., 9., 0., 'Submitted'),), 0.))


def test_zero_fill_session_retains_valid_requested_orders_without_inventing_exposure():
    ledger = FilledPositionState()
    observed = (unfilled_observation(),)
    result = measure(ledger, pl.DataFrame(), observed, assignment_tickers={KEY: 'ABC'})
    assert result == measure(ledger, pl.DataFrame(), ())
    assert not ledger.positions and not ledger._fills and observed[0][1].pending_acquisition
    with pytest.raises(ValueError, match='position clock'):
        measure(ledger, pl.DataFrame(), observed)


@pytest.mark.parametrize('failure', ('foreign_key', 'foreign_account', 'foreign_assignment',
    'foreign_ticker', 'origin_clock', 'future_clock', 'subsecond_clock', 'duplicate'))
def test_zero_fill_observations_still_require_assignment_and_unique_session_clock(failure):
    key, obs = unfilled_observation()
    if failure == 'foreign_key': key = (*key[:3], 'foreign-listing')
    elif failure == 'foreign_account': obs = replace(obs, account_id='foreign')
    elif failure == 'foreign_assignment': obs = replace(obs, assignment_id='foreign')
    elif failure == 'foreign_ticker': obs = replace(obs, ticker='foreign')
    elif failure == 'origin_clock': obs = replace(obs, observed_at=START)
    elif failure == 'future_clock': obs = replace(obs, observed_at=START + timedelta(seconds=5))
    elif failure == 'subsecond_clock': obs = replace(obs, observed_at=obs.observed_at + timedelta(microseconds=1))
    observed = ((key, obs),) * (2 if failure == 'duplicate' else 1)
    with pytest.raises(ValueError):
        measure(FilledPositionState(), pl.DataFrame(), observed, assignment_tickers={KEY: 'ABC'})


def test_mixed_filled_and_unfilled_assignments_keep_only_actual_exposure():
    ledger, market, observed = inputs()
    other_key = (*KEY[:2], 'unfilled', 'other-listing')
    result = measure(ledger, market, (*observed, unfilled_observation(other_key)),
        assignment_tickers={KEY: 'ABC', other_key: 'ABC'})
    assert result == measure(ledger, market, observed)
    assert other_key not in ledger.positions


@pytest.mark.parametrize('failure', ('duplicate_stop', 'invalid_stop', 'wrong_authority'))
def test_unfilled_observations_do_not_bypass_existing_protection_or_authority_checks(failure):
    key, obs = unfilled_observation()
    stop = ObservedWorkingOrder('stop', 'protective_stop', 10., 0., 9., 'Submitted')
    authority = {KEY: 'ABC'}
    if failure == 'duplicate_stop': obs = replace(obs, working_orders=(stop, stop))
    elif failure == 'invalid_stop': obs = replace(obs, working_orders=(replace(stop, stop_price=float('nan')),))
    elif failure == 'wrong_authority': authority = {KEY: ''}
    with pytest.raises(ValueError):
        measure(FilledPositionState(), pl.DataFrame(), ((key, obs),), assignment_tickers=authority)


def test_held_listing_is_required_even_when_outside_top10():
    ledger, market, observations = inputs()
    unrelated = market.with_columns(pl.lit('ranked-only-listing').alias('listing_id'))
    with pytest.raises(ValueError, match='complete causal listing clocks'):
        measure(ledger, unrelated, observations)
    assert measure(ledger, pl.concat([market, unrelated]), observations)['capital_dollar_seconds'] == 380.


def test_quantities_preserve_decimal_cancellation_and_price_is_not_rounded_to_money_scale():
    ledger = FilledPositionState()
    price = D('10.000000000000002')
    for index, action in enumerate(('enter_long', 'exit')):
        ledger.apply_fill(FillFact(str(index), str(index), str(index), *KEY, 'ABC', action,
            D('.0000000001'), price, START + timedelta(seconds=index, milliseconds=500)))
        ledger.apply_fee(FeeFact(str(index), D(1), 'USD'))
    _, market, observations = inputs()
    assert measure(ledger, market, observations)['net_pnl'] == -2
    assert ledger._fills['0'].price == price


def test_complete_16_hour_clock_has_no_truncation_or_inactive_denominator_drift():
    stop_at = START + timedelta(hours=16)
    ledger = FilledPositionState()
    for identity, action, price, at in [
            ('buy', 'enter_long', D(10), START + timedelta(milliseconds=500)),
            ('sell', 'exit', D(11), stop_at - timedelta(milliseconds=500))]:
        ledger.apply_fill(FillFact(identity, identity, identity, *KEY, 'ABC', action, D(100), price, at))
        ledger.apply_fee(FeeFact(identity, D(1), 'USD'))
    clocks = pl.datetime_range(START + timedelta(seconds=1), stop_at, interval='1s', eager=True)
    market = pl.DataFrame(dict(boundary_at=clocks, available_at=clocks,
        listing_id=['listing'] * len(clocks), reference_close=[10.] * len(clocks)))
    order = ObservedWorkingOrder('stop', 'protective_stop', 100., 0., 9., 'Submitted')
    observations = tuple((KEY, AssignmentOrderObservation('account', 'assignment', 'ABC', at,
        False, False, (), (order,), 0.)) for at in clocks[:-1])
    result = project_filled_session_measures(ledger, market, observations,
        start_at=START, stop_at=stop_at, maximum_rows=200000)
    assert len(clocks) == 57600
    assert result['capital_dollar_seconds'] == 57599 * 1000.
    assert result['stop_risk_dollar_seconds'] == 57599 * 100.
    assert result['inactivity_fraction'] == 1 / 57600
    assert result['drawdown'] == 1. and result['net_pnl'] == 98
