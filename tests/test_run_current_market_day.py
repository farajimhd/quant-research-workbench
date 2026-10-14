"""Runnable operator planning, explicit execution gate and exact authorities."""
import asyncio
from threading import get_ident

import pytest

from scripts import run_current_market_day as operator
from test_arte_market_day_source_plan import plan
from test_arte_market_day_certification import DAY


def arguments():
    return ['--start-date', DAY, '--end-date', DAY, '--workers', '2',
        '--max-threads', '2', '--max-memory-bytes', '1048576', '--query-timeout', '30',
        '--max-result-rows', '100000', '--max-result-bytes', '16000000', '--max-plan-units', '10',
        '--canonical-storage-policy', 'synthetic_canonical', '--canonical-disk', 'synthetic_source_disk']


def test_show_grants_opens_nothing_and_writes_only_shared_products(monkeypatch, capsys):
    from research.mlops import env
    monkeypatch.setattr(env, 'discover_env_files', lambda *args: pytest.fail('Read credentials for grant review'))
    monkeypatch.setattr(operator, 'factories', lambda *args: pytest.fail('Opened factories for grant review'))
    assert operator.main(arguments() + ['--show-grants']) == 0
    text = capsys.readouterr().out
    assert 'REVIEW ONLY' in text and 'CREATE' not in text
    assert 'GRANT INSERT ON arte.bars_v1 TO current_market_day_publisher' in text
    assert 'GRANT INSERT ON q_live.' not in text
    assert 'GRANT INSERT ON market_sip_compact.' not in text
    assert '*' not in text


def test_operator_loads_base_environment_before_native_plan(monkeypatch):
    from research.mlops import env
    events = []
    monkeypatch.setattr(env, 'discover_env_files', lambda root: (root / '.env',))
    def load(paths, *, verbose):
        assert paths == (operator.Path(operator.__file__).resolve().parents[1] / '.env',)
        assert verbose is False
        events.append('base_environment')
    monkeypatch.setattr(env, 'load_env_files', load)
    async def run(args):
        assert events == ['base_environment']
        assert args.execute is False
        events.append('native_plan')
        return 0
    monkeypatch.setattr(operator, 'run', run)
    assert operator.main(arguments()) == 0
    assert events == ['base_environment', 'native_plan']


@pytest.mark.parametrize('flags', [['--execute'], ['--execute', '--expected-definition-hash', 'bad'],
    ['--parent', 'bad:2026-08-18']])
def test_incomplete_execution_or_parent_pin_fails_inert(flags):
    with pytest.raises(SystemExit):
        operator.parse_args(arguments() + flags)


@pytest.mark.parametrize('expanded', [False, True])
def test_exact_role_grants_reject_expansion(expanded):
    profile = operator.principal_plan((2026,))[0]
    class Client:
        def execute(self, sql):
            if sql == 'SELECT currentUser()':
                return profile[0]
            if sql == "SELECT getSetting('readonly')":
                return '1'
            return '\n'.join(f'GRANT SELECT ON {db}.{table} TO {profile[0]}' for db, table in profile[1]) + (
                f'\nGRANT INSERT ON arte.bars_v1 TO {profile[0]}' if expanded else '')
    if expanded:
        with pytest.raises(ValueError, match='grants differ'):
            operator._permission(Client(), profile)
    else:
        operator._permission(Client(), profile)


@pytest.mark.parametrize('mode', ['plan', 'execute', 'changed_source'])
def test_operator_runs_native_plan_before_explicit_execution(monkeypatch, capsys, mode):
    from scripts import build_market_day
    from pipelines.market_sip.events.market_day_checkpoint import CompletedMarketDayLedger
    from pipelines.market_sip.events import market_day_checkpoint
    supplied = plan()
    supplied['predecessors'] = {day: None for day in supplied['requested']}
    threads, closes, executions = [], [], []
    main = get_ident()
    class Reader:
        def close(self):
            closes.append(True)
    def readers():
        threads.append(get_ident())
        return Reader()
    monkeypatch.setattr(operator, 'factories', lambda *args: (readers,
        lambda *args: pytest.fail('Opened writer outside native execution')))
    monkeypatch.setattr(build_market_day, 'storage_preflight', lambda *args, **kwargs: None)
    monkeypatch.setattr(operator, 'canonical_storage_preflight', lambda *args, **kwargs: None)
    def source_plan(client, args):
        assert args.symbols == () and args.max_plan_units == 10
        assert args.start.isoformat() == args.end.isoformat() == DAY
        return supplied
    monkeypatch.setattr(build_market_day, 'source_plan', source_plan)
    async def ledger(factory, build, scopes):
        return CompletedMarketDayLedger(build, scopes, (), ())
    monkeypatch.setattr(market_day_checkpoint, 'load_completed_market_day_ledger', ledger)
    monkeypatch.setattr(operator.platform, 'node', lambda: 'DESKTOP-SAAI85T')
    async def execute(definition, **kwargs):
        assert kwargs['workers'] == 2 and not kwargs['parents']
        executions.append(definition)
        for phase in ('preflight', 'complete'):
            await kwargs['on_progress'](dict(phase=phase, active=0, queued=0, completed=2,
                skipped=0, failed=0, total=2))
    monkeypatch.setattr(operator, 'execute_current_market_day_build', execute)
    assert asyncio.run(operator.run(operator.parse_args(arguments()))) == 0
    output = capsys.readouterr().out
    build = output.split(' | build ')[1].split(' |')[0]
    assert not executions and 'PLAN ONLY' in output
    if mode != 'plan':
        expected = '0' * 64 if mode == 'changed_source' else build
        args = operator.parse_args(arguments() + ['--execute', '--expected-definition-hash', expected])
        if mode == 'changed_source':
            with pytest.raises(ValueError, match='approved definition'):
                asyncio.run(operator.run(args))
            assert not executions
        else:
            assert asyncio.run(operator.run(args)) == 0
            assert len(executions) == 1
    assert threads and all(thread != main for thread in threads)
    assert len(closes) == (1 if mode == 'plan' else 2)


@pytest.mark.parametrize('mode', ['healthy', 'policy', 'parts', 'fallback_disk'])
def test_canonical_storage_is_explicit_and_checks_actual_parts(mode):
    class Source:
        def query(self, sql, label):
            if label == 'canonical_table_policy':
                return [dict(name='events_2026', storage_policy='default' if mode == 'policy' else 'canonical')]
            if label == 'canonical_policy_disks':
                return [dict(disks=['default'] if mode == 'fallback_disk' else ['source_disk'])]
            return [dict(disk_name='default' if mode == 'parts' else 'source_disk')]
    if mode == 'healthy':
        operator.canonical_storage_preflight(Source(), years=(2026,), policy='canonical', disks=('source_disk',))
    else:
        with pytest.raises(ValueError):
            operator.canonical_storage_preflight(Source(), years=(2026,), policy='canonical', disks=('source_disk',))
