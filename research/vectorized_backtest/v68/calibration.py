"""Finite training-only activation calibration; no fitness or validation input."""
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter
import numpy as np
from .genome import STAGES, INPUTS, MARKET_INPUTS, random_network
from research.vectorized_backtest.v68.features import CATALOG

PROBE_VERSION='v42-training-activation-probe-v1'
RATE_WEIGHTS=(.30,.30,.20,.20)


def activation_threshold(observed,target):
    """Choose an attainable strict cutoff, including discrete/tied outputs."""
    observed=np.asarray(observed,np.float32)
    if not len(observed) or not np.isfinite(observed).all() or np.ptp(observed)<=1e-4:
        raise ValueError('No varying finite activation observations')
    values,counts=np.unique(observed,return_counts=True)
    # A cutoff halfway between distinct values avoids CPU/GPU endpoint rounding.
    rates=(len(observed)-np.cumsum(counts)[:-1])/len(observed)
    usable=np.flatnonzero(np.diff(values)>2e-6)
    if not len(usable):raise ValueError('No numerically separated activation cutoff')
    index=int(usable[np.argmin(np.abs(rates[usable]-target))])
    threshold=np.float32((float(values[index])+float(values[index+1]))*.5)
    threshold=np.maximum(np.float32(-.999999),np.minimum(np.float32(.999999),threshold))
    rate=float(np.mean(observed>threshold))
    if not 0<rate<1:raise ValueError('Unattainable activation threshold')
    # Tied/discrete outputs can make the nearest cutoff radically unlike the
    # requested behavior. Repair the graph rather than silently changing it.
    tolerance=max(1./len(observed),min(.02,.25*target))
    if abs(rate-target)>tolerance:
        raise ValueError('Activation rate outside construction tolerance')
    return float(threshold),rate

def load_probe(path,training):
    from research.vectorized_backtest.v68.probe_reader import load_probe as load
    if Path(path).suffix=='.json':
        from .calibration_probe import load as load_memory
        original=dict(load_memory(str(path),str(training),Path(path).stat().st_mtime_ns))
    else:raise ValueError('V67 requires a top-five in-memory calibration recipe')
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
        from research.vectorized_backtest.v68.calibration_cpu import observe as compiled
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
            from research.vectorized_backtest.v68.operators import evaluate
            if node.op=='cross':
                starts=probe.get('metadata',{}).get('segment_starts',[0])
                before=np.full_like(z,np.float32(node.bias));ok=np.ones_like(ok)
                for source,w in zip(node.sources,node.weights):
                    before=before+probe['lag_absolute'][:,tick,:,source]*np.float32(w)
                    ok&=known[:,tick,:,source]&probe['lag_known'][:,tick,:,source]
                v=np.clip(np.minimum(z,-before),-1,1)
            else:v=np.tanh(z) if node.op in ('tanh','conv') else evaluate(node.op,terms,node.bias,z)
            values.append(v);masks.append(ok)
            saturated+=int(((np.abs(v)>.99)&ok).sum());total+=int(ok.sum())
        memory=np.asarray(values);memory_known=np.asarray(masks)
        outputs.append(values[-1]);valid.append(masks[-1])
    return np.stack(outputs,1),np.stack(valid,1),saturated,max(1,total)

def calibrate(rng,members,probe,*,selective=False):
    """Fill every slot; failed proposals never enter the replay population."""
    from .genome import sample
    from concurrent.futures import ThreadPoolExecutor
    started=perf_counter();out=[];records=[];replacements=[]
    seeds=rng.integers(0,2**63-1,size=len(members),dtype=np.int64)
    def construct(item):
        slot,member=item;local=np.random.default_rng(int(seeds[slot]));rejected=[]
        for attempt in range(16):
            try:
                accepted,report=_calibrate(local,[member],probe,selective=selective)
                break
            except ValueError as exc:
                rejected.append(dict(slot=slot,attempt=attempt,reason=str(exc)))
                member=sample(local,1)[0]
        else:raise ValueError(f'Calibration replacement exhausted at slot {slot}')
        return slot,accepted,report,rejected
    # Numba scans release the GIL; fixed per-slot seeds preserve scheduling parity.
    with ThreadPoolExecutor(max_workers=min(4,max(1,len(members))),thread_name_prefix='v60-calibrate') as pool:
        results=list(pool.map(construct,enumerate(members)))
    for slot,accepted,report,rejected in results:
        out.extend(accepted)
        replacements.extend(rejected)
        records.extend(dict(r,candidate=slot) for r in report['records'])
    return out,dict(seconds=perf_counter()-started,probe_sha256=probe['sha256'],records=records,
        replacements=replacements,repaired_networks=sum(r['repair_attempts']>0 for r in records),
        unresolved_constant=0,validation_feedback=False)


