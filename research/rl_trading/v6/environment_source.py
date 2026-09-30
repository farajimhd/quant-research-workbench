"""On-demand pinned ARTE execution evidence. No dense quote shard is saved.

Policy observations never receive this object. Bounded reads cover only
submitted/held tickers between consecutive decision clocks. Passive targets
require the existing certified execution-price sidecar, not candle volume.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
import polars as pl
from research.rl_trading.v1 import arte_sql as sql
from research.rl_trading.v1.arte_source import frame
from research.rl_trading.v1.bracket_source import broker_attempts, assert_liquidity_storage
from research.rl_trading.v6.entry_source import _midnight_us
from research.rl_trading.v6.oms import Quote


@dataclass(frozen=True)
class ExecutionBucket:
    ticker: str
    close_us: int
    quote: Quote | None
    high: float | None
    low: float | None


EXECUTION_SCHEMA = {'ticker':pl.String, 'bucket_index':pl.Int64,
    'quote_count':pl.Int64, 'extrema_count':pl.Int64,
    'quote_timestamp_us':pl.Int64, 'quote_valid':pl.Int64, 'bid_int':pl.Int64,
    'ask_int':pl.Int64, 'bid_size':pl.Float64, 'ask_size':pl.Float64,
    'event_count':pl.Int64, 'last_event_us':pl.Int64, 'high_int':pl.Int64,
    'low_int':pl.Int64, 'extremes_valid':pl.Int64}


def _execution_buckets(rows, origin):
    """Validate joined columns in bulk; materialize objects only for OMS use."""
    keys = ['ticker', 'bucket_index']
    if (rows.select(*keys).n_unique() != rows.height or rows.filter(
        (pl.col('quote_count').fill_null(0)>1) |
        (pl.col('extrema_count').fill_null(0)>1)).height):
        raise ValueError('Duplicate pinned quote or extrema bucket')
    invalid = rows.filter((pl.col('extremes_valid') == 1) &
        (~((pl.col('low_int') > 0) & (pl.col('high_int') >= pl.col('low_int'))).fill_null(False)))
    if invalid.height:
        raise ValueError('Malformed certified bucket extrema')
    joined = rows.with_columns(
        (pl.col('quote_count').fill_null(0)==1).alias('has_quote'),
        *[pl.col(k).fill_null(0) for k in ('quote_timestamp_us','quote_valid',
            'bid_int','ask_int','bid_size','ask_size','event_count','last_event_us')],
        (origin+(pl.col('bucket_index')+1)*100_000).alias('close_us'))
    joined = joined.with_columns(
        ((pl.col('quote_valid') == 1) & (pl.col('event_count') > 0) &
         (pl.col('last_event_us') >= pl.col('close_us')-100_000) &
         (pl.col('last_event_us') < pl.col('close_us')) &
         (pl.col('quote_timestamp_us') > 0) &
         (pl.col('quote_timestamp_us') <= pl.col('last_event_us'))).fill_null(False).alias('valid'),
        pl.when(pl.col('extremes_valid') == 1).then(pl.col('high_int')).alias('high'),
        pl.when(pl.col('extremes_valid') == 1).then(pl.col('low_int')).alias('low'))
    projected = joined.sort('close_us','ticker').select('ticker','close_us','has_quote',
        'quote_timestamp_us','bid_int','ask_int','bid_size','ask_size','valid','high','low')
    return tuple(ExecutionBucket(t,clock,
        Quote(clock,quoted,bid/10000,ask/10000,bs,ass,valid) if present else None,
        high/10000 if high is not None else None,low/10000 if low is not None else None)
        for t,clock,present,quoted,bid,ask,bs,ass,valid,high,low in projected.iter_rows())


class ArteExecutionSource:
    def __init__(self, reader, source, ledger, day, *, end_us):
        self.reader, self.source, self.ledger, self.day = reader, source, ledger, day
        self.origin = _midnight_us(day)
        self.end_us = end_us
        self.attempts = broker_attempts(source, ledger, day, set(source['units'][str(day)]))
        self.price_plans = {}
        self.read_hash = sha256()
        self.query_count = self.rows_read = 0
        self.bucket_cache = {}
        assert_liquidity_storage(reader)
        from research.rl_trading.v1.arte_source import storage_check
        storage_check(reader)

    def _frame(self, statement, schema):
        rows = frame(self.reader, statement, schema)
        self.read_hash.update(statement.encode())
        self.read_hash.update(rows.hash_rows(seed=17).to_numpy().tobytes())
        self.query_count += 1
        self.rows_read += rows.height
        return rows

    def buckets(self, start_us, end_us, tickers):
        """Prefetch execution-only evidence in bounded 15s ticker chunks.

        Future cached rows never leave this provider before their clock.
        New/held listings share batched queries. Recently closed listings retain
        their already fetched rows until expiry, so reentries do not refetch.
        No quote cache is saved or exposed to the policy.
        """
        if not self.origin <= start_us < end_us <= self.end_us:
            raise ValueError('Execution read escaped certified session clock')
        tickers = sorted(set(tickers))
        if set(tickers)-set(self.attempts):
            raise ValueError('Execution listing absent from pinned broker population')
        # Keep only unexpired evidence, bounded to 1,024 inactive listings.
        # Requested listings are never evicted. Cached future rows remain private
        # and the searchsorted interval below still enforces the decision clock.
        active=set(tickers)
        inactive=sorted(((t,v) for t,v in self.bucket_cache.items()
                         if t not in active and v[1]>start_us),
                        key=lambda item:item[1][1],reverse=True)[:1024]
        retained={t:v for t,v in self.bucket_cache.items() if t in active}
        self.bucket_cache=dict(inactive)
        self.bucket_cache.update(retained)
        missing=[t for t in tickers if t not in self.bucket_cache or
                 self.bucket_cache[t][0]>start_us or self.bucket_cache[t][1]<end_us]
        if missing:
            stop=min(self.end_us,max(end_us,start_us+15_000_000))
            rows=self._read_buckets(start_us,stop,missing)
            grouped={t:[] for t in missing}
            for row in rows: grouped[row.ticker].append(row)
            for t in missing:
                values=tuple(grouped[t])
                import numpy as np
                self.bucket_cache[t]=(start_us,stop,values,
                    np.asarray([v.close_us for v in values],dtype=np.int64))
        result=[]
        for t in tickers:
            _,_,values,clocks=self.bucket_cache[t]
            left=clocks.searchsorted(start_us,side='right')
            right=clocks.searchsorted(end_us,side='right')
            result.extend(values[left:right])
        return tuple(sorted(result,key=lambda row:(row.close_us,row.ticker)))

    def _read_buckets(self, start_us, end_us, tickers):
        if not self.origin <= start_us < end_us <= self.end_us:
            raise ValueError('Execution read escaped certified session clock')
        tickers = sorted(set(tickers))
        if set(tickers)-set(self.attempts):
            raise ValueError('Execution listing absent from pinned broker population')
        parts = []
        first = (start_us-self.origin)//100_000
        last = (end_us-self.origin)//100_000-1
        for offset in range(0, len(tickers), 100):
            group = tickers[offset:offset+100]
            quote_scope = ','.join(f'({sql.literal(t)},toUUID({sql.literal(self.attempts[t])}))' for t in group)
            where = (f'build_id={sql.literal(self.source["build_id"])} AND '
                     f'session_date=toDate({sql.literal(self.day)}) AND '
                     f'bucket_index BETWEEN {first} AND {last}')
            bar_scope = ','.join(f'({sql.literal(t)},toUUID({sql.literal(self.source["units"][str(self.day)][t]["bars"]["attempt_id"])}))' for t in group)
            # One sparse joined projection, with explicit multiplicity witnesses.
            # FULL ALL retains gaps and duplicates; ANY could hide source errors.
            rows = self._frame(
                'SELECT if(ifNull(q.quote_count,0)>0,q.ticker,b.ticker) AS ticker,'
                'if(ifNull(q.quote_count,0)>0,q.bucket_index,b.bucket_index) AS bucket_index,'
                'q.quote_count AS quote_count,b.extrema_count AS extrema_count,q.quote_timestamp_us,q.quote_valid,'
                'q.bid_int,q.ask_int,q.bid_size,q.ask_size,q.event_count,q.last_event_us,'
                'b.high_int,b.low_int,b.extremes_valid FROM '
                '(SELECT ticker,bucket_index,quote_timestamp_us,quote_valid,bid_int,ask_int,'
                'bid_size,ask_size,event_count,last_event_us,'
                'count() OVER (PARTITION BY ticker,bucket_index) AS quote_count '
                f'FROM arte.liquidity_100ms_v1 WHERE {where} '
                f'AND (ticker,attempt_id) IN ({quote_scope})) q FULL ALL JOIN '
                '(SELECT ticker,bucket_index,high_int,low_int,extremes_valid,'
                'count() OVER (PARTITION BY ticker,bucket_index) AS extrema_count '
                f'FROM arte.bars_v1 WHERE {where} AND resolution_ms=100 '
                f'AND (ticker,attempt_id) IN ({bar_scope})) b '
                'ON q.ticker=b.ticker AND q.bucket_index=b.bucket_index '
                'ORDER BY bucket_index,ticker', EXECUTION_SCHEMA)
            parts.append(rows)
        rows = pl.concat(parts) if parts else pl.DataFrame(schema=EXECUTION_SCHEMA)
        return _execution_buckets(rows, self.origin)

    def target_capacity(self, ticker, close_us, target):
        return self.target_capacities(close_us, {ticker:target})[ticker]

    def target_capacities(self, close_us, targets):
        """One sparse price read for independent targets at the same clock."""
        if not targets:
            return {}
        from src.backend.backtest_market_data import CertifiedMarketDayPlan, MarketDayUnit, ExecutionInterval
        from src.backend.backtest_liquidity_price import certify_price_level_plan, PriceLevelPlan
        from research.rl_trading.v6.bracket_evidence import read_touched_price_levels
        missing = sorted(set(targets)-set(self.price_plans))
        if missing:
            token = sha256(json.dumps([self.source['build_id'],str(self.day),
                [(t,self.attempts[t]) for t in missing]]).encode()).hexdigest()
            market = CertifiedMarketDayPlan(ExecutionInterval.fixed(100),self.source['build_id'],
                self.source['definition_hash'],(str(self.day),),tuple(missing),
                tuple(MarketDayUnit(build_id=self.source['build_id'],session_date=str(self.day),
                    ticker=t,stage='broker_100ms',attempt_id=self.attempts[t],
                    source_hash='',output_rows=0,output_hash='') for t in missing),(100,),token)
            certified = certify_price_level_plan(market,self.reader)
            for t in missing:
                self.price_plans[t] = certified
        if len(targets) == 1:
            plan = self.price_plans[next(iter(targets))]
        else:
            units = {u.ticker:u for t in targets for u in self.price_plans[t].units
                     if u.ticker in targets}
            plan = PriceLevelPlan(self.source['build_id'],tuple(units[t] for t in sorted(units)),
                sha256(json.dumps(sorted(self.price_plans[t].token for t in targets)).encode()).hexdigest())
        touched = pl.DataFrame({'ticker':list(targets),'boundary_us':[close_us]*len(targets),
                                'target_touched':[True]*len(targets),'stop_touched':[False]*len(targets)})
        prices = read_touched_price_levels(self.reader,self.source,self.ledger,
            plan,self.day,touched)
        self.query_count += 1
        self.rows_read += prices.height
        self.read_hash.update(prices.hash_rows(seed=17).to_numpy().tobytes())
        # Sell limit can execute at the limit or higher; still only an upper bound.
        thresholds=pl.DataFrame({'ticker':list(targets),'threshold':[v*10000-1e-6 for v in targets.values()]})
        totals=prices.join(thresholds,on='ticker',how='inner').filter(
            pl.col('price_int')>=pl.col('threshold')).group_by('ticker').agg(pl.col('execution_volume').sum())
        values=dict(totals.iter_rows())
        return {t:float(values.get(t,0.)) for t in targets}

    def certificate(self):
        return {'status':'complete','day':str(self.day),'source_build_id':self.source['build_id'],
                'modeled_luld_certificate_sha256':getattr(self,'luld_certificate',None),
                'broker_attempts_sha256':sha256(json.dumps(self.attempts,sort_keys=True).encode()).hexdigest(),
                'execution_read_sha256':self.read_hash.hexdigest(),
                'query_count':self.query_count,'rows_read':self.rows_read,
                'execution_read_contract':'joined-quote-extrema-and-batched-target-v2',
                'price_plan_tokens':{t:p.token for t,p in self.price_plans.items()},
                'scope':'only_requested_order_and_held_buckets_not_full_day_grid'}
