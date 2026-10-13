"""Distinct successful gene edits; rates/masks are drawn by the population selector."""
from dataclasses import replace
import numpy as np
from .features import CATALOG
from .program import Node,Program,Op,WINDOWS
from .expressions import COMPARISONS,TEMPORAL,UNARY
from .search_space import mutation_feature,MAXIMUM_AGE


def reachable(program):
    used=set()
    def visit(i):
        if i in used:return
        used.add(i);node=program.nodes[i]
        if node.op not in (Op.FEATURE,Op.CONSTANT):
            visit(node.a)
            if node.op not in UNARY:visit(node.b)
    visit(program.output)
    return used


def fields(member):
    from .genome import effective_rules,Policy,STAGES
    result=[]
    for stage,p in effective_rules(member).items():
        result.append((stage,-1,'structure'))
        for i,n in enumerate(p.nodes):
            if n.op==Op.FEATURE:result.append((stage,i,'feature'))
            elif n.op==Op.CONSTANT:result.append((stage,i,'value'))
            else:
                result.append((stage,i,'a'))
                if n.op not in UNARY:result.append((stage,i,'b'))
                if n.op in TEMPORAL:result.append((stage,i,'window'))
                if n.op in COMPARISONS or n.op in (Op.AND,Op.OR,Op.ADD,Op.SUBTRACT):result.append((stage,i,'op'))
    result.extend(('policy',-1,name) for name in Policy.__dataclass_fields__)
    result.append(('policy',-1,'swing_mode'))
    result.extend(('age',-1,s) for s in STAGES if s!='entry')
    return result


def mutate_fields(rng,parent,count):
    """Edit 1..5 distinct logical fields, with bounded retries and exact reporting."""
    from .genome import assemble,effective_rules,random_policy_gene
    from .program import WINDOWS
    from research.vectorized_backtest.v6.torch_backtest.history_bank import SWING_WINDOWS
    if not 1<=count<=5:raise ValueError('Mutation edit count must be 1..5')
    rules=dict(effective_rules(parent));policy=parent.policy;ages=dict(parent.minimum_age);applied=[]
    choices=fields(parent)
    for choice in rng.permutation(len(choices)):
        stage,index,key=choices[int(choice)];candidate_rules=dict(rules);candidate_policy=policy;candidate_ages=dict(ages)
        for _ in range(8):
            if stage=='policy':
                if key.startswith('swing_') and any(e['stage']=='policy' and e['field'].startswith('swing_') and (key=='swing_mode' or e['field']=='swing_mode') for e in applied):break
                if key=='swing_mode':
                    pair=(0,0) if policy.swing_left else (int(rng.choice(SWING_WINDOWS)),int(rng.choice(SWING_WINDOWS)))
                    candidate_policy=replace(policy,swing_left=pair[0],swing_right=pair[1])
                elif key.startswith('swing_'):
                    if not policy.swing_left:break
                    options=[w for w in SWING_WINDOWS if w!=getattr(policy,key)]
                    candidate_policy=replace(policy,**{key:int(rng.choice(options))})
                else:
                    candidate_policy=replace(policy,**{key:random_policy_gene(rng,key)})
                if candidate_policy==policy:continue
            elif stage=='age':
                old=ages.get(key,0);value=int(rng.integers(MAXIMUM_AGE));value+=value>=old
                candidate_ages[key]=value
            else:
                if any(e['stage']==stage and e['field']=='structure' for e in applied):break
                p=rules[stage]
                if key=='structure':
                    if any(e['stage']==stage for e in applied):break
                    from .expressions import exchange
                    from .genome import random_rule
                    updated,changed=exchange(rng,p,random_rule(rng,stage))
                    if not changed:continue
                    candidate_rules[stage]=updated
                    child=assemble(candidate_rules,candidate_policy,candidate_ages)
                    rules,policy,ages=candidate_rules,candidate_policy,candidate_ages
                    applied.append(dict(stage=stage,node=-1,field=key));break
                nodes=list(p.nodes);n=nodes[index]
                if index not in reachable(p):break
                if key=='feature':value=mutation_feature(rng,n.feature)
                elif key=='window':value=int(rng.choice([w for w in WINDOWS if w!=n.window]))
                elif key=='value':
                    value=1.-n.value if n.unit=='bool' else n.value+float(rng.normal(0,max(1e-6,abs(n.value)*.05)))
                elif key=='op':
                    options=(Op.AND,Op.OR) if n.op in (Op.AND,Op.OR) else (Op.ADD,Op.SUBTRACT) if n.op in (Op.ADD,Op.SUBTRACT) else COMPARISONS
                    value=int(rng.choice([o for o in options if o!=n.op]))
                else:
                    units=p.validate(CATALOG)['units'];current=getattr(n,key)
                    options=[j for j in range(index) if j!=current and units[j]==units[current]]
                    if not options:break
                    value=int(rng.choice(options))
                if value==getattr(n,key):continue
                nodes[index]=replace(n,**{key:value});updated=Program(tuple(nodes),p.output)
                try:updated.validate(CATALOG)
                except ValueError:continue
                retained=reachable(updated)
                if any(edit['stage']==stage and edit['node'] not in retained for edit in applied):continue
                candidate_rules[stage]=updated
            try:child=assemble(candidate_rules,candidate_policy,candidate_ages)
            except ValueError:continue
            rules,policy,ages=candidate_rules,candidate_policy,candidate_ages
            applied.append(dict(stage=stage,node=index,field=key));break
        if len(applied)==count:return child,applied
    raise RuntimeError(f'Could apply only {len(applied)} of {count} requested distinct gene edits')
