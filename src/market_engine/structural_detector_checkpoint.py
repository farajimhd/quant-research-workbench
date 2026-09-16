"""Versioned JSON checkpoints for the shared detector; no executable payloads."""
from collections import Counter, deque
from dataclasses import asdict, is_dataclass
from math import isfinite

from .structural_detector import StructuralDetector, DetectorSettings, VERSION
from .structural_evidence import Interactions
from .structural_progression import Progression
from .structural_volume import VolumeLevels
from .swing_structure import SwingStructure, SwingSettings
from .swing_pivot_witness import PivotWitnessStructure
from .immutable_evidence import FrozenDict, FrozenList, freeze

TYPES = {c.__name__: c for c in (StructuralDetector, DetectorSettings, Interactions,
    Progression, VolumeLevels, SwingStructure, SwingSettings, PivotWitnessStructure)}


def encode(value, *, compact=False):
    if compact and isinstance(value,(FrozenDict,FrozenList)):
        return {'type':'json_evidence','immutable_json':value}
    child=lambda item:encode(item,compact=compact)
    if isinstance(value, Counter):
        return {'type': 'counter', 'items': child(dict(value))}
    if isinstance(value, dict):
        return {'type': 'dict', 'items': [[child(k), child(v)] for k,v in value.items()]}
    if isinstance(value, deque):
        return {'type': 'deque', 'maxlen': value.maxlen, 'items': [child(v) for v in value]}
    if isinstance(value, (tuple, set)):
        return {'type': type(value).__name__, 'items': [child(v) for v in value]}
    if isinstance(value, list):
        return [child(v) for v in value]
    if type(value).__name__ in TYPES:
        return {'type': type(value).__name__, 'state': child(asdict(value) if is_dataclass(value) else vars(value))}
    if isinstance(value, float) and not isfinite(value):
        if value == float('-inf'):
            return {'type': 'negative_infinity'}
        raise ValueError('Invalid detector checkpoint number')
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError('Unsupported detector checkpoint value')


def decode(value):
    if isinstance(value, list):
        return [decode(v) for v in value]
    if not isinstance(value, dict):
        return value
    kind = value['type']
    if kind=='json_evidence':
        return freeze(value['immutable_json'])
    if kind == 'dict':
        return {decode(k): decode(v) for k,v in value['items']}
    if kind == 'counter':
        return Counter(decode(value['items']))
    if kind == 'deque':
        return deque((decode(v) for v in value['items']), maxlen=value['maxlen'])
    if kind in ('tuple', 'set'):
        return (tuple if kind == 'tuple' else set)(decode(v) for v in value['items'])
    if kind == 'negative_infinity':
        return float('-inf')
    cls = TYPES[kind]
    state = decode(value['state'])
    if cls in (DetectorSettings, SwingSettings):
        return cls(**state)
    instance = cls.__new__(cls)
    instance.__dict__.update(state)
    return instance


JSON_TYPE='$structural_checkpoint_type'


def encode_json(value):
    """Native JSON containers with explicit tags only for non-JSON types."""
    if isinstance(value,(FrozenDict,FrozenList)):
        return {JSON_TYPE:'immutable','items':value}
    if isinstance(value,Counter):
        return {JSON_TYPE:'counter','items':encode_json(dict(value))}
    if isinstance(value,dict):
        if JSON_TYPE not in value and all(isinstance(k,str) for k in value):
            return {k:encode_json(v) for k,v in value.items()}
        return {JSON_TYPE:'dict_pairs','items':[[encode_json(k),encode_json(v)] for k,v in value.items()]}
    if isinstance(value,deque):
        return {JSON_TYPE:'deque','maxlen':value.maxlen,'items':[encode_json(v) for v in value]}
    if isinstance(value,(tuple,set)):
        return {JSON_TYPE:type(value).__name__,'items':[encode_json(v) for v in value]}
    if isinstance(value,list):return [encode_json(v) for v in value]
    if type(value).__name__ in TYPES:
        return {JSON_TYPE:type(value).__name__,'items':encode_json(asdict(value) if is_dataclass(value) else vars(value))}
    if isinstance(value,float) and not isfinite(value):
        if value==float('-inf'):return {JSON_TYPE:'negative_infinity'}
        raise ValueError('Invalid detector checkpoint number')
    if value is None or isinstance(value,(str,int,float,bool)):return value
    raise ValueError('Unsupported detector checkpoint value')


def decode_json(value):
    if isinstance(value,list):return [decode_json(v) for v in value]
    if not isinstance(value,dict):return value
    if JSON_TYPE not in value:return {k:decode_json(v) for k,v in value.items()}
    kind=value[JSON_TYPE]
    if kind=='immutable':return freeze(value['items'])
    if kind=='dict_pairs':return {decode_json(k):decode_json(v) for k,v in value['items']}
    if kind=='negative_infinity':return float('-inf')
    if kind=='counter':return Counter(decode_json(value['items']))
    if kind=='deque':return deque((decode_json(v) for v in value['items']),maxlen=value['maxlen'])
    if kind in ('tuple','set'):return (tuple if kind=='tuple' else set)(decode_json(v) for v in value['items'])
    cls=TYPES[kind];state=decode_json(value['items'])
    if cls in (DetectorSettings,SwingSettings):return cls(**state)
    instance=cls.__new__(cls);instance.__dict__.update(state);return instance


def checkpoint(detector, *, compact=False):
    result={'contract': VERSION, 'state': encode_json(detector) if compact else encode(detector)}
    if compact:result['format_version']=3
    return result


def restore(value):
    if value.get('contract') != VERSION:
        raise ValueError('Detector checkpoint version mismatch')
    if value.get('format_version',1) not in (1,2,3):
        raise ValueError('Detector checkpoint format mismatch')
    result = decode_json(value['state']) if value.get('format_version')==3 else decode(value['state'])
    if type(result) is not StructuralDetector:
        raise ValueError('Invalid detector checkpoint root')
    return result
