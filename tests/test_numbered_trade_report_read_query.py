"""Cold journal UNION reads stay SELECT-only through the report adapter."""
import pytest

from scripts.clickhouse.report_strategy_one_trades import report_select_query


def test_parenthesized_union_becomes_equivalent_outer_select():
    query = '(SELECT 1 AS value) UNION ALL (SELECT 2 AS value) FORMAT JSONEachRow'
    assert report_select_query(query) == f'SELECT * FROM ({query[:-19]}) FORMAT JSONEachRow'


def test_ordinary_select_remains_unchanged():
    assert report_select_query(' SELECT 1 FORMAT JSONEachRow; ') == 'SELECT 1 FORMAT JSONEachRow'


@pytest.mark.parametrize('query', [
    '(SELECT 1) UNION ALL (DROP TABLE arte.x) FORMAT JSONEachRow',
    'INSERT INTO arte.x SELECT 1',
    '(SELECT 1) UNION ALL (SELECT 2)',
])
def test_adapter_does_not_admit_write_or_unrecognized_union(query):
    with pytest.raises(ValueError):
        report_select_query(query)
