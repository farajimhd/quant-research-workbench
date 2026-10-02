"""Binary transport precision and contract coverage independent of row hashes."""
import math
import struct

import pytest

from src.trading_runtime.arte_journal_rowbinary import encode_journal_rows
from src.trading_runtime.arte_journal_writer import _CONTRACTS


@pytest.mark.parametrize('value', [5.810210334455945e-05, -0.0,
                                  math.nextafter(0.0, 1.0),
                                  math.nextafter(1.0, 2.0), -1.234567891209e12])
def test_float_wire_preserves_original_ieee_bits(value):
    payload = encode_journal_rows((('value', 'Float64'),), ({'value': value},))
    assert payload == struct.pack('<d', value)
    assert struct.unpack('<d', payload)[0].hex() == value.hex()


def test_mixed_native_types_and_nullable_markers():
    columns = (('uuid', 'UUID'), ('date', 'Date'), ('text', 'String'),
               ('amount', 'Decimal(38, 18)'), ('missing', 'Nullable(Float64)'),
               ('present', 'Nullable(Float64)'), ('at', "Nullable(DateTime64(6, 'UTC'))"))
    row = {'uuid': '00112233-4455-6677-8899-aabbccddeeff', 'date': '1970-01-02',
           'text': 'é', 'amount': '-1.000000000000000001', 'missing': None,
           'present': -0.0, 'at': '1970-01-01T00:00:00.000001Z'}
    expected = (bytes.fromhex('7766554433221100ffeeddccbbaa9988') + b'\x01\x00'
                + b'\x02\xc3\xa9' + (-1000000000000000001).to_bytes(16, 'little', signed=True)
                + b'\x01' + b'\x00' + struct.pack('<d', -0.0)
                + b'\x00' + struct.pack('<q', 1))
    assert encode_journal_rows(columns, (row,)) == expected


def test_all_float_family_schema_types_have_encoders():
    from src.trading_runtime.arte_journal_rowbinary import _fields
    families = [contract for contract in _CONTRACTS.values()
                if any('Float64' in kind for _, kind in contract.columns)]
    assert families
    for contract in families:
        assert len(_fields(tuple(contract.columns))) == len(contract.columns)


@pytest.mark.parametrize('row', [{}, {'value': 1., 'extra': 2.}, {'value': float('nan')},
                                  {'value': float('inf')}])
def test_invalid_float_rows_fail_closed(row):
    with pytest.raises(ValueError):
        encode_journal_rows((('value', 'Float64'),), (row,))


def test_unknown_schema_and_decimal_precision_fail_closed():
    with pytest.raises(ValueError, match='Unsupported'):
        encode_journal_rows((('value', 'Array(Float64)'),), ())
    with pytest.raises(ValueError, match='scale'):
        encode_journal_rows((('value', 'Decimal(38, 18)'),), ({'value': '1e-19'},))
