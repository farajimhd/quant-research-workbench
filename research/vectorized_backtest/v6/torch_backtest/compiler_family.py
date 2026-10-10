"""Bounded code identities for exact financial replay specializations.

Dynamo counts recompilations per Python code object. Independent exact replay
families must not exhaust one shared method's budget across a long search.
Only code metadata changes here; instructions, constants and closures do not.
"""
from collections import OrderedDict
from hashlib import sha256
from types import FunctionType, MethodType

_FAMILIES = OrderedDict()
_MAX_FAMILIES = 256


def isolated_tick(method, family):
    original = method.__func__
    key = (original, family)
    function = _FAMILIES.pop(key, None)
    if function is None:
        suffix = sha256(repr(family).encode()).hexdigest()[:24]
        name = original.__name__ + '_family_' + suffix
        code = original.__code__.replace(co_name=name, co_qualname=name)
        function = FunctionType(code, original.__globals__, name,
                                original.__defaults__, original.__closure__)
        function.__kwdefaults__ = original.__kwdefaults__
        function.__module__ = original.__module__
    _FAMILIES[key] = function
    while len(_FAMILIES) > _MAX_FAMILIES:
        _FAMILIES.popitem(last=False)
    return MethodType(function, method.__self__)
