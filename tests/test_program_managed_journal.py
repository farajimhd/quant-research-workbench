"""Managed native contracts and ownership transfer; no real database operations."""
import asyncio
from dataclasses import replace
from hashlib import sha256
from threading import Event, get_ident
from types import SimpleNamespace

import pytest

from src.backend import program_managed_journal as managed
from src.backend.backtest_market_data import CertifiedMarketDayPlan, ExecutionInterval
from src.backend.replay_run_service import ReplayRunController
from src.trading_runtime.journal_contract import canonical_json
from tests.test_program_controller_runtime import definition_and_actor


def controller_request(tmp_path):
    definition, actor = definition_and_actor()
    plan = CertifiedMarketDayPlan(ExecutionInterval.fixed(100), 'synthetic-build', 'b' * 64,
        (definition.session_date.isoformat(),), ('ABC',), (), (100,), 'a' * 64)
    revision = {**definition.configuration_revision, 'content_hash':
        sha256(canonical_json(definition.configuration_revision['payload']).encode()).hexdigest()}
    controller = ReplayRunController(replace(definition, market_data_plan=plan.payload(),
        configuration_revision=revision), runtime_root=tmp_path, program_strategy=actor,
        program_liquidity_resolution_ms=1000)
    return controller, managed.ManagedProgramJournalRequest(plan, plan, 'c' * 64)


def test_local_contracts_preserve_native_broker_settings_and_exact_prices(tmp_path, monkeypatch):
    controller, request = controller_request(tmp_path)
    monkeypatch.setattr('src.backend.backtest_journal_clickhouse.backtest_code_hash',
        lambda _: controller._program_strategy.program_release.header['code_hash'])
    values = managed.prepare_managed_journal_contracts(controller, request)
    config = values['broker_contract'].config
    assert config.liquidity_resolution_ms == 1000
    assert config.commission_per_share == .005 and config.minimum_commission == 1.
    assert config.market_slippage_bps == 5.  # Preserve the native baseline selection.
    assert values['journal_price_contract'].row['price_codec'] == 'binary64-v1'
    assert values['runtime_config'].run_id == controller.run_id
    assert controller._runtime is controller._journal is None


@pytest.mark.parametrize('mode', ['adopt', 'unadopted', 'failure', 'cancel_open'])
def test_owned_control_lifetime_and_explicit_run_owner_transfer(tmp_path, monkeypatch, mode):
    from src.trading_runtime import keeper_session, keeper_ownership, arte_journal_writer
    from src.backend.backtest_v4_keeper_lease import BacktestV4KeeperLease
    controller, request = controller_request(tmp_path)
    main = get_ident()
    closed, opened = [], []
    entered, release = Event(), Event()
    class Resource:
        def __init__(self, name):
            self.name = name
            self.client = object()
        def close(self):
            assert get_ident() != main
            closed.append(self.name)
        def release(self):
            assert get_ident() != main
            closed.append(self.name)
    session, lease = Resource('session'), Resource('lease')
    def open_session():
        assert get_ident() != main
        opened.append('session')
        if mode == 'cancel_open':
            entered.set()
            assert release.wait(5)
        return session
    monkeypatch.setattr(keeper_session, 'open_workstation_keeper_session', open_session)
    monkeypatch.setattr(BacktestV4KeeperLease, 'acquire', lambda *args, **kwargs: lease)
    def client(name):
        def open_client(**kwargs):
            assert get_ident() != main
            opened.append(name)
            if mode == 'failure' and name == 'writer':
                raise ValueError('Writer profile unavailable')
            value = Resource(name)
            value.backtest_v4_lease = lease
            return value
        return open_client
    monkeypatch.setattr(arte_journal_writer, 'backtest_v4_context_client_from_env', client('context'))
    monkeypatch.setattr(arte_journal_writer, 'backtest_v4_operator_client_from_env', client('reader'))
    monkeypatch.setattr(arte_journal_writer, 'backtest_v4_journal_client_from_env', client('writer'))
    class Keeper:
        def __init__(self, owned):
            assert owned is session.client
        def acquire_portfolio_admission_lease(self, resource, **kwargs):
            return dict(owner_id='definition', epoch=1)
        def release_portfolio_admission_lease(self, resource, **kwargs):
            assert get_ident() != main
            closed.append('definition')
    monkeypatch.setattr(keeper_ownership, 'KeeperOwnershipCoordinator', Keeper)
    async def work():
        async with managed.managed_program_journal_scope(controller, request, contracts={}) as scope:
            if mode == 'adopt':
                controller._fixed_keeper_session = session
                controller._fixed_v4_lease = lease
                controller._journal_writer = SimpleNamespace(_client=scope['writer_client'])
                scope.mark_adopted()
    async def run():
        task = asyncio.create_task(work())
        if mode == 'cancel_open':
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif mode == 'failure':
            with pytest.raises(ValueError, match='profile unavailable'):
                await task
        else:
            await task
    asyncio.run(run())
    assert closed.count('context') == 1
    assert closed.count('reader') == (1 if mode == 'failure' else 2)
    if mode == 'adopt':
        assert 'session' not in closed and 'lease' not in closed and 'writer' not in closed
    else:
        assert closed.count('session') == closed.count('lease') == 1
        assert closed.count('writer') == (0 if mode == 'failure' else 1)


