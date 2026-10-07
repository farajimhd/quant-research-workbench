"""Seed-owned variable ruleset construction and bounded insert/delete mutation."""
from dataclasses import dataclass
import numpy as np
from .feature_bank import CATALOG
from .program import Node,Program,Op,WINDOWS,MAX_NODES
from .genome import StrategySpace

STAGES=('entry','exit','trail','replacement')

def condition(rng,features=None):
    features=tuple(range(len(CATALOG))) if features is None else tuple(features)
    if not features or any(type(i) is not int or not 0<=i<len(CATALOG) for i in features):raise ValueError('Invalid searchable feature indices')
    feature=int(rng.choice(features));spec=CATALOG[feature]
    nodes=[Node(Op.FEATURE,feature=feature)]
    if spec.unit=='bool':
        if rng.random()<.25:nodes.append(Node(Op.NOT,a=0))
        return nodes,len(nodes)-1
    if rng.random()<.5:
        op=int(rng.choice([Op.LAG,Op.MEAN,Op.MINIMUM,Op.MAXIMUM,Op.DIFFERENCE]))
        windows=WINDOWS[:-1] if op in (Op.LAG,Op.DIFFERENCE) else WINDOWS
        nodes.append(Node(op,a=0,window=int(rng.choice(windows))))
    unit=spec.unit;low=spec.lower;high=spec.upper
    if rng.random()<.35:
        left=len(nodes)-1
        compatible=[i for i,f in enumerate(CATALOG) if i in features and f.unit==spec.unit]
        operation=int(rng.choice([Op.ADD,Op.SUBTRACT,Op.DIVIDE,Op.MULTIPLY]))
        if operation==Op.MULTIPLY:
            nodes.append(Node(Op.CONSTANT,value=float(rng.uniform(.1,3)),unit='ratio'))
        else:nodes.append(Node(Op.FEATURE,feature=int(rng.choice(compatible))))
        nodes.append(Node(operation,a=left,b=len(nodes)-1))
        if operation==Op.DIVIDE:unit='ratio';low=-2.;high=2.
    left=len(nodes)-1
    compatible=[i for i,f in enumerate(CATALOG) if i in features and f.unit==unit]
    if rng.random()<.4:nodes.append(Node(Op.FEATURE,feature=int(rng.choice(compatible))))
    else:nodes.append(Node(Op.CONSTANT,value=float(rng.uniform(low,high)),unit=unit))
    nodes.append(Node(int(rng.choice([Op.GREATER,Op.GREATER_EQUAL,Op.LESS,Op.LESS_EQUAL,Op.CROSS_ABOVE,Op.CROSS_BELOW])),a=left,b=len(nodes)-1))
    p=Program(tuple(nodes),len(nodes)-1)
    try:p.validate(CATALOG)
    except ValueError:
        n=nodes[-1];nodes[-1]=Node(Op.GREATER,a=n.a,b=n.b)
    return nodes,len(nodes)-1

def compose(clauses,connectors):
    nodes=[];root=None
    for index,(chunk,local) in enumerate(clauses):
        offset=len(nodes)
        nodes.extend(Node(n.op,n.a+offset if n.a>=0 else -1,n.b+offset if n.b>=0 else -1,n.feature,n.window,n.value,n.unit) for n in chunk)
        output=offset+local
        if root is None:root=output
        else:
            nodes.append(Node(connectors[index-1],a=root,b=output));root=len(nodes)-1
    return Program(tuple(nodes),root).validate(CATALOG) and Program(tuple(nodes),root)

@dataclass
class Individual:
    policy:list
    clauses:dict
    connectors:dict
    def programs(self):return {s:compose(self.clauses[s],self.connectors[s]) for s in STAGES}
    def payload(self):return dict(policy=self.policy,programs={s:p.payload() for s,p in self.programs().items()})

def sample(rng,space,count,features=None):
    policies=space.sample(rng,count)
    # V4 lifecycle programs replace the four-clause entry grammar and four
    # legacy entry-mode coordinates. They are not disguised searchable genes.
    policies[:,:4]=space.default[:4];policies[:,50:]=space.default[50:]
    policies=policies.tolist();out=[]
    for policy in policies:
        clauses={s:[condition(rng,features) for _ in range(int(rng.integers(1,4)))] for s in STAGES}
        connectors={s:[int(rng.choice([Op.AND,Op.OR])) for _ in range(len(clauses[s])-1)] for s in STAGES}
        out.append(Individual(policy,clauses,connectors))
    return out

def mutate(rng,parent,space,features=None):
    policy=space.offspring(rng,np.asarray(parent.policy),np.asarray(parent.policy)).tolist()
    policy[:4]=space.default[:4].tolist();policy[50:]=space.default[50:].tolist()
    clauses={s:[(list(chunk),root) for chunk,root in parent.clauses[s]] for s in STAGES}
    connectors={s:list(parent.connectors[s]) for s in STAGES}
    stage=STAGES[int(rng.integers(len(STAGES)))];action=int(rng.integers(4))
    if action==0 and sum(len(c) for c,_ in clauses[stage])+len(clauses[stage])+5<=MAX_NODES:
        clauses[stage].append(condition(rng,features));connectors[stage].append(int(rng.choice([Op.AND,Op.OR])))
    elif action==1 and len(clauses[stage])>1:
        index=int(rng.integers(len(clauses[stage])));clauses[stage].pop(index);connectors[stage].pop(max(0,index-1))
    elif action==2 and connectors[stage]:
        index=int(rng.integers(len(connectors[stage])));connectors[stage][index]=int(rng.choice([Op.AND,Op.OR]))
    else:
        index=int(rng.integers(len(clauses[stage])));clauses[stage][index]=condition(rng,features)
    child=Individual(policy,clauses,connectors)
    try:child.programs()
    except ValueError:return Individual(policy,parent.clauses,parent.connectors)
    return child
