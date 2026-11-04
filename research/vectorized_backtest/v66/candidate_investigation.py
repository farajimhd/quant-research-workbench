"""Restartable, training-only endpoint diagnosis with unchanged replay packets.

One independent lane, isolated artifacts, no campaign-controller mutations.
The observer returns the original packet unchanged and captures overlapping
pre-order causes separately from chronological admission/order accounting.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse
import json
import queue
import time
from dataclasses import replace
from pathlib import Path
import numpy as np
from .publication import write_json
from .distributed_assets import sha
from .identity import digest


def price_causes(prices,bid,ask):
    buy,_,stop,target,_=np.moveaxis(prices,-1,0)
    return dict(missing_buy=~np.isfinite(buy),missing_stop=~np.isfinite(stop),
        missing_target=~np.isfinite(target),invalid_buy=np.isfinite(buy)&((buy<=0)|(buy>1000)),
        invalid_stop=np.isfinite(stop)&((stop<=0)|(stop>=buy)|(stop>bid-.03)),
        invalid_target=np.isfinite(target)&(target<=buy),
        invalid_quote=~np.isfinite(bid)|~np.isfinite(ask)|(bid<=0)|(ask<bid),
        excessive_spread=(ask-bid)>bid*.05)


def variants(parent):
    """Predeclared causal comparisons, not a threshold search."""
    from .authority import construct
    from .price_program import Prices
    from .composition import mutation
    base=Prices.restore(parent.policy.price_program)
    if base.stop.operation!='reference' or base.target.operation!='max':
        raise ValueError('This investigation requires the diagnosed swing reference/max pair')
    if not base.stop.reference.startswith('swing.') or not base.target.reference.startswith('swing.'):
        raise ValueError('Expected confirmed-swing stop and target')
    result=[('parent',parent)]
    for label,stop,target in [('stop_available',True,False),('target_available',False,True),('both_available',True,True)]:
        prices=replace(base,stop=replace(base.stop,operation='available',other='bid') if stop else base.stop,
                       target=replace(base.target,operation='available',other='ask') if target else base.target)
        result.append((label,construct(replace(parent,policy=replace(parent.policy,price_program=prices.payload())))))
    result.append(('ordinary_control',construct(mutation(np.random.default_rng(17006),parent,1))))
    return result


class Observer:
    def __init__(self):self.rows={}

    def capture(self,engine,indices,packet):
        from .lineage import program_key
        from .genome import STAGES
        signals,ratios,prices,owners,abstain=(v.detach().cpu().numpy() for v in packet)
        ix=indices.detach().cpu().numpy();slots=engine.slots_cpu[ix];eligible=slots>=0
        # Quote columns come from the exact resident execution bank.
        safe=slots.clip(0);bid=engine.data.host_tensors['bid'].numpy()[ix[:,None],safe]
        ask=engine.data.host_tensors['ask'].numpy()[ix[:,None],safe]
        work=engine.work[:len(ix)].detach().cpu().numpy();known=engine.known[:len(ix)].detach().cpu().numpy()
        offsets=engine.base.offsets_cpu;counts=engine.base.counts.detach().cpu().numpy()
        for i,member in enumerate(engine.base.members):
            key=program_key(member)
            row=self.rows.setdefault(key,dict(eligible=0,entry_signals=0,price_rejected=0,guard_pass=0,
                causes={},actions={},nodes={},tickers={}))
            entry=((signals[:,i]&1)!=0)|abstain[:,i];entry&=eligible
            masks=dict(eligible=eligible,entry_signals=entry,price_rejected=abstain[:,i]&eligible,
                       guard_pass=((signals[:,i]&1)!=0)&eligible)
            for name,mask in masks.items():row[name]+=int(mask.sum())
            for name,mask in price_causes(prices[:,i],bid,ask).items():
                row['causes'][name]=row['causes'].get(name,0)+int((mask&entry).sum())
            for stage,name in enumerate(STAGES):
                fired=entry if stage==0 else ((signals[:,i]&(1<<stage))!=0)&eligible
                row['actions'][name]=row['actions'].get(name,0)+int(fired.sum())
                start=int(offsets[i*5+stage]);count=int(counts.reshape(-1)[i*5+stage])
                if count:
                    values=work[:,start:start+count];valid=known[:,start:start+count]&eligible[:,None,:]
                    item=row['nodes'].setdefault(name,dict(known=0,nonfinite_known=0,observations=0))
                    item['known']+=int(valid.sum());item['nonfinite_known']+=int((valid&~np.isfinite(values)).sum())
                    item['observations']+=int(eligible.sum())*count
            for col in np.unique(slots[eligible]):
                ticker=engine.data.tickers[int(col)]
                r=row['tickers'].setdefault(ticker,{k:0 for k in masks})
                for name,mask in masks.items():r[name]+=int((mask&(slots==col)).sum())


def run(job_path,source_generation,serial,out):
    from .agent_intervention import evidence
    from .genome import Individual
    from .authority import construct
    from .lineage import program_key
    from .full_common import config,aggregate,serializable
    from .objectives import Objective
    from .session_lanes import Worker
    from .compact_inference import Inference
    from .progress_sessions import read_verified
    import torch
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    job=read_verified(job_path);root=Path(job['campaign'])/'phase-2/global'
    folder,binding=evidence(root,source_generation)
    pop=read_verified(folder/'population.json');index=pop['ids'].index(serial)
    parent=Individual.restore(pop['population'][index]);construct(parent)
    members=variants(parent);payload=[m.payload() for _,m in members];token=digest(payload)
    spec=dict(version='v66-candidate-investigation-v1',serial=serial,source_generation=source_generation,
        evidence=binding,training_sha256=job['training_sha256'],job_sha256=sha(job_path),runner_sha256=sha(__file__),
        execution=job['execution'],members=[dict(label=label,program_sha256=program_key(m),payload=m.payload()) for label,m in members],
        comparison='Stop fallback, target fallback, both; unchanged parent and seeded ordinary-mutation control. No threshold search.',
        acceptance='Full training score/net and chronological/session breadth versus parent; no claim from inactivity alone. Preserve all execution guards. Target available can also change targets when a valid swing lies below ask; quantify outcomes, do not call it behavior-preserving.',
        validation_used=False)
    spec_path=out/'spec.json'
    if spec_path.exists() and read_verified(spec_path)!=spec:raise ValueError('Investigation identity changed; use new output')
    write_json(spec_path,spec)
    cfg=config(job,'workstation','training')
    cfg.update(output=str(out/'replay'),stop_roots=[str(out)],batch_size=len(members),maximum_replay_batch_size=len(members),
        host_prepare_workers=1,host_prefetch_depth=1,host_prefetch=False,maximum_host_sessions=2,
        host_reserve_gib=64.,host_admission_gib=4.,lane_memory_bytes=8*2**30,
        retain_session_residency=False,reuse_results=False,prefetch_transfer=False,
        replay_transfer_mode='synchronous',replay_time_block=256,execution_diagnostics=True,
        compile_cache_root=str(out),indexed_results=True)
    training=read_verified(job['training'],job['training_sha256'])
    manifest=read_verified(training['shard_manifest'],training['shard_manifest_sha256'])
    widths={s['day']:len(s['selected']) for s in manifest['sessions']}
    baseline=read_verified(folder/'training.json',binding['files']['training.json'])
    observer=Observer();original=Inference.__call__
    def observed(engine,indices):
        packet=original(engine,indices);observer.capture(engine,indices,packet);return packet
    Inference.__call__=observed
    events=queue.Queue(maxsize=32);worker=None;panels=[];completed=[];parity=[]
    started=time.time()
    try:
        worker=Worker(cfg,events,0)
        for day in job['training_days']:
            path=out/'days'/f'{day}.json'
            if path.exists():
                saved=read_verified(path)
                if saved['spec_sha256']!=sha(spec_path):raise ValueError('Day spec mismatch')
                receipt=read_verified(saved['receipt'],saved['receipt_sha256'])
            else:
                observer.rows={}
                write_json(out/'state.json',dict(status='running',serial=serial,current_day=day,completed=completed,total=len(job['training_days']),updated_epoch=time.time(),pid=os.getpid()))
                worker.run(day,None,payload,1,token,widths[day])
                receipt_path=out/'replay/generation-0001'/day/'receipt.json';receipt=read_verified(receipt_path)
                write_json(path,dict(spec_sha256=sha(spec_path),receipt=str(receipt_path),receipt_sha256=sha(receipt_path),endpoints=observer.rows))
            if receipt['split']!='training' or receipt['validation_opened'] or receipt['population_sha256']!=token or receipt['execution']!=job['execution']:raise ValueError('Replay boundary mismatch')
            old=baseline['receipts'][day];old=read_verified(old['receipt'],old['receipt_sha256'])
            old_index=old['program_keys'].index(program_key(parent)) if 'program_keys' in old else index
            for metric in ('net_pnl','closed_positions','winning_positions','episode_profits','episode_entry_clocks','episode_exit_clocks','episode_branch'):
                a=np.asarray(receipt['metrics'][metric][0]);b=np.asarray(old['metrics'][metric][old_index])
                if a.shape!=b.shape or not np.allclose(a,b,rtol=0,atol=1e-7):raise ValueError('Parent replay parity failed: '+day+' '+metric)
            parity.append(day)
            metrics=receipt['metrics'];panel=aggregate([metrics],Objective(**job['objective']));panels.append(dict(_session_panel=panel,_summary_objective=(job['objective']['drawdown_weight'],job['objective']['loss_weight'])))
            completed.append(day)
            write_json(out/'state.json',dict(status='running',serial=serial,completed=completed,total=len(job['training_days']),updated_epoch=time.time(),pid=os.getpid(),parent_parity=len(parity)))
            print(json.dumps(dict(day=day,completed=len(completed),net=metrics['net_pnl'],trades=metrics['closed_positions'])),flush=True)
        result=serializable(aggregate(panels,Objective(**job['objective'])))
        write_json(out/'summary.json',dict(spec_sha256=sha(spec_path),labels=[label for label,_ in members],training=result,days=completed,parent_parity=parity,seconds=time.time()-started,validation_used=False))
        write_json(out/'state.json',dict(status='complete',serial=serial,completed=completed,total=len(job['training_days']),updated_epoch=time.time(),pid=os.getpid(),parent_parity=len(parity)))
    except BaseException as error:
        write_json(out/'state.json',dict(status='failed',serial=serial,completed=completed,total=len(job['training_days']),updated_epoch=time.time(),error=str(error),pid=os.getpid()))
        raise
    finally:
        Inference.__call__=original
        if worker:worker.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--job',required=True);p.add_argument('--generation',type=int,required=True)
    p.add_argument('--serial',type=int,required=True);p.add_argument('--out',required=True);a=p.parse_args()
    run(a.job,a.generation,a.serial,a.out)
