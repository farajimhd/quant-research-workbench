"""Random-only initialization. No filesystem or previous-campaign input route."""
from .genome import sample
from .lineage import program_key
from .search_space import VERSION


def initialize(rng,size):
    if not 10<=size<=65536:raise ValueError('Invalid initial population size')
    from .sampling_report import snapshot,delta,coverage
    before=snapshot()
    members=[];keys=set();rejected=0
    for _ in range(size*32):
        member=sample(rng,1)[0];key=program_key(member)
        if key in keys:rejected+=1;continue
        members.append(member);keys.add(key)
        if len(members)==size:
            return members,dict(version=VERSION,seed_count=0,random_count=size,
                unique_count=len(keys),duplicate_rejections=rejected,past_results_loaded=False,sampling=delta(before),coverage=coverage(members))
    raise ValueError('Unique random population budget exhausted')
