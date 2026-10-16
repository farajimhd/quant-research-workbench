"""Process-local generation diagnostics; never used to select training candidates."""
from collections import Counter
COUNTS=Counter()

def snapshot():return dict(COUNTS)

def delta(before):return {k:v-before.get(k,0) for k,v in COUNTS.items() if v!=before.get(k,0)}

def coverage(members):
    from .genome import effective_rules
    from .features import CATALOG
    from .search_space import family
    from .semantics import analyze
    result={k:Counter() for k in ('families','features','operators','windows','effective_sizes','maximum_adds')}
    for m in members:
        result['maximum_adds'][str(m.policy.maximum_adds)]+=1
        for p in effective_rules(m).values():
            live=analyze(p)['reachable'];result['effective_sizes'][str(len(live))]+=1
            for i in live:
                n=p.nodes[i];result['operators'][str(n.op)]+=1
                if n.feature>=0:
                    result['features'][CATALOG[n.feature].name]+=1;result['families'][str(family(CATALOG[n.feature]))]+=1
                if n.op in (13,14,15,16,17,23):result['windows'][str(n.window)]+=1
    return {k:dict(v) for k,v in result.items()}