@pytest.mark.parametrize('cleanup_failure', [False, True])
def test_native_app_preparation_opens_managed_lane_after_inputs_and_stages_only(tmp_path, monkeypatch, cleanup_failure):
    from contextlib import asynccontextmanager
    from threading import Lock
    from src.backend import program_app_preparation as flow
    controller, request = controller_request(tmp_path)
    monkeypatch.setattr('src.backend.backtest_journal_clickhouse.backtest_code_hash',
        lambda _: controller._program_strategy.program_release.header['code_hash'])
    stages = []
    run_service, publisher = object(), object()
    selector = SimpleNamespace(_prepared_lock=Lock(), _prepared=None)
    async def review(service, actual):
        assert service is run_service and actual is controller
    async def prepare(*args, **kwargs):
        stages.append('inputs')
        return ('prepared',)
    @asynccontextmanager
    async def open_scope(actual, supplied, *, contracts):
        assert stages == ['inputs']
        assert supplied is request
        stages.append('owned-controls')
        session, lease = object(), object()
        writer = SimpleNamespace(backtest_v4_lease=lease)
        scope = managed.ManagedProgramJournalScope(dict(contracts, keeper_session=session,
            writer_client=writer), controller)
        yield scope
        assert scope.adopted
        stages.append('close-controls')
        if cleanup_failure:
            raise ValueError('Control cleanup failed')
    async def attach(**values):
        stages.append('journal')
        controller._journal = object()
        controller._fixed_keeper_session = values['keeper_session']
        controller._fixed_v4_lease = values['writer_client'].backtest_v4_lease
        controller._journal_writer = SimpleNamespace(_client=values['writer_client'])
    async def runtime(**kwargs):
        stages.append('runtime')
        async def close(): stages.append('runtime-close')
        controller._runtime = SimpleNamespace(close_background_tasks=close)
    async def signals(*args, **kwargs): stages.append('signals')
    async def bind(*args, **kwargs): stages.append('bind')
    async def stage(service, actual):
        stages.append('stage')
        selector._prepared = (service, actual)
        return actual
    async def close_journal():
        stages.append('journal-close')
        controller._journal = None
    selector._review_controller, selector.stage_controller = review, stage
    monkeypatch.setattr(flow, 'program_configuration_selection_service', lambda: selector)
    monkeypatch.setattr(flow, 'prepare_program_session_input_plan', prepare)
    monkeypatch.setattr(flow, 'bind_prepared_program_launch', bind)
    monkeypatch.setattr(managed, 'managed_program_journal_scope', open_scope)
    controller.attach_prepared_program_journal = attach
    controller._initialize_runtime = runtime
    controller.bootstrap_program_signals = signals
    controller._close_fixed_journal = close_journal
    async def run():
        pending = flow.complete_program_app_preparation(run_service, controller,
            cohorts=(object(),), scopes=({'source_publisher': publisher},), journal_scope=request,
            signal_publisher=publisher, signal_scope={}, checkpoint_connection_factory=lambda: None,
            checkpoint_concurrency=2)
        if cleanup_failure:
            with pytest.raises(ValueError, match='Control cleanup'):
                await pending
            assert selector._prepared is None
            assert stages[-2:] == ['runtime-close', 'journal-close']
        else:
            assert await pending is controller
            assert selector._prepared == (run_service, controller)
        assert controller._task is None
    asyncio.run(run())
    assert stages[:8] == ['inputs', 'owned-controls', 'journal', 'runtime', 'signals', 'bind', 'stage', 'close-controls']
