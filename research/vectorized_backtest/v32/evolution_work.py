"""Isolate an ordered reproduction transaction from the CUDA owner's GIL."""
import os


def _reproduce(state, population, ids, pedigree, rank, generation, config, kwargs):
    import numpy as np
    from .lineage import next_population
    from .semantics import REJECTIONS
    # This job already occupies the bounded CPU pool. Do not create nested
    # descriptor pools, or parallelize random draws / offspring acceptance.
    previous = os.environ.get('V32_REPRODUCTION_WORKERS')
    os.environ['V32_REPRODUCTION_WORKERS'] = '1'
    before = REJECTIONS.copy()
    rng = np.random.default_rng()
    rng.bit_generator.state = state
    try:
        members, new_ids, report = next_population(
            rng, population, ids, pedigree, rank, generation, config, **kwargs)
        return members, new_ids, report, pedigree, kwargs.get('species'), rng.bit_generator.state, REJECTIONS - before
    finally:
        if previous is None:
            os.environ.pop('V32_REPRODUCTION_WORKERS', None)
        else:
            os.environ['V32_REPRODUCTION_WORKERS'] = previous


def next_population(rng, population, ids, pedigree, rank, generation, config, **kwargs):
    from .preparation import submit
    from .semantics import REJECTIONS
    result = submit(_reproduce, rng.bit_generator.state, population, ids, pedigree,
                    rank, generation, config, kwargs).result()
    members, new_ids, report, updated, species, state, rejections = result
    # Publish state only after successful completion; failure cannot advance RNG.
    pedigree.__dict__.update(updated.__dict__)
    if kwargs.get('species') is not None:
        kwargs['species'].__dict__.update(species.__dict__)
    rng.bit_generator.state = state
    REJECTIONS.update(rejections)
    return members, new_ids, report
