"""Bounded completed-bar observations, not an issued native/source capability.

Run/interval pins require independent native binding. Counts come from bars,
not execution quotes. Close and extreme validity remain distinct; unavailable
highs cannot arm profit, while valid closes remain rejection observations.
"""
from bisect import bisect_left
from datetime import date
from types import MappingProxyType
from uuid import UUID
import re

import polars as pl

from .backtest_market_data import (CertifiedMarketDayPlan, MarketDayUnit,
    SESSION_OPEN_OFFSET_MS, _literal, assert_select_only)
from src.trading_runtime.profit_armed_structural_rejection import (
    StructuralRejectionPolicy, RejectionSource, HeldBar)

SESSION_MS = 57_600_000
SCHEMA = dict(source_build_id=pl.String, source_market_plan_token=pl.String,
    source_bars_attempt_id=pl.String, source_indicators_attempt_id=pl.String,
    source_liquidity_attempt_id=pl.String, session_date=pl.String, ticker=pl.String,
    resolution_ms=pl.UInt32, boundary_ms=pl.UInt64, close_int=pl.UInt64,
    high_int=pl.UInt64, trade_count=pl.UInt64, price_valid=pl.Boolean,
    extremes_valid=pl.Boolean, macd_line=pl.Float64, macd_signal=pl.Float64)
KEYS = tuple(SCHEMA)[:7]


def _bindings(plan, session_date, tickers, policy, run_id, interval_plan_token,
              through_boundary_ms, max_rows):
    if (type(plan) is not CertifiedMarketDayPlan or type(session_date) is not date
            or plan.sessions != (session_date.isoformat(),)
            or type(policy) is not StructuralRejectionPolicy
            or type(tickers) is not tuple or len(set(tickers)) != len(tickers)
            or any(type(t) is not str or not t or t not in plan.tickers for t in tickers)
            or type(through_boundary_ms) is not int
            or not 0 <= through_boundary_ms <= SESSION_MS or through_boundary_ms % 100
            or type(max_rows) is not int or not 1 <= max_rows <= 20_000_000):
        raise ValueError('Exact bounded source/session/scope required')
    policy.__post_init__()
    if (type(run_id) is not str or str(UUID(run_id)) != run_id
            or type(interval_plan_token) is not str
            or not re.fullmatch('[0-9a-f]{64}', interval_plan_token)
            or type(plan.build_id) is not str or not plan.build_id.strip()
            or type(plan.token) is not str or not re.fullmatch('[0-9a-f]{64}', plan.token)):
        raise ValueError('Exact run/interval/market identity required')
    if (policy.bar_resolution_ms not in plan.required_resolutions_ms
            or SESSION_OPEN_OFFSET_MS % policy.bar_resolution_ms):
        raise ValueError('Required producer resolution is unavailable/alignment unsupported')
    # Index once, not once per row/proposal. Validate requested producers even
    # when their entire observation inventory is empty.
    units = {}
    for unit in plan.units:
        if type(unit) is not MarketDayUnit:
            raise ValueError('Exact producer unit required')
        if unit.ticker in tickers and unit.stage in ('bars', 'technical', 'broker_100ms'):
            units.setdefault((unit.ticker, unit.stage), []).append(unit)
    sources = {}
    for ticker in tickers:
        attempts = {}
        for stage in ('bars', 'technical', 'broker_100ms'):
            matches = units.get((ticker, stage), ())
            if (len(matches) != 1 or matches[0].build_id != plan.build_id
                    or matches[0].session_date != session_date.isoformat()):
                raise ValueError('Exact producer attempts required')
            attempts[stage] = matches[0].attempt_id
        sources[ticker] = RejectionSource(run_id, ticker, session_date.isoformat(),
            plan.build_id, plan.token, attempts['bars'], attempts['technical'],
            attempts['broker_100ms'], interval_plan_token)
    return sources


