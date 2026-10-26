"""One homologous sparse recurrent core and six typed action readouts."""
from dataclasses import dataclass, asdict
import math
import numpy as np
from research.vectorized_backtest.v42.features import CATALOG
from research.vectorized_backtest.v42.config import STATES
from research.vectorized_backtest.v27.genome import Policy
from research.vectorized_backtest.v27.genome import random_policy_gene
from research.vectorized_backtest.v6.torch_backtest.history_bank import SWING_WINDOWS
STAGES=('entry','exit','add','reduce','trail')
HEADS=(*STAGES,'stop')
HIDDEN=64
MIN_HIDDEN=16
MAX_NODES=512
FAN_IN=8
MARKET_INPUTS=3*len(CATALOG)
INPUTS=MARKET_INPUTS+len(STATES)
OPS=('tanh',)
MODES=('plain','union','intersection','xor','xnor')

@dataclass(frozen=True)
class Neuron:
    sources:tuple
    weights:tuple
    bias:float=0.
    uid:int=0
    op:str='tanh'
@dataclass(frozen=True)
class Branch:
    sources:tuple
    weights:tuple
    bias:float=0.
    threshold:float=0.
@dataclass(frozen=True)
class Head:
    branches:tuple
    mode:str='plain'
    size_weight:float=1.
    size_bias:float=0.
@dataclass(frozen=True)
class Network:
    nodes:tuple
    heads:tuple
    def payload(self):return asdict(self)
    def validate(self):
        size=len(self.nodes)
        if not MIN_HIDDEN<=size<=MAX_NODES-6 or len(self.heads)!=6:raise ValueError('Variable recurrent core and six heads required')
        if any(type(n.uid) is not int or n.uid<=0 for n in self.nodes) or len({n.uid for n in self.nodes})!=size:raise ValueError('Unique persistent neuron identities required')
        def check(sources,weights,low,high,scalars):
            if not isinstance(sources,tuple) or not isinstance(weights,tuple) or not 1<=len(sources)<=FAN_IN or len(sources)!=len(weights):raise ValueError('Invalid sparse fan-in')
            if len(set(sources))!=len(sources) or any(type(s) is not int or not low<=s<high for s in sources):raise ValueError('Invalid shared source')
            if any(not math.isfinite(v) or abs(v)>16 for v in (*weights,*scalars)):raise ValueError('Invalid bounded parameter')
        for n in self.nodes:
            check(n.sources,n.weights,-size,INPUTS,(n.bias,))
            if n.op!='tanh':raise ValueError('V55 recurrent cells use tanh')
        for i,h in enumerate(self.heads):
            if h.mode not in MODES or len(h.branches)!=(1 if h.mode=='plain' else 2) or (i==5 and h.mode!='plain'):raise ValueError('Invalid typed head')
            for b in h.branches:check(b.sources,b.weights,0,size,(b.bias,b.threshold,h.size_weight,h.size_bias))
        if node_count(self)>MAX_NODES:raise ValueError('Strategy compute budget exceeded')
        return self
@dataclass(frozen=True)
class Individual:
    networks:dict
    policy:Policy
    minimum_age:dict
    case_owner:str|None=None
    routing=None
    def validate(self):
        if set(self.networks)!={'shared'} or self.case_owner is not None:raise ValueError('V55 requires one unrestricted shared core')
        self.networks['shared'].validate();self.policy.validate()
        if any(k not in STAGES or type(v) is not int or not 0<=v<=3600 for k,v in self.minimum_age.items()):raise ValueError('Invalid age gate')
        return self
    def payload(self):return dict(version='v55-shared-v1',networks={s:n.payload() for s,n in self.networks.items()},policy=asdict(self.policy),minimum_age=dict(self.minimum_age))
    @classmethod
    def restore(cls,p):
        if p.get('version')!='v55-shared-v1':raise ValueError('V55 cannot import independent graph genomes')
        n=p['networks']['shared']
        nodes=tuple(Neuron(tuple(x['sources']),tuple(x['weights']),x['bias'],x['uid'],x['op']) for x in n['nodes'])
        heads=tuple(Head(tuple(Branch(tuple(b['sources']),tuple(b['weights']),b['bias'],b['threshold']) for b in h['branches']),h['mode'],h['size_weight'],h['size_bias']) for h in n['heads'])
        return cls({'shared':Network(nodes,heads)},Policy(**p['policy']),dict(p['minimum_age']),p.get('case_owner')).validate()
def sample(rng,count,**kwargs):
    out=[]
    for _ in range(count):
        policy=Policy(**{n:random_policy_gene(rng,n) for n in Policy.__dataclass_fields__ if not n.startswith('swing_')})
        nodes=[]
        for j in range(HIDDEN):
            sources=tuple(dict.fromkeys([int(rng.integers(2*len(CATALOG))) for _ in range(4)]+[MARKET_INPUTS+int(rng.integers(len(STATES))) for _ in range(2)]+[-1-int(rng.integers(HIDDEN)) for _ in range(2)]))
            nodes.append(Neuron(sources,tuple(float(x) for x in rng.uniform(-.7,.7,len(sources))),float(rng.uniform(-.3,.3)),j+1))
        heads=[]
        for i in range(6):
            sources=tuple(sorted(set(int(rng.integers(HIDDEN)) for _ in range(FAN_IN))))
            bias=float(np.arctanh(np.clip(2*(min(.2,policy.stop_fraction)-.001)/.199-1,-.999,.999))) if i==5 else 0.
            heads.append(Head((Branch(sources,tuple(float(x) for x in rng.uniform(-.6,.6,len(sources))),0.,float(rng.uniform(-.4,.4)) if i<5 else 0.),),size_bias=bias))
        out.append(Individual({'shared':Network(tuple(nodes),tuple(heads))},policy,{s:int(rng.integers(301)) for s in STAGES[1:]}).validate())
    return out
def mutate(rng,parent):
    from .composition import mutation
    return mutation(rng,parent,1)[0]
def crossover(rng,left,right):
    from .composition import cross
    return cross(rng,left,right)[0]

def node_count(network):
    return len(network.nodes)+sum(len(h.branches)+(h.mode!='plain') for h in network.heads)
