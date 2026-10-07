"""Producer-index parity, ignored domains, causal output and linear traversal."""
from dataclasses import replace, asdict
from datetime import date

import polars as pl
import pytest

from src.backend import backtest_confirmed_original_risk_source as source
from src.backend.backtest_market_data import MarketDayUnit
from test_confirmed_original_risk_source import plan, frame


DAY = date(2026, 1, 1)


def metadata_plan(count):
    original = plan()
    tickers = tuple(f'TEST{i}' for i in range(count))
    units = tuple(replace(u, ticker=ticker) for ticker in tickers for u in original.units)
    return replace(original, tickers=tickers, units=units)


def old_expected_rows(p):
    """Unchanged baseline enumeration as differential metadata oracle."""
    rows = []
    for ticker in p.tickers:
        attempts = {}
        for stage in ('bars', 'technical', 'broker_100ms'):
            matches = [u for u in p.units if type(u) is MarketDayUnit
                       and u.ticker == ticker and u.stage == stage]
            if (len(matches) != 1 or matches[0].build_id != p.build_id
                    or matches[0].session_date != DAY.isoformat()):
                raise ValueError('Completed risk source lacks exact producer attempts')
            attempts[stage] = matches[0].attempt_id
        rows.append(dict(source_build_id=p.build_id, source_market_plan_token=p.token,
                         source_bars_attempt_id=attempts['bars'],
                         source_indicators_attempt_id=attempts['technical'],
                         session_date=DAY.isoformat(), ticker=ticker,
                         source_liquidity_attempt_id=attempts['broker_100ms']))
    return rows


def test_exact_expected_metadata_and_causal_outputs():
    p = metadata_plan(3)
    frames = [frame((100000, 90000, 95000)).with_columns(pl.lit(t).alias('ticker'))
              for t in ('TEST2', 'TEST0')]
    f = pl.concat(frames)
    expected = old_expected_rows(p)
    lookup = source.CompiledCompletedRiskLookup(f, plan=p, session_date=DAY)
    expected_by_ticker = {row['ticker']: row for row in expected}
    for ticker in ('TEST0', 'TEST2'):
        assert lookup._columns[ticker].equals(f.filter(pl.col('ticker') == ticker)
                                              .sort(['ticker', 'boundary_ms']))
        before, newest = lookup.pair_at(ticker, 100000)
        assert (before.boundary_ms, newest.boundary_ms) == (95000, 100000)
        assert before.ticker == newest.ticker == ticker
        assert {key: getattr(newest, key) for key in source.SOURCE_KEYS} == expected_by_ticker[ticker]
        assert lookup.pair_at(ticker, 99900) is None
        assert lookup.pair_at(ticker, 105000) is None
    assert lookup.pair_at('TEST1', 100000) is None


@pytest.mark.parametrize('stage', ['bars', 'technical', 'broker_100ms'])
@pytest.mark.parametrize('defect', ['missing', 'duplicate', 'build', 'date', 'subclass'])
def test_all_declared_producers_fail_closed_even_outside_frame(stage, defect):
    p = metadata_plan(2)
    selected = next(u for u in p.units if u.ticker == 'TEST1' and u.stage == stage)
    others = tuple(u for u in p.units if u is not selected)
    if defect == 'missing':
        units = others
    elif defect == 'duplicate':
        units = (*p.units, selected)
    else:
        changed = replace(selected, **({'build_id': 'foreign'} if defect == 'build'
                                       else {'session_date': '2026-01-02'} if defect == 'date'
                                       else {}))
        if defect == 'subclass':
            class Derived(MarketDayUnit):
                pass
            changed = Derived(**asdict(selected))
        units = (*others, changed)
    bad = replace(p, units=units)
    with pytest.raises(ValueError, match='exact producer attempts'):
        old_expected_rows(bad)
    with pytest.raises(ValueError, match='exact producer attempts'):
        source.CompiledCompletedRiskLookup(frame().with_columns(pl.lit('TEST0').alias('ticker')),
                                           plan=bad, session_date=DAY)


def test_ignored_stages_domains_and_nonexact_types_stay_ignored():
    p = plan()
    class Derived(MarketDayUnit):
        pass
    ignored = (replace(p.units[0], stage='unrelated', build_id='foreign'),
               replace(p.units[0], ticker='OUTSIDE', build_id='foreign'),
               Derived(**asdict(p.units[0])), object())
    extra = replace(p, units=(*p.units, *ignored))
    assert old_expected_rows(extra) == old_expected_rows(p)
    a = source.CompiledCompletedRiskLookup(frame(), plan=p, session_date=DAY)
    b = source.CompiledCompletedRiskLookup(frame(), plan=extra, session_date=DAY)
    assert a.pair_at('TEST', 100000) == b.pair_at('TEST', 100000)


@pytest.mark.parametrize('count', [1, 10, 100])
def test_producer_inventory_is_traversed_once(count):
    p = metadata_plan(count)
    class Counted:
        traversals = 0
        visits = 0
        def __iter__(self):
            self.traversals += 1
            for unit in p.units:
                self.visits += 1
                yield unit
    inventory = Counted()
    observed = replace(p, units=inventory)
    source.CompiledCompletedRiskLookup(frame().with_columns(pl.lit('TEST0').alias('ticker')),
                                       plan=observed, session_date=DAY)
    assert inventory.traversals == 1
    assert inventory.visits == len(p.units)
