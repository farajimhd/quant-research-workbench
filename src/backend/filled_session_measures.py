"""Read-only programme-clock measures of existing fills and OMS protection.

This is a financial projection, never an executor, market source or persistence
authority. Its caller must separately prove the committed run, source coverage
and immutable strategy before admitting an optimization comparison. Final fees
are attributed to their execution clocks. All exposure samples use completed
one-second clocks after liquidity processing, before the next fills.
"""
from datetime import datetime, timedelta, timezone
from math import isfinite
from decimal import Decimal

import polars as pl

from src.trading_runtime.filled_position_state import FilledPositionState
from src.trading_runtime.oms_assignment_observation import AssignmentOrderObservation

_KEY = ['account_id', 'strategy_id', 'assignment_id', 'listing_id']
_AT = 'boundary_at'
_STEP = timedelta(seconds=1)


def project_filled_session_measures(ledger, market, observations, *, start_at, stop_at, maximum_rows):
    """Add four measures to the same ledger's eight terminal financial facts.

    ``market`` is the complete prepared history projection, including held
    listings outside the ranked top10. ``observations`` contains (position-key,
    native OMS observation) pairs; every occupied clock needs processed order
    evidence. Unprotected quantity has its full marked capital at risk; each
    observed stop covers only its actual remaining quantity. No requested stop,
    latest quote, assumed fill or
    broker-observed asynchronous drawdown substitutes for that evidence.

    Float64 exposure arithmetic matches search's analytical measure precision;
    the eight ledger metrics retain their native Decimal/count representation.
    This function belongs on the existing owned worker, not a market callback.
    """
    if (type(ledger) is not FilledPositionState or type(market) is not pl.DataFrame
            or type(observations) is not tuple or type(maximum_rows) is not int or maximum_rows < 1
            or any(type(at) is not datetime or at.tzinfo is None or at.utcoffset() is None
                   or at.microsecond for at in (start_at, stop_at))
            or not _STEP <= stop_at - start_at <= timedelta(hours=16)
            or (stop_at - start_at) % _STEP):
        raise ValueError('Session measures require bounded exact one-second evidence')
    start_at, stop_at = (at.astimezone(timezone.utc) for at in (start_at, stop_at))
    metrics = dict(ledger.terminal_financial_metrics())
    clock_count = (stop_at - start_at) // _STEP
    positions = ledger.positions
    if clock_count * max(1, len(positions)) + len(ledger._fills) + len(observations) > maximum_rows:
        raise ValueError('Session measures exceed their explicit evidence row bound')
    if not positions:
        if observations:
            raise ValueError('Empty execution ledger has foreign OMS observations')
        return dict(metrics, drawdown=0., capital_dollar_seconds=0.,
                    stop_risk_dollar_seconds=0., inactivity_fraction=1.)
    columns = {_AT, 'available_at', 'listing_id', 'reference_close'}
    if not columns <= set(market.columns):
        raise ValueError('Session measures lack completed prepared market columns')
    if any(market.schema[name] != pl.Datetime('us', 'UTC') for name in (_AT, 'available_at')):
        raise ValueError('Session measure market clocks require UTC microsecond columns')
    listings = sorted({key[-1] for key in positions})
    marks = market.select(*sorted(columns)).filter(
        pl.col('listing_id').is_in(listings) & (pl.col(_AT) > start_at) & (pl.col(_AT) <= stop_at))
    if (marks.height != clock_count * len(listings)
            or marks.select(pl.struct(_AT, 'listing_id').is_duplicated().any()).item()
            or marks.filter(pl.col('available_at').is_null() | (pl.col('available_at') > pl.col(_AT))).height):
        raise ValueError('Session measures lack unique complete causal listing clocks')
    clocks = pl.DataFrame({_AT: pl.datetime_range(start_at + _STEP, stop_at, interval='1s', eager=True)})
    owners = pl.DataFrame([{**dict(zip(_KEY, key)), 'ticker': position.ticker}
                           for key, position in positions.items()])
    grid = clocks.join(owners, how='cross').join(marks, on=[_AT, 'listing_id'], how='left', validate='m:1')
    if grid['available_at'].null_count():
        raise ValueError('Session measures have missing or non-grid listing clocks')
    events = []
    for fact in ledger._fills.values():
        if not start_at <= fact.filled_at <= stop_at:
            raise ValueError('Session measures contain fills outside their complete window')
        sign = 1 if fact.action in {'enter_long', 'add_long'} else -1
        events.append({**dict(zip(_KEY, fact.key)), _AT: fact.filled_at,
            'quantity_delta': fact.quantity if sign == 1 else fact.quantity.copy_negate(),
            'cash_delta': float(fact.quantity) * float(fact.price) * sign,
            'fee_delta': float(ledger._fees[fact.execution_id].commission)})
    flow = pl.DataFrame(events, schema_overrides={_AT: pl.Datetime('us', 'UTC'),
        'quantity_delta': pl.Decimal(38, 10)}).group_by(*_KEY, _AT).agg(
            pl.col('quantity_delta').sum(), pl.col('cash_delta').sum(), pl.col('fee_delta').sum())
    flow = flow.sort([*_KEY, _AT]).with_columns(
        pl.col('quantity_delta').cum_sum().over(_KEY).alias('quantity'),
        pl.col('cash_delta').cum_sum().over(_KEY).alias('cash'),
        pl.col('fee_delta').cum_sum().over(_KEY).alias('fees'))
    grid = grid.sort(_AT).join_asof(flow.sort(_AT), on=_AT, by=_KEY,
        strategy='backward', check_sortedness=False).with_columns(
            pl.col('quantity').fill_null(Decimal(0)), pl.col('cash').fill_null(0.), pl.col('fees').fill_null(0.))
    if grid.filter((pl.col('quantity') < 0) | ((pl.col('quantity') > 0) & (
            pl.col('reference_close').is_null() | ~pl.col('reference_close').is_finite()
            | (pl.col('reference_close') <= 0)))).height:
        raise ValueError('Occupied session clock lacks an exact positive mark or quantity')
    protected, levels = [], []
    for key, observation in observations:
        if (type(key) is not tuple or key not in positions or type(observation) is not AssignmentOrderObservation
                or (observation.account_id, observation.assignment_id, observation.ticker)
                   != (key[0], key[2], positions[key].ticker)
                or type(observation.observed_at) is not datetime or observation.observed_at.tzinfo is None
                or observation.observed_at.utcoffset() is None
                or not start_at < observation.observed_at <= stop_at
                or observation.observed_at.microsecond):
            raise ValueError('Session measure OMS observation differs from its position clock')
        stops = [order for order in observation.working_orders
                 if order.role in {'protective_stop', 'trailing_stop'}]
        if (len({order.broker_order_id for order in stops}) != len(stops)
                or any(not isfinite(order.stop_price) or order.stop_price <= 0
                       or not isfinite(order.remaining_quantity) or order.remaining_quantity < 0 for order in stops)):
            raise ValueError('Session measure protection observation is invalid or duplicate')
        protected.append({**dict(zip(_KEY, key)), _AT: observation.observed_at,
            'coverage': sum(order.remaining_quantity for order in stops),
            'uniform_stop': next(iter({order.stop_price for order in stops}))
                if len({order.stop_price for order in stops}) == 1 else None,
            'unobserved': bool(observation.unobserved_order_ids), 'has_observation': True})
        levels.extend({**dict(zip(_KEY, key)), _AT: observation.observed_at,
            'stop': order.stop_price, 'stop_quantity': order.remaining_quantity} for order in stops)
        if len(levels) + len(observations) + grid.height + len(events) > maximum_rows:
            raise ValueError('Session measures exceed their explicit evidence row bound')
    if protected:
        protection = pl.DataFrame(protected, schema_overrides={_AT: pl.Datetime('us', 'UTC'), 'uniform_stop': pl.Float64})
        if protection.select(pl.struct(*_KEY, _AT).is_duplicated().any()).item():
            raise ValueError('Session measures repeat an OMS position clock')
        grid = grid.join(protection, on=[*_KEY, _AT], how='left', validate='1:1')
    else:
        grid = grid.with_columns(pl.lit(False).alias('has_observation'),
            pl.lit(0.).alias('coverage'), pl.lit(None, dtype=pl.Float64).alias('uniform_stop'),
            pl.lit(True).alias('unobserved'))
    rejected = grid.filter((pl.col('quantity') > 0) & (~pl.col('has_observation').fill_null(False)
            | pl.col('unobserved').fill_null(True)
            | ((pl.col('coverage').fill_null(0.) > pl.col('quantity').cast(pl.Float64) + 1e-6)
                & pl.col('uniform_stop').is_null())))
    if rejected.height:
        raise ValueError('Occupied session clock lacks complete processed OMS protection: '
            + str(rejected.select(_AT, 'assignment_id', 'quantity', 'coverage', 'unobserved').head(3).to_dicts()))
    if levels:
        stop_risk = pl.DataFrame(levels, schema_overrides={_AT: pl.Datetime('us', 'UTC')}).join(
            grid.select(*_KEY, _AT, 'reference_close'), on=[*_KEY, _AT], how='left', validate='m:1').group_by(
                *_KEY, _AT).agg((pl.col('stop_quantity') * (pl.col('reference_close')
                    - pl.col('stop')).clip(lower_bound=0.)).sum().alias('protected_risk'))
        grid = grid.join(stop_risk, on=[*_KEY, _AT], how='left', validate='1:1')
    else:
        grid = grid.with_columns(pl.lit(0.).alias('protected_risk'))
    q = pl.col('quantity').cast(pl.Float64)
    price = pl.when(q > 0).then(pl.col('reference_close')).otherwise(0.)
    # OMS may protect the full approved acquisition while only a partial fill
    # is held. Uniform stops cover at most the actual position, never the
    # requested quantity. Different overlapping prices remain ambiguous.
    covered_risk = pl.when(pl.col('uniform_stop').is_not_null()).then(
        pl.min_horizontal(q, pl.col('coverage').fill_null(0.)) *
        (price - pl.col('uniform_stop')).clip(lower_bound=0.)).otherwise(pl.col('protected_risk').fill_null(0.))
    samples = grid.group_by(_AT).agg(
        (q * price - pl.col('cash') - pl.col('fees')).sum().alias('equity'),
        (q * price).sum().alias('capital'),
        pl.when(q > 0).then(covered_risk +
            (q - pl.col('coverage').fill_null(0.)).clip(lower_bound=0.) * price).otherwise(0.).sum().alias('risk'),
        (q > 0).any().alias('occupied')).sort(_AT)
    result = samples.select(
        (pl.col('equity').cum_max().clip(lower_bound=0.) - pl.col('equity')).max().alias('drawdown'),
        pl.col('capital').sum().alias('capital_dollar_seconds'),
        pl.col('risk').sum().alias('stop_risk_dollar_seconds'),
        (~pl.col('occupied')).mean().alias('inactivity_fraction')).row(0, named=True)
    if any(not isfinite(value) for value in result.values()):
        raise ValueError('Session exposure measures exceeded finite analytical precision')
    return {**metrics, **result}


async def project_filled_session_measures_async(*args, **kwargs):
    from .qmd_columnar_history_transport import _owned_worker
    return await _owned_worker(project_filled_session_measures, *args, **kwargs)
