"""Bounded generation selection; no changes to rule or financial evaluation."""
from dataclasses import asdict
from hashlib import sha256
import json
from .genome import effective_rules,sample,mutate
from .rule_plan import prune

VERSION='unique-program-performance-elites-v1'

def program_key(member):
    policy=asdict(member.policy)
    if policy['swing_left']:policy.pop('stop_fraction')  # Unreachable percentage stop.
    value=dict(rules={s:prune(p).payload() for s,p in effective_rules(member).items()},
               policy=policy,minimum_age=member.minimum_age)
    return sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def profiles(ordered,count):
    # Exact comparisons, across every metric and every training session.
    # Equal aggregates are not asserted to be equal trade paths.
    keys=sorted(set.intersection(*(set(v) for v in ordered)))
    return [tuple(v[k][i] for v in ordered for k in keys) for i in range(count)]

def summarize(population,ordered,rank):
    signatures=profiles(ordered,len(population));groups={};representatives=[]
    for i in rank:
        if signatures[i] not in groups:groups[signatures[i]]=[];representatives.append(i)
        groups[signatures[i]].append(i)
    sizes={i:len(groups[signatures[i]]) for i in rank}
    return representatives,sizes,dict(unique_programs=len({program_key(v) for v in population}),
        unique_performance_profiles=len(groups),evaluated_candidates=len(population),
        equivalent_candidate_share=1-len(groups)/max(1,len(rank)))

def next_population(rng,population,representatives):
    count=len(population);elite_count=max(1,count//10);fresh_count=max(1,count//10)
    selected=[];seen=set();rejected=0;fallbacks=0
    def accept(member):
        nonlocal rejected
        key=program_key(member)
        if key in seen:rejected+=1;return False
        seen.add(key);selected.append(member);return True
    for i in representatives:
        accept(population[i])
        if len(selected)>=elite_count:break
    actual_elites=len(selected)
    def fresh():
        for _ in range(32):
            if accept(sample(rng,1)[0]):return
        raise ValueError('Unique fresh candidate generation exhausted; preserve checkpoint')
    for _ in range(fresh_count):fresh()
    while len(selected)<count:
        for _ in range(8):
            tournament=rng.integers(0,len(representatives),size=3)
            if accept(mutate(rng,population[representatives[int(tournament.min())]])):break
        else:fallbacks+=1;fresh()
    return selected,dict(elites=actual_elites,fresh_candidates=fresh_count,
        rejected_duplicate_offspring=rejected,fresh_fallbacks=fallbacks)
