"""Five independent sparse action networks with delayed recurrence and sizing."""
from dataclasses import dataclass,asdict,replace
import math
from .config import MAX_NODES,MAX_DEPTH,MAX_STRATEGY_NODES,FAN_IN,STATES,INITIAL_SIZES
from .features import CATALOG
from research.vectorized_backtest.v27.genome import Policy,random_policy_gene
from research.vectorized_backtest.v27.search_space import feature_index
from research.vectorized_backtest.v6.torch_backtest.history_bank import SWING_WINDOWS
STAGES=('entry','exit','add','reduce','trail')
MARKET_INPUTS=3*len(CATALOG)
INPUTS=MARKET_INPUTS+len(STATES)

@dataclass(frozen=True)
class Neuron:
    sources:tuple
    weights:tuple
    bias:float
    uid:int=0

@dataclass(frozen=True)
class Network:
    nodes:tuple
    threshold:float=0.
    size_source:int=-1
    size_weight:float=1.
    size_bias:float=0.
    def validate(self):
        if not 1<=len(self.nodes)<=MAX_NODES or not math.isfinite(self.threshold) or not -1<self.threshold<1:
            raise ValueError('Network size/threshold outside contract')
        depths=[]
        for i,n in enumerate(self.nodes):
            if not 1<=len(n.sources)<=FAN_IN or len(n.sources)!=len(n.weights) or len(set(n.sources))!=len(n.sources):
                raise ValueError('Invalid sparse connectivity')
            if any(type(s) is not int or not (-len(self.nodes)<=s<INPUTS+i) for s in n.sources):raise ValueError('Forward/unknown connection')
            if type(n.uid) is not int or n.uid<0:raise ValueError('Invalid persistent node identity')
            if any(not math.isfinite(w) or abs(w)>4 for w in (*n.weights,n.bias)):raise ValueError('Invalid weights')
            depths.append(1+max((depths[s-INPUTS] for s in n.sources if s>=INPUTS),default=0))
        if max(depths)>MAX_DEPTH:raise ValueError('Network depth exceeded')
        if not -1<=self.size_source<len(self.nodes) or any(not math.isfinite(v) or abs(v)>4 for v in (self.size_weight,self.size_bias)):raise ValueError('Invalid size head')
        if len(reachable(self,len(self.nodes)-1)|reachable(self,self.size_source if self.size_source>=0 else len(self.nodes)-1))!=len(self.nodes):raise ValueError('Unreachable neurons')
        if not any(0<=s<2*len(CATALOG) for n in self.nodes for s in n.sources):raise ValueError('Market-value-dependent action required')
        return self
    def payload(self):return dict(nodes=[asdict(n) for n in self.nodes],threshold=self.threshold,size_source=self.size_source,size_weight=self.size_weight,size_bias=self.size_bias)
    @classmethod
    def restore(cls,p):return cls(tuple(Neuron(tuple(n['sources']),tuple(n['weights']),n['bias'],n.get('uid',0)) for n in p['nodes']),p['threshold'],p.get('size_source',-1),p.get('size_weight',1.),p.get('size_bias',0.)).validate()

def reachable(net,root):
    live=set()
    def visit(i):
        if i in live:return
        live.add(i)
        for s in net.nodes[i].sources:
            if s<0:visit(-s-1)
            elif s>=INPUTS:visit(s-INPUTS)
    visit(root);return live

def prune(net,root=None):
    actual=len(net.nodes)-1 if root is None else root
    size=net.size_source if root is None and net.size_source>=0 else actual
    live=sorted(reachable(net,actual)|reachable(net,size));mapping={i:j for j,i in enumerate(live)}
    # Keep the trigger as the last node; module extraction may include delayed
    # dependencies after it, so those extra modules cannot be extracted safely.
    if mapping[actual]!=len(live)-1:raise ValueError('Delayed module crosses output boundary')
    def remap(s):return -mapping[-s-1]-1 if s<0 else s if s<INPUTS else INPUTS+mapping[s-INPUTS]
    return Network(tuple(Neuron(tuple(remap(s) for s in net.nodes[i].sources),net.nodes[i].weights,net.nodes[i].bias,net.nodes[i].uid) for i in live),net.threshold,mapping[size],net.size_weight,net.size_bias)

