"""Prepare an additive, hash-bound continuation of a stopped V56 full campaign."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json
from pathlib import Path
from .identity import source_hash,digest
from .campaign_contract import check_full_job
from research.vectorized_backtest.v42.distributed_assets import sha,remote,copy_verified
from research.vectorized_backtest.v42.publication import write_json

def prepare(previous,laptop_code,workstation_code):
    previous=Path(previous);old=json.loads(previous.read_bytes());root=Path(old['campaign'])
    bus=remote(old['transport'],old['workstation_share'])
    if not (root/'STOP').exists() or not (bus/'STOP').exists():raise ValueError('Both campaign stop markers are required')
    commit=json.loads((Path(laptop_code)/'deployment.json').read_bytes())['commit']
    transition=dict(predecessor_job=str(previous),predecessor_job_sha256=sha(previous),checkpoints={},exports={},result_caches={})
    from .campaign_export import records
    for day in old['training_days']:
        export=bus/'sessions'/day/'export.json'
        if export.exists():
            result=records(export.parent,old,day)
            if len(result['records'])!=128:raise ValueError(f'{day}: fewer than 128 eligible experts; explicit selection decision required')
            transition['exports'][day]=sha(export);continue
        node=next(n for n,m in old['machines'].items() if day in m['session_days'])
        session=root/'sessions'/day
        if node=='workstation':session=remote(session,old['workstation_share'])
        cp=session/'global/checkpoint.json'
        if not cp.exists():continue
        saved=json.loads(cp.read_bytes())
        if digest(saved['state'])!=saved['state_sha256']:raise ValueError('Checkpoint state integrity changed')
        transition['checkpoints'][day]=dict(node=node,checkpoint_sha256=sha(cp),state_sha256=saved['state_sha256'],contract_sha256=digest(saved['contract']),completed=saved['state']['completed'])
        audit=session/'resume-audit'/commit[:9];audit.mkdir(parents=True,exist_ok=True)
        copy_verified(cp,audit/'checkpoint.json',sha(cp))
        for cache in session.glob('replays/**/result-cache/*.json'):
            record=json.loads(cache.read_bytes());receipt=sha(cache)
            if record['identity']['source']!=old['source_sha256']:raise ValueError('Unexpected cache source')
            target=audit/'cache'/cache.relative_to(session/'replays')
            copy_verified(cache,target,receipt)
            transition['result_caches'][receipt]=dict(source=old['source_sha256'],day=day,backup=str(target))
    machines={n:dict(m,code=str(laptop_code if n=='laptop' else workstation_code),session_workers=1 if n=='laptop' else 2) for n,m in old['machines'].items()}
    job=dict(old,version='v56-full-v2',commit=commit,source_sha256=source_hash(),machines=machines,entry_suffix='-v2-'+commit[:9],resume_transition=transition,
        global_initialization=dict(version='v56-blind-mix-v1',experts=3072,offspring=3072,group_quota=512,require_surviving_contributions=True))
    check_full_job(job);path=root/('full-job-v2-'+commit[:9]+'.json')
    if path.exists():
        if json.loads(path.read_bytes())!=job:raise ValueError('Existing continuation job differs')
    else:write_json(path,job)
    return path,job

def main():
    p=argparse.ArgumentParser();p.add_argument('--previous',type=Path,required=True);p.add_argument('--laptop-code',type=Path,required=True);p.add_argument('--workstation-code',type=Path,required=True);a=p.parse_args()
    path,job=prepare(a.previous,a.laptop_code,a.workstation_code)
    print(json.dumps(dict(job=str(path),job_sha256=sha(path),completed_exports=len(job['resume_transition']['exports']),checkpoints=job['resume_transition']['checkpoints'],started=False)))

if __name__=='__main__':main()
