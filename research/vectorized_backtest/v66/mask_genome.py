"""Typed sparse mask genome. Stable slots align genes, never execution indices.

Topology masks are packed for Boolean crossover; floating parameters stay float64.
The sparse replay ABI is retained, so no dense masked-zero work is added to CUDA.
"""
from dataclasses import replace, fields
import heapq
import numpy as np
from .genome import Neuron,Network,Individual,Policy,STAGES,INPUTS,prune
from .sampling_report import COUNTS

MODES=('uniform','union','intersection','xor')

def slots(net):
    result=[n.slot or (2**64+n.uid) for n in net.nodes]
    if len(set(result))!=len(result):raise ValueError('Duplicate persistent slots')
    return result

def encode(net):
    from .validation_cache import topology
    return topology(net,lambda:_encode(net))


def _encode(net):
    ids=slots(net)
    def key(s):return ('delay',ids[-s-1]) if s<0 else ('input',s) if s<INPUTS else ('node',ids[s-INPUTS])
    return {slot:(n,tuple(key(s) for s in n.sources)) for slot,n in zip(ids,net.nodes)}

def combine_masks(a,b,mode,rng):
    a=np.asarray(a,dtype=np.uint8);b=np.asarray(b,dtype=np.uint8)
    if a.shape!=b.shape:raise ValueError('Aligned masks required')
    x=np.packbits(a);y=np.packbits(b)
    if mode=='union':z=x|y
    elif mode=='intersection':z=x&y
    elif mode=='xor':z=x^y
    elif mode=='uniform':
        select=np.packbits(np.asarray(rng.random(a.size)<.5,dtype=np.uint8))
        z=(x&select)|(y&~select)
    else:raise ValueError('Unknown mask crossover')
    return np.unpackbits(z,count=a.size).astype(bool)

def numbers(rng,a,b):
    """Half parental inheritance, half convex blend; no bitwise float encoding."""
    a,b=np.broadcast_arrays(np.asarray(a,dtype=np.float64),np.asarray(b,dtype=np.float64))
    alpha=rng.random(a.shape);inherit=np.where(rng.random(a.shape)<.5,a,b)
    return np.where(rng.random(a.shape)<.5,inherit,alpha*a+(1-alpha)*b)