class CompletedStructuralRejectionLookup:
    """Exact-clock causal lookup; sparse slots never become synthetic bars."""
    def __init__(self, frame, *, plan, session_date, tickers, policy, run_id,
                 interval_plan_token, through_boundary_ms, max_rows=2_000_000):
        sources = _bindings(plan, session_date, tickers, policy, run_id,
            interval_plan_token, through_boundary_ms, max_rows)
        if type(frame) is not pl.DataFrame or frame.schema != SCHEMA:
            raise ValueError('Exact ordered typed completed-bar shape required')
        if frame.height > max_rows:
            raise ValueError('Completed-bar row bound exceeded')
        required = [k for k in SCHEMA if k not in ('macd_line', 'macd_signal')]
        if frame.select(pl.any_horizontal(pl.col(required).is_null()).any()).item():
            raise ValueError('Null authoritative completed-bar fields')
        expected = pl.DataFrame([dict(source_build_id=s.market_build_id,
            source_market_plan_token=s.market_plan_token,
            source_bars_attempt_id=s.bars_attempt_id,
            source_indicators_attempt_id=s.indicators_attempt_id,
            source_liquidity_attempt_id=s.liquidity_attempt_id,
            session_date=s.session_date, ticker=s.ticker) for s in sources.values()],
            schema={k: SCHEMA[k] for k in KEYS})
        if frame.select(KEYS).unique().join(expected, on=list(KEYS), how='anti').height:
            raise ValueError('Foreign certified source identity')
        res = policy.bar_resolution_ms
        if frame.filter((pl.col('resolution_ms') != res)
                | (pl.col('boundary_ms') == 0) | (pl.col('boundary_ms') > through_boundary_ms)
                | (pl.col('boundary_ms') % res != 0)
                | (pl.col('price_valid') & (pl.col('close_int') == 0))
                | (pl.col('extremes_valid') & ((pl.col('high_int') == 0)
                    | (pl.col('price_valid') & (pl.col('close_int') > pl.col('high_int')))))).height:
            raise ValueError('Malformed/future completed-bar observation')
        if frame.select(pl.struct(['ticker', 'boundary_ms']).is_duplicated().any()).item():
            raise ValueError('Duplicate completed-bar identity')
        if not frame.equals(frame.sort(['ticker', 'boundary_ms'])):
            raise ValueError('Completed-bar ordering mismatch')
        # Columnar grouping preserves each source inventory, including invalid
        # prices/missing momentum. Materialize scalar HeldBars only at lookup.
        self._columns = MappingProxyType({k[0]: v for k, v in
            frame.partition_by('ticker', as_dict=True, maintain_order=True).items()})
        self._clocks = MappingProxyType({t: tuple(f['boundary_ms']) for t, f in self._columns.items()})
        self.sources = MappingProxyType(sources)
        self.policy, self.through_boundary_ms = policy, through_boundary_ms
        self.row_count = frame.height
        self.unavailable_extremes_count = frame.filter(~pl.col('extremes_valid')).height

    def bar_at(self, ticker, boundary_ms):
        if (ticker not in self.sources or type(boundary_ms) is not int
                or not 0 <= boundary_ms <= self.through_boundary_ms
                or boundary_ms % self.policy.decision_resolution_ms):
            raise ValueError('Exact causal lookup scope/clock required')
        clocks = self._clocks.get(ticker, ())
        index = bisect_left(clocks, boundary_ms)
        if index == len(clocks) or clocks[index] != boundary_ms:
            return None
        row = self._columns[ticker].row(index, named=True)
        return HeldBar(self.sources[ticker], boundary_ms, row['close_int'],
            row['high_int'], row['trade_count'], row['price_valid'],
            row['macd_line'], row['macd_signal'], self.policy.bar_resolution_ms,
            extremes_valid=row['extremes_valid'])

    def activity_at(self, ticker, boundary_ms):
        """Oldest-first completed window; None records a missing source slot."""
        self.bar_at(ticker, boundary_ms)  # validate clock/scope even empty window
        boundary_ms = boundary_ms // self.policy.bar_resolution_ms * self.policy.bar_resolution_ms
        return tuple(None if clock <= 0 else self.bar_at(ticker, clock)
            for clock in (boundary_ms - i * self.policy.bar_resolution_ms
                          for i in reversed(range(self.policy.activity_count))))

    def completed_at(self, ticker, decision_boundary_ms):
        """Latest scheduled completed slot, never fill a missing slot from older data."""
        self.bar_at(ticker, decision_boundary_ms)  # validate decision scope
        clock = decision_boundary_ms // self.policy.bar_resolution_ms * self.policy.bar_resolution_ms
        return self.bar_at(ticker, clock)


