"""Audited controller-only policy overlay for an already running local campaign.

The caller verifies file hashes before loading. Existing optimizer deployments,
source identities, worker code, and local checkpoint contracts stay immutable.
"""
import importlib.util
from pathlib import Path
import sys


def install(folder):
    from . import run_search as original
    package=__package__
    def load(name,filename):
        spec=importlib.util.spec_from_file_location(package+'.'+name,Path(folder)/filename)
        module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module
        spec.loader.exec_module(module);return module
    screen=load('phase2_screen','phase2_screen.py')
    runner=load('_screen_run_search','run_search.py')
    # The optimizer remains original; separate policy file hashes bind the new
    # initialization/reuse contract instead of relabeling old local evidence.
    runner.source_hash=original.source_hash
    phase2=load('phase2','phase2.py');phase2.run=runner.run
    return phase2,screen
