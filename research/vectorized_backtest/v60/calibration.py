"""Finite training-only activation calibration; no fitness or validation input."""
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter
import numpy as np
from .genome import STAGES, INPUTS, MARKET_INPUTS, random_network
from research.vectorized_backtest.v60.features import CATALOG

PROBE_VERSION='v42-training-activation-probe-v1'
RATE_WEIGHTS=(.30,.30,.20,.20)

def load_probe(path,training):
    from research.vectorized_backtest.v60.probe_reader import load_probe as load
    original=load(path,training)
    from .feature_layout import COLUMNS,WIDTH
    from .config import STATES
    from .feature_policy import transform_projected
    import torch
    columns=original['metadata'].get('feature_columns',list(range(len(CATALOG))))
    market=3*len(columns)
    values=torch.from_numpy(original['inputs'][...,:market])
    if not original['metadata'].get('projected_policy'):
        values=transform_projected(values,columns)
    if columns!=list(COLUMNS):
        indices=[columns.index(c)+k*len(columns) for k in range(3) for c in COLUMNS]
        values=values[...,indices]
        original['known']=original['known'][...,[columns.index(c) for c in COLUMNS]]
    original['inputs']=np.pad(values.numpy(),[(0,0)]*3+[(0,len(STATES))])
    original['held_states']=np.zeros(original['inputs'].shape[:-1]+(len(STATES),),np.float32)
    original['compact_layout']=True
    return original

def observe(net,probe,stage='entry',*,reference=False):
    """Float32 CPU graph arithmetic. Recurrence resets per independent window."""
    market_inputs=MARKET_INPUTS;input_count=INPUTS;features=len(CATALOG)
    if probe.get('compact_layout'):
        from .feature_layout import remap_sources,WIDTH
        from .config import STATES
        counts=np.array([len(n.sources) for n in net.nodes])
        sources=np.zeros((len(counts),int(counts.max())),np.int32)
        for i,n in enumerate(net.nodes):sources[i,:counts[i]]=n.sources
        sources=remap_sources(sources,counts)
        net=replace(net,nodes=tuple(replace(n,sources=tuple(sources[i,:counts[i]])) for i,n in enumerate(net.nodes)))
        market_inputs=3*WIDTH;input_count=market_inputs+len(STATES);features=WIDTH
    if not reference and not any(n.op=='cross' for n in net.nodes):
        from research.vectorized_backtest.v60.calibration_cpu import observe as compiled
        return compiled(net,probe,stage,market_inputs,features)
    x=probe['inputs'];known=probe['known'];f=features
    memory=np.zeros((len(net.nodes),x.shape[0],x.shape[2]),np.float32)
    memory_known=np.zeros_like(memory,dtype=bool)
    outputs=[];valid=[];saturated=total=0
    for tick in range(x.shape[1]):
        values=[];masks=[]
        for node in net.nodes:
            z=np.full((x.shape[0],x.shape[2]),np.float32(node.bias));ok=np.zeros(z.shape,bool)
            terms=[]
            for source,w in zip(node.sources,node.weights):
                if w==0 and node.op in ('tanh','conv','identity'):continue
                if source<0:v=memory[-source-1];mask=memory_known[-source-1]
                elif source<market_inputs:v=x[:,tick,:,source];mask=known[:,tick,:,source%f] if source<2*f else False
                elif source<input_count:v=(x[:,tick,:,source] if stage=='entry' else probe['held_states'][:,tick,:,source-market_inputs]);mask=False
                else:v=values[source-input_count];mask=masks[source-input_count]
                term=v*np.float32(w);terms.append(term);z=z+term;ok|=mask if w!=0 else False
            from research.vectorized_backtest.v60.operators import evaluate
            if node.op=='cross':
                starts=probe.get('metadata',{}).get('segment_starts',[0])
                before=np.full_like(z,np.float32(node.bias));ok=np.ones_like(ok)&(tick not in starts)
                for source,w in zip(node.sources,node.weights):
                    before=before+x[:,max(0,tick-1),:,source]*np.float32(w)
                    ok&=known[:,tick,:,source]&known[:,max(0,tick-1),:,source]
                v=np.clip(np.minimum(z,-before),-1,1)
            else:v=np.tanh(z) if node.op in ('tanh','conv') else evaluate(node.op,terms,node.bias,z)
            values.append(v);masks.append(ok)
            saturated+=int(((np.abs(v)>.99)&ok).sum());total+=int(ok.sum())
        memory=np.asarray(values);memory_known=np.asarray(masks)
        outputs.append(values[-1]);valid.append(masks[-1])
    return np.stack(outputs,1),np.stack(valid,1),saturated,max(1,total)

def calibrate(rng,members,probe):
    """Fill every slot; failed proposals never enter the replay population."""
    from .genome import sample
    started=perf_counter();out=[];records=[];replacements=[]
    for slot,member in enumerate(members):
        for attempt in range(16):
            try:
                accepted,report=_calibrate(rng,[member],probe)
                break
            except ValueError as exc:
                replacements.append(dict(slot=slot,attempt=attempt,reason=str(exc)))
                member=sample(rng,1)[0]
        else:raise ValueError(f'Calibration replacement exhausted at slot {slot}')
        out.extend(accepted)
        records.extend(dict(r,candidate=slot) for r in report['records'])
    return out,dict(seconds=perf_counter()-started,probe_sha256=probe['sha256'],records=records,
        replacements=replacements,repaired_networks=sum(r['repair_attempts']>0 for r in records),
        unresolved_constant=0,validation_feedback=False)


