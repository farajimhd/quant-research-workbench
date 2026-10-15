"""Portable field schema identity never substitutes for a source-day seal."""
from dataclasses import replace
from datetime import timedelta

import pytest

from src.backend.backtest_program_inputs import preflight_program_feature_catalog
from src.backend.qmd_columnar_history import CertifiedHistoryPin
from src.market_engine import columnar_history_product as c
from src.trading_runtime.columnar_program_input_contract import (
    portable_revision, validate_program_input_contract,
)
from src.trading_runtime.columnar_rule_program import InputField
from tests.test_columnar_history_publication import Store, publish, consume, prepared_source
from tests.test_backtest_program_inputs import release_for


@pytest.fixture
def pin(prepared_source):
    store = Store()
    publish(store, prepared_source)
    return consume(store, prepared_source).pin()


def reseal(pin, *, parents=None, fields=None, next_day=False):
    """Synthetic independent coverage proofs, validated by real pin construction."""
    parents = parents or pin.parents
    fields = pin.fields if fields is None else fields
    revision = c.product_revision(fields, parents.producer_code_hash,
        volume_window_seconds=parents.volume_window_seconds, feature_plan_hash=parents.feature_plan_hash)
    scope = replace(pin.scope, product_revision=revision,
        session_date=pin.scope.session_date + timedelta(days=int(next_day)))
    header = {**pin.header, **scope.prefix(), **parents.payload(), 'field_count': len(fields)}
    header['field_content_hash'] = c.records_hash(c.FIELD,
        ({**scope.prefix(), 'field_id': f.field_id, 'field_name': f.name, 'unit': f.unit}
         for f in fields))
    header['content_hash'] = c.content_hash({n: v for n, v in header.items()
                                           if n not in {'content_hash', 'certified_at'}})
    return CertifiedHistoryPin(scope, fields, parents, header)


def contracts(pin, selected=None, revision=None):
    selected = pin.fields if selected is None else selected
    revision = revision or portable_revision(selected, pin.parents.producer_code_hash,
        volume_window_seconds=pin.parents.volume_window_seconds)
    return {f.name: InputField(f.name, f.unit, 'q_live.qmd_causal_history_value_v1', revision, 1000)
            for f in selected}


def test_two_day_source_plans_keep_distinct_exact_seals_and_one_portable_contract(pin):
    first = reseal(pin, parents=replace(pin.parents, feature_plan_hash='1' * 64))
    second = reseal(first, parents=replace(first.parents, feature_plan_hash='2' * 64), next_day=True)
    assert first.scope.product_revision != second.scope.product_revision
    assert first.header['content_hash'] != second.header['content_hash']
    fields = contracts(first)
    validate_program_input_contract(fields, first)
    validate_program_input_contract(fields, second)
    # Execute the actual preparation preflight for the same portable release.
    release = release_for(first)
    selected = tuple(f for f in first.fields if f.name in release.fields)
    portable_fields = contracts(first, selected)
    release = replace(release, fields=portable_fields)
    preflight_program_feature_catalog(release, {first.scope.listing_id: first})
    preflight_program_feature_catalog(release, {second.scope.listing_id: second})
    with pytest.raises(ValueError, match='cold coverage proof'):
        replace(first, parents=second.parents)


def test_exact_legacy_revision_remains_supported_and_arbitrary_revision_fails(pin):
    validate_program_input_contract(contracts(pin, revision=pin.scope.product_revision), pin)
    with pytest.raises(ValueError, match='contract revision'):
        validate_program_input_contract(contracts(pin, revision='f' * 64), pin)


@pytest.mark.parametrize('change', ['producer', 'volume'])
def test_changed_producer_semantics_require_new_portable_revision(pin, change):
    parents = replace(pin.parents, **({'producer_code_hash': '9' * 64} if change == 'producer'
                      else {'volume_window_seconds': 60}))
    other = reseal(pin, parents=parents)
    with pytest.raises(ValueError, match='contract revision'):
        validate_program_input_contract(contracts(pin), other)


def test_selected_release_schema_does_not_require_identical_full_day_catalog(pin):
    selected = (pin.fields[0],)
    fields = contracts(pin, selected)
    narrower = reseal(pin, fields=selected)
    validate_program_input_contract(fields, narrower)
    with pytest.raises(ValueError, match='producer-owned history contract'):
        validate_program_input_contract(contracts(pin), narrower)
    # A contract for a larger selected schema cannot be reused by a subset.
    wrong = {selected[0].name: contracts(pin)[selected[0].name]}
    with pytest.raises(ValueError, match='contract revision'):
        validate_program_input_contract(wrong, narrower)


@pytest.mark.parametrize('change', ['unit', 'cadence', 'product', 'name'])
def test_declared_contract_must_preserve_catalog_units_product_name_and_cadence(pin, change):
    fields = contracts(pin)
    name = pin.fields[0].name
    updates = {'unit': 'ratio'} if change == 'unit' else (
        {'resolution_ms': 2000} if change == 'cadence' else (
        {'product': 'arte.history_feature_v1'} if change == 'product' else {'name': pin.fields[1].name}))
    fields[name] = replace(fields[name], **updates)
    with pytest.raises(ValueError, match='producer-owned history contract'):
        validate_program_input_contract(fields, pin)


def test_revision_hash_requires_canonical_shared_ids_and_units(pin):
    fields = pin.fields
    expected = portable_revision(fields, pin.parents.producer_code_hash, volume_window_seconds=30)
    assert len(expected) == 64
    for invalid in (tuple(reversed(fields)), (replace(fields[0], unit='invented'),),
                    (replace(fields[0], field_id=99999),), fields + (fields[-1],)):
        with pytest.raises(ValueError, match='canonical shared field catalog'):
            portable_revision(invalid, pin.parents.producer_code_hash, volume_window_seconds=30)
    with pytest.raises(ValueError):
        portable_revision(fields, 'invalid', volume_window_seconds=30)
    with pytest.raises(ValueError):
        portable_revision(fields, pin.parents.producer_code_hash, volume_window_seconds=True)
    with pytest.raises(ValueError, match='cold-certified'):
        validate_program_input_contract(contracts(pin), object())
