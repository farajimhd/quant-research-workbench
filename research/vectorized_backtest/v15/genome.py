"""V15 typed lifecycle programs with complete bounded gene evolution."""
from dataclasses import dataclass,asdict,replace,field
import math
from research.vectorized_backtest.v6.torch_backtest.program import Node,Program,Op,WINDOWS
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

def condition(rng, feature=None):
    """A bounded typed atom; every evaluator operator has a generation route."""
    from .search_space import feature_index,semantic,threshold,COMPARISONS,TEMPORAL
    index=feature_index(rng) if feature is None else int(feature);spec=CATALOG[index]
    if spec.unit=='bool':
        nodes=[Node(Op.FEATURE,feature=index)]
        if rng.integers(2):nodes.append(Node(Op.NOT,a=0))
        return Program(tuple(nodes),len(nodes)-1)
    nodes=[Node(Op.FEATURE,feature=index)];left=0;value=threshold(rng,index)
    # Compare like semantic quantities. A price change is not a price level.
    if spec.unit=='price':
        choices=PRICE_PEERS[semantic(spec)]
        choices=[i for i in choices if i!=index]
        nodes.append(Node(Op.FEATURE,feature=int(rng.choice(choices))))
        right=1
    else:
        if spec.unit=='log_price':
            nodes.append(Node(Op.DIFFERENCE,a=left,window=int(rng.choice(WINDOWS[:-1]))));left=len(nodes)-1
            value=float(rng.uniform(-.1,.1))
        nodes.append(Node(Op.CONSTANT,value=value,unit=spec.unit));right=len(nodes)-1
    # Most atoms stay pointwise; bounded transformations remain reachable.
    transform=int(rng.integers(60))
    if transform in (0,1) and spec.unit!='log_price':
        op=TEMPORAL[int(rng.integers(len(TEMPORAL)))];window=int(rng.choice(WINDOWS))
        if op in (Op.LAG,Op.DIFFERENCE) and window==120:window=60
        nodes.append(Node(op,a=left,window=window));left=len(nodes)-1
        if op==Op.DIFFERENCE:
            # Compare a delta with a delta, not a level or shifted level.
            nodes.append(Node(op,a=right,window=window));right=len(nodes)-1
    elif transform==2:
        nodes.append(Node(Op.ABS,a=left));left=len(nodes)-1
        nodes.append(Node(Op.ABS,a=right));right=len(nodes)-1
    elif transform in (3,4):
        nodes.append(Node(Op.CONSTANT,value=float(rng.uniform(.5,1.5)),unit='ratio'));factor=len(nodes)-1
        nodes.append(Node(Op.MULTIPLY if transform==3 else Op.DIVIDE,a=left,b=factor));left=len(nodes)-1
    elif transform in (5,6):
        nodes.append(Node(Op.CONSTANT,value=float(rng.uniform(.001,.1)),unit='ratio'));factor=len(nodes)-1
        nodes.append(Node(Op.MULTIPLY,a=right,b=factor));offset=len(nodes)-1
        nodes.append(Node(Op.SUBTRACT if transform==5 else Op.ADD,a=left,b=offset));left=len(nodes)-1
    draw=int(rng.integers(60))
    op=COMPARISONS[4+draw] if draw<2 else COMPARISONS[int(rng.integers(4))]
    # A 120-candle rolling feature cannot add a crossing's extra candle.
    extent=Program(tuple(nodes),left).validate(CATALOG,output_unit=spec.unit)['history']
    if max(extent[left],extent[right])>=119 and op in (Op.CROSS_ABOVE,Op.CROSS_BELOW):
        op=Op.GREATER if op==Op.CROSS_ABOVE else Op.LESS
    nodes.append(Node(op,a=left,b=right))
    rule=Program(tuple(nodes),len(nodes)-1);rule.validate(CATALOG)
    return rule


from .search_space import semantic
PRICE_PEERS={kind:tuple(i for i,f in enumerate(CATALOG) if f.unit=='price' and semantic(f)==kind)
             for kind in ('price','magnitude')}


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
        result.append(assemble({s:random_rule(rng,s) for s in STAGES},policy,{s:age(rng) for s in STAGES if s!='entry'}))
    return result