def random_input(rng,stage):
    # Flat-only entry has no meaningful held-position state.
    if rng.random()<.15:return MARKET_INPUTS+int(rng.integers(7 if stage=='entry' else 0,len(STATES)))
    channel=int(rng.choice(3,p=(.45,.45,.10)))
    return channel*len(CATALOG)+int(feature_index(rng))

def random_network(rng,stage,size=None):
    """Broad exact-size trees with varied branching, depth and sparse inputs."""
    size=int(rng.choice(INITIAL_SIZES)) if size is None else int(size)
    minimum=1
    while sum(FAN_IN**i for i in range(minimum))<size:minimum+=1
    depth=int(rng.integers(minimum,min(MAX_DEPTH,size)+1));nodes=[]
    def build(n,d):
        sources=[]
        if n>1:
            capacity=sum(FAN_IN**i for i in range(d-1));low=(n-2)//capacity+1
            branches=int(rng.integers(low,min(FAN_IN,n-1)+1));parts=[1]*branches;remaining=n-1-branches
            while remaining:
                options=[i for i,x in enumerate(parts) if x<capacity];k=int(rng.choice(options));parts[k]+=1;remaining-=1
            for part in parts:sources.append(build(part,d-1))
        # Every leaf contains a market feature, never a pure-state dead end.
        if not sources:sources.append(int(feature_index(rng))+int(rng.integers(2))*len(CATALOG))
        width=int(rng.integers(max(1,len(sources)),FAN_IN+1))
        while len(sources)<width:
            source=random_input(rng,stage)
            if source not in sources:sources.append(source)
        index=len(nodes)
        if len(sources)<FAN_IN and rng.random()<.2:sources.append(-index-1)
        nodes.append(Neuron(tuple(sources),tuple(float(x) for x in rng.uniform(-1,1,len(sources))),float(rng.uniform(-1,1)),int(rng.integers(1,2**63))))
        return INPUTS+len(nodes)-1
    build(size,depth)
    return Network(tuple(nodes),float(rng.uniform(-.999999,.999999)),int(rng.integers(len(nodes))),float(rng.uniform(-1,1)),float(rng.uniform(-1,1))).validate()

@dataclass(frozen=True)
class Individual:
    networks:dict
    policy:Policy
    minimum_age:dict
    routing=None
    def validate(self):
        if set(self.networks)!=set(STAGES):raise ValueError('Five independent action networks required')
        if sum(len(n.nodes) for n in self.networks.values())>MAX_STRATEGY_NODES:raise ValueError('Strategy compute budget exceeded')
        for stage,n in self.networks.items():
            n.validate()
            if stage=='entry' and any(MARKET_INPUTS<=s<MARKET_INPUTS+7 for node in n.nodes for s in node.sources):raise ValueError('Entry cannot read held-position state')
        self.policy.validate()
        if any(k not in STAGES or type(v) is not int or not 0<=v<=3600 for k,v in self.minimum_age.items()):raise ValueError('Invalid age gate')
        return self
    def payload(self):return dict(version='v29-recurrent-v1',networks={s:n.payload() for s,n in self.networks.items()},policy=asdict(self.policy),minimum_age=self.minimum_age)
    @classmethod
    def restore(cls,p):
        if p.get('version')!='v29-recurrent-v1':raise ValueError('Only fresh V29 recurrent genomes accepted')
        return cls({s:Network.restore(n) for s,n in p['networks'].items()},Policy(**p['policy']),p['minimum_age']).validate()

def sample(rng,count,*,stratified=False):
    out=[]
    sizes={s:rng.permutation(np_sizes(count)) for s in STAGES} if stratified else None
    for j in range(count):
        swing=bool(rng.integers(2));policy=Policy(**{n:random_policy_gene(rng,n) for n in Policy.__dataclass_fields__ if not n.startswith('swing_')},
            swing_left=int(rng.choice(SWING_WINDOWS)) if swing else 0,swing_right=int(rng.choice(SWING_WINDOWS)) if swing else 0)
        out.append(Individual({s:random_network(rng,s,None if sizes is None else sizes[s][j]) for s in STAGES},policy,{s:int(rng.integers(301)) for s in STAGES if s!='entry'}).validate())
    return out

def np_sizes(count):
    import numpy as np
    return np.asarray(INITIAL_SIZES)[np.arange(count)%len(INITIAL_SIZES)]

def mutate(rng,parent):
    from .composition import mutation
    return mutation(rng,parent,int(rng.integers(1,6)))[0]

def crossover(rng,left,right):
    from .composition import cross
    return cross(rng,left,right)[0]
