"""Lossless content-addressed journal evidence and a small read projection."""
from hashlib import sha256
import json

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


def encode_evidence(value, dumps, evidence):
    if isinstance(value, dict):
        if set(value) == {REFERENCE}:
            return dict(value)
        result = {}
        for key, item in value.items():
            encoded = encode_evidence(item, dumps, evidence)
            if (key in EVIDENCE_KEYS and isinstance(item, (dict, list, tuple)) and item
                    and not (isinstance(item, dict) and set(item) == {REFERENCE})):
                raw = dumps(encoded)
                digest = sha256(raw.encode('utf-8')).hexdigest()
                evidence[digest] = raw
                result[key] = {REFERENCE: digest}
            else:
                result[key] = encoded
        return result
    if isinstance(value, (list, tuple)):
        return [encode_evidence(item, dumps, evidence) for item in value]
    return value


def decode_evidence(value, fetch, active=None):
    active = set() if active is None else active
    if isinstance(value, dict):
        if set(value) == {REFERENCE}:
            digest = value[REFERENCE]
            if digest in active:
                raise ValueError('Cyclic journal evidence reference')
            raw = fetch(digest)
            if raw is None or sha256(raw.encode('utf-8')).hexdigest() != digest:
                raise ValueError(f'Missing or corrupt journal evidence: {digest}')
            return decode_evidence(json.loads(raw), fetch, active | {digest})
        return {k: decode_evidence(v, fetch, active) for k, v in value.items()}
    if isinstance(value, list):
        return [decode_evidence(v, fetch, active) for v in value]
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
