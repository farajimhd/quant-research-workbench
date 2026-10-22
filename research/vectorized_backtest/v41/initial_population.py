"""Random-only initialization. No filesystem or previous-campaign input route."""
from .genome import sample
from .lineage import program_key
from .config import VERSION


def initialize_random(rng,size,*,probe=None):
    if not 10<=size<=65536:raise ValueError('Invalid initial population size')
    from .sampling_report import snapshot,delta,coverage
    before=snapshot()
    members=[];keys=set();rejected=0
    if probe is None:raise ValueError('V41 requires a source-bound training activation probe')
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


def initialize(rng,size,*,probe=None):
    import os,json
    from pathlib import Path
    cache=os.environ.get('V41_INITIAL_POOL')
    if cache:
        from .run_search import source_hash,digest
        from .genome import Individual
        saved=json.loads(Path(cache).read_bytes())
        if saved['source_sha256']!=source_hash() or saved['probe_sha256']!=probe['sha256'] or saved['rng_before']!=rng.bit_generator.state or len(saved['population'])!=size or digest(saved['population'])!=saved['population_sha256']:
            raise ValueError('Prepared V41 initialization identity changed')
        members=[Individual.restore(p) for p in saved['population']]
        rng.bit_generator.state=saved['rng_after']
        return members,dict(saved['report'],prepared_initialization_reused=True)
    from time import perf_counter
    from .templates import sample_templates
    if size!=6144:raise ValueError('V41 experiment requires population 1024 and 6x initialization')
    started=perf_counter()
    randoms,report=initialize_random(rng,3072,probe=probe)
    random_seconds=perf_counter()-started;started=perf_counter()
    templates,families=sample_templates();members=templates+randoms
    keys={program_key(m) for m in members}
    if len(keys)!=size:raise ValueError('V41 seed population contains semantic duplicates')
    return members,dict(report,random_count=3072,seed_count=3072,unique_count=size,
        initial_families=families+['random']*3072,random_sampling_seconds=random_seconds,
        template_sampling_seconds=perf_counter()-started,template_calibration=False)