def _calibrate(rng,members,probe):
    started=perf_counter();records=[];out=[]
    for i,member in enumerate(members):
        nets={}
        member,policy_repaired=calibrate_policy(member,probe)
        boundary=entry_boundary(member,probe)
        for stage,net in member.networks.items():
            route=int(rng.choice(4,p=RATE_WEIGHTS));attempts=0;failed=False;target=None
            if stage=='entry' and route==3:route=int(rng.choice(3))
            if route!=3:
                target=float(np.exp(rng.uniform(np.log(.001),np.log(.05)))) if route==0 else float(rng.uniform(*((.05,.30) if route==1 else (.30,.95))))
                for attempt in range(16):
                    values,valid,_,_=observe(net,probe,stage)
                    mask=valid & (boundary if stage=='entry' else True)
                    observed=values[mask]
                    if len(observed) and np.ptp(observed)>1e-4:break
                    if attempt==15:raise ValueError('Activation construction exhausted: '+stage)
                    # Preserve the allocated neuron count; no performance selection.
                    net=random_network(rng,stage,len(net.nodes));attempts+=1
                if not failed:
                    threshold=float(np.clip(np.quantile(observed,1-target),-.999999,.999999))
                    net=replace(net,threshold=threshold)
            nets[stage]=net
            records.append(dict(candidate=i,stage=stage,route=route,target_rate=target,
                repair_attempts=attempts,unresolved_constant=failed,
                boundary_opportunities=int(boundary.sum()) if stage=='entry' else None,
                joint_boundary_probe='ask' in probe,policy_repaired=policy_repaired))
        out.append(replace(member,networks=nets).validate())
    return out,dict(seconds=perf_counter()-started,probe_sha256=probe['sha256'],records=records,
        repaired_networks=sum(r['repair_attempts']>0 for r in records),
        unresolved_constant=sum(r['unresolved_constant'] for r in records),validation_feedback=False)


def calibrate_policy(member,probe):
    """Find a supported swing pair with causal opportunities, without fitness."""
    from .authority import SWING_WINDOWS,feasible_policy
    policy=feasible_policy(member.policy)
    member=replace(member,policy=policy)
    if entry_boundary(member,probe).any():return member,False
    if policy.swing_left and 'swings' in probe:
        ask=probe['ask'][...,None];stop=probe['swings']
        ready=(probe['eligible']&probe['quote_valid']&(probe['bid']>0)&(probe['bid']<=probe['ask']))[...,None]
        good=ready&np.isfinite(stop)&(stop>0)&(stop<ask)&(stop<ask*(1+policy.buy_limit_offset))
        choices=np.flatnonzero(good.any(axis=tuple(range(good.ndim-1))))
        if len(choices):
            # Nearest supported history pair, stable and independent of profit.
            old=SWING_WINDOWS.index(policy.swing_left)*len(SWING_WINDOWS)+SWING_WINDOWS.index(policy.swing_right)
            chosen=int(choices[np.argmin(abs(choices-old))]);left,right=divmod(chosen,len(SWING_WINDOWS))
            return replace(member,policy=replace(policy,swing_left=SWING_WINDOWS[left],swing_right=SWING_WINDOWS[right])),True
    raise ValueError('No causal entry boundary opportunity in training probe')


def entry_boundary(member,probe):
    """The flat-entry stop/book boundary; no financial-result filtering."""
    if 'ask' not in probe:return probe['eligible']
    from .authority import SWING_WINDOWS
    p=member.policy;ask=probe['ask'];bid=probe['bid']
    if p.swing_left:
        column=SWING_WINDOWS.index(p.swing_left)*len(SWING_WINDOWS)+SWING_WINDOWS.index(p.swing_right)
        stop=probe['swings'][...,column]
    else:stop=ask*(1-p.stop_fraction)
    return (probe['eligible']&probe['quote_valid']&np.isfinite(ask)&np.isfinite(bid)&(bid>0)&(ask>=bid)&
            np.isfinite(stop)&(stop>0)&(stop<ask)&(stop<ask*(1+p.buy_limit_offset)))

def policy_families(rng,members):
    """Overlapping initial priors only; mutation and execution bounds unchanged."""
    out=[];families=[]
    envelopes=((.0001,.04,.0001,.10,0.,.05,1,60),
               (.002,.12,.002,.40,0.,.25,15,180),
               (.01,.20,.01,1.,0.,1.,60,300))
    routes=rng.permutation(np.arange(len(members))%2)
    for i,m in enumerate(members):
        if routes[i]==0:out.append(m);families.append('independent');continue
        family=int(rng.integers(3));s0,s1,t0,t1,a0,a1,c0,c1=envelopes[family]
        # Stop/target scales covary, with independent jitter spanning the family.
        scale=float(rng.random())
        stop=float(np.exp(np.log(s0)+(np.log(s1)-np.log(s0))*scale))
        target=float(np.exp(np.log(t0)+(np.log(t1)-np.log(t0))*np.clip(scale+rng.normal(0,.2),0,1)))
        policy=replace(m.policy,stop_fraction=stop,target_fraction=target,
            trail_fraction=float(np.exp(rng.uniform(np.log(s0),np.log(t1)))),
            add_minimum_profit=float(rng.uniform(a0,a1)),reduce_minimum_profit=float(rng.uniform(a0,a1)),
            cooldown=int(rng.integers(c0,c1+1)))
        ages={}
        from .authority import feasible_policy
        out.append(replace(m,policy=feasible_policy(policy),minimum_age=ages).validate());families.append(('short','medium','long')[family])
    return out,families
