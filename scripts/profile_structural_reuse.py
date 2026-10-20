"""Read-only, source-pinned structural reuse timings against an immutable release."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import sys
sys.dont_write_bytecode=True
import argparse
import json
import sqlite3
from pathlib import Path
from contextlib import closing
from datetime import date
from time import perf_counter


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--code-root',type=Path,required=True)
    p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--bars-run-dir',type=Path,required=True)
    p.add_argument('--env-file',type=Path,default=Path('D:/TradingML/secrets/.env'))
    p.add_argument('--endpoint',default='http://192.168.1.218:18123')
    p.add_argument('--mode',choices=('legacy','optimized'),required=True)
    p.add_argument('--unit',action='append',required=True,help='TICKER,YYYY-MM-DD')
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(argv)
    if not a.output.resolve().is_relative_to(Path('D:/TradingML/runtimes').resolve()):
        raise ValueError('Profile output must use the required runtime root')
    sys.path.insert(0,str(a.code_root))
    os.environ['QMD_CLICKHOUSE_URL']=a.endpoint
    from scripts import build_market_day as builder
    from scripts.backfill_structural_sessions import expected_prior_hash,digest
    from pipelines.market_sip.events import structural_checkpoint_storage as storage
    from pipelines.market_sip.events.structural_session_checkpoints import restore_boundary
    from src.backend.structural_v7_seed import preceding_coverage,load_seed,split_evidence
    from pipelines.certified_research.source import inventory,reporting_inventory,require_ready
    frozen=json.loads((a.run_dir/'structural-request.json').read_text())
    options=builder.parse_args(['--date','2026-01-02','--env-file',str(a.env_file),
                              '--max-threads','1','--max-memory-gb','2'])
    results=[]
    ledger=a.bars_run_dir/'build-ledger-v2.sqlite3'
    with closing(builder.Client(options,readonly=True)) as client, closing(sqlite3.connect(
            ledger.as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        client.http.structural_stream_reads = True
        from uuid import uuid4
        query_ids=[]
        execute=client.http.execute
        def traced_execute(sql, *, query_id=None):
            query_id=query_id or 'structural-reuse-profile-'+uuid4().hex
            query_ids.append(query_id)
            return execute(sql,query_id=query_id)
        client.http.execute=traced_execute
        stream=client.http.iter_json_each_row
        def traced_stream(sql, **kwargs):
            kwargs.setdefault('query_id','structural-reuse-profile-'+uuid4().hex)
            query_ids.append(kwargs['query_id'])
            return stream(sql,**kwargs)
        client.http.iter_json_each_row=traced_stream
        for unit in a.unit:
            ticker,day=unit.split(',');session=date.fromisoformat(day)
            offset=len(query_ids);started=perf_counter()
            from pipelines.certified_research.source_guard_batch import RoundQuery
            source_query=RoundQuery(client.query,(session,))
            inv=inventory(source_query,session,session,sessions=(session,))
            require_ready(source_query,inv,reporting_inventory(source_query,inv))
            if inv['source_hashes'][day]!=frozen['canonical']['source_hashes'][day]:
                raise ValueError('Source differs from the frozen campaign')
            seed=preceding_coverage(client.http,ticker=ticker,session=session,campaign_plan_hash=digest(frozen))
            prior=load_seed(client.http,ticker=ticker,session=session,coverage=seed,campaign_plan_hash=digest(frozen))
            split_evidence(client.http,ticker=ticker,seed_session=date.fromisoformat(prior['session']),session=session)
            receipt=db.execute("SELECT * FROM units WHERE build_id=? AND session_date=? AND ticker=? "
                "AND stage='bars' AND status='complete'",(frozen['build_id'],day,ticker)).fetchone()
            if receipt is None:raise ValueError('Benchmark requires a completed certified bar receipt')
            expected=dict(build_id=receipt['build_id'],attempt_id=receipt['attempt_id'],
                source_hash=receipt['source_hash'],output_hash=receipt['output_hash'],
                canonical_hash=inv['source_hashes'][day],producer_hash=digest(frozen['source_hashes']))
            current=storage.read_boundaries(client,ticker,day)
            if {r['checkpoint_boundary'] for r in current}!={'PM','REGULAR'}:
                raise ValueError('Benchmark requires both completed boundaries')
            context=perf_counter();prior_hash=expected_prior_hash(prior,ticker,day,None)
            for row in current:
                if a.mode=='legacy':
                    _,value=restore_boundary(row)
                    if value.get('source')!=expected or value['prior_hash']!=prior_hash:
                        raise ValueError('Reuse provenance differs')
                    storage.publish_boundary(client,value,row['source_plan_hash'])
                else:
                    storage.verify_reused_boundary(client,row,expected_source=expected,expected_prior_hash=prior_hash)
            results.append(dict(ticker=ticker,day=day,context_seconds=context-started,
                verify_seconds=perf_counter()-context,total_seconds=perf_counter()-started,
                payload_bytes=sum(len(r['streaming_state'].encode()) for r in current),
                query_ids=query_ids[offset:],status='verified'))
    value=dict(mode=a.mode,code_root=str(a.code_root),read_only=True,units=results,
               total_seconds=sum(r['total_seconds'] for r in results))
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(value,indent=2),encoding='utf-8')
    print(json.dumps(dict(mode=a.mode,total_seconds=value['total_seconds'],
          units=[{k:v for k,v in r.items() if k!='query_ids'} for r in results])),flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
