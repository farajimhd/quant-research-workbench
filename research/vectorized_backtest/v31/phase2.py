"""Phase-2 engineering entry point; V31 starts fresh and has no local phase."""
from .run_search import run

def run_global(training,validation,output,**kwargs):
    return run(training,validation,output,**kwargs)
