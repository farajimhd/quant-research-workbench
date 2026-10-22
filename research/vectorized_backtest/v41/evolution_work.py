"""Isolate an ordered reproduction transaction from the CUDA owner's GIL."""
import os


def _reproduce(state, population, ids, pedigree, rank, generation, config, kwargs):
    import numpy as np
    from .lineage import next_population
    from .semantics import REJECTIONS
    # This job already occupies the bounded CPU pool. Do not create nested
    # descriptor pools, or parallelize random draws / offspring acceptance.
    previous = os.environ.get('V41_REPRODUCTION_WORKERS')
    os.environ['V41_REPRODUCTION_WORKERS'] = '1'
    before = REJECTIONS.copy()
    rng = np.random.default_rng()
    rng.bit_generator.state = state
    try:
        from .validation_cache import validation_scope
        with validation_scope():
            members, new_ids, report = next_population(
                rng, population, ids, pedigree, rank, generation, config, **kwargs)
        return members, new_ids, report, pedigree, kwargs.get('species'), rng.bit_generator.state, REJECTIONS - before
    finally:
        if previous is None:
            os.environ.pop('V41_REPRODUCTION_WORKERS', None)
        else:
            os.environ['V41_REPRODUCTION_WORKERS'] = previous


def submit_population(rng, population, ids, pedigree, rank, generation, config, *, submit_job=None, **kwargs):
    """Submit training-only inputs; the caller must not mutate them before join."""
    from .preparation import submit
    return (submit_job or submit)(_reproduce, rng.bit_generator.state, population, ids, pedigree,
                                 rank, generation, config, kwargs)


def commit_population(result,rng,pedigree,species_target=None):
    from .semantics import REJECTIONS
    members, new_ids, report, updated, species, state, rejections = result
    # Publish state only after successful completion; failure cannot advance RNG.
    pedigree.__dict__.update(updated.__dict__)
    if species_target is not None:
        species_target.__dict__.update(species.__dict__)
    rng.bit_generator.state = state
    REJECTIONS.update(rejections)
    return members, new_ids, report


def next_population(rng, population, ids, pedigree, rank, generation, config, **kwargs):
    future=submit_population(rng,population,ids,pedigree,rank,generation,config,**kwargs)
    return commit_population(future.result(),rng,pedigree,kwargs.get('species'))
