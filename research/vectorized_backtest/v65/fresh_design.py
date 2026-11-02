"""Training-probe diversity screening, never profit fitting or validation selection.

Templates compile into the same mutable graph and price genes as random members.
Oversampling is bounded CPU construction; only 1024 selected founders get replayed.
"""
from collections import Counter
from dataclasses import replace
from hashlib import sha256
from math import tanh
from time import perf_counter
import numpy as np

VERSION='mixed-probe-diversity-v1'
FAMILIES=('ema_cross','macd_signal','vwap_reclaim','rsi_recovery',
          'high_breakout','candle_agreement','relative_strength','activity_surge')
SETTINGS=dict(version=VERSION,population=1024,initial_proposals=2048,maximum_proposals=6144,
    maximum_template_fraction=.375,maximum_per_template_family=48,
    maximum_identical_request_patterns=2,immigrant_template_probability=.375,
    selection='balanced templates then farthest training-probe action patterns',
    fitness_selection=False,validation_feedback=False)
_BITS=np.array([int(i).bit_count() for i in range(256)],np.uint8)


def template(rng,family):
    from .authority import BY_NAME,construct
    from .genome import Individual,Neuron,Network,STAGES
    from .policy import Policy
    from .price_program import Prices,Expression
    def node(names,weights,*,bias=0.,op='cross'):
        return Network((Neuron(tuple(BY_NAME[n].id for n in names),tuple(weights),float(bias),
            int(rng.integers(1,2**63-1)),op),),threshold=0.,size_weight=0.,size_bias=8.).validate()
    if family=='ema_cross':
        fast=int(rng.choice([3,7,12,20]));slow=int(rng.choice([26,50,60]))
        names=[f'indicator.ema.{fast}.relative',f'indicator.ema.{slow}.relative'];weights=[1.,-1.];bias=0.;op='cross'
    elif family=='macd_signal':
        f=int(rng.choice([3,7,12]));s=int(rng.choice([15,26,50]));q=int(rng.choice([5,9,20]))
        names=[f'indicator.macd_histogram.{f}.{s}.{q}.relative'];weights=[1.];bias=0.;op='cross'
    elif family=='vwap_reclaim':
        names=['session_vwap_rel'];weights=[-1.];bias=tanh(float(rng.choice([0.,-.001,-.003,.001])));op='cross'
    elif family=='rsi_recovery':
        p=int(rng.choice([3,7,14,26]));names=[f'indicator.rsi.{p}'];weights=[1.]
        bias=-tanh(float(rng.choice([.2,.3,.4,.5])));op='cross'
    elif family=='high_breakout':
        resolution=int(rng.choice([10000,60000,3600000,86400000]));lag=int(rng.choice([1,2,3]))
        names=[f'arte.trade.{resolution}ms.lag{lag}.high'];weights=[-1.];bias=0.;op='cross'
    elif family=='candle_agreement':
        resolution=int(rng.choice([10000,60000,3600000,86400000]));lag=int(rng.choice([3,5,10]))
        names=[f'arte.trade.{resolution}ms.lag1.close',f'arte.trade.{resolution}ms.lag{lag}.close']
        weights=[-1.,-1.];bias=0.;op='min'
    elif family=='relative_strength':
        h=int(rng.choice([15,60,300]));names=[f'enriched.context.{h}s.relative_return']
        weights=[1.];bias=0.;op='identity'
    elif family=='activity_surge':
        names=['enriched.activity.trade_acceleration','enriched.return.5s'];weights=[1.,1.];bias=0.;op='min'
    else:raise ValueError('Unknown fresh template family')
    entry=node(names,weights,bias=bias,op=op)
    exit=node(names,[-w for w in weights],bias=-bias,op='cross' if op=='cross' else 'max')
    disabled=node(['enriched.return.5s'],[1.],bias=-2.,op='tanh')
    disabled=replace(disabled,threshold=.99)
    nets={s:disabled for s in STAGES};nets.update(entry=entry,exit=exit)
    stop=float(np.exp(rng.uniform(np.log(.005),np.log(.05))))
    target=stop*float(rng.choice([1.,1.5,2.,3.]))
    prices=Prices(stop=Expression('ask',offset=-stop),target=Expression('ask',offset=target),
                  update=Expression('close',offset=-stop*.75))
    return construct(Individual(nets,Policy(price_program=prices.payload()),{}))


def request_pattern(member,probe):
    from .calibration import observe,action_boundaries
    from .genome import STAGES
    boundaries=action_boundaries(member,probe);bits=[];rates=[]
    for stage in STAGES:
        values,known,_,_=observe(member.networks[stage],probe,stage)
        boundary=boundaries[stage]
        active=known & boundary & (values>member.networks[stage].threshold)
        if not np.isfinite(values[known]).all():raise ValueError('Nonfinite activation')
        bits.append(np.packbits(active.reshape(-1)))
        rates.append(float(active.sum()/max(1,boundary.sum())))
        if stage=='entry' and (not active.any() or active.sum()==boundary.sum()):
            raise ValueError('Entry must be active and selective on the training probe')
    return np.stack(bits),rates


