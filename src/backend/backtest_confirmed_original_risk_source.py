"""Bounded completed-pair projection of certified native bars and MACD facts."""
from datetime import date
from types import MappingProxyType
from bisect import bisect_left

import polars as pl

from src.trading_runtime.confirmed_original_risk_failure import CompletedRiskBucket
from .backtest_market_data import CertifiedMarketDayPlan, MarketDayUnit, SESSION_OPEN_OFFSET_MS, _literal, assert_select_only

SOURCE_KEYS = ('source_build_id', 'source_market_plan_token', 'source_bars_attempt_id',
               'source_indicators_attempt_id', 'session_date', 'ticker', 'source_liquidity_attempt_id')
FACT_KEYS = ('boundary_ms', 'close_int', 'price_valid', 'macd_line', 'macd_signal')
REQUIRED = (*SOURCE_KEYS, *FACT_KEYS)


def compile_completed_risk_pairs(frame):
    """Shift within exact source identity; retain missing rows and original order."""
    if type(frame) is not pl.DataFrame or tuple(frame.columns) != REQUIRED:
        raise ValueError('Completed risk projection requires its closed typed source shape')
    if (any(frame.schema[k] != pl.String for k in SOURCE_KEYS)
            or not frame.schema['boundary_ms'].is_integer()
            or not frame.schema['close_int'].is_integer()
            or frame.schema['price_valid'] != pl.Boolean
            or any(not frame.schema[k].is_float() for k in ('macd_line', 'macd_signal'))):
        raise ValueError('Completed risk projection has changed source types')
    if frame.select(pl.any_horizontal(
            *(pl.col(k).is_null() | (pl.col(k).str.len_chars() == 0) for k in SOURCE_KEYS),
            pl.col('boundary_ms').is_null(), pl.col('boundary_ms') <= 0,
            pl.col('boundary_ms') > 57_600_000, pl.col('boundary_ms') % 5000 != 0).any()).item():
        raise ValueError('Completed risk projection has invalid source keys or clocks')
    if frame.select(pl.struct(*SOURCE_KEYS, 'boundary_ms').is_duplicated().any()).item():
        raise ValueError('Completed risk projection repeats a producer observation')
    f = frame.with_row_index('_risk_order').sort([*SOURCE_KEYS, 'boundary_ms'])
    f = f.with_columns(*(pl.col(k).shift(1).over(SOURCE_KEYS).alias('prior_'+k) for k in FACT_KEYS))
    return f.with_columns((pl.col('boundary_ms')-pl.col('prior_boundary_ms') == 5000)
                         .fill_null(False).alias('pair_complete')).sort('_risk_order').drop('_risk_order')


