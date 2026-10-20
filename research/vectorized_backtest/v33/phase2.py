"""V33 uses the unchanged optimized global engine with sealed initial seeds."""
from .initial_pool import verify as verify_pool
from .run_search import run
from .lineage import Selection
from pathlib import Path
import json

def attach_source_ancestry(pedigree,sources):
    if any(s is not None for s in sources):raise ValueError('Initial-only seeds cannot import evolved ancestry')

def run_global(training,validation,initial_pool,output,**kwargs):
    pool=json.loads(Path(initial_pool).read_bytes())
    return run(training,validation,output,initial_population=pool,selection=Selection(),maximum_gate_gib=8.,
               replay_width_workers=1 if kwargs.get('shared_replay') else None,**kwargs)
