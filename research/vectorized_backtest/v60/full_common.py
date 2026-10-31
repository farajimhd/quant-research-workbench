"""Shared full-campaign contracts and training-only unified fitness."""
import json
from pathlib import Path
import numpy as np
from .objectives import Objective
from .identity import digest,source_hash
from research.vectorized_backtest.v60.distributed_assets import sha


def check_job(job):
    if job.get('version')!='v60-global-v1':raise ValueError('V60 is global-only')
    from .global_contract import check
    return check(job)


def config(job,node,role='training'):
    m=json.loads(Path(job[role]).read_bytes());machine=job['machines'][node]
    return dict(inputs=m['inputs'],history=m['history'],indicator_root=m['indicator_root'],enriched_root=m['enriched_root'],
        feature_cache=m.get('feature_cache'),input_receipts=m['receipts'],indicator_receipts=m['indicator_receipts'],enriched_receipts=m['enriched_receipts'],
        authorization='v57-training',source_sha256=job['source_sha256'],training_manifest=job['training'],objective=job['objective'],execution=job['execution'],
        selection=job['selection'],execution_tail=job.get('execution_tail'),execution_diagnostics=job.get('execution_diagnostics',False),backend='compile',device='cuda',batch_size=job['batch_size'],maximum_replay_batch_size=1024,episode_capacity=65536,maximum_data_gib=4.,maximum_gate_gib=8.,
        capture_gib=machine['capture_gib'],host_reserve_gib=machine['reserve_gib'],host_admission_gib=machine['admission_gib'],host_prefetch=True,prefetch_transfer=node=='workstation',shared_replay=False,
        immutable_population_cache=True,plan_limit=1,maximum_host_sessions=2,retain_session_residency=True,reuse_results=True,trace_validation=True,
        result_cache_transition=job.get('result_cache_transition',{}),split=role,neural_tile=64 if node=='workstation' else 32,gpu_execution_gate=job['campaign'] if node=='workstation' and job.get('version')=='v60-full-v2' else None)


def _original_aggregate(ordered,objective=Objective(),session_sink=None):
    """Sum identical complete-session costs across training days, never validation."""
    objective.validate()
    n=len(ordered[0]['net_pnl']);pnl=np.zeros(n);loss=np.zeros(n);dd=np.zeros(n)
    trades=np.zeros(n);wins=np.zeros(n);risk=np.zeros(n);distance=np.zeros(n);eligible=np.ones(n,bool)
    for m in ordered:
        session_loss=np.zeros(n) if session_sink is not None else None
        if any(len(m[k])!=n for k in ('net_pnl','terminal_valid','episode_profits','episode_exit_clocks')):raise ValueError('Global panel alignment changed')
        session_dd=np.asarray(m['drawdown'],float)
        if session_dd.shape!=(n,) or not np.isfinite(session_dd).all() or (session_dd<0).any():raise ValueError('Finite session equity drawdown required')
        dd+=session_dd
        pnl+=m['net_pnl'];trades+=m['closed_positions'];wins+=m['winning_positions']
        risk=np.maximum(risk,m['maximum_position_risk']);distance=np.maximum(distance,m['maximum_stop_distance']);eligible&=np.asarray(m['terminal_valid'],bool)
        for i in range(n):
            v=np.asarray(m['episode_profits'][i],float);clocks=np.asarray(m['episode_exit_clocks'][i],int)
            if len(v)!=len(clocks) or len(v)!=m['closed_positions'][i] or not np.isfinite(v).all():raise ValueError('Global episode ledger mismatch')
            amount=np.maximum(-v,0).sum();loss[i]+=amount
            if session_loss is not None:session_loss[i]=amount
        if session_sink is not None:
            session_sink.append(dict(score=np.asarray(m['net_pnl'])-objective.drawdown_weight*session_dd-objective.loss_weight*session_loss,net_pnl=np.asarray(m['net_pnl']),gross_losses=session_loss,drawdown=session_dd,closed_positions=np.asarray(m['closed_positions']),eligible=np.asarray(m['terminal_valid'],bool)))
    score=pnl-objective.drawdown_weight*dd-objective.loss_weight*loss
    if not np.isfinite(score).all():raise ValueError('Nonfinite global cost')
    # User-authorized selection policy: valid terminal replay only. Risk and
    # distance remain observed diagnostics; admission is governed by the job.
    if not np.isfinite(risk).all() or not np.isfinite(distance).all() or (risk<0).any() or (distance<0).any():raise ValueError('Invalid risk diagnostics')
    return dict(score=score,net_pnl=pnl,gross_losses=loss,drawdown=dd,closed_positions=trades,winning_positions=wins,maximum_position_risk=risk,eligible=eligible)


def serializable(panel):return {k:v.tolist() for k,v in panel.items()}


def ranking(panel,ids):
    order=np.lexsort((ids,-panel['score']))
    return [dict(serial=int(ids[i]),**{k:(bool(v[i]) if k=='eligible' else float(v[i])) for k,v in panel.items()}) for i in order]


def aggregate(ordered,objective=Objective(),session_sink=None):
    from .robust_score import ticker_component,combine
    objective.validate();panels=[]
    for metrics in ordered:
        panel=_original_aggregate([metrics],objective)
        panel['ticker_component']=ticker_component(metrics)
        panel=combine([panel]);panels.append(panel)
    if session_sink is not None:session_sink.extend(panels)
    return combine(panels)
