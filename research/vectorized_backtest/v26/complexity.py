"""Safe structural simplification preserves feature-validity semantics."""
from dataclasses import replace
from functools import lru_cache
from .program import Program, Op, Node
from .genome import effective_rules


def simplify(program):
    # Preserve signed-zero and numeric-type identity: dataclass equality alone
    # would merge 0, 0.0 and -0.0 despite their distinct canonical JSON bytes.
    return _simplify(program,repr(program))


@lru_cache(maxsize=8192)
def _simplify(program,literal_identity):
    nodes=[];seen={};mapping={}
    def visit(i):
        if i in mapping:return mapping[i]
        n=program.nodes[i]
        op=Op(n.op)
        if op==Op.FEATURE:n=Node(op,feature=n.feature)
        elif op==Op.CONSTANT:n=Node(op,value=n.value,unit=n.unit)
        else:
            temporal=op in (Op.LAG,Op.MEAN,Op.MINIMUM,Op.MAXIMUM,Op.DIFFERENCE,Op.STD)
            n=Node(op,a=n.a,b=n.b,window=n.window if temporal else 1)
        if n.op not in (Op.FEATURE,Op.CONSTANT):
            a=visit(n.a)
            unary=n.op in (Op.NOT,Op.LAG,Op.MEAN,Op.MINIMUM,Op.MAXIMUM,Op.DIFFERENCE,Op.STD,Op.ABS,Op.BOOL_RATIO)
            b=-1 if unary else visit(n.b)
            n=replace(n,a=a,b=b)
            if n.op in (Op.AND,Op.OR) and a==b:
                mapping[i]=a;return a
        # Identical subexpressions share a node; no unsafe x/x or missing-mask folding.
        if n not in seen:seen[n]=len(nodes);nodes.append(n)
        mapping[i]=seen[n];return mapping[i]
    output=visit(program.output)
    from .rule_plan import prune
    return prune(Program(tuple(nodes),output))


def count(member):
    if member.routing is not None:
        r=member.routing
        return count(r["left"])+count(r["right"])+(len(r["condition"].nodes) if r.get("condition") is not None else 0)
    return sum(len(simplify(p).nodes) for p in effective_rules(member).values())+sum(v>0 for v in member.minimum_age.values())
