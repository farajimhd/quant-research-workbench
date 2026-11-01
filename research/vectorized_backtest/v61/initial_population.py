"""Fresh equal random/template initialization; no previous-result input route."""
from .genome import Individual
from .lineage import program_key

def immigrant(rng,probe):
    """Use the same training-only priors and activation calibration as founders."""
    if probe is None:raise ValueError('Fresh V60 offspring require the sealed training probe')
    from .genome import sample
    from .calibration import policy_families,calibrate
    member=sample(rng,1)[0]
    # policy_families balances batches; a singleton would always be independent.
    members,_=policy_families(rng,[member,member])
    members=[members[int(rng.integers(2))]]
    members,_=calibrate(rng,members,probe)
    return members[0]

def initialize(rng,size,*,probe=None):
    """Fresh 50/25/25 small predicates, combinations and compact templates."""
    if size!=6144 or probe is None:raise ValueError('6144 fresh founders and training probe required')
    from dataclasses import replace
    from .genome import sample,random_network,STAGES
    from .calibration import calibrate
    from .initial_design import policies
    from .templates import sample_templates
    from .population_builder import build
    simple=sample(rng,3072,stratified=True,structural=True)
    combinations=sample(rng,1536,stratified=True,structural=True)
    sizes={s:rng.permutation(__import__('numpy').resize([3,4,5,6,7],1536)) for s in STAGES}
    combinations=[replace(m,networks={s:random_network(rng,s,int(sizes[s][i]),(i%3,(i//3)%2,0)) for s in STAGES}).validate() for i,m in enumerate(combinations)]
    randoms,calibration=calibrate(rng,policies(rng,simple+combinations),probe)
    templates,families=sample_templates()
    # Each V60 family allocation is even: retain half of every family, with
    # complete meaningful rules rather than truncating their dependency graph.
    templates=templates[::2];families=families[::2]
    members=randoms+templates
    members,construction=build(size,lambda stream,slot,attempt:members[slot],
        lambda stream,slot,attempt:immigrant(stream,probe),seed=int(rng.integers(2**32)))
    counts=[sum(len(n.nodes) for n in m.networks.values()) for m in members]
    return members,dict(experiment='v61',construction=construction,calibration=calibration,
        random_count=4608,seed_count=1536,past_results_loaded=False,
        initial_families=['simple']*3072+['combination']*1536+families,
        founder_nodes=dict(min=min(counts),median=float(__import__('numpy').median(counts)),max=max(counts)),
        initialization='fresh small 50/25/25; no archived genomes or financial selection')
