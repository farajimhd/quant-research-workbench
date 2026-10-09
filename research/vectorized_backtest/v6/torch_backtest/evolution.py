"""Seed-owned variable ruleset construction and bounded insert/delete mutation."""
from dataclasses import dataclass
import numpy as np
from .feature_bank import CATALOG
from .program import Node,Program,Op,WINDOWS,MAX_NODES
from .genome import StrategySpace
from dataclasses import replace
import math

STAGES=('entry','exit','trail','replacement')

SAMPLING_CONTRACT='v6-semantic-relative-local-conditional-v1'

def threshold_bounds(spec):
    family=semantic_family(spec)
    if family=='relative_price':return -.20,.20
    if family=='macd':return -.15,.15
    if family=='relative_volatility':return 0.,.20
    if family=='relative_activity':return 0.,math.log1p(20.)
    if family=='rsi':return 0.,1.
    return spec.lower,spec.upper


def conditional_policy(policy,space,*,previous=None,rng=None):
    """Canonical inactive values; activating a mode draws fresh seeded genes."""
    from .genome import NAMES
    def active(row):
        result=dict(target_step_fraction=row[6]==0,
            adaptive_window=row[7]==1,adaptive_multiplier=row[7]==1,minimum_trail_fraction=row[7]==1,
            trail_up_fraction=row[7]==0,trail_stop_fraction=row[7]==0,
            swing_left_seconds=row[8]==1,swing_right_seconds=row[8]==1)
        result.update({n:bool(row[9]) for n in ('replacement_margin','replacement_confirm_seconds','replacement_cooldown_seconds','stagnation_weight','stagnation_seconds')})
        result.update({n:False for n in ('retest_tolerance_fraction','retest_timeout_seconds','retest_lookback_seconds')})
        return result
    new=active(policy);old=active(previous) if previous is not None else new
    activated=[n for n in new if new[n] and not old[n]]
    fresh=space.sample(rng,1)[0] if activated else None
    for name,enabled in new.items():
        index=space.policy_start+NAMES.index(name)
        if not enabled:policy[index]=float(space.default[index])
        elif name in activated:policy[index]=float(fresh[index])
    return policy


def reject_degenerate(nodes):
    """Reject structural identities, without estimating validity from data."""
    expressions=[]
    for node in nodes:
        if node.op==Op.FEATURE:expression=('feature',node.feature)
        elif node.op==Op.CONSTANT:expression=('literal',node.value,node.unit)
        else:
            a=expressions[node.a];b=expressions[node.b] if node.b>=0 else None
            if node.op in (Op.SUBTRACT,Op.DIVIDE,Op.GREATER,Op.GREATER_EQUAL,Op.LESS,Op.LESS_EQUAL,Op.CROSS_ABOVE,Op.CROSS_BELOW) and a==b:
                return True
            expression=(node.op,a,b,node.window)
        expressions.append(expression)
    return False


def semantic_family(feature):
    name=feature.name
    if feature.unit=='bool':return 'boolean'
    if 'distance_rel' in name or name in ('bar_vwap_rel','session_vwap_rel','ema_7_rel','ema_26_rel'):return 'relative_price'
    if name in ('macd_line_rel','macd_signal_rel'):return 'macd'
    if name=='rsi_14':return 'rsi'
    if name=='atr_14_rel':return 'relative_volatility'
    if 'log_rvol' in name:return 'relative_activity'
    if name in ('log_volume','log_volume_60s'):return 'share_volume'
    if name in ('log_trades','log_trades_60s'):return 'trade_count'
    if 'observation_count' in name:return 'level_observation_count'
    # No shared-unit comparisons between float size, time and level age.
    return name

def condition(rng,features=None):
    features=tuple(i for i in (range(len(CATALOG)) if features is None else features)
                   if CATALOG[i].unit!='log_price' and CATALOG[i].name not in ('premarket','regular','after_hours','time_of_day_sin','time_of_day_cos'))
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
    unit=spec.unit;low,high=threshold_bounds(spec)
    if rng.random()<.35:
        left=len(nodes)-1
        compatible=[i for i,f in enumerate(CATALOG) if i in features and semantic_family(f)==semantic_family(spec)]
        operation=int(rng.choice([Op.ADD,Op.SUBTRACT,Op.DIVIDE,Op.MULTIPLY]))
        if operation==Op.MULTIPLY:
            nodes.append(Node(Op.CONSTANT,value=float(rng.uniform(.1,3)),unit='ratio'))
        else:nodes.append(Node(Op.FEATURE,feature=int(rng.choice(compatible))))
        nodes.append(Node(operation,a=left,b=len(nodes)-1))
        if operation==Op.DIVIDE:unit='ratio';low=-2.;high=2.
    left=len(nodes)-1
    compatible=[i for i,f in enumerate(CATALOG) if i in features and f.unit==unit and semantic_family(f)==semantic_family(spec)
                and not (i==feature and left==0)]
    if compatible and rng.random()<.4:nodes.append(Node(Op.FEATURE,feature=int(rng.choice(compatible))))
    else:nodes.append(Node(Op.CONSTANT,value=float(rng.uniform(low,high)),unit=unit))
    nodes.append(Node(int(rng.choice([Op.GREATER,Op.GREATER_EQUAL,Op.LESS,Op.LESS_EQUAL,Op.CROSS_ABOVE,Op.CROSS_BELOW])),a=left,b=len(nodes)-1))
    p=Program(tuple(nodes),len(nodes)-1)
    try:p.validate(CATALOG)
    except ValueError:
        n=nodes[-1];nodes[-1]=Node(Op.GREATER,a=n.a,b=n.b)
    return nodes,len(nodes)-1


