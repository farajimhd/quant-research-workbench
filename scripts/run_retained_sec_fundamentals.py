"""Targeted retained SEC gap publication using distinct historical/ingest clocks."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
import json
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import closing
from datetime import datetime, timezone
from uuid import uuid5, NAMESPACE_URL
from scripts.run_fundamentals import Client, save, lock
from pipelines.fundamentals import retained_sec, schema
from pipelines.fundamentals.eligibility import require_eligible
from pipelines.certified_research.contracts import digest, literal
from pipelines.certified_research.publication import KeeperClaim
from src.trading_runtime.keeper_session import open_workstation_keeper_session
from research.mlops.env import load_env_files
from research.mlops.clickhouse import discover_clickhouse_env_files


def selection(manifest, population):
    body=dict(manifest); seal=body.pop('manifest_hash',None)
    if seal!=digest(body) or body.get('population_hash')!=digest(population):
        raise ValueError('Retained gap manifest/population seal differs')
    documents=body['documents']; allowed=set(population['families']['sec_bulk'])
    if not 1<=len(documents)<=128 or len({digest(d) for d in documents})!=len(documents):
        raise ValueError('Expected 1..128 distinct exact gap documents')
    for d in documents:
        if d['cik'] not in allowed:raise ValueError('Document outside reviewed population')
        if not body['start']<=d['day']<=body['end']:raise ValueError('Document outside reviewed date range')
        require_eligible('issuer_cik',[d['cik']])
    return documents


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--population',type=Path,required=True)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--endpoint',default='http://192.168.1.218:18123')
    parser.add_argument('--workers',type=int,default=4)
    parser.add_argument('--plan-only',action='store_true')
    a=parser.parse_args();root=a.run_dir.resolve();runtime=Path('D:/TradingML/runtimes').resolve()
    if not runtime.is_dir() or not root.is_relative_to(runtime) or not 1<=a.workers<=8:
        raise ValueError('Invalid runtime or bounded worker count')
    population=json.loads(a.population.read_text(encoding='utf-8-sig'))
    manifest=json.loads(a.manifest.read_text(encoding='utf-8-sig'));documents=selection(manifest,population)
    root.mkdir(parents=True,exist_ok=True)
    for name in ('receipts','blocked'): (root/name).mkdir(exist_ok=True)
    load_env_files(discover_clickhouse_env_files(),verbose=False)
    with lock(root/'controller.lock'):
        request=dict(manifest_hash=manifest['manifest_hash'],population_hash=digest(population),policy=retained_sec.VERSION,
                     endpoint=a.endpoint,workers=a.workers)
        old=json.loads((root/'request.json').read_text()) if (root/'request.json').exists() else None
        if old and any(old[k]!=v for k,v in request.items()):raise ValueError('Immutable request differs')
        observed=datetime.fromisoformat(old['observed_at']) if old else datetime.now(timezone.utc)
        if not old:save(root/'request.json',dict(request,observed_at=observed.isoformat()))
        with closing(Client(a.endpoint)) as client:
            schema.verify(client.query,'arte')
            planned=[dict(document=d,lineage_hash=digest(retained_sec.inspect(client.query,d))) for d in documents]
        save(root/'plan.json',dict(request=request,documents=planned))
        if a.plan_only:
            print(json.dumps(dict(state='planned',documents=len(planned),policy=retained_sec.VERSION)));return 0
        stop=threading.Event();mutex=threading.RLock();began=time.monotonic()
        signal.signal(signal.SIGINT,lambda *_:stop.set())
        state=dict(state='running',mode='RETAINED SEC | historical acceptance proxy',unit='documents',total=len(documents),
            counts=dict(published=0,unchanged=0,audited=0,failed=0),active={},queued=len(documents),blocked=0,
            workers=a.workers,new_rows=0,reused_rows=0,samples={},remaining_by_family={'SEC':len(documents)},counts_include_active=True)
        def status():
            with mutex:
                state.update(updated_at=datetime.now(timezone.utc).isoformat(),elapsed=time.monotonic()-began)
                save(root/'status.json',state)
        def guard():
            if stop.is_set() or (root/'STOP').exists():raise InterruptedError('Graceful stop requested')
        status()
        with closing(open_workstation_keeper_session(timeout_seconds=30)) as keeper, closing(KeeperClaim(
                keeper.client,str(uuid5(NAMESPACE_URL,'qualified-original-fundamentals-v1')))) as claim:
            def work(item):
                d=item['document'];token=digest(d);started=time.monotonic();receipt=root/'receipts'/(token+'.json')
                with closing(Client(a.endpoint)) as own:
                    try:
                        guard();claim.check()
                        with mutex:state['active'][token]=dict(family='SEC',stage='verify original, extract and publish',started=started-began)
                        status()
                        if receipt.exists():
                            saved=json.loads(receipt.read_text())
                            if saved['lineage_hash']!=digest(retained_sec.inspect(own.query,d)):
                                raise ValueError('Completed receipt source changed; preserved for review')
                            p=saved['publication']
                            rows=own.query("SELECT fact_rows,output_hash FROM arte.reference_batch_receipt_v1 FINAL WHERE batch_key="+literal(p['batch_key']))
                            if rows!=[dict(fact_rows=p['rows'],output_hash=p['output_hash'])]:
                                raise ValueError('Completed receipt readback differs')
                            result=dict(p,state='reused')
                        else:
                            if digest(retained_sec.inspect(own.query,d))!=item['lineage_hash']:
                                raise ValueError('Planned source metadata changed before publication')
                            result=retained_sec.publish(own.query,'arte',d,request['population_hash'],observed,claim,guard)
                            save(receipt,dict(document=d,policy=retained_sec.VERSION,lineage_hash=item['lineage_hash'],
                                publication=result,seconds=time.monotonic()-started,receipt_verified=True))
                        with mutex:
                            reused=result['state']=='reused';state['counts']['unchanged' if reused else 'published']+=1
                            state['reused_rows' if reused else 'new_rows']+=result['rows']
                            if not reused:state['samples'].setdefault('SEC',{'published':[]})['published'].append(time.monotonic()-started)
                    except (ValueError,InterruptedError) as error:
                        save(root/'blocked'/(token+'.json'),dict(document=d,error=str(error)))
                        with mutex:state['blocked']+=1;state['error']=str(error)[:300]
                    except Exception as error:
                        save(root/'blocked'/(token+'.json'),dict(document=d,error=str(error),failure=True))
                        with mutex:state['counts']['failed']+=1;state['error']=str(error)[:300]
                        stop.set()
                    finally:
                        with mutex:
                            state['active'].pop(token,None);state['queued']-=1;state['remaining_by_family']['SEC']-=1
                        status()
            with ThreadPoolExecutor(max_workers=a.workers) as pool:
                pending=set();items=iter(planned);exhausted=False
                while pending or not exhausted:
                    while not exhausted and not stop.is_set() and not (root/'STOP').exists() and len(pending)<a.workers:
                        try:pending.add(pool.submit(work,next(items)))
                        except StopIteration:exhausted=True
                    if stop.is_set() or (root/'STOP').exists():exhausted=True
                    done,pending=wait(pending,timeout=5,return_when=FIRST_COMPLETED)
                    for f in done:f.result()
                    status()
            state['state']='failed' if state['counts']['failed'] else 'stopped' if stop.is_set() or (root/'STOP').exists() else 'blocked' if state['blocked'] else 'complete'
            status();save(root/'completion.json',dict(state=state['state'],counts=state['counts'],blocked=state['blocked'],
                seconds=time.monotonic()-began,full_scope=False))
            print(json.dumps(state))
            return 0 if state['state']=='complete' else 2


if __name__=='__main__':raise SystemExit(main())
