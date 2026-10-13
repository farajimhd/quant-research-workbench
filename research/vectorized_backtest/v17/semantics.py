"""Bounded structural checks; execution masks and numerical kernels stay intact."""
from collections import Counter
from dataclasses import asdict
from functools import lru_cache
from .program import Op

REJECTIONS = Counter()
UNARY = (Op.NOT, Op.LAG, Op.MEAN, Op.MINIMUM, Op.MAXIMUM,
         Op.DIFFERENCE, Op.ABS, Op.BOOL_RATIO)
COMPARISONS = (Op.GREATER, Op.LESS, Op.GREATER_EQUAL, Op.LESS_EQUAL,
               Op.CROSS_ABOVE, Op.CROSS_BELOW)


class SemanticError(ValueError):
    pass


@lru_cache(maxsize=8192)
def analyze(program):
    """Prove constants on valid inputs only; never infer empirical inactivity."""
    rows = {}; self_comparisons = []
    def visit(i):
        if i in rows:
            return rows[i]
        n = program.nodes[i]; op = Op(n.op); constant = None
        if op == Op.FEATURE:
            key = ('feature', n.feature)
        elif op == Op.CONSTANT:
            key = ('constant', n.unit, n.value); constant = n.value
        else:
            a, ca = visit(n.a)
            b, cb = (None, None) if op in UNARY else visit(n.b)
            key = (int(op), a, b, n.window)
            if op in COMPARISONS and a == b:
                self_comparisons.append(i)
                constant = op in (Op.GREATER_EQUAL, Op.LESS_EQUAL)
            elif op == Op.NOT and ca is not None:
                constant = not bool(ca)
            elif op == Op.AND:
                if (ca is not None and not ca) or (cb is not None and not cb):
                    constant = False
                elif ca is not None and cb is not None:
                    constant = bool(ca and cb)
            elif op == Op.OR:
                if (ca is not None and ca) or (cb is not None and cb):
                    constant = True
                elif ca is not None and cb is not None:
                    constant = bool(ca or cb)
            elif op in COMPARISONS[:4] and ca is not None and cb is not None:
                constant = {Op.GREATER: ca > cb, Op.LESS: ca < cb,
                            Op.GREATER_EQUAL: ca >= cb, Op.LESS_EQUAL: ca <= cb}[op]
            elif op in (Op.LAG, Op.MEAN, Op.MINIMUM, Op.MAXIMUM, Op.ABS) and ca is not None:
                constant = abs(ca) if op == Op.ABS else ca
            elif op == Op.BOOL_RATIO and ca is not None:
                constant = float(bool(ca))
        rows[i] = (key, constant)
        return rows[i]
    key, constant = visit(program.output)
    return dict(key=key, constant=constant, reachable=frozenset(rows),
                self_comparisons=tuple(self_comparisons),
                explicit_constant=program.nodes[program.output].op == Op.CONSTANT)


def validate_rule(program, stage):
    info = analyze(program)
    reason = ('self_comparison' if info['self_comparisons'] else
              'inactive_entry' if stage == 'entry' and info['constant'] is not None and not bool(info['constant']) else
              'implicit_constant_signal' if info['constant'] is not None and not info['explicit_constant'] else None)
    if reason:
        REJECTIONS[reason] += 1
        raise SemanticError(reason)
    return info


def disabled(program):
    value = analyze(program)['constant']
    return value is not None and not bool(value)


def identity(member):
    """Drop only proven execution-irrelevant genes, preserving true-signal masks."""
    from .genome import effective_rules
    from .complexity import simplify
    rules = effective_rules(member); policy = asdict(member.policy)
    ages = dict(member.minimum_age)
    if policy['swing_left']:
        policy.pop('stop_fraction')
    for stage in rules:
        if disabled(rules[stage]):
            ages.pop(stage, None)
    if disabled(rules['trail']):
        policy.pop('trail_fraction')
    if disabled(rules['reduce']):
        policy.pop('reduce_minimum_profit')
    if disabled(rules['add']) or policy['maximum_adds'] == 0:
        for name in ('add_minimum_profit', 'maximum_adds'):
            policy.pop(name)
        ages.pop('add', None)
        rule_payload = {s: simplify(p).payload() for s, p in rules.items() if s != 'add'}
        rule_payload['add'] = {'disabled': True}
    else:
        rule_payload = {s: simplify(p).payload() for s, p in rules.items()}
    for stage, p in rules.items():
        if disabled(p):
            rule_payload[stage] = {'disabled': True}
    return dict(rules=rule_payload, policy=policy,
                minimum_age={s: v for s, v in ages.items() if v})


def snapshot():
    return dict(sorted(REJECTIONS.items()))
