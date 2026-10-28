"""Reuse only explicitly hash-bound complete replay receipts on continuation."""
import json
from pathlib import Path
from research.vectorized_backtest.v42.distributed_assets import sha


def restore(job,out,role,days,generation,token,population):
    bound=job.get('resume_transition',{}).get('completed_replays',{})
    paths=[Path(out)/f'generation-{generation:04d}'/day/'receipt.json' for day in days]
    keys=[f'{role}/{generation}/{day}' for day in days]
    if not all(key in bound for key in keys):return None
    result=[];refs={}
    for day,key,path in zip(days,keys,paths):
        receipt_hash=sha(path)
        if receipt_hash!=bound[key]:raise ValueError('Completed replay continuation receipt changed')
        r=json.loads(path.read_bytes())
        if r['day']!=day or r['split']!=role or r['population_sha256']!=token or not r['full_session'] or r['execution']!=job['execution'] or r['validation_opened']!=(role=='validation') or len(r['case_owners'])!=population or any(x is not None for x in r['case_owners']):raise ValueError('Completed replay continuation identity changed')
        if len(r['metrics']['net_pnl'])!=population:raise ValueError('Completed replay population differs')
        result.append(r['metrics']);refs[day]=dict(receipt=str(path),receipt_sha256=receipt_hash)
    return result,refs
