"""Typed expression generation and bounded compatible subtree exchange."""
from dataclasses import replace
from .program import Node,Program,Op,WINDOWS
from .features import CATALOG
from .search_space import GROUPS,feature_index,threshold,draw_numeric

UNARY=(Op.NOT,Op.LAG,Op.MEAN,Op.MINIMUM,Op.MAXIMUM,Op.DIFFERENCE,Op.STD,Op.ABS,Op.BOOL_RATIO)
COMPARISONS=(Op.GREATER,Op.LESS,Op.GREATER_EQUAL,Op.LESS_EQUAL,Op.CROSS_ABOVE,Op.CROSS_BELOW)
TEMPORAL=(Op.LAG,Op.MEAN,Op.MINIMUM,Op.MAXIMUM,Op.DIFFERENCE,Op.STD)
UNIT_GROUPS={unit:tuple(g for group in GROUPS if (g:=tuple(i for i in group if CATALOG[i].unit==unit)))
             for unit in {f.unit for f in CATALOG}}

RATIO_GROUPS=tuple(g for group in GROUPS if (g:=tuple(i for i in group if CATALOG[i].unit in ('price','dollars','ratio','duration'))))

def merge(left,right,op):
    offset=len(left.nodes)
    nodes=left.nodes+tuple(replace(n,a=n.a+offset if n.a>=0 else -1,b=n.b+offset if n.b>=0 else -1) for n in right.nodes)
    return Program(nodes+(Node(op,a=left.output,b=right.output+offset),),len(nodes))


def feature(rng,unit,index=None):
    index=feature_index(rng,UNIT_GROUPS[unit]) if index is None else index
    return Program((Node(Op.FEATURE,feature=int(index)),),0)


def temporal_ratio(rng,index=None,window=None):
    """Uncentered x / lag(x,h), using existing raw numeric feature channels."""
    if index is None:
        index=feature_index(rng,RATIO_GROUPS)
    w=int(rng.choice(WINDOWS[:-1])) if window is None else window
    return Program((Node(Op.FEATURE,feature=int(index)),Node(Op.LAG,a=0,window=w),Node(Op.DIVIDE,a=0,b=1)),2)


def numeric(rng,unit,depth=2,index=None):
    """Balanced bounded typed constructions; no implicit small-expression preference."""
    leaf=feature(rng,unit,index)
    if depth==0:return leaf
    choices=['leaf','temporal','abs','add','subtract','multiply','divide']
    if unit=='ratio':choices+=['same_unit_ratio','temporal_ratio','boolean_ratio']
    choice=str(rng.choice(choices))
    if choice=='leaf':return leaf
    if choice=='boolean_ratio':
        b=feature(rng,'bool');return Program(b.nodes+(Node(Op.BOOL_RATIO,a=b.output),),1)
    if choice=='temporal_ratio':return temporal_ratio(rng)
    if choice=='same_unit_ratio':
        source=str(rng.choice(('price','dollars','ratio','duration')))
        return merge(numeric(rng,source,depth-1),numeric(rng,source,depth-1),Op.DIVIDE)
    if choice=='temporal':
        op=int(rng.choice(TEMPORAL));w=int(rng.choice(WINDOWS[:-1] if op in (Op.LAG,Op.DIFFERENCE) else WINDOWS))
        return Program(leaf.nodes+(Node(op,a=leaf.output,window=w),),len(leaf.nodes))
    if choice=='abs':return Program(leaf.nodes+(Node(Op.ABS,a=leaf.output),),len(leaf.nodes))
    op={'add':Op.ADD,'subtract':Op.SUBTRACT,'multiply':Op.MULTIPLY,'divide':Op.DIVIDE}[choice]
    right_unit='ratio' if choice in ('multiply','divide') else unit
    return merge(numeric(rng,unit,depth-1,index),numeric(rng,right_unit,depth-1),op)


