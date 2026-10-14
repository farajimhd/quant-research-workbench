"""Grant plans are checked by the production publication permission gates."""
from dataclasses import replace
import json
import pytest

from scripts.clickhouse import plan_program_app_principals as plan
from src.backend.qmd_columnar_history import permission_preflight as history_gate
from pipelines.market_sip.events.opening_price_reference_producer import publication_permission_preflight as opening_gate
from pipelines.market_sip.events.liquidity_resolution_producer import publication_permission_preflight as liquidity_gate
from src.trading_runtime.columnar_strategy_publication import _writer_permission as release_writer_gate
from src.trading_runtime.columnar_strategy_reader import release_read_permission_preflight as release_reader_gate
from src.backend.strategy_configuration_transport import configuration_writer_preflight as selection_gate


class GrantClient:
    def __init__(self, profile):
        self.profile = profile

    def execute(self, query):
        if query == 'SELECT currentUser()':
            return self.profile.principal
        if query == 'SHOW GRANTS FINAL':
            return '\n'.join(self.profile.grants())
        if 'FROM system.storage_policies' in query:
            return json.dumps({'disks': ['live_market_ssd']})
        if 'FROM system.tables' in query:
            return '\n'.join(json.dumps(dict(name=t.name, engine='MergeTree',
                storage_policy='live_market_ssd', partition_key=t.partition,
                sorting_key=t.order)) for t in plan.SELECTION)
        if 'FROM system.columns' in query:
            return '\n'.join(json.dumps(dict(table=t.name, name=name, type=kind))
                for t in plan.SELECTION for name, kind in t.columns)
        if 'FROM system.parts' in query:
            return ''
        if 'FROM system.data_skipping_indices' in query:
            return ''
        raise AssertionError('Unexpected access in grant-only qualification: ' + query)


@pytest.mark.parametrize('principal,gate', [
    ('program_history_publisher', lambda client: history_gate(client, writable=True)),
    ('program_opening_reference_publisher', lambda client: opening_gate(client, writable=True)),
    ('program_liquidity_publisher', lambda client: liquidity_gate(client, writable=True)),
    ('program_release_publisher', release_writer_gate),
    ('program_release_reader', release_reader_gate),
    ('program_selection_publisher', selection_gate),
])
def test_plans_pass_native_permission_contracts(principal, gate):
    profile = next(p for p in plan.desired_plans() if p.principal == principal)
    gate(GrantClient(profile))


def test_source_reader_satisfies_all_producer_read_contracts():
    profile = plan.desired_plans()[0]
    assert not profile.writes
    for gate in (history_gate, opening_gate, liquidity_gate):
        gate(GrantClient(profile), writable=False)


def test_writes_are_only_central_declared_products_and_never_source_parents():
    profiles = plan.desired_plans()
    assert len(profiles) == len({p.principal for p in profiles}) == 8
    writes = set().union(*(p.writes for p in profiles))
    assert writes == (plan.scopes(plan.HISTORY) | plan.scopes(plan.OPENING) | plan.scopes(plan.PRIOR_CLOSE)
        | plan.scopes(plan.LIQUIDITY) | plan.scopes(plan.RELEASE) | plan.scopes(plan.SELECTION))
    assert not ({('arte', 'bars_v1'), ('arte', 'liquidity_100ms_v1'),
        ('arte', 'liquidity_execution_price_100ms_v1'),
        ('q_live', 'id_symbol_interval_v1'), ('q_live', 'market_stock_split_v1')} & writes)
    assert all(p.writes <= p.reads for p in profiles)
    assert all(statement.startswith(('GRANT SELECT ON ', 'GRANT INSERT ON '))
        and '*' not in statement for p in profiles for statement in p.grants())


@pytest.mark.parametrize('principal,gate', [
    ('program_history_publisher', lambda c: history_gate(c, writable=True)),
    ('program_opening_reference_publisher', lambda c: opening_gate(c, writable=True)),
    ('program_liquidity_publisher', lambda c: liquidity_gate(c, writable=True)),
    ('program_release_publisher', release_writer_gate),
    ('program_selection_publisher', selection_gate),
])
def test_native_gates_reject_source_write_expansion(principal, gate):
    profile = next(p for p in plan.desired_plans() if p.principal == principal)
    with pytest.raises(ValueError):
        gate(GrantClient(replace(profile, writes=profile.writes | {('arte', 'bars_v1')})))


def test_plan_command_never_opens_connections(monkeypatch, capsys):
    from scripts.clickhouse import provision_trading_journal
    monkeypatch.setattr(provision_trading_journal, '_admin_client',
        lambda *args: pytest.fail('Plan opened a connection'))
    assert plan.main([]) == 0
    output = capsys.readouterr().out
    assert output.startswith('PLAN ONLY')
    assert 'No connection or operational change' in output
    assert 'GRANT SELECT' not in output


def test_apply_is_not_supported_by_review_command():
    with pytest.raises(SystemExit) as error:
        plan.main(['--apply'])
    assert error.value.code == 2
