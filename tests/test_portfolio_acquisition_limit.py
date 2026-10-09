from dataclasses import replace
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pytest

from src.trading_runtime.portfolio import PortfolioReservation
from src.trading_runtime.portfolio_acquisition_limit import SessionAcquisitionLimit, acquisition_limit_reached

AT = datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc)


def scope(maximum=1):
    return SessionAcquisitionLimit('owned-session', 'DU1', AT, AT+timedelta(hours=5), maximum)


def reservation(**changes):
    row = PortfolioReservation('r1', 'd1', 'i1', 'cash', 'DU1', 'strategy', 'assignment',
        'TEST', 'enter_long', 10.0, 10.0, 5.0, 50.0, 1.0, AT)
    return replace(row, **changes)


def reached(rows, intent='i2', **changes):
    args = dict(run_id='owned-session', ticker='TEST', intent_id=intent, at=AT+timedelta(seconds=2))
    args.update(changes)
    return acquisition_limit_reached(scope(), rows, **args)


@pytest.mark.parametrize('status', ['reserved', 'filled', 'released'])
def test_accepted_acquisition_counts_after_fills_and_cash_release(status):
    assert reached([reservation(status=status, remaining_quantity=0.0)])
    assert not reached([reservation(status=status)], intent='i1')


def test_different_ticker_account_and_nonentry_do_not_consume_quota():
    assert not reached([reservation(ticker='OTHER'), reservation(account_id='DU2'),
        reservation(action='take_profit')])


def test_prior_session_and_budget_hold_do_not_consume_quota():
    assert not reached([reservation(created_at=AT-timedelta(seconds=1)),
        reservation(quantity=0.0, intent_id='i1:cash-hold', cash_tranche_key='budget')])


def test_parameterized_quota_counts_aggregate_acquisitions_once():
    policy=scope(2)
    args=dict(run_id=policy.run_id,ticker='TEST',intent_id='i3',at=AT+timedelta(seconds=2))
    first=reservation()
    second=reservation(reservation_id='r2',decision_id='d2',intent_id='i2')
    assert not acquisition_limit_reached(policy,[first],**args)
    assert acquisition_limit_reached(policy,[first,second],**args)


@pytest.mark.parametrize('changes', [dict(run_id='foreign'), dict(ticker='test'),
    dict(at=AT-timedelta(seconds=1)), dict(at=AT+timedelta(hours=5)),
    dict(at=AT.replace(tzinfo=None))])
def test_foreign_or_out_of_session_decisions_fail_closed(changes):
    with pytest.raises(ValueError): reached([],**changes)


@pytest.mark.parametrize('changes', [dict(created_at=AT+timedelta(seconds=3)),
    dict(created_at=AT.replace(tzinfo=None)), dict(quantity=float('nan')),
    dict(quantity=-1.0), dict(quantity=True), dict(intent_id='')])
def test_unproved_acceptance_fails_closed(changes):
    with pytest.raises(ValueError): reached([reservation(**changes)])


def test_duplicate_acceptance_is_not_silently_collapsed():
    with pytest.raises(ValueError,match='duplicated'): reached([reservation(),reservation()])


@pytest.mark.parametrize('maximum',[0,-1,True,1.5,33])
def test_invalid_quota_fails_closed(maximum):
    with pytest.raises(ValueError): scope(maximum)


def test_released_acceptance_survives_actual_typed_snapshot_and_cold_reader(monkeypatch):
    # Existing SQL transport fixture; real snapshot projection, hashes and
    # Portfolio recovery reader. This does not claim production DB coverage.
    from tests.test_arte_portfolio_recovery import _client, _state, AT as snapshot_at
    from src.trading_runtime.arte_portfolio_snapshot import publish_portfolio_snapshot
    from src.trading_runtime.arte_portfolio_recovery import recover_portfolio_engine_state
    client, profile = _client(monkeypatch)
    accepted = reservation(account_key='account-key',account_id='account-id',
        ticker='AAA',created_at=snapshot_at,status='released',remaining_quantity=0.0)
    state = _state()
    state['account_key'] = profile.account_key
    state['reservations'] = [asdict(accepted)]
    publish_portfolio_snapshot(client,run_id='live-run',account_id='account-id',
        state_revision=8,snapshot_at=snapshot_at,state=state)
    cold = recover_portfolio_engine_state(client,run_id='live-run',profiles=(profile,),
        state_revisions={'account-id':8},cutoff_at=snapshot_at)
    assert cold.reservations['r1'] == accepted
    policy = SessionAcquisitionLimit('live-run','account-id',snapshot_at,
        snapshot_at+timedelta(hours=1),1)
    args = dict(run_id='live-run',ticker='AAA',intent_id='new-entry',at=snapshot_at)
    assert acquisition_limit_reached(policy,[accepted],**args)
    assert acquisition_limit_reached(policy,cold.reservations.values(),**args)
    args['intent_id']='i1'
    assert not acquisition_limit_reached(policy,cold.reservations.values(),**args)
