"""Bounded workstation launcher; resume sealed days, publish only full coverage."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('POLARS_MAX_THREADS','2')
os.environ.setdefault('NUMBA_NUM_THREADS','2')
import argparse
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
import json
from pathlib import Path
import platform
import sys
import time
import subprocess
from research.market_labels.v2.build import VERSION,SCOPE,ALGORITHM,GROUPING,build_day,fingerprint,sha,write_json


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,default=Path('D:/TradingML/runtimes/market_shards/datasets/2026-top5-v1'))
    p.add_argument('--labels',type=Path,default=Path('D:/TradingML/runtimes/market_shards/labels/v66-1a1b-v1/dataset.json'))
    p.add_argument('--output',type=Path,help='Default: <source>/labels/1a1b-spread-v2')
    p.add_argument('--workers',type=int,default=4)
    p.add_argument('--days',nargs='+',help='Bounded canary; cannot publish the full registry')
    p.add_argument('--publish',action='store_true')
    a=p.parse_args()
    if a.output is None:a.output=a.source/'labels'/'1a1b-spread-v2'
    runtime=Path('D:/TradingML/runtimes').resolve()
    if not runtime.is_dir():raise ValueError('Required runtime unavailable')
    if not a.output.resolve().is_relative_to(runtime) or a.output.resolve()==a.source.resolve():raise ValueError('Separate external label root required')
    if not 1<=a.workers<=8:raise ValueError('Workers must be 1..8')
    if a.publish and a.days:raise ValueError('Partial canary cannot publish')
    a.output.mkdir(parents=True,exist_ok=True)
    # Kernel-owned advisory lock is released after crashes; no stale PID override.
    lock=(a.output/'writer.lock').open('a+b');lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
    import msvcrt
    msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    manifest={role:json.loads((a.source/(role+'.json')).read_bytes()) for role in ('training','validation')}
    source_hashes={role:sha(a.source/(role+'.json')) for role in manifest}
    from .contract import SOURCE_SHA
    if sha(a.labels)!=SOURCE_SHA:raise ValueError('Approved preserved 1a dataset required')
    binding=dict(input_labels_sha256=sha(a.labels),version=VERSION,source_manifests=source_hashes,source=sha(a.source/'source.json'),producer=fingerprint(),scope=SCOPE,algorithm=ALGORITHM,grouping=GROUPING)
    tasks=[]
    for role,m in manifest.items():
        for item in m['sessions']:
            if a.days and item['day'] not in a.days:continue
            tasks.append((str(a.source/(role+'.json')),item['day'],role,str(a.output),binding,str(a.labels)))
    if not tasks or len({t[1] for t in tasks})!=len(tasks):raise ValueError('Empty/overlapping session inventory')
    if a.days and set(a.days)!={t[1] for t in tasks}:raise ValueError('Unknown canary days')
    prior=a.output/'build.json'
    if prior.exists() and json.loads(prior.read_bytes())['binding']!=binding:raise ValueError('Output belongs to another build; use a fresh root')
    code=Path(__file__).resolve().parents[3]
    deployment=code/'deployment.json'
    commit=json.loads(deployment.read_bytes())['commit'] if deployment.exists() else subprocess.check_output(['git','rev-parse','HEAD'],cwd=code,text=True).strip()
    if deployment.exists():
        for name,expected in json.loads(deployment.read_bytes())['files'].items():
            if sha(code/name)!=expected:raise ValueError('Deployment changed: '+name)
    write_json(prior,dict(binding=binding,host=platform.node(),python=sys.executable,code=str(code),source_commit=commit,source=str(a.source),days=[t[1] for t in tasks]))
    started=time.time();done=[];failed=[];reused=0;active={};pending=iter(tasks);stopping=False
    print(' '.join([sys.executable,'-B','-m','research.market_labels.v2.run_build',*sys.argv[1:]]),flush=True)
    def status(state):
        write_json(a.output/'progress.json',dict(status=state,total=len(tasks),completed=len(done),reused=reused,failed=failed,active=list(active.values()),queued=len(tasks)-len(done)-len(failed)-len(active),elapsed_seconds=time.time()-started))
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        def admit():
            nonlocal stopping
            stopping=stopping or (a.output/'STOP').exists() or bool(failed)
            while not stopping and len(active)<a.workers:
                task=next(pending,None)
                if task is None:break
                existed=(a.output/task[1]/'complete.json').exists()
                active[pool.submit(build_day,task)]=dict(day=task[1],role=task[2],reused=existed)
        admit()
        while active:
            completed,_=wait(active,timeout=2,return_when=FIRST_COMPLETED)
            for future in completed:
                item=active.pop(future)
                try:
                    proof=future.result();reused+=int(item['reused'])
                    done.append(dict(day=proof['day'],role=proof['role'],certificate=sha(a.output/proof['day']/'complete.json'),valid_rows=proof['valid_rows'],excluded_rows=proof['excluded_rows'],selected_pairs=proof['selected_pairs']))
                    print(f"{len(done)}/{len(tasks)} {proof['day']} rows={proof['valid_rows']} selected={proof['selected_pairs']}",flush=True)
                except Exception as error:
                    failed.append(dict(day=item['day'],error=repr(error)));print(failed[-1],flush=True)
            admit();status('draining' if stopping else 'running')
    if failed:status('failed');raise RuntimeError('Label build failed; inspect progress.json')
    if len(done)!=len(tasks):status('stopped');return
    # Recheck pinned source inventories before publishing a complete dataset.
    if any(sha(a.source/(role+'.json'))!=h for role,h in source_hashes.items()):raise ValueError('Source manifest changed')
    dataset=dict(version=VERSION,binding=binding,scope=SCOPE,days=sorted(done,key=lambda x:x['day']),status='verified_review_only',source=str(a.source),full_inventory=not bool(a.days))
    write_json(a.output/'dataset.json',dataset)
    if a.publish:
        registry=runtime/'v66-active-labels.json'
        if registry.exists():
            previous=json.loads(registry.read_bytes())
            archive=runtime/('v66-label-registry-'+previous['version']+'.json')
            if archive.exists() and json.loads(archive.read_bytes())!=previous:raise ValueError('Registry archive identity differs')
            write_json(archive,previous)
        current=dict(version=VERSION,dataset=str(a.output/'dataset.json'),sha256=sha(a.output/'dataset.json'))
        write_json(runtime/('v66-label-registry-'+VERSION+'.json'),current)
        write_json(registry,current)
    status('complete')


if __name__=='__main__':main()
