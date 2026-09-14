"""Lossless content-addressed journal evidence and a small read projection."""
from hashlib import sha256
import json
from src.market_engine.immutable_evidence import FrozenDict, FrozenList
from .journal_storage import unpack,unpack_value

REFERENCE = '$journal_evidence_sha256'
CHART_LEVEL_FIELDS = frozenset({'unified_level_id', 'side', 'price', 'lower', 'upper',
    'entry_boundary', 'combined_entry_boundary', 'unified_break_boundary',
    'threshold_price', 'target_price'})
EVIDENCE_KEYS = frozenset({
    'unified_structural_trigger', 'profit_target_selection', 'protective_stop_selection',
    'v5_entry_selection', 'gap_selection', 'target_resistance_snapshot',
    'structural_level_snapshot', 'structural_resistance_levels', 'structural_support_levels',
    'decision_levels', 'closed_levels', 'levels', 'references',
})


FROZEN_EVIDENCE_KEYS = frozenset({'rows','prior_rows','global_levels','structural_transition_levels','immutable_json','items','parameters'})


def encode_evidence(value, dumps, evidence, memo=None):
    memo={} if memo is None else memo
    sealed=isinstance(value,(FrozenDict,FrozenList))
    if sealed and id(value) in memo:
        return memo[id(value)]
    if isinstance(value, dict):
        if set(value) == {REFERENCE}:
            return dict(value)
        result = {}
        for key, item in value.items():
            encoded = encode_evidence(item, dumps, evidence, memo)
            immutable=isinstance(item,(FrozenDict,FrozenList))
            if ((key in EVIDENCE_KEYS or immutable and key in FROZEN_EVIDENCE_KEYS) and isinstance(item, (dict, list, tuple)) and item
                    and not (isinstance(item, dict) and set(item) == {REFERENCE})):
                cached=memo.get(('json',id(item))) if immutable else None
                if cached is None:
                    raw = dumps(encoded)
                    digest = sha256(raw.encode('utf-8')).hexdigest()
                    if immutable:memo[('json',id(item))]=(digest,raw)
                else:
                    digest,raw=cached
                evidence[digest] = raw
                result[key] = {REFERENCE: digest}
            else:
                result[key] = encoded
        if sealed:memo[id(value)]=result
        return result
    if isinstance(value, (list, tuple)):
        result=[encode_evidence(item, dumps, evidence, memo) for item in value]
        if sealed:memo[id(value)]=result
        return result
    return value


def decode_evidence(value, fetch, active=None, *, immutable=False, memo=None):
    if immutable and isinstance(value, (FrozenDict, FrozenList)):
        return value
    if not isinstance(value, (dict, list)):
        return value
    active = set() if active is None else active
    memo = {} if memo is None else memo
    def decode(item, ancestors=active):
        return decode_evidence(item, fetch, ancestors, immutable=immutable, memo=memo)
    value=unpack_value(value)
    if isinstance(value, dict):
        if len(value) == 1 and REFERENCE in value:
            digest = value[REFERENCE]
            if digest in active:
                raise ValueError('Cyclic journal evidence reference')
            if immutable and digest in memo:
                return memo[digest]
            raw = fetch(digest)
            if raw is not None:raw=unpack(raw)
            if raw is None or sha256(raw.encode('utf-8')).hexdigest() != digest:
                raise ValueError(f'Missing or corrupt journal evidence: {digest}')
            result = decode(json.loads(raw), active | {digest})
            if immutable:
                memo[digest] = result
            return result
        result = {k: decode(v) if isinstance(v, (dict, list)) else v for k, v in value.items()}
        return FrozenDict(result) if immutable else result
    if isinstance(value, list):
        result = [decode(v) if isinstance(v, (dict, list)) else v for v in value]
        return FrozenList(result) if immutable else result
    return value


