"""Import complete financial rows from hash-bound phase-one session receipts."""
import json,hashlib
from pathlib import Path
from .progress_sessions import read_verified
from research.vectorized_backtest.v42.publication import write_json

def prepare(job,bus,output):
    """CPU-only preparation, bounded to two independent session transactions."""
    from concurrent.futures import ThreadPoolExecutor
    def one(day):
        folder=Path(bus)/'sessions'/day;meta=read_verified(folder/'export.json',job['resume_transition']['exports'][day])
        train=read_verified(folder/'training.json',meta['files']['training.json'])
        pedigree=read_verified(folder/'pedigree.json',meta['files']['pedigree.json'])
        receipt=read_verified(folder/'receipt.json',meta['files']['receipt.json'])
        if receipt['day']!=day or receipt['split']!='training' or not receipt['full_session'] or receipt['validation_opened'] or receipt['execution']!=job['execution'] or receipt['population_sha256']!=train['population_sha256']:raise ValueError('Immigration replay authority mismatch')
        if hashlib.sha256((folder/'receipt.json').read_bytes()).hexdigest()!=train['receipts'][day]['receipt_sha256']:raise ValueError('Training receipt link mismatch')
        by_id={r['serial']:r for r in pedigree};metrics=receipt['metrics'];results={}
        if any(len(v)!=len(train['ids']) for v in metrics.values()):raise ValueError('Source financial row alignment mismatch')
        for i,serial in enumerate(train['ids']):
            key=by_id[serial]['program_sha256'];row={k:v[i] for k,v in metrics.items() if k!='result_reused'}
            if key in results and results[key]!=row:raise ValueError('Duplicate network has conflicting financial results')
            results[key]=row
        identity=dict(source=job['source_sha256'],data=receipt['identity'],execution=job['execution'],split='training',episode_capacity=65536)
        prior=job.get('global_restart_caches',{}).get(day)
        if prior:
            old=read_verified(prior['path'],prior['sha256'])
            previous=dict(old['identity']);previous['source']=identity['source']
            if previous!=identity:raise ValueError('Prior global financial identity mismatch')
            encoded=json.dumps(old['results'],sort_keys=True,separators=(',',':'),allow_nan=False)
            if hashlib.sha256(encoded.encode()).hexdigest()!=old['results_sha256']:raise ValueError('Prior global result integrity mismatch')
            for key,row in old['results'].items():
                if key in results and results[key]!=row:raise ValueError('Phase-one/global financial disagreement')
                results[key]=row
        raw=json.dumps(results,sort_keys=True,separators=(',',':'),allow_nan=False)
        path=Path(output)/'training/result-cache'/(day+'.json')
        value=dict(identity=identity,results=results,results_sha256=hashlib.sha256(raw.encode()).hexdigest(),imported_from=dict(export_sha256=job['resume_transition']['exports'][day],receipt_sha256=meta['files']['receipt.json']))
        if path.exists():
            existing=read_verified(path)
            if existing!=value:raise ValueError('Refusing to replace existing replay cache')
        else:write_json(path,value)
        return dict(day=day,rows=len(results),path=str(path))
    with ThreadPoolExecutor(max_workers=2) as pool:return list(pool.map(one,job['training_days']))
