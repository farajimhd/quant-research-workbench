"""Validate variable genomes; scope only the recomputable dependency index."""
from contextlib import contextmanager
@contextmanager
def validation_scope():
    from .whole_network import cache_scope
    with cache_scope():yield
