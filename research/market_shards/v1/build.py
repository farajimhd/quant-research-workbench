"""Two-pass bounded scanner and session-union extractor."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from queue import Queue, Empty
from pathlib import Path
import json
import os
import sys
import time
import threading
import polars as pl
from . import sql, source
from .contract import CONTRACT, digest, write_json, sha, require_runtime, split_days
from .transport import stream
from .progress import Progress


def scanner(c, root, frozen, day, update):
    folder=root/'scanner'/day;folder.mkdir(parents=True,exist_ok=True)
    seal=folder/'complete.json'
    if seal.exists():
        saved=json.loads(seal.read_text())
        if saved['source_hash']!=frozen['hash'] or any(sha(folder/n)!=h for n,h in saved['files'].items()):
            raise ValueError('Scanner resume mismatch')
        return True
    available=source.units(root,day); previous=frozen['predecessors'][day]
    # Source metadata is small; raw quote/trade rows stay on the server.
    prior=set(source.units(root,previous)); shared=sorted(prior & available.keys())
    changed={t:source.price_factor(frozen['splits'],t,previous,day) for t in shared}
    changed={t:f for t,f in changed.items() if f!=1.}
    factors=(f"transform(ticker,[{sql.names(changed)}],[{','.join(repr(changed[t]) for t in sorted(changed))}],1.)" if changed else '1.')
    # Keep rejection counts/values reviewable; only this tiny close projection
    # leaves ClickHouse before ranking, never wide intraday bars.
    base=sql.previous_close(frozen['source_build'],previous,source.attempts(root,previous,shared),frozen['regular'][previous])
    cutoff=source.origin_us(day)+14400_000000
    gate=f"SELECT *,close*{factors} AS adjusted_close, toUInt8(price_valid=1 AND available_at_us<={cutoff} AND adjusted_close>=0.8 AND adjusted_close<=50) AS eligible FROM ({base}) ORDER BY ticker,time_us"
    update(stage='previous close',rows=0,bytes=0)
    stream(c,gate,folder/'eligibility.parquet',['ticker','time_us'],update)
    frame=pl.read_parquet(folder/'eligibility.parquet')
    tickers=frame.filter(pl.col('eligible')==1)['ticker'].to_list()
    if not tickers:
        raise ValueError('No price-eligible tickers')
    update(stage=f'ranking {len(tickers)} tickers',rows=0,bytes=0)
    stream(c,sql.ranking(frozen['source_build'],day,source.attempts(root,day,tickers)),folder/'rank.parquet',['time_us','rank'],update)
    ranked=pl.read_parquet(folder/'rank.parquet')
    selected=sorted(ranked['ticker'].unique().to_list())
    if not selected:
        raise ValueError('No positive premarket activity')
    record=dict(source_hash=frozen['hash'],day=day,selected=selected,
        counts=dict(population=len(available),prior_membership_missing=len(available)-len(shared),
            prior_close_missing=len(shared)-frame.height,price_eligible=len(tickers),
            price_rejected=frame.height-len(tickers),session_union=len(selected),rank_rows=ranked.height),
        files={n:sha(folder/n) for n in ('eligibility.parquet','rank.parquet')})
    write_json(seal,record)
    return False


def scopes(root,frozen,days):
    all_days=frozen['context_sessions']; result={}; references={}
    for day in days:
        selected=json.loads((root/'scanner'/day/'complete.json').read_text())['selected']
        index=all_days.index(day)
        daily=all_days[max(0,index-20):index]
        hourly=all_days[max(0,index-1):index+1]
        references[day]=dict(daily=daily,hourly=hourly,selected=selected,
            daily_missing_sessions=max(0,20-len(daily)))
        for d,kind in [(day,'decision')]+[(d,'daily') for d in daily]+[(d,'hourly') for d in hourly]:
            result.setdefault(d,dict(decision=set(),daily=set(),hourly=set()))[kind].update(selected)
    saved={}
    for day, kinds in sorted(result.items()):
        available=set(source.units(root,day)); missing={}
        for kind, names in kinds.items():
            missing[kind]=sorted(names-available)
        saved[day]=dict(**{kind:sorted(names & available) for kind,names in kinds.items()},
            context_membership_missing=missing)
        # Decision names were selected from this exact source: never drop them.
        if missing['decision']:
            raise ValueError('Selected ticker lost membership')
    return saved,references


def extract(c,root,frozen,day,scope,update):
    folder=root/'bars'/day;folder.mkdir(parents=True,exist_ok=True)
    identity=digest(dict(source=frozen['hash'],day=day,scope=scope))
    complete=folder/'complete.json'
    if complete.exists():
        saved=json.loads(complete.read_text())
        if saved['identity']!=identity or any(sha(folder/n)!=h for n,h in saved['files'].items()):
            raise ValueError('Extracted shard resume mismatch')
        return True
    requests=[]; build=frozen['source_build']; decision=scope['decision']
    if decision:
        trade_attempts=source.attempts(root,day,decision)
        for resolution in (1000,10000,60000):
            requests.append((f'trade-{resolution}',sql.trade(build,day,trade_attempts,resolution),['ticker','time_us']))
        quote_attempts=source.attempts(root,day,decision,'quote')
        write_json(folder/'quote-attempts.json',quote_attempts)
        requests.append(('quote-1000',sql.quotes(frozen['quote_build'],day,quote_attempts),['ticker','side','time_us']))
        execution=source.attempts(root,day,decision,'broker_100ms')
        # Hourly pages cap result size and preserve exact resume boundaries.
        for begin in range(14400,72000,3600):
            requests.append((f'execution-{begin}',sql.execution(build,day,execution,begin,begin+3600),['ticker','time_us']))
    if scope['hourly']:
        requests.append(('trade-3600000',sql.trade(build,day,source.attempts(root,day,scope['hourly']),3600000,True),['ticker','time_us']))
    if scope['daily']:
        requests.append(('trade-86400000',sql.daily(day,scope['daily']),['ticker','time_us']))
    proofs={};began=time.perf_counter()
    for i,(name,statement,keys) in enumerate(requests):
        update(stage=f'{name} {i+1}/{len(requests)}',rows=0,bytes=0,
            eta_seconds=(time.perf_counter()-began)/i*(len(requests)-i) if i>=3 else None)
        proofs[name+'.parquet']=stream(c,statement,folder/(name+'.parquet'),keys,update)
    files={name:p['sha256'] for name,p in proofs.items()}
    if decision:files['quote-attempts.json']=sha(folder/'quote-attempts.json')
    write_json(complete,dict(identity=identity,source_hash=frozen['hash'],scope=scope,files=files,
        proofs=proofs,seconds=time.perf_counter()-began,status='complete'))
    return all(p['reused'] for p in proofs.values())


def pool(args,progress,days,operation):
    jobs=Queue()
    for day in days:jobs.put(day)
    failures=[];lock=threading.Lock()
    def worker(slot):
        c=source.client(args.endpoint,args.env_file,args.query_threads,args.query_memory_gib)
        try:
            while not (args.output/'stop.request').exists():
                try:day=jobs.get_nowait()
                except Empty:break
                progress.worker(slot,day=day,state='active',started=time.time(),stage='starting',rows=0,bytes=0)
                try:
                    reused=operation(c,day,lambda **kw:progress.worker(slot,**kw))
                    progress.finish(slot,reused)
                except Exception as error:
                    # Exceptions must not expose connection credentials or URLs.
                    message=f'{day}: {type(error).__name__}: {str(error).splitlines()[0]}'
                    progress.finish(slot,error=message)
                    with lock:failures.append(message)
                finally:jobs.task_done()
        finally:c.close()
    with ThreadPoolExecutor(max_workers=args.workers,thread_name_prefix='shard') as executor:
        futures=[executor.submit(worker,i) for i in range(args.workers)]
        for future in futures:future.result()
    if failures:raise RuntimeError('; '.join(failures[:5]))
    if not jobs.empty():raise InterruptedError('Stop requested; unfinished sessions retained for resume')


def run(args):
    root=args.output=require_runtime(args.output)
    # OS-level lock releases on crashes without deleting another run's owner.
    import msvcrt
    lock=(root/'controller.lock').open('a+b');lock.seek(0)
    try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:
        lock.close();raise RuntimeError('Dataset controller already running')
    progress=Progress(root,args.workers);status='failed'
    try:
        c=source.client(args.endpoint,args.env_file,args.query_threads,args.query_memory_gib)
        try:frozen=source.freeze(c,root,args.bars_root,args.quotes_root,args.start,args.end)
        finally:c.close()
        days=[d for d in frozen['sessions'] if d not in frozen['blocked']]
        if args.days:
            if not set(args.days)<=set(days):raise ValueError('Profile days outside ready source scope')
            days=sorted(args.days)
        write_json(root/'run.json',dict(pid=os.getpid(),started=time.time(),workers=args.workers,
            query_threads=args.query_threads,query_memory_gib=args.query_memory_gib,
            source_hash=frozen['hash'],contract_hash=digest(CONTRACT),days=days,
            implementation={p.name:sha(p) for p in Path(__file__).parent.glob('*.py')}))
        progress.phase('scanner',len(days),len(frozen['blocked']))
        pool(args,progress,days,lambda c,d,u:scanner(c,root,frozen,d,u))
        scope,refs=scopes(root,frozen,days)
        plan=dict(source_hash=frozen['hash'],scopes=scope,references=refs)
        existing=root/'extraction-plan.json'
        if existing.exists() and json.loads(existing.read_text())!=plan:
            raise ValueError('Extraction scope changed; use a new output root')
        write_json(existing,plan)
        progress.phase('extract',len(scope),len(frozen['blocked']))
        pool(args,progress,sorted(scope),lambda c,d,u:extract(c,root,frozen,d,scope[d],u))
        manifests=split_days(days) if len(days)>=2 else {'profile':days}
        seals={d:sha(root/'bars'/d/'complete.json') for d in scope}
        for role,selected in manifests.items():
            write_json(root/(role+'.json'),dict(version=CONTRACT['version'],role=role,
                source_hash=frozen['hash'],contract=CONTRACT,sessions=[dict(day=d,**refs[d]) for d in selected],
                shard_seals=seals,blocked=frozen['blocked']))
        status='complete_with_blocks' if frozen['blocked'] else 'complete'
        write_json(root/'complete.json',dict(status=status,sessions=len(days),blocked=frozen['blocked'],
            source_hash=frozen['hash'],manifests={r:sha(root/(r+'.json')) for r in manifests},shard_seals=seals))
    except InterruptedError:
        status='interrupted';raise
    finally:
        progress.close(status);lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1);lock.close()


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,default=Path('D:/TradingML/runtimes/market_shards/v1'))
    p.add_argument('--bars-root',type=Path,default=Path('D:/TradingML/runtimes/market-data-pipeline/bars-2026-funnel-32'))
    p.add_argument('--quotes-root',type=Path,default=Path('D:/TradingML/runtimes/market-data-pipeline/quotes-2026-v1/full'))
    p.add_argument('--env-file',type=Path,default=Path('D:/TradingML/secrets/.env'))
    p.add_argument('--endpoint',default='http://127.0.0.1:8123')
    p.add_argument('--start',default='2026-01-01');p.add_argument('--end')
    p.add_argument('--workers',type=int,default=8);p.add_argument('--query-threads',type=int,default=1)
    p.add_argument('--query-memory-gib',type=float,default=2.)
    p.add_argument('--days',nargs='+',help='Explicit bounded profile scope; use a separate output root')
    a=p.parse_args(argv)
    if not 1<=a.workers<=32 or not 1<=a.query_threads<=4 or not 0<a.query_memory_gib<=8 or a.workers*a.query_memory_gib>64:
        p.error('Bounded workers 1..32, threads 1..4, query memory <=8 GiB and aggregate <=64 GiB required')
    run(a)


if __name__=='__main__':main()
