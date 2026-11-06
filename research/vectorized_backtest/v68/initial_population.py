"""Fresh minimal founders; never reuse archived genomes or financial results."""
import numpy as np


def validate_fresh(member):
    """Constructor boundary only: evolved networks retain their full capacity."""
    from .authority import construct
    member=construct(member)
    if any(not 1 <= len(net.nodes) <= 2 for net in member.networks.values()):
        raise ValueError('Fresh V67 action networks must have 1-2 nodes')
    return member


def random_immigrant(rng,probe,*,selective=True):
    """Same minimal construction and training-only calibration as founders."""
    if probe is None:raise ValueError('Fresh V67 offspring require the sealed training probe')
    from .genome import sample
    from .calibration import policy_families,calibrate
    member=sample(rng,1)[0]
    members,_=policy_families(rng,[member,member])
    members=[members[int(rng.integers(2))]]
    members,_=calibrate(rng,members,probe,selective=selective)
    return validate_fresh(members[0])


def initialize_random_baseline(rng,size,*,probe=None):
    """All 1024 founders begin with 1-2 output-reachable nodes per action."""
    if size!=1024 or probe is None:raise ValueError('1024 fresh founders and training probe required')
    from .genome import sample
    from .calibration import calibrate
    from .initial_design import policies
    from .population_builder import build
    members,calibration=calibrate(rng,policies(rng,sample(rng,size,stratified=True,structural=True)),probe)
    members,construction=build(size,lambda stream,slot,attempt:validate_fresh(members[slot]),
        lambda stream,slot,attempt:random_immigrant(stream,probe,selective=False),seed=int(rng.integers(2**32)))
    counts=[sum(len(n.nodes) for n in m.networks.values()) for m in members]
    return members,dict(experiment='v67',construction=construction,calibration=calibration,
        random_count=size,seed_count=0,past_results_loaded=False,initial_families=['simple']*size,
        founder_nodes=dict(min=min(counts),median=float(np.median(counts)),max=max(counts)),
        initialization='fresh minimal 1-2 nodes per action; no templates, archived genomes or financial selection')


def initialize(rng,size,*,probe=None):
    from .fresh_design import initialize as mixed
    return mixed(rng,size,probe)


def immigrant(rng,probe):
    from .fresh_design import immigrant as mixed
    return mixed(rng,probe)
