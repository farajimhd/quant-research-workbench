"""V18 typed lifecycle programs with complete bounded gene evolution."""
from dataclasses import dataclass,asdict,replace,field
import math
from .program import Node,Program,Op,WINDOWS
from research.vectorized_backtest.v6.torch_backtest.history_bank import SWING_WINDOWS
from .features import CATALOG

STAGES=('entry','exit','add','reduce','trail')

def effective_rules(member):
    """Entry is reachable only while flat; management only while holding."""
    return {s:member.rules[s] if s=='entry' or member.open_rules is None else member.open_rules[s] for s in STAGES}

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
        from .semantics import validate_rule
        if set(self.rules)!=set(STAGES):raise ValueError('All position lifecycle rules required')
        for rule in self.rules.values():rule.validate(CATALOG)
        if self.open_rules is not None:
            if set(self.open_rules)!=set(STAGES):raise ValueError("Complete open-state rules required")
            for rule in self.open_rules.values():rule.validate(CATALOG)
        for stage, rule in effective_rules(self).items():validate_rule(rule, stage)
        if any(k not in STAGES or type(v) is not int or v<0 for k,v in self.minimum_age.items()):raise ValueError("Invalid position age condition")
        self.policy.validate();return self
    def payload(self):return dict(rules={s:self.rules[s].payload() for s in STAGES},policy=asdict(self.policy),open_rules={s:self.open_rules[s].payload() for s in STAGES} if self.open_rules is not None else None,minimum_age=self.minimum_age)
    @classmethod
    def restore(cls,value):return cls({s:Program.from_payload(value['rules'][s]) for s in STAGES},Policy(**value['policy']),{s:Program.from_payload(value['open_rules'][s]) for s in STAGES} if value.get('open_rules') is not None else None,value.get('minimum_age',{})).validate()

def condition(rng,feature=None):
    from .expressions import atom
    return atom(rng,feature)


def random_rule(rng,stage):
    # Optional management is explicit; entries always depend on evidence.
    if stage!='entry' and rng.random()<.05:
        return Program((Node(Op.CONSTANT,value=float(rng.integers(2)),unit='bool'),),0)
    rule=condition(rng)
    for _ in range(int(rng.choice(3,p=(.8,.15,.05)))):
        other=condition(rng)
        if len(rule.nodes)+len(other.nodes)+1<=32:
            rule=combine(rule,other,int(rng.choice((Op.AND,Op.OR))))
    return rule


def random_policy_gene(rng,name):
    from .search_space import fraction
    if name=='maximum_adds':return int(rng.integers(6))
    if name=='cooldown':return int(rng.integers(1,301))
    return fraction(rng,name)


def assemble(rules,policy,ages):
    from .complexity import simplify
    rules={s:simplify(p) for s,p in rules.items()}
    never=Program((Node(Op.CONSTANT,value=0.,unit='bool'),),0)
    return Individual({s:rules[s] if s=='entry' else never for s in STAGES},policy,
        {s:rules[s] if s!='entry' else never for s in STAGES},{s:v for s,v in ages.items() if v}).validate()


def sample(rng,count):
    from .search_space import age
    result=[]
    for _ in range(count):
        swing=bool(rng.integers(2))
        policy=Policy(**{name:random_policy_gene(rng,name) for name in Policy.__dataclass_fields__ if not name.startswith('swing_')},
            swing_left=int(rng.choice(SWING_WINDOWS)) if swing else 0,
            swing_right=int(rng.choice(SWING_WINDOWS)) if swing else 0)
        from .semantics import SemanticError
        for attempt in range(32):
            try:
                member=assemble({s:random_rule(rng,s) for s in STAGES},policy,{s:age(rng) for s in STAGES if s!='entry'})
                result.append(member);break
            except SemanticError:continue
        else:raise RuntimeError('Semantic initialization retry budget exhausted')
    return result


def mutate(rng,parent):
    from .variation import mutate_fields
    return mutate_fields(rng,parent,int(rng.integers(1,6)))[0]


def crossover(rng,left,right):
    """Typed rule blocks and independently recombined policy genes."""
    l=effective_rules(left);r=effective_rules(right)
    selected={s:(l if rng.integers(2)==0 else r)[s] for s in STAGES}
    from .expressions import exchange
    stage=str(rng.choice(STAGES));recipient,donor=(l,r) if rng.integers(2)==0 else (r,l)
    selected[stage],_=exchange(rng,recipient[stage],donor[stage])
    genes={s:getattr(left.policy if rng.integers(2)==0 else right.policy,s)
           for s in Policy.__dataclass_fields__ if not s.startswith('swing_')}
    # Mode is one gene; active left/right window choices recombine independently.
    mode=left.policy if rng.integers(2)==0 else right.policy
    for s in ('swing_left','swing_right'):
        choices=[getattr(p.policy,s) for p in (left,right) if getattr(p.policy,s)]
        genes[s]=int(rng.choice(choices)) if mode.swing_left else 0
    ages={s:(left.minimum_age if rng.integers(2)==0 else right.minimum_age).get(s,0) for s in STAGES if s!='entry'}
    return assemble(selected,Policy(**genes),ages)


def combine(left,right,operator):
    offset=len(left.nodes)
    nodes=left.nodes+tuple(replace(n,a=n.a+offset if n.a>=0 else -1,b=n.b+offset if n.b>=0 else -1) for n in right.nodes)
    return Program(nodes+(Node(operator,a=left.output,b=right.output+offset),),len(nodes))
