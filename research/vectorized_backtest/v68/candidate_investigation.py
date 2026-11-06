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
    control,_=mutation(np.random.default_rng(17006),parent,1)
    result.append(('ordinary_control',construct(control)))
    # Diagnostic ablation only: catalog daily-high inputs are signed ratios.
    # Their names do not establish positive-price domains or dead-code proof.
    from .genome import INPUTS,CATALOG,Network,Neuron
    from .authority import ALLOWED
    exit_net=parent.networks['exit'];nodes=exit_net.nodes
    if (len(nodes)==13 and nodes[10].op=='cross' and nodes[10].weights==(1.,)
            and nodes[10].bias==0 and len(nodes[10].sources)==1
            and nodes[10].sources[0]<len(CATALOG)
            and CATALOG[nodes[10].sources[0]].name=='arte.trade.86400000ms.lag2.high'
            and nodes[11].op=='decision' and nodes[11].sources==(INPUTS+10,)
            and nodes[11].weights[0]>=0 and nodes[12].op=='whole_and'
            and INPUTS+11 in nodes[12].sources and exit_net.threshold>=0):
        disabled=Network((Neuron((ALLOWED[0],),(0.,),0.,op='identity'),),0.,0,0.,0.)
        result.append(('disable_exit_experiment',construct(replace(parent,networks=dict(parent.networks,exit=disabled)))))
    else:raise ValueError('Exit ablation experiment does not match selected parent')
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


def configured_variants(parent,serial,comparisons=None):
    """Use the actual candidate contract; never impose17006's price/graph shape."""
    from .authority import construct
    from .composition import mutation
    from .genome import Individual
    from .lineage import program_key
    control,_=mutation(np.random.default_rng(serial),parent,1)
    result=[('parent',parent),('ordinary_control',construct(control))]
    if comparisons is not None:
        if comparisons['parent_sha256']!=program_key(parent):raise ValueError('Comparison parent changed')
        for item in comparisons['repairs']:
            if not all(str(item.get(k,'')).strip() for k in ('label','hypothesis','expected_change','tradeoff','falsification')):
                raise ValueError('Each repair needs an evidence-led comparison plan')
            child=Individual.restore(item['payload']);canonical=construct(child)
            if program_key(child)!=program_key(canonical):raise ValueError('Repair must already be canonical')
            result.append((item['label'],child))
    if len(result)>8 or len({label for label,_ in result})!=len(result):raise ValueError('Invalid bounded comparison labels/count')
    return result


def endpoint_layout(members):
    from .strategy_mix import leaves
    from .lineage import program_key
    from .review_contract import input_contract
    return {label:[dict(branch=i,program_sha256=program_key(branch),
                       networks={name:dict(nodes=len(net.nodes),threshold=net.threshold,
                           size_source=net.size_source,size_weight=net.size_weight,size_bias=net.size_bias,
                           inputs=input_contract(net))
                           for name,net in branch.networks.items()},
                       prices=branch.payload()['policy']['price_program'],minimum_age=branch.minimum_age)
                   for i,branch in enumerate(leaves(member))] for label,member in members}


def run(job_path,source_generation,serial,out,shard_index=0,shard_count=1,profile='configured',comparisons=None):
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
    if not 1<=shard_count<=4 or not 0<=shard_index<shard_count:raise ValueError('Invalid bounded diagnostic shard')
    days=job['training_days'][shard_index::shard_count]
    folder,binding=evidence(root,source_generation)
    pop=read_verified(folder/'population.json');index=pop['ids'].index(serial)
    parent=Individual.restore(pop['population'][index]);construct(parent)
    if profile=='17006' and serial!=17006:raise ValueError('17006 experiment is candidate specific')
    members=variants(parent) if profile=='17006' else configured_variants(parent,serial,comparisons)
    payload=[m.payload() for _,m in members];token=digest(payload)
    spec=dict(version='v67-candidate-investigation-v1',serial=serial,source_generation=source_generation,
        evidence=binding,training_sha256=job['training_sha256'],job_sha256=sha(job_path),runner_sha256=sha(__file__),
        execution=job['execution'],days=days,shard_index=shard_index,shard_count=shard_count,
        profile=profile,comparison_plan=comparisons,endpoint_layout=endpoint_layout(members),
        members=[dict(label=label,program_sha256=program_key(m),payload=m.payload()) for label,m in members],
        comparison='Explicit immutable repair plan plus unchanged parent and seeded ordinary control. No automatic repair grid.',
        acceptance='Full training score/net and chronological/session/ticker breadth versus parent; preserve all execution guards. Evaluate each explicit plan and tradeoff; increased activity alone is not improvement.',
        validation_used=False)
    spec_path=out/'spec.json'
    if spec_path.exists() and digest(read_verified(spec_path))!=digest(spec):raise ValueError('Investigation identity changed; use new output')
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
        for day in days:
            path=out/'days'/f'{day}.json'
            if path.exists():
                saved=read_verified(path)
                if saved['spec_sha256']!=sha(spec_path):raise ValueError('Day spec mismatch')
                receipt=read_verified(saved['receipt'],saved['receipt_sha256'])
            else:
                observer.rows={}
                write_json(out/'state.json',dict(status='running',serial=serial,current_day=day,completed=completed,total=len(days),updated_epoch=time.time(),pid=os.getpid()))
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
            write_json(out/'state.json',dict(status='running',serial=serial,completed=completed,total=len(days),updated_epoch=time.time(),pid=os.getpid(),parent_parity=len(parity)))
            print(json.dumps(dict(day=day,completed=len(completed),net=metrics['net_pnl'],trades=metrics['closed_positions'])),flush=True)
        result=serializable(aggregate(panels,Objective(**job['objective'])))
        write_json(out/'summary.json',dict(spec_sha256=sha(spec_path),labels=[label for label,_ in members],training=result,days=completed,parent_parity=parity,seconds=time.time()-started,validation_used=False))
        write_json(out/'state.json',dict(status='complete',serial=serial,completed=completed,total=len(days),updated_epoch=time.time(),pid=os.getpid(),parent_parity=len(parity)))
    except BaseException as error:
        write_json(out/'state.json',dict(status='failed',serial=serial,completed=completed,total=len(days),updated_epoch=time.time(),error=str(error),pid=os.getpid()))
        raise
    finally:
        Inference.__call__=original
        if worker:worker.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--job',required=True);p.add_argument('--generation',type=int,required=True)
    p.add_argument('--serial',type=int,required=True);p.add_argument('--out',required=True)
    p.add_argument('--shard-index',type=int,default=0);p.add_argument('--shard-count',type=int,default=1)
    p.add_argument('--profile',choices=('configured','17006'),default='configured')
    p.add_argument('--comparisons',help='Training-evidence-led exact repair payloads and plans, bound to parent hash')
    a=p.parse_args()
    comparisons=json.loads(Path(a.comparisons).read_bytes()) if a.comparisons else None
    if comparisons is not None and a.profile!='configured':p.error('Use configured profile with explicit comparisons')
    run(a.job,a.generation,a.serial,a.out,a.shard_index,a.shard_count,a.profile,comparisons)
