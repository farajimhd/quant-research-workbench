"""Five independent sparse action networks with delayed recurrence and sizing."""

from dataclasses import dataclass,asdict,replace

import math

from research.vectorized_backtest.v42.config import MAX_NODES,MAX_DEPTH,MAX_STRATEGY_NODES,FAN_IN,STATES,INITIAL_SIZES,WEIGHT_BOUND

from research.vectorized_backtest.v42.features import CATALOG

from research.vectorized_backtest.v27.genome import Policy,random_policy_gene

from research.vectorized_backtest.v27.search_space import feature_index

from research.vectorized_backtest.v6.torch_backtest.history_bank import SWING_WINDOWS

from research.vectorized_backtest.v42.schedule import new_uid

STAGES=('entry','exit','add','reduce','trail')

MARKET_INPUTS=3*len(CATALOG)

INPUTS=MARKET_INPUTS+len(STATES)

OPS=('tanh','max','min','mean','product','gate','attention','identity','conv','decision','whole_or','whole_and','whole_xor','linear','whole_size')



@dataclass(frozen=True)

class Neuron:

    sources:tuple

    weights:tuple

    bias:float

    uid:int=0

    op:str="tanh"
    slot:int=0



@dataclass(frozen=True)

class Network:

    nodes:tuple

    threshold:float=0.

    size_source:int=-1

    size_weight:float=1.

    size_bias:float=0.

    def __post_init__(self):
        # Fresh scaffolds align by original node order. Growth inside an existing
        # scaffold gets a distinct persistent slot, unaffected by array shifts.
        if self.nodes and any(n.slot==0 for n in self.nodes):
            fresh=all(n.slot==0 for n in self.nodes)
            object.__setattr__(self,'nodes',tuple(replace(n,slot=(i+1 if fresh else 2**64+n.uid)) if n.slot==0 else n for i,n in enumerate(self.nodes)))
    def validate(self):

        from .validation_cache import validate

        return validate(self,self._validate_uncached)

    def _validate_uncached(self):

        if not 1<=len(self.nodes)<=MAX_NODES or not math.isfinite(self.threshold) or not -1<self.threshold<1:

            raise ValueError('Network size/threshold outside contract')

        persistent=[n.slot for n in self.nodes if n.slot]
        if len(persistent)!=len(set(persistent)) or any(type(n.slot) is not int or n.slot<0 for n in self.nodes):raise ValueError('Invalid persistent slots')
        depths=[]

        for i,n in enumerate(self.nodes):

            if not 1<=len(n.sources)<=FAN_IN or len(n.sources)!=len(n.weights) or len(set(n.sources))!=len(n.sources):

                raise ValueError('Invalid sparse connectivity')

            if any(type(s) is not int or not (-len(self.nodes)<=s<INPUTS+i) for s in n.sources):raise ValueError('Forward/unknown connection')

            if n.op not in OPS:raise ValueError('Unknown neural operator')

            if n.op in OPS[9:]:
                expected={'decision':1,'whole_or':2,'whole_and':2,'whole_xor':2,'linear':1,'whole_size':4}[n.op]
                if len(n.sources)!=expected or any(x<INPUTS for x in n.sources):raise ValueError('Invalid whole-network bridge')
                if n.op=='decision' and (not -1<n.weights[0]<1 or not 0<=n.bias<=1):raise ValueError('Invalid parental threshold/age')

            if n.op=='attention' and (len(n.sources)<3 or len(n.sources)%2!=1):raise ValueError('Attention requires query plus key/value pairs')

            if type(n.uid) is not int or n.uid<0:raise ValueError('Invalid persistent node identity')

            if any(not math.isfinite(w) or abs(w)>WEIGHT_BOUND for w in (*n.weights,n.bias)):raise ValueError('Invalid weights')

            depths.append(1+max((depths[s-INPUTS] for s in n.sources if s>=INPUTS),default=0))

        if max(depths)>MAX_DEPTH:raise ValueError('Network depth exceeded')

        if not -1<=self.size_source<len(self.nodes) or any(not math.isfinite(v) or abs(v)>WEIGHT_BOUND for v in (self.size_weight,self.size_bias)):raise ValueError('Invalid size head')

        if len(reachable(self,len(self.nodes)-1)|reachable(self,self.size_source if self.size_source>=0 else len(self.nodes)-1))!=len(self.nodes):raise ValueError('Unreachable neurons')

        if not any(0<=s<2*len(CATALOG) for n in self.nodes for s in n.sources):raise ValueError('Market-value-dependent action required')

        return self

    def payload(self):return dict(nodes=[dict(sources=n.sources,weights=n.weights,bias=n.bias,uid=n.uid,op=n.op,slot=n.slot) for n in self.nodes],threshold=self.threshold,size_source=self.size_source,size_weight=self.size_weight,size_bias=self.size_bias)

    @classmethod

    def restore(cls,p):return cls._decode(p).validate()

    @classmethod

    def _decode(cls,p):

        # Individual.validate checks every decoded network once, along with the

        # cross-network budget, entry state restrictions and policy contract.

        return cls(tuple(Neuron(tuple(n['sources']),tuple(n['weights']),n['bias'],n.get('uid',0),n.get('op','tanh'),n.get('slot',i+1)) for i,n in enumerate(p['nodes'])),p['threshold'],p.get('size_source',-1),p.get('size_weight',1.),p.get('size_bias',0.))



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

    return Network(tuple(Neuron(tuple(remap(s) for s in net.nodes[i].sources),net.nodes[i].weights,net.nodes[i].bias,net.nodes[i].uid,net.nodes[i].op,net.nodes[i].slot) for i in live),net.threshold,mapping[size],net.size_weight,net.size_bias)