class CompiledCompletedRiskLookup:
    """O(1) exact-boundary survivor lookup; future rows are never exposed."""
    def __init__(self, frame, *, plan, session_date, max_rows=2_000_000):
        if (type(plan) is not CertifiedMarketDayPlan or type(session_date) is not date
                or plan.sessions != (session_date.isoformat(),)
                or type(max_rows) is not int or not 1 <= max_rows <= 20_000_000
                or frame.height > max_rows):
            raise ValueError('Completed risk lookup lacks bounded certified session authority')
        units = {}
        stages = ('bars', 'technical', 'broker_100ms')
        declared_tickers = frozenset(plan.tickers)
        producer_units = {}
        for unit in plan.units:
            if (type(unit) is MarketDayUnit and unit.ticker in declared_tickers
                    and unit.stage in stages):
                producer_units.setdefault((unit.ticker, unit.stage), []).append(unit)
        for ticker in plan.tickers:
            pair = {}
            for stage in stages:
                matches = producer_units.get((ticker, stage), ())
                if (len(matches) != 1 or matches[0].build_id != plan.build_id
                        or matches[0].session_date != session_date.isoformat()):
                    raise ValueError('Completed risk source lacks exact producer attempts')
                pair[stage] = matches[0].attempt_id
            units[ticker] = pair
        expected = pl.DataFrame([dict(source_build_id=plan.build_id,
            source_market_plan_token=plan.token, source_bars_attempt_id=u['bars'],
            source_indicators_attempt_id=u['technical'], session_date=session_date.isoformat(), ticker=t,
            source_liquidity_attempt_id=u['broker_100ms'])
            for t,u in units.items()], schema={k:pl.String for k in SOURCE_KEYS})
        if frame.select(SOURCE_KEYS).unique().join(expected, on=SOURCE_KEYS, how='anti').height:
            raise ValueError('Completed risk observations cross the certified source plan')
        compiled = compile_completed_risk_pairs(frame)
        # Only complete finite adverse-momentum survivors become scalar objects.
        candidates = compiled.filter(pl.col('pair_complete') & pl.col('price_valid')
            & pl.col('prior_price_valid') & pl.col('close_int').is_not_null()
            & pl.col('prior_close_int').is_not_null()
            & (pl.col('close_int') > 0) & (pl.col('prior_close_int') > 0)
            & pl.all_horizontal(*(pl.col(k).is_finite() for k in
                ('macd_line','macd_signal','prior_macd_line','prior_macd_signal')))
            & (pl.col('macd_line') < pl.col('macd_signal'))
            & (pl.col('prior_macd_line') < pl.col('prior_macd_signal')))
        self._columns = MappingProxyType(self._partition(frame.sort(['ticker','boundary_ms'])))
        self._pairs = MappingProxyType(self._partition(candidates.sort(['ticker','boundary_ms'])))
        self.plan = plan
        self.session_date = session_date

    def pair_at(self, ticker, boundary_ms):
        if type(ticker) is not str or type(boundary_ms) is not int or not 0 < boundary_ms <= 57_600_000:
            raise ValueError('Completed risk lookup requires exact decision boundary')
        row = self._at(self._pairs,ticker,boundary_ms)
        if row is None:return None
        identity = {k:row[k] for k in SOURCE_KEYS}
        return (CompletedRiskBucket(**identity,**{k:row['prior_'+k] for k in FACT_KEYS}),
                CompletedRiskBucket(**identity,**{k:row[k] for k in FACT_KEYS}))

    @staticmethod
    def _partition(frame):
        return {key[0]:part for key,part in frame.partition_by('ticker',as_dict=True).items()}

    @staticmethod
    def _at(parts,ticker,boundary_ms):
        frame=parts.get(ticker)
        if frame is None:return None
        clocks=frame['boundary_ms']
        index=bisect_left(clocks,boundary_ms)
        if index==len(clocks) or clocks[index]!=boundary_ms:return None
        return frame.row(index,named=True)

    def bucket_at(self,ticker,boundary_ms):
        if type(ticker) is not str or type(boundary_ms) is not int or not 0<boundary_ms<=57_600_000:
            raise ValueError('Completed risk lookup requires exact decision boundary')
        row=self._at(self._columns,ticker,boundary_ms)
        if row is None:return None
        return CompletedRiskBucket(**{k:row[k] for k in REQUIRED})


