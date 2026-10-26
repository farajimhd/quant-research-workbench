"""Fresh phase2 founders: no local session archive or expert promotion."""
import json
import numpy as np
from .initial_population import initialize
from .local_runner import CompactPedigree

def global_seed(exports,job,probe):
    if exports:raise ValueError('V53 cannot seed from prior session experts')
    rng=np.random.default_rng(job['seed']);members,report=initialize(rng,job['population']*job['initial_multiplier'],probe=probe)
    # Coverage histograms have integer keys; freeze their JSON representation
    # before hashing so decoding cannot change numeric versus lexical key order.
    report=json.loads(json.dumps(report,allow_nan=False))
    pedigree=CompactPedigree(3);ids=pedigree.birth(members,1,[(-1,-1)]*len(members),['fresh']*len(members))
    if len(members)!=job['population']*job['initial_multiplier'] or any(m.case_owner is not None for m in members):raise ValueError('Fresh unrestricted initial cohort required')
    from research.vectorized_backtest.v42.family_stats import seed
    seed(pedigree,ids,report['initial_families'])
    return dict(population=[m.payload() for m in members],ids=ids.tolist(),pedigree=list(pedigree.records.values()),next_serial=pedigree.next_serial,rng=rng.bit_generator.state,initialization=report,expert_count=0,fresh_count=len(members),validation_opened=False,source_sha256=job['source_sha256'])
