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
    order_path=root/'order.json'
    order=json.loads(order_path.read_text()) if order_path.exists() else dict(version='compiler-priming-order-v2',families=[],initializers={},validation_opened=False)
    families=order.get('families')
    if (order.get('version')!='compiler-priming-order-v2' or order.get('validation_opened') is not False
            or not isinstance(families,list) or any(type(v) is not str for v in families)
            or len(set(families))!=len(families) or set(order.get('initializers',{}))!=set(families)):
        raise ValueError('Compiler priming order changed')
    token=family_token(key)
    if token in order['initializers']:
        record=order['initializers'][token]
        if (record.get('version')!='exact-capture-initializer-v1' or record.get('key')!=normalized
                or record.get('validation_opened') is not False):
            raise ValueError('Capture initializer identity changed')
        result=[restore(v) for v in record['population']]
        if len(result)!=len(members) or population_hash(result)!=record['population_sha256']:
            raise ValueError('Capture initializer population changed')
        if path.exists() and json.loads(path.read_text())!=record:
            raise ValueError('Capture initializer differs from its authoritative order')
        if not path.exists():write_json(path,record)
        return result
    if path.exists():raise ValueError('Orphaned capture initializer has no authoritative order')
    payload=[state(v) for v in members]
    result=[restore(v) for v in payload]
    record=dict(version='exact-capture-initializer-v1',key=normalized,population=payload,
        population_sha256=population_hash(result),validation_opened=False)
    order['families'].append(token)
    order['initializers'][token]=record
    # Publish the complete authority atomically first. A crash before the
    # derived per-family file is written can reconstruct it exactly on resume.
    write_json(order_path,order)
    write_json(path,record)
    return result
