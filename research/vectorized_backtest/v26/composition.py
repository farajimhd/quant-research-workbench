"""Bounded typed composition; routers retain coherent leaf strategies."""
from dataclasses import replace, fields
from .genome import Individual, Policy, STAGES, effective_rules, assemble, combine, random_rule
from .program import Program, Node, Op

MODES = ('priority', 'reverse_priority', 'conditional', 'agreement', 'exclusive', 'overlay')
OPERATORS = ('subtree', 'lifecycle', 'union', 'intersection', 'difference', 'conditional_rule', 'parameters') + MODES
MAXIMUM_TOTAL_NODES = 1408


def leaf(rng, member):
    return member if member.routing is None else member.routing['left' if rng.integers(2)==0 else 'right']


def validate_routing(member):
    r=member.routing
    if set(r)!= {'mode','left','right','condition'} or r['mode'] not in MODES:
        raise ValueError('Invalid routing contract')
    for key in ('left','right'):
        if not isinstance(r[key],Individual) or r[key].routing is not None:
            raise ValueError('Two leaves and one routing layer only')
        r[key].validate()
    if (r['condition'] is not None)!=(r['mode']=='conditional'):
        raise ValueError('Conditional router must bind one condition')
    if r['condition'] is not None:
        from .features import CATALOG
        from .semantics import validate_rule
        r['condition'].validate(CATALOG);validate_rule(r['condition'],'entry')
    from .complexity import count
    if count(member)>MAXIMUM_TOTAL_NODES:raise ValueError('Total executable-node budget exceeded')


def routed(mode,left,right,condition=None):
    return replace(left,routing=dict(mode=mode,left=left,right=right,condition=condition)).validate()


def negate(p):
    return Program(p.nodes+(Node(Op.NOT,a=p.output),),len(p.nodes))


def cross(rng,a,b,operator=None):
    left,right=leaf(rng,a),leaf(rng,b)
    op=str(rng.choice(OPERATORS)) if operator is None else operator
    if op in MODES:
        return routed(op,left,right,random_rule(rng,'entry') if op=='conditional' else None),op
    rules=dict(effective_rules(left));other=effective_rules(right);policy=left.policy;ages=dict(left.minimum_age)
    if op=='subtree':
        from .expressions import exchange
        stage=str(rng.choice(STAGES));rules[stage],_=exchange(rng,rules[stage],other[stage])
    elif op=='lifecycle':
        # Entry, exit/trail, and add/reduce travel as coherent blocks with policy genes.
        genes={f.name:getattr(policy,f.name) for f in fields(Policy)}
        blocks=((('entry',),('stop_fraction','swing_left','swing_right')),
                (('exit','trail'),('trail_fraction',)),
                (('add','reduce'),('target_fraction','reduce_fraction','add_minimum_profit','reduce_minimum_profit','maximum_adds','cooldown')))
        for stages,names in blocks:
            if rng.integers(2):
                for stage in stages:rules[stage]=other[stage];ages[stage]=right.minimum_age.get(stage,0)
                for name in names:genes[name]=getattr(right.policy,name)
        policy=Policy(**genes)
    elif op=='parameters':
        genes={}
        for f in fields(Policy):
            x,y=getattr(left.policy,f.name),getattr(right.policy,f.name)
            genes[f.name]=float(x+(y-x)*rng.random()) if isinstance(x,float) else (x if rng.integers(2)==0 else y)
        # Swing mode/windows form one categorical gene, never an invalid partial pair.
        swing=left.policy if rng.integers(2)==0 else right.policy
        genes.update(swing_left=swing.swing_left,swing_right=swing.swing_right);policy=Policy(**genes)
    elif op in ('union','intersection','difference','conditional_rule'):
        stage=str(rng.choice(STAGES));x,y=rules[stage],other[stage]
        if op=='union':rules[stage]=combine(x,y,Op.OR)
        elif op=='intersection':rules[stage]=combine(x,y,Op.AND)
        elif op=='difference':rules[stage]=combine(x,negate(y),Op.AND)
        else:
            condition=random_rule(rng,'entry')
            rules[stage]=combine(combine(condition,x,Op.AND),combine(negate(condition),y,Op.AND),Op.OR)
    else:raise ValueError('Unknown composition operator')
    return assemble(rules,policy,ages),op


def mutation(rng,parent,count,crossover_child=False):
    from .variation import mutate_fields
    if parent.routing is None:
        return mutate_fields(rng,parent,1 if crossover_child else count)
    r=dict(parent.routing);choice=int(rng.integers(5))
    if choice==0:
        child=leaf(rng,parent);return child,[dict(stage='routing',field='collapse')]
    if choice==1:
        r['left'],r['right']=r['right'],r['left'];edit='reverse_branches'
    elif choice==2:
        r['mode']=str(rng.choice([m for m in MODES if m!=r['mode']]))
        r['condition']=random_rule(rng,'entry') if r['mode']=='conditional' else None;edit='mode'
    elif choice==3 and r['mode']=='conditional':
        r['condition']=random_rule(rng,'entry');edit='condition'
    else:
        key='left' if rng.integers(2)==0 else 'right'
        r[key],edits=mutate_fields(rng,r[key],1 if crossover_child else count);edit=key+':'+','.join(e['field'] for e in edits)
    return replace(r['left'],routing=r).validate(),[dict(stage='routing',field=edit)]
