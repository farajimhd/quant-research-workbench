"""Audit repaired build population, independent OHLC, and MACD recurrence.

Reads canonical events and pinned ARTE products only. No flatfile or writers.
Full-population stage row counts are checked against the certification ledger;
independent price/indicator calculations are bounded to declared liquid tickers.
"""
from __future__ import annotations
import argparse
from datetime import date, datetime
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
from zoneinfo import ZoneInfo

os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import build_market_day as builder
from pipelines.market_sip.events import market_day_sql as sql


def independent_prices(events, rules, midnight_us):
    """Chronological eligible prints; rules shared, aggregation independent."""
    rules={int(r['token_id']):r for r in rules}
    seconds={}; delayed=unknown=0
    for r in events:
        meta=int(r['event_meta'])
        if meta & 128:
            delayed+=1; continue
        unknown+=int(not(meta & 64))
        price=int(r['price_primary_int'])*(1 if meta & 2 else 100)
        size=float(r['size_primary'])
        if price<=0 or size<=0 or not math.isfinite(size): continue
        tokens=[int(r[f'condition_token_{i}']) for i in range(1,6) if r[f'condition_token_{i}']]
        if not tokens or any(t not in rules for t in tokens): continue
        second=(int(r['sip_timestamp_us'])-midnight_us)//1_000_000
        form_ok=(second<34200 or second>=57600) and any(rules[t]['modifier_int']==12 for t in tokens) and all(
            rules[t]['modifier_int']==12 or (rules[t]['update_last'] and rules[t]['update_high_low']) for t in tokens)
        eligible=lambda key:all(rules[t][key] or (form_ok and rules[t]['modifier_int']==12) for t in tokens)
        value=seconds.setdefault(second,{'last':[],'extremes':[]})
        if eligible('update_last'): value['last'].append(price)
        if eligible('update_high_low'): value['extremes'].append(price)
    return {second:dict(open_int=v['last'][0],close_int=v['last'][-1],
        high_int=max(v['extremes']),low_int=min(v['extremes'])) for second,v in seconds.items()
        if v['last'] and v['extremes']},dict(delayed_excluded=delayed,unknown_clock_trades=unknown)


