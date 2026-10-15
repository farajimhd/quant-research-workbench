"""Versioned optimizer-to-shared-program adapter, without market or broker I/O.

Frozen source evidence is an audit reference, never an app market-data source.
The native release/configuration publishers remain the publication authority.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite
from types import MappingProxyType
from collections.abc import Mapping

from src.market_engine.qmd_feature_catalog import CATALOG
from src.market_engine.volume_rank_rules import VolumeUniverseRule
from .columnar_rule_program import InputField, Operation, RuleNode, RuleProgram
from .optimized_program_specification import OptimizedProgramSpecification, STAGES


def content_hash(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                             allow_nan=False).encode()).hexdigest()


def _hash(value):
    if type(value) is not str or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('Candidate requires SHA256 source identities')
    return value


@dataclass(frozen=True)
class OptimizedCandidate:
    version: str
    generation: int
    serial: int
    candidate: int
    program: Mapping
    execution: Mapping
    universe: Mapping
    program_hash: str
    evidence_hash: str


def restore_candidate(value):
    """Validate a detached, hash-bound V19 candidate without importing research."""
    required = {'version', 'generation', 'serial', 'candidate', 'program', 'execution',
                'universe', 'program_hash', 'evidence_hash'}
    if not isinstance(value, Mapping) or set(value) != required or value['version'] != 'v19':
        raise ValueError('Unsupported optimized candidate envelope')
    if any(type(value[k]) is not int or value[k] < 1 for k in ('generation', 'serial', 'candidate')):
        raise ValueError('Candidate requires positive immutable source indices')
    program = json.loads(json.dumps(dict(value['program']), allow_nan=False))
    if content_hash(program) != _hash(value['program_hash']):
        raise ValueError('Candidate program hash differs')
    _hash(value['evidence_hash'])
    execution = dict(value['execution'])
    if set(execution) != {'entry_dollars', 'add_dollars', 'cost_bps', 'fee_per_share',
            'minimum_order_fee', 'risk_protection', 'maximum_stop_distance', 'maximum_position_risk'}:
        raise ValueError('V19 execution fields differ')
    if execution['risk_protection'] is not True:
        raise ValueError('Unprotected V19 candidate is not supported')
    for key, number in execution.items():
        if key != 'risk_protection' and (type(number) not in (int, float)
                or not isfinite(number) or number < 0):
            raise ValueError('Invalid V19 execution value')
    if (min(execution['entry_dollars'], execution['add_dollars']) <= 0
            or not 0 < execution['maximum_stop_distance'] <= .2
            or not 0 < execution['maximum_position_risk'] <= 300):
        raise ValueError('Candidate execution exceeds its source envelope')
    universe = dict(value['universe'])
    if universe != dict(version='v6-volume-market-blocks-v1', window=30,
                        top_n=10, minimum_close=.8, maximum_close=50.):
        raise ValueError('V19 volume top10 source contract differs')
    return OptimizedCandidate(value['version'], value['generation'], value['serial'],
        value['candidate'], MappingProxyType(program), MappingProxyType(execution),
        MappingProxyType(universe), value['program_hash'], value['evidence_hash'])


def required_candidate_fields(candidate):
    """Resolve IDs through the shared catalog, including the actual swing gene."""
    rules = candidate.program
    if set(rules) != {'rules', 'policy', 'open_rules', 'minimum_age'}:
        raise ValueError('Candidate lifecycle contract differs')
    if set(rules['rules']) != set(STAGES) or (rules['open_rules'] is not None
            and set(rules['open_rules']) != set(STAGES)):
        raise ValueError('Candidate requires complete flat/open rule sets')
    names = set()
    for family in (rules['rules'], rules['open_rules'] or {}):
        for raw in family.values():
            if set(raw) != {'nodes', 'output'} or not 1 <= len(raw['nodes']) <= 32:
                raise ValueError('Invalid bounded source DAG')
            for node in raw['nodes']:
                if set(node) != {'op', 'a', 'b', 'feature', 'window', 'value', 'unit'}:
                    raise ValueError('Source instruction fields differ')
                if type(node['op']) is not int:
                    raise ValueError('Source operation must be an integer')
                Operation(node['op'])
                if node['op'] == Operation.FEATURE:
                    index = node['feature']
                    if type(index) is not int or not 0 <= index < len(CATALOG):
                        raise ValueError('Unknown source feature ID')
                    names.add(CATALOG[index].name)
    policy = rules['policy']
    left, right = policy.get('swing_left'), policy.get('swing_right')
    if left or right:
        names.add(f'history.swing_low.{left}.{right}')
    return frozenset(names)


def compile_candidate(candidate, fields):
    """Translate market DAGs; actual filled position state stays in the app actor.

    The separately frozen execution envelope MUST pass native admission checks
    before launch. A compiled release alone is not execution-mode qualification.
    """
    if type(candidate) is not OptimizedCandidate:
        raise TypeError('Compilation requires a verified candidate')
    # Revalidate detached content: a frozen dataclass does not freeze nested DAGs.
    candidate = restore_candidate({k: getattr(candidate, k) for k in candidate.__dataclass_fields__})
    required = required_candidate_fields(candidate)
    if not required <= set(fields):
        raise ValueError('Candidate lacks its certified QMD input fields')
    for name in required:
        field = fields[name]
        source = next((f for f in CATALOG if f.name == name), None)
        if (type(field) is not InputField or source is None or field.unit != source.unit
                or field.resolution_ms != 1000 or field.product != 'q_live.qmd_causal_history_value_v1'):
            raise ValueError('Candidate input differs from the shared feature catalog')
    programs = {}
    for family in (candidate.program['rules'], candidate.program['open_rules'] or {}):
        for stage, raw in family.items():
            nodes = tuple(RuleNode(Operation(n['op']), n['a'], n['b'],
                CATALOG[n['feature']].name if n['op'] == Operation.FEATURE else '',
                n['window'], n['value'], n['unit']) for n in raw['nodes'])
            program = RuleProgram(nodes, raw['output'])
            program.validate(fields)
            if family is candidate.program['rules'] or stage != 'entry':
                programs[stage] = program
    ages = dict(candidate.program['minimum_age'])
    if any(k not in STAGES or type(v) is not int or not 0 <= v < 2**32 for k, v in ages.items()):
        raise ValueError('Invalid source minimum position age')
    # Entry has no held position: V19 ignores this gene there.
    ages['entry'] = 0
    policy = dict(candidate.program['policy'])
    from .columnar_strategy_release import POLICY
    if set(policy) != (set(POLICY) - {'cooldown_seconds'}) | {'cooldown'}:
        raise ValueError('Candidate policy fields differ')
    policy['cooldown_seconds'] = policy.pop('cooldown')
    execution = candidate.execution
    semantic = content_hash(dict(version=candidate.version, program=dict(candidate.program),
        execution=dict(execution), universe=dict(candidate.universe)))
    return OptimizedProgramSpecification(candidate.program_hash, candidate.generation,
        candidate.candidate, MappingProxyType(programs), MappingProxyType(ages),
        MappingProxyType(policy), float(execution['entry_dollars']), float(execution['add_dollars']),
        float(execution['fee_per_share']), float(execution['minimum_order_fee']),
        VolumeUniverseRule(30, 10, .8, 50.), semantic)
