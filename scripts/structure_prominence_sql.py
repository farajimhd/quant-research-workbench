"""Database-side ordered fold for reaction-prominence-1.

Input rows must be sorted by the canonical cursor, restricted to an actually
existing level episode. Tuple fields: price, lower, upper, side, prior_range
(zero means unavailable), active, accepted_break, split_factor (normally one).
The split factor is applied exactly once by the caller at its effective cursor.
State: completed, current_best, frozen_range, phase, side, completed_encounters.
This is a bounded stateful fold, not a claim that sequential work is parallel.
"""


def fold_sql(events, seed="tuple(0.,0.,0.,toUInt8(0),toInt8(0),toUInt64(0))"):
    def bind(name, value, body):
        return f'arrayElement(arrayMap({name} -> {body},[{value}]),1)'

    def finish(a):
        return f'tuple({a}.1+{a}.2,0.,0.,toUInt8(0),{a}.5,{a}.6+toUInt64({a}.4!=0))'

    scaled='tuple(a.1,a.2,a.3*x.8,a.4,a.5,a.6)'
    reset=f'if(x.7 OR (b.5!=0 AND b.5!=x.4),{finish("b")},b)'
    returned=f'if(c.4=2 AND x.1>=x.2 AND x.1<=x.3,{finish("c")},c)'
    started='if(d.4=0 AND x.1>=x.2 AND x.1<=x.3 AND x.5>0,tuple(d.1,d.2,x.5,toUInt8(1),d.5,d.6),d)'
    best='if(e.4!=0,greatest(e.2,greatest(if(x.4>0,x.1-x.3,x.2-x.1),0.)/if(e.3>0,e.3,1.)),e.2)'
    advanced=bind('v',best,'tuple(e.1,v,e.3,if(e.4!=0 AND v>=1,toUInt8(2),e.4),toInt8(x.4),e.6)')
    active=bind('d',returned,bind('e',started,advanced))
    inactive='tuple(c.1,c.2,c.3,c.4,toInt8(x.4),c.6)'
    body=bind('b',scaled,bind('c',reset,f'if(NOT x.6 OR x.7,{inactive},{active})'))
    return f'arrayFold((a,x)->{body},{events},{seed})'
