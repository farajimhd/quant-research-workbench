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


class ArteExecutionSource:
    def __init__(self, reader, source, ledger, day, *, end_us):
        self.reader, self.source, self.ledger, self.day = reader, source, ledger, day
        self.origin = _midnight_us(day)
        self.end_us = end_us
        self.attempts = broker_attempts(source, ledger, day, set(source['units'][str(day)]))
        self.price_plans = {}
        self.read_hash = sha256()
        self.query_count = self.rows_read = 0
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
        if not self.origin <= start_us < end_us <= self.end_us:
            raise ValueError('Execution read escaped certified session clock')
        tickers = sorted(set(tickers))
        if set(tickers)-set(self.attempts):
            raise ValueError('Execution listing absent from pinned broker population')
        records = {}
        first = (start_us-self.origin)//100_000
        last = (end_us-self.origin)//100_000-1
        for offset in range(0, len(tickers), 100):
            group = tickers[offset:offset+100]
            quote_scope = ','.join(f'({sql.literal(t)},toUUID({sql.literal(self.attempts[t])}))' for t in group)
            where = (f'build_id={sql.literal(self.source["build_id"])} AND '
                     f'session_date=toDate({sql.literal(self.day)}) AND '
                     f'bucket_index BETWEEN {first} AND {last}')
            quote_rows = self._frame('SELECT ticker,bucket_index,quote_timestamp_us,quote_valid,'
                'bid_int,ask_int,bid_size,ask_size,event_count,last_event_us FROM arte.liquidity_100ms_v1 '
                f'WHERE {where} AND (ticker,attempt_id) IN ({quote_scope}) ORDER BY bucket_index,ticker',
                {'ticker':pl.String,'bucket_index':pl.Int64,'quote_timestamp_us':pl.Int64,
                 'quote_valid':pl.Int64,'bid_int':pl.Int64,'ask_int':pl.Int64,
                 'bid_size':pl.Float64,'ask_size':pl.Float64,'event_count':pl.Int64,'last_event_us':pl.Int64})
            for row in quote_rows.iter_rows(named=True):
                close = self.origin+(int(row['bucket_index'])+1)*100_000
                event = int(row['last_event_us'] or 0)
                quoted = int(row['quote_timestamp_us'] or 0)
                valid = (int(row['quote_valid'] or 0)==1 and int(row['event_count'] or 0)>0
                         and close-100_000 <= event < close and 0 < quoted <= event)
                key = (close,row['ticker'])
                if key in records:
                    raise ValueError('Duplicate pinned quote bucket')
                records[key] = [Quote(close,quoted,float(row['bid_int'] or 0)/10000,
                    float(row['ask_int'] or 0)/10000,float(row['bid_size'] or 0),
                    float(row['ask_size'] or 0),valid),None,None]
            bar_scope = ','.join(f'({sql.literal(t)},toUUID({sql.literal(self.source["units"][str(self.day)][t]["bars"]["attempt_id"])}))' for t in group)
            bars = self._frame('SELECT ticker,bucket_index,high_int,low_int,extremes_valid FROM arte.bars_v1 '
                f'WHERE {where} AND resolution_ms=100 AND (ticker,attempt_id) IN ({bar_scope}) ORDER BY bucket_index,ticker',
                {'ticker':pl.String,'bucket_index':pl.Int64,'high_int':pl.Int64,'low_int':pl.Int64,'extremes_valid':pl.Int64})
            bar_keys = set()
            for row in bars.iter_rows(named=True):
                key = (self.origin+(int(row['bucket_index'])+1)*100_000,row['ticker'])
                if key in bar_keys:
                    raise ValueError('Duplicate pinned extrema bucket')
                bar_keys.add(key)
                value = records.setdefault(key,[None,None,None])
                if row['extremes_valid']==1:
                    high,low = row['high_int']/10000,row['low_int']/10000
                    if not 0 < low <= high:
                        raise ValueError('Malformed certified bucket extrema')
                    value[1:] = [high,low]
        return tuple(ExecutionBucket(ticker,close,*records[(close,ticker)])
                     for close,ticker in sorted(records))

    def target_capacity(self, ticker, close_us, target):
        from src.backend.backtest_market_data import CertifiedMarketDayPlan, MarketDayUnit, ExecutionInterval
        from src.backend.backtest_liquidity_price import certify_price_level_plan
        from research.rl_trading.v6.bracket_evidence import read_touched_price_levels
        if ticker not in self.price_plans:
            attempt = self.attempts[ticker]
            token = sha256(json.dumps([self.source['build_id'],str(self.day),ticker,attempt]).encode()).hexdigest()
            market = CertifiedMarketDayPlan(ExecutionInterval.fixed(100),self.source['build_id'],
                self.source['definition_hash'],(str(self.day),),(ticker,),
                (MarketDayUnit(build_id=self.source['build_id'],session_date=str(self.day),
                    ticker=ticker,stage='broker_100ms',attempt_id=attempt,
                    source_hash='',output_rows=0,output_hash=''),),(100,),token)
            self.price_plans[ticker] = certify_price_level_plan(market,self.reader)
        touched = pl.DataFrame({'ticker':[ticker],'boundary_us':[close_us],
                                'target_touched':[True],'stop_touched':[False]})
        prices = read_touched_price_levels(self.reader,self.source,self.ledger,
            self.price_plans[ticker],self.day,touched)
        self.query_count += 1
        self.rows_read += prices.height
        self.read_hash.update(prices.hash_rows(seed=17).to_numpy().tobytes())
        # Sell limit can execute at the limit or higher; still only an upper bound.
        return float(prices.filter(pl.col('price_int') >= target*10000-1e-6)['execution_volume'].sum())

    def certificate(self):
        return {'status':'complete','day':str(self.day),'source_build_id':self.source['build_id'],
                'broker_attempts_sha256':sha256(json.dumps(self.attempts,sort_keys=True).encode()).hexdigest(),
                'execution_read_sha256':self.read_hash.hexdigest(),
                'query_count':self.query_count,'rows_read':self.rows_read,
                'price_plan_tokens':{t:p.token for t,p in self.price_plans.items()},
                'scope':'only_requested_order_and_held_buckets_not_full_day_grid'}