def usable_condition(rng,features=None):
    for _ in range(64):
        chunk,root=condition(rng,features)
        if not reject_degenerate(chunk):return chunk,root
    raise ValueError('Searchable features cannot produce a nondegenerate condition')

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
        policy=conditional_policy(policy,space)
        clauses={s:[usable_condition(rng,features) for _ in range(int(rng.integers(1,4)))] for s in STAGES}
        connectors={s:[int(rng.choice([Op.AND,Op.OR])) for _ in range(len(clauses[s])-1)] for s in STAGES}
        out.append(Individual(policy,clauses,connectors))
    return out

def mutate(rng,parent,space,features=None):
    policy=space.offspring(rng,np.asarray(parent.policy),np.asarray(parent.policy)).tolist()
    policy[:4]=space.default[:4].tolist();policy[50:]=space.default[50:].tolist()
    policy=conditional_policy(policy,space,previous=parent.policy,rng=rng)
    clauses={s:[(list(chunk),root) for chunk,root in parent.clauses[s]] for s in STAGES}
    connectors={s:list(parent.connectors[s]) for s in STAGES}
    stage=STAGES[int(rng.integers(len(STAGES)))];action=int(rng.integers(7))
    if action==0 and sum(len(c) for c,_ in clauses[stage])+len(clauses[stage])+5<=MAX_NODES:
        clauses[stage].append(usable_condition(rng,features));connectors[stage].append(int(rng.choice([Op.AND,Op.OR])))
    elif action==1 and len(clauses[stage])>1:
        index=int(rng.integers(len(clauses[stage])));clauses[stage].pop(index);connectors[stage].pop(max(0,index-1))
    elif action==2 and connectors[stage]:
        index=int(rng.integers(len(connectors[stage])));connectors[stage][index]=int(rng.choice([Op.AND,Op.OR]))
    elif action==3:
        choices=[(i,j) for i,(chunk,_) in enumerate(clauses[stage]) for j,node in enumerate(chunk) if node.op==Op.CONSTANT]
        if choices:
            i,j=choices[int(rng.integers(len(choices)))];node=clauses[stage][i][0][j]
            step=max(.01,abs(node.value)*.1)
            chunk,root=clauses[stage][i]
            if chunk[root].b==j:
                source=next(n.feature for n in chunk if n.op==Op.FEATURE)
                lo,hi=threshold_bounds(CATALOG[source])
            else:lo,hi=.1,3.
            clauses[stage][i][0][j]=replace(node,value=float(np.clip(node.value+rng.normal(0,step),lo,hi)))
    elif action==4:
        choices=[(i,j) for i,(chunk,_) in enumerate(clauses[stage]) for j,node in enumerate(chunk) if node.op in (Op.LAG,Op.MEAN,Op.MINIMUM,Op.MAXIMUM,Op.DIFFERENCE)]
        if choices:
            i,j=choices[int(rng.integers(len(choices)))];node=clauses[stage][i][0][j]
            windows=WINDOWS[:-1] if node.op in (Op.LAG,Op.DIFFERENCE) else WINDOWS
            index=windows.index(node.window);index=int(np.clip(index+rng.choice([-1,1]),0,len(windows)-1))
            clauses[stage][i][0][j]=replace(node,window=windows[index])
    elif action==5:
        i=int(rng.integers(len(clauses[stage])));chunk,root=clauses[stage][i];node=chunk[root]
        if node.op in (Op.GREATER,Op.GREATER_EQUAL,Op.LESS,Op.LESS_EQUAL,Op.CROSS_ABOVE,Op.CROSS_BELOW):
            chunk[root]=replace(node,op=int(rng.choice([Op.GREATER,Op.GREATER_EQUAL,Op.LESS,Op.LESS_EQUAL,Op.CROSS_ABOVE,Op.CROSS_BELOW])))
    else:
        index=int(rng.integers(len(clauses[stage])));clauses[stage][index]=usable_condition(rng,features)
    child=Individual(policy,clauses,connectors)
    try:
        child.programs()
        if any(reject_degenerate(chunk) for rows in clauses.values() for chunk,_ in rows):raise ValueError('Degenerate mutation')
    except ValueError:return Individual(policy,parent.clauses,parent.connectors)
    return child
