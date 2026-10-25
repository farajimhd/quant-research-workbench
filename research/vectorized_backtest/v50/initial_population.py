"""Fresh equal random/template initialization; no previous-result input route."""
from .genome import Individual
from .lineage import program_key

def initialize(rng,size,*,probe=None):
    if size!=6144:raise ValueError('V50 pilot requires 3072 templates plus 3072 fresh random candidates')
    from research.vectorized_backtest.v42.initial_population import initialize_random
    from research.vectorized_backtest.v42.templates import sample_templates
    randoms,report=initialize_random(rng,3072,probe=probe)
    templates,families=sample_templates()
    members=[Individual.restore(m.payload()) for m in templates+randoms]
    if len(members)!=6144 or len({program_key(m) for m in members})!=6144:raise ValueError('Fresh cohort must be complete and unique')
    return members,dict(report,experiment='v50',random_count=3072,seed_count=3072,initial_families=families+['random']*3072,past_results_loaded=False,initialization='fresh random plus templates; no archives or pool-file environment route')
