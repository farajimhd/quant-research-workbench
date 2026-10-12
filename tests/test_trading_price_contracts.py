import struct

import pytest

from src.trading_runtime.arte_journal_schema import TABLES
from src.trading_runtime.arte_journal_writer import _CONTRACTS
from src.trading_runtime.trading_price_contracts import (
    PRICE_FAMILIES, exact_price_contracts, transform_price_fields,
)


BASE = _CONTRACTS


def test_shared_contracts_preserve_legacy_and_nonprice_columns():
    before = tuple(TABLES)
    contracts = exact_price_contracts(BASE)
    assert len(contracts) == 11
    assert sum(len(fields) for _, fields in PRICE_FAMILIES.values()) == 26
    for source, (target, fields) in PRICE_FAMILIES.items():
        old = BASE[source]
        new = contracts[target]
        assert new.partition == old.partition
        assert new.order == old.order
        assert len(new.columns) == len(old.columns)
        assert tuple(item for item in new.columns if not item[0].endswith('_bits')) == tuple(
            item for item in old.columns if item[0] not in fields)
        assert "live_market_ssd" in new.ddl()
    assert tuple(TABLES) == before


@pytest.mark.parametrize("source", tuple(PRICE_FAMILIES))
def test_exact_named_price_round_trip(source):
    _, fields = PRICE_FAMILIES[source]
    values = {field: 9.989999771118164 for field in fields}
    values['unchanged_quantity'] = '1.0000000000'
    encoded = transform_price_fields(source, values, BASE)
    restored = transform_price_fields(source, encoded, BASE, restore=True)
    assert restored == values
    assert all(struct.pack('>d', restored[field]) == struct.pack('>d', values[field]) for field in fields)
    assert values['unchanged_quantity'] == encoded['unchanged_quantity']


def test_rejects_old_hashes_missing_fields_and_nonfinite_prices():
    source = 'trading_order_command_v1'
    for values in ({'content_hash': 'old'}, {}, {'limit_price': float('nan'), 'aux_price': 0.}):
        with pytest.raises(ValueError):
            transform_price_fields(source, values, BASE)
