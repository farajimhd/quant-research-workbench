"""Conservative construction-time normalization; never inspect replay outcomes.

Only exact expressions are shared. No reassociation of floating-point sums,
mask-changing zero-edge removal, or approximate threshold equivalence is used.
"""
from dataclasses import replace
from functools import lru_cache
import math
import numpy as np

VERSION='v66-effective-graph-v1'
BOOL=('whole_or','whole_and','whole_xor','whole_xnor')
IGNORED=BOOL+('whole_size','whole_xnor_size')


def interval(node, prior):
    from .genome import INPUTS,MARKET_INPUTS
    from .features import CATALOG
    ranges=[prior[s-INPUTS] if s>=INPUTS else ((0.,1.) if s>=2*len(CATALOG) and s<MARKET_INPUTS else (-1.,1.)) for s in node.sources]
    def outward(lo,hi):
        # Deliberately loose guard around float32 operation/libdevice rounding.
        e=1e-5*max(1.,abs(lo),abs(hi))
        return lo-e,hi+e
    if node.op=='decision':return (0.,0.) if ranges[0][1]<float(np.float32(node.weights[0])) else (0.,1.)
    if node.op in BOOL:
        if node.op=='whole_and' and any(b<=.5 for a,b in ranges):return (0.,0.)
        if node.op=='whole_or' and all(b<=.5 for a,b in ranges):return (0.,0.)
        return (0.,1.)
    if node.op in ('whole_size','whole_xnor_size'):
        return min(0.,ranges[2][0],ranges[3][0]),max(0.,ranges[2][1],ranges[3][1])
    terms=[(min(w*a,w*b),max(w*a,w*b)) for (a,b),w in zip(ranges,map(float,map(np.float32,node.weights)))]
    magnitude=[max(abs(a),abs(b)) for a,b in terms]
    if node.op=='product' and math.prod(magnitude)>1e30:raise ValueError('Unbounded product intermediate')
    if node.op=='attention' and any(magnitude[0]*magnitude[i]>1e30 for i in range(1,len(magnitude),2)):
        raise ValueError('Unbounded attention intermediate')
    b=float(np.float32(node.bias));lo=b+sum(a for a,c in terms);hi=b+sum(c for a,c in terms)
    if not math.isfinite(lo+hi) or max(abs(lo),abs(hi))>1e30:raise ValueError('Unbounded arithmetic intermediate')
    if node.op=='linear':return outward(lo,hi)
    if node.op=='identity':return max(-1.,outward(lo,hi)[0]),min(1.,outward(lo,hi)[1])
    if node.op in ('min','max'):
        f=min if node.op=='min' else max;lo=b+f(a for a,c in terms);hi=b+f(c for a,c in terms)
    elif node.op=='mean':lo=b+sum(a for a,c in terms)/len(terms);hi=b+sum(c for a,c in terms)/len(terms)
    elif node.op not in ('tanh','conv'):return (-1.,1.)
    return max(-1.,outward(math.tanh(lo),math.tanh(hi))[0]),min(1.,outward(math.tanh(lo),math.tanh(hi))[1])


@lru_cache(maxsize=8192)
def network(net,stage):
    # Sharing can expose a previously hidden impossible threshold; reach a
    # fixed point before hashing, with a strict finite simplification budget.
    for _ in range(len(net.nodes)+3):
        result=_network_once(net,stage)
        if result==net:return result
        net=result
    raise ValueError('Canonicalization failed to reach a fixed point')


def _network_once(net,stage):
    from .genome import INPUTS,Network,Neuron,prune
    # Validate original domains before any rewrite can conceal an invalid op.
    net.validate()
    if stage!='reduce':net=replace(net,size_source=-1,size_weight=0.,size_bias=0.)
    net=prune(net)
    bounds=[]
    for n in net.nodes:bounds.append(interval(n,bounds))
    if bounds[-1][1]<=float(np.float32(net.threshold)):
        if stage=='entry':raise ValueError('Provably impossible entry')
        # The action can never fire; its size output is therefore unobservable.
        from .authority import ALLOWED
        source=ALLOWED[0]
        return Network((Neuron((source,),(0.,),0.,op='identity'),),0.,0,0.,0.).validate()
    nodes=[];mapping={};seen={}
    # Keep repeated edges separate to preserve float32 operation order.
    for i,n in enumerate(net.nodes):
        src=tuple(INPUTS+mapping[s-INPUTS] if s>=INPUTS else s for s in n.sources)
        weights=(1.,)*len(src) if n.op in IGNORED else n.weights
        bias=0. if n.op in IGNORED else n.bias
        op=n.op
        if op in ('whole_xor','whole_xnor') and src[0]==src[1]:
            # Same operand has the same availability mask; preserve that mask.
            bias=1. if op=='whole_xnor' else 0.;op='linear';weights=(0.,)
            src=src[:1]
        key=(op,src,weights,bias)
        previous=seen.get(key)
        if previous is not None:
            mapping[i]=previous
        else:
            mapping[i]=len(nodes);seen[key]=len(nodes)
            nodes.append(replace(n,sources=src,weights=weights,bias=bias,op=op))
    root=mapping[len(net.nodes)-1];size=mapping[net.size_source] if net.size_source>=0 else root
    # If an earlier shared root precedes a live size head, leave this uncommon
    # expression unmerged rather than violating the output-last ABI.
    if root!=len(nodes)-1:return net.validate()
    result=Network(tuple(nodes),net.threshold,size,net.size_weight,net.size_bias)
    return prune(result).validate()


def member(value):
    nets={s:network(n,s) for s,n in value.networks.items()}
    return replace(value,networks=nets)
