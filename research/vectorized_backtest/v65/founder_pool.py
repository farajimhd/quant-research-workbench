"""Sixfold training-only founder construction; no financial screening."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import numpy as np

SETTINGS=dict(version='full-replay-sixfold-v2',size=6144,random=3072,template=1536,
              combination=1536,maximum_proposals=98304,survivors=1024,
              quality_survivors=768,exploration_survivors=256,maximum_action_nodes=3)


def combination(rng,family,index):
    from .fresh_design import template
    from .genome import Neuron,Network,INPUTS
    from .authority import BY_NAME,construct
    member=template(rng,family)
    filters=('enriched.activity.trade_acceleration','market.spread_fraction',
             'enriched.return.5s','enriched.context.60s.relative_return')
    name=filters[index%len(filters)];sign=-1. if name=='market.spread_fraction' else 1.
    trigger=member.networks['entry'].nodes[0]
    context=Neuron((BY_NAME[name].id,),(sign,),float(rng.uniform(-.05,.05)),
                   int(rng.integers(1,2**63-1)),'identity')
    gate=Neuron((INPUTS,INPUTS+1),(1.,1.),0.,int(rng.integers(1,2**63-1)),'min')
    entry=Network((replace(trigger,slot=0),context,gate),threshold=0.,size_weight=0.,size_bias=8.)
    return construct(replace(member,networks=dict(member.networks,entry=entry)))


def validate(member):
    member.validate()
    if any(not 1<=len(n.nodes)<=3 for n in member.networks.values()):
        raise ValueError('Founder action graph exceeds three nodes')
    return member


def initialize(rng,probe,progress=None):
    from .fresh_design import FAMILIES,template,request_pattern
    from .genome import sample
    from .calibration import _calibrate
    from .lineage import program_key
    from hashlib import sha256
    quotas={'random':3072,**{f:192 for f in FAMILIES},**{'combination:'+f:192 for f in FAMILIES}}
    members=[];families=[];seen=set();patterns=Counter();counts=Counter();rejected=Counter();proposed=0;discarded=0
    # Fixed per-proposal RNG streams and ordered collection make worker timing irrelevant.
    with ThreadPoolExecutor(max_workers=4,thread_name_prefix='v65-sixfold') as pool:
        while len(members)<6144 and proposed<SETTINGS['maximum_proposals']:
            requested=[]
            for family,quota in quotas.items():
                requested.extend((family,j) for j in range(min(256,2*(quota-counts[family]))))
            requested=requested[:SETTINGS['maximum_proposals']-proposed]
            seeds=rng.integers(1,2**63-1,len(requested),dtype=np.int64)
            def build(args):
                (family,index),seed=args;local=np.random.default_rng(int(seed))
                try:
                    if family=='random':
                        m=_calibrate(local,sample(local,1),probe,selective=True)[0][0]
                    elif family.startswith('combination:'):m=combination(local,family.split(':')[1],index)
                    else:m=template(local,family)
                    validate(m);pattern,rates=request_pattern(m,probe)
                    return m,program_key(m),sha256(pattern.tobytes()).hexdigest(),None
                except ValueError as e:return None,None,None,str(e)
            pending={f:[] for f in quotas};local_seen=set()
            for (family,_),(m,key,pattern,error) in zip(requested,pool.map(build,zip(requested,seeds))):
                proposed+=1
                if error:rejected[family+': '+error]+=1;continue
                if key in seen or key in local_seen:rejected['duplicate genome']+=1;continue
                local_seen.add(key);pending[family].append((m,key,pattern))
            for family,choices in pending.items():
                accepted=rarity_indices([v[2] for v in choices],patterns,quotas[family]-counts[family])
                discarded+=len(choices)-len(accepted)
                for index in accepted:
                    m,key,pattern=choices[index]
                    seen.add(key);members.append(m);families.append(family);counts[family]+=1;patterns[pattern]+=1
            if progress:progress(len(members),6144)
    if len(members)!=6144:raise ValueError(f'Founder budget exhausted: {len(members)}/6144; {dict(rejected)}')
    return members,dict(settings=SETTINGS,proposed=proposed,rejected=dict(rejected),discarded_viable=discarded,families=dict(counts),
        initial_families=families,probe_patterns=len(patterns),largest_probe_pattern=max(patterns.values()),
        probe_sha256=probe['sha256'],validation_feedback=False,fitness_selection=False)


def rarity_indices(signatures,prior,limit):
    """Soft coverage preference; identical patterns remain admissible when needed."""
    import heapq
    from collections import deque
    groups={}
    for i,key in enumerate(signatures):groups.setdefault(key,deque()).append(i)
    heap=[(prior[key],indices[0],key) for key,indices in groups.items()];heapq.heapify(heap)
    chosen=[]
    while heap and len(chosen)<limit:
        count,_,key=heapq.heappop(heap);chosen.append(groups[key].popleft())
        if groups[key]:heapq.heappush(heap,(count+1,groups[key][0],key))
    return chosen


def seed(job,probe,progress=None):
    from .local_runner import CompactPedigree
    from .family_stats import seed as annotate
    rng=np.random.default_rng(job['seed']);members,report=initialize(rng,probe,progress)
    pedigree=CompactPedigree(3);ids=pedigree.birth(members,0,[(-1,-1)]*len(members),['fresh']*len(members))
    annotate(pedigree,ids,['combination' if f.startswith('combination:') else f for f in report['initial_families']])
    return dict(population=[m.payload() for m in members],ids=ids.tolist(),pedigree=list(pedigree.records.values()),
        next_serial=pedigree.next_serial,rng=rng.bit_generator.state,initialization=report,sources=[None]*len(members),
        validation_opened=False,source_sha256=job['source_sha256'])
