"""Resume V7 history and persist PM/regular streaming states in normalized ARTE.

Requires a completed bar-funnel runtime. Defaults to 2026-01-01 through the
latest certified canonical day. Plan-only performs no schema or data writes.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
from contextlib import closing, ExitStack
from datetime import date, datetime, timedelta, timezone
import json
import sqlite3
from hashlib import sha256

from scripts import build_market_day as builder
from research.level_book.v7.campaign import RULE_SQL
from research.level_book.v7 import campaign_source as source
from research.level_book.v7.clickhouse_persistence import (
    LEVELS_TABLE, OBSERVATIONS_TABLE, COVERAGE_TABLE, compact_checkpoints,
    datetime64_ns, epoch_ns,
)
from pipelines.market_sip.events.structural_session_checkpoints import VERSION, calculate, source_rows, input_bars
from pipelines.market_sip.events import structural_checkpoint_storage as storage
from pipelines.market_sip.events.causal_v7_product import input_bar
from src.backend.structural_v7_seed import load_seed, split_evidence, COVERAGE_HEADER_COLUMNS
from src.market_engine.historical_level_checkpoint import digest


def arguments(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bars-run-dir', type=Path, required=True)
    p.add_argument('--run-dir', type=Path, required=True)
    p.add_argument('--start-date', type=date.fromisoformat, default=date(2026, 1, 1))
    p.add_argument('--end-date', type=date.fromisoformat)
    p.add_argument('--env-file', type=Path, default=Path('D:/TradingML/secrets/.env'))
    p.add_argument('--endpoint', default='http://192.168.1.218:18123')
    p.add_argument('--plan-only', action='store_true')
    p.add_argument('--profile-only', action='store_true', help='Read-only real streaming profile for three certified ticker-days')
    p.add_argument('--resume', action='store_true')
    p.add_argument('--writer-compatibility-proof', type=Path,
                   help='Reviewed exact-float transport deployment proof preserving the frozen calculation request')
    p.add_argument('--migrate-schema', action='store_true', help='Apply additive boundary keys before writing')
    p.add_argument('--workers',type=int,choices=range(1,65),default=8)
    a = p.parse_args(argv)
    if a.end_date and a.end_date < a.start_date:
        p.error('End date precedes start date')
    return a


def profile(client, a, report, selected, closes):
    """Bounded real source/stream/serialize measurements before the campaign."""
    from time import perf_counter
    from types import SimpleNamespace
    from pipelines.market_sip.events.structural_session_checkpoints import normalized_boundary
    from src.backend.structural_v7_seed import preceding_coverage
    candidates = [name for name in ('AAPL', 'MSFT', 'IBM') if any(t == name for _, t in selected)]
    if not candidates:
        candidates = sorted({ticker for _, ticker in selected})[:3]
    results = []
    with closing(sqlite3.connect((a.bars_run_dir/'build-ledger-v2.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory = sqlite3.Row
        for ticker in candidates:
            day = min(d for d,t in selected if t==ticker)
            receipt = db.execute("SELECT * FROM units WHERE build_id=? AND session_date=? AND ticker=? "
                "AND stage='bars' AND status='complete'", (report['build_id'],day,ticker)).fetchone()
            if receipt is None:raise ValueError('Profile requires a completed bar receipt')
            unit = SimpleNamespace(**dict(receipt)); started=perf_counter()
            proof=builder.evidence(client,'arte','bars',unit.build_id,day,ticker,unit.attempt_id)
            if int(proof['n'])!=unit.output_rows or str(proof['hash'])!=unit.output_hash:
                raise ValueError('Profile source differs from certified bars')
            seed=preceding_coverage(client.http,ticker=ticker,session=date.fromisoformat(day))
            prior=load_seed(client.http,ticker=ticker,session=date.fromisoformat(day),coverage=seed)
            actions=split_evidence(client.http,ticker=ticker,seed_session=date.fromisoformat(prior['session']),session=date.fromisoformat(day))
            bars=input_bars(date.fromisoformat(day), source_rows(client,unit))
            fetched=perf_counter()
            values=calculate(prior=prior,ticker=ticker,day=day,bars=bars,actions=actions,
                regular_close=closes[day],existing_ah=True)
            calculated=perf_counter();sizes={}
            for boundary,value in values.items():
                levels,observations,coverage=normalized_boundary(value,'a'*64)
                sizes[boundary]=dict(levels=len(levels),observations=len(observations),
                    payload_bytes=len(coverage['streaming_state'].encode()))
            results.append(dict(ticker=ticker,day=day,bars=len(bars),fetch_seconds=fetched-started,
                stream_seconds=calculated-fetched,serialize_seconds=perf_counter()-calculated,boundaries=sizes))
    save_json(a.run_dir/'structural-profile.json',dict(status='complete',read_only=True,units=results))
    print(json.dumps(results),flush=True)
    return 0


def ah_coverage(client, ticker, day):
    rows = client.query(f"SELECT {COVERAGE_HEADER_COLUMNS} FROM {COVERAGE_TABLE} FINAL PREWHERE ticker={source.literal(ticker)} "
        f"AND session_date=toDate({source.literal(day)}) AND checkpoint_boundary=''")
    if len(rows)>1 or any(r['state'] not in ('complete','empty') for r in rows):
        raise ValueError('Ambiguous or incomplete existing AH checkpoint')
    return rows


def publish_ah(client, book, plan_hash):
    """Append a daily snapshot, closing original open AH intervals in place.

    Original source hashes and checkpoint lineage are retained in closed rows.
    A restart may repeat these deterministic writes before coverage is exposed.
    """
    ticker = book['ticker']; stamp = datetime64_ns(epoch_ns(book['available_at']))
    for table in (LEVELS_TABLE, OBSERVATIONS_TABLE):
        from research.level_book.v7.clickhouse_persistence import normalized_row_pages
        identity='level_id' if table==LEVELS_TABLE else 'observation_id'
        for opened in normalized_row_pages(client.query,table=table,
                prewhere=f"ticker={source.literal(ticker)} AND checkpoint_boundary=''",
                predicate='isNull(valid_to)',identity=identity):
            for row in opened:
                row.update(valid_to=stamp,row_revision=epoch_ns(book['available_at']))
            storage.insert(client,table,opened)
    levels, observations, coverage = compact_checkpoints([book], plan_hash)
    storage.insert(client, LEVELS_TABLE, levels)
    storage.insert(client, OBSERVATIONS_TABLE, observations)
    for table, expected in ((LEVELS_TABLE, len(levels)), (OBSERVATIONS_TABLE, len(observations))):
        rows = client.query(f"SELECT count() n FROM {table} FINAL PREWHERE ticker={source.literal(ticker)} "
            f"AND checkpoint_boundary='' AND valid_from=toDateTime64({source.literal(stamp)},9,'UTC')")
        if int(rows[0]['n']) != expected:
            raise ValueError('AH normalized snapshot read-back count differs')
    coverage[0]['published_at'] = datetime64_ns(epoch_ns(datetime.now(timezone.utc).timestamp()))
    storage.insert(client, COVERAGE_TABLE, coverage)
    storage.storage_preflight(client)



def require_parent(owner):
    import psutil
    try:
        process=psutil.Process(owner['pid'])
        valid=process.is_running() and process.create_time()==owner['created']
    except psutil.NoSuchProcess:
        valid=False
    if not valid:raise RuntimeError('Structural owner exited; drain before another campaign resumes')


def ticker_file(root,ticker,suffix):
    return root/'workers'/(sha256(ticker.encode()).hexdigest()+suffix)


def save_json(path, value):
    """Retain atomic/fsynced writes across brief Windows reader sharing races."""
    import time
    for attempt in range(7):
        try:
            return builder.save(path,value)
        except PermissionError as error:
            if getattr(error,'winerror',None) not in (5,32,33) or attempt==6:
                raise
            time.sleep(.05*2**attempt)


def read_json(path):
    """Tolerate brief Windows replacement/sharing races, not persistent IO loss."""
    import time
    for attempt in range(6):
        try:return json.loads(path.read_text())
        except (OSError,json.JSONDecodeError):
            if attempt==5:raise
            time.sleep(.05*2**attempt)


def compatible_writer_request(current, frozen, proof):
    """Pin both executable identities while retaining completed calculation lineage."""
    allowed = {'pipelines/market_sip/events/structural_checkpoint_storage.py',
               'scripts/backfill_structural_sessions.py'}
    if proof.get('version') in ('structural-campaign-seed-v2','structural-projection-perf-v3'):
        allowed.add('src/backend/structural_v7_seed.py')
    if proof.get('version')=='structural-projection-perf-v3':
        allowed.add('pipelines/market_sip/events/structural_session_checkpoints.py')
    if proof.get('version')=='structural-core-perf-v4':
        allowed.update({'src/backend/structural_v7_seed.py',
            'pipelines/market_sip/events/structural_session_checkpoints.py',
            'src/market_engine/streaming_level_book.py',
            'research/level_book/v7/clickhouse_persistence.py'})
    old = frozen['source_hashes']; new = current['source_hashes']
    changed = {key.replace('\\', '/') for key in old.keys() | new.keys()
               if old.get(key) != new.get(key)}
    if (proof.get('version') not in ('structural-exact-float-transport-v1','structural-campaign-seed-v2','structural-projection-perf-v3','structural-core-perf-v4')
            or proof.get('previous_request_hash') != digest(frozen)
            or proof.get('current_request_hash') != digest(current)
            or not changed or not changed <= allowed
            or {k:v for k,v in current.items() if k != 'source_hashes'} !=
               {k:v for k,v in frozen.items() if k != 'source_hashes'}):
        raise ValueError('Writer compatibility proof does not match the frozen calculation and executable')
    return frozen


def expected_prior_hash(prior, ticker, day, regular_close):
    if prior is not None:
        return prior['checkpoint_hash']
    # Reuse the calculator's existing deterministic genesis contract. A first
    # listing day has no preceding AH; its completed boundaries still reuse.
    return calculate(prior=None, ticker=ticker, day=day, bars=[], actions=[],
                     regular_close=regular_close, existing_ah=True)['PM']['prior_hash']


def empty_ah_path(root,ticker,day):
    return root/'empty-ah'/sha256(ticker.encode()).hexdigest()/(day+'.json')


def _ticker_worker(spec_path, ticker):
    from types import SimpleNamespace
    import pandas_market_calendars as mcal
    from pipelines.certified_research.source import inventory,reporting_inventory,require_ready
    import psutil
    from time import perf_counter
    global _SPEC_CACHE
    if _SPEC_CACHE is None:
        _SPEC_CACHE=json.loads(Path(spec_path).read_text())
    spec=_SPEC_CACHE
    require_parent(spec['owner'])
    a=SimpleNamespace(**spec['arguments'])
    a.run_dir=Path(a.run_dir);a.bars_run_dir=Path(a.bars_run_dir);a.env_file=Path(a.env_file)
    os.environ['QMD_CLICKHOUSE_URL']=a.endpoint
    lease=a.run_dir/'workers'/('lease-'+str(os.getpid())+'.json')
    save_lease=dict(pid=os.getpid(),created=psutil.Process().create_time(),owner=spec['owner'])
    save_json(lease,save_lease)
    with builder.build_lock(ticker_file(a.run_dir,ticker,'.lock')):
        try:return _process_ticker(spec,a,ticker)
        except Exception as error:
            # Some HTTP exceptions require multiple constructor arguments and
            # cannot cross multiprocessing's pickle boundary. Retain the real
            # server reason instead of destroying the whole pool on unpickle.
            progress_path=ticker_file(a.run_dir,ticker,'.json')
            try:progress=read_json(progress_path) if progress_path.exists() else {}
            except (OSError,ValueError):progress={}
            context=f"{progress.get('active',ticker)} [{progress.get('stage','starting')}]"
            raise RuntimeError(f'{context}: {type(error).__name__}: {error}') from None
        finally:lease.unlink(missing_ok=True)


def _process_ticker(spec,a,ticker):
    from types import SimpleNamespace
    from pipelines.certified_research.source import inventory,reporting_inventory,require_ready
    from time import perf_counter
    request=spec['request'];history_inventory=spec['history_inventory']
    reporting=spec['reporting'];rules=spec['rules'];by_ticker=spec['by_ticker']
    selected={(day,ticker) for day in by_ticker[ticker]}
    plan_hash=spec['plan_hash'];producer_hash=spec['producer_hash']
    history_start=date.fromisoformat(spec['history_start']);end=date.fromisoformat(spec['end'])
    closes={day:datetime.fromisoformat(value) for day,value in spec['closes'].items()}
    report=dict(build_id=spec['build_id'])
    done=reused=0;started=perf_counter()
    options=builder.parse_args(['--date',str(a.start_date),'--env-file',str(a.env_file),
                               '--max-threads','1','--max-memory-gb','2'])
    with closing(builder.Client(options)) as client, closing(sqlite3.connect(
            (a.bars_run_dir/'build-ledger-v2.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        client.http.structural_stream_reads = True
        first_target, last_target = min(by_ticker[ticker]), max(by_ticker[ticker])
        known_ah = client.query(f"SELECT {COVERAGE_HEADER_COLUMNS} FROM {COVERAGE_TABLE} FINAL PREWHERE ticker={source.literal(ticker)} "
            f"AND checkpoint_boundary='' AND session_date<={source.literal(last_target)} ORDER BY session_date")
        ah_by_day = {str(row['session_date']): row for row in known_ah}
        if len(ah_by_day) != len(known_ah) or any(row['state'] not in ('complete','empty') for row in known_ah):
            raise ValueError('Existing AH coverage is ambiguous or incomplete')
        preceding = [day for day in ah_by_day if day < first_target]
        replay_start = max(preceding) if preceding else str(history_start-timedelta(days=1))
        metadata = client.query('SELECT * FROM market_sip_compact.events_ordinal_continuity FINAL '
            f'WHERE ticker={source.literal(ticker)} AND source_date>{source.literal(replay_start)} '
            f'AND source_date<={source.literal(last_target)} ORDER BY source_date')
        if client.query(source.reporting_coverage_sql(str(history_start), str(end))) != reporting:
            raise ValueError('Reporting certificates changed during structural calculation')
        if digest(client.query(RULE_SQL)) != request['rules_hash']:
            raise ValueError('Canonical condition rules changed during structural calculation')
        profile_totals=dict(context=0.,fetch=0.,calculate=0.,publish=0.,reuse_verify=0.,total=0.)
        last_profile={}
        def progress(day, stage, source_bars=0):
            save_json(ticker_file(a.run_dir,ticker,'.json'),dict(owner=spec['owner']['token'],
                status='running',active=f'{day} {ticker}',stage=stage,completed=done,reused=reused,
                total=len(selected),failed=0,queued=len(selected)-done-reused,
                profile_last=last_profile,profile_totals=profile_totals,
                source_bars=source_bars,query_profiles=client.profile_totals))
        for meta in metadata:
            unit_started=perf_counter()
            require_parent(spec['owner'])
            day = str(meta['source_date'])
            target = (day, ticker) in selected
            exists = ah_by_day.get(day)
            if exists and not target:
                continue
            progress(day,'checking source')
            source.require_reporting_coverage([day], reporting)
            from pipelines.certified_research.source_guard_batch import RoundQuery
            source_query = RoundQuery(client.query, (date.fromisoformat(day),))
            certified_day = inventory(source_query, date.fromisoformat(day), date.fromisoformat(day),
                                      sessions=(date.fromisoformat(day),))
            if certified_day['missing'] or certified_day['incomplete']:
                raise ValueError('Required historical replay day is not canonical-certified')
            if certified_day['source_hashes'].get(day) != history_inventory['source_hashes'].get(day):
                raise ValueError('Canonical replay source changed after the frozen plan')
            require_ready(source_query,certified_day,reporting_inventory(source_query,certified_day))
            if (a.run_dir / 'STOP').exists():
                raise KeyboardInterrupt
            prior_days = [previous for previous in ah_by_day if previous < day]
            prior_rows = [ah_by_day[max(prior_days)]] if prior_days else []
            prior = load_seed(client.http, ticker=ticker, session=date.fromisoformat(day),
                              coverage=prior_rows[0],campaign_plan_hash=plan_hash) if prior_rows else None
            # This also validates causal split evidence. Retain it on reuse;
            # it is a prerequisite check even when no calculation is required.
            actions = split_evidence(client.http, ticker=ticker,
                seed_session=date.fromisoformat(prior['session']) if prior else history_start-timedelta(days=1),
                session=date.fromisoformat(day))
            current = storage.read_boundaries(client, ticker, day) if target else []
            unit = None
            source_ref = None
            if target:
                receipt = db.execute("SELECT * FROM units WHERE build_id=? AND session_date=? AND ticker=? "
                    "AND stage='bars' AND status='complete'", (report['build_id'], day, ticker)).fetchone()
                if receipt is None:
                    raise ValueError(f'Missing certified bar receipt: {day} {ticker}')
                unit = SimpleNamespace(**dict(receipt))
                source_ref = dict(build_id=unit.build_id, attempt_id=unit.attempt_id,
                    source_hash=unit.source_hash, output_hash=unit.output_hash,
                    canonical_hash=history_inventory['source_hashes'][day],
                    producer_hash=producer_hash)
            if exists and {r['checkpoint_boundary'] for r in current} == {'PM', 'REGULAR'}:
                context_done=perf_counter()
                progress(day,'verifying reuse')
                prior_hash=expected_prior_hash(prior,ticker,day,closes[day])
                verify_reuse(client,current,source_ref,prior_hash)
                verified=perf_counter()
                last_profile=dict(context=context_done-unit_started,reuse_verify=verified-context_done,
                                  total=verified-unit_started)
                for key,value in last_profile.items():profile_totals[key]+=value
                reused += 1
                progress(day,'reuse verified')
                continue
            progress(day,'calculating')
            context_done=perf_counter()
            bars = []
            if target:
                proof = builder.evidence(client, 'arte', 'bars', unit.build_id, day, ticker, unit.attempt_id)
                if int(proof['n']) != unit.output_rows or str(proof['hash']) != unit.output_hash:
                    raise ValueError('Persisted bars differ from certified output receipt')
                bars = input_bars(date.fromisoformat(day), source_rows(client, unit))
            else:
                bars, _ = source.decode(client.query(source.bars_sql(ticker, day, rules, meta)))
            historical_bars = None
            empty_history = False
            empty_receipt = None
            if target and not exists:
                historical_bars, audit = source.decode(client.query(source.bars_sql(ticker, day, rules, meta)))
                empty_history = not historical_bars
                if empty_history:
                    # Match the historical campaign's empty-day contract: keep
                    # the last real AH seed; never fit/synthesize one without
                    # eligible prices. Canonical/reporting guards above still
                    # apply, and PM/REGULAR retain the normal causal engine.
                    empty_receipt = dict(ticker=ticker,day=day,state='empty',
                        reason='no eligible price seconds',source=source_ref,
                        metadata=meta,audit=audit,plan_hash=plan_hash,
                        prior_hash=expected_prior_hash(prior,ticker,day,closes[day]))
            elif not target and not exists and not bars:
                empty_history=True
                empty_receipt=dict(ticker=ticker,day=day,state='empty',
                    reason='no eligible price seconds',source=None,metadata=meta,
                    plan_hash=plan_hash,prior_hash=expected_prior_hash(prior,ticker,day,closes[day]))
            empty_reuse=False
            if empty_receipt is not None:
                empty_path=empty_ah_path(a.run_dir,ticker,day)
                empty_reuse=empty_path.exists()
                if empty_reuse and read_json(empty_path)!=empty_receipt:
                    raise ValueError('Empty AH source/parent differs from frozen receipt')
            fetched=perf_counter()
            values = calculate(prior=prior, ticker=ticker, day=day, bars=bars,
                actions=actions, regular_close=closes[day], existing_ah=bool(exists) or empty_history,
                persist_intraday=target, historical_bars=historical_bars)
            calculated=perf_counter()
            progress(day,'publishing',len(bars))
            # Expose missing AH first so legacy prior-day readers can never
            # mistake a newly published regular snapshot for the day's AH.
            if 'AH' in values:
                progress(day,'publishing AH',len(bars))
                publish_ah(client, values['AH'], plan_hash)
                ah_by_day[day] = ah_coverage(client, ticker, day)[0]
            progress(day,'publishing PM/REGULAR',len(bars))
            for boundary in ('PM', 'REGULAR'):
                if boundary in values:
                    values[boundary]['source'] = source_ref
                    storage.publish_boundary(client, values[boundary], plan_hash)
            if empty_receipt is not None:
                empty_path.parent.mkdir(parents=True,exist_ok=True)
                save_json(empty_path,empty_receipt)
            published=perf_counter()
            timings=dict(context=context_done-unit_started,fetch=fetched-context_done,
                calculate=calculated-fetched,publish=published-calculated,total=published-unit_started)
            for key,value in timings.items():profile_totals[key]+=value
            done += int(target and not empty_reuse)
            reused += int(target and empty_reuse)
            last_profile=timings
            progress(day,'verified',len(bars))
            print(f'{done+reused}/{len(selected)} | completed {done} | reused {reused} | failed 0 | {day} {ticker}', flush=True)

        final_ah = max((day for day in ah_by_day if day<=last_target),default=None)
        if last_target not in ah_by_day and not empty_ah_path(a.run_dir,ticker,last_target).exists():
            raise ValueError('Final target day has no completed AH checkpoint')
        if final_ah is not None:
            load_seed(client.http,ticker=ticker,session=date.fromisoformat(last_target)+timedelta(days=1),
                      coverage=ah_by_day[final_ah],campaign_plan_hash=plan_hash)

    result=dict(owner=spec['owner']['token'],status='complete',ticker=ticker,
                completed=done,reused=reused,seconds=perf_counter()-started,
                profile_totals=profile_totals,query_profiles=client.profile_totals)
    save_json(ticker_file(a.run_dir,ticker,'.json'),result)
    return result


_SPEC_CACHE=None


def warmup_dependency_plan(query, by_ticker, history_start, certified_days, *, batch_size=256):
    """Check actual pre-target replay frontiers using metadata only, before admission.

    A later checkpoint cannot seed an earlier target. A new listing with no
    earlier canonical rows needs no artificial year-long warm-up.
    """
    if not 1 <= batch_size <= 256:raise ValueError('Invalid dependency batch size')
    tickers=sorted(by_ticker);blocked={}
    certified=','.join(source.literal(str(day)) for day in sorted(certified_days)) or "''"
    for offset in range(0,len(tickers),batch_size):
        batch=tickers[offset:offset+batch_size]
        names='['+','.join(map(source.literal,batch))+']'
        firsts='['+','.join(source.literal(min(by_ticker[t])) for t in batch)+']'
        seeds=query(f"SELECT ticker,toString(maxIf(session_date,session_date<"
            f"toDate(transform(ticker,{names},{firsts},'1970-01-01')))) AS seed "
            f"FROM {COVERAGE_TABLE} FINAL PREWHERE ticker IN ({','.join(map(source.literal,batch))}) "
            "AND checkpoint_boundary='' WHERE state IN ('complete','empty') GROUP BY ticker")
        seed_by_ticker={row['ticker']:row['seed'] for row in seeds}
        frontiers='['+','.join(source.literal(max(str(history_start-timedelta(days=1)),
            seed_by_ticker.get(t,'1970-01-01'))) for t in batch)+']'
        gaps=query("SELECT ticker,count() AS missing_days,toString(min(source_date)) AS first_missing,"
            "groupArray(8)(toString(source_date)) AS examples "
            "FROM market_sip_compact.events_ordinal_continuity FINAL "
            f"PREWHERE ticker IN ({','.join(map(source.literal,batch))}) "
            f"WHERE source_date>toDate(transform(ticker,{names},{frontiers},'1970-01-01')) "
            f"AND source_date<toDate(transform(ticker,{names},{firsts},'1970-01-01')) "
            f"AND source_date NOT IN ({certified}) GROUP BY ticker ORDER BY ticker")
        for row in gaps:
            ticker=row['ticker']
            blocked[ticker]=dict(ticker=ticker,reason='uncertified_canonical_or_reporting_warmup',
                first_target=min(by_ticker[ticker]),prior_ah=seed_by_ticker.get(ticker),
                first_missing=row['first_missing'],missing_days=int(row['missing_days']),
                examples=sorted(row['examples']),target_units=len(by_ticker[ticker]))
    return dict(version='structural-warmup-dependencies-v1',blocked=blocked,
        blocked_tickers=len(blocked),blocked_units=sum(row['target_units'] for row in blocked.values()),
        ready_tickers=len(tickers)-len(blocked),
        ready_units=sum(len(days) for ticker,days in by_ticker.items() if ticker not in blocked))


def verify_reuse(client, rows, source_ref, prior_hash):
    """Restart the entire read-only audit once after a broken transport.

    Nothing is published or counted until both boundaries pass. Never continue
    a partially consumed iterator, retry writes, or catch integrity errors.
    """
    from http.client import IncompleteRead
    from urllib.error import URLError, HTTPError
    for attempt in range(2):
        try:
            for row in rows:
                storage.verify_reused_boundary(client,row,expected_source=source_ref,
                                               expected_prior_hash=prior_hash)
            return
        except (ConnectionError, TimeoutError, IncompleteRead, URLError) as error:
            if isinstance(error, HTTPError) or attempt:
                raise


def admission_plan(query, by_ticker, *, batch_size=32):
    """Prioritize missing output; coverage headers are hints, never reuse proof.

    All tickers still execute the full source, AH, payload and normalized-row
    checks. Restrict hints to exact requested dates, and fetch no state payload.
    """
    if not 1 <= batch_size <= 32:
        raise ValueError('Invalid admission metadata batch size')
    tickers=sorted(by_ticker)
    paired={ticker:set() for ticker in tickers}
    for offset in range(0,len(tickers),batch_size):
        batch=tickers[offset:offset+batch_size]
        first=min(min(by_ticker[t]) for t in batch)
        last=max(max(by_ticker[t]) for t in batch)
        rows=query(f"SELECT ticker,toString(session_date) AS day,"
            "groupUniqArray(checkpoint_boundary) AS boundaries "
            f"FROM {COVERAGE_TABLE} FINAL PREWHERE ticker IN "
            f"({','.join(map(source.literal,batch))}) "
            f"AND session_date BETWEEN {source.literal(first)} AND {source.literal(last)} "
            "AND checkpoint_boundary IN ('PM','REGULAR') "
            "GROUP BY ticker,session_date")
        for row in rows:
            ticker=row['ticker'];day=row['day']
            if ticker not in batch:
                raise ValueError('Admission metadata returned an unrelated ticker')
            if day in by_ticker[ticker] and set(row['boundaries'])=={'PM','REGULAR'}:
                paired[ticker].add(day)
    # Completely unbuilt tickers first, partial lanes next, audit-only last.
    # Chronological replay within each ticker is unchanged.
    order=sorted(tickers,key=lambda t:(
        bool(paired[t]), len(paired[t])/len(by_ticker[t]), t))
    return dict(version='structural-admission-v1',order=order,
        persisted_pair_hints=sum(map(len,paired.values())),
        audit_only_tickers=sum(len(paired[t])==len(by_ticker[t]) for t in tickers),
        missing_pair_hints=sum(len(by_ticker[t])-len(paired[t]) for t in tickers),
        integrity_verified=False)


def run_pool(a,request,build_id,by_ticker,history_inventory,reporting,rules,
             closes,history_start,end,plan_hash,producer_hash,total,dependencies=None,
             admission=None):
    """Bounded ticker lanes; each lane retains chronological AH ownership."""
    from concurrent.futures import ProcessPoolExecutor,wait,FIRST_COMPLETED
    import multiprocessing
    import psutil
    from uuid import uuid4
    workers=a.run_dir/'workers';workers.mkdir(exist_ok=True)
    if (a.run_dir/'STOP').exists():raise KeyboardInterrupt
    # A crashed parent releases its OS lock before children finish their current
    # unit. Refuse a new pool until those exact process identities have drained.
    for lease in workers.glob('lease-*.json'):
        old=json.loads(lease.read_text())
        try:alive=psutil.Process(old['pid']).create_time()==old['created']
        except psutil.NoSuchProcess:alive=False
        if alive:raise ValueError('Prior structural worker is still draining; preserve receipts and retry later')
    owner=dict(pid=os.getpid(),created=psutil.Process().create_time(),token=uuid4().hex)
    spec=dict(owner=owner,arguments={key:str(value) if isinstance(value,(Path,date)) else value
            for key,value in vars(a).items()},request=request,build_id=build_id,
        by_ticker=by_ticker,history_inventory=dict(source_hashes=history_inventory['source_hashes']),
        reporting=reporting,rules=rules,closes={day:value.isoformat() for day,value in closes.items()},
        history_start=str(history_start),end=str(end),plan_hash=plan_hash,producer_hash=producer_hash)
    path=a.run_dir/('worker-spec-'+owner['token']+'.json');save_json(path,spec)
    order=admission['order'] if admission is not None else sorted(by_ticker)
    if len(order)!=len(by_ticker) or set(order)!=set(by_ticker):
        raise ValueError('Admission must contain every requested ticker exactly once')
    iterator=iter(order);pending={};results=[]
    # Windows limits one ProcessPoolExecutor to 61 handles. Two bounded pools
    # provide the requested 64 lanes without exceeding that OS wait-set limit.
    sizes=[min(32,a.workers-i) for i in range(0,a.workers,32)]
    with ExitStack() as stack:
        pools=[stack.enter_context(ProcessPoolExecutor(max_workers=size,
               mp_context=multiprocessing.get_context('spawn'))) for size in sizes]
        loads=[0]*len(pools);assigned={}
        def admit():
            while len(pending)<a.workers and not (a.run_dir/'STOP').exists():
                ticker=next(iterator,None)
                if ticker is None:break
                index=min((i for i in range(len(pools)) if loads[i]<sizes[i]),
                          key=lambda i:loads[i]/sizes[i])
                future=pools[index].submit(_ticker_worker,str(path),ticker)
                pending[future]=ticker;assigned[future]=index;loads[index]+=1
        try:
            admit()
            while pending:
                ready,_=wait(pending,timeout=2,return_when=FIRST_COMPLETED)
                for future in ready:
                    ticker=pending.pop(future);loads[assigned.pop(future)]-=1
                    try:results.append(future.result())
                    except BaseException as error:
                        if (a.run_dir/'STOP').exists():raise KeyboardInterrupt from error
                        raise RuntimeError(f'Structural ticker {ticker} failed: {error}') from error
                admit()
                completed=sum(row['completed'] for row in results)
                reused=sum(row['reused'] for row in results)
                active=[]
                for ticker in pending.values():
                    progress=ticker_file(a.run_dir,ticker,'.json')
                    if progress.exists():
                        row=read_json(progress)
                        if row.get('owner')==owner['token']:
                            completed+=row['completed'];reused+=row['reused'];active.append(row)
                save_json(a.run_dir/'status.json',dict(status='running',completed=completed,reused=reused,
                    total=total,failed=0,queued=total-completed-reused,workers=active,
                    blocked_units=(dependencies or {}).get('blocked_units',0),
                    blocked_tickers=(dependencies or {}).get('blocked_tickers',0),
                    worker_limit=a.workers,query_threads=1,query_memory_gib=2))
        except BaseException:
            (a.run_dir/'STOP').touch()
            for future in pending:future.cancel()
            raise
    return results

def run(a):
    import pandas_market_calendars as mcal
    from pipelines.certified_research.source import inventory,reporting_inventory,require_ready
    from types import SimpleNamespace
    os.environ['QMD_CLICKHOUSE_URL'] = a.endpoint
    runtime = Path('D:/TradingML/runtimes').resolve()
    if (not runtime.is_dir() or not a.run_dir.parent.is_dir()
            or not a.run_dir.resolve().is_relative_to(runtime)):
        raise ValueError('Required runtime parent is unavailable')
    if not (a.plan_only or a.profile_only) and os.environ.get('COMPUTERNAME','').upper()!='DESKTOP-SAAI85T':
        raise ValueError('Execute structural writers on DESKTOP-SAAI85T')
    options = builder.parse_args(['--date', str(a.start_date), '--env-file', str(a.env_file),
                                  '--max-threads', '1', '--max-memory-gb', '2'])
    with closing(builder.Client(options, readonly=a.plan_only or a.profile_only)) as client:
        latest = client.query('SELECT max(source_date) day FROM market_sip_compact.events_source_day_stats FINAL')[0]['day']
        frozen=a.run_dir/'structural-request.json'
        end = (date.fromisoformat(read_json(frozen)['end']) if a.resume and a.end_date is None and frozen.exists()
               else a.end_date or date.fromisoformat(latest))
        inv = inventory(client.query, a.start_date, end)
        if inv['missing'] or inv['incomplete']:
            raise ValueError('Structural target range has uncertified canonical source days')
        require_ready(client.query,inv,reporting_inventory(client.query,inv))
        report = json.loads((a.bars_run_dir / 'build' / 'latest.json').read_text())
        if report['status'] != 'core_complete' and not a.profile_only:
            raise ValueError('Structural backfill waits for completed bar-funnel certification')
        units = report['definition']['plan']['units']
        selected = {(r['source_date'], r['ticker']) for r in units if str(a.start_date) <= r['source_date'] <= str(end)}
        expected_days = {str(day) for day in inv['expected']}
        if {day for day, _ in selected} != expected_days:
            raise ValueError('Completed bars do not cover the structural target range')
        history_start = min(date(2025, 1, 1), a.start_date)
        history_inventory = inventory(client.query, history_start, end)
        reporting = client.query(source.reporting_coverage_sql(str(history_start), str(end)))
        import src.market_engine.streaming_level_book as kernel
        import research.level_book.v7.campaign as retrospective
        import research.level_book.v7.clickhouse_persistence as normalized
        import src.backend.structural_v7_seed as seeds
        request = dict(version=VERSION, start=str(a.start_date), end=str(end),
            build_id=report['build_id'], bar_definition=digest(report['definition']),
            population_hash=digest(sorted(selected)), canonical=dict(content_hash=inv['content_hash'],
                source_hashes=history_inventory['source_hashes'], import_statuses=inv['import_statuses']),
            reporting=reporting, rules_hash=digest(client.query(RULE_SQL)),
            source_hashes={str(Path(module.__file__).relative_to(Path(__file__).resolve().parents[1])):
                sha256(Path(module.__file__).read_bytes()).hexdigest()
                for module in (storage, sys.modules[calculate.__module__], kernel, retrospective,
                               normalized, seeds, source, sys.modules[__name__])})
        # Canonical inventory contains dates; freeze its JSON representation.
        request = json.loads(json.dumps(request, default=str))
        if a.writer_compatibility_proof:
            if not a.resume or not frozen.exists():
                raise ValueError('Writer compatibility requires an existing resumable request')
            request = compatible_writer_request(request, read_json(frozen),
                                                read_json(a.writer_compatibility_proof))
        plan_hash = digest(request)
        producer_hash = digest(request['source_hashes'])
        by_ticker = {}
        for day, ticker in selected:
            by_ticker.setdefault(ticker, []).append(day)
        history_reporting=reporting_inventory(client.query,history_inventory)
        if history_reporting['mutating']:
            raise ValueError('Historical warm-up source has overlapping mutations')
        canonical_ready={str(day) for day in history_inventory['expected']
            if day not in history_inventory['missing'] and day not in history_inventory['incomplete']}
        dependencies=warmup_dependency_plan(client.query,by_ticker,history_start,
            canonical_ready & set(history_reporting['verified']))
        a.run_dir.mkdir(exist_ok=True)
        save_json(a.run_dir/'warmup-dependencies.json',dict(dependencies,plan_hash=plan_hash))
        save_json(a.run_dir / 'structural-plan.json', request)
        if a.plan_only:
            print(json.dumps(dict(start=str(a.start_date), end=str(end), ticker_days=len(selected),
                                  status='planned', build_id=report['build_id'],
                                  ready_tickers=dependencies['ready_tickers'],
                                  blocked_tickers=dependencies['blocked_tickers'],
                                  blocked_units=dependencies['blocked_units'])), flush=True)
            return 0
        if a.profile_only:
            schedule=mcal.get_calendar('XNYS').schedule(start_date=a.start_date,end_date=end)
            closes={str(i.date()):row.market_close.to_pydatetime() for i,row in schedule.iterrows()}
            return profile(client,a,report,selected,closes)
        with builder.build_lock(builder.RUNTIME / 'structural-session-arte.lock'):
            path = a.run_dir / 'structural-request.json'
            if path.exists():
                if not a.resume or json.loads(path.read_text()) != request:
                    raise ValueError('Structural resume requires identical frozen request')
            elif a.resume:
                raise ValueError('No structural request exists to resume')
            else:
                save_json(path, request)
            if a.migrate_schema:
                storage.migrate(client)
            storage.require_schema(client)
            rules = client.query(RULE_SQL)
            schedule = mcal.get_calendar('XNYS').schedule(start_date=history_start, end_date=end)
            closes = {str(i.date()): row.market_close.to_pydatetime() for i, row in schedule.iterrows()}
            tickers = sorted({ticker for _, ticker in selected})
            ledger_path = a.bars_run_dir / 'build-ledger-v2.sqlite3'
            db = sqlite3.connect(ledger_path.as_uri() + '?mode=ro', uri=True)
            db.row_factory = sqlite3.Row
            done = reused = 0
            try:
                ready_tickers={ticker:days for ticker,days in by_ticker.items()
                    if ticker not in dependencies['blocked']}
                admission=admission_plan(client.query,ready_tickers)
                save_json(a.run_dir/'admission-plan.json',dict(admission,plan_hash=plan_hash))
                results=run_pool(a,request,report['build_id'],ready_tickers,history_inventory,
                    reporting,rules,closes,history_start,end,plan_hash,producer_hash,len(selected),
                    dependencies,admission)
                done=sum(row['completed'] for row in results)
                reused=sum(row['reused'] for row in results)
                if dependencies['blocked_units']:
                    if done+reused!=dependencies['ready_units']:
                        raise ValueError('Ready structural unit counts do not reconcile')
                    save_json(a.run_dir/'status.json',dict(status='blocked',completed=done,reused=reused,
                        total=len(selected),failed=0,queued=dependencies['blocked_units'],
                        blocked_units=dependencies['blocked_units'],blocked_tickers=dependencies['blocked_tickers'],
                        error='Historical warm-up certification required; see warmup-dependencies.json'))
                    return 2
                if done + reused != len(selected):
                    raise ValueError('Structural completion count differs from frozen population')
            except BaseException as error:
                interrupted = isinstance(error, KeyboardInterrupt)
                status_path=a.run_dir/'status.json'
                if status_path.exists():
                    previous=json.loads(status_path.read_text())
                    done=previous.get('completed',done);reused=previous.get('reused',reused)
                save_json(a.run_dir / 'status.json', dict(status='interrupted' if interrupted else 'failed',
                    completed=done, reused=reused, total=len(selected), failed=0 if interrupted else 1,
                    error=client.clean_error(error)))
                raise
            finally:
                db.close()
            save_json(a.run_dir / 'structural-completion.json', dict(request=request,
                completed=done, reused=reused, failed=0, utc=datetime.now(timezone.utc).isoformat()))
            save_json(a.run_dir / 'status.json', dict(status='complete', completed=done,
                reused=reused, total=len(selected), failed=0, queued=0))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(run(arguments()))
    except KeyboardInterrupt:
        raise SystemExit(130)