def activity_payload(value, fetch=None):
    """Project selected chart evidence, resolving only referenced chart fields.

    Execution externalizes evidence before journaling. Resolve that indirection
    here as well as on compact reads of existing runs, without hydrating whole
    structural books or changing canonical recovery payloads.
    """
    active = set()
    def resolved(item, project):
        if not isinstance(item, dict) or set(item) != {REFERENCE}:
            return project(item)
        if fetch is None:
            return dict(item)
        digest = item[REFERENCE]
        if digest in active:
            raise ValueError('Cyclic journal evidence reference')
        raw = fetch(digest)
        if raw is not None:raw=unpack(raw)
        if raw is None or sha256(raw.encode('utf-8')).hexdigest() != digest:
            raise ValueError(f'Missing or corrupt journal evidence: {digest}')
        active.add(digest)
        try:
            return resolved(json.loads(raw), project)
        finally:
            active.remove(digest)
    return _activity_payload(value, resolved)


def _activity_payload(value, resolved):
    """Keep decision scalars and selected evidence; hydrate full detail on demand.

    This is explicitly a presentation projection, never a recovery payload.
    Canonical source evidence is preserved separately without truncation.
    """
    if isinstance(value, dict) and set(value) == {REFERENCE}:
        return resolved(value, lambda item: _activity_payload(item, resolved))
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in ('levels', 'references', 'decision_levels', 'closed_levels',
                       'structural_resistance_levels', 'structural_support_levels'):
                # Full book collections are not chart selections. In particular,
                # do not fetch an externalized book just to discard it below.
                continue
            if key in EVIDENCE_KEYS:
                projected = resolved(item, lambda item: _chart_evidence(item, resolved))
                if projected is not None:
                    result[key] = projected
                continue
            if key == 'entry_rules' and isinstance(item, dict):
                result[key] = {phase: {k: v for k, v in stage.items()
                                      if k in ('group_scores', 'groups', 'matched_groups', 'operator', 'passed', 'score')}
                               for phase, stage in item.items() if isinstance(stage, dict)}
                continue
            if key == 'order' and isinstance(item, dict):
                result[key] = {k: v for k, v in item.items() if k != 'canonical_metadata'}
                continue
            if key in ('canonical_metadata', 'source_values', 'parameters'):
                continue
            result[key] = _activity_payload(item, resolved)
        return result
    if isinstance(value, (list, tuple)):
        return [_activity_payload(v, resolved) for v in value]
    return value


def decode_immutable_json(raw, fetch):
    """Hydrate owned checkpoint JSON bottom-up without a second mutable tree."""
    memo = {}
    return decode_evidence(json.loads(raw, object_hook=lambda value: decode_evidence(
        value, fetch, immutable=True, memo=memo)), fetch, immutable=True, memo=memo)


def _chart_evidence(item, resolved):
    if not isinstance(item, dict):
        return None
    result = {k: v for k, v in item.items()
              if v is None or isinstance(v, (str, int, float, bool))}
    def level(row):
        return {k: v for k, v in row.items() if k in CHART_LEVEL_FIELDS} if isinstance(row, dict) else {}
    def levels(rows):
        return [resolved(row, level) for row in rows] if isinstance(rows, (list, tuple)) else []
    for selected in ('selected_target_prices', 'profit_targets'):
        if selected in item:
            result[selected] = resolved(item[selected], lambda values: values)
    for selected in ('prior_snapshot_levels', 'qualified_levels', 'supports', 'resistances'):
        if selected in item:
            result[selected] = resolved(item[selected], levels)
    if 'level' in item:
        result['level'] = resolved(item['level'], level)
    def snapshot(value):
        if not isinstance(value, dict):
            return {}
        return {**{k: v for k, v in value.items()
                   if v is None or isinstance(v, (str, int, float, bool))},
                'levels': resolved(value.get('levels', []), levels)}
    if 'current_snapshot' in item:
        result['current_snapshot'] = resolved(item['current_snapshot'], snapshot)
    return result
