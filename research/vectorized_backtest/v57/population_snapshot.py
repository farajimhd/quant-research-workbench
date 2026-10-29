"""One canonical encoding per immutable cohort; keep exact legacy JSON hashes."""
import json
from hashlib import sha256
from dataclasses import asdict
from .validation_cache import immutable


def dumps(value):
    return json.dumps(value,sort_keys=True,separators=(',', ':'),allow_nan=False)


def envelope(values,**fragments):
    """Join already canonical subtrees without traversing their graphs again."""
    return '{'+','.join(dumps(k)+':'+(fragments[k] if k in fragments else dumps(values[k]))
                        for k in sorted(set(values)|set(fragments)))+'}'


def signature(members):
    return tuple((tuple((k,id(v)) for k,v in sorted(m.networks.items())),dumps(asdict(m.policy)),
                  tuple(sorted(m.minimum_age.items())),m.case_owner) for m in members)


class Snapshot:
    def __init__(self,members):
        self.members=tuple(members)  # Strong references prevent identity reuse.
        if not all(immutable(n) for m in members for n in m.networks.values()):
            raise ValueError('Snapshot requires immutable, typed network graphs')
        self.signature=signature(members)
        self.payload=[m.payload() for m in members]
        # Do not retain an alias to mutable age dictionaries.
        for p in self.payload:p['minimum_age']=dict(p['minimum_age'])
        self.raw=dumps(self.payload);self.token=sha256(self.raw.encode()).hexdigest()

    def verify(self,members):
        if len(members)!=len(self.members) or any(a is not b for a,b in zip(members,self.members)) or signature(members)!=self.signature:
            raise ValueError('Frozen cohort or metadata changed')
        return self.token

    def successor(self,ids,rng):
        raw=envelope(dict(ids=ids.tolist(),rng=rng),population=self.raw)
        return raw,sha256(raw.encode()).hexdigest()
