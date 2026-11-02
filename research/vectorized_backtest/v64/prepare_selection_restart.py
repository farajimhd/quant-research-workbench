"""Prepare an isolated V64 campaign; never stops or launches a process."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse
import json
from pathlib import Path
from .distributed_assets import sha
from .identity import source_hash
from .publication import write_json
from .runtime import require_runtime
from .global_contract import check


def prepare(source, output, code):
    source=Path(source); output=require_runtime(output); code=Path(code)
    target=output/'global-job.json'
    if target.exists() or (output/'phase-2/global/checkpoint.json').exists():
        raise ValueError('Preserve existing campaign; use a fresh destination')
    old=json.loads(source.read_bytes())
    if old.get('version')!='v63-global-v1':raise ValueError('Explicit V63 predecessor required')
    root=Path(old['campaign'])/'phase-2/global'
    deployment=code/'deployment.json'; deployed=json.loads(deployment.read_bytes())
    for name,expected in deployed['files'].items():
        if sha(code/name)!=expected:raise ValueError('Deployment changed: '+name)
    job=dict(old,version='v64-global-v1',campaign=str(output),transport=str(output/'asset-bus'),
             code=str(code),commit=deployed['commit'],deployment=str(deployment),source_sha256=source_hash(),
             fresh_initial_replay='v64-fresh-founders-v1',
             selection_restart=dict(job=str(source),job_sha256=sha(source),
                 seal_sha256=sha(root/'generation-0000/complete.json'),
                 initial_pool_sha256=sha(root/'initial-pool.json')))
    for key in ('resume_transition','result_cache_transition'):job.pop(key,None)
    job['machines']={k:dict(v,code=str(code)) for k,v in old['machines'].items()}
    check(job);write_json(target,job)
    return target


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-job',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--code',type=Path,required=True)
    args=parser.parse_args()
    print(prepare(args.source_job,args.output,args.code),flush=True)


if __name__=='__main__':main()
