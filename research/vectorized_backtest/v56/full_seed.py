"""Restore the authorized, unselected V53 initial pool and exact saved RNG."""
from .initial_reuse import verify,read
from .identity import digest
from .genome import Individual
def global_seed(exports,job,probe):
    if exports:raise ValueError('No trained winner import route')
    bound=verify(job);state=read(bound['initial_pool'])
    if digest(state['population'])!=bound['population_sha256'] or digest(state['rng'])!=bound['rng_sha256']:raise ValueError('Founder population or RNG changed')
    members=[Individual.restore(p) for p in state['population']]
    if digest([m.payload() for m in members])!=bound['population_sha256']:raise ValueError('Founder schema or parameters changed')
    if len(members)!=6144 or state.get('completed',0)!=0:raise ValueError('Only pre-selection founders are authorized')
    state['source_sha256']=job['source_sha256']
    state['initialization']=dict(state['initialization'],v56_authorized_founder_reuse=True,source_initial_pool_sha256=bound['initial_pool_sha256'],founder_market_replays=0)
    return state
