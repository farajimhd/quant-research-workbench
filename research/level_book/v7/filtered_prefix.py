"""Publish verified prefixes using the unchanged campaign numerical contract."""
import os
import shutil
import time
import traceback
from datetime import date

from . import campaign as c


def marker(target, before):
    return target / 'prefixes' / (date.fromisoformat(before).isoformat() + '.json')


def worker(args):
    """Extend a full-source plan only through the requested prior source session.

    Full plans and numerical/source hashes are identical to full campaign builds.
    A prefix marker never claims readiness of the unprocessed suffix.
    """
    before = date.fromisoformat(args.before).isoformat()
    root = args.runtime
    plan = c.checked_plan(root)
    row = next(r for r in plan['rows'] if r['ticker'] == args.ticker)
    if row['status'] == 'deferred':
        raise ValueError(row['reason'])
    target = c.paths(root, args.ticker)
    target.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    progress = dict(ticker=args.ticker, state='active', stage='preflight', completed=0,
                    total=0, resumed=0, empty=0, retried=0, pid=os.getpid(), before=before)

    last_publish = -float('inf')
    def publish(**values):
        nonlocal last_publish
        progress.update(values, updated_at=c.now(), elapsed_seconds=time.monotonic()-started)
        # Receipts remain immediate and durable; UI snapshots need at most 2 Hz.
        if progress['state'] == 'active' and time.monotonic()-last_publish < .5:
            return
        last_publish = time.monotonic()
        c.write(target/'progress.json', progress, immutable=False)

    def query(sql):
        for attempt in range(3):
            try:
                return c.query(sql, args.threads)
            except Exception as exc:
                transient = any(s in str(exc).lower() for s in
                    ('timed out','connection','10054','10060','temporarily','http 503','http 502'))
                if not transient or attempt == 2:
                    raise
                publish(retried=progress['retried']+1, stage='retrying query')
                time.sleep(2**attempt)

    with c.exclusive(target/'worker.lock'):
        try:
            publish()
            if query(c.coverage_sql(plan['start'],plan['end'],[args.ticker])) != [row['coverage']]:
                raise ValueError('Certified history differs from frozen coverage')
            if query(c.RULE_SQL) != plan['rules']:
                raise ValueError('Trade condition rules changed')
            predicate = f"ticker={c.literal(args.ticker)} AND source_date BETWEEN {c.literal(plan['start'])} AND {c.literal(plan['end'])}"
            days = query('SELECT * FROM market_sip_compact.events_ordinal_continuity FINAL WHERE '+predicate+' ORDER BY source_date')
            reporting_rows = query(c.reporting_coverage_sql(plan['start'],plan['end']))
            c.require_reporting_coverage([d['source_date'] for d in days],reporting_rows)
            reporting_hash = c.digest(reporting_rows)
            splits = c.canonical_splits(query(f"SELECT execution_date,split_from,split_to,inserted_at FROM q_live.market_stock_split_v1 FINAL WHERE provider_ticker={c.literal(args.ticker)} AND execution_date BETWEEN {c.literal(plan['start'])} AND {c.literal(plan['end'])} ORDER BY execution_date"))
            source = dict(days=days, splits=splits, plan_hash=plan['plan_hash'],
                          reporting_coverage_hash=reporting_hash)
            c.write(target/'source-plan.json', source)
            prefix = [d for d in days if d['source_date'] < before]
            if not prefix:
                raise ValueError('No preceding source session for '+args.ticker)
            progress['total'] = len(prefix)
            prior = None
            for metadata in prefix:
                day = metadata['source_date']
                if (root/'STOP').exists() or (getattr(args,'stop_file',None) is not None and args.stop_file.exists()):
                    raise InterruptedError('Stopped at checkpoint')
                if shutil.disk_usage(root).free < 10*1024**3:
                    raise ValueError('Runtime disk has less than 10 GiB free')
                receipt_path = target/'receipts'/f'{day}.json'
                book_path = target/'books'/f'{day}.json.gz'
                expected = c.source_hash(metadata,plan['rules'])
                parent = prior['checkpoint_hash'] if prior else None
                if receipt_path.exists():
                    receipt = c.read(receipt_path)
                    if receipt['source_hash'] != expected or receipt['parent_hash'] != parent:
                        raise ValueError('Resume source/parent hash mismatch')
                    if receipt['state'] == 'complete':
                        prior = c.verified_book(book_path)
                        if prior['checkpoint_hash'] != receipt['checkpoint_hash']:
                            raise ValueError('Resume checkpoint/receipt mismatch')
                    elif receipt['state'] == 'empty':
                        progress['empty'] += 1
                    else:
                        raise ValueError('Invalid receipt state')
                    progress['resumed'] += 1
                else:
                    publish(stage='ClickHouse OHLCV', session=day, bars_processed=0)
                    begin = time.monotonic()
                    bars,audit = c.decode(query(c.bars_sql(args.ticker,day,plan['rules'],metadata)))
                    current = query(f"SELECT * FROM market_sip_compact.events_ordinal_continuity FINAL WHERE ticker={c.literal(args.ticker)} AND source_date={c.literal(day)}")
                    if current != [metadata]:
                        raise ValueError('Canonical source changed during aggregation')
                    bar_hash = c.digest(bars)
                    receipt = dict(source_hash=expected,parent_hash=parent,bar_hash=bar_hash,
                                   bars=len(bars),audit=audit,sql_seconds=time.monotonic()-begin)
                    if not bars:
                        receipt.update(state='empty',reason='no eligible price seconds')
                        progress['empty'] += 1
                    else:
                        actions = [s for s in splits if prior and prior['session'] < s['execution_date'] <= day]
                        publish(stage='MLE fitting',bars_total=len(bars))
                        begin = time.monotonic()
                        engine = c.fit_day(prior,args.ticker,day,bars,actions)
                        last = begin
                        for i,bar in enumerate(bars):
                            engine.update(bar,observed_at=bar['t'])
                            if i % 1000 == 0 and time.monotonic()-last >= 1:
                                publish(bars_processed=i)
                                last = time.monotonic()
                        prior = engine.historical_checkpoint(bar_hash)
                        c.write(book_path,prior)
                        receipt.update(state='complete',checkpoint_hash=prior['checkpoint_hash'],
                            fit_seconds=time.monotonic()-begin,levels=sum(r['qualified'] for r in prior['levels']),
                            candidates=sum(not r['qualified'] for r in prior['levels']))
                    c.write(receipt_path,receipt)
                publish(completed=progress['completed']+1,session=day,stage='checkpoint saved')
            if query(c.coverage_sql(plan['start'],plan['end'],[args.ticker])) != [row['coverage']] or query(c.RULE_SQL) != plan['rules']:
                raise ValueError('Source or rules changed before prefix publication')
            if c.digest(query(c.reporting_coverage_sql(plan['start'],plan['end']))) != reporting_hash:
                raise ValueError('Trade-reporting coverage changed before prefix publication')
            value = dict(version=1,plan_hash=plan['plan_hash'],ticker=args.ticker,before=before,
                         sessions=len(prefix),through=prefix[-1]['source_date'],
                         source_plan_hash=c.digest(source),checkpoint_hash=prior['checkpoint_hash'] if prior else None)
            value['prefix_hash'] = c.digest(value)
            c.write(marker(target,before),value)
            publish(state='complete',stage='verified prefix')
        except BaseException as exc:
            publish(state='failed',stage='failed',reason=str(exc))
            c.write(target/'error.json',dict(at=c.now(),error=str(exc),traceback=traceback.format_exc()),immutable=False)
            raise
