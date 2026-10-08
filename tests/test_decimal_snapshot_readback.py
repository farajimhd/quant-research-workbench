import json
from types import SimpleNamespace

import pytest

from src.trading_runtime.decimal_snapshot_readback import (
    RULE, canonical_decimal_row, declared_decimal_row, declared_decimal_rows,
)
from src.trading_runtime.fixed_structural_lot_snapshot import ROOT, TABLES, _row


def test_actual_clickhouse_scale_stripped_strings_are_exactly_padded():
    table = SimpleNamespace(columns=(('price', 'Decimal(38, 18)'), ('optional', 'Nullable(Decimal(38, 18))')))
    for raw, expected in (('5.3', '5.300000000000000000'), ('0', '0.000000000000000000'),
                          ('0.000000000000000001', '0.000000000000000001')):
        wire = json.loads(json.dumps({'price': raw, 'optional': None}))
        assert canonical_decimal_row(table, wire) == {'price': expected, 'optional': None}
        assert wire['price'] == raw


@pytest.mark.parametrize('value', [5.3, 0, True, '5e0', 'NaN', 'Infinity', '+5.3', ' 5.3',
                                  '05.3', '0.0000000000000000001', '100000000000000000000'])
def test_no_float_rounding_or_excess_precision_is_accepted(value):
    table = SimpleNamespace(columns=(('price', 'Decimal(38, 18)'),))
    with pytest.raises(ValueError): canonical_decimal_row(table, {'price': value})


def test_real_snapshot_domain_validator_still_checks_normalized_rows():
    row = {}
    for name, kind in ROOT.columns:
        if kind.startswith('UInt'): value = 1
        elif 'Decimal' in kind: value = '5.3'
        elif kind == 'UUID': value = '11111111-1111-4111-8111-111111111111'
        elif kind == 'FixedString(64)': value = 'a' * 64
        else: value = 'sample'
        row[name] = value
    with pytest.raises(ValueError, match='canonical text'): _row(ROOT, row)
    normalized = canonical_decimal_row(ROOT, row)
    assert _row(ROOT, normalized) == normalized
    with pytest.raises(ValueError): _row(ROOT, {**normalized, 'reference_ask': '-1.000000000000000000'})
    with pytest.raises(ValueError): canonical_decimal_row(ROOT, {**row, 'foreign': 1})


def test_declared_reader_rejects_unissued_source():
    table = SimpleNamespace(columns=(('price', 'Decimal(38, 18)'),))
    with pytest.raises(ValueError): declared_decimal_row(object(), table, {'price': '5.3'})


@pytest.mark.parametrize('contract', TABLES, ids=lambda table: table.name)
def test_all_fixed_lot_wire_schemas_preserve_exact_domain_values(contract):
    canonical = {}
    for name, kind in contract.columns:
        if kind.startswith('Nullable(Decimal'):
            value = None
        elif 'Decimal' in kind:
            value = '5.300000000000000000'
        elif kind.startswith('UInt'):
            value = 1
        elif kind == 'UUID':
            value = '11111111-1111-4111-8111-111111111111'
        elif kind == 'FixedString(64)':
            value = 'a' * 64
        else:
            value = 'sample'
        canonical[name] = value
    wire = {name: ('5.3' if value == '5.300000000000000000' else value)
            for name, value in canonical.items()}
    restored = canonical_decimal_row(contract, json.loads(json.dumps(wire)))
    assert restored == canonical
    assert _row(contract, restored) == canonical


@pytest.mark.parametrize('rules', [(), (RULE,), (RULE, RULE)])
def test_declared_batch_selection_with_explicit_controlled_authority(monkeypatch, rules):
    """Selection unit test only; controlled authority is not native issuance proof."""
    from src.backend import backtest_fixed_structural_lot_source as authority
    from src.trading_runtime import strategy_registry
    checks = []
    payload = {'immutable': 'controlled-selection-fixture'}
    release = SimpleNamespace(rule_set_contracts=rules, canonical_payload=lambda: payload)
    source = SimpleNamespace(
        installed_payload={'strategy': {'strategy_number': 90,
                                       'numbered_release': {'contract': payload}}},
        require_installed_admission=lambda: checks.append('admission'),
    )
    monkeypatch.setattr(authority, 'require_native_fixed_structural_lot_source',
                        lambda value: checks.append('source') if value is source else pytest.fail('foreign source'))
    monkeypatch.setattr(strategy_registry, 'numbered_strategy', lambda number: release)
    table = SimpleNamespace(columns=(('price', 'Decimal(38, 18)'),))
    rows = ({'price': '5.3'}, {'price': '0'})
    if len(rules) == 2:
        with pytest.raises(ValueError, match='one exact declared rule'):
            declared_decimal_rows(source, table, rows)
    else:
        result = declared_decimal_rows(source, table, rows)
        if rules:
            assert result == ({'price': '5.300000000000000000'},
                              {'price': '0.000000000000000000'})
        else:
            assert result is rows
    assert checks == ['source', 'admission']