def load_completed_risk_lookup(client, *, plan, session_date, tickers,
                               through_boundary_ms=57_600_000, max_rows=2_000_000):
    """One bounded Arrow SELECT, including preceding rows needed after restart."""
    if (type(plan) is not CertifiedMarketDayPlan or type(session_date) is not date
            or plan.sessions != (session_date.isoformat(),) or 5000 not in plan.required_resolutions_ms
            or type(tickers) is not tuple or len(set(tickers)) != len(tickers)
            or set(tickers)-set(plan.tickers) or type(through_boundary_ms) is not int
            or not 0 < through_boundary_ms <= 57_600_000 or through_boundary_ms % 100
            or type(max_rows) is not int or not 1 <= max_rows <= 20_000_000):
        raise ValueError('Completed risk loader requires exact bounded certified scope')
    attempts = {}
    indicator_units = ()
    liquidity_units=()
    for stage in ('bars','technical','broker_100ms'):
        units = tuple(u for u in plan.units if u.stage == stage and u.ticker in tickers)
        if (len(units) != len(tickers) or len({u.ticker for u in units}) != len(tickers)
                or any(u.build_id != plan.build_id or u.session_date != session_date.isoformat() for u in units)):
            raise ValueError('Completed risk loader has missing or foreign producer units')
        attempts[stage] = ','.join(f'({_literal(u.ticker)},toUUID({_literal(u.attempt_id)}))' for u in units)
        if stage == 'technical':indicator_units=units
        if stage == 'broker_100ms':liquidity_units=units
    schema = {**{k:pl.String for k in SOURCE_KEYS}, 'boundary_ms':pl.UInt64,
              'close_int':pl.UInt64,'price_valid':pl.Boolean,'macd_line':pl.Float64,'macd_signal':pl.Float64}
    if not tickers:
        return CompiledCompletedRiskLookup(pl.DataFrame(schema=schema),plan=plan,session_date=session_date,max_rows=max_rows)
    indicator_pin = 'CASE ' + ' '.join(f'WHEN b.ticker={_literal(u.ticker)} THEN {_literal(u.attempt_id)}'
                                      for u in indicator_units) + " ELSE '' END"
    liquidity_pin = 'CASE ' + ' '.join(f'WHEN b.ticker={_literal(u.ticker)} THEN {_literal(u.attempt_id)}'
                                      for u in liquidity_units) + " ELSE '' END"
    query = assert_select_only(f'''SELECT b.build_id AS source_build_id,
        {_literal(plan.token)} AS source_market_plan_token,toString(b.attempt_id) AS source_bars_attempt_id,
        {indicator_pin} AS source_indicators_attempt_id,toString(b.session_date) AS session_date,b.ticker,
        {liquidity_pin} AS source_liquidity_attempt_id,
        (toUInt64(b.bucket_index)+1)*5000-{SESSION_OPEN_OFFSET_MS} AS boundary_ms,
        b.close_int,toBool(b.price_valid AND i.resolution_ms=5000) AS price_valid,i.macd_line,i.macd_signal
        FROM arte.bars_v1 b LEFT JOIN arte.indicators_v1 i ON
        i.build_id=b.build_id AND i.session_date=b.session_date AND i.ticker=b.ticker
        AND i.resolution_ms=b.resolution_ms AND i.bucket_index=b.bucket_index
        AND (i.ticker,i.attempt_id) IN ({attempts['technical']})
        WHERE b.build_id={_literal(plan.build_id)} AND b.session_date=toDate({_literal(session_date.isoformat())})
        AND (b.ticker,b.attempt_id) IN ({attempts['bars']}) AND b.resolution_ms=5000
        AND b.bucket_index>={SESSION_OPEN_OFFSET_MS//5000}
        AND b.bucket_index<{(SESSION_OPEN_OFFSET_MS+through_boundary_ms)//5000}
        ORDER BY b.ticker,b.bucket_index LIMIT {max_rows+1} FORMAT ArrowStream''')
    stream = client.iter_arrow_record_batches(query)
    frames=[];count=0
    try:
        for batch in stream:
            if tuple(batch.schema.names) != REQUIRED or batch.nbytes > 64*1024*1024:
                raise ValueError('Completed risk Arrow shape or bounded batch changed')
            count += batch.num_rows
            if count > max_rows:
                raise ValueError('Completed risk source overflow; no truncation permitted')
            frames.append(pl.from_arrow(batch).with_columns(pl.col('price_valid').cast(pl.Boolean)))
    finally:
        close=getattr(stream,'close',None)
        if close is not None:close()
    frame=pl.concat(frames) if frames else pl.DataFrame(schema=schema)
    if frame.height and frame.filter(pl.col('boundary_ms') > through_boundary_ms).height:
        raise ValueError('Completed risk source crossed its requested as-of boundary')
    return CompiledCompletedRiskLookup(frame,plan=plan,session_date=session_date,max_rows=max_rows)
