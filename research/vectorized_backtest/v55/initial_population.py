"""Fresh shared-core founders with feature-family priors, never old winners."""
from dataclasses import replace
import numpy as np
from .genome import sample,MARKET_INPUTS,INPUTS,HIDDEN,Branch,Head,CATALOG
from .identity_cache import program_key
from research.vectorized_backtest.v42.templates import ALLOCATIONS

def calibrate(rng,member,probe,diagnostics=None):
    if probe is None:raise ValueError('Sealed training activation probe required')
    flat=probe['inputs'].reshape(-1,INPUTS);eligible=probe['eligible'].reshape(-1)
    ix=np.flatnonzero(eligible);ix=ix[np.linspace(0,len(ix)-1,min(64,len(ix)),dtype=int)]
    n=member.networks['shared'];heads=list(n.heads)
    for held in (False,True):
        x=flat[ix].copy()
        if held:x[:,MARKET_INPUTS:]=probe['held_states'].reshape(-1,INPUTS-MARKET_INPUTS)[ix]
        bank=np.concatenate((x,np.zeros((len(ix),len(n.nodes)),np.float32)),1)
        src=np.array([list(y.sources)+[0]*(8-len(y.sources)) for y in n.nodes]);weights=np.array([list(y.weights)+[0.]*(8-len(y.weights)) for y in n.nodes],np.float32)
        src=np.where(src<0,INPUTS-src-1,src);z=np.array([y.bias for y in n.nodes],np.float32)[None].repeat(len(ix),0)
        known=probe['known'].reshape(-1,len(CATALOG))[ix]
        masks=np.concatenate((known,known,np.zeros((len(ix),len(CATALOG)+INPUTS-MARKET_INPUTS+len(n.nodes)),bool)),1)
        good=np.zeros_like(z,dtype=bool)
        for e in range(8):
            z+=bank[:,src[:,e]]*weights[:,e];good|=masks[:,src[:,e]]&(weights[:,e]!=0)
        h=np.tanh(z)
        for a in ((1,2,3,4) if held else (0,)):
            branch=heads[a].branches[0];v=np.full(len(ix),np.float32(branch.bias))
            available=np.zeros(len(ix),bool)
            for s,w in zip(branch.sources,branch.weights):
                v+=h[:,s]*np.float32(w);available|=good[:,s]&(w!=0)
            v=np.tanh(v)
            target=float(np.exp(rng.uniform(np.log(.005),np.log(.30))))
            observed=v[available]
            if not len(observed) or np.ptp(observed)<=1e-4:
                if diagnostics is not None:diagnostics['unresolved_heads']+=1
                continue
            threshold=float(np.clip(np.quantile(observed,1-target),-.999999,.999999))
            heads[a]=replace(heads[a],branches=(replace(branch,threshold=threshold),))
    return replace(member,networks={'shared':replace(n,heads=tuple(heads))}).validate()

def immigrant(rng,probe):return calibrate(rng,sample(rng,1)[0],probe)

def initialize(rng,size,*,probe=None):
    if size!=6144:raise ValueError('V55 requires 3072 feature-family priors plus 3072 random founders')
    members=sample(rng,size);families=[];position=0
    terms={'ema_cross':'ema','trend_pullback':'ema','vwap_reclaim':'vwap','vwap_recovery':'vwap','high_breakout':'high','compression_breakout':'range','support_bounce':'low','relative_strength':'return','activity_surge':'volume','candle_agreement':'close','rsi_recovery':'rsi','macd_signal':'macd','macd_zero':'macd'}
    for family,count in ALLOCATIONS.items():
        candidates=[i for i,f in enumerate(CATALOG) if terms[family] in f.name.lower()]
        if not candidates:raise ValueError('No catalog features for '+family)
        for j in range(count):
            m=members[position];n=m.networks['shared'];nodes=list(n.nodes)
            # Common temporal feature bank: current level plus preceding latent.
            # All six heads still read this one bank; no action-owned subcores.
            for k in range(16):
                feature=candidates[(j+k)%len(candidates)];feature+=len(CATALOG)*(k%2)
                nodes[k]=replace(nodes[k],sources=(feature,-k-1),weights=(float((1 if k%4<2 else -1)*rng.uniform(.4,1.)),float(rng.uniform(.1,.8))),bias=0.)
            heads=list(n.heads);branch=heads[0].branches[0]
            heads[0]=replace(heads[0],branches=(replace(branch,sources=tuple(range(8)),weights=tuple(float(x) for x in rng.uniform(.2,.8,8))),))
            members[position]=replace(m,networks={'shared':replace(n,nodes=tuple(nodes),heads=tuple(heads))});families.append(family);position+=1
    if position!=3072:raise ValueError('Template allocation changed')
    families+=['random']*3072
    diagnostics=dict(unresolved_heads=0)
    members=[calibrate(rng,m,probe,diagnostics) for m in members]
    if len({program_key(m) for m in members})!=size:raise ValueError('Duplicate founders')
    return members,dict(experiment='v55',random_count=3072,seed_count=3072,initial_families=families,past_results_loaded=False,
        initialization='fresh initially 64-unit evolving shared RNN; 3072 feature-family priors and 3072 random; architecture differs from V53',
        calibration='64 training-eligible reset-state cross-sections; flat/held channels; known-market-dependent heads only; activation only; no fitness',
        calibration_diagnostics=diagnostics,probe_sha256=probe['sha256'],validation_feedback=False)
