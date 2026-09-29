"""One-query-per-batch exact one-second census."""
from datetime import date

from research.rl_trading.v1 import arte_sql
from research.rl_trading.v6.census import one_second_counts


class _Reader:
    def __init__(self):
        self.statements = []

    def execute(self, statement):
        arte_sql._approved(statement)
        self.statements.append(statement)
        return '{"ticker":"ABC","n":7}\n'


def test_census_returns_zero_for_certified_empty_listing():
    attempt = '11111111-1111-1111-1111-111111111111'
    day = date(2026, 8, 3)
    source = {'build_id': 'build', 'units': {str(day): {
        ticker: {'bars': {'attempt_id': attempt}} for ticker in ('ABC', 'DEF')}}}
    reader = _Reader()
    assert one_second_counts(reader, source, day, ['ABC', 'DEF']) == {
        'ABC': 7, 'DEF': 0}
    assert len(reader.statements) == 1
    assert 'resolution_ms=1000' in reader.statements[0]
