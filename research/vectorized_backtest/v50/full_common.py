"""Shared full-campaign contracts and training-only unified fitness."""
import json
from pathlib import Path
import numpy as np
from .objectives import Objective
from .identity import digest,source_hash
from research.vectorized_backtest.v42.distributed_assets import sha


def check_job(job):
    full=job['version']=='v50-full-v1'
    if job['version'] not in ('v50-pilot-v1','v50-full-v1') or source_hash()!=job['source_sha256']:raise ValueError('V50 source mismatch')
    if sha(job['source_job'])!=job['source_job_sha256'] or sha(job['initialization_probe'])!=job['initialization_probe_sha256']:raise ValueError('Source/probe changed')
    if sha(job['training'])!=job['training_sha256']:raise ValueError('Training manifest changed')
    train=json.loads(Path(job['training']).read_bytes())['days']
    if full:
        if sha(job['validation'])!=job['validation_sha256']:raise ValueError('Validation manifest changed')
        valid=json.loads(Path(job['validation']).read_bytes())['days']
        if len(train)!=24 or len(set(train))!=24 or len(valid)!=12 or len(set(valid))!=12 or max(train)>=min(valid):raise ValueError('Chronological certified24/12 split required')
        if job['training_days']!=train or job['validation_days']!=valid or set(job['machines'])!={'workstation'}:raise ValueError('Full run must use all sessions on workstation only')
        if job['machines']['workstation']['days']!=dict(training=train,validation=valid):raise ValueError('Workstation session ownership changed')
    elif job['training_days']!=['2026-08-13'] or job['validation_days'] or job['training_days'][0] not in train:raise ValueError('Pilot restricted to certified Aug13 training')
    if (job['population'],job['initial_multiplier'],job['phase2_generations'])!=(1024,6,100 if full else 50):raise ValueError('Approved population/budget mismatch')
    if job['objective']!=dict(drawdown_weight=1.,loss_weight=.1):raise ValueError('V50 cost differs from authorization')


def config(job,node,role='training'):
    m=json.loads(Path(job[role]).read_bytes());machine=job['machines'][node]
    return dict(inputs=m['inputs'],history=m['history'],indicator_root=m['indicator_root'],enriched_root=m['enriched_root'],
        feature_cache=m.get('feature_cache'),input_receipts=m['receipts'],indicator_receipts=m['indicator_receipts'],enriched_receipts=m['enriched_receipts'],
        authorization='v50-training',source_sha256=job['source_sha256'],training_manifest=job['training'],objective=job['objective'],execution=job['execution'],
        selection=job['selection'],backend='compile',device='cuda',batch_size=job['batch_size'],episode_capacity=65536,maximum_data_gib=4.,maximum_gate_gib=8.,
        capture_gib=machine['capture_gib'],host_reserve_gib=machine['reserve_gib'],host_admission_gib=machine['admission_gib'],host_prefetch=True,shared_replay=False,
        immutable_population_cache=True,plan_limit=1,maximum_host_sessions=2,retain_session_residency=True,reuse_results=True,trace_validation=True,
        split=role)


def aggregate(ordered,objective=Objective()):
    """Sum identical complete-session costs across training days, never validation."""
    objective.validate()
    n=len(ordered[0]['net_pnl']);pnl=np.zeros(n);loss=np.zeros(n);dd=np.zeros(n)
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
    score=pnl-objective.drawdown_weight*dd-objective.loss_weight*loss
    if not np.isfinite(score).all():raise ValueError('Nonfinite global cost')
    eligible&=(risk<=300.+1e-8)&(distance<=.2+1e-10)
    return dict(score=score,net_pnl=pnl,gross_losses=loss,drawdown=dd,closed_positions=trades,winning_positions=wins,maximum_position_risk=risk,eligible=eligible)


def serializable(panel):return {k:v.tolist() for k,v in panel.items()}


def ranking(panel,ids):
    order=np.lexsort((ids,-panel['score']))
    return [dict(serial=int(ids[i]),**{k:(bool(v[i]) if k=='eligible' else float(v[i])) for k,v in panel.items()}) for i in order]
