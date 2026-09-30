"""Restart-safe SELECT-only modeled LULD sidecar, bounded ticker workers.

No feature-bank rewrite, raw flatfile read, or ClickHouse writer. Aggregates
eligible transaction prices inside canonical ClickHouse; persists sparse band
changes only. Tier-2 fallback is an explicit research scenario, not membership.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ.setdefault('POLARS_MAX_THREADS', '1')
import argparse
from concurrent.futures import ThreadPoolExecutor
from collections import deque
from datetime import date
import json
from pathlib import Path
from time import perf_counter
import polars as pl
from research.mlops.clickhouse import (ClickHouseHttpClient, default_clickhouse_url,
    default_clickhouse_user, default_clickhouse_password, discover_clickhouse_env_files)
from research.mlops.env import load_env_files
from research.rl_trading.v1 import arte_source
from research.rl_trading.v1.arte_sql import literal
from research.rl_trading.v1.common import digest, file_hash, exclusive
from research.rl_trading.v1.bracket_source import broker_attempts
from research.rl_trading.v6.entry_source import _midnight_us
from research.rl_trading.v6.split import TRAIN, DEVELOPMENT, CONTEXT_ONLY
from research.rl_trading.v6.reference import read_reference
from research.rl_trading.v6.luld import VERSION, project, LuldBook
from pipelines.market_sip.events.market_day_sql import canonical_source, condition_expressions


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(value, sort_keys=True, default=str), encoding='utf-8')
    temporary.replace(path)


def _client():
    return ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(),
        default_clickhouse_password(), persistent=True, timeout_seconds=180,
        default_query_params={'readonly':1, 'max_threads':1, 'max_execution_time':150,
            'max_memory_usage':1073741824, 'max_result_rows':100000,
            'max_result_bytes':30000000, 'result_overflow_mode':'throw'})


def _rows(client, sql):
    return [json.loads(line) for line in client.execute(sql+' FORMAT JSONEachRow').splitlines() if line]


def _worker(packet):
    day, previous_day, listing, source, prior, attempt, rules, folder, tier, scenario = packet
    ticker = listing['ticker']
    target = folder / (digest([ticker,listing['listing_id']])[:24]+'.parquet')
    proof = target.with_suffix('.json')
    contract = {'version':VERSION, 'day':str(day), 'previous_day':str(previous_day),
        'ticker':ticker, 'listing_id':listing['listing_id'], 'source_hash':digest(source),
        'prior_hash':digest(prior), 'broker_attempt':attempt, 'rules_hash':digest(rules),
        'tier':tier, 'tier_authority':scenario}
    if proof.is_file():
        saved=json.loads(proof.read_text())
        if saved['contract']!=contract or file_hash(target)!=saved['sha256']:
            raise ValueError('LULD resume provenance or output hash changed')
        return saved
    client, reader = _client(), arte_source.reader(threads=1)
    began = perf_counter()
    try:
        previous_unit = prior['units'][str(previous_day)].get(ticker)
        origin = _midnight_us(day)
        start, end = origin+34200_000_000, origin+57600_000_000
        prior_close = None
        if previous_unit:
            # Frozen prior population must describe the same listing, checked
            # by caller before any prior price is used.
            where = (f"build_id={literal(prior['build_id'])} AND session_date=toDate({literal(previous_day)}) "
                f"AND ticker={literal(ticker)} AND attempt_id=toUUID({literal(previous_unit['bars']['attempt_id'])})")
            old=_rows(client,'SELECT close_int FROM arte.bars_v1 WHERE '+where+
                ' AND resolution_ms=1000 AND bucket_index>=34200 AND bucket_index<57600 '
                'AND price_valid=1 ORDER BY bucket_index DESC LIMIT 1')
            if old:
                prior_close=old[0]['close_int']/10000
                _, _, _, reference = read_reference(reader, day, listing)
                seen_splits = set()
                for split in reference['splits']:
                    split_day = date.fromisoformat(str(split.get('execution_date', split.get('date'))))
                    if previous_day < split_day <= day and split_day not in seen_splits:
                        prior_close *= float(split['split_from'])/float(split['split_to'])
                        seen_splits.add(split_day)
        schema={'available_us':pl.Int64,'lower':pl.Float64,'upper':pl.Float64,
                'paused':pl.Boolean,'pause_start_us':pl.Int64}
        if prior_close is None:
            changes=pl.DataFrame(schema=schema)
            report={'unknown_prior_close':True,'modeled_pauses':0,'official_halt_evidence':False}
        else:
            form,last,_,_=condition_expressions(rules)
            # Eligible last-sale conditions and canonical delayed flag shared
            # with the pinned market-day compiler; no size-weighted average.
            statement=f'''WITH decoded AS (
              SELECT *,bitAnd(event_meta,1) AS kind,
                toFloat64(price_primary_int)/if(bitAnd(event_meta,2)!=0,10000.,100.) AS price,
                fromUnixTimestamp64Micro(toInt64(sip_timestamp_us),'America/New_York') AS local_time,
                toHour(local_time)*3600+toMinute(local_time)*60+toSecond(local_time) AS local_second,
                arrayFilter(t->t>0,[toUInt16(condition_token_1),toUInt16(condition_token_2),
                  toUInt16(condition_token_3),toUInt16(condition_token_4),toUInt16(condition_token_5)]) AS tokens,
                ({form}) AS form_ok FROM ({canonical_source(day,ticker)})
              ) SELECT toInt64((intDiv(sip_timestamp_us-{start},500000)+1)*500000+{start}) AS bucket_us,
                sum(price) AS price_sum,count() AS count FROM decoded
              WHERE sip_timestamp_us>={start} AND sip_timestamp_us<{end} AND kind=1
                AND bitAnd(event_meta,128)=0 AND price>0 AND size_primary>0 AND {last}
              GROUP BY bucket_us ORDER BY bucket_us'''
            trades=pl.DataFrame(_rows(client,statement),schema={'bucket_us':pl.Int64,
                'price_sum':pl.Float64,'count':pl.Int64})
            quote_sql=('SELECT toInt64((intDiv(bucket_index,5)+1)*500000+'+str(origin)+') AS bucket_us,'
                'argMax(quote_timestamp_us,bucket_index) AS quote_us,'
                'argMax(bid_int,bucket_index)/10000. AS bid,argMax(ask_int,bucket_index)/10000. AS ask '
                f'FROM arte.liquidity_100ms_v1 WHERE build_id={literal(source["build_id"])} '
                f'AND session_date=toDate({literal(day)}) AND ticker={literal(ticker)} '
                f'AND attempt_id=toUUID({literal(attempt)}) AND bucket_index>=342000 AND bucket_index<576000 '
                'AND quote_valid=1 GROUP BY bucket_us ORDER BY bucket_us')
            quotes=pl.DataFrame(_rows(client,quote_sql),schema={'bucket_us':pl.Int64,'quote_us':pl.Int64,
                'bid':pl.Float64,'ask':pl.Float64})
            changes,report=project(start,end,trades,quotes,previous_close=prior_close,tier=tier)
            report['query_sha256']=digest([statement,quote_sql])
            report['aggregate_sha256']=digest([trades.hash_rows(seed=17).to_list(),quotes.hash_rows(seed=17).to_list()])
            report['previous_close']=prior_close
        target.parent.mkdir(parents=True,exist_ok=True)
        temporary=target.with_suffix('.parquet.tmp')
        changes.write_parquet(temporary); temporary.replace(target)
        saved={'status':'complete','contract':contract,'file':target.name,'sha256':file_hash(target),
            'rows':changes.height,'report':report,'elapsed_seconds':perf_counter()-began}
        _write(proof,saved)
        return saved
    finally:
        client.close(); reader.close()


def _bounded(pool, packets, width):
    items=iter(packets)
    pending=deque()
    for _ in range(width*2):
        packet=next(items,None)
        if packet is None: break
        pending.append(pool.submit(_worker,packet))
    try:
        while pending:
            yield pending.popleft().result()
            packet=next(items,None)
            if packet is not None: pending.append(pool.submit(_worker,packet))
    finally:
        for future in pending: future.cancel()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--early-manifest',type=Path,required=True)
    parser.add_argument('--late-manifest',type=Path,required=True)
    parser.add_argument('--ledger',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--workers',type=int,default=8)
    parser.add_argument('--through',type=date.fromisoformat,default=DEVELOPMENT[-1])
    parser.add_argument('--tickers',nargs='+',help='Bounded canary only; never a training sidecar')
    parser.add_argument('--tier-map',type=Path,help='Pinned JSON mapping ticker to historical tier')
    parser.add_argument('--tier2-scenario',action='store_true',help='Explicit modeled fallback, not official membership')
    args=parser.parse_args(argv)
    runtime=Path(os.environ.get('QW_RUNTIME_ROOT','')).resolve()
    if (not runtime.is_dir() or not 1<=args.workers<=32 or
            any(not p.resolve().is_relative_to(runtime) for p in
                (args.output,args.early_manifest,args.late_manifest,args.ledger))):
        raise ValueError('Bounded workers and configured runtime inputs/output required')
    if not args.tier_map and not args.tier2_scenario:
        raise ValueError('Supply tier map or explicitly choose modeled tier2 scenario')
    tiers=json.loads(args.tier_map.read_text()) if args.tier_map else {}
    load_env_files(discover_clickhouse_env_files(),verbose=False)
    output=args.output.resolve(); output.mkdir(parents=True,exist_ok=True)
    previous=CONTEXT_ONLY[0]
    days=[]
    with exclusive(output/'build.lock'):
        for day in TRAIN+DEVELOPMENT:
            if day>args.through: break
            manifest=lambda d:args.early_manifest if d<=date(2026,8,17) else args.late_manifest
            source=arte_source.load_build(manifest(day),args.ledger,[day])
            prior=arte_source.load_build(manifest(previous),args.ledger,[previous])
            reader=arte_source.reader(threads=1)
            try:
                population,pop_proof=arte_source.population(reader,source,day)
                old_population,_=arte_source.population(reader,prior,previous)
                old_ids={row['ticker']:row['listing_id'] for row in old_population}
                rules=source['definition']['plan']['rules']
                if not rules or digest(rules)!=source['definition']['rules_hash']:
                    raise ValueError('Archived trade conditions differ from certified build')
                attempts=broker_attempts(source,args.ledger,day,set(source['units'][str(day)]))
            finally: reader.close()
            selected=[row for row in population if not args.tickers or row['ticker'] in args.tickers]
            if args.tickers and set(args.tickers)-{r['ticker'] for r in selected}:
                raise ValueError('Canary ticker absent from frozen population')
            packets=[]
            folder=output/str(day); folder.mkdir(parents=True,exist_ok=True)
            for listing in selected:
                ticker=listing['ticker']
                old=prior
                if old_ids.get(ticker)!=listing['listing_id']:
                    old={**prior,'units':{str(previous):{}}}
                tier=tiers.get(ticker,2 if args.tier2_scenario else None)
                scenario='supplied_historical_map' if ticker in tiers else 'explicit_tier2_scenario'
                current_slice={k:source[k] for k in ('build_id','definition_hash')}
                current_slice['units']={str(day):{ticker:source['units'][str(day)][ticker]}}
                prior_slice={k:prior[k] for k in ('build_id','definition_hash')}
                prior_slice['units']={str(previous):{ticker:old['units'][str(previous)][ticker]}
                                     if ticker in old['units'][str(previous)] else {}}
                packets.append((day,previous,listing,current_slice,prior_slice,attempts[ticker],rules,folder,tier,scenario))
            reports=[]
            _write(output/'progress.json',{'completed':days,'failed':[], 'active':str(day)})
            # Bound futures as well as arrays; fail without admitting the
            # remaining market after the first broken source/contract.
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                try:
                    for report in _bounded(pool,packets,args.workers):
                        reports.append(report)
                        if len(reports)%100==0 or len(reports)==len(packets):
                            print(json.dumps({'day':str(day),'completed':len(reports),'total':len(packets)}),flush=True)
                            _write(output/'progress.json',{'completed':days,'failed':[],
                                'active':str(day),'listings_completed':len(reports),'listings_total':len(packets)})
                except Exception as error:
                    _write(output/'progress.json',{'completed':days,'failed':[str(day)],
                        'active':None,'error':str(error)})
                    raise
            cert={'version':VERSION,'status':'complete','day':str(day),'regular_end_us':_midnight_us(day)+57600_000_000,
                'source_build_id':source['build_id'],'source_definition_hash':source['definition_hash'],
                'population_hash':pop_proof['snapshot_hash'],'canary_only':bool(args.tickers),
                'files':reports,'tier_map_sha256':file_hash(args.tier_map) if args.tier_map else None,
                'unknown_prior_close':sum(r['report'].get('unknown_prior_close',False) for r in reports),
                'modeled_pauses':sum(r['report']['modeled_pauses'] for r in reports),
                'execution_consistency_audit':'passed' if all(
                    not r['report'].get('trade_buckets_during_modeled_pause',0) for r in reports) else 'failed',
                'official_halt_evidence':False}
            _write(folder/'complete.json',cert)
            days.append(str(day)); previous=day
            _write(output/'progress.json',{'completed':days,'failed':[], 'active':None})
    return 0


def open_sidecar(root, day, source):
    folder=Path(root)/str(day)
    cert=json.loads((folder/'complete.json').read_text())
    if (cert['version']!=VERSION or cert['status']!='complete' or cert['canary_only'] or
            cert['source_build_id']!=source['build_id'] or
            cert['source_definition_hash']!=source['definition_hash']):
        raise ValueError('Uncertified, canary, or wrong-source LULD sidecar')
    if cert.get('execution_consistency_audit')!='passed':
        raise ValueError('Modeled halt conflicts with observed eligible trades; investigate before training')
    frames={}
    for item in cert['files']:
        path=folder/item['file']
        if file_hash(path)!=item['sha256']: raise ValueError('LULD sidecar hash mismatch')
        frames[item['contract']['ticker']]=pl.read_parquet(path)
    return LuldBook(frames,end_us=cert['regular_end_us']),file_hash(folder/'complete.json')


if __name__=='__main__':
    raise SystemExit(main())
