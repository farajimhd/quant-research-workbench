"""Causal full-session purchase, aging and funding policy for momentum v26."""
from math import isfinite, floor

from .early_squeeze_fast import below
from .early_squeeze_price import eligible, midpoint


DEFAULTS = dict(maximum_spread_bps=250., minimum_session_shares=25_000.,
    minimum_session_dollars=100_000., minimum_trade_rate_10s=1.,
    minimum_trade_rate_60s=.5, maximum_source_age_seconds=2.,
    age_seconds=300., red_grace_seconds=60.,
    exit_commission_per_share=.005, exit_minimum_commission=1.)


def purchase_gate(observation, policy):
    facts, failed = {}, []
    for key, threshold in [('market.volume', 'minimum_session_shares'),
            ('market.session_dollar_volume', 'minimum_session_dollars'),
            ('market.trade_rate_10s', 'minimum_trade_rate_10s'),
            ('market.trade_rate_60s', 'minimum_trade_rate_60s')]:
        from .early_squeeze_breakout import stamp
        row = observation.source_values.get(key, {})
        value, at = row.get('value'), stamp(row.get('observed_at'))
        valid = (isinstance(value, (int, float)) and isfinite(value) and at is not None
            and 0 <= (observation.observed_at-at).total_seconds() <= policy['maximum_source_age_seconds'])
        facts[key] = value
        if not valid or value < policy[threshold]:
            failed.append(key)
    spread = ((observation.ask-observation.bid) / ((observation.ask+observation.bid)/2)*10_000
        if observation.bid > 0 and observation.ask >= observation.bid else None)
    facts['spread_bps'] = spread
    if spread is None or spread > policy['maximum_spread_bps']:
        failed.append('spread_bps')
    return dict(passed=not failed, facts=facts, failed=failed)


def net_position(executions, *, account_id, conid, run_id, first_fill_at, now,
                 quantity, bid, policy):
    """Use final causal execution costs plus configured estimated exit commission."""
    rows = [e for e in executions if e.account_id == account_id and e.instrument.conid == conid
        and e.run_id == run_id and first_fill_at <= e.source_event_time.timestamp() <= now]
    if not rows or quantity <= 0 or bid <= 0:
        return dict(status='unavailable', reason='position_execution_evidence_unavailable')
    if any(e.commission_status != 'final' or e.commission is None
           or not e.commission.is_finite() or e.commission_currency != e.instrument.currency for e in rows):
        return dict(status='unavailable', reason='final_entry_fees_unavailable')
    bought = sum(float(e.quantity) for e in rows if e.side.upper() == 'BUY')
    sold = sum(float(e.quantity) for e in rows if e.side.upper() == 'SELL')
    if abs(bought-sold-quantity) > 1e-6:
        return dict(status='unavailable', reason='position_execution_quantity_mismatch')
    net_cost = sum(float(e.quantity*e.price)*(1 if e.side.upper() == 'BUY' else -1)
        + float(e.commission) for e in rows)
    exit_fee = max(policy['exit_minimum_commission'], quantity*policy['exit_commission_per_share'])
    return dict(status='verified', net_pnl=quantity*bid-net_cost-exit_fee,
        break_even=(net_cost+exit_fee)/quantity, bid=bid, quantity=quantity,
        estimated_exit_fee=exit_fee, observed_at=now, authority='canonical_executions_and_executable_bid')


def aged_action(active, rows, *, now, bid, tick, net, policy, target_floor=None,
                 recover_with_bracket=False):
    first = active.get('first_fill_at')
    if first is None or now-first < policy['age_seconds']:
        return None
    age = now-first
    # The deadline is absolute, including sparse observations and missing fee evidence.
    green = net.get('status') == 'verified' and net['net_pnl'] > 0
    if active.get('age_mode') == 'red' and not recover_with_bracket or not green:
        active['age_mode'] = 'red'
        if green:
            return dict(action='exit', reason='aged_red_recovered_green')
        if age >= policy['age_seconds']+policy['red_grace_seconds']:
            return dict(action='exit', reason='aged_red_grace_expired')
        return dict(action='hold', reason='aged_red_waiting_for_green')
    active['age_mode'] = 'green'
    if active.get('age_target'):
        if bid >= active['age_target']:
            return dict(action='exit', reason='aged_green_resistance_target')
        return dict(action='protect', stop=active['age_stop'], target=active['age_target'])
    lower = [r for r in rows.values() if eligible(r) and midpoint(r) < bid
        and net['break_even'] < below(r['lower'], tick) < bid]
    upper = [r for r in rows.values() if eligible(r) and r['lower'] > (target_floor or bid)]
    if not lower or not upper:
        return dict(action='exit', reason='aged_green_no_valid_resistance_bracket')
    resistance = max(lower, key=midpoint)
    target = min(upper, key=lambda r: r['lower'])
    target_price = round(floor(target['lower']/tick+1e-9)*tick, 10)
    if target_price <= (target_floor or bid):
        return dict(action='exit', reason='aged_green_no_valid_resistance_bracket')
    active.update(age_stop=below(resistance['lower'], tick), age_target=target_price)
    return dict(action='protect', stop=active['age_stop'], target=active['age_target'])


def funding_rank(candidates, requested_gap):
    """Only smaller-gap positions can fund a request; green oldest first."""
    eligible_rows = [r for r in candidates if 0 < r['gap'] < requested_gap
        and (r['net_pnl'] > 0 or r['age'] >= 360)]
    return sorted(eligible_rows, key=lambda r: (r['net_pnl'] <= 0, -r['age'], -r['net_pnl'], r['ticker']))


def cash_shortfall(reasons):
    reasons = set(reasons)
    if any(r.startswith('limited_by_') and r != 'limited_by_available_funds' for r in reasons):
        return False
    return bool(reasons & {'invalid_quantity_or_reference_price',
        'limited_by_available_funds', 'quantity_below_minimum_increment'})
