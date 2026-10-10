"""Bounded compiler specialization lifetime for exact financial replay.

Dynamo counts recompilations per Python code object. Independent exact replay
families must not exhaust one shared method's budget across a long search.
Keep original code identity: changing metadata can change generated reductions.
Captured CUDA graphs retain their kernels independently of Dynamo's guards.
"""
from collections import OrderedDict
import torch

_ACTIVE_FAMILIES = OrderedDict()
_MAX_METHODS = 16


def isolated_tick(method, family):
    """Call only at the serial capture barrier, before compiling a new runner."""
    code = method.__func__.__code__
    previous = _ACTIVE_FAMILIES.pop(code, None)
    if previous != family:
        torch._dynamo.reset_code(code)
    _ACTIVE_FAMILIES[code] = family
    while len(_ACTIVE_FAMILIES) > _MAX_METHODS:
        _ACTIVE_FAMILIES.popitem(last=False)
    return method
