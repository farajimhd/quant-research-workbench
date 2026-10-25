"""Restore the exact sealed V42 initial cohort and its post-initialization RNG."""
import json
import os
import hashlib
from pathlib import Path
from .identity import digest
from .genome import Individual


def initialize(rng, size, *, probe=None):
    if 'V49_INITIAL_POOL' not in os.environ:
        from research.vectorized_backtest.v42.initial_population import initialize as original
        members,report=original(rng,size,probe=probe)
        return [Individual.restore(m.payload()) for m in members],dict(report,experiment='v49',fresh_calibrated_initialization=True)
    path = Path(os.environ['V49_INITIAL_POOL'])
    if hashlib.sha256(path.read_bytes()).hexdigest() != os.environ['V49_INITIAL_POOL_SHA256']:
        raise ValueError('Pinned initial population file changed')
    saved = json.loads(path.read_bytes())
    if (size != 6144 or len(saved['population']) != size
            or saved['probe_sha256'] != probe['sha256']
            or saved['rng_before'] != rng.bit_generator.state
            or digest(saved['population']) != saved['population_sha256']):
        raise ValueError('Fair initial cohort identity changed')
    members = [Individual.restore(p) for p in saved['population']]
    rng.bit_generator.state = saved['rng_after']
    return members, dict(saved['report'], prepared_initialization_reused=True,
                         experiment='v49', original_v42_population_sha256=saved['population_sha256'])
