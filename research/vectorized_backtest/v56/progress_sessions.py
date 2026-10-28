"""Read-only session strength summaries from sealed global replay receipts."""
import json,hashlib,statistics,math,re
from pathlib import Path

def strength(rows):
    valid=[r for r in rows if r.get('eligible',False)]
    if not valid:return {}
    if any(not math.isfinite(float(r[k])) for r in valid for k in ('score','net_pnl','drawdown')):raise ValueError('Nonfinite session summary')
    top=max(valid,key=lambda r:(r['net_pnl'],r['score'],-r['serial']))
    best=max(valid,key=lambda r:(r['score'],-r['serial']))
    trades=top['closed_positions'];wins=top.get('winning_positions')
    return dict(best_score_net=best['net_pnl'],best_score_drawdown=best['drawdown'],best_score_serial=best['serial'],top_net=top['net_pnl'],median_net=statistics.median(r['net_pnl'] for r in valid),top_drawdown=top['drawdown'],
        top_win_rate=100*wins/trades if wins is not None and trades else None,best_score=max(r['score'] for r in valid),
        profitable_fraction=100*sum(r['net_pnl']>0 for r in valid)/len(valid),median_drawdown=statistics.median(r['drawdown'] for r in valid),candidates=len(valid),top_serial=top['serial'])

def read_verified(path,expected=None):
    raw=Path(path).read_bytes()
    if expected is not None and hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('Sealed global evidence changed: '+str(path))
    return json.loads(raw)

def population_metadata(path):
    """Read the canonical publisher's ID prefix/token suffix, not 500 MB of graphs."""
    with Path(path).open('rb') as stream:
        prefix=stream.read(1024*1024).decode('utf-8');stream.seek(max(0,stream.seek(0,2)-4096));suffix=stream.read().decode('utf-8')
    if not prefix.startswith('{"ids":'):raise ValueError('Unsupported population metadata layout')
    ids,end=json.JSONDecoder().raw_decode(prefix,7)
    token=re.search(r',"population_sha256":"([0-9a-f]{64})"}\s*$',suffix)
    if prefix[end:end+14]!=',"population":' or not token or not isinstance(ids,list) or len(set(ids))!=len(ids):raise ValueError('Invalid population metadata')
    return ids,token[1]

def load_live_session(job,root,generation,role,day,ids,token):
    """Completed replay evidence; explicitly not a sealed-generation result."""
    from .full_common import aggregate,ranking
    from .objectives import Objective
    path=Path(root)/'replays'/role/f'generation-{generation:04d}'/day/'receipt.json'
    receipt=read_verified(path)
    if receipt['day']!=day or receipt['split']!=role or receipt['population_sha256']!=token or not receipt['full_session'] or receipt['validation_opened']!=(role=='validation') or receipt['execution']!=job['execution']:raise ValueError('Live replay receipt identity changed')
    panel=aggregate([receipt['metrics']],Objective(**job['objective']))
    if len(panel['score'])!=len(ids):raise ValueError('Live replay candidate alignment changed')
    timing_path=path.parent/'timings.json';seconds=None;timings=[]
    if timing_path.exists():
        timings=read_verified(timing_path)
        if timings:seconds=sum(float(row.get(k,0)) for row in timings for k in ('load_seconds','rule_seconds','replay_seconds'))
    replay=dict(total_candidates=len(ids),completed_candidates=len(ids),evaluated_candidates=receipt.get('evaluated_candidates'),reused_candidates=receipt.get('reused_candidates'),
        completed_local_batches=len(timings),total_local_batches=len(timings),session_seconds=seconds,stage='Session complete')
    return dict(ids=ids,panel=panel),dict(strength=strength(ranking(panel,ids)),batch_seconds=seconds,cached=receipt.get('evaluated_candidates')==0,replay_details=replay)

def load_global(job,root,generation,stop,progress=lambda done,total:None):
    from .full_common import aggregate,ranking
    from .objectives import Objective
    from research.vectorized_backtest.v42.distributed_assets import remote
    root=Path(root);folder=root/f'generation-{generation:04d}';seal=read_verified(folder/'complete.json');panels={};summaries={};done=0
    from .evolution_report import summarize
    records=read_verified(folder/'evaluated-pedigree.json',seal['evaluated_pedigree_sha256']) if seal.get('evaluated_pedigree_sha256') else None
    reports={}
    total=len(job['training_days'])+len(job['validation_days'])
    for role in ('training','validation'):
        evidence=read_verified(folder/(role+'.json'),seal[role+'_sha256'])
        if role=='validation' and not evidence['display_only']:raise ValueError('Validation authority changed')
        ids=evidence['ids']
        for day in job[role+'_days']:
            if stop.is_set():raise InterruptedError('Observer closed')
            ref=evidence['receipts'][day];path=Path(ref['receipt'])
            if str(root).startswith('\\\\'):path=remote(path,job['workstation_share'])
            receipt=read_verified(path,ref['receipt_sha256'])
            if receipt['day']!=day or receipt['split']!=role or receipt['population_sha256']!=evidence['population_sha256'] or not receipt['full_session'] or receipt['validation_opened']!=(role=='validation'):raise ValueError('Global session receipt identity changed')
            panel=aggregate([receipt['metrics']],Objective(**job['objective']))
            if len(panel['score'])!=len(ids):raise ValueError('Global session candidate alignment changed')
            key=role+':'+day
            if records is not None:reports[key]=summarize(ids,panel,records,generation=generation)
            panels[key]=dict(ids=ids,panel=panel);summary=strength(ranking(panel,ids))
            timing_path=path.parent/'timings.json';seconds=None
            if timing_path.exists():
                timings=read_verified(timing_path)
                if timings:seconds=sum(float(row.get(k,0)) for row in timings for k in ('load_seconds','rule_seconds','replay_seconds'))
            summaries[key]=dict(strength=summary,batch_seconds=seconds,cached=receipt.get('evaluated_candidates')==0)
            done+=1;progress(done,total)
    result=dict(generation=generation,panels=panels,summaries=summaries,reports=reports)
    if records is not None:
        from .progress_global import combined
        result['aggregates']={role:combined(result,records,len(job[role+'_days']),role) for role in ('training','validation')}
        result['aggregate']=result['aggregates']['training']
    return result
