"""Fresh equal random/template initialization; no previous-result input route."""
from .genome import Individual
from .lineage import program_key

def immigrant(rng,probe):
    """Use the same training-only priors and activation calibration as founders."""
    if probe is None:raise ValueError('Fresh V57 offspring require the sealed training probe')
    from .genome import sample
    from .calibration import policy_families,calibrate
    member=sample(rng,1)[0]
    # policy_families balances batches; a singleton would always be independent.
    members,_=policy_families(rng,[member,member])
    members=[members[int(rng.integers(2))]]
    members,_=calibrate(rng,members,probe)
    return members[0]

def initialize(rng,size,*,probe=None):
    if size!=6144:raise ValueError('V57 pilot requires 3072 templates plus 3072 fresh random candidates')
    from .random_population import initialize_random
    from .templates import sample_templates
    randoms,report=initialize_random(rng,3072,probe=probe)
    templates,families=sample_templates()
    members=[Individual.restore(m.payload()) for m in templates+randoms]
    if len(members)!=6144 or len({program_key(m) for m in members})!=6144:raise ValueError('Fresh cohort must be complete and unique')
    return members,dict(report,experiment='v57',random_count=3072,seed_count=3072,initial_families=families+['random']*3072,past_results_loaded=False,initialization='fresh random plus templates; no archives or pool-file environment route')
