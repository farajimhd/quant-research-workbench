"""Prepare a fresh V68 evolution from sealed V67 founder execution evidence."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json
from pathlib import Path
from .distributed_assets import sha
from .identity import source_hash
from .runtime import require_runtime
from .publication import write_json
from .supervision import VERSION,SETTINGS


def prepare(source,output,code):
    source=Path(source);output=require_runtime(output);code=Path(code)
    if (output/'global-job.json').exists() or (output/'phase-2').exists():
        raise ValueError('Use a fresh campaign destination')
    old=json.loads(source.read_bytes())
    if old.get('version')!='v67-global-v1':raise ValueError('Sealed V67 predecessor required')
    deployment=code/'deployment.json';deployed=json.loads(deployment.read_bytes())
    for name,expected in deployed['files'].items():
        if sha(code/name)!=expected:raise ValueError('Deployment changed: '+name)
    root=Path(old['campaign']);sealed=root/'phase-2/global/generation-0000'
    seal=json.loads((sealed/'complete.json').read_bytes());replays={}
    for role in ('training','validation'):
        path=sealed/(role+'.json')
        if sha(path)!=seal[role+'_sha256']:raise ValueError('Founder split seal changed')
        record=json.loads(path.read_bytes())
        if len(record['ids'])!=6144 or set(record['receipts'])!=set(old[role+'_days']):
            raise ValueError('Complete sixfold founder evidence required')
        for day,ref in record['receipts'].items():
            if sha(ref['receipt'])!=ref['receipt_sha256']:raise ValueError('Founder receipt changed')
            replays[f'{role}/0/{day}']=dict(path=ref['receipt'],sha256=ref['receipt_sha256'])
    bound=dict(version='v68-saved-founder-rescore-v1',predecessor_job=str(source),
        predecessor_job_sha256=sha(source),founders=str(root/'fresh-founders.json'),
        founders_sha256=sha(root/'fresh-founders.json'),completed_replays=replays,result_caches={})
    job=dict(old,version='v68-global-v1',campaign=str(output),transport=str(output/'asset-bus'),
             code=str(code),commit=deployed['commit'],deployment=str(deployment),source_sha256=source_hash(),
             score_version=VERSION,supervision=dict(old['supervision'],settings=SETTINGS),
             founder_readmission=bound,result_cache_transition={})
    for key in ('resume_transition','selection_restart','founder_restart','execution_restart'):
        job.pop(key,None)
    job['machines']={k:dict(v,code=str(code)) for k,v in old['machines'].items()}
    job['host_loading']=dict(old['host_loading'],workers=1)
    # Preserve the archived upstream source manifest under its original hash.
    from .global_contract import source_job_path
    authority=source_job_path(old);target=output/'source-authority'/(old['source_job_sha256']+'.json')
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists() and sha(target)!=sha(authority):raise ValueError('Source authority collision')
    if not target.exists():target.write_bytes(authority.read_bytes())
    from .global_contract import check
    check(job);write_json(output/'global-job.json',job)
    return output/'global-job.json'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-job',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--code',type=Path,required=True)
    args=parser.parse_args();print(prepare(args.source_job,args.output,args.code),flush=True)


if __name__=='__main__':main()