def load_completed_structural_rejection_lookup(client, *, plan, session_date,
        tickers, policy, run_id, interval_plan_token, through_boundary_ms,
        max_rows=2_000_000):
    kwargs = dict(plan=plan, session_date=session_date, tickers=tickers, policy=policy,
        run_id=run_id, interval_plan_token=interval_plan_token,
        through_boundary_ms=through_boundary_ms, max_rows=max_rows)
    sources = _bindings(**kwargs)
    if not sources:
        return CompletedStructuralRejectionLookup(pl.DataFrame(schema=SCHEMA), **kwargs)
    def attempts(field):
        return ','.join(f'({_literal(t)},toUUID({_literal(getattr(s, field))}))'
                        for t, s in sources.items())
    def pin(field):
        return 'CASE ' + ' '.join(f'WHEN b.ticker={_literal(t)} THEN {_literal(getattr(s, field))}'
            for t, s in sources.items()) + " ELSE '' END"
    res = policy.bar_resolution_ms
    sql = f'''SELECT b.build_id AS source_build_id,
        {_literal(plan.token)} AS source_market_plan_token,
        toString(b.attempt_id) AS source_bars_attempt_id,
        {pin('indicators_attempt_id')} AS source_indicators_attempt_id,
        {pin('liquidity_attempt_id')} AS source_liquidity_attempt_id,
        toString(b.session_date) AS session_date,b.ticker,b.resolution_ms,
        (toUInt64(b.bucket_index)+1)*{res}-{SESSION_OPEN_OFFSET_MS} AS boundary_ms,
        b.close_int,b.high_int,b.trade_count,toBool(b.price_valid) AS price_valid,
        toBool(b.extremes_valid) AS extremes_valid,i.macd_line,i.macd_signal
        FROM arte.bars_v1 b LEFT JOIN arte.indicators_v1 i ON
        i.build_id=b.build_id AND i.session_date=b.session_date AND i.ticker=b.ticker
        AND i.resolution_ms=b.resolution_ms AND i.bucket_index=b.bucket_index
        AND (i.ticker,i.attempt_id) IN ({attempts('indicators_attempt_id')})
        WHERE b.build_id={_literal(plan.build_id)} AND b.session_date=toDate({_literal(session_date.isoformat())})
        AND (b.ticker,b.attempt_id) IN ({attempts('bars_attempt_id')})
        AND b.resolution_ms={res} AND b.bucket_index>={SESSION_OPEN_OFFSET_MS // res}
        AND b.bucket_index<{(SESSION_OPEN_OFFSET_MS + through_boundary_ms) // res}
        ORDER BY b.ticker,b.bucket_index LIMIT {max_rows + 1}
        SETTINGS join_use_nulls=1 FORMAT ArrowStream'''
    assert_select_only(sql)
    stream = client.iter_arrow_record_batches(sql)
    frames, count = [], 0
    try:
        for batch in stream:
            if batch.schema.names != list(SCHEMA) or batch.nbytes > 64 * 1024 * 1024:
                raise ValueError('Arrow completed-bar shape/byte bound mismatch')
            count += batch.num_rows
            if count > max_rows:
                raise ValueError('Completed-bar row bound exceeded')
            observed = pl.from_arrow(batch)
            for flag in ('price_valid', 'extremes_valid'):
                if (observed.schema[flag] not in (pl.Boolean, pl.UInt8)
                        or observed[flag].null_count()
                        or observed.filter(~pl.col(flag).cast(pl.UInt8).is_in([0, 1])).height):
                    raise ValueError('Exact producer Boolean/UInt8 flag required')
            frames.append(observed.with_columns(
                pl.col('price_valid', 'extremes_valid').cast(pl.Boolean)))
    finally:
        close = getattr(stream, 'close', None)
        if callable(close):
            close()
    frame = pl.concat(frames) if frames else pl.DataFrame(schema=SCHEMA)
    return CompletedStructuralRejectionLookup(frame, **kwargs)
