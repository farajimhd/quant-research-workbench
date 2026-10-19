"""Random-only initialization. No filesystem or previous-campaign input route."""
from .genome import sample
from .lineage import program_key
from .config import VERSION


def initialize(rng,size,*,probe=None):
    if not 10<=size<=65536:raise ValueError('Invalid initial population size')
    from .sampling_report import snapshot,delta,coverage
    before=snapshot()
    members=[];keys=set();rejected=0
    if probe is None:raise ValueError('V32 requires a source-bound training activation probe')
    proposals=iter(sample(rng,size,stratified=True,structural=True))
    for attempt in range(size*32):
        member=next(proposals) if attempt<size else sample(rng,1)[0];key=program_key(member)
        if key in keys:rejected+=1;continue
        members.append(member);keys.add(key)
        if len(members)==size:
            from .calibration import policy_families,calibrate
            members,families=policy_families(rng,members)
            members,calibration=calibrate(rng,members,probe)
            final_keys={program_key(m) for m in members}
            if len(final_keys)!=size:raise ValueError('Calibrated population lost uniqueness')
            return members,dict(version=VERSION,seed_count=0,random_count=size,
                unique_count=len(final_keys),duplicate_rejections=rejected,past_results_loaded=False,sampling=delta(before),coverage=coverage(members),
                calibration=calibration,policy_families=families)
    raise ValueError('Unique random population budget exhausted')
