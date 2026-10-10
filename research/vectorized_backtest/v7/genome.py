"""Typed V7 lifecycle programs and position-only policy genes."""
from dataclasses import dataclass,asdict,replace,field
import math
from research.vectorized_backtest.v6.torch_backtest.program import Node,Program,Op,WINDOWS
from research.vectorized_backtest.v6.torch_backtest.history_bank import SWING_WINDOWS
from .features import CATALOG

STAGES=('entry','exit','add','reduce','trail')

@dataclass(frozen=True)
class Policy:
    stop_fraction:float=.03
    target_fraction:float=.05
    reduce_fraction:float=.25
    trail_fraction:float=.03
    add_minimum_profit:float=.01
    reduce_minimum_profit:float=.02
    maximum_adds:int=2
    cooldown:int=30
    swing_left:int=0
    swing_right:int=0
    def validate(self):
        for name in ('stop_fraction','target_fraction','reduce_fraction','trail_fraction','add_minimum_profit','reduce_minimum_profit'):
            value=getattr(self,name)
            if not math.isfinite(value) or not 0<=value<=1:raise ValueError('Invalid position fraction')
        if min(self.stop_fraction,self.target_fraction,self.reduce_fraction,self.trail_fraction)<=0:raise ValueError('Positive position fractions required')
        if type(self.maximum_adds) is not int or not 0<=self.maximum_adds<=5 or type(self.cooldown) is not int or not 1<=self.cooldown<=300:
            raise ValueError('Invalid bounded adds/cooldown')
        if (self.swing_left,self.swing_right)!=(0,0) and (self.swing_left not in SWING_WINDOWS or self.swing_right not in SWING_WINDOWS):
            raise ValueError('Unsupported persisted swing pair')
        return self

@dataclass(frozen=True)
class Individual:
    rules:dict
    policy:Policy
    open_rules:dict|None=None
    minimum_age:dict=field(default_factory=dict)
    def validate(self):
        if set(self.rules)!=set(STAGES):raise ValueError('All position lifecycle rules required')
        for rule in self.rules.values():rule.validate(CATALOG)
        if self.open_rules is not None:
            if set(self.open_rules)!=set(STAGES):raise ValueError("Complete open-state rules required")
            for rule in self.open_rules.values():rule.validate(CATALOG)
        if any(k not in STAGES or type(v) is not int or v<0 for k,v in self.minimum_age.items()):raise ValueError("Invalid position age condition")
        self.policy.validate();return self
    def payload(self):return dict(rules={s:self.rules[s].payload() for s in STAGES},policy=asdict(self.policy),open_rules={s:self.open_rules[s].payload() for s in STAGES} if self.open_rules is not None else None,minimum_age=self.minimum_age)
    @classmethod
    def restore(cls,value):return cls({s:Program.from_payload(value['rules'][s]) for s in STAGES},Policy(**value['policy']),{s:Program.from_payload(value['open_rules'][s]) for s in STAGES} if value.get('open_rules') is not None else None,value.get('minimum_age',{})).validate()

def condition(rng):
    # Every feature is eligible. Price channels compare with another price;
    # log-price thresholds use causal differences, never arbitrary price levels.
    index=int(rng.integers(len(CATALOG)));spec=CATALOG[index]
    nodes=[Node(Op.FEATURE,feature=index)];left=0
    if spec.unit=='price':
        choices=[i for i,f in enumerate(CATALOG) if f.unit=='price' and i!=index]
        nodes.append(Node(Op.FEATURE,feature=int(rng.choice(choices))))
    elif spec.unit=='log_price':
        nodes.append(Node(Op.DIFFERENCE,a=0,window=int(rng.choice(WINDOWS[:-1]))));left=1
        nodes.append(Node(Op.CONSTANT,value=float(rng.uniform(-.1,.1)),unit=spec.unit))
    else:
        if spec.unit!='bool' and rng.random()<.35:
            nodes.append(Node(Op.MEAN,a=0,window=int(rng.choice(WINDOWS))));left=1
        lo,hi=spec.lower,spec.upper
        nodes.append(Node(Op.CONSTANT,value=float(rng.integers(0,2)) if spec.unit=='bool' else float(rng.uniform(lo,hi)),unit=spec.unit))
    nodes.append(Node(int(rng.choice([Op.GREATER,Op.LESS])),a=left,b=len(nodes)-1))
    return Program(tuple(nodes),len(nodes)-1)

def sample(rng,count):
    result=[]
    for _ in range(count):
        swing=bool(rng.integers(2))
        policy=Policy(stop_fraction=float(rng.uniform(.005,.15)),target_fraction=float(rng.uniform(.01,.2)),
            reduce_fraction=float(rng.uniform(.1,1)),trail_fraction=float(rng.uniform(.005,.1)),
            add_minimum_profit=float(rng.uniform(0,.15)),reduce_minimum_profit=float(rng.uniform(0,.2)),
            maximum_adds=int(rng.integers(0,6)),cooldown=int(rng.integers(1,301)),
            swing_left=int(rng.choice(SWING_WINDOWS)) if swing else 0,swing_right=int(rng.choice(SWING_WINDOWS)) if swing else 0)
        rules={}
        for stage in STAGES:
            rule=condition(rng)
            for _ in range(int(rng.integers(0,3))):rule=combine(rule,condition(rng),int(rng.choice([Op.AND,Op.OR])))
            rules[stage]=rule
        result.append(Individual(rules,policy,{s:condition(rng) for s in STAGES}).validate())
    return result

def mutate(rng,parent):
    open_branch=parent.open_rules is not None and bool(rng.integers(2))
    rules=dict(parent.open_rules if open_branch else parent.rules);stage=STAGES[int(rng.integers(len(STAGES)))];rule=rules[stage]
    action=int(rng.integers(4))
    if action==0 and len(rule.nodes)<24:
        rules[stage]=combine(rule,condition(rng),int(rng.choice([Op.AND,Op.OR])))
    elif action==1:
        nodes=list(rule.nodes);choices=[i for i,n in enumerate(nodes) if n.op==Op.CONSTANT and n.unit!='bool']
        if choices:
            i=int(rng.choice(choices));n=nodes[i]
            nodes[i]=replace(n,value=n.value+float(rng.normal(0,max(.005,abs(n.value)*.1))))
            rules[stage]=Program(tuple(nodes),rule.output)
        else:rules[stage]=condition(rng)
    else:rules[stage]=condition(rng)
    ages=dict(parent.minimum_age)
    name=rng.choice(('stop_fraction','target_fraction','reduce_fraction','trail_fraction','add_minimum_profit','reduce_minimum_profit'))
    value=float(min(1,max(.001,getattr(parent.policy,name)+rng.normal(0,.01))))
    return Individual(parent.rules if open_branch else rules,replace(parent.policy,**{name:value}),rules if open_branch else parent.open_rules,ages).validate()


def combine(left,right,operator):
    offset=len(left.nodes)
    nodes=left.nodes+tuple(replace(n,a=n.a+offset if n.a>=0 else -1,b=n.b+offset if n.b>=0 else -1) for n in right.nodes)
    return Program(nodes+(Node(operator,a=left.output,b=right.output+offset),),len(nodes))
