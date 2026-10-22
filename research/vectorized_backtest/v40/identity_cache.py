"""Scoped canonical JSON fragments for structurally immutable networks only."""
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
from dataclasses import asdict
import json

_active=ContextVar('v40_identity_fragments',default=None)
CAPACITY=4096


@contextmanager
def identity_scope():
    token=_active.set(OrderedDict())
    try:yield
    finally:_active.reset(token)


def program_key(member):
    cache=_active.get()
    if cache is None:return None
    from .genome import Individual,Network
    from .validation_cache import immutable,known_immutable
    if type(member) is not Individual or type(member.networks) is not dict:
        return None
    if any(type(k) is not str or type(n) is not Network for k,n in member.networks.items()):
        return None
    def dumps(value):return json.dumps(value,sort_keys=True,separators=(',',':'))
    fragments=[]
    for stage,net in sorted(member.networks.items()):
        saved=cache.get(id(net))
        if saved is not None and saved[0] is net:
            fragment=saved[1]
        else:
            value=net.payload()
            for node in value['nodes']:node.pop('uid',None)
            fragment=dumps(value)
            if known_immutable(net) or immutable(net):
                cache[id(net)]=(net,fragment)
                if len(cache)>CAPACITY:cache.popitem(last=False)
        fragments.append(dumps(stage)+':'+fragment)
    # Mutable policy/age mappings are serialized afresh on every call. Match
    # the exact legacy sorted-key JSON, including its floating-point spelling.
    value='{"minimum_age":'+dumps(member.minimum_age)+',"networks":{'+','.join(fragments)+'},"policy":'+dumps(asdict(member.policy))+',"version":"v40-recurrent-v1"}'
    return sha256(value.encode()).hexdigest()
