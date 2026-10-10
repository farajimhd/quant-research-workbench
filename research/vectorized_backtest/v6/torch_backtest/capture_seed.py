"""Persist the exact constructor population used by a financial capture family."""
import json
from hashlib import sha256
from .runtime import require_runtime,write_json
from .run_search import state,restore
from .training_pass import population_hash


def family_token(key):
    return sha256(json.dumps(key,separators=(',',':')).encode()).hexdigest()


def seed_population(root,key,members):
    """Never replace a family's initializer when candidate values are updated."""
    root=require_runtime(root)
    path=root/(family_token(key)+'.json')
    normalized=json.loads(json.dumps(key))
    if path.exists():
        record=json.loads(path.read_text())
        if (record.get('version')!='exact-capture-initializer-v1' or record.get('key')!=normalized
                or record.get('validation_opened') is not False):
            raise ValueError('Capture initializer identity changed')
        result=[restore(v) for v in record['population']]
        if len(result)!=len(members) or population_hash(result)!=record['population_sha256']:
            raise ValueError('Capture initializer population changed')
        return result
    payload=[state(v) for v in members]
    result=[restore(v) for v in payload]
    write_json(path,dict(version='exact-capture-initializer-v1',key=normalized,population=payload,
        population_sha256=population_hash(result),validation_opened=False))
    return result
