"""Immutable JSON evidence that can be shared across causal publications.

Use freeze(), not the container constructors. Mutable computation must explicitly
construct a new dict/list; copying immutable evidence can retain its identity.
"""


def _immutable(*args, **kwargs):
    raise TypeError('Published evidence is immutable; construct a new value')


class FrozenDict(dict):
    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _immutable

    def __deepcopy__(self, memo):
        return self

    def __reduce__(self):
        return freeze, (dict(self),)

    def derived(self, key, build):
        """Private disposable projections; never part of JSON/checkpoint state."""
        cache=getattr(self,'_derived',None)
        if cache is None:
            cache=self._derived={}
        if key not in cache:cache[key]=freeze(build())
        return cache[key]


class FrozenList(list):
    __setitem__ = __delitem__ = append = extend = insert = pop = remove = clear = sort = reverse = __iadd__ = __imul__ = _immutable

    def __deepcopy__(self, memo):
        return self

    def __reduce__(self):
        return freeze, (list(self),)


def freeze(value):
    if isinstance(value, (FrozenDict, FrozenList)):
        return value
    if isinstance(value, dict):
        return FrozenDict({key: freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return FrozenList(freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(freeze(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f'Unsupported evidence type: {type(value).__name__}')


def evidence_payload(value):
    """dataclasses.asdict semantics, retaining sealed evidence subtrees."""
    from copy import deepcopy
    from dataclasses import fields, is_dataclass
    from collections import defaultdict
    if isinstance(value,(FrozenDict,FrozenList)):
        return value
    if is_dataclass(value) and not isinstance(value,type):
        return {field.name:evidence_payload(getattr(value,field.name)) for field in fields(value)}
    if isinstance(value,tuple) and hasattr(value,'_fields'):
        return type(value)(*(evidence_payload(item) for item in value))
    if isinstance(value,(list,tuple)):
        return type(value)(evidence_payload(item) for item in value)
    if isinstance(value,dict):
        items=((evidence_payload(key),evidence_payload(item)) for key,item in value.items())
        return defaultdict(value.default_factory,items) if isinstance(value,defaultdict) else type(value)(items)
    return deepcopy(value)
