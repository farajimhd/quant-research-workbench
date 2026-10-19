"""Finite training-only activation calibration; no fitness or validation input."""
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter
import numpy as np
from .genome import STAGES, INPUTS, MARKET_INPUTS, random_network
from .features import CATALOG

PROBE_VERSION='v32-training-activation-probe-v1'
RATE_WEIGHTS=(.30,.30,.20,.20)

def load_probe(path,training):
    """The immutable probe must name this exact training manifest and receipts."""
    with np.load(path,allow_pickle=False) as z:
        meta=json.loads(str(z['metadata']))
        if meta.get('version')!=PROBE_VERSION or meta.get('training_sha256')!=sha256(Path(training).read_bytes()).hexdigest():
            raise ValueError('Calibration training identity mismatch')
        manifest=json.loads(Path(training).read_text(encoding='utf-8-sig'))
        if not meta.get('days') or any(d not in manifest['days'] for d in meta['days']) or meta.get('validation_opened') is not False:
            raise ValueError('Calibration must use training only')
        if meta['receipts']!={d:manifest['receipts'][d] for d in meta['days']}:
            raise ValueError('Calibration receipt binding mismatch')
        x=np.array(z['inputs']);known=np.array(z['known']);eligible=np.array(z['eligible']);held=np.array(z['held_states'])
    if x.ndim!=4 or x.shape[-1]!=INPUTS or x.dtype!=np.float32 or not np.isfinite(x).all() or np.abs(x).max()>1:
        raise ValueError('Bounded normalized activation inputs required')
    if known.shape!=x.shape[:-1]+(len(CATALOG),) or eligible.shape!=x.shape[:-1] or known.dtype!=bool or eligible.dtype!=bool:
        raise ValueError('Calibration mask shape/type mismatch')
    if held.shape!=x.shape[:-1]+(INPUTS-MARKET_INPUTS,) or held.dtype!=np.float32 or not np.isfinite(held).all() or np.abs(held).max()>1:
        raise ValueError('Representative held-state probe required')
    if not 1<=x.shape[0]<=8 or not 2<=x.shape[1]<=64 or not 1<=x.shape[2]<=128 or not eligible.any():
        raise ValueError('Finite calibration envelope exceeded')
    for name,array in dict(inputs=x,known=known,eligible=eligible,held_states=held).items():
        if meta.get('slice_sha256',{}).get(name)!=sha256(array.tobytes()).hexdigest():raise ValueError('Calibration slice bytes changed')
    return dict(inputs=x,known=known,eligible=eligible,held_states=held,metadata=meta,sha256=sha256(Path(path).read_bytes()).hexdigest())

def observe(net,probe,stage='entry',*,reference=False):
    """Float32 CPU graph arithmetic. Recurrence resets per independent window."""
    if not reference:
        from .calibration_cpu import observe as compiled
        return compiled(net,probe,stage,MARKET_INPUTS,len(CATALOG))
    x=probe['inputs'];known=probe['known'];f=len(CATALOG)
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
                elif source<MARKET_INPUTS:v=x[:,tick,:,source];mask=known[:,tick,:,source%f] if source<2*f else False
                elif source<INPUTS:v=(x[:,tick,:,source] if stage=='entry' else probe['held_states'][:,tick,:,source-MARKET_INPUTS]);mask=False
                else:v=values[source-INPUTS];mask=masks[source-INPUTS]
                term=v*np.float32(w);terms.append(term);z=z+term;ok|=mask if w!=0 else False
            from .operators import evaluate
            v=np.tanh(z) if node.op in ('tanh','conv') else evaluate(node.op,terms,node.bias,z);values.append(v);masks.append(ok)
            saturated+=int(((np.abs(v)>.99)&ok).sum());total+=int(ok.sum())
        memory=np.asarray(values);memory_known=np.asarray(masks)
        outputs.append(values[-1]);valid.append(masks[-1])
    return np.stack(outputs,1),np.stack(valid,1),saturated,max(1,total)

def calibrate(rng,members,probe):
    started=perf_counter();records=[];out=[]
    for i,member in enumerate(members):
        nets={}
        for stage,net in member.networks.items():
            route=int(rng.choice(4,p=RATE_WEIGHTS));attempts=0;failed=False;target=None
            if route!=3:
                target=float(np.exp(rng.uniform(np.log(.001),np.log(.05)))) if route==0 else float(rng.uniform(*((.05,.30) if route==1 else (.30,.95))))
                for attempt in range(3):
                    values,valid,_,_=observe(net,probe,stage)
                    mask=valid & (probe['eligible'] if stage=='entry' else True)
                    observed=values[mask]
                    if len(observed) and np.ptp(observed)>1e-4:break
                    if attempt==2:failed=True;break
                    # Preserve the allocated neuron count; no performance selection.
                    net=random_network(rng,stage,len(net.nodes));attempts+=1
                if not failed:
                    threshold=float(np.clip(np.quantile(observed,1-target),-.999999,.999999))
                    net=replace(net,threshold=threshold)
            nets[stage]=net
            records.append(dict(candidate=i,stage=stage,route=route,target_rate=target,
                repair_attempts=attempts,unresolved_constant=failed))
        out.append(replace(member,networks=nets).validate())
    return out,dict(seconds=perf_counter()-started,probe_sha256=probe['sha256'],records=records,
        repaired_networks=sum(r['repair_attempts']>0 for r in records),
        unresolved_constant=sum(r['unresolved_constant'] for r in records),validation_feedback=False)

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
        ages={s:0 if rng.random()<.5 else int(rng.integers(c0,c1+1)) for s in STAGES if s!='entry'}
        out.append(replace(m,policy=policy,minimum_age=ages).validate());families.append(('short','medium','long')[family])
    return out,families
