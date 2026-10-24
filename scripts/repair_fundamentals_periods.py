"""Bounded additive period repair; never reruns fact normalization/extraction."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse,json,time,platform
from concurrent.futures import ThreadPoolExecutor,as_completed
from scripts.run_fundamentals import Client,save,lock
from pipelines.fundamentals import schema
from pipelines.fundamentals.period_recovery import candidates_sql,recovery_sql,identity_sql,VERSION
from pipelines.certified_research.contracts import literal,digest
from research.mlops.env import load_env_files
from research.mlops.clickhouse import discover_clickhouse_env_files


def run(a):
    root=Path(a.run_dir).resolve();runtime=Path('D:/TradingML/runtimes').resolve()
    if not runtime.is_dir() or not root.is_relative_to(runtime):raise ValueError('Required runtime root unavailable')
    if not 1<=a.workers<=4:raise ValueError('Use 1..4 bounded workers')
    if a.execute and platform.node().upper()!='DESKTOP-SAAI85T':raise ValueError('Deploy committed release to workstation first')
    root.mkdir(parents=True,exist_ok=True)
    population=json.loads(Path(a.population).read_text(encoding='utf-8-sig'))
    ciks=sorted(set(population['families']['sec_reported']))
    if a.ciks:ciks=[x for x in ciks if x in a.ciks.split(',')]
    if not ciks:raise ValueError('Empty approved scope')
    load_env_files(discover_clickhouse_env_files(),verbose=False)
    with lock(runtime/'fundamentals-period-repair.lock'):
        c=Client(a.endpoint)
        try:
            if a.execute:schema.install(c.query)
            else:schema.verify(c.query,required=False)
        finally:c.close()
        started=time.monotonic();jobs=[ciks[i:i+32] for i in range(0,len(ciks),32)]
        if not a.execute:jobs=jobs[:8]  # Profile a bounded sample, never a duplicate full run.
        results=[]
        def work(ids):
            if (root/'STOP').exists():raise RuntimeError('Graceful STOP requested')
            client=Client(a.endpoint);q=client.query;begin=time.monotonic()
            try:
                sql=candidates_sql(ids)
                if not a.execute:
                    sql=sql.replace('SELECT company_fact_id,source_hash FROM arte.reference_period_evidence_v1 FINAL',"SELECT '' company_fact_id,'' source_hash WHERE 0")
                # Only immutable IDs cross the wire; values/history stay in CH.
                keys=q('SELECT DISTINCT company_fact_id,source_hash FROM ('+sql+') ORDER BY company_fact_id,source_hash')
                recovered=unmatched=reused=0
                for i in range(0,len(keys),128):
                    if (root/'STOP').exists():raise RuntimeError('Graceful STOP requested')
                    batch=keys[i:i+128];key=digest((VERSION,'json-slash-v2',batch));receipt=root/(key+'.json')
                    if a.execute and receipt.exists():
                        old=json.loads(receipt.read_text());reused+=old['unmatched'];continue
                    pairs=','.join('('+literal(x['company_fact_id'])+','+literal(x['source_hash'])+')' for x in batch)
                    selected='SELECT * FROM ('+sql+') WHERE (company_fact_id,source_hash) IN ('+pairs+')'
                    recovery=recovery_sql(selected)
                    if a.execute:
                        q('INSERT INTO arte.reference_period_evidence_v1 '+recovery,read=False)
                        found=int(q('SELECT count() n FROM arte.reference_period_evidence_v1 FINAL WHERE (company_fact_id,source_hash) IN ('+pairs+')')[0]['n'])
                        # Recompute the proof against retained identities on readback.
                        retained=candidates_sql(ids,exclude_completed=False)
                        identity=identity_sql('r.cik','r.taxonomy','r.tag','r.unit_code','toString(p.period_start)','toString(r.period_end_date)','assumeNotNull(r.accession_number)','p.frame')
                        proof=f'''SELECT r.company_fact_id,r.source_hash FROM ({retained}) r
                          INNER JOIN arte.reference_period_evidence_v1 AS p FINAL
                          ON r.company_fact_id=p.company_fact_id AND r.source_hash=p.source_hash
                          WHERE (r.company_fact_id,r.source_hash) IN ({pairs}) AND
                          {identity}=r.company_fact_id'''
                        verified=int(q('SELECT count() n FROM ('+proof+')')[0]['n'])
                        if verified!=found:raise ValueError('Period identity readback mismatch')
                        save(receipt,dict(version=VERSION,keys=batch,recovered=found,unmatched=len(batch)-found))
                    else:found=int(q('SELECT count() n FROM ('+recovery+')')[0]['n'])
                    recovered+=found;unmatched+=len(batch)-found
                return dict(ciks=ids,candidates=len(keys),recovered=recovered,unmatched=unmatched,reused_unmatched=reused,seconds=time.monotonic()-begin)
            finally:client.close()
        with ThreadPoolExecutor(max_workers=a.workers) as pool:
            futures=[pool.submit(work,ids) for ids in jobs]
            for future in as_completed(futures):
                try:results.append(future.result())
                except BaseException as error:
                    for pending in futures:pending.cancel()
                    save(root/'status.json',dict(state='failed',error=str(error),results=results))
                    raise
                elapsed=time.monotonic()-started
                state=dict(state='running',completed=len(results),total=len(jobs),elapsed=elapsed,
                           eta_seconds=elapsed/len(results)*(len(jobs)-len(results)),results=results)
                save(root/'status.json',state)
                print(f"[{len(results)}/{len(jobs)}] recovered {sum(x['recovered'] for x in results)} | unmatched {sum(x['unmatched'] for x in results)} | {elapsed:.1f}s | ETA {state['eta_seconds']:.1f}s",flush=True)
        if a.execute:
            c=Client(a.endpoint)
            try:schema.verify(c.query)
            finally:c.close()
        state['state']='complete';save(root/'status.json',state)


if __name__=='__main__':
    p=argparse.ArgumentParser(__doc__);p.add_argument('--population',required=True);p.add_argument('--run-dir',required=True)
    p.add_argument('--endpoint',default='http://192.168.1.218:18123');p.add_argument('--workers',type=int,default=4)
    p.add_argument('--ciks');p.add_argument('--execute',action='store_true');run(p.parse_args())
