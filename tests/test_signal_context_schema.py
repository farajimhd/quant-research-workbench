import pytest

from src.backend.signal_context_schema import TABLES, LOGICAL_TABLES, CONTEXT, context_table, context_table_map, schema_plan


def test_exact_thirteen_context_tables_preserve_legacy_columns_and_calendar():
    assert len(TABLES) == 13 and len({table.name for table in TABLES}) == 13
    assert len(context_table_map()) == 12
    for original in LOGICAL_TABLES:
        wrapped = context_table(original)
        assert wrapped.columns[:-2] == original.columns
        assert wrapped.name == original.name.removesuffix('_v1') + '_context_v2'
        assert wrapped.order == 'source_context_id, ' + original.order
        assert "PARTITION BY toYYYYMM(session_key)" in wrapped.ddl()
        assert "storage_policy = 'live_market_ssd'" in wrapped.ddl()
        assert dict(wrapped.columns)['session_key'] == 'Date'
    assert CONTEXT in TABLES
    with pytest.raises(ValueError):
        context_table('private_strategy_table')


def test_plan_contains_only_explicit_thirteen_table_reader_publisher_grants():
    plan = schema_plan(reader_principals=('reader_a', 'reader_b'), publisher_principal='publisher')
    assert len(plan['ddl']) == 13 and len(plan['grants']) == 52
    assert len(set(plan['grants'])) == 52
    assert {g for g in plan['grants'] if g.startswith('GRANT INSERT')} == {
        f'GRANT INSERT ON arte.{table.name} TO publisher' for table in TABLES}
    assert all('*' not in g and 'WITH GRANT' not in g for g in plan['grants'])
    for readers, publisher in [(('reader', 'reader'), 'publisher'), (('reader',), 'x; DROP USER y')]:
        with pytest.raises(ValueError):
            schema_plan(reader_principals=readers, publisher_principal=publisher)
