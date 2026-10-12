import struct

import pytest

from src.trading_runtime.arte_journal_writer import _CONTRACTS, _wire_row, typed_row
from src.trading_runtime.trading_price_contracts import PRICE_FAMILIES
from src.trading_runtime.trading_price_rows import exact_price_row, restore_exact_price_row


def raw_row(source):
    values = {}
    for field, kind in _CONTRACTS[source].columns:
        if field == 'content_hash':
            continue
        base = kind[9:-1] if kind.startswith('Nullable(') else kind
        if base.startswith('DateTime64'):
            value = '2026-10-12T12:00:00+00:00'
        elif base == 'Date':
            value = '2026-10-12'
        elif base == 'UUID':
            value = '00000000-0000-0000-0000-000000000001'
        elif base.startswith('Decimal') or base.startswith(('UInt', 'Int')):
            value = 1
        elif base == 'Bool':
            value = True
        elif base == 'FixedString(64)':
            value = 'a' * 64
        else:
            value = 'test'
        values[field] = value
    for field in PRICE_FAMILIES[source][1]:
        values[field] = 9.989999771118164
    return values


@pytest.mark.parametrize('source', tuple(PRICE_FAMILIES))
def test_shared_hash_wire_and_readback_are_exact(source):
    target, fields = PRICE_FAMILIES[source]
    raw = raw_row(source)
    projected = exact_price_row(source, raw)
    wire = _wire_row(target, projected)
    quoted = dict(wire)
    for field in fields:
        quoted[field + '_bits'] = str(quoted[field + '_bits'])
    assert restore_exact_price_row(source, quoted, stored_utc=True) == restore_exact_price_row(
        source, wire, stored_utc=True)
    restored = restore_exact_price_row(source, wire, stored_utc=True)
    for field in fields:
        assert struct.pack('>d', restored[field]) == struct.pack('>d', raw[field])
    changed = dict(wire)
    changed[fields[0] + '_bits'] += 1
    with pytest.raises(ValueError, match='hash mismatch'):
        restore_exact_price_row(source, changed, stored_utc=True)
    changed[fields[0] + '_bits'] = 0x7ff0000000000000
    with pytest.raises(ValueError):
        restore_exact_price_row(source, changed, stored_utc=True)


def test_nullable_and_unset_order_prices_and_no_legacy_rounding():
    source = 'trading_order_command_v1'
    raw = raw_row(source)
    raw.update(limit_price=None, aux_price=0.)
    restored = restore_exact_price_row(source, exact_price_row(source, raw), stored_utc=False)
    assert restored['limit_price'] is None
    assert restored['aux_price'] == 0.
    raw['aux_price'] = 9.989999771118164
    with pytest.raises(ValueError, match='loses decimal precision'):
        typed_row(source, raw)


def test_exact_price_contract_confers_no_commit_family_admission():
    from src.trading_runtime.arte_journal_writer import _FAMILIES
    assert not {target for target, _ in PRICE_FAMILIES.values()} & {row[0] for row in _FAMILIES}


def test_missing_hash_wrong_columns_and_nonnullable_prices_fail_closed():
    source = 'trading_execution_v1'
    row = exact_price_row(source, raw_row(source))
    for broken in (
        {key: value for key, value in row.items() if key != 'content_hash'},
        {**row, 'price_bits': None},
        {**row, 'unexpected_price': 1},
        {**row, 'price_bits': 0x8000000000000000},
    ):
        with pytest.raises(ValueError):
            restore_exact_price_row(source, broken)