def diverse_indices(patterns,families,keys,size=1024,template_cap=384,family_cap=48):
    """Vectorized Jaccard updates, linear memory, deterministic proposal tie order."""
    if len(patterns)<size:raise ValueError('Insufficient viable proposals')
    patterns=np.asarray(patterns,np.uint8)
    if patterns.ndim!=3 or patterns.shape[1]!=5:raise ValueError('Five action patterns required')
    counts=_BITS[patterns].sum(2,dtype=np.int32)
    distance=np.ones(len(patterns),np.float32);available=np.ones(len(patterns),bool)
    fingerprints=[sha256(p.tobytes()).digest() for p in patterns]
    groups={}
    for i,key in enumerate(fingerprints):groups.setdefault(key,[]).append(i)
    used=Counter();seen=set();selected=[];weights=np.array([.5,.2,.1,.1,.1])
    families=np.asarray(families)
    def take(mask):
        candidates=np.flatnonzero(available & mask)
        if not len(candidates):return False
        index=int(candidates[np.argmax(distance[candidates])]);selected.append(index)
        available[index]=False;seen.add(keys[index]);used[fingerprints[index]]+=1
        if used[fingerprints[index]]>=2:available[groups[fingerprints[index]]]=False
        # Identical genes cannot consume multiple slots, even with new node IDs.
        for i,key in enumerate(keys):
            if key in seen:available[i]=False
        intersections=_BITS[np.bitwise_and(patterns,patterns[index])].sum(2,dtype=np.int32)
        unions=counts+counts[index]-intersections
        similarity=np.divide(intersections,unions,out=np.ones_like(unions,dtype=np.float64),where=unions>0)
        distance[:]=np.minimum(distance,(1-similarity)@weights)
        return True
    for _ in range(family_cap):
        for family in FAMILIES:
            if len(selected)<min(template_cap,size):take(families==family)
    while len(selected)<size:
        if not take(families=='random'):raise ValueError('Insufficient distinct random proposals')
    return selected,dict(request_patterns=len({fingerprints[i] for i in selected}),
        largest_request_pattern=max(Counter(fingerprints[i] for i in selected).values()),
        families=dict(Counter(str(families[i]) for i in selected)))


def initialize(rng,size,probe):
    if size!=1024 or probe is None:raise ValueError('1024 founders and training probe required')
    from concurrent.futures import ThreadPoolExecutor
    from .genome import sample
    from .calibration import _calibrate
    from .initial_population import validate_fresh
    from .lineage import program_key
    started=perf_counter();members=[];families=[];patterns=[];rates=[];keys=[];rejected=Counter();proposed=0
    for round_index in range(3):
        random=sample(rng,1280,stratified=True,structural=True)
        seeds=rng.integers(1,2**63-1,size=2048,dtype=np.int64)
        def build(index):
            local=np.random.default_rng(int(seeds[index]));family='random' if index<1280 else FAMILIES[(index-1280)%len(FAMILIES)]
            try:
                if family=='random':member=_calibrate(local,[random[index]],probe)[0][0]
                else:member=template(local,family)
                validate_fresh(member);pattern,rate=request_pattern(member,probe)
                return member,family,pattern,rate,None
            except ValueError as error:return None,family,None,None,str(error)
        with ThreadPoolExecutor(max_workers=4,thread_name_prefix='v65-founder-proposals') as pool:
            results=list(pool.map(build,range(2048)))
        proposed+=len(results)
        for member,family,pattern,rate,error in results:
            if error:rejected[family+': '+error]+=1;continue
            members.append(member);families.append(family);patterns.append(pattern);rates.append(rate);keys.append(program_key(member))
        try:chosen,diversity=diverse_indices(patterns,families,keys,size)
        except ValueError:
            if round_index==2:raise
            continue
        selected=[members[i] for i in chosen];selected_families=[families[i] for i in chosen]
        return selected,dict(experiment='v65',initialization=VERSION,settings=SETTINGS,
            proposed=proposed,accepted_proposals=len(members),rejected=dict(rejected),
            discarded_viable_proposals=len(members)-size,construction={'accepted':size},
            seed_count=sum(f!='random' for f in selected_families),random_count=selected_families.count('random'),
            initial_families=selected_families,diversity=diversity,
            activation_rates=[rates[i] for i in chosen],probe_sha256=probe['sha256'],
            past_results_loaded=False,validation_feedback=False,fitness_selection=False,seconds=perf_counter()-started)
    raise AssertionError('Unreachable proposal budget')


def immigrant(rng,probe):
    """Small independent proposals; the same mixture refreshes later generations."""
    from .initial_population import random_immigrant,validate_fresh
    for _ in range(16):
        family=FAMILIES[int(rng.integers(len(FAMILIES)))] if rng.random()<.375 else 'random'
        try:
            member=random_immigrant(rng,probe) if family=='random' else validate_fresh(template(rng,family))
            request_pattern(member,probe)
            # Observer-only provenance, deliberately absent from genome hashing.
            # Birth records persist it; inherited children use parental ancestry.
            object.__setattr__(member,'_fresh_family',family)
            return member
        except ValueError:continue
    raise ValueError('Mixed fresh immigrant construction exhausted')
