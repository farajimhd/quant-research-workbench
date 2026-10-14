import struct
from types import SimpleNamespace

import pytest

from src.market_engine.typed_product_insert import insert_product_rows
from src.trading_runtime.arte_journal_schema import TableContract


@pytest.mark.parametrize('value', [1.66, 1.945, 1.59, 7.69, 1.14, 4.81, -0.0])
def test_binary_product_float_is_exact_without_decimal_server_parsing(value):
    table=TableContract('exact_test', (('value','Float64'),), 'tuple()', 'tuple()', database='q_live')
    requests=[]
    insert_product_rows(SimpleNamespace(execute=requests.append), table, [{'value':value}])
    header,_,payload=requests[0].partition(b'\n')
    assert header.endswith(b'FORMAT RowBinary')
    assert payload == struct.pack('<d',value)


def test_unsupported_and_oversized_product_rows_fail_before_io():
    client=SimpleNamespace(execute=lambda request: pytest.fail('Unexpected INSERT'))
    table=TableContract('exact_test', (('value','Float64'),), 'tuple()', 'tuple()', database='q_live')
    for rows in ([], [{'value':1.0,'extra':0}], [{'value':float('nan')}], [{'value':1.0}]*4097):
        with pytest.raises(ValueError): insert_product_rows(client,table,rows)