def random_input(rng,stage):

    # Flat-only entry has no meaningful held-position state.

    if rng.random()<.15:return MARKET_INPUTS+int(rng.integers(7 if stage=='entry' else 0,len(STATES)))

    # Equivalent to choice's normalized cumulative probabilities and side='right'.

    draw=rng.random();channel=0 if draw<.45 else 1 if draw<.9 else 2

    return channel*len(CATALOG)+int(feature_index(rng))



def random_network(rng,stage,size=None,profile=None):

    """Broad exact-size trees with varied branching, depth and sparse inputs."""

    size=int(rng.choice(INITIAL_SIZES)) if size is None else int(size)

    minimum=1

    while sum(FAN_IN**i for i in range(minimum))<size:minimum+=1

    depth=int(rng.integers(minimum,min(MAX_DEPTH,size)+1));nodes=[]

    if profile is not None:

        shape, density, recurrence=profile

        depth=minimum if shape==0 else size if shape==2 else depth

    def build(n,d):

        sources=[]

        if n>1:

            capacity=sum(FAN_IN**i for i in range(d-1));low=(n-2)//capacity+1

            branches=1 if profile is not None and shape==2 else int(rng.integers(low,min(FAN_IN,n-1)+1));parts=[1]*branches;remaining=n-1-branches

            while remaining:

                options=[i for i,x in enumerate(parts) if x<capacity];k=options[int(rng.integers(len(options)))];parts[k]+=1;remaining-=1

            for part in parts:sources.append(build(part,d-1))

        # Every leaf contains a market feature, never a pure-state dead end.

        if not sources:sources.append(int(feature_index(rng))+int(rng.integers(2))*len(CATALOG))

        upper=FAN_IN if profile is None or density else max(len(sources),4)

        width=int(rng.integers(max(1,len(sources)),upper+1))

        while len(sources)<width:

            source=random_input(rng,stage)

            if source not in sources:sources.append(source)

        index=len(nodes)

        if len(sources)<FAN_IN and rng.random() < (.2 if profile is None else (0.,.2,.5)[recurrence]):sources.append(-index-1)

        nodes.append(Neuron(tuple(sources),tuple(float(x) for x in rng.uniform(-1,1,len(sources))),float(rng.uniform(-1,1)),new_uid(rng,('initial',stage,index))))

        return INPUTS+len(nodes)-1

    build(size,depth)

    # Half of initial action graphs retain the original tanh family.

    if rng.random()<.5:

        from research.vectorized_backtest.v42.sampling_fast import operator_choice

        nodes=[replace(n,op=operator_choice(rng,len(n.sources))) if rng.random()<.35 else n for n in nodes]

        if size>=3 and rng.random()<.5:

            # Exact one-step shift registers: convolution is across causal clocks.

            taps=min(size-1,int(rng.integers(2,5)))

            source=int(feature_index(rng))

            for j in range(taps):

                nodes[j]=Neuron((source if j==0 else -j,), (1.,),0.,nodes[j].uid,'identity')

            target=nodes[taps]

            sources=tuple(dict.fromkeys((source,)+tuple(-j-1 for j in range(taps))+target.sources))[:FAN_IN]

            nodes[taps]=replace(target,sources=sources,weights=tuple(float(x) for x in rng.uniform(-1,1,len(sources))),op=str(rng.choice(('conv','max','min','mean'))))

            # Existing upstream edges retain reachability; prune replaced branches.

    return prune(Network(tuple(nodes),float(rng.uniform(-.999999,.999999)),int(rng.integers(len(nodes))),float(rng.uniform(-1,1)),float(rng.uniform(-1,1)))).validate()