def mutate(rng,parent):
    """Local rule edits, bounded resampling and independent policy/age edits."""
    from .complexity import simplify
    from .search_space import age,MAXIMUM_AGE,COMPARISONS,TEMPORAL
    rules=effective_rules(parent);stage=str(rng.choice(STAGES));rule=simplify(rules[stage])
    nodes=list(rule.nodes);action=int(rng.integers(8))
    if action==0:
        other=condition(rng)
        if len(nodes)+len(other.nodes)+1<=32:rule=combine(rule,other,int(rng.choice((Op.AND,Op.OR))))
    elif action==1:
        choices=[i for i,n in enumerate(nodes) if n.op==Op.CONSTANT and n.unit!='bool']
        if choices:
            i=int(rng.choice(choices));n=nodes[i]
            shifted=n.unit=='ratio' and n.value<-.5 and any(x.op==Op.FEATURE and semantic(CATALOG[x.feature])=='shifted_magnitude' for x in nodes)
            value=-1.+min(1.5,max(1e-6,(n.value+1)*float(rng.lognormal(0,.2)))) if shifted else n.value+float(rng.normal(0,max(1e-6,abs(n.value)*.05)))
            nodes[i]=replace(n,value=value)
            rule=Program(tuple(nodes),rule.output)
    elif action==2:
        choices=[i for i,n in enumerate(nodes) if n.op==Op.FEATURE]
        if choices:
            i=int(rng.choice(choices));n=nodes[i];f=CATALOG[n.feature]
            # Keep threshold/feature semantics aligned; full replacement explores other families.
            candidates=[j for j,g in enumerate(CATALOG) if g.unit==f.unit and semantic(g)==semantic(f)
                        and (g.lower,g.upper)==(f.lower,f.upper)]
            nodes[i]=replace(n,feature=int(rng.choice(candidates)));rule=Program(tuple(nodes),rule.output)
    elif action==3:
        choices=[i for i,n in enumerate(nodes) if n.op in TEMPORAL]
        if choices:
            i=int(rng.choice(choices));nodes[i]=replace(nodes[i],window=int(rng.choice(WINDOWS)))
            candidate=Program(tuple(nodes),rule.output)
            try:candidate.validate(CATALOG);rule=candidate
            except ValueError:pass  # Composed context remains bounded to 120 candles.
    elif action==4 and nodes[rule.output].op in (Op.AND,Op.OR):
        n=nodes[rule.output];rule=Program(tuple(nodes),int(rng.choice((n.a,n.b))))
    elif action==5:rule=random_rule(rng,stage)
    elif action==6:
        choices=[i for i,n in enumerate(nodes) if n.op in COMPARISONS or n.op in (Op.AND,Op.OR)]
        if choices:
            i=int(rng.choice(choices));n=nodes[i]
            ops=(Op.AND,Op.OR) if n.op in (Op.AND,Op.OR) else COMPARISONS[:4]
            nodes[i]=replace(n,op=int(rng.choice(ops)));rule=Program(tuple(nodes),rule.output)
    elif action==7 and len(nodes)<32:
        rule=Program(tuple(nodes)+(Node(Op.NOT,a=rule.output),),len(nodes))
    rules[stage]=simplify(rule)
    names=tuple(Policy.__dataclass_fields__)+tuple('age_'+s for s in STAGES if s!='entry')
    name=str(rng.choice(names));policy=parent.policy;ages=dict(parent.minimum_age)
    if name.startswith('age_'):
        stage=name[4:];ages[stage]=age(rng) if rng.random()<.5 else int(min(MAXIMUM_AGE,max(0,ages.get(stage,0)+rng.integers(-10,11))))
    elif name.startswith('swing_'):
        if rng.random()<.25:policy=replace(policy,swing_left=0,swing_right=0)
        else:
            pair={s:getattr(policy,s) or int(rng.choice(SWING_WINDOWS)) for s in ('swing_left','swing_right')}
            pair[name]=int(rng.choice(SWING_WINDOWS));policy=replace(policy,**pair)
    elif name in ('maximum_adds','cooldown') or rng.random()<.5:
        policy=replace(policy,**{name:random_policy_gene(rng,name)})
    else:
        lo=0. if name in ('add_minimum_profit','reduce_minimum_profit') else 1e-4
        policy=replace(policy,**{name:float(min(1,max(lo,getattr(policy,name)*float(rng.lognormal(0,.2))+rng.normal(0,.001))))})
    return assemble(rules,policy,ages)


def crossover(rng,left,right):
    """Typed rule blocks and independently recombined policy genes."""
    l=effective_rules(left);r=effective_rules(right)
    selected={s:(l if rng.integers(2)==0 else r)[s] for s in STAGES}
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
