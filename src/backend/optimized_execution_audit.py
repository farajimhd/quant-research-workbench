"""Bounded, read-only reconciliation of already prepared execution evidence.

This module acquires no data and infers no broker causality. Callers supply
stable keys, typed fields, completeness receipts and explicit state identities.
"""
from dataclasses import dataclass, field, replace
from math import isfinite
from types import MappingProxyType
from typing import Mapping

import polars as pl

STAGES = ('inputs', 'membership', 'rule_outputs', 'decisions', 'orders',
          'fills', 'positions', 'fees_pnl')
# Raw market-only program outputs remain comparable after broker state diverges.
# Only sequential branch/age/policy decisions require equivalent position/OMS state.
STATE_DEPENDENT = frozenset(('decisions',))


@dataclass(frozen=True)
class AuditTableSpec:
    keys: tuple[str, ...]
    timestamp: str
    fields: tuple[str, ...]
    # Absolute tolerance only; omitted fields use exact typed equality.
    tolerances: Mapping[str, float] = field(default_factory=dict)
    state_identity: str | None = None

    def __post_init__(self):
        if (not self.keys or not self.fields or len(set(self.keys)) != len(self.keys)
                or len(set(self.fields)) != len(self.fields)
                or set(self.keys) & set(self.fields)
                or self.timestamp in self.keys
                or any(name.startswith(('l_', 'r_', '_')) for name in self.keys)):
            raise ValueError('Audit requires unique disjoint keys and fields')
        tolerances = dict(self.tolerances)
        if any(name not in self.fields or not isfinite(value) or value < 0
               for name, value in tolerances.items()):
            raise ValueError('Tolerances must be explicit finite nonnegative field values')
        object.__setattr__(self, 'tolerances', MappingProxyType(tolerances))


@dataclass(frozen=True)
class AuditEvidence:
    frame: pl.DataFrame
    expected_rows: int
    complete: bool


@dataclass(frozen=True)
class AuditStageResult:
    stage: str
    status: str
    reference_rows: int
    actual_rows: int
    missing_rows: int
    extra_rows: int
    mismatched_rows: int
    not_comparable_rows: int
    first_divergence_at: object | None
    samples: tuple[dict, ...]
    upstream_divergence: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecutionAuditResult:
    stages: tuple[AuditStageResult, ...]
    first_divergence: AuditStageResult | None
    equal: bool


def _validate(evidence, spec, max_rows, label):
    frame = evidence.frame
    if (evidence.complete is not True or isinstance(evidence.expected_rows, bool)
            or evidence.expected_rows < 0 or frame.height != evidence.expected_rows):
        raise ValueError(f'{label}: incomplete evidence or row count mismatch')
    if frame.height > max_rows:
        raise ValueError(f'{label}: audit row bound exceeded')
    columns = set(spec.keys) | set(spec.fields) | {spec.timestamp}
    if spec.state_identity:
        columns.add(spec.state_identity)
    if not columns <= set(frame.columns):
        raise ValueError(f'{label}: missing required columns')
    dtype = frame.schema[spec.timestamp]
    if not isinstance(dtype, pl.Datetime) or dtype.time_zone is None:
        raise ValueError(f'{label}: timestamp must be timezone aware')
    if frame.select(pl.any_horizontal(pl.col(name).is_null() for name in
                    (*spec.keys, spec.timestamp)).any()).item():
        raise ValueError(f'{label}: null identity or timestamp')
    if frame.select(pl.struct(spec.keys).is_duplicated().any()).item():
        raise ValueError(f'{label}: duplicate keys')
    order = list(dict.fromkeys((spec.timestamp, *spec.keys)))
    if not frame.select(order).equals(frame.select(order).sort(order)):
        raise ValueError(f'{label}: evidence is not ordered by timestamp and keys')
    for name in spec.tolerances:
        if not frame.schema[name].is_numeric():
            raise ValueError(f'{label}: tolerance requires numeric field {name}')
    return frame.select(sorted(columns))


