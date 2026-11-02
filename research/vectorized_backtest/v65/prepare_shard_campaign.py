"""Prepare a fresh V65 campaign from sealed ARTE shards and V64 execution settings."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse
import json
from pathlib import Path
from .runtime import require_runtime
from .identity import digest, source_hash
from .distributed_assets import sha
from .publication import write_json


def prepare(source, dataset, root, code, workers=6):
    from .global_contract import VERSION, STRATEGY_CONTRACT, check
    from .calibration_probe import prepare as probe
    dataset=Path(dataset);root=require_runtime(root);code=Path(code)
    if (root/'global-job.json').exists():raise ValueError('Campaign already exists')
    original=json.loads(Path(source).read_bytes())
    if original['version']!='v64-global-v1':raise ValueError('Expected V64 source job')
    if not (dataset/'complete.json').is_file():raise ValueError('Dataset is incomplete')
    assets={};manifests={};seen={}
    for role in ('training','validation'):
        manifest=dataset/(role+'.json');doc=json.loads(manifest.read_bytes())
        days=[s['day'] for s in doc['sessions']]
        assets[role]={}
        for session in doc['sessions']:
            day=session['day'];files={manifest,dataset/'source.json',dataset/'scanner'/day/'complete.json',
                dataset/'scanner'/day/'rank.parquet',dataset/'scanner'/day/'eligibility.parquet'}
            for d in set([day]+session['daily']+session['hourly']):
                folder=dataset/'bars'/d;seal=folder/'complete.json'
                if sha(seal)!=doc['shard_seals'][d]:raise ValueError('Shard seal changed: '+d)
                receipt=json.loads(seal.read_bytes());files.add(seal)
                names=receipt['files'] if d==day else [n for n in receipt['files'] if n in ('trade-1000.parquet','trade-3600000.parquet','trade-86400000.parquet')]
                for name in names:
                    path=folder/name;files.add(path)
                    if path not in seen:
                        actual=sha(path)
                        if actual!=receipt['files'][name]:raise ValueError('Shard changed: '+str(path))
                        seen[path]=dict(path=str(path),sha256=actual,size=path.stat().st_size)
            for path in files:
                if path not in seen:seen[path]=dict(path=str(path),sha256=sha(path),size=path.stat().st_size)
            assets[role][day]=[seen[p] for p in sorted(files)]
        wrapper=dict(version='v65-arte-manifest-v1',days=days,shard_manifest=str(manifest),
            shard_manifest_sha256=sha(manifest),source_hash=doc['source_hash'],
            inputs=str(dataset/'bars'),history=str(dataset/'bars'),indicator_root=None,enriched_root=None,
            receipts={d:doc['shard_seals'][d] for d in days},indicator_receipts={},enriched_receipts={})
        path=root/(role+'.json');write_json(path,wrapper);manifests[role]=(path,days)
    job=dict(original)
    for key in ('selection_restart','founder_restart','execution_restart','continuation','promotion_exports',
                'resume_transition','result_cache_transition','training_probe_reused'):
        job.pop(key,None)
    deployment=code/'deployment.json';deployed=json.loads(deployment.read_bytes())
    job.update(version=VERSION,source_sha256=source_hash(),source_job=str(source),source_job_sha256=sha(source),
        code=str(code),commit=deployed['commit'],deployment=str(deployment),campaign=str(root),
        transport=str(root/'asset-bus'),assets=assets,asset_sha256=digest(assets),
        campaign_stage='fresh-global',initial_multiplier=1,global_session_workers=workers,
        fresh_initial_replay='v65-fresh-founders-v1',execution_tail=str(dataset/'bars'),
        strategy_contract=dict(STRATEGY_CONTRACT),dataset=str(dataset),dataset_source_hash=doc['source_hash'])
    for role,(path,days) in manifests.items():
        job[role]=str(path);job[role+'_sha256']=sha(path);job[role+'_days']=days
    machine=dict(original['machines']['workstation'],hostname='DESKTOP-SAAI85T',code=str(code),
        days={r:manifests[r][1] for r in manifests},session_workers=workers)
    job['machines']={'workstation':machine}
    recipe=probe(job);job.update(initialization_probe=str(recipe),initialization_probe_sha256=sha(recipe))
    check(job);write_json(root/'global-job.json',job)
    return job


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--dataset',type=Path,default=Path('D:/TradingML/runtimes/market_shards/datasets/2026-top5-v1'))
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--code',type=Path,required=True)
    parser.add_argument('--workers',type=int,choices=(2,4,6,8),default=6)
    args=parser.parse_args();job=prepare(args.source,args.dataset,args.root,args.code,args.workers)
    print('Prepared',Path(job['campaign'])/'global-job.json',flush=True)


if __name__=='__main__':main()
