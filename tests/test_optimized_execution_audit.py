from dataclasses import replace
from datetime import datetime, timedelta, timezone

import polars as pl
import pytest

from src.backend.optimized_execution_audit import (
    STAGES, AuditEvidence, AuditTableSpec, compare_audit_table, reconcile_execution_audit,
)


def evidence():
    return AuditEvidence(pl.DataFrame({'id': [1, 2],
        'at': [datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=i) for i in range(2)],
        'value': [1., 2.], 'state': ['flat', 'flat']}), 2, True)


def spec():
    return AuditTableSpec(('id',), 'at', ('value',), state_identity='state')


def compare(left, right, **kwargs):
    return compare_audit_table('decisions', left, right, kwargs.pop('spec', spec()), max_rows=10, **kwargs)


def test_equal_and_bounded_samples():
    source = evidence()
    assert compare(source, source).status == 'equal'
    changed = replace(source, frame=source.frame.with_columns((pl.col('value') + 1).alias('value')))
    result = compare(source, changed, sample_limit=1)
    assert result.status == 'equal_state_rule_mismatch'
    assert result.mismatched_rows == 2
    assert len(result.samples) == 1
    assert result.first_divergence_at == source.frame['at'][0]


def test_state_divergence_is_not_rule_mismatch():
    source = evidence()
    changed = replace(source, frame=source.frame.with_columns(pl.lit('long').alias('state'), pl.lit(8.).alias('value')))
    result = compare(source, changed)
    assert result.status == 'state_diverged_not_comparable'
    assert result.mismatched_rows == 0
    assert result.not_comparable_rows == 2


def test_market_only_rule_outputs_stay_comparable_after_position_diverges():
    source = evidence()
    changed = replace(source, frame=source.frame.with_columns(
        pl.lit('long').alias('state'), pl.lit(8.).alias('value')))
    result = compare_audit_table('rule_outputs', source, changed, spec(), max_rows=10)
    assert result.mismatched_rows == 2
    assert result.not_comparable_rows == 0
    assert compare_audit_table('rule_outputs', source, source,
        replace(spec(), state_identity=None), max_rows=10).status == 'equal'


def test_only_explicit_tolerance_applies():
    source = evidence()
    changed = replace(source, frame=source.frame.with_columns((pl.col('value') + .01).alias('value')))
    assert compare(source, changed).mismatched_rows == 2
    assert compare(source, changed, spec=replace(spec(), tolerances={'value': .02})).status == 'equal'


def test_missing_and_extra_rows_preserved():
    source = evidence()
    changed = replace(source, frame=source.frame.with_columns((pl.col('id') + 1).alias('id')))
    result = compare(source, changed)
    assert (result.missing_rows, result.extra_rows, result.mismatched_rows) == (1, 1, 1)


@pytest.mark.parametrize('mode', ['receipt', 'incomplete', 'duplicate', 'order', 'timezone', 'null_key', 'schema'])
def test_invalid_evidence_fails_closed(mode):
    source = evidence()
    changed = source
    if mode == 'receipt': changed = replace(source, expected_rows=3)
    if mode == 'incomplete': changed = replace(source, complete=False)
    if mode == 'duplicate': changed = replace(source, frame=source.frame.with_columns(pl.lit(1).alias('id')))
    if mode == 'order': changed = replace(source, frame=source.frame.reverse())
    if mode == 'timezone': changed = replace(source, frame=source.frame.with_columns(pl.col('at').dt.replace_time_zone(None)))
    if mode == 'null_key': changed = replace(source, frame=source.frame.with_columns(pl.lit(None).cast(pl.Int64).alias('id')))
    if mode == 'schema': changed = replace(source, frame=source.frame.with_columns(pl.col('value').cast(pl.Int64)))
    with pytest.raises(ValueError): compare(source, changed)


def test_complete_pipeline_tracks_dependencies_and_requires_all_layers():
    source = {stage: evidence() for stage in STAGES}
    actual = dict(source)
    actual['fills'] = replace(evidence(), frame=evidence().frame.with_columns(pl.lit(7.).alias('value')))
    specs = {stage: spec() for stage in STAGES}
    result = reconcile_execution_audit(source, actual, specs, max_rows_per_stage=10)
    assert not result.equal
    assert result.first_divergence.stage == 'fills'
    assert result.stages[-1].upstream_divergence == ('fills',)
    with pytest.raises(ValueError):
        reconcile_execution_audit({}, actual, specs, max_rows_per_stage=10)


def test_bounds_and_state_proof_required():
    with pytest.raises(ValueError):
        compare(evidence(), evidence(), spec=replace(spec(), state_identity=None))
    with pytest.raises(ValueError):
        compare_audit_table('inputs', evidence(), evidence(), spec(), max_rows=1)
    with pytest.raises(ValueError):
        replace(spec(), tolerances={'value': float('nan')})