def atom(rng,index=None):
    index=feature_index(rng) if index is None else int(index);spec=CATALOG[index]
    if spec.unit=='bool':
        rule=feature(rng,'bool',index)
        if rng.random()<.25:
            nodes=rule.nodes+(Node(Op.BOOL_RATIO,a=rule.output),)
            w=int(rng.choice(WINDOWS));nodes+=(Node(Op.MEAN,a=1,window=w),)
            # A rate multiplied by its window is a rolling true-count.
            count=bool(rng.integers(2));limit=1.
            if count:
                nodes+=(Node(Op.CONSTANT,value=float(w),unit='ratio'),Node(Op.MULTIPLY,a=2,b=3));limit=float(w)
            left=len(nodes)-1;nodes+=(Node(Op.CONSTANT,value=float(rng.uniform(0,limit)),unit='ratio'),)
            nodes+=(Node(int(rng.choice(COMPARISONS[:4])),a=left,b=len(nodes)-1),)
            result=Program(nodes,len(nodes)-1);result.validate(CATALOG);return result
        return Program(rule.nodes+(Node(Op.NOT,a=0),),1) if rng.integers(2) else rule
    for _ in range(8):
        left=numeric(rng,spec.unit,index=index)
        if rng.random()<.5:
            right=numeric(rng,spec.unit)
        else:
            value=threshold(rng,index)
            # Raw positive-unit ratios are centered on one, not the source feature's zero-distance prior.
            root=left.nodes[left.output]
            if root.op==Op.DIVIDE:
                try:units=left.validate(CATALOG,output_unit=spec.unit)['units']
                except ValueError:continue
                if units[root.a]==units[root.b] and units[root.a] in ('price','dollars','duration'):
                    value=draw_numeric(rng,.01,4.)
            # Delta thresholds retain a meaningful nonzero searchable literal.
            if left.nodes[left.output].op==Op.DIFFERENCE:value=float(rng.uniform(-.1,.1))
            right=Program((Node(Op.CONSTANT,value=value,unit=spec.unit),),0)
        if left==right:continue
        op=int(rng.choice(COMPARISONS))
        rule=merge(left,right,op)
        try:rule.validate(CATALOG);return rule
        except ValueError:continue
    # Bounded direct comparison remains data-dependent and typed.
    return merge(feature(rng,spec.unit,index),Program((Node(Op.CONSTANT,value=threshold(rng,index),unit=spec.unit),),0),Op.GREATER)


def subprogram(program,root):
    nodes=[];mapping={}
    def visit(i):
        if i in mapping:return mapping[i]
        n=program.nodes[i]
        if n.op not in (Op.FEATURE,Op.CONSTANT):
            a=visit(n.a);b=-1 if n.op in UNARY else visit(n.b);n=replace(n,a=a,b=b)
        mapping[i]=len(nodes);nodes.append(n);return mapping[i]
    output=visit(root);return Program(tuple(nodes),output)


def exchange(rng,left,right):
    """Replace a recipient subtree with a same-unit donor subtree, then validate."""
    lu=left.validate(CATALOG)['units'];ru=right.validate(CATALOG)['units']
    pairs=[(i,j) for i,u in enumerate(lu) for j,v in enumerate(ru) if u==v]
    for index in rng.permutation(len(pairs))[:8]:
        target,donor=pairs[int(index)];piece=subprogram(right,donor);nodes=[];mapping={}
        def visit(i):
            if i in mapping:return mapping[i]
            if i==target:
                offset=len(nodes)
                nodes.extend(replace(n,a=n.a+offset if n.a>=0 else -1,b=n.b+offset if n.b>=0 else -1) for n in piece.nodes)
                mapping[i]=piece.output+offset;return mapping[i]
            n=left.nodes[i]
            if n.op not in (Op.FEATURE,Op.CONSTANT):n=replace(n,a=visit(n.a),b=-1 if n.op in UNARY else visit(n.b))
            mapping[i]=len(nodes);nodes.append(n);return mapping[i]
        output=visit(left.output);candidate=Program(tuple(nodes),output)
        try:
            candidate.validate(CATALOG)
            if candidate!=left:return candidate,True
        except ValueError:continue
    return left,False
