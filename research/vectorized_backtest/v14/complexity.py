"""Safe structural simplification preserves feature-validity semantics."""
from dataclasses import replace
from research.vectorized_backtest.v6.torch_backtest.program import Program, Op
from .genome import effective_rules


def simplify(program):
    nodes=[];seen={};mapping={}
    def visit(i):
        if i in mapping:return mapping[i]
        n=program.nodes[i]
        if n.op not in (Op.FEATURE,Op.CONSTANT):
            a=visit(n.a)
            unary=n.op in (Op.NOT,Op.LAG,Op.MEAN,Op.MINIMUM,Op.MAXIMUM,Op.DIFFERENCE,Op.ABS)
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
    return sum(len(simplify(p).nodes) for p in effective_rules(member).values())+sum(v>0 for v in member.minimum_age.values())
