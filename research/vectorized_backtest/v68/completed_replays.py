"""Reuse only explicitly hash-bound complete replay receipts on continuation."""
import json
import hashlib
from pathlib import Path
from research.vectorized_backtest.v68.distributed_assets import sha


def bindings(job):
    return job.get('founder_readmission',job.get('resume_transition',{})).get('completed_replays',{})


def reference(job,out,role,day,generation):
    path=Path(out)/f'generation-{generation:04d}'/day/'receipt.json'
    expected=bindings(job).get(f'{role}/{generation}/{day}')
    if isinstance(expected,dict):return Path(expected['path']),expected['sha256']
    return path,expected


def restore(job,out,role,days,generation,token,population):
    bound=bindings(job)
    paths=[Path(out)/f'generation-{generation:04d}'/day/'receipt.json' for day in days]
    keys=[f'{role}/{generation}/{day}' for day in days]
    if not all(key in bound for key in keys):return None
    result=[];refs={}
    for day,key,path in zip(days,keys,paths):
        expected=bound[key]
        if isinstance(expected,dict):path=Path(expected['path']);expected=expected['sha256']
        raw=path.read_bytes();receipt_hash=hashlib.sha256(raw).hexdigest()
        if receipt_hash!=expected:raise ValueError('Completed replay continuation receipt changed')
        from .receipt_storage import decode
        r=decode(path,json.loads(raw))
        if r['day']!=day or r['split']!=role or r['population_sha256']!=token or not r['full_session'] or r['execution']!=job['execution'] or r['validation_opened']!=(role=='validation') or len(r['case_owners'])!=population or any(x is not None for x in r['case_owners']):raise ValueError('Completed replay continuation identity changed')
        if len(r['metrics']['net_pnl'])!=population:raise ValueError('Completed replay population differs')
        result.append(r['metrics']);refs[day]=dict(receipt=str(path),receipt_sha256=receipt_hash)
    return result,refs