def recurrence(rows, seed=None):
    maximum=0.; state=seed
    for row in rows:
        close=row['close_int']/10000.
        if state is None: ema12=ema26=close; signal=0.
        else:
            ema12=state[0]+2/13*(close-state[0]);ema26=state[1]+2/27*(close-state[1])
            signal=state[2]+.2*(ema12-ema26-state[2])
        line=ema12-ema26
        maximum=max(maximum,abs(ema12-row['ema_12']),abs(ema26-row['ema_26']),
            abs(line-row['macd_line']),abs(signal-row['macd_signal']))
        state=(ema12,ema26,signal)
    if maximum>1e-7: raise ValueError(f'Independent MACD recurrence mismatch: {maximum}')
    return state,maximum


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--tickers',default='NVDA,AAPL')
    p.add_argument('--previous-build-id',action='append',default=[])
    a=p.parse_args(argv)
    root=Path('D:/TradingML/runtimes').resolve()
    if not a.output.resolve().is_relative_to(root) or not a.manifest.resolve().is_relative_to(root):
        raise ValueError('External runtime paths required')
    report=json.loads(a.manifest.read_text()); definition=report['definition']; build=report['build_id']
    if report['status']!='core_complete':raise ValueError('Build has not completed')
    args=builder.parse_args(['--start-date',definition['start'],'--end-date',definition['end'],
        '--max-memory-gb','8','--query-timeout','1800','--env-file','D:/TradingML/secrets/.env'])
    c=builder.Client(args,persistent=False)
    ledger=sqlite3.connect('file:'+str(root/'build-ledger-v2.sqlite3')+'?mode=ro',uri=True)
    ledger.row_factory=sqlite3.Row
    results=[]; days=definition['plan']['requested']
    try:
        builder.storage_preflight(c,'arte',True)
        coverage=builder.reporting_coverage(c,date.fromisoformat(definition['start']),date.fromisoformat(definition['end']))
        if coverage!=definition['trade_eligibility']['verified_coverage']:raise ValueError('Reporting receipts changed')
        proof=ledger.execute('SELECT * FROM builds WHERE build_id=?',(build,)).fetchone()
        if not proof or proof['status']!='core_complete' or proof['definition_hash']!=builder.digest(definition):
            raise ValueError('Build ledger definition mismatch')
        for day in days:
            units=[dict(r) for r in ledger.execute('SELECT * FROM units WHERE build_id=? AND session_date=?',(build,day))]
            expected={r['ticker'] for r in definition['plan']['units'] if r['source_date']==day}
            for stage,table in [('bars','bars_v1'),('technical','indicators_v1'),('broker_100ms','liquidity_100ms_v1')]:
                certified={r['ticker']:r for r in units if r['stage']==stage and r['status']=='complete'}
                if set(certified)!=expected:raise ValueError(f'{day}: incomplete {stage} population')
                observed=c.query(f"SELECT ticker,toString(attempt_id) attempt_id,count() n FROM arte.{table} WHERE build_id={sql.literal(build)} AND session_date={sql.literal(day)} GROUP BY ticker,attempt_id",'audit_stage_counts')
                by_key={(r['ticker'],r['attempt_id']):r['n'] for r in observed}
                for ticker,r in certified.items():
                    if by_key.pop((ticker,r['attempt_id']),0)!=r['output_rows']:
                        raise ValueError(f'{day} {ticker}: {stage} rows differ from ledger')
                if by_key:raise ValueError(f'{day}: uncertified {stage} attempts remain')
            results.append(dict(day=day,tickers=len(expected),status='certified_row_counts_passed'))
            print(f'Audit {day}: {len(expected)} ticker-days, three stage counts verified',flush=True)
        samples=[]
        for ticker in a.tickers.split(','):
            seed=None
            for day in days:
                unit={r['stage']:dict(r) for r in ledger.execute('SELECT * FROM units WHERE build_id=? AND session_date=? AND ticker=?',(build,day,ticker))}
                if not unit:raise ValueError(f'{day} {ticker}: audit ticker absent')
                where=sql.selection(build,day,ticker,unit['bars']['attempt_id'])
                technical=sql.selection(build,day,ticker,unit['technical']['attempt_id'])
                values=c.query(f"SELECT b.bucket_index,b.close_int,i.ema_12,i.ema_26,i.macd_line,i.macd_signal FROM (SELECT bucket_index,close_int FROM arte.bars_v1 WHERE {where} AND resolution_ms=1000 AND price_valid=1 AND extremes_valid=1) b INNER JOIN (SELECT bucket_index,ema_12,ema_26,macd_line,macd_signal FROM arte.indicators_v1 WHERE {technical} AND resolution_ms=1000) i USING(bucket_index) ORDER BY bucket_index",'independent_macd')
                seed,error=recurrence(values,seed)
                sample=dict(day=day,ticker=ticker,macd_candles=len(values),maximum_macd_error=error)
                if a.previous_build_id:
                    originals=[dict(r) for r in ledger.execute(
                        'SELECT * FROM units WHERE session_date=? AND ticker=? AND stage=\'bars\' AND build_id IN ('+
                        ','.join('?' for _ in a.previous_build_id)+')',[day,ticker,*a.previous_build_id])]
                    if len(originals)!=1:raise ValueError(f'{day} {ticker}: original bar authority is ambiguous')
                    original=originals[0]
                    previous=sql.selection(original['build_id'],day,ticker,original['attempt_id'])
                    projection='bucket_index,open_int,high_int,low_int,close_int'
                    def prices(selected):
                        rows=c.query(f"SELECT {projection} FROM arte.bars_v1 WHERE {selected} AND resolution_ms=1000 AND price_valid=1 AND extremes_valid=1 ORDER BY bucket_index",'bar_change_inventory')
                        return {r['bucket_index']:tuple(r[k] for k in ('open_int','high_int','low_int','close_int')) for r in rows}
                    old,new=prices(previous),prices(where)
                    changed=sorted(k for k in old.keys()|new.keys() if old.get(k)!=new.get(k))
                    sample.update(original_build_id=original['build_id'],changed_1s_price_bars=len(changed),
                        removed_1s_price_bars=len(old.keys()-new.keys()),added_1s_price_bars=len(new.keys()-old.keys()),
                        first_changes=[dict(bucket_index=k,old_ohlc=old.get(k),new_ohlc=new.get(k)) for k in changed[:20]])
                if day in ('2026-07-30','2026-07-31','2026-08-03','2026-08-18','2026-09-18'):
                    midnight=int(datetime.fromisoformat(day).replace(tzinfo=ZoneInfo('America/New_York')).timestamp()*1e6)
                    events=c.query(f"SELECT sip_timestamp_us,ordinal,event_meta,price_primary_int,size_primary,condition_token_1,condition_token_2,condition_token_3,condition_token_4,condition_token_5 FROM market_sip_compact.events_2026 WHERE ticker={sql.literal(ticker)} AND event_date BETWEEN {sql.literal(day)} AND toDate({sql.literal(day)})+1 AND bitAnd(event_meta,1)=1 AND sip_timestamp_us>={midnight+14400*1000000} AND sip_timestamp_us<{midnight+15300*1000000} ORDER BY sip_timestamp_us,ordinal",'independent_prices')
                    expected,evidence=independent_prices(events,definition['plan']['rules'],midnight)
                    observed=c.query(f"SELECT bucket_index,open_int,high_int,low_int,close_int FROM arte.bars_v1 WHERE {where} AND resolution_ms=1000 AND price_valid=1 AND extremes_valid=1 AND bucket_index>=14400 AND bucket_index<15300 ORDER BY bucket_index",'observed_prices')
                    actual={int(r['bucket_index']):{k:r[k] for k in ('open_int','high_int','low_int','close_int')} for r in observed}
                    if actual!=expected:raise ValueError(f'{day} {ticker}: independent eligible OHLC mismatch')
                    sample.update(first_15_minute_ohlc_candles=len(actual),**evidence)
                samples.append(sample)
                print(f'Audit {day} {ticker}: independent MACD error {error:.3g}',flush=True)
        builder.save(a.output,dict(status='passed',build_id=build,definition_hash=builder.digest(definition),
            full_population_stage_counts=results,independent_samples=samples,
            scope='all certified ticker-day stage counts; independent 1s MACD all sessions for declared tickers; independent first 15 min OHLC on declared sample dates',
            sealed_test_labels_accessed=False))
    finally:
        c.close();ledger.close()
    return 0


if __name__=='__main__':raise SystemExit(main())
