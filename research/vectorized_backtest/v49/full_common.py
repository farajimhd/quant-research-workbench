"""Shared full-campaign contracts and training-only unified fitness."""
import json
from pathlib import Path
import numpy as np
from .objectives import Objective
from .identity import digest,source_hash
from research.vectorized_backtest.v42.distributed_assets import sha


def check_job(job):
    if job['version']!='v49-full-v1' or source_hash()!=job['source_sha256']:raise ValueError('Full V49 source mismatch')
    for role,count in (('training',24),('validation',12)):
        if sha(job[role])!=job[role+'_sha256']:raise ValueError('Split manifest changed')
        manifest=json.loads(Path(job[role]).read_bytes())
        if len(manifest['days'])!=count or len(set(manifest['days']))!=count:raise ValueError('Explicit 24/12 split required')
    train=json.loads(Path(job['training']).read_bytes())['days'];valid=json.loads(Path(job['validation']).read_bytes())['days']
    if set(train)&set(valid) or max(train)>=min(valid):raise ValueError('Chronological disjoint validation required')
    for role,days in (('training',train),('validation',valid)):
        assigned=[d for m in job['machines'].values() for d in m['days'][role]]
        if sorted(assigned)!=sorted(days):raise ValueError('Every session must have exactly one host')
    if job['objective']['drawdown_weight']!=.2:raise ValueError('Approved equity drawdown coefficient required')
    if (job['phase0_generations'],job['phase1_generations'],job['phase2_generations'],job['population'],job['objective']['loss_weight'])!=(0,50,100,1024,.5):raise ValueError('Approved full campaign budgets required')


def config(job,node,role='training'):
    m=json.loads(Path(job[role]).read_bytes());machine=job['machines'][node]
    return dict(inputs=m['inputs'],history=m['history'],indicator_root=m['indicator_root'],enriched_root=m['enriched_root'],
        feature_cache=m.get('feature_cache'),input_receipts=m['receipts'],indicator_receipts=m['indicator_receipts'],enriched_receipts=m['enriched_receipts'],
        authorization='v49-training',source_sha256=job['source_sha256'],training_manifest=job['training'],objective=job['objective'],execution=job['execution'],
        selection=job['selection'],backend='compile',device='cuda',batch_size=job['batch_size'],episode_capacity=65536,maximum_data_gib=4.,maximum_gate_gib=8.,
        capture_gib=machine['capture_gib'],host_reserve_gib=machine['reserve_gib'],host_admission_gib=machine['admission_gib'],host_prefetch=True,shared_replay=False,
        immutable_population_cache=True,plan_limit=1,maximum_host_sessions=2,retain_session_residency=True,reuse_results=True,trace_validation=True,
        phase0_generations=job['phase0_generations'],phase1_generations=job['phase1_generations'],split=role)


def aggregate(ordered,objective=Objective()):
    """Sum identical complete-session costs across training days, never validation."""
    objective.validate()
    n=len(ordered[0]['net_pnl']);pnl=np.zeros(n);loss=np.zeros(n);dd=np.zeros(n);equity=np.zeros(n);peak=np.zeros(n)
    trades=np.zeros(n);wins=np.zeros(n);risk=np.zeros(n);distance=np.zeros(n);eligible=np.ones(n,bool)
    for m in ordered:
        if any(len(m[k])!=n for k in ('net_pnl','terminal_valid','episode_profits','episode_exit_clocks')):raise ValueError('Global panel alignment changed')
        session_dd=np.asarray(m['drawdown'],float)
        if session_dd.shape!=(n,) or not np.isfinite(session_dd).all() or (session_dd<0).any():raise ValueError('Finite session equity drawdown required')
        dd+=session_dd
        pnl+=m['net_pnl'];trades+=m['closed_positions'];wins+=m['winning_positions']
        risk=np.maximum(risk,m['maximum_position_risk']);distance=np.maximum(distance,m['maximum_stop_distance']);eligible&=np.asarray(m['terminal_valid'],bool)
        for i in range(n):
            v=np.asarray(m['episode_profits'][i],float);clocks=np.asarray(m['episode_exit_clocks'][i],int)
            if len(v)!=len(clocks) or len(v)!=m['closed_positions'][i] or not np.isfinite(v).all():raise ValueError('Global episode ledger mismatch')
            loss[i]+=np.maximum(-v,0).sum()
            if len(v):
                order=np.argsort(clocks,kind='stable');c=clocks[order];starts=np.r_[0,np.flatnonzero(c[1:]!=c[:-1])+1]
                path=equity[i]+np.cumsum(np.add.reduceat(v[order],starts));peaks=np.maximum.accumulate(np.r_[peak[i],path])[1:]
                equity[i]=path[-1];peak[i]=peaks[-1]
    score=pnl-objective.drawdown_weight*dd-objective.loss_weight*loss
    if not np.isfinite(score).all():raise ValueError('Nonfinite global cost')
    eligible&=(risk<=300.+1e-8)&(distance<=.2+1e-10)
    return dict(score=score,net_pnl=pnl,gross_losses=loss,drawdown=dd,closed_positions=trades,winning_positions=wins,maximum_position_risk=risk,eligible=eligible)


def serializable(panel):return {k:v.tolist() for k,v in panel.items()}


def ranking(panel,ids):
    order=np.lexsort((ids,-panel['score']))
    return [dict(serial=int(ids[i]),**{k:(bool(v[i]) if k=='eligible' else float(v[i])) for k,v in panel.items()}) for i in order]
