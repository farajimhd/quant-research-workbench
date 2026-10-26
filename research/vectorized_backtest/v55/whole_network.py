"""Boolean readout composition with dependency-complete donor unit import."""
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from .genome import HEADS,STAGES,MAX_NODES,node_count
from .structure import remap_node,remap_head
POLICY=dict(total_crossover_fraction=.25,modes=dict(union=.4,intersection=.2,xor=.2,xnor=.2),max_strategy_nodes=512,
    shared_core='base plus missing donor readout dependencies',nested_composition='reject explicitly',stop='continuous inheritance; never Boolean')
_cache=ContextVar('v55_dependency_index',default=None)
@contextmanager
def cache_scope():
    token=_cache.set(OrderedDict())
    try:yield
    finally:_cache.reset(token)
def index(net):
    cache=_cache.get()
    saved=cache.get(id(net)) if cache is not None else None
    if saved is not None and saved[0] is net:return saved[1:]
    ids={n.uid:i for i,n in enumerate(net.nodes)};closures=[]
    for h in net.heads:
        needed={s for b in h.branches for s in b.sources};todo=list(needed)
        while todo:
            i=todo.pop()
            for s in net.nodes[i].sources:
                if s<0 and -s-1 not in needed:needed.add(-s-1);todo.append(-s-1)
        closures.append({net.nodes[i].uid for i in needed})
    if cache is not None:
        cache[id(net)]=(net,ids,closures)
        if len(cache)>4096:cache.popitem(last=False)
    return ids,closures
def fits(a,b,stages):
    x=a.networks['shared'];y=b.networks['shared'];base,_=index(x);_,closure=index(y)
    if any(len(x.heads[HEADS.index(s)].branches)!=1 or len(y.heads[HEADS.index(s)].branches)!=1 for s in stages):return False
    needed=set().union(*(closure[HEADS.index(s)] for s in stages))
    return node_count(x)+len(needed-base.keys())+2*len(stages)<=MAX_NODES
def cross(a,b,mode,stages=None):
    stages=STAGES if stages is None else tuple(stages)
    if mode not in POLICY['modes'] or not stages or len(set(stages))!=len(stages) or any(s not in STAGES for s in stages):raise ValueError('Invalid discrete composition')
    if not fits(a,b,stages):raise ValueError('Head composition dependency or two-branch budget exceeded')
    x=a.networks['shared'];y=b.networks['shared'];lookup,_=index(x);lookup=dict(lookup);_,closures=index(y)
    needed=set().union(*(closures[HEADS.index(s)] for s in stages));extra=[n for n in y.nodes if n.uid in needed and n.uid not in lookup]
    for n in extra:lookup[n.uid]=len(lookup)
    nodes=x.nodes+tuple(remap_node(n,y.nodes,lookup) for n in extra);heads=list(x.heads);ages=dict(a.minimum_age)
    for s in stages:
        i=HEADS.index(s);donor=remap_head(y.heads[i],y.nodes,lookup)
        heads[i]=replace(heads[i],branches=(heads[i].branches[0],donor.branches[0]),mode=mode)
        ages[s]=max(a.minimum_age.get(s,0),b.minimum_age.get(s,0))
    child=replace(a,networks={'shared':replace(x,nodes=nodes,heads=tuple(heads))},minimum_age=ages).validate()
    return child,dict(actions=[dict(stage=s,mode='shared-head:'+mode,whole=True) for s in stages],whole_network=True,
        shared_core='base-parent plus required donor units',imported_units=len(extra),parent_behavior_preserved=False)