def network(rng,a,b,mode):
    aa,bb=encode(a),encode(b);merged={}
    aligned=[s for s in aa if s in bb]
    if not aligned:raise ValueError('No aligned node slots')
    target=aligned[rng.pick(len(aligned),'mask-node')] if hasattr(rng,'pick') else aligned[int(rng.integers(len(aligned)))]
    for slot in dict.fromkeys((*aa,*bb)):
        if slot not in aa:merged[slot]=bb[slot];continue
        if slot not in bb:merged[slot]=aa[slot];continue
        an,ak=aa[slot];bn,bk=bb[slot]
        if slot!=target:merged[slot]=aa[slot];continue
        # Attention/conv/gate have ordered argument roles: inherit the complete
        # argument block if operations differ or either operation is ordered.
        if an.op!=bn.op or an.op in ('attention','conv','gate'):
            merged[slot]=(an,ak) if rng.random()<.5 else (bn,bk);continue
        keys=tuple(dict.fromkeys((*ak,*bk)));ai={k:i for i,k in enumerate(ak)};bi={k:i for i,k in enumerate(bk)}
        mask=combine_masks([k in ai for k in keys],[k in bi for k in keys],mode,rng)
        selected=tuple(k for k,yes in zip(keys,mask) if yes)
        if not selected:raise ValueError('Mask removed all node inputs')
        wa=np.asarray([an.weights[ai[k]] if k in ai else bn.weights[bi[k]] for k in selected])
        wb=np.asarray([bn.weights[bi[k]] if k in bi else an.weights[ai[k]] for k in selected])
        shared=np.asarray([k in ai and k in bi for k in selected])
        weights=tuple(float(v) for v in np.where(shared,numbers(rng,wa,wb),wa))
        node=replace(an,weights=weights,bias=float(numbers(rng,an.bias,bn.bias)))
        merged[slot]=(node,selected)
    aid=slots(a);root=aid[-1];size=aid[a.size_source] if a.size_source>=0 else root
    # Activation mask = nodes reachable from action and sizing heads. Dormant
    # merged nodes are omitted; no unreachable CUDA computation is introduced.
    live=set();pending=[root,size]
    while pending:
        slot=pending.pop()
        if slot in live:continue
        live.add(slot)
        pending.extend(k[1] for k in merged[slot][1] if k[0]!='input')
    incoming={s:set(k[1] for k in merged[s][1] if k[0]=='node') for s in live}
    dependents={s:[] for s in live}
    for s,deps in incoming.items():
        for d in deps:dependents[d].append(s)
    ready=[(s==root,s) for s in live if not incoming[s]];heapq.heapify(ready);order=[]
    while ready:
        _,s=heapq.heappop(ready);order.append(s)
        for d in dependents[s]:
            incoming[d].remove(s)
            if not incoming[d]:heapq.heappush(ready,(d==root,d))
    if len(order)!=len(live) or order[-1]!=root:raise ValueError('Mask creates instantaneous cycle/output dependency')
    index={s:i for i,s in enumerate(order)}
    def source(k):return k[1] if k[0]=='input' else -index[k[1]]-1 if k[0]=='delay' else INPUTS+index[k[1]]
    # Remap the packed source array with vector operations. Stable slot lookup
    # and dependency ordering remain discrete; their tie order must not change.
    blocks=[merged[s][1] for s in order]
    lengths=np.fromiter(map(len,blocks),np.int64,count=len(blocks))
    flat=[k for block in blocks for k in block]
    kinds=np.fromiter((0 if k[0]=='input' else 1 if k[0]=='node' else 2 for k in flat),np.int8,count=len(flat))
    values=np.fromiter((k[1] if k[0]=='input' else index[k[1]] for k in flat),np.int64,count=len(flat))
    remapped=np.where(kinds==0,values,np.where(kinds==1,INPUTS+values,-values-1)).tolist()
    ends=np.cumsum(lengths);starts=np.r_[0,ends[:-1]]
    nodes=[]
    for s,first,last in zip(order,starts,ends):
        node=merged[s][0];sources=tuple(remapped[first:last])
        nodes.append(node if node.sources==sources and node.slot==s else replace(node,sources=sources,slot=s))
    nodes=tuple(nodes)
    return Network(nodes,float(numbers(rng,a.threshold,b.threshold)),index[size],float(numbers(rng,a.size_weight,b.size_weight)),float(numbers(rng,a.size_bias,b.size_bias))).validate()

def cross(rng,a,b):
    if a.case_owner!=b.case_owner:raise ValueError('Cross-case reproduction is forbidden in phase0')
    base,donor=(a,b) if rng.plan.get('orientation',rng.pick(2,'orientation'))==0 else (b,a)
    nets=dict(base.networks);events=[];ages=dict(base.minimum_age)
    count=rng.plan.get('count',1+rng.pick(5,'action-count'))
    start=rng.pick(5,'action-start')
    for j in range(count):
        stage=STAGES[(start+j)%5];mode=rng.plan['modes'][j] if rng.plan.get('modes') else MODES[rng.pick(4,'mask-mode')]
        COUNTS['requested_mask_'+mode]+=1
        nets[stage]=network(rng.fork('mask',j),base.networks[stage],donor.networks[stage],mode)
        COUNTS['accepted_mask_'+mode]+=1
        events.append(dict(stage=stage,mode='mask:'+mode,whole=False,homologous=True))
        if stage!='entry':ages[stage]=(base if rng.random()<.5 else donor).minimum_age.get(stage,0)
    genes={}
    for f in fields(Policy):
        x,y=getattr(base.policy,f.name),getattr(donor.policy,f.name)
        # Integer/categorical policy genes are inherited, preserving valid sets.
        genes[f.name]=float(numbers(rng,x,y)) if type(x) is float else (x if rng.random()<.5 else y)
    # Swing windows form one coupled gene.
    swing=base.policy if rng.random()<.5 else donor.policy
    genes.update(swing_left=swing.swing_left,swing_right=swing.swing_right)
    from .authority import feasible_policy
    return Individual(nets,feasible_policy(Policy(**genes)),ages,base.case_owner).validate(),dict(actions=events,base_parent=0 if base is a else 1,policy_field='typed-mask')