def compare_audit_table(stage: str, reference: AuditEvidence, actual: AuditEvidence,
                        spec: AuditTableSpec, *, max_rows: int,
                        sample_limit: int = 10) -> AuditStageResult:
    """Compare one complete bounded table; malformed evidence fails closed.

    A state identity must cover all relevant pre-decision portfolio, working
    order and policy state. Its provenance is the caller's responsibility.
    """
    if stage not in STAGES or max_rows < 1 or not 0 <= sample_limit <= 100:
        raise ValueError('Invalid stage or audit bounds')
    if stage in STATE_DEPENDENT and not spec.state_identity:
        raise ValueError('Decision audit requires pre-decision state identity')
    left = _validate(reference, spec, max_rows, 'reference')
    right = _validate(actual, spec, max_rows, 'actual')
    if left.schema != right.schema:
        raise ValueError('Audit evidence schemas differ; normalize explicitly before comparison')
    # Rename all non-key columns explicitly to avoid suffix/key collisions.
    names = [name for name in left.columns if name not in spec.keys]
    joined = left.rename({name: f'l_{name}' for name in names}).with_columns(
        pl.lit(True).alias('_left')).join(
        right.rename({name: f'r_{name}' for name in names}).with_columns(
            pl.lit(True).alias('_right')), on=list(spec.keys), how='full', coalesce=True)
    missing = pl.col('_right').is_null()
    extra = pl.col('_left').is_null()
    present = ~(missing | extra)
    state_equal = pl.lit(True)
    if spec.state_identity and stage in STATE_DEPENDENT:
        a, b = pl.col(f'l_{spec.state_identity}'), pl.col(f'r_{spec.state_identity}')
        state_equal = a.is_not_null() & b.is_not_null() & a.eq_missing(b)
    checks = []
    for name in set(spec.fields) | {spec.timestamp}:
        a, b = pl.col(f'l_{name}'), pl.col(f'r_{name}')
        equal = a.eq_missing(b)
        if name in spec.tolerances:
            # Nulls match only nulls; NaNs/infinities never gain tolerance.
            equal = equal | (a.is_finite() & b.is_finite()
                             & ((a - b).abs() <= spec.tolerances[name])).fill_null(False)
        checks.append(~equal)
    mismatch = present & state_equal & pl.any_horizontal(checks)
    incomparable = present & ~state_equal
    joined = joined.with_columns(missing.alias('missing'), extra.alias('extra'),
        mismatch.alias('mismatch'), incomparable.alias('not_comparable'),
        pl.coalesce(f'l_{spec.timestamp}', f'r_{spec.timestamp}').alias('_at'))
    counts = joined.select(pl.col(name).sum() for name in
                           ('missing', 'extra', 'mismatch', 'not_comparable')).row(0)
    differences = joined.filter(pl.any_horizontal('missing', 'extra', 'mismatch', 'not_comparable')).sort(
        list(dict.fromkeys(('_at', *spec.keys))))
    status = ('equal' if not differences.height else
              'state_diverged_not_comparable' if counts[3] and not any(counts[:3]) else
              'equal_state_rule_mismatch' if stage in STATE_DEPENDENT and counts[2]
              and not (counts[0] or counts[1] or counts[3]) else 'different')
    return AuditStageResult(stage, status, left.height, right.height, *counts,
        differences['_at'][0] if differences.height else None,
        tuple(differences.head(sample_limit).to_dicts()))


def reconcile_execution_audit(reference: Mapping[str, AuditEvidence],
                              actual: Mapping[str, AuditEvidence],
                              specs: Mapping[str, AuditTableSpec], *,
                              max_rows_per_stage: int, sample_limit: int = 10) -> ExecutionAuditResult:
    """Require all audit layers, preserving observed upstream dependencies.

    Upstream divergence is contextual evidence, never a causal attribution.
    Equal-state rule mismatches remain visible even after upstream differences.
    """
    if any(set(value) != set(STAGES) for value in (reference, actual, specs)):
        raise ValueError('Execution audit requires every stage exactly once')
    results = []
    upstream = []
    for stage in STAGES:
        result = compare_audit_table(stage, reference[stage], actual[stage], specs[stage],
            max_rows=max_rows_per_stage, sample_limit=sample_limit)
        result = replace(result, upstream_divergence=tuple(upstream))
        results.append(result)
        if result.status != 'equal':
            upstream.append(stage)
    divergent = [result for result in results if result.status != 'equal']
    first = min(divergent, key=lambda result: (result.first_divergence_at, STAGES.index(result.stage))) if divergent else None
    return ExecutionAuditResult(tuple(results), first, not divergent)