def _calibrate(rng,members,probe,*,selective=False):
    started=perf_counter();records=[];out=[]
    for i,member in enumerate(members):
        nets={}
        member,policy_repaired=calibrate_policy(member,probe)
        boundary=entry_boundary(member,probe)
        boundaries=action_boundaries(member,probe)
        for stage,net in member.networks.items():
            latent=selective and stage in ('add','reduce','trail') and rng.random()<.6
            if latent:
                # An initially inactive but market-dependent management head.
                # This is a mutable starting point, never an execution rule.
                last=net.nodes[-1]
                last=replace(last,op='tanh',bias=-2.,weights=tuple(float(w)*.05 for w in last.weights))
                net=replace(net,nodes=(*net.nodes[:-1],last),threshold=.99)
                route=3
            elif selective:
                route=int(rng.choice(3,p=(.6,.3,.1) if stage=='entry' else (.4,.4,.2) if stage=='exit' else (.6,.4,0.)))
            else:route=int(rng.choice(4,p=RATE_WEIGHTS))
            attempts=0;failed=False;target=None
            if stage=='entry' and route==3:route=int(rng.choice(3))
            if route!=3:
                if selective:
                    ranges=((.001,.01),(.01,.03),(.03,.10)) if stage=='entry' else ((.002,.03),(.03,.10),(.10,.30)) if stage=='exit' else ((.001,.01),(.01,.05),(.01,.05))
                    low,high=ranges[route];target=float(np.exp(rng.uniform(np.log(low),np.log(high))))
                else:target=float(np.exp(rng.uniform(np.log(.001),np.log(.05)))) if route==0 else float(rng.uniform(*((.05,.30) if route==1 else (.30,.95))))
                realized=None
                for attempt in range(16):
                    values,valid,_,_=observe(net,probe,stage)
                    mask=valid & boundaries.get(stage,True)
                    observed=values[mask]
                    try:
                        threshold,realized=activation_threshold(observed,target)
                        break
                    except ValueError:pass
                    if attempt==15:raise ValueError('Activation construction exhausted: '+stage)
                    # Preserve the allocated neuron count; no performance selection.
                    net=random_network(rng,stage,len(net.nodes));attempts+=1
                if not failed:
                    net=replace(net,threshold=threshold)
            nets[stage]=net
            records.append(dict(candidate=i,stage=stage,route=route,target_rate=target,
                repair_attempts=attempts,unresolved_constant=failed,
                boundary_opportunities=int(boundary.sum()) if stage=='entry' else None,
                realized_rate=realized if route!=3 else 0. if latent else None,
                selective_construction=selective,initially_inactive=latent,
                joint_boundary_probe='ask' in probe,policy_repaired=policy_repaired))
        out.append(replace(member,networks=nets).validate())
    return out,dict(seconds=perf_counter()-started,probe_sha256=probe['sha256'],records=records,
        repaired_networks=sum(r['repair_attempts']>0 for r in records),
        unresolved_constant=sum(r['unresolved_constant'] for r in records),validation_feedback=False)


def calibrate_policy(member,probe):
    """Apply construction before activation fitting; never require a lucky pivot."""
    from .authority import feasible_policy
    policy=feasible_policy(member.policy)
    changed=policy!=member.policy
    member=replace(member,policy=policy)
    if entry_boundary(member,probe).any():return member,changed
    raise ValueError('No causal quoted entry opportunity in training probe')


def entry_boundary(member,probe):
    """The flat-entry stop/book boundary; no financial-result filtering."""
    if 'ask' not in probe:return probe['eligible']
    from .price_program import Prices
    if 'price_references' not in probe:raise ValueError('Actual price references required for joint calibration')
    prices,guard=Prices.restore(member.policy.price_program).evaluate(probe['price_references'])
    # Marketability and whole-share liquidity are part of the joint opportunity,
    # not a change to the cost or an execution fallback.
    return probe['eligible'] & probe['quote_valid'] & guard & (prices[0]>=probe['ask']) & (probe['ask_size']>=1)


def action_boundaries(member,probe):
    """Market-only feasibility; no inventory, fills or financial feedback."""
    if 'price_references' not in probe:return {'entry':entry_boundary(member,probe)}
    from .price_program import Prices
    from .authority import FIXED_ADD_DOLLARS
    prices,_=Prices.restore(member.policy.price_program).evaluate(probe['price_references'])
    buy,sell,_,_,update=prices
    eligible=probe['eligible']
    sell_ok=eligible & np.isfinite(sell) & (sell>0)
    return dict(entry=entry_boundary(member,probe),exit=sell_ok,reduce=sell_ok,
                add=eligible & np.isfinite(buy) & (buy>0) & (buy<=FIXED_ADD_DOLLARS),
                trail=eligible & np.isfinite(update) & (update>0) & (update<probe['price_references']['close']))


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