@dataclass(frozen=True)

class Individual:

    networks:dict

    policy:Policy

    minimum_age:dict

    case_owner:str|None=None

    routing=None

    def validate(self):

        if self.case_owner is not None and (type(self.case_owner) is not str or ':security:' not in self.case_owner):raise ValueError('Invalid immutable case owner')

        if set(self.networks)!=set(STAGES):raise ValueError('Five independent action networks required')

        if sum(len(n.nodes) for n in self.networks.values())>MAX_STRATEGY_NODES:raise ValueError('Strategy compute budget exceeded')

        for stage,n in self.networks.items():

            n.validate()

            if stage=='entry' and any(MARKET_INPUTS<=s<MARKET_INPUTS+7 for node in n.nodes for s in node.sources):raise ValueError('Entry cannot read held-position state')

        self.policy.validate()

        if any(k not in STAGES or type(v) is not int or not 0<=v<=3600 for k,v in self.minimum_age.items()):raise ValueError('Invalid age gate')

        return self

    def payload(self):
        value=dict(version=('v49-whole-v1' if any(x.op in OPS[9:] for n in self.networks.values() for x in n.nodes) else 'v42-recurrent-v1'),networks={s:n.payload() for s,n in self.networks.items()},policy=asdict(self.policy),minimum_age=self.minimum_age)
        if self.case_owner is not None:value['case_owner']=self.case_owner
        return value

    @classmethod

    def restore(cls,p):

        if p.get('version') not in ('v42-recurrent-v1','v49-whole-v1'):raise ValueError('Only fresh V42 recurrent genomes accepted')

        return cls({s:Network._decode(n) for s,n in p['networks'].items()},Policy(**p['policy']),p['minimum_age'],p.get('case_owner')).validate()



def sample(rng,count,*,stratified=False,structural=False):

    out=[]

    sizes={s:rng.permutation(np_sizes(count)) for s in STAGES} if stratified else None

    for j in range(count):

        swing=bool(rng.integers(2));policy=Policy(**{n:random_policy_gene(rng,n) for n in Policy.__dataclass_fields__ if not n.startswith('swing_')},

            swing_left=int(rng.choice(SWING_WINDOWS)) if swing else 0,swing_right=int(rng.choice(SWING_WINDOWS)) if swing else 0)

        out.append(Individual({s:random_network(rng,s,None if sizes is None else sizes[s][j],

            (j%3,(j//3)%2,(j//6)%3) if structural else None) for s in STAGES},policy,{s:int(rng.integers(301)) for s in STAGES if s!='entry'}).validate())

    return out



def np_sizes(count):

    import numpy as np

    return np.asarray(INITIAL_SIZES)[np.arange(count)%len(INITIAL_SIZES)]



def mutate(rng,parent):

    from .composition import mutation,proposal

    context=proposal(rng,'standalone-mutation',parent)

    return mutation(context,parent,1+context.pick(5,'edit-count'))[0]



def crossover(rng,left,right):

    from .composition import cross

    return cross(rng,left,right)[0]
