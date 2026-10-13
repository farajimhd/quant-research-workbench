"""Typed expression generation and bounded compatible subtree exchange."""
from dataclasses import replace
from .program import Node,Program,Op,WINDOWS
from .features import CATALOG
from .search_space import GROUPS,feature_index,threshold

UNARY=(Op.NOT,Op.LAG,Op.MEAN,Op.MINIMUM,Op.MAXIMUM,Op.DIFFERENCE,Op.ABS,Op.BOOL_RATIO)
COMPARISONS=(Op.GREATER,Op.LESS,Op.GREATER_EQUAL,Op.LESS_EQUAL,Op.CROSS_ABOVE,Op.CROSS_BELOW)
TEMPORAL=(Op.LAG,Op.MEAN,Op.MINIMUM,Op.MAXIMUM,Op.DIFFERENCE)
UNIT_GROUPS={unit:tuple(g for group in GROUPS if (g:=tuple(i for i in group if CATALOG[i].unit==unit)))
             for unit in {f.unit for f in CATALOG}}


def merge(left,right,op):
    offset=len(left.nodes)
    nodes=left.nodes+tuple(replace(n,a=n.a+offset if n.a>=0 else -1,b=n.b+offset if n.b>=0 else -1) for n in right.nodes)
    return Program(nodes+(Node(op,a=left.output,b=right.output+offset),),len(nodes))


def feature(rng,unit,index=None):
    index=feature_index(rng,UNIT_GROUPS[unit]) if index is None else index
    return Program((Node(Op.FEATURE,feature=int(index)),),0)


def numeric(rng,unit,depth=2,index=None):
    """Both operands can be independent, compatible, recursively built formulas."""
    # Retain V15's mostly pointwise prior; richer forms are reachable without
    # making every candidate pay for nested rolling reductions.
    leaf=feature(rng,unit,index);choice=int(rng.integers(60)) if depth else 0
    if unit=='ratio' and choice==19:
        boolean=feature(rng,'bool')
        leaf=Program(boolean.nodes+(Node(Op.BOOL_RATIO,a=boolean.output),),1)
    elif 16<=choice<=19:
        if choice in (16,17):
            left=numeric(rng,unit,depth-1,index);right=numeric(rng,unit,depth-1)
            return merge(left,right,Op.ADD if choice==16 else Op.SUBTRACT)
        left=numeric(rng,unit,depth-1,index);right=numeric(rng,'ratio',depth-1)
        return merge(left,right,Op.MULTIPLY if choice==18 else Op.DIVIDE)
    if choice==20:return Program(leaf.nodes+(Node(Op.ABS,a=leaf.output),),len(leaf.nodes))
    if 12<=choice<=15:
        op=TEMPORAL[int(rng.integers(len(TEMPORAL)))];w=int(rng.choice(WINDOWS))
        if op in (Op.LAG,Op.DIFFERENCE) and w==120:w=60
        return Program(leaf.nodes+(Node(op,a=leaf.output,window=w),),len(leaf.nodes))
    return leaf


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
        if rng.random()<.45:
            right=numeric(rng,spec.unit)
        else:
            value=threshold(rng,index)
            # Delta thresholds retain a meaningful nonzero searchable literal.
            if left.nodes[left.output].op==Op.DIFFERENCE:value=float(rng.uniform(-.1,.1))
            right=Program((Node(Op.CONSTANT,value=value,unit=spec.unit),),0)
        if left==right:continue
        op=COMPARISONS[int(rng.integers(4))] if rng.random()>.03 else COMPARISONS[4+int(rng.integers(2))]
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
